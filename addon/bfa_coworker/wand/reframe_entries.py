# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The Reframe buttons as toolshelf entries (#622), bpy-free.

Bforartists' ``bl_ui.space_toolsystem_common.draw_entries(layout, context, entries)`` snaps icon
buttons into a grid by the region's width (icon only when narrow, with names at four columns).  An
entry only needs ``poll`` and ``draw(layout, as_icon=...)``; the Video panel's ``CoworkerEntry`` does
it for plain operators.  The Reframe buttons also set the operator's ``layout`` property, show
*pressed* for the current layout, and Track All is greyed out, so they have their own entries here.
"""

from __future__ import annotations

__all__ = (
    "ActionEntry",
    "LayoutEntry",
    "PREVIEW_VIEWS",
    "SEPARATOR",
    "entries",
    "shows_in",
)

from typing import Any

from . import icons
from . import reframe_layout as rl

# Where the Reframe panels show: both views with a timeline, and the Preview alone (it works on the
# picture; the Video panel stays out of the Preview alone, #504).
PREVIEW_VIEWS = frozenset({"SEQUENCER", "SEQUENCER_PREVIEW", "PREVIEW"})

# Between groups of buttons.  ``draw_entries`` compares with its own ``Separator``; the panel swaps it in.
SEPARATOR = object()

_SWAP = ("bfacw.reframe_swap", "Swap", "ARROW_LEFTRIGHT")
_RESET = ("bfacw.reframe_reset", "Reset", "LOOP_BACK")


def shows_in(view_type: Any) -> bool:
    """Whether the Reframe panels show in a Sequencer area of this view type."""
    return view_type in PREVIEW_VIEWS


def _blender_icon(name: str) -> str:
    from ..shared import icon as safe_icon
    return safe_icon(name)


class LayoutEntry:
    """One layout's button: sets ``layout`` on ``bfacw.reframe_layout``, pressed when current,
    greyed out when the layout isn't built yet (Track All)."""

    __slots__ = ("item", "pressed", "poll", "enabled")
    operator = "bfacw.reframe_layout"

    def __init__(self, item: rl.Layout, pressed: bool = False):
        self.item, self.pressed, self.poll, self.enabled = item, pressed, True, item.ready

    def draw(self, layout: Any, *, as_icon: bool) -> None:
        if not self.poll:
            return
        host = layout if self.enabled else layout.row(align=True)
        if not self.enabled:
            host.enabled = False
        kwargs: dict[str, Any] = {"text": "" if as_icon else self.item.short or self.item.label,
                                  "depress": self.pressed}
        value = icons.icon_id(self.item.icon) if self.item.icon else 0
        if value:
            op = host.operator(self.operator, icon_value=value, **kwargs)
        else:
            op = host.operator(self.operator, icon=_blender_icon(self.item.fallback), **kwargs)
        op.layout = self.item.id


class ActionEntry:
    """Swap or Reset: a plain operator with a Blender icon."""

    __slots__ = ("operator", "text", "icon", "poll")

    def __init__(self, operator: str, text: str, icon: str):
        self.operator, self.text, self.icon, self.poll = operator, text, icon, True

    def draw(self, layout: Any, *, as_icon: bool) -> None:
        if self.poll:
            layout.operator(self.operator, text="" if as_icon else self.text, icon=_blender_icon(self.icon))


def entries(current: str = "", separator: Any = SEPARATOR) -> list[Any]:
    """The toolshelf's entries: the ready layouts (the current one pressed), Track All greyed out,
    then Swap and Reset; *separator* between the groups (Bforartists' own ``Separator``)."""
    by_id = {item.id: item for item in rl.LAYOUTS}
    out: list[Any] = []
    for group in (("full", "full_blur", "full_tracked"), ("split_half", "split_top", "split_bottom", "track_all")):
        if out:
            out.append(separator)
        out += [LayoutEntry(by_id[i], pressed=bool(current) and current == i) for i in group if i in by_id]
    out.append(separator)
    out += [ActionEntry(*_SWAP), ActionEntry(*_RESET)]
    return out
