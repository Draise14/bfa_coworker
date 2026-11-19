"""
Unit tests for the asset-catalog answers (#602, #603), bpy-free.

- ``list_asset_catalogs`` answers with the catalog paths and UUIDs of the library's
  ``blender_assets.cats.txt``, not with the library's folders; a library without that file
  answers with an empty list and says so.
- An asset without a catalog shows no catalog (not the null UUID), in the index and in
  ``get_asset_tags``; where a catalog is set its path is given as well as its UUID.
- The index schema went up, so indexes written with the old stored form are rebuilt.

Run with::

    python -m unittest tests.test_asset_catalogs -v
"""

__all__ = ()

import json
import os
import shutil
import sys
import tempfile
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "mcp", "blmcp", "tools")

U_CHAIRS = "11111111-1111-4111-8111-111111111111"
U_TABLES = "22222222-2222-4222-8222-222222222222"
U_ROCKS = "33333333-3333-4333-8333-333333333333"
U_LOCAL = "44444444-4444-4444-8444-444444444444"
U_ESS = "55555555-5555-4555-8555-555555555555"
U_UNNAMED = "66666666-6666-4666-8666-666666666666"
NULL = "00000000-0000-0000-0000-000000000000"


def _expand_includes(path, source):
    out, skip = [], False
    for line in source.splitlines(True):
        if line.startswith("# @include_begin: "):
            name = line[len("# @include_begin: "):].rstrip()
            with open(os.path.join(os.path.dirname(path), name), "r", encoding="utf-8") as fh:
                out.append(fh.read() + chr(10))
            skip = True
        elif skip:
            skip = not line.startswith("# @include_end")
        else:
            out.append(line)
    return "".join(out)


def _load(filename, name=None):
    path = os.path.join(_TOOLS, filename)
    with open(path, "r", encoding="utf-8") as fh:
        source = _expand_includes(path, fh.read())
    module = types.ModuleType(name or filename)
    exec(compile(source, path, "exec"), module.__dict__)  # pylint: disable=exec-used
    return module


def _cats_text(*rows):
    return "# made up\nVERSION 1\n\n" + "".join("{}:{}:{}\n".format(u, p, p.replace("/", "-")) for u, p in rows)


class _Case(unittest.TestCase):
    """A made-up library whose folders and catalogs differ, behind a stand-in ``bpy``."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t602_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["BFACW_ASSET_INDEX_DIR"] = os.path.join(self.tmp, "index")
        self.addCleanup(os.environ.pop, "BFACW_ASSET_INDEX_DIR", None)
        # Folders: Wood/Oak, Stone/. Catalogs: Props/Chairs, Props/Tables, Rocks.
        self.lib = os.path.join(self.tmp, "lib")
        for folder in ("Wood/Oak", "Stone"):
            os.makedirs(os.path.join(self.lib, folder))
        with open(os.path.join(self.lib, "Wood", "Oak", "a.blend"), "wb") as fh:
            fh.write(b"blend")
        self.write_cats(self.lib, (U_CHAIRS, "Props/Chairs"), (U_TABLES, "Props/Tables"), (U_ROCKS, "Rocks"))
        self.nocats = os.path.join(self.tmp, "nocats")
        os.makedirs(os.path.join(self.nocats, "Folder"))
        self.libs = [types.SimpleNamespace(name="Lib", path=self.lib),
                     types.SimpleNamespace(name="Plain", path=self.nocats)]
        self.blend_file = ""
        self.essentials = ""
        self.saved = sys.modules.get("bpy")
        sys.modules["bpy"] = self.bpy = self._make_bpy()
        self.addCleanup(self._restore)

    def _restore(self):
        if self.saved is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self.saved

    def _make_bpy(self):
        case = self
        bpy = types.ModuleType("bpy")
        bpy.context = types.SimpleNamespace(
            preferences=types.SimpleNamespace(filepaths=types.SimpleNamespace(asset_libraries=self.libs)))
        bpy.path = types.SimpleNamespace(abspath=lambda p: p)
        bpy.app = types.SimpleNamespace(binary_path="")        # no binary: an index build cannot start

        class _Data:
            @property
            def filepath(self_inner):
                return case.blend_file
        bpy.data = _Data()

        def system_resource(kind, path=""):
            if not case.essentials:
                raise ValueError("no essentials")
            return case.essentials
        bpy.utils = types.SimpleNamespace(system_resource=system_resource)
        return bpy

    @staticmethod
    def write_cats(folder, *rows):
        with open(os.path.join(folder, "blender_assets.cats.txt"), "w", encoding="utf-8") as fh:
            fh.write(_cats_text(*rows))

    def write_index(self, lib_path, assets):
        shared = _load("_asset_index_shared.py", "shared")
        blend = os.path.join(lib_path, "Wood", "Oak", "a.blend") if lib_path == self.lib else None
        files = {}
        if blend:
            st = os.stat(blend)
            files = {"Wood/Oak/a.blend": {"mtime_ns": st.st_mtime_ns, "size": st.st_size}}
        index = {"schema": shared._BLMCP_INDEX_SCHEMA, "library_path": lib_path, "built_at": 1.0,
                 "files": files, "assets": assets}
        with open(shared._blmcp_index_path(lib_path, self.bpy), "w", encoding="utf-8") as fh:
            json.dump(index, fh)


def _asset(name, kind="MATERIAL", catalog_id=""):
    return {"name": name, "type": kind, "file": "Wood/Oak/a.blend", "tags": [], "description": "",
            "is_asset": True, "catalog_id": catalog_id, "catalog_simple_name": "", "color_tag": "NONE"}


class TestListAssetCatalogs(_Case):

    def _call(self, library_name=""):
        mod = _load("list_asset_catalogs_toolcode.py")
        return mod.main(mod.Params(library_name=library_name))._asdict()

    def test_answers_with_catalog_paths_and_uuids_not_folders(self):
        res = self._call("Lib")
        self.assertEqual(res["status"], "ok")
        got = {c["path"]: c["uuid"] for c in res["catalogs"]}
        self.assertEqual(got, {"Props/Chairs": U_CHAIRS, "Props/Tables": U_TABLES, "Rocks": U_ROCKS})
        text = json.dumps(res)
        for folder in ("Wood", "Oak", "Stone", "(root)"):
            self.assertNotIn(folder, text)

    def test_every_path_it_returns_selects_the_same_catalog_for_jump(self):
        jump = _load("jump_to_asset_browser_toolcode.py")
        for cat in self._call("Lib")["catalogs"]:
            uuid, nearest = jump.match_catalog(jump._catalogs_for("Lib", {})[0], cat["path"])
            self.assertEqual((uuid, nearest), (cat["uuid"], []))

    def test_a_library_without_a_catalog_file_answers_empty_and_says_so(self):
        res = self._call("Plain")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["catalogs"], [])
        self.assertIn("no catalogs", res["message"].lower())
        self.assertNotIn("Folder", json.dumps(res))

    def test_all_libraries_lists_each_with_its_own_catalogs(self):
        res = self._call()
        self.assertEqual({c["library"] for c in res["catalogs"]}, {"Lib"})
        self.assertEqual({e["library"]: e["catalogs"] for e in res["libraries"]}, {"Lib": 3, "Plain": 0})
        self.assertIn("'Plain'", res["message"])

    def test_unknown_library(self):
        res = self._call("Nope")
        self.assertEqual(res["status"], "library not found")
        self.assertEqual(res["catalogs"], [])

    def test_counts_and_sample_names_come_from_the_index_when_it_is_ready(self):
        self.write_index(self.lib, {
            "Oak": _asset("Oak", catalog_id=U_CHAIRS),
            "Birch": _asset("Birch", catalog_id=U_CHAIRS),
            "Granite": _asset("Granite", "OBJECT", U_ROCKS),
            "Loose": _asset("Loose", catalog_id=""),
            "Gone": _asset("Gone", catalog_id=U_UNNAMED),        # a catalog the file does not name
        })
        res = self._call("Lib")
        by_path = {c["path"]: c for c in res["catalogs"]}
        self.assertEqual(by_path["Props/Chairs"]["assets"], 2)
        self.assertEqual(by_path["Props/Chairs"]["counts"], {"MATERIAL": 2})
        self.assertEqual(by_path["Rocks"]["asset_names"], [{"name": "Granite", "type": "OBJECT"}])
        self.assertEqual(by_path["Props/Tables"]["assets"], 0)
        self.assertEqual(res["libraries"][0]["assets_without_catalog"], 2)
        self.assertEqual(res["asset_counts"]["MATERIAL"], 4)

    def _no_index_answer(self, binary="", started=True):
        """list_asset_catalogs with no index; *binary* is the indexer's executable ("" = none)."""
        self.bpy.app.binary_path = binary
        mod = _load("list_asset_catalogs_toolcode.py")
        launched = []
        mod.subprocess = types.SimpleNamespace(
            Popen=lambda *a, **k: launched.append(a) or object(), DEVNULL=-3, CREATE_NO_WINDOW=0)
        res = mod.main(mod.Params(library_name="Lib"))._asdict()
        return res, launched

    def test_without_an_index_the_catalogs_still_come_and_a_started_build_is_said_to_be_running(self):
        res, launched = self._no_index_answer(binary=sys.executable)
        self.assertEqual(len(res["catalogs"]), 3)
        self.assertNotIn("assets", res["catalogs"][0])
        self.assertIsNone(res["libraries"][0]["assets_without_catalog"])
        self.assertEqual(len(launched), 1)
        self.assertIn("being built", res["message"])
        # a second call while that build runs (marker fresh): still "being built", no second build
        res2, launched2 = self._no_index_answer(binary=sys.executable)
        self.assertEqual(launched2, [])
        self.assertIn("being built", res2["message"])

    def test_no_binary_for_the_indexer_says_so_and_promises_no_retry(self):
        res, launched = self._no_index_answer(binary="")
        self.assertEqual(launched, [])
        self.assertIn("could not be built", res["message"])
        self.assertIn("no Bforartists binary", res["message"])
        self.assertNotIn("ask again", res["message"])
        self.assertEqual(len(res["catalogs"]), 3)

    def test_a_missing_library_folder_says_so_and_promises_no_retry(self):
        self.libs.append(types.SimpleNamespace(name="Gone", path=os.path.join(self.tmp, "gone")))
        res = self._call("Gone")
        self.assertIn("does not exist", res["message"])
        self.assertNotIn("ask again", res["message"])
        self.assertIsNone(res["libraries"][0]["assets_without_catalog"])

    def test_non_assets_in_the_index_are_not_counted(self):
        """A library of one asset built from internal node groups and materials: those are not assets."""
        assets = {"Chair": _asset("Chair", "OBJECT", U_CHAIRS)}
        for i in range(6):
            assets["internal_ng_%d" % i] = dict(_asset("internal_ng_%d" % i, "NODETREE"), is_asset=False)
        for i in range(2):
            assets["internal_mat_%d" % i] = dict(_asset("internal_mat_%d" % i), is_asset=False)
        self.write_index(self.lib, assets)
        res = self._call("Lib")
        self.assertEqual(res["libraries"][0]["assets_without_catalog"], 0)
        self.assertEqual(sum(res["asset_counts"].values()), 1)
        by_path = {c["path"]: c for c in res["catalogs"]}
        self.assertEqual(by_path["Props/Chairs"]["assets"], 1)
        self.assertEqual([n["name"] for c in res["catalogs"] for n in c["asset_names"]], ["Chair"])

    def test_two_catalogs_with_one_path_are_both_listed_with_their_own_counts(self):
        self.write_cats(self.lib, (U_CHAIRS, "Props/Chairs"), (U_TABLES, "Props/Chairs"), (U_ROCKS, "Rocks"))
        self.write_index(self.lib, {"A": _asset("A", catalog_id=U_CHAIRS), "B": _asset("B", catalog_id=U_TABLES),
                                    "C": _asset("C", catalog_id=U_TABLES)})
        res = self._call("Lib")
        rows = [(c["path"], c["uuid"], c["assets"]) for c in res["catalogs"]]
        self.assertEqual(rows, [("Props/Chairs", U_CHAIRS, 1), ("Props/Chairs", U_TABLES, 2), ("Rocks", U_ROCKS, 0)])
        self.assertEqual(res["libraries"][0]["assets_without_catalog"], 0)

    def test_the_real_tool_code_loader_gives_working_tool_code(self):
        """The tool code as the server sends it (``toolcode_load_from_filepath``), not this file's splicer."""
        sys.path.insert(0, os.path.join(_ROOT, "mcp"))
        self.addCleanup(sys.path.remove, os.path.join(_ROOT, "mcp"))
        from blmcp.tools_helpers import toolcode_load_from_filepath  # pylint: disable=import-outside-toplevel
        for tool in ("list_asset_catalogs", "get_asset_tags", "jump_to_asset_browser"):
            source = toolcode_load_from_filepath(os.path.join(_TOOLS, tool + ".py"))
            self.assertFalse(any(line.startswith("# @include_begin") for line in source.splitlines()))
            module = types.ModuleType("real_" + tool)
            exec(compile(source, tool, "exec"), module.__dict__)  # pylint: disable=exec-used
            self.assertTrue(callable(module._catalogs_for), tool)
            if tool == "list_asset_catalogs":
                res = module.main(module.Params(library_name="Lib"))
                self.assertEqual(sorted(c["path"] for c in res.catalogs), ["Props/Chairs", "Props/Tables", "Rocks"])

    def test_current_file_uses_the_catalog_file_beside_the_blend(self):
        folder = os.path.join(self.tmp, "scene")
        os.makedirs(folder)
        self.write_cats(folder, (U_LOCAL, "Local/Cat"))
        self.blend_file = os.path.join(folder, "s.blend")
        res = self._call("LOCAL")
        self.assertEqual([(c["path"], c["uuid"]) for c in res["catalogs"]], [("Local/Cat", U_LOCAL)])

    def test_current_file_unsaved_has_none_and_says_why(self):
        res = self._call("LOCAL")
        self.assertEqual(res["catalogs"], [])
        self.assertIn("not saved", res["message"])
        self.assertEqual(res["message"].count("no catalog"), 1)      # said once, not twice

    def test_essentials_reads_the_builtin_catalog_file(self):
        self.essentials = os.path.join(self.tmp, "ess")
        os.makedirs(self.essentials)
        self.write_cats(self.essentials, (U_ESS, "Essentials/Wood"))
        res = self._call("ESSENTIALS")
        self.assertEqual([(c["path"], c["uuid"]) for c in res["catalogs"]], [("Essentials/Wood", U_ESS)])

    def test_a_catalog_file_with_a_stray_byte_does_not_break_it(self):
        with open(os.path.join(self.lib, "blender_assets.cats.txt"), "wb") as fh:
            fh.write(b"VERSION 1\n\n" + U_CHAIRS.encode() + b":Props/Chairs:Props-Chairs\n\xff\xfe junk\n")
        res = self._call("Lib")
        self.assertEqual(res["status"], "ok")
        self.assertEqual([c["path"] for c in res["catalogs"]], ["Props/Chairs"])


class TestAssetWithoutCatalog(_Case):

    def _tags(self, asset_name):
        mod = _load("get_asset_tags_toolcode.py")
        return mod.main(mod.Params(library_name="Lib", asset_name=asset_name))._asdict()

    def test_no_catalog_shows_no_catalog_not_the_null_uuid(self):
        self.write_index(self.lib, {"Loose": _asset("Loose", catalog_id=""),
                                    "Old": _asset("Old", catalog_id=NULL)})
        for name in ("Loose", "Old"):
            res = self._tags(name)
            self.assertEqual(res["status"], "ok")
            self.assertEqual((res["catalog"], res["catalog_id"]), ("", ""))
            self.assertNotIn(NULL, json.dumps(res))

    def test_a_set_catalog_gives_its_path_and_uuid(self):
        self.write_index(self.lib, {"Oak": _asset("Oak", catalog_id=U_CHAIRS)})
        res = self._tags("Oak")
        self.assertEqual((res["catalog"], res["catalog_id"]), ("Props/Chairs", U_CHAIRS))

    def test_a_catalog_the_file_does_not_name_keeps_its_uuid_with_no_path(self):
        self.write_index(self.lib, {"Gone": _asset("Gone", catalog_id=U_UNNAMED)})
        res = self._tags("Gone")
        self.assertEqual((res["catalog"], res["catalog_id"]), ("", U_UNNAMED))

    def _live_stub(self, catalog_id):
        """get_asset_tags with no index: the asset is loaded from its .blend (live path)."""
        mat = types.SimpleNamespace(
            asset_data=types.SimpleNamespace(tags=[], catalog_id=catalog_id), color_tag="NONE",
            description="", use_nodes=True, blend_method="OPAQUE", preview=None)

        class Load:
            def __init__(self_inner, path):
                self_inner.to = types.SimpleNamespace()

            def __enter__(self_inner):
                names = types.SimpleNamespace(
                    materials=["Mat"], node_groups=[], objects=[], worlds=[], actions=[], collections=[])
                return names, self_inner.to

            def __exit__(self_inner, *exc):
                if getattr(self_inner.to, "materials", None):
                    self_inner.to.materials = [mat]
                return False
        self.bpy.data.libraries = types.SimpleNamespace(load=Load)

    def test_without_an_index_the_null_uuid_is_no_catalog(self):
        self._live_stub(NULL)
        res = self._tags("Mat")
        self.assertEqual((res["status"], res["catalog"], res["catalog_id"]), ("ok", "", ""))
        self.assertNotIn(NULL, json.dumps(res))

    def test_without_an_index_a_set_catalog_gives_path_and_uuid(self):
        self._live_stub(U_CHAIRS)
        res = self._tags("Mat")
        self.assertEqual((res["catalog"], res["catalog_id"]), ("Props/Chairs", U_CHAIRS))

    def test_an_asset_in_either_of_two_catalogs_with_one_path_shows_it(self):
        self.write_cats(self.lib, (U_CHAIRS, "Props/Chairs"), (U_TABLES, "Props/Chairs"))
        self.write_index(self.lib, {"A": _asset("A", catalog_id=U_CHAIRS), "B": _asset("B", catalog_id=U_TABLES)})
        for name, uuid in (("A", U_CHAIRS), ("B", U_TABLES)):
            res = self._tags(name)
            self.assertEqual((res["catalog"], res["catalog_id"]), ("Props/Chairs", uuid))

    def test_a_library_without_a_catalog_file_still_answers(self):
        os.remove(os.path.join(self.lib, "blender_assets.cats.txt"))
        self.write_index(self.lib, {"Oak": _asset("Oak", catalog_id=U_CHAIRS)})
        res = self._tags("Oak")
        self.assertEqual((res["status"], res["catalog"], res["catalog_id"]), ("ok", "", U_CHAIRS))


class TestIndexedCatalogForm(unittest.TestCase):

    def setUp(self):
        saved = sys.modules.get("bpy")
        sys.modules["bpy"] = types.ModuleType("bpy")
        self.addCleanup(lambda: sys.modules.__setitem__("bpy", saved) if saved else sys.modules.pop("bpy", None))
        self.shared = _load("_asset_index_shared.py", "shared")
        script = self.shared._BLMCP_INDEXER_SCRIPT.rstrip()
        self.ix = types.ModuleType("indexer")
        exec(compile(script[:-len("main()")], "<indexer>", "exec"), self.ix.__dict__)  # pylint: disable=exec-used

    def _entry(self, **asset_data):
        data = types.SimpleNamespace(tags=[], **asset_data)
        block = types.SimpleNamespace(name="A", asset_data=data)
        return self.ix._entry(block, "MATERIAL", "a.blend")

    def test_the_null_uuid_is_stored_as_no_catalog(self):
        entry = self._entry(catalog_id=NULL, catalog_simple_name="")
        self.assertEqual((entry["catalog_id"], entry["catalog_simple_name"]), ("", ""))
        self.assertNotIn("catalog", entry)

    def test_a_catalog_is_stored_as_uuid_and_simple_name(self):
        entry = self._entry(catalog_id=U_CHAIRS, catalog_simple_name="Props-Chairs")
        self.assertEqual((entry["catalog_id"], entry["catalog_simple_name"]), (U_CHAIRS, "Props-Chairs"))

    def test_an_asset_without_asset_data_has_no_catalog(self):
        entry = self.ix._entry(types.SimpleNamespace(name="A", asset_data=None), "MATERIAL", "a.blend")
        self.assertEqual(entry["catalog_id"], "")

    def test_the_schema_went_up_and_the_script_writes_the_same_number(self):
        self.assertGreaterEqual(self.shared._BLMCP_INDEX_SCHEMA, 3)
        self.assertIn('"schema": {:d},'.format(self.shared._BLMCP_INDEX_SCHEMA), self.shared._BLMCP_INDEXER_SCRIPT)

    def test_an_index_in_the_old_stored_form_is_rebuilt(self):
        tmp = tempfile.mkdtemp(prefix="t602_idx_")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.environ["BFACW_ASSET_INDEX_DIR"] = tmp
        self.addCleanup(os.environ.pop, "BFACW_ASSET_INDEX_DIR", None)
        bpy = types.SimpleNamespace()
        lib = os.path.join(tmp, "lib")
        os.makedirs(lib)
        index = {"schema": 2, "library_path": lib, "built_at": 1.0, "files": {},
                 "assets": {"A": {"name": "A", "type": "MATERIAL", "catalog": NULL}}}
        with open(self.shared._blmcp_index_path(lib, bpy), "w", encoding="utf-8") as fh:
            json.dump(index, fh)
        self.assertIsNone(self.shared._blmcp_index_read(lib, bpy))


class TestDuplicatePaths(unittest.TestCase):

    def setUp(self):
        self.mod = _load("_asset_catalogs_shared.py", "cats")
        self.text = _cats_text((U_CHAIRS, "Props/Chairs"), (U_TABLES, "Props/Chairs"), (U_ROCKS, "Rocks"))

    def test_every_entry_is_kept_in_file_order(self):
        self.assertEqual(self.mod.parse_catalog_entries(self.text),
                         [("Props/Chairs", U_CHAIRS), ("Props/Chairs", U_TABLES), ("Rocks", U_ROCKS)])

    def test_a_path_picks_the_first_uuid(self):
        self.assertEqual(self.mod.parse_catalogs(self.text), {"Props/Chairs": U_CHAIRS, "Rocks": U_ROCKS})
        self.assertEqual(self.mod.match_catalog(self.mod.parse_catalogs(self.text), "props/chairs"), (U_CHAIRS, []))

    def test_every_uuid_resolves_to_its_path(self):
        by_uuid = self.mod.catalog_paths_by_uuid(self.mod.parse_catalog_entries(self.text))
        self.assertEqual(by_uuid[U_CHAIRS], "Props/Chairs")
        self.assertEqual(by_uuid[U_TABLES], "Props/Chairs")

    def test_unique_paths_parse_as_before(self):
        text = _cats_text((U_CHAIRS, "Props/Chairs"), (U_ROCKS, "Rocks"))
        self.assertEqual(self.mod.parse_catalogs(text), {"Props/Chairs": U_CHAIRS, "Rocks": U_ROCKS})


class TestCatalogHelpers(unittest.TestCase):

    def setUp(self):
        self.mod = _load("_asset_catalogs_shared.py", "cats")

    def test_no_catalog_forms(self):
        for value in ("", None, NULL, NULL.upper(), "  "):
            self.assertEqual(self.mod.catalog_uuid_or_empty(value), "")

    def test_describe_catalog(self):
        paths = self.mod.catalog_paths_by_uuid({"Props/Chairs": U_CHAIRS})
        self.assertEqual(self.mod.describe_catalog(U_CHAIRS.upper(), paths),
                         {"catalog": "Props/Chairs", "catalog_id": U_CHAIRS.upper()})
        self.assertEqual(self.mod.describe_catalog(NULL, paths), {"catalog": "", "catalog_id": ""})
        self.assertEqual(self.mod.describe_catalog(U_UNNAMED, paths), {"catalog": "", "catalog_id": U_UNNAMED})


if __name__ == "__main__":
    unittest.main()
