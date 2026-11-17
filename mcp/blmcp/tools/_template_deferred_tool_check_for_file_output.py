# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Inline `_deferred_tool_check_for_file_output`.

__all__ = ()

import os
from collections.abc import Callable


def _deferred_tool_stopped_result(output_path: str, what: str = "render") -> dict[str, object]:
    """The answer for a job stopped in Blender before it finished (#527): ``status`` "stopped",
    with the partial image when Blender wrote one."""
    if os.path.exists(output_path):
        return {"status": "stopped", "filepath": output_path, "partial": True, "message": (
            "The {:s} was stopped in Blender before it finished. The image at filepath is the part it "
            "got to (fewer samples, or unfinished tiles). Don't start it again unless the user asks.".format(what))}
    return {"status": "stopped", "filepath": None, "partial": False, "message": (
        "The {:s} was stopped in Blender before it finished; Blender wrote no image. Don't start it again "
        "unless the user asks.".format(what))}


def _deferred_tool_check_for_file_output(
        job_type: str,
        output_path: str,
        restore_attrs: list[tuple[object, str, object]] | None = None,
) -> Callable[[], dict[str, object] | None]:
    """
    Return a deferred checker for a background job that writes a file.

    The returned callable follows the deferred tool convention
    (see ``deferred_tool.py``): returns ``None`` while the job is
    running, or a result ``dict`` once it completes.

    When *restore_attrs* is provided, each ``(obj, attr, value)`` tuple
    is restored via ``setattr`` after the job completes -- in order, and once:
    the list is emptied, as the checker and the job's end both restore (#524).
    One value that can't go back doesn't keep the others from it.

    A render stopped in Blender answers ``status`` "stopped", with the partial image if
    Blender wrote one (#527), instead of "ok".  Probed on 5.2.4: the job bar's X ends the
    job as if done (``render_complete``, the image written) and only Cycles' own status
    line says "Cancelled" (``render_stats``); ``render_cancel`` is Blender's other signal.
    A Cycles time limit ends with "Finished", not a stop.

    The checker carries ``job_kind`` / ``job_label``: the bridge hands a job
    still running after a while to its job registry (Tools 6, #309), and
    ``long_jobs`` matches the kind to Blender's render handlers.
    """
    import bpy  # pylint: disable=import-error,no-name-in-module

    stopped = [False]

    # Both may run on the render's own thread: they only set a flag.
    def on_cancel(*_args: object) -> None:
        stopped[0] = True

    def on_stats(*args: object) -> None:
        text = args[0] if args and isinstance(args[0], str) else ""
        # Cycles: "... | Cancelled" once a stop reached it.  Only the last segment counts, and all
        # of it: an object named "CancelButton" shows in "Synchronizing object | CancelButton".
        if text.rsplit("|", 1)[-1].strip() == "Cancelled":
            stopped[0] = True

    hooked: list[tuple[object, object]] = []
    if job_type == 'RENDER':
        app_handlers = getattr(bpy.app, "handlers", None)
        for name, fn in (("render_cancel", on_cancel), ("render_stats", on_stats)):
            handlers = getattr(app_handlers, name, None)
            try:
                handlers.append(fn)
                hooked.append((handlers, fn))
            except (AttributeError, TypeError):
                pass

    def unhook() -> None:
        while hooked:
            handlers, fn = hooked.pop()
            try:
                while fn in handlers:
                    handlers.remove(fn)
            except (AttributeError, TypeError, ValueError):
                pass

    def restore() -> None:
        unhook()
        while restore_attrs:
            obj, attr, value = restore_attrs.pop(0)
            try:
                setattr(obj, attr, value)
            except (AttributeError, ReferenceError, TypeError, ValueError, RuntimeError):
                pass

    def check_is_finished() -> dict[str, object] | None:
        if bpy.app.is_job_running(job_type):
            return None
        restore()
        if stopped[0]:
            return _deferred_tool_stopped_result(output_path)
        if os.path.exists(output_path):
            return {"status": "ok", "filepath": output_path}
        return {"status": "error", "message": (
            "The job ended without writing {:s} (it was cancelled or failed; Blender's Info "
            "editor has the reason).".format(os.path.basename(output_path)))}

    check_is_finished.job_kind = job_type  # type: ignore[attr-defined]
    check_is_finished.job_on_finish = restore  # type: ignore[attr-defined]  # also when cancelled
    check_is_finished.job_label = "{:s} {:s}".format(  # type: ignore[attr-defined]
        job_type.replace("_", " ").title(), os.path.basename(output_path))
    return check_is_finished
