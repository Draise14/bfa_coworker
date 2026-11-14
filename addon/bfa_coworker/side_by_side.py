# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Chats running side by side, safely (Tier 4, Threads 3, #359): the decisions.

Several chats' turns run at once (online engines; Local takes turns, see
:func:`threads.slots_for`).  Blender has one main thread and one linear undo
stack, so this module holds what keeps them apart, free of ``bpy``:

* :func:`fair_order` -- tool calls of several chats take turns on Blender's main
  thread: one call at a time, round-robin by chat.
* :class:`StepJournal` -- which chat last stepped on the undo stack, so a chat
  knows whether another chat's steps sit on top of its own (no positional undo
  then, and Revert has to ask).
* :func:`revert_choice` -- what Revert may offer (#359 / plan 4o section 3.3).
* :func:`other_chats_line` -- the one-line "Other chats: ..." context.
The claims (who is changing what) are :mod:`claims`; an HTTP 429 backing off only
the chat that got it is :func:`llm_transport.retry_delay`.
"""

from __future__ import annotations

__all__ = (
    "JOURNAL",
    "RevertChoice",
    "StepJournal",
    "WORKING",
    "fair_order",
    "request_thread",
    "revert_choice",
    "side_by_side",
    "undo_step_name",
    "working_chats",
)

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

_THREAD_RE = re.compile(rb'"thread":\s*"([^"\\]*)"')


def request_thread(raw: bytes) -> str:
    """The chat a raw bridge request (JSON bytes) is stamped with ("" = none / the
    default chat), without parsing the whole payload.  A ``"thread": "..."`` inside a
    JSON string value is escaped (``\\"thread\\"``), so only the real key matches."""
    found = _THREAD_RE.search(raw)
    return found.group(1).decode("utf-8", "replace") if found else ""


def fair_order(ready: Iterable[tuple[Any, str]], last: str = "") -> list[tuple[Any, str]]:
    """The calls to run now, fairly: *ready* is ``(call, chat)`` in arrival order;
    *last* is the chat whose call ran last.  One call per chat (its oldest), chats in
    a fixed cyclic order (their tags, sorted) starting with the one after *last*: no
    chat can run two calls while another chat's call waits."""
    first: dict[str, Any] = {}
    for call, chat in ready:
        first.setdefault(chat, call)
    chats = sorted(first)
    if not chats:
        return []
    start = next((i for i, c in enumerate(chats) if c > last), 0)
    return [(first[c], c) for c in chats[start:] + chats[:start]]


class StepJournal:
    """Which chat stepped on Blender's one undo stack, in order (#359).

    Every call a chat makes that may push an undo step (a write tool, an undo
    push) is noted with a rising number; a chat keeps the number from when its
    turn began (or from its own last undo push) and asks whether another chat
    stepped since.  Thread-safe: the bridge notes on the main thread, a turn's
    worker asks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._last: dict[str, int] = {}

    def note(self, chat: str) -> int:
        """*chat* (its tag) may have pushed an undo step now; returns its number."""
        with self._lock:
            self._seq += 1
            self._last[str(chat or "")] = self._seq
            return self._seq

    def mark(self) -> int:
        """The number to compare with later (the latest step so far)."""
        with self._lock:
            return self._seq

    def others_since(self, chat: str, mark: int) -> list[str]:
        """The other chats that stepped after *mark* (sorted)."""
        chat = str(chat or "")
        with self._lock:
            return sorted(c for c, seq in self._last.items() if c != chat and seq > mark)

    def clear(self) -> None:
        with self._lock:
            self._seq = 0
            self._last.clear()


JOURNAL = StepJournal()


# Who reports its running chats: the loop's runner and the External Agent engine each
# add a callable returning the tags of the chats whose turn runs now.
WORKING: list[Callable[[], Iterable[str]]] = []


def working_chats() -> list[str]:
    """The chats (tags) whose turn runs now, on any engine."""
    found: set[str] = set()
    for source in list(WORKING):
        try:
            found.update(str(t or "") for t in source())
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    return sorted(found)


def side_by_side() -> bool:
    """Two or more chats are working at once."""
    return len(working_chats()) >= 2


@dataclass
class RevertChoice:
    """What Revert may offer for a chat's last turn (#359, plan 4o section 3.3).

    ``kind``: ``none`` (no finished turn), ``edited`` (you changed the scene since),
    ``direct`` (only this chat stepped since its turn began: revert in one go),
    ``choose`` (another chat stepped since: *Revert both*, or *Cancel*) or ``busy``
    (that chat is still working on top of it: not now).  ``options`` are among
    ``revert``, ``both``, ``scoped`` (a restore from this turn's own snapshot, where
    one was kept) and ``cancel``."""

    kind: str
    message: str = ""
    options: tuple[str, ...] = ()
    others: list[str] = field(default_factory=list)


def revert_choice(finished: bool, user_edited: bool, others: list[str], working: list[str],
                  has_snapshot: bool = False, title: Callable[[str], str] = str) -> RevertChoice:
    """Revert's choice for a chat's last turn: *others* stepped on the undo stack since
    that turn began, *working* are the chats running now.  Never a silent partial
    revert: an undo to the turn's start also takes back the other chats' steps since,
    so with *others* it is offered only as *Revert both*, and not while one of them
    still works."""
    if not finished:
        return RevertChoice("none", "There's no finished turn to revert.")
    if user_edited:
        return RevertChoice("edited", "The scene changed after the turn, so use Edit > Undo History to "
                                      "pick the step.")
    others = sorted(set(others))
    if not others:
        return RevertChoice("direct", "", ("revert",))
    scoped = ("scoped",) if has_snapshot else ()
    busy = [o for o in others if o in set(working)]
    if busy:
        return RevertChoice("busy", "{:s} still working after this turn: reverting now would undo its steps "
                                    "too. Wait until it's done, or use Edit > Undo History.".format(
                                        _chats(busy, title, "is", "are")),
                            scoped + ("cancel",), others)
    return RevertChoice("choose", "{:s} the scene after this turn began. Reverting this turn also undoes "
                                  "those changes.".format(_chats(others, title, "changed", "changed")),
                        ("both",) + scoped + ("cancel",), others)


def _chats(tags: list[str], title: Callable[[str], str], one: str, many: str) -> str:
    """"The Node chat is" / "The Node and Sequencer chats are"."""
    names = [title(t) for t in tags]
    if len(names) == 1:
        return "The {:s} chat {:s}".format(names[0], one)
    return "The {:s} and {:s} chats {:s}".format(", ".join(names[:-1]), names[-1], many)


def undo_step_name(label: str, tool: str) -> str:
    """The undo step of one tool call, named by its chat: "Coworker (3D): add cube"."""
    words = " ".join(str(tool or "a step").replace("_", " ").split())
    return "{:s}: {:s}".format(label or "Coworker", words)
