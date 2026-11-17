# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The code of the agent's last steps, on disk *before* each one runs (#576).

``operations.jsonl`` is written after a step returns, so a script that crashes Blender
leaves no trace of itself.  Here the code goes to ``steps_last.txt`` in the log folder
(``~/.cache/bfa_coworker/logs``) first, flushed to the disk, and the entry is marked
"finished" afterwards: after a crash the last entry that still says "RUNNING" is the
script that was running.

Bounded: the last :data:`KEEP` steps, at most :data:`MAX_CHARS` characters of code each.
The first step of a session moves the file of the session before to
``steps_last.prev.txt`` (one generation), so a crashed session's file is still there after
the restart.  The code goes through :func:`log.redact` first (keys, tokens, bearer
headers), *then* is cut to length, so no half key is left.  Coworker's own bookkeeping
code is not the agent's and is not written (the caller leaves it out).

Never raises: a log that can't be written must not stop a step.  No ``bpy``.
"""

__all__ = (
    "KEEP",
    "MAX_CHARS",
    "begin",
    "end",
    "file_path",
    "reset",
)

import os
import threading
import time
from pathlib import Path

KEEP = 8                # the last steps kept
MAX_CHARS = 20000       # code kept per step (the rest is cut, and says so)
FILE_NAME = "steps_last.txt"
PREV_NAME = "steps_last.prev.txt"

_lock = threading.Lock()
_entries: list[dict] = []
_counter = [0]
_started = [False]
_dir_override: list = [None]       # tests


def _folder() -> Path:
    if _dir_override[0] is not None:
        return Path(_dir_override[0])
    from . import log
    return log.get_log_path().parent


def file_path() -> Path:
    """The file the steps are written to."""
    return _folder() / FILE_NAME


def reset(folder: object = None) -> None:
    """Forget the steps and the session start (tests; *folder* writes there instead)."""
    with _lock:
        _entries.clear()
        _counter[0] = 0
        _started[0] = False
        _dir_override[0] = folder


def _render() -> str:
    parts = ["# Coworker: the code of the last agent steps, written before each one ran (#576).\n"
             "# A step marked RUNNING never finished: if Blender crashed, it was running.\n"]
    for entry in _entries:
        parts.append("\n=== step {n} | {when} | {tool} | chat {thread} | {state} ===\n{code}\n".format(**entry))
    return "".join(parts)


def _write() -> None:
    path = file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_render())
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _first_step_of_session() -> None:
    """The file left by the session before is kept as ``.prev`` (a crash's trace)."""
    path = file_path()
    try:
        if path.is_file() and path.stat().st_size > 0:
            os.replace(path, path.with_name(PREV_NAME))
    except OSError:
        pass


def begin(tool: str, code: str, thread: str = "") -> object:
    """Write the code of a step that is about to run; returns a token for :func:`end`
    (``None`` when nothing was written).  Redacted first, then cut."""
    try:
        from . import log
        text = log.redact(str(code))
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + "\n... [cut: {:d} more characters]".format(len(text) - MAX_CHARS)
        with _lock:
            if not _started[0]:
                _started[0] = True
                _first_step_of_session()
            _counter[0] += 1
            entry = {"n": _counter[0], "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "tool": log.redact(str(tool or "?")), "thread": log.redact(str(thread or "main")),
                     "state": "RUNNING", "code": text}
            _entries.append(entry)
            del _entries[:-KEEP]
            _write()
        return entry
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def end(token: object, outcome: str = "finished") -> None:
    """Mark the step *token* (from :func:`begin`) as finished."""
    if not isinstance(token, dict):
        return
    try:
        with _lock:
            token["state"] = str(outcome)
            if token in _entries:
                _write()
    except Exception:  # pylint: disable=broad-exception-caught
        pass
