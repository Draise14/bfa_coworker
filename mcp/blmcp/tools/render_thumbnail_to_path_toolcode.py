# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for low-quality thumbnail rendering.

The preview profile (``_template_render_preview_profile.py``), smaller: the longest edge
at most 320 px, 16 samples.  #524: the settings stay on until the render job is over (a
context manager put them back as soon as ``INVOKE_DEFAULT`` returned, before the job had
read them, so a "thumbnail" rendered at the scene's full size and samples).
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


class Result(NamedTuple):
    status: str
    filepath: str | None = None
    message: str | None = None
    caps: dict[str, Any] | None = None


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
    _PREVIEW_NO_CAMERA_MESSAGE, _THUMB_MAX_EDGE, _THUMB_MAX_SAMPLES, _THUMB_SIMPLIFY_SUBDIV, _THUMB_TEXTURE_LIMIT,
    _preview_busy_message, _preview_caps_text, _preview_no_camera, _preview_profile_apply,
    _preview_profile_restore,
)
# @include_end


def main(params: Params) -> Result | Callable[[], dict[str, object] | None]:
    import os
    import bpy  # pylint: disable=import-error,no-name-in-module

    use_deferred = not bpy.app.background

    # Resolve the output path inside the MCP scratch directory.
    output_path = os.path.join(bpy.app.tempdir, "bfa_coworker", os.path.basename(params.output_path))

    scene = bpy.context.scene
    rd = scene.render

    if use_deferred:
        busy = _preview_busy_message(bpy.app.is_job_running)
        if busy:
            return Result(status="error", message=busy)
    if _preview_no_camera(scene):
        return Result(status="error", message=_PREVIEW_NO_CAMERA_MESSAGE)
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass

    # The profile and `filepath` in one undo list, put back when the render is over.
    saved, caps = _preview_profile_apply(
        scene, max_edge=_THUMB_MAX_EDGE, max_samples=_THUMB_MAX_SAMPLES, simplify_subdiv=_THUMB_SIMPLIFY_SUBDIV,
        texture_limit=_THUMB_TEXTURE_LIMIT)
    saved.append((rd, "filepath", rd.filepath))
    rd.filepath = output_path
    text = "Thumbnail: {:s}.".format(_preview_caps_text(caps))

    def fail(message: str) -> Result:
        _preview_profile_restore(saved)
        return Result(status="error", message="{:s} {:s} Trying again won't make it cheaper: check the camera and "
                                              "the scene.".format(message.strip(), text), caps=caps)

    render_args = ('INVOKE_DEFAULT',) if use_deferred else ()
    try:
        ret = bpy.ops.render.render(*render_args, write_still=True)
    except RuntimeError as ex:
        return fail(str(ex))
    if 'CANCELLED' in ret:
        return fail("Blender didn't start the render (another render or bake may be running).")

    if use_deferred:
        base = _deferred_tool_check_for_file_output('RENDER', output_path, restore_attrs=saved)

        def check() -> dict[str, object] | None:
            result = base()          # restores `saved` once the job is over
            if result is None:
                return None
            result = dict(result)
            result["caps"] = caps
            # "stopped" (#527) and errors keep their own sentence first.
            result["message"] = text if result.get("status") == "ok" else "{:s} {:s}".format(
                str(result.get("message") or ""), text)
            return result

        for attr in ("job_kind", "job_label", "job_on_finish"):
            setattr(check, attr, getattr(base, attr, None))
        return check

    _preview_profile_restore(saved)
    return Result(status="ok", filepath=output_path, message=text, caps=caps)
