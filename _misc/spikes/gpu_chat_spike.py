# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Spike "GPU 0": can a GPU-drawn, themed chat replace panel layouts?

Throwaway prototype, not shipped.  Run in a GUI session with event
simulation, so the script can type into the prototype::

    bforartists --factory-startup --enable-event-simulate \\
        --python _misc/spikes/gpu_chat_spike.py -- <out_dir>

It builds a Coworker node-editor area (center chat), a 3D View whose
"Coworker" sidebar tab is painted by the same module (compact chat), a
native UIList and a toolshelf panel (fallbacks), then simulates typing,
Enter / Shift+Enter, a node-editor shortcut, scrolling and zoom, opens a
new file, and writes screenshots + ``findings.json``.
"""

__all__ = ()

import json
import os
import sys
import time

import blf  # pylint: disable=import-error
import bpy  # pylint: disable=import-error
import gpu  # pylint: disable=import-error
from gpu_extras.batch import batch_for_shader  # pylint: disable=import-error

OUT_DIR = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.getcwd()
os.makedirs(OUT_DIR, exist_ok=True)
FINDINGS: dict = {"version": bpy.app.version_string}
TREE = "SPIKE_GpuChatTree"

# -- Chat model (stand-in for chat_state output) ------------------------------------------

CHAT = {
    "messages": [
        {"role": "user", "text": "Make a low-poly campfire on a ground plane."},
        {"role": "assistant", "text": "I'll add a ground plane first, then five logs in a "
                                      "ring and a warm point light in the middle."},
        {"role": "tool", "text": "execute_blender_code  ok  (0.4 s)"},
        {"role": "assistant", "text": "Done. Here is the light I used:",
         "code": "light = bpy.data.lights.new('Fire', 'POINT')\nlight.energy = 800\nlight.color = (1.0, 0.55, 0.2)"},
    ],
    "input": "",
    "cursor": 0,
    "scroll": 0.0,      # pixels scrolled up from the bottom
    "zoom": 1.0,
    "focused": False,
    "sent": [],
}
_STATS = {"draws": 0, "draw_ms": 0.0, "modal_events": 0, "consumed": 0}


# -- Theme + scale -------------------------------------------------------------------------

def _theme():
    prefs = bpy.context.preferences
    ui = prefs.themes[0].user_interface
    scale = prefs.view.ui_scale * prefs.system.pixel_size
    box, text_w, reg = ui.wcol_box, ui.wcol_text, ui.wcol_regular
    back = getattr(ui, "panel_back", None) or (0.18, 0.18, 0.18, 1.0)
    return {
        "scale": scale * CHAT["zoom"],
        "unit": 20.0 * scale * CHAT["zoom"],
        "back": tuple(back) if len(back) == 4 else (*back, 1.0),
        "box": tuple(box.inner), "box_outline": tuple(box.outline), "text": tuple(box.text) + (1.0,),
        "user": tuple(reg.inner_sel), "user_text": tuple(reg.text_sel) + (1.0,),
        "field": tuple(text_w.inner), "field_text": tuple(text_w.text) + (1.0,),
        "round": float(box.roundness),
    }


# -- Drawing primitives -----------------------------------------------------------------------

_SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")


def _rounded(x, y, w, h, r, color):
    """Filled rounded rectangle as triangles (no TRI_FAN: not on every backend)."""
    import math
    r = max(0.0, min(r, w / 2, h / 2))
    steps = 6
    pts = []
    for cx, cy, a0 in ((x + w - r, y + h - r, 0), (x + r, y + h - r, 90),
                       (x + r, y + r, 180), (x + w - r, y + r, 270)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90 * i / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    center = (x + w / 2, y + h / 2)
    tris = []
    for i in range(len(pts)):
        tris += [center, pts[i], pts[(i + 1) % len(pts)]]
    batch = batch_for_shader(_SHADER, "TRIS", {"pos": tris})
    _SHADER.bind()
    _SHADER.uniform_float("color", color)
    batch.draw(_SHADER)


def _wrap(text, width, font, size):
    blf.size(font, size)
    lines = []
    for para in text.split("\n"):
        words, line = para.split(" "), ""
        for word in words:
            trial = word if not line else line + " " + word
            if blf.dimensions(font, trial)[0] <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


_FONTS = {}


def _mono_font():
    if "mono" not in _FONTS:
        _FONTS["mono"] = 0
        fonts = bpy.utils.system_resource("DATAFILES", path="fonts")
        for name in sorted(os.listdir(fonts)) if fonts and os.path.isdir(fonts) else []:
            if "mono" in name.lower():
                fid = blf.load(os.path.join(fonts, name))
                if fid != -1:
                    _FONTS["mono"] = fid
                    FINDINGS["mono_font"] = name
                    break
    return _FONTS["mono"]


# -- The chat view (one module, any width) ----------------------------------------------------

def draw_chat(x0, y0, width, height, compact):
    t0 = time.perf_counter()
    th = _theme()
    u, pad = th["unit"], th["unit"] * 0.4
    size = 11 * th["scale"]
    gpu.state.blend_set("ALPHA")
    _rounded(x0, y0, width, height, 0, th["back"])

    # Input field pinned to the bottom (grows with its lines, max 5).
    in_lines = _wrap(CHAT["input"] or "Ask Coworker...  (Enter to send, Shift+Enter for a new line)",
                     width - 4 * pad, 0, size)[-5:]
    in_h = len(in_lines) * size * 1.4 + 2 * pad
    _rounded(x0 + pad, y0 + pad, width - 2 * pad, in_h, th["round"] * u * 0.5,
             th["user"] if CHAT["focused"] else th["field"])
    blf.size(0, size)
    blf.color(0, *(th["field_text"][:3]), 1.0 if CHAT["input"] else 0.5)
    for i, line in enumerate(in_lines):
        blf.position(0, x0 + 2 * pad, y0 + pad + in_h - pad - (i + 1) * size * 1.3, 0)
        blf.draw(0, line)

    # Messages, newest at the bottom, scrolled from the bottom up; clipped.
    top_of_input = y0 + in_h + 2 * pad
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(int(x0), int(top_of_input), int(width), int(y0 + height - top_of_input))
    y = top_of_input + pad - CHAT["scroll"]
    mono = _mono_font()
    for msg in reversed(CHAT["messages"]):
        bubble_w = width - 2 * pad - (0 if compact else u * 2)
        lines = _wrap(msg["text"], bubble_w - 2 * pad, 0, size)
        code_lines = msg.get("code", "").split("\n") if msg.get("code") else []
        h = (len(lines) + len(code_lines)) * size * 1.4 + 2 * pad + (pad if code_lines else 0)
        if y > y0 + height:
            break
        bx = x0 + pad + (u * 2 if msg["role"] == "user" and not compact else 0)
        color = th["user"] if msg["role"] == "user" else th["box"]
        if msg["role"] == "tool":
            color = (*th["box"][:3], th["box"][3] * 0.6)
        _rounded(bx, y, bubble_w, h, th["round"] * u * 0.5, color)
        text_col = th["user_text"] if msg["role"] == "user" else th["text"]
        cy = y + h - pad
        blf.color(0, *text_col)
        for line in lines:
            cy -= size * 1.4
            blf.position(0, bx + pad, cy + size * 0.3, 0)
            blf.draw(0, line)
        if code_lines:
            cy -= pad * 0.5
            code_h = len(code_lines) * size * 1.4 + pad * 0.5
            _rounded(bx + pad * 0.5, cy - code_h, bubble_w - pad, code_h, 3 * th["scale"], th["field"])
            blf.size(mono, size)
            blf.color(mono, *th["field_text"])
            for line in code_lines:
                cy -= size * 1.4
                blf.position(mono, bx + pad, cy + size * 0.3, 0)
                blf.draw(mono, line)
            blf.size(0, size)
        y += h + pad
    gpu.state.scissor_test_set(False)
    gpu.state.blend_set("NONE")
    _STATS["draws"] += 1
    _STATS["draw_ms"] += (time.perf_counter() - t0) * 1000


def _draw_center():
    space = bpy.context.space_data
    if getattr(space, "tree_type", "") != TREE:
        return
    region = bpy.context.region
    draw_chat(0, 0, region.width, region.height, compact=False)


def _draw_sidebar():
    region = bpy.context.region
    if getattr(region, "active_panel_category", "") != "Coworker":
        return
    tabs = 24 * bpy.context.preferences.view.ui_scale  # leave the tab strip visible
    draw_chat(0, 0, region.width - tabs, region.height, compact=True)


# -- Input: one modal operator per window --------------------------------------------------------

def _hit_chat(context, event):
    """The chat area/region under the mouse, or None."""
    for area in context.window.screen.areas:
        if not (area.x <= event.mouse_x < area.x + area.width and area.y <= event.mouse_y < area.y + area.height):
            continue
        if area.ui_type == TREE:
            return area
        if area.type == "VIEW_3D":
            for region in area.regions:
                if (region.type == "UI" and getattr(region, "active_panel_category", "") == "Coworker"
                        and region.x <= event.mouse_x < region.x + region.width):
                    return area
    return None


class SPIKE_OT_chat_input(bpy.types.Operator):
    bl_idname = "spike.chat_input"
    bl_label = "Spike chat input"

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        FINDINGS["modal_started"] = True
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        _STATS["modal_events"] += 1
        area = _hit_chat(context, event)
        CHAT["focused"] = area is not None
        if area is None or event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "TIMER", "TIMER_REPORT"}:
            return {"PASS_THROUGH"}
        consumed = self._handle(event)
        if consumed:
            _STATS["consumed"] += 1
            area.tag_redraw()
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    @staticmethod
    def _handle(event):
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            if event.ctrl:
                CHAT["zoom"] = max(0.6, min(2.0, CHAT["zoom"] + (0.1 if event.type == "WHEELUPMOUSE" else -0.1)))
            else:
                CHAT["scroll"] = max(0.0, CHAT["scroll"] + (40 if event.type == "WHEELUPMOUSE" else -40))
            return True
        if event.value != "PRESS":
            return event.type not in {"LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE"}
        if event.type in {"RET", "NUMPAD_ENTER"}:
            if event.shift:
                CHAT["input"] += "\n"
            elif CHAT["input"].strip():
                CHAT["sent"].append(CHAT["input"])
                CHAT["messages"].append({"role": "user", "text": CHAT["input"]})
                CHAT["input"] = ""
                CHAT["scroll"] = 0.0          # follow the newest message
            return True
        if event.type == "BACK_SPACE":
            CHAT["input"] = CHAT["input"][:-1]
            return True
        if event.type == "V" and event.ctrl:
            CHAT["input"] += bpy.context.window_manager.clipboard
            return True
        if event.unicode and not event.ctrl and not event.alt:
            CHAT["input"] += event.unicode
            return True
        return event.type not in {"LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE", "ESC"}


# -- Fallbacks: native list + toolshelf panel ------------------------------------------------------

class SPIKE_LineItem(bpy.types.PropertyGroup):
    text: bpy.props.StringProperty()


class SPIKE_UL_lines(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.text)


class SPIKE_PT_list_fallback(bpy.types.Panel):
    bl_label = "Native list fallback"
    bl_idname = "SPIKE_PT_list_fallback"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Coworker"

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "tree_type", "") == TREE

    def draw(self, context):
        wm = context.window_manager
        self.layout.template_list("SPIKE_UL_lines", "", wm, "spike_lines", wm, "spike_line_index", rows=6)
        self.layout.prop(wm, "spike_native_input", text="")


class SPIKE_PT_coworker_tab(bpy.types.Panel):
    """Empty panel that creates the 3D View's Coworker tab (painted over by the GPU chat)."""
    bl_label = "Coworker"
    bl_idname = "SPIKE_PT_coworker_tab"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Coworker"
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        self.layout.label(text="")


class SPIKE_PT_toolshelf(bpy.types.Panel):
    bl_label = "Coworker tools"
    bl_idname = "SPIKE_PT_toolshelf"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "TOOLS"

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "tree_type", "") == TREE

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator("wm.context_toggle", text="Explain", icon="QUESTION")
        col.operator("wm.context_toggle", text="Templates", icon="PRESET")
        col.operator("wm.context_toggle", text="Wizard", icon="SHADERFX")


class SPIKE_ChatTree(bpy.types.NodeTree):
    bl_idname = TREE
    bl_label = "Coworker"
    bl_icon = "GHOST_ENABLED"


CLASSES = (SPIKE_LineItem, SPIKE_UL_lines, SPIKE_PT_list_fallback, SPIKE_PT_coworker_tab,
           SPIKE_PT_toolshelf, SPIKE_ChatTree, SPIKE_OT_chat_input)
_HANDLERS = []


def _register():
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            FINDINGS.setdefault("register_errors", []).append("{}: {}".format(cls.__name__, ex))
    wm = bpy.types.WindowManager
    wm.spike_lines = bpy.props.CollectionProperty(type=SPIKE_LineItem)
    wm.spike_line_index = bpy.props.IntProperty()
    wm.spike_native_input = bpy.props.StringProperty(default="Native input")
    _HANDLERS.append((bpy.types.SpaceNodeEditor, bpy.types.SpaceNodeEditor.draw_handler_add(
        _draw_center, (), "WINDOW", "POST_PIXEL")))
    _HANDLERS.append((bpy.types.SpaceView3D, bpy.types.SpaceView3D.draw_handler_add(
        _draw_sidebar, (), "UI", "POST_PIXEL")))
    lines = bpy.context.window_manager.spike_lines
    for i in range(60):
        lines.add().text = "Line {:d} of the native list fallback".format(i + 1)


# -- Script ---------------------------------------------------------------------------------------

def _win():
    return bpy.context.window_manager.windows[-1]


def _areas():
    return list(_win().screen.areas)


def _area(ui_type):
    return next((a for a in _areas() if a.ui_type == ui_type), None)


def _setup():
    """Big area -> Coworker editor (center chat); 2nd biggest -> 3D View (compact chat)."""
    areas = sorted(_areas(), key=lambda a: a.width * a.height, reverse=True)
    node, view3d = areas[0], areas[1]
    node.ui_type = TREE
    node.spaces.active.show_region_ui = True
    node.spaces.active.show_region_toolbar = True
    view3d.ui_type = "VIEW_3D"
    view3d.spaces.active.show_region_ui = True
    FINDINGS["center_area_px"] = [node.width, node.height]
    FINDINGS["compact_area_px"] = [view3d.width, view3d.height]
    FINDINGS["areas"] = [a.ui_type for a in _areas()]
    # UIList: does setting the active index scroll the list to the newest row?
    bpy.context.window_manager.spike_line_index = len(bpy.context.window_manager.spike_lines) - 1


def _select_coworker_tabs():
    """Open the Coworker sidebar tabs.  Editable only after a region has drawn."""
    done = True
    for area in _areas():
        if area.type not in {"VIEW_3D", "NODE_EDITOR"}:
            continue
        for region in area.regions:
            if region.type != "UI":
                continue
            try:
                region.active_panel_category = "Coworker"
            except AttributeError as ex:
                FINDINGS["tab_set_note"] = str(ex)
                done = False
            if area.type == "NODE_EDITOR":
                FINDINGS["node_sidebar_width"] = region.width
        area.tag_redraw()
    return done


def _sim(window, etype, value="PRESS", **kw):
    window.event_simulate(type=etype, value=value, **kw)


def _shoot(name):
    win = _win()
    path = os.path.join(OUT_DIR, name + ".png")
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.screen.screenshot(filepath=path)
    return name + ".png"


_STEP = {"n": 0}


def _save():
    FINDINGS["stats"] = dict(_STATS)
    with open(os.path.join(OUT_DIR, "findings.json"), "w", encoding="utf-8") as fh:
        json.dump(FINDINGS, fh, indent=2)


def _tick():
    step = _STEP["n"]
    _STEP["n"] += 1
    win = _win()
    try:
        if step == 0:
            FINDINGS["event_simulate"] = bool(getattr(bpy.app, "use_event_simulate", False))
            _register()
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.wm.window_new_main()
            return 1.0
        if step == 1:
            _setup()
            with bpy.context.temp_override(window=win):
                bpy.ops.spike.chat_input("INVOKE_DEFAULT")
            return 0.7
        if step == 2:
            if _select_coworker_tabs():
                return 0.7
            FINDINGS["tab_retry"] = FINDINGS.get("tab_retry", 0) + 1
            if FINDINGS["tab_retry"] < 6:
                _STEP["n"] = 2   # retry after another redraw
            return 0.5
        if step == 3:
            FINDINGS["shot_initial"] = _shoot("1_initial")
            node = _area(TREE)
            cx, cy = node.x + node.width // 2, node.y + node.height // 2
            _sim(win, "MOUSEMOVE", "NOTHING", x=cx, y=cy)
            for ch in "Hello":
                _sim(win, ch.upper(), unicode=ch, x=cx, y=cy, shift=ch.isupper())
                _sim(win, ch.upper(), "RELEASE", x=cx, y=cy)
            _sim(win, "RET", shift=True, x=cx, y=cy)
            for ch in "world":
                _sim(win, ch.upper(), unicode=ch, x=cx, y=cy)
            FINDINGS["input_before_enter"] = CHAT["input"]
            sidebar_before = node.spaces.active.show_region_ui
            _sim(win, "N", unicode="n", x=cx, y=cy)   # node editor: toggle sidebar
            _sim(win, "BACK_SPACE", x=cx, y=cy)        # remove the typed 'n'
            FINDINGS["shortcut_N_blocked"] = node.spaces.active.show_region_ui == sidebar_before
            _sim(win, "RET", x=cx, y=cy)
            FINDINGS["sent"] = list(CHAT["sent"])
            return 0.5
        if step == 4:
            FINDINGS["shot_after_typing"] = _shoot("2_after_typing")
            for i in range(200):
                CHAT["messages"].append({"role": "assistant" if i % 2 else "user",
                                         "text": "Message {:d}: a longer line of text so it wraps "
                                                 "inside the bubble at narrower widths.".format(i)})
            _STATS["draws"], _STATS["draw_ms"] = 0, 0.0
            for area in _areas():
                area.tag_redraw()
            return 1.0
        if step == 5:
            node = _area(TREE)
            cx, cy = node.x + node.width // 2, node.y + node.height // 2
            for _ in range(6):
                _sim(win, "WHEELUPMOUSE", x=cx, y=cy)
            FINDINGS["scroll_after_wheel"] = CHAT["scroll"]
            _sim(win, "WHEELUPMOUSE", x=cx, y=cy, ctrl=True)
            _sim(win, "WHEELUPMOUSE", x=cx, y=cy, ctrl=True)
            FINDINGS["zoom_after_ctrl_wheel"] = round(CHAT["zoom"], 2)
            return 0.7
        if step == 6:
            FINDINGS["shot_scrolled_zoomed"] = _shoot("3_200_messages_scrolled_zoomed")
            FINDINGS["avg_draw_ms_200_messages"] = round(_STATS["draw_ms"] / max(1, _STATS["draws"]), 2)
            FINDINGS["draws_measured"] = _STATS["draws"]
            CHAT["scroll"], CHAT["zoom"] = 0.0, 1.0
            _save()
            # File load: does the modal input survive?
            before = _STATS["modal_events"]
            with bpy.context.temp_override(window=win):
                bpy.ops.wm.read_homefile()
            FINDINGS["modal_events_before_load"] = before
            return 1.0
        if step == 7:
            _STATS["events_at_load"] = _STATS["modal_events"]
            _sim(win, "MOUSEMOVE", "NOTHING", x=100, y=100)
            _sim(win, "MOUSEMOVE", "NOTHING", x=120, y=110)
            return 0.7
        if step == 8:
            # Read one step later: simulated events are processed asynchronously.
            FINDINGS["modal_alive_after_file_load"] = _STATS["modal_events"] > _STATS["events_at_load"]
    except Exception as ex:  # pylint: disable=broad-exception-caught
        import traceback
        FINDINGS.setdefault("errors", []).append("step {:d}: {}".format(step, traceback.format_exc()[-900:]))
    _save()
    bpy.ops.wm.quit_blender()
    return None


# persistent: a file load removes non-persistent timers (found in this spike).
bpy.app.timers.register(_tick, first_interval=2.0, persistent=True)
