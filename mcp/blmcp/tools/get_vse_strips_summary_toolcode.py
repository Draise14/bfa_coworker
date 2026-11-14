# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for listing the Video Sequencer's strips (Tools 1, #304).
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    channel: int = 0
    strip_type: str = ""
    frame_start: int | None = None
    frame_end: int | None = None
    name_contains: str = ""
    limit: int = 50
    scene_name: str = ""


class Result(NamedTuple):
    status: str
    scene: str
    total: int
    matched: int
    shown: int
    more: int
    strips: list[dict[str, Any]]
    message: str


# @include_begin: _template_vse_shared.py
from blmcp.tools._template_vse_shared import (  # noqa: E402  (the real helpers, inlined in Blender)
    _vse_apply_limit, _vse_filter_rows, _vse_resolve_scene, _vse_rows, _vse_sort_rows,
)
# @include_end


def main(params: Params) -> Result:
    scene, error = _vse_resolve_scene(params.scene_name)
    if scene is None:
        return Result(status="error", scene="", total=0, matched=0, shown=0, more=0, strips=[], message=error)
    rows = _vse_sort_rows(_vse_rows(scene))
    if not rows:
        return Result(status="ok", scene=scene.name, total=0, matched=0, shown=0, more=0, strips=[], message=(
            "Scene '{:s}' has no strips in the Sequencer.".format(scene.name)))
    matched = _vse_filter_rows(
        rows, channel=int(params.channel or 0), strip_type=params.strip_type,
        frame_start=params.frame_start, frame_end=params.frame_end, name_contains=params.name_contains,
    )
    shown, more = _vse_apply_limit(matched, params.limit)
    if not matched:
        message = ("No strip matches the filters ({:d} strips in '{:s}'). Loosen channel / strip_type / "
                   "frame range / name_contains.".format(len(rows), scene.name))
    elif more:
        message = ("{:d} of {:d} matching strips shown; {:d} more. Narrow with channel, strip_type, "
                   "frame_start/frame_end or name_contains, or raise limit.".format(len(shown), len(matched), more))
    else:
        message = "{:d} strips in '{:s}' ({:d} match).".format(len(rows), scene.name, len(matched))
    return Result(status="ok", scene=scene.name, total=len(rows), matched=len(matched), shown=len(shown),
                  more=more, strips=shown, message=message)
