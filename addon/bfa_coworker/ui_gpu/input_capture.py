# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Route window events to the GPU chat (Tier 4, tasks "GPU 3" and "GPU 4").

GPU-drawn widgets get no mouse or keyboard from Blender, so one small modal
operator per window watches events and hands them to the chat surface
(:class:`surface.ChatSurface`) under the mouse:

* **hover** redraws the surface when what's under the mouse changes (button
  highlights, the scrollbar fading in), and **press / release** make clicks;
  a press on the scrollbar handle keeps dragging even outside the region;
* the **wheel** scrolls the chat, **Ctrl+wheel** zooms it;
* **click** in the input box focuses it and places the cursor (Shift+click
  selects); a click anywhere else leaves it -- that click still goes through;
* **keys** go to the focused box (Enter sends, Shift+Enter adds a line...);
  while it has focus, editor shortcuts never fire;
* Ctrl+C / X / V use the window manager clipboard;
* **Edit** opens the text in a native text field, which takes IME input
  (Chinese, Japanese, Korean...) that a GPU box can't;
* the caret blinks, and the operator restarts after a file is opened (a
  file load ends every modal operator) and for new windows;
* **autosave** gets a gap: Blender skips autosave while any modal operator
  runs, so every ~30 s, once the chat has had no keys or clicks for a
  moment, the operator ends itself in every window for a quarter second --
  the pending autosave lands -- and then restarts (#370).

Only the region is redrawn, never the whole area (a 3D View sidebar must
not re-render the viewport).  The host calls :func:`set_host` with a
*router* -- ``router(context, event) -> (surface, area, region) | None`` --
and an *on_action* callback for what the box asks (``send``, ``stop``...).
A bare :class:`input_box.InputBox` also works as a target (input only).
Needs ``bpy``.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import bpy  # pylint: disable=import-error

from .input_box import InputBox, Key

__all__ = (
    "ensure_running",
    "focus",
    "focused_box",
    "gap_due",
    "key_from_event",
    "register",
    "set_host",
    "unregister",
)

Router = Callable[[Any, Any], "tuple[Any, Any, Any] | None"]
OnAction = Callable[[InputBox, str, str], None]

_HOST: dict[str, Any] = {"router": None, "on_action": None, "after_key": None}
# The focused box and its region; the surface being dragged / hovered; running windows.
_FOCUS: dict[str, Any] = {"box": None, "region": None}
_DRAG: dict[str, Any] = {"hit": None}
# Ctrl+MMB drag zoom over a full-size chat: (hit, start mouse y, start zoom).
_ZOOM_DRAG: dict[str, Any] = {"hit": None, "y": 0.0, "zoom": 1.0}
_HOVER: dict[str, Any] = {"hit": None, "text_cursor": False}
_RUNNING: set[int] = set()
# The autosave gap (#370): paused while it's open, when it last opened, the last key / click, and
# the window timers that wake each operator so it can end.
_AUTOSAVE: dict[str, Any] = {"paused": False, "last_gap": 0.0, "last_input": 0.0, "timers": []}

_IGNORED = frozenset({"TIMER", "TIMER_REPORT", "TIMERREGION", "WINDOW_DEACTIVATE", "NONE"})
_MOVES = frozenset({"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"})
_WHEEL = {"WHEELUPMOUSE": True, "WHEELDOWNMOUSE": False}
_NEWLINE_MARK = " ↵ "     # how a line break shows in the one-line native field
_BLINK_SECONDS = 0.53
_WATCH_SECONDS = 1.0
_GAP_EVERY_SECONDS = 30.0       # at most this long between autosave gaps (or the autosave interval, if shorter)
_GAP_SECONDS = 0.25             # how long the operators stay off; Blender retries autosave every 10 ms
_INPUT_LULL_SECONDS = 1.5       # no gap until the chat has had no keys / clicks for this long


def set_host(router: Router | None, on_action: OnAction | None = None,
             after_key: Callable[[InputBox], None] | None = None) -> None:
    """Set (or clear, with ``None``) where events go, who handles ``send`` / ``stop``,
    and who hears about every key the focused box used (*after_key*)."""
    _HOST["router"], _HOST["on_action"], _HOST["after_key"] = router, on_action, after_key
    if router is None:
        _blur()
        _DRAG["hit"] = _HOVER["hit"] = None


def focused_box() -> InputBox | None:
    return _FOCUS["box"]


def key_from_event(event: Any) -> Key:
    return Key(type=event.type, value=event.value, unicode=event.unicode or "",
               shift=event.shift, ctrl=event.ctrl, alt=event.alt, oskey=event.oskey)


def _tag(region: Any) -> None:
    try:
        if region is not None:
            region.tag_redraw()
    except ReferenceError:      # the region went away (screen change, file load)
        pass


def _redraw() -> None:
    _tag(_FOCUS["region"])


def _blur() -> None:
    box = _FOCUS["box"]
    if box is not None:
        box.focused = False
        box.suggestions = []
    _redraw()
    _FOCUS["box"] = _FOCUS["region"] = None


def focus(box: InputBox, region: Any) -> None:
    """Give *box* (drawn in *region*) the keyboard."""
    if _FOCUS["box"] is not box:
        _blur()
    box.focused, box.blink_phase = True, True
    _FOCUS["box"], _FOCUS["region"] = box, region
    _redraw()


def _run_actions(box: InputBox, context: Any) -> None:
    actions, box.actions = box.actions, []
    for action, value in actions:
        if action == "copy":
            context.window_manager.clipboard = value
        elif action == "paste":
            box.paste(context.window_manager.clipboard or "")
        elif action == "blur":
            _blur()
        elif action == "native_edit":
            bpy.ops.bfacw.gpu_input_native("INVOKE_DEFAULT")
        elif _HOST["on_action"] is not None:
            _HOST["on_action"](box, action, value)


def _local(event: Any, region: Any) -> tuple[float, float]:
    return float(event.mouse_x - region.x), float(event.mouse_y - region.y)


def _same(a: Any, b: Any) -> bool:
    return a is not None and b is not None and a[0] is b[0]


class BFACW_OT_gpu_input(bpy.types.Operator):
    """Pass mouse and keys to the Coworker chat"""

    bl_idname = "bfacw.gpu_input"
    bl_label = "Coworker Chat Input"
    bl_options = {"INTERNAL"}

    _window = 0

    def invoke(self, context, _event):
        ptr = context.window.as_pointer()
        if ptr in _RUNNING:
            return {"CANCELLED"}
        self._window = ptr
        _RUNNING.add(ptr)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _finish(self):
        _RUNNING.discard(self._window)
        return {"FINISHED", "PASS_THROUGH"}

    def modal(self, context, event):
        router = _HOST["router"]
        if router is None or _AUTOSAVE["paused"]:     # the gap's window timer wakes us to end (#370)
            return self._finish()
        if event.type not in _MOVES or _DRAG["hit"] is not None or _ZOOM_DRAG["hit"] is not None:
            if event.type not in _IGNORED:
                _AUTOSAVE["last_input"] = time.monotonic()
        if event.type in _IGNORED or not event.type:
            # No type = an event Python can't name, like a file drop (EVT_DROP): it must reach
            # the file handlers even while the box has focus (#215).
            return {"PASS_THROUGH"}
        try:
            return self._handle(context, event, router)
        except ReferenceError:      # a region/area went away mid-event
            _DRAG["hit"] = _HOVER["hit"] = None
            return {"PASS_THROUGH"}

    def _handle(self, context, event, router):
        drag = _DRAG["hit"]
        zoom = _ZOOM_DRAG["hit"]
        if zoom is not None:                           # Ctrl+MMB drag: up zooms in, like Blender's views
            if event.type in _MOVES:
                surface, _area, region = zoom
                surface.zoom = max(0.6, min(2.5, _ZOOM_DRAG["zoom"] + (event.mouse_y - _ZOOM_DRAG["y"]) * 0.005))
                _tag(region)
                return {"RUNNING_MODAL"}
            if event.type == "MIDDLEMOUSE" and event.value == "RELEASE":
                _ZOOM_DRAG["hit"] = None
                return {"RUNNING_MODAL"}
        if event.type == "MIDDLEMOUSE" and event.value == "PRESS" and event.ctrl:
            hit = router(context, event)
            # Full-size chats only: in a sidebar, Ctrl+MMB zooms the whole sidebar (and the chat with it).
            if hit is not None and hasattr(hit[0], "zoom") and not getattr(hit[0], "compact", True):
                _ZOOM_DRAG.update(hit=hit, y=float(event.mouse_y), zoom=hit[0].zoom)
                return {"RUNNING_MODAL"}
        if event.type in _MOVES:
            if drag is not None:                       # dragging: keep feeding, even outside
                surface, _area, region = drag
                surface.on_move(*_local(event, region))
                _tag(region)
                return {"RUNNING_MODAL"}
            hit = router(context, event)
            hover = _HOVER["hit"]
            if hover is not None and not _same(hover, hit):
                if hover[0].on_leave():
                    _tag(hover[2])
            _HOVER["hit"] = hit if hit is not None and hasattr(hit[0], "on_move") else None
            over_text = False
            if _HOVER["hit"] is not None:
                x, y = _local(event, hit[2])
                if hit[0].on_move(x, y):
                    _tag(hit[2])
                over_text = hit[0].hit(x, y) == "input"
            if over_text != _HOVER["text_cursor"]:    # an I-beam over the text, like a text field
                _HOVER["text_cursor"] = over_text
                try:
                    context.window.cursor_set("TEXT" if over_text else "DEFAULT")
                except (AttributeError, TypeError, ValueError):
                    pass
            return {"PASS_THROUGH"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and drag is not None:
            surface, _area, region = drag
            surface.on_release(*_local(event, region))
            _DRAG["hit"] = None
            _tag(region)
            return {"RUNNING_MODAL"}

        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "PRESS":
            hit = router(context, event)
            if hit is not None and event.type == "LEFTMOUSE":
                target, _area, region = hit
                x, y = _local(event, region)
                if hasattr(target, "on_press"):            # a chat surface
                    used = target.on_press(x, y, shift=event.shift)
                    if target.box.focused:
                        focus(target.box, region)
                    elif _FOCUS["box"] is target.box:
                        _blur()
                    if target.pressed is not None:
                        _DRAG["hit"] = hit
                    _tag(region)
                    if used:
                        return {"RUNNING_MODAL"}
                elif target.rect is not None and target.rect.contains(x, y):   # a bare input box
                    focus(target, region)
                    target.place_cursor(x, y, select=event.shift)
                    return {"RUNNING_MODAL"}
            if _FOCUS["box"] is not None:
                _blur()
            return {"PASS_THROUGH"}

        if event.type in _WHEEL:
            hit = router(context, event)
            if hit is not None and hasattr(hit[0], "on_wheel"):
                if hit[0].on_wheel(_WHEEL[event.type], event.ctrl, *_local(event, hit[2])):
                    _tag(hit[2])
                    return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        box = _FOCUS["box"]
        if box is None:
            return {"PASS_THROUGH"}
        used = box.handle_key(key_from_event(event))
        _run_actions(box, context)
        if used and _HOST["after_key"] is not None:
            _HOST["after_key"](box)
        _redraw()
        return {"RUNNING_MODAL"} if used else {"PASS_THROUGH"}


class BFACW_OT_gpu_input_native(bpy.types.Operator):
    """Edit the message in a native text field (supports IME input)"""

    bl_idname = "bfacw.gpu_input_native"
    bl_label = "Edit Message"
    bl_options = {"INTERNAL"}

    text: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Message",
        description="Line breaks show as ↵ and come back as line breaks",
        options={"SKIP_SAVE"},
    )

    def invoke(self, context, _event):
        box = _FOCUS["box"]
        if box is None:
            return {"CANCELLED"}
        self.text = box.model.text.replace("\n", _NEWLINE_MARK)
        return context.window_manager.invoke_props_dialog(self, width=int(520 * context.preferences.view.ui_scale))

    def draw(self, _context):
        self.layout.prop(self, "text", text="")

    def execute(self, _context):
        box = _FOCUS["box"]
        if box is not None:
            box.model.set_text(self.text.replace(_NEWLINE_MARK, "\n").replace("↵", "\n"))
            box.focused = True
            _redraw()
        return {"FINISHED"}


def ensure_running() -> None:
    """Start the input operator in every window that doesn't have it yet."""
    if _HOST["router"] is None or _AUTOSAVE["paused"]:
        return
    wm = bpy.context.window_manager
    _RUNNING.intersection_update(w.as_pointer() for w in wm.windows)   # closed windows
    for window in wm.windows:
        if window.as_pointer() in _RUNNING:
            continue
        try:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.bfacw.gpu_input("INVOKE_DEFAULT")
        except (RuntimeError, TypeError):   # window closing / no screen yet: next tick
            pass


def gap_due(now: float, last_gap: float, last_input: float,
            autosave_on: bool, autosave_minutes: float) -> bool:
    """Is it time to let Blender's autosave through (#370)?"""
    if not autosave_on:
        return False
    every = min(_GAP_EVERY_SECONDS, max(1.0, autosave_minutes) * 60.0)
    return now - last_gap >= every and now - last_input >= _INPUT_LULL_SECONDS


def _autosave_prefs() -> tuple[bool, float]:
    try:
        paths = bpy.context.preferences.filepaths
        return bool(paths.use_auto_save_temporary_files), float(paths.auto_save_time)
    except (AttributeError, TypeError):
        return False, 0.0


def _open_gap() -> None:
    """End the operator in every window for a moment, so the waiting autosave can run."""
    _AUTOSAVE["paused"], _AUTOSAVE["last_gap"] = True, time.monotonic()
    wm = bpy.context.window_manager
    for window in wm.windows:
        if window.as_pointer() in _RUNNING:
            try:        # an idle window gets no events: a timer wakes its operator
                _AUTOSAVE["timers"].append(wm.event_timer_add(0.02, window=window))
            except (RuntimeError, TypeError, ValueError):
                pass
    bpy.app.timers.register(_close_gap, first_interval=_GAP_SECONDS, persistent=True)


def _close_gap() -> None:
    wm = bpy.context.window_manager
    for timer in _AUTOSAVE["timers"]:
        try:
            wm.event_timer_remove(timer)
        except (RuntimeError, TypeError, ValueError, ReferenceError):
            pass
    _AUTOSAVE["timers"].clear()
    _AUTOSAVE["paused"] = False
    ensure_running()


def _watch() -> float:
    if not _AUTOSAVE["paused"]:
        if _HOST["router"] is not None and _RUNNING and gap_due(
                time.monotonic(), _AUTOSAVE["last_gap"], _AUTOSAVE["last_input"], *_autosave_prefs()):
            _open_gap()
        else:
            ensure_running()
    return _WATCH_SECONDS


def _blink() -> float:
    box = _FOCUS["box"]
    if box is not None:
        box.blink_phase = not box.blink_phase
        _redraw()
    return _BLINK_SECONDS


@bpy.app.handlers.persistent
def _on_load_pre(*_args) -> None:
    # Opening a file ends every modal operator and frees every area.
    _RUNNING.clear()
    _FOCUS["box"] = _FOCUS["region"] = None
    _DRAG["hit"] = _HOVER["hit"] = _ZOOM_DRAG["hit"] = None


_CLASSES = (BFACW_OT_gpu_input, BFACW_OT_gpu_input_native)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_pre.append(_on_load_pre)
    _AUTOSAVE["last_gap"] = time.monotonic()
    for fn, first in ((_watch, 0.2), (_blink, _BLINK_SECONDS)):
        if not bpy.app.timers.is_registered(fn):
            bpy.app.timers.register(fn, first_interval=first, persistent=True)


def unregister() -> None:
    set_host(None)
    if bpy.app.timers.is_registered(_close_gap):
        bpy.app.timers.unregister(_close_gap)
        _close_gap()
    for fn in (_watch, _blink):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _RUNNING.clear()
