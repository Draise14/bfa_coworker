# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Which MCP server Coworker starts (#416).

The add-on starts the server it ships.  A ``bfa-coworker-mcp`` launcher on
PATH is only used when the vendor layout is missing, after a ``--help``
probe, and a launcher that dies during startup falls back to the bundled
server.  Fake launchers are small scripts in a temp folder put first on PATH.

Does not require Blender.  Run with::

    python -m unittest tests.test_mcp_launcher -v
"""

__all__ = ()

import contextlib
import importlib
import io
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


def _write_launcher(folder: Path, help_exit: int, run_exit: int) -> Path:
    """A fake ``bfa-coworker-mcp`` that logs each run to ``runs.txt``.

    ``--help`` exits with *help_exit*, any other call with *run_exit*; a non-zero
    exit prints a launcher error first.
    """
    log = folder / "runs.txt"
    error = "Fatal error in launcher: Unable to create process"
    if sys.platform == "win32":
        def branch(code: int) -> str:
            return "(echo {:s} 1>&2 & exit /b {:d})".format(error, code) if code else "(exit /b 0)"
        path = folder / "bfa-coworker-mcp.cmd"
        path.write_text(
            "@echo off\r\n"
            'echo %*>>"{log}"\r\n'
            'if "%1"=="--help" {help}\r\n'
            "{run}\r\n".format(log=log, help=branch(help_exit), run=branch(run_exit)),
            encoding="utf-8")
    else:
        def branch(code: int) -> str:
            return "{{ echo '{:s}' >&2; exit {:d}; }}".format(error, code) if code else "exit 0"
        path = folder / "bfa-coworker-mcp"
        path.write_text(
            "#!/bin/sh\n"
            'echo "$*" >> "{log}"\n'
            '[ "$1" = "--help" ] && {help}\n'
            "{run}\n".format(log=log, help=branch(help_exit), run=branch(run_exit)),
            encoding="utf-8")
        path.chmod(0o755)
    return path


def _runs(folder: Path) -> list[str]:
    log = folder / "runs.txt"
    return log.read_text(encoding="utf-8").split() if log.is_file() else []


class _LauncherCase(unittest.TestCase):
    """A temp folder first on PATH, a temp vendor-deps cache, a clean probe cache."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        deps = self.tmp / "vendor_deps"
        deps.mkdir()
        for target, value in (
                ("_get_vendor_deps_dir", lambda: deps),
                ("_HARNESS_DIR", self.tmp / "harness"),
                ("_PATH_LAUNCHER_PROBES", {}),
                ("_find_blender_python", lambda: sys.executable),
                ("_vendor_native_compat", lambda _py: True),
        ):
            # create: the probe cache is new in #416, so these tests also run against older code.
            patcher = mock.patch.object(ac, target, value, create=target == "_PATH_LAUNCHER_PROBES")
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")})
        env.start()
        self.addCleanup(env.stop)

    def vendor_layout(self, present: bool) -> None:
        problems = [] if present else ["blmcp package missing: vendor/blmcp"]
        for target, value in (
                ("_check_vendor_layout", lambda: list(problems)),
                ("_ensure_vendor_deps", lambda: True),
        ):
            patcher = mock.patch.object(ac, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def resolve(self) -> tuple[tuple, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = ac._resolve_mcp_python()
        return result, out.getvalue()


class TestResolveMcpPython(_LauncherCase):

    def test_normal_install_never_runs_path_launcher(self):
        launcher = _write_launcher(self.bin, help_exit=0, run_exit=0)
        self.vendor_layout(present=True)
        (exe, use_module), log = self.resolve()
        self.assertEqual((exe, use_module), (sys.executable, True))
        self.assertEqual(_runs(self.bin), [], "a normal install ran the PATH launcher")
        self.assertIn("skipped", log)
        self.assertIn(launcher.name.lower(), log.lower())

    def test_broken_launcher_falls_back_to_bundled_server(self):
        launcher = _write_launcher(self.bin, help_exit=1, run_exit=1)
        self.vendor_layout(present=False)
        (exe, use_module), log = self.resolve()
        self.assertEqual((exe, use_module), (sys.executable, True))
        self.assertIn("skipped", log)
        self.assertIn(launcher.name.lower(), log.lower())
        self.assertIn("--help exited with code 1", log)
        self.assertIn("Fatal error in launcher", log)

    def test_working_launcher_used_only_without_vendor_layout(self):
        launcher = _write_launcher(self.bin, help_exit=0, run_exit=0)
        self.vendor_layout(present=False)
        (exe, use_module), _log = self.resolve()
        self.assertFalse(use_module)
        self.assertEqual(Path(exe).name.lower(), launcher.name.lower())

    def test_probe_runs_once_per_session(self):
        _write_launcher(self.bin, help_exit=1, run_exit=1)
        self.vendor_layout(present=False)
        self.resolve()
        self.resolve()
        self.assertEqual(_runs(self.bin), ["--help"])

    def test_probe_timeout_skips_the_launcher(self):
        launcher = _write_launcher(self.bin, help_exit=0, run_exit=0)
        self.vendor_layout(present=False)
        with mock.patch.object(ac.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(str(launcher), 5)):
            (exe, use_module), log = self.resolve()
        self.assertEqual((exe, use_module), (sys.executable, True))
        self.assertIn("no answer within 5 s", log)


class TestStartFallsBack(_LauncherCase):
    """A launcher that passes ``--help`` but dies on start: one retry with the bundled server."""

    def setUp(self) -> None:
        super().setUp()
        self.vendor_layout(present=False)
        self.launched: list[list[str]] = []
        real_popen = subprocess.Popen

        def fake_popen(argv, **kwargs):
            if "--help" not in argv:    # the probe runs through Popen too
                self.launched.append(list(argv))
            if "-m" in argv:        # the bundled server: stands in for a healthy one
                argv = [sys.executable, "-c", "import time; time.sleep(30)"]
            kwargs.pop("preexec_fn", None)
            proc = real_popen(argv, **kwargs)
            self.addCleanup(self._kill, proc)
            return proc

        for target, value in (
                ("_build_mcp_env", lambda **_kw: dict(os.environ)),
                ("_kill_process_on_port", lambda *_a, **_kw: None),
                ("_find_available_port", lambda port: port),
                ("_wait_for_port", self._fake_wait_for_port),
        ):
            patcher = mock.patch.object(ac, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in (("Popen", fake_popen),):
            patcher = mock.patch.object(ac.subprocess, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target in ("reap_orphans", "bind_to_parent"):
            patcher = mock.patch.object(ac._process_guard, target, lambda *_a, **_kw: None)
            patcher.start()
            self.addCleanup(patcher.stop)
        state = mock.patch.object(ac, "_agent_state", ac.AgentState())
        state.start()
        self.addCleanup(state.stop)
        proc = mock.patch.object(ac, "_mcp_server_process", None)
        proc.start()
        self.addCleanup(proc.stop)

    @staticmethod
    def _fake_wait_for_port(*_args, proc=None, **_kwargs) -> bool:
        """The port is "ready" while the process is still alive after a moment."""
        if proc is None:
            return True
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return True
        return False

    @staticmethod
    def _kill(proc) -> None:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        for pipe in (proc.stdout, proc.stderr):
            if pipe:
                pipe.close()

    def test_launcher_dying_on_start_retries_with_bundled_server(self):
        launcher = _write_launcher(self.bin, help_exit=0, run_exit=1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            proc = ac.start_mcp_server(port=50999)
        self.assertIsNotNone(proc, ac._agent_state.error)
        self.assertEqual(len(self.launched), 2)
        self.assertEqual(Path(self.launched[0][0]).name.lower(), launcher.name.lower())
        self.assertEqual(self.launched[1][:3], [sys.executable, "-m", "blmcp"])
        log = out.getvalue()
        self.assertIn("exited during startup", log)
        self.assertIn(launcher.name.lower(), log.lower())
        # It is skipped for the rest of the session.
        self.assertIn("exited during startup", ac._PATH_LAUNCHER_PROBES[self.launched[0][0]])

    def test_error_names_the_launcher_when_the_retry_fails_too(self):
        launcher = _write_launcher(self.bin, help_exit=0, run_exit=1)
        with mock.patch.object(ac, "_ensure_vendor_deps", lambda: False), \
                contextlib.redirect_stdout(io.StringIO()):
            proc = ac.start_mcp_server(port=50999)
        self.assertIsNone(proc)
        self.assertEqual(len(self.launched), 1, "retried more than once")
        self.assertIn(launcher.name.lower(), ac._agent_state.error.lower())
        self.assertIn("failed first", ac._agent_state.error)


class TestExternalAgentEntry(_LauncherCase):
    """External Agent hands its app a stdio entry: the bundled server, never the PATH launcher."""

    def test_broken_launcher_on_path_is_not_used(self):
        _write_launcher(self.bin, help_exit=1, run_exit=1)
        entry = ac.build_mcp_server_entry(use_blender_python=True)
        self.assertEqual(entry["command"], sys.executable)
        self.assertTrue(entry["args"][0].endswith(ac._HARNESS_LAUNCHER_NAME))
        self.assertEqual(_runs(self.bin), [])


if __name__ == "__main__":
    unittest.main()
