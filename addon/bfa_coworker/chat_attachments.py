# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Chat image attachments (issue #88, Tier 3k).

Turns an image datablock, a file on disk, or a fresh capture (Render
Result / screen) into a base64 ``data:`` URI that a vision model can
read, and keeps the files the captures write tidy.

Design constraints this module exists to honour:

* **Encode on the main thread.**  A conversation turn runs on a worker
  thread that must never touch ``bpy`` (it races Blender's global Python
  context counter), so ``image_to_data_uri`` is called from the send
  operator's ``execute()`` -- before the worker starts -- and only ever
  receives an already-materialised datablock or path.
* **No imports from ``mcp.blmcp``.**  The addon ships standalone; the
  imbuf downscaler is a slimmed copy of the MCP screenshot helper
  rather than a shared import.
* **Stored history stays plain text.**  Data URIs live on
  ``AgentState.user_attachments`` for the request build only -- the
  saved chat JSON never carries base64 (see ``ui_chat._save_chat_history``).

Payloads are capped at :data:`ATTACHMENT_LIMIT_BYTES` (the same 1 MiB
base64 budget the MCP screenshot tools use) by downscaling with
``imbuf``.  ``bpy`` is imported lazily inside the functions that need
it so this module can be unit-tested outside Blender.
"""

__all__ = (
    "ATTACHMENT_LIMIT_BYTES",
    "SUPPORTED_EXTENSIONS",
    "attachment_marker",
    "attachment_name",
    "attachments_dir",
    "capture_render",
    "capture_screen",
    "image_to_data_uri",
    "load_image_file",
    "prune_attachments",
)

import base64
import os
import tempfile
import time
from pathlib import Path

# Base64 expands bytes by 4/3, so cap the RAW bytes at 3/4 of a MiB to
# keep the encoded data URI at ~1 MiB -- exactly the MCP screenshot
# budget.  A vision encoder turns an image into a roughly fixed token
# count, so past this size extra pixels buy nothing but payload weight.
ATTACHMENT_LIMIT_BYTES = (1_048_576 * 3) // 4

# Newest N capture files kept in the attachments directory; older
# captures are pruned after each new one (their datablocks keep their
# in-memory pixels, only the file goes away).
_KEEP_ATTACHMENTS = 10

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

# Extensions a vision backend can be trusted to decode straight from a
# data URI.  Anything else (bmp/tif) is re-encoded to PNG even when it
# already fits the size cap.
_RAW_OK_EXT = (".png", ".jpg", ".jpeg", ".webp")

# Extensions the file browser, drag-and-drop FileHandler, and
# ``load_image_file`` accept.
SUPPORTED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


def attachments_dir() -> Path:
    """Return the directory capture PNGs are written to.

    Mirrors ``ui_chat._chat_history_dir()/attachments`` -- computed
    here instead of imported to avoid a circular import (ui_chat will
    import this module for the send path).
    """
    import bpy  # pylint: disable=import-error,no-name-in-module
    base = Path(bpy.utils.user_resource("SCRIPTS")) / "bfa_coworker_chat_history" / "attachments"
    base.mkdir(parents=True, exist_ok=True)
    return base


def attachment_name(img) -> str:
    """Display name for an attached image datablock (file basename)."""
    path = _image_filepath(img)
    if path:
        return os.path.basename(path)
    return str(getattr(img, "name", "") or "image")


def attachment_marker(names) -> str:
    """Return the ``[attached: ...]`` line stored with a user message.

    Stored history keeps plain text -- the data URI is injected only
    into the sent request -- so this marker is what the chat panel and
    later turns see.  Empty input yields an empty string.
    """
    clean = [str(n) for n in (names or []) if n]
    if not clean:
        return ""
    return "[attached: {:s}]".format(", ".join(clean))


def load_image_file(filepath: str):
    """Load *filepath* as an image datablock for the chat socket.

    Returns the (possibly reused) ``bpy.types.Image``, or ``None`` when
    the path is missing or not a supported image type.  Main thread
    only.
    """
    if not filepath or not os.path.isfile(filepath):
        return None
    if os.path.splitext(filepath)[1].lower() not in _MIME_BY_EXT:
        return None
    import bpy  # pylint: disable=import-error,no-name-in-module
    try:
        return bpy.data.images.load(filepath, check_existing=True)
    except (RuntimeError, OSError, ValueError):
        return None


def capture_render() -> str | None:
    """Save the Render Result to a PNG in the attachments dir.

    Returns the file path, or ``None`` when nothing has been rendered
    yet (no Render Result datablock, or zero-sized).  Main thread only.
    """
    import bpy  # pylint: disable=import-error,no-name-in-module
    img = bpy.data.images.get("Render Result")
    if img is None:
        return None
    try:
        width, height = img.size[0], img.size[1]
    except (IndexError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None  # Nothing rendered yet.
    path = _unique_path(attachments_dir(), "render")
    try:
        img.save_render(str(path))
    except (RuntimeError, OSError, ValueError):
        return None
    prune_attachments()
    if not path.is_file():
        return None
    return str(path)


def capture_screen(target: str = "WINDOW") -> str | None:
    """Screenshot the whole window or the largest 3D Viewport.

    *target* is ``"WINDOW"`` or ``"VIEW_3D"``.  Same call pattern as
    the MCP screenshot tools (``bpy.ops.screen.screenshot[_area]`` with
    an explicit ``filepath``, so no file dialog opens).  Returns the
    saved PNG path, or ``None`` in background mode / with no window /
    with no 3D Viewport to capture.  Main thread only.
    """
    import bpy  # pylint: disable=import-error,no-name-in-module
    if bpy.app.background:
        return None
    window = bpy.context.window
    if window is None:
        return None
    path = _unique_path(attachments_dir(), "screen")
    if target == "VIEW_3D":
        # Prefer the context's area when it is a 3D Viewport, otherwise
        # the largest one -- mirrors the MCP area-screenshot tool.
        area = bpy.context.area
        if area is None or area.ui_type != "VIEW_3D":
            view_areas = sorted(
                (a for a in window.screen.areas if a.ui_type == "VIEW_3D"),
                key=lambda a: -(a.width * a.height),
            )
            if not view_areas:
                return None
            area = view_areas[0]
        if area is None:
            return None
        with bpy.context.temp_override(window=window, area=area):
            try:
                bpy.ops.screen.screenshot_area(filepath=str(path))
            except RuntimeError:
                return None
    else:
        try:
            bpy.ops.screen.screenshot(filepath=str(path))
        except RuntimeError:
            return None
    if not path.is_file():
        return None
    prune_attachments()
    return str(path)


def image_to_data_uri(img, limit: int = ATTACHMENT_LIMIT_BYTES) -> str | None:
    """Encode an image datablock as a base64 ``data:`` URI.

    Fast path: an unmodified datablock whose source file still exists
    on disk is read directly (no re-encode, original mime preserved).
    Otherwise -- Render Result, packed, generated, edited, or a missing
    file -- the datablock is saved to a temporary PNG first.  Both paths
    downscale to *limit* bytes with imbuf.

    Must be called on the MAIN thread (touches bpy).
    """
    if img is None:
        return None
    path = _image_filepath(img)
    dirty = bool(getattr(img, "is_dirty", False))
    if path and os.path.isfile(path) and not dirty:
        data, mime = _encode_file(path, limit)
    else:
        try:
            with tempfile.TemporaryDirectory(prefix="bfacw_attach_") as tmpdir:
                out = os.path.join(tmpdir, "attachment.png")
                # The approved slow path: save_render works for every
                # datablock that has pixels, including Render Result
                # (which has no filepath and cannot use save()).
                img.save_render(out)
                data, mime = _encode_file(out, limit)
        except (AttributeError, RuntimeError, OSError, ValueError):
            return None
    if not data:
        return None
    return "data:{:s};base64,{:s}".format(mime, base64.b64encode(data).decode("ascii"))


def prune_attachments(directory=None, keep: int = _KEEP_ATTACHMENTS) -> None:
    """Keep only the *keep* newest files in the attachments directory.

    Best-effort: a missing directory or an unlink race is swallowed --
    pruning is housekeeping, never a reason to fail a capture.
    """
    if directory is None:
        try:
            directory = attachments_dir()
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            return
    directory = Path(directory)
    try:
        files = sorted(
            (f for f in directory.iterdir() if f.is_file()),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Internals


def _image_filepath(img) -> str:
    """Absolute on-disk path for *img*, or ``""`` when there is none."""
    raw = str(getattr(img, "filepath", "") or "")
    if not raw:
        return ""
    if raw.startswith("//"):
        # Blend-relative path -- needs bpy to resolve against the .blend.
        try:
            import bpy  # pylint: disable=import-error,no-name-in-module
            raw = bpy.path.abspath(raw)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            return ""
    return raw


def _encode_file(path: str, limit: int) -> tuple[bytes, str]:
    """Return ``(bytes, mime)`` for *path*, downscaling to *limit*.

    png/jpg/webp that already fit are passed through untouched (no
    quality loss, no re-encode).  Everything else -- oversized files
    and formats a vision backend may not decode -- is re-encoded to
    PNG through imbuf, downscaling until it fits.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in _RAW_OK_EXT and os.path.getsize(path) <= limit:
        with open(path, "rb") as fh:
            return fh.read(), _MIME_BY_EXT[ext]
    with tempfile.TemporaryDirectory(prefix="bfacw_scale_") as tmpdir:
        return _downscale_to_limit(tmpdir, path, limit)


def _downscale_to_limit(tmpdir: str, filepath: str, size_limit: int) -> tuple[bytes, str]:
    """Re-encode *filepath* as PNG, downscaling until it fits *size_limit*.

    Slimmed copy of the MCP screenshot downscaler (the addon must not
    import from ``mcp.blmcp``): encode, then walk dimension divisors
    2..64 until the output fits, never going below 64 px.  Returns
    ``(bytes, "image/png")``.  Falls back to the original file bytes
    and mime when ``imbuf`` is unavailable or cannot load the file --
    impossible inside Blender, but it keeps this function total.
    """

    def _read_raw() -> tuple[bytes, str]:
        ext = os.path.splitext(filepath)[1].lower()
        with open(filepath, "rb") as fh:
            return fh.read(), _MIME_BY_EXT.get(ext, "image/png")

    try:
        import imbuf  # type: ignore[import-not-found]  # pylint: disable=import-error,no-name-in-module
    except ImportError:
        return _read_raw()
    # Capability guard, not just import guard: outside Blender (tests)
    # a stub ``imbuf`` may exist without the load/write API.
    if not hasattr(imbuf, "load") or not hasattr(imbuf, "write"):
        return _read_raw()
    im = imbuf.load(filepath)
    if im is None:
        return _read_raw()

    filepath_out = os.path.join(tmpdir, "downscaled.png")

    def _write(buf) -> bytes:
        imbuf.write(buf, filepath=filepath_out)
        with open(filepath_out, "rb") as fh:
            return fh.read()

    try:
        # NOTE: no HiDPI pixel-size pre-shrink (the MCP helper has one
        # because it captures the screen; attachment files are not
        # device-pixel screenshots) -- the byte cap below is enough.
        data = _write(im)
        if len(data) <= size_limit:
            return data, "image/png"
        orig_w, orig_h = im.size
        for divisor in (2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64):
            new_w, new_h = orig_w // divisor, orig_h // divisor
            if new_w <= 64 or new_h <= 64:
                break
            im_copy = im.copy()
            try:
                im_copy.resize((new_w, new_h), method="BILINEAR")
                data = _write(im_copy)
            finally:
                im_copy.free()
            if len(data) <= size_limit:
                return data, "image/png"
        # Best effort: even the smallest allowed scale may still be
        # over the limit for pathological content; return the smallest
        # encode we produced rather than dropping the attachment.
        return data, "image/png"
    finally:
        im.free()


def _unique_path(directory: Path, prefix: str) -> Path:
    """Return a not-yet-existing ``<prefix>_<timestamp>[_n].png`` path."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    candidate = directory / f"{prefix}_{stamp}.png"
    index = 1
    while candidate.exists():
        candidate = directory / f"{prefix}_{stamp}_{index}.png"
        index += 1
    return candidate
