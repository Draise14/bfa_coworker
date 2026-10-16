# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Issue #85: with two Blender windows open, Coworker must drive the window it
was started from -- never the other one.

Window A runs in a subprocess (its own bridge + session id); window B is this
test process.  Both use the REAL bridge (``mcp_to_blender_server``), the real
MCP-side client (``blmcp.tools_helpers.connection``) and the real
``agent_controller`` / ``shared`` helpers -- no Blender needed.

Run with::

    python -m unittest tests.test_multi_window -v
"""

__all__ = ()

import importlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON = os.path.join(_REPO, "addon", "bfa_coworker")
_MCP = os.path.join(_REPO, "mcp")


def _pkg(name: str = "bfcw_mw"):
    """Import the add-on modules as a package without running __init__ (no bpy)."""
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [_ADDON]  # type: ignore[attr-defined]
        sys.modules[name] = pkg
    return name


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _identify(port: int) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(json.dumps({"type": "identify"}).encode() + b"\0")
        buf = bytearray()
        while b"\0" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
    return json.loads(bytes(buf).partition(b"\0")[0])


_WINDOW_A = textwrap.dedent("""
    import sys, time, types, importlib
    pkg = types.ModuleType("bfcw_a"); pkg.__path__ = [sys.argv[1]]; sys.modules["bfcw_a"] = pkg
    bridge = importlib.import_module("bfcw_a.mcp_to_blender_server")
    si = importlib.import_module("bfcw_a.session_identity")
    bridge.start("127.0.0.1", int(sys.argv[2]))
    print("READY", si.SESSION_ID, bridge.get_actual_port(), flush=True)
    end = time.time() + 60
    while time.time() < end:
        bridge.poll()
        time.sleep(0.01)
""")


class TestTwoWindows(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._saved_appdata = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = cls._tmp.name
        cls.port = _free_port()

        # Window A: owns the configured bridge port.
        cls.proc_a = subprocess.Popen(
            [sys.executable, "-c", _WINDOW_A, _ADDON, str(cls.port)],
            stdout=subprocess.PIPE, text=True, env=dict(os.environ))
        line = cls.proc_a.stdout.readline().split()
        assert line and line[0] == "READY", line
        cls.session_a, cls.port_a = line[1], int(line[2])

        # Window B: this process, configured with the SAME port.
        p = _pkg()
        cls.bridge = importlib.import_module(p + ".mcp_to_blender_server")
        cls.si = importlib.import_module(p + ".session_identity")
        cls.ac = importlib.import_module(p + ".agent_controller")
        cls.shared = importlib.import_module(p + ".shared")
        cls.pg = importlib.import_module(p + ".process_guard")
        cls.bridge.start("127.0.0.1", cls.port)
        cls._stop = threading.Event()

        def _serve():
            while not cls._stop.is_set():
                cls.bridge.poll()
                time.sleep(0.01)
        cls._thread = threading.Thread(target=_serve, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._stop.set()
        cls._thread.join(timeout=2)
        cls.bridge.stop()
        cls.proc_a.kill()
        cls.proc_a.wait()
        if cls._saved_appdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = cls._saved_appdata
        cls._tmp.cleanup()

    # -- ports ---------------------------------------------------------------

    def test_second_window_gets_its_own_bridge_port(self):
        self.assertEqual(self.port_a, self.port)
        self.assertNotEqual(self.bridge.get_actual_port(), self.port)

    def test_effective_ports_report_the_port_this_window_owns(self):
        prefs = types.SimpleNamespace(port_offset=0, bridge_port=self.port, mcp_port=0, llm_port=0)
        bridge, _mcp, _llm = self.shared.effective_ports(prefs)
        self.assertEqual(bridge, self.bridge.get_actual_port())

    def test_mcp_server_is_pointed_at_this_windows_bridge(self):
        # The root cause of #85: the launch passed the CONFIGURED port.
        env = self.ac._build_mcp_env(blender_port=self.port)
        self.assertEqual(int(env["BFACW_PORT"]), self.bridge.get_actual_port())
        self.assertEqual(env["BFACW_SESSION_ID"], self.si.SESSION_ID)
        self.assertNotEqual(self.si.SESSION_ID, self.session_a)

    # -- identity & refusal --------------------------------------------------

    def test_identify_tells_the_windows_apart(self):
        a = _identify(self.port_a)
        b = _identify(self.bridge.get_actual_port())
        self.assertEqual(a["result"]["session"], self.session_a)
        self.assertEqual(b["result"]["session"], self.si.SESSION_ID)
        self.assertEqual(b["result"]["bridge_port"], self.bridge.get_actual_port())

    def _send_code(self, port: int, session: str) -> dict:
        # Load the MCP-side client by path (blmcp/__init__ needs the mcp SDK).
        spec = importlib.util.spec_from_file_location(
            "blmcp_connection_mw", os.path.join(_MCP, "blmcp", "tools_helpers", "connection.py"))
        conn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(conn)
        saved = {k: os.environ.get(k) for k in ("BFACW_PORT", "BFACW_HOST", "BFACW_SESSION_ID")}
        os.environ.update({"BFACW_PORT": str(port), "BFACW_HOST": "127.0.0.1",
                           "BFACW_SESSION_ID": session})
        try:
            return conn.send_code("result = 1", strict_json=True)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_request_for_another_window_is_refused(self):
        # B's MCP server mistakenly aimed at A's bridge: A must refuse it.
        resp = self._send_code(self.port_a, self.si.SESSION_ID)
        self.assertEqual(resp["status"], "error")
        self.assertIn("not the one this Coworker was started from", resp["message"])

    def test_unstamped_request_is_still_accepted(self):
        # External Harness clients send no session id: never refused for that.
        resp = self._send_code(self.port_a, "")
        self.assertNotIn("not the one this Coworker was started from", str(resp.get("message", "")))

    def test_ping_flags_a_port_owned_by_another_window(self):
        res = self.ac.ping_agent(bridge_port=self.port_a, operating_mode="EXTERNAL_HARNESS")
        self.assertTrue(res["bridge_server"].startswith("FAIL"), res)
        self.assertIn("another window", res["bridge_server"])
        own = self.ac.ping_agent(bridge_port=self.bridge.get_actual_port(),
                                 operating_mode="EXTERNAL_HARNESS")
        self.assertEqual(own["bridge_server"], "OK (this window)")

    # -- live-window list ----------------------------------------------------

    def test_other_window_is_listed_as_live(self):
        self.si._cache["t"] = 0.0
        others = self.si.other_live_sessions()
        self.assertIn(self.proc_a.pid, [int(o["pid"]) for o in others])
        self.assertNotIn(os.getpid(), [int(o["pid"]) for o in others])


class TestKillGuard(unittest.TestCase):
    """A window must never kill another live window's servers (#85)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self.pg = importlib.import_module(_pkg() + ".process_guard")
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

    def _spawn(self, code="import time; time.sleep(60)"):
        p = subprocess.Popen([sys.executable, "-c", code])
        self.procs.append(p)
        return p

    def test_server_of_another_live_window_is_protected(self):
        other_blender = self._spawn()
        server = self._spawn()
        self.pg._save([{"pid": server.pid, "kind": "mcp-server", "port": 9191,
                        "owner": other_blender.pid}])
        self.assertFalse(self.pg.may_kill(server.pid, "mcp-server"))

    def test_own_and_orphaned_servers_may_be_freed(self):
        own = self._spawn()
        orphan = self._spawn("import time; time.sleep(60)  # blmcp server")  # still looks like ours
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        self.pg._save([
            {"pid": own.pid, "kind": "mcp-server", "port": 9191, "owner": os.getpid()},
            {"pid": orphan.pid, "kind": "mcp-server", "port": 9192, "owner": gone.pid},
        ])
        self.assertTrue(self.pg.may_kill(own.pid, "mcp-server"))
        self.assertTrue(self.pg.may_kill(orphan.pid, "mcp-server"))

    def test_unrecorded_python_is_never_killed_by_name_alone(self):
        # On Windows only "python.exe" is known; it could be another window's
        # MCP server started through a launcher (unrecorded child).
        p = self._spawn()
        self.assertFalse(self.pg.may_kill(p.pid, "mcp-server"))

    def test_reused_pid_of_a_dead_owner_is_left_alone(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        reused = subprocess.Popen(["sleep", "60"])
        self.procs.append(reused)
        self.pg._save([{"pid": reused.pid, "kind": "llama-server", "port": 8081, "owner": gone.pid}])
        self.assertFalse(self.pg.may_kill(reused.pid, "llama-server"))

    def test_unrecorded_foreign_program_is_left_alone(self):
        foreign = subprocess.Popen(["sleep", "60"])
        self.procs.append(foreign)
        self.assertFalse(self.pg.may_kill(foreign.pid, "llama-server"))

    def test_kill_own_only_touches_this_windows_llama(self):
        other_blender = self._spawn()
        mine = self._spawn("import time; time.sleep(60)  # llama-server")
        theirs = self._spawn("import time; time.sleep(60)  # llama-server")
        self.pg._save([
            {"pid": mine.pid, "kind": "llama-server", "port": 8081, "owner": os.getpid()},
            {"pid": theirs.pid, "kind": "llama-server", "port": 8082, "owner": other_blender.pid},
        ])
        self.assertEqual(self.pg.kill_own("llama-server"), 1)
        mine.wait(timeout=5)
        time.sleep(0.2)
        self.assertIsNone(theirs.poll(), "another window's llama-server was killed")

    def test_pids_listening_finds_a_listener(self):
        if sys.platform != "win32" and subprocess.run(["which", "fuser"],
                                                      capture_output=True).returncode != 0:
            self.skipTest("fuser not installed")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            self.assertIn(os.getpid(), self.pg.pids_listening(port))


if __name__ == "__main__":
    unittest.main()
