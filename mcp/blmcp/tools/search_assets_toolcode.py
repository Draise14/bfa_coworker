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
    """Search across asset libraries by name, tag, and description.

    Matches are returned when the query appears in the asset name,
    any of its tags, or its description text.
    """
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
                                    if _matches_query(blend_path, mat_name, "MATERIAL", query_lower):
                                        matches.append({
                                            "name": mat_name,
                                            "type": "MATERIAL",
                                            "library": lib.name,
                                            "file": f,
                                        })
                            # Check node groups.
                            if not asset_type or asset_type == "NODETREE":
                                for ng_name in data_from.node_groups:
                                    if _matches_query(blend_path, ng_name, "NODETREE", query_lower):
                                        matches.append({
                                            "name": ng_name,
                                            "type": "NODETREE",
                                            "library": lib.name,
                                            "file": f,
                                        })
                            # Check objects.
                            if not asset_type or asset_type == "OBJECT":
                                for obj_name in data_from.objects:
                                    if _matches_query(blend_path, obj_name, "OBJECT", query_lower):
                                        matches.append({
                                            "name": obj_name,
                                            "type": "OBJECT",
                                            "library": lib.name,
                                            "file": f,
                                        })
                            # Check worlds.
                            if not asset_type or asset_type == "WORLD":
                                for world_name in data_from.worlds:
                                    if _matches_query(blend_path, world_name, "WORLD", query_lower):
                                        matches.append({
                                            "name": world_name,
                                            "type": "WORLD",
                                            "library": lib.name,
                                            "file": f,
                                        })
                            # Check collections.
                            if not asset_type or asset_type == "COLLECTION":
                                for col_name in data_from.collections:
                                    if _matches_query(blend_path, col_name, "COLLECTION", query_lower):
                                        matches.append({
                                            "name": col_name,
                                            "type": "COLLECTION",
                                            "library": lib.name,
                                            "file": f,
                                        })
                            # Check actions.
                            if not asset_type or asset_type == "ACTION":
                                for act_name in data_from.actions:
                                    if _matches_query(blend_path, act_name, "ACTION", query_lower):
                                        matches.append({
                                            "name": act_name,
                                            "type": "ACTION",
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


def _matches_query(blend_path: str, asset_name: str, asset_type: str, query_lower: str) -> bool:
    """Check if an asset matches the query by name, tags, or description."""
    # Fast path: name match.
    if query_lower in asset_name.lower():
        return True

    # Slower path: inspect asset_data for tags and description.
    try:
        import bpy  # pylint: disable=import-error

        with bpy.data.libraries.load(blend_path) as (data_from, data_to):
            # Load the specific datablock to inspect asset_data.
            if asset_type == "MATERIAL":
                data_to.materials = [asset_name]
                datablock = data_to.materials[0]
            elif asset_type == "NODETREE":
                data_to.node_groups = [asset_name]
                datablock = data_to.node_groups[0]
            elif asset_type == "OBJECT":
                data_to.objects = [asset_name]
                datablock = data_to.objects[0]
            elif asset_type == "WORLD":
                data_to.worlds = [asset_name]
                datablock = data_to.worlds[0]
            elif asset_type == "COLLECTION":
                data_to.collections = [asset_name]
                datablock = data_to.collections[0]
            elif asset_type == "ACTION":
                data_to.actions = [asset_name]
                datablock = data_to.actions[0]
            else:
                return False

        if datablock is None:
            return False

        # Check description.
        if hasattr(datablock, "description") and datablock.description:
            if query_lower in datablock.description.lower():
                return True

        # Check tags.
        if hasattr(datablock, "asset_data") and datablock.asset_data:
            if hasattr(datablock.asset_data, "tags"):
                for tag in datablock.asset_data.tags:
                    if query_lower in tag.name.lower():
                        return True

    except Exception:
        pass

    return False
