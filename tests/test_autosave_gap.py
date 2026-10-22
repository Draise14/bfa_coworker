# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the autosave gap in the GPU chat's input router
(ui_gpu/input_capture.py, #370) with a stand-in ``bpy``.

Blender skips autosave while any modal operator runs, and the chat keeps one
running in every window, so the router must step aside now and then.

1. ``gap_due``: never with autosave off; waits for the interval (30 s, or the
   autosave interval if shorter) and for a lull in keys / clicks.
2. The gap: ``_watch`` opens it, every running operator ends on its next
   event (a window timer wakes idle ones), nothing restarts while it's open,
   and closing it removes the timers and restarts the operators.
3. Keys and clicks push the next gap back; plain mouse moves don't.

Run with::

    python -m unittest tests.test_autosave_gap -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


class _Window:
    def __init__(self, ptr):
        self._ptr, self.screen = ptr, object()

    def as_pointer(self):
        return self._ptr


class _WM:
    def __init__(self):
        self.windows = [_Window(1), _Window(2)]
        self.timers, self.removed = [], []

    def event_timer_add(self, step, window=None):
        timer = (step, window.as_pointer())
        self.timers.append(timer)
        return timer

    def event_timer_remove(self, timer):
        self.removed.append(timer)


class _Timers:
    def __init__(self):
        self.registered = {}

    def register(self, fn, first_interval=0.0, persistent=False):
        self.registered[fn] = (first_interval, persistent)

    def is_registered(self, fn):
        return fn in self.registered

    def unregister(self, fn):
        self.registered.pop(fn, None)


def _fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(Operator=object)
    bpy.props = types.SimpleNamespace(StringProperty=lambda **_kw: None)
    bpy.app = types.SimpleNamespace(timers=_Timers(),
                                    handlers=types.SimpleNamespace(persistent=lambda fn: fn, load_pre=[]))
    paths = types.SimpleNamespace(use_auto_save_temporary_files=True, auto_save_time=2)
    bpy.context = types.SimpleNamespace(window_manager=_WM(),
                                        preferences=types.SimpleNamespace(filepaths=paths),
                                        temp_override=lambda **_kw: _NullCtx())
    bpy.ops = types.SimpleNamespace(bfacw=types.SimpleNamespace(gpu_input=None))
    return bpy


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _load(bpy):
    saved = sys.modules.get("bpy")
    sys.modules["bpy"] = bpy
    try:
        name = "bfacw_autosave_gap_pkg"
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(_ADDON)]
            sys.modules[name] = pkg
        sys.modules.pop(name + ".ui_gpu.input_capture", None)
        return importlib.import_module(name + ".ui_gpu.input_capture")
    finally:
        if saved is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = saved


class TestGapDue(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ic = _load(_fake_bpy())

    def test_never_with_autosave_off(self):
        self.assertFalse(self.ic.gap_due(1000.0, 0.0, 0.0, False, 2))

    def test_waits_for_the_interval(self):
        due = self.ic.gap_due
        self.assertFalse(due(129.0, 100.0, 0.0, True, 2))
        self.assertTrue(due(130.0, 100.0, 0.0, True, 2))

    def test_short_autosave_interval_is_never_exceeded(self):
        # 30 s cap even for a long autosave interval; a 1-minute interval still gaps every 30 s.
        self.assertTrue(self.ic.gap_due(130.0, 100.0, 0.0, True, 60))
        self.assertTrue(self.ic.gap_due(130.0, 100.0, 0.0, True, 1))

    def test_waits_for_a_typing_lull(self):
        due = self.ic.gap_due
        self.assertFalse(due(200.0, 100.0, 199.0, True, 2))
        self.assertTrue(due(200.0, 100.0, 198.0, True, 2))


class TestGap(unittest.TestCase):

    def setUp(self):
        self.bpy = _fake_bpy()
        self.ic = _load(self.bpy)
        self.started = []
        self.ic.bpy.ops.bfacw.gpu_input = self._start
        self.ic.set_host(lambda *_a: None)
        self.ic._RUNNING.update({1, 2})
        self.ic._AUTOSAVE.update(paused=False, last_gap=0.0, last_input=0.0, timers=[])

    def _start(self, _mode):
        self.started.append(_mode)
        self.ic._RUNNING.add(len(self.started))

    def _op(self, ptr):
        op = self.ic.BFACW_OT_gpu_input()
        op._window = ptr
        return op

    def test_watch_opens_the_gap_and_operators_end(self):
        ic, wm = self.ic, self.bpy.context.window_manager
        ic._watch()
        self.assertTrue(ic._AUTOSAVE["paused"])
        self.assertEqual([t[1] for t in wm.timers], [1, 2])     # each window gets a wake-up timer
        self.assertIn(ic._close_gap, self.bpy.app.timers.registered)
        self.assertTrue(self.bpy.app.timers.registered[ic._close_gap][1])   # survives a file load
        for ptr in (1, 2):
            event = types.SimpleNamespace(type="TIMER", value="NOTHING")
            self.assertEqual(self._op(ptr).modal(None, event), {"FINISHED", "PASS_THROUGH"})
        self.assertFalse(ic._RUNNING)

    def test_nothing_restarts_while_open_then_close_restarts(self):
        ic, wm = self.ic, self.bpy.context.window_manager
        ic._open_gap()
        ic._RUNNING.clear()
        ic._watch()
        ic.ensure_running()
        self.assertEqual(self.started, [])
        ic._close_gap()
        self.assertFalse(ic._AUTOSAVE["paused"])
        self.assertEqual(wm.removed, wm.timers)
        self.assertEqual(len(self.started), 2)

    def test_no_gap_with_autosave_off(self):
        self.bpy.context.preferences.filepaths.use_auto_save_temporary_files = False
        self.ic._watch()
        self.assertFalse(self.ic._AUTOSAVE["paused"])

    def test_keys_push_the_gap_back_but_moves_dont(self):
        ic = self.ic
        ic._AUTOSAVE["last_input"] = 0.0
        op = self._op(1)
        op._handle = lambda *_a: {"PASS_THROUGH"}
        op.modal(None, types.SimpleNamespace(type="MOUSEMOVE", value="NOTHING"))
        self.assertEqual(ic._AUTOSAVE["last_input"], 0.0)
        op.modal(None, types.SimpleNamespace(type="A", value="PRESS"))
        self.assertGreater(ic._AUTOSAVE["last_input"], 0.0)


if __name__ == "__main__":
    unittest.main()
