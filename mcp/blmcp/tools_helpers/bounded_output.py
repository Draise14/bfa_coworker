# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Short tool results for every client (Tier 4, task "Engine 9").

A big result (a node-tree dump, an asset list, a long traceback) costs the
agent thousands of tokens, on every engine.  :func:`bounded` wraps each tool:
a result longer than :data:`LIMIT` characters becomes a short preview (head
and tail) plus an ``output_id``; the full text is saved, and the
``read_tool_output`` tool pages through it.

Saving never fails the tool: if the file can't be written, the preview says
so and the result is cut without a handle.
"""

__all__ = (
    "LIMIT",
    "bounded",
    "read",
)

import functools
import inspect
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

# Results up to this many characters pass unchanged (~2K tokens).
LIMIT = 8000
# Reading documentation is deliberate and its examples matter: a larger allowance.
_LIMITS = {"get_python_api_docs": 24000, "search_api_docs": 16000, "search_manual_docs": 16000,
           "read_tool_output": 1 << 30}
# A longer result keeps this much of its start and end (~2 KB together).
_HEAD, _TAIL = 1400, 600
# Saved outputs kept: the newest ones, for a day.
_KEEP_FILES, _KEEP_SECONDS = 200, 24 * 3600
# Fields up to this size stay whole in a shortened structured result.
_SMALL_FIELD = 300
# read_tool_output pages at most this much.
PAGE_MAX = 6000


def _dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    if not os.path.isdir(base):
        base = tempfile.gettempdir()
    return Path(base) / "bfa_coworker" / "tool_outputs"


def _prune(folder: Path) -> None:
    try:
        files = sorted(folder.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
        now = time.time()
        for i, f in enumerate(files):
            if i >= _KEEP_FILES or now - f.stat().st_mtime > _KEEP_SECONDS:
                f.unlink()
    except OSError:
        pass


def _save(text: str) -> str:
    """Save *text*; return its id ("" when it can't be saved)."""
    try:
        folder = _dir()
        folder.mkdir(parents=True, exist_ok=True)
        output_id = uuid.uuid4().hex[:12]
        (folder / (output_id + ".txt")).write_text(text, encoding="utf-8")
        _prune(folder)
        return output_id
    except OSError:
        return ""


def _preview(text: str) -> str:
    cut = len(text) - _HEAD - _TAIL
    return "{:s}\n... [{:d} characters left out] ...\n{:s}".format(text[:_HEAD], cut, text[-_TAIL:])


def shorten(result: Any, tool: str = "") -> Any:
    """*result* unchanged when it's short; else a preview with a handle to the rest."""
    if isinstance(result, str):
        text = result
    elif isinstance(result, dict):
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return result
    else:
        return result          # images, lists of content blocks: as they are
    if len(text) <= _LIMITS.get(tool, LIMIT):
        return result
    output_id = _save(text)
    how = ("Only part of it is shown. Read the rest with read_tool_output(output_id={!r}, offset=0) "
           "if you need it.".format(output_id) if output_id else
           "Only part of it is shown (the full text couldn't be saved).")
    if isinstance(result, str):
        return "[{:s} result: {:d} characters. {:s}]\n{:s}".format(tool or "Tool", len(text), how, _preview(text))
    return _shorten_fields(result, output_id, len(text), how)


def _size(value: Any) -> int:
    """Size as the client gets it (FastMCP sends JSON with an indent of 2)."""
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str, indent=2))
    except (TypeError, ValueError):
        return len(str(value))


def _shorten_fields(result: dict[str, Any], output_id: str, total: int, how: str) -> dict[str, Any]:
    """The same fields, each in its own type: small ones as they are, big strings as
    a head-and-tail preview, big lists as their first items.  The handle comes
    first, so a client that cuts the result again still keeps it."""
    short: dict[str, Any] = {"output_id": output_id, "total_chars": total, "note": how}
    big = [k for k, v in result.items() if _size(v) > _SMALL_FIELD]
    # The big fields share ~1.5 KB, so the whole answer stays near 2 KB with its handle.
    budget = max(_SMALL_FIELD, (_HEAD + _TAIL) * 3 // 4 // max(1, len(big)))
    cut: dict[str, str] = {}
    for key, value in result.items():
        if key not in big:
            short[key] = value
        elif isinstance(value, str):
            head, tail = budget * 2 // 3, budget // 3
            short[key] = "{:s}\n... [{:d} characters left out] ...\n{:s}".format(
                value[:head], len(value) - head - tail, value[-tail:])
            cut[key] = "{:d} of {:d} characters".format(head + tail, len(value))
        elif isinstance(value, list):
            kept, used = [], 0
            for item in value:
                used += _size(item)
                if kept and used > budget:
                    break
                kept.append(item)
            short[key] = kept
            cut[key] = "first {:d} of {:d} items".format(len(kept), len(value))
        else:
            text = json.dumps(value, ensure_ascii=False, default=str)
            short[key] = text[:budget] + "..."
            cut[key] = "{:d} of {:d} characters (as text)".format(budget, len(text))
    short["_shortened"] = cut
    return short


def bounded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool function so its result is :func:`shorten`-ed (keeps its signature)."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper_async(*args: Any, **kwargs: Any) -> Any:
            return shorten(await fn(*args, **kwargs), fn.__name__)
        return wrapper_async

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return shorten(fn(*args, **kwargs), fn.__name__)
    return wrapper


def read(output_id: str, offset: int = 0, limit: int = PAGE_MAX) -> dict[str, Any]:
    """A page of a saved output."""
    if not output_id or not all(c in "0123456789abcdef" for c in output_id):
        return {"status": "error", "error": "unknown output_id"}
    path = _dir() / (output_id + ".txt")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"status": "error", "error": "no saved output {!r} (outputs are kept for a day)".format(output_id)}
    limit = max(1, min(int(limit), PAGE_MAX))
    offset = max(0, int(offset))
    page = text[offset:offset + limit]
    end = offset + len(page)
    return {"status": "ok", "offset": offset, "next_offset": end if end < len(text) else None,
            "total_chars": len(text), "text": page}
