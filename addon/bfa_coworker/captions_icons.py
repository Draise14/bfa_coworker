# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The pictures of the Auto Captions dialog (#605): a badge per language and "Highlight + ..." word animations.

PNGs in ``icons/captions/`` (``<name>_64.png``, made by ``_misc/build_caption_icons.py`` from Bforartists' own
icon SVGs and badges drawn there), loaded with ``bpy.utils.previews`` and drawn with ``icon_value``, so they work
in plain Blender as in Bforartists.  :func:`icon_id` is 0 when a picture can't be loaded (no previews, a missing
file): the caller then uses a built-in icon.
"""

from __future__ import annotations

__all__ = ("ICON_DIR", "free", "icon_id", "png_path")

import os
from typing import Any

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "captions")
SIZE = 64
_STATE: dict[str, Any] = {"previews": None}


def png_path(name: str) -> str:
    return os.path.join(ICON_DIR, "{:s}_{:d}.png".format(name, SIZE))


def icon_id(name: str) -> int:
    """The ``icon_value`` of picture *name* (``"lang_en"``, ``"hl_pop"``), or 0."""
    try:
        previews = _STATE["previews"]
        if previews is None:
            import bpy.utils.previews  # pylint: disable=import-error
            previews = bpy.utils.previews.new()
            for entry in os.listdir(ICON_DIR):
                if entry.endswith("_{:d}.png".format(SIZE)):
                    previews.load(entry[:-len("_{:d}.png".format(SIZE))], os.path.join(ICON_DIR, entry), "IMAGE")
            _STATE["previews"] = previews
        preview = previews.get(name)
        return preview.icon_id if preview is not None else 0
    except Exception:  # pylint: disable=broad-exception-caught
        return 0


def free() -> None:
    previews, _STATE["previews"] = _STATE["previews"], None
    if previews is not None:
        try:
            import bpy.utils.previews  # pylint: disable=import-error
            bpy.utils.previews.remove(previews)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
