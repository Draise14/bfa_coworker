# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Lightweight file-based logging for the Coworker add-on.

All modules already emit rich ``print()`` diagnostics prefixed with
``[🛠️Coworker]``. Those go to Blender's system console and vanish when
Blender closes. This module tees those messages to a rotating log file so
users (and bug reports) can retrieve them after the fact.

Usage::

    from . import log
    log.write("download started")            # file + console
    log.write("oops", level="ERROR")         # file + console, ERROR tag

The log lives at ``~/.cache/bfa_coworker/logs/coworker.log`` and rotates to
``coworker.log.1`` when it exceeds ~1 MB (single backup, no unbounded growth).
"""

__all__ = (
    "write",
    "get_log_path",
    "read_tail",
    "install_print_tee",
)

import os
import sys
import threading
from pathlib import Path

_lock = threading.Lock()
_LOG_MAX_BYTES = 1_000_000  # ~1 MB before rotation.
_tee_installed = False


def get_log_path() -> Path:
    """Return the log file path, creating the parent directory."""
    base = Path.home() / ".cache" / "bfa_coworker" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "coworker.log"


def _rotate_if_needed(path: Path) -> None:
    """Rotate the log to ``.1`` if it exceeds the size cap."""
    try:
        if path.is_file() and path.stat().st_size > _LOG_MAX_BYTES:
            backup = path.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            path.rename(backup)
    except OSError:
        pass  # Never let logging failures break the addon.


def write(msg: str, level: str = "INFO") -> None:
    """Append a timestamped line to the log file.

    This does NOT print to the console — callers that already ``print()``
    get file output automatically via :func:`install_print_tee`.
    """
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[{:s}] [{:5s}] {:s}\n".format(ts, level, msg)
    with _lock:
        path = get_log_path()
        _rotate_if_needed(path)
        try:
            with open(str(path), "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass  # Disk full / read-only — ignore, don't crash.


def read_tail(max_lines: int = 200) -> list[str]:
    """Return the last *max_lines* lines of the log (oldest first)."""
    path = get_log_path()
    if not path.is_file():
        return []
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return [ln.rstrip("\n") for ln in lines[-max_lines:]]
    except OSError:
        return []


class _TeeStream:
    """A file-like object that writes to both the original stream and the log."""

    def __init__(self, original) -> None:
        self._original = original
        self._buf = ""

    def write(self, data: str) -> int:
        # Pass through to the real console first.
        try:
            self._original.write(data)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        # Buffer and flush complete lines to the log.
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                write(line)
        return len(data)

    def flush(self) -> None:
        try:
            self._original.flush()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def install_print_tee() -> None:
    """Redirect ``sys.stdout``/``sys.stderr`` so all ``print()`` output is logged.

    Idempotent. Only tees lines that are not already going through
    :func:`write` directly (the Coworker prints flow through here).
    """
    global _tee_installed
    if _tee_installed:
        return
    _tee_installed = True
    if sys.stdout is not None and not isinstance(sys.stdout, _TeeStream):
        sys.stdout = _TeeStream(sys.stdout)  # type: ignore[assignment]
    if sys.stderr is not None and not isinstance(sys.stderr, _TeeStream):
        sys.stderr = _TeeStream(sys.stderr)  # type: ignore[assignment]
    write("=== Coworker log session started ===")
