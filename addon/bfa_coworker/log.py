# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Lightweight file-based logging for the Coworker add-on.

All modules already emit rich ``print()`` diagnostics prefixed with
``[Coworker]``. Those go to Blender's system console and vanish when
Blender closes. This module tees those messages to a rotating log file so
users (and bug reports) can retrieve them after the fact.

Usage::

    from . import log
    log.write("download started")            # file + console
    log.write("oops", level="ERROR")         # file + console, ERROR tag

The log lives at ``~/.cache/bfa_coworker/logs/coworker.log`` and rotates to
``coworker.log.1`` when it exceeds ~1 MB (single backup, no unbounded growth).

Secrets never reach it (#346): :func:`redact` replaces your API keys, the
Hugging Face token and the session token (handed in with :func:`set_secret`)
and anything that looks like ``Authorization: Bearer ...`` with ``***``.  The
console, the log file, session exports and Copy buttons all go through it, and
:func:`redact_data` does the same for saved chats and session memory (#418).
"""

__all__ = (
    "write",
    "get_log_path",
    "read_tail",
    "install_print_tee",
    "set_suppress_console",
    "set_secret",
    "redact",
    "redact_data",
)

import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_LOG_MAX_BYTES = 1_000_000  # ~1 MB before rotation.
_tee_installed = False
_suppress_console = True  # When True, routine [Coworker] lines are log-only (not Blender console).

# Prefixes identifying this addon's own diagnostic lines.  ASCII only so the
# output can never raise UnicodeEncodeError on a non-UTF-8 console (cp1252).
_ADDON_PREFIXES = ("[Coworker]",)
# Addon lines that indicate warnings -- always shown even when console
# suppression is on.
_WARN_PREFIXES = ("[Coworker][WARN]",)
# Severity markers that mark an addon line as a key issue (errors, hard
# failures) -- always shown even when console suppression is on.
_SEVERITY_MARKERS = ("ERROR", "FAILED", "FATAL", "TRACEBACK")

# ANSI colors.  Emoji prefixes in console output were replaced with colored
# ASCII tags: cyan for routine diagnostics, yellow for warnings, red for
# error-level lines.  Color is applied only when the console supports it (and
# never to the log file), and is opt-out via the NO_COLOR environment variable.
_ANSI = {
    "reset": "\033[0m",
    "cyan": "\033[36m",
    "yellow": "\033[33m",
    "red": "\033[31m",
}
_color_enabled: "bool | None" = None


def _supports_color() -> bool:
    """True when ANSI color should be emitted to the console.

    Requires a TTY, no ``NO_COLOR`` opt-out, and (on Windows) virtual-terminal
    processing enabled on the console handle.
    """
    global _color_enabled
    if _color_enabled is None:
        _color_enabled = False
        try:
            if os.environ.get("NO_COLOR"):
                return False
            stream = sys.stdout
            if stream is not None and getattr(stream, "isatty", None) and stream.isatty():
                if os.name == "nt":
                    try:
                        import ctypes
                        _k = ctypes.windll.kernel32
                        _k.SetConsoleMode(_k.GetStdHandle(-11), 7)
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
                _color_enabled = True
        except Exception:  # pylint: disable=broad-exception-caught
            _color_enabled = False
    return _color_enabled


def _color_for_line(line: str) -> str:
    """Return the ANSI color name for a console line ('' = no color)."""
    if line.startswith(_WARN_PREFIXES):
        return "yellow"
    if line.startswith(_ADDON_PREFIXES):
        upper = line.upper()
        if any(marker in upper for marker in _SEVERITY_MARKERS):
            return "red"
        return "cyan"
    return ""


def _colorize(data: str) -> str:
    """Wrap addon diagnostic lines in ANSI color for the console.

    Applied only on the console pass-through, never to the log file.  Non-addon
    output (Blender messages, user scripts, tracebacks) is left untouched.
    """
    if not data or not _supports_color() or "[Coworker]" not in data:
        return data
    if "\n" not in data:
        color = _color_for_line(data)
        return (_ANSI[color] + data + _ANSI["reset"]) if color else data
    out = []
    for line in data.split("\n"):
        color = _color_for_line(line)
        out.append((_ANSI[color] + line + _ANSI["reset"]) if color else line)
    return "\n".join(out)


def _should_pass_through(data: str) -> bool:
    """Return True when *data* should reach the Blender console.

    With console suppression on (Debug Mode off), only this addon's
    warning/error lines pass through, alongside everything that is not the
    addon's own output (Blender's messages, other addons, user scripts).
    Routine [Coworker] diagnostics go to the log file only. With
    suppression off (Debug Mode on), everything passes through.
    """
    if not _suppress_console:
        return True
    if data.startswith(_WARN_PREFIXES):
        return True
    if not data.startswith(_ADDON_PREFIXES):
        return True
    upper = data.upper()
    return any(marker in upper for marker in _SEVERITY_MARKERS)


# ---------------------------------------------------------------------------
# Secrets never leak (Security 2, #346)

REDACTED = "***"
# Shorter values are not hidden: a 3-letter "key" would blank out ordinary words.
_MIN_SECRET = 8
# Read from the environment as well: the session token given to scripted setups,
# and the Hugging Face token the model download falls back to.
_SECRET_ENV = ("BFACW_TOKEN", "HF_TOKEN", "HUGGINGFACE_TOKEN")
_named_secrets: dict[str, str] = {}
_secrets: tuple[str, ...] = ()          # longest first, swapped whole (read from any thread)
# Secrets we were not told about but recognise: an Authorization header (in any quoting),
# a bare bearer token, OpenAI-style "sk-..." keys and Hugging Face "hf_..." tokens.
_SECRET_PATTERNS = (
    re.compile(r"(?i)(\bauthorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic|token)\s+)[^\s\"',;]+"),
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"()\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"()\bhf_[A-Za-z0-9]{20,}"),
)


def _rebuild_secrets() -> None:
    global _secrets
    found = set(_named_secrets.values())
    found.update(os.environ.get(name, "").strip() for name in _SECRET_ENV)
    _secrets = tuple(sorted((s for s in found if len(s) >= _MIN_SECRET), key=len, reverse=True))


def set_secret(name: str, value: str) -> None:
    """Hide *value* from now on (``name`` is the slot it replaces: a changed key
    stops hiding the old one).  Empty clears the slot."""
    _named_secrets[name] = (value or "").strip()
    _rebuild_secrets()


def redact(text: str) -> str:
    """*text* with every known secret, and anything that looks like one, as ``***``."""
    if not text:
        return text
    for secret in _secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\g<1>" + REDACTED, text)
    return text


def redact_data(value: Any) -> Any:
    """A copy of *value* (JSON-like: dicts, lists, strings) with :func:`redact` applied
    to every string in it.  For what is written to disk: chats and session memory (#418)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_data(v) for v in value]
    return value


_rebuild_secrets()


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

    This does NOT print to the console -- callers that already ``print()``
    get file output automatically via :func:`install_print_tee`.
    """
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[{:s}] [{:5s}] {:s}\n".format(ts, level, redact(msg))
    with _lock:
        path = get_log_path()
        _rotate_if_needed(path)
        try:
            with open(str(path), "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass  # Disk full / read-only -- ignore, don't crash.


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
        # True when the previous write() was a suppressed addon line that
        # did not end with a newline. print() emits the payload and the
        # trailing newline as two separate write() calls, so the bare
        # newline chunk (no prefix) belongs to that suppressed line and
        # must be swallowed too (otherwise blank console lines appear).
        self._pending_suppressed_newline = False

    def write(self, data: str) -> int:
        size = len(data)
        data = redact(data)     # console and log file alike (#346)
        # Buffer and flush complete lines to the log.
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            stripped = line.strip()
            if stripped:
                write(stripped)
        # Also flush any remaining partial line immediately so that
        # crash logs capture everything written so far.
        if self._buf.strip():
            write(self._buf.strip())
            self._buf = ""
        if self._pending_suppressed_newline and not data.strip():
            # Trailing newline of a suppressed line: swallow it.
            self._pending_suppressed_newline = False
            return size
        # Pass through to the real console, but skip this addon's routine
        # [Coworker] diagnostics when console suppression is active
        # (debug mode OFF). Warnings/errors always pass through, as does
        # everything that is not this addon's own output.
        if _should_pass_through(data):
            try:
                self._original.write(_colorize(data))
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._pending_suppressed_newline = False
        else:
            self._pending_suppressed_newline = not data.endswith("\n")
        return size

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
    """Toggle console suppression for this addon's routine diagnostics.

    When *enabled* is True (the default, Debug Mode off), this addon's
    routine [Coworker]/[Coworker] lines are written to the log file only
    and do not appear in Blender's console. Warnings ([Coworker][WARN]) and
    error-level lines (ERROR/FAILED/FATAL/TRACEBACK) always pass through,
    as does everything that is not this addon's own output (user scripts,
    Blender's messages, tracebacks). When *enabled* is False (Debug Mode
    on), every addon line is shown.
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
        return  # Swallow -- do NOT print to console.
    # Not a policy warning -- pass through to the original handler.
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
            "[Coworker] Blender policy: suppressed {:d} sandbox warning(s) "
            "for {:s} (expected; vendored deps still load -- see log)".format(
                _policy_warning_count, mods
            )
        )
