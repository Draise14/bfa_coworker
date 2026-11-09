# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for rendering the current scene.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from collections.abc import Callable
from typing import NamedTuple


class Params(NamedTuple):
    output_path: str
    background: bool = False


class Result(NamedTuple):
    status: str
    filepath: str | None = None
    message: str | None = None
    job_id: str | None = None


# @include_begin: _template_deferred_tool_check_for_file_output.py
def _deferred_tool_check_for_file_output(
        job_type: str,
        output_path: str,
        restore_attrs: list[tuple[object, str, object]] | None = None,
) -> Callable[[], dict[str, object] | None]:
    return lambda: None
# @include_end


# @include_begin: _coworker_api_shared.py
# @include_end


def main(params: Params) -> Result | Callable[[], dict[str, object] | None]:
    import os
    import bpy  # pylint: disable=import-error,no-name-in-module

    use_deferred = not bpy.app.background

    # Resolve the output path inside the MCP scratch directory.
    output_path = os.path.join(bpy.app.tempdir, "bfa_coworker", os.path.basename(params.output_path))

    scene = bpy.context.scene

    # NOTE: `filepath` is set manually (not via context manager) because
    # `write_still` reads it after the render completes.
    # With `INVOKE_DEFAULT` a context manager would restore it too early.
    rd = scene.render
    orig_filepath = rd.filepath
    rd.filepath = output_path

    render_args = ('INVOKE_DEFAULT',) if use_deferred else ()

    if use_deferred and bpy.app.is_job_running('RENDER'):
        rd.filepath = orig_filepath
        return Result(status="error", message=(
            "A render is already running. Wait for it (get_job_status, or the job bar at the "
            "bottom), then render again."))
    # A file left by an earlier render would read as this one's result.
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass

    try:
        ret = bpy.ops.render.render(*render_args, write_still=True)
    except RuntimeError as ex:
        rd.filepath = orig_filepath
        return Result(status="error", message=str(ex))
    if 'CANCELLED' in ret:
        rd.filepath = orig_filepath
        return Result(status="error", message=(
            "Blender didn't start the render (another render or bake may be running). "
            "Try again when it has finished."))

    if use_deferred:
        check = _deferred_tool_check_for_file_output(
            'RENDER', output_path, restore_attrs=[(rd, "filepath", orig_filepath)],
        )
        if not params.background:
            # Held open; a render still going after a while gets a job id (deferred_tool).
            return check
        job_id = _coworker_api().start_job('RENDER', check.job_label, check, check.job_on_finish)
        return Result(status="ok", filepath=output_path, job_id=job_id, message=(
            "Rendering in the background. Call get_job_status(job_id) to wait for it; "
            "the image is written to filepath when it is done."))

    rd.filepath = orig_filepath
    return Result(status="ok", filepath=output_path)
