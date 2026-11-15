# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the risky-step text rules against ordinary Blender code (#549).

1. Blender's own unlinks (an object or a child collection out of a collection, an
   ``unlink`` operator) don't raise the "delete or move files" card.
2. File removal still does: ``os.unlink`` / ``os.remove``, ``Path(...).unlink()``,
   ``shutil.rmtree``, a variable holding a path, an import alias, a plain assignment of
   the function, and an ``.unlink(`` whose receiver can't be told.
3. The other rules don't match the Blender calls that share their words
   (``.remove(`` / ``.move(`` / ``.write(`` / ``.replace(`` on Blender data, operators
   with ``export`` or ``save`` in their names that aren't file exports).

Run with::

    python -m unittest tests.test_risky_steps_549 -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_DELETE = "delete or move files"


def _load():
    name = "bfacw_risky_549_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".risky_steps")


rs = _load()


def _why(code):
    return rs.reason(code, "")          # "" = an unsaved .blend: no Blender asked


class TestBlenderUnlinksPass(unittest.TestCase):

    def test_collection_unlinks_dont_ask(self):
        for code in (
                "import bpy\ncoll = bpy.data.collections['Props']\ncoll.objects.unlink(obj)",
                "for c in obj.users_collection:\n    c.objects.unlink(obj)",
                "bpy.context.scene.collection.objects.unlink(obj)",
                "bpy.context.scene.collection.children.unlink(sub)",
                "bpy.data.collections['A'].children.unlink(bpy.data.collections['B'])",
                "parent.children .unlink(child)",
                "bpy.data.collections[\"Furniture\"].objects.unlink( chair )",
                "bpy.ops.text.unlink()",
                "bpy.ops.font.unlink()",
                "bpy.ops.action.unlink(force_delete=False)",
                "bpy.ops.ed.lib_id_unlink()",
                "obj.name = 'unlink_me'"):
            with self.subTest(code=code):
                self.assertEqual(_why(code), "")

    def test_a_blender_unlink_beside_a_file_removal_still_asks(self):
        code = "coll.objects.unlink(obj)\nfrom pathlib import Path\nPath(p).unlink()"
        self.assertEqual(_why(code), _DELETE)


class TestFileRemovalAsks(unittest.TestCase):

    def test_plain_file_calls(self):
        for code in (
                "import os\nos.unlink(p)",
                "import os\nos.remove(p)",
                "import os\nos.rmdir(d)",
                "import os\nos.rename(a, b)",
                "import shutil\nshutil.rmtree(d)",
                "import shutil\nshutil.move(a, b)",
                "from pathlib import Path\nPath(p).unlink()",
                "from pathlib import Path\nPath(p).unlink(missing_ok=True)",
                "from pathlib import Path\n(Path(folder) / 'a.png').unlink()",
                "from pathlib import Path\nPath(d).rmdir()"):
            with self.subTest(code=code):
                self.assertEqual(_why(code), _DELETE)

    def test_a_variable_holding_a_path(self):
        for code in (
                "from pathlib import Path\np = Path(bpy.path.abspath('//old.png'))\np.unlink()",
                "import pathlib\ntarget = pathlib.Path(name)\nif target.exists():\n    target.unlink()",
                "import os\nf = bpy.path.abspath(img.filepath)\nos.remove(f)",
                "for f in folder.glob('*.tmp'):\n    f.unlink()"):
            with self.subTest(code=code):
                self.assertEqual(_why(code), _DELETE)

    def test_through_an_alias(self):
        for code in (
                "import os as o\no.remove(p)",
                "import os as o\no.unlink(p)",
                "from os import remove\nremove(p)",
                "from os import unlink as rm\nrm(p)",
                "from shutil import rmtree\nrmtree(d)",
                "import shutil as sh\nsh.rmtree(d)",
                "from pathlib import Path as P\nP(p).unlink()",
                "import os\nrm = os.remove\nrm(p)",
                "import os as o\nkill = o.unlink\nkill(p)"):
            with self.subTest(code=code):
                self.assertEqual(_why(code), _DELETE)

    def test_cant_tell_keeps_the_card(self):
        # A receiver that might hold a path: the safe side.
        for code in ("thing.unlink(x)", "objs = coll.objects\nobjs.unlink(obj)", "get_path().unlink()",
                     "exec('import os; os.remove(p)')"):
            with self.subTest(code=code):
                self.assertEqual(_why(code), _DELETE)

    def test_code_that_doesnt_parse_keeps_the_text_rules(self):
        self.assertEqual(_why("Path(p).unlink(\nif"), _DELETE)
        self.assertEqual(_why("coll.objects.unlink(obj\nif"), "")


class TestOtherRulesAgainstBlenderCode(unittest.TestCase):

    def test_blender_calls_that_share_the_words(self):
        for code in (
                "bpy.data.objects.remove(obj, do_unlink=True)",
                "bpy.data.meshes.remove(me)",
                "obj.modifiers.remove(mod)",
                "obj.vertex_groups.remove(vg)",
                "tree.nodes.remove(node)\ntree.links.remove(link)",
                "obj.data.materials.pop(index=0)",
                "bpy.ops.object.modifier_move_up(modifier='Bevel')",
                "bpy.ops.object.modifier_move_to_index(modifier='Bevel', index=0)",
                "bpy.ops.object.move_to_collection(collection_index=1)",
                "bpy.ops.collection.objects_remove(collection='A')",
                "seq.strips.remove(strip)",
                "fc.keyframe_points.remove(kp)",
                "name = obj.name.replace('.001', '')",
                "text = bpy.data.texts.new('n')\ntext.write('hello')",
                "bpy.ops.object.shade_smooth()\nbpy.ops.wm.context_toggle(data_path='space_data.show_region_ui')",
                "pos = obj.location\npos.rename = 1",
                "mods.replace(a, b)",
                "bpy.ops.wm.save_userpref",
                "import os\npath = os.path.join(bpy.path.abspath('//'), 'x.png')\nos.path.exists(path)",
                "from os import path\npath.join(a, b)",
                "from bpy.types import Operator\nimport bmesh\nbmesh.ops.remove_doubles(bm, verts=bm.verts)"):
            with self.subTest(code=code):
                self.assertEqual(_why(code), "")

    def test_running_a_program_through_an_alias(self):
        for code in ("import subprocess\nsubprocess.run(['ls'])", "from subprocess import run\nrun(['ls'])",
                     "import subprocess as sp\nsp.Popen(['ls'])", "from os import system\nsystem('ls')",
                     "import os\nos.startfile(p)"):
            with self.subTest(code=code):
                self.assertEqual(_why(code), "run another program")


if __name__ == "__main__":
    unittest.main()
