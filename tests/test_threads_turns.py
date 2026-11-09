# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Two threads through the real turn loop (Threads 1, #357).

The real ``agent_controller`` (exec'd with a stand-in bpy, as in
``test_turn_loop_integration``) runs turns for the default thread and for a
second thread against the fake LLM / MCP server:

1. Each thread keeps its own history and plan; the default thread is the
   module-level state, so one thread behaves exactly as before.
2. Stop is per thread: stopping one never sets the other's Stop, and an LLM
   error lands in the failing thread's state only (the transport writes the
   turn's own state).
3. One turn at a time: while one thread's turn runs, another's returns at
   once; the LoopEngine queues it in its own thread and starts it when the
   gate frees (``next_turn``).
4. The thread rides on every MCP call (``X-Coworker-Thread``).
5. New Thread in one thread leaves the other's ``Coworker_*`` text blocks and
   its remembered scene-lock names alone; undo names go through ``repr()``.

Run with::

    python -m unittest tests.test_threads_turns -v
"""

__all__ = ()

import ast
import json
import os
import sys
import threading
import time
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from test_turn_loop_integration import (  # noqa: E402
    _TurnLoopTestBase, _EXECUTE_CODE_TOOL, _start_fake_server, _tool_call_msg,
)


def _plan_call(call_id, **args):
    return {"content": "", "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": "update_plan", "arguments": json.dumps(args)}}]}


class _ThreadsBase(_TurnLoopTestBase):

    def setUp(self):
        super().setUp()
        self.node = self.ac.THREADS.create(editor="NODE_EDITOR")

    def _serve(self, script):
        self.server.shutdown()
        self.server.server_close()
        self.server = _start_fake_server(script, mcp_tools=[_EXECUTE_CODE_TOOL])
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = "local"
        cfg.local_port = self.port
        cfg.local_ctx_size = 8192
        cfg.local_max_tokens = 1024
        cfg.thinking_budget_tokens = 0
        self.lm.set_config(cfg)
        self.lm._runtime_ctx_cache = None

    def _turn(self, message, thread=None):
        self._pin_fake_bpy()
        try:
            return self.ac.run_conversation_turn(message, chat_mode="AGENT", llm_url=None,
                                                 model="fake-model", mcp_port=self.port, thread=thread)
        finally:
            self._unpin_fake_bpy()

    @staticmethod
    def _said(history):
        return [(m["role"], m.get("content")) for m in history if m.get("role") in ("user", "assistant")
                and not m.get("system_note") and not m.get("ui_only")]


class TestTwoThreadsTakeTurns(_ThreadsBase):

    def test_separate_history_and_plan(self):
        self._serve([
            {"content": "A cube is in the scene."},
            _plan_call("p1", steps=["add a Mix node", "connect it"]),
            {"content": "The node tree has a Mix node."},
        ])
        self._turn("add a cube")
        self._turn("tidy the nodes", thread=self.node)
        main, node = self.state.conversation_history, self.node.state.conversation_history
        self.assertEqual([c for r, c in self._said(main) if r == "user"], ["add a cube"])
        self.assertEqual(self._said(main)[-1], ("assistant", "A cube is in the scene."))
        self.assertEqual([c for r, c in self._said(node) if r == "user"], ["tidy the nodes"])
        self.assertEqual(self._said(node)[-1], ("assistant", "The node tree has a Mix node."))
        self.assertNotIn("tidy the nodes", json.dumps(main))
        self.assertNotIn("add a cube", json.dumps(node))
        self.assertEqual(node[0]["role"], "system", "a new thread gets its own system prompt")
        # The plan went to the node thread's store, the default thread's is untouched.
        self.assertEqual([s["text"] for s in self.node.store.goal.steps], ["add a Mix node", "connect it"])
        self.assertEqual(self.sm.store.goal.steps, [])
        self.assertEqual(self.node.turn_count, 1)
        self.assertEqual(self.ac._session_turn_count, 1)
        self.assertFalse(self.state.turn_active or self.node.state.turn_active)
        self.assertIsNone(self.ac.THREADS.running())

    def test_thread_rides_on_mcp_calls(self):
        self._serve([_tool_call_msg("c1", "print('main')"), {"content": "ok"},
                     _tool_call_msg("c2", "print('node')"), {"content": "ok"}])
        self._turn("main step")
        self._turn("node step", thread=self.node)
        with self.server.lock:
            calls = [(h, json.dumps(b)) for h, b in zip(self.server.mcp_thread_headers, self.server.mcp_requests)
                     if b.get("method") == "tools/call"]
        self.assertTrue(calls)
        self.assertEqual({h for h, b in calls if "print('main')" in b}, {"main"})
        self.assertEqual({h for h, b in calls if "print('node')" in b}, {self.node.id})
        # The loop's own bookkeeping calls carry the thread too.
        self.assertIn(self.node.id, {h for h, _b in calls})
        self.assertNotIn("", {h for h, _b in calls})

    def test_an_error_stays_in_its_thread(self):
        self._serve([{"content": "fine"}])
        self.server.chat_error_specs = [{"is_stream": None, "status": 401,
                                         "body": "{\"error\": {\"message\": \"bad key\"}}"} for _ in range(8)]
        self.state.error = ""
        self._turn("this fails", thread=self.node)
        self.assertTrue(self.node.state.error, "the node thread's turn failed")
        self.assertEqual(self.state.error, "", "the default thread's state is untouched")


class TestStopAndGate(_ThreadsBase):

    def test_stop_is_per_thread(self):
        self.ac.request_stop(self.node)
        self.assertTrue(self.node.stop.is_set())
        self.assertFalse(self.ac._stop_event.is_set())
        self.ac.request_stop()
        self.assertTrue(self.ac._stop_event.is_set())
        self.ac.clear_stop(self.node)
        self.assertFalse(self.node.stop.is_set())
        self.assertTrue(self.ac._stop_event.is_set(), "clearing one leaves the other")
        self.ac.clear_stop()

    def test_a_new_turn_does_not_clear_another_threads_stop(self):
        self._serve([{"content": "node done"}])
        self.ac.request_stop()                       # Stop pressed in the default thread
        self._turn("node work", thread=self.node)
        self.assertTrue(self.ac._stop_event.is_set(), "the node turn started without clearing it")
        self.assertEqual(self._said(self.node.state.conversation_history)[-1], ("assistant", "node done"))

    def test_one_turn_at_a_time(self):
        self._serve([{"content": "node done"}])
        self.assertTrue(self.ac.THREADS.try_begin(self.ac.THREADS.default))    # the default's turn runs
        try:
            before = list(self.node.state.conversation_history)
            self.assertIs(self._turn("node work", thread=self.node), self.node.state.conversation_history)
            self.assertEqual(self.node.state.conversation_history, before, "nothing started")
            self.assertTrue(self.ac.turn_blocked(self.node))
            self.assertFalse(self.ac.turn_blocked())
        finally:
            self.ac.THREADS.end(self.ac.THREADS.default)
        self._turn("node work", thread=self.node)
        self.assertEqual(self._said(self.node.state.conversation_history)[-1], ("assistant", "node done"))

    def test_loop_engines_queue_and_hand_over(self):
        from bfa_coworker.engine import Prompt
        from bfa_coworker.engine_loop import LoopConfig, LoopEngine
        self._serve([{"content": "node done"}])
        main_engine = LoopEngine(self.ac.controller_for(None))
        node_engine = LoopEngine(self.ac.controller_for(self.node))
        self.ac.THREADS.default.engine, self.node.engine = main_engine, node_engine
        self._pin_fake_bpy()
        self.addCleanup(self._unpin_fake_bpy)
        self.assertTrue(self.ac.THREADS.try_begin(self.ac.THREADS.default))    # the default's turn runs
        turn, pos = node_engine.submit(Prompt(text="node work"), LoopConfig(mcp_port=self.port))
        self.assertIsNone(turn)
        self.assertEqual(pos, 1)
        self.assertEqual(self.node.queue.pending_count, 1)
        self.assertEqual(self.ac._message_queue.pending_count, 0)
        # The default thread's turn ends: its engine hands over to the node thread.
        self.ac.THREADS.end(self.ac.THREADS.default)
        main_engine._dequeue_next()
        end = time.time() + 20
        while time.time() < end and not any(m.get("content") == "node done"
                                            for m in self.node.state.conversation_history):
            time.sleep(0.05)
        self.assertEqual(self._said(self.node.state.conversation_history)[-1], ("assistant", "node done"))
        self.assertEqual(self.node.queue.pending_count, 0)
        self.assertNotIn("node work", json.dumps(self.state.conversation_history))


class _Texts(list):
    def remove(self, block):        # bpy.data.texts.remove
        super().remove(block)


class TestNewThreadLeavesOthersAlone(_ThreadsBase):

    def _fake_texts(self, names):
        texts = _Texts(types.SimpleNamespace(name=n) for n in names)
        fake = types.SimpleNamespace(data=types.SimpleNamespace(texts=texts))
        saved = sys.modules.get("bpy")
        sys.modules["bpy"] = fake
        self.addCleanup(lambda: sys.modules.__setitem__("bpy", saved) if saved else sys.modules.pop("bpy", None))
        return texts

    def test_text_blocks(self):
        texts = self._fake_texts(["Coworker_001", "Coworker_002", "Coworker_003", "Notes"])
        self.ac.THREADS.default.text_blocks.update({"Coworker_001", "Coworker_003"})
        self.node.text_blocks.add("Coworker_002")
        self.ac._clear_coworker_text_blocks(self.node)
        self.assertEqual([t.name for t in texts], ["Coworker_001", "Coworker_003", "Notes"])
        self.ac.THREADS.remove(self.node.id)
        self.ac._clear_coworker_text_blocks()       # one thread left: every Coworker_* block, as before
        self.assertEqual([t.name for t in texts], ["Notes"])

    def test_scene_lock_names(self):
        cw = sys.modules["bfa_coworker.co_work_guard"]
        cw.clear_session()
        cw.remember_session({"Cube"}, set(), thread="main")
        cw.remember_session({"MixNode"}, set(), thread=self.node.id)
        self.ac.clear_session_scene_lock(self.node)
        self.assertEqual(cw.session_names()[0], {"Cube"})
        self.ac.clear_session_scene_lock()
        self.assertEqual(cw.session_names()[0], set())

    def test_undo_names_go_through_repr(self):
        code = self.ac._undo_code("push", "Coworker (3D)'s turn")
        ast.parse(code)
        self.assertIn('"Coworker (3D)\'s turn"', code)
        self.assertIn("undo_push(message='bfa_coworker_step')", self.ac._undo_code("push", "bfa_coworker_step"))

    def test_mcp_header_names_the_worker_thread(self):
        threads = sys.modules["bfa_coworker.threads"]
        self.assertEqual(self.ac._mcp_headers()["X-Coworker-Thread"], "main")
        with threads.use(self.node):
            self.assertEqual(self.ac._mcp_headers()["X-Coworker-Thread"], self.node.id)


if __name__ == "__main__":
    unittest.main()
