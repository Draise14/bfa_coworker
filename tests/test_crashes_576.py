# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Two crashes from use (#576): Paste Image, and agent code running during a slider drag.

1. The agent's code of a step is on disk before it runs (``step_code_log``): redacted like
   ``operations.jsonl``, bounded, marked RUNNING until the step returns.
2. A scene-changing bridge step waits while the user drags a slider or a modal tool runs
   (``ui_activity`` + ``scene_busy`` + the bridge's ``_busy_gate``), a bounded time, then answers clearly;
   reads and Coworker's own bookkeeping never wait.
3. Paste Image does not switch an area under a drag, and a restore that finds its image gone
   does not raise.

Fake window manager and ``bpy``; no Blender.

Run with::

    python -m unittest tests.test_crashes_576 -v
"""

__all__ = ()

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_crashes576_pkg"


def _load(module):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + module)


log = _load("log")
scl = _load("step_code_log")
ua = _load("ui_activity")
br = _load("mcp_to_blender_server")
att = _load("chat_attachments")
lj = _load("long_jobs")
sb = _load("scene_busy")


WRITE = "import bpy\nbpy.data.objects.new('x', None)"


def _op(ident, pointer=1):
    return types.SimpleNamespace(bl_idname=ident, as_pointer=lambda: pointer)


def _wm(modal=(), playing=False):
    window = types.SimpleNamespace(
        modal_operators=[_op(i, n + 1) for n, i in enumerate(modal)],
        screen=types.SimpleNamespace(is_animation_playing=playing))
    return types.SimpleNamespace(windows=[window])


class StepCodeLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        scl.reset(self.tmp.name)
        self.addCleanup(scl.reset)
        self.path = scl.file_path()

    def test_code_is_on_disk_before_the_step_ends_and_marked_running(self):
        token = scl.begin("execute_blender_code", "import bpy\nbpy.data.objects.remove(o)", "main")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("bpy.data.objects.remove(o)", text)
        self.assertIn("RUNNING", text)
        scl.end(token, "finished")
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("RUNNING", text.split("===", 1)[1])
        self.assertIn("finished", text)

    def test_keys_are_redacted_before_the_file_is_written(self):
        key = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
        scl.begin("execute_blender_code", "headers = {'Authorization': 'Bearer %s'}\nK = '%s'" % (key, key))
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(key, text)
        self.assertIn("***", text)

    def test_a_named_secret_is_redacted(self):
        log.set_secret("t576", "my-very-secret-token-576")
        self.addCleanup(log.set_secret, "t576", "")
        scl.begin("t", "x = 'my-very-secret-token-576'")
        self.assertNotIn("my-very-secret-token-576", self.path.read_text(encoding="utf-8"))

    def test_only_the_last_steps_are_kept(self):
        for n in range(scl.KEEP + 5):
            scl.begin("t", "step_number_%d()" % n)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("=== step "), scl.KEEP)
        self.assertNotIn("step_number_0()", text)
        self.assertIn("step_number_%d()" % (scl.KEEP + 4), text)

    def test_long_code_is_cut_and_says_so(self):
        scl.begin("t", "x = 1\n" * (scl.MAX_CHARS // 2))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("[cut:", text)
        self.assertLess(len(text), scl.MAX_CHARS + 2000)

    def test_a_key_is_never_cut_in_half_at_the_limit(self):
        key = "sk-" + "Z" * 40
        scl.begin("t", "a" * (scl.MAX_CHARS - 10) + " " + key)
        self.assertNotIn("sk-Z", self.path.read_text(encoding="utf-8"))

    def test_a_key_registered_after_the_step_began_is_hidden_in_the_next_write(self):
        token = scl.begin("t", "x = 'late-registered-secret-576'")
        self.assertIn("late-registered-secret-576", self.path.read_text(encoding="utf-8"))
        log.set_secret("t576b", "late-registered-secret-576")
        self.addCleanup(log.set_secret, "t576b", "")
        scl.end(token, "finished")
        self.assertNotIn("late-registered-secret-576", self.path.read_text(encoding="utf-8"))

    def test_the_file_name_has_the_process_id(self):
        import os
        self.assertEqual(self.path.name, "steps_%d.txt" % os.getpid())

    def test_files_of_other_processes_older_than_a_few_days_are_removed(self):
        import os
        import time
        old = Path(self.tmp.name) / "steps_1.txt"
        new = Path(self.tmp.name) / "steps_2.txt"
        old.write_text("x")
        new.write_text("y")
        past = time.time() - (scl.KEEP_DAYS + 1) * 86400
        os.utime(old, (past, past))
        scl.begin("t", "code")
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_a_failed_write_adds_no_entry_and_leaves_no_false_running(self):
        with mock.patch.object(scl, "_write", side_effect=OSError("disk")):
            self.assertIsNone(scl.begin("t", "never_written()"))
        scl.begin("t", "second()")
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("never_written()", text)
        self.assertEqual(text.count("=== step "), 1)

    def test_a_failed_end_removes_the_file_instead_of_leaving_running(self):
        token = scl.begin("t", "code()")
        with mock.patch.object(scl, "_write", side_effect=OSError("disk")):
            scl.end(token, "finished")
        self.assertFalse(self.path.exists())

    def test_never_raises(self):
        (Path(self.tmp.name) / "file.txt").write_text("x")      # a file where a folder is needed
        scl.reset(str(Path(self.tmp.name) / "file.txt" / "nested"))
        self.assertIsNone(scl.begin("t", "code"))
        scl.end(None)
        scl.end("not a token")


class DragDetection(unittest.TestCase):
    def setUp(self):
        ua.reset()
        self.addCleanup(ua.reset)

    def test_quiet_means_not_busy(self):
        self.assertEqual(ua.busy_reason(_wm()), "")

    def test_a_modal_tool_is_busy(self):
        self.assertIn("TRANSFORM_OT_translate", ua.busy_reason(_wm(["TRANSFORM_OT_translate"])))

    def test_coworkers_own_modals_are_ignored(self):
        self.assertEqual(ua.busy_reason(_wm(["bfacw.gpu_input", "BFACW_OT_gpu_input"])), "")

    def test_a_user_update_just_now_is_busy_then_quiet_again(self):
        ua.note_update()
        self.assertIn("slider", ua.busy_reason(_wm()))
        self.assertEqual(ua.busy_reason(_wm(), now=ua._last_update[0] + ua.QUIET + 0.01), "")

    def test_updates_from_our_own_step_are_not_a_drag(self):
        with ua.stepping():
            ua.note_update()
        ua.note_update()                       # right after the step: its redraw, still its own
        self.assertEqual(ua.busy_reason(_wm()), "")

    def test_playback_does_not_hold_steps_back(self):
        ua.note_update()
        self.assertEqual(ua.busy_reason(_wm(playing=True)), "")

    def test_a_broken_window_manager_means_not_busy(self):
        self.assertEqual(ua.busy_reason(None), "")
        self.assertEqual(ua.busy_reason(types.SimpleNamespace(windows=[object()])), "")


class DragGate(unittest.TestCase):
    def setUp(self):
        ua.reset()
        self.addCleanup(ua.reset)
        self.wm = _wm()
        bpy = types.SimpleNamespace(context=types.SimpleNamespace(window_manager=self.wm))
        patcher = mock.patch.dict(sys.modules, {"bpy": bpy})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ran = []

    def _run(self):
        self.ran.append(1)
        return br._ExecResult({"status": "ok", "result": "done"})

    def test_nothing_in_the_way_runs_at_once(self):
        res = br._busy_gate("", "execute_blender_code", WRITE, False, self._run)
        self.assertEqual(self.ran, [1])
        self.assertIsNone(res.check_fn)

    def test_a_write_waits_for_the_drag_then_runs(self):
        ua.note_update()
        res = br._busy_gate("", "execute_blender_code", WRITE, False, self._run)
        self.assertEqual(self.ran, [])
        self.assertIsNotNone(res.check_fn)
        self.assertIsNone(res.check_fn())              # still dragging
        self.assertEqual(self.ran, [])
        ua._last_update[0] -= 1.0                      # the user let go
        self.assertEqual(res.check_fn(), {"status": "ok", "result": "done"})
        self.assertEqual(self.ran, [1])

    def test_waiting_is_bounded_and_answers_that_nothing_changed(self):
        self.wm.windows[0].modal_operators = [_op("TRANSFORM_OT_rotate")]
        t0 = 1000.0
        with mock.patch.object(br.time, "monotonic", return_value=t0):
            res = br._busy_gate("", "execute_blender_code", WRITE, False, self._run)
        with mock.patch.object(br.time, "monotonic", return_value=t0 + ua.WAIT_LIMIT + 1):
            answer = res.check_fn()
        self.assertEqual(self.ran, [])
        self.assertEqual(answer["status"], "error")
        self.assertIn("Nothing was changed", answer["message"])
        self.assertIn("TRANSFORM_OT_rotate", answer["message"])

    def test_reads_and_plans_never_wait(self):
        ua.note_update()
        res = br._busy_gate("", "get_scene_info", WRITE, False, self._run)
        self.assertEqual(self.ran, [1])
        self.assertIsNone(res.check_fn)

    def test_stop_ends_the_wait_without_running(self):
        ua.note_update()
        res = br._busy_gate("t1", "execute_blender_code", WRITE, False, self._run)
        with mock.patch.object(br, "_stop_pressed", return_value=True):
            answer = res.check_fn()
        self.assertEqual(self.ran, [])
        self.assertIn("Stopped", answer["message"])

    def test_a_deferred_answer_of_the_step_is_followed(self):
        ua.note_update()
        inner = mock.Mock(side_effect=[None, {"status": "ok", "result": "late"}])

        def run():
            return br._ExecResult({}, inner)
        res = br._busy_gate("", "execute_blender_code", WRITE, False, run)
        ua._last_update[0] -= 1.0
        self.assertIsNone(res.check_fn())              # ran, its own answer not there yet
        self.assertIsNone(res.check_fn())
        self.assertEqual(res.check_fn()["result"], "late")


class PasteGuards(unittest.TestCase):
    def setUp(self):
        ua.reset()
        self.addCleanup(ua.reset)

    def test_paste_does_not_borrow_an_area_during_a_drag(self):
        paste = mock.Mock()
        paste.poll.return_value = True
        wm = _wm(["TRANSFORM_OT_translate"])
        ops = types.SimpleNamespace(image=types.SimpleNamespace(clipboard_paste=paste))
        bpy = types.SimpleNamespace(ops=ops, context=types.SimpleNamespace(window_manager=wm), data=mock.Mock())
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            self.assertIsNone(att.paste_clipboard_image(bpy.context))
        paste.assert_not_called()

    def test_the_editors_restore_survives_a_vanished_image(self):
        class Space:
            image = "shown"
            use_image_pin = False

            def __setattr__(self, name, value):
                if name == "image" and value == "shown":
                    raise ReferenceError("gone")
                object.__setattr__(self, name, value)

        space = Space()
        area = types.SimpleNamespace(type="IMAGE_EDITOR", spaces=types.SimpleNamespace(active=space),
                                     regions=[], ui_type="IMAGE_EDITOR", width=1, height=1)
        window = types.SimpleNamespace(screen=types.SimpleNamespace(areas=[area]))
        context = mock.MagicMock()
        context.window = window
        with att._image_editor(context) as got:
            self.assertIs(got, space)               # leaving the block must not raise


class LongModalsAndOwnChanges(unittest.TestCase):
    def setUp(self):
        ua.reset()
        self.addCleanup(ua.reset)

    def test_a_modal_running_longer_than_the_limit_is_no_drag(self):
        wm = _wm(["WM_OT_some_file_browser"])
        self.assertEqual(ua.check(wm, now=100.0)[0], "modal")
        self.assertEqual(ua.check(wm, now=100.0 + ua.LONG_MODAL - 1)[0], "modal")
        self.assertEqual(ua.check(wm, now=100.0 + ua.LONG_MODAL + 1), ("", ""))

    def test_a_new_modal_of_the_same_type_is_held_again(self):
        wm = _wm(["TRANSFORM_OT_translate"])
        ua.check(wm, now=100.0)
        wm.windows[0].modal_operators = []
        self.assertEqual(ua.check(wm, now=100.5)[0], "")             # gone, but not yet forgotten
        self.assertEqual(ua.check(wm, now=200.0), ("", ""))          # it ended: forgotten
        wm.windows[0].modal_operators = [_op("TRANSFORM_OT_translate")]
        self.assertEqual(ua.check(wm, now=201.0)[0], "modal")

    def test_a_flicker_is_not_an_end(self):
        wm = _wm(["WM_OT_some_file_browser"])
        ua.sample(wm, now=10.0)
        wm.windows[0].modal_operators = []
        ua.sample(wm, now=10.3)                     # not listed for a moment
        wm.windows[0].modal_operators = [_op("WM_OT_some_file_browser")]
        self.assertEqual(ua.check(wm, now=10.0 + ua.LONG_MODAL + 1), ("", ""))   # still the old stamp

    def test_the_sampler_stamps_a_modal_before_any_step_asks(self):
        wm = _wm(["VIEW3D_OT_walk"])
        ua.sample(wm, now=10.0)
        self.assertEqual(ua.check(wm, now=10.0 + ua.LONG_MODAL + 1), ("", ""))

    def test_the_refusal_names_the_tool_and_has_no_mouse_wording_for_a_modal(self):
        text = ua.refusal(ua.busy_reason(_wm(["TRANSFORM_OT_rotate"])), 8.0, "modal")
        self.assertIn("TRANSFORM_OT_rotate", text)
        self.assertIn("Esc", text)
        self.assertNotIn("let go of the mouse", text)
        self.assertIn("let go of the mouse", ua.refusal("x", 8.0, "drag"))

    def test_own_changes_are_not_a_drag(self):
        ua.own(1.0)
        ua.note_update()
        self.assertEqual(ua.busy_reason(_wm()), "")

    def test_a_slow_evaluation_after_a_long_step_is_still_its_own(self):
        with mock.patch.object(ua.time, "monotonic", side_effect=[100.0, 103.0, 103.0]):
            with ua.stepping():
                pass                              # the step took 3 s
        with mock.patch.object(ua.time, "monotonic", return_value=105.0):
            ua.note_update()                      # its evaluation arrives 2 s later
        self.assertEqual(ua._last_update[0], 0.0)
        with mock.patch.object(ua.time, "monotonic", return_value=109.0):
            ua.note_update()                      # later still: the user's
        self.assertEqual(ua._last_update[0], 109.0)

    def test_own_change_of_the_api_marks_it(self):
        cw = _load("coworker_api")
        cw.own_change()
        ua.note_update()
        self.assertEqual(ua._last_update[0], 0.0)

    def test_two_chats_waiting_for_the_same_drag_both_run_after_it(self):
        wm = _wm()
        bpy = types.SimpleNamespace(context=types.SimpleNamespace(window_manager=wm))
        ran = []
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            ua.note_update()
            parent = br._busy_gate("", "execute_blender_code", WRITE, False, lambda: ran.append("parent") or br._ExecResult({"status": "ok"}))
            helper = br._busy_gate("h1", "execute_blender_code", WRITE, False, lambda: ran.append("helper") or br._ExecResult({"status": "ok"}))
            self.assertEqual(ran, [])
            ua._last_update[0] -= 1.0
            self.assertEqual(parent.check_fn()["status"], "ok")
            self.assertEqual(helper.check_fn()["status"], "ok")
        self.assertEqual(sorted(ran), ["helper", "parent"])

    def test_playback_running_does_not_hold_a_step(self):
        wm = _wm(playing=True)
        bpy = types.SimpleNamespace(context=types.SimpleNamespace(window_manager=wm))
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            ua.note_update()
            res = br._busy_gate("", "execute_blender_code", WRITE, False, lambda: br._ExecResult({"status": "ok"}))
        self.assertIsNone(res.check_fn)

    def test_jump_to_tools_are_gated_like_writes(self):
        wm = _wm(["TRANSFORM_OT_translate"])
        bpy = types.SimpleNamespace(context=types.SimpleNamespace(window_manager=wm))
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            res = br._busy_gate("", "jump_to_asset_browser", "a", False, lambda: br._ExecResult({"status": "ok"}))
        self.assertIsNotNone(res.check_fn)
        self.assertTrue(sb.changes_scene("jump_to_asset_browser"))
        self.assertFalse(sb.changes_scene("get_scene_digest"))

    def test_a_step_that_waited_for_an_approval_card_is_checked_again_when_it_runs(self):
        src = (_ADDON / "mcp_to_blender_server.py").read_text(encoding="utf-8")
        body = src[src.index("    def _execute(c: str, s: bool)"):src.index("    def _run() -> _ExecResult")]
        self.assertIn("_busy_gate(", body)          # the gate sits in _execute, after card and claim waits
        self.assertNotIn("_busy_gate(", src[src.index("    def _gated_run()"):src.index("    exec_result = None")])

    def test_internal_code_goes_through_the_same_gate(self):
        src = (_ADDON / "mcp_to_blender_server.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertIn("_busy_gate(_thread, _tool, c, _internal,", src)


class PasteRefusal(unittest.TestCase):
    def setUp(self):
        ua.reset()
        self.addCleanup(ua.reset)

    def _context(self, areas):
        window = types.SimpleNamespace(screen=types.SimpleNamespace(areas=areas))
        wm = _wm(["TRANSFORM_OT_translate"])
        wm.windows = [window]
        window.modal_operators = [_op("TRANSFORM_OT_translate")]
        context = mock.MagicMock()
        context.window = window
        context.window_manager = wm
        return context

    def test_busy_blocks_only_when_an_area_would_be_borrowed(self):
        space = types.SimpleNamespace(image=None, use_image_pin=False)
        editor = types.SimpleNamespace(type="IMAGE_EDITOR", spaces=types.SimpleNamespace(active=space), regions=[],
                                       ui_type="IMAGE_EDITOR", width=1, height=1)
        with att._image_editor(self._context([editor])) as got:
            self.assertIs(got, space)               # an open Image Editor is used, drag or not
        other = types.SimpleNamespace(type="TIMELINE", spaces=types.SimpleNamespace(active=space), regions=[],
                                      ui_type="TIMELINE", width=1, height=1)
        with att._image_editor(self._context([other])) as got:
            self.assertIsNone(got)
        self.assertIn("tool", att.take_refusal())
        self.assertEqual(att.take_refusal(), "")    # read once

    def test_the_editor_reports_the_wait_and_pastes_no_text(self):
        src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
        self.assertIn("Paste Image waits while a tool is running", src)
        self.assertIn("chat_attachments.take_refusal()", src)


def _step_refused(result_text):
    """The loop's own test (``agent_controller._step_refused``, #591), copied from its source."""
    text = str(result_text or "")
    return '"status": "error"' in text[:200] and '"message": "Not run:' in text[:400]


class UnifiedGate(unittest.TestCase):
    def setUp(self):
        ua.reset()
        lj.clear()
        self.addCleanup(ua.reset)
        self.addCleanup(lj.clear)
        self.wm = _wm()
        bpy = types.SimpleNamespace(context=types.SimpleNamespace(window_manager=self.wm),
                                    app=types.SimpleNamespace(is_job_running=lambda kind: kind == self.job))
        patcher = mock.patch.dict(sys.modules, {"bpy": bpy})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.job = ""
        self.ran = []

    def _run(self):
        self.ran.append(1)
        return br._ExecResult({"status": "ok", "result": "done"})

    def test_a_render_is_refused_at_once_with_the_render_wording(self):
        self.job = "RENDER"
        res = br._busy_gate("", "execute_blender_code", "import bpy\nbpy.data.objects.new('x', None)", False, self._run)
        self.assertIsNone(res.check_fn)
        self.assertEqual(self.ran, [])
        self.assertIn("render is running", res.response["message"])
        self.assertTrue(_step_refused(json.dumps(res.response)))

    def test_a_bake_gets_its_own_word(self):
        self.job = "OBJECT_BAKE"
        res = br._busy_gate("", "execute_blender_code", "import bpy\nbpy.data.objects.new('x', None)", False, self._run)
        self.assertIn("bake is running", res.response["message"])

    def test_a_modal_tool_is_waited_for_then_refused_with_the_tool_wording(self):
        self.wm.windows[0].modal_operators = [_op("TRANSFORM_OT_translate")]
        t0 = 1000.0
        with mock.patch.object(br.time, "monotonic", return_value=t0):
            res = br._busy_gate("", "execute_blender_code", "import bpy\nbpy.data.objects.new('x', None)", False, self._run)
        self.assertIsNotNone(res.check_fn)
        with mock.patch.object(br.time, "monotonic", return_value=t0 + ua.WAIT_LIMIT + 1):
            answer = res.check_fn()
        self.assertIn("TRANSFORM_OT_translate", answer["message"])
        self.assertTrue(_step_refused(json.dumps(answer)))

    def test_a_render_that_starts_while_waiting_for_a_drag_refuses_with_the_render_wording(self):
        ua.note_update()
        res = br._busy_gate("", "execute_blender_code", "import bpy\nbpy.data.objects.new('x', None)", False, self._run)
        self.job = "RENDER"
        ua._last_update[0] -= 1.0
        answer = res.check_fn()
        self.assertIn("render is running", answer["message"])
        self.assertEqual(self.ran, [])

    def test_the_scene_touching_reads_and_jump_to_are_one_list(self):
        for tool in lj.SCENE_TOUCHING_READS | {"jump_to_anything"}:
            self.assertTrue(sb.changes_scene(tool), tool)
        self.wm.windows[0].modal_operators = [_op("TRANSFORM_OT_translate")]
        for tool in ("get_asset_tags", "jump_to_tab_by_name"):
            res = br._busy_gate("", tool, WRITE, False, self._run)
            self.assertIsNotNone(res.check_fn, tool)

    def test_a_script_that_only_reads_is_not_held(self):
        self.wm.windows[0].modal_operators = [_op("TRANSFORM_OT_translate")]
        res = br._busy_gate("", "execute_blender_code", "import bpy\nresult = {'n': len(bpy.data.objects)}", False, self._run)
        self.assertIsNone(res.check_fn)
        self.assertEqual(self.ran, [1])

    def test_internal_code_is_held_only_for_the_risky_markers(self):
        self.wm.windows[0].modal_operators = [_op("TRANSFORM_OT_translate")]
        risky = br._busy_gate("", "coworker_internal", "bpy.ops.ed.undo()", True, self._run)
        self.assertIsNotNone(risky.check_fn)
        quiet = br._busy_gate("", "coworker_internal", "result = {'ok': 1}", True, self._run)
        self.assertIsNone(quiet.check_fn)

    def test_the_early_refusal_only_covers_render_and_bake(self):
        self.wm.windows[0].modal_operators = [_op("TRANSFORM_OT_translate")]
        self.assertIsNone(br._busy_refusal("execute_blender_code", "import bpy\nbpy.data.objects.new('x', None)", False))
        self.job = "RENDER"
        self.assertIsNotNone(br._busy_refusal("execute_blender_code", "import bpy\nbpy.data.objects.new('x', None)", False))


if __name__ == "__main__":
    unittest.main()
