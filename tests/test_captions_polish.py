# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Captions polish (#594): the word strips follow their caption line (the layout from the line's own
settings, the signature the handler compares, the refit), the style choices (letter case, font, bold,
italic, size, colours) in the dialog and the tool, and the clearer menus.  No Blender.

Run with::

    python -m unittest tests.test_captions_polish -v
"""

__all__ = ()

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_captions
from test_captions import _OpsCase, _clip, cap

WIDE = (1920, 1080)


def half(text, px):
    """A stand-in for blf: half the size per character."""
    return len(text) * px * 0.5


# -- the layout maths -------------------------------------------------------------------------

class TestWordLayout(unittest.TestCase):

    def lay(self, text="aa bb cc", px=20, res=WIDE, loc=(0.5, 0.1), ax="CENTER", ay="BOTTOM", al="CENTER",
            wrap=0.0, skip=0, italic=False, measure=half):
        return cap.word_layout(text, measure, px, res[0], res[1], loc, ax, ay, al, wrap, skip, italic=italic)

    def test_a_centred_line_starts_half_its_width_left_of_the_anchor(self):
        got = self.lay()
        width = half("aa bb cc", 20)
        self.assertAlmostEqual(got[0]["x"], (960 - width / 2) / 1920)
        self.assertAlmostEqual(got[1]["x"] - got[0]["x"], half("aa ", 20) / 1920)
        self.assertAlmostEqual(got[2]["x"] - got[0]["x"], half("aa bb ", 20) / 1920)
        self.assertEqual({w["y"] for w in got}, {0.1})
        self.assertEqual([w["text"] for w in got], ["aa", "bb", "cc"])

    def test_left_centre_and_right_anchors(self):
        width = half("aa bb cc", 20)
        left = self.lay(loc=(0.1, 0.1), ax="LEFT", al="LEFT")
        centre = self.lay(loc=(0.5, 0.1))
        right = self.lay(loc=(0.9, 0.1), ax="RIGHT", al="RIGHT")
        self.assertAlmostEqual(left[0]["x"], 0.1)
        self.assertAlmostEqual(centre[0]["x"], 0.5 - width / 2 / 1920)
        self.assertAlmostEqual(right[0]["x"], 0.9 - width / 1920)
        self.assertAlmostEqual(right[2]["x"] + half("cc", 20) / 1920, 0.9)

    def test_the_font_decides_through_the_measure(self):
        def wide(text, px):
            return len(text) * px * 0.8
        a, b = self.lay(), self.lay(measure=wide)
        self.assertLess(b[0]["x"], a[0]["x"])
        self.assertGreater(b[2]["x"] - b[0]["x"], a[2]["x"] - a[0]["x"])

    def test_wrap_breaks_where_blender_does(self):
        # 8 px a character at 16: "aa bb" is 40 px, "aa bb cc" 64 px; 0.03 x 1920 = 57.6 breaks before "cc".
        got = self.lay("aa bb cc", px=16, wrap=0.03)
        self.assertEqual([w["line"] for w in got], [0, 0, 1])
        self.assertEqual(self.lay("aa bb cc", px=16, wrap=0.04)[2]["line"], 0)
        self.assertEqual({w["line"] for w in self.lay("aa bb cc", px=16, wrap=0.0)}, {0})

    def test_lines_sit_a_pitch_apart_from_the_anchor(self):
        px = 100
        pitch = cap.LINE_PITCH * px / 1080
        wrap = 0.12
        bottom = self.lay("aaaa bbbb", px=px, wrap=wrap, ay="BOTTOM", loc=(0.5, 0.3))
        top = self.lay("aaaa bbbb", px=px, wrap=wrap, ay="TOP", loc=(0.5, 0.9))
        mid = self.lay("aaaa bbbb", px=px, wrap=wrap, ay="CENTER", loc=(0.5, 0.5))
        self.assertAlmostEqual(bottom[0]["y"], 0.3 + pitch)
        self.assertAlmostEqual(bottom[1]["y"], 0.3)
        self.assertAlmostEqual(top[0]["y"], 0.9)
        self.assertAlmostEqual(top[1]["y"], 0.9 - pitch)
        self.assertAlmostEqual(mid[0]["y"], 0.5 + pitch / 2)
        self.assertAlmostEqual(mid[1]["y"], 0.5 - pitch / 2)

    def test_alignment_places_a_short_line_inside_the_block(self):
        px, wrap = 100, 0.12
        text = "aaaaaa bb"
        left = self.lay(text, px=px, wrap=wrap, al="LEFT", loc=(0.5, 0.3))
        centre = self.lay(text, px=px, wrap=wrap, al="CENTER", loc=(0.5, 0.3))
        right = self.lay(text, px=px, wrap=wrap, al="RIGHT", loc=(0.5, 0.3))
        block, short = half("aaaaaa", px), half("bb", px)
        self.assertAlmostEqual(left[1]["x"], left[0]["x"])
        self.assertAlmostEqual(centre[1]["x"] - centre[0]["x"], (block - short) / 2 / 1920)
        self.assertAlmostEqual(right[1]["x"] - right[0]["x"], (block - short) / 1920)

    def test_explicit_line_breaks_are_lines(self):
        got = self.lay("aa\nbb cc", px=20)
        self.assertEqual([w["line"] for w in got], [0, 1, 1])

    def test_a_speakers_label_is_laid_out_but_has_no_strip(self):
        full = self.lay("Ana: aa bb", skip=1)
        self.assertEqual([w["text"] for w in full], ["aa", "bb"])
        plain = self.lay("Ana: aa bb")
        self.assertAlmostEqual(full[0]["x"], plain[1]["x"])

    def test_italic_widens_the_anchor_box(self):
        base = self.lay()
        slanted = self.lay(italic=True)
        shift = cap.ITALIC_OVERHANG_EM * 20 / 1920
        self.assertAlmostEqual(base[0]["x"] - slanted[0]["x"], shift / 2)
        left = self.lay(loc=(0.1, 0.1), ax="LEFT", al="LEFT", italic=True)
        self.assertAlmostEqual(left[0]["x"], 0.1)
        right = self.lay(loc=(0.9, 0.1), ax="RIGHT", al="RIGHT", italic=True)
        self.assertAlmostEqual(right[0]["x"], self.lay(loc=(0.9, 0.1), ax="RIGHT", al="RIGHT")[0]["x"] - shift)

    def test_empty_text_and_a_frame_of_any_size(self):
        self.assertEqual(self.lay(""), [])
        self.assertEqual(self.lay("   "), [])
        tall = self.lay(res=(1080, 1920))
        self.assertAlmostEqual(tall[0]["x"], (540 - half("aa bb cc", 20) / 2) / 1080)

    def test_wrap_words(self):
        self.assertEqual(cap.wrap_words(["aa", "bb", "cc"], half, 16, 0), [["aa", "bb", "cc"]])
        self.assertEqual(cap.wrap_words(["aa", "bb", "cc"], half, 16, 60), [["aa", "bb"], ["cc"]])
        self.assertEqual(cap.wrap_words(["aaaaaaaaaaaaaaaa"], half, 16, 60), [["aaaaaaaaaaaaaaaa"]])
        self.assertEqual(cap.wrap_words([], half, 16, 60), [])


class TestBoldMeasure(unittest.TestCase):

    def test_bold_is_wider_by_glyph(self):
        bold = cap.bold_measure(half, "inter")
        self.assertGreater(bold("the", 100), half("the", 100))
        self.assertGreater(bold("Tutorial words fit", 100), half("Tutorial words fit", 100))
        self.assertGreater(bold("t", 100) - half("t", 100), bold("5", 100) - half("5", 100))
        self.assertEqual(bold("", 100), 0)

    def test_it_grows_with_the_size_and_adds_up(self):
        bold = cap.bold_measure(half, "inter")
        self.assertAlmostEqual(bold("part", 200) - half("part", 200), 2 * (bold("part", 100) - half("part", 100)))

        def extra(t):
            return bold(t, 100) - half(t, 100)
        self.assertAlmostEqual(extra("ab"), extra("a") + extra("b"))

    def test_a_font_without_a_bold_face_and_a_monospaced_one(self):
        other = cap.bold_measure(half, "other")
        self.assertAlmostEqual(other("abcd", 100) - half("abcd", 100), 4 * cap.SYNTHETIC_BOLD_EM * 100)
        mono = cap.bold_measure(half, "mono")
        self.assertEqual(mono("abcd", 100), half("abcd", 100))

    def test_font_family(self):
        self.assertEqual(cap.font_family("C:/x/Blender/fonts/Inter.woff2"), "inter")
        self.assertEqual(cap.font_family("<builtin>"), "inter")
        self.assertEqual(cap.font_family(""), "inter")
        self.assertEqual(cap.font_family("//fonts\\DejaVuSansMono.woff2"), "mono")
        self.assertEqual(cap.font_family("/f/Roboto-Regular.ttf"), "other")


class TestLayoutSignature(unittest.TestCase):

    NAMES = ("text", "font", "bold", "italic", "size", "anchor_x", "anchor_y", "alignment", "wrap", "loc",
             "transform", "res_x", "res_y", "label")
    BASE = ("Alpha one", "Inter.woff2", False, False, 67.0, "CENTER", "BOTTOM", "CENTER", 1.0, (0.5, 0.09),
            (0.0, 0.0, 1.0, 1.0, 0.0), 1920, 1080, "")

    def sig(self, **changes):
        values = dict(zip(self.NAMES, self.BASE), **changes)
        return cap.layout_signature(*(values[n] for n in self.NAMES))

    def test_stable_and_short(self):
        self.assertEqual(self.sig(), self.sig())
        self.assertEqual(len(self.sig()), 8)

    def test_every_setting_changes_it(self):
        base = self.sig()
        for change in ({"text": "Alpha two"}, {"font": "Other.ttf"}, {"bold": True}, {"italic": True}, {"size": 70.0},
                       {"anchor_x": "LEFT"}, {"anchor_y": "TOP"}, {"alignment": "RIGHT"}, {"wrap": 0.4},
                       {"loc": (0.5, 0.5)}, {"transform": (10.0, 0.0, 1.0, 1.0, 0.0)}, {"res_x": 1080},
                       {"res_y": 1920}, {"label": "Ana:"}):
            self.assertNotEqual(self.sig(**change), base, change)

    def test_a_drag_that_ends_where_it_began_is_no_change(self):
        self.assertEqual(self.sig(loc=(0.5 + 1e-7, 0.09 - 1e-7)), self.sig())
        self.assertEqual(self.sig(size=67.00001), self.sig())


# -- letter case and the style choices ----------------------------------------------------------

class TestCase(unittest.TestCase):

    def test_the_four_cases(self):
        text = "hello NASA world, don't stop"
        self.assertEqual(cap.apply_case(text, "UPPER"), "HELLO NASA WORLD, DON'T STOP")
        self.assertEqual(cap.apply_case(text, "LOWER"), "hello nasa world, don't stop")
        self.assertEqual(cap.apply_case(text, "TITLE"), "Hello NASA World, Don't Stop")
        self.assertEqual(cap.apply_case(text, "AS_SPOKEN"), text)
        self.assertEqual(cap.apply_case(text, "STYLE"), text)

    def test_title_case_keeps_a_leading_quote_and_lowers_the_rest(self):
        self.assertEqual(cap.apply_case("“quoted words” aNd more", "TITLE"), "“Quoted Words” And More")

    def test_names(self):
        for given, key in (("uppercase", "UPPER"), ("Title Case", "TITLE"), ("as spoken", "AS_SPOKEN"),
                           ("lower", "LOWER"), ("", "STYLE"), (None, "STYLE"), ("style", "STYLE"), ("caps", "UPPER")):
            self.assertEqual(cap.case_key(given), key, given)
        self.assertIsNone(cap.case_key("sideways"))
        self.assertEqual(list(cap.CASES), ["STYLE", "AS_SPOKEN", "UPPER", "LOWER", "TITLE"])

    def test_the_style_decides_when_nothing_is_chosen(self):
        self.assertEqual(cap.effective_case("BOLD_POP"), "UPPER")
        self.assertEqual(cap.effective_case("CLEAN"), "AS_SPOKEN")
        self.assertEqual(cap.effective_case("BOLD_POP", "STYLE"), "UPPER")
        self.assertEqual(cap.effective_case("BOLD_POP", "AS_SPOKEN"), "AS_SPOKEN")
        self.assertEqual(cap.effective_case("CLEAN", "UPPER"), "UPPER")
        self.assertEqual(cap.effective_case("WORD_HIGHLIGHT", "TITLE"), "TITLE")

    def test_resolve_style_carries_the_case(self):
        self.assertTrue(cap.resolve_style("BOLD_POP", *WIDE)["uppercase"])
        self.assertEqual(cap.resolve_style("BOLD_POP", *WIDE)["case"], "UPPER")
        self.assertEqual(cap.resolve_style("BOLD_POP", *WIDE, choices={"case": "LOWER"})["case"], "LOWER")
        self.assertFalse(cap.resolve_style("BOLD_POP", *WIDE, choices={"case": "LOWER"})["uppercase"])
        self.assertTrue(cap.resolve_style("CLEAN", *WIDE, choices={"case": "UPPER"})["uppercase"])


class TestStyleChoices(unittest.TestCase):

    def test_nothing_chosen_is_as_the_style_has_it(self):
        self.assertEqual(cap.check_style_params(), {})
        self.assertEqual(cap.check_style_params("STYLE", "", None, None, 1.0, None, None), {})
        self.assertEqual(cap.check_style_params(size=0), {})
        for style in cap.STYLES:
            self.assertEqual(cap.resolve_style(style, *WIDE), cap.resolve_style(style, *WIDE, choices={}))

    def test_the_choices_that_are_made(self):
        got = cap.check_style_params("title", " My Font ", True, False, 1.5, [1, 0.5, 0.25], "#FF0000")
        self.assertEqual(got["case"], "TITLE")
        self.assertEqual(got["font"], "My Font")
        self.assertIs(got["bold"], True)
        self.assertIs(got["italic"], False)
        self.assertEqual(got["size"], 1.5)
        self.assertEqual(got["color"], (1.0, 0.5, 0.25, 1.0))
        self.assertEqual(got["highlight"], (1.0, 0.0, 0.0, 1.0))

    def test_a_hex_colour_is_what_is_seen_on_screen(self):
        self.assertAlmostEqual(cap.check_style_params(text_color="#808080")["color"][0], 0.2159, 3)
        self.assertEqual(cap.check_style_params(text_color="000000")["color"], (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(cap.check_style_params(text_color="#FFFFFF80")["color"][3], round(128 / 255, 5))

    def test_bold_and_italic_take_words_too(self):
        got = cap.check_style_params(bold="on", italic="OFF")
        self.assertEqual((got["bold"], got["italic"]), (True, False))
        self.assertEqual(cap.check_style_params(bold="style", italic="auto"), {})

    def test_mistakes_say_what_is_wrong(self):
        for kwargs in ({"case": "sideways"}, {"bold": "maybe"}, {"size": "big"}, {"size": 9}, {"size": 0.1},
                       {"text_color": "red"}, {"text_color": [1, 2, 3, 4, 5]}, {"text_color": [1, 1]},
                       {"text_color": [2, 0, 0]}, {"highlight_color": "#12345"}):
            with self.assertRaises(cap.CaptionsError, msg=str(kwargs)):
                cap.check_style_params(**kwargs)

    def test_the_style_gives_way_to_the_choices(self):
        base = cap.resolve_style("CLEAN", *WIDE)
        got = cap.resolve_style("CLEAN", *WIDE, choices={"size": 1.5, "bold": True, "italic": True,
                                                         "color": (0.1, 0.2, 0.3, 1.0)})
        self.assertEqual(got["props"]["font_size"], round(base["props"]["font_size"] * 1.5))
        self.assertTrue(got["props"]["use_bold"] and got["props"]["use_italic"])
        self.assertEqual(got["props"]["color"], (0.1, 0.2, 0.3, 1.0))
        self.assertFalse(base["props"]["use_bold"])
        self.assertFalse(cap.resolve_style("BOLD_POP", *WIDE, choices={"bold": False})["props"]["use_bold"])

    def test_the_highlight_colour_is_for_the_style_that_has_one(self):
        own = {"highlight": (0.2, 0.9, 0.2, 1.0)}
        self.assertEqual(cap.resolve_style("WORD_HIGHLIGHT", *WIDE, choices=own)["highlight"], (0.2, 0.9, 0.2, 1.0))
        self.assertEqual(cap.resolve_style("WORD_HIGHLIGHT", *WIDE)["highlight"], (1.0, 0.82, 0.1, 1.0))
        self.assertIsNone(cap.resolve_style("CLEAN", *WIDE, choices=own)["highlight"])

    def test_characters_per_line_follow_size_bold_and_case(self):
        plain = cap.default_max_chars("CLEAN", *WIDE)
        self.assertLess(cap.default_max_chars("CLEAN", *WIDE, {"size": 2.0}), plain)
        tall = (1080, 1920)         # a wide frame's 42 is the ceiling
        plain = cap.default_max_chars("CLEAN", *tall)
        self.assertLess(cap.default_max_chars("CLEAN", *tall, {"bold": True}), plain)
        self.assertLess(cap.default_max_chars("CLEAN", *tall, {"case": "UPPER"}), plain)
        self.assertEqual(cap.default_max_chars("CLEAN", *tall, {"case": "TITLE"}), plain)
        self.assertEqual(cap.default_max_chars("BOLD_POP", *WIDE), cap.default_max_chars("BOLD_POP", *WIDE, {}))

    def test_a_size_choice_is_not_a_hand_edit(self):
        choices = {"size": 1.5}
        props = cap.resolve_style("CLEAN", *WIDE, choices=choices)["props"]
        look = {"location": props["location"], "font_size": props["font_size"], "wrap_width": props["wrap_width"],
                "anchor_x": "CENTER", "anchor_y": "BOTTOM"}
        self.assertEqual(cap.hand_edits(look, "CLEAN", *WIDE, choices=choices), [])
        self.assertEqual(cap.hand_edits(look, "CLEAN", *WIDE), ["size"])

    def test_relayout_keeps_the_choices(self):
        got = cap.relayout("MAKE UP WORDS", "CLEAN", 1080, 1920, 1, 61, 40, 2, WIDE, None, "BOTTOM", 0.0, "",
                           {"size": 2.0, "case": "UPPER"})
        plain = cap.resolve_style("CLEAN", 1080, 1920)["props"]["font_size"]
        self.assertEqual(got["style"]["props"]["font_size"], 2 * plain)
        self.assertEqual(got["style"]["case"], "UPPER")


# -- the tool and the operator ---------------------------------------------------------------------

class TestStyleChoicesInCaptions(_OpsCase):

    def test_the_choices_become_the_strips_and_are_kept_in_their_tags(self):
        clip = _clip(self.scene)
        choices = cap.check_style_params("UPPER", None, True, True, 1.5, "#FFFFFF", None)
        self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript, choices=choices)
        line = self.lines()[0]
        self.assertEqual(line.text, line.text.upper())
        self.assertTrue(line.use_bold and line.use_italic)
        self.assertEqual(line.font_size, round(cap.resolve_style("CLEAN", *WIDE)["props"]["font_size"] * 1.5))
        tag = line.get("bfacw_caption")
        self.assertEqual(tag["choices"]["case"], "UPPER")
        self.assertIs(tag["choices"]["bold"], True)
        self.assertEqual(tag["choices"]["size"], 1.5)
        self.assertIn("color", tag["choices"])

    def test_nothing_chosen_changes_nothing(self):
        clip = _clip(self.scene)
        self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript)
        plain = [(s.text, s.font_size, s.use_bold) for s in self.lines()]
        self.assertTrue(all("choices" not in s.get("bfacw_caption") for s in self.lines()))
        self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript, choices={})
        self.assertEqual([(s.text, s.font_size, s.use_bold) for s in self.lines()], plain)

    def test_bold_pop_keeps_capitals_unless_told_otherwise(self):
        clip = _clip(self.scene)
        self.ops.add_captions(self.scene, [clip], "BOLD_POP", None, self.transcript)
        self.assertTrue(all(s.text == s.text.upper() for s in self.lines()))
        self.ops.add_captions(self.scene, [clip], "BOLD_POP", None, self.transcript, choices={"case": "TITLE"})
        self.assertTrue(any(s.text != s.text.upper() for s in self.lines()))
        self.assertTrue(all(s.text == cap.apply_case(s.text, "TITLE") for s in self.lines()))

    def test_lowercase_for_any_style(self):
        clip = _clip(self.scene)
        self.ops.add_captions(self.scene, [clip], "BOXED", None, self.transcript, choices={"case": "LOWER"})
        self.assertTrue(all(s.text == s.text.lower() for s in self.lines()))

    def test_a_rewrite_by_edit_by_transcript_keeps_the_case(self):
        """``set_caption_text`` (Translate, Edit by Transcript's caption rewrite) writes in the caption's own case."""
        clip = _clip(self.scene)
        self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript, choices={"case": "UPPER"})
        line = self.lines()[0]
        self.ops.set_caption_text(self.scene, line, "a brand new line")
        self.assertEqual(line.text.replace("\n", " "), "A BRAND NEW LINE")
        self.ops.add_captions(self.scene, [clip], "BOLD_POP", None, self.transcript, replace_edited=True,
                              choices={"case": "AS_SPOKEN"})
        line = self.lines()[0]
        self.ops.set_caption_text(self.scene, line, "Mixed Case here")
        self.assertEqual(line.text.replace("\n", " "), "Mixed Case here")

    def test_the_font_the_artist_chose_is_the_strips_font(self):
        clip = _clip(self.scene)
        font = SimpleNamespace(name="My Font", filepath="//my.ttf")
        with mock.patch.object(self.ops, "_choice_font", lambda choice: font if choice == "My Font" else None):
            self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript, choices={"font": "My Font"})
        self.assertTrue(all(s.font is font for s in self.lines()))

    def test_a_font_that_does_not_exist_is_said_before_anything_changes(self):
        clip = _clip(self.scene)
        self.ops.bpy.data.fonts = [SimpleNamespace(name="Mine", filepath="//m.ttf")]
        self.ops.bpy.data.fonts_get = None
        with mock.patch.object(self.ops.bpy.data, "fonts", _Fonts([SimpleNamespace(name="Mine", filepath="//m.ttf")])):
            with self.assertRaises(self.ops.CaptionsError) as ctx:
                self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript, choices={"font": "Nope"})
        self.assertIn("No font", str(ctx.exception))
        self.assertIn("Mine", str(ctx.exception))
        self.assertEqual(self.lines(), [])

    def test_the_tool_checks_and_passes_the_choices(self):
        _clip(self.scene)
        seen = {}
        real = self.ops.add_captions

        def spy(*args, **kw):
            seen.update(kw)
            return real(*args, **kw)
        with mock.patch.object(self.ops, "add_captions", spy):
            out = self.ops.tool_add_captions(["speech"], "CLEAN", None, self.transcript, case="upper", bold=True,
                                             size=1.25, text_color="#00FF00")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(seen["choices"]["case"], "UPPER")
        self.assertEqual(seen["choices"]["size"], 1.25)
        bad = self.ops.tool_add_captions(["speech"], "CLEAN", None, self.transcript, case="sideways")
        self.assertEqual(bad["status"], "error")
        self.assertIn("case", bad["message"])
        bad = self.ops.tool_add_captions(["speech"], "CLEAN", None, self.transcript, size=99)
        self.assertEqual(bad["status"], "error")

    def test_the_tool_listing_has_the_choices(self):
        text = (test_captions._TOOLS / "add_captions.py").read_text(encoding="utf-8")
        code = (test_captions._TOOLS / "add_captions_toolcode.py").read_text(encoding="utf-8")
        for name in ("case", "font", "bold", "italic", "size", "text_color", "highlight_color"):
            self.assertRegex(text, r"\n        {:s}: ".format(name))
            self.assertRegex(code, r"\n    {:s}: ".format(name))
        self.assertIsNotNone(re.search(r"params\.highlight_color", code))


class _Fonts(list):
    """``bpy.data.fonts``: a list with ``get``."""

    def get(self, name, default=None):
        return next((f for f in self if f.name == name), default)


# -- word strips follow their line -----------------------------------------------------------------

class _Transform(SimpleNamespace):
    pass


class TestWordStripsFollowTheLine(_OpsCase):
    """``add_captions`` on the Word-by-word style, then the line's settings changed like in the strip's
    properties: ``refit_words`` puts the word strips where the line's own settings say."""

    def setUp(self):
        super().setUp()
        patch = self._measured()
        patch.start()
        self.addCleanup(patch.stop)
        self.clip = _clip(self.scene)
        self.ops.add_captions(self.scene, [self.clip], "WORD_HIGHLIGHT", None, self.transcript)
        self.line = self.lines()[0]
        self.line.use_italic = False

    def mine(self):
        return sorted([w for w in self.words() if w.get("bfacw_caption")["line"] == self.line.name],
                      key=lambda w: w.name)

    def predicted(self):
        measure = self.ops._line_measure(self.line)
        return cap.word_layout(self.line.text, measure, self.line.font_size, 1920, 1080, tuple(self.line.location),
                               self.line.anchor_x, self.line.anchor_y, self.line.alignment_x, self.line.wrap_width,
                               italic=bool(self.line.use_italic))

    def test_the_tag_has_the_layout_signature(self):
        self.assertTrue(self.line.get("bfacw_caption")["lay"])
        self.assertEqual(self.ops.refit_words(self.scene), 0)

    def drop_signature(self):
        """A caption made before #594: no layout signature in its tag."""
        tag = self.line.get("bfacw_caption")
        del tag["lay"]

    def test_a_caption_from_before_only_has_its_signature_noted(self):
        """Word strips placed or deleted by hand in a 1.1.44 file are not changed by opening it."""
        self.drop_signature()
        self.line.use_bold = True                   # even a line that differs from its words
        before = [(w.location, w.use_bold) for w in self.mine()]
        names = [w.name for w in self.mine()]
        with mock.patch.object(self.ops, "_reposition_words") as moved, mock.patch.object(self.ops, "_rebuild_words") as rebuilt:
            self.assertEqual(self.ops.refit_words(self.scene, rebuild=True), 0)
        moved.assert_not_called()
        rebuilt.assert_not_called()
        self.assertEqual([(w.location, w.use_bold) for w in self.mine()], before)
        self.assertEqual([w.name for w in self.mine()], names)
        self.assertTrue(self.line.get("bfacw_caption")["lay"], "the signature is noted now")
        self.assertNotIn("choices", self.line.get("bfacw_caption"))
        self.line.use_italic = True                 # the first real change refits
        self.assertEqual(self.ops.refit_words(self.scene), 1)
        self.assertTrue(all(w.use_bold and w.use_italic for w in self.mine()))

    def test_the_operator_refits_a_caption_from_before(self):
        self.drop_signature()
        self.line.use_bold = True
        self.assertEqual(self.ops.refit_words(self.scene, [self.line.name], force=True, rebuild=True), 1)
        self.assertTrue(all(w.use_bold for w in self.mine()))

    def test_a_typed_line_break_survives_a_rebuild(self):
        self.line.text = "Delta echo\nfoxtrot"
        self.assertEqual(self.ops.refit_words(self.scene, rebuild=True), 1)
        self.assertEqual(self.line.text, "Delta echo\nfoxtrot")
        ys = {round(w.location[1], 4) for w in self.mine()}
        self.assertEqual(len(ys), 2)

    def test_nothing_changed_costs_no_layout_and_no_measure(self):
        """The signature is compared first: no word is measured and no font is looked up or loaded."""
        with mock.patch.object(self.ops, "_reposition_words") as moved, mock.patch.object(self.ops, "_rebuild_words") as rebuilt,                 mock.patch.object(self.ops, "_line_measure") as measure, mock.patch.object(self.ops, "_font") as font,                 mock.patch.object(self.ops, "_measure") as base:
            for _ in range(5):
                self.assertEqual(self.ops.refit_words(self.scene), 0)
        for mocked in (moved, rebuilt, measure, font, base):
            mocked.assert_not_called()

    def test_bold_italic_font_and_size_are_taken_over_and_the_words_move(self):
        before = [w.location[0] for w in self.mine()]
        font = SimpleNamespace(name="Other", filepath="//other.ttf")
        self.line.use_bold, self.line.use_italic, self.line.font, self.line.font_size = True, True, font, 90
        self.assertEqual(self.ops.refit_words(self.scene), 1)
        for w in self.mine():
            self.assertTrue(w.use_bold and w.use_italic)
            self.assertIs(w.font, font)
            self.assertEqual(w.font_size, 90)
        self.assertNotEqual([w.location[0] for w in self.mine()], before)
        self.assertEqual([w.location[0] for w in self.mine()], [p["x"] for p in self.predicted()])
        self.assertEqual(self.ops.refit_words(self.scene), 0)

    def test_the_words_keep_their_frames_when_they_are_only_moved(self):
        frames = [(w.left_handle, w.right_handle) for w in self.mine()]
        names = [w.name for w in self.mine()]
        self.line.use_bold = True
        self.ops.refit_words(self.scene)
        self.assertEqual([(w.left_handle, w.right_handle) for w in self.mine()], frames)
        self.assertEqual([w.name for w in self.mine()], names)

    def test_left_centre_and_right_aligned_lines_place_their_words(self):
        for anchor, x in (("LEFT", 0.1), ("CENTER", 0.5), ("RIGHT", 0.9)):
            self.line.anchor_x, self.line.alignment_x = anchor, anchor
            self.line.location = (x, self.line.location[1])
            self.assertEqual(self.ops.refit_words(self.scene), 1, anchor)
            got = [w.location[0] for w in self.mine()]
            self.assertEqual(got, [p["x"] for p in self.predicted()], anchor)
            width = half(self.line.text, self.line.font_size)
            first = {"LEFT": x, "CENTER": x - width / 2 / 1920, "RIGHT": x - width / 1920}[anchor]
            self.assertAlmostEqual(got[0], first, msg=anchor)

    def test_a_wrap_width_and_a_new_position_move_them(self):
        self.line.wrap_width = 0.3
        self.line.location = (0.5, 0.5)
        self.assertEqual(self.ops.refit_words(self.scene), 1)
        ys = {round(w.location[1], 4) for w in self.mine()}
        self.assertGreater(len(ys), 1, "the line wrapped into several lines")
        self.assertEqual([(w.location[0], w.location[1]) for w in self.mine()],
                         [(p["x"], p["y"]) for p in self.predicted()])

    def test_the_words_of_the_text_decide_the_strips(self):
        count = len(self.mine())
        self.line.text = "Delta echo foxtrot"
        before = [w.name for w in self.mine()]
        self.assertEqual(self.ops.refit_words(self.scene), 0, "a timer never adds or removes a strip")
        self.assertEqual([w.name for w in self.mine()], before)
        self.assertEqual(self.ops.refit_words(self.scene, rebuild=True), 1)
        words = self.mine()
        self.assertEqual([w.text for w in words], ["Delta", "echo", "foxtrot"])
        self.assertLess(len(words), count)
        self.assertEqual(words[0].left_handle, self.line.left_handle)
        self.assertEqual(words[-1].right_handle, self.line.right_handle)
        self.assertEqual(self.ops.refit_words(self.scene), 0)

    def test_the_same_number_of_words_with_other_letters_keeps_the_strips(self):
        names = [w.name for w in self.mine()]
        words = self.line.text.split()
        self.line.text = " ".join("x" + w for w in words)
        self.ops.refit_words(self.scene)
        self.assertEqual([w.name for w in self.mine()], names)
        self.assertEqual([w.text for w in self.mine()], ["x" + w for w in words])

    def test_names_only_and_force(self):
        self.assertEqual(self.ops.refit_words(self.scene, ["nobody"], force=True), 0)
        self.assertEqual(self.ops.refit_words(self.scene, [self.line.name], force=True, rebuild=True), 1)
        self.line.use_bold = True
        self.assertEqual(self.ops.refit_words(self.scene, ["nobody"]), 0)
        self.assertEqual(self.ops.refit_words(self.scene, [self.line.name]), 1)

    def test_a_line_without_word_strips_is_left_alone(self):
        self.ops.add_captions(self.scene, [self.clip], "CLEAN", None, self.transcript)
        for s in self.lines():
            s.use_bold = True
        self.assertEqual(self.ops.refit_words(self.scene), 0)

    def test_an_animated_transform_is_not_a_change_and_is_not_copied(self):
        self.line.transform = _Transform(offset_x=0.0, offset_y=0.0, scale_x=1.0, scale_y=1.0, rotation=0.0)
        for w in self.mine():
            w.transform = _Transform(offset_x=0.0, offset_y=0.0, scale_x=1.0, scale_y=1.0, rotation=0.0)
        self.line.get("bfacw_caption")["anim"] = "SLIDE_UP"                 # keys transform.offset_y
        self.ops.refit_words(self.scene, force=True)
        self.line.transform.offset_y = 12.0                                 # Slide Up, at some frame
        self.assertEqual(self.ops.refit_words(self.scene), 0)
        self.line.transform.offset_x = 30.0
        self.line.transform.scale_x = 1.2
        self.assertEqual(self.ops.refit_words(self.scene), 1)
        for w in self.mine():
            self.assertEqual((w.transform.offset_x, w.transform.scale_x), (30.0, 1.2))

    def test_the_words_follow_a_new_frame_size(self):
        self.scene.render.resolution_x, self.scene.render.resolution_y = 1080, 1920
        self.assertEqual(self.ops.refit_words(self.scene, rebuild=True), len(self.lines()))
        self.assertEqual([w.location[0] for w in self.mine()][0], cap.word_layout(
            self.line.text, self.ops._line_measure(self.line), self.line.font_size, 1080, 1920,
            tuple(self.line.location), self.line.anchor_x, self.line.anchor_y, self.line.alignment_x,
            self.line.wrap_width)[0]["x"])

    def test_a_font_that_cannot_be_measured_leaves_them(self):
        before = [w.location for w in self.mine()]
        self.line.use_bold = True
        with mock.patch.object(self.ops, "_line_measure", lambda line: None):
            self.assertEqual(self.ops.refit_words(self.scene), 0)
        self.assertEqual([w.location for w in self.mine()], before)


class TestTheHandler(_OpsCase):
    """The depsgraph handler and its timer: cheap, debounced, never while busy."""

    def setUp(self):
        super().setUp()
        self.timers = []
        self.ops.bpy.app.timers = SimpleNamespace(register=lambda fn, first_interval=0.0: self.timers.append(fn),
                                                  is_registered=lambda fn: fn in self.timers,
                                                  unregister=lambda fn: self.timers.remove(fn))
        self.ops.bpy.app.handlers = SimpleNamespace(persistent=lambda f: f, depsgraph_update_post=[])
        self.now = [100.0]
        self.busy = ""
        self.refits = []
        self.real_busy = self.ops._busy
        for name, value in (("_clock", lambda: self.now[0]), ("_busy", lambda: self.busy),
                            ("_visible_scenes", lambda: {"Scene"}),
                            ("refit_words", lambda scene, *a, **k: self.refits.append(scene.name) or 0)):
            patcher = mock.patch.object(self.ops, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ops._WATCH.update(dirty=set(), t=0.0)
        self.playing = False
        patcher = mock.patch.object(self.ops, "_playing", lambda: self.playing)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_handler_only_notes_a_change(self):
        self.ops._on_depsgraph(self.scene)
        self.assertEqual(self.ops._WATCH["dirty"], {"Scene"})
        self.assertEqual(len(self.timers), 1)
        self.assertEqual(self.refits, [], "nothing is looked at in the handler")

    def test_a_scene_without_strips_costs_nothing(self):
        bare = SimpleNamespace(name="Bare", sequence_editor=None, frame_current=1)
        self.ops._on_depsgraph(bare)
        self.assertEqual((self.ops._WATCH["dirty"], self.timers), (set(), []))

    def test_a_change_during_playback_is_noted_and_held_back(self):
        self.playing = True
        self.ops._on_depsgraph(self.scene)
        self.assertEqual(self.ops._WATCH["dirty"], {"Scene"})
        self.now[0] += 1.0
        self.busy = "playing"
        self.assertEqual(self.timers[0](), self.ops.RETRY_S)
        self.assertEqual(self.refits, [])
        self.assertEqual(self.ops._WATCH["dirty"], {"Scene"}, "not lost")
        self.busy, self.playing = "", False
        self.assertIsNone(self.timers[0]())
        self.assertEqual(self.refits, ["Scene"])

    def test_a_refit_request_during_a_modal_operator_waits_until_it_ends(self):
        modal = [object()]
        win = SimpleNamespace(modal_operators=modal)
        self.ops.bpy.context.window_manager = SimpleNamespace(windows=[win])
        self.ops.bpy.app.is_job_running = lambda kind: False
        with mock.patch.object(self.ops, "_busy", self.real_busy):
            self.ops._on_depsgraph(self.scene)
            self.now[0] += 1.0
            self.assertEqual(self.ops._busy(), "a modal operator")
            self.assertEqual(self.timers[0](), self.ops.RETRY_S)
            self.assertEqual(self.refits, [])
            modal.clear()
            self.assertEqual(self.ops._busy(), "")
            self.assertIsNone(self.timers[0]())
        self.assertEqual(self.refits, ["Scene"])

    def test_an_open_agent_turn_holds_it_back(self):
        api = SimpleNamespace(any_turn_open=lambda: True)
        self.ops.bpy.context.window_manager = SimpleNamespace(windows=[])
        self.ops.bpy.app.is_job_running = lambda kind: False
        pkg = __import__("sys").modules[test_captions._PKG]
        with mock.patch.dict("sys.modules", {test_captions._PKG + ".coworker_api": api}),                 mock.patch.object(pkg, "coworker_api", api, create=True):
            self.assertEqual(self.real_busy(), "an agent turn")
            api.any_turn_open = lambda: False
            self.assertEqual(self.real_busy(), "")

    def test_the_timer_waits_until_nothing_has_changed_for_a_moment(self):
        self.ops._on_depsgraph(self.scene)
        self.now[0] += 0.1
        self.assertAlmostEqual(self.timers[0](), self.ops.DEBOUNCE_S - 0.1)
        self.ops._on_depsgraph(self.scene)
        self.now[0] += 0.2
        self.assertGreater(self.timers[0](), 0)
        self.assertEqual(self.refits, [])
        self.now[0] += 0.5
        self.assertIsNone(self.timers[0]())
        self.assertEqual(self.refits, ["Scene"])
        self.assertEqual(self.ops._WATCH["dirty"], set())

    def test_nothing_happens_while_busy_and_it_comes_back(self):
        self.ops._on_depsgraph(self.scene)
        self.now[0] += 1.0
        for busy in ("playing", "rendering", "an agent step"):
            self.busy = busy
            self.assertEqual(self.timers[0](), self.ops.RETRY_S)
        self.assertEqual(self.refits, [])
        self.busy = ""
        self.assertIsNone(self.timers[0]())
        self.assertEqual(self.refits, ["Scene"])

    def test_only_the_scene_being_edited(self):
        other = SimpleNamespace(name="Other", sequence_editor=object(), frame_current=1)
        self.ops._on_depsgraph(other)
        self.now[0] += 1.0
        self.timers[0]()
        self.assertEqual(self.refits, [])

    def test_start_and_stop(self):
        self.ops._watch_start()
        self.ops._watch_start()
        self.assertEqual(self.ops.bpy.app.handlers.depsgraph_update_post.count(self.ops._on_depsgraph), 1)
        self.ops._on_depsgraph(self.scene)
        self.ops._watch_stop()
        self.assertEqual(self.ops.bpy.app.handlers.depsgraph_update_post, [])
        self.assertEqual((self.ops._WATCH["dirty"], self.timers), (set(), []))


# -- clearer menus ----------------------------------------------------------------------------------

class TestMenus(_OpsCase):

    def test_every_entry_of_the_icon_menus_has_an_icon_and_a_tooltip(self):
        for items in (self.ops._ANIM_ITEMS, self.ops._POSITION_ITEMS, self.ops._CASE_ITEMS):
            for ident, label, tip, icon, _number in items:
                self.assertTrue(label and tip and icon and icon != "NONE", ident)
        self.assertEqual([i[0] for i in self.ops._CASE_ITEMS], list(cap.CASES))
        numbers = [i[4] for i in self.ops._CASE_ITEMS]
        self.assertEqual(numbers, sorted(set(numbers)))

    def test_the_icons_are_blender_icons(self):
        data = Path(__file__).resolve().parent / "data" / "blender_icons.txt"
        known = {line.strip() for line in data.read_text(encoding="utf-8").splitlines() if line.strip()
                 and not line.startswith("#")}
        for items in (self.ops._ANIM_ITEMS, self.ops._POSITION_ITEMS, self.ops._CASE_ITEMS):
            for ident, _label, _tip, icon, _number in items:
                self.assertIn(icon, known, ident)

    def test_lines_says_what_it_counts(self):
        items = {n: [i[1] for i in self.ops._lines_items_for(n)] for n in (1, 2)}
        self.assertEqual(items[2], ["As the Style (2 Lines)", "1 Line", "2 Lines"])
        self.assertEqual(items[1][0], "As the Style (1 Line)")

        def count(style, animation="STYLE"):
            return self.ops._lines_items(SimpleNamespace(style=style, animation=animation), None)[0][1]
        self.assertEqual(count("CLEAN"), "As the Style (2 Lines)")
        self.assertEqual(count("BOLD_POP"), "As the Style (1 Line)")
        self.assertEqual(count("WORD_HIGHLIGHT"), "As the Style (1 Line)")
        self.assertEqual(count("CLEAN", "TYPEWRITER"), "As the Style (1 Line)")
        self.assertEqual([i[0] for i in self.ops._lines_items(SimpleNamespace(style="CLEAN", animation="FADE"), None)],
                         ["STYLE", "1", "2"])
        self.assertEqual(self.ops._lines_value("STYLE"), 0)

    def test_the_dialog_has_a_style_and_a_motion_group(self):
        op = test_captions.TestCaptionStrips._operator(self)[0]
        layout = test_captions._Layout()
        op.layout = layout
        _clip(self.scene)
        op.draw(SimpleNamespace(scene=self.scene))
        labels = [e[1] for e in layout.log if e[0] == "label"]
        style_at = next(i for i, t in enumerate(labels) if t.startswith("Style"))
        motion_at = next(i for i, t in enumerate(labels) if t.startswith("Motion"))
        self.assertLess(style_at, motion_at)
        props = [e[1] for e in layout.log if e[0] == "prop"]
        for name in ("style", "case", "font_choice", "bold", "italic", "size", "set_text_color", "text_color",
                     "set_highlight_color", "highlight_color", "position", "margin"):
            self.assertLess(props.index(name), props.index("animation"), name)
        self.assertLess(props.index("animation"), props.index("language"))

    def test_the_dialogs_choices_reach_the_tool_call(self):
        op = test_captions.TestCaptionStrips._operator(self)[0]
        op.case, op.bold, op.italic, op.size = "TITLE", "ON", "OFF", 1.5
        op.set_text_color, op.text_color = True, (0.1, 0.2, 0.3, 1.0)
        got = op._choices()
        self.assertEqual((got["case"], got["bold"], got["italic"], got["size"]), ("TITLE", True, False, 1.5))
        self.assertEqual(got["color"], (0.1, 0.2, 0.3, 1.0))
        self.assertNotIn("highlight", got)
        op.font_choice, op.font_file = "FILE", ""
        with self.assertRaises(self.ops.CaptionsError):
            op._choices()
        op.font_file = "//my.ttf"
        self.assertEqual(op._choices()["font"], "//my.ttf")
        op.font_choice = "FONT:Mine"
        self.assertEqual(op._choices()["font"], "Mine")


if __name__ == "__main__":
    unittest.main()
