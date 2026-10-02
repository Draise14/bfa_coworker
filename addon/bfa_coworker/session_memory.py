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
destroys the current state -- restoring first snapshots the current session.

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
    "is_system_note",
    "find_retire_boundary",
    "compact_history",
    "memory_writer_prompt",
    "MAX_ARCHIVE_MESSAGES",
    "store_lock",
)

import hashlib
import json
import threading
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

# Approximate characters per token -- must match agent_controller._CHARS_PER_TOKEN.
_CHARS_PER_TOKEN = 3.5

_MAX_MEMORY_CHARS = int(MEMORY_TARGET_TOKENS * _CHARS_PER_TOKEN)
_MAX_BULLET_CHARS = 160
_MAX_RETIRE_TEXT_CHARS = 6000

# Hard cap on ``archive.jsonl`` length.  When a compaction appends past this
# many messages the oldest lines are dropped (newest-N rotation), so a very
# long session cannot grow the archive without bound.  The archive is a
# debugging/continuity fallback, not the active conversation, so trimming the
# oldest raw turns is safe.
MAX_ARCHIVE_MESSAGES = 5000


# ---------------------------------------------------------------------------
# Memory block


# Prefix of the addon's own injected context messages (entity warnings,
# spiral corrections, tool-result fillers).  They are turn-scoped: "already
# created this turn" is only true for the turn that produced them.  Retired
# into a memory block they become false memories that confuse the model in
# a later turn or thread, so they are excluded from memory building.
_SYSTEM_NOTE_PREFIX = "[System:"


def is_system_note(message: dict[str, Any]) -> bool:
    """True for the addon's injected ``[System: ...]`` context messages.

    These are stored with the ``user`` role so they reach the model, but
    they are system-authored, turn-scoped context -- not user intent.
    """
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(
            str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "").lstrip().startswith(_SYSTEM_NOTE_PREFIX)


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
        if m.get("ui_only") or is_system_note(m):
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
    retired turns.  The result is the full replacement block -- prior memory
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
        "files touched, pending work, and errors seen -- using ONLY the "
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


def find_retire_boundary(
    messages: list[dict[str, Any]],
    keep_recent: int = MAX_WINDOW_TURNS,
    fallback_to_last_user: bool = False,
) -> int:
    """Return the index where older messages may be retired up to.

    Keeps the system prompt (index 0) and the most recent *keep_recent*
    messages verbatim.  The boundary is pushed forward to the next ``user``
    message so a tool-call exchange is never cut in half; returns the length
    of *messages* when there is nothing safe to retire.

    When *fallback_to_last_user* is set and the recent window holds no
    ``user`` boundary (a long single-turn agent/reasoning run whose tail is
    all assistant/tool/reasoning messages), the boundary falls back to the
    LAST ``user`` message so older turns can still be retired while the
    current turn is always kept verbatim.  Returns the length of *messages*
    when there is no ``user`` message at all.
    """
    n = len(messages)
    if n == 0:
        return 0
    start = 1 if messages[0].get("role") == "system" else 0
    boundary = max(start, n - keep_recent)
    if boundary <= start and not fallback_to_last_user:
        return n
    # Advance to the next user message so no orphaned tool result remains.
    adv = max(boundary, start)
    while adv < n and messages[adv].get("role") != "user":
        adv += 1
    if adv < n:
        return adv
    if not fallback_to_last_user:
        return n
    # Nothing retirable inside the recent window: keep the current turn by
    # retiring up to the last user message instead of wiping everything.
    for i in range(n - 1, start - 1, -1):
        if messages[i].get("role") == "user":
            return i
    return n


def compact_history(
    history: list[dict[str, Any]],
    prior_memory: str = "",
    memory_writer: Callable[[str, str], str | None] | None = None,
    keep_recent: int = MAX_WINDOW_TURNS,
    updated_turn: int = 0,
    fallback_to_last_user: bool = False,
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    """Retire old turns from *history* and produce a fresh memory block.

    Returns ``(new_history, memory_block, retired)``.  *memory_writer* (when
    given) receives the retired conversation text plus the prior memory and
    returns the LLM-written note (or ``None`` on failure -- the heuristic
    fallback is used then).  The caller is responsible for archiving
    *retired* and snapshotting a checkpoint afterwards.

    When there is nothing safe to retire (no ``user``-role boundary past the
    verbatim window) the history is returned UNCHANGED with an empty retired
    list -- never reduced to just the system prompt.
    """
    boundary = find_retire_boundary(history, keep_recent, fallback_to_last_user)
    if boundary >= len(history):
        # Nothing retirable: the boundary is the whole length (no user-role
        # message to land on).  Retiring here would gut the conversation to
        # the system prompt, so return it untouched instead.
        return list(history), prior_memory, []
    # Never retire or archive the system prompt (index 0): it is a live
    # instruction, not conversation, and must always survive in *kept*.
    _has_system = bool(history) and history[0].get("role") == "system"
    _start = 1 if _has_system else 0
    retired = [m for m in history[_start:boundary] if not m.get("ui_only")]
    kept = history[boundary:]
    if _has_system and (not kept or kept[0] is not history[0]):
        kept = [history[0]] + kept

    summary: str | None = None
    if memory_writer is not None and retired:
        def _msg_text(m: dict[str, Any]) -> str:
            if is_system_note(m):
                return ""
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(
                    str(b.get("text", "")) for b in content if isinstance(b, dict))
            return "{:s}: {:s}".format(m.get("role", "?"), str(content or ""))

        retired_text = "\n".join(
            t for t in (_msg_text(m) for m in retired) if t)
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
        # Retired turns kept in memory for the cumulative chat display: the
        # sidebar shows the WHOLE conversation (retired turns above the live
        # window) even though the model's context stays compacted.  Bounded to
        # MAX_ARCHIVE_MESSAGES; older turns remain on disk (append_archive).
        self.retired_history: list[dict[str, Any]] = []

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
            "memory_updated_turn": self.memory_updated_turn,
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

    def reset(self) -> None:
        """Clear all session state (memory, checkpoints, archive binding).

        Used by "New Thread" so a fresh conversation does not inherit the
        previous thread's memory block or checkpoints.  The archive *file* is
        left on disk as history; only the live binding is cleared.
        """
        self.checkpoints = []
        self.memory_block = ""
        self.memory_updated_turn = 0
        self.archive_path = None
        self.retired_history = []

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
        # Capture the target BEFORE snapshotting: the pre-restore snapshot is
        # appended (and, at MAX_CHECKPOINTS, trims the oldest), which would
        # shift every index and make ``self.checkpoints[index]`` return the
        # wrong record -- or, at the cap with the newest index, the just-added
        # pre-restore snapshot (a silent no-op).
        target = self.checkpoints[index]
        self.snapshot(current_history, reason="pre-restore", turn_index=turn_index)
        self.memory_block = target.get("memory_block", "")
        self.memory_updated_turn = int(target.get("memory_updated_turn", 0) or 0)
        # A checkpoint predates the current compaction, so its history already
        # contains the turns that were retired afterwards.  Clear the display
        # aggregate to avoid showing the restored turns twice.
        self.retired_history = []
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
        """Append retired turns to ``archive.jsonl``.  Returns bytes written.

        The file is capped at :data:`MAX_ARCHIVE_MESSAGES` lines; when the cap
        is exceeded the oldest lines are dropped (newest-N rotation) so a very
        long session cannot grow the archive without bound.  The return value
        is the number of bytes appended by *this* call.
        """
        if not retired:
            return 0
        path = self.archive_path
        if path is None:
            return 0
        written = 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(path), "a", encoding="utf-8") as fh:
                for m in retired:
                    line = json.dumps(m, default=str) + "\n"
                    fh.write(line)
                    written += len(line.encode("utf-8"))
            self._rotate_archive(path)
            return written
        except OSError:
            return 0

    def append_retired(self, retired: list[dict[str, Any]]) -> None:
        """Accumulate retired turns for the cumulative chat display.

        Unlike :meth:`append_archive` (a disk-only debug fallback), this keeps
        the retired turns in memory so the sidebar can show the WHOLE
        conversation -- retired turns above the live window -- even though the
        model's context stays compacted.  Bounded to
        :data:`MAX_ARCHIVE_MESSAGES` so a very long session cannot grow memory
        without limit; older turns remain on disk via :meth:`append_archive`.
        """
        if not retired:
            return
        self.retired_history.extend(retired)
        if len(self.retired_history) > MAX_ARCHIVE_MESSAGES:
            self.retired_history = self.retired_history[-MAX_ARCHIVE_MESSAGES:]

    @staticmethod
    def _rotate_archive(path: Path) -> None:
        """Keep only the newest :data:`MAX_ARCHIVE_MESSAGES` lines of *path*."""
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return
        if len(lines) <= MAX_ARCHIVE_MESSAGES:
            return
        keep = lines[-MAX_ARCHIVE_MESSAGES:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(str(tmp), "w", encoding="utf-8") as fh:
                fh.writelines(keep)
            tmp.replace(path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

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
            "retired_history": self.retired_history,
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
        retired_history = payload.get("retired_history")
        if isinstance(retired_history, list):
            self.retired_history = [
                m for m in retired_history if isinstance(m, dict)
            ]


# Module singleton -- mirrors agent_controller's ``_agent_state`` pattern.
store = CheckpointStore()

# Guards ``store`` mutations shared between the turn worker thread
# (compaction) and the UI operators (restore / compact / reset).  A re-entrant
# lock so the same thread can nest acquisitions safely.
store_lock = threading.RLock()
