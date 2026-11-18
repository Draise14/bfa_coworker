# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Caption animation per line, per word or both (#594, Part 3): the three choices and the old single
"animation" mapped onto them, the key lists of a word's motion and of the line animation on word strips
(times, counts, limits, the offset that holds a scaled word in place), the channel block and the refusal,
and the strips (a hidden anchor, one strip per word on a channel of its own, keys on the words, not the
anchor; re-running replaces, a bold change moves the words and keys them again, Edit by Transcript's text
rewrite makes them again).  No Blender: made-up words on the stand-in ``bpy`` of ``test_captions``.

Run with::

    python -m unittest tests.test_captions_words -v
"""

__all__ = ()

import unittest
from types import SimpleNamespace
from unittest import mock

import test_captions
from test_captions import _OpsCase, _clip, cap

FPS = 30.0
WIDE = (1920, 1080)
MOTIONS = ("POP", "SCALE", "RISE", "FADE_IN")
LINES = ("NONE", "FADE", "POP", "SLIDE_UP", "SLIDE_DOWN", "TYPEWRITER")


def tracks(line="NONE", word="POP", fs=100, fe=190, a=130, b=150, mid=(1000.0, 200.0), line_mid=(960.0, 200.0),
           reveal=None):
    return cap.word_tracks(line, word, fs, fe, a, b, FPS, WIDE[0], WIDE[1], mid, line_mid, reveal,
                           (1.0, 1.0, 1.0, 1.0), (1.0, 0.8, 0.1, 1.0))


def by_path(ts):
    return {t["path"]: t for t in ts}


# -- the choices and the old values -----------------------------------------------------------

class TestChoices(unittest.TestCase):

    def test_the_names(self):
        self.assertEqual(cap.animate_key("per word"), "WORD")
        self.assertEqual(cap.animate_key("Per Line"), "LINE")
        self.assertEqual(cap.animate_key(None), "STYLE")
        self.assertEqual(cap.animate_key("both"), "BOTH")
        self.assertIsNone(cap.animate_key("sideways"))
        self.assertEqual(set(cap.ANIMATIONS), {"STYLE", "NONE", "FADE", "SLIDE_UP", "SLIDE_DOWN", "TYPEWRITER", "POP"})
        self.assertEqual(cap.animation_key("slide down"), "SLIDE_DOWN")
        self.assertEqual(cap.word_animation_key("Highlight + Pop"), "HIGHLIGHT_POP")
        self.assertEqual(cap.word_animation_key("pop and highlight"), "HIGHLIGHT_POP")
        self.assertEqual(cap.word_animation_key("fade in"), "FADE_IN")
        self.assertEqual(cap.word_animation_key("fade"), "FADE_IN")
        self.assertEqual(cap.word_animation_key("highlight"), "HIGHLIGHT")
        self.assertEqual(cap.word_animation_key(None), "STYLE")
        self.assertIsNone(cap.word_animation_key("wobble"))
        self.assertIsNone(cap.word_animation_key("highlight wobble"))
        self.assertEqual(set(cap.WORD_ANIMATIONS), {"STYLE", "NONE", "HIGHLIGHT", *MOTIONS,
                                                    *("HIGHLIGHT_" + m for m in MOTIONS)})

    def test_every_word_animation_says_its_mode(self):
        self.assertEqual({k: cap.word_mode(k) for k in cap.WORD_ANIMATIONS if k != "STYLE"},
                         {"NONE": "none", "HIGHLIGHT": "highlight",
                          **{m: "motion" for m in MOTIONS}, **{"HIGHLIGHT_" + m: "motion" for m in MOTIONS}})
        self.assertEqual(cap.word_parts("HIGHLIGHT_RISE"), (True, "RISE"))
        self.assertEqual(cap.word_parts("SCALE"), (False, "SCALE"))

    def test_left_alone_it_is_the_styles_own(self):
        for style, want in (("CLEAN", ("STYLE", "NONE")), ("BOLD_POP", ("STYLE", "NONE")),
                            ("WORD_HIGHLIGHT", ("STYLE", "HIGHLIGHT")), ("BOXED", ("STYLE", "NONE"))):
            got = cap.resolve_animation(style)
            self.assertEqual((got["line"], got["word"]), want, style)
            self.assertEqual(got["animate"], "STYLE")

    def test_the_old_single_value_is_the_line_animation_and_keeps_the_styles_words(self):
        for old in ("NONE", "FADE", "SLIDE_UP", "TYPEWRITER", "POP", "STYLE"):
            for style in cap.STYLES:
                got = cap.resolve_animation(style, legacy=old)
                self.assertEqual(got["line"], old, (old, style))
                self.assertEqual(got["words"], "highlight" if style == "WORD_HIGHLIGHT" else "none", (old, style))
        self.assertEqual(cap.resolve_animation("CLEAN", legacy="slide up")["line"], "SLIDE_UP")

    def test_animate_decides_what_moves(self):
        r = cap.resolve_animation
        self.assertEqual(r("CLEAN", "NONE", "FADE", "POP"), {"animate": "NONE", "line": "NONE", "word": "NONE",
                                                              "words": "none"})
        self.assertEqual(r("WORD_HIGHLIGHT", "LINE", "SLIDE_UP", "POP"),
                         {"animate": "LINE", "line": "SLIDE_UP", "word": "NONE", "words": "none"})
        self.assertEqual(r("CLEAN", "WORD", "FADE", "POP"), {"animate": "WORD", "line": "NONE", "word": "POP",
                                                              "words": "motion"})
        self.assertEqual(r("CLEAN", "WORD")["word"], "HIGHLIGHT")
        self.assertEqual(r("CLEAN", "WORD", None, "STYLE")["word"], "HIGHLIGHT")
        both = r("BOXED", "BOTH", "TYPEWRITER", "highlight rise")
        self.assertEqual((both["line"], both["word"], both["words"]), ("TYPEWRITER", "HIGHLIGHT_RISE", "motion"))

    def test_without_animate_the_choices_imply_it(self):
        r = cap.resolve_animation
        self.assertEqual(r("CLEAN", None, None, "pop")["animate"], "WORD")
        self.assertEqual(r("CLEAN", None, "fade", None)["animate"], "LINE")
        self.assertEqual(r("CLEAN", None, "fade", "pop")["animate"], "BOTH")
        self.assertEqual(r("WORD_HIGHLIGHT", None, "fade", None)["words"], "highlight")     # as before

    def test_the_tool_checks_its_choices(self):
        self.assertEqual(cap.check_animation_params(None, None, None), (None, None, None))
        self.assertEqual(cap.check_animation_params("both", "slide down", "highlight pop"),
                         ("BOTH", "SLIDE_DOWN", "HIGHLIGHT_POP"))
        for bad in (("sideways", None, None), (None, "wobble", None), (None, None, "wobble")):
            with self.assertRaises(cap.CaptionsError):
                cap.check_animation_params(*bad)

    def test_a_tag_from_before_has_words_exactly_for_the_highlight_style(self):
        self.assertEqual(cap.tag_words({"style": "WORD_HIGHLIGHT", "anim": "FADE"}), ("highlight", "HIGHLIGHT"))
        self.assertEqual(cap.tag_words({"style": "CLEAN", "anim": "FADE"}), ("none", "NONE"))
        self.assertEqual(cap.tag_words({"style": "CLEAN", "wanim": "HIGHLIGHT_POP"}), ("motion", "HIGHLIGHT_POP"))
        self.assertEqual(cap.tag_words({"style": "WORD_HIGHLIGHT", "wanim": "NONE"}), ("none", "NONE"))

    def test_a_typewriter_of_the_visible_line_needs_one_line_but_not_with_word_strips(self):
        self.assertTrue(cap.plan_one_line(cap.resolve_animation("CLEAN", legacy="TYPEWRITER")))
        self.assertTrue(cap.plan_one_line(cap.resolve_animation("CLEAN", "LINE", "TYPEWRITER")))
        self.assertFalse(cap.plan_one_line(cap.resolve_animation("CLEAN", "BOTH", "TYPEWRITER", "POP")))


# -- a word's keys ----------------------------------------------------------------------------

class TestWordKeys(unittest.TestCase):

    def test_the_keys_follow_the_words_own_time(self):
        for motion in MOTIONS:
            early = by_path(tracks(word=motion, a=110, b=125))
            late = by_path(tracks(word=motion, a=160, b=175))
            for path in early:
                first_early = min(f for f, _v in early[path]["keys"])
                first_late = min(f for f, _v in late[path]["keys"])
                self.assertGreaterEqual(first_early, 100, (motion, path))
                self.assertLess(first_early, first_late, (motion, path))
            key_frames = {f for t in late.values() for f, _v in t["keys"]}
            self.assertTrue(all(f >= 159 for f in key_frames), motion)      # nothing before the word

    def test_pop_scale_rise_and_fade_in_hold_the_word_hidden_or_up(self):
        pop = by_path(tracks(word="POP"))
        self.assertEqual(pop["blend_alpha"]["keys"], [(129, 0.0), (130, 1.0)])
        self.assertEqual([v for _f, v in pop["transform.scale_x"]["keys"]], [cap.POP_FROM, cap.POP_OVER, 1.0])
        rise = by_path(tracks(word="RISE"))
        self.assertEqual(rise["blend_alpha"]["keys"][0], (130, 0.0))
        self.assertLess(rise["transform.offset_y"]["keys"][0][1], 0)
        self.assertEqual(rise["transform.offset_y"]["keys"][-1][1], 0.0)
        self.assertNotIn("transform.scale_x", rise)
        fade = by_path(tracks(word="FADE_IN"))
        self.assertEqual(list(fade), ["blend_alpha"])
        self.assertEqual(fade["blend_alpha"]["keys"][0], (130, 0.0))
        scale = by_path(tracks(word="SCALE"))
        self.assertNotIn("blend_alpha", scale)          # a scale leaves the word up the whole time
        values = [v for _f, v in scale["transform.scale_x"]["keys"]]
        self.assertEqual((values[0], max(values), values[-1]), (1.0, cap.WORD_SCALE_UP, 1.0))

    def test_every_key_lies_inside_the_caption(self):
        for motion in MOTIONS:
            for line in LINES:
                for a, b in ((100, 110), (130, 150), (185, 190), (189, 190)):
                    for t in tracks(line, "HIGHLIGHT_" + motion, a=a, b=b, reveal=140):
                        for f, _v in t["keys"]:
                            self.assertTrue(100 <= f <= 190, (motion, line, a, t["path"], f))
                        frames = [f for f, _v in t["keys"]]
                        self.assertEqual(frames, sorted(set(frames)), (motion, line, a, t["path"]))

    def test_a_scaled_word_stays_on_its_own_middle(self):
        """The strip scales around the frame's middle: the offset keeps the word's middle where it is."""
        c0 = (WIDE[0] / 2, WIDE[1] / 2)
        for motion in ("POP", "SCALE"):
            ts = by_path(tracks(word=motion, mid=(1400.0, 300.0)))
            for (f, s), (_f2, ox), (_f3, oy) in zip(ts["transform.scale_x"]["keys"], ts["transform.offset_x"]["keys"],
                                                    ts["transform.offset_y"]["keys"]):
                self.assertAlmostEqual(c0[0] + s * (1400.0 - c0[0]) + ox, 1400.0, 1, (motion, f))
                self.assertAlmostEqual(c0[1] + s * (300.0 - c0[1]) + oy, 300.0, 1, (motion, f))

    def test_the_line_pop_on_words_scales_the_whole_line_around_its_middle(self):
        c0 = (WIDE[0] / 2, WIDE[1] / 2)
        ts = by_path(tracks("POP", "HIGHLIGHT", mid=(1400.0, 300.0), line_mid=(1000.0, 250.0)))
        for (f, s), (_f2, ox), (_f3, oy) in zip(ts["transform.scale_x"]["keys"], ts["transform.offset_x"]["keys"],
                                                ts["transform.offset_y"]["keys"]):
            self.assertAlmostEqual(c0[0] + s * (1400.0 - c0[0]) + ox, 1000.0 + s * (1400.0 - 1000.0), 1, f)
            self.assertAlmostEqual(c0[1] + s * (300.0 - c0[1]) + oy, 250.0 + s * (300.0 - 250.0), 1, f)

    def test_highlight_lights_the_word_at_its_own_time_only(self):
        ts = by_path(tracks(word="HIGHLIGHT_FADE_IN", a=130, b=150))
        keys = ts["color"]["keys"]
        self.assertEqual([f for f, _v in keys], [100, 130, 150])
        self.assertEqual([v for _f, v in keys], [(1, 1, 1, 1), (1, 0.8, 0.1, 1), (1, 1, 1, 1)])
        self.assertEqual(ts["color"]["interpolation"], "CONSTANT")
        self.assertEqual(ts["color"]["steady"], (1, 1, 1, 1))
        first = by_path(tracks(word="HIGHLIGHT_POP", a=100, b=120))["color"]["keys"]
        self.assertEqual([f for f, _v in first], [100, 120])                # the first word is lit from the start
        last = by_path(tracks(word="HIGHLIGHT_POP", a=170, b=190))["color"]["keys"]
        self.assertEqual([f for f, _v in last], [100, 170])                 # and the last stays lit to the end

    def test_the_line_animation_goes_on_every_word_not_on_the_line(self):
        fade = by_path(tracks("FADE", "HIGHLIGHT_POP"))["blend_alpha"]["keys"]
        self.assertEqual(fade[0], (100, 0.0))                                # the line's way in
        self.assertEqual(fade[-1], (190, 0.0))                               # and its way out, over the word's own
        self.assertIn((130, 1.0), fade)
        slide = by_path(tracks("SLIDE_DOWN", "HIGHLIGHT_FADE_IN"))
        self.assertGreater(slide["transform.offset_y"]["keys"][0][1], 0)     # from above
        up = by_path(tracks("SLIDE_UP", "HIGHLIGHT_FADE_IN"))
        self.assertLess(up["transform.offset_y"]["keys"][0][1], 0)           # from below
        self.assertEqual(set(by_path(tracks("NONE", "HIGHLIGHT_FADE_IN"))), {"blend_alpha", "color"})

    def test_typewriter_on_word_strips_shows_the_words_in_order(self):
        texts = ["Made", "up", "words", "for", "a", "test"]
        frames = cap.typewriter_frames(texts, 100, 190, FPS)
        self.assertEqual(frames, sorted(frames))
        self.assertEqual(frames[0], 100)
        self.assertLessEqual(frames[-1], 100 + 45)                           # at most half the caption
        a = by_path(tracks("TYPEWRITER", "HIGHLIGHT_SCALE", a=130, b=150, reveal=frames[3]))["blend_alpha"]["keys"]
        self.assertIn((frames[3] - 1, 0.0), a)
        self.assertIn((frames[3], 1.0), a)

    def test_a_caption_too_short_gets_no_motion(self):
        self.assertEqual(tracks("FADE", "POP", fs=100, fe=103, a=100, b=102), [])
        self.assertEqual(tracks(word="POP", fs=100, fe=190, a=189, b=190), [])


class TestLimits(unittest.TestCase):

    def test_the_key_counts_stay_inside_the_limits(self):
        for line in LINES:
            for word in cap.WORD_ANIMATIONS:
                if word == "STYLE":
                    continue
                self.assertLessEqual(cap.word_key_count(line, word), cap.MAX_WORD_KEYS, (line, word))
        self.assertLessEqual(cap.word_key_count("POP", "HIGHLIGHT_POP"), 40)

    def test_the_paths_are_the_ones_a_word_strip_may_key(self):
        for line in LINES:
            for word in cap.WORD_ANIMATIONS:
                if word != "STYLE":
                    self.assertLessEqual(set(cap.word_animation_paths(line, word)), set(cap.WORD_PATHS), (line, word))
        self.assertIn("color", cap.word_animation_paths("NONE", "HIGHLIGHT_POP"))
        self.assertNotIn("color", cap.word_animation_paths("NONE", "POP"))

    def test_a_plan_inside_the_limits_passes(self):
        self.assertIsNone(cap.check_word_plan([6, 9, 12, 24], "FADE", "HIGHLIGHT_POP"))
        self.assertIsNone(cap.check_word_plan([], "FADE", "POP"))

    def test_too_many_words_in_a_line_is_refused_naming_the_limit_and_what_to_do(self):
        msg = cap.check_word_plan([6, 25], "NONE", "POP")
        self.assertIn(str(cap.MAX_WORD_CHANNELS), msg)
        self.assertIn("25", msg)
        self.assertIn("Highlight", msg)
        self.assertIn("words per line", msg)

    def test_too_many_keys_in_a_run_is_refused(self):
        msg = cap.check_word_plan([20] * 400, "FADE", "HIGHLIGHT_POP")
        self.assertIn(str(cap.MAX_RUN_KEYS), msg)
        self.assertIn("lighter Word Animation", msg)
        self.assertIn("fewer clips", msg)
        self.assertNotIn("fewer words per line", msg)

    def test_no_room_above_the_clip_is_refused(self):
        msg = cap.check_word_plan([10], "NONE", "POP", max_channel_room=5)
        self.assertIn("10", msg)
        self.assertIn(str(cap.MAX_CHANNEL), msg)

    def test_every_word_has_a_span_even_with_no_time_of_its_own(self):
        framed = {"frame_start": 100, "frame_end": 160, "words": [{"frame": 100}, {"frame": 120}, {"frame": 120},
                                                                  {"frame": 150}]}
        self.assertEqual(cap.word_spans(framed), [(0, 100, 120), (1, 120, 120), (2, 120, 150), (3, 150, 160)])
        self.assertEqual(len(cap.highlight_windows(framed)), 3)         # the cheap way leaves the empty one out


# -- the strips -------------------------------------------------------------------------------

class _Transform(SimpleNamespace):

    def keyframe_insert(self, prop, frame):
        self.owner.keys.append(("transform." + prop, frame, getattr(self, prop)))


class _MotionCase(_OpsCase):
    """``add_captions`` on the stand-in with the properties word motions need on a text strip."""

    def setUp(self):
        super().setUp()
        patch = self._measured()
        patch.start()
        self.addCleanup(patch.stop)
        original = test_captions._Strips.new_effect

        def new_effect(strips, *args, **kw):
            s = original(strips, *args, **kw)
            s.transform = _Transform(offset_x=0.0, offset_y=0.0, scale_x=1.0, scale_y=1.0, rotation=0.0, owner=s)
            s.blend_alpha = 1.0
            return s
        stack = mock.patch.object(test_captions._Strips, "new_effect", new_effect)
        stack.start()
        self.addCleanup(stack.stop)
        self.clip = _clip(self.scene)

    def make(self, style="CLEAN", **kw):
        return self.ops.add_captions(self.scene, [self.clip], style, None, self.transcript, **kw)

    def words_of(self, line):
        return sorted((w for w in self.words() if w.get("bfacw_caption")["line"] == line.name), key=lambda w: w.name)

    def all_strips(self):
        return list(self.scene.sequence_editor.strips)


class TestMotionStrips(_MotionCase):

    def test_the_line_stays_as_a_hidden_anchor_and_every_word_is_a_strip_for_the_whole_line(self):
        result = self.make(animate="WORD", word_animation="POP")
        self.assertEqual((result["animate"], result["word_animation"]), ("Per Word", "Pop"))
        for line in self.lines():
            self.assertEqual(line.color[3], 0.0)
            self.assertFalse(line.use_outline or line.use_shadow or line.use_box)
            self.assertTrue(line.text)                                       # the anchor keeps the text
            tag = line.get("bfacw_caption")
            self.assertEqual((tag["anim"], tag["wanim"]), ("NONE", "POP"))
            words = self.words_of(line)
            self.assertEqual([w.text for w in words], line.text.split())
            for w in words:
                self.assertEqual((w.left_handle, w.right_handle), (line.left_handle, line.right_handle))
                self.assertEqual(w.color[3], 1.0)
            self.assertEqual(len({w.channel for w in words}), len(words))   # a channel each
            self.assertEqual([w.channel for w in words], list(range(line.channel + 1, line.channel + 1 + len(words))))

    def test_the_anchor_carries_no_keys_and_the_words_carry_them_at_their_own_time(self):
        self.make(animate="WORD", word_animation="HIGHLIGHT_FADE_IN")
        for line in self.lines():
            self.assertEqual(line.keys, [])
            tag = line.get("bfacw_caption")
            for w in self.words_of(line):
                wt = w.get("bfacw_caption")
                start = line.left_handle + wt["at"]
                alpha = [f for p, f, *_ in w.keys if p == "blend_alpha"]
                self.assertTrue(alpha and min(alpha) >= start, (w.name, alpha, start))
                lit = [f for p, f, *_ in w.keys if p == "color"]
                self.assertIn(start, lit)
            self.assertIn("word_frames", tag)

    def test_the_same_block_of_channels_serves_every_line(self):
        self.make(animate="WORD", word_animation="RISE")
        lines = self.lines()
        self.assertGreater(len(lines), 5)
        self.assertEqual(len({ln.channel for ln in lines}), 1)
        longest = max(len(self.words_of(ln)) for ln in lines)
        used = {w.channel for w in self.words()}
        self.assertEqual(used, set(range(lines[0].channel + 1, lines[0].channel + 1 + longest)))
        names = self.scene.sequence_editor.channels
        self.assertEqual(names[lines[0].channel].name, "Captions")
        self.assertTrue(all(names[c].name == "Captions Highlight" for c in used))

    def test_no_two_strips_of_a_channel_overlap(self):
        self.make(animate="BOTH", line_animation="FADE", word_animation="HIGHLIGHT_POP")
        by_channel = {}
        for s in self.all_strips():
            if s.channel > 1:
                by_channel.setdefault(s.channel, []).append((s.left_handle, s.right_handle))
        for channel, spans in by_channel.items():
            spans.sort()
            for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
                self.assertLessEqual(a1, b0, channel)

    def test_both_puts_the_line_animation_on_each_word_not_on_the_anchor(self):
        self.make(animate="BOTH", line_animation="SLIDE_UP", word_animation="HIGHLIGHT_FADE_IN")
        line = self.lines()[0]
        self.assertEqual(line.keys, [])
        for w in self.words_of(line):
            paths = {p for p, *_ in w.keys}
            self.assertIn("transform.offset_y", paths)
            self.assertIn("blend_alpha", paths)
            self.assertIn("color", paths)
            frames = [f for p, f, *_ in w.keys if p == "transform.offset_y"]
            self.assertEqual(min(frames), line.left_handle)

    def test_typewriter_shows_the_words_in_order_each_on_its_strip(self):
        self.make(animate="BOTH", line_animation="TYPEWRITER", word_animation="HIGHLIGHT_SCALE")
        line = self.lines()[0]
        self.assertEqual(self.words_of(line)[0].get("bfacw_caption")["i"], 0)
        words = self.words_of(line)
        self.assertEqual(self.lines()[0].keys, [])
        shown = []
        for w in words:
            alpha = [(f, v) for p, f, v in w.keys if p == "blend_alpha"]
            up = [f for (_f0, v0), (f, v) in zip(alpha, alpha[1:]) if v0 == 0.0 and v == 1.0]
            shown.append(up[0] if up else line.left_handle)
        self.assertEqual(shown, sorted(shown))

    def test_highlight_alone_keeps_the_cheap_way(self):
        result = self.make(animate="WORD", word_animation="HIGHLIGHT")
        self.assertEqual(result["word_animation"], "Highlight")
        for line in self.lines():
            self.assertEqual(line.color, (1, 1, 1, 1))                       # the line draws as before
            words = self.words_of(line)
            self.assertEqual({w.channel for w in words}, {line.channel + 1})
            self.assertEqual([w.keys for w in words], [[]] * len(words))
            self.assertTrue(all(w.left_handle >= line.left_handle and w.right_handle <= line.right_handle
                                for w in words))
            if len(words) > 1:
                self.assertTrue(all(w.right_handle - w.left_handle < line.right_handle - line.left_handle
                                    for w in words))

    def test_per_line_makes_no_word_strips_and_keys_the_line(self):
        self.make("WORD_HIGHLIGHT", animate="LINE", line_animation="FADE")
        self.assertEqual(self.words(), [])
        self.assertTrue(all(any(p == "blend_alpha" for p, *_ in ln.keys) for ln in self.lines()))
        self.assertTrue(all(ln.get("bfacw_caption")["wanim"] == "NONE" for ln in self.lines()))

    def test_none_moves_nothing(self):
        self.make("WORD_HIGHLIGHT", animate="NONE")
        self.assertEqual(self.words(), [])
        self.assertTrue(all(ln.keys == [] for ln in self.lines()))

    def test_every_word_animation_on_every_style(self):
        for style in cap.STYLES:
            for word in cap.WORD_ANIMATIONS:
                if word == "STYLE":
                    continue
                with self.subTest(style=style, word=word):
                    result = self.make(style, animate="WORD", word_animation=word)
                    self.assertEqual(result["status"], "ok")
                    mode = cap.word_mode(word)
                    if mode == "none":
                        self.assertEqual(self.words(), [])
                    else:
                        self.assertGreater(len(self.words()), 0)
                    for ln in self.lines():
                        self.assertEqual(ln.color[3] == 0.0, mode == "motion")
                    self.assertTrue(all(w.get("bfacw_caption")["style"] == style for w in self.words()))

    def test_running_it_again_replaces_what_the_earlier_run_made(self):
        first = self.make(animate="WORD", word_animation="POP")
        n_words, n_lines = len(self.words()), len(self.lines())
        again = self.make(animate="WORD", word_animation="HIGHLIGHT_SCALE")
        self.assertEqual(again["replaced"], first["lines_created"])
        self.assertEqual((len(self.lines()), len(self.words())), (n_lines, n_words))
        self.assertEqual(len({s.name for s in self.all_strips()}), len(self.all_strips()))
        third = self.make(animate="LINE", line_animation="FADE")             # to no word strips at all
        self.assertEqual(self.words(), [])
        self.assertEqual(len(self.lines()), third["lines_created"])
        self.assertTrue(all(ln.color[3] == 1.0 for ln in self.lines()))
        # the channels above the captions are free again
        self.assertEqual({s.channel for s in self.all_strips()} - {1}, {self.lines()[0].channel})

    def test_too_many_words_in_a_line_refuses_and_builds_nothing(self):
        before = [(s.name, s.channel) for s in self.all_strips()]
        with mock.patch.object(self.ops.cap, "MAX_WORD_CHANNELS", 3):
            with self.assertRaises(self.ops.CaptionsError) as raised:
                self.make(animate="WORD", word_animation="POP")
        self.assertIn("3", str(raised.exception))
        self.assertIn("Highlight", str(raised.exception))
        self.assertEqual([(s.name, s.channel) for s in self.all_strips()], before)

    def test_a_refusal_keeps_the_captions_of_an_earlier_run(self):
        self.make("CLEAN")
        before = [(s.name, s.channel, s.text) for s in self.all_strips()]
        with mock.patch.object(self.ops.cap, "MAX_RUN_KEYS", 50):
            with self.assertRaises(self.ops.CaptionsError):
                self.make(animate="WORD", word_animation="HIGHLIGHT_POP")
        self.assertEqual([(s.name, s.channel, s.text) for s in self.all_strips()], before)

    def test_no_room_for_the_channel_block_refuses_before_anything_is_removed(self):
        self.make("CLEAN")
        tall = _clip(self.scene, "tall", "//media/tall.wav", channel=122, left=1, right=3001)
        before = [(s.name, s.channel) for s in self.all_strips()]
        with self.assertRaises(self.ops.CaptionsError) as raised:
            self.ops.add_captions(self.scene, [tall], "CLEAN", None, self.transcript, animate="WORD",
                                  word_animation="POP")
        self.assertIn("channels", str(raised.exception))
        self.assertEqual([(s.name, s.channel) for s in self.all_strips()], before)

    def test_a_speakers_label_is_a_word_strip_too_since_the_anchor_draws_nothing(self):
        result = self.make(animate="WORD", word_animation="HIGHLIGHT_RISE", speakers=True, speaker_labels=True)
        self.assertEqual(result["speakers"][0]["name"], "Speech")
        line = self.lines()[0]
        words = self.words_of(line)
        self.assertEqual([w.text for w in words], line.text.split())
        self.assertEqual(words[0].text, "Speech:")
        self.assertEqual(len({w.channel for w in words}), len(words))
        self.assertEqual(self.ops.refit_words(self.scene), 0)
        line.use_bold = True
        self.assertEqual(self.ops.refit_words(self.scene), 1)
        self.assertTrue(all(w.use_bold for w in self.words_of(self.scene.sequence_editor.strips.get(line.name))))

    def test_the_old_animation_argument_still_animates_the_line_as_before(self):
        self.make("CLEAN", animation="FADE")
        line = self.lines()[0]
        self.assertTrue(any(p == "blend_alpha" for p, *_ in line.keys))
        self.assertEqual(self.words(), [])
        tag = line.get("bfacw_caption")
        self.assertEqual((tag["anim"], tag["wanim"]), ("FADE", "NONE"))
        self.make("WORD_HIGHLIGHT", animation="SLIDE_UP")                    # the styles words and the line's keys
        self.assertTrue(self.words())
        self.assertTrue(all(any(p == "transform.offset_y" for p, *_ in w.keys) for w in self.words()))

    def test_the_tool_takes_the_new_choices_and_the_old_one(self):
        out = self.ops.tool_add_captions(["speech"], "clean", "en", self.transcript, animate="per word",
                                         word_animation="highlight + rise")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["word_animation"], "Highlight + Rise")
        self.assertIn("Highlight + Rise", out["message"])
        old = self.ops.tool_add_captions(["speech"], "clean", "en", self.transcript, animation="typewriter")
        self.assertEqual(old["status"], "ok", old)
        self.assertEqual(old["animation"], "Typewriter")
        bad = self.ops.tool_add_captions(["speech"], "clean", "en", self.transcript, word_animation="wobble")
        self.assertEqual(bad["status"], "error")
        self.assertIn("word_animation", bad["message"])
        with mock.patch.object(self.ops.cap, "MAX_WORD_CHANNELS", 2):
            refused = self.ops.tool_add_captions(["speech"], "clean", "en", self.transcript, animate="word",
                                                 word_animation="pop")
        self.assertEqual(refused["status"], "error")
        self.assertIn("limit", refused["message"])


class TestMotionFollowsTheAnchor(_MotionCase):

    def setUp(self):
        super().setUp()
        self.make(animate="BOTH", line_animation="FADE", word_animation="HIGHLIGHT_POP")
        self.line = self.lines()[0]
        self.name = self.line.name

    def lookup(self):
        return self.scene.sequence_editor.strips.get(self.name)

    def test_bold_and_the_font_on_the_hidden_anchor_move_the_words(self):
        before = [w.location[0] for w in self.words_of(self.line)]
        self.line.use_bold = True
        font = SimpleNamespace(name="Other", filepath="//other.ttf")
        self.line.font = font
        self.assertEqual(self.ops.refit_words(self.scene), 1)
        words = self.words_of(self.lookup())
        self.assertTrue(all(w.use_bold and w.font is font for w in words))
        self.assertNotEqual([w.location[0] for w in words], before)
        self.assertEqual(self.ops.refit_words(self.scene), 0)
        self.assertEqual(self.lookup().color[3], 0.0)

    def test_the_refit_only_moves_and_keys_again_it_adds_and_removes_nothing(self):
        names = [s.name for s in self.all_strips()]
        self.line.font_size = 90
        self.ops.refit_words(self.scene)
        self.assertEqual([s.name for s in self.all_strips()], names)
        word = self.words_of(self.lookup())[2]
        self.assertTrue(word.keys)

    def test_a_pop_keeps_the_word_on_its_new_middle_after_a_bigger_size(self):
        self.line.font_size = 120
        self.ops.refit_words(self.scene)
        word = self.words_of(self.lookup())[2]
        offsets = [v for p, _f, v in word.keys if p == "transform.offset_x"]
        scales = [v for p, _f, v in word.keys if p == "transform.scale_x"]
        self.assertTrue(offsets and scales)
        width = len(word.text) * word.font_size * 0.5
        middle = word.location[0] * 1920 + width / 2
        # the last keys written belong to the run after the size change: they hold this middle
        got = 960 + scales[-1] * (middle - 960) + offsets[-1]
        self.assertAlmostEqual(got, middle, 0)

    def test_the_text_of_the_caption_changed_makes_the_words_again(self):
        """Edit by Transcript and Translate write the text through ``set_caption_text``."""
        self.ops.set_caption_text(self.scene, self.lookup(), "Brand new words here now")
        line = self.lookup()
        words = self.words_of(line)
        self.assertEqual([w.text for w in words], ["Brand", "new", "words", "here", "now"])
        self.assertEqual(line.color[3], 0.0)
        self.assertEqual([w.channel for w in words], list(range(line.channel + 1, line.channel + 6)))
        self.assertTrue(all(w.keys for w in words))
        self.assertEqual(line.keys, [])
        self.assertEqual(len({w.name for w in self.words()}), len(self.words()))

    def test_text_changes_three_times_in_a_row_never_leave_a_stale_strip(self):
        for text in ("one two", "alpha beta gamma delta", "last"):
            self.ops.set_caption_text(self.scene, self.lookup(), text)
            line = self.lookup()
            self.assertEqual([w.text for w in self.words_of(line)], text.split())
        self.assertEqual(len([w for w in self.words() if w.get("bfacw_caption")["line"] == self.name]), 1)

    def test_make_vertical_makes_the_words_again_for_the_new_frame(self):
        self.scene.render.resolution_x, self.scene.render.resolution_y = 1080, 1920
        out = self.ops.relayout_captions(self.scene, old_res=(1920, 1080))
        self.assertGreater(out["laid_out"], 0)
        for line in self.lines():
            self.assertEqual(line.color[3], 0.0)
            words = self.words_of(line)
            self.assertTrue(words)
            self.assertEqual("".join(w.text for w in words), line.text.replace(" ", "").replace("\n", ""))
            self.assertTrue(all(w.keys for w in words))
        self.assertEqual(len({s.name for s in self.all_strips()}), len(self.all_strips()))

    def test_remove_silences_refit_keys_the_words_again_over_the_new_frames(self):
        self.lookup().right_handle -= 6
        word = self.words_of(self.lookup())[1]
        seen = len(word.keys)
        self.assertTrue(self.ops.refit_captions(self.scene, [self.name]))
        frames = [f for p, f, _v in word.keys[seen:] if p == "blend_alpha"]       # the keys of this run
        self.assertTrue(frames)
        self.assertLessEqual(max(frames), self.lookup().right_handle)

    def test_a_signature_edit_by_hand_still_reads_the_anchor(self):
        line = self.lookup()
        self.assertFalse(self.ops._edited(line))
        line.text = "reworded by hand"
        self.assertTrue(self.ops._edited(line))


def _copy_strip(strips, src, name, left, right):
    """A hard cut's second piece: a copy of strip *src* with its tag, over *left* to *right*."""
    import copy
    piece = type(src)(name, src.type, src.channel, left, right)
    piece.__dict__.update({k: copy.copy(v) for k, v in src.__dict__.items()
                           if k not in ("name", "left_handle", "right_handle", "_props", "keys")})
    piece._props = {k: type(v)(dict(v)) if isinstance(v, dict) else v for k, v in src._props.items()}
    piece.keys = list(src.keys)
    strips.append(piece)
    return piece


class TestAfterACut(_MotionCase):
    """Remove Silences moves the line's ``word_frames``; every word's keys follow them (one source of time)."""

    def setUp(self):
        super().setUp()
        self.make(animate="WORD", word_animation="FADE_IN")
        self.name = max(self.lines(), key=lambda ln: len(ln.text.split())).name

    def line(self):
        return self.scene.sequence_editor.strips.get(self.name)

    def cut(self, k, length):
        """A cut of *length* frames that starts at word *k*'s start, as the ripple leaves the tag and strips."""
        line = self.line()
        tag = line.get("bfacw_caption")
        wf = list(tag["word_frames"])
        pos = wf[k]
        tag["word_frames"] = [v if v < pos else (pos if v < pos + length else v - length) for v in wf]
        line["bfacw_caption"] = tag
        for s in [line] + self.words_of(line):
            s.right_handle -= length
        return list(tag["word_frames"])

    def first_alpha(self, word, since):
        return [f for p, f, _v in word.keys[since:] if p == "blend_alpha"]

    def check_cut(self, k, length):
        line = self.line()
        words = self.words_of(line)
        seen = [len(w.keys) for w in words]
        wf = self.cut(k, length)
        self.assertTrue(self.ops.refit_captions(self.scene, [self.name]))
        line = self.line()
        fs, fe = line.left_handle, line.right_handle
        for i, w in enumerate(self.words_of(line)):
            alpha = self.first_alpha(w, seen[i])
            want = min(fs + wf[i], fe)
            if fe - want >= 2:
                self.assertEqual(alpha[0], want, (k, length, i))
            self.assertTrue(all(fs <= f <= fe for f in alpha), (k, length, i, alpha))

    def test_a_cut_before_the_first_middle_and_last_word(self):
        n = len(self.words_of(self.line()))
        for k in (1, n // 2, n - 1):
            self.check_cut(k, 4)

    def test_a_cut_longer_than_a_word_never_shows_a_word_from_the_first_frame(self):
        n = len(self.words_of(self.line()))
        line = self.line()
        self.check_cut(n - 2, 40)
        line = self.line()
        words = self.words_of(line)
        last = self.first_alpha(words[-1], 0)[-6:]
        self.assertTrue(any(f > line.left_handle + 3 for f in last))

    def test_a_hard_cut_gives_each_word_to_the_piece_it_is_in(self):
        line = self.line()
        words = self.words_of(line)
        mid = line.left_handle + (line.right_handle - line.left_handle) // 2
        strips = self.scene.sequence_editor.strips
        right = line.right_handle
        piece = _copy_strip(strips, line, line.name + ".001", mid, right)
        piece.get("bfacw_caption")["word_frames"] = list(line.get("bfacw_caption")["word_frames"])
        line.right_handle = mid
        for w in words:
            copy = _copy_strip(strips, w, w.name + ".001", mid, right)
            w.right_handle = mid
            self.assertEqual(copy.get("bfacw_caption")["line"], line.name)       # both name the first anchor
        self.assertGreaterEqual(self.ops.refit_words(self.scene), 1)
        second = [w for w in self.words() if w.get("bfacw_caption")["line"] == piece.name]
        first = [w for w in self.words() if w.get("bfacw_caption")["line"] == line.name]
        self.assertEqual((len(first), len(second)), (len(words), len(words)))
        self.assertTrue(all(w.left_handle == mid for w in second))
        shift = mid - line.left_handle
        self.assertEqual(piece.get("bfacw_caption")["word_frames"],
                         [v - shift for v in line.get("bfacw_caption")["word_frames"]])
        self.assertEqual(piece.color[3], 0.0)


class TestNeverAClearCaptionWithoutWords(_MotionCase):

    def setUp(self):
        super().setUp()
        self.make(animate="WORD", word_animation="POP")
        self.line = self.lines()[0]
        self.name = self.line.name

    def lookup(self):
        return self.scene.sequence_editor.strips.get(self.name)

    def test_a_longer_text_than_the_limit_falls_back_to_highlight_and_says_why(self):
        before = len(self.all_strips())
        del self.ops.NOTES[:]
        self.ops.set_caption_text(self.scene, self.lookup(), " ".join("w%d" % i for i in range(30)))
        line = self.lookup()
        self.assertEqual(line.color[3], 1.0)                                    # the caption draws again
        self.assertEqual(line.get("bfacw_caption")["wanim"], "HIGHLIGHT")
        words = self.words_of(line)
        self.assertEqual(len(words), 30)
        self.assertEqual({w.channel for w in words}, {line.channel + 1})
        self.assertTrue(self.ops.NOTES and "24" in self.ops.NOTES[0], self.ops.NOTES)
        self.assertGreater(before, 0)

    def test_somebody_elses_strip_on_the_block_is_not_run_over(self):
        widest = max(len(ln.text.split()) for ln in self.lines())
        line = self.lookup()
        top = line.channel + len(self.words_of(line))
        music = _clip(self.scene, "music", "//media/music.wav", channel=top + 1, left=line.left_handle,
                      right=line.right_handle)
        del self.ops.NOTES[:]
        self.ops.set_caption_text(self.scene, line, " ".join("w%d" % i for i in range(len(self.words_of(line)) + 2)))
        line = self.lookup()
        self.assertTrue(all(w.channel != music.channel for w in self.words_of(self.lookup())))
        self.assertEqual(music.channel, top + 1)
        self.assertEqual(line.color[3], 1.0)
        self.assertIn("channel {:d}".format(music.channel), self.ops.NOTES[0])
        self.assertGreater(widest, 0)

    def test_with_no_room_even_for_highlight_it_is_plain(self):
        with mock.patch.object(self.ops, "_block_problem", return_value="no room"):
            self.ops.set_caption_text(self.scene, self.lookup(), "one two three")
        line = self.lookup()
        self.assertEqual((line.color[3], line.get("bfacw_caption")["wanim"]), (1.0, "NONE"))
        self.assertEqual(self.words_of(line), [])

    def test_the_text_that_fits_keeps_its_motion(self):
        self.ops.set_caption_text(self.scene, self.lookup(), "one two three")
        line = self.lookup()
        self.assertEqual((line.color[3], len(self.words_of(line))), (0.0, 3))

    def test_an_anchor_whose_words_are_all_gone_draws_again_from_the_timer_path(self):
        for w in self.words_of(self.lookup()):
            self.scene.sequence_editor.strips.remove(w)
        self.assertGreaterEqual(self.ops.refit_words(self.scene), 1)
        self.assertEqual(self.lookup().color[3], 1.0)
        self.assertEqual(self.lookup().get("bfacw_caption")["wanim"], "NONE")
        self.assertEqual(self.words_of(self.lookup()), [])

    def test_refit_word_highlights_makes_the_words_again(self):
        for w in self.words_of(self.lookup()):
            self.scene.sequence_editor.strips.remove(w)
        self.assertGreaterEqual(self.ops.refit_words(self.scene, [self.name], force=True, rebuild=True), 1)
        line = self.lookup()
        self.assertEqual(line.color[3], 0.0)
        self.assertEqual([w.text for w in self.words_of(line)], line.text.split())

    def test_make_vertical_never_leaves_a_piece_clear_without_words(self):
        self.scene.render.resolution_x, self.scene.render.resolution_y = 1080, 1920
        with mock.patch.object(self.ops, "_add_highlights", return_value=0):
            self.ops.relayout_captions(self.scene, old_res=(1920, 1080))
        for ln in self.lines():
            self.assertEqual(ln.color[3], 1.0, ln.name)

    def test_names_of_channels_the_artist_made_are_kept(self):
        self.scene.sequence_editor.channels[self.lookup().channel + 1].name = "Mine"
        self.make(animate="WORD", word_animation="RISE")
        line = self.lines()[0]
        self.assertEqual(self.scene.sequence_editor.channels[line.channel + 1].name, "Mine")
        self.assertEqual(self.scene.sequence_editor.channels[line.channel + 2].name, "Captions Highlight")


class TestDialog(_OpsCase):

    def test_the_dialog_has_animate_with_its_two_lists_in_the_motion_group(self):
        op = test_captions.TestCaptionStrips._operator(self)[0]
        _clip(self.scene)
        for animate, want in (("STYLE", []), ("NONE", []), ("LINE", ["line_animation"]),
                              ("WORD", ["word_animation"]), ("BOTH", ["line_animation", "word_animation"])):
            op.animate = animate
            layout = test_captions._Layout()
            op.layout = layout
            op.draw(SimpleNamespace(scene=self.scene))
            props = [e[1] for e in layout.log if e[0] == "prop"]
            self.assertEqual([p for p in props if p in ("line_animation", "word_animation")], want, animate)
            self.assertLess(props.index("size"), props.index("animate"))            # style group first
            self.assertLess(props.index("animate"), props.index("language"))

    def test_what_the_dialog_passes_on(self):
        op = test_captions.TestCaptionStrips._operator(self)[0]
        op.animate, op.line_animation, op.word_animation = "BOTH", "SLIDE_DOWN", "HIGHLIGHT_RISE"
        look = op._look(self.scene, [])
        self.assertEqual((look["animate"], look["line_animation"], look["word_animation"]),
                         ("BOTH", "SLIDE_DOWN", "HIGHLIGHT_RISE"))
        op.animate, op.animation = "STYLE", "FADE"                 # the old single list still counts
        look = op._look(self.scene, [])
        self.assertEqual((look["animate"], look["line_animation"], look["word_animation"], look["animation"]),
                         ("STYLE", None, None, "FADE"))

    def test_lines_count_one_for_a_typewriter_of_the_visible_line_only(self):
        lines = lambda **kw: self.ops._lines_items(SimpleNamespace(**dict(dict(style="CLEAN", animate="STYLE"), **kw)),
                                                   None)[0][1]
        self.assertEqual(lines(), "As the Style (2 Lines)")
        self.assertEqual(lines(animate="LINE", line_animation="TYPEWRITER"), "As the Style (1 Line)")
        self.assertEqual(lines(animate="BOTH", line_animation="TYPEWRITER", word_animation="POP"),
                         "As the Style (2 Lines)")
        self.assertEqual(lines(style="WORD_HIGHLIGHT"), "As the Style (1 Line)")
        self.assertEqual(lines(style="WORD_HIGHLIGHT", animate="LINE", line_animation="FADE"), "As the Style (1 Line)")
        self.assertEqual(lines(style="BOXED", animate="NONE"), "As the Style (2 Lines)")

    def test_every_entry_has_an_icon_and_a_description(self):
        for items in (self.ops._ANIMATE_ITEMS, self.ops._WORD_ITEMS, self.ops._ANIM_ITEMS):
            for item in items:
                self.assertTrue(item[1] and item[2], item)
                self.assertEqual(len(item), 5)


class TestOldTags(_OpsCase):
    """Captions made before this change: their tags have no word animation."""

    def test_an_old_word_by_word_caption_is_still_read_as_highlight_words(self):
        patch = self._measured()
        patch.start()
        self.addCleanup(patch.stop)
        clip = _clip(self.scene)
        self.ops.add_captions(self.scene, [clip], "WORD_HIGHLIGHT", None, self.transcript)
        line = self.lines()[0]
        tag = line.get("bfacw_caption")
        del tag["wanim"]
        tag["anim"] = "FADE"
        self.assertEqual(cap.tag_words(tag), ("highlight", "HIGHLIGHT"))
        line.text = "Delta echo foxtrot"
        self.ops.text_changed(line, self.scene)
        self.assertEqual([w.text for w in self.words() if w.get("bfacw_caption")["line"] == line.name],
                         ["Delta", "echo", "foxtrot"])
        self.assertEqual(line.color, (1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
