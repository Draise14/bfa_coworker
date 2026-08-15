# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
LLM Manager — handles detection, download, and lifecycle of local/remote LLM backends.

Local mode: manages ``llama-server`` subprocess (downloads models, starts/stops server).
Remote mode: validates API connectivity.

All public functions are thread-safe.
"""

__all__ = (
    "LLMConfig",
    "LLMState",
    "ModelPreset",
    "RemoteProviderPreset",
    "RemoteModelPreset",
    "get_presets",
    "get_preset_by_id",
    "get_remote_providers",
    "get_remote_provider_by_id",
    "get_curated_remote_models",
    "fetch_remote_models",
    "scan_existing_models",
    "find_llama_server",
    "invalidate_llama_server_cache",
    "download_model",
    "download_llama_server",
    "start_local_llama",
    "stop_local_llama",
    "health_check",
    "check_remote_api",
    "get_state",
    "set_config",
    "get_config",
    "_get_models_dir",
    "_set_download_progress",
)

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Constants

_LOCAL_LLM_DEFAULT_PORT = 8081
_LOCAL_LLM_HEALTH_URL = "http://127.0.0.1:{:d}/health"
_LOCAL_LLM_CHAT_URL = "http://127.0.0.1:{:d}/v1/chat/completions"
_MODEL_DOWNLOAD_TIMEOUT = 300  # seconds

# llama-server release download.
_LLAMA_SERVER_VERSION = "b10154"

# Common install locations for llama-server on Windows.
_LLAMA_SEARCH_PATHS_WIN = [
    # PATH is searched automatically via shutil.which().
    # Also check common install dirs.
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "llama.cpp", "llama-server.exe"),
    os.path.join(os.environ.get("PROGRAMFILES", ""), "llama.cpp", "llama-server.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "llama.cpp", "llama-server.exe"),
]


def _get_bundled_llama_dir() -> Path:
    """Return the directory where the addon stores its bundled llama-server binaries.

    Uses ``~/.cache/bfa_coworker_llama/`` so it persists across addon updates
    and does not require Blender's bpy module.  The directory is created on
    first access.
    """
    base = Path.home() / ".cache" / "bfa_coworker_llama"
    base.mkdir(parents=True, exist_ok=True)
    return base


# ---------------------------------------------------------------------------
# Data types

@dataclass
class LLMConfig:
    """Persisted configuration for the LLM backend."""

    mode: str = "local"  # "local" | "remote"
    # Local mode
    llama_path: str = ""
    model_repo_id: str = "unsloth/gemma-4-26B-A4B-it-GGUF"
    model_filename: str = "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
    downloaded_models_dir: str = ""
    local_port: int = _LOCAL_LLM_DEFAULT_PORT
    local_ctx_size: int = 8192
    local_max_tokens: int = 16384  # Max output tokens per API call
    hf_token: str = ""  # HuggingFace token for gated models
    llama_backend: str = "auto"  # "auto" | "cpu" | "cuda" | "vulkan"
    # Remote mode
    remote_api_url: str = ""
    remote_api_key: str = ""
    remote_model: str = ""


@dataclass
class LLMState:
    """Runtime state of the LLM backend."""

    is_running: bool = False
    current_mode: str = "off"  # "off" | "local" | "remote"
    model_name: str = ""
    error: str = ""
    download_progress: str = ""
    download_progress_eta: str = ""  # ETA estimate, e.g. "3m 24s remaining"
    download_progress_pct: float = 0.0  # 0.0 to 100.0
    download_active: bool = False  # True while a model download is in progress
    download_kind: str = ""  # "model" | "llama_server" | ""


# ---------------------------------------------------------------------------
# Model Presets

@dataclass
class ModelPreset:
    """Metadata for a curated model preset shown in the UI dropdown."""

    identifier: str
    name: str
    repo_id: str
    filename: str
    ram_gb: str  # e.g. "16-20 GB"
    disk_gb: str  # e.g. "~16 GB"
    capability: str  # "Excellent" | "Strong" | "Moderate"
    category: str  # "flagship" | "mid_range" | "lightweight"
    description: str  # Longer tooltip text
    context_window: int = 131072  # Context window size in tokens
    max_tokens: int = 16384  # Max output tokens per API call
    vision: bool = False  # Whether the model supports image input
    mmproj_filename: str = ""  # Projector filename for vision (e.g. "mmproj-F16.gguf")
    hardware_note: str = ""  # Hardware recommendation (RAM + GPU gen, e.g. "RTX 3090/4090/5090")
    why: str = ""  # One-line "why pick this" per sub-tier


# ---------------------------------------------------------------------------
# Remote Provider Presets

@dataclass
class RemoteProviderPreset:
    """Metadata for a curated remote API provider preset."""

    identifier: str
    name: str
    base_url: str  # e.g. "https://openrouter.ai/api"
    description: str  # Tooltip text shown in the provider dropdown
    api_key_help: str  # e.g. "Get a key from openrouter.ai/keys"
    models: list["RemoteModelPreset"] = field(default_factory=list)


@dataclass
class RemoteModelPreset:
    """Metadata for a curated remote model shown in the model dropdown."""

    identifier: str  # Full model ID, e.g. "anthropic/claude-sonnet-4.6"
    name: str  # Display name, e.g. "Claude 4.6 Sonnet"
    provider_name: str  # e.g. "Anthropic"
    description: str  # Short tooltip
    context_window: int = 200000  # Default context window size


PRESET_MODELS: list[ModelPreset] = [
    # ── Flagship (24 GB+ VRAM) ──────────────────────────────────────
    ModelPreset(
        identifier="qwen38_27b_q8",
        name="Qwen3.8-27B (Q8_0)",
        repo_id="unsloth/Qwen3.8-27B-GGUF",
        filename="Qwen3.8-27B-Q8_0.gguf",
        ram_gb="24-28 GB",
        disk_gb="~29 GB",
        capability="Excellent",
        category="flagship",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        hardware_note="RTX 3090/4090/5090 — 24 GB+ VRAM",
        why="Latest Qwen3.8 — best coding + vision + agentic reasoning at high precision",
        description=(
            "Qwen3.8-27B at Q8_0 — the latest Qwen generation. Native vision-language,\n"
            "thinking mode, and agentic tool calling. 262K context. Apache 2.0.\n"
            "Best quality flagship for complex multi-step Blender tasks."
        ),
    ),
    ModelPreset(
        identifier="fable_fusion_27b_q6",
        name="Fable Fusion 27B (Q6_K)",
        repo_id="DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF",
        filename="Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-Q6_K.gguf",
        ram_gb="20-24 GB",
        disk_gb="~24 GB",
        capability="Excellent",
        category="flagship",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        hardware_note="RTX 3090/4090/5090 — 24 GB+ VRAM",
        why="Top-ranked fine-tune — ARC-711 benchmark, uncensored, vision-capable",
        description=(
            "Multi-stage fine-tune of Qwen3.6-27B. Exceeds base model in 6/7 benchmarks.\n"
            "Vision-capable, 256K context, uncensored. Apache 2.0.\n"
            "The strongest open 27B fine-tune for agentic work."
        ),
    ),
    ModelPreset(
        identifier="nail_35b_q4",
        name="Nail 35B A3B (UD-Q4_K_XL)",
        repo_id="peculiar-ragdoll/Nail-Qwen3.6-35B-A3B-GGUF",
        filename="Nail-Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",
        ram_gb="16-20 GB",
        disk_gb="~22 GB",
        capability="Excellent",
        category="flagship",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        hardware_note="RTX 3090/4090/5090 — 24 GB+ VRAM (MoE, ~3.4B active)",
        why="MoE efficiency — 3.4B active params, fast inference, sharpened template",
        description=(
            "Qwen3.6-35B-A3B with improved chat template and force-applied terseness prompt.\n"
            "~3.4B active params — runs fast on 24 GB cards. Vision-capable.\n"
            "Apache 2.0. Best throughput-to-quality ratio in flagship tier."
        ),
    ),
    # ── Mid-Range (16-20 GB VRAM) ───────────────────────────────────
    ModelPreset(
        identifier="gpt_oss_20b_q4",
        name="GPT-OSS 20B (Q4_K_M)",
        repo_id="unsloth/gpt-oss-20b-GGUF",
        filename="gpt-oss-20b-Q4_K_M.gguf",
        ram_gb="8-12 GB",
        disk_gb="~12 GB",
        capability="Strong",
        category="mid_range",
        context_window=131072,
        max_tokens=16384,
        vision=False,
        mmproj_filename="",
        hardware_note="RTX 3090/4090 — 12 GB+ VRAM (MoE, 3.6B active)",
        why="OpenAI's open-weight reasoning model — best Blender benchmarked default",
        description=(
            "OpenAI's open-weight reasoning model. 21B params / 3.6B active.\n"
            "Native function calling, structured outputs, and agentic capabilities.\n"
            "Runs within 16 GB RAM. Apache 2.0. Best-tested default for Blender."
        ),
    ),
    ModelPreset(
        identifier="qwen38_27b_q4",
        name="Qwen3.8-27B (Q4_K_M)",
        repo_id="unsloth/Qwen3.8-27B-GGUF",
        filename="Qwen3.8-27B-Q4_K_M.gguf",
        ram_gb="16-20 GB",
        disk_gb="~17 GB",
        capability="Excellent",
        category="mid_range",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        hardware_note="RTX 3090/4090 — 16 GB+ VRAM",
        why="Latest Qwen3.8 at Q4 — vision + agentic, fits 16 GB cards",
        description=(
            "Qwen3.8-27B at Q4_K_M — the latest Qwen generation. Native vision-language,\n"
            "thinking mode, and agentic tool calling. 262K context. Apache 2.0.\n"
            "Fits 16 GB VRAM while keeping excellent quality."
        ),
    ),
    ModelPreset(
        identifier="fable_fusion_27b_iq4",
        name="Fable Fusion 27B (IQ4_XS)",
        repo_id="DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF",
        filename="Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-IQ4_XS.gguf",
        ram_gb="12-16 GB",
        disk_gb="~17 GB",
        capability="Excellent",
        category="mid_range",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        hardware_note="RTX 3090/4090 — 16 GB+ VRAM",
        why="Fable Fusion at IQ4 — fits 16 GB, still top-tier reasoning",
        description=(
            "Fable Fusion 27B at IQ4_XS — smaller quant that still outperforms base Qwen3.6.\n"
            "Vision-capable, 256K context. Apache 2.0.\n"
            "Best mid-range choice for users with 16 GB cards."
        ),
    ),
    # ── Lightweight (≤8 GB VRAM) ────────────────────────────────────
    ModelPreset(
        identifier="gemma4_e4b_q4",
        name="Gemma 4 E4B (Q4_K_M)",
        repo_id="unsloth/gemma-4-E4B-it-GGUF",
        filename="gemma-4-E4B-it-Q4_K_M.gguf",
        ram_gb="4-6 GB",
        disk_gb="~5 GB",
        capability="Strong",
        category="lightweight",
        context_window=131072,
        max_tokens=8192,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        hardware_note="Any GPU or integrated — 4 GB+ VRAM",
        why="Google's small agentic model — vision + function calling, runs anywhere",
        description=(
            "Google's Gemma 4 E4B — 4.5B effective params with native function calling,\n"
            "thinking mode, and vision. 128K context. Apache 2.0.\n"
            "Best all-round light pick — runs on almost any hardware."
        ),
    ),
    ModelPreset(
        identifier="qwen35_9b_dsv4_q4",
        name="Qwen3.5-9B DeepSeek-V4-Flash (Q4_K_M)",
        repo_id="Jackrong/Qwen3.5-9B-DeepSeek-V4-Flash-GGUF",
        filename="Qwen3.5-9B-DeepSeek-V4-Flash-Q4_K_M.gguf",
        ram_gb="4-6 GB",
        disk_gb="~6 GB",
        capability="Strong",
        category="lightweight",
        context_window=262144,
        max_tokens=8192,
        vision=True,
        mmproj_filename="mmproj.gguf",
        hardware_note="Any GPU — 4 GB+ VRAM",
        why="DeepSeek-V4 distilled reasoning — best reasoning-per-GB in light tier",
        description=(
            "Qwen3.5-9B fine-tuned with DeepSeek-V4 reasoning distillation.\n"
            "Vision-capable, 262K context. Apache 2.0.\n"
            "Punches well above its weight for tool calling and reasoning."
        ),
    ),
    ModelPreset(
        identifier="qwen35_9b_q8",
        name="Qwen3.5-9B (Q8_0)",
        repo_id="unsloth/Qwen3.5-9B-GGUF",
        filename="Qwen3.5-9B-Q8_0.gguf",
        ram_gb="6-8 GB",
        disk_gb="~10 GB",
        capability="Strong",
        category="lightweight",
        context_window=262144,
        max_tokens=8192,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        hardware_note="Any GPU — 8 GB+ VRAM",
        why="Highest quality light quant — Q8_0 precision, vision, 262K context",
        description=(
            "Qwen3.5-9B at Q8_0 — highest quality quantization for the light tier.\n"
            "Vision-capable, 262K context, thinking mode. Apache 2.0.\n"
            "Best quality-to-size ratio for users with 8 GB+ VRAM."
        ),
    ),
]


def get_presets() -> list[ModelPreset]:
    """Return the full list of curated model presets."""
    return list(PRESET_MODELS)


def get_preset_by_id(identifier: str) -> ModelPreset | None:
    """Look up a preset by its identifier string. Returns ``None`` if not found."""
    for p in PRESET_MODELS:
        if p.identifier == identifier:
            return p
    return None


# ---------------------------------------------------------------------------
# Remote Provider Presets — OpenRouter curated models

PRESET_REMOTE_PROVIDERS: list[RemoteProviderPreset] = [
    RemoteProviderPreset(
        identifier="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api",
        description=(
            "One API key gives access to 300+ models from OpenAI, Anthropic,\n"
            "DeepSeek, Meta, Google, and more. OpenAI-compatible API.\n"
            "Get a key at openrouter.ai/keys"
        ),
        api_key_help="Create a key at https://openrouter.ai/keys",
        models=[
            RemoteModelPreset(
                identifier="anthropic/claude-sonnet-4.6",
                name="Claude 4.6 Sonnet",
                provider_name="Anthropic",
                description="Best all-around — strong coding, tool use, and reasoning. 200K context.",
                context_window=200000,
            ),
            RemoteModelPreset(
                identifier="openai/gpt-4.1",
                name="GPT-4.1",
                provider_name="OpenAI",
                description="OpenAI's latest flagship. Excellent coding and instruction following.",
                context_window=1000000,
            ),
            RemoteModelPreset(
                identifier="openai/gpt-4o",
                name="GPT-4o",
                provider_name="OpenAI",
                description="Fast multimodal model. Great for quick tool-calling tasks.",
                context_window=128000,
            ),
            RemoteModelPreset(
                identifier="deepseek/deepseek-chat-v3-0324",
                name="DeepSeek Chat V3",
                provider_name="DeepSeek",
                description="Very cost-effective. Strong performance for Blender scripting.",
                context_window=131072,
            ),
            RemoteModelPreset(
                identifier="google/gemini-2.5-flash",
                name="Gemini 2.5 Flash",
                provider_name="Google",
                description="Fast, cheap, and capable. Good for quick Blender tasks.",
                context_window=1048576,
            ),
            RemoteModelPreset(
                identifier="meta-llama/llama-4-maverick",
                name="Llama 4 Maverick",
                provider_name="Meta",
                description="Open-weight model. Strong tool calling. 128K context.",
                context_window=131072,
            ),
            RemoteModelPreset(
                identifier="qwen/qwen3.6-35b-a3b",
                name="Qwen3.6 35B A3B",
                provider_name="Qwen",
                description="MoE architecture — efficient, strong coding. Native MCP support.",
                context_window=131072,
            ),
            RemoteModelPreset(
                identifier="mistralai/mistral-small-3.1-24b",
                name="Mistral Small 3.1 24B",
                provider_name="Mistral",
                description="Compact but capable. Native function calling, 128K context.",
                context_window=131072,
            ),
            RemoteModelPreset(
                identifier="openai/gpt-5-mini",
                name="GPT-5 Mini",
                provider_name="OpenAI",
                description="Fast and affordable. Great for simpler Blender tasks.",
                context_window=262144,
            ),
            RemoteModelPreset(
                identifier="google/gemini-2.5-pro",
                name="Gemini 2.5 Pro",
                provider_name="Google",
                description="Google's most capable model. Excellent reasoning. 1M context.",
                context_window=1048576,
            ),
        ],
    ),
]


def get_remote_providers() -> list[RemoteProviderPreset]:
    """Return the full list of curated remote provider presets."""
    return list(PRESET_REMOTE_PROVIDERS)


def get_remote_provider_by_id(identifier: str) -> RemoteProviderPreset | None:
    """Look up a remote provider preset by its identifier. Returns ``None`` if not found."""
    for p in PRESET_REMOTE_PROVIDERS:
        if p.identifier == identifier:
            return p
    return None


def get_curated_remote_models(provider_id: str) -> list[RemoteModelPreset]:
    """Return the curated model list for a given provider."""
    provider = get_remote_provider_by_id(provider_id)
    if provider is None:
        return []
    return list(provider.models)


def fetch_remote_models(
    base_url: str,
    api_key: str,
) -> tuple[list[dict], str]:
    """
    Fetch the live model list from a remote API's ``/v1/models`` endpoint.

    Supports OpenAI-compatible and OpenRouter-style responses.

    Returns a tuple of ``(models, error)``:
      - ``models`` is a list of dicts with keys ``id``, ``name``, ``owned_by``.
      - ``error`` is an empty string on success, or a message on failure.
    """
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        url = "{:s}/v1/models".format(base)
    else:
        url = "{:s}/models".format(base)

    print("[🛠️Coworker] fetch_remote_models: GET {:s}".format(url))

    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer {:s}".format(api_key)},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
            data = json.loads(raw)

            # OpenRouter wraps models in a "data" array; OpenAI uses a flat array.
            model_list = data.get("data", data if isinstance(data, list) else [])
            if isinstance(model_list, dict):
                model_list = []

            models: list[dict] = []
            for m in model_list:
                if not isinstance(m, dict):
                    continue
                mid = m.get("id", "")
                if not mid:
                    continue
                models.append({
                    "id": mid,
                    "name": mid,
                    "owned_by": m.get("owned_by", ""),
                })

            # Sort alphabetically by id.
            models.sort(key=lambda x: x["id"].lower())

            print("[🛠️Coworker] fetch_remote_models: {:d} models found".format(len(models)))
            return models, ""

    except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
        msg = "Failed to fetch models: {:s}".format(str(ex))
        print("[🛠️Coworker] fetch_remote_models: {:s}".format(msg))
        return [], msg


def scan_existing_models(
    models_dir: str | None = None,
) -> list[dict]:
    """
    Scan the models directory and HuggingFace cache for ``.gguf`` files.

    Returns a list of dicts with keys:
      - ``path`` (str): absolute path to the model file
      - ``filename`` (str): base filename
      - ``size_gb`` (str): human-readable file size
      - ``source`` (str): ``"models_dir"``, ``"hf_cache"``, or ``"custom"``
      - ``repo_id`` (str or None): inferred repo ID if from HF cache
    """
    found: list[dict] = []
    seen_paths: set[str] = set()

    def _add(path: str, source: str, repo_id: str | None = None) -> None:
        if path in seen_paths:
            return
        seen_paths.add(path)
        try:
            size_bytes = os.path.getsize(path)
            if size_bytes > 1024 ** 3:
                size_str = "{:.1f} GB".format(size_bytes / (1024 ** 3))
            elif size_bytes > 1024 ** 2:
                size_str = "{:.0f} MB".format(size_bytes / (1024 ** 2))
            else:
                size_str = "{:.0f} KB".format(size_bytes / 1024)
        except OSError:
            size_str = "?"
        found.append({
            "path": path,
            "filename": os.path.basename(path),
            "size_gb": size_str,
            "source": source,
            "repo_id": repo_id,
        })

    # 1. Scan the configured models directory.
    scan_dir = models_dir
    if not scan_dir:
        with _lock:
            scan_dir = _config.downloaded_models_dir
    if scan_dir and os.path.isdir(scan_dir):
        for root, _dirs, files in os.walk(scan_dir):
            for f in files:
                if f.endswith(".gguf"):
                    _add(os.path.join(root, f), "models_dir")

    # 2. Scan the HuggingFace cache.
    hf_cache_base = Path.home() / ".cache" / "huggingface" / "hub"
    hf_home = os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE")
    if hf_home:
        hf_cache_base = Path(hf_home) / "hub"

    if hf_cache_base.is_dir():
        for cache_dir in hf_cache_base.iterdir():
            if cache_dir.name.startswith("models--"):
                # Parse repo_id from directory name: models--org--repo
                repo_id = cache_dir.name.replace("models--", "").replace("--", "/", 1)
                snapshots_dir = cache_dir / "snapshots"
                if snapshots_dir.is_dir():
                    for root, _dirs, files in os.walk(str(snapshots_dir)):
                        for f in files:
                            if f.endswith(".gguf"):
                                _add(os.path.join(root, f), "hf_cache", repo_id)

    # Sort by filename for consistent UI ordering.
    found.sort(key=lambda x: x["filename"].lower())
    return found


# ---------------------------------------------------------------------------
# Module-level state (thread-safe via lock)

_lock = threading.Lock()
_config: LLMConfig = LLMConfig()
_state: LLMState = LLMState()
_llama_process: "subprocess.Popen | None" = None
# Set to request cancellation of an in-progress model download.
_download_cancel_event = threading.Event()


def get_state() -> LLMState:
    """Return a copy of the current runtime state."""
    with _lock:
        return LLMState(
            is_running=_state.is_running,
            current_mode=_state.current_mode,
            model_name=_state.model_name,
            error=_state.error,
            download_progress=_state.download_progress,
            download_progress_eta=_state.download_progress_eta,
            download_progress_pct=_state.download_progress_pct,
            download_active=_state.download_active,
            download_kind=_state.download_kind,
        )


def set_config(cfg: LLMConfig) -> None:
    """Atomically update the configuration."""
    with _lock:
        _config.mode = cfg.mode
        _config.llama_path = cfg.llama_path
        _config.model_repo_id = cfg.model_repo_id
        _config.model_filename = cfg.model_filename
        _config.downloaded_models_dir = cfg.downloaded_models_dir
        _config.local_port = cfg.local_port
        _config.local_ctx_size = cfg.local_ctx_size
        _config.local_max_tokens = cfg.local_max_tokens
        _config.hf_token = cfg.hf_token
        _config.llama_backend = cfg.llama_backend
        _config.remote_api_url = cfg.remote_api_url
        _config.remote_api_key = cfg.remote_api_key
        _config.remote_model = cfg.remote_model


def get_config() -> LLMConfig:
    """Return a copy of the current configuration."""
    with _lock:
        return LLMConfig(
            mode=_config.mode,
            llama_path=_config.llama_path,
            model_repo_id=_config.model_repo_id,
            model_filename=_config.model_filename,
            downloaded_models_dir=_config.downloaded_models_dir,
            local_port=_config.local_port,
            local_ctx_size=_config.local_ctx_size,
            local_max_tokens=_config.local_max_tokens,
            hf_token=_config.hf_token,
            llama_backend=_config.llama_backend,
            remote_api_url=_config.remote_api_url,
            remote_api_key=_config.remote_api_key,
            remote_model=_config.remote_model,
        )


# ---------------------------------------------------------------------------
# llama-server detection

_find_llama_server_cache: str | None = None
_find_llama_server_checked: bool = False


def find_llama_server() -> str | None:
    """Search PATH and common install locations for ``llama-server``.

    Prefers the active backend's bundled binary (e.g. ``llama-server-cuda.exe``)
    over the generic ``llama-server.exe``.
    """
    global _find_llama_server_cache, _find_llama_server_checked
    if _find_llama_server_checked:
        return _find_llama_server_cache
    _find_llama_server_checked = True

    # Determine the active backend for bundled binary preference.
    with _lock:
        active_backend = _config.llama_backend
    if active_backend == "auto":
        active_backend = _detect_gpu_backend()

    print("[🛠️Coworker] find_llama_server: searching for llama-server (backend={:s})...".format(active_backend))

    # 1. Check the bundled directory for a backend-specific binary first.
    bundled_dir = _get_bundled_llama_dir()
    if active_backend and active_backend != "cpu":
        backend_binary = bundled_dir / "llama-server-{backend}.exe".format(backend=active_backend)
        print("[🛠️Coworker] find_llama_server:   checking bundled {:s}".format(str(backend_binary)))
        if backend_binary.is_file():
            print("[🛠️Coworker] find_llama_server: found bundled backend binary at {:s}".format(str(backend_binary)))
            _find_llama_server_cache = str(backend_binary)
            return str(backend_binary)

    # 2. Search PATH first.
    exe = shutil.which("llama-server")
    if exe:
        print("[🛠️Coworker] find_llama_server: found via 'llama-server' -> {:s}".format(exe))
        _find_llama_server_cache = exe
        return exe
    print("[🛠️Coworker] find_llama_server: 'llama-server' not on PATH, trying 'llama-server.exe'")
    exe = shutil.which("llama-server.exe")
    if exe:
        print("[🛠️Coworker] find_llama_server: found via 'llama-server.exe' -> {:s}".format(exe))
        _find_llama_server_cache = exe
        return exe
    # Fall back to known install paths.
    print("[🛠️Coworker] find_llama_server: not on PATH, checking known install dirs...")
    for path in _LLAMA_SEARCH_PATHS_WIN:
        print("[🛠️Coworker] find_llama_server:   checking {:s}".format(path))
        if os.path.isfile(path):
            print("[🛠️Coworker] find_llama_server: found at {:s}".format(path))
            _find_llama_server_cache = path
            return path
    # Check the generic bundled binary as last resort.
    bundled = bundled_dir / "llama-server.exe"
    print("[🛠️Coworker] find_llama_server:   checking bundled {:s}".format(str(bundled)))
    if bundled.is_file():
        print("[🛠️Coworker] find_llama_server: found bundled at {:s}".format(str(bundled)))
        _find_llama_server_cache = str(bundled)
        return str(bundled)
    print("[🛠️Coworker] find_llama_server: NOT FOUND")
    _find_llama_server_cache = None
    return None


def invalidate_llama_server_cache() -> None:
    """Reset the ``find_llama_server`` cache so the next call re-searches.

    Call this after the user installs llama-server externally or changes
    the configured path, so the addon detects it without a Blender restart.
    """
    global _find_llama_server_cache, _find_llama_server_checked
    print("[🛠️Coworker] invalidate_llama_server_cache: cache cleared")
    _find_llama_server_checked = False
    _find_llama_server_cache = None


# ---------------------------------------------------------------------------
# Model download

def _get_models_dir() -> Path:
    """Return the directory where downloaded models are stored."""
    with _lock:
        custom = _config.downloaded_models_dir
    if custom:
        custom_path = Path(custom)
        if custom_path.exists():
            if custom_path.is_dir():
                print("[🛠️Coworker] _get_models_dir: using custom dir {:s}".format(str(custom_path)))
                return custom_path
            print(
                "[🛠️Coworker] _get_models_dir: custom models dir exists but is not a directory: {:s}".format(
                    str(custom_path)
                )
            )
        else:
            try:
                custom_path.mkdir(parents=True, exist_ok=True)
                print("[🛠️Coworker] _get_models_dir: created custom dir {:s}".format(str(custom_path)))
                return custom_path
            except OSError as ex:
                print(
                    "[🛠️Coworker] _get_models_dir: failed to create custom dir {:s}: {:s}".format(
                        str(custom_path), str(ex)
                    )
                )
    # Default: <user_home>/bfa_coworker_models/
    default = Path.home() / "bfa_coworker_models"
    default.mkdir(parents=True, exist_ok=True)
    print("[🛠️Coworker] _get_models_dir: using default dir {:s}".format(str(default)))
    return default


def _set_download_progress(msg: str) -> None:
    with _lock:
        _state.download_progress = msg


def _set_download_progress_eta(eta: str, pct: float) -> None:
    with _lock:
        _state.download_progress_eta = eta
        _state.download_progress_pct = pct


def _clear_download_state() -> None:
    """Clear download progress, ETA, error, and active flag. Called before a new download."""
    with _lock:
        _state.download_progress = ""
        _state.download_progress_eta = ""
        _state.download_progress_pct = 0.0
        _state.error = ""
        _state.download_active = False
        _state.download_kind = ""


def _set_download_kind(kind: str) -> None:
    """Set the download kind ("model" | "llama_server" | "")."""
    with _lock:
        _state.download_kind = kind


def _detect_gpu_backend() -> str:
    """Detect the best GPU backend for llama-server on this machine.

    Returns one of "cuda", "vulkan", or "cpu".
    """
    if sys.platform != "win32":
        # Non-Windows: default to cpu (or vulkan on Linux if available).
        # We don't auto-detect on macOS/Linux — user can override manually.
        return "cpu"

    # Windows detection.
    # 1. Check for NVIDIA GPU via nvidia-smi.
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            print("[🛠️Coworker] _detect_gpu_backend: NVIDIA GPU detected -> cuda")
            return "cuda"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    # 2. Check for AMD / Intel Arc GPU via wmic.
    try:
        result = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "name"],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout.lower()
        if "amd" in output or "radeon" in output or "intel" in output:
            print("[🛠️Coworker] _detect_gpu_backend: AMD/Intel GPU detected -> vulkan")
            return "vulkan"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    # 3. Fallback to CPU.
    print("[🛠️Coworker] _detect_gpu_backend: no compatible GPU detected -> cpu")
    return "cpu"


def cancel_download() -> None:
    """Request cancellation of an in-progress model download.

    The download thread checks the cancel event between chunks and aborts,
    deleting the partial file. Safe to call even if no download is active.
    """
    print("[🛠️Coworker] cancel_download: cancellation requested")
    _download_cancel_event.set()


def _check_disk_space(dest: Path, required_bytes: int | None) -> bool:
    """Verify there is enough free disk space for the download.

    Returns ``True`` if space is sufficient (or size unknown), ``False``
    otherwise. Sets an error message when space is insufficient.
    """
    if required_bytes is None or required_bytes <= 0:
        return True
    try:
        usage = shutil.disk_usage(str(dest.parent if dest.parent.exists() else dest))
    except OSError as ex:
        print("[🛠️Coworker] _check_disk_space: could not query disk usage — {:s}".format(str(ex)))
        return True  # Can't determine — let the download try anyway.
    # Require the file size plus a 5% safety margin.
    needed = int(required_bytes * 1.05)
    if usage.free < needed:
        msg = (
            "Not enough disk space: need {:s} but only {:s} free on {:s}"
        ).format(_format_bytes(needed), _format_bytes(usage.free), str(dest.parent))
        print("[🛠️Coworker] _check_disk_space: {:s}".format(msg))
        _set_error(msg)
        return False
    print("[🛠️Coworker] _check_disk_space: OK — {:s} free, need {:s}".format(
        _format_bytes(usage.free), _format_bytes(needed)))
    return True


def _format_bytes(bytes_val: float) -> str:
    """Format bytes to a human-readable string (KB/MB/GB)."""
    if bytes_val >= 1024 ** 3:
        return "{:.1f} GB".format(bytes_val / (1024 ** 3))
    if bytes_val >= 1024 ** 2:
        return "{:.0f} MB".format(bytes_val / (1024 ** 2))
    if bytes_val >= 1024:
        return "{:.0f} KB".format(bytes_val / 1024)
    return "{:.0f} B".format(bytes_val)


def _format_eta(seconds: float) -> str:
    """Format seconds into a concise ETA string like '3m 24s remaining'."""
    if seconds < 0 or seconds > 86400:
        return "calculating..."
    mins, secs = divmod(int(seconds), 60)
    if mins > 0:
        return "{:d}m {:02d}s remaining".format(mins, secs)
    return "{:d}s remaining".format(secs)


def _get_time() -> float:
    """Return the current monotonic time (seconds)."""
    import time as _time
    return _time.monotonic()


def _get_hf_file_size(repo_id: str, filename: str) -> int | None:
    """Get the total file size of a HuggingFace model file via a HEAD request.

    Returns the size in bytes, or ``None`` if it cannot be determined.
    """
    url = "https://huggingface.co/{:s}/resolve/main/{:s}".format(repo_id, filename)
    print("[🛠️Coworker] _get_hf_file_size: checking {:s}".format(url))
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            size_str = resp.headers.get("Content-Length")
            if size_str:
                size = int(size_str)
                print("[🛠️Coworker] _get_hf_file_size: size = {:d} bytes ({:s})".format(
                    size, _format_bytes(size)))
                return size
    except (urllib.error.URLError, OSError, ValueError) as ex:
        print("[🛠️Coworker] _get_hf_file_size: failed — {:s}".format(str(ex)))
    return None


def _download_gguf_direct(
    repo_id: str,
    filename: str,
    dest: Path,
    progress_callback: Callable[[str], None] | None = None,
) -> bool:
    """
    Download a GGUF model file directly from HuggingFace via HTTP.

    Streams the file in 64 KB chunks with real progress reporting (percentage,
    ETA, speed). Handles 401/403/404 errors with clear actionable messages.

    Returns ``True`` on success, ``False`` on failure.
    """
    url = "https://huggingface.co/{:s}/resolve/main/{:s}".format(repo_id, filename)
    print("[🛠️Coworker] _download_gguf_direct: url = {:s}".format(url))
    print("[🛠️Coworker] _download_gguf_direct: dest = {:s}".format(str(dest)))

    # Get file size first (informational + progress calculation).
    total_bytes = _get_hf_file_size(repo_id, filename)
    if total_bytes is not None:
        size_hint = _format_bytes(total_bytes)
        print("[🛠️Coworker] _download_gguf_direct: total size = {:s}".format(size_hint))
    else:
        size_hint = "unknown size"

    # Pre-flight: verify enough disk space before committing to a multi-GB download.
    if not _check_disk_space(dest, total_bytes):
        if progress_callback:
            progress_callback(get_state().error or "Not enough disk space")
        return False

    _set_download_progress("Downloading {:s} ({:s}) ...".format(filename, size_hint))
    if progress_callback:
        progress_callback("Downloading {:s} ({:s}) ...".format(filename, size_hint))

    try:
        req = urllib.request.Request(url, method="GET")
        # Pass HF_TOKEN if available (from env var, or configured token).
        hf_token = ""
        with _lock:
            hf_token = _config.hf_token
        if not hf_token:
            hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or ""
        if hf_token:
            req.add_header("Authorization", "Bearer {:s}".format(hf_token))

        with urllib.request.urlopen(req, timeout=120) as resp:
            # Apply a per-chunk socket read timeout so a stalled connection
            # mid-download raises instead of hanging forever. 60s between
            # chunks is generous for any live connection.
            try:
                raw_sock = getattr(getattr(resp, "fp", None), "raw", None)
                sock = getattr(raw_sock, "_sock", None) if raw_sock is not None else None
                if sock is not None:
                    sock.settimeout(60.0)
            except (AttributeError, OSError):
                pass  # Best-effort — if we can't set it, read() uses the default.

            actual_total = int(resp.headers.get("Content-Length", "0")) or total_bytes or 0
            downloaded = 0
            chunk_size = 64 * 1024  # 64 KB
            start_time = _get_time()
            last_update = start_time

            # Ensure parent directory exists.
            dest.parent.mkdir(parents=True, exist_ok=True)

            with open(str(dest), "wb") as f_out:
                while True:
                    # Check for user-requested cancellation between chunks.
                    if _download_cancel_event.is_set():
                        print("[🛠️Coworker] _download_gguf_direct: cancelled by user")
                        f_out.close()
                        if dest.exists():
                            dest.unlink()
                        _set_download_progress("Download cancelled")
                        _set_download_progress_eta("", 0.0)
                        if progress_callback:
                            progress_callback("Download cancelled")
                        return False
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    downloaded += len(chunk)
                    now = _get_time()

                    # Update progress every 200ms to avoid flooding the UI.
                    if now - last_update < 0.2 and actual_total > 0:
                        continue

                    last_update = now
                    if actual_total > 0:
                        pct = downloaded / actual_total * 100.0
                        # Calculate speed and ETA.
                        elapsed = now - start_time
                        if elapsed > 0:
                            speed_bps = downloaded / elapsed
                            remaining_bytes = actual_total - downloaded
                            eta_secs = remaining_bytes / speed_bps if speed_bps > 0 else 0
                            speed_str = "{:s}/s".format(_format_bytes(speed_bps))
                            eta_str = _format_eta(eta_secs)
                            _set_download_progress_eta(
                                "{:.0f}% of {:s} — {:s}".format(pct, _format_bytes(actual_total), eta_str),
                                pct,
                            )
                            msg = "Downloading {:s} ... {:.0f}% ({:s} / {:s}) — {:s}".format(
                                filename, pct,
                                _format_bytes(downloaded),
                                _format_bytes(actual_total),
                                speed_str,
                            )
                        else:
                            _set_download_progress_eta(
                                "{:.0f}% of {:s}".format(pct, _format_bytes(actual_total)),
                                pct,
                            )
                            msg = "Downloading {:s} ... {:.0f}% ({:s} / {:s})".format(
                                filename, pct,
                                _format_bytes(downloaded),
                                _format_bytes(actual_total),
                            )
                    else:
                        msg = "Downloading {:s} ... {:s}".format(filename, _format_bytes(downloaded))
                    _set_download_progress(msg)
                    if progress_callback:
                        progress_callback(msg)

        # Verify the file is not empty/corrupt (basic check).
        if dest.stat().st_size == 0:
            dest.unlink()
            _set_error("Downloaded file is empty — the server may be blocking the request")
            return False

        print("[🛠️Coworker] _download_gguf_direct: download complete — {:s} ({:s})".format(
            str(dest), _format_bytes(dest.stat().st_size)))
        _set_download_progress("Download complete: {:s}".format(filename))
        if progress_callback:
            progress_callback("Download complete: {:s}".format(filename))
        return True

    except urllib.error.HTTPError as ex:
        # Clean up partial download.
        if dest.exists():
            dest.unlink()
        if ex.code == 401:
            msg = (
                "HuggingFace returned 401 (Unauthorized) for {:s}.\n"
                "This repo may require authentication.\n"
                "Set the HF_TOKEN environment variable or use a different model."
            ).format(repo_id)
        elif ex.code == 403:
            msg = (
                "HuggingFace returned 403 (Forbidden) for {:s}.\n"
                "The model may be gated. Visit https://huggingface.co/{:s} to request access."
            ).format(repo_id, repo_id)
        elif ex.code == 404:
            msg = (
                "HuggingFace returned 404 (Not Found) for {:s}/{:s}.\n"
                "The file may not exist. Check the repo ID and filename."
            ).format(repo_id, filename)
        else:
            msg = "Failed to download model (HTTP {:d}: {:s})".format(ex.code, ex.reason)
        print("[🛠️Coworker] _download_gguf_direct: {:s}".format(msg))
        _set_error(msg)
        if progress_callback:
            progress_callback(msg)
        return False

    except (urllib.error.URLError, OSError) as ex:
        # Clean up partial download.
        if dest.exists():
            dest.unlink()
        msg = "Network error while downloading: {:s}".format(str(ex))
        print("[🛠️Coworker] _download_gguf_direct: {:s}".format(msg))
        _set_error(msg)
        if progress_callback:
            progress_callback(msg)
        return False


def download_model(
    repo_id: str | None = None,
    filename: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> Path | None:
    """
    Download a GGUF model from HuggingFace.

    Uses direct HTTP download (streaming with progress). If the direct
    download fails for a non-auth reason, falls back to launching
    ``llama-server --hf-repo/--hf-file`` as a last resort.

    Returns the path to the downloaded model, or ``None`` on failure.
    """
    with _lock:
        r = repo_id or _config.model_repo_id
        f = filename or _config.model_filename
    print("[🛠️Coworker] download_model: repo_id={:s}, filename={:s}".format(r, f))
    if not r or not f:
        print("[🛠️Coworker] download_model: repo ID or filename not configured")
        _set_error("Model repo ID and filename must be configured")
        return None

    models_dir = _get_models_dir()
    dest = models_dir / f
    print("[🛠️Coworker] download_model: dest = {:s}".format(str(dest)))

    # Clear stale state before starting.
    _clear_download_state()
    _set_download_kind("model")
    _download_cancel_event.clear()

    # Check if already downloaded.
    if dest.exists():
        print("[🛠️Coworker] download_model: already exists, skipping download")
        _set_download_progress("Model already downloaded: {:s}".format(str(dest)))
        if progress_callback:
            progress_callback("Model already downloaded: {:s}".format(str(dest)))
        return dest

    # Mark download as active so the UI poll knows we're still working.
    with _lock:
        _state.download_active = True

    import time

    server_port = _LOCAL_LLM_DEFAULT_PORT
    with _lock:
        server_port = _config.local_port or _LOCAL_LLM_DEFAULT_PORT

    def _do_download():
        """Try direct HTTP download first, then fall back to llama-server."""
        try:
            # ── Primary: direct HTTP download ────────────────────────
            success = _download_gguf_direct(r, f, dest, progress_callback)
            if success:
                # Download succeeded — report and done.
                _set_download_progress("Download complete: {:s}".format(f))
                if progress_callback:
                    progress_callback("Model downloaded to {:s}".format(str(dest)))
                return

            # Cancelled — don't fall through to the fallback path.
            if _download_cancel_event.is_set():
                return

            # ── Fallback: llama-server --hf-repo/--hf-file ──────────
            # If direct download failed for a non-auth reason (network
            # restrictions, proxy issues), try the server's built-in downloader.
            error_state = get_state().error or ""
            if "401" in error_state or "403" in error_state or "404" in error_state:
                # Auth/gating/not-found — don't retry, just surface the error.
                return

            print("[🛠️Coworker] download_model: direct download failed, falling back to llama-server --hf-repo")
            _set_download_progress("Downloading via llama-server...")
            if progress_callback:
                progress_callback("Trying alternate download method...")

            # Clear the temporary error from the direct attempt before trying the fallback.
            with _lock:
                _state.error = ""

            proc = start_local_llama(port=server_port)
            if proc is None:
                error = get_state().error or "llama-server failed to start"
                _set_error(error)
                return

            # Poll health until server is ready (download finished).
            deadline = time.time() + 900  # 15 minute timeout for fallback
            poll_interval = 2.0
            while time.time() < deadline:
                if _download_cancel_event.is_set():
                    print("[🛠️Coworker] download_model: fallback cancelled by user")
                    _set_download_progress("Download cancelled")
                    return
                if health_check():
                    _set_download_progress(
                        "Download complete — llama-server is running on port {:d}".format(
                            server_port
                        )
                    )
                    if progress_callback:
                        progress_callback("Model downloaded and server running")
                    return
                # Check if the process died.
                if proc.poll() is not None:
                    error = "llama-server process exited unexpectedly during download"
                    print("[🛠️Coworker] download_model: {:s}".format(error))
                    _set_error(error)
                    return
                time.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.2, 15.0)

            _set_error("Model download timed out (fallback) after 15 minutes")

        except Exception as ex:  # pylint: disable=broad-exception-caught
            _set_error("Download failed: {:s}".format(str(ex)))
            if progress_callback:
                progress_callback("Download failed: {:s}".format(str(ex)))
        finally:
            # Download is done (success or failure) — clear the active flag.
            with _lock:
                _state.download_active = False

    thread = threading.Thread(target=_do_download, daemon=True)
    thread.start()

    # Return None immediately — the download is async.
    # The caller should poll get_state() for progress.
    return None


def _find_model_in_hf_cache(repo_id: str, filename: str) -> str | None:
    """
    Search the HuggingFace cache for a GGUF model file.

    The HF cache layout is:
      ~/.cache/huggingface/hub/models--{org}--{repo}/snapshots/{hash}/{filename}

    Also searches the local models directory's ``.hf_cache/`` subfolder since
    downloads are redirected there (see ``download_model``).

    Returns the full path to the model if found, or ``None``.
    """
    # Normalize the repo_id for the cache directory name.
    cache_dir_name = "models--{:s}".format(repo_id.replace("/", "--"))

    # Collect all possible cache roots to search.
    cache_roots: list[Path] = []

    # 1. Local models dir .hf_cache (primary — redirected downloads).
    with _lock:
        local_models = _config.downloaded_models_dir
    if local_models and os.path.isdir(local_models):
        cache_roots.append(Path(local_models) / ".hf_cache" / "hub")
    else:
        default_local = Path.home() / "bfa_coworker_models"
        cache_roots.append(default_local / ".hf_cache" / "hub")

    # 2. Standard HF cache location.
    cache_roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    # 3. HF_HOME / HF_HUB_CACHE env var.
    hf_home = os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE")
    if hf_home:
        cache_roots.append(Path(hf_home) / "hub")

    for root in cache_roots:
        cache_dir = root / cache_dir_name
        if not cache_dir.is_dir():
            continue
        # Walk the snapshots directory looking for the filename.
        for walk_root, _dirs, files in os.walk(str(cache_dir)):
            for candidate in files:
                if candidate == filename:
                    found = os.path.join(walk_root, candidate)
                    print("[🛠️Coworker] _find_model_in_hf_cache: found {:s}".format(found))
                    return found

    print("[🛠️Coworker] _find_model_in_hf_cache: {:s} not found in cache".format(filename))
    return None


# ---------------------------------------------------------------------------
# llama-server binary download

def download_llama_server(
    progress_callback: Callable[[str], None] | None = None,
    backend: str | None = None,
) -> str | None:
    """
    Download and extract the ``llama-server`` binary from GitHub releases.

    Downloads the latest compatible release zip from the
    ``ggml-org/llama.cpp`` repository and extracts ``llama-server.exe``
    (or the platform-equivalent binary) into the bundled directory
    (``~/.cache/bfa_coworker_llama/``).

    *backend* — one of ``"auto"``, ``"cpu"``, ``"cuda"``, ``"vulkan"``.
      If ``None`` or ``"auto"``, auto-detects via :func:`_detect_gpu_backend`.
      On Windows, CUDA 12.4 also downloads ``cudart`` DLLs.

    Returns the absolute path to the extracted binary, or ``None`` on
    failure.  Progress is reported via ``_state.download_progress`` and
    the optional *progress_callback*.
    """
    _clear_download_state()
    _set_download_kind("llama_server")

    # Resolve backend.
    if backend is None or backend == "auto":
        backend = _detect_gpu_backend()

    # Determine platform and architecture.
    # Asset naming convention (as of b10154):
    #   Windows: llama-{tag}-bin-win-{variant}-{arch}.zip
    #   macOS:   llama-{tag}-bin-macos-{arch}.tar.gz
    #   Ubuntu:  llama-{tag}-bin-ubuntu-{variant}-{arch}.tar.gz
    if sys.platform == "win32":
        platform = "win"
        arch = "x64"
        binary_name = "llama-server.exe"
        archive_ext = ".zip"
        # Map backend to variant string.
        if backend == "cuda":
            variant = "cuda-12.4"
        elif backend == "vulkan":
            variant = "vulkan"
        else:
            variant = "cpu"
    elif sys.platform == "darwin":
        platform = "macos"
        arch = "arm64" if os.uname().machine == "arm64" else "x64"
        binary_name = "llama-server"
        variant = ""
        archive_ext = ".tar.gz"
    else:
        platform = "ubuntu"
        arch = "x64"
        binary_name = "llama-server"
        variant = "vulkan" if backend == "vulkan" else "cpu"
        archive_ext = ".tar.gz"

    tag = _LLAMA_SERVER_VERSION
    # Build the download URL.
    if variant:
        url = (
            "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/"
            "llama-{tag}-bin-{platform}-{variant}-{arch}{ext}"
        ).format(tag=tag, platform=platform, variant=variant, arch=arch, ext=archive_ext)
    else:
        url = (
            "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/"
            "llama-{tag}-bin-{platform}-{arch}{ext}"
        ).format(tag=tag, platform=platform, arch=arch, ext=archive_ext)

    # CUDA cudart DLLs URL (Windows only).
    cudart_url: str | None = None
    if sys.platform == "win32" and backend == "cuda":
        cudart_url = (
            "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/"
            "cudart-llama-bin-win-cuda-12.4-x64.zip"
        ).format(tag=tag)

    dest_dir = _get_bundled_llama_dir()
    # Use a backend-specific binary name so multiple backends can coexist.
    backend_suffix = backend if backend != "cpu" else ""
    if backend_suffix:
        dest_binary = dest_dir / "llama-server-{backend}.exe".format(backend=backend_suffix)
    else:
        dest_binary = dest_dir / binary_name

    # Check if already downloaded.
    if dest_binary.is_file():
        msg = "llama-server already downloaded at {:s}".format(str(dest_binary))
        print("[🛠️Coworker] download_llama_server: {:s}".format(msg))
        _set_download_progress(msg)
        if progress_callback:
            progress_callback(msg)
        return str(dest_binary)

    _set_download_progress("Downloading llama-server ({:s}) from {:s} ...".format(backend, url))
    if progress_callback:
        progress_callback("Downloading llama-server ({:s}) ...".format(tag))

    print("[🛠️Coworker] download_llama_server: url = {:s}".format(url))
    print("[🛠️Coworker] download_llama_server: dest_dir = {:s}".format(str(dest_dir)))

    try:
        # Stream the zip download with progress.
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=120) as resp:
            total_size = int(resp.headers.get("Content-Length", "0"))
            downloaded = 0
            chunk_size = 64 * 1024  # 64 KB
            data = io.BytesIO()

            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                data.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = downloaded / total_size * 100.0
                    _set_download_progress_eta(
                        "{:.0f}% of {:s}".format(pct, _format_bytes(total_size)),
                        pct,
                    )
                    msg = "Downloading llama-server ... {:.0f}% ({:s} / {:s})".format(
                        pct, _format_bytes(downloaded), _format_bytes(total_size),
                    )
                else:
                    msg = "Downloading llama-server ... {:s}".format(_format_bytes(downloaded))
                _set_download_progress(msg)
                if progress_callback:
                    progress_callback(msg)

        # Extract the archive.
        _set_download_progress("Extracting llama-server ...")
        if progress_callback:
            progress_callback("Extracting llama-server ...")

        data.seek(0)
        if archive_ext == ".zip":
            with zipfile.ZipFile(data) as zf:
                binary_members = [
                    m for m in zf.namelist()
                    if m.endswith(binary_name) or m.endswith("/" + binary_name)
                ]
                if not binary_members:
                    _set_error(
                        "Could not find {:s} in the downloaded archive".format(binary_name)
                    )
                    return None
                temp_dir = dest_dir / ".tmp_extract"
                temp_dir.mkdir(parents=True, exist_ok=True)
                zf.extract(binary_members[0], str(temp_dir))
                extracted = temp_dir / binary_members[0]
        else:
            with tarfile.open(fileobj=data, mode="r:gz") as tf:
                binary_members = [
                    m for m in tf.getmembers()
                    if m.name.endswith(binary_name) or m.name.endswith("/" + binary_name)
                ]
                if not binary_members:
                    _set_error(
                        "Could not find {:s} in the downloaded archive".format(binary_name)
                    )
                    return None
                temp_dir = dest_dir / ".tmp_extract"
                temp_dir.mkdir(parents=True, exist_ok=True)
                tf.extract(binary_members[0], str(temp_dir))
                extracted = temp_dir / binary_members[0].name

        # Move to final location.
        if dest_binary.exists():
            dest_binary.unlink()
        shutil.move(str(extracted), str(dest_binary))
        # Cleanup temp dir.
        shutil.rmtree(str(temp_dir), ignore_errors=True)

        # Download and extract cudart DLLs for CUDA backend.
        if cudart_url:
            _set_download_progress("Downloading CUDA runtime DLLs ...")
            if progress_callback:
                progress_callback("Downloading CUDA runtime DLLs ...")
            try:
                cudart_req = urllib.request.Request(cudart_url, method="GET")
                with urllib.request.urlopen(cudart_req, timeout=120) as cudart_resp:
                    cudart_data = io.BytesIO(cudart_resp.read())
                with zipfile.ZipFile(cudart_data) as cudart_zf:
                    cudart_zf.extractall(str(dest_dir))
                print("[🛠️Coworker] download_llama_server: cudart DLLs extracted to {:s}".format(str(dest_dir)))
            except (urllib.error.URLError, OSError, zipfile.BadZipFile) as ex:
                print("[🛠️Coworker] download_llama_server: cudart download failed — {:s}".format(str(ex)))
                # Non-fatal — the server may still work if CUDA is installed system-wide.

        # Make executable on non-Windows.
        if sys.platform != "win32":
            dest_binary.chmod(dest_binary.stat().st_mode | 0o111)

        msg = "llama-server installed at {:s}".format(str(dest_binary))
        print("[🛠️Coworker] download_llama_server: {:s}".format(msg))
        _set_download_progress(msg)
        if progress_callback:
            progress_callback(msg)
        # Invalidate the cache so find_llama_server picks up the new binary.
        global _find_llama_server_checked, _find_llama_server_cache
        _find_llama_server_checked = False
        _find_llama_server_cache = None
        return str(dest_binary)

    except urllib.error.HTTPError as ex:
        err = "Failed to download llama-server (HTTP {:d}: {:s})".format(
            ex.code, ex.reason
        )
        print("[🛠️Coworker] download_llama_server: {:s}".format(err))
        _set_error(err)
        if progress_callback:
            progress_callback(err)
        return None
    except (urllib.error.URLError, OSError, zipfile.BadZipFile) as ex:
        err = "Failed to download/extract llama-server: {:s}".format(str(ex))
        print("[🛠️Coworker] download_llama_server: {:s}".format(err))
        _set_error(err)
        if progress_callback:
            progress_callback(err)
        return None


# ---------------------------------------------------------------------------
# Local LLM lifecycle


def start_local_llama(
    model_path: Path | str | None = None,
    port: int | None = None,
) -> "subprocess.Popen | None":
    """
    Launch ``llama-server`` as a subprocess.

    *model_path* — if ``None``, uses the configured model.  If the local
      file does NOT exist, passes ``--hf-repo``/``--hf-file`` to
      ``llama-server`` so it auto-downloads via HuggingFace.
    *port* — if ``None``, uses the configured local port.
    Returns the ``Popen`` handle, or ``None`` on failure.
    """
    global _llama_process

    print("[🛠️Coworker] start_local_llama: called")
    print("[🛠️Coworker] start_local_llama:   model_path={:s}".format(str(model_path)))
    print("[🛠️Coworker] start_local_llama:   port={:s}".format(str(port)))

    with _lock:
        if _llama_process is not None and _llama_process.poll() is None:
            print("[🛠️Coworker] start_local_llama: already running, returning None")
            _set_error("llama-server is already running")
            return None

    server_exe = find_llama_server()
    if not server_exe:
        # Re-search once in case the user installed llama-server since the
        # first (cached) lookup. This avoids requiring a Blender restart.
        invalidate_llama_server_cache()
        server_exe = find_llama_server()
    if not server_exe:
        print("[🛠️Coworker] start_local_llama: server_exe not found, aborting")
        _set_error(
            "llama-server not found — use \"Download llama-server\" in preferences "
            "or set the path manually"
        )
        return None

    print("[🛠️Coworker] start_local_llama: server_exe = {:s}".format(server_exe))

    # Resolve model source.  We prefer a local .gguf file, but fall back
    # to ``--hf-repo``/``--hf-file`` so llama-server can auto-download.
    use_hf: bool = False
    hf_repo: str = ""
    hf_file: str = ""

    if model_path is None:
        models_dir = _get_models_dir()
        with _lock:
            fname = _config.model_filename
            repo = _config.model_repo_id
        model_path = models_dir / fname if fname else None
        print("[🛠️Coworker] start_local_llama: resolved model_path = {:s}".format(str(model_path)))

    if model_path and os.path.isfile(str(model_path)):
        print("[🛠️Coworker] start_local_llama: local model file exists at {:s}".format(str(model_path)))
    else:
        # No local .gguf — try the HuggingFace cache first.
        print("[🛠️Coworker] start_local_llama: local model NOT found, checking HF cache...")
        with _lock:
            repo = _config.model_repo_id
            fname = _config.model_filename
        hf_cached = _find_model_in_hf_cache(repo, fname)
        if hf_cached:
            model_path = Path(hf_cached)
            print("[🛠️Coworker] start_local_llama: using HF cached model at {:s}".format(hf_cached))
        else:
            # Not in cache either — try --hf-repo/--hf-file as last resort.
            print("[🛠️Coworker] start_local_llama: not in HF cache either, will use --hf-repo/--hf-file")
            hf_repo = repo
            hf_file = fname
            use_hf = True

    if port is None:
        with _lock:
            port = _config.local_port
        print("[🛠️Coworker] start_local_llama: using configured port {:d}".format(port))

    with _lock:
        ctx_size = _config.local_ctx_size or 8192
    # Auto-upgrade from the old 8192 default to 32768 for existing users.
    # 8192 is too small for system prompt + tools + conversation.
    if ctx_size <= 8192:
        ctx_size = 32768
        print("[🛠️Coworker] start_local_llama: auto-upgraded ctx_size from 8192 to 32768")
    print("[🛠️Coworker] start_local_llama: using ctx_size {:d}".format(ctx_size))

    print("[🛠️Coworker] start_local_llama: platform = {:s}".format(sys.platform))

    try:
        # Build args and environment (shared across platforms).
        # Determine GPU offload layers based on backend.
        with _lock:
            backend = _config.llama_backend
        if backend == "auto":
            backend = _detect_gpu_backend()
        ngpu_layers = 99 if backend in ("cuda", "vulkan") else 0

        args = [
            server_exe,
            '--jinja',
            '--verbose',
            '--host', '127.0.0.1',
            '--port', str(port),
            '--ctx-size', str(ctx_size),
            '--n-gpu-layers', str(ngpu_layers),
        ]
        if use_hf:
            args.extend(['--hf-repo', hf_repo, '--hf-file', hf_file])
        else:
            args.extend(['--model', str(model_path)])

        # Redirect HF cache into models dir so all downloads are
        # consolidated in the user's configured models directory.
        hf_cache_dir = _get_models_dir() / ".hf_cache"
        hf_cache_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HF_HOME"] = str(hf_cache_dir)
        env["HF_HUB_CACHE"] = str(hf_cache_dir)
        # Pass HF_TOKEN for gated models.
        with _lock:
            cfg_token = _config.hf_token
        if cfg_token:
            env["HF_TOKEN"] = cfg_token

        if sys.platform == "win32":
            # Launch llama-server in a NEW console window so the user can
            # see server output and close the window to stop it.
            # subprocess.CREATE_NEW_CONSOLE (0x00000010) gives us a proper
            # Popen handle that terminates the actual server, not a wrapper.
            # This avoids the broken PowerShell Start-Process path which
            # silently fails to capture the PID and never starts the server.
            print("[🛠️Coworker] start_local_llama: WIN32 path (CREATE_NEW_CONSOLE)")
            print("[🛠️Coworker] start_local_llama:   args = {:s}".format(str(args)))
            print("[🛠️Coworker] start_local_llama:   HF_HOME = {:s}".format(str(hf_cache_dir)))
            creationflags = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
            with open(os.devnull, 'w') as devnull:
                proc = subprocess.Popen(
                    args,
                    stdin=devnull,
                    stdout=devnull,
                    stderr=devnull,
                    creationflags=creationflags,
                    env=env,
                )
            print("[🛠️Coworker] start_local_llama:   Popen returned pid={:d}".format(proc.pid))
        else:
            # Linux / macOS: detach from the parent process group so the
            # server survives Blender exiting.  We redirect stdio to
            # /dev/null so it doesn't hijack the Blender console.
            print("[🛠️Coworker] start_local_llama: POSIX path (start_new_session=True)")
            print("[🛠️Coworker] start_local_llama:   args = {:s}".format(str(args)))
            with open(os.devnull, 'w') as devnull:
                proc = subprocess.Popen(
                    args,
                    stdin=devnull,
                    stdout=devnull,
                    stderr=devnull,
                    start_new_session=True,
                )
                print("[🛠️Coworker] start_local_llama:   Popen returned pid={:d}".format(proc.pid))

    except FileNotFoundError:
        print("[🛠️Coworker] start_local_llama: FileNotFoundError — binary not found")
        _set_error("Failed to launch llama-server — binary not found")
        return None
    except OSError as ex:
        print("[🛠️Coworker] start_local_llama: OSError — {:s}".format(str(ex)))
        _set_error("Failed to launch llama-server: {:s}".format(str(ex)))
        return None

    _llama_process = proc
    with _lock:
        _state.is_running = True
        _state.current_mode = "local"
        _state.model_name = os.path.basename(hf_file or str(model_path or ""))
        _state.error = ""
        _state.download_progress = ""

    print("[🛠️Coworker] start_local_llama: SUCCESS — server launched")
    return proc


def stop_local_llama() -> None:
    """Gracefully terminate the ``llama-server`` subprocess."""
    global _llama_process

    print("[🛠️Coworker] stop_local_llama: called")

    with _lock:
        proc = _llama_process

    print("[🛠️Coworker] stop_local_llama:   tracked proc = {:s}".format(str(proc)))

    # Try to terminate the tracked process first.
    if proc is not None:
        try:
            print("[🛠️Coworker] stop_local_llama:   calling proc.terminate()")
            proc.terminate()
            print("[🛠️Coworker] stop_local_llama:   waiting up to 3s for exit...")
            proc.wait(timeout=3)
            print("[🛠️Coworker] stop_local_llama:   process exited")
        except subprocess.TimeoutExpired:
            print("[🛠️Coworker] stop_local_llama:   timeout — killing")
            proc.kill()
            proc.wait(timeout=3)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[🛠️Coworker] stop_local_llama:   exception during terminate: {:s}".format(str(ex)))

    _llama_process = None

    # Fallback: kill any remaining llama-server processes by image name.
    # This catches orphaned processes from previous sessions that may
    # still be holding the port.
    try:
        if sys.platform == "win32":
            print("[🛠️Coworker] stop_local_llama:   running taskkill /f /im llama-server.exe")
            result = subprocess.run(
                ["taskkill", "/f", "/im", "llama-server.exe"],
                capture_output=True,
                timeout=5,
            )
            print("[🛠️Coworker] stop_local_llama:   taskkill stdout = {:s}".format(result.stdout.decode().strip()))
            print("[🛠️Coworker] stop_local_llama:   taskkill stderr = {:s}".format(result.stderr.decode().strip()))
        else:
            print("[🛠️Coworker] stop_local_llama:   running pkill -f llama-server")
            result = subprocess.run(
                ["pkill", "-f", "llama-server"],
                capture_output=True,
                timeout=5,
            )
            print("[🛠️Coworker] stop_local_llama:   pkill stdout = {:s}".format(result.stdout.decode().strip()))
            print("[🛠️Coworker] stop_local_llama:   pkill stderr = {:s}".format(result.stderr.decode().strip()))
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[🛠️Coworker] stop_local_llama:   fallback kill exception: {:s}".format(str(ex)))

    with _lock:
        _state.is_running = False
        _state.current_mode = "off"

    print("[🛠️Coworker] stop_local_llama: done")


# ---------------------------------------------------------------------------
# Health checks

def health_check(url: str | None = None) -> bool:
    """
    Ping the LLM backend to confirm it is ready.

    *url* — defaults to ``http://127.0.0.1:{port}/health`` using the configured port.
    """
    if url is None:
        with _lock:
            port = _config.local_port
        url = _LOCAL_LLM_HEALTH_URL.format(port)
    print("[🛠️Coworker] health_check: pinging {:s} ...".format(url))
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            ok = resp.status == 200
            print("[🛠️Coworker] health_check: status={:d} -> {:s}".format(resp.status, "OK" if ok else "FAIL"))
            return ok
    except (urllib.error.URLError, OSError) as ex:
        print("[🛠️Coworker] health_check: connection failed — {:s}".format(str(ex)))
        return False


def wait_until_ready(timeout: float = 60.0, proc: "subprocess.Popen | None" = None) -> bool:
    """Block until the local llama-server answers a health check.

    Polls :func:`health_check` until it succeeds or *timeout* seconds elapse.
    If *proc* is given and the process exits early, returns ``False``
    immediately with an error set. Returns ``True`` when the server is ready.
    """
    import time as _time
    deadline = _time.monotonic() + timeout
    poll = 0.5
    print("[🛠️Coworker] wait_until_ready: waiting up to {:.0f}s for llama-server...".format(timeout))
    while _time.monotonic() < deadline:
        if health_check():
            print("[🛠️Coworker] wait_until_ready: server is ready")
            return True
        if proc is not None and proc.poll() is not None:
            _set_error("llama-server exited during startup (check model path and port)")
            print("[🛠️Coworker] wait_until_ready: process exited early")
            return False
        _time.sleep(poll)
        poll = min(poll * 1.5, 3.0)
    _set_error("llama-server did not become ready within {:.0f}s".format(timeout))
    print("[🛠️Coworker] wait_until_ready: timed out")
    return False


def check_remote_api(base_url: str, api_key: str) -> bool:
    """
    Validate a remote API connection by listing models.

    Supports OpenAI-compatible endpoints (OpenRouter, OpenAI, etc.).
    Intelligently handles URLs with or without the ``/v1`` prefix.
    """
    base = base_url.rstrip("/")
    # Avoid double ``/v1`` if the URL already includes it.
    if not base.endswith("/v1"):
        url = "{:s}/v1/models".format(base)
    else:
        url = "{:s}/models".format(base)

    print("[🛠️Coworker] check_remote_api: checking {:s}".format(url))

    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer {:s}".format(api_key)},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            ok = "data" in data
            print("[🛠️Coworker] check_remote_api: status={:d}, has_data={:s}".format(resp.status, str(ok)))
            return ok
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
        print("[🛠️Coworker] check_remote_api: failed — {:s}".format(str(ex)))
        return False


# ---------------------------------------------------------------------------
# Internal helpers

def _set_error(msg: str) -> None:
    with _lock:
        _state.error = msg


# ---------------------------------------------------------------------------
# Cleanup helper (call from unregister)

def cleanup() -> None:
    """Stop the local LLM if running. Safe to call multiple times."""
    stop_local_llama()