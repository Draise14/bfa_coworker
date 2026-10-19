# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Visual check for the GPU UI toolkit (task "GPU 1"): a demo page drawn only
through ``ui_gpu``, photographed in the default theme, a light theme and at
1.5x zoom.  Not shipped.  Run in a GUI session::

    bforartists --factory-startup --python _misc/spikes/gpu_toolkit_demo.py -- <out_dir>
"""

__all__ = ()

import importlib
import os
import sys
import types

import bpy  # pylint: disable=import-error

OUT_DIR = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.getcwd()
os.makedirs(OUT_DIR, exist_ok=True)
_ADDON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "addon", "bfa_coworker")

# Import the toolkit without running the add-on's own __init__.
_pkg = types.ModuleType("cwk")
_pkg.__path__ = [os.path.abspath(_ADDON)]
sys.modules["cwk"] = _pkg
style_mod = importlib.import_module("cwk.ui_gpu.style")
layout = importlib.import_module("cwk.ui_gpu.layout")
render = importlib.import_module("cwk.ui_gpu.render")

STATE = {"zoom": 1.0, "mouse": None}
RESULTS = {}


def _draw():
    region = bpy.context.region
    style = style_mod.style_from_preferences(bpy.context.preferences, zoom=STATE["zoom"])
    canvas = layout.Canvas(style, render.measure, layout.InteractionState(mouse=STATE["mouse"]))
    u, pad = style.unit, style.pad
    page = layout.Rect(u, u, min(region.width - 2 * u, 34 * u), region.height - 2 * u)
    canvas.fill(page, style.back, style.panel_roundness * u * 0.5)
    canvas.stroke(page, style.outline, style.panel_roundness * u * 0.5)
    top = page.top - pad
    canvas.label(layout.Rect(page.x + pad, top - u, page.w, u), "Coworker toolkit demo", style.title, bold=True)
    top -= u + pad
    text = ("Everything on this page is drawn by ui_gpu: colours, roundness, outlines, font size "
            "and scale come from the active theme, so it should look like the rest of the "
            "interface in any theme and at any UI scale.")
    top = canvas.wrapped(page.x + pad, top, page.w - 2 * pad, text) - pad
    # A message box and a selected one.
    for selected, msg in ((False, "A reply in a box (wcol_box)."), (True, "Your message (selected colour).")):
        h = canvas.text_height(msg, page.w - 4 * pad) + 2 * pad
        r = layout.Rect(page.x + pad, top - h, page.w - 2 * pad, h)
        canvas.box(r, selected=selected)
        canvas.wrapped(r.x + pad, r.top - pad, r.w - 2 * pad, msg,
                       style.box.text_sel if selected else style.box.text)
        top = r.y - pad
    # A code block.
    code = ["light = bpy.data.lights.new('Fire', 'POINT')", "light.energy = 800"]
    h = len(code) * style.line_height + 2 * pad
    r = layout.Rect(page.x + pad, top - h, page.w - 2 * pad, h)
    canvas.box(r, colors=style.text_field)
    y = r.top - pad
    for line in code:
        y -= style.line_height
        canvas.text(r.x + pad, y + style.line_height * 0.3, line, style.text_field.text, font="mono")
    top = r.y - pad
    # Buttons: normal, hovered, active.
    bw = 7 * u
    for i, (wid, label, active) in enumerate((("copy", "Copy", False), ("run", "Run", False),
                                              ("send", "Send", True))):
        br = layout.Rect(page.x + pad + i * (bw + pad), top - u, bw, u)
        canvas.button(br, wid, label, active=active)
        if wid == "run":
            RESULTS["run_button_center"] = (br.x + br.w / 2, br.y + br.h / 2)
    top -= u + pad
    canvas.label(layout.Rect(page.x + pad, top - u, page.w, u), "A link in the theme's link colour", style.link)
    render.render(canvas.commands)


def _shoot(name):
    win = bpy.context.window_manager.windows[-1]
    path = os.path.join(OUT_DIR, name + ".png")
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.screen.screenshot(filepath=path)
    RESULTS.setdefault("shots", []).append(name + ".png")


_STEP = {"n": 0}
_HANDLE = []


def _tick():
    step = _STEP["n"]
    _STEP["n"] += 1
    try:
        if step == 0:
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.wm.window_new_main()   # a window without the splash screen
            return 1.0
        if step == 1:
            _HANDLE.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL"))
            win = bpy.context.window_manager.windows[-1]
            area = max(win.screen.areas, key=lambda a: a.width * a.height)
            area.ui_type = "VIEW_3D"
            # Hover over the Run button (region coordinates of the 3D View main region).
            STATE["area"] = area
            return 0.8
        if step == 2:
            area = STATE["area"]
            if "run_button_center" in RESULTS:
                STATE["mouse"] = RESULTS["run_button_center"]
            area.tag_redraw()
            return 0.6
        if step == 3:
            _shoot("1_default_theme")
            presets = bpy.utils.preset_paths("interface_theme")
            light = next((os.path.join(p, f) for p in presets for f in os.listdir(p)
                          if f.lower().startswith("blender_light")), None)
            RESULTS["light_preset"] = os.path.basename(light) if light else None
            if light:
                bpy.ops.script.execute_preset(filepath=light, menu_idname="USERPREF_MT_interface_theme_presets")
            STATE["area"].tag_redraw()
            return 0.8
        if step == 4:
            _shoot("2_light_theme")
            STATE["zoom"] = 1.5
            STATE["mouse"] = None
            STATE["area"].tag_redraw()
            return 0.8
        if step == 5:
            _shoot("3_light_theme_zoom_1_5")
    except Exception:  # pylint: disable=broad-exception-caught
        import traceback
        RESULTS.setdefault("errors", []).append(traceback.format_exc()[-800:])
    import json
    RESULTS.pop("run_button_center", None)
    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, indent=2)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_tick, first_interval=2.0, persistent=True)
