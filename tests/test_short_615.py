# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Highlights H5, Make Short (#615), the parts that need no Blender.

1. ``short_plan.py``: the name ``Short 01: <title>`` (numbered, cut to 63 bytes, never in a character), the tag,
   the borders (in the pauses: half the pause at most, never on a word, never outside the edit), the layout the
   kind of video suggests, the hook title's frames and text, the output file's name, the report line.
2. ``wand/cut_plan.within`` (Remove Silences' "this frame range only" mode): cuts outside are dropped, cuts that
   cross a border are shortened, touching cuts stay merged.
3. The source: the polish chain keeps to the issue (no deletion, the long edit is compared before and after,
   the picks and the vertical tag are not inherited, one operator with an undo step).

Run with::

    python -m unittest tests.test_short_615 -v
"""

__all__ = ()

import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_wand_remove_silences as W
import test_captions as TC
from test_reframe_copy_saved import _Action, _Scene
from test_sequencer_ui import _reframe

SP = W._import("short_plan")
CP = W._import("wand.cut_plan")
_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


class TestNames(unittest.TestCase):

    def test_the_name_is_numbered_and_has_the_title(self):
        self.assertEqual(SP.short_name(1, "The rig we threw away"), "Short 01: The rig we threw away")
        self.assertEqual(SP.short_name(12, "  two   spaces "), "Short 12: two spaces")
        self.assertEqual(SP.short_name(3, ""), "Short 03: Untitled")

    def test_the_next_number_follows_the_scenes_that_exist(self):
        self.assertEqual(SP.next_number(["Scene", "Scene Vertical"]), 1)
        self.assertEqual(SP.next_number(["Scene", "Short 01: a", "Short 04: b", "Short 02: c"]), 5)
        self.assertEqual(SP.next_number(["My Short 09: x"]), 1)

    def test_a_long_title_is_cut_to_blenders_63_bytes_not_in_the_middle_of_a_letter(self):
        name = SP.short_name(1, "Zeit" + "\u00fc" * 80)
        self.assertLessEqual(len(name.encode("utf-8")), SP.ID_BYTES)
        name.encode("utf-8").decode("utf-8")
        self.assertTrue(SP.short_name(2, "x" * 200).startswith("Short 02: xxx"))
        self.assertEqual(len(SP.short_name(2, "x" * 200)), 63)

    def test_the_tag_says_where_the_short_came_from(self):
        pick = {"id": "ab12", "title": "T", "first_word": {"media": "m", "i": 3}, "last_word": {"media": "m", "i": 90}}
        got = json.loads(SP.tag("Scene", pick))
        self.assertEqual((got["scene"], got["first_word"]["i"], got["last_word"]["i"], got["id"]), ("Scene", 3, 90, "ab12"))
        self.assertNotIn("sig", got)


class TestBorders(unittest.TestCase):
    FPS = 24.0

    def test_the_borders_sit_in_the_pauses_never_on_a_word(self):
        # the sentence before ends at 100 (30 frames of pause: the lead of 8 frames fits), the pick is 130..400,
        # the next one starts at 420 (20 frames: half of it, 10, is less than the tail of 12)
        first, last = SP.borders(130, 400, 100, 420, self.FPS, 1, 3000)
        self.assertEqual((first, last), (122, 409))
        self.assertGreater(first, 100)
        self.assertLess(last, 420)

    def test_a_long_pause_gives_a_short_lead_and_tail(self):
        first, last = SP.borders(1000, 1500, 100, 3000, self.FPS, 1, 4000)
        self.assertEqual(first, 1000 - round(SP.LEAD_SECONDS * self.FPS))
        self.assertEqual(last, 1500 - 1 + round(SP.TAIL_SECONDS * self.FPS))

    def test_no_pause_means_the_border_is_on_the_sentence_and_not_inside_the_neighbour(self):
        self.assertEqual(SP.borders(130, 400, 130, 400, self.FPS, 1, 3000), (130, 399))

    def test_at_the_ends_of_the_edit_the_borders_stay_inside_it(self):
        self.assertEqual(SP.borders(3, 400, None, None, self.FPS, 1, 405)[0], 1)
        self.assertEqual(SP.borders(3, 400, None, None, self.FPS, 1, 405)[1], 405)

    def test_the_range_is_never_empty(self):
        first, last = SP.borders(50, 51, None, None, self.FPS, 1, 3000)
        self.assertGreaterEqual(last, first)

    def test_neighbours_come_from_the_sentences_as_they_play_now(self):
        sents = [{"start": 10, "end": 60}, {"start": 90, "end": 200}, {"start": 230, "end": 300}, {"start": 330, "end": 400}]
        self.assertEqual(SP.neighbours(sents, 90, 300), (60, 330))
        self.assertEqual(SP.neighbours(sents, 10, 60), (None, 90))
        self.assertEqual(SP.neighbours(sents, 330, 400), (300, None))


class TestPolishSettings(unittest.TestCase):

    def test_a_tutorial_keeps_the_whole_screen_a_talker_is_cropped(self):
        self.assertEqual(SP.layout_for("tutorial"), "BLUR_FILL")
        for kind in ("podcast", "talk", "auto", ""):
            self.assertEqual(SP.layout_for(kind), "CROP")

    def test_the_silence_settings_are_tighter_than_the_wands_default(self):
        threshold, min_silence, padding, min_keep = SP.SILENCES
        self.assertLess(min_silence, 0.4)
        self.assertLess(threshold, -10)
        self.assertGreater(padding, 0)
        self.assertGreater(min_keep, 0)

    def test_the_hook_covers_the_first_three_seconds_or_all_of_a_short_one(self):
        self.assertEqual(SP.hook_frames(24, 100, 900), (100, 172))
        self.assertEqual(SP.hook_frames(24, 100, 120), (100, 121))

    def test_the_hook_text_is_the_hook_else_the_title_and_is_cut(self):
        self.assertEqual(SP.hook_text({"hook": "We deleted it.", "title": "T"}), "We deleted it.")
        self.assertEqual(SP.hook_text({"hook": "", "title": "The title"}), "The title")
        self.assertEqual(SP.hook_text({}), "")
        long = SP.hook_text({"hook": "word " * 60})
        self.assertLessEqual(len(long), SP.HOOK_CHARS + 3)
        self.assertTrue(long.endswith("..."))


class TestOutput(unittest.TestCase):

    def test_the_file_is_named_after_the_short_and_safe_for_every_system(self):
        stem = SP.file_stem("Short 01: Why we dropped the \"old\" rig? / part 2")
        self.assertFalse(re.search(r'[:/\\"?*<>|]', stem))
        self.assertTrue(stem.startswith("Short 01 Why we dropped"))
        self.assertEqual(SP.file_stem("???"), "Short")

    def test_the_path_is_next_to_the_blend_and_an_mp4(self):
        path = SP.output_path("D:/work", "Short 01: A")
        self.assertTrue(path.replace("\\", "/").endswith("D:/work/Short 01 A.mp4"))

    def test_one_report_line_per_step(self):
        self.assertEqual(SP.step_line("Make Vertical", "done", "9:16"), "Make Vertical: done, 9:16")
        self.assertEqual(SP.step_line("Auto Captions", "skipped"), "Auto Captions: skipped")


class TestWithin(unittest.TestCase):

    def test_cuts_outside_the_range_are_dropped_and_crossing_ones_shortened(self):
        cuts = [(10, 20), (90, 120), (150, 160), (190, 260), (300, 310)]
        self.assertEqual(CP.within(cuts, 100, 200), [(100, 120), (150, 160), (190, 200)])

    def test_nothing_in_the_range_gives_nothing(self):
        self.assertEqual(CP.within([(1, 5), (50, 60)], 10, 40), [])
        self.assertEqual(CP.within([], 1, 10), [])

    def test_a_cut_touching_the_border_from_outside_is_not_a_cut(self):
        self.assertEqual(CP.within([(0, 100)], 100, 200), [])


class TestTheRangeFitsTheScene(unittest.TestCase):
    """The picks come from the strips, which may reach past the scene's range: refuse, don't cut off."""

    def test_borders_alone_would_clamp_silently_so_the_check_comes_first(self):
        self.assertEqual(SP.borders(500, 700, None, None, 24.0, 1, 250), (492, 492))      # the 1-frame short
        self.assertEqual(SP.borders(200, 400, None, None, 24.0, 1, 250), (192, 250))      # cut off
        self.assertTrue(SP.range_problem(500, 700, 1, 250))
        self.assertTrue(SP.range_problem(200, 400, 1, 250))

    def test_a_pick_past_the_end_is_refused_with_a_way_out(self):
        text = SP.range_problem(500, 700, 1, 250)
        self.assertIn("outside the scene's range", text)
        self.assertIn("Move the Start / End frame", text)

    def test_a_pick_that_crosses_the_end_is_refused(self):
        text = SP.range_problem(200, 400, 1, 250)
        self.assertIn("past the scene's End frame (250)", text)
        self.assertIn("Extend the End frame", text)

    def test_a_pick_before_the_start_is_refused(self):
        self.assertIn("before the scene's Start frame", SP.range_problem(5, 100, 50, 250))
        self.assertTrue(SP.range_problem(1, 40, 50, 250))

    def test_a_pick_inside_the_range_is_fine_also_when_it_ends_on_the_last_frame(self):
        self.assertEqual(SP.range_problem(100, 251, 1, 250), "")
        self.assertEqual(SP.range_problem(1, 251, 1, 250), "")
        self.assertEqual(SP.range_problem(100, 200, 1, 250), "")


class TestWithinMerges(unittest.TestCase):

    def test_touching_and_overlapping_cuts_become_one(self):
        self.assertEqual(CP.within([(10, 20), (20, 30), (28, 40), (60, 70)], 0, 100), [(10, 40), (60, 70)])

    def test_merged_before_clipping(self):
        self.assertEqual(CP.within([(90, 110), (110, 130)], 100, 120), [(100, 120)])


class TestRemoveSilencesInARange(unittest.TestCase):
    """``wand.sequencer.remove_silences(frame_range=...)`` with the sound reading stood in (no sound, no Blender):
    what reaches ``apply_cuts`` is only what lies inside the range."""

    def _run(self, cuts, frame_range):
        seq = W._load_sequencer(W._fake_bpy(False))
        sound = SimpleNamespace(name="talk", select=False)
        scene = SimpleNamespace(name="Scene", sequence_editor=SimpleNamespace())
        got = {}

        def fake_apply(scn, applied, ripple, update_end, what):
            got["cuts"] = list(applied)
            return {"status": "ok", "cuts": len(applied), "seconds_removed": 1.0, "captions_dropped": [],
                    "scene_end_changed": False, "frames_removed": sum(b - a for a, b in applied)}

        with mock.patch.multiple(seq, _level=lambda ed: ([sound], None), _listen=lambda every, chosen: [sound],
                                 _analyse=lambda *a: ([{"name": "talk"}], [], []), _overhang=lambda *a: None,
                                 _common=lambda *a: (cuts, []), apply_cuts=fake_apply):
            out = seq.remove_silences(scene, (-40.0, 0.3, 0.1, 0.2), ["talk"], frame_range=frame_range)
        return out, got

    def test_only_the_cuts_inside_the_range_are_applied(self):
        out, got = self._run([(10, 20), (90, 120), (150, 160), (190, 260), (300, 310)], (100, 200))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(got["cuts"], [(100, 120), (150, 160), (190, 200)])

    def test_no_range_means_all_cuts(self):
        _out, got = self._run([(10, 20), (300, 310)], None)
        self.assertEqual(got["cuts"], [(10, 20), (300, 310)])

    def test_nothing_in_the_range_is_nothing_and_changes_nothing(self):
        out, got = self._run([(10, 20), (300, 310)], (100, 200))
        self.assertEqual(out["status"], "nothing")
        self.assertNotIn("cuts", got)


class TestCaptionsInARange(TC._OpsCase):

    def test_the_captions_are_clipped_to_the_range(self):
        clip = TC._clip(self.scene)                                   # 1..3001, the whole talk
        self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript, frame_range=(1, 1 + 10 * 24))
        lines = self.lines()
        self.assertEqual(len(lines), 3)                              # the first 10 s, as with a 10 s strip
        self.assertTrue(all(s.right_handle <= 1 + 10 * 24 for s in lines))
        self.assertEqual(clip.right_handle, 3001)                    # the strip itself is not touched

    def test_a_range_without_the_strip_gives_nothing_to_caption(self):
        clip = TC._clip(self.scene)
        with self.assertRaises(self.ops.CaptionsError):
            self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript, frame_range=(5000, 6000))
        self.assertEqual(self.lines(), [])


class TestCopySceneNameAndTags(unittest.TestCase):

    def test_copy_scene_has_its_own_name_and_tags(self):
        rf = _reframe()
        short = rf.copy_scene(_Scene("Long", _Action("a")), "Short 01: Hi", {"a": "b", "c": 2})
        self.assertEqual(short.name, "Short 01: Hi")
        self.assertEqual((short.get("a"), short.get("c")), ("b", 2))
        self.assertTrue(short.use_extra_user)

    def test_make_verticals_copy_is_still_called_vertical_and_tagged(self):
        rf = _reframe()
        copy = rf._copy_scene(_Scene("Long", _Action("a")))             # pylint: disable=protected-access
        self.assertEqual(copy.name, "Long Vertical")
        self.assertEqual(copy.get(rf.VERTICAL_TAG), "Long")


class TestBuildFailureRemovesTheCopy(unittest.TestCase):
    """``build()`` with the Blender parts stood in: a step that raises takes the half-made copy away."""

    @staticmethod
    def _module(removed):
        pkg = "bfacw_short615_pkg"
        root = types.ModuleType(pkg)
        root.__path__ = [str(W._ADDON)]
        stubs = {
            "bpy": types.ModuleType("bpy"),
            pkg: root,
            pkg + ".highlight_picks": SimpleNamespace(PROP="coworker_picks", clock=lambda f, fps: "0:00"),
            pkg + ".highlight_picks_ops": SimpleNamespace(edit_signature=lambda scene: ("sig",), load=lambda scene: {},
                                                           _redraw=lambda: None),
            pkg + ".reframe": SimpleNamespace(VERTICAL_TAG="vertical", _SHOWN_BEFORE={}, ReframeError=RuntimeError,
                                              copy_scene=lambda scene, name, tags: _FakeShort(name)),
            pkg + ".scene_reader_ops": SimpleNamespace(read=lambda scene: {"sentences": []}),
            pkg + ".wand.sequencer": SimpleNamespace(remove_silences=None),
            pkg + ".wand": SimpleNamespace(sequencer=None),
        }
        stubs[pkg + ".wand"].sequencer = stubs[pkg + ".wand.sequencer"]
        
        class _Scenes(list):
            remove = removed.append

        stubs["bpy"].data = SimpleNamespace(scenes=_Scenes(), filepath="")
        stubs["bpy"].context = SimpleNamespace()
        stubs[pkg + ".wand.sequencer"].remove_silences = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        return pkg, stubs

    def test_a_failing_step_removes_the_half_made_copy_and_raises(self):
        removed = []
        pkg, stubs = self._module(removed)
        with mock.patch.dict(sys.modules, stubs):
            sys.modules.pop(pkg + ".highlight_short", None)
            hs = importlib.import_module(pkg + ".highlight_short")
            sound = SimpleNamespace(type="SOUND", mute=False, right_handle=300, left_handle=1, name="talk")
            scene = _FakeScene(sound)
            pick = {"start": 50, "end": 200, "title": "T"}
            with mock.patch.object(hs, "_fps", lambda s: 24.0):
                with self.assertRaises(RuntimeError):
                    hs.build(scene, pick, {"silences": True})
            self.assertEqual(len(removed), 1)
            self.assertTrue(removed[0].name.startswith("Short 01"))
            sys.modules.pop(pkg + ".highlight_short", None)

    def test_a_pick_past_the_range_is_refused_before_any_copy_is_made(self):
        removed, made = [], []
        pkg, stubs = self._module(removed)
        stubs[pkg + ".reframe"].copy_scene = lambda *a: made.append(a)
        with mock.patch.dict(sys.modules, stubs):
            sys.modules.pop(pkg + ".highlight_short", None)
            hs = importlib.import_module(pkg + ".highlight_short")
            scene = _FakeScene(None)
            with self.assertRaises(hs.ShortError) as ctx:
                hs.build(scene, {"start": 500, "end": 700, "title": "T"}, {})
            self.assertIn("scene's range", str(ctx.exception))
            self.assertEqual((made, removed), ([], []))
            sys.modules.pop(pkg + ".highlight_short", None)


class _FakeShort:
    def __init__(self, name):
        self.name = name
        self.props = {}
        self.timeline_markers = []
        self.use_preview_range = True
        self.frame_start = self.frame_end = self.frame_current = 0
        self.render = SimpleNamespace(fps=24, fps_base=1.0)
        self.sequence_editor = SimpleNamespace(strips=[SimpleNamespace(type="SOUND", mute=False, right_handle=300,
                                                                       left_handle=1, name="talk", channel=1)])

    def __contains__(self, key):
        return key in self.props


class _FakeScene:
    def __init__(self, sound):
        self.name = "Long"
        self.frame_start, self.frame_end, self.frame_current = 1, 250, 1
        self.render = SimpleNamespace(fps=24, fps_base=1.0)
        self.sequence_editor = SimpleNamespace(meta_stack=[], strips=[sound] if sound else [])


class TestTheSourceKeepsTheIssue(unittest.TestCase):
    """What the Blender side must do, read from its source (it is run live: the PR's by-hand checks)."""

    @classmethod
    def setUpClass(cls):
        cls.src = (_ADDON / "highlight_short.py").read_text(encoding="utf-8")
        cls.ops = (_ADDON / "highlight_picks_ops.py").read_text(encoding="utf-8")

    def test_the_short_is_a_scene_copy_with_its_range_set_and_nothing_is_deleted(self):
        self.assertIn("reframe.copy_scene(", self.src)
        self.assertIn("short.frame_start, short.frame_end", self.src)
        for call in (".strips.remove(", "bpy.ops.sequencer.delete", "bpy.ops.sequencer.split"):
            self.assertNotIn(call, self.src)

    def test_remove_silences_shows_progress_while_it_reads_the_sound(self):
        self.assertIn("context=bpy.context", self.src)
        self.assertIn("report_progress=True", self.src)
        self.assertNotIn("report_progress=False", self.src)

    def test_the_picks_and_the_vertical_tag_are_not_inherited(self):
        self.assertIn("(hp.PROP, reframe.VERTICAL_TAG)", self.src)

    def test_the_chain_uses_the_existing_tools_inside_the_short(self):
        self.assertIn("frame_range=(first, last + 1)", self.src)          # silences and captions: this range only
        self.assertIn("in_copy=False", self.src)                           # Make Vertical: not a second copy
        self.assertEqual(len(re.findall(r"add_captions\(", self.src)), 1)

    def test_no_step_pushes_an_undo_of_its_own_the_operator_has_the_one_step(self):
        self.assertNotIn("undo_push", self.src)
        self.assertNotIn("tool_add_captions", self.src)
        make = self.ops[self.ops.index("class BFACW_OT_highlights_make_short"):]
        self.assertIn("'UNDO'", make.split("class BFACW_OT_highlights_dismiss")[0])

    def test_every_step_is_an_option_that_is_on_by_default(self):
        make = self.ops[self.ops.index("class BFACW_OT_highlights_make_short"):].split("def invoke")[0]
        for name in ("silences", "vertical", "captions", "hook"):
            self.assertRegex(make, name + r": BoolProperty\([^)]*default=True")


if __name__ == "__main__":
    unittest.main()
