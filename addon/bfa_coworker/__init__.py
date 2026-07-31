# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Bforartists Coworker add-on that provides an MCP socket bridge-server.
"""

__all__ = (
    "register",
    "unregister",
)

import bpy  # pylint: disable=import-error
import os

from . import mcp_to_blender_server
from .preferences import _State, _BFACW_Preferences
from .operators_server import (
    _BFACW_OT_server_start,
    _BFACW_OT_server_stop,
    _autostart_timer,
    _cli_execute_handler,
)
from .operators_llm import (
    _BFACW_OT_download_model,
    _BFACW_OT_cancel_download,
    _BFACW_OT_start_llm,
    _BFACW_OT_stop_llm,
    _BFACW_OT_download_llama_server,
    _BFACW_OT_scan_existing_models,
    _BFACW_OT_select_preset,
    _BFACW_OT_select_existing_model,
)
from .operators_agent import (
    _BFACW_OT_test_remote_api,
    _BFACW_OT_refresh_remote_models,
    _BFACW_OT_open_model_browser,
    _BFACW_OT_ping_agent,
    _BFACW_OT_check_ports,
    _BFACW_OT_benchmark_objects,
    _BFACW_OT_benchmark_scene,
    _BFACW_OT_benchmark_animation,
    _BFACW_OT_benchmark_collections,
)
from .operators_hf import (
    _BFACW_OT_open_hf_cache,
    _BFACW_OT_clear_hf_cache,
)
from .shared import (
    effective_ports,
    get_llm_manager,
    get_agent_controller,
)

# Store the CLI handle, only for correct register/unregister.
_cli_commands: list[object] = []

_classes = (
    _BFACW_Preferences,
    _BFACW_OT_server_start,
    _BFACW_OT_server_stop,
    _BFACW_OT_download_model,
    _BFACW_OT_cancel_download,
    _BFACW_OT_start_llm,
    _BFACW_OT_stop_llm,
    _BFACW_OT_download_llama_server,
    _BFACW_OT_scan_existing_models,
    _BFACW_OT_select_existing_model,
    _BFACW_OT_select_preset,
    _BFACW_OT_test_remote_api,
    _BFACW_OT_refresh_remote_models,
    _BFACW_OT_open_model_browser,
    _BFACW_OT_ping_agent,
    _BFACW_OT_check_ports,
    _BFACW_OT_benchmark_objects,
    _BFACW_OT_benchmark_scene,
    _BFACW_OT_benchmark_animation,
    _BFACW_OT_benchmark_collections,
    _BFACW_OT_open_hf_cache,
    _BFACW_OT_clear_hf_cache,
)


def register() -> None:
    # Start file-based logging as early as possible so all subsequent
    # print() diagnostics are captured to disk.
    from . import log
    log.install_print_tee()
    # Coalesce Blender 5.3+ "Policy Violation" warnings from vendored deps
    # into a single summary line instead of a console flood.
    log.install_policy_warning_filter()

    # Clear stale CLI command handles from a previous registration.
    _cli_commands.clear()

    for cls in _classes:
        bpy.utils.register_class(cls)
    _cli_commands.append(bpy.utils.register_cli_command("bfa_coworker", _cli_execute_handler))

    # Register the chat UI modules.
    from . import ui_chat
    ui_chat.register()

    # Defer auto-start so the server does not slow down Blender's startup.
    if not bpy.app.background:
        if not _State.startup_online_ok_or_error():
            return

        prefs = bpy.context.preferences.addons[__package__].preferences
        if prefs.use_autostart:
            bpy.app.timers.register(
                _autostart_timer,
                first_interval=prefs.autostart_delay,
                persistent=True,
            )

        # If agent autostart is also enabled, schedule the full agent startup.
        if prefs.agent_autostart:
            bpy.app.timers.register(
                _autostart_agent_timer,
                first_interval=prefs.autostart_delay + 2.0,
                persistent=True,
            )


def _autostart_agent_timer() -> None:
    """Deferred timer callback that starts the full agent (MCP server + LLM)."""
    from . import ui_chat

    if bpy.app.background:
        return

    prefs = bpy.context.preferences.addons[__package__].preferences
    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)

    print("Agent auto-start: using ports bridge={:d} mcp={:d} llm={:d}".format(
        _bridge_port, _mcp_port, _llm_port))

    # Start the MCP HTTP server.
    _ac = get_agent_controller()
    if not _ac._agent_state.mcp_server_running:
        proc = _ac.start_mcp_server(port=_mcp_port, blender_port=_bridge_port)
        if proc is None:
            print("Agent auto-start: MCP server failed — {:s}".format(_ac._agent_state.error))
            return

    # Start local LLM if configured.
    if prefs.llm_mode == "local":
        _llm = get_llm_manager()
        _llm_cfg = _llm.get_config()
        _llm_cfg.mode = prefs.llm_mode
        _llm_cfg.llama_path = prefs.llama_path
        _llm_cfg.model_repo_id = prefs.model_repo_id
        _llm_cfg.model_filename = prefs.model_filename
        _llm_cfg.downloaded_models_dir = prefs.downloaded_models_dir
        _llm_cfg.local_ctx_size = prefs.local_ctx_size
        _llm_cfg.local_max_tokens = prefs.local_max_tokens
        _llm_cfg.local_port = _llm_port
        _llm.set_config(_llm_cfg)

        llm_state = _llm.get_state()
        if not llm_state.is_running:
            # If an existing model path is set, use it directly.
            existing_path = prefs.existing_model_path
            if existing_path and os.path.isfile(existing_path):
                _llm.start_local_llama(model_path=existing_path)
            else:
                _llm.start_local_llama()

    print("Agent auto-start: full agent running on ports bridge={:d} mcp={:d} llm={:d}".format(
        _bridge_port, _mcp_port, _llm_port))


def unregister() -> None:
    from . import execute_interactive
    from . import ui_chat

    # Clean up subprocesses.
    get_llm_manager().cleanup()
    get_agent_controller().cleanup()

    # Unregister chat UI.
    ui_chat.unregister()

    for cmd in _cli_commands:
        try:
            bpy.utils.unregister_cli_command(cmd)
        except RuntimeError:
            pass
    _cli_commands.clear()

    if bpy.app.timers.is_registered(_autostart_timer):
        bpy.app.timers.unregister(_autostart_timer)

    mcp_to_blender_server.stop()
    if bpy.app.timers.is_registered(execute_interactive.run):
        bpy.app.timers.unregister(execute_interactive.run)
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
