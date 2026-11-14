# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for an overview of the Video Sequencer timeline (Tools 1, #304).
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    scene_name: str = ""


class Result(NamedTuple):
    status: str
    scene: str
    sequencer_shows: str
    fps: float
    resolution: list[int]
    frame_start: int
    frame_end: int
    current_frame: int
    strip_count: int
    content_range: list[int]
    active_strip: str
    channels: list[dict[str, Any]]
    gaps: list[list[int]]
    overlaps: list[dict[str, Any]]
    markers: list[dict[str, Any]]
    message: str


# @include_begin: _template_vse_shared.py
from blmcp.tools._template_vse_shared import (  # noqa: E402  (the real helpers, inlined in Blender)
    _vse_gaps, _vse_overlaps, _vse_resolve_scene, _vse_rows, _vse_shown_scene,
)
# @include_end

# Markers listed at most (the count is always given).
_MARKERS_MAX = 50


def _channels(scene: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Channels holding top-level strips: number, name, strip count, mute, lock."""
    counts: dict[int, int] = {}
    for row in rows:
        if not row.get("meta"):
            counts[row["channel"]] = counts.get(row["channel"], 0) + 1
    channels = getattr(scene.sequence_editor, "channels", None) if scene.sequence_editor else None
    out = []
    for number in sorted(counts):
        entry: dict[str, Any] = {"channel": number, "strips": counts[number]}
        try:
            ch = channels[number] if channels is not None else None
        except (IndexError, KeyError, TypeError):
            ch = None
        if ch is not None:
            entry.update(name=ch.name, mute=bool(ch.mute), lock=bool(ch.lock))
        out.append(entry)
    return out


def main(params: Params) -> Result:
    scene, error = _vse_resolve_scene(params.scene_name)
    shown = _vse_shown_scene()
    if scene is None:
        return Result(status="error", scene="", sequencer_shows="", fps=0.0, resolution=[], frame_start=0,
                      frame_end=0, current_frame=0, strip_count=0, content_range=[], active_strip="", channels=[],
                      gaps=[], overlaps=[], markers=[], message=error)
    rd = scene.render
    rows = _vse_rows(scene)
    top = [r for r in rows if not r.get("meta")]
    ed = scene.sequence_editor
    active = ed.active_strip.name if ed is not None and ed.active_strip is not None else ""
    markers = sorted(({"name": m.name, "frame": int(m.frame)} for m in scene.timeline_markers),
                     key=lambda m: m["frame"])
    fps = round(rd.fps / (rd.fps_base or 1.0), 3)
    content = [min(r["start"] for r in top), max(r["end"] for r in top)] if top else []
    gaps = _vse_gaps(rows, int(scene.frame_start), int(scene.frame_end)) if top else []
    overlaps = _vse_overlaps(rows)
    notes = ["{:d} strips on {:d} channels, frames {:d}-{:d} at {:g} fps.".format(
        len(rows), len({r["channel"] for r in top}), scene.frame_start, scene.frame_end, fps)]
    if not rows:
        notes = ["Scene '{:s}' has no strips in the Sequencer.".format(scene.name)]
    if shown is not None and shown.name != scene.name:
        notes.append("The Sequencer editor shows scene '{:s}'; this is '{:s}'.".format(shown.name, scene.name))
    if not getattr(rd, "use_sequencer", True):
        notes.append("Post Processing > Sequencer is off, so a render ignores the strips.")
    if overlaps:
        notes.append("{:d} overlapping pairs.".format(len(overlaps)))
    if len(markers) > _MARKERS_MAX:
        notes.append("{:d} markers; the first {:d} listed.".format(len(markers), _MARKERS_MAX))
    return Result(
        status="ok",
        scene=scene.name,
        sequencer_shows=shown.name if shown is not None else "",
        fps=fps,
        resolution=[int(rd.resolution_x), int(rd.resolution_y), int(rd.resolution_percentage)],
        frame_start=int(scene.frame_start),
        frame_end=int(scene.frame_end),
        current_frame=int(scene.frame_current),
        strip_count=len(rows),
        content_range=content,
        active_strip=active,
        channels=_channels(scene, rows),
        gaps=gaps,
        overlaps=overlaps,
        markers=markers[:_MARKERS_MAX],
        message=" ".join(notes),
    )
