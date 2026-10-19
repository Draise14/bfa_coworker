# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for ChatStateApplier (chat_state.py, Tier 4i "Engine 3").

Events are applied to a stand-in ``AgentState`` and the resulting history is
split with the chat panel's OWN turn grouping (``chat_turns.py``, which
ui_chat imports), so the tests
check what the panel will actually draw:

1. an external-agent turn shaped like the recorded OpenCode session
   (reasoning, a failed tool call, a successful retry, a streamed reply)
   becomes one turn: user bubble, Workshop (reasoning + two tool rows, the
   failed one marked as an error), and the reply as the conclusion.  The
   end-to-end replay of the recording itself lands with "Agent 4";
2. live fields while running (streaming text, phase, thinking) and after;
3. Stop and errors still end the turn with a visible reply;
4. plan and usage go to their sinks; stale events are ignored.

Run with::

    python -m unittest tests.test_chat_state -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_chat_state_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".chat_state"), importlib.import_module(name + ".engine")


cs, eng = _load()


def _panel_functions():
    """The panel's turn grouping (shared chat_turns.py, imported by ui_chat)."""
    turns = importlib.import_module("bfacw_chat_state_pkg.chat_turns")
    return turns.group_turns, turns.split_turn


_group_turns, _split_turn = _panel_functions()


class _State:
    def __init__(self):
        self.conversation_history = [{"role": "system", "content": "sys"}]
        self.streaming_text = ""
        self.reasoning_text = ""
        self.is_thinking = False
        self.thinking_start_time = 0.0
        self.turn_phase = ""
        self.ui_status = ""
        self.error = ""
        self.usage = []

    def record_usage(self, usage):
        self.usage.append(usage)


def _apply_all(applier, events, turn_id="t1"):
    for kind, data in events:
        applier.apply(eng.AgentEvent(kind, turn_id, data))


class TestRecordedSession(unittest.TestCase):

    def test_external_agent_turn_draws_as_one_turn(self):
        events = [("turn_start", {"text": "look up primitive_cube_add"})]
        events += [("thought", {"delta": w, "segment": "r1"}) for w in ("I should ", "search the docs.")]
        events += [
            ("tool_start", {"tool_id": "call_1", "name": "search_api_docs",
                            "title": "bfa-coworker_search_api_docs", "status": "pending"}),
            ("tool_update", {"tool_id": "call_1", "status": "in_progress",
                             "args": {"identifier": "primitive_cube_add"}}),
            ("tool_end", {"tool_id": "call_1", "status": "failed",
                          "error": "Error executing tool search_api_docs: query Field required"}),
            ("tool_start", {"tool_id": "call_2", "name": "search_api_docs", "status": "pending"}),
            ("tool_end", {"tool_id": "call_2", "status": "completed",
                          "result": '{"hits": [{"text": "primitive_cube_add(size=2.0, ...)"}]}'}),
        ]
        events += [("text", {"delta": w, "segment": "m2"})
                   for w in ("The `primitive_cube_add` ", "operator takes size and location.")]
        events.append(("turn_end", {"stop_reason": "end_turn"}))

        state = _State()
        clock = iter([100.0, 112.5]).__next__
        _apply_all(cs.ChatStateApplier(state, clock=clock), events)

        turns = _group_turns(state.conversation_history)
        self.assertEqual(len(turns), 1)
        user, process, conclusion = _split_turn(turns[0])
        self.assertEqual(user["content"], "look up primitive_cube_add")
        tools = [m for m in process if m["role"] == "tool"]
        self.assertEqual([t["name"] for t in tools], ["search_api_docs", "search_api_docs"])
        self.assertTrue(tools[0]["content"].startswith("Error"))      # drawn red
        self.assertIn("primitive_cube_add", tools[1]["content"])
        self.assertTrue(any(m["role"] == "reasoning" for m in process))
        self.assertIsNotNone(conclusion)
        self.assertIn("primitive_cube_add", conclusion["content"])
        self.assertEqual(state.conversation_history[-1]["turn_seconds"], 12.5)
        self.assertFalse(state.is_thinking)
        self.assertEqual((state.streaming_text, state.turn_phase, state.ui_status), ("", "", "Idle"))


class TestLiveAndEndStates(unittest.TestCase):

    def test_live_fields_while_running(self):
        state = _State()
        applier = cs.ChatStateApplier(state)
        _apply_all(applier, [
            ("turn_start", {"text": "hi"}),
            ("status", {"text": "Thinking"}),
            ("thought", {"delta": "Let me "}),
            ("thought", {"delta": "think."}),
            ("text", {"delta": "Hel", "segment": "m1"}),
            ("text", {"delta": "lo", "segment": "m1"}),
        ])
        self.assertTrue(state.is_thinking)
        self.assertEqual(state.streaming_text, "Hello")
        self.assertEqual(state.reasoning_text, "Let me think.")
        self.assertEqual(state.turn_phase, "thinking")
        self.assertEqual(state.ui_status, "Thinking")

    def test_preamble_goes_to_workshop_not_conclusion(self):
        state = _State()
        _apply_all(cs.ChatStateApplier(state), [
            ("turn_start", {"text": "cube"}),
            ("text", {"delta": "I'll add a cube.", "segment": "m1"}),
            ("tool_start", {"tool_id": "c1", "name": "execute_blender_code"}),
            ("tool_end", {"tool_id": "c1", "status": "completed", "result": "ok"}),
            ("text", {"delta": "Done.", "segment": "m2"}),
            ("turn_end", {"stop_reason": "end_turn"}),
        ])
        _, process, conclusion = _split_turn(_group_turns(state.conversation_history)[0])
        self.assertEqual(conclusion["content"], "Done.")
        preambles = [m for m in process if m["role"] == "reasoning" and m["label"] == "Coworker"]
        self.assertEqual(preambles[0]["content"], "I'll add a cube.")

    def test_tool_row_updated_in_place(self):
        state = _State()
        applier = cs.ChatStateApplier(state)
        _apply_all(applier, [("turn_start", {"text": "x"}),
                             ("tool_start", {"tool_id": "c1", "name": "get_objects_summary"})])
        row = state.conversation_history[-1]
        self.assertEqual(row["content"], "(running)")
        _apply_all(applier, [("tool_end", {"tool_id": "c1", "status": "completed", "result": "3 objects"})])
        self.assertIs(state.conversation_history[-1], row)  # same dict: copy buttons keep their index
        self.assertEqual(row["content"], "3 objects")

    def test_stop_and_error_end_with_a_reply(self):
        state = _State()
        _apply_all(cs.ChatStateApplier(state), [("turn_start", {"text": "long"}),
                                                ("turn_end", {"stop_reason": "cancelled"})])
        self.assertEqual(state.conversation_history[-1]["content"], "Stopped.")
        self.assertEqual(state.ui_status, "Stopped")

        state2 = _State()
        _apply_all(cs.ChatStateApplier(state2), [("turn_start", {"text": "x"}),
                                                 ("error", {"message": "agent exited (exit code 1)"}),
                                                 ("turn_end", {"stop_reason": "error"})])
        self.assertIn("agent exited", state2.conversation_history[-1]["content"])
        self.assertEqual(state2.error, "agent exited (exit code 1)")

    def test_plan_usage_and_stale_events(self):
        state = _State()
        plans = []
        applier = cs.ChatStateApplier(state, on_plan=plans.append)
        _apply_all(applier, [
            ("turn_start", {"text": "plan"}),
            ("plan", {"steps": [{"text": "Ground", "status": "done"}]}),
            ("usage", {"input_tokens": 500, "output_tokens": 20, "total_tokens": 520}),
        ])
        applier.apply(eng.AgentEvent("text", "old-turn", {"delta": "stale"}))
        self.assertEqual(plans, [[{"text": "Ground", "status": "done"}]])
        self.assertEqual(state.usage, [{"prompt_tokens": 500, "completion_tokens": 20, "total_tokens": 520}])
        self.assertEqual(state.streaming_text, "")


if __name__ == "__main__":
    unittest.main()
