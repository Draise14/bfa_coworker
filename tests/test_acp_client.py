# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the ACP client (acp_client.py, Tier 4h "Agent 2").

The client talks to ``tests/fake_acp_agent.py``, which strictly replays the
real OpenCode sessions recorded in ``tests/fixtures/acp``:

1. tool call: our MCP tool (``bfa-coworker_search_api_docs``) arrives as two
   tool rows (first failed, then completed), the reply streams, usage is
   reported;
2. permission: the request reaches the callback, which answers later, and
   the turn then completes;
3. plan: OpenCode's ``todowrite`` becomes a plan event (todo/doing/done);
4. cancel: ``session/cancel`` ends the turn with ``stopReason: cancelled``;
5. a crash mid-turn fails the pending prompt with the agent's stderr;
6. ``fs/*`` requests are refused; an agent that can't start raises.

Run with::

    python -m unittest tests.test_acp_client -v
"""

__all__ = ()

import importlib
import json
import sys
import threading
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_FIXTURES = _REPO / "tests" / "fixtures" / "acp"
_FAKE = _REPO / "tests" / "fake_acp_agent.py"


def _load():
    name = "bfacw_acp_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".acp_client"), importlib.import_module(name + ".engine")


acp, eng = _load()


def _client(fixture, **kw):
    argv = [sys.executable, str(_FAKE), str(_FIXTURES / fixture)] + kw.pop("extra", [])
    events: list = []
    lock = threading.Lock()

    def on_update(update):
        with lock:
            events.extend(acp.update_to_events(update))
        if kw.get("on_first_update") and len(events) >= 1:
            kw.pop("on_first_update")()

    client = acp.AcpClient(argv, cwd=str(_REPO), on_update=on_update,
                           on_permission=kw.get("on_permission"))
    client.start()
    return client, events


def _session(client):
    info = client.initialize(timeout=20)
    result = client.new_session(str(_REPO), [acp.stdio_mcp_server("python", ["-m", "blmcp"])], timeout=20)
    return info, result["sessionId"]


def _fold(events):
    bus = eng.EventBus()
    for kind, data in events:
        bus.emit(kind, "t1", **data)
    record = eng.TurnRecord("t1")
    bus.drain_to(record.apply)
    return record


class TestReplayedSessions(unittest.TestCase):

    def test_mcp_tool_call(self):
        client, events = _client("opencode_mcp_tool_call.jsonl")
        try:
            info, sid = _session(client)
            self.assertEqual(info.name, "OpenCode")
            self.assertTrue(info.load_session and info.image and info.mcp_http)
            result = client.prompt(sid, [{"type": "text", "text": "look up primitive_cube_add"}], timeout=30)
        finally:
            client.close()
        self.assertEqual(result["stopReason"], "end_turn")
        self.assertGreater(result["usage"]["totalTokens"], 0)
        rec = _fold(events)
        self.assertEqual([(r.name, r.status) for r in rec.tools],
                         [("search_api_docs", "failed"), ("search_api_docs", "completed")])
        self.assertIn("query", rec.tools[0].error)          # the validation error text
        self.assertEqual(rec.tools[1].args, {"query": "primitive_cube_add"})
        self.assertIn("primitive_cube_add", rec.tools[1].result)
        self.assertIn("primitive_cube_add", rec.text)
        self.assertTrue(rec.thought)
        self.assertEqual(client.commands[0]["name"], "<redacted>")

    def test_permission_answered_later(self):
        seen = []

        def on_permission(params, respond):
            seen.append(params)
            # Answer from another thread, as the UI will after a click.
            option = next(o["optionId"] for o in params["options"] if o["kind"] == "allow_once")
            threading.Timer(0.1, respond, (option,)).start()

        client, events = _client("opencode_permission.jsonl", on_permission=on_permission)
        try:
            _, sid = _session(client)
            result = client.prompt(sid, [{"type": "text", "text": "run echo"}], timeout=30)
        finally:
            client.close()
        self.assertEqual(result["stopReason"], "end_turn")
        self.assertEqual(seen[0]["toolCall"]["rawInput"]["command"], "echo hello-from-acp")
        rec = _fold(events)
        self.assertEqual(rec.tools[0].status, "completed")
        self.assertIn("hello-from-acp", rec.tools[0].result)

    def test_plan_from_todowrite(self):
        client, events = _client("opencode_plan.jsonl")
        try:
            _, sid = _session(client)
            client.prompt(sid, [{"type": "text", "text": "plan"}], timeout=30)
        finally:
            client.close()
        rec = _fold(events)
        self.assertEqual(len(rec.plan), 3)
        self.assertEqual([s["status"] for s in rec.plan], ["done", "todo", "todo"])
        self.assertIn("ground", rec.plan[0]["text"].lower())

    def test_cancel(self):
        holder = {}

        def cancel_soon():
            threading.Timer(0.05, lambda: holder["client"].cancel(holder["sid"])).start()

        client, _ = _client("opencode_cancel.jsonl", on_first_update=cancel_soon)
        holder["client"] = client
        try:
            _, holder["sid"] = _session(client)
            result = client.prompt(holder["sid"], [{"type": "text", "text": "long essay"}], timeout=30)
        finally:
            client.close()
        self.assertEqual(result["stopReason"], "cancelled")


class TestFailures(unittest.TestCase):

    def test_crash_mid_turn_fails_the_prompt(self):
        # Exit after initialize + session/new answers + 5 updates.
        client, _ = _client("opencode_mcp_tool_call.jsonl", extra=["--exit-after", "8"])
        try:
            _, sid = _session(client)
            with self.assertRaises(acp.AcpError) as ctx:
                client.prompt(sid, [{"type": "text", "text": "x"}], timeout=30)
        finally:
            client.close()
        self.assertIn("exit code 3", str(ctx.exception))
        self.assertIn("simulated crash", str(ctx.exception))
        self.assertFalse(client.alive)
        with self.assertRaises(acp.AcpError):
            client.request("session/prompt", {}, timeout=1)

    def test_agent_that_cannot_start(self):
        client = acp.AcpClient([str(_REPO / "no_such_agent.exe")])
        with self.assertRaises(acp.AcpError):
            client.start()

    def test_fs_request_refused_and_permission_without_ui_cancelled(self):
        client = acp.AcpClient(["x"])
        sent = []
        client._write = sent.append  # no process needed for this path
        client._dispatch({"jsonrpc": "2.0", "id": 7, "method": "fs/read_text_file", "params": {}})
        client._dispatch({"jsonrpc": "2.0", "id": 8, "method": "session/request_permission",
                          "params": {"options": [{"optionId": "once", "kind": "allow_once"}]}})
        self.assertEqual(sent[0]["error"]["code"], -32601)
        self.assertEqual(sent[1]["result"], {"outcome": {"outcome": "cancelled"}})


class TestUpdateMapping(unittest.TestCase):

    def test_chunks_keep_message_segment(self):
        events = acp.update_to_events({"sessionUpdate": "agent_message_chunk", "messageId": "m7",
                                       "content": {"type": "text", "text": "Hi"}})
        self.assertEqual(events, [("text", {"delta": "Hi", "segment": "m7"})])

    def test_standard_plan_update(self):
        events = acp.update_to_events({"sessionUpdate": "plan", "entries": [
            {"content": "Ground", "status": "completed"}, {"content": "Logs", "status": "in_progress"}]})
        self.assertEqual(events[0][1]["steps"], [{"text": "Ground", "status": "done"},
                                                 {"text": "Logs", "status": "doing"}])

    def test_unknown_updates_ignored(self):
        self.assertEqual(acp.update_to_events({"sessionUpdate": "current_mode_update"}), [])
        self.assertEqual(acp.update_to_events({"sessionUpdate": "agent_message_chunk",
                                               "content": {"type": "image"}}), [])

    def test_stdio_server_shape(self):
        server = acp.stdio_mcp_server("py", ["-m", "blmcp"], {"A": "1"})
        self.assertEqual(server, {"name": "bfa-coworker", "command": "py", "args": ["-m", "blmcp"],
                                  "env": [{"name": "A", "value": "1"}]})
        json.dumps(server)


if __name__ == "__main__":
    unittest.main()
