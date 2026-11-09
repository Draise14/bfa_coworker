# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Loop guard: the same tool call again and again (Tier 4, Runtime 1, #310).

A model stuck in a loop repeats one call (same tool, same input) and wastes
minutes, tokens and sometimes the scene.  One rule for every engine:

- the **3rd** identical call in a turn asks you first with the approval card
  ("It keeps doing the same thing: let it try again, or stop?");
- the **4th** is refused with a short tool error, without running;
- a read that changes nothing (``engine.PLAN_SAFE_TOOLS``) gets more room
  (asks on the 5th), and any other call in between starts its count again:
  reading the digest after every step is normal, reading it five times in a
  row without doing anything is not;
- a long job's status poll (``get_job_status``) is never counted;
- in Auto mode nobody can answer: the 3rd call is declined at once.

The local loop (Local / Remote) keeps its own :class:`LoopGuard` per turn and
waits for the answer on its worker thread (:func:`wait_answer`).  External
Agent and External Harness calls are counted at the bridge
(:data:`BRIDGE`, :func:`bridge_wrap`), per session, and reset when a turn
starts; a Harness client has no turns in Blender, so a pause of
:data:`_BRIDGE_IDLE_RESET` seconds counts as a new one.

bpy-free and thread-safe.
"""

from __future__ import annotations

__all__ = (
    "ASK",
    "BRIDGE",
    "REFUSE",
    "RUN",
    "LoopGuard",
    "bridge_wrap",
    "decide",
    "repeat_request",
    "signature",
    "wait_answer",
)

import hashlib
import json
import threading
import time
from typing import Any, Callable

from .approvals import ApprovalRequest, Approvals

RUN, ASK, REFUSE = "run", "ask", "refuse"

REPEAT_ASK = 3          # the 3rd identical call asks; the 4th is refused
READ_REPEAT_ASK = 5     # reads that change nothing get more room

_BRIDGE_IDLE_RESET = 60.0
_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


def _ordinal(n: int) -> str:
    return _ORDINALS.get(n, "{:d}th".format(n))


def signature(tool: str, args: Any) -> str:
    """Tool name + its input with sorted keys, hashed (identical calls match)."""
    try:
        text = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(args)
    return hashlib.sha1("{:s}\0{:s}".format(str(tool or ""), text).encode("utf-8")).hexdigest()


def _is_read(tool: str) -> bool:
    from .engine import PLAN_SAFE_TOOLS
    return tool in PLAN_SAFE_TOOLS


def _is_exempt(tool: str) -> bool:
    from .long_jobs import is_status_poll
    return is_status_poll(tool)      # waiting for a render repeats its poll (Tools 6)


class LoopGuard:
    """Counts identical calls per key (a session) until the turn ends."""

    def __init__(self, ask_at: int = REPEAT_ASK, read_ask_at: int = READ_REPEAT_ASK,
                 idle_reset: float = 0.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ask_at = ask_at
        self.read_ask_at = read_ask_at
        self.idle_reset = idle_reset     # > 0: a pause this long starts a new turn (Harness)
        self._clock = clock
        self._lock = threading.Lock()
        self._counts: dict[str, dict[str, int]] = {}
        self._last: dict[str, float] = {}

    def new_turn(self, key: str | None = None) -> None:
        """A new turn: every count starts again (only *key*'s when given)."""
        with self._lock:
            if key is None:
                self._counts.clear()
                self._last.clear()
            else:
                self._counts.pop(key, None)
                self._last.pop(key, None)

    def check(self, tool: str, args: Any, key: str = "") -> tuple[str, int]:
        """Count this call: ``(verdict, nth)``, *verdict* one of RUN / ASK / REFUSE."""
        tool = str(tool or "")
        if _is_exempt(tool):
            return RUN, 0
        read = _is_read(tool)
        sig = signature(tool, args)
        with self._lock:
            now = self._clock()
            if self.idle_reset > 0 and now - self._last.get(key, now) > self.idle_reset:
                self._counts.pop(key, None)
            self._last[key] = now
            counts = self._counts.setdefault(key, {})
            if not read:
                # Something else ran: the scene may have changed, re-reading is fair again.
                for other in [s for s, (r, _n) in counts.items() if r]:
                    del counts[other]
            nth = counts.get(sig, (read, 0))[1] + 1
            counts[sig] = (read, nth)
        limit = self.read_ask_at if read else self.ask_at
        if nth < limit:
            return RUN, nth
        return (ASK if nth == limit else REFUSE), nth


# -- What the model and the card say -------------------------------------------------

def _error(message: str) -> dict[str, object]:
    return {"status": "error", "message": message}


def refused(tool: str, nth: int) -> dict[str, object]:
    return _error("Refused: this is the {:s} call to {:s} with the same input in this turn, and it isn't "
                  "getting anywhere. Don't repeat it: change the approach, or stop and tell the user "
                  "what blocks you.".format(_ordinal(nth), tool or "this tool"))


def declined(tool: str) -> dict[str, object]:
    return _error("The user stopped this repeat ({:s} with the same input again). Don't call it again "
                  "with the same input; try another way or ask them.".format(tool or "this tool"))


def auto_declined(tool: str) -> dict[str, object]:
    return _error("Auto mode doesn't repeat a step a third time without the user ({:s} with the same "
                  "input). Change the approach, or finish and report what blocks you.".format(tool or "this tool"))


def _kind(tool: str) -> str:
    return "repeat:" + (tool or "")


def repeat_request(tool: str, args: Any, nth: int, source: str = "") -> ApprovalRequest:
    """The approval card for a repeat."""
    return ApprovalRequest(
        tool=_kind(tool), source=source, args=args, coworker_tool=True,
        reason="run {:s} a {:s} time with the same input".format(tool or "a tool", _ordinal(nth)),
        note="It keeps doing the same thing: let it try again, or stop?")


def decide(verdict: str, tool: str, nth: int, approvals: Approvals, auto: bool) -> tuple[str, dict[str, object] | None]:
    """What to do right now: ``(RUN, None)``, ``(REFUSE, error)`` or ``(ASK, None)`` (show the card)."""
    if verdict == RUN:
        return RUN, None
    if verdict == REFUSE:
        return REFUSE, refused(tool, nth)
    if auto:                                    # nobody is there to answer
        return REFUSE, auto_declined(tool)
    if approvals.allowed_always(_kind(tool)):   # "Allow this turn" was picked
        return RUN, None
    return ASK, None


def wait_answer(approvals: Approvals, request: ApprovalRequest, stop: Any = None,
                poll: float = 0.1) -> str:
    """Ask *request* and wait (a worker thread): the choice, or "cancel" on Stop."""
    done = threading.Event()
    box: list[str] = []

    def respond(choice: str) -> None:
        box.append(choice)
        done.set()

    approvals.ask(request, respond)
    while not done.wait(poll):
        if stop is not None and stop.is_set():
            approvals.answer("deny", request.id)    # take the card down
            return "cancel"
    return box[0] if box else "cancel"


def bridge_wrap(guard: LoopGuard, approvals: Approvals, auto: bool, tool: str, args: Any, key: str,
                proceed: Callable[[], tuple[dict[str, object], Callable[[], dict[str, object] | None] | None]],
                source: str = "The agent",
                ) -> tuple[dict[str, object], Callable[[], dict[str, object] | None] | None] | None:
    """The bridge's repeat check: None lets the call through, else ``(response, check_fn)``
    (a refusal, or the card's deferred answer; once allowed, *proceed* runs the call)."""
    verdict, nth = guard.check(tool, args, key)
    action, error = decide(verdict, tool, nth, approvals, auto)
    if action == RUN:
        return None
    if action == REFUSE:
        print("[Coworker] loop guard: refused {:s} ({:s} identical call)".format(tool, _ordinal(nth)))
        return error, None
    state: dict[str, Any] = {"answer": None, "inner": None}
    approvals.ask(repeat_request(tool, args, nth, source), lambda choice: state.__setitem__("answer", choice))

    def check() -> dict[str, object] | None:
        if state["inner"] is not None:      # allowed, and the call itself is deferred
            return state["inner"]()
        answer = state["answer"]
        if answer is None:
            return None
        if answer in ("deny", "cancel"):
            return declined(tool)
        response, inner = proceed()
        if inner is not None:
            state["inner"] = inner
            return None
        return response
    return {}, check


# External Agent / External Harness calls, counted at the bridge.
BRIDGE = LoopGuard(idle_reset=_BRIDGE_IDLE_RESET)
