# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Reframe layouts in the Sequencer (#606): the operators, the sidebar / toolshelf box, the Strip-menu
entries and the agent's ``reframe_strips`` tool.

``bfacw.reframe_layout`` gives the selected picture strips one of the layouts of
``wand/reframe_layout.py`` (Full Frame, Blur Fill, Tracked, three splits), replacing the earlier
layout; ``bfacw.reframe_swap`` exchanges a split's top and bottom framing; ``bfacw.reframe_reset``
puts a part back to its default crop.  The logic is ``wand/reframe_apply.py`` (bpy-free); this file
is its Blender backend (strip duplicates, the Reframe modifier, Follow Subject) and the UI.

A split is two ordinary strips of the same source: the strip itself (top) and a duplicate on the next
free channel (bottom, same timing, no audio), each with crop and transform.  Captions, their word
strips and the audio are never touched.  Remove Silences and Edit by Transcript cut every strip at a
level alike, so both parts are cut together (``wand/reframe_apply.find_partner`` pairs the pieces).
"""

from __future__ import annotations

__all__ = (
    "BFACW_MT_reframe_layout",
    "BFACW_OT_reframe_layout",
    "BFACW_OT_reframe_reset",
    "BFACW_OT_reframe_swap",
    "BlenderBackend",
    "register",
    "tool_reframe_strips",
    "unregister",
)

import os
import re
from typing import Any

import bpy  # pylint: disable=import-error
from bpy.props import EnumProperty  # pylint: disable=import-error
from bpy.types import Menu, Operator, Panel  # pylint: disable=import-error

from . import reframe
from .shared import icon as safe_icon
from .wand import icons as wand_icons
from .wand import reframe_apply as ra
from .wand import reframe_layout as rl
from .wand import scene as _seq
from .wand import sequencer as _wand

CATEGORY = "Coworker"
PANEL_LABEL = "Reframe"
SWAP_ICON, RESET_ICON = "ARROW_LEFTRIGHT", "LOOP_BACK"
SWAP_TIP = ("Swap the top and bottom of a split: what the top part shows goes to the bottom and the other way "
            "round.\nWorks without the agent started.")
RESET_TIP = ("Put the selected strips back to their layout's default framing: a split part to the centre of the "
             "picture, a Full Frame strip centred with no keys.\nWorks without the agent started.")
# Strip properties a duplicate takes over (the ones a part of a split should look the same in).
_COPIED = ("blend_type", "blend_alpha", "color_multiply", "color_saturation", "mute", "use_flip_x", "use_flip_y",
           "alpha_mode", "multiply_alpha", "use_deinterlace", "use_float", "color_tag")


# -- the Blender backend ---------------------------------------------------------------------

def _copy_props(source: Any, target: Any) -> None:
    for name in _COPIED:
        try:
            setattr(target, name, getattr(source, name))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass
    try:
        target.colorspace_settings.name = source.colorspace_settings.name
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass


class BlenderBackend(ra.Backend):
    """``reframe_apply.Backend`` on the real Sequencer."""

    def source_size(self, strip: Any) -> tuple[int, int]:
        try:
            return reframe.source_size(strip)
        except reframe.ReframeError as ex:
            raise ra.LayoutError(str(ex)) from None

    def canvas_size(self, scene: Any) -> tuple[int, int]:
        return reframe.canvas_size(scene)

    def duplicate(self, strips: Any, strip: Any, name: str, channel: int) -> Any:
        start = int(round(float(strip.content_start)))
        try:
            if strip.type == "MOVIE":
                twin = strips.new_movie(name, bpy.path.abspath(strip.filepath), channel, start)
            elif strip.type == "IMAGE":
                first = bpy.path.abspath(os.path.join(strip.directory, strip.elements[0].filename))
                twin = strips.new_image(name, first, channel, start)
                for element in list(strip.elements)[1:]:
                    twin.elements.append(element.filename)
            elif strip.type == "SCENE":
                twin = strips.new_scene(name, strip.scene, channel, start)
                twin.scene_input = strip.scene_input
                twin.scene_camera = strip.scene_camera
            else:
                raise ra.LayoutError("'{:s}' can't be split".format(strip.name))
        except (RuntimeError, TypeError, ValueError) as ex:
            raise ra.LayoutError("Couldn't make the second part of '{:s}': {!s}".format(strip.name, ex)) from None
        twin.content_start = strip.content_start
        left, right = int(strip.left_handle), int(strip.right_handle)
        if int(twin.right_handle) < right:
            twin.right_handle = right
        twin.left_handle = left
        twin.right_handle = right
        _copy_props(strip, twin)
        twin.channel = channel
        return twin

    def remove(self, strips: Any, doomed: list) -> None:
        from .wand.prefetch import remove_strips
        remove_strips(strips, doomed)

    def __init__(self, scene: Any = None) -> None:
        self.scene = scene

    def modifier_state(self, strip: Any) -> dict | None:
        mod = reframe.find_modifier(strip)
        if mod is None:
            return None
        keyed = self.scene is not None and reframe.fcurve_for(self.scene, mod, "Offset X") is not None
        return {"blur": bool(reframe.get_inputs(mod).get("Blur Fill")), "keyed": keyed}

    def clear_modifier(self, scene: Any, strip: Any) -> None:
        mod = reframe.find_modifier(strip)
        if mod is not None:
            reframe.clear_keys(scene, mod)
            strip.modifiers.remove(mod)

    def make_full(self, scene: Any, strip: Any, mode: str) -> None:
        try:
            reframe.reframe_strip(scene, strip, mode, reset=True)
        except reframe.ReframeError as ex:
            raise ra.LayoutError(str(ex)) from None

    def reset_full(self, scene: Any, strip: Any) -> None:
        reframe.apply_reframe(scene, strip, "RESET_FRAMING")

    def start_tracking(self, scene: Any, strips: list) -> str:
        try:
            return reframe.start_follow(scene, strips)
        except reframe.ReframeError as ex:
            raise ra.LayoutError(str(ex)) from None
        except Exception as ex:  # pylint: disable=broad-exception-caught  # reframe_follow.Unavailable
            raise ra.LayoutError(str(ex)) from None


# -- shared by the operators and the tool -----------------------------------------------------

def _level_strips(scene: Any) -> Any:
    strips, _channels = _wand._level(scene.sequence_editor)  # pylint: disable=protected-access
    return strips


def _selected_names(scene: Any) -> list[str]:
    return [s.name for s in _level_strips(scene) if s.select and s.type in ra.PICTURE_TYPES]


def _active_layout(context: Any) -> str:
    """The layout of the active strip (its tag, or Make Vertical's modifier), else ``""``."""
    scene = _seq.sequencer_scene(context)
    ed = getattr(scene, "sequence_editor", None)
    active = getattr(ed, "active_strip", None) if ed is not None else None
    if active is None or active.type not in ra.PICTURE_TYPES:
        return ""
    try:
        return ra.current_layout(active, BlenderBackend(scene))
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def build(scene: Any, layout_id: str, names: list[str]) -> dict[str, Any]:
    """Give *names* the layout *layout_id* in *scene* (the operator's and the tool's code)."""
    backend = BlenderBackend(scene)
    try:
        return ra.apply_layout(scene, _level_strips(scene), layout_id, names, backend)
    except (ra.LayoutError, reframe.ReframeError) as ex:
        return {"status": "error", "message": str(ex)}


def _report(op: Operator, out: dict[str, Any]) -> set[str]:
    if out["status"] == "ok":
        op.report({'INFO'}, out["message"])
        return {'FINISHED'}
    if out["status"] == "nothing":
        op.report({'WARNING'}, out["message"])
    else:
        op.report({'ERROR'}, out["message"])
    return {'CANCELLED'}


def _poll(cls: Any, context: Any) -> bool:
    return _seq.poll(cls, context)


# -- operators -------------------------------------------------------------------------------

_ITEMS = [(item.id, item.label, item.description) for item in rl.LAYOUTS]


class BFACW_OT_reframe_layout(Operator):
    """Give the selected strips a vertical layout: full frame, blur fill, tracked, or a split into two crops"""
    bl_idname = "bfacw.reframe_layout"
    bl_label = "Reframe Layout"
    bl_options = {'REGISTER', 'UNDO'}

    layout: EnumProperty(name="Layout", items=_ITEMS, default="full")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _poll(cls, context)

    @classmethod
    def description(cls, _context: Any, properties: Any) -> str:
        try:
            return rl.layout(properties.layout).description
        except (KeyError, AttributeError):
            return cls.__doc__ or ""

    def execute(self, context: Any) -> set[str]:
        scene = _seq.sequencer_scene(context)
        out = build(scene, self.layout, _selected_names(scene))
        return _report(self, out)


class BFACW_OT_reframe_swap(Operator):
    """Swap the top and bottom of the selected split"""
    bl_idname = "bfacw.reframe_swap"
    bl_label = "Swap"
    bl_description = SWAP_TIP
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _poll(cls, context)

    def execute(self, context: Any) -> set[str]:
        scene = _seq.sequencer_scene(context)
        try:
            out = ra.swap_parts(scene, _level_strips(scene), _selected_names(scene), BlenderBackend(scene))
        except ra.LayoutError as ex:
            out = {"status": "error", "message": str(ex)}
        return _report(self, out)


class BFACW_OT_reframe_reset(Operator):
    """Put the selected strips back to their layout's default framing"""
    bl_idname = "bfacw.reframe_reset"
    bl_label = "Reset Framing"
    bl_description = RESET_TIP
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _poll(cls, context)

    def execute(self, context: Any) -> set[str]:
        scene = _seq.sequencer_scene(context)
        try:
            out = ra.reset_framing(scene, _level_strips(scene), _selected_names(scene), BlenderBackend(scene))
        except (ra.LayoutError, reframe.ReframeError) as ex:
            out = {"status": "error", "message": str(ex)}
        return _report(self, out)


# -- placement -------------------------------------------------------------------------------

def _layout_button(layout: Any, item: rl.Layout, current: str, *, text: str = "") -> None:
    """One layout's button: its icon (Coworker's PNG, else the Blender fallback), pressed when current."""
    value = wand_icons.icon_id(item.icon) if item.icon else 0
    kwargs: dict[str, Any] = {"text": text, "depress": current == item.id}
    if value:
        op = layout.operator("bfacw.reframe_layout", icon_value=value, **kwargs)
    else:
        op = layout.operator("bfacw.reframe_layout", icon=safe_icon(item.fallback), **kwargs)
    op.layout = item.id


def _action_buttons(layout: Any, *, text: bool) -> None:
    layout.operator("bfacw.reframe_swap", text="Swap" if text else "", icon=safe_icon(SWAP_ICON))
    layout.operator("bfacw.reframe_reset", text="Reset" if text else "", icon=safe_icon(RESET_ICON))


def _draw_buttons(layout: Any, context: Any, *, wide: bool) -> None:
    current = _active_layout(context)
    items = {item.id: item for item in rl.LAYOUTS}
    if wide:
        for group in (("full", "full_blur", "full_tracked"), ("split_half", "split_top", "split_bottom")):
            grid = layout.grid_flow(row_major=True, columns=3, even_columns=True, even_rows=True, align=True)
            grid.scale_y = 1.8
            for layout_id in group:
                _layout_button(grid, items[layout_id], current)
        row = layout.row(align=True)
        row.enabled = False
        _layout_button(row, items["track_all"], current, text="Track All")
        row = layout.row(align=True)
        _action_buttons(row, text=True)
    else:
        col = layout.column(align=True)
        for item in rl.LAYOUTS:
            if item.ready:
                _layout_button(col, item, current)
        _action_buttons(col, text=False)


class BFACW_PT_reframe_sidebar(Panel):
    """The Reframe box in the Sequencer's sidebar (Blender and Bforartists)"""
    bl_label = PANEL_LABEL
    bl_idname = "BFACW_PT_reframe_sidebar"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 1

    @classmethod
    def poll(cls, context: Any) -> bool:
        return getattr(context.space_data, "view_type", "") in _wand.VIEWS

    def draw_header(self, _context: Any) -> None:
        self.layout.label(icon=safe_icon("FULLSCREEN_ENTER"))

    def draw(self, context: Any) -> None:
        _draw_buttons(self.layout, context, wide=True)


class BFACW_PT_reframe_toolshelf(Panel):
    """The Reframe buttons in Bforartists' toolshelf, tab Coworker"""
    bl_label = PANEL_LABEL
    bl_idname = "BFACW_PT_reframe_toolshelf"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = CATEGORY
    bl_options = {"HIDE_BG"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        space = context.space_data
        return bool(getattr(space, "show_toolshelf_tabs", False)) and getattr(space, "view_type", "") in _wand.VIEWS

    def draw_header(self, _context: Any) -> None:
        self.layout.label(icon=safe_icon("FULLSCREEN_ENTER"))

    def draw(self, context: Any) -> None:
        _draw_buttons(self.layout, context, wide=False)


class BFACW_MT_reframe_layout(Menu):
    bl_idname = "BFACW_MT_reframe_layout"
    bl_label = "Reframe Layout"

    def draw(self, context: Any) -> None:
        layout = self.layout
        current = _active_layout(context)
        for item in rl.LAYOUTS:
            if not item.ready:
                continue
            if item.id == "split_half":
                layout.separator()
            _layout_button(layout, item, current, text=item.label)
        layout.separator()
        layout.operator("bfacw.reframe_swap", icon=safe_icon(SWAP_ICON))
        layout.operator("bfacw.reframe_reset", icon=safe_icon(RESET_ICON))


# -- the agent's tool ------------------------------------------------------------------------

_ALIASES = {"half": "split_half", "half_and_half": "split_half", "split": "split_half", "top": "split_top",
            "top_bias": "split_top", "bottom": "split_bottom", "bottom_bias": "split_bottom", "crop": "full",
            "centre": "full", "center": "full", "full_frame": "full", "blur": "full_blur", "blur_fill": "full_blur",
            "tracked": "full_tracked", "track": "full_tracked"}


def layout_id(text: Any) -> str:
    """``"Split, Half and Half"``, ``"split-top"``, ``"blur"``... -> a layout id, or ``""``."""
    key = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    key = _ALIASES.get(key, key)
    for item in rl.LAYOUTS:
        if key in (item.id, re.sub(r"[^a-z0-9]+", "_", item.label.lower()).strip("_")):
            return item.id
    return ""


def tool_reframe_strips(layout: Any = "", strips: Any = None, swap: bool = False) -> dict[str, Any]:
    """``reframe_strips`` (MCP): the operators' work without a UI, one undo step.  *layout* ``""`` with
    *swap* only swaps; both builds the layout, then swaps."""
    wanted = layout_id(layout) if str(layout or "").strip() else ""
    if str(layout or "").strip() and not wanted:
        return {"status": "error", "message": "Unknown layout '{!s}'. Use one of: {:s}.".format(
            layout, ", ".join(rl.buildable()))}
    if not wanted and not swap:
        return {"status": "error", "message": "Give a layout ({:s}) or swap=true.".format(", ".join(rl.buildable()))}
    scene = _wand._tool_scene()  # pylint: disable=protected-access
    if scene is None or scene.sequence_editor is None:
        return {"status": "error", "message": "The Sequencer's scene has no strips"}
    if isinstance(strips, str):
        strips = [strips] if strips else []
    names = [str(n) for n in strips or ()] or _selected_names(scene)
    if not names:
        return {"status": "nothing", "message": "No strips named and none selected: name the movie, image or scene "
                                                 "strips to reframe (strips=[...])", "scene": scene.name}
    out: dict[str, Any] = {"status": "ok", "message": "", "changed": []}
    if wanted:
        out = build(scene, wanted, names)
    if swap and out["status"] == "ok":
        try:
            swapped = ra.swap_parts(scene, _level_strips(scene), names, BlenderBackend(scene))
        except ra.LayoutError as ex:
            swapped = {"status": "error", "message": str(ex)}
        out["swapped"] = swapped.get("changed", [])
        out["message"] = (out["message"] + ". " if out["message"] else "") + swapped["message"]
        if swapped["status"] != "ok" and not wanted:
            out["status"] = swapped["status"]
    elif swap:
        out["swapped"] = []
    if out["status"] == "ok" and (out.get("changed") or out.get("swapped")):
        _wand._push_undo("Reframe")  # pylint: disable=protected-access
    out["scene"] = scene.name
    return out


# -- registration ----------------------------------------------------------------------------

_CLASSES = (BFACW_OT_reframe_layout, BFACW_OT_reframe_swap, BFACW_OT_reframe_reset, BFACW_MT_reframe_layout,
            BFACW_PT_reframe_sidebar)
_DONE: list[type] = []


def register() -> None:
    classes = list(_CLASSES)
    if _wand._has_toolshelf_tabs():                       # pylint: disable=protected-access
        classes.append(BFACW_PT_reframe_toolshelf)       # Bforartists only: Blender refuses a TOOLS category
    for cls in classes:
        bpy.utils.register_class(cls)
        _DONE.append(cls)


def unregister() -> None:
    for cls in reversed(_DONE):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _DONE.clear()
