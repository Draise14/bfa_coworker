# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #500: what the approval card and the Workshop show.

1. The card's line is the tool and its arguments (``tool(key=value, ...)``), long
   values cut with an ellipsis, secrets as ``***``; ``execute_blender_code`` (and a
   client without a tool name) still shows its code.  Through the real bridge request
   path (Ask on Remote: the card; External Harness: the repeat card), the External
   Agent's request (its ``rawInput``), and the MCP server's ``args`` text, which stays
   JSON when a value is long.
2. A tool's answer in the Workshop: the tool's own answer first, without the bridge's
   envelope; a failed tool's first line says ``Error: ...``.

Run with::

    python -m unittest tests.test_approval_card_500 -v
"""

__all__ = ()

import contextlib
import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_ADDON = _ROOT / "addon" / "bfa_coworker"
_PKG = "bfacw_card500_pkg"


def _load(module):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + module)


ap = _load("approvals")
rs = _load("risky_steps")
lg = _load("loop_guard")
ae = _load("answer_envelope")
log = _load("log")

_MARK = "# blmcp-toolcode-skip-preflight\n"
_TOOLCODE = (_MARK + "# SPDX-FileCopyrightText: 2026 Blender Authors\n# (Bforartists-maintained fork)\n"
             "result = {'ran': True}\n")
_LONG = "Kitchen_Counter_Left_Drawer_Handle_" + "Brushed_Steel_" * 6 + "001"
_SECRET = "sk-" + "Zq7Rk2" * 6          # looks like an OpenAI key


def _card(tool, inputs, code=_TOOLCODE):
    """The card a Coworker tool's call asks with (as the bridge builds it)."""
    return ap.ApprovalRequest(tool=tool, source="Coworker", args=rs.card_args(tool, code, inputs),
                              coworker_tool=True, name=tool)


class TestCardLine(unittest.TestCase):

    def test_plain_tool_shows_its_name_and_arguments(self):
        card = _card("set_strip_frame_range", json.dumps({"strip_name": "Title", "start": 120}))
        self.assertEqual(card.title, "Coworker wants to run set_strip_frame_range")
        self.assertEqual(card.detail, 'set_strip_frame_range(strip_name="Title", start=120)')
        self.assertNotIn("blmcp-toolcode", card.detail)

    def test_long_values_are_cut_with_an_ellipsis(self):
        card = _card("assign_material_to_objects",
                     json.dumps({"material_name": _LONG, "object_names": ["Cube"] * 30, "slot_index": 2}))
        detail = card.detail
        self.assertTrue(detail.startswith('assign_material_to_objects(material_name="Kitchen_Counter'), detail)
        self.assertIn("…", detail)
        self.assertNotIn(_LONG, detail)
        self.assertLessEqual(len(detail), 160)
        self.assertIn("object_names=", detail)          # every argument still named
        short = _card("t", json.dumps({"a": "x" * 47}))
        self.assertEqual(short.detail, 't(a="{:s}…)'.format("x" * 46))

    def test_secret_looking_values_are_redacted(self):
        card = _card("assign_material_to_objects", json.dumps({"material_name": "M", "object_names": [_SECRET]}))
        self.assertIn('object_names=["***"]', card.detail)
        self.assertNotIn("Zq7Rk2", card.detail)
        # A key the user set (the chat's Copy hides it too): hidden before it is cut.
        log.set_secret("remote_api_key", "my-own-key-1234567890")
        self.addCleanup(log.set_secret, "remote_api_key", "")
        card = _card("t", json.dumps({"text": "x" * 30 + "my-own-key-1234567890"}))
        self.assertNotIn("my-own-key", card.detail)

    def test_execute_blender_code_still_shows_its_code(self):
        code = "import bpy\n\nbpy.context.scene.frame_current = 7"
        card = _card("execute_blender_code", json.dumps({"code": code}), code=code)
        self.assertEqual(card.detail, "import bpy bpy.context.scene.frame_current = 7")
        plan = _card("execute_blender_plan", json.dumps({"steps": [{"template": "create_cube"}]}), code="x = 1")
        self.assertEqual(plan.detail, "x = 1")           # the code it runs, not the steps
        # A client that sends code without a tool name, or an older server without args.
        self.assertEqual(_card("", None, code="y = 2").detail, "y = 2")
        self.assertEqual(_card("get_scene_digest", None, code="z = 3").detail, "z = 3")

    def test_code_that_isnt_tool_code_shows_as_code_whatever_its_tool_name(self):
        # A client can name any tool: only Coworker's own tool-code is summed up by its arguments.
        risky = "import os\nos.remove('scene.blend')"
        card = _card("get_objects_summary", json.dumps({"limit": 5}), code=risky)
        self.assertEqual(card.detail, "import os os.remove('scene.blend')")

    def test_a_risky_step_names_the_tool_too(self):
        card = ap.ApprovalRequest(tool="risky:overwrite pixels",
                                  args=rs.card_args("bake_textures", _TOOLCODE, '{"a": 1}'),
                                  coworker_tool=True, reason="overwrite pixels", name="bake_textures")
        self.assertEqual((card.title, card.detail), ("The agent wants to overwrite pixels", "bake_textures(a=1)"))

    def test_arguments_cut_on_the_way_are_shown_as_text(self):
        card = _card("t", '{"a": "' + "y" * 500)          # not JSON any more: no crash, short text
        self.assertTrue(card.detail.startswith('{"a": "yyy'))
        self.assertLessEqual(len(card.detail), 160)

    def test_the_external_agents_own_tools_keep_their_line(self):
        self.assertEqual(ap.ApprovalRequest(tool="bash", args={"command": "ls"}).detail, "ls")
        pairs = ap.ApprovalRequest(tool="Edit", args={"file_path": "a.py", "old": "x"}).detail
        self.assertEqual(pairs, 'file_path="a.py", old="x"')
        # One of Coworker's tools through the External Agent (its rawInput is the arguments).
        ours = ap.ApprovalRequest(tool="get_object_detail_summary", args={"name": "Cube"}, coworker_tool=True)
        self.assertEqual(ours.detail, 'get_object_detail_summary(name="Cube")')

    def test_the_mcp_servers_args_stay_json(self):
        spec = importlib.util.spec_from_file_location(
            "bfacw_card500_connection", _ROOT / "mcp" / "blmcp" / "tools_helpers" / "connection.py")
        conn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(conn)
        text = conn._args_text({"code": "x" * 5000, "name": "Cube"})
        self.assertLessEqual(len(text), 400)
        parsed = json.loads(text)                       # still JSON: the card can read it
        self.assertEqual(parsed["name"], "Cube")
        self.assertTrue(parsed["code"].endswith("…"))
        self.assertEqual(json.loads(conn._args_text({"start": 120})), {"start": 120})


class TestBridgeCards(unittest.TestCase):
    """The real bridge request path, with a stand-in ``bpy``."""

    @classmethod
    def setUpClass(cls):
        cls.bridge = _load("mcp_to_blender_server")
        cls.si = _load("session_identity")
        cls.sandbox = _load("weak_sandbox")

    def _bpy(self, approval, engine):
        bpy = types.ModuleType("bpy")
        prefs = types.SimpleNamespace(approval_mode=approval, operating_mode=engine)
        bpy.context = types.SimpleNamespace(preferences=types.SimpleNamespace(
            addons={_PKG: types.SimpleNamespace(preferences=prefs)}))
        return bpy

    def _setup(self, approval, engine):
        rs.APPROVALS.end_session()
        self.addCleanup(rs.APPROVALS.end_session)
        self.si.set_token_required(False)
        self.addCleanup(self.si.set_token_required, True)
        for patcher in (mock.patch.dict(sys.modules, {"bpy": self._bpy(approval, engine)}),
                        mock.patch.object(self.sandbox, "WeakSandboxForLLM", contextlib.nullcontext)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _send(self, tool, code, args=None):
        request = {"type": "execute", "code": code, "strict_json": False, "tool": tool}
        if args is not None:
            request["args"] = json.dumps(args)
        exec_result, _strict = self.bridge._execute_code_from_request(json.dumps(request).encode("utf-8"))
        return exec_result

    def test_ask_mode_card_shows_the_tools_arguments(self):
        self._setup("ASK", "REMOTE_API")
        result = self._send("assign_material_to_objects", _TOOLCODE,
                            {"material_name": "Material", "object_names": ["Cube"]})
        self.assertIsNotNone(result.check_fn)             # it asks first
        card = rs.APPROVALS.current()
        self.assertEqual(card.title, "Coworker wants to run assign_material_to_objects")
        self.assertEqual(card.detail, 'assign_material_to_objects(material_name="Material", object_names=["Cube"])')
        rs.APPROVALS.answer("once")
        self.assertEqual(result.check_fn(), {"status": "ok", "result": {"ran": True}})

    def test_ask_mode_card_for_execute_blender_code_shows_the_code(self):
        self._setup("ASK", "REMOTE_API")
        code = "import bpy\nresult = {'frame': 7}\n"
        self._send("execute_blender_code", code, {"code": code})
        self.assertEqual(rs.APPROVALS.current().detail, "import bpy result = {'frame': 7}")

    def test_the_external_agent_repeat_card_shows_the_tools_arguments(self):
        # The External Agent: on the External Harness nobody can answer a card, so its
        # repeat is refused at once without one (#499, test_loop_guard).
        self._setup("AUTO", "EXTERNAL_AGENT")
        lg.BRIDGE.new_turn()
        self.addCleanup(lg.BRIDGE.new_turn)
        args = {"name": "Cube", "location": [0, 0, 1]}
        for _n in range(2):
            self.assertIsNone(self._send("set_location", _TOOLCODE, args).check_fn)
        third = self._send("set_location", _TOOLCODE, args)
        self.assertIsNotNone(third.check_fn, third.response)
        card = rs.APPROVALS.current()
        self.assertIn("3rd time", card.title)
        self.assertEqual(card.detail, 'set_location(name="Cube", location=[0, 0, 1])')


class TestWorkshopResult(unittest.TestCase):

    def test_a_tools_own_error_reads_as_an_error_first(self):
        text = json.dumps({"status": "ok", "result": {"status": "error", "scene": "Edit", "strip": None,
                                                      "message": "No strip named 'title'. Did you mean 'Title'?"}},
                          indent=2)
        shown = ae.readable(text).splitlines()
        self.assertEqual(shown[0], "Error: No strip named 'title'. Did you mean 'Title'?")
        self.assertIn('"scene": "Edit"', "\n".join(shown))
        self.assertNotIn('"status"', "\n".join(shown))

    def test_the_bridges_error_reads_as_an_error_first(self):
        tb = "Traceback (most recent call last):\n  File \"<x>\", line 1\nNameError: name 'y' is not defined"
        shown = ae.readable(json.dumps({"status": "error", "message": tb, "stdout": "partial"}))
        self.assertTrue(shown.startswith("Error: NameError: name 'y' is not defined\nTraceback"), shown)
        self.assertTrue(shown.endswith("stdout: partial"))
        self.assertEqual(ae.readable('{"status": "error", "message": "declined"}'), "Error: declined")

    def test_an_answer_shows_the_tools_own_answer_first(self):
        text = json.dumps({"status": "ok", "result": {"frame": 7}, "stdout": "hello\n"}, indent=2)
        self.assertEqual(ae.readable(text), '{\n  "frame": 7\n}\nstdout: hello')
        self.assertEqual(ae.readable('{"status": "ok", "result": "Done."}'), "Done.")
        self.assertEqual(ae.readable('{"status": "ok", "result": null}'), "(no result)")

    def test_anything_else_is_left_as_it_is(self):
        for text in ("Error calling tool 'x': refused", "plain text", "[1, 2]",
                     '{"status": "ok", "scene": "Edit", "message": "Frame 60"}',    # a tool's own flat answer
                     '{"name": "Cube"}', "{not json"):
            self.assertEqual(ae.readable(text), text)

    def test_the_workshop_row_draws_the_readable_answer(self):
        cv = _load("ui_gpu.chat_view")
        lay = _load("ui_gpu.layout")
        st = _load("ui_gpu.style")
        text = json.dumps({"status": "ok", "result": {"status": "error", "message": "Object 'Cubee' not found."}},
                          indent=2)
        hist = [{"role": "user", "content": "go", "turn_start": True},
                {"role": "tool", "name": "get_object_detail_summary", "tool_call_id": "c1", "content": text},
                {"role": "assistant", "content": "Done."}]
        view = cv.ChatView()
        view.expanded.add("t0")                          # the Workshop open
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState())
        view.draw(canvas, lay.Rect(0, 0, 600, 3000), hist)
        texts = [cmd.text for cmd in canvas.commands if cmd.kind == "text"]
        frame = texts[texts.index("error") + 1:]          # the result frame's label, then its lines
        self.assertTrue(frame and frame[0].startswith("Error: Object 'Cubee' not found."), frame[:3])
        self.assertNotIn('"status": "ok",', " ".join(texts))
        self.assertTrue(cv._tool_failed(hist[1]))          # still counted as failed


if __name__ == "__main__":
    unittest.main()
