# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
whisper-server: local speech to text, managed like llama-server (Tier 4, task "Video 1", #375).

The route was frozen by the Video 0 spike (#374, plan 4v section 6.3):

* **Builds** from the newest whisper.cpp GitHub release that *has* Windows assets (the
  tagged releases carry none; the build prereleases do), matched by name pattern because
  the names change between builds: always the CPU build, and with an NVIDIA GPU the
  CUDA 12 build.  Each archive is unpacked into its own folder; only
  ``Release/whisper-server.exe`` is run.
* **Models** ``ggml-<name>.bin`` from Hugging Face ``ggerganov/whisper.cpp``; *small* by
  default on CPU, *large-v3-turbo* with CUDA.
* **Server**: ``whisper-server -m <model> --host 127.0.0.1 --port <port> -t <min(8, cores)>
  -nfa --dtw <preset>`` (DTW word onsets need flash attention off); ready when
  ``GET /health`` answers 200 (the connection is refused until the model is loaded).
* **Request**: ``POST /inference`` with a 16 kHz mono WAV, ``response_format=verbose_json``,
  ``max_len=1``, ``split_on_word=true``, ``language``, optional ``prompt``.  No whisper VAD:
  it breaks the DTW times.
* **Remote**: an OpenAI-compatible ``/v1/audio/transcriptions`` with the Remote API key.

Files live in ``~/.cache/bfa_coworker_whisper/`` (next to llama-server's
``~/.cache/bfa_coworker_llama/``; not inside it, because *Remove llama-server* empties
that folder)::

    cpu/Release/whisper-server.exe      the CPU build, as unpacked from its zip
    cuda12/Release/whisper-server.exe   the CUDA 12 build
    models/ggml-small.bin               the models
    coworker_transcripts/               transcripts of media in unsaved .blend files

Files already there (copied by hand, or left by an earlier download) are used as they are.
Windows only for now: on macOS / Linux whisper.cpp ships no server build, so the manager
says so (Remote still works).

Every public function is thread-safe and never needs ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "BUILDS",
    "BUILD_PATTERNS",
    "MODEL_PRESETS",
    "WhisperState",
    "build_dir",
    "builds_to_download",
    "cancel_download",
    "data_dir",
    "default_threads",
    "get_preset",
    "get_state",
    "has_nvidia",
    "health",
    "installed_builds",
    "is_ready",
    "match_asset",
    "model_file",
    "nvidia_found",
    "model_status",
    "pick_release",
    "platform_problem",
    "resolve_build",
    "resolve_model",
    "running_port",
    "safe_extract",
    "server_command",
    "server_exe",
    "start_download",
    "start_server",
    "stop_server",
    "transcribe_remote",
    "transcribe_wav",
    "wait_ready",
)

import json
import os
import platform as _platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
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
# Builds and models

RELEASES_API = "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=40"
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{:s}.bin"
SERVER_EXE = "whisper-server.exe"
KIND = "whisper-server"        # the process_guard kind
USER_AGENT = "bfa-coworker"     # GitHub's API wants one; nothing personal in it

BUILDS = ("cpu", "cuda12")
BUILD_LABELS = {"cpu": "CPU", "cuda12": "CUDA 12"}
# Asset names changed between builds (b5130 whisper-cublas-12.4.0-bin-x64.zip,
# b5454 whisper-bin-win-cuda-12.4.0-x64.zip), so they are matched by pattern.
BUILD_PATTERNS: dict[str, tuple[str, ...]] = {
    "cpu": (r"^whisper-bin-x64\.zip$",),
    "cuda12": (r"^whisper-bin-win-cuda-12\.[\d.]+-x64\.zip$", r"^whisper-cublas-12\.[\d.]+-bin-x64\.zip$"),
}
_MARKER = ".coworker_build.json"


@dataclass(frozen=True)
class ModelPreset:
    identifier: str
    label: str
    size: int              # bytes (Hugging Face, 2026-11)
    sha256: str
    dtw: str               # whisper.cpp's --dtw preset name
    measured: bool         # measured by the spike (#374) on the 125 s test clip
    note: str


MODEL_PRESETS: tuple[ModelPreset, ...] = (
    ModelPreset("tiny", "Tiny", 77_691_713,
                "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21", "tiny", False,
                "Smallest and fastest; not measured"),
    ModelPreset("base", "Base", 147_951_465,
                "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe", "base", True,
                "Fast on CPU (5 s per 2 min); word times +-0.14 s"),
    ModelPreset("small", "Small", 487_601_967,
                "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b", "small", True,
                "Default on CPU (17 s per 2 min); word times +-0.10 s"),
    ModelPreset("medium", "Medium", 1_533_763_059,
                "6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208", "medium", False,
                "Not measured"),
    ModelPreset("large-v3-turbo", "Large v3 Turbo", 1_624_555_275,
                "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69", "large.v3.turbo", True,
                "Default with CUDA (1.3 s per 2 min; 70 s on CPU); word times +-0.10 s"),
)
DEFAULT_MODEL = {"cpu": "small", "cuda12": "large-v3-turbo"}

# The one-time CUDA start on a machine took 31.8 s in the spike; later starts ~1.5 s.
_READY_TIMEOUT = {"cpu": 90.0, "cuda12": 240.0}

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def get_preset(identifier: str) -> ModelPreset | None:
    return next((p for p in MODEL_PRESETS if p.identifier == identifier), None)


# ---------------------------------------------------------------------------
# State

@dataclass
class WhisperState:
    # Download
    download_active: bool = False
    download_kind: str = ""          # "server" | "model"
    download_target: str = ""        # build or model id
    download_pct: float = 0.0
    download_text: str = ""
    # Server
    proc: Any = None
    port: int = 0
    build: str = ""
    model: str = ""
    starting: bool = False
    ready: bool = False
    status_text: str = ""
    ready_seconds: float = 0.0
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


_lock = threading.RLock()
_state = WhisperState()
_cancel = threading.Event()
_data_dir_override: list[Path] = []
_nvidia: list[bool] = []


def get_state() -> WhisperState:
    """A snapshot (copy) of the state for the UI."""
    with _lock:
        snap = WhisperState(**{k: getattr(_state, k) for k in WhisperState.__dataclass_fields__})
        snap.extra = dict(_state.extra)
    alive = snap.proc is not None and snap.proc.poll() is None
    if snap.proc is not None and not alive:
        snap.ready = False
    return snap


def _set(**kw: Any) -> None:
    with _lock:
        for k, v in kw.items():
            setattr(_state, k, v)


# ---------------------------------------------------------------------------
# Where things are

def data_dir() -> Path:
    """``~/.cache/bfa_coworker_whisper`` (created on first use)."""
    base = _data_dir_override[0] if _data_dir_override else Path.home() / ".cache" / "bfa_coworker_whisper"
    base.mkdir(parents=True, exist_ok=True)
    return base


def set_data_dir(path: str | os.PathLike | None) -> None:
    """Tests: keep everything in *path* (``None`` = back to the default)."""
    _data_dir_override[:] = [Path(path)] if path else []


def build_dir(build: str) -> Path:
    return data_dir() / build


def models_dir() -> Path:
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return data_dir() / "whisper-server.log"


def platform_problem() -> str:
    """Why local speech can't run here ("" = it can)."""
    if sys.platform != "win32":
        return ("Local speech isn't available on this platform yet: whisper.cpp ships its "
                "server for Windows only. Turn on Use Remote for speech, or use Windows.")
    machine = (_platform.machine() or "").lower()
    if machine not in ("amd64", "x86_64", ""):
        return "Local speech isn't available on {:s} Windows yet (only x64 builds are set up).".format(machine)
    return ""


def server_exe(build: str) -> Path | None:
    """``<build>/Release/whisper-server.exe`` (or one folder deeper) when it is there."""
    root = build_dir(build)
    first = root / "Release" / SERVER_EXE
    if first.is_file():
        return first
    for cand in (root / SERVER_EXE, *root.glob("*/" + SERVER_EXE), *root.glob("*/*/" + SERVER_EXE)):
        if cand.is_file():
            return cand
    return None


def installed_builds() -> list[str]:
    return [b for b in BUILDS if server_exe(b) is not None]


def build_info(build: str) -> dict[str, Any]:
    """What the download recorded (tag, asset); ``{}`` for files put there by hand."""
    try:
        return json.loads((build_dir(build) / _MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def model_file(identifier: str) -> Path:
    return models_dir() / "ggml-{:s}.bin".format(identifier)


def model_status(identifier: str) -> str:
    """"ready", "incomplete" (wrong size) or "missing"."""
    path = model_file(identifier)
    if not path.is_file():
        return "missing"
    preset = get_preset(identifier)
    try:
        if preset is not None and path.stat().st_size != preset.size:
            return "incomplete"
    except OSError:
        return "missing"
    return "ready"


def has_nvidia() -> bool:
    """An NVIDIA GPU with a working driver (``nvidia-smi -L``); cached."""
    if _nvidia:
        return _nvidia[0]
    found = False
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run([exe, "-L"], capture_output=True, timeout=10,
                                 creationflags=_CREATE_NO_WINDOW)
            found = out.returncode == 0 and b"GPU" in out.stdout
        except (OSError, subprocess.SubprocessError):
            found = False
    _nvidia[:] = [found]
    return found


_nvidia_probe: list[threading.Thread] = []


def nvidia_found() -> bool | None:
    """For drawing: the cached answer, or ``None`` while a background check runs."""
    if _nvidia:
        return _nvidia[0]
    if not _nvidia_probe:
        th = threading.Thread(target=has_nvidia, daemon=True, name="bfacw-nvidia-smi")
        _nvidia_probe.append(th)
        th.start()
    return None


def resolve_build(device: str, nvidia: bool | None = None) -> str:
    """The build to run for the device setting ("AUTO" | "CPU" | "CUDA").

    *nvidia* (drawing passes :func:`nvidia_found`) saves the ``nvidia-smi`` call."""
    device = str(device or "AUTO").upper()
    if device == "CPU":
        return "cpu"
    if device == "CUDA":
        return "cuda12"
    nvidia = has_nvidia() if nvidia is None else nvidia
    if nvidia and server_exe("cuda12") is not None:
        return "cuda12"
    if server_exe("cpu") is not None:
        return "cpu"
    return "cuda12" if nvidia else "cpu"


def builds_to_download(device: str, nvidia: bool | None = None) -> list[str]:
    """Auto: the CPU build always, plus CUDA 12 with an NVIDIA GPU (the frozen route)."""
    device = str(device or "AUTO").upper()
    if device == "CPU":
        wanted = ["cpu"]
    elif device == "CUDA":
        wanted = ["cuda12"]
    else:
        wanted = ["cpu"] + (["cuda12"] if (has_nvidia() if nvidia is None else nvidia) else [])
    return [b for b in wanted if server_exe(b) is None]


def resolve_model(model: str, build: str) -> str:
    """"AUTO" -> small on CPU, large-v3-turbo with CUDA."""
    if model and model.upper() != "AUTO" and get_preset(model) is not None:
        return model
    return DEFAULT_MODEL.get(build, "small")


def default_threads(cpu_count: int | None = None) -> int:
    return max(1, min(8, int(cpu_count if cpu_count is not None else (os.cpu_count() or 4))))


def server_command(exe: str | os.PathLike, model_path: str | os.PathLike, port: int,
                   threads: int, dtw: str) -> list[str]:
    """The frozen start line (DTW word onsets need flash attention off: ``-nfa``)."""
    return [str(exe), "-m", str(model_path), "--host", "127.0.0.1", "--port", str(int(port)),
            "-t", str(int(threads)), "-nfa", "--dtw", str(dtw)]


# ---------------------------------------------------------------------------
# Release assets

def match_asset(names: list[str], build: str) -> str | None:
    for pattern in BUILD_PATTERNS.get(build, ()):
        rx = re.compile(pattern)
        for name in names:
            if rx.match(name):
                return name
    return None


def pick_release(releases: list[dict[str, Any]], build: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(release, asset)``: the newest release with an asset for *build*.

    ``releases/latest`` (a tagged release) has no assets today; the build prereleases do,
    so this looks through the list instead.
    """
    def when(rel: dict[str, Any]) -> str:
        return str(rel.get("published_at") or rel.get("created_at") or "")
    for rel in sorted((r for r in releases if isinstance(r, dict) and not r.get("draft")), key=when, reverse=True):
        assets = [a for a in rel.get("assets") or [] if isinstance(a, dict)]
        name = match_asset([str(a.get("name", "")) for a in assets], build)
        if name:
            return rel, next(a for a in assets if a.get("name") == name)
    return None


def _http_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Archive extraction (zip-slip safe)

class UnsafeArchive(Exception):
    """An archive member would land outside the folder it is unpacked into."""


def _inside(target: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(target), str(root)]) == str(root)
    except ValueError:      # another drive
        return False


def safe_extract(zip_path: str | os.PathLike, dest: str | os.PathLike) -> int:
    """Unpack *zip_path* into *dest*; refuses the whole archive (``UnsafeArchive``) when a
    member is absolute or resolves outside *dest*.  Returns the number of files."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        for m in members:
            name = m.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or not _inside((dest / name).resolve(), dest):
                raise UnsafeArchive("refusing {!r}: it would be written outside {:s}".format(m.filename, str(dest)))
        count = 0
        for m in members:
            zf.extract(m, str(dest))
            count += 0 if m.is_dir() else 1
    return count


# ---------------------------------------------------------------------------
# Downloads (one at a time, on a worker thread)

def _fmt_bytes(n: float) -> str:
    """Decimal units, as the download pages give them (ggml-base.bin = 148 MB)."""
    if n >= 1e9:
        return "{:.2f} GB".format(n / 1e9)
    if n >= 1e6:
        return "{:.0f} MB".format(n / 1e6)
    return "{:.0f} KB".format(n / 1e3)


def _disk_ok(folder: Path, needed: int) -> str:
    """"" or why there isn't room for *needed* bytes (+5 %)."""
    if needed <= 0:
        return ""
    try:
        free = shutil.disk_usage(str(folder)).free
    except OSError:
        return ""
    want = int(needed * 1.05)
    if free < want:
        return "Not enough disk space: need {:s}, {:s} free on {:s}".format(_fmt_bytes(want), _fmt_bytes(free), str(folder))
    return ""


class _Cancelled(Exception):
    pass


def _download_file(url: str, dest: Path, label: str, expected_size: int = 0, sha256: str = "",
                   room_factor: float = 1.0) -> None:
    """Stream *url* to *dest* (via ``.part``) with progress; raises on any failure."""
    import hashlib
    dest.parent.mkdir(parents=True, exist_ok=True)
    problem = _disk_ok(dest.parent, int(expected_size * room_factor))
    if problem:
        raise RuntimeError(problem)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    h = hashlib.sha256() if sha256 else None
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0) or expected_size
            if total and total != expected_size:
                problem = _disk_ok(dest.parent, int(total * room_factor))
                if problem:
                    raise RuntimeError(problem)
            done = 0
            t0 = last = time.monotonic()
            with open(part, "wb") as out:
                while True:
                    if _cancel.is_set():
                        raise _Cancelled()
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    if h is not None:
                        h.update(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last >= 0.2:
                        last = now
                        speed = done / max(0.001, now - t0)
                        pct = done * 100.0 / total if total else 0.0
                        _set(download_pct=pct, download_text="Downloading {:s}: {:.0f}% ({:s} of {:s}, {:s}/s)".format(
                            label, pct, _fmt_bytes(done), _fmt_bytes(total) if total else "?", _fmt_bytes(speed)))
        if total and done != total:
            raise RuntimeError("{:s} download stopped at {:s} of {:s}".format(label, _fmt_bytes(done), _fmt_bytes(total)))
        if h is not None and h.hexdigest().lower() != sha256.lower():
            raise RuntimeError("{:s}: checksum mismatch, the download is damaged; try again".format(label))
        os.replace(part, dest)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise


def _install_build(build: str, releases: list[dict[str, Any]]) -> str:
    picked = pick_release(releases, build)
    if picked is None:
        raise RuntimeError("No whisper.cpp release has a {:s} build for Windows".format(BUILD_LABELS[build]))
    rel, asset = picked
    name = str(asset["name"])
    label = "whisper-server {:s} ({:s})".format(BUILD_LABELS[build], rel.get("tag_name", "?"))
    root = data_dir()
    downloads = root / "downloads"
    zip_path = downloads / name
    # The zip plus what it unpacks to (about twice its size for the CUDA build).
    _download_file(str(asset["browser_download_url"]), zip_path, label, int(asset.get("size") or 0), room_factor=3.0)
    _set(download_text="Unpacking {:s} ...".format(label), download_pct=100.0)
    tmp = root / (build + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        safe_extract(zip_path, tmp)
        if not (tmp / "Release" / SERVER_EXE).is_file() and not list(tmp.glob("**/" + SERVER_EXE)):
            raise RuntimeError("{:s} has no {:s}".format(name, SERVER_EXE))
        (tmp / _MARKER).write_text(json.dumps({"tag": rel.get("tag_name", ""), "asset": name,
                                               "size": asset.get("size")}), encoding="utf-8")
        final = build_dir(build)
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            zip_path.unlink()
        except OSError:
            pass
    return label


def _run_download(kind: str, target: str, done_cb: Callable[[str], None] | None) -> None:
    message = ""
    try:
        if kind == "server":
            builds = builds_to_download(target) if target.upper() in ("AUTO", "CPU", "CUDA") else [target]
            if not builds:
                message = "whisper-server is already downloaded"
            else:
                _set(download_text="Looking for the newest whisper.cpp build ...")
                releases = _http_json(RELEASES_API)
                labels = [_install_build(b, releases) for b in builds]
                message = "Installed " + ", ".join(labels)
        else:
            preset = get_preset(target)
            if preset is None:
                raise RuntimeError("Unknown speech model {!r}".format(target))
            if model_status(target) == "ready":
                message = "The {:s} model is already downloaded".format(preset.label)
            else:
                _download_file(MODEL_URL.format(preset.identifier), model_file(preset.identifier),
                               "the {:s} model".format(preset.label), preset.size, sha256=preset.sha256)
                message = "Downloaded the {:s} model ({:s})".format(preset.label, _fmt_bytes(preset.size))
        _set(error="")
    except _Cancelled:
        message = "Download cancelled"
        _set(error="")
    except urllib.error.HTTPError as ex:
        message = "Download failed (HTTP {:d})".format(ex.code)
        _set(error=message)
    except (urllib.error.URLError, OSError, RuntimeError, UnsafeArchive, zipfile.BadZipFile, ValueError) as ex:
        message = "Download failed: {:s}".format(str(ex))
        _set(error=message)
    finally:
        _set(download_active=False, download_pct=0.0, download_text=message, download_kind="", download_target="")
        print("[Coworker] whisper download: {:s}".format(message))
        if done_cb is not None:
            try:
                done_cb(message)
            except Exception:  # pylint: disable=broad-exception-caught
                pass


def start_download(kind: str, target: str, done_cb: Callable[[str], None] | None = None,
                   wait: bool = False) -> bool:
    """Download the server (*kind* "server", *target* a device setting or a build) or a
    model (*kind* "model", *target* a preset id) on a worker thread.  False when another
    download runs or this platform can't run the server."""
    problem = platform_problem() if kind == "server" else ""
    if problem:
        _set(error=problem, download_text=problem)
        return False
    with _lock:
        if _state.download_active:
            return False
        _state.download_active = True
        _state.download_kind = kind
        _state.download_target = target
        _state.download_pct = 0.0
        _state.download_text = "Starting the download ..."
        _cancel.clear()
    th = threading.Thread(target=_run_download, args=(kind, target, done_cb), daemon=True,
                          name="bfacw-whisper-download")
    th.start()
    if wait:
        th.join()
    return True


def cancel_download() -> None:
    """Stop the running download (its partial file is deleted)."""
    _cancel.set()


# ---------------------------------------------------------------------------
# Server lifecycle

def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", int(port)))
            return True
        except OSError:
            return False


def _free_port_from(start: int, attempts: int = 20) -> int:
    for port in range(int(start), int(start) + attempts):
        if _port_free(port):
            return port
    raise RuntimeError("Ports {:d}-{:d} are all in use".format(start, start + attempts - 1))


def health(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:{:d}/health".format(int(port)), timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # pylint: disable=broad-exception-caught  # refused, reset, a bad status line
        return False


def log_tail(n_lines: int = 15) -> str:
    try:
        lines = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n_lines:])


def running_port() -> int:
    """The port of this window's whisper-server while it runs, else 0."""
    with _lock:
        proc, port = _state.proc, _state.port
    return port if proc is not None and proc.poll() is None else 0


def is_ready() -> bool:
    with _lock:
        proc, ready = _state.proc, _state.ready
    return bool(ready and proc is not None and proc.poll() is None)


def _wait_ready_thread(proc: Any, port: int, build: str, model: str, t0: float) -> None:
    try:
        _wait_ready(proc, port, build, model, t0)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        with _lock:
            mine = _state.proc is proc
        if mine:
            _set(starting=False, error="Waiting for whisper-server failed: {:s}".format(str(ex)))


def _wait_ready(proc: Any, port: int, build: str, model: str, t0: float) -> None:
    timeout = _READY_TIMEOUT.get(build, 90.0)
    while True:
        with _lock:
            if _state.proc is not proc:
                return      # stopped or replaced
        if health(port, timeout=1.0):
            secs = time.monotonic() - t0
            _set(starting=False, ready=True, ready_seconds=secs, error="",
                 status_text="Running on port {:d} ({:s}, {:s}); ready after {:.1f} s".format(
                     port, model, BUILD_LABELS.get(build, build), secs))
            print("[Coworker] whisper-server ready on {:d} after {:.1f} s".format(port, secs))
            return
        if proc.poll() is not None:
            tail = log_tail()
            msg = "whisper-server stopped while starting (exit code {:d})".format(proc.returncode)
            if tail:
                msg += ":\n" + tail
            _set(starting=False, ready=False, proc=None, port=0, error=msg, status_text="Stopped: start failed")
            _process_guard.forget(getattr(proc, "pid", None))
            print("[Coworker] {:s}".format(msg))
            return
        if time.monotonic() - t0 > timeout:
            _set(error="whisper-server did not answer within {:.0f} s".format(timeout))
            _terminate(proc)
            _set(starting=False, ready=False, proc=None, port=0, status_text="Stopped: start timed out")
            return
        time.sleep(0.25)


def start_server(model: str, build: str, port: int, threads: int | None = None) -> str:
    """Start whisper-server (returns at once; :func:`wait_ready` waits).  Returns "" or why not.

    Already running with this model and build: nothing to do.  The port moves up to the next
    free one when another program (or another Blender window) holds it.
    """
    problem = platform_problem()
    if problem:
        return problem
    preset = get_preset(model)
    if preset is None:
        return "Unknown speech model {!r}".format(model)
    exe = server_exe(build)
    if exe is None:
        return ("whisper-server ({:s}) isn't downloaded yet: Preferences > Add-ons > Coworker > Speech > "
                "Download".format(BUILD_LABELS.get(build, build)))
    status = model_status(model)
    if status != "ready":
        return ("The {:s} speech model isn't downloaded{:s}: Preferences > Add-ons > Coworker > Speech > "
                "Download it ({:s})".format(preset.label, " completely" if status == "incomplete" else "",
                                            _fmt_bytes(preset.size)))
    with _lock:
        cur = _state.proc
        if cur is not None and cur.poll() is None:
            if _state.model == model and _state.build == build:
                return ""
    if cur is not None:
        stop_server()
    _process_guard.reap_orphans()
    try:
        actual = int(port) if _port_free(port) else _free_port_from(int(port) + 1)
    except RuntimeError as ex:
        return str(ex)
    cmd = server_command(exe, model_file(model), actual, threads or default_threads(), preset.dtw)
    try:
        log = open(log_path(), "w", encoding="utf-8", errors="replace")
    except OSError:
        log = None
    try:
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
            kwargs["preexec_fn"] = _process_guard.linux_preexec()
        proc = subprocess.Popen(cmd, cwd=str(exe.parent), stdin=subprocess.DEVNULL,
                                stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT, **kwargs)
    except OSError as ex:
        if log is not None:
            log.close()
        return "Could not start whisper-server: {:s}".format(str(ex))
    if log is not None:
        log.close()      # the child keeps its own handle
    _process_guard.bind_to_parent(proc, KIND, actual)
    t0 = time.monotonic()
    first_cuda = build == "cuda12"
    _set(proc=proc, port=actual, build=build, model=model, starting=True, ready=False, error="",
         status_text="Starting whisper-server ({:s}, {:s}) on port {:d} ...{:s}".format(
             model, BUILD_LABELS.get(build, build), actual,
             " The first CUDA start on a computer can take about 30 s." if first_cuda else ""))
    print("[Coworker] whisper-server: {:s}".format(" ".join(cmd)))
    threading.Thread(target=_wait_ready_thread, args=(proc, actual, build, model, t0), daemon=True,
                     name="bfacw-whisper-ready").start()
    return ""


def wait_ready(timeout: float = 300.0) -> bool:
    """Block until the server answers (True) or failed / *timeout* passed (False)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            proc, starting, ready = _state.proc, _state.starting, _state.ready
        if ready and proc is not None and proc.poll() is None:
            return True
        if not starting:
            return False
        time.sleep(0.1)
    return False


def _terminate(proc: Any) -> None:
    try:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    _process_guard.forget(getattr(proc, "pid", None))


def stop_server() -> None:
    """Stop this window's whisper-server (and any leftover one it launched)."""
    with _lock:
        proc = _state.proc
        _state.proc = None
    _terminate(proc)
    try:
        _process_guard.kill_own(KIND)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    _set(port=0, starting=False, ready=False, status_text="Stopped", build="", model="")


def cleanup() -> None:
    """Add-on unregister: stop the server and any download."""
    cancel_download()
    stop_server()


# ---------------------------------------------------------------------------
# Requests

def _multipart(fields: list[tuple[str, str]], file_field: str, file_path: str | os.PathLike,
               mime: str = "audio/wav") -> tuple[bytes, str]:
    boundary = "----bfacw" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields:
        parts.append("--{:s}\r\nContent-Disposition: form-data; name=\"{:s}\"\r\n\r\n{:s}\r\n".format(
            boundary, name, str(value)).encode("utf-8"))
    with open(file_path, "rb") as fh:
        data = fh.read()
    parts.append("--{:s}\r\nContent-Disposition: form-data; name=\"{:s}\"; filename=\"{:s}\"\r\n"
                 "Content-Type: {:s}\r\n\r\n".format(boundary, file_field, os.path.basename(str(file_path)),
                                                     mime).encode("utf-8"))
    parts.append(data)
    parts.append("\r\n--{:s}--\r\n".format(boundary).encode("utf-8"))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def inference_fields(language: str = "auto", prompt: str = "") -> list[tuple[str, str]]:
    """The frozen request (no VAD: it breaks the DTW times)."""
    fields = [("response_format", "verbose_json"), ("max_len", "1"), ("split_on_word", "true"),
              ("language", (language or "auto").strip() or "auto")]
    if prompt:
        fields.append(("prompt", prompt))
    return fields


def transcribe_wav(wav_path: str | os.PathLike, language: str = "auto", prompt: str = "",
                   port: int | None = None, timeout: float = 3600.0) -> dict[str, Any]:
    """POST the WAV to this window's whisper-server; returns its ``verbose_json``."""
    port = port or running_port()
    if not port:
        raise RuntimeError("whisper-server isn't running")
    body, ctype = _multipart(inference_fields(language, prompt), "file", wav_path)
    req = urllib.request.Request("http://127.0.0.1:{:d}/inference".format(int(port)), data=body,
                                 headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError("whisper-server answered HTTP {:d}: {:s}".format(ex.code, detail)) from None


# OpenAI's endpoint takes at most 25 MB per file (about 13 minutes of 16 kHz mono WAV).
REMOTE_MAX_BYTES = 25 * 1000 * 1000


class RemoteUnsupported(RuntimeError):
    """The provider has no OpenAI-compatible transcription endpoint."""


def remote_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("No Remote API URL is set (Preferences > Coworker > Remote Settings)")
    if base.endswith("/v1"):
        return base + "/audio/transcriptions"
    return base + "/v1/audio/transcriptions"


def remote_fields(model: str, language: str = "", prompt: str = "") -> list[tuple[str, str]]:
    fields = [("model", model or "whisper-1"), ("response_format", "verbose_json"),
              ("timestamp_granularities[]", "word")]
    if language and language.lower() != "auto":
        fields.append(("language", language))
    if prompt:
        fields.append(("prompt", prompt))
    return fields


def transcribe_remote(wav_path: str | os.PathLike, base_url: str, api_key: str, model: str = "whisper-1",
                      language: str = "", prompt: str = "", timeout: float = 600.0) -> dict[str, Any]:
    """POST the WAV to an OpenAI-compatible ``/v1/audio/transcriptions`` (word timestamps).

    The key goes in the Authorization header only; it is never logged or put in an error."""
    if not api_key:
        raise RuntimeError("No Remote API key is set (Preferences > Coworker > Remote Settings)")
    size = os.path.getsize(wav_path)
    if size > REMOTE_MAX_BYTES:
        raise RuntimeError("The audio is {:s}; the remote endpoint takes at most 25 MB (about 13 minutes). "
                           "Use local speech for long clips.".format(_fmt_bytes(size)))
    url = remote_url(base_url)
    body, ctype = _multipart(remote_fields(model, language, prompt), "file", wav_path)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": ctype, "Authorization": "Bearer " + api_key, "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        if ex.code in (404, 405):
            raise RemoteUnsupported(
                "This provider has no OpenAI-compatible /v1/audio/transcriptions endpoint (HTTP {:d}). "
                "Use local speech, or a provider that has it.".format(ex.code)) from None
        if ex.code in (401, 403):
            raise RuntimeError("The provider refused the Remote API key for transcription (HTTP {:d})".format(
                ex.code)) from None
        detail = ex.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError("The remote transcription failed (HTTP {:d}): {:s}".format(ex.code, detail)) from None
    if not isinstance(data, dict) or not isinstance(data.get("words"), list):
        raise RuntimeError("The provider answered without word timestamps (it may not support "
                           "timestamp_granularities[]=word)")
    return data
