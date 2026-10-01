# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for chat turn grouping in ui_chat.py.

The history renderer groups messages into turns.  Two regressions are pinned:

* The UI-only welcome greeting must not form a phantom turn (it created a
  "Turn 1" with no user message, so the user's real first turn looked like
  turn 2).
* A real user message must anchor a turn even when the ``turn_start`` flag is
  missing (the flag can be lost when history is sliced/compacted/reloaded);
  otherwise the message fell into the collapsed Workshop and looked like it had
  disappeared.
"""

__all__ = ()

import os
import types
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UI_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "ui_chat.py")


def _load_source() -> str:
    with open(_UI_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_func(name: str, extra: dict | None = None):
    src = _load_source()
    marker = "\ndef {:s}(".format(name)
    start = src.find(marker)
    if start < 0:
        raise ImportError("{:s} not found".format(name))
    start += 1
    end = len(src)
    for m in ("\ndef _", "\ndef ", "\nclass ", "\n# ---"):
        idx = src.find(m, start + 100)
        if 0 <= idx < end:
            end = idx
    mod = types.ModuleType("_ui_extract")
    mod.__dict__["__file__"] = _UI_PATH
    if extra:
        mod.__dict__.update(extra)
    exec(compile(src[start:end], _UI_PATH, "exec"), mod.__dict__)  # noqa: S102
    return mod.__dict__[name]


_is_system_note_msg = _extract_func("_is_system_note_msg")
_group_turns = _extract_func(
    "_group_turns", {"_is_system_note_msg": _is_system_note_msg})
_split_turn = _extract_func(
    "_split_turn", {"_is_system_note_msg": _is_system_note_msg})
_hist_index = _extract_func("_hist_index")


def _user(content, turn_start=True, **kw):
    m = {"role": "user", "content": content}
    if turn_start:
        m["turn_start"] = True
    m.update(kw)
    return m


class TestGroupTurns(unittest.TestCase):
    def test_ui_only_greeting_does_not_form_a_turn(self):
        history = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "Welcome!", "ui_only": True},
            _user("make a cube"),
            {"role": "assistant", "content": "Done."},
        ]
        turns = _group_turns(history)
        self.assertEqual(len(turns), 1, turns)
        self.assertEqual(turns[0][0]["content"], "make a cube")

    def test_user_message_anchors_turn_without_turn_start(self):
        # turn_start lost: the message must still form its own turn.
        history = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "Welcome!", "ui_only": True},
            _user("first", turn_start=False),
            {"role": "assistant", "content": "ok"},
            _user("second", turn_start=False),
            {"role": "assistant", "content": "ok2"},
        ]
        turns = _group_turns(history)
        self.assertEqual(len(turns), 2, turns)
        self.assertEqual(turns[0][0]["content"], "first")
        self.assertEqual(turns[1][0]["content"], "second")

    def test_system_note_does_not_anchor_turn(self):
        history = [
            _user("real request"),
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "{}"},
            _user("[System: tool results are above]", turn_start=False),
            {"role": "assistant", "content": "final"},
        ]
        turns = _group_turns(history)
        self.assertEqual(len(turns), 1, turns)
        self.assertEqual(turns[0][0]["content"], "real request")


class TestHistIndex(unittest.TestCase):
    """_hist_index must never raise when a message has left the history.

    Regression: the chat panel called ``history.index(msg)`` while the turn's
    worker thread mutated the same list, so a message captured by
    ``_group_turns`` could be gone by lookup time and ``list.index`` raised
    ``ValueError`` -- aborting the Panel draw and blanking the whole history
    for the rest of a long turn.
    """

    def test_present_message_returns_index(self):
        a = {"role": "user", "content": "a"}
        b = {"role": "assistant", "content": "b"}
        history = [a, b]
        self.assertEqual(_hist_index(history, a), 0)
        self.assertEqual(_hist_index(history, b), 1)

    def test_missing_message_returns_minus_one(self):
        history = [{"role": "user", "content": "a"}]
        gone = {"role": "assistant", "content": "b"}
        self.assertEqual(_hist_index(history, gone), -1)

    def test_identity_wins_over_equal_value(self):
        # Two distinct dicts that compare equal must map to their own slot.
        x = {"role": "assistant", "content": "same"}
        y = {"role": "assistant", "content": "same"}
        history = [x, y]
        self.assertEqual(_hist_index(history, x), 0)
        self.assertEqual(_hist_index(history, y), 1)

    def test_empty_history_returns_minus_one(self):
        self.assertEqual(_hist_index([], {"role": "user", "content": "z"}), -1)


class TestSplitTurn(unittest.TestCase):
    def test_user_and_conclusion_both_found(self):
        turn = [
            _user("make a cube", turn_start=False),
            {"role": "assistant", "content": "Done."},
        ]
        user_msg, _process, conclusion = _split_turn(turn)
        self.assertIsNotNone(user_msg)
        self.assertEqual(user_msg["content"], "make a cube")
        self.assertIsNotNone(conclusion)
        self.assertEqual(conclusion["content"], "Done.")

    def test_system_note_goes_to_process_not_user(self):
        turn = [
            _user("real"),
            _user("[System: context]", turn_start=False),
            {"role": "assistant", "content": "done"},
        ]
        user_msg, process, _conclusion = _split_turn(turn)
        self.assertEqual(user_msg["content"], "real")
        self.assertTrue(any(m["content"].startswith("[System:") for m in process))

    def test_ui_only_turn_is_empty(self):
        # After grouping, ui_only is skipped, so a turn never contains one;
        # but _split_turn on a lone ui_only must not crash and yields no user.
        turn = [{"role": "assistant", "content": "hi", "ui_only": True}]
        user_msg, _process, conclusion = _split_turn(turn)
        self.assertIsNone(user_msg)
        self.assertEqual(conclusion["content"], "hi")


if __name__ == "__main__":
    unittest.main()
