# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Threads 0 (#356): two ACP sessions on ONE agent process -- concurrent or serialized?

Throwaway spike, not shipped; plain Python, no Blender::

    python _misc/spikes/threads/acp_sessions_spike.py <out_dir> [preset_id ...]

Presets: ``claude_code`` (claude-agent-acp), ``opencode`` (opencode acp); the default
is every one that is installed.  Uses the add-on's own ``acp_client``.  Per preset it:

1. starts one agent process, ``initialize``, opens two sessions (``session/new``),
   and picks the cheapest model the agent lists (to keep the cost to a few cents);
2. prompts both sessions at once with a tiny counting prompt and records when each
   session's chunks arrive -- overlapping streams mean the agent runs them concurrently;
3. prompts both again, cancels session 1 after its first chunk, and records how each
   turn ends (session 2 should finish with ``end_turn``).

No MCP server is given to the sessions, so the agent can't touch anything.
Writes ``<out_dir>/acp_results.json``.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_ADDON = _REPO / "addon" / "bfa_coworker"
_PKG = "bfacw_spike_pkg"
if _PKG not in sys.modules:                      # import the add-on modules without bpy
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [str(_ADDON)]
    sys.modules[_PKG] = _pkg
acp = importlib.import_module(_PKG + ".acp_client")
presets = importlib.import_module(_PKG + ".acp_presets")

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
WANT = sys.argv[2:] or ["claude_code", "opencode"]

PROMPT = "Reply with the numbers 1 to 12, one per line, nothing else. Do not use any tools."
LONG = "Reply with the numbers 1 to 80 written out as English words, one per line. No tools."
_CHEAP = ("haiku", "mini", "flash", "small", "nano", "free")


class Recorder:
    """Per-session timestamps of agent_message_chunk updates (sessionId kept)."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.lock = threading.Lock()
        self.chunks: dict[str, list[float]] = {}
        self.first_chunk: dict[str, threading.Event] = {}
        self.no_session_id = 0

    def raw(self, msg: dict) -> None:
        if msg.get("method") != "session/update":
            return
        params = msg.get("params") or {}
        sid = params.get("sessionId") or ""
        upd = params.get("update") or {}
        if not sid:
            self.no_session_id += 1
        if upd.get("sessionUpdate") in ("agent_message_chunk", "agent_thought_chunk"):
            with self.lock:
                self.chunks.setdefault(sid, []).append(time.perf_counter() - self.t0)
            ev = self.first_chunk.get(sid)
            if ev is not None:
                ev.set()


def _patch_dispatch(client, rec: Recorder) -> None:
    """acp_client hands on_update only the ``update`` (no sessionId); tap the raw message."""
    orig = client._dispatch  # pylint: disable=protected-access

    def dispatch(msg):
        rec.raw(msg)
        orig(msg)
    client._dispatch = dispatch  # pylint: disable=protected-access


def _pick_cheap_model(client, sid: str, new_result: dict) -> str:
    for opt in new_result.get("configOptions") or []:
        if opt.get("id") != "model" and opt.get("category") != "model":
            continue
        values = []
        for o in opt.get("options") or []:
            if isinstance(o, dict) and "options" in o:        # grouped options
                values += [v for v in o.get("options") or []]
            else:
                values.append(o)
        for want in _CHEAP:
            for v in values:
                vid = str(v.get("value", ""))
                if want in (vid + str(v.get("name", ""))).lower():
                    try:
                        client.set_config_option(sid, opt.get("id", "model"), vid)
                        return vid
                    except acp.AcpError as ex:
                        return "set failed: {}".format(ex)
        return "kept " + str(opt.get("currentValue", ""))
    return "no model option"


def _prompt(client, sid: str, text: str, out: dict) -> None:
    s = time.perf_counter()
    try:
        r = client.prompt(sid, [{"type": "text", "text": text}], timeout=240)
        out["stop_reason"] = r.get("stopReason")
    except acp.AcpError as ex:
        out["error"] = str(ex)[:400]
    out["wall_s"] = round(time.perf_counter() - s, 2)


def _both(client, rec, sids, text, cancel_first_after_chunk=False) -> dict:
    rec.t0 = time.perf_counter()
    with rec.lock:
        rec.chunks = {}
    rec.first_chunk = {sid: threading.Event() for sid in sids}
    res = {sid: {} for sid in sids}
    threads = [threading.Thread(target=_prompt, args=(client, sid, text, res[sid])) for sid in sids]
    for t in threads:
        t.start()
    cancelled_at = None
    if cancel_first_after_chunk:
        if rec.first_chunk[sids[0]].wait(120):
            cancelled_at = round(time.perf_counter() - rec.t0, 2)
            client.cancel(sids[0])
    for t in threads:
        t.join(300)
    spans = {}
    with rec.lock:
        for sid in sids:
            c = rec.chunks.get(sid, [])
            spans[sid] = (round(c[0], 2), round(c[-1], 2), len(c)) if c else None
    a, b = spans[sids[0]], spans[sids[1]]
    overlap = bool(a and b and a[0] < b[1] and b[0] < a[1])
    return {"results": [res[s] for s in sids], "chunk_spans_s": [a, b], "streams_overlap": overlap,
            "cancelled_at_s": cancelled_at}


def run_preset(pid: str) -> dict:
    preset = next((p for p in presets.PRESETS if p.id == pid), None)
    if preset is None:
        return {"skipped": "unknown preset"}
    argv = presets.resolve_command(preset.command)
    if not argv:
        return {"skipped": "{} not installed (no `{}` on PATH)".format(preset.name, preset.command)}
    argv = argv + list(preset.args)
    cwd = OUT / ("cwd_" + pid)
    cwd.mkdir(exist_ok=True)
    rec = Recorder()
    client = acp.AcpClient(argv, cwd=str(cwd))
    _patch_dispatch(client, rec)
    out: dict = {"argv": argv}
    try:
        client.start()
        info = client.initialize()
        out["agent"] = "{} {}".format(info.name, info.version)
        out["load_session"] = info.load_session
        s1 = client.new_session(str(cwd), [])
        s2 = client.new_session(str(cwd), [])
        sids = [s1.get("sessionId"), s2.get("sessionId")]
        out["two_sessions"] = bool(all(sids) and sids[0] != sids[1])
        out["model"] = [_pick_cheap_model(client, sids[0], s1), _pick_cheap_model(client, sids[1], s2)]
        # Warm-up on one session (first-turn startup cost), then the measured runs.
        warm: dict = {}
        _prompt(client, sids[0], "Say hi in one word. No tools.", warm)
        out["warmup"] = warm
        solo: dict = {}
        rec.t0 = time.perf_counter()
        _prompt(client, sids[1], PROMPT, solo)
        out["solo_prompt"] = solo
        out["concurrent"] = _both(client, rec, sids, PROMPT)
        solo_long: dict = {}
        _prompt(client, sids[1], LONG, solo_long)
        out["solo_long"] = solo_long
        out["concurrent_long"] = _both(client, rec, sids, LONG)
        out["cancel_one"] = _both(client, rec, sids, LONG, cancel_first_after_chunk=True)
        out["updates_without_session_id"] = rec.no_session_id
        out["process_alive_after"] = client.alive
    except Exception as ex:  # pylint: disable=broad-exception-caught
        out["error"] = "{}: {}".format(type(ex).__name__, ex)[:600]
        out["stderr_tail"] = client.stderr_tail(10)
    finally:
        client.close()
    return out


def main() -> None:
    results = {}
    for pid in WANT:
        print("==", pid, flush=True)
        results[pid] = run_preset(pid)
        print(json.dumps(results[pid], indent=1), flush=True)
    (OUT / "acp_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
