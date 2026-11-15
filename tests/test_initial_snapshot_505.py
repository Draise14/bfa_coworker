# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The turn's first code step always takes the baseline entity snapshot (#505).

The first ``execute_blender_code`` step of a turn pushes an undo bookmark with
the turn's baseline snapshot.  When that first code looked read-only to
``_code_is_readonly`` (``ob.location.x += 1.0`` has none of its mutation
patterns) the push asked for no snapshot, the turn printed "initial entity
snapshot FAILED" and a later step that made an object was never locked.
Now the first push always asks for it; when there is still none, the reason
is logged once.

Drives the real turn loop against the stub bridge / fake model of
``tests.test_turn_loop_integration`` (no Blender).  The Ask-mode card of the
report is answered on the bridge's side (``risky_steps``), so for the turn loop
an asked step is the same Agent-mode step as here.

Run with::

    python -m unittest tests.test_initial_snapshot_505 -v
"""

__all__ = ()

import contextlib
import io
import json
import unittest

from tests.test_turn_loop_integration import (
    _EXECUTE_CODE_TOOL,
    _LM,
    _SM,
    _TurnLoopTestBase,
    _exec_agent_controller,
    _start_fake_server,
    _tool_call_msg,
)

# The probe's code of #493 phase D: a real edit that _code_is_readonly calls read-only.
_MOVE = ("import bpy\nob = bpy.data.objects.get('Cube')\nob.location.x += 1.0\n"
         "result = {'moved': ob.name, 'x': ob.location.x}")
_MAKE = "import bpy\nbpy.ops.mesh.primitive_uv_sphere_add()\n"


class FirstStepTakesTheBaseline(_TurnLoopTestBase):

    def _turn(self, script):
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
        self.server.snapshot_script = True
        self.server.scene_objects = ["Cube"]
        self.state.conversation_history = [{"role": "system", "content": "You are a helpful agent."}]
        getattr(self.ac, "_NO_SNAPSHOT_LOGGED", set()).clear()
        self.ac.co_work_guard.clear()
        self.ac.co_work_guard.clear_session()
        out = io.StringIO()
        self._pin_fake_bpy()
        try:
            with contextlib.redirect_stdout(out):
                self.ac.run_conversation_turn(
                    "move the cube, then add a sphere", chat_mode="AGENT",
                    llm_url=None, model="fake-model", mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()
        return out.getvalue()

    def _internal(self):
        with self.server.lock:
            calls = [r for r in self.server.mcp_requests if r.get("method") == "tools/call"
                     and (r.get("params") or {}).get("name") == "coworker_internal"]
        return [(c["params"]["arguments"].get("code", ""), c["params"]["arguments"].get("data") or {})
                for c in calls]

    def test_read_only_looking_first_step_still_takes_the_snapshot(self):
        self.assertTrue(self.ac._code_is_readonly(_MOVE), "the case of #505: the heuristic calls it read-only")
        log = self._turn([_tool_call_msg("call_1", _MOVE), _tool_call_msg("call_2", _MAKE),
                          {"content": "moved and added"}])
        internal = [code for code, _data in self._internal()]
        self.assertTrue(internal)
        self.assertIn("'snapshot': {", internal[0], "the turn's first internal call takes the baseline snapshot")
        # #501: a snapshot only -- the loop pushes no undo step of its own.
        self.assertEqual([c for c in internal if "undo_push(" in c], [])
        self.assertIn("initial entity snapshot taken", log)
        self.assertNotIn("FAILED", log)
        self.assertNotIn("no entity snapshot", log)

    def test_the_object_a_later_step_makes_is_locked_in_that_turn(self):
        log = self._turn([_tool_call_msg("call_1", _MOVE), _tool_call_msg("call_2", _MAKE),
                          {"content": "moved and added"}])
        locks = [data for code, data in self._internal() if "hide_select = True" in code]
        self.assertTrue(locks, "a co-work lock call ran in the turn:\n" + log[-2000:])
        self.assertIn("Sphere", locks[0].get("objects", []))
        self.assertNotIn("Cube", locks[0].get("objects", []), "the user's object is not locked")
        self.assertIn("_lock_step_entities: locked 1 objects", log)

    def test_no_snapshot_is_said_once_with_its_reason(self):
        self.server.snapshot_script = None
        self.ac._NO_SNAPSHOT_LOGGED.clear()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for _ in range(3):
                self.ac._note_no_initial_snapshot("the undo push answered without a snapshot")
        lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("(the undo push answered without a snapshot)", lines[0])
        self.assertIn("co-work scene lock is off for this turn", lines[0])
        self.assertNotIn("FAILED", lines[0])


class InitialSnapshotReasons(unittest.TestCase):
    """``_initial_snapshot``: the snapshot, or why there is none."""

    @classmethod
    def setUpClass(cls):
        _TurnLoopTestBase._load_transport()
        cls.ac = _exec_agent_controller(_SM, _LM)

    def test_snapshot(self):
        raw = json.dumps({"status": "ok", "result": {"status": "ok", "snapshot": {
            "object_names": ["Cube"], "active_object": "Cube", "selected_object_names": ["Cube"]}}})
        snap, why = self.ac._initial_snapshot(raw)
        self.assertEqual(why, "")
        self.assertEqual(snap.object_names, {"Cube"})
        self.assertEqual(snap.active_object, "Cube")

    def test_reasons(self):
        cases = {
            "Error calling execute_blender_code: the bridge isn't running": "not JSON",
            json.dumps({"status": "error", "message": "Traceback: boom"}): "answered error: Traceback: boom",
            json.dumps({"status": "ok", "result": {"status": "error",
                                                   "message": "No window/area available for push"}}):
                "the undo push answered: No window/area available for push",
            json.dumps({"status": "ok", "result": {"status": "ok", "message": "push executed"}}):
                "without a snapshot",
            json.dumps(["ok"]): "not an object",
        }
        for raw, expected in cases.items():
            snap, why = self.ac._initial_snapshot(raw)
            self.assertIsNone(snap, raw)
            self.assertIn(expected, why, raw)


if __name__ == "__main__":
    unittest.main()
