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

The same code draws the full editor and the compact sidebar (``compact``).
Clicks come back from :meth:`ChatView.draw` as ``(action, value)`` pairs for
the input layer to carry out: ``("copy", text)``, ``("toggle", turn_key)``,
``("link", url)``, ``("jump", "")``.  Free of ``bpy``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..chat_turns import group_turns, is_system_note_msg, split_turn
from .layout import Canvas, InteractionState, Rect
from .markdown import Block, Span, parse
from .style import RGBA, Style

__all__ = (
    "ChatView",
    "message_text",
)

_ERROR = (0.92, 0.40, 0.33, 1.0)   # failed tool rows (themes have no error colour)
_HEADING_SCALE = {1: 1.3, 2: 1.18, 3: 1.08}


def message_text(content: Any) -> str:
    """Plain text of a message's content (string or a list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "")


def _soft(color: RGBA, amount: float = 0.35) -> RGBA:
    return (color[0], color[1], color[2], color[3] * (1.0 - amount))


def _tool_failed(msg: dict) -> bool:
    content = message_text(msg.get("content"))
    return content.startswith("Error") or '"status": "error"' in content


@dataclass
class ChatView:
    """View state of one chat surface (scroll, open Workshops, height cache)."""

    scroll: float = 0.0
    expanded: set[str] = field(default_factory=set)
    content_height: float = 0.0
    _heights: dict[tuple, float] = field(default_factory=dict)

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
        width = rect.w - 2 * pad - (style.unit * 0.35)   # leave room for the scrollbar
        turns = group_turns([m for m in history if m.get("role") != "system"])
        active = len(turns) - 1 if (live is not None and getattr(live, "is_thinking", False)) else -1
        if not turns:
            canvas.wrapped(rect.x + pad, rect.top - pad, rect.w - 2 * pad,
                           "Start a conversation: type below and press Enter.", _soft(style.text))
            self.content_height = 0.0
            return actions

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
        if self.scroll > style.unit:
            size = style.unit * 1.2
            jump = Rect(rect.right - size - pad - style.unit * 0.35, rect.y + pad, size, size)
            if canvas.button(jump, "jump", "v"):
                actions.append(("jump", ""))
        return actions

    # -- measuring -----------------------------------------------------------------
    def _turn_height(self, canvas: Canvas, turn: list[dict], idx: int, width: float,
                     active: bool, live: Any, compact: bool) -> float:
        key = None
        if not active:
            key = (idx, len(turn), sum(len(message_text(m.get("content"))) for m in turn),
                   round(width), canvas.style.scale, f"t{idx}" in self.expanded, compact)
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
            text = message_text(user.get("content"))
            inner_w = width - indent - 2 * pad
            h = canvas.text_height(text, inner_w) + 2 * pad
            box = Rect(x + indent, top - h, width - indent, h)
            canvas.box(box, colors=style.button, selected=True, outline=False)
            canvas.wrapped(box.x + pad, box.top - pad, inner_w, text, style.button.text_sel)
            top = box.y - pad
        if process:
            top = self._draw_workshop(canvas, process, idx, x, top, width, active, compact, actions) - pad
        if active:
            phase = getattr(live, "turn_phase", "") or "thinking"
            dots = "." * (1 + int(getattr(live, "thinking_dots", 0)) % 3)
            streaming = getattr(live, "streaming_text", "") or ""
            if streaming:
                top = self._draw_markdown(canvas, parse(streaming), idx, x, top, width, actions)
            label = "Coworker is {:s}{:s}".format("reading your message" if phase == "reading" else phase, dots)
            top -= style.line_height
            canvas.text(x, top + style.line_height * 0.3, label, _soft(style.text))
        elif conclusion is not None:
            top = self._draw_markdown(canvas, parse(message_text(conclusion.get("content"))),
                                      idx, x, top, width, actions)
        return top

    def _draw_workshop(self, canvas: Canvas, process: list[dict], idx: int, x: float, top: float,
                       width: float, active: bool, compact: bool,
                       actions: list[tuple[str, str]]) -> float:
        style = canvas.style
        pad = style.pad * (0.7 if compact else 1.0)
        key = f"t{idx}"
        tools = [m for m in process if m.get("role") == "tool"]
        failed = sum(1 for m in tools if _tool_failed(m))
        parts = ["Workshop", "{:d} step{:s}".format(len(tools), "" if len(tools) == 1 else "s")]
        if failed:
            parts.append("{:d} failed".format(failed))
        if active:
            parts.append("working")
        is_open = key in self.expanded
        header = Rect(x, top - style.unit, width, style.unit)
        if canvas.button(header, "ws:" + key, ("v  " if is_open else ">  ") + "  |  ".join(parts), align="left"):
            actions.append(("toggle", key))
        top = header.y
        if not is_open:
            return top
        for msg in process:
            role = msg.get("role")
            text = message_text(msg.get("content"))
            if role == "tool":
                bad = _tool_failed(msg)
                first = " ".join(text.split())[:200]   # one-line preview (JSON spans lines)
                line = "{:s}  {:s}  {:s}".format("x" if bad else "ok", str(msg.get("name", "tool")), first)
                top = canvas.wrapped(x + pad, top - pad * 0.5, width - 2 * pad, line,
                                     _ERROR if bad else style.text, font="mono")
            elif role == "reasoning":
                label = str(msg.get("label") or "Thinking")
                body = " ".join(text.split())[:400]
                top = canvas.wrapped(x + pad, top - pad * 0.5, width - 2 * pad,
                                     "{:s}: {:s}".format(label, body), _soft(style.text))
            elif is_system_note_msg(msg):
                body = text[len("[System:"):-1].strip() if text.startswith("[System:") and text.endswith("]") else text
                top = canvas.wrapped(x + pad, top - pad * 0.5, width - 2 * pad,
                                     "Note: " + " ".join(body.split())[:300], _soft(style.text, 0.5))
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
                    canvas.label(strip.inset(pad, 0), block.lang or "code", _soft(style.text_field.text, 0.4))
                    bw = style.unit * 3.2
                    copy = Rect(box.right - bw - pad * 0.5, strip.y + (head - style.unit * 0.9) / 2,
                                bw, style.unit * 0.9)
                    if canvas.button(copy, "copy:{:d}:{:d}".format(idx, code_n), "Copy"):
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
                top = self._flow(canvas, block.spans, x, top - pad * 0.5, width, actions,
                                 size=size, bold=True, color=style.title)
            elif block.kind == "item":
                indent = style.unit * (0.6 + block.level)
                marker = "*" if block.marker == "-" else block.marker
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
                    canvas.fill(Rect(cx - 1, top + line_h * 0.12, w + 2, line_h * 0.76), style.text_field.inner, 2.0)
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

    def _draw_scrollbar(self, canvas: Canvas, rect: Rect) -> None:
        if self.content_height <= rect.h:
            return
        style = canvas.style
        track_w = max(2.0, style.unit * 0.15)
        frac = rect.h / self.content_height
        thumb_h = max(style.unit, rect.h * frac)
        travel = rect.h - thumb_h
        max_scroll = self.content_height - rect.h
        y = rect.y + (self.scroll / max_scroll) * travel if max_scroll > 0 else rect.y
        canvas.fill(Rect(rect.right - track_w - 2, y, track_w, thumb_h), _soft(style.text, 0.6), track_w / 2)
