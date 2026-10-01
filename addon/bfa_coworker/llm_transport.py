# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
LLM HTTP transport for the Coworker agent.

Owns everything between the conversation loop and the LLM endpoint:

* the OpenAI-compatible request helpers (streaming SSE and
  non-streaming with the full retry/reshape taxonomy),
* the streaming chunk accumulator and response reassembly,
* the llama-server HTTP 500 fault classification (template / server /
  toolcall) and the actionable per-fault error messages.

The conversation loop (:mod:`agent_controller`) injects the agent state
and stop event via :func:`bind`; everything else in this module is pure
transport logic with no bpy dependency, which keeps it unit-testable
outside Blender.

Split from agent_controller.py (tier 3 hardening): that module had grown
to ~5,900 lines holding transport, orchestration, and subprocess
management in one file; transport is the piece with the cleanest
boundary -- it touches only the request, the shared agent state, and the
stop event.
"""

__all__ = (
    "_CHAT_SAMPLING",
    "_DEFAULT_MAX_TOKENS",
    "_DEFAULT_TEMPERATURE_CODE",
    "_DEFAULT_TEMPERATURE_PROSE",
    "_STREAM_TIMEOUT",
    "bind",
    "classify_llm_500",
    "is_context_overflow",
    "context_overflow_message",
    "server_fault_message",
    "toolcall_fault_message",
    "openai_chat_completions",
    "openai_chat_completions_stream",
)

import json
import re
import threading
import time
import typing
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

# -- Sampling parameters (owned here; agent_controller imports them) -
# Sampling tuned for MoE local models.
_CHAT_SAMPLING = {
    "repeat_penalty": 1.1,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
}

# Temperature auto-switches based on mode:
#   Agent mode (code gen) -> 0.2: sharp, deterministic
#   Ask mode (prose/UI)  -> 0.35: natural writing
_DEFAULT_TEMPERATURE_CODE = 0.2
_DEFAULT_TEMPERATURE_PROSE = 0.35

# Default max output tokens per call when the caller does not pin one.
_DEFAULT_MAX_TOKENS = 1024

# Socket timeout (seconds) for streaming and non-streaming LLM requests.
_STREAM_TIMEOUT = 600.0

# -- Shared state, injected by agent_controller at import time ------
# The transport needs to flag non-fatal notices (mid-stream drops,
# tool-calling downgrades) and observe user stops, but it must not own
# the state: one owner (AgentState in agent_controller), one writer per
# field.

_agent_state: Any = None  # AgentState instance; set via bind()
_stop_event: threading.Event | None = None  # set via bind()


def bind(state: Any, stop_event: threading.Event) -> None:
    """Inject the shared agent state and stop event (called once)."""
    global _agent_state, _stop_event
    _agent_state = state
    _stop_event = stop_event


# -- Pure conversation helpers, injected by agent_controller --------
# These live in agent_controller (they shape the conversation history)
# but the transport's fallback paths need them.  Injected as a namespace
# so the dependency stays one-way: transport never imports the loop.

_helpers: Any = None  # SimpleNamespace; set via bind_helpers()


def bind_helpers(helpers: Any) -> None:
    """Inject the pure history helpers used by fallback reshaping."""
    global _helpers
    _helpers = helpers


def _h(name: str):
    """Fetch an injected helper; raises a clear error if unbound."""
    if _helpers is None:
        raise RuntimeError(
            "llm_transport.bind_helpers() was never called -- "
            "the fallback reshape path cannot run"
        )
    return getattr(_helpers, name)


def _stop_requested() -> bool:
    """True when the user requested a stop (safe before bind())."""
    return _stop_event is not None and _stop_event.is_set()


# -- LLM HTTP 500 fault classification ------------------------------
# An HTTP 500 from llama-server means one of two very different things, and
# only one of them is safe to "fix" by reshaping the request:
#
#   * TEMPLATE fault - the model's Jinja chat template cannot represent the
#     request (no branch for the ``tool`` role, no tool-call parser).  This
#     is recoverable by resending the conversation in a shape the template
#     can render (tool results as plain user text, no ``tools`` parameter).
#
#   * SERVER fault - a genuine resource or hardware failure (GPU OOM, CUDA
#     error, allocation failure).  NOT recoverable by reshaping the request;
#     retrying is the only option, and the real cause must be surfaced
#     rather than masked.
#
#   * TOOLCALL fault - llama-server could not parse the model's *own*
#     generated tool-call arguments as JSON (an unterminated string, a
#     truncated code block).  The request was fine; the model's output was
#     malformed.  Reshaping the request is pointless, but a single retry
#     with a nudge is worth it, and the cause must be named so it is not
#     mistaken for a server crash.
#
# Treating the second as the first silently downgrades the session to
# text-based tool calling and hides the real cause, so classify first.

_FAULT_TEMPLATE = "template"
_FAULT_SERVER = "server"
_FAULT_TOOLCALL = "toolcall"

# Markers of a chat-template rejection.  These appear in the JSON body
# llama-server returns when it cannot render or parse the conversation.
_TEMPLATE_FAULT_MARKERS = (
    "unable to generate parser",
    "unexpected message role",
    "no user query found",
    "jinja",
    "chat template",
    "template",
)

# Markers of a malformed tool call generated by the model.  llama-server
# parses the model's output into the OpenAI ``tool_calls`` shape and fails
# when the arguments are not valid JSON -- typically because generation was
# cut off mid-string.  Checked BEFORE the template markers, because the
# message also contains the word "template" ("...for this template...").
_TOOLCALL_FAULT_MARKERS = (
    "failed to parse tool call",
    "parse tool call arguments",
    "tool call arguments as json",
    "invalid string: missing closing quote",
    "missing closing quote",
)

# Markers of a resource or hardware failure.  Checked BEFORE the template
# markers because several template markers ("template") are generic enough
# to appear inside unrelated server messages.
#
# Kept in sync with ``llm_manager._GPU_OOM_MARKERS``; the extra entries here
# cover non-GPU allocation failures and ggml asserts.
_SERVER_FAULT_MARKERS = (
    "out of memory",
    "outofmemory",
    "out of device memory",
    "outofdevicememory",
    "outofhostmemory",
    "cuda error",
    "cudamalloc",
    "ggml_assert",
    "ggml_vulkan",
    "allocatememory",
    "failed to allocate",
    "failed to allocate vulkan0 buffer",
    "failed to allocate buffer for kv cache",
    "failed to allocate gpu buffer",
)


def classify_llm_500(error_body: str) -> str:
    """Classify an HTTP 500 body as a template, server, or tool-call fault.

    Returns :data:`_FAULT_TEMPLATE` when the body shows the chat template
    rejected the request, :data:`_FAULT_TOOLCALL` when the model's own
    generated tool-call arguments were not valid JSON, or
    :data:`_FAULT_SERVER` for a resource or hardware failure.

    An empty or unrecognised body is classified as a **server** fault: the
    safe default is to keep native tool calling and retry, never to silently
    downgrade the protocol on an error we do not understand.
    """
    lowered = (error_body or "").lower()
    if any(marker in lowered for marker in _SERVER_FAULT_MARKERS):
        return _FAULT_SERVER
    if any(marker in lowered for marker in _TOOLCALL_FAULT_MARKERS):
        return _FAULT_TOOLCALL
    if any(marker in lowered for marker in _TEMPLATE_FAULT_MARKERS):
        return _FAULT_TEMPLATE
    return _FAULT_SERVER


# -- HTTP 400: context-window exhaustion ----------------------------
# llama-server answers a request larger than the window it was started
# with using HTTP 400 and a JSON body identifying the cause.  Re-sending
# the identical payload can never succeed, so this class of 400 is
# detected and handled separately from the chat-template 400s.
_CONTEXT_OVERFLOW_MARKERS = (
    "exceeds the available context size",
    "exceed_context_size_error",
    "exceeds context size",
    "available context size",
    "context length exceeded",
    "prompt is too long",
    "too many tokens",
)


def is_context_overflow(error_body: str) -> bool:
    """True when a 400 body means the request exceeded the context window.

    The recovery differs completely from a template 400: the payload must
    be made *smaller* (compaction / re-trim), so the caller needs to tell
    the two apart.  Never raises.
    """
    lowered = (error_body or "").lower()
    return any(marker in lowered for marker in _CONTEXT_OVERFLOW_MARKERS)


def context_overflow_message(error_body: str) -> str:
    """Build an actionable message for a context-window 400.

    Names the real cause (the request was larger than the window the
    server was started with) and what the agent will do about it, instead
    of the bare ``HTTP Error 400: Bad Request`` the raw exception carries.
    """
    parts = [
        "The conversation grew larger than the local context window, so the "
        "model server rejected the request (HTTP 400).",
        "Coworker is compacting the conversation and retrying automatically. "
        "If this keeps happening, use 'Compact Now' in the Session panel or "
        "start a new chat to reset the working window.",
    ]
    if error_body:
        parts.append("--- server response ---\n{:s}".format(error_body[:800]))
    return "\n\n".join(parts)


def _server_log_shows_fault() -> bool:
    """True when the llama-server log tail shows a resource/hardware fault.

    Cross-checks an ambiguous HTTP 500.  llama-server names the real cause
    (OOM, CUDA error, ggml assert) in its own log far more clearly than in
    the short JSON body it returns to the client, so the log is the better
    signal when the body is bare or misleading.
    """
    try:
        from . import llm_manager as _llm
        tail = _llm.get_llama_server_log_tail() or ""
    except Exception:  # pylint: disable=broad-exception-caught
        return False
    if not tail:
        return False
    try:
        if _llm._log_looks_like_gpu_oom(tail):  # pylint: disable=protected-access
            return True
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return any(marker in tail.lower() for marker in _SERVER_FAULT_MARKERS)


def toolcall_fault_message(error_body: str) -> str:
    """Build an actionable error message for a malformed tool call.

    The request was valid; the model's own generated tool-call arguments
    were not parseable JSON (usually a string left unterminated because
    generation was cut off).  Name the cause so it is not mistaken for a
    server crash, and suggest the two things that actually help.
    """
    parts = [
        "The model produced a tool call whose arguments were not valid JSON, "
        "so the local LLM server could not parse it (HTTP 500). This is a "
        "model generation failure, not a server fault.",
        "The usual cause is a single tool call that was too large for one "
        "response: the arguments were cut off mid-string before the JSON was "
        "complete. Ask for the work in smaller steps (e.g. \"do this in a few "
        "smaller scripts\") so each tool call fits in one response.",
    ]
    if error_body:
        parts.append("--- server response ---\n{:s}".format(error_body[:800]))
    parts.append(
        "Try again, or lower the Reasoning Effort / raise the Context Window "
        "so the model has room to finish its tool call."
    )
    return "\n\n".join(parts)


def server_fault_message(error_body: str) -> str:
    """Build an actionable error message for an LLM server fault.

    Includes the server's response body and the llama-server log tail, and
    appends the GPU out-of-memory hint when the log shows an OOM - the most
    common cause of a 500 on a model that otherwise works.
    """
    parts = ["The local LLM server returned an internal error (HTTP 500)."]
    if error_body:
        parts.append("--- server response ---\n{:s}".format(error_body[:800]))
    try:
        from . import llm_manager as _llm
        tail = _llm.get_llama_server_log_tail()
        if tail:
            parts.append("--- llama-server.log (tail) ---\n{:s}".format(tail))
            if _llm._log_looks_like_gpu_oom(tail):  # pylint: disable=protected-access
                parts.append(_llm._gpu_oom_hint())  # pylint: disable=protected-access
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return "\n\n".join(parts)


def openai_chat_completions(
    url: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    thinking_budget_tokens: int = 0,
    chat_mode: str = "AGENT",
) -> dict[str, Any] | None:
    """POST to a chat completions endpoint and return the parsed JSON response.

    *tools* may be ``None`` or ``[]`` (both mean "no tool schema"); callers
    such as the session-memory writer legitimately pass ``None``.

    *model* -- when provided, included in the request body. Required for
    remote APIs (OpenRouter, OpenAI, etc.). Omitted for local llama-server
    which auto-detects the model.
    *max_tokens* -- max output tokens per call. ``None`` uses 16384 default.
    """
    # Start each call with a clean error classification so a stale overflow
    # from a previous call is never mistaken for the current failure.
    if _agent_state is not None:
        _agent_state.error_kind = ""
    temperature = _DEFAULT_TEMPERATURE_CODE if chat_mode == "AGENT" else _DEFAULT_TEMPERATURE_PROSE
    body: dict[str, Any] = {
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens if max_tokens is not None else _DEFAULT_MAX_TOKENS,
        "temperature": temperature,
        **_CHAT_SAMPLING,
    }
    if thinking_budget_tokens > 0:
        body["thinking_budget_tokens"] = thinking_budget_tokens
    if model:
        body["model"] = model
    if tools:
        body["tools"] = tools

    data_bytes = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://bforartists.org",
        "X-OpenRouter-Title": "Bforartists Coworker",
    }
    if api_key:
        headers["Authorization"] = "Bearer {:s}".format(api_key)

    tools = tools or []
    print("[Coworker] _openai_chat_completions: POST {:s}".format(url))
    print("[Coworker] _openai_chat_completions:   model = {:s}".format(model or "(auto-detect)"))
    print("[Coworker] _openai_chat_completions:   messages = {:d}, tools = {:d}, body = {:d} bytes".format(
        len(messages), len(tools), len(data_bytes)))

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    # Retry loop for transient failures (e.g. server just became ready
    # but the HTTP worker hasn't started yet).
    # Also handles 503 Service Unavailable -- llama-server returns this
    # while the model is still loading (can take 30-120s for large models).
    # We retry 503 with exponential backoff up to 120s total.
    # Also handles chat template crashes: custom GGUF templates (DavidAU
    # fine-tunes, etc.) may 500 on the ``tools`` parameter.  We inject
    # tool descriptions into the system prompt as text and retry without
    # the ``tools`` JSON parameter, then parse text-based tool calls from
    # the response.
    import time as _time
    max_retries = 5
    max_503_retries = 12  # Bounded so total 503 backoff stays under ~120s.
    tools_tried = bool(tools)
    _503_attempts = 0
    # One-shot guards for the request-reshaping fallbacks.  Each reshapes the
    # payload into a strictly simpler shape, so a second identical failure
    # means the reshape did not help -- retrying it would burn the whole
    # retry budget on a payload that cannot succeed.
    _flattened = False
    _roles_sanitized = False
    _tools_as_text = False
    _toolcall_nudged = False
    # Body of the most recent HTTP error, read once per attempt.  Carried
    # across attempts so the final failure block can always surface the
    # real server reason (``ex.read()`` returns empty on a second call).
    _last_error_body = ""
    for attempt in range(max_retries + max_503_retries):
        try:
            with urllib.request.urlopen(req, timeout=_STREAM_TIMEOUT) as resp:
                raw = resp.read().decode()
                print("[Coworker] _openai_chat_completions: status={:d}, response={:d} bytes".format(
                    resp.status, len(raw)))
                print("[Coworker] _openai_chat_completions: first 500 chars: {:s}".format(raw[:500]))
                result: dict[str, Any] = json.loads(raw)
                # Log the assistant message content and any tool calls.
                choice = result.get("choices", [{}])[0]
                msg = choice.get("message", {})
                finish = choice.get("finish_reason", "")
                content = msg.get("content") or ""

                tool_calls = msg.get("tool_calls") or []
                print("[Coworker] _openai_chat_completions: finish_reason={:s}".format(finish))
                print("[Coworker] _openai_chat_completions: content   = {:s}".format(
                    repr(content[:200]) if content else "(empty)"))
                print("[Coworker] _openai_chat_completions: tool_calls= {:d}".format(len(tool_calls)))
                for i, tc in enumerate(tool_calls):
                    fn = tc.get("function", {})
                    print("[Coworker] _openai_chat_completions:   tool[{:d}] = {:s}({:s})".format(
                        i, fn.get("name", "?"), str(fn.get("arguments", ""))[:120]))
                # Log reasoning content (chain-of-thought) for debugging.
                reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
                if reasoning:
                    print("[Coworker] _openai_chat_completions: reasoning ({:d} chars):".format(
                        len(reasoning)))
                    # Collapse consecutive blank lines to reduce console clutter.
                    _clean = re.sub(r"\n{3,}", "\n\n", _h('strip_think_tags')(reasoning))
                    print(_clean)

                    print("[Coworker] _openai_chat_completions: --- end reasoning ---")
                # If we fell back to text-based tool calling, parse text calls.
                # Only when tools were actually offered: with no tools in the
                # request (Ask mode), parsed "calls" would be spurious -- the
                # model is just writing JSON/XML in prose (issue #66).
                if not tools_tried and not tool_calls and tools:
                    text_calls = _h('parse_text_tool_calls')(content)
                    if text_calls:
                        print("[Coworker] _openai_chat_completions: parsed {:d} text-based tool calls".format(
                            len(text_calls)))
                        msg["tool_calls"] = text_calls
                        choice["finish_reason"] = "tool_calls"
                        result["_text_tool_fallback"] = True

                # -- XML tool call fallback --------------------------------
                # Light reasoning models (Qwen3.5-9B DeepSeek-V4-Flash,
                # Gemma 4 E4B) often emit tool calls as XML inside
                # ``reasoning_content`` or ``content`` instead of the proper
                # OpenAI ``tool_calls`` array.  Parse both fields.
                # Same gating as text-based parsing: without an offered
                # tool list (Ask mode), XML blocks are prose, not calls.
                if not msg.get("tool_calls") and tools:
                    xml_sources: list[tuple[str, str]] = []
                    if reasoning:
                        xml_sources.append(("reasoning_content", reasoning))
                    if content:
                        xml_sources.append(("content", content))
                    for source_name, source_text in xml_sources:
                        xml_calls = _h('parse_xml_tool_calls')(source_text)
                        if xml_calls:
                            print("[Coworker] _openai_chat_completions: "
                                  "parsed {:d} XML tool calls from {:s}".format(
                                      len(xml_calls), source_name))
                            msg["tool_calls"] = xml_calls
                            choice["finish_reason"] = "tool_calls"
                            result["_xml_tool_fallback"] = True
                            break

                _clear_stale_errors()
                return result
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
            # -- Capture the error body ONCE for every HTTP error --------
            # ``ex.read()`` returns empty on a second call, so the body MUST
            # be read exactly once here and reused by the 500 classifier, the
            # 400 reshape, and the final failure block.  Losing it was how a
            # real context-window 400 was previously reported to the user as
            # a bare "HTTP Error 400: Bad Request".
            _is_500 = isinstance(ex, urllib.error.HTTPError) and ex.code == 500
            _http_body = ""
            if isinstance(ex, urllib.error.HTTPError):
                try:
                    _http_body = ex.read().decode("utf-8", errors="replace")
                except Exception:
                    _http_body = ""
            _500_body = _http_body
            _last_error_body = _http_body
            # -- HTTP 500: distinguish template fault from server fault --
            # Both arrive as a bare 500, and the recovery differs completely:
            #   * template fault -> reshape the request (tools as text)
            #   * server fault   -> keep the request, retry with backoff
            # Reshaping on a server fault silently downgrades the session to
            # text-based tool calling and masks the real cause (usually an
            # OOM).  Classify first, then choose.
            _fault = classify_llm_500(_500_body) if _is_500 else ""

            # A bare or generic 500 body is ambiguous.  llama-server names
            # the real cause in its own log far more clearly than in the
            # short JSON it returns, so cross-check before reshaping.
            # A specific tool-call match is NOT ambiguous, so it is exempt:
            # the log tail may still hold an OOM from an earlier run.
            _log_fault = False
            if _is_500 and _fault not in (_FAULT_TEMPLATE, _FAULT_TOOLCALL):
                _log_fault = _server_log_shows_fault()
                if _log_fault:
                    _fault = _FAULT_SERVER

            # -- Server fault: do NOT reshape the request ---------------
            # Fall through to the generic retry-with-backoff path and, if
            # the fault persists, surface the real cause (body + log tail +
            # GPU-OOM hint) so it is not mistaken for a template problem.
            if _is_500 and _fault == _FAULT_SERVER:
                print("[Coworker] _openai_chat_completions: 500 SERVER fault "
                      "(not a template problem) -- keeping tools, will retry")
                if _500_body:
                    print("[Coworker] _openai_chat_completions:   500 body = {:s}".format(_500_body[:500]))
                if _log_fault:
                    print("[Coworker] _openai_chat_completions:   llama-server log "
                          "confirms a resource/hardware fault")

            # -- Malformed tool call: retry once with a nudge -----------
            # The request was valid; the model emitted tool-call arguments
            # that were not valid JSON (usually a string left unterminated
            # because generation was cut off).  Reshaping the request cannot
            # help, but a single retry with an explicit instruction often
            # does.  Guarded so a model that keeps failing surfaces the real
            # error instead of looping.
            if _is_500 and _fault == _FAULT_TOOLCALL and not _toolcall_nudged:
                _toolcall_nudged = True
                print("[Coworker] _openai_chat_completions: 500 TOOLCALL fault -- "
                      "model emitted malformed tool-call JSON, retrying once with a nudge")
                if _500_body:
                    print("[Coworker] _openai_chat_completions:   500 body = {:s}".format(_500_body[:500]))
                # Copy the list and the target dict: when the caller's
                # history is short enough to be sent as-is, ``messages`` IS
                # the live conversation history, and mutating it would
                # permanently pollute the real system prompt.
                messages = list(messages)
                # The nudge must change the *shape* of the next attempt, not
                # just ask for the same call again.  The usual cause is a
                # single oversized tool call (a long script) that ran out of
                # output tokens mid-string, so re-emitting it verbatim would
                # truncate in exactly the same place.  Tell the model to
                # split the work into smaller calls instead.
                _nudge = (
                    "\n\nIMPORTANT: Your previous tool call could not be parsed "
                    "because its arguments were not valid JSON -- the arguments "
                    "were cut off before the JSON was complete. This almost "
                    "always means the tool call was too large for one response. "
                    "Do NOT repeat the same large call. Instead, split the work "
                    "into several smaller tool calls and make them one at a "
                    "time: keep each script short (roughly 40 lines or fewer), "
                    "and build the result up across multiple calls. Emit "
                    "complete, well-formed JSON arguments -- never truncate a "
                    "string or a code block."
                )
                _nudged = False
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "system":
                        messages[i] = {
                            **messages[i],
                            "content": str(messages[i].get("content") or "") + _nudge,
                        }
                        _nudged = True
                        break
                if not _nudged:
                    messages.insert(0, {"role": "system", "content": _nudge.strip()})
                body["messages"] = messages
                data_bytes = json.dumps(body).encode()
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                continue

            # -- Chat template crash fallback: inject tools as text ----
            # Some custom GGUF chat templates (e.g. Fable Fusion, DavidAU
            # fine-tunes) 500 on the ``tools`` parameter.  We inject tool
            # descriptions into the system prompt and retry without the
            # ``tools`` JSON parameter, preserving full agent functionality.
            if tools_tried and _is_500 and _fault == _FAULT_TEMPLATE and tools and not _tools_as_text:
                _tools_as_text = True
                print("[Coworker] _openai_chat_completions: 500 TEMPLATE fault -- "
                      "injecting tools as text and retrying")
                if _500_body:
                    print("[Coworker] _openai_chat_completions:   500 body = {:s}".format(_500_body[:500]))
                tools_tried = False
                # Tell the user why the agent's behaviour changed.  Without
                # this the downgrade is invisible and looks like the model
                # simply got worse at calling tools.
                _agent_state.warning = (
                    "This model's chat template rejects native tool calling, so "
                    "Coworker switched to text-based tool calling for this "
                    "request. Tool use may be less reliable than usual."
                )
                # Build a text description of available tools.
                tool_text = (
                    "\n\nYou have access to the following tools. "
                    "To call a tool, output a JSON block with the format:\n"
                    '{"tool": "tool_name", "arguments": {"arg1": "value1"}}\n'
                    "Available tools:\n"
                )
                for t in tools:
                    fn = t.get("function", {})
                    name = fn.get("name", "?")
                    desc = fn.get("description", "")
                    params = fn.get("parameters", {})
                    props = params.get("properties", {})
                    param_str = ", ".join(
                        "{:s}: {:s}".format(k, v.get("description", "?"))
                        for k, v in props.items()
                    )[:200]
                    tool_text += "- {:s}: {:s} ({:s})\n".format(name, desc, param_str)
                # Inject into the last system message, or add a new one.
                # Copy the list and the target dict first: when the caller's
                # history is short enough to be sent as-is, ``messages`` IS
                # the live conversation history, and mutating it would
                # permanently pollute the real system prompt with tool text.
                messages = list(messages)
                injected = False
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "system":
                        messages[i] = {
                            **messages[i],
                            "content": str(messages[i].get("content") or "") + tool_text,
                        }
                        injected = True
                        break
                if not injected:
                    messages.insert(0, {"role": "system", "content": tool_text})
                # Rebuild request without tools.
                body.pop("tools", None)
                body["messages"] = messages
                data_bytes = json.dumps(body).encode()
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                continue

            # -- Chat template role error fallback ----------------------
            # Some models (Qwen, etc.) have strict Jinja templates that
            # reject non-standard message roles.  Sanitize roles and retry.
            # Only reachable for template faults -- server faults returned
            # above without reshaping the request.
            if _is_500 and "Unexpected message role" in _500_body and not _roles_sanitized:
                _roles_sanitized = True
                print("[Coworker] _openai_chat_completions: 500 error -- "
                      "unexpected message role, sanitizing and retrying")
                messages = _h('sanitize_message_roles')(messages)
                body["messages"] = messages
                print("[Coworker] _openai_chat_completions:   sanitized roles = {:s}".format(
                    _h('describe_message_roles')(messages)))
                data_bytes = json.dumps(body).encode()
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                continue

            # -- Chat template parser-generation failure (400) --------
            # llama-server auto-generates a parser for the model's Jinja
            # chat template when a request carries tool-calling structure.
            # Some templates fail that generation with HTTP 400
            # "Unable to generate parser for this template ... Unexpected
            # message role."  Flatten the conversation to plain
            # system/user/assistant messages and retry without tools; the
            # model then answers in text (text tool calls are still parsed).
            if isinstance(ex, urllib.error.HTTPError) and ex.code == 400:
                # Reuse the body read once at the top of the handler.
                _error_body = _last_error_body
                # -- Context window exceeded: fail fast, do not retry --
                # The request was larger than the window the server was
                # started with.  Re-sending the identical payload can never
                # succeed and only burns the retry budget; flag it so the
                # caller can compact the conversation and retry, and surface
                # the real server reason instead of a bare 400.
                if is_context_overflow(_error_body):
                    _agent_state.error_kind = "context_overflow"
                    _msg = context_overflow_message(_error_body)
                    print("[Coworker] _openai_chat_completions: 400 context window "
                          "exceeded -- not retrying the same payload")
                    if _error_body:
                        print("[Coworker] _openai_chat_completions:   400 body = {:s}".format(_error_body[:500]))
                    _agent_state.error = _msg[:500]
                    _agent_state.error_full = _msg
                    return None
                if ("parser" in _error_body and "template" in _error_body) or "Unexpected message role" in _error_body:
                    if _flattened:
                        # Already flattened once and it still failed, so the
                        # template rejects even the plain shape.  Fall through
                        # to the generic retry path so the real error surfaces
                        # instead of looping on an identical payload.
                        print("[Coworker] _openai_chat_completions: 400 template/parser error "
                              "persists after flattening -- not retrying the same shape")
                    else:
                        _flattened = True
                        print("[Coworker] _openai_chat_completions: 400 template/parser error - "
                              "flattening conversation and retrying without tools")
                        if _error_body:
                            print("[Coworker] _openai_chat_completions:   400 body = {:s}".format(_error_body[:300]))
                        tools_tried = False
                        body.pop("tools", None)
                        # Reassign ``messages`` as well as the body.  Setting
                        # only body["messages"] left the original list intact,
                        # so every retry re-flattened the same input and failed
                        # identically -- the fallback could never converge.
                        messages = _h('flatten_for_plain_chat')(messages)
                        body["messages"] = messages
                        print("[Coworker] _openai_chat_completions:   flattened roles = {:s}".format(
                            _h('describe_message_roles')(messages)))
                        print("[Coworker] _openai_chat_completions:   flattened shape:")
                        print(_h('describe_history_for_log')(messages))
                        data_bytes = json.dumps(body).encode()
                        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                        continue

                # Any other 400 (including a template/parser 400 that
                # persists after flattening) is a malformed request that
                # re-sending verbatim cannot fix.  Surface the real server
                # reason now instead of burning the whole retry budget on the
                # identical payload and then reporting a bare "HTTP Error 400".
                _msg = "LLM request failed: {:s}".format(_error_body or str(ex))
                print("[Coworker] _openai_chat_completions: 400 request rejected -- "
                      "not retrying the same payload")
                if _error_body:
                    print("[Coworker] _openai_chat_completions:   400 body = {:s}".format(_error_body[:500]))
                _agent_state.error = _msg[:500]
                _agent_state.error_full = _msg
                return None

            # -- 503 Service Unavailable: model still loading ----------
            # llama-server returns 503 while the model is loading into
            # memory (can take 30-120s for large models).  Retry with
            # exponential backoff up to 120s total.
            if isinstance(ex, urllib.error.HTTPError) and ex.code == 503:
                _503_attempts += 1
                backoff = min(2.0 * _503_attempts, 10.0)  # 2s, 4s, 6s, ... 10s max
                if _503_attempts % 5 == 0:
                    print("[Coworker] _openai_chat_completions: 503 attempt {:d} -- "
                          "model still loading, retrying in {:.0f}s...".format(_503_attempts, backoff))
                _time.sleep(backoff)
                continue
            # -- Non-retryable 4xx: fail fast with the real reason -----
            # 401/403/404/422 are deterministic client errors; retrying the
            # identical payload wastes ~8s and masks the real cause behind a
            # generic "HTTP Error".  (400 has its own reshape/fail-fast path
            # above; 408/429 fall through to the generic transient retry.)
            if isinstance(ex, urllib.error.HTTPError) and ex.code in (401, 403, 404, 422):
                _body = ""
                try:
                    _body = ex.read().decode("utf-8", errors="replace")
                except Exception:  # pylint: disable=broad-exception-caught
                    _body = ""
                _msg = "LLM request failed: HTTP {:d} -- {:s}".format(
                    ex.code, (_body or str(ex))[:500])
                print("[Coworker] _openai_chat_completions: HTTP {:d} is not "
                      "retryable -- surfacing the reason".format(ex.code))
                _agent_state.error = _msg[:500]
                _agent_state.error_full = _msg
                return None
            if attempt < max_retries - 1:
                # A malformed tool call is a *generation* failure, not a
                # transient one.  Once the one-shot nudge has been sent, the
                # payload already carries the "split the work up" instruction,
                # so re-sending it verbatim would truncate in the same place
                # and burn the whole retry budget (5 x ~60s) before reporting
                # the same error.  Surface it immediately instead.
                if _is_500 and _fault == _FAULT_TOOLCALL and _toolcall_nudged:
                    print("[Coworker] _openai_chat_completions: malformed tool call "
                          "persists after the nudge -- not retrying the same payload")
                else:
                    # Reuse the body already read for 500 classification above
                    # (``ex.read()`` is empty on a second call).
                    _error_body = _500_body
                    if not _is_500 and isinstance(ex, urllib.error.HTTPError) and ex.code == 500:
                        try:
                            _error_body = ex.read().decode("utf-8", errors="replace")
                        except Exception:
                            pass
                    if _error_body:
                        print("[Coworker] _openai_chat_completions: attempt {:d}/{:d} FAILED -- {:s}".format(
                            attempt + 1, max_retries, str(ex)))
                        print("[Coworker] _openai_chat_completions:   500 body = {:s}".format(_error_body[:500]))
                    else:
                        print("[Coworker] _openai_chat_completions: attempt {:d}/{:d} FAILED -- {:s}, retrying in 2s...".format(
                            attempt + 1, max_retries, str(ex)))
                    _time.sleep(2)
                    continue
            # Surface the final failure.  For a server fault, build an
            # actionable message (body + llama-server log tail + GPU-OOM
            # hint) so a resource failure is not misread as a template bug.
            if _is_500 and _fault == _FAULT_SERVER:
                _msg = server_fault_message(_500_body)
                print("[Coworker] _openai_chat_completions: all attempts FAILED -- "
                      "LLM server fault ({:s})".format(str(ex)))
                _agent_state.error = _msg[:500]
                _agent_state.error_full = _msg
            elif _is_500 and _fault == _FAULT_TOOLCALL:
                _msg = toolcall_fault_message(_500_body)
                print("[Coworker] _openai_chat_completions: all attempts FAILED -- "
                      "malformed tool call ({:s})".format(str(ex)))
                _agent_state.error = _msg[:500]
                _agent_state.error_full = _msg
            else:
                # Reuse the body read once at the top of the handler.  Reading
                # ``ex.read()`` again returns empty, which is exactly how a
                # real 400 reason was previously lost and surfaced as a bare
                # "HTTP Error 400: Bad Request".
                _error_body = _last_error_body
                if _error_body:
                    print("[Coworker] _openai_chat_completions: all attempts FAILED -- {:s}".format(str(ex)))
                    print("[Coworker] _openai_chat_completions:   500 body = {:s}".format(_error_body[:500]))
                    _agent_state.error = "LLM request failed: {:s}".format(_error_body[:500])
                    _agent_state.error_full = "LLM request failed: {:s}".format(_error_body)
                else:
                    print("[Coworker] _openai_chat_completions: all attempts FAILED -- {:s}".format(str(ex)))
                    _agent_state.error = "LLM request failed: {:s}".format(str(ex))
                    _agent_state.error_full = "LLM request failed: {:s}".format(str(ex))
            return None
    return None


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>(.*?)</think\s*>", re.DOTALL | re.IGNORECASE)


def _split_inline_think(text: str) -> tuple[str, str]:
    """Split a content delta that inlines `` thinking...</think>`` blocks.

    Returns ``(visible, reasoning)``.  Only *complete* tagged blocks are routed
    to reasoning; any unmatched tag (a tag split across streaming chunks) is
    removed from the visible text so it never leaks, and its body stays in
    visible (best-effort -- correct for the common whole-tag-in-one-delta case).
    """
    reasoning = "".join(_THINK_BLOCK_RE.findall(text))
    visible = _THINK_BLOCK_RE.sub("", text)
    visible = re.sub(r"\s?</?think\b[^>]*>", "", visible, flags=re.IGNORECASE)
    return visible, reasoning


def _clear_stale_errors() -> None:
    """Clear a previous request's error state after a successful request.

    Only ``error_kind`` used to be reset per call, so a stale ``error`` /
    ``error_full`` from an earlier failure could be read between calls.
    """
    if _agent_state is None:
        return
    for _attr in ("error", "error_full", "error_kind"):
        try:
            setattr(_agent_state, _attr, "")
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def _extract_reasoning_delta(delta: dict[str, Any]) -> str:
    """Extract reasoning text from a streaming chunk's delta.

    Different providers stream chain-of-thought under different field
    names:
      * OpenRouter / some OpenAI-compatible servers: ``reasoning``
      * DeepSeek / llama-server: ``reasoning_content``
    Inline ``<think>...</think>`` tags are stripped so stored reasoning
    matches the non-streaming path (see :func:`_strip_think_tags`).
    """
    reasoning = ""
    if isinstance(delta, dict):
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
    if not reasoning:
        return ""
    # Remove only the ``think`` wrapper tokens (and the whitespace immediately
    # touching them); keep every OTHER whitespace so streamed token spaces
    # survive.  Trimming the delta (as ``_strip_think_tags`` does) would
    # concatenate the whole chain-of-thought into one unreadable word
    # ("Letmethinkabout...").  End-trimming happens once, on the assembled
    # text, in the caller.
    return re.sub(r"\s?</?think>\s?", "", reasoning)


def _parse_sse_chunk(chunk: dict[str, Any], acc: dict[str, Any]) -> None:
    """Accumulate one OpenAI-compatible streaming chunk into *acc*.

    *acc* is a dict with keys ``content`` (str), ``reasoning`` (str),
    ``tool_calls`` (list of assembled OpenAI tool-call dicts),
    ``finish_reason`` (str) and ``usage`` (dict, set by the final
    ``include_usage`` chunk).  Malformed chunks are skipped silently --
    a mid-stream provider hiccup should not abort an otherwise good
    generation.
    """
    choices = chunk.get("choices") or []
    if choices:
        choice = choices[0]
        delta = choice.get("delta") or {}
        delta_content = delta.get("content")
        if isinstance(delta_content, str) and delta_content:
            if "<think" in delta_content.lower():
                # Inline think tags in ``content``: route the tagged part to
                # reasoning and the plain text to content, each EXACTLY once.
                _visible, _think = _split_inline_think(delta_content)
                acc["content"] += _visible
                acc["reasoning"] += _think
            else:
                acc["content"] += delta_content
        # Some providers put <think>...</think> inline in content instead of
        # the reasoning field; route the tagged part to reasoning.
        delta_reasoning = _extract_reasoning_delta(delta)
        if delta_reasoning:
            acc["reasoning"] += delta_reasoning
        for tc_delta in delta.get("tool_calls") or []:
            idx = tc_delta.get("index", 0)
            fn = tc_delta.get("function") or {}
            while len(acc["tool_calls"]) <= idx:
                acc["tool_calls"].append({
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
            slot = acc["tool_calls"][idx]
            if tc_delta.get("id"):
                slot["id"] = tc_delta["id"]
            if fn.get("name"):
                slot["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]
        if choice.get("finish_reason"):
            acc["finish_reason"] = choice["finish_reason"]
    if chunk.get("usage"):
        acc["usage"] = chunk["usage"]


def _assemble_stream_result(acc: dict[str, Any]) -> dict[str, Any]:
    """Build the non-streaming response shape from accumulated stream chunks.

    The rest of the turn loop consumes the standard
    ``choices[0].message`` payload, so the streamed generation is
    re-assembled into exactly that shape (plus ``usage`` when the
    provider sent it).
    """
    msg: dict[str, Any] = {"role": "assistant", "content": acc["content"]}
    if acc["reasoning"]:
        msg["reasoning_content"] = acc["reasoning"]
    if acc["tool_calls"]:
        msg["tool_calls"] = [
            {
                "id": tc["id"] or "call_{}_{}".format(idx, tc["function"]["name"]),
                "type": "function",
                "function": tc["function"],
            }
            for idx, tc in enumerate(acc["tool_calls"])
        ]
        # Drop slots that never received a function name (provider glitch).
        msg["tool_calls"] = [
            tc for tc in msg["tool_calls"] if tc["function"].get("name")
        ]
        if not msg["tool_calls"]:
            del msg["tool_calls"]
    result: dict[str, Any] = {
        "choices": [{
            "message": msg,
            "finish_reason": acc["finish_reason"] or "stop",
        }],
    }
    if acc["usage"]:
        result["usage"] = acc["usage"]
    return result


def openai_chat_completions_stream(
    url: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    thinking_budget_tokens: int = 0,
    chat_mode: str = "AGENT",
    on_status: Callable[[str], None] | None = None,
    on_stream_text: Callable[[str], None] | None = None,
    on_stream_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """POST a *streaming* chat-completions request and reassemble the reply.

    Issue #69: in Remote API mode the non-streaming request is a black
    box -- nothing arrives until the full generation completes (up to the
    600 s timeout) and Stop cannot cancel it.  Streaming gives live text
    and reasoning for the Workshop, honours ``_stop_event`` mid-stream,
    and captures ``usage`` from the final chunk for the token counters.

    Returns the response in the same ``choices[0].message`` shape as
    :func:`_openai_chat_completions` (with ``usage`` attached when the
    provider provides it), or ``None`` when the stream failed before the
    first token (callers then fall back to the non-streaming request).
    """
    # Start each call with a clean error classification (see the
    # non-streaming helper) so a stale overflow is never misattributed.
    if _agent_state is not None:
        _agent_state.error_kind = ""
    temperature = _DEFAULT_TEMPERATURE_CODE if chat_mode == "AGENT" else _DEFAULT_TEMPERATURE_PROSE
    body: dict[str, Any] = {
        "messages": messages,
        "stream": True,
        # OpenAI-compatible extension: ask the provider to append a final
        # usage-only chunk.  Servers that ignore it simply omit usage.
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens if max_tokens is not None else _DEFAULT_MAX_TOKENS,
        "temperature": temperature,
        **_CHAT_SAMPLING,
    }
    if thinking_budget_tokens > 0:
        body["thinking_budget_tokens"] = thinking_budget_tokens
    if model:
        body["model"] = model
    if tools:
        body["tools"] = tools

    data_bytes = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "HTTP-Referer": "https://bforartists.org",
        "X-OpenRouter-Title": "Bforartists Coworker",
    }
    if api_key:
        headers["Authorization"] = "Bearer {:s}".format(api_key)

    print("[Coworker] _openai_chat_completions_stream: POST {:s}".format(url))
    print("[Coworker] _openai_chat_completions_stream:   model = {:s}".format(model or "(auto-detect)"))
    print("[Coworker] _openai_chat_completions_stream:   messages = {:d}, tools = {:d}".format(
        len(messages), len(tools)))

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
    if on_status:
        on_status("Contacting {:s}...".format(model or "API"))

    acc: dict[str, Any] = {
        "content": "",
        "reasoning": "",
        "tool_calls": [],
        "finish_reason": "",
        "usage": None,
    }
    _request_start = time.monotonic()
    got_first_token = False
    _content_seen = False
    _reasoning_since: float | None = None
    try:
        with urllib.request.urlopen(req, timeout=_STREAM_TIMEOUT) as resp:
            if resp.status != 200:
                raise urllib.error.URLError("HTTP {:d}".format(resp.status))
            for raw_line in resp:
                # Stop takes effect immediately, mid-generation: close the
                # response, keep what has streamed so far as a partial.
                if _stop_requested():
                    print("[Coworker] _openai_chat_completions_stream: "
                          "stop requested mid-stream -- aborting (partial kept)")
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    # Some providers report errors inside a 200 SSE stream.
                    err = chunk["error"]
                    _err_msg = (
                        str(err.get("message", err)) if isinstance(err, dict) else str(err)
                    )
                    raise urllib.error.URLError("stream error: {:s}".format(_err_msg))
                if not got_first_token:
                    got_first_token = True
                    if on_status:
                        on_status("Generating...")
                _parse_sse_chunk(chunk, acc)
                # Per-phase elapsed status (issue #69).  Phases are tracked
                # by what the CURRENT chunk carries (not the accumulator,
                # which is cumulative): a reasoning-only delta keeps the
                # Reasoning phase alive with its elapsed time; the first
                # content delta switches to the Generating phase.  The
                # phase status is one-shot per transition so it is not
                # re-emitted on every chunk; while reasoning continues,
                # the elapsed seconds update once per second.
                if on_status:
                    _elapsed = time.monotonic() - _request_start
                    if acc["content"]:
                        if not _content_seen:
                            _content_seen = True
                            on_status("Generating... ({:.0f}s)".format(_elapsed))
                    elif _reasoning_since is None:
                        _reasoning_since = _elapsed
                        on_status("Reasoning... ({:.0f}s)".format(_elapsed))
                    elif _elapsed - _reasoning_since >= 1.0:
                        _reasoning_since = _elapsed
                        on_status("Reasoning... ({:.0f}s)".format(_elapsed))
                if on_stream_reasoning and acc["reasoning"]:
                    on_stream_reasoning(acc["reasoning"])
                if on_stream_text and acc["content"]:
                    on_stream_text(acc["content"])
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as ex:
        if got_first_token:
            # Mid-stream drop: keep whatever arrived as a partial answer so
            # the user sees the content rather than a blank panel.
            print("[Coworker] _openai_chat_completions_stream: stream dropped "
                  "mid-generation ({:s}) -- returning partial".format(str(ex)))
            _agent_state.warning = (
                "The response stream was interrupted; the reply may be incomplete."
            )
            return _assemble_stream_result(acc)
        print("[Coworker] _openai_chat_completions_stream: failed before first "
              "token ({:s}) -- falling back to non-streaming".format(str(ex)))
        # Record the reason so a caller that does not retry non-streaming can
        # still report it (the error body is not re-readable once consumed).
        if isinstance(ex, urllib.error.HTTPError):
            try:
                _stream_body = ex.read().decode("utf-8", errors="replace")
            except Exception:  # pylint: disable=broad-exception-caught
                _stream_body = ""
            if _stream_body and is_context_overflow(_stream_body):
                if _agent_state is not None:
                    _agent_state.error_kind = "context_overflow"
            if _stream_body and _agent_state is not None:
                _agent_state.error_full = "LLM stream failed: {:s}".format(_stream_body[:500])
        return None

    if not got_first_token:
        # Connected but never streamed a token (e.g. a 200 body that is a
        # JSON error instead of SSE).  Let the caller retry non-streaming.
        print("[Coworker] _openai_chat_completions_stream: no tokens received")
        return None

    result = _assemble_stream_result(acc)
    if acc.get("usage"):
        usage = acc["usage"]
        print("[Coworker] _openai_chat_completions_stream: usage "
              "prompt={:s} completion={:s} total={:s}".format(
                  str(usage.get("prompt_tokens")),
                  str(usage.get("completion_tokens")),
                  str(usage.get("total_tokens"))))
    _clear_stale_errors()
    return result
