# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Layout for GPU-drawn UI: rectangles, text wrapping, an immediate-mode canvas
(Tier 4, task "GPU 1").

The canvas does not draw.  Each frame it records **draw commands** (boxes,
text, clipping) and **hit areas** (where each button is), so:

* :mod:`render` turns the commands into ``gpu`` / ``blf`` calls (the only
  part that needs Blender's GPU);
* the input layer hit-tests the previous frame's areas to set hover, press
  and click in :class:`InteractionState` for the next frame.

Text is measured through a ``measure(text, font, size) -> width`` function:
:func:`render.measure` in Blender, a fixed-width stand-in in tests.

Coordinates are Blender region pixels: origin bottom-left, y up.  Helpers
that lay out downwards take a ``top`` and return the new top.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .style import RGBA, Style, WidgetColors

__all__ = (
    "Canvas",
    "DrawCmd",
    "InteractionState",
    "Measure",
    "Rect",
    "fixed_measure",
    "wrap_text",
)

# measure(text, font, size) -> width in pixels.  font: "ui" or "mono".
Measure = Callable[[str, str, float], float]


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle; (x, y) is the bottom-left corner."""

    x: float
    y: float
    w: float
    h: float

    @property
    def top(self) -> float:
        return self.y + self.h

    @property
    def right(self) -> float:
        return self.x + self.w

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def inset(self, dx: float, dy: float | None = None) -> "Rect":
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy, max(0.0, self.w - 2 * dx), max(0.0, self.h - 2 * dy))

    def intersect(self, other: "Rect") -> "Rect":
        x0, y0 = max(self.x, other.x), max(self.y, other.y)
        x1, y1 = min(self.right, other.right), min(self.top, other.top)
        return Rect(x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))


def wrap_text(text: str, width: float, measure: Measure, font: str = "ui",
              size: float = 11.0) -> list[str]:
    """Wrap *text* to *width* pixels.

    Words go to the next line when they don't fit; a word longer than the
    whole width is split by characters.  Newlines are kept, blank lines too.
    """
    width = max(1.0, width)
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            trial = word if not line else line + " " + word
            if measure(trial, font, size) <= width:
                line = trial
                continue
            if line:
                lines.append(line)
                line = ""
            # The word alone: split it if it is wider than the line.
            while word and measure(word, font, size) > width:
                cut = len(word)
                while cut > 1 and measure(word[:cut], font, size) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


@dataclass(frozen=True)
class DrawCmd:
    """One thing to draw.  *kind*: rect, outline, text, icon, clip, unclip.

    An ``icon`` command keeps the icon's name in *text* (see :mod:`.icons`).
    """

    kind: str
    rect: Rect | None = None
    color: RGBA = (1.0, 1.0, 1.0, 1.0)
    radius: float = 0.0
    text: str = ""
    font: str = "ui"
    size: float = 11.0
    bold: bool = False


@dataclass
class InteractionState:
    """Mouse and keyboard state the input layer writes, the canvas reads.

    ``clicked`` is set for exactly one frame after a press and release on the
    same widget id.
    """

    mouse: tuple[float, float] | None = None
    pressed: str | None = None
    clicked: str | None = None
    focused: str | None = None

    def hovered(self, rect: Rect) -> bool:
        return self.mouse is not None and rect.contains(*self.mouse)


@dataclass
class Canvas:
    """Immediate-mode builder for one region's frame."""

    style: Style
    measure: Measure
    state: InteractionState = field(default_factory=InteractionState)
    commands: list[DrawCmd] = field(default_factory=list)
    hit_areas: list[tuple[Rect, str]] = field(default_factory=list)
    _clips: list[Rect] = field(default_factory=list)

    # -- primitives ----------------------------------------------------------
    def fill(self, rect: Rect, color: RGBA, radius: float = 0.0) -> None:
        self.commands.append(DrawCmd("rect", rect, color, radius))

    def stroke(self, rect: Rect, color: RGBA, radius: float = 0.0) -> None:
        self.commands.append(DrawCmd("outline", rect, color, radius))

    def text(self, x: float, baseline: float, text: str, color: RGBA | None = None,
             font: str = "ui", bold: bool = False, size: float | None = None) -> None:
        self.commands.append(DrawCmd("text", Rect(x, baseline, 0.0, 0.0),
                                     color or self.style.text, text=text, font=font,
                                     size=size or self.style.font_size, bold=bold))

    def push_clip(self, rect: Rect) -> None:
        if self._clips:
            rect = rect.intersect(self._clips[-1])
        self._clips.append(rect)
        self.commands.append(DrawCmd("clip", rect))

    def pop_clip(self) -> None:
        self._clips.pop()
        self.commands.append(DrawCmd("clip", self._clips[-1]) if self._clips else DrawCmd("unclip"))

    # -- widgets ---------------------------------------------------------------
    def box(self, rect: Rect, colors: WidgetColors | None = None, selected: bool = False,
            outline: bool = True) -> None:
        """A themed box (``wcol_box`` by default; *selected* uses ``inner_sel``)."""
        colors = colors or self.style.box
        radius = self.style.radius(colors)
        self.fill(rect, colors.inner_sel if selected else colors.inner, radius)
        if outline:
            self.stroke(rect, colors.outline, radius)

    def label(self, rect: Rect, text: str, color: RGBA | None = None, font: str = "ui",
              bold: bool = False, align: str = "left") -> None:
        """One line of text, vertically centred in *rect*."""
        size = self.style.font_size
        width = self.measure(text, font, size)
        if align == "center":
            x = rect.x + (rect.w - width) / 2
        elif align == "right":
            x = rect.right - width
        else:
            x = rect.x
        baseline = rect.y + (rect.h - size) / 2 + size * 0.2
        self.text(x, baseline, text, color, font, bold)

    def wrapped(self, x: float, top: float, width: float, text: str, color: RGBA | None = None,
                font: str = "ui", bold: bool = False) -> float:
        """Draw *text* wrapped to *width* below *top*; return the new top."""
        size, line_h = self.style.font_size, self.style.line_height
        for line in wrap_text(text, width, self.measure, font, size):
            top -= line_h
            self.text(x, top + (line_h - size) / 2 + size * 0.2, line, color, font, bold)
        return top

    def text_height(self, text: str, width: float, font: str = "ui") -> float:
        """Height :meth:`wrapped` will use for *text* at *width*."""
        return len(wrap_text(text, width, self.measure, font, self.style.font_size)) * self.style.line_height

    def icon(self, rect: Rect, name: str, color: RGBA | None = None) -> None:
        """Icon *name* (a Blender icon name, see :mod:`.icons`) in the square *rect*."""
        self.commands.append(DrawCmd("icon", rect, color or self.style.text, text=name))

    def icon_size(self) -> float:
        """Side of an inline icon: about the height of capital letters plus a little."""
        return self.style.font_size * 1.15

    def button(self, rect: Rect, widget_id: str, label: str, active: bool = False,
               align: str = "center", icon: str = "") -> bool:
        """A themed button (``wcol_regular``); returns True on the frame it was clicked.

        With *icon*, the icon sits before the label (or alone when *label* is empty).
        """
        colors = self.style.button
        hovered = self.state.hovered(rect)
        pressed = self.state.pressed == widget_id
        inner = colors.inner_sel if (active or pressed) else colors.inner
        if hovered and not (active or pressed):
            inner = _lighten(inner, 0.06)
        radius = self.style.radius(colors)
        self.fill(rect, inner, radius)
        self.stroke(rect, colors.outline, radius)
        text_rect = rect.inset(self.style.pad, 0) if align != "center" else rect
        color = colors.text_sel if (active or pressed) else colors.text
        if icon:
            side = min(self.icon_size(), rect.h * 0.8)
            gap = self.style.pad * 0.5 if label else 0.0
            if align == "center":
                x = rect.x + (rect.w - side - gap - self.measure(label, "ui", self.style.font_size)) / 2
            else:
                x = text_rect.x
            self.icon(Rect(x, rect.y + (rect.h - side) / 2, side, side), icon, color)
            text_rect = Rect(x + side + gap, rect.y, max(0.0, rect.right - x - side - gap), rect.h)
            align = "left"
        if label:
            self.label(text_rect, label, color, align=align)
        self.hit_areas.append((rect, widget_id))
        return self.state.clicked == widget_id

    # -- hit testing -------------------------------------------------------------
    def hit_test(self, px: float, py: float) -> str | None:
        """The top-most widget id at (px, py), from this frame's areas."""
        for rect, widget_id in reversed(self.hit_areas):
            if rect.contains(px, py):
                return widget_id
        return None


def _lighten(color: RGBA, amount: float) -> RGBA:
    return (min(1.0, color[0] + amount), min(1.0, color[1] + amount),
            min(1.0, color[2] + amount), color[3])


def fixed_measure(char_width: float = 0.5) -> Measure:
    """A stand-in measure for tests: every character is *char_width* x size wide."""
    def measure(text: str, _font: str, size: float) -> float:
        return len(text) * size * char_width
    return measure
