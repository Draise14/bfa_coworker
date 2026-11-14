# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Blender's autosave with the GPU chat's input router
(ui_gpu/input_capture.py, #370, #488) with a stand-in ``bpy``.

Blender skips autosave while any modal operator runs, and the chat keeps one
running in every window.

1. With Blender's *Save Autosave* (``wm.save_auto_save``, 5.2+) the chat
   writes the autosave itself, by Blender's rules (``autosave_step``): every
   interval from the last autosave or save, nothing in a saved file, never
   while another modal operator runs or in edit / sculpt mode.  The operator
   never steps aside, so every key and click that arrives around an autosave
   reaches the chat (#488).
2. Without it (Blender 5.1), the gap (#370): ``gap_due`` never with autosave
   off, waits for the interval (30 s, or the autosave interval if shorter) and
   for a lull in keys / clicks; ``_watch`` opens it, every running operator
   ends on its next event (a window timer wakes idle ones), nothing restarts
   while it's open, and closing it removes the timers and restarts the
   operators.  Keys and clicks push the next gap back; plain mouse moves don't.

Run with::

    python -m unittest tests.test_autosave_gap -v
"""

__all__ = ()

import contextlib
import importlib
import io
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
                                    handlers=types.SimpleNamespace(persistent=lambda fn: fn, load_pre=[],
                                                                   save_post=[]))
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

    def test_restart_works_while_a_window_is_maximized(self):
        # Bforartists refuses screen= in temp_override while any window shows a temporary
        # screen (Ctrl+Space); the floating chat then never came back after a gap (#391).
        def override(**kw):
            if "screen" in kw:
                raise TypeError("Overriding context with an active temporary screen isn't supported")
            return _NullCtx()
        self.ic.bpy.context.temp_override = override
        self.ic._RUNNING.clear()
        self.ic.ensure_running()
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


class _Writer:
    """``bpy.ops.wm.save_auto_save``: counts writes; *fail* makes one raise like Blender."""

    def __init__(self, present=True):
        self.present, self.calls, self.fail = present, 0, False

    def get_rna_type(self):
        if not self.present:
            raise KeyError("bpy.ops.wm.save_auto_save: not found")
        return object()

    def __call__(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Error: cannot write the autosave")
        return {"FINISHED"}


class TestAutosaveStep(unittest.TestCase):
    """Blender's autosave rules, as the chat applies them (#488)."""

    @classmethod
    def setUpClass(cls):
        cls.ic = _load(_fake_bpy())

    def step(self, now, since=100.0, on=True, minutes=2, dirty=True, busy=False, undo_ok=True):
        return self.ic.autosave_step(now, since, on, minutes, dirty, busy, undo_ok)

    def test_never_with_autosave_off(self):
        self.assertEqual(self.step(10000.0, on=False), "wait")

    def test_due_one_watch_tick_before_blenders_interval(self):
        self.assertEqual(self.step(218.9), "wait")          # 2 min from 100 s, less the 1 s tick
        self.assertEqual(self.step(219.0), "write")

    def test_a_saved_file_starts_the_next_interval(self):
        self.assertEqual(self.step(300.0, dirty=False), "skip")

    def test_waits_for_another_modal_operator_and_for_edit_mode(self):
        self.assertEqual(self.step(300.0, busy=True), "wait")
        self.assertEqual(self.step(300.0, undo_ok=False), "wait")


class TestAutosaveWriter(unittest.TestCase):
    """Blender 5.2+: the chat writes the autosave and never steps aside (#488)."""

    def setUp(self):
        self.bpy = _fake_bpy()
        self.writer = _Writer()
        self.bpy.ops.wm = types.SimpleNamespace(save_auto_save=self.writer)
        self.bpy.data = types.SimpleNamespace(is_dirty=True)
        self.bpy.context.mode = "OBJECT"
        self.bpy.context.preferences.edit = types.SimpleNamespace(use_global_undo=True)
        for window in self.bpy.context.window_manager.windows:
            window.modal_operators = [types.SimpleNamespace(bl_idname="BFACW_OT_gpu_input")]
        self.ic = _load(self.bpy)
        self.ic.bpy.ops.bfacw.gpu_input = lambda _mode: None
        self.ic.set_host(lambda *_a: None)
        self.ic._RUNNING.update({1, 2})
        self.ic._AUTOSAVE.update(paused=False, last_gap=0.0, last_input=0.0, timers=[], since=0.0, writer=None)

    def _op(self, ptr):
        op = self.ic.BFACW_OT_gpu_input()
        op._window = ptr
        op._handle = lambda *_a: {"RUNNING_MODAL"}      # the chat used the event
        return op

    def test_due_writes_once_without_a_gap(self):
        ic = self.ic
        ic._watch()
        self.assertEqual(self.writer.calls, 1)
        self.assertFalse(ic._AUTOSAVE["paused"])
        self.assertEqual(self.bpy.context.window_manager.timers, [])
        self.assertNotIn(ic._close_gap, self.bpy.app.timers.registered)
        ic._watch()                                     # the next interval has only just started
        self.assertEqual(self.writer.calls, 1)

    def test_no_key_or_click_is_lost_around_an_autosave(self):
        # Presses before, at and after the autosave moment all reach the chat: the operators
        # never end, whatever the gap rules would have said (a lull, 30 s since the last gap).
        ic = self.ic
        ops = [self._op(1), self._op(2)]
        events = [types.SimpleNamespace(type=t, value="PRESS") for t in ("A", "LEFTMOUSE", "C", "RIGHTMOUSE")]
        got = sent = 0
        for tick in range(300):
            if tick % 50 == 0:
                ic._AUTOSAVE.update(since=0.0, last_gap=0.0, last_input=0.0)    # due now
            if tick % 3 == 0:
                ic._watch()
            for op in ops:
                event = events[tick % len(events)]
                sent += 1
                if op.modal(None, event) == {"RUNNING_MODAL"}:
                    got += 1
        self.assertEqual(self.writer.calls, 6)
        self.assertEqual(got, sent)
        self.assertEqual(ic._RUNNING, {1, 2})
        self.assertEqual(self.bpy.context.window_manager.timers, [])

    def test_a_saved_file_is_not_written(self):
        self.bpy.data.is_dirty = False
        self.ic._watch()
        self.assertEqual(self.writer.calls, 0)
        self.assertGreater(self.ic._AUTOSAVE["since"], 0.0)     # like Blender: the next interval starts

    def test_waits_while_another_modal_operator_runs(self):
        window = self.bpy.context.window_manager.windows[1]
        window.modal_operators.append(types.SimpleNamespace(bl_idname="TRANSFORM_OT_translate"))
        self.ic._watch()
        self.assertEqual(self.writer.calls, 0)
        window.modal_operators.pop()
        self.ic._watch()
        self.assertEqual(self.writer.calls, 1)

    def test_waits_in_edit_mode_unless_global_undo_is_off(self):
        self.bpy.context.mode = "EDIT_MESH"
        self.ic._watch()
        self.assertEqual(self.writer.calls, 0)
        self.bpy.context.preferences.edit.use_global_undo = False
        self.ic._watch()
        self.assertEqual(self.writer.calls, 1)

    def test_a_failed_write_is_reported_not_raised(self):
        self.writer.fail = True
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.ic._watch()
        self.assertIn("autosave failed", out.getvalue())
        self.assertGreater(self.ic._AUTOSAVE["since"], 0.0)

    def test_a_save_restarts_the_interval(self):
        self.ic._on_save_post()
        self.ic._watch()
        self.assertEqual(self.writer.calls, 0)

    def test_without_the_operator_the_gap_is_used(self):
        self.writer.present = False
        self.ic._watch()
        self.assertEqual(self.writer.calls, 0)
        self.assertTrue(self.ic._AUTOSAVE["paused"])


if __name__ == "__main__":
    unittest.main()
