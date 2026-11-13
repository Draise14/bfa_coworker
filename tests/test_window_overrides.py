# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the window-only context overrides in ui_chat.py (#401, after #391),
with a stand-in ``bpy`` that refuses ``screen=`` the way Bforartists does
while any window shows a temporary screen (a maximized area, Ctrl+Space).

1. Attach Screen: the other windows still join in.
2. Attach Screen: the part you picked is still taken (the timer after it).
3. Auto mode: the next step is still sent.

Run with::

    python -m unittest tests.test_window_overrides -v
"""

__all__ = ()

import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _refusing_override(**kw):
    if "screen" in kw:
        raise TypeError("Overriding context with an active temporary screen isn't supported")
    return _NullCtx()


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Window:
    def __init__(self, ptr):
        self._ptr, self.screen = ptr, types.SimpleNamespace(areas=[])

    def as_pointer(self):
        return self._ptr

    def cursor_modal_set(self, _cursor):
        pass

    def cursor_modal_restore(self):
        pass


class _Timers:
    def __init__(self):
        self.registered = []

    def register(self, fn, first_interval=0.0, persistent=False):
        self.registered.append(fn)


def _fake_bpy(calls):
    def op(name):
        return lambda *args, **kw: calls.append((name, args, kw))
    bpy = types.SimpleNamespace()
    bpy.types = types.SimpleNamespace(Context=object, Operator=object)
    bpy.props = types.SimpleNamespace(IntVectorProperty=lambda **_kw: None)
    bpy.app = types.SimpleNamespace(background=False, timers=_Timers())
    bpy.ops = types.SimpleNamespace(bfacw=types.SimpleNamespace(
        chat_capture_screen=op("chat_capture_screen"), chat_send=op("chat_send")))
    bpy.context = types.SimpleNamespace(temp_override=_refusing_override)
    return bpy


def _source(start_marker):
    src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
    start = src.index(start_marker)
    end = src.index("\n\n\n", start)
    return src[start:end]


def _capture_operator(bpy):
    ns = {
        "bpy": bpy, "Operator": object, "BoolProperty": lambda **_kw: None,
        "StringProperty": lambda **_kw: None,
        "_SHOT": {"handlers": [], "rects": {}, "hover": {}, "session": 0, "active": False,
                  "last": {}, "thread": "", "timers": [], "cursors": set()},
        "_SHOT_TICK": 0.05,
        "_attach_thread": lambda _context, thread_id="": types.SimpleNamespace(id=thread_id or "t-shown"),
        "_shot_timer_remove": lambda _wm, _timer: None, "_shot_clear_windows": lambda _wm: None,
        "_shot_handlers_add": lambda: None, "_shot_handlers_remove": lambda: None,
        "_shot_redraw": lambda _window: None, "_shot_status": None,
        "_shot_rect": lambda a, b, square=False: (*a, *b),
        "_area_rect_at": lambda _window, _x, _y: (0, 0, 100, 100),
    }
    exec(compile(_source("class BFACW_OT_chat_capture_screen("), "ui_chat.py", "exec"), ns)  # noqa: S102
    op = ns["BFACW_OT_chat_capture_screen"]()
    op.p1, op.p2, op.use_rect, op.sibling, op.thread_id = (0, 0), (0, 0), False, False, ""
    return op


class TestWhileAWindowIsMaximized(unittest.TestCase):

    def setUp(self):
        self.calls = []
        self.bpy = _fake_bpy(self.calls)
        self.main, self.floating = _Window(1), _Window(2)
        wm = types.SimpleNamespace(windows=[self.main, self.floating],
                                   modal_handler_add=lambda _op: None,
                                   event_timer_add=lambda _step, window=None: object())
        self.context = types.SimpleNamespace(
            window=self.main, window_manager=wm, temp_override=_refusing_override,
            workspace=types.SimpleNamespace(status_text_set=lambda _text: None))

    def test_attach_screen_reaches_the_other_windows(self):
        op = _capture_operator(self.bpy)
        event = types.SimpleNamespace(mouse_x=10, mouse_y=10)
        self.assertEqual(op.invoke(self.context, event), {'RUNNING_MODAL'})
        self.assertEqual(self.calls, [("chat_capture_screen", ("INVOKE_DEFAULT",), {"sibling": True})])

    def test_attach_screen_takes_the_picked_part(self):
        op = _capture_operator(self.bpy)
        op.invoke(self.context, types.SimpleNamespace(mouse_x=10, mouse_y=10))
        self.calls.clear()
        self.assertEqual(op._take(self.context, (0, 0, 50, 40)), {'FINISHED'})
        for later in self.bpy.app.timers.registered:
            later()
        self.assertEqual(self.calls, [("chat_capture_screen", ("EXEC_DEFAULT",),
                                       {"p1": (0, 0), "p2": (50, 40), "use_rect": True,
                                        "thread_id": "t-shown"})])

    def test_auto_mode_sends_the_next_step(self):
        props = types.SimpleNamespace(chat_input="")
        self.bpy.context.window = self.main
        self.bpy.context.window_manager = types.SimpleNamespace(windows=[self.main], bfacw_chat_props=props)
        state = types.SimpleNamespace(is_thinking=False)
        # Auto mode runs in one thread (#417): its next step goes to that thread, as text.
        thread = types.SimpleNamespace(id="t3", auto={"run": object()})
        ns = {"bpy": self.bpy,
              "agent_controller": types.SimpleNamespace(thread_state=lambda _t: state)}
        exec(compile(_source("def _auto_send("), "ui_chat.py", "exec"), ns)  # noqa: S102
        ns["_auto_send"](thread, "Next: step 2")
        self.assertEqual(props.chat_input, "", "the thread's own draft is left alone")
        self.assertEqual(self.calls, [("chat_send", (), {"thread_id": "t3", "text": "Next: step 2"})])


if __name__ == "__main__":
    unittest.main()
