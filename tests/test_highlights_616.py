# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Highlights H6 (#616): the chat tools as a background job, and the External Agent path.

The model is a STAND-IN (the one of ``test_highlights_613``), never a real model: these tests prove the
plumbing, not the taste of any model.

1. ``highlight_agent.py`` (bpy-free): the agent's own picks go through the checks of the search (numbers that
   exist, whole sentences, the length, no shared frames), the scores are whole numbers 0 to 3 and the density is
   counted, never taken; ``parse_steps``, ``resolve_pick``, the prepared message.
2. ``highlight_tools.py`` with a stand-in ``bpy``: ``suggest_highlights`` answers at once with a job id of the
   long-jobs machinery, the search runs on the panel's worker with a stand-in model, ``get_job_status`` shows the
   progress and then the picks, and they are on the scene; ``propose_highlights`` lands in the same list;
   ``make_short`` builds without switching the artist's window; the panel's button hands the job to an
   External Agent (a message) or an External Harness (the clipboard).
3. The tools are in the tool files, the vse domain, the docs and the job row.

Run with::

    python -m unittest tests.test_highlights_616 -v
"""

__all__ = ()

import ast
import importlib
import sys
import types
import unittest
from pathlib import Path

import highlights_texts_613 as TEXTS
import test_highlight_picks_614 as P614
import test_highlights_613 as H613
import test_wand_remove_silences as W

HA = W._import("highlight_agent")
HP = W._import("highlight_picks")
HL = W._import("highlights")
AJ = W._import("ask_json")
LJ = W._import("long_jobs")
FPS = H613.FPS
_ROOT = Path(__file__).resolve().parents[1]
_ADDON = _ROOT / "addon" / "bfa_coworker"
_TOOLS = _ROOT / "mcp" / "blmcp" / "tools"
CHAPTER = HL.load_chapter()


def proposal(first, last, **extra):
    return dict({"first": first, "last": last, "title": "The rig we threw away", "hook": "Six months, gone.",
                 "reason": "A story with a payoff.",
                 "scores": {"hook": 3, "alone": 3, "complete": 2, "one_idea": 3, "energy": 2, "tease": 1}}, **extra)


# -- 1. the checks --------------------------------------------------------------------------------------

class TestProposalsGoThroughTheChecks(unittest.TestCase):

    def setUp(self):
        self.ss = H613.simple_sentences(40, seconds=5.0)       # a sentence is 5 s, a pause 1 s: 4 of them are 23 s

    def check(self, raw, **kw):
        return HA.check_proposals(raw, self.ss, FPS, CHAPTER, **kw)

    def test_a_good_pick_is_stored_in_the_form_the_search_returns(self):
        picks, refused = self.check([proposal(3, 6)])
        self.assertEqual(refused, [])
        pick = picks[0]
        self.assertEqual((pick["first"], pick["last"]), (3, 6))
        self.assertEqual((pick["start"], pick["end"]), (self.ss[2]["start"], self.ss[5]["end"]))
        self.assertEqual(pick["first_word"], self.ss[2]["first"])
        self.assertEqual(pick["last_word"], self.ss[5]["last"])
        self.assertEqual((pick["title"], pick["hook"]), ("The rig we threw away", "Six months, gone."))
        self.assertFalse(pick["unscored"])
        stored = HP.from_found(picks, self.ss)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["state"], "ok")
        self.assertEqual(HP.loads(HP.dumps({"v": 1, "picks": stored, "markers": [], "markers_on": False,
                                            "found": {}}))["picks"][0]["title"], "The rig we threw away")

    def test_the_numbers_must_exist_and_be_in_order(self):
        _picks, refused = self.check([proposal(3, 999), proposal(9, 4), {"first": "x", "last": 3}, "nonsense", None])
        self.assertEqual([r["why"] for r in refused], ["no_such_sentence", "backwards", "not_a_range", "not_a_range",
                                                       "not_a_range"])
        self.assertTrue(all(r["message"] for r in refused))
        self.assertEqual([r["index"] for r in refused], [0, 1, 2, 3, 4])

    def test_the_length_is_checked_like_the_searchs(self):
        _picks, refused = self.check([proposal(1, 2), proposal(1, 20)])           # 11 s, and 113 s
        self.assertEqual([r["why"] for r in refused], ["too_short", "too_long"])
        picks, refused = self.check([proposal(1, 20)], max_seconds=200)           # never above 60 s, whatever is asked
        self.assertEqual((picks, refused[0]["why"]), ([], "too_long"))

    def test_a_range_a_little_too_long_is_cut_back_to_a_full_stop(self):
        picks, refused = self.check([proposal(1, 7)], max_seconds=36)            # 7 sentences = 41 s: back to 6 (35 s)
        self.assertEqual(refused, [])
        self.assertEqual((picks[0]["first"], picks[0]["last"]), (1, 6))
        self.assertTrue(picks[0]["trimmed"])

    def test_no_two_picks_share_frames_and_taken_ranges_count(self):
        picks, refused = self.check([proposal(3, 6), proposal(5, 8), proposal(9, 12)])
        self.assertEqual([(p["first"], p["last"]) for p in sorted(picks, key=lambda p: p["first"])], [(3, 6), (9, 12)])
        self.assertEqual([r["why"] for r in refused], ["overlaps"])
        picks, refused = self.check([proposal(3, 6)], taken=[(self.ss[4]["start"], self.ss[7]["end"])])
        self.assertEqual((picks, refused[0]["why"]), ([], "overlaps"))

    def test_scores_are_whole_numbers_on_the_chapters_criteria_and_density_is_counted(self):
        picks, _ = self.check([proposal(3, 6, scores={"hook": 9, "alone": -2, "energy": 2.6, "density": 3, "mood": 3,
                                                      "tease": True, "complete": "3"})])
        scores = picks[0]["scores"]
        self.assertEqual({k: v for k, v in scores.items() if k != "density"}, {"hook": 3, "alone": 0, "energy": 3})
        counted = HL.density([s for s in self.ss if 3 <= s["n"] <= 6])
        self.assertEqual(scores["density"], counted)          # the agent's own 3 is not taken

    def test_the_total_is_added_up_in_code_from_the_kind_of_videos_weights(self):
        mine = dict(hook=3, alone=3, complete=3, one_idea=3, energy=3, tease=3)
        picks, _ = self.check([proposal(3, 6, scores=mine)], kind="tutorial")
        weights = CHAPTER["weights"]["tutorial"]
        want = HL.total(dict(mine, density=picks[0]["scores"]["density"]), weights)
        self.assertEqual((picks[0]["total"], picks[0]["kind"]), (want, "tutorial"))
        auto, _ = self.check([proposal(3, 6, scores=mine)], kind="auto")
        self.assertEqual(auto[0]["kind"], HL.DEFAULT_KIND)

    def test_without_scores_it_is_listed_after_the_scored_ones(self):
        picks, _ = self.check([proposal(3, 6, scores=None), proposal(9, 12, scores={"hook": 1}), proposal(15, 18)])
        self.assertEqual([(p["first"], p["unscored"]) for p in picks], [(15, False), (9, False), (3, True)])
        self.assertEqual(picks[-1]["total"], 0.0)
        self.assertEqual(picks[-1]["reason"], "A story with a payoff.")

    def test_a_pick_with_no_title_gets_a_placeholder_from_its_label(self):
        picks, _ = self.check([{"first": 3, "last": 6}])
        self.assertTrue(HP.from_found(picks, self.ss)[0]["title"])

    def test_texts_are_cut_and_cleaned(self):
        picks, _ = self.check([proposal(3, 6, title="T" * 300, hook="h\x00 " * 100, reason="r " * 400)])
        self.assertLessEqual(len(picks[0]["title"]), HL.TITLE_CHARS + 3)
        self.assertLessEqual(len(picks[0]["hook"]), HL.HOOK_CHARS + 3)
        self.assertLessEqual(len(picks[0]["reason"]), HL.REASON_CHARS + 3)
        self.assertNotIn("\x00", picks[0]["hook"])

    def test_not_a_list_and_too_many(self):
        picks, refused = self.check("all of it")
        self.assertEqual((picks, len(refused)), ([], 1))
        many = [proposal(1 + 2 * (k % 17), 4 + 2 * (k % 17)) for k in range(30)]
        _picks, refused = self.check(many)
        self.assertIn("too_many", [r["why"] for r in refused])

    def test_a_pick_found_by_the_agent_on_the_written_podcast_is_placed_like_a_found_one(self):
        data = TEXTS.podcast()
        ss = H613.sentences_of(data)
        info = H613.planted_info(data, ss)[0]
        first, last = info["nums"][0], info["nums"][-1]
        picks, refused = HA.check_proposals([proposal(first, last)], ss, FPS, CHAPTER, kind="podcast")
        self.assertEqual((len(picks), refused), (1, []))
        self.assertEqual(len(HP.from_found(picks, ss)), 1)
        self.assertLessEqual(picks[0]["seconds"], 60.0)


class TestArguments(unittest.TestCase):

    def test_steps(self):
        self.assertEqual(HA.parse_steps(None), ({k: True for k in HA.STEP_NAMES}, ""))
        self.assertEqual(HA.parse_steps([])[0], {k: False for k in HA.STEP_NAMES})
        self.assertEqual(HA.parse_steps(["Captions", "hook"])[0],
                         {"silences": False, "vertical": False, "captions": True, "hook": True})
        self.assertEqual(HA.parse_steps("silences, vertical")[0]["vertical"], True)
        self.assertEqual(HA.parse_steps({"hook": True})[0], {"silences": False, "vertical": False, "captions": False,
                                                              "hook": True})
        for bad in (["zoom"], {"zoom": True}, 5):
            steps, why = HA.parse_steps(bad)
            self.assertIsNone(steps)
            self.assertIn("silences", why)

    def test_a_pick_is_found_by_id_by_place_or_by_title(self):
        picks = [{"id": "ab12", "title": "The rig"}, {"id": "cd34", "title": "A Story"}, {"id": "2", "title": "Odd"}]
        self.assertEqual(HA.resolve_pick(picks, "cd34")[0]["id"], "cd34")
        self.assertEqual(HA.resolve_pick(picks, "1")[0]["id"], "ab12")
        self.assertEqual(HA.resolve_pick(picks, "2")[0]["id"], "2")                 # an id wins over a place
        self.assertEqual(HA.resolve_pick(picks, "a story")[0]["id"], "cd34")
        for bad in ("9", "nothing", "", None):
            found, why = HA.resolve_pick(picks, bad)
            self.assertIsNone(found)
            self.assertIn("ab12", why)
        self.assertIn("suggest_highlights", HA.resolve_pick([], "x")[1])

    def test_the_prepared_message_names_the_tools_and_the_options(self):
        text = HA.prepared_message("tutorial", 30, 4)
        for needle in ("read_edit_sentences", "propose_highlights", "30 seconds", "4 pieces", "tutorial"):
            self.assertIn(needle, text)
        self.assertNotIn("make_short(", text)
        self.assertIn("Do not build a short yet", text)
        self.assertIn("first minutes", HA.prepared_message("auto", 60, 5))


# -- 2. the tools, with a stand-in bpy ------------------------------------------------------------------

class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _load(bpy):
    """highlight_picks_ops and highlight_tools, imported against the stand-in bpy."""
    names = ("bpy", "bpy.props", "bpy.types")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"bpy": bpy, "bpy.props": bpy.props, "bpy.types": bpy.types})
    try:
        W._package()
        for mod in ("highlight_picks_ops", "highlight_tools", "scene_reader_ops", "wand.sequencer", "wand.scene"):
            sys.modules.pop(W._PKG + "." + mod, None)
            parent, _dot, leaf = (W._PKG + "." + mod).rpartition(".")
            if hasattr(sys.modules[parent], leaf):      # `from . import x` would hand back the old one
                delattr(sys.modules[parent], leaf)
        return W._import("highlight_tools")
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


class _Base(unittest.TestCase):

    def setUp(self):
        self.bpy = P614._fake_bpy()
        self.tools = _load(self.bpy)
        self.ops = self.tools.ops
        self.scene = P614._Scene()
        self.bpy.data.scenes["Scene"] = self.scene
        self.data = TEXTS.podcast()
        self.ss = H613.sentences_of(self.data)
        self.reader_sents = self.ss
        self.reader = lambda scene, context=None: {"sentences": self.reader_sents, "report": {"messages": ["No speech."]},
                                                   "windows": []}
        self._patch(self.ops.scene_reader_ops, "read", self.reader)
        self._patch(self.ops.scene_reader_ops, "context_tokens", lambda: 32768)
        self._patch(self.ops.scene_reader_ops, "_ops", lambda: types.SimpleNamespace(_tool_scene=lambda: self.scene))
        self.undo = []
        self._patch(self.ops._wand, "_push_undo", self.undo.append)
        LJ.clear()
        self.addCleanup(LJ.clear)
        AJ.STUB = None
        self.addCleanup(setattr, AJ, "STUB", None)
        self.addCleanup(self._stop_worker)

    def _patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def _stop_worker(self):
        job = self.ops._JOB
        if job["stop"] is not None:
            job["stop"].set()
        if job["thread"] is not None:
            job["thread"].join(30)

    def stand_in(self):
        info = H613.planted_info(self.data, self.ss)
        model = H613.StandIn([i["first_words"] for i in info], kind="podcast")
        AJ.reset_schema_memory()
        AJ.STUB = model
        return model, info

    def finish_search(self):
        job = self.ops._JOB
        job["thread"].join(60)
        self.assertFalse(job["thread"].is_alive())


class TestSuggestHighlights(_Base):

    def test_it_answers_at_once_with_a_job_id_and_the_search_runs_as_a_long_job(self):
        _model, _info = self.stand_in()
        out = self.tools.tool_suggest_highlights("podcast", 60, 5)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["job_id"].startswith("job-"))
        self.assertEqual(out["sentences"], len(self.ss))
        self.assertIn("get_job_status", out["message"])
        self.assertTrue(out["privacy"])
        st = LJ.status(out["job_id"])
        self.assertEqual((st["kind"], st["label"]), ("HIGHLIGHTS", "Find highlights"))
        if st["state"] == "running":
            self.assertTrue(LJ.row_text(LJ.running()[0]).startswith("Finding highlights"))
        self.assertEqual(self.ops._JOB["source"], "agent")

    def test_the_job_ends_with_the_picks_which_are_on_the_scene_and_in_the_panels_list(self):
        _model, info = self.stand_in()
        out = self.tools.tool_suggest_highlights("podcast", 60, 5)
        self.finish_search()
        for _ in range(3):
            LJ.poll()
            if LJ.status(out["job_id"])["state"] != "running":
                break
            self.ops._job_tick()
        st = LJ.status(out["job_id"])
        self.assertEqual(st["state"], "done", st)
        picks = st["result"]["picks"]
        self.assertTrue(3 <= len(picks) <= 5, len(picks))
        self.assertEqual({p["id"] for p in picks}, {p["id"] for p in self.ops.load(self.scene)["picks"]})
        for key in ("n", "id", "title", "hook", "reason", "seconds", "at", "total", "state"):
            self.assertIn(key, picks[0])
        self.assertLessEqual(max(p["seconds"] for p in picks), 60.0)
        self.assertIn("make_short", st["result"]["next"])
        self.assertTrue(st["result"]["report"]["requests"] >= 2)
        found = [i for i in info if any(self._holds(p, i) for p in self.ops.load(self.scene)["picks"])]
        self.assertGreaterEqual(len(found), 2)            # a stand-in, so only the plumbing: planted moments came through
        self.assertEqual(self.undo, [])                   # an agent's search ends after its turn: no step of its own

    def _holds(self, pick, info):
        start = next(s for s in self.ss if s["n"] == info["nums"][0])["start"]
        return pick["start"] <= start < pick["end"]

    def test_the_panel_shows_the_agents_search_and_refuses_a_second_one(self):
        self.stand_in()
        self.tools.tool_suggest_highlights("auto", 30, 3)
        self.assertTrue(self.ops._JOB["running"])
        second = self.tools.tool_suggest_highlights("auto", 30, 3)
        self.assertEqual(second["status"], "error")
        self.assertTrue(second.get("job_running"))
        self.assertIn("already running", second["message"])
        again = self.tools.tool_propose_highlights([proposal(3, 6)])
        self.assertEqual(again["status"], "error")
        self.assertIn("running", again["message"])
        short = self.tools.tool_make_short("1")
        self.assertEqual(short["status"], "error")
        self.finish_search()

    def test_a_stop_in_the_panel_ends_the_job_with_what_was_found(self):
        self.stand_in()
        out = self.tools.tool_suggest_highlights("podcast", 60, 5)
        self.ops.BFACW_OT_highlights_stop().execute(None)
        self.finish_search()
        self.ops._job_tick()
        LJ.poll()
        self.assertIn(LJ.status(out["job_id"])["state"], ("done", "error"))

    def test_the_arguments_are_checked_and_clamped(self):
        self.assertEqual(self.tools.tool_suggest_highlights("vlog")["status"], "error")
        self.assertEqual(self.tools.tool_suggest_highlights("auto", "soon")["status"], "error")
        self.stand_in()
        seen = []
        self._patch(self.ops, "start_search", lambda scene, seconds, kind, count, source="panel":
                    seen.append((seconds, kind, count, source)) or 7)
        out = self.tools.tool_suggest_highlights("TALK", 500, 99)
        self.assertEqual(seen, [(60.0, "talk", 10, "agent")])
        self.assertEqual(out["status"], "ok")
        self.tools.tool_suggest_highlights("auto", 1, 0)
        self.assertEqual(seen[-1][::2], (15.0, 1))

    def test_with_an_external_agent_it_says_to_read_and_propose(self):
        def external(*_a, **_k):
            raise AJ.Unavailable("Find Highlights asks the model directly; an External Agent or Harness runs its own.",
                                 "external")
        self._patch(AJ, "resolve_asker", external)
        out = self.tools.tool_suggest_highlights("auto")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["mode"], "read_and_propose")
        self.assertIn("read_edit_sentences", out["message"])
        self.assertIn("propose_highlights", out["message"])
        self.assertFalse(self.ops._JOB["running"])
        self.assertEqual(LJ.running(), [])

    def test_no_speech_and_no_strips_say_what_to_do(self):
        self.reader_sents = []
        out = self.tools.tool_suggest_highlights("auto")
        self.assertIn("No speech", out["message"])
        self.scene.sequence_editor = None
        self.assertIn("no strips", self.tools.tool_suggest_highlights("auto")["message"])

    def test_a_file_load_ends_the_job_and_the_checker_says_so(self):
        self.stand_in()
        out = self.tools.tool_suggest_highlights("auto", 60, 3)
        self.finish_search()
        self.ops._reset_job()
        LJ.poll()
        st = LJ.status(out["job_id"])
        self.assertEqual(st["state"], "cancelled")
        self.assertIn("dropped", st["message"])


class TestProposeHighlights(_Base):

    def setUp(self):
        super().setUp()
        info = H613.planted_info(self.data, self.ss)
        self.moment = proposal(info[0]["nums"][0], info[0]["nums"][-1])
        self.second = proposal(info[1]["nums"][0], info[1]["nums"][-1], title="A second story")

    def test_the_proposals_land_in_the_same_picks_list(self):
        out = self.tools.tool_propose_highlights([self.moment, self.second], kind="podcast",
                                                 version=HA.hp.sr.version(self.ss))
        self.assertEqual((out["status"], out["added"], out["refused"]), ("ok", 2, []))
        store = self.ops.load(self.scene)
        self.assertEqual([p["title"] for p in store["picks"]].count("A second story"), 1)
        self.assertEqual(len(store["picks"]), 2)
        self.assertEqual({r["id"] for r in out["picks"]}, {p["id"] for p in store["picks"]})
        self.assertEqual(store["found"]["kind"], "podcast")
        self.assertEqual(self.undo, ["Propose Highlights"])
        for p in store["picks"]:
            self.assertEqual((p["state"], p["sig"]), ("ok", HA.hp.sr.version(self.ss)))

    def test_a_second_call_adds_and_replace_starts_over(self):
        self.tools.tool_propose_highlights([self.moment])
        out = self.tools.tool_propose_highlights([self.second])
        self.assertEqual(len(self.ops.load(self.scene)["picks"]), 2)
        self.assertEqual(len(out["picks"]), 2)
        again = self.tools.tool_propose_highlights([self.moment])                 # the same frames: refused
        self.assertEqual((again["status"], again["refused"][0]["why"]), ("refused", "overlaps"))
        self.assertEqual(len(self.ops.load(self.scene)["picks"]), 2)
        out = self.tools.tool_propose_highlights([self.moment], replace=True)
        self.assertEqual(len(self.ops.load(self.scene)["picks"]), 1)
        self.assertEqual(out["added"], 1)

    def test_the_refused_are_listed_with_the_reason_and_the_rest_is_kept(self):
        out = self.tools.tool_propose_highlights([self.moment, proposal(5, 99999)])
        self.assertEqual((out["status"], out["added"]), ("ok", 1))
        self.assertEqual(out["refused"][0]["why"], "no_such_sentence")
        self.assertIn("1 refused", out["message"])

    def test_nothing_that_passes_leaves_the_list_as_it_was(self):
        self.tools.tool_propose_highlights([self.moment])
        before = self.scene[HA.hp.PROP]
        out = self.tools.tool_propose_highlights([proposal(5, 3)])
        self.assertEqual(out["status"], "refused")
        self.assertEqual(self.scene[HA.hp.PROP], before)

    def test_an_edit_that_changed_since_it_was_read_is_refused(self):
        out = self.tools.tool_propose_highlights([self.moment], version="stale")
        self.assertEqual(out["status"], "error")
        self.assertTrue(out["refused"])
        self.assertEqual(out["version"], HA.hp.sr.version(self.ss))
        self.assertIn("Read it again", out["message"])
        self.assertEqual(self.ops.load(self.scene)["picks"], [])

    def test_empty_arguments_and_unknown_kinds_are_named(self):
        self.assertIn("picks", self.tools.tool_propose_highlights([])["message"])
        self.assertEqual(self.tools.tool_propose_highlights([self.moment], kind="vlog")["status"], "error")
        self.reader_sents = []
        self.assertIn("No speech", self.tools.tool_propose_highlights([self.moment])["message"])

    def test_markers_follow_when_they_are_on(self):
        store = self.ops.load(self.scene)
        store["markers_on"] = True
        self.ops.save(self.scene, store)
        self.tools.tool_propose_highlights([self.moment])
        self.assertEqual(len(self.scene.timeline_markers), 2)


class TestMakeShort(_Base):

    def setUp(self):
        super().setUp()
        info = H613.planted_info(self.data, self.ss)
        self.tools.tool_propose_highlights([proposal(info[0]["nums"][0], info[0]["nums"][-1]),
                                            proposal(info[1]["nums"][0], info[1]["nums"][-1], title="Second")])
        self.ids = [p["id"] for p in self.ops.load(self.scene)["picks"]]
        self.undo.clear()
        self.calls = []

        class ShortError(Exception):
            pass
        short = types.ModuleType(W._PKG + ".highlight_short")
        short.ShortError = ShortError

        def build(scene, pick, steps, window=None):
            self.calls.append((scene, pick["id"], dict(steps), window))
            return {"scene": types.SimpleNamespace(name="Short 01: " + pick["title"], render=types.SimpleNamespace(
                fps=FPS, fps_base=1.0)), "first": 100, "last": 1899, "lines": ["Range: frames 100-1899"]}
        short.build = build
        self.short = short
        sys.modules[W._PKG + ".highlight_short"] = short
        self.addCleanup(sys.modules.pop, W._PKG + ".highlight_short", None)

    def test_it_builds_the_pick_without_switching_the_window_and_in_one_undo_step(self):
        out = self.tools.tool_make_short(self.ids[1])
        self.assertEqual(out["status"], "ok")
        scene, pick_id, steps, window = self.calls[0]
        self.assertIs(scene, self.scene)
        self.assertEqual((pick_id, window), (self.ids[1], None))
        self.assertEqual(steps, {k: True for k in HA.STEP_NAMES})
        self.assertEqual((out["scene"], out["frame_start"], out["frame_end"]), ("Short 01: Second", 100, 1899))
        self.assertEqual(out["seconds"], 60.0)
        self.assertIn("unchanged", out["message"])
        self.assertEqual(self.undo, ["Make Short"])

    def test_the_pick_can_be_named_by_its_place_and_the_steps_are_chosen(self):
        out = self.tools.tool_make_short("1", ["captions", "hook"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(self.calls[0][1:3], (self.ids[0], {"silences": False, "vertical": False, "captions": True,
                                                            "hook": True}))
        self.assertEqual(self.tools.tool_make_short("2", [])["status"], "ok")
        self.assertEqual(self.calls[1][2], {k: False for k in HA.STEP_NAMES})

    def test_a_wrong_pick_or_step_is_named_and_nothing_is_built(self):
        for args in (("nothing",), ("",), (self.ids[0], ["zoom"])):
            out = self.tools.tool_make_short(*args)
            self.assertEqual(out["status"], "error", args)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.undo, [])

    def test_a_pick_the_edit_no_longer_holds_is_refused(self):
        self.reader_sents = H613.sentences_of(TEXTS.tutorial(), key="b" * 16)            # another file with other words: the picks are gone
        out = self.tools.tool_make_short(self.ids[0])
        self.assertEqual(out["status"], "refused")
        self.assertIn("find the picks again", out["message"])
        self.assertEqual(self.calls, [])

    def test_a_short_that_cannot_be_made_is_an_error_with_the_reason_and_no_undo_step(self):
        def fail(*_a, **_k):
            raise self.short.ShortError("Leave the meta strip first (Tab)")
        self.short.build = fail
        out = self.tools.tool_make_short(self.ids[0])
        self.assertEqual((out["status"], out["message"]), ("error", "Leave the meta strip first (Tab)"))
        self.short.build = lambda *a, **k: 1 / 0
        self.assertIn("Make Short stopped", self.tools.tool_make_short(self.ids[0])["message"])
        self.assertEqual(self.undo, [])

    def test_no_picks_says_what_to_call_first(self):
        store = self.ops.load(self.scene)
        store["picks"] = []
        self.ops.save(self.scene, store)
        self.assertIn("suggest_highlights", self.tools.tool_make_short("1")["message"])


class TestThePanelsButtonOnAnExternalEngine(_Base):

    def _op(self):
        op = self.ops.BFACW_OT_highlights_find()
        op.seconds, op.count, op.kind = "30", 4, "talk"
        op.reports = []
        op.report = lambda level, text: op.reports.append((sorted(level)[0], text))
        return op

    def _ctx(self, mode, clipboard=None):
        prefs = types.SimpleNamespace(operating_mode=mode)
        wm = types.SimpleNamespace(clipboard="")
        return types.SimpleNamespace(
            sequencer_scene=self.scene, scene=self.scene, workspace=None, space_data=None, region=None, window_manager=wm,
            preferences=types.SimpleNamespace(addons={W._PKG: types.SimpleNamespace(preferences=prefs)}))

    def _external(self):
        def external(*_a, **_k):
            raise AJ.Unavailable("An External Agent runs its own model.", "external")
        self._patch(AJ, "resolve_asker", external)

    def test_an_external_agent_gets_a_prepared_message_in_its_chat(self):
        self._external()
        sent = []
        helper = types.ModuleType(W._PKG + ".helper_chats")
        helper.submit = lambda thread, text: sent.append((thread, text)) or "started"
        ui = types.ModuleType(W._PKG + ".ui_chat")
        ui._ui_thread = lambda context=None: "THREAD"
        for name, mod in (("helper_chats", helper), ("ui_chat", ui)):
            sys.modules[W._PKG + "." + name] = mod
            self.addCleanup(sys.modules.pop, W._PKG + "." + name, None)
        op = self._op()
        ctx = self._ctx("EXTERNAL_AGENT")
        self.assertEqual(op.execute(ctx), {'FINISHED'})
        self.assertEqual(sent[0][0], "THREAD")
        for needle in ("read_edit_sentences", "propose_highlights", "30 seconds", "4 pieces", "talk"):
            self.assertIn(needle, sent[0][1])
        self.assertEqual(op.reports[0][0], "INFO")
        self.assertIn("agent", op.reports[0][1])
        self.assertFalse(self.ops._JOB["running"])

    def test_a_busy_external_agent_queues_the_message_and_says_so(self):
        self._external()
        helper = types.ModuleType(W._PKG + ".helper_chats")
        helper.submit = lambda thread, text: "queued"
        ui = types.ModuleType(W._PKG + ".ui_chat")
        ui._ui_thread = lambda context=None: None
        for name, mod in (("helper_chats", helper), ("ui_chat", ui)):
            sys.modules[W._PKG + "." + name] = mod
            self.addCleanup(sys.modules.pop, W._PKG + "." + name, None)
        op = self._op()
        op.execute(self._ctx("EXTERNAL_AGENT"))
        self.assertIn("queue", op.reports[0][1])

    def test_an_external_harness_has_no_chat_in_blender_so_the_message_is_copied(self):
        self._external()
        op = self._op()
        ctx = self._ctx("EXTERNAL_HARNESS")
        self.assertEqual(op.execute(ctx), {'FINISHED'})
        self.assertIn("propose_highlights", ctx.window_manager.clipboard)
        self.assertIn("Copied", op.reports[0][1])

    def test_a_failure_to_hand_over_is_an_error_not_a_crash(self):
        self._external()
        op = self._op()
        ctx = self._ctx("EXTERNAL_HARNESS")
        ctx.window_manager = None
        self.assertEqual(op.execute(ctx), {'CANCELLED'})
        self.assertEqual(op.reports[0][0], "ERROR")

    def test_other_problems_are_still_just_reported(self):
        self.reader_sents = []
        op = self._op()
        self.assertEqual(op.execute(self._ctx("LOCAL")), {'CANCELLED'})
        self.assertEqual(op.reports[0][0], "ERROR")
        self.assertIn("No speech", op.reports[0][1])

    def test_the_panels_own_search_still_works_and_pushes_its_undo_step(self):
        model, _info = self.stand_in()
        op = self._op()
        op.seconds, op.kind, op.count = "60", "podcast", 5
        self.assertEqual(op.execute(self._ctx("LOCAL")), {'FINISHED'})
        self.assertEqual(self.ops._JOB["source"], "panel")
        self.finish_search()
        self.ops._job_tick()
        self.assertEqual(self.undo, ["Find Highlights"])
        self.assertTrue(self.ops.load(self.scene)["picks"])
        self.assertTrue(self.ops._JOB["report"]["requests"] >= 2)
        self.assertGreaterEqual(model.find_count, 1)


# -- 3. the tools exist, are registered and documented -----------------------------------------------------

class TestRegistration(unittest.TestCase):
    NAMES = ("read_edit_sentences", "suggest_highlights", "propose_highlights", "make_short")

    def test_each_tool_has_its_files_and_is_in_the_vse_domain(self):
        dom = ast.parse((_ROOT / "mcp" / "blmcp" / "tool_domains.py").read_text(encoding="utf-8"))
        text = (_ROOT / "mcp" / "blmcp" / "tool_domains.py").read_text(encoding="utf-8")
        vse = text[text.index('"vse"'):text.index('"geometry_nodes"')]
        self.assertTrue(dom)
        for name in self.NAMES:
            self.assertTrue((_TOOLS / (name + ".py")).is_file(), name)
            self.assertTrue((_TOOLS / (name + "_toolcode.py")).is_file(), name)
            self.assertIn('"' + name + '"', vse, name)

    def test_the_loop_keeps_no_copy_of_the_map(self):
        source = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
        for name in self.NAMES:
            self.assertNotIn('"' + name + '"', source)

    def test_the_docs_and_the_load_tools_text_name_them(self):
        docs = (_ADDON / "skills" / "mcp_tools.md").read_text(encoding="utf-8")
        load = (_TOOLS / "load_tools.py").read_text(encoding="utf-8")
        for name in self.NAMES:
            self.assertIn("`" + name, docs)
            self.assertIn(name, load)

    def test_the_tools_that_change_the_scene_are_refused_while_a_render_runs_and_reading_is_not(self):
        for name in ("suggest_highlights", "propose_highlights", "make_short"):
            self.assertIn("Not run:", LJ.scene_busy_message(name, is_running=lambda kind: kind == "RENDER"), name)
        self.assertEqual(LJ.scene_busy_message("read_edit_sentences", is_running=lambda kind: kind == "RENDER"), "")

    def test_the_job_has_a_row_text_and_an_unknown_job_names_the_new_tool(self):
        LJ.clear()
        job_id = LJ.start("HIGHLIGHTS", "Find highlights", lambda: None)
        self.assertTrue(LJ.row_text(LJ.running()[0]).startswith("Finding highlights"))
        self.assertIn("suggest_highlights", LJ.status("job-nothing")["message"])
        LJ.clear()
        del job_id

    def test_a_tool_file_calls_its_function_in_the_add_on_through_its_toolcode(self):
        for name, module, fn in (("read_edit_sentences", "scene_reader_ops", "tool_read_edit_sentences"),
                                 ("suggest_highlights", "highlight_tools", "tool_suggest_highlights"),
                                 ("propose_highlights", "highlight_tools", "tool_propose_highlights"),
                                 ("make_short", "highlight_tools", "tool_make_short")):
            source = (_TOOLS / (name + "_toolcode.py")).read_text(encoding="utf-8")
            ns = {"__name__": "toolcode_" + name}
            exec(compile(source, name + "_toolcode.py", "exec"), ns)       # noqa: S102
            seen = []
            fake = types.ModuleType("bfa_coworker." + module)
            setattr(fake, fn, lambda *a, _seen=seen: _seen.append(a) or {"status": "ok", "message": "m", "extra": 1})
            sys.modules["bfa_coworker." + module] = fake
            try:
                P = ns["Params"]
                if name == "read_edit_sentences":
                    params = P(first=3, count=20)
                elif name == "suggest_highlights":
                    params = P(kind="talk", max_seconds=30.0, count=3)
                elif name == "propose_highlights":
                    params = P(picks=({"first": 1, "last": 2},), kind="podcast", max_seconds=45.0, version="v", replace=True)
                else:
                    params = P(pick="ab", steps=("hook",))
                result = ns["main"](params)
            finally:
                sys.modules.pop("bfa_coworker." + module, None)
            self.assertEqual((result.status, result.message), ("ok", "m"), name)
            self.assertEqual(len(seen), 1, name)

    def test_the_tool_call_arguments_reach_the_add_on_unchanged(self):
        source = (_TOOLS / "propose_highlights_toolcode.py").read_text(encoding="utf-8")
        ns = {"__name__": "tc"}
        exec(compile(source, "propose_highlights_toolcode.py", "exec"), ns)       # noqa: S102
        seen = []
        fake = types.ModuleType("bfa_coworker.highlight_tools")
        fake.tool_propose_highlights = lambda *a: seen.append(a) or {"status": "ok", "message": ""}
        sys.modules["bfa_coworker.highlight_tools"] = fake
        try:
            ns["main"](ns["Params"](picks=({"first": 1, "last": 2},), kind="tutorial", max_seconds=30.0, version="v1",
                                    replace=True))
        finally:
            sys.modules.pop("bfa_coworker.highlight_tools", None)
        self.assertEqual(seen, [([{"first": 1, "last": 2}], "tutorial", 30.0, "v1", True)])

    def test_registering_the_panel_loads_the_tools_so_the_bridge_finds_them(self):
        ops = (_ADDON / "highlight_picks_ops.py").read_text(encoding="utf-8")
        register = ops[ops.index("def register()"):ops.index("def unregister()")]
        self.assertIn('importlib.import_module(__package__ + ".highlight_tools")', register)

    def test_make_short_passes_no_window_and_the_search_has_no_undo_step_of_its_own(self):
        tools = (_ADDON / "highlight_tools.py").read_text(encoding="utf-8")
        self.assertIn("short.build(scene, found, wanted, None)", tools)
        ops = (_ADDON / "highlight_picks_ops.py").read_text(encoding="utf-8")
        self.assertIn('_JOB["source"] == "panel"', ops)


if __name__ == "__main__":
    unittest.main()
