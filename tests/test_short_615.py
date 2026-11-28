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

import json
import re
import unittest
from pathlib import Path

import test_wand_remove_silences as W

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
