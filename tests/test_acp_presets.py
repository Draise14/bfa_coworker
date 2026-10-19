# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for External Agent presets and detection (acp_presets.py, "Agent 3").

1. npm ``.cmd`` shims resolve to what they run: a native ``.exe`` directly,
   a ``.js`` script through ``node`` (the shim's own node.exe first).
2. ``detect`` reports not installed / needs Node.js, reads the version, picks
   the ACP flag a CLI lists in ``--help``, and refuses one that lists none.
3. Presets are consistent; generic presets split a command line; isolation
   switches are applied only with "Only Coworker tools".

Run with::

    python -m unittest tests.test_acp_presets -v
"""

__all__ = ()

import importlib
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_presets_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".acp_presets")


ap = _load()

# The shim npm wrote for opencode-ai (seen in the ACP spike).
_EXE_SHIM = ('@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\n'
             'CALL :find_dp0\r\n"%dp0%\\..\\opencode-ai\\bin\\opencode.exe"   %*\r\n')
# npm's standard shim for a package whose bin is a .js file.
_NODE_SHIM = ('@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\n'
              'CALL :find_dp0\r\n\r\nIF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n'
              '  SET "_prog=node"\r\n  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\nendLocal & goto #_undefined_# 2>NUL '
              '|| title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\@agentclientprotocol\\claude-agent-acp'
              '\\dist\\index.js" %*\r\n')


def _proc(code=0, out=b"", err=b""):
    return subprocess.CompletedProcess([], code, out, err)


class TestShims(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _file(self, rel, text=""):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_exe_shim_resolves_to_native_exe(self):
        shim = self._file("bin/opencode.cmd", _EXE_SHIM)
        exe = self._file("opencode-ai/bin/opencode.exe", "x")
        argv = ap.resolve_command("opencode", which=lambda n: str(shim) if n == "opencode" else None)
        self.assertEqual(argv, [str(exe.resolve())])

    def test_node_shim_uses_node_and_script(self):
        shim = self._file("npm/claude-agent-acp.cmd", _NODE_SHIM)
        script = self._file("npm/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js", "x")
        which = {"claude-agent-acp": str(shim), "node": "C:/node/node.exe"}.get
        self.assertEqual(ap.resolve_command("claude-agent-acp", which=which),
                         ["C:/node/node.exe", str(script.resolve())])
        local_node = self._file("npm/node.exe", "x")  # the shim's own node wins
        self.assertEqual(ap.resolve_command("claude-agent-acp", which=which)[0], str(local_node))

    def test_unparsable_shim_and_plain_binaries_pass_through(self):
        shim = self._file("x/thing.cmd", "@echo hello")
        self.assertEqual(ap.resolve_command("thing", which=lambda n: str(shim)), [str(shim)])
        self.assertEqual(ap.resolve_command("goose", which=lambda n: "/usr/bin/goose"), ["/usr/bin/goose"])
        self.assertIsNone(ap.resolve_command("nope", which=lambda n: None))


class TestDetect(unittest.TestCase):

    def test_not_installed_and_node_missing(self):
        det = ap.detect(ap.preset_by_id("claude_code"), which=lambda n: None)
        self.assertFalse(det.installed)
        self.assertIn("not installed", det.problem)
        self.assertIn("Node.js", det.problem)
        self.assertIn("npm install -g @agentclientprotocol/claude-agent-acp", det.hint)

    def test_ready_with_version(self):
        calls = []

        def run(argv, **_kw):
            calls.append(argv)
            return _proc(0, b"1.19.3\n")

        det = ap.detect(ap.preset_by_id("opencode"), which=lambda n: "/bin/opencode", run=run)
        self.assertTrue(det.ready)
        self.assertEqual(det.version, "1.19.3")
        self.assertEqual(det.argv, ["/bin/opencode", "acp"])
        self.assertEqual(calls, [["/bin/opencode", "--version"]])

    def test_acp_flag_from_help(self):
        def run(argv, **_kw):
            if argv[-1] == "--help":
                return _proc(0, b"  --experimental-acp   Start the agent in ACP mode\n")
            return _proc(0, b"0.64.0\n")

        det = ap.detect(ap.preset_by_id("gemini"), which=lambda n: "/bin/gemini", run=run)
        self.assertTrue(det.ready)
        self.assertEqual(det.argv, ["/bin/gemini", "--experimental-acp"])

    def test_cli_without_acp_option_is_refused(self):
        det = ap.detect(ap.preset_by_id("copilot"), which=lambda n: "/bin/copilot",
                        run=lambda argv, **_kw: _proc(0, b"usage: copilot [options]\n"))
        self.assertTrue(det.installed)
        self.assertFalse(det.ready)
        self.assertIn("ACP option", det.problem)

    def test_run_errors_never_raise(self):
        def run(argv, **_kw):
            raise OSError("access denied")

        det = ap.detect(ap.preset_by_id("opencode"), which=lambda n: "/bin/opencode", run=run)
        self.assertTrue(det.ready)          # still launchable; the version is just unknown
        self.assertEqual(det.version, "")


class TestPresets(unittest.TestCase):

    def test_presets_consistent(self):
        ids = [p.id for p in ap.PRESETS]
        self.assertEqual(len(ids), len(set(ids)))
        for p in ap.PRESETS:
            self.assertTrue(p.name and p.command and p.install_hint and p.npm_package, p.id)
            self.assertIn(p.maturity, ("supported", "preview"))
        self.assertEqual(ap.preset_by_id("opencode").maturity, "supported")
        # Deprecated Zed-hosted adapter names must not come back (npm, 2026-10-19).
        self.assertNotIn("@zed-industries", " ".join(p.npm_package for p in ap.PRESETS))

    def test_generic_preset(self):
        p = ap.generic_preset("goose acp --verbose")
        self.assertEqual((p.command, p.args), ("goose", ("acp", "--verbose")))
        with self.assertRaises(ValueError):
            ap.generic_preset("   ")

    def test_isolation_env(self):
        oc = ap.preset_by_id("opencode")
        self.assertEqual(ap.launch_env(oc)["OPENCODE_DISABLE_EXTERNAL_SKILLS"], "1")
        self.assertEqual(ap.launch_env(oc, only_coworker_tools=False), {})
        self.assertEqual(ap.launch_env(oc, extra={"BFACW_TOKEN": "t"})["BFACW_TOKEN"], "t")


if __name__ == "__main__":
    unittest.main()
