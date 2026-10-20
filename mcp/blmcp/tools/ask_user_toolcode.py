# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for ask_user: a question with buttons in Coworker's chat.

Returns a checker, so the bridge answers only once the user has (see the
add-on's deferred_tool.py); it gives up a little before the connection would.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    question: str
    choices: Any
    allow_text: bool
    wait_seconds: float


class Result(NamedTuple):
    status: str
    answer: str


# @include_begin: _coworker_api_shared.py
# @include_end


def main(params: Params) -> Any:
    import time
    api = _coworker_api()
    qid = api.post_question(params.question, list(params.choices or []), params.allow_text)
    deadline = time.monotonic() + max(5.0, min(float(params.wait_seconds), 280.0))

    def check_is_finished() -> dict[str, object] | None:
        state = api.question_state(qid)
        if state["answered"]:
            return {"status": "ok", "answer": state["answer"]}
        if state["cancelled"]:
            return {"status": "cancelled", "answer": "",
                    "message": "The user stopped the turn instead of answering."}
        if time.monotonic() > deadline:
            api.cancel_question(qid)
            return {"status": "no_answer", "answer": "",
                    "message": "No answer yet. Carry on with a sensible default, or ask again later."}
        return None
    return check_is_finished
