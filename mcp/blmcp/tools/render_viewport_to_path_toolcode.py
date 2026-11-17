# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for rendering the current scene.

``quality`` (#524): ``"preview"`` (the default) renders under the preview profile
(``_template_render_preview_profile.py``: small, few samples, no compositing...);
``"final"`` with the scene's own settings; ``"viewport"`` is a quick solid-shaded
OpenGL render from the scene camera, under the same size cap.  Whatever the profile
changed goes back when the render is over: done, failed or cancelled.  A render stopped
in Blender answers ``status`` "stopped" with the partial image, if any (#527).
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from collections.abc import Callable
from typing import Any, NamedTuple


class Params(NamedTuple):
    output_path: str
    background: bool = False
    quality: str = "preview"


class Result(NamedTuple):
    status: str
    filepath: str | None = None
    message: str | None = None
    job_id: str | None = None
    quality: str | None = None
    caps: dict[str, Any] | None = None


_QUALITIES = ("preview", "final", "viewport")


# @include_begin: _template_deferred_tool_check_for_file_output.py
def _deferred_tool_check_for_file_output(
        job_type: str,
        output_path: str,
        restore_attrs: list[tuple[object, str, object]] | None = None,
) -> Callable[[], dict[str, object] | None]:
    return lambda: None
# @include_end


# @include_begin: _template_render_preview_profile.py
from blmcp.tools._template_render_preview_profile import (  # noqa: E402  (the real helpers, inlined in Blender)
    _PREVIEW_NO_CAMERA_MESSAGE, _preview_busy_message, _preview_caps_text, _preview_no_camera,
    _preview_output_size, _preview_profile_apply, _preview_profile_restore,
)
# @include_end


# @include_begin: _coworker_api_shared.py
# @include_end


def _quality_text(quality: str, caps: dict[str, Any], size: tuple[int, int]) -> str:
    """What the render ran with, for the answer (and, on a failure, the agent's next step)."""
    if quality == "final":
        return "Quality: final (the scene's own settings, {:d}x{:d} px).".format(*size)
    if quality == "viewport":
        return ("Quality: viewport (solid shading from the camera, {:d}x{:d} px PNG; no materials or "
                "lighting).".format(*(caps.get("resolution") or size)))
    return "Quality: preview -- {:s}.".format(_preview_caps_text(caps))


def _failure_hint(quality: str) -> str:
    if quality == "final":
        return (" Don't start the same final render again: to look at the scene use quality='preview', "
                "and ask the user before another final render.")
    if quality == "viewport":
        return " Trying again won't help: check the camera and the scene."
    return (" A preview is already the cheap render, so trying again won't help: check the camera and "
            "the scene, or use quality='viewport'.")


def main(params: Params) -> Result | Callable[[], dict[str, object] | None]:
    import os
    import bpy  # pylint: disable=import-error,no-name-in-module

    quality = str(params.quality or "preview").strip().lower()
    if quality not in _QUALITIES:
        return Result(status="error", message=(
            "quality is 'preview' (the default, for looking), 'final' (the scene's own settings, only "
            "when the user asked for the real render) or 'viewport' (quick solid shading), not "
            "{!r}.".format(params.quality)))

    use_deferred = not bpy.app.background

    # Resolve the output path inside the MCP scratch directory.
    output_path = os.path.join(bpy.app.tempdir, "bfa_coworker", os.path.basename(params.output_path))

    scene = bpy.context.scene
    rd = scene.render

    if use_deferred:
        busy = _preview_busy_message(bpy.app.is_job_running)
        if busy:
            return Result(status="error", message=busy, quality=quality)
    if _preview_no_camera(scene):
        return Result(status="error", message=_PREVIEW_NO_CAMERA_MESSAGE, quality=quality)

    # A file left by an earlier render would read as this one's result.
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass

    # The preview profile, then `filepath`, all in one undo list.  NOTE: nothing is put back
    # by a context manager: with `INVOKE_DEFAULT` the render job reads the settings on its own
    # thread after the operator returned, and `write_still` reads `filepath` when it is done.
    if quality == "final":
        saved: list[tuple[Any, str, Any]] = []
        caps: dict[str, Any] = {}
    else:
        saved, caps = _preview_profile_apply(scene)
    saved.append((rd, "filepath", rd.filepath))
    rd.filepath = output_path
    text = _quality_text(quality, caps, _preview_output_size(rd))

    def fail(message: str) -> Result:
        _preview_profile_restore(saved)
        return Result(status="error", message="{:s} {:s}{:s}".format(message.strip(), text, _failure_hint(quality)),
                      quality=quality, caps=caps or None)

    if quality == "viewport":
        # Solid shading from the scene camera; quick enough to run in place, no job.
        try:
            if bpy.context.window is None and not bpy.app.background and bpy.context.window_manager.windows:
                with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                    ret = bpy.ops.render.opengl(write_still=True, view_context=False)
            else:
                ret = bpy.ops.render.opengl(write_still=True, view_context=False)
        except RuntimeError as ex:
            return fail(str(ex))
        if 'CANCELLED' in ret or not os.path.exists(output_path):
            return fail("The viewport render wrote no image.")
        _preview_profile_restore(saved)
        return Result(status="ok", filepath=output_path, message=text, quality=quality, caps=caps)

    render_args = ('INVOKE_DEFAULT',) if use_deferred else ()
    try:
        ret = bpy.ops.render.render(*render_args, write_still=True)
    except RuntimeError as ex:
        return fail(str(ex))
    if 'CANCELLED' in ret:
        return fail("Blender didn't start the render (another render or bake may be running, or the "
                    "output is a movie format).")

    if use_deferred:
        base = _deferred_tool_check_for_file_output('RENDER', output_path, restore_attrs=saved)

        def check() -> dict[str, object] | None:
            result = base()          # restores `saved` once the job is over
            if result is None:
                return None
            result = dict(result)
            result["quality"] = quality
            result["caps"] = caps or None
            if result.get("status") == "ok":
                result["message"] = text
            elif result.get("status") == "stopped":      # #527: stopped in Blender, maybe a partial image
                result["message"] = "{:s} {:s}".format(str(result.get("message") or ""), text)
            else:
                result["message"] = "{:s} {:s}{:s}".format(str(result.get("message") or ""), text,
                                                          _failure_hint(quality))
            return result

        for attr in ("job_kind", "job_label", "job_on_finish"):
            setattr(check, attr, getattr(base, attr, None))
        if not params.background:
            # Held open; a render still going after a while gets a job id (deferred_tool).
            return check
        job_id = _coworker_api().start_job('RENDER', check.job_label, check, check.job_on_finish)
        return Result(status="ok", filepath=output_path, job_id=job_id, quality=quality, caps=caps or None,
                      message=("Rendering in the background. Call get_job_status(job_id) to wait for it; "
                               "the image is written to filepath when it is done. " + text))

    _preview_profile_restore(saved)
    return Result(status="ok", filepath=output_path, message=text, quality=quality, caps=caps or None)
