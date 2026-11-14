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
from .shared import CHAT_MODE_ITEMS, HARNESS_PRESET_ITEMS, icon as safe_icon, is_bforartists
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
_IS_BFA = is_bforartists()
AGENT_ICON = safe_icon("WIZARD")       # the ghost in Blender (#456)
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
    state = ui_chat._ui_state(context)          # the thread this chat shows (#358)
    services = agent_controller._agent_state    # service status is shared
    props = _props()
    if _is_harness(prefs):
        running = mcp_to_blender_server.is_running()
        return TopBar(mode_label="External MCP", mode_icon=_mode_icon("EXTERNAL_HARNESS"),
                      status="Bridge Running" if running else "Bridge Offline",
                      status_icon="CHECKMARK" if running else "CANCEL",
                      running=running, start_label="Start Bridge")
    if prefs is not None and prefs.operating_mode == "EXTERNAL_AGENT":
        engine = ui_chat._acp
        running = bool(engine is not None and engine.connected)
        status = (engine.status if engine is not None else "Not connected")
        if state.is_thinking:
            elapsed = time.time() - state.thinking_start_time if state.thinking_start_time else 0.0
            status = "{:s} {:s} ({:s})".format(ui_chat._phase_text(state), ui_chat._spinner_char(state),
                                               ui_chat._fmt_duration(elapsed))
        icon = ("SORTTIME" if state.is_thinking else "CHECKMARK" if running
                else "TIME" if engine is not None and engine.connecting else "CANCEL")
        return TopBar(mode_label="External Agent", mode_icon=_mode_icon("EXTERNAL_AGENT"), status=status,
                      status_icon=icon,
                      running=running, start_label="Connect",
                      chat_modes=tuple((item[0], item[1], item[3]) for item in CHAT_MODE_ITEMS),
                      chat_mode=getattr(props, "chat_mode", ""))
    local = prefs is not None and prefs.operating_mode == "LOCAL_LLM"
    status = getattr(props, "chat_status", "") or ""
    if state is not services:           # an editor's thread has its own status line (#358)
        status = ui_chat._thread_status(ui_chat._ui_thread(context))[0]
    if state.is_thinking:
        elapsed = time.time() - state.thinking_start_time if state.thinking_start_time else 0.0
        status = "{:s} {:s} ({:s})".format(ui_chat._phase_text(state), ui_chat._spinner_char(state),
                                           ui_chat._fmt_duration(elapsed))
    elif not services.mcp_server_running:
        status = "Offline"
    elif state.error:
        status = "Error: {:s}".format(state.error)
    ok = services.mcp_server_running
    icon = ("CHECKMARK" if ok and not state.is_thinking and not state.error else
            "SORTTIME" if state.is_thinking else "ERROR" if state.error else "CANCEL")
    return TopBar(mode_label="Local LLM" if local else "Remote API",
                  mode_icon=_mode_icon("LOCAL_LLM" if local else "REMOTE_API"),
                  status=status, status_icon=icon, running=ok, error=bool(state.error),
                  chat_modes=tuple((item[0], item[1], item[3]) for item in CHAT_MODE_ITEMS),
                  chat_mode=getattr(props, "chat_mode", ""))


def _mode_icon(mode: str) -> str:
    """The mode's icon, as on its tab (OPERATING_MODE_ITEMS): one icon per mode everywhere."""
    from .shared import OPERATING_MODE_ITEMS
    return next((item[3] for item in OPERATING_MODE_ITEMS if item[0] == mode), "DOT")


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
        "Client: {:s}.  Copy Config (sidebar, Coworker) and paste it into the client's MCP settings."
        .format(client),
        "Drop an image here (or attach a render or screen) and ask your client to look at it: "
        "it reads it with get_attached_image.",
    )
    from . import activity
    rows = tuple(
        ({"ok": "CHECKMARK", "error": "ERROR", "cancelled": "CANCEL"}.get(e.status, "TIME"),
         "{:s}  {:s}".format(e.tool, e.args),
         "{:s} ({:.1f} s, {:s})".format(e.preview or e.status, e.seconds,
                                        time.strftime("%H:%M:%S", time.localtime(e.when))))
        for e in activity.entries(30))
    return Notice(title="External Harness", icon="WORLD", lines=lines, buttons=(
        ("copy_log", "Copy Log", "COPYDOWN"),
        ("configure", "Configure", "PREFERENCES"),
    ), rows=rows, rows_title="What your client did in Blender")


def _history(context: Any = None) -> list[dict]:
    """The shown thread's whole conversation, retired turns first (like the panel chat)."""
    from . import ui_chat
    thread = ui_chat._ui_thread(context)
    store = agent_controller._memory(thread).store
    return list(store.retired_history) + list(agent_controller.thread_state(thread).conversation_history)


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


def _commands(query: str) -> list[Suggestion]:
    """The External Agent's own slash commands for "/" in the chat (Agent 8b)."""
    from . import ui_chat
    engine = ui_chat._acp
    if engine is None or not engine.connected:
        return []
    q = query.lower()
    found = [c for c in engine.commands() if q in c["name"].lower() or q in c["description"].lower()]
    found.sort(key=lambda c: (not c["name"].lower().startswith(q), c["name"]))
    return [Suggestion("/" + c["name"], "/" + c["name"], "command", "CONSOLE",
                       (c["hint"] or c["description"])[:60]) for c in found]


# -- Surfaces: one per region ----------------------------------------------------------

_SURFACES: dict[int, tuple[ChatSurface, Any]] = {}   # region pointer -> (surface, region)
_SYNCED: dict[int, str] = {}                        # surface id -> the draft it last showed
_PENDING: list[tuple[str, str, int]] = []


def _surface(region: Any, compact: bool) -> ChatSurface:
    key = region.as_pointer()
    entry = _SURFACES.get(key)
    if entry is None or entry[0].compact != compact:
        surface = ChatSurface(compact=compact,
                              box=InputBox(provider=_mentions, categories=_mention_categories(),
                                           command_provider=_commands))
        surface.view.agent_icon = AGENT_ICON
        surface.view.image_size = _image_size
        _SURFACES[key] = (surface, region)
        return surface
    _SURFACES[key] = (entry[0], region)
    return entry[0]


def draft_from_gpu(text: str) -> bool:
    """True when *text* (the shared draft) belongs to a GPU chat: a GPU box has
    the keyboard, or holds this text.  The panel chat's own @ popup then stays
    closed -- the GPU chat has its inline @ list."""
    if any(s.box.focused for s, _r in _SURFACES.values()):
        return True
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


def _sync_box(surface: ChatSurface, props: Any, busy: bool, running: bool, harness: bool,
              thread: Any = None) -> None:
    """Show the thread's draft and attachment (#417) and the add-on state in the box."""
    from . import ui_chat
    box = surface.box
    draft = (thread.draft if thread is not None else getattr(props, "chat_input", "")) or ""
    if _SYNCED.get(id(surface)) != draft and draft != box.model.text:
        box.model.set_text(draft)
    _SYNCED[id(surface)] = box.model.text
    image = ui_chat._thread_image(thread) if thread is not None else getattr(props, "chat_image", None)
    box.attachment = image.name if image is not None else ""
    surface.attachment_pinned = not (thread.attachment_once if thread is not None
                                     else getattr(props, "chat_image_send_once", True))
    surface.attachment_note = "Your client can look at it (get_attached_image)" if harness else ""
    from .shared import APPROVAL_MODE_ITEMS
    prefs = _prefs()
    mode = getattr(prefs, "approval_mode", "ASK") if prefs is not None else "ASK"
    box.approval = next(((name, icon) for ident, name, _d, icon, _n in APPROVAL_MODE_ITEMS if ident == mode),
                        ("", ""))
    if surface.compact:     # the sidebar chat: a button column; the attach icons go there (#214)
        from . import coworker_api
        box.attach_buttons = False
        last = ("stop", "PAUSE") if busy else ("revert_turn", "LOOP_BACK") if coworker_api.can_revert() else None
        surface.tools = tuple(t for t in (
            ("mention", "OUTLINER_OB_MESH"), last, ("", ""),
            ("attach_menu", "IMAGE_DATA"), ("attach_render", "RENDER_STILL"), ("attach_screen", "MAKE_SCREENSHOT"),
        ) if t is not None)
    box.busy = busy
    box.placeholder = ("Ask Coworker..." if running else
                       "Start Coworker to chat (Start, above)" if not harness else "")


def _view_zoom(region: Any) -> float:
    """Pixels per view unit: the region's own zoom (Ctrl+MMB in a sidebar)."""
    try:
        v2d = region.view2d
        per_pixel = v2d.region_to_view(1.0, 0.0)[0] - v2d.region_to_view(0.0, 0.0)[0]
        return 1.0 / per_pixel if per_pixel > 0 else 1.0
    except (AttributeError, ZeroDivisionError):
        return 1.0


def _approval_card(context: Any = None) -> Any:
    from . import ui_chat
    return ui_chat.approval_card(context)


def _draw(region: Any, rect: Any, compact: bool, zoom: float = 1.0) -> None:
    context = bpy.context
    prefs = _prefs(context)
    props = _props()
    if prefs is None or props is None:
        return
    surface = _surface(region, compact)
    from . import ui_chat
    thread = ui_chat._ui_thread(context)       # each editor its own thread (#358)
    state = agent_controller.thread_state(thread)
    thread.unread = False                       # seen
    harness = _is_harness(prefs)
    _sync_box(surface, props, state.is_thinking, agent_controller._agent_state.mcp_server_running, harness, thread)
    style = gpu_style.style_from_preferences(context.preferences, zoom=surface.zoom * zoom)
    canvas = gpu_layout.Canvas(style, render.measure, surface.interaction())
    surface.bounds = rect
    surface.drop_hover = drop_hover()
    surface.show_tips = bool(getattr(context.preferences.view, "show_tooltips", True))   # #388
    from . import coworker_api
    surface.view.revertable = coworker_api.can_revert() and not state.is_thinking
    card = _approval_card(context)
    surface.view.suggest = not harness and card is None    # no buttons while a card asks (#220)
    typed_before = surface.box.model.text
    actions = surface.draw(canvas, rect, _history(context), live=state, top=_status(context) if compact else None,
                           notice=_harness_notice(prefs) if harness else None,
                           approval=card)     # also in External Harness: a risky step asks (#166)
    if surface.box.model.text != typed_before:
        # Changed by a click while drawing (an @ suggestion): share the draft now,
        # or the panel chat's own @ popup would see the old half-typed "@Ca".
        actions.append(("draft", surface.box.model.text))
        _SYNCED[id(surface)] = surface.box.model.text
    render.render(canvas.commands, style)
    if surface.tip_wait > 0:          # a tooltip is due (#237): draw again then
        _redraw_later(region, surface.tip_wait + 0.02)
    if actions:
        _PENDING.extend((action, value, region.as_pointer()) for action, value in actions)
        if not bpy.app.timers.is_registered(_flush):
            bpy.app.timers.register(_flush, first_interval=0.0)


_TIP_TIMERS: set[int] = set()


def _redraw_later(region: Any, seconds: float) -> None:
    """Tag *region* for a redraw in *seconds* (one timer per region at a time)."""
    key = region.as_pointer()
    if key in _TIP_TIMERS:
        return
    _TIP_TIMERS.add(key)

    def tag() -> None:
        _TIP_TIMERS.discard(key)
        try:
            region.tag_redraw()
        except ReferenceError:
            pass
    bpy.app.timers.register(tag, first_interval=seconds)


_DROP: dict[str, float] = {"until": 0.0}
_SHOWN_SIDEBAR: set[int] = set()


def note_drop_hover() -> None:
    """An image is being dragged over a chat (FileHandler.poll_drop): highlight it."""
    first = _DROP["until"] < time.time()
    _DROP["until"] = time.time() + 0.35
    if first:
        _tag_chat_regions()
        bpy.app.timers.register(_end_drop_hover, first_interval=0.4)


def _end_drop_hover() -> float | None:
    if _DROP["until"] > time.time():
        return 0.2
    _tag_chat_regions()
    return None


def drop_hover() -> bool:
    return _DROP["until"] > time.time()


def _tag_chat_regions() -> None:
    from . import ui_chat
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"NODE_EDITOR", "VIEW_3D"}:
                for region in area.regions:
                    if region.type in {"WINDOW", "UI", "HEADER"}:
                        region.tag_redraw()
            elif area.type in ui_chat.SIDEBAR_TYPES:      # the other editors' sidebar chat (#291)
                for region in area.regions:
                    if region.type == "UI":
                        region.tag_redraw()


def _show_sidebar_once(space: Any) -> None:
    """A new Coworker editor opens with its sidebar (the chat's panels) showing."""
    key = space.as_pointer()
    if key in _SHOWN_SIDEBAR:
        return
    _SHOWN_SIDEBAR.add(key)

    def show() -> None:
        try:
            space.show_region_ui = True
        except (ReferenceError, AttributeError):
            pass
        return None
    bpy.app.timers.register(show, first_interval=0.0)


def _draw_editor() -> None:
    context = bpy.context
    if _is_coworker(context) and context.region is not None:
        _show_sidebar_once(context.space_data)
        region = context.region
        _draw(region, gpu_layout.Rect(0.0, 0.0, float(region.width), float(region.height)), compact=False)


def _sidebar_tab_active(region: Any) -> bool:
    try:
        return region.type == "UI" and region.active_panel_category == CATEGORY
    except AttributeError:
        return False


# -- The sidebar chat panel: find its room on screen ----------------------------------
# BFACW_PT_chat_compact marks the top and bottom of the room it reserves with
# small preview images (left, centre, right).  Reading a thin pixel strip (and
# one row) back from the drawn region finds them wherever the panel is: dragged,
# pinned to another tab, scrolled or zoomed.  The markers are the theme's panel
# colour nudged by a few steps -- invisible, but exact -- and previews never
# highlight on hover.  Top and bottom differ, so a marker clipped away during
# the panel's open / close animation is never mistaken for the other one.
_MARKERS: dict[str, Any] = {"previews": None, "colors": None}
_NUDGE = 3


def _panel_back_bytes() -> tuple[int, int, int]:
    try:
        back = bpy.context.preferences.themes[0].user_interface.panel_back
        return tuple(int(round(min(1.0, max(0.0, c)) * 255)) for c in back[:3])  # type: ignore[return-value]
    except (AttributeError, IndexError):
        return (66, 66, 66)


def marker_colors() -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(top, bottom) marker colours: the panel colour, nudged in blue / green."""
    r, g, b = _panel_back_bytes()
    top = (r, g, b + _NUDGE if b + _NUDGE <= 255 else b - _NUDGE)
    bottom = (r, g + _NUDGE if g + _NUDGE <= 255 else g - _NUDGE, b)
    return top, bottom


def marker_icons() -> tuple[int, int]:
    """Icon ids for the top and bottom marker previews (rebuilt when the theme changes)."""
    colors = marker_colors()
    previews = _MARKERS["previews"]
    if previews is None or _MARKERS["colors"] != colors:
        import bpy.utils.previews  # pylint: disable=import-error
        if previews is not None:
            bpy.utils.previews.remove(previews)
        previews = bpy.utils.previews.new()
        for name, rgb in zip(("top", "bottom"), colors):
            preview = previews.new(name)
            pixels = [rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0] * (16 * 16)
            preview.icon_size = (16, 16)
            preview.icon_pixels_float = pixels
            preview.image_size = (16, 16)
            preview.image_pixels_float = pixels
        _MARKERS["previews"], _MARKERS["colors"] = previews, colors
    return previews["top"].icon_id, previews["bottom"].icon_id


def draw_marker_row(layout: Any, which: str) -> None:
    """One marker row (left, centre, right), drawn by the chat panel."""
    icon = marker_icons()[0 if which == "top" else 1]
    row = layout.row()
    row.scale_y = 0.5
    split = row.split(factor=1.0 / 3.0)
    left = split.row()
    left.alignment = "LEFT"
    left.template_icon(icon_value=icon, scale=0.5)
    rest = split.split(factor=0.5)
    middle = rest.row()
    middle.alignment = "CENTER"
    middle.template_icon(icon_value=icon, scale=0.5)
    right = rest.row()
    right.alignment = "RIGHT"
    right.template_icon(icon_value=icon, scale=0.5)


def _free_markers() -> None:
    if _MARKERS["previews"] is not None:
        import bpy.utils.previews  # pylint: disable=import-error
        bpy.utils.previews.remove(_MARKERS["previews"])
    _MARKERS["previews"] = _MARKERS["colors"] = None


def _near(px: Any, rgb: tuple[int, int, int]) -> bool:
    return abs(px[0] - rgb[0]) <= 1 and abs(px[1] - rgb[1]) <= 1 and abs(px[2] - rgb[2]) <= 1


def _runs(values: list[bool]) -> list[tuple[int, int]]:
    out, start = [], None
    for i, on in enumerate(values + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            out.append((start, i - 1))
            start = None
    return out


# The room's full height (region pixels per zoom unit), learnt while both markers
# are on screen, so a half-scrolled panel keeps its layout and just scrolls.
_ROOM_H: dict[int, float] = {}


def _find_chat_rect(region: Any, expected_h: float, tabs_px: float, zoom: float = 1.0) -> Any:
    """The panel's whole room in region pixels (it may reach past the region's edges
    when scrolled; the region clips it, like any panel), or None this frame."""
    import gpu  # pylint: disable=import-error
    top_rgb, bottom_rgb = marker_colors()
    fb = gpu.state.active_framebuffer_get()
    height, width = int(region.height), int(region.width)
    # A thin strip around the panel's centre, where the centre markers sit.
    centre = int((width - tabs_px) / 2)
    strip_w = 24
    x0 = max(0, min(width - strip_w, centre - strip_w // 2))
    strip = fb.read_color(x0, 0, strip_w, height, 4, 0, "UBYTE").to_list()
    top_rows = [y for y in range(height) if any(_near(px, top_rgb) for px in strip[y])]
    bottom_rows = [y for y in range(height) if any(_near(px, bottom_rgb) for px in strip[y])]
    key = region.as_pointer()
    known = _ROOM_H.get(key)
    room_h = known * zoom if known else expected_h
    if top_rows and bottom_rows:
        top, bottom = max(top_rows) + 1, min(bottom_rows)
        _ROOM_H[key] = (top - bottom) / max(zoom, 1e-6)
    elif top_rows:          # bottom unseen: scrolled off the region, or clipped mid-animation
        top = max(top_rows) + 1
        bottom = top - room_h
        if bottom > 2:
            return None     # it should be on screen: the panel is animating, skip this frame
    elif bottom_rows:       # top scrolled away above the region
        bottom = min(bottom_rows)
        top = bottom + room_h
        if top < height - 2:
            return None
    else:
        # Neither marker on screen (#198): the room is taller than the sidebar and scrolled
        # to its middle.  Find it again from where it sits in the view (scrolling doesn't
        # move it there), and only if the whole strip really is an empty panel room.
        return _room_from_view(region, key, strip, height, room_h)
    probe_y, rgb = (top_rows[-1], top_rgb) if top_rows else (bottom_rows[0], bottom_rgb)
    row = fb.read_color(0, probe_y, width, 1, 4, 0, "UBYTE").to_list()[0]
    across = _runs([_near(px, rgb) for px in row])
    if len(across) < 2:
        return None
    left, right = across[0][0], across[-1][1] + 1
    # Full width: the markers sit inside the panel's padding; grow the room out to the
    # panel's edges, through the panel's own colour as drawn just beside the markers.
    if left > 0:
        inside = row[left - 1]
        while left > 0 and _near(row[left - 1], inside):
            left -= 1
    if right < width:
        inside = row[right]
        while right < width and _near(row[right], inside):
            right += 1
    try:    # remember the room in view space, for when both markers are scrolled away
        _ROOM_VIEW[key] = (region.view2d.region_to_view(0.0, float(top))[1], float(left), float(right))
    except (AttributeError, RuntimeError):
        pass
    return gpu_layout.Rect(float(left), float(bottom), float(right - left), float(top - bottom))


# Where the room's top is in the region's view (scroll-independent), with its left / right.
_ROOM_VIEW: dict[int, tuple[float, float, float]] = {}


def _room_from_view(region: Any, key: int, strip: list, height: int, room_h: float) -> Any:
    known = _ROOM_VIEW.get(key)
    if known is None or not room_h:
        return None
    view_top, left, right = known
    try:
        top = float(region.view2d.view_to_region(0.0, view_top, clip=False)[1])
    except (AttributeError, RuntimeError):
        return None
    bottom = top - room_h
    if not (top >= height - 2 and bottom <= 2):
        return None             # it should show a marker then: collapsed, moved or mid-animation
    # The whole visible strip must be the empty room (panel background), not other panels.
    back = _panel_back_bytes()
    plain = sum(1 for row in strip if any(abs(px[0] - back[0]) <= 2 and abs(px[1] - back[1]) <= 2
                                          and abs(px[2] - back[2]) <= 2 for px in row))
    if plain < height * 0.6:
        return None
    return gpu_layout.Rect(left, bottom, right - left, top - bottom)


def _draw_sidebar() -> None:
    from . import ui_chat
    context = bpy.context
    region = context.region
    if region is None or region.type != "UI" or not ui_chat._compact_sidebar(context):
        return
    props = _props()
    zoom = _view_zoom(region)
    unit = 20.0 * context.preferences.system.ui_scale * zoom
    tabs = _TAB_STRIP_PX * context.preferences.view.ui_scale
    rect = _find_chat_rect(region, (getattr(props, "chat_panel_rows", 22.0)) * unit, tabs, zoom)
    if rect is None:
        entry = _SURFACES.get(region.as_pointer())
        if entry is not None:
            entry[0].bounds = None        # collapsed / scrolled away: no clicks go to it
        return
    # Fit the chat to the part of its room that is on screen (#182): a sidebar shorter
    # than the panel keeps the top bar and the input visible, and the messages shrink
    # between them instead of spilling over the top bar.
    bottom, top = max(rect.y, 0.0), min(rect.top, float(region.height))
    if top - bottom < unit * 3:
        entry = _SURFACES.get(region.as_pointer())
        if entry is not None:
            entry[0].bounds = None
        return
    rect = gpu_layout.Rect(rect.x, bottom, rect.w, top - bottom)
    surface = _surface(region, compact=True)
    surface.on_resize = lambda new_h, _unit=unit: _resize_panel(new_h, _unit)
    _draw(region, rect, compact=True, zoom=zoom)


def _resize_panel(height_px: float, unit_px: float) -> None:
    """The chat panel's bottom edge was dragged (called from the input modal)."""
    props = _props()
    if props is not None and unit_px > 0:
        rows = max(6.0, min(80.0, height_px / unit_px))
        if abs(rows - props.chat_panel_rows) > 0.05:
            props.chat_panel_rows = rows
            _ROOM_H.clear()     # learn the new height when both markers show again


def _router(context: Any, event: Any) -> tuple[ChatSurface, Any, Any] | None:
    """The chat surface under the mouse, if any."""
    window = context.window
    if window is None:
        return None
    from . import ui_chat
    prefs = _prefs(context)
    for area in window.screen.areas:
        if not (area.x <= event.mouse_x < area.x + area.width and area.y <= event.mouse_y < area.y + area.height):
            continue
        coworker = area.type == "NODE_EDITOR" and is_coworker_space(area.spaces.active)
        if not coworker and not ui_chat.docker_area(area, prefs):
            return None
        for region in area.regions:
            if not (region.x <= event.mouse_x < region.x + region.width
                    and region.y <= event.mouse_y < region.y + region.height):
                continue
            if coworker and region.type == "WINDOW" or not coworker and region.type == "UI":
                entry = _SURFACES.get(region.as_pointer())
                if entry is None:
                    return None
                surface = entry[0]
                bounds = surface.bounds
                inside = bounds is not None and bounds.contains(event.mouse_x - region.x, event.mouse_y - region.y)
                if inside or surface.pressed is not None or surface.box.focused:
                    if inside and getattr(event, "value", "") == 'PRESS':
                        # This chat is used: its thread's image goes in the window's socket (#417).
                        with bpy.context.temp_override(window=window, area=area, region=region):
                            ui_chat._focus_thread(ui_chat._ui_thread(bpy.context))
                    return surface, area, region
                return None
        return None
    return None


# -- Actions ----------------------------------------------------------------------------

def _window_override(region: Any = None) -> dict:
    """The window (and area) *region* is in, so an action from a floating window
    happens there, not in the main one.  No screen: the window brings its own, and
    overriding the screen fails while a window is maximized (#391)."""
    wm = bpy.context.window_manager
    if region is not None:
        try:
            for window in wm.windows:
                for area in window.screen.areas:
                    if any(r == region for r in area.regions):
                        return {"window": window, "area": area, "region": region}
        except ReferenceError:
            pass
    window = bpy.context.window or (wm.windows[0] if wm.windows else None)
    return {"window": window} if window is not None else {}


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
        from . import log
        wm.clipboard = log.redact(value)        # keys and tokens as *** (#346)
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
    elif action == "open_editor":           # the full Coworker editor, on this chat (#427)
        ops.thread_open_editor()
    elif action == "draft" and props is not None:
        if props.chat_input != value:
            props.chat_input = value
            _tag_chat_regions()         # the other chats show the new draft at once
    elif action == "suggest_type" and surface is not None:     # "No, ..." to finish yourself (#270)
        surface.box.model.set_text(value)
        if props is not None:
            props.chat_input = value
        if region is not None:
            input_capture.focus(surface.box, region)
    elif action == "suggest":       # a suggestion button (#220): sent as if typed
        _do("send", value, surface, region)
    elif action == "mention":
        ops.mention_search("INVOKE_DEFAULT")
    elif action == "approval_menu":            # the Permissions dropdown: Strict / Permissive / Auto
        bpy.ops.wm.call_panel(name="BFACW_PT_approval_popover", keep_open=True)
    elif action == "attach_menu":               # every attach names this chat's tab (#449, #454)
        from . import ui_chat
        ui_chat.open_attach_popover(context)
    elif action == "attach_render":
        from . import ui_chat
        ops.chat_capture_render(thread_id=ui_chat._ui_thread(context).id)
    elif action == "attach_screen":
        from . import ui_chat
        ops.chat_capture_screen("INVOKE_DEFAULT", thread_id=ui_chat._ui_thread(context).id)
    elif action == "paste" and surface is not None:     # Ctrl V: an image attaches, else text (#450)
        _paste_into(surface.box, value)
    elif action == "open_image":
        _open_image(value)
    elif action == "attachment_pin" and props is not None:
        props.chat_image_send_once = not props.chat_image_send_once
    elif action == "attachment_remove" and props is not None:
        props.chat_image = None
    elif action == "chat_mode" and props is not None:
        props.chat_mode = value
    elif action == "approve":
        ops.agent_approve(choice=value)
    elif action == "plan_card":                 # Start the Plan / Keep Refining (#241)
        ops.plan_card(choice=value)
        if value == "refine" and surface is not None and region is not None:
            input_capture.focus(surface.box, region)
    elif action == "answer":
        ops.agent_answer(choice=value)
    elif action == "mode_menu":
        bpy.ops.wm.call_panel(name="BFACW_PT_mode_popover", keep_open=True)     # #235
    elif action == "revert_turn":
        ops.revert_turn()
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
        elif value == "copy_log":
            from . import activity
            wm.clipboard = activity.log_text()


def _flush() -> None:
    pending = list(_PENDING)
    _PENDING.clear()
    for action, value, key in pending:
        entry = _SURFACES.get(key)
        surface, region = entry if entry is not None else (None, None)
        try:
            with bpy.context.temp_override(**_window_override(region)):
                _do(action, value, surface, region)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] chat action {:s} failed: {:s}".format(action, str(ex)))
    return None


def _region_for_box(box: InputBox) -> Any:
    return next((r for s, r in _SURFACES.values() if s.box is box), None)


def _box_override(box: InputBox) -> dict:
    """The window, area and region *box* is drawn in.  The keyboard modal keeps the
    context it was started in, so without this a key typed in a second chat (or on
    another tab) acts on the first chat's area: its tab's draft and its Send."""
    return _window_override(_region_for_box(box))


def _paste_image(context: Any, text: str, thread: Any, op: Any = None, force: bool = False) -> bool:
    """Attach what the clipboard holds to *thread*'s chat (#450): the image file its text
    names, else its picture (with no text, or *force*).  False: nothing image-like, and
    the caller pastes *text* as before.  *thread* is found when the paste began, from the
    box's (or the menu's) area; *op* reports "Attached ..."."""
    from . import chat_attachments, ui_chat
    try:
        image = chat_attachments.clipboard_image(text, force=force, context=context)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] paste image: {:s}".format(str(ex)))
        return False
    if image is None:
        return False
    ui_chat._attach_to_socket(context, op, image, thread)
    _tag_chat_regions()
    return True


def _paste_into(box: InputBox, text: str) -> None:
    """Ctrl V (or the menu's Paste) in *box*, run in its area: an image attaches to its
    chat, anything else is pasted as text, as before (#450)."""
    from . import ui_chat
    context = bpy.context
    if not _paste_image(context, text, ui_chat._ui_thread(context)):
        box.paste(text)


def _on_box_action(box: InputBox, action: str, value: str) -> None:
    """Keyboard actions (Enter, Esc...) from input_capture, in the box's own area."""
    surface = _surface_for_box(box)
    if surface is None and action == "paste":     # a box without a chat: text only
        box.paste(value)
        return
    try:
        with bpy.context.temp_override(**_box_override(box)):
            _do(action, value, surface, _region_for_box(box))
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] chat action {:s} failed: {:s}".format(action, str(ex)))


def _after_key(box: InputBox) -> None:
    """Keep the draft of the tab this box shows in step with what was typed."""
    props = _props()
    if props is not None:
        with bpy.context.temp_override(**_box_override(box)):
            if props.chat_input != box.model.text:
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
        area.spaces.active.show_region_ui = True
        area.spaces.active.show_region_toolbar = True
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


class BFACW_OT_chat_attach_image(bpy.types.Operator):
    """Pick an image file to send with the next message"""

    bl_idname = "bfacw.chat_attach_image"
    bl_label = "Attach Image"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})  # type: ignore[valid-type]
    filter_image: bpy.props.BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})  # type: ignore[valid-type]
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})  # type: ignore[valid-type]
    # The chat it attaches to (#449): set by the button, else found when the file browser opens,
    # since the file browser hands execute no area to find it from.
    thread_id: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})  # type: ignore[valid-type]

    def invoke(self, context, _event):
        from . import ui_chat
        self.thread_id = ui_chat._attach_thread(context, self.thread_id).id
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import chat_attachments, ui_chat
        image = chat_attachments.load_image_file(self.filepath)
        if image is None:
            self.report({"WARNING"}, "Blender can't read this file as an image")
            return {"CANCELLED"}
        ui_chat._attach_to_socket(context, self, image, ui_chat._attach_thread(context, self.thread_id))
        _tag_chat_regions()
        return {"FINISHED"}


class BFACW_OT_chat_paste_image(bpy.types.Operator):
    """Attach the image on the clipboard to the next message: a copied picture or a copied image file path"""

    bl_idname = "bfacw.chat_paste_image"
    bl_label = "Paste Image"
    # The chat it attaches to (#450): set by the popover's button, else the chat of the area.
    thread_id: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})  # type: ignore[valid-type]

    def execute(self, context):
        from . import ui_chat
        thread = ui_chat._attach_thread(context, self.thread_id)
        if not _paste_image(context, context.window_manager.clipboard or "", thread, op=self, force=True):
            self.report({"WARNING"}, "No image on the clipboard")
            return {"CANCELLED"}
        return {"FINISHED"}


class BFACW_OT_editor_native_edit(bpy.types.Operator):
    """Edit the message in a native text field (for Chinese, Japanese or Korean input)"""

    bl_idname = "bfacw.editor_native_edit"
    bl_label = "Edit in Text Field"

    def invoke(self, context, _event):
        area = context.area
        for region in (area.regions if area else ()):
            entry = _SURFACES.get(region.as_pointer())
            if entry is not None and not entry[0].compact:
                input_capture.focus(entry[0].box, region)
                return bpy.ops.bfacw.gpu_input_native("INVOKE_DEFAULT")
        return {"CANCELLED"}


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
        from . import ui_chat
        layout = self.layout
        ui_chat.draw_new_chat_entries(layout, context)      # New Chat, for This <editor>, Start Fresh (#466)
        layout.operator("bfacw.chat_stop", text="Stop", icon="PAUSE")
        layout.separator()
        layout.operator("bfacw.mention_search", text="@ Mention", icon="OUTLINER_OB_MESH")
        layout.operator("bfacw.editor_native_edit", text="Edit in Text Field (IME)", icon="GREASEPENCIL")
        layout.separator()
        layout.operator("bfacw.chat_attach_image", text="Attach Image...", icon="FILE_IMAGE")
        layout.operator("bfacw.chat_paste_image", text="Paste Image", icon="PASTEDOWN")
        layout.operator("bfacw.chat_capture_render", text="Attach Render", icon="RENDER_STILL")
        layout.operator("bfacw.chat_capture_screen", text="Attach Screen", icon=safe_icon("MAKE_SCREENSHOT"))
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


# -- The right-click menu (#453) ------------------------------------------------------
# input_capture opens it over a chat (the editor's or a sidebar's) in that chat's area and
# region, so its entries -- the existing operators -- act on the chat that was clicked.

_COPY_WHAT = [("MESSAGE", "Message", "Copy the message under the mouse, like its Copy button"),
              ("CODE", "Code", "Copy the code block under the mouse"),
              ("CHAT", "Whole Chat", "Copy this chat: your messages and Coworker's replies, without the tool steps")]


class BFACW_OT_chat_context_copy(bpy.types.Operator):
    """Copy from the chat that was right-clicked"""

    bl_idname = "bfacw.chat_context_copy"
    bl_label = "Copy"
    bl_options = {"INTERNAL"}

    what: bpy.props.EnumProperty(items=_COPY_WHAT, default="MESSAGE", options={"SKIP_SAVE"})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties):
        return next(d for ident, _n, d in _COPY_WHAT if ident == properties.what)

    def execute(self, context):
        from . import log
        from .ui_gpu.chat_view import transcript
        turn = input_capture.menu_target()["turn"] or {}
        text = (transcript(_history(context)) if self.what == "CHAT"
                else turn.get("code", "") if self.what == "CODE" else turn.get("text", ""))
        if not text:
            return {"CANCELLED"}
        context.window_manager.clipboard = log.redact(text)        # keys and tokens as *** (#346)
        return {"FINISHED"}


_EDIT_WHAT = [("cut", "Cut", "Cut the selected text (Ctrl X)"), ("copy", "Copy", "Copy the selected text (Ctrl C)"),
              ("paste", "Paste", "Paste at the cursor; a copied picture or image file attaches instead (Ctrl V)"),
              ("select_all", "Select All", "Select the whole message (Ctrl A)")]


class BFACW_OT_chat_context_edit(bpy.types.Operator):
    """Edit the message you are typing in the chat that was right-clicked"""

    bl_idname = "bfacw.chat_context_edit"
    bl_label = "Edit Message"
    bl_options = {"INTERNAL"}

    action: bpy.props.EnumProperty(items=_EDIT_WHAT, default="paste", options={"SKIP_SAVE"})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties):
        return next(d for ident, _n, d in _EDIT_WHAT if ident == properties.action)

    def execute(self, context):
        target = input_capture.menu_target()
        surface = target["surface"]
        if surface is None:
            return {"CANCELLED"}
        wm = context.window_manager
        from . import ui_chat
        if self.action == "paste" and _paste_image(context, wm.clipboard or "", ui_chat._ui_thread(context)):
            input_capture.focus(surface.box, target["region"])     # as Ctrl V: the image attaches (#450)
        else:
            copied = input_capture.menu_edit(self.action, wm.clipboard or "")
            if copied is not None:
                wm.clipboard = copied           # as Ctrl C / X do: your own text, as typed
        _after_key(surface.box)                 # the tab's draft follows
        try:
            target["region"].tag_redraw()
        except (AttributeError, ReferenceError):
            pass
        return {"FINISHED"}


class BFACW_MT_chat_context(bpy.types.Menu):
    bl_idname = "BFACW_MT_chat_context"
    bl_label = "Coworker Chat"

    def draw(self, context):
        from . import ui_chat
        layout = self.layout
        target = input_capture.menu_target()
        surface, turn = target["surface"], target["turn"]
        if target["widget"] == "input" and surface is not None:
            selected = bool(surface.box.model.selected_text())
            for action, label, icon, enabled in (("cut", "Cut", safe_icon("CUT"), selected),
                                                 ("copy", "Copy", "COPYDOWN", selected),
                                                 ("paste", "Paste", "PASTEDOWN", True),
                                                 ("select_all", "Select All", safe_icon("SELECT_ALL"), True)):
                row = layout.row()
                row.enabled = enabled
                row.operator("bfacw.chat_context_edit", text=label, icon=icon).action = action
            layout.separator()
        elif turn:
            if turn.get("text"):
                layout.operator("bfacw.chat_context_copy", text="Copy Message", icon="COPYDOWN").what = "MESSAGE"
            if turn.get("code"):
                layout.operator("bfacw.chat_context_copy", text="Copy Code", icon="SCRIPT").what = "CODE"
            if turn.get("last") and bpy.ops.bfacw.revert_turn.poll():
                layout.operator("bfacw.revert_turn", text="Revert", icon="LOOP_BACK")
            layout.separator()
        layout.operator("bfacw.chat_attach_image", text="Attach Image...", icon="FILE_IMAGE")
        layout.operator("bfacw.chat_paste_image", text="Paste Image", icon="PASTEDOWN")
        layout.operator("bfacw.chat_capture_screen", text="Capture Editor", icon=safe_icon("MAKE_SCREENSHOT"))
        layout.operator("bfacw.chat_capture_render", text="Capture Render", icon="RENDER_STILL")
        layout.separator()
        ui_chat.draw_new_chat_entries(layout, context)      # New Chat, for This <editor>, Start Fresh (#466)
        if agent_controller.thread_state(ui_chat._ui_thread(context)).is_thinking:
            layout.operator("bfacw.chat_stop", text="Stop", icon="PAUSE")
        layout.operator("bfacw.chat_context_copy", text="Copy Whole Chat", icon="COPYDOWN").what = "CHAT"
        layout.operator("wm.call_panel", text="History", icon="TIME").name = "BFACW_PT_chat_history"
        layout.separator()
        layout.operator("bfacw.open_addon_prefs", text="Preferences", icon="PREFERENCES")


def _area_view_setting(area: Any, name: str, default: Any) -> Any:
    for region in (area.regions if area else ()):
        entry = _SURFACES.get(region.as_pointer())
        if entry is not None:
            return getattr(entry[0].view, name, default)
    return default


# The header's widths, in UI units (20 px at scale 1).  Python can't measure text: about
# a third of a unit a character, as the tab strip assumes, plus the icon and the padding.
_CHAR_W = 0.35
_ICON_W = 1.0
_PAD_W = 0.6
_TABS_MIN = 8.0             # the tab strip at its smallest: a few icon tabs and its x, + and menu
_TAB_EXTRA_W = 0.4          # a tab button is a little wider than the strip's own estimate
_STATUS_MAX = 60            # characters


def _text_w(text: str, icon: bool = True) -> float:
    return _CHAR_W * len(text) + (_ICON_W if icon else 0.0) + _PAD_W


def _header_fit(units: float, fixed: float, modes_full: float, modes_icon: float, status_len: int,
                tabs_need: float) -> tuple[float, bool, int]:
    """How the header fits in *units*: the room for the tab strip, the chat mode as icons
    only, and how many characters of the status show.

    *fixed* is what always shows whole (editor type, Chat, View, the mode button,
    Start / Stop, the display dropdown, Preferences).  The tab strip keeps what it needs up
    to two fifths of the header (never under its smallest, so its x, + and menu stay);
    then the chat mode goes to icons and the status is cut before that room is touched.
    The tabs get whatever is left over."""
    free = units - fixed
    floor = max(_TABS_MIN, min(tabs_need, 0.4 * units))
    room = free - floor
    status_len = min(status_len, _STATUS_MAX)
    status_full = _ICON_W + _CHAR_W * status_len + (_PAD_W if status_len else 0.0)
    icon_only = bool(modes_full) and room < modes_full + status_full
    modes = modes_icon if icon_only else modes_full
    if room - modes >= status_full:
        chars = status_len
    else:
        chars = max(0, int((room - modes - _ICON_W - _PAD_W) / _CHAR_W))
        chars = chars if chars >= 4 else 0          # a stub of text says nothing: the icon only
    status_w = _ICON_W + (_CHAR_W * chars + _PAD_W if chars else 0.0)
    return max(_TABS_MIN, free - modes - status_w), icon_only, chars


def _cut(text: str, chars: int) -> str:
    return text if len(text) <= chars else (text[:chars - 1].rstrip() + "\u2026" if chars else "")


def _tabs_need() -> float:
    """What the tab strip would take with every title shown (its own estimate, #427)."""
    from . import ui_chat
    labels = [ui_chat._tab_label(t) for t in agent_controller.THREADS.live()]
    return sum(0.33 * len(label) + 0.2 + _ICON_W + _TAB_EXTRA_W for label in labels) + 3.5


def _draw_header(self: Any, context: Any) -> None:
    """Editor type, Chat, View | the chat tabs | mode, status, Start, display, Preferences (#467)."""
    layout = self.layout
    layout.template_header()
    row = layout.row(align=True)
    row.menu("BFACW_MT_editor_chat")
    row.menu("BFACW_MT_editor_view")
    info = _status(context)
    props = _props()
    modes = info.chat_modes if props is not None else ()
    if info.running:
        start_text = "Stop Bridge" if info.start_label == "Start Bridge" else "Stop"
    else:
        start_text = info.start_label
    fixed = (2.0 + _text_w("Chat", False) + _text_w("View", False)          # editor type, menus
             + _text_w(info.mode_label) + 0.6 + (_ICON_W + _PAD_W if info.error else 0.0)
             + _text_w(start_text) + 2.0 + _ICON_W + _PAD_W                # display dropdown, prefs
             + 2.0)                                                         # gaps between the groups
    modes_full = sum(_text_w(m[1]) for m in modes) + (0.5 if modes else 0.0)
    modes_icon = (len(modes) * (_ICON_W + 0.4) + 0.5) if modes else 0.0
    scale = context.preferences.system.ui_scale
    units = context.region.width / (20.0 * scale)
    tabs_units, icon_only, chars = _header_fit(units, fixed, modes_full, modes_icon, len(info.status),
                                               _tabs_need())
    # The strip's estimate leaves out what Blender adds to each tab button.
    tabs_units = max(_TABS_MIN, tabs_units - _TAB_EXTRA_W * len(agent_controller.THREADS.live()))
    # The chat tabs (#427) in the middle (#467).
    from . import ui_chat
    layout.separator_spacer()
    ui_chat.draw_chat_tabs(layout, context, units=tabs_units)
    layout.separator_spacer()
    if modes:
        layout.prop(props, "chat_mode", expand=True, icon_only=icon_only)
        layout.separator()
    status = layout.row(align=True)
    # The mode opens the same popover as the sidebar chat's mode button (#235).
    status.popover("BFACW_PT_mode_popover", text=info.mode_label, icon=info.mode_icon)
    status.label(text=_cut(info.status, chars), icon=info.status_icon)
    if info.error:
        status.operator("bfacw.copy_status_error", text="", icon="COPYDOWN")
    if info.running:
        layout.operator("bfacw.agent_stop", text=start_text, icon="X")
    else:
        layout.operator("bfacw.agent_start", text=start_text, icon="PLAY")
    layout.separator()
    # View settings, like the 3D Viewport's Overlays dropdown (#467).
    layout.popover(panel="BFACW_PT_editor_display", text="", icon=safe_icon("OVERLAY"))
    layout.operator("bfacw.open_addon_prefs", text="", icon="PREFERENCES")


class BFACW_PT_editor_display(bpy.types.Panel):
    """How the chat shows: the thumbnail size (room for more view settings)"""

    bl_space_type = "NODE_EDITOR"
    bl_region_type = "HEADER"
    bl_label = "Chat Display"
    bl_idname = "BFACW_PT_editor_display"
    bl_ui_units_x = 10

    @classmethod
    def poll(cls, context):
        return _is_coworker(context)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Thumbnail Size")
        current = _area_view_setting(context.area, "thumb_size", "M")
        row = layout.row(align=True)
        for size, text in (("S", "Small"), ("M", "Medium"), ("L", "Large")):
            row.operator("bfacw.editor_thumbs", text=text, depress=current == size).size = size


# -- Toolshelf and sidebar --------------------------------------------------------------

class _CoworkerPanel:
    bl_space_type = "NODE_EDITOR"
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return _is_coworker(context)


# The toolshelf snaps like Bforartists' tabbed toolshelves: 1 / 2 / 3 columns of
# icon buttons as it widens, then icon + text.  Bforartists' own helpers
# (bl_ui.space_toolsystem_common) are used when present; Blender gets the same
# logic here.
_SEPARATOR = object()


def _column_count(region: Any) -> int:
    """1-3 icon columns, or 4 = wide enough for text (Bforartists' thresholds)."""
    try:
        from bl_ui.space_toolsystem_common import toolsystem_column_count  # pylint: disable=import-error
        return toolsystem_column_count(region)
    except ImportError:
        pass
    view2d = region.view2d
    scale = view2d.region_to_view(1.0, 0.0)[0] - view2d.region_to_view(0.0, 0.0)[0]
    width = region.width * scale / bpy.context.preferences.system.ui_scale
    return 4 if width > 200.0 else 3 if width > 160.0 else 2 if width > 120.0 else 1


def _draw_tool_entries(layout: Any, context: Any, entries: list) -> None:
    """*entries*: (operator, text, icon, props) tuples or _SEPARATOR."""
    columns = _column_count(context.region)
    if columns == 4:                                 # wide: big buttons with text
        col = layout.column(align=True)
        col.scale_y = 2.0
        for entry in entries:
            if entry is _SEPARATOR:
                col.separator(factor=0.5)
                continue
            operator, text, icon, props = entry
            op = col.operator(operator, text=text, icon=icon)
            for key, value in props.items():
                setattr(op, key, value)
        return
    col = layout.column(align=True)                  # narrow: rows of square icons
    row, index = None, 0
    for entry in entries:
        if entry is _SEPARATOR:
            col = layout.column(align=True)
            row, index = None, 0
            continue
        if index == 0:
            row = col.row(align=True)
            row.scale_x = row.scale_y = 2.0
            row.alignment = "LEFT"
        operator, text, icon, props = entry
        op = row.operator(operator, text="", icon=icon)
        for key, value in props.items():
            setattr(op, key, value)
        index = (index + 1) % columns


class _ToolPanel(_CoworkerPanel):
    bl_region_type = "TOOLS"
    # Only Bforartists' toolshelf has tabs: Blender refuses a category there (#456).
    bl_category = CATEGORY if _IS_BFA else ""

    def entries(self, context) -> list:
        return []

    def draw(self, context):
        _draw_tool_entries(self.layout, context, self.entries(context))


class BFACW_PT_editor_tools_chat(_ToolPanel, bpy.types.Panel):
    """Mention, stop (a new thread is the tabs' +, #427)"""

    bl_label = "Chat"
    bl_idname = "BFACW_PT_editor_tools_chat"

    def entries(self, context):
        from . import ui_chat
        out = [("bfacw.mention_search", "@ Mention", "OUTLINER_OB_MESH", {})]
        if ui_chat._ui_state(context).is_thinking:
            out.append(("bfacw.chat_stop", "Stop", "PAUSE", {}))
        return out


class BFACW_PT_editor_tools_attach(_ToolPanel, bpy.types.Panel):
    """Attach an image to the next message"""

    bl_label = "Attach"
    bl_idname = "BFACW_PT_editor_tools_attach"

    def entries(self, context):
        return [("bfacw.chat_attach_image", "Attach Image...", "FILE_IMAGE", {}),
                ("bfacw.chat_paste_image", "Paste Image", "PASTEDOWN", {}),
                ("bfacw.chat_capture_render", "Attach Render", "RENDER_STILL", {}),
                ("bfacw.chat_capture_screen", "Attach Screen", safe_icon("MAKE_SCREENSHOT"), {})]

    # The image picker itself is the input row's popover (#265): just the buttons here.


class BFACW_PT_editor_tools_session(_ToolPanel, bpy.types.Panel):
    """The plan, project rules and the session log"""

    bl_label = "Session"
    bl_idname = "BFACW_PT_editor_tools_session"

    def entries(self, context):
        return [("bfacw.plan_open", "Goal & Plan", "CHECKBOX_HLT", {}),
                ("bfacw.edit_rules", "Project Rules", "TEXT", {}),
                ("bfacw.copy_session_log", "Copy Session Log", "COPYDOWN", {}),
                ("bfacw.export_session_log", "Export Session Log", "EXPORT", {})]


class BFACW_PT_editor_chat(_CoworkerPanel, bpy.types.Panel):
    """The chat's controls, as in the 3D View's Coworker tab"""

    bl_label = "Coworker"
    bl_idname = "BFACW_PT_editor_chat"
    bl_region_type = "UI"
    bl_order = 0

    def draw(self, context):
        from . import ui_chat
        # The plan is its own panel (Plan, under Queue), the image a picker (#171).
        ui_chat.draw_chat_controls(self.layout, context, show_chat_mode=False, has_header=True,
                                   show_image=False)      # on the input's button row (#213)


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
    sources = (ui_chat.BFACW_PT_chat_queue, ui_chat.BFACW_PT_chat_plan, ui_chat.BFACW_PT_chat_history,
               ui_chat.BFACW_PT_chat_status, ui_chat.BFACW_PT_chat_session, ui_chat.BFACW_PT_chat_session_context,
               ui_chat.BFACW_PT_chat_session_memory)
    # The editor's own order and open state (#212); the sidebar's differ.
    layout = {"BFACW_PT_chat_queue": (1, set()), "BFACW_PT_chat_plan": (2, set()),
              "BFACW_PT_chat_history": (3, set()), "BFACW_PT_chat_status": (4, {"DEFAULT_CLOSED"})}
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
        if cls.bl_idname in layout:
            attrs["bl_order"], attrs["bl_options"] = layout[cls.bl_idname]
        for name in ("draw", "draw_header"):
            if name in cls.__dict__:
                attrs[name] = cls.__dict__[name]
        parent = getattr(cls, "bl_parent_id", "")
        if parent:
            attrs["bl_parent_id"] = ids[parent]
        out.append(type(ids[cls.bl_idname], (bpy.types.Panel,), attrs))
    return out


def _sidebar_copies() -> list[type]:
    """The 3D View's sidebar tab, registered again for every other editor with a sidebar (#291):
    the compact chat, its controls, Queue, Plan, History and Status (with Session)."""
    from . import ui_chat
    sources = (ui_chat.BFACW_PT_chat_compact, ui_chat.BFACW_PT_chat_controls, ui_chat.BFACW_PT_chat_queue,
               ui_chat.BFACW_PT_chat_plan, ui_chat.BFACW_PT_chat_history, ui_chat.BFACW_PT_chat_status,
               ui_chat.BFACW_PT_chat_session, ui_chat.BFACW_PT_chat_session_context,
               ui_chat.BFACW_PT_chat_session_memory)
    out = []
    for space, _cls_name, short, _icon in ui_chat.SIDEBAR_SPACES:
        if space == "VIEW_3D":
            continue                # the originals live there
        ids = {cls.bl_idname: cls.bl_idname.replace("BFACW_PT_chat_", "BFACW_PT_{:s}_chat_".format(short))
               for cls in sources}
        for cls in sources:
            attrs = {"bl_idname": ids[cls.bl_idname], "bl_space_type": space, "bl_region_type": "UI",
                     "bl_category": CATEGORY, "bl_label": cls.bl_label, "__doc__": cls.__doc__}
            if space == "NODE_EDITOR":      # not inside the Coworker editor: it has its own
                attrs["poll"] = classmethod(_poll_outside_coworker(None, cls.poll))
            elif "poll" in cls.__dict__:
                attrs["poll"] = cls.__dict__["poll"]
            for name in ("bl_options", "bl_order", "draw", "draw_header", "draw_header_preset"):
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
    _quiet_other_drop_handlers()
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


def _over_chat(context: Any) -> bool:
    """A file drag is over a Coworker chat (the editor, or the sidebar chat tab)."""
    try:
        if _is_coworker(context):
            return True
        from . import ui_chat
        return ui_chat._compact_sidebar(context) and ui_chat._is_coworker_panel_region(context)
    except (AttributeError, ReferenceError):
        return False


_WRAPPED_DROPS: list[tuple[type, Any]] = []


def _drop_outside_chat(fn: Any) -> Any:
    # A closure: Blender calls poll_drop(cls, context) and dislikes extra default args.
    def poll_drop(cls, context):
        if _over_chat(context):
            return False
        return fn(cls, context)
    return poll_drop


def _quiet_other_drop_handlers() -> None:
    """Over the chat only "attach" makes sense: other file handlers (the node
    editor's Image node, the viewport's image empty / camera background) say
    no there, so the drop attaches without a "which one?" menu."""
    for cls in _all_subclasses(bpy.types.FileHandler):
        if cls.__name__ == "BFACW_FH_chat_drop" or not getattr(cls, "is_registered", False):
            continue
        old = cls.__dict__.get("poll_drop")
        if old is None:
            continue

        _WRAPPED_DROPS.append((cls, old))
        cls.poll_drop = classmethod(_drop_outside_chat(old.__func__))


def _give_back() -> None:
    for cls, old in _WRAPPED_DROPS:
        cls.poll_drop = old
    _WRAPPED_DROPS.clear()
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
            BFACW_PT_editor_display,
            BFACW_MT_editor_chat, BFACW_MT_editor_view, BFACW_PT_editor_tools_chat,
            BFACW_PT_editor_tools_attach, BFACW_PT_editor_tools_session, BFACW_PT_editor_chat,
            BFACW_OT_chat_attach_image, BFACW_OT_chat_paste_image, BFACW_OT_editor_native_edit,
            BFACW_OT_chat_context_copy, BFACW_OT_chat_context_edit, BFACW_MT_chat_context)
_COPIES: list[type] = []


def register() -> None:
    if bpy.app.background:
        return
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    from . import activity
    activity.on_change = _tag_chat_regions      # Agent 9: a new tool call shows at once
    _COPIES[:] = _editor_copies() + _sidebar_copies()
    for cls in _COPIES:
        bpy.utils.register_class(cls)
    ours = {c.__name__ for c in _CLASSES + tuple(_COPIES)} | {c.bl_idname for c in _COPIES}
    _take_over(ours)
    _HANDLERS.append((bpy.types.SpaceNodeEditor,
                      bpy.types.SpaceNodeEditor.draw_handler_add(_draw_editor, (), "WINDOW", "POST_PIXEL"),
                      "WINDOW"))
    from . import ui_chat
    for _space, cls_name, _short, _icon in ui_chat.SIDEBAR_SPACES:     # every editor's sidebar chat (#291)
        space_cls = getattr(bpy.types, cls_name, None)
        if space_cls is not None:
            _HANDLERS.append((space_cls, space_cls.draw_handler_add(_draw_sidebar, (), "UI", "POST_PIXEL"), "UI"))
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
    _free_markers()
