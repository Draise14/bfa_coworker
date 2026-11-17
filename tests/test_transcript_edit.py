# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Edit by Transcript (#498), the bpy-free core ``transcript_edit.py`` (made-up words only).

1. The view: which words each strip shows, lines per sentence with a time mark, the parser
   ignoring marks that were edited or deleted.
2. The edit: cut (a sentence, a word, the first and the last line), retext (a typo,
   punctuation, a merged / split word), move (a line to another place), mixed edits, repeated
   words, text that no longer matches (flagged, nothing cut), typed text with no audio behind it.
3. The timeline: cut frames in the pauses with padding, at a strip's edges, over several strips;
   moves as blocks of frames; filler words; the record next to the transcript.

Run with::

    python -m unittest tests.test_transcript_edit -v
"""

__all__ = ()

import json
import os
import tempfile
import unittest

import test_wand_remove_silences as W

TE = W._import("transcript_edit")
FPS = 30

SENTENCES = [
    (1.0, "Welcome to the little test clip."),
    (6.0, "We talk about apples and pears today."),
    (10.0, "The price of pears went up again."),
    (14.0, "Um, that is all for now."),
    (17.0, "Thanks for watching the the show."),
]


def made_up_words(sentences=SENTENCES, step=0.4, length=0.3):
    out = []
    for start, text in sentences:
        t = start
        for w in text.split():
            out.append({"w": w, "start": round(t, 3), "end": round(t + length, 3)})
            t += step
    return out


WORDS = made_up_words()


def piece(name="talk", left=1, right=1 + 20 * FPS, cs=1.0, offset=0.0):
    return {"name": name, "left": left, "right": right, "content_start": cs, "offset": offset}


def view(words=WORDS, pieces=None, retext=None):
    occ = TE.timeline_words(words, pieces or [piece()], FPS)
    return occ, TE.render(occ, words, retext, FPS)


def lines_of(text):
    return text.splitlines()


def drop_line(text, n):
    lines = lines_of(text)
    del lines[n]
    return "\n".join(lines) + "\n"


def line_with(text, words):
    return next(n for n, ln in enumerate(lines_of(text)) if words in ln)


def indices(sentence_no):
    """The word indices of SENTENCES[sentence_no]."""
    first = sum(len(s.split()) for _t, s in SENTENCES[:sentence_no])
    return list(range(first, first + len(SENTENCES[sentence_no][1].split())))


def edit(text, v, words=WORDS, retext=None):
    return TE.compute_edit(v["tokens"], v["order"], text, words, retext, base_lines=v["lines"])


# -- 1. the view ------------------------------------------------------------------------------------

class TestView(unittest.TestCase):

    def test_one_line_per_sentence_with_a_time_mark(self):
        _occ, v = view()
        lines = lines_of(v["text"])
        self.assertEqual(len([ln for ln in lines if ln]), 5)
        self.assertEqual(lines[0], "[0:01.0] Welcome to the little test clip.")
        self.assertTrue(lines[2].startswith("[0:06.0] We talk"))
        self.assertEqual(v["lines"][0]["frame"], 1 + 30)
        self.assertEqual(v["order"], list(range(len(WORDS))))

    def test_long_pause_makes_a_paragraph(self):
        _occ, v = view()
        self.assertEqual(lines_of(v["text"])[1], "")       # 3.3 s ... 6.0 s: a pause over 2 s
        self.assertNotEqual(lines_of(v["text"])[3], "")    # 8.7 s ... 10.0 s: a new line only

    def test_marks_are_not_words(self):
        toks = TE.parse("[0:01.0] Hello there\n[0:0x.1] still text\n[] plain\nno mark at all\n")
        self.assertEqual([t for _n, t in toks], ["Hello", "there", "[0:0x.1]", "still", "text", "plain",
                                                 "no", "mark", "at", "all"])
        self.assertEqual(TE.parse_mark("[1:02.5] x"), 62.5)
        self.assertEqual(TE.parse_mark("[1:00:02.0] x"), 3602.0)
        self.assertIsNone(TE.parse_mark("Hello"))
        self.assertEqual(TE.format_mark(3725.04), "[1:02:05.0]")

    def test_words_follow_the_strip_they_play_in(self):
        # the clip cut in two: 0-7 s at frame 1, 10-20 s right after it; the words of 7-10 s are gone
        p1 = piece("talk", 1, 211, cs=1.0)
        p2 = piece("talk.001", 211, 211 + 300, cs=1.0 - 90)
        occ = TE.timeline_words(WORDS, [p2, p1], FPS)
        self.assertEqual([o["piece"] for o in occ][:3], ["talk"] * 3)
        gone = set(indices(1)) - {o["i"] for o in occ}
        self.assertTrue(gone)                               # the second sentence (6.0-8.7 s) is half gone
        self.assertEqual([o["frame"] for o in occ], sorted(o["frame"] for o in occ))

    def test_offset_and_trim(self):
        p = piece("talk", 61, 200, cs=1.0, offset=0.0)      # trimmed: shows 2-6.6 s
        occ = TE.timeline_words(WORDS, [p], FPS)
        self.assertTrue(all(61 <= o["frame"] < 200 for o in occ))
        self.assertNotIn(0, [o["i"] for o in occ])

    def test_unchanged_text_is_no_edit(self):
        _occ, v = view()
        e = edit(v["text"], v)
        self.assertFalse(e["changed"])
        self.assertEqual(e["flags"], [])

    def test_edited_or_deleted_marks_change_nothing(self):
        _occ, v = view()
        lines = lines_of(v["text"])
        lines[0] = lines[0].replace("[0:01.0] ", "")
        lines[2] = lines[2].replace("[0:06.0]", "[9:99.9]")
        e = edit("\n".join(lines), v)
        self.assertFalse(e["changed"], e)
        self.assertEqual(e["flags"], [])


# -- 2. the edit -------------------------------------------------------------------------------------

class TestEdit(unittest.TestCase):

    def test_delete_a_sentence_cuts_its_words(self):
        _occ, v = view()
        e = edit(drop_line(v["text"], line_with(v["text"], "The price")), v)
        self.assertEqual(e["cut"], indices(2))
        self.assertEqual(e["retexted"], [])
        self.assertFalse(e["moved"])
        self.assertEqual(e["flags"], [])
        self.assertEqual(e["ops"], [{"op": "cut", "words": [indices(2)[0], indices(2)[-1]], "count": 7,
                                     "seconds": 3.42, "text": "The price of pears went up again."}])
        self.assertEqual(e["seconds"], 3.42)
        self.assertFalse(e["needs_confirm"])

    def test_delete_one_word(self):
        _occ, v = view()
        e = edit(v["text"].replace("little ", ""), v)
        self.assertEqual(e["cut"], [3])

    def test_delete_first_and_last_line(self):
        _occ, v = view()
        lines = lines_of(v["text"])
        e = edit("\n".join(lines[1:-1]), v)
        self.assertEqual(e["cut"], indices(0) + indices(4))

    def test_typo_is_retext_not_a_cut(self):
        _occ, v = view()
        e = edit(v["text"].replace("apples", "aples"), v)
        self.assertEqual(e["cut"], [])
        self.assertEqual(e["retexted"], [indices(1)[3]])
        self.assertEqual(e["retext"], {str(indices(1)[3]): "aples"})
        self.assertEqual(e["ops"], [{"op": "retext", "word": indices(1)[3], "from": "apples", "to": "aples"}])

    def test_punctuation_and_case_are_retext(self):
        _occ, v = view()
        e = edit(v["text"].replace("clip.", "clip!").replace("Welcome", "WELCOME"), v)
        self.assertEqual(e["cut"], [])
        self.assertEqual(sorted(e["retexted"]), [0, 5])

    def test_merge_and_split_words_keep_their_time(self):
        _occ, v = view()
        e = edit(v["text"].replace("went up", "wentup"), v)
        self.assertEqual(e["cut"], [])
        a, b = indices(2)[4], indices(2)[5]
        self.assertEqual(e["retext"], {str(a): "wentup", str(b): ""})
        self.assertIn(b, e["order"])                        # joined, not cut
        e2 = edit(v["text"].replace("today.", "to day."), v)
        self.assertEqual(e2["cut"], [])
        self.assertEqual(e2["retext"], {str(indices(1)[-1]): "to day."})

    def test_retext_survives_the_next_edit(self):
        occ, v = view()
        e = edit(v["text"].replace("went up", "wentup"), v)
        v2 = TE.render(occ, WORDS, e["retext"], FPS)
        self.assertIn("wentup again.", v2["text"])
        again = TE.compute_edit(v2["tokens"], v2["order"], v2["text"], WORDS, e["retext"], base_lines=v2["lines"])
        self.assertFalse(again["changed"])
        # cutting the joined word's owner cuts both
        e3 = TE.compute_edit(v2["tokens"], v2["order"], v2["text"].replace("wentup ", ""), WORDS, e["retext"],
                             base_lines=v2["lines"])
        self.assertEqual(sorted(e3["cut"]), [indices(2)[4], indices(2)[5]])
        self.assertEqual(e3["retext"], {})

    def test_typed_text_with_no_audio_is_flagged_and_not_cut(self):
        _occ, v = view()
        lines = lines_of(v["text"])
        lines.insert(3, "And here is a brand new sentence nobody said.")
        e = edit("\n".join(lines), v)
        self.assertFalse(e["changed"])
        self.assertEqual(e["cut"], [])
        self.assertEqual(len(e["flags"]), 1)
        self.assertEqual(e["flags"][0]["line"], 3)
        self.assertEqual(e["flags"][0]["reason"], "no_audio")
        self.assertTrue(e["flags"][0]["text"].startswith("And here"))

    def test_rewritten_line_is_flagged_and_its_words_stay(self):
        _occ, v = view()
        lines = lines_of(v["text"])
        lines[4] = "[0:08.0] Costs climbed sharply over winter."
        e = edit("\n".join(lines), v)
        self.assertEqual(e["cut"], [])
        self.assertEqual(e["retexted"], [])
        self.assertEqual([f["reason"] for f in e["flags"]], ["no_match"])
        self.assertEqual(e["flags"][0]["line"], 4)
        self.assertEqual(e["order"], list(range(len(WORDS))))

    def test_text_that_no_longer_matches_at_all_cuts_nothing(self):
        _occ, v = view()
        e = edit("Something else entirely, from another story.\nNothing like it.\n", v)
        self.assertEqual(e["cut"], [])
        self.assertFalse(e["changed"])
        self.assertTrue(e["flags"])

    def test_move_a_line(self):
        _occ, v = view()
        lines = lines_of(v["text"])
        moved = lines.pop(line_with(v["text"], "The price"))     # to the very start
        lines.insert(0, moved)
        e = edit("\n".join(lines), v)
        self.assertEqual(e["cut"], [])
        self.assertTrue(e["moved"])
        self.assertEqual(e["flags"], [])
        self.assertEqual(e["order"][:7], indices(2))
        moves = [op for op in e["ops"] if op["op"] == "move"]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["count"], 7)

    def test_mixed_cut_retext_and_move(self):
        _occ, v = view()
        lines = lines_of(v["text"])
        last = lines.pop()                                  # "Thanks for watching the the show." to the front
        lines.insert(0, last)
        text = "\n".join(lines).replace("little ", "").replace("pears today", "peers today")
        e = edit(text, v)
        self.assertEqual(e["cut"], [3])
        self.assertEqual(e["retext"], {str(indices(1)[5]): "peers"})
        self.assertTrue(e["moved"])
        self.assertEqual(e["order"][:6], indices(4))
        self.assertEqual(e["flags"], [])

    def test_one_of_two_repeated_words_is_flagged_not_cut(self):
        # "the the" -> "the": the text can't say which "the" went, so nothing is cut and the line is flagged
        _occ, v = view()
        e = edit(v["text"].replace("the the show", "the show"), v)
        self.assertEqual(e["cut"], [])
        self.assertFalse(e["changed"])
        self.assertEqual([(f["line"], f["reason"]) for f in e["flags"]], [(5, "ambiguous")])
        # both of them taken out is certain
        e = edit(v["text"].replace("the the ", ""), v)
        self.assertEqual(e["cut"], indices(4)[3:5])

    def test_delete_and_fix_a_typo_side_by_side(self):
        _occ, v = view()
        e = edit(v["text"].replace("apples and pears", "aples pears"), v)
        # "apples and" written as "aples": a block of 2 words as 1 is one retext of the block, the
        # words stay on the timeline ("and" is joined to "apples", not cut: nothing is cut by guessing)
        self.assertEqual(e["cut"], [])
        self.assertEqual(e["retext"], {str(indices(1)[3]): "aples", str(indices(1)[4]): ""})
        self.assertEqual(e["flags"], [])

    def test_filler_words(self):
        occ, v = view()
        found = TE.find_fillers(occ, WORDS)
        self.assertEqual([f["text"] for f in found], ["Um,"])
        self.assertEqual(TE.find_fillers(occ, WORDS[:5]), [])
        with_phrases = TE.find_fillers(TE.timeline_words(made_up_words([(1.0, "So you know it works.")]),
                                                         [piece()], FPS),
                                       made_up_words([(1.0, "So you know it works.")]), phrases=True)
        self.assertEqual([f["text"] for f in with_phrases], ["you know"])

    def test_remove_tokens_keeps_the_artists_own_edits(self):
        _occ, v = view()
        mine = v["text"].replace("apples", "aples")
        out = TE.remove_tokens(mine, v["tokens"], indices(3)[:1])     # "Um,"
        self.assertNotIn("Um,", out)
        self.assertIn("aples", out)
        self.assertIn("that is all for now.", out)
        e = edit(out, v)
        self.assertEqual(e["cut"], indices(3)[:1])


# -- 3. the timeline --------------------------------------------------------------------------------

class TestFrames(unittest.TestCase):

    def test_cut_sits_in_the_pauses_with_padding(self):
        occ, _v = view()
        frames = TE.cut_ranges(occ, indices(2), WORDS, [piece()], FPS, padding=0.12)
        self.assertEqual(len(frames), 1)
        f0, f1 = frames[0]
        last_kept_end = 1 + WORDS[indices(1)[-1]]["end"] * FPS
        first_cut = 1 + WORDS[indices(2)[0]]["start"] * FPS
        next_start = 1 + WORDS[indices(3)[0]]["start"] * FPS
        self.assertGreater(f0, last_kept_end)               # the kept word isn't clipped
        self.assertLessEqual(f0, first_cut)                 # the cut word goes whole
        self.assertLess(f1, next_start)                     # padding before the next word
        self.assertAlmostEqual(f0, first_cut - 0.12 * FPS, delta=1)
        # the pause after the sentence is 1.3 s: the cut takes 0.6 s of it, the rest stays
        last_cut_end = 1 + WORDS[indices(2)[-1]]["end"] * FPS
        self.assertAlmostEqual(f1, last_cut_end + TE.PAUSE_LIMIT_S * FPS, delta=1)
        self.assertGreater(next_start - f1, 0.5 * FPS)

    def test_short_pause_is_split_in_half(self):
        words = made_up_words([(1.0, "one two three four")], step=0.34, length=0.3)
        occ = TE.timeline_words(words, [piece()], FPS)
        f0, f1 = TE.cut_ranges(occ, [1], words, [piece()], FPS, padding=0.12)[0]
        self.assertAlmostEqual(f0, 1 + (words[1]["start"] - 0.02) * FPS, delta=1)
        self.assertAlmostEqual(f1, 1 + (words[2]["start"] - 0.02) * FPS, delta=1)

    def test_first_and_last_words_end_at_the_word_plus_padding_not_at_the_strip_edge(self):
        occ, _v = view()
        p = piece()
        frames = TE.cut_ranges(occ, indices(0) + indices(4), WORDS, [p], FPS)
        self.assertAlmostEqual(frames[0][0], 1 + (WORDS[0]["start"] - TE.PADDING_S) * FPS, delta=1)
        self.assertGreater(frames[0][0], p["left"])
        last = WORDS[indices(4)[-1]]
        self.assertAlmostEqual(frames[-1][1], 1 + (last["end"] + TE.PADDING_S) * FPS, delta=1)
        self.assertLess(frames[-1][1], p["right"])

    def test_cuts_over_two_strips(self):
        p1 = piece("talk", 1, 1 + 9 * FPS, cs=1.0)                  # 0-9 s
        p2 = piece("talk.001", 1 + 9 * FPS, 1 + 20 * FPS, cs=1.0)   # 9-20 s
        occ = TE.timeline_words(WORDS, [p1, p2], FPS)
        frames = TE.cut_ranges(occ, [indices(1)[-1], indices(2)[0]], WORDS, [p1, p2], FPS)
        # the last word of talk ends at the word plus padding, the first of talk.001 starts a padding
        # before it: two ranges, one in each strip, neither running to the strip's edge
        self.assertEqual(len(frames), 2)
        (a0, a1), (b0, b1) = frames
        self.assertLessEqual(a0, 1 + WORDS[indices(1)[-1]]["start"] * FPS)
        self.assertLess(a1, 1 + 9 * FPS)
        self.assertGreater(b0, 1 + 9 * FPS)
        self.assertGreater(b1, 1 + WORDS[indices(2)[0]]["end"] * FPS)

    def test_plan_moves(self):
        self.assertEqual(TE.plan_moves([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]), [])
        self.assertEqual(TE.plan_moves([1, 2, 3, 4, 5], [4, 5, 1, 2, 3]), [([4, 5], None)])
        self.assertEqual(TE.plan_moves([1, 2, 3, 4, 5], [1, 4, 2, 3, 5]), [([4], 1)])

    def test_move_ranges(self):
        occ, _v = view()
        block = indices(2)
        got = TE.move_ranges(occ, block, None, WORDS, [piece()], FPS)
        self.assertIsNotNone(got)
        first, end, to = got
        self.assertEqual(to, 1)
        self.assertLess(1 + WORDS[indices(1)[-1]]["end"] * FPS, first)
        self.assertLessEqual(first, 1 + WORDS[block[0]]["start"] * FPS)
        self.assertGreater(end, 1 + WORDS[block[-1]]["end"] * FPS)
        self.assertLess(end, 1 + WORDS[indices(3)[0]]["start"] * FPS)
        self.assertIsNone(TE.move_ranges(occ, [0, 2], None, WORDS, [piece()], FPS))   # not one stretch

    def test_caption_text_and_lines(self):
        occ, v = view()
        self.assertEqual(TE.caption_text(occ, WORDS, {"1": "into"}, 1 + 30, 1 + 30 + 24), "Welcome into")
        self.assertEqual(TE.line_at_frame(v["lines"], 1 + 6.5 * FPS), 2)
        self.assertEqual(TE.line_at_frame(v["lines"], 0), None)
        self.assertEqual(TE.line_of_word(v["lines"], indices(2)[3]), 3)

    def test_record_next_to_the_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = os.path.join(tmp, "0123456789abcdef.base.en.json")
            with open(transcript, "w", encoding="utf-8") as fh:
                json.dump({"words": []}, fh)
            path = TE.edits_path(transcript)
            self.assertEqual(path.name, "0123456789abcdef.base.en.edits.json")
            _occ, v = view()
            e = edit(drop_line(v["text"], line_with(v["text"], "The price")), v)
            rec = TE.edit_record(None, {"key": "0123456789abcdef"}, e, e["order"], when="2026-01-01T00:00:00")
            TE.write_record(path, rec)
            back = TE.read_record(path)
            self.assertEqual(back["schema"], TE.EDIT_SCHEMA)
            self.assertEqual(back["history"][0]["ops"][0]["op"], "cut")
            self.assertEqual(back["state"]["order"], e["order"])
            rec2 = TE.edit_record(back, {"key": "0123456789abcdef"}, edit(v["text"], v), e["order"])
            self.assertEqual(len(rec2["history"]), 1)      # nothing changed: nothing added
            with open(transcript, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"words": []})      # the transcript itself never changes

    def test_edits_file_is_not_taken_for_a_transcript(self):
        T = W._import("transcripts")
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, T.CACHE_FOLDER)
            os.makedirs(folder)
            TE.write_record(os.path.join(folder, "0123456789abcdef.base.en.edits.json"),
                            TE.edit_record(None, {}, {"ops": []}, []))
            self.assertIsNone(T.find_cached("0123456789abcdef", fallback_dir=tmp))

    def test_a_long_transcript_is_quick(self):
        import time
        sentences = [(i * 4.0, "This is sentence number {:d} of the long one.".format(i)) for i in range(400)]
        words = made_up_words(sentences)
        p = piece(right=1 + 1700 * FPS)
        t0 = time.perf_counter()
        occ = TE.timeline_words(words, [p], FPS)
        v = TE.render(occ, words, None, FPS)
        lines = v["text"].splitlines()
        del lines[100:110]
        e = TE.compute_edit(v["tokens"], v["order"], "\n".join(lines), words)
        TE.cut_ranges(occ, e["cut"], words, [p], FPS)
        self.assertLess(time.perf_counter() - t0, 3.0)
        self.assertGreater(len(e["cut"]), 0)


if __name__ == "__main__":
    unittest.main()
