# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The chat view of the GPU chat (Tier 4, task "GPU 2").

Draws a conversation, in the chat panel's own message shapes (see
``chat_state`` / ``chat_turns``), onto a :class:`layout.Canvas`:

* one box per turn: your message, the collapsible **Workshop** (tool rows,
  failed ones marked, reasoning, notes), and the reply as markdown;
* code blocks in monospace with a **Copy** button; tables as aligned rows;
* while a turn runs, its live text and phase instead of the reply;
* laid out from the **bottom up**: the newest turn sits just above the
  input, ``scroll`` counts pixels up from the bottom (0 = follow the newest),
  and a "jump to newest" button appears when you've scrolled away.

Icons come first, like in the native chat, so a row reads before its words:
you, Coworker (the add-on's agent icon), a finished turn, the Workshop (a
warning when a step failed), each tool row, notes, code and Copy, and
keyframe markers on headings -- full-colour Bforartists icons, see :mod:`.icons`.

The same code draws the full editor and the compact sidebar (``compact``).
Clicks come back from :meth:`ChatView.draw` as ``(action, value)`` pairs for
the input layer to carry out: ``("copy", text)``, ``("toggle", turn_key)``,
``("link", url)``, ``("jump", "")``.  Free of ``bpy``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..chat_status import fmt_duration, phase_text, spinner_char
from ..chat_turns import NOTE_TITLES, group_turns, is_system_note_msg, split_turn
from .layout import Canvas, InteractionState, Rect, wrap_text
from .markdown import Block, Span, parse
from .style import RGBA, Style
from .suggestions import TYPE, suggestions

__all__ = (
    "ChatView",
    "message_text",
)

_ERROR = (0.92, 0.40, 0.33, 1.0)   # failed tool rows (themes have no error colour)
# Heading sizes relative to body text, close to Blender 5.3's label_markdown.
_HEADING_SCALE = {1: 1.6, 2: 1.4, 3: 1.2, 4: 1.05}
# Heading markers, as in the native chat: keyframe shapes that shrink with the level.
_HEADING_ICON = {1: "KEYTYPE_EXTREME_VEC", 2: "KEYTYPE_MOVING_HOLD_VEC", 3: "KEYTYPE_JITTER_VEC"}


# "[attached: a.png, b.jpg]" -- the marker a user message carries for its images.
_ATTACHED = re.compile(r"\s*\[attached:\s*([^\]]*)\]")
# Thumbnail heights (in widget units) for the S / M / L sizes.
THUMB_SIZES = {"S": 2.5, "M": 4.0, "L": 7.0}
_LIVE_LINES = 3        # the live-thinking window, in lines
_SCROLL_IDLE_ALPHA = 0.4   # the scrollbar's strength with the mouse away (#279)
_FRAME_LINES = 14      # code frames show this many lines (Show all for the rest)
_RESULT_LINES = 3      # a tool's result: a few lines (often not for reading; Show all: #236)
_RESULT_LINE_CHARS = 160   # ... each cut this short while folded (one long JSON line wraps a lot)
_ENTRY_CHARS = 600     # a Workshop thinking entry shows this much (Show all for the rest)


def split_attachments(text: str) -> tuple[str, list[str]]:
    """(text without the marker, attached image names)."""
    names: list[str] = []
    for m in _ATTACHED.finditer(text):
        names += [n.strip() for n in m.group(1).split(",") if n.strip()]
    return _ATTACHED.sub("", text).strip(), names


def message_text(content: Any) -> str:
    """Plain text of a message's content (string or a list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "")


def _soft(color: RGBA, amount: float = 0.35) -> RGBA:
    return (color[0], color[1], color[2], color[3] * (1.0 - amount))


def _lighten(color: RGBA, amount: float) -> RGBA:
    return (min(1.0, color[0] + amount), min(1.0, color[1] + amount), min(1.0, color[2] + amount), color[3])


def _tool_failed(msg: dict) -> bool:
    content = message_text(msg.get("content"))
    return content.startswith("Error") or '"status": "error"' in content


@dataclass
class ChatView:
    """View state of one chat surface (scroll, open Workshops, height cache)."""

    scroll: float = 0.0
    expanded: set[str] = field(default_factory=set)
    agent_icon: str = "WIZARD"          # the host passes "GHOST_ENABLED" in Blender
    thumb_size: str = "M"               # attachment thumbnails: S, M or L
    # The host sets it when the last turn can be undone in one step (Engine 6b).
    revertable: bool = False
    # Suggestion buttons under the newest reply (#220); the host turns them off while a card asks.
    suggest: bool = True
    _suggest_idx: int = -1
    # The host's lookup of an image's pixel size by name (None: not loaded).
    image_size: Callable[[str], "tuple[int, int] | None"] | None = None
    content_height: float = 0.0
    _heights: dict[tuple, float] = field(default_factory=dict)
    _drag_y: float | None = None        # mouse y while the scrollbar handle is dragged
    _live_h: dict[int, float] = field(default_factory=dict)   # running turn: tallest stream so far
    _row_h: dict[tuple, float] = field(default_factory=dict)  # Workshop rows, measured once (#218)

    # -- public ----------------------------------------------------------------
    def scroll_by(self, dy: float, visible_height: float) -> None:
        """Scroll up (dy > 0) or down; clamped to the content."""
        self.scroll = max(0.0, min(self.scroll + dy, max(0.0, self.content_height - visible_height)))

    def draw(self, canvas: Canvas, rect: Rect, history: list[dict], live: Any = None,
             compact: bool = False) -> list[tuple[str, str]]:
        """Draw *history* into *rect*; return the actions clicked this frame."""
        actions: list[tuple[str, str]] = []
        style = canvas.style
        pad = style.pad * (0.7 if compact else 1.0)
        gap = pad
        width = rect.w - 2 * pad - self._bar_widths(style)[1]   # leave room for the scrollbar
        self._scrollbar_input(canvas, rect)
        turns = group_turns([m for m in history if m.get("role") != "system"])
        active = len(turns) - 1 if (live is not None and getattr(live, "is_thinking", False)) else -1
        if not turns:
            canvas.wrapped(rect.x + pad, rect.top - pad, rect.w - 2 * pad,
                           "Start a conversation: type below and press Enter.", _soft(style.text))
            self.content_height = 0.0
            return actions

        self._last = len(turns) - 1
        self._suggest_idx = self._last if active < 0 and self.suggest else -1
        heights = [self._turn_height(canvas, turn, idx, width, idx == active, live, compact)
                   for idx, turn in enumerate(turns)]
        self.content_height = sum(heights) + gap * (len(turns) + 1)
        self.scroll = max(0.0, min(self.scroll, max(0.0, self.content_height - rect.h)))

        canvas.push_clip(rect)
        bottom = rect.y + gap - self.scroll
        for idx in range(len(turns) - 1, -1, -1):
            h = heights[idx]
            if bottom > rect.top:
                break
            if bottom + h >= rect.y:
                self._draw_turn(canvas, turns[idx], idx, rect.x + pad, bottom + h, width,
                                idx == active, live, compact, actions)
            bottom += h + gap
        canvas.pop_clip()

        self._draw_scrollbar(canvas, rect)
        size = style.unit * 1.2
        right = rect.right - pad - self._bar_widths(style)[1]
        if self.scroll > style.unit:
            jump = Rect(right - size, rect.y + pad, size, size)
            if canvas.button(jump, "jump", "", icon="TRIA_DOWN"):
                actions.append(("jump", ""))
            right = jump.x - pad * 0.5
        if active >= 0 and split_turn(turns[active])[1]:
            # Fold / unfold the running turn's Workshop from wherever you are in the chat
            # (#196); the Thinking box always shows -- it lets you feel what's happening.
            # Icons only (#216): the fold arrow and the Workshop's own icon.
            key = "t{:d}".format(active)
            side = canvas.icon_size()
            w = 2 * side + 2.0 * style.pad
            pill = Rect(right - w, rect.y + pad, w, size)
            if canvas.button(pill, "fold_workshop", ""):
                actions.append(("toggle", key))
            iy = pill.y + (pill.h - side) / 2
            lead = pill.x + style.pad * 0.75
            canvas.icon(Rect(lead, iy, side, side), "TRIA_DOWN" if key in self.expanded else "TRIA_RIGHT",
                        style.button.text)
            canvas.icon(Rect(lead + side + style.pad * 0.5, iy, side, side), "PACKAGE", style.button.text)
        return actions

    # -- measuring -----------------------------------------------------------------
    def _turn_height(self, canvas: Canvas, turn: list[dict], idx: int, width: float,
                     active: bool, live: Any, compact: bool) -> float:
        key = None
        if not active:
            key = (idx, len(turn), sum(len(message_text(m.get("content"))) for m in turn),
                   round(width), canvas.style.scale, f"t{idx}" in self.expanded, compact, self.thumb_size,
                   idx == self._suggest_idx,
                   tuple(sorted(k for k in self.expanded if k.startswith("x{:d}:".format(idx)))))
            cached = self._heights.get(key)
            if cached is not None:
                return cached
        scratch = Canvas(canvas.style, canvas.measure, InteractionState())
        top = 0.0
        new_top = self._draw_turn(scratch, turn, idx, 0.0, top, width, active, live, compact, [])
        height = top - new_top
        if key is not None:
            if len(self._heights) > 2000:
                self._heights.clear()
            self._heights[key] = height
        return height

    # -- drawing -------------------------------------------------------------------
    def _draw_turn(self, canvas: Canvas, turn: list[dict], idx: int, x: float, top: float,
                   width: float, active: bool, live: Any, compact: bool,
                   actions: list[tuple[str, str]]) -> float:
        style = canvas.style
        pad = style.pad * (0.7 if compact else 1.0)
        user, process, conclusion = split_turn(turn)
        if user is not None:
            indent = 0.0 if compact else style.unit * 2
            raw = message_text(user.get("content"))
            text, images = split_attachments(raw)
            side, gap = canvas.icon_size(), pad * 0.6
            copy_w = style.line_height
            inner_w = width - indent - 2 * pad - side - gap - copy_w - pad * 0.5
            thumbs = self._layout_thumbs(canvas, images, inner_w, compact)
            thumbs_h = (max(r.top for _n, r, _ok in thumbs) + pad) if thumbs else 0.0
            h = (canvas.text_height(text, inner_w) if text else style.line_height) + thumbs_h + 2 * pad
            box = Rect(x + indent, top - h, width - indent, h)
            canvas.box(box, colors=style.button, selected=True, outline=False)
            canvas.icon(self._first_line_icon(canvas, box.x + pad, box.top - pad, style.line_height),
                        "USER", style.button.text_sel)
            self._copy_button(canvas, box.right - pad * 0.5, box.top - pad * 0.5, "copyuser:{:d}".format(idx),
                              text, actions)
            text_top = box.top - pad
            if text:
                text_top = canvas.wrapped(box.x + pad + side + gap, text_top, inner_w, text, style.button.text_sel)
            self._draw_thumbs(canvas, thumbs, box.x + pad + side + gap, text_top - pad, actions)
            top = box.y - pad
        # The same spinner and words as the panel chat (chat_status), driven by
        # the state's thinking_dots tick.
        spinner = spinner_char(live) if active else ""
        if active:
            # While it runs: the phase and the Thinking box, the Workshop right under
            # them, then the reply as it streams -- the order a finished turn has (#178).
            top = self._draw_live(canvas, idx, x, top, width, live, spinner, pad, actions, stream=False)
            if process:
                top = self._draw_workshop(canvas, process, turn, idx, x, top - pad * 0.5, width, active,
                                          compact, actions, spinner) - pad
            return self._draw_live(canvas, idx, x, top, width, live, spinner, pad, actions, stream=True)
        self._live_h.pop(idx, None)
        if process:
            top = self._draw_workshop(canvas, process, turn, idx, x, top, width, active, compact, actions,
                                      spinner) - pad
        if conclusion is not None:
            seconds = next((m["turn_seconds"] for m in reversed(turn)
                            if isinstance(m.get("turn_seconds"), (int, float))), None)
            who = str(conclusion.get("agent") or "Coworker")     # Engine 4: who answered
            label = who + ("  |  " + fmt_duration(seconds) if seconds is not None and seconds >= 1 else "")
            row_top = top
            top = self._icon_line(canvas, x, top, self.agent_icon, label, _soft(style.text, 0.2))
            done = canvas.icon_size()
            canvas.icon(self._first_line_icon(canvas, x + width - done, row_top, style.line_height),
                        "CHECKMARK", style.text)
            # Copy the whole reply, like the panel chat's copy button.
            reply = message_text(conclusion.get("content"))
            self._copy_button(canvas, x + width - done - style.pad * 0.5, row_top, "copymsg:{:d}".format(idx),
                              reply, actions)
            if self.revertable and idx == getattr(self, "_last", -1):
                side = style.line_height
                right = x + width - done - style.pad - side
                if canvas.button(Rect(right - side, row_top - side, side, side), "revert_turn", "",
                                 icon="LOOP_BACK"):
                    actions.append(("revert_turn", ""))
            top = self._draw_markdown(canvas, parse(reply), idx, x, top, width, actions)
            if idx == self._suggest_idx:
                top = self._draw_suggestions(canvas, reply, x, top, width, actions)
        return top

    def _draw_suggestions(self, canvas: Canvas, reply: str, x: float, top: float, width: float,
                          actions: list[tuple[str, str]]) -> float:
        """The next steps the reply offers, as large buttons; a click sends one (#220)."""
        style = canvas.style
        h, gap = style.unit * 1.35, style.pad * 0.4
        room = width - canvas.icon_size() - 3 * style.pad
        for n, (label, text) in enumerate(suggestions(reply)):
            shown = label
            while shown and canvas.measure(shown + "\u2026", "ui", style.font_size) > room:
                shown = shown[:-1]
            if shown != label:
                shown = shown.rstrip() + "\u2026"
            button = Rect(x, top - style.pad * 0.5 - h, width, h)
            if canvas.button(button, "suggest:{:d}".format(n), shown, align="left", icon="FORWARD"):
                # "No, something else...": you finish it in the input (#270).
                actions.append(("suggest", text) if text != TYPE else ("suggest_type", "No, "))
            top = button.y - gap
        return top

    def _draw_live(self, canvas: Canvas, idx: int, x: float, top: float, width: float, live: Any,
                   spinner: str, pad: float, actions: list[tuple[str, str]], stream: bool = False) -> float:
        """The running turn.  ``stream=False``: phase + time and the live thinking (a
        fixed-height window that shows its newest lines; folded with the floating
        Thinking button).  ``stream=True``: the reply as it streams (its height only
        grows, so a half-typed line can't make the chat jitter)."""
        style = canvas.style
        if stream:
            streaming = getattr(live, "streaming_text", "") or ""
            if streaming:
                start = top
                end = self._draw_markdown(canvas, parse(streaming), idx, x, top, width, actions)
                used = max(self._live_h.get(idx, 0.0), start - end)
                self._live_h[idx] = used
                top = start - used
            return top
        label = "{:s} {:s}".format(phase_text(live), spinner)
        started = float(getattr(live, "thinking_start_time", 0.0) or 0.0)
        if started:
            label += "  |  " + fmt_duration(time.time() - started)
        reasoning = " ".join(str(getattr(live, "reasoning_text", "") or "").split())
        top = self._icon_line(canvas, x, top, self.agent_icon, label, _soft(style.text, 0.15))
        if reasoning:
            inner_w = width - 3 * pad
            lines = wrap_text(reasoning, inner_w, canvas.measure, "ui", style.font_size)[-_LIVE_LINES:]
            h = _LIVE_LINES * style.line_height + pad
            frame = Rect(x + pad * 0.5, top - pad * 0.5 - h, width - pad, h)
            canvas.box(frame, colors=style.text_field)
            canvas.push_clip(frame)
            line_top = frame.top - pad * 0.5 - (_LIVE_LINES - len(lines)) * style.line_height
            for line in lines:      # newest lines at the bottom, like a terminal
                line_top -= style.line_height
                canvas.text(frame.x + pad, line_top + (style.line_height - style.font_size) / 2
                            + style.font_size * 0.2, line, _soft(style.text_field.text, 0.25))
            canvas.pop_clip()
            top = frame.y - pad * 0.5
        return top

    def _copy_button(self, canvas: Canvas, right: float, top: float, widget_id: str, text: str,
                     actions: list[tuple[str, str]]) -> None:
        """A small square Copy button whose top-right corner is (right, top)."""
        side = canvas.style.line_height
        if canvas.button(Rect(right - side, top - side, side, side), widget_id, "", icon="COPYDOWN"):
            actions.append(("copy", text))

    def _code_frame(self, canvas: Canvas, x: float, top: float, width: float, code: str, label: str,
                    widget_id: str, actions: list[tuple[str, str]], color: RGBA | None = None,
                    expand_key: str = "", max_lines: int = _FRAME_LINES, line_chars: int = 0) -> float:
        """Code (or a tool's output) in its own framed box with a header and Copy; a long
        one shows its first lines and Show all (*expand_key*, #169)."""
        style = canvas.style
        pad = style.pad
        lines = code.rstrip("\n").split("\n")
        is_open = bool(expand_key) and expand_key in self.expanded
        shown = lines if is_open else lines[:max_lines]
        more = len(lines) - len(shown)
        cut = bool(line_chars) and not is_open and any(len(line) > line_chars for line in shown)
        if cut:
            shown = [line if len(line) <= line_chars else line[:line_chars] + "…" for line in shown]
        text = "\n".join(shown) + ("\n... {:d} more line{:s}".format(more, "" if more == 1 else "s")
                                   if more and not expand_key else "")
        head = style.line_height + pad * 0.5
        inner_w = width - 2 * pad
        h = canvas.text_height(text, inner_w, font="mono") + head + pad
        frame = Rect(x, top - h, width, h)
        canvas.box(frame, colors=style.text_field)
        soft = _soft(style.text_field.text, 0.4)
        canvas.label(Rect(frame.x + pad, frame.top - head, frame.w, head), label, soft)
        self._copy_button(canvas, frame.right - pad * 0.5, frame.top - pad * 0.25, widget_id, code, actions)
        canvas.wrapped(frame.x + pad, frame.top - head, inner_w, text, color or style.text_field.text, font="mono")
        if expand_key and (more or is_open or cut):     # cut lines: Show all brings them back whole
            return self._show_all(canvas, x, frame.y, expand_key, is_open, more, "line", actions)
        return frame.y

    def _show_all(self, canvas: Canvas, x: float, top: float, key: str, is_open: bool, more: int, unit: str,
                  actions: list[tuple[str, str]]) -> float:
        """The small Show all / Show less button under a cut-off entry (#169)."""
        style = canvas.style
        label = ("Show less" if is_open else "Show all" if more <= 0 else
                 "Show all ({:d} more {:s}{:s})".format(more, unit, "" if more == 1 else "s"))
        w = canvas.icon_size() + canvas.measure(label, "ui", style.font_size) + 2 * style.pad
        h = style.line_height * 1.1
        if canvas.button(Rect(x, top - h - style.pad * 0.2, w, h), "more:" + key, label,
                         icon="TRIA_DOWN" if is_open else "TRIA_RIGHT"):
            actions.append(("toggle", key))
        return top - h - style.pad * 0.2

    @staticmethod
    def _call_inputs(turn: list[dict]) -> dict[str, tuple[str, dict]]:
        """tool_call_id -> (tool name, arguments) from the turn's assistant tool calls."""
        out: dict[str, tuple[str, dict]] = {}
        for msg in turn:
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {"input": args}
                out[str(call.get("id", ""))] = (str(fn.get("name", "")), args if isinstance(args, dict) else {})
        return out

    # -- attachment thumbnails --------------------------------------------------------
    def _layout_thumbs(self, canvas: Canvas, names: list[str], width: float,
                       compact: bool) -> list[tuple[str, Rect, bool]]:
        """(name, rect, loaded) per image, wrapped in rows; rects grow down from y=0."""
        style = canvas.style
        units = THUMB_SIZES.get(self.thumb_size, 4.0)
        if compact:
            units = min(units, THUMB_SIZES["S"])
        height = style.unit * units
        out: list[tuple[str, Rect, bool]] = []
        x = y = 0.0
        for name in names:
            size = self.image_size(name) if self.image_size is not None else None
            if size and size[0] > 0 and size[1] > 0:
                w, h, loaded = min(width, height * size[0] / size[1]), height, True
            else:   # not in this file: a chip with the name
                w = min(width, canvas.measure(name, "ui", style.font_size) + canvas.icon_size() + 2 * style.pad)
                h, loaded = style.unit, False
            if x > 0 and x + w > width:
                x, y = 0.0, max(r.top for _n, r, _ok in out) + style.pad * 0.5
            out.append((name, Rect(x, y, w, h), loaded))
            x += w + style.pad * 0.5
        return out

    def _draw_thumbs(self, canvas: Canvas, thumbs: list[tuple[str, Rect, bool]], x: float, top: float,
                     actions: list[tuple[str, str]]) -> None:
        style = canvas.style
        row_tops: dict[float, float] = {}
        for _name, r, _ok in thumbs:     # each row's height, so rows hang from *top*
            row_tops[r.y] = max(row_tops.get(r.y, 0.0), r.h)
        for name, r, loaded in thumbs:
            row_h = row_tops[r.y]
            rect = Rect(x + r.x, top - r.y - row_h + (row_h - r.h), r.w, r.h)
            widget_id = "img:" + name
            if loaded:
                canvas.fill(rect, style.text_field.inner, style.radius(style.text_field))
                canvas.image(rect.inset(1.0), name)
                hovered = canvas.state.hovered(rect)
                canvas.stroke(rect, style.cursor if hovered else style.text_field.outline,
                              style.radius(style.text_field))
            else:
                canvas.box(rect, colors=style.box)
                side = canvas.icon_size()
                canvas.icon(Rect(rect.x + style.pad * 0.5, rect.y + (rect.h - side) / 2, side, side),
                            "IMAGE_DATA", style.box.text)
                canvas.label(Rect(rect.x + side + style.pad, rect.y, rect.w, rect.h), name, style.box.text)
            canvas.hit_areas.append((rect, widget_id))
            if loaded and canvas.state.clicked == widget_id:
                actions.append(("open_image", name))

    # -- icons -------------------------------------------------------------------------
    @staticmethod
    def _first_line_icon(canvas: Canvas, x: float, top: float, line_h: float) -> Rect:
        """The square for an icon at *x*, centred on the line that starts at *top*."""
        side = canvas.icon_size()
        return Rect(x, top - line_h + (line_h - side) / 2, side, side)

    def _icon_line(self, canvas: Canvas, x: float, top: float, icon: str, text: str,
                   color: RGBA, width: float | None = None, font: str = "ui") -> float:
        """An icon, then *text* (wrapped to *width* when given); return the new top."""
        style = canvas.style
        side, gap = canvas.icon_size(), style.pad * 0.6
        canvas.icon(self._first_line_icon(canvas, x, top, style.line_height), icon, color)
        if width is None:
            top -= style.line_height
            canvas.text(x + side + gap, top + (style.line_height - style.font_size) / 2 + style.font_size * 0.2,
                        text, color, font)
            return top
        return canvas.wrapped(x + side + gap, top, width - side - gap, text, color, font=font)

    def _draw_workshop(self, canvas: Canvas, process: list[dict], turn: list[dict], idx: int, x: float,
                       top: float, width: float, active: bool, compact: bool,
                       actions: list[tuple[str, str]], spinner: str = "") -> float:
        style = canvas.style
        pad = style.pad * (0.7 if compact else 1.0)
        key = f"t{idx}"
        tools = [m for m in process if m.get("role") == "tool"]
        failed = sum(1 for m in tools if _tool_failed(m))
        # "Workshop <spinner>" while the turn runs, like the panel chat.
        parts = ["Workshop " + spinner if active else "Workshop",
                 "{:d} step{:s}".format(len(tools), "" if len(tools) == 1 else "s")]
        if failed:
            parts.append("{:d} failed".format(failed))
        is_open = key in self.expanded
        header = Rect(x, top - style.unit, width, style.unit)
        if canvas.button(header, "ws:" + key, "", align="left"):
            actions.append(("toggle", key))
        side, gap = canvas.icon_size(), style.pad * 0.5
        iy = header.y + (header.h - side) / 2
        text_color = style.button.text
        lead = header.x + style.pad
        canvas.icon(Rect(lead, iy, side, side), "TRIA_DOWN" if is_open else "TRIA_RIGHT", text_color)
        canvas.icon(Rect(lead + side + gap, iy, side, side),
                    "WARNING" if failed else "PACKAGE", text_color)
        canvas.label(Rect(lead + 2 * (side + gap), header.y, header.w, header.h), "  |  ".join(parts), text_color)
        top = header.y
        if not is_open:
            return top
        inputs = self._call_inputs(turn)
        row_x, row_w = x + pad, width - 2 * pad
        clip = canvas._clips[-1] if canvas._clips else None      # None: only measuring
        for n, msg in enumerate(process):
            role = msg.get("role")
            text = message_text(msg.get("content"))
            # A long Workshop (#218): a row measured before is only stepped over when it is
            # off screen, or when the turn is just being measured.
            row_key = (id(msg), idx, n, role, len(text), msg.get("tool_call_id"), msg.get("name"), round(row_w),
                       style.scale, tuple(k for k in ("x{:d}:{:d}".format(idx, n), "x{:d}:{:d}:code".format(idx, n),
                                                      "x{:d}:{:d}:res".format(idx, n)) if k in self.expanded))
            known = self._row_h.get(row_key)
            if known is not None and (clip is None or top - known > clip.top or top < clip.y):
                top -= known
                continue
            row_start = top
            if role == "tool":
                bad = _tool_failed(msg)
                name = str(msg.get("name", "tool"))
                args = msg.get("args") or inputs.get(str(msg.get("tool_call_id", "")), ("", {}))[1]
                row_top = top - pad * 0.5
                top = self._icon_line(canvas, row_x, row_top, "ERROR" if bad else "CHECKMARK", name,
                                      _ERROR if bad else style.text, row_w - style.line_height, font="mono")
                code = args.get("code") if isinstance(args, dict) else None
                if isinstance(code, str) and code.strip():      # what ran, in its own frame
                    top = self._code_frame(canvas, row_x, top - pad * 0.3, row_w, code, "python",
                                           "copycode:{:d}:{:d}".format(idx, n), actions,
                                           expand_key="x{:d}:{:d}:code".format(idx, n)) - pad * 0.3
                elif isinstance(args, dict) and args:
                    shown = ", ".join("{:s}={:s}".format(k, json.dumps(v)[:60]) for k, v in list(args.items())[:4])
                    top = canvas.wrapped(row_x + side + gap, top, row_w - side - gap, shown,
                                         _soft(style.text, 0.35), font="mono")
                result = text.strip()
                if result and result != "(running)":
                    top = self._code_frame(canvas, row_x + style.pad, top - pad * 0.3, row_w - style.pad, result,
                                           "error" if bad else "result", "copyres:{:d}:{:d}".format(idx, n),
                                           actions, _ERROR if bad else None,
                                           expand_key="x{:d}:{:d}:res".format(idx, n),
                                           max_lines=_RESULT_LINES, line_chars=_RESULT_LINE_CHARS) - pad * 0.3
            elif role == "reasoning":
                label = str(msg.get("label") or "Thinking")
                body = " ".join(text.split())
                row_top = top - pad * 0.5
                key = "x{:d}:{:d}".format(idx, n)
                is_open = key in self.expanded
                cut = len(body) > _ENTRY_CHARS and not is_open
                shown = body[:_ENTRY_CHARS].rstrip() + "..." if cut else body
                top = self._icon_line(canvas, row_x, row_top, self.agent_icon,
                                      "{:s}: {:s}".format(label, shown), _soft(style.text),
                                      row_w - style.line_height - pad * 0.5)
                self._copy_button(canvas, row_x + row_w, row_top, "copythink:{:d}:{:d}".format(idx, n), body,
                                  actions)
                if len(body) > _ENTRY_CHARS:
                    top = self._show_all(canvas, row_x + canvas.icon_size() + style.pad * 0.5, top, key, is_open,
                                         len(body) - _ENTRY_CHARS, "character", actions)
            elif is_system_note_msg(msg):
                body = text[len("[System:"):-1].strip() if text.startswith("[System:") and text.endswith("]") else text
                title, icon = NOTE_TITLES.get(str(msg.get("system_note") or ""), ("Note", "INFO"))
                top = self._icon_line(canvas, row_x, top - pad * 0.5, icon,
                                      "{:s}: {:s}".format(title, " ".join(body.split())[:300]),
                                      _soft(style.text, 0.5), row_w)
            if len(self._row_h) > 5000:
                self._row_h.clear()
            self._row_h[row_key] = row_start - top
        return top

    # -- markdown --------------------------------------------------------------------
    def _draw_markdown(self, canvas: Canvas, blocks: list[Block], idx: int, x: float, top: float,
                       width: float, actions: list[tuple[str, str]]) -> float:
        style = canvas.style
        pad = style.pad
        code_n = 0
        for block in blocks:
            if block.kind == "code" or block.kind == "table":
                lines = block.lines or [""]
                text = "\n".join(lines)
                inner_w = width - 2 * pad
                # Code blocks get a header strip (language + Copy) so the
                # button never covers the code, even in the narrow sidebar.
                head = style.unit * 1.1 if block.kind == "code" else 0.0
                h = canvas.text_height(text, inner_w, font="mono") + 2 * pad + head
                box = Rect(x, top - h - pad * 0.5, width, h)
                canvas.box(box, colors=style.text_field)
                if block.kind == "code":
                    strip = Rect(box.x, box.top - head, box.w, head)
                    soft = _soft(style.text_field.text, 0.4)
                    side = canvas.icon_size()
                    canvas.icon(Rect(strip.x + pad, strip.y + (head - side) / 2, side, side), "SCRIPT", soft)
                    canvas.label(Rect(strip.x + pad + side + pad * 0.5, strip.y, strip.w, head),
                                 block.lang or "code", soft)
                    bw = style.unit * 0.9    # a square icon button
                    copy = Rect(box.right - bw - pad * 0.75, strip.y + (head - bw) / 2, bw, bw)
                    if canvas.button(copy, "copy:{:d}:{:d}".format(idx, code_n), "", icon="COPYDOWN"):
                        actions.append(("copy", text))
                    code_n += 1
                canvas.wrapped(box.x + pad, box.top - head - pad, inner_w, text, style.text_field.text,
                               font="mono")
                top = box.y - pad * 0.5
            elif block.kind == "hr":
                top -= pad
                canvas.fill(Rect(x, top, width, max(1.0, style.scale)), style.outline)
                top -= pad
            elif block.kind == "heading":
                size = style.font_size * _HEADING_SCALE.get(block.level, 1.0)
                top -= pad * (1.2 if block.level <= 2 else 0.8)
                icon = _HEADING_ICON.get(block.level, "KEYTYPE_BREAKDOWN_VEC")
                side = size * 0.8
                # Keyframe colours with their dark border, like the Dope Sheet.
                mark = Rect(x, top - size * 1.45 + (size * 1.45 - side) / 2, side, side)
                edge = max(1.0, style.scale * 1.5)
                canvas.icon(Rect(mark.x - edge, mark.y - edge, side + 2 * edge, side + 2 * edge), icon,
                            style.keyframe_border)
                canvas.icon(mark, icon, style.keyframes[min(block.level, 4) - 1])
                top = self._flow(canvas, block.spans, x + side + pad * 0.6, top, width - side - pad * 0.6,
                                 actions, size=size, bold=True, color=style.title) - pad * 0.4
            elif block.kind == "item":
                indent = style.unit * (0.6 + block.level)
                marker = ("•" if block.level == 0 else "◦") if block.marker == "-" else block.marker
                canvas.text(x + indent - style.unit * 0.5, top - style.line_height + style.line_height * 0.3,
                            marker, style.text)
                top = self._flow(canvas, block.spans, x + indent, top, width - indent, actions)
            elif block.kind == "quote":
                start = top
                top = self._flow(canvas, block.spans, x + style.unit * 0.6, top, width - style.unit * 0.6,
                                 actions, color=_soft(style.text, 0.25))
                canvas.fill(Rect(x, top, max(2.0, 2 * style.scale), start - top), style.outline)
            else:
                top = self._flow(canvas, block.spans, x, top, width, actions) - pad * 0.3
        return top

    def _flow(self, canvas: Canvas, spans: list[Span], x: float, top: float, width: float,
              actions: list[tuple[str, str]], size: float | None = None, bold: bool = False,
              color: RGBA | None = None) -> float:
        """Lay out styled *spans* as wrapped lines; return the new top."""
        style = canvas.style
        size = size or style.font_size
        line_h = size * 1.45
        base = color or style.text
        pieces: list[tuple[str, Span]] = []
        for span in spans:
            for token in re.split(r"(\s+)", span.text):
                if token:
                    pieces.append((token, span))
        lines: list[list[tuple[str, Span, float]]] = [[]]
        used = 0.0
        for token, span in pieces:
            font = "mono" if span.style == "code" else "ui"
            w = canvas.measure(token, font, size)
            if token.isspace():
                if lines[-1]:
                    lines[-1].append((" ", span, canvas.measure(" ", font, size)))
                    used += lines[-1][-1][2]
                continue
            if lines[-1] and used + w > width:
                while lines[-1] and lines[-1][-1][0] == " ":
                    lines[-1].pop()
                lines.append([])
                used = 0.0
            lines[-1].append((token, span, w))
            used += w
        for line in lines:
            top -= line_h
            baseline = top + (line_h - size) / 2 + size * 0.2
            cx = x
            for token, span, w in line:
                if span.style == "code":
                    canvas.fill(Rect(cx - 2, top + line_h * 0.1, w + 4, line_h * 0.8), style.text_field.inner,
                                line_h * 0.3)
                    canvas.text(cx, baseline, token, style.text_field.text, font="mono", size=size)
                elif span.style == "link":
                    canvas.text(cx, baseline, token, style.link, size=size, bold=bold)
                    area = Rect(cx, top, w, line_h)
                    canvas.hit_areas.append((area, "link:" + span.url))
                    if canvas.state.clicked == "link:" + span.url and ("link", span.url) not in actions:
                        actions.append(("link", span.url))
                else:
                    tint = _soft(base, 0.3) if span.style == "italic" else base
                    canvas.text(cx, baseline, token, tint, size=size, bold=bold or span.style == "bold")
                cx += w
        return top

    # -- scrollbar: like Blender's region scrollbars (View2D) ---------------------------
    @staticmethod
    def _bar_widths(style: Style) -> tuple[float, float]:
        """(thin, full) widths: V2D_SCROLL_MIN_WIDTH and V2D_SCROLL_WIDTH -- in UI scale only,
        like Blender's region scrollbars: the chat's or the panel's zoom doesn't fatten it (#279)."""
        px = style.ui_scale
        return 5.0 * px + 2.0 * px, 0.45 * 20.0 * px + 2.0 * px

    def _bar_geometry(self, style: Style, rect: Rect) -> tuple[Rect, Rect, float] | None:
        """(track, handle, scroll per handle pixel) at full width, or None when all fits."""
        max_scroll = self.content_height - rect.h
        if max_scroll <= 0:
            return None
        _thin, full = self._bar_widths(style)
        margin = 2.0 * style.scale
        track = Rect(rect.right - full - margin, rect.y + margin, full, rect.h - 2 * margin)
        handle_h = min(track.h, max(style.unit, track.h * rect.h / self.content_height))
        travel = track.h - handle_h
        y = track.y + (self.scroll / max_scroll) * travel
        return track, Rect(track.x, y, full, handle_h), (max_scroll / travel if travel > 0 else 0.0)

    def _scrollbar_input(self, canvas: Canvas, rect: Rect) -> None:
        """Drag the handle; click the track to page (uses last frame's geometry)."""
        state = canvas.state
        geometry = self._bar_geometry(canvas.style, rect)
        if geometry is None or state.mouse is None:
            self._drag_y = None
            return
        _track, handle, ratio = geometry
        if state.pressed == "scroll_handle":
            if self._drag_y is not None:
                self.scroll_by((state.mouse[1] - self._drag_y) * ratio, rect.h)
            self._drag_y = state.mouse[1]
            return
        self._drag_y = None
        if state.clicked == "scroll_track":
            page = rect.h * 0.9
            self.scroll_by(page if state.mouse[1] > handle.top else -page, rect.h)

    def _draw_scrollbar(self, canvas: Canvas, rect: Rect) -> None:
        geometry = self._bar_geometry(canvas.style, rect)
        if geometry is None:
            return
        style = canvas.style
        track, handle, _ratio = geometry
        colors = style.scroll
        state = canvas.state
        dragging = state.pressed == "scroll_handle"
        over = state.mouse is not None and track.contains(*state.mouse)
        # Fade in as the mouse comes near (AZONEFADEOUT .. AZONEFADEIN), like Blender; while the
        # chat is longer than its room it always shows, faint (#279).
        alpha = 0.0
        if dragging:
            alpha = 1.0
        elif state.mouse is not None:
            mx, my = state.mouse
            dx = max(track.x - mx, 0.0, mx - track.right)
            dy = max(track.y - my, 0.0, my - track.top)
            dist = (dx * dx + dy * dy) ** 0.5
            unit = 20.0 * style.ui_scale
            fade_in, fade_out = 5.0 * unit, 6.5 * unit
            alpha = 1.0 if dist <= fade_in else max(0.0, 1.0 - (dist - fade_in) / (fade_out - fade_in))
        alpha = max(alpha, _SCROLL_IDLE_ALPHA)
        canvas.hit_areas.append((track, "scroll_track"))
        canvas.hit_areas.append((handle, "scroll_handle"))
        if alpha <= 0.0:
            return
        thin, full = self._bar_widths(style)
        width = full if (over or dragging) else thin
        x = track.right - width

        def faded(color: RGBA) -> RGBA:
            return (color[0], color[1], color[2], color[3] * alpha)

        radius = colors.roundness * width / 2
        canvas.fill(Rect(x, track.y, width, track.h), faded(colors.inner), radius)
        canvas.stroke(Rect(x, track.y, width, track.h), faded(colors.outline), radius)
        item = colors.item
        if dragging:
            item = _lighten(item, 0.2)
        elif over and handle.contains(*state.mouse):
            item = _lighten(item, 0.1)
        canvas.fill(Rect(x, handle.y, width, handle.h), faded(item), radius)
        canvas.stroke(Rect(x, handle.y, width, handle.h), faded(colors.outline), radius)
