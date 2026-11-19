"""
Unit tests for the asset-tool follow-ups of #589, bpy-free.

- ``jump_to_asset_browser``: catalog file parsing, path matching, and the create path
  (the largest area of the current workspace becomes the Asset Browser; no workspace is
  duplicated or renamed; a failure sets the area back and says so).
- The asset indexer's F-curve counter reads layers / strips / channelbags.

Run with::

    python -m unittest tests.test_asset_browser_create -v
"""

__all__ = ()

import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "mcp", "blmcp", "tools")


def _expand_includes(path, source):
    """Splice ``# @include_begin`` blocks, mirroring ``tools_helpers``."""
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


def _load_path(path, name):
    with open(path, "r", encoding="utf-8") as fh:
        source = _expand_includes(path, fh.read())
    module = types.ModuleType(name)
    exec(compile(source, path, "exec"), module.__dict__)  # pylint: disable=exec-used
    return module


def _jump():
    return _load_path(os.path.join(_TOOLS, "jump_to_asset_browser_toolcode.py"), "jump_toolcode")


U1 = "11111111-1111-4111-8111-111111111111"
U2 = "22222222-2222-4222-8222-222222222222"
U3 = "33333333-3333-4333-8333-333333333333"


class TestParseCatalogs(unittest.TestCase):

    def test_reads_uuid_and_path(self):
        text = (
            "# comment\nVERSION 1\n\n"
            "{}:Props/Chairs:Props-Chairs\n"
            "{}:Props/Tables:Props-Tables\n"
            "{}:Rocks:Rocks\n"
        ).format(U1, U2, U3)
        self.assertEqual(_jump().parse_catalogs(text),
                         {"Props/Chairs": U1, "Props/Tables": U2, "Rocks": U3})

    def test_skips_bad_lines_and_normalises_slashes(self):
        text = "VERSION 1\nnot-a-uuid:Bad:Bad\njust one field\n{}:\\Props\\Lamps\\:x\n{}::empty path\n".format(U1, U2)
        self.assertEqual(_jump().parse_catalogs(text), {"Props/Lamps": U1})

    def test_empty(self):
        self.assertEqual(_jump().parse_catalogs(""), {})


class TestMatchCatalog(unittest.TestCase):

    CATS = {"Props/Chairs": U1, "Props/Tables": U2, "Rocks": U3, "Plants/Rocks": "4" * 8 + "-4444-4444-8444-444444444444"}

    def setUp(self):
        self.match = _jump().match_catalog

    def test_exact(self):
        self.assertEqual(self.match(self.CATS, "Props/Chairs"), (U1, []))

    def test_slashes_and_case(self):
        self.assertEqual(self.match(self.CATS, "/props\\chairs/"), (U1, []))

    def test_unique_last_segment(self):
        self.assertEqual(self.match(self.CATS, "Tables"), (U2, []))

    def test_ambiguous_last_segment_gives_candidates(self):
        uuid, nearest = self.match({"A/Rocks": U1, "B/Rocks": U2}, "Rocks")
        self.assertIsNone(uuid)
        self.assertEqual(sorted(nearest), ["A/Rocks", "B/Rocks"])

    def test_unknown_gives_nearest(self):
        uuid, nearest = self.match(self.CATS, "Prop/Chair")
        self.assertIsNone(uuid)
        self.assertIn("Props/Chairs", nearest)
        self.assertLessEqual(len(nearest), 5)

    def test_nothing_close(self):
        self.assertEqual(self.match(self.CATS, "zzzzzz"), (None, []))

    def test_empty_catalogs(self):
        self.assertEqual(self.match({}, "Props"), (None, []))


# ---------------------------------------------------------------------------
# The create path.


class _Space:
    def __init__(self):
        self.params = types.SimpleNamespace(
            asset_library_reference="LOCAL", catalog_id="", directory=b"")
        self.tree_type = ""


class _Area:
    """Area stand-in. Only real Area.type identifiers are accepted, like the RNA enum."""

    TYPES = ("VIEW_3D", "FILE_BROWSER", "NODE_EDITOR", "OUTLINER", "IMAGE_EDITOR")

    def __init__(self, ui_type, width, height, tree_type="", fail_ui=False):
        self._type = "FILE_BROWSER" if ui_type.startswith("ASSETS") else ui_type
        self.ui_type = ui_type
        self.width, self.height = width, height
        self.fail_ui = fail_ui
        self.spaces = types.SimpleNamespace(active=_Space())
        self.spaces.active.tree_type = tree_type

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value not in self.TYPES:
            raise TypeError("enum \"{}\" not found in {}".format(value, self.TYPES))
        self._type = value

    def __setattr__(self, key, value):
        if key == "ui_type" and getattr(self, "fail_ui", False) and value == "ASSETS":
            raise TypeError("ui_type refused")
        object.__setattr__(self, key, value)
        if key == "ui_type":   # like the RNA property: the editor type follows
            object.__setattr__(self, "_type", "FILE_BROWSER" if value.startswith("ASSETS") else value)


class TestCreatePath(unittest.TestCase):

    def setUp(self):
        self._saved_bpy = sys.modules.get("bpy")
        self.addCleanup(self._restore)
        self.duplicated = []

    def _restore(self):
        if self._saved_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self._saved_bpy

    def _bpy(self, areas):
        screen = types.SimpleNamespace(areas=areas)
        workspace = types.SimpleNamespace(name="Layout", screens=[screen])
        window = types.SimpleNamespace(workspace=workspace, screen=screen)
        bpy = types.ModuleType("bpy")
        bpy.app = types.SimpleNamespace(background=False)
        ops = types.SimpleNamespace(
            workspace=types.SimpleNamespace(duplicate=lambda: self.duplicated.append(1)))
        bpy.ops = ops
        bpy.context = types.SimpleNamespace(
            window=window, screen=screen,
            preferences=types.SimpleNamespace(filepaths=types.SimpleNamespace(asset_libraries=[])))
        bpy.data = types.SimpleNamespace(workspaces=[workspace], filepath="")
        bpy.context.window_manager = types.SimpleNamespace(windows=[window])
        bpy.path = types.SimpleNamespace(abspath=lambda p: p)
        sys.modules["bpy"] = bpy
        self.workspace = workspace
        return bpy

    def test_converts_the_largest_area_and_names_it(self):
        small = _Area("OUTLINER", 200, 300)
        big = _Area("VIEW_3D", 1200, 700)
        self._bpy([small, big])
        mod = _jump()
        res = mod.main(mod.Params())
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.created)
        self.assertEqual((big.type, big.ui_type), ("FILE_BROWSER", "ASSETS"))
        self.assertEqual(small.ui_type, "OUTLINER")
        self.assertEqual(res.changed_area["was"], "VIEW_3D")
        self.assertEqual(res.workspace, "Layout")
        self.assertIn("set that area's editor type back to 'VIEW_3D'", res.message)

    def test_no_workspace_is_duplicated_or_renamed(self):
        self._bpy([_Area("VIEW_3D", 1200, 700)])
        mod = _jump()
        mod.main(mod.Params())
        self.assertEqual(self.duplicated, [])
        self.assertEqual(self.workspace.name, "Layout")
        self.assertEqual(len(sys.modules["bpy"].data.workspaces), 1)

    def test_the_chat_editor_is_never_converted(self):
        chat = _Area("NODE_EDITOR", 2000, 1000, tree_type="BFACW_CoworkerTree")
        view = _Area("VIEW_3D", 800, 600)
        self._bpy([chat, view])
        mod = _jump()
        res = mod.main(mod.Params())
        self.assertEqual(res.status, "ok")
        self.assertEqual(chat.ui_type, "NODE_EDITOR")
        self.assertEqual(view.ui_type, "ASSETS")

    def test_only_the_chat_editor_means_nothing_changes(self):
        chat = _Area("NODE_EDITOR", 2000, 1000, tree_type="BFACW_CoworkerTree")
        self._bpy([chat])
        mod = _jump()
        res = mod.main(mod.Params())
        self.assertEqual(res.status, "error")
        self.assertEqual(chat.ui_type, "NODE_EDITOR")

    def test_failure_sets_the_area_back_and_says_so(self):
        big = _Area("VIEW_3D", 1200, 700, fail_ui=True)
        self._bpy([big])
        mod = _jump()
        res = mod.main(mod.Params())
        self.assertEqual(res.status, "error")
        self.assertIsNone(res.created)
        self.assertEqual((big.type, big.ui_type), ("VIEW_3D", "VIEW_3D"))
        self.assertIn("ui_type refused", res.message)
        self.assertIn("set back to 'VIEW_3D'", res.message)
        self.assertEqual(self.duplicated, [])

    def test_allow_edits_false_changes_nothing(self):
        big = _Area("VIEW_3D", 1200, 700)
        self._bpy([big])
        mod = _jump()
        res = mod.main(mod.Params(allow_edits=False))
        self.assertEqual(res.status, "error")
        self.assertEqual(big.ui_type, "VIEW_3D")

    def test_library_and_catalog_are_applied_to_the_new_browser(self):
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp(prefix="t589_create_")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "blender_assets.cats.txt"), "w", encoding="utf-8") as fh:
            fh.write("VERSION 1\n\n{}:Props/Chairs:Props-Chairs\n".format(U1))
        big = _Area("VIEW_3D", 1200, 700)
        bpy = self._bpy([big])
        bpy.context.preferences.filepaths.asset_libraries.append(
            types.SimpleNamespace(name="Lib", path=tmp))
        mod = _jump()
        res = mod.main(mod.Params(library_name="Lib", catalog_path="Props/Chairs"))
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.catalog_set, "Props/Chairs")
        self.assertEqual(res.catalog_id, U1)
        self.assertEqual(big.spaces.active.params.catalog_id, U1)
        self.assertEqual(big.spaces.active.params.directory, b"")   # no folder write

    def test_params_that_appear_later_are_applied_from_the_checker(self):
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp(prefix="t589_create_")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "blender_assets.cats.txt"), "w", encoding="utf-8") as fh:
            fh.write("VERSION 1\n\n{}:Props/Chairs:Props-Chairs\n".format(U1))
        big = _Area("VIEW_3D", 1200, 700)
        bpy = self._bpy([big])
        bpy.context.preferences.filepaths.asset_libraries.append(types.SimpleNamespace(name="Lib", path=tmp))
        real_params = big.spaces.active.params
        big.spaces.active.params = None               # a new browser has no parameters until it is drawn
        mod = _jump()
        check = mod.main(mod.Params(library_name="Lib", catalog_path="Props/Chairs"))
        self.assertTrue(callable(check))
        self.assertEqual(big.ui_type, "ASSETS")
        self.assertIsNone(check())                    # not drawn yet: still waiting
        big.spaces.active.params = real_params        # the first draw
        res = check()
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["catalog_id"], U1)
        self.assertEqual(res["changed_area"]["was"], "VIEW_3D")
        self.assertEqual(real_params.catalog_id, U1)

    def test_params_that_never_appear_are_reported_not_dropped(self):
        big = _Area("VIEW_3D", 1200, 700)
        self._bpy([big])
        big.spaces.active.params = None
        mod = _jump()
        check = mod.main(mod.Params(library_name="Lib"))
        import time
        real = time.monotonic
        try:
            time.monotonic = lambda: real() + 10.0    # past the checker's deadline
            res = check()
        finally:
            time.monotonic = real
        self.assertEqual(res["status"], "ok")
        self.assertIsNone(res["library_set"])
        self.assertIn("no parameters yet", res["message"])

    def test_the_browser_closed_before_the_checker_ran(self):
        big = _Area("VIEW_3D", 1200, 700)
        self._bpy([big])
        big.spaces.active.params = None
        mod = _jump()
        check = mod.main(mod.Params(library_name="Lib"))
        big.ui_type = "VIEW_3D"
        res = check()
        self.assertEqual(res["status"], "error")
        self.assertIn("closed", res["message"])

    def test_a_missing_catalog_is_reported_with_the_nearest(self):
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp(prefix="t589_create_")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "blender_assets.cats.txt"), "w", encoding="utf-8") as fh:
            fh.write("VERSION 1\n\n{}:Props/Chairs:Props-Chairs\n".format(U1))
        big = _Area("VIEW_3D", 1200, 700)
        bpy = self._bpy([big])
        bpy.context.preferences.filepaths.asset_libraries.append(
            types.SimpleNamespace(name="Lib", path=tmp))
        mod = _jump()
        res = mod.main(mod.Params(library_name="Lib", catalog_path="Prop/Chair"))
        self.assertEqual(res.status, "ok")
        self.assertIsNone(res.catalog_set)
        self.assertIn("Props/Chairs", res.nearest_catalogs)
        self.assertIn("not found", res.message)


class TestCreateGuards(unittest.TestCase):
    """The review's items: free-area choice, busy refusals, catalog file, UUID read-back, truthful answers."""

    def setUp(self):
        self._saved_bpy = sys.modules.get("bpy")
        self.addCleanup(self._restore)
        self.duplicated = []
        self.tmp = __import__("tempfile").mkdtemp(prefix="t589_guard_")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def _restore(self):
        if self._saved_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self._saved_bpy

    def _bpy(self, areas, libs=()):
        bpy = TestCreatePath._bpy(self, areas)
        bpy.context.window.modal_operators = []
        bpy.context.window.screen.is_temporary = False
        bpy.context.window_manager = types.SimpleNamespace(windows=[bpy.context.window])
        bpy.app.is_job_running = lambda job: False
        bpy.context.preferences.filepaths.asset_libraries.extend(libs)
        return bpy

    def _write_cats(self, folder, text):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "blender_assets.cats.txt"), "w", encoding="utf-8", errors="replace") as fh:
            fh.write(text)

    # -- 1: never take the chat's area
    def test_an_editor_with_its_sidebar_open_is_not_taken(self):
        view = _Area("VIEW_3D", 1200, 700)
        view.spaces.active.show_region_ui = True      # the compact chat can be in this sidebar
        outliner = _Area("OUTLINER", 300, 300)
        self._bpy([view, outliner])
        mod = _jump()
        res = mod.main(mod.Params())
        self.assertEqual(res.status, "ok")
        self.assertEqual(view.ui_type, "VIEW_3D")
        self.assertEqual(outliner.ui_type, "ASSETS")

    def test_an_editor_with_its_sidebar_closed_can_be_taken(self):
        view = _Area("VIEW_3D", 1200, 700)
        view.spaces.active.show_region_ui = False
        self._bpy([view, _Area("OUTLINER", 300, 300)])
        mod = _jump()
        self.assertEqual(mod.main(mod.Params()).status, "ok")
        self.assertEqual(view.ui_type, "ASSETS")

    def test_no_free_area_changes_nothing_and_says_so(self):
        view = _Area("VIEW_3D", 1200, 700)
        view.spaces.active.show_region_ui = True
        self._bpy([view])
        mod = _jump()
        res = mod.main(mod.Params())
        self.assertEqual(res.status, "error")
        self.assertIn("No free editor to turn into an Asset Browser", res.message)
        self.assertIn("split an area", res.message)
        self.assertEqual(view.ui_type, "VIEW_3D")

    # -- 4: busy
    def test_another_modal_operator_refuses(self):
        big = _Area("OUTLINER", 1200, 700)
        bpy = self._bpy([big])
        bpy.context.window.modal_operators = [
            types.SimpleNamespace(bl_idname="BFACW_OT_gpu_input"),
            types.SimpleNamespace(bl_idname="TRANSFORM_OT_translate")]
        mod = _jump()
        res = mod.main(mod.Params())
        self.assertEqual(res.status, "error")
        self.assertIn("TRANSFORM_OT_translate", res.message)
        self.assertEqual(big.ui_type, "OUTLINER")

    def test_only_the_chats_own_modal_operator_does_not_block(self):
        big = _Area("OUTLINER", 1200, 700)
        bpy = self._bpy([big])
        bpy.context.window.modal_operators = [
            types.SimpleNamespace(bl_idname="BFACW_OT_gpu_input"),
            types.SimpleNamespace(bl_idname="bfacw.gpu_input_native")]
        mod = _jump()
        self.assertEqual(mod.main(mod.Params()).status, "ok")

    def test_a_running_render_or_bake_refuses(self):
        for job, word in (("RENDER", "render"), ("OBJECT_BAKE", "bake")):
            big = _Area("OUTLINER", 1200, 700)
            bpy = self._bpy([big])
            bpy.app.is_job_running = lambda name, job=job: name == job
            mod = _jump()
            res = mod.main(mod.Params())
            self.assertEqual(res.status, "error")
            self.assertIn(word, res.message)
            self.assertEqual(big.ui_type, "OUTLINER")

    def test_a_temporary_screen_is_skipped_for_the_main_window(self):
        big = _Area("OUTLINER", 1200, 700)
        bpy = self._bpy([big])
        main_window = bpy.context.window
        temp = types.SimpleNamespace(
            workspace=main_window.workspace, screen=types.SimpleNamespace(areas=[_Area("OUTLINER", 9, 9)], is_temporary=True))
        bpy.context.window = temp
        bpy.context.window_manager = types.SimpleNamespace(windows=[temp, main_window])
        mod = _jump()
        res = mod.main(mod.Params())
        self.assertEqual(res.status, "ok")
        self.assertEqual(big.ui_type, "ASSETS")

    # -- 5: the catalog file
    def test_a_catalog_file_that_is_not_utf8_cannot_break_the_tool(self):
        folder = os.path.join(self.tmp, "lib")
        os.makedirs(folder)
        with open(os.path.join(folder, "blender_assets.cats.txt"), "wb") as fh:
            fh.write(b"VERSION 1" + bytes([10, 10]) + ("%s:Props/Ch" % U1).encode() + bytes([0xff, 0xfe]) + b"airs:x" + bytes([10]))
        big = _Area("OUTLINER", 1200, 700)
        self._bpy([big], libs=[types.SimpleNamespace(name="Lib", path=folder)])
        mod = _jump()
        res = mod.main(mod.Params(library_name="Lib", catalog_path="Props/Chairs"))
        self.assertEqual(res.status, "ok")
        self.assertEqual(big.ui_type, "ASSETS")             # converted, with a clear answer either way
        self.assertIsNone(res.catalog_set)
        self.assertIn("not found", res.message)

    def test_the_catalog_file_is_read_before_the_area_is_converted(self):
        folder = os.path.join(self.tmp, "lib")
        self._write_cats(folder, "VERSION 1" + chr(10) + chr(10) + "%s:Props/Chairs:x" % U1 + chr(10))
        big = _Area("OUTLINER", 1200, 700)
        self._bpy([big], libs=[types.SimpleNamespace(name="Lib", path=folder)])
        order = []
        mod = _jump()
        real = mod._catalogs_for
        mod._catalogs_for = lambda ref, cache: (order.append(("read", big.ui_type)), real(ref, cache))[1]
        mod.main(mod.Params(library_name="Lib", catalog_path="Props/Chairs"))
        self.assertEqual(order[0], ("read", "OUTLINER"))

    def test_current_file_uses_the_catalog_file_beside_the_blend(self):
        folder = os.path.join(self.tmp, "proj")
        self._write_cats(folder, "VERSION 1" + chr(10) + chr(10) + "%s:Local/Cat:x" % U2 + chr(10))
        big = _Area("OUTLINER", 1200, 700)
        bpy = self._bpy([big])
        bpy.data.filepath = os.path.join(folder, "scene.blend")
        mod = _jump()
        res = mod.main(mod.Params(library_name="LOCAL", catalog_path="Local/Cat"))
        self.assertEqual(res.catalog_id, U2)

    def test_an_unsaved_file_has_no_catalog_file_and_says_so(self):
        big = _Area("OUTLINER", 1200, 700)
        self._bpy([big])
        mod = _jump()
        res = mod.main(mod.Params(library_name="LOCAL", catalog_path="Local/Cat"))
        self.assertIsNone(res.catalog_set)
        self.assertIn("not saved", res.message)

    # -- 6: UUID read-back
    def test_a_catalog_id_the_browser_does_not_keep_is_not_reported(self):
        big = _Area("OUTLINER", 1200, 700)
        self._bpy([big])
        space = big.spaces.active

        class Params:
            asset_library_reference = "LOCAL"
            _id = ""

            @property
            def catalog_id(self):
                return self._id

            @catalog_id.setter
            def catalog_id(self, value):
                pass                                    # silently ignored
        space.params = Params()
        mod = _jump()
        applied = mod._apply_filters(space, mod.Params(catalog_path=U1))
        self.assertIsNone(applied.catalog)
        self.assertTrue(any("did not keep" in n for n in applied.notes))

    def test_a_36_character_name_that_is_not_a_uuid_goes_through_the_path_lookup(self):
        name = "Props/" + "x" * 30                      # 36 characters, with a "-" free of the UUID shape
        self.assertEqual(len(name), 36)
        mod = _jump()
        self.assertFalse(mod.is_uuid(name))
        self.assertFalse(mod.is_uuid("12345678-1234-1234-1234-12345678zzzz"))
        self.assertFalse(mod.is_uuid("a" * 8 + "-" * 4 + "b" * 24))
        self.assertTrue(mod.is_uuid(U1))
        folder = os.path.join(self.tmp, "lib")
        self._write_cats(folder, "VERSION 1" + chr(10) + chr(10) + "%s:%s:x" % (U3, name) + chr(10))
        big = _Area("OUTLINER", 1200, 700)
        self._bpy([big], libs=[types.SimpleNamespace(name="Lib", path=folder)])
        res = mod.main(mod.Params(library_name="Lib", catalog_path=name))
        self.assertEqual(res.catalog_id, U3)

    # -- 7: truthful answers
    def test_a_failed_set_back_is_reported_as_such(self):
        big = _Area("VIEW_3D", 1200, 700)
        self._bpy([big])

        def always_fail(self, key, value):
            if key == "ui_type":
                raise TypeError("refused " + value)
            object.__setattr__(self, key, value)
        big.__class__ = type("StuckArea", (_Area,), {"__setattr__": always_fail})
        mod = _jump()
        res = mod.main(mod.Params())
        self.assertEqual(res.status, "error")
        self.assertIn("also failed", res.message)
        self.assertNotIn("the area was set back", res.message)

    def _deferred(self, switch=None, newfile=False):
        folder = os.path.join(self.tmp, "lib")
        self._write_cats(folder, "VERSION 1" + chr(10) + chr(10) + "%s:Props/Chairs:x" % U1 + chr(10))
        big = _Area("VIEW_3D", 1200, 700)
        bpy = self._bpy([big], libs=[types.SimpleNamespace(name="Lib", path=folder)])
        real_params = big.spaces.active.params
        big.spaces.active.params = None
        mod = _jump()
        check = mod.main(mod.Params(library_name="Lib", catalog_path="Props/Chairs"))
        self.assertTrue(callable(check))
        big.spaces.active.params = real_params
        if switch:
            bpy.context.window.workspace = types.SimpleNamespace(name=switch, screens=[])
        if newfile:
            bpy.data.filepath = os.path.join(self.tmp, "other.blend")
        return check(), real_params

    def test_the_workspace_switched_before_the_answer_is_said(self):
        res, params = self._deferred(switch="Shading")
        self.assertEqual(res["status"], "ok")
        self.assertIn("made in workspace 'Layout'", res["message"])
        self.assertIn("now shows 'Shading'", res["message"])

    def test_a_file_loaded_before_the_answer_gets_nothing(self):
        res, params = self._deferred(newfile=True)
        self.assertEqual(res["status"], "error")
        self.assertIn("nothing was applied to the new file", res["message"])
        self.assertEqual(params.catalog_id, "")


# ---------------------------------------------------------------------------
# The indexer's F-curve counter.


class TestFcurveCount(unittest.TestCase):

    def setUp(self):
        self._saved_bpy = sys.modules.get("bpy")
        sys.modules["bpy"] = types.ModuleType("bpy")
        self.addCleanup(self._restore)
        shared = _load_path(os.path.join(_TOOLS, "_asset_index_shared.py"), "shared")
        script = shared._BLMCP_INDEXER_SCRIPT.rstrip()
        self.assertTrue(script.endswith("main()"))
        self.ix = types.ModuleType("indexer")
        exec(compile(script[:-len("main()")], "<indexer>", "exec"), self.ix.__dict__)  # pylint: disable=exec-used

    def _restore(self):
        if self._saved_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self._saved_bpy

    @staticmethod
    def _bag(n):
        return types.SimpleNamespace(fcurves=[object()] * n)

    def test_counts_through_layers_strips_and_channelbags(self):
        strip_a = types.SimpleNamespace(channelbags=[self._bag(3), self._bag(2)])
        strip_b = types.SimpleNamespace(channelbags=[self._bag(1)])
        action = types.SimpleNamespace(layers=[
            types.SimpleNamespace(strips=[strip_a]), types.SimpleNamespace(strips=[strip_b])])
        self.assertEqual(self.ix._action_fcurve_count(action), 6)

    def test_entry_reports_it(self):
        strip = types.SimpleNamespace(channelbags=[self._bag(4)])
        action = types.SimpleNamespace(
            name="Act", layers=[types.SimpleNamespace(strips=[strip])], frame_range=(1.0, 10.0))
        entry = self.ix._entry(action, "ACTION", "x.blend")
        self.assertEqual(entry["fcurves_count"], 4)

    def test_the_index_reads_loaded_datablocks_after_the_load_block_closes(self):
        """Blender fills ``data_to`` only when the ``with`` block exits; inside it holds the names."""
        strip = types.SimpleNamespace(channelbags=[self._bag(3)])
        action = types.SimpleNamespace(
            name="Act", layers=[types.SimpleNamespace(strips=[strip])], frame_range=(1.0, 10.0))

        class DataFrom:
            actions = ["Act"]
            materials = node_groups = objects = collections = worlds = []

        class DataTo:
            pass

        class Load:
            def __enter__(self_inner):
                return DataFrom(), DataTo_holder[0]

            def __exit__(self_inner, *exc):
                to = DataTo_holder[0]
                names = getattr(to, "actions", None)
                if names:
                    to.actions = [action]
                return False

        DataTo_holder = [None]

        def load(_path):
            DataTo_holder[0] = DataTo()
            return Load()

        class Coll(list):
            """Like bpy_prop_collection: ``in`` wants a name, an ID raises TypeError; remove takes the ID."""
            removed = []

            def __contains__(self, item):
                if not isinstance(item, str):
                    raise TypeError("bpy_prop_collection.__contains__ expected a string")
                return super().__contains__(item)

            def remove(self, item):
                self.removed.append(item)

        coll = Coll()
        purged = []
        self.ix.bpy = types.SimpleNamespace(
            data=types.SimpleNamespace(
                libraries=types.SimpleNamespace(load=load), actions=coll,
                orphans_purge=lambda **kw: purged.append(kw)))
        entries = self.ix._load_and_collect(os.path.join("lib", "a.blend"), "lib")
        self.assertEqual(entries["Act"]["fcurves_count"], 3)
        self.assertEqual(coll.removed, [action])             # the loaded datablock is dropped again
        self.assertEqual(purged, [{"do_recursive": True}])   # and what it pulled in

    def test_schema_one_index_is_stale(self):
        """Indexes written before the load-order fix are empty; they must be rebuilt."""
        import json
        import shutil
        import tempfile
        shared = _load_path(os.path.join(_TOOLS, "_asset_index_shared.py"), "shared2")
        tmp = tempfile.mkdtemp(prefix="t589_idx_")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.environ["BFACW_ASSET_INDEX_DIR"] = tmp
        self.addCleanup(os.environ.pop, "BFACW_ASSET_INDEX_DIR", None)
        bpy = types.SimpleNamespace()
        lib = os.path.join(tmp, "lib")
        os.makedirs(lib)
        index = {"schema": 1, "library_path": lib, "built_at": 1.0, "files": {}, "assets": {}}
        with open(shared._blmcp_index_path(lib, bpy), "w", encoding="utf-8") as fh:
            json.dump(index, fh)
        self.assertIsNone(shared._blmcp_index_read(lib, bpy))
        index["schema"] = shared._BLMCP_INDEX_SCHEMA
        with open(shared._blmcp_index_path(lib, bpy), "w", encoding="utf-8") as fh:
            json.dump(index, fh)
        self.assertIsNotNone(shared._blmcp_index_read(lib, bpy))
        self.assertGreaterEqual(shared._BLMCP_INDEX_SCHEMA, 2)

    def test_the_build_script_keeps_its_marker_fresh(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="t589_marker_")
        self.addCleanup(shutil.rmtree, tmp, True)
        marker = os.path.join(tmp, "m.building")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.utime(marker, (1.0, 1.0))
        self.ix._MARKER[0] = marker
        self.ix._touch_marker()
        self.assertGreater(os.path.getmtime(marker), 1000.0)

    def test_no_layers_is_zero(self):
        self.assertEqual(self.ix._action_fcurve_count(types.SimpleNamespace()), 0)
        self.assertEqual(self.ix._action_fcurve_count(types.SimpleNamespace(layers=[])), 0)


if __name__ == "__main__":
    unittest.main()
