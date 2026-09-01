# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for listing asset library catalog/directory structure.
"""

__all__ = (
    "Result",
    "Params",
    "main",
)

from typing import Any, NamedTuple


class Result(NamedTuple):
    status: str
    library_name: str
    catalogs: list[dict[str, Any]]
    asset_counts: dict[str, int]


class Params(NamedTuple):
    library_name: str = ""


def main(params: Params) -> Result:
    library_name = params.library_name
    """List the catalog/directory structure of asset libraries.

    Each catalog includes up to 10 sample asset names so the agent
    can see what is in each folder without a separate search call.
    """
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

                # Scan blend files for asset types and collect sample names.
                dir_counts = {"MATERIAL": 0, "NODETREE": 0, "OBJECT": 0, "WORLD": 0, "ACTION": 0, "COLLECTION": 0}
                asset_names: dict[str, list[str]] = {
                    "MATERIAL": [], "NODETREE": [], "OBJECT": [],
                    "WORLD": [], "ACTION": [], "COLLECTION": [],
                }
                for bf in blend_files:
                    bp = os.path.join(root, bf)
                    try:
                        with bpy.data.libraries.load(bp) as (data_from, _data_to):
                            for k, names_list in [
                                ("MATERIAL", data_from.materials),
                                ("NODETREE", data_from.node_groups),
                                ("OBJECT", data_from.objects),
                                ("WORLD", data_from.worlds),
                                ("ACTION", data_from.actions),
                                ("COLLECTION", data_from.collections),
                            ]:
                                count = len(names_list)
                                dir_counts[k] += count
                                # Collect first 10 asset names per type.
                                remaining = 10 - len(asset_names[k])
                                if remaining > 0:
                                    asset_names[k].extend(list(names_list)[:remaining])
                    except Exception:
                        pass

                # Only include directories with assets or subdirectories.
                has_assets = any(v > 0 for v in dir_counts.values())
                if has_assets or dirs:
                    # Build asset_names list: combine all types into one flat list.
                    sample_assets = []
                    for k in ("MATERIAL", "NODETREE", "OBJECT", "WORLD", "COLLECTION", "ACTION"):
                        for name in asset_names[k]:
                            sample_assets.append({"name": name, "type": k})

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
                        "asset_names": sample_assets[:10],
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
