# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Subject finding for Follow Subject (#592): activity, pointer, tracks, smoothing, layout choice.

The footage is generated: a small grey picture per moment with a facecam box, a typing area and a pointer
at known positions, so the boxes found can be checked against the truth.  No model, no bpy."""

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfa_coworker"


def _import(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


sf = _import("subject_find")
GW, GH = 64, 36
FPS = 24.0


def blank(level=0.2):
    return [[level] * GW for _ in range(GH)]


def paint(g, x0, y0, x1, y1, value):
    """A rectangle in picture fractions."""
    for y in range(int(y0 * GH), int(y1 * GH)):
        for x in range(int(x0 * GW), int(x1 * GW)):
            g[y][x] = value


def scene(t, pointer=None, typing=False, cam=None, shift=0.0):
    """One moment.  *pointer* (x, y) a 2 x 2 cell blob; *typing* flips the lines in the left area; *cam* a facecam
    box (x0, y0, x1, y1) whose picture changes with t; *shift* lifts the whole background (a cut)."""
    g = blank(0.2 + shift)
    if typing:
        paint(g, 0.10, 0.30, 0.50, 0.80, 0.2 + shift + (0.5 if t % 2 else 0.0))
    if cam:
        paint(g, *cam, 0.5 + shift + (0.3 if t % 2 else 0.0))
    if pointer:
        px, py = pointer
        paint(g, px, py, px + 2 / GW, py + 2 / GH, 0.98)
    return g


def pair(t, **kw):
    """The moment and the same a little later; a pointer moves 0.03 of the picture between the two."""
    later = dict(kw)
    if kw.get("pointer"):
        later["pointer"] = (kw["pointer"][0] + 0.03, kw["pointer"][1])
    return scene(2 * t, **kw), scene(2 * t + 1, **later)


def near(a, b, tol):
    return abs(a - b) <= tol


CAM = (0.74, 0.06, 0.96, 0.42)


class TestActivity(unittest.TestCase):

    def test_a_still_picture_has_no_subject(self):
        self.assertEqual(sf.find_activity(blank(), blank()), [])

    def test_the_pointer_is_found_where_it_moved(self):
        a = scene(0, pointer=(0.30, 0.50))
        b = scene(0, pointer=(0.33, 0.52))
        found = sf.find_activity(a, b)
        self.assertEqual([s.kind for s in found], ["pointer"])
        box = found[0].box
        self.assertTrue(near(box.cx, 0.32, 0.05) and near(box.cy, 0.52, 0.06), box)

    def test_a_changing_area_is_screen_and_its_box_matches(self):
        found = sf.find_activity(*pair(0, typing=True))
        self.assertEqual([s.kind for s in found], ["screen"])
        box = found[0].box
        for got, want in zip(box, (0.10, 0.30, 0.50, 0.80)):
            self.assertTrue(near(got, want, 0.04), box)

    def test_facecam_and_typing_area_stay_two_regions(self):
        found = sf.find_activity(*pair(0, typing=True, cam=CAM))
        self.assertEqual(len(found), 2)
        centres = sorted(round(s.box.cx, 1) for s in found)
        self.assertEqual(centres, [0.3, 0.8])

    def test_noise_below_the_threshold_is_ignored(self):
        a = blank()
        b = [[v + 0.03 for v in row] for row in a]
        self.assertEqual(sf.find_activity(a, b), [])

    def test_a_cut_is_a_whole_picture_change(self):
        self.assertTrue(sf.is_cut(scene(0), scene(0, shift=0.5)))
        self.assertFalse(sf.is_cut(*pair(0, typing=True)))


class TestFaces(unittest.TestCase):

    def test_a_face_replaces_the_facecam_region_around_it(self):
        activity = sf.find_activity(*pair(0, typing=True, cam=CAM))
        face = sf.Box(0.80, 0.10, 0.90, 0.30)
        got = sf.merge_faces(activity, [face])
        self.assertEqual(sorted(s.kind for s in got), ["face", "screen"])
        self.assertTrue(all(s.box.cx < 0.6 for s in got if s.kind == "screen"))

    def test_a_pointer_in_front_of_a_face_stays(self):
        face = sf.Box(0.4, 0.2, 0.6, 0.6)
        pointer = sf.Subject("pointer", sf.Box(0.7, 0.7, 0.75, 0.75))
        self.assertEqual(sorted(s.kind for s in sf.merge_faces([pointer], [face])), ["face", "pointer"])


def run(moments, fps=FPS, requested="auto", step=72):
    """Analyse *moments*: ``[(kwargs of scene, faces)]``, one every *step* frames."""
    samples = []
    for i, (kw, faces) in enumerate(moments):
        a, b = pair(i, **kw)
        samples.append(sf.Sample(i * step, a, b, tuple(faces)))
    return sf.analyse(samples, fps, requested)


def mid(track):
    return sf._median_box(track)  # pylint: disable=protected-access


class TestTracks(unittest.TestCase):

    def test_screen_recording_with_a_facecam_gives_a_split_without_a_model(self):
        truth_cam = CAM
        moments = [(dict(typing=True, cam=truth_cam), []) for _ in range(12)]
        out = run(moments)
        self.assertEqual(out.layout, "split")
        self.assertEqual(len(out.primary), 1)
        found = {round(mid(out.primary[0]).cx, 1), round(mid(out.secondary[0]).cx, 1)}
        self.assertEqual(found, {0.3, 0.8})
        # the camera box in the corner is the main subject (it goes on top), the rest of the screen second
        self.assertEqual((out.primary[0].kind, out.secondary[0].kind), ("pip", "screen"))
        self.assertEqual(round(mid(out.primary[0]).cx, 1), 0.8)

    def test_with_a_face_it_goes_on_top_and_the_screen_below(self):
        face = sf.Box(0.80, 0.10, 0.90, 0.30)
        moments = [(dict(typing=True, cam=CAM), [face]) for _ in range(12)]
        out = run(moments)
        self.assertEqual(out.layout, "split")
        self.assertEqual(out.primary[0].kind, "face")
        self.assertEqual(out.secondary[0].kind, "screen")
        self.assertTrue(near(mid(out.primary[0]).cx, 0.85, 0.01))

    def test_one_talker_off_centre_is_one_crop_that_stays_on_them(self):
        face = sf.Box(0.70, 0.2, 0.84, 0.5)
        out = run([({}, [face]) for _ in range(10)])
        self.assertEqual(out.layout, "single")
        samples = sf.track_samples(out.primary)
        keys = sf.rm.smooth_track(samples, FPS)
        self.assertTrue(all(near(x, 0.77, 0.01) for _f, x in keys), keys)

    def test_the_pointer_alone_is_followed(self):
        moments = [(dict(pointer=(0.2 + 0.05 * i, 0.4)), []) for i in range(10)]
        out = run(moments)
        self.assertEqual(out.layout, "single")
        self.assertEqual(out.primary[0].kind, "pointer")

    def test_nothing_found_falls_back_to_the_centre(self):
        out = run([({}, []) for _ in range(6)])
        self.assertEqual(out.layout, "centre")
        self.assertEqual(out.primary, ())

    def test_a_split_asked_for_without_two_subjects_stays_single(self):
        face = sf.Box(0.4, 0.2, 0.6, 0.6)
        out = run([({}, [face]) for _ in range(8)], requested="split")
        self.assertEqual(out.layout, "single")

    def test_single_asked_for_wins_over_two_subjects(self):
        out = run([(dict(typing=True, cam=CAM), []) for _ in range(12)], requested="single")
        self.assertEqual(out.layout, "single")

    def test_noise_subject_seen_once_is_dropped(self):
        face = sf.Box(0.4, 0.2, 0.6, 0.6)
        moments = [({}, [face]) for _ in range(10)]
        moments[4] = (dict(pointer=(0.8, 0.8)), [face])
        out = run(moments)
        self.assertEqual(out.layout, "single")
        self.assertIsNone(out.secondary[0])

    def test_two_subjects_close_together_are_not_apart(self):
        a, b = sf.Box(0.40, 0.2, 0.50, 0.5), sf.Box(0.52, 0.2, 0.62, 0.5)
        out = run([({}, [a, b]) for _ in range(8)])
        self.assertEqual(out.layout, "single")

    def test_two_faces_apart_make_a_split(self):
        a, b = sf.Box(0.10, 0.2, 0.25, 0.5), sf.Box(0.70, 0.2, 0.85, 0.5)
        out = run([({}, [a, b]) for _ in range(8)])
        self.assertEqual(out.layout, "split")

    def test_a_cut_starts_new_tracks(self):
        face_a, face_b = sf.Box(0.1, 0.2, 0.25, 0.5), sf.Box(0.7, 0.2, 0.85, 0.5)
        moments = [(dict(), [face_a]) for _ in range(5)] + [(dict(shift=0.5), [face_b]) for _ in range(5)]
        out = run(moments)
        self.assertEqual(out.cuts, (5,))
        self.assertEqual(len(out.primary), 2)
        self.assertTrue(near(mid(out.primary[0]).cx, 0.175, 0.01))
        self.assertTrue(near(mid(out.primary[1]).cx, 0.775, 0.01))


class TestLayoutHold(unittest.TestCase):

    def test_a_short_flicker_does_not_change_the_layout(self):
        labels = ["single"] * 6 + ["split"] + ["single"] * 6
        frames = [i * 24 for i in range(len(labels))]          # one sample a second
        runs = sf.hold_runs(labels, frames, FPS, 2.0)
        self.assertEqual(runs, [("single", 0, 12)])

    def test_a_long_split_holds(self):
        labels = ["single"] * 4 + ["split"] * 5 + ["single"] * 4
        frames = [i * 24 for i in range(len(labels))]
        runs = sf.hold_runs(labels, frames, FPS, 2.0)
        self.assertEqual([r[0] for r in runs], ["single", "split", "single"])

    def test_every_run_but_perhaps_the_last_lasts_the_minimum(self):
        labels = ["split", "single", "split", "single", "single", "split", "split", "split", "single"]
        frames = [i * 24 for i in range(len(labels))]
        runs = sf.hold_runs(labels, frames, FPS, 2.0)
        for lab, a, b in runs[:-1]:
            self.assertGreaterEqual((frames[b + 1] - frames[a]) / FPS, 2.0, (lab, a, b))

    def test_empty(self):
        self.assertEqual(sf.hold_runs([], [], FPS), [])

    def test_choose(self):
        runs = [("single", 0, 3), ("split", 4, 12)]
        self.assertEqual(sf.choose_layout("auto", runs, True), "split")
        self.assertEqual(sf.choose_layout("auto", runs, False), "single")
        self.assertEqual(sf.choose_layout("single", runs, True), "single")
        self.assertEqual(sf.choose_layout("split", runs, True), "split")
        self.assertEqual(sf.choose_layout("split", runs, False), "single")


class TestFraming(unittest.TestCase):

    def _track(self, xs, y=0.3, size=0.1, kind="face", step=24):
        pts = tuple((i * step, None if x is None else sf.Box(x - size / 2, y - size / 2, x + size / 2, y + size / 2))
                    for i, x in enumerate(xs))
        return sf.Track(kind, pts, 1.0)

    def test_u_follows_the_subject_and_v_is_from_the_bottom(self):
        keys, zoom = sf.part_framing([self._track([0.8] * 6)], 1080, 960, 1920, 1080, FPS)
        self.assertTrue(keys)
        self.assertTrue(all(near(u, 0.8, 1e-6) and near(v, 0.7, 1e-6) for _f, u, v in keys), keys)
        self.assertGreater(zoom, 1.0)

    def test_a_small_face_zooms_in_and_a_big_region_does_not(self):
        _k, face_zoom = sf.part_framing([self._track([0.5] * 4, size=0.08)], 1080, 960, 1920, 1080, FPS)
        _k, screen_zoom = sf.part_framing([self._track([0.5] * 4, size=0.8, kind="screen")], 1080, 960, 1920, 1080, FPS)
        self.assertGreater(face_zoom, 1.5)
        self.assertEqual(screen_zoom, 1.0)
        self.assertLessEqual(face_zoom, sf.ZOOM_MAX)

    def test_the_crop_does_not_jitter(self):
        wobble = [0.50, 0.52, 0.49, 0.51, 0.50, 0.53, 0.48, 0.50]
        keys, _z = sf.part_framing([self._track(wobble)], 1080, 960, 1920, 1080, FPS)
        us = {round(u, 3) for _f, u, _v in keys}
        self.assertEqual(len(us), 1, us)

    def test_it_never_pans_faster_than_the_limit(self):
        keys, _z = sf.part_framing([self._track([0.1, 0.9, 0.9, 0.9, 0.9], step=12)], 1080, 960, 1920, 1080, FPS)
        for (f0, u0, _a), (f1, u1, _b) in zip(keys, keys[1:]):
            self.assertLessEqual(abs(u1 - u0), 0.25 * (f1 - f0) / FPS + 1e-6)

    def test_a_cut_jumps_instead_of_sliding(self):
        a = self._track([0.2] * 5)
        b = sf.Track("face", tuple((f + 24 * 5, box) for f, box in self._track([0.8] * 5).points), 1.0)
        keys, _z = sf.part_framing([a, b], 1080, 960, 1920, 1080, FPS)
        mids = [(f, u) for f, u, _v in keys if 0.25 < u < 0.75]
        self.assertEqual(mids, [], keys)           # no key in between: the value jumps
        for (f0, u0, _a), (f1, u1, _b) in zip(keys, keys[1:]):
            if abs(u1 - u0) > 0.3:
                self.assertLessEqual(f1 - f0, 1)

    def test_nothing_gives_nothing(self):
        self.assertEqual(sf.part_framing([None], 1080, 960, 1920, 1080, FPS), ([], 1.0))

    def test_sample_plan_has_a_floor_and_a_cap(self):
        self.assertEqual(sf.sample_plan(24 * 5, FPS), sf.MIN_SAMPLES)
        self.assertEqual(sf.sample_plan(24 * 60, FPS), sf.PER_MINUTE)
        self.assertEqual(sf.sample_plan(24 * 3600, FPS), sf.MAX_SAMPLES)


if __name__ == "__main__":
    unittest.main()
