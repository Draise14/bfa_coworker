# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the per-turn cost ledger (Tier 3i).

The ledger attributes a turn's wall time to prompt PRE-FILL vs GENERATION vs
loop overhead so a slow local turn can be diagnosed instead of guessed at.
``AgentState`` lives in ``agent_controller.py`` (which imports bpy), so the
class is extracted from source and executed against a minimal namespace, and
``_log_turn_cost`` is exercised with a stub agent state.

Run with::

    python -m unittest tests.test_turn_cost -v
"""

__all__ = ()

import dataclasses
import os
import time
import types
from typing import Any
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AC_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "agent_controller.py")


def _load_source() -> str:
    with open(_AC_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_agent_state():
    """Exec the ``AgentState`` dataclass from source (no bpy needed)."""
    src = _load_source()
    start = src.find("@dataclass\nclass AgentState:")
    if start < 0:
        raise ImportError("AgentState not found in source")
    # The class body ends at the first module-level (un-indented) statement
    # after it -- ``_agent_state = AgentState()``.
    end = src.find("\n_agent_state = AgentState()", start + 10)
    if end < 0:
        end = len(src)
    mod = types.ModuleType("_ac_agentstate")
    mod.__dict__["dataclass"] = dataclasses.dataclass
    mod.__dict__["field"] = dataclasses.field
    mod.__dict__["Any"] = Any
    mod.__dict__["time"] = time
    exec(compile(src[start:end], _AC_PATH, "exec"), mod.__dict__)  # noqa: S102
    return mod.AgentState


_AgentState = _extract_agent_state()


def _extract_log_turn_cost(stub_action_state):
    """Exec ``_log_turn_cost`` with a stub ``_agent_state`` + ``time``."""
    src = _load_source()
    start = src.find("\ndef _log_turn_cost(")
    if start < 0:
        raise ImportError("_log_turn_cost not found in source")
    start += 1
    end = src.find("\ndef ", start + 10)
    if end < 0:
        end = len(src)
    mod = types.ModuleType("_ac_cost_log")
    mod.__dict__["_agent_state"] = stub_action_state
    mod.__dict__["time"] = time
    exec(compile(src[start:end], _AC_PATH, "exec"), mod.__dict__)  # noqa: S102
    return mod._log_turn_cost


class TestTurnCostLedger(unittest.TestCase):
    def setUp(self):
        self.state = _AgentState()

    def test_reset_starts_fresh_with_start_time(self):
        self.state.bump_turn_cost("tools", 5)
        self.state.reset_turn_cost()
        self.assertEqual(self.state.last_turn_cost["requests"], 0)
        self.assertIn("start", self.state.last_turn_cost)

    def test_record_timings_accumulates(self):
        self.state.reset_turn_cost()
        self.state.record_request_timings({
            "prompt_n": 100, "predicted_n": 50,
            "prompt_ms": 200.0, "predicted_ms": 500.0,
        })
        self.state.record_request_timings({
            "prompt_n": 40, "predicted_n": 10,
            "prompt_ms": 100.0, "predicted_ms": 200.0,
        })
        cost = self.state.last_turn_cost
        self.assertEqual(cost["requests"], 2)
        self.assertEqual(cost["prompt_n"], 140)
        self.assertEqual(cost["predicted_n"], 60)
        self.assertAlmostEqual(cost["prompt_ms"], 300.0)
        self.assertAlmostEqual(cost["predicted_ms"], 700.0)

    def test_record_timings_ignores_empty_and_bad(self):
        self.state.reset_turn_cost()
        self.state.record_request_timings(None)
        self.state.record_request_timings({})
        # Empty/None never counts as a request.
        self.assertEqual(self.state.last_turn_cost["requests"], 0)
        # A present but negative value is not summed (still one request).
        self.state.record_request_timings({"prompt_n": -5})
        self.assertEqual(self.state.last_turn_cost["requests"], 1)
        self.assertEqual(self.state.last_turn_cost.get("prompt_n", 0), 0)

    def test_bump_turn_cost_counts(self):
        self.state.reset_turn_cost()
        self.state.bump_turn_cost("nudges")
        self.state.bump_turn_cost("nudges")
        self.state.bump_turn_cost("tools", 3)
        self.assertEqual(self.state.last_turn_cost["nudges"], 2)
        self.assertEqual(self.state.last_turn_cost["tools"], 3)

    def test_reset_usage_clears_cost(self):
        self.state.bump_turn_cost("tools", 2)
        self.state.reset_usage()
        self.assertEqual(self.state.last_turn_cost, {})


class _StubState:
    """Minimal stand-in exposing only what ``_log_turn_cost`` reads."""

    def __init__(self, cost):
        self.last_turn_cost = cost


class TestLogTurnCost(unittest.TestCase):
    def test_prints_summary_and_records_wall(self):
        state = _StubState({
            "requests": 3, "start": time.time() - 12.0,
            "prompt_n": 1000, "prompt_ms": 4000.0,
            "predicted_n": 8000, "predicted_ms": 8000.0,
            "tools": 5, "nudges": 1, "continues": 2, "malformed": 0,
        })
        log = _extract_log_turn_cost(state)
        lines = []
        import builtins
        _real_print = builtins.print
        builtins.print = lambda *a, **k: lines.append(" ".join(str(x) for x in a))
        try:
            log()
        finally:
            builtins.print = _real_print
        self.assertTrue(any("turn cost" in ln for ln in lines), lines)
        self.assertIn("wall", state.last_turn_cost)
        self.assertGreater(state.last_turn_cost["wall"], 0)

    def test_no_requests_prints_nothing(self):
        log = _extract_log_turn_cost(_StubState({"requests": 0}))
        import builtins
        lines = []
        _real_print = builtins.print
        builtins.print = lambda *a, **k: lines.append(a)
        try:
            log()
        finally:
            builtins.print = _real_print
        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
