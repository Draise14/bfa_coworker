# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Child-process guard: servers the add-on launches die with Blender.

``llama-server`` and the MCP server are started as subprocesses.  A clean
add-on unregister stops them, but when Blender / Bforartists CRASHES no
Python code runs, so on Windows the servers kept running -- holding their
ports and, for llama-server, gigabytes of VRAM.  The next start then launched
a second server and the two ground the machine to a halt.

Three layers, strongest first:

1. **Windows Job Object** (``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``): each
   launched server is assigned to a job whose only handle is owned by the
   Blender process.  When Blender exits for ANY reason -- crash included --
   Windows closes the handle and kills every process in the job.
2. **Linux parent-death signal** (``PR_SET_PDEATHSIG``): the kernel sends
   SIGTERM to the server when Blender dies.  (macOS has no equivalent; layer 3
   covers it.)
3. **Registry + reaper** (all platforms): every launched server is recorded
   (pid, kind, port, owning Blender pid) in a small JSON file.  On the next
   start, entries whose owning Blender process is gone but whose server is
   still alive -- and still looks like that server -- are killed.  This also
   cleans up orphans left by crashes BEFORE this guard existed (layers 1-2
   only protect servers launched by a build that has them).

Every function is best-effort and never raises: a guard failure must never
stop a server from starting.  Deliberately free of ``bpy``.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

__all__ = (
    "bind_to_parent",
    "linux_preexec",
    "record",
    "forget",
    "reap_orphans",
    "registry_path",
)

_lock = threading.Lock()
_job_handle: Any = None  # Windows job handle, kept open for Blender's lifetime

# Executable-name fragments that identify each kind of server we launch; the
# reaper only kills a recorded pid whose image/cmdline still matches (a pid
# can be reused by an unrelated process after a reboot).
_KIND_MARKERS = {
    "llama-server": ("llama-server",),
    "mcp-server": ("python", "blmcp", "bfa-coworker-mcp", "bfa_coworker"),
}


def registry_path() -> Path:
    """Location of the launched-server registry (per user, survives crashes)."""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "bfa_coworker" / "launched_servers.json"


# ---------------------------------------------------------------------------
# Layer 1: Windows Job Object


def _win_job() -> Any:
    """Create (once) the kill-on-close job object.  Returns its handle or None."""
    global _job_handle
    if _job_handle is not None:
        return _job_handle
    try:
        from ctypes import wintypes  # pylint: disable=import-outside-toplevel

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimit),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimit()
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        info.BasicLimitInformation.LimitFlags = 0x2000
        # JobObjectExtendedLimitInformation = 9
        if not kernel32.SetInformationJobObject(
                job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        _job_handle = job
        return job
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] process_guard: job object unavailable -- {:s}".format(str(ex)))
        return None


def bind_to_parent(proc: "subprocess.Popen | None", kind: str = "", port: int = 0) -> bool:
    """Tie *proc*'s lifetime to Blender's and record it for the reaper.

    Windows: assign it to the kill-on-close job (layer 1).  Every platform:
    record it in the registry (layer 3).  Returns True when the OS-level tie
    (layer 1) is in place.  Never raises.
    """
    if proc is None:
        return False
    tied = False
    if sys.platform == "win32":
        with _lock:
            job = _win_job()
            handle = getattr(proc, "_handle", None)
            if job and handle:
                try:
                    from ctypes import wintypes  # pylint: disable=import-outside-toplevel
                    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
                    kernel32.AssignProcessToJobObject.argtypes = [
                        wintypes.HANDLE, wintypes.HANDLE]
                    tied = bool(kernel32.AssignProcessToJobObject(job, int(handle)))
                    if not tied:
                        print("[Coworker] process_guard: AssignProcessToJobObject "
                              "failed (error {:d})".format(ctypes.get_last_error()))
                except Exception as ex:  # pylint: disable=broad-exception-caught
                    print("[Coworker] process_guard: could not bind pid {:d} -- {:s}".format(
                        proc.pid, str(ex)))
    record(proc.pid, kind, port)
    if tied:
        print("[Coworker] process_guard: {:s} pid {:d} will close with Blender".format(
            kind or "server", proc.pid))
    return tied


# ---------------------------------------------------------------------------
# Layer 2: Linux parent-death signal


def linux_preexec() -> "Callable[[], None] | None":
    """``preexec_fn`` that makes the child get SIGTERM when Blender dies.

    Returns None off Linux (pass it straight to ``Popen(preexec_fn=...)``).
    """
    if not sys.platform.startswith("linux"):
        return None

    # Load libc HERE, in the parent.  ``preexec_fn`` runs in the forked child
    # of a multi-threaded Blender, where only async-signal-safe work is safe:
    # a ``dlopen`` there can deadlock on a loader/malloc lock another thread
    # held at fork time.  The child then only makes the ``prctl`` call.
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return None

    def _set_pdeathsig() -> None:
        try:
            libc.prctl(1, 15)  # PR_SET_PDEATHSIG = 1, SIGTERM = 15
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    return _set_pdeathsig


# ---------------------------------------------------------------------------
# Layer 3: registry + reaper


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(entries: list[dict[str, Any]]) -> None:
    try:
        path = registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def record(pid: int, kind: str, port: int = 0) -> None:
    """Remember a launched server (owned by this Blender process)."""
    with _lock:
        entries = [e for e in _load() if int(e.get("pid", -1)) != int(pid)]
        entries.append({"pid": int(pid), "kind": kind, "port": int(port or 0),
                        "owner": os.getpid()})
        _save(entries)


def forget(pid: int | None) -> None:
    """Drop a server from the registry (it was stopped cleanly)."""
    if not pid:
        return
    with _lock:
        _save([e for e in _load() if int(e.get("pid", -1)) != int(pid)])


def _process_image(pid: int) -> str | None:
    """Lower-cased image name / command line of *pid*, or None if not running."""
    if pid <= 0:
        return None
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq {:d}".format(pid), "/FO", "CSV", "/NH", "/V"],
                capture_output=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.decode(errors="replace")
            line = out.strip().splitlines()[0] if out.strip() else ""
            if not line or '"{:d}"'.format(pid) not in line:
                return None
            return line.lower()
        proc_dir = Path("/proc/{:d}".format(pid))
        if proc_dir.exists():
            try:
                return proc_dir.joinpath("cmdline").read_bytes().replace(
                    b"\0", b" ").decode(errors="replace").lower()
            except OSError:
                return ""
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, timeout=5).stdout.decode(errors="replace")
        return out.strip().lower() or None
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _kill(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/f", "/t", "/pid", str(pid)],
                           capture_output=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.kill(pid, 15)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def reap_orphans() -> int:
    """Kill recorded servers whose owning Blender process is gone.

    A server owned by ANOTHER running Blender instance is left alone (several
    instances may share one machine).  A recorded pid that no longer looks
    like the server we launched (pid reuse) is just forgotten.  Returns the
    number of processes killed.  Never raises.
    """
    killed = 0
    try:
        with _lock:
            entries = _load()
            keep: list[dict[str, Any]] = []
            for e in entries:
                pid = int(e.get("pid", -1))
                owner = int(e.get("owner", -1))
                image = _process_image(pid)
                if image is None:
                    continue  # already gone
                if owner == os.getpid() or (
                        owner > 0 and _process_image(owner) is not None):
                    keep.append(e)  # its Blender is still running
                    continue
                markers = _KIND_MARKERS.get(str(e.get("kind", "")), ())
                if markers and not any(m in image for m in markers):
                    continue  # pid reused by something else -- forget it
                print("[Coworker] process_guard: killing orphaned {:s} pid {:d} "
                      "(port {:d}) left by a Blender session that is gone".format(
                          str(e.get("kind") or "server"), pid, int(e.get("port", 0) or 0)))
                _kill(pid)
                killed += 1
            _save(keep)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] process_guard: reap skipped -- {:s}".format(str(ex)))
    return killed
