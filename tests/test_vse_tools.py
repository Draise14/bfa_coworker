# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the five Video Sequencer tools (Tools 1, #304).

1. The pure helpers in ``_template_vse_shared.py``: the limit and its "more" count,
   the filters, near names and the not-found text, overlaps, gaps, the planned range,
   conflicts and the nearest free channel.
2. Each tool-code's ``main`` on a stand-in ``bpy`` (strips that behave like 5.2's:
   ``left_handle`` / ``right_handle`` / ``content_start``), with its includes inlined
   as they are when sent to Blender: result shapes, the errors, a move and its one undo
   step, a refused overlap, a locked strip, the preview's settings put back.
3. The registration: the five names in the ``vse`` domain of both lists.

No Blender needed.  Run with::

    python -m unittest tests.test_vse_tools -v
"""

__all__ = ()

import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TOOLS = _REPO / "mcp" / "blmcp" / "tools"
_NAMES = ("get_vse_strips_summary", "get_vse_strip_detail", "get_vse_timeline_overview",
          "set_strip_frame_range", "render_vse_preview")


def _inline(path: Path) -> str:
    """The tool-code as Blender gets it: ``# @include_begin: x`` blocks replaced by x."""
    out, skip = [], False
    for line in path.read_text(encoding="utf-8").splitlines(True):
        if line.startswith("# @include_begin: "):
            out.append((path.parent / line.split(": ", 1)[1].strip()).read_text(encoding="utf-8") + "\n")
            skip = True
        elif skip:
            skip = not line.startswith("# @include_end")
        else:
            out.append(line)
    return "".join(out)


def _load(name: str) -> types.ModuleType:
    path = _TOOLS / (name + ".py")
    module = types.ModuleType(name)
    exec(compile(_inline(path), str(path), "exec"), module.__dict__)  # pylint: disable=exec-used
    return module


SHARED = _load("_template_vse_shared")


# -- A stand-in bpy ---------------------------------------------------------------------

class _Strip:
    """A strip with 5.2's frame properties; overlaps are not shuffled (the tool refuses them)."""

    def __init__(self, name, kind, channel, start, end, meta=None, **extra):
        self.name, self.type, self.channel = name, kind, channel
        self._start, self._end = start, end
        self.content_start_value = float(start)
        self.lock = self.mute = self.select = False
        self._meta = meta
        self.content_duration = 1
        self.left_handle_offset = 0.0
        self.right_handle_offset = 0.0
        self.blend_type, self.blend_alpha = "ALPHA_OVER", 1.0
        self.modifiers = []
        for key, value in extra.items():
            setattr(self, key, value)

    def parent_meta(self):
        return self._meta

    @property
    def left_handle(self):
        return self._start

    @left_handle.setter
    def left_handle(self, value):
        self._start = min(int(value), self._end - 1)

    @property
    def right_handle(self):
        return self._end

    @right_handle.setter
    def right_handle(self, value):
        self._end = max(int(value), self._start + 1)

    @property
    def duration(self):
        return self._end - self._start

    @property
    def content_start(self):
        return self.content_start_value

    @content_start.setter
    def content_start(self, value):
        delta = int(value - self.content_start_value)
        self.content_start_value = float(value)
        self._start += delta
        self._end += delta


class _Collection(list):
    def get(self, name, default=None):
        return next((x for x in self if x.name == name), default)


def _scene(name="Scene", strips=()):
    ed = types.SimpleNamespace(
        strips_all=list(strips), strips=[s for s in strips if s.parent_meta() is None], active_strip=None,
        channels=[types.SimpleNamespace(name="Channel {:d}".format(i), mute=False, lock=False) for i in range(129)])
    image_settings = types.SimpleNamespace(media_type="VIDEO", file_format="FFMPEG", color_mode="RGB", quality=90)
    render = types.SimpleNamespace(fps=24, fps_base=1.0, resolution_x=1920, resolution_y=1080,
                                   resolution_percentage=100, use_sequencer=False, filepath="//out/",
                                   image_settings=image_settings)
    return types.SimpleNamespace(name=name, sequence_editor=ed, render=render, frame_start=1, frame_end=120,
                                 frame_current=7, timeline_markers=[types.SimpleNamespace(name="Intro", frame=1)])


def _install_bpy(scene, background=True):
    calls = {"undo_push": [], "render": []}
    bpy = types.ModuleType("bpy")

    def undo_push(message=""):
        calls["undo_push"].append(message)
        return {"FINISHED"}

    def render(*args, **kwargs):
        calls["render"].append((args, dict(kwargs), {
            "filepath": scene.render.filepath, "pct": scene.render.resolution_percentage,
            "format": scene.render.image_settings.file_format, "frame": scene.frame_current,
            "use_sequencer": scene.render.use_sequencer}))
        with open(scene.render.filepath, "wb") as fh:
            fh.write(b"\xff\xd8\xff fake jpeg")
        return {"FINISHED"}

    bpy.ops = types.SimpleNamespace(ed=types.SimpleNamespace(undo_push=undo_push),
                                    render=types.SimpleNamespace(render=render))
    bpy.data = types.SimpleNamespace(scenes=_Collection([scene]))
    bpy.app = types.SimpleNamespace(background=background, tempdir=tempfile.mkdtemp(prefix="vse304_"),
                                    is_job_running=lambda kind: False)
    bpy.context = types.SimpleNamespace(
        scene=scene, sequencer_scene=None, window=None, window_manager=types.SimpleNamespace(windows=[]),
        preferences=types.SimpleNamespace(view=types.SimpleNamespace(render_display_type="WINDOW"), is_dirty=False))
    sys.modules["bpy"] = bpy
    return bpy, calls


def _timeline():
    return [
        _Strip("BG", "COLOR", 1, 1, 101, color=(0.1, 0.2, 0.5)),
        _Strip("Title", "TEXT", 2, 10, 50, text="Hello", font_size=60.0),
        _Strip("Logo", "IMAGE", 3, 20, 50, directory="C:/img/", elements=[types.SimpleNamespace(filename="logo.png")]),
        _Strip("Lower Third", "TEXT", 2, 60, 90, text="Ann"),
    ]


class _BpyTest(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("bpy")

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self._saved


def _rows(*specs):
    return [{"name": n, "type": t, "channel": c, "start": s, "end": e, "meta": "", "mute": False}
            for n, t, c, s, e in specs]


# -- 1. Pure helpers ----------------------------------------------------------------------

class TestLimitAndFilters(unittest.TestCase):

    def test_limit_and_more(self):
        rows = list(range(120))
        shown, more = SHARED._vse_apply_limit(rows, 50)
        self.assertEqual((len(shown), more), (50, 70))
        self.assertEqual(SHARED._vse_apply_limit(rows, 0)[1], 70)        # 0 = the default (50)
        self.assertEqual(SHARED._vse_apply_limit(rows, "x")[1], 70)
        self.assertEqual(SHARED._vse_apply_limit(rows, 200), (rows, 0))
        self.assertEqual(SHARED._vse_clamp_limit(10_000), SHARED._VSE_LIMIT_MAX)

    def test_filters(self):
        rows = _rows(("BG", "COLOR", 1, 1, 101), ("Title", "TEXT", 2, 10, 50), ("Logo", "IMAGE", 3, 20, 50),
                     ("Lower Third", "TEXT", 2, 60, 90))
        names = lambda **kw: [r["name"] for r in SHARED._vse_filter_rows(rows, **kw)]  # noqa: E731
        self.assertEqual(names(channel=2), ["Title", "Lower Third"])
        self.assertEqual(names(strip_type="text"), ["Title", "Lower Third"])
        self.assertEqual(names(name_contains="THIRD"), ["Lower Third"])
        self.assertEqual(names(frame_start=50), ["BG", "Lower Third"])      # end is exclusive
        self.assertEqual(names(frame_end=15), ["BG", "Title"])
        self.assertEqual(names(channel=2, frame_start=55, frame_end=70), ["Lower Third"])


class TestNamesAndNotFound(unittest.TestCase):

    def test_near_names(self):
        names = ["Title", "Title.001", "Lower Third", "BG"]
        self.assertEqual(SHARED._vse_near_names("title", names)[:2], ["Title", "Title.001"])
        self.assertIn("Lower Third", SHARED._vse_near_names("lower thrid", names))
        self.assertEqual(SHARED._vse_near_names("zzz", names), [])

    def test_not_found_lists_near_names_and_the_channel(self):
        rows = _rows(("Title", "TEXT", 2, 10, 50), ("Lower Third", "TEXT", 2, 60, 90), ("BG", "COLOR", 1, 1, 101))
        text = SHARED._vse_not_found_message("title", rows, "Scene", channel=2)
        self.assertIn("No strip named 'title' in scene 'Scene'.", text)
        self.assertIn("Did you mean: 'Title'", text)
        self.assertIn("Strips on channel 2: 'Title', 'Lower Third'.", text)
        self.assertIn("get_vse_strips_summary", text)

    def test_not_found_without_near_names_lists_strips(self):
        rows = _rows(("BG", "COLOR", 1, 1, 101))
        self.assertIn("Strips: 'BG'.", SHARED._vse_not_found_message("Credits", rows, "Scene"))

    def test_not_found_on_an_empty_timeline(self):
        self.assertIn("has no strips", SHARED._vse_not_found_message("Title", [], "Scene"))


class TestTimelineMaths(unittest.TestCase):

    def test_overlaps(self):
        rows = _rows(("A", "COLOR", 1, 1, 50), ("B", "COLOR", 1, 40, 80), ("C", "COLOR", 1, 80, 90),
                     ("D", "COLOR", 2, 1, 100))
        self.assertEqual(SHARED._vse_overlaps(rows), [{"channel": 1, "a": "A", "b": "B", "frames": [40, 50]}])

    def test_gaps(self):
        rows = _rows(("A", "COLOR", 1, 1, 20), ("B", "COLOR", 2, 30, 60), ("S", "SOUND", 3, 1, 120))
        self.assertEqual(SHARED._vse_gaps(rows, 1, 100), [[20, 29], [60, 100]])
        self.assertEqual(SHARED._vse_gaps(_rows(("A", "COLOR", 1, 1, 200)), 1, 100), [])

    def test_gaps_ignore_muted_and_meta_contents(self):
        rows = _rows(("A", "COLOR", 1, 1, 101))
        rows[0]["mute"] = True
        inner = _rows(("In", "COLOR", 1, 1, 101))
        inner[0]["meta"] = "Meta"
        self.assertEqual(SHARED._vse_gaps(rows + inner, 1, 100), [[1, 100]])

    def test_plan_range(self):
        plan = SHARED._vse_plan_range
        self.assertEqual(plan(10, 50, 50, None, False), (50, 90, ""))        # move keeps the length
        self.assertEqual(plan(10, 50, None, 30, False), (10, 30, ""))        # end trims
        self.assertEqual(plan(10, 50, 20, None, True), (20, 50, ""))         # trim the start
        self.assertEqual(plan(10, 50, 50, 60, False), (50, 60, ""))
        self.assertIn("must be after start", plan(10, 50, None, 10, False)[2])
        self.assertIn("must be after start", plan(10, 50, 60, None, True)[2])

    def test_conflicts_and_free_channel(self):
        rows = _rows(("Title", "TEXT", 2, 10, 50), ("Logo", "IMAGE", 3, 20, 50), ("Up", "COLOR", 4, 0, 100))
        self.assertEqual([r["name"] for r in SHARED._vse_conflicts(rows, "Title", "", 3, 30, 70)], ["Logo"])
        self.assertEqual(SHARED._vse_conflicts(rows, "Title", "", 3, 50, 90), [])     # touching is fine
        self.assertEqual(SHARED._vse_free_channel(rows, "Title", "", 30, 70, 2), 2)   # its own range is free
        self.assertEqual(SHARED._vse_free_channel(rows, "Title", "", 30, 70, 3), 2)   # nearest, above first
        rows += _rows(("Two", "COLOR", 2, 60, 61), ("One", "COLOR", 1, 0, 100))
        self.assertEqual(SHARED._vse_free_channel(rows, "Title", "", 30, 70, 3), 5)


# -- 2. Tool-code on a stand-in bpy -------------------------------------------------------

class TestStripsSummary(_BpyTest):

    def test_shape_and_more(self):
        strips = [_Strip("S{:03d}".format(i), "COLOR", 1 + i % 3, i * 10, i * 10 + 10) for i in range(70)]
        _install_bpy(_scene(strips=strips))
        mod = _load("get_vse_strips_summary_toolcode")
        out = mod.main(mod.Params(limit=25))._asdict()
        self.assertEqual(set(out), set(mod.Result._fields))
        self.assertEqual((out["total"], out["matched"], out["shown"], out["more"]), (70, 70, 25, 45))
        self.assertIn("45 more", out["message"])
        self.assertEqual(set(out["strips"][0]),
                         {"name", "type", "channel", "start", "end", "duration", "mute", "lock", "select", "meta"})

    def test_filter_by_channel(self):
        _install_bpy(_scene(strips=_timeline()))
        mod = _load("get_vse_strips_summary_toolcode")
        out = mod.main(mod.Params(channel=2))._asdict()
        self.assertEqual([s["name"] for s in out["strips"]], ["Title", "Lower Third"])
        self.assertEqual(out["more"], 0)

    def test_unknown_scene(self):
        _install_bpy(_scene(strips=_timeline()))
        mod = _load("get_vse_strips_summary_toolcode")
        out = mod.main(mod.Params(scene_name="Edit"))._asdict()
        self.assertEqual(out["status"], "error")
        self.assertIn("No scene named 'Edit'. Scenes: 'Scene'.", out["message"])


class TestStripDetail(_BpyTest):

    def test_detail(self):
        _install_bpy(_scene(strips=_timeline()))
        mod = _load("get_vse_strip_detail_toolcode")
        out = mod.main(mod.Params(strip_name="Logo"))._asdict()
        self.assertEqual(out["status"], "ok")
        strip = out["strip"]
        self.assertEqual((strip["start"], strip["end"], strip["channel"]), (20, 50, 3))
        self.assertEqual(strip["source"]["filepath"], os.path.join("C:/img/", "logo.png"))
        self.assertEqual(strip["opacity"], 1.0)
        self.assertEqual(strip["modifiers"], [])
        title = mod.main(mod.Params(strip_name="Title"))._asdict()["strip"]
        self.assertEqual(title["text"]["text"], "Hello")

    def test_not_found(self):
        _install_bpy(_scene(strips=_timeline()))
        mod = _load("get_vse_strip_detail_toolcode")
        out = mod.main(mod.Params(strip_name="title"))._asdict()
        self.assertEqual((out["status"], out["strip"]), ("error", None))
        self.assertIn("Did you mean: 'Title'", out["message"])


class TestTimelineOverview(_BpyTest):

    def test_overview(self):
        strips = _timeline()
        scene = _scene(strips=strips)
        scene.sequence_editor.active_strip = strips[1]
        _install_bpy(scene)
        mod = _load("get_vse_timeline_overview_toolcode")
        out = mod.main(mod.Params())._asdict()
        self.assertEqual(set(out), set(mod.Result._fields))
        self.assertEqual(out["fps"], 24)
        self.assertEqual(out["resolution"], [1920, 1080, 100])
        self.assertEqual(out["current_frame"], 7)
        self.assertEqual(out["active_strip"], "Title")
        self.assertEqual(out["content_range"], [1, 101])
        self.assertEqual([(c["channel"], c["strips"]) for c in out["channels"]], [(1, 1), (2, 2), (3, 1)])
        self.assertEqual(out["gaps"], [[101, 120]])
        self.assertEqual(out["overlaps"], [])
        self.assertEqual(out["markers"], [{"name": "Intro", "frame": 1}])
        self.assertIn("Sequencer is off", out["message"])


class TestSetStripFrameRange(_BpyTest):

    def test_move_title_to_frame_50(self):
        strips = _timeline()
        _bpy, calls = _install_bpy(_scene(strips=strips))
        mod = _load("set_strip_frame_range_toolcode")
        out = mod.main(mod.Params(strip_name="Title", start=50, channel=4))._asdict()
        self.assertEqual(out["status"], "ok", out["message"])
        self.assertEqual(out["before"], {"start": 10, "end": 50, "channel": 2})
        self.assertEqual(out["after"], {"start": 50, "end": 90, "channel": 4})
        self.assertTrue(out["undo_step"])
        self.assertEqual(calls["undo_push"], ["Coworker: moved strip 'Title'"])    # one step

    def test_trim(self):
        strips = _timeline()
        _install_bpy(_scene(strips=strips))
        mod = _load("set_strip_frame_range_toolcode")
        out = mod.main(mod.Params(strip_name="BG", start=5, end=80, trim=True))._asdict()
        self.assertEqual(out["after"], {"start": 5, "end": 80, "channel": 1})
        self.assertEqual(strips[0].content_start, 1.0)                     # the content stayed

    def test_overlap_is_refused_and_names_a_free_channel(self):
        strips = _timeline()
        _bpy, calls = _install_bpy(_scene(strips=strips))
        mod = _load("set_strip_frame_range_toolcode")
        out = mod.main(mod.Params(strip_name="Title", start=50))._asdict()      # onto "Lower Third"
        self.assertEqual(out["status"], "error")
        self.assertIn("would overlap 'Lower Third' (60-90); nothing was changed", out["message"])
        self.assertIn("pass channel=3", out["message"])
        self.assertEqual((strips[1].left_handle, strips[1].channel), (10, 2))
        self.assertEqual(calls["undo_push"], [])

    def test_not_found_and_bad_input(self):
        _install_bpy(_scene(strips=_timeline()))
        mod = _load("set_strip_frame_range_toolcode")
        out = mod.main(mod.Params(strip_name="Titel", start=50, channel=2))._asdict()
        self.assertIn("Did you mean: 'Title'", out["message"])
        self.assertIn("Strips on channel 2", out["message"])
        self.assertIn("Nothing to change", mod.main(mod.Params(strip_name="Title"))._asdict()["message"])
        self.assertIn("channel must be 1-128", mod.main(mod.Params(strip_name="Title", channel=0))._asdict()["message"])
        self.assertIn("must be after start", mod.main(mod.Params(strip_name="Title", end=5))._asdict()["message"])

    def test_locked_strip(self):
        strips = _timeline()
        strips[1].lock = True
        _install_bpy(_scene(strips=strips))
        mod = _load("set_strip_frame_range_toolcode")
        out = mod.main(mod.Params(strip_name="Title", start=200))._asdict()
        self.assertEqual(out["status"], "error")
        self.assertIn("is locked", out["message"])
        self.assertEqual(strips[1].left_handle, 10)


class TestRenderPreview(_BpyTest):

    def test_background_render_and_settings_put_back(self):
        scene = _scene(strips=_timeline())
        _bpy, calls = _install_bpy(scene)
        mod = _load("render_vse_preview_toolcode")
        out = mod.main(mod.Params(frame=30, max_size=480))._asdict()
        self.assertEqual(out["status"], "ok", out["message"])
        self.assertEqual((out["frame"], out["width"], out["height"]), (30, 480, 270))
        self.assertTrue(out["filepath"].endswith(".jpg") and os.path.isfile(out["filepath"]))
        _args, kwargs, during = calls["render"][0]
        self.assertEqual(kwargs, {"write_still": True, "scene": "Scene"})
        self.assertEqual((during["pct"], during["format"], during["frame"], during["use_sequencer"]),
                         (25, "JPEG", 30, True))
        rd = scene.render
        self.assertEqual((rd.filepath, rd.resolution_percentage, rd.use_sequencer, rd.image_settings.media_type,
                          rd.image_settings.file_format, scene.frame_current),
                         ("//out/", 100, False, "VIDEO", "FFMPEG", 7))

    def test_no_strips(self):
        _install_bpy(_scene(strips=[]))
        mod = _load("render_vse_preview_toolcode")
        out = mod.main(mod.Params())._asdict()
        self.assertEqual(out["status"], "error")
        self.assertIn("nothing to preview", out["message"])

    def test_size_cap(self):
        mod = _load("render_vse_preview_toolcode")
        self.assertEqual(mod._preview_percentage(1920, 1080, 1024), 53)
        self.assertEqual(mod._preview_percentage(640, 360, 1024), 100)
        self.assertEqual(mod._clamp_max_size(10), 64)
        self.assertEqual(mod._clamp_max_size(99999), 2048)
        self.assertEqual(mod._clamp_max_size(0), 1024)


class TestPreviewContent(unittest.TestCase):
    """The MCP side reads the preview file; errors and jobs stay text."""

    def setUp(self):
        try:
            sys.path.insert(0, str(_REPO / "mcp"))
            from blmcp.tools import render_vse_preview  # pylint: disable=import-outside-toplevel
        except ImportError as ex:  # the mcp package isn't installed here
            self.skipTest(str(ex))
        finally:
            sys.path.remove(str(_REPO / "mcp"))
        self.mod = render_vse_preview

    def test_image_read(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
            fh.write(b"\xff\xd8\xff data")
        try:
            result, image = self.mod.preview_content({"status": "ok", "result": {"status": "ok", "filepath": fh.name}})
            self.assertEqual(image, b"\xff\xd8\xff data")
            self.assertEqual(result["filepath"], fh.name)
        finally:
            os.remove(fh.name)

    def test_approved_step_wrapped_twice(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
            fh.write(bytes([255, 216]))
        try:
            inner = {"status": "ok", "filepath": fh.name}
            result, image = self.mod.preview_content({"status": "ok", "result": {"status": "ok", "result": inner}})
            self.assertEqual((result, image), (inner, bytes([255, 216])))
        finally:
            os.remove(fh.name)

    def test_error_and_job(self):
        self.assertEqual(self.mod.preview_content({"status": "error", "message": "boom"}),
                         ({"status": "error", "message": "boom"}, None))
        job = {"status": "ok", "state": "running", "job_id": "job1"}
        self.assertEqual(self.mod.preview_content({"status": "ok", "result": job}), (job, None))


# -- 3. Registration ----------------------------------------------------------------------

class TestRegistration(unittest.TestCase):

    def test_in_the_vse_domain_of_both_lists(self):
        server = (_REPO / "mcp" / "blmcp" / "tool_domains.py").read_text(encoding="utf-8")
        loop = (_REPO / "addon" / "bfa_coworker" / "agent_controller.py").read_text(encoding="utf-8")
        server_vse = re.search(r'"vse": frozenset\(\{(.*?)\}\)', server, re.S).group(1)
        loop_vse = re.search(r'"vse": frozenset\(\{(.*?)\}\)', loop, re.S).group(1)
        for name in _NAMES:
            self.assertIn('"{:s}"'.format(name), server_vse, name)
            self.assertIn('"{:s}"'.format(name), loop_vse, name)
            self.assertTrue((_TOOLS / (name + ".py")).is_file() and (_TOOLS / (name + "_toolcode.py")).is_file())

    def test_the_read_tools_never_ask_in_ask_mode(self):
        # A Local / Remote turn in Ask mode asks before every tool that isn't in PLAN_SAFE_TOOLS.
        engine = (_REPO / "addon" / "bfa_coworker" / "engine.py").read_text(encoding="utf-8")
        safe = re.search(r"PLAN_SAFE_TOOLS: frozenset\[str\] = frozenset\(\{(.*?)\}\)", engine, re.S).group(1)
        for name in ("get_vse_timeline_overview", "get_vse_strips_summary", "get_vse_strip_detail"):
            self.assertIn('"{:s}"'.format(name), safe)
        for name in ("set_strip_frame_range", "render_vse_preview"):      # a change, a render: as render_viewport_to_path
            self.assertNotIn('"{:s}"'.format(name), safe)

    def test_annotations(self):
        for name in _NAMES:
            text = (_TOOLS / (name + ".py")).read_text(encoding="utf-8")
            if name == "set_strip_frame_range":
                self.assertIn("destructiveHint=True", text)
            else:
                self.assertIn("readOnlyHint=True", text)

    def test_preview_is_handled_like_the_screenshots_in_the_local_loop(self):
        loop = (_REPO / "addon" / "bfa_coworker" / "agent_controller.py").read_text(encoding="utf-8")
        self.assertRegex(loop, r'"get_screenshot_of_window_as_image",\s*"render_vse_preview"\)')


if __name__ == "__main__":
    unittest.main()
