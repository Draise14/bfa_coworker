# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
First start on a fresh profile (#424).

* External Agent runs the same ``_ensure_vendor_deps()`` as Local before its
  agent app starts the MCP server, and the launcher's ``install.json`` is
  written after that -- so it lists the folders a first-start install made.
* Local's MCP server gets its environment after the vendor deps are resolved
  (and maybe installed), so pywin32's folders are on ``PYTHONPATH`` / ``PATH``
  on the very first start, not only after a restart.

Does not require Blender.  Run with::

    python -m unittest tests.test_first_start -v
"""

__all__ = ()

import ast
import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON_PKG = os.path.join(_REPO, "addon", "bfa_coworker")


def _load_agent_controller():
    """Import ``agent_controller`` through a bare stub package (its ``__init__`` needs bpy)."""
    pkg_name = "_bfacw_pkg_under_test"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [_ADDON_PKG]
        sys.modules[pkg_name] = pkg
    return importlib.import_module(pkg_name + ".agent_controller")


ac = _load_agent_controller()


class _FreshProfile(unittest.TestCase):
    """An empty vendor-deps cache; "installing" creates the layout pip leaves behind."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.deps = self.tmp / "vendor_deps"
        self.deps.mkdir()
        self.calls: list[str] = []
        for target, value in (
                ("_get_vendor_deps_dir", lambda: self.deps),
                ("_HARNESS_DIR", self.tmp / "harness"),
                ("_find_blender_python", lambda: sys.executable),
                ("_vendor_native_compat", lambda _py: True),
                ("_check_vendor_layout", lambda: []),
        ):
            patcher = mock.patch.object(ac, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def install_deps(self) -> bool:
        """What a first-start ``pip install --target`` leaves: mcp plus pywin32's folders."""
        self.calls.append("ensure_vendor_deps")
        for sub in ("mcp", "yaml", "win32/lib", "pywin32_system32"):
            (self.deps / sub).mkdir(parents=True, exist_ok=True)
        (self.deps / "mcp" / "__init__.py").write_text("", encoding="utf-8")
        return True


class TestExternalAgentEntry(_FreshProfile):

    def test_vendor_deps_come_before_the_launcher(self):
        real_launcher = ac.ensure_harness_launcher

        def launcher(*args, **kwargs):
            self.calls.append("ensure_harness_launcher")
            return real_launcher(*args, **kwargs)

        with mock.patch.object(ac, "_ensure_vendor_deps", self.install_deps), \
                mock.patch.object(ac, "ensure_harness_launcher", launcher):
            entry, problem = ac.external_agent_server_entry(blender_port=9876)
        self.assertEqual(problem, "")
        self.assertEqual(self.calls, ["ensure_vendor_deps", "ensure_harness_launcher"])
        self.assertEqual(entry["command"], sys.executable)
        self.assertTrue(entry["args"][0].endswith(ac._HARNESS_LAUNCHER_NAME))
        info = json.loads((self.tmp / "harness" / "install.json").read_text(encoding="utf-8"))
        paths = [os.path.normcase(p) for p in info["pythonpath"]]
        self.assertIn(os.path.normcase(str(self.deps)), paths)
        if sys.platform == "win32":
            # Written after the install, so pywin32's folders are listed.
            self.assertIn(os.path.normcase(str(self.deps / "win32" / "lib")), paths)
            self.assertEqual([os.path.normcase(d) for d in info["dll_dirs"]],
                             [os.path.normcase(str(self.deps / "pywin32_system32"))])

    def test_failed_install_stops_before_the_agent_app(self):
        with mock.patch.object(ac, "_ensure_vendor_deps", lambda: False), \
                mock.patch.object(ac, "_check_vendor_layout", lambda: ["vendor deps missing: x"]):
            entry, problem = ac.external_agent_server_entry()
        self.assertIsNone(entry)
        self.assertIn("vendor deps missing", problem)
        self.assertFalse((self.tmp / "harness" / "install.json").exists())

    def test_pip_installed_server_needs_no_vendor_deps(self):
        with mock.patch.object(ac, "_ensure_vendor_deps", self.install_deps):
            entry, problem = ac.external_agent_server_entry(use_blender_python=False)
        self.assertEqual((problem, self.calls), ("", []))
        self.assertEqual(entry["args"][:2], ["-m", "blmcp"])


class TestFreshInstallCopiesDevBlmcp(_FreshProfile):
    """A source install's first start: pip gives the deps, blmcp still has to be copied in."""

    def run_ensure(self, layout_problems: list[list[str]]) -> tuple[bool, list[str]]:
        def fake_run(argv, **_kwargs):
            if "pip" in argv:
                (self.deps / "mcp").mkdir(exist_ok=True)
                (self.deps / "mcp" / "__init__.py").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        def copy():
            self.calls.append("copy_dev_blmcp")
            return True

        with mock.patch.object(ac.subprocess, "run", fake_run), \
                mock.patch.object(ac, "_check_vendor_layout", mock.Mock(side_effect=layout_problems)), \
                mock.patch.object(ac, "_copy_dev_blmcp_into_vendor", copy), \
                contextlib.redirect_stdout(io.StringIO()):
            return ac._ensure_vendor_deps(), self.calls

    def test_blmcp_copied_on_the_first_start(self):
        ok, calls = self.run_ensure([["blmcp package missing: vendor/blmcp"], []])
        self.assertTrue(ok)
        self.assertEqual(calls, ["copy_dev_blmcp"])

    def test_built_install_needs_no_copy(self):
        ok, calls = self.run_ensure([[]])
        self.assertEqual((ok, calls), (True, []))

    def test_still_incomplete_is_reported(self):
        ok, _calls = self.run_ensure([["blmcp package missing"], ["blmcp package missing"]])
        self.assertFalse(ok)


class TestStartExternalAgentUsesIt(unittest.TestCase):
    """``_start_external_agent`` (ui_chat, needs bpy) builds its entry with the deps check."""

    def test_entry_comes_from_external_agent_server_entry_before_connect(self):
        with open(os.path.join(_ADDON_PKG, "ui_chat.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        start = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "_start_external_agent")
        called = [c.func.attr for c in ast.walk(start)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
        self.assertIn("external_agent_server_entry", called)
        self.assertNotIn("build_mcp_server_entry", called)
        work = next(n for n in ast.walk(start) if isinstance(n, ast.FunctionDef) and n.name == "work")
        order = [c.func.attr for c in sorted(
            (c for c in ast.walk(work) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
             and c.func.attr in ("external_agent_server_entry", "connect")),
            key=lambda c: (c.lineno, c.col_offset))]
        self.assertEqual(order[0], "external_agent_server_entry")
        self.assertIn("connect", order)


class TestLocalFirstStartEnv(_FreshProfile):
    """The MCP server's PYTHONPATH / PATH are built after the deps are in place."""

    def setUp(self) -> None:
        super().setUp()
        self.envs: list[dict] = []
        real_popen = subprocess.Popen

        def fake_popen(argv, **kwargs):
            self.envs.append(dict(kwargs.get("env") or {}))
            kwargs.pop("preexec_fn", None)
            proc = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            self.addCleanup(self._kill, proc)
            return proc

        def resolve():
            self.install_deps()         # the first start installs the deps here
            return (sys.executable, True)

        for obj, target, value in (
                (ac, "_resolve_mcp_python", resolve),
                (ac, "_kill_process_on_port", lambda *_a, **_kw: None),
                (ac, "_find_available_port", lambda port: port),
                (ac, "_wait_for_port", lambda *_a, **_kw: True),
                (ac, "_agent_state", ac.AgentState()),
                (ac, "_mcp_server_process", None),
                (ac.subprocess, "Popen", fake_popen),
                (ac._process_guard, "reap_orphans", lambda *_a, **_kw: None),
                (ac._process_guard, "bind_to_parent", lambda *_a, **_kw: None),
        ):
            patcher = mock.patch.object(obj, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _kill(proc) -> None:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        for pipe in (proc.stdout, proc.stderr):
            if pipe:
                pipe.close()

    def check_env(self) -> None:
        self.assertEqual(len(self.envs), 1)
        env = self.envs[0]
        pythonpath = [os.path.normcase(p) for p in env.get("PYTHONPATH", "").split(os.pathsep)]
        self.assertIn(os.path.normcase(str(self.deps)), pythonpath)
        if sys.platform == "win32":
            self.assertIn(os.path.normcase(str(self.deps / "win32" / "lib")), pythonpath)
            self.assertEqual(os.path.normcase(env["PATH"].split(os.pathsep)[0]),
                             os.path.normcase(str(self.deps / "pywin32_system32")))

    def test_start_mcp_server(self):
        with contextlib.redirect_stdout(io.StringIO()):
            proc = ac.start_mcp_server(port=50998)
        self.assertIsNotNone(proc, ac._agent_state.error)
        self.check_env()

    def test_start_mcp_server_network(self):
        with contextlib.redirect_stdout(io.StringIO()):
            proc = ac.start_mcp_server_network(port=50997)
        self.assertIsNotNone(proc, ac._agent_state.error)
        self.check_env()


if __name__ == "__main__":
    unittest.main()
