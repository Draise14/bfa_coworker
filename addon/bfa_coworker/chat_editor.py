# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The Coworker editor and the compact sidebar chat (Tier 4, tasks "GPU 4" + "GPU 5").

**The Coworker editor** is a custom node-editor type ("Coworker" in the
Editor Type menu, with the agent icon) that is only a chat:

* the centre draws the GPU chat (:class:`ui_gpu.surface.ChatSurface`):
  newest message at the bottom, the input pinned under it, Enter sends;
* its **header** is Coworker's own (Chat and View menus, chat mode, status,
  Start / Stop, settings): the node editor's header draw is wrapped so it
  only changes inside a Coworker editor;
* its **sidebar** holds the chat's native panels -- the same controls,
  Session, Queue and Status & Diagnostics panels as the 3D View's Coworker
  tab (registered a second time for this editor), so nothing is lost;
* its **toolshelf** holds icon actions (new thread, @ mention, render or
  screen capture, plan, rules, session log);
* the node editor's own panels (and their tabs) are hidden there -- every
  other node editor is untouched, and all of it is undone on unregister.

**The compact sidebar chat**: with *Sidebar Chat* = Compact (Preferences),
the 3D View's Coworker tab draws the same GPU chat in a small form, with a
top bar for mode, status, Start / Stop, settings and "open the Coworker
editor".  Both read the same conversation; the typed draft is shared too
(``chat_input``), so the native @ mention and capture buttons fill it.

Sending goes through the existing ``bfacw.chat_send`` operator, so every
engine, the queue, attachments and checkpoints behave exactly as in the
panel chat.  External Harness shows a card (the chat runs in the external
client) with Start / Stop Bridge, Copy Config and Configure.
"""

from __future__ import annotations

import time
from typing import Any

import bpy  # pylint: disable=import-error

from . import agent_controller, mcp_to_blender_server
from .shared import CHAT_MODE_ITEMS, HARNESS_PRESET_ITEMS
from .ui_gpu import input_capture, render
from .ui_gpu import layout as gpu_layout
from .ui_gpu import style as gpu_style
from .ui_gpu.input_box import InputBox, Suggestion
from .ui_gpu.surface import ChatSurface, Notice, TopBar

__all__ = (
    "TREE",
    "draft_from_gpu",
    "is_coworker_space",
    "register",
    "unregister",
)

TREE = "BFACW_CoworkerTree"
CATEGORY = "Coworker"
_IS_BFA = hasattr(bpy.types, "VIEW3D_MT_view")
AGENT_ICON = "WIZARD" if _IS_BFA else "GHOST_ENABLED"
_TAB_STRIP_PX = 24          # the sidebar's tab strip, at UI scale 1


def is_coworker_space(space: Any) -> bool:
    return getattr(space, "type", "") == "NODE_EDITOR" and getattr(space, "tree_type", "") == TREE


def _is_coworker(context: Any) -> bool:
    return is_coworker_space(getattr(context, "space_data", None))


def _prefs(context: Any = None) -> Any:
    context = context or bpy.context
    try:
        return context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def _props() -> Any:
    return getattr(bpy.context.window_manager, "bfacw_chat_props", None)


def _compact_enabled(context: Any = None) -> bool:
    prefs = _prefs(context)
    return prefs is not None and getattr(prefs, "chat_sidebar_style", "CLASSIC") == "COMPACT"


def _is_harness(prefs: Any) -> bool:
    return prefs is not None and prefs.operating_mode == "EXTERNAL_HARNESS"


# -- The editor type -----------------------------------------------------------------

class BFACW_CoworkerTree(bpy.types.NodeTree):
    """Chat with Coworker in a full editor"""

    bl_idname = TREE
    bl_label = "Coworker"
    bl_icon = AGENT_ICON


# -- State shown by the header and the compact top bar --------------------------------

def _status(context: Any) -> TopBar:
    """Mode, status and Start / Stop, worded like the panel chat."""
    from . import ui_chat
    prefs = _prefs(context)
    state = agent_controller._agent_state
    props = _props()
    if _is_harness(prefs):
        running = mcp_to_blender_server.is_running()
        return TopBar(mode_label="External MCP", mode_icon="WORLD",
                      status="Bridge Running" if running else "Bridge Offline",
                      status_icon="CHECKMARK" if running else "CANCEL",
                      running=running, start_label="Start Bridge")
    local = prefs is not None and prefs.operating_mode == "LOCAL_LLM"
    status = getattr(props, "chat_status", "") or ""
    if state.is_thinking:
        elapsed = time.time() - state.thinking_start_time if state.thinking_start_time else 0.0
        status = "{:s} {:s} ({:s})".format(ui_chat._phase_text(state), ui_chat._spinner_char(state),
                                           ui_chat._fmt_duration(elapsed))
    elif not state.mcp_server_running:
        status = "Offline"
    elif state.error:
        status = "Error: {:s}".format(state.error)
    ok = state.mcp_server_running
    icon = ("CHECKMARK" if ok and not state.is_thinking and not state.error else
            "SORTTIME" if state.is_thinking else "ERROR" if state.error else "CANCEL")
    return TopBar(mode_label="Local LLM" if local else "Remote API", mode_icon="CONSOLE" if local else "URL",
                  status=status, status_icon=icon, running=ok, error=bool(state.error),
                  chat_modes=tuple((item[0], item[1]) for item in CHAT_MODE_ITEMS),
                  chat_mode=getattr(props, "chat_mode", ""))


def _harness_notice(prefs: Any) -> Notice:
    running = mcp_to_blender_server.is_running()
    port = mcp_to_blender_server.get_actual_port() if running else 0
    client = next((label for ident, label, *_ in HARNESS_PRESET_ITEMS if ident == prefs.harness_preset),
                  prefs.harness_preset)
    lines = (
        "The chat runs in your external MCP client (Claude Desktop, Cursor, VS Code, ...); "
        "Coworker gives it Blender's tools through the bridge.",
        ("Bridge running on port {:d}.".format(port) if port else "Bridge running.") if running
        else "Start the bridge, then connect your client.",
        "Client: {:s}.  Copy its config and paste it into the client's MCP settings.".format(client),
    )
    return Notice(title="External Harness", icon="WORLD", lines=lines, buttons=(
        ("bridge", "Stop Bridge" if running else "Start Bridge", "X" if running else "PLAY"),
        ("copy_config", "Copy Config", "COPYDOWN"),
        ("configure", "Configure", "PREFERENCES"),
    ))


def _history() -> list[dict]:
    """The whole conversation, retired turns first (like the panel chat)."""
    from . import session_memory
    return list(session_memory.store.retired_history) + list(agent_controller._agent_state.conversation_history)


# Object type -> its Outliner icon, so the @ list reads like the Outliner.
_OBJECT_ICONS = {
    "MESH": "OUTLINER_OB_MESH", "CURVE": "OUTLINER_OB_CURVE", "SURFACE": "OUTLINER_OB_SURFACE",
    "META": "OUTLINER_OB_META", "FONT": "OUTLINER_OB_FONT", "CURVES": "OUTLINER_OB_CURVES",
    "POINTCLOUD": "OUTLINER_OB_POINTCLOUD", "VOLUME": "OUTLINER_OB_VOLUME",
    "GREASEPENCIL": "OUTLINER_OB_GREASEPENCIL", "GPENCIL": "OUTLINER_OB_GREASEPENCIL",
    "ARMATURE": "OUTLINER_OB_ARMATURE", "LATTICE": "OUTLINER_OB_LATTICE", "EMPTY": "OUTLINER_OB_EMPTY",
    "LIGHT": "OUTLINER_OB_LIGHT", "LIGHT_PROBE": "OUTLINER_OB_LIGHTPROBE", "CAMERA": "OUTLINER_OB_CAMERA",
    "SPEAKER": "OUTLINER_OB_SPEAKER",
}


def _mention_categories() -> tuple[tuple[str, str, str], ...]:
    """The native mention popup's categories: (id, label, icon)."""
    from . import ui_chat
    return tuple((key, info["label"], info["icon"]) for key, info in ui_chat._MENTION_CATEGORIES.items())


def _mentions(query: str) -> list[Suggestion]:
    from . import ui_chat
    try:
        items = ui_chat._filter_mentionables(ui_chat._collect_all_mentionables(), query)
    except Exception:  # pylint: disable=broad-exception-caught
        return []
    icons = {key: info["icon"] for key, info in ui_chat._MENTION_CATEGORIES.items()}
    out = []
    for item in items[:200]:
        category = item.get("category", "")
        if category == "object":
            kind = str(item.get("type", ""))
            icon, detail = _OBJECT_ICONS.get(kind, "OBJECT_DATA"), kind.replace("_", " ").title()
        else:
            icon, detail = icons.get(category, "DOT"), ui_chat._MENTION_CATEGORIES.get(category, {}).get("label", "")
            detail = detail[:-1] if detail.endswith("s") else detail
        out.append(Suggestion(item["name"], "@" + item["name"], category, icon, detail))
    return out


# -- Surfaces: one per region ----------------------------------------------------------

_SURFACES: dict[int, tuple[ChatSurface, Any]] = {}   # region pointer -> (surface, region)
_SYNCED: dict[int, str] = {}                        # surface id -> the draft it last showed
_PENDING: list[tuple[str, str, int]] = []


def _surface(region: Any, compact: bool) -> ChatSurface:
    key = region.as_pointer()
    entry = _SURFACES.get(key)
    if entry is None or entry[0].compact != compact:
        surface = ChatSurface(compact=compact,
                              box=InputBox(provider=_mentions, categories=_mention_categories()))
        surface.view.agent_icon = AGENT_ICON
        surface.view.image_size = _image_size
        _SURFACES[key] = (surface, region)
        return surface
    _SURFACES[key] = (entry[0], region)
    return entry[0]


def draft_from_gpu(text: str) -> bool:
    """True when *text* (the shared draft) is what a GPU chat box holds."""
    return bool(text) and any(s.box.model.text == text for s, _r in _SURFACES.values())


def _image_size(name: str) -> tuple[int, int] | None:
    image = bpy.data.images.get(name)
    if image is None:
        return None
    width, height = image.size[:]
    return (width, height) if width > 0 and height > 0 else None


def _open_image(name: str) -> None:
    """Show an attached image in an Image Editor, in its own window."""
    image = bpy.data.images.get(name)
    if image is None:
        return
    wm = bpy.context.window_manager
    with bpy.context.temp_override(window=bpy.context.window or wm.windows[0]):
        bpy.ops.wm.window_new()
    window = wm.windows[-1]
    area = max(window.screen.areas, key=lambda a: a.width * a.height)
    area.ui_type = "IMAGE_EDITOR"
    area.spaces.active.image = image


def _surface_for_box(box: InputBox) -> ChatSurface | None:
    return next((s for s, _r in _SURFACES.values() if s.box is box), None)


def _sync_box(surface: ChatSurface, props: Any, busy: bool, running: bool, harness: bool) -> None:
    """Show the shared draft (chat_input) and the add-on state in the box."""
    box = surface.box
    draft = getattr(props, "chat_input", "") or ""
    if _SYNCED.get(id(surface)) != draft and draft != box.model.text:
        box.model.set_text(draft)
    _SYNCED[id(surface)] = box.model.text
    image = getattr(props, "chat_image", None)
    box.attachment = image.name if image is not None else ""
    box.busy = busy
    box.placeholder = ("Ask Coworker...  (Enter to send, Shift+Enter for a new line)" if running else
                       "Start Coworker to chat (Start, above)" if not harness else "")


def _draw(region: Any, width: float, compact: bool) -> None:
    context = bpy.context
    prefs = _prefs(context)
    props = _props()
    if prefs is None or props is None:
        return
    surface = _surface(region, compact)
    state = agent_controller._agent_state
    harness = _is_harness(prefs)
    _sync_box(surface, props, state.is_thinking, state.mcp_server_running, harness)
    style = gpu_style.style_from_preferences(context.preferences, zoom=surface.zoom)
    canvas = gpu_layout.Canvas(style, render.measure, surface.interaction())
    actions = surface.draw(canvas, gpu_layout.Rect(0.0, 0.0, width, float(region.height)), _history(),
                           live=state, top=_status(context) if compact else None,
                           notice=_harness_notice(prefs) if harness else None)
    render.render(canvas.commands, style)
    if actions:
        _PENDING.extend((action, value, region.as_pointer()) for action, value in actions)
        if not bpy.app.timers.is_registered(_flush):
            bpy.app.timers.register(_flush, first_interval=0.0)


def _draw_editor() -> None:
    context = bpy.context
    if _is_coworker(context) and context.region is not None:
        _draw(context.region, float(context.region.width), compact=False)


def _sidebar_tab_active(region: Any) -> bool:
    try:
        return region.type == "UI" and region.active_panel_category == CATEGORY
    except AttributeError:
        return False


def _draw_sidebar() -> None:
    context = bpy.context
    region = context.region
    if region is None or not _compact_enabled(context) or not _sidebar_tab_active(region):
        return
    tabs = _TAB_STRIP_PX * context.preferences.view.ui_scale
    _draw(region, max(1.0, region.width - tabs), compact=True)


def _router(context: Any, event: Any) -> tuple[ChatSurface, Any, Any] | None:
    """The chat surface under the mouse, if any."""
    window = context.window
    if window is None:
        return None
    compact = _compact_enabled(context)
    for area in window.screen.areas:
        if not (area.x <= event.mouse_x < area.x + area.width and area.y <= event.mouse_y < area.y + area.height):
            continue
        coworker = area.type == "NODE_EDITOR" and is_coworker_space(area.spaces.active)
        if not coworker and not (compact and area.type == "VIEW_3D"):
            return None
        for region in area.regions:
            if not (region.x <= event.mouse_x < region.x + region.width
                    and region.y <= event.mouse_y < region.y + region.height):
                continue
            if coworker and region.type == "WINDOW" or not coworker and _sidebar_tab_active(region):
                entry = _SURFACES.get(region.as_pointer())
                if entry is not None:
                    return entry[0], area, region
        return None
    return None


# -- Actions ----------------------------------------------------------------------------

def _window_override() -> dict:
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm.windows else None)
    return {"window": window, "screen": window.screen} if window is not None else {}


def _do(action: str, value: str, surface: ChatSurface | None, region: Any = None) -> None:
    """Carry out one surface action (main thread, outside drawing)."""
    context = bpy.context
    props = _props()
    prefs = _prefs(context)
    wm = context.window_manager
    ops = bpy.ops.bfacw
    if action == "send":
        if not agent_controller._agent_state.mcp_server_running:
            if surface is not None:     # keep what was typed
                surface.box.model.set_text(value)
            if props is not None:
                props.chat_input = value
            ops.chat_send()             # reports "Coworker is not running"
            return
        props.chat_input = value
        ops.chat_send()
    elif action == "stop":
        ops.chat_stop()
    elif action == "copy":
        wm.clipboard = value
    elif action == "copy_error":
        ops.copy_status_error()
    elif action == "link":
        bpy.ops.wm.url_open(url=value)
    elif action == "start":
        ops.agent_start()
    elif action == "stop_agent":
        ops.agent_stop()
    elif action == "prefs":
        ops.open_addon_prefs()
    elif action == "open_editor":
        ops.open_coworker_editor()
    elif action == "open_image":
        _open_image(value)
    elif action == "chat_mode" and props is not None:
        props.chat_mode = value
    elif action == "native_edit" and surface is not None:
        input_capture.focus(surface.box, region)
        bpy.ops.bfacw.gpu_input_native("INVOKE_DEFAULT")
    elif action == "notice":
        if value == "bridge":
            ops.agent_stop() if mcp_to_blender_server.is_running() else ops.agent_start()
        elif value == "copy_config" and prefs is not None:
            ops.copy_mcp_config(client_type=prefs.harness_preset)
        elif value == "configure":
            ops.open_harness_prefs()


def _flush() -> None:
    pending = list(_PENDING)
    _PENDING.clear()
    override = _window_override()
    for action, value, key in pending:
        entry = _SURFACES.get(key)
        surface, region = entry if entry is not None else (None, None)
        try:
            with bpy.context.temp_override(**override):
                _do(action, value, surface, region)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] chat action {:s} failed: {:s}".format(action, str(ex)))
    return None


def _on_box_action(box: InputBox, action: str, value: str) -> None:
    """Keyboard actions (Enter, Esc...) from input_capture, in the modal's context."""
    surface = _surface_for_box(box)
    try:
        _do(action, value, surface)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] chat action {:s} failed: {:s}".format(action, str(ex)))


def _after_key(box: InputBox) -> None:
    """Keep the shared draft in step with what was typed."""
    props = _props()
    if props is not None and props.chat_input != box.model.text:
        props.chat_input = box.model.text
    surface = _surface_for_box(box)
    if surface is not None:
        _SYNCED[id(surface)] = box.model.text


# -- Operators ------------------------------------------------------------------------

class BFACW_OT_open_coworker_editor(bpy.types.Operator):
    """Open the Coworker editor: the full chat, in its own window"""

    bl_idname = "bfacw.open_coworker_editor"
    bl_label = "Open Coworker Editor"

    def execute(self, context):
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "NODE_EDITOR" and is_coworker_space(area.spaces.active):
                    self.report({"INFO"}, "The Coworker editor is already open")
                    return {"CANCELLED"}
        with context.temp_override(window=context.window or context.window_manager.windows[0]):
            bpy.ops.wm.window_new()
        window = context.window_manager.windows[-1]
        area = max(window.screen.areas, key=lambda a: a.width * a.height)
        area.ui_type = TREE
        return {"FINISHED"}


class BFACW_OT_editor_zoom(bpy.types.Operator):
    """Zoom the chat (Ctrl+wheel over the chat does the same)"""

    bl_idname = "bfacw.editor_zoom"
    bl_label = "Zoom Chat"
    bl_options = {"INTERNAL"}

    delta: bpy.props.FloatProperty(default=0.1, options={"SKIP_SAVE"})  # type: ignore[valid-type]

    def execute(self, context):
        area = context.area
        for region in (area.regions if area else ()):
            entry = _SURFACES.get(region.as_pointer())
            if entry is not None:
                surface = entry[0]
                surface.zoom = 1.0 if self.delta == 0 else max(0.6, min(2.5, round(surface.zoom + self.delta, 2)))
                region.tag_redraw()
        return {"FINISHED"}


class BFACW_OT_editor_thumbs(bpy.types.Operator):
    """Size of the image thumbnails in the chat"""

    bl_idname = "bfacw.editor_thumbs"
    bl_label = "Thumbnail Size"
    bl_options = {"INTERNAL"}

    size: bpy.props.EnumProperty(  # type: ignore[valid-type]
        items=[("S", "Small", ""), ("M", "Medium", ""), ("L", "Large", ""), ("NEXT", "Next Size", "")],
        default="NEXT", options={"SKIP_SAVE"})

    @classmethod
    def description(cls, _context, properties):
        return ("Next thumbnail size (Small, Medium, Large)" if properties.size == "NEXT"
                else "{:s} thumbnails for attached images".format(
                    {"S": "Small", "M": "Medium", "L": "Large"}[properties.size]))

    def execute(self, context):
        area = context.area
        for region in (area.regions if area else ()):
            entry = _SURFACES.get(region.as_pointer())
            if entry is not None:
                view = entry[0].view
                order = ["S", "M", "L"]
                view.thumb_size = (order[(order.index(view.thumb_size) + 1) % 3] if self.size == "NEXT"
                                   else self.size)
                region.tag_redraw()
        return {"FINISHED"}


class BFACW_OT_editor_jump(bpy.types.Operator):
    """Scroll the chat to the newest message"""

    bl_idname = "bfacw.editor_jump"
    bl_label = "Jump to Newest"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        area = context.area
        for region in (area.regions if area else ()):
            entry = _SURFACES.get(region.as_pointer())
            if entry is not None:
                entry[0].view.scroll = 0.0
                region.tag_redraw()
        return {"FINISHED"}


# -- Header --------------------------------------------------------------------------

class BFACW_MT_editor_chat(bpy.types.Menu):
    bl_idname = "BFACW_MT_editor_chat"
    bl_label = "Chat"

    def draw(self, context):
        layout = self.layout
        layout.operator("bfacw.chat_clear", text="New Thread", icon="ADD")
        layout.operator("bfacw.chat_stop", text="Stop", icon="PAUSE")
        layout.separator()
        layout.operator("bfacw.mention_search", text="@ Mention", icon="OUTLINER_OB_MESH")
        layout.operator("bfacw.chat_capture_render", text="Attach Render", icon="RENDER_STILL")
        layout.operator("bfacw.chat_capture_screen", text="Attach Screen", icon="FULLSCREEN_ENTER")
        layout.separator()
        layout.operator("bfacw.queue_show", text="Show Queue", icon="FORWARD")
        layout.operator("bfacw.queue_clear", text="Clear Queue", icon="X")
        layout.separator()
        layout.operator("bfacw.copy_session_log", text="Copy Session Log", icon="COPYDOWN")
        layout.operator("bfacw.export_session_log", text="Export Session Log", icon="TEXT")


class BFACW_MT_editor_view(bpy.types.Menu):
    bl_idname = "BFACW_MT_editor_view"
    bl_label = "View"

    def draw(self, context):
        layout = self.layout
        space = context.space_data
        layout.prop(space, "show_region_toolbar")
        layout.prop(space, "show_region_ui")
        layout.separator()
        layout.operator("bfacw.editor_zoom", text="Zoom In", icon="ZOOM_IN").delta = 0.1
        layout.operator("bfacw.editor_zoom", text="Zoom Out", icon="ZOOM_OUT").delta = -0.1
        layout.operator("bfacw.editor_zoom", text="Reset Zoom", icon="ZOOM_ALL").delta = 0.0
        layout.operator("bfacw.editor_jump", text="Jump to Newest", icon="TRIA_DOWN")
        layout.separator()
        for size, label in (("S", "Small Thumbnails"), ("M", "Medium Thumbnails"), ("L", "Large Thumbnails")):
            layout.operator("bfacw.editor_thumbs", text=label, icon="IMAGE_DATA").size = size
        if hasattr(bpy.types, "INFO_MT_area"):
            layout.separator()
            layout.menu("INFO_MT_area")


def _draw_header(self: Any, context: Any) -> None:
    layout = self.layout
    layout.template_header()
    row = layout.row(align=True)
    row.menu("BFACW_MT_editor_chat")
    row.menu("BFACW_MT_editor_view")
    layout.separator_spacer()
    info = _status(context)
    props = _props()
    if info.chat_modes and props is not None:
        layout.prop(props, "chat_mode", expand=True)
        layout.separator()
    status = layout.row(align=True)
    status.label(text=info.mode_label, icon=info.mode_icon)
    status.label(text=info.status[:60], icon=info.status_icon)
    if info.error:
        status.operator("bfacw.copy_status_error", text="", icon="COPYDOWN")
    if info.running:
        layout.operator("bfacw.agent_stop", text="Stop Bridge" if info.start_label != "Start" else "Stop",
                        icon="X")
    else:
        layout.operator("bfacw.agent_start", text=info.start_label, icon="PLAY")
    layout.separator_spacer()
    layout.operator("bfacw.open_addon_prefs", text="", icon="PREFERENCES")


# -- Toolshelf and sidebar --------------------------------------------------------------

class _CoworkerPanel:
    bl_space_type = "NODE_EDITOR"
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return _is_coworker(context)


class BFACW_PT_editor_tools(_CoworkerPanel, bpy.types.Panel):
    """Coworker actions as icon buttons (the toolshelf is a narrow strip)"""

    bl_label = "Coworker"
    bl_idname = "BFACW_PT_editor_tools"
    bl_region_type = "TOOLS"
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        col = self.layout.column(align=True)
        col.scale_y = 1.4
        state = agent_controller._agent_state
        col.operator("bfacw.chat_clear", text="", icon="ADD")
        col.operator("bfacw.mention_search", text="", icon="OUTLINER_OB_MESH")
        col.separator()
        col.operator("bfacw.chat_capture_render", text="", icon="RENDER_STILL")
        col.operator("bfacw.chat_capture_screen", text="", icon="FULLSCREEN_ENTER")
        col.operator("bfacw.editor_thumbs", text="", icon="IMAGE_DATA").size = "NEXT"
        col.separator()
        if state.is_thinking:
            col.operator("bfacw.chat_stop", text="", icon="PAUSE")
        col.operator("bfacw.plan_open", text="", icon="CHECKBOX_HLT")
        col.operator("bfacw.edit_rules", text="", icon="TEXT")
        col.operator("bfacw.copy_session_log", text="", icon="COPYDOWN")


class BFACW_PT_editor_chat(_CoworkerPanel, bpy.types.Panel):
    """The chat's controls, as in the 3D View's Coworker tab"""

    bl_label = "Coworker"
    bl_idname = "BFACW_PT_editor_chat"
    bl_region_type = "UI"
    bl_order = 0

    def draw(self, context):
        from . import ui_chat
        if ui_chat.draw_chat_controls(self.layout, context, show_chat_mode=False):
            ui_chat._draw_goal_plan_inline(self.layout)


# Blender wants poll(cls, context) exactly: closures, not default arguments.
def _poll_in_coworker(base: Any) -> Any:
    def poll(_cls, context):
        return _is_coworker(context) and base(context)
    return poll


def _poll_outside_coworker(fn: Any, inherited: Any = None) -> Any:
    def poll(cls, context):
        if _is_coworker(context):
            return False
        if fn is not None:
            return fn(cls, context)
        return inherited(context) if inherited is not None else True
    return poll


def _editor_copies() -> list[type]:
    """The 3D View's Session / Queue / Status panels, registered again for this editor."""
    from . import ui_chat
    sources = (ui_chat.BFACW_PT_chat_session, ui_chat.BFACW_PT_chat_session_context,
               ui_chat.BFACW_PT_chat_session_memory, ui_chat.BFACW_PT_chat_queue, ui_chat.BFACW_PT_chat_status)
    ids = {cls.bl_idname: cls.bl_idname.replace("BFACW_PT_chat_", "BFACW_PT_editor_") for cls in sources}
    out = []
    for cls in sources:
        # A fresh Panel that borrows the original's drawing: subclassing a
        # registered panel breaks Blender's link to the original's class.
        attrs = {"bl_idname": ids[cls.bl_idname], "bl_space_type": "NODE_EDITOR", "bl_region_type": "UI",
                 "bl_category": CATEGORY, "bl_label": cls.bl_label,
                 "poll": classmethod(_poll_in_coworker(cls.poll)), "__doc__": cls.__doc__}
        for name in ("bl_options", "bl_order"):
            if hasattr(cls, name):
                attrs[name] = getattr(cls, name)
        for name in ("draw", "draw_header"):
            if name in cls.__dict__:
                attrs[name] = cls.__dict__[name]
        parent = getattr(cls, "bl_parent_id", "")
        if parent:
            attrs["bl_parent_id"] = ids[parent]
        out.append(type(ids[cls.bl_idname], (bpy.types.Panel,), attrs))
    return out


# -- Taking over the node editor inside Coworker editors -------------------------------

_ORIGINAL: dict[str, Any] = {}
_WRAPPED_POLLS: list[tuple[type, Any]] = []      # (class, original poll in its __dict__)
_REREGISTERED: list[type] = []                   # classes we re-registered with a poll


def _all_subclasses(cls: type) -> list[type]:
    out = []
    for sub in cls.__subclasses__():
        out.append(sub)
        out += _all_subclasses(sub)
    return out


def _node_panels(ours: set[str]) -> list[type]:
    return [c for c in _all_subclasses(bpy.types.Panel)
            if getattr(c, "bl_space_type", "") == "NODE_EDITOR" and getattr(c, "is_registered", False)
            and c.__name__ not in ours and getattr(c, "bl_idname", c.__name__) not in ours]


def _parent_depth(cls: type, by_id: dict[str, type]) -> int:
    depth, parent = 0, getattr(cls, "bl_parent_id", "")
    while parent and depth < 8:
        depth += 1
        parent = getattr(by_id.get(parent), "bl_parent_id", "") if parent in by_id else ""
    return depth


def _take_over(ours: set[str]) -> None:
    header = bpy.types.NODE_HT_header
    original = _ORIGINAL["header"] = header.draw

    # A plain closure: Blender calls draw(self, context) and crashes on a
    # replacement that carries extra default arguments.
    def draw(self, context):
        if _is_coworker(context):
            _draw_header(self, context)
        else:
            original(self, context)

    header.draw = draw
    panels = _node_panels(ours)
    without = []
    for cls in panels:
        old = cls.__dict__.get("poll")
        if old is not None:
            fn = old.__func__

            _WRAPPED_POLLS.append((cls, old))
            cls.poll = classmethod(_poll_outside_coworker(fn))
        else:
            without.append(cls)
    # Blender only notices a poll at registration: re-register those without one.
    by_id = {getattr(c, "bl_idname", c.__name__): c for c in without}
    depth = {c: _parent_depth(c, by_id) for c in without}
    for cls in sorted(without, key=lambda c: -depth[c]):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            continue
        cls.poll = classmethod(_poll_outside_coworker(None, getattr(cls, "poll", None)))
        _REREGISTERED.append(cls)
    for cls in sorted(_REREGISTERED, key=lambda c: depth[c]):
        try:
            bpy.utils.register_class(cls)
        except (RuntimeError, ValueError) as ex:
            print("[Coworker] editor: could not re-register {:s}: {:s}".format(cls.__name__, str(ex)))


def _give_back() -> None:
    if "header" in _ORIGINAL:
        bpy.types.NODE_HT_header.draw = _ORIGINAL.pop("header")
    for cls, old in _WRAPPED_POLLS:
        cls.poll = old
    _WRAPPED_POLLS.clear()
    by_id = {getattr(c, "bl_idname", c.__name__): c for c in _REREGISTERED}
    depth = {c: _parent_depth(c, by_id) for c in _REREGISTERED}
    for cls in sorted(_REREGISTERED, key=lambda c: -depth[c]):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
        if "poll" in cls.__dict__:
            delattr(cls, "poll")
    for cls in sorted(_REREGISTERED, key=lambda c: depth[c]):
        try:
            bpy.utils.register_class(cls)
        except (RuntimeError, ValueError):
            pass
    _REREGISTERED.clear()


# -- Registration ------------------------------------------------------------------------

_HANDLERS: list[tuple[Any, Any, str]] = []
_CLASSES = (BFACW_CoworkerTree, BFACW_OT_open_coworker_editor, BFACW_OT_editor_zoom, BFACW_OT_editor_jump,
            BFACW_OT_editor_thumbs,
            BFACW_MT_editor_chat, BFACW_MT_editor_view, BFACW_PT_editor_tools, BFACW_PT_editor_chat)
_COPIES: list[type] = []


def register() -> None:
    if bpy.app.background:
        return
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _COPIES[:] = _editor_copies()
    for cls in _COPIES:
        bpy.utils.register_class(cls)
    ours = {c.__name__ for c in _CLASSES + tuple(_COPIES)} | {c.bl_idname for c in _COPIES}
    _take_over(ours)
    _HANDLERS.append((bpy.types.SpaceNodeEditor,
                      bpy.types.SpaceNodeEditor.draw_handler_add(_draw_editor, (), "WINDOW", "POST_PIXEL"),
                      "WINDOW"))
    _HANDLERS.append((bpy.types.SpaceView3D,
                      bpy.types.SpaceView3D.draw_handler_add(_draw_sidebar, (), "UI", "POST_PIXEL"), "UI"))
    input_capture.register()
    input_capture.set_host(_router, _on_box_action, _after_key)


def unregister() -> None:
    if not _HANDLERS and not _COPIES:
        return
    input_capture.unregister()
    for space, handle, region in _HANDLERS:
        try:
            space.draw_handler_remove(handle, region)
        except (ValueError, RuntimeError):
            pass
    _HANDLERS.clear()
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    _give_back()
    for cls in reversed(_COPIES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _COPIES.clear()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _SURFACES.clear()
    _SYNCED.clear()
    _PENDING.clear()
