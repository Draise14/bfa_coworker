# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for searching across asset libraries.
"""

__all__ = (
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Result(NamedTuple):
    status: str
    query: str
    matches: list[dict[str, Any]]
    total_found: int


def main(query: str, library_name: str = "", asset_type: str = "") -> Result:
    """Search across asset libraries by name/tag/type."""
    import bpy  # pylint: disable=import-error
    import os

    matches = []
    query_lower = query.lower()

    try:
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            if library_name and lib.name != library_name:
                continue
            lib_path = str(lib.path) if lib.path else ""
            if not lib_path or not os.path.isdir(lib_path):
                continue

            # Scan blend files for assets.
            for root, _dirs, files in os.walk(lib_path):
                for f in files:
                    if not f.endswith(".blend"):
                        continue
                    blend_path = os.path.join(root, f)
                    # Try to open and scan for assets.
                    try:
                        with bpy.data.libraries.load(blend_path) as (data_from, _data_to):
                            # Check materials.
                            if not asset_type or asset_type == "MATERIAL":
                                for mat_name in data_from.materials:
                                    if query_lower in mat_name.lower():
                                        matches.append({
                                            "name": mat_name,
                                            "type": "MATERIAL",
                                            "library": lib.name,
                                            "file": f,
                                        })
                            # Check node groups.
                            if not asset_type or asset_type == "NODETREE":
                                for ng_name in data_from.node_groups:
                                    if query_lower in ng_name.lower():
                                        matches.append({
                                            "name": ng_name,
                                            "type": "NODETREE",
                                            "library": lib.name,
                                            "file": f,
                                        })
                            # Check objects.
                            if not asset_type or asset_type == "OBJECT":
                                for obj_name in data_from.objects:
                                    if query_lower in obj_name.lower():
                                        matches.append({
                                            "name": obj_name,
                                            "type": "OBJECT",
                                            "library": lib.name,
                                            "file": f,
                                        })
                            # Check worlds.
                            if not asset_type or asset_type == "WORLD":
                                for world_name in data_from.worlds:
                                    if query_lower in world_name.lower():
                                        matches.append({
                                            "name": world_name,
                                            "type": "WORLD",
                                            "library": lib.name,
                                            "file": f,
                                        })
                    except Exception:
                        continue  # Skip files that can't be opened.

                    # Limit results.
                    if len(matches) >= 20:
                        break
                if len(matches) >= 20:
                    break
            if len(matches) >= 20:
                break

    except Exception as ex:
        return Result(
            status="error: {:s}".format(str(ex)),
            query=query,
            matches=[],
            total_found=0,
        )

    return Result(
        status="ok",
        query=query,
        matches=matches[:20],
        total_found=len(matches),
    )
