# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Catalog-file reader shared by the asset tools (#602, #603).

This block is spliced into ``*_toolcode.py`` sources through the
``# @include_begin`` mechanism (like ``_asset_index_shared.py``), so the reader is defined in
the same namespace as each tool's ``main()``.  One reader, used by ``jump_to_asset_browser``
(catalog path -> UUID), ``list_asset_catalogs`` (the catalogs of a library) and the asset
tools that report an asset's catalog (UUID -> catalog path).

Where the catalogs of a library come from (the same places the Asset Browser reads):

- a library of the preferences: ``blender_assets.cats.txt`` in the library's folder;
- ``LOCAL`` (Current File): the file beside the saved .blend;
- ``ESSENTIALS``: the file in Bforartists / Blender's own ``datafiles/assets`` folder;
- ``ALL``: all of them.

The file is read with ``errors="replace"``: a stray byte cannot break a tool.
"""

import os

_CATS_FILE = "blender_assets.cats.txt"


def parse_catalog_entries(text: str) -> list[tuple[str, str]]:
    """Every ``(catalog path, UUID)`` of a ``blender_assets.cats.txt``, in file order.

    Lines are ``UUID:path/with/slashes:Simple Name``; ``#`` comments, the
    ``VERSION`` line and malformed lines are skipped.  Blender allows two catalogs
    with the same path, so a path can appear twice (with different UUIDs).
    """
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("VERSION"):
            continue
        parts = line.split(":", 2)
        if len(parts) < 2:
            continue
        uuid, path = parts[0].strip(), parts[1].strip().replace("\\", "/").strip("/")
        if len(uuid) != 36 or uuid.count("-") != 4 or not path:
            continue
        if (path, uuid) not in found:
            found.append((path, uuid))
    return found


def parse_catalogs(text: str) -> dict[str, str]:
    """``{catalog path: UUID}`` from the text of a catalog file; for a path named twice, the first UUID."""
    found: dict[str, str] = {}
    for path, uuid in parse_catalog_entries(text):
        found.setdefault(path, uuid)
    return found


def match_catalog(catalogs: dict[str, str], query: str) -> tuple[str | None, list[str]]:
    """``(UUID, [])`` for the catalog *query* names, else ``(None, nearest paths)``.

    Matching is exact, then case-insensitive; a unique last-segment match
    (``Chairs`` for ``Props/Chairs``) also counts.  Otherwise the nearest paths
    are returned (at most 5).
    """
    import difflib

    q = query.strip().replace("\\", "/").strip("/")
    if q in catalogs:
        return catalogs[q], []
    lower = {path.lower(): path for path in catalogs}
    if q.lower() in lower:
        return catalogs[lower[q.lower()]], []
    tails = [path for path in catalogs if path.lower().rsplit("/", 1)[-1] == q.lower()]
    if len(tails) == 1:
        return catalogs[tails[0]], []
    nearest = list(tails)
    for path in catalogs:
        if path not in nearest and q and q.lower() in path.lower():
            nearest.append(path)
    for low in difflib.get_close_matches(q.lower(), list(lower), n=5, cutoff=0.4):
        if lower[low] not in nearest:
            nearest.append(lower[low])
    return None, nearest[:5]


def _library_roots(reference: str) -> tuple[list[str], str]:
    """Folders that hold the catalog file(s) for the Asset Browser's library *reference*, and a reason
    when there are none ("" otherwise)."""
    import bpy  # pylint: disable=import-error
    import os

    libs = bpy.context.preferences.filepaths.asset_libraries
    roots: list[str] = []

    def _local():
        if bpy.data.filepath:
            roots.extend(_local_catalog_dirs(os.path.dirname(bpy.path.abspath(bpy.data.filepath))))

    def _essentials():
        try:
            path = bpy.utils.system_resource("DATAFILES", path="assets")
        except Exception:  # pylint: disable=broad-except
            path = ""
        if path and os.path.isdir(path):
            roots.append(path)

    if reference == "LOCAL":
        if not bpy.data.filepath:
            return [], "the current file is not saved, so it has no catalog file"
        _local()
    elif reference == "ALL":
        _local()
        roots.extend(bpy.path.abspath(str(lib.path)) for lib in libs if lib.path)
        _essentials()
    elif reference == "ESSENTIALS":
        _essentials()
        if not roots:
            return [], "the built-in Essentials library was not found"
    else:
        roots.extend(bpy.path.abspath(str(lib.path)) for lib in libs if lib.name == reference and lib.path)
        if not roots:
            return [], "'{:s}' is not a known library".format(reference)
    return roots, ""


def _local_catalog_dirs(blend_dir: str) -> list[str]:
    """Folders whose catalog file Blender uses for a saved file in *blend_dir* ("Current File")."""
    return [blend_dir]


def _catalog_entries_for(reference: str, cache: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """``([(path, UUID), ...] in file order, notes)`` for *reference*, read once into *cache*. Never raises."""
    key = ("entries", reference)
    if key in cache:
        return cache[key]
    import os

    entries: list[tuple[str, str]] = []
    notes: list[str] = []
    try:
        roots, reason = _library_roots(reference)
    except Exception as ex:  # pylint: disable=broad-except
        roots, reason = [], "{:s}: {:s}".format(type(ex).__name__, str(ex))
    if reason:
        notes.append("No catalogs for '{:s}': {:s}.".format(reference, reason))
    for root in roots:
        cats_path = os.path.join(root, _CATS_FILE)
        if not os.path.isfile(cats_path):
            continue
        try:
            with open(cats_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except (OSError, ValueError) as ex:
            notes.append("Could not read '{:s}' ({:s}).".format(cats_path, str(ex)))
            continue
        for pair in parse_catalog_entries(text):
            if pair not in entries:
                entries.append(pair)
    cache[key] = (entries, notes)
    return cache[key]


def _catalogs_for(reference: str, cache: dict) -> tuple[dict[str, str], list[str]]:
    """``({path: UUID}, notes)`` for *reference* (a path named twice gives its first UUID), read once into *cache*."""
    if reference in cache:
        return cache[reference]
    entries, notes = _catalog_entries_for(reference, cache)
    catalogs: dict[str, str] = {}
    for path, uuid in entries:
        catalogs.setdefault(path, uuid)
    cache[reference] = (catalogs, notes)
    return cache[reference]


_NULL_UUID = "00000000-0000-0000-0000-000000000000"


def catalog_uuid_or_empty(value) -> str:
    """The UUID text *value*, or "" for no catalog (empty, or the null UUID Blender stores then)."""
    text = str(value or "").strip()
    if not text or text.lower() == _NULL_UUID:
        return ""
    return text


def catalog_paths_by_uuid(catalogs) -> dict[str, str]:
    """``{lower-case UUID: catalog path}``: every UUID resolves, also when two catalogs share a path.

    *catalogs* is a list of ``(path, UUID)`` pairs (``_catalog_entries_for``) or a ``{path: UUID}`` map."""
    pairs = catalogs.items() if isinstance(catalogs, dict) else catalogs
    return {uuid.lower(): path for path, uuid in pairs}


def describe_catalog(catalog_id, paths_by_uuid: dict[str, str]) -> dict[str, str]:
    """``{"catalog": path, "catalog_id": UUID}`` for an asset's catalog UUID.

    No catalog (empty or the null UUID) gives empty strings, never the null UUID. A UUID the
    catalog file does not name keeps its UUID with an empty path."""
    uuid = catalog_uuid_or_empty(catalog_id)
    return {"catalog": paths_by_uuid.get(uuid.lower(), "") if uuid else "", "catalog_id": uuid}
