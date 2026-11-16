# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The MCP server's port wait on Start (#558).

1. Start waits up to 45 s for the server's port (15 s was too short on a loaded machine).
2. When the port never answers and the server is still running, Start ends that process
   (it ran on untracked before) and the error says how long it waited.
3. A server that dies during the wait ends the wait at once, and the error says so.

Fake processes only; does not require Blender.  Run with::

    python -m unittest tests.test_mcp_port_wait_558 -v
"""

__all__ = ()

import contextlib
import importlib
import io
import os
import sys
import types
import unittest
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


class _FakeProc:
    """A server process: running until ended, or dead from the start with *exit_code*."""

    pid = 48123
    stdout = stderr = stdin = None

    def __init__(self, exit_code=None):
        self.returncode = exit_code
        self.ended = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.ended = True
        if self.returncode is None:
            self.returncode = 1

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


class _StartCase(unittest.TestCase):
    """Everything around the launch stubbed: no process, port or sleep is real."""

    proc_exit_code = None

    def setUp(self) -> None:
        self.proc = _FakeProc()
        self.forgotten: list = []
        self.wait_calls: list = []
        for target, value in (
                ("_resolve_mcp_python", lambda: (sys.executable, True)),
                ("_build_mcp_env", lambda **_kw: {}),
                ("_kill_process_on_port", lambda *_a, **_kw: None),
                ("_find_available_port", lambda port: port),
                ("_start_pipe_drainer", lambda _proc: ([], [], [])),
                ("_wait_for_port", self._fake_wait),
                ("_agent_state", ac.AgentState()),
                ("_mcp_server_process", None),
        ):
            patcher = mock.patch.object(ac, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in (("reap_orphans", lambda *_a, **_kw: None),
                              ("bind_to_parent", lambda *_a, **_kw: None),
                              ("forget", self.forgotten.append)):
            patcher = mock.patch.object(ac._process_guard, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(ac.subprocess, "Popen", lambda *_a, **_kw: self.proc),
                        mock.patch("time.sleep", lambda _s: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fake_wait(self, *args, **kwargs) -> bool:
        self.wait_calls.append(kwargs.get("timeout"))
        self.proc.returncode = self.proc_exit_code      # None: still running, never answered
        return False

    def start(self, network: bool):
        with contextlib.redirect_stdout(io.StringIO()):
            if network:
                return ac.start_mcp_server_network(host="127.0.0.1", port=50998)
            return ac.start_mcp_server(port=50998)


class TestTimeoutEndsTheServer(_StartCase):

    def test_local_start_ends_a_server_that_never_answered(self):
        self.assertIsNone(self.start(network=False))
        self.assertEqual(self.wait_calls, [45.0])
        self.assertTrue(self.proc.ended, "the server was left running untracked")
        self.assertEqual(self.forgotten, [self.proc.pid])
        self.assertIsNone(ac._mcp_server_process)
        self.assertFalse(ac._agent_state.mcp_server_running)
        self.assertIn("(waited ", ac._agent_state.error)
        self.assertIn("never accepted connections", ac._agent_state.error)

    def test_network_start_ends_a_server_that_never_answered(self):
        self.assertIsNone(self.start(network=True))
        self.assertEqual(self.wait_calls, [45.0])
        self.assertTrue(self.proc.ended, "the server was left running untracked")
        self.assertEqual(self.forgotten, [self.proc.pid])
        self.assertIsNone(ac._mcp_server_process)
        self.assertIn("(waited ", ac._agent_state.error)


class TestServerDiedDuringTheWait(_StartCase):
    proc_exit_code = 3

    def test_error_says_the_server_exited(self):
        for network in (False, True):
            with self.subTest(network=network):
                self.proc = _FakeProc()
                self.assertIsNone(self.start(network=network))
                self.assertIn("the server exited after", ac._agent_state.error)
                self.assertIn("with code 3", ac._agent_state.error)


class TestWaitReturnsAtOnceForADeadProcess(unittest.TestCase):

    def test_dead_process_ends_the_wait_without_sleeping(self):
        slept: list = []
        refused = mock.patch.object(ac.socket, "create_connection", side_effect=ConnectionRefusedError())
        with refused, mock.patch("time.sleep", slept.append), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(ac._wait_for_port("127.0.0.1", 50997, timeout=ac._MCP_PORT_WAIT,
                                               interval=1.0, proc=_FakeProc(exit_code=1)))
        self.assertEqual(slept, [])

    def test_live_process_waits_until_the_port_answers(self):
        answers = iter((ConnectionRefusedError(), ConnectionRefusedError(), contextlib.nullcontext()))

        def connect(*_a, **_kw):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        slept: list = []
        with mock.patch.object(ac.socket, "create_connection", connect), \
                mock.patch("time.sleep", slept.append), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(ac._wait_for_port("127.0.0.1", 50997, timeout=ac._MCP_PORT_WAIT,
                                              interval=1.0, proc=_FakeProc()))
        self.assertEqual(slept, [1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
