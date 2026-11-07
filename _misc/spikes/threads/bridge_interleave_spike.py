# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Threads 0 (#356): two fake-engine turns interleaving tool calls through the bridge.

Throwaway spike, not shipped.  Run in a GUI session (the timer path only exists there)::

    bforartists --factory-startup --python _misc/spikes/threads/bridge_interleave_spike.py -- <out_dir>

It imports the add-on package from this checkout (no install, nothing registered),
starts ``mcp_to_blender_server`` on a free port with the ``execute_interactive`` timer,
and drives two :class:`engine.FakeEngine` turns ("3D" and "Node") whose scripted tool
steps really go through the bridge socket, the same payload ``blmcp`` sends.  Each call
records which thread ran it and when.  Then it checks what Blender's single undo stack
does when the two turns' named steps interleave, and what today's single-slot helpers
(``coworker_api.turn_started`` / the loop's one-step failure undo) do with two turns.

Writes ``<out_dir>/bridge_results.json`` and quits ~3 s after the last action.
"""

__all__ = ()

import json
import os
import socket
import sys
import threading
import time
import traceback

import bpy  # pylint: disable=import-error

sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "addon"))

OUT_DIR = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.getcwd()
os.makedirs(OUT_DIR, exist_ok=True)

R: dict = {"version": bpy.app.version_string, "undo_steps_pref": bpy.context.preferences.edit.undo_steps}
_DONE = threading.Event()

# -- the add-on, imported but not registered ------------------------------------------
from bfa_coworker import agent_controller, coworker_api, execute_interactive  # noqa: E402
from bfa_coworker import mcp_to_blender_server as bridge  # noqa: E402
from bfa_coworker import session_identity  # noqa: E402
from bfa_coworker.engine import FakeEngine, Prompt  # noqa: E402

session_identity.set_token_required(False)   # our own client, same process
bridge.start("127.0.0.1", 19876)
PORT = bridge.get_actual_port()
bpy.app.timers.register(execute_interactive.run, first_interval=0.0, persistent=True)
coworker_api.register()      # the depsgraph edit counter Revert checks
R["bridge_port"] = PORT

_MARK = "# blmcp-toolcode-skip-preflight\nimport bpy\n"


def call(code: str, tool: str, thread: str) -> dict:
    """One bridge request, exactly what blmcp's send_code sends (plus a thread id)."""
    payload = {"type": "execute", "code": _MARK + code, "strict_json": True,
               "tool": tool, "thread": thread}
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(60)
        sock.connect(("127.0.0.1", PORT))
        sock.sendall((json.dumps(payload) + "\0").encode("utf-8"))
        buf = bytearray()
        while b"\0" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
    return json.loads(buf.partition(b"\0")[0].decode("utf-8"))


# A tool call's body: proves where it ran and keeps it busy for SLEEP seconds.
_PROBE = """
import bpy, threading, time, traceback
_ns = bpy.app.driver_namespace
_ns['_spk_in'] = _ns.get('_spk_in', 0) + 1
_peak = _ns['_spk_in']
_t0 = time.perf_counter()
{body}
time.sleep({sleep})
_t1 = time.perf_counter()
_ns['_spk_in'] -= 1
result = {{"thread": "{thread}", "seq": {seq}, "t0": _t0, "t1": _t1, "peak_in_flight": _peak,
          "main_thread": threading.current_thread() is threading.main_thread(),
          "via_timer": any('execute_interactive' in f.filename for f in traceback.extract_stack())}}
"""


def add_object(name: str) -> str:
    return ("_me = bpy.data.meshes.new('{n}')\n_ob = bpy.data.objects.new('{n}', _me)\n"
            "bpy.context.scene.collection.objects.link(_ob)\n").format(n=name)


def push(name: str) -> str:
    """The loop's own undo-push code (agent_controller._undo_code), with our step name."""
    return agent_controller._undo_code("push", name)  # pylint: disable=protected-access


STATE = """
st = bpy.context.window_manager.undo_stack
result = {"steps": [s.name for s in st.steps], "active_index": st.active_index,
          "active": st.active.name if st.active else "",
          "objects": sorted(o.name for o in bpy.data.objects if o.name[:2] in ("A_", "B_"))}
"""


def state(tag: str = "spike") -> dict:
    """The undo stack + our objects; the whole response when the call failed."""
    r = call(STATE, "state", tag)
    return r["result"] if r.get("status") == "ok" else {"error": str(r.get("message", r))[:1500]}


class BridgeFakeEngine(FakeEngine):
    """FakeEngine whose tool steps really run through the bridge.

    ``script`` items are ``("tool", {"name", "code"})``; ``gate`` (optional) is a pair
    of events for strict A/B alternation.
    """

    def __init__(self, thread: str, script, gate=None) -> None:
        super().__init__(script=[])
        self.thread, self.tool_script, self.gate = thread, script, gate
        self.calls: list[dict] = []

    def _run(self, turn_id: str, prompt: Prompt) -> None:
        self.bus.emit("turn_start", turn_id, text=prompt.text, mode=prompt.chat_mode)
        for i, (name, code) in enumerate(self.tool_script):
            if self.gate:
                self.gate[0].wait(30)
                self.gate[0].clear()
            tid = "{:s}-{:d}".format(self.thread, i)
            self.bus.emit("tool_start", turn_id, id=tid, title=name)
            sent = time.perf_counter()
            resp = call(code, name, self.thread)
            back = time.perf_counter()
            self.calls.append({"tool": name, "sent": sent, "back": back, "status": resp.get("status"),
                               "result": resp.get("result"), "message": str(resp.get("message", ""))[:300]})
            self.bus.emit("tool_end", turn_id, id=tid, status="completed")
            if self.gate:
                self.gate[1].set()
        self.bus.emit("turn_end", turn_id, stop_reason="end_turn")


def run_pair(a: BridgeFakeEngine, b: BridgeFakeEngine) -> None:
    a.send(Prompt(text="thread " + a.thread))
    b.send(Prompt(text="thread " + b.thread))
    a.wait(120)
    b.wait(120)


def scenario_serialization(n_calls: int = 6, sleep: float = 0.05) -> dict:
    """Both turns fire as fast as they can: do calls overlap? who goes first?"""
    def script(th):
        return [("probe", _PROBE.format(body="", sleep=sleep, thread=th, seq=i)) for i in range(n_calls)]
    a, b = BridgeFakeEngine("A", script("A")), BridgeFakeEngine("B", script("B"))
    t0 = time.perf_counter()
    run_pair(a, b)
    wall = time.perf_counter() - t0
    runs = sorted((c["result"] for c in a.calls + b.calls if c.get("result")), key=lambda r: r["t0"])
    overlaps = sum(1 for x, y in zip(runs, runs[1:]) if y["t0"] < x["t1"])
    order = "".join(r["thread"] for r in runs)
    lat = [c["back"] - c["sent"] for c in a.calls + b.calls]
    return {"calls": len(runs), "sleep_per_call_s": sleep, "wall_s": round(wall, 3),
            "ideal_serial_s": round(2 * n_calls * sleep, 3), "overlapping_pairs": overlaps,
            "max_in_flight": max(r["peak_in_flight"] for r in runs),
            "all_main_thread": all(r["main_thread"] for r in runs),
            "all_via_timer": all(r["via_timer"] for r in runs), "execution_order": order,
            "latency_s": {"min": round(min(lat), 3), "max": round(max(lat), 3),
                          "mean": round(sum(lat) / len(lat), 3)},
            "statuses": sorted({c["status"] for c in a.calls + b.calls})}


def scenario_bridge_overhead(n: int = 20) -> dict:
    """A trivial call from one thread: the bridge's own round trip (timer tick + exec)."""
    lat = []
    for i in range(n):
        s = time.perf_counter()
        call(_PROBE.format(body="", sleep=0, thread="A", seq=i), "probe", "A")
        lat.append(time.perf_counter() - s)
    lat.sort()
    return {"n": n, "median_ms": round(lat[n // 2] * 1000, 1), "max_ms": round(lat[-1] * 1000, 1)}


def scenario_undo() -> dict:
    """Named, interleaved undo steps; then the one-step failure undo and a Revert."""
    out: dict = {}
    gate_a, gate_b = threading.Event(), threading.Event()
    gate_a.set()                       # A goes first, then strictly alternate
    a = BridgeFakeEngine("A", [
        ("push", push("Coworker (3D): before turn")),
        ("add", add_object("A_Cube") + push("Coworker (3D): add cube")),
        ("add", add_object("A_Light") + push("Coworker (3D): add light")),
    ], gate=(gate_a, gate_b))
    b = BridgeFakeEngine("B", [
        ("push", push("Coworker (Node): before turn")),
        ("add", add_object("B_Tree") + push("Coworker (Node): add tree")),
        ("add", add_object("B_Group") + push("Coworker (Node): add group")),
    ], gate=(gate_b, gate_a))
    run_pair(a, b)
    out["push_statuses"] = [c["status"] for c in a.calls + b.calls]
    out["push_errors"] = [c["message"] for c in a.calls + b.calls if c["status"] != "ok"]
    # The loop's own code with an apostrophe in the step name (a per-thread label
    # like "Coworker (3D)'s turn"): _undo_code does not escape it.
    r = call(push("Coworker (3D)'s turn"), "push", "A")
    out["apostrophe_label_push"] = (r.get("status"), str(r.get("message", ""))[:160])
    out["after_interleave"] = state()

    # The loop's failure rollback (agent_controller ~L7352): ONE ed.undo() for "A's last step".
    r = call(agent_controller._undo_code("undo"), "undo", "A")  # pylint: disable=protected-access
    out["one_step_undo_status"] = r.get("status")
    out["after_one_step_undo_by_A"] = state()

    # Redo back, then "Revert A's turn" the way coworker_api.revert_turn does (undo_history
    # to the last step with A's Before name).
    call("# bpy.ops.ed.undo()\nimport bfa_coworker.coworker_api as c\n"
         "w = bpy.context.window_manager.windows[0]\n"
         "with bpy.context.temp_override(window=w, screen=w.screen):\n    bpy.ops.ed.redo()\n", "redo", "spike")
    out["after_redo"] = state()
    steps = out["after_redo"].get("steps", [])
    name = "Coworker (3D): before turn"
    idx = len(steps) - 1 - steps[::-1].index(name) if name in steps else -1
    r = call("# bpy.ops.ed.undo()\nimport bfa_coworker.coworker_api as c\n"
             "result = {{'ok': c._undo(to_item={:d})}}\n".format(idx), "revert", "A")
    out["revert_A_to_item"] = idx
    out["revert_A_status"] = (r.get("status"), r.get("result"))
    out["after_revert_A"] = state()

    # A new push after the revert: the redo tail (B's steps) is discarded for good.
    call(add_object("A_After") + push("Coworker (3D): after revert"), "add", "A")
    out["after_new_push"] = state()
    return out


def scenario_turn_checkpoint() -> dict:
    """Today's single-slot turn checkpoint (coworker_api._turn) with two turns."""
    out: dict = {}
    call("import bfa_coworker.coworker_api as c\nc.turn_started(\"Coworker (3D)'s turn\")\n", "turn", "A")
    out["turn_after_A_start"] = dict(coworker_api._turn)  # pylint: disable=protected-access
    call("import bfa_coworker.coworker_api as c\nc.turn_started(\"Coworker (Node)'s turn\")\n", "turn", "B")
    out["turn_after_B_start"] = dict(coworker_api._turn)  # pylint: disable=protected-access
    call(add_object("A_X"), "add", "A")
    call("import bfa_coworker.coworker_api as c\nc.turn_finished(\"Coworker (3D)'s turn\")\n", "turn", "A")
    call(add_object("B_X"), "add", "B")
    call("import bfa_coworker.coworker_api as c\nc.turn_finished(\"Coworker (Node)'s turn\")\n", "turn", "B")
    out["turn_after_both_finish"] = dict(coworker_api._turn)  # pylint: disable=protected-access
    st = state()
    out["steps_tail"] = st.get("steps", [])[-6:]
    out["can_revert"] = call("import bfa_coworker.coworker_api as c\nresult = {'v': c.can_revert()}\n",
                             "q", "spike")["result"]
    return out


def scenario_thread_stamp() -> dict:
    """A payload stamped with a thread id is accepted (unknown keys are ignored today)."""
    r = call("result = {'ok': True}\nimport bpy\n", "probe", "Node")
    return {"status": r.get("status")}


def _controller() -> None:
    try:
        time.sleep(1.0)                                   # let the UI settle
        R["thread_stamp"] = scenario_thread_stamp()
        R["bridge_overhead"] = scenario_bridge_overhead()
        R["serialization"] = scenario_serialization()
        R["serialization_long"] = scenario_serialization(n_calls=4, sleep=0.3)
        R["undo"] = scenario_undo()
        R["turn_checkpoint"] = scenario_turn_checkpoint()
    except Exception:  # pylint: disable=broad-exception-caught
        R["error"] = traceback.format_exc()
    finally:
        with open(os.path.join(OUT_DIR, "bridge_results.json"), "w", encoding="utf-8") as fh:
            json.dump(R, fh, indent=2)
        _DONE.set()


_quit_at = [0.0]


def _watch() -> float | None:
    """Main thread: once the controller is done, wait ~3 s, stop the bridge, quit."""
    if not _DONE.is_set():
        return 0.2
    if not _quit_at[0]:
        _quit_at[0] = time.monotonic() + 3.0
        return 0.2
    if time.monotonic() < _quit_at[0]:
        return 0.2
    bridge.stop()
    bpy.ops.wm.quit_blender()
    return None


threading.Thread(target=_controller, daemon=True, name="spike-controller").start()
bpy.app.timers.register(_watch, first_interval=1.0, persistent=True)
