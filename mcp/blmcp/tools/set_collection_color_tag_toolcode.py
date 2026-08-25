# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for setting the color tag of a collection.
"""

__all__ = (
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Result(NamedTuple):
    status: str
    collection_name: str
    color_tag: str
    message: str


def main(collection_name: str, color: str) -> Result:
    """Set the color tag of a collection in the current scene."""
    import bpy  # pylint: disable=import-error

    # Validate color enum.
    valid_colors = {
        "NONE", "COLOR_01", "COLOR_02", "COLOR_03", "COLOR_04",
        "COLOR_05", "COLOR_06", "COLOR_07", "COLOR_08",
    }
    if color not in valid_colors:
        return Result(
            status="error",
            collection_name=collection_name,
            color_tag=color,
            message="Invalid color '{:s}'. Valid options: {:s}".format(
                color, ", ".join(sorted(valid_colors))),
        )

    # Find the collection.
    col = bpy.data.collections.get(collection_name)
    if col is None:
        return Result(
            status="error",
            collection_name=collection_name,
            color_tag=color,
            message="Collection '{:s}' not found".format(collection_name),
        )

    try:
        col.color_tag = color
        return Result(
            status="ok",
            collection_name=collection_name,
            color_tag=color,
            message="Collection '{:s}' color tag set to {:s}".format(collection_name, color),
        )
    except Exception as ex:
        return Result(
            status="error",
            collection_name=collection_name,
            color_tag=color,
            message="Failed to set color tag: {:s}".format(str(ex)),
        )
