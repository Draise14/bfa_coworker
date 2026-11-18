# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
One answer to "may this bridge step change the scene now?" (#576, #591).

Two things make a scene-changing step unsafe, and both used to have their own gate:

* a render or bake job (``long_jobs``, #591) reads the scene on its own thread: the step is
  refused at once, with the job's own wording (it can take minutes, waiting is no use);
* the user is in the middle of a modal tool or a value drag (``ui_activity``, #576): the step
  waits a short, bounded time, then is refused.

:func:`status` returns the kind (``render``, ``bake``, ``modal``, ``drag``) and the text of the
"Not run: ..." answer.  Whether a step counts as changing the scene is decided in one place,
:func:`long_jobs.changes_scene_while_busy` (write tools, scene-touching reads, ``jump_to_*``,
and for Coworker's own code only the risky markers).  Every refusal starts with "Not run:", so
the loop's ``_step_refused`` counts it as no failure and runs no undo.  No ``bpy`` import at
module level.
"""

__all__ = (
    "WAITABLE",
    "changes_scene",
    "status",
)

from typing import Any

# Kinds worth waiting for (a drag or a tool ends in seconds); a render or bake is refused at once.
WAITABLE = frozenset({"modal", "drag"})


def changes_scene(tool: str, code: str = "", internal: bool = False) -> bool:
    """The one test for both gates (see :func:`long_jobs.changes_scene_while_busy`)."""
    from . import long_jobs
    return long_jobs.changes_scene_while_busy(tool, code, internal)


def status(tool: str, code: str, internal: bool, window_manager: Any = None) -> tuple[str, str]:
    """``(kind, text)`` for a step that must not run now, else ``("", "")``.  Never raises.

    *text* is the full "Not run: ..." answer for a render or bake, and the short reason
    ("the tool X is running in Blender") for a modal or drag (:func:`refusal` builds the answer)."""
    try:
        from . import long_jobs, ui_activity
        if not long_jobs.changes_scene_while_busy(tool, code, internal):
            return "", ""
        message = long_jobs.scene_busy_message(tool, code, internal=internal)
        if message:
            return ("bake" if long_jobs.busy_kind() == "OBJECT_BAKE" else "render"), message
        kind, _what = ui_activity.check(window_manager)
        if kind:
            return kind, ui_activity.busy_reason(window_manager)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return "", ""
