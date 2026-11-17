# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Caption animation presets, position and speakers (#542): the presets as a description of
keyframes (on any style, clamped to a caption's own frames), Bottom / Top / Middle with a margin
in a wide and a vertical frame, the ``add_captions`` tool's new arguments, the signing rule
(an animation or position Coworker sets is not an edit by hand), a channel per speaker, and
Remove Silences / Make Vertical fitting the animation again.  No Blender: made-up words,
``captions_ops`` on the stand-in ``bpy`` of ``test_captions``.

Run with::

    python -m unittest tests.test_captions_look -v
"""

__all__ = ()

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_captions import _ROOT, _TOOLS, TestCaptionStrips, _Layout, _OpsCase, _clip, _Strips, cap

WIDE = (1920, 1080)
TALL = (1080, 1920)
FPS = 30.0
STYLES = tuple(cap.STYLES)
PRESETS = ("NONE", "FADE", "SLIDE_UP", "TYPEWRITER", "POP")


def _measure(text, px):
    return len(text) * px * 0.5


def _tracks(anim, style="CLEAN", fs=100, fe=160, res=WIDE, **kw):
    if anim == "TYPEWRITER" and "reveal" not in kw:
        kw["reveal"] = cap.reveal_crops("Made up words here", _measure, 54, res[0])
    return cap.animation_tracks(anim, style, fs, fe, FPS, res[0], res[1], **kw)


# -- the presets, as a description ------------------------------------------------------------

class TestPresets(unittest.TestCase):

    def test_names(self):
        self.assertEqual(cap.animation_key("slide up"), "SLIDE_UP")
        self.assertEqual(cap.animation_key("Typewriter"), "TYPEWRITER")
        self.assertEqual(cap.animation_key(None), cap.ANIM_STYLE)
        self.assertEqual(cap.animation_key("off"), "NONE")
        self.assertIsNone(cap.animation_key("wobble"))
        self.assertEqual(set(cap.ANIMATIONS), {cap.ANIM_STYLE, *PRESETS})

    def test_every_preset_on_every_style_keys_only_its_own_properties(self):
        for style in STYLES:
            for anim in PRESETS:
                tracks = _tracks(anim, style, font_size=54)
                for t in tracks:
                    self.assertIn(t["path"], cap.ANIMATED_PATHS, (style, anim))
                    self.assertIn(t["interpolation"], ("BEZIER", "CONSTANT"))
                    frames = [f for f, _v in t["keys"]]
                    self.assertEqual(frames, sorted(set(frames)), (style, anim, t["path"]))
                self.assertEqual(bool(tracks), anim != "NONE", (style, anim))

    def test_the_signature_never_animates(self):
        """#552's signature is the text, length, channel and place: no preset keys any of them."""
        for path in cap.ANIMATED_PATHS:
            self.assertNotIn(path.split(".")[0], ("text", "location", "channel", "frame_start", "left_handle",
                                                  "right_handle", "content_start"))

    def test_fade(self):
        (t,) = _tracks("FADE")
        self.assertEqual(t["path"], "blend_alpha")
        self.assertEqual(t["keys"], [(100, 0.0), (108, 1.0), (154, 1.0), (160, 0.0)])     # 0.25 s in, 0.2 s out
        self.assertEqual(t["steady"], 1.0)

    def test_slide_up_rises_into_place_as_it_fades_in(self):
        tracks = {t["path"]: t for t in _tracks("SLIDE_UP")}
        self.assertEqual(set(tracks), {"blend_alpha", "transform.offset_y"})
        self.assertEqual(tracks["transform.offset_y"]["keys"], [(100, -round(0.035 * 1080, 2)), (108, 0.0)])
        tall = {t["path"]: t for t in _tracks("SLIDE_UP", res=TALL)}
        self.assertAlmostEqual(tall["transform.offset_y"]["keys"][0][1], -0.035 * 1920, 2)   # the frame's own height

    def test_pop_grows_around_the_caption_with_an_overshoot(self):
        tracks = {t["path"]: t for t in _tracks("POP", font_size=54, lines=2, anchor_y="BOTTOM",
                                                  location=(0.5, 0.09))}
        self.assertEqual(set(tracks), {"transform.scale_x", "transform.scale_y", "transform.offset_y"})
        values = [v for _f, v in tracks["transform.scale_x"]["keys"]]
        self.assertEqual(values[0], cap.POP_FROM)
        self.assertGreater(values[1], 1.0)
        self.assertEqual(values[-1], 1.0)
        # Its middle (the bottom edge plus half of two lines) stays put: a strip scales around the
        # frame's centre, so at scale s it is moved by (middle - centre) x (1 - s).
        middle = 0.09 + 2 * 54 * cap.LINE_HEIGHT / 1080 / 2
        self.assertAlmostEqual(cap.pop_middle(2, 54, 1080, "BOTTOM", (0.5, 0.09)), middle)
        for (f, s), (g, off) in zip(tracks["transform.scale_y"]["keys"], tracks["transform.offset_y"]["keys"]):
            self.assertEqual(f, g)
            shown = 540 + (middle * 1080 - 540) * s + off       # where the middle is drawn at this key
            self.assertAlmostEqual(shown, middle * 1080, 2)
        self.assertEqual(tracks["transform.offset_y"]["keys"][-1][1], 0.0)
        self.assertLess(cap.pop_middle(1, 54, 1080, "TOP", (0.5, 0.88)), 0.88)
        self.assertEqual(cap.pop_middle(1, 54, 1080, "CENTER", (0.5, 0.5)), 0.5)
        flat = {t["path"]: t for t in _tracks("POP", font_size=54, anchor_y="CENTER", location=(0.5, 0.5))}
        self.assertTrue(all(v == 0.0 for _f, v in flat["transform.offset_y"]["keys"]))

    def test_typewriter_reveals_letter_by_letter_then_holds(self):
        tracks = {t["path"]: t for t in _tracks("TYPEWRITER")}
        crop = tracks["crop.max_x"]
        self.assertEqual(crop["interpolation"], "CONSTANT")
        crops = [v for _f, v in crop["keys"]]
        self.assertEqual(crops, sorted(crops, reverse=True))      # more and more shows
        self.assertEqual(crops[-1], 0.0)                           # all of it at the end
        self.assertEqual(crop["keys"][0][0], 100)
        self.assertLessEqual(crop["keys"][-1][0], 100 + 30)        # within a second
        self.assertIn("blend_alpha", tracks)                       # and fades out
        self.assertNotIn(0, [k for k, _v in tracks["blend_alpha"]["keys"] if k == 100])

    def test_reveal_crops(self):
        steps = cap.reveal_crops("ab cd", _measure, 20, 1000)   # 50 px wide, centred: 475..525
        self.assertEqual([k for k, _c in steps], [1, 2, 4, 5])  # the space goes with the next letter
        self.assertEqual(steps[0], (1, 1000 - (475 + 10)))
        self.assertEqual(steps[-1], (5, 0))
        self.assertEqual(cap.reveal_crops("  ", _measure, 20, 1000), [])
        self.assertEqual(cap.one_line("typewriter"), True)
        self.assertEqual(cap.one_line("fade"), False)

    def test_the_styles_own_is_todays(self):
        """STYLE (the default): Bold Pop pops its font size as before #542, the others don't move."""
        (t,) = _tracks(cap.ANIM_STYLE, "BOLD_POP", font_size=81)
        self.assertEqual(t["path"], "font_size")
        self.assertEqual(t["keys"], [(100, 81 * 0.78), (103, 81.0)])
        for style in ("CLEAN", "WORD_HIGHLIGHT", "BOXED"):
            self.assertEqual(_tracks(cap.ANIM_STYLE, style, font_size=60), [])
        self.assertEqual(_tracks(cap.ANIM_STYLE, "BOLD_POP", fs=10, fe=14, font_size=81), [])   # < 5 frames: as before


class TestClamping(unittest.TestCase):

    def test_inside_the_caption_and_in_before_out(self):
        for length in range(1, 80):
            for anim in PRESETS:
                tracks = _tracks(anim, fs=200, fe=200 + length, font_size=54)
                for t in tracks:
                    frames = [f for f, _v in t["keys"]]
                    self.assertGreaterEqual(min(frames), 200, (anim, length))
                    self.assertLessEqual(max(frames), 200 + length, (anim, length))   # the last key may be the end
                    ins = [f for f, v in t["keys"] if f < 200 + length - 1]
                    self.assertTrue(all(f <= 200 + length for f in ins))
                win = cap.animation_window(tracks)
                if win:
                    self.assertGreaterEqual(win[0], 200)

    def test_very_short_captions(self):
        for anim in PRESETS:
            self.assertEqual(_tracks(anim, fs=50, fe=50 + cap.MIN_ANIMATED - 1, font_size=54), [], anim)
        # Six frames: a quick way in only (2 frames), no way out.
        (t,) = _tracks("FADE", fs=50, fe=56)
        self.assertEqual(t["keys"], [(50, 0.0), (52, 1.0)])
        self.assertEqual(_tracks("FADE", fs=50, fe=55), [])     # 5 frames: no 2-frame way in fits a third
        self.assertTrue(_tracks("TYPEWRITER", fs=50, fe=50 + cap.MIN_ANIMATED))
        # Ways in and out never meet.
        for length in range(6, 40):
            (t,) = _tracks("FADE", fs=0, fe=length)
            frames = [f for f, _v in t["keys"]]
            self.assertEqual(frames, sorted(set(frames)), length)
            self.assertLessEqual(max(frames[:2]), length // 3 if len(frames) > 2 else length)

    def test_typewriter_takes_at_most_half_a_short_caption(self):
        crop = next(t for t in _tracks("TYPEWRITER", fs=0, fe=12) if t["path"] == "crop.max_x")
        self.assertLessEqual(crop["keys"][-1][0], 6)
        self.assertEqual(crop["keys"][-1][1], 0.0)

    def test_another_frame_rate(self):
        (t,) = cap.animation_tracks("FADE", "CLEAN", 0, 100, 60.0, *WIDE)
        self.assertEqual(t["keys"][1], (15, 1.0))           # 0.25 s at 60 fps


# -- position ----------------------------------------------------------------------------------

class TestPosition(unittest.TestCase):

    def test_names(self):
        self.assertEqual(cap.position_key("top"), "TOP")
        self.assertEqual(cap.position_key("centre"), "MIDDLE")
        self.assertEqual(cap.position_key(None), "BOTTOM")
        self.assertIsNone(cap.position_key("left"))

    def test_wide(self):
        self.assertEqual(cap.placement("BOTTOM", 0, *WIDE), ("BOTTOM", (0.5, 0.09)))     # today's place
        self.assertEqual(cap.placement("TOP", 0, *WIDE), ("TOP", (0.5, 0.91)))
        self.assertEqual(cap.placement("MIDDLE", 0.3, *WIDE), ("CENTER", (0.5, 0.5)))    # no margin there
        self.assertEqual(cap.placement("BOTTOM", 0.2, *WIDE), ("BOTTOM", (0.5, 0.2)))
        self.assertEqual(cap.placement("TOP", 0.15, *WIDE), ("TOP", (0.5, 0.85)))
        self.assertEqual(cap.placement("TOP", 0.9, *WIDE), ("TOP", (0.5, 1 - cap.MAX_MARGIN)))

    def test_vertical(self):
        self.assertEqual(cap.placement("BOTTOM", 0, *TALL), ("BOTTOM", (0.5, 0.24)))     # above the platforms' buttons
        self.assertEqual(cap.placement("TOP", 0, *TALL), ("TOP", (0.5, 0.88)))
        self.assertEqual(cap.placement("MIDDLE", 0, *TALL), ("CENTER", (0.5, 0.5)))
        self.assertEqual(cap.placement("BOTTOM", 0.3, *TALL), ("BOTTOM", (0.5, 0.3)))

    def test_resolve_style_places_it(self):
        props = cap.resolve_style("BOXED", *WIDE)["props"]
        self.assertEqual((props["anchor_y"], props["location"]), ("BOTTOM", (0.5, 0.09)))   # as before #542
        st = cap.resolve_style("BOXED", *TALL, position="top", margin=0.1)
        self.assertEqual((st["props"]["anchor_y"], st["props"]["location"]), ("TOP", (0.5, 0.9)))
        self.assertEqual((st["position"], st["margin"]), ("TOP", 0.1))

    def test_hand_edits_know_the_position(self):
        props = cap.resolve_style("CLEAN", *WIDE, position="TOP")["props"]
        look = {"location": props["location"], "font_size": props["font_size"], "wrap_width": props["wrap_width"],
                "anchor_x": "CENTER", "anchor_y": "TOP", "transform": (0, 0, 1, 1, 0)}
        self.assertEqual(cap.hand_edits(look, "CLEAN", *WIDE, "TOP", 0.0), [])
        self.assertEqual(cap.hand_edits(look, "CLEAN", *WIDE), ["position", "anchor"])       # not the bottom
        look["location"] = (0.5, 0.7)
        self.assertIn("position", cap.hand_edits(look, "CLEAN", *WIDE, "TOP", 0.0))

    def test_relayout_keeps_the_position_in_9_16(self):
        lay = cap.relayout("A few made up words", "CLEAN", *TALL, 10, 50, old_res=WIDE, position="TOP")
        self.assertEqual((lay["style"]["props"]["anchor_y"], lay["style"]["props"]["location"]), ("TOP", (0.5, 0.88)))
        lay = cap.relayout("A few made up words", "CLEAN", *TALL, 10, 50, old_res=WIDE, position="BOTTOM",
                           margin=0.3)
        self.assertEqual(lay["style"]["props"]["location"], (0.5, 0.3))


# -- the tool's arguments ----------------------------------------------------------------------

class TestToolArguments(unittest.TestCase):

    def test_look(self):
        self.assertEqual(cap.check_look_params(None, None, None), (cap.ANIM_STYLE, "BOTTOM", 0.0))
        self.assertEqual(cap.check_look_params("slide-up", "Top", 12), ("SLIDE_UP", "TOP", 0.12))
        self.assertEqual(cap.check_look_params("POP", "middle", "5"), ("POP", "MIDDLE", 0.05))
        for bad in (("wobble", "TOP", 0), ("FADE", "left", 0), ("FADE", "TOP", -1), ("FADE", "TOP", 80),
                    ("FADE", "TOP", "lots")):
            with self.assertRaises(cap.CaptionsError, msg=bad):
                cap.check_look_params(*bad)

    def test_speakers(self):
        self.assertEqual(cap.check_speakers(False, None), (False, []))
        self.assertEqual(cap.check_speakers(True, None), (True, []))
        on, rows = cap.check_speakers(False, [{"strip": "ana.wav", "name": "  Ana  B ", "style": "boxed",
                                                "position": "top"}, {"strip": "ben.wav"}])
        self.assertTrue(on)                                   # settings turn speakers on
        self.assertEqual(rows, [{"strip": "ana.wav", "name": "Ana B", "style": "BOXED", "position": "TOP"},
                                {"strip": "ben.wav"}])
        self.assertEqual(cap.check_speakers(False, {"strip": "x"}), (True, [{"strip": "x"}]))
        for bad in ([{"name": "no strip"}], [{"strip": "a", "style": "neon"}], [{"strip": "a", "position": "left"}],
                    "ana"):
            with self.assertRaises(cap.CaptionsError, msg=bad):
                cap.check_speakers(True, bad)

    def test_the_tool_lists_them_with_todays_defaults(self):
        src = (_TOOLS / "add_captions.py").read_text(encoding="utf-8")
        for arg in ('animation: str = "STYLE"', 'position: str = "BOTTOM"', "margin: float = 0",
                    "speakers: bool = False", "speaker_labels: bool = False",
                    "speaker_settings: list[dict[str, str]] | None = None"):
            self.assertIn(arg, src)
        listing = (_ROOT / "tests" / "test_tool_listing.py").read_text(encoding="utf-8")
        self.assertIn('"title": "Speaker Settings"', listing)
        docs = (_ROOT / "addon" / "bfa_coworker" / "skills" / "mcp_tools.md").read_text(encoding="utf-8")
        self.assertIn("`add_captions(", docs)


# -- speakers ----------------------------------------------------------------------------------

class TestSpeakers(unittest.TestCase):

    def test_names_from_strips(self):
        self.assertEqual(cap.speaker_name("ana_mic.wav.001"), "Ana mic")
        self.assertEqual(cap.speaker_name("Host-2.mp4"), "Host 2")
        self.assertEqual(cap.speaker_name(""), "Speaker")
        self.assertEqual(cap.channel_names("Ana"), ("Captions: Ana", "Captions Highlight: Ana"))
        self.assertEqual(cap.channel_names(""), (cap.CHANNEL_NAME, cap.HIGHLIGHT_CHANNEL_NAME))

    def test_one_per_file_with_settings(self):
        todo = [{"name": "ana.wav", "key": "a"}, {"name": "ben.wav", "key": "b"}, {"name": "ana.wav.001", "key": "a"},
                {"name": "ana2.wav", "key": "c"}]
        plan = cap.plan_speakers(todo, "CLEAN", "BOTTOM", [{"strip": "ana.wav.001", "name": "Ana", "style": "BOXED"},
                                                            {"strip": "ana2.wav", "name": "ana"}], labels=True)
        self.assertEqual(list(plan), ["a", "b", "c"])
        self.assertEqual(plan["a"]["strips"], ["ana.wav", "ana.wav.001"])
        self.assertEqual((plan["a"]["name"], plan["a"]["style"], plan["a"]["label"]), ("Ana", "BOXED", "Ana:"))
        self.assertEqual((plan["b"]["name"], plan["b"]["style"], plan["b"]["position"]), ("Ben", "CLEAN", "BOTTOM"))
        self.assertEqual(plan["c"]["name"], "ana 2")             # two speakers can't share a name (or a channel)
        self.assertEqual(len({p["channel"] for p in plan.values()}), 3)
        self.assertEqual(cap.plan_speakers(todo[:1], "CLEAN", "TOP")["a"]["label"], "")

    def test_labels(self):
        self.assertEqual(cap.with_label("hello\nthere", "Ana:"), "Ana: hello\nthere")
        self.assertEqual(cap.without_label("ANA: hello", "Ana:"), "hello")
        self.assertEqual(cap.without_label("hello", "Ana:"), "hello")
        self.assertEqual(cap.label_chars(36, "Ana:"), 31)
        self.assertEqual(cap.label_chars(12, "Bartholomew:"), 10)

    def test_relayout_keeps_the_label_on_every_piece(self):
        text = "Ana: Made up words that will never fit on one vertical line at this size, so it splits."
        lay = cap.relayout(text, "BOLD_POP", *TALL, 0, 300, old_res=WIDE, label="Ana:")
        self.assertGreater(len(lay["pieces"]), 1)
        for p in lay["pieces"]:
            self.assertTrue(p["text"].startswith("Ana: "), p["text"])
            self.assertNotIn("Ana:", [w["w"] for w in p["words"]])

    def test_pick_channel_skips_another_speakers(self):
        occupied = {3: [(0, 100)]}
        names = {4: "Captions: Ana", 5: "Captions: Ben"}
        self.assertEqual(cap.pick_channel(occupied, names, 2, [(200, 300)], name="Captions: Ben"), 5)
        self.assertEqual(cap.pick_channel(occupied, names, 2, [(200, 300)], name="Captions: Cy", skip={3, 4, 5}), 6)


# -- on the stand-in Blender -------------------------------------------------------------------

def _speech_files(case, words_by_file):
    """Local transcription (#375) as if each file had been transcribed: its own words."""
    import sys
    import types
    from test_captions import _PKG
    tx = types.ModuleType(_PKG + ".transcripts")
    tx.load_for_media = lambda path, model=None, language=None, fallback_dir="": next(
        ({"words": w} for name, w in words_by_file.items() if path.replace("\\", "/").endswith(name)), None)
    tx.words = lambda t: t["words"]
    sys.modules[_PKG + ".transcripts"] = tx


class _Part:
    """A strip's transform or crop: records its keys on the strip."""

    def __init__(self, strip, prefix, **values):
        self.__dict__.update(values)
        self._strip, self._prefix = strip, prefix

    def keyframe_insert(self, prop, frame):
        self._strip.keys.append((self._prefix + "." + prop, frame, getattr(self, prop)))


class _RichStrips(_Strips):
    """Text strips with the properties the presets key (Blender's text strip has them all)."""

    def new_effect(self, name, type_, channel, frame_start, length=1):
        s = super().new_effect(name, type_, channel, frame_start, length)
        s.blend_alpha, s.anchor_y, s.anchor_x, s.wrap_width = 1.0, "BOTTOM", "CENTER", 0.8
        s.transform = _Part(s, "transform", offset_x=0.0, offset_y=0.0, scale_x=1.0, scale_y=1.0, rotation=0.0,
                            origin=(0.5, 0.5))
        s.crop = _Part(s, "crop", max_x=0, min_x=0, max_y=0, min_y=0)
        return s


WORDS = []
for _i, (_t, _line) in enumerate(((1.0, "Alpha one opens the made up part."), (5.0, "Bravo two keeps it going here."),
                                  (9.0, "Charlie three ends the bit."))):
    for _j, _w in enumerate(_line.split()):
        WORDS.append({"w": _w, "start": _t + _j * 0.4, "end": _t + _j * 0.4 + 0.35})


class _LookCase(_OpsCase):

    def setUp(self):
        super().setUp()
        self.scene.render.fps = 30
        rich = _RichStrips()
        rich.extend(self.scene.sequence_editor.strips)
        self.scene.sequence_editor.strips = rich
        self.made_up = self._transcript(WORDS)

    def _transcript(self, words):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"schema": cap.SCHEMA, "language": "en", "words": words}, fh)
        fh.close()
        self.addCleanup(lambda: Path(fh.name).unlink())
        return fh.name

    def run_captions(self, strips, style="CLEAN", **kw):
        return self.ops.add_captions(self.scene, strips, style, "en", self.made_up, **kw)

    def keyed(self, strip):
        out = {}
        for path, frame, value in strip.keys:
            out.setdefault(path, []).append((frame, value))
        return out


class TestAnimatedStrips(_LookCase):

    def test_every_preset_on_every_style(self):
        clip = _clip(self.scene, right=1 + 12 * 30)
        for style in STYLES:
            for anim in PRESETS:
                with self._measured():
                    r = self.run_captions([clip], style, animation=anim, replace_edited=True)
                self.assertEqual(r["animation"], cap.ANIMATIONS[anim]["label"])
                line = self.lines()[0]
                keyed = self.keyed(line)
                self.assertEqual(line.get("bfacw_caption")["anim"], anim)
                if anim == "NONE":
                    self.assertEqual(keyed, {}, style)
                    continue
                self.assertTrue(keyed, (style, anim))
                self.assertTrue(set(keyed) <= set(cap.ANIMATED_PATHS), keyed)
                for frames in keyed.values():
                    self.assertGreaterEqual(min(f for f, _v in frames), line.left_handle)
                    self.assertLessEqual(max(f for f, _v in frames), line.right_handle)
                # Each property is left where the caption is up (steady), not at a key's value.
                self.assertEqual((line.blend_alpha, line.transform.offset_y, line.transform.scale_x,
                                  line.crop.max_x), (1.0, 0.0, 1.0, 0))
                if style == "WORD_HIGHLIGHT":           # the word strips move with their line
                    for w in self.words():
                        if w.get("bfacw_caption")["line"] == line.name:
                            self.assertEqual(self.keyed(w), keyed, anim)
                if anim == "TYPEWRITER":
                    self.assertTrue(all("\n" not in s.text for s in self.lines()), "one line per caption")

    def test_bold_pop_still_pops_by_default(self):
        clip = _clip(self.scene, right=1 + 12 * 30)
        self.run_captions([clip], "BOLD_POP")
        line = self.lines()[0]
        self.assertEqual([k[0] for k in line.keys], ["font_size", "font_size"])
        self.assertEqual(line.get("bfacw_caption")["anim"], cap.ANIM_STYLE)
        self.run_captions([clip], "BOLD_POP", animation="NONE")
        self.assertEqual(self.lines()[0].keys, [])

    def test_a_preset_is_not_a_hand_edit(self):
        """#552: a re-run replaces captions Coworker animated (they're unedited), with the new preset."""
        clip = _clip(self.scene, right=1 + 12 * 30)
        first = self.run_captions([clip], "CLEAN", animation="SLIDE_UP", position="TOP")
        for s in self.lines():
            self.assertFalse(self.ops._edited(s), s.name)
        again = self.run_captions([clip], "CLEAN", animation="FADE", position="MIDDLE")
        self.assertEqual((again["replaced"], again["kept_edited"]), (first["lines_created"], 0))
        self.assertTrue(all(s.get("bfacw_caption")["anim"] == "FADE" for s in self.lines()))
        self.assertTrue(all(tuple(s.location) == (0.5, 0.5) and s.anchor_y == "CENTER" for s in self.lines()))

    def test_a_caption_moved_by_hand_stays_where_it_was_put(self):
        clip = _clip(self.scene, right=1 + 12 * 30)
        self.run_captions([clip], "CLEAN", animation="POP")
        moved = self.lines()[1]
        moved.location = (0.3, 0.6)
        r = self.run_captions([clip], "CLEAN", animation="FADE", position="TOP")
        self.assertEqual(r["kept_edited"], 1)
        kept = self.scene.sequence_editor.strips.get(moved.name)
        self.assertEqual(tuple(kept.location), (0.3, 0.6))
        self.assertEqual(kept.get("bfacw_caption")["anim"], "POP")

    def test_position_and_margin_on_the_strips(self):
        clip = _clip(self.scene, right=1 + 12 * 30)
        self.run_captions([clip], "BOXED", position="TOP", margin=0.2)
        for s in self.lines():
            self.assertEqual((s.anchor_y, tuple(s.location)), ("TOP", (0.5, 0.8)))
            tag = s.get("bfacw_caption")
            self.assertEqual((tag["position"], tag["margin"]), ("TOP", 0.2))
        self.scene.render.resolution_x, self.scene.render.resolution_y = TALL
        self.run_captions([clip], "BOXED", position="BOTTOM", replace_edited=True)
        self.assertTrue(all(tuple(s.location) == (0.5, 0.24) for s in self.lines()))

    def test_make_vertical_keeps_the_position_and_the_animation(self):
        clip = _clip(self.scene, right=1 + 12 * 30)
        self.run_captions([clip], "CLEAN", animation="FADE", position="TOP")
        names = [s.name for s in self.lines()]
        for s in self.lines():
            s.keys.clear()
        self.scene.render.resolution_x, self.scene.render.resolution_y = TALL
        result = self.ops.relayout_captions(self.scene, WIDE)
        self.assertEqual(result["left"], 0)
        for s in self.lines():
            self.assertEqual((s.anchor_y, tuple(s.location)), ("TOP", (0.5, 0.88)))
            self.assertEqual(s.get("bfacw_caption")["anim"], "FADE")
            self.assertIn("blend_alpha", self.keyed(s), s.name)        # keyed again over its own frames
        self.assertTrue(set(names) <= {s.name for s in self.lines()})

    def test_an_animated_transform_is_not_a_hand_edit_for_make_vertical(self):
        clip = _clip(self.scene, right=1 + 12 * 30)
        self.run_captions([clip], "CLEAN", animation="POP")
        line = self.lines()[0]
        line.transform.scale_x = line.transform.scale_y = 0.6      # shown at the first frame of its pop
        curve = SimpleNamespace(evaluate=lambda f: 1.0)          # in the middle it is up
        real = self.ops._strip_curves
        fake = (lambda scene, name, paths=None: {"transform.scale_x": [curve], "transform.scale_y": [curve]}
                if name == line.name else real(scene, name, paths))
        with mock.patch.object(self.ops, "_strip_curves", fake):
            look = self.ops._look_of(self.scene, line)
        self.assertEqual(look["transform"][2:4], (1.0, 1.0))
        self.assertEqual(cap.hand_edits(look, "CLEAN", *WIDE), [])

    def test_remove_silences_refits_the_animation(self):
        clip = _clip(self.scene, right=1 + 12 * 30)
        self.run_captions([clip], "CLEAN", animation="FADE")
        line = self.lines()[0]
        line.keys.clear()
        line.right_handle -= 10                     # trimmed by the cut
        self.assertEqual(self.ops.refit_captions(self.scene, [line.name, "gone"]), 1)
        alpha = self.keyed(line)["blend_alpha"]
        self.assertEqual(alpha[-1], (line.right_handle, 0.0))      # the fade-out ends at the new end
        tag = dict(line.get("bfacw_caption"))
        tag.pop("anim")
        line["bfacw_caption"] = tag                 # made before #542: left as it is
        line.keys.clear()
        self.assertEqual(self.ops.refit_captions(self.scene, [line.name]), 0)
        self.assertEqual(line.keys, [])

    def test_the_tool(self):
        _clip(self.scene, right=1 + 12 * 30)
        r = self.ops.tool_add_captions(["speech"], "clean", "en", self.made_up, animation="typewriter",
                                       position="top", margin=10)
        self.assertEqual(r["status"], "ok", r)
        self.assertIn("(Clean, Typewriter, top)", r["message"])
        self.assertTrue(all(tuple(s.location) == (0.5, 0.9) for s in self.lines()))
        bad = self.ops.tool_add_captions(["speech"], "clean", "en", self.made_up, animation="wobble")
        self.assertEqual(bad["status"], "error")
        self.assertIn("wobble", bad["message"])


BEN = [dict(w, w=w["w"].replace("one", "eins")) for w in WORDS[:6]] + [
    {"w": w, "start": 7.0 + i * 0.4, "end": 7.35 + i * 0.4} for i, w in enumerate("Made up reply from Ben.".split())]


class TestSpeakerStrips(_LookCase):

    def setUp(self):
        super().setUp()
        _speech_files(self, {"ana.wav": WORDS, "ben.wav": BEN})

    def run_captions(self, strips, style="CLEAN", **kw):
        return self.ops.add_captions(self.scene, strips, style, "en", "", **kw)

    def two(self):
        a = _clip(self.scene, "ana.wav", "//media/ana.wav", channel=1, right=1 + 12 * 30)
        b = _clip(self.scene, "ben.wav", "//media/ben.wav", channel=2, right=1 + 12 * 30)
        return a, b

    def test_one_shared_channel_without_speakers_as_before(self):
        a, b = self.two()
        r = self.run_captions([a, b])
        self.assertEqual({s.channel for s in self.lines()}, {r["channel"]})
        self.assertNotIn("speakers", r)
        spans = sorted((s.left_handle, s.right_handle) for s in self.lines())
        self.assertTrue(all(x[1] <= y[0] for x, y in zip(spans, spans[1:])), "no overlap on one channel")

    def test_a_channel_per_speaker_with_labels_and_their_own_look(self):
        a, b = self.two()
        r = self.run_captions([a, b], "CLEAN", speakers=True, speaker_labels=True,
                              speaker_settings=[{"strip": "ben.wav", "name": "Benny", "style": "BOXED",
                                                 "position": "TOP"}])
        self.assertEqual([(s["name"], s["style"], s["position"]) for s in r["speakers"]],
                         [("Ana", "Clean", "BOTTOM"), ("Benny", "Boxed", "TOP")])
        ch = {s["name"]: s["channel"] for s in r["speakers"]}
        self.assertEqual(len(set(ch.values())), 2)
        ed = self.scene.sequence_editor
        self.assertEqual(ed.channels[ch["Ana"]].name, "Captions: Ana")
        self.assertEqual(ed.channels[ch["Benny"]].name, "Captions: Benny")
        ana = [s for s in self.lines() if s.channel == ch["Ana"]]
        ben = [s for s in self.lines() if s.channel == ch["Benny"]]
        self.assertEqual(len(ana) + len(ben), r["lines_created"])
        self.assertTrue(ana and ben)
        self.assertTrue(all(s.text.startswith("Ana: ") for s in ana))
        self.assertTrue(all(s.text.startswith("Benny: ") and s.use_box and s.anchor_y == "TOP" for s in ben))
        self.assertEqual(ben[0].get("bfacw_caption")["speaker"], "Benny")
        self.assertIn("Speakers: Ana on channel", self.ops._summary(r))
        # Both say the same made-up words at the same time: on their own channels they may overlap.
        self.assertEqual(ana[0].left_handle, ben[0].left_handle)
        # Again: each speaker's captions are replaced on their own channel, none edited.
        again = self.run_captions([a, b], "CLEAN", speakers=True, speaker_labels=True,
                                  speaker_settings=[{"strip": "ben.wav", "name": "Benny"}])
        self.assertEqual((again["replaced"], again["kept_edited"]), (r["lines_created"], 0))
        self.assertEqual({s["name"]: s["channel"] for s in again["speakers"]}, ch)

    def test_word_highlight_per_speaker_with_a_label(self):
        a, b = self.two()
        with self._measured():
            r = self.run_captions([a, b], "WORD_HIGHLIGHT", speakers=True, speaker_labels=True)
        chans = sorted(s["channel"] for s in r["speakers"])
        self.assertGreaterEqual(chans[1] - chans[0], 2, "the first speaker's highlight channel is theirs")
        line = next(s for s in self.lines() if s.text.startswith("Ana: "))
        mine = sorted((w for w in self.words() if w.get("bfacw_caption")["line"] == line.name), key=lambda w: w.name)
        self.assertEqual([w.text for w in mine], line.text.split()[1:])     # the label is no word
        px = line.font_size
        lead = len("Ana: ") * px * 0.5
        self.assertAlmostEqual(mine[0].location[0], 0.5 - len(line.text) * px * 0.5 / 2 / 1920 + lead / 1920)

    def test_the_operator_rows(self):
        a, b = self.two()
        rows = self.ops.speaker_rows(self.scene, [a, b])
        self.assertEqual(rows, [("ana.wav", "Ana"), ("ben.wav", "Ben")])
        op, _reports = TestCaptionStrips._operator(self)
        self.ops._fill_speakers(op, self.scene, [a, b])
        op.speaker_list[1].name = "Benny"
        self.ops._fill_speakers(op, self.scene, [a, b])          # the same strips: kept
        self.assertEqual([it.name for it in op.speaker_list], ["Ana", "Benny"])


class TestOperator(_LookCase):

    def setUp(self):
        super().setUp()
        _speech_files(self, {"ana.wav": WORDS, "ben.wav": BEN})

    def test_dialog_and_execute_pass_the_look(self):
        _clip(self.scene, "ana.wav", "//media/ana.wav", channel=1, right=1 + 12 * 30)
        _clip(self.scene, "ben.wav", "//media/ben.wav", channel=2, right=1 + 12 * 30)
        op, reports = TestCaptionStrips._operator(self)
        op.animation, op.position, op.margin, op.speakers, op.speaker_labels = "FADE", "TOP", 15, True, True
        self.assertEqual(op.execute(SimpleNamespace(scene=self.scene)), {'FINISHED'}, reports)
        self.assertEqual([it.name for it in op.speaker_list], ["Ana", "Ben"])
        self.assertTrue(all(s.get("bfacw_caption")["anim"] == "FADE" and tuple(s.location) == (0.5, 0.85)
                            for s in self.lines()))
        self.assertIn("Speakers:", reports[-1][1])
        layout = _Layout()
        op.layout = layout
        op.draw(SimpleNamespace(scene=self.scene, workspace=SimpleNamespace(sequencer_scene=self.scene)))
        drawn = [x[1] for x in layout.log if x[0] == "prop"]
        for name in ("style", "animation", "position", "margin", "speakers", "speaker_labels"):
            self.assertIn(name, drawn)



if __name__ == "__main__":
    unittest.main()
