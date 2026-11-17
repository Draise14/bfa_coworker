# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Two crashes from use (#576): Paste Image, and agent code running during a slider drag.

1. The agent's code of a step is on disk before it runs (``step_code_log``): redacted like
   ``operations.jsonl``, bounded, marked RUNNING until the step returns.
2. A scene-changing bridge step waits while the user drags a slider or a modal tool runs
   (``ui_activity`` + the bridge's ``_drag_gate``), a bounded time, then answers clearly;
   reads and Coworker's own bookkeeping never wait.
3. Paste Image does not switch an area under a drag, and a restore that finds its image gone
   does not raise.

Fake window manager and ``bpy``; no Blender.

Run with::

    python -m unittest tests.test_crashes_576 -v
"""

__all__ = ()

import importlib
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


def _wm(modal=(), playing=False):
    window = types.SimpleNamespace(
        modal_operators=[types.SimpleNamespace(bl_idname=i) for i in modal],
        screen=types.SimpleNamespace(is_animation_playing=playing))
    return types.SimpleNamespace(windows=[window])


class StepCodeLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        scl.reset(self.tmp.name)
        self.addCleanup(scl.reset)
        self.path = Path(self.tmp.name) / scl.FILE_NAME

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

    def test_the_file_of_the_session_before_is_kept_as_prev(self):
        scl.begin("t", "crashed_script()")
        scl.reset(self.tmp.name)                    # a new session
        scl.begin("t", "new_script()")
        prev = (Path(self.tmp.name) / scl.PREV_NAME).read_text(encoding="utf-8")
        self.assertIn("crashed_script()", prev)
        self.assertIn("RUNNING", prev)
        self.assertNotIn("crashed_script()", self.path.read_text(encoding="utf-8"))

    def test_never_raises(self):
        scl.reset(str(Path(self.tmp.name) / "file.txt" / "nested"))
        (Path(self.tmp.name) / "file.txt").write_text("x")      # a file where a folder is needed
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
        res = br._drag_gate("", "execute_blender_code", self._run)
        self.assertEqual(self.ran, [1])
        self.assertIsNone(res.check_fn)

    def test_a_write_waits_for_the_drag_then_runs(self):
        ua.note_update()
        res = br._drag_gate("", "execute_blender_code", self._run)
        self.assertEqual(self.ran, [])
        self.assertIsNotNone(res.check_fn)
        self.assertIsNone(res.check_fn())              # still dragging
        self.assertEqual(self.ran, [])
        ua._last_update[0] -= 1.0                      # the user let go
        self.assertEqual(res.check_fn(), {"status": "ok", "result": "done"})
        self.assertEqual(self.ran, [1])

    def test_waiting_is_bounded_and_answers_that_nothing_changed(self):
        self.wm.windows[0].modal_operators = [types.SimpleNamespace(bl_idname="TRANSFORM_OT_rotate")]
        t0 = 1000.0
        with mock.patch.object(br.time, "monotonic", return_value=t0):
            res = br._drag_gate("", "execute_blender_code", self._run)
        with mock.patch.object(br.time, "monotonic", return_value=t0 + ua.WAIT_LIMIT + 1):
            answer = res.check_fn()
        self.assertEqual(self.ran, [])
        self.assertEqual(answer["status"], "error")
        self.assertIn("Nothing was changed", answer["message"])
        self.assertIn("TRANSFORM_OT_rotate", answer["message"])

    def test_reads_and_plans_never_wait(self):
        ua.note_update()
        res = br._drag_gate("", "get_scene_info", self._run)
        self.assertEqual(self.ran, [1])
        self.assertIsNone(res.check_fn)

    def test_stop_ends_the_wait_without_running(self):
        ua.note_update()
        res = br._drag_gate("t1", "execute_blender_code", self._run)
        with mock.patch.object(br, "_stop_pressed", return_value=True):
            answer = res.check_fn()
        self.assertEqual(self.ran, [])
        self.assertIn("Stopped", answer["message"])

    def test_a_deferred_answer_of_the_step_is_followed(self):
        ua.note_update()
        inner = mock.Mock(side_effect=[None, {"status": "ok", "result": "late"}])

        def run():
            return br._ExecResult({}, inner)
        res = br._drag_gate("", "execute_blender_code", run)
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


if __name__ == "__main__":
    unittest.main()
