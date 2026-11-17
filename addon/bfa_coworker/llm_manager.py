# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
LLM Manager -- handles detection, download, and lifecycle of local/remote LLM backends.

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
    "resolve_llama_path",
    "llama_custom_problem",
    "llama_asset_parts",
    "llama_asset_url",
    "extract_llama_folder",
    "invalidate_llama_server_cache",
    "download_model",
    "download_llama_server",
    "remove_llama_server",
    "start_local_llama",
    "stop_local_llama",
    "check_local_model_ready",
    "set_pending_model_download",
    "clear_pending_model_download",
    "get_pending_model_download",
    "clear_error",
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
    "get_runtime_ctx",
    "get_runtime_vision",
    "validate_ctx_against_hardware",
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
import posixpath
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
from typing import Any, Callable

try:  # Package import (inside Blender / the addon package).
    from . import process_guard as _process_guard  # type: ignore[import-not-found]
except ImportError:  # Standalone import (unit tests load this file directly).
    import importlib.util as _pg_ilu
    _pg_spec = _pg_ilu.spec_from_file_location(
        "process_guard", Path(__file__).with_name("process_guard.py"))
    _process_guard = _pg_ilu.module_from_spec(_pg_spec)  # type: ignore[arg-type]
    _pg_spec.loader.exec_module(_process_guard)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Constants

_LOCAL_LLM_DEFAULT_PORT = 8081
_LOCAL_LLM_HEALTH_URL = "http://127.0.0.1:{:d}/health"
_LOCAL_LLM_CHAT_URL = "http://127.0.0.1:{:d}/v1/chat/completions"
_MODEL_DOWNLOAD_TIMEOUT = 300  # seconds

# Windows: run child processes without a console window.  Blender is a GUI
# app, so a plain Popen of a console program can flash a console window;
# CREATE_NEW_CONSOLE (the old launcher) sent output somewhere we could not
# read.  CREATE_NO_WINDOW keeps things quiet and -- with stdio redirected --
# lets us capture every line.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Windows: give the child its own *visible* console window.  Used only when
# Debug / Diagnostics mode is on, so the user can watch llama-server load a
# model live (token/s, GPU offload, errors).  Normal launches stay silent.
_CREATE_NEW_CONSOLE = 0x00000010 if sys.platform == "win32" else 0


def _show_llama_console() -> bool:
    """True when the llama-server console window should be visible.

    Only on Windows, and only when the user has enabled Debug / Diagnostics
    mode -- the visible console is a debugging aid.  Reads the preference at
    call time and never raises (returns False outside Blender / on error).
    """
    if sys.platform != "win32":
        return False
    try:
        from .shared import is_debug_mode  # type: ignore[import-not-found]
        return bool(is_debug_mode())
    except Exception:  # pylint: disable=broad-exception-caught
        return False


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

# Minimum build for the optional launch flags assembled in
# start_local_llama (--cache-reuse).  Flags guarded by this threshold are
# silently dropped on older/custom builds instead of crashing startup with
# "error: invalid argument".
_MIN_BUILD_CACHE_REUSE = 4000


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


_LLAMA_SERVER_PREV_LOG_NAME = "llama-server.prev.log"


def _llama_server_log_path() -> Path:
    """Return the path of the llama-server log file (recreated each launch)."""
    return _get_bundled_llama_dir() / _LLAMA_SERVER_LOG_NAME


def _rotate_llama_server_log() -> None:
    """Keep the previous launch's log as ``llama-server.prev.log`` (one generation)."""
    try:
        log = _llama_server_log_path()
        if log.is_file():
            os.replace(str(log), str(log.with_name(_LLAMA_SERVER_PREV_LOG_NAME)))
    except OSError as ex:
        print("[Coworker] start_local_llama: could not keep the previous log -- {:s}".format(str(ex)))
        try:
            _llama_server_log_path().unlink()
        except OSError:
            pass


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


# Build thresholds for optional llama-server flags (see _filter_flags_for_build).
_FLAG_BUILD_MINIMUMS: dict[str, int] = {
    "--cache-reuse": _MIN_BUILD_CACHE_REUSE,
}


def _filter_flags_for_build(flags: tuple[str, ...]) -> tuple[str, ...]:
    """Return *flags* filtered to those supported by the pinned server build.

    Values that belong to a flag (e.g. ``'256'`` after ``--cache-reuse``)
    are kept with their flag.  Flags without a known build minimum always
    pass through; when the binary's actual build number cannot be parsed
    the pinned :data:`_LLAMA_SERVER_VERSION` is used as a safe fallback.
    """
    pinned = _parse_llama_build_number(
        "version: " + _LLAMA_SERVER_VERSION.lstrip("b"))
    try:
        exe = find_llama_server()
    except Exception:  # pylint: disable=broad-exception-caught
        exe = None
    build = 0
    if exe:
        try:
            build = _parse_llama_build_number(_llama_server_version(exe))
        except Exception:  # pylint: disable=broad-exception-caught
            build = 0
    if not build:
        build = pinned
    if not build:
        return flags  # Cannot determine build at all -- pass through.
    out: list[str] = []
    drop_value = False
    for tok in flags:
        if drop_value:
            # The previous (dropped) flag took a value -- drop it too,
            # unless the next token is itself a flag.
            drop_value = False
            if tok.startswith("--"):
                out.append(tok)  # fall through to flag handling
            else:
                continue
        minimum = _FLAG_BUILD_MINIMUMS.get(tok)
        if minimum is not None and build < minimum:
            print("[Coworker] _filter_flags_for_build: dropping {:s} "
                  "(needs build >={:d}, have {:d})".format(tok, minimum, build))
            drop_value = True
            continue
        out.append(tok)
    return tuple(out)


def _current_preset_extra_args() -> tuple[str, ...]:
    """Return the extra llama-server flags for the configured model preset.

    Matches the running config's ``model_repo_id`` + ``model_filename``
    against :data:`PRESET_MODELS` (the preferences enum is not carried by
    ``LLMConfig``).  Returns an empty tuple for a custom or unknown model.

    Never raises: a lookup failure must not break server launch.  Without
    this helper the Qwen presets' ``--no-context-shift`` flag was silently
    never applied (the call site referenced a function that did not exist,
    so launch raised ``NameError``).
    """
    try:
        repo = getattr(_config, "model_repo_id", "") or ""
        fname = getattr(_config, "model_filename", "") or ""
        if repo and fname:
            for preset in PRESET_MODELS:
                if preset.repo_id == repo and preset.filename == fname:
                    return tuple(preset.extra_server_args)
    except Exception as _ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] _current_preset_extra_args: lookup failed -- {:s}".format(
            str(_ex)))
    return ()


# Cache of the --flags a given llama-server binary advertises in --help.
# A stricter or mismatched build prints "unknown argument" and exits 1 on any
# flag it does not accept, and version numbers drift, so the binary's own
# --help is the only reliable source of truth.
_server_supported_flags_cache: dict[str, "set[str] | None"] = {}


def _server_supported_flags(server_exe: str) -> "set[str] | None":
    """Return the set of ``--flags`` *server_exe* lists in ``--help``.

    Cached per executable path.  Returns ``None`` when the probe fails (in
    which case callers must NOT filter -- better to pass a flag the build
    might ignore than to strip a needed one).
    """
    if server_exe in _server_supported_flags_cache:
        return _server_supported_flags_cache[server_exe]
    result: "set[str] | None" = None
    try:
        out = subprocess.run(
            [server_exe, "--help"],
            capture_output=True, text=True, timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
        text = (out.stdout or "") + "\n" + (out.stderr or "")
        flags = set(re.findall(r"(?<![\w-])(--[A-Za-z0-9][A-Za-z0-9-]*)", text))
        result = flags or None
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] _server_supported_flags: probe failed -- {:s}".format(str(ex)))
        result = None
    _server_supported_flags_cache[server_exe] = result
    return result


def _server_supports_flag(server_exe: str, flag: str) -> bool:
    """True when the binary's --help lists *flag* (conservative on failure)."""
    flags = _server_supported_flags(server_exe)
    return flags is not None and flag in flags


def _filter_flags_against_help(
    args: list[str], supported: "set[str]", keep: "set[str]" = frozenset(),
) -> tuple[list[str], list[str]]:
    """Drop ``--flags`` not in *supported* (and their values).

    *args[0]* is the executable and is always kept.  Any ``--flag`` in
    *supported* -- or in *keep*, which holds launch-critical flags that must
    never be dropped even if the help probe missed them -- is kept with the
    value token that follows it; any other flag and its value are removed.
    Returns ``(new_args, dropped)``.
    """
    out: list[str] = [args[0]] if args else []
    dropped: list[str] = []
    i = 1
    n = len(args)
    while i < n:
        tok = args[i]
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name in supported or name in keep:
                out.append(tok)
            else:
                dropped.append(name)
                if i + 1 < n and not args[i + 1].startswith("--"):
                    i += 1  # also drop this flag's value
        else:
            out.append(tok)
        i += 1
    return out, dropped


# Flags the server cannot run without.  These are never dropped by the
# help-filter, so a help-probe miss can never launch a server missing its
# port, model, or context size.
_REQUIRED_LLAMA_FLAGS: "frozenset[str]" = frozenset({
    "--jinja", "--host", "--port", "--ctx-size", "--n-gpu-layers",
    "--model", "--mmproj", "--hf-repo", "--hf-file",
})


def build_server_tuning_args(*, gpu: bool, kv_q8: bool, slots: int = 1, projector: bool = False,
                             verbose: bool = False, preset_extra: "tuple[str, ...]" = (),
                             cache_ram_mib: "int | None" = None) -> list[str]:
    """The flags after ``--n-gpu-layers`` that shape memory and speed (#543).

    * ``--parallel``: one slot while Local chains chats (the server's auto is
      4, each with its own recurrent state and checkpoints); more only with
      Local Side by Side, then ``--kv-unified`` so the slots share one context
      instead of splitting it (#399).
    * ``--cache-ram``: a smaller host prompt cache than the server's 8 GB.
    * ``--cache-reuse`` only without a projector: the server disables it for
      multimodal models ("cache_reuse is not supported by multimodal").
    * KV quantization and the batch sizes on GPU backends only.
    Every flag still goes through the binary's own ``--help`` filter.
    """
    args: list[str] = []
    if verbose:
        # --verbose logs every prompt and token: a debug opt-in only.
        args.append("--verbose")
    args.extend(preset_extra)
    if gpu:
        if kv_q8:
            args.extend(("--cache-type-k", "q8_0", "--cache-type-v", "q8_0"))
        # A modest batch/ubatch avoids pathological prompt-processing slowdowns.
        args.extend(("--batch-size", "2048", "--ubatch-size", "512"))
    slots = max(1, int(slots or 1))
    args.extend(("--parallel", str(slots)))
    if slots > 1:
        args.append("--kv-unified")
    args.extend(("--cache-ram", str(int(LOCAL_CACHE_RAM_MIB if cache_ram_mib is None else cache_ram_mib))))
    if not projector:
        args.extend(("--cache-reuse", "256"))
    return args


_llama_server_version_cache: str = ""


def _llama_server_version(server_exe: str) -> str:
    """Return the llama-server build version string (cached per session).

    The version is logged at launch -- an outdated llama.cpp build is a common
    reason a brand-new preset fails to load (unknown model architecture).
    """
    global _llama_server_version_cache
    if _llama_server_version_cache:
        return _llama_server_version_cache
    try:
        result = subprocess.run(
            [server_exe, "--version"],
            capture_output=True, text=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
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
# a GGUF that was cut off mid-download or mid-copy -- llama-server fails with
# ``missing tensor ...`` after loading a few dozen layers.  The GGUF header
# says where its last tensor ends, so the file on disk is checked against its
# own header, in bytes, with no network request (#543: the old check asked
# Hugging Face on every launch and flagged a complete file whose upload had
# since been replaced by a newer one).


def check_model_file_integrity(model_path: Path | str | None) -> str:
    """Return an actionable warning if a local GGUF file is shorter than its header says.

    Any .gguf is checked (not only presets).  Returns an empty string when the
    file is complete or cannot be read as GGUF.
    """
    if not model_path:
        return ""
    if isinstance(model_path, str):
        model_path = Path(model_path)
    if not model_path.is_file():
        return ""
    info = gguf_info(model_path)
    if info is None or not info.get("data_end"):
        return ""
    local = int(info["file_bytes"])
    expected = int(info["data_end"])
    if local < expected:
        return (
            "{:s} is truncated: it is {:,d} bytes on disk, but its header says the data "
            "ends at byte {:,d} ({:,d} bytes missing). Delete it and re-download / re-copy "
            "the model (a partial copy fails with \"missing tensor\")."
        ).format(model_path.name, local, expected, expected - local)
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
        "The vision projector (mmproj) does not match this model -- the generic "
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
            " You are using a {:d}-token context window -- at that size the KV cache "
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
            "'Download llama-server' button so it fetches the CUDA build -- Vulkan "
            "is often less memory-efficient, and on laptops it may pick the "
            "integrated GPU. Check the 'ggml_vulkan: Found N devices' line in "
            "llama-server.log for which device was selected."
        )
    return (
        "The GPU ran out of memory while loading the model{:s}. With --n-gpu-layers "
        "99 both the weights and the KV cache are placed in VRAM.{:s}{:s}\n"
        "Fixes to try:\n"
        "  1. Reduce the context size (e.g. 32768 instead of {:d}) -- the KV cache "
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
    0xC0000005: "ACCESS_VIOLATION -- crashed, often a GPU driver / OOM issue",
    0xC000001D: "ILLEGAL_INSTRUCTION -- the CPU lacks an instruction this build needs (try another llama-server build)",
    0xC0000374: "HEAP_CORRUPTION -- crashed, possible driver bug",
    0xC0000409: "STACK_BUFFER_OVERRUN -- crashed, possible driver bug",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000135: "DLL_NOT_FOUND -- a required DLL is missing (use the bundled build)",
    0xC0000142: "DLL_INIT_FAILED",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO",
    0xC000000D: "INVALID_PARAMETER",
    0xC000013A: "CTRL_C_EXIT",
}


def _describe_exit_code(rc: int) -> str:
    """Return a readable description for a Windows crash exit code."""
    unsigned = rc & 0xFFFFFFFF
    name = _WIN_CRASH_CODES.get(unsigned, "")
    suffix = (" -- " + name) if name else ""
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
    local_ctx_size: int = 32768
    local_max_tokens: int = 16384  # Max output tokens per API call
    thinking_budget_tokens: int = 1024  # Max chain-of-thought reasoning tokens per API call
    hf_token: str = ""  # HuggingFace token for gated models
    llama_backend: str = "auto"  # "auto" | "cpu" | "cuda" | "vulkan"
    local_kv_cache_quant: bool = False  # Quantize KV cache to q8_0 (GPU backends only)
    local_server_verbose: bool = False  # Pass --verbose to llama-server (debug logging)
    # Server slots (--parallel): 1 while Local chains chats; the chat count with
    # Local Side by Side on (see threads.slots_for).  #543
    local_parallel: int = 1
    # Soft co-work lock: while a turn runs, make the coworker's own objects
    # un-selectable in the UI so the user cannot re-target them mid-turn.
    # Restored when the turn ends.
    lock_scene_while_working: bool = True
    # Long-request auto-continue: extra rounds a turn may run when it used
    # its tool-iteration budget while still making progress (all modes).
    auto_continue_rounds: int = 3
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
    # Set when the configured local model is neither on disk nor in the HF
    # cache, so llama-server would have to download it.  Launching is stalled
    # until the user confirms the download (see start_local_llama).
    model_download_required: bool = False
    pending_model_repo: str = ""
    pending_model_filename: str = ""
    pending_model_dest: str = ""
    pending_model_disk_hint: str = ""


# ---------------------------------------------------------------------------
# Model Presets

@dataclass
class ModelPreset:
    """Metadata for a curated model preset shown in the UI dropdown."""

    identifier: str
    name: str
    repo_id: str
    filename: str
    capability: str  # "Excellent" | "Strong" | "Moderate"
    category: str  # "flagship" | "mid_range" | "lightweight"
    description: str  # Longer tooltip text (no memory claims: those are generated)
    context_window: int = 131072  # Context window size in tokens
    max_tokens: int = 16384  # Max output tokens per API call
    vision: bool = False  # Whether the model supports image input
    mmproj_filename: str = ""  # Projector filename for vision (e.g. "mmproj-F16.gguf")
    why: str = ""  # One-line "why pick this" per sub-tier
    expected_sha256: str = ""  # SHA-256 hash for download verification (empty = skip check)
    # Extra llama-server CLI flags for this model (e.g. Qwen-specific
    # switches). Appended after the base flags when the pinned server build
    # is new enough for every flag in the tuple; silently skipped otherwise.
    extra_server_args: tuple[str, ...] = ()
    # What the VRAM estimate needs (#558), read from the model's own GGUF header
    # (see the note above PRESET_MODELS).  0 = not read: no memory text.
    file_bytes: int = 0             # the model file
    projector_bytes: int = 0        # its vision projector (0: none)
    kv_bytes_per_token: int = 0     # 16-bit KV cache per token (model_memory_profile)
    recurrent_bytes: int = 0        # recurrent state per server slot (hybrid models)
    block_count: int = 0            # transformer blocks (--n-gpu-layers counts these)
    sliding_window: int = 0         # > 0: sliding-window layers, counted as full (an upper bound)

    @property
    def disk_gb(self) -> str:
        """The download (model and projector), e.g. ``"~16.2 GB"``; generated."""
        if not self.file_bytes:
            return ""
        return "~{:s} GB".format(_gb_up((self.file_bytes + self.projector_bytes) / _MIB))

    @property
    def vram_note(self) -> str:
        """What the model needs in VRAM: see :func:`preset_memory_note`."""
        return preset_memory_note(self)[0]

    @property
    def hardware_note(self) -> str:
        """The card it fits: see :func:`preset_memory_note`."""
        return preset_memory_note(self)[1]


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


# Extra llama-server flags applied to Qwen-family presets. All exist in
# the pinned build (b10154):
#   --no-context-shift: deterministic KV sizing so the client-side prompt
#   budget (Phase 1) matches what the server actually reserves.
_QWEN_EXTRA_SERVER_ARGS: tuple[str, ...] = (
    "--no-context-shift",
)

# Memory numbers (#558): ``file_bytes``, ``projector_bytes`` (the preset's own
# projector) and, through model_memory_profile(), ``kv_bytes_per_token``,
# ``recurrent_bytes``, ``block_count`` and ``sliding_window`` from each model's
# GGUF header.  Read on 2026-11-16 with _misc/read_preset_headers.py:
# Qwen3.8-27B-UD-Q4_K_M and Qwen3.5-9B-DeepSeek-V4-Flash-Q4_K_M (and their
# projectors) from the files on disk, every other one from the start of its
# header on Hugging Face (one 256 KB range request) and the sizes by HEAD.
# Re-run that script when a preset's file changes.  The VRAM and card texts are
# built from these numbers (preset_memory_note); no memory claim is hand-written.
PRESET_MODELS: list[ModelPreset] = [
    # -- Flagship ----------------------------------------------------
    ModelPreset(
        identifier="qwen38_27b_q8",
        name="Qwen3.8-27B (Q8_0)",
        repo_id="unsloth/Qwen3.8-27B-GGUF",
        filename="Qwen3.8-27B-Q8_0.gguf",
        capability="Excellent",
        category="flagship",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        why="Latest Qwen3.8 -- best coding + vision + agentic reasoning at high precision",
        extra_server_args=_QWEN_EXTRA_SERVER_ARGS,
        description=(
            "Qwen3.8-27B at Q8_0 -- the latest Qwen generation. Native vision-language,\n"
            "thinking mode, and agentic tool calling. 262K context. Apache 2.0.\n"
            "Best quality flagship for complex multi-step Blender tasks."
        ),
        file_bytes=29047086048, projector_bytes=927607488, kv_bytes_per_token=65536,
        recurrent_bytes=156893184, block_count=65,
    ),
    ModelPreset(
        identifier="fable_fusion_27b_q6",
        name="Fable Fusion 27B (Q6_K)",
        repo_id="DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF",
        filename="Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q6_K.gguf",
        capability="Excellent",
        category="flagship",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        why="Top-ranked fine-tune -- ARC-711 benchmark, uncensored, vision-capable",
        extra_server_args=_QWEN_EXTRA_SERVER_ARGS,
        description=(
            "Multi-stage fine-tune of Qwen3.6-27B. Exceeds base model in 6/7 benchmarks.\n"
            "Vision-capable, 256K context, uncensored. Apache 2.0.\n"
            "The strongest open 27B fine-tune for agentic work."
        ),
        file_bytes=24033705440, projector_bytes=927607360, kv_bytes_per_token=65536,
        recurrent_bytes=156893184, block_count=65,
    ),
    ModelPreset(
        identifier="nail_35b_q4",
        name="Nail 35B A3B (UD-Q4_K_XL)",
        repo_id="peculiar-ragdoll/Nail-Qwen3.6-35B-A3B-GGUF",
        filename="Nail-Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",
        capability="Excellent",
        category="flagship",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        why="MoE efficiency -- 3.4B active params, fast inference, sharpened template",
        extra_server_args=_QWEN_EXTRA_SERVER_ARGS,
        description=(
            "Qwen3.6-35B-A3B with improved chat template and force-applied terseness prompt.\n"
            "~3.4B active params (MoE) -- fast for its size. Vision-capable.\n"
            "Apache 2.0. Best throughput-to-quality ratio in flagship tier."
        ),
        file_bytes=22360465312, projector_bytes=899283680, kv_bytes_per_token=20480,
        recurrent_bytes=65863680, block_count=40,
    ),
    # -- Mid-Range ---------------------------------------------------
    ModelPreset(
        identifier="gpt_oss_20b_q4",
        name="GPT-OSS 20B (Q4_K_M)",
        repo_id="unsloth/gpt-oss-20b-GGUF",
        filename="gpt-oss-20b-Q4_K_M.gguf",
        capability="Strong",
        category="mid_range",
        context_window=131072,
        max_tokens=16384,
        vision=False,
        mmproj_filename="",
        why="OpenAI's open-weight reasoning model -- best Blender benchmarked default",
        description=(
            "OpenAI's open-weight reasoning model. 21B params / 3.6B active (MoE).\n"
            "Native function calling, structured outputs, and agentic capabilities.\n"
            "Apache 2.0. Best-tested default for Blender."
        ),
        file_bytes=11624759488, projector_bytes=0, kv_bytes_per_token=49152,
        recurrent_bytes=0, block_count=24, sliding_window=128,
    ),
    ModelPreset(
        identifier="qwen38_27b_q4",
        name="Qwen3.8-27B (Q4_K_M)",
        repo_id="unsloth/Qwen3.8-27B-GGUF",
        filename="Qwen3.8-27B-UD-Q4_K_M.gguf",
        # Measured (#543, RTX 4090, one slot, with the projector): 18,896 MiB of
        # dedicated VRAM at 32K with 16-bit KV, 18,022 MiB with 8-bit; the
        # estimate from these numbers is 19,168 / 18,208 (tests pin it).
        capability="Excellent",
        category="mid_range",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        why="Latest Qwen3.8 at Q4 -- vision + agentic on one card",
        extra_server_args=_QWEN_EXTRA_SERVER_ARGS,
        description=(
            "Qwen3.8-27B at Q4_K_M -- the latest Qwen generation. Native vision-language,\n"
            "thinking mode, and agentic tool calling. 262K context. Apache 2.0."
        ),
        file_bytes=16464440224, projector_bytes=927607488, kv_bytes_per_token=65536,
        recurrent_bytes=156893184, block_count=65,
    ),
    ModelPreset(
        identifier="fable_fusion_27b_iq4",
        name="Fable Fusion 27B (IQ4_XS)",
        repo_id="DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF",
        filename="Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-AMD-MTP-IQ4_XS.gguf",
        capability="Excellent",
        category="mid_range",
        context_window=262144,
        max_tokens=16384,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        why="Fable Fusion at IQ4 -- smaller than the Q6_K, still top-tier reasoning",
        extra_server_args=_QWEN_EXTRA_SERVER_ARGS,
        description=(
            "Fable Fusion 27B at IQ4_XS -- smaller quant that still outperforms base Qwen3.6.\n"
            "Vision-capable, 256K context. Apache 2.0.\n"
            "The mid-range way to run Fable Fusion."
        ),
        file_bytes=16808074720, projector_bytes=927607360, kv_bytes_per_token=65536,
        recurrent_bytes=156893184, block_count=65,
    ),
    # -- Lightweight -------------------------------------------------
    ModelPreset(
        identifier="gemma4_e4b_q4",
        name="Gemma 4 E4B (Q4_K_M)",
        repo_id="unsloth/gemma-4-E4B-it-GGUF",
        filename="gemma-4-E4B-it-Q4_K_M.gguf",
        capability="Strong",
        category="lightweight",
        context_window=131072,
        max_tokens=12288,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        why="Google's small agentic model -- vision + function calling",
        description=(
            "Google's Gemma 4 E4B -- 4.5B effective params with native function calling,\n"
            "thinking mode, and vision. 128K context. Apache 2.0.\n"
            "Best all-round light pick -- small and fast."
        ),
        file_bytes=4977171584, projector_bytes=990372672, kv_bytes_per_token=172032,
        recurrent_bytes=0, block_count=42, sliding_window=512,
    ),
    ModelPreset(
        identifier="qwen35_9b_dsv4_q4",
        name="Qwen3.5-9B DeepSeek-V4-Flash (Q4_K_M)",
        repo_id="Jackrong/Qwen3.5-9B-DeepSeek-V4-Flash-GGUF",
        filename="Qwen3.5-9B-DeepSeek-V4-Flash-Q4_K_M.gguf",
        # Measured (#543, with the projector): 7,530 MiB at 32K with 16-bit KV;
        # the estimate from these numbers is 7,706.
        capability="Strong",
        category="lightweight",
        context_window=262144,
        max_tokens=12288,
        vision=True,
        mmproj_filename="mmproj.gguf",
        why="DeepSeek-V4 distilled reasoning -- best reasoning-per-GB in light tier",
        extra_server_args=_QWEN_EXTRA_SERVER_ARGS,
        description=(
            "Qwen3.5-9B fine-tuned with DeepSeek-V4 reasoning distillation.\n"
            "Vision-capable, 262K context. Apache 2.0.\n"
            "Punches well above its weight for tool calling and reasoning."
        ),
        file_bytes=5629105408, projector_bytes=921705376, kv_bytes_per_token=32768,
        recurrent_bytes=52690944, block_count=32,
    ),
    ModelPreset(
        identifier="qwen35_9b_q8",
        name="Qwen3.5-9B (Q8_0)",
        repo_id="unsloth/Qwen3.5-9B-GGUF",
        filename="Qwen3.5-9B-Q8_0.gguf",
        capability="Strong",
        category="lightweight",
        context_window=262144,
        max_tokens=12288,
        vision=True,
        mmproj_filename="mmproj-F16.gguf",
        why="Highest quality light quant -- Q8_0 precision, vision, 262K context",
        extra_server_args=_QWEN_EXTRA_SERVER_ARGS,
        description=(
            "Qwen3.5-9B at Q8_0 -- highest quality quantization for the light tier.\n"
            "Vision-capable, 262K context, thinking mode. Apache 2.0.\n"
            "Best quality-to-size ratio in the light tier."
        ),
        file_bytes=9527502048, projector_bytes=918166080, kv_bytes_per_token=32768,
        recurrent_bytes=52690944, block_count=32,
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
# Remote Provider Presets -- OpenRouter curated models

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
                description="Best all-around -- strong coding, tool use, and reasoning. 200K context.",
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
                description="MoE architecture -- efficient, strong coding. Native MCP support.",
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

    print("[Coworker] fetch_remote_models: GET {:s}".format(url))

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

            print("[Coworker] fetch_remote_models: {:d} models found".format(len(models)))
            return models, ""

    except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
        msg = "Failed to fetch models: {:s}".format(str(ex))
        print("[Coworker] fetch_remote_models: {:s}".format(msg))
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
        _config.local_kv_cache_quant = cfg.local_kv_cache_quant
        _config.local_server_verbose = cfg.local_server_verbose
        _config.local_parallel = cfg.local_parallel
        _config.lock_scene_while_working = cfg.lock_scene_while_working
        _config.auto_continue_rounds = cfg.auto_continue_rounds
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
            local_kv_cache_quant=_config.local_kv_cache_quant,
            local_server_verbose=_config.local_server_verbose,
            local_parallel=_config.local_parallel,
            lock_scene_while_working=_config.lock_scene_while_working,
            auto_continue_rounds=_config.auto_continue_rounds,
            remote_api_url=_config.remote_api_url,
            remote_api_key=_config.remote_api_key,
            remote_model=_config.remote_model,
        )


# The preferences that shape the llama-server command line (#543).  Every Start
# path copies them through :func:`apply_server_prefs`; a test fails when
# start_local_llama reads a config field that is neither here nor derived below.
SERVER_PREF_FIELDS: tuple[str, ...] = (
    "llama_source", "llama_path", "llama_backend", "model_repo_id", "model_filename",
    "downloaded_models_dir", "local_ctx_size", "local_kv_cache_quant",
    "local_server_verbose", "hf_token",
)
# Config fields start_local_llama reads that are not preferences: the port comes
# from the effective ports, the slot count from the chat preferences.
SERVER_DERIVED_FIELDS: tuple[str, ...] = ("local_port", "local_parallel")
# llama-server's unified KV slots when Local Side by Side is on (threads.LOCAL_SLOTS_MAX).
_LOCAL_SLOTS_MAX = 4


def local_server_slots(chats_at_once: int, local_side_by_side: bool) -> int:
    """``--parallel`` for the Local server: 1 while chats take turns (the default),
    else the chats that may run at once, at most 4 (the same rule as
    ``threads.slots_for("local", ...)``)."""
    if not local_side_by_side:
        return 1
    return max(1, min(int(chats_at_once or 1), _LOCAL_SLOTS_MAX))


def apply_server_prefs(cfg: LLMConfig, prefs: Any,
                       abspath: "Callable[[str], str] | None" = None) -> LLMConfig:
    """Copy the preferences that shape the server command line into *cfg*.

    *prefs* is the add-on preferences (or any object with the same attributes);
    *abspath* resolves a ``//`` path against the .blend (``bpy.path.abspath``).
    Returns *cfg*.  The caller still calls :func:`set_config`.
    """
    for name in SERVER_PREF_FIELDS:
        if not hasattr(prefs, name):
            continue          # an older preferences class still registered
        value = getattr(prefs, name)
        if name == "llama_source":
            value = str(value).lower()
        elif name == "llama_path" and abspath is not None:
            value = abspath(value) if value else value
        setattr(cfg, name, value)
    cfg.local_parallel = local_server_slots(
        int(getattr(prefs, "chats_at_once", 1) or 1), bool(getattr(prefs, "local_side_by_side", False)))
    return cfg


# ---------------------------------------------------------------------------
# llama-server detection

_find_llama_server_cache: str | None = None
_find_llama_server_checked: bool = False
_llama_custom_problem: str = ""     # why the custom path isn't usable, for the preferences


def llama_custom_problem() -> str:
    """Why the custom llama-server path didn't resolve ("" when it did, or isn't checked yet)."""
    return _llama_custom_problem


def resolve_llama_path(path: str) -> tuple[str | None, str]:
    """A user-given llama-server path -> ``(binary, problem)`` (#382).

    Takes the binary itself or the folder it is in (also a llama.cpp checkout's
    ``build/bin`` or ``bin``); ``~`` is expanded.  *problem* says why there is no
    binary: not found, no llama-server in that folder, or not executable.
    """
    names = ("llama-server.exe", "llama-server") if sys.platform == "win32" else ("llama-server",)
    p = os.path.expanduser((path or "").strip())
    if not p:
        return None, "no path set"
    if os.path.isdir(p):
        found = next((os.path.join(p, sub, name) for sub in ("", os.path.join("build", "bin"), "bin")
                      for name in names if os.path.isfile(os.path.join(p, sub, name))), None)
        if found is None:
            return None, "no {:s} in {:s}".format(names[-1], p)
        p = found
    if not os.path.isfile(p):
        return None, "file not found: {:s}".format(p)
    if sys.platform != "win32" and not os.access(p, os.X_OK):
        return None, "not executable -- run: chmod +x \"{:s}\"".format(p)
    return p, ""


def _bundled_llama_candidates(bundled_dir: Path, active_backend: str) -> list[Path]:
    """Where a downloaded llama-server lives, preferred first.

    Windows: ``llama-server-<backend>.exe`` then ``llama-server.exe``, with their DLLs
    beside them.  Linux / macOS: one folder per variant (``llama-vulkan/``,
    ``llama-cpu/``), because the binary loads the libraries next to it (#382).
    """
    if sys.platform == "win32":
        backend_bname = "llama-server-{b}.exe".format(b=active_backend or "cpu")
        return [bundled_dir / backend_bname, bundled_dir / "llama-server.exe"]
    variants = ["vulkan", "cpu"] if active_backend == "vulkan" else ["cpu"]
    return [bundled_dir / "llama-{:s}".format(v) / "llama-server" for v in variants]


def find_llama_server() -> str | None:
    """Search for ``llama-server`` binary.

    Prefers the Coworker-managed bundled binary (latest version) over
    PATH and known install locations.  Logs a warning when the found
    binary is older than _MIN_SUPPORTED_BUILD (too old for Qwen3 SSM).
    """
    global _find_llama_server_cache, _find_llama_server_checked, _llama_custom_problem
    if _find_llama_server_checked:
        return _find_llama_server_cache
    _find_llama_server_checked = True
    _llama_custom_problem = ""

    with _lock:
        active_backend = _config.llama_backend
        llama_source = _config.llama_source
        llama_path = _config.llama_path
    if active_backend == "auto":
        active_backend = _detect_gpu_backend()

    bundled_dir = _get_bundled_llama_dir()
    _log = "[Coworker] find_llama_server"

    # 0. Custom source -- the user explicitly provided their own binary.
    #    Only that path is used; we never fall through to PATH/bundled so
    #    the addon never tampers with a user-managed setup.
    if llama_source == "custom":
        custom, _llama_custom_problem = resolve_llama_path(llama_path)
        if custom:
            print("{:s}: custom path -> {:s}".format(_log, custom))
            _find_llama_server_cache = custom
            _check_llama_version(custom)
            return custom
        print("{:s}: custom source set but not usable -- {:s}".format(_log, _llama_custom_problem))
        _find_llama_server_cache = None
        return None

    # 1. Bundled directory FIRST -- this is the Coworker-managed version
    #    (downloaded via the "Download llama-server" button, always recent).
    # Check the backend-specific binary first, then the generic fallback, so
    # the correct GPU backend binary is used and its companion libraries
    # (cudart, ...) are found in the same directory.
    for bpath in _bundled_llama_candidates(bundled_dir, active_backend):
        if bpath.is_file():
            print("{:s}: found bundled {:s} -> {:s}".format(_log, bpath.name, str(bpath)))
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
            "[Coworker][WARN] WARNING: llama-server build {:d} is outdated "
            "(minimum {:d} for Qwen3 SSM models). "
            "Use 'Download llama-server' in preferences to get the latest version."
            .format(build, _MIN_SUPPORTED_BUILD)
        )


def invalidate_llama_server_cache() -> None:
    """Reset the ``find_llama_server`` cache so the next call re-searches.

    Call this after the user installs llama-server externally or changes
    the configured path, so the addon detects it without a Blender restart.
    """
    global _find_llama_server_cache, _find_llama_server_checked, _gpu_backend_cache
    print("[Coworker] invalidate_llama_server_cache: cache cleared")
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
                print("[Coworker] _get_models_dir: using custom dir {:s}".format(str(custom_path)))
                return custom_path
            print(
                "[Coworker] _get_models_dir: custom models dir exists but is not a directory: {:s}".format(
                    str(custom_path)
                )
            )
        else:
            try:
                custom_path.mkdir(parents=True, exist_ok=True)
                print("[Coworker] _get_models_dir: created custom dir {:s}".format(str(custom_path)))
                return custom_path
            except OSError as ex:
                print(
                    "[Coworker] _get_models_dir: failed to create custom dir {:s}: {:s}".format(
                        str(custom_path), str(ex)
                    )
                )
    # Default: <user_home>/bfa_coworker_models/
    default = Path.home() / "bfa_coworker_models"
    default.mkdir(parents=True, exist_ok=True)
    print("[Coworker] _get_models_dir: using default dir {:s}".format(str(default)))
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


# Cache for GPU backend detection -- nvidia-smi/wmic are expensive and
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
        # We don't auto-detect on macOS/Linux -- user can override manually.
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
            print("[Coworker] _detect_gpu_backend: NVIDIA GPU detected -> cuda")
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
        print("[Coworker] _detect_gpu_backend: AMD/Intel GPU detected -> vulkan")
        _gpu_backend_cache = "vulkan"
        return _gpu_backend_cache

    # 3. Fallback to CPU.
    print("[Coworker] _detect_gpu_backend: no compatible GPU detected -> cpu")
    _gpu_backend_cache = "cpu"
    return _gpu_backend_cache


def resolve_gpu_backend(backend: str) -> str:
    """Resolve a "auto" backend selector to a concrete backend name.

    Returns ``cuda``, ``vulkan``, or ``cpu`` -- the backend llama-server will
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

_TYPICAL_LAYERS = 33               # when a header has no block count
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

def _gguf_layer_count(model_path: "Path | str") -> int | None:
    """The model's transformer blocks (``<arch>.block_count``) from its GGUF
    header, read by :func:`gguf_info`; ``None`` when the file can't be read or
    has no such key.  Counts the MTP layers too: ``--n-gpu-layers`` does."""
    meta = (gguf_info(model_path) or {}).get("meta") or {}
    arch = meta.get("general.architecture")
    try:
        layers = int(meta.get("{:s}.block_count".format(str(arch))) or 0) if arch else 0
    except (TypeError, ValueError):
        layers = 0
    return layers if layers > 0 else None


def plan_gpu_layers(model_bytes: float, projector_bytes: float, profile: dict[str, float], n_layers: int,
                    free_mib: "int | None", ctx: int, kv_q8: bool, slots: int = 1) -> int:
    """``--n-gpu-layers`` from the numbers of the memory plan (#543, #558).

    Every layer when the whole server (:func:`estimate_server_vram_mib`) fits in
    *free_mib* minus the plan's margin, or when the free memory is unknown.
    Else as many layers as fit next to what stays on the GPU anyway (the
    projector, its compute buffer and the context scratch), each layer taking
    its share of the weights, the KV cache and the recurrent state.  The weight
    share is the whole file over the layers: the embeddings and output head
    stay on the CPU in a partial offload, so this errs towards fewer layers.
    """
    if free_mib is None:
        return _FULL_OFFLOAD
    avail = int(free_mib) - _VRAM_MARGIN_MIB
    full = estimate_server_vram_mib(model_bytes, projector_bytes, profile, ctx, kv_q8, slots)
    if full <= avail:
        return _FULL_OFFLOAD
    layers = max(int(n_layers or 0), 1)
    fixed = int(ctx) * _KV_SCRATCH_BYTES_PER_TOKEN
    if projector_bytes:
        fixed += float(projector_bytes) + _PROJECTOR_COMPUTE_MIB * _MIB
    fixed_mib = fixed / _MIB
    per_layer = (full - fixed_mib) / layers
    if per_layer <= 0:
        return _FULL_OFFLOAD
    return max(0, min(layers, int((avail - fixed_mib) // per_layer)))


def autodetect_gpu_layers(model_path: Path, context_size: int, *, free_mib: "int | None" = None,
                          projector_bytes: float = 0.0, kv_q8: bool = False, slots: int = 1) -> int:
    """``--n-gpu-layers`` for *model_path* on this machine: :func:`plan_gpu_layers`
    with the file's own header against the free VRAM (*free_mib*, else read
    now; with only the card's total known, the total minus the desktop's share,
    as :func:`_plan_memory_budget` does)."""
    backend = _detect_gpu_backend()
    if backend == "cpu":
        return 0
    if free_mib is None:
        free_mib = detect_free_vram_mib()
    if free_mib is None:
        _, _, gpu_mb = _hardware_info()
        if gpu_mb is None:
            return _FULL_OFFLOAD  # Cannot detect -- try full offload
        free_mib = gpu_mb - _DESKTOP_VRAM_MIB
    info = gguf_info(model_path)
    try:
        model_bytes = float(info["file_bytes"]) if info else float(os.path.getsize(str(model_path)))
    except OSError:
        return _FULL_OFFLOAD
    layers = _gguf_layer_count(model_path) or _TYPICAL_LAYERS
    ngl = plan_gpu_layers(model_bytes, projector_bytes, model_memory_profile(info), layers, free_mib,
                          context_size, kv_q8, slots)
    print("[Coworker] autodetect_gpu_layers: ngl={:d}/{:d} (free={:d} MiB, model={:d} MiB, ctx={:d}, {:s} KV)".format(
        ngl, layers, int(free_mib), int(model_bytes // _MIB), int(context_size), "q8_0" if kv_q8 else "f16"))
    return ngl


# ---------------------------------------------------------------------------
# Local server memory plan (#543)
#
# What llama-server needs on the GPU, from the model's own GGUF header, against
# what is free on the card when it starts.  Calibrated on an RTX 4090 with the
# pinned build (b10154), one slot, dedicated MiB measured / estimated here:
#   Qwen3.8-27B Q4_K_M + F16 projector  32K f16 KV 18,896 / 19,171
#                                       32K q8_0   18,022 / 18,211
#                                       16K f16    17,856 / 18,083
#                                       64K q8_0   19,270 / 19,427
#   the same without the projector      32K f16    17,760 / 18,030
#   Qwen3.5-9B Q4_K_M + projector       32K f16     7,530 /  7,705
#                                       64K f16     8,586 /  8,857
# Each extra slot adds one recurrent state (150 MiB on the 27B: 4 slots measured
# +450 MiB).  A load spilled into shared memory (prefill ~5 tokens/s, the chat
# "hangs") when its need came within ~1 GB of the card's free memory: hence the
# margin, the same as llama.cpp's own --fit-target default.

_FIRST_REQUEST_TOKENS = 18432       # the first request on Bforartists: 18,359 tokens (#543; #544 trims it)
_REPLY_ROOM_TOKENS = 4096           # a reply on top of it (--no-context-shift: both must fit)
_VRAM_MARGIN_MIB = 1024
_DESKTOP_VRAM_MIB = 1024            # the desktop and Blender, when only the card's total is known
_PROJECTOR_COMPUTE_MIB = 256       # the vision encoder's compute buffer on top of the projector file
_KV_SCRATCH_BYTES_PER_TOKEN = 4096  # attention scratch that grows with the context
_FALLBACK_KV_BYTES_PER_TOKEN = 128 * 1024  # f16 KV per token when the header can't be read (no file yet)
_FALLBACK_PROJECTOR_BYTES = 1024 ** 3      # a vision preset's projector before it is downloaded
_AUTO_CTX_MAX = 65536               # a recommendation never asks for more (Blender needs VRAM too)
_Q8_0_BYTES_PER_ELEM = 34.0 / 32.0  # q8_0: 32 values in 34 bytes (f16: 2 bytes each)
# --cache-ram: llama-server's host prompt cache (server default 8192 MiB).  With
# one slot the cache only fills when a prompt shares little with the slot: two
# chats switching reused the same tokens at 2048 as at 8192, but peak commit
# over eight chats was 23.0 GB instead of 25.2 GB.
LOCAL_CACHE_RAM_MIB = 2048
_MIB = 1024 * 1024

# ggml tensor types: (values per block, bytes per block).  An unknown type makes
# the truncation check skip the file rather than guess.
_GGML_TYPE_SIZES: dict[int, tuple[int, int]] = {
    0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20), 6: (32, 22), 7: (32, 24), 8: (32, 34),
    9: (32, 36), 10: (256, 84), 11: (256, 110), 12: (256, 144), 13: (256, 176), 14: (256, 210),
    15: (256, 292), 16: (256, 66), 17: (256, 74), 18: (256, 98), 19: (256, 50), 20: (32, 18),
    21: (256, 110), 22: (256, 82), 23: (256, 136), 24: (1, 1), 25: (1, 2), 26: (1, 4),
    27: (1, 8), 28: (1, 8), 29: (256, 56), 30: (1, 2), 34: (256, 54), 35: (256, 66), 39: (32, 17),
}
# GGUF metadata value types -> struct format.
_GGUF_SCALARS: dict[int, str] = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f",
                                 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_GGUF_STRING, _GGUF_ARRAY = 8, 9

_gguf_info_cache: dict[tuple[str, int, int], "dict[str, Any] | None"] = {}


def gguf_info(path: "Path | str | None") -> "dict[str, Any] | None":
    """Facts from a GGUF header: ``meta`` (scalars and short numeric arrays),
    ``data_end`` (the byte where the last tensor ends; 0 when a tensor type is
    unknown) and ``file_bytes``.  ``None`` when *path* is not a readable GGUF.
    Cached per path, size and mtime (the preferences draw this)."""
    if not path:
        return None
    try:
        st = os.stat(str(path))
    except OSError:
        return None
    key = (str(path), int(st.st_size), int(st.st_mtime_ns))
    if key in _gguf_info_cache:
        return _gguf_info_cache[key]
    info = None
    try:
        info = _read_gguf(str(path), int(st.st_size))
    except (OSError, struct.error, ValueError, UnicodeDecodeError, OverflowError, MemoryError):
        info = None
    _gguf_info_cache[key] = info
    return info


def _read_gguf(path: str, file_bytes: int) -> "dict[str, Any] | None":
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            return None
        version, = struct.unpack("<I", f.read(4))
        if version < 2:
            return None
        n_tensors, n_kv = struct.unpack("<QQ", f.read(16))

        def rstr() -> str:
            n, = struct.unpack("<Q", f.read(8))
            return f.read(n).decode("utf-8", errors="replace")

        def skip_or_read(vtype: int, keep: bool) -> Any:
            if vtype in _GGUF_SCALARS:
                fmt = _GGUF_SCALARS[vtype]
                return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]
            if vtype == _GGUF_STRING:
                n, = struct.unpack("<Q", f.read(8))
                if keep and n <= 4096:
                    return f.read(n).decode("utf-8", errors="replace")
                f.seek(n, 1)
                return None
            if vtype == _GGUF_ARRAY:
                atype, count = struct.unpack("<IQ", f.read(12))
                if atype in _GGUF_SCALARS:
                    fmt = _GGUF_SCALARS[atype]
                    size = struct.calcsize(fmt)
                    if keep and count <= 1024:
                        return list(struct.unpack("<{:d}{:s}".format(count, fmt[1]), f.read(size * count)))
                    f.seek(size * count, 1)
                    return None
                for _ in range(count):
                    skip_or_read(atype, False)
                return None
            raise ValueError("unknown GGUF value type {:d}".format(vtype))

        meta: dict[str, Any] = {}
        for _ in range(n_kv):
            k = rstr()
            vtype, = struct.unpack("<I", f.read(4))
            v = skip_or_read(vtype, not k.startswith("tokenizer."))
            if v is not None:
                meta[k] = v
        end = 0
        known = True
        for _ in range(n_tensors):
            rstr()
            n_dims, = struct.unpack("<I", f.read(4))
            dims = struct.unpack("<{:d}Q".format(n_dims), f.read(8 * n_dims))
            ttype, offset = struct.unpack("<IQ", f.read(12))
            sizes = _GGML_TYPE_SIZES.get(ttype)
            if sizes is None:
                known = False
                continue
            n = 1
            for d in dims:
                n *= int(d)
            end = max(end, offset + (n // sizes[0]) * sizes[1])
        align = int(meta.get("general.alignment") or 32)
        data_start = (f.tell() + align - 1) // align * align
    return {"meta": meta, "data_end": data_start + end if known else 0, "file_bytes": file_bytes}


def model_memory_profile(info: "dict[str, Any] | None") -> dict[str, float]:
    """Per-token KV bytes (f16) and per-sequence recurrent-state bytes from a
    :func:`gguf_info` header; conservative fallbacks without one.

    Attention layers hold the KV cache; on hybrid models (Qwen3.5/3.8:
    ``full_attention_interval``) the others hold a fixed recurrent state per
    server slot instead.  Sliding-window layers are counted as full (upper bound).
    """
    fallback = {"kv_bytes_per_token": float(_FALLBACK_KV_BYTES_PER_TOKEN), "recurrent_bytes": 0.0,
                "from_header": 0.0}
    meta = (info or {}).get("meta") or {}
    arch = str(meta.get("general.architecture") or "")
    if not arch:
        return fallback

    def m(key: str, default: Any = None) -> Any:
        return meta.get("{:s}.{:s}".format(arch, key), default)

    n_layer = int(m("block_count", 0) or 0) - int(m("nextn_predict_layers", 0) or 0)
    n_head = m("attention.head_count", 0)
    n_head_kv = m("attention.head_count_kv", n_head)
    n_embd = int(m("embedding_length", 0) or 0)
    if n_layer <= 0 or not n_head_kv:
        return fallback
    heads_first = n_head[0] if isinstance(n_head, list) and n_head else n_head
    head_dim = n_embd // int(heads_first) if heads_first else 0
    k_len = int(m("attention.key_length", head_dim) or head_dim)
    v_len = int(m("attention.value_length", k_len) or k_len)
    interval = int(m("full_attention_interval", 0) or 0)
    if isinstance(n_head_kv, list):
        kv_heads = [int(x) for x in n_head_kv[:n_layer]]
    else:
        kv_heads = [int(n_head_kv)] * n_layer
    if interval > 0:
        attn = [il for il in range(n_layer) if (il + 1) % interval == 0]
    else:
        attn = [il for il in range(n_layer) if il < len(kv_heads) and kv_heads[il] > 0]
    kv_elems = sum(kv_heads[il] for il in attn if il < len(kv_heads)) * (k_len + v_len)
    recurrent = 0.0
    d_state = int(m("ssm.state_size", 0) or 0)
    if d_state:
        d_inner = int(m("ssm.inner_size", 0) or 0)
        d_conv = int(m("ssm.conv_kernel", 0) or 0)
        n_group = int(m("ssm.group_count", 0) or 0)
        n_rec = n_layer - len(attn) if interval > 0 else n_layer
        per_layer = (max(d_conv - 1, 0) * (d_inner + 2 * n_group * d_state) + d_inner * d_state) * 4
        recurrent = float(n_rec * per_layer)
    return {"kv_bytes_per_token": float(kv_elems * 2), "recurrent_bytes": recurrent, "from_header": 1.0}


def estimate_server_vram_mib(model_bytes: float, projector_bytes: float, profile: dict[str, float],
                             ctx: int, kv_q8: bool, slots: int = 1) -> int:
    """Dedicated VRAM (MiB) llama-server needs with every layer on the GPU."""
    kv = float(profile.get("kv_bytes_per_token") or _FALLBACK_KV_BYTES_PER_TOKEN)
    if kv_q8:
        kv *= _Q8_0_BYTES_PER_ELEM / 2.0
    total = float(model_bytes) + max(int(slots), 1) * float(profile.get("recurrent_bytes") or 0.0)
    if projector_bytes:
        total += float(projector_bytes) + _PROJECTOR_COMPUTE_MIB * _MIB
    total += int(ctx) * (kv + _KV_SCRATCH_BYTES_PER_TOKEN)
    return int(-(-total // _MIB))


def min_agent_ctx() -> int:
    """The smallest standard context that holds the first request plus a reply."""
    need = _FIRST_REQUEST_TOKENS + _REPLY_ROOM_TOKENS
    return next((s for s in ctx_preset_sizes if s >= need), ctx_preset_sizes[-1])


@dataclass
class ServerMemoryPlan:
    """What a Local server start will use, and whether it fits the free VRAM."""

    ctx: int
    kv_q8: bool
    need_mib: int
    free_mib: int | None        # what the plan was made against (None: unknown)
    fits: bool | None           # None when the free memory is unknown


def plan_server_memory(model_bytes: float, projector_bytes: float, profile: dict[str, float],
                       free_mib: "int | None", *, ctx: int = 0, kv_q8_forced: bool = False,
                       slots: int = 1, ctx_cap: int = 262144) -> ServerMemoryPlan:
    """The sizing rule (#543).

    *ctx* > 0 (a launch): keep that context; f16 KV when it fits in
    ``free - 1 GB``, else q8_0.  *ctx* == 0 (a recommendation): the largest
    standard size from :func:`min_agent_ctx` up to 64K (and the model's
    *ctx_cap*) that fits with f16 KV; else the largest that fits with q8_0;
    else the smallest one with q8_0, marked as not fitting.  *kv_q8_forced*
    (the preference) skips f16.
    """
    avail = None if free_mib is None else int(free_mib) - _VRAM_MARGIN_MIB

    def need(c: int, q8: bool) -> int:
        return estimate_server_vram_mib(model_bytes, projector_bytes, profile, c, q8, slots)

    if ctx > 0:
        q8 = bool(kv_q8_forced) or (avail is not None and need(ctx, False) > avail)
        fits = None if avail is None else need(ctx, q8) <= avail
        return ServerMemoryPlan(int(ctx), q8, need(ctx, q8), free_mib, fits)
    cap = max(int(ctx_cap or 0), ctx_preset_sizes[0])
    floor = min(min_agent_ctx(), cap)
    top = min(_AUTO_CTX_MAX, cap)
    sizes = [s for s in ctx_preset_sizes if floor <= s <= top] or [floor]
    if avail is None:
        return ServerMemoryPlan(sizes[0], bool(kv_q8_forced), need(sizes[0], bool(kv_q8_forced)), None, None)
    for q8 in ((True,) if kv_q8_forced else (False, True)):
        fitting = [s for s in sizes if need(s, q8) <= avail]
        if fitting:
            return ServerMemoryPlan(fitting[-1], q8, need(fitting[-1], q8), free_mib, True)
    return ServerMemoryPlan(sizes[0], True, need(sizes[0], True), free_mib, False)


# ---------------------------------------------------------------------------
# Preset memory texts (#558): built from the header numbers in PRESET_MODELS
# through the estimate above, so they can't drift from what a start plans.

# Card sizes a text may name.  A card counts as fitting when the estimate plus
# the plan's margin and the desktop's share (what _plan_memory_budget assumes
# with only the total known) is within it.
_PRESET_CARD_GB: tuple[int, ...] = (4, 6, 8, 10, 12, 16, 20, 24, 32, 48, 64, 80, 96)
_PRESET_TIER_LABELS = {"flagship": "Flagship", "mid_range": "Mid", "lightweight": "Light"}
# The Recommended Model list's order: its items are 3-tuples, so a saved
# preference keeps the index -- never reorder, add new ids at the end.
_PRESET_ENUM_ORDER: tuple[str, ...] = (
    "gpt_oss_20b_q4", "qwen38_27b_q4", "fable_fusion_27b_iq4",
    "qwen38_27b_q8", "fable_fusion_27b_q6", "nail_35b_q4",
    "gemma4_e4b_q4", "qwen35_9b_dsv4_q4", "qwen35_9b_q8",
)


def _gb_up(mib: float) -> str:
    """*mib* in GB (1024 MiB), one decimal, rounded up: a text never understates."""
    tenths = int(-((-float(mib) * 10) // 1024))
    return "{:d}.{:d}".format(tenths // 10, tenths % 10)


def preset_profile(preset: "ModelPreset | None") -> "dict[str, float] | None":
    """*preset*'s header numbers as a :func:`model_memory_profile`; ``None`` without them."""
    if preset is None or not preset.file_bytes or not preset.kv_bytes_per_token:
        return None
    return {"kv_bytes_per_token": float(preset.kv_bytes_per_token),
            "recurrent_bytes": float(preset.recurrent_bytes), "from_header": 1.0}


def preset_vram_mib(preset: "ModelPreset", kv_q8: bool, ctx: int = 0, slots: int = 1) -> int:
    """:func:`estimate_server_vram_mib` for *preset* from its header numbers
    (0 when it has none); *ctx* 0 is :func:`min_agent_ctx`."""
    profile = preset_profile(preset)
    if profile is None:
        return 0
    return estimate_server_vram_mib(preset.file_bytes, preset.projector_bytes, profile,
                                    ctx or min_agent_ctx(), kv_q8, slots)


def _smallest_card_gb(need_mib: int) -> "int | None":
    reserve = _VRAM_MARGIN_MIB + _DESKTOP_VRAM_MIB
    return next((gb for gb in _PRESET_CARD_GB if need_mib + reserve <= gb * 1024), None)


def preset_memory_note(preset: "ModelPreset") -> tuple[str, str]:
    """``(vram line, card note)`` for *preset*, both from its header numbers.

    At :func:`min_agent_ctx` (32K), one slot, every layer on the GPU, with the
    projector; the 8-bit and 16-bit KV cache figures, rounded up.  A card size
    is named only when the estimate plus the plan's 1 GB margin and ~1 GB for
    the desktop fits in it.  With sliding-window layers the estimate counts
    them in full, and the text says "at most".  ``("", "")`` without numbers.
    """
    q8 = preset_vram_mib(preset, True)
    f16 = preset_vram_mib(preset, False)
    if not q8:
        return "", ""
    ctx = ctx_preset_label(min_agent_ctx())
    upto = "at most " if preset.sliding_window else ""
    extras = ["every layer on the GPU"]
    if preset.projector_bytes:
        extras.append("vision projector included")
    if preset.sliding_window:
        extras.append("sliding-window layers counted in full")
    vram = "VRAM at {:s} context: {:s}~{:s} GB with 8-bit KV, ~{:s} GB with 16-bit ({:s})".format(
        ctx, upto, _gb_up(q8), _gb_up(f16), ", ".join(extras))
    span = "{:s}~{:s}-{:s} GB VRAM at {:s}".format(upto, _gb_up(q8), _gb_up(f16), ctx)
    c8, c16 = _smallest_card_gb(q8), _smallest_card_gb(f16)
    if c8 is None:
        card = "More than {:d} GB of VRAM".format(_PRESET_CARD_GB[-1])
    elif c16 == c8:
        card = "{:d} GB+ card".format(c8)
    elif c16 is None:
        card = "{:d} GB+ card with 8-bit KV".format(c8)
    else:
        card = "{:d} GB+ card with 8-bit KV, {:d} GB+ with 16-bit".format(c8, c16)
    return vram, "{:s} -- {:s}".format(card, span)


def preset_info_text(preset: "ModelPreset") -> str:
    """The Model Preset Information box, one line per row."""
    lines = ["Capability: {:s}  |  Download: {:s}".format(preset.capability, preset.disk_gb or "unknown")]
    vram, card = preset_memory_note(preset)
    if vram:
        lines += [vram, "Card: {:s} (keeps 1 GB free, ~1 GB for the desktop)".format(card)]
    lines += ["Why: {:s}".format(preset.why), preset.description]
    return "\n".join(lines)


def preset_tooltip(preset: "ModelPreset") -> str:
    """The Recommended Model list's tooltip for *preset*."""
    tier = _PRESET_TIER_LABELS.get(preset.category, preset.category)
    memory = "; ".join(t for t in (preset.hardware_note, preset.disk_gb and preset.disk_gb + " download") if t)
    return "[{:s}] {:s} -- {:s}".format(tier, memory, preset.why) if memory else "[{:s}] {:s}".format(
        tier, preset.why)


def preset_enum_items() -> list[tuple[str, str, str]]:
    """Items for the ``model_preset`` preference (``shared.MODEL_PRESET_ITEMS``),
    built from :data:`PRESET_MODELS` in :data:`_PRESET_ENUM_ORDER`."""
    by_id = {p.identifier: p for p in PRESET_MODELS}
    order = list(_PRESET_ENUM_ORDER) + [p.identifier for p in PRESET_MODELS if p.identifier not in _PRESET_ENUM_ORDER]
    items = [("_custom", "Custom (manual entry)", "Manually specify repo ID and filename")]
    for ident in order:
        if ident in by_id:
            items.append((ident, by_id[ident].name, preset_tooltip(by_id[ident])))
    return items


def detect_free_vram_mib() -> "int | None":
    """Free memory (MiB) of the first NVIDIA GPU now, or ``None``."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip().splitlines()[0].strip()))
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


_free_vram_cache: dict[str, Any] = {}
# Free VRAM measured just before this window's server started: while it runs,
# the card's free memory no longer includes what the server itself took.
_launch_free_vram_mib: "int | None" = None


def _detect_free_vram_cached() -> "int | None":
    """:func:`detect_free_vram_mib`, cached briefly for UI redraws; the value
    from the last launch while this window's server runs."""
    import time as _time
    if _launch_free_vram_mib is not None and _llama_process is not None and _llama_process.poll() is None:
        return _launch_free_vram_mib
    now = _time.monotonic()
    if _free_vram_cache and now - float(_free_vram_cache.get("t", 0.0)) < _HARDWARE_CACHE_TTL:
        return _free_vram_cache.get("v")
    value = detect_free_vram_mib()
    _free_vram_cache.clear()
    _free_vram_cache.update(t=now, v=value)
    return value


def _plan_memory_budget(backend: str, ram_gb: "float | None", vram_gb: "float | None",
                        free_vram_mib: "int | None") -> "tuple[int | None, bool]":
    """``(free MiB to plan against, on the GPU?)`` for *backend*."""
    if resolve_gpu_backend(backend) in ("cuda", "vulkan"):
        if free_vram_mib is None:
            free_vram_mib = _detect_free_vram_cached()
        if free_vram_mib is None and vram_gb:
            free_vram_mib = int(vram_gb * 1024) - _DESKTOP_VRAM_MIB    # total only: leave the desktop ~1 GB
        return free_vram_mib, True
    if ram_gb:
        return int(ram_gb * 1024) - 2048, False           # weights in RAM; 2 GB for the OS + Blender
    return None, False


_projector_bytes_cache: dict[str, tuple[float, float]] = {}


def _projector_bytes(model_path: str) -> float:
    """Size of the projector the server would load next to *model_path* (0: none);
    cached briefly -- the preferences draw this."""
    import time as _time
    now = _time.monotonic()
    hit = _projector_bytes_cache.get(model_path)
    if hit is not None and now - hit[0] < _HARDWARE_CACHE_TTL:
        return hit[1]
    projector = _resolve_mmproj_path(model_path)
    try:
        size = float(os.path.getsize(str(projector))) if projector else 0.0
    except OSError:
        size = 0.0
    _projector_bytes_cache[model_path] = (now, size)
    return size


def recommend_server_plan(
    model_gb: float = 0.0,
    backend: str = "auto",
    ram_gb: "float | None" = None,
    vram_gb: "float | None" = None,
    *,
    model_path: "Path | str | None" = None,
    projector_bytes: "float | None" = None,
    free_vram_mib: "int | None" = None,
    kv_q8_forced: bool = False,
    ctx_cap: int = 262144,
    ctx: int = 0,
    profile: "dict[str, float] | None" = None,
) -> ServerMemoryPlan:
    """:func:`plan_server_memory` for this machine and model.  *model_path*
    (when the file exists) gives the real sizes and header; else *model_gb*,
    *projector_bytes* and *profile* (a preset's header numbers, #558; the
    fallback profile without one) estimate them."""
    if ram_gb is None or vram_gb is None:
        _ram, _vram = _detect_hardware_cached()
        ram_gb = _ram if ram_gb is None else ram_gb
        vram_gb = _vram if vram_gb is None else vram_gb
    info = gguf_info(model_path) if model_path else None
    model_bytes = float(info["file_bytes"]) if info else float(model_gb) * 1024 ** 3
    if info is not None and projector_bytes is None:
        projector_bytes = _projector_bytes(str(model_path))
    free, _gpu = _plan_memory_budget(backend, ram_gb, vram_gb, free_vram_mib)
    if info is not None or not profile:
        profile = model_memory_profile(info)
    return plan_server_memory(model_bytes, float(projector_bytes or 0.0), profile, free,
                              ctx=ctx, kv_q8_forced=kv_q8_forced, ctx_cap=ctx_cap)


def recommend_context_size(
    model_gb: float = 0.0,
    backend: str = "auto",
    ram_gb: float | None = None,
    vram_gb: float | None = None,
    **kwargs: Any,
) -> int:
    """Recommend a context size (tokens) for this machine: see :func:`plan_server_memory`.

    Free VRAM (not the card's total), the model's own KV and recurrent-state
    needs, and never less than the first request plus a reply.  Returns one of
    :data:`ctx_preset_sizes`.
    """
    return recommend_server_plan(model_gb, backend, ram_gb, vram_gb, **kwargs).ctx


def _gb(mib: float) -> str:
    return "{:.1f}".format(mib / 1024.0)


def hardware_context_hint(model_gb: float = 0.0, backend: str = "auto", **kwargs: Any) -> str:
    """One-line hint for the preferences UI: the size recommended now, and why."""
    ram, vram = _detect_hardware_cached()
    plan = recommend_server_plan(model_gb, backend, ram, vram, **kwargs)
    kv = "8-bit" if plan.kv_q8 else "16-bit"
    gpu = resolve_gpu_backend(backend) in ("cuda", "vulkan")
    where = "VRAM" if gpu else "RAM"
    first = "the first request is ~{:d}K tokens".format(_FIRST_REQUEST_TOKENS // 1024)
    label = ctx_preset_label(plan.ctx)
    if plan.free_mib is None:
        return "Recommended: {:s} ({:s}; free memory unknown).".format(label, first)
    if plan.fits:
        return "Recommended now: {:s} with {:s} KV cache -- ~{:s} of {:s} GB free {:s} ({:s}).".format(
            label, kv, _gb(plan.need_mib), _gb(plan.free_mib), where, first)
    return ("Recommended: {:s} with 8-bit KV cache ({:s}), but it needs ~{:s} GB {:s} and only {:s} GB "
            "is free now -- close other GPU apps first.").format(
                label, first, _gb(plan.need_mib), where, _gb(plan.free_mib))


def validate_ctx_against_hardware(
    model_gb: float,
    backend: str,
    ctx: int,
    **kwargs: Any,
) -> str | None:
    """Return a warning string if *ctx* looks wrong for this model and machine.

    Pure recommendation check (D4: startup-only validation -- never restarts
    or changes the running server).  Warns when *ctx* cannot hold the first
    request, or when the server would not fit the free VRAM (GPU backends) or
    RAM (CPU backend) even with an 8-bit KV cache.  ``None`` when it looks safe.
    """
    if ctx <= 0 or model_gb <= 0:
        return None
    if ctx < _FIRST_REQUEST_TOKENS + _REPLY_ROOM_TOKENS:
        return ("Context size {:,} is smaller than the first request (~{:,} tokens with the tools and "
                "a reply) -- the first message fails. Use {:s} or more.").format(
                    ctx, _FIRST_REQUEST_TOKENS + _REPLY_ROOM_TOKENS, ctx_preset_label(min_agent_ctx()))
    ram_gb, vram_gb = _detect_hardware_cached()
    gpu = resolve_gpu_backend(backend) in ("cuda", "vulkan")
    if gpu and not vram_gb:
        return None  # No VRAM data -- do not guess.
    if not gpu and not ram_gb:
        return None
    plan = recommend_server_plan(model_gb, backend, ram_gb, vram_gb, ctx=ctx, **kwargs)
    if plan.fits is not False:
        return None
    if gpu:
        return ("Context size {:,} needs ~{:s} GB of VRAM (model, projector and {:s} KV cache) but only "
                "~{:s} GB is free now -- lower Context Size or close other GPU apps; otherwise only part of "
                "the model goes on the GPU and the rest runs on the CPU, much slower.").format(
                    ctx, _gb(plan.need_mib), "8-bit" if plan.kv_q8 else "16-bit", _gb(plan.free_mib or 0))
    return ("Context size {:,} needs ~{:s} GB of RAM for the model and KV cache but only ~{:s} GB is "
            "left -- lower Context Size to avoid swapping or an out-of-memory crash.").format(
                ctx, _gb(plan.need_mib), _gb(plan.free_mib or 0))


# Cached runtime context size, invalidated per llama-server launch.
_runtime_ctx_cache: int | None = None
_runtime_ctx_logged_mismatch = False


def get_runtime_ctx(port: int | None = None) -> int | None:
    """Return the context size actually applied by the running llama-server.

    Queries ``http://127.0.0.1:<port>/props`` and reads the applied
    ``n_ctx`` (which may differ from the configured value if the model or
    build clamped it). Returns ``None`` when the server is not reachable or
    does not expose the field -- callers then fall back to the configured
    size. Results are cached per launch (the cache is cleared in
    :func:`start_local_llama`) and mismatches between configured and applied
    values are logged once.
    """
    global _runtime_ctx_cache, _runtime_ctx_logged_mismatch
    if port is None:
        with _lock:
            port = _config.local_port
    if port <= 0:
        return None
    if _runtime_ctx_cache is not None:
        return _runtime_ctx_cache
    url = "http://127.0.0.1:{:d}/props".format(port)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError) as ex:
        print("[Coworker] get_runtime_ctx: /props unreachable -- {:s}".format(str(ex)))
        return None
    n_ctx = body.get("default_generation_settings", {}).get("n_ctx")
    if n_ctx is None:
        n_ctx = body.get("n_ctx")
    if not isinstance(n_ctx, int) or n_ctx <= 0:
        print("[Coworker] get_runtime_ctx: /props returned no usable n_ctx")
        return None
    _runtime_ctx_cache = n_ctx
    with _lock:
        configured = _config.local_ctx_size or 0
    if configured and n_ctx != configured and not _runtime_ctx_logged_mismatch:
        _runtime_ctx_logged_mismatch = True
        print(
            "[Coworker] get_runtime_ctx: applied n_ctx ({:d}) differs from "
            "configured local_ctx_size ({:d}) -- using the server's value for "
            "prompt budgeting".format(n_ctx, configured))
    return n_ctx


# Whether the running llama-server takes images (#487), per launch like the context size.
_runtime_vision_cache: bool | None = None


def get_runtime_vision(port: int | None = None) -> bool:
    """True when the running llama-server takes images (a vision projector is loaded).

    ``/props`` reports it as ``modalities.vision``.  An older build without that
    field: the projector Coworker attached at launch (``--mmproj``) decides.
    Unknown (the server isn't reachable) counts as no, uncached: a picture sent
    to a server without a projector is a refused request, a note never is.
    """
    global _runtime_vision_cache
    if _runtime_vision_cache is not None:
        return _runtime_vision_cache
    if port is None:
        with _lock:
            port = _config.local_port
    if not port or port <= 0:
        return False
    url = "http://127.0.0.1:{:d}/props".format(port)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError) as ex:
        print("[Coworker] get_runtime_vision: /props unreachable -- {:s}".format(str(ex)))
        return False
    modalities = body.get("modalities") if isinstance(body, dict) else None
    if isinstance(modalities, dict) and "vision" in modalities:
        seen = bool(modalities.get("vision"))
    else:
        seen = _last_launched_model_path is not None and _resolve_mmproj_path(_last_launched_model_path) is not None
    _runtime_vision_cache = seen
    print("[Coworker] get_runtime_vision: the local model {:s} images".format("takes" if seen else "doesn't take"))
    return seen


def cancel_download() -> None:
    """Request cancellation of an in-progress model download.

    The download thread checks the cancel event between chunks and aborts,
    deleting the partial file. Safe to call even if no download is active.
    """
    print("[Coworker] cancel_download: cancellation requested")
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
        print("[Coworker] _check_disk_space: could not query disk usage -- {:s}".format(str(ex)))
        return True  # Can't determine -- let the download try anyway.
    # Require the file size plus a 5% safety margin.
    needed = int(required_bytes * 1.05)
    if usage.free < needed:
        msg = (
            "Not enough disk space: need {:s} but only {:s} free on {:s}"
        ).format(_format_bytes(needed), _format_bytes(usage.free), str(dest.parent))
        print("[Coworker] _check_disk_space: {:s}".format(msg))
        _set_error(msg)
        return False
    print("[Coworker] _check_disk_space: OK -- {:s} free, need {:s}".format(
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
    print("[Coworker] _get_hf_file_size: checking {:s}".format(url))
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            size_str = resp.headers.get("Content-Length")
            if size_str:
                size = int(size_str)
                print("[Coworker] _get_hf_file_size: size = {:d} bytes ({:s})".format(
                    size, _format_bytes(size)))
                return size
    except (urllib.error.URLError, OSError, ValueError) as ex:
        print("[Coworker] _get_hf_file_size: failed -- {:s}".format(str(ex)))
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
    print("[Coworker] download_model: repo_id={:s}, filename={:s}".format(r, f))
    if not r or not f:
        print("[Coworker] download_model: repo ID or filename not configured")
        _set_error("Model repo ID and filename must be configured")
        return None

    models_dir = _get_models_dir()
    dest = models_dir / f
    print("[Coworker] download_model: dest = {:s}".format(str(dest)))

    # Clear stale state before starting.
    _clear_download_state()
    _set_download_kind("model")
    # The user (or the confirmation dialog) has committed to the download --
    # drop the "download required" prompt state.
    clear_pending_model_download()
    _download_cancel_event.clear()

    # Check if already downloaded.
    if dest.exists():
        print("[Coworker] download_model: already exists, skipping download")
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
            # -- Primary: direct HTTP download ------------------------
            success = _download_gguf_direct(r, f, dest, progress_callback)
            if success:
                # Download succeeded -- report and done.
                _set_download_progress("Download complete: {:s}".format(f))
                if progress_callback:
                    progress_callback("Model downloaded to {:s}".format(str(dest)))
                # Also download the mmproj file if the preset has one.
                _download_mmproj_if_needed(r, f, models_dir, progress_callback)
                return

            # Cancelled -- don't fall through to the fallback path.
            if _download_cancel_event.is_set():
                return

            # -- Fallback: llama-server --hf-repo/--hf-file ----------
            # If direct download failed for a non-auth reason (network
            # restrictions, proxy issues), try the server's built-in downloader.
            error_state = get_state().error or ""
            if "401" in error_state or "403" in error_state or "404" in error_state:
                # Auth/gating/not-found -- don't retry, just surface the error.
                return

            print("[Coworker] download_model: direct download failed, falling back to llama-server --hf-repo")
            _set_download_progress("Downloading via llama-server...")
            if progress_callback:
                progress_callback("Trying alternate download method...")

            # Clear the temporary error from the direct attempt before trying the fallback.
            with _lock:
                _state.error = ""

            proc = start_local_llama(port=server_port, allow_download=True)
            if proc is None:
                error = get_state().error or "llama-server failed to start"
                _set_error(error)
                return

            # Poll health until server is ready (download finished).
            deadline = time.time() + 900  # 15 minute timeout for fallback
            poll_interval = 2.0
            while time.time() < deadline:
                if _download_cancel_event.is_set():
                    print("[Coworker] download_model: fallback cancelled by user")
                    _set_download_progress("Download cancelled")
                    return
                if health_check():
                    _set_download_progress(
                        "Download complete -- llama-server is running on port {:d}".format(
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
                    print("[Coworker] download_model: {:s}".format(error))
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
            # Download is done (success or failure) -- clear the active flag.
            with _lock:
                _state.download_active = False

    thread = threading.Thread(target=_do_download, daemon=True)
    thread.start()

    # Return None immediately -- the download is async.
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

    # 1. Local models dir .hf_cache (primary -- redirected downloads).
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
                    print("[Coworker] _find_model_in_hf_cache: found {:s}".format(found))
                    return found

    print("[Coworker] _find_model_in_hf_cache: {:s} not found in cache".format(filename))
    return None


def _preset_disk_hint(repo_id: str, filename: str) -> str:
    """Return a curated preset's disk-size hint (e.g. "~16 GB"), or "".

    Purely informational: shown in the download-confirmation dialog so the
    user sees roughly how large the transfer is before committing.  Reads the
    curated preset metadata (no network).
    """
    for preset in PRESET_MODELS:
        if preset.repo_id == repo_id and preset.filename == filename:
            return preset.disk_gb
    for preset in PRESET_MODELS:
        if filename and preset.filename == filename:
            return preset.disk_gb
    return ""


def check_local_model_ready(
    model_path: Path | str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Return whether a local GGUF can be launched WITHOUT downloading.

    A model is ready when it exists as a local file, or is already present in
    the HuggingFace cache -- either way ``llama-server`` starts with no
    network transfer.  When it is not ready, the second element describes the
    download that would be required (repo, filename, destination, and a
    human-readable size hint); it is ``{}`` when ready.

    Only inspects the filesystem/cache -- never the network -- so it is cheap
    enough to call on the main thread.
    """
    with _lock:
        repo_id = _config.model_repo_id
        filename = _config.model_filename

    resolved: Path | str | None = model_path
    if resolved is None and filename:
        resolved = _get_models_dir() / filename

    if resolved and os.path.isfile(str(resolved)):
        return True, {}
    if repo_id and filename and _find_model_in_hf_cache(repo_id, filename):
        return True, {}

    return False, {
        "repo_id": repo_id,
        "filename": filename,
        "dest": str(resolved) if resolved else "",
        "disk_hint": _preset_disk_hint(repo_id, filename),
    }


def set_pending_model_download(info: dict[str, Any]) -> None:
    """Record that the configured model still needs an explicit download.

    ``start_local_llama`` calls this instead of launching (and instead of
    letting llama-server fetch the file) so the UI can ask the user first.
    """
    with _lock:
        _state.model_download_required = True
        _state.pending_model_repo = str(info.get("repo_id") or "")
        _state.pending_model_filename = str(info.get("filename") or "")
        _state.pending_model_dest = str(info.get("dest") or "")
        _state.pending_model_disk_hint = str(info.get("disk_hint") or "")


def clear_pending_model_download() -> None:
    """Forget a pending download (confirmed, cancelled, or already satisfied)."""
    with _lock:
        _state.model_download_required = False
        _state.pending_model_repo = ""
        _state.pending_model_filename = ""
        _state.pending_model_dest = ""
        _state.pending_model_disk_hint = ""


def get_pending_model_download() -> dict[str, Any] | None:
    """Return info about a model that still needs downloading, or ``None``."""
    with _lock:
        if not _state.model_download_required:
            return None
        return {
            "repo_id": _state.pending_model_repo,
            "filename": _state.pending_model_filename,
            "dest": _state.pending_model_dest,
            "disk_hint": _state.pending_model_disk_hint,
        }


# ---------------------------------------------------------------------------
# llama-server binary download

def llama_asset_parts(system: str, machine: str, backend: str) -> tuple[str, str, str, str]:
    """``(platform, variant, arch, archive_ext)`` of llama.cpp's release asset for this computer.

    Asset names (as of b10154):
      Windows: ``llama-{tag}-bin-win-{cpu|cuda-12.4|vulkan}-x64.zip``
      macOS:   ``llama-{tag}-bin-macos-{arm64|x64}.tar.gz``
      Linux:   ``llama-{tag}-bin-ubuntu[-vulkan]-{x64|arm64}.tar.gz`` -- the CPU build
               has no variant in its name (#382)
    """
    arch = "arm64" if machine.lower() in ("arm64", "aarch64") else "x64"
    if system == "win32":
        return "win", {"cuda": "cuda-12.4", "vulkan": "vulkan"}.get(backend, "cpu"), "x64", ".zip"
    if system == "darwin":
        return "macos", "", arch, ".tar.gz"
    return "ubuntu", "vulkan" if backend == "vulkan" else "", arch, ".tar.gz"


def llama_asset_url(tag: str, platform: str, variant: str, arch: str, ext: str) -> str:
    name = "-".join(p for p in ("llama", tag, "bin", platform, variant, arch) if p) + ext
    return "https://github.com/ggml-org/llama.cpp/releases/download/{:s}/{:s}".format(tag, name)


def extract_llama_folder(tf: tarfile.TarFile, binary_name: str, unpack_dir: Path) -> Path | None:
    """Unpack the folder *binary_name* is in, flattened, into a fresh *unpack_dir* (#382).

    On Linux and macOS ``llama-server`` is a small launcher that loads its libraries
    from its own folder (``RUNPATH=$ORIGIN``), real files and version symlinks alike,
    so the whole folder comes along.  Returns the unpacked binary, or ``None`` if the
    archive has none.
    """
    binaries = [m for m in tf.getmembers() if m.isfile() and posixpath.basename(m.name) == binary_name]
    if not binaries:
        return None
    top = posixpath.dirname(binaries[0].name)
    members = []
    for m in tf.getmembers():
        if posixpath.dirname(m.name) != top or not (m.isfile() or m.issym()):
            continue
        if m.issym() and ("/" in m.linkname or m.linkname in ("", ".", "..")):
            continue        # only links to a neighbour (libllama.so -> libllama.so.0)
        m.name = posixpath.basename(m.name)
        members.append(m)
    if unpack_dir.exists():
        shutil.rmtree(str(unpack_dir))
    unpack_dir.mkdir(parents=True)
    safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
    print("[Coworker] download_llama_server: extracting {:d} files to {:s}".format(len(members), str(unpack_dir)))
    tf.extractall(str(unpack_dir), members=members, **safe)
    return unpack_dir / binary_name


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

    *backend* -- one of ``"auto"``, ``"cpu"``, ``"cuda"``, ``"vulkan"``.
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

    machine = os.uname().machine if hasattr(os, "uname") else "AMD64"
    platform, variant, arch, archive_ext = llama_asset_parts(sys.platform, machine, backend)
    binary_name = "llama-server.exe" if platform == "win" else "llama-server"
    tag = _LLAMA_SERVER_VERSION
    url = llama_asset_url(tag, platform, variant, arch, archive_ext)

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
    if archive_ext != ".zip":
        # Linux / macOS: the binary loads the libraries beside it, so each
        # variant gets its own folder (#382).
        dest_binary = dest_dir / "llama-{:s}".format(variant or "cpu") / binary_name
    elif backend_suffix:
        dest_binary = dest_dir / "llama-server-{backend}.exe".format(backend=backend_suffix)
    else:
        dest_binary = dest_dir / binary_name

    # Check if already downloaded.
    if dest_binary.is_file():
        msg = "llama-server already downloaded at {:s}".format(str(dest_binary))
        print("[Coworker] download_llama_server: {:s}".format(msg))
        _set_download_progress(msg)
        if progress_callback:
            progress_callback(msg)
        _clear_download_progress()
        return str(dest_binary)

    _set_download_progress("Downloading llama-server ({:s}) from {:s} ...".format(backend, url))
    if progress_callback:
        progress_callback("Downloading llama-server ({:s}) ...".format(tag))

    print("[Coworker] download_llama_server: url = {:s}".format(url))
    print("[Coworker] download_llama_server: dest_dir = {:s}".format(str(dest_dir)))

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

        # Companion DLL extensions -- extract these alongside the binary
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
                # alongside the exe -- we need them all.
                members_to_extract = list(binary_members)
                for m in zf.namelist():
                    if m in members_to_extract:
                        continue
                    if _is_companion_file(m):
                        members_to_extract.append(m)
                print("[Coworker] download_llama_server: extracting {:d} files from archive".format(
                    len(members_to_extract)))
                for m in members_to_extract:
                    zf.extract(m, str(dest_dir))
                # The extracted binary may be in a subdirectory -- move to dest.
                extracted_bin = dest_dir / binary_members[0]
                if not extracted_bin.is_file():
                    # Zip entry had a directory prefix (e.g. bin/llama-server.exe).
                    extracted_bin = dest_dir / os.path.basename(binary_members[0])
        else:
            with tarfile.open(fileobj=data, mode="r:gz") as tf:
                extracted_bin = extract_llama_folder(tf, binary_name, dest_binary.parent)
                if extracted_bin is None:
                    _set_error(
                        "Could not find {:s} in the downloaded archive".format(binary_name)
                    )
                    return None

        # Move binary to final location with backend suffix.
        if extracted_bin != dest_binary:
            if dest_binary.exists():
                dest_binary.unlink()
            shutil.move(str(extracted_bin), str(dest_binary))

        # For CUDA builds, also download the separate cudart zip as a safety
        # net -- the main zip should already have the DLLs (extracted above),
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
                print("[Coworker] download_llama_server: cudart DLLs (backup) extracted to {:s}".format(str(dest_dir)))
            except (urllib.error.URLError, OSError, zipfile.BadZipFile) as ex:
                print("[Coworker] download_llama_server: cudart backup download failed -- {:s}".format(str(ex)))
                # Non-fatal -- the main zip should have already provided them.

        # Post-extraction: verify critical DLLs are present for CUDA backend.
        if backend == "cuda" and sys.platform == "win32":
            expected_dlls = ["cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"]
            missing = [d for d in expected_dlls if not (dest_dir / d).is_file()]
            if missing:
                print(
                    "[Coworker][WARN] download_llama_server: WARNING -- missing DLLs after extraction: {:s}"
                    .format(", ".join(missing))
                )
            else:
                print("[Coworker] download_llama_server: all CUDA DLLs verified in {:s}".format(str(dest_dir)))

        # Make executable on non-Windows.
        if sys.platform != "win32":
            dest_binary.chmod(dest_binary.stat().st_mode | 0o111)

        msg = "llama-server installed at {:s}".format(str(dest_binary))
        print("[Coworker] download_llama_server: {:s}".format(msg))
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
        print("[Coworker] download_llama_server: {:s}".format(err))
        _set_error(err)
        if progress_callback:
            progress_callback(err)
        _clear_download_progress()
        return None
    except (urllib.error.URLError, OSError, zipfile.BadZipFile) as ex:
        err = "Failed to download/extract llama-server: {:s}".format(str(ex))
        print("[Coworker] download_llama_server: {:s}".format(err))
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
    still invalidated -- the bundled copy is preferred on next search.

    Returns True if any action was taken (files removed or cache cleared).
    """
    bundled_dir = _get_bundled_llama_dir()
    _log = "[Coworker][WARN] remove_llama_server"

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
                print("{:s}: failed to remove {:s} -- {:s}".format(_log, item.name, str(ex)))
    else:
        print("{:s}: bundled dir does not exist -- {:s}".format(_log, str(bundled_dir)))

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
# Process launch
#
# llama-server is launched with subprocess.Popen on every platform, with its
# stdout/stderr redirected to the log file so a startup failure is always
# visible.  (The previous Windows CreateProcessW + CREATE_NEW_CONSOLE launcher
# sent output to a console window the addon could not read, so a crash showed
# no reason; it was removed in favour of CREATE_NO_WINDOW + redirection.)



# ---------------------------------------------------------------------------
# Local LLM lifecycle


def get_llama_process() -> "subprocess.Popen | None":
    """Return the Popen handle of the currently running llama-server (or None)."""
    return _llama_process


# The memory plan of the last launch (None before one, or on a CPU backend).
_last_memory_plan: "ServerMemoryPlan | None" = None
# Servers the user stopped (Stop pressed while they loaded or ran): their exit
# is not a startup failure (#543: a late "exited during startup" error).
_user_stopped_pids: set[int] = set()


def get_last_memory_plan() -> "ServerMemoryPlan | None":
    return _last_memory_plan


def stopped_by_user(proc: "subprocess.Popen | None") -> bool:
    """True when *proc* ended because the user pressed Stop."""
    pid = getattr(proc, "pid", None)
    return pid is not None and pid in _user_stopped_pids


def start_local_llama(
    model_path: Path | str | None = None,
    port: int | None = None,
    allow_download: bool = False,
) -> "subprocess.Popen | None":
    """
    Launch ``llama-server`` as a subprocess.

    *model_path* -- if ``None``, uses the configured model.
    *port* -- if ``None``, uses the configured local port.
    *allow_download* -- when the model is neither on disk nor in the HF cache,
      ``llama-server`` would have to download it.  That is refused (the launch
      is stalled and a pending-download state is recorded for the UI to
      confirm) unless *allow_download* is set.  Only the EXPLICIT download flow
      (``download_model``) passes ``True``; starting the agent never downloads
      implicitly.
    Returns the ``Popen`` handle, or ``None`` on failure.
    """
    global _llama_process
    global _last_launched_model_path

    print("[Coworker] start_local_llama: called")
    print("[Coworker] start_local_llama:   model_path={:s}".format(str(model_path)))
    print("[Coworker] start_local_llama:   port={:s}".format(str(port)))

    with _lock:
        if _llama_process is not None and _llama_process.poll() is None:
            print("[Coworker] start_local_llama: already running, returning None")
            # Not _set_error(): it takes _lock again, and a threading.Lock is
            # not re-entrant -- that deadlocked here (#543).
            _state.error = "llama-server is already running"
            return None

    # A crashed Blender session may have left its llama-server running (it
    # still holds the port and VRAM).  Kill orphans whose Blender is gone
    # before launching, so two servers never fight over the hardware.
    _process_guard.reap_orphans()

    server_exe = find_llama_server()
    if not server_exe:
        # Re-search once in case the user installed llama-server since the
        # first (cached) lookup. This avoids requiring a Blender restart.
        invalidate_llama_server_cache()
        server_exe = find_llama_server()
    if not server_exe:
        print("[Coworker] start_local_llama: server_exe not found, aborting")
        _set_error(
            "llama-server not found -- use \"Download llama-server\" in preferences "
            "or set the path manually"
        )
        return None

    print("[Coworker] start_local_llama: server_exe = {:s}".format(server_exe))
    print("[Coworker] start_local_llama: llama-server version = {:s}".format(_llama_server_version(server_exe)))

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
        print("[Coworker] start_local_llama: resolved model_path = {:s}".format(str(model_path)))

    if model_path and os.path.isfile(str(model_path)):
        print("[Coworker] start_local_llama: local model file exists at {:s}".format(str(model_path)))
        _last_launched_model_path = Path(model_path)
        integrity_warning = check_model_file_integrity(model_path)
        if integrity_warning:
            print("[Coworker] start_local_llama: WARNING -- {:s}".format(integrity_warning))
    else:
        # No local .gguf -- try the HuggingFace cache first.
        print("[Coworker] start_local_llama: local model NOT found, checking HF cache...")
        _last_launched_model_path = None
        with _lock:
            repo = _config.model_repo_id
            fname = _config.model_filename
        hf_cached = _find_model_in_hf_cache(repo, fname)
        if hf_cached:
            model_path = Path(hf_cached)
            _last_launched_model_path = Path(hf_cached)
            print("[Coworker] start_local_llama: using HF cached model at {:s}".format(hf_cached))
        elif allow_download:
            # Explicit download flow only (see ``download_model``): let
            # llama-server fetch the file via --hf-repo/--hf-file.
            print("[Coworker] start_local_llama: download allowed -- will use --hf-repo/--hf-file")
            hf_repo = repo
            hf_file = fname
            use_hf = True
        else:
            # Not on disk and not in the HF cache.  NEVER download implicitly
            # just because llama-server was asked to start -- the model can be
            # many GB.  Stall the launch, record what a download would fetch,
            # and let the caller ask the user to confirm.
            print("[Coworker] start_local_llama: model not available locally -- "
                  "stalling launch pending user-confirmed download")
            _ready, _info = check_local_model_ready(model_path)
            if _info:
                set_pending_model_download(_info)
            _set_error(
                "Model not downloaded yet -- confirm the download, or point to "
                "an existing .gguf in Preferences \u2192 Local LLM."
            )
            return None

    # A real local path (or a cache hit) was found -- nothing is pending.
    if not use_hf:
        clear_pending_model_download()

    if port is None:
        with _lock:
            port = _config.local_port
        print("[Coworker] start_local_llama: using configured port {:d}".format(port))
    # Auto-select a free port if the configured one is busy.
    _configured_port = port
    try:
        port = _find_free_port(port)
    except RuntimeError as ex:
        _set_error(str(ex))
        return None
    if port != _configured_port:
        print("[Coworker] start_local_llama: port {:d} is busy (another "
              "llama-server or process?) -- launching on {:d} instead".format(
                  _configured_port, port))
    # Keep the config in sync with the port the server actually binds.  Without
    # this, health checks and chat requests kept targeting the configured port
    # (which something else was holding) while the server ran on the free one --
    # the readiness probe then misreported startup.
    with _lock:
        _config.local_port = port

    with _lock:
        ctx_size = _config.local_ctx_size or 32768
    # NOTE: no silent auto-upgrade -- the configured size is applied verbatim
    # and the real applied value is reported via /props (see get_runtime_ctx).
    # If it looks too small for agent work, warn (never restart mid-session).
    if ctx_size <= 8192:
        print(
            "[Coworker] start_local_llama: ctx_size {:d} is small for agent work "
            "(system prompt + tools + conversation); consider 16K-32K in preferences".format(ctx_size))
    print("[Coworker] start_local_llama: using ctx_size {:d}".format(ctx_size))
    global _runtime_ctx_cache, _runtime_ctx_logged_mismatch, _runtime_vision_cache
    _runtime_ctx_cache = None
    _runtime_ctx_logged_mismatch = False
    _runtime_vision_cache = None

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
            "[Coworker] start_local_llama: WARNING -- {:d} context on a {:.1f} GB model "
            "needs a very large KV cache; if llama-server fails to start, lower "
            "Context Size in preferences (16K-32K is plenty for agent work)".format(
                ctx_size, model_bytes / (1024 ** 3)))

    print("[Coworker] start_local_llama: platform = {:s}".format(sys.platform))

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
                # Also check System32 directly -- shutil.which may miss it.
                sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "vulkan-1.dll")
                if not os.path.isfile(sys32):
                    print("[Coworker][WARN] start_local_llama: vulkan-1.dll not found -- Vulkan backend may fail")
                else:
                    print("[Coworker] start_local_llama: vulkan-1.dll found at {:s}".format(sys32))
            else:
                print("[Coworker] start_local_llama: vulkan-1.dll found at {:s}".format(vulkan_dll))

        # The model and its projector first: the projector decides
        # --cache-reuse, and both feed the memory plan below.
        mmproj_path = None
        if not use_hf:
            # Guard: launching with a missing/empty model file makes llama-server
            # exit 1 immediately ("failed to load model"), and on Windows that
            # error is not captured.  Fail early with an actionable message.
            if not model_path or not os.path.isfile(str(model_path)):
                _set_error(
                    "Model file not found: {:s}. Download a model in Preferences "
                    "(or point at an existing .gguf) before starting the agent.".format(
                        str(model_path) if model_path else "(none selected)"))
                return None
            # A projector file next to the model makes it a vision model.
            mmproj_path = _resolve_mmproj_path(model_path)
        with _lock:
            verbose = bool(getattr(_config, "local_server_verbose", False))
            kv_quant = bool(getattr(_config, "local_kv_cache_quant", False))
            slots = int(getattr(_config, "local_parallel", 1) or 1)
            # Model-specific extra flags (e.g. Qwen3 knobs), version-guarded
            # against the pinned llama-server build so an unknown flag never
            # crashes startup on an older binary.
            preset_extra = _current_preset_extra_args()
        if preset_extra:
            preset_extra = _filter_flags_for_build(preset_extra)
            print("[Coworker] start_local_llama: preset extra args = {:s}".format(str(preset_extra)))
        # CPU backend: keep launch flags minimal -- no attention/KV knobs,
        # they mainly benefit GPU paths and risk breaking exotic CPU builds.
        #
        # Flash-attention: deliberately NOT passed.  In current llama.cpp
        # `--flash-attn` takes an *optional* value (`[on|off|auto]`), which makes
        # it a startup hazard: bare `--flash-attn` followed by another flag makes
        # the parser eat that flag as its value; the `=` form is rejected by the
        # pinned build; the space form breaks on older boolean-only builds.
        # llama-server already defaults flash-attention to 'auto', which is what
        # we want on GPU, so we rely on that default.
        gpu = backend in ("cuda", "vulkan")
        kv_q8 = gpu and kv_quant
        # -- Memory plan (#543): 16-bit KV cache when the server fits the free
        # VRAM with it, else 8-bit (a spill into shared memory makes prefill
        # crawl).  The context size stays the user's.
        global _launch_free_vram_mib, _last_memory_plan
        _launch_free_vram_mib = None
        _last_memory_plan = None
        projector_bytes = 0
        if gpu and not use_hf and model_path:
            free_mib = detect_free_vram_mib()
            _launch_free_vram_mib = free_mib
            try:
                projector_bytes = os.path.getsize(str(mmproj_path)) if mmproj_path else 0
            except OSError:
                projector_bytes = 0
            plan = plan_server_memory(model_bytes, projector_bytes, model_memory_profile(gguf_info(model_path)),
                                      free_mib, ctx=ctx_size, kv_q8_forced=kv_quant, slots=slots)
            _last_memory_plan = plan
            print("[Coworker] start_local_llama: memory plan -- ctx {:d}, {:s} KV, {:d} slot(s), needs ~{:d} MiB "
                  "VRAM, {:s} MiB free".format(ctx_size, "q8_0" if plan.kv_q8 else "f16", slots, plan.need_mib,
                                               "?" if free_mib is None else str(free_mib)))
            if plan.kv_q8 and not kv_quant:
                print("[Coworker] start_local_llama: 16-bit KV would not fit the free VRAM -- using q8_0")
            if plan.fits is False:
                print("[Coworker][WARN] start_local_llama: ~{:d} MiB needed but only {:d} MiB free (1 GB kept "
                      "free) -- only the layers that fit go on the GPU, the rest run on the CPU (slower); close "
                      "other GPU apps or lower Context Size".format(plan.need_mib, int(free_mib or 0)))
            kv_q8 = plan.kv_q8
        # GPU layers from the same numbers as the plan (#558): every layer when
        # the server fits the free VRAM, else as many as fit.
        if gpu and model_path and os.path.isfile(str(model_path)):
            ngpu_layers = autodetect_gpu_layers(Path(str(model_path)), ctx_size, free_mib=_launch_free_vram_mib,
                                                projector_bytes=projector_bytes, kv_q8=kv_q8, slots=slots)
        else:
            ngpu_layers = 99 if gpu else 0
        args = [
            server_exe,
            '--jinja',
            '--host', '127.0.0.1',
            '--port', str(port),
            '--ctx-size', str(ctx_size),
            '--n-gpu-layers', str(ngpu_layers),
        ]
        args.extend(_filter_flags_for_build(tuple(build_server_tuning_args(
            gpu=gpu, kv_q8=kv_q8, slots=slots, projector=mmproj_path is not None, verbose=verbose,
            preset_extra=tuple(preset_extra or ())))))
        if use_hf:
            args.extend(['--hf-repo', hf_repo, '--hf-file', hf_file])
        else:
            args.extend(['--model', str(model_path)])
            if mmproj_path:
                args.extend(['--mmproj', str(mmproj_path)])
                print("[Coworker] start_local_llama: using mmproj at {:s}".format(str(mmproj_path)))

        # -- Fresh, captureable server log ------------------------------
        # Start a new log so the tail we read on failure reflects THIS
        # launch; the previous one is kept (one generation) so the evidence
        # of a crash survives the restart that follows it (#543).  The
        # server's stdout/stderr are redirected to this file below (both
        # platforms), so a startup crash is always visible.
        _rotate_llama_server_log()

        # -- Drop flags this binary does not accept ---------------------
        # A build that rejects a flag prints "unknown argument" and exits 1
        # before logging anything.  Verify against the binary's own --help so
        # an unsupported optional flag is dropped instead of killing startup
        # (e.g. --no-context-shift / --flash-attn on an older or CPU build).
        _supported = _server_supported_flags(server_exe)
        if _supported is not None:
            args, _dropped = _filter_flags_against_help(
                args, _supported, keep=_REQUIRED_LLAMA_FLAGS)
            if _dropped:
                print("[Coworker][WARN] start_local_llama: dropping flags unsupported by this "
                      "build: {:s}".format(", ".join(sorted(set(_dropped)))))

        env = os.environ.copy()
        if use_hf:
            # A download: redirect the HF cache into the models dir so it lands
            # in the user's configured models directory.  Only then -- a local
            # model needs no folder there (#543).
            hf_cache_dir = _get_models_dir() / ".hf_cache"
            hf_cache_dir.mkdir(parents=True, exist_ok=True)
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
        # PATH-based DLL discovery is unreliable when the child gets its own
        # console (Debug mode) or no console; os.add_dll_directory() (Python
        # 3.8+) is the robust alternative.
        global _bundled_dll_handle
        _bundled_dll_handle = None
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            try:
                _bundled_dll_handle = os.add_dll_directory(bundled_dir)
                print("[Coworker] start_local_llama: registered DLL dir {:s}".format(bundled_dir))
            except OSError as ex:
                print("[Coworker] start_local_llama: os.add_dll_directory failed -- {:s}".format(str(ex)))

        # -- Console visibility (Debug mode) ----------------------------
        # Debug / Diagnostics mode shows the server in its own console
        # window so the user can watch it load a model live.  Otherwise the
        # server runs silently with stdio redirected to the log file, so a
        # startup crash is still captured for the failure tail.
        show_console = _show_llama_console()
        log_handle = None
        if show_console:
            # A visible console receives the server's stdout/stderr directly
            # (stdio_target=None), so keep the log file populated for the
            # failure tail via the server's own --log-file.  Only add it when
            # the build advertises it: the flag filter above already ran, so
            # appending an unknown flag here would exit 1 unvalidated.
            stdio_target = None
            _log_flag_ok = _supported is None or "--log-file" in _supported
            if _log_flag_ok and "--log-file" not in args:
                args.extend(["--log-file", str(_llama_server_log_path())])
            elif not _log_flag_ok:
                print("[Coworker][WARN] start_local_llama: this build has no --log-file; "
                      "Debug console will not write a log tail")
            print("[Coworker] start_local_llama: Debug mode -- showing llama-server console window")
        else:
            try:
                log_handle = open(str(_llama_server_log_path()), "w", encoding="utf-8", errors="replace")
            except OSError as ex:
                log_handle = None
                print("[Coworker] start_local_llama: could not open log file -- {:s}".format(str(ex)))
            stdio_target = log_handle if log_handle is not None else subprocess.DEVNULL
        print("[Coworker] start_local_llama:   args = {:s}".format(str(args)))
        print("[Coworker] start_local_llama:   log = {:s}".format(str(_llama_server_log_path())))
        if sys.platform == "win32":
            # CREATE_NO_WINDOW (default): silent, every line captured for the
            # failure tail.  CREATE_NEW_CONSOLE (Debug mode): visible console.
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=stdio_target,
                stderr=stdio_target,
                creationflags=_CREATE_NEW_CONSOLE if show_console else _CREATE_NO_WINDOW,
                env=env,
            )
        else:
            # Linux / macOS: own session (terminal signals do not reach
            # it), but on Linux the kernel still SIGTERMs it when Blender
            # dies (process_guard parent-death signal).
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=stdio_target,
                stderr=stdio_target,
                start_new_session=True,
                preexec_fn=_process_guard.linux_preexec(),
            )
        print("[Coworker] start_local_llama:   Popen returned pid={:d}".format(proc.pid))
        # Tie the server's lifetime to Blender's: on Windows a kill-on-close
        # job object ends it even when Blender CRASHES (no Python runs then).
        _process_guard.bind_to_parent(proc, "llama-server", int(port or 0))

    except FileNotFoundError:
        print("[Coworker] start_local_llama: FileNotFoundError -- binary not found")
        _set_error("Failed to launch llama-server -- binary not found")
        return None
    except OSError as ex:
        print("[Coworker] start_local_llama: OSError -- {:s}".format(str(ex)))
        _set_error("Failed to launch llama-server: {:s}".format(str(ex)))
        return None

    _llama_process = proc
    with _lock:
        _state.is_running = True
        _state.current_mode = "local"
        _state.model_name = os.path.basename(hf_file or str(model_path or ""))
        _state.error = ""
        _state.download_progress = ""

    print("[Coworker] start_local_llama: SUCCESS -- server launched")
    return proc


def stop_local_llama() -> None:
    """Gracefully terminate the ``llama-server`` subprocess."""
    global _llama_process

    print("[Coworker] stop_local_llama: called")
    # NOTE: `_shutting_down` lives on AgentState (agent_controller),
    # not LLMState -- do not set it here.
    _state.error = "Stopping LLM..."

    with _lock:
        proc = _llama_process

    print("[Coworker] stop_local_llama:   tracked proc = {:s}".format(str(proc)))

    # Try to terminate the tracked process first.
    if proc is not None:
        # Marked before it dies: a start still waiting for it to load then
        # ends as "Stopped", not as a startup failure (#543).
        if getattr(proc, "pid", None) is not None:
            _user_stopped_pids.add(proc.pid)
        try:
            print("[Coworker] stop_local_llama:   calling proc.terminate()")
            proc.terminate()
            print("[Coworker] stop_local_llama:   waiting up to 10s for exit...")
            proc.wait(timeout=10)
            print("[Coworker] stop_local_llama:   process exited")
        except subprocess.TimeoutExpired:
            print("[Coworker] stop_local_llama:   timeout -- force killing")
            _state.error = "Force killing LLM..."
            proc.kill()
            proc.wait(timeout=3)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] stop_local_llama:   exception during terminate: {:s}".format(str(ex)))

    _llama_process = None
    if proc is not None:
        _process_guard.forget(getattr(proc, "pid", None))

    # Fallback: stop any OTHER llama-server this window launched, plus orphans
    # left by a Blender that is gone.  Issue #85: never kill by image name --
    # that also killed the llama-server of every other open Blender window
    # (and any llama-server the user runs themselves).
    try:
        own = _process_guard.kill_own("llama-server")
        orphans = _process_guard.reap_orphans()
        if own or orphans:
            print("[Coworker] stop_local_llama:   stopped {:d} leftover + {:d} orphaned llama-server(s)".format(
                own, orphans))
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] stop_local_llama:   fallback cleanup exception: {:s}".format(str(ex)))

    with _lock:
        _state.is_running = False
        _state.current_mode = "off"
    _state.error = ""

    print("[Coworker] stop_local_llama: done")


# ---------------------------------------------------------------------------
# Health checks

def health_check(url: str | None = None, quiet: bool = False) -> bool:
    """
    Ping the LLM backend to confirm it is ready.

    *url* -- defaults to ``http://127.0.0.1:{port}/health`` using the configured port.
    *quiet* -- no log line (the ready wait polls every few seconds for up to 20 minutes, #591).
    """
    if url is None:
        with _lock:
            port = _config.local_port
        url = _LOCAL_LLM_HEALTH_URL.format(port)
    if not quiet:
        print("[Coworker] health_check: pinging {:s} ...".format(url))
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            ok = resp.status == 200
            if not quiet:
                print("[Coworker] health_check: status={:d} -> {:s}".format(resp.status, "OK" if ok else "FAIL"))
            return ok
    except (urllib.error.URLError, OSError) as ex:
        if not quiet:
            print("[Coworker] health_check: connection failed -- {:s}".format(str(ex)))
        return False


def _abandon_launched_server(proc: "subprocess.Popen | None") -> None:
    """Give up on a launched server that never became ready.

    Terminates the process if it is still alive (a timed-out load otherwise
    lingers and holds the port, so the next start hits "already running" or a
    port conflict) and clears the running state so a retry starts clean.
    """
    global _llama_process
    try:
        if proc is not None and proc.poll() is None:
            print("[Coworker] _abandon_launched_server: terminating pid={:d}".format(proc.pid))
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # pylint: disable=broad-exception-caught
                proc.kill()
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] _abandon_launched_server: {:s}".format(str(ex)))
    with _lock:
        if _llama_process is proc:
            _llama_process = None
        _state.is_running = False
        _state.current_mode = "off"


# #591: the longest a still-running llama-server may take to load its model before we give up.
_READY_HARD_CAP = 20.0 * 60.0


def wait_until_ready(timeout: float = 60.0, proc: "subprocess.Popen | None" = None) -> bool:
    """Block until the local llama-server answers a health check.

    Polls :func:`health_check` until it succeeds or *timeout* seconds elapse.
    If *proc* is given and the process exits early, returns ``False``
    immediately with an error set. Returns ``True`` when the server is ready.

    On failure, the llama-server log tail is included in the error so the
    real startup problem is visible instead of a generic message.

    A server the user stopped (:func:`stopped_by_user`) returns ``False``
    with no error: the caller shows "Stopped" (#543).
    """
    import time as _time
    started = _time.monotonic()
    deadline = started + timeout
    poll = 0.5
    last_note = started
    print("[Coworker] wait_until_ready: waiting up to {:.0f}s for llama-server...".format(timeout))
    while True:
        now = _time.monotonic()
        if now >= deadline:
            # #591: past the limit, a server process that is still alive is still reading the
            # model (a cold disk, a big file): keep waiting, up to a hard cap, instead of
            # abandoning a load that was about to finish.  A dead process ends it at once below.
            if proc is not None and proc.poll() is None and now - started < _READY_HARD_CAP:
                deadline = now + 30.0
            else:
                break
        if now - last_note >= 30.0:
            last_note = now
            print("[Coworker] wait_until_ready: llama-server is still loading the model ({:.0f}s so far; "
                  "process {:s}; log: {:s})".format(
                      now - started, "running" if proc is None or proc.poll() is None else "exited",
                      (get_llama_server_log_tail(1, 160).splitlines() or ["empty"])[-1]))
        if stopped_by_user(proc):
            print("[Coworker] wait_until_ready: stopped by the user before it was ready")
            return False
        if health_check(quiet=True):
            print("[Coworker] wait_until_ready: server is ready")
            return True
        if proc is not None and proc.poll() is not None and stopped_by_user(proc):
            print("[Coworker] wait_until_ready: stopped by the user before it was ready")
            return False
        if proc is not None and proc.poll() is not None:
            # The launched process reports as exited.  That is NOT necessarily
            # a startup failure: a server may already be answering on the port,
            # or a very fast handle read can be wrong.  Give a short grace
            # during which a still-starting server can answer.
            _grace_deadline = _time.monotonic() + 3.0
            _serving = False
            while _time.monotonic() < _grace_deadline:
                if health_check():
                    _serving = True
                    break
                _time.sleep(0.5)
            if _serving:
                print("[Coworker] wait_until_ready: launched process exited but a "
                      "server is answering on the port -- treating as ready")
                return True
            with _lock:
                _port = _config.local_port
            tail = get_llama_server_log_tail()
            msg = ("llama-server exited during startup (exit code {:d}{:s}) on port "
                   "{:d} -- check the model file, mmproj, GPU memory, and whether "
                   "another process is using the port").format(
                       proc.returncode, _describe_exit_code(proc.returncode), _port)
            if not tail:
                # No log was captured (the build has no --log-file, or it died
                # before writing one).  Say so instead of showing a stale file,
                # and point at the console window where its output went.
                msg += ("\n\nNo server log was captured -- llama-server printed no "
                        "output before exiting (this build has no --log-file, so its "
                        "output went to its own console window). A very early exit "
                        "is most often an unsupported launch flag or a missing/"
                        "unreadable model file.")
            # DLL_NOT_FOUND on Windows -- the CUDA/Vulkan runtime DLLs are
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
            print("[Coworker] wait_until_ready: process exited early (rc={:d}){:s}".format(
                proc.returncode, ":\n{:s}".format(tail) if tail else ""))
            _abandon_launched_server(proc)
            return False
        _time.sleep(poll)
        poll = min(poll * 1.5, 3.0)
    tail = get_llama_server_log_tail()
    msg = "llama-server did not become ready within {:.0f}s".format(_time.monotonic() - started)
    if proc is not None and proc.poll() is None:
        msg += (" (the process was still running, but the model never finished loading: a very large model "
                "or a slow disk. Starting it again once the file is in the system cache is usually much faster)")
    if tail:
        msg += "\n\n--- llama-server.log (tail) ---\n{:s}".format(tail)
    _set_error(msg)
    print("[Coworker] wait_until_ready: timed out")
    _abandon_launched_server(proc)
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

    print("[Coworker] check_remote_api: checking {:s}".format(url))

    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer {:s}".format(api_key)},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            ok = "data" in data
            print("[Coworker] check_remote_api: status={:d}, has_data={:s}".format(resp.status, str(ok)))
            return ok
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
        print("[Coworker] check_remote_api: failed -- {:s}".format(str(ex)))
        return False


# ---------------------------------------------------------------------------
# Internal helpers

_MIN_MMPROJ_BYTES = 1 * 1024 * 1024  # 1 MB -- real projectors are 100s of MB


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
            "[Coworker] _resolve_mmproj_path: skipping {:s} -- only {:.0f} KB "
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
      one vision-preset model -- otherwise the generic file could be another
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
        # Fallback: the generic name -- but only when this folder holds exactly
        # one vision-preset model, so the generic file can't be a different
        # model's projector.
        if _count_vision_models_in_dir(model_dir) == 1:
            candidates.append(model_dir / preset.mmproj_filename)

        for candidate in candidates:
            if _is_valid_mmproj(candidate, model_name):
                print(
                    "[Coworker] _resolve_mmproj_path: using {:s} (preset {:s})".format(
                        str(candidate), preset.identifier)
                )
                return candidate

        # Explain why vision is unavailable so it isn't a mystery.
        generic = model_dir / preset.mmproj_filename
        if generic.is_file() and _count_vision_models_in_dir(model_dir) > 1:
            print(
                "[Coworker] _resolve_mmproj_path: {:s} is present but the folder contains several "
                "vision models -- one shared projector can't match them all, so it is not attached. "
                "Use the addon's Download button (saves each projector under its own name) or rename "
                "it to {:s}".format(generic.name, _local_mmproj_name(preset))
            )
        else:
            print(
                "[Coworker] _resolve_mmproj_path: preset {:s} needs {:s} but it is missing -- "
                "vision input will be unavailable".format(preset.identifier, _local_mmproj_name(preset))
            )
        return None

    # 2. Non-preset model that looks vision-capable -- generic projector names.
    if "vl" in model_name or "vision" in model_name:
        for candidate_name in ("mmproj-F16.gguf", "mmproj.gguf", "mmproj-BF16.gguf"):
            candidate = model_dir / candidate_name
            if _is_valid_mmproj(candidate, model_name):
                print(
                    "[Coworker] _resolve_mmproj_path: using {:s} for vision model {:s}".format(
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
    because several presets share the generic HF filename ``mmproj-F16.gguf`` --
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
        print("[Coworker] _download_mmproj_if_needed: {:s} already exists".format(str(mmproj_dest)))
        return

    print(
        "[Coworker] _download_mmproj_if_needed: downloading {:s} as {:s} from {:s}".format(
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
            "[Coworker] _download_mmproj_if_needed: {:s} downloaded to {:s}".format(
                mmproj_fname, str(mmproj_dest))
        )
    else:
        print("[Coworker] _download_mmproj_if_needed: failed to download {:s}".format(mmproj_fname))
        # Non-fatal -- the model can still run without vision.


def _set_error(msg: str) -> None:
    with _lock:
        _state.error = msg


def clear_error() -> None:
    """Clear the last error message (e.g. after the user cancels a download)."""
    with _lock:
        _state.error = ""


# ---------------------------------------------------------------------------
# Cleanup helper (call from unregister)

def cleanup() -> None:
    """Stop the local LLM if running. Safe to call multiple times."""
    stop_local_llama()