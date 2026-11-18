# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Draw Reframe's layout icons (#606) as PNGs, with no dependencies beyond the standard library.

Each icon is a tall 9:16 frame (the outline and yellow border of Bforartists' strip colours, as in
Coworker's ``vertical`` icon) holding one small picture -- sky, sun, hills, in the colours of
Bforartists' ``image`` icon -- laid out the way the layout fills the frame:

* ``reframe_full``: the picture fills the frame;
* ``reframe_full_blur``: the whole wide picture in the middle, a blurred, dimmed copy behind it;
* ``reframe_full_tracked``: the picture and a target on the sun, the subject being followed;
* ``reframe_split_half`` / ``_top`` / ``_bottom``: two parts of the same picture, one above the other
  (1/2 + 1/2, 2/3 + 1/3, 1/3 + 2/3), the top one showing the sky and sun, the bottom one the hills;
* ``reframe_track_all``: the half split with a target in each part.

Full colour, one palette for both themes: the ``dark`` and ``light`` files are the same picture
(the scheme of ``icons/wand/``, which ``wand/icons.py`` loads).  All shapes here are drawn by us from
the palette; none is copied from another icon.

    python _misc/build_reframe_icons.py
"""

from __future__ import annotations

import os
import struct
import zlib

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT_DIR = os.path.join(_REPO, "addon", "bfa_coworker", "icons", "wand")
NAMES = ("reframe_full", "reframe_full_blur", "reframe_full_tracked", "reframe_split_half",
         "reframe_split_top", "reframe_split_bottom", "reframe_track_all")
SIZES = (32, 64)
THEMES = ("dark", "light")
SS = 4                      # supersampling
BASE = 64                   # design size in pixels

# Bforartists' palette steps (the `image` and `seq_strip` icons)
OUTLINE = (0x32, 0x00, 0x00)
BORDER = (0xfe, 0xcb, 0x32)
BORDER_SHADE = (0xfe, 0x98, 0x32)
SKY = (0x75, 0xb6, 0xfe)
SUN, SUN_RIM = (0xfd, 0xfe, 0x00), (0xfe, 0xc1, 0x00)
HILL, HILL_DARK = (0x3c, 0x98, 0x3c), (0x1e, 0x4c, 0x1e)
WHITE, BLACK = (0xfe, 0xfe, 0xfe), (0, 0, 0)
SEAM = (0x32, 0x00, 0x00)

# The frame: 9:16, centred, in the 64 px design.
FX0, FX1, FY0, FY1 = 15, 49, 2, 62
INNER = (FX0 + 4, FY0 + 4, FX1 - 4, FY1 - 4)       # x0, y0, x1, y1 (y down)


class Canvas:
    def __init__(self, size: int) -> None:
        self.n = size * SS
        self.px = [[None] * self.n for _ in range(self.n)]

    def put(self, x: int, y: int, colour: tuple) -> None:
        if 0 <= x < self.n and 0 <= y < self.n:
            self.px[y][x] = colour

    def rect(self, x0: float, y0: float, x1: float, y1: float, colour: tuple, clip: tuple | None = None) -> None:
        ax, ay, bx, by = (int(round(v * SS)) for v in (x0, y0, x1, y1))
        if clip:
            cx0, cy0, cx1, cy1 = (int(round(v * SS)) for v in clip)
            ax, ay, bx, by = max(ax, cx0), max(ay, cy0), min(bx, cx1), min(by, cy1)
        for y in range(ay, by):
            for x in range(ax, bx):
                self.put(x, y, colour)

    def poly(self, points: list, colour: tuple, clip: tuple | None = None) -> None:
        pts = [(px * SS, py * SS) for px, py in points]
        ys = [p[1] for p in pts]
        y_lo, y_hi = int(min(ys)), int(max(ys)) + 1
        if clip:
            y_lo, y_hi = max(y_lo, int(clip[1] * SS)), min(y_hi, int(clip[3] * SS))
        for y in range(y_lo, y_hi):
            fy = y + 0.5
            xs = []
            for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
                if (y0 <= fy < y1) or (y1 <= fy < y0):
                    xs.append(x0 + (fy - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for a, b in zip(xs[0::2], xs[1::2]):
                lo, hi = int(round(a)), int(round(b))
                if clip:
                    lo, hi = max(lo, int(clip[0] * SS)), min(hi, int(clip[2] * SS))
                for x in range(lo, hi):
                    self.put(x, y, colour)

    def disc(self, cx: float, cy: float, r: float, colour: tuple, clip: tuple | None = None, inner: float = 0.0) -> None:
        for y in range(int((cy - r) * SS), int((cy + r) * SS) + 1):
            for x in range(int((cx - r) * SS), int((cx + r) * SS) + 1):
                d = (((x + 0.5) / SS - cx) ** 2 + ((y + 0.5) / SS - cy) ** 2) ** 0.5
                if inner <= d <= r:
                    if clip and not (clip[0] * SS <= x < clip[2] * SS and clip[1] * SS <= y < clip[3] * SS):
                        continue
                    self.put(x, y, colour)

    def blur(self, box: tuple, radius: int) -> None:
        x0, y0, x1, y1 = (int(round(v * SS)) for v in box)
        r = radius * SS
        for horizontal in (True, False):
            for line in range(y0 if horizontal else x0, y1 if horizontal else x1):
                lo, hi = (x0, x1) if horizontal else (y0, y1)
                get = (lambda i: self.px[line][i]) if horizontal else (lambda i: self.px[i][line])
                vals = [get(i) or (0, 0, 0) for i in range(lo, hi)]
                out = []
                acc = [0, 0, 0]
                cnt = 0
                window: list = []
                for i in range(len(vals) + r):
                    if i < len(vals):
                        window.append(vals[i])
                        for k in range(3):
                            acc[k] += vals[i][k]
                        cnt += 1
                    if len(window) > 2 * r + 1:
                        gone = window.pop(0)
                        for k in range(3):
                            acc[k] -= gone[k]
                        cnt -= 1
                    if i >= r:
                        out.append(tuple(acc[k] // cnt for k in range(3)))
                for j, colour in enumerate(out):
                    if horizontal:
                        self.px[line][lo + j] = colour
                    else:
                        self.px[lo + j][line] = colour

    def dim(self, box: tuple, factor: float) -> None:
        x0, y0, x1, y1 = (int(round(v * SS)) for v in box)
        for y in range(y0, y1):
            for x in range(x0, x1):
                c = self.px[y][x]
                if c:
                    self.px[y][x] = tuple(int(v * factor) for v in c)

    def to_rgba(self, size: int) -> bytes:
        step = self.n // size
        out = bytearray()
        for y in range(size):
            for x in range(size):
                r = g = b = a = 0
                for j in range(step):
                    for i in range(step):
                        c = self.px[y * step + j][x * step + i]
                        if c:
                            r, g, b, a = r + c[0], g + c[1], b + c[2], a + 1
                total = step * step
                if a:
                    out += bytes((r // a, g // a, b // a, round(255 * a / total)))
                else:
                    out += b"\x00\x00\x00\x00"
        return bytes(out)


def write_png(path: str, size: int, rgba: bytes) -> None:
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
                 + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def picture(c: Canvas, rect: tuple, v0: float = 0.0, v1: float = 1.0) -> None:
    """The little picture (virtual 0..1 on both axes, y down) shown in *rect*: the slice *v0*..*v1* of its height."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0

    def X(u: float) -> float:
        return x0 + u * w

    def Y(v: float) -> float:
        return y0 + (v - v0) / (v1 - v0) * h

    c.rect(x0, y0, x1, y1, SKY)
    sun = (X(0.70), Y(0.26))
    r = 0.15 * w * 1.3 / 1.0
    c.disc(sun[0], sun[1], r, SUN_RIM, rect)
    c.disc(sun[0], sun[1], r * 0.72, SUN, rect)
    ground = Y(0.80)
    c.poly([(X(0), Y(0.70)), (X(0.30), Y(0.42)), (X(0.52), Y(0.70)), (X(0.74), Y(0.50)), (X(1.0), Y(0.76)),
            (X(1.0), Y(1.0)), (X(0), Y(1.0))], HILL_DARK, rect)
    c.poly([(X(0.30), Y(0.42)), (X(0.52), Y(0.70)), (X(0.62), Y(0.70)), (X(0.38), Y(0.53))], HILL_DARK, rect)
    c.rect(x0, ground, x1, y1 + 1, HILL, rect)
    c.poly([(X(0), Y(0.70)), (X(0.22), Y(0.52)), (X(0.42), Y(0.80)), (X(0), Y(0.80))], HILL, rect)


def target(c: Canvas, cx: float, cy: float, r: float = 5.0) -> None:
    c.disc(cx, cy, r + 1.0, BLACK, inner=r - 2.2)
    c.disc(cx, cy, r, WHITE, inner=r - 1.4)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        c.rect(min(cx + dx * (r - 3), cx + dx * (r + 3)) - (0.8 if dx == 0 else 0),
               min(cy + dy * (r - 3), cy + dy * (r + 3)) - (0.8 if dy == 0 else 0),
               max(cx + dx * (r - 3), cx + dx * (r + 3)) + (0.8 if dx == 0 else 0),
               max(cy + dy * (r - 3), cy + dy * (r + 3)) + (0.8 if dy == 0 else 0), WHITE)
    c.disc(cx, cy, 1.2, WHITE)


def frame(c: Canvas) -> None:
    c.rect(FX0, FY0, FX1, FY1, OUTLINE)
    c.rect(FX0 + 1.5, FY0 + 1.5, FX1 - 1.5, FY1 - 1.5, BORDER)
    c.rect(FX0 + 1.5, FY1 - 3.0, FX1 - 1.5, FY1 - 1.5, BORDER_SHADE)
    c.rect(INNER[0] - 1, INNER[1] - 1, INNER[2] + 1, INNER[3] + 1, OUTLINE)


def draw(name: str) -> Canvas:
    c = Canvas(BASE)
    frame(c)
    x0, y0, x1, y1 = INNER
    full = (x0, y0, x1, y1)
    height = y1 - y0
    if name in ("reframe_full", "reframe_full_tracked"):
        picture(c, full, 0.0, 1.0)
        if name.endswith("tracked"):
            target(c, x0 + 0.70 * (x1 - x0), y0 + 0.26 * height, 5.5)
    elif name == "reframe_full_blur":
        picture(c, full, 0.0, 1.0)
        c.blur(full, 3)
        c.dim(full, 0.62)
        mw = x1 - x0
        mh = mw * 9 / 16
        my0 = y0 + (height - mh) / 2
        c.rect(x0, my0 - 1, x1, my0 + mh + 1, WHITE)
        picture(c, (x0, my0, x1, my0 + mh), 0.0, 1.0)
    else:
        share = {"reframe_split_half": 0.5, "reframe_split_top": 2 / 3, "reframe_split_bottom": 1 / 3,
                 "reframe_track_all": 0.5}[name]
        seam = y0 + height * share
        # Two parts of the one picture, drawn so the shares read at 16 px: the top part shows the sky and
        # sun, the bottom part the hills, a dark seam between them.
        picture(c, (x0, y0, x1, seam), 0.0, 0.5)
        picture(c, (x0, seam, x1, y1), 0.5, 1.0)
        c.rect(x0, seam - 1.6, x1, seam + 1.6, SEAM)
        c.rect(x0, seam - 0.5, x1, seam + 0.5, WHITE)
        if name == "reframe_track_all":
            target(c, x0 + 0.70 * (x1 - x0), y0 + (seam - y0) * 0.5, 4.6)
            target(c, x0 + 0.40 * (x1 - x0), seam + (y1 - seam) * 0.5, 4.6)
    return c


def main() -> None:
    for name in NAMES:
        canvas = draw(name)
        for size in SIZES:
            rgba = canvas.to_rgba(size)
            for theme in THEMES:
                write_png(os.path.join(OUT_DIR, "{:s}_{:s}_{:d}.png".format(name, theme, size)), size, rgba)
        print("drew", name)


if __name__ == "__main__":
    main()
