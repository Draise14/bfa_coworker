# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the render preview profile (#524).

1. ``_template_render_preview_profile.py`` on stand-ins: the caps (longest edge with the
   resolution percentage folded in, samples for Cycles and EEVEE, the Cycles time limit,
   Simplify, the passes off, a PNG), only what changed is recorded, the restore is exact,
   in order (media type before file format) and happens once.
2. ``render_viewport_to_path`` tool-code on a stand-in ``bpy``: ``quality`` preview / final
   / viewport, the caps still on while the render job runs (``INVOKE_DEFAULT`` reads them
   on its own thread) and back when it is over -- done, cancelled, failed; the errors carry
   the quality and the caps; the job guard and the no-camera message.
3. ``render_thumbnail_to_path``: 320 px / 16 samples while the job runs (it used to put the
   settings back as soon as the operator returned), EEVEE capped under ``BLENDER_EEVEE``.
4. The tool argument (``quality``, default ``"preview"``) and its description.
5. The chat capture uses the same profile and refuses while a render or bake job runs;
   a deferred render whose caller hung up still puts its settings back (``deferred_tool``).

No Blender needed.  Run with::

    python -m unittest tests.test_render_preview_524 -v
"""

__all__ = ()

import ast
import importlib
import importlib.util
import os
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TOOLS = _REPO / "mcp" / "blmcp" / "tools"
_ADDON = _REPO / "addon" / "bfa_coworker"


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


# -- Stand-ins ----------------------------------------------------------------------------

class _ImageSettings:
    """Output settings that behave like 5.2's: a movie format only with the VIDEO media type
    (switching the media type picks a matching format), PNG has no 32-bit depth."""

    _MOVIES = ("FFMPEG",)

    def __init__(self, media_type="IMAGE", file_format="PNG", color_mode="RGBA", color_depth="8"):
        self._media, self._format = media_type, file_format
        self.color_mode, self.color_depth = color_mode, color_depth

    @property
    def media_type(self):
        return self._media

    @media_type.setter
    def media_type(self, value):
        self._media = value
        if value == "IMAGE" and self._format in self._MOVIES:
            self._format = "PNG"
        elif value == "VIDEO" and self._format not in self._MOVIES:
            self._format = "FFMPEG"

    @property
    def file_format(self):
        return self._format

    @file_format.setter
    def file_format(self, value):
        if (value in self._MOVIES) != (self._media == "VIDEO"):
            raise TypeError("enum {!r} not found for media type {!r}".format(value, self._media))
        self._format = value
        if value == "PNG" and self.color_depth == "32":
            self.color_depth = "16"


def _scene(engine="CYCLES", res=(3840, 2160), pct=100, image=None, camera=True, cycles=True, eevee=True):
    rd = types.SimpleNamespace(
        engine=engine, resolution_x=res[0], resolution_y=res[1], resolution_percentage=pct,
        use_simplify=False, simplify_subdivision_render=6, use_compositing=True, use_sequencer=True,
        use_motion_blur=True, use_persistent_data=True, filepath="//renders/shot_",
        image_settings=image or _ImageSettings())
    scene = types.SimpleNamespace(render=rd, camera=object() if camera else None,
                                  objects=[types.SimpleNamespace(type="CAMERA")] if camera else [])
    if cycles:
        scene.cycles = types.SimpleNamespace(samples=4096, time_limit=0.0, use_adaptive_sampling=True,
                                             use_denoising=True, device="GPU")
    if eevee:
        scene.eevee = types.SimpleNamespace(taa_render_samples=4096)
    return scene


def _dump(scene):
    rd, ims = scene.render, scene.render.image_settings
    d = {k: getattr(rd, k) for k in ("resolution_x", "resolution_y", "resolution_percentage", "use_simplify",
                                     "simplify_subdivision_render", "use_compositing", "use_sequencer",
                                     "use_motion_blur", "use_persistent_data", "filepath")}
    d.update({"image." + k: getattr(ims, k) for k in ("media_type", "file_format", "color_mode", "color_depth")})
    if hasattr(scene, "cycles"):
        d.update({"cycles." + k: v for k, v in vars(scene.cycles).items()})
    if hasattr(scene, "eevee"):
        d["eevee.taa_render_samples"] = scene.eevee.taa_render_samples
    return d


class _Bpy(types.ModuleType):
    """``bpy`` for one tool-code call: a render started with INVOKE_DEFAULT stays a running job
    until :meth:`finish_job` (which writes the image where ``filepath`` points *then*)."""

    def __init__(self, scene, tempdir, background=False, raise_on_render=None, render_ret=None):
        super().__init__("bpy")
        self.scene = scene
        self.jobs = set()
        self.seen = []
        self._raise, self._ret = raise_on_render, render_ret
        self.app = types.SimpleNamespace(background=background, tempdir=tempdir,
                                         is_job_running=lambda kind: kind in self.jobs)
        self.context = types.SimpleNamespace(scene=scene, window=object(),
                                             window_manager=types.SimpleNamespace(windows=[object()]))
        self.ops = types.SimpleNamespace(render=types.SimpleNamespace(render=self._render, opengl=self._opengl))

    def _write(self):
        os.makedirs(os.path.dirname(self.scene.render.filepath), exist_ok=True)
        with open(self.scene.render.filepath, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")

    def _render(self, *args, **kwargs):
        self.seen.append(("render", args, kwargs, _dump(self.scene)))
        if self._raise:
            raise RuntimeError(self._raise)
        if self._ret is not None:
            return self._ret
        if "INVOKE_DEFAULT" in args:
            self.jobs.add("RENDER")
            return {"RUNNING_MODAL"}
        self._write()
        return {"FINISHED"}

    def _opengl(self, **kwargs):
        self.seen.append(("opengl", (), kwargs, _dump(self.scene)))
        self._write()
        return {"FINISHED"}

    def finish_job(self, write=True):
        if write:
            self._write()
        self.jobs.discard("RENDER")


class _ToolTest(unittest.TestCase):
    TOOL = ""

    def setUp(self):
        self.tool = _load(self.TOOL + "_toolcode")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev_bpy = sys.modules.get("bpy")
        self.addCleanup(self._restore_bpy)

    def _restore_bpy(self):
        if self._prev_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self._prev_bpy

    def _bpy(self, scene, **kw):
        fake = _Bpy(scene, self._tmp.name, **kw)
        sys.modules["bpy"] = fake
        return fake

    def _out(self, name):
        return os.path.join(self._tmp.name, "bfa_coworker", name)


# -- 1. The profile -------------------------------------------------------------------------

class TestPreviewProfile(unittest.TestCase):

    def test_caps_a_4k_scene(self):
        scene = _scene(image=_ImageSettings("VIDEO", "FFMPEG", "RGB", "8"))
        saved, caps = PROFILE._preview_profile_apply(scene)
        d = _dump(scene)
        self.assertEqual((d["resolution_x"], d["resolution_y"], d["resolution_percentage"]), (1024, 576, 100))
        self.assertEqual(d["cycles.samples"], PROFILE._PREVIEW_MAX_SAMPLES)
        self.assertEqual(d["cycles.time_limit"], PROFILE._PREVIEW_TIME_LIMIT_S)
        self.assertEqual(d["eevee.taa_render_samples"], PROFILE._PREVIEW_MAX_SAMPLES)
        self.assertTrue(d["use_simplify"])
        self.assertEqual(d["simplify_subdivision_render"], PROFILE._PREVIEW_SIMPLIFY_SUBDIV)
        for attr in ("use_compositing", "use_sequencer", "use_motion_blur", "use_persistent_data"):
            self.assertFalse(d[attr], attr)
        self.assertEqual((d["image.media_type"], d["image.file_format"]), ("IMAGE", "PNG"))
        # The user's choices a preview keeps.
        self.assertTrue(d["cycles.use_adaptive_sampling"])
        self.assertTrue(d["cycles.use_denoising"])
        self.assertEqual(d["cycles.device"], "GPU")
        self.assertEqual(caps["resolution"], [1024, 576])
        self.assertEqual(caps["off"], ["compositing", "Sequencer", "motion blur", "persistent data"])
        text = PROFILE._preview_caps_text(caps)
        for part in ("1024x576 px", "at most 32 samples", "30 s", "compositing", "PNG"):
            self.assertIn(part, text)
        self.assertTrue(saved)

    def test_the_percentage_is_folded_in_and_nothing_is_upscaled(self):
        scene = _scene(res=(3840, 2160), pct=25)          # 960x540: already under the cap
        PROFILE._preview_profile_apply(scene)
        self.assertEqual((scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage),
                         (3840, 2160, 25))
        scene = _scene(res=(4000, 3000), pct=50)          # 2000x1500
        _saved, caps = PROFILE._preview_profile_apply(scene)
        self.assertEqual((scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage),
                         (1024, 768, 100))
        self.assertEqual(caps["resolution"], [1024, 768])
        scene = _scene(res=(1080, 1920))                  # portrait
        PROFILE._preview_profile_apply(scene)
        self.assertEqual((scene.render.resolution_x, scene.render.resolution_y), (576, 1024))
        scene = _scene(res=(200, 100))
        PROFILE._preview_profile_apply(scene, max_edge=PROFILE._THUMB_MAX_EDGE)
        self.assertEqual((scene.render.resolution_x, scene.render.resolution_y), (200, 100))

    def test_only_what_changed_is_recorded(self):
        scene = _scene(res=(640, 360))
        scene.cycles.samples, scene.cycles.time_limit, scene.eevee.taa_render_samples = 8, 10.0, 4
        scene.render.use_compositing = False
        saved, _caps = PROFILE._preview_profile_apply(scene)
        changed = {attr for _owner, attr, _prev in saved}
        self.assertNotIn("resolution_x", changed)
        self.assertNotIn("samples", changed)
        self.assertNotIn("time_limit", changed)
        self.assertNotIn("taa_render_samples", changed)
        self.assertNotIn("use_compositing", changed)
        self.assertIn("use_simplify", changed)

    def test_restore_is_exact_in_order_and_once(self):
        scene = _scene(image=_ImageSettings("VIDEO", "FFMPEG", "RGB", "8"))
        before = _dump(scene)
        saved, _caps = PROFILE._preview_profile_apply(scene)
        self.assertNotEqual(_dump(scene), before)
        PROFILE._preview_profile_restore(saved)
        self.assertEqual(_dump(scene), before)          # FFMPEG back: the media type went first
        self.assertEqual(saved, [])
        scene.cycles.samples = 64                       # the user changes something afterwards
        PROFILE._preview_profile_restore(saved)         # a second restore (the job's end) changes nothing
        self.assertEqual(scene.cycles.samples, 64)

    def test_a_format_change_keeps_the_colour_depth(self):
        scene = _scene(image=_ImageSettings("IMAGE", "OPEN_EXR", "RGBA", "32"))
        before = _dump(scene)
        saved, _caps = PROFILE._preview_profile_apply(scene)
        self.assertEqual((scene.render.image_settings.file_format, scene.render.image_settings.color_depth),
                         ("PNG", "16"))
        PROFILE._preview_profile_restore(saved)
        self.assertEqual(_dump(scene), before)

    def test_a_value_that_cant_go_back_doesnt_stop_the_others(self):
        scene = _scene()
        before = _dump(scene)
        saved, _caps = PROFILE._preview_profile_apply(scene)

        class _Gone:
            def __setattr__(self, name, value):
                raise ReferenceError("StructRNA has been removed")
        saved.insert(0, (_Gone(), "x", 1))
        PROFILE._preview_profile_restore(saved)
        self.assertEqual(_dump(scene), before)

    def test_without_cycles_or_eevee(self):
        scene = _scene(cycles=False, eevee=False)
        saved, caps = PROFILE._preview_profile_apply(scene)
        self.assertEqual(caps["resolution"], [1024, 576])
        PROFILE._preview_profile_restore(saved)
        self.assertEqual(scene.render.resolution_x, 3840)

    def test_busy_and_no_camera(self):
        self.assertEqual(PROFILE._preview_busy_message(lambda kind: False), "")
        self.assertIn("render is already running", PROFILE._preview_busy_message(lambda kind: kind == "RENDER"))
        self.assertIn("bake is running", PROFILE._preview_busy_message(lambda kind: kind == "OBJECT_BAKE"))

        def bad(kind):
            raise TypeError("unknown job type")
        self.assertEqual(PROFILE._preview_busy_message(bad), "")
        self.assertFalse(PROFILE._preview_no_camera(_scene()))
        scene = _scene()
        scene.camera = None                 # a camera object is there: Blender's render takes it
        self.assertFalse(PROFILE._preview_no_camera(scene))
        self.assertTrue(PROFILE._preview_no_camera(_scene(camera=False)))


# -- 2. render_viewport_to_path ---------------------------------------------------------------

class TestViewportTool(_ToolTest):
    TOOL = "render_viewport_to_path"

    def test_preview_is_the_default_and_stays_on_until_the_job_is_over(self):
        scene = _scene()
        before = _dump(scene)
        fake = self._bpy(scene)
        self.assertEqual(self.tool.Params(output_path="look.png").quality, "preview")
        check = self.tool.main(self.tool.Params(output_path="look.png"))
        self.assertTrue(callable(check))
        _kind, args, _kw, at_render = fake.seen[0]
        self.assertEqual(args, ("INVOKE_DEFAULT",))
        self.assertEqual((at_render["resolution_x"], at_render["resolution_y"]), (1024, 576))
        self.assertEqual(at_render["cycles.samples"], 32)
        self.assertFalse(at_render["use_compositing"])
        # The job reads the settings on its own thread after the operator returned: still capped.
        self.assertIsNone(check())
        self.assertEqual(scene.render.resolution_x, 1024)
        self.assertEqual(scene.cycles.samples, 32)
        fake.finish_job()
        result = check()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["filepath"], self._out("look.png"))
        self.assertTrue(os.path.isfile(self._out("look.png")), "write_still wrote where filepath pointed then")
        self.assertEqual(result["quality"], "preview")
        self.assertEqual(result["caps"]["resolution"], [1024, 576])
        self.assertIn("Quality: preview", result["message"])
        self.assertEqual(_dump(scene), before)
        check.job_on_finish()                       # the job's end restores too: nothing changes
        self.assertEqual(_dump(scene), before)

    def test_cancelled_or_failed_job_restores_and_says_what_it_ran_with(self):
        scene = _scene()
        before = _dump(scene)
        fake = self._bpy(scene)
        check = self.tool.main(self.tool.Params(output_path="look.png"))
        fake.finish_job(write=False)                # Esc / the job bar's X, or an engine error
        result = check()
        self.assertEqual(result["status"], "error")
        self.assertIn("cancelled or failed", result["message"])
        self.assertIn("Quality: preview", result["message"])
        self.assertIn("1024x576 px", result["message"])
        self.assertIn("trying again won't help", result["message"])
        self.assertEqual(_dump(scene), before)

    def test_a_handed_off_job_restores_on_its_end(self):
        scene = _scene()
        before = _dump(scene)
        fake = self._bpy(scene)
        check = self.tool.main(self.tool.Params(output_path="look.png"))
        self.assertEqual(check.job_kind, "RENDER")
        fake.finish_job(write=False)
        check.job_on_finish()                       # long_jobs: a cancel ends the job before its checker
        self.assertEqual(_dump(scene), before)

    def test_final_uses_the_scenes_own_settings(self):
        scene = _scene()
        before = _dump(scene)
        fake = self._bpy(scene)
        check = self.tool.main(self.tool.Params(output_path="final.png", quality="final"))
        at_render = fake.seen[0][3]
        self.assertEqual((at_render["resolution_x"], at_render["cycles.samples"]), (3840, 4096))
        self.assertTrue(at_render["use_compositing"])
        self.assertEqual(at_render["filepath"], self._out("final.png"))
        fake.finish_job()
        result = check()
        self.assertEqual(result["quality"], "final")
        self.assertIsNone(result["caps"])
        self.assertIn("Quality: final", result["message"])
        self.assertEqual(_dump(scene), before)

    def test_viewport_is_an_opengl_render_under_the_size_cap(self):
        scene = _scene()
        before = _dump(scene)
        fake = self._bpy(scene)
        result = self.tool.main(self.tool.Params(output_path="gl.png", quality="viewport"))._asdict()
        kind, _args, kwargs, at_render = fake.seen[0]
        self.assertEqual(kind, "opengl")
        self.assertEqual(kwargs, {"write_still": True, "view_context": False})
        self.assertEqual((at_render["resolution_x"], at_render["resolution_y"]), (1024, 576))
        self.assertEqual(result["status"], "ok")
        self.assertIn("Quality: viewport", result["message"])
        self.assertEqual(_dump(scene), before)

    def test_render_error_restores_and_carries_the_quality(self):
        scene = _scene()
        before = _dump(scene)
        self._bpy(scene, raise_on_render="Error: out of GPU memory")
        result = self.tool.main(self.tool.Params(output_path="x.png"))._asdict()
        self.assertEqual(result["status"], "error")
        self.assertIn("out of GPU memory", result["message"])
        self.assertIn("Quality: preview", result["message"])
        self.assertEqual(result["quality"], "preview")
        self.assertEqual(_dump(scene), before)
        self._bpy(scene, raise_on_render="Error: out of GPU memory")
        result = self.tool.main(self.tool.Params(output_path="x.png", quality="final"))._asdict()
        self.assertIn("Quality: final", result["message"])
        self.assertIn("Don't start the same final render again", result["message"])
        self.assertEqual(_dump(scene), before)

    def test_not_started_restores(self):
        scene = _scene(image=_ImageSettings("VIDEO", "FFMPEG", "RGB", "8"))
        before = _dump(scene)
        self._bpy(scene, render_ret={"CANCELLED"})
        result = self.tool.main(self.tool.Params(output_path="x.png", quality="final"))._asdict()
        self.assertEqual(result["status"], "error")
        self.assertIn("didn't start", result["message"])
        self.assertEqual(_dump(scene), before)

    def test_blocking_render_in_background_mode_restores_at_once(self):
        scene = _scene()
        before = _dump(scene)
        fake = self._bpy(scene, background=True)
        result = self.tool.main(self.tool.Params(output_path="bg.png"))._asdict()
        self.assertEqual(fake.seen[0][1], ())
        self.assertEqual(fake.seen[0][3]["resolution_x"], 1024)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(_dump(scene), before)

    def test_background_job_gets_a_job_id_and_restores_on_its_end(self):
        scene = _scene()
        before = _dump(scene)
        fake = self._bpy(scene)
        started = {}
        api = types.ModuleType("x524.bfa_coworker.coworker_api")

        def start_job(kind, label, check, on_finish):
            started.update(kind=kind, label=label, check=check, on_finish=on_finish)
            return "job-1"
        api.start_job = start_job
        sys.modules[api.__name__] = api
        self.addCleanup(sys.modules.pop, api.__name__, None)
        result = self.tool.main(self.tool.Params(output_path="bg.png", background=True))._asdict()
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["quality"], "preview")
        self.assertEqual(scene.cycles.samples, 32)          # still rendering
        fake.finish_job()
        self.assertEqual(started["check"]()["status"], "ok")
        started["on_finish"]()
        self.assertEqual(_dump(scene), before)

    def test_guards(self):
        scene = _scene()
        before = _dump(scene)
        fake = self._bpy(scene)
        fake.jobs.add("OBJECT_BAKE")
        result = self.tool.main(self.tool.Params(output_path="x.png"))._asdict()
        self.assertEqual(result["status"], "error")
        self.assertIn("bake is running", result["message"])
        self.assertEqual(fake.seen, [])
        fake = self._bpy(_scene(camera=False))
        result = self.tool.main(self.tool.Params(output_path="x.png"))._asdict()
        self.assertIn("no camera", result["message"])
        self.assertEqual(fake.seen, [])
        result = self.tool.main(self.tool.Params(output_path="x.png", quality="ultra"))._asdict()
        self.assertIn("'preview'", result["message"])
        self.assertEqual(_dump(scene), before)


# -- 3. render_thumbnail_to_path ----------------------------------------------------------------

class TestThumbnailTool(_ToolTest):
    TOOL = "render_thumbnail_to_path"

    def test_caps_hold_while_the_job_runs(self):
        for engine in ("CYCLES", "BLENDER_EEVEE"):
            scene = _scene(engine=engine)
            before = _dump(scene)
            fake = self._bpy(scene)
            check = self.tool.main(self.tool.Params(output_path="t.png"))
            at_render = fake.seen[0][3]
            self.assertEqual((at_render["resolution_x"], at_render["resolution_y"]), (320, 180))
            self.assertEqual(at_render["cycles.samples"], 16)
            self.assertEqual(at_render["eevee.taa_render_samples"], 16, engine)
            self.assertIsNone(check())
            self.assertEqual(scene.render.resolution_x, 320, "still capped while the job renders")
            self.assertEqual(scene.eevee.taa_render_samples, 16)
            fake.finish_job()
            result = check()
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["caps"]["resolution"], [320, 180])
            self.assertEqual(_dump(scene), before)

    def test_error_and_guards(self):
        scene = _scene()
        before = _dump(scene)
        self._bpy(scene, raise_on_render="Error: no luck")
        result = self.tool.main(self.tool.Params(output_path="t.png"))._asdict()
        self.assertIn("no luck", result["message"])
        self.assertIn("320x180 px", result["message"])
        self.assertEqual(_dump(scene), before)
        fake = self._bpy(scene)
        fake.jobs.add("RENDER")
        self.assertIn("already running", self.tool.main(self.tool.Params(output_path="t.png")).message)
        self.assertEqual(fake.seen, [])
        self.assertEqual(_dump(scene), before)

    def test_no_eevee_next_engine_test_left(self):
        for name in ("render_thumbnail_to_path_toolcode.py", "render_viewport_to_path_toolcode.py",
                     "_template_render_preview_profile.py"):
            self.assertNotIn("EEVEE_NEXT", (_TOOLS / name).read_text(encoding="utf-8"), name)


# -- 4. The tool argument ------------------------------------------------------------------------

class TestToolArgument(unittest.TestCase):

    def test_quality_argument_and_description(self):
        tree = ast.parse((_TOOLS / "render_viewport_to_path.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "render_viewport_to_path")
        names = [a.arg for a in fn.args.args]
        self.assertEqual(names, ["output_path", "background", "quality"])
        self.assertEqual(ast.literal_eval(fn.args.defaults[-1]), "preview")
        doc = ast.get_docstring(fn)
        self.assertIn('quality="final"', doc)
        self.assertIn("only when the user asked for the real render", doc)
        self.assertIn('quality="viewport"', doc)

    def test_prompts_name_the_argument(self):
        for path in (_REPO / "mcp" / "blmcp" / "data" / "prompts.yml",
                     _REPO / "mcp" / "blmcp" / "data" / "prompts_compact.yml",
                     _REPO / "mcp" / "blmcp" / "data" / "skills" / "rendering.md",
                     _ADDON / "skills" / "mcp_tools.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn('quality="final"', text, path.name)


# -- 5. The chat capture and the bridge ------------------------------------------------------------

def _load_chat_attachments():
    spec = importlib.util.spec_from_file_location("chat_attachments_524", _ADDON / "chat_attachments.py")
    ca = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ca)
    return ca


class TestChatCapture(unittest.TestCase):

    def setUp(self):
        self.ca = _load_chat_attachments()
        self._prev_bpy = sys.modules.get("bpy")
        self.addCleanup(lambda: sys.modules.__setitem__("bpy", self._prev_bpy) if self._prev_bpy
                        else sys.modules.pop("bpy", None))

    def test_same_profile_as_the_tools(self):
        profile = self.ca._preview_profile()
        self.assertEqual(profile["_PREVIEW_MAX_EDGE"], PROFILE._PREVIEW_MAX_EDGE)
        scene = _scene()
        before = _dump(scene)
        saved = self.ca._clamp_render_settings(scene)
        self.assertEqual((scene.render.resolution_x, scene.cycles.samples), (1024, 32))
        self.assertFalse(scene.render.use_compositing)
        self.ca._restore_render_settings(saved)
        self.assertEqual(_dump(scene), before)
        self.assertFalse(hasattr(self.ca, "_RENDER_MAX_EDGE"), "one definition of the caps")

    def test_refuses_while_a_render_or_bake_runs(self):
        running = set()
        touched = []
        fake = types.ModuleType("bpy")
        fake.app = types.SimpleNamespace(is_job_running=lambda kind: kind in running)

        class _Ctx:
            @property
            def window(self):
                touched.append("window")
                return None
        fake.context = _Ctx()
        sys.modules["bpy"] = fake
        self.assertEqual(self.ca.capture_refusal(), "")
        running.add("RENDER")
        self.assertIn("render or bake is running", self.ca.capture_refusal())
        self.assertIsNone(self.ca.capture_render_from_view())
        self.assertEqual(touched, [], "nothing is looked at, nothing rendered")
        running.discard("RENDER")
        running.add("OBJECT_BAKE")
        self.assertTrue(self.ca.capture_refusal())

    def test_a_missing_profile_refuses(self):
        self.ca._PREVIEW_PROFILE.clear()
        self.ca._preview_profile_dirs = lambda: [os.path.join(tempfile.gettempdir(), "no-such-dir-524")]
        self.assertIn("missing", self.ca.capture_refusal())
        self.assertEqual(self.ca._clamp_render_settings(_scene()), [])

    def test_the_operator_reports_the_refusal(self):
        src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        body = src[src.index("class BFACW_OT_chat_capture_render("):src.index("# -- Capture part of the screen")]
        self.assertLess(body.index("capture_refusal()"), body.index("capture_render_from_view()"))


def _pkg() -> str:
    name = "bfacw_jobs_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return name


class TestHungUpCaller(unittest.TestCase):
    """deferred_tool: a render whose caller hung up still restores when its job ends."""

    def setUp(self):
        self.deferred = importlib.import_module(_pkg() + ".deferred_tool")
        self.jobs = importlib.import_module(_pkg() + ".long_jobs")
        self.jobs.clear()
        self.deferred._deferred_clients.clear()
        self.addCleanup(self.jobs.clear)

    def test_the_job_goes_on_and_restores(self):
        server_end, client_end = socket.socketpair()
        server_end.setblocking(False)
        result, restored = [None], []

        def check_is_finished():
            return result[0]
        check_is_finished.job_kind = "RENDER"
        check_is_finished.job_label = "Render look.png"
        check_is_finished.job_on_finish = lambda: restored.append(1)
        self.deferred.add(server_end, check_is_finished, True, "", "")
        client_end.close()                                   # the caller hung up mid-render
        self.deferred.poll()
        self.assertFalse(self.deferred.has_pending())
        running = self.jobs.running()
        self.assertEqual([j["label"] for j in running], ["Render look.png"])
        self.assertEqual(restored, [])                      # not while it still renders
        result[0] = {"status": "error", "message": "cancelled"}
        self.jobs.poll()
        self.assertEqual(restored, [1])

    def test_other_replies_are_just_dropped(self):
        server_end, client_end = socket.socketpair()
        server_end.setblocking(False)
        self.deferred.add(server_end, lambda: None, True, "", "")       # e.g. ask_user
        client_end.close()
        self.deferred.poll()
        self.assertEqual(self.jobs.running(), [])


if __name__ == "__main__":
    unittest.main()
