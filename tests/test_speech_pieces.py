# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Long audio in pieces that can't loop (#540), bpy-free, on generated sound and made-up words.

1. The loudness envelope and the piece plan: pieces cover the sound back to back, at most
   one whisper window long, cut in pauses (never mid-word); without a pause, at the quietest.
2. The no-speech gate: digital silence, a steady tone and a steady chord give no piece to
   send; speech does, also a quiet speaker (-20 dB).
3. The repeat check: a short sequence back to back many times, or too many words.
4. Time shifting: a piece's answer back in file time, the pieces joined.
5. Word ends: the cap per word, trimmed to the sound's end, words in silence dropped.
6. ``speech.transcribe_pieces`` against a stand-in server: one request per piece with the
   piece fields, the retry of a looping piece, a piece kept empty, the language kept,
   progress n of m, a Stop between pieces, no piece WAV left.
7. The job: progress in get_job_status and the job row; nothing cached after a Stop; a
   finished transcript cached once per file, plus its copy in the add-on's cache folder
   (#541: found after the .blend moves); an old whole-file transcript is not used.

Run with::

    python -m unittest tests.test_speech_pieces -v
"""

__all__ = ()

import importlib
import math
import os
import sys
import tempfile
import threading
import types
import unittest
import wave
from array import array
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_PKG = "bfacw_pieces_pkg"


def _load(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


sp = _load("speech_pieces")
tr = _load("transcripts")
wm = _load("whisper_manager")
speech = _load("speech")
long_jobs = _load("long_jobs")

RATE = 16000


# -- generated sound ------------------------------------------------------------------------

def _tone(seconds, amp, freq=220.0):
    n = int(round(seconds * RATE))
    return array("h", (int(amp * 32767 * math.sin(2 * math.pi * freq * i / RATE)) for i in range(n)))


def _silence(seconds):
    return array("h", bytes(2 * int(round(seconds * RATE))))


_SYLLABLE = _tone(0.16, 0.3) + _tone(0.06, 0.003)       # loud, then a 40 dB dip
_WORD = _SYLLABLE + _SYLLABLE
_GAP = _silence(0.08)


def _sentence(words=8, gain=1.0):
    """About 4.2 s of "speech": words of two syllables with short gaps."""
    out = array("h")
    for _ in range(words):
        out += _WORD + _GAP
    if gain != 1.0:
        out = array("h", (int(s * gain) for s in out))
    return out


def _speech(seconds, gain=1.0, pause=1.0):
    """Sentences with a *pause* after each, for about *seconds*; and where the pauses are."""
    out, pauses = array("h"), []
    sentence = _sentence(gain=gain)
    while len(out) / RATE < seconds:
        out += sentence
        start = len(out) / RATE
        out += _silence(pause)
        pauses.append((start, len(out) / RATE))
    return out, pauses


def _db(samples):
    return sp.envelope_db(samples.tobytes(), int(RATE * sp.HOP_S))


def _write(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return str(path)


# ---------------------------------------------------------------------------
# 1. The envelope and the piece plan

class TestPlan(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.samples, cls.pauses = _speech(130.0)
        cls.db = _db(cls.samples)

    def test_envelope_is_one_value_per_10_ms(self):
        self.assertEqual(len(self.db), math.ceil(len(self.samples) / 160))
        self.assertAlmostEqual(max(self.db), 20 * math.log10(0.3 / math.sqrt(2)), delta=0.5)
        self.assertEqual(min(self.db), sp.FLOOR_DB, "digital silence is the floor")

    def test_pieces_cover_the_sound_back_to_back_in_pauses(self):
        pieces = sp.plan_pieces(self.db)
        self.assertGreaterEqual(len(pieces), 3)
        self.assertEqual(pieces[0][0], 0.0)
        self.assertAlmostEqual(pieces[-1][1], len(self.db) * sp.HOP_S, places=3)
        for (a, b), (c, _d) in zip(pieces, pieces[1:]):
            self.assertEqual(b, c, "back to back")
        for a, b in pieces:
            self.assertLessEqual(b - a, sp.PIECE_MAX_S + 1e-6, "two whisper windows at most")
        for a, b in pieces[:-1]:
            self.assertGreaterEqual(b - a, sp.PIECE_MIN_S - 1e-6, "not cut shorter (but the last)")
        for _a, cut in pieces[:-1]:
            self.assertTrue(any(p0 < cut < p1 for p0, p1 in self.pauses), "cut {:.2f} s in a pause".format(cut))

    def test_the_latest_good_pause_wins(self):
        pieces = sp.plan_pieces(self.db)
        cut = pieces[0][1]
        later = [p for p in self.pauses if p[0] > cut and p[1] <= sp.PIECE_MAX_S]
        self.assertEqual(later, [], "no whole pause left between the cut and the window's end")

    def test_a_long_silence_beats_a_gap_between_two_words_later(self):
        # Speech, 30 s of silence, then speech up to the window's end: the cut goes in the
        # silence, not in the 0.14 s gap between two words near the end (found on a test file).
        a, _ = _speech(20.0)
        samples = a + _silence(30.0) + _speech(40.0)[0]
        db = _db(samples)
        cut = sp.plan_pieces(db)[0][1]
        speech_a = len(a) / RATE
        self.assertTrue(speech_a < cut < speech_a + 30.0, cut)

    def test_short_sound_is_one_piece(self):
        db = _db(_speech(45.0)[0])
        self.assertEqual(sp.plan_pieces(db), [(0.0, round(len(db) * sp.HOP_S, 3))])
        self.assertEqual(sp.plan_pieces([]), [(0.0, 0.0)])

    def test_without_a_pause_it_cuts_at_the_quietest(self):
        loud = [-10.0] * 13000
        for i in range(4400, 4420):
            loud[i] = -25.0               # a softer 0.2 s, not a pause (above -35 dB)
        pieces = sp.plan_pieces(loud)
        self.assertTrue(4400 * sp.HOP_S <= pieces[0][1] <= 4420 * sp.HOP_S, pieces)
        self.assertTrue(all(b - a <= sp.PIECE_MAX_S + 1e-6 for a, b in pieces))

    def test_speech_span_trims_the_silent_ends(self):
        samples = _silence(3.0) + _sentence() + _silence(5.0)
        db = _db(samples)
        a, b = sp.speech_span(db, 0.0, len(db) * sp.HOP_S)
        self.assertAlmostEqual(a, 3.0 - 0.3, delta=0.02)
        self.assertAlmostEqual(b, 3.0 + len(_sentence()) / RATE - 0.08 - 0.06 + 0.5, delta=0.05)


# ---------------------------------------------------------------------------
# 2. The no-speech gate

class TestNoSpeech(unittest.TestCase):

    def gate(self, samples, file_db=None):
        db = _db(samples)
        quiet = sp.quiet_level(file_db if file_db is not None else db)
        return sp.has_speech(db, 0.0, len(db) * sp.HOP_S, quiet=quiet)

    def test_digital_silence(self):
        self.assertFalse(self.gate(_silence(20.0)))

    def test_a_steady_tone(self):
        self.assertFalse(self.gate(_tone(20.0, 0.3, 440.0)))

    def test_a_steady_chord_with_a_slow_swell(self):
        n = 20 * RATE
        chord = array("h", (int(32767 * 0.1 * (0.8 + 0.2 * math.sin(2 * math.pi * 0.25 * i / RATE))
                                * sum(math.sin(2 * math.pi * f * i / RATE) for f in (220.0, 277.18, 329.63)) / 3)
                            for i in range(n)))
        self.assertFalse(self.gate(chord))

    def test_speech_and_a_quiet_speaker(self):
        self.assertTrue(self.gate(_speech(10.0)[0]))
        self.assertTrue(self.gate(_speech(10.0, gain=0.1)[0]), "-20 dB: the pause level follows the file")

    def test_a_quiet_speaker_in_a_loud_file(self):
        loud, _ = _speech(10.0)
        quiet, _ = _speech(10.0, gain=0.1)
        self.assertTrue(self.gate(quiet, file_db=_db(loud + quiet)))

    def test_one_word_is_speech(self):
        self.assertTrue(self.gate(_silence(5.0) + _WORD + _GAP + _WORD + _silence(5.0)))


# ---------------------------------------------------------------------------
# 3. The repeat check

class TestRepeats(unittest.TestCase):

    SPEECH = ("so today we look at the strips and how the cut works when you move the "
              "playhead to the next shot and press the key again").split()

    def test_normal_speech_passes(self):
        self.assertEqual(sp.degenerate(self.SPEECH, 10.0), "")
        self.assertEqual(sp.degenerate("it is very very very good".split(), 3.0), "", "a word said three times")
        self.assertEqual(sp.degenerate("no no no no".split(), 2.0), "")

    def test_a_short_sequence_back_to_back(self):
        loop = self.SPEECH[:6] + "alpha beta gamma delta".split() * 250
        self.assertEqual(sp.repeat_run(loop)[:2], (250, 4))
        self.assertIn("4-word sequence repeated", sp.degenerate(loop, 1e6))
        self.assertIn("1-word sequence repeated 12 times", sp.degenerate(self.SPEECH[:3] + ["la", "la"] * 6, 30.0))

    def test_too_many_words_for_the_length(self):
        self.assertIn("words in 10 s", sp.degenerate(self.SPEECH * 3, 10.0))
        self.assertEqual(sp.degenerate(self.SPEECH * 3, 30.0), "")

    def test_punctuation_and_case_do_not_hide_a_loop(self):
        loop = ["Thank", "you."] * 3 + ["thank", "you"] * 3
        self.assertEqual(sp.repeat_run(loop)[:2], (6, 2))

    def test_words_as_dicts(self):
        self.assertTrue(sp.degenerate([{"w": "again,"}] * 20, 30.0))


# ---------------------------------------------------------------------------
# 4. Time shifting

def _seg(text, start, dtw, p=0.9, nsp=0.0):
    return {"text": " " + text, "start": start, "end": start + 0.3, "no_speech_prob": nsp,
            "words": [{"word": " " + text, "start": start, "end": start + 0.3, "t_dtw": dtw, "probability": p}]}


class TestShift(unittest.TestCase):

    def test_a_piece_moves_to_file_time(self):
        vj = {"duration": 20.0, "segments": [_seg("hello", 1.0, 124), _seg("there", 1.5, 174),
                                             _seg("late", 20.9, 2114)]}
        segs = sp.shift_verbose_json(vj, 60.0, length_s=20.0)
        self.assertEqual([s["text"] for s in segs], [" hello", " there"], "past the piece's end: dropped")
        self.assertEqual(segs[0]["start"], 61.0)
        self.assertEqual(segs[0]["words"][0]["t_dtw"], 124 + 6000)
        self.assertEqual(segs[1]["words"][0]["end"], 61.8)
        self.assertEqual(vj["segments"][0]["start"], 1.0, "the piece's own answer is left alone")

    def test_merged_pieces_give_file_times(self):
        a = {"duration": 25.0, "language": "german", "language_probabilities": {"de": 0.9, "en": 0.1},
             "segments": [_seg("eins", 0.5, 74)]}
        b = {"duration": 20.0, "segments": [_seg("zwei", 0.5, 74), _seg("drei", 1.0, 124)]}
        merged = sp.merge_verbose_json([(2.0, 25.0, a), (40.0, 20.0, b)], 75.0)
        self.assertEqual(merged["duration"], 75.0)
        self.assertEqual(merged["language_probabilities"], {"de": 0.9, "en": 0.1})
        words, timing = tr.verbose_json_words(merged)
        self.assertEqual(timing, "dtw")
        self.assertEqual([(w["w"], w["start"]) for w in words],
                         [("eins", 2.5), ("zwei", 40.5), ("drei", 41.0)])     # t_dtw - 0.24 + offset
        doc = tr.from_verbose_json(merged, "a.wav", "0123456789abcdef", 1, "small")
        self.assertEqual((doc["language"], doc["media"]["duration_s"]), ("de", 75.0))


# ---------------------------------------------------------------------------
# 5. Word ends

class TestWordEnds(unittest.TestCase):

    def test_the_cap_per_word(self):
        self.assertAlmostEqual(tr.word_cap("a"), 0.34)
        self.assertAlmostEqual(tr.word_cap("the"), 0.52)
        self.assertAlmostEqual(tr.word_cap("world."), 0.70)
        self.assertEqual(tr.word_cap("everything"), tr.MAX_WORD_S)
        self.assertEqual(tr.MAX_WORD_S, 1.0)

    def test_the_last_word_of_a_phrase_is_not_stretched_to_the_next(self):
        vj = {"duration": 10.0, "segments": [_seg("go", 1.0, 124), _seg("on", 1.4, 164), _seg("next", 6.0, 624)]}
        words, _ = tr.verbose_json_words(vj)
        self.assertEqual([w["end"] for w in words], [1.4, 1.83, 6.61])

    def test_ends_follow_the_sound_and_words_in_silence_go(self):
        # 1 s silence, a word of 0.44 s, 2 s silence: the word ends where its sound ends.
        samples = _silence(1.0) + _WORD + _silence(2.0)
        db = _db(samples)
        words = [{"w": "hello", "start": 1.0, "end": 1.7}, {"w": "ghost", "start": 2.5, "end": 3.2}]
        out = sp.refine_words(words, db)
        self.assertEqual([w["w"] for w in out], ["hello"], "a word with no sound near it is dropped")
        self.assertAlmostEqual(out[0]["end"], 1.0 + len(_WORD) / RATE - 0.06 + 0.03, delta=0.02)
        self.assertEqual(sp.refine_words(words, []), words, "no envelope: as they were")

    def test_an_early_start_does_not_cut_the_word_short(self):
        samples = _silence(1.0) + _WORD + _silence(2.0)
        out = sp.refine_words([{"w": "hello", "start": 0.85, "end": 1.55}], _db(samples))
        self.assertGreater(out[0]["end"], 1.3)


# ---------------------------------------------------------------------------
# 6. transcribe_pieces against a stand-in server

class _Server:
    """A stand-in for whisper_manager.transcribe_wav: two words per piece at 0.5 s / 1.0 s of the
    piece; pieces listed in *loops* loop (all requests, or the first only for *loops_once*)."""

    def __init__(self, loops=(), loops_once=(), lang="de"):
        self.calls, self.loops, self.loops_once, self.lang = [], set(loops), set(loops_once), lang
        self.on_call = None

    def __call__(self, path, language="auto", prompt="", port=None, timeout=3600.0, extra=()):
        with wave.open(str(path), "rb") as w:
            length = w.getnframes() / float(w.getframerate())
        self.calls.append({"path": str(path), "language": language, "prompt": prompt, "extra": dict(extra),
                           "length": length})
        if self.on_call is not None:
            self.on_call(len(self.calls))
        n = len(self.calls)
        piece = sum(1 for c in self.calls if c["extra"].get("temperature") == "0.0")
        retry = dict(extra).get("temperature") == "0.4"
        if piece in self.loops or (piece in self.loops_once and not retry):
            segs = [_seg(w, 0.2 + 0.05 * i, 44 + 5 * i) for i, w in enumerate(["round", "and", "round", "again"] * 40)]
        else:
            segs = [_seg("word{:d}a".format(n), 0.5, 74), _seg("word{:d}b".format(n), 1.0, 124)]
        vj = {"duration": length, "segments": segs, "language": "german"}
        if language == "auto":
            vj["language_probabilities"] = {self.lang: 0.9, "en": 0.1}
        return vj


PLAN = {"route": "local", "model": "small", "build": "cpu", "cache_model": "small", "language": "auto"}


class _Files(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # speech 40 s, digital silence 60 s, speech 100 s: about 200 s.
        a, _ = _speech(40.0)
        b, _ = _speech(100.0)
        self.samples = a + _silence(60.0) + b
        self.wav = _write(self.tmp / "0123456789abcdef.1.16k.wav", self.samples)

    def pieces_left(self):
        return sorted(p.name for p in self.tmp.glob("*.p*.16k.wav"))


class TestTranscribePieces(_Files):

    def run_pieces(self, server, stop=None, prompt=""):
        notes = []
        with mock.patch.object(wm, "transcribe_wav", server):
            doc = speech.transcribe_pieces(self.wav, "0123456789abcdef", 5, dict(PLAN), prompt, media="C:/m/talk.mp4",
                                           progress=lambda n, m, text: notes.append((n, m, text)), stop=stop)
        return doc, notes

    def test_one_request_per_piece_in_file_time(self):
        server = _Server()
        doc, notes = self.run_pieces(server)
        db = sp.wav_envelope(self.wav)["db"]
        quiet = sp.quiet_level(db)
        plan = sp.plan_pieces(db)
        sent = [p for p in plan if sp.has_speech(db, p[0], p[1], quiet=quiet)]
        self.assertGreaterEqual(len(plan), 4)
        self.assertEqual(len(server.calls), len(sent), "the silent piece(s) aren't sent")
        self.assertEqual(doc["engine"]["pieces"], {"scheme": tr.PIECES_SCHEME, "count": len(plan), "sent": len(sent),
                                                   "no_speech": len(plan) - len(sent), "retried": 0, "failed": []})
        for call in server.calls:
            self.assertEqual((call["extra"].get("max_context"), call["extra"].get("temperature")), ("0", "0.0"),
                             "no text context carried into a piece")
            self.assertLessEqual(call["length"], sp.PIECE_MAX_S + 1e-6)
        starts = [round(sp.speech_span(db, a, b, quiet=quiet)[0] + 0.5, 2) for a, b in sent]
        firsts = [w["start"] for w in doc["words"] if w["w"].endswith("a")]
        self.assertEqual(firsts, starts, "each piece's words moved back to file time")
        self.assertEqual(len(doc["words"]), 2 * len(sent))
        self.assertTrue(all(w["end"] - w["start"] <= tr.word_cap(w["w"]) + 1e-6 for w in doc["words"]))
        self.assertEqual(doc["media"]["duration_s"], round(len(self.samples) / RATE, 3))
        self.assertEqual([(n, m) for n, m, _t in notes], [(n, len(plan)) for n in range(1, len(plan) + 1)])
        self.assertIn("piece 1 of {:d} (0:00-".format(len(plan)), notes[0][2])
        self.assertEqual(self.pieces_left(), [], "no piece WAV left")
        self.assertEqual(tr.validate(doc), [])
        self.assertTrue(tr.is_current(doc))

    def test_the_language_found_first_is_kept(self):
        server = _Server(lang="de")
        doc, _ = self.run_pieces(server)
        self.assertEqual([c["language"] for c in server.calls], ["auto"] + ["de"] * (len(server.calls) - 1))
        self.assertEqual(doc["language"], "de")

    def test_a_looping_piece_is_tried_again_with_other_settings(self):
        server = _Server(loops_once={2})
        doc, _ = self.run_pieces(server)
        retries = [c for c in server.calls if c["extra"].get("temperature") == "0.4"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["extra"].get("best_of"), "5")
        self.assertEqual(doc["engine"]["pieces"]["retried"], 1)
        self.assertEqual(doc["engine"]["pieces"]["failed"], [])
        self.assertNotIn("round", [w["w"] for w in doc["words"]])

    def test_a_prompt_is_kept_and_the_retry_goes_without(self):
        # Probed (#540): max_context=0 drops the prompt too, so it isn't sent with one; the retry
        # of a looping piece drops the prompt (it can feed a loop) and the context.
        server = _Server(loops_once={1})
        self.run_pieces(server, prompt="Bforartists")
        first, retry = server.calls[0], server.calls[1]
        self.assertEqual((first["prompt"], "max_context" in first["extra"]), ("Bforartists", False))
        self.assertEqual((retry["prompt"], retry["extra"].get("max_context")), ("", "0"))
        self.assertEqual(wm.piece_fields(), wm.PIECE_FIELDS)
        self.assertEqual(wm.inference_fields("en", "", wm.piece_fields())[-3:],
                         [("max_context", "0"), ("temperature", "0.0"), ("temperature_inc", "0.2")])

    def test_a_piece_that_still_loops_is_kept_empty_and_said(self):
        server = _Server(loops={2})
        doc, _ = self.run_pieces(server)
        failed = doc["engine"]["pieces"]["failed"]
        self.assertEqual(len(failed), 1)
        self.assertRegex(failed[0][2], r"^(160 words in \d+ s|a 4-word sequence repeated 40 times)$")
        self.assertNotIn("round", [w["w"] for w in doc["words"]])
        self.assertGreater(len(doc["words"]), 0, "the other pieces keep their words")
        out = speech.shape_result(doc, {"content_start": 1.0, "left": 1.0, "right": 1e6, "fps": 24.0}, "talk")
        self.assertEqual(out["pieces"]["failed"], [failed[0][:2]])
        self.assertIn("1 piece kept repeating itself even when tried again", out["message"])

    def test_a_stop_ends_it_before_the_next_piece(self):
        server = _Server()
        stop = threading.Event()
        server.on_call = lambda n: stop.set()
        with self.assertRaises(speech._Stopped):
            self.run_pieces(server, stop=stop)
        self.assertEqual(len(server.calls), 1)
        self.assertEqual(self.pieces_left(), [])

    def test_a_wav_it_cannot_read_is_sent_whole_as_before(self):
        bad = self.tmp / "0123456789abcdef.2.16k.wav"
        bad.write_bytes(b"RIFF" + b"\0" * 64)
        calls = []

        def server(path, language="auto", prompt="", extra=()):
            calls.append((str(path), dict(extra)))
            return {"duration": 2.0, "segments": [_seg("hi", 0.5, 74)]}
        with mock.patch.object(wm, "transcribe_wav", server):
            doc = speech.transcribe_pieces(str(bad), "0123456789abcdef", 5, dict(PLAN))
        self.assertEqual([c[0] for c in calls], [str(bad)])
        self.assertEqual(calls[0][1].get("max_context"), "0")
        self.assertEqual([w["w"] for w in doc["words"]], ["hi"])
        self.assertEqual(doc["engine"]["pieces"]["count"], 1)


# ---------------------------------------------------------------------------
# 7. The job: progress, the cache, Stop

class TestJob(_Files):

    def setUp(self):
        super().setUp()
        wm.set_data_dir(self.tmp / "whisper")
        self.addCleanup(wm.set_data_dir, None)
        self.server = _Server()
        src = self.wav
        for p in (mock.patch.object(speech, "_write_wav",
                                    lambda media, out, stop=None: Path(out).write_bytes(Path(src).read_bytes())),
                  mock.patch.object(wm, "start_server", lambda *a, **k: ""),
                  mock.patch.object(wm, "stop_server", lambda: None),
                  mock.patch.object(wm, "is_ready", lambda: True),
                  mock.patch.object(wm, "build_info", lambda build: {}),
                  mock.patch.object(wm, "transcribe_wav", self.server)):
            p.start()
            self.addCleanup(p.stop)

    def work(self, blend=""):
        job = speech._Job()
        speech._work(job, str(self.tmp / "talk.mp4"), "0123456789abcdef", 5, dict(PLAN, language="en"),
                     {"port": 1}, blend, "")
        return job

    def test_cached_once_per_file_and_found_after_the_blend_moves(self):
        blend = self.tmp / "proj" / "edit.blend"
        job = self.work(str(blend))
        self.assertEqual(job.error, "")
        self.assertEqual(Path(job.path).parent, (self.tmp / "proj").resolve() / "coworker_transcripts")
        names = sorted(p.name for p in (self.tmp / "proj" / "coworker_transcripts").glob("*.json"))
        self.assertEqual(names, ["0123456789abcdef.small.en.json"], "one file per media file")
        moved = self.tmp / "elsewhere" / "edit.blend"
        self.assertIsNone(tr.find_cached("0123456789abcdef", "small", "en", str(moved)))
        found = tr.find_cached("0123456789abcdef", "small", "en", "", wm.data_dir())
        self.assertIsNotNone(found, "#541: the add-on's cache folder has it too")
        self.assertEqual(tr.read(found)["words"], job.doc["words"])

    def test_a_stop_between_pieces_caches_nothing(self):
        job = speech._Job()
        self.server.on_call = lambda n: job.stopped.set() if n == 2 else None
        speech._work(job, str(self.tmp / "talk.mp4"), "0123456789abcdef", 5, dict(PLAN), {"port": 1},
                     str(self.tmp / "proj" / "edit.blend"), "")
        self.assertEqual(job.error, speech.STOPPED)
        self.assertIsNone(job.doc)
        self.assertEqual(list(self.tmp.glob("**/*.json")), [], "no transcript, not even of the pieces done")
        self.assertEqual(list((Path(tempfile.gettempdir()) / "bfa_coworker_speech").glob("0123456789abcdef.*")), [])

    def test_progress_while_it_runs(self):
        job = speech._Job()
        seen = []
        self.server.on_call = lambda n: seen.append(job.progress_state())
        speech._work(job, str(self.tmp / "talk.mp4"), "0123456789abcdef", 5, dict(PLAN), {"port": 1}, "", "")
        m = job.doc["engine"]["pieces"]["count"]
        frac, text, short = seen[0]
        self.assertEqual((frac, short), (0.0, "piece 1 of {:d}".format(m)))
        self.assertIn("Transcribing piece 1 of {:d} (0:00-".format(m), text)
        self.assertIn("with small (CPU)", text)
        self.assertEqual(seen[-1][2], "piece {:d} of {:d}".format(m, m))
        self.assertAlmostEqual(seen[-1][0], (m - 1) / m)


class TestOldTranscripts(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def doc(self, engine):
        return {"schema": tr.SCHEMA, "media": {"path": "a.mp4", "key": "0123456789abcdef", "size": 1, "duration_s": 1.0},
                "engine": engine, "language": "en", "words": [{"w": "hi", "start": 0.1, "end": 0.4}]}

    def test_a_whole_file_local_transcript_is_transcribed_again(self):
        old = self.doc({"name": "whisper.cpp", "model": "small", "timing": "dtw"})
        new = self.doc({"name": "whisper.cpp", "model": "small", "timing": "dtw",
                        "pieces": {"scheme": tr.PIECES_SCHEME, "count": 1}})
        remote = self.doc({"name": "remote", "model": "whisper-1", "timing": "remote"})
        self.assertEqual([tr.is_current(d) for d in (old, new, remote)], [False, True, True])
        path = tr.cache_path("0123456789abcdef", "small", "en", "", self.tmp)
        tr.write(path, old)
        self.assertIsNone(tr.find_cached("0123456789abcdef", "small", "en", "", self.tmp))
        tr.write(path, new)
        self.assertEqual(tr.find_cached("0123456789abcdef", "small", "en", "", self.tmp), path)
        rpath = tr.cache_path("0123456789abcdef", "remote-whisper-1", "en", "", self.tmp)
        tr.write(rpath, remote)
        self.assertEqual(tr.find_cached("0123456789abcdef", "remote-whisper-1", "en", "", self.tmp), rpath)


class TestJobRow(unittest.TestCase):

    def setUp(self):
        long_jobs.clear()
        self.addCleanup(long_jobs.clear)

    def checker(self, state):
        def check():
            return None
        check.job_progress = lambda: state[0]
        return check

    def test_piece_n_of_m_in_the_status_and_the_row(self):
        a, b = [(None, "Starting whisper-server", "")], [(None, "", "")]
        ja = long_jobs.start("TRANSCRIBE", "Transcribe a", self.checker(a))
        jb = long_jobs.start("TRANSCRIBE", "Transcribe b", self.checker(b))
        self.assertNotIn("progress_percent", long_jobs.status(ja))
        self.assertEqual(long_jobs.status(ja)["progress_text"], "Starting whisper-server")
        a[0] = (0.4, "Transcribing piece 3 of 5 (0:58-1:27 of 2:20) with small (CPU)", "piece 3 of 5")
        st = long_jobs.status(ja)
        self.assertEqual((st["progress_percent"], st["progress_step"]), (40, "piece 3 of 5"))
        self.assertIn("piece 3 of 5", st["progress_text"])
        rows = {r["label"]: r for r in long_jobs.running()}
        self.assertEqual(rows["Transcribe a"]["text"], "Transcribing 40%, piece 3 of 5")
        self.assertTrue(rows["Transcribe b"]["text"].startswith("Transcribing, "), "b shows its own, not a's")
        self.assertNotIn("progress_step", long_jobs.status(jb))

    def test_auto_captions_status_line_reads_it(self):
        # captions_ops.job_status_text reads the first TRANSCRIBE row's progress_text.
        a = [(0.5, "Transcribing piece 2 of 4 (0:29-0:58 of 1:40) with small (CPU)", "piece 2 of 4")]
        long_jobs.start("TRANSCRIBE", "Transcribe a", self.checker(a))
        rows = [j for j in long_jobs.running() if j.get("kind") == "TRANSCRIBE"]
        self.assertIn("piece 2 of 4", rows[0]["progress_text"])

    def test_the_tool_hands_its_progress_to_the_job(self):
        src = (_ADDON / "speech.py").read_text(encoding="utf-8")
        self.assertIn("check_is_finished.job_progress = job.progress_state", src)


if __name__ == "__main__":
    unittest.main()
