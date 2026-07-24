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
    "DEFAULT_BRIDGE_PORT",
    "DEFAULT_MCP_PORT",
    "DEFAULT_LLM_PORT",
    "effective_ports",
    "get_llm_manager",
    "get_agent_controller",
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


def effective_ports(prefs) -> tuple[int, int, int]:
    """Return (bridge_port, mcp_port, llm_port) with offset applied."""
    offset = prefs.port_offset if hasattr(prefs, 'port_offset') else 0
    return (
        DEFAULT_BRIDGE_PORT + offset,
        DEFAULT_MCP_PORT + offset,
        DEFAULT_LLM_PORT + offset,
    )


# ── Lazy Import Helpers (avoids circular imports) ────────────────────────

def get_llm_manager():
    """Lazy import of llm_manager module."""
    from . import llm_manager as _m
    return _m


def get_agent_controller():
    """Lazy import of agent_controller module."""
    from . import agent_controller as _m
    return _m


# ── Static EnumProperty Items ────────────────────────────────────────────

# Static preset items for the model_preset EnumProperty.
# Must be a module-level constant — callbacks can fail during class registration.
# Only the "custom" entry and a simple flat list — the UI categorizes them visually.
MODEL_PRESET_ITEMS: list[tuple[str, str, str]] = [
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

# Static preset items for the remote_provider EnumProperty.
_REMOTE_PROVIDER_ITEMS: list[tuple[str, str, str]] = [
    ("openrouter", "OpenRouter", "One key → 300+ models (OpenAI, Anthropic, DeepSeek, etc.)"),
    ("_custom", "Custom (manual entry)", "Manually specify API URL and model name"),
]

REMOTE_PROVIDER_ITEMS: list[tuple[str, str, str]] = _REMOTE_PROVIDER_ITEMS