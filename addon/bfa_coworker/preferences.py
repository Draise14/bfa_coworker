# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Add-on preferences definition and runtime state tracking.
"""

__all__ = (
    "_State",
    "_BFACW_Preferences",
    "BFACW_OT_pref_tab_select",
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
    AGENT_MODE_ITEMS,
    MCP_SERVER_MODE_ITEMS,
    OPERATING_MODE_ITEMS,
    CHAT_MODE_ITEMS,
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

    def _update_llm_mode(self, _context: bpy.types.Context) -> None:
        """Sync llm_mode to llm_manager config and stop local LLM if switching to remote."""
        llm = get_llm_manager()
        cfg = llm.get_config()
        cfg.mode = self.llm_mode
        cfg.remote_api_url = self.remote_api_url
        cfg.remote_api_key = self.remote_api_key
        cfg.remote_model = self.remote_model
        cfg.llama_path = self.llama_path
        cfg.model_repo_id = self.model_repo_id
        cfg.model_filename = self.model_filename
        cfg.downloaded_models_dir = self.downloaded_models_dir
        cfg.local_ctx_size = self.local_ctx_size
        cfg.local_max_tokens = self.local_max_tokens
        llm.set_config(cfg)
        # If switching to remote, stop any running local LLM.
        if self.llm_mode == "remote":
            llm.stop_local_llama()

    llm_mode: EnumProperty(  # type: ignore[valid-type]
        name="LLM Mode",
        items=[
            ("local", "Local (llama.cpp)", "Run a local LLM via llama-server"),
            ("remote", "Remote API", "Use a remote API like OpenAI or OpenRouter"),
        ],
        default="local",
        update=_update_llm_mode,
    )

    # ── Unified Operating Mode ──────────────────────────────────────────

    def _update_operating_mode(self, _context: bpy.types.Context) -> None:
        """Sync operating_mode to agent_mode and llm_mode, and switch to the relevant tab."""
        if self.operating_mode == "LOCAL_LLM":
            self.agent_mode = "SELF_CONTAINED"
            self.llm_mode = "local"
            self.pref_tab = "LOCAL_LLM"
        elif self.operating_mode == "REMOTE_API":
            self.agent_mode = "SELF_CONTAINED"
            self.llm_mode = "remote"
            self.pref_tab = "REMOTE_API"
        elif self.operating_mode == "EXTERNAL_HARNESS":
            self.agent_mode = "EXTERNAL_HARNESS"
            # llm_mode stays as-is (not used in harness mode)
            self.pref_tab = "ADVANCED"

    operating_mode: EnumProperty(  # type: ignore[valid-type]
        name="Operating Mode",
        description="Select how the agent connects to an LLM",
        items=OPERATING_MODE_ITEMS,
        default="LOCAL_LLM",
        update=_update_operating_mode,
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
        description="Directory where generative models are downloaded and cached",
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

    # ── Preferences Tab ──────────────────────────────────────────────────

    pref_tab: EnumProperty(  # type: ignore[valid-type]
        name="Tab",
        items=[
            ("LOCAL_LLM", "Local LLM", "Configure and download local models", 'CONSOLE', 0),
            ("REMOTE_API", "Remote API", "Configure remote API access", 'WORLD', 1),
            ("GENERATIVE", "Generative", "Image/video/audio generation backends", 'RENDER_RESULT', 2),
            ("ADVANCED", "Advanced", "External harness, ports, and diagnostics", 'SETTINGS', 3),
        ],
        default="LOCAL_LLM",
    )

    # ── Agent Mode ───────────────────────────────────────────────────────

    agent_mode: EnumProperty(  # type: ignore[valid-type]
        name="Agent Mode",
        description="How the agent operates",
        items=AGENT_MODE_ITEMS,
        default="SELF_CONTAINED",
    )

    # ── MCP Server Mode (for External Harness) ───────────────────────────

    mcp_server_mode: EnumProperty(  # type: ignore[valid-type]
        name="MCP Server Mode",
        description="How the MCP server is launched in External Harness mode",
        items=MCP_SERVER_MODE_ITEMS,
        default="STDIO",
    )

    # ── MCP Server Network Settings ──────────────────────────────────────

    mcp_server_host: StringProperty(  # type: ignore[valid-type]
        name="MCP Server Host",
        description="Host for the MCP HTTP server in Network mode",
        default="127.0.0.1",
    )

    mcp_server_port_override: IntProperty(  # type: ignore[valid-type]
        name="MCP Server Port",
        description="Port for the MCP HTTP server in Network mode (0 = use default 9191 + offset)",
        default=0,
        min=0,
        max=65535,
    )

    # ── Skills (Tier 6) ────────────────────────────────────────────────

    custom_skills_text: StringProperty(  # type: ignore[valid-type]
        name="Custom Skills",
        description=(
            "Extra instructions or skills injected into every conversation.\n"
            "Use for project-specific conventions, tool preferences,\n"
            "or custom workflow rules. Markdown format supported."
        ),
        default="",
    )

    # ── BYOK Provider Profiles (Tier 2) ─────────────────────────────────

    saved_providers_json: StringProperty(  # type: ignore[valid-type]
        name="Saved Providers",
        description="JSON-serialized list of saved provider profiles",
        default="[]",
    )

    def _get_saved_providers(self) -> list[dict]:
        """Deserialize saved provider profiles from JSON."""
        import json as _json
        try:
            return _json.loads(self.saved_providers_json)
        except (json.JSONDecodeError, TypeError):
            return []

    def _set_saved_providers(self, providers: list[dict]) -> None:
        """Serialize saved provider profiles to JSON."""
        import json as _json
        self.saved_providers_json = _json.dumps(providers)

    def _draw_effective_ports(self, box) -> None:
        """Draw the current effective port values as read-only labels."""
        bridge, mcp, llm = effective_ports(self)
        col = box.column(align=True)
        col.label(text="Effective:  Bridge {:d}  |  MCP {:d}  |  LLM {:d}".format(
            bridge, mcp, llm))

    # ── Diagnostics (debug only, behind flag) ───────────────────────────

    def _draw_diagnostics(self, layout) -> None:
        """Draw the diagnostics panel below all tabs when BFACW_DEBUG is enabled."""
        if not BFACW_DEBUG:
            return
        diag_box = layout.box()
        diag_box.label(text="\U0001f6e0\ufe0f Diagnostics", icon='INFO')
        diag_box.label(
            text="Temporary debug tools \u2014 hidden when BFACW_DEBUG=False",
            icon='BLANK1',
        )
        row = diag_box.row()
        row.operator("bfacw.check_ports", icon="FILE_REFRESH", text="Check Ports")
        row.operator("bfacw.ping_agent", icon="FILE_REFRESH", text="Diagnose")
        # ── Multi-Step Test Suites ────────────────────────────────────
        diag_box.label(text="Test Suites (multi-step artist workflows)", icon='RENDER_RESULT')
        diag_box.label(
            text="Click any step to run it (steps build on each other). "
                 "Use Reset to start over.",
            icon='BLANK1',
        )

        _SUITE_META = [
            ("scene_build",   "Scene Build",   'MESH_CUBE',           6),
            ("animation",     "Animation",     'ANIM',                5),
            ("modifiers",     "Modifiers",     'MODIFIER',            6),
            ("assets_materials", "Assets+Mat", 'TEXTURE',             5),
            ("baseline",      "Baseline",      'CONSOLE',             4),
            ("error_handling","Errors",        'ERROR',               3),
        ]

        from . import operators_agent as _oa_suite

        # Grid layout: 3 columns.
        grid = diag_box.grid_flow(row_major=True, columns=3, even_columns=True, even_rows=True)

        for suite_key, suite_label, suite_icon, total_steps in _SUITE_META:
            suite_box = grid.box()
            suite_header = suite_box.row()
            suite_header.label(text=suite_label, icon=suite_icon)

            # Show progress.
            step_idx = _oa_suite._test_suite_progress.get(suite_key, 0)
            suite_header.label(
                text="Step {:d}/{:d}".format(step_idx, total_steps),
                icon='INFO',
            )

            # Step buttons in a column.
            suite = _oa_suite._TEST_SUITES.get(suite_key, [])
            for s_num, s_label, _ in suite:
                step_row = suite_box.row(align=True)
                is_done = step_idx > s_num
                is_current = step_idx == s_num
                if is_done:
                    step_icon = 'CHECKBOX_HLT'
                elif is_current:
                    step_icon = 'RADIOBUT_ON'
                else:
                    step_icon = 'RADIOBUT_OFF'
                op = step_row.operator(
                    "bfacw.test_step",
                    text="{:d}. {:s}".format(s_num, s_label),
                    icon=step_icon,
                )
                op.suite = suite_key

            # Reset button at the bottom of each suite.
            reset_row = suite_box.row(align=True)
            reset_op = reset_row.operator(
                "bfacw.test_step_reset",
                icon='LOOP_BACK',
                text="Reset",
            )
            reset_op.suite = suite_key
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
                val = ping.get(key, "\u2014")
                diag_box.label(
                    text="{:<6s} {:s}".format(label + ":", val),
                    icon=status_icon if val.startswith("OK") else "ERROR",
                )

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout

        # ── Operating Mode selector (top-level, always visible) ─────────
        box = layout.box()
        box.label(text="Operating Mode", icon='TOOL_SETTINGS')
        box.row().prop(self, "operating_mode", expand=True)
        layout.separator()

        # ── Tab selector row ────────────────────────────────────────────
        row = layout.row(align=True)
        row.scale_y = 1.3
        for tab_id, tab_label, tab_icon in [
            ("LOCAL_LLM", "Local Settings", 'CONSOLE'),
            ("REMOTE_API", "Remote Settings", 'WORLD'),
            ("GENERATIVE", "Generative", 'RENDER_RESULT'),
            ("ADVANCED", "Advanced", 'SETTINGS'),
        ]:
            # Hide irrelevant tabs based on operating mode.
            if self.operating_mode == "LOCAL_LLM" and tab_id == "REMOTE_API":
                continue
            if self.operating_mode == "REMOTE_API" and tab_id == "LOCAL_LLM":
                continue
            if self.operating_mode == "EXTERNAL_HARNESS" and tab_id in ("LOCAL_LLM", "REMOTE_API"):
                continue
            is_active = (self.pref_tab == tab_id)
            op = row.operator(
                "bfacw.pref_tab_select",
                text=tab_label,
                icon=tab_icon,
                depress=is_active,
            )
            op.tab_id = tab_id

        layout.separator()

        # ── Draw the active tab ─────────────────────────────────────────
        if self.pref_tab == 'LOCAL_LLM':
            if self.operating_mode == "LOCAL_LLM":
                self._draw_tab_local_llm(context)
            else:
                self.pref_tab = "ADVANCED"
        elif self.pref_tab == 'REMOTE_API':
            if self.operating_mode == "REMOTE_API":
                self._draw_tab_remote_api(context)
            else:
                self.pref_tab = "ADVANCED"
        elif self.pref_tab == 'GENERATIVE':
            self._draw_tab_generative_ai(context)
        elif self.pref_tab == 'ADVANCED':
            self._draw_tab_advanced(context)

        # ── Diagnostics (debug only, behind flag) ───────────────────────
        self._draw_diagnostics(layout)

    # ── Tab: Local LLM ─────────────────────────────────────────────────

    def _draw_tab_local_llm(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout

        # ── LLM Configuration (Local mode only) ────────────────────────
        box = layout.box()
        box.label(text="Local LLM Configuration", icon='CONSOLE')

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

    # ── Tab: Remote API ────────────────────────────────────────────────

    def _draw_tab_remote_api(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout

        # ── Remote API Configuration ────────────────────────────────────
        box = layout.box()
        box.label(text="Remote API Configuration", icon='WORLD')

        llm = get_llm_manager()

        # ── Remote Provider ─────────────────────────────────────
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

        # ── Saved Provider Profiles (BYOK, Tier 2) ──────────────
        box.separator()
        box.label(text="Saved Provider Profiles", icon='BOOKMARKS')
        providers = self._get_saved_providers()
        if providers:
            for p in providers:
                row = box.row(align=True)
                op = row.operator(
                    "bfacw.load_provider",
                    text="{:s} ({:s})".format(p.get("name", "?"), p.get("model", "?")),
                    icon='FILE_TICK',
                )
                op.profile_name = p.get("name", "")
                op = row.operator(
                    "bfacw.delete_provider",
                    text="",
                    icon='X',
                )
                op.profile_name = p.get("name", "")
        else:
            box.label(text="No saved profiles. Configure above and save.", icon='INFO')
        row = box.row()
        row.operator("bfacw.save_provider", icon="ADD", text="Save Current as Profile")

    # ── Tab: Generative ─────────────────────────────────────────────

    def _draw_tab_generative_ai(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout

        # ── Generation (Tier 5) ────────────────────────────────────────
        gen_box = layout.box()
        gen_box.label(text="Generative (Image / Video / Audio)", icon='RENDER_RESULT')
        gen_box.label(text="Experimental (WIP)", icon='WARNING')
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

        # ── Poly Haven Asset Test (Tier 1) ─────────────────────────────
        ph_box = layout.box()
        ph_box.label(text="Poly Haven Asset Download (Test)", icon='WORLD')
        ph_box.label(
            text="Download a free CC0 HDRI or texture to test the Poly Haven integration.",
            icon='INFO',
        )
        row = ph_box.row(align=True)
        row.operator("bfacw.test_polyhaven_hdri", icon='WORLD', text="Download Test HDRI")
        row.operator("bfacw.test_polyhaven_texture", icon='TEXTURE', text="Download Test Texture")

    # ── Tab: Advanced ──────────────────────────────────────────────────

    def _draw_tab_advanced(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout

        # ── Operating Mode ─────────────────────────────────────────────
        box = layout.box()
        box.label(text="Operating Mode", icon='WORKSPACE')
        box.prop(self, "operating_mode", expand=True)

        # ── Bridge Server ──────────────────────────────────────────────
        bridge_box = layout.box()
        bridge_box.label(text="Bridge Server", icon='NETWORK_DRIVE')
        bridge_box.prop(self, "host")
        bridge_box.prop(self, "port")
        from . import mcp_to_blender_server as _mbs
        if _mbs.is_running():
            _bridge_port, _, _ = effective_ports(self)
            bridge_box.label(
                text="Status: Running on {:s}:{:d}".format(self.host, _bridge_port),
                icon='CHECKMARK',
            )
        else:
            bridge_box.label(text="Status: Stopped", icon='X')

        # ── MCP Server (External Harness) ──────────────────────────────
        mcp_box = layout.box()
        mcp_box.label(text="MCP Server (External Harness)", icon='SETTINGS')
        mcp_box.prop(self, "mcp_server_mode", expand=True)

        if self.mcp_server_mode == "STDIO":
            # Show config snippet for Claude Desktop.
            mcp_box.label(text="Claude Desktop Config:", icon='COPYDOWN')
            config_json = (
                '{\n'
                '  "mcpServers": {\n'
                '    "bfa-coworker": {\n'
                '      "command": "python",\n'
                '      "args": ["-m", "blmcp", "--transport", "stdio"],\n'
                '      "env": {\n'
                '        "BFACW_HOST": "' + self.host + '",\n'
                '        "BFACW_PORT": "' + str(self.port) + '"\n'
                '      }\n'
                '    }\n'
                '  }\n'
                '}'
            )
            mcp_box.label(text=config_json, icon='BLANK1')
            row = mcp_box.row()
            row.operator("bfacw.copy_mcp_config", icon="COPYDOWN", text="Copy to Clipboard")

        elif self.mcp_server_mode == "NETWORK":
            mcp_box.prop(self, "mcp_server_host")
            mcp_box.prop(self, "mcp_server_port_override")
            if self.mcp_server_host not in ("127.0.0.1", "localhost", "::1"):
                mcp_box.label(
                    text="\u26a0 Binding to non-localhost exposes the MCP server to your network!",
                    icon='ERROR',
                )
            row = mcp_box.row(align=True)
            from . import agent_controller as _ac
            if _ac._agent_state.mcp_server_running:
                row.operator("bfacw.mcp_server_stop", icon="CANCEL", text="Stop MCP Server")
            else:
                row.operator("bfacw.mcp_server_start", icon="PLAY", text="Start MCP Server")

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
                val = ping.get(key, "\u2014")
                box.label(
                    text="{:<6s} {:s}".format(label + ":", val),
                    icon=status_icon if val.startswith("OK") else "ERROR",
                )

        # ── Port Settings ──────────────────────────────────────────────
        port_box = layout.box()
        port_box.label(text="Port Settings", icon='SETTINGS')
        port_box.prop(self, "port_offset")
        self._draw_effective_ports(port_box)
        row = port_box.row()
        row.prop(self, "bridge_port")
        row.prop(self, "mcp_port")
        row.prop(self, "llm_port")

        # ── Skills ─────────────────────────────────────────────────────
        skills_box = layout.box()
        skills_box.label(text="Skills", icon='TEXT')
        try:
            import bpy  # pylint: disable=import-error
            version_str = ".".join(str(v) for v in bpy.app.version[:3])
            skills_box.label(
                text="Blender {:s}".format(version_str),
                icon='BLENDER',
            )
        except Exception:
            pass
        # Show loaded skill files.
        try:
            from . import skills as _skills_mod  # pylint: disable=import-error
            loaded = _skills_mod.list_loaded_skills()
            if loaded:
                col = skills_box.column(align=True)
                col.label(text="Loaded Skills:", icon='CHECKMARK')
                for name in loaded:
                    col.label(text="  \u2022 {:s}".format(name))
            else:
                skills_box.label(text="No skills loaded", icon='INFO')
        except Exception:
            skills_box.label(text="Skills module not available", icon='ERROR')
        row = skills_box.row()
        row.operator("bfacw.reload_skills", icon="FILE_REFRESH", text="Reload Skills")

        # ── Custom Skills ──────────────────────────────────────────────
        custom_box = layout.box()
        custom_box.label(text="Custom Skills", icon='GREASEPENCIL')
        custom_box.label(
            text="Extra instructions injected into every conversation. "
                 "Use for project-specific conventions, tool preferences, "
                 "or workflow rules. Markdown format supported.",
            icon='INFO',
        )
        custom_box.prop(self, "custom_skills_text")


# ---------------------------------------------------------------------------
# Preferences Tab Selector Operator

class BFACW_OT_pref_tab_select(bpy.types.Operator):  # type: ignore[misc]
    """Switch to a different preferences tab."""
    bl_idname = "bfacw.pref_tab_select"
    bl_label = "Select Preferences Tab"
    bl_description = "Switch to this preferences tab"
    bl_options = {'INTERNAL'}

    tab_id: StringProperty(  # type: ignore[valid-type]
        name="Tab ID",
        default="LOCAL_LLM",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        prefs.pref_tab = self.tab_id
        return {'FINISHED'}