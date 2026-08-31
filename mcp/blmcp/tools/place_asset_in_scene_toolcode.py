# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for placing a collection or object asset at an explicit world transform.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple, Optional


class Params(NamedTuple):
    library_name: str
    asset_name: str
    asset_type: str = ""
    link_mode: str = "APPEND"
    # location/scale in world units; rotation in degrees (XYZ Euler).
    location: Optional[tuple[float, float, float]] = None
    rotation: Optional[tuple[float, float, float]] = None
    scale: Optional[tuple[float, float, float]] = None


class Result(NamedTuple):
    status: str
    asset_name: str
    asset_type: str
    link_mode: str
    position: Optional[list[float]]
    objects_affected: int
    message: str


def main(params: Params) -> Result:
    """Place a COLLECTION or OBJECT asset at an explicit world transform.

    *link_mode* ``"APPEND"`` (default) loads a full copy and edits its
    transform directly.  ``"LINK"`` keeps a shared reference: for
    collections this creates an empty + collection instance placed at the
    requested transform (objects inside linked collections cannot be moved).

    Supports OBJECT and COLLECTION assets only.  For other asset types use
    ``load_asset_in_context`` instead.
    """
    import bpy  # pylint: disable=import-error
    import os

    link = params.link_mode.upper() == "LINK"

    try:
        # Find the library path.
        lib_path = None
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            if lib.name == params.library_name:
                lib_path = str(lib.path) if lib.path else None
                break
        if not lib_path or not os.path.isdir(lib_path):
            return Result(
                status="error",
                asset_name=params.asset_name,
                asset_type=params.asset_type or "unknown",
                link_mode=params.link_mode,
                position=None,
                objects_affected=0,
                message="Library '{:s}' not found or path invalid".format(params.library_name),
            )

        # Find the blend file containing this asset.
        blend_path = _find_blend_path(lib_path, params.asset_name)
        if not blend_path:
            return Result(
                status="error",
                asset_name=params.asset_name,
                asset_type=params.asset_type or "unknown",
                link_mode=params.link_mode,
                position=None,
                objects_affected=0,
                message="Asset '{:s}' not found in library '{:s}'".format(
                    params.asset_name, params.library_name),
            )

        asset_type = params.asset_type or _detect_type(blend_path, params.asset_name)
        if asset_type not in ("OBJECT", "COLLECTION"):
            return Result(
                status="error",
                asset_name=params.asset_name,
                asset_type=asset_type,
                link_mode=params.link_mode,
                position=None,
                objects_affected=0,
                message=(
                    "place_asset_in_scene supports OBJECT and COLLECTION assets "
                    "(got '{:s}'); use load_asset_in_context for this type".format(asset_type)
                ),
            )

        if asset_type == "OBJECT":
            return _place_object(params, blend_path, link)
        return _place_collection(params, blend_path, link)

    except Exception as ex:
        return Result(
            status="error",
            asset_name=params.asset_name,
            asset_type=params.asset_type or "unknown",
            link_mode=params.link_mode,
            position=None,
            objects_affected=0,
            message="Failed to place asset: {:s}".format(str(ex)),
        )


def _find_blend_path(lib_path: str, asset_name: str) -> str | None:
    """Walk *lib_path* and return the first blend containing *asset_name*."""
    import bpy  # pylint: disable=import-error
    import os

    for root, _dirs, files in os.walk(lib_path):
        for f in files:
            if not f.endswith(".blend"):
                continue
            candidate = os.path.join(root, f)
            try:
                with bpy.data.libraries.load(candidate) as (data_from, _data_to):
                    all_names = (
                        list(data_from.materials)
                        + list(data_from.node_groups)
                        + list(data_from.objects)
                        + list(data_from.worlds)
                        + list(data_from.actions)
                        + list(data_from.collections)
                    )
                    if asset_name in all_names:
                        return candidate
            except Exception:
                continue
    return None


def _detect_type(blend_path: str, asset_name: str) -> str:
    """Auto-detect the asset type by checking each datablock collection."""
    import bpy  # pylint: disable=import-error

    try:
        with bpy.data.libraries.load(blend_path) as (data_from, _data_to):
            if asset_name in data_from.collections:
                return "COLLECTION"
            if asset_name in data_from.objects:
                return "OBJECT"
            if asset_name in data_from.materials:
                return "MATERIAL"
            if asset_name in data_from.node_groups:
                return "NODETREE"
            if asset_name in data_from.worlds:
                return "WORLD"
            if asset_name in data_from.actions:
                return "ACTION"
    except Exception:
        pass
    return "UNKNOWN"


def _place_object(params: Params, blend_path: str, link: bool) -> Result:
    import bpy  # pylint: disable=import-error
    import math

    with bpy.data.libraries.load(blend_path, link=link) as (data_from, data_to):
        data_to.objects = [params.asset_name]
    obj = data_to.objects[0]
    if obj is None:
        return Result(
            status="error",
            asset_name=params.asset_name,
            asset_type="OBJECT",
            link_mode=params.link_mode,
            position=None,
            objects_affected=0,
            message="Failed to load object '{:s}'".format(params.asset_name),
        )

    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    if params.location is not None:
        obj.location = params.location
    if params.rotation is not None:
        obj.rotation_euler = tuple(math.radians(v) for v in params.rotation)
    if params.scale is not None:
        obj.scale = params.scale

    desc = []
    if params.location is not None:
        desc.append("at ({:.2f}, {:.2f}, {:.2f})".format(*params.location))
    if params.rotation is not None:
        desc.append("rot ({:.1f}, {:.1f}, {:.1f}) deg".format(*params.rotation))
    if params.scale is not None:
        desc.append("scale ({:.2f}, {:.2f}, {:.2f})".format(*params.scale))

    return Result(
        status="ok",
        asset_name=params.asset_name,
        asset_type="OBJECT",
        link_mode="LINK" if link else "APPEND",
        position=list(params.location) if params.location is not None else None,
        objects_affected=1,
        message="Object '{:s}' {:s} to scene{:s}".format(
            params.asset_name, "linked" if link else "appended",
            (" " + " ".join(desc)) if desc else ""),
    )


def _place_collection(params: Params, blend_path: str, link: bool) -> Result:
    import bpy  # pylint: disable=import-error
    import math

    with bpy.data.libraries.load(blend_path, link=link) as (data_from, data_to):
        data_to.collections = [params.asset_name]
    col = data_to.collections[0]
    if col is None:
        return Result(
            status="error",
            asset_name=params.asset_name,
            asset_type="COLLECTION",
            link_mode=params.link_mode,
            position=None,
            objects_affected=0,
            message="Failed to load collection '{:s}'".format(params.asset_name),
        )

    if link:
        # Linked collection: objects cannot be moved, so instance it via an
        # empty placed at the requested transform.
        empty = bpy.data.objects.new(params.asset_name + "_Instance", None)
        empty.instance_collection = col
        bpy.context.scene.collection.objects.link(empty)
        if params.location is not None:
            empty.location = params.location
        if params.rotation is not None:
            empty.rotation_euler = tuple(math.radians(v) for v in params.rotation)
        if params.scale is not None:
            empty.scale = params.scale
        return Result(
            status="ok",
            asset_name=params.asset_name,
            asset_type="COLLECTION",
            link_mode="LINK",
            position=list(params.location) if params.location is not None else None,
            objects_affected=len(col.all_objects),
            message="Collection '{:s}' linked and instanced via '{:s}' at ({:.2f}, {:.2f}, {:.2f})".format(
                params.asset_name, empty.name,
                *(params.location if params.location is not None else (0.0, 0.0, 0.0))),
        )

    # Appended collection: link it into the scene and transform the copy.
    bpy.context.scene.collection.children.link(col)
    affected = _transform_collection(
        col,
        location=params.location,
        rotation=params.rotation,
        scale=params.scale,
    )
    where = " at ({:.2f}, {:.2f}, {:.2f})".format(*params.location) if params.location is not None else ""
    return Result(
        status="ok",
        asset_name=params.asset_name,
        asset_type="COLLECTION",
        link_mode="APPEND",
        position=list(params.location) if params.location is not None else None,
        objects_affected=affected,
        message="Collection '{:s}' appended to scene{:s} ({:d} objects)".format(
            params.asset_name, where, affected),
    )


def _all_objects(col) -> list[Any]:
    """Return all objects in *col*, including nested collections (recursive)."""
    import bpy  # pylint: disable=import-error

    objs = list(col.objects)
    for child in col.children:
        objs.extend(_all_objects(child))
    return objs


def _transform_collection(col, location, rotation, scale) -> int:
    """Apply rotation/scale around the collection centroid, then move it.

    Returns the number of objects transformed.
    """
    import math  # pylint: disable=import-error
    from mathutils import Euler, Matrix, Vector  # pylint: disable=import-error

    objs = _all_objects(col)
    if not objs:
        return 0
    if rotation is None and scale is None and location is None:
        return len(objs)

    # 1) Rotate/scale around the centroid (in place).
    if rotation is not None or scale is not None:
        centroid = _centroid(objs)
        rot = (
            Euler(tuple(math.radians(v) for v in rotation)).to_matrix().to_4x4()
            if rotation is not None else
            Matrix.Identity(4)
        )
        scl = (
            Matrix.Diagonal((scale[0], scale[1], scale[2], 1.0))
            if scale is not None else
            Matrix.Identity(4)
        )
        xform = (
            Matrix.Translation(centroid)
            @ rot
            @ scl
            @ Matrix.Translation(-centroid)
        )
        for obj in objs:
            obj.matrix_world = xform @ obj.matrix_world

    # 2) Move the (possibly new) centroid to the requested location.
    if location is not None:
        delta = Vector(location) - _centroid(objs)
        for obj in objs:
            obj.location = obj.location + delta

    return len(objs)


def _centroid(objs: list[Any]):
    """Average world location of *objs*."""
    from mathutils import Vector  # pylint: disable=import-error

    total = Vector((0.0, 0.0, 0.0))
    for obj in objs:
        total += obj.location
    return total / len(objs)