# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Auto Captions (#376): words -> caption lines, styles and safe areas, frames and
channels, SRT out and in, Translate for many captions, re-running replaces, and the
``add_captions`` tool.  No Blender: ``captions_ops`` runs on a stand-in ``bpy``.

Run with::

    python -m unittest tests.test_captions -v
"""

__all__ = ()

import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
_ADDON = _ROOT / "addon" / "bfa_coworker"
_TOOLS = _ROOT / "mcp" / "blmcp" / "tools"
_SAMPLE = Path(__file__).resolve().parent / "data" / "captions_sample_transcript.json"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cap = _load(_ADDON / "captions.py", "bfacw_captions_for_tests")


def _words(text: str, start: float = 0.0, step: float = 0.3, gaps: dict | None = None) -> list[dict]:
    """Words of *text*, one every *step* seconds; *gaps* = {word index: extra pause before it}."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        t += (gaps or {}).get(i, 0.0)
        out.append({"w": w, "start": round(t, 3), "end": round(t + step, 3)})
        t += step
    return out


def _texts(caps: list[dict]) -> list[str]:
    return [c["text"].replace("\n", " | ") for c in caps]


# -- words -> lines ------------------------------------------------------------------

class TestWordsToLines(unittest.TestCase):

    def test_a_pause_starts_a_new_caption(self):
        caps = cap.build_captions(_words("one two three four", gaps={2: 1.0}), 30, 2)
        self.assertEqual(_texts(caps), ["one two", "three four"])

    def test_a_sentence_end_starts_a_new_caption_but_a_title_does_not(self):
        caps = cap.build_captions(_words("I met Dr. Smith today. He was kind."), 40, 2)
        self.assertEqual(_texts(caps), ["I met Dr. Smith today.", "He was kind."])
        self.assertFalse(cap.is_sentence_end("e.g."))
        self.assertFalse(cap.is_sentence_end("U.S."))
        self.assertTrue(cap.is_sentence_end("done!”"))

    def test_limits_hold_on_a_long_run_of_words(self):
        text = " ".join("word{:d}".format(i) for i in range(60))
        for chars, lines in ((20, 1), (32, 2), (42, 2)):
            caps = cap.build_captions(_words(text, step=0.25), chars, lines, max_duration=4.0)
            for c in caps:
                self.assertLessEqual(len(c["lines"]), lines)
                for line in c["lines"]:
                    self.assertLessEqual(len(line), chars)
                self.assertLessEqual(c["words"][-1]["end"] - c["words"][0]["start"], 4.0 + 1e-9)
            self.assertEqual(" ".join(w["w"] for c in caps for w in c["words"]), text)

    def test_two_lines_are_balanced(self):
        caps = cap.build_captions(_words("Hello and welcome to this short test of captions"), 30, 2)
        self.assertEqual(len(caps), 1)
        top, bottom = caps[0]["lines"]
        self.assertLessEqual(abs(len(top) - len(bottom)), 8)

    def test_one_very_long_word_gets_a_caption_of_its_own(self):
        long = "Pneumonoultramicroscopicsilicovolcanoconiosis"
        caps = cap.build_captions(_words("a {:s} b c".format(long)), 20, 1)
        self.assertIn(long, _texts(caps))
        self.assertTrue(all(len(c["lines"]) == 1 for c in caps))

    def test_empty_and_broken_transcripts(self):
        self.assertEqual(cap.build_captions([]), [])
        self.assertEqual(cap.build_captions(None), [])
        broken = [{"w": "", "start": 0, "end": 1}, {"w": "x"}, {"start": 1}, "junk",
                  {"w": "ok", "start": "nan-ish", "end": 2}, {"w": "fine", "start": 2.0, "end": 2.4}]
        self.assertEqual(_texts(cap.build_captions(broken)), ["fine"])

    def test_overlapping_and_unsorted_times(self):
        words = [{"w": "c", "start": 1.0, "end": 1.6}, {"w": "a", "start": 0.0, "end": 0.9},
                 {"w": "b", "start": 0.5, "end": 1.2}]
        clean = cap.normalise_words(words)
        self.assertEqual([w["w"] for w in clean], ["a", "b", "c"])
        for x, y in zip(clean, clean[1:]):
            self.assertLessEqual(x["end"], y["start"])
        caps = cap.build_captions(words, 30, 2)
        for x, y in zip(caps, caps[1:]):
            self.assertLessEqual(x["end"], y["start"])

    def test_punctuation_tokens_join_their_word(self):
        clean = cap.normalise_words([{"w": "Hello", "start": 0, "end": 0.4}, {"w": ",", "start": 0.4, "end": 0.45},
                                     {"w": "“", "start": 0.5, "end": 0.5}, {"w": "world", "start": 0.5, "end": 0.9},
                                     {"w": "”.", "start": 0.9, "end": 0.95}])
        self.assertEqual([w["w"] for w in clean], ["Hello,", "“world”."])

    def test_numbers_and_titles_stay_with_the_next_word(self):
        self.assertTrue(cap.glued("20", "percent"))
        self.assertTrue(cap.glued("Dr.", "Smith"))
        self.assertFalse(cap.glued("20,", "and"))
        # A split is due; it must not fall between "20" and "percent".
        caps = cap.build_captions(_words("the price went up by about 20 percent this year"), 26, 1)
        for c in caps:
            self.assertFalse(c["words"][-1]["w"] == "20" and c is not caps[-1], _texts(caps))

    def test_no_lone_short_word_when_avoidable(self):
        caps = cap.build_captions(_words("one two three four five six seven."), 20, 1)
        self.assertTrue(all(len(c["words"]) > 1 for c in caps), _texts(caps))

    def test_a_caption_ends_on_a_comma_rather_than_mid_clause(self):
        caps = cap.build_captions(_words("First, the editor removes the long pauses between sentences, so the "
                                         "pace feels tighter."), 36, 2)
        self.assertTrue(any(c["words"][-1]["w"].endswith(",") for c in caps[:-1]), _texts(caps))

    def test_min_duration_but_never_into_the_next(self):
        words = [{"w": "Hi.", "start": 0.0, "end": 0.1}, {"w": "There", "start": 2.0, "end": 2.3},
                 {"w": "now.", "start": 2.3, "end": 2.5}, {"w": "Go.", "start": 2.6, "end": 2.7}]
        caps = cap.build_captions(words, 30, 2, min_duration=0.8)
        self.assertAlmostEqual(caps[0]["end"], 0.8)
        self.assertAlmostEqual(caps[1]["end"], 2.6)       # stretched to the next caption, not past it

    def test_the_sample_transcript(self):
        data = json.loads(_SAMPLE.read_text(encoding="utf-8"))
        words = cap.transcript_words(data)
        for chars, lines in ((36, 2), (20, 1)):
            caps = cap.build_captions(words, chars, lines)
            self.assertEqual([w["w"] for c in caps for w in c["words"]], [w["w"] for w in words])
            self.assertEqual(caps[0]["text"], "Hello, and welcome\nto this short test" if lines == 2
                             else "Hello, and welcome")
            for c in caps:
                self.assertTrue(all(len(x) <= chars for x in c["lines"]), c["text"])
            for x, y in zip(caps, caps[1:]):
                self.assertLessEqual(x["end"], y["start"])

    def test_transcript_words_checks_the_format(self):
        self.assertEqual(cap.transcript_words([{"w": "a"}]), [{"w": "a"}])
        with self.assertRaises(cap.CaptionsError):
            cap.transcript_words({"schema": "something/9", "words": []})
        with self.assertRaises(cap.CaptionsError):
            cap.transcript_words({"schema": cap.SCHEMA})
        with self.assertRaises(cap.CaptionsError):
            cap.transcript_words("nope")

    def test_layout_lines(self):
        self.assertEqual(cap.layout_lines(["a", "b"], 10, 1), ["a b"])
        self.assertIsNone(cap.layout_lines(["aaaa", "bbbb", "cccc"], 9, 1))
        self.assertEqual(cap.layout_lines(["aaaa", "bbbb", "cccc"], 9, 2), ["aaaa", "bbbb cccc"])
        self.assertEqual(cap.layout_lines(["aaaa", "bbbb", "cccc", "dddd"], 9, 1, force=True),
                         ["aaaa bbbb", "cccc dddd"])
        self.assertEqual(cap.layout_lines([], 9, 1, force=True), [])

    def test_even_words(self):
        words = cap.even_words("aa b", 1.0, 2.0)
        self.assertEqual([w["w"] for w in words], ["aa", "b"])
        self.assertAlmostEqual(words[0]["start"], 1.0)
        self.assertAlmostEqual(words[-1]["end"], 2.0)
        self.assertGreater(words[0]["end"] - words[0]["start"], words[1]["end"] - words[1]["start"])
        self.assertEqual(cap.even_words("", 0, 1), [])


# -- styles --------------------------------------------------------------------------

class TestStyles(unittest.TestCase):

    def test_names(self):
        self.assertEqual(cap.style_key("Bold Pop"), "BOLD_POP")
        self.assertEqual(cap.style_key("word-by-word"), "WORD_HIGHLIGHT")
        self.assertEqual(cap.style_key("boxed"), "BOXED")
        self.assertEqual(cap.style_key(None), "CLEAN")
        self.assertIsNone(cap.style_key("comic sans"))
        with self.assertRaises(cap.CaptionsError):
            cap.resolve_style("comic sans", 1920, 1080)

    def test_every_style_fits_the_safe_area_wide_and_vertical(self):
        for res in ((1920, 1080), (1080, 1920), (1280, 720), (720, 1280)):
            kind = cap.frame_kind(*res)
            safe = cap.safe_area(kind)
            for key in cap.STYLES:
                st = cap.resolve_style(key, *res)
                props = st["props"]
                self.assertEqual(st["kind"], kind)
                self.assertEqual((props["anchor_x"], props["anchor_y"]), ("CENTER", "BOTTOM"))
                x, y = props["location"]
                self.assertEqual(x, 0.5)
                self.assertGreaterEqual(y, 0.05)                    # above the title-safe edge
                self.assertLessEqual(props["wrap_width"], 0.8)
                chars = cap.default_max_chars(key, *res)
                lines = st["max_lines"]
                width, height = cap.text_box(["M" * 0 + "a" * chars] * lines, props["font_size"])
                self.assertLessEqual(width, safe["width"] * res[0] + 1, (key, res))
                self.assertLessEqual(y + height / res[1], safe["top"] + 0.02, (key, res))   # bottom third
                if kind == "VERTICAL":
                    self.assertGreaterEqual(y, 0.22, "above the platform buttons and text")
                self.assertEqual(st["font_file"], "Inter.woff2")

    def test_font_size_follows_the_output(self):
        small = cap.resolve_style("CLEAN", 1280, 720)["props"]["font_size"]
        big = cap.resolve_style("CLEAN", 3840, 2160)["props"]["font_size"]
        self.assertAlmostEqual(big / small, 3.0, delta=0.1)

    def test_what_each_style_is(self):
        self.assertTrue(cap.resolve_style("BOXED", 1920, 1080)["props"]["use_box"])
        self.assertIn("box_color", cap.resolve_style("BOXED", 1920, 1080)["props"])
        pop = cap.resolve_style("BOLD_POP", 1920, 1080)
        self.assertTrue(pop["uppercase"] and pop["pop"] and pop["props"]["use_bold"])
        word = cap.resolve_style("WORD_HIGHLIGHT", 1080, 1920)
        self.assertEqual(word["max_lines"], 1)
        self.assertEqual(len(word["highlight"]), 4)


# -- frames and channels ------------------------------------------------------------

class TestFrames(unittest.TestCase):

    def test_media_seconds_to_timeline_frames(self):
        caps = [{"text": "a", "start": 1.0, "end": 2.0, "words": [{"w": "a", "start": 1.0, "end": 2.0}]},
                {"text": "b", "start": 2.0, "end": 3.5, "words": []}]
        framed = cap.to_frames(caps, 24.0, content_start=101)
        self.assertEqual([(c["frame_start"], c["frame_end"]) for c in framed], [(125, 149), (149, 185)])
        self.assertEqual(framed[0]["words"][0]["frame"], 125)

    def test_clipped_to_what_the_strip_shows(self):
        caps = [{"text": str(i), "start": float(i), "end": i + 1.0} for i in range(5)]
        framed = cap.to_frames(caps, 10.0, content_start=0, left=15, right=35)
        self.assertEqual([(c["frame_start"], c["frame_end"]) for c in framed], [(15, 20), (20, 30), (30, 35)])

    def test_never_overlapping_and_at_least_one_frame(self):
        caps = [{"text": "a", "start": 0.0, "end": 0.01}, {"text": "b", "start": 0.01, "end": 0.02},
                {"text": "c", "start": 0.02, "end": 1.0}]
        framed = cap.to_frames(caps, 24.0)
        for x in framed:
            self.assertGreaterEqual(x["frame_end"] - x["frame_start"], 1)
        for x, y in zip(framed, framed[1:]):
            self.assertLessEqual(x["frame_end"], y["frame_start"])

    def test_highlight_windows_cover_the_caption(self):
        c = {"frame_start": 10, "frame_end": 40,
             "words": [{"w": "a", "frame": 11}, {"w": "b", "frame": 20}, {"w": "c", "frame": 20}, {"w": "d", "frame": 33}]}
        windows = cap.highlight_windows(c)
        self.assertEqual(windows, [(0, 10, 20), (2, 20, 33), (3, 33, 40)])     # "b" has no frames of its own

    def test_highlight_left(self):
        # A 500 px line centred in 1920 px, the word after a 280 px prefix.
        self.assertAlmostEqual(cap.highlight_left(0.5, 500, 280, 1920), 0.5 - 250 / 1920 + 280 / 1920)
        self.assertAlmostEqual(cap.highlight_left(0.5, 500, 0, 1920), 0.5 - 250 / 1920)

    def test_pick_channel(self):
        occupied = {1: [(1, 100)], 2: [(1, 100)], 3: [(200, 300)], 4: [(50, 60)]}
        self.assertEqual(cap.pick_channel(occupied, {}, 2, (10, 90)), 3)       # free in that range
        self.assertEqual(cap.pick_channel(occupied, {}, 2, (10, 90), need=2), 5)
        self.assertEqual(cap.pick_channel(occupied, {7: "Captions"}, 2, (10, 90)), 7)   # the named one first
        self.assertEqual(cap.pick_channel(occupied, {1: "Captions"}, 2, (10, 90)), 3)   # not below the clip
        full = {c: [(0, 10)] for c in range(1, 129)}
        self.assertIsNone(cap.pick_channel(full, {}, 0, (0, 5)))
        self.assertIsNone(cap.pick_channel({}, {}, 128, (0, 5)))

    def test_owned_by(self):
        self.assertTrue(cap.owned_by({"source": "media:a"}, ["media:a"]))
        self.assertFalse(cap.owned_by({"source": "media:b"}, ["media:a"]))
        self.assertFalse(cap.owned_by(None, ["media:a"]))


# -- SRT ------------------------------------------------------------------------------

class TestSrt(unittest.TestCase):

    def test_time_format(self):
        self.assertEqual(cap.srt_time(0), "00:00:00,000")
        self.assertEqual(cap.srt_time(3723.4567), "01:02:03,457")
        self.assertEqual(cap.srt_time(-1), "00:00:00,000")

    def test_round_trip(self):
        cues = [(1.44, 3.92, "Hello, and welcome\nto this short test"), (3.92, 7.18, "of automatic captions"),
                (3600.5, 3602.25, "an hour in")]
        text = cap.format_srt(cues)
        self.assertTrue(text.startswith("1\n00:00:01,440 --> 00:00:03,920\nHello, and welcome\nto this short test\n\n2\n"))
        back, skipped = cap.parse_srt(text)
        self.assertEqual(skipped, 0)
        self.assertEqual([(c["start"], c["end"], c["text"]) for c in back],
                         [(round(a, 3), round(b, 3), t) for a, b, t in cues])

    def test_empty_cues_are_not_written(self):
        self.assertEqual(cap.format_srt([(0, 1, "  "), (1, 2, "x")]), "1\n00:00:01,000 --> 00:00:02,000\nx\n")

    def test_tolerant_reading(self):
        text = ("﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i> <b>there</b>\r\nsecond line\r\n\r\n"
                "2\r\n00:00:03.000 --> 00:00:04.000 X1:0 X2:10\r\n{\\an8}Top &amp; centre\r\n"
                "3\r\n00:00:05,000 --> 00:00:06,000\r\nno blank line before me\r\n\r\n"
                "4\r\n00:00:07,000 --> broken\r\nskip me\r\n\r\n"
                "5\r\n00:00:09,000 --> 00:00:08,000\r\nends before it starts\r\n\r\n"
                "6\r\n00:00:10,000 --> 00:00:11,000\r\n\r\n"
                "just some junk\r\n\r\n"
                "7\r\n0:0:12,5 --> 0:0:13,25\r\n<font color=\"#ff0\">short times</font>\r\n")
        cues, skipped = cap.parse_srt(text)
        self.assertEqual([c["text"] for c in cues], ["Hello there\nsecond line", "Top & centre",
                                                     "no blank line before me", "short times"])
        self.assertEqual([c["start"] for c in cues], [1.0, 3.0, 5.0, 12.5])
        self.assertEqual(cues[-1]["end"], 13.25)
        self.assertEqual(skipped, 4)        # broken timing, backwards, no text, junk
        self.assertEqual(cap.parse_srt(""), ([], 0))

    def test_old_mac_line_ends(self):
        cues, skipped = cap.parse_srt("1\r00:00:01,000 --> 00:00:02,000\rHi\r\r")
        self.assertEqual((len(cues), skipped, cues[0]["text"]), (1, 0, "Hi"))

    def test_the_spike_sample_reads(self):
        sample = ("1\n00:00:00,000 --> 00:00:01,000\ncaption one\n\n2\n00:00:01,000 --> 00:00:02,000\ncaption two\n\n")
        self.assertEqual(len(cap.parse_srt(sample)[0]), 2)


# -- Translate -----------------------------------------------------------------------

class TestBatchTranslate(unittest.TestCase):

    def test_numbered_text_and_reply(self):
        self.assertEqual(cap.batch_text(["Hello, and\nwelcome", "bye"]), "[1] Hello, and welcome\n[2] bye")
        reply = "[1] Hola, y bienvenidos\n2. adiós\n3) extra\n[9] out of range\nnoise"
        self.assertEqual(cap.parse_batch_reply(reply, 3), {1: "Hola, y bienvenidos", 2: "adiós", 3: "extra"})
        self.assertEqual(cap.parse_batch_reply("[1]\n", 1), {})


# -- the tool's arguments --------------------------------------------------------------

class TestToolParams(unittest.TestCase):

    def test_checked(self):
        self.assertEqual(cap.check_tool_params(None, "bold pop", "auto"), ([], "BOLD_POP", None))
        self.assertEqual(cap.check_tool_params("Speech", "clean", "ES"), (["Speech"], "CLEAN", "es"))
        self.assertEqual(cap.check_tool_params(["a", "", "b"], "WORD_HIGHLIGHT", "zh-Hans"),
                         (["a", "b"], "WORD_HIGHLIGHT", "zh_hans"))
        with self.assertRaises(cap.CaptionsError):
            cap.check_tool_params([], "neon", None)
        with self.assertRaises(cap.CaptionsError):
            cap.check_tool_params([], "clean", "English please")
        with self.assertRaises(cap.CaptionsError):
            cap.check_tool_params(5, "clean", None)


# -- captions_ops on a stand-in bpy ------------------------------------------------------

class _Props(dict):
    def to_dict(self):
        return dict(self)


class _Strip:
    def __init__(self, name, type_, channel, left, right, **kw):
        self.name, self.type, self.channel = name, type_, channel
        self.left_handle, self.right_handle, self.content_start = left, right, float(left)
        self.select, self.text, self.font, self.font_size = False, "", None, 60
        self.location, self.color, self.keys, self._props = (0.5, 0.5), (1, 1, 1, 1), [], {}
        self.__dict__.update(kw)

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __setitem__(self, key, value):
        self._props[key] = _Props(value) if isinstance(value, dict) else value

    def __getitem__(self, key):
        return self._props[key]

    def keyframe_insert(self, path, frame):
        self.keys.append((path, frame, getattr(self, path)))


class _Strips(list):
    def new_effect(self, name, type_, channel, frame_start, length=1):
        while self.get(name) is not None:
            name += ".001"
        s = _Strip(name, type_, channel, frame_start, frame_start + length)
        self.append(s)
        return s

    def get(self, name, default=None):
        return next((s for s in self if s.name == name), default)

    def remove(self, strip):
        for i, s in enumerate(self):
            if s is strip:
                del self[i]
                return
        raise ValueError(strip)


class _Ed:
    def __init__(self):
        self.strips = _Strips()
        self.channels = [SimpleNamespace(name="Channel {:d}".format(i)) for i in range(129)]
        self.active_strip = None

    @property
    def strips_all(self):
        return self.strips


class _Scene:
    def __init__(self, res=(1920, 1080)):
        self.name = "Scene"
        self.render = SimpleNamespace(resolution_x=res[0], resolution_y=res[1], fps=24, fps_base=1.0)
        self.frame_start = 1
        self.sequence_editor = _Ed()
        self.animation_data = None

    def sequence_editor_create(self):
        self.sequence_editor = self.sequence_editor or _Ed()
        return self.sequence_editor


def _fake_bpy(state):
    bpy = types.ModuleType("bpy")
    bpy.path = SimpleNamespace(abspath=lambda p, library=None: p)
    bpy.utils = SimpleNamespace(system_resource=lambda *_a, **_k: "", escape_identifier=lambda s: s,
                                register_class=lambda c: None, unregister_class=lambda c: None)
    bpy.data = SimpleNamespace(scenes=state["scenes"])
    bpy.context = SimpleNamespace(scene=None, sequencer_scene=None, workspace=None)
    bpy.ops = SimpleNamespace(ed=SimpleNamespace(undo_push=lambda message="": state["undo"].append(message)))
    props = types.ModuleType("bpy.props")
    for n in ("BoolProperty", "EnumProperty", "IntProperty", "StringProperty"):
        setattr(props, n, lambda **_k: None)
    bpy.props = props
    bpy.types = SimpleNamespace(Operator=type("Operator", (), {}), Menu=type("Menu", (), {}))
    extras = types.ModuleType("bpy_extras")
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ExportHelper = io_utils.ImportHelper = type("Helper", (), {})
    extras.io_utils = io_utils
    return {"bpy": bpy, "bpy.props": props, "bpy.types": bpy.types, "bpy_extras": extras,
            "bpy_extras.io_utils": io_utils}


_PKG = "bfacw_captions_pkg"
_STATE = {"scenes": {}, "undo": []}


def _ops():
    if _PKG + ".captions_ops" in sys.modules:
        return sys.modules[_PKG + ".captions_ops"]
    fakes = _fake_bpy(_STATE)
    saved = {k: sys.modules.get(k) for k in fakes}
    sys.modules.update(fakes)
    try:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
        prefs = types.ModuleType(_PKG + ".preferences")
        prefs._LANGUAGES = (("en", "English"), ("es", "Spanish"))
        sys.modules[_PKG + ".preferences"] = prefs
        return importlib.import_module(_PKG + ".captions_ops")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _clip(scene, name="speech", path="//media/talk.wav", channel=1, left=1, right=3001, type_="SOUND"):
    s = _Strip(name, type_, channel, left, right, select=True)
    if type_ == "SOUND":
        s.sound = SimpleNamespace(filepath=path, library=None)
    else:
        s.filepath = path
    scene.sequence_editor.strips.append(s)
    return s


class _OpsCase(unittest.TestCase):

    def setUp(self):
        self.ops = _ops()
        self.scene = _Scene()
        _STATE["scenes"].clear()
        _STATE["scenes"]["Scene"] = self.scene
        _STATE["undo"].clear()
        self.ops.bpy.context.scene = self.scene
        self.transcript = str(_SAMPLE)
        # Local transcription (#375) is not in these tests: as if this build had none.
        self._saved_tx = sys.modules.get(_PKG + ".transcripts", "unset")
        sys.modules[_PKG + ".transcripts"] = None

    def tearDown(self):
        if self._saved_tx == "unset":
            sys.modules.pop(_PKG + ".transcripts", None)
        else:
            sys.modules[_PKG + ".transcripts"] = self._saved_tx

    def _measured(self):
        """The bundled font, measured at half the size per character (blf stands in)."""
        stack = mock.patch.multiple(self.ops, _font=lambda: SimpleNamespace(filepath="Inter.woff2"),
                                    _measure=lambda font: (lambda text, px: len(text) * px * 0.5))
        return stack

    def lines(self):
        return [s for s in self.scene.sequence_editor.strips if (s.get("bfacw_caption") or {}).get("role") == "line"]

    def words(self):
        return [s for s in self.scene.sequence_editor.strips if (s.get("bfacw_caption") or {}).get("role") == "word"]


class TestCaptionStrips(_OpsCase):

    def test_captions_from_a_transcript_file(self):
        clip = _clip(self.scene)
        movie = _clip(self.scene, "movie", "//media/talk.wav", channel=2, type_="MOVIE")     # its picture
        result = self.ops.add_captions(self.scene, [clip, movie], "CLEAN", None, self.transcript)
        lines = self.lines()
        self.assertEqual(result["lines_created"], len(lines))
        self.assertEqual(result["sources"], ["speech"])                 # the movie and its sound: once
        self.assertEqual(result["channel"], 3)                          # first free above the clips
        self.assertEqual(self.scene.sequence_editor.channels[3].name, "Captions")
        self.assertEqual(lines[0].name, "cap_0001")
        self.assertEqual(lines[0].text, "Hello, and welcome to this short test")     # 42 a line here
        self.assertEqual(lines[0].left_handle, round(1 + 1.44 * 24))      # content_start + t x fps
        self.assertTrue(all(s.channel == 3 for s in lines))
        self.assertEqual(result["first_frame"], lines[0].left_handle)
        self.assertEqual(result["last_frame"], lines[-1].right_handle - 1)
        tag = lines[0].get("bfacw_caption")
        self.assertEqual((tag["style"], tag["role"]), ("CLEAN", "line"))
        self.assertTrue(tag["source"].startswith("media:"))
        for x, y in zip(lines, lines[1:]):
            self.assertLessEqual(x.right_handle, y.left_handle)

    def test_running_again_replaces_instead_of_stacking(self):
        clip = _clip(self.scene)
        first = self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript)
        names = [s.name for s in self.lines()]
        again = self.ops.add_captions(self.scene, [clip], "BOXED", None, self.transcript)
        self.assertEqual(again["replaced"], first["lines_created"])
        self.assertEqual([s.name for s in self.lines()], names)        # same names: keyframe paths hold
        self.assertEqual(again["channel"], first["channel"])
        self.assertTrue(all(s.get("bfacw_caption")["style"] == "BOXED" for s in self.lines()))
        self.assertTrue(all(s.use_box for s in self.lines()))

    def test_another_source_keeps_its_captions(self):
        a = _clip(self.scene, "a", "//a.wav", left=1, right=200)
        b = _clip(self.scene, "b", "//b.wav", left=1, right=3001, channel=2)
        self.ops.add_captions(self.scene, [a], "CLEAN", None, self.transcript)
        n_a = len(self.lines())
        self.ops.add_captions(self.scene, [b], "CLEAN", None, self.transcript)
        sources = {s.get("bfacw_caption")["source"] for s in self.lines()}
        self.assertEqual(len(sources), 2)
        self.assertGreater(len(self.lines()), n_a)
        self.assertEqual(len({s.name for s in self.lines()}), len(self.lines()))     # no name clash

    def test_only_what_the_strip_shows_is_captioned(self):
        clip = _clip(self.scene, left=1, right=1 + 10 * 24)            # the first 10 s
        self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript)
        self.assertTrue(all(s.right_handle <= clip.right_handle for s in self.lines()))
        self.assertEqual(len(self.lines()), 3)

    def test_no_transcription_in_this_build(self):
        clip = _clip(self.scene)
        self.assertFalse(self.ops.transcription_available())
        with self.assertRaises(self.ops.CaptionsError) as ctx:
            self.ops.add_captions(self.scene, [clip], "CLEAN", None, "")
        self.assertIn("transcription", str(ctx.exception))
        self.assertIn("Import SRT", str(ctx.exception))
        self.assertEqual(self.lines(), [])

    def test_transcription_when_the_build_has_it(self):
        data = json.loads(_SAMPLE.read_text(encoding="utf-8"))
        calls = []
        tx = types.ModuleType(_PKG + ".transcripts")
        tx.SCHEMA = "bfa_coworker.transcript/1"
        tx.load_for_media = lambda path, model=None, language=None: calls.append((path, language)) or data
        tx.words = lambda t: t["words"]
        sys.modules[_PKG + ".transcripts"] = tx
        clip = _clip(self.scene)
        result = self.ops.add_captions(self.scene, [clip], "CLEAN", "en", "")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].replace("\\", "/").endswith("media/talk.wav"))
        self.assertEqual(calls[0][1], "en")
        self.assertEqual(result["language"], "en")
        self.assertGreater(result["lines_created"], 10)

    def test_transcription_built_in_but_not_run_yet(self):
        tx = types.ModuleType(_PKG + ".transcripts")
        tx.load_for_media = lambda path, model=None, language=None, blend_path=None, fallback_dir="": None
        tx.words = lambda t: t["words"]
        sys.modules[_PKG + ".transcripts"] = tx
        with self.assertRaises(self.ops.CaptionsError) as ctx:
            self.ops.add_captions(self.scene, [_clip(self.scene)], "CLEAN", None, "")
        self.assertIn("hasn't been transcribed yet", str(ctx.exception))
        self.assertIn("transcribe_strip", str(ctx.exception))

    def test_word_by_word_highlight(self):
        with self._measured():
            clip = _clip(self.scene)
            result = self.ops.add_captions(self.scene, [clip], "WORD_HIGHLIGHT", None, self.transcript)
        lines, words = self.lines(), self.words()
        self.assertTrue(all("\n" not in s.text for s in lines))         # one line each
        self.assertEqual(result["highlight_strips"], len(words))
        self.assertGreater(len(words), len(lines))
        self.assertTrue(all(w.channel == result["channel"] + 1 for w in words))
        self.assertEqual(self.scene.sequence_editor.channels[result["channel"] + 1].name, "Captions Highlight")
        first = [w for w in words if w.get("bfacw_caption")["line"] == "cap_0001"]
        self.assertEqual([w.text for w in first], lines[0].text.split())
        self.assertEqual(first[0].left_handle, lines[0].left_handle)
        self.assertEqual(first[-1].right_handle, lines[0].right_handle)
        self.assertEqual(first[0].anchor_x, "LEFT")
        # The first word starts where the centred line does; later words further right.
        px = lines[0].font_size
        self.assertAlmostEqual(first[0].location[0], 0.5 - len(lines[0].text) * px * 0.5 / 2 / 1920)
        self.assertGreater(first[1].location[0], first[0].location[0])
        # Re-running in another style takes the word strips away too.
        self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript)
        self.assertEqual(self.words(), [])

    def test_replacing_tags_the_animation(self):
        """A new cap_0001 must not pick up the old one's Bold Pop size from the evaluated action."""
        tagged = []
        fcurves = _Strips()
        fcurves.append(SimpleNamespace(data_path='sequence_editor.strips_all["cap_0001"].font_size'))
        fcurves.append(SimpleNamespace(data_path='sequence_editor.strips_all["mine"].font_size'))
        bag = SimpleNamespace(fcurves=fcurves)
        action = SimpleNamespace(layers=[SimpleNamespace(strips=[SimpleNamespace(channelbags=[bag])])],
                                 update_tag=lambda: tagged.append(True))
        self.scene.animation_data = SimpleNamespace(action=action)
        clip = _clip(self.scene)
        self.ops.add_captions(self.scene, [clip], "BOLD_POP", None, self.transcript)
        self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript)
        self.assertTrue(tagged)
        self.assertEqual([fc.data_path for fc in fcurves], ['sequence_editor.strips_all["mine"].font_size'])

    def test_bold_pop_pops_and_is_capitals(self):
        clip = _clip(self.scene)
        self.ops.add_captions(self.scene, [clip], "BOLD_POP", None, self.transcript)
        line = self.lines()[0]
        self.assertEqual(line.text, line.text.upper())
        self.assertEqual([k[0] for k in line.keys], ["font_size", "font_size"])
        self.assertLess(line.keys[0][2], line.keys[1][2])
        self.assertEqual(line.keys[1][1] - line.keys[0][1], 3)

    def test_no_room_changes_nothing(self):
        clip = _clip(self.scene, channel=128)
        with self.assertRaises(self.ops.CaptionsError):
            self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript)
        self.assertEqual(self.lines(), [])


class TestSrtStrips(_OpsCase):

    def write(self, name, text):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix="bfacw_srt_"))
        path = folder / name
        path.write_text(text, encoding="utf-8")
        self.addCleanup(lambda: [p.unlink() for p in folder.iterdir()] and folder.rmdir())
        return str(path)

    def test_export_reads_back_with_the_same_times(self):
        clip = _clip(self.scene)
        ch = self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript)["channel"]
        hand = self.scene.sequence_editor.strips.new_effect("my_title", "TEXT", ch, 3050, length=24)
        hand.text = "Typed by hand"                                     # on the Captions channel, untagged
        cues = self.ops.caption_cues(self.scene)
        self.assertEqual(cues[-1][2], "Typed by hand")
        back, skipped = cap.parse_srt(cap.format_srt(cues))
        self.assertEqual(skipped, 0)
        self.assertEqual(len(back), len(cues))
        for (a, b, t), c in zip(cues, back):
            self.assertAlmostEqual(c["start"], a, places=3)
            self.assertAlmostEqual(c["end"], b, places=3)
            self.assertEqual(c["text"], t)
        self.assertAlmostEqual(cues[0][0], round(1.44 * 24) / 24, places=6)

    def test_import_makes_styled_captions_at_the_cue_times(self):
        path = self.write("talk.srt", "1\n00:00:01,000 --> 00:00:02,500\nHello <i>there</i>\nfriend\n\n"
                                      "2\n00:00:03,000 --> 00:00:04,000\nBye\n\nbroken\n")
        result = self.ops.import_srt(self.scene, path, "BOXED")
        lines = self.lines()
        self.assertEqual((result["cues"], result["skipped"], len(lines)), (2, 1, 2))
        self.assertEqual(lines[0].text, "Hello there\nfriend")
        self.assertEqual((lines[0].left_handle, lines[0].right_handle), (1 + 24, 1 + 60))
        self.assertTrue(lines[0].use_box)
        self.assertEqual(result["channel"], 1)                           # an empty timeline
        again = self.ops.import_srt(self.scene, path, "CLEAN")
        self.assertEqual((again["replaced"], len(self.lines())), (2, 2))  # re-import replaces

    def test_import_word_by_word_splits_cues_into_lines(self):
        with self._measured():
            path = self.write("w.srt", "1\n00:00:00,000 --> 00:00:04,000\nthis cue is much too long for a "
                                       "single line of word by word captions\n")
            self.ops.import_srt(self.scene, path, "WORD_HIGHLIGHT")
        lines = self.lines()
        self.assertGreater(len(lines), 1)
        self.assertEqual(lines[0].left_handle, 1)
        self.assertEqual(lines[-1].right_handle, 1 + 96)
        self.assertTrue(self.words())

    def test_import_errors(self):
        with self.assertRaises(self.ops.CaptionsError):
            self.ops.import_srt(self.scene, self.write("bad.srt", "nothing here\n"), "CLEAN")
        with self.assertRaises(self.ops.CaptionsError):
            self.ops.import_srt(self.scene, str(Path(self.write("x.srt", "")).with_name("missing.srt")), "CLEAN")


class TestTranslateCaptions(_OpsCase):

    def test_a_stub_translator_through_the_batch_path(self):
        clip = _clip(self.scene)
        self.ops.add_captions(self.scene, [clip], "CLEAN", None, self.transcript)
        lines = self.lines()
        before = [(s.name, s.channel, s.left_handle, s.right_handle, dict(s.get("bfacw_caption")), s.font_size)
                  for s in lines]
        path, current, what = self.ops.translate_request(self.scene, lines)
        self.assertTrue(path.startswith(cap.TRANSLATE_PREFIX))
        self.assertIn("{:d} captions".format(len(lines)), what)
        # The stub "translator": every caption in capitals with a marker, the last one left out.
        sent = current.split("\n")
        reply = "\n".join("[{:d}] ES {:s}".format(i + 1, line.split("] ", 1)[1].upper()) for i, line in enumerate(sent[:-1]))
        written, missing = self.ops.apply_translation(path, reply)
        self.assertEqual((written, missing), (len(lines) - 1, 1))
        after = self.lines()
        self.assertEqual([(s.name, s.channel, s.left_handle, s.right_handle, dict(s.get("bfacw_caption")), s.font_size)
                          for s in after], before)                       # same strips, timing, style, tag
        self.assertTrue(after[0].text.startswith("ES HELLO, AND"))
        self.assertTrue(all(len(x) <= 42 for x in after[0].text.split("\n")))
        self.assertFalse(after[-1].text.startswith("ES "))
        self.assertEqual(_STATE["undo"], ["Translate Captions"])

    def test_a_translated_word_by_word_line_gets_new_highlights(self):
        with self._measured():
            clip = _clip(self.scene)
            self.ops.add_captions(self.scene, [clip], "WORD_HIGHLIGHT", None, self.transcript)
            line = self.lines()[0]
            line.font = SimpleNamespace(filepath="Inter.woff2")
            self.ops.set_caption_text(self.scene, line, "Hola y bienvenidos")
        mine = [w for w in self.words() if w.get("bfacw_caption")["line"] == line.name]
        self.assertEqual([w.text for w in mine], ["Hola", "y", "bienvenidos"])
        self.assertEqual((mine[0].left_handle, mine[-1].right_handle), (line.left_handle, line.right_handle))


class TestTool(_OpsCase):

    def test_errors_are_plain(self):
        r = self.ops.tool_add_captions([], "CLEAN", None, self.transcript)
        self.assertEqual(r["status"], "error")
        self.assertIn("select them", r["message"])
        _clip(self.scene)
        self.assertIn("No strip named 'nope'", self.ops.tool_add_captions(["nope"], "CLEAN", None, None)["message"])
        self.scene.sequence_editor.strips.new_effect("title", "TEXT", 5, 1, length=10)
        self.assertIn("Only sound and movie", self.ops.tool_add_captions(["title"], "CLEAN", None, None)["message"])
        self.assertIn("Unknown style", self.ops.tool_add_captions(["speech"], "neon", None, None)["message"])
        self.assertIn("transcription", self.ops.tool_add_captions(["speech"], "clean", None, None)["message"])

    def test_typed_result(self):
        _clip(self.scene)
        r = self.ops.tool_add_captions(["speech"], "bold pop", "auto", self.transcript)
        self.assertEqual(r["status"], "ok")
        for key in ("lines_created", "channel", "first_frame", "last_frame", "style", "sources", "message"):
            self.assertIn(key, r)
        self.assertEqual(r["style"], "Bold Pop")
        self.assertEqual(_STATE["undo"], ["Add Captions"])
        self.assertIn("caption lines on channel", r["message"])

    def test_the_toolcode(self):
        toolcode = _load(_TOOLS / "add_captions_toolcode.py", "bfacw_add_captions_toolcode")
        out = toolcode.main(toolcode.Params(strips=("x",)))
        self.assertEqual(out.status, "error")                      # the add-on isn't loaded under its name
        fake = types.ModuleType("bl_ext.user_default.bfa_coworker.captions_ops")
        fake.tool_add_captions = lambda strips, style, language, path: {
            "status": "ok", "message": "m", "lines_created": 3, "channel": 4, "first_frame": 1, "last_frame": 9,
            "style": "Clean", "extra_key": "dropped"}
        sys.modules[fake.__name__] = fake
        try:
            out = toolcode.main(toolcode.Params(strips=("speech",), style="CLEAN"))
        finally:
            del sys.modules[fake.__name__]
        self.assertEqual((out.status, out.lines_created, out.channel), ("ok", 3, 4))
        self.assertEqual(set(out._asdict()), set(toolcode.Result._fields))

    def test_in_the_vse_domain(self):
        src = (_ROOT / "mcp" / "blmcp" / "tool_domains.py").read_text(encoding="utf-8")
        self.assertIn('"add_captions"', src)


if __name__ == "__main__":
    unittest.main()
