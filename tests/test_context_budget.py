# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Tier 3 Phase 1 prompt-budget helpers in agent_controller.py.

1. ``_estimate_tools_tokens`` must count the tool-schema JSON (it is
   re-sent on every request and is a large share of a 16K local context).

2. ``_compute_prompt_budget`` must reserve max_tokens + template overhead +
   a safety margin, clamp an oversized max_tokens, and never return a
   value that would trim the prompt to nothing.

3. ``_prompt_preflight`` must re-trim an overflowing history (keeping the
   system prompt and the last user turn) and return a friendly, actionable
   error — not let the server answer with a raw 400 — only when even the
   pinned turn cannot fit.

4. ``_collapse_poll_failed_error`` must collapse a ``poll() failed,
   context is incorrect`` traceback to a one-line corrective hint while
   leaving every other error untouched.

Loaded from source (the module imports bpy, which is not available in the
unit-test environment).

Run with::

    python -m unittest tests.test_context_budget -v
"""

__all__ = ()

import json
import os
import re
import types
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AC_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "agent_controller.py")

_CHARS_PER_TOKEN = 3.5
_TEMPLATE_OVERHEAD_TOKENS = 512


def _load_source():
    with open(_AC_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_func(source: str, name: str, extra: dict | None = None):
    marker = "\ndef {:s}(".format(name)
    start = source.find(marker)
    if start < 0:
        raise ImportError("{:s} not found in source".format(name))
    start += 1
    end = len(source)
    for m in ["\ndef _", "\ndef ", "\nclass ", "\n# ---"]:
        idx = source.find(m, start + 100)
        if 0 <= idx < end:
            end = idx
    mod = types.ModuleType("_ac_extract")
    mod.__dict__["json"] = json
    mod.__dict__["re"] = re
    mod.__dict__["Any"] = object
    mod.__dict__["_CHARS_PER_TOKEN"] = _CHARS_PER_TOKEN
    mod.__dict__["_TEMPLATE_OVERHEAD_TOKENS"] = _TEMPLATE_OVERHEAD_TOKENS
    if extra:
        mod.__dict__.update(extra)
    exec(compile(source[start:end], _AC_PATH, "exec"), mod.__dict__)
    return mod.__dict__[name]


_src = _load_source()

_repair_tool_call_pairs = _extract_func(_src, "_repair_tool_call_pairs")
_message_text_length = _extract_func(_src, "_message_text_length")
_estimate_messages_tokens = _extract_func(
    _src, "_estimate_messages_tokens",
    {"_message_text_length": _message_text_length, "_CHARS_PER_TOKEN": _CHARS_PER_TOKEN},
)
_estimate_tools_tokens = _extract_func(_src, "_estimate_tools_tokens")
_fit_history_to_budget = _extract_func(
    _src, "_fit_history_to_budget",
    {
        "_estimate_messages_tokens": _estimate_messages_tokens,
        "_CHARS_PER_TOKEN": _CHARS_PER_TOKEN,
    },
)
_compute_prompt_budget = _extract_func(
    _src, "_compute_prompt_budget",
    {"_TEMPLATE_OVERHEAD_TOKENS": _TEMPLATE_OVERHEAD_TOKENS},
)
_prompt_preflight = _extract_func(
    _src, "_prompt_preflight",
    {
        "_estimate_tools_tokens": _estimate_tools_tokens,
        "_estimate_messages_tokens": _estimate_messages_tokens,
        "_fit_history_to_budget": _fit_history_to_budget,
        "_repair_tool_call_pairs": _repair_tool_call_pairs,
    },
)
_collapse_poll_failed_error = _extract_func(
    _src, "_collapse_poll_failed_error",
    {"_POLL_FAILED_MARKER": "poll() failed, context is incorrect"},
)


def _mk_history(n_turns: int, text_len: int = 400) -> list:
    """System prompt + n user/assistant turns of ~text_len chars each."""
    msgs = [{"role": "system", "content": "You are a Blender agent. " * 10}]
    for i in range(n_turns):
        msgs.append({"role": "user", "content": "turn {:d} {:s}".format(i, "x" * text_len)})
        msgs.append({"role": "assistant", "content": "ok {:s}".format("y" * text_len)})
    return msgs


class TestEstimateToolsTokens(unittest.TestCase):

    def test_zero_for_empty(self):
        self.assertEqual(_estimate_tools_tokens(None), 0)
        self.assertEqual(_estimate_tools_tokens([]), 0)

    def test_counts_schema_json(self):
        tools = [{"type": "function", "function": {
            "name": "execute_blender_code",
            "description": "Run Blender Python code. " * 20,
            "parameters": {"type": "object", "properties": {
                "code": {"type": "string", "description": "The code"}}},
        }}]
        est = _estimate_tools_tokens(tools)
        blob = json.dumps(tools)
        expected_min = int(len(blob) / _CHARS_PER_TOKEN)
        self.assertGreaterEqual(est, expected_min)

    def test_bigger_schema_costs_more(self):
        small = [{"type": "function", "function": {"name": "a", "description": "hi"}}]
        big = [{"type": "function", "function": {"name": "b", "description": "hi" * 100}}]
        self.assertLess(_estimate_tools_tokens(small), _estimate_tools_tokens(big))


class TestComputePromptBudget(unittest.TestCase):

    def test_reserves_max_tokens_and_overhead(self):
        ctx, max_tokens = 16384, 4096
        budget = _compute_prompt_budget(ctx, max_tokens)
        self.assertLessEqual(budget, ctx - max_tokens - _TEMPLATE_OVERHEAD_TOKENS)
        self.assertGreater(budget, ctx // 2)

    def test_clamps_oversized_max_tokens(self):
        budget = _compute_prompt_budget(8192, 8000)
        # max_tokens was clamped to half the window; budget stays usable.
        self.assertGreater(budget, 0)
        self.assertLessEqual(budget, 8192 - 8192 // 2)

    def test_tiny_context_keeps_floor(self):
        budget = _compute_prompt_budget(2048, 1500)
        self.assertGreaterEqual(budget, max(2048 // 2, 1024))

    def test_zero_ctx_returns_zero(self):
        self.assertEqual(_compute_prompt_budget(0, 1024), 0)
        self.assertEqual(_compute_prompt_budget(-5, 1024), 0)

    def test_smaller_ctx_gives_smaller_budget(self):
        self.assertLess(
            _compute_prompt_budget(8192, 2048),
            _compute_prompt_budget(32768, 2048),
        )


class TestPromptPreflight(unittest.TestCase):

    def test_passthrough_when_fits(self):
        history = _mk_history(3)
        out, err = _prompt_preflight(history, [], 100000)
        self.assertIs(out, history)
        self.assertIsNone(err)

    def test_zero_budget_passthrough(self):
        history = _mk_history(30)
        out, err = _prompt_preflight(history, [], 0)
        self.assertIs(out, history)
        self.assertIsNone(err)

    def test_retrims_to_budget(self):
        history = _mk_history(30)
        budget = _estimate_messages_tokens(history[:11])
        out, err = _prompt_preflight(history, [], budget)
        self.assertIsNone(err)
        self.assertLessEqual(_estimate_messages_tokens(out), budget)
        # System prompt survives and the last user turn is still present
        # (the pinned tail may include the assistant reply after it).
        self.assertEqual(out[0]["role"], "system")
        self.assertTrue(any(m["role"] == "user" for m in out[-2:]))

    def test_tool_schema_counted_against_budget(self):
        history = _mk_history(6)
        big_tools = [{"type": "function", "function": {
            "name": "t", "description": "z" * 20000}}]
        tight = _estimate_messages_tokens(history) + 100
        out, err = _prompt_preflight(history, big_tools, tight)
        # With a large tool schema counted, the history must be re-trimmed.
        self.assertLess(
            _estimate_messages_tokens(out), _estimate_messages_tokens(history))

    def test_friendly_error_when_even_pinned_turn_overflows(self):
        huge = "z" * 200000
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": huge},
        ]
        out, err = _prompt_preflight(history, [], 1024)
        self.assertIsNotNone(err)
        self.assertIn("context window", err)
        self.assertIn("compact", err.lower())

    def test_no_error_after_trim_when_pinned_fits(self):
        history = _mk_history(30)
        budget = _estimate_messages_tokens(history) - 10
        out, err = _prompt_preflight(history, [], budget)
        self.assertIsNone(err)


class TestCollapsePollFailedError(unittest.TestCase):

    def test_collapses_traceback(self):
        tb = (
            'TypeError: calling "bpy.ops.object.shade_smooth()" error: '
            'Operator bpy.ops.object.shade_smooth.poll() failed, '
            'context is incorrect\n'
            '  File "<string>", line 1\n'
            'Traceback (most recent call last):\n'
            '  ...long traceback...\n'
        )
        out = _collapse_poll_failed_error(
            json.dumps({"status": "error", "message": tb}))
        self.assertIn("shade_smooth", out)
        self.assertIn("temp_override", out)
        self.assertNotIn("Traceback", out)
        self.assertLess(len(out), 400)

    def test_leaves_other_errors_untouched(self):
        other = json.dumps({"status": "error", "message": "SyntaxError: bad code"})
        self.assertEqual(_collapse_poll_failed_error(other), other)

    def test_leaves_success_untouched(self):
        ok = json.dumps({"status": "ok", "result": "done"})
        self.assertEqual(_collapse_poll_failed_error(ok), ok)

    def test_unnamed_operator(self):
        tb = "poll() failed, context is incorrect"
        out = _collapse_poll_failed_error(tb)
        self.assertIn("temp_override", out)


if __name__ == "__main__":
    unittest.main()
