# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Route window events to a GPU :class:`input_box.InputBox` (Tier 4, "GPU 3").

A GPU-drawn box gets no keyboard from Blender, so one small modal operator
per window watches events and hands them to the box that has focus:

* **click** in a box focuses it and places the cursor (Shift+click
  selects); a click anywhere else leaves it -- the click still goes through;
* **keys** go to the focused box, which uses them (Enter sends, Shift+Enter
  adds a line, ...); while it has focus, editor shortcuts never fire;
* Ctrl+C / X / V use the window manager clipboard;
* **Edit** opens the text in a native text field, which takes IME input
  (Chinese, Japanese, Korean...) that a GPU box can't;
* the caret blinks, and the operator restarts after a file is opened (a
  file load ends every modal operator) and for new windows.

The host (the Coworker editor, task "GPU 4") calls :func:`set_host` with a
*router* -- ``router(context, event) -> (box, area, region) | None``, the box
under the mouse -- and an *on_action* callback for what the box asks
(``send``, ``stop``).  Needs ``bpy``.
"""

from __future__ import annotations

from typing import Any, Callable

import bpy  # pylint: disable=import-error

from .input_box import InputBox, Key

__all__ = (
    "ensure_running",
    "focused_box",
    "key_from_event",
    "register",
    "set_host",
    "unregister",
)

Router = Callable[[Any, Any], "tuple[InputBox, Any, Any] | None"]
OnAction = Callable[[InputBox, str, str], None]

_HOST: dict[str, Any] = {"router": None, "on_action": None}
# The focused box and the area to redraw; the windows that have the operator running.
_FOCUS: dict[str, Any] = {"box": None, "area": None}
_RUNNING: set[int] = set()

_IGNORED = frozenset({"MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "TIMER", "TIMER_REPORT", "TIMERREGION",
                      "WINDOW_DEACTIVATE", "NONE"})
_NEWLINE_MARK = " ↵ "     # how a line break shows in the one-line native field
_BLINK_SECONDS = 0.53
_WATCH_SECONDS = 1.0


def set_host(router: Router | None, on_action: OnAction | None = None) -> None:
    """Set (or clear, with ``None``) where events go and who handles ``send`` / ``stop``."""
    _HOST["router"], _HOST["on_action"] = router, on_action
    if router is None:
        _blur()


def focused_box() -> InputBox | None:
    return _FOCUS["box"]


def key_from_event(event: Any) -> Key:
    return Key(type=event.type, value=event.value, unicode=event.unicode or "",
               shift=event.shift, ctrl=event.ctrl, alt=event.alt, oskey=event.oskey)


def _redraw() -> None:
    area = _FOCUS["area"]
    try:
        if area is not None:
            area.tag_redraw()
    except ReferenceError:      # the area went away (screen change, file load)
        _FOCUS["area"] = None


def _blur() -> None:
    box = _FOCUS["box"]
    if box is not None:
        box.focused = False
        box.suggestions = []
    _redraw()
    _FOCUS["box"] = _FOCUS["area"] = None


def _focus(box: InputBox, area: Any) -> None:
    if _FOCUS["box"] is not box:
        _blur()
    box.focused, box.blink_phase = True, True
    _FOCUS["box"], _FOCUS["area"] = box, area


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


class BFACW_OT_gpu_input(bpy.types.Operator):
    """Pass keys and clicks to the Coworker chat's input box"""

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
        if router is None:
            return self._finish()
        if event.type in _IGNORED:
            return {"PASS_THROUGH"}
        box = _FOCUS["box"]
        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "PRESS":
            hit = router(context, event)
            if hit is not None and event.type == "LEFTMOUSE":
                hit_box, area, region = hit
                mx, my = event.mouse_x - region.x, event.mouse_y - region.y
                if hit_box.rect is not None and hit_box.rect.contains(mx, my):
                    _focus(hit_box, area)
                    hit_box.place_cursor(mx, my, select=event.shift)
                    _redraw()
                    return {"RUNNING_MODAL"}
            if box is not None:
                _blur()
            return {"PASS_THROUGH"}
        if box is None:
            return {"PASS_THROUGH"}
        used = box.handle_key(key_from_event(event))
        _run_actions(box, context)
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
    if _HOST["router"] is None:
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


def _watch() -> float:
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
    _FOCUS["box"] = _FOCUS["area"] = None


_CLASSES = (BFACW_OT_gpu_input, BFACW_OT_gpu_input_native)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_pre.append(_on_load_pre)
    for fn, first in ((_watch, 0.2), (_blink, _BLINK_SECONDS)):
        if not bpy.app.timers.is_registered(fn):
            bpy.app.timers.register(fn, first_interval=first, persistent=True)


def unregister() -> None:
    set_host(None)
    for fn in (_watch, _blink):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _RUNNING.clear()
