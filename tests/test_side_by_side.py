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
4. Remote: chats' requests go out at once; an HTTP 429 backs off only the chat
   that got it (its own worker waits, honouring Retry-After and its Stop).
5. Claims: write calls claim what they change for their chat's running turn; a
   write to another running chat's claim asks (wait / go ahead), waits in Auto
   mode, and is refused when the two would wait for each other; generated code
   is a whole-scene write whose changes (names that came or went) are claimed after it runs.
6. Revert: directly only when no other chat stepped since the turn began;
   else Revert both (not while that chat works) or Cancel -- never a silent
   partial revert; each chat's turn checkpoint is its own.
7. The "Other chats working now: ..." context line.

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

sbs, ea, ac_client, claims, cw = _load("side_by_side", "engine_acp", "acp_client", "claims", "coworker_api")


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

    def test_each_turn_knows_its_chat(self):
        sid = self._both_prompting()
        turns = {t: self.engine._running[t]["turn"] for t in ("t1", "t2")}
        self.assertEqual({t: self.engine.turn_thread(turns[t]) for t in turns}, {"t1": "t1", "t2": "t2"},
                         "not the current thread (the last started)")
        for ev in self.client.release.values():
            ev.set()
        self.assertTrue(_wait_for(lambda: not self.engine.is_busy()))
        self.engine.drain()
        self.assertEqual(self.engine._turn_threads, {}, "forgotten when the turn end is applied")
        self.assertTrue(sid)

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



def _transport():
    import importlib.util
    path = os.path.join(os.path.dirname(_HERE), "addon", "bfa_coworker", "llm_transport.py")
    spec = importlib.util.spec_from_file_location("_bfacw_sbs_transport", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRateLimitPerChat(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.lt = _transport()

    def test_retry_delay(self):
        lt = self.lt
        self.assertEqual(lt.retry_delay("3", 1), 3.0, "the provider's Retry-After")
        self.assertEqual([lt.retry_delay(None, n) for n in (1, 2, 3)], [2.0, 4.0, 8.0])
        self.assertEqual(lt.retry_delay("Wed, 21 Oct 2026 07:28:00 GMT", 2), 4.0, "a date: our own")
        self.assertEqual(lt.retry_delay("999", 1), 60.0, "never longer than a minute")

    def test_a_429_backs_off_only_its_chat(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        lt = self.lt
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)).decode()
                chat = "A" if "chat A" in body else "B"
                seen.append((chat, time.monotonic()))
                limited = chat == "A" and sum(1 for c, _t in seen if c == "A") <= 2
                payload = (json.dumps({"error": {"message": "slow down"}}) if limited else
                           json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok " + chat},
                                                    "finish_reason": "stop"}]}))
                self.send_response(429 if limited else 200)
                if limited:
                    self.send_header("Retry-After", "1")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload.encode())))
                self.end_headers()
                self.wfile.write(payload.encode())

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:{:d}/v1/chat/completions".format(server.server_address[1])
        import types
        states = {c: types.SimpleNamespace(error="", error_full="", error_kind="", warning="", ui_status="")
                  for c in "AB"}
        stops = {c: threading.Event() for c in "AB"}
        done, results = {}, {}

        def chat(name):
            with lt.using(states[name], stops[name]):
                results[name] = lt.openai_chat_completions(url, [{"role": "user", "content": "chat " + name}], [],
                                                           max_tokens=16)
            done[name] = time.monotonic()
        start = time.monotonic()
        workers = [threading.Thread(target=chat, args=(c,)) for c in "AB"]
        for w in workers:
            w.start()
        for w in workers:
            w.join(20)
        self.assertEqual(results["B"]["choices"][0]["message"]["content"], "ok B")
        self.assertEqual(results["A"]["choices"][0]["message"]["content"], "ok A", "A got through after waiting")
        self.assertLess(done["B"] - start, 0.9, "B never waited for A's back-off")
        self.assertGreaterEqual(done["A"] - start, 1.9, "A waited Retry-After twice")
        self.assertIn("Rate limited", states["A"].ui_status)
        self.assertEqual(states["B"].ui_status, "", "B's chat says nothing about it")
        self.assertEqual([c for c, _t in seen].count("B"), 1)

    def test_stop_ends_the_wait(self):
        lt = self.lt
        import types
        state = types.SimpleNamespace(error="", error_full="", error_kind="", warning="", ui_status="")
        stop = threading.Event()
        threading.Timer(0.2, stop.set).start()
        start = time.monotonic()
        with lt.using(state, stop):
            self.assertTrue(lt._wait_unless_stopped(5.0))
        self.assertLess(time.monotonic() - start, 1.0)



class TestClaims(unittest.TestCase):

    def setUp(self):
        self.table = claims.ClaimTable()

    def test_what_a_write_call_names(self):
        targets, whole = claims.targets_of("assign_material_to_objects",
                                           json.dumps({"material_name": "Rust", "object_names": ["Cube", "Rock"]}))
        self.assertEqual(targets, {("material", "Rust"), ("object", "Cube"), ("object", "Rock")})
        self.assertFalse(whole)
        cut = json.dumps({"node_tree_name": "Rock Shader", "insert_mode": "add_top_level", "x": "y" * 500})[:400]
        self.assertIn(("node_tree", "Rock Shader"), claims.targets_of("wire_node_group", cut)[0],
                      "the bridge's arguments are cut at 400 characters")
        targets, whole = claims.targets_of("execute_blender_code", None,
                                           "ob = bpy.data.objects['Cube']\nob.name = \"Hero\"\nbpy.ops.object.mode_set(mode='OBJECT')")
        self.assertTrue(whole, "generated code: a whole-scene write")
        self.assertEqual(targets, {("any", "Cube"), ("any", "Hero")})

    def test_only_existing_datablocks_count_for_code(self):
        code = ("ob = bpy.data.objects.new('New_1', None); result = {'made': ob.name}; "
                "bpy.data.objects['Cube'].hide_set(True)")
        targets, _whole = claims.targets_of("execute_blender_code", None, code, exists={"Cube"}.__contains__)
        self.assertEqual(targets, {("any", "Cube")},
                         "a dict key ('made') or a name it is about to make is no claim")

    def test_which_tools_write(self):
        self.assertTrue(claims.writes("execute_blender_code"))
        self.assertTrue(claims.writes("set_collection_color_tag"))
        for tool in ("get_objects_summary", "search_api_docs", "jump_to_tab_by_name", "update_plan", ""):
            self.assertFalse(claims.writes(tool, plan_safe=("update_plan",)), tool)

    def test_decisions(self):
        running = ["t1", "t2"]
        self.table.claim("t1", {("object", "Cube")})
        self.assertEqual(claims.decide(self.table, "t2", {("object", "Rock")}, running).action, claims.RUN)
        asked = claims.decide(self.table, "t2", {("any", "Cube")}, running)
        self.assertEqual((asked.action, asked.holder, asked.names), (claims.ASK, "t1", ["Cube"]),
                         "a rename of Cube in another chat asks")
        self.assertEqual(claims.decide(self.table, "t2", {("object", "Cube")}, running, ask=False).action,
                         claims.WAIT, "Auto mode's answer: wait")
        self.assertEqual(claims.decide(self.table, "t1", {("object", "Cube")}, running).action, claims.RUN,
                         "its own claim")
        self.assertEqual(claims.decide(self.table, "t2", {("object", "Cube")}, ["t2"]).action, claims.RUN,
                         "claims last for the holder's running turn")
        self.assertEqual(claims.decide(self.table, "t3", {("object", "Cube")}, running).action, claims.RUN,
                         "a chat without a running turn (the user's own client) isn't held")

    def test_no_two_chats_wait_for_each_other(self):
        running = ["t1", "t2"]
        self.table.claim("t1", {("object", "Cube")})
        self.table.claim("t2", {("object", "Lamp")})
        self.table.set_waiting("t2", "t1")          # t2 waits for t1's Cube
        refused = claims.decide(self.table, "t1", {("object", "Lamp")}, running)
        self.assertEqual((refused.action, refused.holder), (claims.REFUSE, "t2"))

    def test_release_frees_and_unblocks(self):
        self.table.claim("t1", {("object", "Cube")})
        self.table.set_waiting("t2", "t1")
        self.table.release("t1")
        self.assertEqual(self.table.claims_of("t1"), set())
        self.assertEqual(self.table.waits_for("t2"), "", "nobody to wait for any more")

    def test_what_a_script_changed(self):
        self.assertEqual(claims.changed_names({"Cube", "Light"}, {"Hero", "Light", "New"}),
                         {("any", "Cube"), ("any", "Hero"), ("any", "New")}, "renamed, made")

    def test_question_words(self):
        self.assertEqual(claims.conflict_question("3D", ["Cube"]),
                         "The 3D chat is changing Cube -- wait for it to finish, or go ahead anyway?")


class TestClaimGateOnTheBridge(unittest.TestCase):
    """The bridge's gate: the second chat renaming Cube asks, through the question card."""

    @classmethod
    def setUpClass(cls):
        cls.br, = _load("mcp_to_blender_server")

    def setUp(self):
        claims.TABLE.clear()
        cw._questions.clear()
        saved = list(sbs.WORKING)
        sbs.WORKING[:] = [lambda: list(self.working)]
        self.addCleanup(lambda: sbs.WORKING.__setitem__(slice(None), saved))
        self.addCleanup(claims.TABLE.clear)
        self.addCleanup(cw._questions.clear)
        self.working = ["t1", "t2"]
        self.runs = []
        saved_exists = self.br._datablock_exists
        self.br._datablock_exists = {"Cube"}.__contains__          # the scene has a Cube
        self.addCleanup(setattr, self.br, "_datablock_exists", saved_exists)

    def _run(self):
        self.runs.append(1)
        return self.br._ExecResult({"status": "ok", "result": None})

    def _rename(self, chat):
        return self.br._claim_gate(chat, "execute_blender_code", None,
                                   "bpy.data.objects['Cube'].name = 'From {:s}'".format(chat), self._run)

    def test_first_runs_and_claims_second_asks(self):
        first = self._rename("t1")
        self.assertEqual(first.response["status"], "ok")
        self.assertIn(("any", "Cube"), claims.TABLE.claims_of("t1"))
        second = self._rename("t2")
        self.assertIsNotNone(second.check_fn, "the second one waits for an answer")
        self.assertEqual(len(self.runs), 1, "and has not overwritten anything")
        qid, question = cw.current_question("t2")
        self.assertIn("is changing Cube", question["question"])
        self.assertEqual(question["choices"], ["Wait", "Go ahead"])
        self.assertIsNone(second.check_fn())
        cw.answer_question("Go ahead", qid)
        self.assertEqual(second.check_fn()["status"], "ok")
        self.assertEqual(len(self.runs), 2)

    def test_a_name_the_other_chat_renamed_still_asks(self):
        self.br._datablock_exists = set().__contains__        # Cube is called Hero3D now
        claims.TABLE.claim("t1", {("any", "Cube"), ("object", "Hero3D")})
        second = self._rename("t2")
        self.assertIsNotNone(second.check_fn, "Cube is gone from the scene, but it is the 3D chat's")
        self.assertEqual(self.runs, [])

    def test_wait_runs_when_the_other_turn_ends(self):
        self._rename("t1")
        second = self._rename("t2")
        qid, _q = cw.current_question("t2")
        cw.answer_question("Wait", qid)
        self.assertIsNone(second.check_fn())
        self.assertIsNone(second.check_fn(), "still waiting")
        claims.TABLE.release("t1")                  # the 3D chat's turn ended
        self.assertEqual(second.check_fn()["status"], "ok")
        self.assertEqual(len(self.runs), 2)

    def test_stop_while_it_waits_changes_nothing(self):
        self._rename("t1")
        second = self._rename("t2")
        cw.cancel_questions("t2")                   # Stop in the second chat
        self.assertIn("Nothing was changed", second.check_fn()["message"])
        self.assertEqual(len(self.runs), 1)

    def test_one_chat_at_work_runs_as_before(self):
        self.working = ["t2"]
        claims.TABLE.claim("t1", {("object", "Cube")})
        self.assertEqual(self._rename("t2").response["status"], "ok")

    def test_waiting_too_long_gives_up(self):
        self._rename("t1")
        second = self._rename("t2")
        saved = claims.WAIT_LIMIT
        claims.WAIT_LIMIT = -1.0           # already waited too long (Windows' clock ticks in 16 ms)
        self.addCleanup(setattr, claims, "WAIT_LIMIT", saved)
        self.assertIn("nothing was changed", second.check_fn()["message"])
        self.assertIsNone(cw.current_question("t2"), "its question is withdrawn")



class TestRevertDecision(unittest.TestCase):

    def test_the_choices(self):
        title = {"t2": "Node", "t3": "Sequencer"}.get
        self.assertEqual(sbs.revert_choice(False, False, [], []).kind, "none")
        self.assertEqual(sbs.revert_choice(True, True, ["t2"], []).kind, "edited", "you changed it: Undo History")
        direct = sbs.revert_choice(True, False, [], ["t2"])
        self.assertEqual((direct.kind, direct.options), ("direct", ("revert",)),
                         "another chat working elsewhere, but nothing on top of this turn")
        both = sbs.revert_choice(True, False, ["t2"], [], title=title)
        self.assertEqual((both.kind, both.options, both.others), ("choose", ("both", "cancel"), ["t2"]))
        self.assertIn("The Node chat changed the scene", both.message)
        busy = sbs.revert_choice(True, False, ["t2", "t3"], ["t3"], title=title)
        self.assertEqual((busy.kind, busy.options), ("busy", ("cancel",)), "not while it still works")
        self.assertIn("Sequencer chat is still working", busy.message)
        scoped = sbs.revert_choice(True, False, ["t2"], [], has_snapshot=True)
        self.assertEqual(scoped.options, ("both", "scoped", "cancel"), "a scoped restore where a snapshot exists")


class TestRevertWithTwoChats(unittest.TestCase):
    """coworker_api's turn checkpoints, one per chat, and Revert by the journal."""

    def setUp(self):
        self.calls = []
        self._undo = cw._undo
        cw._undo = lambda message="", to_item=-1: self.calls.append(message or to_item) or True
        self.addCleanup(setattr, cw, "_undo", self._undo)
        cw._turns.clear()
        cw._turns[""] = cw._turn
        cw._turn.update(before=False, after=False, edits=0, name="", mark=0)
        saved = list(sbs.WORKING)
        sbs.WORKING[:] = [lambda: list(self.working)]
        self.addCleanup(lambda: sbs.WORKING.__setitem__(slice(None), saved))
        self.working = []

    def _stack(self):
        import types
        names = ["Original"] + [c for c in self.calls if isinstance(c, str)]
        wm = types.SimpleNamespace(undo_stack=types.SimpleNamespace(steps=[types.SimpleNamespace(name=n)
                                                                           for n in names]))
        sys.modules["bpy"] = types.SimpleNamespace(context=types.SimpleNamespace(window_manager=wm))
        self.addCleanup(sys.modules.pop, "bpy", None)
        return names

    def test_each_chat_its_own_checkpoint_and_direct_revert(self):
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        cw.turn_started("Coworker (Node)'s turn", thread="t2")
        self.assertNotEqual(cw._turn_of("t1")["name"], cw._turn_of("t2")["name"], "a Before step each (#359)")
        cw.turn_finished("Coworker (Node)'s turn", thread="t2")
        self.assertTrue(cw.can_revert("t2"))
        self.assertFalse(cw.can_revert("t1"), "its turn still runs")
        self.assertEqual(cw.revert_choice("t2").kind, "direct",
                         "nothing from the 3D chat since the Node turn began (its Before came earlier)")

    def test_another_chat_stepped_since_offers_revert_both(self):
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        sbs.JOURNAL.note("t2")                      # the Node chat's call lands on top
        cw.turn_finished("Coworker (3D)'s turn", thread="t1")
        self.working = ["t2"]
        self.assertEqual(cw.revert_choice("t1").kind, "busy")
        self.assertIn("still working", cw.revert_turn("t1", both=True), "not while it works")
        self.working = []
        self.assertEqual(cw.revert_choice("t1").kind, "choose")
        names = self._stack()
        self.assertIn("Revert Both", cw.revert_turn("t1"), "never a silent partial revert")
        self.assertEqual(self.calls[-1], "Coworker (3D)'s turn", "nothing undone")
        cw._turn_of("t2").update(after=True, edits=cw._edits[0])
        self.assertEqual(cw.revert_turn("t1", both=True), "")
        self.assertEqual(self.calls[-1], names.index(cw._turn_of("t1")["name"]), "back to the 3D turn's Before step")
        self.assertEqual(cw.reverted_with, ["t2"], "the Node chat is told")
        self.assertFalse(cw.can_revert("t2"), "its steps went too")

    def test_without_the_undo_list_both_is_refused(self):
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        sbs.JOURNAL.note("t2")
        cw.turn_finished("Coworker (3D)'s turn", thread="t1")
        before = list(self.calls)
        self.assertIn("Undo History", cw.revert_turn("t1", both=True), "no positional undo (no undo_stack)")
        self.assertEqual(self.calls, before)

    def test_coworkers_own_changes_are_not_your_edit(self):
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        cw.turn_finished("Coworker (3D)'s turn", thread="t1")
        cw.own_change()                             # e.g. the scene lock's release by the bridge
        cw._count_edit()
        self.assertTrue(cw.can_revert("t1"))
        cw._own_until[0] = 0.0
        cw._count_edit()                            # later: yours
        self.assertFalse(cw.can_revert("t1"))

    def test_another_chats_turn_is_not_your_edit(self):
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        cw.turn_finished("Coworker (3D)'s turn", thread="t1")
        cw.turn_started("Coworker (Node)'s turn", thread="t2")
        cw._count_edit()                            # the Node chat's change, while its turn is open
        self.assertTrue(cw.can_revert("t1"), "not counted as yours")
        cw.turn_finished("Coworker (Node)'s turn", thread="t2")
        cw._count_edit()                            # now nobody's turn: yours
        self.assertFalse(cw.can_revert("t1"))



class TestOtherChatsLine(unittest.TestCase):

    def test_line(self):
        self.assertEqual(sbs.other_chats_line([]), "")
        line = sbs.other_chats_line([("Node", "tidy   the rock shader", ["Rock Shader"]),
                                     ("Sequencer", "x" * 80, [])])
        self.assertTrue(line.startswith('Other chats working now: the Node chat ("tidy the rock shader"; '
                                        'changing Rock Shader); the Sequencer chat ("'))
        self.assertIn("…", line, "a long request is cut")
        self.assertIn("ask the user first", line)
        many = sbs.other_chats_line([("3D", "", ["A", "B", "C", "D", "E"])])
        self.assertIn("the 3D chat (changing A, B, C, D ...)", many)


if __name__ == "__main__":
    unittest.main()
