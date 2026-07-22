# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Blender Chat Panel — provides an in-Blender chat interface to the MCP agent.

Registers a ``VIEW_3D`` sidebar panel with conversation history, multi-line
input, send/clear/stop buttons, and a status bar.

Also registers a Text Editor side panel for prompt-based interaction.
"""

__all__ = (
    "ChatHistoryProperties",
    "BLMCP_PT_chat_panel",
    "BLMCP_PT_chat_text_editor",
    "BLMCP_OT_chat_send",
    "BLMCP_OT_chat_clear",
    "BLMCP_OT_chat_stop",
    "BLMCP_OT_agent_start",
    "BLMCP_OT_agent_stop",
    "chat_timer_update",
    "register",
    "unregister",
)

import json
import os
from pathlib import Path

import bpy  # pylint: disable=import-error
from bpy.props import (  # pylint: disable=import-error
    StringProperty,
)
from bpy.types import (  # pylint: disable=import-error
    Operator,
    Panel,
    PropertyGroup,
)

import textwrap

from . import agent_controller
from . import llm_manager
from . import mcp_to_blender_server
from .shared import effective_ports


_WRAP_WIDTH = 60


def _wrap_text(text: str, width: int = _WRAP_WIDTH) -> str:
    """Wrap text to a given width for display in Blender labels."""
    if not text:
        return ""
    return "\n".join(
        textwrap.fill(line, width=width)
        for line in text.split("\n")
    )


# ---------------------------------------------------------------------------
# Properties

def _chat_history_dir() -> Path:
    """Return the directory where chat history JSON files are stored."""
    base = Path(bpy.utils.user_resource("SCRIPTS")) / "blender_mcp_chat_history"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _chat_history_path() -> Path:
    """Return the path to the chat history JSON file for the current blend file."""
    session_name = "default"
    if bpy.data.filepath:
        session_name = Path(bpy.data.filepath).stem
    return _chat_history_dir() / "{:s}.json".format(session_name)


class ChatHistoryProperties(PropertyGroup):  # type: ignore[misc]
    """Persistent chat history properties stored on the WindowManager."""

    chat_input: StringProperty(  # type: ignore[valid-type]
        name="Input",
        description="Type your message for the MCP agent",
        default="",
    )

    chat_status: StringProperty(  # type: ignore[valid-type]
        name="Status",
        default="Idle",
    )


def _load_chat_history() -> list[dict]:
    """Load conversation history from disk."""
    path = _chat_history_path()
    if path.exists():
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_chat_history() -> None:
    """Save conversation history to disk."""
    path = _chat_history_path()
    try:
        with open(str(path), "w", encoding="utf-8") as fh:
            json.dump(agent_controller._agent_state.conversation_history, fh, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Operators

class BLMCP_OT_chat_send(Operator):  # type: ignore[misc]
    """Send the current input to the MCP agent."""
    bl_idname = "blmcp.chat_send"
    bl_label = "Send"
    bl_description = "Send your message to the MCP agent"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.blmcp_chat_props  # type: ignore[attr-defined]
        message = props.chat_input.strip()
        if not message:
            return {"CANCELLED"}

        if not agent_controller._agent_state.mcp_server_running:
            self.report({"ERROR"}, "Agent is not running. Start it from Preferences or the Chat panel.")
            return {"CANCELLED"}

        # Clear input.
        props.chat_input = ""
        props.chat_status = "Thinking..."

        # Get LLM config.
        llm_cfg = llm_manager.get_config()
        llm_url = None
        api_key = None
        model = None
        if llm_cfg.mode == "remote":
            llm_url = llm_cfg.remote_api_url
            api_key = llm_cfg.remote_api_key
            model = llm_cfg.remote_model or None

        # Run the conversation turn in a background thread.
        import threading

        # Get effective ports from preferences.
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)

        def _do_turn():
            try:
                agent_controller.run_conversation_turn(
                    user_message=message,
                    on_text=None,
                    on_status=lambda s: _update_status(s),
                    llm_url=llm_url or None,
                    api_key=api_key or None,
                    model=model,
                    mcp_port=_mcp_port,
                )
            except Exception as ex:  # pylint: disable=broad-exception-caught
                agent_controller._agent_state.error = str(ex)
            finally:
                _save_chat_history()
                _update_status("Idle")
                _redraw_areas(context)

        def _update_status(text: str) -> None:
            props.chat_status = text
            _redraw_areas(context)

        thread = threading.Thread(target=_do_turn, daemon=True)
        thread.start()

        return {"FINISHED"}


class BLMCP_OT_chat_clear(Operator):  # type: ignore[misc]
    """Clear the conversation history."""
    bl_idname = "blmcp.chat_clear"
    bl_label = "Clear"
    bl_description = "Clear the conversation history"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller._agent_state.conversation_history.clear()
        agent_controller._agent_state.streaming_text = ""
        _save_chat_history()
        _redraw_areas(context)
        return {"FINISHED"}


class BLMCP_OT_chat_stop(Operator):  # type: ignore[misc]
    """Stop the current generation."""
    bl_idname = "blmcp.chat_stop"
    bl_label = "Stop"
    bl_description = "Stop the current generation"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller._agent_state.is_thinking = False
        agent_controller._agent_state.status_text = "Stopped"
        wm = context.window_manager
        props = wm.blmcp_chat_props  # type: ignore[attr-defined]
        props.chat_status = "Stopped"
        _redraw_areas(context)
        return {"FINISHED"}


class BLMCP_OT_agent_start(Operator):  # type: ignore[misc]
    """Start the agent: MCP bridge, MCP server, and LLM backend."""
    bl_idname = "blmcp.agent_start"
    bl_label = "Start Agent"
    bl_description = "Start the MCP bridge, MCP server, and LLM backend"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.blmcp_chat_props  # type: ignore[attr-defined]

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
            self.report({"INFO"}, "Bridge server started")

        # Step 2: Start the blender-mcp HTTP server.
        if not agent_controller._agent_state.mcp_server_running:
            prefs = context.preferences.addons[__package__].preferences
            _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
            proc = agent_controller.start_mcp_server(port=_mcp_port, blender_port=_bridge_port)
            if proc is None:
                self.report({"ERROR"}, agent_controller._agent_state.error)
                return {"CANCELLED"}
            self.report({"INFO"}, "MCP server started on port {:d}".format(_mcp_port))

        # Step 3: Start the LLM backend (only in local mode).
        # This can be slow (model download or server startup), so it runs
        # on a background thread to avoid freezing Blender's UI.
        prefs = context.preferences.addons[__package__].preferences
        # Sync preferences to llm_manager config before starting.
        llm_cfg = llm_manager.get_config()
        llm_cfg.mode = prefs.llm_mode
        llm_cfg.llama_path = prefs.llama_path
        llm_cfg.model_repo_id = prefs.model_repo_id
        llm_cfg.model_filename = prefs.model_filename
        llm_cfg.downloaded_models_dir = prefs.downloaded_models_dir
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        llm_cfg.local_port = _llm_port
        llm_cfg.local_ctx_size = prefs.local_ctx_size
        llm_cfg.remote_api_url = prefs.remote_api_url
        llm_cfg.remote_api_key = prefs.remote_api_key
        llm_cfg.remote_model = prefs.remote_model
        llm_manager.set_config(llm_cfg)

        if llm_cfg.mode == "local":
            llm_state = llm_manager.get_state()
            if not llm_state.is_running:

                def _start_llm_backend():
                    # If an existing model path is set, use it directly.
                    existing_path = prefs.existing_model_path
                    if existing_path and os.path.isfile(existing_path):
                        llm_manager.start_local_llama(model_path=existing_path)
                    else:
                        llm_manager.start_local_llama()
                    # Update status on the main thread.
                    def _update():
                        state = llm_manager.get_state()
                        if state.is_running:
                            props.chat_status = "Connected"
                        else:
                            props.chat_status = "Error: " + (state.error or "LLM failed to start")
                        _redraw_areas(bpy.context)
                    bpy.app.timers.register(_update, first_interval=1.0)

                import threading
                thread = threading.Thread(target=_start_llm_backend, daemon=True)
                thread.start()
                props.chat_status = "Starting LLM backend..."
            else:
                props.chat_status = "Connected"
        else:
            # In remote mode, no LLM backend is started.
            props.chat_status = "Connected"

        # Load chat history.
        history = _load_chat_history()
        if history:
            agent_controller._agent_state.conversation_history = history

        if llm_cfg.mode != "local" or llm_manager.get_state().is_running:
            props.chat_status = "Connected"

        _redraw_areas(context)
        return {"FINISHED"}


class BLMCP_OT_agent_stop(Operator):  # type: ignore[misc]
    """Stop the agent and all subprocesses."""
    bl_idname = "blmcp.agent_stop"
    bl_label = "Stop Agent"
    bl_description = "Stop the MCP server and LLM backend"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.blmcp_chat_props  # type: ignore[attr-defined]

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
        _redraw_areas(context)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Timer for UI updates

def chat_timer_update() -> float | None:
    """
    Timer callback that periodically redraws chat areas.

    Registered when the add-on starts, runs while Blender is alive.
    """
    # Redraw all chat panels.
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
                if area.type == 'TEXT_EDITOR':
                    area.tag_redraw()
    return 0.5  # Check every 0.5 seconds.


# ---------------------------------------------------------------------------
# Panels

class BLMCP_PT_chat_panel(Panel):  # type: ignore[misc]
    """Chat panel in the 3D Viewport sidebar."""
    bl_label = "MCP Chat"
    bl_idname = "BLMCP_PT_chat_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MCP"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.blmcp_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state

        # Agent control buttons.
        row = layout.row(align=True)
        if state.mcp_server_running:
            row.operator("blmcp.agent_stop", icon="CANCEL", text="Stop Agent")
        else:
            row.operator("blmcp.agent_start", icon="PLAY", text="Start Agent")

        # Status.
        status = props.chat_status
        if state.is_thinking:
            status = "Thinking..."
        elif not state.mcp_server_running:
            status = "Offline"
        elif state.error:
            status = "Error: {:s}".format(state.error)

        row = layout.row()
        row.label(text="Status: {:s}".format(status), icon=(
            'CHECKMARK' if state.mcp_server_running and not state.is_thinking else
            'SORTTIME' if state.is_thinking else
            'ERROR' if state.error else
            'X'
        ))

        # LLM info.
        llm_state = llm_manager.get_state()
        if llm_state.is_running:
            layout.label(text="Model: {:s}".format(llm_state.model_name or "Local LLM"), icon='CONSOLE')
        llm_cfg = llm_manager.get_config()
        if llm_cfg.mode == "remote" and llm_cfg.remote_model:
            layout.label(text="Model: {:s}".format(llm_cfg.remote_model), icon='WORLD')

        layout.separator()

        # Input area (multi-line textbox) — always at top.
        layout.textbox(props, "chat_input")

        # Action buttons.
        row = layout.row(align=True)
        row.scale_y = 1.5
        if state.is_thinking:
            row.operator("blmcp.chat_stop", icon="PAUSE", text="Stop")
        else:
            row.operator("blmcp.chat_send", icon="PLAY", text="Send")
        row.operator("blmcp.chat_clear", icon="X", text="Clear")

        layout.separator()

        # Conversation history — latest message first.
        history = state.conversation_history
        if history:
            box = layout.box()
            for msg in reversed(history[-20:]):  # Show last 20, newest first.
                role = msg.get("role", "")
                content = msg.get("content", "")
                tool_name = msg.get("name", "")

                if role == "user":
                    row = box.row()
                    row.label(text="You:", icon='USER')
                    for line in _wrap_text(content).split("\n"):
                        box.label(text=line)
                elif role == "assistant":
                    row = box.row()
                    row.label(text="Agent:", icon='CONSOLE')
                    if content:
                        for line in _wrap_text(content).split("\n"):
                            box.label(text=line)
                elif role == "tool":
                    row = box.row()
                    row.label(text="[Tool] {:s}:".format(tool_name), icon='TOOL_SETTINGS')
                    c = content or ""
                    if len(c) > 200:
                        c = c[:200] + "..."
                    for line in _wrap_text(c).split("\n"):
                        box.label(text=line)

                box.separator()
        else:
            layout.label(text="No messages yet. Start the agent and type below.", icon='INFO')


class BLMCP_PT_chat_text_editor(Panel):  # type: ignore[misc]
    """Chat panel in the Text Editor sidebar."""
    bl_label = "MCP Chat"
    bl_idname = "BLMCP_PT_chat_text_editor"
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = "MCP"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.blmcp_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state

        # Status bar.
        row = layout.row(align=True)
        if state.mcp_server_running:
            row.operator("blmcp.agent_stop", icon="CANCEL", text="Stop")
            row.label(text="Running", icon='CHECKMARK')
        else:
            row.operator("blmcp.agent_start", icon="PLAY", text="Start")
            row.label(text="Stopped", icon='X')

        layout.separator()

        # Input (multi-line textbox) — always at top.
        layout.textbox(props, "chat_input")

        row = layout.row(align=True)
        row.scale_y = 1.5
        if state.is_thinking:
            row.operator("blmcp.chat_stop", icon="PAUSE", text="Stop")
        else:
            row.operator("blmcp.chat_send", icon="PLAY", text="Send")
        row.operator("blmcp.chat_clear", icon="X", text="Clear")

        layout.separator()

        # Conversation summary — latest message first.
        history = state.conversation_history
        if history:
            box = layout.box()
            box.label(text="History ({:d} messages)".format(len(history)), icon='TEXT')
            for msg in reversed(history[-10:]):
                role = msg.get("role", "")
                content = msg.get("content", "")
                preview = content[:80] + "..." if content and len(content) > 80 else (content or "")
                box.label(text="[{:s}] {:s}".format(role, preview))
        else:
            layout.label(text="No conversation yet.", icon='INFO')


# ---------------------------------------------------------------------------
# Helpers

def _redraw_areas(context: bpy.types.Context | None) -> None:
    """Force redraw of all panels."""
    if context and context.area:
        context.area.tag_redraw()


# ---------------------------------------------------------------------------
# Registration helpers

_classes = (
    ChatHistoryProperties,
    BLMCP_OT_chat_send,
    BLMCP_OT_chat_clear,
    BLMCP_OT_chat_stop,
    BLMCP_OT_agent_start,
    BLMCP_OT_agent_stop,
    BLMCP_PT_chat_panel,
    BLMCP_PT_chat_text_editor,
)


def register() -> None:
    # Idempotent registration — unregister old classes first if re-enabling.
    if hasattr(bpy.types.WindowManager, "blmcp_chat_props"):
        unregister()

    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.blmcp_chat_props = bpy.props.PointerProperty(type=ChatHistoryProperties)  # type: ignore[attr-defined]

    # Register the chat UI update timer.
    if not bpy.app.background:
        bpy.app.timers.register(chat_timer_update, first_interval=1.0, persistent=True)


def unregister() -> None:
    # Save history.
    _save_chat_history()

    if bpy.app.timers.is_registered(chat_timer_update):
        bpy.app.timers.unregister(chat_timer_update)

    if hasattr(bpy.types.WindowManager, "blmcp_chat_props"):
        del bpy.types.WindowManager.blmcp_chat_props  # type: ignore[attr-defined]
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass