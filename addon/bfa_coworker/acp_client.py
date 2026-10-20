# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Agent Client Protocol (ACP) client (Tier 4h, task "Agent 2").

Coworker is the *client* (the editor side): it starts an ACP agent such as
``opencode acp`` as a subprocess and talks JSON-RPC 2.0 to it, one JSON
object per line, over the agent's stdin/stdout.

* :class:`AcpClient` -- transport (process, reader thread, request ids,
  timeouts) plus the protocol calls Coworker uses: ``initialize``,
  ``session/new``, ``session/load``, ``session/prompt``, ``session/cancel``.
  Requests from the agent are answered here: ``session/request_permission``
  goes to a callback that may answer *later* (when the user clicks), and
  ``fs/*`` / ``terminal/*`` are refused because Coworker never advertises
  them -- the agent works through Coworker's MCP tools instead.
* :func:`update_to_events` -- maps one ``session/update`` to engine events
  (see :mod:`engine`), so an ACP agent drives the same chat panel as the
  built-in loop.

Behaviour follows the sessions recorded in ``tests/fixtures/acp`` (OpenCode
1.19.3).  Stdlib only (plus :mod:`process_guard`, so the agent closes with
Blender) and free of ``bpy``.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from . import process_guard

__all__ = (
    "AcpClient",
    "AcpError",
    "AgentInfo",
    "MCP_SERVER_NAME",
    "PROTOCOL_VERSION",
    "stdio_mcp_server",
    "update_to_events",
)

PROTOCOL_VERSION = 1

# Our MCP server's tools reach the agent prefixed with the server name.
MCP_SERVER_NAME = "bfa-coworker"
_TOOL_PREFIX = MCP_SERVER_NAME + "_"

# Longest tool result kept in an event (the Workshop shows a preview).
_RESULT_PREVIEW_CHARS = 2000

# Lines of the agent's stderr kept for error messages.
_STDERR_LINES = 200

# JSON-RPC error codes.
_METHOD_NOT_FOUND = -32601
_AGENT_EXITED = -32000


class AcpError(Exception):
    """A JSON-RPC error from the agent, or the agent process failing."""

    def __init__(self, message: str, code: int = _AGENT_EXITED, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass
class AgentInfo:
    """What ``initialize`` told us about the agent."""

    name: str = ""
    version: str = ""
    protocol_version: int = 0
    load_session: bool = False
    image: bool = False
    mcp_http: bool = False
    auth_methods: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


# Answer to an agent's permission request: call with the chosen optionId,
# or with None to answer "cancelled".
Respond = Callable[[str | None], None]


class AcpClient:
    """One ACP agent process and its JSON-RPC connection."""

    def __init__(
        self,
        argv: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
        on_permission: Callable[[dict[str, Any], Respond], None] | None = None,
    ) -> None:
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        # Called on the READER thread; must not touch bpy.
        self.on_update = on_update
        self.on_permission = on_permission
        self.proc: subprocess.Popen | None = None
        self.info = AgentInfo()
        self.commands: list[dict[str, Any]] = []
        self.exit_code: int | None = None
        self._stderr: deque[str] = deque(maxlen=_STDERR_LINES)
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, dict[str, Any]] = {}
        self._closed = threading.Event()
        self._readers: list[threading.Thread] = []

    # -- Process -----------------------------------------------------------
    def start(self) -> None:
        """Launch the agent.  Raises :class:`AcpError` if it can't start."""
        try:
            self.proc = subprocess.Popen(
                self.argv, cwd=self.cwd,
                env=None if self.env is None else {**os.environ, **self.env},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                preexec_fn=process_guard.linux_preexec(),
            )
        except OSError as ex:
            raise AcpError("could not start {!r}: {}".format(self.argv[0] if self.argv else "", ex)) from ex
        # The agent (and what it starts: node, its CLI, our MCP server) closes with
        # Blender, even when Blender crashes.
        process_guard.bind_to_parent(self.proc, "acp-agent")
        self._readers = [
            threading.Thread(target=self._read_stdout, name="acp-reader", daemon=True),
            threading.Thread(target=self._read_stderr, name="acp-stderr", daemon=True),
        ]
        for thread in self._readers:
            thread.start()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and not self._closed.is_set()

    def stderr_tail(self, lines: int = 15) -> str:
        return "\n".join(list(self._stderr)[-lines:])

    def close(self, timeout: float = 3.0) -> None:
        """End the session: close stdin, wait briefly, then kill."""
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
        self._closed.set()
        process_guard.forget(proc.pid)
        self._fail_pending("agent closed")
        # The readers end at EOF once the process is gone; then release the pipes.
        for thread in self._readers:
            if thread is not threading.current_thread():
                thread.join(timeout)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    # -- JSON-RPC ----------------------------------------------------------
    def _write(self, msg: dict[str, Any]) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise AcpError("agent is not running")
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, ValueError) as ex:
                raise AcpError(self._exit_message("agent stopped reading")) from ex

    def request(self, method: str, params: dict[str, Any], timeout: float | None = 120.0) -> Any:
        """Send a request and block until its result (or raise AcpError)."""
        if not self.alive:
            raise AcpError(self._exit_message("agent is not running"))
        done = threading.Event()
        with self._state_lock:
            self._next_id += 1
            rid = self._next_id
            slot: dict[str, Any] = {"event": done}
            self._pending[rid] = slot
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            if not done.wait(timeout):
                raise AcpError("{:s}: no answer within {:.0f} s".format(method, timeout or 0))
        finally:
            with self._state_lock:
                self._pending.pop(rid, None)
        if "error" in slot:
            err = slot["error"]
            raise AcpError(str(err.get("message", "error")), int(err.get("code", 0)), err.get("data"))
        return slot.get("result")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _read_stdout(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    self._stderr.append("[stdout, not JSON] " + line[:500])
                    continue
                if isinstance(msg, dict):
                    self._dispatch(msg)
        finally:
            try:
                self.exit_code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.exit_code = None
            self._closed.set()
            self._fail_pending(self._exit_message("agent exited"))

    def _read_stderr(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stderr is not None
        for raw in proc.stderr:
            self._stderr.append(raw.decode("utf-8", errors="replace").rstrip())

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "method" in msg and "id" in msg:
            self._handle_request(msg)
        elif "method" in msg:
            if msg["method"] == "session/update":
                update = (msg.get("params") or {}).get("update") or {}
                if update.get("sessionUpdate") == "available_commands_update":
                    self.commands = list(update.get("availableCommands") or [])
                if self.on_update is not None:
                    self.on_update(update)
        elif "id" in msg:
            with self._state_lock:
                slot = self._pending.get(msg["id"])
            if slot is not None:
                if "error" in msg:
                    slot["error"] = msg["error"] or {}
                else:
                    slot["result"] = msg.get("result")
                slot["event"].set()

    def _handle_request(self, msg: dict[str, Any]) -> None:
        rid, method = msg["id"], msg["method"]
        params = msg.get("params") or {}
        if method == "session/request_permission" and self.on_permission is not None:
            answered = threading.Event()

            def respond(option_id: str | None) -> None:
                if answered.is_set():
                    return
                answered.set()
                outcome = ({"outcome": "selected", "optionId": option_id} if option_id
                           else {"outcome": "cancelled"})
                try:
                    self._write({"jsonrpc": "2.0", "id": rid, "result": {"outcome": outcome}})
                except AcpError:
                    pass  # the agent is gone; nothing left to answer

            self.on_permission(params, respond)
            return
        if method == "session/request_permission":
            # No UI attached: never approve silently.
            self._write({"jsonrpc": "2.0", "id": rid, "result": {"outcome": {"outcome": "cancelled"}}})
            return
        self._write({"jsonrpc": "2.0", "id": rid,
                     "error": {"code": _METHOD_NOT_FOUND, "message": "Method not found: " + method}})

    def _fail_pending(self, message: str) -> None:
        with self._state_lock:
            slots = list(self._pending.values())
        for slot in slots:
            if not slot["event"].is_set():
                slot["error"] = {"code": _AGENT_EXITED, "message": message}
                slot["event"].set()

    def _exit_message(self, what: str) -> str:
        code = self.proc.poll() if self.proc is not None else None
        tail = self.stderr_tail(8)
        text = what if code is None else "{:s} (exit code {:d})".format(what, code)
        return text + (":\n" + tail if tail else "")

    # -- Protocol calls ----------------------------------------------------
    def initialize(self, timeout: float = 60.0) -> AgentInfo:
        """Handshake.  We advertise no fs/terminal: tools come from our MCP server."""
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
        }, timeout=timeout) or {}
        caps = result.get("agentCapabilities") or {}
        agent = result.get("agentInfo") or {}
        self.info = AgentInfo(
            name=str(agent.get("name", "")),
            version=str(agent.get("version", "")),
            protocol_version=int(result.get("protocolVersion") or 0),
            load_session=bool(caps.get("loadSession")),
            image=bool((caps.get("promptCapabilities") or {}).get("image")),
            mcp_http=bool((caps.get("mcpCapabilities") or {}).get("http")),
            auth_methods=list(result.get("authMethods") or []),
            raw=result,
        )
        return self.info

    def new_session(self, cwd: str, mcp_servers: list[dict[str, Any]],
                    timeout: float = 120.0) -> dict[str, Any]:
        """``session/new``; returns the result (``sessionId``, ``configOptions``...)."""
        return self.request("session/new", {"cwd": cwd, "mcpServers": mcp_servers},
                            timeout=timeout) or {}

    def load_session(self, session_id: str, cwd: str, mcp_servers: list[dict[str, Any]],
                     timeout: float = 120.0) -> dict[str, Any]:
        return self.request("session/load", {"sessionId": session_id, "cwd": cwd,
                                             "mcpServers": mcp_servers}, timeout=timeout) or {}

    def set_config_option(self, session_id: str, config_id: str, value: str,
                          timeout: float = 30.0) -> dict[str, Any]:
        """``session/set_config_option`` (mode, model, effort...); returns the new ``configOptions``."""
        return self.request("session/set_config_option", {"sessionId": session_id, "configId": config_id,
                                                           "value": value}, timeout=timeout) or {}

    def prompt(self, session_id: str, blocks: list[dict[str, Any]],
               timeout: float | None = None) -> dict[str, Any]:
        """``session/prompt``; blocks until the turn ends.  Call off the main thread."""
        return self.request("session/prompt", {"sessionId": session_id, "prompt": blocks},
                            timeout=timeout) or {}

    def cancel(self, session_id: str) -> None:
        if self.alive:
            try:
                self.notify("session/cancel", {"sessionId": session_id})
            except AcpError:
                pass


def stdio_mcp_server(command: str, args: list[str], env: dict[str, str] | None = None,
                     name: str = MCP_SERVER_NAME) -> dict[str, Any]:
    """Our MCP server in ACP's ``mcpServers`` stdio shape."""
    return {"name": name, "command": command, "args": list(args),
            "env": [{"name": k, "value": v} for k, v in (env or {}).items()]}


# -- session/update -> engine events -------------------------------------------

_PLAN_STATUS = {"pending": "todo", "in_progress": "doing", "completed": "done",
                "cancelled": "done"}


def _content_text(content: Any) -> str:
    """Text of ACP tool-call content (a list of ``{"type":"content","content":{...}}``)."""
    parts: list[str] = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        inner = item.get("content") if item.get("type") == "content" else item
        if isinstance(inner, dict) and inner.get("type") == "text":
            parts.append(str(inner.get("text", "")))
    return "\n".join(p for p in parts if p)


def _tool_name(title: str) -> str:
    """Our tool's own name: OpenCode titles it ``bfa-coworker_<tool>``, Claude Code
    ``mcp__bfa-coworker__<tool>``."""
    for prefix in ("mcp__" + MCP_SERVER_NAME + "__", _TOOL_PREFIX):
        if title.startswith(prefix):
            return title[len(prefix):]
    return title


def _plan_steps(entries: list[Any], text_key: str) -> list[dict[str, str]]:
    steps = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get(text_key):
            steps.append({"text": str(entry[text_key]),
                          "status": _PLAN_STATUS.get(str(entry.get("status", "")), "todo")})
    return steps


def update_to_events(update: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Map one ACP ``session/update`` payload to ``(kind, data)`` engine events."""
    kind = update.get("sessionUpdate")
    if kind in ("agent_message_chunk", "agent_thought_chunk"):
        content = update.get("content") or {}
        if content.get("type") != "text" or not content.get("text"):
            return []
        event = "text" if kind == "agent_message_chunk" else "thought"
        return [(event, {"delta": str(content["text"]),
                         "segment": str(update.get("messageId") or "")})]

    if kind in ("tool_call", "tool_call_update"):
        tool_id = str(update.get("toolCallId", ""))
        status = str(update.get("status") or "")
        title = str(update.get("title") or "")
        data: dict[str, Any] = {"tool_id": tool_id}
        if title:
            data["title"] = title
            data["name"] = _tool_name(title)
        raw_input = update.get("rawInput")
        if isinstance(raw_input, dict) and raw_input:
            data["args"] = raw_input
        events: list[tuple[str, dict[str, Any]]] = []
        if kind == "tool_call":
            events.append(("tool_start", dict(data, status=status or "pending")))
        elif status in ("completed", "failed"):
            result = _content_text(update.get("content"))[:_RESULT_PREVIEW_CHARS]
            end = dict(data, status=status, result=result)
            if status == "failed":
                end["error"] = result or "failed"
            events.append(("tool_end", end))
        else:
            events.append(("tool_update", dict(data, status=status or "in_progress")))
        # Agents that keep their plan in a todo tool (OpenCode's todowrite).
        todos = raw_input.get("todos") if isinstance(raw_input, dict) else None
        if isinstance(todos, list) and todos:
            steps = _plan_steps(todos, "content")
            if steps:
                events.append(("plan", {"steps": steps, "source": "agent"}))
        return events

    if kind == "plan":
        steps = _plan_steps(update.get("entries") or [], "content")
        return [("plan", {"steps": steps, "source": "agent"})] if steps else []

    return []
