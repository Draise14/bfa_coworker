# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for listing asset library catalog/directory structure.
"""

__all__ = (
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Result(NamedTuple):
    status: str
    library_name: str
    catalogs: list[dict[str, Any]]
    asset_counts: dict[str, int]


def main(library_name: str = "") -> Result:
    """List the catalog/directory structure of asset libraries."""
    import bpy  # pylint: disable=import-error
    import os

    all_catalogs = []
    total_counts = {"MATERIAL": 0, "NODETREE": 0, "OBJECT": 0, "WORLD": 0, "ACTION": 0, "COLLECTION": 0}

    try:
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            if library_name and lib.name != library_name:
                continue
            lib_path = str(lib.path) if lib.path else ""
            if not lib_path or not os.path.isdir(lib_path):
                continue

            # Walk the directory structure to find catalogs.
            for root, dirs, files in os.walk(lib_path):
                rel = os.path.relpath(root, lib_path)
                if rel == ".":
                    rel = ""

                # Count blend files in this directory.
                blend_files = [f for f in files if f.endswith(".blend")]

                # Scan blend files for asset types.
                dir_counts = {"MATERIAL": 0, "NODETREE": 0, "OBJECT": 0, "WORLD": 0, "ACTION": 0, "COLLECTION": 0}
                for bf in blend_files:
                    bp = os.path.join(root, bf)
                    try:
                        with bpy.data.libraries.load(bp) as (data_from, _data_to):
                            dir_counts["MATERIAL"] += len(data_from.materials)
                            dir_counts["NODETREE"] += len(data_from.node_groups)
                            dir_counts["OBJECT"] += len(data_from.objects)
                            dir_counts["WORLD"] += len(data_from.worlds)
                            dir_counts["ACTION"] += len(data_from.actions)
                            dir_counts["COLLECTION"] += len(data_from.collections)
                    except Exception:
                        pass

                # Only include directories with assets or subdirectories.
                has_assets = any(v > 0 for v in dir_counts.values())
                if has_assets or dirs:
                    catalog = {
                        "path": rel or "(root)",
                        "library": lib.name,
                        "blend_files": len(blend_files),
                        "materials": dir_counts["MATERIAL"],
                        "node_groups": dir_counts["NODETREE"],
                        "objects": dir_counts["OBJECT"],
                        "worlds": dir_counts["WORLD"],
                        "actions": dir_counts["ACTION"],
                        "collections": dir_counts["COLLECTION"],
                    }
                    all_catalogs.append(catalog)
                    for k, v in dir_counts.items():
                        total_counts[k] += v

        if not all_catalogs:
            return Result(
                status="no catalogs found" if not library_name else "library not found",
                library_name=library_name or "(all)",
                catalogs=[],
                asset_counts=total_counts,
            )

        return Result(
            status="ok",
            library_name=library_name or "(all)",
            catalogs=all_catalogs,
            asset_counts=total_counts,
        )

    except Exception as ex:
        return Result(
            status="error: {:s}".format(str(ex)),
            library_name=library_name or "(all)",
            catalogs=[],
            asset_counts=total_counts,
        )
