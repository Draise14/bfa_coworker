# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #622: the Reframe buttons drawn as Bforartists' toolshelf entries, and the Reframe panels in the
Preview (bpy-free).

Run with::

    python -m unittest tests.test_reframe_entries -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_entries_pkg"


def _import(name: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


E = _import("wand.reframe_entries")
rl = _import("wand.reframe_layout")


class _Op:
    pass


class _Layout:
    """A UILayout stand-in recording every operator drawn (through nested rows too)."""

    def __init__(self, log=None, enabled=True):
        self.log = [] if log is None else log
        self.enabled = enabled

    def row(self, align=False):
        return _Layout(self.log, self.enabled)

    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)

    def operator(self, idname, **kwargs):
        op = _Op()
        self.log.append((idname, kwargs, op, self.enabled))
        return op


def _draw(entry, as_icon):
    layout = _Layout()
    entry.draw(layout, as_icon=as_icon)
    return layout.log


class TestEntries(unittest.TestCase):

    def setUp(self):
        self.patch = [(E.icons, "icon_id", E.icons.icon_id), (E, "_blender_icon", E._blender_icon)]
        E.icons.icon_id = lambda name: 7 if name else 0
        E._blender_icon = lambda name: "B:" + name

    def tearDown(self):
        for obj, name, value in self.patch:
            setattr(obj, name, value)

    def _real(self, current=""):
        return [e for e in E.entries(current) if e is not E.SEPARATOR]

    def test_one_entry_per_ready_layout_then_track_all_swap_reset(self):
        got = self._real()
        layouts = [e for e in got if isinstance(e, E.LayoutEntry)]
        self.assertEqual([e.item.id for e in layouts if e.enabled], list(rl.buildable()))
        self.assertEqual([e.item.id for e in layouts if not e.enabled], [])
        self.assertEqual([e.operator for e in got[-2:]], ["bfacw.reframe_swap", "bfacw.reframe_reset"])
        self.assertEqual(got.index(next(e for e in layouts if e.item.id == "track_all")), len(layouts) - 1)

    def test_each_ready_layout_sets_its_layout_on_the_operator(self):
        for entry in self._real():
            if not isinstance(entry, E.LayoutEntry) or not entry.enabled:
                continue
            for as_icon in (True, False):
                (idname, kwargs, op, _en), = _draw(entry, as_icon)
                self.assertEqual(idname, "bfacw.reframe_layout")
                self.assertEqual(op.layout, entry.item.id)
                self.assertEqual(kwargs["text"], "" if as_icon else entry.item.short)

    def test_only_the_current_layout_is_pressed(self):
        for item in rl.LAYOUTS:
            if not item.ready:
                continue
            pressed = [e.item.id for e in self._real(item.id) if isinstance(e, E.LayoutEntry)
                       and _draw(e, True)[0][1]["depress"]]
            self.assertEqual(pressed, [item.id])
        self.assertFalse(any(_draw(e, True)[0][1]["depress"] for e in self._real("") if isinstance(e, E.LayoutEntry)))

    def test_track_all_is_enabled_now_that_subjects_are_found(self):
        got = self._real()
        track = next(e for e in got if isinstance(e, E.LayoutEntry) and e.item.id == "track_all")
        self.assertTrue(track.enabled)
        self.assertTrue(all(_draw(e, True)[0][3] for e in got))

    def test_icons_are_pngs_with_the_blender_fallback(self):
        entry = self._real("full")[0]
        self.assertEqual(_draw(entry, True)[0][1]["icon_value"], 7)
        E.icons.icon_id = lambda name: 0
        kwargs = _draw(entry, True)[0][1]
        self.assertNotIn("icon_value", kwargs)
        self.assertEqual(kwargs["icon"], "B:" + entry.item.fallback)

    def test_swap_and_reset_follow_the_icon_rule(self):
        swap, reset = self._real()[-2:]
        self.assertEqual(_draw(swap, True)[0][1]["text"], "")
        self.assertEqual(_draw(reset, False)[0][1]["text"], "Reset")

    def test_groups_are_apart_by_the_given_separator(self):
        sep = object()
        got = E.entries("", sep)
        self.assertEqual(got.count(sep), 2)
        self.assertNotIn(E.SEPARATOR, got)

    def test_an_entry_works_for_bforartists_draw_entries(self):
        # draw_entries reads only ``poll`` and ``draw(row, as_icon=...)``.
        for entry in self._real():
            self.assertTrue(entry.poll)
            self.assertTrue(callable(entry.draw))


class _Col(_Layout):
    def column(self, align=False):
        return self


class TestToolshelfDraw(unittest.TestCase):

    def setUp(self):
        self.saved = E._blender_icon, E.icons.icon_id
        E._blender_icon = lambda name: "B:" + name
        E.icons.icon_id = lambda name: 0

    def tearDown(self):
        E._blender_icon, E.icons.icon_id = self.saved

    def test_it_draws_through_draw_entries_with_its_separator(self):
        seen = []
        sep = object()
        tools = types.SimpleNamespace(Separator=sep, draw_entries=lambda layout, ctx, ents: seen.append(ents))
        layout = _Col()
        E.draw_toolshelf(layout, None, "full", tools)
        self.assertEqual(layout.log, [])
        self.assertEqual(seen[0].count(sep), 2)

    def test_an_exception_in_draw_entries_falls_back_to_the_plain_column(self):
        def boom(*_a):
            raise RuntimeError("changed")
        layout = _Col()
        E.draw_toolshelf(layout, None, "full", types.SimpleNamespace(Separator=object(), draw_entries=boom))
        ids = [row[0] for row in layout.log]
        self.assertEqual(ids.count("bfacw.reframe_layout"), 7)
        self.assertEqual(ids[-2:], ["bfacw.reframe_swap", "bfacw.reframe_reset"])

    def test_without_the_helper_it_is_the_plain_column_too(self):
        for tools in (types.SimpleNamespace(), types.SimpleNamespace(draw_entries=None, Separator=None)):
            layout = _Col()
            E.draw_toolshelf(layout, None, "", tools)
            self.assertEqual(len(layout.log), 9)


class TestViews(unittest.TestCase):

    def test_the_preview_alone_is_included(self):
        for view in ("SEQUENCER", "SEQUENCER_PREVIEW", "PREVIEW"):
            self.assertTrue(E.shows_in(view), view)
        for view in ("", None, "OTHER"):
            self.assertFalse(E.shows_in(view), view)

    def test_both_reframe_panels_poll_through_it(self):
        text = (_ADDON / "reframe_layouts.py").read_text(encoding="utf-8")
        for name in ("BFACW_PT_reframe_sidebar", "BFACW_PT_reframe_toolshelf"):
            body = text[text.index("class " + name):]
            body = body[:body.index("def draw_header")]
            self.assertIn("re_entries.shows_in(", body, name)
            self.assertNotIn("_wand.VIEWS", body, name)
        toolshelf = text[text.index("class BFACW_PT_reframe_toolshelf"):]
        self.assertIn("show_toolshelf_tabs", toolshelf[:toolshelf.index("def draw_header")])

    def test_the_video_panel_stays_out_of_the_preview_alone(self):
        text = (_ADDON / "wand" / "sequencer.py").read_text(encoding="utf-8")
        self.assertIn('VIEWS = frozenset({"SEQUENCER", "SEQUENCER_PREVIEW"})', text)

    def test_the_toolshelf_draws_through_draw_entries(self):
        text = (_ADDON / "reframe_layouts.py").read_text(encoding="utf-8")
        self.assertIn("re_entries.draw_toolshelf(self.layout, context, _active_layout(context))", text)
        self.assertNotIn("wide=", text)
        self.assertNotIn(chr(92) + "n", text)      # a lost line continuation (#628 review)


if __name__ == "__main__":
    unittest.main()
