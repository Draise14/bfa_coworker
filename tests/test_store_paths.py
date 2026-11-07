# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for a Microsoft Store (MSIX) Bforartists (store_paths.py, #390).

A Store copy's new AppData files really live in its package folder, so paths
handed to other programs must point there.

1. ``real_path``: Local and Roaming AppData map to the package's LocalCache
   when the package wrote the file there; real files, other folders, relative
   arguments and non-Store copies are left alone.
2. ``is_redirected``: only absolute AppData paths, only in a Store copy.
3. The External Agent gets the real working folder and MCP server paths.
4. ``install.json`` for the MCP launcher carries real folders.
5. "Add to <client>" changes an existing config in place, and says so instead
   of creating a config the client could never see.

Run with::

    python -m unittest tests.test_store_paths -v
"""

__all__ = ()

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_FAMILY = "Trinumedia.Bforartists5_abv20tse8q4c6"


def _load(*names):
    pkg_name = "bfacw_store_paths_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[pkg_name] = pkg
    return [importlib.import_module(pkg_name + "." + n) for n in names]


sp, ea, hc, ac = _load("store_paths", "engine_acp", "harness_clients", "agent_controller")


class _Home(unittest.TestCase):
    """A fake user profile: AppData\\Local, AppData\\Roaming and a package's LocalCache."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.local, self.roaming = root / "AppData" / "Local", root / "AppData" / "Roaming"
        self.cache = self.local / "Packages" / _FAMILY / "LocalCache"
        for folder in (self.local, self.roaming, self.cache):
            folder.mkdir(parents=True)
        self.env = {"LOCALAPPDATA": str(self.local), "APPDATA": str(self.roaming)}

    def tearDown(self):
        self._tmp.cleanup()

    def _packaged(self, family=_FAMILY):
        """Run as a Store copy with this fake profile."""
        return mock.patch.multiple(sp, package_family_name=lambda: family), \
            mock.patch.dict(os.environ, self.env)


class TestRealPath(_Home):

    def test_local_appdata_written_by_the_package(self):
        moved = self.cache / "Local" / "bfa_coworker" / "agent_workspace"
        moved.mkdir(parents=True)
        given = str(self.local / "bfa_coworker" / "agent_workspace")
        self.assertEqual(sp.real_path(given, _FAMILY, self.env), str(moved))

    def test_roaming_appdata_written_by_the_package(self):
        moved = self.cache / "Roaming" / "Bforartists" / "5.2" / "extensions"
        moved.mkdir(parents=True)
        given = str(self.roaming / "Bforartists" / "5.2" / "extensions")
        self.assertEqual(sp.real_path(given, _FAMILY, self.env), str(moved))

    def test_a_real_file_stays(self):
        # Written before the Store copy, or by another program: it is really there.
        real = self.local / "bfa_coworker"
        real.mkdir()
        self.assertEqual(sp.real_path(str(real), _FAMILY, self.env), str(real))

    def test_outside_appdata_relative_and_not_packaged_stay(self):
        (self.cache / "Local" / "x").mkdir(parents=True)
        other = str(Path(self._tmp.name) / ".cache" / "x")
        self.assertEqual(sp.real_path(other, _FAMILY, self.env), other)
        self.assertEqual(sp.real_path("--transport", _FAMILY, self.env), "--transport")
        self.assertEqual(sp.real_path(str(self.local / "x"), "", self.env), str(self.local / "x"))

    def test_package_folders_stay(self):
        inside = str(self.cache / "Local" / "bfa_coworker")
        self.assertEqual(sp.real_path(inside, _FAMILY, self.env), inside)

    def test_is_redirected(self):
        self.assertTrue(sp.is_redirected(str(self.roaming / "Claude" / "c.json"), _FAMILY, self.env))
        self.assertFalse(sp.is_redirected(str(self.roaming / "Claude" / "c.json"), "", self.env))
        self.assertFalse(sp.is_redirected(str(Path(self._tmp.name) / ".claude.json"), _FAMILY, self.env))
        self.assertFalse(sp.is_redirected("relative.json", _FAMILY, self.env))

    def test_not_a_store_copy_here(self):
        # The test machine runs a normal Python, never inside a package.
        sp._FAMILY.clear()
        self.assertEqual(sp.package_family_name(), "")


class TestExternalAgent(_Home):

    def test_agent_gets_the_real_folders(self):
        workspace = self.local / "bfa_coworker" / "agent_workspace"
        moved = self.cache / "Local" / "bfa_coworker" / "agent_workspace"
        moved.mkdir(parents=True)
        launcher_dir = self.cache / "Roaming" / "Bforartists" / "launcher"
        launcher_dir.mkdir(parents=True)
        launcher = self.roaming / "Bforartists" / "launcher"
        seen = {}

        class _Client:
            def __init__(self, argv, cwd, **_kw):
                seen["cwd"] = cwd

            def start(self):
                pass

            def initialize(self):
                return types.SimpleNamespace(load_session=False, image=False, name="", version="")

        def _open_session(_client, _load, _resume, cwd, servers):
            seen["session_cwd"], seen["server"] = cwd, servers[0]
            raise ea.AcpError("stop here")

        state = types.SimpleNamespace(conversation_history=[], streaming_text="", reasoning_text="",
                                      is_thinking=False, thinking_start_time=0.0, turn_phase="",
                                      ui_status="", error="")
        engine = ea.AcpEngine(state, client_factory=_Client)
        engine._open_session = _open_session
        family, environ = self._packaged()
        with family, environ:
            engine._connect(["agent"], {"command": str(launcher), "args": ["--transport", "stdio"]},
                            str(workspace), None)
        self.assertEqual(seen["cwd"], str(moved))               # the process starts there
        self.assertEqual(seen["session_cwd"], str(moved))       # and the session is told so
        self.assertEqual(seen["server"]["command"], str(launcher_dir))
        self.assertEqual(seen["server"]["args"], ["--transport", "stdio"])


class TestLauncherInstall(_Home):

    def test_install_json_has_real_folders(self):
        addon = self.cache / "Roaming" / "Bforartists" / "extensions" / "bfa_coworker"
        (addon / "vendor").mkdir(parents=True)
        given_vendor = str(self.roaming / "Bforartists" / "extensions" / "bfa_coworker" / "vendor")
        harness = Path(self._tmp.name) / "harness"
        family, environ = self._packaged()
        with family, environ, mock.patch.object(ac, "_HARNESS_DIR", harness):
            ac.ensure_harness_launcher(given_vendor)
        info = json.loads((harness / "install.json").read_text(encoding="utf-8"))
        self.assertEqual(info["pythonpath"], [str(addon / "vendor")])


class TestClientConfig(_Home):

    def test_existing_config_changed_in_place(self):
        config = self.roaming / "Claude" / "claude_desktop_config.json"
        config.parent.mkdir()
        config.write_text("{}", encoding="utf-8")
        backups = Path(self._tmp.name) / "home" / ".cache" / "bfa_coworker" / "harness" / "backups"
        family, environ = self._packaged()
        with family, environ, mock.patch.object(hc, "_STORE_BACKUP_DIR", backups):
            hc._write_atomic(config, '{"a": 1}')
        self.assertEqual(config.read_text(encoding="utf-8"), '{"a": 1}')
        self.assertFalse(config.with_name(config.name + ".bfacw.tmp").exists())
        # The old config is kept outside AppData (nothing redirected there).
        saved = list(backups.glob("claude_desktop_config.json.*.bak"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_text(encoding="utf-8"), "{}")

    def test_change_that_lands_in_the_package_copy_is_reported(self):
        config = self.roaming / "Claude" / "claude_desktop_config.json"
        config.parent.mkdir()
        config.write_text("{}", encoding="utf-8")
        private = self.cache / "Roaming" / "Claude" / "claude_desktop_config.json"
        backups = Path(self._tmp.name) / "backups"
        real_write = Path.write_text

        def _write_into_package(path, text, *args, **kwargs):
            # As if this Windows build copied the edited file into the package.
            private.parent.mkdir(parents=True, exist_ok=True)
            return real_write(private, text, *args, **kwargs)

        family, environ = self._packaged()
        with family, environ, mock.patch.object(hc, "_STORE_BACKUP_DIR", backups), \
                mock.patch.object(Path, "write_text", _write_into_package), \
                self.assertRaises(ValueError) as caught:
            hc._write_atomic(config, '{"a": 1}')
        self.assertIn("Copy Config", str(caught.exception))
        self.assertEqual(config.read_text(encoding="utf-8"), "{}")

    def test_mcp_server_gets_real_vendor_folders(self):
        addon_vendor = self.roaming / "Bforartists" / "extensions" / "bfa_coworker" / "vendor"
        moved = self.cache / "Roaming" / "Bforartists" / "extensions" / "bfa_coworker" / "vendor"
        moved.mkdir(parents=True)
        family, environ = self._packaged()
        with family, environ, mock.patch.object(ac, "_find_vendor_pythonpath",
                                                 return_value=str(addon_vendor)):
            env = ac._build_mcp_env()
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(moved))

    def test_new_config_refused_with_a_reason(self):
        config = self.roaming / "Claude" / "claude_desktop_config.json"
        family, environ = self._packaged()
        with family, environ, self.assertRaises(ValueError) as caught:
            hc._write_atomic(config, "{}")
        self.assertIn("Store version", str(caught.exception))
        self.assertFalse(config.exists())

    def test_normal_copy_writes_as_before(self):
        config = self.roaming / "Claude" / "claude_desktop_config.json"
        family, environ = self._packaged("")
        with family, environ:
            hc._write_atomic(config, "{}")
        self.assertEqual(config.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
