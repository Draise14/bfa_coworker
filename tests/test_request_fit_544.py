# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""#544: a Local request is made to fit or not sent; a silent model is not waited
on forever; no cut-off loops.  bpy-free.

* the fitting order (``_domain_drop_order``), the reply / thinking policy
  (``_thinking_for_reply``), the context-size advice (``_ctx_needed``);
* the transport: ``thinking_fields`` (0 = off, as b10154 needs it) and the
  wall-clock limits over a real socket -- keep-alive lines do not hold a request
  open, progress reports do, a crawling prefill and Stop end it at once;
* the turn loop (the real ``run_conversation_turn`` against the fake server of
  ``test_turn_loop_integration``): a too-big request is made to fit and the chat
  says what was left out; a refused request is never re-sent; a reply with no
  room is sent with thinking off; a cut-off is retried once, then explained;
  Remote requests carry none of the Local fields.

Run with::

    python -m unittest tests.test_request_fit_544 -v
"""

__all__ = ()

import importlib.util
import json
import os
import sys
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TESTS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_TESTS)
_ADDON = os.path.join(_REPO, "addon", "bfa_coworker")
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

from test_turn_loop_integration import (  # noqa: E402
    _TurnLoopTestBase, _EXECUTE_CODE_TOOL, _start_fake_server,
)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ADDON, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LT = _load("_lt_544", "llm_transport.py")
_TURNS = _load("_chat_turns_544", "chat_turns.py")
_STATUS = _load("_chat_status_544", "chat_status.py")


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------

class _PureBase(_TurnLoopTestBase):
    """The exec'd agent_controller (the harness), for its pure helpers."""


class TestFittingOrder(_PureBase):

    def test_scene_only_domains_go_first_the_requests_own_last(self):
        early, late = self.ac._domain_drop_order(
            {"assets", "lighting", "material", "modeling", "vse"},
            message_domains={"modeling"}, editor_domains={"vse"}, turn_loaded=set())
        # Only in the scene / preferences: first, the larger domain first ("assets": 13 tools).
        self.assertEqual(early[0], "assets")
        self.assertEqual(set(early[:3]), {"assets", "lighting", "material"})
        # The chat's editor's domain after those, still before history.
        self.assertEqual(early[-1], "vse")
        # What the message names: only after history (step 5).
        self.assertEqual(late, ["modeling"])

    def test_domains_the_model_loaded_go_last_of_all(self):
        _early, late = self.ac._domain_drop_order(
            {"modeling", "material"}, message_domains={"modeling"}, editor_domains=set(),
            turn_loaded={"material"})
        self.assertEqual(late, ["modeling", "material"])

    def test_ctx_needed_picks_the_preference_size(self):
        self.assertEqual(self.ac._ctx_needed(19915, 1024), 32768)    # the A27 first request
        self.assertEqual(self.ac._ctx_needed(3000), 16384)
        self.assertEqual(self.ac._ctx_needed(400000), 0)

    def test_fit_note_says_what_and_how_to_get_it_back(self):
        line = self.ac._fit_note_text({"domains": ["assets", "lighting"], "skills": False, "builtin": True},
                                      16384, 32768)
        self.assertIn("16K", line)
        self.assertIn("assets and lighting tools", line)
        self.assertIn("skill notes", line)
        self.assertIn("raise the Context Window to 32K", line)
        self.assertEqual(self.ac._fit_note_text({"domains": [], "skills": False, "builtin": False}, 16384, 0), "")

    def test_too_big_text_has_the_numbers_and_the_fix(self):
        text = self.ac._too_big_text({"messages": 9000, "tools": 6500}, 16384, 13978, local=True)
        self.assertIn("not sent", text)
        self.assertIn("15,500", text)
        self.assertIn("16K", text)
        self.assertIn("Context Window", text)
        self.assertIn("32K", text)
        remote = self.ac._too_big_text({"messages": 9000, "tools": 6500}, 16384, 13978, local=False)
        self.assertIn("larger context window", remote)

    def test_preflight_refuses_tools_over_the_budget(self):
        tools = [{"type": "function", "function": {"name": "t", "description": "z" * 70000}}]
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
        _out, err = self.ac._prompt_preflight(msgs, tools, 13978)
        self.assertIsNotNone(err)

    def test_notices_never_reach_the_model(self):
        hist = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "To fit ...", "notice": "fit"},
                {"role": "assistant", "content": "answer"}]
        sent = self.ac._strip_ui_only_from_history(hist)
        self.assertEqual([m["content"] for m in sent], ["hi", "answer"])

    def test_add_notice_once_per_turn(self):
        hist = [{"role": "user", "content": "hi"}]
        self.ac._add_notice(hist, "x", "error")
        self.ac._add_notice(hist, "x", "error")
        self.assertEqual(len(hist), 2)


class TestThinkingForReply(_PureBase):

    def test_budget_kept_when_it_fits(self):
        self.assertEqual(self.ac._thinking_for_reply(5000, 1024), 1024)

    def test_budget_cut_to_what_is_left(self):
        self.assertEqual(self.ac._thinking_for_reply(1800, 1024), 1800 - 1024)

    def test_off_when_no_room(self):
        self.assertEqual(self.ac._thinking_for_reply(1039, 1024), 0)    # the A27 cut-off rows
        self.assertEqual(self.ac._thinking_for_reply(957, 1024), 0)

    def test_no_cap_is_capped_by_the_room(self):
        self.assertEqual(self.ac._thinking_for_reply(9000, None), 9000 - 1024)
        self.assertEqual(self.ac._thinking_for_reply(900, None), 0)


class TestThinkingFields(unittest.TestCase):

    def test_none_sends_nothing(self):
        self.assertEqual(_LT.thinking_fields(None), {})

    def test_zero_is_off_both_ways(self):
        # b10154 ignores a budget of 0 (unlimited); 1 ends the thinking, the
        # template switch turns it off where the template has one.
        self.assertEqual(_LT.thinking_fields(0), {"thinking_budget_tokens": 1,
                                                  "chat_template_kwargs": {"enable_thinking": False}})

    def test_budget_is_sent(self):
        self.assertEqual(_LT.thinking_fields(512), {"thinking_budget_tokens": 512})


class TestStreamWatch(unittest.TestCase):

    def _watch(self, first=10.0, stall=5.0, prefill=60.0):
        self.now = [0.0]
        return _LT._StreamWatch(first, stall, prefill, clock=lambda: self.now[0])

    def test_nothing_for_the_first_limit_fires(self):
        w = self._watch()
        self.now[0] = 9.9
        self.assertEqual(w.check(), "")
        self.now[0] = 10.1
        self.assertEqual(w.check(), "first_token")

    def test_progress_moves_the_limit(self):
        w = self._watch()
        for t, done in ((4.0, 1000), (8.0, 2000), (12.0, 3000), (16.0, 4000)):
            self.now[0] = t
            w.progress(5000, done)
            self.assertEqual(w.check(), "")
        self.now[0] = 25.0          # 9 s after the last step forward: still inside the limit
        self.assertEqual(w.check(), "")
        self.now[0] = 26.5
        self.assertEqual(w.check(), "first_token")

    def test_progress_that_does_not_move_does_not_count(self):
        w = self._watch()
        for t in (3.0, 6.0, 9.0):           # the first report counts (the server started reading) ...
            self.now[0] = t
            w.progress(5000, 100)
        self.now[0] = 12.9
        self.assertEqual(w.check(), "")
        self.now[0] = 13.1                  # ... the same number again does not
        self.assertEqual(w.check(), "first_token")

    def test_crawling_prefill_is_stopped_early(self):
        w = self._watch(first=180.0, prefill=600.0)
        self.now[0] = 1.0
        w.progress(18000, 0)
        self.now[0] = 81.0          # 25 tok/s (measured on a CPU-only prefill): 18K takes 12 min
        w.progress(18000, 2048)
        self.assertEqual(w.check(), "prefill_slow")

    def test_stall_after_output(self):
        w = self._watch()
        self.now[0] = 2.0
        w.output()
        self.now[0] = 6.9
        self.assertEqual(w.check(), "")
        self.now[0] = 7.1
        self.assertEqual(w.check(), "stall")


class TestChatSide(unittest.TestCase):

    def test_split_turn_keeps_the_reply_not_the_notice(self):
        turn = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "To fit ...", "notice": "fit"},
                {"role": "assistant", "content": "answer"},
                {"role": "assistant", "content": "**No answer**", "notice": "error"}]
        _user, process, conclusion = _TURNS.split_turn(turn)
        self.assertEqual(conclusion["content"], "answer")
        self.assertEqual(process, [])
        self.assertEqual([n["notice"] for n in _TURNS.turn_notices(turn)], ["fit", "error"])

    def test_waiting_phase(self):
        self.assertEqual(_STATUS.phase_text(types.SimpleNamespace(turn_phase="waiting")), "Waiting for the model")


# ---------------------------------------------------------------------------
# Transport over a real socket: keep-alives, progress, Stop, stall
# ---------------------------------------------------------------------------

class _ScriptedSSE(BaseHTTPRequestHandler):
    """Answers every POST with a chunked SSE stream from ``server.plan``:
    ``(delay_s, bytes)`` steps; ``server.bodies`` keeps the request bodies."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.bodies.append(json.loads(raw.decode("utf-8")))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for delay, data in self.server.plan:
                time.sleep(delay)
                self.wfile.write(("%x\r\n" % len(data)).encode() + data + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            self.server.closed_by_client = True


def _data(obj):
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


_KEEPALIVE = b": keep-alive\n\n"
_TOKEN = _data({"choices": [{"index": 0, "delta": {"content": "hi"}}]})
_DONE = _data({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n"


def _progress(total, done):
    return _data({"choices": [{"index": 0, "delta": {"role": "assistant", "content": None}}],
                  "prompt_progress": {"total": total, "cache": 0, "processed": done, "time_ms": 1}})


class TestStreamLimits(unittest.TestCase):

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedSSE)
        self.server.daemon_threads = True
        self.server.bodies = []
        self.server.closed_by_client = False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:{:d}/v1/chat/completions".format(self.server.server_address[1])
        self.state = types.SimpleNamespace(error="", error_full="", error_kind="", warning="", ui_status="")
        self.stop = threading.Event()
        _LT.bind(self.state, self.stop)
        self.saved = {k: getattr(_LT, k) for k in ("_FIRST_TOKEN_LIMIT_LOCAL", "_FIRST_TOKEN_LIMIT_REMOTE",
                                                   "_STREAM_STALL_TIMEOUT", "_PREFILL_LIMIT")}
        _LT._FIRST_TOKEN_LIMIT_LOCAL = 0.8
        _LT._FIRST_TOKEN_LIMIT_REMOTE = 0.8
        _LT._STREAM_STALL_TIMEOUT = 0.8

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(_LT, k, v)
        self.server.shutdown()
        self.server.server_close()

    def _call(self, local=True, budget=None):
        statuses = []
        t0 = time.monotonic()
        out = _LT.openai_chat_completions_stream(self.url, [{"role": "user", "content": "hi"}], [],
                                                 model="m", thinking_budget_tokens=budget,
                                                 on_status=statuses.append, local=local)
        return out, time.monotonic() - t0, statuses

    def test_keepalive_lines_do_not_hold_a_silent_request_open(self):
        # llama-server b10154 sends ": " every 30 s while it reads a long prompt; each line
        # restarted the old per-read timeout (a 908 s wait).  Now the clock decides.
        self.server.plan = [(0.1, _KEEPALIVE)] * 60
        out, took, statuses = self._call(local=True)
        self.assertIsNone(out)
        self.assertLess(took, 2.5)
        self.assertEqual(self.state.error_kind, "stream_timeout")
        self.assertIn("VRAM", self.state.error_full)
        self.assertEqual(statuses[0], "Waiting for the model...")

    def test_remote_same_bug_same_fix_with_its_own_limit(self):
        # Remote: providers send keep-alive comments too; the request used to wait without end.
        self.server.plan = [(0.1, _KEEPALIVE)] * 60
        out, took, statuses = self._call(local=False)
        self.assertIsNone(out)
        self.assertLess(took, 2.5)
        self.assertIn("provider sent nothing", self.state.error_full)
        self.assertEqual(statuses[0], "Contacting m...")
        self.assertNotIn("return_progress", self.server.bodies[0])
        # The Remote limit is the module's existing ceiling, the Local one is far shorter.
        self.assertEqual(self.saved["_FIRST_TOKEN_LIMIT_REMOTE"], _LT._STREAM_TIMEOUT)
        self.assertLess(self.saved["_FIRST_TOKEN_LIMIT_LOCAL"], self.saved["_FIRST_TOKEN_LIMIT_REMOTE"])

    def test_progress_keeps_a_working_prefill_alive(self):
        self.server.plan = [(0.3, _progress(4000, n)) for n in (500, 1000, 1500, 2000, 2500, 3000, 3500, 4000)]
        self.server.plan += [(0.1, _TOKEN), (0.0, _DONE)]
        _LT._PREFILL_LIMIT = 60.0
        out, took, statuses = self._call(local=True)
        self.assertIsNotNone(out)
        self.assertGreater(took, 2.0)       # longer than the 0.8 s first-token limit
        self.assertEqual(out["choices"][0]["message"]["content"], "hi")
        self.assertTrue(any("(reading the request: " in s for s in statuses))
        self.assertTrue(self.server.bodies[0].get("return_progress"))

    def test_crawling_prefill_ends_early_with_the_reason(self):
        _LT._FIRST_TOKEN_LIMIT_LOCAL = 30.0
        _LT._PREFILL_LIMIT = 5.0
        self.server.plan = [(0.2, _progress(100000, n)) for n in range(100, 3000, 100)]
        out, took, _st = self._call(local=True)
        self.assertIsNone(out)
        self.assertLess(took, 3.0)
        self.assertIn("far too slowly", self.state.error_full)

    def test_stop_ends_a_silent_request_at_once(self):
        _LT._FIRST_TOKEN_LIMIT_LOCAL = 30.0
        self.server.plan = [(5.0, _KEEPALIVE)] * 4      # nothing for 5 s at a time
        threading.Timer(0.3, self.stop.set).start()
        out, took, _st = self._call(local=True)
        self.assertIsNone(out)
        self.assertLess(took, 1.5)
        self.assertEqual(self.state.error_kind, "")

    def test_stall_after_output_keeps_the_partial(self):
        self.server.plan = [(0.0, _TOKEN)] + [(0.1, _KEEPALIVE)] * 60
        out, took, _st = self._call(local=True)
        self.assertEqual(out["choices"][0]["message"]["content"], "hi")
        self.assertLess(took, 2.5)
        self.assertIn("stalled", self.state.warning)

    def test_thinking_off_on_the_wire(self):
        self.server.plan = [(0.0, _TOKEN), (0.0, _DONE)]
        self._call(local=True, budget=0)
        body = self.server.bodies[0]
        self.assertEqual(body["thinking_budget_tokens"], 1)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})


# ---------------------------------------------------------------------------
# The turn loop
# ---------------------------------------------------------------------------

def _tool(name, size=4000):
    return {"name": name, "description": name + " " + "d" * size,
            "inputSchema": {"type": "object", "properties": {}}}


class _LoopBase(_TurnLoopTestBase):

    def setUp(self):
        super().setUp()
        # These tests are about the fit step leaving the SCENE's domains out; on the 16K preset those wait
        # for load_tools from the start (#635), and the small windows here (8K) stand for a full one.
        self.ac._SMALL_WINDOW_CTX = 0

    def _mk(self, script, ctx=8192, max_tokens=1024, thinking=0, tools=(), mode="local"):
        self.server.shutdown()
        self.server.server_close()
        self.server = _start_fake_server(script, mcp_tools=[_EXECUTE_CODE_TOOL] + list(tools))
        self.server.n_ctx = ctx
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = mode
        cfg.local_port = self.port
        cfg.local_ctx_size = ctx
        cfg.local_max_tokens = max_tokens
        cfg.thinking_budget_tokens = thinking
        if mode == "remote":
            cfg.remote_api_url = "http://127.0.0.1:{:d}/v1".format(self.port)
            cfg.remote_model = "remote-model"
        self.lm.set_config(cfg)
        self.lm._runtime_ctx_cache = None
        self.state.conversation_history = [{"role": "system", "content": "You are a helpful agent."}]

    def _turn(self, text, mode="AGENT"):
        statuses, texts = [], []
        self._pin_fake_bpy()
        try:
            hist = self.ac.run_conversation_turn(
                text, on_text=texts.append, on_status=statuses.append, chat_mode=mode,
                llm_url=None, model=None, mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()
        return hist, texts, statuses

    def _sent(self):
        with self.server.lock:
            return [r for r in self.server.requests if r not in self.server.memory_writer_calls]

    def _pad_for_room(self, room, text_head, tools_est):
        """A user message that leaves about *room* tokens for the reply at the harness's ctx."""
        ctx = self.server.n_ctx
        target = ctx - max(int(ctx * 0.1), 256) - self.ac._TEMPLATE_OVERHEAD_TOKENS - room
        base = self.ac._estimate_messages_tokens(self.state.conversation_history) + tools_est + 300
        return text_head + " " + "w" * int((target - base) * self.ac._CHARS_PER_TOKEN)


class TestFitBeforeSending(_LoopBase):

    def test_too_many_tools_are_made_to_fit_and_the_chat_says_so(self):
        # The A27 shape: Bforartists' default asset libraries switch on "assets", the
        # default scene "lighting" / "material"; the message names "modeling" (mesh).
        tools = [_tool("search_assets"), _tool("get_asset_libraries"), _tool("list_asset_catalogs"),
                 _tool("three_point_lighting_rig"), _tool("setup_pbr_material"),
                 _tool("set_collection_color_tag")]
        self._mk([{"content": "done"}], ctx=8192, tools=tools)
        self.ac._detect_domain_from_scene = lambda: {"assets", "lighting", "material"}
        hist, texts, statuses = self._turn("bevel the mesh edges")
        sent = self._sent()
        self.assertEqual(len(sent), 1)
        names = {t["function"]["name"] for t in sent[0].get("tools") or []}
        self.assertIn("execute_blender_code", names)            # core: never left out
        self.assertIn("load_tools", names)
        self.assertIn("set_collection_color_tag", names)        # the message's own domain
        self.assertNotIn("search_assets", names)                # scene-only, the largest: first out
        est = (self.ac._estimate_messages_tokens(sent[0]["messages"])
               + self.ac._estimate_tools_tokens(sent[0]["tools"]))
        self.assertLessEqual(est, self.ac._compute_prompt_budget(8192, 1024))
        notes = [m for m in hist if m.get("notice") == "fit"]
        self.assertEqual(len(notes), 1)
        self.assertIn("assets", notes[0]["content"])
        self.assertIn("Context Window to", notes[0]["content"])
        self.assertIn("done", texts)
        # The line is for the user: the next request never carries it.
        self._turn("thanks")
        self.assertNotIn("To fit the", json.dumps(self._sent()[-1]["messages"]))

    def test_a_dropped_domain_can_be_loaded_back(self):
        tools = [_tool("search_assets"), _tool("get_asset_libraries"), _tool("list_asset_catalogs"),
                 _tool("three_point_lighting_rig", 1000)]
        load = {"content": "Need assets.", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "load_tools", "arguments": json.dumps({"domain": "lighting"})}}]}
        self._mk([load, {"content": "done"}], ctx=8192, tools=tools)
        self.ac._detect_domain_from_scene = lambda: {"assets", "lighting"}
        hist, _texts, _st = self._turn("hello")
        reply = [m for m in hist if m.get("role") == "tool" and m.get("name") == "load_tools"]
        self.assertTrue(reply)
        if "three_point_lighting_rig" not in {t["function"]["name"] for t in self._sent()[0]["tools"]}:
            self.assertIn("Loaded lighting tools", reply[0]["content"])
        self.assertIn("three_point_lighting_rig", {t["function"]["name"] for t in self._sent()[1]["tools"]})


class TestRefusedRequestNotResent(_LoopBase):

    _OVERFLOW = json.dumps({"error": {"code": 400, "type": "exceed_context_size_error",
                                      "message": "request (9000 tokens) exceeds the available context size "
                                                 "(8192 tokens), try increasing it",
                                      "n_prompt_tokens": 9000, "n_ctx": 8192}})

    def test_remote_overflow_is_not_resent_and_explained(self):
        # Remote has no client-side budget: the same bug (the identical payload re-sent), same fix.
        self._mk([{"content": "never"}], mode="remote")
        self.server.chat_error_specs = [{"is_stream": True, "status": 400, "body": self._OVERFLOW}]
        hist, texts, _st = self._turn("hello")
        self.assertEqual(len(self._sent()), 1)
        self.assertEqual(hist[-1].get("notice"), "error")
        self.assertIn("9,000 tokens", hist[-1]["content"])
        self.assertIn("larger context window", hist[-1]["content"])

    def test_local_errors_are_in_the_chat_in_full(self):
        self._mk([{"content": "never"}])
        body = "x" * 1500 + " END-OF-THE-SERVER-TEXT"
        self.server.chat_error_specs = [{"is_stream": None, "status": 500, "body": body}] * 20
        hist, _texts, _st = self._turn("hello")
        notes = [m for m in hist if m.get("notice") == "error"]
        self.assertTrue(notes)
        self.assertIn("The request failed", notes[-1]["content"])


class TestNoCutOffLoops(_LoopBase):

    def test_little_room_sends_the_request_with_thinking_off(self):
        self._mk([{"content": "fits"}], ctx=8192, max_tokens=4096, thinking=1024)
        tools_est = self.ac._estimate_tools_tokens(self.ac._build_tool_set(
            self.ac._mcp_tools_to_openai([_EXECUTE_CODE_TOOL]), set(), log=False))
        hist, texts, _st = self._turn(self._pad_for_room(700, "Cut the gap.", tools_est))
        sent = self._sent()
        self.assertEqual(len(sent), 1)
        self.assertLess(sent[0]["max_tokens"], 1024)
        self.assertEqual(sent[0].get("thinking_budget_tokens"), 1)
        self.assertEqual(sent[0].get("chat_template_kwargs"), {"enable_thinking": False})
        self.assertIn("fits", texts)

    def test_plenty_of_room_keeps_the_configured_thinking(self):
        self._mk([{"content": "ok"}], ctx=32768, max_tokens=4096, thinking=1024)
        self._turn("hello")
        self.assertEqual(self._sent()[0].get("thinking_budget_tokens"), 1024)
        self.assertNotIn("chat_template_kwargs", self._sent()[0])

    def test_users_own_small_max_tokens_is_not_overridden(self):
        self._mk([{"content": "ok"}], ctx=32768, max_tokens=1024, thinking=512)
        self._turn("hello")
        self.assertEqual(self._sent()[0].get("thinking_budget_tokens"), 512)

    def test_cut_off_in_the_thinking_retries_once_with_thinking_off(self):
        # Was: the same request again with 2048, then 4096 -- cut off each time.
        self._mk([{"content": "", "reasoning_content": "hmm " * 50, "finish_reason": "length"},
                  {"content": "answer"}], ctx=32768, max_tokens=4096, thinking=1024)
        hist, texts, _st = self._turn("hello")
        sent = self._sent()
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0].get("thinking_budget_tokens"), 1024)
        self.assertEqual(sent[1].get("thinking_budget_tokens"), 1)
        self.assertEqual(sent[1].get("chat_template_kwargs"), {"enable_thinking": False})
        self.assertIn("answer", texts + [m.get("content") for m in hist])

    def test_cut_off_twice_stops_with_a_clear_message(self):
        self._mk([{"content": "part one", "finish_reason": "length"},
                  {"content": " part two", "finish_reason": "length"},
                  {"content": "never reached"}], ctx=32768, max_tokens=4096, thinking=1024)
        hist, _texts, _st = self._turn("write a long script")
        self.assertEqual(len(self._sent()), 2)
        self.assertEqual(hist[-1].get("notice"), "error")
        self.assertIn("cut off twice", hist[-1]["content"])
        self.assertIn("cut off twice", self.state.error)
        self.assertEqual(hist[-2]["content"], "part one part two")     # the work so far is kept

    def test_remote_requests_carry_no_local_fields(self):
        self._mk([{"content": "ok"}], mode="remote", thinking=1024)
        _h, _t, statuses = self._turn("hello")
        body = self._sent()[0]
        for key in ("thinking_budget_tokens", "chat_template_kwargs", "return_progress"):
            self.assertNotIn(key, body)
        self.assertIn("Contacting remote-model...", statuses)

    def test_local_requests_ask_for_progress_and_say_waiting(self):
        self._mk([{"content": "ok"}])
        _h, _t, statuses = self._turn("hello")
        self.assertTrue(self._sent()[0].get("return_progress"))
        self.assertIn("Waiting for the model...", statuses)


class TestStaleErrors(_LoopBase):

    def test_stop_clears_an_earlier_error(self):
        self.state.error = "LLM stream failed: old"
        self.state.error_full = "old"
        self.ac.request_stop()
        self.assertEqual((self.state.error, self.state.error_full, self.state.error_kind), ("", "", ""))

    def test_a_new_turn_starts_without_the_last_ones_error(self):
        self._mk([{"content": "ok"}])
        self.state.error = "LLM stream failed: old"
        seen = []
        real_wait = self.ac._wait_for_port

        def _wait(*a, **k):         # runs after the turn started, before any request
            seen.append(self.state.error)
            return real_wait(*a, **k)
        self.ac._wait_for_port = _wait
        self._turn("hello")
        self.assertEqual(seen, [""])


if __name__ == "__main__":
    unittest.main()
