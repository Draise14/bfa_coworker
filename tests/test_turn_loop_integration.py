# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
End-to-end integration test: drive the REAL conversation turn loop
(``agent_controller.run_conversation_turn``) against a fake LLM server.

PR #78 wired session-memory compaction into the turn loop via fragile
str_replace edits that were only verified by AST inspection.  This
harness proves the wiring behaviourally, end to end, over HTTP:

1. The prompt preflight shapes the request the fake server receives
   (roles sanitized, reasoning stripped, memory block injected into the
   system message, tool pairs repaired, budget respected).
2. The ~60% compaction trigger fires when the estimated prompt exceeds
   the safe budget, retiring old turns with a real memory-writer LLM
   call to the fake server, archiving them, and checkpointing.
3. ``role: "reasoning"`` entries are pruned from STORED history once
   they fall outside the verbatim window (not only dropped at send time).
4. The memory block is injected into the system message of the FOLLOWING
   request — exactly once, without accumulating into the stored history
   across turns (the aliasing defect this test pins).

The test loads ``agent_controller`` by exec'ing its source (the module
imports bpy, unavailable in unit tests) but every function it calls is
the real production code, including the real llm_transport HTTP layer,
the real llm_manager local-mode config, and the real session_memory
store — the only substitution is the llama-server HTTP endpoint itself.

Run with::

    python -m unittest tests.test_turn_loop_integration -v
"""

__all__ = ()

import importlib.util
import json
import os
import threading
import types
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AC_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "agent_controller.py")
_SM_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "session_memory.py")
_LM_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "llm_manager.py")


# ---------------------------------------------------------------------------
# Module loading (bpy-free execution of the real agent_controller source)
# ---------------------------------------------------------------------------

def _load_session_memory():
    spec = importlib.util.spec_from_file_location(
        "session_memory_live", _SM_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_llm_manager():
    spec = importlib.util.spec_from_file_location(
        "llm_manager_live", _LM_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


_SM = _load_session_memory()
_LM = _load_llm_manager()


def _load_transport():
    spec = importlib.util.spec_from_file_location(
        "bfa_coworker.llm_transport",
        os.path.join(_REPO, "addon", "bfa_coworker", "llm_transport.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    # agent_controller's relative imports (``from .llm_transport import
    # ...``) resolve through sys.modules["bfa_coworker"]; register a
    # package object carrying the loaded siblings so they resolve to the
    # modules this harness loaded.
    import sys
    if "bfa_coworker" not in sys.modules:
        pkg = types.ModuleType("bfa_coworker")
        pkg.__path__ = [os.path.join(_REPO, "addon", "bfa_coworker")]
        pkg.session_memory = _SM
        pkg.llm_manager = _LM
        pkg.llm_transport = module
        sys.modules["bfa_coworker"] = pkg
    # Pin the submodule entries too, so agent_controller's
    # ``from .llm_transport import ...`` / ``from . import llm_manager``
    # reuses the harness-loaded instances instead of importing fresh
    # copies with default config (which pointed the loop at port 8081).
    sys.modules["bfa_coworker.llm_transport"] = module
    sys.modules["bfa_coworker.session_memory"] = _SM
    sys.modules["bfa_coworker.llm_manager"] = _LM
    return module


def _exec_agent_controller(sm, lm):
    """Exec the real agent_controller source; return its namespace.

    The module imports bpy, which is unavailable in unit tests, but bpy is
    only referenced inside function bodies (guarded try/except) — never at
    module top level — so a plain exec works once the relative sibling
    imports resolve.  *sm* and *lm* (the live session_memory / llm_manager
    modules) are re-pinned into the bfa_coworker package entry right before
    the exec so the turn loop reads the config this test just set.
    """
    import types as _types
    import socket as _socket
    import asyncio as _asyncio
    import concurrent.futures as _cf
    import shutil as _shutil
    import subprocess as _sp
    import sys as _sys
    import re as _re
    import textwrap as _tw

    # Re-pin the per-test sibling modules: setUp loads a FRESH
    # session_memory/llm_manager per test, and the turn loop resolves its
    # siblings through this package entry (``from . import llm_manager``).
    pkg = _sys.modules["bfa_coworker"]
    pkg.session_memory = sm
    pkg.llm_manager = lm

    with open(_AC_PATH, "r", encoding="utf-8") as fh:
        source = fh.read()

    ns = {
        "__name__": "bfa_coworker.agent_controller_live",
        "__package__": "bfa_coworker",
        "__file__": _AC_PATH,
        "bpy": None,  # guarded imports fall back; never None-dereferenced
        "json": json,
        "os": os,
        "re": _re,
        "socket": _socket,
        "asyncio": _asyncio,
        "concurrent": _cf,
        "shutil": _shutil,
        "subprocess": _sp,
        "sys": _sys,
        "threading": threading,
        "textwrap": _tw,
        # Module-level stop event placeholder: the source defines its own
        # real one at line ~1340, but ``_transport.bind`` at line 1236 runs
        # BEFORE that definition, so seed the name to survive the exec.
        "_stop_event": threading.Event(),
        "types": __import__("types"),
        # Forward-referenced helpers: _parse_text_tool_calls and
        # _parse_xml_tool_calls are defined later in the source than the
        # module-level bind_helpers call; seed the names so the exec
        # survives (the source's own definitions then shadow these).
        "_parse_text_tool_calls": lambda content: [],
        "_parse_xml_tool_calls": lambda text: [],
    }
    # Exec into a REAL module object's __dict__: the test must be able to
    # rebind module globals (_agent_state, _stop_event, _session_turn_count)
    # and have the exec'd functions observe the rebinding. A SimpleNamespace
    # copy would decouple the two and silently test a ghost module.
    import sys as _pin
    module = types.ModuleType("bfa_coworker.agent_controller_live")
    module.__dict__.update(ns)
    exec(compile(source, _AC_PATH, "exec"), module.__dict__)
    _pin.modules["bfa_coworker.agent_controller_live"] = module
    return module


# ---------------------------------------------------------------------------
# Fake llama-server: scripted /v1/chat/completions + /props
# ---------------------------------------------------------------------------

class _FakeLLMHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # silence request logging
        pass

    def do_GET(self):
        if self.path.startswith("/props"):
            body = json.dumps({
                "default_generation_settings": {"n_ctx": 8192},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = {}
        server = self.server
        is_stream = "text/event-stream" in (self.headers.get("Accept") or "")
        with server.lock:
            server.requests.append(body)
            idx = len(server.requests) - 1
            messages = body.get("messages") or []
            is_writer_prompt = bool(messages) and "session-memory note" in str(
                (messages or [{}])[0].get("content") or "") and "Previous note:" in str(
                (messages or [{}, {}])[-1].get("content") or "")
            if is_writer_prompt:
                # Memory-writer calls: non-streaming, no tools, small
                # max_tokens, and the distinctive session-memory system
                # prompt from session_memory.memory_writer_prompt.
                server.memory_writer_calls.append(body)
        script = server.scripted_responses
        with server.lock:
            if idx < len(script):
                content = script[idx]
            else:
                content = script[-1] if script else "ok"
        if is_stream:
            # SSE response: one full-content delta chunk, then [DONE].
            chunk = {
                "choices": [{
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }],
            }
            payload = (
                b"data: " + json.dumps(chunk).encode() + b"\n\n"
                b"data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_fake_server(script):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLLMHandler)
    server.scripted_responses = list(script)
    server.requests = []
    server.memory_writer_calls = []
    server.lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class _TurnLoopTestBase(unittest.TestCase):
    """Shared harness: fake server + live agent_controller module."""

    def setUp(self):
        # Load (once) and register the real transport + sibling package in
        # sys.modules BEFORE exec'ing agent_controller, so the module's
        # relative imports resolve to these exact instances.
        self._load_transport()

        # Fresh session-memory store per test (isolate turn counters and
        # the memory block).
        self.sm = _load_session_memory()
        self.sm.store = self.sm.CheckpointStore()
        self.lm = _load_llm_manager()
        self._saved_lm_config = self.lm._config
        self._saved_ctx_cache = self.lm._runtime_ctx_cache
        self.lm._runtime_ctx_cache = None

        self.server = _start_fake_server(["fake reply"])
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = "local"
        cfg.local_port = self.port
        cfg.local_ctx_size = 8192
        cfg.local_max_tokens = 1024
        cfg.thinking_budget_tokens = 0
        self.lm.set_config(cfg)

        self.ac = _exec_agent_controller(self.sm, self.lm)
        # Replace the module-singleton agent state with a fresh one so the
        # test owns the conversation history and turn guard.
        self.state = self.ac.AgentState()
        self.ac._agent_state = self.state
        self.ac._stop_event = threading.Event()
        self.ac._transport.bind(self.state, self.ac._stop_event)
        self.ac._transport.bind_helpers(types.SimpleNamespace(
            strip_think_tags=self.ac._strip_think_tags,
            parse_text_tool_calls=self.ac._parse_text_tool_calls,
            parse_xml_tool_calls=self.ac._parse_xml_tool_calls,
            sanitize_message_roles=self.ac._sanitize_message_roles,
            describe_message_roles=self.ac._describe_message_roles,
            describe_history_for_log=self.ac._describe_history_for_log,
            flatten_for_plain_chat=self.ac._flatten_for_plain_chat,
        ))
        self.ac._session_turn_count = 0
        self.ac._system_prompt_cache.clear() if hasattr(
            self.ac, "_system_prompt_cache") else None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.lm._config = self._saved_lm_config
        self.lm._runtime_ctx_cache = self._saved_ctx_cache

    _transport_singleton = None

    @classmethod
    def _load_transport(cls):  # noqa: C901  (class-level cache)
        if cls._transport_singleton is None:
            cls._transport_singleton = _load_transport()
        return cls._transport_singleton

    # -- helpers -----------------------------------------------------------

    def _seed_history(self, turns=30, with_reasoning=True):
        """Seed a history large enough to trip the ~60% compaction trigger.

        30 turns of ~1500-char messages at 3.5 chars/token is ~13k tokens,
        well over 0.6 * (8192 - 1024 - 512) safe budget for an 8192 ctx.
        """
        history = [{"role": "system", "content": "You are a helpful agent."}]
        for i in range(turns):
            history.append({"role": "user",
                            "content": "request {:d} ".format(i) + "x" * 1400})
            if with_reasoning and i < turns - 10:
                history.append({"role": "reasoning",
                                "content": "chain of thought {:d}".format(i)})
            history.append({"role": "assistant",
                            "content": "reply {:d} ".format(i) + "y" * 1400})
        self.state.conversation_history = history
        return history

    def _run_turn(self, message="hello", chat_mode="ASK"):
        statuses = []
        texts = []
        history = self.ac.run_conversation_turn(
            message,
            on_text=texts.append,
            on_status=statuses.append,
            chat_mode=chat_mode,
            llm_url=None,  # resolve via (monkeypatched) llm_manager config
            model="fake-model",
            mcp_port=0,
        )
        return history, texts, statuses


class TestTurnLoopIntegration(_TurnLoopTestBase):

    def test_full_turn_fires_compaction_preflight_and_memory_injection(self):
        self._seed_history(turns=30)
        history, texts, statuses = self._run_turn("hello there")

        # ── 1. The turn completed and produced the scripted reply ────
        self.assertTrue(any("fake reply" in (t or "") for t in texts),
                        "on_text should receive the scripted reply")
        self.assertEqual(self.state.error, "")

        # ── 2. Compaction fired during the turn ───────────────────────
        # The seed history vastly exceeds 60% of the safe budget, so the
        # first loop iteration must have compacted before the first LLM
        # request.
        with self.server.lock:
            main_requests = [
                r for r in self.server.requests
                if r not in self.server.memory_writer_calls
            ]
            writer_calls = list(self.server.memory_writer_calls)
        self.assertGreaterEqual(len(writer_calls), 1,
                                "memory-writer LLM call must fire on compaction")
        self.assertEqual(len(main_requests), 1,
                         "ASK turn with content reply = exactly one main LLM request")

        # Memory-writer request shape: no tools, small max_tokens, a
        # system+user prompt built by memory_writer_prompt.
        writer_body = writer_calls[0]
        self.assertNotIn("tools", writer_body)
        self.assertLessEqual(writer_body.get("max_tokens", 0), 1024)
        self.assertIn("session-memory note",
                      str(writer_body["messages"][0].get("content")))

        # ── 3. Main request shape (preflight output) ─────────────────
        main = main_requests[0]
        main_roles = [m.get("role") for m in main["messages"]]
        self.assertEqual(main_roles[0], "system")
        self.assertNotIn("reasoning", main_roles,
                         "reasoning role must never reach the LLM request")
        self.assertEqual(main_roles[-1], "user")
        self.assertEqual(main["messages"][-1]["content"], "hello there")
        self.assertEqual(main.get("model"), "fake-model")
        self.assertEqual(main.get("max_tokens"), 1024)
        self.assertNotIn("tools", main,
                         "ASK mode sends no tool schema")
        # Budget fit: prompt must respect the computed budget.
        total_chars = sum(len(str(m.get("content") or ""))
                          for m in main["messages"])
        self.assertLess(total_chars, 8192 * 3.5,
                        "preflight/budget-fit must keep the prompt inside ctx")

        # ── 4. Memory block injected into the sent system message ────
        sent_system = main["messages"][0]["content"]
        mem_block = self.sm.store.memory_block
        self.assertTrue(mem_block, "compaction must produce a memory block")
        self.assertIn(mem_block, sent_system,
                      "the memory block must be injected into the system prompt")

        # ── 5. Stored history was compacted and reasoning-pruned ─────
        stored = self.state.conversation_history
        self.assertIs(stored, history)  # compacted IN PLACE via history[:]
        self.assertEqual(stored[0]["role"], "system")
        self.assertLess(len(stored), 62,
                        "compaction must retire old turns from stored history")
        self.assertTrue(
            all(m.get("role") != "reasoning" for m in stored[1:]),
            "reasoning entries outside the verbatim window must be pruned "
            "from stored history")

        # ── 6. Checkpoint snapshot recorded for the compaction ───────
        payload = self.sm.store.to_payload()
        self.assertGreaterEqual(len(payload.get("checkpoints", [])), 1)
        ckpts = payload["checkpoints"]
        self.assertTrue(any(
            c.get("reason") == "compaction" for c in ckpts),
            "an automatic checkpoint with reason=compaction must be recorded")

    def test_memory_block_does_not_accumulate_in_stored_system_prompt(self):
        # Turn 1: seeds + compaction + memory block built.
        self._seed_history(turns=30)
        self._run_turn("first question")
        mem_after_turn1 = self.sm.store.memory_block
        self.assertTrue(mem_after_turn1)

        # Turn 2: start from a small session so compaction must NOT
        # re-trigger (the 30-turn seed's verbatim window is itself large
        # enough to re-trigger every turn, which would rebuild the block).
        # The point: the system prompt in STORAGE must not grow with the
        # memory block (the injection must not alias the stored dict).
        self.state.conversation_history = [
            {"role": "system", "content": "You are a helpful agent."},
            {"role": "user", "content": "quick follow-up"},
            {"role": "assistant", "content": "sure thing"},
        ]
        n_req_before = len(self.server.requests)
        self._run_turn("second question")
        # No re-compaction on turn 2: no memory-writer call fired.
        with self.server.lock:
            turn2_requests = list(self.server.requests[n_req_before:])
            turn2_writers = [
                r for r in turn2_requests
                if r in self.server.memory_writer_calls]
        self.assertEqual(turn2_writers, [],
                         "turn 2 must not re-compact or call the memory writer")
        main = [r for r in turn2_requests if r not in turn2_writers]
        self.assertEqual(len(main), 1)
        # Turn 2's system message carries the block exactly once...
        self.assertEqual(main[0]["messages"][0]["content"].count(
            mem_after_turn1), 1)
        # ...and the STORED system prompt is free of the block.
        stored_sys = self.state.conversation_history[0]["content"]
        self.assertNotIn(mem_after_turn1, stored_sys,
                         "stored system prompt must not accumulate the "
                         "memory block across turns")
        self.assertEqual(self.sm.store.memory_block, mem_after_turn1,
                         "no re-compaction on turn 2 — block unchanged")

    def test_turn_loop_strips_reasoning_at_send_time_only_within_window(self):
        # A small history with a reasoning entry that is INSIDE the
        # verbatim window: it must be sent-stripped but NOT pruned from
        # storage (panel still shows it) when no compaction fires.
        small = [{"role": "system", "content": "sys"},
                 {"role": "user", "content": "hi"},
                 {"role": "reasoning", "content": "quiet thoughts"},
                 {"role": "assistant", "content": "hello"}]
        self.state.conversation_history = small
        self._run_turn("again")
        stored = self.state.conversation_history
        roles = [m.get("role") for m in stored]
        self.assertIn("reasoning", roles,
                      "reasoning inside the verbatim window stays in storage")
        # But the request that went out had it stripped.
        main_requests = [
            r for r in self.server.requests
            if r not in self.server.memory_writer_calls
        ]
        self.assertEqual(len(main_requests), 1)
        req_roles = [m.get("role") for m in main_requests[0]["messages"]]
        self.assertNotIn("reasoning", req_roles)

    def test_reentrancy_guard_blocks_parallel_turns(self):
        self.state.conversation_history = [
            {"role": "system", "content": "sys"}]
        before = list(self.state.conversation_history)
        self.state.turn_active = True
        result = self.ac.run_conversation_turn("should not run")
        self.assertEqual(result, before)
        self.assertEqual(len(self.server.requests), 0)


if __name__ == "__main__":
    unittest.main()
