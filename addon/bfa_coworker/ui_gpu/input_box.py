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
_MIN_VISIBLE_LINES = 2
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
    placeholder: str = "Ask Coworker..."
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
    big_buttons: bool = False              # double-height buttons (the editor's full chat)
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
        # AltGr (Ctrl+Alt on Windows) types characters like @ on Spanish / German keyboards.
        if key.ctrl and key.alt and key.unicode and key.unicode.isprintable():
            m.insert(key.unicode)
            self._refresh_suggestions()
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
        """Height the box needs at *width*: text lines (2 to 6), then the button row."""
        style = canvas.style
        visible = max(_MIN_VISIBLE_LINES, min(len(self._lines(canvas, width)), _MAX_VISIBLE_LINES))
        chip = style.unit * 1.9 if self.attachment else 0.0
        margin, _gap, button_h = self._button_metrics(canvas)
        return style.pad + chip + visible * style.line_height + style.pad * 0.5 + button_h + margin

    # -- buttons -----------------------------------------------------------------------
    def _button_metrics(self, canvas: Canvas) -> tuple[float, float, float]:
        """(edge margin, gap between buttons, button height)."""
        style = canvas.style
        return style.pad * 0.75, style.pad * 0.5, style.unit * (1.6 if self.big_buttons else 1.0)

    def _buttons(self, canvas: Canvas) -> list[tuple[str, str, str, bool, float]]:
        """(id, label, icon, active, width), right to left, like the panel chat:
        Send, or Stop (+ Queue when there is text) while a turn runs."""
        style = canvas.style
        has_text = bool(self.model.text.strip())
        if self.busy:
            specs = [("stop", "Stop", "PAUSE", False)]
            if has_text:
                specs.append(("queue", "Queue", "ADD", False))
        else:
            specs = [("send", "Send", "PLAY", has_text)]
        out = []
        for widget_id, label, icon, active in specs:
            content = canvas.measure(label, "ui", style.font_size)
            if icon:
                content += canvas.icon_size() + style.pad * 0.5
            out.append((widget_id, label, icon, active, max(style.unit * 2.4, content + style.pad * 2.5)))
        return out

    def _text_width(self, canvas: Canvas, width: float) -> float:
        return width - 2 * canvas.style.pad

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

    @staticmethod
    def _fit(canvas: Canvas, text: str, width: float) -> str:
        """*text* cut with an ellipsis to fit *width*."""
        size = canvas.style.font_size
        if canvas.measure(text, "ui", size) <= width:
            return text
        while text and canvas.measure(text + "\u2026", "ui", size) > width:
            text = text[:-1]
        return text + "\u2026" if text else ""

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
        hovered = canvas.state.hovered(box)
        canvas.box(box, colors=style.text_field, selected=self.focused)
        if self.focused or hovered:          # ready to type: the theme's text-cursor colour
            ring = style.cursor if self.focused else (style.cursor[0], style.cursor[1], style.cursor[2], 0.45)
            canvas.stroke(box, ring, style.radius(style.text_field))
        canvas.hit_areas.append((box, "input"))
        # The button row along the bottom: a shortcut hint on the left, buttons on the right.
        margin, gap, button_h = self._button_metrics(canvas)
        right = box.right - margin
        for widget_id, label, icon, active, button_w in self._buttons(canvas):
            button = Rect(right - button_w, box.y + margin, button_w, button_h)
            if canvas.button(button, widget_id, label, active=active, icon=icon):
                if widget_id == "stop":
                    self.actions.append(("stop", ""))
                else:
                    self.send()      # Send, or Queue (the host queues a send while a turn runs)
            right = button.x - gap
        hint_color = (style.text_field.text[0], style.text_field.text[1], style.text_field.text[2], 0.45)
        hint = self._fit(canvas, "Enter to send  \u00b7  Shift+Enter: new line  \u00b7  @ to mention",
                         right - box.x - 2 * pad)
        if hint:
            canvas.label(Rect(box.x + pad, box.y + margin, right - box.x - pad, button_h), hint, hint_color)
        top = box.top - pad
        if self.attachment:
            # The image waiting to be sent: a small thumbnail and its name (cut to fit).
            thumb = u * 1.6
            side = canvas.icon_size()
            name_room = min(box.w * 0.7, u * 14) - thumb - side - pad * 1.6
            name = self._fit(canvas, self.attachment, name_room)
            chip_w = thumb + side + pad * 1.6 + canvas.measure(name, "ui", style.font_size) + pad
            chip = Rect(box.x + pad, top - thumb - u * 0.1, chip_w, thumb)
            canvas.box(chip, colors=style.button, outline=False)
            canvas.image(Rect(chip.x + 2, chip.y + 2, thumb - 4, thumb - 4), self.attachment)
            canvas.icon(Rect(chip.x + thumb + pad * 0.4, chip.y + (thumb - side) / 2, side, side),
                        "IMAGE_DATA", style.button.text)
            canvas.label(Rect(chip.x + thumb + side + pad * 0.8, chip.y, chip.w, thumb), name, style.button.text)
            top -= u * 1.9
        text_color = style.text_field.text_sel if self.focused else style.text_field.text
        lines = self._lines(canvas, rect.w)
        text_area = Rect(box.x + pad * 0.25, box.y + margin + button_h, box.w - pad * 0.5,
                         max(0.0, top - box.y - margin - button_h + pad * 0.75))
        canvas.push_clip(text_area)
        size = style.font_size
        if not self.model.text:
            hint_text = self._fit(canvas, self.placeholder, self._text_width(canvas, rect.w))
            canvas.label(Rect(box.x + pad, top - style.line_height, box.w, style.line_height),
                         hint_text, (text_color[0], text_color[1], text_color[2], 0.5))
            if self.focused and self.blink_phase:     # the caret, ready at the start
                canvas.fill(Rect(box.x + pad, top - style.line_height * 0.9,
                                 max(1.0, style.scale * 1.5), style.line_height * 0.8), style.cursor)
            self._drawn.append((top, top - style.line_height, box.x + pad, 0, ""))
            lines = []
        # Keep the cursor's line visible when the text is taller than the box.
        cur_line = max((i for i, (start, _) in enumerate(lines) if start <= self.model.cursor), default=0)
        first = max(0, min(cur_line - _MAX_VISIBLE_LINES + 1, len(lines) - _MAX_VISIBLE_LINES))
        sel = self.model.selection
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
