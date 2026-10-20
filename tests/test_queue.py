# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the queue's per-message actions (#197): move a message to the
front, take one out (edit / delete / send now), on both queues -- the local
loop's MessageQueue and the External Agent's.

Run with::

    python -m unittest tests.test_queue -v
"""

__all__ = ()

import importlib
import os
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _message_queue_class():
    src = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
    start = src.index("@dataclass\nclass MessageQueue:")
    end = src.index("\n\n\n", src.index("class MessageQueue:"))
    ns: dict = {}
    exec(compile("from __future__ import annotations\nimport threading, time\n"     # noqa: S102
                 "from dataclasses import dataclass, field\nfrom typing import Any\n" + src[start:end],
                 "agent_controller.py", "exec"), ns)
    return ns["MessageQueue"]


def _load_acp():
    name = "bfacw_queue_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".engine_acp"), importlib.import_module(name + ".engine")


class TestLocalQueue(unittest.TestCase):

    def test_move_and_remove(self):
        q = _message_queue_class()()
        for text in ("one", "two", "three"):
            q.enqueue(text)
        self.assertTrue(q.move_to_front(2))
        self.assertEqual([i["message"] for i in q.get_all()], ["three", "one", "two"])
        self.assertEqual(q.remove(1)["message"], "one")
        self.assertIsNone(q.remove(5))
        self.assertFalse(q.move_to_front(-1))
        self.assertEqual([i["message"] for i in q.get_all()], ["three", "two"])


class TestAgentQueue(unittest.TestCase):

    def test_move_and_remove(self):
        ea, eng = _load_acp()
        state = types.SimpleNamespace(conversation_history=[], streaming_text="", reasoning_text="",
                                      is_thinking=False, thinking_start_time=0.0, turn_phase="",
                                      ui_status="", error="")
        engine = ea.AcpEngine(state)
        engine._turn_id = "running"
        for text in ("one", "two", "three"):
            engine.submit(eng.Prompt(text=text))
        self.assertTrue(engine.queue_to_front(2))
        self.assertEqual([i["message"] for i in engine.queue_items()], ["three", "one", "two"])
        self.assertEqual(engine.remove_queued(0).text, "three")
        self.assertIsNone(engine.remove_queued(9))
        self.assertEqual([i["message"] for i in engine.queue_items()], ["one", "two"])


if __name__ == "__main__":
    unittest.main()
