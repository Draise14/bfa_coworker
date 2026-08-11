# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Bforartists Coworker Chat Panel — provides an in-Blender chat interface to the MCP agent.

Registers a ``VIEW_3D`` sidebar panel with conversation history, multi-line
input, send/clear/stop buttons, and a status bar.

Also registers a Text Editor side panel for prompt-based interaction.
"""

__all__ = (
    "ChatHistoryProperties",
    "BFACW_PT_chat_panel",
    "BFACW_PT_chat_text_editor",
    "BFACW_OT_chat_send",
    "BFACW_OT_chat_clear",
    "BFACW_OT_chat_stop",
    "BFACW_OT_agent_start",
    "BFACW_OT_agent_stop",
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
    EnumProperty,
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
from .shared import effective_ports, CHAT_MODE_ITEMS


_WRAP_WIDTH = 60


def _wrap_text(text: str, width: int = _WRAP_WIDTH) -> str:
    """Wrap text to a given width for display in Blender labels."""
    if not text:
        return ""
    return "\n".join(
        textwrap.fill(line, width=width)
        for line in text.split("\n")
    )


def _draw_multiline(layout: bpy.types.UILayout, text: str, width: int = _WRAP_WIDTH) -> None:
    """Draw multi-line text in a layout, using ``label_multiline`` if available.

    In Blender 5.3+, ``UILayout.label_multiline(text=...)`` natively wraps
    long text across multiple lines.  For older versions we fall back to
    one ``label()`` call per wrapped line.
    """
    if not text:
        return
    if hasattr(layout, "label_multiline"):
        layout.label_multiline(text=text)
    else:
        for line in _wrap_text(text, width=width).split("\n"):
            layout.label(text=line)


def _draw_reasoning(layout: bpy.types.UILayout, text: str) -> None:
    """Draw reasoning (chain-of-thought) content in a collapsible panel.

    Shows a box with a "Thinking:" label and preview of the first 3 lines.
    Inside the box, a collapsible panel reveals the full reasoning.
    """
    if not text:
        return

    lines = text.strip().split("\n")
    preview_lines = lines[:3]

    # Outer box for the reasoning section.
    outer = layout.box()

    # Row with "Thinking:" label.
    row = outer.row()
    row.label(text="Thinking:", icon='CONSOLE')

    # Preview of first 3 lines.
    for line in preview_lines:
        _draw_multiline(outer, line)

    # Collapsible panel for full reasoning.
    header, body = outer.panel("reasoning_full")
    header.label(text="Show full reasoning ({:d} lines)".format(len(lines)))

    if body:
        body.separator()
        for line in lines:
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
        # Show truncated raw content.
        raw_preview = content[:300] + ("..." if len(content) > 300 else "")
        _draw_multiline(detail_box, raw_preview, width=_WRAP_WIDTH)


# ---------------------------------------------------------------------------
# Properties

def _chat_history_dir() -> Path:
    """Return the directory where chat history JSON files are stored."""
    base = Path(bpy.utils.user_resource("SCRIPTS")) / "bfa_coworker_chat_history"
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

    chat_mode: EnumProperty(  # type: ignore[valid-type]
        name="Mode",
        description="Agent mode: LLM can execute tools. Ask mode: read-only Q&A",
        items=CHAT_MODE_ITEMS,
        default="AGENT",
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

class BFACW_OT_chat_send(Operator):  # type: ignore[misc]
    """Send the current input to the MCP agent."""
    bl_idname = "bfacw.chat_send"
    bl_label = "Send"
    bl_description = "Send your message to the MCP agent"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
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
                    on_reasoning=lambda r: _update_streaming(context, r),
                    on_status=lambda s: _update_status(s),
                    llm_url=llm_url or None,
                    api_key=api_key or None,
                    model=model,
                    mcp_port=_mcp_port,
                    chat_mode=props.chat_mode,
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

        def _update_streaming(ctx: bpy.types.Context, text: str) -> None:
            """Called when reasoning or streaming text arrives — refresh UI."""
            _redraw_areas(ctx)

        thread = threading.Thread(target=_do_turn, daemon=True)
        thread.start()

        return {"FINISHED"}


class BFACW_OT_chat_clear(Operator):  # type: ignore[misc]
    """Clear the conversation history."""
    bl_idname = "bfacw.chat_clear"
    bl_label = "Clear"
    bl_description = "Clear the conversation history"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller._agent_state.conversation_history.clear()
        agent_controller._agent_state.streaming_text = ""
        agent_controller._agent_state.reasoning_text = ""
        agent_controller._agent_state.thinking_dots = 0
        _save_chat_history()
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_chat_stop(Operator):  # type: ignore[misc]
    """Stop the current generation."""
    bl_idname = "bfacw.chat_stop"
    bl_label = "Stop"
    bl_description = "Stop the current generation"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller.request_stop()
        agent_controller._agent_state.status_text = "Stopped"
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        props.chat_status = "Stopped"
        _redraw_areas(context)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# @Mention Autocomplete (Tier 2)

class BFACW_OT_mention_search(Operator):  # type: ignore[misc]
    """Search for scene objects by name and insert @mention into chat."""
    bl_idname = "bfacw.mention_search"
    bl_label = "@ Mention Object"
    bl_description = "Search scene objects and insert an @mention into the chat input"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager

        # Collect all scene objects.
        objects = []
        for obj in bpy.data.objects:
            objects.append({
                "name": obj.name,
                "type": obj.type,
            })

        if not objects:
            self.report({"INFO"}, "No objects in the scene.")
            return {"CANCELLED"}

        # Sort by name.
        objects.sort(key=lambda o: o["name"].lower())

        def _draw_menu(menu, _context):
            layout = menu.layout
            layout.label(text="Select an object to @mention:", icon='OUTLINER_OB_MESH')
            for obj in objects[:50]:  # Limit to 50 to avoid huge menus.
                op = layout.operator(
                    "bfacw.mention_insert",
                    text="[{:s}] {:s}".format(obj["type"], obj["name"]),
                    icon='OBJECT_DATA',
                )
                op.object_name = obj["name"]

        wm.popup_menu(_draw_menu, title="@ Mention Object", icon='OUTLINER_OB_MESH')
        return {"FINISHED"}


class BFACW_OT_mention_insert(Operator):  # type: ignore[misc]
    """Insert an @mentioned object name into the chat input."""
    bl_idname = "bfacw.mention_insert"
    bl_label = "Insert @mention"
    bl_description = "Insert the selected object name as an @mention in the chat input"

    object_name: StringProperty(  # type: ignore[valid-type]
        name="Object Name",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        current = props.chat_input
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
                    "# Write instructions for the AI agent below.\n"
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
    bl_description = "Reload project rules into the agent's system prompt"

    def execute(self, context: bpy.types.Context) -> set[str]:
        # Clear cached system prompt so it's rebuilt on next turn.
        agent_controller._clear_system_prompt_cache()
        self.report({"INFO"}, "Project rules reloaded")
        return {"FINISHED"}


class BFACW_OT_agent_start(Operator):  # type: ignore[misc]
    """Start the agent: MCP bridge, MCP server, and LLM backend."""
    bl_idname = "bfacw.agent_start"
    bl_label = "Start Agent"
    bl_description = "Start the MCP bridge, MCP server, and LLM backend"

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences

        # In External Harness mode, only start the bridge server.
        if prefs.agent_mode == "EXTERNAL_HARNESS":
            return self._start_bridge_only(context)

        return self._start_full_agent(context)

    def _start_bridge_only(self, context: bpy.types.Context) -> set[str]:
        """Start only the bridge server (External Harness mode)."""
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        if mcp_to_blender_server.is_running():
            self.report({"INFO"}, "Bridge server already running")
            _bridge_port, _, _ = effective_ports(
                context.preferences.addons[__package__].preferences)
            props.chat_status = "External Harness — Bridge on port {:d}".format(_bridge_port)
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

        props.chat_status = "External Harness — Bridge on port {:d}".format(_bridge_port)
        self.report({"INFO"}, "Bridge server started on port {:d}".format(_bridge_port))
        _redraw_areas(context)
        return {"FINISHED"}

    def _start_full_agent(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

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

        # Step 2: Start the MCP HTTP server.
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


class BFACW_OT_agent_stop(Operator):  # type: ignore[misc]
    """Stop the agent and all subprocesses."""
    bl_idname = "bfacw.agent_stop"
    bl_label = "Stop Agent"
    bl_description = "Stop the MCP server and LLM backend"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

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
    Timer callback that periodically redraws chat areas and animates
    the "Thinking..." indicator.

    Registered when the add-on starts, runs while Blender is alive.
    """
    from . import agent_controller as _ac

    # Animate thinking dots.
    if _ac._agent_state.is_thinking:
        _ac._agent_state.thinking_dots += 1

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

class BFACW_PT_chat_panel(Panel):  # type: ignore[misc]
    """Chat panel in the 3D Viewport sidebar."""
    bl_label = "Coworker Chat"
    bl_idname = "BFACW_PT_chat_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Coworker"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state
        prefs = context.preferences.addons[__package__].preferences
        is_harness = (prefs.agent_mode == "EXTERNAL_HARNESS")

        # Agent control buttons.
        row = layout.row(align=True)
        row.scale_y = 2.0
        if is_harness:
            if mcp_to_blender_server.is_running():
                row.operator("bfacw.agent_stop", icon="CANCEL", text="Stop Bridge")
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start Bridge")
        else:
            if state.mcp_server_running:
                row.operator("bfacw.agent_stop", icon="CANCEL", text="Stop Agent")
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start Agent")

        # Status.
        if is_harness:
            bridge_running = mcp_to_blender_server.is_running()
            if bridge_running:
                _bridge_port, _, _ = effective_ports(prefs)
                status = "External Harness — Bridge on port {:d}".format(_bridge_port)
            else:
                status = "Bridge Offline"
        else:
            status = props.chat_status
            if state.is_thinking:
                # Animated thinking dots (cycled by timer).
                dots = [".", "..", "...", "...."]
                status = "Thinking{:s}".format(dots[state.thinking_dots % 4])
            elif not state.mcp_server_running:
                status = "Offline"
            elif state.error:
                status = "Error: {:s}".format(state.error)

        row = layout.row()
        is_ok = (mcp_to_blender_server.is_running() if is_harness else state.mcp_server_running)
        row.label(text="Status: {:s}".format(status), icon=(
            'CHECKMARK' if is_ok and not state.is_thinking else
            'SORTTIME' if state.is_thinking else
            'ERROR' if state.error else
            'X'
        ))

        # Liveness dots (Tier 1).
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

        # Tool count.
        if not is_harness and state.mcp_server_running:
            if state.tool_count > 0:
                layout.label(text="Tools: {:d} loaded".format(state.tool_count), icon='MODIFIER')
            else:
                layout.label(
                    text="Tools: none loaded — MCP may still be starting",
                    icon='ERROR',
                )

        # LLM info.
        if not is_harness:
            llm_state = llm_manager.get_state()
            if llm_state.is_running:
                layout.label(text="Model: {:s}".format(llm_state.model_name or "Local LLM"), icon='CONSOLE')
            llm_cfg = llm_manager.get_config()
            if llm_cfg.mode == "remote" and llm_cfg.remote_model:
                layout.label(text="Model: {:s}".format(llm_cfg.remote_model), icon='WORLD')

        # ── External Harness: Config & Instructions ──
        if is_harness and mcp_to_blender_server.is_running():
            box = layout.box()
            box.label(text="Connect an External MCP Client", icon='WORLD')

            # Copy config buttons.
            row = box.row(align=True)
            op = row.operator("bfacw.copy_mcp_config", text="Claude Desktop Config", icon='COPYDOWN')
            op.client_type = "claude"
            op = row.operator("bfacw.copy_mcp_config", text="VS Code Config", icon='COPYDOWN')
            op.client_type = "vscode"

            # Instructions.
            box.label(text="1. Copy the config above to your clipboard", icon='DOT')
            box.label(text="2. Paste into your MCP client's config file", icon='DOT')
            box.label(text="3. Restart your MCP client", icon='DOT')
            box.label(text="4. The client will connect to Blender's bridge", icon='DOT')

            # MCP server mode selector.
            box.separator()
            box.label(text="MCP Server Mode:", icon='SETTINGS')
            box.prop(prefs, "mcp_server_mode", expand=True)

            if prefs.mcp_server_mode == "NETWORK":
                box.prop(prefs, "mcp_server_host")
                row = box.row(align=True)
                row.prop(prefs, "mcp_server_port_override")
                if prefs.mcp_server_host not in ("127.0.0.1", "localhost", "::1"):
                    box.label(
                        text="\u26a0 Binding to non-localhost exposes the MCP server to your network!",
                        icon='ERROR',
                    )
                row = box.row(align=True)
                if agent_controller._agent_state.mcp_server_running:
                    row.operator("bfacw.mcp_server_stop", icon="CANCEL", text="Stop MCP Server")
                else:
                    row.operator("bfacw.mcp_server_start", icon="PLAY", text="Start MCP Server")

            layout.separator()

        # ── In harness mode, disable chat input ──
        if is_harness:
            layout.label(text="Chat is handled by your external MCP client.", icon='INFO')
            layout.label(text="Messages below are read-only monitoring.", icon='INFO')
        else:
            # Agent/Ask mode toggle (Tier 1).
            row = layout.row(align=True)
            row.prop(props, "chat_mode", expand=True)

            layout.separator()

            # Input area (multi-line textbox).
            layout.textbox(props, "chat_input")

            # @mention button (Tier 2).
            row = layout.row(align=True)
            row.operator("bfacw.mention_search", icon="OUTLINER_OB_MESH", text="@ Mention Object")

            # Action buttons.
            row = layout.row(align=True)
            row.scale_y = 1.5
            if state.is_thinking:
                row.operator("bfacw.chat_stop", icon="PAUSE", text="Stop")
            else:
                row.operator("bfacw.chat_send", icon="PLAY", text="Send")
            row.operator("bfacw.chat_clear", icon="X", text="Clear")

        layout.separator()

        # Conversation history — latest message first.
        history = state.conversation_history
        if history:
            box = layout.box()
            # Show a streaming preview if we're thinking and streaming text exists.
            if state.is_thinking and state.streaming_text:
                preview_row = box.row()
                preview_row.label(text="Agent (live):", icon='CONSOLE')
                _draw_multiline(box, state.streaming_text[:300] + "...")
                box.separator()

            for msg in reversed(history[-20:]):  # Show last 20, newest first.
                role = msg.get("role", "")
                content = msg.get("content", "")
                tool_name = msg.get("name", "")
                summary = msg.get("summary", "")

                if role == "user":
                    row = box.row()
                    row.label(text="You:", icon='USER')
                    _draw_multiline(box, content)
                elif role == "assistant":
                    row = box.row()
                    row.label(text="Agent:", icon='CONSOLE')
                    if content:
                        _draw_multiline(box, content)
                elif role == "tool":
                    row = box.row()
                    is_error = (
                        '"status": "error"' in (content or "") or
                        (content or "").startswith("Error")
                    )
                    row.label(
                        text="[Tool] {:s}:".format(tool_name),
                        icon='CANCEL' if is_error else 'TOOL_SETTINGS',
                    )
                    # Prefer the human-readable summary if available, otherwise
                    # show truncated content.
                    display = summary if summary else (content or "")
                    if not summary and len(display) > 200:
                        display = display[:200] + "..."
                    _draw_multiline(box, display)
                elif role == "reasoning":
                    # Reasoning: show collapsed by default with a distinct style.
                    _draw_reasoning(box, content)

                box.separator()
        else:
            layout.label(text="No messages yet. Start the agent and type below.", icon='INFO')


class BFACW_PT_chat_text_editor(Panel):  # type: ignore[misc]
    """Chat panel in the Text Editor sidebar."""
    bl_label = "Coworker Chat"
    bl_idname = "BFACW_PT_chat_text_editor"
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Coworker"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state
        prefs = context.preferences.addons[__package__].preferences
        is_harness = (prefs.agent_mode == "EXTERNAL_HARNESS")

        # Status bar.
        row = layout.row(align=True)
        if is_harness:
            if mcp_to_blender_server.is_running():
                row.operator("bfacw.agent_stop", icon="CANCEL", text="Stop Bridge")
                row.label(text="Bridge Running", icon='CHECKMARK')
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start Bridge")
                row.label(text="Bridge Stopped", icon='X')
        else:
            if state.mcp_server_running:
                row.operator("bfacw.agent_stop", icon="CANCEL", text="Stop")
                row.label(text="Running", icon='CHECKMARK')
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start")
                row.label(text="Stopped", icon='X')

        layout.separator()

        if not is_harness:
            # Agent/Ask mode toggle (Tier 1).
            row = layout.row(align=True)
            row.prop(props, "chat_mode", expand=True)

            # Project Rules button (Tier 2).
            row = layout.row(align=True)
            row.operator("bfacw.edit_rules", icon="TEXT", text="Edit Rules")

            layout.separator()

            # Input (multi-line textbox).
            layout.textbox(props, "chat_input")

            row = layout.row(align=True)
            row.scale_y = 1.5
            if state.is_thinking:
                row.operator("bfacw.chat_stop", icon="PAUSE", text="Stop")
            else:
                row.operator("bfacw.chat_send", icon="PLAY", text="Send")
            row.operator("bfacw.chat_clear", icon="X", text="Clear")
        else:
            layout.label(text="Chat handled by external MCP client.", icon='INFO')

        layout.separator()

        # Conversation summary — latest message first.
        history = state.conversation_history
        if history:
            box = layout.box()
            box.label(text="History ({:d} messages)".format(len(history)), icon='TEXT')
            for msg in reversed(history[-10:]):
                role = msg.get("role", "")
                content = msg.get("content", "")
                summary = msg.get("summary", "")
                if role == "reasoning":
                    preview = "Thinking... ({:d} chars)".format(len(content or ""))
                elif role == "tool":
                    display = summary if summary else (content or "")
                    preview = display[:80] + "..." if display and len(display) > 80 else (display or "")
                else:
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
    BFACW_OT_chat_send,
    BFACW_OT_chat_clear,
    BFACW_OT_chat_stop,
    BFACW_OT_mention_search,
    BFACW_OT_mention_insert,
    BFACW_OT_edit_rules,
    BFACW_OT_reload_rules,
    BFACW_OT_agent_start,
    BFACW_OT_agent_stop,

    BFACW_PT_chat_panel,
    BFACW_PT_chat_text_editor,
)


def register() -> None:
    # Idempotent registration — unregister old classes first if re-enabling.
    if hasattr(bpy.types.WindowManager, "bfacw_chat_props"):
        unregister()

    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bfacw_chat_props = bpy.props.PointerProperty(type=ChatHistoryProperties)  # type: ignore[attr-defined]

    # Register the chat UI update timer.
    if not bpy.app.background:
        bpy.app.timers.register(chat_timer_update, first_interval=1.0, persistent=True)


def unregister() -> None:
    # Save history.
    _save_chat_history()

    if bpy.app.timers.is_registered(chat_timer_update):
        bpy.app.timers.unregister(chat_timer_update)

    if hasattr(bpy.types.WindowManager, "bfacw_chat_props"):
        del bpy.types.WindowManager.bfacw_chat_props  # type: ignore[attr-defined]
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass