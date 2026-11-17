# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
A question card shows only in the chat that asked it (#572).

With chats side by side on External Agent, every chat has its own agent session,
and each session's MCP server carries that chat's thread id (``BFACW_THREAD_ID``),
which the bridge files the chat's questions, approvals and plan under.  The cause
found: when the agent was started again (Stop and Start, or after it stopped), the
engine kept the chat whose turn started last as its current one.  The new process's
first session -- the default chat's, with no thread id for its tools -- became that
chat's session, so its questions showed in All editors (and that tab was marked as
waiting); the other chats kept session ids the new process didn't know.  The same
mix-up happened when another .blend opened while a tab's message started.

A stand-in agent (it knows only its own sessions, and records each session's MCP
server environment), no Blender:

1. Stop and Start with three chats: each chat's next turn runs in a session of its
   own, with its own thread id; the default chat continues its saved session; no
   chat is told "session not found"; what is filed per chat stays the same.
2. Another .blend opens while a tab's message starts: the tab keeps its session.
3. Two tabs on one stored chat: the second gets a session of its own.
4. A permission request from a session whose turn isn't running asks in that
   session's chat, not in the chat started last.
5. End to end: a session's thread id goes through the MCP server's ``send_code``
   and the bridge to the question card -- shown, answered (typed or clicked) and
   marked only in its own chat, with two other chats open; also with many switches.
6. A message sent to a chat answers that chat's open question, not the question of the
   tab its area shows (Send with a chat named, Explain / Translate from an editor).
7. Remote and Local: one MCP server for every chat; each call's chat header reaches the
   tool's bridge call on the worker thread ask_user waits on, also when chats alternate.

Run with::

    python -m unittest tests.test_question_own_chat_572 -v
"""

__all__ = ()

import json
import os
import socket
import sys
import threading
import time
import unittest
import unittest.mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from test_threads import _FakeAcpClient, _MCP_ENTRY, _REPO, _have, _load, _state, _wait_for, cw, ea, eng  # noqa: E402

AcpError = sys.modules[ea.__name__].AcpError
_CWD = os.path.join(os.environ.get("TEMP", "."), "bfacw_572")
_NO_THREAD = "<none>"


def _thread_id(servers):
    """The thread id a session's MCP server gets in its environment."""
    env = {e["name"]: e["value"] for e in servers[0].get("env") or []}
    return env.get("BFACW_THREAD_ID", _NO_THREAD)


class _Agent(_FakeAcpClient):
    """One agent process: it knows only the sessions it opened or loaded, and each
    session's MCP server environment (the last ``session/load`` wins, as Claude Code)."""

    count = [0]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.env: dict[str, str] = {}
        self.loaded: list[str] = []
        self.slow_open = 0.0

    def new_session(self, cwd, servers, timeout=None):
        time.sleep(self.slow_open)
        _Agent.count[0] += 1
        sid = "s{:d}".format(_Agent.count[0])
        self.sessions.append((sid, servers))
        self.env[sid] = _thread_id(servers)
        return {"sessionId": sid}

    def load_session(self, sid, cwd, servers, timeout=None):
        time.sleep(self.slow_open)
        self.sessions.append((sid, servers))
        self.loaded.append(sid)
        self.env[sid] = _thread_id(servers)
        return {}

    def prompt(self, sid, blocks, timeout=None):
        if sid not in self.env:
            raise AcpError("Session not found: " + sid)
        self.prompts.append((sid, blocks[0]["text"]))
        return {"stopReason": "end_turn"}


class _Chats(unittest.TestCase):
    """Three chats on one External Agent: All editors (""), t2 and t3."""

    def setUp(self):
        self.states = {"": _state(), "t2": _state(), "t3": _state()}
        self.engine = ea.AcpEngine(self.states[""], client_factory=_Agent)
        self.engine.state_for = self.states.get
        self.saved: dict[str, str] = {}         # each chat's session, as the add-on files it
        self.engine.on_session = lambda sid: self.saved.__setitem__("", sid)
        self.engine.on_thread_session = lambda sid, tag: self.saved.__setitem__(tag, sid)
        self.engine.resume_for = lambda tag: self.saved.get(tag, "")
        self.engine.connect(["fake"], _MCP_ENTRY, _CWD, wait=True)
        self.assertTrue(self.engine.connected, self.engine.error)

    def _turn(self, text, thread):
        self.engine.submit(eng.Prompt(text=text, thread=thread))
        self.assertTrue(_wait_for(lambda: not self.engine.is_busy()))
        self.engine.drain()

    def _ran_as(self, client=None):
        """Each prompt's text -> the thread id its session's tools carry."""
        client = client or self.engine.client
        return {text: client.env[sid] for sid, text in client.prompts}

    def _errors(self):
        return [s.error for s in self.states.values() if s.error]


class TestStartAgainKeepsEachChatsSession(_Chats):

    def test_stop_and_start_with_three_chats(self):
        for text, tag in (("main", ""), ("two", "t2"), ("three", "t3")):
            self._turn(text, tag)
        before = dict(self.saved)
        self.assertEqual(self._ran_as(), {"main": _NO_THREAD, "two": "t2", "three": "t3"})
        self.engine.disconnect()                        # Stop ...
        self.engine.connect(["fake"], _MCP_ENTRY, _CWD, wait=True, resume=self.saved[""])     # ... Start
        self.assertEqual(self.engine.thread, "", "the first session is the default chat's")
        second = self.engine.client
        self.assertEqual(second.loaded, [before[""]], "the default chat continues its own session")
        for text, tag in (("three again", "t3"), ("main again", ""), ("two again", "t2")):
            self._turn(text, tag)
        self.assertEqual(self._ran_as(second), {"three again": "t3", "main again": _NO_THREAD,
                                                "two again": "t2"},
                         "each chat's tools say its own thread (before: t3 ran as All editors)")
        self.assertEqual(self._errors(), [], "no chat kept a session the new agent doesn't know")
        self.assertEqual(self.saved, before, "each chat continues its own conversation")
        self.assertEqual(sorted(second.loaded), sorted(before.values()))

    def test_start_after_the_agent_stopped_by_itself(self):
        self._turn("two", "t2")
        self.engine.client.alive = False
        self.engine.connected = False                   # "The agent stopped -- press Start"
        self.engine.connect(["fake"], _MCP_ENTRY, _CWD, wait=True)
        self._turn("two again", "t2")
        self.assertEqual(self._ran_as()["two again"], "t2")
        self.assertEqual(self._errors(), [])

    def test_a_tab_whose_message_came_before_start(self):
        """A message sent in t3 before the agent ran made t3 the current chat."""
        self.engine.disconnect()
        self.engine.submit(eng.Prompt(text="too early", thread="t3"))      # "not connected"
        self.assertTrue(_wait_for(lambda: not self.engine.is_busy()))
        self.engine.connect(["fake"], _MCP_ENTRY, _CWD, wait=True)
        self.states["t3"].error = ""
        self._turn("now", "t3")
        self._turn("main now", "")
        self.assertEqual(self._ran_as(), {"now": "t3", "main now": _NO_THREAD})


class TestSwitchWhileATabStarts(_Chats):

    def test_another_blend_opens_while_a_tab_sends(self):
        self._turn("main", "")
        self._turn("two", "t2")
        self.engine.client.slow_open = 0.3
        self.engine.switch_session("")          # File > Open: the default chat's fresh session opens ...
        time.sleep(0.05)
        self._turn("two again", "t2")           # ... while you send in tab t2
        self.assertTrue(_wait_for(lambda: len(self.engine.client.sessions) >= 3))
        time.sleep(0.4)
        self.engine.client.slow_open = 0.0
        self._turn("main again", "")
        self._turn("two third", "t2")
        ran = self._ran_as()
        self.assertEqual(ran["two third"], "t2", "before: t2 got the default chat's new session")
        self.assertEqual(ran["main again"], _NO_THREAD)
        prompts = {text: sid for sid, text in self.engine.client.prompts}
        self.assertEqual(prompts["main again"], self.saved[""], "the default chat is in its new session")
        self.assertEqual(prompts["two third"], prompts["two"])


class TestStartWhileAPromptForAnotherChat(_Chats):

    def test_submit_for_another_thread_while_start_opens_its_session(self):
        class _Slow(_Agent):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.slow_open = 0.4

        self.engine.disconnect()
        self.saved.clear()
        self.engine._client_factory = _Slow
        self.engine.connect(["fake"], _MCP_ENTRY, _CWD, wait=False)      # Start: the first session opens slowly
        time.sleep(0.1)
        self.engine.submit(eng.Prompt(text="too early", thread="t2"))   # helper_chats.submit, not the UI
        self.assertTrue(_wait_for(lambda: self.engine.connected and not self.engine.connecting, 5.0))
        self.engine.drain()
        self.states["t2"].error = ""
        self._turn("main", "")
        self._turn("two", "t2")
        first = self.engine.client.sessions[0][0]
        prompts = {text: sid for sid, text in self.engine.client.prompts}
        self.assertEqual(prompts["main"], first, "the default chat holds the first session")
        self.assertNotEqual(prompts["two"], first, "t2 gets its own")
        self.assertEqual(self._ran_as(), {"main": _NO_THREAD, "two": "t2"})
        self.assertEqual(self.saved[""], first)
        self.assertEqual(self.saved["t2"], prompts["two"])


class TestOneSessionOneChat(_Chats):

    def test_two_tabs_on_one_stored_chat(self):
        self._turn("main", "")
        main_sid = self.saved[""]
        self.engine.resume_for = lambda tag: main_sid      # t2's chat was saved with the same session
        self._turn("two", "t2")
        prompts = {text: sid for sid, text in self.engine.client.prompts}
        self.assertNotEqual(prompts["two"], main_sid, "t2 gets a session of its own")
        self.assertEqual(self._ran_as(), {"main": _NO_THREAD, "two": "t2"})
        self.assertNotIn(main_sid, self.engine.client.loaded)
        self.assertEqual(self.saved[""], main_sid, "the stored record stays with the chat that holds it")
        self.assertNotEqual(self.saved.get("t2"), prompts["two"], "the refused tab's fresh id isn't filed over it")
        self.assertNotIn("t2", self.saved)
        self._turn("main again", "")
        self.assertEqual(self._ran_as()["main again"], _NO_THREAD)

    def test_a_tab_continues_its_own_saved_session(self):
        self._turn("two", "t2")
        sid = self.saved["t2"]
        self.engine.forget_thread("t2")                  # e.g. the tab came back from History
        self._turn("two again", "t2")
        self.assertIn(sid, self.engine.client.loaded, "its own session continues")


class TestPermissionAsksInItsOwnChat(_Chats):

    def _ask(self, session):
        self.engine.ask_coworker_tools = True
        self.engine._on_permission({"sessionId": session,
                                    "toolCall": {"title": "mcp__bfa-coworker__execute_blender_code"},
                                    "options": [{"kind": "allow_once", "optionId": "o"}]}, lambda _c: None)

    def test_a_session_with_no_running_turn(self):
        self._turn("two", "t2")
        self._turn("three", "t3")                       # t3 is the chat started last
        self._ask(self.saved["t2"])
        self.assertIsNotNone(self.engine.approvals.current("t2"), "t2's session asks in t2")
        self.assertIsNone(self.engine.approvals.current("t3"))
        self._ask(self.saved["t3"])
        self.assertIsNotNone(self.engine.approvals.current("t3"))

    def test_an_unknown_session_still_asks_somewhere(self):
        self._turn("three", "t3")
        self._ask("s-nobody-knows")
        self.assertIsNotNone(self.engine.approvals.current("t3"), "as before: the current chat")


class TestPermissionFromAClosedTab(_Chats):

    def test_a_session_nobody_owns_falls_back_and_is_not_decided(self):
        self._turn("three", "t3")
        answers = []
        self.engine.ask_coworker_tools = True
        self.engine.auto_approve = False
        self.engine.never_ask = False
        self.engine.forget_thread("t2")
        self.engine._on_permission({"sessionId": "s-closed-tab",
                                    "toolCall": {"title": "mcp__bfa-coworker__execute_blender_code"},
                                    "options": [{"kind": "allow_once", "optionId": "o"},
                                                {"kind": "reject_once", "optionId": "r"}]}, answers.append)
        self.assertEqual(answers, [], "neither approved automatically nor dropped")
        self.assertIsNotNone(self.engine.approvals.current("t3"), "the current chat is asked")


class TestQuestionEndToEnd(_Chats):
    """A session's thread id -> its MCP server's send_code -> the bridge -> the card."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(_REPO / "mcp"))
        self.addCleanup(sys.path.remove, str(_REPO / "mcp"))
        from blmcp.tools_helpers import connection
        self.connection = connection
        self.br, self.si = _load("mcp_to_blender_server", "session_identity")
        env = unittest.mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in ("BFACW_THREAD_ID", "BFACW_SESSION_ID", "BFACW_TOKEN"):
            os.environ.pop(name, None)
        for qid in list(cw._questions):     # none left from other tests
            cw.cancel_question(qid)
        self.addCleanup(lambda: [cw.cancel_question(qid) for qid in list(cw._questions)])

    def _ask_user(self, servers, question):
        """What ask_user does in the MCP server process the agent started for *servers*:
        send_code over a socket, and the bridge's request handling (its tool-code posts)."""
        os.environ.pop("BFACW_THREAD_ID", None)
        for entry in servers[0].get("env") or []:
            os.environ[entry["name"]] = entry["value"]
        os.environ["BFACW_TOKEN"] = self.si.session_token()
        posted = []

        def tool_code(code, strict_json=False):
            posted.append(cw.post_question(question, ["Red", "Blue"], True))
            return self.br._ExecResult({"status": "ok", "result": None})
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        def serve():
            conn, _ = server.accept()
            buf = b""
            while b"\0" not in buf:
                buf += conn.recv(65536)
            with unittest.mock.patch.object(self.br, "_execute_code", tool_code):
                self.br._execute_code_from_request(buf.split(b"\0")[0])
            conn.sendall(json.dumps({"status": "ok", "result": None}).encode() + b"\0")
            conn.close()
        worker = threading.Thread(target=serve)
        worker.start()
        os.environ.update({"BFACW_PORT": str(server.getsockname()[1]), "BFACW_HOST": "127.0.0.1"})
        try:
            self.connection.send_code("ask", strict_json=True)
        finally:
            worker.join(5)
            server.close()
        return posted[0]

    def _session_servers(self, tag):
        """The MCP servers the agent got for *tag*'s current session."""
        sid = self.saved[tag]
        return [servers for s, servers in self.engine.client.sessions if s == sid][-1]

    def _shown(self):
        """Which chats show a card (and so a "waiting for you" tab): the chat's own query."""
        return sorted(tag for tag in ("", "t2", "t3") if cw.current_question(tag) is not None)

    def test_after_stop_and_start_the_card_is_in_its_own_tab(self):
        for text, tag in (("main", ""), ("two", "t2"), ("three", "t3")):
            self._turn(text, tag)
        self.engine.disconnect()
        self.engine.connect(["fake"], _MCP_ENTRY, _CWD, wait=True, resume=self.saved[""])
        self._turn("three again", "t3")
        qid = self._ask_user(self._session_servers("t3"), "Which colour?")
        self.assertEqual(self._shown(), ["t3"], "before: it showed in All editors")
        # A typed answer in All editors or t2 doesn't reach it; in t3 it does.
        self.assertFalse(cw.answer_question("Green", thread=""))
        self.assertFalse(cw.answer_question("Green", thread="t2"))
        shown_qid, _q = cw.current_question("t3")
        self.assertEqual(shown_qid, qid)
        self.assertTrue(cw.answer_question("Red", shown_qid))
        self.assertEqual(cw.question_state(qid)["answer"], "Red")

    def test_many_switches_each_card_stays_in_its_chat(self):
        tags = ("", "t2", "t3")
        for text, tag in (("main", ""), ("two", "t2"), ("three", "t3")):
            self._turn(text, tag)
        asked = {}
        for i in range(12):                       # switching tabs often: turns go back and forth
            tag = tags[(i * 2) % 3]
            self._turn("turn {:d}".format(i), tag)
            if i == 5:
                self.engine.disconnect()
                self.engine.connect(["fake"], _MCP_ENTRY, _CWD, wait=True, resume=self.saved[""])
        for tag in tags:
            asked[tag] = self._ask_user(self._session_servers(tag), "For " + (tag or "main"))
        for tag in tags:
            qid, q = cw.current_question(tag)
            self.assertEqual((qid, q["question"]), (asked[tag], "For " + (tag or "main")))
        # A clicked choice in t2 answers t2's question only.
        qid, q = cw.current_question("t2")
        self.assertTrue(cw.answer_question(q["choices"][1], qid))
        self.assertEqual(cw.question_state(asked["t2"])["answer"], "Blue")
        self.assertEqual(self._shown(), ["", "t3"])
        self.assertEqual(self._errors(), [])


class TestTypedAnswerGoesToItsChat(unittest.TestCase):
    """``ui_chat._answer_typed`` from its source (ui_chat needs bpy): a message sent to a chat
    (Send with its tab, Explain / Translate from an editor) answers that chat's question only."""

    def setUp(self):
        src = (_REPO / "addon" / "bfa_coworker" / "ui_chat.py").read_text(encoding="utf-8")
        start = src.index("def _answer_typed")
        body = src[start:src.index("\n\n\n", start)]
        self.area_thread = "t2"            # the tab the area in context shows
        ns = {"__name__": cw.__package__ + ".ui_chat", "__package__": cw.__package__,
              "_ui_thread": lambda context=None: self.area_thread}
        exec("from __future__ import annotations\n" + body, ns)
        self.answer_typed = ns["_answer_typed"]
        for qid in list(cw._questions):     # none left from other tests
            cw.cancel_question(qid)
        self.addCleanup(lambda: [cw.cancel_question(qid) for qid in list(cw._questions)])

    def test_the_message_answers_its_own_chats_question(self):
        q2 = cw.post_question("For t2?", ["Yes"], True, thread="t2")
        q3 = cw.post_question("For t3?", ["Yes"], True, thread="t3")
        # Sent to t3 (its thread_id / its editor) from an area that shows t2.
        self.assertTrue(self.answer_typed("blue", None, "t3"))
        self.assertEqual(cw.question_state(q3)["answer"], "blue")
        self.assertFalse(cw.question_state(q2)["answered"], "t2's question is still open")
        # With no chat named, the area's: t2.
        self.assertTrue(self.answer_typed("green"))
        self.assertEqual(cw.question_state(q2)["answer"], "green")

    def test_a_chat_with_no_question_takes_the_message_as_a_message(self):
        q2 = cw.post_question("For t2?", ["Yes"], True, thread="t2")
        self.assertFalse(self.answer_typed("explain this", None, "t3"), "before: it answered t2's question")
        self.assertFalse(cw.question_state(q2)["answered"])


@unittest.skipUnless(_have("mcp", "starlette", "httpx", "yaml", "anyio"), "needs the MCP server's dependencies")
class TestRemoteAndLocalCallsKeepTheirChat(unittest.TestCase):
    """Our loop (Remote, Local) calls the one HTTP MCP server for every chat, naming the
    chat in ``X-Coworker-Thread``; ask_user's bridge call runs on a worker thread."""

    def setUp(self):
        sys.path.insert(0, str(_REPO / "mcp"))
        self.addCleanup(sys.path.remove, str(_REPO / "mcp"))
        env = unittest.mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in ("BFACW_TOKEN", "BFACW_THREAD_ID", "BFACW_SESSION_ID"):
            os.environ.pop(name, None)

    def test_alternating_chats_on_one_server(self):
        import anyio
        import blmcp
        from blmcp.tools_helpers.bounded_output import CURRENT_THREAD
        from mcp.server.fastmcp import FastMCP
        from starlette.testclient import TestClient
        mcp = FastMCP("questions-572")

        @mcp.tool()
        async def which_chat() -> str:
            """The chat the bridge call would carry (connection.send_code reads it)."""
            return await anyio.to_thread.run_sync(lambda: CURRENT_THREAD.get() or "-")
        blmcp._configure_http(mcp, "127.0.0.1", 9193)
        order = ["t3", "main", "t2", "t3", "t3", "t2", "main", "t2", "t3"]
        seen = []
        with TestClient(mcp.streamable_http_app(), base_url="http://127.0.0.1:9193") as client:
            for i, tag in enumerate(order):
                body = json.dumps({"jsonrpc": "2.0", "id": str(i), "method": "tools/call",
                                   "params": {"name": "which_chat", "arguments": {}}})
                resp = client.post("/", content=body, headers={
                    "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                    "X-Coworker-Thread": tag})
                self.assertEqual(resp.status_code, 200, resp.text)
                text = resp.text.replace(" ", "")
                seen.append(next((t for t in set(order) if '"text":"{:s}"'.format(t) in text), text[:80]))
        self.assertEqual(seen, order)


if __name__ == "__main__":
    unittest.main()
