# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #539: Remove Silences keeps the whole edit in step (made-up data only).

1. ``wand/cut_plan.py`` (bpy-free): the common cuts of several sounds, the time map,
   the plan per strip (refusals, cuts, caption trims and drops), piece names, the
   scene end.
2. ``wand/sequencer.py`` on a stand-in strip model that splits, renames and pushes
   an overlapping strip to another channel the way Blender does: two speakers stay
   in sync, captions are trimmed with no overlap and no tail, a locked strip refuses
   before anything changes, names stay, the scene end follows, a second run does
   nothing, an hour of strips plans and applies quickly.
3. The ``remove_silences`` tool: its tool-code, its signature and its domain.

Run with::

    python -m unittest discover -s tests -p "test_remove_silences_whole_edit.py" -v
"""

__all__ = ()

import importlib.util
import re
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import test_wand_remove_silences as W

_ROOT = Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / "mcp" / "blmcp" / "tools"
P = W._import("wand.cut_plan")
CAP = W._import("captions")
FPS = 30


# -- 1. the plan --------------------------------------------------------------------------

class TestCommonCuts(unittest.TestCase):

    def test_one_sound_is_its_own_silences(self):
        self.assertEqual(P.common_cuts([(0, 100, [(0, 10), (40, 60)])]), [(0, 10), (40, 60)])

    def test_two_speakers_cut_only_where_both_are_quiet(self):
        a = (0, 100, [(30, 70)])            # A talks 0-30 and 70-100
        b = (0, 100, [(0, 40), (60, 100)])  # B talks 40-60
        self.assertEqual(P.common_cuts([a, b]), [(30, 40), (60, 70)])

    def test_no_sound_at_all_is_not_a_silence(self):
        a = (0, 50, [(40, 50)])
        b = (80, 120, [(80, 90)])
        self.assertEqual(P.common_cuts([a, b]), [(40, 50), (80, 90)])  # 50-80 has no sound: kept

    def test_a_sound_that_was_not_read_blocks_its_span(self):
        a = (0, 100, [(0, 100)])
        self.assertEqual(P.common_cuts([a, (20, 30, [])]), [(0, 20), (30, 100)])

    def test_subtract(self):
        self.assertEqual(P.subtract([(0, 10)], [(2, 3), (5, 20)]), [(0, 2), (3, 5)])
        self.assertEqual(P.subtract([(0, 10), (20, 30)], []), [(0, 10), (20, 30)])
        self.assertEqual(P.subtract([(0, 10)], [(0, 10)]), [])


class TestMap(unittest.TestCase):

    def test_map_frame(self):
        cuts = [(10, 20), (30, 35)]
        self.assertEqual(P.map_frame(5, cuts), 5)
        self.assertEqual(P.map_frame(10, cuts), 10)
        self.assertEqual(P.map_frame(15, cuts), 10)       # inside: where the cut began
        self.assertEqual(P.map_frame(20, cuts), 10)
        self.assertEqual(P.map_frame(32, cuts), 20)
        self.assertEqual(P.map_frame(40, cuts), 25)

    def test_map_matches_the_old_ripple(self):
        S = W.S
        cuts = [(10, 20), (30, 35), (50, 51)]
        starts = list(range(0, 80, 3))
        self.assertEqual([s - P.map_frame(s, cuts) for s in starts], S.ripple_shifts(starts, cuts))

    def test_overlapping(self):
        self.assertEqual(P.overlapping(15, 33, [(10, 20), (30, 35), (40, 50)]), [(15, 20), (30, 33)])


def _s(name, left, right, kind="media", **kw):
    return dict({"name": name, "left": left, "right": right, "kind": kind, "locked": False, "refuse": ""}, **kw)


class TestPlan(unittest.TestCase):

    def test_media_cut_and_untouched_left(self):
        out = P.plan([_s("a", 0, 100), _s("b", 100, 200)], [(10, 20), (50, 60)], fps=FPS)
        self.assertEqual(out["refused"], [])
        self.assertEqual(out["cut"], {"a": [(10, 20), (50, 60)]})

    def test_locked_over_a_cut_refuses(self):
        out = P.plan([_s("a", 0, 100), _s("Cam", 0, 100, locked=True)], [(10, 20)], fps=FPS)
        self.assertEqual(out["refused"], [("Cam", "is locked")])

    def test_locked_after_a_cut_refuses_only_with_ripple(self):
        strips = [_s("a", 0, 100), _s("end", 100, 150, locked=True)]
        self.assertEqual(P.plan(strips, [(10, 20)], ripple=True, fps=FPS)["refused"][0][0], "end")
        self.assertEqual(P.plan(strips, [(10, 20)], ripple=False, fps=FPS)["refused"], [])

    def test_locked_before_the_cuts_is_fine(self):
        self.assertEqual(P.plan([_s("x", 0, 5, locked=True)], [(10, 20)], fps=FPS)["refused"], [])

    def test_strips_that_cannot_be_cut(self):
        strips = [_s("meta", 0, 100, refuse="is a meta strip"), _s("later", 200, 300, refuse="is a scene strip")]
        out = P.plan(strips, [(10, 20)], fps=FPS)
        self.assertEqual(out["refused"], [("meta", "is a meta strip")])     # "later" only moves

    def test_effects_follow(self):
        out = P.plan([_s("a", 0, 100), _s("fx", 0, 100, kind="follow")], [(10, 20)], fps=FPS)
        self.assertNotIn("fx", out["cut"])

    def test_caption_trimmed_to_what_it_keeps(self):
        out = P.plan([_s("cap", 0, 60, kind="text")], [(10, 20), (50, 70)], fps=FPS)
        self.assertEqual(out["trim"], {"cap": (0, 40)})
        self.assertEqual(out["cut"], {})

    def test_caption_inside_a_cut_goes_with_its_words(self):
        strips = [_s("cap", 12, 18, kind="text"), _s("cap_w01", 12, 18, kind="text", line="cap"),
                  _s("other_w01", 30, 40, kind="text", line="other")]
        out = P.plan(strips, [(10, 20)], fps=FPS)
        self.assertEqual(sorted(out["drop"]), ["cap", "cap_w01"])
        self.assertNotIn("other_w01", out["trim"])        # untouched: only moves

    def test_caption_left_too_short_goes(self):
        # 1 s caption, 0.8 s cut away: 0.2 s left (< 0.3 s)
        out = P.plan([_s("cap", 0, 30, kind="text")], [(6, 30)], fps=FPS)
        self.assertEqual(out["drop"], ["cap"])
        # 0.5 s left: kept
        out = P.plan([_s("cap", 0, 30, kind="text")], [(15, 30)], fps=FPS)
        self.assertEqual(out["trim"], {"cap": (0, 15)})

    def test_a_short_word_keeps_half_of_itself(self):
        out = P.plan([_s("w", 0, 6, kind="text")], [(4, 6)], fps=FPS)    # 0.2 s word, 0.13 s left
        self.assertEqual(out["trim"], {"w": (0, 4)})
        out = P.plan([_s("w", 0, 6, kind="text")], [(2, 6)], fps=FPS)    # 0.07 s left
        self.assertEqual(out["drop"], ["w"])

    def test_captions_without_ripple_are_cut_and_short_pieces_go(self):
        out = P.plan([_s("cap", 0, 60, kind="text")], [(5, 50)], ripple=False, fps=FPS)
        self.assertEqual(out["cut"], {"cap": [(0, 50)]})    # the 5-frame head is too short: only 50-60 stays
        out = P.plan([_s("cap", 0, 10, kind="text")], [(3, 6)], ripple=False, fps=FPS)
        self.assertEqual(out["cut"], {"cap": [(0, 6)]})     # both pieces short: the longest stays

    def test_mapped_captions_never_overlap(self):
        rows = [_s("c{:d}".format(i), i * 13, i * 13 + 13, kind="text") for i in range(20)]
        cuts = [(5, 17), (40, 41), (60, 90), (120, 200), (230, 231)]
        out = P.plan(rows, cuts, fps=FPS)
        spans = []
        for r in rows:
            if r["name"] in out["drop"]:
                continue
            spans.append(out["trim"].get(r["name"], (P.map_frame(r["left"], cuts),
                                                     P.map_frame(r["left"], cuts) + r["right"] - r["left"])))
        spans.sort()
        for a, b in zip(spans, spans[1:]):
            self.assertLessEqual(a[1], b[0])

    def test_piece_names(self):
        self.assertEqual(P.piece_names("F", 3, set()), ["F", "F.001", "F.002"])
        self.assertEqual(P.piece_names("F", 3, {"F.001"}), ["F", "F.002", "F.003"])
        self.assertEqual(P.piece_names("F", 0, set()), [])

    def test_stretch_edges(self):
        self.assertEqual(P.stretch_edges(1, 301, [(285, 301)], 1, 302), (1, 302, [(285, 302)]))
        self.assertEqual(P.stretch_edges(1, 301, [(100, 120)], 1, 302), (1, 302, [(100, 120)]))  # loud end kept
        self.assertEqual(P.stretch_edges(2, 301, [(2, 20)], 1, 301), (1, 301, [(1, 20)]))

    def test_follow_end(self):
        self.assertEqual(P.follow_end(300, 301, 166), 165)      # at the end: follows
        self.assertEqual(P.follow_end(400, 301, 166), 165)      # past it: follows
        self.assertIsNone(P.follow_end(200, 301, 166))          # set short on purpose: left
        self.assertEqual(P.follow_end(300, 301, 1, frame_start=1), 1)

    def test_an_hour_plans_quickly(self):
        rows = [_s("s{:d}".format(i), i * 750, i * 750 + 750) for i in range(144)]
        rows += [_s("m{:d}".format(i), i * 750, i * 750 + 750) for i in range(144)]
        rows += [_s("c{:d}".format(i), i * 180, i * 180 + 150, kind="text") for i in range(600)]
        cuts = [(i * 180 + 150, i * 180 + 170) for i in range(600)]
        t0 = time.perf_counter()
        out = P.plan(rows, cuts, fps=FPS)
        self.assertLess(time.perf_counter() - t0, 0.5)
        self.assertEqual(len(out["cut"]), 288)


# -- 2. the operator's work on a stand-in Blender -----------------------------------------

class _Gone(Exception):
    pass


class FakeStrip:
    """Enough of ``bpy.types.Strip`` (5.2 names) for Remove Silences."""

    def __init__(self, ed, name, kind, channel, left, right, path="", text=""):
        self.__dict__.update(_ed=ed, _name=None, type=kind, channel=channel, _left=left, _right=right,
                             _cs=float(left), select=False, lock=False, retiming_keys=[], sound_offset=0.0,
                             filepath=path, text=text, props={}, removed=False, sound=None, input_1=None,
                             location=(0.5, 0.08))
        if kind == "SOUND":
            self.__dict__["sound"] = types.SimpleNamespace(filepath=path, library=None, packed_file=None)
        if kind != "SOUND":
            del self.__dict__["sound_offset"]
        self.name = name

    def __getattribute__(self, item):
        if item not in ("__dict__", "removed") and object.__getattribute__(self, "__dict__").get("removed"):
            raise _Gone(item)
        return object.__getattribute__(self, item)

    def __setattr__(self, key, value):
        if key == "name":
            self.__dict__["_name"] = self._ed.unique(value, self)
        elif key == "right_handle":
            self.__dict__["_right"] = int(value)
            self._ed.shuffle(self)
        elif key == "content_start":
            delta = int(round(value - self._cs))
            self.__dict__.update(_cs=float(value), _left=self._left + delta, _right=self._right + delta)
            self._ed.shuffle(self)
        else:
            self.__dict__[key] = value

    name = property(lambda self: self._name)
    left_handle = property(lambda self: self._left)
    right_handle = property(lambda self: self._right)
    content_start = property(lambda self: self._cs)
    content_duration = property(lambda self: self._right - self._left)

    def as_pointer(self):
        return id(self)

    def get(self, key, default=None):
        return self.props.get(key, default)

    def __getitem__(self, key):
        return self.props[key]

    def __setitem__(self, key, value):
        self.props[key] = value

    def split(self, frame, split_method="SOFT", ignore_connections=True):
        assert self._left < frame < self._right
        right = FakeStrip(self._ed, self._name, self.type, self.channel, frame, self._right, self.filepath, self.text)
        right.__dict__.update(_cs=self._cs, lock=self.lock, select=self.select,
                              props=dict(self.props), sound=self.sound)
        if "sound_offset" in self.__dict__:
            right.__dict__["sound_offset"] = self.sound_offset
        self.__dict__["_right"] = frame
        self._ed.items.append(right)
        return right


class FakeStrips(list):
    def remove(self, strip):
        super().remove(strip)
        strip.__dict__["removed"] = True

    def get(self, name):
        return next((s for s in self if s.name == name), None)


class FakeEditor:
    def __init__(self):
        self.items = FakeStrips()
        self.meta_stack = []
        self.channels = [types.SimpleNamespace(lock=False, name="Channel {:d}".format(i)) for i in range(64)]
        self.shuffled = 0

    strips = property(lambda self: self.items)
    strips_all = property(lambda self: self.items)

    def unique(self, name, me):
        taken = {s._name for s in self.items if s is not me and not s.removed}
        if name not in taken:
            return name
        n = 1
        while "{:s}.{:03d}".format(name, n) in taken:
            n += 1
        return "{:s}.{:03d}".format(name, n)

    def shuffle(self, strip):
        """Blender moves a strip that lands on another one to the next free channel."""
        def hit():
            return any(o is not strip and not o.removed and o.channel == strip.channel and
                       o._left < strip._right and o._right > strip._left for o in self.items)
        while hit():
            strip.__dict__["channel"] += 1
            self.shuffled += 1

    def add(self, name, kind, channel, left, right, path="", text=""):
        s = FakeStrip(self, name, kind, channel, left, right, path, text)
        self.items.append(s)
        return s


def _scene(ed, end):
    return types.SimpleNamespace(sequence_editor=ed, name="Edit", frame_start=1, frame_end=end,
                                 render=types.SimpleNamespace(fps=FPS, fps_base=1.0), use_preview_range=False,
                                 frame_preview_start=1, frame_preview_end=end, animation_data=None)


def _env(loud, seconds):
    """A 10 ms envelope: -10 dB inside the *loud* (start, end) seconds, -80 dB elsewhere."""
    out = []
    for i in range(int(round(seconds / 0.01))):
        t = i * 0.01
        out.append(-10.0 if any(a <= t < b for a, b in loud) else -80.0)
    return out


SETTINGS = (-40.0, 0.4, 0.15, 0.1)


class _Pipeline(unittest.TestCase):

    def setUp(self):
        bpy = W._fake_bpy(False)
        self.seq = W._load_sequencer(bpy)
        self.speech = {}

        def analyse(scene, sounds, context=None, report_progress=True):
            infos = []
            for s in sounds:
                loud, seconds = self.speech[s.name]
                infos.append({"sound": s, "env": _env(loud, seconds), "t0": 0.0, "t1": seconds, "fps": FPS,
                              "content_start": s.content_start, "offset": 0.0, "left": s.left_handle,
                              "right": s.right_handle, "name": s.name})
            return infos, [], []
        self.seq._analyse = analyse

    def run_op(self, scene, names, ripple=True):
        return self.seq.remove_silences(scene, SETTINGS, names, ripple, True, None, False)


def _media_time(ed, path, frame):
    for s in ed.items:
        if s.filepath == path and s.type != "TEXT" and s._left <= frame < s._right:
            return (frame - s._cs) / FPS
    return None


class TestWholeEdit(_Pipeline):

    def _two_speakers(self):
        ed = FakeEditor()
        a = ed.add("Speaker_A", "SOUND", 1, 1, 301, "a.wav")
        b = ed.add("Speaker_B", "SOUND", 2, 1, 301, "b.wav")
        cam = ed.add("Cam", "MOVIE", 3, 1, 301, "cam.mp4")
        title = ed.add("Title", "COLOR", 4, 1, 301)
        self.speech = {"Speaker_A": ([(0.8, 3.0), (5.0, 6.0), (8.0, 9.3)], 10.0),
                       "Speaker_B": ([(2.6, 4.0), (6.5, 7.2)], 10.0)}
        return ed, a, b, cam, title

    def test_two_speakers_and_a_camera_stay_in_sync(self):
        ed, a, b, cam, title = self._two_speakers()
        sc = _scene(ed, 300)
        out = self.run_op(sc, ["Speaker_A", "Speaker_B"])
        self.assertEqual(out["status"], "ok", out)
        self.assertGreater(out["cuts"], 0)
        end = max(s._right for s in ed.items)
        for f in range(1, end):
            ta, tb, tc = (_media_time(ed, p, f) for p in ("a.wav", "b.wav", "cam.mp4"))
            self.assertIsNotNone(ta)
            self.assertAlmostEqual(ta, tb)
            self.assertAlmostEqual(ta, tc)
        self.assertEqual(sum(s._right - s._left for s in ed.items if s.type == "COLOR"), end - 1)
        # B's words are kept: 2.6-4.0 s is never cut although A is quiet there
        kept = [(a_, b_) for a_, b_ in out["cut_ranges"] if a_ < 1 + 4.0 * FPS and b_ > 1 + 2.6 * FPS]
        self.assertEqual(kept, [])
        self.assertEqual(ed.shuffled, 0)
        self.assertEqual(sc.frame_end, end - 1)
        self.assertEqual(out["end_frame"], end - 1)

    def test_names_stay_on_the_first_piece(self):
        ed, *_ = self._two_speakers()
        ed.add("Speaker_A.001", "SOUND", 5, 400, 500, "other.wav")     # a name the numbering must skip
        out = self.run_op(_scene(ed, 300), ["Speaker_A", "Speaker_B"])
        names = out["new_names"]
        self.assertEqual(names["Speaker_A"][0], "Speaker_A")
        self.assertEqual(names["Cam"][:3], ["Cam", "Cam.001", "Cam.002"])
        self.assertNotIn("Speaker_A.001", names["Speaker_A"])
        self.assertEqual(sorted(s.name for s in ed.items if s.name.startswith("Speaker_A")),
                         sorted(names["Speaker_A"] + ["Speaker_A.001"]))
        pieces = sorted((s._left, s.name) for s in ed.items if s.filepath == "cam.mp4")
        self.assertEqual([n for _l, n in pieces], names["Cam"])

    def test_second_run_does_nothing(self):
        ed, *_ = self._two_speakers()
        sc = _scene(ed, 300)
        self.run_op(sc, ["Speaker_A", "Speaker_B"])
        for s in ed.items:
            s.select = s.type == "SOUND"
        before = sorted((s.name, s._left, s._right, s.channel) for s in ed.items)
        # the pieces now play these seconds of the files: the same speech, pieces of it
        self.speech = {}
        for s in ed.items:
            if s.type == "SOUND":
                t0 = (s._left - s._cs) / FPS
                t1 = (s._right - s._cs) / FPS
                loud = [(0.8, 3.0), (5.0, 6.0), (8.0, 9.3)] if s.filepath == "a.wav" else [(2.6, 4.0), (6.5, 7.2)]
                self.speech[s.name] = ([(max(0, a - t0), b - t0) for a, b in loud if b > t0 and a < t1], t1 - t0)
        out = self.run_op(sc, None)
        self.assertEqual(out["status"], "nothing", out)
        self.assertEqual(sorted((s.name, s._left, s._right, s.channel) for s in ed.items), before)

    def test_locked_picture_refuses_and_changes_nothing(self):
        ed, a, b, cam, title = self._two_speakers()
        cam.lock = True
        before = sorted((s.name, s._left, s._right) for s in ed.items)
        out = self.run_op(_scene(ed, 300), ["Speaker_A", "Speaker_B"])
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["refused"], [{"strip": "Cam", "reason": "is locked"}])
        self.assertIn("“Cam” is locked", out["message"])
        self.assertEqual(sorted((s.name, s._left, s._right) for s in ed.items), before)

    def test_locked_channel_refuses(self):
        ed, *_ = self._two_speakers()
        ed.channels[4].lock = True
        out = self.run_op(_scene(ed, 300), ["Speaker_A", "Speaker_B"])
        self.assertEqual([r["strip"] for r in out["refused"]], ["Title"])

    def test_gaps_left_open(self):
        ed, *_ = self._two_speakers()
        sc = _scene(ed, 300)
        out = self.run_op(sc, ["Speaker_A", "Speaker_B"], ripple=False)
        self.assertEqual(out["status"], "ok")
        pieces = sorted((s._left, s._right) for s in ed.items if s.filepath == "a.wav")
        self.assertTrue(any(b[0] > a[1] for a, b in zip(pieces, pieces[1:])))
        for f in range(1, 300):                 # nothing moved: every frame still shows the same moment
            t = _media_time(ed, "a.wav", f)
            if t is not None:
                self.assertAlmostEqual(t, (f - 1) / FPS)
        # the quiet tail went (the only cut that shortens the edit): the scene end follows it
        self.assertEqual(sc.frame_end, max(s._right for s in ed.items) - 1)
        self.assertEqual(sc.frame_end, 284)

    def test_scene_end_left_when_asked(self):
        ed, *_ = self._two_speakers()
        sc = _scene(ed, 300)
        self.seq.remove_silences(sc, SETTINGS, ["Speaker_A", "Speaker_B"], True, False, None, False)
        self.assertEqual(sc.frame_end, 300)

    def test_preview_range_follows(self):
        ed, *_ = self._two_speakers()
        sc = _scene(ed, 300)
        sc.use_preview_range, sc.frame_preview_start, sc.frame_preview_end = True, 10, 300
        self.run_op(sc, ["Speaker_A", "Speaker_B"])
        self.assertEqual(sc.frame_preview_end, sc.frame_end)

    def test_captions_trimmed_no_overlap_no_tail(self):
        ed = FakeEditor()
        ed.add("Cam", "MOVIE", 2, 1, 301, "cam.mp4")
        ed.add("Cam_snd", "SOUND", 1, 1, 301, "cam.mp4")
        cues = [(0.8, 2.0), (2.0, 3.4), (3.4, 4.5), (4.5, 5.0), (5.0, 5.9), (5.9, 8.6), (8.6, 9.5), (9.5, 9.95)]
        caps = []
        for i, (s, e) in enumerate(cues, 1):
            c = ed.add("cap_{:04d}".format(i), "TEXT", 3, 1 + round(s * FPS), 1 + round(e * FPS), text="cue")
            c["bfacw_caption"] = {"role": "line", "word_frames": [0, 5]}
            caps.append(c)
            w = ed.add("cap_{:04d}_w01".format(i), "TEXT", 4, c._left, c._right, text="w")
            w["bfacw_caption"] = {"role": "word", "line": c.name}
        self.speech = {"Cam_snd": ([(0.8, 3.0), (5.0, 6.0), (8.0, 9.3)], 10.0)}
        sc = _scene(ed, 300)
        out = self.run_op(sc, ["Cam", "Cam_snd"])
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(ed.shuffled, 0)
        texts = [s for s in ed.items if s.type == "TEXT"]
        self.assertEqual({s.channel for s in texts}, {3, 4})
        media_end = max(s._right for s in ed.items if s.type != "TEXT")
        for ch in (3, 4):
            rows = sorted((s._left, s._right) for s in texts if s.channel == ch)
            for x, y in zip(rows, rows[1:]):
                self.assertLessEqual(x[1], y[0])
            self.assertLessEqual(rows[-1][1], media_end)
        self.assertEqual(sorted(out["captions_dropped"]),
                         ["cap_0003", "cap_0003_w01", "cap_0004", "cap_0004_w01", "cap_0008", "cap_0008_w01"])
        self.assertEqual(sc.frame_end, media_end - 1)
        # a word strip still spans its line, and the line kept its name
        for s in texts:
            if s.props["bfacw_caption"]["role"] == "word":
                line = ed.items.get(s.props["bfacw_caption"]["line"])
                self.assertEqual((s._left, s._right), (line._left, line._right))

    def _signed(self, ed, name, left, right, channel=3, role="line", line=None):
        c = ed.add(name, "TEXT", channel, left, right, text=name.upper())
        c["bfacw_caption"] = {"role": role, **({"line": line} if line else {})}
        c["bfacw_caption"]["sig"] = CAP.caption_signature(c.text, right - left, channel, c.location)
        return c

    def test_trimmed_captions_are_not_hand_edited_afterwards(self):
        """#552: a re-run of Auto Captions keeps hand-edited captions; a trim by the cut isn't one."""
        ed = FakeEditor()
        ed.add("Cam", "MOVIE", 2, 1, 301, "cam.mp4")
        ed.add("Cam_snd", "SOUND", 1, 1, 301, "cam.mp4")
        across = self._signed(ed, "cap_0001", 61, 181)        # 2.0-6.0 s: over the 3.0-5.0 s pause
        edited = self._signed(ed, "cap_0002", 181, 271)       # 6.0-9.0 s, then changed by hand
        edited.text = "CHANGED BY HAND"
        self.speech = {"Cam_snd": ([(0.8, 3.0), (5.0, 6.0), (8.0, 9.3)], 10.0)}
        out = self.run_op(_scene(ed, 300), ["Cam", "Cam_snd"])
        self.assertIn("cap_0001", out["captions_trimmed"])
        self.assertIn("cap_0002", out["captions_trimmed"])
        sig = lambda s: CAP.caption_signature(s.text, s._right - s._left, s.channel, s.location)  # noqa: E731
        self.assertEqual(across.props["bfacw_caption"]["sig"], sig(across))      # still as Coworker made it
        self.assertNotEqual(edited.props["bfacw_caption"]["sig"], sig(edited))   # still edited by hand

    def test_remove_silences_asks_the_captions_to_fit_their_animation_again(self):
        """#542: after the cut the unedited captions it trimmed are refitted (captions_ops.refit_captions)."""
        ed = FakeEditor()
        ed.add("Cam", "MOVIE", 2, 1, 301, "cam.mp4")
        ed.add("Cam_snd", "SOUND", 1, 1, 301, "cam.mp4")
        self._signed(ed, "cap_0001", 61, 181)             # over the 3.0-5.0 s pause: trimmed
        edited = self._signed(ed, "cap_0002", 181, 271)
        edited.text = "CHANGED BY HAND"                   # edited: not refitted
        self.speech = {"Cam_snd": ([(0.8, 3.0), (5.0, 6.0), (8.0, 9.3)], 10.0)}
        asked = []
        fake = types.ModuleType(W._PKG + ".captions_ops")
        fake.refit_captions = lambda scene, names: asked.append(list(names)) or len(names)
        with mock.patch.dict(sys.modules, {W._PKG + ".captions_ops": fake}):
            out = self.run_op(_scene(ed, 300), ["Cam", "Cam_snd"])
        self.assertIn("cap_0001", out["captions_trimmed"])
        self.assertEqual(len(asked), 1, asked)
        self.assertIn("cap_0001", asked[0])
        self.assertNotIn("cap_0002", asked[0])

    def test_gaps_left_word_strips_name_their_piece(self):
        ed = FakeEditor()
        ed.add("Cam", "MOVIE", 2, 1, 301, "cam.mp4")
        ed.add("Cam_snd", "SOUND", 1, 1, 301, "cam.mp4")
        line = self._signed(ed, "cap_0001", 61, 181)
        w1 = self._signed(ed, "cap_0001_w01", 61, 100, channel=4, role="word", line="cap_0001")
        w2 = self._signed(ed, "cap_0001_w02", 150, 181, channel=4, role="word", line="cap_0001")
        self.speech = {"Cam_snd": ([(0.8, 3.0), (5.0, 6.0), (8.0, 9.3)], 10.0)}
        out = self.run_op(_scene(ed, 300), ["Cam", "Cam_snd"], ripple=False)
        names = out["new_names"]["cap_0001"]
        self.assertEqual(names[0], "cap_0001")
        self.assertEqual(len(names), 2)
        self.assertEqual(w1.props["bfacw_caption"]["line"], "cap_0001")
        self.assertEqual(w2.props["bfacw_caption"]["line"], names[1])
        piece = ed.items.get(names[1])
        sig = lambda s: CAP.caption_signature(s.text, s._right - s._left, s.channel, s.location)  # noqa: E731
        for s in (line, piece, w1, w2):
            self.assertEqual(s.props["bfacw_caption"]["sig"], sig(s), s.name)

    def test_no_one_frame_picture_left_past_its_sound(self):
        """The picture a frame longer than its own sound: the trailing silence takes that frame too."""
        ed = FakeEditor()
        ed.add("Cam", "MOVIE", 2, 1, 302, "cam.mp4")
        snd = ed.add("Cam_snd", "SOUND", 1, 1, 301, "cam.mp4")
        self.speech = {"Cam_snd": ([(0.8, 3.0), (5.0, 6.0), (8.0, 9.3)], 10.0)}
        sc = _scene(ed, 301)
        out = self.run_op(sc, ["Cam", "Cam_snd"])
        self.assertEqual(out["status"], "ok")
        movies = sorted((s._left, s._right) for s in ed.items if s.type == "MOVIE")
        sounds = sorted((s._left, s._right) for s in ed.items if s.type == "SOUND")
        self.assertEqual(movies, sounds)
        self.assertGreaterEqual(min(b - a for a, b in movies), 10)
        self.assertEqual(sc.frame_end, movies[-1][1] - 1)
        del snd

    def test_an_hour_applies_quickly(self):
        ed = FakeEditor()
        speech = {}
        for i in range(144):
            left = 1 + i * 750
            ed.add("cam{:03d}".format(i), "MOVIE", 2, left, left + 750, "cam.mp4")
            ed.add("snd{:03d}".format(i), "SOUND", 1, left, left + 750, "cam.mp4")
            speech["snd{:03d}".format(i)] = ([(0.8, 11.0), (14.9, 16.3)], 25.0)
        self.speech = speech
        for s in ed.items:
            s.select = True
        t0 = time.perf_counter()
        out = self.run_op(_scene(ed, 144 * 750), None)
        took = time.perf_counter() - t0
        self.assertEqual(out["status"], "ok")
        self.assertEqual(ed.shuffled, 0)
        self.assertLess(took, 10.0)         # the stand-in is slower than Blender (its overlap check is Python)


# -- 3. the tool --------------------------------------------------------------------------------

class TestTool(unittest.TestCase):

    def _toolcode(self, wand):
        source = (_TOOLS / "remove_silences_toolcode.py").read_text(encoding="utf-8")
        ns = {"__name__": "remove_silences_toolcode"}
        exec(compile(source, "remove_silences_toolcode.py", "exec"), ns)  # pylint: disable=exec-used
        key = "probe_pkg.bfa_coworker.wand.sequencer"
        saved = sys.modules.get(key)
        if wand is not None:
            sys.modules[key] = wand
        self.addCleanup(lambda: sys.modules.pop(key, None) if saved is None else sys.modules.update({key: saved}))
        return ns

    def test_passes_the_settings_and_answers(self):
        calls = []

        def tool(strips, threshold_db, min_silence, padding, min_keep, close_gaps, update_scene_end):
            calls.append((strips, threshold_db, min_silence, padding, min_keep, close_gaps, update_scene_end))
            return {"status": "ok", "message": "Removed 4 silences", "cuts": 4, "seconds_removed": 14.7,
                    "new_names": {"Cam": ["Cam", "Cam.001"]}, "end_frame": 310, "refused": [], "extra": 1}
        ns = self._toolcode(types.SimpleNamespace(tool_remove_silences=tool))
        p = ns["Params"](strips=("Cam",), threshold_db=-35.0, close_gaps=False)
        out = ns["main"](p)
        self.assertEqual(calls, [(["Cam"], -35.0, 0.4, 0.15, 0.1, False, True)])
        self.assertEqual(out.status, "ok")
        self.assertEqual(out.new_names, {"Cam": ["Cam", "Cam.001"]})
        self.assertEqual(out.end_frame, 310)

    def test_not_loaded(self):
        ns = self._toolcode(None)
        out = ns["main"](ns["Params"]())
        self.assertEqual(out.status, "error")

    def test_signature_matches_params(self):
        tool = (_TOOLS / "remove_silences.py").read_text(encoding="utf-8")
        sig = re.search(r"def remove_silences\((.*?)\) ->", tool, re.S).group(1)
        names = re.findall(r"(\w+):", sig)
        ns = self._toolcode(None)
        self.assertEqual(names, list(ns["Params"]._fields))

    def test_in_the_vse_domain(self):
        spec = importlib.util.spec_from_file_location("td539", _ROOT / "mcp" / "blmcp" / "tool_domains.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn("remove_silences", module.DOMAINS["vse"])
        docs = (_ROOT / "addon" / "bfa_coworker" / "skills" / "mcp_tools.md").read_text(encoding="utf-8")
        self.assertIn("`remove_silences(", docs)

    def test_operator_has_the_option(self):
        seq = W._load_sequencer(W._fake_bpy(False))
        self.assertTrue(seq.SPEC.param("update_scene_end").default)
        self.assertIn("update_scene_end", seq.COWORKER_OT_sequencer_remove_silences.__annotations__)


if __name__ == "__main__":
    unittest.main()
