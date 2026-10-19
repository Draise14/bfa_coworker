# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Check for the GPU chat's input box (task "GPU 3").  Not shipped.

Draws an :class:`ui_gpu.input_box.InputBox` pinned to the bottom of a
Coworker node-editor area, wires it with ``ui_gpu.input_capture`` and drives
it with simulated clicks and keys: click to focus, type, ``@`` suggestions
from the scene, Shift+Enter, Enter to send, Ctrl+A / Ctrl+C, Esc, typing
after Esc goes to the editor, then the same after opening a file.  Writes
``results.json`` and screenshots.  Run in a GUI session::

    bforartists --factory-startup --enable-event-simulate --python _misc/spikes/gpu_input_demo.py -- <out_dir>
"""

__all__ = ()

import importlib
import json
import os
import sys
import types

import bpy  # pylint: disable=import-error

OUT_DIR = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.getcwd()
os.makedirs(OUT_DIR, exist_ok=True)
_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_pkg = types.ModuleType("cwk")
_pkg.__path__ = [os.path.join(_REPO, "addon", "bfa_coworker")]
sys.modules["cwk"] = _pkg
style_mod = importlib.import_module("cwk.ui_gpu.style")
layout = importlib.import_module("cwk.ui_gpu.layout")
render = importlib.import_module("cwk.ui_gpu.render")
input_box = importlib.import_module("cwk.ui_gpu.input_box")
capture = importlib.import_module("cwk.ui_gpu.input_capture")

TREE = "DEMO_CoworkerInputTree"
SENT: list[str] = []
RESULT: dict = {}


def _provider(query):
    names = sorted(o.name for o in bpy.data.objects)
    return [input_box.Suggestion(n, "@" + n.replace(" ", "_"), o.type.lower())
            for n, o in ((n, bpy.data.objects[n]) for n in names) if n.lower().startswith(query.lower())]


BOX = input_box.InputBox(provider=_provider)


def _draw():
    if getattr(bpy.context.space_data, "tree_type", "") != TREE:
        return
    region = bpy.context.region
    style = style_mod.style_from_preferences(bpy.context.preferences)
    canvas = layout.Canvas(style, render.measure)
    canvas.fill(layout.Rect(0, 0, region.width, region.height), style.back)
    top = region.height - style.pad
    for text in SENT:
        top = canvas.wrapped(style.pad, top, region.width - 2 * style.pad, "Sent: " + text, style.text) - style.pad
    BOX.draw(canvas, layout.Rect(style.pad, style.pad, region.width - 2 * style.pad, region.height * 0.5))
    render.render(canvas.commands)


def _router(context, event):
    for area in context.window.screen.areas:
        if area.ui_type != TREE:
            continue
        for region in area.regions:
            if (region.type == "WINDOW" and region.x <= event.mouse_x < region.x + region.width
                    and region.y <= event.mouse_y < region.y + region.height):
                return BOX, area, region
    return None


def _on_action(_box, action, value):
    if action == "send":
        SENT.append(value)


class DEMO_Tree(bpy.types.NodeTree):
    bl_idname = TREE
    bl_label = "Coworker Input"
    bl_icon = "GHOST_ENABLED"


def _win():
    return bpy.context.window_manager.windows[0]


def _chat_area():
    areas = sorted(_win().screen.areas, key=lambda a: a.width * a.height, reverse=True)
    return areas[0]


def _setup_area():
    area = _chat_area()
    area.ui_type = TREE
    area.spaces.active.show_region_ui = False
    area.spaces.active.show_region_toolbar = False
    area.tag_redraw()


def _box_center():
    area = _chat_area()
    region = next(r for r in area.regions if r.type == "WINDOW")
    rect = BOX.rect
    return int(region.x + rect.x + rect.w * 0.3), int(region.y + rect.y + rect.h * 0.5)


def _sim(etype, value="PRESS", **kw):
    _win().event_simulate(type=etype, value=value, **kw)


def _key(etype, unicode="", **mods):
    x, y = _box_center()
    _sim(etype, "PRESS", x=x, y=y, unicode=unicode, **mods) if unicode else _sim(etype, "PRESS", x=x, y=y, **mods)
    _sim(etype, "RELEASE", x=x, y=y, **mods)


def _type(text):
    for ch in text:
        if ch.isalpha():
            _key(ch.upper(), unicode=ch)
        elif ch == " ":
            _key("SPACE", unicode=ch)
        else:   # "@" is Shift+2 on a US keyboard; the box only reads the character
            _key("TWO", unicode=ch, shift=True)


def _click():
    x, y = _box_center()
    _sim("MOUSEMOVE", "NOTHING", x=x, y=y)
    _sim("LEFTMOUSE", "PRESS", x=x, y=y)
    _sim("LEFTMOUSE", "RELEASE", x=x, y=y)


def _shoot(name):
    win = _win()
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.screen.screenshot(filepath=os.path.join(OUT_DIR, name + ".png"))


def _save():
    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(RESULT, fh, indent=2)


_STEP = {"n": 0}


def _tick():
    step = _STEP["n"]
    _STEP["n"] += 1
    try:
        if step == 0:
            RESULT["event_simulate"] = bool(getattr(bpy.app, "use_event_simulate", False))
            bpy.utils.register_class(DEMO_Tree)
            bpy.types.SpaceNodeEditor.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")
            capture.register()
            capture.set_host(_router, _on_action)
            _setup_area()
            return 1.0
        if step == 1:
            RESULT["operator_running"] = bool(capture._RUNNING)
            _click()
            _type("move @ca")
            return 0.8
        if step == 2:
            RESULT["focused_by_click"] = BOX.focused
            RESULT["suggestions"] = [s.label for s in BOX.suggestions]
            _shoot("1_typing_with_suggestions")
            _key("DOWN_ARROW")
            _key("TAB")
            _type("up")
            _key("RET", shift=True)
            _type("and fire")
            return 0.8
        if step == 3:
            RESULT["text_before_send"] = BOX.model.text
            _shoot("2_multi_line")
            _key("RET")
            return 0.6
        if step == 4:
            RESULT["sent"] = list(SENT)
            RESULT["box_empty_after_send"] = BOX.model.text == ""
            _type("hello")
            _key("A", ctrl=True)
            _key("C", ctrl=True)
            return 0.6
        if step == 5:
            RESULT["clipboard"] = bpy.context.window_manager.clipboard
            _key("ESC")
            return 0.6
        if step == 6:
            RESULT["blurred_by_esc"] = not BOX.focused
            BOX.model.clear()
            _type("q")
            return 0.6
        if step == 7:
            RESULT["keys_ignored_when_not_focused"] = BOX.model.text == ""
            _shoot("3_after_send")
            with bpy.context.temp_override(window=_win()):
                bpy.ops.wm.read_homefile()
            return 1.2
        if step == 8:
            _setup_area()
            return 1.5     # the watcher restarts the operator
        if step == 9:
            RESULT["operator_restarted_after_file_load"] = bool(capture._RUNNING)
            _click()
            _type("after load")
            return 0.8
        if step == 10:
            RESULT["typing_after_file_load"] = BOX.model.text
            _shoot("4_after_file_load")
            capture.unregister()
            RESULT["unregistered"] = True
    except Exception:  # pylint: disable=broad-exception-caught
        import traceback
        RESULT.setdefault("errors", []).append("step {:d}: {}".format(step, traceback.format_exc()[-900:]))
    _save()
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_tick, first_interval=2.0, persistent=True)
