# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the thread model (threads.py, Threads 1, #357) and the shared
records that now carry a thread: approvals, ask_user questions, the plan
written over the bridge, the bridge's repeat count, the External Agent's
sessions and the MCP server's thread header.

1. The registry: one default thread, editor threads (two 3D Views share one),
   closing, the one-turn-at-a-time gate and who goes next (oldest message).
2. The worker's current thread, tags and undo labels.
3. Two engines on two threads (fake turns through the real LoopEngine): their
   own history, Queue and Stop; the second waits for the first, then starts.
4. Approvals and questions: each thread sees, answers and Stops its own;
   "Allow this turn" holds for one thread; with no thread given, as before.
5. The External Agent: one session per thread on one process, each with
   ``BFACW_THREAD_ID`` for its tools; a turn's events land in its own chat; a
   late update from another thread's session is dropped.
6. The MCP server: the ``X-Coworker-Thread`` header reaches the tool, and
   ``send_code`` stamps ``thread`` on the bridge payload.

Run with::

    python -m unittest tests.test_threads -v
"""

__all__ = ()

import importlib
import importlib.util
import json
import logging
import os
import socket
import sys
import threading
import time
import types
import unittest
import unittest.mock
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"


def _load(*names):
    pkg_name = "bfacw_threads_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[pkg_name] = pkg
    return [importlib.import_module(pkg_name + "." + n) for n in names]


th, ap, cw, eng, el, ea, lg = _load("threads", "approvals", "coworker_api", "engine", "engine_loop",
                                    "engine_acp", "loop_guard")


def _message_queue_class():
    """The controller's MessageQueue, from its source (agent_controller needs bpy)."""
    src = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
    start = src.index("@dataclass\nclass MessageQueue:")
    end = src.index("\n\n\n", src.index("class MessageQueue:"))
    ns: dict = {}
    exec("from __future__ import annotations\nimport threading, time\n"
         "from dataclasses import dataclass, field\nfrom typing import Any\n" + src[start:end], ns)
    return ns["MessageQueue"]


MessageQueue = _message_queue_class()


def _state():
    return types.SimpleNamespace(conversation_history=[], turn_active=False, error="", ui_status="",
                                 streaming_text="", reasoning_text="", is_thinking=False,
                                 thinking_start_time=0.0, turn_phase="", turn_usage={})


def _registry():
    return th.ThreadRegistry(make_state=_state, make_queue=MessageQueue, make_store=dict,
                             default_objects=True)


class TestRegistry(unittest.TestCase):

    def test_one_default_thread(self):
        reg = _registry()
        self.assertTrue(reg.default.is_default)
        self.assertEqual(reg.live(), [reg.default])
        self.assertIs(reg.get(""), reg.default)
        self.assertIs(reg.get(None), reg.default)
        self.assertIs(reg.for_editor(""), reg.default)

    def test_threads_have_their_own_objects(self):
        reg = _registry()
        a, b = reg.create(editor="VIEW_3D"), reg.create(editor="NODE_EDITOR")
        self.assertNotEqual(a.id, b.id)
        for name in ("state", "queue", "store", "stop", "domains", "text_blocks"):
            self.assertIsNot(getattr(a, name), getattr(b, name), name)
        self.assertEqual(reg.live(), [reg.default, a, b])
        self.assertIs(reg.get(b.id), b)

    def test_controller_objects_stay_with_the_default(self):
        reg = th.ThreadRegistry(make_state=_state, make_queue=MessageQueue)
        self.assertIsNone(reg.default.state)      # the controller's module-level state is its
        self.assertIsNotNone(reg.create().state)

    def test_editor_kind_shares_a_thread(self):
        reg = _registry()
        a = reg.for_editor("VIEW_3D")
        self.assertIs(reg.for_editor("VIEW_3D"), a, "two 3D Views share one")
        self.assertIsNot(reg.for_editor("NODE_EDITOR"), a)
        self.assertIsNone(reg.for_editor("SEQUENCE_EDITOR", create=False))

    def test_remove(self):
        reg = _registry()
        a = reg.create()
        self.assertFalse(reg.remove(th.DEFAULT_ID), "never the default")
        self.assertTrue(reg.try_begin(a))
        self.assertFalse(reg.remove(a.id), "never while its turn runs")
        reg.end(a)
        self.assertTrue(reg.remove(a.id))
        self.assertIsNone(reg.get(a.id))

    def test_one_turn_at_a_time(self):
        reg = _registry()
        a, b = reg.create(), reg.create()
        self.assertTrue(reg.try_begin(a))
        self.assertIs(reg.running(), a)
        self.assertTrue(reg.blocked(b))
        self.assertFalse(reg.blocked(a))
        self.assertFalse(reg.try_begin(b))
        self.assertTrue(reg.try_begin(a), "its own stopped turn may start again")
        reg.end(b)
        self.assertIs(reg.running(), a, "only the owner frees the gate")
        reg.end(a)
        self.assertIsNone(reg.running())
        self.assertTrue(reg.try_begin(b))

    def test_oldest_waiting_message_goes_next(self):
        reg = _registry()
        a, b = reg.create(), reg.create()
        self.assertIsNone(reg.next_waiting())
        b.queue.enqueue("b first")
        time.sleep(0.01)
        a.queue.enqueue("a later")
        self.assertIs(reg.next_waiting(), b)
        b.queue.dequeue()
        self.assertIs(reg.next_waiting(), a)


class TestCurrentAndNames(unittest.TestCase):

    def test_use_is_per_worker(self):
        reg = _registry()
        a = reg.create()
        seen = []
        with th.use(a):
            self.assertIs(th.current(), a)
            worker = threading.Thread(target=lambda: seen.append(th.current()))
            worker.start()
            worker.join()
        self.assertEqual(seen, [None], "another worker has its own")
        self.assertIsNone(th.current())

    def test_tag_and_label(self):
        reg = _registry()
        node = reg.create(editor="NODE_EDITOR")
        self.assertEqual(th.tag(reg.default), "")
        self.assertEqual(th.tag("main"), "")
        self.assertEqual(th.tag(None), "")
        self.assertEqual(th.tag(node), node.id)
        self.assertEqual(th.label(reg.default), "Coworker")
        self.assertEqual(th.label(node), "Coworker (Node)")
        self.assertEqual(th.label(reg.create(editor="VIEW_3D")), "Coworker (3D)")


class TestEditorThreads(unittest.TestCase):
    """Threads 2 (#358): which thread an editor's chat shows."""

    def test_shared_thread_unless_editor_focus(self):
        reg = _registry()
        self.assertIs(th.pick(reg, "VIEW_3D"), reg.default, "focus off: All editors, as before")
        self.assertIs(th.pick(reg, "", focus=True), reg.default, "no editor kind: All editors")
        self.assertEqual(reg.live(), [reg.default], "nothing made for a shared chat")

    def test_each_editor_kind_its_own_thread(self):
        reg = _registry()
        made = []
        create = lambda editor: made.append(editor) or reg.create(editor=editor)   # noqa: E731
        view3d = th.pick(reg, "VIEW_3D", focus=True, create=create)
        node = th.pick(reg, "NODE_EDITOR", focus=True, create=create)
        self.assertIsNot(view3d, node)
        self.assertIs(th.pick(reg, "VIEW_3D", focus=True, create=create), view3d, "two 3D Views share one")
        self.assertEqual(made, ["VIEW_3D", "NODE_EDITOR"], "made once, with this .blend's last chat")
        self.assertEqual((view3d.editor, node.editor), ("VIEW_3D", "NODE_EDITOR"))

    def test_pinned_area_and_its_fallback(self):
        reg = _registry()
        kind = th.pick(reg, "VIEW_3D", focus=True)
        pinned = reg.create(editor="VIEW_3D")
        self.assertIs(th.pick(reg, "VIEW_3D", pinned=pinned.id, focus=True), pinned)
        self.assertIs(th.pick(reg, "VIEW_3D", pinned=pinned.id), pinned, "a pin wins over the tab")
        reg.remove(pinned.id)
        self.assertIs(th.pick(reg, "VIEW_3D", pinned=pinned.id, focus=True), kind, "a lost pin: the editor kind")

    def test_short_names(self):
        self.assertEqual(th.short_name("NODE_EDITOR"), "Node")
        self.assertEqual(th.short_name("SEQUENCE_EDITOR"), "Sequencer")
        self.assertEqual(th.short_name(""), "")


class _FakeController:
    """What agent_controller.ThreadController gives a LoopEngine, over a ThreadRegistry:
    fake turns that write the thread's history and honour its Stop."""

    def __init__(self, reg, thread, release):
        self.reg, self.thread, self.release = reg, thread, release

    @property
    def _agent_state(self):
        return self.thread.state

    @property
    def _stop_event(self):
        return self.thread.stop

    def request_stop(self):
        self.thread.stop.set()

    def enqueue_message(self, *args, **kwargs):
        return self.thread.queue.enqueue(*args, **kwargs)

    def dequeue_message(self):
        return self.thread.queue.dequeue()

    def turn_blocked(self):
        return self.reg.blocked(self.thread)

    def next_turn(self, engine=None):
        waiting = self.reg.next_waiting()
        if waiting is None:
            return None
        return engine if waiting is self.thread else waiting.engine

    def run_conversation_turn(self, user_message="", **_kw):
        state = self.thread.state
        if not self.reg.try_begin(self.thread):
            return state.conversation_history
        state.turn_active = True
        self.thread.stop.clear()
        try:
            state.conversation_history.append({"role": "user", "content": user_message})
            # Work until released or stopped.
            while not self.release[self.thread.id].wait(0.01):
                if self.thread.stop.is_set():
                    state.conversation_history.append({"role": "assistant", "content": "stopped"})
                    return state.conversation_history
            state.conversation_history.append({"role": "assistant", "content": "done: " + user_message})
            return state.conversation_history
        finally:
            state.turn_active = False
            self.reg.end(self.thread)


def _wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestTwoThreadsOnTheLoopEngine(unittest.TestCase):

    def setUp(self):
        self.reg = _registry()
        self.a, self.b = self.reg.create(editor="VIEW_3D"), self.reg.create(editor="NODE_EDITOR")
        self.release = {self.a.id: threading.Event(), self.b.id: threading.Event()}
        for t in (self.a, self.b):
            t.engine = el.LoopEngine(_FakeController(self.reg, t, self.release))
        self.addCleanup(lambda: [e.set() for e in self.release.values()])

    def _texts(self, thread):
        return [(m["role"], m["content"]) for m in thread.state.conversation_history]

    def test_separate_history_and_queue_one_turn_at_a_time(self):
        cfg = el.LoopConfig()
        started, pos = self.a.engine.submit(eng.Prompt(text="build the scene"), cfg)
        self.assertTrue(started)
        self.assertTrue(_wait_for(lambda: self.a.state.turn_active))
        none, pos = self.b.engine.submit(eng.Prompt(text="tidy the nodes"), cfg)
        self.assertIsNone(none, "B waits while A's turn runs")
        self.assertEqual(pos, 1)
        self.assertEqual(self.b.queue.pending_count, 1)
        self.assertEqual(self.a.queue.pending_count, 0, "B's message is in B's queue")
        self.release[self.a.id].set()
        self.assertTrue(_wait_for(lambda: self.b.state.turn_active), "B starts when A ends")
        self.release[self.b.id].set()
        self.assertTrue(_wait_for(lambda: len(self.b.state.conversation_history) == 2))
        self.assertEqual(self._texts(self.a), [("user", "build the scene"), ("assistant", "done: build the scene")])
        self.assertEqual(self._texts(self.b), [("user", "tidy the nodes"), ("assistant", "done: tidy the nodes")])

    def test_stop_in_one_leaves_the_other(self):
        cfg = el.LoopConfig()
        self.a.engine.submit(eng.Prompt(text="long build"), cfg)
        self.assertTrue(_wait_for(lambda: self.a.state.turn_active))
        self.b.engine.submit(eng.Prompt(text="waiting job"), cfg)
        self.b.engine.cancel()                      # Stop in the waiting thread's editor
        self.assertTrue(self.b.stop.is_set())
        self.assertFalse(self.a.stop.is_set(), "A keeps working")
        time.sleep(0.05)
        self.assertTrue(self.a.state.turn_active)
        self.a.engine.cancel()                      # now Stop A
        self.assertTrue(_wait_for(lambda: not self.a.state.turn_active))
        self.assertEqual(self.a.state.conversation_history[-1]["content"], "stopped")
        # B's message still went next, and its turn started with its Stop cleared.
        self.assertTrue(_wait_for(lambda: self.b.state.turn_active))
        self.assertFalse(self.b.stop.is_set())
        self.release[self.b.id].set()
        self.assertTrue(_wait_for(lambda: len(self.b.state.conversation_history) == 2))
        self.assertEqual(self.b.state.conversation_history[-1]["content"], "done: waiting job")

    def test_oldest_waiting_thread_goes_first(self):
        cfg = el.LoopConfig()
        c = self.reg.create(editor="SEQUENCE_EDITOR")
        self.release[c.id] = threading.Event()
        c.engine = el.LoopEngine(_FakeController(self.reg, c, self.release))
        self.a.engine.submit(eng.Prompt(text="a1"), cfg)
        self.assertTrue(_wait_for(lambda: self.a.state.turn_active))
        c.engine.submit(eng.Prompt(text="c1"), cfg)          # waits first
        time.sleep(0.01)
        self.b.engine.submit(eng.Prompt(text="b1"), cfg)
        time.sleep(0.01)
        self.a.engine.submit(eng.Prompt(text="a2"), cfg)     # A's own, but queued last
        self.release[self.a.id].clear()
        threading.Timer(0.05, self.release[self.a.id].set).start()
        self.assertTrue(_wait_for(lambda: c.state.turn_active), "C waited longest")
        self.assertFalse(self.b.state.turn_active)
        self.release[c.id].set()
        self.assertTrue(_wait_for(lambda: self.b.state.turn_active), "then B")
        self.release[self.b.id].set()
        self.assertTrue(_wait_for(lambda: len(self.a.state.conversation_history) == 4), "then A's second")


class TestApprovalsPerThread(unittest.TestCase):

    def test_each_thread_sees_and_answers_its_own(self):
        a = ap.Approvals()
        got = {}
        a.ask(ap.ApprovalRequest(tool="x", thread=""), lambda c: got.__setitem__("main", c))
        a.ask(ap.ApprovalRequest(tool="y", thread="t2"), lambda c: got.__setitem__("t2", c))
        self.assertEqual(a.current("t2").tool, "y")
        self.assertEqual(a.current("").tool, "x")
        self.assertEqual(a.current().tool, "x", "no thread: the oldest of all, as before")
        self.assertEqual(a.count("t2"), 1)
        self.assertEqual(a.count(), 2)
        self.assertTrue(a.answer("deny", thread="t2"))
        self.assertEqual(got, {"t2": "deny"})
        a.cancel_all("t2")
        self.assertEqual(a.count(), 1, "Stop in t2 leaves the default thread's question")
        a.cancel_all("")
        self.assertEqual(got["main"], "cancel")

    def test_allow_this_turn_is_per_thread(self):
        a = ap.Approvals()
        got = []
        a.ask(ap.ApprovalRequest(tool="x", thread="t2"), got.append)
        a.ask(ap.ApprovalRequest(tool="y", thread=""), got.append)
        a.ask(ap.ApprovalRequest(tool="z", thread="t2"), got.append)
        a.answer("always", thread="t2")
        self.assertEqual(got, ["always", "always"], "t2's other question is answered, not the default's")
        self.assertTrue(a.allowed_always("any", "t2"))
        self.assertFalse(a.allowed_always("any", ""))
        a.new_turn("")
        self.assertTrue(a.allowed_always("any", "t2"), "another thread's new turn keeps t2's")
        a.new_turn("t2")
        self.assertFalse(a.allowed_always("any", "t2"))

    def test_repeat_card_belongs_to_its_thread(self):
        a = ap.Approvals()
        guard = lg.LoopGuard()
        for _ in range(2):
            lg.bridge_wrap(guard, a, False, "execute_blender_code", {"code": "x"}, "s", lambda: ({}, None), thread="t2")
        self.assertIsNone(lg.bridge_wrap(guard, a, False, "execute_blender_code", {"code": "x"}, "s",
                                         lambda: ({}, None)), "the default thread counts apart")
        _r, check = lg.bridge_wrap(guard, a, False, "execute_blender_code", {"code": "x"}, "s",
                                   lambda: ({"status": "ok"}, None), thread="t2")
        self.assertIsNotNone(check)
        self.assertIsNone(a.current(""))
        self.assertEqual(a.current("t2").thread, "t2")


class TestQuestionsAndPlanPerThread(unittest.TestCase):

    def setUp(self):
        cw._questions.clear()
        self.addCleanup(cw._questions.clear)

    def test_questions_carry_their_thread(self):
        q1 = cw.post_question("Which color?", ["Red", "Blue"])
        with cw.for_thread("t2"):
            q2 = cw.post_question("Which node?", ["Mix"])
        self.assertEqual(cw.current_question("t2")[0], q2)
        self.assertEqual(cw.current_question("")[0], q1)
        self.assertEqual(cw.current_question()[0], q1, "no thread: the oldest of all")
        cw.cancel_questions("t2")
        self.assertTrue(cw.question_state(q2)["cancelled"])
        self.assertFalse(cw.question_state(q1)["cancelled"], "Stop in t2 leaves the other")
        self.assertTrue(cw.answer_question("Red", thread=""))
        self.assertEqual(cw.question_state(q1)["answer"], "Red")

    def test_for_thread_restores(self):
        with cw.for_thread("t2"):
            with cw.for_thread("t3"):
                self.assertEqual(cw._thread[0], "t3")
            self.assertEqual(cw._thread[0], "t2")
        self.assertEqual(cw._thread[0], "")


class _FakeAcpClient:
    """Enough of AcpClient for the engine: sessions, prompts and updates."""

    def __init__(self, argv, cwd="", env=None, on_update=None, on_permission=None):
        self.on_update = on_update
        self.sessions = []          # (session id, servers)
        self.prompts = []           # (session id, text)
        self.update_session = ""
        self.alive = True
        self.commands = []
        self.cancelled = []

    def start(self):
        pass

    def initialize(self, timeout=None):
        return types.SimpleNamespace(load_session=True, image=False, name="Fake", version="1", mcp_http=False)

    def new_session(self, cwd, servers, timeout=None):
        sid = "s{:d}".format(len(self.sessions) + 1)
        self.sessions.append((sid, servers))
        return {"sessionId": sid}

    def load_session(self, sid, cwd, servers, timeout=None):
        return {}

    def emit(self, session, text):
        self.update_session = session
        self.on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})

    def prompt(self, sid, blocks, timeout=None):
        self.prompts.append((sid, blocks[0]["text"]))
        self.emit("s-old", "late chunk from another session")
        self.emit(sid, "reply in " + sid)
        return {"stopReason": "end_turn"}

    def cancel(self, sid):
        self.cancelled.append(sid)

    def close(self, timeout=None):
        self.alive = False


_MCP_ENTRY = {"command": "python", "args": ["-m", "blmcp"], "env": {"BFACW_PORT": "9876"}}


class TestExternalAgentSessionPerThread(unittest.TestCase):

    def setUp(self):
        self.states = {"": _state(), "t2": _state()}
        self.engine = ea.AcpEngine(self.states[""], client_factory=_FakeAcpClient)
        self.engine.state_for = self.states.get
        self.engine.connect(["fake"], _MCP_ENTRY, os.path.join(os.environ.get("TEMP", "."), "bfacw_t"), wait=True)
        self.assertTrue(self.engine.connected, self.engine.error)
        self.client = self.engine.client

    def _turn(self, text, thread=""):
        self.engine.submit(eng.Prompt(text=text, thread=thread))
        self.assertTrue(_wait_for(lambda: not self.engine.is_busy()))
        self.engine.drain()

    def _env(self, servers):
        return {e["name"]: e["value"] for e in servers[0].get("env") or []}

    def test_each_thread_its_own_session_and_chat(self):
        self._turn("hello main")
        self._turn("hello node", thread="t2")
        self._turn("again main")
        self.assertEqual(len(self.client.sessions), 2, "one session per thread, kept open")
        main_sid, node_sid = self.client.sessions[0][0], self.client.sessions[1][0]
        self.assertNotIn("BFACW_THREAD_ID", self._env(self.client.sessions[0][1]))
        self.assertEqual(self._env(self.client.sessions[1][1])["BFACW_THREAD_ID"], "t2")
        self.assertEqual(self._env(self.client.sessions[1][1])["BFACW_PORT"], "9876")
        self.assertEqual(self.client.prompts, [(main_sid, "hello main"), (node_sid, "hello node"),
                                               (main_sid, "again main")])
        main = [m["content"] for m in self.states[""].conversation_history]
        node = [m["content"] for m in self.states["t2"].conversation_history]
        self.assertIn("hello main", main)
        self.assertIn("again main", main)
        self.assertNotIn("hello node", main)
        self.assertIn("hello node", node)
        self.assertIn("reply in " + node_sid, node)
        self.assertFalse(any("late chunk" in str(c) for c in main + node), "other sessions' updates dropped")

    def test_queued_prompt_runs_in_its_thread(self):
        release = threading.Event()
        real_prompt = self.client.prompt

        def slow_prompt(sid, blocks, timeout=None):
            release.wait(5)
            return real_prompt(sid, blocks, timeout)
        self.client.prompt = slow_prompt
        self.engine.submit(eng.Prompt(text="first, main"))
        _tid, pos = self.engine.submit(eng.Prompt(text="queued, node", thread="t2"))
        self.assertEqual(pos, 1)
        release.set()
        self.assertTrue(_wait_for(lambda: len(self.client.prompts) == 2 and not self.engine.is_busy()))
        self.engine.drain()
        node = [m["content"] for m in self.states["t2"].conversation_history]
        self.assertIn("queued, node", node)
        self.assertEqual(self.engine.thread, "t2")

    def test_stop_in_a_waiting_thread_drops_only_its_messages(self):
        release = threading.Event()
        real_prompt = self.client.prompt
        self.client.prompt = lambda sid, blocks, timeout=None: (release.wait(5), real_prompt(sid, blocks))[1]
        self.engine.submit(eng.Prompt(text="main runs"))
        self.engine.submit(eng.Prompt(text="node waits", thread="t2"))
        self.engine.submit(eng.Prompt(text="main waits"))
        self.assertEqual(self.engine.drop_queued("t2"), 1)
        self.assertEqual([p["message"] for p in self.engine.queue_items()], ["main waits"])
        release.set()
        self.assertTrue(_wait_for(lambda: len(self.client.prompts) == 2 and not self.engine.is_busy()))

    def test_new_thread_in_an_editor_opens_a_fresh_session(self):
        self._turn("node one", thread="t2")
        self._turn("main one")
        self.engine.forget_thread("t2")
        self._turn("node two", thread="t2")
        sids = [sid for sid, _servers in self.client.sessions]
        self.assertEqual(len(sids), 3, "main, node, and the node's fresh one")
        self.assertEqual(self.client.prompts[-1][0], sids[2])

    def test_permission_cards_carry_the_thread(self):
        self.engine.ask_coworker_tools = True
        self.engine.use_thread("t2")
        self.engine._on_permission({"toolCall": {"title": "mcp__bfa-coworker__execute_blender_code"},
                                    "options": [{"kind": "allow_once", "optionId": "o"}]}, lambda _c: None)
        self.assertEqual(self.engine.approvals.current("t2").thread, "t2")
        self.assertIsNone(self.engine.approvals.current(""))


class TestBridgeRunsCallsForTheirThread(unittest.TestCase):
    """The bridge reads the payload's ``thread``: the call's plan / questions / digest
    (coworker_api) and its activity row are that thread's."""

    def setUp(self):
        self.br, self.si, self.act = _load("mcp_to_blender_server", "session_identity", "activity")
        self.act.clear()

    def _request(self, **extra):
        seen = []

        def fake_execute(code, strict_json=False):
            seen.append(cw._thread[0])
            return self.br._ExecResult({"status": "ok", "result": None})
        payload = {"type": "execute", "code": "result = 1", "strict_json": True, "tool": "update_plan",
                   "token": self.si.session_token(), **extra}
        with unittest.mock.patch.object(self.br, "_execute_code", fake_execute):
            self.br._execute_code_from_request(json.dumps(payload).encode())
        return seen[0], self.act.entries(1)[0].thread

    def test_thread_from_the_payload(self):
        self.assertEqual(self._request(thread="t5"), ("t5", "t5"))
        self.assertEqual(self._request(thread="main"), ("", ""), "the default thread's id is no tag")
        self.assertEqual(self._request(), ("", ""), "no thread: as before")
        self.assertEqual(cw._thread[0], "", "restored after the call")


def _have(*names):
    return all(importlib.util.find_spec(n) is not None for n in names)


@unittest.skipUnless(_have("mcp", "starlette", "httpx", "yaml"), "needs the MCP server's dependencies")
class TestMcpThreadHeader(unittest.TestCase):

    def setUp(self):
        sys.path.insert(0, str(_REPO / "mcp"))
        self.addCleanup(sys.path.remove, str(_REPO / "mcp"))
        patcher = unittest.mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("BFACW_TOKEN", "BFACW_THREAD_ID", "BFACW_SESSION_ID"):
            os.environ.pop(name, None)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def _call(self, **headers):
        import blmcp
        from blmcp.tools_helpers.bounded_output import CURRENT_THREAD
        from mcp.server.fastmcp import FastMCP
        from starlette.testclient import TestClient
        mcp = FastMCP("threads-test")

        @mcp.tool()
        def which_thread() -> str:
            """The calling thread."""
            return CURRENT_THREAD.get() or "-"

        blmcp._configure_http(mcp, "127.0.0.1", 9191)
        with TestClient(mcp.streamable_http_app(), base_url="http://127.0.0.1:9191") as client:
            body = json.dumps({"jsonrpc": "2.0", "id": "1", "method": "tools/call",
                               "params": {"name": "which_thread", "arguments": {}}})
            resp = client.post("/", content=body, headers={"Content-Type": "application/json",
                                                           "Accept": "application/json, text/event-stream",
                                                           **headers})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.text

    def test_header_reaches_the_tool(self):
        self.assertIn('"text":"t7"', self._call(**{"X-Coworker-Thread": "t7"}).replace(" ", ""))
        self.assertIn('"text":"-"', self._call().replace(" ", ""))

    def _bridge_payload(self, thread_var="", env=""):
        """What send_code puts on the wire, caught by a one-shot socket server."""
        from blmcp.tools_helpers import connection
        from blmcp.tools_helpers.bounded_output import CURRENT_THREAD
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        got = {}

        def serve():
            conn, _ = server.accept()
            buf = b""
            while b"\0" not in buf:
                buf += conn.recv(65536)
            got["payload"] = json.loads(buf.split(b"\0")[0])
            conn.sendall(json.dumps({"status": "ok", "result": 1}).encode() + b"\0")
            conn.close()
        worker = threading.Thread(target=serve)
        worker.start()
        os.environ.update({"BFACW_PORT": str(server.getsockname()[1]), "BFACW_HOST": "127.0.0.1"})
        os.environ.pop("BFACW_THREAD_ID", None)
        if env:
            os.environ["BFACW_THREAD_ID"] = env
        token = CURRENT_THREAD.set(thread_var)
        try:
            connection.send_code("result = 1", strict_json=True)
        finally:
            CURRENT_THREAD.reset(token)
            worker.join(5)
            server.close()
        return got["payload"]

    def test_send_code_stamps_the_thread(self):
        self.assertEqual(self._bridge_payload(thread_var="t7")["thread"], "t7")
        self.assertEqual(self._bridge_payload(env="t9")["thread"], "t9", "an ACP session's server")
        self.assertNotIn("thread", self._bridge_payload(), "no thread: the payload as before")


if __name__ == "__main__":
    unittest.main()
