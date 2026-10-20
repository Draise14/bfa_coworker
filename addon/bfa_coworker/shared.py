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
    "is_debug_mode",
    "effective_ports",
    "UPDATE_REPO_URL",
    "update_repo_of_this_install",
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

# -- Default Ports --------------------------------------------------------

DEFAULT_BRIDGE_PORT = 9876
DEFAULT_MCP_PORT = 9191
DEFAULT_LLM_PORT = 8081

# Debug mode: controlled by user-facing preference toggle.
# Previously a hardcoded constant; now reads from preferences at runtime.
def is_debug_mode() -> bool:
    """Return True when the user has enabled debug/diagnostics mode.

    Reads from the add-on preferences at call time.  Falls back to True
    (show diagnostics) when preferences are not yet available (e.g. during
    early registration).
    """
    try:
        import bpy  # pylint: disable=import-error
        prefs = bpy.context.preferences.addons.get(__package__)
        if prefs is not None and hasattr(prefs.preferences, "debug_mode"):
            return prefs.preferences.debug_mode
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return True  # safe default -- show diagnostics until preferences load


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
    # Issue #85: once THIS window's servers are running they may have moved
    # to a free port (another Blender window held the configured one).  Every
    # caller -- MCP launch, health checks, harness configs, the UI -- must use
    # the port this window actually owns, never the configured one.
    try:
        from . import mcp_to_blender_server as _mbs
        if _mbs.is_running() and _mbs.get_actual_port():
            bridge = int(_mbs.get_actual_port())
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    try:
        from . import agent_controller as _ac
        _st = _ac._agent_state
        if getattr(_st, "mcp_server_running", False) and getattr(_st, "mcp_port_actual", 0):
            mcp = int(_st.mcp_port_actual)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return (bridge, mcp, llm)


# -- Updates (issue #86) ----------------------------------------------------

# The Coworker extension repository (Blender's "v1" static listing).  Keep in
# sync with extension_repo.py and installer/bfa_coworker_installer.
UPDATE_REPO_URL = "https://raw.githubusercontent.com/Draise14/bfa_coworker/main/extensions/index.json"


def update_repo_of_this_install():
    """The extension repository this copy is installed in, if it is the
    Coworker update repository; else ``None`` (installed from a file)."""
    try:
        import bpy  # pylint: disable=import-error
        parts = (__package__ or "").split(".")
        if len(parts) < 3 or parts[0] != "bl_ext":
            return None
        module = parts[1]
        for repo in bpy.context.preferences.extensions.repos:
            if repo.module == module:
                if repo.use_remote_url and repo.remote_url.rstrip("/") == UPDATE_REPO_URL:
                    return repo
                return None
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return None


# -- Lazy Import Helpers (avoids circular imports) ------------------------

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


# -- Static EnumProperty Items --------------------------------------------

# Static preset items for the model_preset EnumProperty.
# Must be a module-level constant -- callbacks can fail during class registration.
# Only the "custom" entry and a simple flat list -- the UI categorizes them visually.
MODEL_PRESET_ITEMS: list[tuple[str, str, str]] = [
    ("_custom", "Custom (manual entry)", "Manually specify repo ID and filename"),
    ("gpt_oss_20b_q4", "GPT-OSS 20B (Q4_K_M)", "[Mid] 8-12 GB RAM, ~12 GB disk -- OpenAI reasoning, DEFAULT"),
    ("qwen38_27b_q4", "Qwen3.8-27B (Q4_K_M)", "[Mid] 16-20 GB RAM, ~17 GB disk -- vision, 262K ctx"),
    ("fable_fusion_27b_iq4", "Fable Fusion 27B (IQ4_XS)", "[Mid] 12-16 GB RAM, ~17 GB disk -- vision, top fine-tune"),
    ("qwen38_27b_q8", "Qwen3.8-27B (Q8_0)", "[Flagship] 24-28 GB RAM, ~29 GB disk -- vision, best quality"),
    ("fable_fusion_27b_q6", "Fable Fusion 27B (Q6_K)", "[Flagship] 20-24 GB RAM, ~24 GB disk -- vision, ARC-711"),
    ("nail_35b_q4", "Nail 35B A3B (UD-Q4_K_XL)", "[Flagship] 16-20 GB RAM, ~22 GB disk -- MoE, vision, fast"),
    ("gemma4_e4b_q4", "Gemma 4 E4B (Q4_K_M)", "[Light] 4-6 GB RAM, ~5 GB disk -- vision, function calling"),
    ("qwen35_9b_dsv4_q4", "Qwen3.5-9B DeepSeek-V4-Flash (Q4_K_M)", "[Light] 4-6 GB RAM, ~6 GB disk -- vision, distilled reasoning"),
    ("qwen35_9b_q8", "Qwen3.5-9B (Q8_0)", "[Light] 6-8 GB RAM, ~10 GB disk -- vision, highest quality light"),
]

# Static preset items for the remote_provider EnumProperty.
_REMOTE_PROVIDER_ITEMS: list[tuple[str, str, str]] = [
    ("openrouter", "OpenRouter", "One key -> 300+ models (OpenAI, Anthropic, DeepSeek, etc.)"),
    ("_custom", "Custom (manual entry)", "Manually specify API URL and model name"),
]

REMOTE_PROVIDER_ITEMS: list[tuple[str, str, str]] = _REMOTE_PROVIDER_ITEMS

# Static preset items for the gen_backend EnumProperty.
_GEN_BACKEND_ITEMS: list[tuple[str, str, str]] = [
    (
        "local",
        "Local (Built-in)",
        "Run generative models locally via diffusers/torch -- "
        "models downloaded on first use from HuggingFace",
    ),
    (
        "pallaidium",
        "Pallaidium Bridge",
        "Bridge to Pallaidium addon if installed -- "
        "access 50+ models through Pallaidium's pipeline",
    ),
    (
        "comfyui",
        "ComfyUI",
        "Connect to a local ComfyUI server -- "
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

# -- Agent Mode EnumProperty Items ----------------------------------------

AGENT_MODE_ITEMS: list[tuple[str, str, str]] = [
    (
        "SELF_CONTAINED",
        "Self-Contained",
        "Built-in chat UI with managed local LLM or remote API -- "
        "everything runs inside Blender (recommended for new users)",
    ),
    (
        "EXTERNAL_HARNESS",
        "External Harness",
        "Bridge-only mode -- run the TCP bridge server inside Blender "
        "and connect an external MCP client (Claude Desktop, Cursor, "
        "VS Code, or any MCP-compatible tool)",
    ),
]

# -- Operating Mode EnumProperty Items ------------------------------------
# Unified top-level selector that combines agent_mode + llm_mode.

# How much the chat asks before tools run (#240), for every engine.
APPROVAL_MODE_ITEMS: list[tuple[str, str, str, str, int]] = [
    ("ASK", "Ask",
     "Ask before each tool that changes the scene (Allow once, Always for that tool, Deny), and before "
     "risky steps. Looking at the scene never asks. External Agent: its tool requests; External Harness: "
     "your client asks per tool itself, so only risky steps ask here", "LOCKED", 0),
    ("RISKY", "Risky only",
     "Ask only before risky steps: saving or opening files, quitting, deleting or moving files, exporting, "
     "running another program", "WARNING", 1),
    ("AUTO", "Auto",
     "Never ask: every tool runs straight away (the agent's own tools still follow Only Coworker Tools)",
     "UNLOCKED", 2),
]

OPERATING_MODE_ITEMS: list[tuple[str, str, str, str, int]] = [
    (
        "LOCAL_LLM",
        "Local LLM",
        "Chat here; a model runs on this computer (llama-server, download one in Settings). "
        "Private, works offline",
        "CONSOLE",
        0,
    ),
    (
        "REMOTE_API",
        "Remote API",
        "Chat here; a model from an online service (OpenAI, OpenRouter, ...) with your API key",
        "WORLD",
        1,
    ),
    (
        "EXTERNAL_AGENT",
        "External Agent",
        "Chat here; an agent app on this computer (Claude Code, OpenCode, Codex, Gemini, Copilot) "
        "does the work with Coworker's Blender tools, on your own subscription",
        "LINKED",
        3,
    ),
    (
        "EXTERNAL_HARNESS",
        "External Harness",
        "Chat in another app (Claude Desktop, Cursor, VS Code, ...): it works in Blender through "
        "Coworker's bridge, and Coworker's own chat is off",
        "NETWORK_DRIVE",
        2,
    ),
]

# -- MCP Server Mode EnumProperty Items -----------------------------------

MCP_SERVER_MODE_ITEMS: list[tuple[str, str, str]] = [
    (
        "MANAGED",
        "Managed (HTTP)",
        "The addon manages the MCP server as a subprocess with HTTP transport -- "
        "used by the built-in chat UI",
    ),
    (
        "STDIO",
        "Stdio (External Client)",
        "The MCP server runs via stdio -- for external MCP clients like "
        "Claude Desktop, Cursor, or VS Code. The addon provides config snippets "
        "but does NOT manage the server process",
    ),
    (
        "NETWORK",
        "Network (HTTP Server)",
        "The MCP server listens on a configurable host:port with HTTP transport -- "
        "for browser-based clients or remote connections",
    ),
]

# -- Chat Mode EnumProperty Items (Tier 1: Agent/Ask Toggle) --------------

CHAT_MODE_ITEMS: list[tuple[str, str, str, str, int]] = [
    ("AGENT", "Coworker", "Coworker works in the scene: it runs tools and changes things", "GHOST_ENABLED", 0),
    ("ASK", "Ask", "Ask questions: it answers without changing anything", "QUESTION", 1),
    ("PLAN", "Plan", "Make a step plan for your request without changing anything. Carry it out later -- "
                     "with this engine or another (Local, Remote, an agent): the plan stays in this chat",
     "LINENUMBERS_ON", 2),
]

# -- Harness Preset Dataclass ---------------------------------------------


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
        "chat_paste_hint",
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
        chat_paste_hint: str = "",
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
        self.chat_paste_hint = chat_paste_hint


# -- Harness Presets ------------------------------------------------------
# Config formats, paths and steps below were checked against each client's
# own documentation (issue #100); formats live in harness_clients.py.
_HARNESS_PRESETS: list[HarnessPreset] = [
    HarnessPreset(
        identifier="claude_desktop",
        name="Claude Desktop",
        description="Anthropic's desktop app for Windows and macOS.",
        icon="URL",
        is_open_source=False,
        config_path_help=(
            "Windows: %APPDATA%\\Claude\\claude_desktop_config.json\n"
            "  (some installs: %LOCALAPPDATA%\\Claude-Data\\claude_<id>\\claude_desktop_config.json)\n"
            "macOS: ~/Library/Application Support/Claude/claude_desktop_config.json"
        ),
        setup_steps=[
            "Quit Claude Desktop fully (File -> Exit) -- it rewrites its config while running",
            "Click 'Add to Claude Desktop', or open Settings -> Developer -> Edit Config and merge the copied entry",
            "Reopen Claude Desktop",
            "Check the server under the + menu -> Connectors",
        ],
        docs_url="https://modelcontextprotocol.io/docs/develop/connect-local-servers",
        notes="Claude Desktop must be fully quit and reopened after config changes.",
    ),
    HarnessPreset(
        identifier="claude_code",
        name="Claude Code",
        description="Anthropic's coding agent: terminal CLI, plus VS Code and JetBrains extensions.",
        icon="CONSOLE",
        is_open_source=False,
        config_path_help=(
            "User scope (all projects): ~/.claude.json  (Windows: %USERPROFILE%\\.claude.json)\n"
            "Project scope: .mcp.json in the folder you open with Claude Code"
        ),
        setup_steps=[
            "Click 'Add to Claude Code' -- it writes the server into ~/.claude.json",
            "Or: save the copied config as .mcp.json in your project folder",
            "Restart Claude Code (CLI: exit and re-run claude; VS Code: reload the window)",
            "Run /mcp in Claude Code -- bfa-coworker should show as connected",
        ],
        docs_url="https://code.claude.com/docs/en/mcp",
        notes="Claude Code does NOT read claude_desktop_config.json -- a server set up for Claude Desktop is invisible to it.",
        chat_paste_hint="Claude Code: /mcp only lists servers that are already configured -- use 'Add to Claude Code' or a .mcp.json file to add one.",
    ),
    HarnessPreset(
        identifier="codex",
        name="Codex CLI",
        description="OpenAI's open-source coding agent for the terminal.",
        icon="CONSOLE",
        is_open_source=True,
        config_path_help=(
            "~/.codex/config.toml  (or .codex/config.toml in a trusted project) -- TOML, not JSON"
        ),
        setup_steps=[
            "Click 'Add to Codex CLI', or paste the copied TOML into ~/.codex/config.toml",
            "Or run: codex mcp add bfa-coworker --env BFACW_PORT=<port> -- <command> <args>",
            "Start codex in your terminal",
            "Run codex mcp list to confirm bfa-coworker is listed",
        ],
        docs_url="https://learn.chatgpt.com/docs/extend/mcp?surface=cli",
        notes="Codex reads TOML: each server is a [mcp_servers.<name>] table.",
    ),
    HarnessPreset(
        identifier="cursor",
        name="Cursor",
        description="AI code editor (VS Code fork) with agent mode and MCP support.",
        icon="FILE_TEXT",
        is_open_source=False,
        config_path_help=(
            "Global: ~/.cursor/mcp.json   Project: .cursor/mcp.json"
        ),
        setup_steps=[
            "Click 'Add to Cursor', or merge the copied entry into ~/.cursor/mcp.json",
            "Restart Cursor",
            "Open Customize in the sidebar and check that bfa-coworker is enabled",
        ],
        docs_url="https://cursor.com/docs/context/mcp",
        notes="Cursor uses the mcpServers key with type: stdio.",
    ),
    HarnessPreset(
        identifier="windsurf",
        name="Windsurf",
        description="IDE with the Cascade agent (now documented as Devin Desktop).",
        icon="FILE_TEXT",
        is_open_source=False,
        config_path_help=(
            "Current: %APPDATA%\\devin\\mcp_config.json (Windows), ~/.config/devin/mcp_config.json\n"
            "Older Windsurf: ~/.codeium/windsurf/mcp_config.json"
        ),
        setup_steps=[
            "In Cascade, open ... -> MCPs -> Open MCP config file once (creates it)",
            "Click 'Add to Windsurf', or merge the copied entry into that file",
            "Restart Windsurf / Devin Desktop",
        ],
        docs_url="https://docs.devin.ai/desktop/cascade/mcp",
        notes="Cascade servers are added by editing mcp_config.json; there is no chat-paste or one-click install.",
    ),
    HarnessPreset(
        identifier="cline",
        name="Cline",
        description="Open-source coding agent extension for VS Code and JetBrains.",
        icon="EXTENSION",
        is_open_source=True,
        config_path_help=(
            "IDE: MCP Servers icon -> Configure -> 'Configure MCP Servers' opens cline_mcp_settings.json\n"
            "CLI: ~/.cline/data/settings/cline_mcp_settings.json"
        ),
        setup_steps=[
            "Click the MCP Servers icon in Cline's toolbar",
            "Open the Configure tab and click 'Configure MCP Servers'",
            "Merge the copied entry into mcpServers and save",
        ],
        docs_url="https://docs.cline.bot/mcp/configuring-mcp-servers",
        notes="Cline asks you to approve each tool call unless you add it to autoApprove.",
    ),
    HarnessPreset(
        identifier="opencode",
        name="OpenCode",
        description="Open-source coding agent: terminal TUI, desktop app, or IDE.",
        icon="CONSOLE",
        is_open_source=True,
        config_path_help=(
            "Global: ~/.config/opencode/opencode.json   Project: opencode.json"
        ),
        setup_steps=[
            "Install: npm install -g opencode-ai  (or see the install docs)",
            "Click 'Add to OpenCode', or merge the copied mcp entry into opencode.json",
            "Start opencode",
            "Run opencode mcp list to confirm bfa-coworker is listed",
        ],
        docs_url="https://opencode.ai/docs/mcp-servers/",
        notes="OpenCode uses its own schema: mcp -> {type: local, command: [...], environment}.",
    ),
    HarnessPreset(
        identifier="vscode",
        name="VS Code (Copilot)",
        description="VS Code's built-in MCP support for agent mode.",
        icon="FILE_TEXT",
        is_open_source=False,
        config_path_help=(
            "Workspace: .vscode/mcp.json   User: run 'MCP: Open User Configuration'"
        ),
        setup_steps=[
            "Run 'MCP: Open User Configuration' from the Command Palette (or create .vscode/mcp.json)",
            "Merge the copied entry into the servers section and save",
            "Start the server from the MCP list (MCP: List Servers)",
        ],
        docs_url="https://code.visualstudio.com/docs/copilot/customization/mcp-servers",
        notes="VS Code uses the servers key (not mcpServers).",
    ),
    HarnessPreset(
        identifier="freebuff",
        name="Freebuff (unverified)",
        description="Freebuff agent client -- no public MCP docs found; format unverified.",
        icon="URL",
        is_open_source=True,
        config_path_help=(
            "Unknown -- use Freebuff's MCP / tools settings."
        ),
        setup_steps=[
            "Open Freebuff's MCP / tools settings",
            "Add a new stdio server using the copied command, args and env",
            "Restart Freebuff",
        ],
        docs_url="",
        notes="The copied config uses the common mcpServers format; we couldn't confirm Freebuff reads it.",
    ),
    HarnessPreset(
        identifier="generic",
        name="Generic STDIO",
        description="Common mcpServers format for any MCP client not listed above.",
        icon="SETTINGS",
        is_open_source=False,
        config_path_help=(
            "Varies by client -- check its documentation for MCP stdio servers."
        ),
        setup_steps=[
            "Find where your MCP client stores its servers",
            "Merge the copied bfa-coworker entry into it (rename the top key if your client uses another one)",
            "Restart your MCP client",
        ],
        docs_url="",
        notes="The command, args and env are what every stdio client needs, whatever its key names.",
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