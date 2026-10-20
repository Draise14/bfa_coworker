# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Engine seam: one event stream for every engine (Tier 4i.1).

Coworker can be driven by different engines -- the local llama-server loop,
a remote API through the same loop, or an external agent over ACP (Tier 4h).
Each engine reports what happens in a turn as :class:`AgentEvent` values on
an :class:`EventBus`; the chat UI renders only from those events, so every
engine gets the same panel, Workshop, plan and Stop behaviour.

Threading model:

* engines call :meth:`EventBus.emit` from any thread (worker, reader, ...);
* the main thread calls :meth:`EventBus.drain` (from a ``bpy.app.timers``
  callback, wired in Tier 4i.1b) and applies the events, e.g. to a
  :class:`TurnRecord`.  Nothing here touches ``bpy``.

Streaming engines send many tiny text/thought chunks (hundreds per turn were
recorded from OpenCode, see ``tests/fixtures/acp``); :meth:`EventBus.drain`
merges consecutive chunks of the same kind and turn into one event so a
redraw applies a handful of events, not hundreds.

Deliberately free of ``bpy`` so it can be unit-tested outside Blender.
"""

from __future__ import annotations

import itertools
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

__all__ = (
    "AgentEvent",
    "EVENT_KINDS",
    "Engine",
    "EngineCaps",
    "EventBus",
    "FakeEngine",
    "Prompt",
    "TurnRecord",
    "new_turn_id",
)

# Every kind an engine may emit.  ``data`` keys per kind:
#   turn_start   text (the user's request), mode
#   status       text (one line, e.g. "Reading your message...")
#   text         delta (reply text to append), segment? (message id)
#   thought      delta (reasoning text to append), segment?
#                A turn can hold several assistant messages (preamble, tool
#                round, final reply); each has its own *segment* id.
#   tool_start   tool_id, name, title, args
#   tool_update  tool_id, status ("pending"|"in_progress"), title?, args?, result?
#   tool_end     tool_id, status ("completed"|"failed"), result?, error?
#   plan         steps: [{"text", "status": "todo"|"doing"|"done"}], goal?
#   approval     request (an ApprovalRequest dict, Tier 4i.3)
#   question     question, choices, allow_text (Tier 4i.5 ask_user)
#   usage        input_tokens?, output_tokens?, cached_tokens?, total_tokens?
#   turn_end     stop_reason ("end_turn"|"cancelled"|"error"|...)
#   error        message
EVENT_KINDS: frozenset[str] = frozenset((
    "turn_start", "status", "text", "thought",
    "tool_start", "tool_update", "tool_end",
    "plan", "approval", "question", "usage",
    "turn_end", "error",
))

# Kinds whose consecutive events are merged by :meth:`EventBus.drain`.
_STREAM_KINDS = frozenset(("text", "thought"))

_TOOL_FINAL = frozenset(("completed", "failed"))


# Plan mode (#194): the tools a plan may use -- they look, they don't change anything.
PLAN_SAFE_TOOLS: frozenset[str] = frozenset({
    "get_objects_summary", "get_object_detail_summary", "get_scene_digest", "get_operation_history",
    "get_blendfile_summary_datablocks", "get_blendfile_summary_missing_files",
    "get_blendfile_summary_of_linked_libraries", "get_blendfile_summary_path_info",
    "get_blendfile_summary_usage_guess", "get_active_node_tree", "get_node_group_interface",
    "get_asset_libraries", "get_asset_tags", "list_asset_catalogs", "search_assets",
    "search_polyhaven_assets", "get_polyhaven_status", "get_screenshot_of_window_as_image",
    "get_screenshot_of_window_as_json", "get_screenshot_of_area_as_image", "search_api_docs",
    "search_manual_docs", "get_python_api_docs", "read_tool_output", "ask_user", "update_plan", "load_tools",
    "list_blender_templates",
})
PLAN_NOTE = ("[Plan mode] Don't change the Blender scene or any file. Look at what you need (scene, docs), "
             "then write a short step plan for this request with the update_plan tool (3-8 concrete steps), "
             "and reply with a one-paragraph summary. Don't carry the plan out: that comes later.")


def new_turn_id() -> str:
    """A short unique turn id (``t_`` + 12 hex characters)."""
    return "t_" + uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class AgentEvent:
    """One thing that happened in a turn."""

    kind: str
    turn_id: str
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0


@dataclass(frozen=True)
class EngineCaps:
    """What an engine supports; the UI shows or hides controls from this."""

    label: str = ""           # Engine chip, e.g. "Local", "Remote", "Agent"
    images: bool = False      # Accepts image attachments
    cancel: bool = True       # Stop works mid-turn
    load_session: bool = False  # Can resume a saved session
    usage: bool = False       # Reports token usage
    models: bool = False      # Model can be chosen from the panel
    modes: bool = False       # Agent advertises modes (e.g. plan / build)
    own_loop: bool = False    # Engine runs its own turn loop (memory, stop rules)


@dataclass
class Prompt:
    """One user request, as every engine receives it."""

    text: str
    images: list[str] = field(default_factory=list)       # file paths
    image_names: list[str] = field(default_factory=list)  # display names
    context: str = ""          # live context chip (Tier 4b B6)
    chat_mode: str = "AGENT"   # "AGENT", "ASK" or "PLAN"


class Engine(Protocol):
    """What the chat UI needs from an engine."""

    name: str
    caps: EngineCaps
    bus: EventBus

    def send(self, prompt: Prompt) -> str:
        """Start a turn for *prompt*; return its turn id."""

    def cancel(self) -> None:
        """Stop the running turn (no-op when idle)."""

    def is_busy(self) -> bool:
        """True while a turn runs."""

    def stop(self) -> None:
        """Shut the engine down (end of session)."""


class EventBus:
    """Thread-safe event queue: emit from any thread, drain on the main thread."""

    def __init__(self, max_pending: int = 50000) -> None:
        self._lock = threading.Lock()
        self._queue: deque[AgentEvent] = deque()
        self._seq = itertools.count(1)
        self._max_pending = max_pending
        self.dropped = 0

    def emit(self, kind: str, turn_id: str, **data: Any) -> AgentEvent:
        """Queue an event.  Raises ``ValueError`` for an unknown *kind*."""
        if kind not in EVENT_KINDS:
            raise ValueError("unknown event kind: {!r}".format(kind))
        with self._lock:
            event = AgentEvent(kind, turn_id, data, next(self._seq))
            if len(self._queue) >= self._max_pending and kind in _STREAM_KINDS:
                # A stalled UI must not grow memory without bound; stream
                # chunks are the only events that are safe to lose.
                self.dropped += 1
                return event
            self._queue.append(event)
        return event

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()

    def drain(self, max_events: int | None = None) -> list[AgentEvent]:
        """Take queued events (oldest first), merging consecutive stream chunks.

        *max_events* bounds how many queued events are taken this call (the
        rest stay queued); merged chunks count once per original event.
        """
        with self._lock:
            n = len(self._queue) if max_events is None else min(max_events, len(self._queue))
            taken = [self._queue.popleft() for _ in range(n)]
        return _merge_stream_chunks(taken)

    def drain_to(self, apply: Callable[[AgentEvent], None], max_events: int | None = None) -> int:
        """Drain and call *apply* for each event; return how many were applied."""
        events = self.drain(max_events)
        for event in events:
            apply(event)
        return len(events)


def _merge_stream_chunks(events: Iterable[AgentEvent]) -> list[AgentEvent]:
    out: list[AgentEvent] = []
    for event in events:
        prev = out[-1] if out else None
        if (prev is not None and event.kind in _STREAM_KINDS and prev.kind == event.kind
                and prev.turn_id == event.turn_id
                and prev.data.get("segment", "") == event.data.get("segment", "")):
            merged = dict(prev.data)
            merged["delta"] = str(prev.data.get("delta", "")) + str(event.data.get("delta", ""))
            out[-1] = AgentEvent(prev.kind, prev.turn_id, merged, prev.seq)
        else:
            out.append(event)
    return out


@dataclass
class ToolRow:
    """One tool call as the Workshop shows it."""

    tool_id: str
    name: str = ""
    title: str = ""
    status: str = "pending"
    args: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    error: str = ""


@dataclass
class TurnRecord:
    """What the panel shows for one turn, built only from events."""

    turn_id: str
    request: str = ""
    status: str = ""
    # Ordered (segment id, text) pairs: one per assistant message of the turn.
    text_segments: list[list[str]] = field(default_factory=list)
    thought: str = ""
    tools: list[ToolRow] = field(default_factory=list)
    plan: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stop_reason: str = ""

    @property
    def finished(self) -> bool:
        return bool(self.stop_reason)

    @property
    def text(self) -> str:
        """All reply text of the turn, one paragraph per message."""
        return "\n\n".join(t for _, t in self.text_segments if t)

    @property
    def last_text(self) -> str:
        """The most recent message's text (the final reply once finished)."""
        for _, text in reversed(self.text_segments):
            if text:
                return text
        return ""

    def _append_text(self, segment: str, delta: str) -> None:
        if self.text_segments and self.text_segments[-1][0] == segment:
            self.text_segments[-1][1] += delta
        else:
            self.text_segments.append([segment, delta])

    def _tool(self, tool_id: str) -> ToolRow:
        for row in self.tools:
            if row.tool_id == tool_id:
                return row
        row = ToolRow(tool_id)
        self.tools.append(row)
        return row

    def apply(self, event: AgentEvent) -> None:
        """Fold *event* into this record (events of other turns are ignored)."""
        if event.turn_id != self.turn_id:
            return
        kind, data = event.kind, event.data
        if kind == "turn_start":
            self.request = str(data.get("text", self.request))
        elif kind == "status":
            self.status = str(data.get("text", ""))
        elif kind == "text":
            self._append_text(str(data.get("segment", "")), str(data.get("delta", "")))
        elif kind == "thought":
            self.thought += str(data.get("delta", ""))
        elif kind in ("tool_start", "tool_update", "tool_end"):
            row = self._tool(str(data.get("tool_id", "")))
            for key in ("name", "title"):
                if data.get(key):
                    setattr(row, key, str(data[key]))
            if isinstance(data.get("args"), dict) and data["args"]:
                row.args = dict(data["args"])
            if "result" in data:
                row.result = str(data["result"])
            if data.get("error"):
                row.error = str(data["error"])
            status = data.get("status")
            if status and row.status not in _TOOL_FINAL:
                row.status = str(status)
            elif kind == "tool_end" and row.status not in _TOOL_FINAL:
                row.status = "failed" if row.error else "completed"
        elif kind == "plan":
            self.plan = [dict(step) for step in data.get("steps", [])]
        elif kind == "usage":
            for key, value in data.items():
                if isinstance(value, int):
                    self.usage[key] = self.usage.get(key, 0) + value
        elif kind == "approval":
            self.approvals.append(dict(data.get("request", {})))
        elif kind == "question":
            self.questions.append(dict(data))
        elif kind == "error":
            self.errors.append(str(data.get("message", "")))
        elif kind == "turn_end":
            self.stop_reason = str(data.get("stop_reason", "end_turn"))
            self.status = ""


class FakeEngine:
    """Scripted engine for tests: replays a fixed list of (kind, data) steps.

    With ``threaded=True`` (default) :meth:`send` returns at once and a worker
    thread emits the script, so tests exercise the real cross-thread path;
    :meth:`cancel` ends the turn with ``stop_reason="cancelled"``.
    """

    name = "Fake"

    def __init__(self, script: list[tuple[str, dict[str, Any]]] | None = None,
                 caps: EngineCaps | None = None, threaded: bool = True,
                 step_delay: float = 0.0) -> None:
        self.caps = caps or EngineCaps(label="Fake", usage=True)
        self.bus = EventBus()
        self.script = list(script or [("text", {"delta": "Done."})])
        self._threaded = threaded
        self._step_delay = step_delay
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.prompts: list[Prompt] = []

    def send(self, prompt: Prompt) -> str:
        if self.is_busy():
            raise RuntimeError("FakeEngine: a turn is already running")
        self.prompts.append(prompt)
        turn_id = new_turn_id()
        self._cancel.clear()
        if self._threaded:
            self._thread = threading.Thread(target=self._run, args=(turn_id, prompt), daemon=True)
            self._thread.start()
        else:
            self._run(turn_id, prompt)
        return turn_id

    def _run(self, turn_id: str, prompt: Prompt) -> None:
        self.bus.emit("turn_start", turn_id, text=prompt.text, mode=prompt.chat_mode)
        for kind, data in self.script:
            # wait() doubles as the step delay and returns True once cancelled.
            cancelled = (self._cancel.wait(self._step_delay) if self._step_delay
                         else self._cancel.is_set())
            if cancelled:
                self.bus.emit("turn_end", turn_id, stop_reason="cancelled")
                return
            self.bus.emit(kind, turn_id, **data)
        self.bus.emit("turn_end", turn_id, stop_reason="end_turn")

    def cancel(self) -> None:
        self._cancel.set()

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: float = 5.0) -> None:
        """Test helper: block until the running turn ends."""
        if self._thread is not None:
            self._thread.join(timeout)

    def stop(self) -> None:
        self.cancel()
        self.wait()
