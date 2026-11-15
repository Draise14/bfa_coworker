# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Stopping the MCP server closes its pipes (#502).

Every way the server process ends -- Stop (terminate), a server that does not
end in time (kill), a restart (start while one runs) and add-on unregister
(``cleanup()``) -- leaves stdout / stderr / stdin closed once the pipe readers
are done, and nothing closes a pipe while its reader still reads it.  The
stand-in process's pipes block like real ones until the process ends; one
test runs a real child process with ResourceWarnings on.

Does not require Blender.  Run with::

    python -m unittest tests.test_mcp_pipes_closed -v
"""

__all__ = ()

import gc
import importlib
import os
import subprocess
import sys
import threading
import types
import unittest
import warnings
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


class _Pipe:
    """A pipe end: yields its lines, then blocks until the process has ended (end of file)."""

    def __init__(self, proc, lines=(b"line\n",)):
        self._proc = proc
        self._lines = list(lines)
        self.closed = False
        self.reading = False
        self.closed_under_read = False

    def __iter__(self):
        return self

    def __next__(self):
        self.reading = True
        try:
            if self._lines:
                return self._lines.pop(0)
            self._proc.ended.wait()
            if self._proc.eof_held.is_set():   # a child process still holds the write end
                self._proc.eof_released.wait()
            raise StopIteration
        finally:
            self.reading = False

    def close(self):
        if self.reading:
            self.closed_under_read = True
        self.closed = True


class _Proc:
    """A stand-in for ``subprocess.Popen`` with stdin / stdout / stderr pipes."""

    pid = 4242

    def __init__(self, obeys_terminate=True):
        self.ended = threading.Event()
        self.eof_held = threading.Event()
        self.eof_released = threading.Event()
        self.obeys_terminate = obeys_terminate
        self.returncode = None
        self.killed = False
        self.stdin = _Pipe(self, ())
        self.stdout = _Pipe(self)
        self.stderr = _Pipe(self)

    def poll(self):
        return self.returncode

    def terminate(self):
        if self.obeys_terminate:
            self._end(1)

    def kill(self):
        self.killed = True
        self._end(9)

    def wait(self, timeout=None):
        if not self.ended.wait(0 if timeout is None else min(timeout, 0.05)):
            raise subprocess.TimeoutExpired("stand-in", timeout)
        return self.returncode

    def _end(self, code):
        self.returncode = code
        self.ended.set()

    def pipes(self):
        return (self.stdin, self.stdout, self.stderr)


class _Base(unittest.TestCase):
    def setUp(self):
        for target, value in (
                ("_agent_state", ac.AgentState()),
                ("_mcp_server_process", None),
                ("_mcp_shutting_down", False),
        ):
            patcher = mock.patch.object(ac, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target in ("forget", "reap_orphans", "bind_to_parent"):
            patcher = mock.patch.object(ac._process_guard, target, lambda *_a, **_kw: None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def running(self, **kw):
        proc = _Proc(**kw)
        threads, _out, _err = ac._start_pipe_drainer(proc)
        ac._mcp_server_process = proc
        ac._agent_state.mcp_server_running = True
        return proc, threads

    def assert_released(self, proc, threads):
        self.assertIsNotNone(proc.returncode, "the process ended")
        self.assertFalse([t for t in threads if t.is_alive()], "the pipe readers are done")
        for pipe in proc.pipes():
            self.assertTrue(pipe.closed, "every pipe is closed")
            self.assertFalse(pipe.closed_under_read, "no pipe was closed while it was read")


class PipesClosedOnEveryExit(_Base):
    def test_stop(self):
        proc, threads = self.running()
        ac.stop_mcp_server()
        self.assert_released(proc, threads)
        self.assertFalse(proc.killed)
        self.assertIsNone(ac._mcp_server_process)
        self.assertFalse(ac._agent_state.mcp_server_running)

    def test_stop_kills_a_server_that_does_not_end(self):
        proc, threads = self.running(obeys_terminate=False)
        ac.stop_mcp_server()
        self.assertTrue(proc.killed)
        self.assert_released(proc, threads)

    def test_unregister(self):
        proc, threads = self.running()
        ac.cleanup()
        self.assert_released(proc, threads)

    def test_restart_releases_the_old_server(self):
        proc, threads = self.running()
        with mock.patch.object(ac, "_kill_process_on_port", lambda *_a, **_kw: None), \
                mock.patch.object(ac, "_find_available_port", lambda port: port), \
                mock.patch.object(ac, "_resolve_mcp_python", lambda: (None, False)), \
                mock.patch("time.sleep", lambda *_a: None):
            self.assertIsNone(ac.start_mcp_server(port=1))
        self.assert_released(proc, threads)

    def test_reader_closes_its_pipe_when_the_server_exits_by_itself(self):
        proc, threads = self.running()
        proc._end(0)
        for t in threads:
            t.join(2)
        self.assertTrue(proc.stdout.closed and proc.stderr.closed)

    def test_a_pipe_still_read_is_never_closed_under_its_reader(self):
        """A child process that inherited the pipe keeps it open: stop does not wait forever or close it."""
        proc, threads = self.running()
        proc.eof_held.set()
        proc._end(1)
        ac._release_process_pipes(proc, timeout=0.2)
        self.assertFalse(proc.stdout.closed_under_read or proc.stderr.closed_under_read)
        self.assertFalse(proc.stdout.closed, "left to its reader")
        self.assertTrue(proc.stdin.closed)
        proc.eof_released.set()
        for t in threads:
            t.join(2)
        self.assert_released(proc, threads)


class RealProcessNoResourceWarning(_Base):
    def test_stop_a_real_child_with_warnings_on(self):
        code = "import sys, time\nprint('ready', flush=True)\nprint('err', file=sys.stderr, flush=True)\ntime.sleep(60)\n"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proc = subprocess.Popen([sys.executable, "-c", code],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            threads, out, _err = ac._start_pipe_drainer(proc)
            ac._mcp_server_process = proc
            for _ in range(100):
                if out:
                    break
                threading.Event().wait(0.05)
            ac.stop_mcp_server()
            self.assertIsNotNone(proc.poll())
            self.assertTrue(proc.stdin.closed and proc.stdout.closed and proc.stderr.closed)
            self.assertFalse([t for t in threads if t.is_alive()])
            del proc, threads
            gc.collect()
        found = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
