# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
An external agent as the chat's engine (Tier 4, task "Agent 4").

:class:`AcpEngine` runs an agent app on this computer (OpenCode, Claude Code,
Codex, Gemini, Copilot -- see :mod:`acp_presets`) over the Agent Client
Protocol (:mod:`acp_client`).  You chat in Coworker's own chat; the agent
plans and works with **Coworker's Blender tools** -- our MCP server is handed
to its session -- and its turn streams back into the same chat state as a
local turn (:mod:`chat_state`), so the panel and the GPU chat draw it the same:
text, thinking, Workshop rows, plan, Stop, Queue.

Permissions: requests for Coworker's own tools are approved (they run in
Blender with a checkpoint to revert to); everything else the agent asks for
(its shell, file edits) is refused, so it stays inside Blender.

Threads: the agent's messages arrive on the client's reader thread and are put
on an :class:`engine.EventBus`; :meth:`drain` applies them on the main thread.
Free of ``bpy``.
"""

from __future__ import annotations

import base64
import os
import threading
from collections import deque
from typing import Any, Callable

from .acp_client import MCP_SERVER_NAME, AcpClient, AcpError, stdio_mcp_server, update_to_events
from .chat_state import ChatStateApplier
from .engine import EngineCaps, EventBus, Prompt, new_turn_id

__all__ = (
    "AcpEngine",
)


class AcpEngine:
    """One external agent session, driven from the chat."""

    name = "External Agent"

    def __init__(self, state: Any, client_factory: Callable[..., AcpClient] = AcpClient) -> None:
        self.state = state                      # the add-on's agent state (chat history...)
        self.caps = EngineCaps(label="External Agent", images=False, cancel=True, own_loop=True)
        self.bus = EventBus()
        self.applier = ChatStateApplier(state)
        self.client: AcpClient | None = None
        self.session_id = ""
        self.agent_label = ""
        self.status = "Not connected"
        self.error = ""
        self.connected = False
        self.connecting = False
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._turn_id = ""
        self._queue: deque[Prompt] = deque()
        self.on_change: Callable[[], None] | None = None     # redraw hook (any thread)
        self.only_coworker_tools = True      # off: the agent may also use its own tools

    # -- connection ----------------------------------------------------------------
    def connect(self, argv: list[str], mcp_entry: dict[str, Any], cwd: str,
                env: dict[str, str] | None = None, label: str = "", wait: bool = False) -> None:
        """Start the agent and open a session (in the background unless *wait*)."""
        if self.connected or self.connecting:
            return
        self.connecting, self.error = True, ""
        self.agent_label = label or (os.path.basename(argv[0]) if argv else "agent")
        self.status = "Starting {:s}...".format(self.agent_label)
        self._changed()
        worker = threading.Thread(target=self._connect, args=(argv, mcp_entry, cwd, env), daemon=True,
                                  name="acp-connect")
        worker.start()
        if wait:
            worker.join()

    def _connect(self, argv: list[str], mcp_entry: dict[str, Any], cwd: str,
                 env: dict[str, str] | None) -> None:
        try:
            os.makedirs(cwd, exist_ok=True)
            client = self._client_factory(argv, cwd=cwd, env=env, on_update=self._on_update,
                                          on_permission=self._on_permission)
            client.start()
            info = client.initialize()
            server = stdio_mcp_server(mcp_entry["command"], list(mcp_entry.get("args") or []),
                                      dict(mcp_entry.get("env") or {}))
            session = client.new_session(cwd, [server])
            self.session_id = str(session.get("sessionId", ""))
            if not self.session_id:
                raise AcpError("the agent did not open a session")
            self.client = client
            self.caps = EngineCaps(label="External Agent", images=info.image, cancel=True,
                                   load_session=info.load_session, own_loop=True)
            name = info.name or self.agent_label
            self.status = "Connected to {:s}{:s}".format(name, " " + info.version if info.version else "")
            self.connected = True
        except (AcpError, OSError, KeyError) as ex:
            self.error = str(ex)
            self.status = "Couldn't connect: {:s}".format(str(ex).splitlines()[0] if str(ex) else "error")
            try:
                if "client" in locals():
                    client.close()      # type: ignore[possibly-undefined]
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        finally:
            self.connecting = False
            self._changed()

    def disconnect(self) -> None:
        client, self.client = self.client, None
        self.connected, self.session_id = False, ""
        self._queue.clear()
        if client is not None:
            if self._turn_id:
                client.cancel(self.session_id)
            client.close()
        self.status = "Not connected"
        self._changed()

    # -- the chat's engine interface (like LoopEngine) ------------------------------
    def is_busy(self) -> bool:
        return bool(self._turn_id)

    def submit(self, prompt: Prompt, _config: Any = None) -> tuple[str | None, int]:
        """Start a turn, or queue it while one runs: ``(turn_id, 0)`` / ``(None, position)``."""
        if self.is_busy():
            return None, self.enqueue(prompt)
        return self._start(prompt), 0

    def enqueue(self, prompt: Prompt, _config: Any = None) -> int:
        self._queue.append(prompt)
        return len(self._queue)

    def cancel(self) -> None:
        self._queue.clear()
        if self.client is not None and self._turn_id:
            self.client.cancel(self.session_id)

    def stop(self) -> None:
        self.cancel()

    def drain(self) -> int:
        """Apply the agent's events to the chat state (main thread)."""
        return self.bus.drain_to(self.applier.apply)

    # -- turns ------------------------------------------------------------------------
    def _start(self, prompt: Prompt) -> str:
        turn_id = new_turn_id()
        with self._lock:
            self._turn_id = turn_id
        self.bus.emit("turn_start", turn_id, text=prompt.text, mode=prompt.chat_mode)
        threading.Thread(target=self._run, args=(turn_id, prompt), daemon=True, name="acp-turn").start()
        return turn_id

    def _blocks(self, prompt: Prompt) -> list[dict[str, Any]]:
        text = prompt.text
        if prompt.context:
            text = "{:s}\n\n[Blender: {:s}]".format(text, prompt.context)
        if prompt.chat_mode == "ASK":
            text += "\n\n[Answer only; don't change the Blender scene.]"
        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if self.caps.images:
            for uri in prompt.images:
                head, _, data = str(uri).partition(",")
                if head.startswith("data:") and ";base64" in head and data:
                    mime = head[5:].split(";")[0] or "image/png"
                    try:
                        base64.b64decode(data[:64] + "=" * (-len(data[:64]) % 4), validate=False)
                    except ValueError:
                        continue
                    blocks.append({"type": "image", "mimeType": mime, "data": data})
        return blocks

    def _run(self, turn_id: str, prompt: Prompt) -> None:
        stop_reason = "end_turn"
        try:
            if self.client is None or not self.connected:
                raise AcpError("not connected -- press Start first")
            result = self.client.prompt(self.session_id, self._blocks(prompt), timeout=None)
            stop_reason = str(result.get("stopReason") or "end_turn")
        except AcpError as ex:
            stop_reason = "error"
            self.bus.emit("error", turn_id, message=str(ex).splitlines()[0] if str(ex) else "error")
            if self.client is not None and not self.client.alive:
                self.connected = False
                self.status = "The agent stopped -- press Start to reconnect"
        finally:
            self.bus.emit("turn_end", turn_id, stop_reason=stop_reason)
            with self._lock:
                self._turn_id = ""
            self._changed()
        if self._queue and self.connected:
            self._start(self._queue.popleft())

    # -- from the agent (reader thread) -----------------------------------------------
    def _on_update(self, params: dict[str, Any]) -> None:
        turn_id = self._turn_id
        if not turn_id:
            return
        for kind, data in update_to_events(params.get("update") or params):
            self.bus.emit(kind, turn_id, **data)
        self._changed()

    def _on_permission(self, params: dict[str, Any], respond: Callable[[str | None], None]) -> None:
        """Coworker's tools: yes.  Anything else (the agent's shell, file edits): no."""
        call = params.get("toolCall") or {}
        title = str(call.get("title") or call.get("kind") or "")
        options = params.get("options") or []
        # OpenCode: "bfa-coworker_<tool>"; Claude Code: "mcp__bfa-coworker__<tool>".
        ours = MCP_SERVER_NAME in title or "blender" in title.lower() or not self.only_coworker_tools
        wanted = ("allow_always", "allow_once") if ours else ("reject_once", "reject_always")
        pick = next((o for kind in wanted for o in options if o.get("kind") == kind), None)
        respond(str(pick.get("optionId")) if pick else None)
        if not ours and self._turn_id:
            self.bus.emit("status", self._turn_id,
                          text="Refused {:s}: the agent can only use Coworker's Blender tools".format(title or "a tool"))

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
