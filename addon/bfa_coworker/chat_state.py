# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Engine events -> the chat panel's state (Tier 4i, task "Engine 3").

The chat panel draws turns from ``AgentState`` -- ``conversation_history``
plus a few live fields (``streaming_text``, ``reasoning_text``,
``is_thinking``, ``turn_phase``, ``ui_status``).  Our own turn loop writes
those directly.  :class:`ChatStateApplier` writes them from engine events
instead, in exactly the shapes the loop uses, so a turn from an external
agent (Tier 4h) draws with the same bubble, Workshop, live readout and
reply -- the panel can't tell which engine produced it.

Message shapes (what ``ui_chat._split_turn`` expects):

* ``{"role": "user", "content": request}`` starts a turn;
* ``{"role": "reasoning", "label": ..., "content": ...}`` and
  ``{"role": "tool", "name": ..., "content": ...}`` go in the Workshop (a
  failed tool's content starts with ``"Error"``);
* the conclusion is an assistant message without ``tool_calls``, and the
  turn's last message carries ``turn_seconds``.

Call :meth:`ChatStateApplier.apply` on the MAIN thread only (from the event
drain); it mutates the history list the panel draws.  Free of ``bpy``.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .engine import AgentEvent

__all__ = (
    "ChatStateApplier",
)

# Workshop label for assistant text written before a tool call (a preamble
# such as "I'll look that up first"); kept out of the conclusion slot.
_PREAMBLE_LABEL = "Coworker"


class ChatStateApplier:
    """Fold one engine's events into the chat panel's ``AgentState``."""

    def __init__(self, state: Any,
                 on_plan: Callable[[list[dict[str, str]]], None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.state = state
        self.on_plan = on_plan
        # Who answers (Engine 4): "Claude Code" on an External Agent; the reply keeps it.
        self.agent_name = ""
        self._clock = clock
        self._turn_id = ""
        self._started = 0.0
        self._segment = ""
        self._text = ""          # current text segment (the live readout)
        self._reasoning = ""     # reasoning not yet written to history
        self._tools: dict[str, dict[str, Any]] = {}

    # -- helpers -----------------------------------------------------------
    @property
    def _history(self) -> list[dict[str, Any]]:
        return self.state.conversation_history

    def _flush_reasoning(self) -> None:
        if self._reasoning.strip():
            self._history.append({"role": "reasoning", "label": "Thinking",
                                  "content": self._reasoning})
        self._reasoning = ""

    def _flush_preamble(self) -> None:
        """Move the current text segment into the Workshop (it wasn't the reply)."""
        if self._text.strip():
            self._history.append({"role": "reasoning", "label": _PREAMBLE_LABEL,
                                  "content": self._text})
        self._text = ""
        self.state.streaming_text = ""

    # -- events --------------------------------------------------------------
    def apply(self, event: AgentEvent) -> None:
        kind, data = event.kind, event.data
        if kind == "turn_start":
            self._begin(event.turn_id, str(data.get("text", "")))
            return
        if event.turn_id != self._turn_id:
            return  # a stale event from a previous turn
        state = self.state
        if kind == "status":
            state.ui_status = str(data.get("text", ""))
        elif kind == "text":
            segment = str(data.get("segment", ""))
            if segment != self._segment and self._text:
                # A new assistant message: the previous one was a preamble.
                self._flush_reasoning()
                self._flush_preamble()
            self._segment = segment
            self._text += str(data.get("delta", ""))
            state.streaming_text = self._text
            state.turn_phase = "thinking"
        elif kind == "thought":
            self._reasoning += str(data.get("delta", ""))
            state.reasoning_text = self._reasoning
            state.turn_phase = "thinking"
        elif kind == "tool_start":
            self._tool_start(data)
        elif kind == "tool_update":
            row = self._tools.get(str(data.get("tool_id", "")))
            if row is None:
                self._tool_start(data)
            elif data.get("name"):
                row["name"] = str(data["name"])
        elif kind == "tool_end":
            self._tool_end(data)
        elif kind == "plan":
            if self.on_plan is not None:
                self.on_plan([dict(s) for s in data.get("steps", [])])
        elif kind == "usage":
            record = getattr(state, "record_usage", None)
            if callable(record):
                record({k: v for k, v in {
                    "prompt_tokens": data.get("input_tokens"),
                    "completion_tokens": data.get("output_tokens"),
                    "total_tokens": data.get("total_tokens"),
                }.items() if isinstance(v, int)})
        elif kind == "error":
            state.error = str(data.get("message", ""))
        elif kind == "turn_end":
            self._end(str(data.get("stop_reason", "end_turn")))

    def _begin(self, turn_id: str, request: str) -> None:
        state = self.state
        self._turn_id = turn_id
        self._started = self._clock()
        self._segment, self._text, self._reasoning = "", "", ""
        self._tools = {}
        self._history.append({"role": "user", "content": request, "turn_start": True})
        state.is_thinking = True
        state.thinking_start_time = self._started
        state.turn_phase = "reading"
        state.streaming_text = ""
        state.reasoning_text = ""
        state.error = ""

    def _tool_start(self, data: dict[str, Any]) -> None:
        tool_id = str(data.get("tool_id", ""))
        if tool_id in self._tools:
            return
        # Text so far was a preamble to this call; reasoning came before it.
        self._flush_reasoning()
        self._flush_preamble()
        name = str(data.get("name") or data.get("title") or "tool")
        row = {"role": "tool", "name": name, "tool_call_id": tool_id, "content": "(running)"}
        if isinstance(data.get("args"), dict) and data["args"]:
            row["args"] = dict(data["args"])     # the call's input (code, query...) for the chat
        self._tools[tool_id] = row
        self._history.append(row)
        self.state.ui_status = "Using {:s}...".format(name)
        self.state.turn_phase = "thinking"

    def _tool_end(self, data: dict[str, Any]) -> None:
        tool_id = str(data.get("tool_id", ""))
        row = self._tools.get(tool_id)
        if row is None:
            self._tool_start(data)
            row = self._tools[tool_id]
        if data.get("name"):
            row["name"] = str(data["name"])
        if data.get("status") == "failed" or data.get("error"):
            message = str(data.get("error") or data.get("result") or "failed")
            # The panel marks a tool red when its content starts with "Error".
            row["content"] = message if message.startswith("Error") else "Error: " + message
        else:
            row["content"] = str(data.get("result", "")) or "(done)"

    def _end(self, stop_reason: str) -> None:
        state = self.state
        self._flush_reasoning()
        reply = self._text.strip()
        if not reply and stop_reason == "cancelled":
            reply = "Stopped."
        elif not reply and stop_reason == "error":
            reply = "The agent stopped with an error: {:s}".format(state.error or "unknown error")
        if reply:
            message = {"role": "assistant", "content": reply}
            if self.agent_name:
                message["agent"] = self.agent_name
            self._history.append(message)
        if self._history:
            self._history[-1]["turn_seconds"] = round(self._clock() - self._started, 1)
        state.is_thinking = False
        state.turn_phase = ""
        state.streaming_text = ""
        state.ui_status = "Stopped" if stop_reason == "cancelled" else "Idle"
        self._turn_id = ""
        self._tools = {}
