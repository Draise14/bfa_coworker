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
    ("AGENT", "Agent", "LLM can execute tools and modify the scene"),
    ("ASK", "Ask", "LLM answers questions without modifying anything"),
]