# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
One chat surface: the GPU chat as it sits in a region (Tier 4, task "GPU 4").

A :class:`ChatSurface` puts the pieces together for one place on screen --
the centre of a Coworker editor (full) or a sidebar's Coworker tab
(compact) -- and keeps that place's own scroll, zoom, open Workshops, typed
text and mouse state:

* a compact **top bar** (sidebar only; the editor has its header): mode,
  status, Start / Stop, open the Coworker editor, settings, chat mode;
* the **chat view** (:mod:`.chat_view`), or a **notice card** in its place
  (External Harness: the chat runs in the external client);
* the **input box** (:mod:`.input_box`) pinned at the bottom.

Mouse input arrives through :meth:`on_move` / :meth:`on_press` /
:meth:`on_release` / :meth:`on_wheel` in region pixels (the host,
``input_capture``, calls them); clicks land on the next :meth:`draw`, which
returns the actions for the host to carry out, e.g. ``("send", text)``,
``("stop", "")``, ``("copy", text)``, ``("link", url)``, ``("start", "")``,
``("stop_agent", "")``, ``("open_editor", "")``, ``("prefs", "")``,
``("chat_mode", "ASK")`` or a notice button's ``("notice", id)``.  Free of
``bpy``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .chat_view import ChatView, _soft
from .input_box import InputBox
from .layout import Canvas, InteractionState, Rect
from .style import Style

__all__ = (
    "ChatSurface",
    "Notice",
    "TopBar",
)

_ZOOM_STEP = 0.1
_ZOOM_MIN, _ZOOM_MAX = 0.6, 2.5
_WHEEL_LINES = 3


@dataclass
class TopBar:
    """What the compact top bar shows (the host fills it from the add-on state)."""

    mode_label: str = "Local LLM"
    mode_icon: str = "CONSOLE"
    status: str = ""
    status_icon: str = "CHECKMARK"
    running: bool = False
    start_label: str = "Start"        # "Start Bridge" in External Harness
    error: bool = False
    chat_modes: tuple[tuple[str, str], ...] = ()   # (id, label); empty hides the row
    chat_mode: str = ""


@dataclass
class Notice:
    """A card drawn in place of the chat (e.g. External Harness)."""

    title: str
    icon: str
    lines: tuple[str, ...]
    buttons: tuple[tuple[str, str, str], ...] = ()   # (id, label, icon)


@dataclass
class ChatSurface:
    """The chat in one region: layout, input state and actions."""

    compact: bool = False
    view: ChatView = field(default_factory=ChatView)
    box: InputBox = field(default_factory=InputBox)
    zoom: float = 1.0
    mouse: tuple[float, float] | None = None
    pressed: str | None = None
    _clicked: str | None = None
    _hits: list[tuple[Rect, str]] = field(default_factory=list)
    _chat_rect: Rect | None = None
    _style: Style | None = None
    # Where the host drew the surface last (region pixels); None = not on screen.
    bounds: Rect | None = None
    # Called with the wanted height (pixels) while the bottom grip is dragged.
    on_resize: Any = None
    drop_hover: bool = False     # an image is being dragged over: show the drop target
    attachment_pinned: bool = False   # the attached image stays for every message

    # -- mouse (region pixels) ------------------------------------------------------
    def hit(self, x: float, y: float) -> str | None:
        """The widget at (x, y) in the last drawn frame (top-most first)."""
        for rect, widget_id in reversed(self._hits):
            if rect.contains(x, y):
                return widget_id
        return None

    def on_move(self, x: float, y: float) -> bool:
        """Track the mouse; True when the surface should redraw (hover changed)."""
        before = self.hit(*self.mouse) if self.mouse is not None else None
        self.mouse = (x, y)
        if self.pressed == "resize" and self.on_resize is not None and self.bounds is not None:
            self.on_resize(max(0.0, self.bounds.top - y))
            return True
        return self.pressed is not None or before != self.hit(x, y) or self._near_scrollbar(x)

    def on_leave(self) -> bool:
        changed = self.mouse is not None
        self.mouse = None
        return changed

    def on_press(self, x: float, y: float, shift: bool = False) -> bool:
        """A left press; True when the surface used it."""
        self.mouse = (x, y)
        widget = self.hit(x, y)
        if widget is None:
            if self.box.focused:
                self.box.focused, self.box.suggestions = False, []
            return False
        if widget == "input":
            self.box.focused = True
            self.box.place_cursor(x, y, select=shift)
            return True
        self.pressed = widget
        return True

    def on_release(self, x: float, y: float) -> bool:
        """A left release; a press and release on the same widget is a click."""
        if self.pressed is None:
            return False
        if self.hit(x, y) == self.pressed or self.pressed in ("scroll_handle", "resize"):
            self._clicked = self.pressed if self.pressed not in ("scroll_handle", "resize") else None
        self.pressed = None
        self.mouse = (x, y)
        return True

    def on_wheel(self, up: bool, ctrl: bool, x: float, y: float) -> bool:
        """Scroll the chat (Ctrl: zoom the whole surface); True when used."""
        if self._chat_rect is None or not self._chat_rect.contains(x, y):
            return False
        if ctrl:
            self.zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, round(self.zoom + (_ZOOM_STEP if up else -_ZOOM_STEP), 2)))
            return True
        step = (self._style.line_height if self._style else 16.0) * _WHEEL_LINES
        self.view.scroll_by(step if up else -step, self._chat_rect.h)
        return True

    def _near_scrollbar(self, x: float) -> bool:
        rect = self._chat_rect
        return rect is not None and self._style is not None and x > rect.right - 7 * self._style.unit

    # -- drawing ------------------------------------------------------------------------
    def interaction(self) -> InteractionState:
        """The state the host's :class:`layout.Canvas` should be built with."""
        return InteractionState(mouse=self.mouse, pressed=self.pressed, clicked=self._clicked,
                                focused="input" if self.box.focused else None)

    def draw(self, canvas: Canvas, rect: Rect, history: list[dict], live: Any = None,
             top: TopBar | None = None, notice: Notice | None = None) -> list[tuple[str, str]]:
        """Draw into *rect* (region pixels); return this frame's actions."""
        style = canvas.style
        self._style = style
        actions: list[tuple[str, str]] = []
        pad = style.pad * (0.7 if self.compact else 1.0)
        canvas.push_clip(rect)      # never paint outside the chat's room (other panels)
        canvas.fill(rect, style.back)
        if self.compact and self.on_resize is not None:
            rect = self._draw_grip(canvas, rect)
        top_edge = rect.top
        if top is not None:
            top_edge = self._draw_top_bar(canvas, Rect(rect.x, rect.y, rect.w, rect.h), top, actions)
        inner = Rect(rect.x + pad, rect.y + pad, rect.w - 2 * pad, rect.h)
        # A notice (External Harness) replaces the chat: nothing to type into.
        show_input = notice is None
        self.box.big_buttons = not self.compact
        box_h = self.box.height(canvas, inner.w) if show_input else 0.0
        input_rect = Rect(inner.x, inner.y, inner.w, box_h)
        chat_y = input_rect.top + pad if show_input else rect.y
        if show_input and self.box.attachment:      # the image tray, above the input
            chat_y = self._draw_tray(canvas, Rect(inner.x, chat_y, inner.w, 0), actions) + pad
        chat = Rect(rect.x, chat_y, rect.w, max(0.0, top_edge - chat_y - pad))
        self._chat_rect = chat
        if notice is not None:
            self._draw_notice(canvas, chat, notice, actions)
        else:
            actions += self.view.draw(canvas, chat, history, live=live, compact=self.compact)
        if show_input:
            before = len(self.box.actions)
            self.box.draw(canvas, input_rect)
            actions += self.box.actions[before:]
            del self.box.actions[before:]
        elif self.box.focused:
            self.box.focused = False
        if self.drop_hover:
            self._draw_drop_target(canvas, rect)
        canvas.pop_clip()
        self._hits = list(canvas.hit_areas)
        self._clicked = None
        # The view's own actions that only change the view are done here.
        out = []
        for action, value in actions:
            if action == "toggle":
                self.view.expanded.symmetric_difference_update({value})
            elif action == "jump":
                self.view.scroll = 0.0
            else:
                out.append((action, value))
        return out

    def _draw_top_bar(self, canvas: Canvas, rect: Rect, top: TopBar, actions: list[tuple[str, str]]) -> float:
        """Mode + status + buttons, then the chat-mode row; return the bar's bottom edge."""
        style = canvas.style
        u, pad = style.unit, style.pad * 0.7
        row = Rect(rect.x + pad, rect.top - pad - u, rect.w - 2 * pad, u)
        side = canvas.icon_size()
        canvas.icon(Rect(row.x, row.y + (u - side) / 2, side, side), top.mode_icon, style.text)
        # Buttons from the right: settings, open the editor, Start/Stop.
        bw = u * 1.15
        x = row.right
        for widget_id, icon, label in (("prefs", "PREFERENCES", ""), ("open_editor", "FULLSCREEN_ENTER", "")):
            x -= bw
            if canvas.button(Rect(x, row.y, bw, u), widget_id, label, icon=icon):
                actions.append((widget_id, ""))
            x -= pad * 0.5
        start_label = "Stop" if top.running else top.start_label
        start_w = canvas.icon_size() + canvas.measure(start_label, "ui", style.font_size) + 3.0 * style.pad
        if x - start_w - row.x < u * 4:      # narrow: Start / Stop as an icon
            start_label, start_w = "", bw
        x -= start_w
        running_id = "stop_agent" if top.running else "start"
        if canvas.button(Rect(x, row.y, start_w, u), running_id, start_label,
                         icon="X" if top.running else "PLAY"):
            actions.append((running_id, ""))
        # Status, clipped to the room that's left.
        sx = row.x + side + pad
        room = x - pad - sx
        canvas.icon(Rect(sx, row.y + (u - side) / 2, side, side), top.status_icon,
                    style.text if not top.error else (0.92, 0.40, 0.33, 1.0))
        text = _fit(canvas, top.status, room - side - pad * 0.5)
        if text:
            canvas.label(Rect(sx + side + pad * 0.5, row.y, room, u), text, _soft(style.text, 0.15))
        if top.error:
            canvas.hit_areas.append((Rect(sx, row.y, room, u), "copy_error"))
            if canvas.state.clicked == "copy_error":
                actions.append(("copy_error", ""))
        bottom = row.y - pad * 0.5
        if top.chat_modes:
            mrow = Rect(row.x, bottom - u, row.w, u)
            w = mrow.w / len(top.chat_modes)
            for i, (mode_id, label) in enumerate(top.chat_modes):
                if canvas.button(Rect(mrow.x + i * w, mrow.y, w, u), "mode:" + mode_id, label,
                                 active=mode_id == top.chat_mode):
                    actions.append(("chat_mode", mode_id))
            bottom = mrow.y - pad * 0.5
        return bottom

    def _draw_tray(self, canvas: Canvas, anchor: Rect, actions: list[tuple[str, str]]) -> float:
        """The image waiting to be sent, in its own box above the input: a thumbnail,
        its name, a pin (keep it for every message) and remove.  Returns its top."""
        style = canvas.style
        u, pad = style.unit, style.pad * 0.6
        narrow = anchor.w < u * 9
        thumb = u * (1.4 if narrow else 1.8 if self.compact else 2.6)
        tray = Rect(anchor.x, anchor.y, anchor.w, thumb + 2 * pad)
        canvas.box(tray, colors=style.box)
        name = self.box.attachment
        picture = Rect(tray.x + pad, tray.y + pad, thumb * (1.0 if narrow else 1.5), thumb)
        canvas.fill(picture, style.text_field.inner, style.radius(style.text_field))
        canvas.image(picture.inset(1.0), name)
        canvas.hit_areas.append((picture, "img:" + name))
        if canvas.state.clicked == "img:" + name:
            actions.append(("open_image", name))
        bw = u * 1.15
        remove = Rect(tray.right - pad - bw, tray.y + (tray.h - u) / 2, bw, u)
        pin = Rect(remove.x - pad * 0.5 - bw, remove.y, bw, u)
        if canvas.button(pin, "attach_pin", "", active=self.attachment_pinned,
                         icon="PINNED" if self.attachment_pinned else "UNPINNED"):
            actions.append(("attachment_pin", ""))
        if canvas.button(remove, "attach_remove", "", icon="X"):
            actions.append(("attachment_remove", ""))
        side = canvas.icon_size()
        tx = picture.right + pad
        room = pin.x - pad - tx - side - pad * 0.5
        label = name if canvas.measure(name, "ui", style.font_size) <= room else name
        while label and canvas.measure(label + "\u2026", "ui", style.font_size) > room:
            label = label[:-1]
        if label != name:
            label = label + "\u2026" if label else ""
        mid = tray.y + tray.h / 2
        if room < u * 1.5:                  # no room for words: the picture and buttons say it
            return tray.top
        canvas.icon(Rect(tx, mid + style.line_height * 0.05, side, side), "IMAGE_DATA", style.box.text)
        canvas.label(Rect(tx + side + pad * 0.5, mid, room, style.line_height), label, style.box.text)
        status = _fit(canvas, "Kept for every message" if self.attachment_pinned else "Sent with the next message",
                      room + side)
        canvas.label(Rect(tx, mid - style.line_height, room + side, style.line_height), status,
                     _soft(style.box.text, 0.35))
        return tray.top

    def _draw_drop_target(self, canvas: Canvas, rect: Rect) -> None:
        """Dragging an image file over the chat: a highlight that says what a drop does."""
        style = canvas.style
        tint = (style.cursor[0], style.cursor[1], style.cursor[2], 0.12)
        inner = rect.inset(style.pad * 0.5)
        canvas.fill(inner, tint, style.radius(style.box))
        canvas.stroke(inner, style.cursor, style.radius(style.box))
        label = "Drop to attach the image"
        side = canvas.icon_size()
        w = side + style.pad * 0.6 + canvas.measure(label, "ui", style.font_size) + 2 * style.pad
        chip = Rect(inner.x + (inner.w - w) / 2, inner.y + inner.h / 2 - style.unit * 0.6, w, style.unit * 1.2)
        canvas.box(chip, colors=style.tooltip)
        canvas.icon(Rect(chip.x + style.pad, chip.y + (chip.h - side) / 2, side, side), "IMAGE_DATA",
                    style.tooltip.text)
        canvas.label(Rect(chip.x + style.pad + side + style.pad * 0.6, chip.y, w, chip.h), label, style.tooltip.text)

    def _draw_grip(self, canvas: Canvas, rect: Rect) -> Rect:
        """A resize grip along the bottom edge (drag to change the panel's height)."""
        style = canvas.style
        h = max(6.0, style.pad * 0.9)
        grip = Rect(rect.x, rect.y, rect.w, h)
        hovered = canvas.state.hovered(grip) or self.pressed == "resize"
        dots = style.cursor if hovered else _soft(style.text, 0.55)
        cx, cy = grip.x + grip.w / 2, grip.y + grip.h / 2
        for i in range(-2, 3):
            canvas.fill(Rect(cx + i * 4 * style.scale - style.scale, cy - style.scale, 2 * style.scale,
                             2 * style.scale), dots, style.scale)
        canvas.hit_areas.append((grip, "resize"))
        return Rect(rect.x, rect.y + h, rect.w, rect.h - h)

    def _draw_notice(self, canvas: Canvas, rect: Rect, notice: Notice, actions: list[tuple[str, str]]) -> None:
        style = canvas.style
        u, pad = style.unit, style.pad
        x, width = rect.x + pad, rect.w - 2 * pad
        lines_h = sum(canvas.text_height(line, width - 2 * pad) for line in notice.lines)
        # Buttons side by side when they fit, else one per row (narrow sidebars).
        needs = [canvas.icon_size() + canvas.measure(label, "ui", style.font_size) + 3.0 * pad
                 for _id, label, _icon in notice.buttons]
        side_by_side = sum(needs) + pad * 0.5 * len(needs) <= width - 2 * pad
        rows = 1 if side_by_side else len(notice.buttons)
        h = u + lines_h + (rows * u * 1.25 if notice.buttons else 0.0) + 3 * pad
        card = Rect(x, max(rect.y, rect.top - pad - h), width, h)
        canvas.box(card, colors=style.box)
        side = canvas.icon_size()
        head = Rect(card.x + pad, card.top - pad - u, card.w - 2 * pad, u)
        canvas.icon(Rect(head.x, head.y + (u - side) / 2, side, side), notice.icon, style.title)
        canvas.label(Rect(head.x + side + pad * 0.6, head.y, head.w, u), notice.title, style.title, bold=True)
        top = head.y - pad * 0.5
        for line in notice.lines:
            top = canvas.wrapped(card.x + pad, top, card.w - 2 * pad, line, _soft(style.text, 0.1))
        if notice.buttons:
            bx, by = card.x + pad, card.y + pad + (rows - 1) * u * 1.25
            for (widget_id, label, icon), need in zip(notice.buttons, needs):
                bw = max(need, u * 4.5) if side_by_side else card.w - 2 * pad
                if canvas.button(Rect(bx, by, bw, u * 1.1), "notice:" + widget_id, label, icon=icon):
                    actions.append(("notice", widget_id))
                if side_by_side:
                    bx += bw + pad * 0.5
                else:
                    by -= u * 1.25


def _fit(canvas: Canvas, text: str, width: float) -> str:
    """*text* cut with "..." to fit *width*."""
    size = canvas.style.font_size
    if width <= 0:
        return ""
    if canvas.measure(text, "ui", size) <= width:
        return text
    while text and canvas.measure(text + "...", "ui", size) > width:
        text = text[:-1]
    return (text + "...") if text else ""
