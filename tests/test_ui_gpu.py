# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the GPU UI toolkit's bpy-free parts (ui_gpu/style.py, ui_gpu/layout.py).

1. ``style_from_preferences`` reads theme colours (RGB text -> RGBA), widget
   roundness, UI-style font points and scale; falls back per field; zoom is
   clamped; derived sizes (unit, font size, radius) follow scale and zoom.
2. ``wrap_text`` wraps by words, splits over-long words, keeps blank lines.
3. ``Canvas`` records draw commands and hit areas: boxes use the theme
   (selected -> inner_sel), buttons hover / press / click, clipping nests,
   ``wrapped`` returns the height ``text_height`` predicts.

Run with::

    python -m unittest tests.test_ui_gpu -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_ui_gpu_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return (importlib.import_module(name + ".ui_gpu.style"),
            importlib.import_module(name + ".ui_gpu.layout"))


st, lay = _load()
NS = types.SimpleNamespace


def _wcol(inner, sel, outline, text, text_sel, roundness):
    return NS(inner=inner, inner_sel=sel, outline=outline, text=text, text_sel=text_sel, roundness=roundness)


def _prefs(ui_scale=2.0, points=12.0, **ui_overrides):
    ui = NS(
        panel_back=(0.3, 0.3, 0.3, 1.0), panel_header=(0.2, 0.2, 0.2, 1.0),
        panel_outline=(0.1, 0.1, 0.1, 1.0), panel_text=(0.9, 0.9, 0.9), panel_title=(1.0, 1.0, 1.0),
        panel_roundness=0.5,
        wcol_box=_wcol((0.25, 0.25, 0.25, 1.0), (0.4, 0.4, 0.4, 1.0), (0, 0, 0, 1), (0.9, 0.9, 0.9), (1, 1, 1), 0.2),
        wcol_regular=_wcol((0.35, 0.35, 0.35, 1.0), (0.3, 0.5, 0.8, 1.0), (0, 0, 0, 1), (0.9, 0.9, 0.9), (1, 1, 1), 0.4),
        wcol_text=_wcol((0.1, 0.1, 0.1, 1.0), (0.2, 0.2, 0.2, 1.0), (0, 0, 0, 1), (0.8, 0.8, 0.8), (1, 1, 1), 0.2),
        wcol_tooltip=None, widget_text_cursor=(0.2, 0.6, 1.0), link=(0.4, 0.7, 1.0),
    )
    for k, v in ui_overrides.items():
        setattr(ui, k, v)
    return NS(themes=[NS(user_interface=ui)], system=NS(ui_scale=ui_scale, pixel_size=1.0),
              view=NS(ui_scale=1.0), ui_styles=[NS(widget=NS(points=points))])


class TestStyle(unittest.TestCase):

    def test_reads_theme(self):
        s = st.style_from_preferences(_prefs())
        self.assertEqual(s.back, (0.3, 0.3, 0.3, 1.0))
        self.assertEqual(s.text, (0.9, 0.9, 0.9, 1.0))          # RGB -> RGBA
        self.assertEqual(s.button.inner_sel, (0.3, 0.5, 0.8, 1.0))
        self.assertEqual(s.box.roundness, 0.2)
        self.assertEqual(s.cursor, (0.2, 0.6, 1.0, 1.0))
        self.assertEqual(s.tooltip, st.Style().tooltip)          # missing widget -> fallback
        self.assertEqual(s.font_points, 12.0)

    def test_scale_and_derived_sizes(self):
        s = st.style_from_preferences(_prefs(ui_scale=2.0, points=10.0), zoom=1.5)
        self.assertEqual(s.scale, 3.0)
        self.assertEqual(s.unit, 60.0)
        self.assertEqual(s.font_size, 30.0)
        self.assertAlmostEqual(s.radius(s.box), 0.2 * 60.0 * 0.5)
        self.assertEqual(st.style_from_preferences(_prefs(), zoom=99).zoom, 3.0)
        self.assertEqual(s.with_zoom(0.1).zoom, 0.5)

    def test_background_scale_fallback(self):
        prefs = _prefs(ui_scale=0.0)                    # background mode reports 0
        prefs.view.ui_scale, prefs.system.pixel_size = 1.25, 2.0
        self.assertEqual(st.style_from_preferences(prefs).ui_scale, 2.5)

    def test_broken_preferences_fall_back(self):
        self.assertEqual(st.style_from_preferences(NS()).back, st.Style().back)
        s = st.style_from_preferences(_prefs(panel_back="not a colour"))
        self.assertEqual(s.back, st.Style().back)


class TestWrap(unittest.TestCase):
    measure = staticmethod(lay.fixed_measure(1.0))  # 1 char = size px; size 1 -> 1 px per char

    def wrap(self, text, width):
        return lay.wrap_text(text, width, self.measure, "ui", 1.0)

    def test_words(self):
        self.assertEqual(self.wrap("the quick brown fox", 10), ["the quick", "brown fox"])

    def test_long_word_is_split(self):
        self.assertEqual(self.wrap("abcdefghijkl xy", 5), ["abcde", "fghij", "kl xy"])

    def test_newlines_and_blank_lines(self):
        self.assertEqual(self.wrap("one\n\ntwo", 10), ["one", "", "two"])

    def test_empty(self):
        self.assertEqual(self.wrap("", 10), [""])


class TestCanvas(unittest.TestCase):

    def canvas(self, **state):
        return lay.Canvas(st.Style(), lay.fixed_measure(0.5), lay.InteractionState(**state))

    def test_box_uses_theme(self):
        c = self.canvas()
        r = lay.Rect(0, 0, 100, 40)
        c.box(r)
        c.box(r, selected=True, outline=False)
        kinds = [(cmd.kind, cmd.color) for cmd in c.commands]
        self.assertEqual(kinds, [("rect", st.Style().box.inner), ("outline", st.Style().box.outline),
                                 ("rect", st.Style().box.inner_sel)])

    def test_button_hover_press_click(self):
        r = lay.Rect(10, 10, 80, 20)
        idle = self.canvas()
        self.assertFalse(idle.button(r, "send", "Send"))
        idle_inner = idle.commands[0].color
        hover = self.canvas(mouse=(20, 15))
        hover.button(r, "send", "Send")
        self.assertNotEqual(hover.commands[0].color, idle_inner)
        pressed = self.canvas(pressed="send")
        pressed.button(r, "send", "Send")
        self.assertEqual(pressed.commands[0].color, st.Style().button.inner_sel)
        clicked = self.canvas(clicked="send")
        self.assertTrue(clicked.button(r, "send", "Send"))
        self.assertEqual(clicked.hit_test(20, 15), "send")
        self.assertIsNone(clicked.hit_test(200, 15))

    def test_hit_test_prefers_last_drawn(self):
        c = self.canvas()
        c.button(lay.Rect(0, 0, 100, 100), "back", "")
        c.button(lay.Rect(10, 10, 20, 20), "front", "")
        self.assertEqual(c.hit_test(15, 15), "front")
        self.assertEqual(c.hit_test(80, 80), "back")

    def test_clip_nesting(self):
        c = self.canvas()
        c.push_clip(lay.Rect(0, 0, 100, 100))
        c.push_clip(lay.Rect(50, 50, 100, 100))
        c.pop_clip()
        c.pop_clip()
        clips = [cmd for cmd in c.commands]
        self.assertEqual(clips[1].rect, lay.Rect(50, 50, 50, 50))    # intersected
        self.assertEqual(clips[2].rect, lay.Rect(0, 0, 100, 100))    # back to outer
        self.assertEqual(clips[3].kind, "unclip")

    def test_wrapped_height_matches_prediction(self):
        c = self.canvas()
        text = "a fairly long sentence that has to wrap over several lines here"
        predicted = c.text_height(text, 120)
        new_top = c.wrapped(0, 500, 120, text)
        self.assertAlmostEqual(500 - new_top, predicted)
        self.assertGreater(len([cmd for cmd in c.commands if cmd.kind == "text"]), 1)

    def test_label_alignment(self):
        c = self.canvas()
        r = lay.Rect(0, 0, 200, 20)
        c.label(r, "abc", align="right")
        width = lay.fixed_measure(0.5)("abc", "ui", c.style.font_size)
        self.assertAlmostEqual(c.commands[0].rect.x, 200 - width)


if __name__ == "__main__":
    unittest.main()
