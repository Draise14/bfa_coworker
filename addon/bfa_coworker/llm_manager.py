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
    "remove_llama_server",
    "start_local_llama",
    "stop_local_llama",
    "get_llama_process",
    "get_llama_server_log_tail",
    "health_check",
    "check_remote_api",
    "get_state",
    "set_config",
    "get_config",
    "_get_models_dir",
    "_set_download_progress",
    "detect_system_ram_gb",
    "detect_vram_gb",
    "recommend_context_size",
    "hardware_context_hint",
    "resolve_gpu_backend",
    "ctx_preset_label",
    "ctx_preset_sizes",
)

import io
import ctypes
import hashlib
import re
import json
import os
import shutil
import socket
import struct
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

def _port_is_taken(port: int) -> bool:
    """Return True if *port* is already in use on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True

def _find_free_port(start: int, attempts: int = 10) -> int:
    """Find a free port starting from *start*, scanning upward."""
    for offset in range(attempts):
        port = start + offset
        if not _port_is_taken(port):
            if offset > 0:
                print("[Coworker] port {:d} busy; using {:d} instead".format(start, port))
            return port
    raise RuntimeError(
        "Ports {:d}-{:d} are all in use. Kill stray llama-server processes.".format(
            start, start + attempts - 1)
    )


# llama-server release download.
_LLAMA_SERVER_VERSION = "b10154"

# Minimum build number required for Qwen3 hybrid (SSM/Mamba) architecture.
# Builds before this lack blk.*.ssm_conv1d.weight support.
_MIN_SUPPORTED_BUILD = 9500

def _parse_llama_build_number(version_line: str) -> int:
    """Extract the numeric build number from a llama-server --version line.

    Example input: 'version: 8966 (7b8443ac7)'
    Returns 0 if parsing fails.
    """
    import re
    m = re.search(r'version:\s*(\d+)', version_line)
    return int(m.group(1)) if m else 0

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
# llama-server log capture
#
# llama-server is launched with stdio redirected to this log file so that
# startup failures (bad model path, mismatched mmproj, OOM, outdated binary,
# ...) are visible to the addon instead of vanishing into a devnull/console
# void.  On failure the tail is surfaced automatically (see
# :func:`wait_until_ready` / :func:`get_llama_server_log_tail`).

_LLAMA_SERVER_LOG_NAME = "llama-server.log"


def _llama_server_log_path() -> Path:
    """Return the path of the llama-server log file (recreated each launch)."""
    return _get_bundled_llama_dir() / _LLAMA_SERVER_LOG_NAME


def get_llama_server_log_tail(n_lines: int = 40, max_chars: int = 4000) -> str:
    """Return the tail of the most recent llama-server log.

    Used to surface the real startup error when llama-server exits early
    or fails to become ready.  Returns an empty string if no log exists yet.
    """
    log_path = _llama_server_log_path()
    if not log_path.is_file():
        return ""
    try:
        with open(str(log_path), "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    tail = "".join(lines[-n_lines:]).strip()
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
        newline = tail.find("\n")
        if newline != -1:
            tail = tail[newline + 1:]
    return tail


_llama_server_version_cache: str = ""


def _llama_server_version(server_exe: str) -> str:
    """Return the llama-server build version string (cached per session).

    The version is logged at launch — an outdated llama.cpp build is a common
    reason a brand-new preset fails to load (unknown model architecture).
    """
    global _llama_server_version_cache
    if _llama_server_version_cache:
        return _llama_server_version_cache
    try:
        result = subprocess.run(
            [server_exe, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        output = (result.stdout or result.stderr or "").strip()
        _llama_server_version_cache = output.splitlines()[0] if output else "unknown"
    except Exception:  # pylint: disable=broad-exception-caught
        _llama_server_version_cache = "unknown"
    return _llama_server_version_cache


# ---------------------------------------------------------------------------
# Truncated / corrupt model detection
#
# The most common "llama-server crashes at startup" cause with local files is
# a GGUF that was cut off mid-download or mid-copy — llama-server fails with
# ``missing tensor ...`` after loading a few dozen layers.  We compare the
# local file size against the size on HuggingFace (for curated presets) and
# surface an actionable hint.

_hf_size_cache: dict[tuple[str, str], int | None] = {}


def _hf_repo_file_size(repo_id: str, filename: str) -> int | None:
    """Return the expected size (bytes) of a preset file on HuggingFace.

    Uses a HEAD request to the HF ``resolve`` URL.  Cached per session.
    Returns ``None`` when the size can't be determined (offline, gated repo,
    network error) so callers can silently skip the check.
    """
    key = (repo_id, filename)
    if key in _hf_size_cache:
        return _hf_size_cache[key]
    url = "https://huggingface.co/{:s}/resolve/main/{:s}".format(repo_id, filename)
    req = urllib.request.Request(url, method="HEAD")
    size: int | None = None
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            try:
                size = int(resp.headers.get("Content-Length") or 0) or None
            except (TypeError, ValueError):
                size = None
    except Exception:  # pylint: disable=broad-exception-caught
        size = None
    _hf_size_cache[key] = size
    return size


def check_model_file_integrity(model_path: Path | str | None) -> str:
    """Return an actionable warning if a local model file looks truncated.

    Only checks curated presets (the repo ID + filename are known).  Compares
    the local file size against the size on HuggingFace; returns an empty
    string when the file is fine or the check can't run.
    """
    if not model_path:
        return ""
    if isinstance(model_path, str):
        model_path = Path(model_path)
    if not model_path.is_file():
        return ""
    name = model_path.name.lower()
    for preset in PRESET_MODELS:
        if preset.filename.lower() != name:
            continue
        try:
            local = model_path.stat().st_size
        except OSError:
            return ""
        expected = _hf_repo_file_size(preset.repo_id, preset.filename)
        if expected and local < expected * 0.995:
            return (
                "{:s} is likely truncated: it is {:.1f} GB on disk, but the file on "
                "HuggingFace is {:.1f} GB. Delete it and re-download / re-copy the model "
                "(a partial copy fails with \"missing tensor\")."
            ).format(model_path.name, local / (1024 ** 3), expected / (1024 ** 3))
        break
    return ""


_MODEL_LOAD_FAILURE_MARKERS = (
    "missing tensor",
    "error loading model",
    "failed to load model",
    "model file is too small",
)


def _log_looks_like_model_load_failure(tail: str) -> bool:
    """True when the llama-server log tail shows a model-load failure."""
    lowered = tail.lower()
    return any(marker in lowered for marker in _MODEL_LOAD_FAILURE_MARKERS)


_MMPROJ_MISMATCH_MARKERS = (
    "you may be using wrong mmproj",
    "mismatch between text model",
    "failed to load multimodal model",
    "failed to load vision model",
)


def _log_looks_like_mmproj_mismatch(tail: str) -> bool:
    """True when the llama-server log tail shows a wrong-projector failure."""
    lowered = tail.lower()
    return any(marker in lowered for marker in _MMPROJ_MISMATCH_MARKERS)


def _mmproj_mismatch_hint(model_path: Path | str | None) -> str:
    """Actionable hint when llama-server dies because of a wrong projector."""
    if isinstance(model_path, str):
        model_path = Path(model_path)
    model_name = model_path.name if model_path else ""
    local_name = ""
    for preset in PRESET_MODELS:
        if preset.mmproj_filename and model_name and model_name.lower() == preset.filename.lower():
            local_name = _local_mmproj_name(preset)
            break
    rename_hint = (
        "rename it to {:s}".format(local_name)
        if local_name else "use the addon's Download button"
    )
    return (
        "The vision projector (mmproj) does not match this model — the generic "
        "mmproj-F16.gguf / mmproj.gguf in the model folder belongs to a different "
        "model, and several presets share the same generic projector filename, so "
        "they overwrite each other. Fix: delete the stray projector and use the "
        "addon's Download button (it saves each model's projector under its own "
        "name), or {:s}. The model also runs fine without a projector (text-only, "
        "no image input).".format(rename_hint)
    )


# ---------------------------------------------------------------------------
# GPU out-of-memory detection
#
# When --n-gpu-layers 99 puts the weights AND the KV cache into VRAM, a GPU
# without enough free memory dies with a Vulkan/CUDA OOM. The failure often
# surfaces as an access-violation crash (exit code 0xC0000005) instead of a
# clean error message, so we match the log and decode the exit code.

_GPU_OOM_MARKERS = (
    "ggml_vulkan",
    "erroroutofdevicememory",
    "erroroutofhostmemory",
    "vk::device::allocatememory",
    "failed to allocate vulkan0 buffer",
    "failed to allocate buffer for kv cache",
    "cuda error: out of memory",
    "cudamalloc",
    "out of device memory",
    "failed to allocate gpu buffer",
)


def _log_looks_like_gpu_oom(tail: str) -> bool:
    """True when the llama-server log tail shows a GPU out-of-memory failure."""
    lowered = tail.lower()
    return any(marker in lowered for marker in _GPU_OOM_MARKERS)


def _gpu_oom_hint() -> str:
    """Actionable hint when llama-server dies because the GPU ran out of memory."""
    ctx = _config.local_ctx_size
    backend = _config.llama_backend or "auto"
    model_size = ""
    if _last_launched_model_path:
        try:
            size_gb = Path(_last_launched_model_path).stat().st_size / (1024 ** 3)
            model_size = " (model file is {:.1f} GB)".format(size_gb)
        except OSError:
            pass
    ctx_part = ""
    if ctx and ctx >= 65536:
        ctx_part = (
            " You are using a {:d}-token context window — at that size the KV cache "
            "alone is several GB of VRAM on a 27B-class model, so it usually does "
            "not fit alongside the weights.".format(ctx)
        )
    elif ctx and ctx > 32768:
        ctx_part = (
            " You are using a {:d}-token context window, which makes the KV cache "
            "very large.".format(ctx)
        )
    backend_part = ""
    if backend == "vulkan":
        backend_part = (
            " This log is from the Vulkan backend (ggml_vulkan). If you have an "
            "NVIDIA GPU, switch the backend to CUDA and use the addon's "
            "'Download llama-server' button so it fetches the CUDA build — Vulkan "
            "is often less memory-efficient, and on laptops it may pick the "
            "integrated GPU. Check the 'ggml_vulkan: Found N devices' line in "
            "llama-server.log for which device was selected."
        )
    return (
        "The GPU ran out of memory while loading the model{:s}. With --n-gpu-layers "
        "99 both the weights and the KV cache are placed in VRAM.{:s}{:s}\n"
        "Fixes to try:\n"
        "  1. Reduce the context size (e.g. 32768 instead of {:d}) — the KV cache "
        "is the biggest VRAM consumer.\n"
        "  2. Lower --n-gpu-layers so part of the model stays in system RAM "
        "(llama_backend / GPU layers in preferences).\n"
        "  3. Close other GPU-heavy apps, or run the model on CPU if the GPU is "
        "too small for this model.".format(
            model_size, ctx_part, backend_part, ctx or 65536,
        )
    )


# Windows NTSTATUS crash codes: llama-server segfaulting shows up as a large
# negative-looking exit code (e.g. 3221225477 = 0xC0000005 = access violation).
_WIN_CRASH_CODES: dict[int, str] = {
    0xC0000005: "ACCESS_VIOLATION — crashed, often a GPU driver / OOM issue",
    0xC000001D: "ILLEGAL_INSTRUCTION — the CPU lacks an instruction this build needs (try another llama-server build)",
    0xC0000374: "HEAP_CORRUPTION — crashed, possible driver bug",
    0xC0000409: "STACK_BUFFER_OVERRUN — crashed, possible driver bug",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000135: "DLL_NOT_FOUND — a required DLL is missing (use the bundled build)",
    0xC0000142: "DLL_INIT_FAILED",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO",
    0xC000000D: "INVALID_PARAMETER",
    0xC000013A: "CTRL_C_EXIT",
}


def _describe_exit_code(rc: int) -> str:
    """Return a readable description for a Windows crash exit code."""
    unsigned = rc & 0xFFFFFFFF
    name = _WIN_CRASH_CODES.get(unsigned, "")
    suffix = (" — " + name) if name else ""
    return " (0x{:08X}{:s})".format(unsigned, suffix)


# ---------------------------------------------------------------------------
# Data types

@dataclass
class LLMConfig:
    """Persisted configuration for the LLM backend."""

    mode: str = "local"  # "local" | "remote"
    # Local mode
    llama_source: str = "bundled"  # "bundled" (addon-managed) | "custom" (user-provided)
    llama_path: str = ""
    model_repo_id: str = "unsloth/gemma-4-26B-A4B-it-GGUF"
    model_filename: str = "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
    downloaded_models_dir: str = ""
    local_port: int = _LOCAL_LLM_DEFAULT_PORT
    local_ctx_size: int = 16384
    local_max_tokens: int = 16384  # Max output tokens per API call
    thinking_budget_tokens: int = 1024  # Max chain-of-thought reasoning tokens per API call
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
    expected_sha256: str = ""  # SHA-256 hash for download verification (empty = skip check)


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
        filename="Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q6_K.gguf",
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
        filename="Qwen3.8-27B-UD-Q4_K_M.gguf",
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
        filename="Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-AMD-MTP-IQ4_XS.gguf",
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
        max_tokens=12288,
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
        max_tokens=12288,
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
        max_tokens=12288,
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



def parse_model_url(url: str) -> tuple[str, str] | None:
    """Parse a HuggingFace URL or direct GGUF link.

    Returns (repo_id, filename) or None if unparseable.
    """
    url = url.strip()
    # HuggingFace resolve URL:
    # https://huggingface.co/org/repo/resolve/main/file.gguf
    hf_match = re.match(
        r"https?://huggingface\.co/([^/]+/[^/]+)/resolve/[^/]+/(.+)",
        url,
    )
    if hf_match:
        return hf_match.group(1), hf_match.group(2)
    # Direct .gguf link (any URL ending in .gguf)
    if url.lower().endswith(".gguf"):
        filename = url.rsplit("/", 1)[-1]
        return "", filename
    # Local file path
    if os.path.isfile(url) and url.lower().endswith(".gguf"):
        return "", os.path.basename(url)
    return None

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
# Keep the handle from os.add_dll_directory() alive for the whole
# session: if it is garbage-collected, the bundled DLL search
# directory is removed and llama-server can fail with DLL_NOT_FOUND
# mid-session on Windows.
_bundled_dll_handle = None
_last_launched_model_path: Path | None = None
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
        _config.llama_source = cfg.llama_source
        _config.llama_path = cfg.llama_path
        _config.model_repo_id = cfg.model_repo_id
        _config.model_filename = cfg.model_filename
        _config.downloaded_models_dir = cfg.downloaded_models_dir
        _config.local_port = cfg.local_port
        _config.local_ctx_size = cfg.local_ctx_size
        _config.local_max_tokens = cfg.local_max_tokens
        _config.thinking_budget_tokens = cfg.thinking_budget_tokens
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
            llama_source=_config.llama_source,
            llama_path=_config.llama_path,
            model_repo_id=_config.model_repo_id,
            model_filename=_config.model_filename,
            downloaded_models_dir=_config.downloaded_models_dir,
            local_port=_config.local_port,
            local_ctx_size=_config.local_ctx_size,
            local_max_tokens=_config.local_max_tokens,
            thinking_budget_tokens=_config.thinking_budget_tokens,
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
    """Search for ``llama-server`` binary.

    Prefers the Coworker-managed bundled binary (latest version) over
    PATH and known install locations.  Logs a warning when the found
    binary is older than _MIN_SUPPORTED_BUILD (too old for Qwen3 SSM).
    """
    global _find_llama_server_cache, _find_llama_server_checked
    if _find_llama_server_checked:
        return _find_llama_server_cache
    _find_llama_server_checked = True

    with _lock:
        active_backend = _config.llama_backend
        llama_source = _config.llama_source
        llama_path = _config.llama_path
    if active_backend == "auto":
        active_backend = _detect_gpu_backend()

    bundled_dir = _get_bundled_llama_dir()
    _log = "[🛠️Coworker] find_llama_server"

    # 0. Custom source — the user explicitly provided their own binary.
    #    Only that path is used; we never fall through to PATH/bundled so
    #    the addon never tampers with a user-managed setup.
    if llama_source == "custom":
        if llama_path and os.path.isfile(llama_path):
            print("{:s}: custom path -> {:s}".format(_log, llama_path))
            _find_llama_server_cache = llama_path
            _check_llama_version(llama_path)
            return llama_path
        print("{:s}: custom source set but path missing/invalid — {:s}".format(_log, llama_path or "(empty)"))
        _find_llama_server_cache = None
        return None

    # 1. Bundled directory FIRST — this is the Coworker-managed version
    #    (downloaded via the "Download llama-server" button, always recent).
    # Check backend-specific binary first (e.g. llama-server-cuda.exe),
    # then the generic fallback (llama-server.exe).  This ensures the
    # correct GPU backend binary is used and its companion DLLs (cudart,
    # etc.) are found in the same directory.
    backend_bname = "llama-server-{b}.exe".format(b=active_backend or "cpu")
    for bname in (backend_bname, "llama-server.exe"):
        bpath = bundled_dir / bname
        if bpath.is_file():
            print("{:s}: found bundled {:s} -> {:s}".format(_log, bname, str(bpath)))
            _find_llama_server_cache = str(bpath)
            _check_llama_version(str(bpath))
            return str(bpath)

    # 2. Search PATH (may include WinGet, pip, or system installs).
    for name in ("llama-server", "llama-server.exe"):
        exe = shutil.which(name)
        if exe:
            print("{:s}: found via PATH -> {:s}".format(_log, exe))
            _find_llama_server_cache = exe
            _check_llama_version(exe)
            return exe

    # 3. Known install directories (WinGet package path, etc.).
    print("{:s}: not on PATH, checking known install dirs...".format(_log))
    for path in _LLAMA_SEARCH_PATHS_WIN:
        if os.path.isfile(path):
            print("{:s}: found at {:s}".format(_log, path))
            _find_llama_server_cache = path
            _check_llama_version(path)
            return path

    print("{:s}: NOT FOUND".format(_log))
    _find_llama_server_cache = None
    return None


def _check_llama_version(exe_path: str) -> None:
    """Log a warning if the llama-server binary is too old for Qwen3 models."""
    ver = _llama_server_version(exe_path)
    build = _parse_llama_build_number(ver)
    if build and build < _MIN_SUPPORTED_BUILD:
        print(
            "[⚠️Coworker] WARNING: llama-server build {:d} is outdated "
            "(minimum {:d} for Qwen3 SSM models). "
            "Use 'Download llama-server' in preferences to get the latest version."
            .format(build, _MIN_SUPPORTED_BUILD)
        )


    _find_llama_server_cache = None
    return None


def invalidate_llama_server_cache() -> None:
    """Reset the ``find_llama_server`` cache so the next call re-searches.

    Call this after the user installs llama-server externally or changes
    the configured path, so the addon detects it without a Blender restart.
    """
    global _find_llama_server_cache, _find_llama_server_checked, _gpu_backend_cache
    print("[🛠️Coworker] invalidate_llama_server_cache: cache cleared")
    _find_llama_server_checked = False
    _find_llama_server_cache = None
    _gpu_backend_cache = None


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


def _clear_download_progress() -> None:
    """Clear download progress bar fields but preserve the error message.

    Used after a download finishes so the progress bar disappears while
    any error message remains visible to the user.
    """
    with _lock:
        _state.download_progress = ""
        _state.download_progress_eta = ""
        _state.download_progress_pct = 0.0
        _state.download_active = False
        _state.download_kind = ""


def _set_download_kind(kind: str) -> None:
    """Set the download kind ("model" | "llama_server" | "")."""
    with _lock:
        _state.download_kind = kind


# Cache for GPU backend detection — nvidia-smi/wmic are expensive and
# called from multiple places (find_llama_server, start_local_llama,
# download_llama_server).  Cache the result so we only spawn once.
_gpu_backend_cache: str | None = None


def _detect_gpu_backend() -> str:
    """Detect the best GPU backend for llama-server on this machine.

    Returns one of ``"cuda"``, ``"vulkan"``, or ``"cpu"``.
    Result is cached after the first call.
    """
    global _gpu_backend_cache
    if _gpu_backend_cache is not None:
        return _gpu_backend_cache

    if sys.platform != "win32":
        # Non-Windows: default to cpu (or vulkan on Linux if available).
        # We don't auto-detect on macOS/Linux — user can override manually.
        _gpu_backend_cache = "cpu"
        return _gpu_backend_cache

    # Windows detection.
    # 1. Check for NVIDIA GPU via nvidia-smi.
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            print("[🛠️Coworker] _detect_gpu_backend: NVIDIA GPU detected -> cuda")
            _gpu_backend_cache = "cuda"
            return _gpu_backend_cache
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    # 2. Check for AMD / Intel GPU via PowerShell (wmic is deprecated on Win10+).
    #    Use Get-CimInstance which works on Windows 10 1903+ and all Win11.
    gpu_found = False
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout.lower()
        if "amd" in output or "radeon" in output:
            gpu_found = True
        elif "intel" in output and any(k in output for k in ("arc", "a380", "a750", "a770")):
            gpu_found = True  # Intel Arc discrete GPUs.
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    # Fallback: try wmic if PowerShell failed (older Windows).
    if not gpu_found:
        try:
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                capture_output=True, text=True, timeout=5,
            )
            output = result.stdout.lower()
            if "amd" in output or "radeon" in output:
                gpu_found = True
            elif "intel" in output and any(k in output for k in ("arc", "a380", "a750", "a770")):
                gpu_found = True
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass

    if gpu_found:
        print("[🛠️Coworker] _detect_gpu_backend: AMD/Intel GPU detected -> vulkan")
        _gpu_backend_cache = "vulkan"
        return _gpu_backend_cache

    # 3. Fallback to CPU.
    print("[🛠️Coworker] _detect_gpu_backend: no compatible GPU detected -> cpu")
    _gpu_backend_cache = "cpu"
    return _gpu_backend_cache


def resolve_gpu_backend(backend: str) -> str:
    """Resolve a "auto" backend selector to a concrete backend name.

    Returns ``cuda``, ``vulkan``, or ``cpu`` — the backend llama-server will
    actually use (relevant for memory planning).
    """
    if backend != "auto":
        return backend
    return _detect_gpu_backend()


# ---------------------------------------------------------------------------
# Hardware-aware context-size recommendation
#
# A 64K+ context window on a 27B-class model puts many GB of KV cache into
# VRAM/RAM and is the #1 cause of GPU out-of-memory crashes at startup.  We
# detect the machine's memory and recommend a context size that fits, so new
# users get something that "just works" instead of a slider to misconfigure.

# Standard context sizes exposed as one-click preset buttons.
ctx_preset_sizes: tuple[int, ...] = (4096, 8192, 16384, 32768, 65536, 131072)




def ctx_preset_label(tokens: int) -> str:
    """Return a short label like ``32K`` for a token count."""
    return "{:d}K".format(max(1, tokens // 1024))


def detect_system_ram_gb() -> float | None:
    """Return total physical RAM in GB, or ``None`` if undetectable."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):  # type: ignore[misc]
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return stat.ullTotalPhys / (1024 ** 3)
        elif sys.platform.startswith("linux"):
            with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip().isdigit():
                return int(result.stdout.strip()) / (1024 ** 3)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return None


def detect_vram_gb() -> float | None:
    """Return the VRAM (GB) of the first NVIDIA GPU, or ``None``."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            first = result.stdout.strip().splitlines()[0].strip()
            return float(first) / 1024.0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


_hardware_detect_cache: dict[str, object] = {}
_HARDWARE_CACHE_TTL = 30.0


def _detect_hardware_cached() -> tuple[float | None, float | None]:
    """Return (ram_gb, vram_gb), cached briefly so UI redraws don't spawn nvidia-smi."""
    import time as _time
    now = _time.monotonic()
    if _hardware_detect_cache and now - float(_hardware_detect_cache.get("t", 0.0)) < _HARDWARE_CACHE_TTL:
        return float(_hardware_detect_cache.get("ram") or 0.0) or None, \
            float(_hardware_detect_cache.get("vram") or 0.0) or None
    ram = detect_system_ram_gb()
    vram = detect_vram_gb()
    _hardware_detect_cache.clear()
    _hardware_detect_cache["t"] = now
    _hardware_detect_cache["ram"] = ram or 0.0
    _hardware_detect_cache["vram"] = vram or 0.0
    return ram, vram




# ---- GPU auto-detection for --n-gpu-layers ----

_RUNTIME_OVERHEAD_MB = 700
_KV_MB_PER_1K_CTX = 70
_TYPICAL_LAYERS = 33
_FULL_OFFLOAD = 99

def _hardware_info() -> tuple[int | None, str | None, int | None]:
    """Return (ram_mb, gpu_label, gpu_mb). Cached for the session."""
    ram_gb = detect_system_ram_gb()
    ram_mb = int(ram_gb * 1024) if ram_gb else None
    gpu_label = None
    gpu_mb = None

    if shutil.which("nvidia-smi"):
        try:
            name = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                text=True, timeout=5,
            ).strip().splitlines()[0]
            total = int(subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.total",
                 "--format=csv,noheader,nounits"],
                text=True, timeout=5,
            ).strip().splitlines()[0])
            gpu_label = name
            gpu_mb = total
            print("[Coworker] _hardware_info: GPU = {:s} ({:d} MB)".format(name, total))
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True, timeout=5,
            )
            total_mb = int(out.strip()) // (1024 * 1024)
            gpu_label = "Apple unified memory"
            gpu_mb = int(total_mb * 0.6)  # ~60% usable for model
            print("[Coworker] _hardware_info: Apple unified = {:d} MB".format(gpu_mb))
        except Exception:
            pass

    return ram_mb, gpu_label, gpu_mb

def _gguf_layer_count(model_path: Path) -> int | None:
    """Read the number of transformer blocks from a GGUF file header.

    Returns the layer count if found, or ``None`` if the file can't be
    read or doesn't contain the expected metadata key.
    The GGUF format stores ``<arch>.block_count`` in its metadata.
    """
    # GGUF type IDs (from llama.cpp gguf.h).
    _GGUF_UINT8 = 0
    _GGUF_INT8 = 1
    _GGUF_UINT16 = 2
    _GGUF_INT16 = 3
    _GGUF_UINT32 = 4
    _GGUF_UINT64 = 5
    _GGUF_FLOAT32 = 6
    _GGUF_FLOAT64 = 7
    _GGUF_BOOL = 8
    _GGUF_STRING = 9
    _GGUF_ARRAY = 10
    # Fixed-size byte widths for scalar types.
    _FIXED_SIZES = {
        _GGUF_UINT8: 1, _GGUF_INT8: 1,
        _GGUF_UINT16: 2, _GGUF_INT16: 2,
        _GGUF_UINT32: 4, _GGUF_FLOAT32: 4,
        _GGUF_UINT64: 8, _GGUF_FLOAT64: 8,
        _GGUF_BOOL: 1,
    }

    def _skip_value_bytes(fobj: "io.BufferedRandom | io.BufferedReader", vtype: int) -> None:
        """Advance *fobj* past the value bytes for type *vtype*.

        The type ID has already been read; this only skips the payload.
        """
        if vtype in _FIXED_SIZES:
            fobj.read(_FIXED_SIZES[vtype])
        elif vtype == _GGUF_STRING:
            slen = int.from_bytes(fobj.read(8), "little")
            fobj.read(slen)
        elif vtype == _GGUF_ARRAY:
            atype = int.from_bytes(fobj.read(4), "little")
            alen = int.from_bytes(fobj.read(8), "little")
            for _ in range(alen):
                if atype in _FIXED_SIZES:
                    fobj.read(_FIXED_SIZES[atype])
                elif atype == _GGUF_STRING:
                    elen = int.from_bytes(fobj.read(8), "little")
                    fobj.read(elen)
                else:
                    break  # Unknown element type — bail.

    try:
        with open(str(model_path), "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return None
            version = int.from_bytes(f.read(4), "little")
            if version < 2:
                return None  # V1 format — no standard metadata layout.
            n_tensors = int.from_bytes(f.read(4), "little")
            n_kv = int.from_bytes(f.read(4), "little")
            # Read key-value metadata entries looking for block_count.
            for _ in range(n_kv):
                # Key: uint64 length-prefixed string.
                key_len = int.from_bytes(f.read(8), "little")
                key = f.read(key_len).decode("utf-8", errors="replace")
                # Value type: uint32.
                val_type = int.from_bytes(f.read(4), "little")

                if val_type == _GGUF_UINT32 and "block_count" in key:
                    lc = int.from_bytes(f.read(4), "little")
                    if lc > 0:
                        print("[Coworker] _gguf_layer_count: {:s} has {:d} layers".format(
                            model_path.name, lc))
                        return lc
                    # Zero layer count — skip.
                    continue
                if val_type == _GGUF_UINT64 and "block_count" in key:
                    lc = int.from_bytes(f.read(8), "little")
                    if lc > 0:
                        print("[Coworker] _gguf_layer_count: {:s} has {:d} layers".format(
                            model_path.name, lc))
                        return lc
                    continue
                # Not block_count (or wrong type) — skip the value.
                _skip_value_bytes(f, val_type)
            return None
    except (OSError, struct.error, ValueError):
        return None


def autodetect_gpu_layers(model_path: Path, context_size: int) -> int:
    """Calculate optimal --n-gpu-layers for the given model and hardware."""
    backend = _detect_gpu_backend()
    if backend == "cpu":
        return 0

    _, _, gpu_mb = _hardware_info()
    if gpu_mb is None:
        return _FULL_OFFLOAD  # Cannot detect -- try full offload

    try:
        model_mb = model_path.stat().st_size // (1024 * 1024)
    except OSError:
        return _FULL_OFFLOAD

    kv_mb = int((context_size / 1024) * _KV_MB_PER_1K_CTX)
    usable_mb = gpu_mb - _RUNTIME_OVERHEAD_MB - kv_mb

    if usable_mb <= 0:
        return 0  # Not enough VRAM for GPU offload
    if usable_mb >= model_mb * 1.05:
        return _FULL_OFFLOAD  # Full GPU offload

    # Try to read the actual layer count from the GGUF header so the
    # per-layer estimate is accurate even for models with != 33 layers.
    actual_layers = _gguf_layer_count(model_path) or _TYPICAL_LAYERS
    per_layer = model_mb / actual_layers
    ngl = max(0, min(actual_layers, int(usable_mb / per_layer)))
    print("[Coworker] autodetect_gpu_layers: ngl={:d}/{:d} (gpu={:d}MB, model={:d}MB, kv={:d}MB, usable={:d}MB)".format(
        ngl, actual_layers, gpu_mb, model_mb, kv_mb, usable_mb))
    return ngl
def recommend_context_size(
    model_gb: float = 0.0,
    backend: str = "auto",
    ram_gb: float | None = None,
    vram_gb: float | None = None,
) -> int:
    """Recommend a context size (tokens) that fits the detected hardware.

    Heuristic: assume ~256 KB of KV-cache memory per token of context (an
    upper bound for 27B-class GQA models — smaller models need far less),
    i.e. ~4096 tokens per GB of budget.  The KV cache must fit in VRAM when
    a GPU backend is used, otherwise in system RAM.  Returns one of
    :data:`ctx_preset_sizes`.
    """
    if ram_gb is None or vram_gb is None:
        _ram, _vram = _detect_hardware_cached()
        if ram_gb is None:
            ram_gb = _ram
        if vram_gb is None:
            vram_gb = _vram
    resolved = resolve_gpu_backend(backend)
    if resolved in ("cuda", "vulkan") and vram_gb:
        # Weights + KV cache both live in VRAM; leave 1.5 GB headroom.
        budget_gb = max(vram_gb - model_gb - 1.5, 1.5)
    elif ram_gb:
        # Weights in RAM; leave 2 GB for the OS + Blender.
        budget_gb = max(ram_gb - model_gb - 2.0, 2.0)
    else:
        return 32768  # Hardware unknown — safe mid-range default.
    tokens = int(budget_gb * 4096)
    tokens = max(4096, min(tokens, 131072))
    # Snap down to the nearest standard preset size.
    chosen = ctx_preset_sizes[0]
    for size in ctx_preset_sizes:
        if size <= tokens:
            chosen = size
        else:
            break
    return chosen


def hardware_context_hint(model_gb: float = 0.0, backend: str = "auto") -> str:
    """One-line hint for the preferences UI: recommended size for this machine."""
    ram, vram = _detect_hardware_cached()
    recommended = recommend_context_size(model_gb, backend, ram, vram)
    parts: list[str] = []
    if ram:
        parts.append("{:.0f} GB RAM".format(ram))
    if vram:
        parts.append("{:.0f} GB VRAM".format(vram))
    hw = " · ".join(parts) if parts else "unknown hardware"
    return (
        "Recommended for your hardware ({:s}): {:s} \u2014 larger sizes need much "
        "more memory and can crash startup.".format(hw, ctx_preset_label(recommended))
    )


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


def _verify_sha256(path: Path, expected: str) -> None:
    """Compute SHA-256 of *path*, raise RuntimeError on mismatch."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    got = h.hexdigest().lower()
    want = expected.lower().strip()
    if got != want:
        raise RuntimeError(
            "Checksum mismatch for {:s}: expected {:s}..., got {:s}..."
            " - delete the file and re-download.".format(
                path.name, want[:12], got[:12])
        )

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
    expected_sha256: str = "",
) -> bool:
    """Download a GGUF model from HuggingFace via HTTP.

    Streams in 64 KB chunks with progress reporting. Supports
    resume via .part files and optional SHA-256 verification
    before atomic rename.

    Returns True on success, False on failure.
    """
    url = "https://huggingface.co/{:s}/resolve/main/{:s}".format(repo_id, filename)
    print("[Coworker] _download_gguf_direct: url = {:s}".format(url))
    print("[Coworker] _download_gguf_direct: dest = {:s}".format(str(dest)))

    part = dest.with_suffix(dest.suffix + ".part")

    total_bytes = _get_hf_file_size(repo_id, filename)
    if total_bytes is not None:
        size_hint = _format_bytes(total_bytes)
        print("[Coworker] total size = {:s}".format(size_hint))
    else:
        size_hint = "unknown size"

    if not _check_disk_space(dest, total_bytes):
        if progress_callback:
            progress_callback(get_state().error or "Not enough disk space")
        return False

    already = 0
    if part.exists():
        already = part.stat().st_size
        if total_bytes and already >= total_bytes:
            print("[Coworker] .part already complete")
            already = 0
        elif already > 0:
            print("[Coworker] resuming from .part ({:s} already)".format(
                _format_bytes(already)))

    _set_download_progress("Downloading {:s} ({:s}) ...".format(filename, size_hint))
    if progress_callback:
        progress_callback("Downloading {:s} ({:s}) ...".format(filename, size_hint))

    try:
        req = urllib.request.Request(url, method="GET")
        hf_token = ""
        with _lock:
            hf_token = _config.hf_token
        if not hf_token:
            hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or ""
        if hf_token:
            req.add_header("Authorization", "Bearer {:s}".format(hf_token))

        if already > 0:
            req.add_header("Range", "bytes={:d}-".format(already))

        with urllib.request.urlopen(req, timeout=120) as resp:
            try:
                raw_sock = getattr(getattr(resp, "fp", None), "raw", None)
                sock = getattr(raw_sock, "_sock", None) if raw_sock is not None else None
                if sock is not None:
                    sock.settimeout(60.0)
            except (AttributeError, OSError):
                pass

            content_length = int(resp.headers.get("Content-Length", "0")) or 0
            is_resume = (resp.status == 206)
            if is_resume:
                actual_total = already + content_length
            else:
                actual_total = content_length or total_bytes or 0
                already = 0

            downloaded = already
            chunk_size = 64 * 1024
            start_time = _get_time()
            last_update = start_time
            dest.parent.mkdir(parents=True, exist_ok=True)
            mode = "ab" if is_resume and already > 0 else "wb"
            with open(str(part), mode) as f_out:
                while True:
                    if _download_cancel_event.is_set():
                        print("[Coworker] download cancelled")
                        f_out.close()
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
                    if now - last_update < 0.2 and actual_total > 0:
                        continue
                    last_update = now
                    if actual_total > 0:
                        pct = downloaded / actual_total * 100.0
                        elapsed = now - start_time
                        if elapsed > 0:
                            speed_bps = (downloaded - already) / elapsed
                            remaining = actual_total - downloaded
                            eta = remaining / speed_bps if speed_bps > 0 else 0
                            speed_str = "{:s}/s".format(_format_bytes(speed_bps))
                            eta_str = _format_eta(eta)
                            _set_download_progress_eta(
                                "{:s} -- {:s}".format(_format_bytes(actual_total), eta_str),
                                pct,
                            )
                            msg = "Downloading {:s} ... {:.0f}% ({:s} / {:s}) -- {:s}".format(
                                filename, pct, _format_bytes(downloaded),
                                _format_bytes(actual_total), speed_str,
                            )
                        else:
                            _set_download_progress_eta(
                                "{:s}".format(_format_bytes(actual_total)), pct,
                            )
                            msg = "Downloading {:s} ... {:.0f}% ({:s} / {:s})".format(
                                filename, pct, _format_bytes(downloaded),
                                _format_bytes(actual_total),
                            )
                    else:
                        msg = "Downloading {:s} ... {:s}".format(filename, _format_bytes(downloaded))
                    _set_download_progress(msg)
                    if progress_callback:
                        progress_callback(msg)

        if part.stat().st_size == 0:
            part.unlink()
            _set_error("Downloaded file is empty")
            return False

        if expected_sha256:
            _set_download_progress("Verifying checksum ...")
            if progress_callback:
                progress_callback("Verifying SHA-256 checksum ...")
            try:
                _verify_sha256(part, expected_sha256)
            except RuntimeError as ex:
                print("[Coworker] SHA-256 FAILED")
                _set_error(str(ex))
                if progress_callback:
                    progress_callback(str(ex))
                return False

        os.replace(str(part), str(dest))
        print("[Coworker] download complete")
        _set_download_progress("Download complete: {:s}".format(filename))
        if progress_callback:
            progress_callback("Download complete: {:s}".format(filename))
        return True

    except urllib.error.HTTPError as ex:
        if ex.code == 416 and part.exists():
            print("[Coworker] HTTP 416 - .part may be complete")
            if expected_sha256:
                try:
                    _verify_sha256(part, expected_sha256)
                except RuntimeError as ex2:
                    part.unlink()
                    _set_error(str(ex2))
                    if progress_callback:
                        progress_callback(str(ex2))
                    return False
            os.replace(str(part), str(dest))
            _set_download_progress("Download complete: {:s}".format(filename))
            if progress_callback:
                progress_callback("Download complete: {:s}".format(filename))
            return True
        if part.exists():
            part.unlink()
        if ex.code == 401:
            msg = "HuggingFace 401 (Unauthorized) for {:s}. Set HF_TOKEN or use a different model.".format(repo_id)
        elif ex.code == 403:
            msg = "HuggingFace 403 (Forbidden) for {:s}. Model may be gated.".format(repo_id)
        elif ex.code == 404:
            msg = "HuggingFace 404 (Not Found) for {:s}/{:s}.".format(repo_id, filename)
        else:
            msg = "Failed to download model (HTTP {:d}: {:s})".format(ex.code, ex.reason)
        print("[Coworker] _download_gguf_direct: {:s}".format(msg))
        _set_error(msg)
        if progress_callback:
            progress_callback(msg)
        return False

    except (urllib.error.URLError, OSError) as ex:
        msg = "Network error while downloading: {:s}".format(str(ex))
        print("[Coworker] _download_gguf_direct: {:s}".format(msg))
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
                # Also download the mmproj file if the preset has one.
                _download_mmproj_if_needed(r, f, models_dir, progress_callback)
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
                    tail = get_llama_server_log_tail()
                    error = "llama-server process exited unexpectedly during download"
                    if tail:
                        error += "\n\n--- llama-server.log (tail) ---\n{:s}".format(tail)
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
        _clear_download_progress()
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

        # Companion DLL extensions — extract these alongside the binary
        # so CUDA/Vulkan runtime libraries are co-located.
        # On Linux, versioned shared libs like ``libcublas.so.12`` don't end
        # in ``.so`` so we also match ``.so.`` followed by digits.
        def _is_companion_file(name: str) -> bool:
            low = name.lower()
            if low.endswith(".dll") or low.endswith(".dylib"):
                return True
            if low.endswith(".so"):
                return True
            # Versioned .so: libcudart.so.12, libcublas.so.12.4, etc.
            if ".so." in low and low.split(".so.")[-1].isdigit():
                return True
            return False

        data.seek(0)
        if archive_ext == ".zip":
            with zipfile.ZipFile(data) as zf:
                # Find the server binary inside the archive.
                binary_members = [
                    m for m in zf.namelist()
                    if m.endswith(binary_name) or m.endswith("/" + binary_name)
                ]
                if not binary_members:
                    _set_error(
                        "Could not find {:s} in the downloaded archive".format(binary_name)
                    )
                    return None
                # Extract the binary + ALL companion DLLs/SOs.
                # The CUDA release zip bundles cudart, cublas, cublasLt etc.
                # alongside the exe — we need them all.
                members_to_extract = list(binary_members)
                for m in zf.namelist():
                    if m in members_to_extract:
                        continue
                    if _is_companion_file(m):
                        members_to_extract.append(m)
                print("[🛠️Coworker] download_llama_server: extracting {:d} files from archive".format(
                    len(members_to_extract)))
                for m in members_to_extract:
                    zf.extract(m, str(dest_dir))
                # The extracted binary may be in a subdirectory — move to dest.
                extracted_bin = dest_dir / binary_members[0]
                if not extracted_bin.is_file():
                    # Zip entry had a directory prefix (e.g. bin/llama-server.exe).
                    extracted_bin = dest_dir / os.path.basename(binary_members[0])
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
                # Extract all companions for tar archives too.
                members_to_extract = list(binary_members)
                for m in tf.getmembers():
                    if m in members_to_extract:
                        continue
                    if _is_companion_file(m.name):
                        members_to_extract.append(m)
                print("[🛠️Coworker] download_llama_server: extracting {:d} files from archive".format(
                    len(members_to_extract)))
                tf.extractall(str(dest_dir), members=members_to_extract)
                extracted_bin = dest_dir / os.path.basename(binary_members[0].name)

        # Move binary to final location with backend suffix.
        if dest_binary.exists():
            dest_binary.unlink()
        shutil.move(str(extracted_bin), str(dest_binary))

        # For CUDA builds, also download the separate cudart zip as a safety
        # net — the main zip should already have the DLLs (extracted above),
        # but the cudart zip provides the canonical set.
        if cudart_url:
            _set_download_progress("Downloading CUDA runtime DLLs (backup) ...")
            if progress_callback:
                progress_callback("Downloading CUDA runtime DLLs ...")
            try:
                cudart_req = urllib.request.Request(cudart_url, method="GET")
                with urllib.request.urlopen(cudart_req, timeout=120) as cudart_resp:
                    cudart_data = io.BytesIO(cudart_resp.read())
                with zipfile.ZipFile(cudart_data) as cudart_zf:
                    cudart_zf.extractall(str(dest_dir))
                print("[🛠️Coworker] download_llama_server: cudart DLLs (backup) extracted to {:s}".format(str(dest_dir)))
            except (urllib.error.URLError, OSError, zipfile.BadZipFile) as ex:
                print("[🛠️Coworker] download_llama_server: cudart backup download failed — {:s}".format(str(ex)))
                # Non-fatal — the main zip should have already provided them.

        # Post-extraction: verify critical DLLs are present for CUDA backend.
        if backend == "cuda" and sys.platform == "win32":
            expected_dlls = ["cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"]
            missing = [d for d in expected_dlls if not (dest_dir / d).is_file()]
            if missing:
                print(
                    "[⚠️Coworker] download_llama_server: WARNING — missing DLLs after extraction: {:s}"
                    .format(", ".join(missing))
                )
            else:
                print("[🛠️Coworker] download_llama_server: all CUDA DLLs verified in {:s}".format(str(dest_dir)))

        # Make executable on non-Windows.
        if sys.platform != "win32":
            dest_binary.chmod(dest_binary.stat().st_mode | 0o111)

        msg = "llama-server installed at {:s}".format(str(dest_binary))
        print("[🛠️Coworker] download_llama_server: {:s}".format(msg))
        _set_download_progress(msg)
        if progress_callback:
            progress_callback(msg)
        # Invalidate the cache so find_llama_server picks up the new binary.
        global _find_llama_server_checked, _find_llama_server_cache, _llama_server_version_cache
        _find_llama_server_checked = False
        _find_llama_server_cache = None
        _llama_server_version_cache = ""
        # Clear download progress so the bar disappears from preferences.
        _clear_download_progress()
        return str(dest_binary)

    except urllib.error.HTTPError as ex:
        err = "Failed to download llama-server (HTTP {:d}: {:s})".format(
            ex.code, ex.reason
        )
        print("[🛠️Coworker] download_llama_server: {:s}".format(err))
        _set_error(err)
        if progress_callback:
            progress_callback(err)
        _clear_download_progress()
        return None
    except (urllib.error.URLError, OSError, zipfile.BadZipFile) as ex:
        err = "Failed to download/extract llama-server: {:s}".format(str(ex))
        print("[🛠️Coworker] download_llama_server: {:s}".format(err))
        _set_error(err)
        if progress_callback:
            progress_callback(err)
        _clear_download_progress()
        return None



def remove_llama_server() -> bool:
    """Remove bundled llama-server binaries and invalidate the search cache.

    Deletes the bundled directory contents (llama-server binaries,
    CUDA runtime DLLs, etc.) and always invalidates the search cache
    so the next find_llama_server() re-searches from scratch.

    If the binary was found via PATH (e.g. WinGet), the cache is
    still invalidated — the bundled copy is preferred on next search.

    Returns True if any action was taken (files removed or cache cleared).
    """
    bundled_dir = _get_bundled_llama_dir()
    _log = "[⚠️Coworker] remove_llama_server"

    removed = False
    if bundled_dir.is_dir():
        for item in list(bundled_dir.iterdir()):
            # Skip __pycache__ and hidden dirs.
            if item.name.startswith(".") or item.name == "__pycache__":
                continue
            try:
                if item.is_file():
                    item.unlink()
                    print("{:s}: removed {:s}".format(_log, item.name))
                    removed = True
                elif item.is_dir():
                    import shutil as _shutil
                    _shutil.rmtree(str(item))
                    print("{:s}: removed dir {:s}".format(_log, item.name))
                    removed = True
            except OSError as ex:
                print("{:s}: failed to remove {:s} — {:s}".format(_log, item.name, str(ex)))
    else:
        print("{:s}: bundled dir does not exist — {:s}".format(_log, str(bundled_dir)))

    # Always invalidate cache so find_llama_server re-searches.
    global _find_llama_server_checked, _find_llama_server_cache, _llama_server_version_cache
    _find_llama_server_checked = False
    _find_llama_server_cache = None
    _llama_server_version_cache = ""

    if removed:
        print("{:s}: bundled llama-server removed".format(_log))
    else:
        print("{:s}: cache invalidated (PATH binary may remain on system)".format(_log))
    return True


# ---------------------------------------------------------------------------
# New-console launch (Windows only)
#
# subprocess.Popen(CREATE_NEW_CONSOLE) cannot set the title of the new
# console window — subprocess.STARTUPINFO exposes no lpTitle — and calling
# SetConsoleTitleW from the parent renames the *parent's* console instead
# (which is why the Blender/Bforartists terminal was getting retitled).
# CreateProcessW with STARTUPINFOW.lpTitle sets the title on the new console
# at creation time, with no wrapper process: the returned handle still
# refers to llama-server itself, so termination behaves exactly as before.


class _ConsoleProcess:
    """Minimal Popen-like handle for a process launched via CreateProcessW.

    Exposes the subset of ``subprocess.Popen`` used by the rest of this
    module: ``pid``, ``poll``, ``returncode``, ``wait``, ``terminate`` and
    ``kill``.
    """

    _STILL_ACTIVE = 259  # STILL_ACTIVE (winnt.h)

    def __init__(self, handle: int, pid: int) -> None:
        self._handle = handle
        self.pid = pid
        self._returncode: int | None = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        if self._returncode is None:
            code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(
                self._handle, ctypes.byref(code)
            ) and code.value != _ConsoleProcess._STILL_ACTIVE:
                self._returncode = int(code.value)
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        if self._returncode is not None:
            return self._returncode
        kernel32 = ctypes.windll.kernel32
        if timeout is None:
            kernel32.WaitForSingleObject(self._handle, 0xFFFFFFFF)
        elif kernel32.WaitForSingleObject(
            self._handle, int(timeout * 1000)
        ) == 0x00000102:  # WAIT_TIMEOUT
            raise subprocess.TimeoutExpired(self.pid, timeout)
        self.poll()
        return self._returncode if self._returncode is not None else 0

    def terminate(self) -> None:
        ctypes.windll.kernel32.TerminateProcess(self._handle, 1)

    kill = terminate

    def __del__(self) -> None:
        try:
            if self._handle:
                ctypes.windll.kernel32.CloseHandle(self._handle)
                self._handle = None
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def _popen_new_console(
    args: list[str],
    env: dict[str, str],
    title: str,
) -> _ConsoleProcess:
    """Launch *args* in a brand-new console window titled *title*.

    Equivalent to ``subprocess.Popen(args, creationflags=CREATE_NEW_CONSOLE)``
    but passes ``STARTUPINFOW.lpTitle`` so the new window is titled instead
    of inheriting the executable name. Standard output/error are left
    connected to the new console (nothing is redirected, so the parent
    console keeps all of its own output). Windows only.
    """
    import ctypes
    from ctypes import wintypes

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,  # lpApplicationName
        wintypes.LPWSTR,  # lpCommandLine
        wintypes.LPVOID,  # lpProcessAttributes
        wintypes.LPVOID,  # lpThreadAttributes
        wintypes.BOOL,  # bInheritHandles
        wintypes.DWORD,  # dwCreationFlags
        wintypes.LPVOID,  # lpEnvironment
        wintypes.LPCWSTR,  # lpCurrentDirectory
        ctypes.POINTER(STARTUPINFOW),  # lpStartupInfo
        ctypes.POINTER(PROCESS_INFORMATION),  # lpProcessInformation
    ]

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    si.lpTitle = title

    # Environment block: NUL-separated UTF-16 "KEY=VALUE" pairs with a
    # trailing double NUL; CREATE_UNICODE_ENVIRONMENT must be set.
    env_block = "".join("{}={}\0".format(k, v) for k, v in env.items()) + "\0"
    env_buf = ctypes.create_unicode_buffer(env_block)

    pi = PROCESS_INFORMATION()
    ok = kernel32.CreateProcessW(
        None,  # lpApplicationName — resolved from the command line
        ctypes.create_unicode_buffer(subprocess.list2cmdline(args)),  # lpCommandLine
        None,  # lpProcessAttributes
        None,  # lpThreadAttributes
        False,  # bInheritHandles
        0x00000010 | 0x00000400,  # CREATE_NEW_CONSOLE | CREATE_UNICODE_ENVIRONMENT
        env_buf,  # lpEnvironment
        None,  # lpCurrentDirectory — inherit
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())

    kernel32.CloseHandle(pi.hThread)
    return _ConsoleProcess(int(pi.hProcess), int(pi.dwProcessId))


# ---------------------------------------------------------------------------
# Local LLM lifecycle


def get_llama_process() -> "subprocess.Popen | None":
    """Return the Popen handle of the currently running llama-server (or None)."""
    return _llama_process


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
    global _last_launched_model_path

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
    print("[🛠️Coworker] start_local_llama: llama-server version = {:s}".format(_llama_server_version(server_exe)))

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
        _last_launched_model_path = Path(model_path)
        integrity_warning = check_model_file_integrity(model_path)
        if integrity_warning:
            print("[🛠️Coworker] start_local_llama: WARNING — {:s}".format(integrity_warning))
    else:
        # No local .gguf — try the HuggingFace cache first.
        print("[🛠️Coworker] start_local_llama: local model NOT found, checking HF cache...")
        _last_launched_model_path = None
        with _lock:
            repo = _config.model_repo_id
            fname = _config.model_filename
        hf_cached = _find_model_in_hf_cache(repo, fname)
        if hf_cached:
            model_path = Path(hf_cached)
            _last_launched_model_path = Path(hf_cached)
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
    # Auto-select a free port if the configured one is busy.
    try:
        port = _find_free_port(port)
    except RuntimeError as ex:
        _set_error(str(ex))
        return None

    with _lock:
        ctx_size = _config.local_ctx_size or 16384
    # Auto-upgrade from the old 8192 default to 32768 for existing users.
    # 8192 is too small for system prompt + tools + conversation.
    if ctx_size <= 8192:
        ctx_size = 32768
        print("[🛠️Coworker] start_local_llama: auto-upgraded ctx_size from 8192 to 32768")
    print("[🛠️Coworker] start_local_llama: using ctx_size {:d}".format(ctx_size))

    # Large context windows on big models need a LOT of KV-cache memory.
    # Warn loudly so an OOM-ish startup failure is self-explanatory.
    try:
        model_bytes = (
            os.path.getsize(str(model_path))
            if model_path and os.path.isfile(str(model_path)) else 0
        )
    except OSError:
        model_bytes = 0
    if ctx_size > 32768 and model_bytes >= 12 * 1024 * 1024 * 1024:
        print(
            "[🛠️Coworker] start_local_llama: WARNING — {:d} context on a {:.1f} GB model "
            "needs a very large KV cache; if llama-server fails to start, lower "
            "Context Size in preferences (16K-32K is plenty for agent work)".format(
                ctx_size, model_bytes / (1024 ** 3)))

    print("[🛠️Coworker] start_local_llama: platform = {:s}".format(sys.platform))

    try:
        # Build args and environment (shared across platforms).
        # Determine GPU offload layers based on backend.
        with _lock:
            backend = _config.llama_backend
        if backend == "auto":
            backend = _detect_gpu_backend()

        # Pre-flight: verify the Vulkan runtime is installed when selected.
        if backend == "vulkan" and sys.platform == "win32":
            vulkan_dll = shutil.which("vulkan-1.dll")
            if vulkan_dll is None:
                # Also check System32 directly — shutil.which may miss it.
                sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "vulkan-1.dll")
                if not os.path.isfile(sys32):
                    print("[⚠️Coworker] start_local_llama: vulkan-1.dll not found — Vulkan backend may fail")
                else:
                    print("[🛠️Coworker] start_local_llama: vulkan-1.dll found at {:s}".format(sys32))
            else:
                print("[🛠️Coworker] start_local_llama: vulkan-1.dll found at {:s}".format(vulkan_dll))

        if backend in ("cuda", "vulkan") and model_path and os.path.isfile(str(model_path)):
            ngpu_layers = autodetect_gpu_layers(Path(str(model_path)), ctx_size)
        else:
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
            # Add --mmproj if a projector file exists next to the model.
            mmproj_path = _resolve_mmproj_path(model_path)
            if mmproj_path:
                args.extend(['--mmproj', str(mmproj_path)])
                print("[🛠️Coworker] start_local_llama: using mmproj at {:s}".format(str(mmproj_path)))

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

        # Ensure the bundled directory is on PATH so companion DLLs
        # (cudart64_12.dll, etc.) are found when llama-server launches in
        # its own console window on Windows.
        bundled_dir = str(_get_bundled_llama_dir())
        existing_path = env.get("PATH", "")
        if bundled_dir not in existing_path:
            env["PATH"] = bundled_dir + os.pathsep + existing_path

        # On Windows, also register the bundled dir as a DLL search directory.
        # PATH-based DLL discovery is unreliable with CREATE_NEW_CONSOLE;
        # os.add_dll_directory() (Python 3.8+) is the robust alternative.
        global _bundled_dll_handle
        _bundled_dll_handle = None
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            try:
                _bundled_dll_handle = os.add_dll_directory(bundled_dir)
                print("[🛠️Coworker] start_local_llama: registered DLL dir {:s}".format(bundled_dir))
            except OSError as ex:
                print("[🛠️Coworker] start_local_llama: os.add_dll_directory failed — {:s}".format(str(ex)))

        # On Windows the output goes to the new console window.
        # On Linux/macOS it goes to a log file to avoid hijacking the Blender console.

        if sys.platform == "win32":
            # Launch llama-server in a NEW console window so the user can
            # see its output and close the window to stop it.
            # CreateProcessW(CREATE_NEW_CONSOLE) gives us a proper handle
            # that terminates the actual server, not a wrapper.
            # stdout/stderr are NOT redirected -- the output goes to the new
            # console window so the user can see model loading, health
            # checks, and errors in real time.  Nothing about the parent
            # (Blender/Bforartists) console is touched.
            print("[🛠️Coworker] start_local_llama: WIN32 path (CREATE_NEW_CONSOLE)")
            print("[🛠️Coworker] start_local_llama:   args = {:s}".format(str(args)))
            # The window title is set via STARTUPINFOW.lpTitle at creation
            # time, so the NEW console is titled.  (SetConsoleTitleW from
            # here would retitle the Blender/Bforartists terminal instead —
            # the parent's own console.)
            proc = _popen_new_console(
                args,
                env=env,
                title="BFA Coworker — llama-server",
            )
            print("[🛠️Coworker] start_local_llama:   Popen returned pid={:d}".format(proc.pid))
        else:
            # Linux / macOS: detach from the parent process group so the
            # server survives Blender exiting.  Redirect stdio to the log
            # file so it does not hijack the Blender console.
            try:
                log_handle = open(str(_llama_server_log_path()), "w", encoding="utf-8", errors="replace")
            except OSError as ex:
                log_handle = None
                print("[🛠️Coworker] start_local_llama: could not open log file — {:s}".format(str(ex)))
            stdio_target = log_handle if log_handle is not None else subprocess.DEVNULL
            print("[🛠️Coworker] start_local_llama: POSIX path (start_new_session=True)")
            print("[🛠️Coworker] start_local_llama:   args = {:s}".format(str(args)))
            print("[🛠️Coworker] start_local_llama:   log = {:s}".format(str(_llama_server_log_path())))
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=stdio_target,
                stderr=stdio_target,
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
    # NOTE: `_shutting_down` lives on AgentState (agent_controller),
    # not LLMState — do not set it here.
    _state.error = "Stopping LLM..."

    with _lock:
        proc = _llama_process

    print("[🛠️Coworker] stop_local_llama:   tracked proc = {:s}".format(str(proc)))

    # Try to terminate the tracked process first.
    if proc is not None:
        try:
            print("[🛠️Coworker] stop_local_llama:   calling proc.terminate()")
            proc.terminate()
            print("[🛠️Coworker] stop_local_llama:   waiting up to 10s for exit...")
            proc.wait(timeout=10)
            print("[🛠️Coworker] stop_local_llama:   process exited")
        except subprocess.TimeoutExpired:
            print("[🛠️Coworker] stop_local_llama:   timeout — force killing")
            _state.error = "Force killing LLM..."
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
    _state.error = ""

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

    On failure, the llama-server log tail is included in the error so the
    real startup problem is visible instead of a generic message.
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
            tail = get_llama_server_log_tail()
            msg = "llama-server exited during startup (exit code {:d}{:s}) — check the model file, mmproj, GPU memory, and port".format(
                proc.returncode, _describe_exit_code(proc.returncode))
            # DLL_NOT_FOUND on Windows — the CUDA/Vulkan runtime DLLs are
            # missing from the bundled directory.
            if sys.platform == "win32" and (proc.returncode & 0xFFFFFFFF) == 0xC0000135:
                bundled = _get_bundled_llama_dir()
                cuda_dlls = ["cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"]
                missing = [d for d in cuda_dlls if not (bundled / d).is_file()]
                if missing:
                    msg += (
                        "\n\nDLL_NOT_FOUND: The following CUDA runtime DLLs are missing from "
                        "{:s}: {:s}. Re-download the llama-server CUDA binary from Preferences "
                        "to restore them.".format(str(bundled), ", ".join(missing))
                    )
                else:
                    msg += (
                        "\n\nDLL_NOT_FOUND: The CUDA DLLs are present in {:s} but Windows "
                        "cannot find them. Try adding the bundled directory to your system PATH "
                        "or re-downloading the CUDA binary.".format(str(bundled))
                    )
            # The GPU OOM hint doesn't need the model path, so it runs first and
            # is not gated on _last_launched_model_path.
            if tail and _log_looks_like_gpu_oom(tail):
                msg += "\n\n{:s}".format(_gpu_oom_hint())
            # A truncated/corrupt GGUF is the most common local-model cause:
            # llama-server dies with "missing tensor" after a few dozen layers.
            # Surface an actionable "re-download" hint when we can confirm it.
            if tail and _last_launched_model_path is not None:
                # Truncated/corrupt GGUF -> suggest re-downloading.
                if _log_looks_like_model_load_failure(tail):
                    integrity_warning = check_model_file_integrity(_last_launched_model_path)
                    if integrity_warning:
                        msg += "\n\n{:s}".format(integrity_warning)
                # Wrong mmproj -> explain the generic-name collision + fix.
                if _log_looks_like_mmproj_mismatch(tail):
                    msg += "\n\n{:s}".format(_mmproj_mismatch_hint(_last_launched_model_path))
            if tail:
                msg += "\n\n--- llama-server.log (tail) ---\n{:s}".format(tail)
            _set_error(msg)
            print("[🛠️Coworker] wait_until_ready: process exited early (rc={:d}){:s}".format(
                proc.returncode, ":\n{:s}".format(tail) if tail else ""))
            return False
        _time.sleep(poll)
        poll = min(poll * 1.5, 3.0)
    tail = get_llama_server_log_tail()
    msg = "llama-server did not become ready within {:.0f}s".format(timeout)
    if tail:
        msg += "\n\n--- llama-server.log (tail) ---\n{:s}".format(tail)
    _set_error(msg)
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

_MIN_MMPROJ_BYTES = 1 * 1024 * 1024  # 1 MB — real projectors are 100s of MB


def _is_valid_mmproj(candidate: Path, model_name: str) -> bool:
    """Basic sanity checks on a candidate mmproj file."""
    del model_name  # Reserved for future checks (e.g. architecture match).
    if not candidate.is_file():
        return False
    try:
        size = candidate.stat().st_size
    except OSError:
        return False
    if size < _MIN_MMPROJ_BYTES:
        print(
            "[🛠️Coworker] _resolve_mmproj_path: skipping {:s} — only {:.0f} KB "
            "(truncated/broken download?)".format(str(candidate), size / 1024.0)
        )
        return False
    return True


def _local_mmproj_name(preset: "ModelPreset") -> str:
    """Unique local filename for a preset's vision projector.

    Several presets share the same generic HF filename (``mmproj-F16.gguf``),
    so saving them all under that name makes them clobber each other when
    multiple models live in one folder.  Each projector is kept under a
    model-specific name derived from the repo, e.g. ``mmproj-F16-Qwen3.5-9B.gguf``.
    """
    repo_stem = preset.repo_id.rstrip("/").split("/")[-1]
    if repo_stem.lower().endswith("-gguf"):
        repo_stem = repo_stem[:-5]
    base = os.path.splitext(preset.mmproj_filename)[0]
    ext = os.path.splitext(preset.mmproj_filename)[1] or ".gguf"
    return "{:s}-{:s}{:s}".format(base, repo_stem, ext)


def _count_vision_models_in_dir(model_dir: Path | None) -> int:
    """Count curated vision-preset model files present in *model_dir*."""
    if not model_dir or not model_dir.is_dir():
        return 0
    files = {
        entry.lower()
        for entry in os.listdir(str(model_dir))
        if os.path.isfile(os.path.join(str(model_dir), entry))
    }
    return sum(
        1 for p in PRESET_MODELS
        if p.mmproj_filename and p.filename.lower() in files
    )


def _resolve_mmproj_path(model_path: Path | str | None) -> Path | None:
    """Resolve the mmproj (vision projector) file for a local model.

    A mismatched projector makes llama-server exit at startup, so we only
    attach one when we are confident it belongs to the model:

    * If the model filename matches a curated preset that declares an
      ``mmproj_filename`` (vision-capable model), prefer the per-model
      projector file (``mmproj-F16-Qwen3.5-9B.gguf``).  Fall back to the
      generic name (``mmproj-F16.gguf``) only when this folder holds exactly
      one vision-preset model — otherwise the generic file could be another
      model's projector.
    * Otherwise, pick up a generic ``mmproj-*.gguf`` next to the model only
      when the model name itself looks vision-capable (contains "vl" or
      "vision").

    Also rejects trivially small files (truncated downloads).
    Returns ``None`` when no suitable projector is found.
    """
    if isinstance(model_path, str):
        model_path = Path(model_path)
    model_dir = model_path.parent if model_path else None
    if not model_dir or not model_dir.is_dir():
        return None
    model_name = (model_path.name or "").lower()

    # 1. Curated preset match.
    for preset in PRESET_MODELS:
        if not preset.mmproj_filename:
            continue
        if model_name != preset.filename.lower():
            continue

        # Preferred: the per-model projector file (unique per preset).
        candidates = [model_dir / _local_mmproj_name(preset)]
        # Fallback: the generic name — but only when this folder holds exactly
        # one vision-preset model, so the generic file can't be a different
        # model's projector.
        if _count_vision_models_in_dir(model_dir) == 1:
            candidates.append(model_dir / preset.mmproj_filename)

        for candidate in candidates:
            if _is_valid_mmproj(candidate, model_name):
                print(
                    "[🛠️Coworker] _resolve_mmproj_path: using {:s} (preset {:s})".format(
                        str(candidate), preset.identifier)
                )
                return candidate

        # Explain why vision is unavailable so it isn't a mystery.
        generic = model_dir / preset.mmproj_filename
        if generic.is_file() and _count_vision_models_in_dir(model_dir) > 1:
            print(
                "[🛠️Coworker] _resolve_mmproj_path: {:s} is present but the folder contains several "
                "vision models — one shared projector can't match them all, so it is not attached. "
                "Use the addon's Download button (saves each projector under its own name) or rename "
                "it to {:s}".format(generic.name, _local_mmproj_name(preset))
            )
        else:
            print(
                "[🛠️Coworker] _resolve_mmproj_path: preset {:s} needs {:s} but it is missing — "
                "vision input will be unavailable".format(preset.identifier, _local_mmproj_name(preset))
            )
        return None

    # 2. Non-preset model that looks vision-capable — generic projector names.
    if "vl" in model_name or "vision" in model_name:
        for candidate_name in ("mmproj-F16.gguf", "mmproj.gguf", "mmproj-BF16.gguf"):
            candidate = model_dir / candidate_name
            if _is_valid_mmproj(candidate, model_name):
                print(
                    "[🛠️Coworker] _resolve_mmproj_path: using {:s} for vision model {:s}".format(
                        str(candidate), model_name)
                )
                return candidate

    return None


def _download_mmproj_if_needed(
    repo_id: str,
    model_filename: str,
    models_dir: Path,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Download the mmproj file for a vision-capable model if not already present.

    Looks up the preset by model filename to find the correct projector filename.
    Skips if the projector already exists or the model doesn't have one.

    The projector is saved under a per-model name (see :func:`_local_mmproj_name`)
    because several presets share the generic HF filename ``mmproj-F16.gguf`` —
    saving them all under that name would clobber each other when multiple
    vision models share one models folder.
    """
    # Find the preset that matches this model filename.
    preset = None
    for p in PRESET_MODELS:
        if p.filename == model_filename and p.mmproj_filename:
            preset = p
            break
    if not preset:
        return  # No projector needed for this model.

    mmproj_fname = preset.mmproj_filename
    local_name = _local_mmproj_name(preset)
    mmproj_dest = models_dir / local_name
    if mmproj_dest.exists():
        print("[🛠️Coworker] _download_mmproj_if_needed: {:s} already exists".format(str(mmproj_dest)))
        return

    print(
        "[🛠️Coworker] _download_mmproj_if_needed: downloading {:s} as {:s} from {:s}".format(
            mmproj_fname, local_name, repo_id)
    )
    _set_download_progress("Downloading vision projector {:s} ...".format(local_name))
    if progress_callback:
        progress_callback("Downloading vision projector {:s} ...".format(local_name))

    # Reuse the direct download function for the projector file (source name
    # is the HF filename, destination is the per-model local name).
    success = _download_gguf_direct(repo_id, mmproj_fname, mmproj_dest, progress_callback)
    if success:
        print(
            "[🛠️Coworker] _download_mmproj_if_needed: {:s} downloaded to {:s}".format(
                mmproj_fname, str(mmproj_dest))
        )
    else:
        print("[🛠️Coworker] _download_mmproj_if_needed: failed to download {:s}".format(mmproj_fname))
        # Non-fatal — the model can still run without vision.


def _set_error(msg: str) -> None:
    with _lock:
        _state.error = msg


# ---------------------------------------------------------------------------
# Cleanup helper (call from unregister)

def cleanup() -> None:
    """Stop the local LLM if running. Safe to call multiple times."""
    stop_local_llama()