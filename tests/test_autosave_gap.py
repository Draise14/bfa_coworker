# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Blender's autosave with the GPU chat's input router
(ui_gpu/input_capture.py, #370, #488) with a stand-in ``bpy``.

Blender skips autosave while any modal operator runs, and the chat keeps one
running in every window.

1. With Blender's *Save Autosave* (``wm.save_auto_save``) the chat writes the
   autosave itself, by Blender's rules (``autosave_step``): every interval
   from the last autosave or save, nothing in a saved file, never while
   another modal operator runs or in edit / sculpt mode.  The operator never
   steps aside, so every key and click that arrives around an autosave
   reaches the chat (#488).
2. The operator restarts in every window, also while one is maximized (#391).

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


class TestRestart(unittest.TestCase):

    def setUp(self):
        self.bpy = _fake_bpy()
        self.ic = _load(self.bpy)
        self.started = []
        self.ic.bpy.ops.bfacw.gpu_input = self._start
        self.ic.set_host(lambda *_a: None)
        self.ic._RUNNING.update({1, 2})
        self.ic._AUTOSAVE.update(since=0.0)

    def _start(self, _mode):
        self.started.append(_mode)
        self.ic._RUNNING.add(len(self.started))

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


class _Writer:
    """``bpy.ops.wm.save_auto_save``: counts writes; *fail* makes one raise like Blender."""

    def __init__(self):
        self.calls, self.fail = 0, False

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
    """The chat writes the autosave and never steps aside (#488)."""

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
        self.ic._AUTOSAVE.update(since=0.0)

    def _op(self, ptr):
        op = self.ic.BFACW_OT_gpu_input()
        op._window = ptr
        op._handle = lambda *_a: {"RUNNING_MODAL"}      # the chat used the event
        return op

    def test_due_writes_once_without_a_gap(self):
        ic = self.ic
        ic._watch()
        self.assertEqual(self.writer.calls, 1)
        self.assertEqual(self.bpy.context.window_manager.timers, [])
        ic._watch()                                  # the next interval has only just started
        self.assertEqual(self.writer.calls, 1)

    def test_no_key_or_click_is_lost_around_an_autosave(self):
        # Presses before, at and after the autosave moment all reach the chat: the operators
        # never end.
        ic = self.ic
        ops = [self._op(1), self._op(2)]
        events = [types.SimpleNamespace(type=t, value="PRESS") for t in ("A", "LEFTMOUSE", "C", "RIGHTMOUSE")]
        got = sent = 0
        for tick in range(300):
            if tick % 50 == 0:
                ic._AUTOSAVE.update(since=0.0)    # due now
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


if __name__ == "__main__":
    unittest.main()
