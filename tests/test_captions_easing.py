# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Easing presets and speed for the caption animations, and the icons of the Auto Captions dialog (#605).

The easing is Blender's own keyframe interpolation and easing on the keys the animation has already:
the mapping of each choice, the way out as the mirror of the way in, no key added (same key lists with
and without), steps (Typewriter's crop, the highlight colour) left alone, the speed scaled inside the
limits of #583, captions and tags from before read as As the Style / Normal, and easing and speed kept
by every path that keys the words again.  The icons: every name the dialog uses resolves on Blender 5.2.
No Blender: made-up words on the stand-in ``bpy`` of ``test_captions``.

Run with::

    python -m unittest tests.test_captions_easing -v
"""

__all__ = ()

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_captions
from test_captions import _OpsCase, cap
from test_captions_words import FPS, WIDE, _MotionCase

_ROOT = Path(__file__).resolve().parents[1]
_ADDON = _ROOT / "addon" / "bfa_coworker"
_DATA = Path(__file__).resolve().parent / "data"
EASINGS = [k for k in cap.EASINGS if k != "STYLE"]
LINES = ("FADE", "SLIDE_UP", "SLIDE_DOWN", "POP", "TYPEWRITER")
MOTIONS = ("POP", "SCALE", "RISE", "FADE_IN")


def line_tracks(animation, style="CLEAN", fs=100, fe=190, easing=None, speed=None):
    return cap.animation_tracks(animation, style, fs, fe, FPS, WIDE[0], WIDE[1], 50.0, 1, "BOTTOM", (0.5, 0.09),
                                [(1, 10), (2, 5), (3, 0)], easing, speed)


def word_tr(line="NONE", word="POP", line_easing=None, word_easing=None, speed=None, reveal=None, a=130, b=150):
    return cap.word_tracks(line, word, 100, 190, a, b, FPS, WIDE[0], WIDE[1], (1000.0, 200.0), (960.0, 200.0),
                           reveal, (1, 1, 1, 1), (1, 0.8, 0.1, 1), line_easing, word_easing, speed)


# -- the choices ------------------------------------------------------------------------------

class TestChoices(unittest.TestCase):

    def test_the_names_follow_blenders_graph_editor(self):
        self.assertEqual([e["label"] for e in cap.EASINGS.values()],
                         ["As the Style", "Linear", "Ease In", "Ease Out", "Ease In and Out", "Back", "Bounce",
                          "Elastic"])
        self.assertEqual([s["label"] for s in cap.SPEEDS.values()], ["Fast", "Normal", "Slow"])
        for text, key in (("ease out", "EASE_OUT"), ("Ease In and Out", "EASE_IN_OUT"), ("bounce", "BOUNCE"),
                          (None, "STYLE"), ("", "STYLE"), ("as the style", "STYLE"), ("elastic", "ELASTIC")):
            self.assertEqual(cap.easing_key(text), key, text)
        self.assertIsNone(cap.easing_key("wobbly"))
        self.assertEqual((cap.speed_key("slow"), cap.speed_key(None), cap.speed_key("Fast")),
                         ("SLOW", "NORMAL", "FAST"))
        self.assertIsNone(cap.speed_key("warp"))

    def test_every_choice_is_a_blender_interpolation_and_easing(self):
        interpolations = {"LINEAR", "SINE", "QUAD", "CUBIC", "QUART", "QUINT", "EXPO", "CIRC", "BACK", "BOUNCE",
                          "ELASTIC"}
        easings = {"AUTO", "EASE_IN", "EASE_OUT", "EASE_IN_OUT"}
        self.assertEqual(set(cap.EASING_TYPES), set(cap.EASINGS) - {"STYLE"})
        for key, (interp, ease) in cap.EASING_TYPES.items():
            self.assertIn(interp, interpolations, key)
            self.assertIn(ease, easings, key)
        self.assertEqual(cap.EASING_TYPES["EASE_IN"], ("CUBIC", "EASE_IN"))
        self.assertEqual(cap.EASING_TYPES["EASE_OUT"], ("CUBIC", "EASE_OUT"))
        self.assertEqual(cap.EASING_TYPES["EASE_IN_OUT"], ("CUBIC", "EASE_IN_OUT"))
        self.assertEqual(cap.EASING_TYPES["LINEAR"][0], "LINEAR")

    def test_the_tool_checks_its_arguments(self):
        self.assertEqual(cap.check_easing_params("ease out", None, "slow"), ("EASE_OUT", None, "SLOW"))
        self.assertEqual(cap.check_easing_params(None, "", ""), (None, None, None))
        for args, word in ((("sideways", None, None), "line_easing"), ((None, "x", None), "word_easing"),
                           ((None, None, "warp"), "speed")):
            with self.assertRaises(cap.CaptionsError) as ctx:
                cap.check_easing_params(*args)
            self.assertIn(word, str(ctx.exception))

    def test_a_tag_from_before_reads_as_the_style_and_normal(self):
        self.assertEqual(cap.tag_easing({}), ("STYLE", "STYLE", "NORMAL"))
        self.assertEqual(cap.tag_easing(None), ("STYLE", "STYLE", "NORMAL"))
        self.assertEqual(cap.tag_easing({"anim": "FADE", "wanim": "POP"}), ("STYLE", "STYLE", "NORMAL"))
        self.assertEqual(cap.tag_easing({"ease": "BACK", "wease": "BOUNCE", "speed": "SLOW"}),
                         ("BACK", "BOUNCE", "SLOW"))
        self.assertEqual(cap.tag_easing({"ease": "junk", "speed": 3}), ("STYLE", "STYLE", "NORMAL"))


class TestWhatThereIsToEase(unittest.TestCase):

    def plan(self, style="CLEAN", **kw):
        return cap.resolve_animation(style, **kw)

    def test_a_line_animation_has_something_to_ease_none_and_highlight_have_not(self):
        for line in LINES:
            self.assertTrue(cap.line_eases(self.plan(animate="LINE", line=line)), line)
        self.assertFalse(cap.line_eases(self.plan(animate="LINE", line="NONE")))
        self.assertFalse(cap.line_eases(self.plan(animate="NONE")))
        self.assertFalse(cap.line_eases(self.plan(animate="STYLE")))                    # Clean: the style stands still
        self.assertTrue(cap.line_eases(self.plan("BOLD_POP", animate="STYLE"), "BOLD_POP"))     # Bold Pop's own pop

    def test_only_a_word_motion_has_something_to_ease(self):
        self.assertFalse(cap.words_ease(self.plan(animate="WORD", word="HIGHLIGHT")))
        self.assertFalse(cap.words_ease(self.plan(animate="WORD", word="NONE")))
        for m in MOTIONS:
            self.assertTrue(cap.words_ease(self.plan(animate="WORD", word=m)), m)
            self.assertTrue(cap.words_ease(self.plan(animate="WORD", word="HIGHLIGHT_" + m)), m)

    def test_what_is_kept_for_the_tag(self):
        p = cap.apply_easing(self.plan(animate="BOTH", line="FADE", word="POP"), "BACK", "BOUNCE", "SLOW")
        self.assertEqual((p["ease"], p["wease"], p["speed"]), ("BACK", "BOUNCE", "SLOW"))
        p = cap.apply_easing(self.plan(animate="WORD", word="HIGHLIGHT"), "BACK", "BOUNCE", "SLOW")
        self.assertEqual((p["ease"], p["wease"], p["speed"]), ("STYLE", "STYLE", "NORMAL"))     # nothing to ease
        p = cap.apply_easing(self.plan(animate="LINE", line="TYPEWRITER"), "EASE_OUT", "BOUNCE", "FAST")
        self.assertEqual((p["ease"], p["wease"], p["speed"]), ("EASE_OUT", "STYLE", "FAST"))
        p = cap.apply_easing(self.plan(animate="NONE"), "LINEAR", None, "FAST")
        self.assertEqual((p["ease"], p["speed"]), ("STYLE", "NORMAL"))
        p = cap.apply_easing(self.plan(animate="LINE", line="FADE"))                           # nothing chosen
        self.assertEqual((p["ease"], p["wease"], p["speed"]), ("STYLE", "STYLE", "NORMAL"))


# -- the key lists ----------------------------------------------------------------------------

class TestLineEasing(unittest.TestCase):

    def test_as_the_style_is_exactly_todays_curves(self):
        for line in LINES + ("STYLE",):
            for style in ("CLEAN", "BOLD_POP"):
                plain = cap.animation_tracks(line, style, 100, 190, FPS, 1920, 1080, 50.0, 1, "BOTTOM", (0.5, 0.09),
                                             [(1, 10), (2, 5), (3, 0)])
                self.assertEqual(line_tracks(line, style, easing="STYLE", speed="NORMAL"), plain, line)
                self.assertEqual(line_tracks(line, style), plain, line)
                self.assertTrue(all("easing" not in t for t in plain), line)

    def test_the_same_keys_with_and_without_easing(self):
        for line in LINES + ("STYLE",):
            for style in ("CLEAN", "BOLD_POP"):
                plain = line_tracks(line, style)
                for e in EASINGS:
                    eased = line_tracks(line, style, easing=e)
                    self.assertEqual([(t["path"], t["keys"], t["steady"], t["interpolation"]) for t in eased],
                                     [(t["path"], t["keys"], t["steady"], t["interpolation"]) for t in plain],
                                     (line, style, e))

    def test_each_smooth_key_gets_a_blender_interpolation_and_easing(self):
        for e in EASINGS:
            for t in line_tracks("SLIDE_UP", easing=e):
                self.assertEqual([f for f, _i, _e in t["easing"]], [f for f, _v in t["keys"]], (e, t["path"]))
                self.assertTrue(all(i == cap.EASING_TYPES[e][0] for _f, i, _e in t["easing"]))

    def test_a_way_in_slows_into_place_with_ease_out_and_the_way_out_is_mirrored(self):
        alpha = {t["path"]: t for t in line_tracks("FADE", easing="EASE_OUT")}["blend_alpha"]
        self.assertEqual(len(alpha["keys"]), 4)
        kinds = [e for _f, _i, e in alpha["easing"]]
        self.assertEqual(kinds[0], "EASE_OUT")      # the way in: fast then soft into place
        self.assertEqual(kinds[2], "EASE_IN")       # the way out: soft start, then away (the way in backwards)
        alpha = {t["path"]: t for t in line_tracks("FADE", easing="EASE_IN")}["blend_alpha"]
        self.assertEqual([e for _f, _i, e in alpha["easing"]][0::2], ["EASE_IN", "EASE_OUT"])
        for e in ("EASE_IN_OUT", "LINEAR"):         # symmetric: nothing to mirror
            alpha = {t["path"]: t for t in line_tracks("FADE", easing=e)}["blend_alpha"]
            self.assertEqual(len({k for _f, _i, k in alpha["easing"]}), 1)

    def test_back_bounce_and_elastic_land_on_the_way_in_and_pull_back_on_the_way_out(self):
        for e in ("BACK", "BOUNCE", "ELASTIC"):
            alpha = {t["path"]: t for t in line_tracks("FADE", easing=e)}["blend_alpha"]
            self.assertEqual([(i, k) for _f, i, k in alpha["easing"]][0::2], [(e, "EASE_OUT"), (e, "EASE_IN")], e)

    def test_steps_stay_constant_and_get_no_easing(self):
        for e in EASINGS:
            crop = {t["path"]: t for t in line_tracks("TYPEWRITER", easing=e)}
            self.assertEqual(crop["crop.max_x"]["interpolation"], "CONSTANT")
            self.assertNotIn("easing", crop["crop.max_x"])
            self.assertIn("easing", crop["blend_alpha"])        # its fade out is smooth

    def test_bold_pops_own_pop_can_be_eased_too(self):
        (t,) = line_tracks("STYLE", "BOLD_POP", easing="BOUNCE")
        self.assertEqual(t["easing"][0][1:], ("BOUNCE", "EASE_OUT"))
        self.assertEqual(len(t["keys"]), 2)

    def test_pop_keys_the_offset_with_the_same_easing_as_the_scale(self):
        ts = {t["path"]: t for t in line_tracks("POP", easing="BACK")}
        self.assertEqual(ts["transform.scale_x"]["easing"], ts["transform.offset_y"]["easing"])


class TestSpeed(unittest.TestCase):

    def old_windows(self, length, fps):
        n_in, n_out = min(int(round(0.25 * fps)), length // 3), min(int(round(0.2 * fps)), length // 4)
        return (n_in if n_in >= 2 else 0), (n_out if n_out >= 2 else 0)

    def test_normal_is_todays_windows(self):
        for fps in (12.0, 24.0, 25.0, 30.0, 60.0):
            for length in range(1, 400):
                self.assertEqual(cap._windows(length, fps), self.old_windows(length, fps), (length, fps))
                self.assertEqual(cap._windows(length, fps, "NORMAL"), self.old_windows(length, fps))

    def test_fast_is_shorter_and_slow_longer(self):
        fast, normal, slow = (cap._windows(300, 30.0, s) for s in ("FAST", "NORMAL", "SLOW"))
        self.assertLess(fast[0], normal[0] )
        self.assertLess(normal[0], slow[0])
        self.assertLess(fast[1], normal[1])
        self.assertLess(normal[1], slow[1])

    def test_every_speed_stays_inside_the_limits_of_583(self):
        for speed in ("FAST", "NORMAL", "SLOW"):
            for fps in (12.0, 24.0, 30.0, 60.0):
                for length in range(1, 600):
                    n_in, n_out = cap._windows(length, fps, speed)
                    self.assertLessEqual(n_in, length // 3, (speed, fps, length))      # a way in: a third at most
                    self.assertLessEqual(n_out, length // 4, (speed, fps, length))     # a way out: a quarter
                    self.assertIn(n_in, range(0, length + 1))
                    self.assertTrue(n_in == 0 or n_in >= 2)
                    self.assertTrue(n_out == 0 or n_out >= 2)
                    self.assertLessEqual(n_in + n_out, length)

    def test_a_caption_too_short_to_animate_still_is_not(self):
        for speed in ("FAST", "SLOW"):
            self.assertEqual(line_tracks("FADE", fs=10, fe=13, speed=speed), [])

    def test_the_line_keys_follow_the_speed_and_their_count_stays(self):
        counts = {s: sum(len(t["keys"]) for t in line_tracks("SLIDE_UP", speed=s)) for s in ("FAST", "NORMAL", "SLOW")}
        self.assertEqual(len(set(counts.values())), 1)
        first = {s: {t["path"]: t for t in line_tracks("FADE", speed=s)}["blend_alpha"]["keys"][1][0]
                 for s in ("FAST", "NORMAL", "SLOW")}
        self.assertLess(first["FAST"], first["NORMAL"])
        self.assertLess(first["NORMAL"], first["SLOW"])

    def test_no_speed_makes_a_word_need_more_keys_than_the_limit_counts(self):
        most = cap.word_key_count("SLIDE_UP", "HIGHLIGHT_POP")
        for speed in ("FAST", "NORMAL", "SLOW"):
            for a in (100, 130, 160, 186):
                n = sum(len(t["keys"]) * (4 if t["path"] == "color" else 1)
                        for t in word_tr("SLIDE_UP", "HIGHLIGHT_POP", speed=speed, a=a, b=a + 20))
                self.assertLessEqual(n, most, (speed, a))

    def test_the_word_motion_follows_the_speed_inside_the_caption(self):
        ends = {s: {t["path"]: t for t in word_tr(word="FADE_IN", speed=s)}["blend_alpha"]["keys"][-1][0]
                for s in ("FAST", "NORMAL", "SLOW")}
        self.assertLess(ends["FAST"], ends["NORMAL"])
        self.assertLess(ends["NORMAL"], ends["SLOW"])
        late = {t["path"]: t for t in word_tr(word="FADE_IN", speed="SLOW", a=186, b=190)}["blend_alpha"]
        self.assertLessEqual(max(f for f, _v in late["keys"]), 190)


class TestWordEasing(unittest.TestCase):

    def test_as_the_style_is_exactly_todays_curves(self):
        for line in ("NONE", "FADE", "SLIDE_UP", "POP", "TYPEWRITER"):
            for word in ("HIGHLIGHT",) + MOTIONS + tuple("HIGHLIGHT_" + m for m in MOTIONS):
                plain = word_tr(line, word, reveal=120)
                self.assertEqual(word_tr(line, word, "STYLE", "STYLE", "NORMAL", reveal=120), plain, (line, word))
                self.assertTrue(all("easing" not in t for t in plain))

    def test_the_same_keys_with_and_without_easing(self):
        for line in ("NONE", "FADE", "SLIDE_DOWN", "POP", "TYPEWRITER"):
            for word in MOTIONS + tuple("HIGHLIGHT_" + m for m in MOTIONS):
                plain = word_tr(line, word, reveal=120)
                for le in ("BACK", "EASE_OUT"):
                    for we in EASINGS:
                        eased = word_tr(line, word, le, we, reveal=120)
                        strip = lambda ts: [(t["path"], t["keys"], t["steady"], t["interpolation"]) for t in ts]
                        self.assertEqual(strip(eased), strip(plain), (line, word, le, we))
                        n = sum(len(t["keys"]) for t in plain)
                        self.assertEqual(sum(len(t["keys"]) for t in eased), n)

    def test_the_word_motion_gets_the_word_easing_and_the_line_animation_the_line_easing(self):
        ts = {t["path"]: t for t in word_tr("FADE", "POP", "LINEAR", "BOUNCE")}
        scale = ts["transform.scale_x"]
        self.assertTrue(all(i == "BOUNCE" for _f, i, _e in scale["easing"][:-1]))
        alpha = ts["blend_alpha"]            # the line's fade out only: the word's pop has no alpha beyond a step
        self.assertTrue(any(i == "LINEAR" for _f, i, _e in alpha["easing"]))

    def test_a_late_words_motion_does_not_take_the_lines_way_out(self):
        # the last word is spoken where the caption's fade out starts: that key is the line's, so it is mirrored
        ts = {t["path"]: t for t in word_tr("FADE", "POP", "EASE_OUT", "BACK", a=180, b=190)}
        out = [(f, i, e) for f, i, e in ts["blend_alpha"]["easing"] if f >= 190 - cap._windows(90, FPS)[1]]
        self.assertTrue(out)
        self.assertTrue(all((i, e) == ("CUBIC", "EASE_IN") for _f, i, e in out), out)
        scale = ts["transform.scale_x"]["easing"]               # the pop itself keeps the word's easing
        self.assertTrue(all(i == "BACK" for _f, i, _e in scale), scale)

    def test_the_colour_stays_constant_and_unease(self):
        ts = {t["path"]: t for t in word_tr("NONE", "HIGHLIGHT_POP", "BACK", "BOUNCE")}
        self.assertEqual(ts["color"]["interpolation"], "CONSTANT")
        self.assertNotIn("easing", ts["color"])
        ts = {t["path"]: t for t in word_tr("NONE", "HIGHLIGHT", "BACK", "BOUNCE")}
        self.assertEqual(list(ts), ["color"])
        self.assertNotIn("easing", ts["color"])

    def test_scale_grows_with_ease_out_and_settles_back_with_the_mirror(self):
        scale = {t["path"]: t for t in word_tr("NONE", "SCALE", None, "EASE_OUT")}["transform.scale_x"]
        self.assertEqual([k for _f, _i, k in scale["easing"]][:2], ["EASE_OUT", "EASE_IN"])

    def test_easing_entries_name_only_frames_the_track_has(self):
        for word in MOTIONS:
            for t in word_tr("SLIDE_UP", word, "ELASTIC", "BACK"):
                if "easing" in t:
                    self.assertLessEqual({f for f, _i, _e in t["easing"]}, {f for f, _v in t["keys"]})


# -- the strips -------------------------------------------------------------------------------

class _Modes(_MotionCase):
    """``add_captions`` with the interpolation and easing each strip's keys were given recorded."""

    def setUp(self):
        super().setUp()
        self.modes = {}
        original = self.ops._set_modes

        def record(scene, name, modes):
            self.modes.setdefault(name, {}).update(modes)
            return original(scene, name, modes)
        patch = mock.patch.object(self.ops, "_set_modes", record)
        patch.start()
        self.addCleanup(patch.stop)

    def eased(self, strip):
        return {path: m for path, m in self.modes.get(strip.name, {}).items() if not isinstance(m, str)}


class TestTheCaption(_Modes):

    def test_left_alone_the_tag_and_the_keys_are_as_before(self):
        self.make(animate="LINE", line_animation="FADE")
        for line in self.lines():
            tag = line.get("bfacw_caption")
            self.assertTrue(all(k not in tag for k in ("ease", "wease", "speed")))
            self.assertEqual(self.eased(line), {})
            self.assertEqual(set(self.modes[line.name].values()), {"BEZIER"})

    def test_the_line_easing_is_on_the_keys_and_in_the_tag(self):
        result = self.make(animate="LINE", line_animation="SLIDE_UP", line_easing="BOUNCE", speed="SLOW")
        self.assertEqual((result["line_easing"], result["word_easing"], result["speed"]),
                         ("Bounce", "As the Style", "Slow"))
        for line in self.lines():
            tag = line.get("bfacw_caption")
            self.assertEqual((tag["ease"], tag["speed"]), ("BOUNCE", "SLOW"))
            self.assertNotIn("wease", tag)
            modes = self.eased(line)
            self.assertEqual(set(modes), {"blend_alpha", "transform.offset_y"})
            for m in modes.values():
                self.assertTrue(all(i == "BOUNCE" for i, _e in m.values()))

    def test_the_word_easing_is_on_each_words_keys(self):
        self.make(animate="WORD", word_animation="POP", word_easing="ELASTIC")
        for line in self.lines():
            self.assertEqual(line.get("bfacw_caption")["wease"], "ELASTIC")
            self.assertEqual(self.eased(line), {})             # the anchor has no keys
            for w in self.words_of(line):
                modes = self.eased(w)
                self.assertIn("transform.scale_x", modes)
                self.assertTrue(all(i == "ELASTIC" for _f, (i, _e) in
                                    ((f, v) for m in modes.values() for f, v in m.items())))

    def test_no_key_is_added_by_an_easing(self):
        self.make(animate="BOTH", line_animation="FADE", word_animation="HIGHLIGHT_RISE", speed="SLOW")
        plain = sorted((w.name, len(w.keys)) for w in self.words())
        self.make(animate="BOTH", line_animation="FADE", word_animation="HIGHLIGHT_RISE", line_easing="BACK",
                  word_easing="BOUNCE", speed="SLOW")
        self.assertEqual(sorted((w.name, len(w.keys)) for w in self.words()), plain)

    def test_nothing_to_ease_keeps_nothing_in_the_tag(self):
        self.make(animate="WORD", word_animation="HIGHLIGHT", word_easing="BACK", line_easing="BOUNCE", speed="SLOW")
        for line in self.lines():
            tag = line.get("bfacw_caption")
            self.assertTrue(all(k not in tag for k in ("ease", "wease", "speed")))

    def test_the_tool_takes_the_arguments(self):
        out = self.ops.tool_add_captions(["speech"], "clean", "en", self.transcript, animate="both",
                                         line_animation="fade", word_animation="rise", line_easing="ease out",
                                         word_easing="back", speed="fast")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual((out["line_easing"], out["word_easing"], out["speed"]), ("Ease Out", "Back", "Fast"))
        self.assertIn("easing: Ease Out", out["message"])
        self.assertIn("speed: fast", out["message"])
        bad = self.ops.tool_add_captions(["speech"], "clean", "en", self.transcript, line_easing="wobbly")
        self.assertEqual(bad["status"], "error")
        self.assertIn("line_easing", bad["message"])

    def test_the_tool_without_them_says_nothing_of_them(self):
        out = self.ops.tool_add_captions(["speech"], "clean", "en", self.transcript, animate="line",
                                         line_animation="fade")
        self.assertEqual(out["status"], "ok", out)
        self.assertNotIn("easing", out["message"])
        self.assertNotIn("speed", out["message"])


class TestItSurvives(_Modes):
    """Easing and speed live in the caption's tag: every path that keys the words again reads them."""

    def setUp(self):
        super().setUp()
        self.make(animate="BOTH", line_animation="FADE", word_animation="POP", line_easing="EASE_OUT",
                  word_easing="BACK", speed="SLOW")
        self.name = max(self.lines(), key=lambda ln: len(ln.text.split())).name

    def line(self):
        return self.scene.sequence_editor.strips.get(self.name)

    def last_modes_of_words(self):
        return [self.modes[w.name] for w in self.words_of(self.line())]

    def assert_eased(self):
        for modes in self.last_modes_of_words():
            scale = modes["transform.scale_x"]
            self.assertTrue(all(i == "BACK" for _f, (i, _e) in scale.items() if _f != max(scale)))

    def test_a_new_bold_and_font_through_the_refit(self):
        self.line().use_bold = True
        self.assertEqual(self.ops.refit_words(self.scene), 1)
        self.modes.clear()
        self.line().font_size = 99
        self.ops.refit_words(self.scene)
        self.assert_eased()
        self.assertEqual(self.line().get("bfacw_caption")["wease"], "BACK")

    def test_a_text_change(self):
        self.modes.clear()
        self.ops.set_caption_text(self.scene, self.line(), "Brand new words here now")
        self.assert_eased()
        tag = self.line().get("bfacw_caption")
        self.assertEqual((tag["ease"], tag["wease"], tag["speed"]), ("EASE_OUT", "BACK", "SLOW"))

    def test_remove_silences_refit(self):
        self.line().right_handle -= 6
        self.modes.clear()
        self.assertTrue(self.ops.refit_captions(self.scene, [self.name]))
        self.assert_eased()

    def test_make_vertical(self):
        self.scene.render.resolution_x, self.scene.render.resolution_y = 1080, 1920
        self.modes.clear()
        self.ops.relayout_captions(self.scene, old_res=(1920, 1080))
        for line in self.lines():
            tag = line.get("bfacw_caption")
            self.assertEqual((tag["ease"], tag["wease"], tag["speed"]), ("EASE_OUT", "BACK", "SLOW"))
        self.assertTrue(any(self.eased(w) for w in self.words()))

    def test_the_speed_reaches_the_keys_made_again(self):
        word = self.words_of(self.line())[1]

        def alpha_frames(speed):
            tag = self.line().get("bfacw_caption")
            tag["speed"] = speed
            self.line()["bfacw_caption"] = tag
            seen = len(word.keys)
            self.line().right_handle -= 1
            self.ops.refit_captions(self.scene, [self.name])
            return sorted({f for p, f, _v in word.keys[seen:] if p == "blend_alpha"})
        fast, slow = alpha_frames("FAST"), alpha_frames("SLOW")
        self.assertTrue(fast and slow)
        self.assertNotEqual(fast, slow)

    def test_the_tag_keys_are_handed_to_the_pieces_of_a_cut(self):
        tag = dict(self.line().get("bfacw_caption"))
        look = self.ops._look_tag(tag)
        self.assertEqual((look["ease"], look["wease"], look["speed"]), ("EASE_OUT", "BACK", "SLOW"))


class TestAnOldCaptionKeepsItsLook(_Modes):

    def test_a_caption_made_before_reads_as_the_style_and_normal(self):
        self.make(animate="LINE", line_animation="FADE")
        line = self.lines()[0]
        tag = line.get("bfacw_caption")
        for k in ("ease", "wease", "speed"):
            tag.pop(k, None)
        line.right_handle -= 5
        self.modes.clear()
        self.assertTrue(self.ops.reanimate(self.scene, line))
        self.assertEqual(self.eased(line), {})
        self.assertEqual(set(self.modes[line.name].values()), {"BEZIER"})


# -- the dialog and its icons -----------------------------------------------------------------

class TestDialog(_OpsCase):

    def draw(self, **kw):
        op = test_captions.TestCaptionStrips._operator(self)[0]
        for k, v in kw.items():
            setattr(op, k, v)
        test_captions._clip(self.scene)
        layout = test_captions._Layout()
        op.layout = layout
        op.draw(SimpleNamespace(scene=self.scene, workspace=SimpleNamespace(sequencer_scene=self.scene)))
        return op, layout

    def test_the_easing_rows_sit_under_their_animation_and_speed_under_both(self):
        _op, layout = self.draw(animate="BOTH", line_animation="FADE", word_animation="POP")
        props = [e[1] for e in layout.log if e[0] == "prop"]
        self.assertLess(props.index("line_animation"), props.index("line_easing"))
        self.assertLess(props.index("line_easing"), props.index("word_animation"))
        self.assertLess(props.index("word_animation"), props.index("word_easing"))
        self.assertLess(props.index("word_easing"), props.index("speed"))
        self.assertLess(props.index("speed"), props.index("language"))

    def test_what_the_dialog_passes_on(self):
        op = test_captions.TestCaptionStrips._operator(self)[0]
        op.animate, op.line_animation, op.word_animation = "BOTH", "FADE", "POP"
        op.line_easing, op.word_easing, op.speed = "EASE_OUT", "BOUNCE", "SLOW"
        look = op._look(self.scene, [])
        self.assertEqual((look["line_easing"], look["word_easing"], look["speed"]), ("EASE_OUT", "BOUNCE", "SLOW"))
        params = self.ops._look_params(dict(look))
        self.assertEqual((params["line_easing"], params["word_easing"], params["speed"]),
                         ("EASE_OUT", "BOUNCE", "SLOW"))

    def test_every_dropdown_entry_has_an_icon(self):
        lists = {"style": self.ops._STYLE_ITEMS, "animate": self.ops._ANIMATE_ITEMS, "line": self.ops._ANIM_ITEMS,
                 "word": self.ops._WORD_ITEMS, "easing": self.ops._EASING_ITEMS, "speed": self.ops._SPEED_ITEMS,
                 "position": self.ops._POSITION_ITEMS, "case": self.ops._CASE_ITEMS,
                 "language": self.ops._LANGUAGE_ITEMS, "lines": self.ops._LINES_BY_COUNT[1] + self.ops._LINES_BY_COUNT[2]}
        lists.update({"bold": self.ops._TRI_ITEMS["bold"], "italic": self.ops._TRI_ITEMS["italic"],
                      "font": self.ops._font_items(None, None)})
        for name, items in lists.items():
            self.assertTrue(items, name)
            for item in items:
                self.assertEqual(len(item), 5, (name, item[0]))
                self.assertNotIn(item[3], ("NONE", ""), "{:s}: {:s} has no icon".format(name, item[1]))


# -- every icon resolves on Blender 5.2 ---------------------------------------------------------

def _names(file_name):
    lines = (_DATA / file_name).read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def _resolve(name, known, fallbacks):
    """What ``shared.icon()`` gives in a build whose icons are *known*."""
    if name in known:
        return name
    fallback = fallbacks.get(name, "NONE")
    return fallback if fallback in known else "NONE"


def _used_in_the_operator_module():
    """Every icon name written in ``captions_ops.py``: ``safe_icon('X')``, ``_wrapped(..., 'X')``, ``icon='X'``."""
    tree = ast.parse((_ADDON / "captions_ops.py").read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if func == "safe_icon" and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            found.setdefault(node.args[0].value, node.lineno)
        if func == "_wrapped" and len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
            found.setdefault(node.args[2].value, node.lineno)
        for kw in node.keywords:
            if kw.arg == "icon" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                found.setdefault(kw.value.value, node.lineno)
    return found


class TestIcons(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("bfacw_shared_for_captions_icons", _ADDON / "shared.py")
        cls.shared = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.shared)
        cls.blender = _names("blender_icons.txt")
        cls.bfa_only = _names("bforartists_only_icons.txt")

    def test_every_table_entry_has_an_icon_and_none_is_a_placeholder(self):
        for table, entries in cap.ICONS.items():
            for key, name in entries.items():
                self.assertTrue(name and name != "NONE", (table, key))

    def test_every_entry_of_every_dropdown_has_a_table_entry(self):
        icons = cap.ICONS
        for table, keys in (("style", cap.STYLES), ("animate", cap.ANIMATE), ("line", cap.ANIMATIONS),
                            ("easing", cap.EASINGS), ("speed", cap.SPEEDS), ("position", cap.POSITIONS),
                            ("case", cap.CASES)):
            self.assertEqual(set(icons[table]), set(keys), table)
        word = set(icons["word"])
        for key in cap.WORD_ANIMATIONS:
            self.assertIn(key.split("_", 1)[-1] if key.startswith("HIGHLIGHT_") else key, word, key)
        self.assertEqual(set(icons["section"]), {"style", "motion", "text", "speakers"})

    def test_the_easing_icons_are_the_graph_editors(self):
        self.assertEqual({k: v for k, v in cap.ICONS["easing"].items() if k != "STYLE"},
                         {"LINEAR": "IPO_LINEAR", "EASE_IN": "IPO_EASE_IN", "EASE_OUT": "IPO_EASE_OUT",
                          "EASE_IN_OUT": "IPO_EASE_IN_OUT", "BACK": "IPO_BACK", "BOUNCE": "IPO_BOUNCE",
                          "ELASTIC": "IPO_ELASTIC"})

    def test_every_icon_of_the_dialog_resolves_on_blender_52(self):
        names = cap.ui_icon_names() | set(_used_in_the_operator_module())
        self.assertGreater(len(names), 30)
        for name in sorted(names):
            got = _resolve(name, self.blender, self.shared.ICON_FALLBACKS)
            self.assertIn(got, self.blender, name)
            self.assertNotEqual(got, "NONE", "{:s} has no Blender icon or fallback".format(name))

    def test_a_bforartists_only_name_has_its_blender_fallback(self):
        used = cap.ui_icon_names() | set(_used_in_the_operator_module())
        only = {n for n in used if n in self.bfa_only and n not in self.blender}
        self.assertIn("SEQ_TEXT", only)                    # the Text section keeps Bforartists' own strip icon
        for name in only:
            self.assertIn(self.shared.ICON_FALLBACKS.get(name), self.blender, name)

    def test_the_operator_writes_no_bforartists_only_name_outside_the_helper(self):
        tree = ast.parse((_ADDON / "captions_ops.py").read_text(encoding="utf-8"))
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in self.bfa_only \
                    and node.value not in self.blender and node.value.isupper():
                parent = parents.get(node)
                if isinstance(parent, ast.Set):
                    continue        # a report type: self.report({'WARNING'}, ...)
                if not (isinstance(parent, ast.Call) and getattr(parent.func, "id", "") == "safe_icon"):
                    bad.append((node.value, node.lineno))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
