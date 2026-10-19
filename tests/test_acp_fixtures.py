# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Checks the recorded ACP fixtures in ``tests/fixtures/acp`` (Tier 4h.0).

The fixtures are real sessions recorded with ``_misc/acp_record.py``.  These
tests keep them usable as replay data for the ACP client (well-formed JSON-RPC,
the flows each file is named for) and keep them free of user data (the
agent's command list is redacted, local paths are placeholders).
"""

__all__ = ()

import glob
import json
import os
import re
import unittest

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "acp")

# Absolute local paths that must never appear in a committed fixture.
_PERSONAL = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]|/home/|AppData|[\\/]\.claude[\\/]", re.I)


def _load(name: str) -> list[dict]:
    with open(os.path.join(_DIR, name), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _updates(records: list[dict], kind: str) -> list[dict]:
    out = []
    for rec in records:
        params = rec["msg"].get("params")
        if isinstance(params, dict) and params.get("update", {}).get("sessionUpdate") == kind:
            out.append(params["update"])
    return out


def _requests(records: list[dict], method: str) -> list[dict]:
    return [r["msg"] for r in records if r["msg"].get("method") == method and "id" in r["msg"]]


def _result_of(records: list[dict], method: str) -> dict:
    """Result of the client's (first) *method* request."""
    ids = [r["msg"]["id"] for r in records
           if r["dir"] == "client->agent" and r["msg"].get("method") == method]
    for rec in records:
        if rec["dir"] == "agent->client" and rec["msg"].get("id") in ids and "result" in rec["msg"]:
            return rec["msg"]["result"]
    raise AssertionError("no result for " + method)


class TestFixturesWellFormed(unittest.TestCase):

    def test_every_fixture_is_jsonrpc(self):
        files = glob.glob(os.path.join(_DIR, "*.jsonl"))
        self.assertGreaterEqual(len(files), 4)
        for path in files:
            for rec in _load(os.path.basename(path)):
                self.assertIn(rec["dir"], ("client->agent", "agent->client"), path)
                self.assertEqual(rec["msg"].get("jsonrpc"), "2.0", path)
                self.assertIsInstance(rec["t"], (int, float))

    def test_fixtures_are_redacted(self):
        for path in glob.glob(os.path.join(_DIR, "*.jsonl")):
            records = _load(os.path.basename(path))
            text = "\n".join(json.dumps(r["msg"]) for r in records)
            self.assertIsNone(_PERSONAL.search(text), "local path left in " + path)
            for upd in _updates(records, "available_commands_update"):
                names = [c.get("name") for c in upd.get("availableCommands", [])]
                self.assertEqual(names, ["<redacted>"], "command list not redacted in " + path)


class TestRecordedFlows(unittest.TestCase):
    """What each recorded flow shows; the ACP client is built against these."""

    def test_initialize_capabilities(self):
        init = _result_of(_load("opencode_mcp_tool_call.jsonl"), "initialize")
        caps = init["agentCapabilities"]
        self.assertTrue(caps["loadSession"])
        self.assertTrue(caps["promptCapabilities"]["image"])
        self.assertIn("http", caps["mcpCapabilities"])

    def test_mcp_tool_call_through_stdio_server(self):
        records = _load("opencode_mcp_tool_call.jsonl")
        calls = _updates(records, "tool_call")
        self.assertTrue(calls)
        # Our tools arrive prefixed with the MCP server name.
        self.assertTrue(all(c["title"].startswith("bfa-coworker_") for c in calls))
        done = [u for u in _updates(records, "tool_call_update") if u.get("status") == "completed"]
        self.assertTrue(done)
        usage = _result_of(records, "session/prompt")["usage"]
        self.assertGreater(usage["totalTokens"], 0)

    def test_permission_request_and_answer(self):
        records = _load("opencode_permission.jsonl")
        reqs = _requests(records, "session/request_permission")
        self.assertEqual(len(reqs), 1)
        kinds = {o["kind"] for o in reqs[0]["params"]["options"]}
        self.assertTrue({"allow_once", "allow_always", "reject_once"} <= kinds)
        answers = [r["msg"] for r in records
                   if r["dir"] == "client->agent" and r["msg"].get("id") == reqs[0]["id"]]
        self.assertEqual(answers[0]["result"]["outcome"]["outcome"], "selected")

    def test_plan_arrives_as_todowrite_tool(self):
        records = _load("opencode_plan.jsonl")
        self.assertEqual(_updates(records, "plan"), [])
        todo_inputs = [u.get("rawInput", {}).get("todos") for u in _updates(records, "tool_call_update")
                       if u.get("rawInput", {}).get("todos")]
        self.assertTrue(todo_inputs)
        self.assertTrue(all({"content", "status"} <= set(t) for t in todo_inputs[0]))

    def test_cancel_stop_reason(self):
        result = _result_of(_load("opencode_cancel.jsonl"), "session/prompt")
        self.assertEqual(result["stopReason"], "cancelled")


if __name__ == "__main__":
    unittest.main()
