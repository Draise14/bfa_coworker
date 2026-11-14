# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Bforartists Coworker Chat Panel -- provides an in-Blender chat interface to the MCP agent.

Registers a ``VIEW_3D`` sidebar panel with conversation history, multi-line
input, send/clear/stop buttons, and a status bar.

Also registers a Text Editor side panel for prompt-based interaction.
"""

__all__ = (
    "ChatHistoryProperties",
    "BFACW_PT_chat_panel",
    "BFACW_PT_chat_session",
    "BFACW_PT_chat_queue",
    "BFACW_PT_chat_status",
    "BFACW_PT_chat_text_editor",
    "BFACW_OT_chat_send",
    "BFACW_OT_chat_clear",
    "BFACW_OT_chat_stop",
    "BFACW_OT_chat_queue_send",
    "BFACW_OT_chat_capture_render",
    "BFACW_OT_chat_capture_screen",
    "BFACW_OT_chat_image_drop",
    "BFACW_FH_chat_drop",
    "BFACW_OT_export_session_log",
    "BFACW_OT_copy_session_log",
    "BFACW_OT_copy_status_error",
    "BFACW_OT_agent_start",
    "BFACW_OT_agent_stop",
    "BFACW_OT_agent_restart",
    "chat_timer_update",
    "register",
    "unregister",
)

import array
import json
import os
import re
import threading
import time
from pathlib import Path

import bpy  # pylint: disable=import-error
from bpy.props import (  # pylint: disable=import-error
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import (  # pylint: disable=import-error
    Operator,
    Panel,
    PropertyGroup,
)

import textwrap

from . import agent_controller
from . import chat_attachments
from . import llm_manager
from . import log as _log
from . import mcp_to_blender_server
from .engine import EngineCaps, Prompt
from .engine_loop import LoopConfig, LoopEngine
from .shared import effective_ports, APPROVAL_MODE_ITEMS, CHAT_MODE_ITEMS, OPERATING_MODE_ITEMS, icon as safe_icon, is_bforartists


# The engine behind the chat for Local LLM / Remote API (Tier 4i.1b).  Created
# on first use; its worker hooks are module functions defined further down.
_engine: LoopEngine | None = None


_acp = None   # the External Agent engine (engine_acp.AcpEngine), made on first use


def _is_external_agent(context: bpy.types.Context | None = None) -> bool:
    try:
        prefs = (context or bpy.context).preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return False
    return prefs.operating_mode == "EXTERNAL_AGENT"


def _acp_engine():
    """The External Agent engine (Tier 4, Agent 4): chat here, the agent app works."""
    global _acp
    if _acp is None:
        from .engine_acp import AcpEngine
        _acp = AcpEngine(agent_controller._agent_state)
        # Threads 1 (#357): a thread's turn writes that thread's chat.
        _acp.state_for = lambda tag: agent_controller.thread_state(agent_controller.THREADS.get(tag))

        def changed() -> None:
            # Any thread.  The chat shows "running" and the status like the other modes.
            state = agent_controller._agent_state
            state.mcp_server_running = bool(_acp.connected)
            if not state.is_thinking:
                state.ui_status = _acp.status

            def redraw() -> None:     # every chat: editors, sidebars, headers
                from . import chat_editor as _ce
                _ce._tag_chat_regions()
                _tag_chat_regions_redraw()
            bpy.app.timers.register(redraw, first_interval=0.0)
        _acp.on_change = changed

        # Goal & Plan, like a local turn: your message is the goal, and the
        # agent's plan (or OpenCode's to-do list) fills the steps.
        from . import session_memory as _sm

        def _store():           # the running thread's Goal & Plan (#358)
            return agent_controller._memory(agent_controller.THREADS.get(_acp.thread)).store

        def request(text: str) -> None:
            with _sm.store_lock:
                _store().goal.set_request(text)

        def plan(steps: list[dict[str, str]]) -> None:
            with _sm.store_lock:
                _store().goal.apply_update({"steps": steps})
        _acp.on_request = request
        _acp.applier.on_plan = plan
    return _acp


def _queued_messages(context: bpy.types.Context | None = None) -> list[dict]:
    """The waiting messages of whichever engine the chat uses."""
    if _is_external_agent(context) and _acp is not None:
        return _acp.queue_items()
    return agent_controller.thread_queue(_ui_thread(context)).get_all()     # this chat's thread (#358)


def _agent_workspace() -> Path:
    """Where the external agent runs (its working folder): user-private, not the .blend's."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) / "bfa_coworker" if os.name == "nt" and local else Path.home() / ".cache" / "bfa_coworker"
    return base / "agent_workspace"


def _ensure_bridge(prefs, port: int = 0) -> str:
    """The External Agent's tools reach Blender through the bridge: start it again if it
    stopped (e.g. the add-on was disabled and enabled while the agent ran). "" when it runs
    on *port* (the one the agent was given), else what went wrong."""
    if mcp_to_blender_server.is_running():
        return ""
    wanted = port or effective_ports(prefs)[0]
    try:
        mcp_to_blender_server.start(prefs.host, wanted)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return "Blender's bridge for the agent is off and didn't start again: {:s}".format(str(ex))
    from . import execute_interactive
    if not bpy.app.timers.is_registered(execute_interactive.run):
        bpy.app.timers.register(execute_interactive.run, first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
                                persistent=True)
    actual = mcp_to_blender_server.get_actual_port()
    if actual and actual != wanted:
        return ("Blender's bridge restarted on port {:d}, but the agent uses {:d} (another window took it): "
                "Stop and Start Coworker".format(actual, wanted))
    return ""


def _scene_context_line(context: bpy.types.Context) -> str:
    """One short line about where the user is, sent with each External Agent message."""
    try:
        scene = context.scene
        active = context.view_layer.objects.active if context.view_layer else None
        selected = [o.name for o in context.selected_objects][:5] if hasattr(context, "selected_objects") else []
        parts = ["{:s} {:s}".format(bpy.app.version_string, "Bforartists" if _is_bfa else "Blender"),
                 "scene '{:s}' ({:d} objects)".format(scene.name, len(scene.objects)),
                 "mode {:s}".format(context.mode)]
        if active is not None:
            parts.append("active '{:s}'".format(active.name))
        if selected:
            parts.append("selected: " + ", ".join(selected))
        return "; ".join(parts) + ". Use the bfa-coworker tools to inspect and change the scene."
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


_EDITOR_NAMES = {'VIEW_3D': "3D Viewport", 'IMAGE_EDITOR': "Image Editor", 'NODE_EDITOR': "Node Editor",
                 'SEQUENCE_EDITOR': "Video Sequencer", 'CLIP_EDITOR': "Movie Clip Editor",
                 'DOPESHEET_EDITOR': "Dope Sheet", 'GRAPH_EDITOR': "Graph Editor", 'NLA_EDITOR': "NLA Editor",
                 'TEXT_EDITOR': "Text Editor", 'SPREADSHEET': "Spreadsheet"}


def _editor_focus(context: bpy.types.Context) -> str:
    """What the editor the message came from shows, in a few words (#291)."""
    space = getattr(context, "space_data", None)
    kind = getattr(space, "type", "")
    try:
        if kind == 'NODE_EDITOR':
            tree = space.edit_tree or space.node_tree
            if tree is not None:
                active = tree.nodes.active
                owner = space.id        # a material / world / scene for their own (embedded) trees
                where = ("the {:s} '{:s}' node tree".format(type(owner).__name__.lower(), owner.name)
                         if owner is not None and owner != tree else "node tree '{:s}'".format(tree.name))
                return "{:s} ({:s}){:s}".format(
                    where, space.tree_type, ", active node '{:s}'".format(active.name) if active else "")
        elif kind == 'SEQUENCE_EDITOR':
            editor = context.scene.sequence_editor
            strip = editor.active_strip if editor else None
            if strip is not None:
                return "active strip '{:s}' ({:s}, channel {:d})".format(strip.name, strip.type, strip.channel)
        elif kind == 'IMAGE_EDITOR' and space.image is not None:
            return "image '{:s}'{:s}".format(space.image.name, " (UV editing)" if space.mode == 'UV' else "")
        elif kind == 'CLIP_EDITOR' and space.clip is not None:
            return "movie clip '{:s}'".format(space.clip.name)
        elif kind == 'TEXT_EDITOR' and space.text is not None:
            return "text '{:s}', line {:d}".format(space.text.name, space.text.current_line_index + 1)
        elif kind in ('DOPESHEET_EDITOR', 'GRAPH_EDITOR', 'NLA_EDITOR'):
            obj = context.active_object
            action = obj.animation_data.action if obj and obj.animation_data else None
            if action is not None:
                return "action '{:s}' of '{:s}'".format(action.name, obj.name)
        elif kind == 'SPREADSHEET' and context.active_object is not None:
            return "the data of '{:s}'".format(context.active_object.name)
    except (AttributeError, ReferenceError, TypeError):
        pass
    return ""


def _editor_context_line(context: bpy.types.Context, only: bool) -> str:
    """The editor a sidebar message came from; with *only*, the request stays in it (#291)."""
    kind = getattr(getattr(context, "space_data", None), "type", "")
    name = _EDITOR_NAMES.get(kind)
    if name is None:
        return ""
    focus = _editor_focus(context)
    line = "The user wrote this from the {:s}{:s}.".format(name, ": " + focus if focus else "")
    if only:
        line += (" Use this editor's context only: work on what it shows, and leave the rest of the "
                 "scene alone unless asked.")
    return line


# -- External Agent setup: which agent, is it installed, install / update / remove ------
_AGENT_CHECKS: dict[str, object] = {}      # preset key -> acp_presets.Detection, or "checking"
_AGENT_JOBS: dict[str, str] = {}           # preset id -> npm job status line


def _selected_agent_preset(prefs):
    from . import acp_presets
    if prefs.external_agent_preset == "custom":
        try:
            return acp_presets.generic_preset(prefs.external_agent_command)
        except ValueError:
            return None
    return acp_presets.preset_by_id(prefs.external_agent_preset)


def _agent_options_file() -> Path:
    """Your picks per agent (model, mode, effort), re-applied when it starts."""
    return _agent_workspace().parent / "agent_options.json"


def _read_json(path: Path) -> dict:
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_agent_option(path: Path, preset_id: str, config_id: str, value: str) -> None:
    """Any thread: plain file I/O."""
    data = _read_json(path)
    data.setdefault(preset_id, {})[config_id] = value
    try:
        with open(str(path), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


# A picker's icon, by the agent's option category.
_PICKER_ICONS = {"mode": 'TOOL_SETTINGS', "model": 'EXPERIMENTAL', "thought_level": 'OUTLINER_OB_LIGHT'}
# Enum items for the open picker; kept alive here (Blender needs the strings to outlive the call).
_PICKER_ITEMS: list[tuple[str, str, str]] = []


# The options block (#195): one look for every engine -- a label with its icon, then
# a row of buttons (few choices) or a dropdown (many).
_OPTION_LABELS = {"mode": "Mode", "model": "Model", "thought_level": "Effort"}
_OPTION_PROPS = {"mode": "bfacw_agent_mode", "model": "bfacw_agent_model", "thought_level": "bfacw_agent_effort"}
_EXPAND_UP_TO = 4


def _option_row(layout, label: str, icon: str):
    """A row: the label (with its icon) on the left, the control's room on the right."""
    split = layout.split(factor=0.32, align=True)
    split.label(text=label, icon=icon)
    return split.row(align=True)


def _draw_agent_pickers(layout) -> None:
    """The running agent's own options: Agent Mode, Model, Effort (#195)."""
    if _acp is None or not _acp.connected:
        return
    wm = bpy.context.window_manager
    col = layout.column(align=True)
    for picker in _acp.pickers():
        prop = _OPTION_PROPS.get(picker["category"])
        if prop is None or (picker["category"] == "mode" and _modes_cover_agent_mode(picker)):
            continue
        row = _option_row(col, _OPTION_LABELS[picker["category"]], _PICKER_ICONS.get(picker["category"], 'DOT'))
        if len(picker["choices"]) <= _EXPAND_UP_TO:
            row.prop(wm, prop, expand=True)          # a button per choice, named
        else:
            row.prop(wm, prop, text="")


def _draw_engine_options(layout, context, prefs) -> None:
    """Local and Remote get the same block as External Agent (#195): Model, Effort."""
    mode = prefs.operating_mode
    if mode not in ("LOCAL_LLM", "REMOTE_API"):
        return
    running = agent_controller._agent_state.mcp_server_running
    col = layout.column(align=True)
    if mode == "LOCAL_LLM":
        row = _option_row(col, "Model", _PICKER_ICONS["model"])
        row.enabled = not running                 # a new local model loads at the next Start
        row.prop(prefs, "model_preset", text="")
        if hasattr(prefs, "reasoning_effort"):
            row = _option_row(col, "Effort", _PICKER_ICONS["thought_level"])
            row.prop(prefs, "reasoning_effort", text="")
    else:
        from . import operators_agent as _oa
        row = _option_row(col, "Model", _PICKER_ICONS["model"])
        if _oa.REMOTE_MODEL_IDS:
            row.prop(context.window_manager, "bfacw_remote_model", text="")
        else:
            row.prop(prefs, "remote_model", text="")
        row.operator("bfacw.refresh_remote_models", text="", icon='FILE_REFRESH')


# -- Coworker / Ask / Plan drive the agent's own mode (#221) ---------------------------
_AGENT_MODE: dict[str, str] = {"normal": "", "syncing": ""}


def _is_plan_choice(value: str, name: str) -> bool:
    return "plan" in value.lower() or "plan" in name.lower()


def agent_mode_for(chat_mode: str, choices: list, current: str, normal: str = "") -> str | None:
    """The agent mode that matches *chat_mode*, or None to keep *current*.

    Plan -> the agent's plan mode (when it has one); Coworker / Ask -> out of plan
    mode, back to *normal* (the last normal mode you used) or its first one.
    """
    plans = [v for v, n, _d in choices if _is_plan_choice(v, n)]
    others = [v for v, n, _d in choices if not _is_plan_choice(v, n)]
    if chat_mode == "PLAN":
        return plans[0] if plans and current not in plans else None
    if current in plans and others:
        return normal if normal in others else others[0]
    return None


def _modes_cover_agent_mode(picker: dict) -> bool:
    """The agent offers just "normal" and "plan": Coworker | Ask | Plan says it all."""
    choices = picker["choices"]
    return len(choices) <= 2 and any(_is_plan_choice(v, n) for v, n, _d in choices)


def _sync_agent_mode(chat_mode: str, wait: bool = False) -> None:
    picker = _picker_for("mode")
    if picker is None:
        return
    value = agent_mode_for(chat_mode, picker["choices"], picker["current"], _AGENT_MODE["normal"])
    if value is not None and value != picker["current"]:
        _acp.set_option(picker["id"], value, wait=wait)


def _chat_follows_agent_mode(value: str) -> None:
    """You picked the agent's mode yourself: the chat's mode follows (its Plan = our Plan)."""
    picker = _picker_for("mode")
    name = next((n for v, n, _d in (picker["choices"] if picker else ()) if v == value), "")
    props = bpy.context.window_manager.bfacw_chat_props
    plan = _is_plan_choice(value, name)
    if not plan:
        _AGENT_MODE["normal"] = value
    wanted = "PLAN" if plan else ("AGENT" if props.chat_mode == "PLAN" else props.chat_mode)
    if props.chat_mode != wanted:
        _AGENT_MODE["syncing"] = "1"
        try:
            props.chat_mode = wanted
        finally:
            _AGENT_MODE["syncing"] = ""


def _on_chat_mode(self, _context) -> None:
    if not _AGENT_MODE["syncing"]:
        _sync_agent_mode(self.chat_mode)


def _agent_follows_approval(engine, mode: str) -> None:
    engine.ask_coworker_tools = mode == "ASK"
    engine.never_ask = mode == "AUTO"


# -- Threads 2 (#358): each editor kind has its own thread ------------------------------
# A sidebar chat shows its editor kind's thread while Use Editor Context Only is on (the
# chat's tab for that editor), else the shared "All editors" thread (the default one,
# today's single conversation).  "New thread here" and the full-screen picker pin one
# area to a thread; the Coworker editor shows the thread picked in its list (its pin).
# One rule for every chat (#417): threads.pick(editor kind, the area's pin, focus).
_AREA_PINS: dict[int, str] = {}       # an area's space -> its pinned thread id


def _pin_key(area) -> int:
    """An area's pin key: its space, which Ctrl+Space / Ctrl+Alt+Space carry over to the
    maximized area and back (ED_area_data_swap), so the pin follows it (#417)."""
    try:
        return area.spaces.active.as_pointer()
    except (AttributeError, ReferenceError):
        return area.as_pointer()


def _is_coworker_area(area) -> bool:
    if getattr(area, "type", "") != 'NODE_EDITOR':
        return False
    from . import chat_editor as _ce
    return _ce.is_coworker_space(area.spaces.active)


def _ui_thread(context=None):
    """The thread the chat in *context*'s area shows and acts on."""
    from . import threads
    reg = agent_controller.THREADS
    context = context if context is not None else bpy.context
    area = getattr(context, "area", None)
    if area is None:
        return reg.default
    try:
        _restore_tabs()                 # this .blend's tabs, once (#427)
        # The tab this area shows (its pin), else All editors (#427).
        return threads.pick(reg, "", pinned=_AREA_PINS.get(_pin_key(area), ""))
    except (AttributeError, ReferenceError):
        return reg.default


def _ui_store(context=None):
    """The shown thread's session store: its Goal & Plan, memory and checkpoints (#417)."""
    return agent_controller._memory(_ui_thread(context)).store


# -- Threads 2b (#417): the window's attachment socket follows the thread in use ----------
# The draft and the pin are read through the chat's own thread (chat_input and
# chat_image_send_once have get / set); the attached image is a datablock pointer, so
# the window's socket holds the image of the thread last used and every other thread
# keeps its own by name until its chat is used again.
_SOCKET = None      # threads.AttachmentSocket, made on first use


def _socket():
    global _SOCKET
    if _SOCKET is None:
        from . import threads
        _SOCKET = threads.AttachmentSocket()
    return _SOCKET


def _thread_image(thread):
    """*thread*'s attached image (the socket's, for the thread in use)."""
    props = getattr(bpy.context.window_manager, "bfacw_chat_props", None)
    socket = getattr(props, "chat_image", None)
    if thread.id == _socket().focus:
        return socket
    return bpy.data.images.get(thread.attachment) if thread.attachment else None


def _focus_thread(thread) -> None:
    """*thread*'s chat is in use: the socket now holds its image (the last one's is kept)."""
    global _sticky_relinking
    props = getattr(bpy.context.window_manager, "bfacw_chat_props", None)
    if thread is None or props is None:
        return
    socket = _socket()
    image = props.chat_image
    socket.sticky = dict(_sticky_attachment)
    name = socket.use(agent_controller.THREADS, thread, image.name if image is not None else "")
    if name is None:
        return
    _sticky_attachment.clear()
    _sticky_attachment.update(socket.sticky)
    _sticky_relinking = True        # a swap, not the user picking: the pin record moved with it
    try:
        props.chat_image = bpy.data.images.get(name) if name else None
    finally:
        _sticky_relinking = False


def _socket_set_by(thread, image) -> None:
    """The socket was set from *thread*'s chat: the image is that thread's (#417)."""
    socket = _socket()
    socket.sticky = dict(_sticky_attachment)
    socket.set_by(agent_controller.THREADS, thread, image.name if image is not None else "")
    _sticky_attachment.clear()
    _sticky_attachment.update(socket.sticky)


def _draft_get(_self) -> str:
    return _ui_thread(bpy.context).draft


def _draft_set(_self, value: str) -> None:
    _ui_thread(bpy.context).draft = value


def _send_once_get(_self) -> bool:
    return _ui_thread(bpy.context).attachment_once


def _send_once_set(_self, value: bool) -> None:
    _ui_thread(bpy.context).attachment_once = bool(value)


def _auto_get(_self) -> bool:
    return _ui_thread(bpy.context).auto["run"] is not None


def _auto_set(_self, value: bool) -> None:
    _auto_toggle(_ui_thread(bpy.context), bool(value), bpy.context)


def _editor_thread(editor: str, chat_id: str = ""):
    """A new tab's thread in *editor*'s context ("" = All editors); with *chat_id*, that
    History chat's messages and plan come back in it."""
    from . import threads
    reg = agent_controller.THREADS
    thread = reg.create(editor=editor, title=threads.short_name(editor) or "Chat")
    thread.chat_id = chat_id or _chat_index().new_id()
    if chat_id:
        _load_thread_chat(thread)
    return thread


# -- Threads 2c (#427): the open tabs, saved with the .blend --------------------------------
_TABS: dict = {"blend": None}       # the .blend whose saved tabs are open


def _restore_tabs() -> None:
    """The tabs open when this .blend was last saved come back (once per file), and each
    area shows the tab it showed then (#431)."""
    blend = _blend_key()
    if _TABS["blend"] == blend:
        return
    _TABS["blend"] = blend
    from . import threads
    index = _chat_index()
    for tab in index.tabs_for_blend(blend):
        threads.reopen(agent_controller.THREADS, tab["chat"], tab.get("editor", ""), make=_editor_thread)
    try:
        _restore_area_tabs(index, blend)
    except (AttributeError, ReferenceError, KeyError):
        pass


def _workspace_areas() -> list:
    """Every area of every workspace, with its key in the saved session (#431)."""
    from .chat_history import area_key
    out = []
    for workspace in bpy.data.workspaces:
        for screen in workspace.screens:
            if getattr(screen, "is_temporary", False):     # a maximized area's screen
                continue
            for i, area in enumerate(screen.areas):
                out.append((area_key(workspace.name, i, area.ui_type), area))
    return out


def _save_area_tabs() -> None:
    """Remember which tab each area shows, for this .blend (#431)."""
    if _TABS["blend"] is None:
        return          # as _save_tabs: nothing came back yet, so keep what was saved
    try:
        index = _chat_index()
        reg = agent_controller.THREADS
        tabs = []
        for key, area in _workspace_areas():
            thread = reg.get(_AREA_PINS.get(_pin_key(area)) or None)
            if thread is None or thread.is_default:        # All editors: what an area shows anyway
                continue
            if thread.chat_id and index.chat_path(thread.chat_id).exists():
                tabs.append({"key": key, "editor": key.rsplit("|", 1)[-1], "chat": thread.chat_id})
        index.set_area_tabs_for_blend(_blend_key(), tabs)
    except (OSError, AttributeError, ReferenceError):
        pass


def _restore_area_tabs(index, blend: str) -> None:
    """Each area back on the tab it showed (its pin): by its place in the workspace, else
    by its editor kind when the layout changed (#431)."""
    saved = index.area_tabs_for_blend(blend)
    if not saved:
        return
    from . import threads
    from .chat_history import match_area_tabs
    reg = agent_controller.THREADS
    areas = _workspace_areas()
    shown = match_area_tabs(saved, [key for key, _area in areas])
    editor_of = {t["chat"]: t.get("editor", "") for t in index.tabs_for_blend(blend)}
    for key, area in areas:
        chat_id = shown.get(key)
        if not chat_id or not index.chat_path(chat_id).exists():
            continue
        thread = threads.reopen(reg, chat_id, editor_of.get(chat_id, ""), make=_editor_thread)
        _AREA_PINS[_pin_key(area)] = thread.id


def _save_tabs() -> None:
    """Remember the open tabs (with a chat to come back to) for this .blend."""
    if _TABS["blend"] is None:
        return          # its saved tabs never came back (no chat drawn yet): keep them (#431)
    try:
        index = _chat_index()
        tabs = [{"chat": t.chat_id, "editor": t.editor} for t in agent_controller.THREADS.live()
                if not t.is_default and t.chat_id and index.chat_path(t.chat_id).exists()]
        index.set_tabs_for_blend(_blend_key(), tabs)
    except (OSError, AttributeError):
        pass


def _show_in_area(area, thread) -> None:
    """*area*'s chat shows *thread* now (its tab is pressed)."""
    if area is not None:
        _AREA_PINS[_pin_key(area)] = thread.id
    _focus_thread(thread)
    thread.unread = False
    _redraw_all_chats()


def _redraw_all_chats() -> None:
    """Every chat shows a tab switch at once (sidebars, Coworker editors, headers)."""
    try:
        _tag_chat_regions_redraw()
        from . import chat_editor as _ce
        _ce._tag_chat_regions()
        for wm in bpy.data.window_managers:
            for window in wm.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
    except (AttributeError, ReferenceError):
        pass


def _ui_state(context=None):
    """The shown thread's state (history, live readout); service status stays the default's."""
    return agent_controller.thread_state(_ui_thread(context))


def _thread_engine(thread, mode: str | None = None):
    """The engine that runs *thread*'s turns: the External Agent (one session per thread),
    or our loop (one LoopEngine per thread)."""
    if thread is None or thread.is_default or _is_external_agent():
        return _loop_engine(mode)
    if thread.engine is None:
        engine = LoopEngine(agent_controller.controller_for(thread))
        engine.on_redraw = lambda: _redraw_areas_safe()
        engine.on_turn_finished = lambda t=thread: _thread_turn_finished(t)
        thread.engine = engine
    if mode is not None:
        label = "Remote" if mode == "remote" else "Local"
        if thread.engine.caps.label != label:
            thread.engine.name = label
            thread.engine.caps = EngineCaps(label=label, images=True, usage=True)
    return thread.engine


def _thread_turn_finished(thread) -> None:
    """A thread's turn ended (worker thread): save its chat, and mark it unread."""
    thread.unread = True
    if thread.is_default:
        _save_chat_history()
    else:
        _save_thread_chat(thread)
        _save_tabs()            # its tab comes back with the .blend (#427)


def _thread_status(thread) -> tuple[str, str]:
    """``(word, icon)`` for a thread's tab / list row: Thinking, Waiting (a question or
    its turn), Done (unread) or Idle."""
    from . import coworker_api, risky_steps, threads
    state = agent_controller.thread_state(thread)
    tag = threads.tag(thread)
    asks = (coworker_api.current_question(tag) is not None or risky_steps.APPROVALS.current(tag) is not None
            or (_acp is not None and _acp.approvals.current(tag) is not None))
    if asks:
        return "Waiting for you", "QUESTION"
    if state.is_thinking or state.turn_active:
        return "Thinking", "SORTTIME"
    if agent_controller.thread_queue(thread).pending_count:
        return "Waiting for its turn", "TIME"
    if thread.unread:
        return "Done", "CHECKMARK"
    return "Idle", "DOT"


def _thread_title(thread) -> str:
    from . import threads
    if thread.is_default:
        return "All editors"
    return thread.title or threads.short_name(thread.editor) or "Thread"


def _approvals_new_turn(thread=None) -> None:
    """A new turn: "Allow this turn" from the last one asks again (risky steps and the agent's)."""
    from . import loop_guard, risky_steps, threads
    tag = threads.tag(thread if thread is not None else _ui_thread())
    risky_steps.APPROVALS.new_turn(tag)
    loop_guard.BRIDGE.new_turn()        # repeats are counted per turn (#310)
    if _acp is not None:
        _acp.approvals.new_turn(tag)


def apply_approval_mode(mode: str) -> None:
    """The approval mode changed (#240): the agent follows, and every "Always" asks again."""
    from . import risky_steps
    risky_steps.APPROVALS.forget_always()
    if _acp is not None:
        _agent_follows_approval(_acp, mode)
        _acp.approvals.forget_always()
    _tag_chat_regions_redraw()


class BFACW_PT_approval_popover(Panel):  # type: ignore[misc]
    """How much the chat asks before tools run: a dropdown from the button by Send"""
    bl_label = "Permissions"
    bl_idname = "BFACW_PT_approval_popover"
    bl_space_type = 'TOPBAR'          # only shown as a popover
    bl_region_type = 'HEADER'
    bl_ui_units_x = 13

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        prefs = context.preferences.addons[__package__].preferences
        layout.label(text="Before tools run", icon='LOCKED')
        col = layout.column(align=True)
        col.scale_y = 1.3
        for ident, _name, _desc, _icon, _n in APPROVAL_MODE_ITEMS:
            col.prop_enum(prefs, "approval_mode", ident)
        hint = layout.column()
        hint.scale_y = 0.8
        _draw_multiline(hint, next(d for i, _n, d, _ic, _x in APPROVAL_MODE_ITEMS if i == prefs.approval_mode))
        hint.label(text="Applies at once, also while it runs", icon='INFO')


def _picker_for(category: str) -> dict | None:
    if _acp is None or not _acp.connected:
        return None
    return next((p for p in _acp.pickers() if p["category"] == category), None)


# Enum items must outlive the call that returns them (Blender keeps the strings).
_OPTION_ITEMS: dict[str, list[tuple[str, str, str]]] = {}


def _make_option_prop(category: str):
    def items(_self, _context):
        picker = _picker_for(category)
        _OPTION_ITEMS[category] = ([(v, n, d or n) for v, n, d in picker["choices"]] if picker else
                                   [("", "(not connected)", "")])
        return _OPTION_ITEMS[category]

    def get(_self):
        picker = _picker_for(category)
        if picker is None:
            return 0
        values = [v for v, _n, _d in picker["choices"]]
        return values.index(picker["current"]) if picker["current"] in values else 0

    def set_(_self, index):
        picker = _picker_for(category)
        if picker is not None and 0 <= index < len(picker["choices"]):
            value = picker["choices"][index][0]
            _acp.set_option(picker["id"], value)
            if category == "mode":
                _chat_follows_agent_mode(value)

    what = {"mode": "How the agent works (some ask before changes; Plan only plans)",
            "model": "Which model the agent uses (from your own subscription)",
            "thought_level": "How hard the agent thinks before it answers (more is slower)"}[category]
    return bpy.props.EnumProperty(name=_OPTION_LABELS[category], description=what + ". Coworker remembers "
                                  "your pick for this agent", items=items, get=get, set=set_)


def _remote_model_items(_self, _context):
    from . import operators_agent as _oa
    _OPTION_ITEMS["remote"] = [(m, m, m) for m in _oa.REMOTE_MODEL_IDS] or [("", "(refresh the list)", "")]
    return _OPTION_ITEMS["remote"]


def _remote_model_get(_self):
    from . import operators_agent as _oa
    prefs = bpy.context.preferences.addons[__package__].preferences
    return _oa.REMOTE_MODEL_IDS.index(prefs.remote_model) if prefs.remote_model in _oa.REMOTE_MODEL_IDS else 0


def _remote_model_set(_self, index):
    from . import operators_agent as _oa
    if 0 <= index < len(_oa.REMOTE_MODEL_IDS):
        bpy.context.preferences.addons[__package__].preferences.remote_model = _oa.REMOTE_MODEL_IDS[index]


def _installs_file() -> Path:
    """Which agent adapters Coworker installed (so Remove only touches those)."""
    return _agent_workspace().parent / "installed_agents.json"


def _installed_by_coworker() -> dict:
    try:
        return json.loads(_installs_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _set_installed_by_coworker(preset_id: str, package: str | None) -> None:
    data = _installed_by_coworker()
    if package:
        data[preset_id] = {"package": package, "when": time.strftime("%Y-%m-%d")}
    else:
        data.pop(preset_id, None)
    try:
        _installs_file().parent.mkdir(parents=True, exist_ok=True)
        _installs_file().write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def _check_agent(preset) -> None:
    """Look for *preset*'s agent in the background (it runs ``--version``)."""
    import threading as _threading
    from . import acp_presets
    key = preset.id + ":" + preset.command
    if _AGENT_CHECKS.get(key) == "checking":
        return
    _AGENT_CHECKS[key] = "checking"

    def work() -> None:
        _AGENT_CHECKS[key] = acp_presets.detect(preset)
        bpy.app.timers.register(lambda: (_tag_chat_regions_redraw(), _tag_prefs_redraw(), None)[-1],
                                first_interval=0.0)
    _threading.Thread(target=work, daemon=True, name="acp-detect").start()


def _tag_prefs_redraw() -> None:
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            for area in win.screen.areas:
                if area.type == 'PREFERENCES':
                    area.tag_redraw()


def _agent_location(found) -> str:
    """The file that runs the agent (the npm script for a node shim)."""
    argv = list(getattr(found, "argv", []) or [])
    if len(argv) >= 2 and os.path.basename(argv[0]).lower().startswith("node"):
        return argv[1]
    return argv[0] if argv else ""


def _npm_owned(preset, found) -> bool:
    """The adapter runs from npm's global packages under exactly this package name."""
    where = _agent_location(found).replace("\\", "/")
    return bool(preset.npm_package) and "/node_modules/" + preset.npm_package + "/" in where


def _npm_button(layout, preset, action: str, text: str, icon: str) -> None:
    op = layout.operator("bfacw.external_agent_npm", text=text, icon=icon)
    op.preset_id, op.action = preset.id, action


def draw_external_agent_setup(layout, context, compact: bool = False) -> None:
    """External Agent: pick the agent app, see if it's ready, install / update / remove it.

    Preferences get the full form; the chat's controls (``compact``) the short one.
    """
    prefs = context.preferences.addons[__package__].preferences
    state = agent_controller._agent_state
    box = layout.box() if not compact else layout.column()
    preset = _selected_agent_preset(prefs)
    if not compact:
        _draw_multiline(box, "Nothing runs until you press Start. Then Coworker opens the agent below in the "
                             "background and it works in Blender with Coworker's tools, under your own "
                             "login and subscription. To stop using it, pick another Operating Mode.",
                        icon='INFO')
    row = box.row(align=True)
    row.enabled = not state.mcp_server_running
    row.prop(prefs, "external_agent_preset", text="" if compact else "Agent")
    if prefs.external_agent_preset == "custom":
        box.prop(prefs, "external_agent_command", text="" if compact else "Command")
    if preset is None:
        box.label(text="Type the command that starts your agent in ACP mode", icon='INFO')
        return
    if preset.account and not compact:
        _draw_multiline(box, preset.account, icon='USER')
    key = preset.id + ":" + preset.command
    found = _AGENT_CHECKS.get(key)
    if found is None:
        _check_agent(preset)
        found = "checking"
    mine = preset.id in _installed_by_coworker()
    job = _AGENT_JOBS.get(preset.id, "")
    head = box.row(align=True)
    if found == "checking":
        head.label(text="Looking for {:s}...".format(preset.name), icon='SORTTIME')
    elif found.ready:
        version = (" " + found.version) if found.version else ""
        source = "" if compact else ("  (installed by Coworker)" if mine else
                                     "  (installed with npm)" if _npm_owned(preset, found) else
                                     "  (already on this computer)")
        head.label(text="{:s}{:s}: ready{:s}".format(preset.name, version, source), icon='CHECKMARK')
    elif compact and not found.installed:        # the sidebar is narrow: the short form
        head.label(text="{:s} isn't installed".format(preset.name), icon='ERROR')
    else:
        head.label(text="{:s} isn't ready".format(preset.name), icon='ERROR')
    head.operator("bfacw.external_agent_check", text="", icon='FILE_REFRESH')
    if found != "checking" and not found.ready and found.problem and not compact:
        _draw_multiline(box, found.problem)
    if job:
        box.label(text=job, icon='SORTTIME' if job.endswith("...") else 'INFO')
    if found == "checking":
        return
    _draw_agent_pickers(box)
    if not found.ready:
        if preset.npm_package and not job:
            _npm_button(box, preset, "INSTALL", "Install {:s}".format(preset.name), 'IMPORT')
        if preset.install_hint and not compact:
            _draw_multiline(box, preset.install_hint)
    elif not compact:
        where = _agent_location(found)
        if where:
            loc = box.row(align=True)
            loc.label(text=where, icon='FILE_FOLDER')
            loc.operator("bfacw.external_agent_open_folder", text="", icon='FILE_FOLDER').path = where
        if preset.login_hint:
            _draw_multiline(box, preset.login_hint, icon='USER')
        if preset.npm_package and not job:
            actions = box.row(align=True)
            actions.enabled = not state.mcp_server_running
            _npm_button(actions, preset, "UPDATE", "Update", 'FILE_REFRESH')
            if mine or _npm_owned(preset, found):
                _npm_button(actions, preset, "REMOVE", "Remove", 'TRASH')
    if not compact:
        opt = box.row()
        opt.enabled = not state.mcp_server_running
        opt.prop(prefs, "external_agent_only_coworker_tools")
        # Approval is the chat's lock button (it applies at once, also while the agent runs: #271).
        tools = box.row()
        tools.enabled = not state.mcp_server_running
        tools.prop(prefs, "external_agent_tools_on_demand")
        if preset.docs_url:
            box.operator("wm.url_open", text="{:s} docs".format(preset.name), icon='URL').url = preset.docs_url


def _picker_items(self, _context):
    picker = next((p for p in (_acp.pickers() if _acp is not None else []) if p["id"] == self.option_id), None)
    _PICKER_ITEMS[:] = [(v, n, d or n) for v, n, d in (picker["choices"] if picker else [])] or [("", "", "")]
    return _PICKER_ITEMS


class BFACW_OT_agent_option(Operator):  # type: ignore[misc]
    """Change one of the agent's own settings for this session: its mode, model or effort.
Coworker remembers your pick for the next time this agent starts"""
    bl_idname = "bfacw.agent_option"
    bl_label = "Agent Setting"
    bl_property = "value"
    bl_options = {'INTERNAL'}

    option_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]
    value: EnumProperty(items=_picker_items, options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties) -> str:
        picker = next((p for p in (_acp.pickers() if _acp is not None else []) if p["id"] == properties.option_id),
                      None)
        if picker is None:
            return cls.__doc__
        what = {"mode": "How the agent works (some ask before changes, Plan only plans)",
                "model": "Which model the agent uses (from your own subscription)",
                "thought_level": "How hard the agent thinks before it answers (more is slower)"}.get(
                    picker["category"], picker["name"])
        return "{:s}. Now: {:s}. Coworker remembers your pick for this agent".format(what, picker["current_name"])

    def invoke(self, context: bpy.types.Context, _event) -> set[str]:
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        if _acp is None or not self.value:
            return {"CANCELLED"}
        _acp.set_option(self.option_id, self.value)
        picker = _picker_for("mode")
        if picker is not None and picker["id"] == self.option_id:
            _chat_follows_agent_mode(self.value)          # #221
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_external_agent_check(Operator):  # type: ignore[misc]
    """Look for the selected agent again: is it installed, which version, can Coworker start it"""
    bl_idname = "bfacw.external_agent_check"
    bl_label = "Check Agent"

    def execute(self, context: bpy.types.Context) -> set[str]:
        preset = _selected_agent_preset(context.preferences.addons[__package__].preferences)
        if preset is not None:
            _AGENT_CHECKS.pop(preset.id + ":" + preset.command, None)
            _check_agent(preset)
        return {"FINISHED"}


class BFACW_OT_external_agent_open_folder(Operator):  # type: ignore[misc]
    """Show the folder the agent runs from in your file browser"""
    bl_idname = "bfacw.external_agent_open_folder"
    bl_label = "Open Agent Folder"

    path: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> set[str]:
        folder = os.path.dirname(self.path) if os.path.isfile(self.path) else self.path
        if not folder or not os.path.isdir(folder):
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=folder)
        return {"FINISHED"}


_NPM_ACTIONS = {
    # action: (npm arguments, confirm title, confirm message, button)
    "INSTALL": (["install", "-g", "{pkg}"], "Install {name}?",
                "Coworker runs: npm install -g {pkg}. It uses your own {name} login. "
                "You can remove it again from here.", "Install"),
    "UPDATE": (["install", "-g", "{pkg}@latest"], "Update {name}?",
               "Coworker runs: npm install -g {pkg}@latest", "Update"),
    "REMOVE": (["uninstall", "-g", "{pkg}"], "Remove {name}?",
               "Coworker runs: npm uninstall -g {pkg}. Your {name} login and settings stay. "
               "Pick another Operating Mode to stop using External Agent.", "Remove"),
}


class BFACW_OT_external_agent_npm(Operator):  # type: ignore[misc]
    """Install, update or remove the selected agent's ACP adapter with npm (asks first)"""
    bl_idname = "bfacw.external_agent_npm"
    bl_label = "Agent Adapter"

    preset_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]
    action: EnumProperty(  # type: ignore[valid-type]
        items=[("INSTALL", "Install", "Install the agent's ACP adapter (npm install -g)"),
               ("UPDATE", "Update", "Update the agent's ACP adapter to its latest version"),
               ("REMOVE", "Remove", "Uninstall the ACP adapter Coworker installed (npm uninstall -g)")],
        options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, properties):
        from . import acp_presets
        preset = acp_presets.preset_by_id(properties.preset_id)
        name, pkg = (preset.name, preset.npm_package) if preset else ("the agent", "its adapter")
        return {
            "INSTALL": "Install {:s} for Coworker: runs `npm install -g {:s}` (needs Node.js). It uses your "
                       "own {:s} login".format(name, pkg, name),
            "UPDATE": "Update {:s} to its latest version: runs `npm install -g {:s}@latest`".format(name, pkg),
            "REMOVE": "Uninstall what Coworker installed: runs `npm uninstall -g {:s}`. Your login and "
                      "settings stay".format(pkg),
        }.get(properties.action, "")

    def invoke(self, context, event):
        from . import acp_presets
        preset = acp_presets.preset_by_id(self.preset_id)
        if preset is None or not preset.npm_package:
            return {"CANCELLED"}
        _args, title, message, button = _NPM_ACTIONS[self.action]
        fill = {"name": preset.name, "pkg": preset.npm_package}
        if self.action == "REMOVE" and preset.id not in _installed_by_coworker():
            message += " It wasn't installed by Coworker: other apps (e.g. Zed) may use it too."
        return context.window_manager.invoke_confirm(self, event, title=title.format(**fill),
                                                     message=message.format(**fill), confirm_text=button)

    def execute(self, context: bpy.types.Context) -> set[str]:
        import subprocess as _subprocess
        import threading as _threading
        from . import acp_presets
        preset = acp_presets.preset_by_id(self.preset_id)
        if preset is None or not preset.npm_package:
            return {"CANCELLED"}
        npm = acp_presets.resolve_command("npm")
        if npm is None:
            self.report({"WARNING"}, "npm isn't installed: install Node.js first (nodejs.org)")
            return {"CANCELLED"}
        action = self.action
        args = [a.format(pkg=preset.npm_package) for a in _NPM_ACTIONS[action][0]]
        verb = {"INSTALL": "Installing", "UPDATE": "Updating", "REMOVE": "Removing"}[action]
        _AGENT_JOBS[preset.id] = "{:s} {:s}...".format(verb, preset.npm_package)

        def work() -> None:
            ok = False
            try:
                proc = _subprocess.run(npm + args, capture_output=True, timeout=600,
                                       creationflags=getattr(_subprocess, "CREATE_NO_WINDOW", 0))
                ok = proc.returncode == 0
                tail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
                _AGENT_JOBS[preset.id] = "" if ok else "{:s} failed: {:s}".format(
                    verb, tail[0] if tail else "npm error")
            except (OSError, _subprocess.SubprocessError) as ex:
                _AGENT_JOBS[preset.id] = "{:s} failed: {:s}".format(verb, str(ex))
            if ok and action == "INSTALL":
                _set_installed_by_coworker(preset.id, preset.npm_package)
            elif ok and action == "REMOVE":
                _set_installed_by_coworker(preset.id, None)
            _AGENT_CHECKS.pop(preset.id + ":" + preset.command, None)

            def done() -> None:
                if not _AGENT_JOBS.get(preset.id):
                    _AGENT_JOBS.pop(preset.id, None)
                _check_agent(preset)
                _tag_chat_regions_redraw()
                _tag_prefs_redraw()
            bpy.app.timers.register(done, first_interval=0.0)
        _threading.Thread(target=work, daemon=True, name="acp-npm").start()
        return {"FINISHED"}


def _loop_engine(mode: str | None = None):
    """The chat's engine: our turn loop, or the External Agent in that mode.
    *mode* ("local"/"remote") updates the loop engine's label."""
    global _engine
    if _is_external_agent():
        return _acp_engine()
    if _engine is None:
        _engine = LoopEngine(agent_controller)
        _engine.on_redraw = lambda: _redraw_areas_safe()
        _engine.on_turn_finished = lambda: _thread_turn_finished(agent_controller.THREADS.default)
        agent_controller.THREADS.default.engine = _engine     # a waiting message starts here (#357)
    if mode is not None:
        label = "Remote" if mode == "remote" else "Local"
        if _engine.caps.label != label:
            _engine.name = label
            _engine.caps = EngineCaps(label=label, images=True, usage=True)
    return _engine


def _sync_prefs_to_config(prefs: bpy.types.AddonPreferences) -> None:
    """Copy all relevant preference fields into llm_manager._config."""
    llm_cfg = llm_manager.get_config()
    # Derive mode from operating_mode.
    if prefs.operating_mode == "LOCAL_LLM":
        llm_cfg.mode = "local"
    elif prefs.operating_mode == "REMOTE_API":
        llm_cfg.mode = "remote"
    else:
        llm_cfg.mode = "local"  # fallback for harness mode
    llm_cfg.llama_path = prefs.llama_path
    llm_cfg.model_repo_id = prefs.model_repo_id
    llm_cfg.model_filename = prefs.model_filename
    llm_cfg.downloaded_models_dir = prefs.downloaded_models_dir
    llm_cfg.local_ctx_size = prefs.local_ctx_size
    llm_cfg.local_max_tokens = prefs.local_max_tokens
    llm_cfg.thinking_budget_tokens = prefs.thinking_budget_tokens
    # getattr with a default: a Blender session that still has an older
    # _BFACW_Preferences class registered (e.g. before a restart after an
    # addon update) must not crash agent start over a newer preference.
    llm_cfg.lock_scene_while_working = getattr(prefs, "lock_scene_while_working", True)
    llm_cfg.auto_continue_rounds = int(getattr(prefs, "auto_continue_rounds", 3))
    llm_cfg.remote_api_url = prefs.remote_api_url
    llm_cfg.remote_api_key = prefs.remote_api_key
    llm_cfg.remote_model = prefs.remote_model
    llm_manager.set_config(llm_cfg)


_WRAP_WIDTH = 60

# Panel category both chat panels live under.  The drag-and-drop handler
# uses it to tell "over the Coworker panel" from "somewhere else in the
# sidebar" (a FileHandler poll gets no mouse position, only the region).
_CHAT_PANEL_CATEGORY = "Coworker"

# Thumbnail sizing for the attached-image preview.
# Everything here follows from one fact in the C code: a preview icon is
# drawn into a SQUARE.  template_icon() marks its button BUT_ICON_PREVIEW,
# so widget_draw_preview_icon() (interface_widgets.cc) takes
# min(button_w, button_h), subtracts PREVIEW_PAD and calls
# icon_draw_preview(..., aspect=1.0f, size) -> icon_draw_size() builds
# w = h = size/aspect -> icon_draw_rect() fits the image inside that
# square, centred, aspect preserved.  A wide image is therefore never
# drawn wider than the button is tall, and shrinking the button only
# shrinks the thumbnail with it (a short button draws the image as wide
# as the button is tall).
#
# So the thumbnail buffer is made SQUARE and the letterbox bands are
# filled with a dimmed, blurred, cover-scaled copy of the same image
# (_attachment_thumbnail_pixels).  That keeps the thumbnail panel-wide,
# aspect-correct and borderless -- no empty band above or below it --
# which a square draw rect cannot do on its own.
#
# The size is quantised so dragging the sidebar does not resample on
# every single pixel of width, and the pixels come from our own buffer
# resampled to exactly the size Blender will draw them at, because a
# plain template_icon draws the datablock's 32px icon buffer
# (interface_icons.cc icon_create_rect) and looks very pixely once the
# thumbnail is panel-wide.
_UI_UNIT_BASE_PX = 20.0
_ATTACHMENT_PREVIEW_MARGIN_PX = 40
_ATTACHMENT_PREVIEW_MIN_PX = 64
_ATTACHMENT_PREVIEW_MAX_PX = 512
_ATTACHMENT_PREVIEW_QUANTUM_PX = 16
# How much the cover-scaled backdrop is darkened (see
# _attachment_thumbnail_pixels): low enough to read as a backdrop for the
# sharp image in the middle, high enough that it is not a black box.
_ATTACHMENT_THUMBNAIL_DIM = 0.4
# Images bigger than this are left to Blender's own (small) preview icon:
# the thumbnail resample copies the pixels into a float buffer, so a huge
# source would cost two buffers of 16 bytes per pixel for a thumb.
_ATTACHMENT_THUMBNAIL_MAX_SOURCE_PX = 12000000


# -- Brand detection (#456): only Bforartists has bpy.app.bfa_version_string.  Cached
#    once at import time.
_is_bfa: bool = is_bforartists()
_AGENT_ICON: str = safe_icon("WIZARD")

# The thinking spinner, phase wording and durations live in chat_status.py,
# shared with the GPU chat (Tier 4, GPU 2).
from .chat_status import (  # noqa: E402
    fmt_duration as _fmt_duration,
    phase_text as _phase_text,
    spinner_char as _spinner_char,
    spinner_glyph as _spinner_glyph,
)


# ---------------------------------------------------------------------------
# Turn grouping lives in chat_turns.py, shared with the GPU chat (Tier 4, GPU 2).
from .chat_turns import (  # noqa: E402
    NOTE_TITLES as _NOTE_TITLES,
    group_turns as _group_turns,
    is_system_note_msg as _is_system_note_msg,
    split_turn as _split_turn,
)


# ---------------------------------------------------------------------------
# Human-readable display helpers (chat + Session panel)

# ``[attached: a.png, b.jpg]`` -- the plain-text marker stored with a user
# message (chat_attachments.attachment_marker).  The model needs it; the chat
# shows an image icon instead.
_ATTACH_MARKER_RE = re.compile(r"\s*\[attached:\s*([^\]]*)\]")


def _split_attachment_marker(text: str) -> tuple[str, list[str]]:
    """Return ``(text without the marker, attached image names)``."""
    text = str(text or "")
    names: list[str] = []
    for m in _ATTACH_MARKER_RE.finditer(text):
        names.extend(n.strip() for n in m.group(1).split(",") if n.strip())
    return _ATTACH_MARKER_RE.sub("", text).strip(), names


def _draw_user_text(layout, text: str) -> None:
    """Draw a user message: its words, then one image icon per attachment."""
    clean, names = _split_attachment_marker(text)
    if clean:
        _draw_multiline(layout, clean)
    if names:
        row = layout.row(align=True)
        for _n in names[:6]:
            row.label(text="", icon='IMAGE_DATA')
        if len(names) > 6:
            row.label(text="+{:d}".format(len(names) - 6))


def _short_words(text: str, limit: int = 46) -> str:
    """First words of *text*, cut on a word boundary with an ellipsis."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(" ,.;:-") + "\u2026"


def _humanize_memory(block: str, max_lines: int = 10) -> list[str]:
    """Turn the stored memory note into short plain-language lines.

    The stored note is written for the model (a ``[Session memory]`` header,
    ``Last updated: turn N`` stamps, ``- user:`` / ``- assistant:`` bullets).
    The panel shows the same facts the way a person would say them.
    """
    out: list[str] = []
    for raw in str(block or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("[Session memory]") or line.startswith("Last updated"):
            continue
        low = line.lower()
        if low.rstrip(":") in ("recent context", "decisions", "pending", "done",
                               "errors seen", "objects & files touched", "goal"):
            heading = {"recent context": "Recently", "errors seen": "Problems hit",
                       "objects & files touched": "Touched",
                       "pending": "Still to do"}.get(low.rstrip(":"), line.rstrip(":"))
            out.append(heading + ":")
            continue
        line = line.lstrip("-*\u2022 ").strip()
        if line.lower().startswith("user:"):
            line = "You: " + line[5:].strip()
        elif line.lower().startswith("assistant:"):
            line = "Coworker: " + line[10:].strip()
        elif line.startswith("Earlier request:"):
            line = "Earlier you asked: " + line[len("Earlier request:"):].strip()
        line, _imgs = _split_attachment_marker(line)
        if line:
            out.append(("  " if not line.endswith(":") else "") + _short_words(line, 110))
    # Drop a trailing heading with nothing under it.
    while out and out[-1].endswith(":"):
        out.pop()
    if len(out) > max_lines:
        out = out[:max_lines] + ["  \u2026"]
    return out


_CHECKPOINT_REASONS = {
    "compaction": "auto",
    "overflow": "auto",
    "manual-compaction": "manual",
    "pre-restore": "before restore",
}


def _checkpoint_title(cp: dict, number: int) -> tuple[str, str]:
    """``("Checkpoint 3: fix the floating parts...", "10:42 | 24 msgs | ~4.1k tokens | auto")``."""
    words = ""
    for m in reversed(cp.get("history") or []):
        if m.get("role") != "user" or _is_system_note_msg(m):
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
        words, _imgs = _split_attachment_marker(str(content or ""))
        if words:
            break
    title = "Checkpoint {:d}: {:s}".format(number, _short_words(words) if words else "(start)")
    ts = str(cp.get("timestamp", ""))
    ts_short = ts.split(" ", 1)[1][:5] if " " in ts else ts
    try:
        size_chars = len(json.dumps(cp.get("history") or [], default=str))
    except (TypeError, ValueError):
        size_chars = 0
    tokens = size_chars / 3.5
    size = ("~{:.1f}k tokens".format(tokens / 1000.0) if tokens >= 1000
            else "~{:d} tokens".format(int(tokens)))
    parts = [p for p in (ts_short, "{:d} msgs".format(int(cp.get("message_count", 0) or 0)),
                         size, _CHECKPOINT_REASONS.get(str(cp.get("reason", "")), ""))
             if p]
    return title, " \u00b7 ".join(parts)






def _hist_index(history: list, msg: dict) -> int:
    """Return *msg*'s index in *history*, or -1 when it is no longer present.

    The conversation history is mutated on the turn's worker thread
    (compaction replaces it, spiral recovery truncates it, auto-continue pops
    from it) while this panel draws on the main thread.  A message captured by
    ``_group_turns`` can therefore be gone by the time its index is needed --
    and ``list.index`` raises ``ValueError``, which aborts the whole Panel
    draw and blanked the chat history for the rest of a long turn.  The copy
    operators already treat a negative index as "no message" (see
    ``BFACW_OT_copy_message``), so returning -1 is the correct, safe result.

    Matching is by IDENTITY first (handles equal-valued messages), falling
    back to equality.
    """
    for i, m in enumerate(history):
        if m is msg:
            return i
    try:
        return history.index(msg)
    except ValueError:
        return -1


def _wrap_text(text: str, width: int = _WRAP_WIDTH) -> str:
    """Wrap text to a given width for display in Blender labels."""
    if not text:
        return ""
    return "\n".join(
        textwrap.fill(line, width=width)
        for line in text.split("\n")
    )


#
# Blender UI primitives can't render bold or italic, don't have a monospace
# label, and have no table widget -- but we can simulate most of it with
# row/column layouts, scale_y tricks for headings, boxes for code/quotes,
# and splitting pipe tables into aligned columns.
# ---------------------------------------------------------------------------

_INLINE_BOLD_RE  = re.compile(r"\*\*([^*]+?)\*\*")
_INLINE_BOLD2_RE = re.compile(r"__([^_]+?)__")
_INLINE_ITAL_RE  = re.compile(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)")
_INLINE_ITAL2_RE = re.compile(r"(?<!_)_([^_\s][^_]*?)_(?!_)")
_INLINE_CODE_RE  = re.compile(r"`([^`]+?)`")
_INLINE_LINK_RE  = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING_RE      = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_RE         = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.+)$")
_HR_RE           = re.compile(r"^\s*(\*{3,}|-{3,}|_{3,})\s*$")
_QUOTE_RE        = re.compile(r"^\s*>\s?(.*)$")
_TABLE_SEP_RE    = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _strip_inline(text):
    """Flatten inline markdown that labels can't style."""
    text = _INLINE_BOLD_RE.sub(r"\1", text)
    text = _INLINE_BOLD2_RE.sub(r"\1", text)
    text = _INLINE_ITAL_RE.sub(r"\1", text)
    text = _INLINE_ITAL2_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _INLINE_LINK_RE.sub(r"\1", text)
    return text


_PARA_SCALE_Y = 0.78  # tight vertical spacing for paragraph text

_CAN_MULTILINE = None

def _can_multiline() -> bool:
    """Whether the host build exposes ``UILayout.label_multiline``.

    The native multi-line label API (Blender PR #154351, merged into
    the workshop/ios-workshop builds) wraps text to the *actual* layout
    width with a tight 0.75 UI_UNIT_Y line height and supports an icon,
    alignment and max-lines cap.  Older stock builds lack it, in which
    case the addon keeps the manual character-chop renderer.  Checked
    once via RNA so ``hasattr`` on a live layout is not required.
    """
    global _CAN_MULTILINE
    if _CAN_MULTILINE is None:
        try:
            import bpy
            _CAN_MULTILINE = any(
                fn.identifier == "label_multiline"
                for fn in bpy.types.UILayout.bl_rna.functions
            )
        except Exception:  # pylint: disable=broad-exception-caught
            _CAN_MULTILINE = False
    return _CAN_MULTILINE

_CODE_SCALE_Y = 0.72
def _wrap_for_label(text, width=40):
    """Wrap text for UILabel -- returns a list of lines."""
    if not text:
        return [""]
    return textwrap.wrap(text, width=width) or [""]

  # tighter again for monospace-style code blocks


# --- LaTeX -> plain text -----------------------------------------------------
# The model occasionally emits $$...$$ blocks or \frac{a}{b}-style markup even
# though the system prompt says not to. Blender's UI labels can't render math,
# so we preprocess to readable ASCII / Unicode equivalents -- matches how a
# human would write the same equation in a chat message.

_LATEX_BLOCK_RE  = re.compile(r"\$\$\s*(.+?)\s*\$\$", re.DOTALL)
_LATEX_INLINE_RE = re.compile(r"(?<![\$\w])\$([^\$\n]+?)\$(?!\w)")
_LATEX_FRAC_RE   = re.compile(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
_LATEX_SQRT_RE   = re.compile(r"\\sqrt\s*\{([^{}]+)\}")
_LATEX_CMD_TAIL_RE = re.compile(r"\\([a-zA-Z]+)")

# Single-arg styling / wrapper commands. Strip them to just their content so
# nested cases like \frac{\mathbf{v}}{\|\mathbf{v}\|} can be parsed by the
# (deliberately non-recursive) frac/sqrt regexes after a few passes.
_LATEX_STRIP_CMD_RE = re.compile(
    r"\\(?:mathbf|mathrm|mathit|mathsf|mathtt|mathbb|mathfrak|mathcal|"
    r"text|textbf|textit|textrm|textsf|texttt|"
    r"boldsymbol|bm|vec|hat|tilde|bar|dot|ddot|underline|overline|"
    r"operatorname)\s*\{([^{}]*)\}"
)

_LATEX_SYMBOLS = {
    r"\cdot":   "\u00b7",
    r"\times":  "\u00d7",
    r"\div":    "\u00f7",
    r"\pm":     "\u00b1",
    r"\mp":     "\u2213",
    r"\approx": "\u2248",
    r"\neq":    "\u2260",
    r"\leq":    "\u2264",
    r"\geq":    "\u2265",
    r"\to":     "\u2192",
    r"\infty":  "\u221e",
    r"\sum":    "\u03a3",
    r"\prod":   "\u03a0",
    r"\int":    "\u222b",
    r"\partial": "\u2202",
    r"\nabla":  "\u2207",
    r"\langle": "\u27e8", r"\rangle": "\u27e9",
    r"\lceil":  "\u2308", r"\rceil":  "\u2309",
    r"\lfloor": "\u230a", r"\rfloor": "\u230b",
    r"\|":      "\u2016",  # double-bar (norm)
    r"\alpha":  "\u03b1", r"\beta":  "\u03b2", r"\gamma":   "\u03b3", r"\delta":  "\u03b4",
    r"\epsilon":"\u03b5", r"\zeta":  "\u03b6", r"\eta":     "\u03b7", r"\theta":  "\u03b8",
    r"\iota":   "\u03b9", r"\kappa": "\u03ba", r"\lambda":  "\u03bb", r"\mu":     "\u03bc",
    r"\nu":     "\u03bd", r"\xi":    "\u03be", r"\pi":      "\u03c0", r"\rho":    "\u03c1",
    r"\sigma":  "\u03c3", r"\tau":   "\u03c4", r"\phi":     "\u03c6", r"\chi":    "\u03c7",
    r"\psi":    "\u03c8", r"\omega": "\u03c9",
    r"\Gamma":  "\u0393", r"\Delta": "\u0394", r"\Theta":   "\u0398", r"\Lambda": "\u039b",
    r"\Xi":     "\u039e", r"\Pi":    "\u03a0", r"\Sigma":   "\u03a3", r"\Phi":    "\u03a6",
    r"\Psi":    "\u03a8", r"\Omega": "\u03a9",
    r"\left":   "",  r"\right": "",
    r"\,":      " ", r"\;":     " ", r"\!":       "",
    r"\\":      "\n",  # LaTeX line break inside an equation
}


def _convert_latex_expr(expr):
    """Convert a single LaTeX expression body to plain text."""
    # 1) Strip styling/wrapper commands first -- this collapses
    #    \mathbf{v}, \text{normalized}, \vec{x} etc. to their inner content,
    #    so frac/sqrt's flat-brace regex can see through them. Iterate to
    #    handle nesting like \mathbf{\hat{n}}.
    for _ in range(6):
        new = _LATEX_STRIP_CMD_RE.sub(r"\1", expr)
        if new == expr:
            break
        expr = new
    # 2) frac/sqrt -- also iterate because substitutions can expose new matches.
    for _ in range(6):
        new = _LATEX_FRAC_RE.sub(r"(\1)/(\2)", expr)
        new = _LATEX_SQRT_RE.sub(r"sqrt(\1)", new)
        if new == expr:
            break
        expr = new
    # 3) Symbols.
    for k, v in _LATEX_SYMBOLS.items():
        expr = expr.replace(k, v)
    # 4) Strip remaining \command tokens -- keep the name as a fallback so
    #    users can still see what was meant (e.g., \mathbb -> mathbb).
    expr = _LATEX_CMD_TAIL_RE.sub(r"\1", expr)
    # 5) Collapse braces left over from stripped commands.
    expr = expr.replace("{", "").replace("}", "")
    return expr.strip()


_FENCE_PROTECT_RE       = re.compile(r"(```.*?```)", re.DOTALL)
_INLINE_CODE_PROTECT_RE = re.compile(r"(`[^`\n]+`)")

# x^2 -> x^2, 10^{-3} -> 10^-^3. Only digits + sign chars are converted, so
# `2^k`, `^L` (control chars in docs), and code paths like `path^foo`
# are left alone.
_SUPERSCRIPT_TR = str.maketrans(
    '0123456789-+',
    '\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207b\u207a')
_SUPERSCRIPT_RE = re.compile(r'\^(\{[\d\-+]+\}|[\d\-+]+)')


def _superscript_powers(text):
    """Convert ^N and ^{NN} to Unicode superscripts."""
    def repl(m):
        s = m.group(1)
        if s.startswith('{') and s.endswith('}'):
            s = s[1:-1]
        return s.translate(_SUPERSCRIPT_TR)
    return _SUPERSCRIPT_RE.sub(repl, text)


def _convert_latex(text):
    """Replace $$...$$ blocks and inline $...$ with readable plain text.
    Code fences and inline `code` are passed through untouched so $-syntax
    in shell snippets / variables doesn't get mangled."""
    def _block(m):
        return "\n" + _convert_latex_expr(m.group(1)) + "\n"

    def _convert_segment(seg):
        seg = _LATEX_BLOCK_RE.sub(_block, seg)
        seg = _LATEX_INLINE_RE.sub(
            lambda m: _convert_latex_expr(m.group(1)), seg,
        )
        # Catches both LaTeX-converted output (^2 left over from \frac/\sqrt
        # bodies) and plain-typed `mc^2`-style powers in normal prose.
        seg = _superscript_powers(seg)
        return seg

    out = []
    # Split on fenced code blocks (odd-index parts are fence content, kept verbatim)
    for i, part in enumerate(_FENCE_PROTECT_RE.split(text)):
        if i % 2 == 1:
            out.append(part)
            continue
        # Within non-fence text, also protect inline `code`
        sub_out = []
        for j, sub in enumerate(_INLINE_CODE_PROTECT_RE.split(part)):
            sub_out.append(sub if j % 2 == 1 else _convert_segment(sub))
        out.append("".join(sub_out))
    return "".join(out)


def _close_trailing_fence(text):
    """If the markdown text contains an odd number of triple-backtick
    fences, it ends with an open code block -- typically because the
    model hit max_tokens mid-snippet. Append a closing fence plus a
    one-line truncation note so the renderer doesn't treat everything
    that follows as code."""
    if text.count("```") % 2 == 0:
        return text
    sep = "" if text.endswith("\n") else "\n"
    return text + sep + "```\n_(response was cut off)_\n"



class BFACW_OT_copy_code_block(Operator):
    """Copy a code block from markdown to the clipboard."""
    bl_idname = "bfacw.copy_code_block"
    bl_label = "Copy Code"
    code_text: bpy.props.StringProperty(name="Code", default="")
    def execute(self, context):
        from . import log
        context.window_manager.clipboard = log.redact(self.code_text)     # (#346)
        self.report({"INFO"}, "Code copied to clipboard")
        return {"FINISHED"}

def _render_markdown(layout, md, width=40):
    """Walk a markdown string and render into the given layout.

    Paragraph text is emitted into a rolling compact column (align=True +
    reduced scale_y) so consecutive wrapped lines don't look double-spaced.
    Special blocks (code, table, heading, rule, quote) break that column
    and render in their own layouts so they aren't squished.
    """
    md = _convert_latex(md)
    # Auto-close an unterminated fenced block -- common when the model
    # hits max_tokens mid-code and the last ``` got truncated. Without
    # this, the markdown renderer treats everything after the opening
    # fence as one giant code block and the conversation layout breaks.
    md = _close_trailing_fence(md)
    lines = md.splitlines()
    n = len(lines)

    # Rolling paragraph column -- lazily created so each block break gets
    # its own column (which keeps tight spacing within but separates
    # visually from whatever comes next).
    para_col = [None]

    def _break_para():
        para_col[0] = None

    def _para_col():
        if para_col[0] is None:
            col = layout.column(align=True)
            col.scale_y = _PARA_SCALE_Y
            para_col[0] = col
        return para_col[0]

    def _emit_para(text, indent=""):
        if not text.strip():
            return
        col = _para_col()
        full = indent + text
        if _can_multiline():
            # Native multiline already has tight 0.75 UI_UNIT_Y leading;
            # keep the column at full scale so it does not over-squish.
            col.scale_y = 1.0
            col.label_multiline(text=full)
        else:
            for chunk in _wrap_for_label(full, width=width):
                col.label(text=chunk if chunk else " ")

    i = 0
    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        # ```fenced code block
        if stripped.startswith("```"):
            _break_para()
            lang = stripped[3:].strip() or "code"

            # Harvest the code body first so the copy button can receive it.
            i += 1
            code_body_lines = []
            while i < n and not lines[i].lstrip().startswith("```"):
                code_body_lines.append(lines[i])
                i += 1
            if i < n and lines[i].lstrip().startswith("```"):
                i += 1  # skip closing fence
            code_body = "\n".join(code_body_lines)

            box = layout.box()
            # Header row: language label on the left, [Copy][Run] on the
            # right. Run is Python-only -- executing a shell/JSON/etc fence
            # doesn't make sense and would just error.
            hrow = box.row(align=False)
            left = hrow.row()
            left.label(text=lang, icon='SCRIPT')
            right = hrow.row(align=True)
            right.alignment = 'RIGHT'
            cop = right.operator(
                "bfacw.copy_code_block", text="", icon='COPYDOWN',
            )
            cop.code_text = code_body


            col = box.column(align=True)
            col.scale_y = _CODE_SCALE_Y
            for code_line in code_body_lines:
                for chunk in _wrap_for_label(code_line, width=max(10, width - 2)):
                    col.label(text=chunk if chunk else " ")
            _break_para()
            continue

        # Pipe table (header + separator)
        if "|" in raw and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            _break_para()
            header_cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            table_start = i
            i += 2
            body_rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                body_rows.append(cells)
                i += 1

            try:
                tbox = layout.box()
                hrow = tbox.row(align=True)
                for cell in header_cells:
                    c = hrow.column(align=True)
                    c.label(text=_strip_inline(cell), icon='DOT')
                for row in body_rows:
                    while len(row) < len(header_cells):
                        row.append("")
                    brow = tbox.row(align=True)
                    for cell in row[: len(header_cells)]:
                        c = brow.column(align=True)
                        c.scale_y = _PARA_SCALE_Y
                        wrapped = _wrap_for_label(
                            _strip_inline(cell),
                            width=max(8, width // max(1, len(header_cells))),
                        )
                        for t in wrapped[:3]:
                            c.label(text=t if t else " ")
            except Exception as e:
                # Table data was malformed enough to break the renderer
                # (ragged rows, empty header, mismatched separator row).
                # Fall back to rendering the raw table block as plain
                # text so the content isn't lost.
                print(f"[Blender Buddy] table render fallback: {e}")
                fallback = layout.column(align=True)
                fallback.scale_y = _PARA_SCALE_Y
                for ln in lines[table_start:i]:
                    for chunk in _wrap_for_label(ln, width=width):
                        fallback.label(text=chunk if chunk else " ")
            _break_para()
            continue

        # Heading
        m = _HEADING_RE.match(stripped)
        if m:
            _break_para()
            level = len(m.group(1))
            text = _strip_inline(m.group(2))
            # Keyframe dots: size scales with heading level.
            # EXTREME=big, MOVING_HOLD=mid, JITTER/BREAKDOWN/GENERATED=small
            icon = {1: 'KEYTYPE_EXTREME_VEC',
                    2: 'KEYTYPE_MOVING_HOLD_VEC',
                    3: 'KEYTYPE_JITTER_VEC',
                    4: 'KEYTYPE_BREAKDOWN_VEC'}.get(level, 'KEYTYPE_GENERATED_VEC')
            if level == 1:
                # H1: separator + large scaled row + uppercase
                layout.separator()
                hrow = layout.row()
                hrow.scale_y = 1.6
                hrow.label(text=text.upper(), icon=icon)
            elif level == 2:
                # H2: slightly larger row, strong icon
                layout.separator(factor=0.5)
                hrow = layout.row()
                hrow.scale_y = 1.35
                hrow.label(text=text, icon=icon)
            elif level == 3:
                # H3: modest boost, distinct icon
                hrow = layout.row()
                hrow.scale_y = 1.2
                hrow.label(text=text, icon=icon)
            else:
                # H4-H6: subtle differentiation
                hrow = layout.row()
                hrow.scale_y = 1.1
                hrow.label(text=text, icon=icon)
            i += 1
            _break_para()
            continue

        # Horizontal rule
        if _HR_RE.match(raw):
            _break_para()
            layout.separator()
            i += 1
            continue

        # Blockquote
        mq = _QUOTE_RE.match(raw)
        if mq:
            _break_para()
            qbox = layout.box()
            qcol = qbox.column(align=True)
            qcol.scale_y = _PARA_SCALE_Y
            qtext = _strip_inline(mq.group(1) or "")
            for chunk in _wrap_for_label("| " + qtext, width=width):
                qcol.label(text=chunk)
            i += 1
            _break_para()
            continue

        # List item -- rendered into the rolling para column so successive
        # items share spacing.
        ml = _LIST_RE.match(raw)
        if ml:
            indent = len(ml.group(1))
            bullet = ml.group(2)
            text = _strip_inline(ml.group(3))
            prefix = "  " * (indent // 2)
            marker = "* " if bullet in ("-", "*", "+") else f"{bullet} "
            _emit_para(text, indent=prefix + marker)
            i += 1
            continue

        # Blank line -- break paragraph, no separator (keeps spacing tight).
        if not stripped:
            _break_para()
            i += 1
            continue

        # Regular paragraph line
        _emit_para(_strip_inline(stripped))
        i += 1

def _draw_multiline(
    layout: bpy.types.UILayout,
    text: str,
    width: int = _WRAP_WIDTH,
    icon: str = 'NONE',
) -> None:
    """Draw multi-line text in a layout, optionally with a leading icon.

    Prefers the host build's native ``label_multiline`` (Blender PR
    #154351, workshop builds) which wraps to the real layout width with
    a tight 0.75 UI_UNIT_Y line height - no manual chopping, no tall
    full-height rows per wrap chunk, so chat messages condense
    vertically. Falls back to character-based wrapping on builds that
    lack the API.  When *icon* is given it is placed on the first line
    (the fallback cannot attach an icon to a whole wrapped block).
    """
    if not text:
        return
    if _can_multiline():
        try:
            if icon and icon != 'NONE':
                layout.label_multiline(text=text, icon=icon)
            else:
                layout.label_multiline(text=text)
            return
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    lines = _wrap_text(text, width=width).split("\n")
    for i, line in enumerate(lines):
        layout.label(text=line, icon=icon if i == 0 else 'NONE')


def _draw_reasoning(
    layout: bpy.types.UILayout,
    text: str,
    label: str = "Thinking",
    is_thinking: bool = False,
    thinking_dots: int = 0,
    message_index: int = -1,
    archived: bool = False,
) -> None:
    """Draw reasoning (chain-of-thought) content in a collapsible panel.

    The entire reasoning section is collapsible.  While *is_thinking* is
    True the label animates with dots.  The *label* is stored when the
    reasoning was first captured so it doesn't flicker on every redraw.
    """
    if not text:
        return

    reasoning_lines = text.strip().split("\n")

    # Animate the label with Unicode spinner while thinking.
    if is_thinking:
        display_label = "{:s} {:s}".format(label, _spinner_glyph(thinking_dots))
        icon = _AGENT_ICON
    else:
        display_label = label
        icon = 'CHECKMARK'

    # Collapsible panel for the entire reasoning section.
    header, body = layout.panel("reasoning_{:d}".format(message_index), default_closed=True)
    header.label(text="{:s} ({:d} lines)".format(display_label, len(reasoning_lines)), icon=icon)
    if message_index >= 0:
        op = header.operator("bfacw.copy_message", text="", icon='COPYDOWN')
        op.message_index = message_index
        op.archived = archived

    if body:
        body.separator()
        for line in reasoning_lines:
            _draw_multiline(body, line)


def _draw_tool_summary(layout: bpy.types.UILayout, content: str, summary: str) -> None:
    """Draw a tool result with a human-readable summary.

    Shows the summary prominently.  If the full content differs from the
    summary (e.g., contains a traceback), show a collapsed detail section.
    """
    if not summary and not content:
        return

    display = summary if summary else content
    _draw_multiline(layout, display)

    # If there's a summary different from raw content, show the raw version
    # collapsed as a detail section.
    if summary and summary != content and len(content) > len(summary):
        detail_box = layout.box()
        detail_row = detail_box.row()
        detail_row.label(text="Details:", icon='TEXT')
        # Show the full raw content -- the user asked to see it all, and the
        # panel scrolls.
        _draw_multiline(detail_box, content, width=_WRAP_WIDTH)


def _draw_archive_node(
    layout: bpy.types.UILayout,
    retired_count: int,
    summary: str,
) -> None:
    """Draw a 'Checkpoint' node in the Workshop timeline.

    Marks where older messages were summarized out of the model's context
    (a checkpoint is saved first, so it can be restored from the Session
    panel).  The summary is the memory note written at that point.
    Display-only -- never sent to the model.
    """
    box = layout.box()
    row = box.row()
    row.label(
        text="Checkpoint \u2014 {:d} earlier message(s) summarized".format(retired_count),
        icon='BOOKMARKS',
    )
    body = str(summary or "").strip()
    if body:
        _draw_multiline(box, body)


def _draw_job_rows(layout: bpy.types.UILayout) -> None:
    """Tools 6 (#309): a row per background render or bake the agent is waiting for.

    Under them, Blender's own job bar: it shows the real bake percentage (not
    readable from Python) and its X cancels the job -- Python has no call that
    stops a render or bake, so this is the Stop for it.
    """
    from . import long_jobs
    jobs = long_jobs.running()
    if not jobs:
        return
    box = layout.box()
    for job in jobs:
        box.label(text="{:s} \u2014 {:s}".format(job["text"], job["label"]),
                  icon='RENDER_STILL' if job["kind"] == "RENDER" else 'TEXTURE')
    box.template_running_jobs()


def _draw_tool_inline(
    layout: bpy.types.UILayout,
    tool_name: str,
    display: str,
    is_error: bool,
    message_index: int = -1,
    archived: bool = False,
) -> None:
    """Draw a tool result as a sub-box inside the agent's message box."""
    tool_box = layout.box()
    row = tool_box.row()
    row.label(
        text="\u2699 {:s}".format(tool_name),
        icon=safe_icon('WARNING') if is_error else 'TOOL_SETTINGS',
    )
    if message_index >= 0:
        op = row.operator("bfacw.copy_message", text="", icon='COPYDOWN')
        op.message_index = message_index
        op.archived = archived
    _draw_multiline(tool_box, display)


# ---------------------------------------------------------------------------
# Properties

def _chat_history_dir() -> Path:
    """Return the directory where chat history JSON files are stored."""
    base = Path(bpy.utils.user_resource("SCRIPTS")) / "bfa_coworker_chat_history"
    base.mkdir(parents=True, exist_ok=True)
    return base


# The chat being shown (Chat 7, #156): its id, and the .blend it is continued in.
_CHAT: dict[str, str] = {"id": "", "blend": ""}


def _blend_key() -> str:
    return Path(bpy.data.filepath).stem if bpy.data.filepath else ""


def _chat_index():
    from .chat_history import ChatIndex
    index = ChatIndex(str(_chat_history_dir()))
    index.ensure()
    return index


def _current_chat_id() -> str:
    """This .blend's chat: the one shown, else the one last used here, else a new one."""
    blend = _blend_key()
    if not _CHAT["id"] or _CHAT["blend"] != blend:
        index = _chat_index()
        _CHAT["id"] = index.last_for_blend(blend) or index.new_id()
        _CHAT["blend"] = blend
    return _CHAT["id"]


def _chat_history_path() -> Path:
    """The current chat's file (``chats/<id>.json``; one per chat, not per .blend)."""
    return _chat_index().chat_path(_current_chat_id())


# ---------------------------------------------------------------------------
# Sticky attachment (survives undo / orphan cleanup)
#
# The socket is an ID pointer on the WindowManager.  Blender can free the
# image behind it without the add-on hearing about it -- the agent's own
# auto-undo restores a scene state from before the image was loaded, and an
# image nobody else uses is dropped when the file is saved and reopened --
# and the pointer then silently goes empty: "the image got cleared".  So the
# attached image is (1) given a fake user, so Blender keeps it and saves it
# with the .blend, (2) written to disk when it has no file of its own, and
# (3) remembered here, so the socket is re-linked whenever it empties
# WITHOUT the user clearing it.  Only the user's own clear (the socket's X,
# or "Send once") forgets it.

_sticky_attachment: dict = {}
# True while the add-on itself sets the socket (re-link), so the update
# callback does not treat it as a user action.
_sticky_relinking = False


def _remember_attachment(img) -> None:
    """Pin *img* for the session: fake user + file on disk + remembered."""
    if img is None:
        return
    had_fake = bool(getattr(img, "use_fake_user", False))
    try:
        img.use_fake_user = True
    except (AttributeError, RuntimeError):
        pass
    try:
        path = chat_attachments.persist_image(img)
    except Exception:  # pylint: disable=broad-exception-caught
        path = ""
    previous_uri = (_sticky_attachment.get("uri")
                    if _sticky_attachment.get("image_name") == img.name else "")
    _sticky_attachment.clear()
    _sticky_attachment.update({
        "image_name": img.name,
        "path": path,
        "name": chat_attachments.attachment_name(img),
        "blend": bpy.data.filepath,
        "uri": previous_uri or "",
        "we_set_fake_user": not had_fake,
    })


def _forget_attachment() -> None:
    """The user cleared the socket: drop the pin (and our fake user)."""
    name = _sticky_attachment.get("image_name")
    if name and _sticky_attachment.get("we_set_fake_user"):
        img = bpy.data.images.get(name)
        if img is not None:
            try:
                img.use_fake_user = False
            except (AttributeError, RuntimeError):
                pass
    _sticky_attachment.clear()


def _restore_attachment(props):
    """Re-link the socket if Blender emptied it behind the user's back.

    Returns the (re-linked) image, or ``None``.  Never crosses files: an
    attachment remembered for another .blend is forgotten instead.
    """
    global _sticky_relinking
    if props.chat_image is not None:
        return props.chat_image
    if not _sticky_attachment:
        return None
    if _sticky_attachment.get("blend") != bpy.data.filepath:
        _sticky_attachment.clear()
        return None
    img = bpy.data.images.get(_sticky_attachment.get("image_name") or "")
    if img is None:
        img = chat_attachments.load_image_file(_sticky_attachment.get("path") or "")
    if img is None:
        return None
    _sticky_relinking = True
    try:
        props.chat_image = img
    finally:
        _sticky_relinking = False
    try:
        img.use_fake_user = True
    except (AttributeError, RuntimeError):
        pass
    _sticky_attachment["image_name"] = img.name
    return img


# The thread an attach from our own operators is for (#449, #454): set around the socket
# write, so the image goes to the chat that asked for it, not to whatever chat
# ``context.area`` finds then (a timer or a file browser has no area: "All editors").
_ATTACH_TARGET: dict = {"thread": None}


def _on_chat_image_update(self, context) -> None:
    """RNA update for the socket: remember a new image, forget a user clear."""
    if _sticky_relinking:
        return
    # Our attaches name their thread; a native set (the socket's browse) is the area's chat's (#417).
    thread = _ATTACH_TARGET["thread"] or _ui_thread(context)
    _socket_set_by(thread, self.chat_image)     # this chat's thread's image now (#417)
    try:
        if self.chat_image is None:
            _forget_attachment()
        else:
            _remember_attachment(self.chat_image)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] attachment pin skipped -- {:s}".format(str(ex)))


class ChatHistoryProperties(PropertyGroup):  # type: ignore[misc]
    """Persistent chat history properties stored on the WindowManager."""

    chat_auto_mode: BoolProperty(  # type: ignore[valid-type]
        name="Auto",
        description=("Keep going on your own until the plan is done (e.g. overnight): after each good turn "
                     "Coworker sends the next step itself, allows the agent's tool requests your settings "
                     "allow, and answers its questions with its best judgement. It stops when the plan is "
                     "done, a limit is reached (Preferences), two turns make no progress, on errors, or "
                     "when you press Stop"),
        options={'SKIP_SAVE'},
        get=_auto_get, set=_auto_set,       # this chat's thread's Auto mode (#417)
    )

    chat_history_filter: StringProperty(  # type: ignore[valid-type]
        name="Search Chats",
        description="Show only the chats whose title has this text",
        default="",
        options={'SKIP_SAVE', 'TEXTEDIT_UPDATE'},
    )

    chat_input: StringProperty(  # type: ignore[valid-type]
        name="Input",
        description="Type your message for the Coworker (AI agent)",
        options={'SKIP_SAVE'},
        get=_draft_get, set=_draft_set,     # this chat's thread's draft (#417)
    )

    chat_status: StringProperty(  # type: ignore[valid-type]
        name="Status",
        default="Idle",
    )

    chat_mode: EnumProperty(  # type: ignore[valid-type]
        name="Mode",
        description="Coworker: it works in the scene. Ask: it answers without changing anything. "
                    "Plan: it makes a step plan to carry out later, with any engine",
        items=CHAT_MODE_ITEMS,
        default="AGENT",
        update=_on_chat_mode,      # External Agent: its own mode follows (#221)
    )

    chat_newest_first: BoolProperty(  # type: ignore[valid-type]
        name="Newest First",
        description="Show the most recent messages at the top of the chat history",
        default=True,
    )

    # -- Session memory & checkpoints (Tier 3) ----------------------
    session_show_checkpoints: BoolProperty(  # type: ignore[valid-type]
        name="Show Checkpoints",
        description="Expand the checkpoint list in the Session section",
        default=False,
    )

    session_checkpoint_index: IntProperty(  # type: ignore[valid-type]
        name="Checkpoint",
        description="Index of the selected checkpoint",
        default=0,
        min=0,
    )

    session_show_memory_editor: BoolProperty(  # type: ignore[valid-type]
        name="Edit Memory",
        description="Show the raw memory note the Coworker reads, to edit it",
        default=False,
    )

    session_memory_edit: StringProperty(  # type: ignore[valid-type]
        name="Session Memory",
        description="Editable session memory block (empty = unchanged)",
        default="",
    )

    # -- Image socket (issue #88, Tier 3k) --------------------------
    # One Blender-standard socket that every attach source feeds:
    # file attach, render/screen capture, drag-and-drop, and the
    # template_ID datablock browser below.  Encoded to a data URI on
    # the MAIN thread at send time (see chat_attachments).
    chat_image: PointerProperty(  # type: ignore[valid-type]
        name="Image",
        description="Image attached to the chat (drag-and-drop, attach, capture, or pick a datablock)",
        type=bpy.types.Image,
        update=_on_chat_image_update,
    )

    chat_image_send_once: BoolProperty(  # type: ignore[valid-type]
        name="Send Once",
        description=(
            "Send the attached image with the next message only, then "
            "detach it (off = sticky: re-sent every turn until cleared; the "
            "chat's pin button toggles this)"
        ),
        options={'SKIP_SAVE'},
        get=_send_once_get, set=_send_once_set,     # per thread (#417)
    )

    # The compact chat panel's height (Tier 4, GPU 5; see chat_editor._find_chat_rect).
    chat_panel_rows: FloatProperty(  # type: ignore[valid-type]
        name="Chat Height",
        description="Height of the sidebar chat (drag its bottom edge to change it)",
        default=22.0, min=6.0, max=80.0,
    )

    chat_image_expanded: BoolProperty(  # type: ignore[valid-type]
        name="Show Image Panel",
        description=(
            "Expand the image socket, thumbnail and capture buttons; "
            "collapse them to give the chat the panel back"
        ),
        default=True,
    )


def _load_chat_history() -> list[dict]:
    """Load conversation history from disk."""
    path = _chat_history_path()
    if path.exists():
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                history = json.load(fh)
            # Detect old sessions (no turn_start flags anywhere) for backward compat.
            # New sessions keep their turn_start flags so only real user messages
            # create turns -- agent-injected messages won't inflate turn count.
            return history
        except (json.JSONDecodeError, OSError):
            pass
    return []


# Thread lock for history serialization -- prevents concurrent threads
# from writing partial dumps when a turn finishes while another is active.
_history_save_lock = threading.Lock()


def _has_user_messages() -> bool:
    return any(m.get("role") == "user" for m in agent_controller._agent_state.conversation_history)


def _agent_sessions_file() -> Path:
    """Which External Agent session belongs to which chat (to resume it after a restart)."""
    return _chat_history_dir() / "agent_sessions.json"


def _read_agent_sessions(path: Path) -> dict:
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _saved_agent_session(path: Path, chat_key: str, preset_id: str) -> str:
    """The session id the agent *preset_id* last had for this chat, or ""."""
    entry = _read_agent_sessions(path).get(chat_key) or {}
    return str(entry.get("session", "")) if entry.get("agent") == preset_id else ""


_agent_sessions_lock = threading.Lock()     # the default chat's and the tabs' sessions (#431)


def _remember_agent_session(path: Path, chat_key: str, preset_id: str, session_id: str) -> None:
    """Keep *session_id* for this chat (any thread: plain file I/O, no bpy)."""
    with _agent_sessions_lock:
        data = _read_agent_sessions(path)
        data[chat_key] = {"agent": preset_id, "session": session_id}
        try:
            with open(str(path), "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError:
            pass


def _tab_chat_file(tag: str) -> str:
    """The chat file of the tab (thread) *tag*: ``<chat id>.json``, "" when it has none."""
    thread = agent_controller.THREADS.get(tag) if tag else None
    return thread.chat_id + ".json" if thread is not None and thread.chat_id else ""


def _thread_agent_session(path: Path, tag: str, preset_id: str) -> str:
    """The session the tab *tag*'s chat had with the agent (#431); "" for none (a new chat)."""
    chat_file = _tab_chat_file(tag)
    return _saved_agent_session(path, chat_file, preset_id) if chat_file else ""


def _remember_thread_session(path: Path, tag: str, preset_id: str, session_id: str) -> None:
    chat_file = _tab_chat_file(tag)
    if chat_file:
        _remember_agent_session(path, chat_file, preset_id, session_id)


def _session_memory_archive_path() -> Path:
    """Return the path of the session-memory archive (next to chat history)."""
    return _chat_history_dir() / "archive.jsonl"


def _session_memory_state_path() -> Path:
    """The current chat's session memory (checkpoints, goal and plan), next to it."""
    return _chat_index().memory_path(_current_chat_id())


def _save_session_memory_state() -> None:
    """Persist the session-memory store (memory note + checkpoints)."""
    from . import session_memory as _sm
    st = _sm.store
    st.archive_path = _session_memory_archive_path()
    try:
        with _sm.store_lock:
            payload = st.to_payload()
            payload["archive_path"] = str(st.archive_path)
        with open(str(_session_memory_state_path()), "w", encoding="utf-8") as fh:
            json.dump(_log.redact_data(payload), fh, indent=2)   # no key on disk (#418)
    except OSError:
        pass


def _load_session_memory_state() -> None:
    """Restore the session-memory store from its sidecar JSON, if present."""
    from . import session_memory as _sm
    st = _sm.store
    st.archive_path = _session_memory_archive_path()
    path = _session_memory_state_path()
    if not path.exists():
        return
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        with _sm.store_lock:
            st.load_payload(payload)
    except (json.JSONDecodeError, OSError):
        pass


def _save_chat_history() -> None:
    """Save conversation history to disk (thread-safe) with versioned copies."""
    import time as _time
    base_dir = _chat_history_dir()        # crash backups stay in the history folder
    with _history_save_lock:
        try:
            # What goes to disk has its keys as *** (#418); the chat on screen keeps them.
            history = _log.redact_data(agent_controller._agent_state.conversation_history)
            # Save timestamped copy.
            ts = _time.strftime("%Y-%m-%d_%H-%M-%S", _time.localtime())
            versioned_path = base_dir / "default_{:s}.json".format(ts)
            with open(str(versioned_path), "w", encoding="utf-8") as fh:
                json.dump(history, fh, indent=2)
            # Also save to default.json (latest).
            with open(str(_chat_history_path()), "w", encoding="utf-8") as fh:
                json.dump(history, fh, indent=2)
            # Prune old versions: keep last 10.
            _prune_old_sessions(base_dir)
            # Persist the session-memory store alongside (Tier 3).
            _save_session_memory_state()
            # Chat 7: the chat list knows it (title, when, engine, .blend).
            _chat_index().touch(_CHAT["id"], history,
                                blend=_CHAT["blend"], engine=_chat_engine_label(),
                                blend_path=bpy.data.filepath, mode=_operating_mode())
        except OSError:
            pass


def _save_thread_chat(thread) -> None:
    """An editor thread's chat in its own file; History lists it with its editor (#358)."""
    if thread is None or thread.is_default:
        _save_chat_history()
        return
    state = agent_controller.thread_state(thread)
    history = list(state.conversation_history)
    if not any(m.get("role") == "user" for m in history):
        return
    history = _log.redact_data(history)       # no key on disk (#418)
    from . import session_memory as _sm
    with _history_save_lock:
        try:
            index = _chat_index()
            if not thread.chat_id:
                thread.chat_id = index.new_id()
            with open(str(index.chat_path(thread.chat_id)), "w", encoding="utf-8") as fh:
                json.dump(history, fh, indent=2)
            with _sm.store_lock:
                payload = thread.store.to_payload()
            with open(str(index.memory_path(thread.chat_id)), "w", encoding="utf-8") as fh:
                json.dump(_log.redact_data(payload), fh, indent=2)
            index.touch(thread.chat_id, history, blend=_blend_key(), engine=_chat_engine_label(),
                        blend_path=bpy.data.filepath, mode=_operating_mode(), editor=thread.editor)
        except OSError:
            pass


def _load_thread_chat(thread) -> None:
    """*thread*'s saved chat (``thread.chat_id``) back into it: history and Goal & Plan."""
    from . import session_memory as _sm
    index = _chat_index()
    try:
        with open(str(index.chat_path(thread.chat_id)), "r", encoding="utf-8") as fh:
            history = json.load(fh)
    except (OSError, ValueError):
        history = []
    if isinstance(history, list) and history:
        agent_controller.thread_state(thread).conversation_history = \
            agent_controller._sanitize_loaded_history(history)
    try:
        with open(str(index.memory_path(thread.chat_id)), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        with _sm.store_lock:
            thread.store.load_payload(payload)
    except (OSError, ValueError, AttributeError):
        pass


@bpy.app.handlers.persistent
def _threads_on_save(*_args) -> None:
    """The .blend was saved (maybe under a new name): each editor's chat is filed under it,
    so reopening the file brings each editor its own last thread (#358)."""
    blend = _blend_key()
    if _CHAT["id"] and _CHAT["blend"] != blend:
        # Saved under a new name (or for the first time): the chat goes with the file, it
        # doesn't start again under the new name (#431).
        _CHAT["blend"] = blend
        if _has_user_messages():
            _save_chat_history()
        else:
            _chat_index().set_last_for_blend(blend, _CHAT["id"])
    for thread in agent_controller.THREADS.live():
        if not thread.is_default:
            _save_thread_chat(thread)
    _save_tabs()
    _save_area_tabs()
    if _TABS["blend"] is not None:
        _TABS["blend"] = blend          # saved under a new name: these tabs are its tabs


@bpy.app.handlers.persistent
def _threads_on_load(*_args) -> None:
    """Another .blend opens: the old file's session is saved -- its chat, tabs and each
    area's tab (#431) -- and its editor threads are let go (#358)."""
    if _has_user_messages() and not agent_controller._agent_state.turn_active:
        _save_chat_history()
    _forget_editor_threads()


@bpy.app.handlers.persistent
def _session_on_load(*_args) -> None:
    """A .blend opened (File > Open, recent files, History): its session comes back (#431).

    All editors shows the chat last used in that file, with its Goal & Plan and memory, so
    the next turn has it as context (Local, Remote).  On External Agent the agent
    continues that chat's own conversation (its saved session), or starts a fresh one --
    before, it went on in the old file's conversation.  The tabs and each area's tab come
    back when a chat is first drawn (_restore_tabs)."""
    if agent_controller._agent_state.turn_active:
        return              # a turn still writes the old chat: it stays until the turn ends
    try:
        index = _chat_index()
        blend = _blend_key()
        _reset_for_chat_switch()
        _CHAT["id"], _CHAT["blend"] = index.last_for_blend(blend) or index.new_id(), blend
        _restore_chat_history()
        if _acp is not None and _acp.connected:
            _acp.drop_threads()
            resume = (_saved_agent_session(_agent_sessions_file(), _CHAT["id"] + ".json", _acp.preset_id)
                      if _has_user_messages() else "")
            _acp.switch_session(resume)
        _tag_chat_regions_redraw()
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] couldn't bring this file's chats back: {}".format(ex))


def _forget_editor_threads() -> None:
    """Another .blend: its editors bring their own threads back when first shown."""
    reg = agent_controller.THREADS
    for thread in reg.live():
        if not thread.is_default:
            _save_thread_chat(thread)
    _save_tabs()
    _save_area_tabs()
    for thread in reg.live():
        if not thread.is_default and not agent_controller.thread_state(thread).turn_active:
            reg.remove(thread.id)
    _TABS["blend"] = None               # the next file's tabs come back when first shown
    _AREA_PINS.clear()
    if reg.get(_socket().focus) is None:
        _socket().focus = reg.default.id


def _current_chat_of(thread) -> str:
    """*thread*'s chat in History (the default thread's is this .blend's chat)."""
    return _CHAT["id"] if thread.is_default else thread.chat_id


def _prune_old_sessions(base_dir) -> None:
    """Keep at most 10 versioned session files, remove oldest."""
    import re as _re
    pattern = _re.compile(r"^default_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.json$")
    files = []
    for f in base_dir.iterdir():
        if f.is_file() and pattern.match(f.name):
            files.append(f)
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    for old_file in files[10:]:
        try:
            old_file.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Operators


def _capture_chat_attachment(props) -> tuple[list[str] | None, list[str] | None]:
    """Encode the image socket for a send -- MAIN THREAD ONLY.

    Returns ``(attachments, attachment_names)`` ready for
    ``run_conversation_turn`` / ``enqueue_message``, or ``(None, None)``
    for a plain-text send (empty socket, or an image that cannot be
    encoded).  Called by the send operators BEFORE any worker thread
    starts: encoding reads bpy image pixels, which the turn worker must
    never touch.  A successful capture consumes the socket when
    "Send Once" is ticked; the default sticky socket keeps the image
    for every following send.
    """
    img = props.chat_image
    if img is None:
        # Emptied behind the user's back (undo, orphan cleanup)?  Re-link
        # the pinned image; failing that, re-send its last encoded copy.
        img = _restore_attachment(props)
    if img is None:
        _cached = _sticky_attachment.get("uri") if _sticky_attachment else ""
        if _cached:
            return [_cached], [_sticky_attachment.get("name") or "image"]
        return None, None
    uri = chat_attachments.image_to_data_uri(img)
    if not uri:
        # Oversized or broken: keep the socket (so the user can fix it)
        # and send this message as plain text.
        return None, None
    name = chat_attachments.attachment_name(img)
    if _sticky_attachment.get("image_name") == getattr(img, "name", None):
        _sticky_attachment["uri"] = uri
    if props.chat_image_send_once:
        props.chat_image = None
    return [uri], [name]


def _warn_failed_attachment(op, props) -> None:
    """Warn when the socket holds an image that could not be encoded.

    ``_capture_chat_attachment`` returns no attachments for both an
    empty socket and a failed encode, and deliberately keeps the socket
    on failure so the user can retry -- so a set socket plus an empty
    result means the encode failed and the send is silently plain text.
    Say so instead.
    """
    if props.chat_image is None:
        return
    op.report(
        {"WARNING"},
        "Could not prepare the attached image; sending without it. "
        "Free space in the temp folder, or save/pack the image, then retry.",
    )


def _attach_thread(context, thread_id: str = ""):
    """The thread an attach goes to: *thread_id*'s, taken when the attach began (the chat whose
    button was used), while it is open; else the chat of *context*'s area (#449, #454)."""
    thread = agent_controller.THREADS.get(thread_id) if thread_id else None
    return thread if thread is not None else _ui_thread(context)


def _attach_to_socket(context, op, img, thread=None) -> None:
    """Store *img* in the chat socket as *thread*'s image and confirm on *op* (main thread).

    *thread* is the chat the attach is for, found when it began (#449, #454): this may run
    from a timer or after a file browser, with no area in *context*.  ``None``: the chat of
    *context*'s area.  *op* ``None``: no report."""
    props = context.window_manager.bfacw_chat_props  # type: ignore[attr-defined]
    _ATTACH_TARGET["thread"] = thread if thread is not None else _ui_thread(context)
    try:
        props.chat_image = img
    finally:
        _ATTACH_TARGET["thread"] = None
    # Warm the preview now so the panel can draw the thumbnail on its
    # first redraw instead of one frame later (the pixels are filled by
    # Blender's preview thread after ``preview_ensure()``).
    _ensure_attachment_preview(img)
    if op is not None:
        op.report({"INFO"},
                  f"Attached {chat_attachments.attachment_name(img)}")


class BFACW_OT_chat_send(Operator):  # type: ignore[misc]
    """Send the current input to the Coworker agent (or queue if busy)."""
    bl_idname = "bfacw.chat_send"
    bl_label = "Send"
    bl_description = "Send your message to the Coworker agent"

    # Right-click Explain / Translate (#358): start in this editor kind's thread.
    editor: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]
    # Auto mode's next step (#417): this thread, and this text instead of its draft.
    thread_id: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]
    text: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        from . import threads
        thread = (agent_controller.THREADS.get(self.thread_id) if self.thread_id else None) or (
            threads.pick(agent_controller.THREADS, self.editor, focus=True, create=_editor_thread)
            if self.editor else _ui_thread(context))
        _focus_thread(thread)               # its attachment is in the socket now (#417)
        message = (self.text or thread.draft).strip()
        if not message:
            return {"CANCELLED"}
        if _answer_typed(message, context):     # an open question (ask_user) takes it as its answer
            if not self.text:
                thread.draft = ""
            _redraw_areas(context)
            return {"FINISHED"}

        if not agent_controller._agent_state.mcp_server_running:
            self.report({"WARNING"}, "Coworker is not running. Start it from Preferences or the Chat panel.")
            return {"CANCELLED"}

        # Sync preferences to config, then read LLM config.
        prefs = context.preferences.addons[__package__].preferences
        _sync_prefs_to_config(prefs)
        llm_cfg = llm_manager.get_config()
        llm_url = None
        api_key = None
        model = None
        if llm_cfg.mode == "remote":
            llm_url = llm_cfg.remote_api_url
            api_key = llm_cfg.remote_api_key
            model = llm_cfg.remote_model or None

        # Get effective ports from preferences.
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        actual_mcp = agent_controller._agent_state.mcp_port_actual
        send_mcp_port = actual_mcp if actual_mcp else _mcp_port

        # Encode the image socket HERE, on the main thread, before any
        # worker thread starts (Tier 3k): the turn worker never touches
        # bpy, and both the queued and direct paths below need the payload.
        attachments, attachment_names = _capture_chat_attachment(props)
        if attachments is None:
            _warn_failed_attachment(self, props)

        state = agent_controller.thread_state(thread)
        engine = _thread_engine(thread, llm_cfg.mode)
        external = _is_external_agent(context)
        if external:
            problem = _ensure_bridge(prefs, getattr(_acp, "bridge_port", 0))
            if problem:
                self.report({"WARNING"}, problem)
            _sync_agent_mode(props.chat_mode, wait=True)     # in case it connected after you picked (#221)
        # Sent from an editor's sidebar chat: say which editor, and what it shows (#291).
        # The thread's editor context comes from where it was made (#427).
        editor_line = _editor_context_line(context, bool(thread.editor)) if _compact_sidebar(context) else ""
        prompt = Prompt(
            text=message,
            images=list(attachments or []),
            image_names=list(attachment_names or []),
            chat_mode=props.chat_mode,
            context=" ".join(p for p in (_scene_context_line(context), editor_line) if p) if external else "",
            thread=threads.tag(thread),
        )
        config = LoopConfig(llm_url=llm_url or None, api_key=api_key or None,
                            model=model, mcp_port=send_mcp_port)
        # The thread's system prompt (and where the user is) is read now, on the main
        # thread, also for a message that waits for another thread's turn (#358).
        agent_controller.prefetch_main_thread_context(thread)
        if editor_line and not external:     # our loop: the editor rides with this turn's system prompt
            state.prefetched_system_prompt += "\n\n# Where the user is\n" + editor_line

        # A running turn queues the message, unless the user pressed Stop
        # (see LoopEngine.submit).  Another thread's turn makes it wait in its own
        # thread's queue (one turn at a time, #357).  Only the started path shows "reading".
        busy = engine.is_busy() and not agent_controller.thread_stop(thread).is_set()
        if busy or (not external and agent_controller.turn_blocked(thread)):
            pos = engine.enqueue(prompt, config)
            if not self.text:
                thread.draft = ""
            other = agent_controller.THREADS.running()
            self.report({"INFO"}, "Message queued (position {:d})".format(pos) if busy or other is None else
                        "Waits for the {:s} thread's turn".format(_thread_title(other)))
            _redraw_areas(context)
            return {"FINISHED"}

        # Clear input and start processing.  Show the "reading" phase at once,
        # before the worker thread even starts, so the send never looks frozen.
        if not self.text:
            thread.draft = ""
        state.turn_phase = "reading"
        if thread.is_default:       # the shared status line mirrors the default thread only
            props.chat_status = "Reading your message..."
        state.ui_status = "Reading your message..."

        from . import coworker_api
        coworker_api.turn_started(_turn_label(context, thread))     # the undo step to revert to
        _approvals_new_turn(thread)                            # "Allow this turn" ends with the turn
        thread.unread = False
        engine.submit(prompt, config)
        return {"FINISHED"}


class BFACW_OT_chat_clear(Operator):  # type: ignore[misc]
    """This chat goes to History and its tab starts empty"""
    bl_idname = "bfacw.chat_clear"
    bl_label = "Start This Chat Fresh"
    bl_description = "This chat goes to History and its tab starts empty"

    # The tab it was drawn for (#466): a menu names it, so the entry acts on that chat
    # wherever it runs; "" is the chat of the area in context.
    thread_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import threads
        reg = agent_controller.THREADS
        thread = reg.get(self.thread_id) if self.thread_id else _ui_thread(context)
        if thread is None:              # its tab was closed meanwhile
            return {"CANCELLED"}
        why = threads.close_refused(reg, thread, agent_controller.thread_state(thread).turn_active)
        if why:                         # not while its turn runs
            self.report({"WARNING"}, why)
            return {"CANCELLED"}
        if thread.is_default:
            _fresh_default_chat()
        else:                           # another tab: a fresh chat for it alone (#358), the tab stays
            _new_chat_in_thread(thread)
            _save_tabs()
        _redraw_all_chats()
        return {"FINISHED"}


def _fresh_default_chat() -> None:
    """All editors starts fresh: its chat stays in History (× on its tab, Start This Chat Fresh)."""
    from . import session_memory as _sm
    agent_controller._agent_state.conversation_history.clear()
    agent_controller._agent_state.streaming_text = ""
    agent_controller._agent_state.reasoning_text = ""
    agent_controller._agent_state.thinking_dots = 0
    # Reset token accounting so the context bar returns to
    # "No usage recorded yet" for the new thread.
    agent_controller._agent_state.reset_usage()
    # Reset session memory + checkpoints so the new thread does not inherit
    # the previous thread's memory block or turn counter.
    with _sm.store_lock:
        _sm.store.reset()
    agent_controller._session_turn_count = 0
    agent_controller._reset_session_domains()
    # Fresh thread -> forget which datablocks the coworker created so a
    # later turn does not re-lock the old thread's objects.
    agent_controller.clear_session_scene_lock()
    # Clear Coworker_* text datablocks from the text editor.
    agent_controller._clear_coworker_text_blocks()
    # Clear cached system prompt so project rules are reloaded on next turn.
    agent_controller._clear_system_prompt_cache()
    # Chat 7: a new chat; the old one stays in the History list.
    _start_new_chat()
    # External Agent: the agent forgets the old conversation as well.
    if _acp is not None and _acp.connected:
        _acp.new_session()
    _save_chat_history()


def _new_chat_in_thread(thread) -> None:
    """Start This Chat Fresh in a tab other than All editors: its history, plan, domains, text blocks and
    remembered scene-lock names start again; the other threads keep theirs (#358)."""
    from . import session_memory as _sm
    state = agent_controller.thread_state(thread)
    _save_thread_chat(thread)
    state.conversation_history = []
    state.streaming_text = state.reasoning_text = ""
    state.reset_usage()
    with _sm.store_lock:
        thread.store.reset()
    thread.turn_count = 0
    agent_controller._reset_session_domains(thread)
    agent_controller.clear_session_scene_lock(thread)
    agent_controller._clear_coworker_text_blocks(thread)
    thread.chat_id = _chat_index().new_id()
    thread.session = ""
    if _acp is not None and _acp.connected:
        from . import threads
        _acp.forget_thread(threads.tag(thread))     # its next turn opens a fresh session


_RECENT: dict = {"mtime": None, "paths": []}


def _recent_blend_files() -> list[str]:
    """Blender's recent-files list (File > Open Recent), re-read when it changes."""
    try:
        path = os.path.join(bpy.utils.user_resource('CONFIG'), "recent-files.txt")
        mtime = os.path.getmtime(path)
    except (OSError, AttributeError):
        return []
    if _RECENT["mtime"] != mtime:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                _RECENT["paths"] = [line.strip() for line in fh if line.strip()]
        except OSError:
            _RECENT["paths"] = []
        _RECENT["mtime"] = mtime
    return _RECENT["paths"]


def _chat_blend_path(entry: dict) -> str:
    """The .blend a History entry was in: its stored path, or -- for chats saved before
    the path was kept (#273) -- the recent file with that name."""
    path = str(entry.get("blend_path") or "")
    if path and os.path.isfile(path):
        return path
    name = str(entry.get("blend") or "")
    if not name:
        return ""
    return next((p for p in _recent_blend_files() if Path(p).stem == name and os.path.isfile(p)), "")


def _operating_mode() -> str:
    try:
        return bpy.context.preferences.addons[__package__].preferences.operating_mode
    except (AttributeError, KeyError):
        return ""


def _history_mode(entry: dict) -> str:
    """Where a chat ran, as an operating mode (older entries only kept the engine's name)."""
    if entry.get("mode"):
        return str(entry["mode"])
    engine = str(entry.get("engine") or "")
    return {"Local": "LOCAL_LLM", "Remote": "REMOTE_API", "External Harness": "EXTERNAL_HARNESS",
            "": ""}.get(engine, "EXTERNAL_AGENT")


def _chat_engine_label() -> str:
    """Which engine this chat runs on, for the History list."""
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        mode = prefs.operating_mode
    except (AttributeError, KeyError):
        return ""
    if mode == "EXTERNAL_AGENT":
        return (_acp.agent_label if _acp is not None and _acp.agent_label else "External Agent")
    return {"LOCAL_LLM": "Local", "REMOTE_API": "Remote", "EXTERNAL_HARNESS": "External Harness"}.get(mode, "")


def _start_new_chat() -> None:
    index = _chat_index()
    _CHAT["id"], _CHAT["blend"] = index.new_id(), _blend_key()
    index.set_last_for_blend(_CHAT["blend"], _CHAT["id"])


def _reset_for_chat_switch() -> None:
    """What a fresh chat resets, before another chat comes in."""
    from . import session_memory as _sm
    state = agent_controller._agent_state
    state.conversation_history = []
    state.streaming_text = state.reasoning_text = ""
    state.reset_usage()
    with _sm.store_lock:
        _sm.store.reset()
    agent_controller._session_turn_count = 0
    agent_controller._reset_session_domains()
    agent_controller.clear_session_scene_lock()
    agent_controller._clear_system_prompt_cache()


class BFACW_OT_chat_where(Operator):  # type: ignore[misc]
    """Where this chat ran"""
    bl_idname = "bfacw.chat_where"
    bl_label = "Where It Ran"
    bl_options = {'INTERNAL'}

    engine: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties) -> str:
        return "This chat ran on {:s}".format(properties.engine) if properties.engine else cls.__doc__

    def execute(self, _context: bpy.types.Context) -> set[str]:
        return {"CANCELLED"}          # just the icon and its tooltip


class BFACW_OT_chat_open_blend(Operator):  # type: ignore[misc]
    """Open the .blend this chat was in and continue the chat there (it asks first only when this file
has unsaved changes)"""
    bl_idname = "bfacw.chat_open_blend"
    bl_label = "Open the Chat's .blend"
    bl_options = {'INTERNAL'}

    chat_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]
    # History's file row (#431): open that .blend with its whole session.
    blend: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties) -> str:
        if properties.blend:
            return ("Open {:s}.blend: its chats come back as they were, each editor on the tab it showed "
                    "(it asks first only when this file has unsaved changes)".format(properties.blend))
        return cls.__doc__

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not agent_controller._agent_state.is_thinking

    def _path(self) -> tuple[str, str]:
        """(path, why not) for the .blend: why not when it moved, was renamed or is gone."""
        index = _chat_index()
        if self.blend:
            group = next((g for g in index.files() if g["blend"] == self.blend), None) or {}
            entries = group.get("chats") or []
            entry = next((e for e in entries if _chat_blend_path(e)), entries[0] if entries else {})
        else:
            entry = index.get(self.chat_id) or {}
        path = _chat_blend_path(entry)
        if path:
            return path, ""
        known = str(entry.get("blend_path") or "")
        name = str(entry.get("blend") or self.blend or "")
        return "", ("{:s}.blend isn't at {:s} any more: it was moved, renamed or deleted".format(name, known)
                    if known else "{:s}.blend wasn't found (moved, renamed or deleted?)".format(name))

    def invoke(self, context: bpy.types.Context, event) -> set[str]:
        # Straight in (no file browser): a confirmation only when this file has unsaved changes.
        path, why = self._path()
        if not path:
            self.report({"ERROR"}, why)
            return {"CANCELLED"}
        if bpy.data.is_dirty:
            return context.window_manager.invoke_confirm(
                self, event, title="Open {:s}?".format(Path(path).name),
                message="Unsaved changes in this file are lost", confirm_text="Open", icon=safe_icon('WARNING'))
        return self.execute(context)

    def execute(self, context: bpy.types.Context) -> set[str]:
        path, why = self._path()
        if not path:
            self.report({"ERROR"}, why)
            return {"CANCELLED"}
        if self.chat_id and not self.blend:
            _chat_index().set_last_for_blend(Path(path).stem, self.chat_id)   # the file continues this chat
        # The file's session comes back on load (_session_on_load): its chat, tabs, each area's tab.
        try:
            bpy.ops.wm.open_mainfile("EXEC_DEFAULT", filepath=path)     # no file browser, no second ask
        except RuntimeError as ex:
            self.report({"ERROR"}, "Couldn't open {:s}: {:s}".format(Path(path).name, str(ex).splitlines()[-1]))
            return {"CANCELLED"}
        return {"FINISHED"}


class BFACW_OT_chat_open(Operator):  # type: ignore[misc]
    """Continue this chat: its messages, plan and memory come back (on External Agent, the
agent continues its own conversation for it too)"""
    bl_idname = "bfacw.chat_open"
    bl_label = "Open Chat"
    bl_options = {'INTERNAL'}

    chat_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not _ui_state(context).is_thinking

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import threads
        index = _chat_index()
        entry = index.get(self.chat_id) if self.chat_id else None
        if entry is None:
            return {"CANCELLED"}
        # #427: a History chat opens as a tab (or its open tab is shown) in this area.
        reg = agent_controller.THREADS
        if self.chat_id == _CHAT["id"]:
            thread = reg.default
        else:
            thread = threads.reopen(reg, self.chat_id, str(entry.get("editor", "")), make=_editor_thread)
            _save_tabs()
        _show_in_area(getattr(context, "area", None), thread)
        self.report({"INFO"}, "Opened: {:s}".format(entry.get("title", "")))
        return {"FINISHED"}


class BFACW_OT_chat_rename(Operator):  # type: ignore[misc]
    """Give this chat a name of your own"""
    bl_idname = "bfacw.chat_rename"
    bl_label = "Rename Chat"
    bl_options = {'INTERNAL'}

    chat_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]
    title: StringProperty(name="Name", options={'SKIP_SAVE'})  # type: ignore[valid-type]

    def invoke(self, context: bpy.types.Context, _event) -> set[str]:
        entry = _chat_index().get(self.chat_id)
        if entry is None:
            return {"CANCELLED"}
        self.title = entry.get("title", "")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not _chat_index().rename(self.chat_id, self.title):
            return {"CANCELLED"}
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_chat_pin(Operator):  # type: ignore[misc]
    """Pin this chat to the top of the History list (click again to unpin)"""
    bl_idname = "bfacw.chat_pin"
    bl_label = "Pin Chat"
    bl_options = {'INTERNAL'}

    chat_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> set[str]:
        index = _chat_index()
        entry = index.get(self.chat_id)
        if entry is None:
            return {"CANCELLED"}
        index.set_pinned(self.chat_id, not entry.get("pinned"))
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_chat_delete(Operator):  # type: ignore[misc]
    """Delete this chat and its memory for good (the scene isn't touched)"""
    bl_idname = "bfacw.chat_delete"
    bl_label = "Delete Chat"
    bl_options = {'INTERNAL'}

    chat_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not agent_controller._agent_state.is_thinking

    def invoke(self, context: bpy.types.Context, event) -> set[str]:
        entry = _chat_index().get(self.chat_id)
        if entry is None:
            return {"CANCELLED"}
        return context.window_manager.invoke_confirm(
            self, event, title="Delete Chat?", message="\"{:s}\" and its memory are deleted for good. "
                                                       "The scene isn't touched.".format(entry.get("title", "")),
            confirm_text="Delete", icon=safe_icon('WARNING'))

    def execute(self, context: bpy.types.Context) -> set[str]:
        reg = agent_controller.THREADS
        open_tab = next((t for t in reg.live() if _current_chat_of(t) == self.chat_id), None)
        _chat_index().delete(self.chat_id)
        if open_tab is not None and open_tab.is_default:
            _fresh_default_chat()           # All editors starts fresh (its tab stays)
        elif open_tab is not None and not agent_controller.thread_state(open_tab).turn_active:
            reg.remove(open_tab.id)         # its tab closes with it (#427)
            _save_tabs()
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_PT_chat_history(Panel):  # type: ignore[misc]
    """Your chats by .blend: open a file with all its chats, or continue any chat here"""
    bl_label = "History"
    bl_idname = "BFACW_PT_chat_history"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 3

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        # #431: the files, newest first, each with its chats (tabs) under it.
        layout = self.layout
        props = context.window_manager.bfacw_chat_props
        row = layout.row(align=True)
        row.prop(props, "chat_history_filter", text="", icon='VIEWZOOM')
        groups = _chat_index().files(props.chat_history_filter)
        if not groups:
            layout.label(text="No chats yet" if not props.chat_history_filter else "No chat matches",
                         icon='INFO')
            return
        current = _current_chat_of(_ui_thread(context))     # this chat's thread's (#417)
        col = layout.column(align=True)
        shown = total = 0
        for group in groups:
            total += len(group["chats"])
            if shown >= _HISTORY_ROWS:
                continue
            box = col.box()
            _draw_history_file(box, group)
            for entry in group["chats"]:
                if shown >= _HISTORY_ROWS:
                    break
                _draw_history_chat(box, entry, entry["id"] == current)
                shown += 1
        if total > shown:
            layout.label(text="{:d} more: search to find them".format(total - shown))


def _draw_history_file(box, group: dict) -> None:
    """A file row: the .blend's name, when it was last used, and Open (#431)."""
    from .chat_history import relative_time
    head = box.row(align=True)
    name = group["blend"]
    if not name:
        head.label(text="Not saved in a file", icon='FILE')
        return
    path = _chat_blend_path({"blend": name, "blend_path": group.get("blend_path", "")})
    here = bool(bpy.data.filepath) and bool(path) and os.path.normcase(path) == os.path.normcase(bpy.data.filepath)
    head.label(text=name, icon='FILE_BLEND')
    sub = head.row(align=True)
    sub.alignment = 'RIGHT'
    sub.label(text="open" if here else relative_time(float(group.get("updated", 0))))
    if path and not here:           # an icon: it fits a narrow sidebar (#273)
        sub.operator("bfacw.chat_open_blend", text="", icon='FILEBROWSER').blend = name


def _draw_history_chat(box, entry: dict, is_current: bool) -> None:
    """A chat under its file: pin, its title (click: continue it here, as a tab), rename,
    delete; then its editor, where it ran and when."""
    from .chat_history import relative_time
    head = box.row(align=True)
    head.separator(factor=1.0)      # nested under its file
    head.operator("bfacw.chat_pin", text="", emboss=False,
                  icon='PINNED' if entry.get("pinned") else 'UNPINNED').chat_id = entry["id"]
    op = head.operator("bfacw.chat_open", text=entry.get("title") or "(untitled)", emboss=False,
                       icon='RADIOBUT_ON' if is_current else 'RADIOBUT_OFF', depress=is_current)
    op.chat_id = entry["id"]
    head.operator("bfacw.chat_rename", text="", emboss=False, icon='GREASEPENCIL').chat_id = entry["id"]
    head.operator("bfacw.chat_delete", text="", emboss=False, icon='TRASH').chat_id = entry["id"]
    # Its editor (All editors: the world), where it ran as the mode's icon (#239), when.
    sub = box.row(align=True)
    sub.scale_y = 0.8
    sub.separator(factor=1.0)
    sub.label(text="", icon=_editor_icon(str(entry.get("editor") or "")))
    mode = _history_mode(entry)
    icon = next((item[3] for item in OPERATING_MODE_ITEMS if item[0] == mode), 'BLANK1')
    sub.operator("bfacw.chat_where", text="", icon=icon, emboss=False).engine = str(entry.get("engine") or "")
    sub.label(text=relative_time(float(entry.get("updated", 0))))


_HISTORY_ROWS = 25


class BFACW_OT_queue_item(Operator):  # type: ignore[misc]
    """A queued message: send it next, edit it, delete it, or send it now"""
    bl_idname = "bfacw.queue_item"
    bl_label = "Queued Message"
    bl_options = {'INTERNAL'}

    action: EnumProperty(  # type: ignore[valid-type]
        items=(("NEXT", "Next", "Move this message to the front: it goes right after the running turn"),
               ("EDIT", "Edit", "Take it out of the queue and back into the input box to change it"),
               ("DELETE", "Delete", "Take this message out of the queue"),
               ("SEND", "Send Now", "Nothing is running: send this message now")),
        options={'SKIP_SAVE'},
    )
    index: IntProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties) -> str:
        return {"NEXT": "Move this message to the front: it goes right after the running turn",
                "EDIT": "Take it out of the queue and back into the input box to change it",
                "DELETE": "Take this message out of the queue",
                "SEND": "Nothing is running: send the next message now"}.get(properties.action, cls.__doc__)

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent = _is_external_agent(context) and _acp is not None
        props = context.window_manager.bfacw_chat_props
        queue = agent_controller.thread_queue(_ui_thread(context))      # this chat's thread (#358)
        if self.action == "NEXT":
            ok = _acp.queue_to_front(self.index) if agent else queue.move_to_front(self.index)
            if not ok:
                return {"CANCELLED"}
        elif self.action in ("EDIT", "DELETE", "SEND"):
            taken = _acp.remove_queued(self.index) if agent else queue.remove(self.index)
            if taken is None:
                return {"CANCELLED"}
            text = taken.text if agent else str(taken.get("message", ""))
            mode = taken.chat_mode if agent else str(taken.get("chat_mode", "AGENT"))
            if self.action == "EDIT":
                props.chat_input = text
            elif self.action == "SEND":
                props.chat_mode = mode
                props.chat_input = text
                bpy.ops.bfacw.chat_send()
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_queue_clear(Operator):  # type: ignore[misc]
    """Clear all queued messages."""
    bl_idname = "bfacw.queue_clear"
    bl_label = "Clear Queue"
    bl_description = "Remove all queued messages from the message queue"

    def execute(self, context: bpy.types.Context) -> set[str]:
        if _is_external_agent(context) and _acp is not None:
            _acp.clear_queue()
        else:
            agent_controller.thread_queue(_ui_thread(context)).clear()
        self.report({"INFO"}, "Message queue cleared")
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_queue_show(Operator):  # type: ignore[misc]
    """Show queued messages in a popup."""
    bl_idname = "bfacw.queue_show"
    bl_label = "Show Queue"
    bl_description = "Display all queued messages in a popup menu"

    def execute(self, context: bpy.types.Context) -> set[str]:
        queue_items = _queued_messages(context)
        if not queue_items:
            self.report({"INFO"}, "Queue is empty")
            return {"CANCELLED"}

        def _draw_menu(menu, _context):
            layout = menu.layout
            layout.label(
                text="Queued Messages ({:d})".format(len(queue_items)),
                icon='FORWARD',
            )
            for idx, item in enumerate(queue_items):
                msg = item.get("message", "")
                mode = item.get("chat_mode", "AGENT")
                preview = msg[:60] + ("..." if len(msg) > 60 else "")
                row = layout.row()
                row.label(text="[{:d}] [{:s}] {:s}".format(idx + 1, mode, preview))

        context.window_manager.popup_menu(
            _draw_menu,
            title="Message Queue",
            icon='FORWARD',
        )
        return {"FINISHED"}


class BFACW_OT_export_session_log(Operator):  # type: ignore[misc]
    """Export the current session to a Blender text datablock."""
    bl_idname = "bfacw.export_session_log"
    bl_label = "Export Session Log"
    bl_description = "Export full session history, system prompt, and version info to a text block"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller.export_session_log()
        self.report({"INFO"}, "Session log exported to text block")
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_copy_session_log(Operator):  # type: ignore[misc]
    """Copy the session log to the clipboard."""
    bl_idname = "bfacw.copy_session_log"
    bl_label = "Copy Session Log"
    bl_description = "Copy full session history to clipboard"

    def execute(self, context: bpy.types.Context) -> set[str]:
        log_text = agent_controller.export_session_log_to_clipboard()
        context.window_manager.clipboard = log_text
        self.report({"INFO"}, "Session log copied to clipboard")
        return {"FINISHED"}


class BFACW_OT_copy_status_error(Operator):  # type: ignore[misc]
    """Copy the full error/status text to the clipboard for troubleshooting."""
    bl_idname = "bfacw.copy_status_error"
    bl_label = "Copy Error"
    bl_description = "Copy the full error text to the clipboard for troubleshooting"

    def execute(self, context: bpy.types.Context) -> set[str]:
        state = agent_controller._agent_state
        text = state.error_full or state.error or ""
        if not text.strip():
            self.report({"WARNING"}, "No error text to copy")
            return {"CANCELLED"}
        from . import log
        context.window_manager.clipboard = log.redact(text)    # keys and tokens as *** (#346)
        self.report({"INFO"}, "Error text copied to clipboard")
        return {"FINISHED"}


class BFACW_OT_chat_stop(Operator):  # type: ignore[misc]
    """Stop the current generation."""
    bl_idname = "bfacw.chat_stop"
    bl_label = "Stop"
    bl_description = "Stop the current generation"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import coworker_api, threads
        thread = _ui_thread(context)
        tag = threads.tag(thread)           # only this chat's thread stops (#357, #358)
        coworker_api.cancel_questions(tag)  # a question waiting for you is answered "cancelled"
        if thread.auto["run"] is not None:
            thread.auto["user_stop"] = True     # this thread's Auto stops after this turn (#177, #417)
        from . import risky_steps
        risky_steps.APPROVALS.cancel_all(tag)   # a risky step waiting for you is declined
        if _is_external_agent(context) and _acp is not None and _acp.thread != tag:
            _acp.drop_queued(tag)           # another thread's turn runs: only this one's waiting messages go
        else:
            _thread_engine(thread).cancel()
        agent_controller.thread_state(thread).status_text = "Stopped"
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        props.chat_status = "Stopped"
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_agent_approve(Operator):  # type: ignore[misc]
    """Answer the agent's question: may it run this tool?"""
    bl_idname = "bfacw.agent_approve"
    bl_label = "Answer the Agent"
    bl_options = {'INTERNAL'}

    choice: EnumProperty(  # type: ignore[valid-type]
        items=(("once", "Allow Once", "Let the agent run this tool this one time"),
               ("always", "Allow This Turn", "Let every tool run without asking for the rest of this turn; "
                                             "your next message asks again"),
               ("deny", "Deny", "Don't run it: the agent is told no and carries on without it")),
        options={'SKIP_SAVE'},
    )

    @classmethod
    def description(cls, _context, properties) -> str:
        return {"once": "Let the agent run this tool this one time",
                "always": "Let the agent run this tool without asking again, until you stop the agent",
                "deny": "Don't run it: the agent is told no and carries on without it"}.get(
                    properties.choice, cls.__doc__)

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import risky_steps, threads
        tag = threads.tag(_ui_thread(context))
        answered = (_acp is not None and _acp.approvals.current(tag) is not None
                    and _acp.approvals.answer(self.choice, thread=tag))
        if not answered and not risky_steps.APPROVALS.answer(self.choice, thread=tag):
            return {"CANCELLED"}
        _redraw_areas(context)
        return {"FINISHED"}


def approval_card(context=None):
    """What the chat asks you right now (GPU card / layout box): the agent's "may I?"
    first, else a question from ask_user (Engine 6); None when nothing is open."""
    from .ui_gpu.surface import Approval
    from . import threads
    tag = threads.tag(_ui_thread(context))     # this chat's thread's questions only (#357, #358)
    request = _acp.approvals.current(tag) if _acp is not None else None
    if request is not None:
        return Approval(title=request.title, detail=request.detail, waiting=_acp.approvals.count(tag),
                        note="One of Coworker's Blender tools" if request.coworker_tool
                        else "The agent's own tool, outside Blender")
    from . import risky_steps
    risky = risky_steps.APPROVALS.current(tag)
    if risky is not None:
        return Approval(title=risky.title, detail=risky.detail, waiting=risky_steps.APPROVALS.count(tag),
                        note=risky.note or "Permissions: the button by Send (Strict / Permissive / Auto)",
                        icon=safe_icon("WARNING") if risky.reason else "LOCKED")
    from . import coworker_api
    open_question = coworker_api.current_question(tag)
    if open_question is None:
        return _plan_card(context)
    _qid, q = open_question
    note = ("Pick one, or type your answer and press Enter" if q["choices"] and q["allow_text"] else
            "Pick one" if q["choices"] else "Type your answer and press Enter")
    return Approval(title=q["question"], note=note, action="answer", icon="INFO",
                    buttons=tuple(("c{:d}".format(i), choice, "") for i, choice in enumerate(q["choices"])))


# The Plan card (#241): after a Plan turn wrote a plan, carry it out or keep refining.
# Each thread answers it for its own plan (#417: ``thread.plan_dismissed``).

def _plan_key(thread=None) -> str:
    from . import session_memory as _sm
    with _sm.store_lock:
        goal = agent_controller._memory(thread or _ui_thread()).store.goal
        return "|".join([goal.session_goal, goal.turn_goal] + [str(s.get("text", "")) for s in goal.steps])


def _plan_card(context=None):
    """A plan just made in Plan mode: Start the Plan / Keep Refining (None otherwise)."""
    from .ui_gpu.surface import Approval
    from . import session_memory as _sm
    thread = _ui_thread(context)
    state = agent_controller.thread_state(thread)
    try:
        props = bpy.context.window_manager.bfacw_chat_props
    except AttributeError:
        return None
    history = state.conversation_history
    if (props.chat_mode != "PLAN" or state.is_thinking or not history
            or history[-1].get("role") != "assistant"):
        return None
    with _sm.store_lock:
        done, total = agent_controller._memory(thread).store.goal.progress()
    if not total or done >= total or _plan_key(thread) == thread.plan_dismissed:
        return None
    return Approval(title="The plan is ready: {:d} step{:s}".format(total, "" if total == 1 else "s"),
                    note="Start it now (Coworker mode, with this engine), or say what should change",
                    action="plan_card", icon="LINENUMBERS_ON",
                    buttons=(("start", "Start the Plan", "PLAY"), ("refine", "Keep Refining", "GREASEPENCIL")))


class BFACW_OT_plan_card(Operator):  # type: ignore[misc]
    """The Plan card's buttons: start the plan, or keep refining it"""
    bl_idname = "bfacw.plan_card"
    bl_label = "Plan"
    bl_options = {'INTERNAL'}

    choice: EnumProperty(  # type: ignore[valid-type]
        items=(("start", "Start the Plan", "Carry out the plan now, step by step (Coworker mode)"),
               ("refine", "Keep Refining", "Hide this card and type what should change in the plan")),
        options={'SKIP_SAVE'},
    )

    @classmethod
    def description(cls, _context, properties) -> str:
        return {"start": "Carry out the plan now, step by step (Coworker mode, with the engine that is on)",
                "refine": "Hide this card and type what should change; the plan stays in Plan mode"}.get(
                    properties.choice, cls.__doc__)

    def execute(self, context: bpy.types.Context) -> set[str]:
        thread = _ui_thread(context)
        thread.plan_dismissed = _plan_key(thread)
        _redraw_areas(context)
        if self.choice == "start":
            return bpy.ops.bfacw.plan_execute()
        return {"FINISHED"}


_TURN_SEEN: list = [False, None]    # was a turn running at the last timer tick, and whose (Engine 6b, #358)


def _turn_label(context: bpy.types.Context | None = None, thread=None) -> str:
    """The undo step's name: "Coworker's turn", whichever engine works; an editor's
    thread says whose, "Coworker (Node)'s turn" (#358)."""
    from . import threads
    return "{:s}'s turn".format(threads.label(thread))


class BFACW_OT_revert_turn(Operator):  # type: ignore[misc]
    """Undo everything the last turn changed in the scene, in one step.
Only while you haven't changed the scene since; otherwise use Edit > Undo History"""
    bl_idname = "bfacw.revert_turn"
    bl_label = "Revert Turn"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import coworker_api
        return coworker_api.can_revert() and not agent_controller._agent_state.is_thinking

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import coworker_api
        why = coworker_api.revert_turn()
        if why:
            self.report({"WARNING"}, why)
            return {"CANCELLED"}
        self.report({"INFO"}, "Reverted the last turn")
        _redraw_areas(context)
        return {"FINISHED"}


def _answer_typed(message: str, context=None) -> bool:
    """An open question that takes text gets your message as its answer."""
    from . import coworker_api, threads
    open_question = coworker_api.current_question(threads.tag(_ui_thread(context)))
    if open_question is None or not open_question[1]["allow_text"]:
        return False
    return coworker_api.answer_question(message, open_question[0])


class BFACW_OT_agent_answer(Operator):  # type: ignore[misc]
    """Answer the question in the chat with this choice"""
    bl_idname = "bfacw.agent_answer"
    bl_label = "Answer"
    bl_options = {'INTERNAL'}

    choice: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import coworker_api, threads
        open_question = coworker_api.current_question(threads.tag(_ui_thread(context)))
        if open_question is None:
            return {"CANCELLED"}
        qid, q = open_question
        try:
            text = q["choices"][int(self.choice.lstrip("c"))]
        except (ValueError, IndexError):
            return {"CANCELLED"}
        coworker_api.answer_question(text, qid)
        _redraw_areas(context)
        return {"FINISHED"}


def draw_approval_box(layout: bpy.types.UILayout) -> None:
    """The agent's open question in a layout panel (the GPU chat draws its own card)."""
    card = approval_card()
    if card is None:
        return
    box = layout.box()
    title = card.title + ("  (+{:d} more)".format(card.waiting - 1) if card.waiting > 1 else "")
    _draw_multiline(box, title, icon=card.icon)
    sub = box.column()
    sub.scale_y = 0.8
    sub.label(text=card.note)
    if card.detail:
        _draw_multiline(sub, card.detail)
    if not card.buttons:
        return
    col = box.column(align=True)
    col.scale_y = 1.3
    row = col.row(align=True) if len(card.buttons) <= 3 else col
    op_id = {"approve": "bfacw.agent_approve", "plan_card": "bfacw.plan_card"}.get(card.action, "bfacw.agent_answer")
    for button_id, label, icon in card.buttons:
        row.operator(op_id, text=label, icon=icon or 'NONE').choice = button_id


class BFACW_OT_chat_queue_send(Operator):  # type: ignore[misc]
    """Queue the current input message for later processing."""
    bl_idname = "bfacw.chat_queue_send"
    bl_label = "Queue Message"
    bl_description = "Add the current message to the queue for processing after the current turn"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        from . import threads
        thread = _ui_thread(context)        # this chat's thread: its draft, image and queue (#417)
        _focus_thread(thread)
        message = props.chat_input.strip()
        if not message:
            self.report({"WARNING"}, "Nothing to queue")
            return {"CANCELLED"}

        prefs = context.preferences.addons[__package__].preferences
        _sync_prefs_to_config(prefs)
        llm_cfg = llm_manager.get_config()
        llm_url = None
        api_key = None
        model = None
        if llm_cfg.mode == "remote":
            llm_url = llm_cfg.remote_api_url
            api_key = llm_cfg.remote_api_key
            model = llm_cfg.remote_model or None

        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        actual_mcp = agent_controller._agent_state.mcp_port_actual
        send_mcp_port = actual_mcp if actual_mcp else _mcp_port

        # Main-thread capture of the image socket (Tier 3k), same as the
        # direct send path -- the encoded payload travels with the queue item.
        attachments, attachment_names = _capture_chat_attachment(props)
        if attachments is None:
            _warn_failed_attachment(self, props)

        pos = _thread_engine(thread, llm_cfg.mode).enqueue(
            Prompt(text=message, images=list(attachments or []),
                   image_names=list(attachment_names or []), chat_mode=props.chat_mode,
                   thread=threads.tag(thread)),
            LoopConfig(llm_url=llm_url or None, api_key=api_key or None,
                       model=model, mcp_port=send_mcp_port),
        )
        props.chat_input = ""
        self.report({"INFO"}, "Queued (position {:d})".format(pos))
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_chat_capture_render(Operator):  # type: ignore[misc]
    """Render the current view and attach the result (main thread only)."""
    bl_idname = "bfacw.chat_capture_render"
    bl_label = "Render View"
    bl_description = (
        "Render the 3D Viewport's view (in any window) through a temporary camera -- your own "
        "camera is never moved -- or, with no 3D Viewport open, the scene's camera; and attach it"
    )

    # The chat it is for (#449): its tab, when called with no area in context.
    thread_id: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> set[str]:
        thread = _attach_thread(context, self.thread_id)
        # Render the current view.  A throwaway camera is aligned to the
        # viewport and removed again, so the scene is left untouched.
        path = chat_attachments.capture_render_from_view()      # a 3D Viewport in any window
        if not path:
            # No 3D Viewport anywhere: the scene's active camera (#267), else the last Render Result.
            path = chat_attachments.capture_render_from_camera() or chat_attachments.capture_render()
        if not path:
            self.report({"WARNING"}, "Nothing to render: open a 3D Viewport, or add a camera")
            return {"CANCELLED"}
        img = chat_attachments.load_image_file(path)
        if img is None:
            self.report({"ERROR"}, "Could not load the rendered image")
            return {"CANCELLED"}
        _attach_to_socket(context, self, img, thread)
        return {"FINISHED"}


# -- Capture part of the screen (#200) ------------------------------------------------
# Like the Asset Browser's "Capture Screenshot Preview": the screen dims except the
# part that will be taken.  Click an editor to take it, or drag a rectangle; Enter
# takes the last part taken in that window (outlined), Space moves it, Shift keeps it square.
# One capture runs in every window at once (#266): a modal only hears its own window, so each
# window gets its own instance; per window its part ("rects") and whether it is a hovered
# editor ("hover"); the first instance to take or cancel ends the session for all.
# #454: the highlight always follows the mouse (the part last taken, per window, is kept in
# "last", not in the operator's saved properties); each instance has a window timer, so it
# ends as soon as another one ends the session; "thread" is the chat it attaches to (found
# when it started); and a file load, a closed window, an error or the add-on going away
# ends it too (cancel, load_pre, unregister), leaving no veil, crosshair or status text.
_SHOT: dict = {"handlers": [], "rects": {}, "hover": {}, "session": 0, "active": False,
               "last": {}, "thread": "", "timers": [], "cursors": set()}
_SHOT_REGIONS = ("WINDOW", "HEADER", "UI", "TOOLS", "TOOL_HEADER", "FOOTER", "NAVIGATION_BAR",
                 "EXECUTE", "ASSET_SHELF", "ASSET_SHELF_HEADER", "CHANNELS", "PREVIEW")
_SHOT_DRAG = 6
_SHOT_TICK = 0.05       # each instance's window timer: how soon it notices the session ended


def _shot_rect(a, b, square: bool = False) -> tuple[int, int, int, int]:
    """(left, bottom, right, top) of the corners *a* and *b*; *square*: as wide as tall."""
    (ax, ay), (bx, by) = a, b
    if square:
        side = max(abs(bx - ax), abs(by - ay))
        bx = ax + side * (1 if bx >= ax else -1)
        by = ay + side * (1 if by >= ay else -1)
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


def _shot_draw() -> None:
    window, region = bpy.context.window, bpy.context.region
    key = window.as_pointer() if window is not None else None
    rect = _SHOT["rects"].get(key)
    if rect is None or region is None:
        return          # each window shows its own part (#217, #266), never a copy of another's
    import gpu
    from gpu_extras.batch import batch_for_shader
    ox, oy = region.x, region.y
    left, bottom, right, top = rect[0] - ox, rect[1] - oy, rect[2] - ox, rect[3] - oy
    w, h = region.width, region.height

    def quads(rects):
        tris = []
        for x0, y0, x1, y1 in rects:
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
            if x1 > x0 and y1 > y0:
                tris += [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]
        return tris

    def frame(x0, y0, x1, y1, t):
        return quads(((x0, y0, x1, y0 + t), (x0, y1 - t, x1, y1), (x0, y0, x0 + t, y1), (x1 - t, y0, x1, y1)))

    shade = quads(((0, 0, w, bottom), (0, top, w, h), (0, bottom, left, top), (right, bottom, w, top)))
    # The border inside the part's edge (#217): a whole editor's edge lies in the gap
    # between areas, where nothing draws.
    edge = frame(left, bottom, right, top, 2)
    # Hovering an editor (no part dragged yet): tint it, so it reads even when it fills the window.
    tint = quads(((left, bottom, right, top),)) if _SHOT["hover"].get(key) else []
    # The part Enter takes again (#454): a thin outline, while the highlight follows the mouse.
    last = _SHOT["last"].get(key)
    ghost = (frame(last[0] - ox, last[1] - oy, last[2] - ox, last[3] - oy, 1)
             if last is not None and tuple(last) != tuple(rect) else [])
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    # As the Asset Browser's capture: a light veil outside, the border in the theme's colour.
    border = tuple(bpy.context.preferences.themes[0].user_interface.editor_border) + (1.0,)
    for tris, color in ((shade, (1.0, 1.0, 1.0, 0.25)), (tint, (1.0, 1.0, 1.0, 0.08)),
                        (ghost, border[:3] + (0.6,)), (edge, border[:4])):
        if tris:
            batch = batch_for_shader(shader, "TRIS", {"pos": tris})
            shader.bind()
            shader.uniform_float("color", color)
            batch.draw(shader)
    gpu.state.blend_set("NONE")


def _shot_handlers_add() -> None:
    for name in dir(bpy.types):
        space = getattr(bpy.types, name, None)
        if not (name.startswith("Space") and hasattr(space, "draw_handler_add")):
            continue
        for region_type in _SHOT_REGIONS:
            try:
                handle = space.draw_handler_add(_shot_draw, (), region_type, "POST_PIXEL")
            except (TypeError, ValueError, RuntimeError):
                continue
            _SHOT["handlers"].append((space, handle, region_type))


def _shot_handlers_remove() -> None:
    for space, handle, region_type in _SHOT["handlers"]:
        try:
            space.draw_handler_remove(handle, region_type)
        except (TypeError, ValueError, RuntimeError):
            pass
    _SHOT["handlers"].clear()
    _SHOT["rects"].clear()
    _SHOT["hover"].clear()
    _SHOT["active"] = False


def _shot_redraw(window) -> None:
    try:
        for area in window.screen.areas:
            area.tag_redraw()
    except (AttributeError, ReferenceError):
        pass


def _shot_timer_remove(wm, timer) -> None:
    """Stop one instance's window timer (once: it may already be gone)."""
    for i, known in enumerate(_SHOT["timers"]):
        if known is timer:
            del _SHOT["timers"][i]
            try:
                wm.event_timer_remove(timer)
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
                pass
            return


def _shot_clear_windows(wm) -> None:
    """No crosshair and no capture help in the status bar, in any window (#454)."""
    for window in list(getattr(wm, "windows", ())):
        try:
            if window.as_pointer() in _SHOT["cursors"]:
                window.cursor_modal_restore()
            with bpy.context.temp_override(window=window):
                window.workspace.status_text_set(None)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
        _shot_redraw(window)
    _SHOT["cursors"].clear()


def _shot_end_all(wm=None) -> None:
    """End any capture at once (file load, add-on off): veil, flag, timers, cursors, status
    text.  A running instance sees a new session and stops on its next event."""
    wm = wm if wm is not None else getattr(bpy.context, "window_manager", None)
    _shot_handlers_remove()
    for timer in list(_SHOT["timers"]):
        _shot_timer_remove(wm, timer)
    _SHOT["timers"].clear()
    _SHOT["session"] += 1
    _SHOT["thread"] = ""
    if wm is not None:
        _shot_clear_windows(wm)


@bpy.app.handlers.persistent
def _shot_on_load_pre(*_args) -> None:
    # Opening a file ends every modal operator, maybe without its cancel(): end the capture here.
    _shot_end_all()


def _shot_status(header, _context) -> None:
    row = header.layout
    for text, icon in (("Take Editor", "MOUSE_LMB"), ("Drag a Part", "MOUSE_LMB_DRAG"),
                       ("Take Last Part", "EVENT_RETURN"), ("Move", "EVENT_SPACEKEY"),
                       ("Square", "EVENT_SHIFT"), ("Cancel", "EVENT_ESC")):
        row.label(text=text, icon=icon)


def _area_rect_at(window, x: int, y: int) -> tuple[int, int, int, int] | None:
    for area in window.screen.areas:
        if area.x <= x < area.x + area.width and area.y <= y < area.y + area.height:
            return area.x, area.y, area.x + area.width, area.y + area.height
    return None


class BFACW_OT_chat_capture_screen(Operator):  # type: ignore[misc]
    """Attach a screenshot to the chat socket (main thread only)."""
    bl_idname = "bfacw.chat_capture_screen"
    bl_label = "Capture Screen"
    bl_description = ("Attach part of the screen: click an editor, or drag a rectangle "
                      "(Enter: the last part again, Space: move, Shift: square, Esc: cancel)")

    # The part to take, in window pixels (a script, or the timer after a pick).  Not saved: a
    # saved part froze the next run's highlight on it (#454); the last part is in _SHOT["last"].
    p1: bpy.props.IntVectorProperty(size=2, options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    p2: bpy.props.IntVectorProperty(size=2, options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    use_rect: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    # The chat it attaches to (#454): its tab, found when the capture started.
    thread_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    sibling: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    _session, _win, _key, _timer = 0, None, 0, None

    # -- this instance's window: its part, and whether that is a hovered editor --
    def _rect(self):
        return _SHOT["rects"].get(self._key)

    def _set(self, rect, hover: bool) -> None:
        _SHOT["rects"][self._key] = rect
        _SHOT["hover"][self._key] = hover

    def invoke(self, context: bpy.types.Context, event) -> set[str]:
        if bpy.app.background or context.window is None:
            return self.execute(context)
        win = context.window
        if not self.sibling:                 # the one you started: a new session, in every window
            _shot_handlers_remove()
            _SHOT["session"] += 1
            _SHOT["active"] = True
            _SHOT["thread"] = _attach_thread(context, self.thread_id).id    # this chat's tab (#454)
            _shot_handlers_add()
        self._session, self._win, self._key, self._timer = _SHOT["session"], win, win.as_pointer(), None
        try:
            self._begin(context, event)
        except Exception:
            # Nothing may stay behind (#454): no veil, flag, crosshair, timer or status text.
            if self.sibling:
                self._finish(context)
            else:
                self._end(context)
            raise
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _begin(self, context, event) -> None:
        win, wm = self._win, context.window_manager
        given = _shot_rect(tuple(self.p1), tuple(self.p2))
        if not self.sibling and given[2] - given[0] >= 8 and given[3] - given[1] >= 8:
            _SHOT["last"][self._key] = given            # a script handed a part: Enter takes it
        self._start, self._dragging, self._moving, self._picked = None, False, False, False
        self._square, self._cursor = False, (event.mouse_x, event.mouse_y)
        if not self.sibling:        # the editor under the mouse; the other windows' when it gets there
            self._set(_area_rect_at(win, *self._cursor), True)
        self._timer = wm.event_timer_add(_SHOT_TICK, window=win)
        _SHOT["timers"].append(self._timer)
        win.cursor_modal_set("CROSSHAIR")
        _SHOT["cursors"].add(self._key)
        if not self.sibling:
            try:
                context.workspace.status_text_set(_shot_status)
            except (TypeError, AttributeError):
                context.workspace.status_text_set("Click an editor or drag a part to attach (any window)  -  "
                                                  "Enter: the last part  -  Space: move  -  Shift: square  -  "
                                                  "Esc: cancel")
            for other in wm.windows:       # the other windows join in (#266)
                if other != win:
                    try:
                        # The window alone: it brings its own screen, and overriding the screen is refused
                        # while any window is maximized (Ctrl+Space, #401).
                        with context.temp_override(window=other):
                            bpy.ops.bfacw.chat_capture_screen("INVOKE_DEFAULT", sibling=True)
                    except RuntimeError:
                        pass
        for window in wm.windows:
            _shot_redraw(window)

    def _finish(self, context) -> None:
        """This instance ends: its timer and its cursor back."""
        if self._timer is not None:
            _shot_timer_remove(context.window_manager, self._timer)
            self._timer = None
        _SHOT["cursors"].discard(self._key)
        try:
            self._win.cursor_modal_restore()
        except (AttributeError, ReferenceError, RuntimeError):
            pass

    def _end(self, context) -> None:
        """This instance ends the session: every window's veil, crosshair and status text."""
        _shot_handlers_remove()
        self._finish(context)
        _shot_clear_windows(context.window_manager)

    def cancel(self, context) -> None:
        # Blender ends it from outside: a file load, its window closed (#454).
        if self._session == _SHOT["session"] and _SHOT["active"]:
            self._end(context)
        else:
            self._finish(context)

    def _take(self, context, rect) -> set[str]:
        thread_id = _SHOT["thread"]
        self._end(context)
        if rect is None:
            return {'CANCELLED'}
        _SHOT["last"][self._key] = tuple(rect)        # Enter takes it again next time, in this window
        win, a, b = self._win, tuple(rect[:2]), tuple(rect[2:])

        def later():      # after the redraw without the dimming
            try:
                with bpy.context.temp_override(window=win):     # the window alone, see invoke (#401)
                    bpy.ops.bfacw.chat_capture_screen('EXEC_DEFAULT', p1=a, p2=b, use_rect=True,
                                                      thread_id=thread_id)
            except (RuntimeError, ReferenceError) as ex:
                print("[Coworker] capture: {!s}".format(ex))
            return None
        bpy.app.timers.register(later, first_interval=0.1)
        return {'FINISHED'}

    def modal(self, context: bpy.types.Context, event) -> set[str]:
        try:
            return self._modal(context, event)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            # Never leave the veil up (#454): end the capture, then say why.
            self._end(context)
            print("[Coworker] capture ended: {!s}".format(ex))
            return {'CANCELLED'}

    def _modal(self, context, event) -> set[str]:
        if self._session != _SHOT["session"] or not _SHOT["active"]:
            self._finish(context)           # another window took it, or it was cancelled there
            return {'CANCELLED', 'PASS_THROUGH'}
        if event.type == 'TIMER':
            return {'PASS_THROUGH'}         # its own tick, or someone else's
        x, y = event.mouse_x, event.mouse_y
        if event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            self._square = event.value == 'PRESS'
        if event.type == 'SPACE':
            self._moving = event.value == 'PRESS'
        elif event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        elif event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            # The part dragged or moved now; else the last one taken here; else the editor shown.
            last = _SHOT["last"].get(self._key)
            return self._take(context, self._rect() if self._picked or last is None else last)
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._start, self._dragging = (x, y), False
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self._start is not None:
            return self._take(context, self._rect() if self._dragging else _area_rect_at(self._win, x, y))
        elif event.type == 'MOUSEMOVE':
            dx, dy = x - self._cursor[0], y - self._cursor[1]
            rect = self._rect()
            if self._moving and rect is not None:
                self._set((rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy), False)
                self._picked = True
            elif self._start is not None:
                sx, sy = self._start
                if not self._dragging and abs(x - sx) > _SHOT_DRAG and abs(y - sy) > _SHOT_DRAG:
                    self._dragging = True
                if self._dragging:
                    self._set(_shot_rect(self._start, (x, y), self._square), False)
                    self._picked = True
            elif not self._picked:          # the highlight follows the mouse (#454)
                self._set(_area_rect_at(self._win, x, y), True)
            _shot_redraw(self._win)
        self._cursor = (x, y)
        return {'RUNNING_MODAL'}

    def execute(self, context: bpy.types.Context) -> set[str]:
        thread = _attach_thread(context, self.thread_id)
        path = None
        if self.use_rect:
            path = chat_attachments.capture_screen_rect(context.window, *self.p1, *self.p2)
        if not path:
            # From a script: prefer the 3D Viewport (what the model needs to see);
            # fall back to the whole window when no viewport exists.
            path = chat_attachments.capture_screen(target="VIEW_3D")
        if not path:
            path = chat_attachments.capture_screen(target="WINDOW")
        if not path:
            self.report({"WARNING"}, "Screenshot unavailable (background mode?)")
            return {"CANCELLED"}
        img = chat_attachments.load_image_file(path)
        if img is None:
            self.report({"ERROR"}, "Could not load the screenshot")
            return {"CANCELLED"}
        _attach_to_socket(context, self, img, thread)
        return {"FINISHED"}


class BFACW_OT_chat_image_drop(Operator):  # type: ignore[misc]
    """Receive an image file dragged onto the Coworker chat panel."""
    bl_idname = "bfacw.chat_image_drop"
    bl_label = "Attach Image to Coworker Chat"
    bl_description = "Attach the dropped image file to the chat"

    # Blender's file-handler drop writes these onto the import operator:
    # ``filepath`` is the first supported path, while ``directory`` +
    # ``files`` describe the whole drop (multi-file drops included).
    filepath: bpy.props.StringProperty(  # type: ignore[valid-type]
        subtype='FILE_PATH', options={'SKIP_SAVE'})
    directory: bpy.props.StringProperty(  # type: ignore[valid-type]
        subtype='DIR_PATH', options={'SKIP_SAVE'})
    files: bpy.props.CollectionProperty(  # type: ignore[valid-type]
        type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE'})

    def execute(self, context: bpy.types.Context) -> set[str]:
        img = chat_attachments.load_image_file(self.filepath)
        if img is None:
            self.report({"WARNING"}, "Blender can't read this file as an image")
            return {"CANCELLED"}
        _attach_to_socket(context, self, img, _attach_thread(context))     # the chat it was dropped on
        return {"FINISHED"}


def _image_data_icon_value() -> int:
    """``FileHandler.bl_icon`` for the image icon (a Bforartists addition).

    Bforartists gives FileHandler a plain INT ``bl_icon`` ("Icon to
    display for the file handler", rna_ui.cc) and passes it to the entry
    it adds to its "multiple file handlers" drop menu, so the icon has to
    be looked up as the enum's integer value.  Returns 0 ("no icon") when
    the build has no such property or the enum cannot be read: stock
    Blender simply ignores the attribute.
    """
    try:
        items = bpy.types.UILayout.bl_rna.functions["label"].parameters[
            "icon"].enum_items
        return int(items["IMAGE_DATA"].value)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return 0


_IMAGE_DATA_ICON = _image_data_icon_value()


class BFACW_FH_chat_drop(bpy.types.FileHandler):  # type: ignore[misc]
    """Drag-and-drop image files onto the Coworker chat panel.

    ``poll_drop`` gets no mouse position, so the drop is scoped by region
    + panel category: the Text Editor chat panel, or the 3D Viewport
    sidebar while the "Coworker" tab is active.

    In the 3D Viewport Blender's own image drop handlers match the same
    extensions, so several handlers match and Blender lists them -- our
    entry is "Attach Image to Coworker Chat".  In the Text Editor ours is
    the only match, so the drop attaches directly.  We deliberately do not
    patch Blender's handlers (see the note above
    ``BFACW_OT_copy_message``).
    """
    bl_idname = "BFACW_FH_chat_drop"
    bl_label = "Attach Image to Coworker Chat"
    bl_import_operator = "bfacw.chat_image_drop"
    # Semicolon-separated per bpy.types.FileHandler.bl_file_extensions;
    # mirrors chat_attachments.SUPPORTED_EXTENSIONS.
    bl_file_extensions = ".png;.jpg;.jpeg;.webp;.bmp;.tif;.tiff;.exr;.hdr;.tga;.jp2;.j2c;.dpx;.cin;.sgi;.rgb;.psd"
    # Bforartists shows this icon on our entry in its "multiple file
    # handlers" drop menu (see _image_data_icon_value).
    bl_icon = _IMAGE_DATA_ICON

    @classmethod
    def poll_drop(cls, context) -> bool:
        # Blender calls this mid-drag from C, so it must never raise or
        # return a non-bool: anything unexpected means "not ours".
        try:
            area = context.area
            if area is None:
                return False
            if area.type == "TEXT_EDITOR":
                return True
            from . import chat_editor as _ce
            if area.type == "NODE_EDITOR" and _ce.is_coworker_space(area.spaces.active):
                _ce.note_drop_hover()          # the GPU chat highlights the drop target
                return True
            if _is_coworker_panel_region(context):
                _ce.note_drop_hover()
                return True
            return False
        except (AttributeError, ReferenceError, TypeError, ValueError):
            return False


def _is_coworker_panel_region(context) -> bool:
    """True for the 3D Viewport sidebar while the Coworker tab is active."""
    try:
        area = context.area
        region = context.region
    except AttributeError:
        return False
    return (area is not None
            and area.type in SIDEBAR_TYPES
            and region is not None
            and region.type == "UI"
            and getattr(region, "active_panel_category", "") == _CHAT_PANEL_CATEGORY)


# NOTE: we deliberately do NOT monkey-patch Blender's own image FileHandlers
# (VIEW3D_FH_empty_image / VIEW3D_FH_camera_background_image) to make them
# yield inside our panel.  Doing so replaced ``poll_drop`` on classes that
# Blender registers from a startup module, and a wrapper left behind by an
# add-on reload segfaulted Blender inside ``bpy_class_call`` on the next drag
# (EXCEPTION_ACCESS_VIOLATION via file_handler_poll_drop).  The trade-off is
# that a drop in the 3D Viewport matches several handlers, so Blender shows
# its usual "multiple file handlers" menu -- with our "Attach Image to
# Coworker Chat" entry in it.  That is stock, safe behaviour: the Text Editor
# chat panel still attaches directly, and the socket, the two capture buttons
# and the menu entry cover the rest.


class BFACW_OT_copy_message(Operator):  # type: ignore[misc]
    """Copy a message from the conversation history to the clipboard."""
    bl_idname = "bfacw.copy_message"
    bl_label = "Copy Message"
    bl_description = "Copy this message\'s content to the clipboard"

    message_index: bpy.props.IntProperty(  # type: ignore[valid-type]
        name="Message Index",
        description="Index into conversation_history",
        default=-1,
    )

    archived: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Archived",
        description="Read the message from the retired-history archive",
        default=False,
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        history = (
            _ui_store(context).retired_history if self.archived
            else _ui_state(context).conversation_history
        )
        if self.message_index < 0 or self.message_index >= len(history):
            self.report({"ERROR"}, "Message not found (stale index)")
            return {"CANCELLED"}
        msg = history[self.message_index]
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        # Build clipboard text with context.
        parts = []
        if role == "tool":
            name = msg.get("name", "")
            summary = msg.get("summary", "")
            if name:
                parts.append("[Tool: {:s}]".format(name))
            if summary and summary != content:
                parts.append(summary)
            if content:
                parts.append("--- Full output ---")
                parts.append(content)
        elif role == "reasoning":
            label = msg.get("label", "Thinking")
            parts.append("[{:s}]".format(label))
            parts.append(content)
        else:
            if content:
                parts.append(content)

        from . import log
        context.window_manager.clipboard = log.redact("\n\n".join(parts))     # (#346)
        self.report({"INFO"}, "Message copied to clipboard")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# @Mention System (Tier 2+)

# Mention categories with their data sources and icons.
_MENTION_CATEGORIES = {
    "object": {
        "label": "Objects",
        "icon": 'OUTLINER_OB_MESH',
        "data": lambda: [
            {"name": obj.name, "type": obj.type, "category": "object"}
            for obj in bpy.data.objects
        ],
    },
    "material": {
        "label": "Materials",
        "icon": 'MATERIAL',
        "data": lambda: [
            {"name": mat.name, "type": "MAT", "category": "material"}
            for mat in bpy.data.materials
        ],
    },
    "collection": {
        "label": "Collections",
        "icon": 'OUTLINER_COLLECTION',
        "data": lambda: [
            {"name": col.name, "type": "COL", "category": "collection"}
            for col in bpy.data.collections
        ],
    },
    "nodegroup": {
        "label": "Node Groups",
        "icon": 'NODETREE',
        "data": lambda: [
            {"name": ng.name, "type": ng.type or "NODE", "category": "nodegroup"}
            for ng in bpy.data.node_groups
        ],
    },
    "world": {
        "label": "Worlds",
        "icon": 'WORLD',
        "data": lambda: [
            {"name": w.name, "type": "WORLD", "category": "world"}
            for w in bpy.data.worlds
        ],
    },
    "action": {
        "label": "Actions",
        "icon": 'ACTION',
        "data": lambda: [
            {"name": a.name, "type": "ACT", "category": "action"}
            for a in bpy.data.actions
        ],
    },
}


def _collect_all_mentionables() -> list[dict]:
    """Collect all mentionable items from all categories."""
    items = []
    for cat_key, cat_info in _MENTION_CATEGORIES.items():
        try:
            items.extend(cat_info["data"]())
        except Exception:
            pass
    return items


def _filter_mentionables(
    items: list[dict],
    filter_text: str = "",
    category: str = "",
) -> list[dict]:
    """Filter mentionable items by text and category."""
    filtered = items
    if category and category in _MENTION_CATEGORIES:
        filtered = [i for i in filtered if i.get("category") == category]
    if filter_text:
        filter_lower = filter_text.lower()
        filtered = [i for i in filtered if filter_lower in i["name"].lower()]
    return filtered


class BFACW_OT_mention_search(Operator):  # type: ignore[misc]
    """Search for scene items by name and insert @mention into chat."""
    bl_idname = "bfacw.mention_search"
    bl_label = "@ Mention"
    bl_description = "Search objects, materials, collections, and more to insert @mention"

    filter_text: StringProperty(  # type: ignore[valid-type]
        name="Filter",
        default="",
    )
    category: StringProperty(  # type: ignore[valid-type]
        name="Category",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        # Auto-detect filter from input: if user typed @word, use word as filter.
        current_input = props.chat_input or ""
        if not self.filter_text and "@" in current_input:
            # Find the last @ and extract text after it.
            last_at = current_input.rfind("@")
            after_at = current_input[last_at + 1:]
            # If there's text after @ without a space, use it as filter.
            if after_at and not after_at.startswith(" "):
                self.filter_text = after_at.split()[-1] if after_at.split() else ""

        # Collect and filter items.
        all_items = _collect_all_mentionables()
        filtered = _filter_mentionables(all_items, self.filter_text, self.category)

        if not filtered:
            self.report({"INFO"}, "No matches found.")
            return {"CANCELLED"}

        def _draw_menu(menu, _context):
            layout = menu.layout

            # Category filter buttons.
            row = layout.row(align=True)
            row.label(text="", icon='VIEWZOOM')
            op = row.operator("bfacw.mention_search", text="All", icon='NONE')
            op.category = ""
            op.filter_text = self.filter_text
            for cat_key, cat_info in _MENTION_CATEGORIES.items():
                op = row.operator(
                    "bfacw.mention_search",
                    text="",
                    icon=cat_info["icon"],
                )
                op.category = cat_key
                op.filter_text = self.filter_text

            layout.separator()

            # Filtered results.
            display_items = filtered[:50]  # Limit to 50.
            if self.filter_text:
                layout.label(
                    text="{:d} matches for '{:s}'".format(len(display_items), self.filter_text),
                    icon='SORTBYEXT',
                )
            else:
                layout.label(
                    text="{:d} items".format(len(display_items)),
                    icon='INFO',
                )

            for item in display_items:
                cat = item.get("category", "object")
                cat_info = _MENTION_CATEGORIES.get(cat, _MENTION_CATEGORIES["object"])
                op = layout.operator(
                    "bfacw.mention_insert",
                    text="[{:s}] {:s}".format(item["type"], item["name"]),
                    icon=cat_info["icon"],
                )
                op.object_name = item["name"]
                op.category = cat

        wm.popup_menu(_draw_menu, title="@ Mention", icon='OUTLINER_OB_MESH')
        return {"FINISHED"}


class BFACW_OT_mention_insert(Operator):  # type: ignore[misc]
    """Insert an @mentioned item name into the chat input."""
    bl_idname = "bfacw.mention_insert"
    bl_label = "Insert @mention"
    bl_description = "Insert the selected item name as an @mention in the chat input"

    object_name: StringProperty(  # type: ignore[valid-type]
        name="Item Name",
        default="",
    )
    category: StringProperty(  # type: ignore[valid-type]
        name="Category",
        default="object",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        current = props.chat_input or ""

        # Remove any partial @mention that was being typed.
        # Find the last @ and remove everything after it.
        if "@" in current:
            last_at = current.rfind("@")
            before_at = current[:last_at]
            after_at = current[last_at + 1:]
            # If there's text after @ without a space, it's a partial mention.
            if after_at and not after_at.startswith(" "):
                current = before_at

        # Insert the mention.
        mention = "@{:s}".format(self.object_name)
        if current and not current.endswith(" "):
            mention = " " + mention
        props.chat_input = current + mention + " "
        _redraw_areas(context)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Project Rules (Tier 2)

def _rules_dir() -> Path:
    """Return the directory where project rules are stored."""
    base = Path(bpy.utils.user_resource("SCRIPTS")) / "bfa_coworker_rules"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _global_rules_path() -> Path:
    """Return the path to the global rules file."""
    return _rules_dir() / "global.md"


def _blend_rules_path() -> Path:
    """Return the path to the blend-file-specific rules file."""
    if bpy.data.filepath:
        stem = Path(bpy.data.filepath).stem
        return _rules_dir() / "{:s}.md".format(stem)
    return _rules_dir() / "default.md"


def _load_rules() -> str:
    """Load project rules, merging global and blend-specific files."""
    parts = []
    global_path = _global_rules_path()
    if global_path.exists():
        try:
            parts.append(global_path.read_text(encoding="utf-8"))
        except OSError:
            pass
    blend_path = _blend_rules_path()
    if blend_path.exists():
        try:
            parts.append(blend_path.read_text(encoding="utf-8"))
        except OSError:
            pass
    return "\n\n".join(parts)


class BFACW_OT_edit_rules(Operator):  # type: ignore[misc]
    """Open the project rules file in the Blender Text Editor."""
    bl_idname = "bfacw.edit_rules"
    bl_label = "Edit Rules"
    bl_description = "Open the project rules file for editing"

    def execute(self, context: bpy.types.Context) -> set[str]:
        rules_path = _blend_rules_path()

        # Create default rules file if it doesn't exist.
        if not rules_path.exists():
            try:
                rules_path.write_text(
                    "# Project Rules for {:s}\n"
                    "# Write instructions for the agent below.\n"
                    "# Each line starting with # is a comment.\n"
                    "\n"
                    "- Be concise and specific.\n"
                    "- Use Blender 5.2+ API conventions.\n".format(
                        Path(bpy.data.filepath).stem if bpy.data.filepath else "this scene"
                    ),
                    encoding="utf-8",
                )
            except OSError as ex:
                self.report({"ERROR"}, "Failed to create rules file: {:s}".format(str(ex)))
                return {"CANCELLED"}

        # Open in Text Editor.
        try:
            text = bpy.data.texts.load(str(rules_path), internal=False)
        except (OSError, RuntimeError) as ex:
            self.report({"ERROR"}, "Failed to open rules file: {:s}".format(str(ex)))
            return {"CANCELLED"}

        # Switch to Text Editor workspace.
        for area in context.screen.areas:
            if area.type == 'TEXT_EDITOR':
                area.spaces[0].text = text
                area.tag_redraw()
                break

        self.report({"INFO"}, "Opened rules file: {:s}".format(str(rules_path)))
        return {"FINISHED"}


class BFACW_OT_reload_rules(Operator):  # type: ignore[misc]
    """Reload project rules into the agent's system prompt."""
    bl_idname = "bfacw.reload_rules"
    bl_label = "Reload Rules"
    bl_description = "Reload project rules into the Coworker's system prompt"

    def execute(self, context: bpy.types.Context) -> set[str]:
        # Clear cached system prompt so it's rebuilt on next turn.
        agent_controller._clear_system_prompt_cache()
        self.report({"INFO"}, "Project rules reloaded")
        return {"FINISHED"}


class BFACW_OT_agent_start(Operator):  # type: ignore[misc]
    """Start the Coworker agent: MCP bridge, MCP server, and LLM backend."""
    bl_idname = "bfacw.agent_start"
    bl_label = "Start Coworker"
    bl_description = "Start the Coworker agent: MCP bridge, MCP server, and LLM backend"

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences

        # In External Harness mode, only start the bridge server.
        if prefs.operating_mode == "EXTERNAL_HARNESS":
            return self._start_bridge_only(context)
        if prefs.operating_mode == "EXTERNAL_AGENT":
            return self._start_external_agent(context)

        return self._start_full_agent(context)

    def _start_external_agent(self, context: bpy.types.Context) -> set[str]:
        """External Agent mode: the bridge, then the agent app with our MCP server."""
        import threading as _threading
        from . import acp_presets
        prefs = context.preferences.addons[__package__].preferences
        engine = _acp_engine()
        if engine.connected or engine.connecting:
            return {"FINISHED"}
        if not mcp_to_blender_server.is_running():
            if self._start_bridge_only(context) != {"FINISHED"}:
                return {"CANCELLED"}
        try:
            preset = (acp_presets.generic_preset(prefs.external_agent_command)
                      if prefs.external_agent_preset == "custom"
                      else acp_presets.preset_by_id(prefs.external_agent_preset))
        except ValueError:
            preset = None
        if preset is None:
            self.report({"WARNING"}, "Pick an agent, or type its command (Custom Command)")
            return {"CANCELLED"}
        # This .blend's chat comes back, and with it the agent's session for it.
        if not _has_user_messages():
            _restore_chat_history()
        sessions_file, chat_key = _agent_sessions_file(), _chat_history_path().name
        resume = _saved_agent_session(sessions_file, chat_key, preset.id) if _has_user_messages() else ""
        # Filed under the chat that is open when the session starts (Chat 7: it can change).
        _acp_engine().on_session = lambda session_id: _remember_agent_session(
            sessions_file, (_CHAT["id"] or "default") + ".json", preset.id, session_id)
        # A tab's chat continues its own conversation too (#431), filed under that chat.
        _acp_engine().preset_id = preset.id
        _acp_engine().resume_for = lambda tag: _thread_agent_session(sessions_file, tag, preset.id)
        _acp_engine().on_thread_session = lambda session_id, tag: _remember_thread_session(
            sessions_file, tag, preset.id, session_id)
        bridge_port, _, _ = effective_ports(prefs)
        engine.bridge_port = bridge_port        # where its tools look for Blender (_ensure_bridge)
        host = prefs.host
        use_blender_python = getattr(prefs, "use_blender_python_for_harness", True)
        tools_on_demand = getattr(prefs, "external_agent_tools_on_demand", True)
        only_ours = getattr(prefs, "external_agent_only_coworker_tools", True)
        env = acp_presets.launch_env(preset, only_coworker_tools=only_ours)
        engine.only_coworker_tools = only_ours
        _agent_follows_approval(engine, getattr(prefs, "approval_mode", "ASK"))
        engine.approvals.on_change = engine.on_change     # a question redraws the chats
        cwd = str(_agent_workspace())
        engine.connecting = True
        engine.status = "Looking for {:s}...".format(preset.name)
        engine.on_change and engine.on_change()

        # Your earlier picks for this agent (model, mode, effort) come back in each session.
        options_file = _agent_options_file()
        engine.preferred = dict(_read_json(options_file).get(preset.id) or {})
        engine.on_option = lambda config_id, value: _remember_agent_option(options_file, preset.id, config_id, value)

        def work() -> None:
            engine.login_hint = preset.login_hint
            # #424: the agent app starts our MCP server, so its vendor deps come first -- the
            # same check (and first-start install) as Local's start, here off the main thread.
            try:
                entry, problem = agent_controller.external_agent_server_entry(
                    blender_host=host, blender_port=bridge_port, use_blender_python=use_blender_python)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                entry, problem = None, "Coworker's MCP server isn't ready -- {:s}".format(str(ex))
            if entry is None:
                engine.connecting = False
                engine.status = problem
                engine.on_change and engine.on_change()
                return
            if tools_on_demand:
                # Engine 7: the server lists the basic tools; load_tools adds the rest.
                entry["env"] = dict(entry.get("env") or {}, BFACW_TOOLS_ON_DEMAND="1")
            found = acp_presets.detect(preset)
            engine.connecting = False
            if not found.ready:
                engine.status = "{:s} {:s}".format(found.problem, found.hint).strip()
                engine.on_change and engine.on_change()
                return
            engine.connect(found.argv, entry, cwd, env, label=preset.name, wait=True, resume=resume)
            if engine.connected and (engine.resumed or engine.resume_failed):
                engine.status += (": continuing your last conversation" if engine.resumed
                                  else ": couldn't continue the last conversation, so it starts fresh")
                engine.on_change and engine.on_change()
        _threading.Thread(target=work, daemon=True, name="acp-start").start()
        context.window_manager.bfacw_chat_props.chat_status = "Starting {:s}...".format(preset.name)
        _redraw_areas(context)
        return {"FINISHED"}

    def _start_bridge_only(self, context: bpy.types.Context) -> set[str]:
        """Start only the bridge server (External Harness mode)."""
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        if context.preferences.addons[__package__].preferences.operating_mode == "EXTERNAL_HARNESS":
            # The client starts our MCP server itself: have it loadable by then (#458).
            agent_controller.prepare_harness_server_async()

        if mcp_to_blender_server.is_running():
            self.report({"INFO"}, "Bridge server already running")
            actual = mcp_to_blender_server.get_actual_port()
            if actual:
                props.chat_status = "External Harness -- Bridge on port {:d}".format(actual)
            else:
                props.chat_status = "External Harness -- Bridge running"
            return {"FINISHED"}

        if bpy.app.background:
            self.report({"ERROR"}, "Cannot start in background mode")
            return {"CANCELLED"}

        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _, _ = effective_ports(prefs)
        try:
            mcp_to_blender_server.start(prefs.host, _bridge_port)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            self.report({"ERROR"}, "Bridge server failed: {:s}".format(str(ex)))
            return {"CANCELLED"}

        from . import execute_interactive
        bpy.app.timers.register(
            execute_interactive.run,
            first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
            persistent=True,
        )

        actual = mcp_to_blender_server.get_actual_port()
        if actual:
            props.chat_status = "External Harness -- Bridge on port {:d}".format(actual)
            self.report({"INFO"}, "Bridge server started on port {:d}".format(actual))
        else:
            props.chat_status = "External Harness -- Bridge running"
            self.report({"INFO"}, "Bridge server started")
        _redraw_areas(context)
        return {"FINISHED"}

    def _start_full_agent(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        # Local mode: never let llama-server trigger an implicit multi-GB
        # download.  If the configured model is not on disk (and not cached),
        # stall the launch and ask the user to confirm the download first.
        _prefs = context.preferences.addons[__package__].preferences
        if _prefs.operating_mode == "LOCAL_LLM" and not llm_manager.get_state().is_running:
            _sync_prefs_to_config(_prefs)
            _existing = _prefs.existing_model_path
            _ready, _info = llm_manager.check_local_model_ready(
                _existing if _existing and os.path.isfile(_existing) else None)
            if not _ready:
                llm_manager.set_pending_model_download(_info)
                props.chat_status = "Waiting for download confirmation..."
                _redraw_areas(context)
                _result = bpy.ops.bfacw.confirm_model_download('INVOKE_DEFAULT')
                return {"CANCELLED"} if _result == {"CANCELLED"} else {"FINISHED"}

        # Warm the main-thread bpy-derived cache (system prompt, prefs) now, on
        # the main thread, so the first turn worker never reads bpy itself.
        agent_controller.prefetch_main_thread_context()

        # Step 1: Start the MCP bridge server (inside Blender).
        if not mcp_to_blender_server.is_running():
            if bpy.app.background:
                self.report({"ERROR"}, "Cannot start in background mode")
                return {"CANCELLED"}
            prefs = context.preferences.addons[__package__].preferences
            _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
            try:
                mcp_to_blender_server.start(prefs.host, _bridge_port)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                self.report({"ERROR"}, "Bridge server failed: {:s}".format(str(ex)))
                return {"CANCELLED"}
            # Register timer.
            from . import execute_interactive
            bpy.app.timers.register(
                execute_interactive.run,
                first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
                persistent=True,
            )
            actual_bridge = mcp_to_blender_server.get_actual_port()
            if actual_bridge and actual_bridge != _bridge_port:
                # Issue #85: say plainly that this window got its own port.
                self.report({"INFO"}, "Another Blender window uses port {:d} -- this window's "
                            "Coworker is linked to port {:d}".format(_bridge_port, actual_bridge))
            elif actual_bridge:
                self.report({"INFO"}, "Bridge server started on port {:d}".format(actual_bridge))
            else:
                self.report({"INFO"}, "Bridge server started")

        # Step 2: Start the MCP HTTP server.
        if not agent_controller._agent_state.mcp_server_running:
            prefs = context.preferences.addons[__package__].preferences
            _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
            proc = agent_controller.start_mcp_server(port=_mcp_port, blender_port=_bridge_port)
            if proc is None:
                self.report({"ERROR"}, agent_controller._agent_state.error)
                return {"CANCELLED"}
            actual_mcp = agent_controller._agent_state.mcp_port_actual
            if actual_mcp:
                self.report({"INFO"}, "MCP server started on port {:d}".format(actual_mcp))
            else:
                self.report({"INFO"}, "MCP server started on port {:d}".format(_mcp_port))

        # Step 3: Start the LLM backend (only in local mode).
        # This can be slow (model download or server startup), so it runs
        # on a background thread to avoid freezing Blender's UI.
        prefs = context.preferences.addons[__package__].preferences
        # Sync preferences to llm_manager config before starting.
        _sync_prefs_to_config(prefs)
        llm_cfg = llm_manager.get_config()
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        llm_cfg.local_port = _llm_port
        llm_manager.set_config(llm_cfg)

        if llm_cfg.mode == "local":
            llm_state = llm_manager.get_state()

            def _set_chat_status(msg: str) -> None:
                bpy.app.timers.register(
                    lambda m=msg: setattr(props, "chat_status", m) or _redraw_areas_safe(),
                    first_interval=0.0,
                )

            if not llm_state.is_running:

                def _start_llm_backend():
                    existing_path = prefs.existing_model_path
                    if existing_path and os.path.isfile(existing_path):
                        proc = llm_manager.start_local_llama(model_path=existing_path)
                    else:
                        proc = llm_manager.start_local_llama()
                    if proc is None:
                        _err = llm_manager.get_state().error or "llama-server failed to start"
                        agent_controller._agent_state.error = _err
                        _set_chat_status("Error: " + _err)
                        return
                    # Wait for the model to actually load before claiming
                    # readiness.  Posting the welcome right after Popen makes
                    # it appear even when llama-server crashes at startup
                    # ("welcome message happens, then closes") and the first
                    # real turn then hangs 120s on a dead port.
                    _set_chat_status("Loading model... (large models can take a few minutes)")
                    if not llm_manager.wait_until_ready(timeout=300.0, proc=proc):
                        _err = llm_manager.get_state().error or "llama-server did not become ready"
                        agent_controller._agent_state.error = _err
                        _set_chat_status("Error: " + _err)
                        return
                    # Warm up tools + post welcome message (background thread).
                    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
                    actual_mcp = agent_controller._agent_state.mcp_port_actual
                    warmup_mcp = actual_mcp if actual_mcp else _mcp_port
                    agent_controller.warmup_agent(
                        on_status=lambda s: bpy.app.timers.register(
                            lambda s=s: setattr(props, "chat_status", s) or _redraw_areas_safe(),
                            first_interval=0.0,
                        ),
                        mcp_port=warmup_mcp,
                    )
                    # Mark connected on the main thread after warmup completes.
                    _set_chat_status("Connected")

                thread = threading.Thread(target=_start_llm_backend, daemon=True)
                thread.start()
                props.chat_status = "Starting LLM backend..."
            else:
                # Already running -- warmup in background thread, but only
                # after the model has actually finished loading.
                def _warmup_existing():
                    if llm_manager.get_config().mode == "local":
                        _set_chat_status("Loading model... (large models can take a few minutes)")
                        if not llm_manager.wait_until_ready(
                            timeout=300.0, proc=llm_manager.get_llama_process()
                        ):
                            _err = llm_manager.get_state().error or "llama-server did not become ready"
                            agent_controller._agent_state.error = _err
                            _set_chat_status("Error: " + _err)
                            return
                    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
                    actual_mcp = agent_controller._agent_state.mcp_port_actual
                    warmup_mcp = actual_mcp if actual_mcp else _mcp_port
                    agent_controller.warmup_agent(
                        on_status=lambda s: bpy.app.timers.register(
                            lambda s=s: setattr(props, "chat_status", s) or _redraw_areas_safe(),
                            first_interval=0.0,
                        ),
                        mcp_port=warmup_mcp,
                    )
                    _set_chat_status("Connected")
                threading.Thread(target=_warmup_existing, daemon=True).start()
                props.chat_status = "Warming up..."
        else:
            # In remote mode, no LLM backend is started.
            def _warmup_remote():
                _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
                actual_mcp = agent_controller._agent_state.mcp_port_actual
                warmup_mcp = actual_mcp if actual_mcp else _mcp_port
                agent_controller.warmup_agent(
                    on_status=lambda s: bpy.app.timers.register(
                        lambda s=s: setattr(props, "chat_status", s) or _redraw_areas_safe(),
                        first_interval=0.0,
                    ),
                    mcp_port=warmup_mcp,
                )
                bpy.app.timers.register(
                    lambda: setattr(props, "chat_status", "Connected") or _redraw_areas_safe(),
                    first_interval=0.0,
                )
            threading.Thread(target=_warmup_remote, daemon=True).start()
            props.chat_status = "Warming up..."

        _restore_chat_history()

        # Local-mode status is driven by the background thread (Starting ->
        # Loading -> Connected / Error: ...); only remote mode marks
        # "Connected" here.
        if llm_cfg.mode != "local":
            props.chat_status = "Connected"

        _redraw_areas(context)
        return {"FINISHED"}


def _restore_chat_history() -> None:
    """The saved chat of this .blend back in the chat (on Start, every mode)."""
    history = _load_chat_history()
    # Restore the session-memory store (memory note + checkpoints).
    _load_session_memory_state()
    if history:
        # -- Loaded-history diagnostic -----------------------------
        # A persisted history is restored verbatim, so a stale prompt or
        # a bad shape from an earlier session is sent to the model as-is.
        # Log what was loaded -- the first user message in particular
        # reveals whether the model is answering a stale prompt.
        print("[Coworker] _load_chat_history: loaded {:d} messages from {:s}".format(
            len(history), str(_chat_history_path())))
        print(agent_controller._describe_history_for_log(history))
        # Sanitize before use: a history written by an older build may
        # contain ui_only greetings, a leading assistant message,
        # reasoning entries, or half-finished tool-call exchanges -- all
        # of which trip strict Jinja chat templates.
        history = agent_controller._sanitize_loaded_history(history)
        if history:
            print("[Coworker] _load_chat_history: after sanitize:")
            print(agent_controller._describe_history_for_log(history))
        agent_controller._agent_state.conversation_history = history


class BFACW_OT_agent_stop(Operator):  # type: ignore[misc]
    """Stop the Coworker agent and all subprocesses."""
    bl_idname = "bfacw.agent_stop"
    bl_label = "Stop Coworker"
    bl_description = "Stop the Coworker agent and all subprocesses"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        # External Agent: close the agent app's session.
        if _acp is not None and (_acp.connected or _acp.connecting):
            _acp.disconnect()

        # Stop LLM.
        llm_manager.stop_local_llama()

        # Stop MCP server.
        agent_controller.stop_mcp_server()

        # Stop bridge server.
        if mcp_to_blender_server.is_running():
            from . import execute_interactive
            mcp_to_blender_server.stop()
            if bpy.app.timers.is_registered(execute_interactive.run):
                bpy.app.timers.unregister(execute_interactive.run)

        props.chat_status = "Stopped"
        agent_controller._agent_state.mcp_server_running = False
        # Stopping the agent ends the working session -- clear the token
        # accounting so the context bar does not stay pinned at its old value.
        agent_controller._agent_state.reset_usage()
        agent_controller.clear_session_scene_lock()
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_agent_restart(Operator):  # type: ignore[misc]
    """Restart the Coworker agent (stop then start)."""
    bl_idname = "bfacw.agent_restart"
    bl_label = "Restart Coworker"
    bl_description = "Stop all components and restart the Coworker agent"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        # Stop first.
        props.chat_status = "Stopping..."
        _redraw_areas(context)

        llm_manager.stop_local_llama()
        agent_controller.stop_mcp_server()
        if mcp_to_blender_server.is_running():
            from . import execute_interactive
            mcp_to_blender_server.stop()
            if bpy.app.timers.is_registered(execute_interactive.run):
                bpy.app.timers.unregister(execute_interactive.run)

        agent_controller._agent_state.mcp_server_running = False
        # Fresh agent session -- clear stale token accounting and the
        # session-created entity set.
        agent_controller._agent_state.reset_usage()
        agent_controller.clear_session_scene_lock()

        # Start again after a brief delay.
        def _deferred_start():
            props.chat_status = "Starting..."
            _redraw_areas_safe()
            # Re-register the bridge timer.
            if not mcp_to_blender_server.is_running():
                from . import execute_interactive
                prefs = context.preferences.addons[__package__].preferences
                _bridge_port, _, _ = effective_ports(prefs)
                mcp_to_blender_server.start(prefs.host, _bridge_port)
                bpy.app.timers.register(
                    execute_interactive.run,
                    first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
                    persistent=True)
            # Start MCP server.
            agent_controller.start_mcp_server()
            props.chat_status = "Connected"
            _redraw_areas_safe()
            return None  # Don't repeat timer.

        bpy.app.timers.register(_deferred_start, first_interval=0.5)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Timer for UI updates

# Track whether we already opened the mention popup for the current @
_mention_popup_open = False

# Last status mirrored from the turn worker into the UI property.  The worker
# records ``_agent_state.ui_status`` (plain Python); the main-thread timer
# applies it to ``props.chat_status``.  The marker avoids re-applying an
# unchanged value (which would clobber status text set directly by the
# start/stop operators on the main thread).
_last_applied_ui_status = ""

# Snapshot of the panel-visible agent state from the previous timer tick, and
# when the sidebar was last redrawn.  While idle the timer only redraws when
# the snapshot changes (a turn ended, an error arrived, the bridge stopped...)
# or the slow heartbeat elapses -- never every tick.
_last_ui_signature: tuple = ()
_last_idle_redraw = 0.0
_IDLE_REDRAW_INTERVAL = 2.0


def chat_timer_update() -> float | None:
    """
    Timer callback that periodically redraws chat areas, animates
    the "Thinking..." indicator, and auto-opens the mention popup
    when the user types @ in the input field.

    Registered when the add-on starts, runs while Blender is alive.
    """
    global _mention_popup_open
    global _last_applied_ui_status
    global _last_ui_signature
    global _last_idle_redraw
    from . import agent_controller as _ac

    # Animate thinking dots (every thread's, #358).
    for _t in _ac.THREADS.live():
        _st = _ac.thread_state(_t)
        if _st.is_thinking:
            _st.thinking_dots += 1

    # Fold the engines' events into their turn records (main thread only).
    for _eng in [_engine] + [t.engine for t in _ac.THREADS.live() if not t.is_default]:
        if _eng is None or _eng is _acp:
            continue
        try:
            _eng.drain()
        except Exception as _drain_ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] engine drain failed: {}".format(_drain_ex))
    if _acp is not None:
        try:
            if _acp.drain():
                _save_chat_history() if not _ac._agent_state.is_thinking else None
        except Exception as _drain_ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] external agent drain failed: {}".format(_drain_ex))

    # Engine 6b: an undo step before and after every turn (any engine), so Revert
    # and Edit > Undo History see the agent's turn as one step.  Turns the timer
    # sees start here (queued ones); a sent message already got its "before".
    from . import coworker_api as _cw
    # One turn at a time across threads (#357): whichever thread is thinking (#358).
    _thinker = next((t for t in _ac.THREADS.live() if _ac.thread_state(t).is_thinking), None)
    _thinking = _thinker is not None
    if _thinking != _TURN_SEEN[0]:
        _finished = _TURN_SEEN[1]
        (_cw.turn_started if _thinking else _cw.turn_finished)(_turn_label(None, _thinker or _finished))
        _TURN_SEEN[0] = _thinking
        _TURN_SEEN[1] = _thinker
        if _thinking:
            _thinker.auto["err_before"] = _ac.thread_state(_thinker).error or ""
            _approvals_new_turn(_thinker)   # a queued message is a new turn too
        elif _finished is not None:
            _auto_after_turn(_finished)     # #177: keep going while its plan is on track (#417)

    # Mirror the turn worker's status into the UI property.  The worker must
    # not touch a bpy property itself (that races Blender's context counter);
    # it records plain Python state and we apply it here on the main thread.
    # Apply only when it CHANGED, so status text set directly by the start/stop
    # operators is not clobbered on the next tick.
    _ui_status = _ac._agent_state.ui_status
    if _ui_status and _ui_status != _last_applied_ui_status:
        for wm in bpy.data.window_managers:
            _props = getattr(wm, "bfacw_chat_props", None)
            if _props is not None:
                _props.chat_status = _ui_status
        _last_applied_ui_status = _ui_status

    # Auto-open @mention popup when user types @ in input.
    try:
        for wm in bpy.data.window_managers:
            props = getattr(wm, "bfacw_chat_props", None)
            if props is None:
                continue
            text = props.chat_input or ""
            # Typed in the GPU chat (editor / compact sidebar)?  It has its
            # own inline @ list -- a native popup would steal its keys.
            from . import chat_editor as _ce
            if _ce.draft_from_gpu(text):
                continue
            if "@" in text and not _ac._agent_state.is_thinking:
                last_at = text.rfind("@")
                after = text[last_at + 1:]
                # Only trigger if @ is at end or followed by text (not space).
                if not after or (not after.startswith(" ") and len(after) <= 30):
                    if not _mention_popup_open:
                        _mention_popup_open = True
                        # Defer popup to avoid timer re-entrancy.
                        bpy.app.timers.register(
                            lambda: _open_mention_for_at(text),
                            first_interval=0.0,
                        )
            else:
                _mention_popup_open = False
    except Exception:
        pass

    # Re-link a pinned image the scene dropped (agent undo, orphan cleanup).
    if _sticky_attachment:
        try:
            for wm in bpy.data.window_managers:
                _p = getattr(wm, "bfacw_chat_props", None)
                if _p is not None and _p.chat_image is None:
                    if _restore_attachment(_p) is not None:
                        print("[Coworker] attachment: re-linked the pinned image")
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # Mirror the pinned goal & plan to/from its editable text block.
    try:
        _sync_plan_text()
    except Exception as _plan_ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] plan text sync skipped -- {:s}".format(str(_plan_ex)))

    # Redraw the chat panels -- only when something they show may have changed.
    _st = _ac._agent_state
    _sig = _ui_signature(_st)
    _now = time.monotonic()
    if (_st.is_thinking or _sig != _last_ui_signature
            or _now - _last_idle_redraw >= _IDLE_REDRAW_INTERVAL):
        _last_ui_signature = _sig
        _last_idle_redraw = _now
        _tag_chat_regions_redraw()
    # Tick faster while a turn is running so the spinner reads as motion;
    # otherwise keep the light idle cadence.
    return 0.1 if _ac._agent_state.is_thinking else 0.5


def _ui_signature(state) -> tuple:
    """Cheap snapshot of the agent state the chat panels display.

    The worker thread mutates this state without tagging a redraw, so the
    timer compares snapshots to notice changes (turn finished, history grew,
    error/warning set, server or bridge stopped) while idle.
    """
    from . import session_memory as _sm
    try:
        from . import co_work_guard as _cwg
        locked = _cwg.is_locked()
    except Exception:  # pylint: disable=broad-exception-caught
        locked = False
    return (
        state.is_thinking,
        state.mcp_server_running,
        mcp_to_blender_server.is_running(),
        state.ui_status,
        state.error,
        state.warning,
        len(state.conversation_history),
        len(_sm.store.retired_history),
        locked,
        # The attached image (the GPU chats show it in the input box).
        tuple(getattr(getattr(wm, "bfacw_chat_props", None), "chat_image", None) is not None
              and wm.bfacw_chat_props.chat_image.name or "" for wm in bpy.data.window_managers),
    )


def _tag_chat_regions_redraw() -> None:
    """Redraw the sidebar (UI) regions that host the chat panels.

    Tags the region, not the area: ``area.tag_redraw()`` also redraws the
    3D viewport itself, which made the scene re-render on every timer tick.
    """
    from . import chat_editor as _ce
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            for area in win.screen.areas:
                if area.type == 'NODE_EDITOR' and _ce.is_coworker_space(area.spaces.active):
                    for region in area.regions:
                        if region.type in {'WINDOW', 'HEADER', 'UI'}:
                            region.tag_redraw()
                    continue
                if area.type not in SIDEBAR_TYPES:
                    continue
                for region in area.regions:
                    if region.type == 'UI':
                        region.tag_redraw()


def _open_mention_for_at(text: str) -> float | None:
    """Open the mention popup filtered by the text after @."""
    global _mention_popup_open
    try:
        last_at = text.rfind("@")
        after = text[last_at + 1:] if last_at >= 0 else ""
        # Don't open if user already inserted a mention (space after @).
        if after.startswith(" "):
            _mention_popup_open = False
            return None
        bpy.ops.bfacw.mention_search(filter_text=after)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Panels

def draw_chat_controls(layout, context: bpy.types.Context, show_chat_mode: bool = True,
                       show_prefs: bool = True, minimal_image: bool = False,
                       show_image: bool = True, has_header: bool = False) -> bool:
    """The chat's controls: mode, Start / Stop, status, scene protection,
    External Harness setup, chat mode, reasoning effort and the image socket.

    Shared by the 3D View chat panel and the Coworker editor's sidebar (Tier 4,
    GPU 4).  Returns False in External Harness mode, where the chat itself
    runs in the external client (nothing else to draw).

    *has_header*: the chat above has its own Start / Stop, status and settings (the
    Coworker editor's header, the sidebar chat's top bar), so they are not drawn
    again here (#234).
    """
    wm = context.window_manager
    props = wm.bfacw_chat_props  # type: ignore[attr-defined]
    state = agent_controller._agent_state
    prefs = context.preferences.addons[__package__].preferences
    is_harness = (prefs.operating_mode == "EXTERNAL_HARNESS")

    # -- The operating mode, switchable right here (#193) --
    narrow = context.region is not None and context.region.width < 320 * context.preferences.system.ui_scale
    mode_row = draw_mode_switcher(layout, prefs, icons_only=narrow)
    mode_row.enabled = not _mode_locked(prefs)
    if show_prefs and not has_header:     # else the header / top bar has it (#234)
        mode_row.separator(factor=0.3)
        mode_row.operator("bfacw.open_addon_prefs", icon="PREFERENCES", text="")

    # -- External Agent: which agent app does the work --
    if prefs.operating_mode == "EXTERNAL_AGENT":
        draw_external_agent_setup(layout, context, compact=True)
    _draw_engine_options(layout, context, prefs)     # Local / Remote: Model / Effort, like the agent's (#195)

    # -- Agent control buttons (compact) --
    # (With a header / top bar, Start / Stop is there: #234.)
    row = layout.row(align=True)
    row.scale_y = 1.8
    if has_header:
        pass
    elif is_harness:
        if mcp_to_blender_server.is_running():
            row.operator("bfacw.agent_stop", icon="X", text="Stop Bridge")
        else:
            row.operator("bfacw.agent_start", icon="PLAY", text="Start Bridge")
    elif state.mcp_server_running:
        row.operator("bfacw.agent_stop", icon="X", text="Stop")
    else:
        row.operator("bfacw.agent_start", icon="PLAY", text="Start")

    # -- Compact status line --
    if is_harness:
        status = "Bridge Running" if mcp_to_blender_server.is_running() else "Bridge Offline"
        is_ok = mcp_to_blender_server.is_running()
    else:
        status = props.chat_status
        if prefs.operating_mode == "EXTERNAL_AGENT" and not state.is_thinking:
            status = _acp.status if _acp is not None else "Not connected"
        elif state.is_thinking:
            elapsed = time.time() - state.thinking_start_time if state.thinking_start_time else 0.0
            status = "{:s} {:s} ({:s})".format(
                _phase_text(state), _spinner_char(state),
                _fmt_duration(elapsed))
        elif not state.mcp_server_running and prefs.operating_mode != "EXTERNAL_AGENT":
            status = "Offline"
        elif state.error:
            status = "Error: {:s}".format(state.error)
        is_ok = state.mcp_server_running

    status_icon = (
        'CHECKMARK' if is_ok and not state.is_thinking and not state.error else
        'SORTTIME' if state.is_thinking else
        'ERROR' if state.error else 'CANCEL'
    )
    if not has_header:      # the header / top bar shows the status (#234)
        status_row = layout.row()
        status_row.label(text="", icon=status_icon)
        _draw_multiline(status_row, status)
    if state.error:
        err_row = layout.row(align=True)
        err_row.scale_y = 0.8
        err_row.operator(
            "bfacw.copy_status_error",
            icon="COPYDOWN",
            text="Copy Error",
        )
    elif state.warning:
        # Non-fatal notice (e.g. tool-calling downgrade).  Shown when
        # there is no error, so it never masks a real failure.
        warn_row = layout.row()
        warn_row.scale_y = 0.9
        warn_row.label(text="", icon='INFO')
        _draw_multiline(warn_row, state.warning)

    # -- Co-work scene protection notice (scene safety Phase 1) -----
    # While the coworker works it briefly makes the objects it created
    # un-selectable (the soft lock), so the user cannot re-target them
    # mid-turn.  Say so -- otherwise the "I can't click this" moment
    # looks like a bug.  Everything is released when the turn ends.
    if not is_harness:
        try:
            from . import co_work_guard as _cwg
            if _cwg.is_locked():
                _lo, _lc = _cwg.managed_names()
                lock_row = layout.row()
                lock_row.scale_y = 0.8
                lock_row.label(
                    text="Scene protection: {:d} object(s) temporarily "
                         "unselectable while working".format(len(_lo) + len(_lc)),
                    icon='LOCKED',
                )
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # -- External Harness mode --
    if is_harness:
        # Only what is often changed (#234): the client, and its config to paste.
        # Everything else is in Configure (the card, or here in the classic panel).
        row = layout.row(align=True)
        row.prop(prefs, "harness_preset", text="")
        op = row.operator("bfacw.copy_mcp_config", icon="COPYDOWN", text="Copy Config")
        op.client_type = prefs.harness_preset
        if not has_header:
            layout.operator("bfacw.open_harness_prefs", icon="PREFERENCES", text="Configure")
            layout.label(text="Chat handled by external client.", icon='INFO')
        return False

    # -- Mode toggle --
    if show_chat_mode:
        row = layout.row(align=True)
        row.prop(props, "chat_mode", expand=True)
    if not has_header:          # the GPU chats have it on their input row (#240)
        layout.prop(prefs, "approval_mode", text="Approval")

    layout.separator()

    # -- Image attachment socket (issue #88 / Tier 3k) ----------
    # Above the chat input: set the image BEFORE typing, so what is
    # attached is visible right next to the message it will ride with.
    if not show_image:      # the GPU chat has it on its input's button row (#213)
        return True
    if minimal_image:       # the Coworker editor: the picker and the preview (#171)
        _draw_attachment_row(layout, props, context, minimal=True)
    else:
        _draw_attachment_row(layout, props, context)
    return True


# The editors with a right sidebar that get a Coworker tab (#291): space type,
# its Space class, a short id for the panel copies, and the toggle's icon.
SIDEBAR_SPACES = (
    ('VIEW_3D', "SpaceView3D", "view3d", 'VIEW3D'),
    ('IMAGE_EDITOR', "SpaceImageEditor", "image", 'IMAGE'),
    ('NODE_EDITOR', "SpaceNodeEditor", "node", 'NODETREE'),
    ('SEQUENCE_EDITOR', "SpaceSequenceEditor", "sequencer", 'SEQUENCE'),
    ('CLIP_EDITOR', "SpaceClipEditor", "clip", 'TRACKER'),
    ('DOPESHEET_EDITOR', "SpaceDopeSheetEditor", "dopesheet", 'ACTION'),
    ('GRAPH_EDITOR', "SpaceGraphEditor", "graph", 'GRAPH'),
    ('NLA_EDITOR', "SpaceNLA", "nla", 'NLA'),
    ('TEXT_EDITOR', "SpaceTextEditor", "text", 'TEXT'),
    ('SPREADSHEET', "SpaceSpreadsheet", "spreadsheet", 'SPREADSHEET'),
)
SIDEBAR_TYPES = frozenset(s[0] for s in SIDEBAR_SPACES)
# Editors that still offer the classic (layout) chat; the others always show the compact one.
_CLASSIC_TYPES = frozenset(('VIEW_3D', 'TEXT_EDITOR'))


def docker_area(area, prefs) -> bool:
    """True when *area*'s sidebar shows the compact GPU chat (not the Coworker editor itself)."""
    kind = getattr(area, "type", "")
    if kind not in SIDEBAR_TYPES:
        return False
    if kind == 'NODE_EDITOR':
        from . import chat_editor as _ce
        if _ce.is_coworker_space(area.spaces.active):
            return False
    return kind not in _CLASSIC_TYPES or getattr(prefs, "chat_sidebar_style", "CLASSIC") == "COMPACT"


def _compact_sidebar(context: bpy.types.Context) -> bool:
    """True when this editor's Coworker tab shows the compact GPU chat.

    Then the classic chat panel steps aside for BFACW_PT_chat_compact (the GPU
    chat in a panel); Session, Queue and Status stay below it.
    """
    try:
        prefs = context.preferences.addons[__package__].preferences
        return docker_area(context.area, prefs)
    except (KeyError, AttributeError, ReferenceError):
        return False


class BFACW_PT_chat_compact(Panel):  # type: ignore[misc]
    """The compact chat, as a real panel: collapse, drag and pin it like any other.

    The panel only reserves room; the GPU chat (chat_editor.py) paints it.
    Invisible marker images at the room's top and bottom let the chat find it in
    the drawn region, so it follows the panel wherever Blender puts it
    (dragged, pinned to another tab, scrolled, Ctrl+MMB zoomed).
    """
    bl_label = "Coworker Chat"
    bl_idname = "BFACW_PT_chat_compact"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_order = 2          # under Queue and Plan (#212)

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background and _compact_sidebar(context)

    def draw_header(self, _context: bpy.types.Context) -> None:
        self.layout.label(text="", icon=_AGENT_ICON)

    def draw(self, context: bpy.types.Context) -> None:
        from . import chat_editor as _ce
        props = context.window_manager.bfacw_chat_props  # type: ignore[attr-defined]
        draw_chat_tabs(self.layout, context)            # Threads 2c (#427)
        col = self.layout.column(align=True)
        _ce.draw_marker_row(col, "top")
        room = col.column()
        room.scale_y = max(1.0, props.chat_panel_rows - 1.0)
        room.label(text="")
        _ce.draw_marker_row(col, "bottom")


# -- Threads 2c (#427): the chat tabs --------------------------------------------------------
# One strip in every chat (Classic and Compact sidebars, the Coworker editor's header):
# a tab per open thread (its editor's icon, a short title, its status; the shown one
# pressed), a × to close it, one + for a new chat for All editors and, in an editor, a
# button with its icon for a chat with its context (#466), and a menu for every tab
# (and New Chat for This <editor>, Start This Chat Fresh).

def _editor_icon(editor: str) -> str:
    return next((s[3] for s in SIDEBAR_SPACES if s[0] == editor), 'WORLD' if not editor else 'WINDOW')


# A Node Editor named by its tree, for the tooltips ("New Chat for This Shader Editor").
_NODE_TREE_NAMES = {"ShaderNodeTree": "Shader Editor", "GeometryNodeTree": "Geometry Node Editor",
                    "CompositorNodeTree": "Compositor", "TextureNodeTree": "Texture Node Editor"}


def _area_name(context) -> str:
    area = getattr(context, "area", None)
    if area is None or _is_coworker_area(area):
        return ""
    if area.type == 'NODE_EDITOR':
        tree = getattr(getattr(area.spaces, "active", None), "tree_type", "")
        return _NODE_TREE_NAMES.get(tree, "Node Editor")
    return _EDITOR_NAMES.get(area.type, "")


def _tab_kind(context) -> str:
    """The editor context a new tab here gets ("" in the Coworker editor: All editors)."""
    area = getattr(context, "area", None)
    if area is None or _is_coworker_area(area) or area.type not in SIDEBAR_TYPES:
        return ""
    return area.type


def _tab_label(thread) -> str:
    from . import threads
    from .chat_history import title_of
    return threads.tab_title(thread, title_of(agent_controller.thread_state(thread).conversation_history))


def _tab_icon(thread) -> tuple[str, bool]:
    """``(icon, alert)``: the status when it isn't Idle, else the editor's icon; alert
    (red) while it waits for you."""
    word, icon = _thread_status(thread)
    if word == "Idle":
        return _editor_icon(thread.editor), False
    return icon, word == "Waiting for you"


def tab_strip(context, units: float = 0.0):
    """``(shown, tabs, labels, modes)`` for this chat's strip.  *units*: the room in UI
    units (default: the region's width)."""
    from . import threads
    reg = agent_controller.THREADS
    shown = _ui_thread(context)
    tabs = reg.live()
    labels = [_tab_label(t) for t in tabs]
    if not units:
        region = getattr(context, "region", None)
        scale = context.preferences.system.ui_scale if context.preferences else 1.0
        units = region.width / (20.0 * scale) if region is not None else 14.0
    # Python can't measure text: about a third of a UI unit a character, plus the icon
    # (tab_modes adds the icon's unit to a titled tab).  Blender cuts a title that doesn't
    # fit with "...", so in a narrow strip a title is given at most two fifths of it,
    # leaving room for the other tabs' icons.
    cap = max(3.0, 0.4 * units)
    widths = [min(cap, 0.33 * len(label)) + 0.2 for label in labels]
    shown_i = tabs.index(shown) if shown in tabs else 0
    return shown, tabs, labels, threads.tab_modes(widths, shown_i, units, icon_w=1.0,
                                                  tail_w=_tab_tools_w(context) + 0.5)


_TAB_TOOLS_W = 3.0          # the tools to the right of the tabs: close, new, the tab menu


def _tab_tools_w(context) -> float:
    """The tools' width: one unit more where the strip has an editor's own + (#466)."""
    return _TAB_TOOLS_W + (1.0 if _tab_kind(context) else 0.0)


# New Chat (#466): the + is for All editors everywhere; one editor's context is an opt-in.
_NEW_CHAT_SCOPES = (('ALL', "All Editors", "A chat for All editors"),
                    ('EDITOR', "This Editor", "A chat using this editor's context"))


def new_tab_tip(editor_name: str = "") -> str:
    """New Chat's tooltip: for All editors, or with *editor_name*'s context (#466)."""
    if editor_name:
        return "New chat in a new tab, using this {:s}'s context".format(editor_name)
    return "New chat in a new tab, for All editors"


def new_tab_editor(scope: str, kind: str) -> str:
    """The editor a new tab gets: All editors ("") unless *scope* is ``EDITOR``, then *kind*."""
    return kind if scope == 'EDITOR' else ""


def new_chat_for_label(context) -> str:
    """"New Chat for This 3D Viewport" where the chat has an editor's context, else ""."""
    kind = _tab_kind(context)
    if not kind:
        return ""
    return "New Chat for This {:s}".format(_area_name(context) or _EDITOR_NAMES.get(kind, kind))


def draw_new_chat_entries(layout, context) -> None:
    """New Chat, New Chat for This <editor> (only where the chat has one) and Start This
    Chat Fresh (greyed out while that chat's turn runs), in the tab menu, the Chat menu
    and the chat's right-click menu (#466)."""
    from . import threads
    layout.operator("bfacw.tab_new", text="New Chat", icon='ADD')
    label = new_chat_for_label(context)
    if label:
        layout.operator("bfacw.tab_new", text=label, icon=_editor_icon(_tab_kind(context))).scope = 'EDITOR'
    thread = _ui_thread(context)
    fresh = layout.row()
    fresh.enabled = not threads.close_refused(agent_controller.THREADS, thread,
                                              agent_controller.thread_state(thread).turn_active)
    fresh.operator("bfacw.chat_clear", text="Start This Chat Fresh",
                   icon=safe_icon('FILE_REFRESH')).thread_id = thread.id


def draw_chat_tabs(layout, context, units: float = 0.0) -> None:
    """The tab strip (see :func:`tab_strip`)."""
    from . import threads
    reg = agent_controller.THREADS
    shown, tabs, labels, modes = tab_strip(context, units)
    row = layout.row()
    strip = row.row(align=True)             # the tabs, left: they share what's left of the row
    for thread, label, mode in zip(tabs, labels, modes):
        if mode == "hidden":
            continue
        icon, alert = _tab_icon(thread)
        cell = strip.row(align=True)
        cell.alert = alert
        op = cell.operator("bfacw.tab_show", text=label if mode == "full" else "", icon=icon,
                           depress=thread is shown)
        op.thread_id = thread.id
    row.separator()
    kind = _tab_kind(context)
    tools = row.row(align=True)             # fixed width, to the right: close, new, (new here,) the menu
    tools.ui_units_x = _tab_tools_w(context)
    close = tools.row(align=True)           # closes the shown tab
    close.enabled = not threads.close_refused(reg, shown, agent_controller.thread_state(shown).turn_active)
    close.operator("bfacw.tab_close", text="", icon='X').thread_id = shown.id
    tools.operator("bfacw.tab_new", text="", icon='ADD')            # All editors (#466)
    if kind:                                # with this editor's context, under its icon (#466)
        tools.operator("bfacw.tab_new", text="", icon=_editor_icon(kind)).scope = 'EDITOR'
    tools.menu("BFACW_MT_chat_tabs", text="", icon='DOWNARROW_HLT')


class BFACW_MT_chat_tabs(bpy.types.Menu):  # type: ignore[misc]
    """Every open chat tab, with its status"""
    bl_idname = "BFACW_MT_chat_tabs"
    bl_label = "Chat Tabs"

    def draw(self, context: bpy.types.Context) -> None:
        shown = _ui_thread(context)
        for thread in agent_controller.THREADS.live():
            word, _icon = _thread_status(thread)
            icon, _alert = _tab_icon(thread)
            text = "{:s}  --  {:s}".format(_tab_label(thread), word)
            op = self.layout.operator("bfacw.tab_show", text=("\u25cf " if thread is shown else "") + text,
                                      icon=icon)
            op.thread_id = thread.id
        self.layout.separator()
        draw_new_chat_entries(self.layout, context)         # #466


class BFACW_OT_tab_show(Operator):  # type: ignore[misc]
    """Show this chat here"""
    bl_idname = "bfacw.tab_show"
    bl_label = "Show Chat"
    bl_options = {'INTERNAL'}

    thread_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties) -> str:
        thread = agent_controller.THREADS.get(properties.thread_id)
        if thread is None:
            return cls.__doc__
        where = "All editors" if not thread.editor else _EDITOR_NAMES.get(thread.editor, thread.editor)
        return "{:s} ({:s}): {:s}".format(_tab_label(thread), where, _thread_status(thread)[0])

    def execute(self, context: bpy.types.Context) -> set[str]:
        thread = agent_controller.THREADS.get(self.thread_id)
        if thread is None:
            return {"CANCELLED"}
        _show_in_area(getattr(context, "area", None), thread)       # this area's tab (its pin)
        return {"FINISHED"}


class BFACW_OT_tab_new(Operator):  # type: ignore[misc]
    """New chat in a new tab, for All editors"""
    bl_idname = "bfacw.tab_new"
    bl_label = "New Chat"
    bl_options = {'INTERNAL'}

    # ALL (the +): a chat for All editors; EDITOR: with this editor's context (#466).
    scope: EnumProperty(items=_NEW_CHAT_SCOPES, default='ALL', options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def description(cls, context, properties) -> str:
        if context is None or getattr(properties, "scope", 'ALL') != 'EDITOR' or not _tab_kind(context):
            return new_tab_tip("")
        return new_tab_tip(_area_name(context))

    def execute(self, context: bpy.types.Context) -> set[str]:
        thread = _editor_thread(new_tab_editor(self.scope, _tab_kind(context)))
        _show_in_area(getattr(context, "area", None), thread)
        return {"FINISHED"}


class BFACW_OT_tab_close(Operator):  # type: ignore[misc]
    """Close (stays in History)"""
    bl_idname = "bfacw.tab_close"
    bl_label = "Close Chat"
    bl_options = {'INTERNAL'}

    thread_id: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties) -> str:
        thread = agent_controller.THREADS.get(properties.thread_id)
        if thread is not None and thread.is_default:
            return "Start All editors fresh (this chat stays in History)"
        return "Close (stays in History)"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import threads
        reg = agent_controller.THREADS
        thread = reg.get(self.thread_id)
        if thread is None:
            return {"CANCELLED"}
        why = threads.close_refused(reg, thread, agent_controller.thread_state(thread).turn_active)
        if why:
            self.report({"WARNING"}, why)
            return {"CANCELLED"}
        if thread.is_default:
            _fresh_default_chat()
            _redraw_all_chats()
            return {"FINISHED"}
        if thread.auto["run"] is not None:
            _auto_toggle(thread, False)
        _save_thread_chat(thread)
        for key in [k for k, v in _AREA_PINS.items() if v == thread.id]:
            del _AREA_PINS[key]             # those areas show All editors again
        if _socket().focus == thread.id:
            _focus_thread(reg.default)
        if _acp is not None and _acp.connected:
            _acp.forget_thread(threads.tag(thread))
        reg.remove(thread.id)
        _save_tabs()
        _redraw_all_chats()
        return {"FINISHED"}


class BFACW_OT_thread_open_editor(Operator):  # type: ignore[misc]
    """Open the Coworker editor on this chat: the full chat in its own window"""
    bl_idname = "bfacw.thread_open_editor"
    bl_label = "Open in Coworker Editor"
    bl_options = {'INTERNAL'}

    def execute(self, context: bpy.types.Context) -> set[str]:
        thread = _ui_thread(context)
        bpy.ops.bfacw.open_coworker_editor()
        for window in context.window_manager.windows:       # the editor shows this thread (its pin)
            for area in window.screen.areas:
                if _is_coworker_area(area):
                    _AREA_PINS[_pin_key(area)] = thread.id
        _redraw_all_chats()
        return {"FINISHED"}


class BFACW_OT_thread_focus(Operator):  # type: ignore[misc]
    """Put this chat's attached image in the image socket, to change or remove it"""
    bl_idname = "bfacw.thread_focus"
    bl_label = "Change Image"
    bl_options = {'INTERNAL'}

    def execute(self, context: bpy.types.Context) -> set[str]:
        _focus_thread(_ui_thread(context))
        _redraw_areas(context)
        return {"FINISHED"}


_ATTACH_POPOVER: dict = {"thread": ""}      # the chat whose attach button opened the popover (#449)


def open_attach_popover(context) -> None:
    """Open the Attach popover for the chat of *context*'s area (the GPU chat's attach button)."""
    thread = _ui_thread(context)
    _ATTACH_POPOVER["thread"] = thread.id
    _focus_thread(thread)       # the socket the popover shows holds this chat's image (#417)
    bpy.ops.wm.call_panel(name="BFACW_PT_attach_popover", keep_open=True)


def _draw_image_picker(layout, props, thread_id: str, new: str = "") -> None:
    """The socket's picker for an image already in the file, and our own Open (#449).

    Not ``template_ID(open="image.open")``: Blender's Open fills the button it was started
    from, and a popover opened by the GPU chat (or a sidebar redrawn meanwhile) has none to
    fill, so the image loaded and nothing was attached.  ``bfacw.chat_attach_image`` keeps the
    chat it is for (*thread_id*) and reads any image Blender can."""
    row = layout.row(align=True)
    if new:
        row.template_ID(props, "chat_image", new=new)
    else:
        row.template_ID(props, "chat_image")
    op = row.operator("bfacw.chat_attach_image", text="", icon=safe_icon('FILEBROWSER'))
    if op is not None:
        op.thread_id = thread_id


class BFACW_PT_attach_popover(Panel):  # type: ignore[misc]
    """The image for the next message: pick, open, render or screen (#213)"""
    bl_label = "Attach"
    bl_idname = "BFACW_PT_attach_popover"
    bl_space_type = 'TOPBAR'          # only shown as a popover, from the chat's input
    bl_region_type = 'HEADER'
    bl_ui_units_x = 12

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        props = context.window_manager.bfacw_chat_props  # type: ignore[attr-defined]
        layout.label(text="Image for the next message", icon='IMAGE_DATA')
        thread = _attach_thread(context, _ATTACH_POPOVER["thread"])
        _draw_image_picker(layout, props, thread.id)
        op = layout.operator("bfacw.chat_paste_image", text="Paste Image", icon='PASTEDOWN')     # #450
        if op is not None:
            op.thread_id = thread.id
        if props.chat_image is not None:
            _draw_attachment_preview(layout, props.chat_image, context, max_units=6.0)
            layout.prop(props, "chat_image_send_once", text="Send Once")
        # Render and Screen are on the input row itself; Open is the picker's (#265).
        hint = layout.row()
        hint.scale_y = 0.75
        hint.label(text="Or drop or paste an image (Ctrl V).")


class BFACW_PT_chat_controls(Panel):  # type: ignore[misc]
    """The chat's controls under the compact sidebar chat: mode tabs, engine, Start (#212)"""
    bl_label = "Coworker"
    bl_idname = "BFACW_PT_chat_controls"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 5          # last (#212)

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background and _compact_sidebar(context)

    def draw(self, context: bpy.types.Context) -> None:
        # The compact chat's top bar has Coworker / Ask / Plan, its input the image (#213).
        draw_chat_controls(self.layout, context, show_chat_mode=False, show_image=False, has_header=True)


class BFACW_PT_chat_panel(Panel):  # type: ignore[misc]
    """Main chat panel in the 3D Viewport sidebar -- input and messages."""
    bl_label = "Coworker"
    bl_idname = "BFACW_PT_chat_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_order = 2          # the classic chat, in the compact chat's place

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background and not _compact_sidebar(context)

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = _ui_state(context)      # the thread this chat shows (#358)

        draw_chat_tabs(layout, context)     # Threads 2c (#427)
        if not draw_chat_controls(layout, context):
            return

        # -- Input area --
        layout.textbox(props, "chat_input")

        # @mention button.
        row = layout.row(align=True)
        row.operator("bfacw.mention_search", icon="OUTLINER_OB_MESH", text="@ Mention")

        # -- Action buttons --
        if state.is_thinking:
            # During thinking: Stop + Queue side by side.
            btn_row = layout.row(align=True)
            btn_row.scale_y = 1.5
            btn_row.operator("bfacw.chat_queue_send", icon="ADD", text="Queue")
            btn_row.operator("bfacw.chat_stop", icon="PAUSE", text="Stop")
        else:
            # Idle: Send (+ Revert).
            btn_row = layout.row(align=True)
            btn_row.scale_y = 1.5
            btn_row.operator("bfacw.chat_send", icon="PLAY", text="Send")
            from . import coworker_api
            if coworker_api.can_revert():
                btn_row.operator("bfacw.revert_turn", icon="LOOP_BACK", text="")

        draw_approval_box(layout)

        # Goal & Plan has its own panel now (Plan, right under Queue; #171).

        layout.separator()

        # -- Conversation history --------------------------------------
        # Drawn here, directly under the input and action buttons, so the
        # messages read as one continuous chat instead of a detached panel.
        self._draw_chat_history(context)

    def _draw_chat_history(self, context: bpy.types.Context) -> None:
        """Draw the conversation history (turns) into this panel.

        Kept as a method so it renders with the chat input, Send button and
        agent controls (the History ``Panel`` was removed; the section now
        lives inside the Coworker panel).
        """
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = _ui_state(context)      # the thread this chat shows (#358)
        prefs = context.preferences.addons[__package__].preferences
        # Snapshot the history ONCE per frame.  The turn loop runs on a worker
        # thread and MUTATES this same list mid-draw (compaction reassigns it,
        # spiral recovery truncates it, auto-continue pops from it).  Iterating
        # the live list raised "list changed size during iteration", which
        # aborted the Panel draw and made the whole chat history disappear for
        # the rest of a long turn.  ``list()`` copies atomically under the GIL,
        # so grouping, lookup and rendering all see one consistent view.
        live = list(state.conversation_history)
        # Cumulative display: retired turns (kept in memory by the session
        # store) are rendered as ORDINARY turns above the live window in ONE
        # continuous conversation, so a compaction never looks like the chat
        # vanished.  Grouping the COMBINED list also recombines a turn whose
        # prompt was retired while its reply stayed live (an earlier compaction
        # could split one) back into a single bubble.  The model's context is
        # unaffected -- retired turns are never re-sent.
        from . import session_memory as _sm
        archived = list(_ui_store(context).retired_history)
        combined = archived + live
        # id -> index maps, built once per draw: every copy button and
        # Workshop entry needs its message's index, and a linear scan per
        # lookup made a long chat's draw quadratic (at up to 10 draws/sec).
        archived_index = {id(m): i for i, m in enumerate(archived)}
        live_index = {id(m): i for i, m in enumerate(live)}

        if combined:
            # Display order toggle + message count.
            hist_box = layout.box()
            toggle_row = hist_box.row(align=True)
            toggle_row.prop(
                props, "chat_newest_first",
                icon='SORTTIME', text="Newest First",
            )
            # Count displayable messages (exclude system/internal).
            displayable = sum(1 for m in combined if m.get("role") != "system")
            _draw_multiline(
                hist_box,
                "({:d} messages)".format(displayable),
            )
            # The UI-only welcome greeting no longer forms a turn, so show it
            # once here -- otherwise it would never be visible.  Keep the
            # agent hat/icon header so it reads as a Coworker message, not a
            # bare line of text.
            for _g in combined:
                if (_g.get("ui_only") and _g.get("role") == "assistant"
                        and _g.get("content")):
                    _gr = hist_box.row()
                    _gr.label(text="* Coworker:", icon=_AGENT_ICON)
                    _draw_multiline(hist_box, _g.get("content", ""))
                    break

            # Group the FULL conversation (retired + live) into turns.  Each
            # question renders as ONE bubble: your prompt, the reasoning
            # (surface), the Workshop (deeper), then the conclusion -- whether
            # or not the turn has since been retired from the model's context.
            turns = _group_turns(combined)

            # Determine display order and turn limit.
            max_turns = prefs.chat_max_visible_turns
            visible_turns = turns[-max_turns:] if max_turns > 0 else turns
            turn_iter = (
                reversed(visible_turns) if props.chat_newest_first
                else visible_turns
            )

            # Absolute turn number by IDENTITY (not list.index, which
            # compares by value and could collide on equal turns).
            turn_numbers = {id(t): i + 1 for i, t in enumerate(turns)}

            for _display_idx, turn in enumerate(turn_iter):
                _turn_num = turn_numbers.get(id(turn), 0)
                try:
                    self._draw_turn(
                        hist_box, archived, live,
                        archived_index, live_index, turn,
                        _turn_num, _display_idx, len(visible_turns),
                        props, state,
                    )
                except Exception as _draw_ex:  # pylint: disable=broad-exception-caught
                    # One unexpected turn must never blank the WHOLE history
                    # (a single raising lookup/draw used to vanish the panel
                    # for the rest of a long turn).  Skip just this turn.
                    print("[Coworker] chat history: skipped a turn that failed "
                          "to draw -- {:s}".format(str(_draw_ex)))

        else:
            _draw_multiline(
                layout,
                "Start a conversation by typing a message and clicking Send.",
                icon='INFO',
            )

    def _draw_turn(
        self,
        hist_box,
        archived: list,
        live: list,
        archived_index: dict,
        live_index: dict,
        turn: list,
        turn_num: int,
        display_idx: int,
        visible_count: int,
        props,
        state,
    ) -> None:
        """Draw one conversation turn (user input, Workshop, reply).

        Split out of ``_draw_chat_history`` so a single bad turn can be skipped
        in isolation (see the caller's try/except) instead of aborting the
        whole panel -- the failure mode that hid the chat history during a
        long turn.

        A turn's messages may live in the retired store, the live history, or
        (for a turn split by an earlier compaction) both; each copy action is
        pointed at the list its message actually belongs to.
        """
        def _idx(msg: dict) -> tuple[int, bool]:
            """(index, archived) for *msg* from whichever list holds it."""
            if id(msg) in archived_index:
                return archived_index[id(msg)], True
            if id(msg) in live_index:
                return live_index[id(msg)], False
            # Not in either snapshot by identity: fall back to the
            # equality match (-1 when gone).
            return _hist_index(live, msg), False

        def _copy(op, msg: dict) -> None:
            """Point a copy operator at the right source list + index."""
            op.message_index, op.archived = _idx(msg)

        user_msg, process_msgs, conclusion_msg = _split_turn(turn)
        if not user_msg:
            if conclusion_msg:
                tb = hist_box.box()
                cr = tb.row()
                cr.label(text="Turn {:d} -- {:s}:".format(turn_num, conclusion_msg.get("agent") or "Coworker"),
                         icon=_AGENT_ICON)
                _copy(cr.operator("bfacw.copy_message", text="", icon="COPYDOWN"),
                      conclusion_msg)
                _draw_multiline(tb, conclusion_msg.get("content", ""))
            return
        has_proc = bool(process_msgs)
        turn_box = hist_box.box()

        # Only the active (newest) turn animates while thinking -- past turns
        # keep a static label.  Computed before the header so the header can
        # show the live elapsed time for the running turn.
        is_newest_turn = (
            (props.chat_newest_first and display_idx == 0)
            or (not props.chat_newest_first
                and display_idx == visible_count - 1)
        )
        is_active_turn = state.is_thinking and is_newest_turn

        # --- Turn header (always visible) ---
        has_err = has_proc and any(
            p.get("role") == "tool"
            and ('"status": "error"' in (p.get("content") or "")
                 or (p.get("content") or "").startswith("Error"))
            for p in process_msgs
        )
        tic = "CHECKMARK" if conclusion_msg else "USER"
        hr = turn_box.row(align=True)
        hr.label(text="", icon=tic)
        sub = hr.row(align=True)
        sub.scale_x = 0.5
        sub.label(text="Turn {:d}".format(turn_num))
        # Turn duration: the finished turn carries ``turn_seconds`` (stamped by
        # the turn loop on its last message); the active turn shows elapsed so
        # far.  Formatted with minutes (long local turns run into the minutes).
        _turn_seconds = None
        for _m in reversed(turn):
            if isinstance(_m.get("turn_seconds"), (int, float)):
                _turn_seconds = float(_m["turn_seconds"])
                break
        _dur_text = ""
        if is_active_turn and getattr(state, "thinking_start_time", 0.0):
            _dur_text = _fmt_duration(time.time() - state.thinking_start_time)
        elif _turn_seconds is not None:
            _dur_text = _fmt_duration(_turn_seconds)
        if _dur_text:
            sub.label(text="| {:s}".format(_dur_text))
        _copy(hr.operator("bfacw.copy_message", text="", icon="COPYDOWN"),
              user_msg)

        # --- User message (always visible) ---
        # Labelled so every turn clearly shows what the user typed, mirroring
        # the "* Coworker:" label on the reply below.
        urow = turn_box.row()
        urow.label(text="You:", icon='USER')
        _draw_user_text(turn_box, user_msg.get("content", ""))

        # --- Workshop (collapsible: only the internals collapse) ---
        if has_proc:
            ph, pb = turn_box.panel(
                "turn_proc_{:d}".format(turn_num),
                default_closed=True,
            )
            pb_icon = safe_icon("WARNING") if has_err else "PACKAGE"
            if is_active_turn:
                ws_label = "Workshop {:s}".format(_spinner_char(state))
            else:
                ws_label = "Workshop"
            ph.label(text=ws_label, icon=pb_icon)
            if pb:
                work_box = pb.box()
                for pm in process_msgs:
                    pr = pm.get("role", "")
                    pc = pm.get("content", "")
                    is_sm = _is_system_note_msg(pm)
                    if is_sm:
                        # Agent-to-itself guidance: a compact, titled note --
                        # never a user bubble.
                        _title, _icon = _NOTE_TITLES.get(
                            str(pm.get("system_note") or ""),
                            ("System context", 'INFO'))
                        sb = work_box.box()
                        sb.scale_y = 0.85
                        sb.label(text=_title, icon=_icon)
                        _body = pc if isinstance(pc, str) else ""
                        if _body.startswith("[System:") and _body.endswith("]"):
                            _body = _body[len("[System:"):-1].strip()
                        _draw_multiline(sb, _body)
                    elif pr == "reasoning":
                        _pm_idx, _pm_arch = _idx(pm)
                        _draw_reasoning(
                            work_box, pc, pm.get("label", "Thinking"),
                            is_thinking=False,
                            thinking_dots=0,
                            message_index=_pm_idx,
                            archived=_pm_arch,
                        )
                    elif pr == "tool":
                        tn = pm.get("name", "")
                        ts = pm.get("summary", "")
                        ie = (
                            '"status": "error"' in (pc or "")
                            or (pc or "").startswith("Error")
                        )
                        # Show the full result -- the user asked to see it all,
                        # and the panel scrolls.
                        d = ts if ts else (pc or "")
                        _pm_idx, _pm_arch = _idx(pm)
                        _draw_tool_inline(
                            work_box, tn, d, ie,
                            message_index=_pm_idx,
                            archived=_pm_arch,
                        )
                    elif pr == "compaction":
                        # Timeline marker: context was compressed here.
                        try:
                            _retired_n = int(pm.get("retired", 0) or 0)
                        except (TypeError, ValueError):
                            _retired_n = 0
                        _draw_archive_node(work_box, _retired_n, pc)
                    elif pr == "user":
                        work_box.label(text="Agent Context", icon="INFO")
                        _draw_multiline(work_box, pc)
                    elif pr == "assistant":
                        work_box.label(text="Self Prompt", icon="CONSOLE")
                        _draw_multiline(work_box, pc)

        # Background jobs (Tools 6): visible even while the Workshop is collapsed.
        if is_newest_turn:
            _draw_job_rows(turn_box)

        # --- Coworker reply: live while thinking, then the final message ---
        # While THIS turn is still being generated, show the live readout so
        # the user watches the answer form; only when the turn finishes is it
        # replaced by the final message.  Ordering matters: the active-turn
        # check comes FIRST so a mid-turn "final-looking" message (e.g. one
        # that triggers the end-of-turn execution nudge) cannot show a
        # conclusion while the turn is plainly still running.
        if is_active_turn:
            turn_box.separator()
            if not state.streaming_text and getattr(state, "turn_phase", ""):
                # Pre-first-token: show the activity phase ("Reading your
                # message" / "Dreaming, one moment") in the turn just sent.
                turn_box.label(
                    text="{:s} {:s}".format(_phase_text(state), _spinner_char(state)),
                    icon=_AGENT_ICON)
            else:
                # Live readout lives inside the ACTIVE turn box, visible even
                # while the Workshop is collapsed.
                turn_box.label(
                    text="Coworker (live) {:s}".format(_spinner_char(state)),
                    icon=_AGENT_ICON)
                _draw_multiline(turn_box, state.streaming_text)
        elif conclusion_msg:
            turn_box.separator()
            cr = turn_box.row()
            cr.label(text="* {:s}:".format(conclusion_msg.get("agent") or "Coworker"), icon=_AGENT_ICON)
            _copy(cr.operator("bfacw.copy_message", text="", icon="COPYDOWN"),
                  conclusion_msg)
            _render_markdown(turn_box, conclusion_msg.get("content", ""))


class BFACW_PT_chat_session(Panel):  # type: ignore[misc]
    """Session panel -- context usage, memory, compaction, and checkpoints."""
    bl_label = "Session"
    bl_idname = "BFACW_PT_chat_session"
    bl_options = {'DEFAULT_CLOSED'}
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_parent_id = "BFACW_PT_chat_status"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        # Hidden in External Harness and External Agent modes: session memory,
        # compaction, context use and checkpoints belong to Coworker's own turn
        # loop; there the external client / agent keeps its own conversation.
        if bpy.app.background:
            return False
        prefs = context.preferences.addons[__package__].preferences
        return prefs.operating_mode not in ("EXTERNAL_HARNESS", "EXTERNAL_AGENT")

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = _ui_state(context)      # this chat's thread's usage (#417)
        _draw_session_section(layout, context, props, state)


class BFACW_PT_chat_session_context(Panel):  # type: ignore[misc]
    """Session > Context -- how full the model's context window is."""
    bl_label = "Context"
    bl_idname = "BFACW_PT_chat_session_context"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_parent_id = "BFACW_PT_chat_session"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return BFACW_PT_chat_session.poll(context)

    def draw_header(self, context: bpy.types.Context) -> None:
        self.layout.label(text="", icon='MEMORY')

    def draw(self, context: bpy.types.Context) -> None:
        _draw_context_section(self.layout, agent_controller._agent_state)


class BFACW_PT_chat_session_memory(Panel):  # type: ignore[misc]
    """Session > Memory & Checkpoints -- summarized turns and restore points."""
    bl_label = "Memory & Checkpoints"
    bl_idname = "BFACW_PT_chat_session_memory"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_parent_id = "BFACW_PT_chat_session"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return BFACW_PT_chat_session.poll(context)

    def draw_header(self, context: bpy.types.Context) -> None:
        self.layout.label(text="", icon='BOOKMARKS')

    def draw(self, context: bpy.types.Context) -> None:
        _draw_memory_section(self.layout, context.window_manager.bfacw_chat_props)  # type: ignore[attr-defined]


class BFACW_PT_chat_plan(Panel):  # type: ignore[misc]
    """The goal and the step plan, with its progress: what Coworker (or your agent) is working through"""
    bl_label = "Plan"
    bl_idname = "BFACW_PT_chat_plan"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 1          # sidebar: under Queue, closed (#212)

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw_header(self, _context: bpy.types.Context) -> None:
        from . import session_memory as _sm
        with _sm.store_lock:
            done, total = _ui_store(None).goal.progress()
        row = self.layout.row(align=True)
        row.label(text="", icon='PINNED')
        if total:
            sub = row.row()
            sub.scale_x = 0.8
            sub.progress(factor=done / float(total), type='BAR', text="{:d}/{:d}".format(done, total))

    def draw(self, context: bpy.types.Context) -> None:
        _draw_plan_minimal(self.layout)


def _draw_plan_minimal(layout) -> None:
    """The Plan panel: the current goal, then the steps (#171).  Minimal: editing
    and notes stay in Edit Plan."""
    from . import session_memory as _sm
    with _sm.store_lock:
        g = _ui_store(None).goal
        goal = g.turn_goal or g.session_goal
        steps = [dict(s) for s in g.steps]
    if not (goal or steps):
        _draw_multiline(layout, "A multi-step request shows its plan here, and the steps tick off as "
                                "they're done.", icon='INFO')
        return
    if goal:
        _draw_multiline(layout, goal, icon='USER')
    icons = {"done": 'CHECKBOX_HLT', "doing": 'PLAY', "todo": 'CHECKBOX_DEHLT'}
    col = layout.column(align=True)
    for i, step in enumerate(steps, 1):
        row = col.row()
        row.label(text="", icon=icons.get(step.get("status"), 'CHECKBOX_DEHLT'))
        _draw_multiline(row, "{:d}. {:s}".format(i, step.get("text", "")))
    row = layout.row(align=True)
    row.scale_y = 0.9
    row.operator("bfacw.plan_open", text="Edit", icon='TEXT')
    row.operator("bfacw.plan_clear", text="Clear", icon='X')
    if any(s.get("status") != "done" for s in steps):
        go = layout.row()
        go.scale_y = 1.3
        go.operator("bfacw.plan_execute", text="Carry Out the Plan", icon='PLAY')
        props = bpy.context.window_manager.bfacw_chat_props
        if props.chat_mode == "PLAN":
            _draw_multiline(layout, "Planned. Carry it out here, or switch the mode above first (e.g. a "
                                    "local model or another agent): the plan stays in this chat.", icon='INFO')
    _draw_auto(layout)


class BFACW_OT_plan_execute(Operator):  # type: ignore[misc]
    """Carry out the plan now, step by step, with the engine that is on now (switch Local / Remote /
Agent first to use another one: the plan stays in this chat)"""
    bl_idname = "bfacw.plan_execute"
    bl_label = "Carry Out the Plan"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import session_memory as _sm
        with _sm.store_lock:
            pending = bool(_ui_store(context).goal.pending_steps())
        return pending and not _ui_state(context).is_thinking

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        with _sm.store_lock:
            goal = _ui_store(context).goal
            done, total = goal.progress()
            step = (goal.next_step() or {}).get("text", "")
        props = context.window_manager.bfacw_chat_props
        props.chat_mode = "AGENT"
        props.chat_input = ("Carry out the plan now ({:d}/{:d} done), step by step, starting with: {:s}. "
                            "Keep the plan updated as you finish each step.".format(done, total, step))
        return bpy.ops.bfacw.chat_send()


def _draw_auto(layout) -> None:
    """Auto mode (#177): the toggle, how it's going, or why it stopped (this chat's thread, #417)."""
    props = bpy.context.window_manager.bfacw_chat_props
    prefs = bpy.context.preferences.addons[__package__].preferences
    auto = _ui_thread().auto
    box = layout.box()
    box.prop(props, "chat_auto_mode", text="Auto: keep going until the plan is done", icon='TIME')
    run = auto["run"]
    if run is not None:
        minutes = int((time.time() - run.started) // 60)
        _draw_multiline(box, "{:d} extra turn{:s} so far, {:d} min. Stops at {:.0f} h or {:d} turns.".format(
            run.turns, "" if run.turns == 1 else "s", minutes, prefs.auto_max_hours, prefs.auto_max_turns))
    elif auto["last"]:
        _draw_multiline(box, auto["last"], icon='INFO')
    else:
        limits = box.row(align=True)
        limits.prop(prefs, "auto_max_hours", text="Hours")
        limits.prop(prefs, "auto_max_turns", text="Turns")


# Auto mode's state lives on each thread (#417): ``thread.auto`` holds the run while it's
# on, the last summary, a Stop you pressed; risky steps, questions and the External
# Agent's permission requests are answered for that thread only.

def _auto_toggle(thread, on: bool, context=None) -> None:
    from . import auto_mode, coworker_api, risky_steps, threads
    auto, tag = thread.auto, threads.tag(thread)
    if on and auto["run"] is None:
        prefs = (context or bpy.context).preferences.addons[__package__].preferences
        auto.update(run=auto_mode.AutoRun(max_seconds=prefs.auto_max_hours * 3600.0,
                                          max_turns=prefs.auto_max_turns), last="", user_stop=False)
        coworker_api.auto_answers[tag] = auto_mode.QUESTION_ANSWER
        risky_steps.auto_threads.add(tag)
        if _acp is not None:
            _acp.auto_threads.add(tag)
        print("[Coworker] Auto mode on ({:s})".format(_thread_title(thread)))
    elif not on and auto["run"] is not None:
        auto["run"].stopped = auto["run"].stopped or "you turned it off"
        _auto_finish(thread)


def _auto_finish(thread) -> None:
    """*thread*'s Auto stopped: say why, and stop answering for you."""
    from . import coworker_api, risky_steps, threads
    from . import session_memory as _sm
    auto, tag = thread.auto, threads.tag(thread)
    run = auto["run"]
    auto["run"] = None
    coworker_api.auto_answers.pop(tag, None)
    risky_steps.auto_threads.discard(tag)
    if _acp is not None:
        _acp.auto_threads.discard(tag)
    if run is None:
        return
    with _sm.store_lock:
        done, total = agent_controller._memory(thread).store.goal.progress()
    auto["last"] = run.summary(done, total).replace("**", "")
    agent_controller.thread_state(thread).warning = auto["last"]
    print("[Coworker] " + auto["last"])
    _tag_chat_regions_redraw()


def _auto_after_turn(thread) -> None:
    """*thread*'s turn ended (main thread): continue, wait for the queue, or stop."""
    auto = thread.auto
    run = auto["run"]
    if run is None:
        return
    from . import auto_mode
    from . import session_memory as _sm
    state = agent_controller.thread_state(thread)
    with _sm.store_lock:
        goal = agent_controller._memory(thread).store.goal
        done, total = goal.progress()
        step = goal.next_step() or {}
    error = state.error if state.error and state.error != auto["err_before"] else ""
    cancelled = bool(auto.get("user_stop", False))
    auto["user_stop"] = False
    queued = (len(_acp.queue_items()) if _is_external_agent() and _acp is not None
              else agent_controller.thread_queue(thread).pending_count)
    action = run.after_turn(ok=not error, error=error, cancelled=cancelled, done=done, total=total,
                            queued=queued)
    if action == "stop":
        _auto_finish(thread)
    elif action == "continue":
        prompt = auto_mode.continue_prompt(str(step.get("text", "")), done, total)
        bpy.app.timers.register(lambda: _auto_send(thread, prompt), first_interval=1.5)


def _auto_send(thread, prompt: str) -> None:
    if thread.auto["run"] is None or agent_controller.thread_state(thread).is_thinking:
        return None
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm.windows else None)
    if window is None:
        return None
    with bpy.context.temp_override(window=window):   # window only: a screen is refused while maximized (#401)
        bpy.ops.bfacw.chat_send(thread_id=thread.id, text=prompt)
    return None


class BFACW_PT_chat_queue(Panel):  # type: ignore[misc]
    """Top-level queue panel -- shows pending queued messages."""
    bl_label = "Queue"
    bl_idname = "BFACW_PT_chat_queue"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_order = 0          # sidebar: first, open (#212)

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        # Hidden in External Harness mode: chat (and thus the message
        # queue) is handled entirely by the external MCP client.
        if bpy.app.background:
            return False
        prefs = context.preferences.addons[__package__].preferences
        return prefs.operating_mode != "EXTERNAL_HARNESS"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        state = _ui_state(context)      # the thread this chat shows (#358)
        queue_items = _queued_messages(context)

        if not queue_items:
            layout.label(text="Nothing waiting", icon='CHECKMARK')
            sub = layout.row()
            sub.scale_y = 0.75
            sub.label(text="Send while a turn runs to queue the next message.")
            return

        from .chat_history import relative_time
        layout.label(text="Waiting to send ({:d})".format(len(queue_items)), icon='FORWARD')
        hint = layout.column()
        hint.scale_y = 0.75
        _draw_multiline(hint, "They go one after another when the turn ends.")
        modes = {ident: (name, icon) for ident, name, _d, icon, _n in CHAT_MODE_ITEMS}
        for idx, item in enumerate(queue_items):
            msg = " ".join(str(item.get("message", "")).split())
            name, icon = modes.get(item.get("chat_mode", "AGENT"), ("Coworker", 'GHOST_ENABLED'))
            box = layout.box()
            head = box.row(align=True)
            meta = "#{:d}  {:s}".format(idx + 1, name)
            if item.get("queued_at"):
                meta += "  \u00b7  " + relative_time(float(item["queued_at"]))
            head.label(text=meta, icon=icon)
            if item.get("attachments"):         # an icon, not the word (#249)
                head.label(text="", icon='IMAGE_DATA')
            if idx > 0:
                op = head.operator("bfacw.queue_item", text="", icon='TRIA_UP', emboss=False)
                op.action, op.index = "NEXT", idx
            op = head.operator("bfacw.queue_item", text="", icon='GREASEPENCIL', emboss=False)
            op.action, op.index = "EDIT", idx
            op = head.operator("bfacw.queue_item", text="", icon='X', emboss=False)
            op.action, op.index = "DELETE", idx
            _draw_multiline(box, msg if len(msg) <= 240 else msg[:237] + "...")

        row = layout.row(align=True)
        if not state.is_thinking:
            op = row.operator("bfacw.queue_item", text="Send Next Now", icon='PLAY')
            op.action, op.index = "SEND", 0
        row.operator("bfacw.queue_clear", icon="TRASH", text="Clear All")


def _draw_window_link(layout: bpy.types.UILayout, prefs: "bpy.types.AddonPreferences") -> None:
    """Which Blender window this Coworker drives (issue #85).

    Several Blender / Bforartists windows can run Coworker at once; each one
    only ever drives its own scene.  Show that plainly, plus the ports this
    window really owns (they move when another window holds the defaults).
    """
    if not mcp_to_blender_server.is_running():
        return
    from . import session_identity
    from .shared import DEFAULT_BRIDGE_PORT
    me = session_identity.describe()
    bridge, mcp, _llm = effective_ports(prefs)
    box = layout.box()
    col = box.column(align=True)
    col.label(text="Linked to this window", icon='LINKED')
    col.label(text="{:s} - {:s}".format(me["app"], me["file"] or "unsaved file"))
    ports = "Bridge {:d}".format(bridge)
    if agent_controller._agent_state.mcp_server_running:
        ports += " - MCP {:d}".format(mcp)
    col.label(text=ports)
    configured = (prefs.bridge_port if getattr(prefs, "bridge_port", 0) > 0
                  else DEFAULT_BRIDGE_PORT + getattr(prefs, "port_offset", 0))
    if bridge != configured:
        sub = col.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="Port {:d} is used by another window,".format(configured), icon='INFO')
        sub.label(text="so this window moved to {:d}.".format(bridge))
    others = session_identity.other_live_sessions()
    if others:
        sub = col.column(align=True)
        sub.scale_y = 0.8
        sub.label(
            text="{:d} other Coworker window{:s} open".format(len(others), "" if len(others) == 1 else "s"),
            icon='WINDOW')
        for other in others[:3]:
            sub.label(text="  {:s} - {:s} (bridge {:d})".format(
                str(other.get("app", "")), str(other.get("file", "") or "unsaved file"),
                int(other.get("bridge_port", 0) or 0)))
        sub.label(text="Each window only edits its own scene.")


class BFACW_PT_chat_status(Panel):  # type: ignore[misc]
    """Status sub-panel -- health dots, model info, tools, advanced diagnostics."""
    bl_label = "Status & Diagnostics"
    bl_idname = "BFACW_PT_chat_status"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 4

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state
        prefs = context.preferences.addons[__package__].preferences
        is_harness = (prefs.operating_mode == "EXTERNAL_HARNESS")

        # -- Which window this Coworker drives (#85) --
        _draw_window_link(layout, prefs)

        # -- Liveness dots --
        if not is_harness and state.mcp_server_running:
            agent_controller._check_liveness()
            liveness_row = layout.row(align=True)
            liveness_row.label(
                text="Bridge: {:s}".format("\u25cf" if state.bridge_live else "\u25cb"),
                icon='NETWORK_DRIVE',
            )
            liveness_row.label(
                text="MCP: {:s}".format("\u25cf" if state.mcp_live else "\u25cb"),
                icon='SETTINGS',
            )
            liveness_row.label(
                text="LLM: {:s}".format("\u25cf" if state.llm_live else "\u25cb"),
                icon='CONSOLE',
            )
            layout.separator()

        # -- Restart button --
        if state.mcp_server_running or (is_harness and mcp_to_blender_server.is_running()):
            restart_row = layout.row()
            restart_row.scale_y = 0.8
            restart_row.operator("bfacw.agent_restart", icon="LOOP_BACK", text="Restart Coworker")

        # -- Mode indicator --
        external = prefs.operating_mode == "EXTERNAL_AGENT"
        if not is_harness:
            from .shared import OPERATING_MODE_ITEMS
            name, icon = next(((i[1], i[3]) for i in OPERATING_MODE_ITEMS if i[0] == prefs.operating_mode),
                              ("Local LLM", 'CONSOLE'))
            if external:
                preset = _selected_agent_preset(prefs)
                if preset is not None:
                    name = "{:s} ({:s})".format(name, preset.name)
            layout.row(align=True).label(text="Mode: " + name, icon=icon)

        # -- Tool count (our own turn loop's; the agent lists its tools itself) --
        if not is_harness and not external and state.mcp_server_running:
            if state.tool_count > 0:
                layout.label(text="Tools: {:d} loaded".format(state.tool_count), icon='MODIFIER')
            else:
                layout.label(
                    text="Tools: none loaded",
                    icon=safe_icon('WARNING'),
                )

        # -- LLM info --
        if not is_harness:
            if not external:        # the local / remote model: not what the External Agent runs
                llm_state = llm_manager.get_state()
                if llm_state.is_running:
                    _draw_multiline(layout, "Model: {:s}".format(llm_state.model_name or "Local LLM"))
                elif llm_state.download_active and llm_state.download_kind == "model":
                    prog_row = layout.row()
                    prog_row.scale_y = 0.6
                    pct = llm_state.download_progress_pct
                    if pct > 0:
                        prog_row.progress(factor=pct / 100.0, type='BAR')
                    else:
                        prog_row.label(text="Loading model...", icon='SORTTIME')
                llm_cfg = llm_manager.get_config()
                if llm_cfg.mode == "remote" and llm_cfg.remote_model:
                    _draw_multiline(layout, "Model: {:s}".format(llm_cfg.remote_model))

            # -- Token usage (issue #69) --
            # Both llama-server and OpenAI-compatible remote APIs report a
            # usage object per request.  Show turn + session totals so the
            # user can see what the chat is consuming.
            _usage = _ui_state(context).session_usage
            if _usage:
                _turn_usage = _ui_state(context).turn_usage
                _turn_tot = _turn_usage.get("total_tokens", 0)
                _sess_p = _usage.get("prompt_tokens", 0)
                _sess_c = _usage.get("completion_tokens", 0)
                _sess_tot = _usage.get("total_tokens", 0)
                _usage_text = "Tokens: {:d}+{:d}={:d}".format(_sess_p, _sess_c, _sess_tot)
                if state.is_thinking and _turn_tot > 0:
                    _usage_text += " (turn: {:d})".format(_turn_tot)
                _draw_multiline(layout, _usage_text)

            # -- Speed (llama-server timings) --
            # Per-request prompt-eval vs generation throughput.  Prompt tok/s
            # collapsing means the prompt is too big; gen tok/s collapsing
            # means the model is over-reasoning.  Only llama-server reports it.
            _timings = _ui_state(context).last_timings
            if _timings:
                _pp = float(_timings.get("prompt_per_second", 0.0) or 0.0)
                _tp = float(_timings.get("predicted_per_second", 0.0) or 0.0)
                if _pp > 0 or _tp > 0:
                    _draw_multiline(
                        layout,
                        "Speed: {:.0f} tok/s prompt, {:.0f} tok/s gen".format(_pp, _tp),
                    )

            # -- Last turn cost (Tier 3i) --
            # Prefill vs generation vs tool/loop overhead for the previous
            # turn, so a slow turn can be attributed at a glance.
            _cost = _ui_state(context).last_turn_cost
            if _cost and _cost.get("requests"):
                _wall = float(_cost.get("wall", 0.0) or 0.0)
                _p_s = float(_cost.get("prompt_ms", 0.0)) / 1000.0
                _g_s = float(_cost.get("predicted_ms", 0.0)) / 1000.0
                _draw_multiline(
                    layout,
                    "Last turn: {:s} wall | gen {:d} tok/{:s}, "
                    "prefill {:d} tok/{:s} | {:d} tools".format(
                        _fmt_duration(_wall),
                        int(_cost.get("predicted_n", 0)), _fmt_duration(_g_s),
                        int(_cost.get("prompt_n", 0)), _fmt_duration(_p_s),
                        int(_cost.get("tools", 0)),
                    ),
                )

        # -- Export/Copy Log (advanced) --
        if not is_harness:
            layout.separator()
            row = layout.row(align=True)
            row.scale_y = 1.2
            row.operator("bfacw.export_session_log", icon="EXPORT", text="Export Log")
            row.operator("bfacw.copy_session_log", icon="COPYDOWN", text="Copy Log")

        # -- The GPU chat's draw trace (#387): on, then copy it right after a glitch --
        from . import chat_editor as _ce
        if _compact_sidebar(context) or _ce.is_coworker_space(context.space_data):
            layout.separator()
            row = layout.row(align=True)
            row.prop(prefs, "chat_draw_trace", text="Chat Draw Trace", toggle=True,
                     icon=safe_icon("REC") if prefs.chat_draw_trace else safe_icon("RADIOBUT_OFF"))
            row.operator("bfacw.copy_chat_draw_trace", icon=safe_icon("COPYDOWN"), text="Copy")

        # -- External Harness MCP server controls --
        if is_harness and mcp_to_blender_server.is_running():
            box = layout.box()
            box.label(text="MCP Server Mode:", icon='SETTINGS')
            box.prop(prefs, "mcp_server_mode", expand=True)

            if prefs.mcp_server_mode == "NETWORK":
                box.prop(prefs, "mcp_server_host")
                row = box.row(align=True)
                row.prop(prefs, "mcp_server_port_override")
                if prefs.mcp_server_host not in ("127.0.0.1", "localhost", "::1"):
                    box.label(
                        text="\u26a0 Non-localhost exposes MCP to network!",
                        icon='ERROR',
                    )
                row = box.row(align=True)
                if agent_controller._agent_state.mcp_server_running:
                    row.operator("bfacw.mcp_server_stop", icon="CANCEL", text="Stop MCP Server")
                else:
                    row.operator("bfacw.mcp_server_start", icon="PLAY", text="Start MCP Server")


class BFACW_PT_chat_text_editor(Panel):  # type: ignore[misc]
    """Chat panel in the Text Editor sidebar."""
    bl_label = "Coworker Chat"
    bl_idname = "BFACW_PT_chat_text_editor"
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background and not _compact_sidebar(context)   # the compact chat's place (#291)

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = _ui_state(context)      # the thread this chat shows (#358)
        prefs = context.preferences.addons[__package__].preferences
        is_harness = (prefs.operating_mode == "EXTERNAL_HARNESS")
        if not is_harness:
            draw_chat_tabs(layout, context)     # Threads 2c (#427)

        # Status bar.
        row = layout.row(align=True)
        if is_harness:
            if mcp_to_blender_server.is_running():
                row.operator("bfacw.agent_stop", icon="X", text="Stop Bridge")
                row.label(text="Bridge Running", icon='CHECKMARK')
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start Bridge")
                row.label(text="Bridge Stopped", icon='X')
        else:
            if agent_controller._agent_state.mcp_server_running:     # service status: shared
                row.operator("bfacw.agent_stop", icon="X", text="Stop")
                row.label(text="Running", icon='CHECKMARK')
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start")
                row.label(text="Stopped", icon='X')

        layout.separator()

        if not is_harness:
            # Agent/Ask mode toggle (Tier 1).
            row = layout.row(align=True)
            row.prop(props, "chat_mode", expand=True)
            _draw_reasoning_effort_row(layout, prefs)

            # Project Rules button (Tier 2).
            row = layout.row(align=True)
            row.operator("bfacw.edit_rules", icon="TEXT", text="Edit Rules")

            layout.separator()

            # -- Image attachment socket (issue #88 / Tier 3k) ------
            # Above the chat input (same order as the Viewport panel).
            _draw_attachment_row(layout, props, context)

            # Input (multi-line textbox).
            layout.textbox(props, "chat_input")

            row = layout.row(align=True)
            row.scale_y = 1.5
            if state.is_thinking:
                row.operator("bfacw.chat_stop", icon="PAUSE", text="Stop")
            else:
                row.operator("bfacw.chat_send", icon="PLAY", text="Send")
        else:
            layout.label(text="Chat handled by external MCP client.", icon='INFO')

        layout.separator()

        # Conversation summary -- latest message first.
        history = state.conversation_history
        # Prefix retired turns so the count reflects the WHOLE conversation;
        # the last-10 preview still draws from the tail.  Mirrors the main
        # panel's "Archived context" so nothing appears to vanish.
        try:
            from . import session_memory as _sm
            _combined = list(_ui_store(context).retired_history) + list(history)
        except Exception:  # pylint: disable=broad-exception-caught
            _combined = list(history)
        display_history = [m for m in _combined if m.get("role") != "system"]
        if display_history:
            box = layout.box()
            box.label(text="History ({:d} messages)".format(len(display_history)), icon='TEXT')
            for msg in reversed(display_history[-10:]):
                role = msg.get("role", "")
                content = msg.get("content", "")
                summary = msg.get("summary", "")
                if role == "reasoning":
                    preview = "Thinking... ({:d} chars)".format(len(content or ""))
                elif role == "tool":
                    display = summary if summary else (content or "")
                    preview = display[:80] + "..." if display and len(display) > 80 else (display or "")
                elif role == "compaction":
                    try:
                        _rn = int(msg.get("retired", 0) or 0)
                    except (TypeError, ValueError):
                        _rn = 0
                    preview = "checkpoint: {:d} message(s) summarized".format(_rn)
                else:
                    preview = content if content else ""
                _draw_multiline(box, "[{:s}] {:s}".format(role, preview))
        else:
            layout.label(text="No conversation yet.", icon='INFO')


# ---------------------------------------------------------------------------
# Helpers


# The three modes where you chat in Coworker, then the one where you chat elsewhere (#192).
_INTEGRATED_MODES = ("LOCAL_LLM", "REMOTE_API", "EXTERNAL_AGENT")
# Short names for the switcher in narrow sidebars (the tooltips keep the full ones).
_SHORT_MODE = {"LOCAL_LLM": "Local", "REMOTE_API": "Remote", "EXTERNAL_AGENT": "Agent",
               "EXTERNAL_HARNESS": "Harness"}


def draw_mode_switcher(layout, prefs, icons_only: bool = False, full_names: bool = False):
    """Local / Remote / External Agent | External Harness, one click each (#193).
    Returns the row (callers disable it while something runs)."""
    row = layout.row(align=True)
    group = row.row(align=True)
    def label(ident: str) -> dict:
        if full_names:
            return {}
        if icons_only and ident != prefs.operating_mode:    # narrow: only the active one is named
            return {"text": ""}
        return {"text": _SHORT_MODE[ident]}
    for ident in _INTEGRATED_MODES:
        group.prop_enum(prefs, "operating_mode", ident, **label(ident))
    row.separator(factor=0.6)
    row.prop_enum(prefs, "operating_mode", "EXTERNAL_HARNESS", **label("EXTERNAL_HARNESS"))
    return row


def _mode_locked(prefs) -> bool:
    """Coworker is started in its mode, so the mode stays put until Stop.  The bridge
    alone doesn't count (#458): every mode shares it and it runs without the agent
    (Auto Start, a harness check, an agent that was stopped), which greyed the switch
    out with the agent off.  In External Harness the bridge is what Start starts."""
    if agent_controller._agent_state.mcp_server_running:
        return True
    if _acp is not None and (_acp.connected or _acp.connecting):
        return True
    return prefs.operating_mode == "EXTERNAL_HARNESS" and mcp_to_blender_server.is_running()


class BFACW_PT_mode_popover(Panel):  # type: ignore[misc]
    """The mode and its model, from the chat's mode button: the Coworker panel's rows (#235)"""
    bl_label = "Mode"
    bl_idname = "BFACW_PT_mode_popover"
    bl_space_type = 'TOPBAR'          # only shown as a popover
    bl_region_type = 'HEADER'
    bl_ui_units_x = 14

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        prefs = context.preferences.addons[__package__].preferences
        running = _mode_locked(prefs)
        row = draw_mode_switcher(layout, prefs)
        row.enabled = not running
        if running:
            hint = layout.row()
            hint.scale_y = 0.75
            hint.label(text="Stop Coworker to switch modes", icon='INFO')
        mode = prefs.operating_mode
        if mode == "EXTERNAL_AGENT":
            if _acp is not None and _acp.connected:
                _draw_agent_pickers(layout)
            else:
                layout.prop(prefs, "external_agent_preset", text="")
        elif mode == "EXTERNAL_HARNESS":
            row = layout.row(align=True)
            row.prop(prefs, "harness_preset", text="")
            op = row.operator("bfacw.copy_mcp_config", icon="COPYDOWN", text="Copy Config")
            op.client_type = prefs.harness_preset
        else:
            _draw_engine_options(layout, context, prefs)
        layout.separator()
        layout.operator("bfacw.open_addon_prefs", text="All Settings...", icon='PREFERENCES')


def _draw_attachment_row(layout, props, context, minimal: bool = False) -> None:
    """Draw the chat image socket + capture buttons (issue #88 / Tier 3k).

    Shared by the 3D Viewport chat panel and the Text Editor panel:
    one Blender-standard socket (``template_ID``: browse / new) and our
    own Open (``bfacw.chat_attach_image``, #449), fed by every attach
    source -- the file browser, render capture, screen capture, and
    drag-and-drop -- plus the Send Once
    toggle and a thumbnail preview of what is attached.  The socket is
    encoded on the main thread at send time (``_capture_chat_attachment``),
    never during drawing.

    The section collapses from its own header with the same
    ``DOWNARROW_HLT`` / ``TRIA_RIGHT`` arrow pair Blender uses for its
    sub-panels, so the image can be tucked away while you read the chat.
    Collapsed, the header still names the attached datablock, so what will
    be sent stays visible.
    """
    expanded = getattr(props, "chat_image_expanded", True)
    img = props.chat_image
    box = layout.box()
    thread = _ui_thread(context)
    if thread.id != _socket().focus:    # the socket holds another thread's image (#417)
        mine = _thread_image(thread)
        row = box.row(align=True)
        row.label(text=mine.name if mine is not None else "No image attached", icon='IMAGE_DATA')
        row.operator("bfacw.thread_focus", text="Change", icon='GREASEPENCIL')
        return
    head = box.row(align=True)
    head.prop(props, "chat_image_expanded",
              icon='DOWNARROW_HLT' if expanded else 'TRIA_RIGHT',
              text="Image")
    if img is not None and not expanded:
        head.label(text=img.name, icon='IMAGE_DATA')
    if not expanded:
        return
    if minimal:
        # The Coworker editor (#171): pick an image and see it.  Render / Screen
        # are in the toolshelf's Attach panel, and the chat's tray has the pin.
        _draw_image_picker(box, props, thread.id)
        _draw_attachment_preview(box, img, context, max_units=8.0)
        return
    head.prop(props, "chat_image_send_once", text="Send once")
    _draw_image_picker(box, props, thread.id, new="image.new")
    _draw_attachment_preview(box, img, context, max_units=8.0)
    row = box.row(align=True)
    row.operator("bfacw.chat_capture_render", icon='RENDER_STILL', text="Render")
    row.operator("bfacw.chat_capture_screen", icon=safe_icon('MAKE_SCREENSHOT'), text="Screen")


def _ensure_attachment_preview(img) -> None:
    """Start generating *img*'s preview so the thumbnail can be drawn.

    ``preview_ensure()`` only *creates* the preview; its pixels are
    filled by Blender's preview thread a moment later, which is why
    this is called when the image is ATTACHED rather than only from the
    panel draw -- by the first redraw the thumbnail is usually ready.
    Best-effort: an image without pixels must not break attaching, and
    in ``--background`` mode no preview icon is produced at all (the
    draw helper simply draws nothing).
    """
    if img is None:
        return
    try:
        img.preview_ensure()
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return


# Our own preview buffer for the attachment thumbnail.  Module-level
# because a preview collection has to outlive a single panel draw; the
# pixels are only recomposed when the target size (or image) changes.
#
# A private bpy.utils.previews collection is used rather than the
# datablock's own preview: Blender regenerates an image's preview from
# the file (128 x 128) and would throw our composite away, and writing to
# the datablock also perturbs every other UI that shows its icon.
_ATTACHMENT_THUMB = {"collection": None, "preview": None, "key": None,
                     "icon": 0}


def _thumbnail_collection():
    """Lazily create the private ``bpy.utils.previews`` collection."""
    if _ATTACHMENT_THUMB["collection"] is None:
        try:
            from bpy.utils import previews as _previews
            _ATTACHMENT_THUMB["collection"] = _previews.new()
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            _ATTACHMENT_THUMB["collection"] = None
    return _ATTACHMENT_THUMB["collection"]


def free_attachment_thumbnail() -> None:
    """Release the private preview buffer (called from unregister)."""
    state = _ATTACHMENT_THUMB
    collection = state["collection"]
    state.update({"collection": None, "preview": None, "key": None, "icon": 0})
    if collection is None:
        return
    try:
        from bpy.utils import previews as _previews
        _previews.remove(collection)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        pass


def _attachment_preview_px(img, context) -> tuple[int, int]:
    """Edge length of the square thumbnail buffer, as ``(side, side)``.

    Sized from the panel's live width so the thumbnail spans it (see the
    sizing note near ``_ATTACHMENT_PREVIEW_MARGIN_PX`` for why the buffer
    is square rather than the image's own shape).  Falls back to
    ``(0, 0)`` when the image has no usable pixel size.
    """
    try:
        src_w, src_h = int(img.size[0]), int(img.size[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return 0, 0
    if src_w <= 0 or src_h <= 0:
        return 0, 0
    try:
        region_width = int(getattr(context.region, "width", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        region_width = 0
    available = max(region_width - _ATTACHMENT_PREVIEW_MARGIN_PX,
                    _ATTACHMENT_PREVIEW_MIN_PX)
    side = min(available, _ATTACHMENT_PREVIEW_MAX_PX)
    quantum = _ATTACHMENT_PREVIEW_QUANTUM_PX
    if quantum > 1:
        side = max(_ATTACHMENT_PREVIEW_MIN_PX,
                   round(float(side) / quantum) * quantum)
    return side, side


def _attachment_fit_size(src_w, src_h, side) -> tuple[int, int]:
    """Pixel size that FITS an image into a ``side`` square (aspect kept)."""
    if src_w >= src_h:
        return side, max(1, round(side * src_h / src_w))
    return max(1, round(side * src_w / src_h)), side


def _attachment_cover_size(src_w, src_h, side) -> tuple[int, int]:
    """Pixel size that COVERS a ``side`` square (the other axis overflows)."""
    if src_w >= src_h:
        return max(side, round(side * src_w / src_h)), side
    return side, max(side, round(side * src_h / src_w))


def _attachment_source_floats(img):
    """``(floats, width, height)`` for *img*'s pixels, or None.

    Read through ``foreach_get`` into a byte-packed ``array('f')`` so a
    large image does not become millions of Python float objects.
    Images above ``_ATTACHMENT_THUMBNAIL_MAX_SOURCE_PX`` are refused: the
    copy needs two float buffers, and a thumbnail is not worth hundreds
    of megabytes (those fall back to Blender's own small preview icon).
    """
    try:
        src_w, src_h = int(img.size[0]), int(img.size[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    if src_w <= 0 or src_h <= 0:
        return None
    if src_w * src_h > _ATTACHMENT_THUMBNAIL_MAX_SOURCE_PX:
        return None
    try:
        buf = array.array("f", [0.0]) * (src_w * src_h * 4)
        img.pixels.foreach_get(buf)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return None
    return buf, src_w, src_h


def _attachment_scale_pixels(img, width, height, *, blur=False, dim=None):
    """*img* resampled to ``width`` x ``height``; its RGBA floats or None.

    The source pixels are copied INTO a scratch image and resampled
    there.  The copy is explicit -- ``Image.copy()`` comes back all black
    for a generated image, because the copy is regenerated rather than
    duplicated -- and the user's datablock is never modified.  ``blur``
    softens the result with a down/up resample round trip and ``dim``
    multiplies the colour channels and forces the copy opaque; together
    they make the muted backdrop of ``_attachment_thumbnail_pixels``.
    Returns ``None``, never raising (this runs from a panel draw), when
    the pixels cannot be read.
    """
    source = _attachment_source_floats(img)
    if source is None:
        return None
    buf, src_w, src_h = source
    scratch = None
    try:
        scratch = bpy.data.images.new("bfacw_thumbnail_scratch", src_w, src_h,
                                      alpha=True)
        scratch.pixels.foreach_set(buf)
        del buf
        scratch.scale(width, height)
        if blur and width > 8 and height > 8:
            scratch.scale(max(1, width // 8), max(1, height // 8))
            scratch.scale(width, height)
        pixels = list(scratch.pixels)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return None
    finally:
        if scratch is not None:
            try:
                bpy.data.images.remove(scratch)
            except Exception:  # noqa: BLE001
                pass
    needed = width * height * 4
    if len(pixels) < needed:
        return None
    pixels = pixels[:needed]
    if dim is not None:
        for channel in (0, 1, 2):
            pixels[channel::4] = [value * dim for value in pixels[channel::4]]
        pixels[3::4] = [1.0] * (width * height)
    return pixels


def _attachment_thumbnail_pixels(img, side):
    """A ``side`` x ``side`` RGBA thumbnail of *img*, or None if unreadable.

    The whole image is fitted into the middle of the square, over a
    dimmed, blurred, cover-scaled copy of itself.  Blender draws a preview
    icon into a square rect, so a wide image fitted on its own would leave
    empty bands above and below it; filling them with a muted version of
    the same image keeps the thumbnail panel-wide and borderless while
    still showing the whole image.
    """
    try:
        src_w, src_h = int(img.size[0]), int(img.size[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    if src_w <= 0 or src_h <= 0 or side <= 0:
        return None
    cover_w, cover_h = _attachment_cover_size(src_w, src_h, side)
    fit_w, fit_h = _attachment_fit_size(src_w, src_h, side)
    backdrop = _attachment_scale_pixels(
        img, cover_w, cover_h, blur=True, dim=_ATTACHMENT_THUMBNAIL_DIM)
    front = _attachment_scale_pixels(img, fit_w, fit_h)
    if backdrop is None or front is None:
        return None
    x_off = (cover_w - side) // 2
    y_off = (cover_h - side) // 2
    fit_y0 = (side - fit_h) // 2
    fit_x0 = (side - fit_w) // 2
    row_floats = side * 4
    out = []
    for y in range(side):
        start = ((y + y_off) * cover_w + x_off) * 4
        row = backdrop[start:start + row_floats]
        if fit_y0 <= y < fit_y0 + fit_h:
            fy = y - fit_y0
            row = list(row)
            row[fit_x0 * 4:(fit_x0 + fit_w) * 4] = \
                front[fy * fit_w * 4:(fy + 1) * fit_w * 4]
        out.extend(row)
    return out


def _attachment_datablock_icon(img) -> int:
    """The image datablock's own preview icon (small, but always valid)."""
    try:
        preview = img.preview_ensure()
        return int(getattr(preview, "icon_id", 0) or 0)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return 0


def _attachment_preview_icon(img, context) -> tuple[int, int, int]:
    """``(icon_value, width, height)`` for the attachment thumbnail.

    Prefers a buffer we compose ourselves -- the image at full width over
    a muted backdrop, so the thumbnail is panel-wide and has no empty band
    above or below it -- because the datablock's stock icon is only 32px
    and looks very pixely once the thumbnail spans the panel.  Falls back
    to that stock icon, which Blender then scales, when the buffer cannot
    be built, and to ``(0, 0, 0)`` when there is nothing to draw.
    """
    width, height = _attachment_preview_px(img, context)
    if width <= 0 or height <= 0:
        return 0, 0, 0

    preview_state = _ATTACHMENT_THUMB
    key = None
    try:
        key = (img.name, width, height, tuple(img.size))
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        key = None
    if key is not None and preview_state["key"] == key and preview_state["icon"]:
        return preview_state["icon"], width, height

    collection = _thumbnail_collection()
    if collection is not None:
        try:
            preview = preview_state["preview"]
            if preview is None:
                preview = collection.new("attachment")
                preview_state["preview"] = preview
            pixels = _attachment_thumbnail_pixels(img, width)
            if pixels is not None:
                # Size first: clearing a buffer is what lets the dynamic
                # pixel array take our new dimensions.  Both buffers get
                # the same pixels because template_icon asks for
                # ICON_SIZE_ICON and only falls back to the big preview
                # when the small buffer is missing (interface_icons.cc
                # icon_draw_size) -- filling both means the thumbnail is
                # sharp whichever buffer Blender reaches for.
                preview.image_size = (width, height)
                preview.image_pixels_float[:] = pixels
                preview.icon_size = (width, height)
                preview.icon_pixels_float[:] = pixels
                icon = int(getattr(preview, "icon_id", 0) or 0)
                if icon:
                    preview_state.update({"key": key, "icon": icon})
                    return icon, width, height
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            pass

    return _attachment_datablock_icon(img), width, height


def _draw_attachment_preview(layout, img, context=None, max_units: float = 0.0) -> None:
    """Draw a thumbnail of the attached image (issue #88 / Tier 3k).

    A filename alone is easy to misread, so the user can see WHAT is
    attached before sending.  The button is sized from the panel's live
    width so the thumbnail spans it (see the sizing note near
    ``_ATTACHMENT_PREVIEW_MARGIN_PX``), and the pixels are our own
    resample of the image at exactly the size Blender draws them --
    without that, a template_icon shows the datablock's 32px icon and the
    thumbnail is very pixely.  Best-effort: a datablock with no pixels
    (or a stub image in tests) has no preview to make, and a panel draw
    must never raise.
    """
    if img is None:
        return
    icon, width, height = _attachment_preview_icon(img, context)
    if not icon or width <= 0 or height <= 0:
        return
    try:
        ui_scale = float(context.preferences.system.ui_scale) or 1.0
    except (AttributeError, TypeError, ValueError):
        ui_scale = 1.0
    unit = _UI_UNIT_BASE_PX * ui_scale
    # Drawn straight onto the panel layout, not inside a layout.row():
    # a row wrapper made the (deliberately large) icon button vanish --
    # measured in a real session, where the same call without the row
    # drew a 162x40 thumbnail and with it drew nothing at all.
    scale = max(1.0, width / unit)
    if max_units:           # a square preview the full width of a wide panel is very tall
        scale = min(scale, max_units)
    layout.template_icon(icon_value=icon, scale=scale)


def _redraw_areas(context: bpy.types.Context | None) -> None:
    """Force redraw of all panels."""
    if context and context.area:
        context.area.tag_redraw()


def _redraw_areas_safe() -> None:
    """Defer a panel redraw to the main thread via a timer.

    Safe to call from background threads where the operator's
    ``context`` may have been invalidated after ``execute()``
    returned.  Uses ``bpy.app.timers`` to access ``bpy.context``
    on the main thread where it is always valid.
    """
    bpy.app.timers.register(
        lambda: _redraw_areas(bpy.context),
        first_interval=0.0,
    )


# ---------------------------------------------------------------------------
# Session memory & checkpoint operators (Tier 3 Phase 5)

def _draw_session_section(layout, context, props, state) -> None:
    """Session panel header: one plain summary line.

    The details live in the sub-panels (Context, Memory & Checkpoints) so
    the user opens only what they care about.  Goal & Plan lives in the
    chat panel.
    """
    from . import session_memory as _sm
    ctx_size = getattr(state, "ctx_size_used", 0) or 0
    last_prompt = getattr(state, "last_prompt_tokens", 0) or 0
    parts = []
    if ctx_size > 0 and last_prompt > 0:
        parts.append("Context {:d}%".format(min(int(last_prompt * 100 / ctx_size), 100)))
    with _sm.store_lock:
        n_cp = len(_ui_store(context).checkpoints)
    if n_cp:
        parts.append("{:d} checkpoint{:s}".format(n_cp, "" if n_cp == 1 else "s"))
    layout.label(text="  \u00b7  ".join(parts) if parts else "New session", icon='INFO')


def _draw_context_section(layout, state) -> None:
    """Session > Context: how full the model's working memory is."""
    from . import session_memory as _sm
    # The LATEST request's prompt tokens (current occupancy), not a running
    # sum -- a sum only grows and would pin the bar at 100%.
    ctx_size = getattr(state, "ctx_size_used", 0) or 0
    last_prompt = getattr(state, "last_prompt_tokens", 0) or 0
    if ctx_size > 0 and last_prompt > 0:
        pct = min(int(last_prompt * 100 / ctx_size), 100)
        row = layout.row(align=True)
        row.progress(factor=pct / 100.0, type='BAR',
                     text="{:d}% of {:d}k tokens".format(pct, max(1, ctx_size // 1024)))
        if pct >= int(_sm.COMPACTION_TRIGGER_RATIO * 100):
            _draw_multiline(layout, "Getting full -- older turns will be summarized "
                                    "into memory automatically.", icon='INFO')
    else:
        layout.label(text="Fills up as you chat", icon='INFO')


def _draw_memory_section(layout, props) -> None:
    """Session > Memory & Checkpoints: what was summarized, and restore points."""
    from . import session_memory as _sm
    st = _ui_store(None)
    with _sm.store_lock:
        memory_block = st.memory_block
        checkpoints = st.list_checkpoints()

    # -- What the coworker remembers from summarized turns ------------
    lines = _humanize_memory(memory_block)
    if lines:
        box = layout.box()
        for ln in lines:
            if ln.endswith(":"):
                box.label(text=ln)
            else:
                _draw_multiline(box, ln.strip())
    else:
        _draw_multiline(layout, "Nothing summarized yet. When the context fills "
                                "up, older turns are summarized here and a "
                                "checkpoint is saved.", icon='INFO')

    row = layout.row(align=True)
    row.operator("bfacw.session_compact_now", icon='BOOKMARKS', text="Checkpoint Now")
    row.prop(props, "session_show_memory_editor", text="", icon='GREASEPENCIL',
             toggle=True)
    if props.session_show_memory_editor:
        # Raw note, for power users: what the model actually reads.
        if not props.session_memory_edit and memory_block:
            props.session_memory_edit = memory_block
        layout.textbox(props, "session_memory_edit",
                       placeholder="Memory note (empty = reload current)")
        layout.operator("bfacw.session_memory_view_edit", icon='CHECKMARK',
                        text="Apply Memory")

    # -- Restore points (newest first) ---------------------------------
    if checkpoints:
        cbox = layout.box()
        for i in range(len(checkpoints) - 1, -1, -1):
            title, detail = _checkpoint_title(checkpoints[i], i + 1)
            row = cbox.row(align=True)
            row.label(text=title, icon='BOOKMARKS')
            row.operator("bfacw.session_checkpoint_restore", text="",
                         icon='LOOP_BACK').index = i
            sub = cbox.row()
            sub.scale_y = 0.7
            sub.label(text="      " + detail)


class BFACW_OT_session_checkpoint_restore(Operator):  # type: ignore[misc]
    """Restore the selected checkpoint (the current session is auto-saved first)"""
    bl_idname = "bfacw.session_checkpoint_restore"
    bl_label = "Restore"
    bl_description = (
        "Go back to this checkpoint. The current conversation is saved as a "
        "checkpoint first, so nothing is lost"
    )

    index: IntProperty(default=-1, min=-1)  # type: ignore[valid-type]

    def invoke(self, context: bpy.types.Context, event) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        st = _ui_store(context)
        index = self.index if self.index >= 0 else \
            context.window_manager.bfacw_chat_props.session_checkpoint_index  # type: ignore[attr-defined]
        try:
            with _sm.store_lock:
                restored = st.restore(
                    index,
                    _ui_state(context).conversation_history,
                )
                _ui_state(context).conversation_history[:] = restored
        except IndexError:
            self.report({"WARNING"}, "Checkpoint no longer exists")
            return {"CANCELLED"}
        _save_thread_chat(_ui_thread(context))
        self.report({"INFO"}, "Checkpoint restored -- the previous state was saved as a checkpoint too")
        return {"FINISHED"}


class BFACW_OT_session_checkpoint_select(Operator):  # type: ignore[misc]
    """Choose a checkpoint as the restore target (radio button)"""
    bl_idname = "bfacw.session_checkpoint_select"
    bl_label = "Select Checkpoint"
    bl_description = "Choose this checkpoint as the restore target"

    index: IntProperty(default=0, min=0)  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> set[str]:
        context.window_manager.bfacw_chat_props.session_checkpoint_index = self.index  # type: ignore[attr-defined]
        return {"FINISHED"}


class BFACW_OT_session_memory_view_edit(Operator):  # type: ignore[misc]
    """View or edit the session memory block"""
    bl_idname = "bfacw.session_memory_view_edit"
    bl_label = "Apply Memory"
    bl_description = ("Write the edited session memory block to the store, or "
                      "reload it into the editor when the box is left empty")

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        props = context.window_manager.bfacw_chat_props  # type: ignore[attr-defined]
        text = props.session_memory_edit
        with _sm.store_lock:
            if text.strip():
                _ui_store(context).memory_block = text.strip()
                message = "Session memory updated"
            else:
                props.session_memory_edit = _ui_store(context).memory_block
                message = "Loaded current session memory into the editor"
        _save_chat_history()
        self.report({"INFO"}, message)
        return {"FINISHED"}


# Manual "Compact Now" keeps a larger recent window than the forced-overflow
# path but smaller than the automatic window, and always snapshots the
# pre-compaction state so the action is reversible.
_MANUAL_COMPACT_KEEP_RECENT = 8


class BFACW_OT_session_compact_now(Operator):  # type: ignore[misc]
    """Checkpoint now: save the session, then summarize older turns into memory"""
    bl_idname = "bfacw.session_compact_now"
    bl_label = "Checkpoint Now"
    bl_description = (
        "Save a checkpoint of the whole session, then summarize the oldest "
        "turns into the session memory to free context. The goal and plan "
        "are pinned and never summarized away. Restore the checkpoint from "
        "the list below at any time."
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        st = _ui_store(context)
        history = _ui_state(context).conversation_history
        # Only compact when there is genuinely something safe to retire: never
        # let a manual compaction reduce a young conversation to the system
        # prompt (find_retire_boundary returns len(history) when nothing is
        # retirable).
        boundary = _sm.find_retire_boundary(history, _MANUAL_COMPACT_KEEP_RECENT)
        if boundary >= len(history):
            self.report({"INFO"}, "Nothing to compact yet")
            return {"CANCELLED"}
        st.archive_path = _session_memory_archive_path()
        with _sm.store_lock:
            # Snapshot the PRE-compaction state so "Compact Now" is reversible.
            st.snapshot(history, reason="manual-compaction")
            kept, memory_block, retired = _sm.compact_history(
                history, prior_memory=st.memory_block,
                keep_recent=_MANUAL_COMPACT_KEEP_RECENT)
            st.memory_block = memory_block
            st.append_archive(retired)
            st.append_retired(retired)
            history[:] = kept
        _save_thread_chat(_ui_thread(context))
        self.report({"INFO"}, "Checkpoint saved -- {:d} older messages summarized".format(len(retired)))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Goal & plan (Tier 3 hardening)

def _draw_reasoning_effort_row(layout, prefs) -> None:
    """Compact Reasoning Effort selector for the chat panels (local mode).

    The thinking budget is sent with every request, so it is safe to change
    at any time -- including mid-session; it applies from the next message.
    Remote providers ignore it, so the row is shown for the local model only.
    """
    if getattr(prefs, "operating_mode", "") != "LOCAL_LLM":
        return
    if not hasattr(prefs, "reasoning_effort"):
        return
    row = layout.row(align=True)
    row.scale_y = 0.9
    row.label(text="Thinking", icon='SOLO_ON')
    row.prop(prefs, "reasoning_effort", text="")


def _draw_goal_plan_section(layout) -> None:
    """Draw the pinned goal and plan (body of the chat panel's Goal & Plan section)."""
    from . import session_memory as _sm
    with _sm.store_lock:
        g = _ui_store(None).goal
        session_goal = g.session_goal
        turn_goal = g.turn_goal
        steps = [dict(s) for s in g.steps]
        notes = g.user_notes
        done, total = g.progress()
    if not (session_goal or turn_goal or steps or notes):
        _draw_multiline(
            layout,
            "Set automatically from your first request. It is pinned to every "
            "request, so it survives checkpoints and long turns.",
            icon='INFO')
    else:
        box = layout.box()
        box.label(text="Session goal", icon='PINNED')
        _draw_user_text(box, session_goal or "(none)")
        # Same request (the goal is just clipped longer) -> show it once.
        if turn_goal and turn_goal != session_goal and turn_goal != \
                _sm.goal_plan._clip(session_goal, _sm.goal_plan._TURN_GOAL_CHARS):
            box.label(text="Current request", icon='USER')
            _draw_user_text(box, turn_goal)
        if steps:
            pbox = layout.box()
            row = pbox.row()
            row.label(text="Plan  {:d}/{:d} done".format(done, total), icon='PRESET')
            if total:
                row.progress(factor=done / float(total), type='BAR', text="")
            _icons = {"done": 'CHECKBOX_HLT', "doing": 'PLAY', "todo": 'CHECKBOX_DEHLT'}
            for i, s in enumerate(steps, 1):
                srow = pbox.row()
                srow.label(text="", icon=_icons.get(s.get("status"), 'CHECKBOX_DEHLT'))
                _draw_multiline(srow, "{:d}. {:s}".format(i, s.get("text", "")))
        if notes:
            nbox = layout.box()
            nbox.label(text="Your notes", icon='TEXT')
            _draw_multiline(nbox, notes)
    row = layout.row(align=True)
    row.operator("bfacw.plan_open", icon='TEXT', text="Edit in Text Editor")
    row.operator("bfacw.plan_clear", icon='X', text="Clear Plan")


def _draw_goal_plan_inline(layout) -> None:
    """Collapsible Goal & Plan section inside the chat panel.

    Hidden until there is a goal (no clutter on a fresh chat).  The header
    carries the progress so it is useful even collapsed.
    """
    from . import session_memory as _sm
    with _sm.store_lock:
        g = _ui_store(None).goal
        empty = g.is_empty()
        done, total = g.progress()
    if empty:
        return
    try:
        header, body = layout.panel("bfacw_goal_plan", default_closed=False)
    except Exception:  # pylint: disable=broad-exception-caught
        header, body = layout.box(), layout  # builds without layout panels
    hrow = header.row(align=True)
    hrow.label(text="Goal & Plan", icon='PINNED')
    if total:
        sub = hrow.row()
        sub.scale_x = 0.9
        sub.progress(factor=done / float(total), type='BAR',
                     text="{:d}/{:d}".format(done, total))
    if body:
        _draw_goal_plan_section(body)


# Last text written to (or adopted from) the plan text block.  A difference
# means the USER edited it -> parse back into the store.  ``None`` until the
# block is first seen this session.
_plan_last_written: str | None = None
_PLAN_TEXT: dict[str, str] = {"id": "main"}     # whose plan the text block shows (#417)


def _sync_plan_text() -> None:
    """Keep the ``Coworker Plan.md`` text block and the pinned plan in sync.

    Main thread only (called from the chat timer).  The block exists only
    after the user opened it once ("Edit in Text Editor"), so the add-on
    never adds a datablock to the user's file uninvited.  User edits win:
    when the text differs from what we last wrote, it is parsed into the
    store; otherwise store changes (new goal, plan progress) are written out.
    """
    global _plan_last_written
    from . import session_memory as _sm
    name = _sm.goal_plan.PLAN_TEXT_NAME
    text = bpy.data.texts.get(name)
    if text is None:
        _plan_last_written = None
        return
    current = text.as_string()
    store = agent_controller._memory(agent_controller.THREADS.get(_PLAN_TEXT["id"])).store
    with _sm.store_lock:
        if _plan_last_written is not None and current != _plan_last_written:
            # The user edited the file: adopt it.
            if store.goal.parse_markdown(current):
                print("[Coworker] plan: applied your edits from '{:s}'".format(name))
            _plan_last_written = current
            return
        rendered = store.goal.render_markdown()
    if rendered != current:
        text.clear()
        text.write(rendered)
        current = rendered
    _plan_last_written = current


class BFACW_OT_plan_open(Operator):  # type: ignore[misc]
    """Open the goal & plan in the Text Editor to read or edit it"""
    bl_idname = "bfacw.plan_open"
    bl_label = "Edit Plan"
    bl_description = (
        "Open the pinned goal & plan as an editable text ('Coworker Plan.md'). "
        "Edit the goal, steps ([ ] / [>] / [x]) or notes -- your changes are "
        "sent to the Coworker with its next request"
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _plan_last_written
        from . import session_memory as _sm
        name = _sm.goal_plan.PLAN_TEXT_NAME
        text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        _PLAN_TEXT["id"] = _ui_thread(context).id        # this chat's thread's plan (#417)
        with _sm.store_lock:
            rendered = _ui_store(context).goal.render_markdown()
        text.clear()
        text.write(rendered)
        _plan_last_written = rendered
        # write() leaves the cursor at the end, so the editor opened scrolled past the last
        # line and the plan looked empty (#272): back to the top.
        try:
            text.cursor_set(0)
        except (AttributeError, TypeError):
            text.current_line_index = 0
        # Show it: reuse an open Text Editor, else open one in a new window.
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == 'TEXT_EDITOR':
                    area.spaces.active.text = text
                    area.spaces.active.top = 0
                    area.tag_redraw()
                    self.report({"INFO"}, "Plan opened in the Text Editor")
                    return {"FINISHED"}
        try:
            bpy.ops.wm.window_new()
            new_win = context.window_manager.windows[-1]
            area = new_win.screen.areas[0]
            area.ui_type = 'TEXT_EDITOR'
            area.spaces.active.text = text
            area.spaces.active.top = 0
            self.report({"INFO"}, "Plan opened in a new Text Editor window")
        except Exception:  # pylint: disable=broad-exception-caught
            self.report({"INFO"}, "Plan saved as text '{:s}' -- open it in any "
                                  "Text Editor".format(name))
        return {"FINISHED"}


class BFACW_OT_plan_clear(Operator):  # type: ignore[misc]
    """Clear the step plan (the goals are kept)"""
    bl_idname = "bfacw.plan_clear"
    bl_label = "Clear Plan"
    bl_description = (
        "Remove the current step plan and your notes. The session goal and "
        "current request stay pinned"
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        with _sm.store_lock:
            g = _ui_store(context).goal
            g.steps = []
            g.user_notes = ""
            g.revision += 1
        _save_chat_history()
        self.report({"INFO"}, "Plan cleared")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Registration helpers

_classes = (
    ChatHistoryProperties,
    BFACW_OT_session_checkpoint_select,
    BFACW_OT_session_checkpoint_restore,
    BFACW_OT_session_memory_view_edit,
    BFACW_OT_session_compact_now,
    BFACW_OT_plan_open,
    BFACW_OT_plan_clear,
    BFACW_OT_chat_send,
    BFACW_OT_chat_clear,
    BFACW_OT_chat_stop,
    BFACW_OT_chat_queue_send,
    BFACW_OT_chat_capture_render,
    BFACW_OT_chat_capture_screen,
    BFACW_OT_chat_image_drop,
    BFACW_FH_chat_drop,
    BFACW_OT_export_session_log,
    BFACW_OT_copy_session_log,
    BFACW_OT_copy_status_error,
    BFACW_OT_queue_clear,
    BFACW_OT_queue_item,
    BFACW_OT_queue_show,
    BFACW_OT_mention_search,
    BFACW_OT_mention_insert,
    BFACW_OT_edit_rules,
    BFACW_OT_reload_rules,
    BFACW_OT_agent_start,
    BFACW_OT_agent_stop,
    BFACW_OT_external_agent_check,
    BFACW_OT_agent_option,
    BFACW_OT_external_agent_open_folder,
    BFACW_OT_external_agent_npm,
    BFACW_OT_agent_approve,
    BFACW_OT_agent_answer,
    BFACW_OT_revert_turn,
    BFACW_OT_agent_restart,
    BFACW_OT_copy_message,
    BFACW_OT_copy_code_block,

    BFACW_PT_chat_queue,
    BFACW_PT_chat_plan,
    BFACW_OT_plan_execute,
    BFACW_OT_plan_card,
    BFACW_PT_mode_popover,
    BFACW_PT_approval_popover,
    BFACW_PT_chat_history,
    BFACW_OT_chat_open,
    BFACW_OT_chat_where,
    BFACW_OT_chat_open_blend,
    BFACW_OT_chat_rename,
    BFACW_OT_chat_pin,
    BFACW_OT_chat_delete,
    BFACW_PT_chat_compact,
    BFACW_PT_chat_panel,
    BFACW_PT_chat_controls,
    BFACW_PT_attach_popover,
    BFACW_MT_chat_tabs,
    BFACW_OT_tab_show,
    BFACW_OT_tab_new,
    BFACW_OT_tab_close,
    BFACW_OT_thread_open_editor,
    BFACW_OT_thread_focus,
    BFACW_PT_chat_status,
    BFACW_PT_chat_session,
    BFACW_PT_chat_session_context,
    BFACW_PT_chat_session_memory,
    BFACW_PT_chat_text_editor,
)


def register() -> None:
    # Idempotent registration -- unregister old classes first if re-enabling.
    if hasattr(bpy.types.WindowManager, "bfacw_chat_props"):
        unregister()

    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bfacw_chat_props = bpy.props.PointerProperty(type=ChatHistoryProperties)  # type: ignore[attr-defined]
    # #195: the engines' options as real dropdowns / button rows.
    for category, prop in _OPTION_PROPS.items():
        setattr(bpy.types.WindowManager, prop, _make_option_prop(category))
    bpy.types.WindowManager.bfacw_remote_model = bpy.props.EnumProperty(  # type: ignore[attr-defined]
        name="Model", description="The remote model to use (refresh to fetch the list from your provider)",
        items=_remote_model_items, get=_remote_model_get, set=_remote_model_set)

    # Register the chat UI update timer.
    if not bpy.app.background:
        bpy.app.timers.register(chat_timer_update, first_interval=1.0, persistent=True)
    if _threads_on_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_threads_on_load)
    if _shot_on_load_pre not in bpy.app.handlers.load_pre:      # no capture outlives a file (#454)
        bpy.app.handlers.load_pre.append(_shot_on_load_pre)
    if _session_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_session_on_load)
    if _threads_on_save not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(_threads_on_save)


def unregister() -> None:
    # Save history -- unless it's already been emptied (add-on cleanup on quit):
    # never overwrite the saved chat with nothing.
    if _has_user_messages():
        _save_chat_history()
    for _thread in agent_controller.THREADS.live():        # each editor's chat too (#358)
        if not _thread.is_default:
            _save_thread_chat(_thread)
    if _threads_on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_threads_on_load)
    # A capture running now ends with the add-on (#454): its draw handlers would outlive it.
    if _shot_on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_shot_on_load_pre)
    try:
        _shot_end_all()
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] capture cleanup skipped -- {:s}".format(str(ex)))
    if _session_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_session_on_load)
    if _threads_on_save in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_threads_on_save)

    # The bridge stops with the add-on (__init__): close the External Agent too, or the chat
    # says "Connected" after re-enabling while its tools can't reach Blender.
    if _acp is not None and (_acp.connected or _acp.connecting):
        try:
            _acp.disconnect()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # Drop the thumbnail's private preview buffer.
    free_attachment_thumbnail()

    if bpy.app.timers.is_registered(chat_timer_update):
        bpy.app.timers.unregister(chat_timer_update)

    if hasattr(bpy.types.WindowManager, "bfacw_chat_props"):
        del bpy.types.WindowManager.bfacw_chat_props  # type: ignore[attr-defined]
    for prop in (*_OPTION_PROPS.values(), "bfacw_remote_model"):
        if hasattr(bpy.types.WindowManager, prop):
            delattr(bpy.types.WindowManager, prop)
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
