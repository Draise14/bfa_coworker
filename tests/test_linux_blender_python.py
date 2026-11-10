# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
#435: on Linux, Local mode failed with "Cannot find Python to run MCP server".

1. ``_find_blender_python`` finds Bforartists' own Python on Linux, where the
   tarball ships only the versioned name (``python/bin/python3.13``).
2. When the MCP server can't start, the chat keeps the reason
   ``_resolve_mcp_python`` gave instead of the generic text.
"""

__all__ = ()

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_linuxpy_pkg"


def _import(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


ac = _import("agent_controller")
VERSIONED = "python{:d}.{:d}".format(*sys.version_info[:2])


class TestFindBlenderPython(unittest.TestCase):

    def prefix(self, *names):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        os.makedirs(os.path.join(root, "bin"))
        for name in names:
            Path(root, "bin", name).write_text("")
        return root

    def find(self, root, platform="linux"):
        with mock.patch.object(ac.sys, "platform", platform), mock.patch.object(ac.sys, "prefix", root):
            return ac._find_blender_python()

    def test_linux_tarball_has_only_the_versioned_name(self):
        root = self.prefix(VERSIONED)
        self.assertEqual(self.find(root), str(Path(root, "bin", VERSIONED)))

    def test_plain_python3_still_comes_first(self):
        root = self.prefix("python3", VERSIONED)
        self.assertEqual(self.find(root), str(Path(root, "bin", "python3")))

    def test_macos_too(self):
        root = self.prefix(VERSIONED)
        self.assertEqual(self.find(root, "darwin"), str(Path(root, "bin", VERSIONED)))

    def test_none_when_there_is_no_python(self):
        self.assertIsNone(self.find(self.prefix("python2.7")))


class TestStartKeepsTheReason(unittest.TestCase):

    def start(self, fn):
        reason = "MCP server dependencies not found in vendor deps cache. Run ..."

        def resolve():
            ac._agent_state.error = reason
            return (None, False)
        with mock.patch.object(ac, "_resolve_mcp_python", resolve), \
                mock.patch.object(ac, "_kill_process_on_port", lambda *a, **k: None), \
                mock.patch.object(ac, "_find_available_port", lambda port, *a, **k: port), \
                mock.patch.object(ac, "_mcp_server_process", None), \
                mock.patch.object(ac._agent_state, "error", ""):
            self.assertIsNone(fn(port=59191))
            return ac._agent_state.error, reason

    def test_http_start(self):
        error, reason = self.start(ac.start_mcp_server)
        self.assertEqual(error, reason)

    def test_network_start(self):
        error, reason = self.start(ac.start_mcp_server_network)
        self.assertEqual(error, reason)

    def test_generic_text_when_no_reason_was_given(self):
        with mock.patch.object(ac, "_resolve_mcp_python", lambda: (None, False)), \
                mock.patch.object(ac, "_kill_process_on_port", lambda *a, **k: None), \
                mock.patch.object(ac, "_find_available_port", lambda port, *a, **k: port), \
                mock.patch.object(ac, "_mcp_server_process", None), \
                mock.patch.object(ac._agent_state, "error", ""):
            self.assertIsNone(ac.start_mcp_server(port=59191))
            self.assertEqual(ac._agent_state.error, "Cannot find Python to run MCP server")


if __name__ == "__main__":
    unittest.main()
