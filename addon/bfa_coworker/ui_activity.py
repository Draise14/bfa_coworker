# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Is the user in the middle of a mouse drag or a modal tool?  (#576)

Dragging a slider (a Geometry Nodes modifier's input, a property field) is a *button*
interaction, not an operator: ``window.modal_operators`` stays empty, and no Python state
says "a button is held".  Blender keeps running its timers meanwhile, so the bridge's poll
timer can run agent code in the middle of a drag, on a modifier whose node tree the code
then rewrites or removes -- the crash behind #576.  The co-work lock (#557) only stops
re-selecting objects; it does not stop a drag.

What Python *can* see, and is used here (all of it exists in Blender 5.2, 5.4 and
Bforartists; nothing Bforartists-only):

* ``window.modal_operators`` of every window: a transform, a paint stroke, a walk
  navigation... is running.  Coworker's own long-lived modals (the chat's input capture and
  the like) are ignored by id prefix.
* ``depsgraph_update_post`` fired *outside* a bridge step: while a slider moves, every
  mouse step changes the property and updates the depsgraph.  The handler stamps the time;
  an update within :data:`QUIET` seconds (that did not come from a step of ours, see
  :func:`stepping`) means "the user is changing something right now".  A held slider that
  does not move makes no update: that case Python cannot see (Blender's own).
* Animation playback and scrubbing also update the depsgraph; playback is ignored (it would
  hold every step back for as long as it plays).

The logic is bpy-free (the window manager is passed in), so it is unit-testable.
"""

__all__ = (
    "QUIET",
    "WAIT_LIMIT",
    "busy_reason",
    "note_update",
    "stepping",
    "reset",
)

import contextlib
import time
from typing import Any, Iterator

QUIET = 0.35            # seconds without a user update before a step may run
WAIT_LIMIT = 8.0        # the step waits at most this long, then answers "not run"
_GRACE = 0.6            # updates this soon after one of our steps are its own (the redraw)

_last_update = [0.0]
_step_depth = [0]
_own_until = [0.0]

# Coworker's own long-lived modal operators (the chat's input, the mic...) never count.
_IGNORED_PREFIXES = ("BFACW_", "bfacw.")
# Modal operators that run for ever or are harmless to a scene change.
_IGNORED_IDS = frozenset({
    "SCREEN_OT_animation_play",
    "SCREEN_OT_region_scale",
    "SCREEN_OT_area_move",
    "SCREEN_OT_area_split",
    "SCREEN_OT_area_join",
    "SCREEN_OT_frame_jump",
    "SCREEN_OT_repeat_history",
    "WM_OT_drop_blend_file",
})


def reset() -> None:
    """Forget everything (tests)."""
    _last_update[0] = 0.0
    _step_depth[0] = 0
    _own_until[0] = 0.0


def note_update(*_args: Any) -> None:
    """``depsgraph_update_post`` handler: stamp an update that is not from one of our steps."""
    now = time.monotonic()
    if _step_depth[0] > 0 or now < _own_until[0]:
        return
    _last_update[0] = now


@contextlib.contextmanager
def stepping() -> Iterator[None]:
    """Around a bridge step: the updates it causes are no drag of the user's."""
    _step_depth[0] += 1
    try:
        yield
    finally:
        _step_depth[0] -= 1
        _own_until[0] = time.monotonic() + _GRACE


def _modal_ids(window_manager: Any) -> list[str]:
    found: list[str] = []
    try:
        windows = list(getattr(window_manager, "windows", ()) or ())
    except Exception:  # pylint: disable=broad-exception-caught
        return found
    for window in windows:
        try:
            for op in getattr(window, "modal_operators", ()) or ():
                ident = str(getattr(op, "bl_idname", "") or "")
                if ident and not ident.startswith(_IGNORED_PREFIXES) and ident not in _IGNORED_IDS:
                    found.append(ident)
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return found


def _playing(window_manager: Any) -> bool:
    try:
        return any(bool(getattr(w.screen, "is_animation_playing", False)) for w in window_manager.windows)
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def busy_reason(window_manager: Any, now: float | None = None) -> str:
    """Why a scene-changing step should wait now, or ``""``.  Never raises."""
    try:
        modal = _modal_ids(window_manager)
        if modal:
            return "a tool is running in Blender ({:s})".format(", ".join(sorted(set(modal))[:3]))
        now = time.monotonic() if now is None else now
        if _last_update[0] and now - _last_update[0] < QUIET and not _playing(window_manager):
            return "you are changing a value or dragging a slider"
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return ""


def refusal(reason: str, waited: float) -> str:
    """The answer when the wait ran out."""
    return ("Not run: {:s}, and it did not stop for {:.0f} s. Nothing was changed. Running code "
            "while a slider is dragged can crash Blender. Ask the user to let go of the mouse, "
            "then try this step again.").format(reason, waited)
