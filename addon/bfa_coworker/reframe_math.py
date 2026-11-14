# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The maths of Make Vertical / Reframe (Video 3, #333).  Free of ``bpy``.

A strip's picture is ``src_w`` x ``src_h`` pixels, shown on a canvas (the render
size) of ``canvas_w`` x ``canvas_h``.  The *Reframe* compositor modifier scales
it about its centre and pans it.  Its inputs are kept in units that stay valid
whatever the sizes are, so a shot's framing can be dragged by hand:

* **Zoom** -- 1 = the picture just covers the canvas (Centre Crop) or just fits
  inside it (Blur Fill).
* **Offset X / Y** -- -1 .. 1 across the pan range: -1 shows the picture's left
  (bottom) edge, 1 its right (top) edge, 0 is centred.  Inside the range the
  picture always covers the canvas (Centre Crop).
* **Frame Width / Height** -- the canvas size relative to the picture
  (``canvas_w / src_w``, ``canvas_h / src_h``), set by Make Vertical; the group
  derives the cover and fit scales from them.

Follow subject turns the subject's horizontal position (0 = the picture's left
edge, 1 = its right edge) into Offset X keyframes: :func:`smooth_track` limits
the speed and holds still when the subject barely moves.
"""

from __future__ import annotations

__all__ = (
    "DEFAULTS",
    "BLEED",
    "BACKGROUND_OVERSCAN",
    "MODES",
    "blur_background_scale",
    "clamp",
    "cover_scale",
    "fit_scale",
    "frame_ratio",
    "framing_for",
    "mode_scale",
    "offset_px",
    "offset_to_subject",
    "pan_range",
    "plan_modifier",
    "sample_frames",
    "smooth_track",
    "subject_to_offset",
    "track_to_offsets",
)

from typing import Any, Iterable, Sequence

# The two framing modes (the operator's enum identifiers).
MODES = ("CROP", "BLUR_FILL")

# Centre Crop scales a hair past "just covers" so no half-covered edge row shows
# (a 16:9 picture scaled to exactly 1920 px tall left a soft last row in 5.2.4).
BLEED = 1.002
# Blur Fill's background is scaled past "covers" so the blur's soft edges stay
# outside the canvas.
BACKGROUND_OVERSCAN = 1.08

# The modifier's per-shot inputs and their defaults (names = the group's sockets).
DEFAULTS: dict[str, Any] = {
    "Offset X": 0.0,
    "Offset Y": 0.0,
    "Zoom": 1.0,
    "Blur Fill": False,
    "Blur Amount": 40.0,
    "Background Dim": 0.3,
}
# Inputs the user frames with: kept when a strip already has a Reframe modifier.
USER_INPUTS = ("Offset X", "Offset Y", "Zoom", "Blur Fill", "Blur Amount", "Background Dim")
# Inputs that describe the sizes: always rewritten (the canvas may have changed).
SIZE_INPUTS = ("Frame Width", "Frame Height")


def clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if value < lo else hi if value > hi else value


def _check(src_w: float, src_h: float, canvas_w: float, canvas_h: float) -> None:
    if min(src_w, src_h, canvas_w, canvas_h) <= 0:
        raise ValueError("sizes must be positive: source {}x{}, canvas {}x{}".format(
            src_w, src_h, canvas_w, canvas_h))


def frame_ratio(src_w: float, src_h: float, canvas_w: float, canvas_h: float) -> tuple[float, float]:
    """The canvas size relative to the picture: the modifier's Frame Width / Height."""
    _check(src_w, src_h, canvas_w, canvas_h)
    return canvas_w / src_w, canvas_h / src_h


def cover_scale(src_w: float, src_h: float, canvas_w: float, canvas_h: float) -> float:
    """Scale at which the picture just covers the canvas (centre crop, nothing stretched)."""
    rx, ry = frame_ratio(src_w, src_h, canvas_w, canvas_h)
    return max(rx, ry)


def fit_scale(src_w: float, src_h: float, canvas_w: float, canvas_h: float) -> float:
    """Scale at which the whole picture just fits inside the canvas (contain)."""
    rx, ry = frame_ratio(src_w, src_h, canvas_w, canvas_h)
    return min(rx, ry)


def blur_background_scale(src_w: float, src_h: float, canvas_w: float, canvas_h: float,
                          overscan: float = BACKGROUND_OVERSCAN) -> float:
    """Blur Fill's background: the picture scaled to cover, a little past the edges."""
    return cover_scale(src_w, src_h, canvas_w, canvas_h) * overscan


def mode_scale(mode: str, src_w: float, src_h: float, canvas_w: float, canvas_h: float,
               zoom: float = 1.0) -> float:
    """The foreground's scale for *mode* at *zoom* (what the group computes)."""
    if mode == "BLUR_FILL":
        return fit_scale(src_w, src_h, canvas_w, canvas_h) * zoom
    return cover_scale(src_w, src_h, canvas_w, canvas_h) * zoom * BLEED


def pan_range(src_w: float, src_h: float, canvas_w: float, canvas_h: float,
              scale: float) -> tuple[float, float]:
    """Half the difference between the scaled picture and the canvas, in canvas pixels.

    The picture's centre may move this far each way; for a picture larger than the
    canvas that keeps it covering the canvas, for a smaller one (Blur Fill) inside it.
    """
    _check(src_w, src_h, canvas_w, canvas_h)
    return abs(src_w * scale - canvas_w) / 2.0, abs(src_h * scale - canvas_h) / 2.0


def offset_px(offset: float, half_range: float) -> float:
    """The picture's shift in canvas pixels for a normalised offset (the group's maths).

    Positive offset pans the *frame* right / up, so the picture moves left / down.
    """
    return -clamp(offset) * half_range


def subject_to_offset(x: float, src_w: float, canvas_w: float, scale: float) -> float:
    """Offset X that puts the picture's horizontal fraction *x* (0..1) at the canvas centre,
    clamped so the picture still covers the canvas (or stays inside it)."""
    if src_w <= 0 or canvas_w <= 0 or scale <= 0:
        raise ValueError("sizes and scale must be positive")
    picture = src_w * scale
    half = (picture - canvas_w) / 2.0
    if abs(half) < 1e-9:
        return 0.0
    # Frame centre on the picture, in pixels from the picture's centre: (x - 0.5) * picture;
    # the frame pans by -shift, and shift = -offset * |half|.
    return clamp((clamp(x, 0.0, 1.0) - 0.5) * picture / abs(half))


def offset_to_subject(offset: float, src_w: float, canvas_w: float, scale: float) -> float:
    """The picture fraction (0..1) at the canvas centre for *offset* (inverse of the above)."""
    if src_w <= 0 or canvas_w <= 0 or scale <= 0:
        raise ValueError("sizes and scale must be positive")
    picture = src_w * scale
    half = abs(picture - canvas_w) / 2.0
    return 0.5 + clamp(offset) * half / picture


def framing_for(mode: str, src_w: float, src_h: float, canvas_w: float, canvas_h: float) -> dict[str, Any]:
    """Fresh modifier values for a shot in *mode* (defaults + the size inputs)."""
    if mode not in MODES:
        raise ValueError("mode must be one of {}".format(", ".join(MODES)))
    rx, ry = frame_ratio(src_w, src_h, canvas_w, canvas_h)
    values = dict(DEFAULTS)
    values["Blur Fill"] = mode == "BLUR_FILL"
    values["Frame Width"] = rx
    values["Frame Height"] = ry
    return values


def plan_modifier(existing: dict[str, Any] | None, mode: str, src_w: float, src_h: float,
                  canvas_w: float, canvas_h: float, reset: bool = False) -> tuple[str, dict[str, Any]]:
    """What Make Vertical writes on one strip: ``(action, values)``.

    * no Reframe modifier yet -> ``("added", fresh values for mode)``;
    * one already there -> ``("kept", only the size inputs)``: the shot's own Offset,
      Zoom, Blur Fill, Blur Amount and Dim stay as the user left them;
    * ``reset`` -> ``("reset", fresh values for mode)``.
    """
    fresh = framing_for(mode, src_w, src_h, canvas_w, canvas_h)
    if existing is None:
        return "added", fresh
    if reset:
        return "reset", fresh
    return "kept", {k: fresh[k] for k in SIZE_INPUTS}


def sample_frames(left: int, right: int, count: int) -> list[int]:
    """*count* frames spread over a shot ``[left, right)`` (centres of equal parts)."""
    length = right - left
    if length <= 0 or count <= 0:
        return []
    count = min(count, length)
    frames = []
    for i in range(count):
        f = left + int((i + 0.5) * length / count)
        if not frames or f != frames[-1]:
            frames.append(f)
    return frames


def _fill_missing(samples: Sequence[tuple[int, float | None]]) -> list[tuple[int, float]]:
    """Drop unknown positions; a shot with none at all gives ``[]``."""
    return [(int(f), float(x)) for f, x in samples if x is not None]


def _median3(values: list[float]) -> list[float]:
    if len(values) < 3:
        return list(values)
    out = [values[0]]
    for i in range(1, len(values) - 1):
        out.append(sorted(values[i - 1:i + 2])[1])
    out.append(values[-1])
    return out


def smooth_track(samples: Iterable[tuple[int, float | None]], fps: float,
                 max_speed: float = 0.25, hold: float = 0.04) -> list[tuple[int, float]]:
    """Sampled subject positions ``(frame, x)`` -> keyframes ``(frame, x)``.

    * a lone outlier is removed (median of three);
    * movement smaller than *hold* (fraction of the picture width) is ignored: the
      frame holds still instead of drifting;
    * the frame never pans faster than *max_speed* picture widths per second;
    * keys in the middle of a still stretch are dropped (two keys hold it).
    """
    pts = sorted(_fill_missing(list(samples)))
    if not pts:
        return []
    frames = [f for f, _x in pts]
    xs = _median3([x for _f, x in pts])
    fps = fps if fps > 0 else 24.0
    cur = clamp(xs[0], 0.0, 1.0)
    keys = [(frames[0], cur)]
    for f, x in zip(frames[1:], xs[1:]):
        x = clamp(x, 0.0, 1.0)
        target = cur if abs(x - cur) < hold else x
        step = max_speed * (f - keys[-1][0]) / fps
        cur = cur + clamp(target - cur, -step, step)
        keys.append((f, round(cur, 6)))
    # Drop redundant keys inside a hold (prev == cur == next).
    out = [keys[0]]
    for i in range(1, len(keys) - 1):
        if not (abs(keys[i][1] - keys[i - 1][1]) < 1e-9 and abs(keys[i][1] - keys[i + 1][1]) < 1e-9):
            out.append(keys[i])
    if len(keys) > 1:
        out.append(keys[-1])
    return out


def track_to_offsets(keys: Iterable[tuple[int, float]], src_w: float, canvas_w: float,
                     scale: float) -> list[tuple[int, float]]:
    """Subject keyframes ``(frame, x)`` -> Offset X keyframes ``(frame, offset)``."""
    return [(f, round(subject_to_offset(x, src_w, canvas_w, scale), 6)) for f, x in keys]
