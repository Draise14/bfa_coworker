# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Images attach to the chat you used (#449), and the screen capture always cleans up (#454).

1. An attach names its thread: with no area in context (a timer, the file browser) the
   image still goes to the tab whose button was used, not to "All editors".  A native
   set (the socket's browse) still goes to the area's chat.
2. ``bfacw.chat_attach_image`` keeps the tab it was started from across the file browser.
3. Capture: every way out -- take, Esc, right-click, cancel(), a file load, an error in
   modal or in invoke -- leaves ``_SHOT`` clean (no veil, flag, timer or crosshair) and the
   status text cleared in every window; the highlight follows the mouse on every run, Enter
   takes the last part again, a sibling window's instance ends on its next timer tick, and
   the screenshot goes to the thread found when the capture started.
4. A format Blender reads but a vision backend may not (EXR, TGA...) is attached as a
   PNG copy instead of being refused.

Run with::

    python -m unittest tests.test_image_attach_capture -v
"""

__all__ = ()

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _module(name):
    spec = importlib.util.spec_from_file_location("bfacw_iac_" + name, _ADDON / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod        # dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


threads = _module("threads")


def _slice(src, start_marker, end_marker):
    start = src.index(start_marker)
    return src[start:src.index(end_marker, start)]


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


# -- 1, 2: the attach goes to the thread it was started for ------------------------------

class _Context:
    def __init__(self, area=None):
        self.area = area
        self.window_manager = None


class _Props:
    """``chat_image`` with the RNA update ui_chat registers on it."""

    def __init__(self, update, bpy):
        self._image, self._update, self._bpy = None, update, bpy

    @property
    def chat_image(self):
        return self._image

    @chat_image.setter
    def chat_image(self, value):
        self._image = value
        self._update(self, self._bpy.context)


class _Image:
    def __init__(self, name):
        self.name = name


class TestAttachGoesToItsThread(unittest.TestCase):

    def setUp(self):
        src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        self.reg = threads.ThreadRegistry()
        self.socket = threads.AttachmentSocket()
        self.tab = self.reg.create(editor="", title="Chat")
        self.pins = {"chat-area": self.tab.id}
        self.bpy = types.SimpleNamespace(context=_Context())
        reg, socket, pins = self.reg, self.socket, self.pins

        def ui_thread(context=None):        # as ui_chat._ui_thread: the area's pin, else the default
            area = getattr(context, "area", None)
            return reg.get(pins.get(area, "")) or reg.default if area is not None else reg.default

        self.ns = {
            "bpy": self.bpy, "_ui_thread": ui_thread, "_sticky_relinking": False,
            "_forget_attachment": lambda: None, "_remember_attachment": lambda _img: None,
            "_socket_set_by": lambda thread, image: socket.set_by(reg, thread, image.name if image else ""),
            "_ensure_attachment_preview": lambda _img: None,
            "agent_controller": types.SimpleNamespace(THREADS=reg),
            "chat_attachments": types.SimpleNamespace(attachment_name=lambda img: img.name),
        }
        code = (_slice(src, "_ATTACH_TARGET: dict =", "\n\n\nclass ChatHistoryProperties(")
                + "\n\n\n" + _slice(src, "def _attach_thread(", "\n\n\nclass BFACW_OT_chat_send("))
        exec(compile(code, "ui_chat.py", "exec"), self.ns)  # noqa: S102
        self.props = _Props(self.ns["_on_chat_image_update"], self.bpy)
        wm = types.SimpleNamespace(bfacw_chat_props=self.props)
        self.bpy.context.window_manager = wm
        self.reports = []
        self.op = types.SimpleNamespace(report=lambda _level, text: self.reports.append(text))

    def test_no_area_goes_to_the_given_thread(self):
        context = self.bpy.context                      # a timer, the file browser: no area
        self.assertIsNone(context.area)
        self.ns["_attach_to_socket"](context, self.op, _Image("shot.png"), self.tab)
        self.assertEqual(self.tab.attachment, "shot.png", "the image is the tab's")
        self.assertEqual(self.socket.focus, self.tab.id, "the socket holds the tab's image now")
        self.assertEqual(self.reg.default.attachment, "", "All editors keeps what it had")
        self.assertIsNone(self.ns["_ATTACH_TARGET"]["thread"], "the target is not left set")
        self.assertEqual(self.reports, ["Attached shot.png"])

    def test_without_a_thread_the_area_decides(self):
        context = _Context(area="chat-area")
        context.window_manager = self.bpy.context.window_manager
        self.ns["_attach_to_socket"](context, None, _Image("render.png"))
        self.assertEqual(self.tab.attachment, "render.png")

    def test_a_native_set_is_the_area_chat(self):
        self.bpy.context.area = "chat-area"             # the socket's own browse, in the tab's area
        self.props.chat_image = _Image("picked.png")
        self.assertEqual(self.tab.attachment, "picked.png")
        self.bpy.context.area = None
        self.props.chat_image = _Image("other.png")     # no area, no thread named: All editors, as before
        self.assertEqual(self.reg.default.attachment, "other.png")

    def test_a_closed_tab_falls_back_to_the_area(self):
        context = _Context(area="chat-area")
        self.assertIs(self.ns["_attach_thread"](context, self.tab.id), self.tab)
        self.assertIs(self.ns["_attach_thread"](_Context(), "gone"), self.reg.default)

    def test_attach_image_operator_keeps_the_tab_across_the_file_browser(self):
        src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
        attached, browsers = [], []
        image = _Image("photo.exr.png")
        ns_ui = types.SimpleNamespace(
            _attach_thread=self.ns["_attach_thread"],
            _attach_to_socket=lambda context, op, img, thread=None: attached.append((img, thread)))
        ns_ca = types.SimpleNamespace(load_image_file=lambda path: image if path else None)
        pkg = types.ModuleType("bfacw_iac_pkg")
        pkg.__path__ = []
        pkg.ui_chat, pkg.chat_attachments = ns_ui, ns_ca
        saved = {k: sys.modules.get(k) for k in ("bfacw_iac_pkg",)}
        sys.modules["bfacw_iac_pkg"] = pkg
        try:
            props = types.SimpleNamespace(StringProperty=lambda **_kw: "", BoolProperty=lambda **_kw: False)
            bpy = types.SimpleNamespace(types=types.SimpleNamespace(Operator=object),
                                        props=props)
            ns = {"bpy": bpy, "__name__": "bfacw_iac_pkg.chat_editor", "__package__": "bfacw_iac_pkg",
                  "_tag_chat_regions": lambda: None}
            exec(compile(_slice(src, "class BFACW_OT_chat_attach_image(", "\n\n\nclass "),  # noqa: S102
                         "chat_editor.py", "exec"), ns)
            op = ns["BFACW_OT_chat_attach_image"]()
            op.thread_id, op.filepath = "", ""
            op.report = lambda *_a: None
            wm = types.SimpleNamespace(fileselect_add=lambda o: browsers.append(o))
            area_ctx = types.SimpleNamespace(area="chat-area", window_manager=wm)
            self.assertEqual(op.invoke(area_ctx, None), {"RUNNING_MODAL"})
            self.assertEqual(op.thread_id, self.tab.id, "the tab is kept when the browser opens")
            op.filepath = "C:/pics/photo.exr"
            self.assertEqual(op.execute(types.SimpleNamespace(area=None)), {"FINISHED"})
            self.assertEqual(attached, [(image, self.tab)], "attached to the tab, with no area in context")
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


# -- 3: the capture's lifecycle -------------------------------------------------------------

class _Area:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h

    def tag_redraw(self):
        pass


class _Workspace:
    def __init__(self):
        self.status = "idle"
        self.fail = None

    def status_text_set(self, text):
        if self.fail is not None and text is not None:
            raise self.fail
        self.status = text


class _Window:
    def __init__(self, ptr, areas):
        self._ptr = ptr
        self.screen = types.SimpleNamespace(areas=areas)
        self.workspace = _Workspace()
        self.cursor = None

    def as_pointer(self):
        return self._ptr

    def cursor_modal_set(self, cursor):
        self.cursor = cursor

    def cursor_modal_restore(self):
        self.cursor = None


class _WM:
    def __init__(self, windows):
        self.windows = windows
        self.modal = []
        self.timers = []

    def modal_handler_add(self, op):
        self.modal.append(op)

    def event_timer_add(self, step, window=None):
        timer = types.SimpleNamespace(step=step, window=window)
        self.timers.append(timer)
        return timer

    def event_timer_remove(self, timer):
        self.timers.remove(timer)


class _Space:
    """One space type, so handlers are really added and removed."""
    live: list = []

    @classmethod
    def draw_handler_add(cls, fn, args, region, kind):
        handle = object()
        cls.live.append(handle)
        return handle

    @classmethod
    def draw_handler_remove(cls, handle, region):
        cls.live.remove(handle)


def _event(type_, value="PRESS", x=10, y=10):
    return types.SimpleNamespace(type=type_, value=value, mouse_x=x, mouse_y=y)


class TestCaptureLifecycle(unittest.TestCase):

    def setUp(self):
        _Space.live = []
        self.left, self.right = _Area(0, 0, 100, 100), _Area(100, 0, 100, 100)
        self.w1 = _Window(1, [self.left, self.right])
        self.w2 = _Window(2, [_Area(0, 0, 50, 50)])
        self.wm = _WM([self.w1, self.w2])
        self.calls, self.timers, self.attached, self.prop_options = [], [], [], {}

        def op_call(*args, **kw):
            self.calls.append((args, kw))
            return {'FINISHED'}

        def int_vector(**kw):
            self.prop_options.setdefault("int_vector", []).append(kw.get("options"))

        self.bpy = types.SimpleNamespace(
            types=types.SimpleNamespace(SpaceView3D=_Space, Context=object),
            props=types.SimpleNamespace(IntVectorProperty=int_vector),
            app=types.SimpleNamespace(
                background=False,
                handlers=types.SimpleNamespace(persistent=lambda fn: fn),
                timers=types.SimpleNamespace(register=lambda fn, first_interval=0.0: self.timers.append(fn))),
            ops=types.SimpleNamespace(bfacw=types.SimpleNamespace(chat_capture_screen=op_call)),
            context=types.SimpleNamespace(window_manager=self.wm, temp_override=lambda **_kw: _NullCtx()),
        )
        shown = types.SimpleNamespace(id="t-tab")
        self.ns = {
            "bpy": self.bpy, "Operator": object,
            "BoolProperty": lambda **_kw: False, "StringProperty": lambda **_kw: "",
            "_attach_thread": lambda _context, thread_id="": types.SimpleNamespace(id=thread_id) if thread_id
            else shown,
            "chat_attachments": types.SimpleNamespace(),
            "_attach_to_socket": lambda *a, **kw: self.attached.append(a),
        }
        src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        code = _slice(src, "_SHOT: dict =", "\nclass BFACW_OT_chat_image_drop(")
        exec(compile(code, "ui_chat.py", "exec"), self.ns)  # noqa: S102
        self.shot = self.ns["_SHOT"]

    def _ctx(self, window):
        return types.SimpleNamespace(window=window, window_manager=self.wm, workspace=window.workspace,
                                     temp_override=lambda **_kw: _NullCtx())

    def _op(self, sibling=False, thread_id=""):
        op = self.ns["BFACW_OT_chat_capture_screen"]()
        op.p1, op.p2, op.use_rect, op.sibling, op.thread_id = (0, 0), (0, 0), False, sibling, thread_id
        return op

    def _start(self, x=10, y=10):
        op = self._op()
        self.assertEqual(op.invoke(self._ctx(self.w1), _event("MOUSEMOVE", "NOTHING", x, y)), {'RUNNING_MODAL'})
        self.assertTrue(self.shot["active"])
        self.assertTrue(_Space.live, "the veil is drawn")
        return op

    def assertClean(self):
        self.assertFalse(self.shot["active"])
        self.assertEqual(self.shot["handlers"], [])
        self.assertEqual(_Space.live, [], "no veil left")
        self.assertEqual(self.shot["rects"], {})
        self.assertEqual(self.shot["hover"], {})
        self.assertEqual(self.shot["timers"], [])
        self.assertEqual(self.wm.timers, [], "no window timer left")
        self.assertEqual(self.shot["cursors"], set())
        self.assertIsNone(self.w1.cursor, "no crosshair left")
        for window in (self.w1, self.w2):
            self.assertIsNone(window.workspace.status, "status text cleared in every window")

    def test_the_part_is_not_saved_with_the_operator(self):
        for options in self.prop_options["int_vector"]:
            self.assertIn('SKIP_SAVE', options, "a saved part froze the next run's highlight (#454)")

    def test_esc_and_right_click(self):
        for key in ("ESC", "RIGHTMOUSE"):
            op = self._start()
            self.assertEqual(op.modal(self._ctx(self.w1), _event(key)), {'CANCELLED'})
            self.assertClean()

    def test_take_cleans_up_and_attaches_to_the_starting_thread(self):
        op = self._start()
        ctx = self._ctx(self.w1)
        op.modal(ctx, _event("LEFTMOUSE", "PRESS", 150, 50))
        self.assertEqual(op.modal(ctx, _event("LEFTMOUSE", "RELEASE", 150, 50)), {'FINISHED'})
        self.assertClean()
        self.assertEqual(self.shot["last"][1], (100, 0, 200, 100), "the part is kept for Enter, per window")
        self.calls.clear()
        self.timers[-1]()
        self.assertEqual(self.calls, [(("EXEC_DEFAULT",), {"p1": (100, 0), "p2": (200, 100), "use_rect": True,
                                                          "thread_id": "t-tab"})])

    def test_three_runs_in_a_row_follow_the_mouse(self):
        for run in range(3):
            op = self._start(10, 10)
            ctx = self._ctx(self.w1)
            self.assertEqual(self.shot["rects"][1], (0, 0, 100, 100), "run {:d}: the editor under the mouse".format(run))
            op.modal(ctx, _event("MOUSEMOVE", "NOTHING", 150, 50))
            self.assertEqual(self.shot["rects"][1], (100, 0, 200, 100), "run {:d}: it follows the mouse".format(run))
            self.assertTrue(self.shot["hover"][1])
            op.modal(ctx, _event("LEFTMOUSE", "PRESS", 20, 20))
            op.modal(ctx, _event("LEFTMOUSE", "RELEASE", 20, 20))
            self.assertClean()

    def test_enter_takes_the_last_part_again(self):
        self.shot["last"][1] = (5, 5, 60, 60)
        op = self._start(150, 50)
        self.assertEqual(self.shot["rects"][1], (100, 0, 200, 100), "the highlight shows the editor, not the last part")
        self.assertEqual(op.modal(self._ctx(self.w1), _event("RET")), {'FINISHED'})
        self.timers[-1]()
        self.assertEqual(self.calls[-1][1]["p1"], (5, 5))
        self.assertClean()

    def test_cancel_from_blender(self):
        op = self._start()
        op.cancel(self._ctx(self.w1))           # its window closed, or a file loads
        self.assertClean()

    def test_file_load_mid_capture(self):
        op = self._start()
        self.ns["_shot_on_load_pre"]()
        self.assertClean()
        # Should Blender still send it an event, it stops at once.
        self.assertEqual(op.modal(self._ctx(self.w1), _event("MOUSEMOVE", "NOTHING")), {'CANCELLED', 'PASS_THROUGH'})

    def test_an_error_in_modal_ends_the_capture(self):
        op = self._start()

        class _Broken:
            type, value = "MOUSEMOVE", "NOTHING"

            @property
            def mouse_x(self):
                raise RuntimeError("region freed")

        self.assertEqual(op.modal(self._ctx(self.w1), _Broken()), {'CANCELLED'})
        self.assertClean()

    def test_an_error_in_invoke_leaves_nothing(self):
        self.w1.workspace.fail = RuntimeError("no status bar")
        op = self._op()
        with self.assertRaises(RuntimeError):
            op.invoke(self._ctx(self.w1), _event("MOUSEMOVE", "NOTHING"))
        self.assertEqual(self.wm.modal, [], "no modal handler for an operator that failed")
        self.w1.workspace.fail = None
        self.assertClean()

    def test_a_sibling_ends_on_its_next_tick(self):
        starter = self._start()
        sibling = self._op(sibling=True)
        self.assertEqual(sibling.invoke(self._ctx(self.w2), _event("MOUSEMOVE", "NOTHING")), {'RUNNING_MODAL'})
        self.assertEqual([t.window for t in self.wm.timers], [self.w1, self.w2], "each window has its tick")
        self.assertEqual(self.w2.cursor, "CROSSHAIR")
        starter.modal(self._ctx(self.w1), _event("ESC"))
        self.assertEqual(sibling.modal(self._ctx(self.w2), _event("TIMER", "NOTHING")), {'CANCELLED', 'PASS_THROUGH'})
        self.assertIsNone(self.w2.cursor)
        self.assertClean()

    def test_a_sibling_take_goes_to_the_starting_thread(self):
        self._op(thread_id="t-9").invoke(self._ctx(self.w1), _event("MOUSEMOVE", "NOTHING"))
        sibling = self._op(sibling=True)
        sibling.invoke(self._ctx(self.w2), _event("MOUSEMOVE", "NOTHING"))
        ctx = self._ctx(self.w2)
        sibling.modal(ctx, _event("MOUSEMOVE", "NOTHING", 20, 20))
        sibling.modal(ctx, _event("LEFTMOUSE", "PRESS", 20, 20))
        sibling.modal(ctx, _event("LEFTMOUSE", "RELEASE", 20, 20))
        self.timers[-1]()
        self.assertEqual(self.calls[-1][1]["thread_id"], "t-9")

    def test_timers_pass_through(self):
        op = self._start()
        self.assertEqual(op.modal(self._ctx(self.w1), _event("TIMER", "NOTHING")), {'PASS_THROUGH'})
        self.assertTrue(self.shot["active"])
        op.modal(self._ctx(self.w1), _event("ESC"))
        self.assertClean()


# -- 4: formats outside the direct list are converted ---------------------------------------

class _FakeImage:
    def __init__(self, path, size=(4, 4)):
        self.filepath, self.size, self.file_format, self.name = path, size, "OPEN_EXR", os.path.basename(path)

    def save(self, filepath=""):
        with open(filepath, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\0" * 8)

    def save_render(self, filepath):
        with open(filepath, "wb") as fh:
            fh.write(b"not a png")


class TestUnsupportedFormatConverts(unittest.TestCase):

    def setUp(self):
        self.ca = _module("chat_attachments")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ca.attachments_dir = lambda: self.dir / "attachments"
        (self.dir / "attachments").mkdir()
        self.loaded, self.removed = [], []
        test = self

        class _Images:
            fail_save = False

            def load(self, path, check_existing=False):
                if path.endswith(".txt"):
                    raise RuntimeError("unsupported image format")
                img = _FakeImage(path)
                if _Images.fail_save:
                    img.save = None
                test.loaded.append((path, check_existing))
                return img

            def remove(self, img):
                test.removed.append(img.filepath)

        self.images = _Images
        self.saved = sys.modules.get("bpy")
        sys.modules["bpy"] = types.SimpleNamespace(data=types.SimpleNamespace(images=_Images()))

    def tearDown(self):
        if self.saved is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self.saved
        self.tmp.cleanup()

    def _file(self, name):
        path = self.dir / name
        path.write_bytes(b"data")
        return str(path)

    def test_exr_attaches_as_a_png_copy(self):
        src = self._file("three.exr")
        img = self.ca.load_image_file(src)
        self.assertIsNotNone(img, "an EXR is converted, not refused")
        self.assertTrue(img.filepath.endswith(".png"))
        self.assertTrue(os.path.basename(img.filepath).startswith("three"), "the copy keeps the file's name")
        self.assertEqual(self.removed, [src], "the source datablock is freed again")
        self.assertEqual(self.loaded[0], (src, False), "loaded on its own: the user's image is not touched")
        self.assertEqual(self.loaded[1], (img.filepath, True))

    def test_png_is_loaded_directly(self):
        src = self._file("one.png")
        img = self.ca.load_image_file(src)
        self.assertEqual(img.filepath, src)
        self.assertEqual(self.loaded, [(src, True)])

    def test_a_file_blender_cannot_read(self):
        self.assertIsNone(self.ca.load_image_file(self._file("notes.txt")))

    def test_a_copy_that_is_not_a_png_is_dropped(self):
        self.images.fail_save = True        # save() missing: save_render writes the scene's format
        self.assertIsNone(self.ca.load_image_file(self._file("four.tga")))
        self.assertEqual(list((self.dir / "attachments").iterdir()), [], "no stray file left")

    def test_the_drop_handler_lists_the_converted_formats(self):
        for ext in (".exr", ".hdr", ".tga"):
            self.assertIn(ext, self.ca.SUPPORTED_EXTENSIONS)


if __name__ == "__main__":
    unittest.main()
