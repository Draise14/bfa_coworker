# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Plan mode (#194): plan without changing anything, on every engine.

1. The tools a plan may use only look (no execute_blender_code, no writes).
2. External Agent: the prompt carries the plan note; Coworker's changing
   tools are refused in a Plan turn, looking ones allowed.
3. The local loop: Plan has its instruction, only looking tools, a guard
   that drops any other call, and no auto-continue.

Run with::

    python -m unittest tests.test_plan_mode -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_plan_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".engine"), importlib.import_module(name + ".engine_acp")


eng, ea = _load()
_AC = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")


def _state():
    return types.SimpleNamespace(conversation_history=[], streaming_text="", reasoning_text="",
                                 is_thinking=False, thinking_start_time=0.0, turn_phase="", ui_status="", error="")


class TestSafeTools(unittest.TestCase):

    def test_only_tools_that_look(self):
        for changing in ("execute_blender_code", "setup_pbr_material", "wire_node_group", "place_asset_in_scene",
                         "download_polyhaven_asset", "set_collection_color_tag", "render_viewport_to_path"):
            self.assertNotIn(changing, eng.PLAN_SAFE_TOOLS)
        for looking in ("get_scene_digest", "get_objects_summary", "search_api_docs", "update_plan"):
            self.assertIn(looking, eng.PLAN_SAFE_TOOLS)


class TestExternalAgent(unittest.TestCase):

    def test_prompt_carries_the_plan_note(self):
        engine = ea.AcpEngine(_state())
        blocks = engine._blocks(eng.Prompt(text="Build a campfire", chat_mode="PLAN"))
        self.assertIn("[Plan mode]", blocks[0]["text"])
        self.assertNotIn("[Plan mode]", engine._blocks(eng.Prompt(text="Build"))[0]["text"])

    def test_changing_tools_refused_in_a_plan_turn(self):
        engine = ea.AcpEngine(_state())
        engine._turn_id, engine._turn_mode = "t1", "PLAN"
        options = [{"optionId": "a1", "kind": "allow_once"}, {"optionId": "r1", "kind": "reject_once"}]
        answers = []
        engine._on_permission({"toolCall": {"title": "mcp__bfa-coworker__execute_blender_code"}, "options": options},
                              answers.append)
        engine._on_permission({"toolCall": {"title": "mcp__bfa-coworker__get_scene_digest"}, "options": options},
                              answers.append)
        self.assertEqual(answers, ["r1", "a1"])
        engine._turn_mode = "AGENT"
        engine._on_permission({"toolCall": {"title": "mcp__bfa-coworker__execute_blender_code"}, "options": options},
                              answers.append)
        self.assertEqual(answers[-1], "a1")


class TestLocalLoop(unittest.TestCase):

    def test_instruction_tools_guard_and_no_auto_continue(self):
        self.assertIn("_PLAN_MODE_PROMPT_ADDENDUM = (", _AC)
        self.assertIn('if chat_mode == "PLAN" and _PLAN_MODE_PROMPT_ADDENDUM not in history[0]["content"]', _AC)
        self.assertIn('if chat_mode == "PLAN":     # #194: only the tools that look', _AC)
        self.assertIn('if chat_mode == "PLAN" and raw_tool_calls:', _AC)
        self.assertIn('if chat_mode in ("ASK", "PLAN") or not openai_tools or _auto_round >= _max_auto_rounds:', _AC)


if __name__ == "__main__":
    unittest.main()
