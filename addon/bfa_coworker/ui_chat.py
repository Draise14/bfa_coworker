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
    "BFACW_PT_chat_queue",
    "BFACW_PT_chat_status",
    "BFACW_PT_chat_text_editor",
    "BFACW_OT_chat_send",
    "BFACW_OT_chat_clear",
    "BFACW_OT_chat_stop",
    "BFACW_OT_chat_queue_send",
    "BFACW_OT_export_session_log",
    "BFACW_OT_copy_session_log",
    "BFACW_OT_agent_start",
    "BFACW_OT_agent_stop",
    "BFACW_OT_agent_restart",
    "chat_timer_update",
    "register",
    "unregister",
)

import json
import os
import time
import threading
from pathlib import Path

import bpy  # pylint: disable=import-error
from bpy.props import (  # pylint: disable=import-error
    BoolProperty,
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
    llm_cfg.remote_api_url = prefs.remote_api_url
    llm_cfg.remote_api_key = prefs.remote_api_key
    llm_cfg.remote_model = prefs.remote_model
    llm_manager.set_config(llm_cfg)


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


def _draw_reasoning(
    layout: bpy.types.UILayout,
    text: str,
    label: str = "Thinking",
    is_thinking: bool = False,
    thinking_dots: int = 0,
    message_index: int = -1,
) -> None:
    """Draw reasoning (chain-of-thought) content in a collapsible panel.

    Shows a box with a thinking label and preview of the first 3 lines.
    While *is_thinking* is True, the label animates with dots.
    Inside the box, a collapsible panel reveals the full reasoning.
    The *label* is stored when the reasoning was first captured so it
    doesn't flicker on every redraw.
    """
    if not text:
        return

    lines = text.strip().split("\n")
    preview_lines = lines[:3]
    remaining_lines = lines[3:]

    # Animate the label with Unicode spinner while thinking.
    if is_thinking:
        spinners = ["\u25d0", "\u25d3", "\u25d1", "\u25d2"]  # \u25d0 \u25d3 \u25d1 \u25d2
        display_label = "{:s} {:s}".format(label, spinners[thinking_dots % 4])
        icon = 'CONSOLE'
    else:
        display_label = label
        icon = 'CHECKMARK'

    # Outer box for the reasoning section.
    outer = layout.box()

    # Row with thinking label and copy button.
    row = outer.row()
    row.label(text="{:s}:".format(display_label), icon=icon)
    if message_index >= 0:
        op = row.operator("bfacw.copy_message", text="", icon='COPYDOWN')
        op.message_index = message_index

    # Preview of first 3 lines.
    for line in preview_lines:
        _draw_multiline(outer, line)

    # Collapsible panel for full reasoning (closed by default).
    # Only shows lines beyond the preview to avoid duplication.
    if remaining_lines:
        header, body = outer.panel("reasoning_full", default_closed=True)
        header.label(text="Show full reasoning ({:d} more lines)".format(len(remaining_lines)))

        if body:
            body.separator()
            for line in remaining_lines:
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


def _draw_tool_inline(
    layout: bpy.types.UILayout,
    tool_name: str,
    display: str,
    is_error: bool,
    message_index: int = -1,
) -> None:
    """Draw a tool result as a sub-box inside the agent's message box."""
    tool_box = layout.box()
    row = tool_box.row()
    row.label(
        text="\u2699 {:s}".format(tool_name),
        icon='WARNING' if is_error else 'TOOL_SETTINGS',
    )
    if message_index >= 0:
        op = row.operator("bfacw.copy_message", text="", icon='COPYDOWN')
        op.message_index = message_index
    _draw_multiline(tool_box, display)


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
        description="Type your message for the Coworker (AI agent)",
        default="",
    )

    chat_status: StringProperty(  # type: ignore[valid-type]
        name="Status",
        default="Idle",
    )

    chat_mode: EnumProperty(  # type: ignore[valid-type]
        name="Mode",
        description="Coworker mode: the agent can execute tools. Ask mode: read-only Q&A",
        items=CHAT_MODE_ITEMS,
        default="AGENT",
    )

    chat_newest_first: BoolProperty(  # type: ignore[valid-type]
        name="Newest First",
        description="Show the most recent messages at the top of the chat history",
        default=True,
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


# Thread lock for history serialization — prevents concurrent threads
# from writing partial dumps when a turn finishes while another is active.
_history_save_lock = threading.Lock()


def _save_chat_history() -> None:
    """Save conversation history to disk (thread-safe) with versioned copies."""
    import time as _time
    base_dir = _chat_history_path().parent
    with _history_save_lock:
        try:
            # Save timestamped copy.
            ts = _time.strftime("%Y-%m-%d_%H-%M-%S", _time.localtime())
            versioned_path = base_dir / "default_{:s}.json".format(ts)
            with open(str(versioned_path), "w", encoding="utf-8") as fh:
                json.dump(agent_controller._agent_state.conversation_history, fh, indent=2)
            # Also save to default.json (latest).
            with open(str(_chat_history_path()), "w", encoding="utf-8") as fh:
                json.dump(agent_controller._agent_state.conversation_history, fh, indent=2)
            # Prune old versions: keep last 10.
            _prune_old_sessions(base_dir)
        except OSError:
            pass


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

class BFACW_OT_chat_send(Operator):  # type: ignore[misc]
    """Send the current input to the Coworker agent (or queue if busy)."""
    bl_idname = "bfacw.chat_send"
    bl_label = "Send"
    bl_description = "Send your message to the Coworker agent"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        message = props.chat_input.strip()
        if not message:
            return {"CANCELLED"}

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

        # If a turn is already active, queue the message.
        if agent_controller._agent_state.turn_active:
            pos = agent_controller.enqueue_message(
                message=message,
                chat_mode=props.chat_mode,
                llm_url=llm_url or None,
                api_key=api_key or None,
                model=model,
                mcp_port=send_mcp_port,
            )
            props.chat_input = ""
            self.report({"INFO"}, "Message queued (position {:d})".format(pos))
            _redraw_areas(context)
            return {"FINISHED"}

        # Clear input and start processing.
        props.chat_input = ""
        props.chat_status = "Thinking..."

        def _do_turn():
            try:
                agent_controller.run_conversation_turn(
                    user_message=message,
                    on_text=None,
                    on_reasoning=lambda r: _update_streaming(r),
                    on_status=lambda s: _update_status(s),
                    llm_url=llm_url or None,
                    api_key=api_key or None,
                    model=model,
                    mcp_port=send_mcp_port,
                    chat_mode=props.chat_mode,
                )
            except Exception as ex:  # pylint: disable=broad-exception-caught
                agent_controller._agent_state.error = str(ex)
            finally:
                _save_chat_history()
                # Auto-dequeue next message if queue is not empty.
                _try_dequeue_next()
                _update_status("Idle")
                _redraw_areas_safe()

        def _try_dequeue_next():
            """Try to process the next queued message."""
            next_msg = agent_controller.dequeue_message()
            if next_msg:
                props.chat_status = "Processing queued message..."
                _redraw_areas_safe()
                # Start processing the next message in a new thread.
                import threading as _threading
                _threading.Thread(target=_do_turn_from_queue, args=(next_msg,), daemon=True).start()

        def _do_turn_from_queue(item: dict):
            """Process a message from the queue."""
            try:
                agent_controller.run_conversation_turn(
                    user_message=item["message"],
                    on_text=None,
                    on_reasoning=lambda r: _update_streaming(r),
                    on_status=lambda s: _update_status(s),
                    llm_url=item.get("llm_url"),
                    api_key=item.get("api_key"),
                    model=item.get("model"),
                    mcp_port=item.get("mcp_port", 0),
                    chat_mode=item.get("chat_mode", "AGENT"),
                )
            except Exception as ex:  # pylint: disable=broad-exception-caught
                agent_controller._agent_state.error = str(ex)
            finally:
                _save_chat_history()
                _try_dequeue_next()
                _update_status("Idle")
                _redraw_areas_safe()

        def _update_status(text: str) -> None:
            props.chat_status = text
            _redraw_areas_safe()

        def _update_streaming(text: str) -> None:
            """Called when reasoning or streaming text arrives — refresh UI."""
            _redraw_areas_safe()

        import threading
        thread = threading.Thread(target=_do_turn, daemon=True)
        thread.start()

        return {"FINISHED"}


class BFACW_OT_chat_clear(Operator):  # type: ignore[misc]
    """Clear the conversation history and start a fresh thread."""
    bl_idname = "bfacw.chat_clear"
    bl_label = "New Thread"
    bl_description = "Clear conversation history and start a fresh thread (system prompt stays)"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller._agent_state.conversation_history.clear()
        agent_controller._agent_state.streaming_text = ""
        agent_controller._agent_state.reasoning_text = ""
        agent_controller._agent_state.thinking_dots = 0
        # Clear Coworker_* text datablocks from the text editor.
        agent_controller._clear_coworker_text_blocks()
        # Clear cached system prompt so project rules are reloaded on next turn.
        agent_controller._clear_system_prompt_cache()
        _save_chat_history()
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_queue_clear(Operator):  # type: ignore[misc]
    """Clear all queued messages."""
    bl_idname = "bfacw.queue_clear"
    bl_label = "Clear Queue"
    bl_description = "Remove all queued messages from the message queue"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller._message_queue.clear()
        self.report({"INFO"}, "Message queue cleared")
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_queue_show(Operator):  # type: ignore[misc]
    """Show queued messages in a popup."""
    bl_idname = "bfacw.queue_show"
    bl_label = "Show Queue"
    bl_description = "Display all queued messages in a popup menu"

    def execute(self, context: bpy.types.Context) -> set[str]:
        queue_items = agent_controller._message_queue.get_all()
        if not queue_items:
            self.report({"INFO"}, "Queue is empty")
            return {"CANCELLED"}

        def _draw_menu(menu, _context):
            layout = menu.layout
            layout.label(
                text="Queued Messages ({:d})".format(len(queue_items)),
                icon='QUEUE',
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
            icon='QUEUE',
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


class BFACW_OT_chat_queue_send(Operator):  # type: ignore[misc]
    """Queue the current input message for later processing."""
    bl_idname = "bfacw.chat_queue_send"
    bl_label = "Queue Message"
    bl_description = "Add the current message to the queue for processing after the current turn"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
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

        pos = agent_controller.enqueue_message(
            message=message,
            chat_mode=props.chat_mode,
            llm_url=llm_url or None,
            api_key=api_key or None,
            model=model,
            mcp_port=send_mcp_port,
        )
        props.chat_input = ""
        self.report({"INFO"}, "Queued (position {:d})".format(pos))
        _redraw_areas(context)
        return {"FINISHED"}


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

    def execute(self, context: bpy.types.Context) -> set[str]:
        history = agent_controller._agent_state.conversation_history
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

        context.window_manager.clipboard = "\n\n".join(parts)
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
            row.label(text="Filter:", icon='VIEWZOOM')
            op = row.operator("bfacw.mention_search", text="All", icon='NONE')
            op.category = ""
            op.filter_text = self.filter_text
            for cat_key, cat_info in _MENTION_CATEGORIES.items():
                op = row.operator(
                    "bfacw.mention_search",
                    text=cat_info["label"],
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

        return self._start_full_agent(context)

    def _start_bridge_only(self, context: bpy.types.Context) -> set[str]:
        """Start only the bridge server (External Harness mode)."""
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        if mcp_to_blender_server.is_running():
            self.report({"INFO"}, "Bridge server already running")
            actual = mcp_to_blender_server.get_actual_port()
            if actual:
                props.chat_status = "External Harness — Bridge on port {:d}".format(actual)
            else:
                props.chat_status = "External Harness — Bridge running"
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
            props.chat_status = "External Harness — Bridge on port {:d}".format(actual)
            self.report({"INFO"}, "Bridge server started on port {:d}".format(actual))
        else:
            props.chat_status = "External Harness — Bridge running"
            self.report({"INFO"}, "Bridge server started")
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
            actual_bridge = mcp_to_blender_server.get_actual_port()
            if actual_bridge:
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

                import threading
                thread = threading.Thread(target=_start_llm_backend, daemon=True)
                thread.start()
                props.chat_status = "Starting LLM backend..."
            else:
                # Already running — warmup in background thread, but only
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

        # Load chat history.
        history = _load_chat_history()
        if history:
            agent_controller._agent_state.conversation_history = history

        # Local-mode status is driven by the background thread (Starting →
        # Loading → Connected / Error: ...); only remote mode marks
        # "Connected" here.
        if llm_cfg.mode != "local":
            props.chat_status = "Connected"

        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_agent_stop(Operator):  # type: ignore[misc]
    """Stop the Coworker agent and all subprocesses."""
    bl_idname = "bfacw.agent_stop"
    bl_label = "Stop Coworker"
    bl_description = "Stop the Coworker agent and all subprocesses"

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
    """Main chat panel in the 3D Viewport sidebar — input and messages."""
    bl_label = "Coworker"
    bl_idname = "BFACW_PT_chat_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Coworker"

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

        # ── Agent control buttons (compact) ──
        row = layout.row(align=True)
        row.scale_y = 1.8
        if is_harness:
            if mcp_to_blender_server.is_running():
                actual = mcp_to_blender_server.get_actual_port()
                tip = "Bridge running on port {:d}".format(actual) if actual else "Stop Bridge"
                row.operator("bfacw.agent_stop", icon="CANCEL", text="Stop Bridge")
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start Bridge")
        else:
            if state.mcp_server_running:
                row.operator("bfacw.agent_stop", icon="CANCEL", text="Stop")
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start")

        # ── Compact status line ──
        if is_harness:
            status = "Bridge Running" if mcp_to_blender_server.is_running() else "Bridge Offline"
            is_ok = mcp_to_blender_server.is_running()
        else:
            status = props.chat_status
            if state.is_thinking:
                spinners = ["\u25d0", "\u25d3", "\u25d1", "\u25d2"]
                elapsed = time.time() - state.thinking_start_time if state.thinking_start_time else 0.0
                status = "Thinking {:s} ({:.0f}s)".format(spinners[state.thinking_dots % 4], elapsed)
            elif not state.mcp_server_running:
                status = "Offline"
            elif state.error:
                status = "Error: {:s}".format(state.error)
            is_ok = state.mcp_server_running

        status_icon = (
            'CHECKMARK' if is_ok and not state.is_thinking else
            'SORTTIME' if state.is_thinking else
            'WARNING' if state.error else 'X'
        )
        status_row = layout.row()
        status_row.label(text=status, icon=status_icon)
        # Long error messages (traceback, server logs) — show multiline.
        if state.error and len(status) > 60:
            _draw_multiline(layout, state.error)

        # ── External Harness mode ──
        if is_harness:
            if mcp_to_blender_server.is_running():
                box = layout.box()
                box.label(text="External MCP Client", icon='WORLD')
                row = box.row(align=True)
                row.scale_y = 1.2
                row.operator("bfacw.open_harness_prefs", icon="PREFERENCES", text="Configure")
                row = box.row(align=True)
                row.prop(prefs, "harness_preset", text="")
                op = row.operator("bfacw.copy_mcp_config", icon="COPYDOWN", text="Copy")
                op.client_type = prefs.harness_preset
            layout.label(text="Chat handled by external client.", icon='INFO')
            return

        # ── Mode toggle ──
        row = layout.row(align=True)
        row.prop(props, "chat_mode", expand=True)

        layout.separator()

        # ── Input area ──
        layout.textbox(props, "chat_input")

        # @mention button.
        row = layout.row(align=True)
        row.operator("bfacw.mention_search", icon="OUTLINER_OB_MESH", text="@ Mention")

        # ── Action buttons ──
        if state.is_thinking:
            # During thinking: Stop + Queue side by side.
            btn_row = layout.row(align=True)
            btn_row.scale_y = 1.5
            btn_row.operator("bfacw.chat_stop", icon="PAUSE", text="Stop")
            btn_row.operator("bfacw.chat_queue_send", icon="ADD", text="Queue")
        else:
            # Idle: Send + New Thread.
            btn_row = layout.row(align=True)
            btn_row.scale_y = 1.5
            btn_row.operator("bfacw.chat_send", icon="PLAY", text="Send")
            btn_row.operator("bfacw.chat_clear", icon="X", text="New Thread")

        layout.separator()

        # ── Conversation history ──
        history = state.conversation_history
        if history:
            # Display order toggle + message count.
            hist_box = layout.box()
            toggle_row = hist_box.row(align=True)
            toggle_row.prop(
                props, "chat_newest_first",
                icon='SORTTIME', text="Newest First",
            )
            # Count displayable messages (exclude system/internal).
            displayable = sum(1 for m in history if m.get("role") != "system")
            _draw_multiline(
                hist_box,
                "({:d} messages)".format(displayable),
            )

            # Group messages into turns (each user message starts a new turn).
            turns: list[list[dict]] = []
            current_turn: list[dict] = []
            for msg in history:
                role = msg.get("role", "")
                if role == "user":
                    if current_turn:
                        turns.append(current_turn)
                    current_turn = [msg]
                elif role in ("assistant", "tool", "reasoning"):
                    current_turn.append(msg)
            if current_turn:
                turns.append(current_turn)

            # Determine display order and turn limit.
            visible_turns = turns[-3:]
            turn_iter = (
                reversed(visible_turns) if props.chat_newest_first
                else visible_turns
            )

            for turn_idx, turn in enumerate(turn_iter):
                # Separate messages by role, preserving chronological order
                # for interleaved reasoning+tool display.
                user_msg = None
                process_msgs: list[dict] = []  # reasoning + tool, in order
                conclusion_msg = None
                for msg in turn:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    is_system_user = (
                        role == "user"
                        and isinstance(content, str)
                        and content.startswith("[System:")
                    )
                    if role == "user" and not is_system_user:
                        user_msg = msg
                    elif role in ("reasoning", "tool") or is_system_user:
                        process_msgs.append(msg)
                    elif role == "assistant":
                        if msg.get("tool_calls"):
                            pass  # Intermediate "running tools" — skip
                        else:
                            conclusion_msg = msg

                # Handle assistant-only turns (e.g. the welcome message).
                if not user_msg:
                    if conclusion_msg:
                        turn_box = hist_box.box()
                        c_row = turn_box.row()
                        c_row.label(text="Coworker:", icon='CONSOLE')
                        op = c_row.operator(
                            "bfacw.copy_message", text="", icon='COPYDOWN',
                        )
                        op.message_index = history.index(conclusion_msg)
                        _draw_multiline(
                            turn_box,
                            conclusion_msg.get("content", ""),
                        )
                    continue

                # ── Outer turn box ──────────────────────────────
                turn_box = hist_box.box()

                # ── Collapsible: user prompt + process steps ────
                has_process = bool(process_msgs)

                if has_process:
                    # Determine turn status icon.
                    has_tool_error = any(
                        p.get("role") == "tool" and (
                            '"status": "error"' in (p.get("content") or "")
                            or (p.get("content") or "").startswith("Error")
                        )
                        for p in process_msgs
                    )
                    if has_tool_error:
                        turn_icon = 'CANCEL'
                    elif conclusion_msg:
                        turn_icon = 'CHECKMARK'
                    else:
                        turn_icon = 'SORTTIME'

                    proc_header, proc_body = turn_box.panel(
                        "turn_process_{:d}".format(turn_idx),
                        default_closed=True,
                    )
                    # Header: status icon + user message preview + copy button.
                    hdr_row = proc_header.row()
                    hdr_row.label(text="", icon=turn_icon)
                    _draw_multiline(
                        proc_header,
                        "You: {:s}".format(user_msg.get("content", "")),
                    )
                    op_row = proc_header.row()
                    op_row.label(text="", icon='USER')
                    op = op_row.operator(
                        "bfacw.copy_message", text="", icon='COPYDOWN',
                    )
                    op.message_index = history.index(user_msg)

                    if proc_body:
                        # Interleaved reasoning + tool steps (no duplicate user message).
                        proc_body.separator()

                        # Interleaved reasoning + tool steps.
                        for p_msg in process_msgs:
                            p_role = p_msg.get("role", "")
                            p_content = p_msg.get("content", "")
                            is_system_msg = (
                                p_role == "user"
                                and isinstance(p_content, str)
                                and p_content.startswith("[System:")
                            )
                            if is_system_msg:
                                # Entity context notification — show as internal info.
                                sys_box = proc_body.box()
                                sys_box.label(text="System Context", icon='INFO')
                                _draw_multiline(sys_box, p_content)
                            elif p_role == "reasoning":
                                _draw_reasoning(
                                    proc_body,
                                    p_content,
                                    p_msg.get("label", "Thinking"),
                                    is_thinking=state.is_thinking,
                                    thinking_dots=state.thinking_dots,
                                    message_index=history.index(p_msg),
                                )
                            elif p_role == "tool":
                                t_content = p_msg.get("content", "")
                                t_summary = p_msg.get("summary", "")
                                t_name = p_msg.get("name", "")
                                is_error = (
                                    '"status": "error"' in (t_content or "") or
                                    (t_content or "").startswith("Error")
                                )
                                display = (
                                    t_summary if t_summary
                                    else (t_content or "")
                                )
                                if not t_summary and len(display) > 200:
                                    display = display[:200] + "..."
                                _draw_tool_inline(
                                    proc_body, t_name, display,
                                    is_error,
                                    message_index=history.index(p_msg),
                                )

                        # Live streaming preview (inside collapsible).
                        if (
                            state.is_thinking
                            and state.streaming_text
                            and turn_idx == 0
                        ):
                            proc_body.separator()
                            proc_body.label(
                                text="Coworker (live):", icon='CONSOLE',
                            )
                            _draw_multiline(
                                proc_body,
                                state.streaming_text[:300] + "...",
                            )
                else:
                    # No process steps — show compact user header.
                    _draw_multiline(
                        turn_box,
                        "You: {:s}".format(user_msg.get("content", "")),
                    )
                    op_row = turn_box.row()
                    op_row.label(text="", icon='USER')
                    op = op_row.operator(
                        "bfacw.copy_message", text="", icon='COPYDOWN',
                    )
                    op.message_index = history.index(user_msg)

                # ── Agent conclusion (always visible) ───────────
                if conclusion_msg:
                    turn_box.separator()
                    c_row = turn_box.row()
                    c_row.label(text="Coworker:", icon='CONSOLE')
                    op = c_row.operator(
                        "bfacw.copy_message", text="", icon='COPYDOWN',
                    )
                    op.message_index = history.index(conclusion_msg)
                    _draw_multiline(
                        turn_box,
                        conclusion_msg.get("content", ""),
                    )

                # Streaming preview for in-progress turn (no conclusion yet).
                if (
                    not conclusion_msg
                    and state.is_thinking
                    and state.streaming_text
                    and turn_idx == 0
                    and not has_process
                ):
                    turn_box.separator()
                    turn_box.label(
                        text="Coworker (live):", icon='CONSOLE',
                    )
                    _draw_multiline(
                        turn_box,
                        state.streaming_text[:300] + "...",
                    )

        else:
            layout.label(
                text="Getting ready...",
                icon='INFO',
            )


class BFACW_PT_chat_queue(Panel):  # type: ignore[misc]
    """Queue sub-panel — shows pending queued messages."""
    bl_label = "Message Queue"
    bl_idname = "BFACW_PT_chat_queue"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Coworker"
    bl_parent_id = "BFACW_PT_chat_panel"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        state = agent_controller._agent_state
        queue_items = agent_controller._message_queue.get_all()

        if not queue_items:
            layout.label(text="Queue empty", icon='CHECKMARK')
            return

        layout.label(
            text="{:d} message(s) queued".format(len(queue_items)),
            icon='QUEUE',
        )

        for idx, item in enumerate(queue_items):
            msg = item.get("message", "")
            mode = item.get("chat_mode", "AGENT")
            preview = msg[:60] + ("..." if len(msg) > 60 else "")
            box = layout.box()
            hdr = box.row()
            hdr.label(text="[{:d}] {:s}".format(idx + 1, mode), icon='SORTTIME')
            _draw_multiline(box, preview)

        # Clear button.
        row = layout.row(align=True)
        row.scale_y = 1.0
        row.operator("bfacw.queue_clear", icon="TRASH", text="Clear Queue")


class BFACW_PT_chat_status(Panel):  # type: ignore[misc]
    """Status sub-panel — health dots, model info, tools, advanced diagnostics."""
    bl_label = "Status & Diagnostics"
    bl_idname = "BFACW_PT_chat_status"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Coworker"
    bl_parent_id = "BFACW_PT_chat_panel"
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
        is_harness = (prefs.operating_mode == "EXTERNAL_HARNESS")

        # ── Liveness dots ──
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

        # ── Restart button ──
        if state.mcp_server_running or (is_harness and mcp_to_blender_server.is_running()):
            restart_row = layout.row()
            restart_row.scale_y = 0.8
            restart_row.operator("bfacw.agent_restart", icon="LOOP_BACK", text="Restart Coworker")

        # ── Mode indicator ──
        if not is_harness:
            mode_row = layout.row(align=True)
            if prefs.operating_mode == "REMOTE_API":
                mode_row.label(text="Mode: Remote API", icon='URL')
            else:
                mode_row.label(text="Mode: Local LLM", icon='CONSOLE')

        # ── Tool count ──
        if not is_harness and state.mcp_server_running:
            if state.tool_count > 0:
                layout.label(text="Tools: {:d} loaded".format(state.tool_count), icon='MODIFIER')
            else:
                layout.label(
                    text="Tools: none loaded",
                    icon='WARNING',
                )

        # ── LLM info ──
        if not is_harness:
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

        # ── Export/Copy Log (advanced) ──
        if not is_harness:
            layout.separator()
            row = layout.row(align=True)
            row.scale_y = 1.2
            row.operator("bfacw.export_session_log", icon="EXPORT", text="Export Log")
            row.operator("bfacw.copy_session_log", icon="COPYDOWN", text="Copy Log")

        # ── External Harness MCP server controls ──
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
        is_harness = (prefs.operating_mode == "EXTERNAL_HARNESS")

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
            row.operator("bfacw.chat_clear", icon="X", text="New Thread")
        else:
            layout.label(text="Chat handled by external MCP client.", icon='INFO')

        layout.separator()

        # Conversation summary — latest message first.
        history = state.conversation_history
        display_history = [m for m in history if m.get("role") != "system"]
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
                else:
                    preview = content[:80] + "..." if content and len(content) > 80 else (content or "")
                _draw_multiline(box, "[{:s}] {:s}".format(role, preview))
        else:
            layout.label(text="No conversation yet.", icon='INFO')


# ---------------------------------------------------------------------------
# Helpers

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
# Registration helpers

_classes = (
    ChatHistoryProperties,
    BFACW_OT_chat_send,
    BFACW_OT_chat_clear,
    BFACW_OT_chat_stop,
    BFACW_OT_chat_queue_send,
    BFACW_OT_export_session_log,
    BFACW_OT_copy_session_log,
    BFACW_OT_queue_clear,
    BFACW_OT_queue_show,
    BFACW_OT_mention_search,
    BFACW_OT_mention_insert,
    BFACW_OT_edit_rules,
    BFACW_OT_reload_rules,
    BFACW_OT_agent_start,
    BFACW_OT_agent_stop,
    BFACW_OT_agent_restart,
    BFACW_OT_copy_message,

    BFACW_PT_chat_panel,
    BFACW_PT_chat_queue,
    BFACW_PT_chat_status,
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