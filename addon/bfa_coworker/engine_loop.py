# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Local and Remote through the engine seam (Tier 4i.1b).

:class:`LoopEngine` runs our own turn loop (``run_conversation_turn``) for
the Local LLM and Remote API modes behind the :mod:`engine` interface.  It
owns what the chat operators used to inline: the turn worker thread, the
hand-off to the message queue when busy, auto-dequeue when a turn ends, and
Stop.  Alongside the loop's existing state writes it reports each turn as
events on its :class:`~engine.EventBus`; the main thread drains them into
:class:`~engine.TurnRecord` values (Tier 4i.1c renders from those).

The controller (the ``agent_controller`` module) is passed in, so this file
does not import ``bpy`` and is unit-tested with a stand-in controller.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from .engine import EngineCaps, EventBus, Prompt, TurnRecord, new_turn_id
from .session_memory import is_system_note

__all__ = (
    "LoopConfig",
    "LoopEngine",
)

# Finished turn records kept for the panel (oldest dropped first).
_MAX_RECORDS = 50


@dataclass
class LoopConfig:
    """Connection settings captured on the main thread at send time."""

    llm_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    mcp_port: int = 0


class _StreamTracker:
    """Turn the loop's accumulated stream text into (delta, segment) pairs.

    ``run_conversation_turn`` reports the whole text received so far.  When
    a new LLM request starts in the same turn (after a tool round) the text
    starts over; that begins a new segment.
    """

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._index = 1
        self.text = ""

    @property
    def segment(self) -> str:
        return "{:s}{:d}".format(self._prefix, self._index)

    def feed(self, full: str) -> tuple[str, str]:
        if full.startswith(self.text):
            delta = full[len(self.text):]
        else:
            self._index += 1
            delta = full
        self.text = full
        return delta, self.segment

    def new_segment(self) -> str:
        self._index += 1
        self.text = ""
        return self.segment


class LoopEngine:
    """Our turn loop (Local LLM / Remote API) as an :class:`~engine.Engine`."""

    def __init__(self, controller: Any, name: str = "Local",
                 caps: EngineCaps | None = None) -> None:
        self._c = controller
        self.name = name
        self.caps = caps or EngineCaps(label="Local", images=True, usage=True, own_loop=False)
        self.bus = EventBus()
        self.config = LoopConfig()
        # Hooks set by the UI.  Both run on the turn WORKER thread, as the
        # inline code they replace did.
        self.on_redraw: Callable[[], None] | None = None
        self.on_turn_finished: Callable[[], None] | None = None
        self.records: OrderedDict[str, TurnRecord] = OrderedDict()

    # -- Engine protocol ---------------------------------------------------
    def send(self, prompt: Prompt) -> str:
        """Start a turn with the last :attr:`config` (no queueing)."""
        return self._start(prompt, self.config)

    def cancel(self) -> None:
        self._c.request_stop()

    def is_busy(self) -> bool:
        return bool(self._c._agent_state.turn_active)

    def stop(self) -> None:
        self.cancel()

    # -- Chat entry points -------------------------------------------------
    def submit(self, prompt: Prompt, config: LoopConfig) -> tuple[str | None, int]:
        """Start a turn, or queue it when one is running.

        Returns ``(turn_id, 0)`` for a started turn or ``(None, position)``
        for a queued one.  After the user pressed Stop the old worker may
        still be unwinding (a blocking read or tool call cannot be cut off
        at once); queueing behind it would look frozen, so a new turn starts
        instead and supersedes the old worker through the loop's turn token.
        """
        self.config = config
        if self._c._agent_state.turn_active and not self._c._stop_event.is_set():
            return None, self.enqueue(prompt, config)
        return self._start(prompt, config), 0

    def enqueue(self, prompt: Prompt, config: LoopConfig) -> int:
        """Queue *prompt* for after the current turn; return its position."""
        return self._c.enqueue_message(
            message=prompt.text,
            chat_mode=prompt.chat_mode,
            llm_url=config.llm_url,
            api_key=config.api_key,
            model=config.model,
            mcp_port=config.mcp_port,
            attachments=prompt.images or None,
            attachment_names=prompt.image_names or None,
        )

    # -- Main thread ---------------------------------------------------------
    def drain(self) -> int:
        """Apply queued events to the turn records (call on the main thread)."""
        events = self.bus.drain()
        for event in events:
            record = self.records.get(event.turn_id)
            if record is None:
                record = TurnRecord(event.turn_id)
                self.records[event.turn_id] = record
                while len(self.records) > _MAX_RECORDS:
                    self.records.popitem(last=False)
            record.apply(event)
        return len(events)

    @property
    def latest_record(self) -> TurnRecord | None:
        return next(reversed(self.records.values())) if self.records else None

    # -- Worker ------------------------------------------------------------
    def _start(self, prompt: Prompt, config: LoopConfig) -> str:
        turn_id = new_turn_id()
        threading.Thread(target=self._run, args=(turn_id, prompt, config), daemon=True).start()
        return turn_id

    def _redraw(self) -> None:
        if self.on_redraw is not None:
            self.on_redraw()

    def _run(self, turn_id: str, prompt: Prompt, config: LoopConfig) -> None:
        state = self._c._agent_state
        text = _StreamTracker("m")
        thought = _StreamTracker("r")
        prev_error = state.error
        history: Any = None
        self.bus.emit("turn_start", turn_id, text=prompt.text, mode=prompt.chat_mode)

        def on_status(value: str) -> None:
            # Plain Python state only: the main-thread timer mirrors it into
            # the bpy chat property (an off-main RNA write races Blender).
            state.ui_status = value
            self.bus.emit("status", turn_id, text=value)
            self._redraw()

        def on_stream(tracker: _StreamTracker, kind: str, full: str) -> None:
            delta, segment = tracker.feed(full)
            if delta:
                self.bus.emit(kind, turn_id, delta=delta, segment=segment)
            self._redraw()

        try:
            history = self._c.run_conversation_turn(
                user_message=prompt.text,
                on_text=None,
                on_reasoning=lambda _r: self._redraw(),
                on_status=on_status,
                llm_url=config.llm_url or None,
                api_key=config.api_key or None,
                model=config.model,
                mcp_port=config.mcp_port,
                chat_mode=prompt.chat_mode,
                on_stream_text=lambda t: on_stream(text, "text", t),
                on_stream_reasoning=lambda r: on_stream(thought, "thought", r),
                attachments=prompt.images or None,
                attachment_names=prompt.image_names or None,
            )
        except Exception as ex:  # pylint: disable=broad-exception-caught
            state.error = str(ex)
            self.bus.emit("error", turn_id, message=str(ex))
        finally:
            self._finish(turn_id, prompt, history, prev_error, text)
            if self.on_turn_finished is not None:
                self.on_turn_finished()
            self._dequeue_next()
            state.ui_status = "Idle"
            self._redraw()

    def _finish(self, turn_id: str, prompt: Prompt, history: Any, prev_error: Any,
                text: _StreamTracker) -> None:
        state = self._c._agent_state
        final = _last_assistant_text(history, prompt.text)
        if final and final != text.text:
            # Non-streamed reply, or the loop rewrote the streamed text.
            self.bus.emit("text", turn_id, delta=final, segment=text.new_segment())
        usage = getattr(state, "turn_usage", None) or {}
        tokens = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        tokens = {k: v for k, v in tokens.items() if isinstance(v, int)}
        if tokens:
            self.bus.emit("usage", turn_id, **tokens)
        if self._c._stop_event.is_set():
            stop_reason = "cancelled"
        elif state.error and state.error != prev_error:
            stop_reason = "error"
        else:
            stop_reason = "end_turn"
        self.bus.emit("turn_end", turn_id, stop_reason=stop_reason)

    def _dequeue_next(self) -> None:
        item = self._c.dequeue_message()
        if not item:
            return
        self._c._agent_state.ui_status = "Processing queued message..."
        self._redraw()
        prompt = Prompt(
            text=item["message"],
            images=list(item.get("attachments") or []),
            image_names=list(item.get("attachment_names") or []),
            chat_mode=item.get("chat_mode", "AGENT"),
        )
        config = LoopConfig(
            llm_url=item.get("llm_url"),
            api_key=item.get("api_key"),
            model=item.get("model"),
            mcp_port=item.get("mcp_port", 0),
        )
        self._start(prompt, config)


def _last_assistant_text(history: Any, request: str) -> str:
    """Text of this turn's last assistant message (``""`` if none).

    *history* is the whole conversation.  Only assistant messages after the
    last user message count, and that user message must be this turn's
    *request*: a turn blocked by the loop's re-entrancy guard returns the
    history without it, and must not report the previous turn's reply.
    """
    if not isinstance(history, list):
        return ""
    found = ""
    for message in reversed(history):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "user" and is_system_note(message):
            continue  # injected context, not the user speaking
        if role == "user":
            return found if request and request in str(message.get("content", "")) else ""
        if role == "assistant" and not found:
            content = message.get("content")
            if isinstance(content, str) and content:
                found = content
    return ""
