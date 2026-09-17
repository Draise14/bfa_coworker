"""
Regression tests for Ask mode (issue #66).

Ask mode must be strictly informational: the coworker answers questions
with prose and never executes tools, even if the model emits tool-call
shaped output.  Three layers are tested:

1. ``_parse_text_tool_calls`` / ``_parse_xml_tool_calls`` are only fed
   the model's output when tools were actually offered.  In Ask mode no
   tools are offered, so JSON/XML blocks in the model's prose must NOT
   become executable tool calls.

2. ``_run_conversation_turn_inner`` carries a hard guard: tool calls
   present in an LLM response are suppressed in Ask mode (never passed
   to ``_call_mcp_tool_sync``).

3. The system prompt gets an informational-only addendum in Ask mode
   and the ``chat_mode`` parameter reaches ``_openai_chat_completions``
   so prose temperature / gating apply.

Loaded from source (the module imports bpy, which is not available in
the unit-test environment).

Run with::

    python -m unittest tests.test_ask_mode -v
"""

__all__ = ()

import ast
import json
import os
import re
import types
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AC_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "agent_controller.py")


def _load_source():
    with open(_AC_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_func(source: str, name: str, extra: dict | None = None) -> object:
    """Extract one top-level function from agent_controller.py source."""
    marker = "\ndef {:s}(".format(name)
    start = source.find(marker)
    if start < 0:
        raise ImportError("{:s} not found in source".format(name))
    start += 1  # skip the leading newline

    # End at the next top-level def/class/comment-divider at same indent.
    end = len(source)
    search_from = start + 100
    for m in ["\ndef _", "\ndef ", "\nclass ", "\n# ---"]:
        idx = source.find(m, search_from)
        if 0 <= idx < end:
            end = idx

    func_source = source[start:end]
    mod = types.ModuleType("_ac_extract")
    mod.__dict__["json"] = json
    mod.__dict__["re"] = re
    mod.__dict__["Any"] = object
    mod.__dict__["__file__"] = _AC_PATH
    if extra:
        mod.__dict__.update(extra)
    exec(compile(func_source, _AC_PATH, "exec"), mod.__dict__)
    return mod.__dict__[name]


_src = _load_source()
_parse_text_tool_calls = _extract_func(_src, "_parse_text_tool_calls")
_parse_xml_tool_calls = _extract_func(_src, "_parse_xml_tool_calls")

# The Ask-mode addendum constant must exist in the source and be appended
# to the system prompt when chat_mode == "ASK" (see source-level tests).
_ASK_ADDENDUM_ASSIGN_RE = re.compile(
    r'^_ASK_MODE_PROMPT_ADDENDUM\s*=\s*(.+?)(?=\n\S)', re.DOTALL | re.MULTILINE
)


class TestFallbackParsersGatedOnTools(unittest.TestCase):
    """Fallback parsers must not mint tool calls when none were offered."""

    def test_text_parser_still_parses_when_given_content(self):
        # The parser itself is unchanged; gating happens at the call site.
        content = '{"tool": "execute_blender_code", "arguments": {"code": "bpy.ops.mesh.primitive_cube_add()"}}'
        calls = _parse_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "execute_blender_code")

    def test_xml_parser_still_parses_when_given_content(self):
        text = (
            "<tool_call>\n"
            "<function=execute_blender_code>\n"
            "<parameter=code>\n"
            "bpy.ops.mesh.primitive_cube_add()\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        calls = _parse_xml_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "execute_blender_code")

    def test_call_sites_gated_on_tools_being_offered(self):
        """Both fallback parse blocks run only when ``tools`` is non-empty."""
        # Text-based fallback block.
        self.assertRegex(
            _src,
            r'if not tools_tried and not tool_calls and tools:\n'
            r'\s+text_calls = _parse_text_tool_calls',
        )
        # XML fallback block.
        self.assertRegex(
            _src,
            r'if not msg\.get\("tool_calls"\) and tools:\n'
            r'\s+xml_sources: list\[tuple\[str, str\]\] = \[\]',
        )

    def test_tools_as_text_downgrade_never_in_ask_mode_path(self):
        """The 500 template-fault downgrade requires tools to be offered."""
        self.assertRegex(
            _src,
            r'if tools_tried and _is_500 and _fault == _FAULT_TEMPLATE and tools:',
        )


class TestAskModeExecutionGuard(unittest.TestCase):
    """run_conversation_turn must never execute tools in Ask mode."""

    def test_guard_present_before_tool_processing(self):
        """The suppression guard sits between response parsing and execution."""
        m = re.search(
            r'# Check for tool calls\.\n'
            r'\s+raw_tool_calls = msg\.get\("tool_calls"\)\n'
            r'.*?'
            r'if chat_mode == "ASK" and raw_tool_calls:',
            _src,
            re.DOTALL,
        )
        self.assertIsNotNone(
            m, "ASK-mode tool-call suppression guard missing from agent loop")

    def test_guard_clears_calls_and_finish_reason(self):
        """The guard neutralizes the response instead of executing it."""
        m = re.search(
            r'if chat_mode == "ASK" and raw_tool_calls:\n'
            r'(.*?)\n\n',
            _src,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "ASK-mode guard body not found")
        body = m.group(1)
        self.assertIn('msg["tool_calls"] = []', body)
        self.assertIn("raw_tool_calls = None", body)
        self.assertIn('finish_reason = "stop"', body)
        # The guard must log so users can diagnose suppressed calls.
        self.assertIn("ASK mode", body)

    def test_guard_executes_no_mcp_tools_in_ask_mode(self):
        """Every MCP execution site in the turn loop checks ASK first."""
        # The only path to _call_mcp_tool_sync inside the turn loop runs
        # under `if raw_tool_calls and finish_reason == "tool_calls":`,
        # which the guard neutralizes by clearing both conditions.
        start = _src.find('if raw_tool_calls and finish_reason == "tool_calls":')
        self.assertGreater(start, 0, "tool execution block missing")
        # The guard must appear BEFORE the execution condition.
        guard = _src.find('if chat_mode == "ASK" and raw_tool_calls:')
        self.assertGreater(guard, 0, "guard missing")
        self.assertLess(guard, start, "guard must run before tool execution")


class TestAskModeSystemPrompt(unittest.TestCase):
    """Ask mode appends an informational-only addendum to the prompt."""

    def _extract_inner(self) -> str:
        """Extract _run_conversation_turn_inner source."""
        marker = "\ndef _run_conversation_turn_inner("
        start = _src.find(marker)
        self.assertGreater(start, 0, "_run_conversation_turn_inner not found")
        end = _src.find("\ndef ", start + 100)
        return _src[start:end]

    def test_addendum_constant_defined(self):
        self.assertIn("_ASK_MODE_PROMPT_ADDENDUM = (", _src)

    def test_addendum_mentions_readonly(self):
        m = _ASK_ADDENDUM_ASSIGN_RE.search(_src)
        self.assertIsNotNone(m, "_ASK_MODE_PROMPT_ADDENDUM not found")
        # The parenthesized string literal: pull out the text content.
        literals = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
        text = "".join(literals)
        self.assertIn("READ-ONLY", text.upper().replace("READ-ONLY", "READ-ONLY"))
        self.assertIn("Ask mode", text)
        self.assertIn("Do NOT", text)

    def test_addendum_appended_only_in_ask_mode(self):
        inner = self._extract_inner()
        # Guarded against duplication: only appended when not already present.
        self.assertIn(
            'if chat_mode == "ASK" and _ASK_MODE_PROMPT_ADDENDUM not in '
            'history[0]["content"]:',
            inner.replace("\n        ", " ").replace("\n    ", " "),
            "Ask-mode addendum must be marker-guarded against duplication",
        )
        self.assertIn(
            'history[0]["content"] += _ASK_MODE_PROMPT_ADDENDUM',
            inner,
            "Ask-mode addendum must be appended to the system prompt",
        )


class TestChatModeForwarding(unittest.TestCase):
    """chat_mode must reach _openai_chat_completions in the turn loop."""

    def test_main_call_forwards_chat_mode(self):
        # The main request in the turn loop passes chat_mode.
        m = re.search(
            r'response = _openai_chat_completions\(\s*llm_url, history_to_send, '
            r'openai_tools, api_key, model, max_tokens, '
            r'thinking_budget_tokens=thinking_budget, chat_mode=chat_mode\)',
            _src,
        )
        self.assertIsNotNone(
            m, "main _openai_chat_completions call must forward chat_mode")

    def test_all_call_sites_forward_chat_mode(self):
        """Every call site in the turn loop forwards chat_mode."""
        sites = []
        for m in re.finditer(r'_openai_chat_completions\(', _src):
            s = m.start()
            if _src[max(0, s - 4):s] == "def ":
                continue  # the definition itself
            sites.append(s)
        calls = sites
        self.assertGreaterEqual(len(calls), 4, "expected >= 4 call sites")
        for s in calls:
            # Grab the call's argument span up to the matching close paren.
            window = _src[s:s+400]
            self.assertIn(
                "chat_mode=chat_mode", window,
                "call site does not forward chat_mode: {:s}".format(window[:120]),
            )


class TestAskModeNoToolListing(unittest.TestCase):
    """Ask mode must not offer any tools to the LLM (pre-existing, issue #66)."""

    def test_ask_mode_empty_tools(self):
        self.assertRegex(
            _src,
            r'if chat_mode == "ASK":\n\s+openai_tools = \[\]',
        )


if __name__ == "__main__":
    unittest.main()
