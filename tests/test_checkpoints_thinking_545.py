# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""#545: checkpoint summaries return text, compaction counts only history,
thinking where it pays.  bpy-free.

* the memory writer: thinking OFF on Local (budget 1 + the template switch on
  the wire), its own reply limit and a time limit, one retry for an empty or
  cut-off note, then the heuristic note; Remote sends no thinking field;
* the transport's ``timeout`` (the writer's limit) over a real socket;
* the compaction rule (``session_memory.compaction_threshold``): only the
  history counts, against the room the fixed part leaves, with a floor -- the
  A27 numbers; no status and no checkpoint when there is nothing to retire;
* the thinking policy (``_next_step_kind`` / ``_thinking_for_step``): full
  budget on the first step, after an error and after a new plan, a quarter on
  routine steps; the four Reasoning Effort levels stay distinct; and the real
  turn loop sends it that way.

Run with::

    python -m unittest tests.test_checkpoints_thinking_545 -v
"""

__all__ = ()

import importlib.util
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TESTS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_TESTS)
_ADDON = os.path.join(_REPO, "addon", "bfa_coworker")
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

from test_turn_loop_integration import (  # noqa: E402
    _TurnLoopTestBase, _EXECUTE_CODE_TOOL, _start_fake_server, _tool_call_msg,
)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ADDON, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LT = _load("_lt_545", "llm_transport.py")
_SM = _load("_sm_545", "session_memory.py")

# The A27 Local test (Bforartists 5.2.3, 27B, 32K): prompt budget 28,724; the
# system prompt (13,849 chars) plus 51 tools were ~19,700 of it before anything
# was said ("estimated 19701 / budget 28724 -- compacting" on the first step).
_A27_BUDGET = 28724
_A27_FIXED = 19701


# ---------------------------------------------------------------------------
# The compaction rule
# ---------------------------------------------------------------------------

class TestCompactionThreshold(unittest.TestCase):

    def test_a27_counts_only_the_room_the_history_has(self):
        t = _SM.compaction_threshold(_A27_BUDGET, _A27_FIXED)
        self.assertEqual(t, int((_A27_BUDGET - _A27_FIXED) * _SM.COMPACTION_TRIGGER_RATIO))
        # The old rule fired at 0.6 x the WHOLE budget: below the fixed part alone.
        self.assertLess(_A27_BUDGET * 0.6, _A27_FIXED)
        # The new one leaves the history thousands of tokens.
        self.assertGreater(t, 5000)

    def test_a_tiny_room_has_a_floor(self):
        # 16K window, fixed part nearly the whole budget: not "compact after every exchange".
        t = _SM.compaction_threshold(13978, 13500)
        self.assertEqual(t, int(_SM.COMPACTION_MIN_ROOM_TOKENS * _SM.COMPACTION_TRIGGER_RATIO))
        self.assertEqual(_SM.compaction_threshold(13978, 20000), t)     # fixed over the budget

    def test_remote_has_no_token_trigger(self):
        self.assertEqual(_SM.compaction_threshold(0, 5000), 0)

    def test_writer_limit_scales_with_the_note(self):
        try:
            for ctx, lo, hi in ((8192, 512, 700), (32768, 1900, 2000), (262144, 2250, 2400)):
                _SM.set_memory_budget(ctx)
                note = int(_SM.memory_max_chars() / 3.5)
                limit = _SM.memory_writer_max_tokens()
                self.assertTrue(lo <= limit <= hi, (ctx, limit))
                self.assertGreater(limit, note)          # room for the whole note it asks for
        finally:
            _SM.set_memory_budget(16384)


def _a27_like_system():
    # About the A27 system prompt's size (13,849 chars).
    return {"role": "system", "content": "You are Coworker. " + "Rules and skills. " * 770}


def _big_tools(n=51, chars=1000):
    return [{"type": "function", "function": {"name": "tool_{:02d}".format(i), "description": "d" * chars,
                                              "parameters": {"type": "object", "properties": {}}}}
            for i in range(n)]


class TestMaybeCompact(_TurnLoopTestBase):
    """``_maybe_compact_session`` on the A27 shape (no LLM call needed)."""

    def _run(self, history, budget=_A27_BUDGET, tools=None):
        statuses, calls = [], []

        def writer(text, prior):
            calls.append(text)
            return "Done\n- the note about the scene objects"
        self.ac._maybe_compact_session(history, _big_tools() if tools is None else tools, budget,
                                       on_status=statuses.append, memory_writer=writer)
        return statuses, calls

    def _turns(self, n, user_chars=60, reply_chars=200):
        h = [_a27_like_system()]
        for i in range(n):
            h.append({"role": "user", "content": "request {:d} ".format(i) + "u" * user_chars})
            h.append({"role": "assistant", "content": "reply {:d} ".format(i) + "r" * reply_chars})
        return h

    def test_short_chat_with_a_large_tool_list_does_not_compact(self):
        # Before: 0.6 x 28,724 < system + tools, so every step "compacted".
        h = self._turns(4)
        h.append({"role": "user", "content": "and now?"})
        n0 = len(h)
        statuses, calls = self._run(h)
        self.assertEqual(statuses, [])
        self.assertEqual(calls, [])
        self.assertEqual(len(h), n0)
        self.assertEqual(self.sm.store.list_checkpoints(), [])

    def test_history_over_its_room_compacts_and_keeps_the_current_turn(self):
        h = self._turns(8, user_chars=1500, reply_chars=4000)      # ~12K tokens of history
        h.append({"role": "user", "content": "the current request"})
        statuses, calls = self._run(h)
        self.assertEqual(statuses, ["Checkpoint: summarizing older turns..."])
        self.assertEqual(len(calls), 1)
        self.assertEqual(h[0]["role"], "system")
        self.assertEqual(h[-1]["content"], "the current request")
        self.assertLessEqual(len(h), 1 + self.sm.COMPACTION_KEEP_RECENT + 1)
        self.assertIn("the note", self.sm.store.memory_block)
        self.assertEqual([c.get("reason") for c in self.sm.store.list_checkpoints()], ["compaction"])

    def test_nothing_to_retire_means_no_status_and_no_checkpoint(self):
        # One long current turn (A27: 100 of 117 "compactions" retired 0 messages).
        h = [_a27_like_system(), {"role": "assistant", "content": "Hi!", "ui_only": True},
             {"role": "user", "content": "build it"}]
        for i in range(14):
            h.append({"role": "assistant", "content": "step " + "s" * 3000,
                      "tool_calls": [{"id": "c{:d}".format(i), "type": "function",
                                      "function": {"name": "execute_blender_code", "arguments": "{}"}}]})
            h.append({"role": "tool", "tool_call_id": "c{:d}".format(i), "content": "ok " * 500})
        statuses, calls = self._run(h)
        self.assertEqual((statuses, calls), ([], []))
        self.assertEqual(self.sm.store.list_checkpoints(), [])

    def test_the_tool_results_are_counted_as_sent(self):
        # Stored tool results are kept whole for the panel; the request trims them.
        h = self._turns(2)
        h.append({"role": "user", "content": "inspect"})
        h.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_scene_digest", "arguments": "{}"}}]})
        h.append({"role": "tool", "tool_call_id": "c1", "content": "x" * 60000})   # ~17K tokens stored
        h.append({"role": "user", "content": "next"})
        statuses, _calls = self._run(h)
        self.assertEqual(statuses, [])

    def test_the_message_window_still_compacts(self):
        h = self._turns(12)                          # 24 small messages: over the 20-message window
        h.append({"role": "user", "content": "now"})
        statuses, calls = self._run(h)
        self.assertEqual(len(calls), 1)
        self.assertEqual(h[-1]["content"], "now")


# ---------------------------------------------------------------------------
# The memory writer
# ---------------------------------------------------------------------------

class TestMemoryWriter(_TurnLoopTestBase):

    def setUp(self):
        super().setUp()
        self.calls = []
        self.replies = []
        self._orig = self.ac._transport.openai_chat_completions

        def fake(url, messages, tools, api_key=None, model=None, max_tokens=None,
                 thinking_budget_tokens=None, chat_mode="AGENT", timeout=None):
            self.calls.append({"max_tokens": max_tokens, "thinking": thinking_budget_tokens,
                               "timeout": timeout, "tools": tools})
            r = self.replies.pop(0) if self.replies else None
            if r is None:
                return None
            content, finish = r
            return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
        self.ac._transport.openai_chat_completions = fake

    def tearDown(self):
        self.ac._transport.openai_chat_completions = self._orig
        super().tearDown()

    def test_local_sends_thinking_off_with_its_own_limit_and_a_time_limit(self):
        self.replies = [("Done\n- a cube", "stop")]
        w = self.ac._memory_writer_factory("http://x", "", "", local=True)
        self.assertEqual(w("user: add a cube", ""), "Done\n- a cube")
        self.assertEqual(len(self.calls), 1)
        c = self.calls[0]
        self.assertEqual(c["thinking"], 0)                       # off (not None: no field = unlimited)
        self.assertEqual(c["max_tokens"], self.sm.memory_writer_max_tokens())
        self.assertIsNotNone(c["timeout"])
        self.assertLessEqual(c["timeout"], self.ac._MEMORY_WRITER_TIME_LIMIT)
        self.assertIsNone(c["tools"])

    def test_remote_sends_no_thinking_field(self):
        self.replies = [("note", "stop")]
        w = self.ac._memory_writer_factory("http://x", "k", "m", local=False)
        self.assertEqual(w("t", ""), "note")
        self.assertIsNone(self.calls[0]["thinking"])

    def test_empty_or_cut_off_is_tried_once_more_then_the_fallback(self):
        self.replies = [("", "length"), ("half a no", "length")]
        w = self.ac._memory_writer_factory("http://x", "", "", local=True)
        self.assertIsNone(w("t", ""))
        self.assertEqual(len(self.calls), 2)                     # one retry, no more
        self.assertEqual(self.calls[1]["max_tokens"], 2 * self.calls[0]["max_tokens"])
        self.assertLess(self.calls[1]["timeout"], self.calls[0]["timeout"] + 0.01)   # one limit for both

    def test_retry_that_works_is_used(self):
        self.replies = [("", "stop"), ("Done\n- ok", "stop")]
        w = self.ac._memory_writer_factory("http://x", "", "", local=True)
        self.assertEqual(w("t", ""), "Done\n- ok")

    def test_no_answer_is_not_retried(self):
        self.replies = [None]
        w = self.ac._memory_writer_factory("http://x", "", "", local=True)
        self.assertIsNone(w("t", ""))
        self.assertEqual(len(self.calls), 1)

    def test_the_turns_error_state_is_kept(self):
        self.state.error, self.state.error_kind = "earlier", "x"
        self.replies = [None]
        self.ac._memory_writer_factory("http://x", "", "", local=True)("t", "")
        self.assertEqual((self.state.error, self.state.error_kind), ("earlier", "x"))


class _Slow(BaseHTTPRequestHandler):
    # HTTP/1.0: the handler ends after its one answer (a kept-alive handler thread
    # would outlive the test, and test_engine_loop counts live threads).
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        self.server.bodies.append(body)
        time.sleep(self.server.delay)
        data = json.dumps({"choices": [{"message": {"content": "late"}, "finish_reason": "stop"}]}).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass


class TestTransportTimeout(unittest.TestCase):

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
        self.server.daemon_threads = False
        self.server.block_on_close = True           # server_close() joins the handler threads
        self.server.bodies = []
        self.server.delay = 3.0
        threading.Thread(target=self.server.serve_forever, daemon=True, name="slow-server-545").start()
        self.url = "http://127.0.0.1:{:d}/v1/chat/completions".format(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_the_whole_call_ends_at_the_limit(self):
        t0 = time.monotonic()
        out = _LT.openai_chat_completions(self.url, [{"role": "user", "content": "hi"}], None,
                                          thinking_budget_tokens=0, timeout=1.0)
        self.assertIsNone(out)
        self.assertLess(time.monotonic() - t0, 2.5)
        self.assertEqual(len(self.server.bodies), 1)              # not re-sent after the limit
        body = self.server.bodies[0]
        self.assertEqual(body.get("thinking_budget_tokens"), 1)
        self.assertEqual(body.get("chat_template_kwargs"), {"enable_thinking": False})

    def test_an_answer_inside_the_limit_is_returned(self):
        self.server.delay = 0.1
        out = _LT.openai_chat_completions(self.url, [{"role": "user", "content": "hi"}], None, timeout=5.0)
        self.assertEqual(out["choices"][0]["message"]["content"], "late")


# ---------------------------------------------------------------------------
# Thinking where it pays
# ---------------------------------------------------------------------------

def _call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class TestStepKind(_TurnLoopTestBase):

    def _h(self, *tail):
        return [{"role": "system", "content": "s"}, {"role": "user", "content": "old"},
                {"role": "assistant", "content": "", "tool_calls": [_call("execute_blender_code", {})]},
                {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok"}'},
                {"role": "user", "content": "make a table"}] + list(tail)

    def test_first_request_of_a_turn(self):
        self.assertEqual(self.ac._next_step_kind(self._h()), "first")
        # an injected note is not the user speaking: still the first step
        note = self.sm.make_system_note("scene is empty", kind="entity")
        self.assertEqual(self.ac._next_step_kind(self._h(note)), "first")

    def test_routine_follow_up(self):
        h = self._h({"role": "assistant", "content": "", "tool_calls": [_call("execute_blender_code", {})]},
                    {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok", "result": 1}'},
                    self.sm.make_system_note("continue", kind="followup"))
        self.assertEqual(self.ac._next_step_kind(h), "routine")

    def test_after_a_tool_error(self):
        h = self._h({"role": "assistant", "content": "", "tool_calls": [_call("execute_blender_code", {})]},
                    {"role": "tool", "tool_call_id": "c1",
                     "content": '{"status": "error", "message": "Traceback ..."}'})
        self.assertEqual(self.ac._next_step_kind(h), "error")

    def test_after_the_loop_guards_note(self):
        h = self._h({"role": "assistant", "content": "", "tool_calls": [_call("execute_blender_code", {})]},
                    {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok"}'},
                    self.sm.make_system_note("same error twice -- try another way", kind="spiral"))
        self.assertEqual(self.ac._next_step_kind(h), "error")

    def test_after_a_new_plan_but_not_after_ticking_a_step(self):
        plan = self._h({"role": "assistant", "content": "",
                        "tool_calls": [_call("update_plan", {"steps": ["table", "chairs"]})]},
                       {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok"}'})
        self.assertEqual(self.ac._next_step_kind(plan), "plan")
        tick = self._h({"role": "assistant", "content": "",
                        "tool_calls": [_call("update_plan", {"done": [1]})]},
                       {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok"}'})
        self.assertEqual(self.ac._next_step_kind(tick), "routine")


class TestThinkingForStep(_TurnLoopTestBase):

    def test_full_budget_where_it_pays(self):
        for kind in ("first", "error", "plan"):
            for budget in (None, 512, 1024, 2048):
                self.assertEqual(self.ac._thinking_for_step(budget, kind), budget)

    def test_routine_mapping_per_effort(self):
        f = self.ac._thinking_for_step
        self.assertIsNone(f(None, "routine"))        # Off = no cap: the user's choice, every step
        self.assertEqual(f(2048, "routine"), 512)    # High
        self.assertEqual(f(1024, "routine"), 256)    # Medium (default)
        self.assertEqual(f(512, "routine"), 0)       # Low: off
        self.assertEqual(f(768, "routine"), 0)       # Custom below 4 x 256
        self.assertEqual(f(4096, "routine"), 1024)

    def test_the_four_efforts_stay_distinct_and_ordered(self):
        f = self.ac._thinking_for_step
        efforts = (None, 2048, 1024, 512)            # Off, High, Medium, Low
        full = [f(b, "first") for b in efforts]
        self.assertEqual(len(set(full)), 4)
        routine = [f(b, "routine") for b in efforts]
        self.assertEqual(len(set(routine)), 4)
        as_num = [10 ** 9 if r is None else r for r in routine]
        self.assertEqual(as_num, sorted(as_num, reverse=True))

    def test_policy_can_be_switched_off(self):
        saved = self.ac._STEP_THINKING_POLICY
        try:
            self.ac._STEP_THINKING_POLICY = False
            self.assertEqual(self.ac._thinking_for_step(1024, "routine"), 1024)
        finally:
            self.ac._STEP_THINKING_POLICY = saved


class TestTurnLoopThinking(_TurnLoopTestBase):
    """The real turn loop against the harness's fake server."""

    def tearDown(self):
        # The exec'd controller's MCP event-loop thread: stop it, so no thread of
        # this test outlives it (test_engine_loop counts live threads).
        loop, th = getattr(self.ac, "_loop", None), getattr(self.ac, "_loop_thread", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
            if th is not None:
                th.join(2.0)
        super().tearDown()

    def _mk(self, script, thinking=1024, mode="local"):
        self.server.shutdown()
        self.server.server_close()
        self.server = _start_fake_server(script, mcp_tools=[_EXECUTE_CODE_TOOL])
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = mode
        cfg.local_port = self.port
        cfg.local_ctx_size = 32768
        cfg.local_max_tokens = 4096
        cfg.thinking_budget_tokens = thinking
        if mode == "remote":
            cfg.remote_api_url = "http://127.0.0.1:{:d}/v1".format(self.port)
            cfg.remote_model = "remote-model"
        self.lm.set_config(cfg)
        self.lm._runtime_ctx_cache = None
        self.state.conversation_history = [{"role": "system", "content": "You are a helpful agent."}]

    def _turn(self, text):
        self._pin_fake_bpy()
        try:
            return self.ac.run_conversation_turn(text, on_status=lambda s: None, chat_mode="AGENT",
                                                 llm_url=None, model=None, mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()

    def _sent(self):
        with self.server.lock:
            return [r for r in self.server.requests if r not in self.server.memory_writer_calls]

    def test_first_step_full_routine_step_a_quarter(self):
        self._mk([_tool_call_msg("c1", "bpy.ops.mesh.primitive_cube_add()"), {"content": "Added a cube."}])
        self._turn("Add a cube.")
        sent = self._sent()
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0].get("thinking_budget_tokens"), 1024)
        self.assertEqual(sent[1].get("thinking_budget_tokens"), 256)

    def test_after_a_failed_call_the_full_budget(self):
        self._mk([_tool_call_msg("c1", "FAILME_1 = 1"), _tool_call_msg("c2", "x = 2"), {"content": "done"}])
        self.server.fail_tool_markers = ["FAILME_1"]
        self._turn("Do it.")
        budgets = [r.get("thinking_budget_tokens") for r in self._sent()]
        self.assertEqual(budgets[:3], [1024, 1024, 256])

    def test_low_effort_routine_steps_go_with_thinking_off(self):
        self._mk([_tool_call_msg("c1", "x = 1"), {"content": "ok"}], thinking=512)
        self._turn("Do it.")
        sent = self._sent()
        self.assertEqual(sent[0].get("thinking_budget_tokens"), 512)
        self.assertEqual(sent[1].get("thinking_budget_tokens"), 1)
        self.assertEqual(sent[1].get("chat_template_kwargs"), {"enable_thinking": False})

    def test_off_effort_sends_no_field_on_any_step(self):
        self._mk([_tool_call_msg("c1", "x = 1"), {"content": "ok"}], thinking=0)
        self._turn("Do it.")
        for r in self._sent():
            self.assertNotIn("thinking_budget_tokens", r)
            self.assertNotIn("chat_template_kwargs", r)

    def test_remote_is_untouched(self):
        self._mk([_tool_call_msg("c1", "x = 1"), {"content": "ok"}], mode="remote")
        self._turn("Do it.")
        for r in self._sent():
            self.assertNotIn("thinking_budget_tokens", r)


if __name__ == "__main__":
    unittest.main()
