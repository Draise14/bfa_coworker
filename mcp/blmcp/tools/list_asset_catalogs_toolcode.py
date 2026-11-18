# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for listing the catalogs of asset libraries (read from their catalog files, #602).
"""

__all__ = (
    "Result",
    "Params",
    "main",
)

from typing import Any, NamedTuple

# @include_begin: _asset_index_shared.py
# @include_end
# @include_begin: _asset_catalogs_shared.py
# @include_end

_ASSET_TYPES = ("MATERIAL", "NODETREE", "OBJECT", "WORLD", "ACTION", "COLLECTION")
_SAMPLE_NAMES = 10


class Result(NamedTuple):
    status: str
    library_name: str
    catalogs: list[dict[str, Any]]
    asset_counts: dict[str, int]
    # One entry per library looked at: its catalog file, how many catalogs and uncatalogued assets.
    libraries: list[dict[str, Any]] = []
    message: str = ""


class Params(NamedTuple):
    library_name: str = ""


def main(params: Params) -> Result:
    """List the catalogs of asset libraries, read from each library's ``blender_assets.cats.txt``.

    The catalog paths and UUIDs are the ones ``jump_to_asset_browser`` takes. The asset counts and up to
    10 sample names per catalog come from the library's metadata index, when it is ready (the first call
    starts building it). Folders on disk are not catalogs: a library without a catalog file answers with
    an empty list and says so.
    """
    import bpy  # pylint: disable=import-error
    import os

    library_name = params.library_name
    total_counts = {k: 0 for k in _ASSET_TYPES}
    all_catalogs: list[dict[str, Any]] = []
    libraries: list[dict[str, Any]] = []
    messages: list[str] = []
    shown = library_name or "(all)"

    try:
        # (reference the Asset Browser uses, library folder or "").
        targets: list[tuple[str, str]] = []
        if library_name in ("LOCAL", "ESSENTIALS"):
            targets.append((library_name, ""))
        else:
            for lib in bpy.context.preferences.filepaths.asset_libraries:
                if library_name and lib.name != library_name:
                    continue
                targets.append((lib.name, bpy.path.abspath(str(lib.path)) if lib.path else ""))
        if not targets:
            if library_name:
                return Result(status="library not found", library_name=shown, catalogs=[],
                              asset_counts=total_counts,
                              message="No asset library named '{:s}'. Use get_asset_libraries to see the names "
                                      "(or LOCAL for the current file, ESSENTIALS for the built-in one).".format(library_name))
            return Result(status="no catalogs found", library_name=shown, catalogs=[], asset_counts=total_counts,
                          message="No asset libraries are configured.")

        cache: dict = {}
        for reference, lib_path in targets:
            catalogs, notes = _catalogs_for(reference, cache)
            messages.extend(notes)
            index = None
            if lib_path and os.path.isdir(lib_path):
                index = _blmcp_index_ensure(lib_path, bpy)
            per_catalog: dict[str, dict[str, Any]] = {}
            uncatalogued = 0
            if isinstance(index, dict) and isinstance(index.get("assets"), dict):
                by_uuid = catalog_paths_by_uuid(catalogs)
                for name_i, entry in index["assets"].items():
                    if not isinstance(entry, dict):
                        continue
                    kind = str(entry.get("type", ""))
                    if kind in total_counts:
                        total_counts[kind] += 1
                    uuid = catalog_uuid_or_empty(entry.get("catalog_id", ""))
                    if not uuid or uuid.lower() not in by_uuid:
                        uncatalogued += 1
                        continue
                    slot = per_catalog.setdefault(by_uuid[uuid.lower()], {"counts": {}, "names": []})
                    slot["counts"][kind] = slot["counts"].get(kind, 0) + 1
                    if len(slot["names"]) < _SAMPLE_NAMES:
                        slot["names"].append({"name": str(name_i), "type": kind})
            index_ready = isinstance(index, dict)
            for path, uuid in sorted(catalogs.items()):
                item: dict[str, Any] = {"path": path, "uuid": uuid, "library": reference}
                if index_ready:
                    slot = per_catalog.get(path, {"counts": {}, "names": []})
                    item["assets"] = sum(slot["counts"].values())
                    item["counts"] = slot["counts"]
                    item["asset_names"] = slot["names"]
                all_catalogs.append(item)
            libraries.append({
                "library": reference,
                "catalogs": len(catalogs),
                "assets_without_catalog": uncatalogued if index_ready else None,
            })
            if not catalogs and not notes:
                messages.append(
                    "Library '{:s}' has no catalogs: no {:s} that names any. Its assets are not filed in "
                    "catalogs (the folders on disk are not catalogs).".format(reference, _CATS_FILE))
            if lib_path and not index_ready:
                messages.append(
                    "Asset counts for '{:s}' are not ready: its metadata index is being built; ask again in a "
                    "minute.".format(reference))

        return Result(
            status="ok", library_name=shown, catalogs=all_catalogs, asset_counts=total_counts,
            libraries=libraries, message=" ".join(messages))

    except Exception as ex:
        return Result(
            status="error: {:s}".format(str(ex)), library_name=shown, catalogs=[],
            asset_counts=total_counts, libraries=libraries, message=" ".join(messages))
