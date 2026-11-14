# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Wand 0 (#331): Remove Silences in the Sequencer and the first wand icons.

1. The silence logic (``wand/silences.py``, bpy-free): nothing quiet, all quiet,
   silence at the start and the end, padding, padding larger than the gap, the
   minimum silence and the minimum kept clip, seconds to frames at several frame
   rates with the strip's start, trim and sound offset, the ripple shifts, which
   picture strip goes with a sound, and the preview line.
2. The icons: every wand icon has its SVG source and 32 / 64 px PNGs in both
   themes, at the right size and not blank; the theme and size choice; the
   build script's light-theme colour swap; the add-on build keeps the PNGs.
3. Placement (``wand/sequencer.py`` with a stand-in ``bpy``): register appends
   to the Strip menu and registers the operator and the sidebar panel (the
   toolshelf panel only where the Sequencer has toolshelf tabs); unregister
   takes all of it out again.  The spec gives the operator its idname, label and
   defaults.

Run with::

    python -m unittest tests.test_wand_remove_silences -v
"""

__all__ = ()

import importlib
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ADDON = _ROOT / "addon" / "bfa_coworker"
_PKG = "bfacw_wand0_pkg"


def _package() -> None:
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg


def _import(name: str):
    _package()
    return importlib.import_module(_PKG + "." + name)


S = _import("wand.silences")


def _env(*parts):
    """An envelope from (dB, windows) parts, 10 ms windows."""
    out = []
    for value, count in parts:
        out.extend([value] * count)
    return out


def _round(ranges, digits=3):
    return [(round(a, digits), round(b, digits)) for a, b in ranges]


class TestFindSilences(unittest.TestCase):

    def test_nothing_quiet(self):
        self.assertEqual(S.find_silences(_env((-12.0, 500)), 0.01, -40.0, 0.4, 0.08), [])

    def test_all_quiet(self):
        self.assertEqual(_round(S.find_silences(_env((-70.0, 300)), 0.01, -40.0, 0.4, 0.08)), [(0.0, 3.0)])

    def test_empty(self):
        self.assertEqual(S.find_silences([], 0.01, -40.0, 0.4), [])

    def test_middle_silence_keeps_padding_both_sides(self):
        env = _env((-10.0, 100), (-70.0, 100), (-10.0, 100))
        self.assertEqual(_round(S.find_silences(env, 0.01, -40.0, 0.4, 0.1)), [(1.1, 1.9)])

    def test_silence_at_the_ends_has_no_padding_on_the_outside(self):
        env = _env((-70.0, 100), (-10.0, 100), (-70.0, 100))
        self.assertEqual(_round(S.find_silences(env, 0.01, -40.0, 0.4, 0.1)), [(0.0, 0.9), (2.1, 3.0)])

    def test_shorter_than_the_minimum_stays(self):
        env = _env((-10.0, 100), (-70.0, 30), (-10.0, 100), (-70.0, 40), (-10.0, 100))
        self.assertEqual(_round(S.find_silences(env, 0.01, -40.0, 0.4, 0.0)), [(2.3, 2.7)])

    def test_padding_larger_than_the_gap_drops_it(self):
        env = _env((-10.0, 100), (-70.0, 50), (-10.0, 100))
        self.assertEqual(S.find_silences(env, 0.01, -40.0, 0.4, 0.3), [])
        self.assertEqual(S.find_silences(env, 0.01, -40.0, 0.4, 0.25), [])     # exactly used up

    def test_threshold_is_strict(self):
        env = _env((-10.0, 100), (-40.0, 100), (-10.0, 100))
        self.assertEqual(S.find_silences(env, 0.01, -40.0, 0.4), [])
        self.assertEqual(_round(S.find_silences(env, 0.01, -39.9, 0.4)), [(1.0, 2.0)])

    def test_short_keep_between_cuts_is_removed(self):
        env = _env((-10.0, 100), (-70.0, 100), (-10.0, 5), (-70.0, 100), (-10.0, 100))
        self.assertEqual(_round(S.find_silences(env, 0.01, -40.0, 0.4, 0.0, min_keep_s=0.1)), [(1.0, 3.05)])
        self.assertEqual(_round(S.find_silences(env, 0.01, -40.0, 0.4, 0.0, min_keep_s=0.0)),
                         [(1.0, 2.0), (2.05, 3.05)])

    def test_short_keep_at_the_ends_is_removed(self):
        env = _env((-10.0, 3), (-70.0, 100), (-10.0, 100), (-70.0, 100), (-10.0, 4))
        self.assertEqual(_round(S.find_silences(env, 0.01, -40.0, 0.4, 0.0, min_keep_s=0.1)),
                         [(0.0, 1.03), (2.03, 3.07)])

    def test_total_clamps_the_last_window(self):
        env = _env((-10.0, 100), (-70.0, 100))
        self.assertEqual(_round(S.find_silences(env, 0.01, -40.0, 0.4, 0.1, total_s=1.95)), [(1.1, 1.95)])


class TestFrames(unittest.TestCase):

    def test_seconds_to_frames_at_24(self):
        self.assertEqual(S.to_frames([(1.0, 2.0)], 24.0, 10.0, 0.0, 10, 250), [(34, 58)])

    def test_other_frame_rates(self):
        self.assertEqual(S.to_frames([(1.0, 2.0)], 25.0, 0.0, 0.0, 0, 500), [(25, 50)])
        self.assertEqual(S.to_frames([(1.0, 2.0)], 30000 / 1001, 0.0, 0.0, 0, 500), [(30, 60)])
        self.assertEqual(S.to_frames([(0.5, 1.25)], 60.0, 100.0, 0.0, 0, 500), [(130, 175)])

    def test_sound_offset_and_handles(self):
        # The sound starts 0.5 s in: sound time 1.0 s plays at frame 10 + 0.5 * 24.
        self.assertEqual(S.to_frames([(1.0, 2.0)], 24.0, 10.0, 0.5, 0, 500), [(22, 46)])
        # Clamped to the handles; a range outside them goes.
        self.assertEqual(S.to_frames([(0.0, 2.0), (5.0, 6.0)], 24.0, 0.0, 0.0, 12, 100), [(12, 48)])

    def test_merge_and_sub_frame(self):
        self.assertEqual(S.to_frames([(1.0, 2.0), (1.9, 3.0), (4.0, 4.01)], 24.0, 0.0, 0.0, 0, 500), [(24, 72)])

    def test_sound_window(self):
        # Trimmed by 12 frames at 24 fps, 0.25 s offset: the visible part starts 0.75 s in.
        self.assertEqual(S.sound_window(22, 106, 10.0, 0.25, 24.0), (0.75, 4.25))
        self.assertEqual(S.sound_window(0, 24, 10.0, 0.0, 24.0), (0.0, 7 / 12))

    def test_merge_ranges(self):
        self.assertEqual(S.merge_ranges([(5, 6), (1, 3), (2, 4), (7, 7)]), [(1, 4), (5, 6)])
        self.assertEqual(S.merge_ranges([(1, 3), (4, 5)], gap=1), [(1, 5)])

    def test_ripple_shifts(self):
        removed = [(10, 20), (40, 45)]
        # before, right after the first cut, inside the first, between, after both, spanning both.
        self.assertEqual(S.ripple_shifts([0, 20, 15, 30, 45, 100], removed), [0, 10, 5, 10, 15, 15])

    def test_removed_frames(self):
        self.assertEqual(S.removed_frames([(10, 20), (40, 45)]), 15)


class TestPartners(unittest.TestCase):

    def _strip(self, name, kind, channel, left=10, right=100, start=10.0, path="", connected=()):
        return {"name": name, "type": kind, "channel": channel, "left": left, "right": right,
                "content_start": start, "path": path, "connected": list(connected)}

    def test_connected(self):
        sound = self._strip("talk.001", "SOUND", 1, connected=["shot"])
        self.assertTrue(S.is_partner(sound, self._strip("shot", "COLOR", 5)))

    def test_same_movie_file(self):
        sound = self._strip("a", "SOUND", 1, path="C:/media/Talk.mp4")
        self.assertTrue(S.is_partner(sound, self._strip("b", "MOVIE", 4, path="c:\\media\\talk.mp4")))

    def test_next_channel_same_start_same_name(self):
        sound = self._strip("talk.001", "SOUND", 1)
        self.assertTrue(S.is_partner(sound, self._strip("talk.mp4", "MOVIE", 2)))
        self.assertTrue(S.is_partner(sound, self._strip("talk", "IMAGE", 2)))
        self.assertFalse(S.is_partner(sound, self._strip("talk", "IMAGE", 3)))          # two channels away
        self.assertFalse(S.is_partner(sound, self._strip("talk", "IMAGE", 2, start=30.0)))
        self.assertFalse(S.is_partner(sound, self._strip("other", "IMAGE", 2)))

    def test_not_a_partner(self):
        sound = self._strip("talk", "SOUND", 1, connected=["music", "late"])
        self.assertFalse(S.is_partner(sound, self._strip("music", "SOUND", 2)))          # a sound
        self.assertFalse(S.is_partner(sound, self._strip("late", "COLOR", 2, left=100, right=150)))
        self.assertFalse(S.is_partner(sound, self._strip("caption", "TEXT", 2)))
        self.assertFalse(S.is_partner(sound, sound))


class TestText(unittest.TestCase):

    def test_format_duration(self):
        self.assertEqual(S.format_duration(2.43), "0:02.4")
        self.assertEqual(S.format_duration(41.2), "0:41")
        self.assertEqual(S.format_duration(725), "12:05")
        self.assertEqual(S.format_duration(3723), "1:02:03")

    def test_summary(self):
        self.assertEqual(S.summary(12, 984, 24.0), "12 silences, 0:41 removed")
        self.assertEqual(S.summary(1, 12, 24.0), "1 silence, 0:00.5 removed")
        self.assertEqual(S.summary(0, 0, 24.0), "No silences found")

    def test_db_and_rms(self):
        self.assertAlmostEqual(S.db(1.0), 0.0)
        self.assertAlmostEqual(S.db(0.01), -40.0)
        self.assertEqual(S.db(0.0), -200.0)
        self.assertEqual([round(v, 3) for v in S.window_rms([1, -1, 0.5, -0.5, 9], 2)], [1.0, 0.5])


class TestIcons(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.icons = _import("wand.icons")
        cls.png = _import("ui_gpu.png")

    def test_every_icon_has_its_files(self):
        for name in self.icons.ICON_NAMES:
            self.assertTrue(os.path.isfile(os.path.join(self.icons.ICON_DIR, name + ".svg")), name)
            renders = []
            for theme in self.icons.THEMES:
                for size in self.icons.SIZES:
                    path = self.icons.png_path(name, theme, size)
                    self.assertTrue(os.path.isfile(path), path)
                    with open(path, "rb") as fh:
                        width, height, rgba = self.png.read_rgba(fh.read())
                    self.assertEqual((width, height), (size, size), path)
                    self.assertGreater(sum(1 for a in rgba[3::4] if a > 0), size * size // 20, path)
                    renders.append(rgba)
            self.assertNotEqual(renders[0], renders[2], name + ": the light render is the dark one")

    def test_eight_icons(self):
        self.assertEqual(len(self.icons.ICON_NAMES), 8)
        self.assertIn("remove_silences", self.icons.ICON_NAMES)

    def test_theme_and_size(self):
        self.assertEqual(self.icons.theme_for((0.9, 0.9, 0.9)), "dark")
        self.assertEqual(self.icons.theme_for((0.1, 0.1, 0.1, 1.0)), "light")
        self.assertEqual(self.icons.size_for(1.0), 32)
        self.assertEqual(self.icons.size_for(2.0), 64)

    def test_svgs_are_ours(self):
        for name in self.icons.ICON_NAMES:
            text = Path(self.icons.ICON_DIR, name + ".svg").read_text(encoding="utf-8")
            self.assertIn("Original drawing for the Coworker add-on", text, name)
            self.assertNotIn("<image", text, name)

    def test_build_script_light_swap(self):
        spec = importlib.util.spec_from_file_location("bfacw_build_icons", _ROOT / "_misc" / "build_icons.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        light = build.light_variant('<path stroke="#e6e6e6"/><path fill="#ffc93c"/><path fill="#4c9bd9"/>')
        self.assertNotIn("#e6e6e6", light)
        self.assertNotIn("#ffc93c", light)
        self.assertIn("#4c9bd9", light)
        self.assertEqual(Path(build.ICON_DIR).resolve(), Path(self.icons.ICON_DIR).resolve())

    def test_build_keeps_the_pngs(self):
        manifest = (_ADDON / "blender_manifest.toml").read_text(encoding="utf-8")
        for pattern in ("*.png", "icons/", "icons\\"):
            self.assertNotIn('"' + pattern, manifest.split("[build]", 1)[-1] if "[build]" in manifest else "")


# -- placement with a stand-in bpy -------------------------------------------------------

class _Props:
    def __init__(self, names):
        self._names = set(names)

    def __contains__(self, name):
        return name in self._names


class _Menu:
    def __init__(self):
        self.funcs = []

    def append(self, fn):
        self.funcs.append(fn)

    def remove(self, fn):
        self.funcs.remove(fn)


def _fake_bpy(toolshelf_tabs: bool):
    bpy = types.ModuleType("bpy")
    registered = []
    space = type("SpaceSequenceEditor", (), {"bl_rna": types.SimpleNamespace(
        properties=_Props(["view_type"] + (["show_toolshelf_tabs"] if toolshelf_tabs else [])))})
    bpy.types = types.SimpleNamespace(Operator=type("Operator", (), {}), Panel=type("Panel", (), {}),
                                      SEQUENCER_MT_strip=_Menu(), SpaceSequenceEditor=space)
    bpy.props = types.SimpleNamespace(FloatProperty=lambda **kw: ("float", kw), BoolProperty=lambda **kw: ("bool", kw))
    bpy.utils = types.SimpleNamespace(register_class=registered.append,
                                      unregister_class=registered.remove)
    bpy.path = types.SimpleNamespace(abspath=lambda p, library=None: p)
    bpy.registered = registered
    return bpy


def _load_sequencer(bpy):
    names = ("bpy", "bpy.props", "bpy.types")
    saved = {name: sys.modules.get(name) for name in names}
    sys.modules.update({"bpy": bpy, "bpy.props": bpy.props, "bpy.types": bpy.types})
    try:
        _package()
        sys.modules.pop(_PKG + ".wand.sequencer", None)
        return importlib.import_module(_PKG + ".wand.sequencer")
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class TestPlacement(unittest.TestCase):

    def _cycle(self, toolshelf_tabs):
        bpy = _fake_bpy(toolshelf_tabs)
        seq = _load_sequencer(bpy)
        menu = bpy.types.SEQUENCER_MT_strip
        seq.register()
        names = [cls.__name__ for cls in bpy.registered]
        self.assertEqual(len(menu.funcs), 1)
        seq.unregister()
        self.assertEqual(menu.funcs, [])
        self.assertEqual(bpy.registered, [])
        return names, seq

    def test_blender(self):
        names, _seq = self._cycle(toolshelf_tabs=False)
        self.assertEqual(names, ["COWORKER_OT_sequencer_remove_silences", "COWORKER_PT_sequencer_sidebar"])

    def test_bforartists(self):
        names, _seq = self._cycle(toolshelf_tabs=True)
        self.assertIn("COWORKER_PT_sequencer_toolshelf", names)

    def test_register_twice_cycle(self):
        bpy = _fake_bpy(False)
        seq = _load_sequencer(bpy)
        for _ in range(2):
            seq.register()
            seq.unregister()
        self.assertEqual(bpy.types.SEQUENCER_MT_strip.funcs, [])
        self.assertEqual(bpy.registered, [])

    def test_spec(self):
        seq = _load_sequencer(_fake_bpy(False))
        op = seq.COWORKER_OT_sequencer_remove_silences
        self.assertEqual(op.bl_idname, "coworker.sequencer_remove_silences")
        self.assertEqual(op.bl_label, "Remove Silences")
        self.assertEqual(op.bl_options, {"REGISTER", "UNDO"})
        self.assertEqual(seq.SPEC.kind, "tool")
        self.assertEqual(seq.SPEC.model, "none")
        self.assertEqual([p.kind for p in seq.SPEC.places], ["menu", "toolshelf", "sidebar"])
        self.assertEqual(seq.SPEC.places[0].target, "SEQUENCER_MT_strip")
        self.assertIn(seq.SPEC.icon, _import("wand.icons").ICON_NAMES)
        self.assertEqual(seq.SPEC.param("ripple").default, True)
        self.assertEqual(seq.SPEC.param("threshold_db").default, -40.0)

    def test_entry_draws_like_operator_entry(self):
        seq = _load_sequencer(_fake_bpy(True))
        calls = []

        class _Layout:
            def operator(self, idname, **kw):
                calls.append((idname, kw))

        entry = seq.CoworkerEntry(seq.SPEC.bl_idname, "Remove Silences", "remove_silences", "SPEAKER")
        entry.draw(_Layout(), as_icon=True)
        entry.draw(_Layout(), as_icon=False)
        seq.CoworkerEntry("x", "X", "wand", "SPEAKER", poll=False).draw(_Layout(), as_icon=True)
        self.assertEqual([c[0] for c in calls], [seq.SPEC.bl_idname] * 2)
        self.assertEqual(calls[0][1]["text"], "")
        self.assertEqual(calls[1][1]["text"], "Remove Silences")
        # No previews without Blender: the Blender icon.
        self.assertEqual(calls[0][1]["icon"], "SPEAKER")


if __name__ == "__main__":
    unittest.main()
