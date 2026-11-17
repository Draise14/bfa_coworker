# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for rendering one frame of the Video Sequencer's output (Tools 1, #304).

Renders the frame as a small JPEG in Coworker's temp folder, so a model can look at
it.  Every render setting it touches (output path and format, resolution percentage,
Post Processing > Sequencer, the current frame, the render window preference) is put
back when the render is done, stopped or failed.

#527: a scene strip renders its 3D scene with that scene's own settings, so every scene a
scene strip pulls in gets the preview profile's caps (samples, time limit, Simplify, the
passes) for the render, and they go back with the rest.  #541: ``render.render(scene=...)``
copies the context scene's frame onto that scene, so a scene the window doesn't show gave
the same image for every ``frame``; the context scene now shows *frame* while the operator
starts the render, and gets its own frame back right after.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from collections.abc import Callable
from typing import Any, NamedTuple


class Params(NamedTuple):
    frame: int | None = None
    max_size: int = 1024
    scene_name: str = ""


class Result(NamedTuple):
    status: str
    scene: str = ""
    frame: int | None = None
    filepath: str | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    job_id: str | None = None
    message: str | None = None
    partial: bool | None = None
    strip_scenes: list[str] | None = None


# The image is sent base64-encoded in one MCP message (1 MB): keep the file under 3/4 of it.
_IMAGE_SIZE_LIMIT_IN_BYTES = (1_048_576 * 3) // 4
_MAX_SIZE_RANGE = (64, 2048)
_JPEG_QUALITY = 85


# @include_begin: _template_deferred_tool_check_for_file_output.py
def _deferred_tool_check_for_file_output(
        job_type: str,
        output_path: str,
        restore_attrs: list[tuple[object, str, object]] | None = None,
) -> Callable[[], dict[str, object] | None]:
    return lambda: None
# @include_end


# @include_begin: _template_vse_shared.py
from blmcp.tools._template_vse_shared import (  # noqa: E402  (the real helpers, inlined in Blender)
    _vse_all_strips, _vse_resolve_scene,
)
# @include_end


# @include_begin: _template_render_preview_profile.py
from blmcp.tools._template_render_preview_profile import (  # noqa: E402  (the real helpers, inlined in Blender)
    _PREVIEW_MAX_SAMPLES, _preview_profile_apply_strip_scenes, _preview_profile_restore,
)
# @include_end


def _preview_percentage(res_x: int, res_y: int, max_size: int) -> int:
    """Resolution percentage so the longer side is at most *max_size* pixels (1-100)."""
    longest = max(int(res_x), int(res_y), 1)
    return max(1, min(100, (int(max_size) * 100) // longest))


def _clamp_max_size(value: Any) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        size = 1024
    return max(_MAX_SIZE_RANGE[0], min(_MAX_SIZE_RANGE[1], size))


def main(params: Params) -> Result | Callable[[], dict[str, object] | None]:
    import os
    import bpy  # pylint: disable=import-error,no-name-in-module

    use_deferred = not bpy.app.background
    scene, error = _vse_resolve_scene(params.scene_name)
    if scene is None:
        return Result(status="error", message=error)
    ed = scene.sequence_editor
    if ed is None or not _vse_all_strips(ed):
        return Result(status="error", scene=scene.name, message=(
            "Scene '{:s}' has no strips in the Sequencer, so there is nothing to preview. For a 3D "
            "render use render_viewport_to_path.".format(scene.name)))
    if use_deferred and bpy.app.is_job_running('RENDER'):
        return Result(status="error", scene=scene.name, message=(
            "A render is already running. Wait for it (get_job_status, or the job bar at the bottom), "
            "then call render_vse_preview again."))

    frame = int(params.frame) if params.frame is not None else int(scene.frame_current)
    rd = scene.render
    ims = rd.image_settings
    pct = _preview_percentage(rd.resolution_x, rd.resolution_y, _clamp_max_size(params.max_size))
    width, height = max(1, rd.resolution_x * pct // 100), max(1, rd.resolution_y * pct // 100)
    safe = "".join(c if c.isalnum() else "_" for c in scene.name)[:40]
    out_dir = os.path.join(bpy.app.tempdir, "bfa_coworker")
    os.makedirs(out_dir, exist_ok=True)
    output_path = os.path.join(out_dir, "vse_preview_{:s}_{:d}.jpg".format(safe, frame))

    # What to put back, in this order (the media type before the file format).
    saved: list[tuple[Any, str, Any]] = []

    def keep(owner: Any, attr: str) -> None:
        if hasattr(owner, attr):
            saved.append((owner, attr, getattr(owner, attr)))

    prefs = bpy.context.preferences
    for owner, attr in ((rd, "filepath"), (rd, "resolution_percentage"), (rd, "use_sequencer"),
                        (ims, "media_type"), (ims, "file_format"), (ims, "color_mode"), (ims, "quality"),
                        (scene, "frame_current")):
        keep(owner, attr)
    if use_deferred:
        keep(prefs.view, "render_display_type")
        keep(prefs, "is_dirty")
    # #527: the scenes scene strips pull in render under the preview caps too (own undo list).
    strip_saved, strip_scenes = _preview_profile_apply_strip_scenes(scene)
    done = [False]

    def restore() -> None:
        if done[0]:
            return
        done[0] = True
        for owner, attr, value in saved:
            try:
                setattr(owner, attr, value)
            except (AttributeError, ReferenceError, TypeError, ValueError, RuntimeError):
                pass
        _preview_profile_restore(strip_saved)

    capped = ""
    if strip_scenes:
        capped = (" Scene strips rendered their scenes ({:s}) under the preview caps (at most {:d} samples, "
                  "Simplify, no motion blur or compositing).".format(
                      ", ".join("'{:s}'".format(n) for n in strip_scenes), _PREVIEW_MAX_SAMPLES))

    def finish(stopped: bool = False) -> dict[str, object]:
        restore()
        exists = os.path.exists(output_path)
        if stopped:
            # #527: stopped in Blender -- not "ok"; the partial image when there is one.
            out = {"status": "stopped", "scene": scene.name, "frame": frame, "partial": exists,
                   "strip_scenes": strip_scenes or None, "message": (
                       "The preview of frame {:d} was stopped in Blender before it finished. {:s}Don't start "
                       "it again unless the user asks.".format(frame, "The image at filepath is the part it got to. "
                                                                    if exists else "Blender wrote no image. "))}
            if exists:
                out.update(filepath=output_path, width=width, height=height, size_bytes=os.path.getsize(output_path))
            return out
        if not exists:
            return {"status": "error", "scene": scene.name, "frame": frame, "message": (
                "The render ended without writing the preview (cancelled or failed; Blender's Info "
                "editor has the reason).")}
        size = os.path.getsize(output_path)
        message = "Frame {:d} of the Sequencer in '{:s}' ({:d}x{:d} JPEG).{:s}".format(
            frame, scene.name, width, height, capped)
        if size > _IMAGE_SIZE_LIMIT_IN_BYTES:
            message += " Too big to send ({:d} bytes); call again with a smaller max_size.".format(size)
        return {"status": "ok", "scene": scene.name, "frame": frame, "filepath": output_path,
                "width": width, "height": height, "size_bytes": size, "strip_scenes": strip_scenes or None,
                "message": message}

    try:
        rd.filepath = output_path
        rd.resolution_percentage = pct
        rd.use_sequencer = True
        if hasattr(ims, "media_type"):
            ims.media_type = 'IMAGE'
        ims.file_format = 'JPEG'
        ims.color_mode = 'RGB'
        ims.quality = _JPEG_QUALITY
        scene.frame_current = frame
        if use_deferred:
            # No render window popping up for a preview.
            prefs.view.render_display_type = 'NONE'
    except (AttributeError, TypeError, ValueError) as ex:
        restore()
        return Result(status="error", scene=scene.name, message="Could not set up the preview render: {:s}".format(
            str(ex)))
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass

    # #541: with ``scene=``, the render operator copies the *context* scene's current frame onto
    # that scene before it renders (probed on 5.2.4: a scene the window doesn't show rendered the
    # window's frame, whatever ``frame`` said; a ``temp_override(scene=...)`` doesn't change
    # it).  So the context scene shows *frame* while the operator starts the job, and gets its
    # own frame back as soon as it returned (the job has read the frame by then).
    ctx_scene = getattr(bpy.context, "scene", None)
    ctx_frame = None
    if ctx_scene is not None and ctx_scene != scene and int(getattr(ctx_scene, "frame_current", frame)) != frame:
        ctx_frame = int(ctx_scene.frame_current)
    error = ""
    try:
        if ctx_frame is not None:
            ctx_scene.frame_current = frame
        if use_deferred:
            ret = bpy.ops.render.render('INVOKE_DEFAULT', write_still=True, scene=scene.name)
        else:
            ret = bpy.ops.render.render(write_still=True, scene=scene.name)
    except RuntimeError as ex:
        error = str(ex)
    finally:
        if ctx_frame is not None:
            try:
                ctx_scene.frame_current = ctx_frame
            except (AttributeError, ReferenceError, TypeError, ValueError):
                pass
    if error:
        restore()
        return Result(status="error", scene=scene.name, message=error)
    if 'CANCELLED' in ret:
        restore()
        return Result(status="error", scene=scene.name, message=(
            "Blender didn't start the render (another render or bake may be running). Try again when it "
            "has finished."))
    if not use_deferred:
        return Result(**finish())

    base = _deferred_tool_check_for_file_output('RENDER', output_path)

    def check_is_finished() -> dict[str, object] | None:
        result = base()
        if result is None:
            return None
        return finish(stopped=result.get("status") == "stopped")

    def on_finish() -> None:
        restore()
        base_finish = getattr(base, "job_on_finish", None)
        if base_finish is not None:
            base_finish()

    check_is_finished.job_kind = getattr(base, "job_kind", 'RENDER')  # type: ignore[attr-defined]
    check_is_finished.job_on_finish = on_finish  # type: ignore[attr-defined]  # also when cancelled
    check_is_finished.job_label = "Sequencer preview, frame {:d}".format(frame)  # type: ignore[attr-defined]
    check_is_finished.job_stopped = getattr(base, "job_stopped", None)  # type: ignore[attr-defined]
    return check_is_finished
