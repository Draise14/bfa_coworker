# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Highlights H3 (#613): find and rank (``highlights.py``), the criteria chapter
(``skills/video_highlights.md``) and the written test transcripts (``highlights_texts_613.py``).

The model is a STAND-IN, never a real model: a function that answers the Find and Rank requests
from a list of the planted moments it is told about.  So these tests prove the plumbing (windows,
the checks, the merge, the groups, the ranking sum, the placing, the retries and the report), not
the quality of any model's taste.  The real-model numbers come from ``_misc/highlights_model_check.py``
and are in the pull request.

* Chapter: the packaged chapter reads; a broken one is named, not guessed; it costs the chat nothing.
* Checks: sentence numbers must exist inside the window; the length; a range a little too long is
  trimmed back to a full stop, or refused.
* Merge: the same piece found by two windows becomes one; a little overlap stays apart.
* Ranking sum: the weights of the kind of video, density counted in code.
* Pipeline on the two written hours (podcast, tutorial) with the stand-in: at least four of the six
  planted moments are in the top five, no pick starts or ends inside a sentence, no two picks share
  frames, the report counts what happened.
* Retries, a part that cannot be read, groups on small contexts, Auto kind, stop, no engine.

No Blender needed.  Run with::

    python -m unittest tests.test_highlights_613 -v
"""

__all__ = ()

import json
import re
import unittest
from unittest import mock

import highlights_texts_613 as TEXTS
import test_wand_remove_silences as W

SR = W._import("scene_reader")
HL = W._import("highlights")
AJ = W._import("ask_json")
SK = W._import("skills")
FPS = 30


def sentences_of(data, key="a" * 16):
    """The numbered sentences of a written transcript, through the real scene reader."""
    right = 1 + int((data["minutes"] * 60 + 30) * FPS)
    src = {"key": key, "path": "a.wav", "words": data["words"],
           "pieces": [{"name": "A", "left": 1, "right": right, "content_start": 1.0, "offset": 0.0, "channel": 1}]}
    return SR.read_scene([src], FPS)["sentences"]


def sentence_line(n, text):
    return "S{:d} [0:00.0 5.0s 2.5w/s] {:s}".format(n, text)


def simple_sentences(count=30, seconds=5.0, words=12, punct="."):
    """Made-up sentences by hand, in the shape the scene reader makes."""
    out = []
    for n in range(1, count + 1):
        start = int((n - 1) * (seconds + 1.0) * FPS) + 1
        out.append({
            "n": n, "text": "word " * (words - 1) + "end" + punct, "start": start, "end": start + int(seconds * FPS),
            "t": round((n - 1) * (seconds + 1.0), 2), "seconds": seconds, "words": words, "wps": words / seconds,
            "fillers": 0, "pause_before": 1.0 if n > 1 else 0.0, "question": False, "number": False,
            "speaker": "", "speaker_change": False,
            "first": {"media": "m" * 16, "i": (n - 1) * words}, "last": {"media": "m" * 16, "i": n * words - 1},
            "media": ["m" * 16]})
    return out


def reply(obj_or_text):
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
    return {"choices": [{"message": {"content": text}}]}


class StandIn:
    """A stand-in model behind the real ``ask_json`` (as a transport function): it reads the
    sentence numbers in the Find request and the clip texts in the Rank request, and answers from
    a list of planted passages it is told about.  Not a model: it proves the plumbing."""

    def __init__(self, planted_texts, kind="podcast", decoys=True, bad_first_windows=0, fail_windows=(),
                 rank_scores=None):
        self.planted = planted_texts            # [first six words of each planted passage]
        self.kind = kind
        self.decoys = decoys
        self.bad_first_windows = bad_first_windows
        self.fail_windows = set(fail_windows)   # find-request numbers (1-based) that never answer well
        self.rank_scores = rank_scores
        self.calls = []                         # (find or rank, messages, extra_body)
        self.windows_seen = 0
        self.find_count = 0
        self.rank_count = 0

    def __call__(self, url, messages, tools, key, model, **kw):
        system = messages[0]["content"]
        body = messages[1]["content"]
        self.calls.append(("find" if body.startswith("Transcript part:") else "rank", messages, kw.get("extra_body")))
        del system
        if body.startswith("Transcript part:"):
            self.find_count += 1
            if not any(m["role"] == "assistant" for m in messages):
                self.windows_seen += 1
            if self.find_count in self.fail_windows:
                return reply("I could not decide, sorry.")
            if self.windows_seen <= self.bad_first_windows and not any(
                    m["role"] == "assistant" for m in messages):
                return reply("Here you go: {not json")
            return reply(self._find(body))
        self.rank_count += 1
        return reply(self._rank(body))

    def _find(self, body):
        lines = re.findall(r"^S(\d+) \[[^\]]*\] (.*)$", body, re.M)
        nums = [int(n) for n, _ in lines]
        cands = []
        for first_words in self.planted:
            idx = [i for i, (_n, t) in enumerate(lines) if t.startswith(first_words)]
            if idx:
                i = idx[0]
                # the passage runs eight sentences; the window may cut it, then it is not found here
                if i + 7 < len(lines):
                    cands.append({"first": nums[i], "last": nums[i + 7], "label": "a strong moment"})
        if self.decoys:
            mid = min(len(nums) - 4, 5)
            cands.append({"first": nums[mid], "last": nums[mid + 3], "label": "small talk and a lot of um"})
            cands.append({"first": nums[0], "last": nums[-1], "label": "everything in the window"})
            cands.append({"first": 99999, "last": 100005, "label": "invented"})
        return {"candidates": cands}

    def _rank(self, body):
        clips = re.findall(r"^Clip ([A-Z]+):\n(.*?)(?=\n\nClip |\Z)", body, re.S | re.M)
        picks = []
        for letter, text in clips:
            strong = any(w in text for w in self.planted)
            scores = self.rank_scores or ({"hook": 3, "alone": 3, "complete": 3, "one_idea": 3, "energy": 2, "tease": 1}
                                          if strong else {"hook": 1, "alone": 0, "complete": 1, "one_idea": 1,
                                                          "energy": 0, "tease": 0})
            picks.append({"id": letter, "title": "A title for " + letter, "hook": "The line that opens it.",
                          "reason": "It stands alone." if strong else "It needs the minute before.",
                          "scores": scores})
        return {"picks": picks, "kind": self.kind}


def planted_info(data, sentences):
    """For each planted moment: the first six words of its passage and the numbers of its sentences."""
    out = []
    for p in data["planted"]:
        nums = [s["n"] for s in sentences if p["start"] - 0.5 <= s["t"] <= p["end"]]
        first = next(s for s in sentences if s["n"] == nums[0])
        out.append({"label": p["label"], "nums": nums, "first_words": " ".join(first["text"].split()[:6])})
    return out


def run_stand_in(data, **kw):
    ss = sentences_of(data)
    info = planted_info(data, ss)
    model = StandIn([i["first_words"] for i in info], kind=kw.pop("kind", "podcast"),
                    **{k: kw.pop(k) for k in ("decoys", "bad_first_windows", "fail_windows", "rank_scores") if k in kw})
    asker = AJ.make_asker(("http://127.0.0.1:1/v1/chat/completions", "", ""), model)
    AJ.reset_schema_memory()
    out = HL.find_highlights(ss, FPS, asker, **kw)
    return ss, info, model, out


def hits(info, picks):
    """How many planted moments a list of picks holds (at least half of the passage's sentences)."""
    found = []
    for i in info:
        for p in picks:
            inside = [n for n in i["nums"] if p["first"] <= n <= p["last"]]
            if len(inside) * 2 >= len(i["nums"]):
                found.append(i["label"])
                break
    return found


# -- the chapter ------------------------------------------------------------------------------------

class TestChapter(unittest.TestCase):

    def test_packaged_chapter_reads(self):
        ch = HL.load_chapter()
        self.assertEqual(set(HL.KINDS), set(ch["weights"]))
        self.assertEqual(["hook", "alone", "complete", "one_idea", "energy", "tease"], HL._model_criteria(ch))
        self.assertIn("density", ch["criteria"])
        for kind, table in ch["weights"].items():
            self.assertEqual(set(ch["criteria"]), set(table), kind)
        self.assertIn("{criteria}", ch["rank_prompt"])
        self.assertIn("{max_seconds}", ch["find_prompt"])

    def test_the_weights_follow_the_design(self):
        w = HL.load_chapter()["weights"]
        self.assertEqual(max(w["podcast"], key=w["podcast"].get), "alone")
        self.assertGreater(w["podcast"]["energy"], w["tutorial"]["energy"])
        self.assertEqual(max(w["tutorial"], key=w["tutorial"].get), "complete")
        self.assertEqual(w["tutorial"]["one_idea"], max(w["tutorial"].values()))
        self.assertEqual(w["talk"]["hook"], max(w["talk"].values()))
        self.assertEqual(w["talk"]["complete"], max(w["talk"].values()))

    def test_no_promise_of_reach(self):
        ch = HL.load_chapter()
        for text in (ch["find_prompt"], ch["rank_prompt"]):
            self.assertNotIn("viral", text.lower())

    def test_the_chapter_is_not_loaded_into_every_chat(self):
        self.assertNotIn(HL.CHAPTER_NAME, SK._ALWAYS_FILES)
        SK.clear_cache()
        text = SK.get_always_loaded_skills(bpy_version=(5, 3, 0), max_tokens=100000)
        self.assertNotIn("Find prompt", text)
        self.assertNotIn(HL.CHAPTER_NAME, SK.list_loaded_skills())

    def _text(self):
        return HL._chapter_path().read_text(encoding="utf-8")

    def test_broken_chapters_are_named(self):
        text = self._text()
        for broken, needle in (
                (text.replace("## Find prompt", "## Something else"), "Find Prompt"),
                (text.replace("{criteria}", "the criteria"), "{criteria}"),
                (text.replace("- hook: 3\n- alone: 2", "- hook: 3\n- alone: lots", 1), "whole number"),
                (text.replace("- hook: 2\n", "", 1), "miss"),
                (re.sub(r"## Weights.*?## Find prompt", "## Find prompt", text, flags=re.S), "Weights"),
                (re.sub(r"## Criteria.*?## Weights", "## Weights", text, flags=re.S), "Criteria"),
                (text.replace("- tease: 1", "- tease: 12", 1), "0 to 9")):
            with self.assertRaises(HL.ChapterError) as cm:
                HL.parse_chapter(broken)
            self.assertIn(needle, str(cm.exception))

    def test_a_missing_file_is_named(self):
        with self.assertRaises(HL.ChapterError):
            HL.load_chapter("no/such/chapter.md")


# -- the checks -------------------------------------------------------------------------------------

class TestCheck(unittest.TestCase):

    def setUp(self):
        self.ss = simple_sentences(30)
        self.by_n = {s["n"]: s for s in self.ss}

    def check(self, raw, window=None, lo=15.0, hi=60.0):
        return HL.check_candidate(raw, self.by_n, FPS, lo, hi, window)

    def test_a_good_range(self):
        cand, why = self.check({"first": 3, "last": 8, "label": "why we dropped the old rig now please really"})
        self.assertEqual(why, "")
        self.assertEqual((cand["first"], cand["last"]), (3, 8))
        self.assertEqual(cand["start"], self.by_n[3]["start"])
        self.assertEqual(cand["end"], self.by_n[8]["end"])
        self.assertEqual(cand["seconds"], round((self.by_n[8]["end"] - self.by_n[3]["start"]) / FPS, 2))
        self.assertEqual(len(cand["label"].split()), 8)            # a label is cut to eight words

    def test_numbers_must_exist_and_be_in_the_window(self):
        self.assertEqual(self.check({"first": 3, "last": 99})[1], "no_such_sentence")
        self.assertEqual(self.check({"first": 3, "last": 8}, (4, 20))[1], "outside_window")
        self.assertEqual(self.check({"first": 3, "last": 21}, (1, 20))[1], "outside_window")
        self.assertEqual(self.check({"first": 9, "last": 8})[1], "backwards")
        for raw in (None, "S3 to S8", {"first": "x", "last": 2}, {"last": 4}, [3, 8]):
            self.assertEqual(self.check(raw)[1], "not_a_range", raw)

    def test_numbers_as_text_are_read(self):
        cand, _ = self.check({"first": "3", "last": 8.0})
        self.assertEqual((cand["first"], cand["last"]), (3, 8))

    def test_length(self):
        self.assertEqual(self.check({"first": 3, "last": 4})[1], "too_short")      # 11 s
        self.assertEqual(self.check({"first": 1, "last": 20})[1], "too_long")        # 119 s
        self.assertEqual(self.check({"first": 3, "last": 5})[0]["seconds"], 17.0)    # 5+1+5+1+5

    def test_a_little_too_long_is_trimmed_back_to_a_full_stop(self):
        # 11 sentences: 5 s each, 1 s apart = 65 s; 60 s allowed: drop the last one
        cand, why = self.check({"first": 1, "last": 11})
        self.assertEqual(why, "")
        self.assertEqual((cand["first"], cand["last"]), (1, 10))
        self.assertTrue(cand["trimmed"])
        self.assertLessEqual(cand["seconds"], 60.0)

    def test_trimming_needs_a_full_stop(self):
        ss = simple_sentences(30, punct="")
        by_n = {s["n"]: s for s in ss}
        cand, why = HL.check_candidate({"first": 1, "last": 11}, by_n, FPS, 15.0, 60.0)
        self.assertIsNone(cand)
        self.assertEqual(why, "too_long")
        # a closing quote after the full stop still counts
        ss = simple_sentences(30)
        ss[9]["text"] = 'He said "that is it."'
        cand, _ = HL.check_candidate({"first": 1, "last": 11}, {s["n"]: s for s in ss}, FPS, 15.0, 60.0)
        self.assertEqual(cand["last"], 10)

    def test_the_chosen_length(self):
        # a 30 s short: 5 sentences are 29 s; 6 are 35 s (a little over: trimmed back to 5)
        cand, _ = HL.check_candidate({"first": 1, "last": 6}, self.by_n, FPS, 15.0, 30.0)
        self.assertEqual(cand["last"], 5)
        self.assertEqual(HL.check_candidate({"first": 1, "last": 8}, self.by_n, FPS, 15.0, 30.0)[1], "too_long")


# -- the merge --------------------------------------------------------------------------------------

class TestMerge(unittest.TestCase):

    def setUp(self):
        self.ss = simple_sentences(60)
        self.by_n = {s["n"]: s for s in self.ss}

    def cand(self, first, last, window, label=""):
        c = HL._make(self.by_n, first, last, FPS, label)
        c["windows"] = [window]
        return c

    def test_the_same_piece_from_two_windows_is_one(self):
        out = HL.merge_candidates([self.cand(10, 16, 1, "from one"), self.cand(11, 17, 2, "from two")],
                                  self.by_n, FPS, 60.0)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["first"], out[0]["last"]), (10, 17))       # the union fits
        self.assertEqual(out[0]["windows"], [1, 2])

    def test_a_union_that_is_too_long_keeps_the_longer(self):
        out = HL.merge_candidates([self.cand(10, 19, 1, "long one"), self.cand(14, 21, 2, "other")],
                                  self.by_n, FPS, 60.0)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["first"], out[0]["last"]), (10, 19))
        self.assertEqual(out[0]["label"], "long one")

    def test_a_little_overlap_stays_apart(self):
        out = HL.merge_candidates([self.cand(10, 16, 1), self.cand(16, 22, 2)], self.by_n, FPS, 60.0)
        self.assertEqual([(c["first"], c["last"]) for c in out], [(10, 16), (16, 22)])

    def test_chains_merge_and_order_is_playing_order(self):
        out = HL.merge_candidates([self.cand(30, 36, 3), self.cand(10, 16, 1), self.cand(12, 18, 1),
                                   self.cand(14, 20, 2)], self.by_n, FPS, 60.0)
        self.assertEqual([c["first"] for c in out], sorted(c["first"] for c in out))
        self.assertEqual(len(out), 2)

    def test_apart_stays_apart(self):
        out = HL.merge_candidates([self.cand(1, 6, 1), self.cand(20, 26, 1)], self.by_n, FPS, 60.0)
        self.assertEqual(len(out), 2)


# -- the ranking sum --------------------------------------------------------------------------------

class TestSum(unittest.TestCase):

    def test_total_is_the_weighted_share(self):
        w = {"hook": 2, "alone": 3, "density": 1}
        self.assertEqual(HL.total({"hook": 3, "alone": 3, "density": 3}, w), 100.0)
        self.assertEqual(HL.total({"hook": 0, "alone": 0, "density": 0}, w), 0.0)
        self.assertEqual(HL.total({"hook": 3, "alone": 0, "density": 0}, w), 33.3)
        self.assertEqual(HL.total({"hook": 3}, w), 33.3)                 # no score counts as 0
        self.assertEqual(HL.total({"hook": 3}, {"hook": 0}), 0.0)

    def test_the_kind_changes_the_order(self):
        ch = HL.load_chapter()
        story = {"hook": 1, "alone": 3, "complete": 2, "one_idea": 1, "energy": 3, "tease": 0, "density": 2}
        tip = {"hook": 1, "alone": 2, "complete": 3, "one_idea": 3, "energy": 0, "tease": 0, "density": 2}
        self.assertGreater(HL.total(story, ch["weights"]["podcast"]), HL.total(tip, ch["weights"]["podcast"]))
        self.assertGreater(HL.total(tip, ch["weights"]["tutorial"]), HL.total(story, ch["weights"]["tutorial"]))

    def test_density_is_counted(self):
        clean = simple_sentences(6)
        self.assertEqual(HL.density(clean), 2.33)          # no fillers (3), pauses 17% of the time (1), all quotable (3)
        for s in clean:
            s["fillers"], s["pause_before"] = 3, 2.0
        self.assertEqual(HL.density(clean), 1.0)           # 25% fillers (0), pauses 40% (0), quotable (3)
        self.assertEqual(HL.density([]), 0.0)
        tight = simple_sentences(4)
        for s in tight:
            s["pause_before"] = 0.1
        self.assertEqual(HL.density(tight), 3.0)
        rambling = simple_sentences(4, words=40)
        for s in rambling:
            s["pause_before"] = 0.1
        self.assertLess(HL.density(rambling), 3.0)         # long sentences are not quotable


# -- sizes ------------------------------------------------------------------------------------------

class TestSizes(unittest.TestCase):

    def test_groups_follow_the_context(self):
        sizes = [HL.rank_group_size(c) for c in (4096, 8192, 16384, 32768, 131072)]
        self.assertEqual(sizes, sorted(sizes))
        self.assertGreaterEqual(HL.rank_group_size(2048), 1)
        self.assertLessEqual(HL.rank_group_size(131072), HL.MAX_RANK_GROUP)
        self.assertEqual(HL.rank_group_size(None), HL.rank_group_size(32768))
        for ctx in (4096, 8192, 32768):                    # a full group of full candidates fits the context
            tokens = (HL.rank_group_size(ctx) * (HL._candidate_tokens() + HL.ANSWER_TOKENS_PER_CANDIDATE)
                      + SR.INSTRUCTION_TOKENS)
            self.assertLess(tokens, ctx)

    def test_requests_for_an_hour(self):
        ss = sentences_of(TEXTS.podcast())
        r32, r8 = HL.estimate_requests(ss, 32768), HL.estimate_requests(ss, 8192)
        self.assertEqual(r32["find"], len(SR.windows(ss, 32768)))
        self.assertIn(r32["find"], (4, 5))                 # four or five requests on a 32K context
        self.assertGreater(r8["total"], r32["total"])
        self.assertEqual(r32["total"], r32["find"] + r32["rank"])


# -- the written transcripts ------------------------------------------------------------------------

class TestWrittenTranscripts(unittest.TestCase):

    def test_an_hour_with_three_planted_moments(self):
        for make in (TEXTS.podcast, TEXTS.tutorial):
            d = make()
            self.assertGreaterEqual(d["minutes"], 58)
            self.assertGreater(len(d["words"]), 7000)
            self.assertEqual(len(d["planted"]), 3)
            for p in d["planted"]:
                self.assertTrue(15 <= p["end"] - p["start"] <= 60, p)
            self.assertEqual(make()["words"], d["words"])           # the same text every time
            ss = sentences_of(d)
            self.assertGreater(len(ss), 300)
            self.assertEqual(len(planted_info(d, ss)), 3)

    def test_the_weak_stretches_are_weak(self):
        ss = sentences_of(TEXTS.podcast())
        info = planted_info(TEXTS.podcast(), ss)
        planted_n = {n for i in info for n in i["nums"]}
        weak = [s for s in ss if s["n"] not in planted_n]
        self.assertGreater(sum(1 for s in weak if s["fillers"]) / len(weak), 0.2)    # lots of ums


# -- the whole run with the stand-in -----------------------------------------------------------------

class TestPipeline(unittest.TestCase):

    def check_hour(self, make, kind):
        data = make()
        ss, info, model, out = run_stand_in(data, kind=kind, count=5, context=32768)
        picks = out["picks"]
        by_n = {s["n"]: s for s in ss}
        self.assertLessEqual(len(picks), 5)
        found = hits(info, picks)
        self.assertEqual(len(found), 3, (found, [(p["first"], p["last"]) for p in picks]))
        for p in picks:                                      # never inside a sentence, never a time from the model
            self.assertEqual(p["start"], by_n[p["first"]]["start"])
            self.assertEqual(p["end"], by_n[p["last"]]["end"])
            self.assertTrue(15.0 <= p["seconds"] <= 60.0, p["seconds"])
            self.assertEqual(p["first_word"], by_n[p["first"]]["first"])
            self.assertEqual(p["last_word"], by_n[p["last"]]["last"])
        spans = sorted((p["start"], p["end"]) for p in picks)
        for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
            self.assertLessEqual(a1, b0)                    # no two picks share frames
        self.assertEqual(picks[0]["scores"]["hook"], 3)
        self.assertEqual(out["report"]["kind"], kind)
        return out, model, found

    def test_podcast(self):
        out, model, _ = self.check_hour(TEXTS.podcast, "podcast")
        r = out["report"]
        self.assertEqual(r["windows"], len(SR.windows(sentences_of(TEXTS.podcast()), 32768)))
        self.assertEqual(model.find_count, r["windows"])
        self.assertGreaterEqual(r["raw"], 8)
        self.assertGreater(r["rejected"].get("too_long", 0), 0)               # the whole-window decoy
        self.assertGreater(r["rejected"].get("no_such_sentence", 0) + r["rejected"].get("outside_window", 0), 0)
        self.assertEqual(r["windows_failed"], 0)
        self.assertEqual(r["requests"], model.find_count + model.rank_count)
        self.assertEqual(r["messages"], [])

    def test_tutorial(self):
        self.check_hour(TEXTS.tutorial, "tutorial")

    def test_six_moments_in_the_top_five(self):
        """The done-when of #613 on the stand-in: at least four of the six in the top five (here all)."""
        found = 0
        for make, kind in ((TEXTS.podcast, "podcast"), (TEXTS.tutorial, "tutorial")):
            data = make()
            _ss, info, _m, out = run_stand_in(data, kind=kind, count=5)
            found += len(hits(info, out["picks"][:5]))
        self.assertGreaterEqual(found, 4)

    def test_a_moment_between_two_windows_is_found_once(self):
        data = TEXTS.podcast()
        ss, info, model, out = run_stand_in(data, context=8192, decoys=False)    # small windows, many borders
        self.assertEqual(len(hits(info, out["picks"])), 3)
        firsts = [p["first"] for p in out["picks"]]
        self.assertEqual(len(firsts), len(set(firsts)))

    def test_a_wrong_first_answer_is_retried(self):
        _ss, info, model, out = run_stand_in(TEXTS.podcast(), bad_first_windows=2, decoys=False)
        r = out["report"]
        self.assertEqual(r["retries"], 2)
        self.assertEqual(r["windows_failed"], 0)
        self.assertEqual(r["requests"], model.find_count + model.rank_count)
        self.assertEqual(len(hits(info, out["picks"])), 3)

    def test_a_part_that_cannot_be_read_is_counted_and_skipped(self):
        # window 2 never answers well: 2 requests for it (the retry too), the others go on
        _ss, info, model, out = run_stand_in(TEXTS.podcast(), fail_windows={2, 3}, decoys=False)
        r = out["report"]
        self.assertEqual(r["windows_failed"], 1)
        self.assertIn("1 of {:d} parts could not be read.".format(r["windows"]), r["messages"])
        self.assertGreaterEqual(len(out["picks"]), 1)

    def test_the_model_sees_numbers_and_the_chosen_length(self):
        _ss, _info, model, out = run_stand_in(TEXTS.podcast(), max_seconds=30, decoys=False, count=3)
        system = model.calls[0][1][0]["content"]
        self.assertIn("at most 30 seconds", system)
        self.assertIn("at least 15 seconds", system)
        self.assertIn("S1 [", model.calls[0][1][1]["content"])
        for p in out["picks"]:
            self.assertLessEqual(p["seconds"], 30.0)

    def test_the_schema_goes_with_the_request(self):
        _ss, _info, model, _out = run_stand_in(TEXTS.podcast(), decoys=False)
        find = [c for c in model.calls if c[0] == "find"]
        rank = [c for c in model.calls if c[0] == "rank"]
        self.assertTrue(find and rank)
        self.assertEqual(find[0][2]["response_format"]["json_schema"]["name"], "find")
        self.assertEqual(rank[0][2]["response_format"]["json_schema"]["name"], "rank")
        letters = rank[0][2]["response_format"]["json_schema"]["schema"]["properties"]["picks"]["items"]["properties"]["id"]
        self.assertEqual(letters["enum"][0], "A")

    def test_count_limits_the_picks(self):
        _ss, _info, _m, out = run_stand_in(TEXTS.podcast(), count=2)
        self.assertEqual(len(out["picks"]), 2)

    def test_picks_are_best_first(self):
        _ss, _info, _m, out = run_stand_in(TEXTS.podcast(), count=5)
        totals = [p["total"] for p in out["picks"]]
        self.assertEqual(totals, sorted(totals, reverse=True))


class TestGroupsAndKind(unittest.TestCase):

    def candidates(self, ss, n):
        by_n = {s["n"]: s for s in ss}
        cands = []
        for k in range(n):
            first = 1 + k * 4
            c = HL._make(by_n, first, first + 3, FPS, "piece %d" % k)
            c["windows"] = [1]
            cands.append(c)
        return by_n, cands

    def scripted(self, score_of, kind="talk"):
        calls = []

        def transport(url, messages, tools, key, model, **kw):
            body = messages[1]["content"]
            calls.append(messages)
            clips = re.findall(r"^Clip ([A-Z]+):\nword.*?(\d+)", body, re.M)
            picks = []
            for letter, text in re.findall(r"^Clip ([A-Z]+):\n(.*?)(?=\n\nClip |\Z)", body, re.S | re.M):
                picks.append({"id": letter, "title": "T", "hook": "H", "reason": "R", "scores": score_of(text)})
            return reply({"picks": picks, "kind": kind})
        return transport, calls

    def test_many_candidates_are_ranked_in_groups_and_the_winners_once_more(self):
        ss = simple_sentences(200)
        by_n, cands = self.candidates(ss, 40)

        def score_of(text):
            return {"hook": 1, "alone": 1, "complete": 1, "one_idea": 1, "energy": 1, "tease": 1}
        transport, calls = self.scripted(score_of)
        ask = AJ.make_asker(("http://127.0.0.1:1/v1/chat/completions", "", ""), transport)
        stats = {}
        ranked, kind = HL.rank(HL._Run(ask, None, None), HL.load_chapter(), cands, ss, by_n, count=5, context=8192,
                               stats=stats)
        group = HL.rank_group_size(8192)
        self.assertGreater(len(cands), group)
        self.assertGreater(len(calls), 2)                  # groups, then the winners together
        for messages in calls:                             # every request fits the context
            size = sum(len(m["content"]) for m in messages) / 3.5
            self.assertLess(size, 8192 - 1000)
        self.assertLess(len(ranked), len(cands))           # the group losers are out
        self.assertGreaterEqual(len(ranked), 5)
        self.assertEqual(kind, "talk")

    def test_grouped_ranking_comes_down_to_one_final_group(self):
        ss = simple_sentences(400)
        for context in (8192, 4096):
            for n in (10, 15, 30, 40):
                by_n, cands = self.candidates(ss, n)
                group = HL.rank_group_size(context)
                if n <= group:
                    continue
                transport, calls = self.scripted(lambda text: {"hook": 1, "alone": 1, "complete": 1,
                                                              "one_idea": 1, "energy": 1, "tease": 1})
                ask = AJ.make_asker(("http://127.0.0.1:1/v1/chat/completions", "", ""), transport)
                chunk_ids = []
                real = HL._score_group

                def spy(run, chapter, ch, *a, _real=real, **kw):
                    chunk_ids.append({c["id"] for c in ch})
                    return _real(run, chapter, ch, *a, **kw)
                with mock.patch.object(HL, "_score_group", spy):
                    ranked, _kind = HL.rank(HL._Run(ask, None, None), HL.load_chapter(), cands, ss, by_n, count=5,
                                            context=context)
                sizes = [len(re.findall(r"^Clip [A-Z]+:", c[1]["content"], re.M)) for c in calls]
                label = "context %d, %d candidates, requests %s" % (context, n, sizes)
                self.assertTrue(all(z <= group for z in sizes), label)
                self.assertLessEqual(len(calls), HL.estimate_requests(ss, context, 5, n)["rank"], label)
                last = sizes[-1]
                self.assertEqual(last, len(ranked), label)       # the last request holds every survivor
                times: dict[int, int] = {}                      # how often each id was scored = its round
                by_round: dict[int, set] = {}
                for ids in chunk_ids:
                    r = times.get(next(iter(ids)), 0)
                    for i in ids:
                        times[i] = times.get(i, 0) + 1
                    by_round.setdefault(r, set()).update(ids)
                totals = [len(by_round[r]) for r in sorted(by_round)]
                self.assertEqual(totals[0], n, label)
                for before, after in zip(totals, totals[1:]):
                    self.assertLess(after, before, label)
                self.assertLessEqual(totals[-1], group, label)

    def test_unscored_pieces_sink_and_are_labelled(self):
        ss = simple_sentences(60)
        by_n, cands = self.candidates(ss, 4)

        def transport(url, messages, tools, key, model, **kw):
            return reply({"picks": [{"id": "B", "scores": {"hook": 3, "alone": 3, "complete": 3, "one_idea": 3,
                                                           "energy": 3, "tease": 3}}]})
        ask = AJ.make_asker(("http://127.0.0.1:1/v1/chat/completions", "", ""), transport)
        ranked, _kind = HL.rank(ask, HL.load_chapter(), cands, ss, by_n, kind="talk")
        self.assertEqual(len(ranked), 4)
        self.assertFalse(ranked[0]["unscored"])
        self.assertEqual(ranked[0]["first"], cands[1]["first"])
        self.assertTrue(all(p["unscored"] for p in ranked[1:]))
        self.assertEqual(ranked[1]["title"], "piece 0")                        # the label stands in for the title
        self.assertEqual(ranked[1]["reason"], "Not scored by the model.")

    def test_auto_asks_for_the_kind_a_chosen_kind_does_not(self):
        ss = simple_sentences(60)
        by_n, cands = self.candidates(ss, 3)
        transport, calls = self.scripted(lambda t: {"hook": 2, "alone": 2, "complete": 2, "one_idea": 2,
                                                    "energy": 2, "tease": 2}, kind="tutorial")
        ask = AJ.make_asker(("http://127.0.0.1:1/v1/chat/completions", "", ""), transport)
        _r, kind = HL.rank(ask, HL.load_chapter(), cands, ss, by_n, kind="auto")
        self.assertEqual(kind, "tutorial")
        self.assertIn("what kind of video this is", calls[0][0]["content"])
        self.assertIn("judging from its opening", calls[0][0]["content"])
        calls.clear()
        _r, kind = HL.rank(ask, HL.load_chapter(), cands, ss, by_n, kind="talk")
        self.assertEqual(kind, "talk")                                          # the dialog wins over the answer
        self.assertNotIn("what kind of video this is", calls[0][0]["content"])

    def test_an_unreadable_rank_answer_is_counted(self):
        ss = simple_sentences(60)
        by_n, cands = self.candidates(ss, 3)
        ask = AJ.make_asker(("http://127.0.0.1:1/v1/chat/completions", "", ""),
                            lambda *a, **k: reply("no idea"))
        stats = {}
        ranked, kind = HL.rank(ask, HL.load_chapter(), cands, ss, by_n, stats=stats)
        self.assertEqual(stats["rank_failed"], 1)
        self.assertTrue(all(p["unscored"] for p in ranked))
        self.assertEqual(kind, HL.DEFAULT_KIND)


class TestEdges(unittest.TestCase):

    def ask(self, transport):
        return AJ.make_asker(("http://127.0.0.1:1/v1/chat/completions", "", ""), transport)

    def test_no_speech(self):
        out = HL.find_highlights([], FPS, self.ask(lambda *a, **k: reply({})))
        self.assertEqual(out["picks"], [])
        self.assertEqual(out["report"]["messages"], ["Not enough speech to pick from."])
        short = simple_sentences(2)                                             # 10 s of speech
        out = HL.find_highlights(short, FPS, self.ask(lambda *a, **k: reply({})))
        self.assertEqual(out["report"]["messages"], ["Not enough speech to pick from."])
        self.assertEqual(out["report"]["requests"], 0)

    def test_a_context_that_is_too_small(self):
        out = HL.find_highlights(simple_sentences(30), FPS, self.ask(lambda *a, **k: reply({})), context=1500)
        self.assertEqual(out["picks"], [])
        self.assertIn("too small", out["report"]["messages"][0])

    def test_nothing_good(self):
        out = HL.find_highlights(simple_sentences(30), FPS,
                                 self.ask(lambda *a, **k: reply({"candidates": []})))
        self.assertEqual(out["picks"], [])
        self.assertEqual(out["report"]["requests"], 1)
        self.assertIn("Nothing in this edit", out["report"]["messages"][0])

    def test_no_engine_goes_up(self):
        with self.assertRaises(AJ.Unavailable):
            HL.find_highlights(simple_sentences(30), FPS, self.ask(lambda *a, **k: None))

    def test_a_broken_chapter_goes_up(self):
        with self.assertRaises(HL.ChapterError):
            HL.find_highlights(simple_sentences(30), FPS, self.ask(lambda *a, **k: reply({})),
                               chapter=HL.parse_chapter("# x"))

    def test_stop(self):
        data = TEXTS.podcast()
        ss = sentences_of(data)
        model = StandIn([planted_info(data, ss)[0]["first_words"]], decoys=False)
        stop = {"on": False}
        seen = []

        def should_stop():
            return stop["on"]

        def progress(text):
            seen.append(text)
            if len(seen) == 1:
                stop["on"] = True
        out = HL.find_highlights(ss, FPS, AJ.make_asker(("http://127.0.0.1:1/v1/chat/completions", "", ""), model),
                                 should_stop=should_stop, progress=progress)
        self.assertTrue(out["report"]["stopped"])
        self.assertEqual(model.find_count, 1)                                    # no request after the stop
        self.assertEqual(model.rank_count, 0)
        self.assertIn("Stopped", " ".join(out["report"]["messages"]))
        self.assertTrue(seen[0].startswith("Reading part 1 of"))

    def test_extra_fields_and_more_than_three_are_ignored(self):
        ss = simple_sentences(30)
        cands = [{"first": 2 + k, "last": 5 + k, "label": "x", "extra": 1} for k in range(6)]

        def transport(url, messages, tools, key, model, **kw):
            if messages[1]["content"].startswith("Transcript part:"):
                return reply({"candidates": cands, "note": "ignored"})
            return reply({"picks": []})
        out = HL.find_highlights(ss, FPS, self.ask(transport))
        self.assertEqual(out["report"]["raw"], HL.MAX_CANDIDATES_PER_WINDOW)


if __name__ == "__main__":
    unittest.main()
