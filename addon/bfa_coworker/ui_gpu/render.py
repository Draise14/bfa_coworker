# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Draw :class:`layout.DrawCmd` lists with Blender's ``gpu`` and ``blf`` (Tier 4,
task "GPU 1").  The only part of ``ui_gpu`` that needs a GPU context: call
:func:`render` from a ``draw_handler_add(..., "POST_PIXEL")`` callback.

* Rounded fills are plain triangles (no triangle fans: not every GPU backend
  has them); outlines are 1-pixel polylines.
* Text uses the UI font (font id 0) or Bforartists' bundled DejaVuSansMono
  for code.  ``blf`` has no font-weight control, so bold is drawn twice with
  a small offset.
* Icons are the full-colour Bforartists PNGs from :mod:`.icons` as textures
  (read with :mod:`.png`, never through ``bpy.data``): resampled on the CPU
  to the exact drawn size, premultiplied (clean soft edges), with the theme's
  Icon Saturation / Border / Alpha, drawn 1:1 at whole pixels and dimmed only
  by the command's alpha.  Names without an image are flat triangles.
* Clipping uses the GPU scissor, which also clips text.
"""

from __future__ import annotations

import math
import os
from typing import Any

import blf  # pylint: disable=import-error
import gpu  # pylint: disable=import-error
from gpu_extras.batch import batch_for_shader  # pylint: disable=import-error

from .icons import icon_pixels as _icon_pixels
from .icons import image_path as _icon_image_path
from .icons import triangles as _icon_triangles
from .layout import DrawCmd, Rect
from .png import read_rgba

__all__ = (
    "font_id",
    "measure",
    "render",
)

_FONTS: dict[str, int] = {"ui": 0}
_SHADERS: dict[str, Any] = {}
# (PNG path, side, saturation, border) -> GPUTexture, or None when it can't be read.
_TEXTURES: dict[tuple, Any] = {}
_SOURCES: dict[str, tuple[int, bytes] | None] = {}   # PNG path -> (size, straight RGBA)
_CORNER_STEPS = 6


def font_id(font: str) -> int:
    """``blf`` font id for "ui" or "mono" (loaded on first use, falls back to the UI font)."""
    if font in _FONTS:
        return _FONTS[font]
    fid = 0
    if font == "mono":
        import bpy  # pylint: disable=import-error
        fonts = bpy.utils.system_resource("DATAFILES", path="fonts") or ""
        path = os.path.join(fonts, "DejaVuSansMono.woff2")
        if os.path.isfile(path):
            loaded = blf.load(path)
            fid = loaded if loaded != -1 else 0
    _FONTS[font] = fid
    return fid


def measure(text: str, font: str, size: float) -> float:
    """Width of *text* in pixels (the ``Measure`` used by :class:`layout.Canvas`)."""
    fid = font_id(font)
    blf.size(fid, size)
    return float(blf.dimensions(fid, text)[0])


def _shader(name: str) -> Any:
    shader = _SHADERS.get(name)
    if shader is None:
        shader = gpu.shader.from_builtin(name)
        _SHADERS[name] = shader
    return shader


def _outline_points(rect: Rect, radius: float, rounded: bool | None = None) -> list[tuple[float, float]]:
    r = max(0.0, min(radius, rect.w / 2, rect.h / 2))
    if not (r >= 0.5 if rounded is None else rounded):
        return [(rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.top), (rect.x, rect.top)]
    pts = []
    corners = ((rect.right - r, rect.y + r, 270), (rect.right - r, rect.top - r, 0),
               (rect.x + r, rect.top - r, 90), (rect.x + r, rect.y + r, 180))
    for cx, cy, start in corners:
        for i in range(_CORNER_STEPS + 1):
            a = math.radians(start + 90 * i / _CORNER_STEPS)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _fill(rect: Rect, color: tuple, radius: float) -> None:
    pts = _outline_points(rect, radius)
    center = (rect.x + rect.w / 2, rect.y + rect.h / 2)
    tris = []
    for i, p in enumerate(pts):
        tris += [center, p, pts[(i + 1) % len(pts)]]
    shader = _shader("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRIS", {"pos": tris})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _stroke(rect: Rect, color: tuple, radius: float, width: float = 1.0) -> None:
    """An outline exactly *width* pixels wide (Blender's line width), on whole pixels.

    Drawn as a filled ring, not a smoothed line: a 1-pixel smooth line spreads
    over two pixels and reads thicker than the rest of the interface.
    """
    outer = Rect(round(rect.x), round(rect.y), round(rect.w), round(rect.h))
    w = max(1.0, round(width))
    rounded = min(radius, outer.w / 2, outer.h / 2) >= 0.5
    a = _outline_points(outer, radius, rounded)
    b = _outline_points(outer.inset(w), max(0.0, radius - w), rounded)
    tris = []
    for i in range(len(a)):
        j = (i + 1) % len(a)
        tris += [a[i], a[j], b[j], a[i], b[j], b[i]]
    shader = _shader("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRIS", {"pos": tris})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _source(path: str) -> tuple[int, bytes] | None:
    if path not in _SOURCES:
        try:
            with open(path, "rb") as fh:
                width, height, rgba = read_rgba(fh.read())
            _SOURCES[path] = (width, rgba) if width == height else None
        except (OSError, ValueError):
            _SOURCES[path] = None
    return _SOURCES[path]


def _texture(path: str, side: int, saturation: float, border: float) -> Any:
    key = (path, side, round(saturation, 3), round(border, 3))
    if key in _TEXTURES:
        return _TEXTURES[key]
    texture = None
    source = _source(path)
    if source is not None:
        rgba = _icon_pixels(source[1], source[0], side, saturation, border)
        row = side * 4   # rows run top-down, GPU textures bottom-up
        flipped = b"".join(rgba[(side - 1 - y) * row:(side - y) * row] for y in range(side))
        buffer = gpu.types.Buffer("FLOAT", side * side * 4, [b / 255.0 for b in flipped])
        texture = gpu.types.GPUTexture((side, side), format="RGBA8", data=buffer)
    if len(_TEXTURES) > 512:   # zooming through many sizes
        _TEXTURES.clear()
    _TEXTURES[key] = texture
    return texture


def _image(x: int, y: int, side: int, texture: Any, alpha: float) -> None:
    r, t = x + side, y + side
    shader = _shader("IMAGE_COLOR")
    batch = batch_for_shader(shader, "TRIS", {
        "pos": [(x, y), (r, y), (r, t), (x, y), (r, t), (x, t)],
        "texCoord": [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)],
    })
    gpu.state.blend_set("ALPHA_PREMULT")
    shader.bind()
    shader.uniform_sampler("image", texture)
    shader.uniform_float("color", (alpha, alpha, alpha, alpha))   # premultiplied dimming
    batch.draw(shader)
    gpu.state.blend_set("ALPHA")


def _picture(cmd: DrawCmd) -> None:
    """A Blender image (an attachment thumbnail), fitted into the command's rect."""
    import bpy  # pylint: disable=import-error
    image = bpy.data.images.get(cmd.text)
    if image is None or cmd.rect is None:
        return
    width, height = image.size[:]
    if width <= 0 or height <= 0:
        return
    try:
        texture = gpu.texture.from_image(image)
    except Exception:  # pylint: disable=broad-exception-caught
        return
    r = cmd.rect
    scale = min(r.w / width, r.h / height)
    w, h = width * scale, height * scale
    x, y = round(r.x + (r.w - w) / 2), round(r.y + (r.h - h) / 2)
    # Image textures are scene-linear: convert for the display like Blender's own previews.
    try:
        shader = _shader("IMAGE_SCENE_LINEAR_TO_REC709_SRGB")
    except (ValueError, SystemError):
        shader = _shader("IMAGE")
    batch = batch_for_shader(shader, "TRIS", {
        "pos": [(x, y), (x + w, y), (x + w, y + h), (x, y), (x + w, y + h), (x, y + h)],
        "texCoord": [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)],
    })
    shader.bind()
    shader.uniform_sampler("image", texture)
    batch.draw(shader)


def _icon(cmd: DrawCmd, style: Any) -> None:
    assert cmd.rect is not None
    # One texel per screen pixel: whole-pixel size and position keep icons crisp.
    side = max(1, round(min(cmd.rect.w, cmd.rect.h)))
    path = _icon_image_path(cmd.text, side)
    if path:
        saturation = getattr(style, "icon_saturation", 1.0)
        border = getattr(style, "icon_border", 0.0)
        texture = _texture(path, side, saturation, border)
        if texture is not None:
            x = round(cmd.rect.x + (cmd.rect.w - side) / 2)
            y = round(cmd.rect.y + (cmd.rect.h - side) / 2)
            # Full colour, like Blender's icons (only the theme's Icon Alpha dims them).
            _image(x, y, side, texture, getattr(style, "icon_alpha", 1.0))
            return
    tris = _icon_triangles(cmd.text, cmd.rect)
    if not tris:
        return
    shader = _shader("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRIS", {"pos": tris})
    shader.bind()
    shader.uniform_float("color", cmd.color)
    batch.draw(shader)


def _text(cmd: DrawCmd) -> None:
    fid = font_id(cmd.font)
    blf.size(fid, cmd.size)
    blf.color(fid, *cmd.color)
    assert cmd.rect is not None
    # Whole pixels: text on a half pixel is blurred into two, which reads faded.
    x, y = round(cmd.rect.x), round(cmd.rect.y)
    blf.position(fid, x, y, 0)
    blf.draw(fid, cmd.text)
    if cmd.bold:
        blf.position(fid, x + max(1, round(cmd.size / 16.0)), y, 0)
        blf.draw(fid, cmd.text)


def render(commands: list[DrawCmd], style: Any = None) -> None:
    """Execute *commands* (call inside a POST_PIXEL draw callback).

    *style* (the canvas's :class:`style.Style`) supplies the theme's icon
    settings; without it icons draw at full alpha and saturation, no border.
    """
    gpu.state.blend_set("ALPHA")
    clipped = False
    try:
        for cmd in commands:
            if cmd.kind == "rect" and cmd.rect is not None:
                _fill(cmd.rect, cmd.color, cmd.radius)
            elif cmd.kind == "outline" and cmd.rect is not None:
                _stroke(cmd.rect, cmd.color, cmd.radius, getattr(style, "line_width", 1.0))
            elif cmd.kind == "text":
                _text(cmd)
            elif cmd.kind == "icon" and cmd.rect is not None:
                _icon(cmd, style)
            elif cmd.kind == "image":
                _picture(cmd)
            elif cmd.kind == "clip" and cmd.rect is not None:
                r = cmd.rect
                gpu.state.scissor_test_set(True)
                gpu.state.scissor_set(int(r.x), int(r.y), max(0, int(r.w)), max(0, int(r.h)))
                clipped = True
            elif cmd.kind == "unclip":
                gpu.state.scissor_test_set(False)
                clipped = False
    finally:
        if clipped:
            gpu.state.scissor_test_set(False)
        gpu.state.blend_set("NONE")
