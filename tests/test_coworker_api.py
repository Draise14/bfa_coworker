# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for coworker_api.py (Tier 4, task "Engine 6"): the questions the MCP
server's ask_user tool puts in the chat, and the plan it writes.

1. A question waits until answered (a button or typed text), is read once,
   and the oldest open one is shown first.
2. Stop cancels open questions; an id that's gone reads as cancelled.
3. update_plan writes the same Goal & Plan the local loop does.

Run with::

    python -m unittest tests.test_coworker_api -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_api_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".coworker_api"), importlib.import_module(name + ".goal_plan")


api, gp = _load()


class TestQuestions(unittest.TestCase):

    def setUp(self):
        api._questions.clear()

    def test_waits_then_answered_once(self):
        qid = api.post_question("Which colour?", ["Red", "Blue", " "], allow_text=False)
        self.assertEqual(api.question_state(qid), {"answered": False, "answer": "", "cancelled": False})
        shown_id, q = api.current_question()
        self.assertEqual((shown_id, q["choices"], q["allow_text"]), (qid, ["Red", "Blue"], False))
        self.assertTrue(api.answer_question("Blue", qid))
        self.assertEqual(api.question_state(qid), {"answered": True, "answer": "Blue", "cancelled": False})
        self.assertTrue(api.question_state(qid)["cancelled"])          # read once: gone
        self.assertIsNone(api.current_question())

    def test_oldest_first_and_typed_answers(self):
        first = api.post_question("Name it?")                        # no choices: text only
        api.post_question("Second?", ["A", "B"])
        self.assertEqual(api.current_question()[0], first)
        self.assertTrue(api.current_question()[1]["allow_text"])
        self.assertTrue(api.answer_question("Campfire"))              # the oldest open one
        self.assertEqual(api.question_state(first)["answer"], "Campfire")
        self.assertEqual(api.current_question()[1]["question"], "Second?")

    def test_stop_cancels(self):
        qid = api.post_question("Overwrite?", ["Yes", "No"])
        api.cancel_questions()
        self.assertEqual(api.question_state(qid), {"answered": False, "answer": "", "cancelled": True})
        self.assertFalse(api.answer_question("Yes"))
        other = api.post_question("Again?")
        api.cancel_question(other)
        self.assertTrue(api.question_state(other)["cancelled"])


class TestRevert(unittest.TestCase):
    """Engine 6b: an undo step before and after each turn; Revert only while untouched."""

    def setUp(self):
        self.calls = []
        self._undo = api._undo
        api._undo = lambda message="", to_item=-1: self.calls.append(message or to_item) or True
        api._turn.update(before=False, after=False, edits=0)

    def tearDown(self):
        api._undo = self._undo

    def test_turn_checkpoints_and_revert(self):
        self.assertFalse(api.can_revert())
        api.turn_started("Claude Code's turn")
        api.turn_started("Claude Code's turn")            # the timer seeing it again: no second step
        api.turn_finished("Claude Code's turn")
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.calls[0].startswith("Before Claude Code's turn ("))   # a name of its own (#242)
        self.assertEqual(self.calls[1], "Claude Code's turn")
        self.assertTrue(api.can_revert())
        self.assertEqual(api.revert_turn(), "")
        self.assertEqual(self.calls[-1], -1)                # no undo stack here (no bpy): one plain undo
        self.assertFalse(api.can_revert())

    def test_revert_goes_back_to_the_turns_before_step(self):
        # #242: a tool's own undo steps in between; Revert jumps to "Before ..." by name.
        api.turn_started("Coworker's turn")
        api.turn_finished("Coworker's turn")
        names = ["Original", api._turn["name"], "wire_node_group", "Coworker's turn"]
        steps = [types.SimpleNamespace(name=n) for n in names]
        wm = types.SimpleNamespace(undo_stack=types.SimpleNamespace(steps=steps))
        sys.modules["bpy"] = types.SimpleNamespace(context=types.SimpleNamespace(window_manager=wm))
        try:
            self.assertEqual(api.revert_turn(), "")
        finally:
            del sys.modules["bpy"]
        self.assertEqual(self.calls[-1], 1)                 # undo_history(item=1), the turn's Before step

    def test_no_revert_after_you_changed_the_scene(self):
        api.turn_started("Coworker's turn")
        api.turn_finished("Coworker's turn")
        api._count_edit()
        self.assertFalse(api.can_revert())
        self.assertIn("Undo History", api.revert_turn())


class TestPlan(unittest.TestCase):

    def test_update_plan_writes_goal_and_plan(self):
        store = types.SimpleNamespace(goal=gp.GoalPlan())
        fake = types.SimpleNamespace(store=store, store_lock=__import__("threading").Lock())
        sys.modules["bfacw_api_pkg.session_memory"] = fake
        try:
            text = api.update_plan({"steps": ["Pick the colour", "Paint the cube"], "done": [1]})
        finally:
            del sys.modules["bfacw_api_pkg.session_memory"]
        self.assertIn("1/2", text)
        self.assertEqual([s["status"] for s in store.goal.steps], ["done", "doing"])


if __name__ == "__main__":
    unittest.main()
