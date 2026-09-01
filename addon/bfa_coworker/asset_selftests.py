# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Deterministic in-session asset-tool self-tests (Tier 3d Phase B).

Runs the *same* ``*_toolcode.py`` modules the MCP layer executes against a
throwaway fixture library, entirely in-session — no MCP server, no LLM, no
agent. Each step reports PASS/FAIL and a wall-clock timing so the
diagnostics UI can show a one-click health check of the asset tools.

Steps that genuinely need a live editor/UI (opening the Asset Browser,
visually verifying a load, renders) are listed as *manual* steps with
instructions: the test harness cannot automate them headless.

Run from the addon's diagnostics panel (Preferences → Advanced →
Diagnostics → Asset Tool Self-Tests), or programmatically::

    from bfa_coworker import asset_selftests
    asset_selftests.run_auto_suite()
    asset_selftests.get_results()   # list of step dicts

The fixture builder writes into a temp directory and registers a temp
asset library named ``BFACW_Selftest``; ``teardown`` unloads everything
and removes the library registration.
"""

import os
import sys
import tempfile
import time
import types

# Modules that hold the fixture datablocks — matching the reset purger in
# the integration harness.
_PURGE_ATTRS = (
    "objects", "node_groups", "materials", "meshes", "lattices",
    "curves", "armatures", "cameras", "lights", "metaballs", "actions",
    "worlds", "images",
)

# Names the fixture + steps are allowed to create.  The diagnostics suite
# runs in the user's LIVE session, so cleanup must never touch datablocks
# outside this set (unlike the headless integration harness which resets
# the whole scene).
_FIXTURE_PREFIXES = (
    "Crate_Cube", "Brick_Mat", "Tree_Foliage", "MatTarget", "WireHolder",
    "BFACW_WireTree",
)


# ---------------------------------------------------------------------------
# Toolcode loading (same include expansion as blmcp.tools_helpers).

_INCLUDE_BEGIN_PREFIX = "# @include_begin: "
_INCLUDE_END = "# @include_end"


def _toolcode_dirs():
    """Possible roots containing the ``*_toolcode.py`` files.

    The built addon vendors blmcp under ``vendor/blmcp``; a source checkout
    keeps it at ``mcp/blmcp`` alongside the repo.
    """
    addon_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join(addon_dir, "vendor", "blmcp", "tools"),
        os.path.join(addon_dir, "..", "..", "mcp", "blmcp", "tools"),
    )
    return [os.path.normpath(p) for p in candidates]


def _find_toolcode_dir():
    for dir_path in _toolcode_dirs():
        probe = os.path.join(dir_path, "get_asset_tags_toolcode.py")
        if os.path.isfile(probe):
            return dir_path
    return None


def _expand_includes(toolcode_path, source):
    """Splice ``# @include_begin`` blocks, mirroring ``tools_helpers``."""
    toolcode_dir = os.path.dirname(toolcode_path)
    lines = source.splitlines(True)
    result = []
    skip = False
    for line in lines:
        if line.startswith(_INCLUDE_BEGIN_PREFIX):
            include_name = line[len(_INCLUDE_BEGIN_PREFIX):].rstrip()
            include_path = os.path.join(toolcode_dir, include_name)
            with open(include_path, "r", encoding="utf-8") as fh:
                result.append(fh.read())
            if result[-1] and not result[-1].endswith("\n"):
                result.append("\n")
            skip = True
        elif skip:
            if line.startswith(_INCLUDE_END):
                skip = False
        else:
            result.append(line)
    return "".join(result)


def _load_toolcode(name):
    """Load a toolcode module in-session (exec'd once per call)."""
    toolcode_dir = _find_toolcode_dir()
    if toolcode_dir is None:
        raise RuntimeError("toolcode sources not found (built addon or source checkout)")
    path = os.path.join(toolcode_dir, "{:s}_toolcode.py".format(name))
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    source = _expand_includes(path, source)
    module = types.ModuleType("bfacw_selftest_{:s}".format(name))
    exec(compile(source, path, "exec"), module.__dict__)  # pylint: disable=exec-used
    return module


def _call_tool(name, **params):
    """Run a toolcode ``main(Params(...))`` and return the result dict."""
    module = _load_toolcode(name)
    return module.main(module.Params(**params))._asdict()


# ---------------------------------------------------------------------------
# Fixture builder / teardown.

_BLENDER_BIN = ""


def _register_library(bpy, name, path):
    lib = bpy.context.preferences.filepaths.asset_libraries.new(
        name=name, directory=path)
    return lib


def _unlink_all_objects(bpy):
    # Only fixture-created objects (distinctive names — a debug tool must
    # never touch unrelated scene content).
    for scene in bpy.data.scenes:
        for coll in list(bpy.data.collections) + [scene.collection]:
            for obj in list(coll.objects):
                if _is_fixture_name(obj.name):
                    try:
                        coll.objects.unlink(obj)
                    except Exception:
                        pass


def _is_fixture_name(name):
    return any(str(name).startswith(p) for p in _FIXTURE_PREFIXES)


def _purge_datablocks(bpy, keep_fixture_lib=False):
    import bpy as _bpy  # noqa: F401 (shadow guard)

    # Only touch datablocks this suite created — never the user's scene.
    _unlink_all_objects(bpy)
    for attr in _PURGE_ATTRS:
        coll = getattr(bpy.data, attr, None)
        if coll is None:
            continue
        for datablock in list(coll):
            if not _is_fixture_name(datablock.name):
                continue
            try:
                if getattr(datablock, "use_fake_user", False):
                    datablock.use_fake_user = False
            except Exception:
                pass
            if datablock.users == 0:
                try:
                    coll.remove(datablock)
                except Exception:
                    pass

    # Modifiers created on step-owned objects (WireHolder) reference the
    # fixture node tree; removing the tree cleans them up implicitly.
    for obj in list(bpy.data.objects):
        if not _is_fixture_name(obj.name):
            continue
        for mod in list(obj.modifiers):
            try:
                obj.modifiers.remove(mod)
            except Exception:
                pass

    # Drop the fixture library registration (unless the caller wants to keep
    # the library for a later step).
    if not keep_fixture_lib:
        try:
            libs = bpy.context.preferences.filepaths.asset_libraries
            for lib in list(libs):
                if lib.name.startswith("BFACW_Selftest"):
                    libs.remove(lib)
        except Exception:
            pass


def build_fixture_library(bpy):
    """Create ``BFACW_Selftest`` (object / material / node-group assets).

    Returns the temp directory holding the library.
    """
    tmpdir = tempfile.mkdtemp(prefix="bfacw_selftest_")
    bpy.ops.mesh.primitive_cube_add()
    ob = bpy.context.view_layer.objects.active
    ob.name = ob.data.name = "Crate_Cube"
    ob.asset_mark()
    ob.asset_data.tags.new("prop")
    ob.asset_data.tags.new("wood")
    ob.asset_data.description = "A wooden crate prop"
    try:
        ob.asset_data.preferred_import_method = "APPEND"
        ob.asset_data.use_preferred_import_method = True
    except Exception:
        pass  # older builds may not expose preferred_import_method

    mat = bpy.data.materials.new("Brick_Mat")
    mat.asset_mark()
    mat.asset_data.tags.new("material")
    mat.asset_data.tags.new("brick")
    mat.asset_data.description = "A brick wall material"

    ng = bpy.data.node_groups.new("Tree_Foliage", "GeometryNodeTree")
    ng.interface.new_socket(name="Scale", in_out="INPUT", socket_type="NodeSocketFloat")
    ng.interface.new_socket(name="Seed", in_out="INPUT", socket_type="NodeSocketInt")
    ng.interface.new_socket(name="Color", in_out="INPUT", socket_type="NodeSocketColor")
    ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    ng.asset_mark()
    ng.asset_data.tags.new("geometry")
    ng.asset_data.tags.new("foliage")

    bpy.ops.wm.save_as_mainfile(
        filepath=os.path.join(tmpdir, "assets.blend"), copy=True)
    _register_library(bpy, "BFACW_Selftest", tmpdir)
    return tmpdir


# ---------------------------------------------------------------------------
# The deterministic suite.

_AUTO_STEPS = [
    # (key, label, fn(bpy) -> (ok, detail))
    ("libraries", "get_asset_libraries",
     lambda bpy: _step_libraries(bpy)),
    ("search_name", "search_assets (name)",
     lambda bpy: _step_search(bpy, "Brick", "Brick_Mat")),
    ("search_tag", "search_assets (tag)",
     lambda bpy: _step_search(bpy, "foliage", "Tree_Foliage")),
    ("tags", "get_asset_tags",
     lambda bpy: _step_tags(bpy)),
    ("load_material", "load_asset_in_context MATERIAL",
     lambda bpy: _step_load_material(bpy)),
    ("place_object", "place_asset_in_scene OBJECT",
     lambda bpy: _step_place_object(bpy)),
    ("wire_add", "wire_node_group add_top_level",
     lambda bpy: _step_wire(bpy, "add_top_level")),
    ("wire_output", "wire_node_group connect_to_output",
     lambda bpy: _step_wire(bpy, "connect_to_output")),
    ("interface", "get_node_group_interface",
     lambda bpy: _step_interface(bpy)),
]

MANUAL_STEPS = [
    # (key, label, instructions)
    ("browse_ui", "Asset Browser UI opens",
     "Open an Asset Browser editor and confirm BFACW_Selftest is listed with "
     "previews. Use Jump to Asset Browser from a chat prompt to auto-open it."),
    ("visual_load", "Visual load verification",
     "Drag Crate_Cube / Brick_Mat from the Asset Browser into the viewport and "
     "confirm the result looks right (preview renders correctly, no .001 renames)."),
    ("render", "Render smoke test",
     "Render a frame with the loaded collection/material applied and confirm it "
     "completes without errors."),
]

# Module-level result state for the UI to render.
_results: list[dict] = []
_running: bool = False
_last_error: str = ""


def _step_libraries(bpy):  # pylint: disable=unused-argument
    data = _call_tool("get_asset_libraries")
    libs = data if isinstance(data, list) else data.get("libraries", [])
    names = [lib.get("name") for lib in libs if isinstance(lib, dict)]
    if "BFACW_Selftest" in names:
        return True, "found: {:s}".format(", ".join(names))
    return False, "missing BFACW_Selftest: {:s}".format(str(names))


def _step_search(bpy, query, expected):  # pylint: disable=unused-argument
    data = _call_tool("search_assets", query=query, library_name="BFACW_Selftest")
    names = [m.get("name") for m in data.get("matches", [])]
    if expected in names:
        return True, "found {:s}".format(expected)
    return False, "matches: {:s}".format(str(names))


def _step_tags(bpy):  # pylint: disable=unused-argument
    data = _call_tool("get_asset_tags", library_name="BFACW_Selftest",
                      asset_name="Tree_Foliage")
    if data.get("status") != "ok":
        return False, str(data)
    ok = (data.get("editor_type") == "GeometryNodeTree"
          and "foliage" in data.get("tags", []))
    if ok:
        return True, "editor={:s} tags={:s}".format(
            data.get("editor_type"), ",".join(data.get("tags", [])))
    return False, "unexpected: editor={:s} tags={:s}".format(
        data.get("editor_type"), ",".join(data.get("tags", [])))


def _step_load_material(bpy):
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.view_layer.objects.active.name = "MatTarget"
    data = _call_tool("load_asset_in_context", library_name="BFACW_Selftest",
                      asset_name="Brick_Mat", asset_type="MATERIAL",
                      object_name="MatTarget")
    if data.get("status") != "ok":
        return False, str(data)
    state = _exec_state(
        "import bpy; ob = bpy.data.objects['MatTarget']; "
        "result = {'mats': [s.name for s in ob.data.materials]}")
    mats = state.get("mats", [])
    if any(m == "Brick_Mat" or m.startswith("Brick_Mat.") for m in mats):
        return True, "assigned: {:s}".format(",".join(mats))
    return False, "materials: {:s}".format(str(mats))


def _step_place_object(bpy):
    data = _call_tool("place_asset_in_scene", library_name="BFACW_Selftest",
                      asset_name="Crate_Cube", asset_type="OBJECT",
                      location=[2.0, 3.0, 4.0])
    if data.get("status") != "ok":
        return False, str(data)
    # The fixture keeps an in-session Crate_Cube at the origin (fake user),
    # so the append may land as Crate_Cube / Crate_Cube.001.  Accept any
    # Crate_Cube* object that actually carries the requested position.
    state = _exec_state(
        "import bpy; "
        "result = {'moved': [list(o.location) for o in bpy.data.objects "
        "if o.name.startswith('Crate_Cube')]}")
    moved = state.get("moved", [])
    for loc in moved:
        if abs(loc[0] - 2.0) < 0.01 and abs(loc[1] - 3.0) < 0.01:
            return True, "at (2,3,4)"
    return False, "locs={:s} resp={:s}".format(str(moved), str(data))


def _step_wire(bpy, insert_mode):
    bpy.ops.mesh.primitive_cube_add()
    ob = bpy.context.view_layer.objects.active
    ob.name = "WireHolder"
    ob.modifiers.new("GN", "NODES")
    tree_name = "BFACW_WireTree"
    if bpy.data.node_groups.get(tree_name):
        bpy.data.node_groups.remove(bpy.data.node_groups[tree_name])
    ng = bpy.data.node_groups.new(tree_name, "GeometryNodeTree")
    if insert_mode == "add_top_level":
        ob.modifiers["GN"].node_group = ng
    data = _call_tool("wire_node_group", library_name="BFACW_Selftest",
                      asset_name="Tree_Foliage", tree_type="GeometryNodeTree",
                      node_tree_name=tree_name, insert_mode=insert_mode)
    if data.get("status") != "ok":
        return False, str(data)
    if insert_mode == "add_top_level":
        # add_top_level inserts the group node (no auto-links by design).
        state = _exec_state(
            "import bpy; tree = bpy.data.node_groups['BFACW_WireTree']; "
            "result = {'has_group': any(getattr(n, 'node_tree', None) "
            "and n.node_tree.name == 'Tree_Foliage' for n in tree.nodes)}")
        if state.get("has_group"):
            return True, "group node inserted"
        return False, "group node not found: {:s}".format(str(data))
    if data.get("links_created"):
        return True, "{:d} links".format(len(data["links_created"]))
    return False, "no links: {:s}".format(str(data.get("unmapped", [])))


def _step_interface(bpy):  # pylint: disable=unused-argument
    # Load the group in-session first (interface tool reads bpy.data).
    data = _call_tool("get_node_group_interface", group_name="BFACW_WireTree")
    if data.get("status") != "ok":
        return False, str(data)
    inputs = [s.get("name") for s in data.get("inputs", [])]
    if "Geometry" in [s.get("name") for s in data.get("outputs", [])]:
        return True, "inputs={:s}".format(",".join(inputs))
    return False, "outputs: {:s}".format(str(data.get("outputs", [])))


def _exec_state(code):
    """Evaluate a result-style snippet in-session (same convention as tools)."""
    namespace = {"result": {}}
    exec(code, namespace)  # pylint: disable=exec-used
    return namespace.get("result", {})


# ---------------------------------------------------------------------------
# Public API for the diagnostics UI.

def get_results():
    """Current step results: list of dicts (key, label, ok, elapsed, detail)."""
    return list(_results)


def is_running():
    return _running


def last_error():
    return _last_error


def run_auto_suite():
    """Run every deterministic step against a fresh fixture (blocking)."""
    global _results, _running, _last_error  # pylint: disable=global-statement

    import bpy  # pylint: disable=import-error

    if _running:
        return
    _running = True
    _results = []
    _last_error = ""
    tmpdir = None
    try:
        _purge_datablocks(bpy)
        tmpdir = build_fixture_library(bpy)
        for key, label, fn in _AUTO_STEPS:
            t_start = time.monotonic()
            ok = False
            detail = ""
            try:
                ok, detail = fn(bpy)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                detail = str(ex)
            elapsed = time.monotonic() - t_start
            _results.append({
                "key": key, "label": label, "ok": bool(ok),
                "elapsed": elapsed, "detail": detail,
            })
    except Exception as ex:  # pylint: disable=broad-exception-caught
        _last_error = str(ex)
    finally:
        try:
            if tmpdir is not None:
                _purge_datablocks(bpy)
                import shutil as _shutil
                _shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
        _running = False