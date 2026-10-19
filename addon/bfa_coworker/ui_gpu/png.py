# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
A small PNG reader for the GPU chat's icons (Tier 4, task "GPU 2").

Reads the icon PNGs straight into RGBA bytes for a ``gpu`` texture, so the
icons never go through ``bpy.data.images`` (they'd show up in the artist's
image lists and could be saved into their .blend).  Handles what the icon
export writes: 8-bit RGBA / RGB / grey(+alpha), not interlaced, all five row
filters.  Free of ``bpy``.
"""

from __future__ import annotations

import struct
import zlib

__all__ = (
    "read_rgba",
)

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}   # colour type -> channels


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_rgba(data: bytes) -> tuple[int, int, bytes]:
    """Decode PNG *data* to ``(width, height, rgba)``, rows from the TOP down."""
    if not data.startswith(_SIGNATURE):
        raise ValueError("not a PNG")
    pos, idat = len(_SIGNATURE), []
    width = height = depth = ctype = interlace = 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
    if depth != 8 or ctype not in _CHANNELS or interlace:
        raise ValueError("unsupported PNG (depth {:d}, colour type {:d}, interlace {:d})".format(
            depth, ctype, interlace))
    channels = _CHANNELS[ctype]
    stride = width * channels
    raw = zlib.decompress(b"".join(idat))
    rows: list[bytearray] = []
    prev = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filt, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = prev[i]
            if filt == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filt == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filt == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif filt == 4:
                upleft = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, up, upleft)) & 0xFF
        rows.append(line)
        prev = line
    out = bytearray(width * height * 4)
    o = 0
    for line in rows:
        for x in range(width):
            px = line[x * channels:(x + 1) * channels]
            if channels == 4:
                out[o:o + 4] = px
            elif channels == 3:
                out[o:o + 4] = bytes((px[0], px[1], px[2], 255))
            elif channels == 2:
                out[o:o + 4] = bytes((px[0], px[0], px[0], px[1]))
            else:
                out[o:o + 4] = bytes((px[0], px[0], px[0], 255))
            o += 4
    return width, height, bytes(out)
