# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""process_guard: servers left behind by a crashed Blender are reaped."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "addon", "bfa_coworker", "process_guard.py")


def _load():
    spec = importlib.util.spec_from_file_location("process_guard_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestProcessGuard(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self.pg = _load()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        if self._saved is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._saved
        self._tmp.cleanup()

    def _spawn(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.procs.append(p)
        return p

    def _dead_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid

    def test_orphan_of_dead_blender_is_killed(self):
        p = self._spawn()
        self.pg._save([{"pid": p.pid, "kind": "", "port": 8081, "owner": self._dead_pid()}])
        self.assertEqual(self.pg.reap_orphans(), 1)
        for _ in range(50):
            if p.poll() is not None:
                break
            time.sleep(0.05)
        self.assertIsNotNone(p.poll(), "the orphan must be dead")
        self.assertEqual(self.pg._load(), [])

    def test_server_of_live_blender_is_kept(self):
        p = self._spawn()
        self.pg.record(p.pid, "", 8081)  # owner = this (live) process
        self.assertEqual(self.pg.reap_orphans(), 0)
        self.assertIsNone(p.poll())
        self.pg.forget(p.pid)
        self.assertEqual(self.pg._load(), [])

    def test_reused_pid_is_forgotten_not_killed(self):
        p = self._spawn()
        self.pg._save([{"pid": p.pid, "kind": "llama-server", "port": 8081,
                        "owner": self._dead_pid()}])
        self.assertEqual(self.pg.reap_orphans(), 0, "a python process is not llama-server")
        self.assertIsNone(p.poll())
        self.assertEqual(self.pg._load(), [])

    def test_bind_never_raises(self):
        p = self._spawn()
        self.pg.bind_to_parent(p, "", 0)
        self.assertTrue(any(e["pid"] == p.pid for e in self.pg._load()))
        self.assertFalse(self.pg.bind_to_parent(None))


class TestLinuxPreexec(unittest.TestCase):
    """The parent-death hook must not load libc inside the forked child."""

    def setUp(self):
        self.pg = _load()
        self.loads = []
        self.calls = []
        test = self

        class _Libc:
            def prctl(self, option, sig):
                test.calls.append((option, sig))

        def _cdll(name, use_errno=False):
            test.loads.append(name)
            return _Libc()

        self._saved = (self.pg.sys.platform, self.pg.ctypes.CDLL)
        self.pg.sys.platform = "linux"
        self.pg.ctypes.CDLL = _cdll

    def tearDown(self):
        self.pg.sys.platform, self.pg.ctypes.CDLL = self._saved

    def test_libc_loaded_in_parent_child_only_calls_prctl(self):
        hook = self.pg.linux_preexec()
        self.assertEqual(self.loads, ["libc.so.6"], "libc loads when the hook is built")
        hook()
        self.assertEqual(self.loads, ["libc.so.6"], "the child must not dlopen")
        self.assertEqual(self.calls, [(1, 15)])

    def test_missing_libc_returns_none(self):
        def _missing(name, use_errno=False):
            raise OSError("no libc")
        self.pg.ctypes.CDLL = _missing
        self.assertIsNone(self.pg.linux_preexec())


if __name__ == "__main__":
    unittest.main()
