# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for long jobs in the background (Tier 4, task "Tools 6", #309).

1. The job registry (``long_jobs``): running -> done / error / cancelled,
   timeouts, progress from render status lines, pruning, and a fake
   ten-minute bake on a patched clock that never times out.
2. The local loop's sliced wait (``wait_in_slices``): waits as long as asked,
   in short calls, and stops early on Stop.
3. The bridge hand-off (``deferred_tool``): a render still running after
   ``_HANDOFF_AFTER`` answers with a job id and goes on as a job; other
   deferred replies (ask_user, approvals) are never handed off.
4. ``get_job_status`` tool-code: answers at once, or waits with a checker.
5. The activity view (#423): a deferred call's entry ends done / error when
   its answer goes out (or the client hangs up); a call that started a job
   (``bake_textures``, a handed-off render) stays "running" until the job
   ends done, error or cancelled.

No Blender needed.  Run with::

    python -m unittest tests.test_long_jobs -v
"""

__all__ = ()

import importlib
import json
import socket
import sys
import threading
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_TOOLS = _REPO / "mcp" / "blmcp" / "tools"


def _pkg() -> str:
    name = "bfacw_jobs_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return name


jobs = importlib.import_module(_pkg() + ".long_jobs")


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _JobsTest(unittest.TestCase):

    def setUp(self):
        self.clock = _Clock()
        self._saved_clock = jobs._clock
        jobs._clock = self.clock
        jobs.clear()

    def tearDown(self):
        jobs._clock = self._saved_clock
        jobs.clear()


class TestRegistry(_JobsTest):

    def test_running_then_done(self):
        result = [None]
        finished = []
        job_id = jobs.start("RENDER", "Render a.png", lambda: result[0], on_finish=lambda: finished.append(1))
        self.assertFalse(jobs.poll())
        st = jobs.status(job_id)
        self.assertEqual((st["status"], st["state"], st["kind"]), ("ok", "running", "RENDER"))
        self.assertIn("get_job_status", st["message"])
        self.assertTrue(jobs.has_running())
        self.clock.now += 12
        result[0] = {"status": "ok", "filepath": "/tmp/a.png"}
        self.assertTrue(jobs.poll())
        st = jobs.status(job_id)
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["result"], {"filepath": "/tmp/a.png"})
        self.assertEqual((st["elapsed_seconds"], st["progress_percent"]), (12, 100))
        self.assertFalse(jobs.has_running())
        jobs.poll()
        self.assertEqual(finished, [1])                     # once, however often it's polled

    def test_error_and_cancelled_results(self):
        bad = jobs.start("OBJECT_BAKE", "bake", lambda: {"status": "error", "message": "no image"})
        gone = jobs.start("OBJECT_BAKE", "bake", lambda: {"status": "cancelled"})
        jobs.poll()
        self.assertEqual(jobs.status(bad)["status"], "error")
        self.assertEqual(jobs.status(bad)["message"], "no image")
        self.assertEqual((jobs.status(gone)["status"], jobs.status(gone)["state"]), ("ok", "cancelled"))

    def test_checker_raising_is_an_error(self):
        def boom():
            raise RuntimeError("checker broke")
        job_id = jobs.start("RENDER", "r", boom)
        self.assertTrue(jobs.poll())
        self.assertEqual(jobs.status(job_id)["state"], "error")
        self.assertIn("checker broke", jobs.status(job_id)["message"])

    def test_blender_cancel_handler(self):
        finished = []
        bake = jobs.start("OBJECT_BAKE", "bake", lambda: None, on_finish=lambda: finished.append("bake"))
        render = jobs.start("RENDER", "render", lambda: None)
        jobs.cancelled("OBJECT_BAKE")
        # The handler may run on the job's thread: it only marks the job ...
        self.assertEqual(jobs.status(bake)["state"], "running")
        self.assertEqual(finished, [])
        # ... and the next poll (the bridge timer, main thread) ends it and restores.
        self.assertTrue(jobs.poll())
        self.assertEqual(jobs.status(bake)["state"], "cancelled")
        self.assertEqual(jobs.status(render)["state"], "running")   # only that kind
        self.assertEqual(finished, ["bake"])

    def test_cancel_from_another_thread_restores_on_the_polling_thread(self):
        import threading
        ran_on = []
        jobs.start("RENDER", "render", lambda: None, on_finish=lambda: ran_on.append(threading.get_ident()))
        worker = threading.Thread(target=jobs.cancelled, args=("RENDER",))
        worker.start()
        worker.join()
        self.assertEqual(ran_on, [])
        jobs.poll()
        self.assertEqual(ran_on, [threading.get_ident()])

    def test_file_load_ends_jobs_without_restoring(self):
        finished = []
        job_id = jobs.start("OBJECT_BAKE", "bake", lambda: None, on_finish=lambda: finished.append(1))
        jobs.file_loading()
        st = jobs.status(job_id)
        self.assertEqual(st["state"], "cancelled")
        self.assertIn("another file was loaded", st["message"])
        self.assertEqual(finished, [])             # no restore onto the old file's data
        jobs.poll()
        self.assertEqual(finished, [])

    def test_job_ids_differ_between_sessions(self):
        first = jobs.start("RENDER", "r", lambda: None)
        self.assertRegex(first, r"^job-[0-9a-f]{6}-\d+$")
        old_prefix = jobs._ID_PREFIX
        jobs._ID_PREFIX = "0a0a0a"              # as if Blender restarted
        try:
            self.assertEqual(jobs.status(first.replace(old_prefix, "0a0a0a"))["state"], "unknown")
        finally:
            jobs._ID_PREFIX = old_prefix

    def test_timeout(self):
        finished = []
        job_id = jobs.start("RENDER", "r", lambda: None, on_finish=lambda: finished.append(1), timeout=60)
        self.clock.now += 59
        jobs.poll()
        self.assertEqual(jobs.status(job_id)["state"], "running")
        self.clock.now += 2
        self.assertTrue(jobs.poll())
        st = jobs.status(job_id)
        self.assertEqual((st["status"], st["state"]), ("error", "error"))
        self.assertIn("stopped waiting", st["message"])
        self.assertEqual(finished, [1])

    def test_ten_minute_bake_finishes_without_a_timeout(self):
        """A fake 10-minute bake, polled every 30 s like get_job_status would."""
        started = self.clock.now
        job_id = jobs.start("OBJECT_BAKE", "Bake diffuse: Cube",
                            lambda: {"status": "ok", "images": ["Bake"]}
                            if self.clock.now - started >= 600 else None)
        states = []
        while self.clock.now - started < 660:
            self.clock.now += 30
            jobs.poll()
            states.append(jobs.status(job_id)["state"])
        self.assertEqual(states[:19], ["running"] * 19)
        self.assertEqual(states[19], "done")
        st = jobs.status(job_id)
        self.assertEqual((st["elapsed_seconds"], st["result"]), (600, {"images": ["Bake"]}))
        self.assertEqual(jobs.row_text(jobs._jobs[job_id]), "Baking: done after 600 s")

    def test_unknown_job_is_actionable(self):
        jobs.start("RENDER", "Render a.png", lambda: None)
        st = jobs.status("job999")
        self.assertEqual((st["status"], st["state"]), ("error", "unknown"))
        self.assertIn("Render a.png", st["message"])        # lists the known jobs
        self.assertIn("render_viewport_to_path(background=True)", st["message"])

    def test_finished_jobs_are_pruned(self):
        ids = [jobs.start("RENDER", str(i), lambda: {"status": "ok"}) for i in range(30)]
        for _ in ids:
            self.clock.now += 1
            jobs.poll()
            jobs.start("RENDER", "x", lambda: {"status": "ok"})
        self.assertLessEqual(len(jobs._jobs), jobs._KEEP_FINISHED + 1)


class TestProgress(_JobsTest):

    def test_parse_render_status_lines(self):
        self.assertAlmostEqual(jobs.parse_progress("Sample 32/128"), 0.25)                 # Cycles
        self.assertAlmostEqual(jobs.parse_progress("Remaining: 00:10 | Mem: 90M | Sample 64/128"), 0.5)
        self.assertAlmostEqual(jobs.parse_progress("Rendering 16 / 64 samples"), 0.25)     # EEVEE
        self.assertAlmostEqual(jobs.parse_progress("Rendered 1/4 Tiles, Sample 64/128"), (1 + 0.5) / 4)
        self.assertIsNone(jobs.parse_progress("Sample 12"))                               # no total
        self.assertIsNone(jobs.parse_progress("Synchronizing object | Cube"))
        self.assertIsNone(jobs.parse_progress(""))

    def test_progress_reaches_status_and_row(self):
        render = jobs.start("RENDER", "Render a.png", lambda: None)
        bake = jobs.start("OBJECT_BAKE", "bake", lambda: None)
        jobs.note_progress("RENDER", "Sample 40/100")
        st = jobs.status(render)
        self.assertEqual((st["progress_percent"], st["progress_text"]), (40, "Sample 40/100"))
        self.assertNotIn("progress_percent", jobs.status(bake))   # bakes report no percentage
        self.clock.now += 35
        rows = {j["id"]: j["text"] for j in jobs.running()}
        self.assertEqual(rows, {render: "Rendering 40%", bake: "Baking, 35 s"})


class TestLoopHelpers(_JobsTest):

    def test_status_polls(self):
        self.assertTrue(jobs.is_status_poll("get_job_status"))
        self.assertFalse(jobs.is_status_poll("get_scene_digest"))
        running = json.dumps({"status": "ok", "result": {"job_id": "job1", "state": "running"}})
        done = json.dumps({"status": "ok", "result": {"job_id": "job1", "state": "done"}})
        self.assertTrue(jobs.poll_is_waiting("get_job_status", running))
        self.assertFalse(jobs.poll_is_waiting("get_job_status", done))
        self.assertFalse(jobs.poll_is_waiting("render_viewport_to_path", running))

    def test_call_wait_seconds(self):
        self.assertEqual(jobs.call_wait_seconds("get_job_status", {"job_id": "j"}), 25.0)
        self.assertEqual(jobs.call_wait_seconds("get_job_status", {"wait_seconds": 9999}), 240.0)
        self.assertEqual(jobs.call_wait_seconds("get_job_status", {"wait_seconds": "x"}), 0.0)
        self.assertEqual(jobs.call_wait_seconds("get_job_status", {"wait_seconds": -5}), 0.0)
        self.assertEqual(jobs.call_wait_seconds("execute_blender_code", {"wait_seconds": 30}), 0.0)

    def _fake_call(self, answers):
        sent = []

        def call(args):
            sent.append(args["wait_seconds"])
            self.clock.now += args["wait_seconds"]       # the bridge held it that long
            return json.dumps({"status": "ok", "result": {"state": answers.pop(0)}})
        return call, sent

    def test_wait_in_slices_until_done(self):
        call, sent = self._fake_call(["running", "running", "done", "running"])
        text = jobs.wait_in_slices(call, "get_job_status", {"job_id": "job1", "wait_seconds": 200})
        self.assertIn('"done"', text)
        self.assertEqual(sent, [15.0, 15.0, 15.0])           # short calls, Stop is noticed between

    def test_wait_in_slices_honours_the_wait(self):
        call, sent = self._fake_call(["running"] * 10)
        text = jobs.wait_in_slices(call, "get_job_status", {"job_id": "job1", "wait_seconds": 40})
        self.assertIn('"running"', text)
        self.assertEqual(sent, [15.0, 15.0, 10.0])

    def test_wait_in_slices_stops(self):
        stop = threading.Event()
        stop.set()
        call, sent = self._fake_call(["running"] * 10)
        jobs.wait_in_slices(call, "get_job_status", {"job_id": "job1", "wait_seconds": 240}, stop)
        self.assertEqual(sent, [15.0])


class TestBridgeHandoff(_JobsTest):
    """deferred_tool: a long render answers with a job id instead of timing out."""

    @classmethod
    def setUpClass(cls):
        cls.deferred = importlib.import_module(_pkg() + ".deferred_tool")

    def setUp(self):
        super().setUp()
        self.deferred._deferred_clients.clear()
        self.server_end, self.client_end = socket.socketpair()
        self.server_end.setblocking(False)
        self.client_end.settimeout(2.0)

    def tearDown(self):
        self.deferred.close_all()
        self.client_end.close()
        super().tearDown()

    def _reply(self):
        buf = b""
        while not buf.endswith(b"\0"):
            buf += self.client_end.recv(65536)
        return json.loads(buf[:-1].decode("utf-8"))

    def test_render_is_handed_off_then_finishes_as_a_job(self):
        result = [None]

        def check_is_finished():
            return result[0]
        check_is_finished.job_kind = "RENDER"
        check_is_finished.job_label = "Render big.png"
        restored = []
        check_is_finished.job_on_finish = lambda: restored.append(1)
        self.deferred.add(self.server_end, check_is_finished, True, "", "")
        self.deferred.poll()
        self.assertTrue(self.deferred.has_pending())        # a short render answers as before
        self.deferred._deferred_clients[0].handoff_at = 0.0  # 45 s later
        self.assertTrue(self.deferred.poll())
        reply = self._reply()
        self.assertEqual(reply["status"], "ok")
        self.assertEqual(reply["result"]["state"], "running")
        job_id = reply["result"]["job_id"]
        self.assertFalse(self.deferred.has_pending())
        self.assertEqual(jobs.status(job_id)["label"], "Render big.png")
        result[0] = {"status": "ok", "filepath": "/tmp/big.png"}
        jobs.poll()
        self.assertEqual(jobs.status(job_id)["result"], {"filepath": "/tmp/big.png"})
        self.assertEqual(restored, [1])

    def test_other_deferred_replies_are_not_handed_off(self):
        self.deferred.add(self.server_end, lambda: None, True, "", "")     # e.g. ask_user waiting
        self.deferred._deferred_clients[0].handoff_at = 0.0
        self.deferred.poll()
        self.assertTrue(self.deferred.has_pending())
        self.assertFalse(jobs.has_running())


class TestActivityEntriesFinish(_JobsTest):
    """#423: long-job calls no longer stay "waiting for Blender" in the activity view."""

    @classmethod
    def setUpClass(cls):
        cls.deferred = importlib.import_module(_pkg() + ".deferred_tool")
        cls.activity = importlib.import_module(_pkg() + ".activity")
        cls.api = importlib.import_module(_pkg() + ".coworker_api")

    def setUp(self):
        super().setUp()
        self.activity.clear()
        self.deferred._deferred_clients.clear()
        self.server_end, self.client_end = socket.socketpair()
        self.server_end.setblocking(False)
        self.client_end.settimeout(2.0)

    def tearDown(self):
        self.deferred.close_all()
        self.client_end.close()
        self.activity.clear()
        super().tearDown()

    def _deferred_call(self, tool, check):
        """What the bridge does with a deferred call: record it, then hand it to deferred_tool."""
        entry = self.activity.record(tool, {"job_id": "x"}, {}, 0.01, deferred=True)
        bridge_end, client_end = socket.socketpair()      # one connection per call
        bridge_end.setblocking(False)
        self.addCleanup(client_end.close)
        self.deferred.add(bridge_end, check, True, "", "",
                          on_answer=lambda response: self.activity.answered(entry, response))
        return entry

    def test_deferred_call_ends_as_done(self):
        answer = [None]
        entry = self._deferred_call("get_job_status", lambda: answer[0])
        self.deferred.poll()
        self.assertEqual(entry.status, "waiting")
        answer[0] = {"status": "ok", "job_id": "job-1", "state": "done", "message": "Baked COMBINED into Bake"}
        self.deferred.poll()
        self.assertEqual(entry.status, "ok")                       # a status poll's answer is complete
        self.assertIn("Baked COMBINED", entry.preview)
        self.assertNotIn("waiting", self.activity.log_text().lower())

    def test_deferred_error_and_hang_up(self):
        entry = self._deferred_call("render_viewport_to_path", lambda: {"status": "error", "message": "No camera"})
        self.deferred.poll()
        self.assertEqual((entry.status, entry.preview), ("error", "No camera"))
        bridge_end, client_end = socket.socketpair()
        bridge_end.setblocking(False)
        other = self.activity.record("get_job_status", {}, {}, 0.0, deferred=True)
        self.deferred.add(bridge_end, lambda: None, True, "", "",
                          on_answer=lambda response: self.activity.answered(other, response))
        client_end.close()                                          # the client went away
        self.deferred.poll()
        self.assertEqual(other.status, "error")

    def test_handed_off_render_follows_its_job(self):
        result = [None]

        def check_is_finished():
            return result[0]
        check_is_finished.job_kind = "RENDER"
        check_is_finished.job_label = "Render big.png"
        entry = self._deferred_call("render_viewport_to_path", check_is_finished)
        self.deferred._deferred_clients[0].handoff_at = 0.0
        self.deferred.poll()                                        # answered with a job id
        self.assertEqual(entry.status, "running")
        self.assertTrue(entry.job_id)
        self.assertTrue(self.activity.following())
        self.api.jobs_tick()
        self.assertEqual(entry.status, "running")
        result[0] = {"status": "ok", "filepath": "/tmp/big.png"}
        self.api.jobs_tick()                                        # the bridge's timer
        self.assertEqual(entry.status, "ok")
        self.assertIn("big.png", entry.preview)
        self.assertFalse(self.activity.following())

    def test_step_that_asked_first_follows_its_bake(self):
        # bake_textures asks first (risky_steps): the deferred answer carries the call's whole answer.
        job_id = jobs.start("OBJECT_BAKE", "Bake combined: BakeMonkey", lambda: None)
        inner = {"status": "ok", "result": {"status": "ok", "job_id": job_id, "message": "Baking in the background."}}
        entry = self._deferred_call("bake_textures", lambda: inner)
        self.deferred.poll()
        self.assertEqual((entry.status, entry.job_id), ("running", job_id))
        declined = self._deferred_call("bake_textures", lambda: {"status": "error", "message": "The user declined"})
        self.deferred.poll()
        self.assertEqual((declined.status, declined.preview), ("error", "The user declined"))

    def test_bake_call_ends_when_its_job_ends(self):
        # bake_textures answers at once with a job id (not deferred).
        job_id = jobs.start("OBJECT_BAKE", "Bake combined: Monkey", lambda: None)
        bake = self.activity.record("bake_textures", {"bake_type": "COMBINED"}, {"status": "ok", "result": {
            "status": "ok", "job_id": job_id, "message": "Baking in the background."}}, 0.3)
        poll = self.activity.record("get_job_status", {"job_id": job_id}, {"status": "ok", "result": {
            "status": "ok", "job_id": job_id, "state": "running"}}, 25.0)
        self.assertEqual((bake.status, poll.status), ("running", "ok"))
        jobs.cancelled("OBJECT_BAKE")                               # Esc in Blender
        self.api.jobs_tick()
        self.assertEqual(bake.status, "cancelled")
        self.assertIn("Cancelled", bake.preview)
        # A file load ends jobs outside poll(); the entry still ends.
        job2 = jobs.start("OBJECT_BAKE", "Bake ao: Monkey", lambda: None)
        bake2 = self.activity.record("bake_textures", {}, {"status": "ok", "result": {"status": "ok", "job_id": job2}},
                                     0.1)
        jobs.file_loading()
        self.api.jobs_tick()
        self.assertEqual(bake2.status, "cancelled")


class TestGetJobStatusToolcode(_JobsTest):
    """The tool-code the MCP server sends, run against stand-ins for bpy and the add-on."""

    def _load(self, background=False):
        api = types.SimpleNamespace(job_status=jobs.status)
        bpy = types.ModuleType("bpy")
        bpy.app = types.SimpleNamespace(background=background)
        namespace = {"__name__": "toolcode"}
        source = (_TOOLS / "get_job_status_toolcode.py").read_text(encoding="utf-8")
        exec(compile(source, "get_job_status_toolcode.py", "exec"), namespace)  # pylint: disable=exec-used
        namespace["_coworker_api"] = lambda: api
        saved = sys.modules.get("bpy")
        sys.modules["bpy"] = bpy
        self.addCleanup(lambda: sys.modules.__setitem__("bpy", saved) if saved else sys.modules.pop("bpy", None))
        return namespace

    def test_answers_at_once_when_done_or_not_waiting(self):
        ns = self._load()
        done = jobs.start("RENDER", "r", lambda: {"status": "ok"})
        jobs.poll()
        out = ns["main"](ns["Params"](job_id=done, wait_seconds=60))._asdict()
        self.assertEqual(out["state"], "done")
        running = jobs.start("RENDER", "r", lambda: None)
        out = ns["main"](ns["Params"](job_id=running, wait_seconds=0))._asdict()
        self.assertEqual(out["state"], "running")
        out = ns["main"](ns["Params"](job_id="nope", wait_seconds=60))._asdict()
        self.assertEqual((out["status"], out["state"]), ("error", "unknown"))

    def test_waits_with_a_checker(self):
        ns = self._load()
        result = [None]
        job_id = jobs.start("OBJECT_BAKE", "bake", lambda: result[0])
        check = ns["main"](ns["Params"](job_id=job_id, wait_seconds=30))
        self.assertTrue(callable(check))
        self.assertIsNone(check())                           # still baking, still waiting
        result[0] = {"status": "ok", "images": ["Bake"]}
        jobs.poll()
        self.assertEqual(check()["state"], "done")

    def test_background_mode_never_defers(self):
        ns = self._load(background=True)
        job_id = jobs.start("RENDER", "r", lambda: None)
        out = ns["main"](ns["Params"](job_id=job_id, wait_seconds=60))
        self.assertFalse(callable(out))


if __name__ == "__main__":
    unittest.main()
