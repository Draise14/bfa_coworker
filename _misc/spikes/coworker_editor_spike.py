# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Spike for issue #26 / board "Editors 1": which editor can host the Coworker chat?

Throwaway prototype, not shipped.  Run in a GUI session::

    bforartists --factory-startup --python _misc/spikes/coworker_editor_spike.py -- <out_dir>

It registers three candidate hosts, arranges one window with them, takes
screenshots, writes findings to ``<out_dir>/findings.json`` and quits.

1. Preferences editor: a Python panel in the center (``WINDOW``) region,
   shown only in areas flagged as "Coworker" (area-scoped poll).
2. Node editor with a custom ``NodeTree`` type "Coworker": its own entry
   and icon in the Editor Type menu; chat in the sidebar (``UI``) region.
3. Text Editor: chat in the sidebar (today's approach), for comparison.
"""

__all__ = ()

import json
import os
import sys
import time

import bpy  # pylint: disable=import-error

OUT_DIR = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.getcwd()
os.makedirs(OUT_DIR, exist_ok=True)

# Areas that should show the Coworker center panel (by area pointer).
_COWORKER_AREAS: set[int] = set()
FINDINGS: dict = {"version": bpy.app.version_string}


def _draw_fake_chat(layout, where):
    """Stand-in for the chat UI: native widgets only."""
    box = layout.box()
    row = box.row()
    row.label(text="Turn 1", icon="CHECKMARK")
    row.label(text="| 0:12")
    box.label(text="You:", icon="USER")
    box.label(text="Make a low-poly campfire on a ground plane")
    ws = box.box()
    ws.label(text="Workshop", icon="PACKAGE")
    ws.label(text="execute_blender_code  ok", icon="TOOL_SETTINGS")
    box.separator()
    box.label(text="* Coworker:", icon="GHOST_ENABLED")
    box.label(text="Added a ground plane, 5 logs and a warm point light.")
    col = layout.column()
    col.prop(bpy.context.window_manager, "bfacw_spike_input", text="")
    row = col.row(align=True)
    row.operator("wm.context_toggle", text="Send", icon="PLAY")
    row.operator("wm.context_toggle", text="Stop", icon="CANCEL")
    layout.label(text="Host: " + where, icon="INFO")


# -- 1. Preferences editor, center region ------------------------------------------

class SPIKE_PT_prefs_center(bpy.types.Panel):
    bl_label = "Coworker"
    bl_idname = "SPIKE_PT_prefs_center"
    bl_space_type = "PREFERENCES"
    bl_region_type = "WINDOW"
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        area = context.area
        return area is not None and area.as_pointer() in _COWORKER_AREAS

    def draw(self, context):
        _draw_fake_chat(self.layout, "Preferences editor center (WINDOW)")


# -- 2. Node editor with a custom tree type ------------------------------------------

class SPIKE_CoworkerTree(bpy.types.NodeTree):
    bl_idname = "SPIKE_CoworkerTree"
    bl_label = "Coworker"
    bl_icon = "GHOST_ENABLED"


class SPIKE_PT_node_sidebar(bpy.types.Panel):
    bl_label = "Coworker"
    bl_idname = "SPIKE_PT_node_sidebar"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Coworker"

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "tree_type", "") == "SPIKE_CoworkerTree"

    def draw(self, context):
        _draw_fake_chat(self.layout, "Node editor 'Coworker' type, sidebar")


# -- 3. Text editor sidebar (today) --------------------------------------------------

class SPIKE_PT_text_sidebar(bpy.types.Panel):
    bl_label = "Coworker"
    bl_idname = "SPIKE_PT_text_sidebar"
    bl_space_type = "TEXT_EDITOR"
    bl_region_type = "UI"
    bl_category = "Coworker"

    def draw(self, context):
        _draw_fake_chat(self.layout, "Text Editor sidebar")


CLASSES = (SPIKE_PT_prefs_center, SPIKE_CoworkerTree, SPIKE_PT_node_sidebar, SPIKE_PT_text_sidebar)


def _register():
    bpy.types.WindowManager.bfacw_spike_input = bpy.props.StringProperty(
        name="Message", default="Ask Coworker...")
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    # Is the custom tree type offered as an editor type?
    enum = bpy.types.Area.bl_rna.properties["ui_type"].enum_items
    FINDINGS["editor_type_menu_has_coworker"] = any(
        it.identifier == "SPIKE_CoworkerTree" for it in enum)
    FINDINGS["editor_type_items_sample"] = [it.identifier for it in enum][:40]


def _areas():
    return list(bpy.context.window.screen.areas)


def _setup():
    """Turn the default layout into: Preferences | Node(Coworker) | Text."""
    areas = sorted(_areas(), key=lambda a: a.width * a.height, reverse=True)
    big = areas[0]
    hosts = areas[:3] if len(areas) >= 3 else areas
    FINDINGS["areas_before"] = [a.type for a in _areas()]
    try:
        hosts[0].ui_type = "PREFERENCES"
        _COWORKER_AREAS.add(hosts[0].as_pointer())
    except Exception as ex:  # pylint: disable=broad-exception-caught
        FINDINGS["prefs_error"] = str(ex)
    if len(hosts) > 1:
        try:
            hosts[1].ui_type = "SPIKE_CoworkerTree"
            for region in hosts[1].regions:
                if region.type == "UI":
                    hosts[1].spaces.active.show_region_ui = True
            FINDINGS["node_area_ui_type"] = hosts[1].ui_type
        except Exception as ex:  # pylint: disable=broad-exception-caught
            FINDINGS["node_error"] = str(ex)
    if len(hosts) > 2:
        hosts[2].ui_type = "TEXT_EDITOR"
        hosts[2].spaces.active.show_region_ui = True
    FINDINGS["areas_after"] = [a.ui_type for a in _areas()]
    FINDINGS["big_area"] = big.ui_type
    return 0.5


_STEP = {"n": 0}


def _shoot(window, name):
    path = os.path.join(OUT_DIR, name + ".png")
    with bpy.context.temp_override(window=window, screen=window.screen):
        bpy.ops.screen.screenshot(filepath=path)
    return path


def _node_area(window):
    return next((a for a in window.screen.areas if a.ui_type == "SPIKE_CoworkerTree"), None)


def _tick():
    step = _STEP["n"]
    _STEP["n"] += 1
    wm = bpy.context.window_manager
    try:
        if step == 0:
            _register()
            return 0.5
        if step == 1:
            return _setup()
        if step == 2:
            # Editor Type menu: the dynamic enum resolves our tree type's label.
            area = _node_area(bpy.context.window)
            FINDINGS["editor_menu_label"] = bpy.types.UILayout.enum_item_name(
                area, "ui_type", "SPIKE_CoworkerTree") if area else ""
            FINDINGS["editor_menu_icon"] = bpy.types.UILayout.enum_item_icon(
                area, "ui_type", "SPIKE_CoworkerTree") if area else 0
            # A fresh main window has no splash screen over it.
            with bpy.context.temp_override(window=bpy.context.window):
                bpy.ops.wm.window_new_main()
            return 1.0
        if step == 3:
            win = wm.windows[-1]
            area = _node_area(win)
            FINDINGS["new_window_areas"] = [a.ui_type for a in win.screen.areas]
            if area is not None:
                space = area.spaces.active
                space.show_region_ui = True
                for region in area.regions:
                    if region.type == "UI":
                        try:
                            region.active_panel_category = "Coworker"
                            FINDINGS["sidebar_tab_set"] = True
                        except Exception as ex:  # pylint: disable=broad-exception-caught
                            FINDINGS["sidebar_tab_error"] = str(ex)
                        FINDINGS["sidebar_width_px"] = region.width
            return 0.7
        if step == 4:
            FINDINGS["shot_node_sidebar"] = _shoot(wm.windows[-1], "2_coworker_tab_no_splash")
            area = _node_area(wm.windows[-1])
            if area is not None:
                area.spaces.active.show_region_header = False
                FINDINGS["header_hidden"] = True
            # Flag this window's Preferences area as "Coworker" too: does our
            # panel get the center to itself, or do the native sections stay?
            for prefs_area in wm.windows[-1].screen.areas:
                if prefs_area.ui_type == "PREFERENCES":
                    _COWORKER_AREAS.add(prefs_area.as_pointer())
                    prefs_area.tag_redraw()
            return 0.7
        if step == 5:
            FINDINGS["shot_no_header"] = _shoot(wm.windows[-1], "3_coworker_no_header")
    except Exception as ex:  # pylint: disable=broad-exception-caught
        FINDINGS.setdefault("errors", []).append("step {:d}: {}".format(step, ex))
    with open(os.path.join(OUT_DIR, "findings.json"), "w", encoding="utf-8") as fh:
        json.dump(FINDINGS, fh, indent=2)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_tick, first_interval=2.0)
