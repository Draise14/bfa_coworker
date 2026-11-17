# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The code of the agent's last steps, on disk *before* each one runs (#576).

``operations.jsonl`` is written after a step returns, so a script that crashes Blender
leaves no trace of itself.  Here the code goes to ``steps_<process id>.txt`` in the log
folder (``~/.cache/bfa_coworker/logs``) first, and the entry is marked "finished" after:
after a crash the last entry that still says "RUNNING" is the script that was running.

* One file per Blender process (two instances do not overwrite each other); the file of a
  crashed process stays for reading, and files older than :data:`KEEP_DAYS` days are removed
  at the first step of a session.
* Bounded: the last :data:`KEEP` steps, at most :data:`MAX_CHARS` characters of code each.
* Redacted when the file is *rendered* (every write), not when the step arrived: a key
  registered later (``log.set_secret``) is hidden in the next write too.  The code is cut to
  length first, and :func:`log.redact` also hides a known secret that the cut left short.
* Two writes per step, ``begin`` and ``end``, flushed but not ``fsync``ed: the data sits in
  the operating system's cache once ``flush`` returns, and a crash of Blender (the case here)
  does not lose it; an ``fsync`` on the main thread could stall a step by tens of ms.
* An entry joins the file only after its write worked, so a failed write cannot leave a
  false RUNNING; a failed ``end`` removes the file rather than leave a finished step marked
  RUNNING.
* The caller writes only for steps that change the scene or run model-written code.

Never raises: a log that can't be written must not stop a step.  No ``bpy``.
"""

__all__ = (
    "KEEP",
    "KEEP_DAYS",
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
KEEP_DAYS = 3           # files of other (dead) processes older than this are removed
MAX_CHARS = 20000       # code kept per step (the rest is cut, and says so)

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
    """The file this process writes its steps to."""
    return _folder() / "steps_{:d}.txt".format(os.getpid())


def reset(folder: object = None) -> None:
    """Forget the steps and the session start (tests; *folder* writes there instead)."""
    with _lock:
        _entries.clear()
        _counter[0] = 0
        _started[0] = False
        _dir_override[0] = folder


def _render(entries: list[dict]) -> str:
    from . import log
    parts = ["# Coworker: the code of the last agent steps, written before each one ran (#576).\n"
             "# A step marked RUNNING never finished: if Blender crashed, it was running.\n"]
    for entry in entries:
        parts.append("\n=== step {n} | {when} | {tool} | chat {thread} | {state} ===\n{code}\n".format(
            n=entry["n"], when=entry["when"], tool=entry["tool"], thread=entry["thread"],
            state=entry["state"], code=entry["code"]))
    return log.redact("".join(parts))


def _write(entries: list[dict]) -> None:
    path = file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_render(entries))
        fh.flush()
    os.replace(tmp, path)


def _remove_old_files() -> None:
    """Files of other processes older than :data:`KEEP_DAYS` days go."""
    try:
        mine = file_path().name
        limit = time.time() - KEEP_DAYS * 86400
        for path in _folder().glob("steps_*.txt"):
            if path.name != mine and path.stat().st_mtime < limit:
                path.unlink()
    except OSError:
        pass


def begin(tool: str, code: str, thread: str = "") -> object:
    """Write the code of a step that is about to run; returns a token for :func:`end`
    (``None`` when nothing was written)."""
    try:
        text = str(code)
        if len(text) > MAX_CHARS:
            cut = MAX_CHARS             # cut at a space, so a key is not left as a half-key
            while cut > MAX_CHARS - 200 and not text[cut - 1].isspace():
                cut -= 1
            text = text[:cut] + "\n... [cut: {:d} more characters]".format(len(text) - cut)
        with _lock:
            if not _started[0]:
                _started[0] = True
                _remove_old_files()
            entry = {"n": _counter[0] + 1, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "tool": str(tool or "?"), "thread": str(thread or "main"),
                     "state": "RUNNING", "code": text}
            kept = (_entries + [entry])[-KEEP:]
            _write(kept)                    # only a write that worked adds the entry
            _counter[0] += 1
            _entries[:] = kept
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
                try:
                    _write(_entries)
                except Exception:  # pylint: disable=broad-exception-caught
                    try:
                        file_path().unlink()    # better no file than a finished step marked RUNNING
                    except OSError:
                        pass
    except Exception:  # pylint: disable=broad-exception-caught
        pass
