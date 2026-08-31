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
    "set_suppress_console",
)

import os
import sys
import threading
from pathlib import Path

_lock = threading.Lock()
_LOG_MAX_BYTES = 1_000_000  # ~1 MB before rotation.
_tee_installed = False
_suppress_console = True  # When True, [Coworker] lines are log-only (not Blender console).
_SUPPRESS_PREFIXES = ("[🛠️Coworker]", "[Coworker]")


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
        # Buffer and flush complete lines to the log.
        self._buf += data
        while "
" in self._buf:
            line, self._buf = self._buf.split("
", 1)
            stripped = line.strip()
            if stripped:
                write(stripped)
        # Also flush any remaining partial line immediately so that
        # crash logs capture everything written so far.
        if self._buf.strip():
            write(self._buf.strip())
            self._buf = ""
        # Pass through to the real console, but skip [Coworker] lines
        # when console suppression is active (debug mode OFF).
        if _suppress_console:
            for prefix in _SUPPRESS_PREFIXES:
                if data.startswith(prefix):
                    break
            else:
                # Not a suppressed prefix -- pass through to console.
                try:
                    self._original.write(data)
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
        else:
            try:
                self._original.write(data)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        return len(data)

    def flush(self) -> None:
        try:
            self._original.flush()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def isatty(self) -> bool:
        try:
            return self._original.isatty()
        except Exception:
            return False


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


def set_suppress_console(enabled: bool) -> None:
    """Toggle console suppression for [Coworker] diagnostic lines.

    When *enabled* is True (the default), lines starting with
    [🛠️Coworker] or [Coworker] are written to the log
    file only and do not appear in Blender's console.  Other output
    (user scripts, errors, tracebacks) is always shown.
    """
    global _suppress_console
    _suppress_console = enabled


# ---------------------------------------------------------------------------
# Blender policy-warning coalescing

_policy_warning_count = 0
_policy_warning_modules: set[str] = set()
_policy_summary_printed = False
_original_showwarning = None


def _coalescing_showwarning(message, category, filename, lineno, file=None, line=None):
    """Consolidate Blender 'Policy Violation' warnings into a single summary.

    Blender 5.3's addon sandbox emits one warning per vendored package
    (httpx, click, rich, pygments, ...) plus one for ``sys.path``. These
    flood the console. We count them and emit ONE summary line instead.
    Non-policy warnings pass through unchanged.
    """
    global _policy_warning_count, _policy_summary_printed
    text = str(message)
    if "Policy Violation" in text or "policy violation" in text:
        _policy_warning_count += 1
        # Try to extract the module name for the summary.
        # Common forms: "Policy Violation with top level module: httpx"
        #               "Policy Violation with sys.path: .../vendor/deps"
        mod = ""
        for token in ("top level module:", "sys.path:"):
            if token in text:
                mod = text.split(token, 1)[1].strip().split()[0].rstrip(",.")
                break
        if mod:
            _policy_warning_modules.add(mod)
        write("Suppressed policy warning: {:s}".format(text), level="WARN")
        # Print the summary once, after a short batch of warnings has had a
        # chance to accumulate. Using a count threshold coalesces the burst.
        if not _policy_summary_printed and _policy_warning_count >= 5:
            _policy_summary_printed = True
            print_policy_warning_summary()
        return  # Swallow — do NOT print to console.
    # Not a policy warning — pass through to the original handler.
    if _original_showwarning is not None:
        _original_showwarning(message, category, filename, lineno, file, line)


def install_policy_warning_filter() -> None:
    """Replace ``warnings.showwarning`` to coalesce policy-violation spam.

    The summary line is printed once automatically after the first burst of
    policy warnings (see ``_coalescing_showwarning``). Individual warnings
    are still recorded to the log file. Safe to call multiple times.
    """
    global _original_showwarning
    import warnings
    if _original_showwarning is None:
        _original_showwarning = warnings.showwarning
    warnings.showwarning = _coalescing_showwarning


def print_policy_warning_summary() -> None:
    """Print a one-line summary of suppressed policy warnings, if any."""
    if _policy_warning_count:
        mods = ", ".join(sorted(_policy_warning_modules)) or "vendor packages"
        print(
            "[🛠️Coworker] Blender policy: suppressed {:d} sandbox warning(s) "
            "for {:s} (expected; vendored deps still load — see log)".format(
                _policy_warning_count, mods
            )
        )
