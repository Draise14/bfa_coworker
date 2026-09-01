# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Shared asset-library metadata index (Tier 3d Phase C).

This block is spliced into ``*_toolcode.py`` sources through the
``# @include_begin`` mechanism, so the helpers are defined in the same
namespace as each tool's ``main()`` (everything is ``_blmcp_``-prefixed
and dependency-free apart from the standard library).

What this buys:

- ``get_asset_tags`` and ``search_assets`` answer from an on-disk index
  instead of appending datablocks into the live session (no junk in
  ``bpy.data``, no undo steps, no append renames).
- The index captures the full Asset Details region metadata — tags,
  description, author, copyright, license, catalog, color tag and
  ``preferred_import_method`` (``APPEND`` / ``LINK`` / ``PACK``) plus
  per-type facts (node counts, socket interface for node groups) — so
  ``load_asset_in_context`` can honor an asset's self-declared import
  method *before* loading anything.
- The index lives in the addon's user cache, never inside the library
  folders (libraries are often read-only network shares).
- A stale or missing index is rebuilt by a disposable headless
  subprocess (``--background --factory-startup``) keyed off per-file
  mtime+size fingerprints. In this live session nothing is modified.

Cache layout::

    <cache>/bfa_coworker/asset_index/<sha1(library-path)>.json
    <cache>/bfa_coworker/asset_index/<sha1(library-path)>.building

``BFACW_ASSET_INDEX_DIR`` overrides the cache location (used by tests).
"""

import hashlib
import json
import os
import subprocess
import sys
import time

# Keep this in sync with the schema written by the embedded indexer script.
_BLMCP_INDEX_SCHEMA = 1


# ---------------------------------------------------------------------------
# Indexer script (runs in a throwaway headless Bforartists instance).

_BLMCP_INDEXER_SCRIPT = r"""
# Headless asset-library metadata indexer (Tier 3d Phase C).
# usage: bforartists --background --factory-startup --python <this> -- <lib_dir> <out_json>
# Writes one JSON document per library: {schema, library_path, built_at,
# files: {relpath: {mtime_ns, size}}, assets: {name: entry}}.
import bpy  # pylint: disable=import-error
import json
import os
import sys
import time


def _asset_data(datablock):
    return getattr(datablock, "asset_data", None) or None


def _str_attr(asset_data, attr):
    try:
        value = getattr(asset_data, attr)
    except Exception:
        return ""
    if value is None:
        return ""
    return str(value)


def _catalog_name(asset_data):
    # AssetMetaData has catalog_id; catalog_simple_name appeared in 4.x.
    simple = _str_attr(asset_data, "catalog_simple_name")
    if simple:
        return simple
    catalog_id = _str_attr(asset_data, "catalog_id")
    if catalog_id:
        # Last path component, e.g. "tree/foliage/My Tree" -> "My Tree".
        return catalog_id.rsplit("/", 1)[-1]
    return ""


def _preferred_method(asset_data):
    try:
        method = getattr(asset_data, "preferred_import_method", "")
    except Exception:
        return "", False
    if not method:
        return "", False
    return str(method), bool(getattr(asset_data, "use_preferred_import_method", False))


def _tags(asset_data):
    tags = []
    try:
        for tag in asset_data.tags:
            tags.append(str(tag.name))
    except Exception:
        pass
    return tags


def _node_interface(ng):
    inputs = []
    outputs = []
    interface = getattr(ng, "interface", None)
    if interface is not None and hasattr(interface, "items_tree"):
        for item in interface.items_tree:
            direction = getattr(item, "in_out", "")
            entry = {"name": str(item.name)}
            try:
                entry["socket_type"] = str(item.socket_type)
            except Exception:
                entry["socket_type"] = ""
            if direction == "INPUT":
                inputs.append(entry)
            elif direction == "OUTPUT":
                outputs.append(entry)
    return {"inputs": inputs, "outputs": outputs}


def _entry(datablock, asset_type, blend_rel):
    asset_data = _asset_data(datablock)
    entry = {
        "name": datablock.name,
        "type": asset_type,
        "file": blend_rel,
        "tags": _tags(asset_data) if asset_data else [],
        "description": _str_attr(datablock, "description") or "",
        "author": _str_attr(asset_data, "author") if asset_data else "",
        "copyright": _str_attr(asset_data, "copyright") if asset_data else "",
        "license": _str_attr(asset_data, "license") if asset_data else "",
        "catalog": _catalog_name(asset_data) if asset_data else "",
        "color_tag": _str_attr(datablock, "color_tag") or "NONE",
    }
    preferred, use_preferred = _preferred_method(asset_data) if asset_data else ("", False)
    entry["preferred_import_method"] = preferred
    entry["use_preferred_import_method"] = use_preferred
    if asset_type == "NODETREE":
        entry["editor_type"] = str(getattr(datablock, "type", ""))
        entry["interface"] = _node_interface(datablock)
        entry["node_count"] = len(datablock.nodes)
        entry["input_count"] = len(entry["interface"]["inputs"])
        entry["output_count"] = len(entry["interface"]["outputs"])
    elif asset_type == "MATERIAL":
        entry["use_nodes"] = bool(getattr(datablock, "use_nodes", False))
        try:
            entry["blend_method"] = str(getattr(datablock, "blend_method", ""))
        except Exception:
            entry["blend_method"] = ""
    elif asset_type == "OBJECT":
        data = getattr(datablock, "data", None)
        entry["object_type"] = str(getattr(datablock, "type", ""))
        try:
            entry["vertex_count"] = len(data.vertices) if data and hasattr(data, "vertices") else 0
        except Exception:
            entry["vertex_count"] = 0
    elif asset_type == "COLLECTION":
        entry["object_count"] = len(getattr(datablock, "objects", ()))
        entry["child_collection_count"] = len(getattr(datablock, "children", ()))
        entry["objects"] = [str(o.name) for o in list(getattr(datablock, "objects", ()))[:10]]
    elif asset_type == "WORLD":
        entry["use_nodes"] = bool(getattr(datablock, "use_nodes", False))
        entry["node_count"] = len(getattr(getattr(datablock, "node_tree", None), "nodes", ()))
    elif asset_type == "ACTION":
        try:
            entry["frame_range"] = [frame for frame in getattr(datablock, "frame_range", (0.0, 0.0))]
        except Exception:
            entry["frame_range"] = [0.0, 0.0]
        entry["fcurves_count"] = len(getattr(datablock, "fcurves", ()))
    return entry


def _load_and_collect(blend_path, base_dir):
    x = bpy.data.libraries.load(blend_path)
    with x as (data_from, _data_to):
        collections = {
            "MATERIAL": list(data_from.materials),
            "NODETREE": list(data_from.node_groups),
            "OBJECT": list(data_from.objects),
            "COLLECTION": list(data_from.collections),
            "WORLD": list(data_from.worlds),
            "ACTION": list(data_from.actions),
        }
    blend_rel = os.path.relpath(blend_path, base_dir).replace("\\", "/")
    entries = {}
    for asset_type, names in collections.items():
        for index, name_i in enumerate(names):
            try:
                with bpy.data.libraries.load(blend_path) as (data_from_src, data_to):
                    setattr(data_to, _COLLECTION_ATTR[asset_type], [name_i])
                    loaded = getattr(data_to, _COLLECTION_ATTR[asset_type])[0]
                if loaded is None:
                    continue
                entry = _entry(loaded, asset_type, blend_rel)
                # The appended name may be renamed (.001) on collisions inside
                # this throwaway instance; report the library name (name_i).
                entry["name"] = name_i
                entries[name_i] = entry
                # Drop the appended datablock so memory stays flat.
                coll = getattr(bpy.data, _COLLECTION_ATTR[asset_type])
                if loaded in coll:
                    try:
                        coll.remove(loaded)
                    except Exception:
                        pass
            except Exception:
                continue
    return entries


_COLLECTION_ATTR = {
    "MATERIAL": "materials",
    "NODETREE": "node_groups",
    "OBJECT": "objects",
    "COLLECTION": "collections",
    "WORLD": "worlds",
    "ACTION": "actions",
}


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) != 2:
        sys.stderr.write("usage: ... -- <lib_dir> <out_json>\n")
        sys.exit(2)
    lib_dir, out_path = args
    index = {
        "schema": 1,
        "library_path": os.path.normpath(lib_dir),
        "built_at": time.time(),
        "files": {},
        "assets": {},
    }
    for root, _dirs, files in os.walk(lib_dir):
        for fname in files:
            if not fname.endswith(".blend"):
                continue
            blend_path = os.path.join(root, fname)
            try:
                stat_i = os.stat(blend_path)
            except OSError:
                continue
            blend_rel = os.path.relpath(blend_path, lib_dir).replace("\\", "/")
            index["files"][blend_rel] = {
                "mtime_ns": stat_i.st_mtime_ns,
                "size": stat_i.st_size,
            }
            try:
                index["assets"].update(_load_and_collect(blend_path, lib_dir))
            except Exception:
                continue
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, out_path)
    sys.stderr.write("indexed {:d} assets in {:s}\n".format(
        len(index["assets"]), lib_dir))


main()
"""


# ---------------------------------------------------------------------------
# Cache path resolution (named functions, not stdlib constants, so the
# spliced code never references a `bpy` module before it is imported).

def _blmcp_index_dir(bpy) -> str:
    """Return the index cache directory (creating it if possible)."""
    override = os.environ.get("BFACW_ASSET_INDEX_DIR", "")
    if override:
        try:
            os.makedirs(override, exist_ok=True)
        except OSError:
            pass
        return override
    base = None
    for candidate in (
        lambda: getattr(getattr(bpy, "utils", None), "cache_path", lambda user=False: "")(user=True),
        lambda: getattr(getattr(bpy, "utils", None), "temp_path", lambda: "")(),
        lambda: getattr(getattr(bpy, "utils", None), "user_resource", lambda name: "")(  # noqa: E731
            "DATAFILES"),
    ):
        try:
            value = candidate()
        except Exception:
            value = ""
        if value:
            base = value
            break
    if not base:
        # Last resort: next to the chat-history folder the addon already uses.
        import os as _os
        base = _os.path.join(
            _os.path.expanduser("~"), ".bfa_coworker", "cache")
    cache_dir = os.path.join(base, "bfa_coworker", "asset_index")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        pass
    return cache_dir


def _blmcp_index_path(lib_path: str, bpy) -> str:
    digest = hashlib.sha1(os.path.normpath(lib_path).encode("utf-8")).hexdigest()
    return os.path.join(_blmcp_index_dir(bpy), digest + ".json")


def _blmcp_marker_path(lib_path: str, bpy) -> str:
    digest = hashlib.sha1(os.path.normpath(lib_path).encode("utf-8")).hexdigest()
    return os.path.join(_blmcp_index_dir(bpy), digest + ".building")


# ---------------------------------------------------------------------------
# Read + freshness.

def _blmcp_index_fresh(index: dict, lib_path: str) -> bool:
    """Return True when every fingerprinted file is unchanged on disk."""
    if not isinstance(index, dict):
        return False
    files = index.get("files")
    if not isinstance(files, dict):
        return False
    for blend_rel, fingerprint in files.items():
        blend_path = os.path.join(lib_path, blend_rel.replace("/", os.sep))
        try:
            stat_i = os.stat(blend_path)
        except OSError:
            return False
        if stat_i.st_mtime_ns != fingerprint.get("mtime_ns") or stat_i.st_size != fingerprint.get("size"):
            return False
    return True


def _blmcp_index_read(lib_path: str, bpy) -> dict | None:
    """Return the cached index when present and fresh, else None."""
    index_path = _blmcp_index_path(lib_path, bpy)
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            index = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(index, dict) or index.get("schema") != _BLMCP_INDEX_SCHEMA:
        return None
    if not _blmcp_index_fresh(index, lib_path):
        return None
    return index


def _blmcp_index_lookup(
    lib_path: str,
    asset_name: str,
    bpy,
    asset_type: str = "",
) -> dict | None:
    """Return the index entry for *asset_name* (optionally filtered by type)."""
    index = _blmcp_index_read(lib_path, bpy)
    if index is None:
        return None
    assets = index.get("assets")
    if not isinstance(assets, dict):
        return None
    entry = assets.get(asset_name)
    if entry is None or not isinstance(entry, dict):
        # Case-insensitive fallback for renamed-on-disk spellings.
        lower = asset_name.lower()
        for name_i, candidate in assets.items():
            if str(name_i).lower() == lower:
                entry = candidate
                break
    if entry is None:
        return None
    if asset_type and entry.get("type") != asset_type:
        return None
    return entry


# ---------------------------------------------------------------------------
# Background build.

def _blmcp_trigger_index_build(lib_path: str, bpy) -> tuple[bool, str]:
    """Start a disposable headless indexer for *lib_path*.

    Returns ``(started, message)``. A marker file with a short TTL
    prevents redundant concurrent builds. Never raises.
    """
    marker_path = _blmcp_marker_path(lib_path, bpy)
    try:
        if os.path.exists(marker_path):
            age = time.time() - os.path.getmtime(marker_path)
            if age < 60.0:
                return False, "index build already in progress"
    except OSError:
        pass

    binary = getattr(getattr(bpy, "app", None), "binary_path", "")
    if not binary or not os.path.exists(binary):
        return False, "no Bforartists binary available for indexing"

    index_path = _blmcp_index_path(lib_path, bpy)
    script = _BLMCP_INDEXER_SCRIPT
    script_path = index_path + ".py"

    try:
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(script)
        with open(marker_path, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [
                binary,
                "--background",
                "--factory-startup",
                "--python", script_path,
                "--",
                os.path.normpath(lib_path),
                index_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=(sys.platform != "win32"),
        )
        return True, "index build started in background"
    except OSError:
        try:
            os.remove(marker_path)
        except OSError:
            pass
        return False, "could not start index build"


def _blmcp_index_ensure(lib_path: str, bpy) -> dict | None:
    """Return a fresh index, triggering a background build when unavailable."""
    index = _blmcp_index_read(lib_path, bpy)
    if index is not None:
        return index
    _blmcp_trigger_index_build(lib_path, bpy)
    return None