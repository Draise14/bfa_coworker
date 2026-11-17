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
    catalog_id: str | None = None
    nearest_catalogs: list[str] | None = None
    changed_area: dict | None = None


class Applied(NamedTuple):
    """What _apply_filters did: the library and catalog set, and why anything was not."""
    library: str | None = None
    catalog: str | None = None
    catalog_id: str | None = None
    nearest: list[str] | None = None
    notes: list[str] = []


def main(params: Params):
    """Switch to (or create) the Asset Browser editor.

    Reuses an existing Asset Browser area if one is open in any workspace.
    Otherwise, when *allow_edits* is True, the largest area of the current
    workspace (not the Coworker chat) is converted into an Asset Browser; no
    workspace is created, renamed or switched, and the result names the area
    that was changed so it can be set back.  The library is selected by name;
    a catalog by path (``Props/Chairs``, read from the library's
    ``blender_assets.cats.txt``) or by UUID.  Whatever could not be applied is
    reported, never dropped silently.
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
        # 'ASSETS' (+ subgroups like 'ASSETS_OBJECTS').
        return ui_type.startswith("ASSETS")

    def _result(workspace_name, ui_type, applied, created, message):
        notes = list(applied.notes)
        return Result(
            status="ok",
            workspace=workspace_name,
            area_ui_type=ui_type,
            library_set=applied.library,
            catalog_set=applied.catalog,
            created=created,
            message=" ".join([message] + notes),
            available_libraries=available_libraries,
            catalog_id=applied.catalog_id,
            nearest_catalogs=applied.nearest,
        )

    # 1) Reuse an existing Asset Browser area in any workspace.
    for workspace in bpy.data.workspaces:
        for screen in workspace.screens:
            for area in screen.areas:
                if area.type == "FILE_BROWSER" and _is_asset_ui(area.ui_type):
                    bpy.context.window.workspace = workspace
                    applied = _apply_filters(area.spaces.active, params)
                    return _result(
                        workspace.name, area.ui_type, applied, None,
                        "Reused existing Asset Browser in workspace '{:s}'.".format(workspace.name))

    if not params.allow_edits:
        return Result(
            status="error",
            message="No Asset Browser editor open and allow_edits is False",
            available_libraries=available_libraries,
        )

    # 2) Create one by converting an area of the current workspace. Nothing is
    # duplicated or switched: converting an area of a workspace Blender is still
    # switching to crashes it (access violation in ED_workspace_change).
    workspace = bpy.context.window.workspace
    area = _pick_area(bpy.context.screen.areas)
    if area is None:
        return Result(
            status="error",
            message="No area available to convert; nothing was changed",
            available_libraries=available_libraries,
        )
    before = {"type": area.type, "ui_type": area.ui_type, "width": area.width, "height": area.height}
    try:
        area.type = "FILE_BROWSER"
        area.ui_type = "ASSETS"
    except Exception as ex:  # pylint: disable=broad-except
        reason = "{:s}: {:s}".format(type(ex).__name__, str(ex))
        try:
            area.ui_type = before["ui_type"]
        except Exception as ex2:  # pylint: disable=broad-except
            reason += "; setting the area back to '{:s}' also failed ({:s})".format(
                before["ui_type"], str(ex2))
        return Result(
            status="error",
            message="Could not turn an area into an Asset Browser ({:s}); the area was set back to '{:s}'.".format(
                reason, before["ui_type"]),
            available_libraries=available_libraries,
        )

    changed = {"was": before["ui_type"], "workspace": workspace.name,
               "size": [before["width"], before["height"]]}
    message = (
        "Turned the largest area ({:d}x{:d} px, was '{:s}') of workspace '{:s}' into an Asset Browser; "
        "no workspace was created or renamed. To undo, set that area's editor type back to '{:s}'.".format(
            before["width"], before["height"], before["ui_type"], workspace.name, before["ui_type"]))
    ws_name = workspace.name

    if getattr(area.spaces.active, "params", None) is None and (params.library_name or params.catalog_path):
        # A new file browser gets its parameters when it is first drawn, so the library and
        # catalog are set from the tool's checker (the bridge answers once it returns), on a
        # later tick. The area is looked up again then: it may be gone by then.
        import time
        deadline = time.monotonic() + 3.0

        def check_is_ready() -> dict[str, object] | None:
            space = _find_browser_space()
            if space is None:
                return Result(
                    status="error", workspace=ws_name, created=True, changed_area=changed,
                    message=message + " The Asset Browser was closed before the library/catalog could be set.",
                    available_libraries=available_libraries,
                )._asdict()
            if getattr(space, "params", None) is None and time.monotonic() < deadline:
                return None
            return _result(ws_name, "ASSETS", _apply_filters(space, params), True, message
                           )._replace(changed_area=changed)._asdict()
        return check_is_ready

    return _result(ws_name, area.ui_type, _apply_filters(area.spaces.active, params), True, message
                   )._replace(changed_area=changed)


def _find_browser_space():
    """The space of an open Asset Browser area (any window), or None."""
    import bpy  # pylint: disable=import-error,no-name-in-module

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "FILE_BROWSER" and area.ui_type.startswith("ASSETS"):
                return area.spaces.active
    return None


def _pick_area(areas):
    """The largest area that is not the Coworker chat editor."""
    def _is_chat(area):
        space = getattr(area.spaces, "active", None)
        return str(getattr(space, "tree_type", "")).startswith("BFACW")

    candidates = [a for a in areas if not _is_chat(a)]
    return max(candidates, key=lambda a: a.width * a.height, default=None)


_CATS_FILE = "blender_assets.cats.txt"


def parse_catalogs(text: str) -> dict[str, str]:
    """``{catalog path: UUID}`` from the text of a ``blender_assets.cats.txt``.

    Lines are ``UUID:path/with/slashes:Simple Name``; ``#`` comments, the
    ``VERSION`` line and malformed lines are skipped.
    """
    found: dict[str, str] = {}
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
        found[path] = uuid
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


def _catalog_files(reference: str) -> list[str]:
    """Catalog definition files for the Asset Browser's library *reference*."""
    import bpy  # pylint: disable=import-error
    import os

    roots = []
    libs = bpy.context.preferences.filepaths.asset_libraries
    if reference == "LOCAL":
        if bpy.data.filepath:
            roots.append(os.path.dirname(bpy.data.filepath))
    elif reference == "ALL":
        if bpy.data.filepath:
            roots.append(os.path.dirname(bpy.data.filepath))
        roots.extend(str(lib.path) for lib in libs if lib.path)
    else:
        roots.extend(str(lib.path) for lib in libs if lib.name == reference and lib.path)
    return [os.path.join(bpy.path.abspath(root), _CATS_FILE) for root in roots]


def _apply_filters(space, params: Params) -> Applied:
    """Library + catalog selection on *space*; every miss is reported in ``notes``."""
    notes: list[str] = []
    lib_set = cat_set = cat_id = None
    nearest = None
    p = getattr(space, "params", None)
    if p is None:
        if params.library_name or params.catalog_path:
            notes.append("The browser has no parameters yet, so the library/catalog were not applied.")
        return Applied(notes=notes)

    if params.library_name:
        try:
            p.asset_library_reference = params.library_name
            lib_set = params.library_name
        except Exception as ex:  # pylint: disable=broad-except
            notes.append("Library '{:s}' could not be selected ({:s}: {:s}); the browser stays on '{:s}'.".format(
                params.library_name, type(ex).__name__, str(ex), str(p.asset_library_reference)))

    if params.catalog_path:
        wanted = params.catalog_path
        looks_uuid = len(wanted) == 36 and wanted.count("-") == 4
        if looks_uuid:
            try:
                p.catalog_id = wanted
                cat_set = cat_id = wanted
            except Exception as ex:  # pylint: disable=broad-except
                notes.append("Catalog id '{:s}' was not accepted ({:s}: {:s}).".format(
                    wanted, type(ex).__name__, str(ex)))
        else:
            import os

            reference = str(p.asset_library_reference)
            catalogs: dict[str, str] = {}
            for cats_path in _catalog_files(reference):
                if os.path.isfile(cats_path):
                    try:
                        with open(cats_path, "r", encoding="utf-8") as fh:
                            for path, uuid in parse_catalogs(fh.read()).items():
                                catalogs.setdefault(path, uuid)
                    except OSError as ex:
                        notes.append("Could not read '{:s}' ({:s}).".format(cats_path, str(ex)))
            uuid, nearest = match_catalog(catalogs, wanted)
            if uuid is None:
                where = "'{:s}'".format(reference)
                if not catalogs:
                    notes.append("No catalogs were found for {:s} (no {:s} to read); catalog '{:s}' was not set.".format(
                        where, _CATS_FILE, wanted))
                else:
                    notes.append("Catalog '{:s}' was not found in {:s}; nearest: {:s}.".format(
                        wanted, where, ", ".join(nearest) if nearest else "(none close)"))
            else:
                try:
                    p.catalog_id = uuid
                    cat_set, cat_id, nearest = wanted, uuid, None
                except Exception as ex:  # pylint: disable=broad-except
                    notes.append("Catalog '{:s}' ({:s}) was not accepted ({:s}: {:s}).".format(
                        wanted, uuid, type(ex).__name__, str(ex)))
    return Applied(lib_set, cat_set, cat_id, nearest, notes)
