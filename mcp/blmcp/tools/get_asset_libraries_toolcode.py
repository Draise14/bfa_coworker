# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for listing all configured asset libraries.
"""

__all__ = (
    "Result",
    "Params",
    "main",
)

from typing import Any, NamedTuple


class Result(NamedTuple):
    status: str
    libraries: list[dict[str, Any]]


class Params(NamedTuple):
    pass


def main(params: Params) -> Result:
    del params  # No parameters for this tool.
    """List all configured asset libraries with their paths and asset counts."""
    import bpy  # pylint: disable=import-error

    libraries = []
    try:
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            lib_info = {
                "name": lib.name,
                "path": str(lib.path) if lib.path else "",
            }
            # Count assets in this library if path exists.
            import os
            if lib.path and os.path.isdir(str(lib.path)):
                asset_count = 0
                for root, _dirs, files in os.walk(str(lib.path)):
                    for f in files:
                        if f.endswith((".blend", ".blend1")):
                            asset_count += 1
                lib_info["blend_file_count"] = asset_count
            else:
                lib_info["blend_file_count"] = 0
            libraries.append(lib_info)
    except Exception as ex:
        return Result(
            status="error: {:s}".format(str(ex)),
            libraries=[],
        )

    return Result(
        status="ok",
        libraries=libraries,
    )
