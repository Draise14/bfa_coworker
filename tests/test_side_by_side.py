# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Chats running side by side (Threads 3, #359).

1. The runner: how many chats run at once per engine (Local chained, online up
   to the cap, the Local opt-in on the unified KV slots), a queue beyond the
   cap, the slot reserved before the worker starts, Stop in one chat leaving the
   others running, and Stop All.
2. Interleaving: calls of several chats take turns on the main thread, one call
   per chat per tick, round-robin; the step journal says who stepped on the one
   undo stack; each step is named by its chat.
3. The External Agent: one session per chat on one agent process, prompts at
   once; updates and permission requests routed by ``sessionId``; Stop cancels
   only that chat's session.

Run with::

    python -m unittest tests.test_side_by_side -v
"""

__all__ = ()

import json
import os
import sys
import threading
import time
import unittest
import unittest.mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from test_threads import _FakeController, _load, _registry, _wait_for, eng, el, th  # noqa: E402

sbs, ea, ac_client = _load("side_by_side", "engine_acp", "acp_client")


class _SlotController(_FakeController):
    """The fake controller with the runner's slot reservation (as ThreadController)."""

    def reserve_turn(self):
        return self.reg.try_begin(self.thread)

    def release_turn(self):
        if not self.thread.state.turn_active:
            self.reg.end(self.thread)

    def peek_message(self):
        return self.thread.queue.peek()

    def next_turn(self, engine=None):
        waiting = self.reg.next_waiting()
        if waiting is None:
            return None
        return engine if waiting is self.thread else waiting.engine


class TestSlots(unittest.TestCase):

    def test_online_engines_run_side_by_side_local_chained(self):
        self.assertEqual(th.slots_for("remote"), 3)
        self.assertEqual(th.slots_for("agent", 5), 5)
        self.assertEqual(th.slots_for("local", 3), 1, "Local: one after another")
        self.assertEqual(th.slots_for("local", 3, local_side_by_side=True), 3, "opt-in: the cap")
        self.assertEqual(th.slots_for("local", 8, local_side_by_side=True), th.LOCAL_SLOTS_MAX,
                         "never more than the server's unified slots")
        self.assertEqual(th.slots_for("harness", 3), 1, "the outside client is one chat")
        self.assertEqual(th.slots_for("remote", 0), 1)
        self.assertEqual(th.slots_for("remote", 99), th.CAP_MAX)

    def test_cap_and_queue(self):
        reg = _registry()
        reg.kind = "remote"
        a, b, c, d = (reg.create() for _ in range(4))
        self.assertTrue(all(reg.try_begin(t) for t in (a, b, c)))
        self.assertEqual(reg.running_threads(), [a, b, c])
        self.assertTrue(reg.blocked(d))
        self.assertFalse(reg.try_begin(d))
        self.assertFalse(reg.blocked(b), "a running chat is never blocked by itself")
        self.assertFalse(reg.remove(b.id), "never while its turn runs")
        reg.end(b)
        self.assertTrue(reg.try_begin(d), "a free slot")
        self.assertEqual(reg.running_threads(), [a, c, d])
        self.assertIs(reg.running(), a, "the first started")

    def test_next_waiting_skips_running_chats(self):
        reg = _registry()
        reg.kind = "remote"
        a, b = reg.create(), reg.create()
        a.queue.enqueue("a waits for its own turn")
        time.sleep(0.01)
        b.queue.enqueue("b waits for a slot")
        reg.try_begin(a)
        self.assertIs(reg.next_waiting(), b, "a's message waits for a's turn, not for a slot")
        reg.end(a)
        self.assertIs(reg.next_waiting(), a)

    def test_wait_messages(self):
        reg = _registry()
        node = reg.create(editor="NODE_EDITOR", title="Node")
        reg.try_begin(node)
        self.assertEqual(th.wait_message(1, reg.running_threads(), lambda t: t.title),
                         "Waits for the Node chat's turn")
        self.assertEqual(th.wait_message(3, reg.running_threads() * 3, lambda t: t.title),
                         "Waits for a free slot (3 of 3 chats running)")


class TestRunner(unittest.TestCase):
    """Three chats through the real LoopEngine (fake turns that hold until released)."""

    def setUp(self):
        self.reg = _registry()
        self.chats = [self.reg.create(editor=e) for e in ("VIEW_3D", "NODE_EDITOR", "SEQUENCE_EDITOR", "TEXT_EDITOR")]
        self.release = {t.id: threading.Event() for t in self.chats}
        for t in self.chats:
            t.engine = el.LoopEngine(_SlotController(self.reg, t, self.release))
        self.addCleanup(lambda: [e.set() for e in self.release.values()])
        self.cfg = el.LoopConfig()

    def _send(self, thread, text):
        return thread.engine.submit(eng.Prompt(text=text), self.cfg)

    def _active(self):
        return [t for t in self.chats if t.state.turn_active]

    def test_remote_runs_up_to_the_cap_at_once(self):
        self.reg.kind = "remote"
        view, node, seq, text = self.chats
        for t, msg in ((view, "build the scene"), (node, "tidy the nodes"), (seq, "translate the strips")):
            started, pos = self._send(t, msg)
            self.assertTrue(started, msg)
        self.assertTrue(_wait_for(lambda: len(self._active()) == 3), "three chats work at the same time")
        none, pos = self._send(text, "fix the script")
        self.assertIsNone(none, "the fourth waits for a free slot")
        self.assertEqual(text.queue.pending_count, 1)
        self.release[node.id].set()
        self.assertTrue(_wait_for(lambda: text.state.turn_active), "it starts when one ends")
        for t in self.chats:
            self.release[t.id].set()
        self.assertTrue(_wait_for(lambda: not self._active()))
        for t, msg in ((view, "build the scene"), (node, "tidy the nodes"), (seq, "translate the strips"),
                       (text, "fix the script")):
            self.assertEqual(t.state.conversation_history[-1]["content"], "done: " + msg, "each in its own chat")

    def test_local_is_chained(self):
        self.reg.kind = "local"
        view, node = self.chats[:2]
        self.assertTrue(self._send(view, "build")[0])
        self.assertTrue(_wait_for(lambda: view.state.turn_active))
        self.assertIsNone(self._send(node, "tidy")[0], "Local: the second chat waits its turn")
        time.sleep(0.05)
        self.assertFalse(node.state.turn_active)
        self.release[view.id].set()
        self.assertTrue(_wait_for(lambda: node.state.turn_active), "and starts when the first ends")

    def test_local_side_by_side_opt_in(self):
        self.reg.kind, self.reg.local_side_by_side = "local", True
        view, node = self.chats[:2]
        self.assertTrue(self._send(view, "build")[0])
        self.assertTrue(self._send(node, "tidy")[0])
        self.assertTrue(_wait_for(lambda: view.state.turn_active and node.state.turn_active))

    def test_the_slot_is_taken_before_the_worker_starts(self):
        # Two sends in a row on one slot: the second must queue, not start a worker that
        # finds no slot and drops the message.
        self.reg.kind = "local"
        view, node = self.chats[:2]
        self.assertTrue(self._send(view, "first")[0])
        self.assertIsNone(self._send(node, "second")[0])
        self.assertEqual(node.queue.pending_count, 1)
        self.release[view.id].set()
        self.release[node.id].set()
        self.assertTrue(_wait_for(lambda: node.state.conversation_history
                                  and node.state.conversation_history[-1]["content"] == "done: second"))

    def test_stop_in_one_leaves_the_others_running(self):
        self.reg.kind = "remote"
        view, node, seq = self.chats[:3]
        for t in (view, node, seq):
            self._send(t, "work")
        self.assertTrue(_wait_for(lambda: len(self._active()) == 3))
        node.engine.cancel()
        self.assertTrue(_wait_for(lambda: not node.state.turn_active))
        self.assertEqual(node.state.conversation_history[-1]["content"], "stopped")
        time.sleep(0.05)
        self.assertTrue(view.state.turn_active and seq.state.turn_active, "the others keep working")
        self.assertFalse(view.stop.is_set() or seq.stop.is_set())
        self.assertEqual(self.reg.running_threads(), [view, seq])

    def test_stop_all(self):
        self.reg.kind = "remote"
        for t in self.chats[:3]:
            self._send(t, "work")
        self.assertTrue(_wait_for(lambda: len(self._active()) == 3))
        idle = self.chats[3]

        def stop(thread):
            busy = bool(thread.state.turn_active or thread.queue.pending_count)
            thread.queue.clear()
            if thread.state.turn_active:
                thread.engine.cancel()
            return busy
        self.assertEqual(th.stop_all(self.reg, stop), 3, "the three working chats, not the idle one")
        self.assertTrue(_wait_for(lambda: not self._active()))
        self.assertEqual(self.reg.running_threads(), [])
        self.assertFalse(idle.state.conversation_history)



class _Conn:
    """A client socket that has sent its whole request already."""

    def recv(self, _n):
        raise BlockingIOError

    def sendall(self, _data):
        pass

    def close(self):
        pass


class TestInterleaving(unittest.TestCase):

    def test_request_thread_reads_only_the_key(self):
        raw = json.dumps({"type": "execute", "code": 'x = {"thread": "evil"}', "thread": "t3"}).encode()
        self.assertEqual(sbs.request_thread(raw), "t3")
        self.assertEqual(sbs.request_thread(json.dumps({"code": 'print("thread": "x")'}).encode()), "")

    def test_fair_order_one_per_chat_round_robin(self):
        ready = [("a1", "A"), ("a2", "A"), ("b1", "B"), ("c1", "C")]
        self.assertEqual(sbs.fair_order(ready), [("a1", "A"), ("b1", "B"), ("c1", "C")])
        self.assertEqual(sbs.fair_order(ready, last="A"), [("b1", "B"), ("c1", "C"), ("a1", "A")])
        self.assertEqual(sbs.fair_order(ready, last="C"), [("a1", "A"), ("b1", "B"), ("c1", "C")])
        self.assertEqual(sbs.fair_order([]), [])

    def test_two_chats_calls_alternate_on_the_bridge(self):
        br, = _load("mcp_to_blender_server")
        ran = []

        def fake_execute(data):
            ran.append(json.loads(data)["thread"])
            return br._ExecResult({"status": "ok", "result": None}), True
        clients = []
        for chat in ("t1", "t1", "t1", "t2", "t2", "t2"):       # t1's three calls arrived first
            client = br._Client(_Conn())
            client.buffer.extend(json.dumps({"type": "execute", "thread": chat}).encode() + bytes(1))
            clients.append(client)
        saved = list(br._state.clients), br._state.last_chat
        br._state.clients[:] = clients
        br._state.last_chat = ""
        try:
            with unittest.mock.patch.object(br, "_execute_code_from_request", fake_execute):
                for _ in range(4):
                    br._service_clients()
        finally:
            br._state.clients[:], br._state.last_chat = saved
        self.assertEqual(ran, ["t1", "t2", "t1", "t2", "t1", "t2"], "one call per chat per tick, in turn")

    def test_step_journal(self):
        journal = sbs.StepJournal()
        mark = journal.mark()
        journal.note("t1")
        self.assertEqual(journal.others_since("t1", mark), [], "its own steps don't count")
        journal.note("t2")
        self.assertEqual(journal.others_since("t1", mark), ["t2"])
        mark = journal.mark()
        journal.note("t1")
        self.assertEqual(journal.others_since("t1", mark), [], "nothing from t2 since")

    def test_undo_step_names(self):
        self.assertEqual(sbs.undo_step_name("Coworker (3D)", "execute_blender_code"),
                         "Coworker (3D): execute blender code")
        self.assertEqual(sbs.undo_step_name("", ""), "Coworker: a step")

    def test_working_chats_from_every_engine(self):
        saved = list(sbs.WORKING)
        self.addCleanup(lambda: sbs.WORKING.__setitem__(slice(None), saved))
        sbs.WORKING[:] = [lambda: ["t1"], lambda: ["t2", "t1"]]
        self.assertEqual(sbs.working_chats(), ["t1", "t2"])
        self.assertTrue(sbs.side_by_side())
        sbs.WORKING[:] = [lambda: [""]]
        self.assertFalse(sbs.side_by_side())



class _ConcurrentAcpClient:
    """One agent process that runs several sessions' prompts at once (as Claude Code)."""

    def __init__(self, argv, cwd="", env=None, on_update=None, on_permission=None):
        self.on_update, self.on_permission = on_update, on_permission
        self.sessions, self.cancelled, self.in_prompt = [], [], set()
        self.release = {}
        self.alive, self.commands, self.update_session = True, [], ""
        self.closed = False
        self.lock = threading.Lock()

    def start(self):
        pass

    def initialize(self, timeout=None):
        import types
        return types.SimpleNamespace(load_session=False, image=False, name="Fake", version="1", mcp_http=False)

    def new_session(self, cwd, servers, timeout=None):
        with self.lock:
            sid = "s{:d}".format(len(self.sessions) + 1)
            self.sessions.append(sid)
            self.release[sid] = threading.Event()
        return {"sessionId": sid}

    def load_session(self, sid, cwd, servers, timeout=None):
        return {}

    def say(self, sid, text):
        """What the real client hands on: the params with the session id (#359)."""
        self.on_update({"sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk",
                                                     "content": {"type": "text", "text": text}}})

    def prompt(self, sid, blocks, timeout=None):
        with self.lock:
            self.in_prompt.add(sid)
        try:
            self.release[sid].wait(10)
            return {"stopReason": "cancelled" if sid in self.cancelled else "end_turn"}
        finally:
            with self.lock:
                self.in_prompt.discard(sid)

    def cancel(self, sid):
        self.cancelled.append(sid)
        if sid in self.release:
            self.release[sid].set()

    def close(self, timeout=None):
        self.closed, self.alive = True, False
        for ev in self.release.values():
            ev.set()


def _acp_state():
    import types
    return types.SimpleNamespace(conversation_history=[], streaming_text="", reasoning_text="", is_thinking=False,
                                 thinking_start_time=0.0, turn_phase="", ui_status="", error="")


class TestExternalAgentSideBySide(unittest.TestCase):

    def setUp(self):
        self.states = {"": _acp_state(), "t1": _acp_state(), "t2": _acp_state()}
        self.engine = ea.AcpEngine(self.states[""], client_factory=_ConcurrentAcpClient)
        self.engine.state_for = self.states.get
        self.engine.cap = 3
        self.engine.connect(["fake"], {"command": "python", "args": [], "env": {}},
                            os.path.join(os.environ.get("TEMP", "."), "bfacw_sbs"), wait=True)
        self.assertTrue(self.engine.connected, self.engine.error)
        self.client = self.engine.client
        self.addCleanup(self.client.close)

    def _both_prompting(self):
        self.engine.submit(eng.Prompt(text="build the scene", thread="t1"))
        self.engine.submit(eng.Prompt(text="tidy the nodes", thread="t2"))
        self.assertTrue(_wait_for(lambda: len(self.client.in_prompt) == 2), "two prompts at once on one process")
        sid = {t: self.engine._running[t]["session"] for t in ("t1", "t2")}
        self.assertNotEqual(sid["t1"], sid["t2"], "a session per chat")
        return sid

    def _texts(self, tag):
        return " ".join(str(m.get("content", "")) for m in self.states[tag].conversation_history)

    def test_updates_routed_by_session(self):
        sid = self._both_prompting()
        self.client.say(sid["t2"], "Nodes tidied. ")
        self.client.say(sid["t1"], "Scene built. ")
        self.client.say("s-gone", "a cancelled session's late chunk")
        self.client.say(sid["t2"], "Frames added.")
        for ev in self.client.release.values():
            ev.set()
        self.assertTrue(_wait_for(lambda: not self.engine.is_busy()))
        self.engine.drain()
        self.assertIn("Scene built.", self._texts("t1"))
        self.assertNotIn("Nodes", self._texts("t1"))
        self.assertIn("Nodes tidied.", self._texts("t2"))
        self.assertIn("Frames added.", self._texts("t2"))
        self.assertNotIn("late chunk", self._texts("t1") + self._texts("t2"))

    def test_update_to_events_takes_params_or_update(self):
        update = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}}
        self.assertEqual(ac_client.update_to_events({"sessionId": "s1", "update": update}),
                         ac_client.update_to_events(update))

    def test_stop_in_one_chat_cancels_only_its_session(self):
        sid = self._both_prompting()
        self.engine.cancel_thread("t2")
        self.assertEqual(self.client.cancelled, [sid["t2"]])
        self.assertTrue(_wait_for(lambda: not self.engine.busy_for("t2")))
        self.assertTrue(self.engine.busy_for("t1"), "the other chat keeps working")
        self.client.say(sid["t1"], "still going")
        self.client.release[sid["t1"]].set()
        self.assertTrue(_wait_for(lambda: not self.engine.is_busy()))
        self.engine.drain()
        self.assertIn("still going", self._texts("t1"))

    def test_cap_queues_beyond_and_starts_when_a_slot_frees(self):
        self.engine.cap = 1
        self.engine.submit(eng.Prompt(text="first", thread="t1"))
        _turn, pos = self.engine.submit(eng.Prompt(text="second", thread="t2"))
        self.assertEqual(pos, 1, "chained: the second chat waits")
        self.assertTrue(self.engine.blocked_for("t2"))
        self.assertTrue(_wait_for(lambda: len(self.client.in_prompt) == 1))
        self.client.release[self.engine._running["t1"]["session"]].set()
        self.assertTrue(_wait_for(lambda: self.engine.busy_for("t2")), "it starts when the slot frees")

    def test_permission_goes_to_the_asking_sessions_chat(self):
        sid = self._both_prompting()
        self.engine.ask_coworker_tools = True
        self.engine._on_permission({"sessionId": sid["t2"],
                                    "toolCall": {"title": "mcp__bfa-coworker__execute_blender_code"},
                                    "options": [{"kind": "allow_once", "optionId": "o"}]}, lambda _c: None)
        self.assertIsNotNone(self.engine.approvals.current("t2"))
        self.assertIsNone(self.engine.approvals.current("t1"))

    def test_overdue_stop_lets_one_chat_go_without_closing_the_agent(self):
        sid = self._both_prompting()
        self.client.cancel = lambda s: self.client.cancelled.append(s)     # it ignores Stop
        self.engine.stop_grace = 0.1
        self.engine.cancel_thread("t2")
        self.assertTrue(_wait_for(lambda: not self.engine.busy_for("t2")))
        self.assertFalse(self.client.closed, "closing it would end the other chat's turn")
        self.assertTrue(self.engine.busy_for("t1"))
        self.engine.drain()
        self.assertIn("let it go", self._texts("t2"))
        self.client.release[sid["t1"]].set()
        self.client.release[sid["t2"]].set()


if __name__ == "__main__":
    unittest.main()
