# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Captions in Make Vertical's 9:16 copy (#497): a caption laid out again for another frame
(the style's size, place and line breaks there; split in time where its words no longer
fit), hand-moved captions left alone, and ``captions_ops.relayout_captions`` on the
stand-in ``bpy`` of ``test_captions``.  No Blender.

Run with::

    python -m unittest tests.test_captions_vertical -v
"""

__all__ = ()

import json
import unittest
from types import SimpleNamespace

from test_captions import _SAMPLE, _STATE, _OpsCase, _clip, cap

WIDE, TALL = (1920, 1080), (1080, 1920)
SAFE = cap.safe_area("VERTICAL")


def _sample_captions(style: str, res=WIDE, fps: float = 24.0) -> list[dict]:
    words = json.loads(_SAMPLE.read_text(encoding="utf-8"))["words"]
    caps = cap.build_captions(words, cap.default_max_chars(style, *res), cap.STYLES[style]["max_lines"])
    return cap.to_frames(caps, fps, 1)


def _shown(c: dict, style: str) -> str:
    return c["text"].upper() if cap.STYLES[style]["uppercase"] else c["text"]


def _flat(text: str) -> str:
    return " ".join(text.split())


class TestRelayout(unittest.TestCase):

    def test_a_wide_clean_caption_in_9_16(self):
        text = "Hello, and welcome to this short test of\nthe captions we make for every clip today."
        lay = cap.relayout(text, "CLEAN", *TALL, 100, 220, 42, 2, WIDE)
        props = lay["style"]["props"]
        self.assertEqual(props["font_size"], 60)                        # 0.031 x 1920, the vertical size
        self.assertEqual(props["location"], (0.5, SAFE["bottom"]))      # 0.24, above the platform buttons
        self.assertEqual(props["wrap_width"], SAFE["width"])
        self.assertEqual((lay["chars"], lay["lines"]), (cap.default_max_chars("CLEAN", *TALL), 2))
        pieces = lay["pieces"]
        self.assertGreater(len(pieces), 1)                              # 82 characters: not in 2 x 25
        self.assertEqual(" ".join(_flat(p["text"]) for p in pieces), _flat(text))      # the words stay
        self.assertEqual((pieces[0]["frame_start"], pieces[-1]["frame_end"]), (100, 220))
        for a, b in zip(pieces, pieces[1:]):
            self.assertEqual(a["frame_end"], b["frame_start"])          # no gap, no overlap
        for p in pieces:
            self.assertLessEqual(len(p["lines"]), 2)
            self.assertTrue(all(len(ln) <= lay["chars"] for ln in p["lines"]), p["lines"])
            self.assertLess(p["frame_start"], p["frame_end"])

    def test_one_that_fits_is_only_broken_again(self):
        lay = cap.relayout("We cut the long pauses out of the talk", "CLEAN", *TALL, 10, 60, 42, 2, WIDE)
        self.assertEqual(len(lay["pieces"]), 1)
        piece = lay["pieces"][0]
        self.assertEqual(len(piece["lines"]), 2)                        # 38 characters: two lines of 25
        self.assertTrue(all(len(ln) <= 25 for ln in piece["lines"]))
        self.assertEqual(_flat(piece["text"]), "We cut the long pauses out of the talk")
        self.assertEqual((piece["frame_start"], piece["frame_end"]), (10, 60))

    def test_each_word_keeps_its_time(self):
        words = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen".split()
        frames = [100 + 6 * i for i in range(len(words))]
        lay = cap.relayout(" ".join(words), "CLEAN", *TALL, 100, 190, 42, 2, WIDE, frames)
        pieces = lay["pieces"]
        self.assertEqual(len(pieces), 2)
        second = pieces[1]
        first_word = second["text"].split()[0]
        self.assertEqual(second["frame_start"], frames[words.index(first_word)])
        self.assertEqual([w["frame"] for p in pieces for w in p["words"]], frames)

    def test_without_word_times_they_are_shared_out_by_length(self):
        text = "a fairly long caption that has no word times stored with it at all"
        lay = cap.relayout(text, "CLEAN", *TALL, 0, 100, 42, 2, WIDE, None)
        bounds = [p["frame_start"] for p in lay["pieces"]] + [100]
        self.assertEqual(bounds[0], 0)
        self.assertEqual(bounds, sorted(set(bounds)))
        # A word count that doesn't match the text (edited by hand): the same as none.
        again = cap.relayout(text, "CLEAN", *TALL, 0, 100, 42, 2, WIDE, [0, 5, 9])
        self.assertEqual(again["pieces"], lay["pieces"])

    def test_word_by_word_pieces_are_one_line(self):
        text = "and then the whole timeline is cut to the speech"
        frames = list(range(0, 90, 10))
        lay = cap.relayout(text, "WORD_HIGHLIGHT", *TALL, 0, 95, 42, 1, WIDE, frames)
        self.assertEqual(lay["lines"], 1)
        self.assertEqual(lay["chars"], cap.default_max_chars("WORD_HIGHLIGHT", *TALL))
        self.assertGreater(len(lay["pieces"]), 1)
        for p in lay["pieces"]:
            self.assertEqual(len(p["lines"]), 1)
            self.assertLessEqual(len(p["text"]), lay["chars"])
            self.assertEqual([w["w"] for w in p["words"]], p["text"].split())
            self.assertEqual(p["words"][0]["frame"], p["frame_start"])

    def test_a_sentence_end_is_where_it_splits_first(self):
        lay = cap.relayout("We start the edit now. Then we look at the captions", "CLEAN", *TALL, 0, 120, 42, 2, WIDE)
        self.assertEqual([_flat(p["text"]) for p in lay["pieces"]],
                         ["We start the edit now.", "Then we look at the captions"])

    def test_no_frame_for_a_piece_keeps_one_caption(self):
        text = "a caption much too long for the vertical frame shown for only two frames"
        lay = cap.relayout(text, "CLEAN", *TALL, 50, 51, 42, 2, WIDE)
        self.assertEqual(len(lay["pieces"]), 1)
        self.assertEqual(_flat(lay["pieces"][0]["text"]), text)
        self.assertEqual((lay["pieces"][0]["frame_start"], lay["pieces"][0]["frame_end"]), (50, 51))

    def test_characters_per_line(self):
        fit = cap.default_max_chars("CLEAN", *TALL)
        self.assertEqual(cap.relayout_limits("CLEAN", 42, 2, WIDE, TALL), (fit, 2))     # the style's: the new one's
        self.assertEqual(cap.relayout_limits("CLEAN", 0, 0, None, TALL), (fit, 2))
        self.assertEqual(cap.relayout_limits("CLEAN", 18, 1, WIDE, TALL), (18, 1))      # the artist's, if it fits
        self.assertEqual(cap.relayout_limits("CLEAN", 30, 2, WIDE, TALL), (fit, 2))     # else at most what fits
        self.assertEqual(cap.relayout_limits("WORD_HIGHLIGHT", 42, 2, WIDE, TALL)[1], 1)
        self.assertEqual(cap.relayout_limits("BOLD_POP", 0, 0, WIDE, TALL)[1], 1)

    def test_every_sample_caption_in_every_style_fits_the_9_16_safe_area(self):
        for style in cap.STYLES:
            st = cap.resolve_style(style, *TALL)
            px = st["props"]["font_size"]
            caps = _sample_captions(style)
            total = 0
            for c in caps:
                text = _shown(c, style)
                lay = cap.relayout(text, style, *TALL, c["frame_start"], c["frame_end"],
                                   cap.default_max_chars(style, *WIDE), st["max_lines"], WIDE,
                                   [w["frame"] for w in c["words"]])
                self.assertEqual(" ".join(_flat(p["text"]) for p in lay["pieces"]), _flat(text), style)
                for p in lay["pieces"]:
                    total += 1
                    width, height = cap.text_box(p["lines"], px)
                    self.assertLessEqual(len(p["lines"]), st["max_lines"], (style, p["lines"]))
                    self.assertLessEqual(width, SAFE["width"] * TALL[0] + 1, (style, p["lines"]))
                    self.assertLessEqual(SAFE["bottom"] + height / TALL[1], SAFE["top"] + 0.02, (style, p["lines"]))
            self.assertGreaterEqual(total, len(caps))


class TestHandEdits(unittest.TestCase):

    def look(self, style="CLEAN", res=WIDE, **kw):
        props = cap.resolve_style(style, *res)["props"]
        look = {"location": props["location"], "font_size": float(props["font_size"]),
                "wrap_width": 1.0 if cap.STYLES[style]["highlight"] else props["wrap_width"],
                "anchor_x": "CENTER", "anchor_y": "BOTTOM", "transform": (0.0, 0.0, 1.0, 1.0, 0.0)}
        look.update(kw)
        return look

    def test_as_the_style_made_it(self):
        for style in cap.STYLES:
            self.assertEqual(cap.hand_edits(self.look(style), style, *WIDE), [], style)

    def test_moved_resized_or_transformed_by_hand(self):
        self.assertEqual(cap.hand_edits(self.look(location=(0.5, 0.6)), "CLEAN", *WIDE), ["position"])
        self.assertEqual(cap.hand_edits(self.look(font_size=70.0), "CLEAN", *WIDE), ["size"])
        self.assertEqual(cap.hand_edits(self.look(wrap_width=0.5), "CLEAN", *WIDE), ["wrap width"])
        self.assertEqual(cap.hand_edits(self.look(anchor_y="TOP"), "CLEAN", *WIDE), ["anchor"])
        self.assertEqual(cap.hand_edits(self.look(transform=(0.0, 40.0, 1.0, 1.0, 0.0)), "CLEAN", *WIDE),
                         ["transform"])
        self.assertEqual(cap.hand_edits(self.look(), "neon", *WIDE), ["style"])

    def test_judged_against_the_frame_it_was_made_for(self):
        # The style at 9:16 isn't a hand edit of a caption made for 9:16, and is for one made for 16:9.
        tall = self.look(res=TALL)
        self.assertEqual(cap.hand_edits(tall, "CLEAN", *TALL), [])
        self.assertIn("size", cap.hand_edits(tall, "CLEAN", *WIDE))

    def test_the_report(self):
        self.assertEqual(cap.relayout_report(27, 1), "27 captions laid out for vertical, 1 left as edited")
        self.assertEqual(cap.relayout_report(1, 0, 1),
                         "1 caption laid out for vertical (1 split to fit), 0 left as edited")


# -- the strips (captions_ops on the stand-in bpy) ---------------------------------------------

class _Key:
    def __init__(self, frame, value):
        self.co = [float(frame), float(value)]
        self.handle_left = [frame - 1.0, float(value)]
        self.handle_right = [frame + 1.0, float(value)]


class TestRelayoutStrips(_OpsCase):

    def lines(self):
        return sorted(super().lines(), key=lambda s: s.left_handle)

    def caption(self, style="CLEAN"):
        clip = _clip(self.scene)
        return self.ops.add_captions(self.scene, [clip], style, None, self.transcript)

    def to_vertical(self):
        """What Make Vertical does to its copy: the new size, then the captions laid out again."""
        self.scene.render.resolution_x, self.scene.render.resolution_y = TALL
        return self.ops.relayout_captions(self.scene, WIDE)

    def snapshot(self):
        return [(s.name, s.channel, s.left_handle, s.right_handle, s.text, s.font_size, tuple(s.location),
                 s.color) for s in self.lines()]

    def test_new_captions_store_their_frame_and_word_times(self):
        self.caption()
        tag = self.lines()[0].get("bfacw_caption")
        self.assertEqual(tag["frame"], [1920, 1080])
        words = self.lines()[0].text.split()
        self.assertEqual(len(tag["word_frames"]), len(words))
        self.assertEqual(tag["word_frames"][0], 0)
        self.assertEqual(tag["word_frames"], sorted(tag["word_frames"]))

    def test_clean_captions_in_the_9_16_copy(self):
        made = self.caption()["lines_created"]
        before = self.lines()
        words = " ".join(_flat(s.text) for s in before)
        span = (before[0].left_handle, before[-1].right_handle)
        channel = before[0].channel
        colour = before[0].color
        result = self.to_vertical()
        lines = self.lines()
        self.assertEqual((result["laid_out"], result["left"]), (made, 0))
        self.assertGreater(len(lines), made)                            # some split to fit
        self.assertGreater(result["split"], 0)
        self.assertEqual(result["message"], cap.relayout_report(made, 0, result["split"]))
        self.assertEqual(" ".join(_flat(s.text) for s in lines), words)           # every word, in order
        self.assertEqual((lines[0].left_handle, lines[-1].right_handle), span)
        fit = cap.default_max_chars("CLEAN", *TALL)
        for s in lines:
            self.assertEqual(s.font_size, 60)
            self.assertEqual(tuple(s.location), (0.5, SAFE["bottom"]))
            self.assertEqual(s.wrap_width, SAFE["width"])
            self.assertLessEqual(len(s.text.split("\n")), 2)
            self.assertTrue(all(len(ln) <= fit for ln in s.text.split("\n")), s.text)
            self.assertEqual((s.channel, s.color), (channel, colour))
            tag = s.get("bfacw_caption")
            self.assertEqual((tag["frame"], tag["chars"], tag["style"]), ([1080, 1920], fit, "CLEAN"))
        for a, b in zip(lines, lines[1:]):
            self.assertLessEqual(a.right_handle, b.left_handle)
        self.assertEqual(len({s.name for s in lines}), len(lines))
        # Done once: again changes nothing.
        snap = self.snapshot()
        self.assertEqual(self.ops.relayout_captions(self.scene, WIDE)["laid_out"], 0)
        self.assertEqual(self.snapshot(), snap)

    def test_a_hand_moved_caption_is_left_and_counted(self):
        made = self.caption()["lines_created"]
        moved = self.lines()[4]
        moved.location = (0.5, 0.6)
        kept = (moved.name, moved.text, moved.font_size, moved.left_handle, moved.right_handle)
        worded = self.lines()[2]
        worded.text = worded.text.replace("\n", " ") + " (edited)"
        result = self.to_vertical()
        self.assertEqual((result["laid_out"], result["left"], result["left_names"]), (made - 1, 1, [kept[0]]))
        self.assertIn("1 left as edited", result["message"])
        strip = self.scene.sequence_editor.strips.get(kept[0])
        self.assertEqual((strip.name, strip.text, strip.font_size, strip.left_handle, strip.right_handle), kept)
        self.assertEqual(tuple(strip.location), (0.5, 0.6))
        edited = [s for s in self.lines() if "(edited)" in s.text]     # a text edit is kept and laid out
        self.assertEqual(len(edited), 1)
        self.assertEqual(edited[0].font_size, 60)

    def test_captions_from_before_497_use_the_frame_they_were_in(self):
        self.caption()
        for s in self.lines():
            tag = dict(s.get("bfacw_caption"))
            tag.pop("frame")
            tag.pop("word_frames")
            s["bfacw_caption"] = tag
        words = " ".join(_flat(s.text) for s in self.lines())
        result = self.to_vertical()
        self.assertEqual(result["left"], 0)
        self.assertEqual(" ".join(_flat(s.text) for s in self.lines()), words)
        self.assertTrue(all(s.font_size == 60 for s in self.lines()))

    def test_word_by_word_highlights_follow_their_new_lines(self):
        with self._measured():
            made = self.caption("WORD_HIGHLIGHT")["lines_created"]
            for s in self.lines():
                s.font = SimpleNamespace(filepath="Inter.woff2")
            result = self.to_vertical()
        lines, words = self.lines(), self.words()
        self.assertEqual(result["laid_out"], made)
        self.assertGreater(len(lines), made)
        px = cap.resolve_style("WORD_HIGHLIGHT", *TALL)["props"]["font_size"]
        for line in lines:
            self.assertNotIn("\n", line.text)
            self.assertEqual((line.font_size, line.wrap_width), (px, 1.0))
            mine = sorted((w for w in words if w.get("bfacw_caption")["line"] == line.name), key=lambda w: w.name)
            self.assertTrue(mine, line.name)
            self.assertTrue(all(w.channel == line.channel + 1 for w in mine))
            self.assertTrue(all(line.left_handle <= w.left_handle and w.right_handle <= line.right_handle
                                for w in mine))
            self.assertEqual(mine[0].left_handle, line.left_handle)
            self.assertEqual(mine[-1].right_handle, line.right_handle)
            lw = line.text.split()
            for w in mine:
                i = int(w.name.rsplit("_w", 1)[1]) - 1
                self.assertEqual(w.text, lw[i])
                prefix = " ".join(lw[:i]) + " " if i else ""
                x = cap.highlight_left(0.5, len(line.text) * px * 0.5, len(prefix) * px * 0.5, TALL[0])
                self.assertAlmostEqual(w.location[0], x)
                self.assertEqual(w.location[1], line.location[1])
                self.assertEqual(w.font_size, px)

    def test_bold_pop_keys_follow_the_new_size(self):
        """A caption made before #542 (no "anim" in its tag): its own keys are scaled."""
        self.caption("BOLD_POP")
        for s in self.lines():
            tag = dict(s.get("bfacw_caption"))
            tag.pop("anim")
            s["bfacw_caption"] = tag
        old_names = {s.name for s in self.lines()}
        first = self.lines()[0]
        wide = cap.resolve_style("BOLD_POP", *WIDE)["props"]["font_size"]
        fc = SimpleNamespace(data_path='sequence_editor.strips_all["{:s}"].font_size'.format(first.name),
                             keyframe_points=[_Key(first.left_handle, wide * 0.78), _Key(first.left_handle + 3, wide)],
                             update=lambda: None)
        bag = SimpleNamespace(fcurves=[fc])
        action = SimpleNamespace(layers=[SimpleNamespace(strips=[SimpleNamespace(channelbags=[bag])])],
                                 update_tag=lambda: None)
        self.scene.animation_data = SimpleNamespace(action=action)
        first.font_size = wide * 0.78                    # shown mid-pop: the steady size is the last key
        self.to_vertical()
        tall = cap.resolve_style("BOLD_POP", *TALL)["props"]["font_size"]
        self.assertAlmostEqual(fc.keyframe_points[0].co[1], tall * 0.78)
        self.assertAlmostEqual(fc.keyframe_points[1].co[1], tall)
        self.assertAlmostEqual(fc.keyframe_points[1].handle_left[1], tall)
        self.assertEqual(first.font_size, tall)
        # New pieces pop too (keyed like Auto Captions keys them).
        new = [s for s in self.lines() if s.name not in old_names and s.right_handle - s.left_handle >= 5]
        self.assertTrue(new)
        for s in new:
            self.assertAlmostEqual(s.keys[0][2], tall * 0.78)
            self.assertEqual(s.keys[1][2], tall)
            self.assertEqual(s.keys[1][1] - s.keys[0][1], 3)
        self.assertTrue(all(s.text == s.text.upper() for s in self.lines()))

    def test_text_strips_that_are_not_captions_are_left(self):
        title = self.scene.sequence_editor.strips.new_effect("Title", "TEXT", 6, 1, length=40)
        title.text, title.font_size, title.location = "My title", 90, (0.5, 0.8)
        self.caption()
        self.to_vertical()
        self.assertEqual((title.text, title.font_size, tuple(title.location)), ("My title", 90, (0.5, 0.8)))

    def test_no_sequencer_no_captions(self):
        self.scene.sequence_editor = None
        self.assertEqual(self.ops.relayout_captions(self.scene, WIDE)["laid_out"], 0)
        self.assertFalse(_STATE["undo"])


class TestReframeToolResult(unittest.TestCase):

    def test_the_tool_says_what_happened_to_the_captions(self):
        from pathlib import Path
        tools = Path(__file__).resolve().parents[1] / "mcp" / "blmcp" / "tools"
        ns = {"__name__": "toolcode"}
        source = (tools / "reframe_vertical_toolcode.py").read_text(encoding="utf-8")
        exec(compile(source, "reframe_vertical_toolcode.py", "exec"), ns)  # pylint: disable=exec-used
        done = {"scene": "Edit Vertical", "source_scene": "Edit", "copied": True, "mode": "CROP",
                "resolution": [1080, 1920], "added": ["a"], "kept": [], "reset": [],
                "captions": {"laid_out": 26, "split": 8, "left": 1, "left_names": ["cap_0005"],
                             "message": cap.relayout_report(26, 1, 8)}}
        ns["_coworker_api"] = lambda: SimpleNamespace(reframe_vertical=lambda *_a: done)
        out = ns["main"](ns["Params"](strips=[], mode="CROP", in_copy=True, resolution="1080x1920",
                                      reset=False))._asdict()
        self.assertEqual(out["captions"], {"laid_out": 26, "split": 8, "left": 1, "left_names": ["cap_0005"]})
        self.assertIn("26 captions laid out for vertical (8 split to fit), 1 left as edited", out["message"])
        self.assertIn("cap_0005", out["message"])
        json.dumps(out)
        del done["captions"]                                 # no captions: nothing said about them
        out = ns["main"](ns["Params"](strips=[], mode="CROP", in_copy=True, resolution="1080x1920",
                                      reset=False))._asdict()
        self.assertIsNone(out["captions"])
        self.assertNotIn("Captions", out["message"])


if __name__ == "__main__":
    unittest.main()
