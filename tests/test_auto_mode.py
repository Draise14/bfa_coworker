# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Auto mode's decisions (auto_mode.py, #177).

1. A good turn with steps left continues; the queue goes first.
2. It stops: plan done, no plan, Stop, two errors, two turns without
   progress, the time and turn limits.
3. Timeouts are retried (up to three in a row).

Run with::

    python -m unittest tests.test_auto_mode -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_auto_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".auto_mode")


am = _load()


def _turn(run, done, total=5, error="", cancelled=False, queued=0, now=1000.0):
    return run.after_turn(ok=not error, error=error, cancelled=cancelled, done=done, total=total,
                          queued=queued, now=now)


class TestAutoRun(unittest.TestCase):

    def test_continues_while_on_track(self):
        run = am.AutoRun(started=1000.0)
        self.assertEqual(_turn(run, 1), "continue")
        self.assertEqual(_turn(run, 2, queued=1), "wait")              # your queue goes first
        self.assertEqual(_turn(run, 3), "continue")
        self.assertEqual(run.turns, 2)
        self.assertEqual(_turn(run, 5), "stop")
        self.assertIn("plan is done", run.stopped)

    def test_stops_without_progress_or_plan_or_on_stop(self):
        run = am.AutoRun(started=1000.0)
        _turn(run, 2)
        _turn(run, 2)
        self.assertEqual(_turn(run, 2), "stop")
        self.assertIn("no progress", run.stopped)
        self.assertEqual(_turn(am.AutoRun(), 0, total=0), "stop")
        run = am.AutoRun()
        self.assertEqual(_turn(run, 1, cancelled=True), "stop")
        self.assertIn("Stop", run.stopped)

    def test_errors_and_timeouts(self):
        run = am.AutoRun(started=1000.0)
        self.assertEqual(_turn(run, 1, error="Model crashed"), "continue")
        self.assertEqual(_turn(run, 1, error="Model crashed again"), "stop")
        run = am.AutoRun(started=1000.0)
        for _ in range(3):
            self.assertEqual(_turn(run, 1, error="Request timed out after 300 s"), "continue")
        self.assertEqual(_turn(run, 1, error="Read timed out"), "stop")
        self.assertIn("timing out", run.stopped)

    def test_limits_and_summary(self):
        run = am.AutoRun(started=1000.0, max_seconds=3600)
        self.assertEqual(_turn(run, 1, now=1000.0 + 3601), "stop")
        self.assertIn("time limit", run.stopped)
        run = am.AutoRun(started=1000.0, max_turns=1)
        _turn(run, 1)
        self.assertEqual(_turn(run, 2), "stop")
        self.assertIn("turn limit", run.stopped)
        text = run.summary(2, 5, now=1000.0 + 5400)
        self.assertIn("1 extra turn", text)
        self.assertIn("1 h 30 min", text)
        self.assertIn("2/5", text)

    def test_continue_prompt(self):
        text = am.continue_prompt("Add the logs", 1, 3)
        self.assertTrue(text.startswith("[Auto mode]"))
        self.assertIn("Add the logs", text)


class TestAnswersForYou(unittest.TestCase):
    """While Auto runs: questions are answered, the agent's requests allowed (settings permitting)."""

    def test_question_answered_at_once(self):
        api = importlib.import_module("bfacw_auto_pkg.coworker_api")
        api.auto_answer[0] = am.QUESTION_ANSWER
        try:
            qid = api.post_question("Which colour?", ["Red", "Blue"])
            self.assertEqual(api.question_state(qid)["answer"], am.QUESTION_ANSWER)
        finally:
            api.auto_answer[0] = ""
        qid = api.post_question("Which colour?", ["Red", "Blue"])
        self.assertFalse(api.question_state(qid)["answered"])            # off again: it waits
        api.cancel_question(qid)

    def test_agent_requests_allowed_but_refusals_stay(self):
        ea = importlib.import_module("bfacw_auto_pkg.engine_acp")
        state = types.SimpleNamespace(conversation_history=[], streaming_text="", reasoning_text="",
                                      is_thinking=False, thinking_start_time=0.0, turn_phase="",
                                      ui_status="", error="")
        engine = ea.AcpEngine(state)
        engine._turn_id, engine.auto_approve, engine.ask_coworker_tools = "t1", True, True
        options = [{"optionId": "a1", "kind": "allow_once"}, {"optionId": "r1", "kind": "reject_once"}]
        answers = []
        engine._on_permission({"toolCall": {"title": "mcp__bfa-coworker__execute_blender_code"},
                               "options": options}, answers.append)
        self.assertEqual(answers, ["a1"])                                   # no card: allowed
        self.assertIsNone(engine.approvals.current())
        engine._on_permission({"toolCall": {"title": "bash"}, "options": options}, answers.append)
        self.assertEqual(answers[-1], "r1")                                 # Only Coworker Tools still refuses

    def test_goal_stays_yours(self):
        gp = importlib.import_module("bfacw_auto_pkg.goal_plan")
        goal = gp.GoalPlan()
        goal.set_request("Build a campfire")
        goal.set_request(am.continue_prompt("Logs", 1, 3))
        self.assertEqual(goal.turn_goal, "Build a campfire")


if __name__ == "__main__":
    unittest.main()
