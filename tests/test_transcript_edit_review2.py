# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the second review of Edit by Transcript (#498, PR #587): the faults it found in the rule
"nothing is cut by guessing", one named test each with the exact edit list (A to I), the guard that
the plain edits are still applied (not flagged) and the cases the property tests (rules, fuzz) take
for granted.

A  a selection from the middle of one line to the middle of the next, deleted: nothing of the line below
   is cut when its end is in the text
B  an Apply that leaves the artist's text as typed (Live, flags): the next diff's base is the artist's own
   lines, so a mark in the text and the same mark in the base are the same line
C  ``[2.5]``, ``[1]`` at the start of a line are words, not marks
D  one of two repeated words taken out: where the text tells which, it is cut, no flag
E  a cut never reaches into a kept word; words inside a kept word's time can't be cut: flagged
F  Remove Filler Words only goes ahead with a plan that cuts the fillers it listed
G  Live holds a cut that holds what is on the clipboard
H  the tool checks before it touches the text block: a refused call changes nothing
I  one word written as another is a typo or it is flagged, never a silent caption change

Run with::

    python -m unittest tests.test_transcript_edit_review2 -v
"""

__all__ = ()

import copy
import random
import types
import unittest

import test_transcript_edit as T
import test_transcript_edit_fuzz as F
import test_transcript_edit_rules as R
import test_transcript_edit_review as RV
import test_transcript_edit_ops as FB

TE = T.TE
FPS = T.FPS
mk, ed, rows, text_of = R.mk, R.ed, R.rows, R.text_of


def strip(line):
    return TE.MARK_RE.sub("", line, count=1)


def mark_of(line):
    return TE.MARK_RE.match(line).group(0).strip()


def applied(words, v, e):
    """The timeline after the edit *e* of the view *v*, as the view of it (marks as the renderer writes them now)."""
    pieces = F.layout(words, v["order"], e["order"])
    occ, view = F.view(words, pieces, e["retext"])
    return occ, view


def carried(words, v, e):
    """The base after an Apply that leaves the text as typed: the new view with the artist's own lines."""
    _occ, view = applied(words, v, e)
    cb = TE.carry_base(e, view)
    assert cb is not None
    return dict(view, lines=cb["lines"])


APPLES = [(1.0, "We like red apples."), (4.0, "The price went up today."), (8.0, "That is all.")]


# -- A -----------------------------------------------------------------------------------------------------

class TestA(unittest.TestCase):

    def test_a_a_selection_across_two_lines_keeps_the_end_of_the_second(self):
        words, _occ, v, _p = mk(APPLES)
        self.assertEqual(rows(v)[:2], ["[0:01.0] We like red apples.", "[0:04.0] The price went up today."])
        # "apples." .. "went up" selected and deleted: "We like red today."  The second line is NOT cut
        e = ed(v, v["text"].replace("apples.\n[0:04.0] The price went up ", ""), words)
        self.assertEqual(e["cut"], [])
        self.assertEqual(e["ops"], [])
        self.assertFalse(e["changed"])
        self.assertEqual(e["order"], list(range(len(words))))
        self.assertEqual([(f["line"], f["reason"], f["text"]) for f in e["flags"]], [(0, "no_match", "today.")])
        e = ed(v, v["text"].replace("apples.\n[0:04.0] The price went ", ""), words)
        self.assertEqual((e["cut"], [(f["line"], f["reason"]) for f in e["flags"]]), ([], [(0, "no_match")]))

    def test_a_a_selection_through_whole_lines_is_cut_exactly(self):
        words, _occ, v, _p = mk(APPLES)
        # "red apples. / The price" deleted: "We like went up today." - the first line's tail and the second's head
        e = ed(v, v["text"].replace("red apples.\n[0:04.0] The price ", ""), words)
        self.assertEqual(e["cut"], [2, 3, 4, 5])
        self.assertEqual([op["words"] for op in e["ops"]], [[2, 5]])
        self.assertEqual(e["flags"], [])
        # the whole second line and the end of the first: "We like."
        e = ed(v, v["text"].replace(" red apples.\n[0:04.0] The price went up today.", "."), words)
        self.assertEqual(e["cut"], [2, 3, 4, 5, 6, 7, 8])

    def test_a_the_end_of_a_line_that_is_also_the_end_of_the_next_is_flagged(self):
        words, _occ, v, _p = mk([(1.0, "Take the red one now."), (4.0, "Then we go home now."), (7.0, "The end.")])
        # "red one now. / Then we go home" deleted leaves "Take the now." (the tail of line 1 or of line 2?)
        text = v["text"].replace("red one now.\n[0:04.0] Then we go home ", "")
        self.assertNotEqual(text, v["text"])
        e = ed(v, text, words)
        self.assertEqual(e["cut"], [])
        self.assertEqual([(f["line"], f["reason"]) for f in e["flags"]], [(0, "ambiguous")])

    def test_a_a_mark_edited_to_the_mark_of_a_deleted_line(self):
        words, _occ, v, _p = mk(APPLES)
        lines = rows(v)
        # line 1 carries the mark of line 2, line 2 is deleted: which line is it?  Flagged, nothing is cut
        text = text_of([mark_of(lines[1]) + " " + strip(lines[0]), lines[2]])
        e = ed(v, text, words)
        self.assertEqual(e["cut"], [])
        self.assertEqual([(f["line"], f["reason"]) for f in e["flags"]], [(0, "ambiguous")])
        self.assertFalse(e["changed"])

    def test_a_three_lines_joined_the_mark_deleted_and_moved(self):
        words, _occ, v, _p = mk([(1.0, "One two three four five six seven."), (5.0, "Eight nine."), (7.0, "Ten eleven."),
                                 (10.0, "Twelve thirteen fourteen."), (13.0, "Fifteen sixteen.")])
        lines = rows(v)
        joined = " ".join(strip(ln) for ln in lines[:3])
        e = ed(v, text_of([lines[3], joined, lines[4]]), words)
        self.assertEqual(e["cut"], [])
        self.assertEqual([(f["line"], f["reason"]) for f in e["flags"]], [(1, "no_match")])
        self.assertFalse(e["changed"])

    def test_a_a_one_word_line_with_its_mark_deleted_moved_and_a_typo(self):
        words, _occ, v, _p = mk([(1.0, "Hello there everyone."), (5.0, "Okay."), (8.0, "Now we start the talk.")])
        lines = rows(v)
        e = ed(v, text_of([lines[2], lines[0], "Okzy."]), words)
        self.assertEqual(e["cut"], [])
        self.assertEqual(sorted(e["order"]), list(range(len(words))))        # every word stays


# -- B -----------------------------------------------------------------------------------------------------

REPEATS = [(1.0, "Hello there."), (3.0, "This part goes."), (6.0, "Something else here."), (9.0, "Okay."),
           (10.82, "Okay, let us start.")]


class TestB(unittest.TestCase):

    def live_delete(self, sentences=REPEATS, gone="part"):
        words, _occ, v, _p = mk(sentences)
        lines = [ln for ln in rows(v) if gone not in ln]
        e = ed(v, text_of(lines), words)
        return words, v, e, text_of(lines)

    def test_b_a_typo_fixed_after_a_live_apply_cuts_nothing(self):
        for last in (10.82, 10.833, 10.85, 10.9):
            words, v, e1, typed = self.live_delete(REPEATS[:4] + [(last, "Okay, let us start.")])
            self.assertEqual((e1["cut"], e1["flags"]), ([2, 3, 4], []), last)
            base = carried(words, v, e1)
            # the marks of the typed text are the marks of the base: the same lines
            self.assertEqual([ln[0] for ln in base["lines"]], [mark_of(ln) for ln in typed.splitlines()])
            e2 = ed(base, typed.replace("Hello", "Helo"), words, e1["retext"])
            self.assertEqual((e2["cut"], e2["retext"], e2["flags"], e2["moved"]), ([], {"0": "Helo"}, [], False), last)

    def test_b_nothing_typed_after_a_live_apply_asks_for_nothing(self):
        words, v, e1, typed = self.live_delete()
        e2 = ed(carried(words, v, e1), typed, words, e1["retext"])
        self.assertEqual((e2["changed"], e2["flags"], e2["ops"]), (False, [], []))

    def test_b_the_view_alone_is_not_the_base_after_a_live_apply(self):
        # the marks of the re-rendered timeline are not the marks the text has: with them as the base the text is
        # still never cut wrongly, but it is no longer read as it was typed (the reason for carry_base)
        words, v, e1, typed = self.live_delete()
        _occ, view = applied(words, v, e1)
        self.assertNotEqual([ln["mark"] for ln in view["lines"]], [mark_of(ln) for ln in typed.splitlines()])
        e2 = ed(view, typed.replace("Hello", "Helo"), words, e1["retext"])
        self.assertEqual(e2["cut"], [])

    def test_b_a_flagged_line_stays_pending_against_its_words(self):
        words, _occ, v, _p = mk(APPLES)
        # one line is flagged (a rewrite), another is cut: the Apply cuts the second and the first is still pending
        lines = rows(v)
        typed = text_of([lines[0].replace("red apples.", "blue pears."), lines[1], lines[2]])
        e1 = ed(v, typed, words)
        self.assertEqual(e1["cut"], [])
        self.assertEqual([f["line"] for f in e1["flags"]], [0])
        typed2 = text_of([lines[0].replace("red apples.", "blue pears."), lines[2]])      # and now line 2 is deleted
        e1 = ed(v, typed2, words)
        self.assertEqual((e1["cut"], [f["line"] for f in e1["flags"]]), ([], [0]))      # flagged line: its words stay
        base = carried(words, v, e1)
        e2 = ed(base, typed2, words, e1["retext"])
        self.assertEqual((e2["cut"], [f["line"] for f in e2["flags"]]), ([], [0]))      # the same line, the same flag

    def test_b_the_state_an_apply_stores_holds_the_artists_marks(self):
        words, v, e1, typed = self.live_delete()
        occ, view = applied(words, v, e1)
        FB_ops = RV.FB._load(RV.FB._fake_bpy())
        state = {}
        got = {"view": view, "occ": occ, "fps": FPS, "pieces": F.layout(words, v["order"], e1["order"])}
        FB_ops._state_from_view(state, got, [], TE.carry_base(e1, view))
        import json
        marks = [r[0] for r in json.loads(state["base_lines"])]
        self.assertEqual(marks, [mark_of(ln) for ln in typed.splitlines()])
        # the line numbers of the text (not of the re-rendered view) tell the playhead where each line is
        self.assertEqual([n for n, _f in json.loads(state["lines"])], list(range(len(marks))))

    def test_b_a_second_edit_on_a_text_that_was_not_rewritten(self):
        words, _occ, v, _p = mk([(1.0, "Alpha bravo."), (3.0, "Charlie delta."), (6.0, "Echo foxtrot."),
                                 (9.0, "Golf hotel."), (12.0, "India juliet.")])
        lines = rows(v)
        typed = text_of([lines[0], lines[2], lines[3], lines[4]])                   # "Charlie delta." deleted (Live)
        e1 = ed(v, typed, words)
        self.assertEqual(e1["cut"], [2, 3])
        base = carried(words, v, e1)
        typed2 = text_of([lines[0], lines[2], lines[4]])                             # "Golf hotel." deleted too
        e2 = ed(base, typed2, words, e1["retext"])
        self.assertEqual((e2["cut"], e2["flags"]), ([6, 7], []))


# -- C -----------------------------------------------------------------------------------------------------

class TestC(unittest.TestCase):

    def test_c_only_the_shape_the_renderer_writes_is_a_mark(self):
        for ok in ("[0:05.2] x", "[12:30.0] x", "[1:02:03.4] x", "[0:01.25] x", "  [0:01.0] x"):
            self.assertIsNotNone(TE.MARK_RE.match(ok), ok)
        for word in ("[2.5] x", "[1] x", "[laughs] x", "[] x", "[0:0x.1] x", "[1:2] x", "[0:05] x", "[3:4.5] x"):
            self.assertIsNone(TE.MARK_RE.match(word), word)
        self.assertEqual([t for _n, t in TE.parse("[2.5] hello\n[1] there")], ["[2.5]", "hello", "[1]", "there"])

    def test_c_a_number_in_brackets_at_the_start_of_a_line_after_enter_is_not_cut(self):
        words, _occ, v, _p = mk([(1.0, "The value is [2.5] today."), (5.0, "Next.")])
        for tail in ("[2.5]", "today."):
            e = ed(v, v["text"].replace(" " + tail, "\n" + tail), words)
            self.assertEqual((e["cut"], e["flags"], e["changed"]), ([], [], False), tail)
        words, _occ, v, _p = mk([(1.0, "See note [1] on page three."), (6.0, "Next.")])
        e = ed(v, v["text"].replace(" [1]", "\n[1]"), words)
        self.assertEqual((e["cut"], e["flags"], e["changed"]), ([], [], False))

    def test_c_such_a_word_deleted_is_cut_as_a_word(self):
        words, _occ, v, _p = mk([(1.0, "The value is [2.5] today."), (5.0, "Next.")])
        e = ed(v, v["text"].replace("[2.5] ", ""), words)
        self.assertEqual((e["cut"], e["flags"]), ([3], []))

    def test_c_a_typed_line_that_starts_with_one_is_text_nobody_said(self):
        words, _occ, v, _p = mk([(1.0, "One line."), (5.0, "Another line.")])
        e = ed(v, v["text"] + "[1] a note of mine\n", words)
        self.assertEqual(e["cut"], [])
        self.assertEqual([(f["line"], f["reason"], f["text"]) for f in e["flags"]],
                         [(len(v["text"].splitlines()), "no_audio", "[1] a note of mine")])


# -- D -----------------------------------------------------------------------------------------------------

class TestD(unittest.TestCase):

    def cut_of(self, sentence, old, new):
        words, _occ, v, _p = mk([(1.0, sentence), (6.0, "Next one.")])
        e = ed(v, v["text"].replace(old, new, 1), words)
        return e["cut"], [f["reason"] for f in e["flags"]], e["retexted"]

    def test_d_the_written_form_tells_which_one_stays(self):
        self.assertEqual(self.cut_of("Okay, okay let us go.", "Okay, okay", "okay"), ([0], [], []))
        self.assertEqual(self.cut_of("Okay, okay let us go.", "Okay, okay", "Okay,"), ([1], [], []))

    def test_d_the_very_same_token_in_a_row_is_one_choice(self):
        self.assertEqual(self.cut_of("So the the plan is simple.", "the the", "the"), ([1], [], []))
        self.assertEqual(self.cut_of("I I think so.", "I I", "I"), ([0], [], []))
        self.assertEqual(self.cut_of("so so so far.", "so so so", "so so"), ([0], [], []))
        self.assertEqual(self.cut_of("so so so far.", "so so so", "so"), ([0, 1], [], []))

    def test_d_two_adjacent_words_next_to_a_repeat(self):
        words, _occ, v, _p = mk([(1.0, "Well well that is fine."), (6.0, "Next one.")])
        e = ed(v, v["text"].replace("well well that", "that").replace("Well well that", "that"), words)
        self.assertEqual((e["cut"], e["flags"]), ([0, 1], []))
        e = ed(v, v["text"].replace("Well well that", "Well that"), words)         # the kept one is the capital one
        self.assertEqual((e["cut"], e["flags"]), ([1], []))

    def test_d_anything_else_stays_flagged(self):
        # a repeated phrase, the same word in two places: which one went can't be told
        self.assertEqual(self.cut_of("so we go so we go now.", "so we go so we go", "so we go"), ([], ["ambiguous"], []))
        cut, flags, _r = self.cut_of("a b a b now.", "a b a b", "a b")
        self.assertEqual((cut, flags), ([], ["ambiguous"]))
        cut, flags, _r = self.cut_of("a b c a b.", "a b c a b", "a b c")             # this one has one answer
        self.assertEqual((cut, flags), ([3, 4], []))

    def test_d_plain_deletes_on_transcripts_with_repeats_are_applied(self):
        rng = random.Random(4)
        applied_n = flagged = 0
        for _ in range(600):
            words = F.make_transcript(rng, repeats=True, odd=False)
            occ, v = F.view(words, [F.piece()])
            m0 = F.Model(v["text"], occ, words)
            m = copy.deepcopy(m0)
            if not (F.op_del_word if rng.random() < 0.5 else F.op_del_two)(m, rng):
                continue
            e = F.compute(v, m.text(), words)
            applied_n += 1
            flagged += bool(e["flags"])
            self.assertLessEqual(len(e["cut"]), len(m0.wordset() - m.wordset()))
        self.assertLess(flagged, 0.04 * applied_n)           # the rate was 12 % and 18 %


# -- E -----------------------------------------------------------------------------------------------------

def timed(*spec):
    return [{"w": "w%d" % k, "start": s, "end": e} for k, (s, e) in enumerate(spec)]


def piece(name="a", left=1, right=1 + 60 * FPS, cs=1.0, off=0.0):
    return {"name": name, "left": left, "right": right, "content_start": cs, "offset": off}


class TestE(unittest.TestCase):

    def test_e_a_cut_stops_where_the_next_kept_word_starts(self):
        words = timed((1.0, 1.5), (1.4, 1.9), (1.8, 2.3))              # each word starts before the one before ends
        occ = TE.timeline_words(words, [piece()], FPS)
        self.assertEqual(TE.cut_ranges(occ, [1], words, [piece()], FPS), [(46, 55)])      # 1.5 s .. 1.8 s: not into word 3
        self.assertEqual(TE.cut_ranges(occ, [2], words, [piece()], FPS), [(58, 74)])      # from where word 2 ends (1.9 s)

    def test_e_a_word_inside_the_previous_words_time_is_flagged_and_stays(self):
        words = timed((1.0, 3.0), (1.5, 2.0), (3.2, 3.5))
        occ = TE.timeline_words(words, [piece()], FPS)
        self.assertEqual(TE.cut_ranges(occ, [1], words, [piece()], FPS), [])           # nothing can be cut there
        v = TE.render(occ, words, None, FPS)
        e = TE.compute_edit(v["tokens"], v["order"], "[0:01.0] w0 w2\n", words, None, base_lines=v["lines"])
        self.assertEqual(e["cut"], [])
        self.assertFalse(e["changed"])
        self.assertEqual([(f["line"], f["reason"]) for f in e["flags"]], [(0, "overlap")])

    def test_e_a_deleted_line_inside_a_kept_words_time_is_flagged_on_the_text(self):
        words = timed((1.0, 3.0), (1.5, 2.0), (3.2, 3.5), (4.5, 5.0))
        # the second word lies inside the first: it is a line of its own only by its pause
        occ = TE.timeline_words(words, [piece()], FPS)
        v = TE.render(occ, words, None, FPS)
        e = TE.compute_edit(v["tokens"], v["order"], v["text"].replace(" w1", ""), words, None, base_lines=v["lines"])
        self.assertEqual(e["cut"], [])
        self.assertEqual([f["reason"] for f in e["flags"]], ["overlap"])

    def test_e_no_cut_reaches_into_a_kept_word(self):
        rng = random.Random(5)
        for _case in range(3000):
            ws, t = [], 0.5
            for k in range(rng.randint(3, 25)):
                s = t + rng.choice((0.0, 0.05, 0.2, 0.5, 1.0, 3.0)) - (0.05 if rng.random() < 0.1 else 0.0)
                e = s + rng.choice((0.1, 0.3, 0.6))
                ws.append({"w": "w%d" % k, "start": round(max(0.0, s), 3), "end": round(max(0.0, e), 3)})
                t = e
            p = piece(left=1 + rng.randint(0, 10), right=1 + 700)
            occ = TE.timeline_words(ws, [p], FPS)
            on = [o["i"] for o in occ]
            if not on:
                continue
            cut = set(rng.sample(on, rng.randint(1, len(on))))
            ranges = TE.cut_ranges(occ, cut, ws, [p], FPS)
            for a, b in ranges:
                self.assertGreater(b, a)
                self.assertTrue(p["left"] <= a and b <= p["right"])
            for o in occ:
                if o["i"] in cut:
                    continue
                s0, s1 = 1.0 + ws[o["i"]]["start"] * FPS, 1.0 + ws[o["i"]]["end"] * FPS
                for a, b in ranges:
                    self.assertFalse(a < s1 - 0.5 and b > s0 + 0.5 and s1 > s0, (ws, sorted(cut), ranges))


# -- F, G, H (the Blender side on the stand-in bpy) -------------------------------------------------------

class TestF(RV._Rig):

    def make(self):
        op = self.ops.BFACW_OT_transcript_remove_fillers()
        op.text = lambda ctx: self.text
        self.reports = []
        op.report = lambda kind, msg: self.reports.append((kind, msg))
        op.phrases = False
        return op

    def test_f_only_listed_says_what_else_a_plan_would_do(self):
        on = range(10)
        plan = {"cut": [3], "retexted": [], "moved": False, "flags": []}
        self.assertEqual(TE.only_listed(plan, [3], on), "")
        self.assertEqual(TE.only_listed(dict(plan, cut=[3, 4, 5]), [3], on), "cut 2 other words")
        self.assertEqual(TE.only_listed(dict(plan, cut=[]), [3], on), "leave 1 of the filler words in")
        self.assertEqual(TE.only_listed(dict(plan, retexted=[7]), [3], on), "change 1 word")
        self.assertEqual(TE.only_listed(dict(plan, moved=True), [3], on), "move words")
        self.assertEqual(TE.only_listed(dict(plan, flags=[{}]), [3], on), "flag 1 line")
        self.assertEqual(TE.only_listed(dict(plan, cut=[3, 40]), [3, 40], on), "")       # not on the timeline: nothing to cut

    def test_f_a_plan_that_cuts_more_than_the_fillers_is_refused(self):
        op = self.make()
        bodies = []
        self.ops.apply_text = lambda text, body=None, **k: bodies.append(body) or {"status": "ok", "message": "x"}
        real = self.ops.te.remove_tokens
        # the text it would apply has a whole sentence missing too (as if the text were not what it was read from)
        self.ops.te.remove_tokens = lambda text, tokens, doomed: "\n".join(real(text, tokens, doomed).splitlines()[:3]) + "\n"
        try:
            self.assertEqual(op.execute(types.SimpleNamespace()), {'CANCELLED'})
        finally:
            self.ops.te.remove_tokens = real
        self.assertEqual(bodies, [])
        self.assertEqual(self.reports[0][0], {'ERROR'})
        self.assertIn("would also cut", self.reports[0][1])
        self.assertIn("nothing was changed", self.reports[0][1])


class TestG(RV.TestLive):

    def test_g_two_lines_deleted_side_by_side_one_of_them_on_the_clipboard_is_held(self):
        # line 2 is deleted, then line 3 is Ctrl+X'd (to paste it elsewhere): one cut of both lines
        lines = self.lines()
        self.text.body = self.edited(lines[:2] + lines[4:])
        self.edit = TE.compute_edit(self.view["tokens"], self.view["order"], self.text.body, self.words, None,
                                    base_lines=self.view["lines"])
        self.assertEqual([op["op"] for op in self.edit["ops"]], ["cut"])              # one cut: both lines
        self.bpy.context.window_manager.clipboard = lines[3] + "\n"
        self.tick()
        self.tick(5.0)
        self.tick(5.0)
        self.assertEqual(self.applied, [])
        self.assertIn("clipboard", self.ops._SEEN[self.text.name]["live_note"])
        self.bpy.context.window_manager.clipboard = "something else"
        self.tick(1.0)
        self.assertEqual(len(self.applied), 1)

    def test_g_a_clipboard_that_is_not_in_the_cut_does_not_hold_it(self):
        self.bpy.context.window_manager.clipboard = "[0:01.0] Welcome to the little test clip.\n"
        self.tick(5.0)
        self.tick(5.0)
        self.assertEqual(len(self.applied), 1)


class TestH(RV._Rig):

    def setUp(self):
        super().setUp()
        self.tool_stubs()

    def test_h_a_refused_call_opens_nothing(self):
        got = []
        self.ops.apply_text = lambda text, body=None, **k: got.append(body) or {"status": "ok", "message": "x"}
        out = self.ops.tool_apply_transcript_edit(cut_lines=[4], version="0123456789abcdef")           # stale
        self.assertEqual(out["status"], "refused")
        out = self.ops.tool_apply_transcript_edit(cut_lines=[4])                                        # no version
        self.assertEqual(out["status"], "error")
        out = self.ops.tool_apply_transcript_edit(cut_lines=[40], version=self.ops._hash(self.body))    # no such line
        self.assertEqual(out["status"], "error")
        out = self.ops.tool_apply_transcript_edit(text="")                                              # a large cut, no confirm
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["percent"], 100)
        self.assertEqual(self.calls, [])                       # open_transcript was never called: nothing was made or refreshed
        self.assertEqual(got, [])

    def test_h_a_call_that_goes_ahead_opens_the_text_once(self):
        got = []
        self.ops.apply_text = lambda text, body=None, **k: got.append(body) or {"status": "ok", "message": "x"}
        out = self.ops.tool_apply_transcript_edit(cut_lines=[4], version=self.ops._hash(self.body))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(self.calls.count("open_transcript"), 1)
        self.assertNotIn("The price", got[0])

    def test_h_a_call_the_apply_then_refuses_puts_the_text_back(self):
        self.ops.apply_text = lambda text, body=None, **k: {"status": "refused", "message": "a strip is locked",
                                                           "changed": False}
        restored = []
        self.ops._undo_open = lambda scene, strip, target, snapshot: restored.append((target, snapshot))
        out = self.ops.tool_apply_transcript_edit(cut_lines=[4], version=self.ops._hash(self.body))
        self.assertEqual(out["status"], "refused")
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0][1][1], self.body)          # the text it had before the call


# -- I -----------------------------------------------------------------------------------------------------

class TestI(unittest.TestCase):

    def test_i_a_replacement_that_is_not_clearly_the_same_words_is_flagged(self):
        words, _occ, v, _p = mk([(1.0, "Hello there."), (3.0, "Sick charlie."), (6.0, "Oscar went home.")])
        for new in ("Oscar.", "Car."):
            e = ed(v, v["text"].replace("Sick charlie.", new), words)
            self.assertEqual((e["cut"], e["retexted"], e["retext"]), ([], [], {}), new)
            self.assertEqual([(f["line"], f["reason"]) for f in e["flags"]], [(1, "no_match")], new)

    def test_i_one_word_for_one_word_needs_the_typo_threshold(self):
        words, _occ, v, _p = mk([(1.0, "We bought a cat."), (5.0, "Next one here.")])
        e = ed(v, v["text"].replace(" a ", " and "), words)                        # "a" -> "and": not the same word
        self.assertEqual((e["retexted"], [f["reason"] for f in e["flags"]]), ([], ["no_match"]))
        e = ed(v, v["text"].replace("bought", "bougth"), words)                    # a typo
        self.assertEqual((e["retexted"], e["flags"]), ([1], []))
        e = ed(v, v["text"].replace("Next", "Nexz"), words)                        # a letter of a short word
        self.assertEqual((e["retexted"], e["flags"]), ([4], []))

    def test_i_clearly_the_same_words_are_still_a_caption_fix(self):
        words, _occ, v, _p = mk([(1.0, "I think it is very good."), (5.0, "We are going to win.")])
        e = ed(v, v["text"].replace("it is very", "it's"), words)
        self.assertEqual((e["retext"], e["flags"]), ({"2": "it's", "3": "", "4": ""}, []))
        e = ed(v, v["text"].replace("going to", "gonna"), words)
        self.assertEqual((e["retext"], e["flags"]), ({"8": "gonna", "9": ""}, []))
        e = ed(v, v["text"].replace("are going to", "are gone too"), words)
        self.assertEqual(e["flags"], [])


# -- the guard: plain edits are applied, not flagged --------------------------------------------------------

def _plain(op_name, rng, words, v, m0):
    """One plain edit on the model: ``(model, what the edit must be)``."""
    m = copy.deepcopy(m0)
    if op_name == "delete_line":
        ok = F.op_del_line(m, rng)
    elif op_name == "delete_word":
        ok = F.op_del_word(m, rng)
    elif op_name == "delete_two":
        ok = F.op_del_two(m, rng)
    elif op_name == "typo":
        ok = F.op_typo(m, rng)
    elif op_name == "move":
        ok = F.op_move(m, rng) and [ln for ln in m.text().splitlines() if ln.strip()] !=             [ln for ln in m0.text().splitlines() if ln.strip()]
    elif op_name == "split":
        ks = [k for k in m.textual() if len(m.lines[k]["toks"]) >= 2]
        ok = bool(ks)
        if ok:
            k = rng.choice(ks)
            j = rng.randrange(1, len(m.lines[k]["toks"]))
            tail = m.lines[k]["toks"][j:]
            m.lines[k]["toks"] = m.lines[k]["toks"][:j]
            m.lines.insert(k + 1, {"mark": None, "toks": tail, "origin": set(m.lines[k]["origin"])})
    elif op_name == "join":
        ks = m.textual()
        ok = len(ks) >= 2
        if ok:
            i = rng.randrange(len(ks) - 1)
            a, b = ks[i], ks[i + 1]
            m.lines[a]["toks"] = m.lines[a]["toks"] + m.lines[b]["toks"]
            for k in range(b, a, -1):
                del m.lines[k]
    else:
        raise AssertionError(op_name)
    return m if ok else None


class TestPlainEditsAreApplied(unittest.TestCase):

    OPS = ("delete_line", "delete_word", "delete_two", "typo", "move", "split", "join")

    def check(self, e, op_name, m0, m, what):
        expect = m0.wordset() - m.wordset()
        self.assertEqual(e["flags"], [], what)
        self.assertEqual(set(e["cut"]), expect, what)
        if op_name == "move":
            self.assertTrue(e["moved"], what)
        else:
            self.assertFalse(e["moved"], what)
        if op_name == "typo":
            self.assertEqual(len(e["retexted"]), 1, what)
        elif op_name in ("split", "join"):
            self.assertFalse(e["changed"], what)

    def test_each_plain_edit_on_fresh_text_is_applied_exactly(self):
        for op_name in self.OPS:
            rng = random.Random(hash(op_name) & 0xffff)
            done = 0
            while done < 300:
                words = F.make_transcript(rng, repeats=False, odd=False)
                occ, v = F.view(words, [F.piece()])
                m0 = F.Model(v["text"], occ, words)
                m = _plain(op_name, rng, words, v, m0)
                if m is None:
                    continue
                e = F.compute(v, m.text(), words)
                self.check(e, op_name, m0, m, "%s\n%s\n--\n%s" % (op_name, v["text"], m.text()))
                done += 1

    def test_and_after_one_live_apply_the_same(self):
        for op_name in self.OPS:
            rng = random.Random((hash(op_name) + 7) & 0xffff)
            done = 0
            while done < 300:
                words = F.make_transcript(rng, repeats=False, odd=False)
                occ, v = F.view(words, [F.piece()])
                m0 = F.Model(v["text"], occ, words)
                m1 = copy.deepcopy(m0)
                if not F.op_del_line(m1, rng):                            # the Live apply: one line deleted, text as typed
                    continue
                e1 = F.compute(v, m1.text(), words)
                self.assertEqual((set(e1["cut"]), e1["flags"]), (m0.wordset() - m1.wordset(), []))
                base = carried(words, v, e1)
                for n, ln in enumerate(m1.lines):
                    ln["origin"] = {n} if ln["toks"] else set()
                m = _plain(op_name, rng, words, base, m1)
                if m is None:
                    continue
                e = F.compute(base, m.text(), words, e1["retext"])
                self.check(e, op_name, m1, m, "after a Live apply: %s\n%s\n--\n%s" % (op_name, base["text"], m.text()))
                done += 1


# -- what the property tests take for granted --------------------------------------------------------------

class TestMore(unittest.TestCase):

    def test_copy_and_paste_a_line_is_flagged_not_cut(self):
        words, _occ, v, _p = mk([(1.0, "Alpha bravo."), (3.0, "Charlie delta."), (6.0, "Echo foxtrot.")])
        lines = rows(v)
        e = ed(v, text_of([lines[0], lines[1], lines[0], lines[2]]), words)         # a copy of line 1 after line 2
        self.assertEqual(e["cut"], [])
        self.assertFalse(e["changed"])
        self.assertEqual([(f["line"], f["reason"], f["text"]) for f in e["flags"]], [(2, "no_audio", "Alpha bravo.")])

    def test_copy_then_delete_the_original_is_a_move(self):
        words, _occ, v, _p = mk([(1.0, "Alpha bravo."), (3.0, "Charlie delta."), (6.0, "Echo foxtrot.")])
        lines = rows(v)
        e = ed(v, text_of([lines[1], lines[2], lines[0]]), words)                    # the first line cut and pasted last
        self.assertEqual((e["cut"], e["moved"], e["flags"]), ([], True, []))
        self.assertEqual(e["order"], [2, 3, 4, 5, 0, 1])

    def test_a_line_replaced_by_another_lines_text_is_flagged(self):
        words, _occ, v, _p = mk([(1.0, "Alpha bravo."), (3.0, "Charlie delta."), (6.0, "Echo foxtrot.")])
        lines = rows(v)
        e = ed(v, text_of([lines[0], mark_of(lines[1]) + " " + strip(lines[2]), lines[2]]), words)
        self.assertEqual(e["cut"], [])
        self.assertTrue(e["flags"])
        self.assertFalse(e["changed"])

    def test_a_mark_edited_to_another_lines_mark_with_both_kept(self):
        words, _occ, v, _p = mk([(1.0, "Alpha bravo."), (3.0, "Charlie delta."), (6.0, "Echo foxtrot.")])
        lines = rows(v)
        e = ed(v, text_of([lines[0], mark_of(lines[0]) + " " + strip(lines[1]), lines[2]]), words)
        self.assertEqual((e["cut"], e["flags"], e["changed"]), ([], [], False))

    def test_two_adjacent_words_deleted(self):
        words, _occ, v, _p = mk([(1.0, "Alpha bravo charlie delta echo."), (6.0, "Foxtrot golf.")])
        e = ed(v, v["text"].replace("bravo charlie ", ""), words)
        self.assertEqual((e["cut"], e["flags"]), ([1, 2], []))
        self.assertEqual(e["ops"][0]["text"], "bravo charlie")

    def test_one_and_two_word_lines(self):
        words, _occ, v, _p = mk([(1.0, "Yes."), (3.0, "Fine, thanks."), (6.0, "Okay."), (8.0, "A longer sentence follows here.")])
        lines = rows(v)
        for gone in range(4):
            e = ed(v, text_of([ln for n, ln in enumerate(lines) if n != gone]), words)
            self.assertEqual(len(e["cut"]), [1, 2, 1, 5][gone], gone)
            self.assertEqual(e["flags"], [])
        e = ed(v, text_of([lines[0], lines[2], lines[1], lines[3]]), words)          # two lines swapped
        self.assertEqual((e["cut"], e["moved"], e["flags"]), ([], True, []))

    def test_bracket_number_and_unicode_words(self):
        words, _occ, v, _p = mk([(1.0, "It costs 3.5 [laughs] in café 日本."), (6.0, "Also x[1] and 50% off, naïve.")])
        e = ed(v, v["text"].replace("[laughs] ", ""), words)
        self.assertEqual((e["cut"], e["flags"]), ([3], []))
        e = ed(v, v["text"].replace("x[1] ", ""), words)
        self.assertEqual((e["cut"], e["flags"]), ([8], []))
        e = ed(v, text_of(rows(v)[1:]), words)
        self.assertEqual(e["cut"], list(range(7)))

    def test_blank_paragraph_lines_are_not_lines(self):
        words, _occ, v, _p = mk([(1.0, "First paragraph here."), (8.0, "Second paragraph here."), (15.0, "Third one.")])
        self.assertIn("\n\n", v["text"])
        e = ed(v, v["text"].replace("\n\n", "\n"), words)
        self.assertFalse(e["changed"])
        e = ed(v, v["text"].replace("\n\n", "\n\n\n\n"), words)
        self.assertFalse(e["changed"])
        e = ed(v, v["text"].replace("\n\n[0:08.0] Second paragraph here.", ""), words)
        self.assertEqual((e["cut"], e["flags"]), ([3, 4, 5], []))


if __name__ == "__main__":
    unittest.main()
