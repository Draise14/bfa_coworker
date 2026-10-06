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
    "COMPACTION_ROLE",
    "CheckpointStore",
    "store",
    "build_memory_block",
    "heuristic_memory_block",
    "is_system_note",
    "make_system_note",
    "SYSTEM_NOTE_KEY",
    "set_memory_budget",
    "memory_max_chars",
    "validate_memory_note",
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

try:  # Package import (inside Blender / the addon package).
    from . import goal_plan as _goal_plan  # type: ignore[import-not-found]
except ImportError:  # Standalone import (unit tests load this file directly).
    import importlib.util as _ilu
    import sys as _sys
    _gp_path = Path(__file__).with_name("goal_plan.py")
    _gp_spec = _ilu.spec_from_file_location("goal_plan", _gp_path)
    _goal_plan = _ilu.module_from_spec(_gp_spec)  # type: ignore[arg-type]
    _sys.modules.setdefault("goal_plan", _goal_plan)
    _gp_spec.loader.exec_module(_goal_plan)  # type: ignore[union-attr]

# Public alias so consumers (agent_controller, ui_chat) reach the goal/plan
# helpers through the module they already import.
goal_plan = _goal_plan

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

# Window-scaled memory budget.  The defaults above suit a ~16K window; a larger
# window can afford a richer note (fewer details lost per compaction) and a
# larger slice of retired text for the memory-writer to read.  Updated by
# :func:`set_memory_budget` once the real context size is known.
_memory_max_chars = _MAX_MEMORY_CHARS
_retire_text_chars = _MAX_RETIRE_TEXT_CHARS
# Bounds for the scaled values (tokens for the note, chars for the writer input).
_MEMORY_MIN_TOKENS = 400
_MEMORY_MAX_TOKENS = 1500
_RETIRE_TEXT_MAX_CHARS = 40000


def set_memory_budget(ctx_tokens: int) -> int:
    """Scale the memory-note budget to the context window.  Returns max chars.

    The note costs ~4% of the window (clamped to 400..1500 tokens), so a 8K
    window keeps a lean note while a 64K window keeps far more detail instead
    of discarding it at the old fixed 600-token cap.  The memory-writer may
    read up to ~30% of the window of retired text (it runs as its own request
    against the same server, so this must stay well inside the window).
    """
    global _memory_max_chars, _retire_text_chars
    if ctx_tokens <= 0:
        return _memory_max_chars
    tokens = max(_MEMORY_MIN_TOKENS, min(_MEMORY_MAX_TOKENS, int(ctx_tokens * 0.04)))
    _memory_max_chars = int(tokens * _CHARS_PER_TOKEN)
    _retire_text_chars = max(
        _MAX_RETIRE_TEXT_CHARS,
        min(_RETIRE_TEXT_MAX_CHARS, int(ctx_tokens * 0.3 * _CHARS_PER_TOKEN)))
    return _memory_max_chars


def memory_max_chars() -> int:
    """Current (window-scaled) memory-note size cap, in characters."""
    return _memory_max_chars

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

# Synthetic role stamped on the timeline marker recorded in
# ``retired_history`` when a compaction retires turns.  It is display-only:
# the chat panel renders it as an "Archive" node in the Workshop timeline so
# the user can audit exactly where their context was compressed.  It is never
# sent to the model.
COMPACTION_ROLE = "compaction"


# Structured marker stamped on every injected note (see
# :func:`make_system_note`).  The ``[System:`` text prefix is kept too -- it is
# what the MODEL reads as "this is not the user speaking" -- but detection keys
# on the flag first, so a note can never be mistaken for a real user turn just
# because its wording drifted.  (The auto-continue prompt "Continue. Keep this
# next step small..." had no prefix, so the chat panel showed it as a NEW user
# turn -- the "chat talks to itself" bug -- and compaction treated it as a turn
# boundary.)
SYSTEM_NOTE_KEY = "system_note"

# Legacy injected prompts written by older builds WITHOUT the ``[System:``
# prefix.  Persisted histories may still contain them; they must never anchor
# a turn or become the session goal.
_LEGACY_NOTE_PREFIXES = (
    "Continue. Keep this next step small",
)


def _content_text(content: Any) -> str:
    """Flatten a message ``content`` (str or multimodal list) to text."""
    if isinstance(content, list):
        return " ".join(
            str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "")


def make_system_note(text: str, kind: str = "note") -> dict[str, Any]:
    """Build an injected, system-authored context message.

    Stored with the ``user`` role (strict chat templates reject a mid-
    conversation ``system`` role) but flagged so every consumer -- the chat
    panel, compaction, goal capture -- knows it is NOT the user speaking.
    The text always carries the ``[System: ...]`` wrapper the model reads.
    """
    body = str(text or "").strip()
    if not body.startswith(_SYSTEM_NOTE_PREFIX):
        body = "{:s} {:s}]".format(_SYSTEM_NOTE_PREFIX, body)
    return {"role": "user", "content": body, SYSTEM_NOTE_KEY: kind or "note"}


def is_system_note(message: dict[str, Any]) -> bool:
    """True for the addon's injected context messages (never real user intent).

    Detection order: the structured :data:`SYSTEM_NOTE_KEY` flag, then the
    ``[System:`` text prefix (older histories), then the known legacy
    unprefixed prompts.  These are stored with the ``user`` role so they
    reach the model, but they are system-authored, turn-scoped context.
    """
    if message.get("role") != "user":
        return False
    if message.get(SYSTEM_NOTE_KEY):
        return True
    text = _content_text(message.get("content")).lstrip()
    if text.startswith(_SYSTEM_NOTE_PREFIX):
        return True
    return any(text.startswith(p) for p in _LEGACY_NOTE_PREFIXES)


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
        elif role in ("user", "assistant"):
            # Keep the MOST RECENT bullets: the newest context is what the
            # next turns build on (keeping the first 8 dropped the latest
            # decisions of a long retired span).
            bullets.append(("{:s}: {:s}".format(role, text[:_MAX_BULLET_CHARS])).strip())
            bullets = bullets[-8:]
        elif role == "tool" and ("error" in text.lower() or "failed" in text.lower()):
            errors.append(text[:_MAX_BULLET_CHARS])
            errors = errors[-4:]
        for token in text.replace("\"", " ").split():
            if token.lower().endswith(".blend") and token not in objects:
                objects.append(token[:_MAX_BULLET_CHARS])

    lines = ["[Session memory]"]
    if goal:
        # The authoritative goal is pinned separately (goal_plan); this is
        # just the earliest request in the retired span.
        lines.append("Earlier request: {:s}".format(goal[:_MAX_BULLET_CHARS]))
    if bullets:
        lines.append("Recent context:")
        lines.extend("- {:s}".format(b) for b in bullets)
    if objects:
        lines.append("Objects & files touched:")
        lines.extend("- {:s}".format(o) for o in objects[:6])
    if errors:
        lines.append("Errors seen:")
        lines.extend("- {:s}".format(e) for e in errors)
    return "\n".join(lines)[:_memory_max_chars]


def _fit_note(text: str, max_chars: int) -> str:
    """Trim *text* to *max_chars* by dropping its OLDEST lines first.

    The previous ``block[:max]`` cut from the END, so when the heuristic
    path prepended the prior note the NEWEST information was what got lost
    -- after a few compactions only stale context survived.  The header
    line (``[Session memory]``) is kept.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    header = lines[0] if lines and lines[0].startswith("[Session memory]") else ""
    body = lines[1:] if header else lines
    while body and len("\n".join(([header] if header else []) + body)) > max_chars:
        body.pop(0)
    out = "\n".join(([header] if header else []) + body)
    return out[-max_chars:] if len(out) > max_chars else out


_NOTE_HEADINGS = ("decisions", "objects", "pending", "errors", "goal", "done", "progress")


def validate_memory_note(text: str | None, max_chars: int | None = None) -> str | None:
    """Return a cleaned memory-writer note, or ``None`` when it is unusable.

    Small local models sometimes answer the writer prompt with chat ("Sure!
    Here is...") or an empty / one-line reply; folding that in would replace
    the prior memory with noise.  A usable note has some substance and at
    least one of the expected section headings.  Over-long notes are trimmed
    oldest-line-first.
    """
    if not text:
        return None
    note = str(text).strip()
    # Strip a leading chatty preface line ("Here is the updated note:").
    first, _, rest = note.partition("\n")
    if rest and first.rstrip().endswith(":") and len(first) < 80 and not any(
            h in first.lower() for h in _NOTE_HEADINGS):
        note = rest.strip()
    if len(note) < 20:
        return None
    low = note.lower()
    if not any(h in low for h in _NOTE_HEADINGS):
        return None
    return _fit_note(note, max_chars or _memory_max_chars)


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
    summary = validate_memory_note(summary)
    if summary:
        block = summary
        if not block.startswith("[Session memory]"):
            block = "[Session memory]\n" + block
    else:
        block = heuristic_memory_block(retired_turns)
        if prior_memory.strip():
            # Heuristic path: carry the prior block forward so nothing is
            # lost; the NEW lines go last so trimming drops the oldest.
            prior = "\n".join(
                ln for ln in prior_memory.strip().splitlines()
                if not ln.startswith("Last updated"))
            new_lines = block.splitlines()[1:] if block.startswith("[Session memory]") else block.splitlines()
            block = prior + "\n" + "\n".join(new_lines)
    # Drop any stale stamp, then re-stamp so it always survives trimming.
    block = "\n".join(
        ln for ln in block.splitlines() if not ln.startswith("Last updated"))
    stamp = "Last updated: turn {:d}".format(updated_turn)
    block = _fit_note(block, _memory_max_chars - len(stamp) - 1)
    return "{:s}\n{:s}".format(block, stamp)


def memory_writer_prompt(retired_text: str, prior_memory: str) -> list[dict[str, str]]:
    """Build the messages for the dedicated memory-writer LLM call."""
    sys_text = (
        "You maintain a compact session-memory note for a Blender AI agent. "
        "The user's goal and the step plan are stored separately -- do NOT "
        "restate them. Rewrite the note so it captures what was done, "
        "decisions made, objects & files touched (exact names), pending work, "
        "and errors seen (with the fix that worked) -- using ONLY the "
        "conversation below plus the previous note. Never drop facts from the "
        "previous note that are still true. Keep these exact section headings: "
        "Done / Decisions / Objects & files touched / Pending / Errors seen. "
        "Be terse (bullet points, under {:d} tokens total). "
        "Output only the note text."
    ).format(int(_memory_max_chars / _CHARS_PER_TOKEN))
    prior = prior_memory.strip() or "(none yet)"
    # Keep the NEWEST retired text when it must be cut (the end of the span
    # is closest to the live window the agent continues from).
    convo = retired_text[-_retire_text_chars:]
    return [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": (
            "Previous note:\n{:s}\n\nConversation to fold in:\n{:s}".format(prior, convo)
        )},
    ]


# ---------------------------------------------------------------------------
# Compaction


def _is_real_user(message: dict[str, Any]) -> bool:
    """True for a genuine user turn -- NOT an injected ``[System: ...]`` note.

    The addon stores its turn-scoped context messages (entity warnings, the
    tool-result filler prompt) with the ``user`` role so the model sees them,
    but they are NOT conversation boundaries.  Treating them as boundaries
    let a compaction land mid-turn and retire the CURRENT turn's real prompt --
    which then left the live window anchored on an injected note, so the chat
    panel (which only anchors turns on real user messages) showed no turn at
    all while the real prompt sat in the archive.
    """
    return message.get("role") == "user" and not is_system_note(message)


def find_retire_boundary(
    messages: list[dict[str, Any]],
    keep_recent: int = MAX_WINDOW_TURNS,
    fallback_to_last_user: bool = False,
) -> int:
    """Return the index where older messages may be retired up to.

    Keeps the system prompt (index 0) and the most recent *keep_recent*
    messages verbatim.  The boundary is pushed forward to the next REAL
    ``user`` message (injected ``[System: ...]`` notes are not boundaries) so
    a tool-call exchange is never cut in half AND the current turn's prompt is
    never retired; returns the length of *messages* when there is nothing safe
    to retire.

    When *fallback_to_last_user* is set and the recent window holds no
    ``user`` boundary (a long single-turn agent/reasoning run whose tail is
    all assistant/tool/reasoning messages), the boundary falls back to the
    LAST real ``user`` message so older turns can still be retired while the
    current turn is always kept verbatim.  Returns the length of *messages*
    when there is no real ``user`` message at all.
    """
    n = len(messages)
    if n == 0:
        return 0
    start = 1 if messages[0].get("role") == "system" else 0
    boundary = max(start, n - keep_recent)
    if boundary <= start and not fallback_to_last_user:
        return n
    # Advance to the next REAL user message so no orphaned tool result remains
    # and the current turn's prompt is never retired.
    adv = max(boundary, start)
    while adv < n and not _is_real_user(messages[adv]):
        adv += 1
    if adv < n:
        return adv
    if not fallback_to_last_user:
        return n
    # Nothing retirable inside the recent window: keep the current turn by
    # retiring up to the last real user message instead of wiping everything.
    # (A last real user at ``start`` means there is only one turn -- nothing
    # to retire -- so return ``n``.)
    for i in range(n - 1, start - 1, -1):
        if _is_real_user(messages[i]):
            return i if i > start else n
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
        # Pinned goal & plan (see goal_plan.py).  Lives OUTSIDE the
        # compactable conversation so no compaction can erase it.
        self.goal = _goal_plan.GoalPlan()

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
            "goal_plan": self.goal.to_dict(),
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
        self.goal.reset()

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
        # Restore the goal & plan as they were at that point (older
        # checkpoints carry none -- keep the current goal then).
        if isinstance(target.get("goal_plan"), dict):
            self.goal.load_dict(target["goal_plan"])
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
        # Record a timeline marker at the compaction boundary so the chat panel
        # can show WHEN the context was compressed (inside the Workshop).  The
        # marker carries the fresh memory summary; it is display-only and never
        # re-sent to the model.
        self.retired_history.append({
            "role": COMPACTION_ROLE,
            "content": self.memory_block,
            "retired": len(retired),
            "turn": self.memory_updated_turn,
        })
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
            "goal_plan": self.goal.to_dict(),
        }

    def load_payload(self, payload: dict[str, Any]) -> None:
        """Restore persisted state written by :meth:`to_payload`."""
        if not isinstance(payload, dict):
            return
        self.memory_block = str(payload.get("memory_block", "") or "")
        self.memory_updated_turn = int(payload.get("memory_updated_turn", 0) or 0)
        # Backward compatible: older payloads have no goal_plan.
        self.goal.load_dict(payload.get("goal_plan") or {})
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
