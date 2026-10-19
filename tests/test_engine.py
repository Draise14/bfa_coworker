# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the engine seam (engine.py, Tier 4i.1a).

1. ``EventBus`` accepts only known kinds, keeps order across threads, and
   ``drain`` merges consecutive text/thought chunks of the same turn (but
   never across kinds or turns).
2. ``TurnRecord`` folds events into what the panel shows: reply, reasoning,
   tool rows (a final status sticks), plan, usage, questions, stop reason.
3. ``FakeEngine`` emits turn_start ... turn_end from a worker thread, and
   ``cancel`` ends a turn with ``stop_reason="cancelled"``.

Run with::

    python -m unittest tests.test_engine -v
"""

__all__ = ()

import importlib.util
import sys
import threading
import unittest
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker" / "engine.py"
    spec = importlib.util.spec_from_file_location("bfacw_engine_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve postponed annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


eng = _load_module()


class TestEventBus(unittest.TestCase):

    def test_unknown_kind_rejected(self):
        bus = eng.EventBus()
        with self.assertRaises(ValueError):
            bus.emit("not_a_kind", "t1")

    def test_order_and_seq(self):
        bus = eng.EventBus()
        bus.emit("turn_start", "t1", text="hi")
        bus.emit("status", "t1", text="Reading")
        bus.emit("turn_end", "t1", stop_reason="end_turn")
        events = bus.drain()
        self.assertEqual([e.kind for e in events], ["turn_start", "status", "turn_end"])
        self.assertEqual([e.seq for e in events], sorted(e.seq for e in events))
        self.assertEqual(bus.pending(), 0)

    def test_stream_chunks_merge(self):
        bus = eng.EventBus()
        for ch in "Hel", "lo", " world":
            bus.emit("text", "t1", delta=ch)
        bus.emit("thought", "t1", delta="a")
        bus.emit("thought", "t1", delta="b")
        bus.emit("text", "t1", delta="!")
        bus.emit("text", "t2", delta="other turn")
        events = bus.drain()
        self.assertEqual([(e.kind, e.turn_id, e.data["delta"]) for e in events], [
            ("text", "t1", "Hello world"),
            ("thought", "t1", "ab"),
            ("text", "t1", "!"),
            ("text", "t2", "other turn"),
        ])

    def test_max_events_leaves_rest_queued(self):
        bus = eng.EventBus()
        for i in range(5):
            bus.emit("status", "t1", text=str(i))
        self.assertEqual(len(bus.drain(max_events=2)), 2)
        self.assertEqual(bus.pending(), 3)

    def test_emit_from_many_threads(self):
        bus = eng.EventBus()

        def worker(n):
            for _ in range(200):
                bus.emit("status", "t{:d}".format(n), text="x")

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        events = bus.drain()
        self.assertEqual(len(events), 1600)
        self.assertEqual(len({e.seq for e in events}), 1600)

    def test_overflow_drops_only_stream_chunks(self):
        bus = eng.EventBus(max_pending=3)
        for _ in range(3):
            bus.emit("status", "t1", text="s")
        bus.emit("text", "t1", delta="lost")
        bus.emit("turn_end", "t1", stop_reason="end_turn")
        kinds = [e.kind for e in bus.drain()]
        self.assertEqual(kinds, ["status", "status", "status", "turn_end"])
        self.assertEqual(bus.dropped, 1)

    def test_drain_to(self):
        bus = eng.EventBus()
        bus.emit("text", "t1", delta="a")
        bus.emit("text", "t1", delta="b")
        seen = []
        self.assertEqual(bus.drain_to(seen.append), 1)
        self.assertEqual(seen[0].data["delta"], "ab")


class TestTurnRecord(unittest.TestCase):

    def _fold(self, steps, turn_id="t1"):
        bus = eng.EventBus()
        for kind, data in steps:
            bus.emit(kind, data.pop("_turn", turn_id), **data)
        record = eng.TurnRecord(turn_id)
        bus.drain_to(record.apply)
        return record

    def test_reply_reasoning_and_stop(self):
        rec = self._fold([
            ("turn_start", {"text": "make a cube"}),
            ("status", {"text": "Thinking"}),
            ("thought", {"delta": "I should "}),
            ("thought", {"delta": "add a cube."}),
            ("text", {"delta": "Added "}),
            ("text", {"delta": "a cube."}),
            ("turn_end", {"stop_reason": "end_turn"}),
        ])
        self.assertEqual(rec.request, "make a cube")
        self.assertEqual(rec.thought, "I should add a cube.")
        self.assertEqual(rec.text, "Added a cube.")
        self.assertTrue(rec.finished)
        self.assertEqual(rec.status, "")

    def test_tool_rows_and_final_status_sticks(self):
        rec = self._fold([
            ("tool_start", {"tool_id": "c1", "name": "search_api_docs", "title": "Search docs"}),
            ("tool_update", {"tool_id": "c1", "status": "in_progress", "args": {"query": "cube"}}),
            ("tool_end", {"tool_id": "c1", "status": "completed", "result": "3 hits"}),
            ("tool_update", {"tool_id": "c1", "status": "in_progress"}),  # late, ignored
            ("tool_start", {"tool_id": "c2", "name": "execute_blender_code"}),
            ("tool_end", {"tool_id": "c2", "error": "NameError"}),
        ])
        self.assertEqual([(r.tool_id, r.status) for r in rec.tools],
                         [("c1", "completed"), ("c2", "failed")])
        self.assertEqual(rec.tools[0].args, {"query": "cube"})
        self.assertEqual(rec.tools[0].result, "3 hits")
        self.assertEqual(rec.tools[1].error, "NameError")

    def test_plan_usage_question_error(self):
        rec = self._fold([
            ("plan", {"steps": [{"text": "Ground", "status": "done"},
                                {"text": "Logs", "status": "doing"}]}),
            ("usage", {"input_tokens": 500, "output_tokens": 20}),
            ("usage", {"input_tokens": 100, "output_tokens": 5}),
            ("question", {"question": "Which wood?", "choices": ["Oak", "Pine"]}),
            ("error", {"message": "bridge timeout"}),
        ])
        self.assertEqual([s["status"] for s in rec.plan], ["done", "doing"])
        self.assertEqual(rec.usage, {"input_tokens": 600, "output_tokens": 25})
        self.assertEqual(rec.questions[0]["choices"], ["Oak", "Pine"])
        self.assertEqual(rec.errors, ["bridge timeout"])

    def test_other_turns_ignored(self):
        rec = self._fold([
            ("text", {"delta": "mine"}),
            ("text", {"delta": "theirs", "_turn": "t2"}),
        ])
        self.assertEqual(rec.text, "mine")


class TestFakeEngine(unittest.TestCase):

    def test_threaded_turn(self):
        engine = eng.FakeEngine([("text", {"delta": "Hi"}), ("usage", {"input_tokens": 7})])
        turn_id = engine.send(eng.Prompt("hello"))
        engine.wait()
        self.assertFalse(engine.is_busy())
        record = eng.TurnRecord(turn_id)
        engine.bus.drain_to(record.apply)
        self.assertEqual((record.request, record.text, record.stop_reason), ("hello", "Hi", "end_turn"))
        self.assertEqual(record.usage, {"input_tokens": 7})
        self.assertEqual(engine.prompts[0].text, "hello")

    def test_cancel(self):
        engine = eng.FakeEngine([("text", {"delta": "x"})] * 50, step_delay=0.05)
        turn_id = engine.send(eng.Prompt("long"))
        engine.cancel()
        engine.wait()
        record = eng.TurnRecord(turn_id)
        engine.bus.drain_to(record.apply)
        self.assertEqual(record.stop_reason, "cancelled")
        self.assertLess(len(record.text), 50)

    def test_one_turn_at_a_time(self):
        engine = eng.FakeEngine([("text", {"delta": "x"})] * 20, step_delay=0.05)
        engine.send(eng.Prompt("first"))
        with self.assertRaises(RuntimeError):
            engine.send(eng.Prompt("second"))
        engine.stop()

    def test_caps_defaults(self):
        caps = eng.EngineCaps()
        self.assertTrue(caps.cancel)
        self.assertFalse(caps.own_loop)
        self.assertTrue(eng.new_turn_id().startswith("t_"))


if __name__ == "__main__":
    unittest.main()
