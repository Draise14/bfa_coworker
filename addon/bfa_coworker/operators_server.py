# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Operators for starting/stopping the MCP bridge server.
"""

__all__ = (
    "_BFACW_OT_server_start",
    "_BFACW_OT_server_stop",
    "_autostart_timer",
    "_cli_execute_handler",
)

import bpy  # pylint: disable=import-error

from . import mcp_to_blender_server
from .preferences import _State
from .shared import STATE_OFFLINE_ERROR_MESSAGE, effective_ports


class _BFACW_OT_server_start(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.server_start"
    bl_label = "Start Bridge Server"
    bl_description = "Start the MCP socket bridge server that the MCP server can connect to"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import execute_interactive

        # Timers do not fire in background mode. Use the CLI command instead:
        # `blender --background file.blend --command bfa_coworker`.
        if bpy.app.background:
            self.report({"ERROR"}, "Use `--command bfa_coworker` to start the MCP bridge server in background mode")
            return {"CANCELLED"}
        if not _State.startup_online_ok_or_error():
            self.report({"ERROR"}, STATE_OFFLINE_ERROR_MESSAGE)
            return {"CANCELLED"}
        # Clear any stale auto-start error so it does not persist in the UI.
        _State.startup_info_clear()
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        mcp_to_blender_server.timer_internal_vars_calc(
            active=prefs.timer_interval_active,
            idle=prefs.timer_interval_idle,
            idle_delay=prefs.timer_interval_idle_delay,
        )
        mcp_to_blender_server.use_log = prefs.use_log
        mcp_to_blender_server.log_level = prefs.log_level
        try:
            mcp_to_blender_server.start(prefs.host, _bridge_port)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _State.startup_info_set_from_exception(ex)
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}
        bpy.app.timers.register(
            execute_interactive.run,
            first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
            persistent=True)
        self.report({"INFO"}, "Bridge server started on {:s}:{:d}".format(prefs.host, _bridge_port))
        return {"FINISHED"}


class _BFACW_OT_server_stop(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.server_stop"
    bl_label = "Stop Server"
    bl_description = "Stop the MCP Bridge Server"

    def execute(self, context: bpy.types.Context) -> set[str]:
        del context
        from . import execute_interactive

        # Clear any stale auto-start error so it does not persist in the UI.
        _State.startup_info_clear()
        mcp_to_blender_server.stop()
        if bpy.app.timers.is_registered(execute_interactive.run):
            bpy.app.timers.unregister(execute_interactive.run)
        self.report({"INFO"}, "MCP bridge server stopped")
        return {"FINISHED"}


def _autostart_timer() -> None:
    """
    Deferred timer callback that starts the server when ``use_autostart``
    is enabled. Runs after a delay to avoid slowing down Blender's startup.
    """
    from . import execute_interactive

    if not _State.startup_online_ok_or_error():
        return
    prefs = bpy.context.preferences.addons[__package__].preferences
    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
    mcp_to_blender_server.timer_internal_vars_calc(
        active=prefs.timer_interval_active,
        idle=prefs.timer_interval_idle,
        idle_delay=prefs.timer_interval_idle_delay,
    )
    mcp_to_blender_server.use_log = prefs.use_log
    mcp_to_blender_server.log_level = prefs.log_level

    # This isn't expected:
    # - Maybe the operator is explicitly called as part of an automated action.
    # - The user might have set a very long delay for initial startup and
    #   manually enabled before the timer fires.
    # Whatever the case, running multiple servers would cause confusing errors, so don't do it.
    if mcp_to_blender_server.is_running():
        return

    try:
        mcp_to_blender_server.start(prefs.host, _bridge_port)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        _State.startup_info_set_from_exception(ex)
        return

    bpy.app.timers.register(
        execute_interactive.run,
        first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
        persistent=True)


def _cli_execute_handler(argv: list[str]) -> int:
    """
    Callback for the CLI: ``blender -c bfa_coworker``.
    """
    if not _State.startup_online_ok_or_error():
        return 1
    from .cli import cli_execute
    return cli_execute(argv)