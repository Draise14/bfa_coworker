# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the External Agent engine (engine_acp.py, task "Agent 4"), driven by
the fake ACP agent replaying recorded OpenCode sessions.

1. Connect: start, initialize, open a session with our MCP server; a
   missing agent reports a plain error.
2. A turn from the chat lands in the chat state like a local turn: your
   message, Workshop tool rows, the reply; the turn ends.
3. Queue while busy; Stop cancels; the agent's plan fills Goal & Plan.
4. Permissions: Coworker's tools allowed, anything else refused (with a note).
5. The prompt carries the Blender context, Ask mode, and images when the
   agent takes them.
6. An agent that crashes mid-turn ends the turn with an error.

Run with::

    python -m unittest tests.test_engine_acp -v
"""

__all__ = ()

import importlib
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_FIXTURES = _REPO / "tests" / "fixtures" / "acp"
_FAKE = _REPO / "tests" / "fake_acp_agent.py"


def _load():
    name = "bfacw_engine_acp_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".engine_acp"), importlib.import_module(name + ".engine")


ea, eng = _load()
_MCP = {"command": "python", "args": ["-m", "blmcp", "--transport", "stdio"], "env": {"BFACW_PORT": "9876"}}


def _state():
    return types.SimpleNamespace(conversation_history=[{"role": "system", "content": "sys"}],
                                 streaming_text="", reasoning_text="", is_thinking=False,
                                 thinking_start_time=0.0, turn_phase="", ui_status="", error="")


def _engine(fixture, *extra):
    engine = ea.AcpEngine(_state())
    engine.connect([sys.executable, str(_FAKE), str(_FIXTURES / fixture), *extra], _MCP,
                   tempfile.mkdtemp(prefix="bfacw_acp_"), label="OpenCode", wait=True)
    return engine


def _wait(engine, timeout=20.0):
    end = time.time() + timeout
    while engine.is_busy() and time.time() < end:
        time.sleep(0.05)
        engine.drain()
    engine.drain()


class TestConnect(unittest.TestCase):

    def test_connects_with_our_mcp_server(self):
        engine = _engine("opencode_mcp_tool_call.jsonl")
        try:
            self.assertTrue(engine.connected, engine.error)
            self.assertTrue(engine.session_id)
            self.assertTrue(engine.status.startswith("Connected to OpenCode"))
            self.assertTrue(engine.caps.images)            # OpenCode takes images
        finally:
            engine.disconnect()
        self.assertFalse(engine.connected)

    def test_claude_code_session(self):
        # Recorded live with claude-agent-acp 0.94.0: it asks before our tool runs.
        engine = ea.AcpEngine(_state())
        engine.connect([sys.executable, str(_FAKE), str(_FIXTURES / "claude_plan_tool_call.jsonl")], _MCP,
                       tempfile.mkdtemp(prefix="bfacw_acp_"), label="Claude Code", wait=True)
        try:
            self.assertTrue(engine.status.startswith("Connected to Claude Code"), engine.status)
            self.assertTrue(engine.caps.images and engine.caps.load_session)
            engine.submit(eng.Prompt(text="Look up primitive_cube_add"))
            _wait(engine)
        finally:
            engine.disconnect()
        history = engine.state.conversation_history
        self.assertIn("search_api_docs", [m.get("name") for m in history if m["role"] == "tool"])
        self.assertIn("primitive_cube_add", history[-1]["content"])

    def test_missing_agent_says_so(self):
        engine = ea.AcpEngine(_state())
        engine.connect(["definitely-not-an-agent-xyz"], _MCP, tempfile.mkdtemp(), wait=True)
        self.assertFalse(engine.connected)
        self.assertTrue(engine.status.startswith("Couldn't connect"))


class TestTurns(unittest.TestCase):

    def test_turn_lands_in_the_chat_like_a_local_turn(self):
        engine = _engine("opencode_mcp_tool_call.jsonl")
        try:
            turn_id, pos = engine.submit(eng.Prompt(text="Look up primitive_cube_add"))
            self.assertTrue(turn_id)
            self.assertEqual(pos, 0)
            _wait(engine)
        finally:
            engine.disconnect()
        history = engine.state.conversation_history
        roles = [m["role"] for m in history]
        self.assertEqual(history[1], {"role": "user", "content": "Look up primitive_cube_add", "turn_start": True})
        tools = [m for m in history if m["role"] == "tool"]
        self.assertEqual([t["name"] for t in tools], ["search_api_docs", "search_api_docs"])
        self.assertTrue(tools[0]["content"].startswith("Error"))      # the failed first call
        self.assertEqual(roles[-1], "assistant")
        self.assertIn("primitive_cube_add", history[-1]["content"])
        self.assertFalse(engine.state.is_thinking)
        self.assertFalse(engine.is_busy())

    def test_queue_while_busy_and_cancel(self):
        engine = ea.AcpEngine(_state())
        engine._turn_id = "running"
        self.assertEqual(engine.submit(eng.Prompt(text="next")), (None, 1))
        self.assertEqual(engine.submit(eng.Prompt(text="after")), (None, 2))
        engine.cancel()                                   # Stop also drops the queue
        self.assertEqual(len(engine._queue), 0)

    def test_attached_image_shows_in_the_chat(self):
        engine = ea.AcpEngine(_state())
        engine._run = lambda turn_id, prompt: None        # no agent: just the chat side
        engine.submit(eng.Prompt(text="build a pagoda", images=["data:image/png;base64,AA=="],
                                 image_names=["FB_IMG_1467.jpg"]))
        engine.drain()
        user = engine.state.conversation_history[1]
        self.assertEqual(user["content"], "build a pagoda\n[attached: FB_IMG_1467.jpg]")   # thumbnail marker

    def test_queue_is_visible_and_clearable(self):
        engine = ea.AcpEngine(_state())
        engine._turn_id = "running"
        engine.submit(eng.Prompt(text="next one", chat_mode="ASK"))
        self.assertEqual(engine.queue_items(), [{"message": "next one", "chat_mode": "ASK"}])
        engine.clear_queue()
        self.assertEqual(engine.queue_items(), [])

    def test_agent_plan_fills_goal_and_plan(self):
        gp = importlib.import_module("bfacw_engine_acp_pkg.goal_plan")
        goal = gp.GoalPlan()
        engine = _engine("opencode_plan.jsonl")
        engine.on_request = goal.set_request
        engine.applier.on_plan = lambda steps: goal.apply_update({"steps": steps})
        try:
            engine.submit(eng.Prompt(text="Plan a campfire scene"))
            _wait(engine)
        finally:
            engine.disconnect()
        self.assertEqual(goal.turn_goal, "Plan a campfire scene")
        self.assertTrue(goal.steps)
        self.assertTrue(all(s["status"] in ("todo", "doing", "done") for s in goal.steps))

    def test_crash_mid_turn_ends_the_turn_with_an_error(self):
        engine = _engine("opencode_mcp_tool_call.jsonl", "--exit-after", "6")
        try:
            engine.submit(eng.Prompt(text="Look up primitive_cube_add"))
            _wait(engine)
        finally:
            engine.disconnect()
        last = engine.state.conversation_history[-1]
        self.assertEqual(last["role"], "assistant")
        self.assertIn("error", last["content"].lower())
        self.assertFalse(engine.state.is_thinking)


class TestPermissionsAndPrompt(unittest.TestCase):

    def _ask(self, title):
        engine = ea.AcpEngine(_state())
        engine._turn_id = "t1"
        answers = []
        options = [{"optionId": "a1", "kind": "allow_once"}, {"optionId": "a2", "kind": "allow_always"},
                   {"optionId": "r1", "kind": "reject_once"}]
        engine._on_permission({"toolCall": {"title": title}, "options": options}, answers.append)
        return answers[0], [e for e in engine.bus.drain() if e.kind == "status"]

    def test_coworker_tools_allowed_others_refused(self):
        answer, notes = self._ask("bfa-coworker_execute_blender_code")
        self.assertEqual((answer, notes), ("a2", []))
        answer, notes = self._ask("bash")
        self.assertEqual(answer, "r1")
        self.assertIn("only use Coworker's Blender tools", notes[0].data["text"])

    def test_claude_code_tool_names(self):
        # Claude Code titles our tools mcp__bfa-coworker__<tool>.
        answer, notes = self._ask("mcp__bfa-coworker__search_api_docs")
        self.assertEqual((answer, notes), ("a2", []))
        acp = sys.modules["bfacw_engine_acp_pkg.acp_client"]
        self.assertEqual(acp._tool_name("mcp__bfa-coworker__execute_blender_code"), "execute_blender_code")
        self.assertEqual(acp._tool_name("bfa-coworker_execute_blender_code"), "execute_blender_code")

    def test_own_tools_allowed_when_not_only_coworker(self):
        engine = ea.AcpEngine(_state())
        engine._turn_id, engine.only_coworker_tools = "t1", False
        answers = []
        engine._on_permission({"toolCall": {"title": "bash"},
                               "options": [{"optionId": "a1", "kind": "allow_once"},
                                           {"optionId": "r1", "kind": "reject_once"}]}, answers.append)
        self.assertEqual(answers, ["a1"])

    def test_prompt_blocks(self):
        engine = ea.AcpEngine(_state())
        engine.caps = eng.EngineCaps(label="x", images=True)
        blocks = engine._blocks(eng.Prompt(text="make it red", context="3 objects, Cube selected",
                                           chat_mode="ASK", images=["data:image/png;base64,iVBORw0KGgo="]))
        self.assertIn("[Blender: 3 objects, Cube selected]", blocks[0]["text"])
        self.assertIn("don't change the Blender scene", blocks[0]["text"])
        self.assertEqual(blocks[1], {"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgo="})
        engine.caps = eng.EngineCaps(label="x", images=False)
        self.assertEqual(len(engine._blocks(eng.Prompt(text="x", images=["data:image/png;base64,AA=="]))), 1)


if __name__ == "__main__":
    unittest.main()
