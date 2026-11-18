# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
#591: faults of an unattended run, the parts that need no Blender.

1. A render / bake job running: a step that changes the scene is refused with an answer the
   model can act on; looking goes through; Coworker's own undo / removal code is refused too.
2. A render with no progress for ``STUCK_AFTER`` is reported "stuck" by get_job_status.
3. The repeat guard ends a poll loop whose status answers do not change (renders only).
4. A step that timed out may still be running: no undo then.

Run with::

    python -m unittest tests.test_unattended_run_591 -v
"""

__all__ = ()

import importlib
import json
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _pkg():
    name = "bfacw_591_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return name


jobs = importlib.import_module(_pkg() + ".long_jobs")
lg = importlib.import_module(_pkg() + ".loop_guard")


class _Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def _running(*kinds):
    return lambda kind: kind in kinds


class TestSceneBusy(unittest.TestCase):

    def setUp(self):
        jobs.clear()

    def test_nothing_running_lets_everything_through(self):
        self.assertEqual(jobs.scene_busy_message("execute_blender_code", "bpy.ops.mesh.primitive_cube_add()",
                                                 is_running=_running()), "")

    def test_a_scene_change_is_refused_while_a_render_runs(self):
        msg = jobs.scene_busy_message("execute_blender_code", "bpy.ops.mesh.primitive_cube_add()",
                                      is_running=_running("RENDER"))
        self.assertIn("render is running", msg)
        self.assertIn("Nothing was changed", msg)
        self.assertIn("ask the user to stop it with Esc", msg)

    def test_a_job_of_ours_is_waited_for_with_get_job_status(self):
        jobs.start("RENDER", "Render a.png", lambda: None)
        msg = jobs.scene_busy_message("execute_blender_code", "bpy.ops.mesh.primitive_cube_add()",
                                      is_running=_running("RENDER"))
        self.assertIn("get_job_status(job_id)", msg)

    def test_a_bake_counts_too(self):
        self.assertIn("bake is running", jobs.scene_busy_message(
            "assign_material_to_objects", "", is_running=_running("OBJECT_BAKE")))

    def test_looking_goes_through(self):
        running = _running("RENDER")
        self.assertEqual(jobs.scene_busy_message("get_scene_digest", "", is_running=running), "")
        self.assertEqual(jobs.scene_busy_message("get_job_status", "", is_running=running), "")
        self.assertEqual(jobs.scene_busy_message(
            "execute_blender_code", "import bpy\nresult = [o.name for o in bpy.data.objects]",
            is_running=running), "")

    def test_the_message_names_the_job_when_it_is_ours(self):
        job_id = jobs.start("RENDER", "Render a.png", lambda: None)
        msg = jobs.scene_busy_message("execute_blender_code", "bpy.data.objects.remove(o)",
                                      is_running=_running("RENDER"))
        self.assertIn(job_id, msg)

    def test_own_code_is_refused_for_undo_removals_lock_and_selection(self):
        running = _running("RENDER")
        for code in ("import bpy\nbpy.ops.ed.undo()\nbpy.ops.ed.redo()", "bpy.data.objects.remove(o)",
                     "ob.hide_select = True", "ob.select_set(True)", "bpy.ops.ed.undo_push(message='x')",
                     "view_layer.objects.active = ob"):
            self.assertIn("render is running", jobs.scene_busy_message("coworker_internal", code, True, running), code)
        self.assertEqual(jobs.scene_busy_message(
            "coworker_internal", "result = {'snapshot': [o.name for o in bpy.data.objects]}", True, running), "")

    def test_reads_that_write_are_scene_changes_while_a_job_runs(self):
        running = _running("RENDER")
        for tool in ("get_node_tree_as_code", "get_asset_tags", "search_assets", "jump_to_tab_by_name",
                     "jump_to_view3d_object_by_name", "jump_to_anything_new"):
            self.assertIn("render is running", jobs.scene_busy_message(tool, "", False, running), tool)
        for tool in ("get_scene_digest", "get_objects_summary", "get_job_status", "get_node_group_interface",
                     "get_vse_strips_summary", "list_asset_catalogs"):
            self.assertEqual(jobs.scene_busy_message(tool, "", False, running), "", tool)

    def test_every_tool_that_writes_to_the_data_is_listed(self):
        """A read-only-looking tool whose code creates or removes datablocks must be in the list."""
        tools = _ADDON.parents[1] / "mcp" / "blmcp" / "tools"
        for name in ("get_node_tree_as_code", "get_asset_tags", "search_assets"):
            self.assertIn(name, jobs.SCENE_TOUCHING_READS)
        text = (tools / "_node_code_bpy.py").read_text(encoding="utf-8")
        self.assertIn("node_groups.new(", text)       # why get_node_tree_as_code is listed

    def test_outside_blender_nothing_runs(self):
        self.assertEqual(jobs.scene_busy_message("execute_blender_code", "bpy.ops.ed.undo()"), "")


class TestStuck(unittest.TestCase):

    def setUp(self):
        self.clock = _Clock()
        self._saved = jobs._clock
        jobs._clock = self.clock
        jobs.clear()

    def tearDown(self):
        jobs._clock = self._saved
        jobs.clear()

    def test_a_render_with_no_progress_is_stuck(self):
        job_id = jobs.start("RENDER", "Render a.png", lambda: None)
        self.clock.now += jobs.STUCK_AFTER - 1
        self.assertEqual(jobs.status(job_id)["state"], "running")
        self.clock.now += 5
        st = jobs.status(job_id)
        self.assertEqual((st["state"], st["status"]), ("stuck", "error"))
        self.assertGreaterEqual(st["stuck_seconds"], jobs.STUCK_AFTER)
        self.assertIn("stuck", st["message"])
        self.assertIn("Esc", st["message"])
        self.assertIn("no progress", jobs.running()[0]["text"])

    def test_progress_keeps_it_running(self):
        job_id = jobs.start("RENDER", "Render a.png", lambda: None)
        for n in range(1, 8):
            self.clock.now += jobs.STUCK_AFTER / 2
            jobs.note_progress("RENDER", "Sample {:d}/64".format(n))
            self.assertEqual(jobs.status(job_id)["state"], "running")

    def test_the_same_status_line_again_is_no_progress(self):
        job_id = jobs.start("RENDER", "Render a.png", lambda: None)
        for _ in range(6):
            self.clock.now += jobs.STUCK_AFTER / 3
            jobs.note_progress("RENDER", "Sample 3/64")
        self.assertEqual(jobs.status(job_id)["state"], "stuck")

    def test_a_bake_is_never_stuck(self):
        job_id = jobs.start("OBJECT_BAKE", "Bake", lambda: None)
        self.clock.now += jobs.STUCK_AFTER * 5
        self.assertEqual(jobs.status(job_id)["state"], "running")

    def test_a_stuck_job_still_ends_when_it_does(self):
        result = [None]
        job_id = jobs.start("RENDER", "Render a.png", lambda: result[0])
        self.clock.now += jobs.STUCK_AFTER + 10
        result[0] = {"status": "ok", "filepath": "a.png"}
        jobs.poll()
        self.assertEqual(jobs.status(job_id)["state"], "done")


def _answer(state="running", kind="RENDER", pct=None, text=None):
    inner = {"status": "ok", "job_id": "job-x-1", "state": state, "kind": kind, "elapsed_seconds": 5}
    if pct is not None:
        inner["progress_percent"] = pct
    if text:
        inner["progress_text"] = text
    return json.dumps({"status": "ok", "result": inner})


class TestStalePolls(unittest.TestCase):
    """Only polls that already answered "stuck" count (a quiet but running render is never refused)."""

    def _poll(self, guard, answer):
        args = {"job_id": "job-x-1"}
        verdict, _n = guard.check("get_job_status", args)
        if verdict == lg.RUN:
            guard.note_result("get_job_status", args, answer)
        return verdict

    def test_a_quiet_but_running_render_is_never_refused(self):
        guard = lg.LoopGuard()
        for _ in range(10 * lg.STALE_POLLS):
            self.assertEqual(self._poll(guard, _answer(pct=0)), lg.RUN)

    def test_polling_on_after_stuck_is_refused(self):
        guard = lg.LoopGuard()
        verdicts = [self._poll(guard, _answer(state="stuck", pct=0)) for _ in range(lg.STALE_POLLS + 2)]
        self.assertEqual(verdicts[:lg.STALE_POLLS], [lg.RUN] * lg.STALE_POLLS)
        self.assertEqual(verdicts[lg.STALE_POLLS], lg.REFUSE)

    def test_progress_after_stuck_clears_the_count(self):
        guard = lg.LoopGuard()
        for _ in range(lg.STALE_POLLS - 1):
            self._poll(guard, _answer(state="stuck"))
        self._poll(guard, _answer(pct=5, text="Sample 5/99"))
        self.assertEqual(guard.stale_polls({"job_id": "job-x-1"}), 0)

    def test_a_bake_may_be_polled_for_long(self):
        guard = lg.LoopGuard()
        for _ in range(3 * lg.STALE_POLLS):
            self.assertEqual(self._poll(guard, _answer(state="stuck", kind="OBJECT_BAKE")), lg.RUN)

    def test_the_refusal_tells_the_model_what_to_do(self):
        text = lg.refused("get_job_status", lg.STALE_POLLS)["message"]
        self.assertIn("stuck", text)
        self.assertIn("Esc", text)

    def test_the_bridge_reads_the_job_id_from_the_code(self):
        guard = lg.LoopGuard()
        code = {"code": "main(Params(job_id='job-x-1', wait_seconds=25.0))"}
        for _ in range(lg.STALE_POLLS):
            self.assertEqual(guard.check("get_job_status", code, "s")[0], lg.RUN)
            lg.bridge_watch(guard, "get_job_status", code, "s", "", json.loads(_answer(state="stuck")), None)
        self.assertEqual(guard.check("get_job_status", code, "s")[0], lg.REFUSE)


class TestBridgeChecksTwice(unittest.TestCase):
    """E: the busy check runs before a card is shown and again when the step is finally run."""

    def test_the_check_is_in_the_handler_and_in_execute(self):
        text = (_ADDON / "mcp_to_blender_server.py").read_text(encoding="utf-8")
        start = text.index("def _execute(c: str, s: bool)")
        self.assertIn("_busy_refusal(", text[start:start + 400], "repeated when the delayed run starts")
        self.assertIn("_early = _busy_refusal(", text)
        self.assertLess(text.index("_early = _busy_refusal("), text.index("# Runtime 1 (#310): the same call again"),
                        "before any card or claim wait")

    def test_the_answer_of_the_check(self):
        import unittest.mock as mock
        mbs = importlib.import_module(_pkg() + ".mcp_to_blender_server")
        with mock.patch.object(jobs, "busy_kind", lambda *a, **k: "RENDER"):
            refused = mbs._busy_refusal("execute_blender_code", "bpy.ops.mesh.primitive_cube_add()", False)
            self.assertEqual(refused.response["status"], "error")
            self.assertTrue(refused.response["message"].startswith("Not run:"))
            self.assertIsNone(mbs._busy_refusal("get_scene_digest", "", False))
        with mock.patch.object(jobs, "busy_kind", lambda *a, **k: ""):
            self.assertIsNone(mbs._busy_refusal("execute_blender_code", "bpy.ops.mesh.primitive_cube_add()", False))


class TestNoLiftWhileBusy(unittest.TestCase):
    """B1: the sandbox does not toggle ``hide_select`` around a bridge call while a job runs."""

    def _setup(self, running):
        import unittest.mock as mock
        cg = importlib.import_module(_pkg() + ".co_work_guard")
        ws = importlib.import_module(_pkg() + ".weak_sandbox")
        cg.clear()

        class _Block:
            hide_select = True
        data = types.SimpleNamespace(objects={"A": _Block()}, collections={})
        cg.record_managed(("A",), ())
        cg.record_prior({"A": False}, {})
        fake = types.ModuleType("bpy")
        fake.data = data
        fake.app = types.SimpleNamespace(is_job_running=lambda kind: kind in running)
        patcher = mock.patch.dict(sys.modules, {"bpy": fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cg.clear)
        return ws, data

    def test_lifts_when_nothing_runs(self):
        ws, data = self._setup(running=())
        held = ws._co_work_lift()
        self.assertTrue(held)
        self.assertFalse(data.objects["A"].hide_select)

    def test_does_nothing_while_a_render_or_bake_runs(self):
        for kind in ("RENDER", "OBJECT_BAKE"):
            ws, data = self._setup(running=(kind,))
            self.assertIsNone(ws._co_work_lift())
            self.assertTrue(data.objects["A"].hide_select, "the lock stays as it is")


class TestUndoPushWaits(unittest.TestCase):
    """B3: the turn's undo push is held back while a job runs, and pushed afterwards."""

    def test_push_is_deferred_then_made(self):
        import unittest.mock as mock
        api = importlib.import_module(_pkg() + ".coworker_api")
        pushed = []
        state = {"busy": "RENDER"}
        with mock.patch.object(jobs, "busy_kind", lambda *a, **k: state["busy"]), \
                mock.patch.object(api, "_undo", lambda name: pushed.append(name) or True):
            self.assertFalse(api._push("Coworker's turn"))
            self.assertEqual(pushed, [])
            self.assertEqual(api._push_when_idle(), 1.0, "still running: try again in a second")
            state["busy"] = ""
            self.assertIsNone(api._push_when_idle())
            self.assertEqual(pushed, ["Coworker's turn"])


class TestReadyWaitWithProcess(unittest.TestCase):
    """G and #591 item 4: Stop and a dead process end the extended wait, and it is quiet."""

    def _llm(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("llm_manager_591b", _ADDON / "llm_manager.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["llm_manager_591b"] = mod
        spec.loader.exec_module(mod)
        mod._abandon_launched_server = lambda proc: None
        return mod

    def _run(self, llm, proc, stop_at=None, die_at=None):
        import io
        import contextlib
        import unittest.mock as mock
        clock = [0.0]

        class _T:
            monotonic = staticmethod(lambda: clock[0])

            @staticmethod
            def sleep(sec):
                clock[0] += sec
        stopped = lambda p: stop_at is not None and clock[0] >= stop_at  # noqa: E731
        if die_at is not None:
            proc.poll = lambda: 1 if clock[0] >= die_at else None
            proc.returncode = 1
        out = io.StringIO()
        with mock.patch.object(llm, "health_check", lambda *a, **k: False), \
                mock.patch.object(llm, "stopped_by_user", stopped), \
                mock.patch.object(llm, "get_llama_server_log_tail", lambda *a, **k: "load_tensors: loading"), \
                mock.patch.dict(sys.modules, {"time": _T}), contextlib.redirect_stdout(out):
            return llm.wait_until_ready(timeout=300.0, proc=proc), clock[0], out.getvalue()

    def test_stop_during_the_extended_wait_ends_it_without_an_error(self):
        llm = self._llm()
        alive = types.SimpleNamespace(poll=lambda: None, returncode=None)
        ok, at, _log = self._run(llm, alive, stop_at=700.0)
        self.assertFalse(ok)
        self.assertLess(at, 760.0)
        self.assertEqual(llm.get_state().error, "")

    def test_a_process_that_dies_during_the_extended_wait_ends_it_with_the_exit_error(self):
        llm = self._llm()
        proc = types.SimpleNamespace(poll=lambda: None, returncode=1, pid=1)
        ok, at, _log = self._run(llm, proc, die_at=800.0)
        self.assertFalse(ok)
        self.assertLess(at, 860.0)
        self.assertIn("exited during startup", llm.get_state().error)

    def test_the_wait_logs_every_thirty_seconds_only(self):
        llm = self._llm()
        alive = types.SimpleNamespace(poll=lambda: None, returncode=None)
        _ok, _at, log = self._run(llm, alive, stop_at=400.0)
        lines = [ln for ln in log.splitlines() if "still loading" in ln]
        self.assertTrue(0 < len(lines) <= 14, len(lines))
        self.assertNotIn("health_check", log)


class TestStatusResultType(unittest.TestCase):
    """The tool's Result must take every key ``long_jobs.status`` can give (a missing one was a TypeError)."""

    def test_result_takes_all_status_keys(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "mcp" / "blmcp" / "tools" / "get_job_status_toolcode.py"
        spec = importlib.util.spec_from_file_location("gjs_591", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        clock = _Clock()
        saved, jobs._clock = jobs._clock, clock
        try:
            jobs.clear()
            job_id = jobs.start("RENDER", "r", lambda: None)
            jobs.note_progress("RENDER", "Sample 1/9")
            clock.now += jobs.STUCK_AFTER + 5
            for st in (jobs.status(job_id), jobs.status("nope")):
                mod.Result(**st)
            self.assertEqual(jobs.status(job_id)["state"], "stuck")
        finally:
            jobs._clock = saved
            jobs.clear()


class TestTimedOutStep(unittest.TestCase):

    def test_a_timeout_may_still_run(self):
        text = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
        start = text.index("def _step_may_still_run")
        end = text.index("def _error_is_code_bug")
        ns = {}
        exec(text[start:end], ns)  # the helper is pure
        fn = ns["_step_may_still_run"]
        self.assertTrue(fn("Error executing tool execute_blender_code: Blender connection timed out at "
                           "localhost:9876 after 300 s; Blender may still be busy."))
        self.assertTrue(fn('{"status": "error", "message": "[Preflight] Code execution timed out after 30 seconds."}'))
        self.assertFalse(fn('{"status": "error", "message": "IndexError: tuple index out of range"}'))
        self.assertIn("_step_may_still_run(_prev_code_error)", text)


class TestServerReadyWait(unittest.TestCase):
    """#591: past its limit, a llama-server process that is still alive keeps being waited for."""

    def _llm(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("llm_manager_591", _ADDON / "llm_manager.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["llm_manager_591"] = mod
        spec.loader.exec_module(mod)
        return mod

    def _run(self, llm, proc, ready_at):
        import time as _real_time
        from unittest import mock
        clock = [0.0]

        class _T:
            monotonic = staticmethod(lambda: clock[0])

            @staticmethod
            def sleep(sec):
                clock[0] += sec
        health = lambda *a, **k: clock[0] >= ready_at  # noqa: E731
        with mock.patch.object(llm, "health_check", health), mock.patch.object(llm, "stopped_by_user", lambda p: False),                 mock.patch.object(llm, "get_llama_server_log_tail", lambda *a, **k: "load_tensors: loading"),                 mock.patch.dict(sys.modules, {"time": _T}):
            return llm.wait_until_ready(timeout=300.0, proc=proc), clock[0]

    def test_a_slow_load_is_waited_for(self):
        llm = self._llm()
        alive = types.SimpleNamespace(poll=lambda: None, returncode=None)
        ok, at = self._run(llm, alive, ready_at=700.0)
        self.assertTrue(ok)
        self.assertGreaterEqual(at, 700.0)

    def test_a_load_that_never_ends_stops_at_the_cap(self):
        llm = self._llm()
        llm._abandon_launched_server = lambda proc: None
        alive = types.SimpleNamespace(poll=lambda: None, returncode=None)
        ok, at = self._run(llm, alive, ready_at=1e9)
        self.assertFalse(ok)
        self.assertLess(at, llm._READY_HARD_CAP + 60)
        self.assertIn("still running", llm.get_state().error)

    def test_without_a_process_the_limit_is_the_limit(self):
        llm = self._llm()
        llm._abandon_launched_server = lambda proc: None
        ok, at = self._run(llm, None, ready_at=1e9)
        self.assertFalse(ok)
        self.assertLess(at, 400.0)


if __name__ == "__main__":
    unittest.main()
