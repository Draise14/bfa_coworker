# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
What the bridge ran, live (Tier 4, task "Agent 9").

In External Harness mode the chat is in another app (Claude Desktop, Cursor,
VS Code...), so Blender showed nothing of what that app did.  The bridge now
records every tool call here -- tool, its input, ok / error / waiting, how
long it took, a short preview -- and the chat shows the newest ones, with Copy
Log for a bug report.  Every mode records (the local loop and External Agent
go through the same bridge), so it is a debug view for all of them.

bpy-free; the bridge calls :func:`record` on the main thread.
"""

from __future__ import annotations

__all__ = (
    "Entry",
    "clear",
    "entries",
    "log_text",
    "record",
)

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

_MAX = 200
_PREVIEW = 160


@dataclass
class Entry:
    tool: str               # "execute_blender_code", or "code" from an older server
    args: str               # the tool's input, short
    status: str             # "ok" | "error" | "waiting"
    seconds: float
    preview: str            # the result's start, or the error
    when: float             # time.time()


_lock = threading.Lock()
_entries: deque[Entry] = deque(maxlen=_MAX)
on_change: Callable[[], None] | None = None     # redraw hook


def _short(value: Any, limit: int = _PREVIEW) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def record(tool: str, args: Any, response: dict[str, Any] | None, seconds: float,
           deferred: bool = False) -> None:
    """One call the bridge ran (*response* is what it sent back)."""
    response = response or {}
    if deferred:
        status, preview = "waiting", "waiting for Blender (a render, or your answer)"
    elif response.get("status") == "error":
        status, preview = "error", _short(response.get("message") or response.get("result") or "error")
    else:
        status, preview = "ok", _short(response.get("result", ""))
    with _lock:
        _entries.append(Entry(tool=tool or "code", args=_short(args or "", 120), status=status,
                              seconds=round(seconds, 2), preview=preview, when=time.time()))
    if on_change is not None:
        try:
            on_change()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def entries(limit: int = _MAX) -> list[Entry]:
    """Newest first."""
    with _lock:
        return list(reversed(_entries))[:limit]


def clear() -> None:
    with _lock:
        _entries.clear()


def log_text() -> str:
    """The whole log as plain text (oldest first), for Copy Log."""
    lines = []
    for e in reversed(entries()):
        stamp = time.strftime("%H:%M:%S", time.localtime(e.when))
        lines.append("{:s}  {:s}  {:s}  {:.2f}s  {:s}\n    -> {:s}".format(
            stamp, e.status.upper(), e.tool, e.seconds, e.args, e.preview))
    return "\n".join(lines) or "(no tool calls yet)"
