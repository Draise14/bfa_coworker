# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Paste an image from the clipboard into the chat (#450), with a stand-in ``bpy`` and a
stubbed ``image.clipboard_paste``.

1. Ctrl V in the prompt hands the clipboard's text to the host (``on_action``), which runs
   in the box's own area; with no host the text is pasted as before.
2. A copied path is an image file when it is one line -- plain, quoted ("Copy as path")
   or a ``file://`` URI -- to an existing file with an image extension.
3. The three clipboard cases: a copied image file path attaches that file; a picture with
   no text attaches the picture (read in an Image Editor -- an open one, or an area that
   shows one for the moment and goes back -- saved as a PNG, the "Clipboard" datablock
   removed); plain text stays text.  Text and a picture together stay text for Ctrl V;
   Paste Image (*force*) takes the picture.
4. The chat: Ctrl V attaches to the box's chat or pastes the text; Paste Image attaches to
   the tab whose popover started it; the right-click menu's Paste does what Ctrl V does;
   Paste Image is in the popover, the Chat menu, the right-click menu and the toolbar.

Run with::

    python -m unittest tests.test_paste_image -v
"""

__all__ = ()

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from test_autosave_gap import _fake_bpy, _load

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PNG = b"\x89PNG\r\n\x1a\n"


def _module(name):
    spec = importlib.util.spec_from_file_location("bfacw_paste_" + name, _ADDON / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _slice(src, start_marker, end_marker):
    start = src.index(start_marker)
    return src[start:src.index(end_marker, start)]


# -- 1: Ctrl V goes to the host --------------------------------------------------------------

class TestCtrlVGoesToTheHost(unittest.TestCase):

    def setUp(self):
        self.bpy = _fake_bpy()
        self.ic = _load(self.bpy)
        from importlib import import_module
        box_mod = import_module("bfacw_autosave_gap_pkg.ui_gpu.input_box")
        self.box = box_mod.InputBox()
        self.box.focused = True
        self.key = box_mod.Key(type="V", value="PRESS", ctrl=True)
        self.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(clipboard="copied text"))

    def tearDown(self):
        self.ic.set_host(None)

    def test_the_host_gets_the_clipboard_text(self):
        seen = []
        self.ic.set_host(lambda _c, _e: None, lambda box, action, value: seen.append((box, action, value)))
        self.assertTrue(self.box.handle_key(self.key))
        self.ic._run_actions(self.box, self.context)
        self.assertEqual(seen, [(self.box, "paste", "copied text")])
        self.assertEqual(self.box.model.text, "", "the host decides: an image, or the text")

    def test_without_a_host_the_text_is_pasted(self):
        self.ic.set_host(lambda _c, _e: None)
        self.box.handle_key(self.key)
        self.ic._run_actions(self.box, self.context)
        self.assertEqual(self.box.model.text, "copied text")

    def test_cmd_v_too(self):
        seen = []
        self.ic.set_host(lambda _c, _e: None, lambda _b, action, value: seen.append(action))
        self.box.handle_key(type(self.key)(type="V", value="PRESS", oskey=True))
        self.ic._run_actions(self.box, self.context)
        self.assertEqual(seen, ["paste"])


# -- 2: a copied path -------------------------------------------------------------------------

class TestImagePathFromText(unittest.TestCase):

    def setUp(self):
        self.ca = _module("chat_attachments")
        self.tmp = tempfile.TemporaryDirectory()
        self.png = os.path.join(self.tmp.name, "my shot.png")
        Path(self.png).write_bytes(_PNG)
        self.txt = os.path.join(self.tmp.name, "notes.txt")
        Path(self.txt).write_text("x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_and_quoted_paths(self):
        find = self.ca.image_path_from_text
        self.assertEqual(find(self.png), self.png)
        self.assertEqual(find('"{}"'.format(self.png)), self.png, "Windows' Copy as path")
        self.assertEqual(find("  {}\r\n".format(self.png)), self.png)

    def test_file_uri(self):
        uri = Path(self.png).as_uri()           # file:///.../my%20shot.png
        self.assertEqual(os.path.normcase(self.ca.image_path_from_text(uri)), os.path.normcase(self.png))

    def test_not_an_image_path(self):
        find = self.ca.image_path_from_text
        self.assertEqual(find(""), "")
        self.assertEqual(find("hello there"), "")
        self.assertEqual(find(self.txt), "", "not an image extension")
        self.assertEqual(find(os.path.join(self.tmp.name, "gone.png")), "", "no such file")
        self.assertEqual(find("{}\n{}".format(self.png, self.png)), "", "more than one line is text")
        self.assertEqual(find("see " + self.png), "")
        self.assertEqual(find("x" * 5000 + ".png"), "")


# -- 3: the three clipboard cases ---------------------------------------------------------------

class _Image:
    def __init__(self, name, filepath=""):
        self.name, self.filepath, self.file_format = name, filepath, "PNG"

    def save(self, filepath=""):
        Path(filepath).write_bytes(_PNG + b"\0" * 8)


class _Space:
    def __init__(self):
        self.image, self.use_image_pin = None, False


class _Area:
    def __init__(self, kind, w, h, ui_type=""):
        self.type, self._ui, self._home = kind, ui_type or kind, kind
        self.width, self.height = w, h
        self.regions = [types.SimpleNamespace(type="HEADER"), types.SimpleNamespace(type="WINDOW")]
        self._spaces, self.switches = {}, []

    @property
    def ui_type(self):
        return self._ui

    @ui_type.setter
    def ui_type(self, value):
        self._ui = value
        self.type = "IMAGE_EDITOR" if value == "IMAGE_EDITOR" else self._home
        self.switches.append(value)

    @property
    def spaces(self):
        return types.SimpleNamespace(active=self._spaces.setdefault(self.type, _Space()))


class _Images:
    def __init__(self):
        self.loaded, self.removed = [], []

    def load(self, path, check_existing=False):
        self.loaded.append(path)
        return _Image(os.path.basename(path), path)

    def remove(self, img):
        self.removed.append(img.name)


class _Context:
    def __init__(self, areas, chat=None):
        self.window = types.SimpleNamespace(screen=types.SimpleNamespace(areas=areas))
        self.window_manager = types.SimpleNamespace(windows=[self.window])
        self.area = chat
        self.current = None
        self.overrides = []

    def temp_override(self, **kw):
        ctx = self

        class _Override:
            def __enter__(self):
                self.saved, ctx.current = ctx.current, kw
                ctx.overrides.append(kw)

            def __exit__(self, *_exc):
                ctx.current = self.saved
                return False
        return _Override()


class _ClipboardPaste:
    """``bpy.ops.image.clipboard_paste``: its poll needs an Image Editor and an image."""

    def __init__(self, context, images, has_image):
        self.ctx, self.images, self.has_image, self.calls = context, images, has_image, 0
        self.misses = 0         # reads that come back empty (Windows, a picture just copied)

    def _editor(self):
        cur = self.ctx.current
        return cur["area"] if cur and cur["area"].type == "IMAGE_EDITOR" else None

    def poll(self):
        return self._editor() is not None and self.has_image

    def __call__(self):
        if not self.poll():
            raise RuntimeError("Operator bpy.ops.image.clipboard_paste.poll() failed")
        self.calls += 1
        if self.misses:
            self.misses -= 1
            return {"CANCELLED"}
        space = self._editor().spaces.active
        space.image, space.use_image_pin = _Image("Clipboard"), True   # a UV editor pins it
        return {"FINISHED"}


class TestClipboardImage(unittest.TestCase):

    def setUp(self):
        self.ca = _module("chat_attachments")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "attachments"
        self.dir.mkdir()
        self.ca.attachments_dir = lambda: self.dir
        self.images = _Images()
        self.view3d = _Area("VIEW_3D", 800, 600)
        self.outliner = _Area("OUTLINER", 300, 400)
        self.timeline = _Area("DOPESHEET_EDITOR", 800, 100, "TIMELINE")
        self.chat = _Area("NODE_EDITOR", 300, 100, "BFACW_CoworkerTree")     # smallest, but the chat's
        self.context = _Context([self.view3d, self.outliner, self.timeline, self.chat], chat=self.chat)
        self.op = _ClipboardPaste(self.context, self.images, has_image=True)
        self.saved = sys.modules.get("bpy")
        sys.modules["bpy"] = types.SimpleNamespace(
            data=types.SimpleNamespace(images=self.images), context=self.context,
            ops=types.SimpleNamespace(image=types.SimpleNamespace(clipboard_paste=self.op)))

    def tearDown(self):
        if self.saved is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self.saved
        self.tmp.cleanup()

    def _get(self, text="", force=False):
        return self.ca.clipboard_image(text, force=force, context=self.context)

    def assertRestored(self):
        for area in self.context.window.screen.areas:
            self.assertEqual(area.type, area._home, area._home)
        self.assertIsNone(self.timeline._spaces["IMAGE_EDITOR"].image, "the editor shows what it showed")
        self.assertFalse(self.timeline._spaces["IMAGE_EDITOR"].use_image_pin)

    def test_a_copied_image_file_path(self):
        png = Path(self.tmp.name) / "photo.png"
        png.write_bytes(_PNG)
        image = self._get('"{}"'.format(png))
        self.assertEqual(image.filepath, str(png))
        self.assertEqual(self.op.calls, 0, "the file, not the clipboard's picture")
        self.assertEqual(self.view3d.switches + self.timeline.switches + self.chat.switches, [])

    def test_a_picture_and_no_text(self):
        image = self._get("")
        self.assertIsNotNone(image)
        self.assertEqual(self.op.calls, 1)
        self.assertTrue(image.filepath.startswith(str(self.dir)) and image.filepath.endswith(".png"))
        self.assertTrue(os.path.basename(image.filepath).startswith("clipboard"))
        self.assertEqual(self.images.removed, ["Clipboard"], "the operator's datablock is removed again")
        # The smallest area that is neither the 3D View nor the chat showed an Image Editor, then went back.
        self.assertEqual(self.timeline.switches, ["IMAGE_EDITOR", "TIMELINE"])
        self.assertEqual(self.view3d.switches + self.chat.switches + self.outliner.switches, [])
        self.assertIs(self.context.overrides[-1]["area"], self.timeline)
        self.assertEqual(self.context.overrides[-1]["region"].type, "WINDOW")
        self.assertRestored()

    def test_a_first_read_that_comes_back_empty_is_read_again(self):
        self.op.misses = 1
        self.assertIsNotNone(self._get(""))
        self.assertEqual(self.op.calls, 2)
        self.op.misses, self.op.calls = 5, 0
        self.assertIsNone(self._get(""), "not forever")
        self.assertEqual(self.op.calls, 3)
        self.assertRestored()

    def test_an_open_image_editor_is_used_and_keeps_its_image(self):
        editor = _Area("IMAGE_EDITOR", 400, 400)
        mine = _Image("my render")
        editor.spaces.active.image = mine
        self.context.window.screen.areas.append(editor)
        self.assertIsNotNone(self._get(""))
        self.assertIs(editor.spaces.active.image, mine)
        self.assertFalse(editor.spaces.active.use_image_pin)
        self.assertEqual(self.timeline.switches + editor.switches, [], "no area changes")

    def test_plain_text_stays_text(self):
        self.assertIsNone(self._get("just some words"))
        self.assertEqual(self.op.calls, 0)
        self.assertEqual(self.timeline.switches, [], "text never looks for a picture")

    def test_no_picture_and_no_text(self):
        self.op.has_image = False
        self.assertIsNone(self._get(""))
        self.assertEqual(self.op.calls, 0)
        self.assertEqual(self.images.removed, [])
        self.assertRestored()

    def test_text_and_a_picture_stay_text_unless_forced(self):
        self.assertIsNone(self._get("A1\tB1"), "cells from a spreadsheet: Ctrl V pastes them")
        image = self._get("A1\tB1", force=True)         # Paste Image
        self.assertIsNotNone(image)
        self.assertEqual(self.op.calls, 1)

    def test_a_failing_paste_restores_the_area(self):
        class _Fails(_ClipboardPaste):
            def __call__(self):
                raise RuntimeError("clipboard busy")
        sys.modules["bpy"].ops.image.clipboard_paste = _Fails(self.context, self.images, True)
        self.assertIsNone(self._get(""))
        self.assertRestored()
        self.assertEqual(self.images.removed, [])

    def test_no_operator(self):
        sys.modules["bpy"].ops = types.SimpleNamespace()
        self.assertIsNone(self._get(""))


# -- 4: the chat ----------------------------------------------------------------------------

class _Box:
    def __init__(self):
        self.pasted = []

    def paste(self, text):
        self.pasted.append(text)


class TestChat(unittest.TestCase):

    def setUp(self):
        self.src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
        self.image = _Image("clipboard_1.png", "/att/clipboard_1.png")
        self.offer = None           # what clipboard_image returns
        self.asked, self.attached, self.focused, self.edits = [], [], [], []
        self.tab = types.SimpleNamespace(id="tab-1")
        self.area_thread = types.SimpleNamespace(id="area")
        test = self

        def clipboard_image(text, force=False, context=None):
            test.asked.append((text, force))
            return test.offer

        ui = types.SimpleNamespace(
            _ui_thread=lambda _context: test.area_thread,
            _attach_thread=lambda _context, tid="": test.tab if tid == "tab-1" else test.area_thread,
            _attach_to_socket=lambda context, op, img, thread=None: test.attached.append((img, thread, op)))
        pkg = types.ModuleType("bfacw_paste_pkg")
        pkg.__path__ = []
        pkg.ui_chat, pkg.chat_attachments = ui, types.SimpleNamespace(clipboard_image=clipboard_image)
        self.saved = sys.modules.get("bfacw_paste_pkg")
        sys.modules["bfacw_paste_pkg"] = pkg
        props = types.SimpleNamespace(StringProperty=lambda **_kw: "", EnumProperty=lambda **_kw: "")
        self.wm = types.SimpleNamespace(clipboard="")
        self.bpy = types.SimpleNamespace(types=types.SimpleNamespace(Operator=object), props=props,
                                         context=types.SimpleNamespace(window_manager=self.wm))
        ic = types.SimpleNamespace(
            focus=lambda box, region: test.focused.append((box, region)),
            menu_edit=lambda action, clip: test.edits.append((action, clip)),
            menu_target=lambda: {"surface": test.surface, "region": test.region})
        self.box, self.region = _Box(), types.SimpleNamespace(tag_redraw=lambda: None)
        self.surface = types.SimpleNamespace(box=self.box)
        self.ns = {"bpy": self.bpy, "__name__": "bfacw_paste_pkg.chat_editor", "__package__": "bfacw_paste_pkg",
                   "Any": object, "InputBox": object, "_tag_chat_regions": lambda: None,
                   "input_capture": ic, "_after_key": lambda _box: None}
        code = "\n\n\n".join((
            _slice(self.src, "def _paste_image(", "\n\n\ndef _on_box_action("),
            _slice(self.src, "class BFACW_OT_chat_paste_image(", "\n\n\nclass "),
            _slice(self.src, "_EDIT_WHAT = [", "\n\n\nclass BFACW_MT_chat_context(")))
        exec(compile(code, "chat_editor.py", "exec"), self.ns)  # noqa: S102

    def tearDown(self):
        if self.saved is None:
            sys.modules.pop("bfacw_paste_pkg", None)
        else:
            sys.modules["bfacw_paste_pkg"] = self.saved

    def test_ctrl_v_attaches_to_the_box_chat(self):
        self.offer = self.image
        self.ns["_paste_into"](self.box, "")
        self.assertEqual(self.attached, [(self.image, self.area_thread, None)])
        self.assertEqual(self.box.pasted, [], "no text pasted")
        self.assertEqual(self.asked, [("", False)])

    def test_ctrl_v_with_nothing_image_like_pastes_the_text(self):
        self.ns["_paste_into"](self.box, "hello")
        self.assertEqual(self.box.pasted, ["hello"])
        self.assertEqual(self.attached, [])

    def test_an_error_reading_the_clipboard_still_pastes_the_text(self):
        def boom(*_a, **_kw):
            raise RuntimeError("no")
        sys.modules["bfacw_paste_pkg"].chat_attachments.clipboard_image = boom
        self.ns["_paste_into"](self.box, "hello")
        self.assertEqual(self.box.pasted, ["hello"])

    def _op(self, cls, **props):
        op = self.ns[cls]()
        op.reports = []
        op.report = lambda kind, msg: op.reports.append((kind, msg))
        for k, v in props.items():
            setattr(op, k, v)
        return op

    def test_paste_image_goes_to_the_popover_tab_and_takes_the_picture(self):
        self.offer, self.wm.clipboard = self.image, "some text"
        op = self._op("BFACW_OT_chat_paste_image", thread_id="tab-1")
        self.assertEqual(op.execute(types.SimpleNamespace(window_manager=self.wm)), {"FINISHED"})
        self.assertEqual(self.attached, [(self.image, self.tab, op)])
        self.assertEqual(self.asked, [("some text", True)], "forced: the picture even with text")

    def test_paste_image_with_nothing_says_so(self):
        op = self._op("BFACW_OT_chat_paste_image", thread_id="")
        self.assertEqual(op.execute(types.SimpleNamespace(window_manager=self.wm)), {"CANCELLED"})
        self.assertEqual(op.reports, [({"WARNING"}, "No image on the clipboard")])

    def test_the_menu_paste_does_what_ctrl_v_does(self):
        self.offer, self.wm.clipboard = self.image, ""
        op = self._op("BFACW_OT_chat_context_edit", action="paste")
        self.assertEqual(op.execute(types.SimpleNamespace(window_manager=self.wm)), {"FINISHED"})
        self.assertEqual(self.attached, [(self.image, self.area_thread, None)])
        self.assertEqual(self.edits, [])
        self.assertEqual(self.focused, [(self.box, self.region)])
        self.offer, self.wm.clipboard = None, "words"
        op.execute(types.SimpleNamespace(window_manager=self.wm))
        self.assertEqual(self.edits, [("paste", "words")])

    def test_the_menus_offer_paste_image(self):
        for start, end in (("class BFACW_MT_editor_chat(", "\n\n\nclass "),
                           ("class BFACW_MT_chat_context(", "\n\n\n"),
                           ("class BFACW_PT_editor_tools_attach(", "\n\n\nclass ")):
            self.assertIn("bfacw.chat_paste_image", _slice(self.src, start, end), start)
        self.assertIn("BFACW_OT_chat_paste_image", _slice(self.src, "_CLASSES = (", ")\n"))
        ui = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        popover = _slice(ui, "class BFACW_PT_attach_popover(", "\n\n\nclass ")
        self.assertIn("bfacw.chat_paste_image", popover)
        self.assertIn("op.thread_id = thread.id", popover.split("chat_paste_image", 1)[1])

    def test_ctrl_v_routes_through_do(self):
        do = _slice(self.src, "def _do(", "\n\n\ndef _flush(")
        self.assertIn('elif action == "paste" and surface is not None:', do)
        self.assertIn("_paste_into(surface.box, value)", do)


if __name__ == "__main__":
    unittest.main()
