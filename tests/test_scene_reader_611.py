# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the scene reader (Highlights H1, #611): ``scene_reader.py`` (bpy-free) on made-up edits,
and ``scene_reader_ops.py`` (the Blender side) on stand-in strips.

* Two media files are merged by the time they play; sentences come out in playing order.
* Two microphones on the same speech: one is kept (higher word confidence), none doubled, the
  report names what was dropped; two speakers on two microphones who take turns are both kept.
* A clip used twice gives its sentences twice.  Muted strips are skipped; retimed strips and strips
  inside metas are skipped and the report gives their share of the edit; a clip without a transcript
  is reported.
* Sentences: the lines Edit by Transcript makes, split at the longest pause above 20 s; the counted
  facts (seconds, words per second, fillers, pause before, question, number, speaker change).
* Windows: size from the context, at most 2 400 words, one minute of overlap, always moving on.
* The tool ``read_edit_sentences`` pages the sentences and says what was left out.

Run with::

    python -m unittest tests.test_scene_reader_611 -v
"""

__all__ = ()

import itertools
import types
import unittest

import test_transcript_edit_ops as OPS
import test_wand_remove_silences as W

SR = W._import("scene_reader")
FPS = 30


def talk(sentences, step=0.4, length=0.3, p=None):
    """Words of made-up sentences ``(start second, text)``."""
    out = []
    for start, text in sentences:
        t = start
        for w in text.split():
            word = {"w": w, "start": round(t, 3), "end": round(t + length, 3)}
            if p is not None:
                word["p"] = p
            out.append(word)
            t += step
    return out


def piece(name, left=1, seconds=60, cs=1.0, channel=1, **extra):
    return dict({"name": name, "left": left, "right": left + int(seconds * FPS), "content_start": cs,
                 "offset": 0.0, "channel": channel}, **extra)


def source(key, words, *pieces):
    return {"key": key, "path": key + ".wav", "words": words, "pieces": list(pieces)}


def texts(result):
    return [s["text"] for s in result["sentences"]]


class TestMerge(unittest.TestCase):

    def test_two_files_in_playing_order(self):
        a = source("a" * 16, talk([(1.0, "Hello from the first file."), (20.0, "Back to the first file.")]),
                   piece("A"))
        b = source("b" * 16, talk([(10.0, "Now the second file speaks.")]), piece("B", channel=2))
        out = SR.read_scene([b, a], FPS)       # the order of the sources does not matter
        self.assertEqual(texts(out), ["Hello from the first file.", "Now the second file speaks.",
                                      "Back to the first file."])
        self.assertEqual([s["n"] for s in out["sentences"]], [1, 2, 3])
        self.assertEqual(out["sentences"][1]["first"], {"media": "b" * 16, "i": 0})
        self.assertEqual(out["sentences"][1]["media"], ["b" * 16])
        self.assertEqual(out["report"]["duplicates"], [])

    def test_a_cut_edit_plays_in_timeline_order(self):
        # The second half of the file plays first, the first half after it.
        words = talk([(1.0, "This is the opening line."), (30.0, "This is the closing line.")])
        a = source("a" * 16, words, piece("late", left=1, seconds=10, cs=1 - 29.0 * FPS),
                   piece("early", left=1 + 10 * FPS, seconds=10, cs=1.0 + 10 * FPS))
        out = SR.read_scene([a], FPS)
        self.assertEqual(texts(out), ["This is the closing line.", "This is the opening line."])

    def test_clip_used_twice_gives_two_sets(self):
        words = talk([(1.0, "Say this twice.")])
        a = source("a" * 16, words, piece("one", left=1, seconds=5, cs=1.0),
                   piece("two", left=1 + 10 * FPS, seconds=5, cs=1.0 + 10 * FPS))
        out = SR.read_scene([a], FPS)
        self.assertEqual(texts(out), ["Say this twice.", "Say this twice."])
        self.assertEqual([s["start"] for s in out["sentences"]], [31, 31 + 10 * FPS])
        self.assertEqual(out["report"]["duplicates"], [])

    def test_empty_edit(self):
        out = SR.read_scene([], FPS)
        self.assertEqual(out["sentences"], [])
        self.assertIn("No speech to read.", out["report"]["messages"])


class TestTwoMicrophones(unittest.TestCase):

    def test_same_speech_twice_keeps_the_better_one(self):
        said = [(2.0, "We moved the whole project to a new rig."), (9.0, "It was faster in every way.")]
        good = source("a" * 16, talk(said, p=0.95), piece("mic A"))
        bad = source("b" * 16, talk(said, p=0.60), piece("mic B", channel=2))
        for order in ([good, bad], [bad, good]):
            out = SR.read_scene(order, FPS)
            self.assertEqual(texts(out), ["We moved the whole project to a new rig.", "It was faster in every way."])
            self.assertEqual({m for s in out["sentences"] for m in s["media"]}, {"a" * 16})
            dup = out["report"]["duplicates"]
            self.assertEqual(len(dup), 1)
            self.assertEqual((dup[0]["kept"], dup[0]["dropped"]), ("mic A", "mic B"))
            self.assertEqual(dup[0]["words"], 15)
            self.assertTrue(any("mic B" in m for m in out["report"]["messages"]))

    def test_timing_jitter_is_still_the_same_speech(self):
        said = [(2.0, "A phrase said close to the microphone.")]
        a = source("a" * 16, talk(said, p=0.9), piece("A"))
        b_words = talk([(2.1, "A phrase said close to the microphone.")], p=0.8)
        b = source("b" * 16, b_words, piece("B", channel=2))
        out = SR.read_scene([a, b], FPS)
        self.assertEqual(len(out["sentences"]), 1)

    def test_no_confidence_keeps_the_first_strip_every_time(self):
        said = [(2.0, "Nothing says which one is better here.")]
        a = source("a" * 16, talk(said), piece("A"))
        b = source("b" * 16, talk(said), piece("B", channel=2))
        one = SR.read_scene([a, b], FPS)
        two = SR.read_scene([b, a], FPS)
        self.assertEqual(one["report"]["duplicates"][0]["kept"], "A")
        self.assertEqual(two["report"]["duplicates"][0]["kept"], "A")

    def test_speakers_who_take_turns_are_both_kept(self):
        ana = source("a" * 16, talk([(1.0, "What did you build this year?"), (20.0, "That sounds hard.")]),
                     piece("Ana", speaker="Ana"))
        bo = source("b" * 16, talk([(8.0, "A tool for artists, mostly."), (26.0, "It was.")], p=0.7),
                    piece("Bo", channel=2, speaker="Bo"))
        out = SR.read_scene([ana, bo], FPS)
        self.assertEqual(texts(out), ["What did you build this year?", "A tool for artists, mostly.",
                                      "That sounds hard.", "It was."])
        self.assertEqual(out["report"]["duplicates"], [])
        self.assertEqual([s["speaker_change"] for s in out["sentences"]], [False, True, True, True])

    def test_short_reply_on_another_mic_does_not_wipe_the_phrase(self):
        ana = source("a" * 16, talk([(1.0, "We moved the whole project to a new rig")], p=0.9), piece("Ana", speaker="Ana"))
        bo = source("b" * 16, talk([(2.0, "Right right")], p=0.95), piece("Bo", channel=2, speaker="Bo"))
        out = SR.read_scene([ana, bo], FPS)
        self.assertEqual(sum(s["words"] for s in out["sentences"]), 11)
        self.assertIn("We moved the whole project to a new rig", texts(out))
        self.assertEqual(out["report"]["duplicates"], [])

    def test_camera_plus_two_lapels_loses_no_question(self):
        ana_said = [(1.0, "Did you change the rig this year")]
        bo_said = [(3.8, "Yes we changed all of it")]       # no pause: the camera hears one long phrase
        cam_words = talk(ana_said + bo_said, p=0.8)
        ana = source("a" * 16, talk(ana_said, p=0.7), piece("Ana lapel", channel=2, speaker="Ana"))
        bo = source("b" * 16, talk(bo_said, p=0.95), piece("Bo lapel", channel=3, speaker="Bo"))
        cam = source("c" * 16, cam_words, piece("Camera", channel=1, speaker="Cam"))
        for order in itertools.permutations([ana, bo, cam]):
            out = SR.read_scene(list(order), FPS)
            self.assertEqual(sum(s["words"] for s in out["sentences"]), 13, order)
            allwords = " ".join(texts(out)).split()
            self.assertEqual(sorted(allwords), sorted("Did you change the rig this year Yes we changed all of it".split()))

    def test_better_mic_trimmed_shorter_keeps_the_rest_of_the_phrase(self):
        said = [(1.0, "We moved the whole project to a new rig and it was faster")]
        a = source("a" * 16, talk(said, p=0.7), piece("mic A"))
        b = source("b" * 16, talk(said, p=0.95), piece("mic B", channel=2, seconds=2.4))      # ends before the phrase does
        out = SR.read_scene([a, b], FPS)
        self.assertEqual(" ".join(texts(out)).split(), said[0][1].split())
        dup = out["report"]["duplicates"]
        self.assertEqual((dup[0]["kept"], dup[0]["dropped"]), ("mic B", "mic A"))
        self.assertLess(dup[0]["words"], 13)

    def test_overlapping_speakers_are_not_woven_into_one_sentence(self):
        ana = source("a" * 16, talk([(1.0, "I think the main thing we learned this year was patience")]), piece("Ana", speaker="Ana"))
        bo = source("b" * 16, talk([(4.4, "yes and also that tools matter a lot for artists")], p=0.6), piece("Bo", channel=2, speaker="Bo"))
        out = SR.read_scene([ana, bo], FPS)
        self.assertEqual(texts(out), ["I think the main thing we learned this year was patience",
                                      "yes and also that tools matter a lot for artists"])
        self.assertEqual([s["speaker"] for s in out["sentences"]], ["Ana", "Bo"])

    def test_one_microphone_cut_in_pieces_is_not_a_duplicate(self):
        words = talk([(1.0, "One part."), (5.0, "Another part.")])
        a = source("a" * 16, words, piece("one", left=1, seconds=3), piece("two", left=1 + 4 * FPS, seconds=4, cs=1.0))
        out = SR.read_scene([a], FPS)
        self.assertEqual(texts(out), ["One part.", "Another part."])
        self.assertEqual(out["report"]["duplicates"], [])


class TestSkipped(unittest.TestCase):

    def test_muted_retimed_meta_and_missing_transcript(self):
        words = talk([(1.0, "This one plays.")])
        play = source("a" * 16, words, piece("play", seconds=10))
        muted = source("b" * 16, talk([(1.0, "Muted words.")]), piece("quiet", seconds=10, channel=2, skip="muted"))
        retimed = source("c" * 16, talk([(1.0, "Fast words.")]), piece("fast", seconds=10, channel=3, skip="retimed"))
        meta = {"key": "meta:M", "path": "", "words": None, "pieces": [piece("M", seconds=20, channel=4, skip="meta")]}
        none = source("d" * 16, None, piece("raw", seconds=10, channel=5))
        out = SR.read_scene([play, muted, retimed, meta, none], FPS)
        self.assertEqual(texts(out), ["This one plays."])
        rep = out["report"]
        self.assertEqual(rep["skipped"]["muted"]["names"], ["quiet"])
        self.assertEqual(rep["skipped"]["retimed"]["strips"], 1)
        self.assertEqual(rep["skipped"]["meta"]["frames"], 20 * FPS)
        self.assertEqual(rep["skipped"]["no_transcript"]["names"], ["raw"])
        # 10 s played + 10 s no transcript + 10 s retimed + 20 s meta count; the muted strip does not.
        self.assertAlmostEqual(rep["unsupported_share"], 30 / 50, places=3)
        self.assertEqual(rep["unsupported_seconds"], 30.0)
        text = " ".join(rep["messages"])
        self.assertIn("60%", text)
        self.assertIn("without a transcript", text)
        self.assertIn("muted", text)


class TestSentences(unittest.TestCase):

    def test_counted_facts(self):
        words = talk([(1.0, "Um, so what is the price of 3 apples?"), (8.0, "It is five dollars.")])
        a = source("a" * 16, words, piece("A"))
        out = SR.read_scene([a], FPS)
        first, second = out["sentences"]
        self.assertTrue(first["question"])
        self.assertTrue(first["number"])
        self.assertEqual(first["fillers"], 1)
        self.assertEqual(first["words"], 9)
        self.assertEqual(first["pause_before"], 0.0)
        self.assertFalse(second["question"])
        self.assertFalse(second["number"])
        self.assertGreater(second["pause_before"], 2.0)
        self.assertAlmostEqual(first["wps"], first["words"] / first["seconds"], places=1)
        self.assertEqual(first["t"], 1.0)

    def test_long_sentence_is_split_at_its_longest_pause(self):
        # One sentence of 60 words with no full stop, one 1.5 s pause at word 40 and 1 s at word 20.
        words, t = [], 1.0
        for k in range(60):
            if k == 20:
                t += 0.9
            if k == 40:
                t += 1.5
            words.append({"w": "word{:d}".format(k) + ("," if k % 9 == 8 else ""), "start": t, "end": t + 0.3})
            t += 0.45
        words[-1]["w"] = "last."
        a = source("a" * 16, words, piece("A", seconds=60))
        out = SR.read_scene([a], FPS)
        self.assertGreaterEqual(len(out["sentences"]), 2)
        for s in out["sentences"]:
            self.assertLessEqual(s["seconds"], SR.MAX_SENTENCE_S + 0.1)
        self.assertEqual(sum(s["words"] for s in out["sentences"]), 60)
        joined = " ".join(texts(out))
        self.assertEqual(joined, " ".join(w["w"] for w in words))

    def test_format_and_version(self):
        a = source("a" * 16, talk([(1.0, "Is this a question?"), (9.0, "Yes, 2 of them.")]), piece("A"))
        out = SR.read_scene([a], FPS)
        line = SR.format_sentence(out["sentences"][0])
        self.assertTrue(line.startswith("S1 [0:01.0 "))
        self.assertIn("?", line)
        self.assertTrue(line.endswith("Is this a question?"))
        self.assertIn("pause", SR.format_sentence(out["sentences"][1]))
        v = SR.version(out["sentences"])
        self.assertEqual(v, SR.version(SR.read_scene([a], FPS)["sentences"]))
        out["sentences"][0]["text"] = "changed"
        self.assertNotEqual(v, SR.version(out["sentences"]))


def sentence_list(count, words=15, seconds=6.0):
    """*count* made-up sentences back to back."""
    return [{"n": k + 1, "words": words, "t": round(k * seconds, 2), "seconds": seconds - 0.5,
             "start": 1 + int(k * seconds * FPS), "end": 1 + int((k * seconds + seconds - 0.5) * FPS)}
            for k in range(count)]


class TestWindows(unittest.TestCase):

    def test_one_hour_on_32k(self):
        sents = sentence_list(600)                  # 9 000 words, 60 minutes
        wins = SR.windows(sents, 32768)
        self.assertTrue(all(w["words"] <= SR.MAX_WINDOW_WORDS for w in wins))
        self.assertTrue(4 <= len(wins) <= 6, len(wins))
        self.assertEqual(wins[0]["first"], 1)
        self.assertEqual(wins[-1]["last"], 600)

    def test_smaller_context_gives_more_windows(self):
        sents = sentence_list(600)
        n32 = len(SR.windows(sents, 32768))
        n16 = len(SR.windows(sents, 16384))
        n8 = len(SR.windows(sents, 8192))
        self.assertLessEqual(n32, n16)
        self.assertLess(n16, n8)
        for w in SR.windows(sents, 8192):
            self.assertLessEqual(w["tokens"], SR.window_budget(8192))

    def test_every_sentence_is_in_a_window_and_they_overlap_a_minute(self):
        sents = sentence_list(600)
        wins = SR.windows(sents, 32768)
        seen = set()
        for w in wins:
            seen.update(range(w["first"], w["last"] + 1))
        self.assertEqual(seen, set(range(1, 601)))
        for a, b in zip(wins, wins[1:]):
            self.assertLessEqual(b["first"], a["last"])                 # they overlap
            self.assertGreater(b["first"], a["first"])                  # and move on
            shared = a["t1"] - sents[b["first"] - 1]["t"]
            self.assertGreaterEqual(shared, SR.OVERLAP_S - 6.0)
            self.assertLessEqual(shared, SR.OVERLAP_S + 6.0)

    def test_short_edit_is_one_window_and_tiny_context_none(self):
        sents = sentence_list(10)
        wins = SR.windows(sents, 32768)
        self.assertEqual(len(wins), 1)
        self.assertEqual((wins[0]["first"], wins[0]["last"], wins[0]["words"]), (1, 10, 150))
        self.assertEqual(SR.windows(sents, 2048), [])
        self.assertEqual(SR.windows([], 32768), [])

    def test_unknown_context_is_32k(self):
        sents = sentence_list(600)
        self.assertEqual(SR.windows(sents, None), SR.windows(sents, 32768))

    def test_a_giant_sentence_still_gets_a_window(self):
        sents = sentence_list(3, words=5000)
        wins = SR.windows(sents, 32768)
        self.assertEqual([w["first"] for w in wins], [1, 2, 3])


# -- the Blender side ----------------------------------------------------------------------------

class _Strip:
    def __init__(self, name, kind, path, left, seconds, channel=1, mute=False, keys=0, cs=1.0, **extra):
        self.name, self.type, self.mute, self.channel = name, kind, mute, channel
        self.left_handle, self.right_handle = left, left + int(seconds * FPS)
        self.content_start, self.sound_offset = cs, 0.0
        self.retiming_keys = [object()] * keys
        if kind == 'SOUND':
            self.sound = types.SimpleNamespace(filepath=path, library=None)
        else:
            self.filepath = path
        for k, v in extra.items():
            setattr(self, k, v)


class _Ed:
    def __init__(self, strips, muted_channels=()):
        self.strips = list(strips)
        self.meta_stack = []
        self.channels = {c: types.SimpleNamespace(mute=c in muted_channels) for c in range(1, 10)}

    @property
    def strips_all(self):
        out = []
        for s in self.strips:
            out.append(s)
            out.extend(getattr(s, "strips", []))
        return out


class TestBlenderSide(unittest.TestCase):

    def setUp(self):
        self.bpy = OPS._fake_bpy()
        self.ops = OPS._load(self.bpy)
        pkg = self.ops.__name__.rsplit(".", 1)[0]
        import importlib
        self.mod = importlib.import_module(pkg + ".scene_reader_ops")
        self.words = {
            "ana.wav": talk([(1.0, "Hello there, how are you doing today?")], p=0.9),
            "bo.wav": talk([(8.0, "Quite well, thank you very much.")], p=0.9),
            "fast.wav": talk([(1.0, "Too fast to read.")]),
        }
        self.ops.find_transcript = lambda path: path if path in self.words else ""
        self.ops.load_words = lambda path: self.words[path]
        self.ops.strip_path = lambda s: s.sound.filepath if s.type == 'SOUND' else s.filepath
        import sys
        tr = sys.modules[pkg + ".transcripts"] if pkg + ".transcripts" in sys.modules else importlib.import_module(pkg + ".transcripts")
        self._media_key = tr.media_key
        tr.media_key = lambda path: (path.replace(".wav", "").ljust(16, "0")[:16], 1)
        self.addCleanup(setattr, tr, "media_key", self._media_key)

    def scene(self, strips, **kw):
        ed = _Ed(strips, **kw)
        return types.SimpleNamespace(name="Scene", sequence_editor=ed, frame_start=1,
                                     render=types.SimpleNamespace(fps=FPS, fps_base=1.0))

    def test_collect_prefers_sound_strips_and_flags_the_rest(self):
        meta_inner = _Strip("inner", 'SOUND', "ana.wav", 1, 5)
        meta = _Strip("Meta", 'META', "", 200, 10, channel=4, strips=[meta_inner])
        speed = _Strip("Speed", 'SPEED', "", 1, 5, channel=6, input_1=None)
        fast = _Strip("fast", 'SOUND', "fast.wav", 1, 10, channel=5)
        speed.input_1 = fast
        strips = [
            _Strip("ana", 'SOUND', "ana.wav", 1, 30),
            _Strip("ana video", 'MOVIE', "ana.wav", 1, 30, channel=2),
            _Strip("ana again.001", 'SOUND', "ana.wav", 100, 30, channel=7),
            _Strip("bo", 'SOUND', "bo.wav", 1, 30, channel=3, mute=True),
            fast, speed, meta,
        ]
        sources = self.mod.collect_sources(self.scene(strips))
        by = {s["name"]: s for s in sources}
        self.assertEqual([p["name"] for p in by["ana.wav"]["pieces"]], ["ana", "ana again.001"])   # the sounds, not the movie
        self.assertEqual(by["bo.wav"]["pieces"][0]["skip"], "muted")
        self.assertEqual(by["fast.wav"]["pieces"][0]["skip"], "retimed")
        self.assertEqual(by["Meta"]["pieces"][0]["skip"], "meta")
        self.assertEqual([p["speaker"] for p in by["ana.wav"]["pieces"]], ["Ana", "Ana"])   # one file, one speaker

    def test_muted_channel_and_retiming_keys(self):
        strips = [_Strip("ana", 'SOUND', "ana.wav", 1, 30, channel=2), _Strip("bo", 'SOUND', "bo.wav", 1, 30, channel=3, keys=3)]
        sources = self.mod.collect_sources(self.scene(strips, muted_channels={2}))
        by = {s["name"]: s["pieces"][0].get("skip") for s in sources}
        self.assertEqual(by, {"ana.wav": "muted", "bo.wav": "retimed"})

    def test_tool_pages_the_sentences(self):
        strips = [_Strip("ana", 'SOUND', "ana.wav", 1, 30), _Strip("bo", 'SOUND', "bo.wav", 1, 30, channel=2),
                  _Strip("raw", 'SOUND', "raw.wav", 1, 10, channel=3)]
        scene = self.scene(strips)
        self.ops._tool_scene = lambda: scene
        self.mod.context_tokens = lambda: 16384
        out = self.mod.tool_read_edit_sentences()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["windows_total"], len(out["windows"]))
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["text"].splitlines()[0][:2], "S1")
        self.assertIn("Hello there", out["text"])
        self.assertIn("Quite well", out["text"].splitlines()[1])
        self.assertEqual(out["windows"][0]["last"], 2)
        self.assertIn("without a transcript", " ".join(out["report"]["messages"]))
        page = self.mod.tool_read_edit_sentences(first=2, count=1)
        self.assertEqual((page["first"], page["last"], page["next_first"]), (2, 2, None))
        self.assertTrue(page["text"].startswith("S2 "))
        first = self.mod.tool_read_edit_sentences(first=1, count=1)
        self.assertEqual(first["next_first"], 2)

    def test_tool_without_speech_or_strips(self):
        self.mod.context_tokens = lambda: None          # no network in a test
        scene = self.scene([])
        self.ops._tool_scene = lambda: scene
        out = self.mod.tool_read_edit_sentences()
        self.assertEqual(out["status"], "error")
        self.assertIn("No speech", out["message"])
        scene.sequence_editor = None
        self.assertEqual(self.mod.tool_read_edit_sentences()["status"], "error")

    def test_two_end_keys_retimed_only_when_the_speed_changed(self):
        import sys
        pkg = self.ops.__name__.rsplit(".", 1)[0]
        fake = types.ModuleType(pkg + ".wand.sequencer")
        wand = types.ModuleType(pkg + ".wand")
        wand.sequencer = fake
        self.addCleanup(lambda: [sys.modules.pop(k, None) for k in (pkg + ".wand", pkg + ".wand.sequencer")])
        sys.modules[pkg + ".wand"], sys.modules[pkg + ".wand.sequencer"] = wand, fake
        fake._speed_changed = lambda strip, fps: strip.name == "fast"
        strips = [_Strip("fast", 'SOUND', "fast.wav", 1, 30, keys=2), _Strip("ana", 'SOUND', "ana.wav", 1, 30, channel=2, keys=2)]
        by = {s["name"]: s["pieces"][0].get("skip") for s in self.mod.collect_sources(self.scene(strips))}
        self.assertEqual(by, {"fast.wav": "retimed", "ana.wav": None})

    def test_two_end_keys_that_cannot_be_measured_count_as_retimed(self):
        import sys
        pkg = self.ops.__name__.rsplit(".", 1)[0]
        fake = types.ModuleType(pkg + ".wand.sequencer")
        wand = types.ModuleType(pkg + ".wand")
        wand.sequencer = fake
        self.addCleanup(lambda: [sys.modules.pop(k, None) for k in (pkg + ".wand", pkg + ".wand.sequencer")])
        sys.modules[pkg + ".wand"], sys.modules[pkg + ".wand.sequencer"] = wand, fake      # no _speed_changed in it
        sources = self.mod.collect_sources(self.scene([_Strip("ana", 'SOUND', "ana.wav", 1, 30, keys=2)]))
        self.assertEqual(sources[0]["pieces"][0]["skip"], "retimed")

    def test_speakers_from_strip_names_clashes_numbered_and_muted_files_not_counted(self):
        self.words["x/ana.wav"] = self.words["ana.wav"]
        self.words["y/ana.wav"] = self.words["bo.wav"]
        strips = [_Strip("Ana", 'SOUND', "x/ana.wav", 1, 30), _Strip("Ana.001", 'SOUND', "y/ana.wav", 1, 30, channel=2)]
        by = {s["path"]: s["pieces"][0]["speaker"] for s in self.mod.collect_sources(self.scene(strips))}
        self.assertEqual(sorted(by.values()), ["Ana", "Ana 2"])
        muted = [_Strip("ana", 'SOUND', "ana.wav", 1, 30), _Strip("bo", 'SOUND', "bo.wav", 1, 30, channel=2, mute=True)]
        for src in self.mod.collect_sources(self.scene(muted)):
            self.assertNotIn("speaker", src["pieces"][0])

    def test_tool_output_lists_have_a_limit(self):
        for k in range(30):
            self.words["m%d.wav" % k] = talk([(1.0, "x y z")])
        strips = [_Strip("ana", 'SOUND', "ana.wav", 1, 30)] + [
            _Strip("m%d" % k, 'SOUND', "m%d.wav" % k, 1, 5, channel=2 + k % 7, mute=True) for k in range(30)]
        scene = self.scene(strips)
        self.ops._tool_scene = lambda: scene
        self.mod.context_tokens = lambda: None
        out = self.mod.tool_read_edit_sentences()
        row = out["report"]["skipped"]["muted"]
        self.assertEqual(len(row["names"]), self.mod.MAX_NAMES)
        self.assertEqual(row["names_more"], 30 - self.mod.MAX_NAMES)
        self.assertEqual(row["strips"], 30)

    def test_context_tokens_does_not_ask_the_network_without_a_running_server(self):
        import importlib
        pkg = self.ops.__name__.rsplit(".", 1)[0]
        lm = importlib.import_module(pkg + ".llm_manager")
        calls = []
        old = lm.get_runtime_ctx
        lm.get_runtime_ctx = lambda *a, **k: calls.append(1) or 4096
        self.addCleanup(setattr, lm, "get_runtime_ctx", old)
        saved = lm._llama_process
        self.addCleanup(setattr, lm, "_llama_process", saved)
        lm._llama_process = None
        self.assertEqual(self.mod.context_tokens(), lm._config.local_ctx_size)
        lm._llama_process = types.SimpleNamespace(poll=lambda: 1)       # exited
        self.assertEqual(self.mod.context_tokens(), lm._config.local_ctx_size)
        self.assertEqual(calls, [])
        lm._llama_process = types.SimpleNamespace(poll=lambda: None)    # running
        self.assertEqual(self.mod.context_tokens(), 4096)


if __name__ == "__main__":
    unittest.main()
