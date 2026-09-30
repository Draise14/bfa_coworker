# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Session memory & context checkpoints (Tier 3).

When the conversation approaches the local context window, the oldest
verbatim turns are "retired": they leave the live history, are appended to
a disk archive (``archive.jsonl`` next to the chat-history JSON), and are
summarized into a compact structured *memory block* that is injected into
the system prompt so the agent remembers goals, decisions, and pending
work across compactions.

Checkpoints are automatic snapshots of the session (memory block + history
hash + message count) taken at each compaction and at session start.
Restoring or branching from a checkpoint is always a user action and never
destroys the current state — restoring first snapshots the current session.

This module is deliberately free of ``bpy`` so it can be unit-tested
outside Blender.
"""

__all__ = (
    "MAX_WINDOW_TURNS",
    "MEMORY_TARGET_TOKENS",
    "COMPACTION_TRIGGER_RATIO",
    "CheckpointStore",
    "store",
    "build_memory_block",
    "heuristic_memory_block",
    "find_retire_boundary",
    "compact_history",
    "estimate_history_tokens",
    "memory_writer_prompt",
)

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

# Bounded verbatim window: the number of recent *messages* (beyond the
# system prompt) kept verbatim before older turns are retired.  This mirrors
# the request-side slice in agent_controller and bounds how much history a
# single compaction has to summarize.
MAX_WINDOW_TURNS = 20

# Target size (in tokens) of the generated memory block.  The memory-writer
# prompt asks the LLM to stay under this; the heuristic fallback respects it
# by truncating bullet text.
MEMORY_TARGET_TOKENS = 600

# Compaction triggers when the estimated prompt reaches this fraction of the
# safe prompt budget (see agent_controller._compute_prompt_budget).
COMPACTION_TRIGGER_RATIO = 0.6

# Approximate characters per token — must match agent_controller._CHARS_PER_TOKEN.
_CHARS_PER_TOKEN = 3.5

_MAX_MEMORY_CHARS = int(MEMORY_TARGET_TOKENS * _CHARS_PER_TOKEN)
_MAX_BULLET_CHARS = 160
_MAX_RETIRE_TEXT_CHARS = 6000


# ---------------------------------------------------------------------------
# Memory block


def heuristic_memory_block(retired_turns: list[dict[str, Any]]) -> str:
    """Build a memory block from *retired_turns* without an LLM.

    Fallback used when the memory-writer LLM call fails or is unavailable.
    Extracts the goal (first user message), a few user/assistant bullets,
    and error lines seen in tool results.  Output is capped so the block
    stays well under :data:`MEMORY_TARGET_TOKENS`.
    """
    goal = ""
    bullets: list[str] = []
    errors: list[str] = []
    objects: list[str] = []

    for m in retired_turns:
        role = m.get("role")
        if m.get("ui_only"):
            continue
        text = m.get("content")
        if isinstance(text, list):
            text = " ".join(
                str(b.get("text", "")) for b in text if isinstance(b, dict))
        text = str(text or "").strip()
        if not text:
            continue
        if role == "user" and not goal:
            goal = text
        elif role in ("user", "assistant") and len(bullets) < 8:
            bullets.append(("{:s}: {:s}".format(role, text[:_MAX_BULLET_CHARS])).strip())
        elif role == "tool" and ("error" in text.lower() or "failed" in text.lower()):
            if len(errors) < 4:
                errors.append(text[:_MAX_BULLET_CHARS])
        for token in text.replace("\"", " ").split():
            if token.lower().endswith(".blend") and token not in objects:
                objects.append(token[:_MAX_BULLET_CHARS])

    lines = ["[Session memory]"]
    if goal:
        lines.append("Goal: {:s}".format(goal[:_MAX_BULLET_CHARS]))
    if bullets:
        lines.append("Recent context:")
        lines.extend("- {:s}".format(b) for b in bullets)
    if objects:
        lines.append("Objects & files touched:")
        lines.extend("- {:s}".format(o) for o in objects[:6])
    if errors:
        lines.append("Errors seen:")
        lines.extend("- {:s}".format(e) for e in errors)
    return "\n".join(lines)[:_MAX_MEMORY_CHARS]


def build_memory_block(
    retired_turns: list[dict[str, Any]],
    prior_memory: str = "",
    summary: str | None = None,
    updated_turn: int = 0,
) -> str:
    """Produce the structured memory block injected into the system prompt.

    *summary* is the LLM-written memory text from the dedicated compaction
    turn (when available); without it a heuristic summary is built from the
    retired turns.  The result is the full replacement block — prior memory
    content is expected to be folded into *summary* by the writer prompt,
    or is preserved verbatim here when no summary was produced.
    """
    if summary:
        block = summary.strip()
    else:
        block = heuristic_memory_block(retired_turns)
        if prior_memory.strip():
            # Heuristic path: carry the prior block forward so nothing is lost.
            block = prior_memory.strip() + "\n" + block
    block = block[:_MAX_MEMORY_CHARS]
    if "Last updated" not in block:
        block = "{:s}\nLast updated: turn {:d}".format(block, updated_turn)
    return block


def memory_writer_prompt(retired_text: str, prior_memory: str) -> list[dict[str, str]]:
    """Build the messages for the dedicated memory-writer LLM call."""
    sys_text = (
        "You maintain a compact session-memory note for a Blender AI agent. "
        "Rewrite the note so it captures the goal, decisions made, objects & "
        "files touched, pending work, and errors seen — using ONLY the "
        "conversation below plus the previous note. Keep the exact section "
        "headings: Goal / Decisions / Objects & files touched / Pending / "
        "Errors seen. Be terse (bullet points, under {:d} tokens total). "
        "Output only the note text."
    ).format(MEMORY_TARGET_TOKENS)
    prior = prior_memory.strip() or "(none yet)"
    convo = retired_text[:_MAX_RETIRE_TEXT_CHARS]
    return [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": (
            "Previous note:\n{:s}\n\nConversation to fold in:\n{:s}".format(prior, convo)
        )},
    ]


# ---------------------------------------------------------------------------
# Compaction


def estimate_history_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate the token size of *messages* (same rule as agent_controller)."""
    def _text_len(m: dict[str, Any]) -> int:
        content = m.get("content")
        if isinstance(content, str):
            total = len(content)
        elif isinstance(content, list):
            total = 0
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block.get("text", "")))
                    image_url = block.get("image_url")
                    if isinstance(image_url, dict):
                        total += len(str(image_url.get("url", "")))
                else:
                    total += len(str(block))
        else:
            total = len(str(content or ""))
        for call in m.get("tool_calls") or []:
            if isinstance(call, dict):
                fn = call.get("function", {})
                total += len(str(fn.get("name", "")))
                total += len(str(fn.get("arguments", "")))
        return total

    return sum(int(_text_len(m) / _CHARS_PER_TOKEN) + 1 for m in messages)


def find_retire_boundary(messages: list[dict[str, Any]], keep_recent: int = MAX_WINDOW_TURNS) -> int:
    """Return the index where older messages may be retired up to.

    Keeps the system prompt (index 0) and the most recent *keep_recent*
    messages verbatim.  The boundary is pushed forward to the next ``user``
    message so a tool-call exchange is never cut in half; returns the length
    of *messages* when there is nothing safe to retire.
    """
    n = len(messages)
    if n == 0:
        return 0
    start = 1 if messages[0].get("role") == "system" else 0
    boundary = max(start, n - keep_recent)
    if boundary <= start:
        return n
    # Advance to the next user message so no orphaned tool result remains.
    while boundary < n and messages[boundary].get("role") != "user":
        boundary += 1
    return boundary if boundary < n else n


def compact_history(
    history: list[dict[str, Any]],
    prior_memory: str = "",
    memory_writer: Callable[[str, str], str | None] | None = None,
    keep_recent: int = MAX_WINDOW_TURNS,
    updated_turn: int = 0,
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    """Retire old turns from *history* and produce a fresh memory block.

    Returns ``(new_history, memory_block, retired)``.  *memory_writer* (when
    given) receives the retired conversation text plus the prior memory and
    returns the LLM-written note (or ``None`` on failure — the heuristic
    fallback is used then).  The caller is responsible for archiving
    *retired* and snapshotting a checkpoint afterwards.
    """
    boundary = find_retire_boundary(history, keep_recent)
    retired = [m for m in history[:boundary] if not m.get("ui_only")]
    kept = history[boundary:]
    if history and history[0].get("role") == "system" and (not kept or kept[0] is not history[0]):
        kept = [history[0]] + kept

    summary: str | None = None
    if memory_writer is not None and retired:
        def _msg_text(m: dict[str, Any]) -> str:
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(
                    str(b.get("text", "")) for b in content if isinstance(b, dict))
            return "{:s}: {:s}".format(m.get("role", "?"), str(content or ""))

        retired_text = "\n".join(_msg_text(m) for m in retired)
        try:
            summary = memory_writer(retired_text, prior_memory)
        except Exception:  # pylint: disable=broad-exception-caught
            summary = None

    memory_block = build_memory_block(
        retired, prior_memory=prior_memory, summary=summary, updated_turn=updated_turn)
    return kept, memory_block, retired


# ---------------------------------------------------------------------------
# Checkpoints


def _window_hash(history: list[dict[str, Any]]) -> str:
    """Stable hash of the current verbatim window (for checkpoint identity)."""
    try:
        blob = json.dumps(history, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = ""
    return hashlib.sha1(blob.encode("utf-8", errors="replace")).hexdigest()[:12]


class CheckpointStore:
    """Automatic session checkpoints + disk archive for retired turns.

    Checkpoints are created automatically (at compaction and session start);
    ``restore``/``branch`` are the user actions.  Restoring snapshots the
    current state first, so it is non-destructive.
    """

    MAX_CHECKPOINTS = 10

    def __init__(self) -> None:
        self.checkpoints: list[dict[str, Any]] = []
        self.archive_path: Path | None = None
        self.memory_block: str = ""
        self.memory_updated_turn: int = 0

    # -- checkpoints -------------------------------------------------------

    def snapshot(
        self,
        history: list[dict[str, Any]],
        reason: str,
        turn_index: int = 0,
    ) -> dict[str, Any]:
        """Record an automatic checkpoint of the current session state."""
        record = {
            "turn_index": turn_index,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "memory_block": self.memory_block,
            "window_hash": _window_hash(history),
            "message_count": len(history),
            "history": json.loads(json.dumps(history, default=str)),
        }
        self.checkpoints.append(record)
        if len(self.checkpoints) > self.MAX_CHECKPOINTS:
            self.checkpoints = self.checkpoints[-self.MAX_CHECKPOINTS:]
        return record

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """Return the checkpoint records (oldest first)."""
        return list(self.checkpoints)

    def restore(
        self,
        index: int,
        current_history: list[dict[str, Any]],
        turn_index: int = 0,
    ) -> list[dict[str, Any]]:
        """Restore checkpoint *index*, snapshotting the current state first.

        Returns a fresh copy of the checkpointed history.  The current
        session is preserved as an automatic ``pre-restore`` checkpoint, so
        nothing is lost.
        """
        if not 0 <= index < len(self.checkpoints):
            raise IndexError("checkpoint index out of range: {:d}".format(index))
        self.snapshot(current_history, reason="pre-restore", turn_index=turn_index)
        target = self.checkpoints[index]
        self.memory_block = target.get("memory_block", "")
        return json.loads(json.dumps(target.get("history", []), default=str))

    def branch(self, index: int) -> list[dict[str, Any]]:
        """Return a copy of checkpoint *index*'s history without touching
        the current session (used to explore an alternative path)."""
        if not 0 <= index < len(self.checkpoints):
            raise IndexError("checkpoint index out of range: {:d}".format(index))
        target = self.checkpoints[index]
        return json.loads(json.dumps(target.get("history", []), default=str))

    # -- archive -----------------------------------------------------------

    def append_archive(self, retired: list[dict[str, Any]]) -> int:
        """Append retired turns to ``archive.jsonl``.  Returns bytes written."""
        if not retired:
            return 0
        path = self.archive_path
        if path is None:
            return 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(path), "a", encoding="utf-8") as fh:
                for m in retired:
                    fh.write(json.dumps(m, default=str) + "\n")
            return path.stat().st_size
        except OSError:
            return 0

    def load_archive(self) -> list[dict[str, Any]]:
        """Read all archived turns back (for the memory viewer / debugging)."""
        path = self.archive_path
        if path is None or not path.is_file():
            return []
        out: list[dict[str, Any]] = []
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return out

    # -- persistence -------------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        """Serializable extension for the chat-history JSON file."""
        return {
            "memory_block": self.memory_block,
            "memory_updated_turn": self.memory_updated_turn,
            "checkpoints": self.checkpoints,
        }

    def load_payload(self, payload: dict[str, Any]) -> None:
        """Restore persisted state written by :meth:`to_payload`."""
        if not isinstance(payload, dict):
            return
        self.memory_block = str(payload.get("memory_block", "") or "")
        self.memory_updated_turn = int(payload.get("memory_updated_turn", 0) or 0)
        checkpoints = payload.get("checkpoints")
        if isinstance(checkpoints, list):
            self.checkpoints = [c for c in checkpoints if isinstance(c, dict)]


# Module singleton — mirrors agent_controller's ``_agent_state`` pattern.
store = CheckpointStore()
