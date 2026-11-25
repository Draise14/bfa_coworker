# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A tool is Coworker's by its whole title, never by a word in it (#642)."""

__all__ = ()

import sys
import unittest

import test_engine_acp as base

ea = base.ea

_OPTIONS = [{"optionId": "a1", "kind": "allow_once"}, {"optionId": "a2", "kind": "allow_always"},
            {"optionId": "r1", "kind": "reject_once"}]

_NOT_OURS = (
    "https://x.example/?blender",
    "Fetch https://blender.org",
    "echo bfa-coworker",
    "bfa-coworker_x; echo hi",
    "mcp__bfa-coworker__execute_blender_code && echo hi",
    "mcp__other__execute_blender_code",
    "bfa-coworker_execute_blender_code\nrm -rf x",
    "mcp__bfa-coworker__execute_blender_code\n",
    "BLENDER",
    "blender",
)


def _coworker_tool():
    return sys.modules["bfacw_engine_acp_pkg.acp_client"].coworker_tool


def _engine(only_coworker_tools):
    engine = ea.AcpEngine(base._state())
    engine._turn_id = "t1"
    engine.only_coworker_tools = only_coworker_tools
    return engine


def _ask(engine, call):
    answers = []
    engine._on_permission({"toolCall": call, "options": _OPTIONS}, answers.append)
    return answers


class TestHelper(unittest.TestCase):
    def test_known_forms(self):
        tool = _coworker_tool()
        self.assertEqual(tool({"title": "bfa-coworker_execute_blender_code"}), "execute_blender_code")
        self.assertEqual(tool({"title": "mcp__bfa-coworker__search_api_docs"}), "search_api_docs")

    def test_other_titles(self):
        tool = _coworker_tool()
        for title in _NOT_OURS + ("", "bfa-coworker_", "mcp__bfa-coworker__", " bfa-coworker_x",
                                  "BFA-COWORKER_x", "bfa-coworker_a b", "bfa-coworker_a/b"):
            self.assertEqual(tool({"title": title}), "", repr(title))
        self.assertEqual(tool({}), "")
        self.assertEqual(tool({"title": None, "kind": "other"}), "")
        self.assertEqual(tool({"title": 5}), "")
        self.assertEqual(tool(None), "")

    def test_claude_code_report_must_agree(self):
        tool = _coworker_tool()
        title = "mcp__bfa-coworker__search_api_docs"

        def call(**claude):
            return {"title": title, "_meta": {"claudeCode": claude}}
        self.assertEqual(tool(call(toolName=title, mcpServer={"name": "bfa-coworker"})), "search_api_docs")
        self.assertEqual(tool(call(toolName="Bash")), "")
        self.assertEqual(tool(call(toolName=title, mcpServer={"name": "other"})), "")
        self.assertEqual(tool(call(mcpServer="bfa-coworker")), "")
        self.assertEqual(tool({"title": title, "_meta": {"claudeCode": "x"}}), "")


class TestHandler(unittest.TestCase):
    def test_denied_with_only_coworker_tools(self):
        for title in _NOT_OURS:
            engine = _engine(True)
            self.assertEqual(_ask(engine, {"title": title}), ["r1"], repr(title))

    def test_ask_card_without_only_coworker_tools(self):
        for title in _NOT_OURS:
            engine = _engine(False)
            self.assertEqual(_ask(engine, {"title": title}), [], repr(title))
            request = engine.approvals.current()
            self.assertIsNotNone(request, repr(title))
            self.assertFalse(request.coworker_tool)

    def test_ours_still_allowed_without_card(self):
        for title in ("bfa-coworker_execute_blender_code", "mcp__bfa-coworker__execute_blender_code"):
            for only in (True, False):
                engine = _engine(only)
                self.assertEqual(_ask(engine, {"title": title}), ["a1"], title)
                self.assertIsNone(engine.approvals.current())

    def test_claude_code_call_as_recorded(self):
        engine = _engine(True)
        call = {"name": "mcp__bfa-coworker__search_api_docs", "title": "mcp__bfa-coworker__search_api_docs",
                "kind": "other", "_meta": {"claudeCode": {"toolName": "mcp__bfa-coworker__search_api_docs",
                                                          "mcpServer": {"name": "bfa-coworker"}}}}
        self.assertEqual(_ask(engine, call), ["a1"])

    def test_plan_mode_refuses_changing_tool_of_ours(self):
        for title in ("bfa-coworker_execute_blender_code", "mcp__bfa-coworker__execute_blender_code"):
            engine = _engine(True)
            engine._turn_mode = "PLAN"
            self.assertEqual(_ask(engine, {"title": title}), ["r1"], title)
        engine = _engine(True)
        engine._turn_mode = "PLAN"
        self.assertEqual(_ask(engine, {"title": "bfa-coworker_search_api_docs"}), ["a1"])


if __name__ == "__main__":
    unittest.main()
