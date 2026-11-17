# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the render follow-ups after the preview profile (#527, and #541's first box).

1. The loop guard counts a render that failed like any repeat when the retry only changes
   its file name (``output_path``): the local loop's arguments and the bridge's tool-code
   alike; a render that worked doesn't count toward another file name.
2. The bridge stopping in the middle of a deferred render (``deferred_tool.close_all``):
   the render goes on as a long job and still puts its settings back when it ends, also
   with no bridge timer left (a timer of its own keeps the jobs checked).

No Blender needed.  Run with::

    python -m unittest tests.test_render_followups_527 -v
"""

__all__ = ()

import importlib
import json
import socket
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]
_TOOLS = _REPO / "mcp" / "blmcp" / "tools"
_ADDON = _REPO / "addon" / "bfa_coworker"


def _pkg() -> str:
    name = "bfacw_527_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return name


def _mod(name: str) -> types.ModuleType:
    return importlib.import_module(_pkg() + "." + name)


lg = _mod("loop_guard")

_FAILED = {"status": "error", "message": "The job ended without writing look.png (it was cancelled or failed)"}
_OK = {"status": "ok", "filepath": "/tmp/bfa_coworker/look.png"}


def _bridge_code(path: str, quality: str = "preview") -> dict[str, str]:
    """What the bridge counts for a render: the tool-code with its Params (repr), as the MCP
    server sends it."""
    return {"code": "def main(params): ...\nmain(Params(output_path={!r}, background=False, quality={!r}))".format(
        path, quality)}


# -- 1. The loop guard ------------------------------------------------------------------------

class TestRenderFailuresCount(unittest.TestCase):

    def test_a_failed_render_retried_under_other_names_asks_on_the_third(self):
        g = lg.LoopGuard()
        verdicts = []
        for i in range(4):
            args = {"output_path": "look_{:d}.png".format(i)}
            verdict, nth = g.check("render_viewport_to_path", args)
            verdicts.append((verdict, nth))
            g.note_result("render_viewport_to_path", args, _FAILED)
        self.assertEqual(verdicts, [(lg.RUN, 1), (lg.RUN, 2), (lg.ASK, 3), (lg.REFUSE, 4)])

    def test_renders_that_worked_dont_count_toward_another_name(self):
        g = lg.LoopGuard()
        for i in range(6):
            args = {"output_path": "look_{:d}.png".format(i)}
            self.assertEqual(g.check("render_viewport_to_path", args), (lg.RUN, 1))
            g.note_result("render_viewport_to_path", args, {"status": "ok", "result": _OK})

    def test_another_quality_is_another_render(self):
        g = lg.LoopGuard()
        for i in range(2):
            args = {"output_path": "final_{:d}.png".format(i), "quality": "final"}
            g.check("render_viewport_to_path", args)
            g.note_result("render_viewport_to_path", args, _FAILED)
        # The hint the failure gives: look with the preview instead.  Not a repeat.
        self.assertEqual(g.check("render_viewport_to_path", {"output_path": "p.png", "quality": "preview"}),
                         (lg.RUN, 1))
        self.assertEqual(g.check("render_viewport_to_path", {"output_path": "f2.png", "quality": "final"}),
                         (lg.ASK, 3))

    def test_the_same_name_is_not_counted_twice(self):
        g = lg.LoopGuard()
        args = {"output_path": "look.png"}
        got = []
        for _ in range(3):
            got.append(g.check("render_viewport_to_path", args))
            g.note_result("render_viewport_to_path", args, _FAILED)
        self.assertEqual(got, [(lg.RUN, 1), (lg.RUN, 2), (lg.ASK, 3)])

    def test_the_local_loops_text_answers(self):
        g = lg.LoopGuard()
        args = {"output_path": "t.png"}
        g.check("render_thumbnail_to_path", args)
        # The local loop gets the MCP answer as text: the envelope, the tool's own status inside.
        g.note_result("render_thumbnail_to_path", args, json.dumps({"status": "ok", "result": _FAILED}))
        self.assertEqual(g.check("render_thumbnail_to_path", {"output_path": "t2.png"}), (lg.RUN, 2))
        g.note_result("render_thumbnail_to_path", args, 'cut off {"status": "error", "message": "no camera')
        self.assertEqual(g.check("render_thumbnail_to_path", {"output_path": "t3.png"}), (lg.ASK, 3))

    def test_a_stopped_render_is_not_a_failure(self):
        g = lg.LoopGuard()
        args = {"output_path": "look.png"}
        g.check("render_viewport_to_path", args)
        g.note_result("render_viewport_to_path", args, {"status": "stopped", "message": "Stopped in Blender"})
        self.assertEqual(g.check("render_viewport_to_path", {"output_path": "x.png"}), (lg.RUN, 1))

    def test_the_bridges_code_is_counted_without_the_file_name(self):
        g = lg.LoopGuard()
        got = []
        for i, path in enumerate(("a.png", "it's b.png", 'C:\\renders\\"c".png', "d.png")):
            code = _bridge_code(path)
            got.append(g.check("render_viewport_to_path", code, "s1")[0])
            g.note_result("render_viewport_to_path", code, {"status": "ok", "result": _FAILED}, "s1")
            del i
        self.assertEqual(got, [lg.RUN, lg.RUN, lg.ASK, lg.REFUSE])
        # Another session counts on its own; a new turn forgets.
        self.assertEqual(g.check("render_viewport_to_path", _bridge_code("e.png"), "s2"), (lg.RUN, 1))
        g.new_turn()
        self.assertEqual(g.check("render_viewport_to_path", _bridge_code("f.png"), "s1"), (lg.RUN, 1))

    def test_other_tools_are_counted_as_before(self):
        g = lg.LoopGuard()
        for i in range(4):
            args = {"code": "x = {:d}".format(i)}
            self.assertEqual(g.check("execute_blender_code", args), (lg.RUN, 1))
            g.note_result("execute_blender_code", args, _FAILED)

    def test_a_write_in_between_clears_the_failures(self):
        g = lg.LoopGuard()
        for i in range(2):
            args = {"output_path": "look_{:d}.png".format(i)}
            g.check("render_viewport_to_path", args)
            g.note_result("render_viewport_to_path", args, _FAILED)
        g.check("execute_blender_code", {"code": "add_camera()"})
        self.assertEqual(g.check("render_viewport_to_path", {"output_path": "new.png"}), (lg.RUN, 1))

    def test_a_success_clears_the_failures(self):
        g = lg.LoopGuard()
        for i in range(2):
            args = {"output_path": "look_{:d}.png".format(i)}
            g.check("render_viewport_to_path", args)
            g.note_result("render_viewport_to_path", args, _FAILED)
        args = {"output_path": "ok.png"}
        self.assertEqual(g.check("render_viewport_to_path", args)[0], lg.ASK)
        g.note_result("render_viewport_to_path", args, {"status": "ok", "result": _OK})
        self.assertEqual(g.check("render_viewport_to_path", {"output_path": "next.png"}), (lg.RUN, 1))

    def test_three_failures_in_a_row_are_still_refused(self):
        g = lg.LoopGuard()
        for i in range(3):
            args = {"output_path": "look_{:d}.png".format(i)}
            g.check("render_viewport_to_path", args)
            g.note_result("render_viewport_to_path", args, _FAILED)
        self.assertEqual(g.check("render_viewport_to_path", {"output_path": "x.png"}), (lg.REFUSE, 4))

    def test_the_card_released_render_is_watched(self):
        server = (_ADDON / "mcp_to_blender_server.py").read_text(encoding="utf-8")
        block = server[server.index("def _proceed()"):]
        block = block[:block.index("_repeat = loop_guard.bridge_wrap(")]
        self.assertIn("loop_guard.bridge_watch(", block)

    def test_the_card_released_render_failure_is_counted(self):
        g = lg.LoopGuard()
        approvals = mock.Mock()
        approvals.allowed_always.return_value = False
        asked = []
        approvals.ask.side_effect = lambda req, cb: asked.append(cb)
        code = _bridge_code("c.png")
        for i in range(2):
            c = _bridge_code("f{:d}.png".format(i))
            g.check("render_viewport_to_path", c, "s")
            g.note_result("render_viewport_to_path", c, _FAILED, "s")
        state = [None]

        def inner():
            return state[0]
        inner.job_kind = "RENDER"

        def proceed():
            return {}, lg.bridge_watch(g, "render_viewport_to_path", code, "s", "", {}, inner)
        wrapped = lg.bridge_wrap(g, approvals, False, "render_viewport_to_path", code, "s", proceed)
        _response, check = wrapped
        asked[0]("allow")
        self.assertIsNone(check())
        state[0] = dict(_FAILED)
        check()
        self.assertEqual(g.check("render_viewport_to_path", _bridge_code("d.png"), "s")[0], lg.REFUSE)

    def test_bridge_watch_notes_a_direct_answer(self):
        g = lg.LoopGuard()
        code = _bridge_code("a.png")
        g.check("render_thumbnail_to_path", code, "s1/t")
        self.assertIsNone(lg.bridge_watch(g, "render_thumbnail_to_path", code, "s1", "t",
                                          {"status": "ok", "result": _FAILED}, None))
        self.assertEqual(g.check("render_thumbnail_to_path", _bridge_code("b.png"), "s1/t"), (lg.RUN, 2))

    def test_bridge_watch_notes_a_deferred_answer_and_keeps_the_job_attributes(self):
        g = lg.LoopGuard()
        code = _bridge_code("a.png")
        g.check("render_viewport_to_path", code, "s1")
        state = [None]

        def check():
            return state[0]
        check.job_kind, check.job_label, check.job_on_finish = "RENDER", "Render a.png", object()
        watched = lg.bridge_watch(g, "render_viewport_to_path", code, "s1", "", {}, check)
        self.assertEqual((watched.job_kind, watched.job_label, watched.job_on_finish),
                         (check.job_kind, check.job_label, check.job_on_finish))
        self.assertIsNone(watched())
        self.assertEqual(g.check("render_viewport_to_path", _bridge_code("b.png"), "s1"), (lg.RUN, 1))
        state[0] = dict(_FAILED)
        self.assertEqual(watched(), _FAILED)
        self.assertEqual(g.check("render_viewport_to_path", _bridge_code("c.png"), "s1"), (lg.RUN, 2))

    def test_bridge_watch_leaves_other_tools_alone(self):
        def check():
            return None
        self.assertIs(lg.bridge_watch(lg.LoopGuard(), "execute_blender_code", {"code": "x"}, "s", "", {}, check),
                      check)

    def test_both_loops_report_the_answer(self):
        server = (_ADDON / "mcp_to_blender_server.py").read_text(encoding="utf-8")
        block = server[server.index("loop_guard.bridge_wrap("):]
        block = block[:block.index("if exec_result is None")]
        self.assertIn("loop_guard.bridge_watch(", block)
        controller = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
        self.assertIn("_repeat_guard.note_result(tool_name, args, result_text)", controller)


# -- 2. The bridge stops mid-render --------------------------------------------------------------

class TestBridgeStopsMidRender(unittest.TestCase):

    def setUp(self):
        self.deferred = _mod("deferred_tool")
        self.jobs = _mod("long_jobs")
        self.jobs.clear()
        self.deferred._deferred_clients.clear()
        self.addCleanup(self.jobs.clear)
        self.timers = []
        fake = types.ModuleType("bpy")
        fake.app = types.SimpleNamespace(timers=types.SimpleNamespace(
            register=lambda fn, first_interval=0.0, persistent=False: self.timers.append(fn),
            is_registered=lambda fn: fn in self.timers,
            unregister=self.timers.remove))
        patcher = mock.patch.dict(sys.modules, {"bpy": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _render(self, answers):
        server_end, client_end = socket.socketpair()
        server_end.setblocking(False)
        self.addCleanup(client_end.close)
        result, restored = [None], []

        def check_is_finished():
            return result[0]
        check_is_finished.job_kind = "RENDER"
        check_is_finished.job_label = "Render look.png"
        check_is_finished.job_on_finish = lambda: restored.append(1)
        self.deferred.add(server_end, check_is_finished, True, "", "", on_answer=answers.append)
        return result, restored

    def test_the_render_goes_on_and_restores_when_it_ends(self):
        answers = []
        result, restored = self._render(answers)
        self.deferred.close_all()                      # the bridge stops mid-render
        self.assertFalse(self.deferred.has_pending())
        self.assertIn("bridge stopped", answers[0]["message"])
        self.assertEqual([j["label"] for j in self.jobs.running()], ["Render look.png"])
        self.assertEqual(restored, [])                 # not while it still renders
        self.assertEqual(len(self.timers), 1, "the jobs are checked without the bridge's timer")
        tick = self.timers[0]
        with mock.patch.object(_mod("mcp_to_blender_server"), "is_running", lambda: False):
            self.assertIsNotNone(tick())               # still rendering: check again
            result[0] = {"status": "ok", "filepath": "look.png"}
            self.assertIsNone(tick())                  # over: restored, and the timer ends
        self.assertEqual(restored, [1])
        self.assertEqual(self.jobs.running(), [])

    def test_a_running_bridge_takes_over_the_checks(self):
        result, restored = self._render([])
        self.deferred.close_all()
        with mock.patch.object(_mod("mcp_to_blender_server"), "is_running", lambda: True):
            self.assertIsNone(self.timers[0]())        # the bridge's own timer polls the jobs again
        self.assertEqual(restored, [])
        result[0] = {"status": "ok"}
        self.jobs.poll()
        self.assertEqual(restored, [1])

    def test_other_waits_are_just_closed(self):
        server_end, client_end = socket.socketpair()
        server_end.setblocking(False)
        self.addCleanup(client_end.close)
        self.deferred.add(server_end, lambda: None, True, "", "")      # e.g. ask_user
        self.deferred.close_all()
        self.assertEqual(self.jobs.running(), [])
        self.assertEqual(self.timers, [])

    def test_a_job_handed_off_earlier_is_checked_too(self):
        restored = []
        state = [None]
        self.jobs.start("RENDER", "Render bg.png", lambda: state[0], lambda: restored.append(1))
        self.deferred.close_all()                      # nothing deferred, but a background render runs
        self.assertEqual(len(self.timers), 1)
        state[0] = {"status": "ok"}
        with mock.patch.object(_mod("mcp_to_blender_server"), "is_running", lambda: False):
            self.assertIsNone(self.timers[0]())
        self.assertEqual(restored, [1])


# -- 3. Scene strips render under the preview caps --------------------------------------------------

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


PROFILE = _load("_template_render_preview_profile")


def _render_settings(res=(3840, 2160)):
    return types.SimpleNamespace(
        engine="CYCLES", resolution_x=res[0], resolution_y=res[1], resolution_percentage=100, use_simplify=False,
        simplify_subdivision_render=6, use_compositing=True, use_sequencer=True, use_motion_blur=True,
        use_persistent_data=True, filepath="//shot_",
        image_settings=types.SimpleNamespace(media_type="VIDEO", file_format="FFMPEG", color_mode="RGB", quality=90))


def _shot(name="Shot3D", strips=()):
    """A 3D scene a scene strip pulls in: 4K, 4096 samples, no time limit."""
    return types.SimpleNamespace(
        name=name, render=_render_settings(), frame_current=1,
        cycles=types.SimpleNamespace(samples=4096, time_limit=0.0, use_denoising=True, device="GPU"),
        eevee=types.SimpleNamespace(taa_render_samples=4096),
        sequence_editor=types.SimpleNamespace(strips_all=list(strips)) if strips else None)


def _strip(kind, name, scene=None):
    return types.SimpleNamespace(type=kind, name=name, scene=scene)


def _settings(scene):
    rd, ims = scene.render, scene.render.image_settings
    d = {k: getattr(rd, k) for k in ("resolution_x", "resolution_y", "resolution_percentage", "use_simplify",
                                     "simplify_subdivision_render", "use_compositing", "use_sequencer",
                                     "use_motion_blur", "use_persistent_data", "filepath")}
    d.update({"image." + k: getattr(ims, k) for k in vars(ims)})
    for part in ("cycles", "eevee"):
        if hasattr(scene, part):
            d.update({part + "." + k: v for k, v in vars(getattr(scene, part)).items()})
    d["frame_current"] = scene.frame_current
    return d


class TestStripScenesProfile(unittest.TestCase):

    def test_every_scene_a_strip_pulls_in_once_inside_metas_and_nested(self):
        inner = _shot("Inner")
        shot = _shot("Shot3D", strips=[_strip("SCENE", "inner", inner)])
        edit = _shot("Edit", strips=[_strip("COLOR", "bg"), _strip("SCENE", "a", shot), _strip("SCENE", "b", shot),
                                     _strip("SCENE", "empty", None), _strip("META", "m")])
        edit.sequence_editor.strips_all.append(_strip("SCENE", "loop", edit))       # never the scene itself
        self.assertEqual([s.name for s in PROFILE._preview_strip_scenes(edit)], ["Shot3D", "Inner"])

    def test_samples_and_passes_capped_size_and_output_kept_then_restored(self):
        shot = _shot()
        edit = _shot("Edit", strips=[_strip("SCENE", "shot", shot)])
        before, edit_before = _settings(shot), _settings(edit)
        saved, names = PROFILE._preview_profile_apply_strip_scenes(edit)
        self.assertEqual(names, ["Shot3D"])
        d = _settings(shot)
        self.assertEqual((d["cycles.samples"], d["cycles.time_limit"], d["eevee.taa_render_samples"]),
                         (PROFILE._PREVIEW_MAX_SAMPLES, PROFILE._PREVIEW_TIME_LIMIT_S, PROFILE._PREVIEW_MAX_SAMPLES))
        self.assertTrue(d["use_simplify"])
        for attr in ("use_compositing", "use_motion_blur", "use_persistent_data"):
            self.assertFalse(d[attr], attr)
        # The Sequencer sizes the strip by its scene's resolution: kept (probed on 5.2.4: a smaller
        # strip scene shows smaller in the frame).  Its own Sequencer pass and output format: kept.
        for key in ("resolution_x", "resolution_y", "resolution_percentage", "use_sequencer", "filepath",
                    "image.media_type", "image.file_format"):
            self.assertEqual(d[key], before[key], key)
        self.assertEqual(_settings(edit), edit_before, "the Sequencer scene itself is not touched here")
        PROFILE._preview_profile_restore(saved)
        self.assertEqual(_settings(shot), before)

    def test_no_scene_strips_no_changes(self):
        edit = _shot("Edit", strips=[_strip("COLOR", "bg")])
        self.assertEqual(PROFILE._preview_profile_apply_strip_scenes(edit), ([], []))
        self.assertEqual(PROFILE._preview_profile_apply_strip_scenes(_shot("Bare")), ([], []))


class TestTextureLimit(unittest.TestCase):
    """Cycles' render texture limit is part of the profile (#527), put back with the rest."""

    def _scene(self, limit):
        scene = _shot("Scene")
        scene.cycles.texture_limit_render = limit
        return scene

    def test_capped_and_restored(self):
        for limit, want in (("OFF", "2048"), ("8192", "2048"), ("4096", "2048"), ("2048", "2048"), ("512", "512")):
            scene = self._scene(limit)
            saved, caps = PROFILE._preview_profile_apply(scene)
            self.assertEqual(scene.cycles.texture_limit_render, want, limit)
            self.assertEqual(caps["texture_limit_px"], int(want))
            self.assertIn("Cycles textures at most {:s} px".format(want), PROFILE._preview_caps_text(caps))
            changed = [a for _o, a, _p in saved if a == "texture_limit_render"]
            self.assertEqual(bool(changed), want != limit, "only recorded when it changed")
            PROFILE._preview_profile_restore(saved)
            self.assertEqual(scene.cycles.texture_limit_render, limit)

    def test_thumbnail_and_strip_scenes(self):
        scene = self._scene("OFF")
        PROFILE._preview_profile_apply(scene, texture_limit=PROFILE._THUMB_TEXTURE_LIMIT)
        self.assertEqual(scene.cycles.texture_limit_render, "1024")
        shot = self._scene("OFF")
        edit = _shot("Edit", strips=[_strip("SCENE", "s", shot)])
        saved, _names = PROFILE._preview_profile_apply_strip_scenes(edit)
        self.assertEqual(shot.cycles.texture_limit_render, "2048")
        PROFILE._preview_profile_restore(saved)
        self.assertEqual(shot.cycles.texture_limit_render, "OFF")

    def test_a_build_without_it_is_left_alone(self):
        scene = _shot("Scene")              # no texture_limit_render
        _saved, caps = PROFILE._preview_profile_apply(scene)
        self.assertNotIn("texture_limit_px", caps)
        self.assertNotIn("textures", PROFILE._preview_caps_text(caps))


def _vse_scene(name="Edit", strips=()):
    scene = _shot(name, strips=strips)
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.use_sequencer = False
    scene.frame_current = 7
    return scene


class _VseBpy(types.ModuleType):
    """``bpy`` for render_vse_preview: ``render.render(scene=...)`` copies the *context* scene's
    frame onto that scene before it renders (as Blender's operator does, #541), records the
    strip scene's samples, writes the JPEG when the job ends; ``stop=True`` makes Cycles' status
    line say "Cancelled" first (the job bar's X, probed on 5.2.4)."""

    def __init__(self, edit, window_scene, tempdir, background=False):
        super().__init__("bpy")
        self.edit, self.window_scene, self.jobs, self.seen = edit, window_scene, set(), []
        self.handlers = types.SimpleNamespace(render_cancel=[], render_stats=[])
        self.app = types.SimpleNamespace(background=background, tempdir=tempdir, handlers=self.handlers,
                                         is_job_running=lambda kind: kind in self.jobs)

        class _Ctx:
            scene = window_scene
            sequencer_scene = None
            window = None
            window_manager = types.SimpleNamespace(windows=[object()])
            preferences = types.SimpleNamespace(view=types.SimpleNamespace(render_display_type="WINDOW"),
                                                is_dirty=False)
        self.context = _Ctx()
        class _Scenes(list):
            def get(self, name, default=None):
                return next((s for s in self if s.name == name), default)
        self.data = types.SimpleNamespace(scenes=_Scenes([edit, window_scene]))
        self.ops = types.SimpleNamespace(render=types.SimpleNamespace(render=self._render))

    def _render(self, *args, **kwargs):
        target = self.edit if kwargs.get("scene") == self.edit.name else self.context.scene
        if kwargs.get("scene"):
            target.frame_current = self.context.scene.frame_current      # Blender's own copy
        shots = [s.scene for s in self.edit.sequence_editor.strips_all if s.type == "SCENE"]
        self.seen.append({"frame": target.frame_current, "kwargs": kwargs,
                          "shot_samples": [s.cycles.samples for s in shots]})
        if "INVOKE_DEFAULT" in args:
            self.jobs.add("RENDER")
            return {"RUNNING_MODAL"}
        self._write()
        return {"FINISHED"}

    def _write(self):
        import os
        os.makedirs(os.path.dirname(self.edit.render.filepath), exist_ok=True)
        with open(self.edit.render.filepath, "wb") as fh:
            fh.write(b"\xff\xd8\xff partial" if self.seen else b"\xff\xd8\xff")

    def finish_job(self, write=True, stop=False):
        if stop:
            for fn in list(self.handlers.render_stats):
                fn("Mem: 43M | Cancelled")
        else:
            for fn in list(self.handlers.render_stats):
                fn("Mem: 43M | Finished")
        if write:
            self._write()
        self.jobs.discard("RENDER")


class TestVsePreview(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tool = _load("render_vse_preview_toolcode")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(sys.modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.shot = _shot()
        self.edit = _vse_scene(strips=[_strip("SCENE", "Shot", self.shot), _strip("COLOR", "bg")])
        self.window_scene = _vse_scene("Scene")
        self.window_scene.frame_current = 1

    def _bpy(self, **kw):
        fake = _VseBpy(self.edit, self.window_scene, self._tmp.name, **kw)
        sys.modules["bpy"] = fake
        return fake

    def test_the_frame_asked_for_is_rendered_for_a_scene_the_window_doesnt_show(self):
        fake = self._bpy()
        check = self.tool.main(self.tool.Params(frame=25, max_size=256, scene_name="Edit"))
        self.assertEqual(fake.seen[0]["frame"], 25, "the frame Blender copies from the context scene")
        self.assertEqual(fake.seen[0]["kwargs"], {"write_still": True, "scene": "Edit"})
        self.assertEqual(self.window_scene.frame_current, 1, "the window's scene is back on its frame at once")
        fake.finish_job()
        self.assertEqual(check()["frame"], 25)
        self.assertEqual(self.edit.frame_current, 7)

    def test_each_frame_is_its_own_render(self):
        frames = []
        for f in (5, 15, 25):
            fake = self._bpy()
            check = self.tool.main(self.tool.Params(frame=f, max_size=128, scene_name="Edit"))
            frames.append(fake.seen[0]["frame"])
            fake.finish_job()
            check()
        self.assertEqual(frames, [5, 15, 25])
        self.assertEqual((self.window_scene.frame_current, self.edit.frame_current), (1, 7))

    def test_the_window_frame_comes_back_when_the_render_cant_start(self):
        fake = self._bpy()

        def boom(*args, **kwargs):
            raise RuntimeError("Error: no camera")
        fake.ops.render.render = boom
        result = self.tool.main(self.tool.Params(frame=25, scene_name="Edit"))._asdict()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.window_scene.frame_current, 1)
        self.assertEqual(self.shot.cycles.samples, 4096, "the strip scene's caps are back too")

    def test_the_strip_scene_renders_capped_and_everything_goes_back(self):
        shot_before, edit_before = _settings(self.shot), _settings(self.edit)
        fake = self._bpy()
        check = self.tool.main(self.tool.Params(frame=3, max_size=256, scene_name="Edit"))
        self.assertEqual(fake.seen[0]["shot_samples"], [PROFILE._PREVIEW_MAX_SAMPLES])
        self.assertIsNone(check())
        self.assertEqual(self.shot.cycles.samples, PROFILE._PREVIEW_MAX_SAMPLES, "capped while the job renders")
        fake.finish_job()
        result = check()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["strip_scenes"], ["Shot3D"])
        self.assertIn("'Shot3D'", result["message"])
        self.assertEqual(_settings(self.shot), shot_before)
        self.assertEqual(_settings(self.edit), edit_before)
        self.assertEqual((fake.handlers.render_cancel, fake.handlers.render_stats), ([], []), "the stop handlers go with the job")

    def test_a_job_handed_off_restores_on_its_end(self):
        shot_before = _settings(self.shot)
        fake = self._bpy()
        check = self.tool.main(self.tool.Params(frame=3, scene_name="Edit"))
        fake.finish_job(write=False)
        check.job_on_finish()                       # long_jobs: a cancel ends it before its checker
        self.assertEqual(_settings(self.shot), shot_before)
        self.assertEqual((fake.handlers.render_cancel, fake.handlers.render_stats), ([], []))

    def test_a_stopped_render_answers_stopped_with_the_partial_image(self):
        fake = self._bpy()
        check = self.tool.main(self.tool.Params(frame=3, scene_name="Edit"))
        fake.finish_job(write=True, stop=True)
        result = check()
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(result["partial"])
        self.assertTrue(result["filepath"].endswith(".jpg"))
        self.assertIn("stopped in Blender", result["message"])
        fake = self._bpy()
        check = self.tool.main(self.tool.Params(frame=4, scene_name="Edit"))
        fake.finish_job(write=False, stop=True)
        result = check()
        self.assertEqual((result["status"], result["partial"]), ("stopped", False))
        self.assertIn("wrote no image", result["message"])

    def test_background_mode_blocks_and_restores(self):
        shot_before = _settings(self.shot)
        fake = self._bpy(background=True)
        result = self.tool.main(self.tool.Params(frame=12, max_size=128, scene_name="Edit"))._asdict()
        self.assertEqual(result["status"], "ok", result["message"])
        self.assertEqual(fake.seen[0]["frame"], 12)
        self.assertEqual(fake.seen[0]["shot_samples"], [PROFILE._PREVIEW_MAX_SAMPLES])
        self.assertEqual(_settings(self.shot), shot_before)

    def test_the_mcp_side_shows_a_stopped_renders_image(self):
        src = (_TOOLS / "render_vse_preview.py").read_text(encoding="utf-8")
        self.assertIn('not in ("ok", "stopped")', src)


# -- 4. A stopped render answers "stopped" ------------------------------------------------------------

class _ViewBpy(types.ModuleType):
    """A render job; ``stop`` "x" is the job bar's X (Cycles' status line says "Cancelled", the
    job ends as done: probed on 5.2.4), "esc" Blender's render_cancel handler."""

    def __init__(self, scene, tempdir):
        super().__init__("bpy")
        self.scene, self.jobs = scene, set()
        self.handlers = types.SimpleNamespace(render_cancel=[], render_stats=[])
        self.app = types.SimpleNamespace(background=False, tempdir=tempdir, handlers=self.handlers,
                                         is_job_running=lambda kind: kind in self.jobs)
        self.context = types.SimpleNamespace(scene=scene, window=object(),
                                             window_manager=types.SimpleNamespace(windows=[object()]))
        self.ops = types.SimpleNamespace(render=types.SimpleNamespace(render=self._render))

    def _render(self, *args, **kwargs):
        self.jobs.add("RENDER")
        return {"RUNNING_MODAL"}

    def finish_job(self, write=True, stop=""):
        import os
        for fn in list(self.handlers.render_stats):
            fn("Remaining: 00:05.43 | Mem: 43M | Sample 8/32")
            fn("Mem: 43M | Cancelled" if stop == "x" else "Mem: 43M | Finished")
        if stop == "esc":
            for fn in list(self.handlers.render_cancel):
                fn(self.scene)
        if write:
            os.makedirs(os.path.dirname(self.scene.render.filepath), exist_ok=True)
            with open(self.scene.render.filepath, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
        self.jobs.discard("RENDER")


class TestStoppedRender(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(sys.modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _scene(self):
        scene = _shot("Scene")
        scene.camera = object()
        scene.objects = []
        return scene

    def _run(self, tool, params, stop, write):
        scene = self._scene()
        before = _settings(scene)
        fake = _ViewBpy(scene, self._tmp.name)
        sys.modules["bpy"] = fake
        mod = _load(tool + "_toolcode")
        check = mod.main(mod.Params(**params))
        self.assertEqual((len(fake.handlers.render_cancel), len(fake.handlers.render_stats)), (1, 1),
                         "a stop is listened for while it renders")
        self.assertIsNone(check())
        fake.finish_job(write=write, stop=stop)
        result = check()
        self.assertEqual(_settings(scene), before, "the settings go back however it ends")
        self.assertEqual((fake.handlers.render_cancel, fake.handlers.render_stats), ([], []))
        return result, check

    def test_viewport_preview_stopped_with_a_partial_image(self):
        result, check = self._run("render_viewport_to_path", {"output_path": "look.png"}, stop="x", write=True)
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(result["partial"])
        self.assertTrue(result["filepath"].endswith("look.png"))
        self.assertIn("stopped in Blender", result["message"])
        self.assertIn("Quality: preview", result["message"])
        self.assertNotIn("trying again won't help", result["message"])

    def test_viewport_stopped_without_an_image(self):
        result, _check = self._run("render_viewport_to_path", {"output_path": "look.png", "quality": "final"},
                                   stop="esc", write=False)
        self.assertEqual((result["status"], result["partial"], result["filepath"]), ("stopped", False, None))
        self.assertIn("wrote no image", result["message"])

    def test_an_object_named_cancel_is_not_a_stop(self):
        scene = self._scene()
        fake = _ViewBpy(scene, self._tmp.name)
        sys.modules["bpy"] = fake
        mod = _load("render_viewport_to_path_toolcode")
        check = mod.main(mod.Params(output_path="look.png"))
        for fn in list(fake.handlers.render_stats):
            fn("Remaining: 00:05 | Mem: 43M | Synchronizing object | CancelButton")
        fake.finish_job(write=True, stop="")
        self.assertEqual(check()["status"], "ok")

    def test_a_cancelled_status_line_is_a_stop(self):
        result, _check = self._run("render_viewport_to_path", {"output_path": "look.png"}, stop="x", write=True)
        self.assertEqual(result["status"], "stopped")

    def test_a_render_that_finished_is_still_ok(self):
        result, check = self._run("render_viewport_to_path", {"output_path": "look.png"}, stop="", write=True)
        self.assertEqual(result["status"], "ok")

    def test_thumbnail_stopped(self):
        result, _check = self._run("render_thumbnail_to_path", {"output_path": "t.png"}, stop="x", write=True)
        self.assertEqual(result["status"], "stopped")
        self.assertIn("Thumbnail:", result["message"])


class TestLongJobsStopped(unittest.TestCase):

    def setUp(self):
        self.jobs = _mod("long_jobs")
        self.jobs.clear()
        self.addCleanup(self.jobs.clear)

    def test_a_stopped_answer_is_a_cancelled_job_with_its_result(self):
        job_id = self.jobs.start("RENDER", "Render look.png", lambda: {
            "status": "stopped", "filepath": "/tmp/look.png", "partial": True, "message": "stopped in Blender"})
        self.jobs.poll()
        st = self.jobs.status(job_id)
        self.assertEqual((st["status"], st["state"]), ("ok", "cancelled"))
        self.assertEqual(st["result"]["filepath"], "/tmp/look.png")
        self.assertTrue(st["result"]["partial"])

    def test_blenders_cancel_keeps_what_the_checker_found(self):
        state = [None]
        restored = []
        job_id = self.jobs.start("RENDER", "r", lambda: state[0], lambda: restored.append(1))
        state[0] = {"status": "stopped", "filepath": "/tmp/r.png", "partial": True, "message": "stopped"}
        self.jobs.cancelled("RENDER")
        self.jobs.poll()
        st = self.jobs.status(job_id)
        self.assertEqual(st["state"], "cancelled")
        self.assertEqual(st["result"]["filepath"], "/tmp/r.png")
        self.assertEqual(restored, [1])


class TestReframeSamples(unittest.TestCase):

    def test_render_samples_caps_the_strip_scene_and_restores_in_finally(self):
        src = (_ADDON / "reframe.py").read_text(encoding="utf-8")
        body = src[src.index("def render_samples("):src.index("def _cap_strip_scenes(")]
        self.assertLess(body.index("capped = _cap_strip_scenes(tmp)"), body.index("bpy.ops.render.render("))
        finally_part = body[body.index("finally:"):]
        self.assertLess(finally_part.index("_uncap(capped)"), finally_part.index("bpy.data.scenes.remove(tmp)"))
        self.assertIn('"_preview_profile_apply_strip_scenes"', src)


if __name__ == "__main__":
    unittest.main()
