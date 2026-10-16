# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Per-window identity: the agent always drives the window that started it.

Several Blender / Bforartists windows (processes) can run Coworker at once.
Each gets its own bridge port (the bridge shuffles to the next free port), but
before issue #85 the MCP server it launched was still pointed at the
*configured* port -- i.e. at whichever window had bound it first -- so you
chatted in window B while the code ran in window A.

This module gives every Blender process a short random ``SESSION_ID``:

* the MCP server launched by a window receives it (``BFACW_SESSION_ID``) and
  stamps every bridge request with it;
* the bridge refuses a stamped request whose id is not its own, so a mix-up
  fails loudly instead of silently editing another window's scene;
* a tiny ``sessions.json`` next to the process-guard registry lists the live
  windows, so the UI can say "linked to this window" and warn when another
  Coworker window is open.

Requests WITHOUT a session id (External Harness clients, upstream tools) are
accepted unchanged.  Deliberately free of ``bpy`` at import time; never raises.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

__all__ = (
    "SESSION_ID",
    "ENV_VAR",
    "describe",
    "check_request",
    "pid_alive",
    "register_live",
    "unregister_live",
    "other_live_sessions",
    "sessions_path",
)

ENV_VAR = "BFACW_SESSION_ID"

# One id per Blender process, fixed for its lifetime.
SESSION_ID = uuid.uuid4().hex[:12]

_lock = threading.Lock()
_cache: dict[str, Any] = {"t": 0.0, "others": []}
_CACHE_SECONDS = 5.0


def sessions_path() -> Path:
    """Shared, user-scoped file listing live Coworker windows."""
    try:
        from . import process_guard  # type: ignore[import-not-found]
    except ImportError:  # standalone import (unit tests load this file directly)
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "process_guard", Path(__file__).with_name("process_guard.py"))
        process_guard = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
        _spec.loader.exec_module(process_guard)  # type: ignore[union-attr]
    return process_guard.registry_path().with_name("live_sessions.json")


def pid_alive(pid: int) -> bool:
    """Cheap liveness check (no subprocess) -- safe to call from a draw()."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # exists, owned by someone else
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _app_label() -> str:
    try:
        import bpy  # type: ignore[import-not-found]
        binary = (bpy.app.binary_path or "").lower()
        name = "Bforartists" if "bforartists" in binary else "Blender"
        return "{:s} {:s}".format(name, bpy.app.version_string.split(" ")[0])
    except Exception:  # pylint: disable=broad-exception-caught
        return "Blender"


def _blend_label() -> str:
    try:
        import bpy  # type: ignore[import-not-found]
        path = bpy.data.filepath
        return os.path.basename(path) if path else "unsaved file"
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def describe(bridge_port: int = 0, mcp_port: int = 0) -> dict[str, Any]:
    """Who this window is -- returned by the bridge's ``identify`` request."""
    return {
        "session": SESSION_ID,
        "pid": os.getpid(),
        "app": _app_label(),
        "file": _blend_label(),
        "bridge_port": int(bridge_port or 0),
        "mcp_port": int(mcp_port or 0),
    }


def check_request(request: dict[str, Any]) -> str:
    """Return an error message if *request* was meant for another window.

    Empty string = accept.  Requests without a ``session`` key are accepted
    (External Harness clients and upstream tools don't send one).
    """
    wanted = request.get("session")
    if not wanted or wanted == SESSION_ID:
        return ""
    return (
        "This Blender window (session {:s}, PID {:d}) is not the one this "
        "Coworker was started from (session {:s}). Another Blender window owns "
        "this port. The request was refused so the wrong scene is not edited. "
        "Restart Coworker from the window you are chatting in."
    ).format(SESSION_ID, os.getpid(), str(wanted))


# ---------------------------------------------------------------------------
# Live-window list (for the UI)


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(sessions_path().read_text(encoding="utf-8"))
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(entries: list[dict[str, Any]]) -> None:
    try:
        path = sessions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".{:d}.tmp".format(os.getpid()))
        tmp.write_text(json.dumps(entries, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def register_live(bridge_port: int = 0, mcp_port: int = 0) -> None:
    """Record (or refresh) this window in the live list.  Prunes dead ones."""
    try:
        with _lock:
            me = describe(bridge_port, mcp_port)
            entries = [e for e in _load()
                       if int(e.get("pid", -1)) != os.getpid()
                       and pid_alive(int(e.get("pid", -1)))]
            entries.append(me)
            _save(entries)
            _cache["t"] = 0.0
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def unregister_live() -> None:
    try:
        with _lock:
            _save([e for e in _load() if int(e.get("pid", -1)) != os.getpid()])
            _cache["t"] = 0.0
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def other_live_sessions() -> list[dict[str, Any]]:
    """Other windows currently running Coworker (cached for a few seconds)."""
    now = time.monotonic()
    if now - _cache["t"] < _CACHE_SECONDS:
        return list(_cache["others"])
    others: list[dict[str, Any]] = []
    try:
        for e in _load():
            pid = int(e.get("pid", -1))
            if pid != os.getpid() and pid_alive(pid):
                others.append(e)
    except Exception:  # pylint: disable=broad-exception-caught
        others = []
    _cache["t"] = now
    _cache["others"] = others
    return list(others)
