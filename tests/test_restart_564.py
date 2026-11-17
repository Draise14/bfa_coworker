# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Restart Coworker (#564) and the bridge's port choice (#558).

1. Restart runs the Start button's own path: the bridge and the MCP server come
   up on the ports the preferences give (port offset), the MCP server is told
   the bridge's real port, and the status ends on the start's real result --
   an error when it failed, never an unchecked "Connected".
2. The Local server comes back only if it ran before Restart: through Local
   mode's own Start (same model file), or through the Local server's own Start
   in the other modes.  External Harness restarts the bridge only.
3. The bridge picks its port by binding it: a second window that does not
   answer in time (busy, full backlog) no longer fools the check into a bind
   that fails with WinError 10048.  It moves to the next free port, and the
   note names the port and the window that has it.

The restart operators run from ``ui_chat.py``'s own source over stand-ins (no
Blender); the bridge is the real module on real sockets.  Run with::

    python -m unittest tests.test_restart_564 -v
"""

__all__ = ()

import importlib
import os
import re
import socket
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfcw_564"


def _pkg() -> str:
    """The add-on's modules as a package, without its ``__init__`` (no bpy)."""
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    return _PKG


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Stand-ins for what the restart path calls


class _AgentState:
    def __init__(self) -> None:
        self.mcp_server_running = False
        self.mcp_port_actual = 0
        self.error = ""

    def reset_usage(self) -> None:
        pass


class _FakeAgentController(types.ModuleType):
    """``agent_controller``: records the MCP server launches."""

    def __init__(self) -> None:
        super().__init__(_PKG + ".agent_controller")
        self._agent_state = _AgentState()
        self.launches: list[dict] = []
        self.fail_with = ""

    def start_mcp_server(self, port=9191, blender_host="localhost", blender_port=9876, _retry_depth=0):
        self.launches.append({"port": port, "blender_port": blender_port})
        if self.fail_with:
            self._agent_state.error = self.fail_with
            return None
        self._agent_state.mcp_server_running = True
        self._agent_state.mcp_port_actual = port
        return object()

    def stop_mcp_server(self) -> None:
        self._agent_state.mcp_server_running = False

    def clear_session_scene_lock(self) -> None:
        pass

    def prefetch_main_thread_context(self) -> None:
        pass

    def prepare_harness_server_async(self) -> None:
        pass

    def warmup_agent(self, on_status=None, mcp_port=0) -> None:
        self.warmed_on = mcp_port


class _Proc:
    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.pid = 4242

    def poll(self):
        return None if self.alive else 0


class _FakeLlm(types.ModuleType):
    """``llm_manager``: a Local server that can be running, records its starts."""

    def __init__(self) -> None:
        super().__init__(_PKG + ".llm_manager")
        self.proc: "_Proc | None" = None
        self.starts: list = []
        self.cfg = types.SimpleNamespace(mode="remote", local_port=0)

    def get_llama_process(self):
        return self.proc

    def get_state(self):
        return types.SimpleNamespace(is_running=self.proc is not None and self.proc.poll() is None, error="")

    def stop_local_llama(self) -> None:
        if self.proc is not None:
            self.proc.alive = False
        self.proc = None

    def start_local_llama(self, model_path=None, port=None, allow_download=False):
        self.starts.append({"model_path": model_path, "port": self.cfg.local_port})
        self.proc = _Proc()
        return self.proc

    def get_config(self):
        return self.cfg

    def set_config(self, cfg) -> None:
        self.cfg = cfg

    def check_local_model_ready(self, path):
        return True, None

    def wait_until_ready(self, timeout=0.0, proc=None) -> bool:
        return True

    def stopped_by_user(self, proc) -> bool:
        return False


class _Timers:
    def __init__(self) -> None:
        self.queue: list = []

    def register(self, fn, first_interval=0.0, persistent=False):
        self.queue.append(fn)

    def is_registered(self, fn) -> bool:
        return fn in self.queue

    def unregister(self, fn) -> None:
        if fn in self.queue:
            self.queue.remove(fn)


class _SyncThread:
    """``threading.Thread`` that runs at ``start()``: the start's background steps in order."""

    def __init__(self, target=None, daemon=None, name=None) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


def _operator_source() -> str:
    """Start / Stop / Restart and the helpers they share, from ``ui_chat.py``."""
    src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
    start = src.index("class BFACW_OT_agent_start(")
    end = src.index("\ndef _restore_chat_history(", start)
    parts = [src[start:end]]
    start = src.index("class BFACW_OT_agent_stop(")
    end = src.index("\n# ----", start)
    parts.append(src[start:end])
    return "\n\n".join(parts)


class _Blender:
    """One fake Blender session: prefs, props, bpy, the module stand-ins, and the operators."""

    def __init__(self, mode: str = "LOCAL_LLM", offset: int = 0, model: str = "") -> None:
        p = _pkg()
        self.bridge = importlib.import_module(p + ".mcp_to_blender_server")
        self.shared = importlib.import_module(p + ".shared")
        self.ac = _FakeAgentController()
        self.llm = _FakeLlm()
        self.timers = _Timers()
        self.reports: list = []
        self.ops_calls: list = []
        self.prefs = types.SimpleNamespace(
            operating_mode=mode, host="127.0.0.1", port_offset=offset, bridge_port=0, mcp_port=0,
            llm_port=0, existing_model_path=model)
        self.props = types.SimpleNamespace(chat_status="")
        self.context = types.SimpleNamespace(
            window_manager=types.SimpleNamespace(bfacw_chat_props=self.props),
            preferences=types.SimpleNamespace(addons={p: types.SimpleNamespace(preferences=self.prefs)}),
            area=None)
        interactive = types.ModuleType(p + ".execute_interactive")
        interactive.run = lambda: 0.1
        self._saved = {name: sys.modules.get(name) for name in (
            p + ".agent_controller", p + ".execute_interactive")}
        sys.modules[p + ".agent_controller"] = self.ac        # effective_ports reads it too
        sys.modules[p + ".execute_interactive"] = interactive

        ops = types.SimpleNamespace(
            agent_start=lambda **kw: self._run_op("BFACW_OT_agent_start", kw),
            start_llm=lambda **kw: self._start_llm(),
            confirm_model_download=lambda *a, **kw: {"CANCELLED"})
        bpy = types.SimpleNamespace(
            app=types.SimpleNamespace(background=False, timers=self.timers),
            context=self.context, ops=types.SimpleNamespace(bfacw=ops),
            types=types.SimpleNamespace(Context=object))

        def _sync(prefs) -> None:
            self.llm.cfg.mode = "local" if prefs.operating_mode == "LOCAL_LLM" else "remote"

        self.ns = {
            "__name__": p + ".ui_chat", "__package__": p,
            "bpy": bpy, "Operator": object,
            "BoolProperty": lambda **kw: dict(kw, _prop="bool"),
            "agent_controller": self.ac, "llm_manager": self.llm, "mcp_to_blender_server": self.bridge,
            "effective_ports": self.shared.effective_ports, "os": os, "re": re,
            "threading": types.SimpleNamespace(Thread=_SyncThread),
            "_acp": None, "_sync_prefs_to_config": _sync,
            "_restore_chat_history": lambda: None,
            "_redraw_areas": lambda context: None, "_redraw_areas_safe": lambda: None,
        }
        exec(compile(_operator_source(), str(_ADDON / "ui_chat.py"), "exec"), self.ns)  # noqa: S102

    def close(self) -> None:
        if self.bridge.is_running():
            self.bridge.stop()
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _run_op(self, class_name: str, kwargs: dict) -> set:
        cls = self.ns[class_name]
        op = cls()
        for name, prop in getattr(cls, "__annotations__", {}).items():
            if isinstance(prop, dict) and prop.get("_prop"):
                setattr(op, name, kwargs.get(name, prop.get("default")))
        errors: list = []

        def report(kind, text):
            self.reports.append((sorted(kind)[0], text))
            if "ERROR" in kind:
                errors.append(text)
        op.report = report
        self.ops_calls.append(class_name)
        result = op.execute(self.context)
        if errors:      # what bpy.ops does when an operator reports an error
            raise RuntimeError("Error: " + errors[-1] + "\n")
        return result

    def _start_llm(self) -> set:
        self.ops_calls.append("start_llm")
        self.llm.start_local_llama()
        return {"FINISHED"}

    def run_timers(self) -> None:
        """Run what was registered, as Blender's timer loop would (one-shots)."""
        bridge_timer = sys.modules[_PKG + ".execute_interactive"].run     # persistent: stays
        for _ in range(20):
            due = [fn for fn in self.timers.queue if fn is not bridge_timer]
            self.timers.queue = [fn for fn in self.timers.queue if fn is bridge_timer]
            if not due:
                return
            for fn in due:
                fn()

    def start(self) -> set:
        return self._run_op("BFACW_OT_agent_start", {})

    def restart(self) -> None:
        self._run_op("BFACW_OT_agent_restart", {})
        self.run_timers()


class _RestartCase(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp)})
        patcher.start()
        self.addCleanup(patcher.stop)
        # An offset whose bridge port is free here (the MCP and Local ports are stand-ins).
        self.offset = _free_port() - 9876
        self.model = self.tmp / "made-up-model.gguf"
        self.model.write_bytes(b"GGUF")

    def blender(self, mode: str = "LOCAL_LLM") -> _Blender:
        b = _Blender(mode=mode, offset=self.offset, model=str(self.model))
        self.addCleanup(b.close)
        return b


class TestRestartPorts(_RestartCase):
    """Fault 1: Restart ignored the port offset."""

    def test_restart_uses_the_offset_ports(self):
        b = self.blender("REMOTE_API")
        self.assertEqual(b.start(), {"FINISHED"})
        b.ac.launches.clear()
        b.restart()
        self.assertTrue(b.bridge.is_running())
        self.assertEqual(b.bridge.get_actual_port(), 9876 + self.offset)
        self.assertEqual(b.ac.launches, [{"port": 9191 + self.offset, "blender_port": 9876 + self.offset}])
        self.assertEqual(b.props.chat_status, "Connected")

    def test_restart_in_external_harness_restarts_the_bridge_only(self):
        b = self.blender("EXTERNAL_HARNESS")
        b.start()
        b.restart()
        self.assertEqual(b.bridge.get_actual_port(), 9876 + self.offset)
        self.assertEqual(b.ac.launches, [])
        self.assertEqual(b.props.chat_status,
                         "External Harness -- Bridge on port {:d}".format(9876 + self.offset))


class TestRestartStatus(_RestartCase):
    """Fault 2: Restart said "Connected" without checking the start."""

    def test_a_failed_mcp_server_start_shows_the_error(self):
        b = self.blender("REMOTE_API")
        b.start()
        b.ac.fail_with = "MCP server exited immediately: made-up reason"
        b.restart()
        self.assertTrue(b.props.chat_status.startswith("Error"), b.props.chat_status)
        self.assertIn("made-up reason", b.props.chat_status)
        self.assertEqual(b.props.chat_status.count("Error"), 1, b.props.chat_status)    # said once

    def test_a_failed_bridge_start_shows_the_error(self):
        b = self.blender("REMOTE_API")
        b.start()
        with mock.patch.object(b.bridge, "start", side_effect=OSError("made-up bind failure")):
            try:
                b.restart()
            except OSError:
                pass        # main raised out of its timer; the status is what counts
        self.assertTrue(b.props.chat_status.startswith("Error"), b.props.chat_status)
        self.assertIn("made-up bind failure", b.props.chat_status)
        self.assertEqual(b.props.chat_status.count("Error"), 1, b.props.chat_status)
        self.assertEqual(b.ac.launches[1:], [])     # no MCP server without a bridge

    def test_a_failing_start_is_never_reported_connected(self):
        b = self.blender("REMOTE_API")
        b.start()
        b.ac.fail_with = "boom"
        b.restart()
        self.assertNotIn("Connected", b.props.chat_status)


class TestRestartLocalServer(_RestartCase):
    """Fault 3: Restart stopped the Local server and never started it again."""

    def test_local_mode_brings_the_running_model_back(self):
        b = self.blender("LOCAL_LLM")
        b.start()
        self.assertEqual(len(b.llm.starts), 1)
        b.restart()
        self.assertEqual(len(b.llm.starts), 2)
        self.assertEqual(b.llm.starts[1]["model_path"], str(self.model))     # the same model file
        self.assertEqual(b.llm.starts[1]["port"], 8081 + self.offset)        # on the offset port
        self.assertIsNotNone(b.llm.proc)
        self.assertEqual(b.props.chat_status, "Connected")

    def test_local_mode_leaves_a_stopped_model_off_and_says_so(self):
        b = self.blender("LOCAL_LLM")
        b.start()
        b.llm.stop_local_llama()        # the model was off before Restart
        b.restart()
        self.assertEqual(len(b.llm.starts), 1)
        self.assertTrue(b.ac._agent_state.mcp_server_running)
        self.assertIn("Local model off", b.props.chat_status)
        self.assertNotIn("Connected", b.props.chat_status)

    def test_other_modes_bring_a_running_local_server_back(self):
        b = self.blender("REMOTE_API")
        b.start()
        b.llm.start_local_llama(model_path=str(self.model))     # e.g. for Follow Subject
        b.restart()
        self.assertIn("start_llm", b.ops_calls)
        self.assertIsNotNone(b.llm.proc)

    def test_other_modes_do_not_start_one_that_was_off(self):
        b = self.blender("REMOTE_API")
        b.start()
        b.restart()
        self.assertNotIn("start_llm", b.ops_calls)
        self.assertEqual(b.llm.starts, [])


class TestRestartTimer(_RestartCase):
    """Review: one pending Restart timer at most; removed with the add-on."""

    def press(self, b: _Blender) -> None:
        b._run_op("BFACW_OT_agent_restart", {})

    def test_a_double_press_starts_once_and_the_local_server_comes_back(self):
        b = self.blender("LOCAL_LLM")
        b.start()
        self.press(b)
        self.press(b)       # the first press already stopped the Local server
        self.assertEqual(b.timers.queue.count(b.ns["_restart_timer"]), 1)     # ONE restart timer
        b.run_timers()
        self.assertEqual(b.ops_calls.count("BFACW_OT_agent_start"), 2)      # Start, then one restart
        self.assertEqual(len(b.llm.starts), 2)
        self.assertIsNotNone(b.llm.proc)
        self.assertEqual(b.props.chat_status, "Connected")

    def test_the_timer_is_removed_when_the_add_on_is_unregistered(self):
        b = self.blender("REMOTE_API")
        b.start()
        self.press(b)
        self.assertTrue(b.timers.is_registered(b.ns["_restart_timer"]))
        b.ns["_cancel_restart"]()
        self.assertFalse(b.timers.is_registered(b.ns["_restart_timer"]))
        b.ns["_cancel_restart"]()       # nothing pending: no error
        src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        unregister = src[src.rindex("\ndef unregister("):]
        self.assertIn("_cancel_restart()", unregister)


class TestStaleError(_RestartCase):

    def test_stop_clears_an_old_error(self):
        b = self.blender("REMOTE_API")
        b.start()
        b.ac._agent_state.error = "an old, unrelated error"
        b.ac._agent_state.error_full = "old full"
        b._run_op("BFACW_OT_agent_stop", {})
        self.assertEqual(b.ac._agent_state.error, "")
        self.assertEqual(b.ac._agent_state.error_full, "")


class TestStopStillStops(_RestartCase):

    def test_stop_ends_everything(self):
        b = self.blender("LOCAL_LLM")
        b.start()
        b._run_op("BFACW_OT_agent_stop", {})
        self.assertFalse(b.bridge.is_running())
        self.assertFalse(b.ac._agent_state.mcp_server_running)
        self.assertIsNone(b.llm.proc)
        self.assertEqual(b.props.chat_status, "Stopped")


# ---------------------------------------------------------------------------
# The bridge's port choice (#558)

# A second "window": its bridge listens but its main thread is busy (never polls), so
# connections pile up in the backlog and a connect probe gets no answer in time.
_BUSY_WINDOW = textwrap.dedent("""
    import os, sys, time, types, importlib
    pkg = types.ModuleType("bfcw_busy"); pkg.__path__ = [sys.argv[1]]; sys.modules["bfcw_busy"] = pkg
    bridge = importlib.import_module("bfcw_busy.mcp_to_blender_server")
    bridge.start("127.0.0.1", int(sys.argv[2]))
    print("READY", bridge.get_actual_port(), os.getpid(), flush=True)
    sys.stdin.readline()
""")


class TestBridgePortChoice(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        p = _pkg()
        self.bridge = importlib.import_module(p + ".mcp_to_blender_server")
        self.si = importlib.import_module(p + ".session_identity")
        self.si._cache["t"] = 0.0
        self.addCleanup(lambda: self.bridge.is_running() and self.bridge.stop())

    def busy_window(self) -> tuple[int, int]:
        """Start the busy window on a free port and fill its backlog.  Returns (port, pid)."""
        port = _free_port()
        proc = subprocess.Popen([sys.executable, "-c", _BUSY_WINDOW, str(_ADDON), str(port)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                env=dict(os.environ))
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.stdin.close)
        line = proc.stdout.readline().split()
        self.assertEqual(line[:2], ["READY", str(port)])
        pending = []
        for _ in range(40):
            s = socket.socket()
            s.setblocking(False)
            try:
                s.connect(("127.0.0.1", port))
            except OSError:
                pass
            pending.append(s)
        self.addCleanup(lambda: [s.close() for s in pending])
        return port, int(line[2])

    def test_a_busy_second_window_does_not_fool_the_check(self):
        port, pid = self.busy_window()
        self.bridge.start("127.0.0.1", port)       # main: WinError 10048 raised here
        self.assertTrue(self.bridge.is_running())
        got = self.bridge.get_actual_port()
        self.assertGreater(got, port)
        # The socket it listens on is the one the check bound.
        self.assertEqual(self.bridge._state.sock.getsockname()[1], got)
        note = self.bridge.port_note()
        self.assertIn("Port {:d}".format(port), note)
        self.assertIn("PID {:d}".format(pid), note)            # the other window, by name
        self.assertIn("port {:d}".format(got), note)

    def test_a_held_port_moves_on_and_the_wanted_one_says_nothing(self):
        holder = socket.socket()
        if sys.platform == "win32":
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        port = holder.getsockname()[1]
        self.bridge.start("127.0.0.1", port)
        self.assertNotEqual(self.bridge.get_actual_port(), port)
        self.assertIn("another Blender / Bforartists window or another program", self.bridge.port_note())
        self.bridge.stop()
        free = _free_port()
        self.bridge.start("127.0.0.1", free)
        self.assertEqual(self.bridge.get_actual_port(), free)
        self.assertEqual(self.bridge.port_note(), "")

    def test_no_free_port_names_the_port_and_the_window(self):
        port, pid = self.busy_window()
        with mock.patch.object(self.bridge, "_PORT_SEARCH", 0):
            with self.assertRaises(OSError) as caught:
                self.bridge.start("127.0.0.1", port)
        self.assertFalse(self.bridge.is_running())
        self.assertIn("Port {:d}".format(port), str(caught.exception))
        self.assertIn("PID {:d}".format(pid), str(caught.exception))
        self.assertIn("port_offset", str(caught.exception))

    def test_an_error_that_is_no_port_conflict_is_raised_as_it_is(self):
        with mock.patch.object(self.bridge, "_bind_listener",
                               side_effect=OSError(99, "made-up: cannot assign requested address")):
            with self.assertRaises(OSError) as caught:
                self.bridge.start("127.0.0.1", _free_port())
        self.assertIn("made-up", str(caught.exception))

    def test_a_second_start_keeps_the_real_port(self):
        holder = socket.socket()
        if sys.platform == "win32":
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        port = holder.getsockname()[1]
        self.bridge.start("127.0.0.1", port)
        moved = self.bridge.get_actual_port()
        self.assertNotEqual(moved, port)
        with self.assertRaises(RuntimeError):
            self.bridge.start("127.0.0.1", port)
        self.assertEqual(self.bridge.get_actual_port(), moved)

    def test_the_search_stops_at_the_last_port(self):
        with mock.patch.object(self.bridge, "_bind_listener",
                               side_effect=OSError(98, "in use")) as bind:
            with mock.patch.object(self.bridge, "_address_in_use", return_value=True):
                with self.assertRaises(OSError) as caught:       # no OverflowError
                    self.bridge.start("127.0.0.1", 65530)
        tried = [call.args[1] for call in bind.call_args_list]
        self.assertEqual(tried, list(range(65530, 65536)))
        self.assertIn("65530", str(caught.exception))

    def test_off_windows_a_listener_that_answers_counts_as_taken(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))      # plain: a bind with SO_REUSEADDR could share it
        holder.listen(5)
        self.addCleanup(holder.close)
        port = holder.getsockname()[1]
        self.assertTrue(self.bridge._listener_answers("127.0.0.1", port))
        self.assertFalse(self.bridge._listener_answers("127.0.0.1", _free_port()))
        with mock.patch.object(sys, "platform", "linux"):
            self.bridge.start("127.0.0.1", port)
        self.assertNotEqual(self.bridge.get_actual_port(), port)

    def test_start_bridge_server_reports_the_real_port(self):
        # operators_server needs bpy: the report is checked in its source.
        src = (_ADDON / "operators_server.py").read_text(encoding="utf-8")
        self.assertIn("mcp_to_blender_server.port_note() or", src)
        self.assertIn("mcp_to_blender_server.get_actual_port() or _bridge_port", src)

    def test_in_use_errors_are_recognised(self):
        import errno
        self.assertTrue(self.bridge._address_in_use(OSError(errno.EADDRINUSE, "in use")))
        win = OSError(errno.EADDRINUSE, "in use")
        win.winerror = 10048
        self.assertTrue(self.bridge._address_in_use(win))
        self.assertFalse(self.bridge._address_in_use(OSError(errno.EADDRNOTAVAIL, "no such address")))


class TestAgentSideFollowsTheRealPort(unittest.TestCase):
    """What the MCP server / External Agent is told: the bridge port this window really owns."""

    def test_effective_ports_follow_a_moved_bridge(self):
        p = _pkg()
        bridge = importlib.import_module(p + ".mcp_to_blender_server")
        shared = importlib.import_module(p + ".shared")
        holder = socket.socket()
        if sys.platform == "win32":
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        port = holder.getsockname()[1]
        # The live-session file lives under LOCALAPPDATA: the stop at the end must still
        # see the temp folder (cleanups run last-in first-out), never the real file.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        bridge.start("127.0.0.1", port)
        self.addCleanup(lambda: bridge.is_running() and bridge.stop())
        prefs = types.SimpleNamespace(port_offset=0, bridge_port=port, mcp_port=0, llm_port=0)
        self.assertEqual(shared.effective_ports(prefs)[0], bridge.get_actual_port())
        self.assertNotEqual(bridge.get_actual_port(), port)

    def test_every_start_reads_the_ports_after_the_bridge_started(self):
        # Local / Remote: the MCP server's blender_port is read after the bridge start;
        # External Agent: its server entry too.  Both through effective_ports (actual port).
        src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        full = src[src.index("def _start_full_agent"):src.index("def _restore_chat_history")]
        self.assertLess(full.index("mcp_to_blender_server.start("),
                        full.index("start_mcp_server(port=_mcp_port, blender_port=_bridge_port)"))
        ext = src[src.index("def _start_external_agent"):src.index("def _start_bridge_only")]
        self.assertLess(ext.index("_start_bridge_only(context)"), ext.index("bridge_port, _, _ = effective_ports(prefs)"))
        self.assertTrue(re.search(r"external_agent_server_entry\(\s*blender_host=host, blender_port=bridge_port", ext))


class TestChatBarShowsTheError(unittest.TestCase):
    """The Coworker editor's top bar: a failed start shows its error, not a bare "Offline"."""

    def setUp(self) -> None:
        self.src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
        start = self.src.index("def _start_error(")
        ns: dict = {"Any": object}
        end = self.src.index("\n\n\n", start)
        exec(compile(self.src[start:end], "chat_editor.py", "exec"), ns)  # noqa: S102
        self.start_error = ns["_start_error"]

    def test_only_an_error_status_counts(self):
        self.assertEqual(self.start_error(types.SimpleNamespace(chat_status="Error: Bridge server failed: x")),
                         "Error: Bridge server failed: x")
        self.assertEqual(self.start_error(types.SimpleNamespace(chat_status="Stopped")), "")
        self.assertEqual(self.start_error(None), "")

    def test_the_bar_uses_it_when_nothing_runs(self):
        body = self.src[self.src.index("def _status("):self.src.index("def _start_error(")]
        self.assertIn('status = _start_error(props) or "Offline"', body)
        self.assertIn('failed or "Bridge Offline"', body)


if __name__ == "__main__":
    unittest.main()
