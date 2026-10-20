# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the External Agent's own options (Tier 4, task "Agent 8"): mode,
model and effort pickers, from the options the agent reports
(``configOptions``, as recorded from Claude Code 0.94 and OpenCode 1.19).

1. Pickers: mode, model, effort in that order; others (Fast, sub-agents) left
   out; the current choice by its name.
2. While Only Coworker Tools is on, modes that skip the agent's permission
   questions (Accept edits, Auto, Bypass) aren't offered.
3. A pick goes to the agent (session/set_config_option), is remembered, and
   comes back in the next session.

Run with::

    python -m unittest tests.test_agent_options -v
"""

__all__ = ()

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_FIXTURES = _REPO / "tests" / "fixtures" / "acp"


def _load():
    name = "bfacw_options_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".engine_acp")


ea = _load()


def _recorded_options(fixture):
    for line in open(_FIXTURES / fixture, encoding="utf-8"):
        result = json.loads(line)["msg"].get("result") or {}
        if "configOptions" in result:
            return result["configOptions"]
    raise AssertionError("no configOptions in " + fixture)


CLAUDE = _recorded_options("claude_plan_tool_call.jsonl")
OPENCODE = _recorded_options("opencode_plan.jsonl")


def _state():
    return types.SimpleNamespace(conversation_history=[], streaming_text="", reasoning_text="",
                                 is_thinking=False, thinking_start_time=0.0, turn_phase="", ui_status="", error="")


class _Stub:
    def __init__(self, options):
        self.options = json.loads(json.dumps(options))
        self.sets = []
        self.alive = True

    def start(self):
        pass

    def initialize(self):
        return types.SimpleNamespace(name="stub", version="", image=False, load_session=False)

    def new_session(self, cwd, servers):
        return {"sessionId": "s1", "configOptions": json.loads(json.dumps(self.options))}

    def set_config_option(self, session_id, config_id, value):
        self.sets.append((config_id, value))
        for option in self.options:
            if option["id"] == config_id:
                option["currentValue"] = value
        return {"configOptions": json.loads(json.dumps(self.options))}

    def cancel(self, _sid):
        pass

    def close(self, timeout=3.0):
        self.alive = False


def _engine(options, **kw):
    stub = _Stub(options)
    engine = ea.AcpEngine(_state(), client_factory=lambda *a, **k: stub)
    for key, value in kw.items():
        setattr(engine, key, value)
    engine.connect(["stub"], {"command": "x", "args": [], "env": {}}, tempfile.mkdtemp(), wait=True)
    return engine, stub


class TestPickers(unittest.TestCase):

    def test_claude_code_mode_model_effort(self):
        engine, _ = _engine(CLAUDE)
        pickers = engine.pickers()
        self.assertEqual([p["id"] for p in pickers], ["mode", "model", "effort"])   # no Fast, no sub-agents
        mode = pickers[0]
        self.assertEqual([v for v, _n, _d in mode["choices"]], ["default", "plan"])  # asks before changes
        self.assertEqual(mode["current_name"], "Manual")
        self.assertGreater(len(pickers[1]["choices"]), 5)

    def test_every_mode_when_the_agent_may_use_its_own_tools(self):
        engine, _ = _engine(CLAUDE, only_coworker_tools=False)
        mode = engine.pickers()[0]
        self.assertEqual(len(mode["choices"]), 5)

    def test_opencode_model_and_mode(self):
        engine, _ = _engine(OPENCODE)
        pickers = {p["id"]: p for p in engine.pickers()}
        self.assertEqual(sorted(pickers), ["mode", "model"])
        self.assertEqual([v for v, _n, _d in pickers["mode"]["choices"]], ["build", "plan"])

    def test_a_pick_is_sent_remembered_and_comes_back(self):
        heard = []
        engine, stub = _engine(CLAUDE, on_option=lambda *a: heard.append(a))
        engine.set_option("model", "sonnet", wait=True)
        self.assertEqual(stub.sets, [("model", "sonnet")])
        self.assertEqual(heard, [("model", "sonnet")])
        self.assertEqual(next(p for p in engine.pickers() if p["id"] == "model")["current"], "sonnet")
        # Next start: the remembered pick is applied to the new session; an unsafe one isn't.
        again, stub2 = _engine(CLAUDE, preferred={"model": "sonnet", "mode": "bypassPermissions"})
        self.assertEqual(stub2.sets, [("model", "sonnet")])
        self.assertEqual(next(p for p in again.pickers() if p["id"] == "model")["current"], "sonnet")

    def test_agent_changing_an_option_itself(self):
        engine, _ = _engine(CLAUDE)
        changed = json.loads(json.dumps(CLAUDE))
        next(o for o in changed if o["id"] == "mode")["currentValue"] = "plan"
        engine._on_update({"update": {"sessionUpdate": "config_option_update", "configOptions": changed}})
        self.assertEqual(engine.pickers()[0]["current"], "plan")


if __name__ == "__main__":
    unittest.main()
