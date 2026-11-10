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

A call that answers later (a render, a step that asks first, ``get_job_status``
waiting) is recorded as "waiting", and :func:`answered` finishes its entry when
the answer goes out (#423).  A call that starts a long job (``bake_textures``,
a render handed to ``long_jobs``) is "running" until that job ends:
:func:`follow_jobs` (the bridge's timer) ends it done, error or cancelled.

bpy-free; the bridge calls :func:`record` on the main thread.
"""

from __future__ import annotations

__all__ = (
    "Entry",
    "answered",
    "clear",
    "entries",
    "follow_jobs",
    "following",
    "log_text",
    "record",
)

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from . import log

_MAX = 200
_PREVIEW = 160


@dataclass
class Entry:
    tool: str               # "execute_blender_code", or "code" from an older server
    args: str               # the tool's input, short
    status: str             # "ok" | "error" | "cancelled" | "waiting" (no answer yet) | "running" (its job)
    seconds: float
    preview: str            # the result's start, or the error
    when: float             # time.time()
    thread: str = ""        # the thread that called (#357; "" = the default one)
    job_id: str = ""        # the long job this call started (#423), while it runs


_lock = threading.Lock()
_entries: deque[Entry] = deque(maxlen=_MAX)
on_change: Callable[[], None] | None = None     # redraw hook


def _short(value: Any, limit: int = _PREVIEW) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(log.redact(text).split())       # secrets as *** before cutting: no half key (#346)
    return text if len(text) <= limit else text[:limit - 3] + "..."


# Tools that only report on a job: their answer is complete even when it says "running".
_STATUS_POLLS = frozenset({"get_job_status"})


# The keys of the bridge's answer envelope (not of a tool's own result).
_ENVELOPE = frozenset({"status", "result", "message", "stdout", "stderr", "preflight_notes"})


def _outcome(tool: str, response: dict[str, Any]) -> tuple[str, str, str]:
    """(status, preview, job id to follow) for an answer the bridge sent."""
    # A step that asked first (risky_steps) answers with the call's whole answer as its result.
    while (isinstance(response.get("result"), dict) and "result" in response["result"]
           and set(response["result"]) <= _ENVELOPE):
        response = response["result"]
    result = response.get("result")
    res = result if isinstance(result, dict) else {}
    if response.get("status") == "error" or res.get("status") == "error" or res.get("state") == "error":
        return "error", _short(response.get("message") or res.get("message") or result or "error"), ""
    if res.get("state") == "cancelled":
        return "cancelled", _short(res.get("message") or "cancelled"), ""
    job_id = str(res.get("job_id") or "")
    if job_id and res.get("state", "running") == "running" and tool not in _STATUS_POLLS:
        return "running", _short("{:s} ({:s})".format(str(res.get("message") or "Running"), job_id)), job_id
    return "ok", _short(response.get("result", "")), ""


def _changed() -> None:
    if on_change is not None:
        try:
            on_change()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def record(tool: str, args: Any, response: dict[str, Any] | None, seconds: float,
           deferred: bool = False, thread: str = "") -> Entry:
    """One call the bridge ran (*response* is what it sent back); returns its entry,
    which :func:`answered` finishes for a *deferred* call."""
    response = response or {}
    tool = tool or "code"
    job_id = ""
    if deferred:
        status, preview = "waiting", "waiting for Blender (a render, or your answer)"
    else:
        status, preview, job_id = _outcome(tool, response)
    entry = Entry(tool=tool, args=_short(args or "", 120), status=status, seconds=round(seconds, 2),
                  preview=preview, when=time.time(), thread=thread, job_id=job_id)
    with _lock:
        _entries.append(entry)
    _changed()
    return entry


def answered(entry: Entry, response: dict[str, Any] | None) -> None:
    """The deferred answer for *entry* went out (or the call ended without one)."""
    status, preview, job_id = _outcome(entry.tool, response or {})
    with _lock:
        entry.status, entry.preview, entry.job_id = status, preview, job_id
        entry.seconds = round(max(0.0, time.time() - entry.when), 2)
    _changed()


def following() -> bool:
    """True while an entry waits for its long job to end."""
    with _lock:
        return any(e.job_id for e in _entries)


def follow_jobs(job_status: Callable[[str], dict[str, Any]]) -> bool:
    """End the entries whose long job is over (*job_status* is ``long_jobs.status``);
    True when one changed."""
    with _lock:
        waiting = [e for e in _entries if e.job_id]
    changed = False
    for entry in waiting:
        job = job_status(entry.job_id) or {}
        state = str(job.get("state", ""))
        if state == "running":
            continue
        text = job.get("message") or job.get("result") or state
        status = {"done": "ok", "cancelled": "cancelled", "unknown": "ok"}.get(state, "error")
        if state == "unknown":
            text = "The job is no longer tracked (job {:s})".format(entry.job_id)
        with _lock:
            entry.status, entry.preview, entry.job_id = status, _short(text), ""
            entry.seconds = round(max(0.0, time.time() - entry.when), 2)
        changed = True
    if changed:
        _changed()
    return changed


def entries(limit: int = _MAX) -> list[Entry]:
    """Newest first."""
    with _lock:
        return list(reversed(_entries))[:limit]


def clear() -> None:
    with _lock:
        _entries.clear()


def log_text() -> str:
    """The whole log as plain text (oldest first), for Copy Log; secrets as ``***`` (#346)."""
    lines = []
    for e in reversed(entries()):
        stamp = time.strftime("%H:%M:%S", time.localtime(e.when))
        lines.append("{:s}  {:s}  {:s}  {:.2f}s  {:s}\n    -> {:s}".format(
            stamp, e.status.upper(), e.tool, e.seconds, e.args, e.preview))
    return log.redact("\n".join(lines)) or "(no tool calls yet)"
