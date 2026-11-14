# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Make Vertical / Reframe (Video 3, #333).

1. The maths (``reframe_math``): cover / fit scales for 16:9, 4:3, square and
   already-vertical sources on a 9:16 canvas, the pan range and clamping, the
   subject <-> offset conversion, Blur Fill's background, sampling frames.
2. Smoothing a sampled subject track into keyframes (speed limit, hold, outliers).
3. The "keep user-edited values" rule (``plan_modifier``).
4. Follow subject's request and answer parsing, the engine check, and a stub run.
5. The ``reframe_vertical`` tool: its params / result and that it is registered
   in the ``vse`` domain of both lists.

No Blender needed.  Run with::

    python -m unittest tests.test_reframe -v
"""

__all__ = ()

import importlib
import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_TOOLS = _REPO / "mcp" / "blmcp" / "tools"


def _pkg() -> str:
    name = "bfacw_reframe_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return name


rm = importlib.import_module(_pkg() + ".reframe_math")


class TestScales(unittest.TestCase):

    def test_16x9_on_9x16(self):
        # 1920x1080 on 1080x1920: covering needs the height (1920 / 1080), fitting the width (1080 / 1920).
        self.assertAlmostEqual(rm.cover_scale(1920, 1080, 1080, 1920), 1920 / 1080)
        self.assertAlmostEqual(rm.fit_scale(1920, 1080, 1080, 1920), 1080 / 1920)
        self.assertEqual(rm.frame_ratio(1920, 1080, 1080, 1920), (1080 / 1920, 1920 / 1080))

    def test_4x3_square_and_vertical_sources(self):
        self.assertAlmostEqual(rm.cover_scale(1440, 1080, 1080, 1920), 1920 / 1080)
        self.assertAlmostEqual(rm.fit_scale(1440, 1080, 1080, 1920), 1080 / 1440)
        self.assertAlmostEqual(rm.cover_scale(1000, 1000, 1080, 1920), 1.92)
        self.assertAlmostEqual(rm.fit_scale(1000, 1000, 1080, 1920), 1.08)
        # Already vertical, same size: nothing to do either way.
        self.assertAlmostEqual(rm.cover_scale(1080, 1920, 1080, 1920), 1.0)
        self.assertAlmostEqual(rm.fit_scale(1080, 1920, 1080, 1920), 1.0)
        # A taller-than-9:16 picture (9:21) covers by its width.
        self.assertAlmostEqual(rm.cover_scale(900, 2100, 1080, 1920), 1.2)

    def test_cover_covers_and_fit_fits_without_stretch(self):
        for src in ((1920, 1080), (1440, 1080), (1000, 1000), (1080, 1920), (3840, 1600), (720, 1280)):
            for canvas in ((1080, 1920), (720, 1280), (1920, 1080)):
                c = rm.cover_scale(*src, *canvas)
                f = rm.fit_scale(*src, *canvas)
                # One factor for both axes (no stretch); cover >= canvas on both axes, fit <= on both.
                self.assertGreaterEqual(src[0] * c + 1e-6, canvas[0])
                self.assertGreaterEqual(src[1] * c + 1e-6, canvas[1])
                self.assertLessEqual(src[0] * f - 1e-6, canvas[0])
                self.assertLessEqual(src[1] * f - 1e-6, canvas[1])
                self.assertTrue(abs(src[0] * c - canvas[0]) < 1e-6 or abs(src[1] * c - canvas[1]) < 1e-6)

    def test_background_and_mode_scale(self):
        self.assertAlmostEqual(rm.blur_background_scale(1920, 1080, 1080, 1920),
                               1920 / 1080 * rm.BACKGROUND_OVERSCAN)
        self.assertAlmostEqual(rm.mode_scale("BLUR_FILL", 1920, 1080, 1080, 1920), 1080 / 1920)
        self.assertAlmostEqual(rm.mode_scale("CROP", 1920, 1080, 1080, 1920, zoom=2.0),
                               2 * 1920 / 1080 * rm.BLEED)

    def test_bad_sizes(self):
        with self.assertRaises(ValueError):
            rm.cover_scale(0, 1080, 1080, 1920)
        with self.assertRaises(ValueError):
            rm.framing_for("SIDEWAYS", 1920, 1080, 1080, 1920)


class TestPanAndClamp(unittest.TestCase):

    def test_pan_range_16x9_crop(self):
        s = rm.cover_scale(1920, 1080, 1080, 1920)
        rx, ry = rm.pan_range(1920, 1080, 1080, 1920, s)
        self.assertAlmostEqual(rx, (1920 * s - 1080) / 2)     # ~1166 px each way
        self.assertAlmostEqual(ry, 0.0)                         # height just covers: no vertical pan
        # Zooming in opens a vertical range too.
        _rx, ry2 = rm.pan_range(1920, 1080, 1080, 1920, 2 * s)
        self.assertAlmostEqual(ry2, (1080 * 2 * s - 1920) / 2)

    def test_offset_stays_inside_the_picture(self):
        s = rm.cover_scale(1920, 1080, 1080, 1920)
        rx, _ry = rm.pan_range(1920, 1080, 1080, 1920, s)
        for o in (-5.0, -1.0, -0.3, 0.0, 0.7, 1.0, 3.0):
            shift = rm.offset_px(o, rx)
            # The canvas [-540, 540] stays inside the shifted picture [shift - P/2, shift + P/2].
            half_picture = 1920 * s / 2
            self.assertLessEqual(shift - half_picture, -540 + 1e-6)
            self.assertGreaterEqual(shift + half_picture, 540 - 1e-6)
        self.assertEqual(rm.offset_px(1.0, 100.0), -100.0)    # frame pans right = picture moves left
        self.assertEqual(rm.clamp(4.0), 1.0)
        self.assertEqual(rm.clamp(-4.0), -1.0)
        self.assertEqual(rm.clamp(0.2, 0.0, 1.0), 0.2)

    def test_subject_offset_round_trip(self):
        s = rm.cover_scale(1920, 1080, 1080, 1920)
        self.assertEqual(rm.subject_to_offset(0.5, 1920, 1080, s), 0.0)
        for x in (0.3, 0.45, 0.6, 0.7):
            o = rm.subject_to_offset(x, 1920, 1080, s)
            self.assertAlmostEqual(rm.offset_to_subject(o, 1920, 1080, s), x, places=6)
        # Far edges clamp: the picture keeps covering the canvas.
        self.assertEqual(rm.subject_to_offset(0.0, 1920, 1080, s), -1.0)
        self.assertEqual(rm.subject_to_offset(1.0, 1920, 1080, s), 1.0)
        self.assertGreater(rm.subject_to_offset(0.7, 1920, 1080, s), 0)   # subject right -> pan right
        # No range (already vertical): always centred.
        self.assertEqual(rm.subject_to_offset(0.9, 1080, 1080, 1.0), 0.0)


class TestKeepUserValues(unittest.TestCase):

    def test_added_kept_reset(self):
        action, values = rm.plan_modifier(None, "CROP", 1920, 1080, 1080, 1920)
        self.assertEqual(action, "added")
        self.assertEqual(values["Offset X"], 0.0)
        self.assertFalse(values["Blur Fill"])
        self.assertAlmostEqual(values["Frame Width"], 1080 / 1920)
        edited = dict(values, **{"Offset X": 0.4, "Zoom": 1.3, "Blur Fill": True})
        action, values = rm.plan_modifier(edited, "CROP", 1920, 1080, 1080, 1920)
        self.assertEqual(action, "kept")
        # Only the size inputs are written: the user's offset, zoom and mode stay.
        self.assertEqual(set(values), set(rm.SIZE_INPUTS))
        action, values = rm.plan_modifier(edited, "BLUR_FILL", 1920, 1080, 720, 1280, reset=True)
        self.assertEqual(action, "reset")
        self.assertEqual((values["Offset X"], values["Zoom"], values["Blur Fill"]), (0.0, 1.0, True))
        self.assertAlmostEqual(values["Frame Height"], 1280 / 1080)

    def test_every_user_input_has_a_default(self):
        self.assertEqual(set(rm.USER_INPUTS), set(rm.DEFAULTS))


class TestSmoothing(unittest.TestCase):

    def test_still_subject_holds(self):
        keys = rm.smooth_track([(1, 0.5), (25, 0.51), (49, 0.49), (73, 0.52)], fps=24)
        self.assertEqual([k[1] for k in keys], [0.5, 0.5])          # two keys hold it still
        self.assertEqual((keys[0][0], keys[-1][0]), (1, 73))

    def test_speed_limit(self):
        keys = rm.smooth_track([(1, 0.2), (13, 0.8), (25, 0.8), (37, 0.8)], fps=24, max_speed=0.25)
        for (f0, x0), (f1, x1) in zip(keys, keys[1:]):
            self.assertLessEqual(abs(x1 - x0), 0.25 * (f1 - f0) / 24 + 1e-9)
        self.assertGreater(keys[-1][1], 0.2)

    def test_walking_subject_is_followed(self):
        samples = [(1 + 24 * i, 0.3 + 0.05 * i) for i in range(8)]   # walks right, 0.05 / s
        keys = rm.smooth_track(samples, fps=24)
        self.assertAlmostEqual(keys[-1][1], samples[-1][1], places=6)
        self.assertTrue(all(b[1] >= a[1] for a, b in zip(keys, keys[1:])))

    def test_outlier_and_missing(self):
        keys = rm.smooth_track([(1, 0.5), (10, 0.5), (20, 0.95), (30, 0.5), (40, None), (50, 0.5)], fps=24)
        self.assertTrue(all(abs(x - 0.5) < 1e-9 for _f, x in keys))
        self.assertEqual(rm.smooth_track([(1, None), (2, None)], fps=24), [])

    def test_offsets_from_track(self):
        s = rm.cover_scale(1920, 1080, 1080, 1920)
        offs = rm.track_to_offsets([(1, 0.5), (49, 0.7)], 1920, 1080, s)
        self.assertEqual(offs[0], (1, 0.0))
        self.assertGreater(offs[1][1], 0.0)

    def test_sample_frames(self):
        self.assertEqual(rm.sample_frames(1, 101, 4), [13, 38, 63, 88])
        self.assertEqual(rm.sample_frames(10, 12, 5), [10, 11])
        self.assertEqual(rm.sample_frames(5, 5, 3), [])


rf = importlib.import_module(_pkg() + ".reframe_follow")


class TestFollowSubject(unittest.TestCase):

    def test_parse_x(self):
        self.assertEqual(rf.parse_x('{"x": 0.42}'), 0.42)
        self.assertEqual(rf.parse_x('Sure! ```json\n{"x": 0.7}\n```'), 0.7)
        self.assertIsNone(rf.parse_x('{"x": null}'))
        self.assertIsNone(rf.parse_x("There is no clear subject (none)."))
        self.assertEqual(rf.parse_x("0.25"), 0.25)
        self.assertEqual(rf.parse_x("about 60%"), 0.6)
        self.assertEqual(rf.parse_x("42"), 0.42)
        self.assertIsNone(rf.parse_x("-3"))
        self.assertIsNone(rf.parse_x(""))
        self.assertIsNone(rf.parse_x(None))

    def test_message_is_an_image_block_and_the_question(self):
        msgs = rf.build_messages("data:image/png;base64,AAAA")
        self.assertEqual(len(msgs), 1)
        content = msgs[0]["content"]
        self.assertEqual(content[0], {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}})
        self.assertIn('"x"', content[1]["text"])

    def test_engine_check(self):
        cfg = types.SimpleNamespace(mode="local", local_port=8081, remote_api_url="", remote_api_key="",
                                    remote_model="")
        with self.assertRaisesRegex(rf.Unavailable, "External Agent"):
            rf.vision_target("EXTERNAL_AGENT", cfg, True)
        with self.assertRaisesRegex(rf.Unavailable, "start Coworker"):
            rf.vision_target("LOCAL_LLM", cfg, False)
        with self.assertRaisesRegex(rf.Unavailable, "can't see images"):
            rf.vision_target("LOCAL_LLM", cfg, True, fetch=lambda url: {"modalities": {"vision": False}})
        url, key, model = rf.vision_target("LOCAL_LLM", cfg, True, fetch=lambda url: {"modalities": {"vision": True}})
        self.assertEqual(url, "http://127.0.0.1:8081/v1/chat/completions")
        remote = types.SimpleNamespace(mode="remote", local_port=0, remote_api_url="https://api.example/v1",
                                       remote_api_key="k", remote_model="m")
        self.assertEqual(rf.vision_target("REMOTE_API", remote, False),
                         ("https://api.example/v1/chat/completions", "k", "m"))
        with self.assertRaisesRegex(rf.Unavailable, "Remote API URL"):
            rf.vision_target("REMOTE_API", cfg, False)

    def test_asker_sends_the_frame_and_parses(self):
        import os
        import tempfile
        sent = {}

        def transport(url, messages, tools, key, model, **kw):
            sent.update(url=url, messages=messages, tools=tools, kw=kw)
            return {"choices": [{"message": {"content": '{"x": 0.66}'}}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.png")
            with open(path, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\nfake")
            ask = rf.make_asker(("http://h/v1/chat/completions", "", ""), transport)
            self.assertEqual(ask(path, "shot", 10), 0.66)
        url = sent["messages"][0]["content"][0]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertIsNone(sent["tools"])
        failing = rf.make_asker(("http://h", "", ""), lambda *a, **k: None)
        with self.assertRaises(rf.Unavailable):
            failing(path, "shot", 1)

    def test_stub_run_to_offsets(self):
        """A scripted subject walking right: sampled, smoothed, Offset X keys rise and stay in range."""
        script = {("shot1", 13): 0.35, ("shot1", 38): 0.45, ("shot1", 63): 0.55, ("shot1", 88): 0.65,
                  ("shot2", 113): None, ("shot2", 138): 0.5}
        jobs = [(s, f, "/tmp/{}_{}.png".format(s, f)) for (s, f) in script]
        results = rf.run(jobs, lambda path, strip, frame: script[(strip, frame)])
        self.assertEqual(results["shot1"], [(13, 0.35), (38, 0.45), (63, 0.55), (88, 0.65)])
        self.assertEqual(results["shot2"], [(113, None), (138, 0.5)])
        s = rm.cover_scale(1920, 1080, 1080, 1920) * rm.BLEED
        offs = rm.track_to_offsets(rm.smooth_track(results["shot1"], 24), 1920, 1080, s)
        self.assertEqual([f for f, _o in offs], [13, 38, 63, 88])
        self.assertTrue(all(-1 <= o <= 1 for _f, o in offs))
        self.assertTrue(all(b[1] > a[1] for a, b in zip(offs, offs[1:])))
        self.assertLess(offs[0][1], 0)
        self.assertGreater(offs[-1][1], 0)


class TestReframeVerticalTool(unittest.TestCase):
    """The tool-code, run against a stand-in for the add-on's coworker_api."""

    def _load(self, api):
        namespace = {"__name__": "toolcode"}
        source = (_TOOLS / "reframe_vertical_toolcode.py").read_text(encoding="utf-8")
        exec(compile(source, "reframe_vertical_toolcode.py", "exec"), namespace)  # pylint: disable=exec-used
        namespace["_coworker_api"] = lambda: api
        return namespace

    def test_ok_result(self):
        calls = []

        def reframe_vertical(strips, mode, in_copy, resolution, reset):
            calls.append((strips, mode, in_copy, resolution, reset))
            return {"scene": "Edit Vertical", "source_scene": "Edit", "copied": True, "mode": mode,
                    "resolution": [1080, 1920], "added": ["a", "b"], "kept": ["c"], "reset": []}
        ns = self._load(types.SimpleNamespace(reframe_vertical=reframe_vertical))
        out = ns["main"](ns["Params"](strips=[], mode="blur fill", in_copy=True, resolution="1080x1920",
                                      reset=False))._asdict()
        self.assertEqual(calls, [([], "BLUR_FILL", True, "1080x1920", False)])
        self.assertEqual(out["status"], "ok")
        self.assertEqual((out["scene"], out["copied"], out["mode"]), ("Edit Vertical", True, "BLUR_FILL"))
        self.assertEqual((out["strips_changed"], out["strips_kept"]), (["a", "b"], ["c"]))
        self.assertIn("Reframe", out["message"])
        json.dumps(out)

    def test_errors(self):
        def boom(*_a):
            raise RuntimeError("No visible movie, image or scene strips in 'Scene'.")
        ns = self._load(types.SimpleNamespace(reframe_vertical=boom))
        out = ns["main"](ns["Params"](strips="x", mode="CROP", in_copy=False, resolution="1080x1920",
                                      reset=False))._asdict()
        self.assertEqual(out["status"], "error")
        self.assertIn("No visible", out["message"])
        out = ns["main"](ns["Params"](strips=[], mode="SIDEWAYS", in_copy=True, resolution="", reset=False))._asdict()
        self.assertEqual(out["status"], "error")
        self.assertIn("CROP", out["message"])

    def test_params_match_the_tool(self):
        tool = (_TOOLS / "reframe_vertical.py").read_text(encoding="utf-8")
        sig = re.search(r"def reframe_vertical\((.*?)\) ->", tool, re.S).group(1)
        params = re.findall(r"^\s*(\w+):", sig, re.M)
        ns = self._load(types.SimpleNamespace())
        self.assertEqual(list(ns["Params"]._fields), params)

    def test_in_the_vse_domain_of_both_lists(self):
        spec = importlib.util.spec_from_file_location("tool_domains_reframe", _REPO / "mcp" / "blmcp" / "tool_domains.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn("reframe_vertical", module.DOMAINS["vse"])
        text = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
        block = re.search(r'"vse": frozenset\(\{(.*?)\}\)', text, re.S).group(1)
        self.assertIn('"reframe_vertical"', block)
