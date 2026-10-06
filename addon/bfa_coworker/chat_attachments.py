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
``imbuf``.  ``imbuf.write`` picks the output format from the buffer
itself, not from the file name, so the re-encode pins the buffer to PNG
-- a BMP/JPEG/TIFF source would otherwise be written back in its own
format while being advertised to the model as ``image/png``.  Encoding
never raises: an unencodable image degrades to ``None`` (or to its
original bytes) so a send can never die on an attachment.  ``bpy`` is
imported lazily inside the functions that need it so this module can be
unit-tested outside Blender.
"""

__all__ = (
    "ATTACHMENT_LIMIT_BYTES",
    "SUPPORTED_EXTENSIONS",
    "attachment_marker",
    "attachment_name",
    "attachments_dir",
    "capture_render",
    "capture_render_from_view",
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

# Name of the throwaway camera "Render" creates to shoot the current
# viewport view; it is removed again before the operator returns.
_RENDER_CAMERA_NAME = "BFACW Render Camera"

# "Render" exists to show the vision model what you are looking at, so it
# deliberately shoots cheap: a hard sample cap, a Cycles time limit as a
# backstop for a heavy scene, and a longest-edge cap (a vision encoder
# turns an image into a roughly fixed token count, so extra pixels buy
# nothing).  The user's own render settings are saved first and restored in
# a ``finally`` -- this only ever affects the throwaway capture, never a
# later F12 render.
_RENDER_MAX_EDGE = 1024
_RENDER_MAX_SAMPLES = 32
_RENDER_TIME_LIMIT_S = 30.0

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
    """Save the existing Render Result to a PNG in the attachments dir.

    Returns the file path, or ``None`` when nothing has been rendered
    yet (no Render Result datablock, or zero-sized).  Main thread only.
    """
    import bpy  # pylint: disable=import-error,no-name-in-module
    return _save_render_result(bpy.data.images.get("Render Result"))


def capture_render_from_view() -> str | None:
    """Render the current 3D Viewport view and save it to the attachments dir.

    A **temporary** camera is created, aligned to the viewport's view,
    used for a single render, then removed again -- the user's own
    camera (``scene.camera``) is restored and never moved, and no
    temporary object is left behind even if the render raises.

    Returns the saved PNG path, or ``None`` when there is no 3D
    Viewport to render from or the render produced nothing.  Main
    thread only (touches bpy and runs operators).
    """
    import bpy  # pylint: disable=import-error,no-name-in-module

    window = bpy.context.window
    screen = getattr(window, "screen", None)
    if screen is None:
        return None
    area = _find_view3d_area(screen)
    if area is None:
        return None
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    spaces = getattr(area, "spaces", None)
    space = spaces[0] if spaces else None
    region_3d = getattr(space, "region_3d", None)
    if region is None or space is None or region_3d is None:
        return None

    scene = bpy.context.scene
    previous_camera = scene.camera
    saved_settings = _clamp_render_settings(scene)
    cam_data = bpy.data.cameras.new(_RENDER_CAMERA_NAME)
    cam_obj = bpy.data.objects.new(_RENDER_CAMERA_NAME, cam_data)
    try:
        scene.collection.objects.link(cam_obj)
        scene.camera = cam_obj
        _copy_view_lens(cam_data, space)
        with bpy.context.temp_override(window=window, screen=screen,
                                       area=area, region=region,
                                       space_data=space, region_3d=region_3d):
            try:
                bpy.ops.view3d.camera_to_view()
            except RuntimeError:
                # The operator's poll can fail outside a normal viewport
                # context; fall back to copying the view matrix directly.
                cam_obj.matrix_world = region_3d.view_matrix.inverted()
            bpy.ops.render.render()
        return _save_render_result(bpy.data.images.get("Render Result"))
    except (AttributeError, RuntimeError, OSError, ValueError):
        return None
    finally:
        # Hand the scene back exactly as it was: the user's render
        # settings, their camera, and no leftover camera datablock.
        _restore_render_settings(saved_settings)
        scene.camera = previous_camera
        try:
            bpy.data.objects.remove(cam_obj, do_unlink=True)
        except (RuntimeError, ReferenceError):
            pass
        try:
            bpy.data.cameras.remove(cam_data)
        except (RuntimeError, ReferenceError):
            pass


def _clamp_render_settings(scene) -> list:
    """Lower the render cost for a one-off chat capture.

    Reduces the longest output edge to :data:`_RENDER_MAX_EDGE` (never
    upscaling), caps the sample count and sets a Cycles time limit, so a
    heavy scene cannot block Blender -- and the chat -- for minutes.

    Returns the undo list for :func:`_restore_render_settings`: only the
    attributes that actually changed are recorded, so restoring is exact
    and cannot disturb anything else.  Best-effort throughout: a render
    engine that does not expose an attribute is simply skipped.
    """
    render = getattr(scene, "render", None)
    saved: list = []
    if render is None:
        return saved

    def _snap(owner, attr, new_value) -> None:
        try:
            previous = getattr(owner, attr)
        except AttributeError:
            return
        if previous is None or previous == new_value:
            return
        setattr(owner, attr, new_value)
        saved.append((owner, attr, previous))

    try:
        percent = float(getattr(render, "resolution_percentage", 100) or 100)
        width = max(1, int((getattr(render, "resolution_x", 0) or 0)
                           * percent / 100.0))
        height = max(1, int((getattr(render, "resolution_y", 0) or 0)
                            * percent / 100.0))
        longest = max(width, height)
        if longest > _RENDER_MAX_EDGE:
            scale = _RENDER_MAX_EDGE / float(longest)
            _snap(render, "resolution_x", max(1, round(width * scale)))
            _snap(render, "resolution_y", max(1, round(height * scale)))
            _snap(render, "resolution_percentage", 100)
    except (AttributeError, TypeError, ValueError):
        pass

    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        samples = getattr(cycles, "samples", None)
        if isinstance(samples, int) and samples > _RENDER_MAX_SAMPLES:
            _snap(cycles, "samples", _RENDER_MAX_SAMPLES)
        limit = getattr(cycles, "time_limit", None)
        if isinstance(limit, (int, float)) and (
                not limit or limit > _RENDER_TIME_LIMIT_S):
            _snap(cycles, "time_limit", _RENDER_TIME_LIMIT_S)

    eevee = getattr(scene, "eevee", None)
    if eevee is not None:
        taa = getattr(eevee, "taa_render_samples", None)
        if isinstance(taa, int) and taa > _RENDER_MAX_SAMPLES:
            _snap(eevee, "taa_render_samples", _RENDER_MAX_SAMPLES)

    return saved


def _restore_render_settings(saved) -> None:
    """Put back exactly what :func:`_clamp_render_settings` changed."""
    for owner, attr, previous in saved:
        try:
            setattr(owner, attr, previous)
        except (AttributeError, ReferenceError, TypeError, ValueError):
            pass


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

    Never raises: an image that cannot be read or encoded yields
    ``None`` (the caller falls back to a plain-text send).

    Must be called on the MAIN thread (touches bpy).
    """
    if img is None:
        return None
    path = _image_filepath(img)
    dirty = bool(getattr(img, "is_dirty", False))
    if path and os.path.isfile(path) and not dirty:
        try:
            data, mime = _encode_file(path, limit)
        except (AttributeError, OSError, RuntimeError, ValueError):
            return None
    else:
        try:
            with tempfile.TemporaryDirectory(prefix="bfacw_attach_",
                                             dir=_temp_dir()) as tmpdir:
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


def persist_image(img) -> str:
    """Make sure *img*'s pixels exist as a file on disk; return that path.

    An image that already comes from an unmodified file on disk keeps it.
    Anything else -- a render, a generated or edited image, a packed one --
    is saved as a PNG copy in the attachments folder, so the attachment can
    always be reloaded even if Blender frees the datablock (undo, orphan
    cleanup on save).  Returns ``""`` when nothing could be written.
    Main thread only.
    """
    if img is None:
        return ""
    path = _image_filepath(img)
    if path and os.path.isfile(path) and not bool(getattr(img, "is_dirty", False)):
        return path
    try:
        out = _unique_path(attachments_dir(), "attached")
        img.save_render(str(out))
        if out.is_file():
            return str(out)
    except (AttributeError, RuntimeError, OSError, ValueError):
        pass
    return ""


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


def _save_render_result(img) -> str | None:
    """Save *img* (the Render Result) to a unique PNG, or ``None``.

    Shared by ``capture_render`` (the last render) and
    ``capture_render_from_view`` (a fresh viewport render).

    The write is the success test.  Blender 5.2 reports
    ``Render Result.size`` as ``(0, 0)`` even after a render that did
    produce pixels, while saving a result that was never rendered
    raises ``RuntimeError`` ("does not have any image data").  So the
    reported size is deliberately *not* used to decide whether anything
    has been rendered -- ``save_render`` and the file on disk are.
    """
    if img is None:
        return None
    path = _unique_path(attachments_dir(), "render")
    try:
        img.save_render(str(path))
    except (RuntimeError, OSError, ValueError):
        # Nothing has been rendered yet, or the write failed.
        return None
    try:
        written = path.is_file() and path.stat().st_size > 0
    except OSError:
        written = False
    if not written:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    prune_attachments()
    return str(path)


def _find_view3d_area(screen):
    """The active 3D Viewport, else the largest one on *screen*, else ``None``."""
    import bpy  # pylint: disable=import-error,no-name-in-module
    area = bpy.context.area
    if area is not None and area.type == "VIEW_3D":
        return area
    candidates = [a for a in screen.areas if a.type == "VIEW_3D"]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.width * a.height)


def _copy_view_lens(cam_data, space) -> None:
    """Match *cam_data*'s lens to the viewport's, best-effort.

    ``view3d.camera_to_view`` positions the camera; copying the lens
    keeps the framing close to what the user sees.  A missing or zero
    lens (stub spaces in tests) leaves the camera's default in place.
    """
    try:
        lens = float(getattr(space, "lens", 0.0) or 0.0)
    except (AttributeError, TypeError, ValueError):
        return
    if lens > 0.0:
        cam_data.lens = lens


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


def _temp_dir() -> str | None:
    """Blender's temp directory, or ``None`` for the system temp dir.

    ``bpy.app.tempdir`` honours *Preferences > File Paths > Temporary
    Files*.  Scratch encodes here can be several MB (the full-size
    attempt), so preferring Blender's configured temp keeps them off a
    small or full system drive when the user has moved it.
    """
    try:
        import bpy  # pylint: disable=import-error,no-name-in-module
        tempdir = str(getattr(bpy.app, "tempdir", "") or "")
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return None
    if tempdir and os.path.isdir(tempdir):
        return tempdir
    return None


def _read_raw(filepath: str) -> tuple[bytes, str]:
    """Original file bytes with their real mime -- the last-resort fallback."""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        with open(filepath, "rb") as fh:
            return fh.read(), _MIME_BY_EXT.get(ext, "image/png")
    except OSError:
        return b"", "application/octet-stream"


def _encode_file(path: str, limit: int) -> tuple[bytes, str]:
    """Return ``(bytes, mime)`` for *path*, downscaling to *limit*.

    png/jpg/webp that already fit are passed through untouched (no
    quality loss, no re-encode).  Everything else -- oversized files
    and formats a vision backend may not decode -- is re-encoded to
    PNG through imbuf, downscaling until it fits.  Never raises: an
    unreadable file or unusable scratch space falls back to the
    original bytes and mime.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    if ext in _RAW_OK_EXT and 0 <= size <= limit:
        try:
            with open(path, "rb") as fh:
                return fh.read(), _MIME_BY_EXT[ext]
        except OSError:
            pass
    try:
        with tempfile.TemporaryDirectory(prefix="bfacw_scale_",
                                         dir=_temp_dir()) as tmpdir:
            return _downscale_to_limit(tmpdir, path, limit)
    except (AttributeError, OSError, RuntimeError, ValueError):
        return _read_raw(path)


def _downscale_to_limit(tmpdir: str, filepath: str, size_limit: int) -> tuple[bytes, str]:
    """Re-encode *filepath* as PNG, downscaling until it fits *size_limit*.

    Slimmed copy of the MCP screenshot downscaler (the addon must not
    import from ``mcp.blmcp``): encode, then walk dimension divisors
    2..64 until the output fits, never going below 64 px.  Returns
    ``(bytes, "image/png")``.  Falls back to the original file bytes
    and mime when ``imbuf`` is unavailable or cannot load the file, and
    when every write fails (no space in the scratch directory) -- it
    keeps this function total instead of raising.
    """
    try:
        import imbuf  # type: ignore[import-not-found]  # pylint: disable=import-error,no-name-in-module
    except ImportError:
        return _read_raw(filepath)
    # Capability guard, not just import guard: outside Blender (tests)
    # a stub ``imbuf`` may exist without the load/write API.
    if not hasattr(imbuf, "load") or not hasattr(imbuf, "write"):
        return _read_raw(filepath)
    try:
        im = imbuf.load(filepath)
    except (OSError, RuntimeError, ValueError):
        return _read_raw(filepath)
    if im is None:
        return _read_raw(filepath)

    # ``imbuf.write`` derives the output format from the buffer itself,
    # NOT from the filepath extension: an ImBuf loaded from a BMP/JPEG/
    # TIFF round-trips in its own format (and blows past the limit as an
    # uncompressed BMP), while the mime we advertise is image/png.  Pin
    # the buffer to PNG so ``downscaled.png`` really is a PNG.  Copies
    # inherit ``file_type``.
    # getattr/setattr: ``file_type`` exists at runtime but is absent from
    # the bundled ``imbuf`` stubs (and from the API reference), so a
    # direct attribute access would be a hard type error.
    try:
        if getattr(im, "file_type", None) != "PNG":
            setattr(im, "file_type", "PNG")
    except (AttributeError, TypeError, ValueError):
        pass

    filepath_out = os.path.join(tmpdir, "downscaled.png")

    def _write(buf) -> bytes | None:
        """Write *buf* to the scratch PNG; ``None`` when the write fails."""
        try:
            imbuf.write(buf, filepath=filepath_out)
            with open(filepath_out, "rb") as fh:
                return fh.read()
        except (OSError, RuntimeError, ValueError):
            return None

    try:
        # NOTE: no HiDPI pixel-size pre-shrink (the MCP helper has one
        # because it captures the screen; attachment files are not
        # device-pixel screenshots) -- the byte cap below is enough.
        data = _write(im)
        if data is not None and len(data) <= size_limit:
            return data, "image/png"
        smallest = data
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
            if data is None:
                continue
            if len(data) <= size_limit:
                return data, "image/png"
            if smallest is None or len(data) < len(smallest):
                smallest = data
        if smallest is None:
            # Not one encode landed on disk (e.g. the scratch drive is
            # full): keep the attachment alive with the original bytes.
            return _read_raw(filepath)
        # Best effort: even the smallest allowed scale may still be
        # over the limit for pathological content; return the smallest
        # encode we produced rather than dropping the attachment.
        return smallest, "image/png"
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
