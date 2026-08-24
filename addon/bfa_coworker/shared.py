# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Shared constants, helpers, and lazy-import wrappers for the Coworker add-on.
"""

__all__ = (
    "PORT_MIN",
    "PORT_MAX",
    "AUTOSTART_DELAY",
    "STATE_OFFLINE_ERROR_MESSAGE",
    "MODEL_PRESET_ITEMS",
    "REMOTE_PROVIDER_ITEMS",
    "GEN_BACKEND_ITEMS",
    "AGENT_MODE_ITEMS",
    "MCP_SERVER_MODE_ITEMS",
    "OPERATING_MODE_ITEMS",
    "CHAT_MODE_ITEMS",
    "HARNESS_PRESET_ITEMS",
    "HarnessPreset",
    "DEFAULT_BRIDGE_PORT",
    "DEFAULT_MCP_PORT",
    "DEFAULT_LLM_PORT",
    "BFACW_DEBUG",
    "effective_ports",
    "get_llm_manager",
    "get_agent_controller",
    "get_gen_controller",
)

import os
from pathlib import Path

PORT_MIN = 1024
PORT_MAX = 65535

# Default seconds to wait after registration before auto-starting the server.
# Avoids adding work to Blender's startup sequence.
AUTOSTART_DELAY = 1.0

# This error is shown in the UI & command line when online access isn't enabled.
#
# NOTE(@ideasman42): we could consider `localhost` to be acceptable, this is a grey area
# regarding what counts as "online" or not.
STATE_OFFLINE_ERROR_MESSAGE = "Online access must be enabled in the system preferences"

# ── Default Ports ────────────────────────────────────────────────────────

DEFAULT_BRIDGE_PORT = 9876
DEFAULT_MCP_PORT = 9191
DEFAULT_LLM_PORT = 8081

# Debug flag: when False, hides temporary diagnostics UI from Preferences.
# Set to False for release builds, True during active development.
BFACW_DEBUG = True


def effective_ports(prefs) -> tuple[int, int, int]:
    """Return (bridge_port, mcp_port, llm_port) with offset applied.

    If an individual port override is set (> 0), it is used directly
    *without* the offset.  Otherwise, ``DEFAULT_*_PORT + offset`` is used.
    """
    offset = prefs.port_offset if hasattr(prefs, 'port_offset') else 0
    bridge = (
        prefs.bridge_port
        if hasattr(prefs, 'bridge_port') and prefs.bridge_port > 0
        else DEFAULT_BRIDGE_PORT + offset
    )
    mcp = (
        prefs.mcp_port
        if hasattr(prefs, 'mcp_port') and prefs.mcp_port > 0
        else DEFAULT_MCP_PORT + offset
    )
    llm = (
        prefs.llm_port
        if hasattr(prefs, 'llm_port') and prefs.llm_port > 0
        else DEFAULT_LLM_PORT + offset
    )
    return (bridge, mcp, llm)


# ── Lazy Import Helpers (avoids circular imports) ────────────────────────

def get_llm_manager():
    """Lazy import of llm_manager module."""
    from . import llm_manager as _m
    return _m


def get_agent_controller():
    """Lazy import of agent_controller module."""
    from . import agent_controller as _m
    return _m


def get_gen_controller():
    """Lazy import of gen_controller module."""
    from . import gen_controller as _m
    return _m


# ── Static EnumProperty Items ────────────────────────────────────────────

# Static preset items for the model_preset EnumProperty.
# Must be a module-level constant — callbacks can fail during class registration.
# Only the "custom" entry and a simple flat list — the UI categorizes them visually.
MODEL_PRESET_ITEMS: list[tuple[str, str, str]] = [
    ("_custom", "Custom (manual entry)", "Manually specify repo ID and filename"),
    ("gpt_oss_20b_q4", "GPT-OSS 20B (Q4_K_M)", "[Mid] 8-12 GB RAM, ~12 GB disk — OpenAI reasoning, DEFAULT"),
    ("qwen38_27b_q4", "Qwen3.8-27B (Q4_K_M)", "[Mid] 16-20 GB RAM, ~17 GB disk — vision, 262K ctx"),
    ("fable_fusion_27b_iq4", "Fable Fusion 27B (IQ4_XS)", "[Mid] 12-16 GB RAM, ~17 GB disk — vision, top fine-tune"),
    ("qwen38_27b_q8", "Qwen3.8-27B (Q8_0)", "[Flagship] 24-28 GB RAM, ~29 GB disk — vision, best quality"),
    ("fable_fusion_27b_q6", "Fable Fusion 27B (Q6_K)", "[Flagship] 20-24 GB RAM, ~24 GB disk — vision, ARC-711"),
    ("nail_35b_q4", "Nail 35B A3B (UD-Q4_K_XL)", "[Flagship] 16-20 GB RAM, ~22 GB disk — MoE, vision, fast"),
    ("gemma4_e4b_q4", "Gemma 4 E4B (Q4_K_M)", "[Light] 4-6 GB RAM, ~5 GB disk — vision, function calling"),
    ("qwen35_9b_dsv4_q4", "Qwen3.5-9B DeepSeek-V4-Flash (Q4_K_M)", "[Light] 4-6 GB RAM, ~6 GB disk — vision, distilled reasoning"),
    ("qwen35_9b_q8", "Qwen3.5-9B (Q8_0)", "[Light] 6-8 GB RAM, ~10 GB disk — vision, highest quality light"),
]

# Static preset items for the remote_provider EnumProperty.
_REMOTE_PROVIDER_ITEMS: list[tuple[str, str, str]] = [
    ("openrouter", "OpenRouter", "One key → 300+ models (OpenAI, Anthropic, DeepSeek, etc.)"),
    ("_custom", "Custom (manual entry)", "Manually specify API URL and model name"),
]

REMOTE_PROVIDER_ITEMS: list[tuple[str, str, str]] = _REMOTE_PROVIDER_ITEMS

# Static preset items for the gen_backend EnumProperty.
_GEN_BACKEND_ITEMS: list[tuple[str, str, str]] = [
    (
        "local",
        "Local (Built-in)",
        "Run generative models locally via diffusers/torch — "
        "models downloaded on first use from HuggingFace",
    ),
    (
        "pallaidium",
        "Pallaidium Bridge",
        "Bridge to Pallaidium addon if installed — "
        "access 50+ models through Pallaidium's pipeline",
    ),
    (
        "comfyui",
        "ComfyUI",
        "Connect to a local ComfyUI server — "
        "use custom workflows as generation models",
    ),
    (
        "remote",
        "Remote API",
        "Use a remote OpenAI-compatible generation API "
        "(e.g. fal.ai, LocalAI)",
    ),
]

GEN_BACKEND_ITEMS: list[tuple[str, str, str]] = _GEN_BACKEND_ITEMS

# ── Agent Mode EnumProperty Items ────────────────────────────────────────

AGENT_MODE_ITEMS: list[tuple[str, str, str]] = [
    (
        "SELF_CONTAINED",
        "Self-Contained",
        "Built-in chat UI with managed local LLM or remote API — "
        "everything runs inside Blender (recommended for new users)",
    ),
    (
        "EXTERNAL_HARNESS",
        "External Harness",
        "Bridge-only mode — run the TCP bridge server inside Blender "
        "and connect an external MCP client (Claude Desktop, Cursor, "
        "VS Code, or any MCP-compatible tool)",
    ),
]

# ── Operating Mode EnumProperty Items ────────────────────────────────────
# Unified top-level selector that combines agent_mode + llm_mode.

OPERATING_MODE_ITEMS: list[tuple[str, str, str, str, int]] = [
    (
        "LOCAL_LLM",
        "Local LLM",
        "Run a local LLM via llama-server — everything runs on your machine",
        "CONSOLE",
        0,
    ),
    (
        "REMOTE_API",
        "Remote API",
        "Use a remote API like OpenAI or OpenRouter — no local LLM needed",
        "WORLD",
        1,
    ),
    (
        "EXTERNAL_HARNESS",
        "External Harness",
        "Bridge-only mode — connect an external MCP client "
        "(Claude Desktop, Cursor, VS Code, etc.)",
        "NETWORK_DRIVE",
        2,
    ),
]

# ── MCP Server Mode EnumProperty Items ───────────────────────────────────

MCP_SERVER_MODE_ITEMS: list[tuple[str, str, str]] = [
    (
        "MANAGED",
        "Managed (HTTP)",
        "The addon manages the MCP server as a subprocess with HTTP transport — "
        "used by the built-in chat UI",
    ),
    (
        "STDIO",
        "Stdio (External Client)",
        "The MCP server runs via stdio — for external MCP clients like "
        "Claude Desktop, Cursor, or VS Code. The addon provides config snippets "
        "but does NOT manage the server process",
    ),
    (
        "NETWORK",
        "Network (HTTP Server)",
        "The MCP server listens on a configurable host:port with HTTP transport — "
        "for browser-based clients or remote connections",
    ),
]

# ── Chat Mode EnumProperty Items (Tier 1: Agent/Ask Toggle) ──────────────

CHAT_MODE_ITEMS: list[tuple[str, str, str]] = [
    ("AGENT", "Coworker", "The agent can execute tools and modify the scene"),
    ("ASK", "Ask", "LLM answers questions without modifying anything"),
]

# ── Harness Preset Dataclass ─────────────────────────────────────────────


class HarnessPreset:
    """Metadata for an external MCP client harness preset.

    Each preset describes one supported harness with its config format,
    file location, setup steps, and documentation links.
    """

    __slots__ = (
        "identifier",
        "name",
        "description",
        "icon",
        "is_open_source",
        "config_path_help",
        "setup_steps",
        "docs_url",
        "notes",
    )

    def __init__(
        self,
        identifier: str,
        name: str,
        description: str,
        icon: str = "URL",
        is_open_source: bool = False,
        config_path_help: str = "",
        setup_steps: list[str] | None = None,
        docs_url: str = "",
        notes: str = "",
    ) -> None:
        self.identifier = identifier
        self.name = name
        self.description = description
        self.icon = icon
        self.is_open_source = is_open_source
        self.config_path_help = config_path_help
        self.setup_steps = setup_steps or []
        self.docs_url = docs_url
        self.notes = notes


# ── Harness Presets ──────────────────────────────────────────────────────
# All presets are MCP-compatible and appear in ≥2 of the 3 major "top 10"
# harness rankings (The Tool Nerd, explainx.ai, CellCog) as of Aug 2026.

_HARNESS_PRESETS: list[HarnessPreset] = [
    HarnessPreset(
        identifier="claude_desktop",
        name="Claude Desktop",
        description="Anthropic's desktop app — the original MCP client. Free desktop app for Windows, macOS, Linux.",
        icon="URL",
        is_open_source=False,
        config_path_help=(
            "Windows: %APPDATA%\\Claude\\claude_desktop_config.json\n"
            "macOS: ~/Library/Application Support/Claude/claude_desktop_config.json\n"
            "Linux: ~/.config/Claude/claude_desktop_config.json"
        ),
        setup_steps=[
            "Open Claude Desktop → Settings → Developer → Edit Config",
            "Paste the config into the JSON editor and save",
            "Restart Claude Desktop completely (File → Exit, then re-open)",
            "You should see a hammer icon in the chat input — tools are ready",
        ],
        docs_url="https://modelcontextprotocol.io/quickstart/user",
        notes="Claude Desktop must be fully restarted after config changes. A window close is not enough on some OS versions.",
    ),
    HarnessPreset(
        identifier="claude_code",
        name="Claude Code",
        description="Anthropic's terminal harness — #1 in every 2026 ranking. Richest hook/delegation surface.",
        icon="CONSOLE",
        is_open_source=False,
        config_path_help=(
            "CLAUDE.md project file or ~/.claude/claude_desktop_config.json"
        ),
        setup_steps=[
            "Install Claude Code: npm install -g @anthropic/claude-code",
            "Create or edit ~/.claude/claude_desktop_config.json",
            "Paste the config into the mcpServers section",
            "Run claude in your terminal — tools will auto-discover",
        ],
        docs_url="https://docs.anthropic.com/en/docs/claude-code/overview",
        notes="Claude Code reads MCP config from the same claude_desktop_config.json as Claude Desktop.",
    ),
    HarnessPreset(
        identifier="codex",
        name="Codex CLI",
        description="OpenAI's open-source coding agent (Apache 2.0, 92K+ stars). Terminal-first with desktop app.",
        icon="CONSOLE",
        is_open_source=True,
        config_path_help=(
            "~/.codex/config.json  (or project-level codex.json)"
        ),
        setup_steps=[
            "Install Codex CLI: pip install codex-cli  (or npm install -g @openai/codex)",
            "Create ~/.codex/config.json with the config below",
            "Run codex in your terminal",
            "Verify tools: ask Codex to list available MCP tools",
        ],
        docs_url="https://github.com/openai/codex",
        notes="Codex uses the OpenAI Agents SDK. The python command must have blmcp and its deps available.",
    ),
    HarnessPreset(
        identifier="cursor",
        name="Cursor",
        description="Most popular IDE harness. VS Code fork with native agent mode and MCP support.",
        icon="FILE_TEXT",
        is_open_source=False,
        config_path_help=(
            "~/.cursor/mcp.json  (or .cursor/mcp.json in project root)"
        ),
        setup_steps=[
            "Open Cursor → Settings → Features → MCP Servers",
            "Click 'Add New MCP Server' and paste the config",
            "Or manually edit ~/.cursor/mcp.json",
            "Restart Cursor — the agent will auto-discover Blender tools",
        ],
        docs_url="https://docs.cursor.com/advanced/mcp",
        notes="Cursor's MCP config format uses 'servers' key (not 'mcpServers'). The config below uses the correct format.",
    ),
    HarnessPreset(
        identifier="windsurf",
        name="Windsurf",
        description="IDE-native harness with Cascade agent flow. MCP support for external tools.",
        icon="FILE_TEXT",
        is_open_source=False,
        config_path_help=(
            "~/.codeium/windsurf/mcp_config.json"
        ),
        setup_steps=[
            "Create ~/.codeium/windsurf/mcp_config.json",
            "Paste the config below into the file",
            "Restart Windsurf completely",
            "The Cascade agent will have access to Blender tools",
        ],
        docs_url="https://docs.windsurf.com/mcp",
        notes="Windsurf uses the same MCP config format as VS Code. Config file is auto-created on first launch.",
    ),
    HarnessPreset(
        identifier="cline",
        name="Cline",
        description="Open-source VS Code extension (Apache 2.0, 63K+ stars). Deep MCP integration, 8M+ developers.",
        icon="EXTENSION",
        is_open_source=True,
        config_path_help=(
            "~/.vscode/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
        ),
        setup_steps=[
            "Install Cline from VS Code Marketplace",
            "Open Cline settings → MCP Servers → Add Server",
            "Paste the config into the JSON editor",
            "Restart VS Code — Cline will connect to Blender's bridge",
        ],
        docs_url="https://github.com/cline/cline",
        notes="Cline has a permission-gated tool model — you approve each Blender operation by default.",
    ),
    HarnessPreset(
        identifier="opencode",
        name="OpenCode",
        description="#1 open-source harness (MIT, 176K+ stars). Terminal TUI, desktop app, or IDE-embedded.",
        icon="CONSOLE",
        is_open_source=True,
        config_path_help=(
            "~/.config/opencode/mcp.json  (or project-level opencode.json)"
        ),
        setup_steps=[
            "Install OpenCode: pip install opencode  (or npm install -g opencode)",
            "Create ~/.config/opencode/mcp.json with the config below",
            "Run opencode in your terminal",
            "OpenCode auto-discovers MCP tools on startup",
        ],
        docs_url="https://github.com/sst/opencode",
        notes="OpenCode supports 75+ LLM providers. Point it at any OpenAI-compatible endpoint.",
    ),
    HarnessPreset(
        identifier="generic",
        name="Generic STDIO",
        description="Fallback config for any MCP-compatible client not listed above.",
        icon="SETTINGS",
        is_open_source=False,
        config_path_help=(
            "Varies by client — check your harness documentation for MCP stdio config format."
        ),
        setup_steps=[
            "Find where your MCP client stores its config file",
            "Paste the generic config below into the mcpServers section",
            "Restart your MCP client",
            "If tools don't appear, check the troubleshooting guide",
        ],
        docs_url="",
        notes="The generic config uses the Claude Desktop format (mcpServers key). Adjust the key name if your client uses a different schema.",
    ),
]

# EnumProperty items for the harness_preset selector.
HARNESS_PRESET_ITEMS: list[tuple[str, str, str]] = [
    (p.identifier, p.name, p.description) for p in _HARNESS_PRESETS
]


def get_harness_preset_by_id(identifier: str) -> HarnessPreset | None:
    """Look up a harness preset by its identifier string."""
    for p in _HARNESS_PRESETS:
        if p.identifier == identifier:
            return p
    return None


def get_harness_presets() -> list[HarnessPreset]:
    """Return all harness presets."""
    return list(_HARNESS_PRESETS)