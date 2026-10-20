# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the bridge's activity log (activity.py, task "Agent 9") and its view
under the External Harness card.

1. Each call is recorded: ok / error / waiting, the tool, its input, a short
   preview; newest first; Copy Log text oldest first.
2. The External Harness card lists the calls under it, newest first, and an
   error row uses the error icon.

Run with::

    python -m unittest tests.test_activity -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_activity_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return [importlib.import_module(name + "." + m) for m in ("activity", "ui_gpu.surface", "ui_gpu.layout",
                                                               "ui_gpu.style")]


act, sf, lay, st = _load()


class TestLog(unittest.TestCase):

    def setUp(self):
        act.clear()
        self.changes = []
        act.on_change = lambda: self.changes.append(1)

    def tearDown(self):
        act.on_change = None

    def test_records_newest_first(self):
        act.record("get_objects_summary", '{"limit": 5}', {"status": "ok", "result": {"objects": ["Cube"]}}, 0.04)
        act.record("execute_blender_code", '{"code": "bpy.ops.mesh.foo()"}',
                   {"status": "error", "message": "AttributeError: foo\nmore"}, 0.2)
        act.record("render_viewport_to_path", "{}", {}, 0.01, deferred=True)
        first, second, third = act.entries()
        self.assertEqual([e.status for e in (first, second, third)], ["waiting", "error", "ok"])
        self.assertEqual(second.preview, "AttributeError: foo more")
        self.assertIn("Cube", third.preview)
        self.assertEqual(len(self.changes), 3)
        text = act.log_text()
        self.assertLess(text.index("get_objects_summary"), text.index("render_viewport_to_path"))

    def test_old_server_and_long_inputs(self):
        act.record("", "x" * 500, {"status": "ok", "result": "y" * 500}, 1.0)
        e = act.entries()[0]
        self.assertEqual(e.tool, "code")
        self.assertLessEqual(len(e.args), 120)
        self.assertLessEqual(len(e.preview), 160)


class TestView(unittest.TestCase):

    def test_rows_under_the_harness_card(self):
        notice = sf.Notice(title="External Harness", icon="WORLD", lines=("The chat runs elsewhere.",),
                           buttons=(("bridge", "Start Bridge", "PLAY"),),
                           rows=(("ERROR", "execute_blender_code  {...}", "AttributeError (0.2 s)"),
                                 ("CHECKMARK", "get_objects_summary  {}", "ok (0.0 s)")),
                           rows_title="What your client did in Blender")
        surface = sf.ChatSurface()
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
        surface.draw(canvas, lay.Rect(0, 0, 500, 700), [], notice=notice)
        texts = [c.text for c in canvas.commands if c.kind == "text"]
        self.assertIn("What your client did in Blender", texts)
        joined = " ".join(texts)
        self.assertLess(joined.index("execute_blender_code"), joined.index("get_objects_summary"))
        icons = [c.text for c in canvas.commands if c.kind == "icon"]
        self.assertIn("ERROR", icons)

    def test_empty_log_says_so(self):
        notice = sf.Notice(title="External Harness", icon="WORLD", lines=(), rows_title="What your client did")
        surface = sf.ChatSurface()
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
        surface.draw(canvas, lay.Rect(0, 0, 500, 700), [], notice=notice)
        self.assertTrue(any("Nothing yet" in c.text for c in canvas.commands if c.kind == "text"))


if __name__ == "__main__":
    unittest.main()
