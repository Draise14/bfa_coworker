"""
Unit tests for three small asset-tool checks (#535), bpy-free.

- ``get_asset_tags._socket_count`` counts interface sockets by ``item_type``
  (real sockets are per-type subclasses such as ``NodeTreeInterfaceSocketFloat``,
  so a class-name match counted 0); panels are skipped, sockets inside a
  panel count.
- ``jump_to_asset_browser._apply_filters`` reads ``asset_library_reference``
  (``asset_library_ref`` does not exist on Blender 5.2+), so a browser left on
  "Current File" never gets a library folder written into it.
- ``jump_to_asset_browser.main`` finds an open Asset Browser by the
  ``FILE_BROWSER`` area type (``FILE`` is not an ``Area.type`` value, so
  reusing one never matched).
- ``asset_selftests.build_fixture_library`` sets the fixture's
  ``preferred_import_method`` without a guard.

Run with::

    python -m unittest tests.test_asset_tool_checks -v
"""

__all__ = ()

import os
import shutil
import sys
import tempfile
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "mcp", "blmcp", "tools")

_INCLUDE_BEGIN_PREFIX = "# @include_begin: "
_INCLUDE_END = "# @include_end"


def _expand_includes(path, source):
    out = []
    skip = False
    for line in source.splitlines(True):
        if line.startswith(_INCLUDE_BEGIN_PREFIX):
            name = line[len(_INCLUDE_BEGIN_PREFIX):].rstrip()
            with open(os.path.join(os.path.dirname(path), name), "r", encoding="utf-8") as fh:
                out.append(fh.read())
            out.append("\n")
            skip = True
        elif skip:
            if line.startswith(_INCLUDE_END):
                skip = False
        else:
            out.append(line)
    return "".join(out)


def _load(path, name):
    with open(path, "r", encoding="utf-8") as fh:
        source = _expand_includes(path, fh.read())
    module = types.ModuleType(name)
    exec(compile(source, path, "exec"), module.__dict__)  # pylint: disable=exec-used
    return module


def _toolcode(name):
    return _load(os.path.join(_TOOLS, "{:s}_toolcode.py".format(name)), name + "_toolcode")


# ---------------------------------------------------------------------------
# get_asset_tags: socket counts.


class _Item:
    def __init__(self, item_type, in_out=None):
        self.item_type = item_type
        if in_out is not None:
            self.in_out = in_out


# Stand-ins named like the real per-type classes Blender 5.2+ returns.
class NodeTreeInterfaceSocketFloat(_Item):
    def __init__(self, in_out):
        super().__init__("SOCKET", in_out)


class NodeTreeInterfaceSocketGeometry(_Item):
    def __init__(self, in_out):
        super().__init__("SOCKET", in_out)


class NodeTreeInterfacePanel(_Item):
    def __init__(self):
        super().__init__("PANEL")


class TestSocketCount(unittest.TestCase):

    def setUp(self):
        self.mod = _toolcode("get_asset_tags")

    def _tree(self, items):
        return types.SimpleNamespace(interface=types.SimpleNamespace(items_tree=items))

    def test_counts_subclassed_sockets_per_direction(self):
        # items_tree is flat: the panel and the socket inside it are both listed.
        ng = self._tree([
            NodeTreeInterfaceSocketFloat("INPUT"),
            NodeTreeInterfaceSocketFloat("INPUT"),
            NodeTreeInterfaceSocketGeometry("OUTPUT"),
            NodeTreeInterfacePanel(),
            NodeTreeInterfaceSocketFloat("INPUT"),
        ])
        self.assertEqual(self.mod._socket_count(ng, "INPUT"), 3)
        self.assertEqual(self.mod._socket_count(ng, "OUTPUT"), 1)

    def test_panels_never_count(self):
        ng = self._tree([NodeTreeInterfacePanel(), NodeTreeInterfacePanel()])
        self.assertEqual(self.mod._socket_count(ng, "INPUT"), 0)
        self.assertEqual(self.mod._socket_count(ng, "OUTPUT"), 0)

    def test_no_interface(self):
        self.assertEqual(self.mod._socket_count(types.SimpleNamespace(), "INPUT"), 0)


# ---------------------------------------------------------------------------
# jump_to_asset_browser: the "Current File" check.


class _AssetParams:
    """FileAssetSelectParams stand-in: assigning an unknown library fails like the RNA enum does."""

    def __init__(self, reference, selectable):
        self.asset_library_reference = reference
        self.directory = "//start/"
        self._selectable = set(selectable)

    def __setattr__(self, key, value):
        if key == "asset_library_reference" and hasattr(self, "_selectable") \
                and value not in self._selectable:
            raise TypeError("enum \"{:s}\" not found".format(value))
        object.__setattr__(self, key, value)


class TestJumpCurrentFileCheck(unittest.TestCase):

    def setUp(self):
        self._saved_bpy = sys.modules.get("bpy")
        libs = [
            types.SimpleNamespace(name="Props", path="/libs/props"),
            # Registered, but the browser cannot select it (folder missing / disabled).
            types.SimpleNamespace(name="Offline", path="/libs/offline"),
        ]
        bpy = types.ModuleType("bpy")
        bpy.context = types.SimpleNamespace(preferences=types.SimpleNamespace(
            filepaths=types.SimpleNamespace(asset_libraries=libs)))
        sys.modules["bpy"] = bpy
        self.mod = _toolcode("jump_to_asset_browser")

    def tearDown(self):
        if self._saved_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self._saved_bpy

    def _apply(self, reference, library_name, catalog_path="rocks"):
        params = _AssetParams(reference, selectable=("ALL", "LOCAL", "ESSENTIALS", "Props"))
        space = types.SimpleNamespace(params=params)
        lib_set, cat_set = self.mod._apply_filters(
            space, self.mod.Params(library_name=library_name, catalog_path=catalog_path))
        return params, lib_set, cat_set

    def test_current_file_browser_gets_no_library_folder(self):
        params, lib_set, cat_set = self._apply("LOCAL", "Offline")
        self.assertIsNone(lib_set)
        self.assertIsNone(cat_set)
        self.assertEqual(params.asset_library_reference, "LOCAL")
        self.assertEqual(params.directory, "//start/")

    def test_selected_library_gets_its_folder(self):
        params, lib_set, cat_set = self._apply("LOCAL", "Props")
        self.assertEqual(lib_set, "Props")
        self.assertEqual(cat_set, "rocks")
        self.assertEqual(params.directory, os.path.join("/libs/props", "rocks"))

    def test_catalog_uuid_is_set_directly(self):
        uuid = "12345678-1234-1234-1234-123456789abc"
        params = _AssetParams("LOCAL", selectable=("LOCAL",))
        params.catalog_id = ""
        space = types.SimpleNamespace(params=params)
        _lib, cat_set = self.mod._apply_filters(space, self.mod.Params(catalog_path=uuid))
        self.assertEqual(cat_set, uuid)
        self.assertEqual(params.catalog_id, uuid)


# ---------------------------------------------------------------------------
# asset_selftests: the fixture's import method.


class _AssetMetaData:
    """AssetMetaData stand-in: the enum accepts only the values Blender 5.2+ lists."""

    _METHODS = ("LINK", "APPEND", "APPEND_REUSE", "PACK")

    def __init__(self):
        self.tags = types.SimpleNamespace(new=lambda name: None)
        self.description = ""
        self.use_preferred_import_method = False
        self._method = "APPEND_REUSE"

    @property
    def preferred_import_method(self):
        return self._method

    @preferred_import_method.setter
    def preferred_import_method(self, value):
        if value not in self._METHODS:
            raise TypeError("enum \"{:s}\" not found".format(value))
        self._method = value


class _Asset:
    def __init__(self, name="", meta=_AssetMetaData):
        self.name = name
        self.data = types.SimpleNamespace(name="")
        self.asset_data = None
        self.interface = types.SimpleNamespace(new_socket=lambda **kw: None)
        self._meta = meta

    def asset_mark(self):
        self.asset_data = self._meta()


class _OldAssetMetaData(_AssetMetaData):
    """A build whose enum lacks APPEND: the fixture must now say so instead of skipping it."""

    _METHODS = ("LINK",)


class TestSelftestFixture(unittest.TestCase):

    def _build(self, meta):
        saved, libs = [], []
        view_layer = types.SimpleNamespace(objects=types.SimpleNamespace(active=None))

        def cube_add():
            view_layer.objects.active = _Asset(meta=meta)

        bpy = types.SimpleNamespace(
            ops=types.SimpleNamespace(
                mesh=types.SimpleNamespace(primitive_cube_add=cube_add),
                wm=types.SimpleNamespace(save_as_mainfile=lambda **kw: saved.append(kw))),
            context=types.SimpleNamespace(
                view_layer=view_layer,
                preferences=types.SimpleNamespace(filepaths=types.SimpleNamespace(
                    asset_libraries=types.SimpleNamespace(
                        new=lambda name, directory: libs.append((name, directory)))))),
            data=types.SimpleNamespace(
                materials=types.SimpleNamespace(new=lambda name: _Asset(name)),
                node_groups=types.SimpleNamespace(new=lambda name, kind: _Asset(name))),
        )
        ast = _load(os.path.join(_ROOT, "addon", "bfa_coworker", "asset_selftests.py"), "asset_selftests")
        tmpdir = tempfile.mkdtemp(prefix="t535_fixture_")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        ast.tempfile = types.SimpleNamespace(mkdtemp=lambda prefix="": tmpdir)
        return ast, bpy, view_layer, saved, libs, tmpdir

    def test_fixture_sets_the_import_method(self):
        ast, bpy, view_layer, saved, libs, tmpdir = self._build(_AssetMetaData)
        self.assertEqual(ast.build_fixture_library(bpy), tmpdir)
        crate = view_layer.objects.active
        self.assertEqual(crate.name, "Crate_Cube")
        self.assertEqual(crate.asset_data.preferred_import_method, "APPEND")
        self.assertTrue(crate.asset_data.use_preferred_import_method)
        self.assertEqual(libs, [("BFACW_Selftest", tmpdir)])
        self.assertTrue(saved and saved[0]["copy"])

    def test_an_unknown_import_method_is_not_swallowed(self):
        ast, bpy, _view_layer, _saved, libs, _tmpdir = self._build(_OldAssetMetaData)
        with self.assertRaises(TypeError):
            ast.build_fixture_library(bpy)
        self.assertEqual(libs, [])


# ---------------------------------------------------------------------------
# jump_to_asset_browser: an open Asset Browser is found by the FILE_BROWSER area type.


class _Area:
    """Area stand-in with the real Area.type identifiers."""

    def __init__(self, area_type, ui_type, width=100, height=100):
        self.type = area_type
        self.ui_type = ui_type
        self.width = width
        self.height = height
        self.spaces = types.SimpleNamespace(active=types.SimpleNamespace(params=None))


class TestJumpAreaType(unittest.TestCase):

    def setUp(self):
        self._saved_bpy = sys.modules.get("bpy")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self._saved_bpy

    def _bpy(self, areas):
        screen = types.SimpleNamespace(areas=areas)
        workspace = types.SimpleNamespace(name="Layout", screens=[screen])
        window = types.SimpleNamespace(workspace=workspace)
        bpy = types.ModuleType("bpy")
        bpy.app = types.SimpleNamespace(background=False)
        bpy.context = types.SimpleNamespace(
            window=window, screen=screen,
            preferences=types.SimpleNamespace(filepaths=types.SimpleNamespace(asset_libraries=[])))
        bpy.data = types.SimpleNamespace(workspaces=[workspace])
        sys.modules["bpy"] = bpy
        return bpy

    def test_reuses_an_open_asset_browser(self):
        self._bpy([_Area("VIEW_3D", "VIEW_3D"), _Area("FILE_BROWSER", "ASSETS")])
        mod = _toolcode("jump_to_asset_browser")
        res = mod.main(mod.Params(allow_edits=False))
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.area_ui_type, "ASSETS")
        self.assertIsNone(res.created)
