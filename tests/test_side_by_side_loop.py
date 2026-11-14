# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Chats side by side through the real turn loop (Threads 3, #359).

The real ``agent_controller`` (exec'd with a stand-in bpy, as in
``test_turn_loop_integration``) against the fake LLM / MCP server:

1. No positional undo while another chat stepped: a failed step's rollback
   would ``ed.undo()`` the newest step -- the other chat's -- so with another
   chat's step since our last push it uses the step's own scoped cleanup.
2. The loop's own undo pushes in a tab are named by its chat.
3. On Remote, two chats' turns run at once (the runner's slots), each to its
   own history; the turn's claims go when it ends.

Run with::

    python -m unittest tests.test_side_by_side_loop -v
"""

__all__ = ()

import json
import os
import sys
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import test_turn_loop_integration as _tli  # noqa: E402

_CoWork = _tli.TestCoWorkUserEditAndScopedUndo


class TestNoPositionalUndoWithAnotherChat(_tli._TurnLoopTestBase):

    _FAIL_MARKER = _CoWork._FAIL_MARKER
    _mk_server = _CoWork._mk_server
    _mcp_codes = _CoWork._mcp_codes
    _run_agent_turn = _CoWork._run_agent_turn

    def _script(self):
        self._mk_server([
            _tli._tool_call_msg("call_1", "bpy.ops.mesh.primitive_cube_add()"),
            _tli._tool_call_msg("call_2", self._FAIL_MARKER + "\nimport bpy"),
            _tli._tool_call_msg("call_3", "bpy.ops.mesh.primitive_uv_sphere_add()"),
            {"content": "recovered"},
        ])
        self.server.snapshot_script = True
        self.server.fail_tool_markers.append(self._FAIL_MARKER)

    def test_scoped_cleanup_when_another_chat_stepped(self):
        self._script()
        real = self.ac._call_mcp_tool_sync
        journal = self.ac._side_by_side.JOURNAL

        def other_chat_steps_meanwhile(name, args, port):
            if self._FAIL_MARKER in str((args or {}).get("code", "")):
                journal.note("t9")          # the Node chat's call ran between ours
            return real(name, args, port)
        self.ac._call_mcp_tool_sync = other_chat_steps_meanwhile
        self.addCleanup(setattr, self.ac, "_call_mcp_tool_sync", real)
        _history, texts = self._run_agent_turn("build the set")
        self.assertIn("recovered", texts)
        codes = self._mcp_codes()
        self.assertEqual([c for c in codes if "bpy.ops.ed.undo()" in c], [],
                         "a positional undo would have taken back the other chat's step")
        self.assertIn("Sphere", self.server.scene_objects, "the turn went on")

    def test_global_undo_still_used_when_alone(self):
        self._script()
        self._run_agent_turn("build the set")
        self.assertTrue([c for c in self._mcp_codes() if "bpy.ops.ed.undo()" in c],
                        "one chat at work: the failure rollback as before")


del _CoWork        # not a test class of this module


class TestTwoChatsOnRemote(_tli._TurnLoopTestBase):

    def setUp(self):
        super().setUp()
        self.node = self.ac.THREADS.create(editor="NODE_EDITOR")
        self.addCleanup(self.ac.THREADS.remove, self.node.id)
        self.addCleanup(setattr, self.ac.THREADS, "kind", self.ac.THREADS.kind)

    def _serve(self, script):
        self.server.shutdown()
        self.server.server_close()
        self.server = _tli._start_fake_server(script, mcp_tools=[_tli._EXECUTE_CODE_TOOL])
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = "local"
        cfg.local_port = self.port
        cfg.local_ctx_size = 8192
        cfg.local_max_tokens = 1024
        cfg.thinking_budget_tokens = 0
        self.lm.set_config(cfg)
        self.lm._runtime_ctx_cache = None

    def test_undo_pushes_named_by_chat(self):
        threads = sys.modules["bfa_coworker.threads"]
        with threads.use(self.node):
            self.assertEqual(self.ac._loop_step_name("bfa_coworker_step"), "Coworker (Node): a step")
            self.assertEqual(self.ac._loop_step_name("bfa_coworker_pre_script"), "Coworker (Node): before a step")
        self.assertEqual(self.ac._loop_step_name("bfa_coworker_step"), "bfa_coworker_step", "All editors: as before")

    def test_both_turns_run_at_once(self):
        self._serve([{"content": "reply"}] * 4)
        self.assertEqual(self.ac.configure_runner("REMOTE_API", 3), 3)
        # The default chat holds a slot (its turn runs), the Node chat still gets one.
        self.assertTrue(self.ac.reserve_turn())
        try:
            self.assertFalse(self.ac.turn_blocked(self.node), "online: a second chat runs at once")
            self._pin_fake_bpy()
            try:
                self.ac.run_conversation_turn("tidy the nodes", chat_mode="AGENT", llm_url=None,
                                              model="fake-model", mcp_port=self.port, thread=self.node)
            finally:
                self._unpin_fake_bpy()
        finally:
            self.ac.THREADS.end(self.ac.THREADS.default)
        said = [m.get("content") for m in self.node.state.conversation_history if m.get("role") == "assistant"]
        self.assertIn("reply", said, "the node chat's turn ran while the default chat held its slot")
        self.assertEqual(self.ac.configure_runner("LOCAL_LLM", 3), 1)
        self.assertTrue(self.ac.reserve_turn())
        try:
            self.assertTrue(self.ac.turn_blocked(self.node), "Local: chained")
        finally:
            self.ac.THREADS.end(self.ac.THREADS.default)

    def test_claims_go_when_the_turn_ends(self):
        self._serve([{"content": "done"}])
        claims = sys.modules["bfa_coworker.claims"]
        claims.TABLE.claim(self.node.id, {("object", "Cube")})
        self._pin_fake_bpy()
        try:
            self.ac.run_conversation_turn("x", chat_mode="AGENT", llm_url=None, model="fake-model",
                                          mcp_port=self.port, thread=self.node)
        finally:
            self._unpin_fake_bpy()
        self.assertEqual(claims.TABLE.claims_of(self.node.id), set())


if __name__ == "__main__":
    unittest.main()
