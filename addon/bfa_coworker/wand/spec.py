# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The spec of a Coworker operator, frozen by Wand 0 (#331; plan 4m section 3.1).

One :class:`OperatorSpec` per operator says what it is and where it lives.
Wand 0 builds one operator by hand from it (Remove Silences); Wand 1's
framework will build the operator class, the placement and the MCP tool from
the same fields.  Free of ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "KINDS",
    "MODELS",
    "OperatorSpec",
    "Param",
    "Place",
    "SCOPES",
)

from dataclasses import dataclass, field
from typing import Any

KINDS = ("tool", "macro", "hybrid", "wizard")
MODELS = ("none", "small", "full")
SCOPES = ("selection", "editor", "scene", "file")


@dataclass(frozen=True)
class Param:
    """One operator property: *name*, *default*, label and tooltip; *unit* is a Blender
    unit (``"TIME_ABSOLUTE"``, ...) or ``""``; *soft_min* / *soft_max* bound the slider."""

    name: str
    default: Any
    label: str = ""
    description: str = ""
    unit: str = ""
    soft_min: float | None = None
    soft_max: float | None = None


@dataclass(frozen=True)
class Place:
    """Where the operator is drawn.

    * ``menu``: appended to the menu class *target* (``Menu.append`` adds at the end;
      *after* names the native operator it belongs next to, for the day a menu can
      be inserted into).
    * ``toolshelf``: Bforartists' tabbed toolshelf of *space*, tab *category*, panel
      *panel* (drawn only when ``space.show_toolshelf_tabs`` exists and is on).
    * ``sidebar``: the editor's sidebar (N panel), tab *category*, panel *panel*: the
      Blender place, and Bforartists' with the toolshelf tabs off.
    """

    kind: str
    target: str = ""
    space: str = ""
    category: str = ""
    panel: str = ""
    after: str = ""

    @classmethod
    def menu(cls, target: str, after: str = "") -> "Place":
        return cls("menu", target=target, after=after)

    @classmethod
    def toolshelf(cls, space: str, category: str, panel: str) -> "Place":
        return cls("toolshelf", space=space, category=category, panel=panel)

    @classmethod
    def sidebar(cls, space: str, category: str, panel: str) -> "Place":
        return cls("sidebar", space=space, category=category, panel=panel)


@dataclass(frozen=True)
class OperatorSpec:
    """A Coworker operator.

    *id* is ``"<editor>.<name>"``; the Blender ``bl_idname`` is
    ``"coworker.<editor>_<name>"``.  *icon* is the wand icon's name (see
    ``wand/icons.py``) and *fallback_icon* the Blender icon used without it.
    *kind* is one of :data:`KINDS`, *model* one of :data:`MODELS` (``none`` runs
    with no engine), *scope* one of :data:`SCOPES`.  *preview*: the operator shows
    what it will do before it applies (a dialog with a preview line); *undo*: one
    undo step for the whole run.
    """

    id: str
    label: str
    description: str
    icon: str
    fallback_icon: str
    places: tuple = ()
    kind: str = "tool"
    model: str = "none"
    scope: str = "selection"
    preview: bool = True
    undo: bool = True
    params: tuple = field(default_factory=tuple)

    @property
    def bl_idname(self) -> str:
        editor, _, name = self.id.partition(".")
        return "coworker.{:s}_{:s}".format(editor, name)

    def param(self, name: str) -> Param:
        return next(p for p in self.params if p.name == name)
