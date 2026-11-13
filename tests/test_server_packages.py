# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
#458: Claude Code (External Agent, External Harness) got no Coworker tools on a
fresh install.

The 1.1.42 zip shipped the MCP server's compiled packages (pydantic_core, rpds)
built for Python 3.12; Blender and Bforartists 5.2+ run 3.13.  Only Local's start
noticed and reinstalled them.  External Agent's check looked at files on disk, and
External Harness never checked, so the server an outside app started died on import.

1. The packages are imported once per Python version; a failure reinstalls them,
   success leaves a mark so later starts don't pay for the check.
2. The harness launcher says what to do when the packages don't load.
3. Check Status in External Agent mode doesn't test llama-server or port 9191, and
   preflights the agent's server instead.
4. With the agent off, the mode switch works although the bridge runs.

Run with::

    python -m unittest tests.test_server_packages -v
"""

__all__ = ()

import importlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_serverpkg_pkg"


def _import(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


ac = _import("agent_controller")
TAG = "cp{:d}{:d}".format(*sys.version_info[:2])


class _Deps(unittest.TestCase):
    """A deps cache that holds mcp and passes the layout check."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.deps = self.root / "vendor_deps"
        (self.deps / "mcp").mkdir(parents=True)
        (self.deps / "mcp" / "__init__.py").write_text("")
        for target, value in (("_get_vendor_deps_dir", lambda: self.deps),
                              ("_check_vendor_layout", lambda: []),
                              ("_find_blender_python", lambda: sys.executable),
                              ("_find_vendor_pythonpath", lambda: "")):
            patcher = mock.patch.object(ac, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def probe(self, returncode, stderr=""):
        return mock.patch.object(ac.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], returncode, stdout="", stderr=stderr))


class TestPackagesImportOncePerPython(_Deps):

    def test_packages_that_dont_load_are_reinstalled(self):
        err = "ImportError: cannot import name 'ArgsKwargs' from 'pydantic_core._pydantic_core' (unknown location)"
        with self.probe(1, err), mock.patch.object(ac, "_install_vendor_deps", return_value=True) as install:
            self.assertTrue(ac._ensure_vendor_deps())
        install.assert_called_once()
        self.assertFalse(self.deps.exists(), "the broken packages were removed first")

    def test_packages_that_load_leave_a_mark(self):
        with self.probe(0), mock.patch.object(ac, "_install_vendor_deps") as install:
            self.assertTrue(ac._ensure_vendor_deps())
        install.assert_not_called()
        self.assertEqual((self.deps / ac._DEPS_OK_MARK).read_text(), TAG)
        self.assertTrue(ac.vendor_deps_ready())

    def test_the_mark_skips_the_check(self):
        (self.deps / ac._DEPS_OK_MARK).write_text(TAG)
        with mock.patch.object(ac.subprocess, "run") as run:
            self.assertTrue(ac._ensure_vendor_deps())
        run.assert_not_called()

    def test_another_python_checks_again(self):
        (self.deps / ac._DEPS_OK_MARK).write_text("cp311")
        self.assertFalse(ac.vendor_deps_ready())
        with self.probe(0) as run:
            self.assertTrue(ac._ensure_vendor_deps())
        run.assert_called_once()

    def test_the_check_imports_what_the_server_needs(self):
        with self.probe(0) as run:
            ac._vendor_imports_ok(self.deps)
        code = run.call_args.args[0][-1]
        for module in ("mcp.server.fastmcp", "pydantic_core", "yaml", "blmcp"):
            self.assertIn(module, code)


class TestHarnessReady(_Deps):

    def test_prepare_writes_the_launcher_after_the_packages(self):
        order = []
        with mock.patch.object(ac, "_ensure_vendor_deps", side_effect=lambda: order.append("deps") or True), \
                mock.patch.object(ac, "ensure_harness_launcher", side_effect=lambda: order.append("launcher")):
            self.assertEqual(ac.prepare_harness_server(), (True, ""))
        self.assertEqual(order, ["deps", "launcher"])

    def test_prepare_says_why_it_failed(self):
        with mock.patch.object(ac, "_ensure_vendor_deps", return_value=False):
            ok, problem = ac.prepare_harness_server()
        self.assertFalse(ok)
        self.assertIn("isn't ready", problem)


class TestLauncherExplains(unittest.TestCase):

    def test_missing_packages_print_what_to_do(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        (root / "run_blmcp.py").write_text(ac._HARNESS_LAUNCHER_SOURCE, encoding="utf-8")
        (root / "install.json").write_text(json.dumps({"pythonpath": [], "dll_dirs": []}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        run = subprocess.run([sys.executable, "-I", "-S", str(root / "run_blmcp.py")], capture_output=True,
                             text=True, timeout=60, env=env, cwd=str(root))
        self.assertEqual(run.returncode, 1)
        self.assertIn("press Start: it reinstalls them", run.stderr)
        self.assertNotIn("Traceback", run.stderr)


class TestCheckStatusExternalAgent(unittest.TestCase):

    def setUp(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.addCleanup(self.listener.close)
        self.port = self.listener.getsockname()[1]

    def test_no_llm_or_http_server_checks(self):
        with mock.patch.object(ac, "identify_bridge", return_value={}), \
                mock.patch.object(ac, "validate_mcp_client_config", return_value={"ok": True}) as check, \
                mock.patch.object(ac, "_list_tools_sync") as tools, \
                mock.patch.object(ac.urllib.request, "urlopen") as urlopen:
            result = ac.ping_agent(bridge_port=self.port, operating_mode="EXTERNAL_AGENT")
        tools.assert_not_called()
        urlopen.assert_not_called()
        check.assert_called_once()
        self.assertTrue(result["llm_health"].startswith("N/A"))
        self.assertTrue(result["mcp_server"].startswith("N/A"))
        self.assertEqual(result["harness_config"], "OK")
        self.assertTrue(result["all_ok"])


def _mode_locked(mcp_running=False, bridge=False, agent=None):
    """ui_chat._mode_locked, run on stubs."""
    src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
    start = src.index("def _mode_locked(")
    end = src.index("\n\n\n", start)
    ns = {"agent_controller": types.SimpleNamespace(_agent_state=types.SimpleNamespace(mcp_server_running=mcp_running)),
          "mcp_to_blender_server": types.SimpleNamespace(is_running=lambda: bridge),
          "_acp": agent}
    exec(compile(src[start:end], "ui_chat.py", "exec"), ns)  # noqa: S102
    return ns["_mode_locked"]


class TestModeSwitchWhileOff(unittest.TestCase):
    """With the agent off, the sidebar's mode switch works even if the bridge runs."""

    def prefs(self, mode):
        return types.SimpleNamespace(operating_mode=mode)

    def test_a_running_bridge_alone_does_not_lock_the_mode(self):
        for mode in ("LOCAL_LLM", "REMOTE_API", "EXTERNAL_AGENT"):
            self.assertFalse(_mode_locked(bridge=True)(self.prefs(mode)), mode)

    def test_a_started_coworker_locks_it(self):
        self.assertTrue(_mode_locked(mcp_running=True)(self.prefs("LOCAL_LLM")))
        agent = types.SimpleNamespace(connected=False, connecting=True)
        self.assertTrue(_mode_locked(bridge=True, agent=agent)(self.prefs("EXTERNAL_AGENT")))

    def test_in_external_harness_the_bridge_is_coworker(self):
        self.assertTrue(_mode_locked(bridge=True)(self.prefs("EXTERNAL_HARNESS")))
        self.assertFalse(_mode_locked()(self.prefs("EXTERNAL_HARNESS")))


if __name__ == "__main__":
    unittest.main()
