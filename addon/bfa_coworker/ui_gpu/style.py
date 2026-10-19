# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Style for GPU-drawn UI, taken from the user's theme (Tier 4, task "GPU 1").

:func:`style_from_preferences` reads colours, roundness, font size and scale
from ``bpy.context.preferences`` (passed in, so this module never imports
``bpy`` and is unit-tested with stand-ins).  Every value has a fallback, so a
theme missing a field (older builds, partial themes) still draws.

Read the style on every draw: the user can switch themes or UI scale at any
time, and reading a few dozen attributes is cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

__all__ = (
    "RGBA",
    "Style",
    "WidgetColors",
    "style_from_preferences",
)

RGBA = tuple[float, float, float, float]


def _rgba(value: Any, fallback: RGBA, alpha: float = 1.0) -> RGBA:
    """A theme colour as RGBA (theme text colours are RGB)."""
    try:
        vals = [float(v) for v in value]
    except (TypeError, ValueError):
        return fallback
    if len(vals) == 3:
        return (vals[0], vals[1], vals[2], alpha)
    if len(vals) == 4:
        return (vals[0], vals[1], vals[2], vals[3])
    return fallback


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class WidgetColors:
    """One theme widget style (``wcol_*``)."""

    inner: RGBA
    inner_sel: RGBA
    outline: RGBA
    text: RGBA
    text_sel: RGBA
    roundness: float
    item: RGBA = (0.5, 0.5, 0.5, 1.0)     # sliders: the scrollbar handle, progress fill

    @staticmethod
    def read(wcol: Any, fallback: "WidgetColors") -> "WidgetColors":
        if wcol is None:
            return fallback
        return WidgetColors(
            inner=_rgba(getattr(wcol, "inner", None), fallback.inner),
            inner_sel=_rgba(getattr(wcol, "inner_sel", None), fallback.inner_sel),
            outline=_rgba(getattr(wcol, "outline", None), fallback.outline),
            text=_rgba(getattr(wcol, "text", None), fallback.text),
            text_sel=_rgba(getattr(wcol, "text_sel", None), fallback.text_sel),
            roundness=float(getattr(wcol, "roundness", fallback.roundness)),
            item=_rgba(getattr(wcol, "item", None), fallback.item),
        )


_DARK_BOX = WidgetColors((0.20, 0.20, 0.20, 1.0), (0.30, 0.30, 0.30, 1.0), (0.12, 0.12, 0.12, 1.0),
                         (0.90, 0.90, 0.90, 1.0), (1.0, 1.0, 1.0, 1.0), 0.2)
_DARK_BUTTON = WidgetColors((0.33, 0.33, 0.33, 1.0), (0.33, 0.50, 0.76, 1.0), (0.10, 0.10, 0.10, 1.0),
                            (0.90, 0.90, 0.90, 1.0), (1.0, 1.0, 1.0, 1.0), 0.3)
_DARK_FIELD = WidgetColors((0.12, 0.12, 0.12, 1.0), (0.20, 0.20, 0.20, 1.0), (0.10, 0.10, 0.10, 1.0),
                           (0.85, 0.85, 0.85, 1.0), (1.0, 1.0, 1.0, 1.0), 0.2)
_DARK_SCROLL = WidgetColors((0.31, 0.31, 0.31, 0.5), (0.39, 0.39, 0.39, 0.5), (0.20, 0.20, 0.20, 1.0),
                            (0.90, 0.90, 0.90, 1.0), (1.0, 1.0, 1.0, 1.0), 0.5, (0.50, 0.50, 0.50, 1.0))
_DARK_TOOLTIP = WidgetColors((0.10, 0.10, 0.10, 1.0), (0.20, 0.20, 0.20, 1.0), (0.0, 0.0, 0.0, 1.0),
                             (0.90, 0.90, 0.90, 1.0), (1.0, 1.0, 1.0, 1.0), 0.2)


@dataclass(frozen=True)
class Style:
    """Everything the GPU UI needs to look like the rest of the interface."""

    back: RGBA = (0.16, 0.16, 0.16, 1.0)          # panel background
    header: RGBA = (0.20, 0.20, 0.20, 1.0)        # panel header
    outline: RGBA = (0.10, 0.10, 0.10, 1.0)       # panel outline
    text: RGBA = (0.85, 0.85, 0.85, 1.0)          # panel text
    title: RGBA = (0.95, 0.95, 0.95, 1.0)         # panel title
    panel_roundness: float = 0.4
    box: WidgetColors = _DARK_BOX                 # message boxes
    button: WidgetColors = _DARK_BUTTON           # buttons; inner_sel = your messages / active
    text_field: WidgetColors = _DARK_FIELD        # input field, code blocks
    tooltip: WidgetColors = _DARK_TOOLTIP
    scroll: WidgetColors = _DARK_SCROLL           # scrollbars (wcol_scroll: inner = track, item = handle)
    cursor: RGBA = (0.27, 0.6, 1.0, 1.0)          # text cursor
    link: RGBA = (0.4, 0.7, 1.0, 1.0)             # hyperlinks
    # Keyframe colours (extreme, moving hold, jitter, breakdown) and their border:
    # heading markers use them, like the native chat's keyframe icons.
    keyframes: tuple[RGBA, RGBA, RGBA, RGBA] = (
        (0.79, 0.47, 0.41, 1.0), (0.62, 0.59, 0.58, 1.0), (0.41, 0.60, 0.34, 1.0), (0.36, 0.55, 0.66, 1.0))
    keyframe_border: RGBA = (0.0, 0.0, 0.0, 1.0)
    # The theme's icon settings (Interface > Icons): alpha, saturation, border.
    icon_alpha: float = 1.0
    icon_saturation: float = 1.0
    icon_border: float = 0.0
    font_points: float = 11.0                     # UI style "widget" points
    ui_scale: float = 1.0                         # Blender's UI scale x pixel size
    line_width: float = 1.0                       # outline width in pixels (Interface > Line Width)
    zoom: float = 1.0                             # the chat's own zoom (Ctrl+wheel)
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def scale(self) -> float:
        return self.ui_scale * self.zoom

    @property
    def unit(self) -> float:
        """Blender's "widget unit" (20 px at scale 1)."""
        return 20.0 * self.scale

    @property
    def font_size(self) -> float:
        return self.font_points * self.scale

    @property
    def line_height(self) -> float:
        return self.font_size * 1.45

    @property
    def pad(self) -> float:
        return self.unit * 0.4

    def radius(self, colors: WidgetColors | None = None) -> float:
        """Corner radius in pixels, the way Blender derives it from roundness."""
        roundness = (colors or self.box).roundness
        return max(0.0, min(1.0, roundness)) * self.unit * 0.5

    def with_zoom(self, zoom: float) -> "Style":
        return replace(self, zoom=max(0.5, min(3.0, zoom)))


def style_from_preferences(prefs: Any, zoom: float = 1.0) -> Style:
    """Build a :class:`Style` from ``bpy.context.preferences`` (or a stand-in)."""
    base = Style()
    try:
        ui = prefs.themes[0].user_interface
    except (AttributeError, IndexError, TypeError):
        return replace(base, zoom=zoom)
    system = getattr(prefs, "system", None)
    view = getattr(prefs, "view", None)
    scale = float(getattr(system, "ui_scale", 0.0) or 0.0)
    if scale <= 0.0:  # 0 in background mode (no window)
        scale = float(getattr(view, "ui_scale", 1.0) or 1.0) * float(getattr(system, "pixel_size", 1.0) or 1.0)
    keyframes = base.keyframes
    try:
        anim = prefs.themes[0].common.anim
        keyframes = tuple(_rgba(getattr(anim, "keyframe_" + kind, None), fallback)
                          for kind, fallback in zip(("extreme", "moving_hold", "jitter", "breakdown"),
                                                    base.keyframes))
    except (AttributeError, IndexError, TypeError):
        pass
    border = base.keyframe_border
    try:
        border = _rgba(prefs.themes[0].dopesheet_editor.keyframe_border, base.keyframe_border)
    except (AttributeError, IndexError, TypeError):
        pass
    points = base.font_points
    try:
        points = float(prefs.ui_styles[0].widget.points)
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    return Style(
        back=_rgba(getattr(ui, "panel_back", None), base.back),
        header=_rgba(getattr(ui, "panel_header", None), base.header),
        outline=_rgba(getattr(ui, "panel_outline", None), base.outline),
        text=_rgba(getattr(ui, "panel_text", None), base.text),
        title=_rgba(getattr(ui, "panel_title", None), base.title),
        panel_roundness=float(getattr(ui, "panel_roundness", base.panel_roundness)),
        box=WidgetColors.read(getattr(ui, "wcol_box", None), base.box),
        button=WidgetColors.read(getattr(ui, "wcol_regular", None), base.button),
        text_field=WidgetColors.read(getattr(ui, "wcol_text", None), base.text_field),
        tooltip=WidgetColors.read(getattr(ui, "wcol_tooltip", None), base.tooltip),
        scroll=WidgetColors.read(getattr(ui, "wcol_scroll", None), base.scroll),
        cursor=_rgba(getattr(ui, "widget_text_cursor", None), base.cursor),
        link=_rgba(getattr(ui, "link", None), base.link),
        keyframes=keyframes,  # type: ignore[arg-type]
        keyframe_border=border,
        icon_alpha=_float(getattr(ui, "icon_alpha", None), base.icon_alpha),
        icon_saturation=_float(getattr(ui, "icon_saturation", None), base.icon_saturation),
        icon_border=_float(getattr(ui, "icon_border_intensity", None), base.icon_border),
        font_points=points,
        ui_scale=scale,
        line_width=max(1.0, float(getattr(system, "pixel_size", 1.0) or 1.0)),
        zoom=max(0.5, min(3.0, zoom)),
    )
