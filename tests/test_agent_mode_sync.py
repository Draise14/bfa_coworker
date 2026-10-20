# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Coworker / Ask / Plan driving the External Agent's own mode (#221).

Run with::

    python -m unittest tests.test_agent_mode_sync -v
"""

__all__ = ()

import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _funcs():
    src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
    ns: dict = {}
    for name in ("_is_plan_choice", "agent_mode_for", "_modes_cover_agent_mode"):
        start = src.index("def {:s}(".format(name))
        end = src.index("\n\n\n", start)
        exec(compile(src[start:end], "ui_chat.py", "exec"), ns)      # noqa: S102
    return ns


CLAUDE = [("default", "Manual", ""), ("plan", "Plan", "")]
MANY = [("default", "Ask First", ""), ("acceptEdits", "Accept Edits", ""), ("plan", "Plan Mode", "")]


class TestAgentModeFor(unittest.TestCase):

    def test_plan_picks_the_agents_plan_mode(self):
        f = _funcs()["agent_mode_for"]
        self.assertEqual(f("PLAN", CLAUDE, "default"), "plan")
        self.assertIsNone(f("PLAN", CLAUDE, "plan"))                     # already there

    def test_coworker_and_ask_leave_plan_mode_for_the_last_normal_one(self):
        f = _funcs()["agent_mode_for"]
        self.assertEqual(f("AGENT", CLAUDE, "plan"), "default")
        self.assertEqual(f("ASK", MANY, "plan", normal="acceptEdits"), "acceptEdits")
        self.assertIsNone(f("AGENT", MANY, "acceptEdits"))               # a normal mode stays as it is

    def test_no_plan_mode_nothing_changes(self):
        f = _funcs()["agent_mode_for"]
        self.assertIsNone(f("PLAN", [("build", "Build", "")], "build"))

    def test_two_choice_mode_rows_are_covered_by_the_chat_modes(self):
        cover = _funcs()["_modes_cover_agent_mode"]
        self.assertTrue(cover({"choices": CLAUDE}))
        self.assertFalse(cover({"choices": MANY}))


if __name__ == "__main__":
    unittest.main()
