# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Auto mode: keep working while the plan is on track (Tier 4, #177).

Opt-in (the Plan panel's *Auto* toggle).  When a turn ends well, the plan has
steps left and the queue is empty, Coworker sends the next "continue" message
itself -- on any engine.  It stops, and says why, when:

* the plan is done;
* a limit is reached (hours, turns -- Preferences);
* two turns in a row made no progress on the plan;
* two real errors in a row (a timeout counts as passing: up to three in a row
  are retried);
* you press Stop or turn Auto off.

While it runs it also answers for you: the agent's approval cards are allowed
once (only what your settings already allow -- refusals stay refusals), and a
question gets "choose what fits the plan".

bpy-free: :class:`AutoRun` decides; the add-on sends the message.
"""

from __future__ import annotations

__all__ = (
    "AutoRun",
    "QUESTION_ANSWER",
    "continue_prompt",
)

import time
from dataclasses import dataclass, field

QUESTION_ANSWER = ("Auto mode is on and the user is away: choose what fits the plan best, say which "
                   "you chose, and carry on.")
_TIMEOUT_WORDS = ("timed out", "timeout", "time out", "read timed", "deadline")
_MAX_TIMEOUTS = 3
_MAX_ERRORS = 2
_MAX_STALLS = 2


def continue_prompt(next_step: str, done: int, total: int) -> str:
    """The message Auto sends to keep going."""
    return ("[Auto mode] Continue with the plan ({:d}/{:d} done). Next step: {:s}. Work through the "
            "remaining steps, keep the plan updated as you finish each one, and stop when the plan is "
            "done.".format(done, total, next_step or "the next unfinished step"))


@dataclass
class AutoRun:
    """One Auto session: from turning it on until it stops."""

    max_seconds: float = 8 * 3600.0
    max_turns: int = 30
    started: float = field(default_factory=time.time)
    turns: int = 0              # continues sent
    last_done: int = -1
    stalls: int = 0
    errors: int = 0
    timeouts: int = 0
    stopped: str = ""           # why it stopped ("" while it runs)

    def after_turn(self, *, ok: bool, error: str, cancelled: bool, done: int, total: int,
                   queued: int, now: float | None = None) -> str:
        """A turn ended: "continue", "wait" (the queue goes first) or "stop" (see *stopped*)."""
        now = time.time() if now is None else now
        if self.stopped:
            return "stop"
        if cancelled:
            return self._stop("you pressed Stop")
        if total and done >= total:
            return self._stop("the plan is done ({:d}/{:d})".format(done, total))
        if not total:
            return self._stop("there's no plan to follow (ask for a multi-step job first)")
        timed_out = bool(error) and any(w in error.lower() for w in _TIMEOUT_WORDS)
        if error and not timed_out:
            self.errors += 1
            if self.errors >= _MAX_ERRORS:
                return self._stop("two errors in a row: " + error.splitlines()[0][:120])
        elif timed_out:
            self.timeouts += 1
            if self.timeouts > _MAX_TIMEOUTS:
                return self._stop("it kept timing out ({:d} times in a row)".format(self.timeouts))
        else:
            self.errors = self.timeouts = 0
        if not error:
            if done > self.last_done:
                self.stalls = 0
            elif self.last_done >= 0:
                self.stalls += 1
                if self.stalls >= _MAX_STALLS:
                    return self._stop("two turns made no progress on the plan")
            self.last_done = max(self.last_done, done)
        if now - self.started >= self.max_seconds:
            return self._stop("the time limit ({:.0f} h) is reached".format(self.max_seconds / 3600))
        if self.turns >= self.max_turns:
            return self._stop("the turn limit ({:d}) is reached".format(self.max_turns))
        if queued:
            return "wait"
        self.turns += 1
        return "continue"

    def _stop(self, why: str) -> str:
        self.stopped = why
        return "stop"

    def summary(self, done: int, total: int, now: float | None = None) -> str:
        """What Auto did, for the chat."""
        now = time.time() if now is None else now
        minutes = int((now - self.started) // 60)
        took = "{:d} h {:02d} min".format(minutes // 60, minutes % 60) if minutes >= 60 else "{:d} min".format(minutes)
        return ("**Auto mode stopped:** {:s}. It ran {:d} extra turn{:s} in {:s}; the plan is at {:d}/{:d}. "
                "Each turn can be undone from Edit > Undo History.".format(
                    self.stopped or "turned off", self.turns, "" if self.turns == 1 else "s", took, done, total))
