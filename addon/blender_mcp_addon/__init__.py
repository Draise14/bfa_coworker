# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Blender add-on that provides an MCP socket bridge-server.
"""

__all__ = (
    "register",
    "unregister",
)

import bpy  # pylint: disable=import-error
from bpy.props import (
    BoolProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
    EnumProperty,
)  # pylint: disable=import-error

import os
from pathlib import Path

from . import mcp_to_blender_server

_PORT_MIN = 1024
_PORT_MAX = 65535

# Default seconds to wait after registration before auto-starting the server.
# Avoids adding work to Blender's startup sequence.
_AUTOSTART_DELAY = 1.0

# Store the CLI handle, only for correct register/unregister.
_cli_commands: list[object] = []

# This error is shown in the UI & command line when online access isn't enabled.
#
# NOTE(@ideasman42): we could consider `localhost` to be acceptable, this is a grey area
# regarding what counts as "online" or not.
_state_offline_error_message = "Online access must be enabled in the system preferences"


class _State:
    """
    Module-level runtime state that is not persisted across sessions.
    """

    # Communicate to the user if there is a problem.
    # Displayed in the preferences UI when non-empty.
    autostart_error: str = ""

    @classmethod
    def startup_info_set(cls, error: str) -> None:
        """
        Store a startup error message to display in the preferences UI.
        """
        cls.autostart_error = error

    @classmethod
    def startup_info_set_from_exception(cls, ex: Exception) -> None:
        """
        Store a startup exception message to display in the preferences UI
        and print the full traceback to stderr for debugging.
        """
        # NOTE: this is correct but reads like an unhandled exception.
        # import traceback
        # traceback.print_exception(ex)
        cls.autostart_error = str(ex)

    @classmethod
    def startup_info_clear(cls) -> None:
        """
        Clear any startup error so it no longer appears in the preferences UI.
        """
        cls.autostart_error = ""

    @classmethod
    def startup_online_ok_or_error(cls) -> bool:
        """
        Return True when online access is permitted, otherwise store an error and return False.
        """
        if bpy.app.online_access:
            return True
        cls.startup_info_set(_state_offline_error_message)
        if bpy.app.background:
            print("Error: {:s}".format(_state_offline_error_message))
            print("  Use --online-mode to enable online access from the command line")
        return False


# Static preset items for the model_preset EnumProperty.
# Must be a module-level constant — callbacks can fail during class registration.
# Only the "custom" entry and a simple flat list — the UI categorizes them visually.
_MODEL_PRESET_ITEMS: list[tuple[str, str, str]] = [
    ("_custom", "Custom (manual entry)", "Manually specify repo ID and filename"),
    ("mistral_small_24b_q4", "Mistral Small 3.1 24B (Q4_K_M)", "[Mid] 12-16 GB RAM, ~14 GB disk"),
    ("gemma4_26b_q4", "Gemma 4 26B A4B (UD-Q4_K_M)", "[Mid] 16-20 GB RAM, ~17 GB disk"),
    ("gemma3_27b_q4", "Gemma 3 27B (Q4_K_M)", "[Mid] 16-20 GB RAM, ~16 GB disk"),
    ("qwen36_35b_q4", "Qwen3.6 35B A3B (UD-Q4_K_M)", "[Mid] 12-16 GB RAM, ~22 GB disk"),
    ("gpt_oss_20b_q4", "GPT-OSS 20B (Q4_K_M)", "[Mid] 8-12 GB RAM, ~12 GB disk"),
    ("phi4_14b_q4", "Phi-4 14B (Q4_K_M)", "[Mid] 8-12 GB RAM, ~8 GB disk"),
    ("gemma4_26b_q8", "Gemma 4 26B A4B (Q8_0)", "[Flagship] 24-28 GB RAM, ~27 GB disk"),
    ("deepseek_r1_32b_q4", "DeepSeek R1 Distill 32B (Q4_K_M)", "[Flagship] 20-24 GB RAM, ~19 GB disk"),
    ("qwen25_coder_32b_q4", "Qwen 2.5 Coder 32B (Q4_K_M)", "[Flagship] 20-24 GB RAM, ~19 GB disk"),
    ("llama31_8b_q4", "Llama 3.1 8B (Q4_K_M)", "[Light] 4-6 GB RAM, ~5 GB disk"),
    ("qwen35_9b_heretic_q4", "Qwen3.5 9B Claude 4.6 Heretic (Q4_K_M)", "[Light] 6-8 GB RAM, ~6 GB disk"),
    ("qwen3_8b_q4", "Qwen3 8B (Q4_K_M)", "[Light] 4-6 GB RAM, ~5 GB disk"),
    ("qwen3_8b_q8", "Qwen3 8B (Q8_0)", "[Light] 6-8 GB RAM, ~9 GB disk"),
    ("phi4_14b_q3", "Phi-4 14B (Q3_K_M)", "[Light] 6-8 GB RAM, ~6 GB disk"),
]


class _BlenderMCPPreferences(bpy.types.AddonPreferences):  # type: ignore[misc]
    bl_idname = __package__

    host: StringProperty(  # type: ignore[valid-type]
        name="Host",
        default=mcp_to_blender_server.DEFAULT_HOST,
    )
    port: IntProperty(  # type: ignore[valid-type]
        name="Port",
        default=mcp_to_blender_server.DEFAULT_PORT,
        min=_PORT_MIN,
        max=_PORT_MAX,
    )
    use_autostart: BoolProperty(  # type: ignore[valid-type]
        name="Auto Start",
        description=(
            "Automatically start the MCP bridge server when Blender starts.\n"
            "Without this, you must manually start from the preferences UI.\n"
            "(ignored in background mode)"
        ),
        default=True,
    )
    autostart_delay: FloatProperty(  # type: ignore[valid-type]
        name="Auto Start Delay",
        description=(
            "Seconds to wait after Blender starts before auto-starting the server.\n"
            "Avoids adding overhead to Blender's startup sequence"
        ),
        default=_AUTOSTART_DELAY,
        min=0.0,
        max=30.0,
        step=10,
        precision=1,
        subtype="TIME_ABSOLUTE",
    )

    def _update_use_log(self, _context: bpy.types.Context) -> None:
        mcp_to_blender_server.use_log = self.use_log

    use_log: BoolProperty(  # type: ignore[valid-type]
        name="Log",
        description="Print every tool request and response status to the terminal",
        default=False,
        update=_update_use_log,
    )

    def _update_timer_interval_active(self, _context: bpy.types.Context) -> None:
        # Cached on the server module because the timer callback may fire
        # many times a second, avoid slower preferences lookups.
        mcp_to_blender_server.timer_internal_vars_calc(active=self.timer_interval_active)

    timer_interval_active: FloatProperty(  # type: ignore[valid-type]
        name="Timer Interval",
        description="Seconds between queue polling ticks in interactive mode",
        default=0.25,
        min=0.05,
        max=5.0,
        step=1,
        precision=2,
        subtype='TIME_ABSOLUTE',
        update=_update_timer_interval_active,
    )

    def _update_timer_interval_idle(self, _context: bpy.types.Context) -> None:
        # Cached on the server module because the timer callback may fire
        # many times a second, avoid slower preferences lookups.
        mcp_to_blender_server.timer_internal_vars_calc(idle=self.timer_interval_idle)

    timer_interval_idle: FloatProperty(  # type: ignore[valid-type]
        name="Timer Interval Idle",
        description="Seconds between queue polling ticks while idle (no pending work)",
        default=1.0,
        min=0.1,
        max=10.0,
        step=10,
        precision=2,
        subtype='TIME_ABSOLUTE',
        update=_update_timer_interval_idle,
    )

    def _update_timer_interval_idle_delay(self, _context: bpy.types.Context) -> None:
        # Cached on the server module because the timer callback may fire
        # many times a second, avoid slower preferences lookups.
        mcp_to_blender_server.timer_internal_vars_calc(idle_delay=self.timer_interval_idle_delay)

    timer_interval_idle_delay: FloatProperty(  # type: ignore[valid-type]
        name="Idle Delay",
        description="Seconds of inactivity before switching to the idle polling interval",
        default=5.0,
        min=1.0,
        max=60.0,
        step=100,
        precision=1,
        subtype='TIME_ABSOLUTE',
        update=_update_timer_interval_idle_delay,
    )

    # ── LLM Configuration Properties ─────────────────────────────────

    llm_mode: EnumProperty(  # type: ignore[valid-type]
        name="LLM Mode",
        items=[
            ("local", "Local (llama.cpp)", "Run a local LLM via llama-server"),
            ("remote", "Remote API", "Use a remote API like OpenAI or OpenRouter"),
        ],
        default="local",
    )

    llama_path: StringProperty(  # type: ignore[valid-type]
        name="llama-server Path",
        default="",
        subtype='FILE_PATH',
    )

    model_repo_id: StringProperty(  # type: ignore[valid-type]
        name="Model Repo ID",
        default="unsloth/Mistral-Small-3.1-24B-Instruct-2503-GGUF",
    )

    model_filename: StringProperty(  # type: ignore[valid-type]
        name="Model Filename",
        default="Mistral-Small-3.1-24B-Instruct-2503-Q4_K_M.gguf",
    )

    downloaded_models_dir: StringProperty(  # type: ignore[valid-type]
        name="Models Directory",
        default=str(Path.home() / "blender_mcp_models"),
        subtype='DIR_PATH',
    )

    # ── Model Preset ─────────────────────────────────────────────────

    def _update_model_preset(self, _context: bpy.types.Context) -> None:
        """When user picks a preset, auto-fill repo_id and filename."""
        llm = _get_llm_manager()
        preset = llm.get_preset_by_id(self.model_preset)
        if preset is not None:
            self.model_repo_id = preset.repo_id
            self.model_filename = preset.filename
            # Clear existing model path — using preset now.
            self.existing_model_path = ""
            # Build info string for display.
            self.model_preset_info = (
                "Capability: {cap}  |  RAM: {ram}  |  Disk: {disk}\n{desc}"
            ).format(
                cap=preset.capability,
                ram=preset.ram_gb,
                disk=preset.disk_gb,
                desc=preset.description,
            )
            # Sync to llm_manager config immediately.
            cfg = llm.get_config()
            cfg.model_repo_id = preset.repo_id
            cfg.model_filename = preset.filename
            cfg.downloaded_models_dir = self.downloaded_models_dir
            llm.set_config(cfg)
        else:
            self.model_preset_info = ""

    model_preset: EnumProperty(  # type: ignore[valid-type]
        name="Recommended Model",
        description="Select a curated model preset. Picking one auto-fills the repo and filename below",
        items=_MODEL_PRESET_ITEMS,
        update=_update_model_preset,
        default="mistral_small_24b_q4",
    )

    model_preset_info: StringProperty(  # type: ignore[valid-type]
        name="",
        default="",
    )

    # ── Existing Model Selector ──────────────────────────────────────

    existing_model_path: StringProperty(  # type: ignore[valid-type]
        name="Existing Model Path",
        description=(
            "When set, this absolute path is used directly instead of "
            "resolving via repo/filename. Set by selecting an existing model."
        ),
        default="",
        subtype='FILE_PATH',
    )

    remote_api_url: StringProperty(  # type: ignore[valid-type]
        name="API URL",
        default="",
    )

    remote_api_key: StringProperty(  # type: ignore[valid-type]
        name="API Key",
        default="",
        subtype='PASSWORD',
    )

    remote_model: StringProperty(  # type: ignore[valid-type]
        name="Model Name",
        default="",
    )

    agent_autostart: BoolProperty(  # type: ignore[valid-type]
        name="Auto-Start Agent",
        default=False,
    )

    port_offset: IntProperty(  # type: ignore[valid-type]
        name="Port Offset",
        description=(
            "Offset added to all default ports (bridge: 9876, MCP: 9191, LLM: 8081).\n"
            "Increase by 1, 2, 3... if another app is using the same ports"
        ),
        default=0,
        min=0,
        max=100,
    )

    local_ctx_size: IntProperty(  # type: ignore[valid-type]
        name="Context Window Size",
        description=(
            "Context window size (in tokens) passed to llama-server via --ctx-size.\n"
            "Larger values allow longer conversations but use more RAM.\n"
            "Decrease if you get Jinja errors (context overflow) or out-of-memory.\n"
            "Small models (8B) work well at 4096. MoE models can use 8192-16384.\n"
            "Gemma 4 supports up to 262144."
        ),
        default=8192,
        min=2048,
        max=262144,
        step=1024,
        subtype='UNSIGNED',
    )

    def draw(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout

        # ── LLM Configuration ─────────────────────────────────────────
        box = layout.box()
        box.label(text="LLM Configuration", icon='SETTINGS')
        box.prop(self, "llm_mode", expand=True)

        if self.llm_mode == "local":
            # ── llama-server binary ──────────────────────────────────
            llm_state = _get_llm_manager().get_state()
            llama_found = _get_llm_manager().find_llama_server()
            row = box.row(align=True)
            if llama_found:
                row.label(text="llama-server: Installed", icon='CHECKMARK')
            else:
                row.label(text="llama-server: Not installed", icon='ERROR')
                row.operator(
                    "blmcp.download_llama_server",
                    icon="IMPORT",
                    text="Download llama-server",
                )
            if llm_state.download_progress and "llama-server" in llm_state.download_progress:
                box.label(text=llm_state.download_progress, icon='INFO')
                pct = llm_state.download_progress_pct
                if pct > 0:
                    row = box.row(align=True)
                    row.progress(factor=pct / 100.0, type='BAR')

            # ── Recommended Models (presets) ─────────────────────────
            box.label(text="Pick a Model", icon='VIEWZOOM')

            _CATEGORIES = [
                ("flagship", "Flagship (24 GB+ VRAM)", 'SORT_ASC'),
                ("mid_range", "Mid-Range (12-20 GB VRAM — 4090 Sweet Spot)", 'VIEWZOOM'),
                ("lightweight", "Lightweight (\u2264 8 GB VRAM)", 'LIGHT_SUN'),
            ]

            llm = _get_llm_manager()
            all_presets = llm.get_presets()

            for cat_id, cat_label, cat_icon in _CATEGORIES:
                cat_presets = [p for p in all_presets if p.category == cat_id]
                if not cat_presets:
                    continue
                cat_box = box.box()
                cat_box.label(text=cat_label, icon=cat_icon)
                for preset in cat_presets:
                    row = cat_box.row(align=True)
                    op = row.operator(
                        "blmcp.select_preset",
                        text=preset.name,
                        icon='CHECKBOX_HLT'
                        if self.model_preset == preset.identifier
                        else 'CHECKBOX_DEHLT',
                    )
                    op.preset_id = preset.identifier
                    row.label(
                        text="[{:s}] {:s}".format(preset.ram_gb, preset.capability),
                    )

            # Custom model entry.
            box.prop(self, "model_preset", text="Custom Model")
            if self.model_preset != "_custom" and self.model_preset_info:
                info_box = box.box()
                info_box.label(text="Model Information", icon='INFO')
                for line in self.model_preset_info.split("\n"):
                    info_box.label(text=line)

            # ── Download or use existing ─────────────────────────────
            llm_state = _get_llm_manager().get_state()
            if not llm_state.is_running:
                row = box.row(align=True)
                row.operator("blmcp.download_model", icon="IMPORT", text="Download & Start")
                if llm_state.error:
                    box.label(text=llm_state.error, icon="ERROR")
                if llm_state.download_progress:
                    prog_text = llm_state.download_progress
                    if llm_state.download_progress_eta:
                        prog_text = "{:s}  |  {:s}".format(prog_text, llm_state.download_progress_eta)
                    box.label(text=prog_text, icon='INFO')
                    pct = llm_state.download_progress_pct
                    if pct > 0:
                        row = box.row(align=True)
                        row.progress(factor=pct / 100.0, type='BAR')

            # ── Scan for existing models ────────────────────────────
            box.label(text="Or use an existing model:", icon='FILE_FOLDER')
            row = box.row(align=True)
            row.operator("blmcp.scan_existing_models", icon="FILE_REFRESH", text="Scan")
            box.prop(self, "downloaded_models_dir")
            if self.existing_model_path:
                box.label(
                    text="Using: {:s}".format(os.path.basename(self.existing_model_path)),
                    icon='CHECKMARK',
                )

            # ── Current model status ─────────────────────────────────
            if llm_state.is_running:
                box.label(
                    text="Active model: {:s}".format(llm_state.model_name or "Unknown"),
                    icon='CONSOLE',
                )

            # ── Advanced ─────────────────────────────────────────────
            box.label(text="Advanced", icon='SETTINGS')
            box.prop(self, "model_repo_id")
            box.prop(self, "model_filename")
            box.prop(self, "local_ctx_size")
            row = box.row(align=True)
            row.operator("blmcp.open_hf_cache", icon="FILE_FOLDER", text="Hugging Face Cache")
            row.operator("blmcp.clear_hf_cache", icon="TRASH", text="Clear Cache")

        else:
            box.prop(self, "remote_api_url")
            box.prop(self, "remote_api_key")
            box.prop(self, "remote_model")
            row = box.row()
            row.operator("blmcp.test_remote_api", icon="URL")

        # ── Agent Control ─────────────────────────────────────────────
        box = layout.box()
        box.label(text="Agent Control", icon='WORKSPACE')
        box.prop(self, "agent_autostart")

        agent_state = _get_agent_controller()._agent_state
        row = box.row()
        if agent_state.mcp_server_running:
            row.operator("blmcp.agent_stop", icon="CANCEL", text="Stop Agent")
            row.label(text="Agent is running", icon="CHECKMARK")
        else:
            row.operator("blmcp.agent_start", icon="PLAY", text="Start Agent")
            row.label(text="Agent is stopped", icon="X")

        if agent_state.error:
            box.label(text=agent_state.error, icon="ERROR")

        # ── Ping button ────────────────────────────────────────────
        row = box.row()
        row.operator("blmcp.ping_agent", icon="FILE_REFRESH")
        ping = _BLMCP_OT_ping_agent._result
        if ping:
            status_icon = "CHECKMARK" if ping.get("all_ok") else "ERROR"
            for key, label in [
                ("bridge_server", "Bridge"),
                ("mcp_server", "MCP"),
                ("llm_health", "LLM"),
                ("llm_chat", "Chat"),
            ]:
                val = ping.get(key, "—")
                box.label(
                    text="{:<6s} {:s}".format(label + ":", val),
                    icon=status_icon if val.startswith("OK") else "ERROR",
                )


class _BLMCP_OT_server_start(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.server_start"
    bl_label = "Start MCP Bridge Server"
    bl_description = "Start the MCP socket bridge server that the MCP server can connect to"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import execute_interactive

        # Timers do not fire in background mode. Use the CLI command instead:
        # `blender --background file.blend --command blender_mcp`.
        if bpy.app.background:
            self.report({"ERROR"}, "Use `--command blender_mcp` to start the MCP bridge server in background mode")
            return {"CANCELLED"}
        if not _State.startup_online_ok_or_error():
            self.report({"ERROR"}, _state_offline_error_message)
            return {"CANCELLED"}
        # Clear any stale auto-start error so it does not persist in the UI.
        _State.startup_info_clear()
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _mcp_port, _llm_port = _effective_ports(prefs)
        mcp_to_blender_server.timer_internal_vars_calc(
            active=prefs.timer_interval_active,
            idle=prefs.timer_interval_idle,
            idle_delay=prefs.timer_interval_idle_delay,
        )
        mcp_to_blender_server.use_log = prefs.use_log
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
        self.report({"INFO"}, "MCP server started on {:s}:{:d}".format(prefs.host, _bridge_port))
        return {"FINISHED"}


class _BLMCP_OT_server_stop(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.server_stop"
    bl_label = "Stop MCP Server"
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
    _bridge_port, _mcp_port, _llm_port = _effective_ports(prefs)
    mcp_to_blender_server.timer_internal_vars_calc(
        active=prefs.timer_interval_active,
        idle=prefs.timer_interval_idle,
        idle_delay=prefs.timer_interval_idle_delay,
    )
    mcp_to_blender_server.use_log = prefs.use_log

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
    Callback for the CLI: ``blender -c blender_mcp``.
    """
    if not _State.startup_online_ok_or_error():
        return 1
    from .cli import cli_execute
    return cli_execute(argv)


# ---------------------------------------------------------------------------
# Port helper — computes effective ports from defaults + offset

_DEFAULT_BRIDGE_PORT = 9876
_DEFAULT_MCP_PORT = 9191
_DEFAULT_LLM_PORT = 8081


def _effective_ports(prefs) -> tuple[int, int, int]:
    """Return (bridge_port, mcp_port, llm_port) with offset applied."""
    offset = prefs.port_offset if hasattr(prefs, 'port_offset') else 0
    return (
        _DEFAULT_BRIDGE_PORT + offset,
        _DEFAULT_MCP_PORT + offset,
        _DEFAULT_LLM_PORT + offset,
    )


# ---------------------------------------------------------------------------
# Lazy import helpers (avoids circular imports)

def _get_llm_manager():
    """Lazy import of llm_manager module."""
    from . import llm_manager as _m
    return _m


def _get_agent_controller():
    """Lazy import of agent_controller module."""
    from . import agent_controller as _m
    return _m


# ---------------------------------------------------------------------------
# LLM Operators

class _BLMCP_OT_download_model(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.download_model"
    bl_label = "Download Model"
    bl_description = "Download the configured GGUF model via llama-server (auto-downloads with progress in console)"

    _timer: float | None = None
    _thread = None
    _error: str = ""
    _done: bool = False
    _start_msg_shown: bool = False
    _latest_progress: str = ""

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        del event

        llm = _get_llm_manager()
        state = llm.get_state()

        if not self._start_msg_shown:
            self.report({"INFO"}, "Download started — see llama-server console for progress")
            self._start_msg_shown = True

        # Show progress if it changed.
        prog = state.download_progress
        if prog and prog != self._latest_progress:
            self._latest_progress = prog
            if "Error" in prog or "fail" in prog.lower() or "timed out" in prog.lower():
                self.report({"ERROR"}, prog)
            elif "complete" in prog.lower() or "running" in prog.lower():
                self.report({"INFO"}, prog)

        if not self._done:
            # Re-draw preferences so the progress label updates.
            for wm in bpy.data.window_managers:
                for win in wm.windows:
                    for area in win.screen.areas:
                        if area.type == 'PREFERENCES':
                            area.tag_redraw()
            return {'PASS_THROUGH'}

        if self._timer is not None:
            bpy.app.timers.unregister(self._timer)

        if context and context.area:
            context.area.tag_redraw()

        if state.is_running and not state.error:
            self.report({"INFO"}, "Model downloaded and llama-server is running")
            return {"FINISHED"}
        else:
            self.report({"ERROR"}, self._error or "Download failed")
            return {"CANCELLED"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        llm = _get_llm_manager()
        prefs = context.preferences.addons[__package__].preferences

        cfg = llm.get_config()
        cfg.model_repo_id = prefs.model_repo_id
        cfg.model_filename = prefs.model_filename
        cfg.downloaded_models_dir = prefs.downloaded_models_dir
        cfg.local_ctx_size = prefs.local_ctx_size
        _bridge_port, _mcp_port, _llm_port = _effective_ports(prefs)
        cfg.local_port = _llm_port
        llm.set_config(cfg)

        models_dir = Path(prefs.downloaded_models_dir) if prefs.downloaded_models_dir else (Path.home() / "blender_mcp_models")
        model_path = models_dir / prefs.model_filename if prefs.model_filename else None
        if model_path and model_path.exists():
            self.report({"INFO"}, "Model already downloaded at: {:s}".format(str(model_path)))
            return {"FINISHED"}

        self._done = False
        self._error = ""
        self._start_msg_shown = False
        self._latest_progress = ""

        # download_model now launches llama-server which auto-downloads.
        # It returns None immediately — we poll state for completion.
        llm.download_model(progress_callback=None)

        self._timer = bpy.app.timers.register(
            _make_download_poll(self),
            first_interval=0.5,
            persistent=True,
        )

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


def _make_download_poll(op):
    def _poll() -> float | None:
        llm = _get_llm_manager()
        state = llm.get_state()
        if state.is_running or state.error:
            op._done = True
            op._error = state.error
            return None
        for wm in bpy.data.window_managers:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == 'PREFERENCES':
                        area.tag_redraw()
        return 0.5
    return _poll


class _BLMCP_OT_start_llm(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.start_llm"
    bl_label = "Start Local LLM"
    bl_description = "Start the local llama-server with the configured model"

    def execute(self, context: bpy.types.Context) -> set[str]:
        llm = _get_llm_manager()
        prefs = context.preferences.addons[__package__].preferences

        cfg = llm.get_config()
        cfg.llama_path = prefs.llama_path
        cfg.model_repo_id = prefs.model_repo_id
        cfg.model_filename = prefs.model_filename
        cfg.downloaded_models_dir = prefs.downloaded_models_dir
        cfg.local_ctx_size = prefs.local_ctx_size
        _bridge_port, _mcp_port, _llm_port = _effective_ports(prefs)
        cfg.local_port = _llm_port
        llm.set_config(cfg)

        import threading

        def _do_start():
            # If an existing model path is set, use it directly.
            existing_path = prefs.existing_model_path
            if existing_path and os.path.isfile(existing_path):
                llm.start_local_llama(model_path=existing_path)
            else:
                llm.start_local_llama()

        thread = threading.Thread(target=_do_start, daemon=True)
        thread.start()

        self.report({"INFO"}, "Starting llama-server in background...")
        return {"FINISHED"}


class _BLMCP_OT_stop_llm(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.stop_llm"
    bl_label = "Stop Local LLM"
    bl_description = "Stop the local llama-server"

    def execute(self, context: bpy.types.Context) -> set[str]:
        del context
        llm = _get_llm_manager()
        llm.stop_local_llama()
        self.report({"INFO"}, "llama-server stopped")
        return {"FINISHED"}


class _BLMCP_OT_download_llama_server(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.download_llama_server"
    bl_label = "Download llama-server"
    bl_description = "Download and install the llama-server binary from GitHub releases"

    _timer: float | None = None
    _thread: threading.Thread | None = None
    _done: bool = False
    _error: str = ""
    _latest_progress: str = ""

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        del event

        llm = _get_llm_manager()
        state = llm.get_state()

        # Show progress if it changed.
        prog = state.download_progress
        if prog and prog != self._latest_progress:
            self._latest_progress = prog
            if "Error" in prog or "fail" in prog.lower():
                self.report({"ERROR"}, prog)
            elif "installed" in prog.lower() or "already" in prog.lower():
                self.report({"INFO"}, prog)

        if not self._done:
            for wm in bpy.data.window_managers:
                for win in wm.windows:
                    for area in win.screen.areas:
                        if area.type == 'PREFERENCES':
                            area.tag_redraw()
            return {'PASS_THROUGH'}

        if self._timer is not None:
            bpy.app.timers.unregister(self._timer)

        if context and context.area:
            context.area.tag_redraw()

        if self._error:
            self.report({"ERROR"}, self._error)
            return {"CANCELLED"}
        else:
            self.report({"INFO"}, "llama-server downloaded and installed")
            return {"FINISHED"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        llm = _get_llm_manager()

        # Check if already installed.
        existing = llm.find_llama_server()
        if existing:
            self.report({"INFO"}, "llama-server already available at: {:s}".format(existing))
            return {"FINISHED"}

        self._done = False
        self._error = ""
        self._latest_progress = ""

        def _do_download():
            result = llm.download_llama_server()
            if result is None:
                self._error = llm.get_state().error or "Download failed"
            self._done = True

        self._thread = threading.Thread(target=_do_download, daemon=True)
        self._thread.start()

        self._timer = bpy.app.timers.register(
            _make_llama_download_poll(self),
            first_interval=0.5,
            persistent=True,
        )

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


def _make_llama_download_poll(op):
    def _poll() -> float | None:
        if op._done:
            return None
        for wm in bpy.data.window_managers:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == 'PREFERENCES':
                        area.tag_redraw()
        return 0.5
    return _poll


class _BLMCP_OT_scan_existing_models(bpy.types.Operator):  # type: ignore[misc]
    """Scan for existing GGUF models and populate the existing_model_path."""
    bl_idname = "blmcp.scan_existing_models"
    bl_label = "Scan for Models"
    bl_description = "Scan the models directory and HuggingFace cache for GGUF model files"

    _models: list[dict] = []
    _scan_done: bool = False

    def execute(self, context: bpy.types.Context) -> set[str]:
        llm = _get_llm_manager()
        prefs = context.preferences.addons[__package__].preferences

        _BLMCP_OT_scan_existing_models._scan_done = False

        import threading

        def _do_scan():
            models = llm.scan_existing_models(models_dir=prefs.downloaded_models_dir)
            _BLMCP_OT_scan_existing_models._models = models
            _BLMCP_OT_scan_existing_models._scan_done = True

        self.report({"INFO"}, "Scanning for models...")
        thread = threading.Thread(target=_do_scan, daemon=True)
        thread.start()

        # Poll for completion, then show results.
        bpy.app.timers.register(
            _scan_poll_timer(context),
            first_interval=0.25,
            persistent=True,
        )
        return {"FINISHED"}


def _scan_poll_timer(context: bpy.types.Context):
    """Return a timer callback that shows scan results when done."""
    # Capture stable references before the closure.
    wm = context.window_manager

    def _poll() -> float | None:
        if not _BLMCP_OT_scan_existing_models._scan_done:
            return 0.25  # Keep polling
        models = _BLMCP_OT_scan_existing_models._models

        def _show_menu():
            if not models:
                def _empty_menu(_s, _c):
                    _s.layout.label(text="No GGUF models found.", icon='INFO')
                wm.popup_menu(_empty_menu, title="Scan Results", icon='FILE_FOLDER')
            else:
                def _draw_menu(_s, _c):
                    layout = _s.layout
                    layout.label(text="Found {:d} model(s):".format(len(models)), icon='INFO')
                    for m in models:
                        src_icon = 'FILE_FOLDER' if m["source"] == "models_dir" else 'URL'
                        op = layout.operator(
                            "blmcp.select_existing_model",
                            text="[{:s}] {:s} ({:s})".format(m["source"], m["filename"], m["size_gb"]),
                            icon=src_icon,
                        )
                        op.model_path = m["path"]
                wm.popup_menu(_draw_menu, title="Select Existing Model", icon='FILE_FOLDER')

            # Redraw preferences.
            for w in bpy.data.window_managers:
                for win in w.windows:
                    for area in win.screen.areas:
                        if area.type == 'PREFERENCES':
                            area.tag_redraw()

        bpy.app.timers.register(_show_menu, first_interval=0.1)
        return None
    return _poll


class _BLMCP_OT_select_preset(bpy.types.Operator):  # type: ignore[misc]
    """Select a model preset from the categorized visual list."""
    bl_idname = "blmcp.select_preset"
    bl_label = "Select Preset"
    bl_description = "Select this recommended model preset"

    preset_id: StringProperty(  # type: ignore[valid-type]
        name="Preset ID",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        if self.preset_id:
            prefs.model_preset = self.preset_id
            # Trigger the update handler manually since EnumProperty
            # assignment doesn't always fire the callback on all platforms.
            prefs._update_model_preset(context)
        return {"FINISHED"}


class _BLMCP_OT_select_existing_model(bpy.types.Operator):  # type: ignore[misc]
    """Select a model from the scan results and set it as the active model."""
    bl_idname = "blmcp.select_existing_model"
    bl_label = "Use This Model"
    bl_description = "Use the selected model file directly"

    model_path: StringProperty(  # type: ignore[valid-type]
        name="Model Path",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        if not self.model_path or not os.path.isfile(self.model_path):
            self.report({"ERROR"}, "Model file not found: {:s}".format(self.model_path))
            return {"CANCELLED"}

        # Set the existing model path and clear preset selection.
        prefs.existing_model_path = self.model_path
        prefs.model_preset = "_custom"
        # Keep repo_id/filename so the model is identifiable.
        # model_filename is always the basename.
        prefs.model_filename = os.path.basename(self.model_path)
        # Sync to llm_manager config immediately.
        llm = _get_llm_manager()
        cfg = llm.get_config()
        cfg.model_filename = os.path.basename(self.model_path)
        cfg.downloaded_models_dir = prefs.downloaded_models_dir
        cfg.local_ctx_size = prefs.local_ctx_size
        llm.set_config(cfg)
        self.report(
            {"INFO"},
            "Using existing model: {:s}".format(os.path.basename(self.model_path)),
        )
        return {"FINISHED"}


class _BLMCP_OT_test_remote_api(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.test_remote_api"
    bl_label = "Test Connection"
    bl_description = "Test the remote API connection"

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        if not prefs.remote_api_url:
            self.report({"ERROR"}, "No API URL configured")
            return {"CANCELLED"}
        if not prefs.remote_api_key:
            self.report({"ERROR"}, "No API key configured")
            return {"CANCELLED"}

        llm = _get_llm_manager()
        ok = llm.check_remote_api(prefs.remote_api_url, prefs.remote_api_key)
        if ok:
            self.report({"INFO"}, "Remote API connection successful")
        else:
            self.report({"ERROR"}, "Remote API connection failed — check URL and key")
        return {"FINISHED"}


class _BLMCP_OT_ping_agent(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.ping_agent"
    bl_label = "Ping"
    bl_description = "Test connectivity to MCP server and LLM backend"

    _result: dict = {}  # class-level storage for display in draw()

    def execute(self, context: bpy.types.Context) -> set[str]:
        _ac = _get_agent_controller()
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _mcp_port, _llm_port = _effective_ports(prefs)

        def _do_ping():
            _BLMCP_OT_ping_agent._result = _ac.ping_agent(
                mcp_port=_mcp_port, llm_port=_llm_port,
            )

        import threading
        thread = threading.Thread(target=_do_ping, daemon=True)
        thread.start()
        thread.join(timeout=35)

        result = _BLMCP_OT_ping_agent._result

        if not result:
            self.report({"ERROR"}, "Ping timed out or failed")
            return {"CANCELLED"}

        lines = []
        for key, label in [
            ("bridge_server", "Bridge"),
            ("mcp_server", "MCP"),
            ("llm_health", "LLM Health"),
            ("llm_chat", "LLM Chat"),
        ]:
            val = result.get(key, "not tested")
            lines.append("{:s}: {:s}".format(label, val))

        summary = " | ".join(lines)
        if result.get("all_ok"):
            self.report({"INFO"}, "All OK — {:s}".format(summary))
        else:
            self.report({"ERROR"}, summary)

        return {"FINISHED"}


class _BLMCP_OT_open_hf_cache(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.open_hf_cache"
    bl_label = "Open HF Cache"
    bl_description = "Open the HuggingFace cache folder where models are cached"

    def execute(self, context: bpy.types.Context) -> set[str]:
        import os
        hf_home = os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE")
        if hf_home:
            hf_cache = str(os.path.join(hf_home, "hub"))
        else:
            hf_cache = str(os.path.expanduser("~/.cache/huggingface/hub"))
        import webbrowser
        webbrowser.open(hf_cache)
        self.report({"INFO"}, "Opened {:s}".format(hf_cache))
        return {"FINISHED"}


class _BLMCP_OT_clear_hf_cache(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.clear_hf_cache"
    bl_label = "Clear HF Cache"
    bl_description = "Delete all cached HuggingFace models (frees disk space)"

    def execute(self, context: bpy.types.Context) -> set[str]:
        import os
        import shutil
        hf_home = os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE")
        if hf_home:
            hf_cache = os.path.join(hf_home, "hub")
        else:
            hf_cache = os.path.expanduser("~/.cache/huggingface/hub")

        if not os.path.isdir(hf_cache):
            self.report({"INFO"}, "HF cache is already empty")
            return {"FINISHED"}

        # Count what's being deleted.
        total_bytes = 0
        for root, _dirs, files in os.walk(hf_cache):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total_bytes += os.path.getsize(fp)
                except OSError:
                    pass

        try:
            shutil.rmtree(hf_cache)
        except OSError as ex:
            self.report({"ERROR"}, "Failed to clear cache: {:s}".format(str(ex)))
            return {"CANCELLED"}

        # Recreate the empty directory so HF tools don't break.
        os.makedirs(hf_cache, exist_ok=True)

        import math
        if total_bytes > 0:
            size_str = "{:.1f} GB".format(total_bytes / (1024 ** 3)) if total_bytes >= 1024 ** 3 else "{:.0f} MB".format(total_bytes / (1024 ** 2))
            self.report({"INFO"}, "Cleared {:s} from HF cache".format(size_str))
        else:
            self.report({"INFO"}, "HF cache cleared")
        return {"FINISHED"}


_classes = (
    _BlenderMCPPreferences,
    _BLMCP_OT_server_start,
    _BLMCP_OT_server_stop,
    _BLMCP_OT_download_model,
    _BLMCP_OT_start_llm,
    _BLMCP_OT_stop_llm,
    _BLMCP_OT_download_llama_server,
    _BLMCP_OT_scan_existing_models,
    _BLMCP_OT_select_existing_model,
    _BLMCP_OT_select_preset,
    _BLMCP_OT_test_remote_api,
    _BLMCP_OT_ping_agent,
    _BLMCP_OT_open_hf_cache,
    _BLMCP_OT_clear_hf_cache,
)


def register() -> None:
    # Clear stale CLI command handles from a previous registration.
    _cli_commands.clear()

    for cls in _classes:
        bpy.utils.register_class(cls)
    _cli_commands.append(bpy.utils.register_cli_command("blender_mcp", _cli_execute_handler))

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
        if prefs.agent_autostart and prefs.llm_mode == "local":
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
    _bridge_port, _mcp_port, _llm_port = _effective_ports(prefs)

    print("Agent auto-start: using ports bridge={:d} mcp={:d} llm={:d}".format(
        _bridge_port, _mcp_port, _llm_port))

    # Start the blender-mcp HTTP server.
    _ac = _get_agent_controller()
    if not _ac._agent_state.mcp_server_running:
        proc = _ac.start_mcp_server(port=_mcp_port, blender_port=_bridge_port)
        if proc is None:
            print("Agent auto-start: MCP server failed — {:s}".format(_ac._agent_state.error))
            return

    # Start local LLM if configured.
    if prefs.llm_mode == "local":
        _llm = _get_llm_manager()
        _llm_cfg = _llm.get_config()
        _llm_cfg.llama_path = prefs.llama_path
        _llm_cfg.model_repo_id = prefs.model_repo_id
        _llm_cfg.model_filename = prefs.model_filename
        _llm_cfg.downloaded_models_dir = prefs.downloaded_models_dir
        _llm_cfg.local_ctx_size = prefs.local_ctx_size
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
    _get_llm_manager().cleanup()
    _get_agent_controller().cleanup()

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
