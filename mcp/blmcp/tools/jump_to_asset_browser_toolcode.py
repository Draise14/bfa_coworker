# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for switching to (or creating) the Asset Browser editor.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import NamedTuple


class Params(NamedTuple):
    library_name: str = ""
    catalog_path: str = ""
    allow_edits: bool = True


class Result(NamedTuple):
    status: str
    workspace: str | None = None
    area_ui_type: str | None = None
    library_set: str | None = None
    catalog_set: str | None = None
    created: bool | None = None
    message: str | None = None
    available_libraries: list[str] | None = None


def main(params: Params) -> Result:
    """Switch to (or create) the Asset Browser editor.

    Reuses an existing Asset Browser area if one is open in any workspace.
    Otherwise, when *allow_edits* is True, a new workspace is created by
    duplicating the current one and converting its main area to the Asset
    Browser.  Library and catalog selection are best-effort across Blender
    versions and reported back so the agent can correct course.
    """
    import bpy  # pylint: disable=import-error,no-name-in-module

    if bpy.app.background:
        return Result(status="error", message="Not available in background mode")
    if bpy.context.window is None:
        return Result(status="error", message="No active window")
    if bpy.context.screen is None:
        return Result(status="error", message="No active screen")

    available_libraries = [
        lib.name for lib in bpy.context.preferences.filepaths.asset_libraries
    ]

    def _is_asset_ui(ui_type: str) -> bool:
        # Blender 4.x+: 'ASSETS' (+ subgroups like 'ASSETS_OBJECTS').
        # Blender 3.x: 'ASSET_BROWSER'.
        return ui_type.startswith("ASSETS") or ui_type == "ASSET_BROWSER"

    # 1) Reuse an existing Asset Browser area in any workspace.
    for workspace in bpy.data.workspaces:
        for screen in workspace.screens:
            for area in screen.areas:
                if area.type == "FILE" and _is_asset_ui(area.ui_type):
                    bpy.context.window.workspace = workspace
                    space = area.spaces.active
                    lib_name, cat_name = _apply_filters(space, params)
                    return Result(
                        status="ok",
                        workspace=workspace.name,
                        area_ui_type=area.ui_type,
                        library_set=lib_name,
                        catalog_set=cat_name,
                        message="Reused existing Asset Browser in workspace '{:s}'".format(
                            workspace.name),
                        available_libraries=available_libraries,
                    )

    if not params.allow_edits:
        return Result(
            status="error",
            message="No Asset Browser editor open and allow_edits is False",
            available_libraries=available_libraries,
        )

    # 2) Create one by duplicating the current workspace.
    try:
        bpy.ops.workspace.duplicate()
    except RuntimeError as ex:
        return Result(status="error", message=str(ex), available_libraries=available_libraries)

    new_ws = bpy.context.window.workspace
    new_ws.name = "Asset Browser"
    area = max(bpy.context.screen.areas, key=lambda a: a.width * a.height, default=None)
    if area is None:
        return Result(status="error", message="No area available to convert")
    area.type = "FILE"
    ui_type_set = None
    for candidate in ("ASSETS", "ASSET_BROWSER"):
        try:
            area.ui_type = candidate
            ui_type_set = candidate
            break
        except Exception:
            continue

    space = area.spaces.active
    lib_name, cat_name = _apply_filters(space, params)
    return Result(
        status="ok",
        workspace=new_ws.name,
        area_ui_type=ui_type_set,
        library_set=lib_name,
        catalog_set=cat_name,
        created=True,
        message="Created Asset Browser workspace '{:s}'".format(new_ws.name),
        available_libraries=available_libraries,
    )


def _apply_filters(space, params: Params) -> tuple[str | None, str | None]:
    """Best-effort library + catalog selection on *space*.

    Returns (library_name, catalog_name) actually set (or None each).
    """
    lib_set = None
    cat_set = None
    p = getattr(space, "params", None)
    if p is None:
        return lib_set, cat_set

    if params.library_name:
        try:
            # Blender 4.x attribute name; 3.x used `asset_library_reference`.
            ref_attr = "asset_library_ref" if hasattr(p, "asset_library_ref") else "asset_library_reference"
            setattr(p, ref_attr, params.library_name)
            lib_set = params.library_name
        except Exception:
            lib_set = None

    if params.catalog_path:
        # 4.x catalogs are addressed by UUID; 3.x browsed directories.
        looks_uuid = len(params.catalog_path) == 36 and "-" in params.catalog_path
        try:
            if looks_uuid and hasattr(p, "catalog_id"):
                p.catalog_id = params.catalog_path
                cat_set = params.catalog_path
            elif hasattr(p, "directory"):
                lib_path = _library_path(params.library_name) if params.library_name else None
                if lib_path and getattr(p, "asset_library_ref", "") not in ("LOCAL",):
                    import os
                    p.directory = os.path.join(lib_path, params.catalog_path)
                    cat_set = params.catalog_path
        except Exception:
            cat_set = None
    return lib_set, cat_set


def _library_path(library_name: str) -> str | None:
    import bpy  # pylint: disable=import-error
    import os

    for lib in bpy.context.preferences.filepaths.asset_libraries:
        if lib.name == library_name and lib.path:
            return str(lib.path)
    return None