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
:meth:`on_release` / :meth:`on_wheel` / :meth:`on_right_press` in region
pixels (the host, ``input_capture``, calls them); clicks land on the next
:meth:`draw`, which returns the actions for the host to carry out, e.g. ``("send", text)``,
``("stop", "")``, ``("copy", text)``, ``("link", url)``, ``("start", "")``,
``("stop_agent", "")``, ``("open_editor", "")``, ``("prefs", "")``,
``("chat_mode", "ASK")`` or a notice button's ``("notice", id)``.

Text is selected with the mouse (#451, #455): a drag in the prompt selects
there (the box's own selection, as Shift+arrows make); a press on a message
starts a selection in the chat that a drag extends, across lines, blocks and
turns (the wheel scrolls meanwhile); a double-click selects a word; a click
elsewhere clears it.  :meth:`ChatSurface.selected_text` gives it for Ctrl+C
and the right-click menu.

Long messages (#389): the wheel over the input scrolls the input while its text
is taller than the box (else the chat); a drag on the input's top edge sizes it
(up to about half the chat), with an up-down cursor (:meth:`ChatSurface.cursor_at`).
Free of ``bpy``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from .chat_view import ChatView, _soft, choice_height, draw_choice
from .input_box import InputBox
from .layout import Canvas, InteractionState, Rect, TextLine, row_corners
from .selection import over_text, point_to_pos
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
    "approval_menu": "Permissions: Strict, Permissive or Auto -- how much freedom the tools get",
    "queue": "Queue this message: it goes when the running turn ends",
    "stop": "Stop the running turn",
    "start": "Start Coworker",
    "stop_agent": "Stop Coworker",
    "prefs": "Coworker settings",
    "open_editor": "Open the Coworker editor: the full chat in its own window",
    "mode_menu": "Mode: Local, Remote, Agent or Harness, and its model",
    "copy_error": "Copy the error",
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
    "notice:bridge": "Start or stop the bridge your outside client talks to",
    "notice:copy_config": "Copy your client's config, to paste into its MCP settings",
    "input": "Your message: Enter sends, Shift+Enter is a new line, @ mentions, / runs an agent command",
    "queue": "Queue this message: it goes when the running turn ends",
    "resize": "Drag to make the chat taller or shorter",
    "input_resize": "Drag to make the input taller or shorter (Ctrl+Up / Ctrl+Down while typing)",
    "scroll_track": "Click to page up or down",
    "scroll_handle": "Drag to scroll the chat",
    "approve:once": "Allow this one step (the next one asks again)",
    "approve:always": "Allow every tool for the rest of this turn; your next message asks again",
    "approve:deny": "Don't run it: the model is told you declined",
    "plan_card:start": "Carry out the plan now, step by step (Coworker mode, the engine that is on)",
    "plan_card:refine": "Hide this card and type what should change in the plan",
    "mcat:": "Show every kind",
    "jobs": ("Blender runs this render or bake in the background, and Coworker waits for it. To stop it, "
             "press Esc in the window it started in, or click the X on the job bar in the status bar; "
             "Coworker can't stop it from here yet"),
}
TIP_PREFIXES: tuple[tuple[str, str], ...] = (
    ("helper:", "Show this helper chat's tab: it works on a part of this chat's job"),
    ("copymsg:", "Copy the reply"),
    ("copyuser:", "Copy your message"),
    ("copythink:", "Copy this thinking"),
    ("copycode:", "Copy the code it ran"),
    ("copyres:", "Copy the result"),
    ("copy:", "Copy the code"),
    ("img:", "Open this image in an Image Editor"),
    ("suggest:", "Send this as your next message (No, something else: you finish it in the input)"),
    ("ws:", "Fold or unfold the Workshop: the turn's tool steps"),
    ("more:", "Show the whole entry, or fold it again"),
    ("answer:", "Answer with this choice"),
    ("mention:", "Insert this into your message (Enter or Tab)"),
    ("mcat:", "Show only this kind"),
    ("link:", "Open this link in your browser"),
)
TIP_DELAY = 0.5      # seconds the mouse rests before a tooltip shows: Blender's UI_TOOLTIP_DELAY
TYPING_QUIET = 2.0   # no tooltips this long after a key in the input (#388)


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
    "Helpers",
    "Notice",
    "TopBar",
)

_ZOOM_STEP = 0.1
_ZOOM_MIN, _ZOOM_MAX = 0.6, 2.5
_WHEEL_LINES = 3
_DRAG_PIXELS = 3.0     # a press moves this far before it drags a selection (a click doesn't select)
_TEXT_DRAG = ("input", "select")    # pressed while dragging a selection: in the prompt / in the chat
# Pressing these keeps the chat's selection: they only move the view.
_KEEPS_SELECTION = ("scroll_handle", "scroll_track", "jump", "resize", "input_resize")
# Dragged, not clicked: a release anywhere ends them.
_DRAGGED = ("scroll_handle", "resize", "input_resize")
_RESIZE_CURSOR = "MOVE_Y"   # Blender's up-down arrows (Window.cursor_set), as on an area's edge


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
        ("once", "Allow once", "CHECKMARK"), ("always", "Allow this turn", "PINNED"), ("deny", "Deny", "CANCEL"))
    action: str = "approve"     # the action a button sends: (action, button id)
    icon: str = "LOCKED"


@dataclass
class Helpers:
    """The asking chat's helper chats while any is unfinished (#479): one row each, a click
    shows its tab."""

    title: str                                   # "2 helpers -- 1 working"
    rows: tuple[tuple[str, str, str, str], ...] = ()     # (thread id, title, status word, icon)


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
    # The sidebar chat's icon buttons, in a row along its bottom edge (#214): (action, icon);
    # ("", "") = a gap.
    tools: tuple[tuple[str, str], ...] = ()
    # Tooltips (#237): the widget under a resting mouse and since when; *tip_wait* > 0 asks the
    # host to draw again that many seconds later, when the tooltip is due.
    clock: Callable[[], float] = time.monotonic
    # The notice's live list (#243): rows scrolled past (newest first) and how far it can go.
    notice_scroll: int = 0
    _notice_room: Rect | None = None
    _notice_max: int = 0
    tip_wait: float = 0.0
    _tip: tuple[str, float] | None = None
    # #388: like native tooltips -- off with Preferences > Interface > Tooltips (the host sets
    # *show_tips*), only after the mouse *rests*, gone on any move or click, and none while
    # you type or a reply streams.
    show_tips: bool = True
    _rest: float = 0.0           # when the mouse last moved
    _tip_shown: bool = False     # a tooltip is on screen now (a move must redraw to hide it)
    _tip_blocked: bool = False   # clicked: no tooltip until the mouse moves again
    _typed: tuple[str, float] = ("", -1e9)    # the draft as last drawn, and when it changed
    # Selecting with the mouse (#451, #455): the chat's text lines and measure of the last
    # frame, where the press was (a drag starts past _DRAG_PIXELS), the last press for a
    # double-click (the host sets Blender's double-click time).
    double_click_time: float = 0.35
    _lines: list[TextLine] = field(default_factory=list)
    _measure: Callable[[str, str, float], float] | None = None
    _press_at: tuple[float, float] | None = None
    _dragging: bool = False
    _last_press: tuple[float, float, float] | None = None

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
        moved = self.mouse != (x, y)
        self.mouse = (x, y)
        if moved:
            self._rest = self.clock()
            self._tip_blocked = False
            if self._tip_shown:          # any move hides the tooltip (#388)
                self._tip_shown = False
                return True
        if self.pressed == "resize" and self.on_resize is not None and self.bounds is not None:
            self.on_resize(max(0.0, self.bounds.top - y))
            return True
        if self.pressed == "input_resize":           # the input's top edge (#389)
            self.box.drag_top(y)
            return True
        if self.pressed in _TEXT_DRAG:
            if not self._dragging and self._press_at is not None:
                px, py = self._press_at
                self._dragging = abs(x - px) > _DRAG_PIXELS or abs(y - py) > _DRAG_PIXELS
            if self._dragging:
                if self.pressed == "input":
                    self.box.place_cursor(x, y, select=True, drag=True)
                else:
                    pos = self._chat_pos(x, y)
                    if pos is not None:
                        self.view.extend(pos)
            return True
        return self.pressed is not None or before != self.hit(x, y) or self._near_scrollbar(x)

    def on_leave(self) -> bool:
        changed = self.mouse is not None
        self.mouse = None
        return changed

    def on_press(self, x: float, y: float, shift: bool = False, double: bool = False) -> bool:
        """A left press; True when the surface used it.  *double*: the host saw a
        double-click (a second press soon on the same spot counts as one too)."""
        self.mouse = (x, y)
        self._tip_blocked, self._tip_shown = True, False      # a click hides it (#388)
        now = self.clock()
        last = self._last_press
        if not double and last is not None and now - last[0] <= self.double_click_time \
                and abs(x - last[1]) <= _DRAG_PIXELS and abs(y - last[2]) <= _DRAG_PIXELS:
            double = True
        self._last_press = None if double else (now, x, y)     # a third press starts again
        self._press_at, self._dragging = (x, y), False
        widget = self.hit(x, y)
        if widget not in _KEEPS_SELECTION:
            self.view.clear_selection()       # a click elsewhere clears it (a new one may start below)
        if widget is None:
            pos = self._chat_pos(x, y, press=True)
            if pos is not None:              # on a message: select its text (#455)
                if self.box.focused:
                    self.box.focused, self.box.suggestions = False, []
                if double:
                    self.view.select_word(pos)
                else:
                    self.view.select(pos)
                self.pressed = "select"
                return True
            if self.box.focused:
                self.box.focused, self.box.suggestions = False, []
            return False
        if widget == "input":
            self.box.focused = True
            if double:
                self.box.select_word_at(x, y)
            else:
                self.box.place_cursor(x, y, select=shift)
            self.pressed = "input"            # a drag selects (#451)
            return True
        if widget == "input_resize":
            self.box.begin_resize()
        self.pressed = widget
        return True

    def _chat_pos(self, x: float, y: float, press: bool = False) -> tuple | None:
        """The chat text position at (x, y), from the last frame.  For a press only on a
        message (inside its turn); while dragging the nearest text on screen."""
        if self._measure is None or self._chat_rect is None:
            return None
        lines = self._lines
        if press:
            if not self._chat_rect.contains(x, y):
                return None
            turn = self.view.turn_at(x, y)
            if turn is None:
                return None
            lines = [line for line in lines if line.key[0] == turn["idx"]]
        return point_to_pos(lines, x, y, self._measure)

    def cursor_at(self, x: float, y: float) -> str:
        """The mouse cursor for (x, y): up-down arrows on an edge that resizes (#389), an
        I-beam over text (#455), else the default."""
        if self.hit(x, y) in ("input_resize", "resize"):
            return _RESIZE_CURSOR
        return "TEXT" if self.over_text(x, y) else "DEFAULT"

    def over_text(self, x: float, y: float) -> bool:
        """Is (x, y) over text one can select (the prompt, or a message)?  For the I-beam."""
        widget = self.hit(x, y)
        if widget == "input":
            return True
        return (widget is None and self._chat_rect is not None and self._chat_rect.contains(x, y)
                and over_text(self._lines, x, y))

    def has_selection(self) -> bool:
        """Is text selected in the chat (not the prompt: that's the box's own)?"""
        return self.view.has_selection()

    def selected_text(self) -> str:
        """The chat's selected text, as shown (#455)."""
        return self.view.selected_text()

    def clear_selection(self) -> bool:
        """Drop the chat's selection; True when there was one."""
        return self.view.clear_selection()

    def turn_at(self, x: float, y: float) -> dict | None:
        """The message at (x, y) in the last frame (see :meth:`ChatView.turn_at`), or None."""
        if self._chat_rect is None or not self._chat_rect.contains(x, y):
            return None
        return self.view.turn_at(x, y)

    def on_right_press(self, x: float, y: float) -> dict:
        """A right press (#453): hides the tooltip and returns what the context menu shows
        entries for -- the ``widget`` and the message (``turn``) under the mouse."""
        self.mouse = (x, y)
        self._tip_blocked, self._tip_shown = True, False
        widget = self.hit(x, y)
        return {"widget": widget, "turn": self.turn_at(x, y) if widget != "input" else None}

    def on_release(self, x: float, y: float) -> bool:
        """A left release; a press and release on the same widget is a click."""
        if self.pressed is None:
            return False
        if self.pressed in _TEXT_DRAG:           # the end of a selection drag, not a click
            if self.pressed == "select" and not self.view.has_selection():
                self.view.clear_selection()      # a plain click on a message selects nothing
            self.pressed, self._dragging, self._press_at = None, False, None
            self.mouse = (x, y)
            return True
        if self.pressed == "input_resize":
            self.box.end_resize()
        if self.hit(x, y) == self.pressed or self.pressed in _DRAGGED:
            self._clicked = self.pressed if self.pressed not in _DRAGGED else None
        self.pressed = None
        self.mouse = (x, y)
        return True

    def on_wheel(self, up: bool, ctrl: bool, x: float, y: float) -> bool:
        """Scroll the chat (Ctrl: zoom the whole surface); True when used.  Over the input
        the input scrolls while its text is taller than the box, else the chat (#389)."""
        in_box = self.box.rect is not None and self.box.rect.contains(x, y)
        if not in_box and (self._chat_rect is None or not self._chat_rect.contains(x, y)):
            return False
        room = self._notice_room
        if not ctrl and not in_box and room is not None and room.contains(x, y):     # the harness list (#243)
            self.notice_scroll = max(0, min(self._notice_max, self.notice_scroll + (-1 if up else 1)))
            return True          # used, even at an end (no chat to scroll under it)
        if ctrl:
            self.zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, round(self.zoom + (_ZOOM_STEP if up else -_ZOOM_STEP), 2)))
            return True
        if in_box and self.box.scroll_by(-_WHEEL_LINES if up else _WHEEL_LINES):
            return True          # used, even at an end: the chat doesn't move under the text
        if self._chat_rect is None:
            return False
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
             approval: Approval | None = None, helpers: Helpers | None = None) -> list[tuple[str, str]]:
        """Draw into *rect* (region pixels); return this frame's actions."""
        style = canvas.style
        self._style = style
        actions: list[tuple[str, str]] = []
        pad = style.pad * (0.7 if self.compact else 1.0)
        canvas.push_clip(rect)      # never paint outside the chat's room (other panels)
        canvas.fill(rect, style.back)
        if self.compact and (self.on_resize is not None or (self.tools and notice is None)):
            rect = self._draw_grip(canvas, rect, self.tools if notice is None else (), actions)
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
        # The input may be made at most about half the chat's height (#389).
        self.box.line_cap = self.box.lines_fitting(canvas, (top_edge - inner.y) * 0.5)
        box_h = self.box.height(canvas, inner.w) if show_input else 0.0
        input_rect = Rect(inner.x, inner.y, inner.w, box_h)
        chat_y = input_rect.top + pad if show_input else rect.y
        if self.box.attachment:      # the image tray, above the input (or the notice: #199)
            chat_y = self._draw_tray(canvas, Rect(inner.x, chat_y if show_input else rect.y + pad, inner.w, 0),
                                     actions) + pad
        if approval is not None:     # the question, right above the input (or at the bottom: #166)
            anchor_y = chat_y if show_input else rect.y + pad
            chat_y = self._draw_approval(canvas, Rect(inner.x, anchor_y, inner.w, 0), approval, actions) + pad
        if helpers is not None and helpers.rows and show_input:     # its helper chats (#479)
            chat_y = self._draw_helpers(canvas, Rect(inner.x, chat_y, inner.w, 0), helpers, actions) + pad
        chat = Rect(rect.x, chat_y, rect.w, max(0.0, top_edge - chat_y - pad))
        self._chat_rect = chat
        self._notice_room = None
        if notice is not None:
            self.view.turn_rects = []       # no messages on screen: none for the right-click menu
            self.view.clear_selection()
            self._draw_notice(canvas, chat, notice, actions)
        else:
            # While a drag selects in the chat, its end follows the mouse on this frame's text
            # (the wheel may have scrolled it).
            drag = self.mouse if self.pressed == "select" and self._dragging else None
            actions += self.view.draw(canvas, chat, history, live=live, compact=self.compact, drag=drag)
        if show_input:
            before = len(self.box.actions)
            self.box.draw(canvas, input_rect)
            actions += self.box.actions[before:]
            del self.box.actions[before:]
        else:
            self.box.rect = None             # not on screen: the wheel and clicks don't find it
            self.box.focused = False
        if self.drop_hover:
            self._draw_drop_target(canvas, rect)
        canvas.pop_clip()
        self._hits = list(canvas.hit_areas)
        self._lines, self._measure = list(canvas.text_lines), canvas.measure
        self._clicked = None
        self._tooltip(canvas, rect, live)
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

    def _tooltip(self, canvas: Canvas, rect: Rect, live: Any = None) -> None:
        """The tooltip of the button under a resting mouse, after TIP_DELAY (#237, #388)."""
        self.tip_wait = 0.0
        self._tip_shown = False
        now = self.clock()
        text = self.box.model.text
        if text != self._typed[0]:
            self._typed = (text, now)
        typing = self.box.focused and now - self._typed[1] < TYPING_QUIET
        quiet = (not self.show_tips or self._tip_blocked or typing
                 or bool(getattr(live, "streaming_text", "")))                      # a reply streams
        widget = (self.hit(*self.mouse) if self.mouse is not None and self.pressed is None and not quiet
                  else None)
        tip = tip_for(widget)
        if widget == "approval_menu" and self.box.approval[0]:     # icon only: say which one is on
            tip = "Permissions: {:s} (click to change)".format(self.box.approval[0])
        if not tip:
            self._tip = None
            return
        if self._tip is None or self._tip[0] != widget:
            self._tip = (widget, now)
        # The mouse has to rest TIP_DELAY: moving over a button (or along a row) restarts it.
        wait = TIP_DELAY - (now - max(self._tip[1], self._rest))
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
        self._tip_shown = True

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
                                 active=mode_id == top.chat_mode, icon=icon,
                                 corners=row_corners(i, len(top.chat_modes))):
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
        # A question's choices: one wrapped, left-aligned button each, a keyframe before it --
        # green for the first (the recommended one), blue for the rest.
        choices = approval.action == "answer"
        heights = [choice_height(canvas, width, label) for _c, label, _i in buttons] if choices else []
        needs = [(side if icon else 0.0) + canvas.measure(label, "ui", style.font_size) + 3.0 * style.pad
                 for _c, label, icon in buttons]
        side_by_side = not choices and sum(needs) + pad * 0.5 * (len(needs) - 1) <= width
        rows = 1 if side_by_side else len(buttons)
        buttons_h = sum(heights) + pad * 0.35 * max(0, len(heights) - 1) if choices else rows * u * 1.25
        h = title_h + note_h + detail_h + buttons_h + 2.5 * pad
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
        if choices:
            y = card.y + pad + buttons_h
            for n, ((choice, label, _icon), bh) in enumerate(zip(buttons, heights)):
                y -= bh
                if draw_choice(canvas, Rect(card.x + pad, y, width, bh), approval.action + ":" + choice, label,
                               recommended=n == 0):
                    actions.append((approval.action, choice))
                y -= pad * 0.35
            return card.top
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

    def _draw_helpers(self, canvas: Canvas, anchor: Rect, helpers: Helpers,
                      actions: list[tuple[str, str]]) -> float:
        """The helper chats of this chat: a title, then one row each (status icon, title,
        status); a row is a button that shows that helper's tab.  Returns its top."""
        style = canvas.style
        u, pad = style.unit, style.pad * 0.6
        row_h = u * 1.05
        h = u + len(helpers.rows) * row_h + 2 * pad
        card = Rect(anchor.x, anchor.y, anchor.w, h)
        canvas.box(card, colors=style.box)
        side = canvas.icon_size()
        head = Rect(card.x + pad, card.top - pad - u, card.w - 2 * pad, u)
        canvas.icon(Rect(head.x, head.y + (u - side) / 2, side, side), "WIZARD", style.box.text)
        canvas.label(Rect(head.x + side + pad * 0.6, head.y, head.w - side, u),
                     _fit(canvas, helpers.title, head.w - side - pad), style.box.text, bold=True)
        y = head.y
        for thread_id, title, word, icon in helpers.rows:
            y -= row_h
            row = Rect(card.x + pad, y, card.w - 2 * pad, row_h * 0.95)
            widget = "helper:" + thread_id
            text = _fit(canvas, "{:s}  —  {:s}".format(title, word), row.w - side - 3 * pad)
            if canvas.button(row, widget, text, icon=icon, align="left"):
                actions.append(("show_helper", thread_id))
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

    def _draw_grip(self, canvas: Canvas, rect: Rect, tools: tuple[tuple[str, str], ...] = (),
                   actions: list[tuple[str, str]] | None = None) -> Rect:
        """The bottom edge: the sidebar chat's icon buttons on the left (#214), and the resize
        grip in the rest (drag anywhere off the buttons to change the panel's height)."""
        style = canvas.style
        u, pad = style.unit, style.pad * 0.5
        h = max(6.0, style.pad * 0.9) if not tools else u + pad * 1.5
        grip = Rect(rect.x, rect.y, rect.w, h)
        hovered = canvas.state.hovered(grip) or self.pressed == "resize"
        dots = style.cursor if hovered else _soft(style.text, 0.55)
        if self.on_resize is not None:
            canvas.hit_areas.append((grip, "resize"))        # under the buttons: they win
        x = grip.x + style.pad * 0.7
        groups: list[list[tuple[str, str]]] = [[]]
        for action, icon in tools:
            if action:
                groups[-1].append((action, icon))
            elif groups[-1]:
                groups.append([])
        for group in groups:                 # each group one joined row, like Blender's
            if not group or x + u * len(group) > grip.right - u * 2:
                break
            for i, (action, icon) in enumerate(group):
                if canvas.button(Rect(x, grip.y + (h - u) / 2, u, u), "tool:" + action, "", icon=icon,
                                 corners=row_corners(i, len(group))):
                    if actions is not None:
                        actions.append((action, ""))
                x += u
            x += pad * 1.5
        if self.on_resize is None:
            return Rect(rect.x, rect.y + h, rect.w, rect.h - h)
        cx, cy = (x + grip.right) / 2 if tools else grip.x + grip.w / 2, grip.y + grip.h / 2
        for i in range(-2, 3):
            canvas.fill(Rect(cx + i * 4 * style.scale - style.scale, cy - style.scale, 2 * style.scale,
                             2 * style.scale), dots, style.scale)
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
        self._notice_room, self._notice_max = None, 0
        if room.h < line * 2:
            return
        top = room.top
        per_row = line * 2.3
        fits = max(1, int((room.h - line * 1.2) // per_row))
        self._notice_room = room
        self._notice_max = max(0, len(notice.rows) - fits)            # the wheel scrolls it (#243)
        self.notice_scroll = min(self.notice_scroll, self._notice_max)
        title = notice.rows_title
        if self._notice_max:
            first = self.notice_scroll + 1
            title += "  ({:d}-{:d} of {:d})".format(first, min(len(notice.rows), first + fits - 1), len(notice.rows))
        canvas.label(Rect(room.x, top - line, room.w, line), title, style.title, bold=True)
        top -= line * 1.2
        if not notice.rows:
            canvas.label(Rect(room.x, top - line, room.w, line), "Nothing yet: tool calls show up here as they run.",
                         _soft(style.text, 0.35))
            return
        text_x = room.x + side + style.pad * 0.6
        for icon, title, detail in notice.rows[self.notice_scroll:]:
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
