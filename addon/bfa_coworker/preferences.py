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
    is_debug_mode,
    effective_ports,
    get_llm_manager,
)


def _download_status_icon(msg: str) -> str:
    """Return a green checkmark icon once a download has completed.

    Completion messages are the only ones that start with these prefixes;
    everything else stays the blue info icon.
    """
    if (
        msg.startswith("Download complete")
        or msg.startswith("Model already downloaded")
        or msg.startswith("llama-server installed")
    ):
        return "CHECKMARK"
    return "INFO"


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

    # ── Chat Display ─────────────────────────────────────────────

    chat_max_visible_turns: IntProperty(  # type: ignore[valid-type]
        name="Max Visible Turns",
        description=(
            "Maximum number of conversation turns shown in the chat panel. "
            "0 = show all turns (no limit). Higher values may slow the UI "
            "with very long conversations."
        ),
        default=0,
        min=0,
        max=100,
    )

    # ── Debug Mode ──────────────────────────────────────────────────

    debug_mode: BoolProperty(  # type: ignore[valid-type]
        name="Debug / Diagnostics",
        description=(
            "Show the Diagnostics panel in Preferences with port checking, "
            "benchmark suites, and other developer tools"
        ),
        default=False,
    )

    # ── Log Level ───────────────────────────────────────────────────

    def _update_log_level(self, _context: bpy.types.Context) -> None:
        mcp_to_blender_server.log_level = self.log_level

    log_level: EnumProperty(  # type: ignore[valid-type]
        name="Log Level",
        description=(
            "Tool-call logging granularity:\n"
            "  Off — no logging\n"
            "  Errors Only — log only failed tool calls\n"
            "  All — log every tool request and response"
        ),
        items=[
            ("OFF", "Off", "No tool-call logging"),
            ("ERRORS_ONLY", "Errors Only", "Log only tool calls that returned errors"),
            ("ALL", "All", "Log every tool request and response"),
        ],
        default="OFF",
        update=_update_log_level,
    )

    def _update_use_log(self, _context: bpy.types.Context) -> None:
        # Legacy bool kept for backward compat; syncs to log_level.
        if self.use_log:
            self.log_level = "ALL"
        else:
            self.log_level = "OFF"

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
        cfg.llama_backend = self.llama_backend
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
        description="Select how the Coworker agent connects to an LLM",
        items=OPERATING_MODE_ITEMS,
        default="LOCAL_LLM",
        update=_update_operating_mode,
    )

    llama_path: StringProperty(  # type: ignore[valid-type]
        name="llama-server Path",
        default="",
        subtype='FILE_PATH',
        description=(
            "Path to a custom llama-server.exe. Leave empty to use the bundled version.\n"
            "To add a custom llama.cpp installation to PATH:\n"
            "  Windows: System Properties → Environment Variables → Path → add the folder\n"
            "  macOS/Linux: export PATH=\"/path/to/llama.cpp/build/bin:$PATH\"\n"
            "The addon bundles its own copy — only set this if you need a specific build."
        ),
    )

    def _update_llama_backend(self, _context: bpy.types.Context) -> None:
        """Sync llama_backend to llm_manager config."""
        llm = get_llm_manager()
        cfg = llm.get_config()
        cfg.llama_backend = self.llama_backend
        llm.set_config(cfg)
        # Invalidate the find_llama_server cache so the new backend binary is found.
        llm.invalidate_llama_server_cache()

    llama_backend: EnumProperty(  # type: ignore[valid-type]
        name="GPU Backend",
        description=(
            "Select the GPU backend for llama-server.\n"
            "  Auto — detect NVIDIA (CUDA), AMD/Intel (Vulkan), or CPU\n"
            "  CUDA — NVIDIA GPUs (RTX 20xx+; 3090/4090/5090 recommended)\n"
            "  Vulkan — AMD Radeon, Intel Arc, or NVIDIA fallback\n"
            "  CPU — no GPU acceleration"
        ),
        items=[
            ("auto", "Auto (Detect)", "Auto-detect the best backend for your GPU"),
            ("cuda", "CUDA 12.4", "NVIDIA GPUs — RTX 20xx+ (3090/4090/5090 recommended)"),
            ("vulkan", "Vulkan", "AMD Radeon, Intel Arc, or NVIDIA fallback"),
            ("cpu", "CPU", "No GPU acceleration — runs on CPU only"),
        ],
        default="auto",
        update=_update_llama_backend,
    )

    model_repo_id: StringProperty(  # type: ignore[valid-type]
        name="Model Repo ID",
        default="unsloth/gpt-oss-20b-GGUF",
    )

    model_filename: StringProperty(  # type: ignore[valid-type]
        name="Model Filename",
        default="gpt-oss-20b-Q4_K_M.gguf",
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
            # Auto-set context window: hardware-aware recommendation, capped by
            # the model's own context window. See _apply_recommended_ctx().
            self._apply_recommended_ctx(preset)
            # Auto-set max output tokens from preset.
            self.local_max_tokens = preset.max_tokens
            # Clear existing model path — using preset now.
            self.existing_model_path = ""
            # Build info string for display.
            self.model_preset_info = (
                "Capability: {cap}  |  RAM: {ram}  |  Disk: {disk}\n"
                "Hardware: {hw}\n"
                "Why: {why}\n"
                "{desc}"
            ).format(
                cap=preset.capability,
                ram=preset.ram_gb,
                disk=preset.disk_gb,
                hw=preset.hardware_note,
                why=preset.why,
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
            cfg.llama_backend = self.llama_backend
            llm.set_config(cfg)
        else:
            self.model_preset_info = ""

    def _current_model_gb(self, preset: "ModelPreset | None" = None) -> float:
        """Estimate the model file size in GB (real file size when present)."""
        if self.existing_model_path and os.path.isfile(self.existing_model_path):
            try:
                return os.path.getsize(self.existing_model_path) / (1024 ** 3)
            except OSError:
                pass
        if preset is not None:
            # "16-20 GB" -> use the low end of the range as the estimate.
            try:
                first = float(preset.disk_gb.split("-")[0].strip().split()[0])
                return first
            except (ValueError, IndexError, AttributeError):
                pass
        if self.model_filename:
            models_dir = Path(self.downloaded_models_dir) if self.downloaded_models_dir else (
                Path.home() / "bfa_coworker_models")
            candidate = models_dir / self.model_filename
            if candidate.is_file():
                try:
                    return candidate.stat().st_size / (1024 ** 3)
                except OSError:
                    pass
        return 0.0

    def _apply_recommended_ctx(self, preset: "ModelPreset | None" = None) -> None:
        """Set the context window to a hardware-aware safe recommendation."""
        llm = get_llm_manager()
        model_gb = self._current_model_gb(preset)
        recommended = llm.recommend_context_size(
            model_gb=model_gb,
            backend=self.llama_backend,
        )
        cap = preset.context_window if preset is not None else 131072
        recommended = min(recommended, cap)
        # Snap down again in case the model cap landed off a standard size.
        fitting = [s for s in llm.ctx_preset_sizes if s <= recommended]
        recommended = fitting[-1] if fitting else llm.ctx_preset_sizes[0]
        self.local_ctx_size = recommended
        self.local_ctx_preset = str(recommended)

    model_preset: EnumProperty(  # type: ignore[valid-type]
        name="Recommended Model",
        description="Select a curated model preset. Picking one auto-fills the repo and filename below",
        items=MODEL_PRESET_ITEMS,
        update=_update_model_preset,
        default="gpt_oss_20b_q4",
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
        name="Auto-Start Coworker",
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
            "Larger values allow longer conversations but use much more RAM/VRAM.\n"
            "Prefer the preset buttons above the Custom slider — a context that is "
            "too large for your memory is the most common startup crash.\n"
            "Default 32768 works for most models. Gemma 4 supports up to 262144."
        ),
        default=32768,
        min=4096,
        max=262144,
        step=1024,
        subtype='UNSIGNED',
    )

    def _update_ctx_preset(self, _context: bpy.types.Context) -> None:
        """Sync the context preset button to the numeric context size."""
        if self.local_ctx_preset != "custom":
            try:
                self.local_ctx_size = int(self.local_ctx_preset)
            except ValueError:
                pass

    local_ctx_preset: EnumProperty(  # type: ignore[valid-type]
        name="Context Window Preset",
        description=(
            "One-click context window sizes. The recommended size for your "
            "hardware is suggested automatically when you pick a model. "
            "Custom reveals a manual slider for fine control."
        ),
        items=[
            ("4096", "4K", "4096 tokens — minimal, fastest to load"),
            ("8192", "8K", "8192 tokens — short conversations"),
            ("16384", "16K", "16384 tokens — good default for most agent work"),
            ("32768", "32K", "32768 tokens — recommended default"),
            ("65536", "64K", "65536 tokens — long conversations, needs lots of memory"),
            ("131072", "128K", "131072 tokens — only on high-end hardware"),
            ("custom", "Custom", "Manually set the context size with a slider"),
        ],
        default="32768",
        update=_update_ctx_preset,
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

    # ── Poly Haven Resolution ───────────────────────────────────────────

    polyhaven_resolution: EnumProperty(  # type: ignore[valid-type]
        name="Poly Haven Resolution",
        description=(
            "Default download resolution for Poly Haven textures and HDRIs. "
            "Lower resolutions are faster to download and use less memory."
        ),
        items=lambda self, _context: [
            ("512", "512 - Preview", "Tiny textures for prototyping"),
            ("1k", "1k - Lightweight", "Good for background objects"),
            ("2k", "2k - Balanced (Recommended)", "Best balance of quality and performance"),
            ("4k", "4k - Production", "High quality for close-up shots"),
            ("8k", "8k - Maximum", "Largest files, highest detail"),
        ],
        default=2,
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
        name="Coworker Mode",
        description="How the Coworker agent operates",
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

    # ── Harness Configuration ─────────────────────────────────────────

    use_blender_python_for_harness: BoolProperty(  # type: ignore[valid-type]
        name="Use Blender's Python",
        description=(
            "When ON, harness configs use Blender's bundled Python with "
            "vendor dependencies — no pip install needed.\n"
            "When OFF, uses system 'python' (requires pip-installed "
            "bfa-coworker-mcp)"
        ),
        default=True,
    )

    harness_preset: EnumProperty(  # type: ignore[valid-type]
        name="Harness Preset",
        description="Select an external MCP client to configure",
        items=lambda self, _context: self._get_harness_preset_items(),
        default=0,
    )

    def _get_harness_preset_items(self) -> list[tuple[str, str, str]]:
        """Return harness preset items, importing lazily to avoid circular deps."""
        from .shared import HARNESS_PRESET_ITEMS
        return HARNESS_PRESET_ITEMS

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

    # ── Text Editor Memory Bank ────────────────────────────────────────

    save_code_to_text_editor: BoolProperty(  # type: ignore[valid-type]
        name="Save Executed Code to Text Editor",
        description=(
            "After each successful execute_blender_code call, save the code\n"
            "to a new text datablock (Coworker_HH-MM-SS) for review and reuse"
        ),
        default=True,
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
        """Draw the diagnostics panel below all tabs when debug mode is enabled."""
        if not is_debug_mode():
            return
        diag_box = layout.box()
        diag_box.label(text="\U0001f6e0\ufe0f Diagnostics", icon='INFO')
        diag_box.label(
            text="Temporary debug tools \u2014 hidden when Debug mode is off",
            icon='BLANK1',
        )
        # ── Open Log button ─────────────────────────────────────────
        diag_box.operator(
            "bfacw.open_log",
            icon='CONSOLE',
            text="Open Log",
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
            ("assets_browser", "Asset Browser", 'ASSET_MANAGER',       6),
            ("polyhaven",     "Poly Haven",    'WORLD',               5),
            ("baseline",      "Baseline",      'CONSOLE',             6),
            ("error_handling","Errors",        'ERROR',               3),
            ("vision_camera", "Vision: Camera", 'CAMERA_DATA',         4),
            ("vision_relative", "Vision: Place", 'SNAP_ON',            5),
            ("shader_nodes",  "Shader Nodes",  'MATERIAL',            4),
            ("geometry_nodes", "Geo Nodes",    'GEOMETRY_NODES',      4),
            ("sequencer",     "Sequencer",     'SEQUENCE',            4),
            ("image_editor",  "Image Editor",  'IMAGE_DATA',          3),
            ("compositor",    "Compositor",    'NODE_COMPOSITING',         4),
            ("multi_editor_cross", "Multi-Editor", 'WINDOW',           4),
        ]

        from . import operators_agent as _oa_suite

        # Grid layout: 3 columns.
        grid = diag_box.grid_flow(row_major=True, columns=3, even_columns=True, even_rows=True)

        for suite_key, suite_label, suite_icon, _total_steps in _SUITE_META:
            suite_box = grid.box()
            suite_header = suite_box.row()
            suite_header.label(text=suite_label, icon=suite_icon)

            # Show progress using actual suite length.
            suite = _oa_suite._TEST_SUITES.get(suite_key, [])
            total_steps = len(suite)
            step_idx = _oa_suite._test_suite_progress.get(suite_key, 0)
            suite_header.label(
                text="Step {:d}/{:d}".format(step_idx, total_steps),
                icon='INFO',
            )

            # Step buttons in a column.
            for step_i, (s_num, s_label, _) in enumerate(suite):
                step_row = suite_box.row(align=True)
                is_done = step_i < step_idx
                is_current = step_i == step_idx
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
                # Show elapsed time for completed steps.
                elapsed = _oa_suite._test_suite_timings.get((suite_key, s_num))
                if elapsed is not None:
                    step_row.label(text="{:.1f}s".format(elapsed))

            # Reset button at the bottom of each suite.
            reset_row = suite_box.row(align=True)
            reset_op = reset_row.operator(
                "bfacw.test_step_reset",
                icon='LOOP_BACK',
                text="Reset",
            )
            reset_op.suite = suite_key

        # Compare button for benchmark results.
        diag_box.separator()
        diag_box.operator("bfacw.compare_benchmarks", icon='FILE_REFRESH', text="Compare Results")

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
            is_harness = (self.operating_mode == "EXTERNAL_HARNESS")
            status_icon = "CHECKMARK" if ping.get("all_ok") else "ERROR"
            for key, label in [
                ("bridge_server", "Bridge"),
                ("mcp_server", "MCP"),
                ("llm_health", "LLM"),
                ("llm_chat", "Chat"),
            ]:
                val = ping.get(key, "\u2014")
                # In harness mode, N/A is not an error.
                is_ok = val.startswith("OK") or (is_harness and val.startswith("N/A"))
                diag_box.label(
                    text="{:<6s} {:s}".format(label + ":", val),
                    icon=status_icon if is_ok else "ERROR",
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
        # GPU backend selector.
        box.prop(self, "llama_backend")
        # Unified progress block for llama-server download.
        if llm_state.download_kind == "llama_server":
            if llm_state.download_progress:
                box.label(
                    text=llm_state.download_progress,
                    icon=_download_status_icon(llm_state.download_progress),
                )
            pct = llm_state.download_progress_pct
            if pct > 0:
                row = box.row(align=True)
                row.progress(factor=pct / 100.0, type='BAR')
            if llm_state.download_active:
                row = box.row(align=True)
                # Icon-only (text="") — the operator's bl_label shows as tooltip.
                row.operator("bfacw.cancel_download", icon='CANCEL', text="")

        # ── Recommended Models (presets) ─────────────────────────
        box.label(text="Pick a Model", icon='VIEWZOOM')

        _CATEGORIES = [
            ("flagship", "Flagship (24 GB+ VRAM) — Best quality, needs high-end GPU", 'SORT_ASC'),
            ("mid_range", "Mid-Range (16-20 GB VRAM) — Best balance, RTX 3090/4090 sweet spot", 'VIEWZOOM'),
            ("lightweight", "Lightweight (\u2264 8 GB VRAM) — Runs on any GPU or integrated", 'LIGHT_SUN'),
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
                # Single icon per model: IMAGE_DATA for vision, VIEWZOOM otherwise.
                icon = 'IMAGE_DATA' if preset.vision else 'VIEWZOOM'
                op = row.operator(
                    "bfacw.select_preset",
                    text=preset.name,
                    icon=icon,
                    depress=self.model_preset == preset.identifier,
                )
                op.preset_id = preset.identifier
                # Multiline label: hardware_note + why on subsequent lines.
                col = row.column(align=True)
                col.scale_y = 0.8
                col.label(
                    text="\u2502 {:s}".format(preset.hardware_note),
                )
                col.label(
                    text="\u2514 {:s}".format(preset.why),
                )

        # Custom model entry.
        box.prop(self, "model_preset", text="Custom Model")
        if self.model_preset != "_custom" and self.model_preset_info:
            info_box = box.box()
            info_box.label(text="Model Information", icon='INFO')
            for line in self.model_preset_info.split("\n"):
                info_box.label(text=line)

        # ── Context Window (preset buttons + custom override) ────
        # One-click sizes instead of a free slider — the most common
        # startup crash is a context too large for the available memory.
        ctx_box = box.box()
        ctx_box.label(
            text="Context Window (how much the model remembers per reply)",
            icon='MEMORY',
        )
        row = ctx_box.row(align=True)
        active_ctx = self.local_ctx_size
        llm = get_llm_manager()
        is_custom = (self.local_ctx_preset == "custom") or (
            active_ctx not in llm.ctx_preset_sizes)
        for value in llm.ctx_preset_sizes:
            op = row.operator(
                "bfacw.set_ctx_preset",
                text=llm.ctx_preset_label(value),
                depress=(active_ctx == value),
            )
            op.value = value
        op = row.operator(
            "bfacw.set_ctx_preset",
            text="Custom",
            depress=is_custom,
        )
        op.value = 0
        if is_custom:
            ctx_box.prop(self, "local_ctx_size")
        # Hardware-aware recommendation hint.
        model_gb = self._current_model_gb(llm.get_preset_by_id(self.model_preset))
        ctx_box.label(
            text=llm.hardware_context_hint(model_gb, self.llama_backend),
            icon='INFO',
        )

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
        # The cancel button must NOT live in the same row as the (disabled)
        # download button — row.enabled above greys out the entire row,
        # including Cancel.  It lives with the progress bar instead, with a
        # fallback row for the pre-progress phase (download just started).
        if llm_state.download_active and llm_state.download_kind == "model" \
                and llm_state.download_progress_pct <= 0:
            cancel_row = box.row(align=True)
            # Icon-only (text="") — the operator's bl_label shows as tooltip.
            cancel_row.operator("bfacw.cancel_download", icon='CANCEL', text="")

        # Always show progress/error areas (model downloads only).
        if llm_state.download_kind == "model":
            if llm_state.error:
                err_lines = llm_state.error.split("\n")
                for i, line in enumerate(err_lines):
                    box.label(text=line, icon="ERROR" if i == 0 else 'NONE')
            if llm_state.download_progress:
                prog_text = llm_state.download_progress
                if llm_state.download_progress_eta:
                    prog_text = "{:s}  |  {:s}".format(prog_text, llm_state.download_progress_eta)
                box.label(
                    text=prog_text,
                    icon=_download_status_icon(llm_state.download_progress),
                )
                pct = llm_state.download_progress_pct
                if pct > 0:
                    row = box.row(align=True)
                    row.progress(factor=pct / 100.0, type='BAR')
                    if llm_state.download_active:
                        # Icon-only (text="") — the operator's bl_label shows as tooltip.
                        row.operator("bfacw.cancel_download", icon='CANCEL', text="")

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

        # ── Startup / runtime errors (not download-related) ──────
        if llm_state.error and llm_state.download_kind != "model":
            err_lines = llm_state.error.split("\n")
            for i, line in enumerate(err_lines):
                box.label(text=line, icon="ERROR" if i == 0 else 'NONE')

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

        # ── Poly Haven Asset Download (Tier 1) ────────────────────────
        ph_box = layout.box()
        ph_box.label(text="Poly Haven Asset Download", icon='WORLD')
        ph_box.label(
            text="Download free CC0 HDRIs, textures, and models from Poly Haven.",
            icon='INFO',
        )
        # Resolution selector.
        ph_row = ph_box.row(align=True)
        ph_row.prop(self, "polyhaven_resolution", text="Resolution")
        # Test buttons.
        row = ph_box.row(align=True)
        row.operator("bfacw.test_polyhaven_hdri", icon='WORLD', text="Download Test HDRI")
        row.operator("bfacw.test_polyhaven_texture", icon='TEXTURE', text="Download Test Texture")

    # ── Tab: Advanced ──────────────────────────────────────────────────

    def _draw_tab_advanced(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout

        # ── Mode hint ──────────────────────────────────────────────────
        mode_labels = {
            "LOCAL_LLM": "Local LLM mode — some settings are hidden",
            "REMOTE_API": "Remote API mode — some settings are hidden",
            "EXTERNAL_HARNESS": "External Harness mode — some settings are hidden",
        }
        hint = mode_labels.get(self.operating_mode, "")
        if hint:
            hint_row = layout.row()
            hint_row.label(text=hint, icon='INFO')
            hint_row.scale_y = 0.6

        # ── Bridge Server (always visible) ────────────────────────────
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

        # ── MCP Server (External Harness mode only) ────────────────
        if self.operating_mode == "EXTERNAL_HARNESS":
            mcp_box = layout.box()
            mcp_box.label(text="MCP Server (External Harness)", icon='SETTINGS')
            mcp_box.prop(self, "mcp_server_mode", expand=True)

            if self.mcp_server_mode == "STDIO":
                # ── Step 1: Pick your harness ───────────────────────────
                step1 = mcp_box.box()
                step1.label(text="Step 1: Pick your MCP client", icon='FORWARD')
                step1.prop(self, "harness_preset", text="")

                # Show a short info line about the selected preset.
                from .shared import get_harness_preset_by_id
                preset = get_harness_preset_by_id(self.harness_preset)
                if preset is not None:
                    row = step1.row(align=True)
                    row.label(text=preset.description, icon='INFO')
                    if preset.docs_url:
                        row.operator("bfacw.open_url", icon='URL', text="Docs").url = preset.docs_url

                # ── Step 2: Copy the config ─────────────────────────────
                step2 = mcp_box.box()
                step2.label(text="Step 2: Copy the config", icon='COPYDOWN')
                row = step2.row(align=True)
                op = row.operator("bfacw.copy_mcp_config", icon="COPYDOWN", text="Copy to Clipboard")
                op.client_type = self.harness_preset
                step2.label(
                    text="This copies the connection settings for your selected client.",
                    icon='BLANK1',
                )

                # ── Step 3: Paste into your client ──────────────────────
                step3 = mcp_box.box()
                step3.label(text="Step 3: Paste into your client's config file", icon='FILE_TEXT')
                if preset is not None and preset.config_path_help:
                    for line in preset.config_path_help.split("\n"):
                        step3.label(text=line, icon='FILE_FOLDER')
                row = step3.row(align=True)
                op2 = row.operator("bfacw.open_config_folder", icon="FILE_FOLDER", text="Open Config Folder")
                op2.preset_id = self.harness_preset
                step3.label(
                    text="Tip: The config file is a JSON file. Paste the copied text inside the top-level { } braces.",
                    icon='INFO',
                )

                # ── Step 4: Restart ─────────────────────────────────────
                step4 = mcp_box.box()
                step4.label(text="Step 4: Restart your MCP client", icon='LOOP_BACK')
                step4.label(
                    text="Close and re-open your MCP client completely. "
                         "A window close is not enough on some apps.",
                    icon='BLANK1',
                )
                if preset is not None and preset.notes:
                    step4.label(text="\u2139\ufe0f {:s}".format(preset.notes), icon='INFO')

                # ── Advanced options ────────────────────────────────────
                adv_box = mcp_box.box()
                adv_box.label(text="Advanced Options", icon='SETTINGS')
                adv_box.prop(self, "use_blender_python_for_harness")
                if preset is not None and preset.setup_steps:
                    adv_box.label(text="Detailed setup for this client:", icon='PLAY')
                    for i, step in enumerate(preset.setup_steps, 1):
                        adv_box.label(
                            text="{:d}. {:s}".format(i, step),
                            icon='DOT',
                        )

                # Config preview.
                adv_box.label(text="Config Preview:", icon='COPYDOWN')
                from . import agent_controller as _ac
                _bridge_port, _, _ = effective_ports(self)
                preview = _ac.generate_mcp_client_config(
                    client_type=self.harness_preset,
                    blender_host=self.host,
                    blender_port=_bridge_port,
                    use_blender_python=self.use_blender_python_for_harness,
                )
                for line in preview.split("\n"):
                    adv_box.label(text=line, icon='BLANK1')

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
        box.label(text="Coworker Control", icon='WORKSPACE')
        box.prop(self, "agent_autostart")
        row = box.row()
        row.operator("bfacw.ping_agent", icon="FILE_REFRESH", text="Check Status")
        # Lazy import to avoid circular dependency.
        from . import operators_agent as _oa
        ping = _oa._BFACW_OT_ping_agent._result
        if ping:
            is_harness = (self.operating_mode == "EXTERNAL_HARNESS")
            status_icon = "CHECKMARK" if ping.get("all_ok") else "ERROR"
            for key, label in [
                ("bridge_server", "Bridge"),
                ("mcp_server", "MCP"),
                ("llm_health", "LLM"),
                ("llm_chat", "Chat"),
            ]:
                val = ping.get(key, "\u2014")
                is_ok = val.startswith("OK") or (is_harness and val.startswith("N/A"))
                box.label(
                    text="{:<6s} {:s}".format(label + ":", val),
                    icon=status_icon if is_ok else "ERROR",
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

        # ── Skills (not in External Harness mode) ───────────────────────
        if self.operating_mode != "EXTERNAL_HARNESS":
            skills_box = layout.box()
            skills_box.label(text="Skills", icon='TEXT')
            try:
                import bpy as _bpy_skills  # pylint: disable=import-error
                version_str = ".".join(str(v) for v in _bpy_skills.app.version[:3])
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

        # ── Custom Skills (not in External Harness mode) ────────────────
        if self.operating_mode != "EXTERNAL_HARNESS":
            custom_box = layout.box()
            custom_box.label(text="Custom Skills", icon='GREASEPENCIL')
            custom_box.label(
                text="Extra instructions injected into every conversation. "
                     "Use for project-specific conventions, tool preferences, "
                     "or workflow rules. Markdown format supported.",
                icon='INFO',
            )
            # Multiline textbox (5.3 textbox API — same as chat input).
            custom_box.textbox(self, "custom_skills_text")

        # ── Text Editor Memory Bank (not in External Harness mode) ──────
        if self.operating_mode != "EXTERNAL_HARNESS":
            mem_box = layout.box()
            mem_box.label(text="Text Editor Memory Bank", icon='TEXT')
            mem_box.label(
                text="Save executed code to timestamped text datablocks\n"
                     "(Coworker_HH-MM-SS) for review and reuse.",
                icon='INFO',
            )
            mem_box.prop(self, "save_code_to_text_editor")

        # ── Chat Display ───────────────────────────────────────────────
        chat_box = layout.box()
        chat_box.label(text="Chat Display", icon='SORTTIME')
        chat_box.prop(self, "chat_max_visible_turns")
        chat_box.label(
            text="0 = show all turns. Higher values limit history shown.",
            icon='INFO',
        )

        # ── Debug Mode ─────────────────────────────────────────────────
        debug_box = layout.box()
        debug_box.label(text="Debug Mode", icon='MODIFIER')
        debug_box.prop(self, "debug_mode")
        debug_box.label(
            text="Enable benchmarks and advanced diagnostics.",
            icon='INFO',
        )


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