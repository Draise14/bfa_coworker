# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Frame Inside (#625), bpy-free: pan and zoom the picture inside one part of a split.

A split part is a strip whose crop and transform make its window.  What shows inside the window is
that strip's *framing* (``reframe_layout.Framing``: where in the picture the window looks, and the
zoom).  The artist's three sliders are that framing in friendlier numbers:

* ``x`` / ``y``: -1 .. 1 across the room the picture leaves at the current zoom (0 = centred, +x
  right, +y up, 0 where there is no room: the part already shows the picture's full width or height);
* ``zoom``: 1 (just covers the window) .. ``MAX_ZOOM``.

No compositor modifier is involved (the probe of #625 measured one at 100 to 140 ms per frame and
two framed parts at 4 to 6 fps; crop and transform play at full speed): the sliders rewrite the
part's crop and transform scale and offset.

A keyed pan: the strip's crop and transform are animated, but crop is a whole number and Blender rounds
it, while the offset (and the scale) move smoothly.  The visible edge is their combination, so with only two
keys the picture slips off the window by up to a pixel or two and black shows (measured, see the PR).  So the
artist's keys are kept as *anchors* (``{frame: (u, v, zoom)}``) and :func:`bake` turns them into the keys the
strip carries: only the anchors where the framing stays the same between two of them, every frame where the
pan or the zoom changes.
"""

from __future__ import annotations

__all__ = (
    "MAX_ZOOM",
    "Inside",
    "bake",
    "framing_from_inside",
    "inside_from_framing",
)

from typing import NamedTuple

from . import reframe_layout as rl

MAX_ZOOM = 4.0


class Inside(NamedTuple):
    """The sliders: pan *x*, *y* in -1 .. 1 and *zoom* in 1 .. MAX_ZOOM."""

    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def _halves(rect: rl.Rect, sw: int, sh: int, zoom: float) -> tuple[float, float]:
    """Half of the picture's width and height (as fractions) the window shows at *zoom*."""
    x0, y0, x1, y1 = rect
    scale = rl.cover_scale(x1 - x0, y1 - y0, sw, sh) * zoom
    return min(0.5, (x1 - x0) / (2.0 * scale) / sw), min(0.5, (y1 - y0) / (2.0 * scale) / sh)


def framing_from_inside(inside: Inside, rect: rl.Rect, sw: int, sh: int) -> rl.Framing:
    """The framing that shows *inside* in the window *rect* of a *sw* x *sh* picture."""
    zoom = _clamp(float(inside.zoom), 1.0, MAX_ZOOM)
    half_u, half_v = _halves(rect, sw, sh, zoom)
    return rl.Framing(0.5 + _clamp(float(inside.x), -1.0, 1.0) * (0.5 - half_u),
                      0.5 + _clamp(float(inside.y), -1.0, 1.0) * (0.5 - half_v), zoom)


def inside_from_framing(framing: rl.Framing, rect: rl.Rect, sw: int, sh: int) -> Inside:
    """The sliders that show *framing* (the inverse of :func:`framing_from_inside`)."""
    zoom = _clamp(float(framing.zoom), 1.0, MAX_ZOOM)
    half_u, half_v = _halves(rect, sw, sh, zoom)
    room_u, room_v = 0.5 - half_u, 0.5 - half_v
    x = _clamp((float(framing.u) - 0.5) / room_u, -1.0, 1.0) if room_u > 1e-9 else 0.0
    y = _clamp((float(framing.v) - 0.5) / room_v, -1.0, 1.0) if room_v > 1e-9 else 0.0
    return Inside(x, y, zoom)


def bake(anchors: dict[int, tuple[float, float, float]]) -> list[tuple[int, rl.Framing]]:
    """The framings to key, ``[(frame, Framing)]`` by frame, for the artist's *anchors* (``frame -> (u, v, zoom)``).

    Between two anchors of the same framing a straight line is exact, so those two keys do.  Wherever the
    pan or the zoom differs, every frame in between is keyed, the framing interpolated linearly: Blender
    rounds the animated crop to a whole pixel, so a line between two keys would not stay on the picture.
    Outside the first and last anchor the framing stays (the curves are constant there)."""
    frames = sorted(int(f) for f in anchors)
    out: list[tuple[int, rl.Framing]] = []
    for index, frame in enumerate(frames):
        u, v, zoom = anchors[frame]
        if out and out[-1][0] == frame:
            continue
        out.append((frame, rl.Framing(float(u), float(v), float(zoom))))
        if index + 1 < len(frames):
            nxt = frames[index + 1]
            u2, v2, zoom2 = anchors[nxt]
            if max(abs(float(u2) - float(u)), abs(float(v2) - float(v)), abs(float(zoom2) - float(zoom))) > 1e-9:
                span = nxt - frame
                for step in range(1, span):
                    t = step / span
                    out.append((frame + step, rl.Framing(u + (u2 - u) * t, v + (v2 - v) * t,
                                                         zoom + (zoom2 - zoom) * t)))
    return out
