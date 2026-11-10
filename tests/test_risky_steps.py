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
4. The code and file guard (#347): writes outside the project folder and the
   internet ask; the project folder, the temp folder, ``//`` paths, reading,
   and paths or module names inside strings don't.

Run with::

    python -m unittest tests.test_risky_steps -v
"""

__all__ = ()

import contextlib
import importlib
import json
import os
import sys
import tempfile
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


# Somewhere that is neither the temp folder nor below it (nothing is ever written there).
_ROOT = os.path.abspath(os.sep + "cw_guard_347")
_BLEND = os.path.join(_ROOT, "Proj", "scene.blend")
_SYSTEM = os.path.join(_ROOT, "System32")
_OUTSIDE = "write outside your project folder: "


class TestCodeAndFileGuard(unittest.TestCase):
    """Security 3 (#347): the path guard and the network rule, read from the syntax tree."""

    def setUp(self):
        patcher = mock.patch.object(os, "getcwd", return_value=os.path.join(_ROOT, "Bforartists"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _why(self, code, blend=_BLEND):
        return rs.reason(code, blend)

    def test_writes_outside_the_project_folder_ask(self):
        target = os.path.join(_SYSTEM, "x.txt")
        for code in ("open({!r}, 'w').write('hi')".format(target),
                     "with open({!r}, mode='ab') as f:\n    f.write(b'')".format(target),
                     "from pathlib import Path\nPath({!r}).write_text('x')".format(target),
                     "import pathlib\np = pathlib.Path({!r}) / 'x.txt'\np.write_bytes(b'')".format(_SYSTEM),
                     "import shutil\nshutil.copy('//a.png', {!r})".format(target),
                     "import os\nos.makedirs(os.path.join({!r}, 'x.txt'))".format(_SYSTEM),
                     "img = bpy.data.images['a']\nimg.filepath_raw = {!r}\nimg.save()".format(target),
                     "img.save_render({!r})".format(target),
                     "bpy.data.images['a'].save(filepath={!r})".format(target)):
            self.assertEqual(self._why(code), _OUTSIDE + target, code)

    def test_save_and_export_outside_say_where(self):
        target = os.path.join(_SYSTEM, "x.glb")
        why = self._why("bpy.ops.export_scene.gltf(filepath={!r})".format(target))
        self.assertEqual(why, "export files and " + _OUTSIDE + target)
        why = self._why("bpy.ops.wm.save_as_mainfile(filepath={!r})".format(os.path.join(_SYSTEM, "a.blend")))
        self.assertIn("save the .blend file", why)
        self.assertIn(_OUTSIDE, why)
        self.assertEqual(self._why("bpy.ops.export_scene.gltf(filepath='//out/x.glb')"), "export files")
        self.assertEqual(rs.ask_kind("execute_blender_code", "open({!r}, 'w')".format(target), "RISKY",
                                     "LOCAL_LLM", _BLEND)[0], "risky:" + _OUTSIDE + target)

    def test_inside_the_project_folder_and_temp_are_fine(self):
        project = os.path.dirname(_BLEND)
        for code in ("open({!r}, 'w')".format(os.path.join(project, "notes.txt")),
                     "open({!r}, 'w')".format(os.path.join(project, "sub", "deeper", "notes.txt")),
                     "import os, bpy\nopen(os.path.join(bpy.path.abspath('//'), 'notes.txt'), 'w')",
                     "import os, bpy\nopen(os.path.join(os.path.dirname(bpy.data.filepath), 'n.txt'), 'w')",
                     "img.filepath_raw = '//textures/a.png'\nimg.save()",
                     "bpy.context.scene.render.filepath = '//renders/frame_'",
                     "from pathlib import Path\nimport bpy\n(Path(bpy.data.filepath).parent / 'a.txt').write_text('')",
                     "import tempfile, os\nopen(os.path.join(tempfile.gettempdir(), 'x.txt'), 'w')",
                     "open({!r}, 'w')".format(os.path.join(tempfile.gettempdir(), "cw", "x.txt"))):
            self.assertEqual(self._why(code), "", code)

    def test_slash_slash_is_next_to_the_blend(self):
        # Blender reads "//" as the .blend's folder; Python's open() doesn't (a network share).
        self.assertEqual(self._why("img.save_render('//render.png')"), "")
        self.assertEqual(self._why("bpy.context.scene.render.filepath = '//../elsewhere/'"),
                         _OUTSIDE + os.path.join(_ROOT, "elsewhere"))
        self.assertIn(_OUTSIDE, self._why("open('//notes.txt', 'w')"))

    def test_relative_paths_land_in_the_working_folder(self):
        self.assertEqual(self._why("open('notes.txt', 'w')"),
                         _OUTSIDE + os.path.join(_ROOT, "Bforartists", "notes.txt"))

    def test_reading_and_strings_are_fine(self):
        target = os.path.join(_SYSTEM, "x.txt")
        for code in ("print(open({!r}).read())".format(target),
                     "data = open({!r}, 'rb').read()".format(target),
                     "import shutil\nshutil.copy({!r}, bpy.path.abspath('//x.txt'))".format(target),  # copying in
                     "bpy.ops.import_scene.gltf(filepath={!r})".format(target),
                     "msg = 'saved to {:s} with urllib'\nprint(msg)".format(target.replace("\\", "/")),
                     "# open({!r}, 'w')\n# import urllib.request\nx = 1".format(target),
                     "name = 'C:/a'.replace('a', 'b')\nobj.name = name",
                     "for socket in node.inputs:\n    socket.default_value = 1.0"):
            self.assertEqual(self._why(code), "", code)

    def test_dynamic_paths_dont_nag_but_a_known_start_counts(self):
        for code in ("for name in names:\n    open(name, 'w')",
                     "def save(p):\n    open(p, 'w')",
                     "open(obj['path'], 'w')",
                     "img.save()"):
            self.assertEqual(self._why(code), "", code)
        self.assertEqual(self._why("open(f{!r}, 'w')".format(_SYSTEM + os.sep + "{name}.txt")),
                         _OUTSIDE + _SYSTEM + os.sep)
        self.assertEqual(self._why("import os\nopen(os.path.join({!r}, name), 'w')".format(_SYSTEM)),
                         _OUTSIDE + _SYSTEM + os.sep)
        self.assertEqual(self._why("open(f'{bpy.path.abspath(\"//\")}renders/{i}.png', 'w')"), "")

    def test_unsaved_blend_has_no_project_folder(self):
        target = os.path.join(_SYSTEM, "x.txt")
        why = self._why("open({!r}, 'w')".format(target), blend="")
        self.assertIn("before the .blend is saved", why)
        self.assertIn(target, why)
        self.assertEqual(self._why("img.save_render('//a.png')", blend=""), "")       # "//" unknown yet
        self.assertEqual(self._why("import tempfile, os\nopen(os.path.join(tempfile.gettempdir(), 'a'), 'w')",
                                   blend=""), "")
        # Saving the new file picks its folder: only the save itself asks.
        self.assertEqual(self._why("bpy.ops.wm.save_as_mainfile(filepath={!r})".format(_BLEND), blend=""),
                         "save the .blend file")

    def test_network_asks(self):
        for code in ("import urllib.request\nurllib.request.urlopen('https://example.com')",
                     "from urllib.request import urlopen",
                     "from urllib import request",
                     "import urllib\nurllib.request.urlopen('x')",
                     "import requests\nrequests.get('https://example.com')",
                     "import socket as s",
                     "import http.client",
                     "from http import client",
                     "import httpx", "import aiohttp", "import ftplib", "from smtplib import SMTP",
                     "def f():\n    import requests",
                     "__import__('socket')",
                     "import importlib\nimportlib.import_module('requests')"):
            self.assertEqual(self._why(code), "connect to the internet", code)
        for code in ("from urllib.parse import quote\nquote('a b')", "import urllib.parse",
                     "text = 'import requests; urllib.request.urlopen(x)'", "socket = node.outputs[0]"):
            self.assertEqual(self._why(code), "", code)

    def test_modes_follow_the_other_risky_steps(self):
        net = "import requests\nrequests.get('https://example.com')"
        for engine in ("LOCAL_LLM", "REMOTE_API", "EXTERNAL_AGENT", "EXTERNAL_HARNESS"):
            for approval in ("ASK", "RISKY"):
                self.assertEqual(rs.ask_kind("execute_blender_code", net, approval, engine, _BLEND),
                                 ("risky:connect to the internet", "connect to the internet"), (engine, approval))
            self.assertEqual(rs.ask_kind("execute_blender_code", net, "AUTO", engine, _BLEND), ("", ""))
        rs.auto_declines[0] = True                                 # Auto mode: nobody is there
        try:
            response, check = rs.wrap(net, True, "connect to the internet", lambda c, s: self.fail("ran"))
        finally:
            rs.auto_declines[0] = False
        self.assertIsNone(check)
        self.assertIn("Auto mode doesn't connect to the internet", response["message"])

    def test_toolcode_stays_exempt(self):
        code = _MARK + "import urllib.request\nopen({!r}, 'w')".format(os.path.join(_SYSTEM, "x"))
        self.assertEqual(self._why(code), "")
        self.assertIn("connect to the internet", self._why(rs.untrusted(code)))

    def test_code_that_doesnt_parse_keeps_the_text_rules(self):
        self.assertEqual(self._why("import requests\nif x\n  bpy.ops.wm.save_mainfile()"), "save the .blend file")


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

    def test_bridge_guards_paths_and_network(self):
        # #347 through the real request path: the open .blend's folder decides.
        self.bpy.data = types.SimpleNamespace(filepath=_BLEND)
        self.addCleanup(delattr, self.bpy, "data")
        outside = "open({!r}, 'w')".format(os.path.join(_SYSTEM, "x.txt"))
        for code, title in ((outside, "write outside your project folder"),
                            ("import urllib.request", "connect to the internet")):
            result = self._send("execute_blender_code", code)
            self.assertIsNotNone(result.check_fn, code)
            self.assertIsNone(result.check_fn())
            self.assertIn(title, rs.APPROVALS.current().title)
            rs.APPROVALS.answer("deny")
            self.assertIn("declined", result.check_fn()["message"])
        # A path and a module name that only sit in a string run without a question.
        harmless = "note = {!r}\nresult = {{'ran': True}}\n".format(outside + " via urllib.request")
        result = self._send("execute_blender_code", harmless)
        self.assertEqual(result.response.get("result"), {"ran": True}, result.response)

    def test_model_code_with_the_marker_gets_preflight(self):
        for tool in ("execute_blender_code", "execute_blender_plan"):
            result = self._send(tool, self._PREFLIGHT)
            self.assertEqual(result.response.get("status"), "error", result.response)
            self.assertIn("[Preflight]", result.response.get("message", ""))


if __name__ == "__main__":
    unittest.main()
