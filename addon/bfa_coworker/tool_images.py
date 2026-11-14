# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Pictures a tool makes, on their way to the model (#487).

The MCP server answers an image tool (the screenshot tools, and any tool
returning ``Image``) with image content blocks; a render tool answers with the
path of the file it wrote.  The Local / Remote loop turns either into a
``data:`` URI the same way an attached image is sent
(:mod:`chat_attachments`: the same size cap, downscaled by imbuf when over it),
and the turn shows it to a model that takes pictures in a user-role message
after the tool results (a ``tool`` message can't carry an image in the
OpenAI format).  A model that doesn't take pictures gets a one-line note in the
tool message instead: a picture was made, and where it is.

The stored history never holds the picture: the note message keeps only the
ids of its pictures, the URIs live on the thread's state for the turn (like
attachments) and the newest few are re-sent with each request.

Worker-thread safe: no ``bpy.context`` (imbuf and the temp folder only).
"""

__all__ = (
    "FILE_TOOLS",
    "KEEP",
    "blocks_in",
    "data_uri_from_block",
    "data_uri_from_file",
    "describe",
    "file_of",
    "keep",
    "save_copy",
    "sees_images",
)

import base64
import binascii
import json
import os
import tempfile
import threading
import uuid
from typing import Any

from . import chat_attachments

# Tools that answer with the path of a picture they wrote (no image block).
FILE_TOOLS = frozenset({"render_viewport_to_path", "render_thumbnail_to_path"})

# Pictures re-sent with each request of a turn (the newest; a vision encoder costs
# a fixed ~1500 tokens per picture, so a turn that takes ten screenshots must not
# resend all ten every time).
KEEP = 3

# MCP content block types that carry an image.
_IMAGE_TYPES = ("image", "image/png", "image/jpeg", "image/webp")
# Formats a vision backend decodes straight from a data URI (others are re-encoded).
_RAW_OK = ("image/png", "image/jpeg", "image/webp")
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/bmp": ".bmp",
        "image/tiff": ".tif", "image/gif": ".gif"}
_KEEP_COPIES = 10
_lock = threading.Lock()
_counter = [0]


def blocks_in(content: Any) -> list[tuple[str, str]]:
    """``(mime, base64)`` for each image block in an MCP ``content`` list."""
    out: list[tuple[str, str]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type", "") not in _IMAGE_TYPES:
            continue
        source = block.get("source") if isinstance(block.get("source"), dict) else {}
        data = str(block.get("data") or source.get("data") or "")
        if not data:
            continue
        kind = str(block.get("type"))
        mime = kind if kind.startswith("image/") else str(
            block.get("mimeType") or block.get("mime_type") or source.get("media_type") or "image/png")
        out.append((mime.lower(), data))
    return out


def _scratch_dir(name: str) -> str:
    path = os.path.join(chat_attachments._temp_dir() or tempfile.gettempdir(), name)  # pylint: disable=protected-access
    os.makedirs(path, exist_ok=True)
    return path


def data_uri_from_file(path: str, limit: int = chat_attachments.ATTACHMENT_LIMIT_BYTES) -> str | None:
    """*path* as a ``data:`` URI, downscaled to *limit* like an attachment; ``None`` when unreadable."""
    try:
        data, mime = chat_attachments._encode_file(path, limit)  # pylint: disable=protected-access
    except (AttributeError, OSError, RuntimeError, ValueError):
        return None
    if not data or not mime.startswith("image/"):
        return None
    return "data:{:s};base64,{:s}".format(mime, base64.b64encode(data).decode("ascii"))


def data_uri_from_block(mime: str, b64: str, limit: int = chat_attachments.ATTACHMENT_LIMIT_BYTES) -> str | None:
    """An image block as a ``data:`` URI under the attachment size cap; ``None`` when broken."""
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    if mime in _RAW_OK and len(raw) <= limit:
        return "data:{:s};base64,{:s}".format(mime, base64.b64encode(raw).decode("ascii"))
    # Too big, or a format a backend may not read: through the attachment encoder.
    try:
        with tempfile.TemporaryDirectory(prefix="bfacw_toolimg_", dir=chat_attachments._temp_dir()) as tmp:  # pylint: disable=protected-access
            path = os.path.join(tmp, "tool" + _EXT.get(mime, ".png"))
            with open(path, "wb") as fh:
                fh.write(raw)
            uri = data_uri_from_file(path, limit)
    except OSError:
        return None
    if uri is None or (len(uri) - uri.index(",") - 1) * 3 // 4 > 2 * limit:
        return None             # imbuf couldn't shrink it: a note beats a refused request
    return uri


def save_copy(mime: str, b64: str, tool: str) -> str:
    """Write an image block to the scratch folder (for the note); its path, or ``""``."""
    try:
        raw = base64.b64decode(b64, validate=False)
        with _lock:
            _counter[0] += 1
            n = _counter[0]
        directory = _scratch_dir("bfa_coworker_tool_images")
        path = os.path.join(directory, "{:s}_{:d}_{:d}{:s}".format(
            tool or "tool", os.getpid(), n, _EXT.get(mime, ".png")))
        with open(path, "wb") as fh:
            fh.write(raw)
        chat_attachments.prune_attachments(directory, keep=_KEEP_COPIES)
        return path
    except (binascii.Error, ValueError, OSError):
        return ""


def file_of(tool: str, result_text: str) -> str:
    """The picture a :data:`FILE_TOOLS` tool wrote (its answer's ``filepath``), or ``""``."""
    if tool not in FILE_TOOLS:
        return ""
    try:
        answer = json.loads(result_text)
    except (TypeError, ValueError):
        return ""
    if not isinstance(answer, dict) or answer.get("status") != "ok":
        return ""
    result = answer.get("result")
    path = str(result.get("filepath") or "") if isinstance(result, dict) and result.get("status", "ok") == "ok" else ""
    ext = os.path.splitext(path)[1].lower()
    if not path or ext not in chat_attachments.SUPPORTED_EXTENSIONS or not os.path.isfile(path):
        return ""
    return path


def describe(mime: str, size: int) -> str:
    """``PNG, 120 KB``."""
    kind = mime.split("/", 1)[-1].upper()
    return "{:s}, {:d} KB".format(kind, max(1, int(round(size / 1024.0))))


def sees_images(local_port: int | None, remote_flag: bool) -> bool:
    """The model of this turn takes pictures: Local asks llama-server (a vision
    projector is loaded), Remote follows the *Model Sees Images* preference."""
    if local_port is None:
        return bool(remote_flag)
    from . import llm_manager
    try:
        return bool(llm_manager.get_runtime_vision(local_port))
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def keep(store: dict[str, str], uri: str) -> str:
    """Keep *uri* in a turn's *store* (``id -> data URI``, the newest :data:`KEEP`);
    its id, unique across turns and sessions (a saved history's old note must never
    pick up a new picture)."""
    key = "img_" + uuid.uuid4().hex[:16]
    store[key] = uri
    while len(store) > KEEP:
        store.pop(next(iter(store)))
    return key
