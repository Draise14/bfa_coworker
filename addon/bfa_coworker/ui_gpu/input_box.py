# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The input box of the GPU chat (Tier 4, task "GPU 3").

* :class:`TextModel` -- multi-line text with cursor, selection, word jumps,
  undo / redo.  Pure Python.
* :class:`InputBox` -- the box itself: key handling (**Enter sends,
  Shift+Enter adds a line**), ``@`` suggestions, and drawing onto a
  :class:`layout.Canvas` pinned at the bottom of the chat (grows up to a few
  lines, caret in the theme's cursor colour, selection highlight,
  placeholder, Send / Stop, attachment chip, "edit in a native box" for IME).

Keys come in as :class:`Key` values (``type``/``value``/``unicode`` plus
modifiers, like a Blender event), so everything here is unit-tested without
Blender.  :meth:`InputBox.handle_key` returns whether the key was used and
fills :attr:`InputBox.actions` with requests for the host: ``("send", text)``,
``("stop", "")``, ``("copy", text)``, ``("paste", "")`` (the host answers with
:meth:`InputBox.paste`), ``("native_edit", text)`` and ``("blur", "")``.
Free of ``bpy``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .layout import Canvas, Rect

__all__ = (
    "InputBox",
    "Key",
    "Suggestion",
    "TextModel",
)

_MAX_UNDO = 200
_MAX_VISIBLE_LINES = 6
_MAX_SUGGESTIONS = 8


# -- Text model ----------------------------------------------------------------------

@dataclass
class TextModel:
    """Text, cursor and selection anchor (character offsets), with undo."""

    text: str = ""
    cursor: int = 0
    anchor: int | None = None          # selection = [min(anchor, cursor), max(...)]
    _undo: list[tuple[str, int]] = field(default_factory=list)
    _redo: list[tuple[str, int]] = field(default_factory=list)

    # selection
    @property
    def selection(self) -> tuple[int, int] | None:
        if self.anchor is None or self.anchor == self.cursor:
            return None
        return (min(self.anchor, self.cursor), max(self.anchor, self.cursor))

    def selected_text(self) -> str:
        sel = self.selection
        return self.text[sel[0]:sel[1]] if sel else ""

    def select_all(self) -> None:
        self.anchor, self.cursor = 0, len(self.text)

    # editing
    def _snapshot(self) -> None:
        self._undo.append((self.text, self.cursor))
        del self._undo[:-_MAX_UNDO]
        self._redo.clear()

    def insert(self, chars: str) -> None:
        self._snapshot()
        sel = self.selection
        start, end = sel if sel else (self.cursor, self.cursor)
        self.text = self.text[:start] + chars + self.text[end:]
        self.cursor, self.anchor = start + len(chars), None

    def delete(self, forward: bool = False, word: bool = False) -> None:
        sel = self.selection
        if sel:
            self._snapshot()
            self.text = self.text[:sel[0]] + self.text[sel[1]:]
            self.cursor, self.anchor = sel[0], None
            return
        if forward:
            end = self._word_end(self.cursor) if word else min(len(self.text), self.cursor + 1)
            if end == self.cursor:
                return
            self._snapshot()
            self.text = self.text[:self.cursor] + self.text[end:]
        else:
            start = self._word_start(self.cursor) if word else max(0, self.cursor - 1)
            if start == self.cursor:
                return
            self._snapshot()
            self.text = self.text[:start] + self.text[self.cursor:]
            self.cursor = start
        self.anchor = None

    def set_text(self, text: str) -> None:
        if text != self.text:
            self._snapshot()
        self.text, self.cursor, self.anchor = text, len(text), None

    def clear(self) -> None:
        self.set_text("")

    def undo(self) -> None:
        if self._undo:
            self._redo.append((self.text, self.cursor))
            self.text, self.cursor = self._undo.pop()
            self.anchor = None

    def redo(self) -> None:
        if self._redo:
            self._undo.append((self.text, self.cursor))
            self.text, self.cursor = self._redo.pop()
            self.anchor = None

    # movement
    def _word_start(self, pos: int) -> int:
        while pos > 0 and self.text[pos - 1].isspace():
            pos -= 1
        while pos > 0 and not self.text[pos - 1].isspace():
            pos -= 1
        return pos

    def _word_end(self, pos: int) -> int:
        n = len(self.text)
        while pos < n and self.text[pos].isspace():
            pos += 1
        while pos < n and not self.text[pos].isspace():
            pos += 1
        return pos

    def _line_bounds(self, pos: int) -> tuple[int, int]:
        start = self.text.rfind("\n", 0, pos) + 1
        end = self.text.find("\n", pos)
        return start, (len(self.text) if end < 0 else end)

    def move(self, where: str, select: bool = False) -> None:
        """Move the cursor: left, right, word_left, word_right, home, end, up, down, start, end_all."""
        if select and self.anchor is None:
            self.anchor = self.cursor
        sel = self.selection
        if not select and sel and where in ("left", "right"):
            self.cursor, self.anchor = (sel[0] if where == "left" else sel[1]), None
            return
        pos = self.cursor
        if where == "left":
            pos = max(0, pos - 1)
        elif where == "right":
            pos = min(len(self.text), pos + 1)
        elif where == "word_left":
            pos = self._word_start(pos)
        elif where == "word_right":
            pos = self._word_end(pos)
        elif where == "home":
            pos = self._line_bounds(pos)[0]
        elif where == "end":
            pos = self._line_bounds(pos)[1]
        elif where == "start":
            pos = 0
        elif where == "end_all":
            pos = len(self.text)
        elif where in ("up", "down"):
            start, end = self._line_bounds(pos)
            column = pos - start
            if where == "up" and start > 0:
                p_start, p_end = self._line_bounds(start - 1)
                pos = min(p_start + column, p_end)
            elif where == "down" and end < len(self.text):
                n_start, n_end = self._line_bounds(end + 1)
                pos = min(n_start + column, n_end)
        self.cursor = pos
        if not select:
            self.anchor = None


# -- Keys and suggestions ------------------------------------------------------------

@dataclass(frozen=True)
class Key:
    """A key press, shaped like a Blender event."""

    type: str
    value: str = "PRESS"
    unicode: str = ""
    shift: bool = False
    ctrl: bool = False
    alt: bool = False
    oskey: bool = False


@dataclass(frozen=True)
class Suggestion:
    """One ``@`` suggestion: what the list shows and what gets inserted."""

    label: str
    insert: str
    kind: str = ""          # category, e.g. "object", "material" (the list's filter)
    icon: str = ""          # Blender icon name, e.g. "OUTLINER_OB_CAMERA"
    detail: str = ""        # shown dimmed on the right, e.g. "Camera"


# provider(query) -> suggestions; the host supplies one that searches the scene.
Provider = Callable[[str], list[Suggestion]]

_MOVES = {"LEFT_ARROW": "left", "RIGHT_ARROW": "right", "HOME": "home", "END": "end",
          "UP_ARROW": "up", "DOWN_ARROW": "down"}


@dataclass
class InputBox:
    """The chat's input box: editing, ``@`` suggestions, drawing."""

    model: TextModel = field(default_factory=TextModel)
    provider: Provider | None = None
    placeholder: str = "Ask Coworker...  (Enter to send, Shift+Enter for a new line)"
    focused: bool = False
    busy: bool = False                     # a turn is running: show Stop
    attachment: str = ""                   # attached image name, if any
    actions: list[tuple[str, str]] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    suggestion_index: int = 0
    # The @ list's filter row, like the native mention popup: (id, label, icon).
    categories: tuple[tuple[str, str, str], ...] = ()
    category: str = ""                     # "" = all
    match_count: int = 0
    _mention_start: int | None = None      # offset of the "@" being completed
    blink_phase: bool = True               # the host toggles this for the caret blink
    rect: Rect | None = None               # where the box was last drawn (for clicks)
    # Last drawn lines: (top, bottom, x0, start offset, text) and the measure used.
    _drawn: list[tuple[float, float, float, int, str]] = field(default_factory=list)
    _measure: Callable[[str, str, float], float] | None = None
    _size: float = 11.0

    # -- suggestions -------------------------------------------------------------
    def _mention_query(self) -> str | None:
        """The text after an "@" the cursor is still in, or None."""
        text, pos = self.model.text, self.model.cursor
        at = text.rfind("@", 0, pos)
        if at < 0 or (at > 0 and not text[at - 1].isspace()):
            return None
        query = text[at + 1:pos]
        if any(ch.isspace() for ch in query) or len(query) > 40:
            return None
        self._mention_start = at
        return query

    def _refresh_suggestions(self) -> None:
        query = self._mention_query() if self.provider is not None else None
        if query is None:
            self.suggestions, self._mention_start = [], None
            return
        matches = [m for m in self.provider(query) if not self.category or m.kind == self.category]
        self.match_count = len(matches)
        self.suggestions = matches[:_MAX_SUGGESTIONS]
        self.suggestion_index = min(self.suggestion_index, max(0, len(self.suggestions) - 1))

    def set_category(self, category: str) -> None:
        """Filter the @ list to one category ("" = all)."""
        self.category = category
        self.suggestion_index = 0
        self._refresh_suggestions()

    def accept_suggestion(self, index: int | None = None) -> None:
        if not self.suggestions or self._mention_start is None:
            return
        pick = self.suggestions[self.suggestion_index if index is None else index]
        self.model.anchor = self._mention_start
        self.model.insert(pick.insert + " ")
        self.suggestions, self._mention_start = [], None

    # -- host calls -----------------------------------------------------------------
    def paste(self, text: str) -> None:
        """The host's answer to a ``("paste", "")`` action."""
        if text:
            self.model.insert(text.replace("\r\n", "\n"))
            self._refresh_suggestions()

    def send(self) -> None:
        text = self.model.text.strip()
        if text:
            self.actions.append(("send", text))
            self.model.clear()
            self.model._undo.clear()  # a sent message isn't undone back into the box
        self.suggestions = []

    def place_cursor(self, x: float, y: float, select: bool = False) -> None:
        """Move the cursor to the character nearest (x, y) in the last drawn lines."""
        if not self._drawn or self._measure is None:
            return
        row = next((r for r in self._drawn if r[1] <= y <= r[0]), None)
        if row is None:
            row = self._drawn[0] if y > self._drawn[0][0] else self._drawn[-1]
        _top, _bottom, x0, start, piece = row
        best, best_dx = 0, abs(x - x0)
        for i in range(1, len(piece) + 1):
            dx = abs(x - (x0 + self._measure(piece[:i], "ui", self._size)))
            if dx < best_dx:
                best, best_dx = i, dx
        if select and self.model.anchor is None:
            self.model.anchor = self.model.cursor
        elif not select:
            self.model.anchor = None
        self.model.cursor = start + best
        self._refresh_suggestions()

    # -- keys -------------------------------------------------------------------------
    def handle_key(self, key: Key) -> bool:
        """Apply *key*; return True when the box used it (the host then blocks it)."""
        if not self.focused:
            return False
        if key.value != "PRESS":
            return key.type not in ("LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE")
        m, ctrl = self.model, key.ctrl or key.oskey
        if self.suggestions:
            if key.type == "UP_ARROW":
                self.suggestion_index = (self.suggestion_index - 1) % len(self.suggestions)
                return True
            if key.type == "DOWN_ARROW":
                self.suggestion_index = (self.suggestion_index + 1) % len(self.suggestions)
                return True
            if key.type in ("TAB", "RET", "NUMPAD_ENTER"):
                self.accept_suggestion()
                return True
            if key.type == "ESC":
                self.suggestions, self._mention_start = [], None
                return True
        if key.type in ("RET", "NUMPAD_ENTER"):
            if key.shift:
                m.insert("\n")
            else:
                self.send()
            return True
        if key.type == "ESC":
            self.focused = False
            self.actions.append(("blur", ""))
            return True
        if ctrl and key.type in ("A", "C", "X", "V", "Z", "Y"):
            if key.type == "A":
                m.select_all()
            elif key.type == "C" and m.selected_text():
                self.actions.append(("copy", m.selected_text()))
            elif key.type == "X" and m.selected_text():
                self.actions.append(("copy", m.selected_text()))
                m.delete()
            elif key.type == "V":
                self.actions.append(("paste", ""))
            elif key.type == "Z" and key.shift:
                m.redo()
            elif key.type == "Z":
                m.undo()
            elif key.type == "Y":
                m.redo()
            self._refresh_suggestions()
            return True
        if key.type in _MOVES:
            where = _MOVES[key.type]
            if ctrl and where in ("left", "right"):
                where = "word_" + where
            elif ctrl and where in ("home", "end"):
                where = "start" if where == "home" else "end_all"
            m.move(where, select=key.shift)
            self._refresh_suggestions()
            return True
        if key.type == "BACK_SPACE":
            m.delete(word=ctrl)
            self._refresh_suggestions()
            return True
        if key.type == "DEL":
            m.delete(forward=True, word=ctrl)
            self._refresh_suggestions()
            return True
        if key.unicode and not ctrl and not key.alt and key.unicode.isprintable():
            m.insert(key.unicode)
            self._refresh_suggestions()
            return True
        # Any other key while focused: swallow it, so editor shortcuts never fire.
        return key.type not in ("LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE")

    # -- drawing ------------------------------------------------------------------------
    def height(self, canvas: Canvas, width: float) -> float:
        """Height the box needs at *width* (grows with its lines, up to a limit)."""
        style = canvas.style
        lines = self._lines(canvas, width)
        visible = min(len(lines), _MAX_VISIBLE_LINES)
        chip = style.unit * 1.9 if self.attachment else 0.0
        return max(visible * style.line_height + 2 * style.pad, style.unit + style.pad) + chip

    def _text_width(self, canvas: Canvas, width: float) -> float:
        style = canvas.style
        return width - 2 * style.pad - style.unit * (8.0 if self.busy else 5.2)   # room for the buttons

    def _lines(self, canvas: Canvas, width: float) -> list[tuple[int, str]]:
        """Visual lines as (start offset, text), wrapping by words like the view."""
        from .layout import wrap_text
        out: list[tuple[int, str]] = []
        offset = 0
        text_w = self._text_width(canvas, width)
        for para in self.model.text.split("\n"):
            pieces = wrap_text(para, text_w, canvas.measure, "ui", canvas.style.font_size)
            consumed = 0
            for piece in pieces:
                start = para.find(piece, consumed) if piece else consumed
                start = consumed if start < 0 else start
                out.append((offset + start, piece))
                consumed = start + len(piece)
            offset += len(para) + 1
        return out or [(0, "")]

    def draw(self, canvas: Canvas, rect: Rect) -> list[tuple[str, str]]:
        """Draw the box into *rect* (its bottom part is used).

        Button clicks (from ``canvas.state``) land in :attr:`actions` like keys
        do; the return value is just the actions added by this draw.
        """
        style = canvas.style
        pad, u = style.pad, style.unit
        before = len(self.actions)
        h = min(rect.h, self.height(canvas, rect.w))
        box = Rect(rect.x, rect.y, rect.w, h)
        self.rect, self._measure, self._size, self._drawn = box, canvas.measure, style.font_size, []
        canvas.box(box, colors=style.text_field, selected=self.focused)
        canvas.hit_areas.append((box, "input"))
        # Buttons on the right, like the panel chat: Send, or Stop (+ Queue when
        # there is text) while a turn runs; and "edit in a native box".
        bw = u * 2.8
        has_text = bool(self.model.text.strip())
        primary = Rect(box.right - bw - pad * 0.5, box.y + pad * 0.5, bw, u)
        if self.busy:
            if canvas.button(primary, "stop", "Stop", icon="PAUSE"):
                self.actions.append(("stop", ""))
        elif canvas.button(primary, "send", "Send", active=has_text, icon="PLAY"):
            self.send()
        left = primary.x
        if self.busy and has_text:
            queue = Rect(left - bw - pad * 0.25, primary.y, bw, u)
            if canvas.button(queue, "queue", "Queue", icon="ADD"):
                self.send()          # the host queues a send while a turn runs
            left = queue.x
        native = Rect(left - bw * 0.75 - pad * 0.25, primary.y, bw * 0.75, u)
        if canvas.button(native, "native_edit", "Edit"):
            self.actions.append(("native_edit", self.model.text))
        top = box.top - pad
        if self.attachment:
            # The image waiting to be sent: a small thumbnail and its name.
            thumb = u * 1.6
            chip = Rect(box.x + pad, top - thumb - u * 0.1, min(box.w * 0.6, u * 9), thumb)
            canvas.box(chip, colors=style.button, outline=False)
            canvas.image(Rect(chip.x + 2, chip.y + 2, thumb - 4, thumb - 4), self.attachment)
            side = canvas.icon_size()
            canvas.icon(Rect(chip.x + thumb + pad * 0.4, chip.y + (thumb - side) / 2, side, side),
                        "IMAGE_DATA", style.button.text)
            canvas.label(Rect(chip.x + thumb + side + pad * 0.8, chip.y, chip.w, thumb), self.attachment,
                         style.button.text)
            top -= u * 1.9
        text_color = style.text_field.text_sel if self.focused else style.text_field.text
        lines = self._lines(canvas, rect.w)
        canvas.push_clip(box.inset(pad * 0.25))
        if not self.model.text:
            hint = self.placeholder
            room = self._text_width(canvas, rect.w)
            while hint and canvas.measure(hint, "ui", style.font_size) > room:
                hint = hint[:-2].rstrip() + "\u2026" if len(hint) > 2 else ""
            canvas.label(Rect(box.x + pad, top - style.line_height, box.w, style.line_height),
                         hint, (text_color[0], text_color[1], text_color[2], 0.5))
            lines = []
        # Keep the cursor's line visible when the text is taller than the box.
        cur_line = max((i for i, (start, _) in enumerate(lines) if start <= self.model.cursor), default=0)
        first = max(0, min(cur_line - _MAX_VISIBLE_LINES + 1, len(lines) - _MAX_VISIBLE_LINES))
        sel = self.model.selection
        size = style.font_size
        for row, (start, piece) in enumerate(lines[first:first + _MAX_VISIBLE_LINES]):
            line_top = top - row * style.line_height
            base = line_top - style.line_height + (style.line_height - size) / 2 + size * 0.2
            x0 = box.x + pad
            end = start + len(piece)
            if sel and sel[0] < end + 1 and sel[1] > start:
                a, b = max(sel[0], start), min(sel[1], end)
                xa = x0 + canvas.measure(piece[:a - start], "ui", size)
                xb = x0 + canvas.measure(piece[:b - start], "ui", size) + (4 if sel[1] > end else 0)
                canvas.fill(Rect(xa, line_top - style.line_height, max(2.0, xb - xa), style.line_height),
                            (*style.button.inner_sel[:3], 0.55))
            canvas.text(x0, base, piece, text_color)
            self._drawn.append((line_top, line_top - style.line_height, x0, start, piece))
            is_cursor_line = start <= self.model.cursor <= end and (row + first) == cur_line
            if self.focused and self.blink_phase and is_cursor_line:
                cx = x0 + canvas.measure(piece[:self.model.cursor - start], "ui", size)
                canvas.fill(Rect(cx, line_top - style.line_height * 0.9,
                                 max(1.0, style.scale * 1.5), style.line_height * 0.8), style.cursor)
        canvas.pop_clip()
        if self.suggestions:
            self._draw_suggestions(canvas, Rect(box.x, box.top, min(box.w, u * 14), 0))
        return self.actions[before:]

    def _draw_suggestions(self, canvas: Canvas, anchor: Rect) -> None:
        """The @ list above the box: a filter row (All + one icon per category),
        the match count, then one row per match led by its type icon."""
        style = canvas.style
        u, pad = style.unit, style.pad
        row_h = u
        header = 2 if self.categories else 0
        rows = len(self.suggestions) or 1
        h = row_h * (rows + header) + pad
        panel = Rect(anchor.x, anchor.y + pad * 0.5, anchor.w, h)
        canvas.box(panel, colors=style.tooltip)
        side = canvas.icon_size()
        top = panel.top - pad * 0.5
        soft = (style.tooltip.text[0], style.tooltip.text[1], style.tooltip.text[2], 0.6)
        if self.categories:
            frow = Rect(panel.x + pad * 0.5, top - row_h, panel.w - pad, row_h)
            canvas.icon(Rect(frow.x, frow.y + (row_h - side) / 2, side, side), "VIEWZOOM", soft)
            bx = frow.x + side + pad * 0.5
            all_w = u * 1.8
            if canvas.button(Rect(bx, frow.y, all_w, row_h), "mcat:", "All", active=not self.category):
                self.set_category("")
            bx += all_w
            for cat_id, label, icon in self.categories:
                if bx + u * 1.15 > frow.right:
                    break
                if canvas.button(Rect(bx, frow.y, u * 1.15, row_h), "mcat:" + cat_id, "", icon=icon,
                                 active=self.category == cat_id):
                    self.set_category(cat_id)
                bx += u * 1.15
            top -= row_h
            count = Rect(panel.x + pad * 0.5, top - row_h, panel.w - pad, row_h)
            canvas.icon(Rect(count.x, count.y + (row_h - side) / 2, side, side), "SORTBYEXT", soft)
            canvas.label(Rect(count.x + side + pad * 0.5, count.y, count.w, row_h),
                         "{:d} match{:s}".format(self.match_count, "" if self.match_count == 1 else "es"), soft)
            top -= row_h
        if not self.suggestions:
            canvas.label(Rect(panel.x + pad, top - row_h, panel.w - 2 * pad, row_h), "Nothing matches", soft)
        for i, sug in enumerate(self.suggestions):
            r = Rect(panel.x + pad * 0.25, top - (i + 1) * row_h, panel.w - pad * 0.5, row_h)
            widget_id = "suggest:{:d}".format(i)
            current = i == self.suggestion_index
            if current or canvas.state.hovered(r):
                canvas.fill(r, style.button.inner_sel if current else style.tooltip.inner_sel,
                            style.radius(style.button))
            text = style.button.text_sel if current else style.tooltip.text
            x = r.x + pad * 0.5
            if sug.icon:
                canvas.icon(Rect(x, r.y + (row_h - side) / 2, side, side), sug.icon, text)
                x += side + pad * 0.5
            canvas.label(Rect(x, r.y, r.right - x, row_h), sug.label, text)
            if sug.detail:
                canvas.label(Rect(r.x, r.y, r.w - pad * 0.5, row_h), sug.detail,
                             (text[0], text[1], text[2], 0.55), align="right")
            canvas.hit_areas.append((r, widget_id))
            if canvas.state.clicked == widget_id:
                self.accept_suggestion(i)
