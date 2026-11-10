# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Coworker's own route into the bridge (internal_route.py, #414).

1. Coworker's own maintenance code (cleanup, restore, the co-work lock) runs
   unchecked by its route, also when the model named an object after a risky
   step (``wm.save_mainfile``) or put quotes and newlines in a name: no card,
   no preflight, and the names travel as data.
2. The route can't be forged: ``execute_blender_code`` carrying the route's
   key and marker is still checked, and the route's name without this
   process's key is refused (not run).
3. The model can't reach it: it is not an MCP tool, not in the agent's tool
   list, and the key is in no file or environment variable.
4. The real socket round trip: a worker thread's call is served by the
   bridge's poll on the main thread.
5. No ``agent_controller`` or ``co_work_guard`` payload goes through
   ``execute_blender_code`` any more.

Run with::

    python -m unittest tests.test_internal_route -v
"""

__all__ = ()

import ast
import contextlib
import importlib
import json
import os
import re
import socket
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_PKG = "bfacw_internal_pkg"
_MARK = "# blmcp-toolcode-skip-preflight\n"
# Names a model could give the objects it makes.
_ODD = ("wm.save_mainfile", "bpy.ops.wm.quit_blender()", "it's \"quoted\"\nnext line", "os.remove('x')")


def _load(*names):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return [importlib.import_module(_PKG + "." + n) for n in names]


route, bridge, rs, si, cwg, sandbox = _load(
    "internal_route", "mcp_to_blender_server", "risky_steps", "session_identity", "co_work_guard", "weak_sandbox")


def _agent_controller_funcs(*names: str) -> dict[str, Any]:
    """The named payload builders from agent_controller.py, without importing it (it needs bpy)."""
    source = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    ns: dict[str, Any] = {"Any": Any, "_EntityDiff": object}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "agent_controller.py", "exec"), ns)  # noqa: S102
    return ns


_AC = _agent_controller_funcs("_build_cleanup_code", "_build_scene_restore_code")
_FIELDS = ("object_names mesh_names material_names light_names camera_names collection_names curve_names "
           "grease_pencil_names armature_names node_group_names image_names text_names").split()


class _Seq(dict):
    """A ``bpy.data`` collection stand-in."""

    def remove(self, item):
        del self[next(k for k, v in self.items() if v is item)]


class _Obj:
    def __init__(self):
        self.hide_select = False
        self.selected = False

    def select_set(self, state):
        self.selected = bool(state)


def _fake_bpy(approval: str):
    bpy = types.ModuleType("bpy")
    prefs = types.SimpleNamespace(approval_mode=approval, operating_mode="LOCAL_LLM")
    bpy.context = types.SimpleNamespace(
        preferences=types.SimpleNamespace(addons={_PKG: types.SimpleNamespace(preferences=prefs)}),
        view_layer=types.SimpleNamespace(objects=types.SimpleNamespace(active=None)))
    bpy.data = types.SimpleNamespace(objects=_Seq({n: _Obj() for n in _ODD + ("Keep",)}),
                                     meshes=_Seq(), collections=_Seq())
    return bpy


class _BridgeCase(unittest.TestCase):
    """The real ``_execute_code_from_request``, a stand-in ``bpy``, the strictest approval (Ask)."""

    approval = "ASK"

    def setUp(self):
        rs.APPROVALS.end_session()
        self.addCleanup(rs.APPROVALS.end_session)
        # A token in memory only (never the user's token file).
        patcher = mock.patch.dict(si._token_cache, {"token": "t" * 32})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bpy = _fake_bpy(self.approval)
        for patcher in (mock.patch.dict(sys.modules, {"bpy": self.bpy}),
                        mock.patch.object(sandbox, "WeakSandboxForLLM", contextlib.nullcontext)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _send(self, request):
        exec_result, _strict = bridge._execute_code_from_request(json.dumps(request).encode("utf-8"))
        return exec_result

    def _internal(self, call):
        code, data = call
        return self._send(route.make_request(code, data))

    def assert_ran_without_a_card(self, result):
        self.assertIsNone(result.check_fn, "Coworker's own code must not wait on a card")
        self.assertEqual(result.response.get("status"), "ok", result.response)
        self.assertIsNone(rs.APPROVALS.current())


class TestOwnCodeRunsUnchecked(_BridgeCase):

    def _diff(self):
        diff = types.SimpleNamespace(**{f: set() for f in _FIELDS})
        diff.object_names = set(_ODD)
        return diff

    def test_cleanup_and_restore_with_risky_looking_names(self):
        for approval in ("ASK", "RISKY"):
            self.bpy.context.preferences.addons[_PKG].preferences.approval_mode = approval
            self.bpy.data.objects.update({n: _Obj() for n in _ODD})
            result = self._internal(_AC["_build_cleanup_code"](self._diff()))
            self.assert_ran_without_a_card(result)
            self.assertEqual(sorted(result.response["result"]["cleaned"]), sorted(_ODD))
            self.assertEqual(list(self.bpy.data.objects), ["Keep"])

            self.bpy.data.objects.update({n: _Obj() for n in _ODD})
            result = self._internal(_AC["_build_scene_restore_code"]("wm.save_mainfile", set(_ODD)))
            self.assert_ran_without_a_card(result)
            self.assertEqual(result.response["result"]["restored_selection"], len(_ODD))
            self.assertIs(self.bpy.context.view_layer.objects.active, self.bpy.data.objects["wm.save_mainfile"])

    def test_lock_and_unlock_with_risky_looking_names(self):
        cwg.clear()
        self.addCleanup(cwg.clear)
        cwg.record_managed(set(_ODD), set())
        result = self._internal(cwg.build_lock_code(set(_ODD), set()))
        self.assert_ran_without_a_card(result)
        self.assertEqual(result.response["result"]["locked"], len(_ODD))
        cwg.record_prior(result.response["result"]["prior_hide_select"], None)
        result = self._internal(cwg.build_unlock_code())
        self.assert_ran_without_a_card(result)
        self.assertFalse(any(o.hide_select for o in self.bpy.data.objects.values()))

    def test_the_same_names_pasted_as_model_code_would_ask(self):
        """Why the route exists: the same cleanup, as execute_blender_code, waits on a card."""
        code = "\n".join("bpy.data.objects.remove(bpy.data.objects[{!r}])".format(n) for n in _ODD)
        request = {"type": "execute", "code": code, "strict_json": False, "tool": "execute_blender_code",
                   "token": si.session_token()}
        result = self._send(request)
        self.assertIsNotNone(result.check_fn)
        self.assertIn("save the .blend file", rs.APPROVALS.current().title)


class TestRouteCantBeForged(_BridgeCase):

    approval = "RISKY"
    _RISKY = _MARK + "# bpy.ops.wm.save_mainfile()\nresult = {'ran': True}\n"
    _PREFLIGHT = _MARK + "# action.fcurves\nresult = {'ran': True}\n"

    def test_model_code_with_the_routes_key_and_marker_is_still_checked(self):
        for tool in sorted(rs.MODEL_CODE_TOOLS):
            request = route.make_request(self._RISKY, {})
            request["tool"] = tool
            result = self._send(request)
            self.assertIsNotNone(result.check_fn, tool)                  # held back for a card
            self.assertIn("save the .blend file", rs.APPROVALS.current().title)
            rs.APPROVALS.end_session()
            request = route.make_request(self._PREFLIGHT, {})
            request["tool"] = tool
            result = self._send(request)
            self.assertEqual(result.response.get("status"), "error", result.response)
            self.assertIn("[Preflight]", result.response.get("message", ""))

    def test_the_routes_name_without_the_key_is_refused(self):
        ran = []
        self.bpy.ran = ran
        code = _MARK + "import bpy\nbpy.ran.append(1)\nresult = {'ran': True}\n"
        for key in (None, "", "guess", route._KEY[:-1] + "x", si.session_token()):
            request = route.make_request(code, {})
            if key is None:
                del request["internal"]
            else:
                request["internal"] = key
            result = self._send(request)
            self.assertEqual(result.response.get("status"), "error", key)
            self.assertIn("only for Coworker's own code", result.response.get("message", ""))
        # The shape the MCP server sends (tool name, args, token; it can't know the key).
        result = self._send({"type": "execute", "code": code, "strict_json": False, "tool": route.TOOL,
                             "args": "{}", "token": si.session_token(), "session": si.SESSION_ID})
        self.assertEqual(result.response.get("status"), "error")
        self.assertEqual(ran, [], "a refused request never runs")

    def test_data_is_only_given_to_the_route(self):
        code = "result = {'has_data': 'data' in globals()}"
        request = {"type": "execute", "code": code, "strict_json": True, "tool": "get_objects_summary",
                   "data": {"x": 1}, "token": si.session_token()}
        self.assertEqual(self._send(request).response.get("result"), {"has_data": False})
        self.assertEqual(self._send(route.make_request(code, {"x": 1})).response.get("result"),
                         {"has_data": True})


class TestModelCantReachTheRoute(unittest.TestCase):

    def test_not_an_mcp_tool(self):
        names = set()
        for path in sorted((_REPO / "mcp" / "blmcp").rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(route.TOOL, text, "{:s} must not know the internal route".format(str(path)))
            for m in re.finditer(r"@mcp\.tool\(", text):
                dm = re.search(r"\bdef\s+([a-z][a-z0-9_]+)\s*\(", text[m.end():])
                if dm:
                    names.add(dm.group(1))
        self.assertIn("execute_blender_code", names)                     # the scan works
        self.assertNotIn(route.TOOL, names)

    def test_not_in_the_agents_tool_list(self):
        source = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
        for start, end in (("_SURFACE_TOOLS = frozenset({", "})"),
                           ("_TOOL_DOMAINS: dict[str, frozenset[str]] = {", "_DOMAIN_KEYWORDS")):
            i = source.index(start)
            self.assertNotIn(route.TOOL, source[i:source.index(end, i)])

    def test_the_key_is_in_memory_only(self):
        self.assertGreaterEqual(len(route._KEY), 32)
        self.assertNotIn(route._KEY, os.environ.values())
        self.assertNotIn(route._KEY, json.dumps(si.describe()))
        source = (_ADDON / "internal_route.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"open\(|write_text|write_private|os\.environ")


class TestSocketRoundTrip(unittest.TestCase):
    """A worker thread's call, served by the bridge's own poll on this (main) thread."""

    def test_call_through_the_bridge_socket(self):
        bpy = _fake_bpy("ASK")
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(sys.modules, {"bpy": bpy}))
            stack.enter_context(mock.patch.object(sandbox, "WeakSandboxForLLM", contextlib.nullcontext))
            stack.enter_context(mock.patch.dict(si._token_cache, {"token": "t" * 32}))
            # Not the user's live-window list.
            stack.enter_context(mock.patch.object(si, "register_live", lambda **_kw: None))
            stack.enter_context(mock.patch.object(si, "unregister_live", lambda: None))
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            bridge.start("127.0.0.1", port)
            stack.callback(bridge.stop)
            stack.callback(rs.APPROVALS.end_session)

            answer: list[str] = []
            code, data = _AC["_build_cleanup_code"](types.SimpleNamespace(
                **{f: (set(_ODD) if f == "object_names" else set()) for f in _FIELDS}))
            worker = threading.Thread(target=lambda: answer.append(route.call(code, data, thread="main")))
            worker.start()
            deadline = time.monotonic() + 10
            while worker.is_alive() and time.monotonic() < deadline:
                bridge.poll()
                worker.join(0.01)
            self.assertFalse(worker.is_alive(), "the call wasn't answered")
            response = json.loads(answer[0])
            self.assertEqual(response.get("status"), "ok", response)
            self.assertEqual(sorted(response["result"]["cleaned"]), sorted(_ODD))
            self.assertIsNone(rs.APPROVALS.current())

    def test_unreachable_bridge_is_an_error_line(self):
        with mock.patch.object(bridge, "_actual_port", 0):
            self.assertTrue(route.call("result = 1").startswith("Error calling " + route.TOOL))


class TestNoMaintenanceCodeAsModelCode(unittest.TestCase):

    def test_no_execute_blender_code_payloads_left(self):
        for name in ("agent_controller.py", "co_work_guard.py"):
            source = (_ADDON / name).read_text(encoding="utf-8")
            self.assertNotRegex(source, r'_call_mcp_tool_sync\(\s*"execute_blender_code"', name)
        source = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("_call_internal("), 9)


if __name__ == "__main__":
    unittest.main()
