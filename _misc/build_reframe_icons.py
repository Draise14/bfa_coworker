# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Draw Reframe's layout icons (#606) as PNGs, with no dependencies beyond the standard library.

Each icon is a simple diagram of a 9:16 frame (bold dark outline, white halo; see #628), the
larger part in strong blue, the smaller part orange, a thick dark seam between them where the split is:

* ``reframe_full``: the whole frame in blue;
* ``reframe_full_blur``: the picture in the middle, a soft border fading out above and below it;
* ``reframe_full_tracked``: the whole frame and a target, the subject being followed;
* ``reframe_split_half`` / ``_top`` / ``_bottom``: two parts, 1/2 + 1/2, 2/3 + 1/3, 1/3 + 2/3, the larger
  one in strong blue;
* ``reframe_track_all``: the half split with a target in each part.

Made to read at 16 and 32 px: thick lines, two colours, no detail.

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
DARK = (0x1c, 0x1c, 0x1c)

# The frame: about 9:16, as wide as the 64 px design allows, a white halo and a bold dark outline so it
# reads on a dark and on a light theme at 16 px.
FX0, FX1, FY0, FY1 = 9, 55, 1, 63
INNER = (FX0 + 5, FY0 + 5, FX1 - 5, FY1 - 5)       # x0, y0, x1, y1 (y down)
BIG = (0x1f, 0x4f, 0xe8)       # the larger part: strong blue
SMALL = (0xff, 0xb3, 0x1a)     # the smaller part: orange, clearly another colour
PALE = (0xdf, 0xec, 0xff)      # where Blur Fill fades out


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


def target(c: Canvas, cx: float, cy: float, r: float = 7.0) -> None:
    """A small target: a white ring with a dark edge on both sides, and a cross."""
    c.disc(cx, cy, r + 1.6, DARK, inner=r - 3.2)
    c.disc(cx, cy, r, WHITE, inner=r - 1.8)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        x0, x1 = sorted((cx + dx * (r - 4.5), cx + dx * (r + 3.0)))
        y0, y1 = sorted((cy + dy * (r - 4.5), cy + dy * (r + 3.0)))
        c.rect(x0 - (1.0 if dy else 0), y0 - (1.0 if dx else 0), x1 + (1.0 if dy else 0), y1 + (1.0 if dx else 0), DARK)
        c.rect(x0 - (0.5 if dy else 0), y0 - (0.5 if dx else 0), x1 + (0.5 if dy else 0), y1 + (0.5 if dx else 0), WHITE)
    c.disc(cx, cy, 2.0, WHITE)


def frame(c: Canvas) -> tuple:
    """The 9:16 frame: a white halo, a bold dark outline, the inside; returns the inside's rectangle."""
    c.rect(FX0, FY0, FX1, FY1, WHITE)
    c.rect(FX0 + 2, FY0 + 2, FX1 - 2, FY1 - 2, DARK)
    return (FX0 + 5, FY0 + 5, FX1 - 5, FY1 - 5)


def seam(c: Canvas, x0: float, x1: float, y: float) -> None:
    """A thick dark seam (the bar between the two parts) with a thin white line in it."""
    c.rect(x0, y - 3.0, x1, y + 3.0, DARK)
    c.rect(x0, y - 0.8, x1, y + 0.8, WHITE)


def draw(name: str) -> Canvas:
    c = Canvas(BASE)
    x0, y0, x1, y1 = frame(c)
    height = y1 - y0
    cx = (x0 + x1) / 2
    if name == "reframe_full":
        c.rect(x0, y0, x1, y1, BIG)
    elif name == "reframe_full_tracked":
        c.rect(x0, y0, x1, y1, BIG)
        target(c, cx, y0 + height * 0.45, 12.0)
    elif name == "reframe_full_blur":
        # the whole picture in the middle, a visibly soft border above and below that fades to pale
        mid0, mid1 = y0 + height * 0.36, y0 + height * 0.64
        for row in range(int(y0 * SS), int(y1 * SS)):
            y = row / SS
            d = min(1.0, max(0.0, mid0 - y, y - mid1) / (mid0 - y0))
            colour = tuple(int(BIG[i] + (PALE[i] - BIG[i]) * (0.35 + 0.65 * d) * min(1.0, d * 4)) for i in range(3))
            c.rect(x0, y, x1, y + 1.0 / SS, colour)
        c.rect(x0, mid0, x1, mid1, BIG)
        c.rect(x0, mid0 - 1.2, x1, mid0, WHITE)
        c.rect(x0, mid1, x1, mid1 + 1.2, WHITE)
    else:
        share = {"reframe_split_half": 0.5, "reframe_split_top": 2 / 3, "reframe_split_bottom": 1 / 3,
                 "reframe_track_all": 0.5}[name]
        edge = y0 + height * share
        top_big = share >= 0.5
        c.rect(x0, y0, x1, edge, BIG if top_big else SMALL)
        c.rect(x0, edge, x1, y1, SMALL if top_big else BIG)
        seam(c, x0, x1, edge)
        if name == "reframe_track_all":
            target(c, cx, y0 + (edge - y0) * 0.5 - 1.0, 8.5)
            target(c, cx, edge + (y1 - edge) * 0.5 + 1.0, 8.5)
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
