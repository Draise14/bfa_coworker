# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the rule of Edit by Transcript (#498): **nothing is cut by guessing**.  A cut comes only
from text the user took out, mapped with certainty to the words it came from; everything
uncertain is flagged on its line and changes nothing (the review of #587 found nine ways the first
version cut footage the user kept).

1. One test per reviewed fault in the core (repeated takes, a move with an edit inside, a flagged
   line that still cut, a cut that took a whole pause, words dropped inside a rewritten block,
   delete everything) with the exact edit list.
2. Lines split with Enter or joined, a line copied, marks edited or deleted, apply twice, CRLF.
3. A property test (fixed seed, 2000+ cases): random transcripts with repeated words and
   identical sentences, one or two random operations on the text, compared with what an
   independent model of the operation says was removed.

Run with::

    python -m unittest tests.test_transcript_edit_rules -v
"""

__all__ = ()

import random
import re
import time
import unittest

import test_transcript_edit as T

TE = T.TE
FPS = T.FPS


def mk(sentences, step=0.4, right=1 + 400 * FPS):
    words = T.made_up_words(sentences, step=step)
    p = T.piece(right=right)
    occ = TE.timeline_words(words, [p], FPS)
    return words, occ, TE.render(occ, words, None, FPS), p


def ed(v, text, words, retext=None, timeline_s=None):
    return TE.compute_edit(v["tokens"], v["order"], text, words, retext, base_lines=v["lines"],
                           timeline_s=timeline_s)


def rows(v):
    return [ln for ln in v["text"].split("\n") if ln.strip()]


def text_of(lines):
    return "\n".join(lines) + "\n"


TAKES = [(1.0, "So today we talk about apples."), (5.0, "So today we talk about apples."),
         (9.0, "So today we talk about apples."), (13.0, "And then pears.")]


def take(n):
    return list(range(6 * n, 6 * n + 6))


# -- 1. one test per fault ---------------------------------------------------------------------------

class TestFaults(unittest.TestCase):

    def test_1_repeated_takes_cut_the_take_that_was_deleted(self):
        words, _occ, v, _p = mk(TAKES)
        lines = rows(v)
        self.assertEqual(len(set(ln.split("] ", 1)[0] for ln in lines)), 4)         # the marks differ
        for gone in (0, 1, 2):
            e = ed(v, text_of([ln for n, ln in enumerate(lines) if n != gone]), words)
            self.assertEqual(e["cut"], take(gone), "take %d" % (gone + 1))
            self.assertEqual(e["flags"], [])
            self.assertFalse(e["moved"])
            self.assertEqual([op["words"] for op in e["ops"]], [[6 * gone, 6 * gone + 5]])

    def test_1_repeated_takes_with_the_marks_gone_are_flagged_not_guessed(self):
        words, _occ, v, _p = mk(TAKES)
        bare = [TE.MARK_RE.sub("", ln, count=1) for ln in rows(v)]
        e = ed(v, text_of([ln for n, ln in enumerate(bare) if n != 1]), words)
        self.assertEqual(e["cut"], [])                       # which take? the text can't say
        self.assertEqual({f["reason"] for f in e["flags"]}, {"ambiguous"})
        self.assertEqual(sorted(f["line"] for f in e["flags"]), [0, 1])
        # a take whose mark was deleted but whose place is told by the lines around it is certain again
        lines = rows(v)
        lines[1] = TE.MARK_RE.sub("", lines[1], count=1)
        e = ed(v, text_of(lines), words)
        self.assertFalse(e["changed"])
        e = ed(v, text_of([lines[0], lines[2], lines[3]]), words)        # take 3 kept (marked), take 2 (bare) gone
        self.assertEqual((e["cut"], e["flags"]), (take(1), []))

    def test_1_the_second_of_three_okay_is_not_guessed(self):
        words, _occ, v, _p = mk([(1.0, "okay okay okay let us start.")])
        e = ed(v, v["text"].replace("okay okay okay", "okay okay", 1), words)
        self.assertEqual(e["cut"], [])
        self.assertEqual([(f["line"], f["reason"]) for f in e["flags"]], [(0, "ambiguous")])
        e = ed(v, v["text"].replace("okay okay okay ", "", 1), words)     # all three: certain
        self.assertEqual(e["cut"], [0, 1, 2])
        e = ed(v, v["text"].replace("okay okay okay ", "okay ", 1), words)     # two of three: still one is kept, which?
        self.assertEqual(e["cut"], [])
        self.assertEqual(len(e["flags"]), 1)

    def test_1_two_identical_sentences_apart_delete_the_second(self):
        words, _occ, v, _p = mk([(1.0, "Yes indeed."), (4.0, "Something else here."), (7.0, "Yes indeed.")])
        e = ed(v, text_of(rows(v)[:2]), words)
        self.assertEqual(e["cut"], [5, 6])                        # the second "Yes indeed."
        e = ed(v, text_of(rows(v)[1:]), words)
        self.assertEqual(e["cut"], [0, 1])
        lines = rows(v)
        e = ed(v, text_of([lines[2], lines[1], lines[0]]), words)         # a swap of identical lines is a move
        self.assertEqual((e["cut"], e["moved"], e["flags"]), ([], True, []))
        self.assertEqual(e["order"][:2], [5, 6])

    def test_2_a_move_with_a_small_edit_inside_cuts_the_right_word(self):
        words, _occ, v, _p = mk([(1.0, "Welcome to the little test clip."), (6.0, "We talk about apples and pears today."),
                                 (10.0, "The price of pears went up again sadly."), (14.0, "That is all for now.")])
        for gone, expect in (("of", 15), ("went", 17)):
            lines = rows(v)
            n = next(k for k, ln in enumerate(lines) if "price" in ln)
            moved = lines.pop(n).replace(" %s " % gone, " ")
            lines.insert(0, moved)
            e = ed(v, text_of(lines), words)
            self.assertEqual(e["cut"], [expect], gone)
            self.assertTrue(e["moved"])
            self.assertEqual(e["retext"], {})
            self.assertEqual(e["flags"], [])
            self.assertEqual(e["order"][:6], [w for w in range(13, 20) if w != expect][:6])
        # a typo inside a moved line: retext of that word only
        lines = rows(v)
        n = next(k for k, ln in enumerate(lines) if "apples" in ln)
        lines.insert(0, lines.pop(n).replace("apples", "aples"))
        e = ed(v, text_of(lines), words)
        self.assertEqual((e["cut"], e["retext"], e["moved"]), ([], {"9": "aples"}, True))

    def test_2_a_short_moved_line_with_a_word_deleted(self):
        words, _occ, v, _p = mk([(1.0, "Welcome to the clip."), (6.0, "We like red apples."), (10.0, "The price went up."),
                                 (14.0, "That is all.")])
        lines = rows(v)
        n = next(k for k, ln in enumerate(lines) if "apples" in ln)
        lines.insert(0, lines.pop(n).replace("red ", ""))
        e = ed(v, text_of(lines), words)
        self.assertEqual(e["cut"], [6])                                    # "red", nothing else
        self.assertTrue(e["moved"])

    def test_3_a_flagged_line_never_causes_a_cut(self):
        words, _occ, v, _p = mk([(1.0, "Welcome to the little test clip."), (6.0, "We talk about apples and pears today."),
                                 (10.0, "The price of pears went up again."), (14.0, "That is all for now.")])
        lines = rows(v)
        n = next(k for k, ln in enumerate(lines) if "apples" in ln)
        # moved, and a word added inside: flagged; its words stay, in place, and nothing is cut
        e = ed(v, text_of([lines[n].replace("apples", "red apples")] + lines[:n] + lines[n + 1:]), words)
        self.assertEqual((e["cut"], e["moved"], e["changed"]), ([], False, False))
        self.assertEqual([(f["line"], f["reason"], f["text"]) for f in e["flags"]], [(0, "no_audio", "red")])
        # moved with its mark deleted and a typo: it no longer matches exactly: flagged, not cut
        moved = TE.MARK_RE.sub("", lines[n], count=1).replace("pears", "peers").replace("talk", "tolk")
        e = ed(v, text_of([moved] + lines[:n] + lines[n + 1:]), words)
        self.assertEqual((e["cut"], e["changed"]), ([], False))
        self.assertEqual([(f["line"], f["reason"]) for f in e["flags"]], [(0, "no_match")])
        # ... with only one typo it is still that line (close enough), moved, with the one word changed
        near = TE.MARK_RE.sub("", lines[n], count=1).replace("pears", "peers")
        e = ed(v, text_of([near] + lines[:n] + lines[n + 1:]), words)
        self.assertEqual((e["cut"], e["moved"], e["retext"], e["flags"]), ([], True, {"11": "peers"}, []))
        # a line of new text where a line was deleted: the deleted words stay
        e = ed(v, text_of(lines[:n] + ["Something nobody said at all."] + lines[n + 1:]), words)
        self.assertEqual(e["cut"], [])
        # ... and typed somewhere else, the deleted line goes and the typed one is flagged
        e = ed(v, text_of(lines[:n] + lines[n + 1:] + ["Something nobody said at all."]), words)
        self.assertEqual(e["cut"], list(range(6, 13)))
        self.assertEqual([f["reason"] for f in e["flags"]], ["no_audio"])

    def test_4_a_cut_takes_a_limited_pause_not_a_silent_demo(self):
        sentences = [(1.0, "Hello and welcome."), (4.0, "Now watch this."), (35.0, "That was the demo.")]
        words, occ, v, p = mk(sentences, right=1 + 50 * FPS)
        e = ed(v, text_of([ln for ln in rows(v) if "watch" not in ln]), words)
        self.assertEqual(e["cut"], [3, 4, 5])
        (f0, f1), = TE.cut_ranges(occ, e["cut"], words, [p], FPS)
        self.assertLess((f1 - f0) / FPS, 2.0)                     # was 31 s: words 1.1 s + padding + 0.6 s of the pause
        self.assertAlmostEqual(f0, 1 + (4.0 - TE.PADDING_S) * FPS, delta=1)
        self.assertAlmostEqual(f1, 1 + (4.8 + 0.3 + TE.PAUSE_LIMIT_S) * FPS, delta=2)      # "this." ends at 5.1 s
        self.assertAlmostEqual(e["seconds"], (f1 - f0) / FPS, delta=0.1)
        # the last line, the strip going on to 50 s: the cut ends at the words plus padding, not at the edge
        e = ed(v, text_of([ln for ln in rows(v) if "demo" not in ln]), words)
        (g0, g1), = TE.cut_ranges(occ, e["cut"], words, [p], FPS)
        self.assertLess((g1 - g0) / FPS, 3.0)
        self.assertLess(g1, p["right"])

    def test_4_a_sentence_before_ten_seconds_of_silence_keeps_most_of_it(self):
        words, occ, v, p = mk([(1.0, "Take a look at this."), (4.0, "Now watch."), (15.0, "That was it.")], right=1 + 30 * FPS)
        e = ed(v, text_of([ln for ln in rows(v) if "watch" not in ln]), words)
        (f0, f1), = TE.cut_ranges(occ, e["cut"], words, [p], FPS)
        silence = 15.0 - (4.4 + 0.3)                                # the pause after "watch."
        self.assertGreater(silence - (f1 - f0) / FPS + 0.8, 8.0)    # most of the 10 s stay
        self.assertLess((f1 - f0) / FPS, 2.5)

    def test_4_short_pauses_are_taken_whole_up_to_the_padding(self):
        words = T.made_up_words([(1.0, "one two three four")], step=0.5, length=0.3)
        occ = TE.timeline_words(words, [T.piece()], FPS)
        (f0, f1), = TE.cut_ranges(occ, [1], words, [T.piece()], FPS, padding=0.12)
        self.assertAlmostEqual(f0, 1 + (words[1]["start"] - 0.1) * FPS, delta=1)      # half of the 0.2 s pause
        self.assertAlmostEqual(f1, 1 + (words[2]["start"] - 0.1) * FPS, delta=1)

    def test_5_words_dropped_inside_a_rewritten_block_are_not_cut(self):
        words, _occ, v, _p = mk([(1.0, "I think it is very good."), (5.0, "We are going to win.")])
        e = ed(v, v["text"].replace("it is very", "it's"), words)
        self.assertEqual(e["cut"], [])
        self.assertEqual(e["retext"], {"2": "it's", "3": "", "4": ""})          # the block is one retext
        self.assertEqual(e["order"], list(range(len(words))))                    # every word stays
        self.assertEqual(e["flags"], [])
        e = ed(v, v["text"].replace("going to", "gonna"), words)
        self.assertEqual((e["cut"], e["retext"]), ([], {"8": "gonna", "9": ""}))

    def test_5_a_paraphrase_of_the_whole_text_cuts_nothing(self):
        words, _occ, v, _p = mk(T.SENTENCES)
        e = ed(v, "We talk about the price of pears.\n", words)
        self.assertEqual(e["cut"], [])
        self.assertEqual([f["reason"] for f in e["flags"]], ["no_match"])
        # every line rewritten, its mark kept: each line is flagged and left alone
        lines = [TE.MARK_RE.match(ln).group(0) + "Costs climbed sharply over winter now." for ln in rows(v)]
        e = ed(v, text_of(lines), words)
        self.assertEqual(e["cut"], [])
        self.assertEqual(len(e["flags"]), 5)
        self.assertFalse(e["changed"])

    def test_9_delete_everything_asks_first(self):
        words, _occ, v, _p = mk(T.SENTENCES)
        e = ed(v, "", words)
        self.assertEqual(e["cut"], list(range(len(words))))
        self.assertTrue(e["needs_confirm"])
        self.assertEqual(e["fraction"], 1.0)
        e = ed(v, text_of(rows(v)[:1]), words)                      # four of five lines gone
        self.assertTrue(e["needs_confirm"])
        e = ed(v, text_of(rows(v)[:-1]), words)                     # one line gone: no question
        self.assertFalse(e["needs_confirm"])
        self.assertLess(e["fraction"], 0.5)
        e = ed(v, text_of(rows(v)[:-1]), words, timeline_s=2.0)     # ... unless it is most of the time
        self.assertTrue(e["needs_confirm"])

    def test_edit_list_carries_seconds(self):
        words, _occ, v, _p = mk(T.SENTENCES)
        e = ed(v, text_of([ln for n, ln in enumerate(rows(v)) if n != 2]), words)
        self.assertEqual(e["ops"][0]["seconds"], 3.42)
        self.assertEqual(e["seconds"], 3.42)


# -- 2. lines, marks, copies, noise -----------------------------------------------------------------

class TestLines(unittest.TestCase):

    def setUp(self):
        self.words, self.occ, self.v, _p = mk(T.SENTENCES)
        self.lines = rows(self.v)

    def edit(self, text):
        return ed(self.v, text, self.words)

    def test_enter_in_the_middle_of_a_line_changes_nothing(self):
        lines = list(self.lines)
        a, b = lines[1].split(" about ", 1)
        lines[1:2] = [a, "about " + b]
        e = self.edit(text_of(lines))
        self.assertEqual((e["cut"], e["changed"], e["flags"]), ([], False, []))

    def test_enter_and_a_word_deleted_in_the_second_half(self):
        lines = list(self.lines)
        a, b = lines[1].split(" about ", 1)
        lines[1:2] = [a, ("about " + b).replace("pears ", "")]
        e = self.edit(text_of(lines))
        self.assertEqual(e["cut"], [11])
        self.assertEqual(e["flags"], [])

    def test_enter_and_the_second_half_deleted(self):
        lines = list(self.lines)
        a, _b = lines[1].split(" about ", 1)
        e = self.edit(text_of(lines[:1] + [a] + lines[2:]))
        self.assertEqual(e["cut"], list(range(8, 13)))

    def test_two_lines_joined(self):
        lines = list(self.lines)
        e = self.edit(text_of(lines[:1] + [lines[1] + " " + TE.MARK_RE.sub("", lines[2], count=1)] + lines[3:]))
        self.assertEqual((e["cut"], e["changed"], e["flags"]), ([], False, []))

    def test_two_lines_joined_and_a_word_deleted(self):
        lines = list(self.lines)
        joined = lines[1] + " " + TE.MARK_RE.sub("", lines[2], count=1)
        e = self.edit(text_of(lines[:1] + [joined.replace("price ", "")] + lines[3:]))
        self.assertEqual(e["cut"], [14])
        self.assertEqual(e["flags"], [])

    def test_a_split_line_with_its_mark_deleted_is_flagged_not_cut(self):
        lines = list(self.lines)
        a, b = TE.MARK_RE.sub("", lines[1], count=1).split(" about ", 1)
        e = self.edit(text_of(lines[:1] + [a, "about " + b] + lines[2:]))
        self.assertEqual(e["cut"], [])
        self.assertEqual(len(e["flags"]), 2)

    def test_typed_words_after_a_line_are_flagged(self):
        lines = list(self.lines)
        lines[1:2] = [lines[1], "and then something nobody said"]
        e = self.edit(text_of(lines))
        self.assertEqual(e["cut"], [])
        self.assertEqual([(f["line"], f["reason"]) for f in e["flags"]], [(2, "no_audio")])

    def test_a_copied_line_is_flagged_and_the_original_stays(self):
        lines = list(self.lines)
        e = self.edit(text_of(lines + [lines[0]]))           # same mark, same words: one of them is the copy
        self.assertEqual(e["cut"], [])
        self.assertTrue(e["flags"])
        e = self.edit(text_of(lines[:1] + [TE.MARK_RE.sub("", lines[0], count=1)] + lines[1:]))     # copy without its mark, next to it
        self.assertEqual(e["cut"], [])
        e = self.edit(text_of(lines + [TE.MARK_RE.sub("", lines[0], count=1)]))     # far away: only the far one is the copy
        self.assertEqual((e["cut"], e["changed"]), ([], False))
        self.assertEqual([f["line"] for f in e["flags"]], [5])

    def test_an_edited_mark_is_no_harm(self):
        lines = list(self.lines)
        lines[2] = lines[2].replace("[0:06.0]", "[9:59.9]")
        e = self.edit(text_of(lines))
        self.assertEqual((e["changed"], e["flags"]), (False, []))
        lines[0] = lines[0].replace("[0:01.0]", "[0:01.0 ]")      # spaces in the mark: still its mark
        self.assertFalse(self.edit(text_of(lines))["changed"])

    def test_a_line_that_starts_with_a_bracket_word(self):
        words, _occ, v, _p = mk([(1.0, "[1] first point here."), (5.0, "Next.")])
        e = ed(v, v["text"].replace("[0:01.0] ", "", 1), words)
        self.assertEqual((e["cut"], e["changed"], e["flags"]), ([], False, []))

    def test_apply_twice_is_a_no_op(self):
        e = self.edit(text_of(self.lines[:1] + self.lines[2:]))
        self.assertTrue(e["changed"])
        again = self.edit(self.v["text"])
        self.assertEqual((again["changed"], again["cut"], again["ops"], again["flags"]), (False, [], [], []))
        self.assertFalse(again["needs_confirm"])

    def test_crlf_trailing_spaces_and_punctuation(self):
        crlf = self.v["text"].replace("\n", "  \r\n")
        e = self.edit(crlf)
        self.assertEqual((e["changed"], e["flags"]), (False, []))
        e = self.edit(self.v["text"].replace("today.", "today!").replace("Welcome", "Welcome,"))
        self.assertEqual((e["cut"], sorted(e["retexted"])), ([], [0, 12]))
        e = self.edit(self.v["text"].replace("\n", "\r"))
        self.assertFalse(e["changed"])

    def test_marks_are_all_different(self):
        words = T.made_up_words([(1.0, "One."), (1.02, "Two."), (1.04, "Three."), (1.06, "Four.")], step=0.001, length=0.0005)
        occ = TE.timeline_words(words, [T.piece()], FPS)
        v = TE.render(occ, words, None, FPS)
        marks = [ln.split("] ", 1)[0] for ln in v["text"].splitlines()]
        self.assertEqual(len(set(marks)), len(marks))
        self.assertEqual(marks[0], "[0:01.0")
        self.assertEqual(len({ln["mark"] for ln in v["lines"]}), len(v["lines"]))

    def test_line_words_follow_the_lines(self):
        lines = list(self.lines)
        got = TE.line_words(self.v["tokens"], text_of([lines[2], lines[0]]), self.v["lines"], self.v["order"])
        self.assertEqual(got[0], list(range(13, 20)))
        self.assertEqual(got[1], list(range(0, 6)))

    def test_edit_record_keeps_the_last_fifty(self):
        rec = None
        for k in range(60):
            rec = TE.edit_record(rec, {}, {"ops": [{"op": "cut", "n": k}], "retext": {}}, [], when=str(k))
        self.assertEqual(len(rec["history"]), TE.HISTORY_LIMIT)
        self.assertEqual(rec["history"][-1]["time"], "59")
        self.assertEqual(rec["history"][0]["time"], "10")


# -- 3. the property test ---------------------------------------------------------------------------

VOCAB = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "kilo", "lima",
         "the", "and", "we", "is"]
OPS = ("delete_line", "delete_word", "swap", "move", "letter", "delete_mark", "join", "split", "insert")


def _transcript(rng):
    while True:
        sentences = []
        for _ in range(rng.randint(5, 9)):
            n = rng.randint(3, 7)
            toks = [rng.choice(VOCAB) for _ in range(n)]
            if rng.random() < 0.3:
                toks[rng.randrange(n)] += ","
            toks[-1] += "."
            sentences.append(toks)
        sentences.insert(rng.randrange(len(sentences) + 1), list(rng.choice(sentences)))      # an identical pair
        if rng.random() < 0.4:
            sentences.insert(rng.randrange(len(sentences) + 1), list(rng.choice(sentences)))  # or a third take
        total = sum(len(s) for s in sentences)
        if 20 <= total <= 60:
            break
    t, timed = 1.0, []
    for toks in sentences:
        timed.append((t, " ".join(toks)))
        t += len(toks) * 0.4 + rng.uniform(0.3, 1.4)
    return timed


class _Model:
    """The rendered text as lines of ``(token, word it came from or None)``, and the operations a user does."""

    def __init__(self, v):
        self.lines = []
        for info in v["lines"]:
            mark = "[" + info["mark"].strip("[]") + "]"
            toks = [(t, w) for w, t in v["tokens"][info["first"]:info["first"] + info["count"]]]
            self.lines.append({"mark": mark, "toks": toks})
        self.base = [[(t, w) for t, w in ln["toks"]] for ln in self.lines]
        self.made = 0

    def text(self):
        out = []
        for ln in self.lines:
            body = " ".join(t for t, _w in ln["toks"])
            out.append(((ln["mark"] + " ") if ln["mark"] else "") + body)
        return "\n".join(out) + "\n"

    def truth_order(self):
        seen, out = set(), []
        for ln in self.lines:
            for _t, w in ln["toks"]:
                if w is not None and w not in seen:
                    seen.add(w)
                    out.append(w)
        return out

    def apply(self, rng, op):
        lines = self.lines
        n = len(lines)
        if op == "delete_line" and n > 1:
            del lines[rng.randrange(n)]
        elif op == "delete_word":
            k = rng.choice([k for k in range(n) if lines[k]["toks"]] or [None]) if n else None
            if k is None:
                return False
            del lines[k]["toks"][rng.randrange(len(lines[k]["toks"]))]
        elif op == "swap" and n > 1:
            a, b = rng.sample(range(n), 2)
            lines[a], lines[b] = lines[b], lines[a]
        elif op == "move" and n > 1:
            k = rng.randrange(n)
            line = lines.pop(k)
            p = rng.choice([q for q in range(n) if q != k])
            lines.insert(p, line)
        elif op == "letter":
            spots = [(k, j) for k in range(n) for j, (t, w) in enumerate(lines[k]["toks"])
                     if w is not None and len(re.sub(r"\W", "", t)) >= 5 and t == self.original(w)]
            if not spots:
                return False
            k, j = rng.choice(spots)
            t, w = lines[k]["toks"][j]
            i = t.index(next(c for c in t[1:-1] if c.isalpha()))
            lines[k]["toks"][j] = (t[:i] + ("z" if t[i] != "z" else "q") + t[i + 1:], w)
        elif op == "delete_mark":
            marked = [k for k in range(n) if lines[k]["mark"]]
            if not marked:
                return False
            lines[rng.choice(marked)]["mark"] = None
        elif op == "join" and n > 1:
            k = rng.randrange(n - 1)
            lines[k]["toks"] = lines[k]["toks"] + lines[k + 1]["toks"]
            del lines[k + 1]
        elif op == "split":
            cands = [k for k in range(n) if len(lines[k]["toks"]) >= 2]
            if not cands:
                return False
            k = rng.choice(cands)
            j = rng.randrange(1, len(lines[k]["toks"]))
            tail = lines[k]["toks"][j:]
            lines[k]["toks"] = lines[k]["toks"][:j]
            lines.insert(k + 1, {"mark": None, "toks": tail})
        elif op == "insert":
            self.made += 1
            toks = [("zq%dx%d" % (self.made, i), None) for i in range(rng.randint(3, 5))]
            lines.insert(rng.randint(0, n), {"mark": None, "toks": toks})
        else:
            return False
        return True

    def original(self, w):
        for toks in self.base:
            for t, ww in toks:
                if ww == w:
                    return t
        return None


def _applied_view(e, words):
    """The view after the edit is applied: the words in the new order, one after another."""
    occ = [{"i": w, "piece": "x", "frame": 31 + 10 * k, "end_frame": 34 + 10 * k, "t": (30 + 10 * k) / FPS}
           for k, w in enumerate(e["order"])]
    return TE.render(occ, words, e["retext"], FPS)


def _ambiguous_delete(model):
    """Words were taken out of a line so that the text can't tell which: by brute force, more than
    one choice of that many words of the line leaves the same words."""
    import itertools
    gone = {w for toks in model.base for _t, w in toks if w is not None} - set(model.truth_order())
    for toks in model.base:
        norms = [TE.norm(t) for t, _w in toks]
        out = [i for i, (_t, w) in enumerate(toks) if w in gone]
        if not out or len(out) == len(toks):
            continue
        left = [norms[i] for i in range(len(toks)) if i not in out]
        same = sum(1 for pick in itertools.combinations(range(len(toks)), len(out))
                   if [norms[i] for i in range(len(toks)) if i not in pick] == left)
        if same > 1:
            return True
    return False


class TestProperty(unittest.TestCase):

    CASES = 2400

    def test_random_edits_never_cut_what_the_user_kept(self):
        started = time.perf_counter()
        stats = self.run_cases(498, self.CASES)
        took = time.perf_counter() - started
        self.assertGreaterEqual(self.CASES, 2000)
        self.assertGreater(stats["clean"], self.CASES // 3, stats)           # most cases are certain, not flagged
        self.assertGreater(stats["single"], self.CASES // 3, stats)
        self.assertLess(took, 20.0)

    def run_cases(self, seed, cases):
        rng = random.Random(seed)
        stats = {"clean": 0, "flagged": 0, "single": 0}
        for case in range(cases):
            words = T.made_up_words(_transcript(rng))
            occ = TE.timeline_words(words, [T.piece(right=1 + 600 * FPS)], FPS)
            v = TE.render(occ, words, None, FPS)
            model = _Model(v)
            ops = [] if case % 25 == 0 else [rng.choice(OPS) for _ in range(rng.choice((1, 1, 2)))]
            done = [op for op in ops if model.apply(rng, op)]
            what = "case %d %s\n%s\n--\n%s" % (case, done, v["text"], model.text())
            e = TE.compute_edit(v["tokens"], v["order"], model.text(), words, None, base_lines=v["lines"])
            everything = set(range(len(words)))
            truth_kept = set(model.truth_order())
            truth_cut = everything - truth_kept
            cut = set(e["cut"])
            # 1. no word is cut that the operation did not remove
            self.assertLessEqual(cut, truth_cut, what)
            # 2. a flagged line never causes a cut: the words on a flagged line, and the words of a line
            #    that was flagged because it holds text nobody said, are kept
            flagged_lines = {f["line"] for f in e["flags"]}
            for n in flagged_lines:
                for _t, w in model.lines[n]["toks"] if n < len(model.lines) else ():
                    self.assertNotIn(w, cut, what)
            # 3. nothing flagged: the edit is exactly what the operation removed, in the order it left
            if not e["flags"]:
                stats["clean"] += 1
                # words taken out beside a changed word are part of a rewritten block: kept, shown as ""
                swallowed = {int(k) for k, val in e["retext"].items() if val == ""}
                for w in sorted(swallowed):                 # ... and the word that carries the block's text
                    while w - 1 in swallowed:
                        w -= 1
                    swallowed.add(w - 1)
                self.assertLessEqual(truth_cut - cut, swallowed, what)
                if not swallowed:
                    self.assertEqual(cut, truth_cut, what)
                self.assertEqual([w for w in e["order"] if w in truth_kept], model.truth_order(), what)
                self.assertTrue(all(w in set(e["order"]) for w in truth_cut & swallowed), what)
                changed = {w for ln in model.lines for t, w in ln["toks"] if w is not None and t != model.original(w)}
                if not swallowed:
                    self.assertEqual(set(e["retexted"]), changed, what)
            else:
                stats["flagged"] += 1
            # 4. a single plain operation is certain: no flag unless the text can't tell which words went
            if len(done) == 1:
                stats["single"] += 1
                if done[0] == "insert":
                    self.assertEqual(len(e["flags"]), 1, what)
                    self.assertEqual(e["flags"][0]["reason"], "no_audio", what)
                    self.assertEqual(cut, set(), what)
                    self.assertFalse(e["changed"], what)
                elif done[0] == "delete_word" and _ambiguous_delete(model):
                    self.assertEqual(cut, set(), what)
                else:
                    self.assertEqual(e["flags"], [], what)
                    self.assertEqual(cut, truth_cut, what)
                    if done[0] in ("swap", "move"):
                        self.assertTrue(e["moved"], what)
                    else:
                        self.assertFalse(e["moved"], what)
            if not done:
                self.assertFalse(e["changed"], what)
                self.assertEqual(e["flags"], [], what)
            # 5. apply it and render again: that text asks for nothing
            after = _applied_view(e, words)
            again = TE.compute_edit(after["tokens"], after["order"], after["text"], words, e["retext"],
                                    base_lines=after["lines"])
            self.assertFalse(again["changed"], what)
            self.assertEqual(again["flags"], [], what)
        return stats


if __name__ == "__main__":
    unittest.main()
