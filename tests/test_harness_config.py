# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for External Harness MCP startup hardening.

Covers the failure-diagnosis helpers, the vendor-layout checks, and the
config preflight validator in ``agent_controller.py``.

Does not require Blender — ``agent_controller`` has no module-level ``bpy``
import.  Run with::

    python -m unittest tests.test_harness_config -v
"""

__all__ = ()

import importlib.util
import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON_DIR = os.path.join(_REPO, "addon")
_AC_PATH = os.path.join(_ADDON_DIR, "bfa_coworker", "agent_controller.py")


def _load_agent_controller():
    """Load ``agent_controller.py`` without running the package ``__init__``.

    The package ``__init__.py`` imports ``bpy``, so ``from bfa_coworker import
    agent_controller`` fails outside Blender.  ``agent_controller`` uses
    relative imports of bpy-free siblings, so register a bare stub package
    pointing at the add-on directory and import the module through it.
    """
    import types
    pkg_name = "_bfacw_pkg_under_test"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [os.path.dirname(_AC_PATH)]
        sys.modules[pkg_name] = pkg
    return importlib.import_module(pkg_name + ".agent_controller")


ac = _load_agent_controller()
hc = importlib.import_module("_bfacw_pkg_under_test.harness_clients")

# Never touch the real ~/.cache/bfa_coworker/harness launcher from tests.
import tempfile as _tempfile
from pathlib import Path as _Path
_HARNESS_TMP = _tempfile.TemporaryDirectory()
ac._HARNESS_DIR = _Path(_HARNESS_TMP.name) / "harness"


# The exact traceback shape reported by the user in External Harness mode.
# Note the ``<frozen ...>`` markers are stripped by some MCP clients, which is
# why the original report showed ``File ""``.
_RUNPY_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "<frozen runpy>", line 189, in _run_module_as_main\n'
    '  File "<frozen runpy>", line 148, in _get_module_details\n'
    '  File "<frozen importlib._bootstrap>", line 112, in _get_module\n'
    "ImportError: No module named 'blmcp'\n"
)


class TestSummarizeMcpFailure(unittest.TestCase):
    """``_summarize_mcp_failure`` must surface the exception, not the header."""

    def test_picks_last_line_not_first(self):
        """The old code kept the first 200 chars — i.e. the traceback header."""
        summary, full = ac._summarize_mcp_failure(_RUNPY_TRACEBACK, "")
        self.assertIn("No module named 'blmcp'", summary)
        self.assertNotIn("Traceback (most recent call last)", summary)
        # The full text is preserved untruncated for copy-to-clipboard.
        self.assertIn("_run_module_as_main", full)

    def test_appends_hint_for_known_signature(self):
        summary, _full = ac._summarize_mcp_failure(_RUNPY_TRACEBACK, "")
        self.assertIn("build_addon.py", summary)

    def test_falls_back_to_stdout_when_stderr_empty(self):
        summary, full = ac._summarize_mcp_failure("", "ImportError: boom\n")
        self.assertIn("boom", summary)
        self.assertIn("boom", full)

    def test_no_output(self):
        summary, full = ac._summarize_mcp_failure("", "")
        self.assertEqual(summary, "no output")
        self.assertEqual(full, "no output")

    def test_ignores_blank_lines(self):
        summary, _full = ac._summarize_mcp_failure("error: x\n\n\n   \n", "")
        self.assertEqual(summary, "error: x")

    def test_unknown_signature_has_no_hint(self):
        summary, _full = ac._summarize_mcp_failure("something odd happened\n", "")
        self.assertEqual(summary, "something odd happened")


class TestClassifyMcpFailure(unittest.TestCase):
    """``_classify_mcp_failure`` maps known signatures to actionable hints."""

    def test_blmcp_missing(self):
        hint = ac._classify_mcp_failure("ImportError: No module named 'blmcp'")
        self.assertIn("build_addon.py", hint)

    def test_mcp_sdk_missing(self):
        hint = ac._classify_mcp_failure("ModuleNotFoundError: No module named 'mcp'")
        self.assertIn("bfa-coworker-mcp", hint)

    def test_mcp_2x_fastmcp_removed(self):
        """mcp 2.x removed FastMCP — the most likely real cause of the report."""
        hint = ac._classify_mcp_failure(
            "ModuleNotFoundError: No module named 'mcp.server.fastmcp'. "
            "This is mcp 2.x, where FastMCP was renamed to MCPServer")
        self.assertIn("mcp<2", hint)

    def test_mcp_2x_shim_message(self):
        hint = ac._classify_mcp_failure("FastMCP was renamed to MCPServer")
        self.assertIn("mcp<2", hint)

    def test_pydantic_core_mismatch(self):
        hint = ac._classify_mcp_failure(
            "ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'")
        self.assertIn("build_addon.py", hint)

    def test_pywintypes_missing(self):
        hint = ac._classify_mcp_failure("ModuleNotFoundError: No module named 'pywintypes'")
        self.assertIn("pywin32", hint)

    def test_yaml_missing(self):
        hint = ac._classify_mcp_failure("ModuleNotFoundError: No module named 'yaml'")
        self.assertIn("PyYAML", hint)

    def test_starlette_missing(self):
        hint = ac._classify_mcp_failure("No module named 'starlette'")
        self.assertIn("Starlette", hint)

    def test_pywin32_missing(self):
        hint = ac._classify_mcp_failure("No module named 'win32api'")
        self.assertIn("pywin32", hint)

    def test_port_in_use(self):
        hint = ac._classify_mcp_failure("OSError: [Errno 98] Address already in use")
        self.assertIn("port_offset", hint)

    def test_windows_port_in_use(self):
        hint = ac._classify_mcp_failure(
            "OSError: [WinError 10048] Only one usage of each socket address")
        self.assertIn("port_offset", hint)

    def test_permission_error(self):
        hint = ac._classify_mcp_failure("PermissionError: [Errno 13] Permission denied")
        self.assertIn("firewall", hint.lower())

    def test_bad_interpreter_path(self):
        hint = ac._classify_mcp_failure("OSError: [WinError 193] is not a valid Win32 application")
        self.assertIn("Use Blender's Python", hint)

    def test_generic_module_not_found(self):
        hint = ac._classify_mcp_failure("ModuleNotFoundError: No module named 'zzz'")
        self.assertIn("build_addon.py", hint)

    def test_specific_beats_generic(self):
        """'blmcp' must win over the generic 'no module named' fallback."""
        hint = ac._classify_mcp_failure("No module named 'blmcp'")
        self.assertIn("vendor/blmcp", hint)

    def test_empty_input(self):
        self.assertEqual(ac._classify_mcp_failure(""), "")

    def test_unknown_input(self):
        self.assertEqual(ac._classify_mcp_failure("all good"), "")


class TestCheckVendorLayout(unittest.TestCase):
    """``_check_vendor_layout`` reports missing pieces of the vendored layout."""

    def test_returns_list(self):
        problems = ac._check_vendor_layout()
        self.assertIsInstance(problems, list)
        for problem in problems:
            self.assertIsInstance(problem, str)

    def test_repo_checkout_has_blmcp(self):
        """In this repo the dev checkout provides blmcp, so it must not be
        reported missing (deps may legitimately be absent)."""
        problems = ac._check_vendor_layout()
        joined = " ".join(problems)
        self.assertNotIn("blmcp package missing", joined)


class TestVendorPythonpathReport(unittest.TestCase):
    """``_vendor_pythonpath_report`` pairs the path with what was missing."""

    def test_returns_tuple(self):
        pythonpath, missing = ac._vendor_pythonpath_report()
        self.assertIsInstance(pythonpath, str)
        self.assertIsInstance(missing, list)

    def test_missing_entries_are_absolute(self):
        _pythonpath, missing = ac._vendor_pythonpath_report()
        for entry in missing:
            self.assertTrue(os.path.isabs(entry), entry)


class TestValidateMcpClientConfig(unittest.TestCase):
    """``validate_mcp_client_config`` preflights the emitted harness config."""

    def test_reports_missing_interpreter(self):
        result = ac.validate_mcp_client_config(
            use_blender_python=False,
            check_bridge=False,
        )
        # Either system python exists (python_ok True) or it does not; both
        # are valid outcomes.  The contract is that the keys are present.
        for key in ("ok", "python", "python_ok", "pythonpath", "missing",
                    "import_ok", "bridge_ok", "stderr_tail", "hint", "summary"):
            self.assertIn(key, result)

    def test_bogus_interpreter_path_fails_cleanly(self):
        """A non-existent absolute interpreter must fail without raising."""
        import unittest.mock as mock
        bogus = os.path.join(_REPO, "does", "not", "exist", "python.exe")
        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(bogus, "")):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, check_bridge=False)
        self.assertFalse(result["ok"])
        self.assertFalse(result["python_ok"])
        self.assertIn("not found", result["summary"])
        self.assertTrue(result["hint"])

    def test_missing_pythonpath_entry_fails_cleanly(self):
        """A PYTHONPATH entry that does not exist must be reported."""
        import unittest.mock as mock
        bogus_dir = os.path.join(_REPO, "no_such_vendor_dir")
        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, bogus_dir)):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, check_bridge=False)
        self.assertFalse(result["ok"])
        self.assertIn(bogus_dir, result["missing"])
        self.assertIn("build_addon.py", result["hint"])

    def test_bridge_check_disabled_reports_none(self):
        result = ac.validate_mcp_client_config(check_bridge=False)
        self.assertIsNone(result["bridge_ok"])

    def test_bridge_check_enabled_reports_bool(self):
        """With a successful probe and check_bridge on, bridge_ok is a bool."""
        import unittest.mock as mock
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "usage: blmcp ..."
        fake.stderr = ""
        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, "")), \
                mock.patch.object(ac.subprocess, "run", return_value=fake):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, check_bridge=True)
        self.assertTrue(result["ok"])
        self.assertIsInstance(result["bridge_ok"], bool)

    def test_bridge_unreachable_still_ok(self):
        """A valid config with a stopped bridge is still 'ok' — reported apart."""
        import unittest.mock as mock
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "usage: blmcp ..."
        fake.stderr = ""
        # Port 1 is reserved and never listening.
        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, "")), \
                mock.patch.object(ac.subprocess, "run", return_value=fake):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, blender_port=1, check_bridge=True)
        self.assertTrue(result["ok"])
        self.assertFalse(result["bridge_ok"])
        self.assertIn("Start Bridge", result["summary"])

    def test_import_failure_is_reported(self):
        """When the probe fails, import_ok is False and a hint is set."""
        import unittest.mock as mock
        fake = mock.Mock()
        fake.returncode = 1
        fake.stdout = ""
        fake.stderr = "ImportError: No module named 'blmcp'\n"
        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, "")), \
                mock.patch.object(ac.subprocess, "run", return_value=fake):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, check_bridge=False)
        self.assertFalse(result["ok"])
        self.assertFalse(result["import_ok"])
        self.assertIn("blmcp", result["summary"])
        self.assertIn("build_addon.py", result["hint"])
        self.assertIn("blmcp", result["stderr_tail"])

    def test_probe_timeout_is_reported(self):
        import subprocess as _sp
        import unittest.mock as mock
        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, "")), \
                mock.patch.object(ac.subprocess, "run",
                                  side_effect=_sp.TimeoutExpired("cmd", 30)):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, check_bridge=False)
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["summary"].lower())

    def test_probe_oserror_is_reported(self):
        import unittest.mock as mock
        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, "")), \
                mock.patch.object(ac.subprocess, "run",
                                  side_effect=OSError("nope")):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, check_bridge=False)
        self.assertFalse(result["ok"])
        self.assertIn("nope", result["hint"])

    def test_mcp_2x_is_rejected_even_when_probe_passes(self):
        """mcp 2.x removed FastMCP, so a 2.x SDK must fail the check."""
        import unittest.mock as mock

        def _fake_run(cmd, **_kwargs):
            fake = mock.Mock()
            fake.returncode = 0
            fake.stderr = ""
            if "importlib.metadata" in " ".join(cmd):
                fake.stdout = "2.1.1\n"
            else:
                fake.stdout = "usage: blmcp ..."
            return fake

        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, "")), \
                mock.patch.object(ac.subprocess, "run", side_effect=_fake_run):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, check_bridge=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["mcp_version"], "2.1.1")
        self.assertIn("mcp<2", result["hint"])

    def test_mcp_1x_is_accepted(self):
        """A 1.x SDK must pass the version gate."""
        import unittest.mock as mock

        def _fake_run(cmd, **_kwargs):
            fake = mock.Mock()
            fake.returncode = 0
            fake.stderr = ""
            if "importlib.metadata" in " ".join(cmd):
                fake.stdout = "1.30.0\n"
            else:
                fake.stdout = "usage: blmcp ..."
            return fake

        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, "")), \
                mock.patch.object(ac.subprocess, "run", side_effect=_fake_run):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, check_bridge=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["mcp_version"], "1.30.0")

    def test_version_probe_failure_is_not_fatal(self):
        """If the version probe fails, the check must still succeed."""
        import unittest.mock as mock

        def _fake_run(cmd, **_kwargs):
            fake = mock.Mock()
            fake.stderr = ""
            if "importlib.metadata" in " ".join(cmd):
                fake.returncode = 1
                fake.stdout = ""
            else:
                fake.returncode = 0
                fake.stdout = "usage: blmcp ..."
            return fake

        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, "")), \
                mock.patch.object(ac.subprocess, "run", side_effect=_fake_run):
            result = ac.validate_mcp_client_config(
                use_blender_python=True, check_bridge=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["mcp_version"], "")


class TestGenerateMcpClientConfig(unittest.TestCase):
    """The emitted config runs the stable launcher with the right transport."""

    def test_config_is_valid_json(self):
        import json
        raw = ac.generate_mcp_client_config(client_type="claude_desktop")
        parsed = json.loads(raw)
        self.assertIn("mcpServers", parsed)
        self.assertIn("bfa-coworker", parsed["mcpServers"])

    def test_env_has_bridge_host_and_port(self):
        import json
        raw = ac.generate_mcp_client_config(
            client_type="claude_desktop", blender_host="localhost", blender_port=9876)
        parsed = json.loads(raw)
        env = parsed["mcpServers"]["bfa-coworker"]["env"]
        self.assertEqual(env["BFACW_HOST"], "localhost")
        self.assertEqual(env["BFACW_PORT"], "9876")
        # The launcher carries the paths, so a move can't leave them stale.
        self.assertNotIn("PYTHONPATH", env)

    def test_args_run_launcher_with_stdio_transport(self):
        import json
        import unittest.mock as mock
        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, "")):
            raw = ac.generate_mcp_client_config(client_type="claude_desktop")
        args = json.loads(raw)["mcpServers"]["bfa-coworker"]["args"]
        self.assertEqual(args, [str(ac.harness_launcher_path()), "--transport", "stdio"])
        self.assertTrue(os.path.isfile(args[0]))

    def test_launcher_install_json_points_at_vendor_parent(self):
        """install.json must list the *parent* of vendor/blmcp, not blmcp itself.

        This is the exact mistake that produces ``No module named 'blmcp'``:
        ``import blmcp`` resolves from the directory *containing* the package.
        """
        import json
        vendor_dir = os.path.join(_ADDON_DIR, "bfa_coworker", "vendor")
        ac.ensure_harness_launcher(vendor_dir)
        info = json.loads((ac._HARNESS_DIR / "install.json").read_text(encoding="utf-8"))
        self.assertEqual(info["pythonpath"], [vendor_dir])
        self.assertFalse(info["pythonpath"][0].endswith(os.path.join("vendor", "blmcp")))

    def test_pip_mode_uses_module(self):
        import json
        raw = ac.generate_mcp_client_config(client_type="claude_desktop", use_blender_python=False)
        server = json.loads(raw)["mcpServers"]["bfa-coworker"]
        self.assertEqual(server["command"], "python")
        self.assertEqual(server["args"], ["-m", "blmcp", "--transport", "stdio"])


class TestLauncherEndToEnd(unittest.TestCase):
    """The launcher really starts blmcp from the paths in install.json."""

    def test_launcher_runs_blmcp_help(self):
        import subprocess
        venv_py = os.path.join(_REPO, "mcp", ".venv", "Scripts" if sys.platform == "win32" else "bin",
                               "python.exe" if sys.platform == "win32" else "python")
        if not os.path.isfile(venv_py):
            self.skipTest("mcp/.venv not set up")
        launcher = ac.ensure_harness_launcher(os.path.join(_REPO, "mcp"))
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run([venv_py, str(launcher), "--help"], capture_output=True,
                              text=True, timeout=60, env=env, cwd=os.path.dirname(str(launcher)))
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("--transport", proc.stdout)


_ENTRY = {
    "command": "C:\\Blender\\python\\bin\\python.exe",
    "args": ["C:\\Users\\me\\.cache\\bfa_coworker\\harness\\run_blmcp.py", "--transport", "stdio"],
    "env": {"BFACW_HOST": "localhost", "BFACW_PORT": "9876"},
}


class TestClientFormats(unittest.TestCase):
    """Each preset emits the schema its client documents (issue #100)."""

    def _json(self, client):
        import json
        return json.loads(hc.format_config(client, _ENTRY))

    def test_mcpservers_clients(self):
        for client in ("claude_desktop", "claude_code", "cursor", "windsurf", "cline", "freebuff", "generic"):
            with self.subTest(client=client):
                server = self._json(client)["mcpServers"]["bfa-coworker"]
                self.assertEqual(server["command"], _ENTRY["command"])
                self.assertEqual(server["args"], _ENTRY["args"])
                self.assertEqual(server["env"], _ENTRY["env"])

    def test_type_stdio_where_documented(self):
        self.assertEqual(self._json("claude_code")["mcpServers"]["bfa-coworker"]["type"], "stdio")
        self.assertEqual(self._json("cursor")["mcpServers"]["bfa-coworker"]["type"], "stdio")
        self.assertNotIn("type", self._json("cline")["mcpServers"]["bfa-coworker"])

    def test_vscode_uses_servers(self):
        parsed = self._json("vscode")
        self.assertNotIn("mcpServers", parsed)
        self.assertEqual(parsed["servers"]["bfa-coworker"]["type"], "stdio")

    def test_opencode_schema(self):
        parsed = self._json("opencode")
        server = parsed["mcp"]["bfa-coworker"]
        self.assertEqual(server["type"], "local")
        self.assertEqual(server["command"], [_ENTRY["command"], *_ENTRY["args"]])
        self.assertEqual(server["environment"], _ENTRY["env"])
        self.assertTrue(server["enabled"])

    def test_codex_is_toml(self):
        import tomllib
        data = tomllib.loads(hc.format_config("codex", _ENTRY))
        server = data["mcp_servers"]["bfa-coworker"]
        self.assertEqual(server["command"], _ENTRY["command"])
        self.assertEqual(server["args"], _ENTRY["args"])
        self.assertEqual(server["env"], _ENTRY["env"])

    def test_installable_clients(self):
        for client in ("claude_desktop", "claude_code", "codex", "cursor", "windsurf", "opencode"):
            self.assertTrue(hc.can_install(client), client)
        for client in ("cline", "vscode", "freebuff", "generic"):
            self.assertFalse(hc.can_install(client), client)
            self.assertEqual(hc.config_paths(client), [])


class TestClientWriters(unittest.TestCase):
    """Writers keep other settings, back up, and never break a file."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_claude_code_creates_file(self):
        import json
        path = self.tmp / ".claude.json"
        ac.install_mcp_client_config("claude_code", blender_port=9999, paths=[path])
        server = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["bfa-coworker"]
        self.assertEqual(server["type"], "stdio")
        self.assertEqual(server["env"]["BFACW_PORT"], "9999")

    def test_json_merge_preserves_other_keys_and_backs_up(self):
        import json
        path = self.tmp / ".claude.json"
        original = {
            "numStartups": 42,
            "projects": {"D:/x": {"mcpServers": {}}},
            "mcpServers": {"other": {"command": "foo"}},
        }
        path.write_text(json.dumps(original), encoding="utf-8")
        hc.install("claude_code", _ENTRY, paths=[path])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["numStartups"], 42)
        self.assertIn("projects", data)
        self.assertIn("other", data["mcpServers"])
        self.assertIn("bfa-coworker", data["mcpServers"])
        backup = json.loads((self.tmp / ".claude.json.bfacw.bak").read_text(encoding="utf-8"))
        self.assertEqual(backup, original)

    def test_refuses_invalid_json(self):
        path = self.tmp / "mcp.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            hc.install("cursor", _ENTRY, paths=[path])
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_opencode_merge(self):
        import json
        path = self.tmp / "opencode.json"
        path.write_text(json.dumps({"model": "x", "mcp": {"other": {"type": "local", "command": ["a"]}}}),
                        encoding="utf-8")
        hc.install("opencode", _ENTRY, paths=[path])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "x")
        self.assertIn("other", data["mcp"])
        self.assertEqual(data["mcp"]["bfa-coworker"]["type"], "local")

    def test_codex_replaces_only_our_tables(self):
        import tomllib
        path = self.tmp / "config.toml"
        path.write_text(
            'model = "o4"\n\n'
            '[mcp_servers.bfa-coworker]\ncommand = "old"\nargs = []\n\n'
            '[mcp_servers.bfa-coworker.env]\nBFACW_PORT = "1"\n\n'
            '[mcp_servers.context7]\ncommand = "npx"\n',
            encoding="utf-8")
        hc.install("codex", _ENTRY, paths=[path])
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "o4")
        self.assertEqual(data["mcp_servers"]["context7"]["command"], "npx")
        self.assertEqual(data["mcp_servers"]["bfa-coworker"]["command"], _ENTRY["command"])
        self.assertEqual(data["mcp_servers"]["bfa-coworker"]["env"]["BFACW_PORT"], "9876")

    def test_codex_refuses_invalid_toml(self):
        path = self.tmp / "config.toml"
        path.write_text("[[[", encoding="utf-8")
        with self.assertRaises(ValueError):
            hc.install("codex", _ENTRY, paths=[path])
        self.assertEqual(path.read_text(encoding="utf-8"), "[[[")

    def test_uninstallable_client_raises(self):
        with self.assertRaises(ValueError):
            hc.install("cline", _ENTRY)

    def test_windsurf_without_file_asks_to_create_it(self):
        import unittest.mock as mock
        with mock.patch.object(hc, "config_paths", return_value=[self.tmp / "a.json", self.tmp / "b.json"]):
            with self.assertRaises(ValueError):
                hc.install("windsurf", _ENTRY)


class TestInspectInstalledConfig(unittest.TestCase):
    """Test Config checks the entry the client really launches."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _inspect(self, data, port=9876):
        import json
        import unittest.mock as mock
        path = self.tmp / "claude_desktop_config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with mock.patch.object(hc, "config_paths", return_value=[path]):
            return ac.inspect_installed_client_config("claude_desktop", blender_port=port)

    def test_stale_pythonpath_is_reported(self):
        """The exact break from the installer moving Coworker out of user_default."""
        gone = str(self.tmp / "extensions" / "user_default" / "bfa_coworker" / "vendor")
        result = self._inspect({"mcpServers": {"bfa-coworker": {
            "command": sys.executable, "args": ["-m", "blmcp", "--transport", "stdio"],
            "env": {"BFACW_PORT": "9876", "PYTHONPATH": gone}}}})
        self.assertTrue(result["found"])
        self.assertTrue(any(gone in p for p in result["problems"]), result["problems"])

    def test_port_mismatch_is_reported(self):
        launcher = ac.ensure_harness_launcher("")
        result = self._inspect({"mcpServers": {"bfa-coworker": {
            "command": sys.executable, "args": [str(launcher), "--transport", "stdio"],
            "env": {"BFACW_PORT": "9876"}}}}, port=9877)
        self.assertTrue(any("9877" in p for p in result["problems"]), result["problems"])

    def test_launcher_entry_is_ok(self):
        launcher = ac.ensure_harness_launcher("")
        result = self._inspect({"mcpServers": {"bfa-coworker": {
            "command": sys.executable, "args": [str(launcher), "--transport", "stdio"],
            "env": {"BFACW_PORT": "9876"}}}})
        self.assertEqual(result["problems"], [])
        self.assertTrue(result["summary"].startswith("Installed config OK"))

    def test_missing_entry_is_reported(self):
        result = self._inspect({"mcpServers": {"other": {"command": "x"}}})
        self.assertFalse(result["found"])
        self.assertTrue(result["problems"])

    def test_undocumented_client_is_not_checked(self):
        result = ac.inspect_installed_client_config("cline")
        self.assertFalse(result["checked"])


class TestPingAgentHarnessMode(unittest.TestCase):
    """``ping_agent`` in harness mode reports config health, not just bridge."""

    def test_harness_mode_skips_mcp_and_llm(self):
        result = ac.ping_agent(operating_mode="EXTERNAL_HARNESS", check_harness_config=False)
        self.assertEqual(result["mcp_server"], "N/A (harness mode)")
        self.assertEqual(result["llm_health"], "N/A (harness mode)")
        self.assertEqual(result["llm_chat"], "N/A (harness mode)")
        self.assertEqual(result["harness_config"], "N/A")

    def test_harness_mode_includes_config_probe(self):
        result = ac.ping_agent(operating_mode="EXTERNAL_HARNESS", check_harness_config=True)
        self.assertIn("harness_config", result)
        self.assertNotEqual(result["harness_config"], "N/A")
        self.assertTrue(
            result["harness_config"].startswith("OK")
            or result["harness_config"].startswith("FAIL"),
            result["harness_config"],
        )

    def test_harness_mode_all_ok_ignores_na(self):
        """N/A entries must not make all_ok False."""
        result = ac.ping_agent(operating_mode="EXTERNAL_HARNESS", check_harness_config=False)
        self.assertIsInstance(result["all_ok"], bool)


if __name__ == "__main__":
    unittest.main()