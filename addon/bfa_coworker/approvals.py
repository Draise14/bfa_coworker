# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Approvals: "may I?" questions answered in the chat (Tier 4, Engine 5 / Agent 5).

One model for every engine: an engine (today the External Agent) puts a
request here and waits; the chat shows the oldest one with **Allow once**,
**Always** and **Deny**, and the answer goes back through the request's
callback.  "Always" lasts for the session: the same tool runs without asking
until the agent session ends.

bpy-free and thread-safe: requests arrive on the agent's reader thread, the
answer comes from a click on the main thread.
"""

from __future__ import annotations

__all__ = (
    "CHOICES",
    "ApprovalRequest",
    "Approvals",
)

import itertools
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable

# What a click can answer; "cancel" is the engine's (Stop, the agent went away).
CHOICES = ("once", "always", "deny")

_DETAIL_CHARS = 160
_ids = itertools.count(1)


def _detail(args: Any) -> str:
    """The tool's input on one short line (the code, the query, the path...)."""
    if not args:
        return ""
    if isinstance(args, dict) and len(args) == 1:
        args = next(iter(args.values()))
    text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= _DETAIL_CHARS else text[:_DETAIL_CHARS - 3] + "..."


@dataclass
class ApprovalRequest:
    """One question: who asks to run what."""

    tool: str                   # the tool's name, e.g. "execute_blender_code" or "bash"
    source: str = ""            # who asks, e.g. "Claude Code"
    args: Any = None            # the tool's input, for the detail line
    coworker_tool: bool = False   # one of Coworker's Blender tools
    reason: str = ""              # a risky step, in plain words (#166): "save over your .blend"
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = "a{:d}".format(next(_ids))

    @property
    def title(self) -> str:
        who = self.source or "The agent"
        if self.reason:
            return "{:s} wants to {:s}".format(who, self.reason)
        return "{:s} wants to run {:s}".format(who, self.tool or "a tool")

    @property
    def detail(self) -> str:
        return _detail(self.args)


class Approvals:
    """The open questions (oldest first) and the tools allowed for the session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[tuple[ApprovalRequest, Callable[[str], None]]] = []
        self._always: set[str] = set()
        self.on_change: Callable[[], None] | None = None     # redraw hook (any thread)

    # -- engine side ---------------------------------------------------------------
    def allowed_always(self, tool: str) -> bool:
        with self._lock:
            return tool in self._always

    def ask(self, request: ApprovalRequest, respond: Callable[[str], None]) -> None:
        """Queue *request*; *respond* gets one of :data:`CHOICES` or "cancel", once."""
        with self._lock:
            self._pending.append((request, respond))
        self._changed()

    def cancel_all(self) -> None:
        """Stop or the agent went away: every open question is answered "cancel"."""
        with self._lock:
            pending, self._pending = self._pending, []
        for _request, respond in pending:
            respond("cancel")
        if pending:
            self._changed()

    def forget_always(self) -> None:
        """The approval mode changed (#240): every "Always" asks again."""
        with self._lock:
            self._always.clear()
        self._changed()

    def end_session(self) -> None:
        """A new agent session: cancel what's open and forget "Always"."""
        self.cancel_all()
        with self._lock:
            self._always.clear()

    # -- chat side -----------------------------------------------------------------
    def current(self) -> ApprovalRequest | None:
        """The question the chat shows (the oldest open one)."""
        with self._lock:
            return self._pending[0][0] if self._pending else None

    def count(self) -> int:
        with self._lock:
            return len(self._pending)

    def answer(self, choice: str, request_id: str = "") -> bool:
        """Answer the oldest question (or *request_id*); False when there's none."""
        if choice not in CHOICES:
            raise ValueError("choice must be one of {!r}".format(CHOICES))
        with self._lock:
            index = next((i for i, (r, _) in enumerate(self._pending)
                          if not request_id or r.id == request_id), None)
            if index is None:
                return False
            request, respond = self._pending.pop(index)
            if choice == "always":
                self._always.add(request.tool)
            # Questions for the same tool that "Always" now covers are answered too.
            also = [(r, f) for r, f in self._pending if choice == "always" and r.tool == request.tool]
            self._pending = [p for p in self._pending if p not in also]
        respond(choice)
        for _r, other in also:
            other("always")
        self._changed()
        return True

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()
