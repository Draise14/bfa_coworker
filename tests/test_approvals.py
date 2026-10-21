# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for approvals.py (Tier 4, Engine 5 / Agent 5): the agent's "may I?"
questions answered in the chat.

1. Oldest question first; each answer reaches its own request, once.
2. "Always" lets the same tool through for the session and answers the
   other open questions for that tool; a new session forgets it.
3. Stop (cancel_all) answers every open question "cancel".
4. The detail line is the tool's input, short.

Run with::

    python -m unittest tests.test_approvals -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_approvals_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".approvals")


ap = _load()


class TestApprovals(unittest.TestCase):

    def setUp(self):
        self.box = ap.Approvals()
        self.answers = []

    def _ask(self, tool, **kw):
        request = ap.ApprovalRequest(tool=tool, source="Claude Code", **kw)
        self.box.ask(request, lambda choice, t=tool: self.answers.append((t, choice)))
        return request

    def test_oldest_first_and_answered_once(self):
        self._ask("bash")
        second = self._ask("write")
        self.assertEqual(self.box.current().tool, "bash")
        self.assertEqual(self.box.count(), 2)
        self.assertTrue(self.box.answer("deny"))
        self.assertTrue(self.box.answer("once", second.id))
        self.assertFalse(self.box.answer("once"))            # nothing left
        self.assertEqual(self.answers, [("bash", "deny"), ("write", "once")])
        with self.assertRaises(ValueError):
            self.box.answer("maybe")

    def test_allow_this_turn_covers_every_tool_until_the_next_turn(self):
        # "Always" = Allow this turn: every tool waiting now, and every one asked later in the turn.
        self._ask("bash")
        self._ask("bash")
        self._ask("write")
        self.box.answer("always")
        self.assertEqual(self.answers, [("bash", "always"), ("bash", "always"), ("write", "always")])
        self.assertIsNone(self.box.current())
        self.assertTrue(self.box.allowed_always("read"))            # a tool not asked about yet
        self.box.new_turn()                                          # your next message
        self.assertFalse(self.box.allowed_always("bash"))
        self._ask("bash")
        self.box.end_session()
        self.assertEqual(self.answers[-1], ("bash", "cancel"))

    def test_stop_cancels_everything(self):
        changes = []
        self.box.on_change = lambda: changes.append(1)
        self._ask("bash")
        self._ask("write")
        self.box.cancel_all()
        self.assertEqual([c for _t, c in self.answers], ["cancel", "cancel"])
        self.assertIsNone(self.box.current())
        self.assertTrue(changes)

    def test_title_and_detail(self):
        request = self._ask("execute_blender_code",
                            args={"code": "import bpy\n\nbpy.ops.mesh.primitive_cube_add()"})
        self.assertEqual(request.title, "Claude Code wants to run execute_blender_code")
        self.assertEqual(request.detail, "import bpy bpy.ops.mesh.primitive_cube_add()")
        long = ap.ApprovalRequest(tool="bash", args={"command": "x" * 500, "cwd": "/tmp"})
        self.assertLessEqual(len(long.detail), 160)
        self.assertEqual(ap.ApprovalRequest(tool="t").title, "The agent wants to run t")
        self.assertEqual(ap.ApprovalRequest(tool="t").detail, "")


if __name__ == "__main__":
    unittest.main()
