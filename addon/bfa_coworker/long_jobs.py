# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Long jobs in the background (Tier 4, task "Tools 6", #309).

A long bake or render must not hold the tool call open: the MCP client gives
up after 300 s, and the local loop's HTTP call after 60 s, while Blender
finishes the job anyway.  So a long job answers at once with a **job id**, and
``get_job_status(job_id)`` (any engine) reports running / done / error /
cancelled, with progress where Blender gives it.

A job is a *checker* -- the same convention as ``deferred_tool``: a callable
that returns ``None`` while the job runs and a result ``dict`` once it is over.
The bridge's timer calls :func:`poll` each tick (``mcp_to_blender_server.poll``),
so nothing here needs a thread of its own.

Progress (probed in the Bforartists 5.3 source):

* Render: the ``render_stats`` handler gets the engine's status line --
  Cycles ``"Sample 12/128"`` (``intern/cycles/session/session.cpp``), EEVEE
  ``"Rendering 12 / 64 samples"`` (``eevee_instance.cc``) -- parsed by
  :func:`parse_progress`.
* Bake: the job's progress (``wmJobWorkerStatus.progress``) is not exposed to
  Python, so a bake reads "running, n s"; the Workshop row draws Blender's own
  ``template_running_jobs`` bar, which shows the real percentage.
* Cancel: there is no Python call that stops a render or bake job (only Esc
  and the job bar's X, which set ``G.is_break``).  The ``render_cancel`` and
  ``object_bake_cancel`` handlers tell us when it happened (:func:`cancelled`).
  They may fire on the job's thread, so :func:`cancelled` only *marks* the job;
  :func:`poll` (the bridge timer, on the main thread) ends it and runs its
  ``on_finish``, which touches scene settings.
* Loading a file ends the running jobs (:func:`file_loading`, from ``load_pre``)
  without their ``on_finish``: it would restore settings on the old file's
  freed data.

Job ids carry a random per-process part (``job-3fa9c1-2``), so an id from an
earlier session (a resumed agent, an old message) never names a new job.

Deliberately free of ``bpy`` so it can be unit-tested outside Blender.
"""

from __future__ import annotations

__all__ = (
    "STATUS_POLL_TOOLS",
    "call_wait_seconds",
    "cancelled",
    "clear",
    "file_loading",
    "has_running",
    "is_status_poll",
    "note_progress",
    "parse_progress",
    "poll",
    "poll_is_waiting",
    "row_text",
    "running",
    "start",
    "status",
    "wait_in_slices",
)

import itertools
import re
import secrets
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

# Tools that only read a job's status.  A model waiting for a bake calls the same
# one with the same arguments again and again: that is not a loop, so the repeat
# guard (``agent_controller``'s identical-call count, and the bridge-side count
# Runtime 1 / #310 adds) must not count these, and a poll that says "running"
# is the job making progress, not an idle batch.
STATUS_POLL_TOOLS = frozenset({"get_job_status"})

# A job still running after this long is reported as an error (Blender may
# still finish it; the result is just no longer awaited).  Same as the
# bridge's deferred timeout.
JOB_TIMEOUT = 60.0 * 60.0
# Finished jobs kept for get_job_status (oldest dropped first).
_KEEP_FINISHED = 20

_KIND_VERBS = {"RENDER": "Rendering", "OBJECT_BAKE": "Baking", "TRANSCRIBE": "Transcribing"}

# Patched by the tests to fake a ten-minute job.
_clock: Callable[[], float] = time.monotonic

_lock = threading.Lock()
_ids = itertools.count(1)
# Random per process: ids from an earlier session never match a new job.
_ID_PREFIX = secrets.token_hex(3)
_jobs: dict[str, dict[str, Any]] = {}


def is_status_poll(tool_name: str) -> bool:
    return str(tool_name or "") in STATUS_POLL_TOOLS


# get_job_status waits this long by default and at most (the bridge holds the
# answer; ``get_job_status_toolcode._WAIT_MAX``, under the MCP socket's 300 s).
# The default stays well under the ~60 s per-call timeout some outside MCP
# clients (External Agent / Harness) use; our own loop asks for longer waits
# and slices them (:func:`wait_in_slices`).
_POLL_WAIT_DEFAULT = 25.0
_POLL_WAIT_MAX = 240.0


def call_wait_seconds(tool_name: str, arguments: Any) -> float:
    """How long a call to *tool_name* may wait before it answers (0 for most tools),
    so a caller with its own timeout (the local loop's HTTP call) waits that much longer."""
    if not is_status_poll(tool_name):
        return 0.0
    raw = (arguments or {}).get("wait_seconds", _POLL_WAIT_DEFAULT) if isinstance(arguments, dict) else None
    try:
        return max(0.0, min(float(raw if raw is not None else _POLL_WAIT_DEFAULT), _POLL_WAIT_MAX))
    except (TypeError, ValueError):
        return 0.0


_RUNNING_RE = re.compile(r'"state"\s*:\s*"running"')


def poll_is_waiting(tool_name: str, result_text: str) -> bool:
    """True for a status poll whose job is still running (the agent is waiting)."""
    return is_status_poll(tool_name) and bool(_RUNNING_RE.search(str(result_text or "")[:2000]))


# The local loop waits in calls this short, so Stop is noticed between them.
_SLICE_SECONDS = 15.0


def wait_in_slices(call: Callable[[dict[str, Any]], str], tool_name: str, arguments: dict[str, Any],
                   stop: Any = None, slice_seconds: float = _SLICE_SECONDS) -> str:
    """One status poll for the local loop: waits as long as the model asked, in short
    calls, so Stop (*stop*, a ``threading.Event``) is noticed and no single call outlives
    its HTTP timeout.  Returns the last answer."""
    deadline = _clock() + call_wait_seconds(tool_name, arguments)
    while True:
        left = max(0.0, deadline - _clock())
        text = call(dict(arguments or {}, wait_seconds=min(slice_seconds, left)))
        if left <= slice_seconds or not poll_is_waiting(tool_name, text):
            return text
        if stop is not None and stop.is_set():
            return text


# "Sample 12/128" (Cycles), "Rendering 12 / 64 samples" (EEVEE), "Rendered 2/4 Tiles".
_SAMPLE_RE = re.compile(r"Sample\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)
_SAMPLES_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s+samples", re.IGNORECASE)
_TILES_RE = re.compile(r"Rendered\s+(\d+)\s*/\s*(\d+)\s+Tiles", re.IGNORECASE)


def parse_progress(text: str) -> float | None:
    """A render status line as a fraction 0..1, or ``None`` when it has no count."""
    text = str(text or "")
    m = _SAMPLE_RE.search(text) or _SAMPLES_RE.search(text)
    if not m or int(m.group(2)) <= 0:
        return None
    frac = min(1.0, int(m.group(1)) / int(m.group(2)))
    tiles = _TILES_RE.search(text)
    if tiles and int(tiles.group(2)) > 1:
        # Several tiles: the samples count restarts for each one.
        done, total = int(tiles.group(1)), int(tiles.group(2))
        frac = min(1.0, (min(done, total - 1) + frac) / total)
    return frac


def start(kind: str, label: str, check_fn: Callable[[], dict[str, Any] | None],
          on_finish: Callable[[], None] | None = None, timeout: float = JOB_TIMEOUT) -> str:
    """Register a running job; returns its id.

    *check_fn* returns ``None`` while running, then the result dict (its ``status``
    "ok" means done, "cancelled" cancelled, anything else an error).  *on_finish*
    runs once when the job ends however it ends (restore settings, drop handlers).
    """
    now = _clock()
    job_id = "job-{:s}-{:d}".format(_ID_PREFIX, next(_ids))
    with _lock:
        _jobs[job_id] = {
            "id": job_id, "kind": str(kind), "label": str(label) or str(kind).lower(),
            "state": "running", "started": now, "ended": 0.0, "deadline": now + float(timeout),
            "progress": None, "progress_text": "", "result": None, "message": "",
            "cancel_requested": "",
            "check_fn": check_fn, "on_finish": on_finish,
        }
        _prune()
    return job_id


def _prune() -> None:
    done = [j for j in _jobs.values() if j["state"] != "running"]
    for job in sorted(done, key=lambda j: j["ended"])[:max(0, len(done) - _KEEP_FINISHED)]:
        _jobs.pop(job["id"], None)


def _end(job: dict[str, Any], state: str, result: dict[str, Any] | None = None, message: str = "") -> None:
    """Mark *job* over (caller holds no lock: *on_finish* may touch Blender)."""
    with _lock:
        if job["state"] != "running":
            return
        job.update(state=state, ended=_clock(), result=result, message=message, check_fn=None)
        if state == "done":
            job["progress"] = 1.0
        on_finish, job["on_finish"] = job["on_finish"], None
    if on_finish is not None:
        try:
            on_finish()
        except Exception:  # pylint: disable=broad-exception-caught
            traceback.print_exc()


def poll() -> bool:
    """Check every running job (the bridge's timer); True when one ended."""
    with _lock:
        jobs = [j for j in _jobs.values() if j["state"] == "running"]
    ended = False
    for job in jobs:
        if job.get("cancel_requested"):
            _end(job, "cancelled", message=job["cancel_requested"])
            ended = True
            continue
        if _clock() > job["deadline"]:
            _end(job, "error", message=(
                "No result after {:d} minutes, so Coworker stopped waiting; Blender may still be "
                "working on it (see the job bar at the bottom).".format(int((job["deadline"] - job["started"]) / 60))))
            ended = True
            continue
        check_fn = job["check_fn"]
        if check_fn is None:
            continue
        try:
            result = check_fn()
        except Exception:  # pylint: disable=broad-exception-caught
            _end(job, "error", message=traceback.format_exc(limit=3))
            ended = True
            continue
        if result is None:
            continue
        if not isinstance(result, dict):
            result = {"status": "error", "message": "The job's checker returned {:s}, not a dict".format(
                type(result).__name__)}
        state = {"ok": "done", "cancelled": "cancelled"}.get(str(result.get("status", "")), "error")
        _end(job, state, result=result, message=str(result.get("message", "") or ""))
        ended = True
    return ended


def note_progress(kind: str, text: str) -> None:
    """A status line for the running jobs of *kind* (any thread: the render calls it)."""
    frac = parse_progress(text)
    with _lock:
        for job in _jobs.values():
            if job["state"] == "running" and job["kind"] == kind:
                job["progress_text"] = str(text or "")[:120]
                if frac is not None:
                    job["progress"] = frac


def cancelled(kind: str) -> None:
    """Blender cancelled its job of *kind* (Esc or the job bar's X).

    May be called from the job's own thread, so it only marks the jobs; the next
    :func:`poll` on the main thread ends them and runs their ``on_finish``."""
    with _lock:
        for job in _jobs.values():
            if job["state"] == "running" and job["kind"] == kind and not job["cancel_requested"]:
                job["cancel_requested"] = "Cancelled in Blender before it finished."


def file_loading() -> None:
    """A file is being loaded (``load_pre``): end the running jobs as cancelled,
    without their ``on_finish`` -- it would restore settings on the old file's
    data, which is about to be freed."""
    with _lock:
        jobs = [j for j in _jobs.values() if j["state"] == "running"]
        for job in jobs:
            job["on_finish"] = None
    for job in jobs:
        _end(job, "cancelled", message=(
            "Cancelled because another file was loaded; Blender stops a render or bake "
            "when the file changes."))


def has_running() -> bool:
    with _lock:
        return any(j["state"] == "running" for j in _jobs.values())


def _elapsed(job: dict[str, Any]) -> int:
    return int((job["ended"] or _clock()) - job["started"])


def status(job_id: str) -> dict[str, Any]:
    """What get_job_status answers: ``state`` running / done / error / cancelled."""
    with _lock:
        job = _jobs.get(str(job_id or "").strip())
        if job is None:
            known = ", ".join("{:s} ({:s}, {:s})".format(j["id"], j["label"], j["state"])
                              for j in list(_jobs.values())[-5:])
            return {"status": "error", "job_id": str(job_id), "state": "unknown", "message": (
                "There is no job '{:s}'. {:s}Long jobs come from render_viewport_to_path(background=True), "
                "bake_textures and transcribe_strip, which return the job_id.".format(
                    str(job_id), "Known jobs: {:s}. ".format(known) if known else ""))}
        out: dict[str, Any] = {
            "status": "error" if job["state"] == "error" else "ok",
            "job_id": job["id"], "state": job["state"], "kind": job["kind"], "label": job["label"],
            "elapsed_seconds": _elapsed(job),
        }
        if job["progress"] is not None:
            out["progress_percent"] = int(round(job["progress"] * 100))
        if job["progress_text"] and job["state"] == "running":
            out["progress_text"] = job["progress_text"]
        if job["result"] is not None:
            out["result"] = {k: v for k, v in job["result"].items() if k != "status"}
        if job["message"]:
            out["message"] = job["message"]
        elif job["state"] == "running":
            out["message"] = "Still running. Call get_job_status again to keep waiting."
        return out


def row_text(job: dict[str, Any]) -> str:
    """One line for the Workshop: "Baking 40%" or "Rendering, 35 s"."""
    verb = _KIND_VERBS.get(job["kind"], job["kind"].replace("_", " ").title())
    if job["state"] == "running":
        if job["progress"] is not None:
            return "{:s} {:d}%".format(verb, int(round(job["progress"] * 100)))
        return "{:s}, {:d} s".format(verb, _elapsed(job))
    return "{:s}: {:s} after {:d} s".format(verb, job["state"], _elapsed(job))


def running() -> list[dict[str, Any]]:
    """Snapshots of the running jobs (plus their ``text`` line) for the UI."""
    with _lock:
        jobs = [dict(j) for j in _jobs.values() if j["state"] == "running"]
    for job in jobs:
        job["text"] = row_text(job)
    return jobs


def clear() -> None:
    """Forget every job (tests; add-on unregister)."""
    with _lock:
        _jobs.clear()
