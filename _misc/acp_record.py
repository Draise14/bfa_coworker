# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Record an Agent Client Protocol (ACP) session as a JSONL fixture (Tier 4h.0).

Launches an ACP agent (e.g. ``opencode acp``), plays the client side the way
Coworker will -- no ``fs``/``terminal`` capabilities, our MCP server handed
over as a stdio server -- and writes every JSON-RPC message, in both
directions, to a ``.jsonl`` file::

    python _misc/acp_record.py --out tests/fixtures/acp/opencode_tool_call.jsonl \\
        --cwd <project dir> --prompt "..." [--cancel-after 5] [--load <session id>] -- opencode acp

Permission requests are answered with the first ``allow_once`` option (or the
first option) and recorded.  ``fs/*`` and ``terminal/*`` requests are refused
with "method not found", since we never advertise them.

Fixtures are **redacted** before they are written: the agent's command list
(it can include the user's own installed skills) becomes a count, and the
project, repository and home paths become ``<PROJECT>``, ``<REPO>``, ``<HOME>``.
Review a fixture before committing it anyway.

Stdlib only.  Not shipped with the add-on.
"""

__all__ = ()

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _default_mcp_server() -> dict:
    """Our MCP server as an ACP stdio server, run from this checkout."""
    venv_py = _REPO / "mcp" / ".venv" / "Scripts" / "python.exe"
    return {
        "name": "bfa-coworker",
        "command": str(venv_py if venv_py.is_file() else sys.executable),
        "args": ["-m", "blmcp", "--transport", "stdio"],
        "env": [{"name": "PYTHONPATH", "value": str(_REPO / "mcp")}],
    }


def _redact(msg: dict, replacements: list[tuple[str, str]]) -> dict:
    """Strip user-specific data from a message before it is written."""
    update = msg.get("params", {}).get("update", {}) if isinstance(msg.get("params"), dict) else {}
    if update.get("sessionUpdate") == "available_commands_update":
        msg = json.loads(json.dumps(msg))
        commands = msg["params"]["update"].get("availableCommands", [])
        msg["params"]["update"]["availableCommands"] = [
            {"name": "<redacted>", "description": "<{:d} commands redacted>".format(len(commands))}]
    text = json.dumps(msg, ensure_ascii=True)
    for old, new in replacements:
        for variant in {old, old.replace("\\", "/"), json.dumps(old)[1:-1]}:
            text = text.replace(variant, new)
    return json.loads(text)


class Recorder:
    """Minimal ACP client: JSON-RPC 2.0, newline-delimited, over stdio."""

    def __init__(self, argv: list[str], cwd: str, out_path: Path,
                 env: dict[str, str] | None = None) -> None:
        self._replacements = sorted({
            (os.path.abspath(cwd), "<PROJECT>"), (str(_REPO), "<REPO>"),
            (str(Path.home()), "<HOME>"),
        }, key=lambda kv: -len(kv[0]))
        self._out = out_path.open("w", encoding="utf-8")
        self._t0 = time.monotonic()
        self._next_id = 0
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self.updates: list[dict] = []
        self.proc = subprocess.Popen(
            argv, cwd=cwd, env={**os.environ, **(env or {})},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._stderr: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # -- logging ---------------------------------------------------------
    def _log(self, direction: str, msg: dict) -> None:
        rec = {"t": round(time.monotonic() - self._t0, 3), "dir": direction,
               "msg": _redact(msg, self._replacements)}
        with self._lock:
            self._out.write(json.dumps(rec, ensure_ascii=True) + "\n")
            self._out.flush()

    # -- transport -------------------------------------------------------
    def _send(self, msg: dict) -> None:
        self._log("client->agent", msg)
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params: dict, timeout: float = 300.0) -> dict:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            box: queue.Queue = queue.Queue()
            self._pending[rid] = box
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return box.get(timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self._log("agent->client(non-json)", {"line": line[:2000]})
                continue
            self._log("agent->client", msg)
            if "method" in msg and "id" in msg:
                self._answer(msg)
            elif "method" in msg:
                if msg["method"] == "session/update":
                    self.updates.append(msg.get("params", {}))
            elif "id" in msg:
                box = self._pending.get(msg["id"])
                if box is not None:
                    box.put(msg)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            self._stderr.append(raw.decode("utf-8", errors="replace").rstrip())

    def _answer(self, req: dict) -> None:
        """Answer an agent->client request the way Coworker will."""
        method = req["method"]
        if method == "session/request_permission":
            options = req.get("params", {}).get("options", [])
            pick = next((o for o in options if o.get("kind") == "allow_once"),
                        options[0] if options else None)
            if pick is None:
                result = {"outcome": {"outcome": "cancelled"}}
            else:
                result = {"outcome": {"outcome": "selected", "optionId": pick.get("optionId")}}
            self._send({"jsonrpc": "2.0", "id": req["id"], "result": result})
        else:
            self._send({"jsonrpc": "2.0", "id": req["id"],
                        "error": {"code": -32601, "message": "Method not found: " + method}})

    def close(self) -> list[str]:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:  # pylint: disable=broad-exception-caught
            self.proc.kill()
        self._out.close()
        return self._stderr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--prompt", action="append", default=[],
                    help="Prompt text; repeat for several turns")
    ap.add_argument("--cancel-after", type=float, default=0.0,
                    help="Send session/cancel this many seconds into the LAST prompt")
    ap.add_argument("--no-mcp", action="store_true", help="Do not pass our MCP server")
    ap.add_argument("--load", default="", metavar="SESSION_ID",
                    help="Resume this session (session/load) instead of opening a new one")
    ap.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                    help="Extra environment variable for the agent; repeatable")
    ap.add_argument("agent", nargs=argparse.REMAINDER, help="-- <agent command and args>")
    args = ap.parse_args()
    argv = [a for a in args.agent if a != "--"]
    if not argv:
        ap.error("missing agent command after --")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(item.split("=", 1) for item in args.env)
    rec = Recorder(argv, args.cwd, out, env)
    try:
        init = rec.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
        }, timeout=60)
        print("initialize:", json.dumps(init.get("result", init))[:600])
        params = {"cwd": os.path.abspath(args.cwd),
                  "mcpServers": [] if args.no_mcp else [_default_mcp_server()]}
        if args.load:
            new = rec.request("session/load", dict(params, sessionId=args.load), timeout=120)
            print("session/load:", json.dumps(new.get("result", new))[:600])
            session_id = args.load
        else:
            new = rec.request("session/new", params, timeout=120)
            print("session/new:", json.dumps(new.get("result", new))[:600])
            session_id = new.get("result", {}).get("sessionId")
        print("session id:", session_id)
        for i, text in enumerate(args.prompt):
            last = i == len(args.prompt) - 1
            if last and args.cancel_after > 0:
                threading.Timer(args.cancel_after, rec.notify, ("session/cancel", {"sessionId": session_id})).start()
            res = rec.request("session/prompt", {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": text}],
            }, timeout=600)
            print("prompt {:d} ->".format(i + 1), json.dumps(res.get("result", res))[:300])
        # The command list is redacted in the fixture; report only its size here.
        for upd in rec.updates:
            if upd.get("update", {}).get("sessionUpdate") == "available_commands_update":
                print("available commands:", len(upd["update"].get("availableCommands", [])))
    finally:
        stderr = rec.close()
        kinds: dict[str, int] = {}
        for upd in rec.updates:
            kind = upd.get("update", {}).get("sessionUpdate", "?")
            kinds[kind] = kinds.get(kind, 0) + 1
        print("session/update kinds:", kinds)
        if stderr:
            print("agent stderr (last 15 lines):")
            print("\n".join(stderr[-15:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
