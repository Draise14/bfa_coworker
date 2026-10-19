# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Icons for the GPU chat, drawn as shapes (Tier 4, task "GPU 2").

The native chat leans on icons so a row reads before its words do (you,
Coworker, a finished turn, the Workshop, a warning, a note).  Python can't
draw Blender's built-in icons from ``gpu`` code -- they are compiled into the
program -- so this module draws its own simple glyphs, **named by the Blender
icon they stand for** (``"USER"``, ``"CHECKMARK"``, ``"PACKAGE"``, ...).  Both
chats speak the same icon vocabulary, glyphs take the theme colour they are
given, and they stay sharp at any zoom.

:func:`triangles` turns a name and a square into flat triangles (x, y points,
three per triangle) for ``render``; unknown names get a dot.  Free of ``bpy``.
"""

from __future__ import annotations

import math
from typing import Callable

from .layout import Rect

__all__ = (
    "NAMES",
    "triangles",
)

Point = tuple[float, float]


# -- primitives in a unit square (0..1, y up) ------------------------------------------

def _tri(a: Point, b: Point, c: Point) -> list[Point]:
    return [a, b, c]


def _quad(a: Point, b: Point, c: Point, d: Point) -> list[Point]:
    return [a, b, c, a, c, d]


def _rect(x: float, y: float, w: float, h: float) -> list[Point]:
    return _quad((x, y), (x + w, y), (x + w, y + h), (x, y + h))


def _seg(x0: float, y0: float, x1: float, y1: float, width: float) -> list[Point]:
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length * width / 2, dx / length * width / 2
    return _quad((x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny))


def _poly(points: list[Point], width: float, closed: bool = False) -> list[Point]:
    out: list[Point] = []
    pairs = list(zip(points, points[1:] + (points[:1] if closed else [])))
    for (x0, y0), (x1, y1) in pairs:
        out += _seg(x0, y0, x1, y1, width)
        out += _disc(x1, y1, width / 2, 6)       # round joints
    return out


def _disc(cx: float, cy: float, r: float, steps: int = 16, a0: float = 0.0,
          a1: float = 2 * math.pi) -> list[Point]:
    out: list[Point] = []
    for i in range(steps):
        t0 = a0 + (a1 - a0) * i / steps
        t1 = a0 + (a1 - a0) * (i + 1) / steps
        out += _tri((cx, cy), (cx + r * math.cos(t0), cy + r * math.sin(t0)),
                    (cx + r * math.cos(t1), cy + r * math.sin(t1)))
    return out


def _ring(cx: float, cy: float, r: float, width: float, steps: int = 20) -> list[Point]:
    out: list[Point] = []
    r0, r1 = r - width / 2, r + width / 2
    for i in range(steps):
        t0, t1 = 2 * math.pi * i / steps, 2 * math.pi * (i + 1) / steps
        c0, s0, c1, s1 = math.cos(t0), math.sin(t0), math.cos(t1), math.sin(t1)
        out += _quad((cx + r0 * c0, cy + r0 * s0), (cx + r1 * c0, cy + r1 * s0),
                     (cx + r1 * c1, cy + r1 * s1), (cx + r0 * c1, cy + r0 * s1))
    return out


def _frame(x: float, y: float, w: float, h: float, width: float) -> list[Point]:
    return _poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], width, closed=True)


def _diamond(size: float) -> list[Point]:
    c = 0.5
    return _quad((c, c - size), (c + size, c), (c, c + size), (c - size, c))


_W = 0.11   # stroke width


# -- glyphs ----------------------------------------------------------------------------

def _user() -> list[Point]:
    return _disc(0.5, 0.7, 0.18) + _disc(0.5, 0.08, 0.36, 12, 0.0, math.pi)


def _wizard() -> list[Point]:
    hat = _tri((0.2, 0.3), (0.8, 0.3), (0.58, 0.96))
    return hat + _rect(0.06, 0.18, 0.88, 0.12) + _disc(0.48, 0.52, 0.06, 8)


def _ghost() -> list[Point]:
    body = _disc(0.5, 0.58, 0.32, 16, 0.0, math.pi) + _rect(0.18, 0.22, 0.64, 0.36)
    for i in range(3):   # wavy hem
        x = 0.18 + i * 0.64 / 3
        body += _tri((x, 0.22), (x + 0.64 / 3, 0.22), (x + 0.32 / 3, 0.08))
    return body


def _check() -> list[Point]:
    return _poly([(0.14, 0.5), (0.4, 0.24), (0.86, 0.76)], _W * 1.3)


def _package() -> list[Point]:
    return _frame(0.16, 0.12, 0.68, 0.56, _W) + _rect(0.1, 0.68, 0.8, 0.16) + _seg(0.5, 0.68, 0.5, 0.46, _W)


def _warning() -> list[Point]:
    outline = _poly([(0.5, 0.9), (0.08, 0.12), (0.92, 0.12)], _W, closed=True)
    return outline + _seg(0.5, 0.64, 0.5, 0.4, _W) + _disc(0.5, 0.26, 0.06, 8)


def _error() -> list[Point]:
    return _ring(0.5, 0.5, 0.4, _W) + _seg(0.34, 0.34, 0.66, 0.66, _W) + _seg(0.34, 0.66, 0.66, 0.34, _W)


def _cross() -> list[Point]:
    return _seg(0.2, 0.2, 0.8, 0.8, _W * 1.3) + _seg(0.2, 0.8, 0.8, 0.2, _W * 1.3)


def _info() -> list[Point]:
    return _ring(0.5, 0.5, 0.4, _W) + _seg(0.5, 0.26, 0.5, 0.52, _W) + _disc(0.5, 0.68, 0.06, 8)


def _copy() -> list[Point]:
    return _frame(0.14, 0.14, 0.48, 0.48, _W) + _frame(0.4, 0.4, 0.46, 0.46, _W)


def _time() -> list[Point]:
    return _ring(0.5, 0.5, 0.4, _W) + _poly([(0.5, 0.76), (0.5, 0.5), (0.7, 0.42)], _W)


def _right() -> list[Point]:
    return _tri((0.32, 0.18), (0.32, 0.82), (0.78, 0.5))


def _down() -> list[Point]:
    return _tri((0.18, 0.68), (0.82, 0.68), (0.5, 0.22))


def _forward() -> list[Point]:
    return _seg(0.14, 0.5, 0.62, 0.5, _W) + _tri((0.56, 0.24), (0.56, 0.76), (0.9, 0.5))


def _mesh() -> list[Point]:
    top = [(0.5, 0.9), (0.86, 0.7), (0.86, 0.3), (0.5, 0.1), (0.14, 0.3), (0.14, 0.7)]
    return (_poly(top, _W, closed=True) + _seg(0.5, 0.5, 0.5, 0.1, _W * 0.8)
            + _seg(0.5, 0.5, 0.86, 0.7, _W * 0.8) + _seg(0.5, 0.5, 0.14, 0.7, _W * 0.8))


def _pan() -> list[Point]:
    out = _seg(0.5, 0.18, 0.5, 0.82, _W) + _seg(0.18, 0.5, 0.82, 0.5, _W)
    out += _tri((0.38, 0.8), (0.62, 0.8), (0.5, 0.96)) + _tri((0.38, 0.2), (0.62, 0.2), (0.5, 0.04))
    out += _tri((0.8, 0.38), (0.8, 0.62), (0.96, 0.5)) + _tri((0.2, 0.38), (0.2, 0.62), (0.04, 0.5))
    return out


def _script() -> list[Point]:
    return (_poly([(0.34, 0.78), (0.1, 0.5), (0.34, 0.22)], _W)
            + _poly([(0.66, 0.78), (0.9, 0.5), (0.66, 0.22)], _W) + _seg(0.58, 0.84, 0.42, 0.16, _W * 0.8))


def _image() -> list[Point]:
    return (_frame(0.1, 0.16, 0.8, 0.68, _W) + _tri((0.18, 0.24), (0.46, 0.62), (0.66, 0.24))
            + _tri((0.5, 0.24), (0.66, 0.46), (0.82, 0.24)) + _disc(0.7, 0.66, 0.07, 8))


def _pause() -> list[Point]:
    return _rect(0.24, 0.18, 0.16, 0.64) + _rect(0.6, 0.18, 0.16, 0.64)


def _dot() -> list[Point]:
    return _disc(0.5, 0.5, 0.16, 10)


_GLYPHS: dict[str, Callable[[], list[Point]]] = {
    "USER": _user,
    "WIZARD": _wizard,
    "GHOST_ENABLED": _ghost,
    "CHECKMARK": _check,
    "PACKAGE": _package,
    "WARNING": _warning,
    "ERROR": _error,
    "CANCEL": _error,
    "X": _cross,
    "INFO": _info,
    "COPYDOWN": _copy,
    "TIME": _time,
    "SORTTIME": _time,
    "TRIA_RIGHT": _right,
    "PLAY": _right,
    "TRIA_DOWN": _down,
    "FORWARD": _forward,
    "OUTLINER_OB_MESH": _mesh,
    "VIEW_PAN": _pan,
    "SCRIPT": _script,
    "CONSOLE": _script,
    "IMAGE_DATA": _image,
    "PAUSE": _pause,
    # Heading markers: keyframe shapes that shrink with the level, like the native chat.
    "KEYTYPE_EXTREME_VEC": lambda: _diamond(0.36),
    "KEYTYPE_MOVING_HOLD_VEC": lambda: _diamond(0.3),
    "KEYTYPE_JITTER_VEC": lambda: _diamond(0.24),
    "KEYTYPE_BREAKDOWN_VEC": lambda: _diamond(0.2),
    "DOT": _dot,
}

NAMES = frozenset(_GLYPHS)
_CACHE: dict[str, list[Point]] = {}


def triangles(name: str, rect: Rect) -> list[Point]:
    """Triangles (three points each) drawing icon *name* into the square *rect*."""
    unit = _CACHE.get(name)
    if unit is None:
        unit = _CACHE[name] = _GLYPHS.get(name, _dot)()
    side = min(rect.w, rect.h)
    x0 = rect.x + (rect.w - side) / 2
    y0 = rect.y + (rect.h - side) / 2
    return [(x0 + x * side, y0 + y * side) for x, y in unit]
