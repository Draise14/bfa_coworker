# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The geometry of Reframe's layouts (#606).  Free of ``bpy``.

A *layout* says how one picture (``sw`` x ``sh`` pixels) fills the frame (``fw`` x ``fh``):

* ``full``: one part, the picture's centre fills the frame (cover fit, nothing stretched);
* ``full_blur``: the whole picture fitted, a blurred copy behind it (today's Blur Fill);
* ``full_tracked``: one part that follows the subject (today's Follow Subject);
* ``split_half`` / ``split_top`` / ``split_bottom``: two parts, one above the other, 1/2 + 1/2,
  2/3 + 1/3, 1/3 + 2/3 of the frame's height, each a centred cover crop of the whole picture;
* ``track_all``: a split whose parts each follow their own subject (needs the subject finding of
  #592: listed here, not buildable yet).

The first three are drawn by the Reframe compositor modifier (``reframe.py``); a split is two
ordinary strips of the same source whose ``crop`` and ``transform`` this module computes.

How a Sequencer strip's ``crop`` and ``transform`` act (measured on Blender 5.2.4, see the PR):

* ``crop`` is in the SOURCE picture's pixels and takes edges off; the rest stays where it was
  (the centre of the cropped picture is not the frame's centre);
* ``transform.scale_x / scale_y`` multiply the source pixels (1 = native size; ``new_*(fit_method='FIT')``
  bakes the fitting scale into it), about the picture's centre;
* ``transform.offset_x / offset_y`` move the whole picture in frame pixels (y up).

So with a uniform ``scale`` *s* and offset *(ox, oy)* from the frame's centre, the visible part is
``x: ox - sw*s/2 + min_x*s .. ox + sw*s/2 - max_x*s`` (and the same for y with ``min_y`` at the bottom).

Coordinates of a rectangle: frame pixels, origin at the frame's bottom-left, y up
(``(x0, y0, x1, y1)``); offsets are from the frame's centre.

A part's *framing* is what the artist moves: ``(u, v, zoom)``: the fraction of the picture (from
its left / bottom) that sits at the middle of the part's rectangle, and the zoom over the cover
scale.  Swap exchanges two framings; Reset puts back ``DEFAULT_FRAMING``.
"""

from __future__ import annotations

__all__ = (
    "DEFAULT_FRAMING",
    "LAYOUTS",
    "Framing",
    "Layout",
    "Part",
    "Plan",
    "buildable",
    "cover_scale",
    "fit_scale",
    "framing_of",
    "layout",
    "part_for",
    "part_rects",
    "plan",
    "visible_rect",
)

import math
from dataclasses import dataclass
from typing import NamedTuple


@dataclass(frozen=True)
class Layout:
    """One layout: its id, button text, tooltip, the heights of its parts (fractions, top first) and
    how it is drawn: ``modifier`` ``"CROP"`` / ``"BLUR_FILL"`` (the Reframe modifier) or ``""`` (strips
    with crop and transform).  *icon* names Coworker's own PNG (``wand/icons.py``), *fallback* the
    Blender icon used without it; *ready* False: listed, not buildable yet."""

    id: str
    label: str
    description: str
    parts: tuple[tuple[int, int], ...]          # (numerator, denominator) of the frame's height, top first
    modifier: str = ""
    tracked: bool = False
    icon: str = ""
    fallback: str = "NONE"
    ready: bool = True
    short: str = ""                            # the button's own text


_WORKS = "Works without the agent started."

LAYOUTS: tuple[Layout, ...] = (
    Layout("full", "Full Frame",
           "One picture filling the whole frame: the centre of the shot, cropped to fit, nothing stretched. "
           "Pan and zoom it afterwards in the Reframe modifier.\n" + _WORKS,
           ((1, 1),), "CROP", False, "reframe_full", "FULLSCREEN_ENTER"),
    Layout("full_blur", "Full Frame, Blur Fill",
           "The whole shot fitted inside the frame, over a blurred, dimmed copy of itself.\n" + _WORKS,
           ((1, 1),), "BLUR_FILL", False, "reframe_full_blur", "IMAGE_BACKGROUND"),
    Layout("full_tracked", "Full Frame, Tracked",
           "One picture filling the frame that follows the subject: it samples a few frames and keys the pan "
           "from where a vision model sees the subject.\nWorks without the agent started, except the tracking, "
           "which needs a connected vision-capable model.",
           ((1, 1),), "CROP", True, "reframe_full_tracked", "TRACKER"),
    Layout("split_half", "Split, Half and Half",
           "Two crops of the same footage, one above the other, 50 / 50. Each is an ordinary strip you can move "
           "in the preview.\n" + _WORKS,
           ((1, 2), (1, 2)), "", False, "reframe_split_half", "SPLIT_HORIZONTAL"),
    Layout("split_top", "Split, Top Bias",
           "Two crops of the same footage: two thirds on top, one third below. Each is an ordinary strip you can "
           "move in the preview.\n" + _WORKS,
           ((2, 3), (1, 3)), "", False, "reframe_split_top", "ALIGN_TOP"),
    Layout("split_bottom", "Split, Bottom Bias",
           "Two crops of the same footage: one third on top, two thirds below. Each is an ordinary strip you can "
           "move in the preview.\n" + _WORKS,
           ((1, 3), (2, 3)), "", False, "reframe_split_bottom", "ALIGN_BOTTOM"),
    Layout("track_all", "Track All",
           "In a split, every part follows its own subject. Until then the parts keep a fixed crop you can "
           "move.\nNeeds subject finding (coming).",
           ((1, 2), (1, 2)), "", True, "reframe_track_all", "TRACKING", False),
)
_SHORT = {'full': 'Full Frame', 'full_blur': 'Blur Fill', 'full_tracked': 'Tracked', 'split_half': 'Half and Half', 'split_top': 'Top Bias', 'split_bottom': 'Bottom Bias', 'track_all': 'Track All'}
LAYOUTS = tuple(Layout(**{**item.__dict__, "short": _SHORT[item.id]}) for item in LAYOUTS)
_BY_ID = {item.id: item for item in LAYOUTS}


def layout(layout_id: str) -> Layout:
    """The layout named *layout_id* (``KeyError`` for an unknown one)."""
    return _BY_ID[layout_id]


def buildable() -> tuple[str, ...]:
    """The ids of the layouts that can be built today."""
    return tuple(item.id for item in LAYOUTS if item.ready)


Rect = tuple[int, int, int, int]


class Framing(NamedTuple):
    """What the artist moves: the fraction (*u* from the left, *v* from the bottom) of the picture at
    the middle of the part's rectangle, and the *zoom* over the cover scale (1 = just covers)."""

    u: float = 0.5
    v: float = 0.5
    zoom: float = 1.0


DEFAULT_FRAMING = Framing()


class Part(NamedTuple):
    """One part of a layout: its rectangle in the frame and the strip numbers that fill it."""

    index: int                                  # 0 = top
    rect: Rect
    scale: float                                # transform.scale_x == scale_y
    offset: tuple[float, float]                 # transform.offset_x, offset_y
    crop: tuple[int, int, int, int]             # crop.min_x, max_x, min_y, max_y (source pixels)
    framing: Framing


class Plan(NamedTuple):
    layout: Layout
    parts: tuple[Part, ...]


def _check(*values: float) -> None:
    if min(values) <= 0:
        raise ValueError("sizes must be positive: {!r}".format(values))


def cover_scale(rw: float, rh: float, sw: float, sh: float) -> float:
    """The scale at which a *sw* x *sh* picture just covers a *rw* x *rh* rectangle."""
    _check(rw, rh, sw, sh)
    return max(rw / sw, rh / sh)


def fit_scale(rw: float, rh: float, sw: float, sh: float) -> float:
    """The scale at which a *sw* x *sh* picture just fits inside a *rw* x *rh* rectangle."""
    _check(rw, rh, sw, sh)
    return min(rw / sw, rh / sh)


def part_rects(layout_id: str, fw: int, fh: int) -> list[Rect]:
    """The rectangles of the parts, top first.  They tile the frame exactly (integer rows, no gap, no
    overlap, at odd sizes too): each takes its share rounded to a whole row, the last takes the rest."""
    _check(fw, fh)
    fractions = layout(layout_id).parts
    rects: list[Rect] = []
    top = int(fh)
    for index, (num, den) in enumerate(fractions):
        last = index == len(fractions) - 1
        height = top if last else min(top, (int(fh) * num + den // 2) // den)
        rects.append((0, top - height, int(fw), top))
        top -= height
    return rects


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def part_for(index: int, rect: Rect, sw: int, sh: int, fw: int, fh: int,
             framing: Framing = DEFAULT_FRAMING) -> Part:
    """The strip numbers that show *framing* of a *sw* x *sh* picture in *rect* of a *fw* x *fh* frame.

    The visible area always covers *rect* (crop edges are whole source pixels, rounded outward) and
    reaches past it only where that is outside the frame; an edge shared with another part (the seam)
    sits exactly on the rectangle's edge, by a sub-pixel move of the picture."""
    _check(sw, sh, fw, fh)
    x0, y0, x1, y1 = rect
    rw, rh = x1 - x0, y1 - y0
    _check(rw, rh)
    zoom = max(1.0, float(framing.zoom))
    scale = cover_scale(rw, rh, sw, sh) * zoom
    # The focus stays where the visible part is inside the picture.
    half_u = min(0.5, rw / (2.0 * scale) / sw)
    half_v = min(0.5, rh / (2.0 * scale) / sh)
    u = _clamp(float(framing.u), half_u, 1.0 - half_u)
    v = _clamp(float(framing.v), half_v, 1.0 - half_v)
    # The rectangle in frame-centre coordinates, and where the picture's centre sits.
    left, right = x0 - fw / 2.0, x1 - fw / 2.0
    bottom, top = y0 - fh / 2.0, y1 - fh / 2.0
    ox = (left + right) / 2.0 - (u * sw - sw / 2.0) * scale
    oy = (bottom + top) / 2.0 - (v * sh - sh / 2.0) * scale
    eps = 1e-9
    min_x = max(0, math.floor((left - (ox - sw * scale / 2.0)) / scale + eps))
    max_x = max(0, math.floor(((ox + sw * scale / 2.0) - right) / scale + eps))
    min_y = max(0, math.floor((bottom - (oy - sh * scale / 2.0)) / scale + eps))
    max_y = max(0, math.floor(((oy + sh * scale / 2.0) - top) / scale + eps))
    # An edge shared with a neighbour is put on the rectangle's edge exactly.
    if y0 > 0:                                   # a part above this one: its bottom edge is the seam
        oy += bottom - (oy - sh * scale / 2.0 + min_y * scale)
    elif y1 < fh:                                # a part below: its top edge is the seam
        oy += top - (oy + sh * scale / 2.0 - max_y * scale)
    return Part(int(index), (int(x0), int(y0), int(x1), int(y1)), scale, (ox, oy),
                (int(min_x), int(max_x), int(min_y), int(max_y)), Framing(u, v, zoom))


def visible_rect(part: Part, sw: int, sh: int, fw: int, fh: int) -> tuple[float, float, float, float]:
    """The area of the frame a part's strip shows, in the rectangle coordinates (bottom-left origin)."""
    s = part.scale
    ox, oy = part.offset
    min_x, max_x, min_y, max_y = part.crop
    return (fw / 2.0 + ox - sw * s / 2.0 + min_x * s, fh / 2.0 + oy - sh * s / 2.0 + min_y * s,
            fw / 2.0 + ox + sw * s / 2.0 - max_x * s, fh / 2.0 + oy + sh * s / 2.0 - max_y * s)


def framing_of(rect: Rect, scale: float, offset: tuple[float, float], sw: int, sh: int,
               fw: int, fh: int) -> Framing:
    """The framing of a strip that shows its picture at *scale* and *offset* over *rect*: what sits at the
    rectangle's middle (read back from the strip, so a framing the artist moved is kept by Swap)."""
    _check(sw, sh, fw, fh, scale)
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2.0 - fw / 2.0, (y0 + y1) / 2.0 - fh / 2.0
    u = 0.5 + (cx - offset[0]) / (scale * sw)
    v = 0.5 + (cy - offset[1]) / (scale * sh)
    zoom = scale / cover_scale(x1 - x0, y1 - y0, sw, sh)
    return Framing(u, v, max(1.0, zoom))


def plan(layout_id: str, sw: int, sh: int, fw: int, fh: int) -> Plan:
    """The parts of *layout_id* for a *sw* x *sh* picture in a *fw* x *fh* frame.

    For ``full`` and the splits each part is a centred cover crop; for ``full_blur`` the one part is the
    whole picture fitted (the blurred copy behind is the modifier's); ``full_tracked`` starts as ``full``
    (the subject's position comes later)."""
    item = layout(layout_id)
    rects = part_rects(layout_id, fw, fh)
    parts = [part_for(i, rect, sw, sh, fw, fh) for i, rect in enumerate(rects)]
    if item.modifier == "BLUR_FILL":
        s = fit_scale(fw, fh, sw, sh)
        parts = [Part(0, rects[0], s, (0.0, 0.0), (0, 0, 0, 0), DEFAULT_FRAMING)]
    return Plan(item, tuple(parts))
