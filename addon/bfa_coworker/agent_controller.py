# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Agent Controller — orchestrates the conversation loop inside Blender.

Manages the MCP server subprocess and the LLM conversation
loop. All async I/O runs on a background daemon thread and communicates
results back via ``bpy.app.timers`` for Blender UI integration.
"""

__all__ = (
    "AgentState",
    "ensure_event_loop",
    "schedule_coro",
    "start_mcp_server",
    "start_mcp_server_network",
    "stop_mcp_server",
    "list_mcp_tools",
    "run_conversation_turn",
    "cleanup",
    "ping_agent",
    "warmup_agent",
    "check_ports_available",
    "migrate_vendor_deps",
    "generate_mcp_client_config",
    "validate_mcp_client_config",
    "_get_blender_python_for_config",
)

import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import llm_transport as _transport
from . import session_memory
from . import co_work_guard
from .llm_transport import (
    _CHAT_SAMPLING,
    _DEFAULT_MAX_TOKENS,
    _DEFAULT_TEMPERATURE_CODE,
    _DEFAULT_TEMPERATURE_PROSE,
    classify_llm_500,
    openai_chat_completions,
    openai_chat_completions_stream,
    server_fault_message,
    toolcall_fault_message,
)
import textwrap


# ---------------------------------------------------------------------------
# Constants

_MCP_SERVER_DEFAULT_PORT = 9191
_MCP_SERVER_HEALTH_URL = "http://127.0.0.1:{:d}/health"
_MCP_TOOLS_URL = "http://127.0.0.1:{:d}/tools/list"
_LLM_CHAT_URL = "http://127.0.0.1:{:d}/v1/chat/completions"
# Local (Qwen-family) models often need an extra repair round after an error
# or a truncation, so they get a larger budget; remote models do not and a
# smaller cap keeps a stalled remote run from looping.
_LOCAL_MAX_TOOL_ITERATIONS = 12
_REMOTE_MAX_TOOL_ITERATIONS = 8

# Sampling parameters tuned for MoE local models.
# Defined in llm_transport (the module that sends them) and re-exported
# here via the import block above; the duplicated local copies were
# removed after the tier-3 transport split left them drifting apart.

# Appended to the system prompt in Ask mode (issue #66).  Ask mode is
# informational only: the model must answer in prose and never emit tool
# calls or executable code.  The base prompt describes tool workflows, so
# without this addendum local models frequently call tools anyway.
_ASK_MODE_PROMPT_ADDENDUM = (
    "\n\n[Ask mode] You are in READ-ONLY Ask mode. Answer the user's "
    "questions with explanations and information in plain text. Do NOT "
    "attempt to call tools, run code, or modify the scene or any files. "
    "If the user asks for a change, describe the steps they could take "
    "(or the code they could run) instead of performing it yourself."
)

# (removed: _DEEP_MAX_TOKENS was dead code)

_STREAM_TIMEOUT = 600.0

# Maximum conversation history messages to send per turn.
# Huge history balloons the prompt and makes small models loop.
_MAX_HISTORY_MESSAGES = 20

# ---------------------------------------------------------------------------
# System prompt (loaded lazily, cached per variant)

# Cached prompt text keyed by variant ("compact" | "full").  The variant is
# chosen from the LLM mode, so switching local <-> remote must not reuse the
# other variant's cached text.
_system_prompt_cache: dict[str, str] = {}

# Approximate characters per token for budget estimation.  English prose and
# Python code average ~4 chars/token with BPE tokenizers; 3.5 deliberately
# over-estimates so the budget trims slightly early rather than overflowing.
_CHARS_PER_TOKEN = 3.5

# Reserved headroom (in tokens) for chat-template scaffolding the server adds
# around the messages (BOS/EOS, role markers, tool-schema preamble).
_TEMPLATE_OVERHEAD_TOKENS = 512

# Maximum characters of a tool result sent to the LLM.  Tool results are
# stored in full so the chat panel can display them; this cap is applied only
# when the request is built, so the model gets the gist without the prompt
# ballooning past a small local model's context window.
_MAX_TOOL_RESULT_CHARS = 2000

# Absolute cap on a tool result STORED in history.  The chat panel renders
# from ``conversation_history``, so the stored copy is far larger than the
# send-time cap above -- but a pathological traceback (e.g. a deep
# RecursionError) would otherwise grow the in-memory session without limit.
# Ordinary results are unaffected.
_MAX_STORED_TOOL_RESULT_CHARS = 20000

# Fixed token cost of an injected screenshot.  A vision encoder turns an
# image into a roughly fixed number of tokens regardless of its base64 size,
# so the raw data-URI length must NOT be counted as text.  Used by
# :func:`_message_text_length` so a pending screenshot is budgeted without
# wildly over-trimming the prompt.
_SCREENSHOT_TOKENS = 1500

# Conservative context window used to keep budget enforcement *on* when the
# server's real window cannot be read (``/props`` unreachable) and no value
# is configured.  Trimming slightly is always better than sending an
# unbounded prompt that the server answers with a raw HTTP 400.
_DEFAULT_LOCAL_CTX_FALLBACK = 8192

# Reduced verbatim window used by a *forced* compaction (context-overflow
# recovery).  Smaller than MAX_WINDOW_TURNS so an over-budget prompt has
# something left to retire even after the threshold compaction ran.
_FORCE_COMPACT_KEEP_RECENT = 8

# Minimum reply allowance kept when capping the reply to the space left after
# the prompt (see :func:`_cap_reply_tokens`).  A floor this small is far better
# than refusing the turn; the model can still answer, and auto-continue picks
# up the rest if it hits the limit.
_MIN_REPLY_TOKENS = 256

# Conversation reserve for the domain-skill allowance.  The skill reference
# is injected only into whatever space is genuinely SPARE after the messages,
# the tool schema, and this reserve (room for the turn's own tool exchanges and
# follow-ups), so the allowance tunes itself to the window and the conversation
# — there is no fraction-of-context or ceiling for the user to tune.  A larger
# window, or a shorter conversation, automatically keeps more skills.
_SKILLS_RESERVE_RATIO = 0.30   # reserve this share of the prompt budget
_SKILLS_RESERVE_TOKENS = 1024  # ...but at least this many

# Remote modes keep ``prompt_budget = 0`` (no client-side window), so the
# spare-minus-reserve formula has no input.  Use a conservative flat cap for
# the remote domain-skill allowance (whole files only).  Remote models
# generally predate the Blender 5.2/5.3 API changes, so the version-drift
# skills are especially valuable there.
_SKILLS_REMOTE_MAX_TOKENS = 2000


def _use_compact_prompt() -> bool:
    """Return ``True`` when the compact system prompt should be used.

    Compact is used for local llama-server models only.  Remote providers
    (OpenAI, OpenRouter, Anthropic) get the full prompt: they have large
    context windows and the extra reference material improves answers.

    NOTE: this keys off ``mode``, never ``local_port``.  ``local_port``
    always carries a default (8081), so testing it for ``None`` would select
    the compact prompt even in remote mode.
    """
    try:
        from . import llm_manager as _llm
        return _llm.get_config().mode == "local"
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _prompt_candidates(use_compact: bool) -> list[Path]:
    """Return prompt file paths to try, most-preferred first.

    Two layouts are supported:

    * dev checkout:   ``<repo>/mcp/blmcp/data/prompts.yml``
    * deployed addon: ``<addon>/vendor/blmcp/data/prompts.yml``

    The deployed layout matters: an installed addon has no ``mcp/`` sibling,
    so without the vendor path the prompt silently falls back to the brief
    built-in text.
    """
    this_dir = Path(__file__).resolve().parent
    data_dirs = [
        this_dir.parent.parent / "mcp" / "blmcp" / "data",  # dev checkout
        this_dir / "vendor" / "blmcp" / "data",  # deployed addon
    ]
    names = ["prompts_compact.yml", "prompts.yml"] if use_compact else ["prompts.yml"]
    return [data_dir / name for data_dir in data_dirs for name in names]


def _get_system_prompt(use_compact: bool | None = None) -> str:
    """Load the system prompt, preferring the compact version for local models.

    *use_compact* overrides auto-detection (used by tests).  When ``None``
    the variant is chosen from the LLM mode via :func:`_use_compact_prompt`.
    """
    if use_compact is None:
        use_compact = _use_compact_prompt()
    variant = "compact" if use_compact else "full"
    cached = _system_prompt_cache.get(variant)
    if cached is not None:
        return cached

    for prompt_path in _prompt_candidates(use_compact):
        if not prompt_path.is_file():
            continue
        try:
            with open(str(prompt_path), encoding="utf-8") as fh:
                raw = fh.read()
            # Parse single-key YAML with literal block scalar (|) without yaml lib.
            # Format: "initial_instructions: |\n  indented text..."
            marker = "initial_instructions: |"
            if marker not in raw:
                continue
            _, _, body = raw.partition(marker)
            text = textwrap.dedent(body).strip()
            if text:
                _system_prompt_cache[variant] = text
                print("[🛠️Coworker] _get_system_prompt: loaded {:d} chars ({:s}) from {:s}".format(
                    len(text), variant, str(prompt_path)))
                return text
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[🛠️Coworker] _get_system_prompt: error loading {:s}: {:s}".format(
                str(prompt_path), str(ex)))

    # Fallback: a brief built-in system prompt.
    fallback = (
        "You are a Blender automation assistant. "
        "You have access to tools that can execute Python code in Blender. "
        "Think aloud in full paragraphs. Explain your reasoning step by step. "
        "Summarize tool results in a few words. Avoid fluff and polite filler. "
        "Execute code to complete the user's request, "
        "then respond with a brief summary of what was done."
    )
    _system_prompt_cache[variant] = fallback
    return fallback


def _get_system_prompt_with_rules() -> str:
    """Return the system prompt with skills, project rules, and version info."""
    base = _get_system_prompt()
    try:
        import bpy  # pylint: disable=import-error

        # ── Blender version announcement ──────────────────────
        version_str = ".".join(str(v) for v in bpy.app.version[:3])
        version_header = (
            "You are connected to Blender {:s}. "
            "All code you write must be compatible with this version.\n\n"
            "STYLE: Think aloud in full paragraphs. Explain your reasoning step by step — "
            "what you observe, what you plan to do, and why. The user should be able to "
            "follow your thought process. Be thorough but not repetitive. "
            "When reporting tool results, be brief — just state what happened and whether "
            "it succeeded."
        ).format(version_str)

        # ── Built-in skills (version-aware, from addon/skills/) ──
        try:
            from . import skills as _skills_mod  # pylint: disable=import-error
            # Get user custom skills text from preferences.
            custom_text = ""
            try:
                prefs = bpy.context.preferences.addons[__package__].preferences
                if hasattr(prefs, "custom_skills_text"):
                    custom_text = prefs.custom_skills_text or ""
            except Exception:
                pass
            skills_block = _skills_mod.get_always_loaded_skills(
                bpy_version=bpy.app.version,
                custom_text=custom_text,
            )
            # ── User skills (from SCRIPTS/bfa_coworker_skills/*.md) ──
            user_skills_block = _skills_mod.get_user_skills()
            if user_skills_block:
                if skills_block:
                    skills_block += "\n\n{:s}".format(user_skills_block)
                else:
                    skills_block = user_skills_block
        except Exception:
            skills_block = ""

        # ── Project rules (user .md files) ────────────────────
        rules_dir = Path(bpy.utils.user_resource("SCRIPTS")) / "bfa_coworker_rules"
        rules_parts = []
        global_rules = rules_dir / "global.md"
        if global_rules.exists():
            rules_parts.append(global_rules.read_text(encoding="utf-8"))
        if bpy.data.filepath:
            stem = Path(bpy.data.filepath).stem
            blend_rules = rules_dir / "{:s}.md".format(stem)
            if blend_rules.exists():
                rules_parts.append(blend_rules.read_text(encoding="utf-8"))

        # ── Assemble ──────────────────────────────────────────
        parts: list[str] = [version_header]

        if skills_block:
            parts.append("## Built-in Skills\n{:s}".format(skills_block))

        if rules_parts:
            rules_text = "\n\n".join(rules_parts)
            parts.append(
                "## Project Rules\n"
                "The following project rules MUST be followed:\n\n"
                "{:s}".format(rules_text)
            )

        parts.append("## Instructions\n{:s}".format(base))
        return "\n\n".join(parts)
    except Exception:
        pass
    return base


def _clear_system_prompt_cache() -> None:
    """Clear the cached system prompt and skills so they're rebuilt on next call."""
    _system_prompt_cache.clear()
    try:
        from . import skills as _skills_mod  # pylint: disable=import-error
        _skills_mod.clear_cache()
    except Exception:
        pass


def _repair_tool_call_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop half-finished tool-call exchanges from a message list.

    Slicing a conversation history to fit a context budget can cut a
    tool-call exchange in half.  Both halves are fatal to strict Jinja chat
    templates (llama-server ``--jinja``), which raise
    ``Unexpected message role`` / ``No user query found`` and return HTTP 400:

    * a ``tool`` message whose ``assistant``/``tool_calls`` parent was sliced
      away (orphaned tool result), and
    * an ``assistant`` message carrying ``tool_calls`` whose ``tool`` replies
      were sliced away (orphaned tool call).

    This repairs both directions.  New dicts are not created -- the caller's
    message dicts are reused, and only list membership changes.
    """
    # Pass 1 (forward): drop ``tool`` messages with no preceding assistant
    # message that carries ``tool_calls``.
    paired: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "tool":
            has_parent = any(
                p.get("role") == "assistant" and p.get("tool_calls")
                for p in reversed(paired)
            )
            if not has_parent:
                continue
        paired.append(msg)

    # Pass 2 (forward): drop ``assistant`` messages with ``tool_calls`` that
    # have no immediately-following ``tool`` reply.  Only the run of ``tool``
    # messages directly after the assistant counts -- a later, unrelated tool
    # result must not be mistaken for this call's reply.
    repaired: list[dict[str, Any]] = []
    for index, msg in enumerate(paired):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            has_reply = False
            for following in paired[index + 1:]:
                if following.get("role") != "tool":
                    break
                has_reply = True
                break
            if not has_reply:
                continue
        repaired.append(msg)
    return repaired


def _message_text_length(message: dict[str, Any]) -> int:
    """Return the approximate character length of a message's payload.

    Counts string content, multimodal content blocks, tool-call arguments,
    and tool-call names -- everything that ends up in the rendered prompt.
    """
    total = 0
    content = message.get("content")
    if isinstance(content, str):
        total += len(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                # Text blocks carry "text"; image blocks carry an
                # ``image_url``.  An image's base64 length is meaningless to
                # the text tokenizer (a vision tower consumes a roughly fixed
                # token count), so it is counted at that fixed cost rather
                # than by the data-URI length.
                total += len(str(block.get("text", "")))
                if isinstance(block.get("image_url"), dict):
                    total += int(_SCREENSHOT_TOKENS * _CHARS_PER_TOKEN)
            else:
                total += len(str(block))
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict):
            function = call.get("function", {})
            total += len(str(function.get("name", "")))
            total += len(str(function.get("arguments", "")))
    return total


def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate the total token count of a message list.

    Uses :data:`_CHARS_PER_TOKEN`, which deliberately over-estimates for
    code-heavy prompts so the budget trims slightly early rather than
    overflowing the model's context window.
    """
    return sum(
        int(_message_text_length(m) / _CHARS_PER_TOKEN) + 1
        for m in messages
    )


def _fit_history_to_budget(
    messages: list[dict[str, Any]],
    budget_tokens: int,
) -> list[dict[str, Any]]:
    """Trim *messages* to fit *budget_tokens*, dropping the oldest exchanges.

    The system prompt (index 0, when present) is always kept -- it carries
    the agent's rules and the Blender version.  Everything after it is
    dropped oldest-first in whole tool-call exchanges, so the result never
    contains a half-finished exchange (see :func:`_repair_tool_call_pairs`).

    The **last user message is also always kept**.  Trimming it away leaves
    the model with no question to answer, and it then invents one -- the
    reported "hallucinated task" bug.  A prompt with no user turn is never
    useful, so the budget is allowed to overflow rather than produce one.

    Returns the original list unchanged when it already fits.
    """
    if budget_tokens <= 0 or _estimate_messages_tokens(messages) <= budget_tokens:
        return messages

    has_system = bool(messages) and messages[0].get("role") == "system"
    head = messages[:1] if has_system else []
    tail = messages[1:] if has_system else list(messages)

    # Locate the last user message -- the current request.  It must survive
    # trimming, so it is pinned and only the messages before it are dropped.
    last_user_index = -1
    for index in range(len(tail) - 1, -1, -1):
        if tail[index].get("role") == "user":
            last_user_index = index
            break

    if last_user_index < 0:
        # No user turn at all (e.g. the forced-summary path).  Fall back to
        # the original oldest-first behaviour.
        while tail:
            candidate = head + tail
            if _estimate_messages_tokens(candidate) <= budget_tokens:
                return candidate
            tail = tail[1:]
        return head

    # Everything from the last user message onward is pinned; only the
    # history *before* it may be trimmed.
    pinned = tail[last_user_index:]
    trimmable = tail[:last_user_index]

    while trimmable:
        candidate = head + trimmable + pinned
        if _estimate_messages_tokens(candidate) <= budget_tokens:
            return candidate
        trimmable = trimmable[1:]

    # Even with all older history dropped the prompt overflows.  Return the
    # system prompt plus the pinned turn rather than an empty prompt -- the
    # model must always see the user's question.
    return head + pinned


def _estimate_tools_tokens(tools: list[dict[str, Any]] | None) -> int:
    """Estimate the token cost of the OpenAI tool-schema array.

    The tool JSON is re-sent with every request, so on a 16K local context
    it is a significant share of the budget (the full schema can be several
    thousand tokens). Uses the JSON-serialized size of the schema at
    :data:`_CHARS_PER_TOKEN`, consistent with the message estimator.
    """
    if not tools:
        return 0
    try:
        blob = json.dumps(tools, default=str)
    except (TypeError, ValueError):
        blob = ""
    return int(len(blob) / _CHARS_PER_TOKEN) + _TEMPLATE_OVERHEAD_TOKENS // 2


def _compute_prompt_budget(ctx: int, max_tokens: int) -> int:
    """Return the maximum prompt size (messages + tool schema), in tokens.

    The budget reserves the *minimum* reply space rather than the full
    configured ``max_tokens``.  Reserving the full reply was a real defect:
    ``local_max_tokens`` defaults to the whole context size, so reserving it
    (clamped to half the window) left only ~a third of a 16K window for the
    system prompt and the tool schema — and on a smaller window the fixed
    overhead no longer fit at all, so the very first turn was refused with a
    "conversation no longer fits" error even though nothing had been said.

    The reply is instead capped to whatever legitimately remains after the
    prompt is built (see :func:`_cap_reply_tokens`), so the prompt is never
    starved and ``prompt + reply`` can never exceed the window.

    *max_tokens* is accepted for signature compatibility and no longer
    reduces the prompt budget.
    """
    if ctx <= 0:
        return 0
    margin = max(int(ctx * 0.1), 256)
    budget = ctx - _MIN_REPLY_TOKENS - _TEMPLATE_OVERHEAD_TOKENS - margin
    if budget < 1024:
        # Misconfigured (tiny context): keep a floor that can actually
        # hold a system prompt plus a user turn.
        budget = max(ctx // 2, 1024)
    return budget


def _cap_reply_tokens(ctx: int, requested: int, prompt_tokens: int) -> int:
    """Cap the reply allowance so ``prompt + reply`` fits the window.

    Returns the number of tokens the model may generate for this request:
    the user's request, reduced to the space actually left after the built
    prompt and the fixed chat-template overhead, but never below
    :data:`_MIN_REPLY_TOKENS`.  When even that floor does not fit, the
    caller's preflight has already refused the turn.
    """
    if ctx <= 0:
        return requested if requested > 0 else 0
    margin = max(int(ctx * 0.1), 256)
    room = ctx - prompt_tokens - _TEMPLATE_OVERHEAD_TOKENS - margin
    if room < _MIN_REPLY_TOKENS:
        return _MIN_REPLY_TOKENS
    return min(requested, room) if requested > 0 else room


def _prompt_preflight(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    budget: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """Check a request against the prompt budget right before it is sent.

    Counts everything the server will actually put into the context: the
    message history, the tool schema (re-sent on every request), and any
    screenshot data already injected into the messages. When the estimate
    exceeds the budget the history is re-trimmed with
    :func:`_fit_history_to_budget` (which keeps the system prompt and the
    last user turn). Only when even that pinned turn cannot fit is a
    friendly, actionable error returned instead — surfacing it locally beats
    letting the server reject the POST with a raw 400 the user cannot act
    on.

    Returns ``(messages, error)``; *messages* may be the re-trimmed copy and
    *error* is ``None`` when the request can proceed.
    """
    if budget <= 0:
        return messages, None
    tools_tokens = _estimate_tools_tokens(tools)
    headroom = budget - tools_tokens
    if headroom <= 0:
        # Degenerate (tool schema alone eats the budget): fall back to half
        # the budget rather than trimming the prompt to nothing.
        headroom = max(budget // 2, 1024)
    if _estimate_messages_tokens(messages) <= headroom:
        return messages, None
    fitted = _fit_history_to_budget(messages, headroom)
    fitted = _repair_tool_call_pairs(fitted)
    if _estimate_messages_tokens(fitted) > headroom:
        return fitted, (
            "This conversation no longer fits the local context window — "
            "compacting conversation… Use 'Compact Now' in the Session panel "
            "or start a new chat."
        )
    return fitted, None


def _strip_think_tags(text: str) -> str:
    """Strip ``<think>`` / ``</think>`` wrapper tags from Qwen-style reasoning.

    Some local models (Qwen 2.5/3.x, Fable Fusion, etc.) wrap their
    chain-of-thought in ``<think>...</think>`` blocks inside the
    ``reasoning_content`` field.  These tags are not part of the
    reasoning itself and should be removed before display or storage.
    """
    import re as _re
    text = _re.sub(r"\s*<think>\s*", "", text)
    text = _re.sub(r"\s*</think>\s*", "", text)
    return text.strip()


def _sanitize_loaded_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean a history restored from disk before it is used.

    A persisted history is written by an older build and restored verbatim,
    so it may contain shapes that are invalid to send: ``ui_only`` greeting
    entries, a leading assistant message, ``reasoning`` chain-of-thought
    (a non-standard role), or half-finished tool-call exchanges.  Any of
    these trips a strict Jinja chat template with "Unexpected message role".

    Returns a new list; the caller's dicts are not mutated.  The result
    either starts with the system prompt or is empty -- the system prompt is
    re-inserted on the next turn if it is missing.
    """
    cleaned: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        # UI-only entries (the startup greeting) are not model turns.
        if m.get("ui_only"):
            continue
        # Chain-of-thought uses a non-standard role the template rejects.
        if role == "reasoning":
            continue
        # Strip the marker key so it never reaches the API payload.
        if "ui_only" in m:
            m = {k: v for k, v in m.items() if k != "ui_only"}
        cleaned.append(m)

    # A conversation must not begin with an assistant message.
    while cleaned and cleaned[0].get("role") == "assistant":
        cleaned.pop(0)

    # Drop half-finished tool-call exchanges in both directions.
    cleaned = _repair_tool_call_pairs(cleaned)

    # A trailing user message with no reply is an interrupted turn.  Keeping
    # it would put two user messages in a row once the new request is
    # appended, which strict templates reject.
    if cleaned and cleaned[-1].get("role") == "user":
        cleaned.pop()

    return cleaned


def _trim_history_tool_results(
    messages: list[dict[str, Any]],
    max_chars: int = _MAX_TOOL_RESULT_CHARS,
) -> list[dict[str, Any]]:
    """Return a copy of *messages* with oversized tool results trimmed.

    Tool results are stored in full so the chat panel can show them, but a
    scene dump can be tens of thousands of characters and would balloon the
    prompt past a small local model's context window.  Trimming here -- at
    request-build time -- keeps the display complete while the model still
    gets the gist.

    Only ``tool`` messages are touched, and only when they exceed
    *max_chars*.  New dicts are returned; the caller's history is not
    mutated.
    """
    trimmed: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") != "tool":
            trimmed.append(m)
            continue
        content = m.get("content")
        if not isinstance(content, str) or len(content) <= max_chars:
            trimmed.append(m)
            continue
        trimmed.append({**m, "content": _trim_tool_result(content, max_chars=max_chars)})
    return trimmed


def _strip_reasoning_from_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Remove ``reasoning``-role messages from history before sending to the LLM.

    Reasoning content (chain-of-thought) is stored in history for the UI
    but uses a non-standard ``"reasoning"`` role that most LLM APIs don't
    recognize.  Sending it wastes context window tokens without providing
    useful signal.  We keep it in the full history for UI display but
    strip it before each LLM request.
    """
    return [m for m in messages if m.get("role") != "reasoning"]


def _strip_ui_only_from_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove UI-only messages from history before sending to the LLM.

    Some entries exist purely for the chat panel -- the startup greeting is
    the main one.  They are real history entries so the panel can render
    them, but they are not model turns: sending them adds a phantom
    assistant message (ahead of the system prompt, in the greeting's case)
    that makes the model answer the greeting instead of the user's request.

    The ``ui_only`` key is also dropped from the returned dicts so it never
    reaches the API payload.
    """
    cleaned: list[dict[str, Any]] = []
    for m in messages:
        if m.get("ui_only"):
            continue
        if "ui_only" in m:
            m = {k: v for k, v in m.items() if k != "ui_only"}
        cleaned.append(m)
    return cleaned


_STANDARD_ROLES = frozenset({"system", "user", "assistant", "tool"})


def _sanitize_message_roles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Map any non-standard message roles to ``"user"`` before sending to the LLM.

    Some models (Qwen, etc.) have strict Jinja chat templates that raise
    ``Unexpected message role`` when they encounter a role they don't
    recognise.  This safety net maps unknown roles to ``"user"`` so the
    content is preserved without crashing the template.
    """
    return [
        m if m.get("role") in _STANDARD_ROLES else {**m, "role": "user"}
        for m in messages
    ]


def _describe_message_roles(messages: list[dict[str, Any]]) -> str:
    """Return a compact ``system,user,assistant,...`` role sequence.

    Used to log the exact shape being sent when a chat template rejects a
    request, so the offending role run is visible in the console.
    """
    return ",".join(str(m.get("role", "?")) for m in messages)


def _count_empty_content_messages(messages: list[dict[str, Any]]) -> int:
    """Count messages carrying no content and no tool calls.

    An assistant message left with empty content after ``tool_calls`` are
    stripped is a plausible trigger for a chat template's
    "Unexpected message role" branch, so the request-shape log reports it.
    """
    count = 0
    for m in messages:
        if m.get("tool_calls"):
            continue
        if not _content_as_text(m.get("content")).strip():
            count += 1
    return count


def _describe_history_for_log(messages: list[dict[str, Any]]) -> str:
    """Return a multi-line diagnostic summary of a message list.

    Reports the size, the role sequence, how many messages carry no content,
    and the first user message -- the prompt the model will actually answer.
    That last field is what distinguishes "the model is answering a stale
    prompt" from "the model is hallucinating".
    """
    lines = [
        "  messages   = {:d}".format(len(messages)),
        "  roles      = {:s}".format(_describe_message_roles(messages)),
        "  empty      = {:d}".format(_count_empty_content_messages(messages)),
    ]
    for m in messages:
        if m.get("role") == "user":
            text = _content_as_text(m.get("content")).strip().replace("\n", " ")
            lines.append("  first user = {:s}".format(text[:160] or "(empty)"))
            break
    else:
        lines.append("  first user = (none)")
    return "\n".join(lines)


def _content_as_text(content: Any) -> str:
    """Flatten a message ``content`` value to plain text.

    Content may be a string or a list of multimodal blocks.  Returns ``""``
    for anything that carries no text (e.g. a bare image block).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if text:
                    parts.append(str(text))
            elif block:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _flatten_for_plain_chat(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a tool-calling conversation into plain system/user/assistant.

    llama-server's automatic template-parser generation can fail with HTTP
    400 ("Unable to generate parser for this template ... Unexpected
    message role") when the model's Jinja template cannot represent
    'tool' role messages or assistant 'tool_calls'.  Flattening merges each
    tool result into a user message and drops tool_calls/tool_call_id so
    any chat template can render the conversation.  New dicts are returned
    -- the caller's message dicts are never mutated.

    The output is guaranteed to contain only ``system``, ``user`` and
    ``assistant`` roles.  That guarantee is the whole point of this function:
    a chat template's ``else`` branch raises "Unexpected message role" for
    anything else, so a single stray role makes the retry fail identically to
    the request it was meant to rescue.  Specifically:

    * ``reasoning`` entries are **dropped** -- they are UI-only
      chain-of-thought, already stripped on the normal request path, and the
      non-standard role is what tripped the template in the first place.
    * any other unrecognised role is mapped to ``user`` so its content is
      preserved rather than lost.

    Consecutive messages that end up with the same role are merged, because
    mapping ``tool`` -> ``user`` can produce ``user, user`` runs and strict
    templates reject non-alternating roles.  Merging applies to ``system``
    as well: a second system message (e.g. injected tool text) would
    otherwise sit mid-conversation, which many templates reject outright.
    """
    flat: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        # UI-only chain-of-thought: never send it, and its role is the exact
        # thing the template rejects.
        if role == "reasoning":
            continue
        if role == "tool":
            name = m.get("name", "")
            content = _content_as_text(m.get("content"))
            flat.append({
                "role": "user",
                "content": "[Tool result from {:s}]\n{:s}".format(name, content),
            })
            continue
        cleaned: dict[str, Any] = {}
        for key, value in m.items():
            if key in ("tool_calls", "tool_call_id", "summary", "label",
                       "turn_start", "ui_only"):
                continue
            cleaned[key] = value
        # Normalise content to text so the merge below can always combine
        # runs.  A multimodal list would otherwise block merging and leave a
        # ``user, user`` pair that strict templates reject.
        cleaned["content"] = _content_as_text(cleaned.get("content"))
        # Map any remaining non-standard role to ``user`` so the content
        # survives and the template can render it.
        if cleaned.get("role") not in ("system", "user", "assistant"):
            cleaned["role"] = "user"
        # Drop assistant turns left with no content -- after tool_calls are
        # stripped they carry nothing, and a blank assistant turn is another
        # shape strict templates reject.
        if cleaned.get("role") == "assistant" and not cleaned["content"].strip():
            continue
        flat.append(cleaned)

    # Merge consecutive same-role messages.  ``system`` is included so a
    # second system message is folded into the first rather than left
    # mid-conversation.
    merged: list[dict[str, Any]] = []
    for msg in flat:
        role = msg.get("role")
        if (
            merged
            and role in ("system", "user", "assistant")
            and merged[-1].get("role") == role
        ):
            merged[-1] = {
                "role": role,
                "content": "{:s}\n\n{:s}".format(
                    str(merged[-1].get("content") or ""),
                    str(msg.get("content") or ""),
                ),
            }
            continue
        merged.append(msg)
    return merged


# ── Tool domain system (hybrid: pre-detect + on-demand) ────────────
# Surface tools are always loaded — they cover code execution and basic
# scene inspection.  Domain tools are loaded based on the user's prompt
# (pre-detected) or on-demand via the ``load_tools`` meta-tool.
#
# This keeps the context window small for local models while still
# giving the LLM access to all tools when needed.

_SURFACE_TOOLS = frozenset({
    # ── Code execution ──────────────────────────────────────────────
    "execute_blender_code",
    "execute_blender_plan",  # Two-phase: plan -> tested code
    "list_blender_templates",  # Discover available templates
    # ── Scene inspection ─────────────────────────────────────────────
    "get_blendfile_summary_datablocks",
    "get_blendfile_summary_missing_files",
    "get_blendfile_summary_of_linked_libraries",
    "get_blendfile_summary_path_info",
    "get_blendfile_summary_usage_guess",
    "get_object_detail_summary",
    "get_objects_summary",
    "get_operation_history",      # Avoid repeating failed operations.
    # Read-only asset/polyhaven status — useful for any domain, was previously
    # unreachable because it appeared in no surface/domain set.
    "get_polyhaven_status",
    # ── Visual feedback (always useful for any domain) ────────────────
    "get_screenshot_of_window_as_image",
    "get_screenshot_of_window_as_json",
    "render_thumbnail_to_path",
    # ── Bundled Blender API + manual docs — read-only, no network ────
    # Bundled Blender API + manual docs — read-only, no network
    # Always available so the agent can look up correct APIs on error.
    "get_python_api_docs",
    "search_api_docs",
    "search_manual_docs",
})

_TOOL_DOMAINS: dict[str, frozenset[str]] = {
    "animation": frozenset({
        "jump_to_view3d_object_by_name",
        "jump_to_view3d_object_data_by_name",
        "render_viewport_to_path",
        "batch_keyframe_insert",
    }),
    "material": frozenset({
        "download_polyhaven_asset",
        "search_polyhaven_assets",
        "get_screenshot_of_area_as_image",
        "render_viewport_to_path",
        "setup_pbr_material",
        "assign_material_to_objects",
        "load_asset_in_context",
    }),
    "modeling": frozenset({
        "jump_to_view3d_object_by_name",
        "jump_to_view3d_object_data_by_name",
        "jump_to_tab_by_name",
        "jump_to_tab_by_space_type",
        "get_screenshot_of_area_as_image",
        "set_collection_color_tag",
    }),
    "lighting": frozenset({
        "download_polyhaven_asset",
        "search_polyhaven_assets",
        "render_viewport_to_path",
        "get_screenshot_of_area_as_image",
        "three_point_lighting_rig",
    }),
    "rendering": frozenset({
        "render_viewport_to_path",
        "get_screenshot_of_area_as_image",
        "three_point_lighting_rig",
    }),
    "vse": frozenset({
        "jump_to_tab_by_name",
        "jump_to_tab_by_space_type",
    }),
    "geometry_nodes": frozenset({
        "jump_to_view3d_object_by_name",
        "jump_to_view3d_object_data_by_name",
        "get_screenshot_of_area_as_image",
        "get_active_node_tree",
        "get_node_group_interface",
        "wire_node_group",
    }),
    "assets": frozenset({
        "search_assets",
        "get_asset_libraries",
        "get_asset_tags",
        "list_asset_catalogs",
        "load_asset_in_context",
        "download_polyhaven_asset",
        "search_polyhaven_assets",
        "assign_material_to_objects",
        "place_asset_in_scene",
        "jump_to_asset_browser",
        "get_active_node_tree",
        "get_node_group_interface",
        "wire_node_group",
    }),
}

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "animation": [
        "animate", "keyframe", "fcurve", "armature", "bone", "rig",
        "pose", "timeline", "action", "bounce", "walk cycle", "driver",
    ],
    "material": [
        "material", "shader", "texture", "node", "bsdf", "principled",
        "pbr", "glass", "metal", "rubber", "sss", "subsurface",
    ],
    "modeling": [
        "mesh", "edit", "extrude", "bevel", "loop cut", "knife",
        "sculpt", "boolean", "subdivide", "merge", "bridge",
        "scatter", "duplicate", "array",
    ],
    "lighting": [
        "light", "lamp", "sun", "point", "area", "hdri",
        "world", "environment", "illuminat", "three-point",
    ],
    "rendering": [
        "render", "camera", "eevee", "cycles", "output",
        "resolution", "frame", "focal length", "depth of field",
    ],
    "vse": [
        "sequencer", "strip", "video", "audio", "clip", "edit",
        "cut", "vse", "timeline",
    ],
    "geometry_nodes": [
        "geometry node", "node group", "modifier", "simulation",
        "geonode", "procedural",
    ],
    "assets": [
        "asset", "library", "catalog", "browse", "import asset",
        "append", "link asset", "asset browser",
        "preset", "template", "stock", "material library",
    ],
}

# Synthetic tool schema for on-demand domain loading.
# This is NOT a real MCP tool — the conversation loop intercepts it.
_LOAD_TOOLS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "load_tools",
        "description": (
            "Load additional domain-specific Blender tools. "
            "Domains: "
            + ", ".join(sorted(_TOOL_DOMAINS.keys()))
            + ". Surface tools (code execution, scene inspection, "
            "screenshots, API docs) are always available. "
            "Call load_tools when: (a) you need specialized tools "
            "for a domain, (b) you hit an error and need to look up "
            "the correct API, or (c) the user asks about assets, "
            "materials, animation, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "enum": sorted(_TOOL_DOMAINS.keys()),
                    "description": "The domain to load tools for.",
                }
            },
            "required": ["domain"],
        },
    },
}


def _detect_domains(prompt: str) -> set[str]:
    """Heuristic: detect ALL Blender domains referenced by a user prompt.

    Returns every domain key from ``_TOOL_DOMAINS`` whose keywords appear in
    the prompt (not just the first match), so a multi-domain request such as
    "animate this material" loads both the animation and material tools and
    skill references.
    """
    prompt_lower = prompt.lower()
    found: set[str] = set()
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw in prompt_lower for kw in keywords):
            found.add(domain)
    return found


def _detect_domain_from_scene() -> set[str]:
    """Detect domains from the current scene content.

    Scans ``bpy.data`` for objects, materials, lights, cameras, modifiers,
    sequencer strips, etc. and returns a set of domain keys that match
    what's already in the scene.  This runs in addition to keyword-based
    detection — if the scene has armatures with animation data, the
    "animation" domain is pre-loaded even if the user didn't type "animate".
    """
    domains: set[str] = set()
    try:
        import bpy as _bpy  # pylint: disable=import-error

        # Animation: armatures, actions, or keyframe data.
        if _bpy.data.armatures or _bpy.data.actions:
            domains.add("animation")
        else:
            # Check if any object has animation data.
            for _obj in _bpy.data.objects:
                if getattr(_obj, "animation_data", None) and _obj.animation_data.action:
                    domains.add("animation")
                    break

        # Material: any materials or node groups with shader nodes.
        if _bpy.data.materials or _bpy.data.node_groups:
            domains.add("material")

        # Modeling: meshes with edit-mode potential (any mesh object).
        if _bpy.data.meshes:
            domains.add("modeling")

        # Lighting: any light objects or world setup.
        if _bpy.data.lights or _bpy.data.worlds:
            domains.add("lighting")

        # Rendering: cameras or render settings indicate rendering intent.
        if _bpy.data.cameras:
            domains.add("rendering")

        # VSE: any sequencer strips.
        for _scene in _bpy.data.scenes:
            if _scene.sequence_editor and _scene.sequence_editor.strips:
                domains.add("vse")
                break

        # Geometry Nodes: any object with a geometry nodes modifier.
        for _obj in _bpy.data.objects:
            for _mod in getattr(_obj, "modifiers", []):
                if _mod.type == "NODES":
                    domains.add("geometry_nodes")
                    break
            if "geometry_nodes" in domains:
                break

        # Asset Browser: any configured asset libraries.
        if hasattr(_bpy.context, "preferences") and hasattr(_bpy.context.preferences, "filepaths"):
            if _bpy.context.preferences.filepaths.asset_libraries:
                # Must match the domain key used by _TOOL_DOMAINS / the skill
                # map ("assets"), not a separate "asset_browser" key, or the
                # detected domain is silently ignored.
                domains.add("assets")

    except Exception:
        pass  # Best-effort; not running inside Blender.

    return domains


def _build_tool_set(
    all_openai_tools: list[dict[str, Any]],
    domains: set[str] | None,
) -> list[dict[str, Any]]:
    """Build the tool set for local AND remote mode: surface + domains + load_tools.

    *all_openai_tools* — the full list of all available tools in OpenAI format.
    *domains* — set of pre-detected domains, or ``None`` for surface only.
    """
    allowed = set(_SURFACE_TOOLS)
    if domains:
        for d in domains:
            if d in _TOOL_DOMAINS:
                allowed.update(_TOOL_DOMAINS[d])

    filtered = [
        t for t in all_openai_tools
        if t.get("function", {}).get("name") in allowed
    ]
    # Always include the load_tools meta-tool.
    filtered.append(_LOAD_TOOLS_SCHEMA)
    # Sort by name: a stable, deterministic tool order keeps the provider's
    # prompt-cache prefix valid across turns (only the tool SET changes, never
    # their order).
    filtered.sort(key=lambda t: t.get("function", {}).get("name", ""))

    print("[🛠️Coworker] _build_tool_set: {:d} -> {:d} tools (domains={:s})".format(
        len(all_openai_tools), len(filtered), ",".join(sorted(domains)) if domains else "none"))
    return filtered


# ---------------------------------------------------------------------------
# SSE (Server-Sent Events) parser
# FastMCP in stateless_http mode returns SSE streams even for
# non-streaming JSON-RPC requests.  This extracts JSON payloads
# from each ``data:`` line.

def _parse_sse_json(raw: str) -> dict[str, Any] | None:
    """
    Parse the first JSON payload from an SSE (text/event-stream) body.

    Returns the parsed ``data:`` field as a dict, or ``None`` if no
    valid payload is found.
    """
    for line in raw.splitlines():
        if line.startswith("data: "):
            try:
                return json.loads(line[6:])
            except json.JSONDecodeError:
                continue
    return None


def _parse_sse_text_response(raw: str) -> str:
    """
    Parse SSE body for a tool result, extracting text content blocks.

    Handles both ``type: "text"`` and ``type: "image"`` content blocks.
    For images, returns a descriptive message so the LLM knows the
    screenshot was captured (the image data is not passed to the LLM
    via this path — it goes through the MCP ``Image`` return type).
    """
    result = _parse_sse_json(raw)
    if result is None:
        return "Error: empty or unparseable SSE response"
    if "error" in result:
        return "Error: {:s}".format(str(result["error"]))
    content = result.get("result", {}).get("content", [])
    texts = []
    has_image = False
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type", "")
            if block_type == "text":
                texts.append(block.get("text", ""))
            elif block_type in ("image", "image/png", "image/jpeg", "image/webp"):
                has_image = True
    if texts:
        return "\n".join(texts)
    if has_image:
        return "Screenshot captured successfully (image data returned to LLM)"
    return "Error: no text content in tool result"


def _extract_image_from_tool_result(result: dict) -> str | None:
    """
    Extract a base64-encoded image from a tool result's content blocks.

    Returns the data URI string (e.g. ``"data:image/png;base64,..."``)
    if an image block is found, or ``None`` if there's no image.
    """
    content = result.get("result", {}).get("content", [])
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type", "")
            if block_type in ("image", "image/png", "image/jpeg", "image/webp"):
                data = block.get("data", "") or block.get("source", {}).get("data", "")
                if data:
                    mime = block_type if block_type.startswith("image/") else "image/png"
                    return "data:{:s};base64,{:s}".format(mime, data)
    return None


# ---------------------------------------------------------------------------
# Data types

@dataclass
class AgentState:
    """Runtime state of the agent controller."""

    mcp_server_running: bool = False
    llm_connected: bool = False
    is_thinking: bool = False
    thinking_start_time: float = 0.0  # Timestamp when thinking started (for elapsed timer)
    status_text: str = "Idle"
    error: str = ""
    error_full: str = ""  # Untruncated error text (for copy-to-clipboard troubleshooting)
    error_kind: str = ""  # Machine-readable classification (e.g. "context_overflow")
    warning: str = ""  # Non-fatal notice (e.g. tool-calling downgrade)
    # Context accounting for the Session panel indicator.  Set each turn from
    # the runtime/configured window and the computed prompt budget.
    ctx_size_used: int = 0
    prompt_budget: int = 0
    tool_count: int = 0  # Number of MCP tools available (0 = not loaded yet)
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    streaming_text: str = ""
    reasoning_text: str = ""  # Chain-of-thought from reasoning models
    thinking_dots: int = 0  # Animated spinner state (0-3)

    # ── Token usage tracking (issue #69) ───────────────────────────
    # Per-call usage comes from the LLM response ``usage`` object (stream
    # final chunk or non-streaming body).  Turn totals reset each turn;
    # session totals accumulate for the whole chat session.
    turn_usage: dict[str, int] = field(default_factory=dict)
    session_usage: dict[str, int] = field(default_factory=dict)

    def record_usage(self, usage: dict[str, Any] | None) -> None:
        """Accumulate one LLM call's ``usage`` into turn + session totals."""
        if not isinstance(usage, dict):
            return
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and value >= 0:
                self.turn_usage[key] = self.turn_usage.get(key, 0) + value
                self.session_usage[key] = self.session_usage.get(key, 0) + value

    # ── Liveness tracking (Tier 1) ─────────────────────────────────
    last_bridge_activity: float = 0.0
    last_mcp_activity: float = 0.0
    last_llm_activity: float = 0.0
    bridge_live: bool = False
    mcp_live: bool = False
    llm_live: bool = False

    # ── Re-entrancy guard ──────────────────────────────────────────
    turn_active: bool = False  # True while a conversation turn is in progress.

    # ── Vision pipeline ────────────────────────────────────────────
    _pending_image: str | None = None  # Base64 data URI of last screenshot

    # ── Auto port-shuffle tracking ─────────────────────────────────
    # When a port is in use, the start functions try subsequent ports
    # and store the actual port used here.  0 = use configured port.
    bridge_port_actual: int = 0
    mcp_port_actual: int = 0
    llm_port_actual: int = 0

    # ── Shutdown tracking ──────────────────────────────────────────
    _shutting_down: bool = False  # True during graceful shutdown.


_agent_state = AgentState()

# NOTE: the shared-state binding for the LLM transport layer
# (``_transport.bind`` / ``_transport.bind_helpers``) is deferred to the END
# of this module.  The names it references — ``_stop_event`` and the tool-call
# parsers ``_parse_text_tool_calls`` / ``_parse_xml_tool_calls`` — are defined
# further down, so binding here referenced names that did not exist yet and
# raised ``NameError: name '_stop_event' is not defined`` at import time.


@dataclass
class MessageQueue:
    """Queue for pending user messages to be processed sequentially."""

    _queue: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def enqueue(
        self,
        message: str,
        chat_mode: str = "AGENT",
        llm_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        mcp_port: int = 0,
    ) -> int:
        """Add a message to the queue. Returns the queue position (1-indexed)."""
        with self._lock:
            item = {
                "message": message,
                "chat_mode": chat_mode,
                "llm_url": llm_url,
                "api_key": api_key,
                "model": model,
                "mcp_port": mcp_port,
                "queued_at": time.time(),
            }
            self._queue.append(item)
            return len(self._queue)

    def dequeue(self) -> dict[str, Any] | None:
        """Remove and return the next message from the queue, or None if empty."""
        with self._lock:
            if self._queue:
                return self._queue.pop(0)
            return None

    def peek(self) -> dict[str, Any] | None:
        """Return the next message without removing it."""
        with self._lock:
            if self._queue:
                return self._queue[0]
            return None

    def clear(self) -> None:
        """Remove all messages from the queue."""
        with self._lock:
            self._queue.clear()

    @property
    def pending_count(self) -> int:
        """Number of messages waiting in the queue."""
        with self._lock:
            return len(self._queue)

    @property
    def is_empty(self) -> bool:
        """True if no messages are queued."""
        return self.pending_count == 0

    def get_all(self) -> list[dict[str, Any]]:
        """Return a copy of all queued messages."""
        with self._lock:
            return list(self._queue)


_message_queue = MessageQueue()


def enqueue_message(
    message: str,
    chat_mode: str = "AGENT",
    llm_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    mcp_port: int = 0,
) -> int:
    """Enqueue a user message for processing. Returns queue position."""
    return _message_queue.enqueue(
        message, chat_mode, llm_url, api_key, model, mcp_port,
    )


def dequeue_message() -> dict[str, Any] | None:
    """Dequeue the next message for processing."""
    return _message_queue.dequeue()


# Set to request the in-flight conversation turn to abort. The conversation
# loop checks this between iterations and inside the LLM request path.
_stop_event = threading.Event()


def request_stop() -> None:
    """Request the current generation to stop as soon as possible.

    The co-work scene lock is NOT released here: ``request_stop`` runs on
    Blender's main thread, and running the unlock toolcode would deadlock
    against the main-thread MCP pump.  The active turn's ``finally``
    releases the lock as soon as it unwinds.
    """
    print("[🛠️Coworker] request_stop: stop requested")
    _stop_event.set()
    _agent_state.is_thinking = False
    _agent_state.thinking_start_time = 0.0


def clear_stop() -> None:
    """Clear the stop flag before starting a new turn."""
    _stop_event.clear()


# ---------------------------------------------------------------------------
# Async event loop (background thread)

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None


def _run_async_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Get or create the background async event loop."""
    global _loop, _loop_thread
    if _loop is None or not _loop.is_running():
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_run_async_loop, args=(_loop,), daemon=True)
        _loop_thread.start()
    return _loop


def schedule_coro(coro) -> concurrent.futures.Future:
    """Schedule a coroutine on the background event loop and return a Future."""
    loop = ensure_event_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop)


# ---------------------------------------------------------------------------
# MCP server subprocess management

_mcp_server_process: subprocess.Popen | None = None
_mcp_launch_retry_count: int = 0
_mcp_shutting_down: bool = False

def _get_vendor_deps_dir() -> Path:
    """Return the cache directory for vendored Python dependencies.

    Returns ``~/.cache/bfa_coworker/vendor_deps/``, creating the directory
    if needed.  On first call, migrates any existing ``vendor/deps/`` from
    the legacy addon-relative location into the cache — this removes the
    directory from the addon tree so Blender's sandbox no longer scans it.
    """
    cache = Path.home() / ".cache" / "bfa_coworker" / "vendor_deps"

    # Migration: if the old addon-relative vendor/deps/ still exists,
    # move it to the cache location now.
    legacy = Path(__file__).resolve().parent / "vendor" / "deps"
    if legacy.is_dir() and not cache.is_dir():
        print("[🛠️Coworker] _get_vendor_deps_dir: migrating legacy vendor/deps/ to {:s}".format(str(cache)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        try:
            legacy.rename(cache)
            print("[🛠️Coworker] _get_vendor_deps_dir: migration successful — removed from addon tree")
        except OSError:
            # Rename may fail across filesystems — fall back to copy.
            print("[🛠️Coworker] _get_vendor_deps_dir: rename failed, copying instead...")
            import shutil as _shutil
            _shutil.copytree(str(legacy), str(cache))
            _shutil.rmtree(str(legacy), ignore_errors=True)
            print("[🛠️Coworker] _get_vendor_deps_dir: copy+remove successful")
    elif not cache.is_dir():
        cache.mkdir(parents=True, exist_ok=True)

    return cache


def migrate_vendor_deps() -> None:
    """Eagerly migrate vendor/deps/ out of the addon tree if present.

    Called from ``__init__.py`` during ``register()``, before any sandbox
    scan might detect the vendored top-level packages.
    """
    _get_vendor_deps_dir()


def _find_blender_python() -> str | None:
    """Return the path to Blender's bundled Python executable.

    Blender ships with its own Python interpreter.  On Windows the Python
    binary lives at ``{sys.prefix}/bin/python.exe``; on Linux/macOS it is
    ``{sys.prefix}/bin/python3``.

    We do **not** use ``sys.executable`` here because in Blender's embedded
    Python that points to the Blender executable (``blender.exe``), not a
    Python interpreter.

    Returns ``None`` if no suitable Python is found (unlikely in a running
    Blender add-on, but handled gracefully).
    """
    if sys.platform == "win32":
        # Standard Blender layout: sys.prefix/bin/python.exe
        py_path = Path(sys.prefix) / "bin" / "python.exe"
        if py_path.is_file():
            return str(py_path)
        # Some installations put python.exe directly in sys.prefix.
        py_path = Path(sys.prefix) / "python.exe"
        if py_path.is_file():
            return str(py_path)
        return None

    # Linux/macOS
    py_path = Path(sys.prefix) / "bin" / "python3"
    return str(py_path) if py_path.is_file() else None


def _find_vendor_pythonpath() -> str:
    """Build a PYTHONPATH string pointing at the addon's vendor directories.

    Returns a ``os.pathsep``-joined string suitable for the ``PYTHONPATH``
    environment variable.  The returned path includes:

    * ``~/.cache/bfa_coworker/vendor_deps/`` — pip-installed pure-Python
      dependencies (mcp, pyyaml, docutils, and their transitive deps).
    * ``vendor/`` — parent of ``vendor/blmcp/``, so ``import blmcp``
      resolves to ``vendor/blmcp/__init__.py``.

    If a directory does not exist, it is silently omitted so the addon
    can fall back gracefully during development.
    """
    this_dir = Path(__file__).resolve().parent
    vendor_dir = this_dir / "vendor"
    parts: list[str] = []

    deps_dir = _get_vendor_deps_dir()
    if deps_dir.is_dir():
        parts.append(str(deps_dir))

        # pywin32 layout: the importable ``pywintypes``/``pythoncom`` modules
        # live in ``win32/lib/`` and are normally exposed via a ``pywin32.pth``
        # file.  ``.pth`` files are only processed for real site-packages
        # directories at interpreter startup — NOT for PYTHONPATH entries.
        # Since the MCP subprocess only gets these dirs via PYTHONPATH, the
        # .pth is ignored, so we must add the pywin32 subdirectories directly.
        for sub in ("win32", "win32/lib", "win32com", "win32comext"):
            sub_dir = deps_dir / sub
            if sub_dir.is_dir():
                parts.append(str(sub_dir))

    # Add vendor/ itself so blmcp resolves from vendor/blmcp/.
    if vendor_dir.is_dir():
        parts.append(str(vendor_dir))

    return os.pathsep.join(parts)


def _ensure_vendor_deps() -> bool:
    """Check that vendor deps exist with required packages; auto-install if missing.

    Handles the case where a user installs the addon from source
    (e.g. by copying the addon directory) without running ``build_addon.py``
    first.  If the vendor deps cache is missing or empty, we attempt to install
    the required packages using Blender's ``pip``.

    Returns ``True`` if the deps are available (or were installed), ``False``
    if installation failed.
    """
    this_dir = Path(__file__).resolve().parent
    deps_dir = _get_vendor_deps_dir()

    # Quick check: does the cache exist and contain mcp?
    if deps_dir.is_dir() and (deps_dir / "mcp" / "__init__.py").is_file():
        # Also require the blmcp package itself.  A source install can have
        # deps but no vendor/blmcp/, which previously slipped through here and
        # surfaced later as a bare "No module named 'blmcp'" traceback.
        problems = _check_vendor_layout()
        if not problems:
            return True
        print("[🛠️Coworker] _ensure_vendor_deps: deps present but layout incomplete:")
        for problem in problems:
            print("[🛠️Coworker] _ensure_vendor_deps:   - {:s}".format(problem))
        # blmcp is source, not a wheel — pip cannot install it.  Try the
        # dev-checkout copy first, then report.
        if _copy_dev_blmcp_into_vendor():
            return not _check_vendor_layout()
        return False

    print("[🛠️Coworker] _ensure_vendor_deps: vendor deps cache is missing or empty — attempting auto-install...")

    # Try to install using Blender's pip.
    blender_py = _find_blender_python()
    if not blender_py:
        print("[🛠️Coworker] _ensure_vendor_deps: cannot find Blender's Python for auto-install")
        return False

    # Bootstrap pip if needed (ensurepip is stdlib, always available).
    try:
        subprocess.run(
            [blender_py, "-m", "ensurepip", "--upgrade"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # pip may already be installed.

    try:
        deps_dir.mkdir(parents=True, exist_ok=True)
        pip_packages = ["mcp[cli]>=1.2.0,<2.0.0", "pyyaml", "docutils"]
        if sys.platform == "win32":
            pip_packages.append("pywin32")
        result = subprocess.run(
            [blender_py, "-m", "pip", "install",
             "--target", str(deps_dir),
             "--no-compile",
             ] + pip_packages,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            print("[🛠️Coworker] _ensure_vendor_deps: pip install failed (exit {:d})".format(
                result.returncode))
            print("[🛠️Coworker] _ensure_vendor_deps: stderr = {:s}".format(result.stderr[-2000:] or "(empty)"))
            print("[🛠️Coworker] _ensure_vendor_deps: stdout = {:s}".format(result.stdout[-2000:] or "(empty)"))
            return False
        # Verify that the critical import actually works.
        blender_py_verify = _find_blender_python()
        if blender_py_verify:
            vendor_pp = _find_vendor_pythonpath()
            verify_env = os.environ.copy()
            if vendor_pp:
                verify_env["PYTHONPATH"] = vendor_pp
            # On Windows, pywin32 DLLs must be on PATH for import verification.
            if sys.platform == "win32":
                pywin32_system32 = _get_vendor_deps_dir() / "pywin32_system32"
                if pywin32_system32.is_dir():
                    verify_env["PATH"] = str(pywin32_system32) + os.pathsep + verify_env.get("PATH", "")
            verify = subprocess.run(
                [blender_py_verify, "-c", "import mcp.server.fastmcp"],
                capture_output=True, text=True, timeout=30, env=verify_env,
            )
            if verify.returncode != 0:
                print("[🛠️Coworker] _ensure_vendor_deps: post-install import verification FAILED")
                print("[🛠️Coworker] _ensure_vendor_deps: verify stderr = {:s}".format(
                    verify.stderr[-1500:] or "(empty)"))
                return False
            print("[🛠️Coworker] _ensure_vendor_deps: post-install import verification OK")
        # Clean __pycache__ to save space.
        for root, dirs, _files in os.walk(str(deps_dir)):
            if '__pycache__' in dirs:
                shutil.rmtree(os.path.join(root, '__pycache__'), ignore_errors=True)
        print("[🛠️Coworker] _ensure_vendor_deps: auto-install succeeded")
        return True
    except Exception as ex:
        print("[🛠️Coworker] _ensure_vendor_deps: auto-install failed — {:s}".format(str(ex)))
        return False


def _copy_dev_blmcp_into_vendor() -> bool:
    """Copy ``<repo>/mcp/blmcp`` into ``<addon>/vendor/blmcp`` when missing.

    ``blmcp`` is source, not a wheel, so ``pip install`` cannot provide it.
    In a source checkout the package sits at ``<repo>/mcp/blmcp``; a built
    addon expects it at ``<addon>/vendor/blmcp``.  When only the former
    exists, copy it across so ``python -m blmcp`` resolves.

    Returns ``True`` when ``vendor/blmcp`` exists afterwards.
    """
    this_dir = Path(__file__).resolve().parent
    vendor_blmcp = this_dir / "vendor" / "blmcp"
    if (vendor_blmcp / "__init__.py").is_file():
        return True

    dev_blmcp = this_dir.parent.parent / "mcp" / "blmcp"
    if not (dev_blmcp / "__init__.py").is_file():
        print("[🛠️Coworker] _copy_dev_blmcp_into_vendor: no dev checkout at {:s}".format(
            str(dev_blmcp)))
        return False

    print("[🛠️Coworker] _copy_dev_blmcp_into_vendor: copying {:s} -> {:s}".format(
        str(dev_blmcp), str(vendor_blmcp)))
    try:
        vendor_blmcp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            str(dev_blmcp), str(vendor_blmcp),
            ignore=shutil.ignore_patterns("__pycache__"),
            dirs_exist_ok=True,
        )
    except OSError as ex:
        print("[🛠️Coworker] _copy_dev_blmcp_into_vendor: copy failed — {:s}".format(str(ex)))
        return False
    print("[🛠️Coworker] _copy_dev_blmcp_into_vendor: copy succeeded")
    return True


def _vendor_pythonpath_report() -> tuple[str, list[str]]:
    """Return ``(pythonpath, missing_dirs)`` for the vendored MCP layout.

    ``_find_vendor_pythonpath()`` silently omits directories that do not
    exist, so a broken layout yields an empty (or partial) ``PYTHONPATH``
    with no warning — the failure then only shows up as an opaque
    ``ImportError`` in the child process.  This companion reports what was
    expected but absent so callers can warn up front.
    """
    this_dir = Path(__file__).resolve().parent
    vendor_dir = this_dir / "vendor"
    deps_dir = _get_vendor_deps_dir()

    missing: list[str] = []
    if not (deps_dir / "mcp" / "__init__.py").is_file():
        missing.append(str(deps_dir))
    if not (vendor_dir / "blmcp" / "__init__.py").is_file():
        # A source checkout keeps blmcp at <repo>/mcp/blmcp instead.
        dev_blmcp = this_dir.parent.parent / "mcp" / "blmcp"
        if not (dev_blmcp / "__init__.py").is_file():
            missing.append(str(vendor_dir / "blmcp"))

    return (_find_vendor_pythonpath(), missing)


# ---------------------------------------------------------------------------
# MCP launch failure diagnosis
#
# A bare "MCP server exited immediately" is useless to the user: the real
# cause is the last line of the child's stderr (e.g. ``ImportError: No module
# named 'blmcp'``), which the old code truncated away.  These helpers extract
# the exception line, keep the untruncated text for copy-to-clipboard, and
# map known signatures to an actionable hint.

# Known failure signatures -> actionable hint.  Matched case-insensitively
# against the child's combined stderr/stdout.
_MCP_FAILURE_HINTS: tuple[tuple[str, str], ...] = (
    (
        "fastmcp was renamed to mcpserver",
        "The installed MCP SDK is 2.x, which removed FastMCP. The add-on "
        "requires mcp<2. Run 'python build_addon.py' to reinstall the pinned "
        "vendor deps, or: pip install 'mcp[cli]>=1.2.0,<2.0.0'",
    ),
    (
        "no module named 'mcp.server.fastmcp'",
        "The installed MCP SDK is 2.x, which removed FastMCP. The add-on "
        "requires mcp<2. Run 'python build_addon.py' to reinstall the pinned "
        "vendor deps, or: pip install 'mcp[cli]>=1.2.0,<2.0.0'",
    ),
    (
        "no module named 'blmcp'",
        "The blmcp package is not on PYTHONPATH. Run 'python build_addon.py' "
        "to populate vendor/blmcp/, or enable \"Use Blender's Python\" so the "
        "config emits the vendor directory.",
    ),
    (
        "no module named 'mcp'",
        "The MCP SDK is missing. Run 'python build_addon.py' to populate "
        "vendor/deps/, or install it: pip install bfa-coworker-mcp",
    ),
    (
        "no module named 'yaml'",
        "PyYAML is missing from the vendor deps. Run 'python build_addon.py' "
        "to reinstall vendor/deps/.",
    ),
    (
        "no module named 'docutils'",
        "docutils is missing from the vendor deps. Run 'python build_addon.py' "
        "to reinstall vendor/deps/.",
    ),
    (
        "no module named 'starlette'",
        "Starlette is missing from the vendor deps (needed for HTTP "
        "transport). Run 'python build_addon.py' to reinstall vendor/deps/.",
    ),
    (
        "no module named 'pydantic_core'",
        "pydantic's native extension does not match this Python version. "
        "Run 'python build_addon.py' to reinstall vendor/deps/ for Blender's "
        "Python.",
    ),
    (
        "no module named 'pywintypes'",
        "pywin32 is installed but its modules are not on PYTHONPATH. The "
        "add-on adds win32/ and win32/lib/ automatically — re-copy the config "
        "from preferences, or run 'python build_addon.py'.",
    ),
    (
        "no module named 'win32",
        "pywin32 is missing from the vendor deps. Run 'python build_addon.py' "
        "to reinstall vendor/deps/.",
    ),
    (
        "no module named",
        "A required package is missing from PYTHONPATH. Run "
        "'python build_addon.py' to rebuild the vendor directories.",
    ),
    (
        "modulenotfounderror",
        "A required package is missing from PYTHONPATH. Run "
        "'python build_addon.py' to rebuild the vendor directories.",
    ),
    (
        "importerror",
        "The MCP server failed to import a dependency. Run "
        "'python build_addon.py' to rebuild the vendor directories.",
    ),
    (
        "address already in use",
        "The port is already bound by another process. Stop the other MCP "
        "server, or raise port_offset in Preferences (Advanced tab).",
    ),
    (
        "only one usage of each socket address",
        "The port is already bound by another process. Stop the other MCP "
        "server, or raise port_offset in Preferences (Advanced tab).",
    ),
    (
        "permissionerror",
        "Permission denied. A firewall or antivirus may be blocking the "
        "Python interpreter, or the port is reserved.",
    ),
    (
        "is not a valid win32 application",
        "The Python interpreter path is wrong or corrupt. Re-enable "
        "\"Use Blender's Python\" to regenerate the config.",
    ),
    (
        "no such file or directory",
        "The Python interpreter path does not exist. Re-enable "
        "\"Use Blender's Python\" to regenerate the config.",
    ),
    (
        "cannot find the file",
        "The Python interpreter path does not exist. Re-enable "
        "\"Use Blender's Python\" to regenerate the config.",
    ),
)


def _classify_mcp_failure(text: str) -> str:
    """Return an actionable hint for a known MCP launch failure, else ``""``.

    *text* is the child process's combined stderr/stdout (or any error text).
    Matching is case-insensitive and first-match-wins, so the specific
    ``No module named 'blmcp'`` entry is checked before the generic
    ``no module named`` fallback.
    """
    if not text:
        return ""
    lowered = text.lower()
    for signature, hint in _MCP_FAILURE_HINTS:
        if signature in lowered:
            return hint
    return ""


def _summarize_mcp_failure(stderr_output: str, stdout_output: str) -> tuple[str, str]:
    """Extract a short, useful summary and the full text from child output.

    Returns ``(summary, full)`` where:

    * *full* is the untruncated combined output (for ``error_full`` and
      copy-to-clipboard troubleshooting).
    * *summary* is the **last non-empty line** — the actual exception, e.g.
      ``ImportError: No module named 'blmcp'`` — plus an actionable hint when
      the signature is recognised.  Falls back to the first line when the
      output has no trailing exception line.

    The old code used ``error_detail[:200]``, which kept the *first* 200
    characters of a traceback — i.e. the ``Traceback (most recent call last)``
    header and the ``runpy`` frames — and cut off the exception itself.
    """
    stderr_output = stderr_output or ""
    stdout_output = stdout_output or ""
    full = stderr_output or stdout_output or "no output"

    # The exception is the last non-empty line of stderr (falling back to
    # stdout when stderr is empty).
    lines = [ln.strip() for ln in full.splitlines() if ln.strip()]
    if lines:
        summary = lines[-1]
    else:
        summary = "no output"

    hint = _classify_mcp_failure(full)
    if hint:
        summary = "{:s} — {:s}".format(summary, hint)
    return (summary, full)


def _check_vendor_layout() -> list[str]:
    """Return a list of problems with the vendored MCP layout.

    An empty list means the layout looks usable.  Checks the two things the
    MCP subprocess needs on ``PYTHONPATH``:

    * ``vendor/deps/mcp/`` — the MCP SDK (plus its transitive deps).
    * ``vendor/blmcp/`` — the server package itself, including the
      ``data/prompts.yml`` that ``blmcp.main()`` opens unconditionally.

    ``_ensure_vendor_deps()`` historically only checked the first, so a
    source install with deps but no ``vendor/blmcp/`` produced a bare
    ``ImportError: No module named 'blmcp'`` traceback instead of a clear
    message.
    """
    problems: list[str] = []
    this_dir = Path(__file__).resolve().parent

    deps_dir = _get_vendor_deps_dir()
    if not (deps_dir / "mcp" / "__init__.py").is_file():
        problems.append(
            "vendor deps missing: {:s} (run 'python build_addon.py')".format(
                str(deps_dir / "mcp"))
        )

    # blmcp may live in the addon's vendor/ (built addon) or in the repo's
    # mcp/ directory (source checkout).
    vendor_blmcp = this_dir / "vendor" / "blmcp"
    dev_blmcp = this_dir.parent.parent / "mcp" / "blmcp"
    blmcp_dir = vendor_blmcp if vendor_blmcp.is_dir() else dev_blmcp
    if not (blmcp_dir / "__init__.py").is_file():
        problems.append(
            "blmcp package missing: {:s} (run 'python build_addon.py')".format(
                str(vendor_blmcp))
        )
    elif not (blmcp_dir / "data" / "prompts.yml").is_file():
        problems.append(
            "blmcp data missing: {:s} (run 'python build_addon.py')".format(
                str(blmcp_dir / "data" / "prompts.yml"))
        )

    return problems


def _start_pipe_drainer(proc: subprocess.Popen) -> tuple[list[threading.Thread], list[str], list[str]]:
    """Spawn background threads to drain stdout/stderr pipes.

    Without this, ``subprocess.PIPE`` buffers (4 KB on Windows) fill up
    and the child process blocks on write, never reaching ``mcp.run()``.
    Collected lines are appended to the returned lists for diagnostics.

    Returns ``(drainer_threads, stdout_lines, stderr_lines)``.
    """
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    lock = threading.Lock()
    threads: list[threading.Thread] = []

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            decoded = line.decode(errors="replace").rstrip("\n\r")
            with lock:
                stderr_lines.append(decoded)

    def _drain_stdout() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            decoded = line.decode(errors="replace").rstrip("\n\r")
            with lock:
                stdout_lines.append(decoded)

    t1 = threading.Thread(target=_drain_stderr, daemon=True)
    t1.start()
    threads.append(t1)

    t2 = threading.Thread(target=_drain_stdout, daemon=True)
    t2.start()
    threads.append(t2)

    return threads, stdout_lines, stderr_lines


def _kill_process_on_port(port: int) -> None:
    """Kill any process listening on *port* (platform-independent)."""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if ":{} ".format(port) in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    subprocess.run(
                        ["taskkill", "/f", "/pid", pid],
                        capture_output=True, timeout=5,
                    )
                    print("[🛠️Coworker] _kill_process_on_port: killed PID {:s} on port {:d}".format(pid, port))
                    break
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    else:
        try:
            subprocess.run(
                ["fuser", "-k", "{:d}/tcp".format(port)],
                capture_output=True, timeout=10,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def _wait_for_port(
    host: str,
    port: int,
    timeout: float = 15.0,
    interval: float = 1.0,
    proc: "subprocess.Popen | None" = None,
) -> bool:
    """Wait for *port* to start accepting TCP connections.

    Polls ``socket.create_connection`` every *interval* seconds, up to
    *timeout* total.  Returns ``True`` as soon as the port accepts,
    ``False`` if the timeout expires.

    If *proc* is given, the wait aborts early (returns ``False``) the moment
    the process exits — so a crashed llama-server surfaces immediately
    instead of hanging for the full timeout.
    """
    import time
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with socket.create_connection((host, port), timeout=0.5):
                elapsed = timeout - (deadline - time.monotonic())
                print("[🛠️Coworker] _wait_for_port: {:s}:{:d} ready after {:.1f}s".format(
                    host, port, elapsed))
                return True
        except (OSError, socket.error):
            pass
        if proc is not None:
            rc = proc.poll()
            if rc is not None:
                print("[🛠️Coworker] _wait_for_port: {:s}:{:d} — process exited early (rc={:d}), aborting wait".format(
                    host, port, rc))
                return False
        if attempt % 2 == 0:
            print("[🛠️Coworker] _wait_for_port: still waiting for {:s}:{:d} ({:.0f}s remaining)".format(
                host, port, deadline - time.monotonic()))
        time.sleep(interval)
    print("[🛠️Coworker] _wait_for_port: TIMEOUT — {:s}:{:d} not ready after {:.1f}s".format(
        host, port, timeout))
    return False


def check_ports_available(
    bridge_port: int = 9876,
    mcp_port: int = 9191,
    llm_port: int = 8081,
) -> dict[str, bool]:
    """Test whether each port is available (not in use) by attempting to bind.

    Returns ``{port_label: is_available, ...}``.
    """
    result: dict[str, bool] = {}
    for label, p in [("bridge", bridge_port), ("mcp", mcp_port), ("llm", llm_port)]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", p))
            s.close()
            result[label] = True
        except (OSError, socket.error) as ex:
            s.close()
            result[label] = False
            print("[🛠️Coworker] check_ports_available: {:s} port {:d} is in use — {:s}".format(
                label, p, str(ex)))
    return result


def _find_available_port(preferred: int, max_offset: int = 100) -> int:
    """Return the first available port starting at *preferred*.

    Tries ``preferred``, ``preferred + 1``, … up to ``preferred + max_offset``.
    Returns the first port that can be bound, or 0 if none are available.
    """
    for offset in range(max_offset + 1):
        candidate = preferred + offset
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", candidate))
            s.close()
            if offset > 0:
                print("[🛠️Coworker] _find_available_port: port {:d} in use, shuffled to {:d}".format(
                    preferred, candidate))
            return candidate
        except (OSError, socket.error):
            s.close()
            continue
    print("[🛠️Coworker] _find_available_port: no port available in range {:d}–{:d}".format(
        preferred, preferred + max_offset))
    return 0


def _resolve_mcp_python() -> tuple[str | None, bool]:
    """Resolve the Python executable and whether to use ``-m blmcp``.

    Resolution order:
    1. ``bfa-coworker-mcp`` console_scripts entry point (if user has it on PATH).
    2. Blender's bundled Python (``sys.prefix/bin/python.exe``) with
       ``vendor/deps/`` and ``vendor/blmcp/`` on ``PYTHONPATH``.
    3. ``python`` from PATH as a last resort.

    Returns ``(python_path, use_module)`` where *use_module* is True
    when the MCP server should be launched via ``python -m blmcp``.
    """
    mcp_exe: str | None = None
    use_module = False

    # 1. Check for a pip-installed console_scripts entry point.
    mcp_exe = (
        shutil.which("bfa-coworker-mcp") or
        shutil.which("bfa-coworker-mcp.exe") or
        shutil.which("bfa-coworker-mcp.bat")
    )

    # 2. Fall back to Blender's bundled Python with vendor deps.
    if not mcp_exe:
        if not _ensure_vendor_deps():
            # Report the specific layout problem rather than a generic
            # "dependencies not found" — the two causes need different fixes.
            problems = _check_vendor_layout()
            if problems:
                _agent_state.error = (
                    "MCP server layout incomplete — {:s}. "
                    "Run 'python build_addon.py' to build the extension.".format(
                        "; ".join(problems))
                )
            else:
                _agent_state.error = (
                    "MCP server dependencies not found in vendor deps cache. "
                    "Run 'python build_addon.py' to build the extension, "
                    "or install manually: pip install --target ~/.cache/bfa_coworker/vendor_deps/ mcp[cli] pyyaml docutils"
                )
            return (None, False)

        blender_py = _find_blender_python()
        if blender_py:
            if _vendor_native_compat(blender_py):
                mcp_exe = blender_py
                use_module = True
                print("[🛠️Coworker] _resolve_mcp_python: using Blender's Python at {:s}".format(mcp_exe))
            else:
                # Blender's Python is incompatible; try system python.
                print("[🛠️Coworker] _resolve_mcp_python: Blender's Python {!s} incompatible with vendor native extensions".format(blender_py))
                sys_py = shutil.which("python3") or shutil.which("python")
                if sys_py and _vendor_native_compat(sys_py):
                    mcp_exe = sys_py
                    use_module = True
                    print("[🛠️Coworker] _resolve_mcp_python: using compatible system Python at {:s}".format(mcp_exe))

    # 3. Last resort: system python.
    if not mcp_exe:
        mcp_exe = shutil.which("python") or "python"
        use_module = True
        print("[🛠️Coworker] _resolve_mcp_python: falling back to system python at {:s}".format(mcp_exe))

    return (mcp_exe, use_module)


def _build_mcp_env(
    blender_host: str = "localhost",
    blender_port: int = 9876,
) -> dict[str, str]:
    """Build environment dict for the MCP server subprocess.

    Sets ``BFACW_HOST``, ``BFACW_PORT``, and configures ``PYTHONPATH``
    with vendor directories when using Blender's Python.
    """
    env = os.environ.copy()
    env["BFACW_HOST"] = blender_host
    env["BFACW_PORT"] = str(blender_port)

    # Build PYTHONPATH from vendor directories.
    vendor_pythonpath = _find_vendor_pythonpath()
    existing_pp = env.get("PYTHONPATH", "")
    if vendor_pythonpath:
        env["PYTHONPATH"] = vendor_pythonpath + (os.pathsep + existing_pp if existing_pp else "")

    # On Windows, pywin32 needs its _system32/ DLL directory on PATH.
    if sys.platform == "win32":
        pywin32_system32 = _get_vendor_deps_dir() / "pywin32_system32"
        if pywin32_system32.is_dir():
            env["PATH"] = str(pywin32_system32) + os.pathsep + env.get("PATH", "")

    return env


def start_mcp_server(
    port: int = _MCP_SERVER_DEFAULT_PORT,
    blender_host: str = "localhost",
    blender_port: int = 9876,
    _retry_depth: int = 0,
) -> subprocess.Popen | None:
    """
    Launch the MCP server as a subprocess with HTTP transport.

    Python resolution order:
    1. ``bfa-coworker-mcp`` console_scripts entry point (if user has it on PATH).
    2. Blender's bundled Python (``sys.prefix/bin/python.exe``) with
       ``vendor/deps/`` and ``vendor/blmcp/`` on ``PYTHONPATH``.
    3. ``python`` from PATH as a last resort.

    *``_retry_depth``* is an internal parameter to cap dependency reinstall
    retries at 1 to prevent infinite recursion when imports keep failing.

    Returns the ``Popen`` handle, or ``None`` on failure.
    """
    global _mcp_server_process, _mcp_launch_retry_count, _mcp_shutting_down

    if _mcp_shutting_down:
        print("[🛠️Coworker] start_mcp_server: shutdown in progress — skipping launch")
        return None

    # Kill existing process if known.
    if _mcp_server_process is not None:
        try:
            _mcp_server_process.terminate()
            _mcp_server_process.wait(timeout=3)
        except Exception:  # pylint: disable=broad-exception-caught
            try:
                _mcp_server_process.kill()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        _mcp_server_process = None
        # Brief delay for OS to release the port.
        import time
        time.sleep(0.5)

    # Kill any stale process occupying the port (from addon reinstall or crash).
    _kill_process_on_port(port)
    import time
    time.sleep(0.5)  # Let OS release the port.

    # Auto-shuffle: if the port is still in use after killing, find the next
    # available port so the subprocess doesn't fail to bind.
    shuffled_port = _find_available_port(port)
    if shuffled_port == 0:
        _agent_state.error = (
            "MCP port {:d} is in use and no subsequent port is available. "
            "Increase port_offset in Preferences (Advanced tab).".format(port)
        )
        return None
    if shuffled_port != port:
        print("[🛠️Coworker] start_mcp_server: port {:d} in use, shuffled to {:d}".format(port, shuffled_port))
        port = shuffled_port
    _agent_state.mcp_port_actual = port

    env = _build_mcp_env(blender_host=blender_host, blender_port=blender_port)

    # --- Resolution order ---
    mcp_exe, use_module = _resolve_mcp_python()

    if not mcp_exe:
        _agent_state.error = "Cannot find Python to run MCP server"
        return None

    # --- Launch ---

    try:
        if use_module:
            print("[🛠️Coworker] start_mcp_server: running {:s} -m blmcp with PYTHONPATH={:s}".format(
                mcp_exe, env.get("PYTHONPATH", "(unset)")))
            proc = subprocess.Popen(
                [mcp_exe, "-m", "blmcp", "--transport", "http", "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        else:
            proc = subprocess.Popen(
                [mcp_exe, "--transport", "http", "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
    except FileNotFoundError as ex:
        _agent_state.error = "Failed to launch MCP server: {:s}".format(str(ex))
        return None
    except OSError as ex:
        _agent_state.error = "Failed to launch MCP server: {:s}".format(str(ex))
        return None

    _mcp_server_process = proc
    _agent_state.mcp_server_running = True
    _agent_state.error = ""
    _agent_state.error_full = ""
    print("[🛠️Coworker] start_mcp_server: launched pid={:d}".format(proc.pid))
    print("[🛠️Coworker] start_mcp_server: command = {:s}".format(str(mcp_exe or "python -m blmcp")))
    print("[🛠️Coworker] start_mcp_server: BFACW_HOST={:s} BFACW_PORT={:d}".format(
        blender_host, blender_port))

    # Spawn background threads to drain stdout/stderr pipes.
    # Without this, PIPE buffers fill and the child process deadlocks.
    _drainer_threads, _stdout_lines, _stderr_lines = _start_pipe_drainer(proc)

    # Health check: wait for the MCP HTTP server to bind its port.
    # Check for early exit first (fast path), then poll the port.
    import time
    time.sleep(0.5)  # Brief pause for process to start or fail.
    if proc.poll() is not None:
        # Process exited — collect from drainer.
        time.sleep(0.5)  # Let drainer finish reading.
        stderr_output = "\n".join(_stderr_lines[-100:])
        stdout_output = "\n".join(_stdout_lines[-100:])
        error_detail = (stderr_output or stdout_output or "no output")
        print("[🛠️Coworker] start_mcp_server: process already exited with code {:d}".format(
            proc.returncode))
        if stderr_output:
            print("[🛠️Coworker] start_mcp_server: stderr (tail) = {:s}".format(stderr_output[-1500:]))
        if stdout_output:
            print("[🛠️Coworker] start_mcp_server: stdout (tail) = {:s}".format(stdout_output[-1500:]))

        # Check if it's a ModuleNotFoundError (likely wrong Python version).
        if "ModuleNotFoundError" in error_detail or "ImportError" in error_detail:
            if _retry_depth >= 1:
                print("[🛠️Coworker] start_mcp_server: import error after retry — giving up")
                summary, full = _summarize_mcp_failure(stderr_output, stdout_output)
                _agent_state.error = "MCP server import failed after reinstall: {:s}".format(summary)
                _agent_state.error_full = full
                _agent_state.mcp_server_running = False
                _mcp_server_process = None
                return None
            print("[🛠️Coworker] start_mcp_server: import error detected — attempting dependency reinstall")
            # Clear deps and retry once with Blender's Python.
            deps_dir = _get_vendor_deps_dir()
            if deps_dir.is_dir():
                shutil.rmtree(str(deps_dir), ignore_errors=True)
                if _ensure_vendor_deps():
                    # Try launching again (depth-limited).
                    print("[🛠️Coworker] start_mcp_server: deps reinstalled — retrying launch (attempt {:d})".format(
                        _retry_depth + 1))
                    return start_mcp_server(
                        port=port, blender_host=blender_host, blender_port=blender_port,
                        _retry_depth=_retry_depth + 1,
                    )
        summary, full = _summarize_mcp_failure(stderr_output, stdout_output)
        _agent_state.error = "MCP server exited immediately: {:s}".format(summary)
        _agent_state.error_full = full
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    # Process is alive — actively wait for the port to accept connections.
    # FastMCP + Starlette imports can take 5-10s, so we poll up to 15s.
    print("[🛠️Coworker] start_mcp_server: process alive, waiting for port {:d}...".format(port))
    port_ready = _wait_for_port("127.0.0.1", port, timeout=15.0, interval=1.0)

    if not port_ready:
        # Port never came up — collect drainer output for diagnostics.
        time.sleep(1.0)
        stderr_output = "\n".join(_stderr_lines[-100:])
        stdout_output = "\n".join(_stdout_lines[-100:])
        error_detail = (stderr_output or stdout_output or "no output")
        print("[🛠️Coworker] start_mcp_server: port {:d} never became ready".format(port))
        if stderr_output:
            print("[🛠️Coworker] start_mcp_server: stderr (tail) = {:s}".format(stderr_output[-1500:]))
        if stdout_output:
            print("[🛠️Coworker] start_mcp_server: stdout (tail) = {:s}".format(stdout_output[-1500:]))
        summary, full = _summarize_mcp_failure(stderr_output, stdout_output)
        _agent_state.error = "MCP server started but port {:d} never accepted connections: {:s}".format(
            port, summary)
        _agent_state.error_full = full
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    print("[🛠️Coworker] start_mcp_server: port {:d} is ready".format(port))

    # Log collected output for diagnostics.
    if _stdout_lines:
        print("[🛠️Coworker] start_mcp_server: process alive, stdout so far ({:d} lines):".format(
            len(_stdout_lines)))
        for line in _stdout_lines[-15:]:
            print("[🛠️Coworker] start_mcp_server:   stdout | {:s}".format(line))
    if _stderr_lines:
        print("[🛠️Coworker] start_mcp_server: process alive, stderr so far ({:d} lines):".format(
            len(_stderr_lines)))
        for line in _stderr_lines[-15:]:
            print("[🛠️Coworker] start_mcp_server:   stderr | {:s}".format(line))

    return proc


def stop_mcp_server() -> None:
    """Terminate the MCP server subprocess."""
    global _mcp_server_process, _mcp_shutting_down

    if _mcp_shutting_down:
        return

    _mcp_shutting_down = True
    try:
        proc = _mcp_server_process
        if proc is None:
            return

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        _mcp_server_process = None
        _agent_state.mcp_server_running = False
    finally:
        _mcp_shutting_down = False


# ---------------------------------------------------------------------------
# MCP server — Network mode (External Harness)

def start_mcp_server_network(
    host: str = "127.0.0.1",
    port: int = 9191,
    blender_host: str = "localhost",
    blender_port: int = 9876,
) -> subprocess.Popen | None:
    """Launch the MCP server in network (HTTP) mode for external clients.

    This is similar to ``start_mcp_server()`` but binds to a configurable
    *host*:*port* instead of always using 127.0.0.1.  Useful for:
    - Browser-based MCP clients on the same machine
    - Remote MCP clients on the same network (use with caution)

    Returns the ``Popen`` handle, or ``None`` on failure.
    """
    global _mcp_server_process, _mcp_shutting_down

    if _mcp_shutting_down:
        print("[🛠️Coworker] start_mcp_server_network: shutdown in progress — skipping")
        return None

    # Kill existing process if known.
    if _mcp_server_process is not None:
        try:
            _mcp_server_process.terminate()
            _mcp_server_process.wait(timeout=3)
        except Exception:  # pylint: disable=broad-exception-caught
            try:
                _mcp_server_process.kill()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        _mcp_server_process = None
        import time
        time.sleep(0.5)

    # Kill any stale process on the port.
    _kill_process_on_port(port)
    import time
    time.sleep(0.5)

    # Auto-shuffle: if the port is still in use, find the next available one.
    shuffled_port = _find_available_port(port)
    if shuffled_port == 0:
        _agent_state.error = (
            "MCP network port {:d} is in use and no subsequent port is available. "
            "Increase port_offset in Preferences (Advanced tab).".format(port)
        )
        return None
    if shuffled_port != port:
        print("[🛠️Coworker] start_mcp_server_network: port {:d} in use, shuffled to {:d}".format(port, shuffled_port))
        port = shuffled_port
    _agent_state.mcp_port_actual = port

    env = _build_mcp_env(blender_host=blender_host, blender_port=blender_port)

    # --- Resolution order ---
    mcp_exe, use_module = _resolve_mcp_python()

    if not mcp_exe:
        _agent_state.error = "Cannot find Python to run MCP server"
        return None

    try:
        if use_module:
            proc = subprocess.Popen(
                [mcp_exe, "-m", "blmcp", "--transport", "http",
                 "--host", host, "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        else:
            proc = subprocess.Popen(
                [mcp_exe, "--transport", "http",
                 "--host", host, "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
    except (FileNotFoundError, OSError) as ex:
        _agent_state.error = "Failed to launch MCP server: {:s}".format(str(ex))
        return None

    _mcp_server_process = proc
    _agent_state.mcp_server_running = True
    _agent_state.error = ""
    _agent_state.error_full = ""

    # Drain pipes.  Keep the collected lines so an early exit can be
    # diagnosed — this path previously discarded them and reported a bare
    # "MCP server exited immediately" with no cause at all.
    _drainer_threads, _stdout_lines, _stderr_lines = _start_pipe_drainer(proc)

    # Wait for port.
    import time
    time.sleep(0.5)
    if proc.poll() is not None:
        time.sleep(0.5)  # Let the drainer finish reading.
        stderr_output = "\n".join(_stderr_lines[-100:])
        stdout_output = "\n".join(_stdout_lines[-100:])
        print("[🛠️Coworker] start_mcp_server_network: process exited with code {:d}".format(
            proc.returncode))
        if stderr_output:
            print("[🛠️Coworker] start_mcp_server_network: stderr (tail) = {:s}".format(
                stderr_output[-1500:]))
        if stdout_output:
            print("[🛠️Coworker] start_mcp_server_network: stdout (tail) = {:s}".format(
                stdout_output[-1500:]))
        summary, full = _summarize_mcp_failure(stderr_output, stdout_output)
        _agent_state.error = "MCP server exited immediately: {:s}".format(summary)
        _agent_state.error_full = full
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    port_ready = _wait_for_port(host, port, timeout=15.0, interval=1.0, proc=proc)
    if not port_ready:
        time.sleep(1.0)
        stderr_output = "\n".join(_stderr_lines[-100:])
        stdout_output = "\n".join(_stdout_lines[-100:])
        print("[🛠️Coworker] start_mcp_server_network: port {:d} never became ready".format(port))
        if stderr_output:
            print("[🛠️Coworker] start_mcp_server_network: stderr (tail) = {:s}".format(
                stderr_output[-1500:]))
        summary, full = _summarize_mcp_failure(stderr_output, stdout_output)
        _agent_state.error = "MCP server port {:d} never accepted connections: {:s}".format(
            port, summary)
        _agent_state.error_full = full
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    return proc


# ---------------------------------------------------------------------------
# MCP client config generation (External Harness)


def _vendor_native_compat(python_path: str) -> bool:
    """Check whether vendor deps' native extensions match *python_path*.

    Scans ``~/.cache/bfa_coworker/vendor_deps/`` for ``.pyd`` / ``.so``
    files, extracts the cpython tag (e.g. ``cp312``), and compares it
    against the target interpreter's major.minor version.

    Returns ``True`` when compatible (or when there are no native
    extensions — pure-Python deps work everywhere).
    """
    deps_dir = _get_vendor_deps_dir()
    if not deps_dir.is_dir():
        return True  # No deps yet; let the caller proceed.

    native_versions: set[str] = set()
    for pat in ("*.pyd", "*.so"):
        # rglob, not glob: native extensions also live in subdirectories
        # (e.g. pywin32_system32/, win32/lib/), and a top-level-only scan
        # would miss a version mismatch and let the launch fail later.
        for f in deps_dir.rglob(pat):
            name = f.name
            # Extract cpython tag: something like _cffi_backend.cp313-win_amd64.pyd
            for part in name.split("."):
                if part.startswith("cp") and part[2:].isdigit():
                    native_versions.add(part[:5])  # "cp313"
                    break

    if not native_versions:
        return True  # Pure-Python; no compatibility issue.

    # Determine the target Python version from the executable.
    try:
        import subprocess
        result = subprocess.run(
            [python_path, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return True  # Can't determine; assume compatible.
        target_ver = result.stdout.strip()  # e.g. "3.13"
        target_tag = "cp{:s}{:s}".format(*target_ver.split(".")[:2])  # "cp313"
    except Exception:
        return True  # Can't determine; assume compatible.

    for nv in native_versions:
        if nv != target_tag:
            print(
                "[🛠️Coworker] _vendor_native_compat: MISMATCH — "
                "vendor native exts are {!s} but target Python is {!s}".format(
                    nv, target_tag
                )
            )
            return False
    return True


def _get_blender_python_for_config() -> tuple[str, str]:
    """Return (python_path, pythonpath) for use in harness configs.

    Uses Blender's bundled Python with vendor deps on PYTHONPATH so
    ``python -m blmcp`` works out of the box without any pip install.

    When vendor deps contain native extensions compiled for a different
    Python version than Blender's bundled Python, falls back to a
    compatible system Python to avoid import failures.

    Falls back to ``("python", "")`` if no suitable Python is found.
    """
    import shutil as _shutil
    blender_py = _find_blender_python()
    pythonpath = _find_vendor_pythonpath()
    if blender_py:
        if _vendor_native_compat(blender_py):
            return (blender_py, pythonpath)
        # Blender's Python is incompatible with vendor native extensions.
        # Try to find a system Python that matches the vendor deps.
        print(
            "[🛠️Coworker] _get_blender_python_for_config: "
            "Blender Python {!s} incompatible with vendor native extensions "
            "— searching for compatible system Python...".format(
                blender_py
            )
        )
        for candidate in ("python3", "python"):
            py = _shutil.which(candidate)
            if py and _vendor_native_compat(py):
                print(
                    "[🛠️Coworker] _get_blender_python_for_config: "
                    "using {!s} (compatible with vendor deps)".format(py)
                )
                return (py, pythonpath)
        # No compatible Python found; fall back to Blender's anyway with a warning.
        print(
            "[⚠️Coworker] _get_blender_python_for_config: "
            "no compatible Python found. Using Blender Python {!s} "
            "— vendor deps may fail to import.".format(blender_py)
        )
        return (blender_py, pythonpath)
    return ("python", "")


def generate_mcp_client_config(
    client_type: str = "claude",
    blender_host: str = "localhost",
    blender_port: int = 9876,
    use_blender_python: bool = True,
) -> str:
    """Generate MCP client configuration JSON for external tools.

    *client_type*: one of the harness preset identifiers (e.g. ``"claude_desktop"``,
    ``"codex"``, ``"cursor"``, ``"generic"``).

    When *use_blender_python* is True (default), the config emits the full
    path to Blender's bundled Python with ``PYTHONPATH`` set to the vendor
    directories — no pip install needed.

    Returns a JSON string suitable for the client's config file.
    """
    # Resolve the python command and PYTHONPATH.
    if use_blender_python:
        py_cmd, py_path = _get_blender_python_for_config()
    else:
        py_cmd, py_path = "python", ""

    # Build the env block.
    env: dict[str, str] = {
        "BFACW_HOST": blender_host,
        "BFACW_PORT": str(blender_port),
    }
    if py_path:
        env["PYTHONPATH"] = py_path
    # Point CLI tools (execute_blender_code_for_cli, etc.) at the running
    # Blender/Bforartists binary.  Without this they fall back to a literal
    # "blender" on PATH, which fails when Blender is installed under a
    # different name (e.g. bforartists.exe).
    try:
        import bpy  # pylint: disable=import-error
        env["BLENDER_PATH"] = bpy.app.binary_path
    except Exception:
        pass  # Outside Blender - the client must set BLENDER_PATH itself.

    # Base command block shared by all presets.
    base_cmd = {
        "command": py_cmd,
        "args": ["-m", "blmcp", "--transport", "stdio"],
        "env": env,
    }

    if client_type in ("claude_desktop", "claude_code", "freebuff"):
        config = {
            "mcpServers": {
                "bfa-coworker": dict(base_cmd),
            }
        }
    elif client_type in ("cursor", "windsurf", "cline"):
        config = {
            "servers": {
                "bfa-coworker": {
                    "type": "stdio",
                    **dict(base_cmd),
                }
            }
        }
    elif client_type == "codex":
        config = {
            "mcpServers": {
                "bfa-coworker": dict(base_cmd),
            }
        }
    elif client_type == "opencode":
        config = {
            "mcpServers": {
                "bfa-coworker": dict(base_cmd),
            }
        }
    else:
        # Generic / fallback — raw command block.
        config = dict(base_cmd)

    return json.dumps(config, indent=2)


def validate_mcp_client_config(
    client_type: str = "claude",
    blender_host: str = "localhost",
    blender_port: int = 9876,
    use_blender_python: bool = True,
    check_bridge: bool = True,
) -> dict[str, Any]:
    """Preflight the harness config that :func:`generate_mcp_client_config` emits.

    The addon hands the user a config it has never executed, so a broken
    interpreter path or a missing ``PYTHONPATH`` entry only surfaces later as
    an opaque traceback inside the MCP client.  This runs the same resolution
    the config generator uses and then actually launches the server with
    ``--help`` to prove the import chain works.

    Returns a dict::

        {
            "ok":          bool,          # True when every check passed
            "python":      str,           # resolved interpreter (or command)
            "python_ok":   bool,          # interpreter exists / is on PATH
            "pythonpath":  str,           # resolved PYTHONPATH
            "missing":     list[str],     # PYTHONPATH entries that don't exist
            "import_ok":   bool,          # `-m blmcp --help` exited 0
            "bridge_ok":   bool | None,   # None when check_bridge is False
            "mcp_version": str,           # resolved MCP SDK version ("" if unknown)
            "stderr_tail": str,           # last lines of the probe's stderr
            "hint":        str,           # actionable hint ("" when fine)
            "summary":     str,           # one-line human-readable result
        }

    ``--help`` is used rather than a full stdio handshake: it exercises the
    whole import chain (``blmcp`` → ``mcp.server.fastmcp`` → ``yaml`` →
    ``data/prompts.yml``) while staying fast and side-effect free.
    """
    result: dict[str, Any] = {
        "ok": False,
        "python": "",
        "python_ok": False,
        "pythonpath": "",
        "missing": [],
        "import_ok": False,
        "bridge_ok": None,
        "mcp_version": "",
        "stderr_tail": "",
        "hint": "",
        "summary": "",
    }

    # ── 1. Resolve the interpreter and PYTHONPATH exactly as the config does.
    if use_blender_python:
        py_cmd, py_path = _get_blender_python_for_config()
    else:
        py_cmd, py_path = "python", ""
    result["python"] = py_cmd
    result["pythonpath"] = py_path

    # ── 2. Does the interpreter exist?
    if os.path.isabs(py_cmd) or os.sep in py_cmd or "/" in py_cmd:
        result["python_ok"] = os.path.isfile(py_cmd)
    else:
        result["python_ok"] = shutil.which(py_cmd) is not None
    if not result["python_ok"]:
        result["hint"] = (
            "Python interpreter not found at '{:s}'. Enable \"Use Blender's "
            "Python\" to emit the bundled interpreter path, or install the "
            "MCP server: pip install bfa-coworker-mcp".format(py_cmd)
        )
        result["summary"] = "Python interpreter not found: {:s}".format(py_cmd)
        return result

    # ── 3. Do all PYTHONPATH entries exist?
    if py_path:
        for entry in py_path.split(os.pathsep):
            if entry and not os.path.isdir(entry):
                result["missing"].append(entry)
    if result["missing"]:
        result["hint"] = (
            "PYTHONPATH entries do not exist: {:s}. Run 'python build_addon.py' "
            "to rebuild the vendor directories.".format(", ".join(result["missing"]))
        )
        result["summary"] = "PYTHONPATH entries missing: {:d}".format(len(result["missing"]))
        return result

    # ── 4. Actually launch the server with --help to prove the import chain.
    env = os.environ.copy()
    env["BFACW_HOST"] = blender_host
    env["BFACW_PORT"] = str(blender_port)
    if py_path:
        env["PYTHONPATH"] = py_path
    if sys.platform == "win32":
        pywin32_system32 = _get_vendor_deps_dir() / "pywin32_system32"
        if pywin32_system32.is_dir():
            env["PATH"] = str(pywin32_system32) + os.pathsep + env.get("PATH", "")

    try:
        probe = subprocess.run(
            [py_cmd, "-m", "blmcp", "--help"],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except subprocess.TimeoutExpired:
        result["hint"] = (
            "The MCP server did not respond within 30s. A firewall or "
            "antivirus may be blocking the interpreter."
        )
        result["summary"] = "Probe timed out"
        return result
    except (FileNotFoundError, OSError) as ex:
        result["hint"] = "Could not run the interpreter: {:s}".format(str(ex))
        result["summary"] = "Probe failed to start"
        return result

    result["import_ok"] = (probe.returncode == 0)
    stderr_text = probe.stderr or ""
    result["stderr_tail"] = "\n".join(stderr_text.strip().splitlines()[-15:])

    if not result["import_ok"]:
        summary, _full = _summarize_mcp_failure(stderr_text, probe.stdout or "")
        result["summary"] = summary
        result["hint"] = _classify_mcp_failure(stderr_text) or (
            "The MCP server failed to start. See the error above."
        )
        return result

    # ── 4b. Report the resolved MCP SDK version.  mcp 2.x removed FastMCP,
    # which blmcp imports, so a 2.x SDK is a latent failure even when the
    # probe happens to pass (e.g. a shim module).
    try:
        ver_probe = subprocess.run(
            [py_cmd, "-c",
             "import importlib.metadata as m; print(m.version('mcp'))"],
            capture_output=True, text=True, timeout=20, env=env,
        )
        if ver_probe.returncode == 0:
            result["mcp_version"] = ver_probe.stdout.strip()
            if result["mcp_version"].startswith("2."):
                result["ok"] = False
                result["summary"] = (
                    "MCP SDK {:s} is too new — FastMCP was removed in 2.0".format(
                        result["mcp_version"])
                )
                result["hint"] = (
                    "The add-on requires mcp<2. Run 'python build_addon.py' to "
                    "reinstall the pinned vendor deps, or: "
                    "pip install 'mcp[cli]>=1.2.0,<2.0.0'"
                )
                return result
    except (subprocess.TimeoutExpired, OSError):
        pass  # Version is informational; never fail the check on it.

    # ── 5. Optional: is the bridge reachable?  A config can be valid while
    # the bridge is stopped, so this is reported separately.
    if check_bridge:
        try:
            with socket.create_connection((blender_host, blender_port), timeout=3.0):
                result["bridge_ok"] = True
        except (OSError, socket.error):
            result["bridge_ok"] = False

    result["ok"] = True
    if result["bridge_ok"] is False:
        result["summary"] = (
            "Config OK — but the bridge is not reachable on {:s}:{:d}. "
            "Click Start Bridge in the chat panel.".format(blender_host, blender_port)
        )
    else:
        result["summary"] = "Config OK — MCP server starts and imports cleanly"
    return result


# ---------------------------------------------------------------------------
# Operation History Log (Tier 1)

def _log_operation(tool_name: str, params: dict, result: str) -> None:
    """Append a tool execution to the operation history JSONL file."""
    import time as _time
    log_path = Path.home() / ".cache" / "bfa_coworker" / "operations.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": _time.time(),
        "tool": tool_name,
        "params": params,
        "result": result[:500],  # Truncate for log size.
    }
    try:
        with open(str(log_path), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # Best-effort logging.


# ---------------------------------------------------------------------------
# Liveness Check (Tier 1)

def _check_liveness() -> None:
    """Update liveness booleans based on activity timestamps."""
    import time as _time
    now = _time.monotonic()
    _agent_state.bridge_live = (now - _agent_state.last_bridge_activity) < 20.0
    _agent_state.mcp_live = (now - _agent_state.last_mcp_activity) < 20.0
    _agent_state.llm_live = (now - _agent_state.last_llm_activity) < 20.0


# ---------------------------------------------------------------------------
# MCP tool listing

async def list_mcp_tools(port: int = _MCP_SERVER_DEFAULT_PORT) -> list[dict[str, Any]]:
    """
    Return the list of tools from the MCP server via HTTP.

    Tries both the streamable-http tool listing endpoint and the
    standard MCP list-tools mechanism.
    """
    url = "http://127.0.0.1:{:d}/".format(port)
    print("[🛠️Coworker] list_mcp_tools: trying {:s}".format(url))

    # Use urllib (stdlib, avoids Blender sandbox policy violation from vendored httpx).
    try:
        payload = {"jsonrpc": "2.0", "id": "1", "method": "tools/list"}
        data_bytes = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        print("[🛠️Coworker] list_mcp_tools: urllib POST {:s}".format(url))
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            print("[🛠️Coworker] list_mcp_tools: urllib status={:d}, {:d} bytes".format(resp.status, len(raw)))
            print("[🛠️Coworker] list_mcp_tools: urllib first 300 chars: {:s}".format(raw[:300]))
            # FastMCP in stateless_http mode returns SSE
            # (``event: message`` / ``data: {...}``) even for
            # single-response JSON-RPC calls.
            data = _parse_sse_json(raw)
            if data is None:
                print("[🛠️Coworker] list_mcp_tools: urllib SSE parse returned None")
                return []
            tools = data.get("result", {}).get("tools", [])
            print("[🛠️Coworker] list_mcp_tools: urllib returned {:d} tools".format(len(tools)))
            return tools
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[🛠️Coworker] list_mcp_tools: urllib failed — {:s}".format(str(ex)))

    return []


def _list_tools_sync(port: int = _MCP_SERVER_DEFAULT_PORT, operating_mode: str = "") -> list[dict[str, Any]]:
    """Synchronous wrapper for listing MCP tools, with retry on 0 tools.

    When *operating_mode* is ``"EXTERNAL_HARNESS"``, returns ``[]``
    immediately — the MCP server is managed externally.
    """
    if operating_mode == "EXTERNAL_HARNESS":
        print("[🛠️Coworker] _list_tools_sync: harness mode — skipping")
        return []

    import time
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        print("[🛠️Coworker] _list_tools_sync: port={:d} attempt={:d}/{:d}".format(
            port, attempt, max_retries))
        future = schedule_coro(list_mcp_tools(port))
        try:
            result = future.result(timeout=15)
            count = len(result) if result else 0
            print("[🛠️Coworker] _list_tools_sync: got {:d} tools".format(count))
            if count > 0:
                _agent_state.tool_count = count
                return result
            # 0 tools — retry if server is still running.
            if not _agent_state.mcp_server_running:
                print("[🛠️Coworker] _list_tools_sync: server not running, aborting")
                return result or []
            if attempt < max_retries:
                delay = min(1.0 * attempt, 4.0)  # Backoff: 1s, 2s, 3s, 4s.
                print("[🛠️Coworker] _list_tools_sync: 0 tools, retrying in {:.0f}s...".format(delay))
                time.sleep(delay)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[🛠️Coworker] _list_tools_sync: attempt {:d} FAILED — {:s}".format(attempt, str(ex)))
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            return []
    return []


# ---------------------------------------------------------------------------
# LLM conversation loop (synchronous, called from timer)

def _mcp_tools_to_openai(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert MCP tool metadata to OpenAI ``tools`` format."""
    result = []
    for t in mcp_tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {}),
            },
        })
    return result


def _parse_text_tool_calls(content: str) -> list[dict[str, Any]]:
    """Parse text-based tool calls from an LLM response.

    Looks for JSON blocks matching the format::

        {"tool": "tool_name", "arguments": {"arg1": "value1"}}

    Returns a list of OpenAI-format tool call dicts, or an empty list
    if no tool calls are found.
    """
    import re
    tool_calls: list[dict[str, Any]] = []
    # Match JSON blocks: {"tool": "...", "arguments": {...}}
    pattern = r'\{"tool":\s*"([^"]+)"\s*,\s*"arguments":\s*(\{.*?\})\s*\}'
    for match in re.finditer(pattern, content, re.DOTALL):
        name = match.group(1)
        args_str = match.group(2)
        try:
            args = json.loads(args_str)
        except (json.JSONDecodeError, TypeError):
            args = {}
        tool_id = "text_tool_{:d}".format(len(tool_calls))
        tool_calls.append({
            "id": tool_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args),
            },
        })
    return tool_calls


def _parse_xml_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse XML-style tool calls from an LLM response.

    Handles two formats common with light reasoning models:

    **Format A** (Qwen3.5-9B DeepSeek-V4-Flash, Gemma 4 E4B)::

        <tool_call>
        <function=download_polyhaven_asset>
        <parameter=asset_id>
        sunset_in_the_chalk_quarry
        </parameter>
        <parameter=asset_type>
        hdris
        </parameter>
        </function>
        </tool_call>

    **Format B** (JSON inside XML tags)::

        <tool_call>
        {"name": "tool_name", "arguments": {"arg1": "value1"}}
        </tool_call>

    Returns a list of OpenAI-format tool call dicts, or an empty list
    if no tool calls are found.
    """
    import re
    tool_calls: list[dict[str, Any]] = []

    # ── Format A: <function=name><parameter=key>value</parameter></function> ──
    # Find all <function=...> blocks, optionally wrapped in <tool_call>.
    func_pattern = r'(?:<tool_call>\s*)?<function=([^>]+)>(.*?)</function>(?:\s*</tool_call>)?'
    for match in re.finditer(func_pattern, text, re.DOTALL):
        name = match.group(1).strip()
        params_block = match.group(2)
        args: dict[str, Any] = {}
        # Extract <parameter=key>value</parameter> pairs.
        param_pattern = r'<parameter=([^>]+)>\s*(.*?)\s*</parameter>'
        for p_match in re.finditer(param_pattern, params_block, re.DOTALL):
            key = p_match.group(1).strip()
            value = p_match.group(2).strip()
            # Try to parse as JSON (number, bool, null), else keep as string.
            try:
                args[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                args[key] = value
        if name:
            tool_id = "xml_tool_{:d}".format(len(tool_calls))
            tool_calls.append({
                "id": tool_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args),
                },
            })

    # ── Format B: <tool_call>{JSON}</tool_call> ──
    if not tool_calls:
        json_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(json_pattern, text, re.DOTALL):
            try:
                parsed = json.loads(match.group(1))
                name = parsed.get("name", "")
                args = parsed.get("arguments", {})
                if name:
                    tool_id = "xml_tool_{:d}".format(len(tool_calls))
                    tool_calls.append({
                        "id": tool_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args),
                        },
                    })
            except (json.JSONDecodeError, TypeError):
                continue

    return tool_calls


# Watchdog: give up on a hung MCP tool call so the conversation loop
# can continue instead of blocking forever on an infinite loop in
# LLM-generated code or a deadlocked bridge.
_TOOL_CALL_WATCHDOG_SECONDS = 120
_TOOL_CALL_WATCHDOG_LAST: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Shared state binding for the LLM transport layer
# ---------------------------------------------------------------------------
# Deferred to here (not next to ``_agent_state``) because these references
# need ``_stop_event`` and the tool-call parsers defined above; binding at
# module top raised ``NameError: name '_stop_event' is not defined``.
_transport.bind(_agent_state, _stop_event)
_transport.bind_helpers(
    types.SimpleNamespace(
        strip_think_tags=_strip_think_tags,
        parse_text_tool_calls=_parse_text_tool_calls,
        parse_xml_tool_calls=_parse_xml_tool_calls,
        sanitize_message_roles=_sanitize_message_roles,
        describe_message_roles=_describe_message_roles,
        describe_history_for_log=_describe_history_for_log,
        flatten_for_plain_chat=_flatten_for_plain_chat,
    )
)


def _tool_call_watchdog_hit(tool_name: str) -> None:
    """Report (once) that a tool call has exceeded the watchdog budget."""
    import time as _time
    now = _time.monotonic()
    last = _TOOL_CALL_WATCHDOG_LAST.get(tool_name, 0.0)
    if now - last < 30:
        return  # debounce: do not spam every redraw cycle
    _TOOL_CALL_WATCHDOG_LAST[tool_name] = now
    print("[🛠️Coworker] _call_mcp_tool_sync: TOOL CALL TIMEOUT ({:d}s) for {:s} — "
          "the bridge may be hung; the HTTP request will surface the error.".format(
        _TOOL_CALL_WATCHDOG_SECONDS, tool_name))


def _call_mcp_tool_sync(
    tool_name: str,
    arguments: dict[str, Any],
    port: int = _MCP_SERVER_DEFAULT_PORT,
) -> str:
    """Call an MCP tool synchronously via the HTTP endpoint."""
    import time as _time
    url = "http://127.0.0.1:{:d}/".format(port)
    payload = {
        "jsonrpc": "2.0",
        "id": "tool_{:s}".format(tool_name),
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    data_bytes = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    print("[🛠️Coworker] _call_mcp_tool_sync: {:s} args={:s}".format(
        tool_name, json.dumps(arguments)[:200]))
    try:
        # Start a daemon watchdog that reports if this call exceeds the
        # budget. The HTTP timeout (60s) still owns the actual abort; the
        # watchdog is a belt-and-braces liveness signal for the logs.
        _wd = threading.Timer(_TOOL_CALL_WATCHDOG_SECONDS,
                              _tool_call_watchdog_hit,
                              args=(tool_name,))
        _wd.daemon = True
        _wd.start()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode()
                # FastMCP in stateless_http mode wraps the JSON-RPC
                # response in SSE (``event: message`` / ``data: {...}``).
                result = _parse_sse_text_response(raw)
                print("[🛠️Coworker] _call_mcp_tool_sync: result = {:s}".format(
                    result[:300]))
                # Update liveness and log operation.
                _agent_state.last_mcp_activity = _time.monotonic()
                _log_operation(tool_name, arguments, result)
                return result
        finally:
            _wd.cancel()
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
        print("[🛠️Coworker] _call_mcp_tool_sync: FAILED — {:s}".format(str(ex)))
        return "Error calling tool '{:s}': {:s}".format(tool_name, str(ex))


# ── Friendly tool names for UI status ─────────────────────────────

_TOOL_FRIENDLY_NAMES: dict[str, str] = {
    "execute_blender_code": "Running code in Blender",
    "execute_blender_plan": "Running tested code templates",
    "list_blender_templates": "Showing available templates",
    "get_blendfile_summary_datablocks_toolcode": "Reading scene data",
    "download_polyhaven_asset": "Downloading asset",
    "search_polyhaven_assets": "Searching Poly Haven",
    "place_asset_in_scene": "Placing asset in scene",
    "jump_to_asset_browser": "Opening Asset Browser",
    "setup_pbr_material": "Setting up PBR material",
    "get_object_info": "Inspecting object",
    "create_object": "Creating object",
    "modify_object": "Modifying object",
    "delete_object": "Removing object",
    "set_material": "Applying material",
    "render_scene": "Rendering",
}


def _friendly_tool_status(tool_name: str) -> str:
    """Return a user-friendly status string for a tool name."""
    friendly = _TOOL_FRIENDLY_NAMES.get(tool_name)
    if friendly:
        return "{:s}...".format(friendly)
    # Fallback: convert camelCase/snake_case to readable text.
    import re
    readable = re.sub(r"_+", " ", tool_name)
    readable = re.sub(r"([a-z])([A-Z])", r"\1 \2", readable)
    return "{:s}...".format(readable.capitalize())


# ── Tool error formatting ─────────────────────────────────────────

def _format_tool_error(result_text: str) -> str:
    """Extract a human-readable summary from a tool error result.

    Parses ``{"status": "error", "message": "Traceback..."}`` and returns
    a friendly message like ``"I had trouble with that step — AttributeError"``.

    Returns *result_text* unchanged if it doesn't match the error pattern.
    """
    if '"status": "error"' not in result_text:
        return result_text

    # Try to extract just the exception type from the traceback.
    import re
    m = re.search(r'"message":\s*"([^"]*(?:\\.[^"]*)*)"', result_text, re.DOTALL)
    if m:
        raw_msg = m.group(1)
        raw_msg = raw_msg.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
        lines = raw_msg.strip().splitlines()
        # Walk backwards to find the actual exception line (skip Traceback, File, and blank lines).
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("Traceback") or stripped.startswith("["):
                continue
            if stripped.startswith("File"):
                continue
            # This is the actual exception.
            exc_type = stripped.split(":")[0].strip() if ":" in stripped else stripped
            return "Work had an error \u2014 {:s}, trying again".format(exc_type)
    return result_text


def _tool_result_summary(result_text: str, max_len: int = 150) -> str:
    """Return a short summary of a tool result for UI display.

    For errors, uses ``_format_tool_error``. For successes, extracts a brief
    status or truncates the result.
    """
    if '"status": "error"' in result_text:
        return _format_tool_error(result_text)
    # Try to extract a success message.
    import re
    m = re.search(r'"status":\s*"ok"', result_text)
    if m:
        msg_m = re.search(r'"message":\s*"([^"]*)"', result_text)
        if msg_m:
            return msg_m.group(1)[:max_len]
        return "Done"
    if len(result_text) <= max_len:
        return result_text
    return result_text[:max_len] + "..."


# The context-incorrect poll() failure: a full Python traceback for a one-line
# mistake (the operator needs an active object / wrong mode).  Collapsing it
# keeps the history small and the corrective hint actionable.
_POLL_FAILED_MARKER = "poll() failed, context is incorrect"

# Operator-specific fix text for a poll() failure (scene safety Phase 4).
# Keyed on the `bpy.ops.<area>.<op>` name extracted from the traceback; a
# generic temp_override hint is used when the operator is not listed.
_POLL_OP_FIXES: dict[str, str] = {
    "join": "join() needs >= 2 selected objects AND one active object. "
            "Select explicitly (o.select_set(True)) and set "
            "bpy.context.view_layer.objects.active before calling it.",
    "join_shapes": "join_shapes() needs the shapes selected and one active "
                   "object; select them and set view_layer.objects.active first.",
    "modifier_apply": "set bpy.context.view_layer.objects.active to the object "
                      "that owns the modifier, and confirm the modifier name exists.",
    "modifier_remove": "set the active object to the object that owns the "
                       "modifier before removing it.",
    "mode_set": "an active object is required (and for POSE, an armature); "
                "select it and set view_layer.objects.active first.",
    "origin_set": "select the objects and set the active object before origin_set().",
    "convert": "set the active object / selection before convert().",
    "shade_smooth": "set the active object before shade_smooth().",
    "shade_flat": "set the active object before shade_flat().",
    "make_single_user": "set the active object before make_single_user().",
}


def _collapse_poll_failed_error(result_text: str) -> str:
    """Collapse a ``poll() failed, context is incorrect`` traceback to a hint.

    bpy.operator poll() failures produce a multi-line traceback whose only
    real content is the operator name and the fact that the context was
    wrong.  Store (and send) a one-line corrective hint instead.  Any other
    error text is returned unchanged for the normal smart-trim path.
    """
    if _POLL_FAILED_MARKER not in result_text:
        return result_text
    op_name = ""
    m = re.search(r"Operator bpy\.ops\.([\w.]+)\.poll\(\) failed", result_text)
    if m:
        op_name = m.group(1)
    _op_fix = _POLL_OP_FIXES.get(op_name.split(".")[-1])
    if _op_fix:
        hint = (
            "{:s} failed its poll() check \u2014 the operator's context was "
            "incorrect. Fix: {:s}"
        ).format(op_name or "The operator", _op_fix)
    else:
        hint = (
            "{:s} failed its poll() check \u2014 the operator's context was incorrect. "
            "Fix: use bpy.context.temp_override() to supply the required context "
            "(e.g. active_object / selected_objects / area), or ensure the right "
            "mode and an active object are set before calling it."
        ).format(op_name or "The operator")
    return json.dumps({"status": "error", "message": hint})


def _collapse_index_error(result_text: str) -> str:
    """Collapse an ``IndexError: list index out of range`` into a hint.

    Usually a literal index into an empty Blender collection (or one that
    was emptied by auto-undo or a user edit between tool calls).  Returns
    *result_text* unchanged for any other error.
    """
    if "IndexError" not in result_text or "list index out of range" not in result_text:
        return result_text
    hint = (
        "IndexError: the collection was empty (or the index was out of range). "
        "A Blender collection such as selected_objects / bpy.data.<coll> can be "
        "empty, and the selection may have changed since your last call (auto-undo "
        "or a user edit can remove objects). Guard with len() or use "
        "next(iter(...), None), and re-fetch references by name with "
        "bpy.data.objects.get('Name') before acting."
    )
    return json.dumps({"status": "error", "message": hint})


def _collapse_known_errors(result_text: str) -> str:
    """Collapse known-noisy errors before storage (scene safety Phase 4).

    Dispatches to the poll() / IndexError collapsers; anything else is
    returned unchanged for the normal smart-trim path.
    """
    result_text = _collapse_poll_failed_error(result_text)
    result_text = _collapse_index_error(result_text)
    return result_text


def _trim_tool_result(result_text: str, max_chars: int = _MAX_TOOL_RESULT_CHARS) -> str:
    """Smart-trim a tool result for LLM context, stripping JSON boilerplate.

    Unlike the old hard 500-char cut, this function:
    - Strips the outer ``{"status": ..., "result": ...}`` wrapper and
      keeps only the meaningful inner data.
    - For error results, keeps the head and the tail (the exception
      line at the end of a traceback), trimming the middle.
    - For success results, extracts the ``result`` sub-field if present,
      giving the LLM more structured data within the same token budget.
    - Falls back to a hard truncation for non-JSON or unparseable content.
    """
    if len(result_text) <= max_chars:
        return result_text

    # Try to parse as JSON.
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        # Not JSON — fall back to hard truncation.
        return result_text[:max_chars] + "\n...[+{:d} more chars]".format(
            len(result_text) - max_chars)

    if not isinstance(data, dict):
        return result_text[:max_chars] + "\n...[+{:d} more chars]".format(
            len(result_text) - max_chars)

    status = data.get("status", "")

    # Error results: preserve the TAIL of the message — Python tracebacks
    # put the actual exception (type + message + the failing line of the
    # model's own code) on the LAST lines. Head-truncating cut that off,
    # leaving the model blind to the real error while it could still see
    # the unhelpful stack preamble. Keep a short head for context and the
    # informative tail within the same token budget.
    if status == "error":
        msg = data.get("message", "") or ""
        if len(msg) <= max_chars:
            return "{{\"status\": \"error\", \"message\": \"{:s}\"}}".format(msg)
        head_len = max(max_chars // 4, 80)
        tail_len = max_chars - head_len - 22  # room for the trim marker
        trimmed = msg[:head_len] + "\\n...[+{:d} chars trimmed]...\\n".format(
            len(msg) - head_len - tail_len) + msg[-tail_len:]
        return "{{\"status\": \"error\", \"message\": \"{:s}\"}}".format(trimmed)

    # Success results: extract the inner result field.
    if status == "ok":
        inner = data.get("result", {}) or data.get("message", "")
        inner_str = json.dumps(inner, default=str) if not isinstance(inner, str) else inner
        if len(inner_str) <= max_chars:
            return inner_str
        return inner_str[:max_chars] + "\n...[+{:d} more chars]".format(
            len(inner_str) - max_chars)

    # Unknown format — just return the raw status + truncated content.
    return "(status={:s}) {:s}".format(
        status, result_text[:max_chars - 40] + "...")


def _error_is_code_bug(error_text: str) -> bool:
    """Return ``True`` if *error_text* is a pure code bug with no side effects.

    Code-bug errors (KeyError, AttributeError, NameError) fail before
    creating any objects or modifying the scene.  There's nothing to undo
    — skipping the undo saves 2 round-trips and avoids depsgraph crashes
    from undo+push on empty scenes.

    NOTE: ``ValueError`` and ``TypeError`` are deliberately excluded from
    this list because they can fire *after* objects have been created
    (e.g. a cube added then bad geometry math, or objects created then
    a wrong enum value set).  Undoing such errors is essential to prevent
    duplicates.
    """
    _CODE_BUG_PATTERNS = (
        "KeyError:",
        "AttributeError:",
        "NameError:",
        "Node type",
        "undefined",
    )
    return any(p in error_text for p in _CODE_BUG_PATTERNS)


# ---------------------------------------------------------------------------
# Spiral detection helpers — break repeated error loops

def _extract_error_signature(result_text: str) -> str:
    """Extract a normalized error signature from a tool result.

    Returns a canonical string like ``"RuntimeError: Context missing active object"``
    that can be compared across different code attempts (ignoring line numbers).
    Returns empty string if the result is not an error.
    """
    if '"status": "error"' not in result_text:
        return ""
    import re
    m = re.search(r'"message":\s*"', result_text)
    if not m:
        return ""
    # The closing quote is always the LAST '"' in the result text — true for
    # escaped JSON and for the unescaped re-serialization produced by
    # _trim_tool_result alike, so messages with embedded quotes (e.g.
    # `File "<string>"` tracebacks) are captured in full.
    end = result_text.rfind('"', m.end())
    if end == -1:
        return ""
    raw = result_text[m.end():end]
    # Unescape JSON escapes; a no-op when the text is already raw.
    raw = raw.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    # Drop any appended "HINT: ..." guidance block — the signature must be the
    # actual error line, not the tail of the hint text.
    hint_idx = raw.find("\n\nHINT:")
    if hint_idx != -1:
        raw = raw[:hint_idx]
    lines = raw.strip().splitlines()
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Traceback") or stripped.startswith("["):
            continue
        if stripped.startswith("File"):
            continue
        sig = stripped.replace("[Traceback truncated to last 3 frames]", "").strip()
        return sig
    return ""


def _spiral_corrective_message(error_sig: str) -> str:
    """Return a corrective user message based on the repeated error signature."""
    sig_lower = error_sig.lower()
    if "poll() failed" in sig_lower:
        return (
            "[System: You keep getting 'poll() failed, context is incorrect'. "
            "The operator's context is wrong \u2014 usually no active object or the "
            "wrong selection. Set the selection AND the active object explicitly "
            "in the SAME script right before the operator call "
            "(obj.select_set(True); bpy.context.view_layer.objects.active = obj), "
            "or use bpy.context.temp_override(...). Fix the code \u2014 do not retry "
            "it verbatim.]"
        )
    if "index out of range" in sig_lower or "indexerror" in sig_lower:
        return (
            "[System: You keep getting 'IndexError: list index out of range'. "
            "The collection is empty (or changed since your last call). Do not "
            "index with [0]; guard with len(), use next(iter(...), None), and "
            "re-fetch references by name with bpy.data.objects.get('Name'). "
            "Fix the code \u2014 do not retry it verbatim.]"
        )
    if "context missing active object" in sig_lower or "context missing object" in sig_lower:
        return (
            "[System: You keep getting 'Context missing active object'. "
            "The scene is empty \u2014 there are no objects to operate on. "
            "Create an object first (e.g. bpy.ops.mesh.primitive_cube_add()) "
            "before calling mode-dependent operators.]"
        )
    if "context missing" in sig_lower:
        return (
            "[System: You keep getting a 'Context missing' error. "
            "Check that the required context (active object, selected objects, etc.) "
            "exists before calling this operator.]"
        )
    if "no attribute 'selected_" in sig_lower:
        return (
            "[System: You keep calling a non-existent `bpy.context.selected_*` attribute "
            "(e.g. selected_edges, selected_faces, selected_verts). Blender does not expose "
            "edit-mode selections on context. Read them with bmesh:\n"
            "    import bmesh\n"
            "    bm = bmesh.from_edit_mesh(bpy.context.view_layer.objects.active.data)\n"
            "    sel = [e for e in bm.edges if e.select]\n"
            "Use `bpy.context.selected_objects` only for object-mode object selection. "
            "Fix the code \u2014 do not retry it verbatim.]"
        )
    if "shadernodebsdfprincipled" in sig_lower and "has no attribute" in sig_lower:
        return (
            "[System: ShaderNodeBsdfPrincipled has NO `base_color` attribute. "
            "Use the node's inputs dictionary instead:\n"
            "    principled = nodes.new('ShaderNodeBsdfPrincipled')\n"
            "    principled.inputs['Base Color'].default_value = (R, G, B, 1.0)\n"
            "Other inputs: 'Metallic', 'Roughness', 'Alpha', 'Emission Color'. "
            "Run `print([i.name for i in principled.inputs])` to list all inputs. "
            "Or call get_python_api_docs('bpy.types.ShaderNodeBsdfPrincipled') "
            "to see the full API reference. Fix the code \u2014 do not retry it verbatim.]"
        )
    if "no output from execute_blender_code" in sig_lower:
        return (
            "[System: Your code executed but produced no output. This usually means "
            "the code ran but print() was not called, or the result was empty. "
            "Add print() statements to verify each step, or assign a dict to "
            "a variable named result to return data. "
            "Use get_python_api_docs() to look up the correct API before retrying.]"
        )
    if "subdivision" in sig_lower and "has no attribute" in sig_lower:
        return (
            "[System: Blender 5.3 subdivision modifiers changed their API. "
            "Use print(dir(mod)) to see available attributes. "
            "Use get_python_api_docs('bpy.types.SubdivisionSurfaceModifier') "
            "for the exact API. Fix the code \u2014 do not retry it verbatim.]"
        )
    return (
        "[System: You've hit the same error multiple times in a row. "
        "Stop and reconsider your approach. Read the error message carefully "
        "and try a different strategy. "
        "Use get_python_api_docs to look up the correct API before retrying "
        "(e.g. get_python_api_docs('bpy.types.ShaderNodeBsdfPrincipled') "
        "to see available attributes and inputs).]"
    )


# ---------------------------------------------------------------------------
# Smart undo helpers — detect code iteration and auto-undo duplicates

def _extract_code_operations(code: str) -> set[str]:
    """Extract operation signatures from a code string for overlap detection.

    Returns a set of strings representing operations: ``bpy.ops`` calls,
    ``bpy.data.*.new/remove`` calls, node tree operations, material
    assignment, modifier operations, and quoted name literals.
    """
    ops: set[str] = set()
    # Extract bpy.ops.* calls (e.g. bpy.ops.mesh.primitive_cube_add).
    for m in re.finditer(r"bpy\.ops\.([a-z_]+)\.([a-z_]+)", code):
        ops.add("op:{:s}.{:s}".format(m.group(1), m.group(2)))
    # Extract bpy.data.*.new() / .remove() / .load() calls.
    for m in re.finditer(r"bpy\.data\.([a-z_]+)\.(new|remove|load)", code):
        ops.add("data:{:s}.{:s}".format(m.group(1), m.group(2)))
    # Extract node tree node creation (e.g. .node_tree.nodes.new('ShaderNodeBsdfPrincipled')).
    for m in re.finditer(r"\.node_tree\.nodes\.new\('([^']+)'\)", code):
        ops.add("node:new:{:s}".format(m.group(1)))
    # Extract node tree link creation.
    if re.search(r"\.node_tree\.links\.new\(", code):
        ops.add("node:link")
    # Extract node tree node removal.
    if re.search(r"\.node_tree\.nodes\.remove\(", code):
        ops.add("node:remove")
    # Extract node tree node clear.
    if re.search(r"\.node_tree\.nodes\.clear\(", code):
        ops.add("node:clear")
    # Extract material assignment via .data.materials.append().
    for m in re.finditer(r"\.data\.materials\.append\(([^)]+)\)", code):
        ops.add("mat:append:{:s}".format(m.group(1).strip().strip('"\'')))
    # Extract material assignment via .active_material =.
    for m in re.finditer(r"\.active_material\s*=\s*([^\s;#]+)", code):
        ops.add("mat:assign:{:s}".format(m.group(1).strip()))
    # Extract material slot assignment.
    for m in re.finditer(r"\.material_slots\[\d+\]\.material\s*=\s*([^\s;#]+)", code):
        ops.add("mat:slot:{:s}".format(m.group(1).strip()))
    # Extract modifier creation.
    for m in re.finditer(r"\.modifiers\.new\(name=([^,]+),?\s*type=([^)]+)\)", code):
        ops.add("mod:new:{:s}".format(m.group(2).strip().strip('"\'')))
    # Extract modifier removal.
    if re.search(r"\.modifiers\.remove\(", code):
        ops.add("mod:remove")
    # Extract quoted string literals that look like names (2+ chars, no spaces).
    for m in re.finditer(r'"([A-Za-z_][A-Za-z0-9_.]{1,40})"', code):
        ops.add("name:{:s}".format(m.group(1)))
    return ops


def _codes_overlap(prev_code: str, new_code: str) -> bool:
    """Return ``True`` if two code strings share operations (indicating iteration).

    Compares extracted operations from both code strings. If they share
    any ``bpy.ops`` calls, ``bpy.data.new/remove`` calls, or name literals,
    the new code is likely iterating on the same task as the previous code.
    """
    prev_ops = _extract_code_operations(prev_code)
    new_ops = _extract_code_operations(new_code)
    return bool(prev_ops & new_ops)


def _code_is_readonly(code: str) -> bool:
    """Return ``True`` if *code* appears to be read-only (no scene mutations).

    Read-only code only inspects the scene (e.g. ``len(bpy.data.objects)``)
    and doesn't create, modify, or delete any datablocks.  Skipping the
    entity snapshot for read-only code saves 12 datablock iterations per
    successful execution — a significant saving when the LLM makes many
    inspection calls between mutation calls.
    """
    _MUTATION_PATTERNS = (
        "bpy.ops.",
        ".new(",
        ".remove(",
        ".load(",
        ".clear(",
        ".link(",
        ".unlink(",
        ".append(",
        ".active_material",
        ".material_slots",
        ".modifiers.",
        "collections.new",
        "color_tag",
        "children.link",
        "objects.unlink",
        "layer_col.exclude",
        "layer_col.hide_viewport",
    )
    return not any(p in code for p in _MUTATION_PATTERNS)


# ---------------------------------------------------------------------------
# Entity snapshot / diff — track what the LLM creates during a turn

@dataclass
class _EntitySnapshot:
    """Snapshot of all datablock names in the scene at a point in time."""
    object_names: set[str] = field(default_factory=set)
    mesh_names: set[str] = field(default_factory=set)
    material_names: set[str] = field(default_factory=set)
    node_group_names: set[str] = field(default_factory=set)
    image_names: set[str] = field(default_factory=set)
    light_names: set[str] = field(default_factory=set)
    camera_names: set[str] = field(default_factory=set)
    collection_names: set[str] = field(default_factory=set)
    curve_names: set[str] = field(default_factory=set)
    grease_pencil_names: set[str] = field(default_factory=set)
    armature_names: set[str] = field(default_factory=set)
    text_names: set[str] = field(default_factory=set)

    @classmethod
    def from_dict(cls, data: dict[str, list[str]]) -> "_EntitySnapshot":
        """Build a snapshot from the dict returned by the snapshot code."""
        return cls(
            object_names=set(data.get("object_names", [])),
            mesh_names=set(data.get("mesh_names", [])),
            material_names=set(data.get("material_names", [])),
            node_group_names=set(data.get("node_group_names", [])),
            image_names=set(data.get("image_names", [])),
            light_names=set(data.get("light_names", [])),
            camera_names=set(data.get("camera_names", [])),
            collection_names=set(data.get("collection_names", [])),
            curve_names=set(data.get("curve_names", [])),
            grease_pencil_names=set(data.get("grease_pencil_names", [])),
            armature_names=set(data.get("armature_names", [])),
            text_names=set(data.get("text_names", [])),
        )


@dataclass
class _EntityDiff:
    """Difference between two snapshots — entities created in between."""
    object_names: set[str] = field(default_factory=set)
    mesh_names: set[str] = field(default_factory=set)
    material_names: set[str] = field(default_factory=set)
    node_group_names: set[str] = field(default_factory=set)
    image_names: set[str] = field(default_factory=set)
    light_names: set[str] = field(default_factory=set)
    camera_names: set[str] = field(default_factory=set)
    collection_names: set[str] = field(default_factory=set)
    curve_names: set[str] = field(default_factory=set)
    grease_pencil_names: set[str] = field(default_factory=set)
    armature_names: set[str] = field(default_factory=set)
    text_names: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        """Return ``True`` if no entities were created."""
        return not any(vars(self).values())

    def merge(self, other: "_EntityDiff") -> None:
        """Merge another diff into this one (union of all sets)."""
        for field_name in vars(self):
            getattr(self, field_name).update(getattr(other, field_name))

    def summary(self) -> str:
        """Return a human-readable summary like 'objects: Cube, Sphere; materials: RedMat'."""
        parts: list[str] = []
        labels = [
            ("objects", "object_names"),
            ("meshes", "mesh_names"),
            ("materials", "material_names"),
            ("node groups", "node_group_names"),
            ("images", "image_names"),
            ("lights", "light_names"),
            ("cameras", "camera_names"),
            ("collections", "collection_names"),
            ("curves", "curve_names"),
            ("grease pencils", "grease_pencil_names"),
            ("armatures", "armature_names"),
            ("texts", "text_names"),
        ]
        for label, field_name in labels:
            names = getattr(self, field_name)
            if names:
                sorted_names = sorted(names)
                if len(sorted_names) <= 5:
                    parts.append("{:s}: {:s}".format(label, ", ".join(sorted_names)))
                else:
                    parts.append("{:s}: {:s} (+{:d} more)".format(
                        label, ", ".join(sorted_names[:5]), len(sorted_names) - 5))
        return "; ".join(parts) if parts else "(none)"


def _diff_snapshots(
    prev: _EntitySnapshot,
    current: _EntitySnapshot,
) -> _EntityDiff:
    """Compute the diff between two snapshots."""
    diff = _EntityDiff()
    for field_name in vars(diff):
        prev_set = getattr(prev, field_name)
        curr_set = getattr(current, field_name)
        new_items = curr_set - prev_set
        getattr(diff, field_name).update(new_items)
    return diff


def _entity_diff_to_context_message(diff: _EntityDiff) -> str:
    """Format an entity diff as a system-level context message for the LLM."""
    summary = diff.summary()
    if diff.is_empty():
        return ""
    return (
        "[System: WARNING — You already created these entities this turn:\n"
        "{:s}\n"
        "DO NOT create them again. Modify the existing ones by name. "
        "Create something DIFFERENT with distinct names only.]"
    ).format(summary)


def _build_cleanup_code(diff: _EntityDiff) -> str:
    """Generate Blender Python code to delete entities created by a failed execution.

    Uses the entity diff to remove objects, meshes, materials, lights, cameras,
    collections, curves, grease pencils, armatures, and node groups that were
    created since the last snapshot.  This is a fallback when ``bpy.ops.ed.undo()``
    fails (e.g. no window/area available, or undo stack is empty).
    """
    parts: list[str] = [
        "# blmcp-toolcode-skip-preflight",
        "import bpy",
        "result = {'status': 'ok', 'cleaned': []}",
        "",
    ]
    # Map diff field names to bpy.data collection names and item types.
    _DATA_MAP = (
        ("object_names", "objects", "Object"),
        ("mesh_names", "meshes", "Mesh"),
        ("material_names", "materials", "Material"),
        ("light_names", "lights", "Light"),
        ("camera_names", "cameras", "Camera"),
        ("collection_names", "collections", "Collection"),
        ("curve_names", "curves", "Curve"),
        ("grease_pencil_names", "grease_pencils", "GreasePencil"),
        ("armature_names", "armatures", "Armature"),
        ("node_group_names", "node_groups", "NodeGroup"),
        ("image_names", "images", "Image"),
        ("text_names", "texts", "Text"),
    )
    for field, coll, _label in _DATA_MAP:
        names = getattr(diff, field, set())
        if names:
            names_str = ", ".join(repr(n) for n in sorted(names))
            parts.append(
                "# Remove {:d} {:s}\n"
                "for _name in [{:s}]:\n"
                "    _item = bpy.data.{:s}.get(_name)\n"
                "    if _item:\n"
                "        bpy.data.{:s}.remove(_item)\n"
                "        result['cleaned'].append(_name)".format(
                    len(names), coll, names_str, coll, coll))
    parts.append("")
    parts.append("result['message'] = 'Cleaned up {:d} orphaned datablocks'".format(
        sum(len(getattr(diff, f, set())) for f, _, _ in _DATA_MAP)))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Undo helper — generates code that works in any workspace

def _undo_code(action: str, message: str = "", extra_result: str = "") -> str:
    """Generate Blender Python code for undo/push that works in any workspace.

    Falls back to any available area type when ``VIEW_3D`` is not present
    (e.g. Scripting workspace).  Without this fallback, the ``for...else``
    loop silently skips and the undo never fires, leaving duplicate objects.

    *action* — ``"undo"`` or ``"push"``.
    *message* — undo step name (only used when *action* is ``"push"``).
    *extra_result* — optional extra JSON keys to append to the result dict
        (e.g. ``'\\n    "snapshot": {...},\\n'``).
    """
    if action == "undo":
        body = "bpy.ops.ed.undo()"
    else:
        body = "bpy.ops.ed.undo_push(message='{:s}')".format(message)
    return (
        "# blmcp-toolcode-skip-preflight\n"
        "import bpy\n"
        "def _sn(seq):\n"
        "    try:\n"
        "        return sorted(x.name for x in seq)\n"
        "    except Exception:\n"
        "        return []\n"
        "result = {{'status': 'ok', 'message': '{:s} executed'{:s}}}\n"
        "# Try VIEW_3D first, fall back to any area type.\n"
        "for w in bpy.context.window_manager.windows:\n"
        "    for a in w.screen.areas:\n"
        "        if a.type == 'VIEW_3D':\n"
        "            with bpy.context.temp_override(window=w, area=a):\n"
        "                {:s}\n"
        "            break\n"
        "    else:\n"
        "        continue\n"
        "    break\n"
        "else:\n"
        "    # No VIEW_3D found — try any area in any window.\n"
        "    for w in bpy.context.window_manager.windows:\n"
        "        for a in w.screen.areas:\n"
        "            with bpy.context.temp_override(window=w, area=a):\n"
        "                {:s}\n"
        "            break\n"
        "        else:\n"
        "            continue\n"
        "        break\n"
        "    else:\n"
        "        result = {{'status': 'error', 'message': 'No window/area available for {:s}'}}\n"
    ).format(action, extra_result, body, body, action)


# Snapshot JSON keys used as extra_result for merged undo+snapshot calls.
# Each datablock iteration is wrapped in a try/except so that a single
# corrupted datablock (e.g. from a depsgraph crash) doesn't kill the
# entire snapshot — the other datablock types are still captured.
_SNAPSHOT_EXTRA = (
    ",\n"
    "    'snapshot': {\n"
    "        'object_names':       _sn(bpy.data.objects),\n"
    "        'mesh_names':         _sn(bpy.data.meshes),\n"
    "        'material_names':     _sn(bpy.data.materials),\n"
    "        'node_group_names':   _sn(bpy.data.node_groups),\n"
    "        'image_names':        _sn(bpy.data.images),\n"
    "        'light_names':        _sn(bpy.data.lights),\n"
    "        'camera_names':       _sn(bpy.data.cameras),\n"
    "        'collection_names':   _sn(bpy.data.collections),\n"
    "        'curve_names':        _sn(bpy.data.curves),\n"
    "        'grease_pencil_names': _sn(bpy.data.grease_pencils),\n"
    "        'armature_names':     _sn(bpy.data.armatures),\n"
    "        'text_names':         _sn(bpy.data.texts),\n"
    "    }\n"
)


# ---------------------------------------------------------------------------
# Text editor memory bank helpers

_code_sequence_counter: int = 0


def _next_code_sequence() -> str:
    """Return the next zero-padded 3-digit sequence number (001, 002, ...)."""
    global _code_sequence_counter
    _code_sequence_counter += 1
    return "{:03d}".format(_code_sequence_counter)


def _clear_coworker_text_blocks() -> None:
    """Remove all Coworker_* text datablocks from Blender's text editor."""
    global _code_sequence_counter
    _code_sequence_counter = 0
    try:
        import bpy as _bpy  # pylint: disable=import-error
        for text_block in list(_bpy.data.texts):
            if text_block.name.startswith("Coworker_"):
                _bpy.data.texts.remove(text_block)
    except Exception:
        pass  # Best-effort.


def _save_code_to_text_editor_deferred(code: str, seq: str) -> None:
    """Schedule saving code to a text editor datablock on the main thread.

    Must be called from a background thread.  Uses ``bpy.app.timers`` to
    defer the ``bpy.data.texts`` operations to the main thread since
    Blender's Python API is not thread-safe.
    """
    def _do_save() -> None:
        try:
            import bpy as _bpy  # pylint: disable=import-error
            prefs = _bpy.context.preferences.addons[__package__].preferences
            if getattr(prefs, "save_code_to_text_editor", True):
                name = "Coworker_{:s}".format(seq)
                text_block = _bpy.data.texts.new(name)
                text_block.write(code)
                print("[🛠️Coworker] saved code to text editor '{:s}'".format(name))
        except Exception as _ex:
            print("[🛠️Coworker] FAILED to save code to text editor: {:s}".format(str(_ex)))

    import bpy as _bpy  # pylint: disable=import-error
    _bpy.app.timers.register(_do_save, first_interval=0.0)


def export_session_log(auto_saved: bool = False) -> None:
    """Export the current session to a Blender text datablock.

    Gathers system prompt, conversation history, error signatures,
    llama-server log tail, and version info into a timestamped text block.

    Args:
        auto_saved: If True, appends a note that this was auto-saved due to error spiral.
    """
    import time as _time
    import platform
    import bpy as _bpy  # pylint: disable=import-error

    timestamp = _time.strftime("%Y-%m-%d_%H-%M-%S", _time.localtime())
    block_name = "Coworker_Session_{:s}".format(timestamp)

    lines = []
    lines.append("=" * 72)
    lines.append("BFA Coworker Session Log")
    lines.append("Timestamp: {:s}".format(_time.strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("Auto-saved: {:s}".format(str(auto_saved)))
    lines.append("=" * 72)
    lines.append("")

    # Version info.
    lines.append("--- Version Info ---")
    try:
        manifest_path = _bpy.utils.resource_path('LOCAL')
        lines.append("Blender: {:s}".format(str(_bpy.app.version_string)))
    except Exception:
        lines.append("Blender: unknown")
    lines.append("Python: {:s}".format(platform.python_version()))
    lines.append("OS: {:s} {:s}".format(platform.system(), platform.release()))
    lines.append("")

    # System prompt.
    lines.append("--- System Prompt ---")
    try:
        sys_prompt = _get_system_prompt_with_rules()
        lines.append(sys_prompt)
    except Exception as ex:
        lines.append("[Error loading system prompt: {:s}]".format(str(ex)))
    lines.append("")

        # Conversation history -- grouped by turn.
    lines.append("--- Conversation History ---")
    hist = list(_agent_state.conversation_history)
    turn_list = []
    cur = []
    for m in hist:
        r = m.get("role", "")
        c = m.get("content", "")
        if r == "user" and not c.startswith("[System:"):
            if cur:
                turn_list.append(cur)
            cur = [m]
        elif r in ("assistant", "tool", "reasoning") or (r == "user" and c.startswith("[System:")):
            cur.append(m)
    if cur:
        turn_list.append(cur)

    for ti, tr in enumerate(turn_list):
        lines.append("\n=== Turn {:d} ===".format(ti + 1))
        for m in tr:
            r = m.get("role", "unknown")
            lines.append("[{:s}]".format(r.upper()))
            c = m.get("content", "")
            if r == "assistant":
                if c:
                    lines.append("Content: {:s}".format(str(c)[:2000]))
                for tc in m.get("tool_calls", []):
                    fn = tc.get("function", {})
                    lines.append("  Tool: {:s}".format(fn.get("name", "unknown")))
                    lines.append("  Args: {:s}".format(str(fn.get("arguments", ""))[:500]))
            elif r == "user":
                lines.append("Content: {:s}".format(str(c)[:2000]))
            elif r == "tool":
                lines.append("Result: {:s}".format(str(c)[:1000]))
            elif r == "reasoning":
                lines.append("Thinking: {:s}".format(str(c)))
        lines.append("")

    lines.append("")

    # Token usage (issue #69).
    lines.append("--- Token Usage ---")
    _turn_u = _agent_state.turn_usage
    _sess_u = _agent_state.session_usage
    if _sess_u:
        lines.append(
            "Turn: {:d} prompt / {:d} completion / {:d} total".format(
                _turn_u.get("prompt_tokens", 0),
                _turn_u.get("completion_tokens", 0),
                _turn_u.get("total_tokens", 0),
            )
        )
        lines.append(
            "Session: {:d} prompt / {:d} completion / {:d} total".format(
                _sess_u.get("prompt_tokens", 0),
                _sess_u.get("completion_tokens", 0),
                _sess_u.get("total_tokens", 0),
            )
        )
    else:
        lines.append("No usage reported by the LLM backend this session.")
    lines.append("")

# Error signatures.
    lines.append("--- Error Info ---")
    if _agent_state.error:
        lines.append("Current error: {:s}".format(
            _agent_state.error_full or str(_agent_state.error)))
    lines.append("")

    # LLM server log tail.
    lines.append("--- LLM Server Log (last 50 lines) ---")
    llm_stderr = getattr(_agent_state, 'llm_stderr', None)
    if llm_stderr:
        try:
            stderr_text = llm_stderr if isinstance(llm_stderr, str) else str(llm_stderr)
            log_lines = stderr_text.strip().splitlines()
            for line in log_lines[-50:]:
                lines.append(line)
        except Exception:
            lines.append("[Could not read LLM stderr]")
    else:
        lines.append("[No LLM server log available]")
    lines.append("")
    lines.append("=" * 72)
    lines.append("End of session log")

    # Write to text block on main thread.
    def _do_export() -> None:
        try:
            text_block = _bpy.data.texts.new(block_name)
            text_block.write("\n".join(lines))
            print("[🛠️Coworker] Session log exported to text block '{:s}'".format(block_name))
        except Exception as ex:
            print("[🛠️Coworker] Failed to export session log: {:s}".format(str(ex)))

    _bpy.app.timers.register(_do_export, first_interval=0.0)


def export_session_log_to_clipboard() -> str:
    """Return the session log as a string (for copy-to-clipboard)."""
    import time as _time
    import platform

    lines = []
    lines.append("=" * 72)
    lines.append("BFA Coworker Session Log")
    lines.append("Timestamp: {:s}".format(_time.strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("=" * 72)
    lines.append("")
    lines.append("--- System Prompt ---")
    try:
        lines.append(_get_system_prompt_with_rules())
    except Exception as ex:
        lines.append("[Error: {:s}]".format(str(ex)))
    lines.append("")
    lines.append("--- Conversation History ---")
    history = list(_agent_state.conversation_history)
    for i, msg in enumerate(history):
        role = msg.get("role", "unknown")
        lines.append("\n[Message {:d}] role={:s}".format(i + 1, role))
        content = msg.get("content", "")
        lines.append("Content: {:s}".format(str(content)[:2000]))
    return "\n".join(lines)


# MCP port of the turn that last locked datablocks, so the release path can
# restore them without re-plumbing the port through the turn's call stack.
_active_lock_mcp_port: int = 0


def _lock_step_entities(step_diff: Any, mcp_port: int) -> None:
    """Soft-lock a step's created/touched datablocks (scene safety Phase 1).

    Sets ``hide_select`` on the objects/collections the step created or
    touched so the user cannot re-target them in the viewport mid-turn while
    the agent keeps full programmatic access.  Best-effort: never breaks a
    turn, and a missing config/guard is silently ignored.
    """
    global _active_lock_mcp_port
    try:
        from . import llm_manager as _llm_mgr
        if not getattr(_llm_mgr.get_config(), "lock_scene_while_working", True):
            return
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    try:
        objs = getattr(step_diff, "object_names", None) or set()
        colls = getattr(step_diff, "collection_names", None) or set()
        if not objs and not colls:
            return
        if mcp_port:
            _active_lock_mcp_port = mcp_port
        co_work_guard.record_managed(objs, colls)
        raw = _call_mcp_tool_sync(
            "execute_blender_code",
            {"code": co_work_guard.build_lock_code(objs, colls)}, mcp_port)
        # Best-effort: capture the true prior hide_select values so the
        # unlock restores them exactly rather than a blanket False.
        try:
            data = json.loads(raw)
            result = data.get("result", {}) if isinstance(data, dict) else {}
            co_work_guard.record_prior(
                result.get("prior_hide_select"),
                result.get("prior_hide_select_coll"))
        except (json.JSONDecodeError, TypeError):
            pass
        print("[🛠️Coworker] _lock_step_entities: locked {:d} objects, {:d} "
              "collections".format(len(objs), len(colls)))
    except Exception as _ex:  # pylint: disable=broad-exception-caught
        print("[🛠️Coworker] _lock_step_entities: skipped — {:s}".format(str(_ex)))


def _release_scene_lock() -> None:
    """Restore every co-work-locked datablock and clear the registry.

    Runs on the turn's worker thread (safe for ``_call_mcp_tool_sync``);
    never runs on the main thread because the MCP pump would deadlock.
    Best-effort: never raises.  On failure the priors are KEPT (not cleared)
    so a later turn's release can still restore them — clearing here would
    discard the restore data while the scene keeps ``hide_select = True``
    (which, if the user saves the .blend, persists the lock).
    """
    global _active_lock_mcp_port
    if not co_work_guard.is_locked():
        return
    if not _active_lock_mcp_port:
        print("[🛠️Coworker] _release_scene_lock: no MCP port — deferring unlock")
        return
    try:
        _call_mcp_tool_sync(
            "execute_blender_code",
            {"code": co_work_guard.build_unlock_code()},
            _active_lock_mcp_port)
        print("[🛠️Coworker] _release_scene_lock: released co-work scene lock")
        co_work_guard.clear()
        _active_lock_mcp_port = 0
    except Exception as _ex:  # pylint: disable=broad-exception-caught
        print("[🛠️Coworker] _release_scene_lock: unlock failed, keeping priors "
              "for a later retry — {:s}".format(str(_ex)))


def run_conversation_turn(
    user_message: str,
    on_text: Callable[[str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
    llm_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    mcp_port: int = _MCP_SERVER_DEFAULT_PORT,
    chat_mode: str = "AGENT",
    on_stream_text: Callable[[str], None] | None = None,
    on_stream_reasoning: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """
    Run a full conversation turn.

    1. Prepends system prompt (if not already present).
    2. Appends user message to history.
    3. Sends to LLM, handles tool calls via MCP.
    4. Returns updated conversation history.

    When *chat_mode* is ``"ASK"``, tool execution is skipped and the LLM
    responds with text only (read-only Q&A).

    This is a BLOCKING call — run it via ``schedule_coro`` or in a thread.
    """
    # ── Re-entrancy guard ──────────────────────────────────────────────
    if _agent_state.turn_active:
        if _stop_event.is_set():
            # Previous turn was aborted by the user — the blocking HTTP
            # request is still in-flight but we clear the flag so the new
            # message can proceed.  The old turn's response will be discarded.
            _agent_state.turn_active = False
            print("[🛠️Coworker] run_conversation_turn: previous turn aborted, clearing guard")
        else:
            print("[🛠️Coworker] run_conversation_turn: re-entrancy blocked — turn already active")
            return _agent_state.conversation_history
    _agent_state.turn_active = True
    try:
        return _run_conversation_turn_inner(
            user_message, on_text, on_status, on_reasoning,
            llm_url, api_key, model, mcp_port, chat_mode,
            on_stream_text, on_stream_reasoning,
        )
    finally:
        _agent_state.turn_active = False
        # Always release the co-work scene lock (FINISH, error, Stop, or an
        # exception all pass through here).
        _release_scene_lock()


# Turn counter for memory-block "Last updated" stamps (Tier 3).
_session_turn_count = 0

# Domains loaded for the current session.  Session-sticky: it only grows and
# is never trimmed mid-session, so the tool schema sent to a remote provider
# changes rarely (keeping the prompt-cache prefix stable) and a domain the
# user moved on from stays available if the conversation circles back.
# Reset on New Thread.
_session_loaded_domains: set[str] = set()


def reset_session_domains() -> None:
    """Forget the session-sticky loaded domains (called by New Thread)."""
    global _session_loaded_domains
    _session_loaded_domains = set()


def _memory_writer_factory(llm_url: str, api_key: str, model: str, max_tokens: int):
    """Return a memory_writer callback using a small dedicated LLM call."""
    def _writer(retired_text: str, prior_memory: str) -> str | None:
        messages = session_memory.memory_writer_prompt(retired_text, prior_memory)
        # A writer failure is non-fatal (the heuristic fallback is used), so
        # preserve the main turn's error state across the call: otherwise a
        # transient writer hiccup is left in ``_agent_state.error`` and the
        # UI / benchmark recorder reports a failed turn that actually
        # succeeded.
        _saved_err = (_agent_state.error, _agent_state.error_full,
                      _agent_state.error_kind)
        try:
            response = _transport.openai_chat_completions(
                llm_url, messages, None, api_key, model,
                max_tokens=max_tokens, thinking_budget_tokens=0,
            )
        finally:
            (_agent_state.error, _agent_state.error_full,
             _agent_state.error_kind) = _saved_err
        if not response:
            return None
        try:
            return (
                response.get("choices", [{}])[0]
                .get("message", {}).get("content") or None
            )
        except (IndexError, AttributeError):
            return None
    return _writer


def _maybe_compact_session(
    history: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    prompt_budget: int,
    on_status: Callable[[str], None] | None = None,
    memory_writer: Callable[[str, str], str | None] | None = None,
) -> None:
    """Trigger session-memory compaction when the prompt approaches the budget.

    Retires turns beyond the verbatim window into ``session_memory.store``
    (archive + memory block) when the estimated prompt (history + tool
    schema) reaches :data:`COMPACTION_TRIGGER_RATIO` of the safe budget, or
    when the history outgrows the verbatim window.  Visible status, automatic
    checkpoint after compaction (D5/D6).  Never raises.
    """
    global _session_turn_count
    _session_turn_count += 1
    st = session_memory.store
    tools_tokens = _estimate_tools_tokens(tools)
    # Compare consistently: ``estimated`` (messages + tool schema) against the
    # *whole* prompt budget.  The previous code subtracted the tool schema from
    # the budget AND added it to the estimate, so the trigger fired far too
    # early — on the first turn it fired with two messages and, because there
    # was nothing old enough to retire, produced a no-op "Compacting…" status
    # and an empty checkpoint.
    estimated = _estimate_messages_tokens(history) + tools_tokens
    _boundary = session_memory.find_retire_boundary(history)
    _can_retire = _boundary < len(history)
    _over_window = (
        sum(1 for m in history if not m.get("ui_only"))
        > session_memory.MAX_WINDOW_TURNS + 1
    )
    trigger = _can_retire and (
        (
            prompt_budget > 0
            and estimated >= prompt_budget * session_memory.COMPACTION_TRIGGER_RATIO
        )
        or _over_window
    )
    if not trigger:
        return
    if on_status:
        on_status("Compacting conversation…")
    print("[🛠️Coworker] _maybe_compact_session: estimated {:d} / budget {:d} tokens "
          "— compacting".format(estimated, prompt_budget))
    # Automatic checkpoint of the PRE-compaction state so restore can rewind
    # before the summary.  Taken before history/memory are mutated.
    with session_memory.store_lock:
        st.snapshot(history, reason="compaction", turn_index=_session_turn_count)
    kept, memory_block, retired = session_memory.compact_history(
        history,
        prior_memory=st.memory_block,
        memory_writer=memory_writer,
        updated_turn=_session_turn_count,
    )
    with session_memory.store_lock:
        st.memory_block = memory_block
        st.memory_updated_turn = _session_turn_count
        st.append_archive(retired)
        history[:] = kept
    # Reasoning entries exist for the chat panel while a turn is young, but
    # once they fall outside the verbatim window they are dead weight: they
    # are stripped before every LLM request anyway. Prune them from STORAGE
    # (not just at send time) so archived sessions and later compactions do
    # not carry stale chain-of-thought. The most recent window is preserved
    # verbatim for the panel.
    #
    # ``find_retire_boundary`` returns the index where the RECENT verbatim
    # window begins, or ``len(history)`` when the whole history IS the recent
    # window (nothing retirable).  Only prune reasoning from the OLD region
    # ``[:boundary]`` and ONLY when a boundary actually exists — otherwise the
    # recent window (which is what the Workshop shows) must stay verbatim.
    _reasoning_boundary = session_memory.find_retire_boundary(history)
    if _reasoning_boundary < len(history):
        history[:] = (
            [m for m in history[:_reasoning_boundary] if m.get("role") != "reasoning"]
            + history[_reasoning_boundary:]
        )
    print("[🛠️Coworker] _maybe_compact_session: retired {:d} messages, "
          "archived, checkpoint saved".format(len(retired)))


def _force_compact_session(
    history: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    prompt_budget: int,
    on_status: Callable[[str], None] | None = None,
    memory_writer: Callable[[str, str], str | None] | None = None,
) -> int:
    """Compact aggressively to recover from a context-overflow 400.

    The server rejected the request as larger than its window even though the
    threshold trigger did not shrink it enough (e.g. the retained verbatim
    window itself was large).  Retire into a *smaller* window than the normal
    :data:`session_memory.MAX_WINDOW_TURNS` so there is something left to
    drop, archive the retired turns, refresh the memory block, and snapshot a
    checkpoint.  The caller then rebuilds and retries the request; if even the
    reduced window does not fit, the preflight surfaces the friendly
    "conversation no longer fits" message.  Returns the number retired.

    Never raises.
    """
    global _session_turn_count
    st = session_memory.store
    if on_status:
        on_status("Compacting conversation…")
    # Snapshot the PRE-compaction state (only when something will retire).
    _pre_boundary = session_memory.find_retire_boundary(history, _FORCE_COMPACT_KEEP_RECENT)
    if _pre_boundary < len(history):
        with session_memory.store_lock:
            st.snapshot(history, reason="overflow", turn_index=_session_turn_count)
    kept, memory_block, retired = session_memory.compact_history(
        history,
        prior_memory=st.memory_block,
        memory_writer=memory_writer,
        keep_recent=_FORCE_COMPACT_KEEP_RECENT,
        updated_turn=_session_turn_count,
    )
    with session_memory.store_lock:
        st.memory_block = memory_block
        st.memory_updated_turn = _session_turn_count
        st.append_archive(retired)
        history[:] = kept
    # Same reasoning-entry prune as the threshold path: once outside the
    # verbatim window they are UI-only dead weight that is stripped before
    # every request anyway.  Prune the OLD region only and only when a
    # boundary exists (see the note in ``_maybe_compact_session``).
    _boundary = session_memory.find_retire_boundary(history)
    if _boundary < len(history):
        history[:] = (
            [m for m in history[:_boundary] if m.get("role") != "reasoning"]
            + history[_boundary:]
        )
    print("[🛠️Coworker] _force_compact_session: retired {:d} messages "
          "(keep_recent={:d})".format(len(retired), _FORCE_COMPACT_KEEP_RECENT))
    return len(retired)


def _run_conversation_turn_inner(
    user_message: str,
    on_text: Callable[[str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
    llm_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    mcp_port: int = _MCP_SERVER_DEFAULT_PORT,
    chat_mode: str = "AGENT",
    on_stream_text: Callable[[str], None] | None = None,
    on_stream_reasoning: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """
    Inner body of ``run_conversation_turn`` — wrapped by the re-entrancy guard.
    """
    clear_stop()
    history = _agent_state.conversation_history

    # Drop any leading assistant messages that are NOT UI-only.  A
    # conversation must begin with the system prompt or a user turn; a
    # leading assistant message is a greeting artifact (older builds appended
    # the welcome text straight to the history), and it both confuses the
    # model -- which answers the greeting instead of the user's request -- and
    # trips strict Jinja chat templates.  This also repairs sessions already
    # running in that shape.  ``ui_only`` messages are kept: they are stripped
    # later, and dropping them here would remove the greeting from the panel.
    while history and history[0].get("role") == "assistant" and not history[0].get("ui_only"):
        dropped = history.pop(0)
        print("[🛠️Coworker] run_conversation_turn: dropped leading assistant "
              "message ({:d} chars)".format(len(str(dropped.get("content") or ""))))

    # Ensure the first message is the system prompt.
    if not history or history[0].get("role") != "system":
        system_text = _get_system_prompt_with_rules()
        history.insert(0, {"role": "system", "content": system_text})
        print("[🛠️Coworker] run_conversation_turn: inserted system prompt ({:d} chars)".format(
            len(system_text)))

    # Clear any pending screenshot image from a previous turn — the user
    # is starting fresh, so the old screenshot is stale.
    _agent_state._pending_image = None

    # ── Pre-flight empty-scene check ──────────────────────────────────
    # Small local models often call mode-dependent operators (mode_set, etc.)
    # on an empty scene, which fails with "Context missing active object".
    # Warn the LLM upfront so it creates objects first.
    # NOTE: We append to the existing system prompt (position 0) rather than
    # creating a new message — Qwen's Jinja template requires ALL system
    # messages at the beginning and rejects any mid-conversation system role.
    _preflight_note = ""
    try:
        import bpy as _bpy  # pylint: disable=import-error
        if len(_bpy.data.objects) == 0:
            _preflight_note = (
                "\n\n[Note: The Blender scene is currently empty \u2014 no objects exist. "
                "You must create objects before using mode-dependent operators "
                "like bpy.ops.object.mode_set().]"
            )
            print("[\U0001f6e0\ufe0fCoworker] run_conversation_turn: empty scene detected, injected pre-flight note")
    except Exception:
        pass  # Best-effort; don't break the agent loop.

    # Inject the preflight note into the system prompt (not a separate message)
    # so the message sequence stays: [system, user, ...].
    # Guard: only inject once — don't duplicate on subsequent turns.
    _preflight_marker = "[Note: The Blender scene is currently empty"
    if _preflight_note and _preflight_marker not in history[0]["content"]:
        history[0]["content"] += _preflight_note

    # ── Ask-mode system prompt addendum (issue #66) ──────────────────
    # In Ask mode the system prompt must NOT invite tool use — the default
    # prompt tells the model it can "inspect and modify the scene", which
    # actively encourages tool calls in a mode that must be informational
    # only.  Append a read-only instruction to the (cached) system prompt.
    # Appended per-turn, not baked into the cache, so switching between
    # Agent and Ask modes mid-session stays correct.  Marker-guarded so
    # it is not duplicated on subsequent Ask-mode turns.
    if chat_mode == "ASK" and _ASK_MODE_PROMPT_ADDENDUM not in history[0]["content"]:
        history[0]["content"] += _ASK_MODE_PROMPT_ADDENDUM

    # Append the user message.
    history.append({"role": "user", "content": user_message, "turn_start": True})

    # ── Smart undo tracking (per-turn) ────────────────────────────────
    # Tracks the last execute_blender_code call to detect iteration and
    # auto-undo duplicates. Reset at the start of each turn.
    _prev_code: str | None = None
    _prev_code_errored: bool = False
    _prev_code_error: str = ""  # Error text for code-bug detection.
    _undo_pushed: bool = False  # True once we've pushed the first undo state.

    # ── Entity tracking (per-turn) ────────────────────────────────────
    # Initial snapshot is taken lazily inside the first undo push (merged
    # into a single round-trip). Reset at the start of each turn.
    _turn_snapshot: _EntitySnapshot | None = None
    _turn_entities: _EntityDiff = _EntityDiff()
    _entity_context_injected: bool = False  # True once we've injected entity context.

    # ── Spiral detection (per-turn) ───────────────────────────────────
    # Tracks consecutive identical tool errors to break LLM retry loops.
    _consecutive_errors: list[str] = []

    # In Ask mode, skip tool listing and execution entirely.
    if chat_mode == "ASK":
        openai_tools = []
    else:
        # Get MCP tools.
        tools = _list_tools_sync(mcp_port)
        openai_tools = _mcp_tools_to_openai(tools) if tools else []

    if on_status:
        on_status("Thinking...")
    _agent_state.is_thinking = True
    _agent_state.thinking_start_time = time.time()
    _agent_state.streaming_text = ""
    _agent_state.reasoning_text = ""
    _agent_state.thinking_dots = 0
    # Fresh per-turn token usage counters (issue #69).
    _agent_state.turn_usage = {}
    # Clear any warning from the previous turn.
    _agent_state.warning = ""

    def _llm_request(
        send_messages: list[dict[str, Any]],
        send_tools: list[dict[str, Any]],
        budget: int = 0,
    ) -> dict[str, Any] | None:
        """Run one LLM request with streaming and usage capture (issue #69).

        Streams when possible so text and reasoning arrive live for the
        Workshop and Stop aborts mid-generation; falls back to the
        hardened non-streaming request when the provider rejects
        streaming before the first token (its retry/reshape logic then
        handles 503s, template faults, and 400 flattening as before).
        Usage from whichever path succeeds is accumulated into the
        turn/session totals.

        The stream deltas are also written straight into the rendered
        state (``streaming_text`` / ``reasoning_text``) so the Workshop
        shows text and reasoning live, before the response completes;
        the post-response assignments later in the turn loop still
        overwrite them with the final message.
        """
        def _live_text(text: str) -> None:
            if not text:
                return
            _agent_state.streaming_text = text
            if on_stream_text:
                on_stream_text(text)

        def _live_reasoning(text: str) -> None:
            if not text:
                return
            _agent_state.reasoning_text = text
            if on_stream_reasoning:
                on_stream_reasoning(text)

        response = openai_chat_completions_stream(
            llm_url, send_messages, send_tools, api_key, model,
            max_tokens, thinking_budget_tokens=budget, chat_mode=chat_mode,
            on_status=on_status,
            on_stream_text=_live_text,
            on_stream_reasoning=_live_reasoning,
        )
        if response is None:
            # Streaming not supported by this endpoint — non-streaming fallback.
            response = openai_chat_completions(
                llm_url, send_messages, send_tools, api_key, model,
                max_tokens, thinking_budget_tokens=budget, chat_mode=chat_mode,
            )
        if response is not None:
            _agent_state.record_usage(response.get("usage"))
        return response

    # Determine LLM URL.
    llm_port_local: int | None = None
    if llm_url is None:
        # No URL provided — resolve from config mode.
        from . import llm_manager as _llm_mgr
        _llm_cfg = _llm_mgr.get_config()
        if _llm_cfg.mode == "remote":
            # Build remote URL from config.
            llm_url = _llm_cfg.remote_api_url
            api_key = _llm_cfg.remote_api_key or api_key
            model = _llm_cfg.remote_model or model
        else:
            # Use local llama-server default.
            llm_url = _LLM_CHAT_URL.format(_llm_cfg.local_port)
            llm_port_local = _llm_cfg.local_port

    # Ensure URL ends with /v1/chat/completions (for both local and remote).
    if llm_url:
        base = llm_url.rstrip("/")
        if not base.endswith("/chat/completions"):
            if base.endswith("/v1"):
                llm_url = "{:s}/chat/completions".format(base)
            else:
                llm_url = "{:s}/v1/chat/completions".format(base)

    # Wait for local LLM port to become ready.
    # The model can take 30-120s to load into memory before the server
    # accepts connections. Without this wait, the first chat request
    # would fail with "connection refused".
    if llm_port_local is not None:
        print("[🛠️Coworker] run_conversation_turn: waiting for LLM on 127.0.0.1:{:d}...".format(llm_port_local))
        from . import llm_manager as _llm_mgr
        if not _wait_for_port(
            "127.0.0.1", llm_port_local, timeout=120.0, proc=_llm_mgr.get_llama_process()
        ):
            _agent_state.is_thinking = False
            _agent_state.thinking_start_time = 0.0
            _log_tail = _llm_mgr.get_llama_server_log_tail()
            if _log_tail:
                _agent_state.error = (
                    "LLM server did not become ready — llama-server exited or is stuck.\n\n"
                    "--- llama-server.log (tail) ---\n{:s}".format(_log_tail)
                )
            else:
                _agent_state.error = "LLM server did not become ready after 120s"
            if on_status:
                on_status("Error: LLM server not ready")
            return history

    # Resolve max_tokens from config (local or remote).
    from . import llm_manager as _llm_mgr
    _llm_cfg = _llm_mgr.get_config()
    max_tokens = _llm_cfg.local_max_tokens if llm_port_local is not None else 16384
    # The configured reply size is immutable: the per-iteration cap must be
    # recomputed from THIS, or feeding the already-capped value back in makes
    # the allowance monotonically shrink across tool-loop iterations.
    _requested_max_tokens = max_tokens
    # `thinking_budget_tokens` is a llama-server parameter; strict
    # OpenAI-compatible endpoints reject unknown fields, so only send it
    # on the local path (llm_port_local is None in remote mode).
    thinking_budget = (
        getattr(_llm_cfg, 'thinking_budget_tokens', 0)
        if llm_port_local is not None else 0
    )
    if thinking_budget > 0:
        print("[🛠️Coworker] run_conversation_turn: thinking_budget_tokens={:d}".format(thinking_budget))
    print("[🛠️Coworker] run_conversation_turn: using max_tokens={:d}".format(max_tokens))

    # ── Prompt token budget ────────────────────────────────────────────
    # The context window must hold the prompt AND the generated reply.  The
    # budget is computed against the context size the server *actually*
    # applied (queried from /props via llm_manager.get_runtime_ctx), falling
    # back to the configured value.  Remote providers get 0 (no trimming).
    #
    # Startup-only sizing (D4): a wrong ctx is handled by preflight + the
    # compaction path — never by restarting the server mid-session.
    prompt_budget = 0
    ctx_size_used = 0
    if llm_port_local is not None:
        _ctx_size = getattr(_llm_cfg, "local_ctx_size", 0) or 0
        try:
            _runtime_ctx = _llm_mgr.get_runtime_ctx(llm_port_local)
        except Exception as _ctx_ex:  # pylint: disable=broad-exception-caught
            print("[🛠️Coworker] run_conversation_turn: get_runtime_ctx failed — {:s}".format(str(_ctx_ex)))
            _runtime_ctx = None
        if _runtime_ctx:
            if _ctx_size and _runtime_ctx != _ctx_size:
                print("[🛠️Coworker] run_conversation_turn: runtime n_ctx {:d} != configured {:d} — "
                      "budgeting against the server's value".format(_runtime_ctx, _ctx_size))
            _ctx_size = _runtime_ctx
        if _ctx_size <= 0:
            # Never silently disable budget enforcement: without a real or
            # configured window the prompt would be unbounded and the server
            # would answer with a raw 400.  Use a conservative fallback and
            # say so.
            _ctx_size = _DEFAULT_LOCAL_CTX_FALLBACK
            print("[🛠️Coworker] run_conversation_turn: no runtime/configured context "
                  "size — using conservative fallback {:d} so the prompt stays "
                  "bounded".format(_ctx_size))
        if _ctx_size > 0:
            ctx_size_used = _ctx_size
            prompt_budget = _compute_prompt_budget(_ctx_size, max_tokens)
            print("[🛠️Coworker] run_conversation_turn: prompt budget {:d} tokens "
                  "(ctx {:d}, max_tokens {:d})".format(prompt_budget, _ctx_size, max_tokens))
        # Record actual context usage for the Session panel indicator.
        try:
            _agent_state.ctx_size_used = _ctx_size
            _agent_state.prompt_budget = prompt_budget
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # ── Tool domain system (hybrid: pre-detect + on-demand) ────────────
    # Pre-detect the domain from the user's prompt AND from the current
    # scene content (0 extra round-trips).  The LLM can also call
    # ``load_tools`` mid-turn to switch domains.
    _loaded_domains: set[str] = set(_session_loaded_domains)
    # Domains whose skill reference should be injected into the *sent* system
    # copy.  The actual text (and how much of it fits) is computed inside
    # ``_build_send_messages`` from the real message list, so the allowance
    # tunes itself to the window and the conversation.  Never mutated into the
    # stored history, so it can never accumulate across turns.
    _domain_skill_domains: set[str] = set()
    _all_tools = openai_tools  # Keep full list for on-demand loading.
    if openai_tools:
        # Detect domains for BOTH local and remote.  Remote providers now get
        # the same smart tool filtering + skill reference as local, instead of
        # the full unfiltered schema on every request (smaller schema; and the
        # API-docs tools remain the always-available fallback).
        _detected_domains: set[str] = set()
        _detected_domains.update(_detect_domains(user_message))
        # Also detect domains from scene content (armatures, materials, etc.).
        _detected_domains.update(_detect_domain_from_scene())
        _session_loaded_domains.update(_detected_domains)
        _loaded_domains.update(_detected_domains)
        openai_tools = _build_tool_set(_all_tools, _loaded_domains)
        _domain_skill_domains = set(_loaded_domains)

    # ── Request payload builder ────────────────────────────────────────
    # Extracted so the context-overflow recovery can rebuild the payload
    # from the (now smaller) history and retry without duplicating the
    # slice/strip/trim/inject/budget sequence.  Closes over ``history``,
    # ``openai_tools`` and ``prompt_budget``; returns ``(messages, error)``
    # where *error* is set only when even the pinned turn cannot fit.
    def _build_send_messages() -> tuple[list[dict[str, Any]], str | None]:
        # Slice history to avoid unbounded context growth.  Always keep the
        # system prompt (index 0) if present.  Must preserve tool-call pairs:
        # each "tool" role message MUST follow an "assistant" with tool_calls.
        if len(history) > _MAX_HISTORY_MESSAGES:
            keep = min(_MAX_HISTORY_MESSAGES, len(history))
            if history[0].get("role") == "system":
                msgs = [history[0]] + history[-(keep - 1):]
            else:
                msgs = list(history[-keep:])
        else:
            msgs = list(history)

        # Repair half-finished tool-call exchanges.  This must run for ALL
        # history sizes: the slice above can cut a pair in half, and Qwen's
        # Jinja template crashes with "Unexpected message role" on either an
        # orphaned tool result or an orphaned assistant tool_calls.
        msgs = _repair_tool_call_pairs(msgs)

        # Strip reasoning (non-standard "reasoning" role) and UI-only entries
        # (the startup greeting) before sending to the LLM.
        msgs = _strip_reasoning_from_history(msgs)
        msgs = _strip_ui_only_from_history(msgs)

        # Trim oversized tool results for the request only.  History keeps the
        # full text so the chat panel can display it; the model gets the gist.
        msgs = _trim_history_tool_results(msgs)

        # Sanitize any remaining non-standard roles to "user".
        msgs = _sanitize_message_roles(msgs)

        # ── Inject the session memory block into the system prompt ──
        # The block is small (bounded) and carries retired context; Qwen's
        # Jinja template requires system content up front, so it is appended
        # to message 0 like the domain skills.
        _mem_block = session_memory.store.memory_block
        if _mem_block and msgs and msgs[0].get("role") == "system":
            _sys0 = msgs[0]
            _base = str(_sys0.get("content") or "")
            if _mem_block not in _base:
                # Copy before mutating: msgs[0] is usually the SAME dict as
                # history[0] (no slice happened), so writing in place would
                # accumulate the block in the stored history and double it on
                # every subsequent request.
                _sys0 = dict(_sys0)
                _sys0["content"] = _base.rstrip() + "\n\n" + _mem_block
                msgs[0] = _sys0

        # ── Inject domain-skill reference (send copy, self-tuning) ────
        # Appended to the system copy like the memory block (Qwen's Jinja
        # template needs all system content up front).  In LOCAL mode the
        # allowance is whatever is genuinely SPARE after the messages, the
        # tool schema, and a conversation reserve — so it tunes itself to the
        # window and the conversation with no ratio/ceiling to configure.  In
        # REMOTE mode there is no client-side window (prompt_budget == 0), so
        # a conservative flat cap is used.  Only WHOLE files are included
        # (never truncated); unmatched files are skipped and the API-docs
        # tools remain the fallback.
        _domain_skills_text = ""
        if _domain_skill_domains and msgs and msgs[0].get("role") == "system":
            if prompt_budget > 0:
                _spare = (
                    prompt_budget
                    - _estimate_messages_tokens(msgs)
                    - _estimate_tools_tokens(openai_tools)
                )
                _reserve = max(
                    _SKILLS_RESERVE_TOKENS,
                    int(prompt_budget * _SKILLS_RESERVE_RATIO),
                )
                _allow_tokens = _spare - _reserve
            else:
                # Remote: no window to compute spare from — use the flat cap.
                _allow_tokens = _SKILLS_REMOTE_MAX_TOKENS
            if _allow_tokens > 0:
                try:
                    from . import skills as _skills_mod  # pylint: disable=import-error
                    _domain_skills_text = _skills_mod.get_domain_skills(
                        _domain_skill_domains,
                        max_chars=int(_allow_tokens * _CHARS_PER_TOKEN),
                    ) or ""
                except Exception:  # pylint: disable=broad-exception-caught
                    _domain_skills_text = ""
            if _domain_skills_text:
                _cand = dict(msgs[0])
                _cand["content"] = str(_cand.get("content") or "").rstrip() + "\n\n" + _domain_skills_text
                if prompt_budget > 0:
                    _probe = [_cand] + msgs[1:]
                    _fits = (
                        _estimate_messages_tokens(_probe)
                        + _estimate_tools_tokens(openai_tools)
                        <= prompt_budget
                    )
                else:
                    # Remote: allowance already caps the size; no hard window.
                    _fits = True
                if _fits:
                    msgs[0] = _cand
                else:
                    _domain_skills_text = ""  # Safety net; reserve should prevent this.

        # ── Inject a pending screenshot into the last user message ──
        # Done BEFORE budgeting so the image is counted against the window
        # (a pending screenshot used to be appended after the preflight and
        # was therefore completely unbudgeted — a direct route to a 400).
        # A copy is used so the stored history keeps its plain-text content.
        _pending_image: str | None = getattr(_agent_state, "_pending_image", None)
        if _pending_image and msgs and msgs[-1].get("role") == "user":
            _last = dict(msgs[-1])
            _existing = _last.get("content")
            if isinstance(_existing, str):
                _last["content"] = [
                    {"type": "image_url", "image_url": {"url": _pending_image}},
                    {"type": "text", "text": _existing},
                ]
            elif isinstance(_existing, list):
                _existing = list(_existing)
                _existing.insert(0, {"type": "image_url", "image_url": {"url": _pending_image}})
                _last["content"] = _existing
            msgs[-1] = _last
            _agent_state._pending_image = None  # Clear after use

        # ── Enforce the token budget, then preflight ──────────────────
        # The message-count cap above is a blunt instrument: a few large tool
        # results can still overflow a small local context window.  Trim
        # oldest-first, then run the last-check preflight (counts history +
        # tool schema) which re-trims or surfaces a friendly, actionable
        # error instead of a raw server 400.
        if prompt_budget > 0:
            _before = _estimate_messages_tokens(msgs)
            msgs = _fit_history_to_budget(msgs, prompt_budget)
            # Trimming can cut a tool-call exchange in half — repair again.
            msgs = _repair_tool_call_pairs(msgs)
            _after = _estimate_messages_tokens(msgs)
            if _after < _before:
                print("[🛠️Coworker] run_conversation_turn: trimmed prompt "
                      "{:d} -> {:d} tokens ({:d} messages)".format(
                          _before, _after, len(msgs)))
            msgs, _err = _prompt_preflight(msgs, openai_tools, prompt_budget)
            # Last-resort degradation: if even the trimmed/pinned turn does not
            # fit, drop the (optional) domain-skill reference and re-check.
            # Only when THAT still fails do we surface the friendly error, so a
            # turn is refused solely by genuinely unavoidable content.
            if _err and _domain_skills_text and msgs and msgs[0].get("role") == "system":
                _stripped = dict(msgs[0])
                _stripped["content"] = str(_stripped.get("content") or "").replace(
                    "\n\n" + _domain_skills_text, "")
                msgs[0] = _stripped
                print("[🛠️Coworker] run_conversation_turn: dropped domain skills to "
                      "fit the context window")
                msgs, _err = _prompt_preflight(msgs, openai_tools, prompt_budget)
            return msgs, _err
        return msgs, None

    iterations = 0
    # Mode-aware iteration budget: local models get more repair rounds.
    _max_iterations = (
        _LOCAL_MAX_TOOL_ITERATIONS if llm_port_local is not None
        else _REMOTE_MAX_TOOL_ITERATIONS
    )
    while iterations < _max_iterations:
        iterations += 1

        # Abort early if the user pressed Stop.
        if _stop_event.is_set():
            print("[🛠️Coworker] run_conversation_turn: aborted by user")
            _agent_state.is_thinking = False
            _agent_state.thinking_start_time = 0.0
            if on_status:
                on_status("Stopped")
            return history

        # ── Session memory compaction check (Tier 3 Phase 4) ───────────
        # Retire old turns once the estimated prompt approaches the safe
        # budget, keeping a structured memory block in the system prompt and
        # archiving what was retired.  Best-effort: never blocks the turn.
        try:
            _maybe_compact_session(
                history, openai_tools, prompt_budget, on_status=on_status,
                memory_writer=_memory_writer_factory(
                    llm_url, api_key, model, min(_requested_max_tokens, 1024)))
        except Exception as _compact_ex:  # pylint: disable=broad-exception-caught
            print("[🛠️Coworker] run_conversation_turn: session compaction skipped — {:s}".format(
                str(_compact_ex)))

        # ── Build the exact message payload for this POST ─────────────
        history_to_send, _preflight_err = _build_send_messages()
        if _preflight_err:
            _agent_state.is_thinking = False
            _agent_state.thinking_start_time = 0.0
            _agent_state.error = _preflight_err
            if on_status:
                on_status("Error: conversation too large for context window")
            return history

        # ── Cap the reply allowance to the space the prompt left ──────
        # The prompt now fits the window; give the model whatever is left for
        # the reply (never the whole configured max_tokens, which by default
        # equals the context size and would overflow the server).
        if ctx_size_used > 0:
            _prompt_tokens_est = (
                _estimate_messages_tokens(history_to_send)
                + _estimate_tools_tokens(openai_tools)
            )
            max_tokens = _cap_reply_tokens(ctx_size_used, _requested_max_tokens, _prompt_tokens_est)

        # ── Request-shape diagnostic ──────────────────────────────────
        # Log exactly what is about to be sent.  This is the single most
        # useful signal when a chat template rejects a request: it shows the
        # role sequence, whether any empty-content messages survived, and
        # which user prompt the model will actually answer.
        print("[🛠️Coworker] run_conversation_turn: request shape:")
        print(_describe_history_for_log(history_to_send))

        response = _llm_request(history_to_send, openai_tools, thinking_budget)

        # ── Context-overflow recovery: compact, rebuild, retry once ───
        # The server rejected the request as larger than its context window.
        # Force an aggressive compaction, rebuild the payload from the now
        # smaller history, and retry — instead of surfacing a raw HTTP 400
        # the user cannot act on.  Bounded to a single retry; if the reduced
        # window still does not fit, the preflight shows the friendly message.
        if response is None and getattr(_agent_state, "error_kind", "") == "context_overflow":
            print("[🛠️Coworker] run_conversation_turn: context overflow — forcing "
                  "compaction and retrying once")
            try:
                _force_compact_session(
                    history, openai_tools, prompt_budget, on_status=on_status,
                    memory_writer=_memory_writer_factory(
                        llm_url, api_key, model, min(_requested_max_tokens, 1024)))
            except Exception as _force_ex:  # pylint: disable=broad-exception-caught
                print("[🛠️Coworker] run_conversation_turn: forced compaction failed — "
                      "{:s}".format(str(_force_ex)))
            history_to_send, _preflight_err = _build_send_messages()
            if _preflight_err:
                _agent_state.is_thinking = False
                _agent_state.thinking_start_time = 0.0
                _agent_state.error = _preflight_err
                if on_status:
                    on_status("Error: conversation too large for context window")
                return history
            print("[🛠️Coworker] run_conversation_turn: retry request shape:")
            print(_describe_history_for_log(history_to_send))
            if ctx_size_used > 0:
                _prompt_tokens_est = (
                    _estimate_messages_tokens(history_to_send)
                    + _estimate_tools_tokens(openai_tools)
                )
                max_tokens = _cap_reply_tokens(ctx_size_used, _requested_max_tokens, _prompt_tokens_est)
            response = _llm_request(history_to_send, openai_tools, thinking_budget)

        # ── Abort check ───────────────────────────────────────────────
        # If the user stopped the turn, keep any partial streamed content
        # in the Workshop (marked) instead of discarding it (issue #69).
        # The partial is marked ``partial`` so a later turn never re-sends
        # it to the model as a complete answer.
        if _stop_event.is_set():
            _partial = ""
            if response is not None:
                _partial = (
                    response.get("choices", [{}])[0].get("message", {}).get("content") or ""
                )
            if _partial:
                print("[🛠️Coworker] run_conversation_turn: aborted — keeping "
                      "partial response ({:d} chars)".format(len(_partial)))
                history.append({
                    "role": "assistant",
                    "content": _partial,
                    "partial": True,
                })
            else:
                print("[🛠️Coworker] run_conversation_turn: aborted — nothing streamed yet")
            _agent_state.is_thinking = False
            _agent_state.thinking_start_time = 0.0
            if on_status:
                on_status("Stopped")
            return history

        if response is None:
            _agent_state.is_thinking = False
            _agent_state.thinking_start_time = 0.0
            # Keep the transport's specific reason (context overflow, server
            # fault, malformed tool call) in ``error`` so the chat panel and
            # the benchmark recorder see the real cause; the generic text is
            # only a last resort when the transport left no detail at all.
            _detail = _agent_state.error_full or _agent_state.error
            if _detail:
                _agent_state.error = _detail[:500]
                if on_status:
                    on_status("Error: {:s}".format(_agent_state.error))
            else:
                _agent_state.error = "No response from LLM"
                if on_status:
                    on_status("Error: No response from LLM")
            return history

        # A successful request clears any error the recovery path recorded
        # (e.g. a context-overflow 400 that was compacted away and retried).
        # Without this the stale message would be shown even though the turn
        # produced an answer.
        _agent_state.error = ""
        _agent_state.error_full = ""
        _agent_state.error_kind = ""

        # Safety: log the approximate body size for debugging.  When a budget
        # is active the prompt was already trimmed to fit, so this is purely
        # diagnostic (and only meaningful on the remote path, which has no
        # known context size to budget against).
        body_approx = len(json.dumps(history_to_send, default=str))
        if prompt_budget <= 0 and body_approx > 30000:
            print("[🛠️Coworker] run_conversation_turn: WARNING — history body is {:d} bytes, "
                  "may exceed model context window".format(body_approx))

        choice = response.get("choices", [{}])[0]
        msg = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")

        # Extract text content.
        content = msg.get("content") or ""
        # Empty response: thinking budget cut off reasoning before output.
        # Auto-retry with doubled budget (max 2 retries).
        empty_retries = 0
        doubled_budget = thinking_budget
        while not content and not msg.get("tool_calls") and empty_retries < 2:
            empty_retries += 1
            doubled_budget = min(doubled_budget * 2, 8192) if thinking_budget > 0 else 0
            print("[Coworker] empty response, retrying with thinking_budget={:d}".format(doubled_budget))
            response = _llm_request(history_to_send, openai_tools, doubled_budget)
            if response is None:
                break
            choice = response.get("choices", [{}])[0]
            msg = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")
            content = msg.get("content") or ""


        # ── Auto-continue on finish_reason=length ─────────────────────
        # Reasoning models (Qwen, DeepSeek, Gemma 4) can hit the token
        # limit mid-reasoning before emitting tool calls or text.
        # We detect this and ask the model to continue.
        continue_attempts = 0
        while finish_reason == "length" and continue_attempts < 2:
            continue_attempts += 1
            print("[🛠️Coworker] run_conversation_turn: finish_reason=length, "
                  "auto-continue attempt {:d}/2".format(continue_attempts))

            # Append partial assistant message to history so the model
            # can pick up where it left off.
            partial_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if msg.get("tool_calls"):
                partial_msg["tool_calls"] = msg["tool_calls"]
            history.append(partial_msg)

            # Send a brief continuation prompt.  It must also cap the size of
            # the next step: a bare "Continue." invites the model to dump the
            # whole remaining plan into one tool call, which then truncates
            # mid-string and fails to parse (HTTP 500).
            history.append({
                "role": "user",
                "content": (
                    "Continue. Keep this next step small — if there is a lot "
                    "left to do, do one short piece now and the rest in "
                    "follow-up calls."
                ),
            })

            # Re-request with the same max_tokens.
            # Send a *sanitized* copy, not the raw history: the live history
            # carries UI-only entries (the startup greeting) and non-standard
            # ``reasoning`` messages, and a strict Jinja template rejects both
            # with 400 "Unexpected message role".  The main request path
            # already strips them; this path must too.
            _cont_send = _strip_reasoning_from_history(history)
            _cont_send = _strip_ui_only_from_history(_cont_send)
            _cont_send = _sanitize_message_roles(_cont_send)
            # Budget the continuation request too: it re-sends the full
            # history (plus the appended partial + "Continue." turn), so it
            # can exceed the window even when the trimmed request before it
            # fit.  Re-trim and preflight; stop auto-continue if it still
            # cannot fit rather than clashing with the server's 400.
            if prompt_budget > 0:
                _cont_send = _fit_history_to_budget(_cont_send, prompt_budget)
                _cont_send = _repair_tool_call_pairs(_cont_send)
                _cont_send, _cont_err = _prompt_preflight(
                    _cont_send, openai_tools, prompt_budget)
                if _cont_err:
                    print("[🛠️Coworker] run_conversation_turn: continuation cannot fit "
                          "the context window — stopping auto-continue")
                    break
            # Forward the thinking budget.  Without it the continuation can
            # spend the entire max_tokens on chain-of-thought and leave
            # nothing for the tool call, which then truncates mid-string and
            # fails to parse (HTTP 500) -- the exact failure this path exists
            # to recover from.
            continue_response = _llm_request(_cont_send, openai_tools, thinking_budget)
            if continue_response is None:
                break

            # Pop the "Continue." user message so it doesn't pollute history.
            history.pop()
            # Pop the partial assistant message — we'll replace it with the
            # concatenated version.
            history.pop()

            # Merge results: concatenate content, merge tool_calls.
            cont_choice = continue_response.get("choices", [{}])[0]
            cont_msg = cont_choice.get("message", {})
            cont_content = cont_msg.get("content") or ""
            cont_tool_calls = cont_msg.get("tool_calls") or []

            content = content + cont_content
            if cont_tool_calls:
                # Merge tool calls from continuation, deduplicating by ID.
                existing = msg.get("tool_calls") or []
                seen_ids = {tc.get("id") for tc in existing if tc.get("id")}
                for tc in cont_tool_calls:
                    if tc.get("id") not in seen_ids:
                        existing.append(tc)
                        seen_ids.add(tc.get("id"))
                msg["tool_calls"] = existing
            msg["content"] = content
            finish_reason = cont_choice.get("finish_reason", "")
            print("[🛠️Coworker] run_conversation_turn:   after continue: "
                  "finish_reason={:s}, content_len={:d}, tool_calls={:d}".format(
                      finish_reason, len(content), len(msg.get("tool_calls") or [])))

        # ── End auto-continue ─────────────────────────────────────────

        # Deliver reasoning (chain-of-thought) to UI if present.
        # Different providers use different field names:
        #   - Local llama-server / DeepSeek: "reasoning_content"
        #   - OpenRouter: "reasoning"
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        # Strip <think> wrapper tags for display/storage.
        reasoning = _strip_think_tags(reasoning)
        if reasoning:
            print("[🛠️Coworker] run_conversation_turn: reasoning ({:d} chars) — storing in history".format(
                len(reasoning)))
            _agent_state.reasoning_text = reasoning
            # Pick a random thinking label that sticks for this reasoning block.
            import random as _random
            _thinking_labels = [
                "Considering", "Expanding", "Scheming", "Working",
                "Adjusting", "Thinking", "Planning", "Figuring",
                "Reasoning", "Pondering",
            ]
            label = _random.choice(_thinking_labels)
            history.append({"role": "reasoning", "content": reasoning, "label": label})
            if on_reasoning:
                on_reasoning(reasoning)

            _agent_state.last_llm_activity = time.monotonic()

        if content:
            if on_text:
                on_text(content)
            _agent_state.streaming_text = content

        # Check for tool calls.
        raw_tool_calls = msg.get("tool_calls")

        # ── Ask-mode hard guard (issue #66) ───────────────────────────
        # Ask mode must be informational only.  Even though no tools are
        # offered to the model, defensive layers above (text/XML fallback
        # parsing) or the provider itself could still surface tool calls.
        # Never execute them: keep the prose answer and drop the calls.
        if chat_mode == "ASK" and raw_tool_calls:
            print("[🛠️Coworker] run_conversation_turn: ASK mode — suppressed "
                  "{:d} tool call(s) from LLM response".format(len(raw_tool_calls)))
            msg["tool_calls"] = []
            raw_tool_calls = None
            finish_reason = "stop"

        # Process tool calls if present.
        if raw_tool_calls and finish_reason == "tool_calls":
            # Add assistant message with tool calls to history.
            history.append({"role": "assistant", "content": content, "tool_calls": raw_tool_calls})

            # Process each tool call.
            for tc in raw_tool_calls:
                if _stop_event.is_set():
                    print("[🛠️Coworker] run_conversation_turn: aborted during tool calls")
                    _agent_state.is_thinking = False
                    _agent_state.thinking_start_time = 0.0
                    if on_status:
                        on_status("Stopped")
                    return history

                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}

                tool_name = fn.get("name", "")
                tool_id = tc.get("id", "")

                # ── load_tools meta-tool (on-demand domain loading) ────
                # Intercepted here — not sent to the MCP server.  Handled in
                # every mode (the tool is offered locally AND to remote
                # providers), so the gate is not local-only.
                if tool_name == "load_tools":
                    domain = args.get("domain", "")
                    if domain in _TOOL_DOMAINS and domain not in _loaded_domains:
                        _loaded_domains.add(domain)
                        _session_loaded_domains.add(domain)
                        # Rebuild with all loaded domains.
                        _combined = set(_SURFACE_TOOLS)
                        for d in _loaded_domains:
                            _combined.update(_TOOL_DOMAINS.get(d, set()))
                        openai_tools = [
                            t for t in _all_tools
                            if t.get("function", {}).get("name") in _combined
                        ]
                        openai_tools.append(_LOAD_TOOLS_SCHEMA)
                        openai_tools.sort(
                            key=lambda t: t.get("function", {}).get("name", ""))
                        print("[🛠️Coworker] run_conversation_turn: load_tools '{:s}' — now {:d} tools".format(
                            domain, len(openai_tools)))
                        history.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "name": "load_tools",
                            "content": "Loaded {:s} tools. {:d} tools now available.".format(
                                domain, len(openai_tools)),
                        })
                    else:
                        history.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "name": "load_tools",
                            "content": "Domain '{:s}' already loaded or unknown.".format(domain),
                        })
                    continue  # Skip MCP call — handled locally.

                if on_status:
                    on_status(_friendly_tool_status(tool_name))

                # ── Smart undo: auto-undo before re-executing code ─────
                # If this is execute_blender_code and the previous call
                # errored, undo to clean up partial effects before retrying.
                # On successful overlap (same operations detected), inject
                # context so the LLM knows what already exists.
                #
                # Skip undo for pure code-bug errors (KeyError, AttributeError,
                # TypeError, NameError, ValueError) — these fail before creating
                # any objects, so there's nothing to undo.  Undoing wastes 2
                # round-trips and can trigger depsgraph crashes.
                if tool_name == "execute_blender_code" and _prev_code is not None:
                    should_undo = False
                    reason = ""
                    if _prev_code_errored:
                        if _error_is_code_bug(_prev_code_error):
                            print("[🛠️Coworker] run_conversation_turn: smart undo SKIPPED — code-bug error, no side effects")
                        else:
                            should_undo = True
                            reason = "previous call errored"
                    elif _codes_overlap(_prev_code, args.get("code", "")):
                        pass  # Context injected after tool result.
                    if should_undo:
                        print("[🛠️Coworker] run_conversation_turn: smart undo triggered — {:s}".format(reason))
                        # Undo to the state before the previous execute_blender_code.
                        # Must use context override — bpy.ops.ed.undo() needs a window context
                        # which isn't available in the bridge server's exec() namespace.
                        _undo_result = _call_mcp_tool_sync("execute_blender_code",
                            {"code": _undo_code("undo")}, mcp_port)
                        # Check if undo actually succeeded — if not, fall back to
                        # retroactive entity cleanup using the snapshot diff.
                        if '"status": "error"' in _undo_result:
                            print("[🛠️Coworker] run_conversation_turn: undo FAILED — falling back to entity cleanup")
                            _cleanup_code = _build_cleanup_code(_turn_entities)
                            if _cleanup_code:
                                _call_mcp_tool_sync("execute_blender_code",
                                    {"code": _cleanup_code}, mcp_port)
                        # Push a fresh undo state so the next iteration can undo this one.
                        _call_mcp_tool_sync("execute_blender_code",
                            {"code": _undo_code("push", "bfa_coworker_pre_script")},
                            mcp_port)

                # ── Push initial undo state + initial snapshot (merged) ─
                # Merging saves 1 round-trip at the start of each turn.
                # Skip entity snapshot for read-only code (no scene mutations).
                if tool_name == "execute_blender_code" and not _undo_pushed:
                    _init_extra = _SNAPSHOT_EXTRA if not _code_is_readonly(args.get("code", "") or "") else ""
                    merged_init_raw = _call_mcp_tool_sync("execute_blender_code",
                        {"code": _undo_code("push", "bfa_coworker_pre_script", extra_result=_init_extra)},
                        mcp_port)
                    _undo_pushed = True
                    # Parse initial snapshot from merged result.
                    try:
                        init_data = json.loads(merged_init_raw)
                        if init_data.get("status") == "ok":
                            snap_data = init_data.get("result", {}).get("snapshot")
                            if snap_data:
                                _turn_snapshot = _EntitySnapshot.from_dict(snap_data)
                                print("[🛠️Coworker] run_conversation_turn: initial entity snapshot taken")
                    except (json.JSONDecodeError, TypeError):
                        pass
                    if _turn_snapshot is None:
                        print("[🛠️Coworker] run_conversation_turn: initial entity snapshot FAILED — "
                              "continuing without the co-work scene lock for this turn")

                # ── Inject resolution from preferences ─────────────
                if tool_name in ("download_polyhaven_asset", "setup_pbr_material"):
                    try:
                        _prefs = bpy.context.preferences.addons[__package__].preferences
                        if "resolution" not in args or not args.get("resolution"):
                            args["resolution"] = _prefs.polyhaven_resolution
                        # Also inject polyhaven_resolution for setup_pbr_material.
                        if tool_name == "setup_pbr_material" and "polyhaven_resolution" not in args:
                            args["polyhaven_resolution"] = _prefs.polyhaven_resolution
                    except Exception:
                        pass  # Best-effort; don't break the tool call.

                # Call the MCP tool.
                result_text = _call_mcp_tool_sync(tool_name, args, mcp_port)

                # ── Track code execution for smart undo ────────────────
                if tool_name == "execute_blender_code":
                    _prev_code = args.get("code", "")
                    _prev_code_errored = '"status": "error"' in result_text
                    _prev_code_error = result_text if _prev_code_errored else ""

                    # ── Push bookmark + entity snapshot (merged) ───────
                    # Merging these into a single execute_blender_code call
                    # saves 2 round-trips per iteration vs separate calls.
                    # Skip entity snapshot for read-only code (no scene mutations).
                    if not _prev_code_errored:
                        _step_extra = _SNAPSHOT_EXTRA if not _code_is_readonly(_prev_code or "") else ""
                        merged_raw = _call_mcp_tool_sync("execute_blender_code",
                            {"code": _undo_code("push", "bfa_coworker_step", extra_result=_step_extra)},
                            mcp_port)
                        # Parse snapshot from merged result.
                        try:
                            merged_data = json.loads(merged_raw)
                            if merged_data.get("status") == "ok":
                                snap_data = merged_data.get("result", {}).get("snapshot")
                                if snap_data and _turn_snapshot is not None:
                                    current_snap = _EntitySnapshot.from_dict(snap_data)
                                    step_diff = _diff_snapshots(_turn_snapshot, current_snap)
                                    if not step_diff.is_empty():
                                        _turn_entities.merge(step_diff)
                                        _turn_snapshot = current_snap
                                        # Entity context is now injected AFTER the tool result.
                                        # Co-work soft lock (Phase 1): make this
                                        # step's created/touched datablocks
                                        # un-selectable for the rest of the
                                        # turn so the user cannot re-target
                                        # them mid-turn.
                                        _lock_step_entities(step_diff, mcp_port)
                        except (json.JSONDecodeError, TypeError):
                            pass

                    # ── Save to text editor memory bank ────────────────
                    seq = _next_code_sequence()
                    if _prev_code_errored:
                        # Save error-producing code with error prefix.
                        _save_code_to_text_editor_deferred(
                            "# ERROR: Tool call returned an error\n# Original code:\n" + _prev_code,
                            seq,
                        )
                    else:
                        _save_code_to_text_editor_deferred(_prev_code, seq)

                # Build a human-readable summary for the UI.
                result_summary = _tool_result_summary(result_text)

                # Collapse known-noisy errors before storage (Tier 3 Phase 6,
                # scene safety Phase 4): a poll() failure becomes an
                # operator-specific corrective hint and an IndexError becomes
                # a guard-your-collection hint, both in the stored history and
                # for the model; every other error keeps the full text.
                result_text = _collapse_known_errors(result_text)

                # Store the FULL tool result in history.  Trimming happens
                # when the request is built, not here: the chat panel renders
                # from conversation_history, so truncating at this point made
                # the user see a 500-char stub instead of the real output.
                # The model still gets a trimmed version (see
                # _trim_history_tool_results), so context bloat is unchanged.
                _stored_result = result_text
                if len(_stored_result) > _MAX_STORED_TOOL_RESULT_CHARS:
                    _stored_result = (
                        _stored_result[:_MAX_STORED_TOOL_RESULT_CHARS]
                        + "\n[truncated {:d} chars]".format(
                            len(result_text) - _MAX_STORED_TOOL_RESULT_CHARS))
                history.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": tool_name,
                    "content": _stored_result,
                    "summary": result_summary,
                })

                # Inject entity context AFTER tool result so the model
                # sees the successful result first, then gets context.
                if tool_name == "execute_blender_code" and not _entity_context_injected:
                    ctx = _entity_diff_to_context_message(_turn_entities)
                    if ctx:
                        print("[\U0001f6e0\ufe0fCoworker] run_conversation_turn: entity context injected \u2014 {:s}".format(
                            _turn_entities.summary()))
                        history.append({"role": "user", "content": ctx})
                        _entity_context_injected = True

                # ── Extract screenshot image for vision-capable models ─
                # If the tool result contains an image (screenshot), store
                # it on the agent state so it can be injected into the next
                # user message as an image_url content block.
                if tool_name in ("get_screenshot_of_area_as_image", "get_screenshot_of_window_as_image"):
                    try:
                        result_obj = json.loads(result_text)
                        img_data = _extract_image_from_tool_result(result_obj)
                        if img_data:
                            _agent_state._pending_image = img_data
                            print("[🛠️Coworker] run_conversation_turn: screenshot image captured for vision model")
                    except (json.JSONDecodeError, TypeError):
                        pass

                # ── Spiral detection: break repeated error loops ──────
                if tool_name == "execute_blender_code":
                    # Use the trimmed form for signature extraction: the
                    # signature only needs the error line, and the full result
                    # may be very large.
                    _sig_text = _trim_tool_result(result_text, max_chars=_MAX_TOOL_RESULT_CHARS)
                    error_sig = _extract_error_signature(_sig_text)
                    # Also detect 'no output' - the agent called code but got
                    # nothing back (empty result or only whitespace).
                    _result_stripped = _sig_text.strip().strip('{').strip('}').strip().strip('"')
                    if not error_sig and not _result_stripped:
                        error_sig = '(no output from execute_blender_code)'
                    if error_sig:
                        if _consecutive_errors and error_sig != _consecutive_errors[-1]:
                            _consecutive_errors.clear()
                        _consecutive_errors.append(error_sig)
                        if len(_consecutive_errors) >= 2:
                            print("[\U0001f6e0\ufe0fCoworker] run_conversation_turn: spiral detected \u2014 "
                                  "same error {:d}\u00d7 in a row: {:s}".format(
                                      len(_consecutive_errors), error_sig))
                            # Auto-save session log on spiral detection.
                            try:
                                export_session_log(auto_saved=True)
                            except Exception:  # pylint: disable=broad-exception-caught
                                pass
                            # Truncate: remove the last N assistant+tool message pairs.
                            removed = 0
                            for i in range(len(history) - 1, -1, -1):
                                if removed >= len(_consecutive_errors):
                                    break
                                if history[i].get("role") == "assistant" and history[i].get("tool_calls"):
                                    del history[i:]
                                    removed += 1
                            print("[\U0001f6e0\ufe0fCoworker] run_conversation_turn: truncated {:d} failed attempt(s) from history".format(removed))
                            corrective = _spiral_corrective_message(error_sig)
                            history.append({"role": "user", "content": corrective})
                            _consecutive_errors.clear()
                    else:
                        _consecutive_errors.clear()

            # After processing tool calls, inject a user prompt so the LLM
            # generates a text response instead of another tool call loop.
            # Many local models (Qwen, Fable Fusion, etc.) emit tool calls
            # inside <think> blocks with empty content.  Without a user message
            # after tool results, llama-server's Jinja template may fail with
            # "No user query found in messages."
            if not content or not content.strip():
                history.append({
                    "role": "user",
                    "content": (
                        "The tool results are above. "
                        "Please provide a helpful response to the user based on these results."
                    ),
                })
            continue

        # No more tool calls — add the final assistant message and we're done.
        history.append({"role": "assistant", "content": content})
        break

    # If we hit the iteration limit, the LLM kept calling tools.
    # Add an explicit instruction to summarize and make one final call.
    if iterations >= _max_iterations:
        print("[🛠️Coworker] run_conversation_turn: hit max iterations, forcing summary")
        history.append({
            "role": "user",
            "content": "[System: All tool calls are complete. Please summarize what was done in 1-2 sentences.]",
        })
        # Budget the forced-summary request too: it sends the full history.
        _summary_send: list[dict[str, Any]] | None = history
        if prompt_budget > 0:
            _summary_send = _fit_history_to_budget(history, prompt_budget)
            _summary_send = _repair_tool_call_pairs(_summary_send)
            _summary_send, _sum_err = _prompt_preflight(
                _summary_send, openai_tools, prompt_budget)
            if _sum_err:
                print("[🛠️Coworker] run_conversation_turn: forced summary cannot fit "
                      "the context window — skipping")
                _summary_send = None
        final_response = (
            _llm_request(_summary_send, openai_tools, thinking_budget)
            if _summary_send is not None else None
        )
        if final_response:
            final_choice = final_response.get("choices", [{}])[0]
            final_msg = final_choice.get("message", {})
            final_content = final_msg.get("content") or ""
            if final_content:
                if on_text:
                    on_text(final_content)
                _agent_state.streaming_text = final_content
                history.append({"role": "assistant", "content": final_content})

    _agent_state.is_thinking = False
    _agent_state.thinking_start_time = 0.0
    if on_status:
        on_status("Idle")
    return history


# ---------------------------------------------------------------------------
# Cleanup

def cleanup() -> None:
    """Stop the MCP server subprocess. Safe to call multiple times."""
    stop_mcp_server()
    # Forget the co-work lock registry.  We cannot run the unlock toolcode
    # here (this runs on the main thread during shutdown/disable), but the
    # hide_select flags are scene state that resets on file reload, and the
    # registry is only an in-memory bookkeeping aid.
    co_work_guard.clear()
    _agent_state.conversation_history.clear()
    _agent_state.streaming_text = ""
    _agent_state.reasoning_text = ""
    _agent_state.thinking_dots = 0
    _agent_state.is_thinking = False
    _agent_state.thinking_start_time = 0.0


# ---------------------------------------------------------------------------
# Connectivity diagnostics

def ping_agent(
    mcp_port: int = _MCP_SERVER_DEFAULT_PORT,
    llm_port: int = 8081,
    bridge_port: int = 9876,
    operating_mode: str = "",
    check_harness_config: bool = False,
    use_blender_python: bool = True,
) -> dict[str, Any]:
    """
    Quick connectivity check for all three back-ends.

    When *operating_mode* is ``"EXTERNAL_HARNESS"``, only the bridge
    server is checked — MCP and LLM probes are skipped because those
    services are managed externally.  Pass *check_harness_config* to also
    preflight the generated MCP client config, so "Check Status" reports
    whether the config the user copied can actually start the server.

    Returns a dict with test results suitable for display in the UI::

        {
            "bridge_server":   "OK" | "FAIL: <reason>",
            "mcp_server":      "OK (N tools)" | "FAIL: <reason>" | "N/A (harness mode)",
            "llm_health":      "OK" | "FAIL: <reason>" | "N/A (harness mode)",
            "llm_chat":        "OK" | "FAIL: <reason>" | "N/A (harness mode)",
            "harness_config":  "OK" | "FAIL: <reason>" | "N/A",
            "all_ok":          True | False,
        }
    """
    is_harness = (operating_mode == "EXTERNAL_HARNESS")
    result: dict[str, Any] = {}

    # 1 — Bridge server (raw TCP inside Blender)
    import socket as _socket_mod
    try:
        s = _socket_mod.socket(_socket_mod.AF_INET, _socket_mod.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", bridge_port))
        s.close()
        result["bridge_server"] = "OK"
    except Exception as ex:
        result["bridge_server"] = "FAIL: {:s}".format(str(ex))

    # In harness mode, skip MCP and LLM probes — they're external.
    if is_harness:
        result["mcp_server"] = "N/A (harness mode)"
        result["llm_health"] = "N/A (harness mode)"
        result["llm_chat"] = "N/A (harness mode)"

        # Preflight the config the user is expected to paste into their client.
        if check_harness_config:
            try:
                check = validate_mcp_client_config(
                    blender_host="localhost",
                    blender_port=bridge_port,
                    use_blender_python=use_blender_python,
                    check_bridge=False,
                )
                if check.get("ok"):
                    result["harness_config"] = "OK"
                else:
                    result["harness_config"] = "FAIL: {:s}".format(
                        check.get("summary") or "invalid")
            except Exception as ex:  # pylint: disable=broad-exception-caught
                result["harness_config"] = "FAIL: {:s}".format(str(ex))
        else:
            result["harness_config"] = "N/A"

        result["all_ok"] = all(
            v.startswith("OK") or v.startswith("N/A")
            for k, v in result.items() if k != "all_ok"
        )
        return result

    # 2 — LLM health
    try:
        url = "http://127.0.0.1:{:d}/health".format(llm_port)
        with urllib.request.urlopen(url, timeout=5) as resp:
            result["llm_health"] = "OK" if resp.status == 200 else "FAIL: HTTP {:d}".format(resp.status)
    except Exception as ex:
        result["llm_health"] = "FAIL: {:s}".format(str(ex))

    # 3 — LLM chat (simple echo)
    try:
        url = _LLM_CHAT_URL.format(llm_port)
        body = {
            "messages": [{"role": "user", "content": "Say ping."}],
            "stream": False,
            "max_tokens": 32,
        }
        data_bytes = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            data = json.loads(raw)
            choice = data.get("choices", [{}])[0]
            reply = choice.get("message", {}).get("content", "")
            result["llm_chat"] = "OK ({:s})".format(reply[:80] if reply else "(empty)")
    except Exception as ex:
        result["llm_chat"] = "FAIL: {:s}".format(str(ex))

    # 4 — MCP server (verify with a real tools/list RPC;
    # FastMCP streamable-HTTP does NOT expose /health.)
    try:
        tools = _list_tools_sync(mcp_port, operating_mode)
        if tools:
            result["mcp_server"] = "OK ({:d} tools)".format(len(tools))
        else:
            result["mcp_server"] = "FAIL: no tools returned"
    except Exception as ex:
        result["mcp_server"] = "FAIL: {:s}".format(str(ex))

    result["all_ok"] = all(
        v.startswith("OK") for k, v in result.items() if k != "all_ok"
    )
    return result


def warmup_agent(
    on_status: Callable[[str], None] | None = None,
    on_text: Callable[[str], None] | None = None,
    mcp_port: int = _MCP_SERVER_DEFAULT_PORT,
) -> None:
    """
    Warm up the agent: load MCP tools and post a welcome message.

    This does a lightweight tool-list fetch (so ``tool_count`` is populated
    and the UI shows the agent is ready) and posts a friendly welcome
    message into the conversation history. It does NOT invoke the LLM —
    that's deferred until the user's first real message.

    Call this after the LLM backend is confirmed running but before the
    user sends their first message.
    """
    # 1. Warm up tools (populate tool_count for UI).
    if on_status:
        on_status("Warming up tools...")
    try:
        tools = _list_tools_sync(mcp_port)
        if tools:
            _agent_state.tool_count = len(tools)
            print("[🛠️Coworker] warmup_agent: {:d} tools loaded".format(len(tools)))
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[🛠️Coworker] warmup_agent: tool warmup failed — {:s}".format(str(ex)))

    # 1.5 In local mode, only post the welcome once the LLM backend is
    #     actually healthy.  Posting it unconditionally right after Popen
    #     is a lie — a mid-range model takes 30-120s to load, and a crashed
    #     llama-server would otherwise still get a "we're ready!" message
    #     (the "welcome message happens, then closes" symptom).
    try:
        from . import llm_manager as _llm_mgr
        if _llm_mgr.get_config().mode == "local" and not _llm_mgr.health_check():
            _tail = _llm_mgr.get_llama_server_log_tail()
            _detail = "\n\n--- llama-server.log (tail) ---\n{:s}".format(_tail) if _tail else ""
            _msg = (
                "LLM backend is not ready yet — wait for the model to load, "
                "or check the llama-server log (last lines above).{:s}".format(_detail)
            )
            _agent_state.error = _msg
            if on_status:
                on_status("Error: LLM backend not ready")
            print("[🛠️Coworker] warmup_agent: LLM backend not ready — welcome suppressed")
            return
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[🛠️Coworker] warmup_agent: health pre-check failed — {:s}".format(str(ex)))

    # 2. Post the welcome message.
    # It is a greeting, not a model turn, so it is marked ``ui_only``: the
    # chat panel renders it, but ``_strip_ui_only_from_history`` removes it
    # before every LLM request.  Appending it as a plain assistant message
    # put a phantom assistant turn ahead of the system prompt, which made the
    # model answer the greeting instead of the user's actual request.
    welcome = "Ok, now we are ready! How can I help?"
    _agent_state.conversation_history.append({
        "role": "assistant",
        "content": welcome,
        "ui_only": True,
    })
    if on_text:
        on_text(welcome)
    if on_status:
        on_status("Ready")

    print("[🛠️Coworker] warmup_agent: welcome message posted (UI only)")


# ---------------------------------------------------------------------------
# Module-level: migrate vendor/deps/ out of the addon tree immediately.
# Blender 5.3+ sandbox scans the addon directory tree at load time and
# flags any subdirectory matching a known top-level Python package
# (rich/, click/, httpx/, etc.) as a policy violation — even if never
# imported.  We move vendor/deps/ to ~/.cache/bfa_coworker/vendor_deps/
# at module import time so the scan never sees the package directories.
if (Path(__file__).resolve().parent / "vendor" / "deps").is_dir():
    _get_vendor_deps_dir()