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

:func:`readable` is the other way round, for you (#500): the Workshop shows a
tool's answer as the tool gave it, without the envelope on top, and a failed
tool's first line says it failed.
"""

__all__ = (
    "answer_of",
    "answers_in_full",
    "in_full",
    "readable",
)

import json
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


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _headline(message: Any) -> str:
    """A failure's first line: its message, or a traceback's last line (the exception)."""
    text = str(message or "").strip() or "the tool failed"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines[0].startswith("Traceback") and len(lines) > 1:
        return "Error: {:s}\n{:s}".format(lines[-1], text)
    return "Error: " + text


def readable(text: str) -> str:
    """A tool's answer (the text the model gets) as the Workshop shows it (#500).

    The bridge's envelope (``{"status": "ok", "result": ...}``) is left out: the tool's
    own answer comes first, then the call's ``stdout`` / ``stderr`` and other notes.  A
    failure -- the envelope's, or the tool's own ``"status": "error"`` inside it -- starts
    with ``Error: <message>``, then the rest of the answer.  Anything else (plain text, a
    tool's own JSON without the envelope) is returned as it is."""
    try:
        answer = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(answer, dict) or answer.get("status") not in ("ok", "error"):
        return text
    if "result" not in answer and not (answer["status"] == "error" and "message" in answer):
        return text                     # a tool's own flat answer: already its own
    result = answer.get("result")
    inner = result if isinstance(result, dict) else {}
    rest = {k: v for k, v in answer.items() if k not in ("status", "result", "message")}
    if answer["status"] == "error":
        parts = [_headline(answer.get("message"))]
        if result not in (None, "", {}):
            parts.append(_text(result))
    elif inner.get("status") == "error" or inner.get("state") == "error":
        parts = [_headline(inner.get("message"))]
        own = {k: v for k, v in inner.items() if k not in ("status", "message")}
        if own:
            parts.append(_text(own))
    else:
        parts = [_text(result) if result is not None else "(no result)"]
    for key, value in rest.items():
        if value not in (None, "", [], {}):
            parts.append("{:s}: {:s}".format(key, _text(value)))
    return "\n".join(part.rstrip("\n") for part in parts)
