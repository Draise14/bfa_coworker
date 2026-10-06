# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tier 3 hardening: goal & plan memory, checkpoints, long requests.

Covers the failures reported on 2026-10-05 (local 16K window):

* the chat "talked to itself" -- an unflagged auto-continue prompt
  ("Continue. Keep this next step small...") survived in the history and
  rendered as a NEW user turn;
* system nudges cancelled the live conclusion -- the forced "summarize in
  1-2 sentences" replaced a real final reply, and closing offers ("If you'd
  like, I'll add a chimney") were mistaken for unfinished promises;
* the goal was lost -- the message-count slice and the budget trim dropped
  the user's request mid-turn, and checkpoints kept only a 160-char goal;
* a 16483 > 16384 overflow -- the chars/token estimate ran low on JSON dumps.

Run:  python -m unittest test_goal_plan_hardening  (from the tests/ folder)
"""

import importlib.util
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_ADDON = os.path.join(os.path.dirname(_HERE), "addon", "bfa_coworker")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ADDON, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_gp = _load("goal_plan_under_test", "goal_plan.py")
_sm = _load("session_memory_under_test", "session_memory.py")

from test_chat_turns import _group_turns, _split_turn  # noqa: E402
from test_turn_loop_integration import (  # noqa: E402
    _TurnLoopTestBase, _EXECUTE_CODE_TOOL, _start_fake_server, _tool_call_msg,
)


# ---------------------------------------------------------------------------
# Pure units: goal_plan
# ---------------------------------------------------------------------------

class TestGoalPlan(unittest.TestCase):

    def test_first_request_is_session_goal_each_request_is_turn_goal(self):
        g = _gp.GoalPlan()
        brief = ("Make the house look like the reference image: " + "detail " * 60).strip()
        g.set_request(brief)
        g.set_request("now make the roof red")
        self.assertTrue(g.session_goal.startswith("Make the house look like"))
        # A detailed brief keeps far more than the old 160-char cap.
        self.assertGreater(len(g.session_goal), 300)
        self.assertEqual(g.turn_goal, "now make the roof red")

    def test_multimodal_request_notes_attached_image(self):
        g = _gp.GoalPlan()
        g.set_request([
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            {"type": "text", "text": "match this reference"},
        ])
        self.assertIn("match this reference", g.session_goal)
        self.assertIn("1 image attached", g.session_goal)
        self.assertNotIn("base64", g.session_goal)

    def test_plan_tool_accepts_lenient_shapes(self):
        g = _gp.GoalPlan()
        g.apply_update({"steps": ["1. Inspect scene", "- [x] Fix roof", {"text": "Add door", "status": "doing"}]})
        self.assertEqual([s["status"] for s in g.steps], ["todo", "done", "doing"])
        self.assertEqual(g.steps[0]["text"], "Inspect scene")
        out = g.apply_update({"done": ["1", 3]})
        self.assertIn("3/3 done", out)
        self.assertEqual(g.pending_steps(), [])
        g2 = _gp.GoalPlan()
        g2.apply_update({"steps": "a\nb\nc", "current": 2})
        self.assertEqual(g2.next_step()["text"], "b")

    def test_new_request_clears_finished_plan_keeps_unfinished(self):
        g = _gp.GoalPlan()
        g.set_request("build a house")
        g.apply_update({"steps": ["walls", "roof"]})
        g.set_request("continue")
        self.assertEqual(len(g.steps), 2, "unfinished plan survives 'continue'")
        g.apply_update({"done": [1, 2]})
        g.set_request("now a fence")
        self.assertEqual(g.steps, [], "a finished plan is cleared by a new request")

    def test_render_block_is_bounded_and_keeps_open_steps(self):
        g = _gp.GoalPlan()
        g.set_request("x" * 2000)
        g.apply_update({"steps": ["step {:d} ".format(i) + "y" * 100 for i in range(12)],
                        "done": list(range(1, 9))})
        block = g.render_block(max_chars=900, with_tool_hint=True)
        self.assertLessEqual(len(block), 900)
        self.assertIn("Session goal", block)
        self.assertIn("steps 1-8 done", block)
        self.assertIn("step 9", block)

    def test_markdown_roundtrip_and_user_edits(self):
        g = _gp.GoalPlan()
        g.set_request("build a windmill")
        g.apply_update({"steps": ["base", "blades"], "done": [1]})
        md = g.render_markdown()
        g2 = _gp.GoalPlan()
        self.assertTrue(g2.parse_markdown(md))
        self.assertEqual(g2.to_dict(), g.to_dict())
        edited = md.replace("- [ ] blades", "- [>] blades\n- [ ] paint it red").replace(
            "## Notes\n", "## Notes\nKeep it low-poly\n")
        self.assertTrue(g.parse_markdown(edited))
        self.assertEqual([s["text"] for s in g.steps], ["base", "blades", "paint it red"])
        self.assertEqual(g.steps[1]["status"], "doing")
        self.assertEqual(g.user_notes, "Keep it low-poly")
        self.assertFalse(g.parse_markdown(edited), "re-parsing unchanged text is a no-op")


# ---------------------------------------------------------------------------
# Pure units: session_memory (notes, checkpoints, memory budget)
# ---------------------------------------------------------------------------

class TestSystemNotes(unittest.TestCase):

    def test_flagged_and_legacy_notes_are_never_user_turns(self):
        note = _sm.make_system_note("Keep going.", kind="followup")
        self.assertTrue(note["content"].startswith("[System:"))
        self.assertTrue(_sm.is_system_note(note))
        legacy = {"role": "user", "content": "Continue. Keep this next step small -- "
                  "if there is a lot left to do, do one short piece now."}
        self.assertTrue(_sm.is_system_note(legacy))
        self.assertFalse(_sm.is_system_note({"role": "user", "content": "continue please"}))

    def test_legacy_continue_does_not_split_the_chat_turn(self):
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "fix the floating parts", "turn_start": True},
            {"role": "assistant", "content": "partial"},
            {"role": "user", "content": "Continue. Keep this next step small -- x"},
            {"role": "assistant", "content": "All parts are grounded now."},
        ]
        turns = _group_turns(history)
        self.assertEqual(len(turns), 1, "the agent's own prompt must not start a turn")
        user_msg, _proc, conclusion = _split_turn(turns[0])
        self.assertEqual(user_msg["content"], "fix the floating parts")
        self.assertEqual(conclusion["content"], "All parts are grounded now.")
        # Compaction never treats it as a boundary either.
        self.assertFalse(_sm._is_real_user(history[3]))


class TestCheckpointGoal(unittest.TestCase):

    def test_goal_survives_payload_restore_and_reset(self):
        st = _sm.CheckpointStore()
        st.goal.set_request("build a lighthouse on a cliff")
        st.goal.apply_update({"steps": ["cliff", "tower", "lamp"], "done": [1]})
        st.snapshot([{"role": "system", "content": "s"}], reason="compaction")
        st.goal.apply_update({"done": [2, 3]})
        restored = _sm.CheckpointStore()
        restored.load_payload(json.loads(json.dumps(st.to_payload())))
        self.assertEqual(restored.goal.to_dict(), st.goal.to_dict())
        # Restoring the checkpoint rewinds the plan to that point.
        st.restore(0, [{"role": "system", "content": "s"}])
        self.assertEqual(st.goal.progress(), (1, 3))
        st.reset()
        self.assertTrue(st.goal.is_empty())

    def test_older_payload_without_goal_loads(self):
        st = _sm.CheckpointStore()
        st.load_payload({"memory_block": "m", "checkpoints": []})
        self.assertTrue(st.goal.is_empty())


class TestMemoryNote(unittest.TestCase):

    def tearDown(self):
        _sm.set_memory_budget(16384)

    def test_heuristic_fold_keeps_newest_lines(self):
        prior = "[Session memory]\n" + "\n".join("- old fact {:d} ".format(i) + "z" * 60 for i in range(40))
        turns = [{"role": "user", "content": "the NEWEST request"},
                 {"role": "assistant", "content": "did the newest thing"}]
        block = _sm.build_memory_block(turns, prior_memory=prior, updated_turn=9)
        self.assertIn("the NEWEST request", block, "new info must survive trimming")
        self.assertNotIn("old fact 0 ", block, "the oldest prior lines are dropped first")
        self.assertTrue(block.endswith("Last updated: turn 9"))
        self.assertLessEqual(len(block), _sm.memory_max_chars())

    def test_chatty_or_empty_writer_output_falls_back(self):
        self.assertIsNone(_sm.validate_memory_note("Sure!"))
        self.assertIsNone(_sm.validate_memory_note("I updated the note for you, hope it helps."))
        ok = _sm.validate_memory_note("Here is the note:\nDone:\n- built walls\nPending:\n- roof")
        self.assertTrue(ok.startswith("Done:"))

    def test_memory_budget_scales_with_window(self):
        small = _sm.set_memory_budget(8192)
        large = _sm.set_memory_budget(65536)
        self.assertLess(small, large)
        self.assertGreaterEqual(small, int(400 * 3.5))
        self.assertLessEqual(large, int(1500 * 3.5))


# ---------------------------------------------------------------------------
# Turn loop (fake llama-server)
# ---------------------------------------------------------------------------

class _HardeningBase(_TurnLoopTestBase):

    def _mk_server(self, script, ctx=8192, rounds=None):
        self.server.shutdown()
        self.server.server_close()
        self.server = _start_fake_server(script, mcp_tools=[_EXECUTE_CODE_TOOL])
        self.server.n_ctx = ctx
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = "local"
        cfg.local_port = self.port
        cfg.local_ctx_size = ctx
        cfg.local_max_tokens = 1024
        cfg.thinking_budget_tokens = 0
        if rounds is not None:
            cfg.auto_continue_rounds = rounds
        self.lm.set_config(cfg)
        self.lm._runtime_ctx_cache = None

    def _agent_turn(self, message):
        statuses, texts = [], []
        self._pin_fake_bpy()
        try:
            hist = self.ac.run_conversation_turn(
                message, on_text=texts.append, on_status=statuses.append,
                chat_mode="AGENT", llm_url=None, model="fake-model",
                mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()
        return hist, texts, statuses

    def _main_requests(self):
        with self.server.lock:
            return [r for r in self.server.requests
                    if r not in self.server.memory_writer_calls]

    @staticmethod
    def _user_turns(hist):
        return [m for m in hist if m.get("role") == "user"
                and not _sm.is_system_note(m)]


def _plan_call(call_id, **args):
    return {"content": "", "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": "update_plan", "arguments": json.dumps(args)}}]}


class TestNoSelfTalk(_HardeningBase):

    def test_failed_continuation_leaves_no_user_turn_behind(self):
        self._mk_server([{"content": "I fixed the posts and", "finish_reason": "length"}])
        # The continuation request fails (the stream, the fallback and all
        # of the transport's 500 retries).
        self.server.chat_error_specs = [
            {"is_stream": None, "status": 500, "body": "{}", "min_request": 1}
            for _ in range(12)
        ]
        hist, _texts, _st = self._agent_turn("fix the floating parts")
        self.assertEqual([m["content"] for m in self._user_turns(hist)],
                         ["fix the floating parts"],
                         "only the user may author user turns")
        self.assertFalse(any(m.get("system_note") == "continue" for m in hist),
                         "a failed continuation must remove its scaffold")
        self.assertEqual(len(_group_turns(hist[1:])), 1)

    def test_followup_notes_do_not_pile_up_and_carry_the_goal(self):
        empty_call = dict(_tool_call_msg("c1", "print(1)"), content="")
        empty_call2 = dict(_tool_call_msg("c2", "print(2)"), content="")
        self._mk_server([empty_call, empty_call2, {"content": "Done -- both steps ran."}])
        hist, _texts, _st = self._agent_turn("run two steps please")
        notes = [m for m in hist if m.get("system_note") == "followup"]
        self.assertEqual(len(notes), 1, "older follow-ups are replaced, not stacked")
        self.assertIn("run two steps please", notes[0]["content"])
        self.assertNotIn("Please provide a helpful response", json.dumps(hist))


class TestConclusionProtection(_HardeningBase):

    def test_closing_offer_after_work_is_the_conclusion(self):
        self._mk_server([
            _tool_call_msg("c1", "print('walls')"),
            {"content": "All four walls now sit on the foundation. "
                        "If you'd like, I'll add a chimney next."},
        ])
        hist, _texts, _st = self._agent_turn("ground the walls")
        self.assertEqual(len(self._main_requests()), 2, "no nudge round")
        self.assertFalse(any(m.get("system_note") == "nudge" for m in hist))
        self.assertIn("chimney", hist[-1]["content"])

    def test_final_reply_on_last_iteration_is_not_replaced(self):
        script = [_tool_call_msg("c{:d}".format(i), "print({:d})".format(i)) for i in range(11)]
        script.append({"content": "Finished: every piece is placed."})
        self._mk_server(script, rounds=0)
        hist, _texts, _st = self._agent_turn("place every piece")
        self.assertEqual(hist[-1]["content"], "Finished: every piece is placed.")
        self.assertFalse(any(m.get("system_note") == "wrapup" for m in hist))
        self.assertNotIn("1-2 sentences", json.dumps(hist))

    def test_budget_exhausted_asks_for_progress_report(self):
        script = [_tool_call_msg("c{:d}".format(i), "print({:d})".format(i)) for i in range(12)]
        script.append({"content": "Done: 12 steps. Remaining: the roof. Say continue."})
        self._mk_server(script, rounds=0)
        hist, _texts, _st = self._agent_turn("build everything")
        wraps = [m for m in hist if m.get("system_note") == "wrapup"
                 or "step budget for this request is used up" in str(m.get("content"))]
        self.assertEqual(len(wraps), 1)
        self.assertIn("Remaining: the roof", hist[-1]["content"])
        roles = [m["role"] for m in hist]
        for a, b in zip(roles, roles[1:]):
            self.assertFalse(a == b == "user", "no consecutive user/user messages")


class TestLongRequests(_HardeningBase):

    def test_rounds_continue_while_progressing_and_keep_the_request(self):
        script = [_tool_call_msg("c{:d}".format(i), "print({:d})".format(i)) for i in range(16)]
        script.append({"content": "All 16 steps are done."})
        self._mk_server(script, rounds=3)
        hist, _texts, statuses = self._agent_turn("do the sixteen-step job")
        self.assertEqual(hist[-1]["content"], "All 16 steps are done.")
        self.assertTrue(any("continuing (round 2" in s for s in statuses), statuses)
        # Every request still carries the user's actual request, even past the
        # 20-message slice.
        for r in self._main_requests():
            texts = [str(m.get("content")) for m in r["messages"] if m.get("role") == "user"]
            self.assertTrue(any("do the sixteen-step job" in t for t in texts),
                            "the current request was sliced away")

    def test_no_round_without_progress(self):
        failing = [_tool_call_msg("c{:d}".format(i), "FAILME_{:d}".format(i)) for i in range(12)]
        script = failing + [{"content": "Could not finish -- the scene was busy."}]
        self._mk_server(script, rounds=3)
        self.server.fail_tool_markers = ["FAILME_{:d}".format(i) for i in range(12)]
        hist, _texts, statuses = self._agent_turn("try the risky thing")
        self.assertFalse(any("continuing (round" in s for s in statuses))

    def test_plan_tool_is_local_and_pinned(self):
        self._mk_server([
            _plan_call("p1", steps=["inspect", "fix roof", "fix posts"]),
            _tool_call_msg("c1", "print('fix')"),
            _plan_call("p2", done=[1, 2, 3]),
            {"content": "Roof and posts fixed."},
        ])
        hist, _texts, _st = self._agent_turn("make it match the reference")
        goal = self.sm.store.goal
        self.assertEqual(goal.progress(), (3, 3))
        with self.server.lock:
            mcp_calls = json.dumps(self.server.mcp_requests)
        self.assertNotIn("update_plan", mcp_calls, "the plan tool never reaches MCP")
        last_sys = self._main_requests()[-1]["messages"][0]["content"]
        self.assertIn("[Goal & plan", last_sys)
        self.assertIn("Session goal: make it match the reference", last_sys)
        self.assertIn("[x] 3. fix posts", last_sys)
        tools = [t["function"]["name"] for t in self._main_requests()[0].get("tools", [])]
        self.assertIn("update_plan", tools)


class TestSmallWindowFit(_HardeningBase):

    def test_long_turn_with_big_dumps_fits_16k_and_keeps_request(self):
        script = [_tool_call_msg("c{:d}".format(i), "dump_all()") for i in range(9)]
        script.append({"content": "Diagnosed: 3 parts float."})
        self._mk_server(script, ctx=16384)
        # The server tokenizes denser than the heuristic (JSON numbers).
        self.server.usage_fn = lambda body: int(len(json.dumps(body.get("messages"))) / 2.6)
        hist, texts, _st = self._agent_turn("why are the parts floating?")
        self.assertEqual(self.state.error, "")
        self.assertIn("Diagnosed: 3 parts float.", texts)
        for r in self._main_requests():
            real = int(len(json.dumps(r["messages"])) / 2.6)
            self.assertLess(real, 16384, "a request exceeded the real window")
            users = [str(m.get("content")) for m in r["messages"] if m.get("role") == "user"]
            self.assertTrue(any("why are the parts floating?" in u for u in users))
        self.assertGreater(self.ac._prompt_calibration, 1.0)

    def test_overflow_400_calibrates_and_retries(self):
        self._mk_server([{"content": "fits now"}], ctx=16384)
        self.server.chat_error_specs = [{
            "is_stream": None, "status": 400,
            "body": json.dumps({"error": {
                "code": 400, "type": "exceed_context_size_error",
                "message": "request (16483 tokens) exceeds the available context size (16384 tokens)",
                "n_prompt_tokens": 16483, "n_ctx": 16384}}),
        }]
        _hist, texts, _st = self._agent_turn("hello")
        self.assertEqual(self.state.error, "")
        self.assertIn("fits now", texts)
        self.assertGreater(self.ac._prompt_calibration, 1.5)


class TestEntityTextFilter(_HardeningBase):

    def test_addon_text_blocks_are_not_agent_creations(self):
        snap = self.ac._EntitySnapshot
        prev = snap.from_dict({"text_names": []})
        cur = snap.from_dict({"text_names": ["Coworker_006", "Coworker Plan.md", "MyScript"]})
        diff = self.ac._diff_snapshots(prev, cur)
        self.assertEqual(diff.text_names, {"MyScript"})


if __name__ == "__main__":
    unittest.main()
