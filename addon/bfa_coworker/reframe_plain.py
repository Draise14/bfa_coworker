# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Make Vertical's plain Centre Crop without a compositor modifier (#632).  Free of ``bpy``.

Centre Crop only centres, pans and zooms a picture, which a strip's own ``crop`` and ``transform``
do for free; the *Coworker Reframe* compositor modifier costs about 100-140 ms per frame and halved
preview playback.  So a plain crop is now those strip numbers (the same ones the split layouts use,
:mod:`wand.reframe_layout`), and the modifier stays for Blur Fill, which needs a compositor.

The framing keeps the modifier's units, so the two forms convert both ways and a hand-made
framing survives the change:

* **Zoom** -- 1 = the picture just covers the frame (times :data:`reframe_math.BLEED`, the same
  hair past "covers" the modifier used);
* **Offset X / Y** -- -1 .. 1 across the pan range, -1 the picture's left (bottom) edge, 1 its right
  (top) edge, 0 centred.

``plain_part`` turns a framing into the strip numbers, ``plain_framing`` reads them back from the
strip (so a picture dragged by hand in the preview is read as it now is), ``subject_part`` is the
same for a Follow Subject key (the picture fraction that sits at the middle).
"""

from __future__ import annotations

__all__ = (
    "MARKER",
    "PUNCH",
    "marker_canvas",
    "plain_framing",
    "plain_part",
    "subject_part",
)

from typing import Any

from . import reframe_math as rm
from .wand import reframe_layout as rl

# The strip's custom property that says "this strip's framing is crop + transform made by Make Vertical",
# holding the frame size it was made for.
MARKER = "coworker_plain_crop"
# The strip's custom property that says "this strip's zoom is keyed by Zoom To (a punch-in)": its region, zoom and ease.
PUNCH = "coworker_punch_in"


def _rect(fw: int, fh: int) -> rl.Rect:
    return (0, 0, int(fw), int(fh))


def plain_part(sw: int, sh: int, fw: int, fh: int, offset_x: float = 0.0, offset_y: float = 0.0,
               zoom: float = 1.0, *, tracked: bool = False) -> rl.Part:
    """The strip numbers for a *sw* x *sh* picture in a *fw* x *fh* frame at the modifier-style framing.

    Zoom below 1 would leave the frame uncovered (the modifier allowed it): it counts as 1.
    *tracked*: no horizontal crop, so the pan can be keyed on ``transform.offset_x`` alone."""
    rm.frame_ratio(sw, sh, fw, fh)                           # checks the sizes
    z = max(1.0, float(zoom)) * rm.BLEED
    scale = rm.cover_scale(sw, sh, fw, fh) * z
    u = rm.offset_to_subject(offset_x, sw, fw, scale)
    v = rm.offset_to_subject(offset_y, sh, fh, scale)
    return _finish(rl.part_for(0, _rect(fw, fh), sw, sh, fw, fh, rl.Framing(u, v, z)), tracked)


def subject_part(sw: int, sh: int, fw: int, fh: int, x: float, offset_y: float = 0.0, zoom: float = 1.0) -> rl.Part:
    """The strip numbers with the picture fraction *x* (0 = left edge, 1 = right edge) at the frame's
    middle, clamped so the picture still covers the frame; no horizontal crop (Follow Subject keys it)."""
    z = max(1.0, float(zoom)) * rm.BLEED
    scale = rm.cover_scale(sw, sh, fw, fh) * z
    v = rm.offset_to_subject(offset_y, sh, fh, scale)
    return _finish(rl.part_for(0, _rect(fw, fh), sw, sh, fw, fh, rl.Framing(float(x), v, z)), True)


def _finish(part: rl.Part, tracked: bool) -> rl.Part:
    if not tracked:
        return part
    _min_x, _max_x, min_y, max_y = part.crop
    return part._replace(crop=(0, 0, min_y, max_y))


def plain_framing(sw: int, sh: int, fw: int, fh: int, scale: float, offset: tuple[float, float]) -> dict[str, float]:
    """``{"Offset X", "Offset Y", "Zoom"}`` of a strip showing its picture at *scale* and *offset*."""
    f = rl.framing_of(_rect(fw, fh), scale, offset, sw, sh, fw, fh)
    cover = rm.cover_scale(sw, sh, fw, fh)
    scale_now = cover * f.zoom
    zoom = max(1.0, f.zoom / rm.BLEED)
    return {"Offset X": rm.subject_to_offset(f.u, sw, fw, scale_now),
            "Offset Y": rm.subject_to_offset(f.v, sh, fh, scale_now),
            "Zoom": zoom}


def marker_canvas(value: Any, default: tuple[int, int]) -> tuple[int, int]:
    """The frame size a strip's marker says it was made for (*default* when it does not say)."""
    try:
        w, h = int(value["w"]), int(value["h"])
    except (KeyError, TypeError, ValueError, IndexError):
        return default
    return (w, h) if w > 0 and h > 0 else default
