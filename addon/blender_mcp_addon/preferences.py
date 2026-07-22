# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Add-on preferences definition and runtime state tracking.
"""

__all__ = (
    "_State",
    "_BlenderMCPPreferences",
)

import bpy  # pylint: disable=import-error
from bpy.props import (
    BoolProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
    EnumProperty,
)  # pylint: disable=import-error

from pathlib import Path

from . import mcp_to_blender_server
from .shared import (
    PORT_MIN,
    PORT_MAX,
    AUTOSTART_DELAY,
    STATE_OFFLINE_ERROR_MESSAGE,
    MODEL_PRESET_ITEMS,
    REMOTE_PROVIDER_ITEMS,
    get_llm_manager,
)


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
        cls.startup_info_set(STATE_OFFLINE_ERROR_MESSAGE)
        if bpy.app.background:
            print("Error: {:s}".format(STATE_OFFLINE_ERROR_MESSAGE))
            print("  Use --online-mode to enable online access from the command line")
        return False


class _BlenderMCPPreferences(bpy.types.AddonPreferences):  # type: ignore[misc]
    bl_idname = __package__

    host: StringProperty(  # type: ignore[valid-type]
        name="Host",
        default=mcp_to_blender_server.DEFAULT_HOST,
    )
    port: IntProperty(  # type: ignore[valid-type]
        name="Port",
        default=mcp_to_blender_server.DEFAULT_PORT,
        min=PORT_MIN,
        max=PORT_MAX,
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
        default=AUTOSTART_DELAY,
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
        llm = get_llm_manager()
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
        items=MODEL_PRESET_ITEMS,
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
        default="https://openrouter.ai/api",
        description="Base URL for the OpenAI-compatible API endpoint",
    )

    remote_api_key: StringProperty(  # type: ignore[valid-type]
        name="API Key",
        default="",
        subtype='PASSWORD',
        description="Your API key. Get one at openrouter.ai/keys",
    )

    # ── Remote Provider ────────────────────────────────────────────

    def _update_remote_provider(self, _context: bpy.types.Context) -> None:
        """When user picks a provider, auto-fill the API URL."""
        llm = get_llm_manager()
        provider = llm.get_remote_provider_by_id(self.remote_provider)
        if provider is not None:
            self.remote_api_url = provider.base_url
            # Sync to llm_manager config.
            cfg = llm.get_config()
            cfg.remote_api_url = provider.base_url
            llm.set_config(cfg)

    remote_provider: EnumProperty(  # type: ignore[valid-type]
        name="Provider",
        description="Select a remote API provider. Auto-fills the API URL",
        items=REMOTE_PROVIDER_ITEMS,
        update=_update_remote_provider,
        default="openrouter",
    )

    # ── Remote Model ───────────────────────────────────────────────

    remote_model: StringProperty(  # type: ignore[valid-type]
        name="Model Name",
        default="",
        description=(
            "Model ID to use for completions (e.g. 'openai/gpt-4o').\n"
            "Browse models at openrouter.ai/models"
        ),
    )

    remote_models_count: IntProperty(  # type: ignore[valid-type]
        name="Models Available",
        default=0,
    )

    remote_models_fetch_error: StringProperty(  # type: ignore[valid-type]
        name="",
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
            self._draw_local_llm_config(box)
        else:
            self._draw_remote_api_config(box)

        # ── Agent Control ─────────────────────────────────────────────
        box = layout.box()
        box.label(text="Agent Control", icon='PLAY')
        row = box.row()
        row.prop(self, "agent_autostart")
        row = box.row(align=True)
        row.operator("blmcp.server_start", icon='PLAY')
        row.operator("blmcp.server_stop", icon='PAUSE')
        row = box.row(align=True)
        row.operator("blmcp.ping_agent", icon='VIEWZOOM')

        # Show ping results if available.
        self._draw_ping_result(box)

        # ── Startup State ─────────────────────────────────────────────
        if _State.autostart_error:
            box = layout.box()
            box.label(text="Startup State", icon='ERROR')
            box.label(text=_State.autostart_error, icon='INFO')

    def _draw_local_llm_config(self, box) -> None:
        """Draw the local LLM configuration section."""
        llm = get_llm_manager()
        state = llm.get_state()

        # ── Model Presets (categorized) ───────────────────────────────
        sub = box.box()
        sub.label(text="Recommended Models", icon='SEO')
        presets = llm.get_presets()

        # Helper to draw a preset button row.
        def _draw_preset_row(parent, label_text, icon):
            row = parent.row(align=True)
            row.label(text=label_text, icon=icon)
            row.operator("blmcp.select_preset", text="", icon='LAYER_ACTIVE').preset_id = ""

        # Flagship section.
        flagship = [p for p in presets if p.capability == "Flagship"]
        if flagship:
            sub.separator()
            sub.label(text="Flagship (24 GB+ VRAM)", icon='SORTALPHA')
            for p in flagship:
                row = sub.row(align=True)
                op = row.operator("blmcp.select_preset", text=p.name, icon='LAYER_ACTIVE')
                op.preset_id = p.id
                row.label(text="RAM: {:s} | Disk: {:s}".format(p.ram_gb, p.disk_gb))

        # Mid-Range section.
        mid = [p for p in presets if p.capability == "Mid"]
        if mid:
            sub.separator()
            sub.label(text="Mid-Range (12-20 GB VRAM)", icon='SORTALPHA')
            for p in mid:
                row = sub.row(align=True)
                op = row.operator("blmcp.select_preset", text=p.name, icon='LAYER_ACTIVE')
                op.preset_id = p.id
                row.label(text="RAM: {:s} | Disk: {:s}".format(p.ram_gb, p.disk_gb))

        # Lightweight section.
        light = [p for p in presets if p.capability == "Light"]
        if light:
            sub.separator()
            sub.label(text="Lightweight (<= 8 GB VRAM)", icon='SORTALPHA')
            for p in light:
                row = sub.row(align=True)
                op = sub.row(align=True).operator("blmcp.select_preset", text=p.name, icon='LAYER_ACTIVE')
                op.preset_id = p.id
                sub.row(align=True).label(text="RAM: {:s} | Disk: {:s}".format(p.ram_gb, p.disk_gb))

        # ── Preset dropdown (fallback) ────────────────────────────────
        sub.separator()
        sub.label(text="Custom Model", icon='FILE')
        sub.prop(self, "model_preset", text="")
        if self.model_preset_info:
            sub.label(text=self.model_preset_info, icon='INFO')

        # ── Advanced Settings ─────────────────────────────────────────
        sub.separator()
        sub.label(text="Advanced Settings", icon='SETTINGS')
        sub.prop(self, "model_repo_id")
        sub.prop(self, "model_filename")
        sub.prop(self, "downloaded_models_dir")
        sub.prop(self, "local_ctx_size")

        # ── Existing Model Scan ───────────────────────────────────────
        sub.separator()
        row = sub.row(align=True)
        row.operator("blmcp.scan_existing_models", icon='FILE_FOLDER')
        row.operator("blmcp.open_hf_cache", icon='URL')
        row.operator("blmcp.clear_hf_cache", icon='CANCEL')

        if self.existing_model_path:
            sub.label(
                text="Using existing: {:s}".format(self.existing_model_path),
                icon='CHECKBOX_HLT',
            )

        # ── Download & Start ──────────────────────────────────────────
        box.separator()
        row = box.row(align=True)
        if state.is_running:
            row.operator("blmcp.stop_llm", icon='PAUSE', text="Stop Local LLM")
            status_text = "llama-server is running on port {:d}".format(state.port)
            row.label(text=status_text, icon='CHECKBOX_HLT')
        elif state.error:
            row.operator("blmcp.download_model", icon='FILE', text="Download & Start")
            row.operator("blmcp.start_llm", icon='PLAY', text="Start Local LLM")
            box.label(text=state.error, icon='ERROR')
        else:
            row.operator("blmcp.download_model", icon='FILE', text="Download & Start")
            row.operator("blmcp.start_llm", icon='PLAY', text="Start Local LLM")

        # ── Download Progress ─────────────────────────────────────────
        if state.download_progress:
            box.label(text=state.download_progress, icon='INFO')

        # ── llama-server download ─────────────────────────────────────
        box.separator()
        box.operator("blmcp.download_llama_server", icon='IMPORT')
        row = box.row()
        row.label(text="llama-server path:", icon='FILE')
        row.prop(self, "llama_path", text="")

    def _draw_remote_api_config(self, box) -> None:
        """Draw the remote API configuration section."""
        box.separator()
        box.prop(self, "remote_provider")
        box.prop(self, "remote_api_url")
        box.prop(self, "remote_api_key")

        row = box.row(align=True)
        row.operator("blmcp.test_remote_api", icon='VIEWZOOM')
        row.operator("blmcp.refresh_remote_models", icon='FILE_REFRESH')
        row.operator("blmcp.open_model_browser", icon='URL')

        if self.remote_models_count > 0:
            box.label(
                text="{:d} models available".format(self.remote_models_count),
                icon='INFO',
            )
        if self.remote_models_fetch_error:
            box.label(text=self.remote_models_fetch_error, icon='ERROR')

        box.prop(self, "remote_model")

        # ── Agent Control (also shown in remote mode) ─────────────

    def _draw_ping_result(self, box) -> None:
        """Draw the ping result indicators."""
        # Lazy import to avoid circular dependency.
        from . import operators_agent as _oa
        ping = _oa._BLMCP_OT_ping_agent._result
        if ping:
            status_icon = "CHECKBOX_HLT"
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