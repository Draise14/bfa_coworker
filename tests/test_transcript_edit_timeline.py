# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Edit by Transcript (#498) on the timeline: the core's cuts and moves applied with
Remove Silences' split / ripple code (``wand/sequencer.py``: ``apply_cuts``, ``move_frames``) on the
stand-in strip model of the #539 tests (made-up words and strips only).

* Deleting a sentence takes its picture and sound out together, the music under it too (every
  track in step), closes the gap, drops the caption that was only over it, and the view rebuilt
  from the timeline no longer has those words.
* Moving a sentence to the front reorders picture, sound and its caption together; the edit keeps
  its length and nothing lands on another strip.
* A locked strip refuses before anything changes; the tools are listed in the vse domain.

Run with::

    python -m unittest tests.test_transcript_edit_timeline -v
"""

__all__ = ()

import unittest
from pathlib import Path

import test_remove_silences_whole_edit as RS
import test_transcript_edit as T
import test_wand_remove_silences as W

TE = T.TE
FPS = T.FPS
_ROOT = Path(__file__).resolve().parents[1]


def media_time(ed, path, kind, frame):
    for s in ed.items:
        if s.filepath == path and s.type == kind and s._left <= frame < s._right:
            return (frame - s._cs) / FPS
    return None


class _Edit(unittest.TestCase):

    def setUp(self):
        self.seq = W._load_sequencer(W._fake_bpy(False))
        self.ed = ed = RS.FakeEditor()
        end = 1 + 20 * FPS
        self.cam = ed.add("talk", "MOVIE", 2, 1, end, "talk.mp4")
        self.snd = ed.add("talk_snd", "SOUND", 1, 1, end, "talk.mp4")
        self.music = ed.add("Music", "SOUND", 3, 1, end, "music.wav")
        self.caps = []
        for n, (start, text) in enumerate(T.SENTENCES):
            first = T.indices(n)[0]
            last = T.indices(n)[-1]
            fs = 1 + int(round(T.WORDS[first]["start"] * FPS))
            fe = 1 + int(round(T.WORDS[last]["end"] * FPS)) + 6
            c = ed.add("cap_{:04d}".format(n + 1), "TEXT", 4, fs, fe, text=text)
            c["bfacw_caption"] = {"role": "line", "source": "media:talk.mp4"}
            self.caps.append(c)
        self.scene = RS._scene(ed, end - 1)

    def pieces(self):
        return [{"name": s.name, "left": s._left, "right": s._right, "content_start": s._cs, "offset": 0.0}
                for s in self.ed.items if s.type == "SOUND" and s.filepath == "talk.mp4"]

    def occ(self):
        return TE.timeline_words(T.WORDS, self.pieces(), FPS, 1)

    def assert_in_sync(self):
        end = max(s._right for s in self.ed.items)
        for f in range(1, end):
            a = media_time(self.ed, "talk.mp4", "MOVIE", f)
            b = media_time(self.ed, "talk.mp4", "SOUND", f)
            self.assertIsNotNone(a, f)
            self.assertAlmostEqual(a, b)
        for ch in (1, 2, 3, 4):
            spans = sorted((s._left, s._right) for s in self.ed.items if s.channel == ch)
            for x, y in zip(spans, spans[1:]):
                self.assertLessEqual(x[1], y[0])


class TestCutBySentence(_Edit):

    def test_delete_a_sentence(self):
        occ = self.occ()
        v = TE.render(occ, T.WORDS, None, FPS)
        text = T.drop_line(v["text"], T.line_with(v["text"], "The price"))
        edit = TE.compute_edit(v["tokens"], v["order"], text, T.WORDS, base_lines=v["lines"])
        frames = TE.cut_ranges(occ, edit["cut"], T.WORDS, self.pieces(), FPS)
        before_end = max(s._right for s in self.ed.items)
        out = self.seq.apply_cuts(self.scene, frames, True, True, "Edit by Transcript")
        self.assertEqual(out["status"], "ok", out)
        removed = sum(b - a for a, b in frames)
        self.assertEqual(max(s._right for s in self.ed.items), before_end - removed)       # the gap closed
        self.assertEqual(self.ed.shuffled, 0)
        self.assert_in_sync()
        self.assertEqual(sum(s._right - s._left for s in self.ed.items if s.filepath == "music.wav"),
                         before_end - 1 - removed)                                          # the music too
        left = TE.timeline_words(T.WORDS, self.pieces(), FPS, 1)
        self.assertEqual([o["i"] for o in left], [i for i in range(len(T.WORDS)) if i not in T.indices(2)])
        self.assertIn("cap_0003", out["captions_dropped"])                     # only over the sentence: gone
        self.assertEqual(sorted(s.name for s in self.ed.items if s.type == "TEXT"),
                         ["cap_0001", "cap_0002", "cap_0004", "cap_0005"])
        again = TE.render(left, T.WORDS, None, FPS)
        self.assertNotIn("price", again["text"])
        self.assertEqual(out["new_names"]["talk"][0], "talk")                  # the name stays on the first piece

    def test_locked_strip_refuses_and_nothing_changes(self):
        self.music.lock = True
        occ = self.occ()
        before = sorted((s.name, s._left, s._right) for s in self.ed.items)
        frames = TE.cut_ranges(occ, T.indices(2), T.WORDS, self.pieces(), FPS)
        out = self.seq.apply_cuts(self.scene, frames, True, True, "Edit by Transcript")
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["refused"][0]["strip"], "Music")
        self.assertIn("is locked", out["message"])
        self.assertEqual(sorted((s.name, s._left, s._right) for s in self.ed.items), before)
        self.assertEqual(self.seq.blocked_in(self.scene, 1, 50), [("Music", "is locked")])

    def test_first_and_last_sentence(self):
        occ = self.occ()
        frames = TE.cut_ranges(occ, T.indices(0) + T.indices(4), T.WORDS, self.pieces(), FPS)
        out = self.seq.apply_cuts(self.scene, frames, True, True, "Edit by Transcript")
        self.assertEqual(out["status"], "ok", out)
        self.assert_in_sync()
        left = [o["i"] for o in TE.timeline_words(T.WORDS, self.pieces(), FPS, 1)]
        self.assertEqual(left, T.indices(1) + T.indices(2) + T.indices(3))
        self.assertEqual(min(s._left for s in self.ed.items), 1)


class TestMoveBySentence(_Edit):

    def test_move_a_sentence_to_the_front(self):
        occ = self.occ()
        v = TE.render(occ, T.WORDS, None, FPS)
        lines = v["text"].splitlines()
        moved = lines.pop(T.line_with(v["text"], "The price"))
        lines.insert(0, moved)
        edit = TE.compute_edit(v["tokens"], v["order"], "\n".join(lines), T.WORDS)
        self.assertTrue(edit["moved"])
        before_end = max(s._right for s in self.ed.items)
        for _step in range(5):
            occ = self.occ()
            current = [o["i"] for o in occ]
            blocks = TE.plan_moves(current, edit["order"])
            if not blocks:
                break
            block, after = blocks[0]
            rng = TE.move_ranges(occ, block, after, T.WORDS, self.pieces(), FPS)
            self.assertIsNotNone(rng)
            out = self.seq.move_frames(self.scene, *rng)
            self.assertEqual(out["status"], "ok", out)
        self.assertEqual([o["i"] for o in self.occ()], edit["order"])
        self.assertEqual(max(s._right for s in self.ed.items), before_end)            # same length
        self.assertEqual(self.ed.shuffled, 0)
        self.assert_in_sync()
        cap = next(s for s in self.ed.items if s.name == "cap_0003")
        first_word = self.occ()[0]
        self.assertLessEqual(cap._left, first_word["frame"])                           # its caption came along
        self.assertGreater(cap._right, first_word["frame"])

    def test_move_refuses_over_a_locked_strip(self):
        self.cam.lock = True
        occ = self.occ()
        rng = TE.move_ranges(occ, T.indices(2), None, T.WORDS, self.pieces(), FPS)
        before = sorted((s.name, s._left, s._right) for s in self.ed.items)
        out = self.seq.move_frames(self.scene, *rng)
        self.assertEqual(out["status"], "refused")
        self.assertEqual(sorted((s.name, s._left, s._right) for s in self.ed.items), before)

    def test_move_inside_itself_is_nothing(self):
        self.assertEqual(self.seq.move_frames(self.scene, 10, 20, 15)["status"], "nothing")


class TestTools(unittest.TestCase):

    def test_tools_in_the_vse_domain(self):
        text = (_ROOT / "mcp" / "blmcp" / "tool_domains.py").read_text(encoding="utf-8")
        self.assertIn('"get_transcript_text", "apply_transcript_edit"', text)
        # #651: the loop reads this same file (tests/test_tool_domains_one_source.py).
        docs = (_ROOT / "addon" / "bfa_coworker" / "skills" / "mcp_tools.md").read_text(encoding="utf-8")
        self.assertIn("apply_transcript_edit(", docs)

    def test_toolcode_finds_nothing_outside_blender(self):
        import importlib.util
        for name in ("get_transcript_text_toolcode", "apply_transcript_edit_toolcode"):
            spec = importlib.util.spec_from_file_location(name, _ROOT / "mcp" / "blmcp" / "tools" / (name + ".py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            out = mod.main(mod.Params())
            self.assertEqual(out.status, "error")
            self.assertIn("isn't loaded", out.message)

    def test_open_transcript_is_in_the_video_section(self):
        seq = W._load_sequencer(W._fake_bpy(False))
        self.assertIn(("bfacw.open_transcript", "BFACW_OT_open_transcript", "Edit Transcript", "transcribe", "TEXT"),
                      seq.OTHERS)
        self.assertIn("bfacw.open_transcript", seq.NO_DIALOG)


if __name__ == "__main__":
    unittest.main()
