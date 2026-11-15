# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The wand icons of Coworker's operators (Tier 4m section 3.3, Wand 0, #331).

Our own drawings (``icons/wand/<name>.svg``, rendered by ``_misc/build_icons.py``)
as PNGs in two variants: ``<name>_dark_<size>.png`` for a dark theme (light
strokes) and ``<name>_light_<size>.png`` for a light theme, at 32 and 64 px.
They are loaded with ``bpy.utils.previews`` and drawn with ``icon_value``, so
they work in plain Blender as in Bforartists.  The variant follows the theme's
menu text (light text = dark theme); the size follows the UI scale.  When the
previews can't be made, :func:`operator` falls back to a Blender icon.
"""

from __future__ import annotations

__all__ = (
    "ICON_DIR",
    "ICON_NAMES",
    "SIZES",
    "THEMES",
    "free",
    "icon_id",
    "operator",
    "png_path",
    "size_for",
    "theme_for",
)

import os
from typing import Any

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons", "wand")
# The first set: a familiar base glyph with the wand badge; "wand" is the generic one.
ICON_NAMES = ("remove_silences", "captions", "vertical", "transcribe", "tidy_nodes", "explain",
              "translate", "wand")
THEMES = ("dark", "light")
SIZES = (32, 64)

_STATE: dict[str, Any] = {"previews": None, "key": None}


def png_path(name: str, theme: str, size: int) -> str:
    return os.path.join(ICON_DIR, "{:s}_{:s}_{:d}.png".format(name, theme, size))


def theme_for(text_rgb: Any) -> str:
    """``"dark"`` when the theme's text is light (a dark theme), else ``"light"``."""
    r, g, b = (float(c) for c in tuple(text_rgb)[:3])
    return "dark" if 0.2126 * r + 0.7152 * g + 0.0722 * b >= 0.5 else "light"


def size_for(ui_scale: float) -> int:
    """32 px up to 1.25x UI scale, 64 px above (crisp on hi-DPI and on 2x toolshelf buttons)."""
    return 64 if ui_scale > 1.25 else 32


def _current_key() -> tuple[str, int]:
    import bpy  # pylint: disable=import-error
    prefs = bpy.context.preferences
    try:
        text = prefs.themes[0].user_interface.wcol_menu_item.text
    except (AttributeError, IndexError):
        text = (0.9, 0.9, 0.9)
    scale = getattr(prefs.system, "ui_scale", 1.0) * getattr(prefs.system, "pixel_size", 1.0)
    return theme_for(text), size_for(scale)


def icon_id(name: str) -> int:
    """The ``icon_value`` of wand icon *name* for the current theme, or 0."""
    try:
        key = _current_key()
        previews = _STATE["previews"]
        if previews is None or _STATE["key"] != key:
            import bpy.utils.previews  # pylint: disable=import-error
            if previews is not None:
                bpy.utils.previews.remove(previews)
            previews = bpy.utils.previews.new()
            theme, size = key
            for icon in ICON_NAMES:
                path = png_path(icon, theme, size)
                if os.path.isfile(path):
                    previews.load(icon, path, "IMAGE")
            _STATE["previews"], _STATE["key"] = previews, key
        preview = previews.get(name)
        return preview.icon_id if preview is not None else 0
    except Exception:  # pylint: disable=broad-exception-caught
        return 0        # no previews (background, a missing file): the caller's Blender icon


def operator(layout: Any, idname: str, *, name: str, fallback: str, text: str | None = None) -> Any:
    """``layout.operator`` with wand icon *name*, or the Blender icon *fallback*."""
    from ..shared import icon as safe_icon
    value = icon_id(name)
    kwargs = {} if text is None else {"text": text}
    if value:
        return layout.operator(idname, icon_value=value, **kwargs)
    return layout.operator(idname, icon=safe_icon(fallback), **kwargs)


def free() -> None:
    previews = _STATE["previews"]
    _STATE["previews"] = _STATE["key"] = None
    if previews is not None:
        try:
            import bpy.utils.previews  # pylint: disable=import-error
            bpy.utils.previews.remove(previews)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
