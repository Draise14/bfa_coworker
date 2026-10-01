# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the session memory & checkpoint engine (session_memory.py).

1. ``find_retire_boundary`` must keep the system prompt and the recent
   window, and never cut a tool-call exchange in half.

2. ``compact_history`` must retire old turns, keep a usable memory block,
   use the LLM memory writer when given (falling back to the heuristic on
   failure), and leave UI-only greeting messages out of the archive.

3. The memory block must stay bounded (target ~600 tokens) and carry the
   "Last updated" stamp.

4. ``CheckpointStore`` round-trips: automatic snapshots, non-destructive
   restore (current state saved first), branch without touching the current
   session, archive write/read, and JSON persistence payloads.

Run with::

    python -m unittest tests.test_session_memory -v
"""

__all__ = ()

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "addon" / "bfa_coworker" / "session_memory.py"
    spec = importlib.util.spec_from_file_location("session_memory", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


_sm = _load_module()


def _mk_history(n_turns: int, with_tools: bool = False) -> list:
    msgs = [{"role": "system", "content": "system prompt"}]
    for i in range(n_turns):
        msgs.append({"role": "user", "content": "request {:d}".format(i)})
        if with_tools:
            msgs.append({
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c{:d}".format(i), "type": "function",
                                "function": {"name": "t", "arguments": "{}"}}],
            })
            msgs.append({"role": "tool", "tool_call_id": "c{:d}".format(i),
                         "content": "result {:d}".format(i)})
        msgs.append({"role": "assistant", "content": "reply {:d}".format(i)})
    return msgs


class TestFindRetireBoundary(unittest.TestCase):

    def test_nothing_to_retire_when_small(self):
        history = _mk_history(2)
        self.assertEqual(_sm.find_retire_boundary(history, keep_recent=20),
                         len(history))

    def test_keeps_system_and_window(self):
        history = _mk_history(20)
        boundary = _sm.find_retire_boundary(history, keep_recent=5)
        kept = history[boundary:]
        # At most the recent window (plus one when the boundary advanced to
        # the next user message), starting on a user turn.
        self.assertLessEqual(len(kept), 6)
        self.assertEqual(kept[0]["role"], "user")

    def test_compact_keeps_system_prompt(self):
        history = _mk_history(20)
        kept, _, _ = _sm.compact_history(history, keep_recent=5)
        self.assertEqual(kept[0]["role"], "system")

    def test_compact_never_retires_system_prompt(self):
        history = _mk_history(20)
        _, _, retired = _sm.compact_history(history, keep_recent=5)
        self.assertNotIn("system", [m.get("role") for m in retired])

    def test_never_cuts_tool_pair(self):
        history = _mk_history(20, with_tools=True)
        boundary = _sm.find_retire_boundary(history, keep_recent=5)
        kept = history[boundary:]
        # The boundary lands on a user message, so no orphaned tool result.
        self.assertEqual(kept[0]["role"], "user")
        # And the kept slice is still a well-formed sequence.
        for i, m in enumerate(kept):
            if m.get("role") == "tool":
                prev = kept[i - 1]
                self.assertEqual(prev.get("role"), "assistant")
                self.assertTrue(prev.get("tool_calls"))


class TestBuildMemoryBlock(unittest.TestCase):

    def test_bounded_and_stamped(self):
        turns = [{"role": "user", "content": "x" * 2000}] * 50
        block = _sm.build_memory_block(turns, updated_turn=7)
        self.assertLessEqual(len(block), 600 * 3.5 + 200)
        self.assertIn("Last updated: turn 7", block)

    def test_heuristic_extracts_goal_and_errors(self):
        turns = [
            {"role": "user", "content": "Build a lighthouse in the scene"},
            {"role": "assistant", "content": "created cone and cylinder"},
            {"role": "tool", "content": "error: NameError boom"},
        ]
        block = _sm.build_memory_block(turns)
        self.assertIn("Goal: Build a lighthouse", block)
        self.assertIn("NameError", block)
        self.assertIn("[Session memory]", block)

    def test_summary_replaces_heuristic(self):
        turns = [{"role": "user", "content": "hi"}]
        block = _sm.build_memory_block(turns, summary="Goal: X\nPending: none",
                                       updated_turn=3)
        self.assertTrue(block.startswith("Goal: X"))
        self.assertIn("Last updated: turn 3", block)

    def test_prior_memory_carried_without_summary(self):
        turns = [{"role": "user", "content": "hi"}]
        block = _sm.build_memory_block(turns, prior_memory="old note")
        self.assertIn("old note", block)


class TestCompactHistory(unittest.TestCase):

    def test_manual_compact_rejects_young_history(self):
        # "Compact Now" must treat find_retire_boundary == len(history) as
        # "nothing to compact", so a young conversation is never reduced to
        # the system prompt.
        history = _mk_history(3)  # 7 messages, well under keep_recent
        self.assertGreaterEqual(_sm.find_retire_boundary(history, 8), len(history))

    def test_manual_compact_is_reversible_via_pre_snapshot(self):
        st = _sm.CheckpointStore()
        history = _mk_history(20)
        before = json.loads(json.dumps(history))
        # The operator snapshots the PRE-compaction state first.
        st.snapshot(history, reason="manual-compaction")
        kept, _, _ = _sm.compact_history(history, keep_recent=8)
        self.assertLess(len(kept), len(history))
        restored = st.restore(0, kept)
        self.assertEqual(restored, before)

    def test_retires_and_keeps_recent(self):
        history = _mk_history(20)
        kept, memory, retired = _sm.compact_history(history, keep_recent=5)
        self.assertLessEqual(len(kept), 7)  # system + window (+1 user shift)
        # retired excludes the system prompt, which kept always retains.
        self.assertEqual(len(retired), len(history) - len(kept))
        self.assertNotIn("system", [m.get("role") for m in retired])
        self.assertTrue(memory)

    def test_ui_only_not_retired(self):
        history = _mk_history(20)
        history.insert(1, {"role": "assistant", "content": "welcome",
                           "ui_only": True})
        _, _, retired = _sm.compact_history(history, keep_recent=5)
        self.assertFalse(any(m.get("ui_only") for m in retired))

    def test_llm_writer_used(self):
        history = _mk_history(20)

        def writer(text, prior):
            return "Goal: from LLM writer"

        kept, memory, _ = _sm.compact_history(history, memory_writer=writer,
                                              keep_recent=5)
        self.assertIn("from LLM writer", memory)

    def test_writer_failure_falls_back_to_heuristic(self):
        history = _mk_history(20)

        def writer(text, prior):
            raise RuntimeError("server down")

        _, memory, _ = _sm.compact_history(history, memory_writer=writer,
                                           keep_recent=5)
        self.assertIn("Goal:", memory)

    def test_writer_receives_prior_memory(self):
        history = _mk_history(20)
        seen = {}

        def writer(text, prior):
            seen["prior"] = prior
            return "new note"

        _sm.compact_history(history, prior_memory="prior note",
                            memory_writer=writer, keep_recent=5)
        self.assertEqual(seen["prior"], "prior note")

    def test_history_not_mutated(self):
        history = _mk_history(20)
        snapshot = [dict(m) for m in history]
        _sm.compact_history(history, keep_recent=5)
        self.assertEqual(len(history), len(snapshot))


class TestCheckpointStore(unittest.TestCase):

    def setUp(self):
        self.store = _sm.CheckpointStore()
        self._tmp = tempfile.TemporaryDirectory()
        self.store.archive_path = Path(self._tmp.name) / "archive.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_snapshot_records_fields(self):
        rec = self.store.snapshot(_mk_history(3), reason="compaction",
                                  turn_index=4)
        self.assertEqual(rec["reason"], "compaction")
        self.assertEqual(rec["turn_index"], 4)
        self.assertEqual(rec["message_count"], 7)
        self.assertTrue(rec["window_hash"])

    def test_snapshot_bounded(self):
        for i in range(15):
            self.store.snapshot([{"role": "user", "content": str(i)}],
                                reason="r{:d}".format(i))
        self.assertLessEqual(len(self.store.checkpoints),
                             _sm.CheckpointStore.MAX_CHECKPOINTS)

    def test_restore_snapshots_current_first(self):
        self.store.snapshot(_mk_history(3), reason="compaction")
        current = _mk_history(30)
        restored = self.store.restore(0, current, turn_index=9)
        self.assertEqual(len(restored), 7)
        reasons = [c["reason"] for c in self.store.checkpoints]
        self.assertEqual(reasons[-1], "pre-restore")

    def test_restore_index_error(self):
        with self.assertRaises(IndexError):
            self.store.restore(5, [])

    def test_branch_does_not_snapshot(self):
        self.store.snapshot(_mk_history(3), reason="compaction")
        before = len(self.store.checkpoints)
        branched = self.store.branch(0)
        self.assertEqual(len(branched), 7)
        self.assertEqual(len(self.store.checkpoints), before)

    def test_restore_does_not_alias_target(self):
        self.store.snapshot(_mk_history(3), reason="compaction")
        restored = self.store.restore(0, _mk_history(1))
        restored.append({"role": "user", "content": "new"})
        target_history = self.store.checkpoints[0]["history"]
        self.assertEqual(len(target_history), 7)

    def test_archive_roundtrip(self):
        retired = [{"role": "user", "content": "old"}] * 3
        self.store.append_archive(retired)
        loaded = self.store.load_archive()
        self.assertEqual(len(loaded), 3)
        self.assertEqual(loaded[0]["content"], "old")

    def test_archive_skips_empty(self):
        self.assertEqual(self.store.append_archive([]), 0)
        self.assertFalse(
            (Path(self._tmp.name) / "archive.jsonl").exists())

    def test_restore_at_capacity_returns_selected(self):
        # Fill to capacity so the pre-restore snapshot triggers a trim, which
        # would shift every index if the target were looked up afterwards.
        for i in range(_sm.CheckpointStore.MAX_CHECKPOINTS):
            self.store.snapshot(
                [{"role": "system", "content": "s"},
                 {"role": "user", "content": "cp{:d}".format(i)}],
                reason="r{:d}".format(i))
        restored = self.store.restore(0, _mk_history(1))
        self.assertEqual(restored[-1]["content"], "cp0")

    def test_restore_restores_memory_updated_turn(self):
        self.store.memory_updated_turn = 3
        self.store.snapshot(_mk_history(2), reason="compaction")
        self.store.memory_updated_turn = 99
        self.store.restore(0, _mk_history(1))
        self.assertEqual(self.store.memory_updated_turn, 3)

    def test_archive_rotation_bounds_lines(self):
        old = _sm.MAX_ARCHIVE_MESSAGES
        try:
            _sm.MAX_ARCHIVE_MESSAGES = 5
            self.store.append_archive(
                [{"role": "user", "content": "m{:d}".format(i)} for i in range(8)])
            loaded = self.store.load_archive()
            self.assertEqual(len(loaded), 5)
            self.assertEqual(loaded[-1]["content"], "m7")
        finally:
            _sm.MAX_ARCHIVE_MESSAGES = old

    def test_payload_persistence(self):
        self.store.memory_block = "note"
        self.store.memory_updated_turn = 5
        self.store.snapshot(_mk_history(2), reason="compaction")
        payload = self.store.to_payload()
        blob = json.dumps(payload)  # must be JSON-serializable
        other = _sm.CheckpointStore()
        other.load_payload(json.loads(blob))
        self.assertEqual(other.memory_block, "note")
        self.assertEqual(other.memory_updated_turn, 5)
        self.assertEqual(len(other.checkpoints), 1)

    def test_load_payload_rejects_garbage(self):
        other = _sm.CheckpointStore()
        other.load_payload("not a dict")
        other.load_payload({"checkpoints": ["nope", {"reason": "ok"}]})
        self.assertEqual(other.checkpoints, [{"reason": "ok"}])


class TestMemoryWriterPrompt(unittest.TestCase):

    def test_memory_writer_prompt_structure(self):
        msgs = _sm.memory_writer_prompt("convo text", "prior note")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Goal / Decisions", msgs[0]["content"])
        self.assertIn("prior note", msgs[1]["content"])
        self.assertIn("convo text", msgs[1]["content"])


if __name__ == "__main__":
    unittest.main()
