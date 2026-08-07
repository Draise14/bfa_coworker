# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Add-on preferences definition and runtime state tracking.
"""

__all__ = (
    "_State",
    "_BFACW_Preferences",
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
from .shared import (
    PORT_MIN,
    PORT_MAX,
    AUTOSTART_DELAY,
    STATE_OFFLINE_ERROR_MESSAGE,
    MODEL_PRESET_ITEMS,
    REMOTE_PROVIDER_ITEMS,
    BFACW_DEBUG,
    effective_ports,
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


class _BFACW_Preferences(bpy.types.AddonPreferences):  # type: ignore[misc]
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
        default=str(Path.home() / "bfa_coworker_models"),
        subtype='DIR_PATH',
    )

    # ── Model Preset ─────────────────────────────────────────────────

    def _update_model_preset(self, _context: bpy.types.Context) -> None:
        """When user picks a preset, auto-fill repo_id, filename, ctx_size, and max_tokens."""
        llm = get_llm_manager()
        preset = llm.get_preset_by_id(self.model_preset)
        if preset is not None:
            self.model_repo_id = preset.repo_id
            self.model_filename = preset.filename
            # Auto-set context window from preset (capped at 65536 for consumer GPU safety).
            self.local_ctx_size = min(preset.context_window, 65536)
            # Auto-set max output tokens from preset.
            self.local_max_tokens = preset.max_tokens
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
            cfg.local_ctx_size = self.local_ctx_size
            cfg.local_max_tokens = self.local_max_tokens
            cfg.hf_token = self.hf_token
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

    # ── Individual Port Overrides ─────────────────────────────────

    bridge_port: IntProperty(  # type: ignore[valid-type]
        name="Bridge Port",
        description=(
            "Override the bridge server port. "
            "0 = use default (9876) + offset"
        ),
        default=0,
        min=0,
        max=65535,
    )
    mcp_port: IntProperty(  # type: ignore[valid-type]
        name="MCP Port",
        description=(
            "Override the MCP HTTP server port. "
            "0 = use default (9191) + offset"
        ),
        default=0,
        min=0,
        max=65535,
    )
    llm_port: IntProperty(  # type: ignore[valid-type]
        name="LLM Port",
        description=(
            "Override the LLM server port. "
            "0 = use default (8081) + offset"
        ),
        default=0,
        min=0,
        max=65535,
    )

    local_ctx_size: IntProperty(  # type: ignore[valid-type]
        name="Context Window Size",
        description=(
            "Context window size (in tokens) passed to llama-server via --ctx-size.\n"
            "Larger values allow longer conversations but use more RAM.\n"
            "Decrease if you get Jinja errors (context overflow) or out-of-memory.\n"
            "Default 32768 works for most models. Gemma 4 supports up to 262144."
        ),
        default=32768,
        min=4096,
        max=262144,
        step=1024,
        subtype='UNSIGNED',
    )

    local_max_tokens: IntProperty(  # type: ignore[valid-type]
        name="Max Output Tokens",
        description=(
            "Maximum tokens per LLM API call (reasoning + content + tool calls).\n"
            "Reasoning models need more headroom. If output gets cut off, raise this.\n"
            "Auto-continue will retry if the model hits this limit mid-generation."
        ),
        default=16384,
        min=512,
        max=131072,
        step=1024,
        subtype='UNSIGNED',
    )

    hf_token: StringProperty(  # type: ignore[valid-type]
        name="HuggingFace Token",
        default="",
        subtype='PASSWORD',
        description=(
            "Optional HuggingFace token for gated models.\n"
            "Get one at https://huggingface.co/settings/tokens\n"
            "Only needed for models that require authentication."
        ),
    )

    # ── Generation (Tier 5) Properties ──────────────────────────────

    gen_backend: EnumProperty(  # type: ignore[valid-type]
        name="Generation Backend",
        description="Which backend to use for image/video/audio generation",
        items=[
            ("local", "Local (Built-in)", "Run generative models locally via diffusers/torch"),
            ("pallaidium", "Pallaidium Bridge", "Bridge to Pallaidium addon if installed"),
            ("comfyui", "ComfyUI", "Connect to a local ComfyUI server"),
            ("remote", "Remote API", "Use a remote OpenAI-compatible generation API"),
        ],
        default="local",
    )

    gen_models_dir: StringProperty(  # type: ignore[valid-type]
        name="Gen Models Directory",
        description="Directory where generative AI models are downloaded and cached",
        default=str(Path.home() / "bfa_coworker_gen_models"),
        subtype='DIR_PATH',
    )

    gen_output_dir: StringProperty(  # type: ignore[valid-type]
        name="Output Directory",
        description="Directory where generated media (images, videos, audio) is saved",
        default=str(Path.home() / "bfa_coworker_generated"),
        subtype='DIR_PATH',
    )

    gen_auto_download: BoolProperty(  # type: ignore[valid-type]
        name="Auto-Download Models",
        description="Automatically download generative models when first used",
        default=True,
    )

    gen_comfyui_url: StringProperty(  # type: ignore[valid-type]
        name="ComfyUI URL",
        description="URL of the ComfyUI server (default: http://127.0.0.1:8188)",
        default="http://127.0.0.1:8188",
    )

    gen_remote_url: StringProperty(  # type: ignore[valid-type]
        name="Remote Gen API URL",
        description="Base URL for the remote generation API (OpenAI /v1 dialect)",
        default="",
    )

    gen_remote_key: StringProperty(  # type: ignore[valid-type]
        name="Remote Gen API Key",
        default="",
        subtype='PASSWORD',
        description="API key for the remote generation service",
    )

    def _draw_effective_ports(self, box) -> None:
        """Draw the current effective port values as read-only labels."""
        bridge, mcp, llm = effective_ports(self)
        col = box.column(align=True)
        col.label(text="Effective:  Bridge {:d}  |  MCP {:d}  |  LLM {:d}".format(
            bridge, mcp, llm))

    def draw(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout

        # ── LLM Configuration ─────────────────────────────────────────
        box = layout.box()
        box.label(text="LLM Configuration", icon='SETTINGS')
        box.prop(self, "llm_mode", expand=True)

        if self.llm_mode == "local":
            # ── llama-server binary ──────────────────────────────────
            llm = get_llm_manager()
            llm_state = llm.get_state()
            llama_found = llm.find_llama_server()
            row = box.row(align=True)
            if llama_found:
                row.label(text="llama-server: Installed", icon='CHECKMARK')
            else:
                row.label(text="llama-server: Not installed", icon='ERROR')
                row.operator(
                    "bfacw.download_llama_server",
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
                        "bfacw.select_preset",
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
            llm_state = llm.get_state()

            # Determine download button state.
            models_dir = Path(self.downloaded_models_dir) if self.downloaded_models_dir else (Path.home() / "bfa_coworker_models")
            model_file = models_dir / self.model_filename if self.model_filename else None
            model_exists = model_file and model_file.exists()

            if llm_state.download_active:
                btn_text = "Downloading \u2026"
                btn_icon = 'RENDERLAYERS'
                btn_enabled = False
            elif model_exists:
                btn_text = "Already Downloaded"
                btn_icon = 'CHECKMARK'
                btn_enabled = False
            elif llm_state.is_running:
                btn_text = "Model Running"
                btn_icon = 'CONSOLE'
                btn_enabled = False
            else:
                btn_text = "Download Model"
                btn_icon = "IMPORT"
                btn_enabled = True

            row = box.row(align=True)
            row.operator("bfacw.download_model", icon=btn_icon, text=btn_text)
            if not btn_enabled:
                row.enabled = False
            # Show a cancel button while a download is active.
            if llm_state.download_active:
                row.operator("bfacw.cancel_download", icon='CANCEL', text="Cancel")

            # Always show progress/error areas.
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
            row.operator("bfacw.scan_existing_models", icon="FILE_REFRESH", text="Scan")
            row.operator("bfacw.open_models_dir", icon="FILE_FOLDER", text="Open Folder")
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
            box.prop(self, "local_max_tokens")
            box.prop(self, "hf_token")

        else:
            # ── Remote Provider ─────────────────────────────────────
            llm = get_llm_manager()
            box.label(text="Provider", icon='WORLD')
            box.prop(self, "remote_provider")

            # Show provider description when a known provider is selected.
            if self.remote_provider != "_custom":
                provider = llm.get_remote_provider_by_id(self.remote_provider)
                if provider is not None:
                    for line in provider.description.split("\n"):
                        box.label(text=line, icon='INFO')

            # ── API URL & Key ───────────────────────────────────────
            box.prop(self, "remote_api_url")
            box.prop(self, "remote_api_key")

            row = box.row(align=True)
            row.label(text="API Key Help:", icon='HELP')
            if self.remote_provider != "_custom":
                provider = llm.get_remote_provider_by_id(self.remote_provider)
                if provider is not None:
                    row.label(text=provider.api_key_help)
                else:
                    row.label(text="Enter your API key for the remote service")
            else:
                row.label(text="Enter your API key for the remote service")

            # ── Model ───────────────────────────────────────────────
            box.label(text="Model", icon='VIEWZOOM')
            box.prop(self, "remote_model")
            row = box.row(align=True)
            row.operator("bfacw.refresh_remote_models", icon="FILE_REFRESH", text="Refresh Models")
            row.operator("bfacw.open_model_browser", icon="URL", text="Browse Models")

            # Show fetch status.
            if self.remote_models_count > 0:
                box.label(
                    text="{:d} models available from the API".format(self.remote_models_count),
                    icon='CHECKMARK',
                )
            if self.remote_models_fetch_error:
                box.label(text=self.remote_models_fetch_error, icon='ERROR')

            # ── Test Connection ─────────────────────────────────────
            row = box.row()
            row.operator("bfacw.test_remote_api", icon="URL")

        # ── Generation (Tier 5) ────────────────────────────────────────
        gen_box = layout.box()
        gen_box.label(text="Generative AI (Image / Video / Audio)", icon='RENDER_RESULT')
        gen_box.prop(self, "gen_backend")

        if self.gen_backend == "local":
            gen_box.label(
                text="Models are downloaded from HuggingFace on first use.",
                icon='INFO',
            )
            gen_box.prop(self, "gen_auto_download")
            gen_box.prop(self, "gen_models_dir")
            gen_box.prop(self, "gen_output_dir")

            # Show available plugins.
            try:
                from .gen_plugins import get_plugins_by_type
                for mtype, label, icon in [
                    ("image", "Image Models", 'IMAGE_DATA'),
                    ("video", "Video Models", 'SEQUENCE'),
                    ("audio", "Audio Models", 'SPEAKER'),
                    ("text", "Text Models", 'TEXT'),
                ]:
                    plugins = get_plugins_by_type(mtype)
                    if plugins:
                        gen_box.label(
                            text="{:s}: {:d} available".format(label, len(plugins)),
                            icon=icon,
                        )
            except Exception:
                pass

        elif self.gen_backend == "pallaidium":
            gen_box.label(
                text="Pallaidium addon must be installed and enabled separately.",
                icon='INFO',
            )
            gen_box.label(
                text="Models will be discovered from Pallaidium's plugin registry.",
                icon='BLANK1',
            )

        elif self.gen_backend == "comfyui":
            gen_box.prop(self, "gen_comfyui_url")
            gen_box.label(
                text="ComfyUI must be running with API enabled.",
                icon='INFO',
            )

        elif self.gen_backend == "remote":
            gen_box.prop(self, "gen_remote_url")
            gen_box.prop(self, "gen_remote_key")

        # ── Agent Control ─────────────────────────────────────────────
        box = layout.box()
        box.label(text="Agent Control", icon='WORKSPACE')
        box.prop(self, "agent_autostart")
        row = box.row()
        row.operator("bfacw.ping_agent", icon="FILE_REFRESH", text="Check Status")
        # Lazy import to avoid circular dependency.
        from . import operators_agent as _oa
        ping = _oa._BFACW_OT_ping_agent._result
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

        # ── Advanced Port Settings ──────────────────────────────────────
        port_box = layout.box()
        port_box.label(text="Advanced Port Settings", icon='SETTINGS')
        port_box.prop(self, "port_offset")
        self._draw_effective_ports(port_box)
        row = port_box.row()
        row.prop(self, "bridge_port")
        row.prop(self, "mcp_port")
        row.prop(self, "llm_port")

        # ── Diagnostics (debug only, behind flag) ───────────────────────
        if BFACW_DEBUG:
            diag_box = layout.box()
            diag_box.label(text="🛠️ Diagnostics", icon='INFO')
            diag_box.label(
                text="Temporary debug tools — hidden when BFACW_DEBUG=False",
                icon='BLANK1',
            )
            row = diag_box.row()
            row.operator("bfacw.check_ports", icon="FILE_REFRESH", text="Check Ports")
            row.operator("bfacw.ping_agent", icon="FILE_REFRESH", text="Diagnose")
            # ── Benchmark tests ──────────────────────────────────────
            diag_box.label(text="Benchmarks (send test prompts to agent)", icon='RENDER_RESULT')
            bench_row = diag_box.row(align=True)
            bench_row.operator("bfacw.benchmark_objects", icon="MESH_CUBE", text="Objects")
            bench_row.operator("bfacw.benchmark_scene", icon="SCENE_DATA", text="Scene")
            bench_row.operator("bfacw.benchmark_animation", icon="ANIM", text="Animation")
            bench_row.operator("bfacw.benchmark_collections", icon="OUTLINER_COLLECTION", text="Collections")
            # Show check_ports results inline.
            from . import operators_agent as _oa_check
            check_result = getattr(_oa_check._BFACW_OT_check_ports, "_result", None)
            if check_result:
                for label_key in [("bridge", "Bridge"), ("mcp", "MCP"), ("llm", "LLM")]:
                    available = check_result.get(label_key[0], False)
                    diag_box.label(
                        text="{:s}: {:s}".format(
                            label_key[1],
                            "Available" if available else "In Use",
                        ),
                        icon="CHECKMARK" if available else "ERROR",
                    )
            # Show ping results inline (same as Agent Control).
            ping = _oa._BFACW_OT_ping_agent._result
            if ping:
                status_icon = "CHECKMARK" if ping.get("all_ok") else "ERROR"
                for key, label in [
                    ("bridge_server", "Bridge"),
                    ("mcp_server", "MCP"),
                    ("llm_health", "LLM"),
                    ("llm_chat", "Chat"),
                ]:
                    val = ping.get(key, "—")
                    diag_box.label(
                        text="{:<6s} {:s}".format(label + ":", val),
                        icon=status_icon if val.startswith("OK") else "ERROR",
                    )