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
* A modal operator that has run longer than :data:`LONG_MODAL` seconds (an open file browser,
  Walk / Fly navigation, another add-on's modal timer that never ends) is not waited for:
  it is no drag.  Each modal is stamped when first seen (:func:`sample`, run by a one second
  timer and by every check), keyed by its type name and pointer.

What is NOT caught (Blender's own, nothing in Python shows it): a pause of more than
:data:`QUIET` seconds in the middle of a drag, typing in a text field, open popups and redo
panels, colour pickers, drag-and-drop, a drag during playback, and a drag that starts while
a step is already running.

The logic is bpy-free (the window manager is passed in), so it is unit-testable.
"""

__all__ = (
    "QUIET",
    "WAIT_LIMIT",
    "LONG_MODAL",
    "busy_reason",
    "check",
    "note_update",
    "own",
    "refusal",
    "reset",
    "sample",
    "stepping",
)

import contextlib
import time
from typing import Any, Iterator

QUIET = 0.35            # seconds without a user update before a step may run
WAIT_LIMIT = 8.0        # the step waits at most this long, then answers "not run"
LONG_MODAL = 30.0       # a modal running longer than this is not waited for
_GRACE = 0.6            # least time after one of our steps that depsgraph updates are its own
_GRACE_MAX = 5.0        # ... and at most (a slow evaluation after a heavy step)

_last_update = [0.0]
_step_depth = [0]
_own_until = [0.0]
_first_seen: dict[tuple, float] = {}

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
    _first_seen.clear()


def note_update(*_args: Any) -> None:
    """``depsgraph_update_post`` handler: stamp an update that is not from one of our steps."""
    now = time.monotonic()
    if _step_depth[0] > 0 or now < _own_until[0]:
        return
    _last_update[0] = now


def own(seconds: float = _GRACE) -> None:
    """Coworker itself changes the scene outside a bridge step (an undo push, the lock's
    release, a save): the depsgraph updates that follow are not the user's."""
    _own_until[0] = max(_own_until[0], time.monotonic() + max(0.0, min(float(seconds), _GRACE_MAX)))


@contextlib.contextmanager
def stepping() -> Iterator[None]:
    """Around a bridge step: the updates it causes are no drag of the user's.  After a long
    step the evaluation may come late, so the time after is as long as the step took."""
    began = time.monotonic()
    _step_depth[0] += 1
    try:
        yield
    finally:
        _step_depth[0] -= 1
        own(max(_GRACE, time.monotonic() - began))


def _modals(window_manager: Any) -> list[tuple[tuple, str]]:
    """``[(key, type name)]`` of the modal operators that might be a user interaction."""
    found: list[tuple[tuple, str]] = []
    try:
        windows = list(getattr(window_manager, "windows", ()) or ())
    except Exception:  # pylint: disable=broad-exception-caught
        return found
    for window in windows:
        try:
            for op in getattr(window, "modal_operators", ()) or ():
                ident = str(getattr(op, "bl_idname", "") or "")
                if not ident or ident.startswith(_IGNORED_PREFIXES) or ident in _IGNORED_IDS:
                    continue
                try:
                    pointer = int(op.as_pointer())
                except Exception:  # pylint: disable=broad-exception-caught
                    pointer = 0
                found.append(((ident, pointer), ident))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return found


def sample(window_manager: Any, now: float | None = None) -> None:
    """Stamp each modal operator the first time it is seen, forget the ones that ended.
    Cheap; called each second by a timer and by every check.  Never raises."""
    try:
        now = time.monotonic() if now is None else now
        current = {key for key, _name in _modals(window_manager)}
        for key in current:
            _first_seen.setdefault(key, now)
        for key in [k for k in _first_seen if k not in current]:
            del _first_seen[key]
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _playing(window_manager: Any) -> bool:
    try:
        return any(bool(getattr(w.screen, "is_animation_playing", False)) for w in window_manager.windows)
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def check(window_manager: Any, now: float | None = None) -> tuple[str, str]:
    """``(kind, what)``: ``("modal", tool name)`` while a modal tool younger than
    :data:`LONG_MODAL` s runs, ``("drag", "")`` right after a user change, else ``("", "")``.
    Never raises."""
    try:
        now = time.monotonic() if now is None else now
        sample(window_manager, now)
        young = sorted({name for key, name in _modals(window_manager)
                        if now - _first_seen.get(key, now) < LONG_MODAL})
        if young:
            return "modal", ", ".join(young[:3])
        if _last_update[0] and now - _last_update[0] < QUIET and not _playing(window_manager):
            return "drag", ""
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return "", ""


def busy_reason(window_manager: Any, now: float | None = None) -> str:
    """Why a scene-changing step should wait now, or ``""``."""
    kind, what = check(window_manager, now)
    if kind == "modal":
        return "the tool {:s} is running in Blender".format(what)
    if kind == "drag":
        return "you are changing a value or dragging a slider"
    return ""


def refusal(reason: str, waited: float, kind: str = "drag") -> str:
    """The answer when the wait ran out (*reason* from :func:`busy_reason`)."""
    if kind == "modal":
        return ("Not run: {:s}, and it did not finish in {:.0f} s. Nothing was changed. Running code "
                "while a tool is active in Blender can crash it. Ask the user to finish or cancel it "
                "(Esc), then try this step again.").format(reason, waited)
    return ("Not run: {:s}, and it did not stop for {:.0f} s. Nothing was changed. Running code "
            "while a slider is dragged can crash Blender. Ask the user to let go of the mouse, "
            "then try this step again.").format(reason, waited)
