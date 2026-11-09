# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Inline `_deferred_tool_check_for_file_output`.

__all__ = ()

import os
from collections.abc import Callable


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
    is restored via ``setattr`` after the job completes.

    The checker carries ``job_kind`` / ``job_label``: the bridge hands a job
    still running after a while to its job registry (Tools 6, #309), and
    ``long_jobs`` matches the kind to Blender's render handlers.
    """
    import bpy  # pylint: disable=import-error,no-name-in-module

    def restore() -> None:
        if restore_attrs is not None:
            for obj, attr, value in restore_attrs:
                setattr(obj, attr, value)

    def check_is_finished() -> dict[str, object] | None:
        if bpy.app.is_job_running(job_type):
            return None
        restore()
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
