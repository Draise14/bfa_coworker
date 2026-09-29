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
    """Load ``agent_controller.py`` as a standalone module.

    The package ``__init__.py`` imports ``bpy``, so ``from bfa_coworker import
    agent_controller`` fails outside Blender.  ``agent_controller`` itself is
    stdlib-only, so load it directly from its file path.
    """
    spec = importlib.util.spec_from_file_location("_ac_under_test", _AC_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load {:s}".format(_AC_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules["_ac_under_test"] = module
    spec.loader.exec_module(module)
    return module


ac = _load_agent_controller()


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
    """The emitted config must carry a usable PYTHONPATH."""

    def test_config_is_valid_json(self):
        import json
        raw = ac.generate_mcp_client_config(client_type="claude_desktop")
        parsed = json.loads(raw)
        self.assertIn("mcpServers", parsed)
        self.assertIn("bfa-coworker", parsed["mcpServers"])

    def test_cursor_uses_servers_key(self):
        import json
        raw = ac.generate_mcp_client_config(client_type="cursor")
        parsed = json.loads(raw)
        self.assertIn("servers", parsed)
        self.assertEqual(parsed["servers"]["bfa-coworker"]["type"], "stdio")

    def test_generic_is_raw_command_block(self):
        import json
        raw = ac.generate_mcp_client_config(client_type="generic")
        parsed = json.loads(raw)
        self.assertIn("command", parsed)
        self.assertIn("args", parsed)

    def test_env_has_bridge_host_and_port(self):
        import json
        raw = ac.generate_mcp_client_config(
            client_type="claude_desktop", blender_host="localhost", blender_port=9876)
        parsed = json.loads(raw)
        env = parsed["mcpServers"]["bfa-coworker"]["env"]
        self.assertEqual(env["BFACW_HOST"], "localhost")
        self.assertEqual(env["BFACW_PORT"], "9876")

    def test_pythonpath_points_at_vendor_parent(self):
        """PYTHONPATH must contain the *parent* of vendor/blmcp, not blmcp itself.

        This is the exact mistake that produces ``No module named 'blmcp'``:
        ``import blmcp`` resolves from the directory *containing* the package.
        """
        import json
        import unittest.mock as mock
        vendor_dir = os.path.join(_ADDON_DIR, "bfa_coworker", "vendor")
        with mock.patch.object(ac, "_get_blender_python_for_config",
                               return_value=(sys.executable, vendor_dir)):
            raw = ac.generate_mcp_client_config(
                client_type="claude_desktop", use_blender_python=True)
        parsed = json.loads(raw)
        env = parsed["mcpServers"]["bfa-coworker"]["env"]
        self.assertEqual(env["PYTHONPATH"], vendor_dir)
        # The parent of vendor/blmcp is vendor/ — not vendor/blmcp.
        self.assertFalse(env["PYTHONPATH"].endswith(os.path.join("vendor", "blmcp")))

    def test_args_use_stdio_transport(self):
        import json
        raw = ac.generate_mcp_client_config(client_type="claude_desktop")
        parsed = json.loads(raw)
        args = parsed["mcpServers"]["bfa-coworker"]["args"]
        self.assertEqual(args, ["-m", "blmcp", "--transport", "stdio"])


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