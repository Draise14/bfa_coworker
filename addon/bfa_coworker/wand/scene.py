# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The Sequencer's scene (#504): the scene the Sequencer shows, which since Blender 5.0
is the workspace's ``sequencer_scene``, not the window's scene.

* ``context.sequencer_scene`` first: Bforartists already gives the window's scene
  there while nothing is pinned (and its Sequencer shows that scene); Blender gives
  ``None`` when the Sequencer shows no scene.
* Then ``workspace.sequencer_scene``: ``None`` means the Sequencer shows no scene.
* Only where the workspace has no ``sequencer_scene`` at all (before 5.0) or there is
  no workspace (a timer, the background): the window's scene.

The Coworker video tools' polls and menus use :func:`sequencer_problem` so that with
no Sequencer scene, or one without strips, they are greyed out with the reason.
Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "NO_SCENE",
    "NO_STRIPS",
    "poll",
    "sequencer_problem",
    "sequencer_scene",
)

from typing import Any

NO_SCENE = "The Sequencer shows no scene: pick or add one in its header"
NO_STRIPS = "The Sequencer's scene “{:s}” has no strips yet"

_MISSING = object()


def sequencer_scene(context: Any = None) -> Any:
    """The scene the Sequencer shows, or ``None`` when it shows none."""
    if context is None:
        import bpy  # pylint: disable=import-error
        context = bpy.context
    scene = getattr(context, "sequencer_scene", None)
    if scene is not None:
        return scene
    workspace = getattr(context, "workspace", None)
    shown = getattr(workspace, "sequencer_scene", _MISSING) if workspace is not None else _MISSING
    if shown is not _MISSING:
        return shown
    return getattr(context, "scene", None)


def sequencer_problem(context: Any = None) -> str:
    """Why the Sequencer's tools can't run here ("" when they can): no scene, or no strips."""
    scene = sequencer_scene(context)
    if scene is None:
        return NO_SCENE
    if getattr(scene, "sequence_editor", None) is None:
        return NO_STRIPS.format(getattr(scene, "name", ""))
    return ""


def poll(cls: Any, context: Any) -> bool:
    """An operator ``poll``: a Sequencer scene with strips, else ``poll_message_set`` gives the reason."""
    problem = sequencer_problem(context)
    if problem:
        cls.poll_message_set(problem)
        return False
    return True
