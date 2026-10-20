# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for External Agent hardening (Tier 4, task "Agent 7").

1. Not logged in: the error says so, with the preset's login hint, not a
   raw JSON-RPC message.
2. The agent crashes mid-turn: the chat shows what it last printed.
3. Stop that the agent ignores: after a grace time Coworker closes it, ends
   the turn and says why.
4. The agent process is recorded for the crash reaper while it runs, and
   forgotten when it's closed.

Run with::

    python -m unittest tests.test_agent_hardening -v
"""

__all__ = ()

import importlib
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_FIXTURES = _REPO / "tests" / "fixtures" / "acp"
_FAKE = _REPO / "tests" / "fake_acp_agent.py"


def _load():
    name = "bfacw_hardening_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return [importlib.import_module(name + "." + m) for m in ("engine_acp", "engine", "acp_client", "process_guard")]


ea, eng, acp, guard = _load()
_MCP = {"command": "python", "args": ["-m", "blmcp"], "env": {}}


def _state():
    return types.SimpleNamespace(conversation_history=[{"role": "system", "content": "sys"}],
                                 streaming_text="", reasoning_text="", is_thinking=False,
                                 thinking_start_time=0.0, turn_phase="", ui_status="", error="")


def _wait(engine, timeout=20.0):
    end = time.time() + timeout
    while engine.is_busy() and time.time() < end:
        time.sleep(0.05)
        engine.drain()
    engine.drain()


class _Stub:
    """An agent that opens a session; its prompt waits until it is closed."""

    def __init__(self, new_session_error=None):
        self.new_session_error = new_session_error
        self.closed = threading.Event()
        self.cancels = 0
        self.alive = True

    def start(self):
        pass

    def initialize(self):
        return types.SimpleNamespace(name="stub", version="", image=False, load_session=False)

    def new_session(self, cwd, servers):
        if self.new_session_error:
            raise self.new_session_error
        return {"sessionId": "s1"}

    def prompt(self, session_id, blocks, timeout=None):
        self.closed.wait(10)
        raise acp.AcpError("agent closed")

    def cancel(self, _session_id):
        self.cancels += 1           # ...and ignores it

    def close(self, timeout=3.0):
        self.alive = False
        self.closed.set()


def _stub_engine(stub, label="Claude Code", hint=""):
    engine = ea.AcpEngine(_state(), client_factory=lambda *a, **k: stub)
    engine.login_hint = hint
    engine.connect(["stub"], _MCP, tempfile.mkdtemp(), label=label, wait=True)
    return engine


class TestErrors(unittest.TestCase):

    def test_not_logged_in_says_how_to_log_in(self):
        hint = "Log in to Claude Code once (run `claude` in a terminal)."
        engine = _stub_engine(_Stub(acp.AcpError("Authentication required", -32000)), hint=hint)
        self.assertFalse(engine.connected)
        self.assertEqual(engine.status, "Couldn't connect: Claude Code isn't logged in. " + hint)

    def test_other_errors_keep_their_message(self):
        engine = _stub_engine(_Stub(acp.AcpError("Model not found\nline 2 of the agent's output")))
        self.assertEqual(engine.status, "Couldn't connect: Model not found")
        self.assertIn("```\nline 2 of the agent's output\n```", engine.error)

    def test_crash_mid_turn_shows_what_the_agent_printed(self):
        engine = ea.AcpEngine(_state())
        engine.connect([sys.executable, str(_FAKE), str(_FIXTURES / "opencode_mcp_tool_call.jsonl"),
                        "--exit-after", "6"], _MCP, tempfile.mkdtemp(), label="OpenCode", wait=True)
        try:
            engine.submit(eng.Prompt(text="Look up primitive_cube_add"))
            _wait(engine)
        finally:
            engine.disconnect()
        reply = engine.state.conversation_history[-1]["content"]
        self.assertIn("simulated crash", reply)          # the agent's stderr, in a code block
        self.assertIn("```", reply)


class TestStop(unittest.TestCase):

    def test_an_agent_that_ignores_stop_is_closed(self):
        stub = _Stub()
        engine = _stub_engine(stub, label="OpenCode")
        engine.stop_grace = 0.2
        engine.submit(eng.Prompt(text="spin forever"))
        time.sleep(0.1)
        engine.cancel()
        _wait(engine, timeout=5)
        self.assertEqual(stub.cancels, 1)
        self.assertTrue(stub.closed.is_set())
        self.assertFalse(engine.is_busy())
        self.assertFalse(engine.connected)
        self.assertIn("OpenCode didn't stop when asked", engine.state.conversation_history[-1]["content"])

    def test_an_agent_that_stops_is_left_alone(self):
        stub = _Stub()
        engine = _stub_engine(stub)
        engine.stop_grace = 0.2
        engine._turn_id = "t_done"       # a turn that ends before the grace time is up
        engine.cancel()
        engine._turn_id = ""
        time.sleep(0.4)
        self.assertFalse(stub.closed.is_set())


class TestProcessGuard(unittest.TestCase):

    def test_agent_is_recorded_while_it_runs(self):
        client = acp.AcpClient([sys.executable, str(_FAKE), str(_FIXTURES / "opencode_cancel.jsonl")],
                               cwd=tempfile.mkdtemp())
        client.start()
        try:
            pid = client.proc.pid
            entries = [e for e in guard._load() if e.get("pid") == pid]
            self.assertEqual([e["kind"] for e in entries], ["acp-agent"])
        finally:
            client.close()
        self.assertFalse([e for e in guard._load() if e.get("pid") == pid])


if __name__ == "__main__":
    unittest.main()
