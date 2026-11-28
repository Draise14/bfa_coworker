# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
``ask_json`` (#612, Highlights H2): ask the model for a structured answer and check it.

A wand that needs more than prose (Follow Subject's one number, Highlights' lists of
sentence ranges, Suggest Edits' menu of edits) asks through :func:`ask_json`:

1. The request carries the schema where the server can enforce it (OpenAI-style
   ``response_format: json_schema``, which llama-server and most Remote APIs take) and
   always also says, in words, "JSON only, matching this schema".
2. The answer is checked against the schema **in our code**, whatever the server
   promised (:func:`validate`).
3. A wrong answer gets one retry with the errors named.  After that
   :class:`AskFailed` is raised, which the caller counts ("1 of 5 parts could not be read").
4. A server that refuses the ``response_format`` field (HTTP 400) is asked again without
   it, and remembered for the session.
5. External Agent and External Harness run their own model loop, so there is no direct
   request: :class:`Unavailable` is raised with ``kind == "external"`` so the caller can
   hand the job to the agent as a message instead.

The request goes through ``llm_transport.openai_chat_completions`` with a throwaway
state (an error never shows as the chat's error) and never through the agent's chat
turn, so the chat's history and tool list are not in it.  For a Local server thinking is
switched off for these requests (a short checked answer needs the room).  :data:`STUB`
replaces the transport with a function (tests, scripted live checks): the checking and
the retry still run.

Free of ``bpy`` at import: it runs on worker threads.
"""

from __future__ import annotations

__all__ = (
    "STUB",
    "AskFailed",
    "Result",
    "Unavailable",
    "ask_json",
    "engine_target",
    "extract_candidates",
    "make_asker",
    "resolve_asker",
    "reset_schema_memory",
    "validate",
)

import json
import re
import threading
import types
import urllib.request
from typing import Any, Callable, Iterator, NamedTuple

# Replace the model: ``STUB(url, messages, tools, key, model, **kw) -> response dict | None``
# (the shape ``llm_transport.openai_chat_completions`` returns).
STUB: Callable[..., Any] | None = None

_MAX_ERRORS_SHOWN = 5
_ECHO_LIMIT = 2000
# Endpoints that refused ``response_format`` this session (asked without it from then on).
_NO_SCHEMA: set[str] = set()


class Unavailable(Exception):
    """No engine to ask directly.  ``kind``: ``external`` (hand the job to the agent),
    ``no_engine`` (nothing running or configured) or ``no_vision``."""

    def __init__(self, message: str, kind: str = "no_engine") -> None:
        super().__init__(message)
        self.kind = kind


class AskFailed(Exception):
    """The model's answer did not match the schema, also after the retry."""

    def __init__(self, errors: list[str], text: str, attempts: int) -> None:
        super().__init__("The model's answer did not match the schema after {:d} tries: {:s}".format(
            attempts, "; ".join(errors[:_MAX_ERRORS_SHOWN]) or "no JSON found"))
        self.errors = errors
        self.text = text
        self.attempts = attempts


class Result(NamedTuple):
    value: Any              # the checked answer
    attempts: int           # 1 = right first time, 2 = right after the retry
    schema_sent: bool       # whether the request carried ``response_format``
    text: str               # the raw text of the accepted answer


# ---------------------------------------------------------------------------
# Checking an answer against a schema (the JSON Schema subset our prompts use)

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool))
    or (isinstance(v, float) and v == v and v not in (float("inf"), float("-inf")) and v == int(v)),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v == v
    and v not in (float("inf"), float("-inf")),
}


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Errors (empty: valid).  Supports ``type`` (name or list), ``enum``, ``properties``,
    ``required``, ``additionalProperties: false``, ``items``, ``minItems``/``maxItems``,
    ``minimum``/``maximum``, ``minLength``/``maxLength``."""
    errors: list[str] = []
    types_ = schema.get("type")
    if types_ is not None:
        names = [types_] if isinstance(types_, str) else list(types_)
        if not any(_TYPE_CHECKS.get(n, lambda _v: False)(value) for n in names):
            return ["{:s}: expected {:s}, got {:s}".format(path, " or ".join(names), _kind(value))]
    if "enum" in schema and value not in schema["enum"]:
        errors.append("{:s}: must be one of {:s}".format(path, json.dumps(schema["enum"])))
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for key in schema.get("required") or ():
            if key not in value:
                errors.append("{:s}: missing \"{:s}\"".format(path, key))
        for key, sub in props.items():
            if key in value:
                errors += validate(value[key], sub, "{:s}.{:s}".format(path, key))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append("{:s}: unexpected \"{:s}\"".format(path, str(key)))
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("{:s}: needs at least {:d} items".format(path, schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("{:s}: needs at most {:d} items".format(path, schema["maxItems"]))
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errors += validate(item, schema["items"], "{:s}[{:d}]".format(path, i))
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append("{:s}: at least {:d} characters".format(path, schema["minLength"]))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append("{:s}: at most {:d} characters".format(path, schema["maxLength"]))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append("{:s}: at least {:s}".format(path, str(schema["minimum"])))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append("{:s}: at most {:s}".format(path, str(schema["maximum"])))
    return errors


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    return {dict: "object", list: "array", str: "string", int: "integer", float: "number"}.get(
        type(value), type(value).__name__)


# ---------------------------------------------------------------------------
# Finding the JSON in the model's text

def extract_candidates(text: Any) -> Iterator[Any]:
    """Every JSON value in *text*, best guess first: the whole text, fenced blocks, then each
    object or array found in the prose.  Thinking blocks are dropped first."""
    if not isinstance(text, str):
        return
    s = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    s = re.sub(r"^.*?</think>", "", s, flags=re.S | re.I)         # a think block whose opening was cut
    s = s.strip()
    if not s:
        return
    try:
        yield json.loads(s)
        return
    except ValueError:
        pass
    seen: list[Any] = []
    for m in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", s, re.S):
        try:
            seen.append(json.loads(m.group(1).strip()))
        except ValueError:
            continue
    decoder = json.JSONDecoder()
    i = 0
    while i < len(s):
        if s[i] in "{[":
            try:
                value, end = decoder.raw_decode(s, i)
            except ValueError:
                i += 1
                continue
            seen.append(value)
            i = end
        else:
            i += 1
    yield from seen


# ---------------------------------------------------------------------------
# The request

def _response_text(response: Any) -> str | None:
    try:
        content = response["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return content if isinstance(content, str) else None


def _with_instruction(messages: list[dict[str, Any]], schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Copy of *messages* whose last message also says "JSON only, matching this schema"."""
    text = ("Answer with JSON only, no other text, matching this JSON Schema:\n"
            + json.dumps(schema, separators=(",", ":")))
    out = [dict(m) for m in messages]
    if not out:
        return [{"role": "user", "content": text}]
    last = out[-1]
    content = last.get("content")
    if isinstance(content, list):
        last["content"] = list(content) + [{"type": "text", "text": text}]
    else:
        last["content"] = "{:s}\n\n{:s}".format(str(content or ""), text)
    return out


def _schema_field(schema: dict[str, Any], name: str) -> dict[str, Any]:
    return {"response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "strict": True, "schema": schema}}}


def _is_local(url: str) -> bool:
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])[:/]", url))


def reset_schema_memory() -> None:
    """Forget which endpoints refused ``response_format`` (tests)."""
    _NO_SCHEMA.clear()


def ask_json(messages: list[dict[str, Any]], schema: dict[str, Any], *,
             target: tuple[str, str, str], transport: Callable[..., Any],
             name: str = "answer", max_tokens: int = 1024, retries: int = 1,
             send_schema: bool = True, thinking_off: bool = True) -> Result:
    """Ask for a JSON answer that matches *schema*.

    *target* is ``(url, api_key, model)`` (see :func:`engine_target`), *transport* the
    request function (``llm_transport.openai_chat_completions`` or a stand-in).  Returns
    a :class:`Result`; raises :class:`AskFailed` when the answer is wrong after *retries*
    retries, and :class:`Unavailable` when the engine gives no answer at all.
    """
    url, key, model = target
    convo = _with_instruction(messages, schema)
    budget = 0 if (thinking_off and _is_local(url)) else None
    use_schema = bool(send_schema) and url not in _NO_SCHEMA
    errors: list[str] = []
    text = ""
    attempt = 0
    while attempt <= retries:
        attempt += 1
        extra = _schema_field(schema, name) if use_schema else None
        response = transport(url, convo, None, key or None, model or None, max_tokens=max_tokens,
                             thinking_budget_tokens=budget, chat_mode="ASK", extra_body=extra)
        if not response and use_schema:
            # Perhaps the server refuses response_format: once more without it, and remember.
            use_schema = False
            response = transport(url, convo, None, key or None, model or None, max_tokens=max_tokens,
                                 thinking_budget_tokens=budget, chat_mode="ASK", extra_body=None)
            if response:                  # only a server that answers plain was refusing the field
                _NO_SCHEMA.add(url)
        if not response:
            raise Unavailable("The model didn't answer (is the engine running?).")
        text = _response_text(response) or ""
        errors = []
        first_errors: list[str] | None = None
        for candidate in extract_candidates(text):
            found = validate(candidate, schema)
            if not found:
                return Result(candidate, attempt, use_schema, text)
            if first_errors is None:
                first_errors = found
        errors = first_errors if first_errors is not None else ["the answer contained no JSON"]
        if attempt <= retries:
            convo = convo + [
                {"role": "assistant", "content": text[:_ECHO_LIMIT] or "(empty)"},
                {"role": "user", "content": "That answer was not accepted: {:s}. Answer again with JSON only, "
                                            "matching the schema.".format("; ".join(errors[:_MAX_ERRORS_SHOWN]))},
            ]
    raise AskFailed(errors, text, attempt)


# ---------------------------------------------------------------------------
# Which engine to ask

def _chat_url(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


def _local_has_vision(port: int, fetch: Callable[[str], dict[str, Any]] | None = None) -> bool | None:
    """llama-server's ``/props`` says whether a vision projector is loaded (None: can't tell)."""
    def _get(url: str) -> dict[str, Any]:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    try:
        props = (fetch or _get)("http://127.0.0.1:{:d}/props".format(port))
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    modalities = props.get("modalities") if isinstance(props, dict) else None
    if isinstance(modalities, dict) and "vision" in modalities:
        return bool(modalities["vision"])
    return None


def engine_target(operating_mode: str, cfg: Any, running: bool, *, purpose: str = "This tool",
                  vision: bool = False,
                  fetch: Callable[[str], dict[str, Any]] | None = None) -> tuple[str, str, str]:
    """``(url, api_key, model)`` to ask, or :class:`Unavailable` with what to do.

    *purpose* names the caller in the messages; *vision* also needs a model that sees images.
    """
    need = " (with a vision model)" if vision else ""
    if operating_mode in ("EXTERNAL_AGENT", "EXTERNAL_HARNESS"):
        raise Unavailable("{:s} asks the model directly; an External Agent or Harness runs its own. "
                          "Switch Coworker to Local LLM{:s} or Remote API to use it.".format(purpose, need),
                          "external")
    if operating_mode == "REMOTE_API" or getattr(cfg, "mode", "") == "remote":
        url = str(getattr(cfg, "remote_api_url", "") or "")
        if not url:
            raise Unavailable("{:s} needs {:s}: set the Remote API URL, key and model in Coworker's "
                              "Preferences.".format(purpose, "a vision model" if vision else "a model"))
        return (_chat_url(url), str(getattr(cfg, "remote_api_key", "") or ""),
                str(getattr(cfg, "remote_model", "") or ""))
    if not running:
        raise Unavailable("{:s} needs {:s}: start Coworker with a local model{:s} first.".format(
            purpose, "a vision model" if vision else "a running model",
            " that can see images (e.g. Gemma 4 or Qwen3.8)" if vision else ""))
    port = int(getattr(cfg, "local_port", 0) or 0)
    if vision and _local_has_vision(port, fetch) is False:
        raise Unavailable("The local model can't see images (no vision projector loaded). Pick a vision model "
                          "in Coworker's Preferences for {:s}.".format(purpose), "no_vision")
    return "http://127.0.0.1:{:d}/v1/chat/completions".format(port), "", ""


def make_asker(target: tuple[str, str, str], transport: Callable[..., Any]) -> Callable[..., Result]:
    """``ask(messages, schema, **kw) -> Result`` bound to one engine."""
    def ask(messages: list[dict[str, Any]], schema: dict[str, Any], **kw: Any) -> Result:
        return ask_json(messages, schema, target=target, transport=transport, **kw)
    return ask


def resolve_asker(purpose: str = "This tool", vision: bool = False) -> Callable[..., Result]:
    """The asker for Coworker's current engine (or :data:`STUB`); raises :class:`Unavailable`."""
    if STUB is not None:
        return make_asker(("stub://engine/v1/chat/completions", "", ""), STUB)
    from . import llm_manager, llm_transport
    import bpy  # pylint: disable=import-error
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        mode = str(prefs.operating_mode)
    except (KeyError, AttributeError):
        mode = ""
    cfg = llm_manager.get_config()
    target = engine_target(mode, cfg, bool(llm_manager.get_state().is_running), purpose=purpose, vision=vision)
    # Errors go to a throwaway state, so a failed request never shows as the chat's error.
    scratch = types.SimpleNamespace(error="", error_full="", error_kind="", is_thinking=False)

    def transport(*args: Any, **kwargs: Any) -> Any:
        with llm_transport.using(scratch, threading.Event()):
            return llm_transport.openai_chat_completions(*args, **kwargs)
    return make_asker(target, transport)
