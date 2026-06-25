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
    "find_llama_server",
    "download_model",
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

import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Constants

_LOCAL_LLM_DEFAULT_PORT = 8081
_LOCAL_LLM_HEALTH_URL = "http://127.0.0.1:{:d}/health"
_LOCAL_LLM_CHAT_URL = "http://127.0.0.1:{:d}/v1/chat/completions"
_MODEL_DOWNLOAD_TIMEOUT = 300  # seconds

# Common install locations for llama-server on Windows.
_LLAMA_SEARCH_PATHS_WIN = [
    # PATH is searched automatically via shutil.which().
    # Also check common install dirs.
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "llama.cpp", "llama-server.exe"),
    os.path.join(os.environ.get("PROGRAMFILES", ""), "llama.cpp", "llama-server.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "llama.cpp", "llama-server.exe"),
]


# ---------------------------------------------------------------------------
# Data types

@dataclass
class LLMConfig:
    """Persisted configuration for the LLM backend."""

    mode: str = "local"  # "local" | "remote"
    # Local mode
    llama_path: str = ""
    model_repo_id: str = "unsloth/gemma-4-26B-A4B-it-GGUF"
    model_filename: str = "gemma-4-26B-A4B-it-Q4_K_M.gguf"
    downloaded_models_dir: str = ""
    local_port: int = _LOCAL_LLM_DEFAULT_PORT
    local_ctx_size: int = 8192
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


# ---------------------------------------------------------------------------
# Module-level state (thread-safe via lock)

_lock = threading.Lock()
_config: LLMConfig = LLMConfig()
_state: LLMState = LLMState()
_llama_process: subprocess.Popen | None = None


def get_state() -> LLMState:
    """Return a copy of the current runtime state."""
    with _lock:
        return LLMState(
            is_running=_state.is_running,
            current_mode=_state.current_mode,
            model_name=_state.model_name,
            error=_state.error,
            download_progress=_state.download_progress,
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
            remote_api_url=_config.remote_api_url,
            remote_api_key=_config.remote_api_key,
            remote_model=_config.remote_model,
        )


# ---------------------------------------------------------------------------
# llama-server detection

def find_llama_server() -> str | None:
    """Search PATH and common install locations for ``llama-server``."""
    print("[blender_mcp] find_llama_server: searching for llama-server...")
    # Search PATH first.
    exe = shutil.which("llama-server")
    if exe:
        print("[blender_mcp] find_llama_server: found via 'llama-server' -> {:s}".format(exe))
        return exe
    print("[blender_mcp] find_llama_server: 'llama-server' not on PATH, trying 'llama-server.exe'")
    exe = shutil.which("llama-server.exe")
    if exe:
        print("[blender_mcp] find_llama_server: found via 'llama-server.exe' -> {:s}".format(exe))
        return exe
    # Fall back to known install paths.
    print("[blender_mcp] find_llama_server: not on PATH, checking known install dirs...")
    for path in _LLAMA_SEARCH_PATHS_WIN:
        print("[blender_mcp] find_llama_server:   checking {:s}".format(path))
        if os.path.isfile(path):
            print("[blender_mcp] find_llama_server: found at {:s}".format(path))
            return path
    print("[blender_mcp] find_llama_server: NOT FOUND")
    return None


# ---------------------------------------------------------------------------
# Model download

def _get_models_dir() -> Path:
    """Return the directory where downloaded models are stored."""
    with _lock:
        custom = _config.downloaded_models_dir
    if custom and os.path.isdir(custom):
        print("[blender_mcp] _get_models_dir: using custom dir {:s}".format(custom))
        return Path(custom)
    # Default: <user_home>/blender_mcp_models/
    default = Path.home() / "blender_mcp_models"
    default.mkdir(parents=True, exist_ok=True)
    print("[blender_mcp] _get_models_dir: using default dir {:s}".format(str(default)))
    return default


def _set_download_progress(msg: str) -> None:
    with _lock:
        _state.download_progress = msg


def download_model(
    repo_id: str | None = None,
    filename: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> Path | None:
    """
    Download a GGUF model via ``llama-cli``.

    Uses the configured repo/file or the provided overrides.
    Returns the path to the downloaded model, or ``None`` on failure.
    """
    with _lock:
        r = repo_id or _config.model_repo_id
        f = filename or _config.model_filename
    print("[blender_mcp] download_model: repo_id={:s}, filename={:s}".format(r, f))
    if not r or not f:
        print("[blender_mcp] download_model: repo ID or filename not configured")
        _set_error("Model repo ID and filename must be configured")
        return None

    llama_cli = shutil.which("llama-cli") or shutil.which("llama-cli.exe")
    if not llama_cli:
        print("[blender_mcp] download_model: llama-cli not found on PATH")
        _set_error("llama-cli not found on PATH — cannot download models")
        return None
    print("[blender_mcp] download_model: llama-cli = {:s}".format(llama_cli))

    models_dir = _get_models_dir()
    dest = models_dir / f
    print("[blender_mcp] download_model: models_dir = {:s}".format(str(models_dir)))
    print("[blender_mcp] download_model: dest = {:s}".format(str(dest)))

    # Check if already downloaded.
    if dest.exists():
        print("[blender_mcp] download_model: already exists, skipping download")
        _set_download_progress("Model already downloaded: {:s}".format(str(dest)))
        if progress_callback:
            progress_callback("Model already downloaded: {:s}".format(str(dest)))
        return dest

    _set_download_progress("Downloading {:s}/{:s} ...".format(r, f))
    if progress_callback:
        progress_callback("Downloading {:s}/{:s} ...".format(r, f))

    try:
        print("[blender_mcp] download_model: running llama-cli download...")
        # Use Popen with line-by-line streaming so the user can see
        # download progress in real time in the Blender console.
        proc = subprocess.Popen(
            [
                llama_cli,
                "--hf-repo", r,
                "--hf-file", f,
            ],
            cwd=str(models_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        # Stream output line by line, passing each line through
        # the progress callback (and printing to console).
        for line in proc.stdout:
            line = line.rstrip("\n\r")
            if line:
                print("[llama-cli] {:s}".format(line))
                if progress_callback:
                    progress_callback(line)
        proc.wait(timeout=_MODEL_DOWNLOAD_TIMEOUT)
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)
        print("[blender_mcp] download_model: llama-cli completed successfully")
    except subprocess.CalledProcessError as ex:
        print("[blender_mcp] download_model: llama-cli failed: {:s}".format(str(ex)))
        _set_error("Download failed: {:s}".format(str(ex)))
        if progress_callback:
            progress_callback("Download failed: {:s}".format(str(ex)))
        return None
    except subprocess.TimeoutExpired:
        print("[blender_mcp] download_model: download timed out after {:d}s".format(_MODEL_DOWNLOAD_TIMEOUT))
        _set_error("Download timed out after {:d}s".format(_MODEL_DOWNLOAD_TIMEOUT))
        try:
            proc.kill()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        return None
    except FileNotFoundError:
        print("[blender_mcp] download_model: llama-cli not found despite shutil.which succeeding")
        _set_error("llama-cli not found even though shutil.which succeeded — this should not happen")
        return None

    if dest.exists():
        print("[blender_mcp] download_model: file found at expected path {:s}".format(str(dest)))
        _set_download_progress("Downloaded to {:s}".format(str(dest)))
        if progress_callback:
            progress_callback("Downloaded to {:s}".format(str(dest)))
        return dest

    # llama-cli may put the file in a subdirectory named after the repo.
    # Search recursively for the file in the models directory.
    print("[blender_mcp] download_model: file not at expected path, searching recursively...")
    for found in models_dir.rglob(f):
        print("[blender_mcp] download_model: found at {:s}".format(str(found)))
        _set_download_progress("Downloaded to {:s}".format(str(found)))
        if progress_callback:
            progress_callback("Downloaded to {:s}".format(str(found)))
        return found

    print("[blender_mcp] download_model: file not found anywhere after download")
    _set_error("Download completed but model file not found at {:s}".format(str(dest)))
    return None


def _find_model_in_hf_cache(repo_id: str, filename: str) -> str | None:
    """
    Search the HuggingFace cache for a GGUF model file.

    The HF cache layout is:
      ~/.cache/huggingface/hub/models--{org}--{repo}/snapshots/{hash}/{filename}

    Returns the full path to the model if found, or ``None``.
    """
    # Normalize the repo_id for the cache directory name.
    cache_dir_name = "models--{:s}".format(repo_id.replace("/", "--"))

    # Check the standard HF cache location.
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub" / cache_dir_name
    if not hf_cache.is_dir():
        # Also check HF_HOME / HF_HUB_CACHE env vars.
        hf_home = os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE")
        if hf_home:
            hf_cache = Path(hf_home) / "hub" / cache_dir_name

    if not hf_cache.is_dir():
        print("[blender_mcp] _find_model_in_hf_cache: cache dir not found at {:s}".format(str(hf_cache)))
        return None

    # Walk the snapshots directory looking for the filename.
    for root, _dirs, files in os.walk(str(hf_cache)):
        for candidate in files:
            if candidate == filename:
                found = os.path.join(root, candidate)
                print("[blender_mcp] _find_model_in_hf_cache: found {:s}".format(found))
                return found

    print("[blender_mcp] _find_model_in_hf_cache: {:s} not found in cache".format(filename))
    return None


# ---------------------------------------------------------------------------
# Local LLM lifecycle


def start_local_llama(
    model_path: Path | str | None = None,
    port: int | None = None,
) -> subprocess.Popen | None:
    """
    Launch ``llama-server`` as a subprocess.

    *model_path* — if ``None``, uses the configured model.  If the local
      file does NOT exist, passes ``--hf-repo``/``--hf-file`` to
      ``llama-server`` so it auto-downloads via HuggingFace.
    *port* — if ``None``, uses the configured local port.
    Returns the ``Popen`` handle, or ``None`` on failure.
    """
    global _llama_process

    print("[blender_mcp] start_local_llama: called")
    print("[blender_mcp] start_local_llama:   model_path={:s}".format(str(model_path)))
    print("[blender_mcp] start_local_llama:   port={:s}".format(str(port)))

    with _lock:
        if _llama_process is not None and _llama_process.poll() is None:
            print("[blender_mcp] start_local_llama: already running, returning None")
            _set_error("llama-server is already running")
            return None

    server_exe = find_llama_server()
    if not server_exe:
        print("[blender_mcp] start_local_llama: server_exe not found, aborting")
        _set_error("llama-server not found — set the path in preferences")
        return None

    print("[blender_mcp] start_local_llama: server_exe = {:s}".format(server_exe))

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
        print("[blender_mcp] start_local_llama: resolved model_path = {:s}".format(str(model_path)))

    if model_path and os.path.isfile(str(model_path)):
        print("[blender_mcp] start_local_llama: local model file exists at {:s}".format(str(model_path)))
    else:
        # No local .gguf — try the HuggingFace cache first.
        print("[blender_mcp] start_local_llama: local model NOT found, checking HF cache...")
        with _lock:
            repo = _config.model_repo_id
            fname = _config.model_filename
        hf_cached = _find_model_in_hf_cache(repo, fname)
        if hf_cached:
            model_path = Path(hf_cached)
            print("[blender_mcp] start_local_llama: using HF cached model at {:s}".format(hf_cached))
        else:
            # Not in cache either — try --hf-repo/--hf-file as last resort.
            print("[blender_mcp] start_local_llama: not in HF cache either, will use --hf-repo/--hf-file")
            hf_repo = repo
            hf_file = fname
            use_hf = True

    if port is None:
        with _lock:
            port = _config.local_port
        print("[blender_mcp] start_local_llama: using configured port {:d}".format(port))

    with _lock:
        ctx_size = _config.local_ctx_size or 8192
    print("[blender_mcp] start_local_llama: using ctx_size {:d}".format(ctx_size))

    print("[blender_mcp] start_local_llama: platform = {:s}".format(sys.platform))

    try:
        if sys.platform == "win32":
            # Build the full llama-server command line.
            # Add --verbose to see detailed logs in the console window.
            server_cmd = '"{exe}" --jinja --verbose --host 127.0.0.1 --port {port} --ctx-size {ctx} {modelarg}'.format(
                exe=server_exe,
                port=port,
                ctx=ctx_size,
                modelarg=('--hf-repo "{repo}" --hf-file "{file}"'.format(repo=hf_repo, file=hf_file)
                         if use_hf
                         else '--model "{model}"'.format(model=str(model_path))),
            )

            # Use ``start`` to open a NEW console window, and ``cmd /k``
            # so the window stays open after the server exits (crashes).
            #
            # Inside ``cmd /k "..."`` each literal ``"`` must be escaped
            # as ``""`` for cmd's parser.
            escaped = server_cmd.replace('"', '""')
            cmd_str = 'start "blender_mcp-llama-server" cmd /k "{escaped}"'.format(escaped=escaped)

            print("[blender_mcp] start_local_llama: WIN32 path")
            print("[blender_mcp] start_local_llama:   cmd = {:s}".format(cmd_str))
            proc = subprocess.Popen(cmd_str, shell=True)
            print("[blender_mcp] start_local_llama:   Popen returned pid={:d}".format(proc.pid))
        else:
            # Linux / macOS: detach from the parent process group so the
            # server survives Blender exiting.  We redirect stdio to
            # /dev/null so it doesn't hijack the Blender console.
            args = [
                server_exe,
                '--jinja',
                '--verbose',
                '--host', '127.0.0.1',
                '--port', str(port),
                '--ctx-size', str(ctx_size),
            ]
            if use_hf:
                args.extend(['--hf-repo', hf_repo, '--hf-file', hf_file])
            else:
                args.extend(['--model', str(model_path)])

            print("[blender_mcp] start_local_llama: POSIX path (start_new_session=True)")
            print("[blender_mcp] start_local_llama:   args = {:s}".format(str(args)))
            with open(os.devnull, 'w') as devnull:
                proc = subprocess.Popen(
                    args,
                    stdin=devnull,
                    stdout=devnull,
                    stderr=devnull,
                    start_new_session=True,
                )
                print("[blender_mcp] start_local_llama:   Popen returned pid={:d}".format(proc.pid))

    except FileNotFoundError:
        print("[blender_mcp] start_local_llama: FileNotFoundError — binary not found")
        _set_error("Failed to launch llama-server — binary not found")
        return None
    except OSError as ex:
        print("[blender_mcp] start_local_llama: OSError — {:s}".format(str(ex)))
        _set_error("Failed to launch llama-server: {:s}".format(str(ex)))
        return None

    _llama_process = proc
    with _lock:
        _state.is_running = True
        _state.current_mode = "local"
        _state.model_name = os.path.basename(hf_file or str(model_path or ""))
        _state.error = ""
        _state.download_progress = ""

    print("[blender_mcp] start_local_llama: SUCCESS — server launched")
    return proc


def stop_local_llama() -> None:
    """Gracefully terminate the ``llama-server`` subprocess."""
    global _llama_process

    print("[blender_mcp] stop_local_llama: called")

    with _lock:
        proc = _llama_process

    print("[blender_mcp] stop_local_llama:   tracked proc = {:s}".format(str(proc)))

    # Try to terminate the tracked process first.
    if proc is not None:
        try:
            print("[blender_mcp] stop_local_llama:   calling proc.terminate()")
            proc.terminate()
            print("[blender_mcp] stop_local_llama:   waiting up to 3s for exit...")
            proc.wait(timeout=3)
            print("[blender_mcp] stop_local_llama:   process exited")
        except subprocess.TimeoutExpired:
            print("[blender_mcp] stop_local_llama:   timeout — killing")
            proc.kill()
            proc.wait(timeout=3)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[blender_mcp] stop_local_llama:   exception during terminate: {:s}".format(str(ex)))

    _llama_process = None

    # Fallback: kill any remaining llama-server process by image name.
    # This catches orphaned processes (e.g. when launched via
    # CREATE_NEW_CONSOLE the Popen handle may not be the server itself).
    try:
        if sys.platform == "win32":
            print("[blender_mcp] stop_local_llama:   running taskkill /f /im llama-server.exe")
            result = subprocess.run(
                ["taskkill", "/f", "/im", "llama-server.exe"],
                capture_output=True,
                timeout=5,
            )
            print("[blender_mcp] stop_local_llama:   taskkill stdout = {:s}".format(result.stdout.decode().strip()))
            print("[blender_mcp] stop_local_llama:   taskkill stderr = {:s}".format(result.stderr.decode().strip()))
        else:
            print("[blender_mcp] stop_local_llama:   running pkill -f llama-server")
            result = subprocess.run(
                ["pkill", "-f", "llama-server"],
                capture_output=True,
                timeout=5,
            )
            print("[blender_mcp] stop_local_llama:   pkill stdout = {:s}".format(result.stdout.decode().strip()))
            print("[blender_mcp] stop_local_llama:   pkill stderr = {:s}".format(result.stderr.decode().strip()))
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[blender_mcp] stop_local_llama:   fallback kill exception: {:s}".format(str(ex)))

    with _lock:
        _state.is_running = False
        _state.current_mode = "off"

    print("[blender_mcp] stop_local_llama: done")


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
    print("[blender_mcp] health_check: pinging {:s} ...".format(url))
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            ok = resp.status == 200
            print("[blender_mcp] health_check: status={:d} -> {:s}".format(resp.status, "OK" if ok else "FAIL"))
            return ok
    except (urllib.error.URLError, OSError) as ex:
        print("[blender_mcp] health_check: connection failed — {:s}".format(str(ex)))
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

    print("[blender_mcp] check_remote_api: checking {:s}".format(url))

    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer {:s}".format(api_key)},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            ok = "data" in data
            print("[blender_mcp] check_remote_api: status={:d}, has_data={:s}".format(resp.status, str(ok)))
            return ok
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
        print("[blender_mcp] check_remote_api: failed — {:s}".format(str(ex)))
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