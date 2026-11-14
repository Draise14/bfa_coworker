# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
One envelope per tool answer (#487).

The bridge answers every call as ``{"status": ..., "result": ...}``.  A
deferred call's checker (``check_is_finished`` in tool-code, see
:mod:`deferred_tool`) returns the bare *result*, and the bridge puts the
envelope around it.  A checker that waits for the user first -- an approval
card (:func:`risky_steps.wrap`) or a repeat card (:func:`loop_guard.bridge_wrap`)
-- answers with the call's whole answer, envelope included, so it is marked
with :func:`answers_in_full` and sent as it is.  Without the mark an asked
step that then ran at once came out wrapped twice
(``{"status", "result": {"status", "result": ...}}``), and a declined one as
``"ok"`` holding the error.
"""

__all__ = (
    "answer_of",
    "answers_in_full",
    "in_full",
)

from typing import Any, Callable

_MARK = "answers_in_full"


def answers_in_full(check: Callable[[], Any]) -> Callable[[], Any]:
    """Mark *check*: what it returns once done is the whole answer (envelope included)."""
    setattr(check, _MARK, True)
    return check


def in_full(check: Any) -> bool:
    """*check* returns the whole answer (see :func:`answers_in_full`)."""
    return bool(getattr(check, _MARK, False))


def answer_of(check: Any, result: Any, extra: dict[str, Any] | None = None) -> Any:
    """The bridge's answer for *result*, what *check* returned once done.

    A marked checker's result is the answer already; a tool-code checker's bare
    result gets the envelope, with *extra* (the call's ``stdout`` / ``stderr``
    from before it was deferred).  Anything but a dict is passed through for the
    caller to refuse."""
    if not isinstance(result, dict) or in_full(check):
        return result
    answer: dict[str, Any] = {"status": "ok", "result": result}
    for key, value in (extra or {}).items():
        if value:
            answer[key] = value
    return answer
