# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Blender side of Follow Subject (#592) with stand-ins for bpy and the strips: which strips are targets,
how an analysis is routed (one crop, Track All, the other splits left alone), the run's sample cap, a model
that stops answering, and the timer's messages.  No Blender, no model."""

import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfa_coworker"


def _load():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    fake_reframe = types.ModuleType(_PKG + ".reframe")
    fake_reframe.ReframeError = type("ReframeError", (Exception,), {})
    fake_reframe._follow_targets = lambda scene, strips: (list(strips), [])
    fake_reframe._tell = lambda msg: None
    fake_reframe._engine_notice = lambda follow: ""
    fake_follow = types.ModuleType(_PKG + ".reframe_follow")
    fake_follow.Unavailable = type("Unavailable", (Exception,), {})
    fake_follow.resolve_box_asker = lambda: None
    fakes = {"bpy": mock.MagicMock(), _PKG + ".reframe": fake_reframe, _PKG + ".reframe_follow": fake_follow}
    saved = {k: sys.modules.get(k) for k in list(fakes) + [_PKG + ".reframe_subjects"]}
    sys.modules.pop(_PKG + ".reframe_subjects", None)
    sys.modules.update(fakes)
    try:
        return importlib.import_module(_PKG + ".reframe_subjects"), fake_reframe, fake_follow
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        sys.modules.pop(_PKG + ".reframe_subjects", None)


rs, _REFRAME, _FOLLOW = _load()
sf = rs.sf
ra = rs.ra


class Strip:
    def __init__(self, name, layout=None, part=0, partner=""):
        self.name = name
        self._tag = None if layout is None else {"layout": layout, "part": part, "group": "g", "partner": partner}

    def get(self, key, default=None):
        return self._tag if key == ra.TAG and self._tag is not None else default


class Strips(list):
    def get(self, name):
        return next((s for s in self if s.name == name), None)


def scene_of(*strips):
    return SimpleNamespace(sequence_editor=SimpleNamespace(strips_all=Strips(strips)), name="V")


def analysis(primary=True, secondary=False, layout="single"):
    track = SimpleNamespace(kind="face")
    return SimpleNamespace(primary=[track] if primary else [], secondary=[track] if secondary else [], layout=layout)


class TestTargets(unittest.TestCase):
    def test_a_bias_split_is_skipped_with_the_reason(self):
        s = Strip("A", "split_top", 0, "B")
        ok, why = rs._targets(scene_of(s), [s])
        self.assertEqual(ok, [])
        self.assertEqual(len(why), 1)
        self.assertIn("Track All", why[0])

    def test_half_and_bottom_splits_too(self):
        for lid in ("split_half", "split_bottom"):
            s = Strip("A", lid, 0, "B")
            self.assertEqual(rs._targets(scene_of(s), [s])[0], [])

    def test_a_track_all_part_is_a_target(self):
        s = Strip("A", "track_all", 0, "B")
        self.assertEqual(rs._targets(scene_of(s), [s])[0], [s])

    def test_a_plain_strip_goes_to_the_vertical_check(self):
        s = Strip("A")
        self.assertEqual(rs._targets(scene_of(s), [s])[0], [s])


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.fake_layouts = types.ModuleType(_PKG + ".reframe_layouts")
        patcher = mock.patch.dict(sys.modules, {_PKG + ".reframe_layouts": self.fake_layouts})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_apply(self, strip, result, layout="auto"):
        with mock.patch.object(rs, "apply_split", return_value="split keyed") as split, \
                mock.patch.object(rs, "_single", return_value={"A": 5}) as single:
            lines = rs.apply_results(scene_of(strip), {"A": result}, layout)
        return lines, split, single

    def test_a_top_bias_split_is_left_alone(self):
        lines, split, single = self.run_apply(Strip("A", "split_top", 0, "B"), analysis(secondary=True, layout="split"))
        split.assert_not_called()
        single.assert_not_called()
        self.assertIn("fixed crops", lines[0])

    def test_a_track_all_part_is_keyed_as_a_split(self):
        lines, split, single = self.run_apply(Strip("A", "track_all", 0, "B"), analysis())
        split.assert_called_once()
        single.assert_not_called()
        self.assertEqual(lines, ["split keyed"])

    def test_one_subject_is_one_crop(self):
        lines, split, single = self.run_apply(Strip("A"), analysis())
        single.assert_called_once()
        split.assert_not_called()
        self.assertIn("one crop follows", lines[0])

    def test_nothing_found_says_so_and_keys_nothing(self):
        lines, split, single = self.run_apply(Strip("A"), analysis(primary=False))
        single.assert_not_called()
        self.assertIn("no subject found", lines[0])

    def test_two_subjects_build_the_track_all_split_and_hand_over_the_analysis(self):
        seen = {}

        def build(scene, layout_id, names):
            seen["ready"] = rs._READY.get("A")
            seen["layout"] = layout_id
            return {"status": "ok", "tracking": "built"}

        self.fake_layouts.build = build
        a = analysis(secondary=True, layout="split")
        lines = rs.apply_results(scene_of(Strip("A")), {"A": a}, "auto")
        self.assertEqual(seen, {"ready": a, "layout": "track_all"})
        self.assertEqual(lines, ["built"])
        self.assertIsNone(rs.take_ready("A"))


class TestCap(unittest.TestCase):
    def test_a_short_edit_gets_its_plans(self):
        self.assertEqual(rs._budget([5, 8, 40]), [5, 8, 40])

    def test_a_long_edit_stays_within_the_cap(self):
        counts = rs._budget([40] * 100)
        self.assertLessEqual(sum(counts), rs.MAX_RUN_SAMPLES)
        self.assertGreater(counts.count(0), 0)             # the shots that no longer fit are dropped, and said so
        self.assertTrue(all(n == 0 or n >= sf.MIN_SAMPLES for n in counts))

    def test_a_few_long_shots_share_what_is_left(self):
        counts = rs._budget([40] * 5)
        self.assertEqual(counts, [24] * 5)


def _job(n=3):
    grid = [[0.2] * 8 for _ in range(6)]
    job = rs._Job("A", 24.0)
    job.moments = [(i * 10, grid, grid, "p%d.png" % i) for i in range(n)]
    return job


class TestWorker(unittest.TestCase):
    def test_a_model_that_stops_answering_drops_faces_and_the_run_goes_on(self):
        calls = []

        def ask(path, name, frame):
            calls.append(frame)
            raise _FOLLOW.Unavailable("no answer")

        state = {}
        rs._work([_job()], "auto", "auto", ask, state)
        self.assertEqual(calls, [0])                       # asked once, then not again
        self.assertEqual(state["error"] if "error" in state else "", "")
        self.assertIn("A", state["results"])
        self.assertIn("no answer", state["faces_lost"])

    def test_cancel_stops_before_the_next_sample(self):
        state = {"cancel": True}
        rs._work([_job()], "screen", "auto", None, state)
        self.assertTrue(state["cancelled"])
        self.assertNotIn("results", state)


class TestPoll(unittest.TestCase):
    def poll(self, state):
        thread = mock.Mock()
        thread.is_alive.return_value = False
        rs._RUN.clear()
        rs._RUN.update(thread=thread, state=dict({"error": "", "results": None, "scene": "V", "skipped": [],
                                                   "note": "", "layout": "auto", "on_done": None}, **state))
        rs.bpy.data.scenes.get.return_value = None
        rs._poll()
        return rs.last_result()["message"]

    def test_a_missing_scene_is_said(self):
        msg = self.poll({"results": {"A": analysis()}})
        self.assertIn("not found", msg)
        self.assertIn("nothing keyed", msg)

    def test_a_cancel_is_said(self):
        self.assertIn("stopped", self.poll({"cancelled": True}))

    def test_a_failure_part_way_still_pushes_an_undo_step(self):
        scene = scene_of()
        rs.bpy.data.scenes.get.return_value = scene
        thread = mock.Mock()
        thread.is_alive.return_value = False
        rs._RUN.clear()
        rs._RUN.update(thread=thread, state={"error": "", "results": {"A": analysis()}, "scene": "V", "skipped": [],
                                             "note": "", "layout": "auto", "on_done": None})
        rs.bpy.ops.ed.undo_push.reset_mock()
        with mock.patch.object(rs, "apply_results", side_effect=RuntimeError("boom")):
            rs._poll()
        rs.bpy.ops.ed.undo_push.assert_called_once()
        self.assertIn("boom", rs.last_result()["message"])
        self.assertIn("part-way", rs.last_result()["message"])


if __name__ == "__main__":
    unittest.main()
