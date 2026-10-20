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

import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from .chat_view import ChatView, _soft
from .input_box import InputBox
from .layout import Canvas, InteractionState, Rect
from .style import Style

# Tooltips for the chat's buttons (#237), by widget id (exact, then by prefix).
TIPS: dict[str, str] = {
    "revert_turn": "Revert: undo this whole turn in one step (only while you haven't changed the scene since)",
    "jump": "Jump to the newest message",
    "fold_workshop": "Fold or unfold the running turn's Workshop (its tool steps)",
    "attach_menu": "Image for the next message: pick one in the file, open a file, or attach a render / screen",
    "attach_render": "Attach a render of the scene to the next message",
    "attach_screen": "Attach part of the screen: click an editor or drag a rectangle",
    "attach_pin": "Keep this image for every message (pinned), or send it with the next one only",
    "attach_remove": "Remove the image",
    "send": "Send (Enter)",
    "queue": "Queue this message: it goes when the running turn ends",
    "stop": "Stop the running turn",
    "start": "Start Coworker",
    "stop_agent": "Stop Coworker",
    "prefs": "Coworker settings",
    "open_editor": "Open the Coworker editor: the full chat in its own window",
    "mode_menu": "Mode: Local, Remote, Agent or Harness, and its model",
    "copy_error": "Copy the error",
    "tool:new_thread": "New Thread: start a fresh chat (the old one stays in History)",
    "tool:mention": "@ Mention an object, material or collection",
    "tool:revert_turn": "Revert: undo the last turn in one step",
    "tool:stop": "Stop the running turn",
    "tool:open_editor": "Open the Coworker editor: the full chat in its own window",
    "tool:attach_menu": "Image for the next message: pick one in the file, or open a file",
    "tool:attach_render": "Attach a render of the scene to the next message",
    "tool:attach_screen": "Attach part of the screen: click an editor or drag a rectangle",
    "mode:AGENT": "Coworker: it works in the scene",
    "mode:ASK": "Ask: it answers and changes nothing",
    "mode:PLAN": "Plan: it makes a step plan to carry out later, with any engine",
    "notice:copy_log": "Copy what your client did in Blender",
    "notice:configure": "External Harness settings",
}
TIP_PREFIXES: tuple[tuple[str, str], ...] = (
    ("copymsg:", "Copy the reply"),
    ("copyuser:", "Copy your message"),
    ("copythink:", "Copy this thinking"),
    ("copycode:", "Copy the code it ran"),
    ("copyres:", "Copy the result"),
    ("copy:", "Copy the code"),
    ("img:", "Open this image in an Image Editor"),
    ("suggest:", "Send this as your next message"),
    ("ws:", "Fold or unfold the Workshop: the turn's tool steps"),
)
TIP_DELAY = 0.5      # seconds the mouse rests before a tooltip shows, like Blender's


def tip_for(widget_id: str | None) -> str:
    """The tooltip for a chat widget, or "" (#237)."""
    if not widget_id:
        return ""
    if widget_id in TIPS:
        return TIPS[widget_id]
    return next((tip for prefix, tip in TIP_PREFIXES if widget_id.startswith(prefix)), "")

__all__ = (
    "Approval",
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
    chat_modes: tuple[tuple[str, ...], ...] = ()   # (id, label[, icon]); empty hides the row
    chat_mode: str = ""


@dataclass
class Notice:
    """A card drawn in place of the chat (e.g. External Harness)."""

    title: str
    icon: str
    lines: tuple[str, ...]
    buttons: tuple[tuple[str, str, str], ...] = ()   # (id, label, icon)
    # Agent 9: a live list under the card, newest first: (icon, title, detail).
    rows: tuple[tuple[str, str, str], ...] = ()
    rows_title: str = ""


@dataclass
class Approval:
    """The agent asks before a tool: drawn above the input until you answer."""

    title: str                  # "Claude Code wants to run bash"
    detail: str = ""            # the tool's input on one line
    waiting: int = 1            # questions open, this one included
    note: str = ""              # e.g. "Coworker's Blender tool"
    # (id, label, icon) per button; a question (Engine 6's ask_user) has its choices here.
    buttons: tuple[tuple[str, str, str], ...] = (
        ("once", "Allow once", "CHECKMARK"), ("always", "Always", "PINNED"), ("deny", "Deny", "CANCEL"))
    action: str = "approve"     # the action a button sends: (action, button id)
    icon: str = "LOCKED"


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
    attachment_note: str = ""         # the tray's second line instead (External Harness: #199)
    # A column of icon buttons beside the messages (#214): (action, icon); ("", "") = a gap.
    tools: tuple[tuple[str, str], ...] = ()
    # Tooltips (#237): the widget under a resting mouse and since when; *tip_wait* > 0 asks the
    # host to draw again that many seconds later, when the tooltip is due.
    clock: Callable[[], float] = time.monotonic
    tip_wait: float = 0.0
    _tip: tuple[str, float] | None = None

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
             top: TopBar | None = None, notice: Notice | None = None,
             approval: Approval | None = None) -> list[tuple[str, str]]:
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
            # A short compact chat (#182): the mode row goes first, so the messages keep room.
            if self.compact and top.chat_modes and rect.h < style.unit * 10:
                top = replace(top, chat_modes=())
            top_edge = self._draw_top_bar(canvas, Rect(rect.x, rect.y, rect.w, rect.h), top, actions)
        inner = Rect(rect.x + pad, rect.y + pad, rect.w - 2 * pad, rect.h)
        # A notice (External Harness) replaces the chat: nothing to type into.
        show_input = notice is None
        self.box.big_buttons = not self.compact
        box_h = self.box.height(canvas, inner.w) if show_input else 0.0
        input_rect = Rect(inner.x, inner.y, inner.w, box_h)
        chat_y = input_rect.top + pad if show_input else rect.y
        if self.box.attachment:      # the image tray, above the input (or the notice: #199)
            chat_y = self._draw_tray(canvas, Rect(inner.x, chat_y if show_input else rect.y + pad, inner.w, 0),
                                     actions) + pad
        if approval is not None:     # the question, right above the input (or at the bottom: #166)
            anchor_y = chat_y if show_input else rect.y + pad
            chat_y = self._draw_approval(canvas, Rect(inner.x, anchor_y, inner.w, 0), approval, actions) + pad
        chat = Rect(rect.x, chat_y, rect.w, max(0.0, top_edge - chat_y - pad))
        if self.tools and notice is None and chat.w > style.unit * 6:
            chat = self._draw_tools(canvas, chat, actions)
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
        self._tooltip(canvas, rect)
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

    def _tooltip(self, canvas: Canvas, rect: Rect) -> None:
        """The tooltip of the button under a resting mouse, after TIP_DELAY (#237)."""
        self.tip_wait = 0.0
        widget = self.hit(*self.mouse) if self.mouse is not None and self.pressed is None else None
        tip = tip_for(widget)
        if not tip:
            self._tip = None
            return
        now = self.clock()
        if self._tip is None or self._tip[0] != widget:
            self._tip = (widget, now)
        wait = TIP_DELAY - (now - self._tip[1])
        if wait > 0:
            self.tip_wait = wait
            return
        style = canvas.style
        pad = style.pad
        width = min(canvas.measure(tip, "ui", style.font_size) + 2 * pad, rect.w - 2 * pad, style.unit * 16)
        h = canvas.text_height(tip, width - 2 * pad) + pad
        mx, my = self.mouse
        x = max(rect.x + pad, min(mx - width / 2, rect.right - pad - width))
        y = my - style.unit * 1.1 - h                 # under the mouse, or over it near the bottom
        if y < rect.y + pad:
            y = my + style.unit * 0.9
        box = Rect(x, y, width, h)
        canvas.box(box, colors=style.tooltip)
        canvas.wrapped(box.x + pad, box.top - pad * 0.5, width - 2 * pad, tip, style.tooltip.text)

    def _draw_top_bar(self, canvas: Canvas, rect: Rect, top: TopBar, actions: list[tuple[str, str]]) -> float:
        """Mode + status + buttons, then the chat-mode row; return the bar's bottom edge."""
        style = canvas.style
        u, pad = style.unit, style.pad * 0.7
        row = Rect(rect.x + pad, rect.top - pad - u, rect.w - 2 * pad, u)
        side = canvas.icon_size()
        # The mode icon is a button: it opens the mode menu (#193).
        if canvas.button(Rect(row.x - pad * 0.25, row.y, u, u), "mode_menu", "",          # square (#238)
                         icon=top.mode_icon):
            actions.append(("mode_menu", ""))
        # Buttons from the right: settings, open the editor, Start/Stop (square icons: #238).
        bw = u
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
        sx = row.x + u + pad * 0.5
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
            for i, item in enumerate(top.chat_modes):
                mode_id, label = item[0], item[1]
                icon = item[2] if len(item) > 2 else ""
                if w < u * 3.2:          # narrow: the icon says it (the tooltip names it)
                    label = "" if icon else label
                if canvas.button(Rect(mrow.x + i * w, mrow.y, w, u), "mode:" + mode_id, label,
                                 active=mode_id == top.chat_mode, icon=icon):
                    actions.append(("chat_mode", mode_id))
            bottom = mrow.y - pad * 0.5
        return bottom

    def _draw_tools(self, canvas: Canvas, chat: Rect, actions: list[tuple[str, str]]) -> Rect:
        """The icon column on the left of the messages (#214); returns the room left for them."""
        style = canvas.style
        u, pad = style.unit, style.pad * 0.5
        side = u * 1.15
        x, y = chat.x + pad, chat.top - pad
        for action, icon in self.tools:
            if not action:
                y -= pad * 1.5
                continue
            if y - side < chat.y:
                break
            if canvas.button(Rect(x, y - side, side, side), "tool:" + action, "", icon=icon):
                actions.append((action, ""))
            y -= side + pad * 0.4
        return Rect(x + side, chat.y, max(0.0, chat.right - x - side), chat.h)

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
        bw = u                               # square icon buttons (#238)
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
        status = _fit(canvas, self.attachment_note or (
            "Kept for every message" if self.attachment_pinned else "Sent with the next message"), room + side)
        canvas.label(Rect(tx, mid - style.line_height, room + side, style.line_height), status,
                     _soft(style.box.text, 0.35))
        return tray.top

    def _draw_approval(self, canvas: Canvas, anchor: Rect, approval: Approval,
                       actions: list[tuple[str, str]]) -> float:
        """Who wants to run what, and Allow once / Always / Deny.  Returns its top."""
        style = canvas.style
        u, pad = style.unit, style.pad * 0.7
        width = anchor.w - 2 * pad
        side = canvas.icon_size()
        title = approval.title + ("  (+{:d} more)".format(approval.waiting - 1) if approval.waiting > 1 else "")
        title_h = canvas.text_height(title, width - side - pad * 0.6)
        note_h = canvas.text_height(approval.note, width) if approval.note else 0.0
        detail_h = canvas.text_height(approval.detail, width - pad, "mono") + pad if approval.detail else 0.0
        buttons = approval.buttons
        needs = [(side if icon else 0.0) + canvas.measure(label, "ui", style.font_size) + 3.0 * style.pad
                 for _c, label, icon in buttons]
        side_by_side = sum(needs) + pad * 0.5 * (len(needs) - 1) <= width
        rows = 1 if side_by_side else len(buttons)
        h = title_h + note_h + detail_h + rows * u * 1.25 + 2.5 * pad
        card = Rect(anchor.x, anchor.y, anchor.w, h)
        canvas.box(card, colors=style.box)
        # A thin accent line on the left: this needs you.
        canvas.fill(Rect(card.x, card.y + style.scale * 2, style.scale * 3, card.h - style.scale * 4),
                    style.cursor, style.scale)
        top = card.top - pad
        canvas.icon(Rect(card.x + pad, top - style.line_height + (style.line_height - side) / 2, side, side),
                    approval.icon, style.box.text)
        top = canvas.wrapped(card.x + pad + side + pad * 0.6, top, width - side - pad * 0.6, title,
                             style.box.text, bold=True)
        if approval.note:
            top = canvas.wrapped(card.x + pad, top, width, approval.note, _soft(style.box.text, 0.35))
        if approval.detail:
            frame_h = detail_h - pad * 0.5
            frame = Rect(card.x + pad, top - pad * 0.25 - frame_h, width, frame_h)
            canvas.fill(frame, style.text_field.inner, style.radius(style.text_field))
            canvas.wrapped(frame.x + pad * 0.5, frame.top - pad * 0.25, width - pad, approval.detail,
                           style.text_field.text, font="mono")
        bx, by = card.x + pad, card.y + pad + (rows - 1) * u * 1.25
        for (choice, label, icon), need in zip(buttons, needs):
            bw = need if side_by_side else width
            if canvas.button(Rect(bx, by, bw, u * 1.1), approval.action + ":" + choice, label, icon=icon):
                actions.append((approval.action, choice))
            if side_by_side:
                bx += bw + pad * 0.5
            else:
                by -= u * 1.25
        return card.top

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
        if notice.rows_title:
            self._draw_notice_rows(canvas, Rect(x, rect.y, width, card.y - pad - rect.y), notice)

    def _draw_notice_rows(self, canvas: Canvas, room: Rect, notice: Notice) -> None:
        """The live list under a notice: a title, then two lines per row while they fit."""
        style = canvas.style
        line = style.line_height
        side = canvas.icon_size()
        if room.h < line * 2:
            return
        top = room.top
        canvas.label(Rect(room.x, top - line, room.w, line), notice.rows_title, style.title, bold=True)
        top -= line * 1.2
        if not notice.rows:
            canvas.label(Rect(room.x, top - line, room.w, line), "Nothing yet: tool calls show up here as they run.",
                         _soft(style.text, 0.35))
            return
        text_x = room.x + side + style.pad * 0.6
        for icon, title, detail in notice.rows:
            if top - line * 2 < room.y:
                break
            canvas.icon(Rect(room.x, top - line + (line - side) / 2, side, side), icon,
                        (0.92, 0.40, 0.33, 1.0) if icon == "ERROR" else style.text)
            canvas.label(Rect(text_x, top - line, room.right - text_x, line),
                         _fit(canvas, title, room.right - text_x), style.text)
            canvas.label(Rect(text_x, top - 2 * line, room.right - text_x, line),
                         _fit(canvas, detail, room.right - text_x), _soft(style.text, 0.4))
            top -= line * 2.3


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
