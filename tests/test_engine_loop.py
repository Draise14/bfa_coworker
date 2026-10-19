# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for LoopEngine (engine_loop.py, Tier 4i.1b) with a stand-in controller.

1. A turn forwards the same arguments the chat operator used to pass to
   ``run_conversation_turn`` and reports it as events: streamed text in
   segments (the loop restarts its text each LLM round), reasoning, usage,
   stop reason.
2. Busy -> queued (unless Stop was pressed); the queue is drained after the
   turn with each item's own settings.
3. A non-streamed reply is reported once; a streamed one is not duplicated;
   a blocked turn does not claim the previous turn's reply.
4. Errors and Stop end the turn with ``error`` / ``cancelled``.

Run with::

    python -m unittest tests.test_engine_loop -v
"""

__all__ = ()

import importlib
import sys
import threading
import time
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    """Import engine + engine_loop as a stub package (its __init__ needs bpy)."""
    name = "bfacw_loop_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".engine"), importlib.import_module(name + ".engine_loop")


eng, loop = _load()


class _State:
    def __init__(self):
        self.turn_active = False
        self.error = ""
        self.ui_status = ""
        self.turn_usage = {}


class _Controller:
    """Mimics the parts of agent_controller that LoopEngine uses."""

    def __init__(self, script=None, final="Done: cube added.", raise_exc=None, block=None):
        self._agent_state = _State()
        self._stop_event = threading.Event()
        self.queue = []
        self.calls = []
        self.script = script if script is not None else [
            ("status", "Thinking"),
            ("reasoning", "I need"), ("reasoning", "I need a cube."),
            ("text", "I'll add"), ("text", "I'll add a cube."),
            ("text", "Done:"), ("text", "Done: cube added."),
        ]
        self.final = final
        self.raise_exc = raise_exc
        self.block = block  # threading.Event the turn waits on
        self.history = [{"role": "user", "content": "old"},
                        {"role": "assistant", "content": "old reply"}]

    def enqueue_message(self, **kw):
        self.queue.append(kw)
        return len(self.queue)

    def dequeue_message(self):
        return self.queue.pop(0) if self.queue else None

    def request_stop(self):
        self._stop_event.set()

    def run_conversation_turn(self, **kw):
        self.calls.append(kw)
        self._agent_state.turn_active = True
        try:
            if self.block is not None:
                self.block.wait(5)
            for kind, value in self.script:
                if kind == "status":
                    kw["on_status"](value)
                elif kind == "text":
                    kw["on_stream_text"](value)
                else:
                    kw["on_stream_reasoning"](value)
            if self.raise_exc:
                raise self.raise_exc
            self._agent_state.turn_usage = {"prompt_tokens": 100, "completion_tokens": 20,
                                            "total_tokens": 120}
            self.history.append({"role": "user", "content": kw["user_message"]})
            self.history.append({"role": "assistant", "content": "", "tool_calls": [{}]})
            self.history.append({"role": "user", "content": "[System: note]", "system_note": "note"})
            if self.final:
                self.history.append({"role": "assistant", "content": self.final})
            return self.history
        finally:
            self._agent_state.turn_active = False


def _wait_idle(engine, controller, timeout=5.0):
    """Wait until no turn runs and nothing is queued (workers chain turns)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.02)
        alive = [t for t in threading.enumerate() if t.name.startswith("Thread") and t.is_alive()
                 and t is not threading.current_thread()]
        if not controller._agent_state.turn_active and not controller.queue and not alive:
            return
    raise AssertionError("engine did not go idle")


class TestLoopEngine(unittest.TestCase):

    def _engine(self, controller):
        engine = loop.LoopEngine(controller)
        self.finished = []
        engine.on_turn_finished = lambda: self.finished.append(controller._agent_state.ui_status)
        return engine

    def test_turn_events_and_arguments(self):
        c = _Controller()
        engine = self._engine(c)
        prompt = eng.Prompt("make a cube", chat_mode="AGENT")
        cfg = loop.LoopConfig(llm_url="http://x", api_key="k", model="m", mcp_port=9191)
        turn_id, pos = engine.submit(prompt, cfg)
        self.assertEqual(pos, 0)
        _wait_idle(engine, c)
        engine.drain()
        rec = engine.records[turn_id]
        self.assertEqual([t for _, t in rec.text_segments], ["I'll add a cube.", "Done: cube added."])
        self.assertEqual(rec.last_text, "Done: cube added.")  # streamed final not duplicated
        self.assertEqual(rec.thought, "I need a cube.")
        self.assertEqual(rec.usage, {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
        self.assertEqual(rec.stop_reason, "end_turn")
        self.assertEqual(rec.request, "make a cube")
        kw = c.calls[0]
        self.assertEqual((kw["llm_url"], kw["api_key"], kw["model"], kw["mcp_port"], kw["chat_mode"]),
                         ("http://x", "k", "m", 9191, "AGENT"))
        self.assertIsNone(kw["attachments"])
        self.assertIsNone(kw["on_text"])
        self.assertEqual(c._agent_state.ui_status, "Idle")
        self.assertEqual(len(self.finished), 1)

    def test_attachments_forwarded(self):
        c = _Controller()
        engine = self._engine(c)
        engine.submit(eng.Prompt("look", images=["data:image/png;base64,AA"], image_names=["a.png"]),
                      loop.LoopConfig())
        _wait_idle(engine, c)
        self.assertEqual(c.calls[0]["attachments"], ["data:image/png;base64,AA"])
        self.assertEqual(c.calls[0]["attachment_names"], ["a.png"])

    def test_non_streamed_reply_reported_once(self):
        c = _Controller(script=[("status", "Thinking")], final="Plain reply.")
        engine = self._engine(c)
        turn_id, _ = engine.submit(eng.Prompt("hi"), loop.LoopConfig())
        _wait_idle(engine, c)
        engine.drain()
        self.assertEqual(engine.records[turn_id].text, "Plain reply.")

    def test_blocked_turn_does_not_claim_old_reply(self):
        c = _Controller(script=[])
        c.run_conversation_turn = lambda **kw: c.history  # re-entrancy guard path
        engine = self._engine(c)
        turn_id, _ = engine.submit(eng.Prompt("new request"), loop.LoopConfig())
        _wait_idle(engine, c)
        engine.drain()
        self.assertEqual(engine.records[turn_id].text, "")

    def test_busy_queues_and_drains_with_item_settings(self):
        gate = threading.Event()
        c = _Controller(block=gate)
        engine = self._engine(c)
        engine.submit(eng.Prompt("first"), loop.LoopConfig(model="m1"))
        deadline = time.monotonic() + 2
        while not c._agent_state.turn_active and time.monotonic() < deadline:
            time.sleep(0.01)
        turn_id, pos = engine.submit(eng.Prompt("second", chat_mode="ASK"), loop.LoopConfig(model="m2"))
        self.assertEqual((turn_id, pos), (None, 1))
        gate.set()
        _wait_idle(engine, c)
        self.assertEqual([k["user_message"] for k in c.calls], ["first", "second"])
        self.assertEqual([k["model"] for k in c.calls], ["m1", "m2"])
        self.assertEqual(c.calls[1]["chat_mode"], "ASK")

    def test_after_stop_a_new_turn_starts_instead_of_queueing(self):
        gate = threading.Event()
        c = _Controller(block=gate)
        engine = self._engine(c)
        engine.submit(eng.Prompt("first"), loop.LoopConfig())
        deadline = time.monotonic() + 2
        while not c._agent_state.turn_active and time.monotonic() < deadline:
            time.sleep(0.01)
        engine.cancel()
        turn_id, pos = engine.submit(eng.Prompt("after stop"), loop.LoopConfig())
        self.assertIsNotNone(turn_id)
        self.assertEqual(pos, 0)
        gate.set()
        _wait_idle(engine, c)

    def test_error_and_cancel_stop_reasons(self):
        c = _Controller(raise_exc=RuntimeError("boom"))
        engine = self._engine(c)
        turn_id, _ = engine.submit(eng.Prompt("x"), loop.LoopConfig())
        _wait_idle(engine, c)
        engine.drain()
        self.assertEqual(engine.records[turn_id].stop_reason, "error")
        self.assertEqual(engine.records[turn_id].errors, ["boom"])
        self.assertEqual(c._agent_state.error, "boom")

        c2 = _Controller()
        engine2 = self._engine(c2)
        c2._stop_event.set()  # Stop pressed while the turn ran
        turn_id2, _ = engine2.submit(eng.Prompt("y"), loop.LoopConfig())
        _wait_idle(engine2, c2)
        engine2.drain()
        self.assertEqual(engine2.records[turn_id2].stop_reason, "cancelled")

    def test_records_are_capped(self):
        c = _Controller(script=[])
        engine = self._engine(c)
        for i in range(loop._MAX_RECORDS + 5):
            engine.bus.emit("turn_start", "t{:d}".format(i), text="x")
        engine.drain()
        self.assertEqual(len(engine.records), loop._MAX_RECORDS)
        self.assertEqual(engine.latest_record.turn_id, "t{:d}".format(loop._MAX_RECORDS + 4))


class TestStreamTracker(unittest.TestCase):

    def test_restart_starts_new_segment(self):
        tr = loop._StreamTracker("m")
        self.assertEqual(tr.feed("Hel"), ("Hel", "m1"))
        self.assertEqual(tr.feed("Hello"), ("lo", "m1"))
        self.assertEqual(tr.feed("Done"), ("Done", "m2"))
        self.assertEqual(tr.feed("Done"), ("", "m2"))


if __name__ == "__main__":
    unittest.main()
