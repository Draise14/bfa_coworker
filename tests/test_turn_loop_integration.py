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
import sys
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


def _mk_fake_bpy():
    """Minimal bpy stand-in for the exec'd module.

    The agent_controller source does ``import bpy`` inside its functions;
    when a pip-installed fake-bpy-module is present that import would
    resolve to it (which lacks ``app.timers``), so the fake is pinned into
    ``sys.modules['bpy']`` for the duration of each turn.  Only
    ``bpy.app.timers.register`` is touched outside guarded try/excepts:
    ``_save_code_to_text_editor_deferred`` uses it to defer text-editor
    writes to Blender's main thread. Here the timer callback is recorded
    and never run (there is no main-thread loop) — the callback body is
    fully guarded anyway.
    """
    registered = []

    def _register(cb, first_interval=0.0):  # noqa: ANN001
        registered.append(cb)

    mod = types.ModuleType("bpy")
    mod.app = types.SimpleNamespace(
        timers=types.SimpleNamespace(
            register=_register,
            unregister=lambda cb: None,
        ),
        version=(4, 5, 0),
    )
    return mod, registered


_FAKE_BPY, _FAKE_BPY_TIMERS = _mk_fake_bpy()
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
        # Minimal bpy stand-in: guarded imports fall back gracefully, and
        # _save_code_to_text_editor_deferred's bpy.app.timers.register call
        # (unguarded) lands on a recording no-op.
        "bpy": _FAKE_BPY,
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

        # ── JSON-RPC bridge stub (MCP tools/list + tools/call) ─────────
        # The bridge client's Accept header matches the LLM stream path, so
        # the JSON-RPC envelope is the reliable discriminator. FastMCP
        # stateless mode wraps the JSON-RPC response in SSE.
        if body.get("jsonrpc") == "2.0":
            params = body.get("params") or {}
            args = (params.get("arguments") or {}) if isinstance(params, dict) else {}
            code = str(args.get("code") or "")
            with server.lock:
                server.mcp_requests.append(body)
            if body.get("method") == "tools/list":
                result = {"tools": server.mcp_tools}
            elif "undo_push(message='bfa_coworker_" in code and "'snapshot'" in code:
                # The loop merges the undo bookmark + entity snapshot into a
                # single execute_blender_code call (pre-script push and
                # per-step push). When armed, the stub behaves like the real
                # bridge: the code's `result` dict is wrapped as result.result
                # and delivered via content[].text, and the snapshot reflects
                # the stub scene's CURRENT contents (so step diffs see newly
                # "created" entities, exactly like a real session).
                merged = {"status": "ok", "message": "push executed"}
                with server.lock:
                    armed = server.snapshot_script
                    names = sorted(server.scene_objects)
                if armed:
                    merged["result"] = {"snapshot": {
                        "object_names": names,
                        "mesh_names": names,
                    }}
                result = {"content": [{
                    "type": "text",
                    "text": json.dumps(merged),
                }]}
            else:
                # User tool code: mutate the stub scene for known scripted
                # creations so the next snapshot push diffs non-empty.
                with server.lock:
                    if "primitive_cube_add" in code and "Cube" not in server.scene_objects:
                        server.scene_objects.append("Cube")
                    if "primitive_uv_sphere_add" in code and "Sphere" not in server.scene_objects:
                        server.scene_objects.append("Sphere")
                result = {"content": [{
                    "type": "text",
                    "text": json.dumps(
                        {"status": "ok", "message": "executed"}),
                }]}
            payload = (
                b"event: message\ndata: "
                + json.dumps({"jsonrpc": "2.0", "id": body.get("id"),
                              "result": result}).encode()
                + b"\n\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        # ── OpenAI-compatible chat completions ─────────────────────────
        is_stream = "text/event-stream" in (self.headers.get("Accept") or "")
        messages = body.get("messages") or []
        is_writer_prompt = bool(messages) and "session-memory note" in str(
            (messages or [{}])[0].get("content") or "") and "Previous note:" in str(
            (messages or [{}, {}])[-1].get("content") or "")
        with server.lock:
            server.requests.append(body)
            if is_writer_prompt:
                # Memory-writer calls: non-streaming, no tools, small
                # max_tokens, and the distinctive session-memory system
                # prompt from session_memory.memory_writer_prompt. They are
                # recorded but must NOT consume scripted chat indices.
                server.memory_writer_calls.append(body)
            else:
                server.chat_count += 1
                idx = server.chat_count - 1
        if is_writer_prompt:
            # Distinctive (configurable) note: tests assert the note text
            # actually lands in the injected memory block, not just that
            # the writer call fired.
            response_msg = {"content": server.writer_note}
        else:
            script = server.scripted_responses
            with server.lock:
                if idx < len(script):
                    response_msg = script[idx]
                else:
                    response_msg = script[-1] if script else {"content": "ok"}
        if isinstance(response_msg, str):
            response_msg = {"content": response_msg}
        if is_stream:
            # SSE response: one full delta chunk, then [DONE].
            chunk = {
                "choices": [{
                    "delta": dict(response_msg),
                    "finish_reason": response_msg.get("finish_reason", "stop"),
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
        message = {"role": "assistant"}
        message.update(response_msg)
        payload = json.dumps({
            "choices": [{
                "message": message,
                "finish_reason": response_msg.get("finish_reason", "stop"),
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_fake_server(script, mcp_tools=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLLMHandler)
    server.scripted_responses = list(script)
    server.requests = []
    server.chat_count = 0
    server.memory_writer_calls = []
    server.mcp_requests = []
    server.mcp_tools = list(mcp_tools or [])
    server.snapshot_script = None
    server.scene_objects = []
    server.writer_note = "note"
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

    def _pin_fake_bpy(self):
        """Pin the fake bpy into sys.modules for the duration of a turn.

        agent_controller does function-local ``import bpy``; the pin makes
        those resolve to the recording stand-in regardless of whether a
        pip-installed fake-bpy-module is importable in this environment.
        """
        self._saved_bpy = sys.modules.get("bpy")
        sys.modules["bpy"] = _FAKE_BPY

    def _unpin_fake_bpy(self):
        if self._saved_bpy is not None:
            sys.modules["bpy"] = self._saved_bpy
        else:
            sys.modules.pop("bpy", None)

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
        self._pin_fake_bpy()
        try:
            history = self.ac.run_conversation_turn(
                message,
                on_text=texts.append,
                on_status=statuses.append,
                chat_mode=chat_mode,
                llm_url=None,  # resolve via (monkeypatched) llm_manager config
                model="fake-model",
                mcp_port=0,
            )
        finally:
            self._unpin_fake_bpy()
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


# MCP tools/list format for the tool the fake LLM calls in AGENT-mode
# tests (the real ``_mcp_tools_to_openai`` converts this to OpenAI format).
_EXECUTE_CODE_TOOL = {
    "name": "execute_blender_code",
    "description": "Execute Python code in Blender",
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "code to run"},
        },
        "required": ["code"],
    },
}


def _tool_call_msg(call_id, code):
    """Scripted assistant message that issues one execute_blender_code call."""
    # Non-empty content: the turn loop appends a filler user message after
    # an iteration whose assistant content is blank, which would pollute
    # the expected role sequence.
    return {
        "content": "Working on it.",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": "execute_blender_code",
                "arguments": json.dumps({"code": code}),
            },
        }],
        "finish_reason": "tool_calls",
    }


class TestToolLoopIntegration(_TurnLoopTestBase):
    """AGENT-mode turn loop with real tool-call iterations.

    The fake server doubles as the MCP JSON-RPC bridge (tools/list +
    tools/call on the same port), so the REAL ``_call_mcp_tool_sync`` HTTP
    path executes every tool call. Scripted results are status-ok so no
    smart-undo / spiral / cleanup side effects fire; the real
    ``_undo_code`` push/undo calls issued by the loop itself are answered
    by the same stub.
    """

    def _mk_server(self, script):
        self.server.shutdown()
        self.server.server_close()
        self.server = _start_fake_server(
            script, mcp_tools=[_EXECUTE_CODE_TOOL])
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = "local"
        cfg.local_port = self.port
        cfg.local_ctx_size = 8192
        cfg.local_max_tokens = 1024
        cfg.thinking_budget_tokens = 0
        self.lm.set_config(cfg)

    def _mcp_calls(self, name=None):
        with self.server.lock:
            calls = list(self.server.mcp_requests)
        if name is not None:
            calls = [c for c in calls if c.get("method") == name]
        return calls

    def _main_requests(self):
        with self.server.lock:
            return [r for r in self.server.requests
                    if r not in self.server.memory_writer_calls]

    def _arm_snapshots(self):
        """Arm the stub bridge to answer merged undo+snapshot pushes with a
        live snapshot of the stub scene (mirrors result.snapshot)."""
        self.server.snapshot_script = True

    def test_multi_iteration_tool_loop_end_to_end(self):
        """Two tool iterations, then a final prose reply.

        Proves in order: tools/list runs first; every LLM request offers
        the tool schema and passes preflight; the assistant tool_calls and
        tool results pair up in stored history in call order; the memory
        block is injected exactly once per request and never accumulates
        in the stored system prompt; and reasoning entries stay out of the
        sent request.
        """
        self._mk_server([
            _tool_call_msg("call_1", "print('step one')"),
            _tool_call_msg("call_2", "print('step two')"),
            {"content": "all done"},
        ])
        self.state.conversation_history = [
            {"role": "system", "content": "You are a helpful agent."}]
        texts = []
        self._pin_fake_bpy()
        try:
            history = self.ac.run_conversation_turn(
                "do the thing", on_text=texts.append, chat_mode="AGENT",
                llm_url=None, model="fake-model", mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()

        # The turn completed with the final scripted reply.
        self.assertEqual(self.state.error, "")
        self.assertIn("all done", texts)

        # tools/list ran first and advertised the tool.
        listed = self._mcp_calls("tools/list")
        self.assertGreaterEqual(len(listed), 1)

        # Exactly three LLM requests: two tool iterations + final reply.
        main = self._main_requests()
        self.assertEqual(len(main), 3)
        # Every request offers the tool schema and fits the budget.
        for req in main:
            self.assertIn("tools", req)
            self.assertEqual(req["tools"][0]["function"]["name"],
                             "execute_blender_code")
            total_chars = sum(len(str(m.get("content") or ""))
                              for m in req["messages"])
            self.assertLess(total_chars, 8192 * 3.5)

        # Stored history: system, user, assistant(tool_calls_1), tool_1,
        # assistant(tool_calls_2), tool_2, assistant(final) — pairs intact,
        # in call order, with matching tool_call_ids.
        roles = [m.get("role") for m in history]
        self.assertEqual(roles, [
            "system", "user", "assistant", "tool", "assistant", "tool",
            "assistant"])
        self.assertEqual(history[3]["tool_call_id"], "call_1")
        self.assertEqual(history[5]["tool_call_id"], "call_2")
        self.assertIn("executed", history[3]["content"])
        self.assertEqual(history[6]["content"], "all done")

        # Memory block (never built here — no compaction) is absent from
        # the stored system prompt.
        self.assertEqual(self.sm.store.memory_block, "")
        self.assertNotIn("Session memory", history[0]["content"])

    def test_memory_block_injected_once_per_tool_request(self):
        """Compaction on turn 1, then a tool turn: block present exactly
        once per request, and never accumulates in the stored prompt."""
        # Turn 1 (ASK): seed large history → compaction builds a block.
        self._seed_history(turns=30)
        self._run_turn("first question")
        mem_block = self.sm.store.memory_block
        self.assertTrue(mem_block)
        stored_sys = self.state.conversation_history[0]["content"]
        self.assertNotIn(mem_block, stored_sys)

        # Turn 2 (AGENT): small history + a one-iteration tool call.
        self.state.conversation_history = [
            {"role": "system", "content": "You are a helpful agent."}]
        self._mk_server([
            _tool_call_msg("call_a", "print('step')"),
            {"content": "done with tools"},
        ])
        self._pin_fake_bpy()
        try:
            history = self.ac.run_conversation_turn(
                "now with tools", on_text=lambda s: None, chat_mode="AGENT",
                llm_url=None, model="fake-model", mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()

        self.assertEqual(self.state.error, "")
        main = self._main_requests()
        # Both requests (tool iteration + final reply) carry the block...
        self.assertGreaterEqual(len(main), 2)
        for req in main:
            self.assertEqual(
                req["messages"][0]["content"].count(mem_block), 1,
                "memory block must appear exactly once per request")
        # ...and the STORED system prompt stays block-free.
        self.assertNotIn(
            mem_block, self.state.conversation_history[0]["content"])
        # Tool-call pair survived intact.
        roles = [m.get("role") for m in history]
        self.assertEqual(roles, [
            "system", "user", "assistant", "tool", "assistant"])

    def test_compaction_mid_tool_loop_keeps_pairs_intact(self):
        """A seeded history large enough to re-trigger compaction on the
        SECOND loop iteration must not corrupt the in-flight tool sequence:
        after the mid-loop compaction, the request still contains a
        well-formed assistant(tool_calls) + tool result pair for the
        in-flight call, and the turn completes normally."""
        self._mk_server([
            _tool_call_msg("call_1", "print('one')"),
            _tool_call_msg("call_2", "print('two')"),
            {"content": "loop finished"},
        ])
        # Build a history whose recent verbatim window (~20 messages of
        # 1400 chars ≈ 7.4k tokens) alone exceeds 60% of the safe budget —
        # so compaction fires AGAIN on iteration 2 even after turn 1's
        # compaction retired everything older.
        history = [{"role": "system", "content": "You are a helpful agent."}]
        for i in range(30):
            history.append({"role": "user",
                            "content": "old {:d} ".format(i) + "x" * 1400})
            history.append({"role": "assistant",
                            "content": "old reply {:d} ".format(i) + "y" * 1400})
        history.append({"role": "user", "content": "run the loop"})
        self.state.conversation_history = history

        texts = []
        self._pin_fake_bpy()
        try:
            result = self.ac.run_conversation_turn(
                "run the loop", on_text=texts.append, chat_mode="AGENT",
                llm_url=None, model="fake-model", mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()
        self.assertEqual(self.state.error, "")
        self.assertIn("loop finished", texts)

        # Compaction fired (memory block exists) and reasoning entries
        # outside the window are gone from storage.
        self.assertTrue(self.sm.store.memory_block)
        stored_roles = [m.get("role") for m in result]
        self.assertNotIn("reasoning", stored_roles)

        # Every stored tool result still has its assistant(tool_calls)
        # parent immediately before it — pairs intact after compaction.
        for i, m in enumerate(result):
            if m.get("role") == "tool":
                parent = result[i - 1]
                self.assertEqual(parent.get("role"), "assistant")
                self.assertTrue(parent.get("tool_calls"),
                                "orphaned tool result after mid-loop compaction")
                self.assertEqual(parent["tool_calls"][0]["id"],
                                 m["tool_call_id"])

        # The final request was within budget.
        main = self._main_requests()
        self.assertEqual(len(main), 3)
        last = main[-1]
        total_chars = sum(len(str(m.get("content") or ""))
                          for m in last["messages"])
        self.assertLess(total_chars, 8192 * 3.5)

    def test_entity_context_injected_once_per_request_never_accumulates(self):
        """The entity-diff context branch fires and behaves like the
        memory-block injection: exactly once per REQUEST (never
        accumulating in stored history).

        The context message is appended to the STORED history once (after
        the first result-bearing code call), but the REQUEST-side copies of
        the system prompt / user turns are re-derived per iteration. This
        test proves both sides: the request carries the marker at least
        once (compaction re-fires here, so the per-request injection also
        runs) and the stored history gains it exactly once — the aliasing
        defect class found with the memory block in ASK mode would make it
        grow per iteration."""
        self._mk_server([
            _tool_call_msg("call_1", "bpy.ops.mesh.primitive_cube_add()"),
            _tool_call_msg("call_2", "bpy.ops.mesh.primitive_uv_sphere_add()"),
            {"content": "created two primitives"},
        ])
        # Both scripted calls are mutation code. The stub bridge answers
        # every merged undo+snapshot push with a live snapshot of the stub
        # scene: empty at the initial push, {'Cube'} after call_1, and
        # {'Cube','Sphere'} after call_2 — so the step diffs see each newly
        # "created" entity exactly like a real session, and the warning
        # must reflect the ACCUMULATED diff (both entities).
        self._arm_snapshots()
        self.state.conversation_history = [
            {"role": "system", "content": "You are a helpful agent."}]
        texts = []
        self._pin_fake_bpy()
        try:
            history = self.ac.run_conversation_turn(
                "make primitives", on_text=texts.append, chat_mode="AGENT",
                llm_url=None, model="fake-model", mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()

        self.assertEqual(self.state.error, "")
        self.assertIn("created two primitives", texts)

        marker = "[System: WARNING"
        # Exactly ONE context message in stored history (appended once,
        # guarded by _entity_context_injected). By design it fires after
        # the FIRST result-bearing call, so it names only that call's
        # creation — the accumulated diff would only appear if the flag
        # allowed later updates, which it deliberately does not.
        ctx_msgs = [m for m in history
                    if m.get("role") == "user" and marker in str(m.get("content") or "")]
        self.assertEqual(len(ctx_msgs), 1,
                         "entity context must be stored exactly once")
        self.assertIn("objects: Cube", ctx_msgs[0]["content"])
        self.assertIn("meshes: Cube", ctx_msgs[0]["content"])
        self.assertNotIn("Sphere", ctx_msgs[0]["content"])
        # ...placed right after the first tool result.
        first_tool = next(i for i, m in enumerate(history)
                          if m.get("role") == "tool")
        self.assertIs(history[first_tool + 1], ctx_msgs[0])

        # ── Request side: the warning reaches the model on every request
        # after it was stored, but never duplicated within one request.
        main = self._main_requests()
        self.assertGreaterEqual(len(main), 2)
        first_req_with = None
        for req_i, req in enumerate(main):
            count = sum(
                str(m.get("content") or "").count(marker)
                for m in req["messages"])
            if req_i == 0:
                # Request 1 happens before the first result is stored...
                self.assertEqual(count, 0,
                                 "no entity warning before the first tool result")
            else:
                if count and first_req_with is None:
                    first_req_with = req_i
                if count:
                    self.assertEqual(
                        count, 1,
                        "entity warning must appear exactly once per request")
        self.assertIsNotNone(
            first_req_with,
            "the entity warning must reach the model in a later request")

        # The stored user/system turns stay clean — only the standalone
        # context message carries the marker.
        stored = self.state.conversation_history
        self.assertEqual(
            sum(str(m.get("content") or "").count(marker)
                for m in stored if m.get("role") in ("system", "user")
                and m is not ctx_msgs[0]),
            0,
            "entity warning must not accumulate into system/user turns")

    def test_memory_writer_note_lands_in_injected_block(self):
        """The memory-writer LLM note is not just requested — its text is
        what compaction stores and what gets injected into the next
        request's system prompt."""
        NOTE = ("Goal: build a lighthouse. Decisions: low-poly style. "
                "Pending: add lamp glass.")
        self._seed_history(turns=30)
        self.server.writer_note = NOTE
        history, texts, statuses = self._run_turn("first question")

        self.assertEqual(self.state.error, "")
        # The writer fired and received the retired conversation.
        with self.server.lock:
            writers = list(self.server.memory_writer_calls)
        self.assertGreaterEqual(len(writers), 1)
        self.assertIn("Previous note:", writers[0]["messages"][-1]["content"])

        # The LLM note IS the stored block (bounded, with the update stamp).
        block = self.sm.store.memory_block
        self.assertTrue(block)
        self.assertIn("build a lighthouse", block)
        self.assertIn("add lamp glass", block)
        self.assertIn("Last updated", block)

        # And the SAME note text is what the (single) request's system
        # prompt carries — exactly once, without touching stored history.
        main = self._main_requests()
        self.assertEqual(len(main), 1,
                         "one main LLM request; the writer call is separate")
        self.assertEqual(
            main[0]["messages"][0]["content"].count("build a lighthouse"), 1,
            "the writer note must reach the model exactly once per request")
        self.assertNotIn("build a lighthouse",
                         self.state.conversation_history[0]["content"],
                         "stored system prompt must not accumulate the note")

    def test_conversation_no_longer_fits_error_fires_last(self):
        """When the CURRENT user request alone cannot fit the budget, the
        friendly 'conversation no longer fits' error fires — and nothing
        is sent to the LLM for that request.

        Compaction fires first (seeded history >> trigger) and retires the
        old turns into the memory block, but the live request is pinned
        verbatim by both compaction and _fit_history_to_budget: when that
        single turn alone exceeds the budget (the user pasted a huge
        prompt), preflight must give up with the friendly error instead of
        sending a doomed request."""
        self._mk_server([{"content": "should never be reached"}])
        history = [{"role": "system", "content": "You are a helpful agent."}]
        for i in range(25):
            history.append({"role": "user",
                            "content": "old {:d} ".format(i) + "x" * 3000})
            history.append({"role": "assistant",
                            "content": "old reply {:d} ".format(i) + "y" * 3000})
        self.state.conversation_history = history
        self._pin_fake_bpy()
        try:
            history = self.ac.run_conversation_turn(
                "z" * 60000, chat_mode="AGENT", llm_url=None,
                model="fake-model", mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()
        self.assertIn("no longer fits the local context window",
                      self.state.error)
        self.assertIn("Compact Now", self.state.error)
        # No LLM request went out.
        self.assertEqual(len(self._main_requests()), 0)
        self.assertIs(history, self.state.conversation_history)


if __name__ == "__main__":
    unittest.main()
