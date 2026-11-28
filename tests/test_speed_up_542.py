# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #542 Speed Up: Remove Silences plays the quiet parts faster instead of cutting them
(made-up data only).

1. ``wand/cut_plan.py`` (bpy-free): :func:`sped_length`, :class:`SpeedMap` (frames inside a sped range land
   in proportion, after it they move left by the time saved), the plan with a mapper (captions trimmed in
   proportion, a locked strip after the range refuses).
2. ``wand/sequencer.py`` on the stand-in strip model of ``test_remove_silences_whole_edit``: picture, sound
   and the other tracks stay in step; lengths agree on every track; later strips move left by the time
   saved; names stay; captions are trimmed; the scene end follows; a locked strip, an effect strip and a
   strip that cannot be played faster refuse before anything changes; no Sequencer window is an error before
   anything changes; Cut (the default) is unchanged.
3. The retiming calls (``_retime_pieces``) against a stand-in ``bpy.ops``: only the piece is selected, a key is
   added at its start, the speed is the factor in percent, the length is set so all tracks agree, the
   selection and the active strip come back, and an area switched to a Sequencer for the call is put back.
4. The operator's properties, the tool's checks and its tool-code.

Run with::

    python -m unittest discover -s tests -p "test_speed_up_542.py" -v
"""

__all__ = ()

import contextlib
import sys
import types
import unittest

import test_remove_silences_whole_edit as T
import test_wand_remove_silences as W

P = T.P
FPS = T.FPS
FakeEditor = T.FakeEditor
SETTINGS = T.SETTINGS


# -- 1. the map and the plan --------------------------------------------------------------

class TestSpeedMap(unittest.TestCase):

    def test_sped_length_rounds_to_the_nearest_frame_never_below_one(self):
        self.assertEqual([P.sped_length(n, 4) for n in (100, 103, 13, 7, 3, 2, 1, 0)], [25, 26, 3, 2, 1, 1, 1, 0])
        self.assertEqual(P.sped_length(101, 2), 51)
        self.assertEqual(P.sped_length(13, 8), 2)
        self.assertEqual(P.sped_length(5, 1), 5)

    def test_frames_before_inside_and_after_a_range(self):
        m = P.SpeedMap([(100, 200)], [25])
        self.assertEqual(m.saved, 75)
        self.assertEqual([m.map(f) for f in (0, 99, 100)], [0, 99, 100])
        self.assertEqual(m.map(150), 112.5)             # halfway through the range, halfway through its new length
        self.assertEqual(m.map(200), 125)
        self.assertEqual(m.map(300), 225)

    def test_two_ranges_add_up(self):
        m = P.SpeedMap([(300, 340), (100, 200)], [10, 25])      # given out of order
        self.assertEqual(m.ranges, [(100, 200), (300, 340)])
        self.assertEqual(m.saved, 75 + 30)
        self.assertEqual(m.map(250), 175)
        self.assertEqual(m.map(300), 225)
        self.assertEqual(m.map(340), 235)
        self.assertEqual(m.map(400), 295)

    def test_the_map_never_goes_backwards(self):
        m = P.SpeedMap([(10, 50), (60, 61), (100, 220)], [10, 1, 30])
        last = -1.0
        for f in range(0, 400):
            self.assertGreaterEqual(m.map(f), last)
            last = m.map(f)

    def test_touching_is_clipped(self):
        m = P.SpeedMap([(100, 200)], [25])
        self.assertEqual(m.touching(150, 300), [(150, 200)])


def _s(name, left, right, kind="media", **kw):
    return dict({"name": name, "left": left, "right": right, "kind": kind, "locked": False, "refuse": ""}, **kw)


class TestPlanWithAMapper(unittest.TestCase):

    def test_a_caption_over_a_range_is_trimmed_in_proportion(self):
        m = P.SpeedMap([(100, 200)], [25])
        out = P.plan([_s("cap", 50, 250, kind="text")], [(100, 200)], True, 30, mapper=m)
        self.assertEqual(out["trim"], {"cap": (50, 175)})
        self.assertEqual(out["drop"], [])

    def test_a_caption_inside_a_range_keeps_its_share(self):
        m = P.SpeedMap([(100, 300)], [50])
        out = P.plan([_s("cap", 120, 220, kind="text")], [(100, 300)], True, 30, mapper=m)
        self.assertEqual(out["trim"], {"cap": (105, 130)})      # 100 frames became 25 (0.83 s): readable

    def test_a_caption_that_gets_too_short_to_read_goes_with_its_words(self):
        m = P.SpeedMap([(100, 300)], [25])
        strips = [_s("cap", 120, 180, kind="text"), _s("cap_w1", 120, 150, kind="text", line="cap")]
        out = P.plan(strips, [(100, 300)], True, 30, mapper=m)
        self.assertEqual(sorted(out["drop"]), ["cap", "cap_w1"])

    def test_without_a_mapper_nothing_changes(self):
        out = P.plan([_s("cap", 0, 60, kind="text")], [(10, 20), (50, 70)], fps=30)
        self.assertEqual(out["trim"], {"cap": (0, 40)})


# -- 2. the edit ---------------------------------------------------------------------------

class _Speed(T._Pipeline):

    def setUp(self):
        super().setUp()
        self.real_problem = self.seq._retime_problem
        self.seq._retime_problem = lambda: ""
        self.retimed = []

        def retime(scene, jobs, factor):
            for strip, frames in jobs:
                self.retimed.append((strip.name, strip.type, strip.left_handle, strip.right_handle, frames, factor))
                strip.__dict__["_right"] = strip.left_handle + frames
                strip.retiming_keys = [1, strip.left_handle, strip.left_handle + frames, 9999]
        self.seq._retime_pieces = retime

    def run_speed(self, scene, names, factor=4, update_end=True):
        return self.seq.remove_silences(scene, SETTINGS, names, True, update_end, None, False, mode="SPEED", speed=factor)

    def two(self):
        ed = FakeEditor()
        snd = ed.add("Voice", "SOUND", 1, 1, 301, "v.wav")
        cam = ed.add("Cam", "MOVIE", 2, 1, 301, "cam.mp4")
        title = ed.add("Title", "COLOR", 3, 1, 301)
        music = ed.add("Music", "SOUND", 4, 1, 301, "m.wav")
        tail = ed.add("Outro", "COLOR", 5, 301, 361)
        self.speech = {"Voice": ([(0.8, 3.0), (5.0, 6.0), (8.0, 9.3)], 10.0)}
        return ed, snd, cam, title, music, tail


class TestSpeedUpTheEdit(_Speed):

    def test_the_quiet_parts_play_faster_and_every_track_agrees(self):
        ed, *_ = self.two()
        sc = T._scene(ed, 360)
        out = self.run_speed(sc, ["Voice"])
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["mode"], "SPEED")
        self.assertEqual(out["speed"], 4.0)
        ranges = out["cut_ranges"]
        self.assertGreater(len(ranges), 1)
        # the picture and the voice end where each other do, in every piece
        for path_a, path_b in (("v.wav", "cam.mp4"), ("v.wav", "m.wav")):
            a = sorted((s._left, s._right) for s in ed.items if s.filepath == path_a)
            b = sorted((s._left, s._right) for s in ed.items if s.filepath == path_b)
            self.assertEqual(a, b)
        # the saved time is what the scene lost
        saved = sum(b - a - P.sped_length(b - a, 4) for a, b in ranges)
        self.assertEqual(out["frames_removed"], saved)
        self.assertEqual(max(s._right for s in ed.items), 361 - saved)
        self.assertEqual(sc.frame_end, 360 - saved)
        self.assertEqual(out["seconds_removed"], round(saved / FPS, 2))
        # the pieces inside the ranges were retimed, the speech was not
        got = sorted((n, l, r, f) for n, t, l, r, f, k in self.retimed if t == "MOVIE")
        self.assertEqual(len(got), len(ranges))
        self.assertTrue(all(f == P.sped_length(r - l, 4) for _n, l, r, f in got))
        self.assertEqual(self.retimed[0][5], 4)
        self.assertEqual(ed.shuffled, 0)

    def test_what_follows_moves_left_by_the_time_saved(self):
        ed, *_ = self.two()
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        saved = out["frames_removed"]
        outro = next(s for s in ed.items if s.name == "Outro")
        self.assertEqual((outro._left, outro._right), (301 - saved, 361 - saved))

    def test_the_speech_is_untouched_in_time(self):
        ed, *_ = self.two()
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["status"], "ok")
        # a speech piece plays the same seconds of the file as before: its frames minus its content start
        speech = [s for s in ed.items if s.filepath == "v.wav" and s.retiming_keys == []]
        self.assertGreaterEqual(len(speech), 3)
        for s in speech:
            at = (s._left - s._cs) / FPS
            self.assertTrue(any(a - 0.2 <= at <= b for a, b in [(0.6, 3.2), (4.8, 6.2), (7.8, 9.5)]), (s.name, at))

    def test_names_stay_on_the_first_piece(self):
        ed, *_ = self.two()
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["new_names"]["Cam"][:2], ["Cam", "Cam.001"])
        pieces = sorted((s._left, s.name) for s in ed.items if s.filepath == "cam.mp4")
        self.assertEqual([n for _l, n in pieces], out["new_names"]["Cam"])

    def test_a_colour_strip_over_a_silence_is_shortened_not_retimed(self):
        ed, *_ = self.two()
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(sum(s._right - s._left for s in ed.items if s.name.startswith("Title")), 300 - out["frames_removed"])
        self.assertNotIn("COLOR", {t for _n, t, *_rest in self.retimed})

    def test_captions_are_trimmed_in_proportion(self):
        ed, *_ = self.two()
        cap = ed.add("cap_0001", "TEXT", 6, 1, 301, text="all through")
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(cap._left, 1)
        self.assertEqual(cap._right, 301 - out["frames_removed"])
        self.assertIn("cap_0001", out["captions_trimmed"])

    def test_a_second_run_finds_nothing_in_what_is_sped_up(self):
        ed, *_ = self.two()
        sc = T._scene(ed, 360)
        self.run_speed(sc, ["Voice"])
        analyse = self.seq._analyse

        def reading(scene, sounds, context=None, report_progress=True):
            kept = [s for s in sounds if len(s.retiming_keys) < 2]
            blocked = [(s.left_handle, s.right_handle) for s in sounds if len(s.retiming_keys) >= 2]
            infos, problems, _ = analyse(scene, kept, context, report_progress)
            return infos, problems, blocked
        self.seq._analyse = reading
        before = sorted((s.name, s._left, s._right) for s in ed.items)
        self.speech = {}
        for s in ed.items:
            if s.type == "SOUND" and s.filepath == "v.wav" and not s.retiming_keys:
                t0 = (s._left - s._cs) / FPS
                t1 = (s._right - s._cs) / FPS
                loud = [(0.8, 3.0), (5.0, 6.0), (8.0, 9.3)]
                self.speech[s.name] = ([(max(0, a - t0), b - t0) for a, b in loud if b > t0 and a < t1], t1 - t0)
        for s in ed.items:
            s.select = s.type == "SOUND" and s.filepath == "v.wav"
        out = self.run_speed(sc, None)
        self.assertEqual(out["status"], "nothing", out)
        self.assertEqual(sorted((s.name, s._left, s._right) for s in ed.items), before)

    def test_a_locked_picture_refuses_and_nothing_changes(self):
        ed, snd, cam, *_ = self.two()
        cam.lock = True
        before = sorted((s.name, s._left, s._right) for s in ed.items)
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["status"], "refused")
        self.assertIn("Nothing was sped up", out["message"])
        self.assertEqual(sorted((s.name, s._left, s._right) for s in ed.items), before)
        self.assertEqual(self.retimed, [])

    def test_a_strip_that_cannot_play_faster_refuses(self):
        ed, *_ = self.two()
        ed.add("Mask", "MASK", 7, 1, 301)
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["refused"][0]["strip"], "Mask")
        self.assertIn("use Cut", out["refused"][0]["reason"])

    def test_an_effect_over_a_silence_refuses(self):
        ed, snd, cam, *_ = self.two()
        fx = ed.add("Fade", "GAMMA_CROSS", 8, 1, 301)
        fx.input_1 = cam
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["refused"][0]["strip"], "Fade")

    def test_a_strip_already_retimed_refuses(self):
        ed, snd, cam, *_ = self.two()
        cam.retiming_keys = [1, 50, 100, 301]
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["status"], "refused")
        self.assertIn("retimed", out["refused"][0]["reason"])

    def test_no_sequencer_window_is_an_error_before_anything_changes(self):
        ed, *_ = self.two()
        self.seq._retime_problem = self.real_problem
        self.seq.bpy.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[]))
        before = sorted((s.name, s._left, s._right) for s in ed.items)
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["status"], "error")
        self.assertIn("window", out["message"])
        self.assertEqual(sorted((s.name, s._left, s._right) for s in ed.items), before)

    def test_the_scene_end_stays_when_asked(self):
        ed, *_ = self.two()
        sc = T._scene(ed, 360)
        self.run_speed(sc, ["Voice"], update_end=False)
        self.assertEqual(sc.frame_end, 360)

    def test_many_retimed_sounds_are_one_problem_line_not_dozens(self):
        self.assertEqual(self.seq._retimed_notes([]), [])
        self.assertEqual(len(self.seq._retimed_notes(["A", "B"])), 2)
        self.assertIn("“A” is retimed", self.seq._retimed_notes(["A"])[0])
        many = self.seq._retimed_notes(["S{:d}".format(i) for i in range(35)])
        self.assertEqual(len(many), 1)
        self.assertIn("35 sound strips are retimed", many[0])

    def test_the_report_line_says_what_it_did(self):
        ed, *_ = self.two()
        out = self.run_speed(T._scene(ed, 360), ["Voice"], factor=8)
        self.assertRegex(out["message"], r"^Sped up \d+ silences? 8x \(.* now plays in .*, .* saved\)")
        self.assertEqual(self.retimed[0][5], 8)

    def test_cut_is_still_the_default(self):
        ed, *_ = self.two()
        out = self.seq.remove_silences(T._scene(ed, 360), SETTINGS, ["Voice"], True, True, None, False)
        self.assertEqual(out["status"], "ok")
        self.assertNotIn("mode", out)
        self.assertEqual(self.retimed, [])
        self.assertTrue(out["message"].startswith("Removed "))

    def test_nothing_quiet_says_speed_up(self):
        ed, *_ = self.two()
        self.speech = {"Voice": ([(0.0, 10.0)], 10.0)}
        out = self.run_speed(T._scene(ed, 360), ["Voice"])
        self.assertEqual(out["status"], "nothing")
        self.assertIn("speed up", out["message"])

    def test_an_hour_of_strips_plans_and_applies_quickly(self):
        import time
        ed = FakeEditor()
        ed.add("Voice", "SOUND", 1, 1, 108001, "v.wav")
        ed.add("Cam", "MOVIE", 2, 1, 108001, "cam.mp4")
        loud = [(i * 6.0, i * 6.0 + 3.0) for i in range(600)]
        self.speech = {"Voice": (loud, 3600.0)}
        started = time.monotonic()
        out = self.seq.remove_silences(T._scene(ed, 108000), SETTINGS, ["Voice"], True, True, None, False, mode="SPEED", speed=4)
        self.assertEqual(out["status"], "ok", out)
        self.assertLess(time.monotonic() - started, 20.0)
        self.assertEqual(out["cuts"], 600)


# -- 3. the retiming calls -----------------------------------------------------------------

class _Keys(list):
    def add(self, timeline_frame):
        self.append(timeline_frame)


class TestRetimeCalls(unittest.TestCase):

    def setUp(self):
        self.bpy = W._fake_bpy(False)
        self.seq = W._load_sequencer(self.bpy)
        self.log = []
        self.ed = FakeEditor()
        self.ed.active_strip = None
        self.a = self.ed.add("A", "MOVIE", 1, 1, 101, "a.mp4")
        self.b = self.ed.add("B", "SOUND", 2, 101, 301, "b.wav")
        self.c = self.ed.add("C", "MOVIE", 3, 1, 301, "c.mp4")
        for s in (self.a, self.b, self.c):
            s.retiming_keys = _Keys()
        self.scene = T._scene(self.ed, 300)
        self.area = types.SimpleNamespace(type="SEQUENCE_EDITOR", regions=[types.SimpleNamespace(type="WINDOW")],
                                          width=100, height=100)
        self.workspace = types.SimpleNamespace(sequencer_scene=self.scene)
        self.win = types.SimpleNamespace(screen=types.SimpleNamespace(areas=[self.area]), workspace=self.workspace)

        @contextlib.contextmanager
        def override(**kw):
            self.log.append(("override", sorted(kw)))
            yield
            self.log.append(("override_end",))

        def speed_set(speed):
            sel = sorted(s.name for s in self.ed.items if s.select)
            self.log.append(("speed", speed, sel, self.ed.active_strip.name))
            return {"FINISHED"}
        self.bpy.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[self.win]),
                                                 temp_override=override)
        self.bpy.ops = types.SimpleNamespace(sequencer=types.SimpleNamespace(retiming_segment_speed_set=speed_set))

    def test_each_piece_is_selected_alone_keyed_and_given_the_speed(self):
        self.a.select = self.c.select = True
        self.ed.active_strip = self.c
        self.seq._retime_pieces(self.scene, [(self.a, 25), (self.b, 50)], 4.0)
        self.assertEqual([e for e in self.log if e[0] == "speed"],
                         [("speed", 400.0, ["A"], "A"), ("speed", 400.0, ["B"], "B")])
        self.assertEqual(self.a.retiming_keys, [1])
        self.assertEqual(self.b.retiming_keys, [101])
        self.assertEqual(self.log[0][0], "override")
        self.assertEqual(self.log[-1], ("override_end",))

    def test_the_length_is_set_so_every_track_agrees(self):
        self.seq._retime_pieces(self.scene, [(self.a, 25)], 4.0)
        self.assertEqual(self.a.right_handle, 26)

    def test_the_selection_and_the_active_strip_come_back(self):
        self.a.select = self.c.select = True
        self.ed.active_strip = self.c
        self.seq._retime_pieces(self.scene, [(self.a, 25), (self.b, 50)], 2.0)
        self.assertEqual([s.select for s in (self.a, self.b, self.c)], [True, False, True])
        self.assertIs(self.ed.active_strip, self.c)

    def test_a_one_frame_piece_is_left_alone(self):
        self.seq._retime_pieces(self.scene, [(self.a, 100)], 4.0)
        self.assertEqual([e for e in self.log if e[0] == "speed"], [])

    def test_a_failed_retiming_raises(self):
        self.bpy.ops.sequencer.retiming_segment_speed_set = lambda speed: {"CANCELLED"}
        with self.assertRaises(RuntimeError):
            self.seq._retime_pieces(self.scene, [(self.a, 25)], 4.0)

    def test_the_scene_is_shown_for_the_call_and_put_back(self):
        other = types.SimpleNamespace(name="Other")
        self.workspace.sequencer_scene = other
        shown = []
        self.bpy.ops.sequencer.retiming_segment_speed_set = lambda speed: shown.append(self.workspace.sequencer_scene) or {"FINISHED"}
        self.seq._retime_pieces(self.scene, [(self.a, 25)], 4.0)
        self.assertEqual(shown, [self.scene])
        self.assertIs(self.workspace.sequencer_scene, other)

    def test_an_area_switched_to_a_sequencer_is_put_back(self):
        self.area.type = "VIEW_3D"
        seen = []
        self.bpy.ops.sequencer.retiming_segment_speed_set = lambda speed: seen.append(self.area.type) or {"FINISHED"}
        self.seq._retime_pieces(self.scene, [(self.a, 25)], 4.0)
        self.assertEqual(seen, ["SEQUENCE_EDITOR"])
        self.assertEqual(self.area.type, "VIEW_3D")

    def test_the_problem_is_named_when_there_is_no_window(self):
        self.assertEqual(self.seq._retime_problem(), "")
        self.bpy.context.window_manager.windows = []
        self.assertIn("window", self.seq._retime_problem())


# -- 4. the operator and the tool ------------------------------------------------------------

class TestOperatorAndTool(unittest.TestCase):

    def test_the_operator_has_the_mode_and_the_speed(self):
        seq = W._load_sequencer(W._fake_bpy(False))
        ann = seq.COWORKER_OT_sequencer_remove_silences.__annotations__
        self.assertIn("mode", ann)
        self.assertIn("speed", ann)
        self.assertIn("default='CUT'", ann["mode"])         # annotations are text here (from __future__)
        self.assertIn("default=str(DEFAULT_SPEED)", ann["speed"])
        self.assertEqual(seq.DEFAULT_SPEED, 4)
        self.assertEqual([i[0] for i in seq.SPEEDS], ["2", "4", "8"])

    def test_every_mode_and_speed_has_a_tooltip_and_an_icon(self):
        seq = W._load_sequencer(W._fake_bpy(False))
        for row in seq.MODES + tuple(r[:4] for r in seq.SPEEDS):
            self.assertEqual(len(row), 4, row)
            self.assertTrue(row[2] and row[3].isupper(), row)

    def test_normal_mode(self):
        seq = W._load_sequencer(W._fake_bpy(False))
        for text in ("speed_up", "Speed Up", "SPEED", "speed-up", "speedup"):
            self.assertEqual(seq.normal_mode(text), "SPEED", text)
        for text in ("cut", "", None, "anything"):
            self.assertEqual(seq.normal_mode(text), "CUT", text)

    def test_the_tool_checks_mode_and_speed(self):
        seq = W._load_sequencer(W._fake_bpy(False))
        bad = seq.tool_remove_silences(None, mode="slow")
        self.assertEqual(bad["status"], "error")
        self.assertIn("speed_up", bad["message"])
        bad = seq.tool_remove_silences(None, mode="speed_up", speed=3)
        self.assertEqual(bad["status"], "error")
        self.assertIn("2, 4 or 8", bad["message"])

    def test_the_tool_code_passes_mode_and_speed_on(self):
        calls = []

        def tool(*args):
            calls.append(args)
            return {"status": "ok", "message": "Sped up 3 silences 8x", "cuts": 3, "mode": "SPEED", "speed": 8.0,
                    "sped_seconds": 4.5, "seconds_removed": 3.9}
        source = (T._TOOLS / "remove_silences_toolcode.py").read_text(encoding="utf-8")
        ns = {"__name__": "remove_silences_toolcode"}
        exec(compile(source, "remove_silences_toolcode.py", "exec"), ns)  # pylint: disable=exec-used
        key = "probe_pkg.bfa_coworker.wand.sequencer"
        sys.modules[key] = types.SimpleNamespace(tool_remove_silences=tool)
        self.addCleanup(sys.modules.pop, key, None)
        out = ns["main"](ns["Params"](mode="speed_up", speed=8.0))
        self.assertEqual(calls[0][-2:], ("speed_up", 8.0))
        self.assertEqual((out.mode, out.speed, out.sped_seconds), ("SPEED", 8.0, 4.5))


if __name__ == "__main__":
    unittest.main()
