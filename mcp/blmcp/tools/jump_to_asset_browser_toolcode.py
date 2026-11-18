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

import re
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


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# The editors with a right sidebar, which can show Coworker's compact chat (ui_chat.SIDEBAR_SPACES).
_SIDEBAR_TYPES = frozenset((
    "VIEW_3D", "IMAGE_EDITOR", "NODE_EDITOR", "SEQUENCE_EDITOR", "CLIP_EDITOR",
    "DOPESHEET_EDITOR", "GRAPH_EDITOR", "NLA_EDITOR", "TEXT_EDITOR", "SPREADSHEET",
))
# Coworker's own always-running input operator (ui_gpu/input_capture.py): not a reason to wait.
_OWN_MODAL_PREFIXES = ("BFACW_OT_gpu_input", "bfacw.gpu_input")
_NO_FREE_AREA = (
    "No free editor to turn into an Asset Browser: open one, or split an area. Nothing was changed.")


def is_uuid(text: str) -> bool:
    """True for a real UUID (8-4-4-4-12 hexadecimal), not just any 36-character text."""
    return bool(_UUID_RE.match(text))


def main(params: Params):
    """Switch to (or create) the Asset Browser editor.

    Reuses an existing Asset Browser area if one is open in any workspace.
    Otherwise, when *allow_edits* is True, the largest free area of the current
    workspace (not the Coworker chat, nor an editor whose sidebar is open: the
    compact chat may sit there) is converted into an Asset Browser; no
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
    busy = _busy_reason(bpy)
    if busy:
        return Result(
            status="error",
            message="Not now: {:s}. Nothing was changed; try again when it is finished.".format(busy),
            available_libraries=available_libraries,
        )
    window = _main_window(bpy)
    if window is None:
        return Result(
            status="error", message="No window with a normal screen. Nothing was changed.",
            available_libraries=available_libraries)
    workspace = window.workspace
    area = _pick_area(window.screen.areas)
    if area is None:
        return Result(status="error", message=_NO_FREE_AREA, available_libraries=available_libraries)

    # The catalog file is read before any area is touched, so a bad file cannot leave a converted area.
    cache: dict = {}
    if params.catalog_path and not is_uuid(params.catalog_path):
        _catalogs_for(params.library_name or "LOCAL", cache)

    before = {"type": area.type, "ui_type": area.ui_type, "width": area.width, "height": area.height}
    try:
        area.type = "FILE_BROWSER"
        area.ui_type = "ASSETS"
    except Exception as ex:  # pylint: disable=broad-except
        reason = "{:s}: {:s}".format(type(ex).__name__, str(ex))
        try:
            area.ui_type = before["ui_type"]
            restored = "the area was set back to '{:s}'".format(before["ui_type"])
        except Exception as ex2:  # pylint: disable=broad-except
            restored = "setting the area back to '{:s}' also failed ({:s}); set its editor type by hand".format(
                before["ui_type"], str(ex2))
        return Result(
            status="error",
            message="Could not turn an area into an Asset Browser ({:s}); {:s}.".format(reason, restored),
            available_libraries=available_libraries,
        )

    changed = {"was": before["ui_type"], "workspace": workspace.name,
               "size": [before["width"], before["height"]]}
    message = (
        "Turned the largest free area ({:d}x{:d} px, was '{:s}') of workspace '{:s}' into an Asset Browser; "
        "no workspace was created or renamed. To undo, set that area's editor type back to '{:s}'.".format(
            before["width"], before["height"], before["ui_type"], workspace.name, before["ui_type"]))
    ws_name = workspace.name

    if getattr(area.spaces.active, "params", None) is None and (params.library_name or params.catalog_path):
        # A new file browser gets its parameters when it is first drawn, so the library and
        # catalog are set from the tool's checker (the bridge answers once it returns), on a
        # later tick. The area is looked up again then: it may be gone, or the window may show
        # another workspace or another file by then.
        import time
        deadline = time.monotonic() + 3.0
        filepath0 = bpy.data.filepath

        def check_is_ready() -> dict[str, object] | None:
            if bpy.data.filepath != filepath0:
                return Result(
                    status="error", workspace=ws_name, created=True,
                    message="A different file was loaded before the Asset Browser's library/catalog could be "
                            "set; nothing was applied to the new file.",
                    available_libraries=available_libraries,
                )._asdict()
            space = _find_browser_space(ws_name)
            if space is None:
                return Result(
                    status="error", workspace=ws_name, created=True, changed_area=changed,
                    message=message + " The Asset Browser was closed before the library/catalog could be set.",
                    available_libraries=available_libraries,
                )._asdict()
            if getattr(space, "params", None) is None and time.monotonic() < deadline:
                return None
            applied = _apply_filters(space, params, cache)
            note = ""
            shown = _main_window(bpy)
            if shown is not None and shown.workspace.name != ws_name:
                note = " The browser was made in workspace '{:s}'; the window now shows '{:s}'.".format(
                    ws_name, shown.workspace.name)
            return _result(ws_name, "ASSETS", applied, True, message + note
                           )._replace(changed_area=changed)._asdict()
        return check_is_ready

    return _result(ws_name, area.ui_type, _apply_filters(area.spaces.active, params, cache), True, message
                   )._replace(changed_area=changed)


def _busy_reason(bpy) -> str:
    """Why the editors must not be changed now ("" when they may be): a modal operator of the user's
    is running, or a render or bake job is."""
    try:
        for window in bpy.context.window_manager.windows:
            for op in window.modal_operators:
                if not str(op.bl_idname).startswith(_OWN_MODAL_PREFIXES):
                    return "another operation ('{:s}') is running".format(str(op.bl_idname))
    except (AttributeError, ReferenceError):
        pass
    is_job_running = getattr(bpy.app, "is_job_running", None)
    if is_job_running is not None:
        for job, what in (("RENDER", "a render"), ("OBJECT_BAKE", "a bake"), ("COMPOSITE", "the compositor")):
            try:
                if is_job_running(job):
                    return "{:s} is running".format(what)
            except (TypeError, ValueError):
                continue
    return ""


def _main_window(bpy):
    """The current window, or the first one with a normal screen (not a maximized area's temporary one)."""
    window = bpy.context.window
    if window is not None and not getattr(window.screen, "is_temporary", False):
        return window
    try:
        for other in bpy.context.window_manager.windows:
            if not getattr(other.screen, "is_temporary", False):
                return other
    except AttributeError:
        pass
    return None


def _find_browser_space(workspace_name: str):
    """The space of an open Asset Browser area, preferring workspace *workspace_name*, or None."""
    import bpy  # pylint: disable=import-error,no-name-in-module

    workspaces = sorted(bpy.data.workspaces, key=lambda w: w.name != workspace_name)
    for workspace in workspaces:
        for screen in workspace.screens:
            for area in screen.areas:
                if area.type == "FILE_BROWSER" and area.ui_type.startswith("ASSETS"):
                    return area.spaces.active
    return None


def _pick_area(areas):
    """The largest area that is free: not the Coworker chat editor and not an editor whose sidebar is
    open (the compact chat sits in the sidebar of the editors in ``_SIDEBAR_TYPES``)."""
    def _taken(area):
        space = getattr(area.spaces, "active", None)
        if str(getattr(space, "tree_type", "")).startswith("BFACW"):
            return True
        return area.type in _SIDEBAR_TYPES and bool(getattr(space, "show_region_ui", False))

    candidates = [a for a in areas if not _taken(a)]
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


def _catalogs_for(reference: str, cache: dict) -> tuple[dict[str, str], list[str]]:
    """``({path: UUID}, notes)`` for *reference*, read once into *cache*. Never raises."""
    if reference in cache:
        return cache[reference]
    import os

    catalogs: dict[str, str] = {}
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
        for path, uuid in parse_catalogs(text).items():
            catalogs.setdefault(path, uuid)
    cache[reference] = (catalogs, notes)
    return cache[reference]


def _apply_filters(space, params: Params, cache: dict | None = None) -> Applied:
    """Library + catalog selection on *space*; every miss is reported in ``notes``.

    *cache* holds catalog files already read (``_catalogs_for``)."""
    notes: list[str] = []
    cache = {} if cache is None else cache
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
        uuid = None
        if is_uuid(wanted):
            uuid = wanted
        else:
            reference = str(p.asset_library_reference)
            catalogs, read_notes = _catalogs_for(reference, cache)
            notes.extend(read_notes)
            uuid, nearest = match_catalog(catalogs, wanted)
            if uuid is None:
                if not catalogs:
                    notes.append("No catalogs were found for '{:s}' (no {:s} to read); catalog '{:s}' was not set.".format(
                        reference, _CATS_FILE, wanted))
                else:
                    notes.append("Catalog '{:s}' was not found in '{:s}'; nearest: {:s}.".format(
                        wanted, reference, ", ".join(nearest) if nearest else "(none close)"))
        if uuid is not None:
            try:
                p.catalog_id = uuid
                took = str(p.catalog_id).lower() == uuid.lower()
            except Exception as ex:  # pylint: disable=broad-except
                took = False
                notes.append("Catalog '{:s}' ({:s}) was not accepted ({:s}: {:s}).".format(
                    wanted, uuid, type(ex).__name__, str(ex)))
            else:
                if not took:
                    notes.append("Catalog '{:s}' ({:s}) was set but the browser did not keep it.".format(wanted, uuid))
            if took:
                cat_set, cat_id, nearest = wanted, uuid, None
    return Applied(lib_set, cat_set, cat_id, nearest, notes)
