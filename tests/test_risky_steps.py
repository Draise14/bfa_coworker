# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for asking before risky steps (risky_steps.py, #166).

1. What counts: saving over the .blend, open / reload / quit, deleting or
   moving files, export, running programs -- in plain words; ordinary code
   and Coworker's own tool-code don't.
2. The bridge waits for the answer: Allow once runs the code (also a deferred
   one), Deny / Stop return a clear "declined", Always lets that kind through.
3. Auto mode declines at once instead of waiting.

Run with::

    python -m unittest tests.test_risky_steps -v
"""

__all__ = ()

import contextlib
import importlib
import json
import sys
import types
import unittest
from unittest import mock
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_risky_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".risky_steps")


rs = _load()


class _Result:
    def __init__(self, response, check_fn=None):
        self.response, self.check_fn = response, check_fn


class TestReason(unittest.TestCase):

    def test_what_counts(self):
        self.assertEqual(rs.reason("bpy.ops.wm.save_mainfile()"), "save the .blend file")
        self.assertIn("unsaved work", rs.reason("bpy.ops.wm.quit_blender()"))
        self.assertEqual(rs.reason("import os\nos.remove(p)"), "delete or move files")
        self.assertEqual(rs.reason("Path(p).unlink()"), "delete or move files")
        self.assertEqual(rs.reason("bpy.ops.export_scene.gltf(filepath=p)"), "export files")
        self.assertEqual(rs.reason("bpy.ops.wm.obj_export(filepath=p)"), "export files")
        self.assertEqual(rs.reason("import subprocess\nsubprocess.run(['ls'])"), "run another program")
        both = rs.reason("bpy.ops.wm.save_mainfile()\nos.remove(p)")
        self.assertIn("save the .blend file", both)
        self.assertIn("delete or move files", both)

    def test_ordinary_code_and_toolcode_pass(self):
        self.assertEqual(rs.reason("bpy.ops.mesh.primitive_cube_add()\nobj.name = 'unlink_me'"), "")
        self.assertEqual(rs.reason("# blmcp-toolcode-skip-preflight\nbpy.ops.wm.save_mainfile()"), "")
        self.assertEqual(rs.reason(""), "")


class TestWrap(unittest.TestCase):

    def setUp(self):
        rs.APPROVALS.end_session()
        rs.auto_declines[0] = False
        self.ran = []

    def _execute(self, code, strict):
        self.ran.append(code)
        return _Result({"status": "ok", "result": "saved"})

    def test_waits_then_allow_runs_it(self):
        response, check = rs.wrap("bpy.ops.wm.save_mainfile()", True, "save the .blend file", self._execute)
        self.assertEqual(response, {})
        self.assertIsNone(check())                                 # waiting for you
        card = rs.APPROVALS.current()
        self.assertEqual(card.title, "Coworker wants to save the .blend file")
        rs.APPROVALS.answer("once")
        self.assertEqual(check(), {"status": "ok", "result": "saved"})
        self.assertEqual(len(self.ran), 1)

    def test_deny_and_stop(self):
        _r, check = rs.wrap("os.remove(p)", True, "delete or move files", self._execute)
        rs.APPROVALS.answer("deny")
        self.assertIn("declined", check()["message"])
        _r, check = rs.wrap("os.remove(p)", True, "delete or move files", self._execute)
        rs.APPROVALS.cancel_all()                                  # Stop
        self.assertEqual(check()["status"], "error")
        self.assertEqual(self.ran, [])

    def test_always_and_deferred_code(self):
        _r, check = rs.wrap("bpy.ops.export_scene.gltf()", True, "export files",
                            lambda c, s: _Result({}, lambda: {"status": "ok", "result": "exported"}))
        rs.APPROVALS.answer("always")
        self.assertIsNone(check())                                 # the export itself is deferred...
        self.assertEqual(check()["result"], "exported")            # ...and finishes
        response, check = rs.wrap("bpy.ops.export_scene.gltf()", True, "export files", self._execute)
        self.assertEqual((response["status"], check), ("ok", None))    # Always: no question

    def test_approval_modes_decide_what_asks(self):
        # #240: Ask (our loop: each scene-changing tool; risky everywhere), Risky only, Auto.
        code, risky = "obj.location.x = 1", "bpy.ops.wm.save_mainfile()"
        self.assertEqual(rs.ask_kind("execute_blender_code", code, "ASK", "LOCAL_LLM"), ("execute_blender_code", ""))
        self.assertEqual(rs.ask_kind("get_objects_summary", code, "ASK", "LOCAL_LLM"), ("", ""))   # looking
        self.assertEqual(rs.ask_kind("execute_blender_code", code, "ASK", "EXTERNAL_AGENT"), ("", ""))  # its card
        self.assertEqual(rs.ask_kind("execute_blender_code", code, "ASK", "EXTERNAL_HARNESS"), ("", ""))
        self.assertEqual(rs.ask_kind("execute_blender_code", risky, "ASK", "EXTERNAL_HARNESS")[1],
                         "save the .blend file")
        self.assertEqual(rs.ask_kind("execute_blender_code", code, "RISKY", "LOCAL_LLM"), ("", ""))
        self.assertEqual(rs.ask_kind("execute_blender_code", risky, "RISKY", "LOCAL_LLM")[0],
                         "risky:save the .blend file")
        self.assertEqual(rs.ask_kind("execute_blender_code", risky, "AUTO", "LOCAL_LLM"), ("", ""))

    def test_bake_asks_like_a_risky_step(self):
        # Its toolcode skips the code scan, but a bake overwrites pixels Undo can't restore.
        toolcode = "# blmcp-toolcode-skip-preflight\nbpy.ops.object.bake('INVOKE_DEFAULT')"
        kind, why = rs.ask_kind("bake_textures", toolcode, "RISKY", "LOCAL_LLM")
        self.assertEqual(kind, "risky:" + why)
        self.assertIn("Undo can't bring them back", why)
        self.assertEqual(rs.ask_kind("bake_textures", toolcode, "ASK", "EXTERNAL_HARNESS")[1], why)
        self.assertEqual(rs.ask_kind("bake_textures", toolcode, "AUTO", "LOCAL_LLM"), ("", ""))

    def test_ask_per_tool_always_and_forget(self):
        kind, why = rs.ask_kind("execute_blender_code", "x = 1", "ASK", "LOCAL_LLM")
        _r, check = rs.wrap("x = 1", True, why, self._execute, tool="execute_blender_code", kind=kind)
        self.assertEqual(rs.APPROVALS.current().title, "Coworker wants to run execute_blender_code")
        rs.APPROVALS.answer("always")
        check()
        response, check = rs.wrap("y = 2", True, why, self._execute, tool="execute_blender_code", kind=kind)
        self.assertIsNone(check)                                   # Always: that tool runs
        rs.APPROVALS.forget_always()                               # the mode changed
        _r, check = rs.wrap("z = 3", True, why, self._execute, tool="execute_blender_code", kind=kind)
        self.assertIsNotNone(rs.APPROVALS.current())               # asks again
        rs.auto_declines[0] = True                                 # overnight Auto mode
        try:
            response, check = rs.wrap("w = 4", True, why, self._execute, tool="execute_blender_code", kind=kind)
        finally:
            rs.auto_declines[0] = False
        self.assertEqual((response["status"], check), ("ok", None))   # a plain tool runs; only risky declines

    def test_auto_mode_declines(self):
        rs.auto_declines[0] = True
        try:
            response, check = rs.wrap("bpy.ops.wm.save_mainfile()", True, "save the .blend file", self._execute)
        finally:
            rs.auto_declines[0] = False
        self.assertIsNone(check)
        self.assertIn("Auto mode doesn't", response["message"])
        self.assertIsNone(rs.APPROVALS.current())


_MARK = "# blmcp-toolcode-skip-preflight\n"


class TestSkipMarker(unittest.TestCase):
    """Only Coworker's own tool-code skips the checks; the model's code never does (#347)."""

    def test_only_the_first_line_counts(self):
        save = "bpy.ops.wm.save_mainfile()"
        self.assertEqual(rs.reason(_MARK + save), "")                                   # tool-code
        self.assertTrue(rs.is_toolcode(_MARK + "import bpy\n"))
        for planted in ("x = 1\n" + _MARK + save, "\n" + _MARK + save, "x = 1  " + _MARK + save,
                        "    " + _MARK + save):
            self.assertFalse(rs.is_toolcode(planted), planted)
            self.assertEqual(rs.reason(planted), "save the .blend file", planted)

    def test_model_code_with_the_marker_is_checked(self):
        code = rs.untrusted(_MARK + "bpy.ops.wm.save_mainfile()")
        self.assertFalse(rs.is_toolcode(code))
        self.assertNotIn(_MARK.strip(), code)
        self.assertEqual(rs.ask_kind("execute_blender_code", code, "RISKY", "LOCAL_LLM"),
                         ("risky:save the .blend file", "save the .blend file"))
        self.assertEqual(rs.MODEL_CODE_TOOLS, {"execute_blender_code", "execute_blender_plan"})


class TestBridgeHonoursMarkerForToolcodeOnly(unittest.TestCase):
    """The real bridge request path, with a stand-in ``bpy`` (Permissive approval, Local engine)."""

    # A risky step and a preflight blocker that only appear in comments: the text checks
    # still see them, so the code runs only when the checks are skipped.
    _CODE = _MARK + "# bpy.ops.wm.save_mainfile()\nresult = {'ran': True}\n"
    _PREFLIGHT = _MARK + "# action.fcurves\nresult = {'ran': True}\n"

    @classmethod
    def setUpClass(cls):
        pkg = rs.__name__.rpartition(".")[0]
        cls.bridge = importlib.import_module(pkg + ".mcp_to_blender_server")
        cls.si = importlib.import_module(pkg + ".session_identity")
        cls.sandbox = importlib.import_module(pkg + ".weak_sandbox")
        bpy = types.ModuleType("bpy")
        prefs = types.SimpleNamespace(approval_mode="RISKY", operating_mode="LOCAL_LLM")
        bpy.context = types.SimpleNamespace(preferences=types.SimpleNamespace(
            addons={pkg: types.SimpleNamespace(preferences=prefs)}))
        cls.bpy = bpy

    def setUp(self):
        rs.APPROVALS.end_session()
        self.addCleanup(rs.APPROVALS.end_session)
        self.si.set_token_required(False)
        self.addCleanup(self.si.set_token_required, True)
        for patcher in (mock.patch.dict(sys.modules, {"bpy": self.bpy}),
                        mock.patch.object(self.sandbox, "WeakSandboxForLLM", contextlib.nullcontext)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _send(self, tool, code):
        request = {"type": "execute", "code": code, "strict_json": False, "tool": tool}
        exec_result, _strict = self.bridge._execute_code_from_request(json.dumps(request).encode("utf-8"))
        return exec_result

    def test_coworker_toolcode_still_runs_unchecked(self):
        for code in (self._CODE, self._PREFLIGHT):
            result = self._send("get_objects_summary", code)
            self.assertIsNone(result.check_fn)
            self.assertEqual(result.response.get("status"), "ok", result.response)
            self.assertEqual(result.response.get("result"), {"ran": True})
        self.assertIsNone(rs.APPROVALS.current())

    def test_model_code_with_the_marker_asks(self):
        result = self._send("execute_blender_code", self._CODE)
        self.assertIsNotNone(result.check_fn)                     # held back...
        self.assertIsNone(result.check_fn())
        self.assertIn("save the .blend file", rs.APPROVALS.current().title)   # ...until you answer

    def test_model_code_with_the_marker_gets_preflight(self):
        for tool in ("execute_blender_code", "execute_blender_plan"):
            result = self._send(tool, self._PREFLIGHT)
            self.assertEqual(result.response.get("status"), "error", result.response)
            self.assertIn("[Preflight]", result.response.get("message", ""))


if __name__ == "__main__":
    unittest.main()
