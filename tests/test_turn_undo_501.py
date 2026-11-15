# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
One Ctrl+Z per chat turn (#501), without Blender.

1. turn_undo's rules: which calls may change the scene, a script that only
   reads, when a failed script may be taken back by reloading the newest undo
   step, where Revert goes on the undo list.
2. coworker_api's turn checkpoints: no step at the turn's start, one at its end
   only when it changed something; side by side a step per write call and no
   empty end step; Revert goes back by the turn's own steps (both: by every
   Coworker step since the turn began); a tool skips its own step only inside a
   turn; a loop turn shorter than the chat timer's tick still ends.
3. The tools that pushed their own step skip it from a chat turn.

Run with::

    python -m unittest tests.test_turn_undo_501 -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ADDON = _ROOT / "addon" / "bfa_coworker"
_PKG = "bfacw_undo501_pkg"


def _load(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


tu = _load("turn_undo")
cw = _load("coworker_api")
sbs = _load("side_by_side")

PLAN_SAFE = {"get_scene_digest", "get_objects_summary", "update_plan"}


class TestScriptOnlyReads(unittest.TestCase):

    def test_reading_scripts(self):
        for code in (
                "import bpy\nprint(len(bpy.data.objects), bpy.data.objects['Cube'].location[:])\n",
                "import bpy\nnames = []\nfor ob in bpy.data.objects:\n    names.append(ob.name)\nprint(names)\n",
                "import bpy\nresult = {}\nresult['n'] = len(bpy.data.meshes)\n",
                "import bpy\nob = bpy.data.objects.get('Cube')\nprint(ob.select_get(), ob.matrix_world.to_translation())\n",
                "import bpy\nprint(sorted(o.name for o in bpy.context.scene.objects if o.type == 'MESH'))\n",
                "import bpy\ndef fmt(v):\n    return round(v, 2)\nprint([fmt(c) for c in bpy.data.objects['Cube'].location])\n",
        ):
            self.assertTrue(tu.script_only_reads(code), code)

    def test_changing_scripts(self):
        for code in (
                "import bpy\nbpy.data.objects['Cube'].location.x = 3.0\n",           # an assignment
                "import bpy\nbpy.ops.mesh.primitive_cube_add()\n",                    # an operator
                "import bpy\nbpy.data.objects['Cube'].location.x += 1\n",
                "import bpy\nbpy.data.texts.new('Notes')\n",                          # a new datablock
                "import bpy\nbpy.data.objects['Cube'].select_set(True)\n",            # a call that sets
                "import bpy\nbpy.data.objects['Cube']['tag'] = 1\n",                  # a custom property
                "import bpy\nob = bpy.data.objects['Cube']\nob.modifiers.clear()\n",  # not a list of its own
                "import bpy\nfor bpy.context.scene.frame_current in range(3):\n    pass\n",
                "import bpy\ncopy = bpy.data.objects['Cube'].copy()\n",
                "import bpy\nexec('x = 1')\n",
                "import bpy\nglobal x\n",
                "import bpy\nprint(\n",                                                # not Python: unsure
        ):
            self.assertFalse(tu.script_only_reads(code), code)


class TestCallChanges(unittest.TestCase):

    def test_calls(self):
        self.assertFalse(tu.call_changes("get_scene_digest", "", PLAN_SAFE), "only looks")
        self.assertFalse(tu.call_changes("delegate_task", "", PLAN_SAFE), "a helper's turn is its own")
        self.assertFalse(tu.call_changes("execute_blender_code", "import bpy\nprint(len(bpy.data.objects))",
                                         PLAN_SAFE))
        self.assertTrue(tu.call_changes("execute_blender_code", "import bpy\nbpy.ops.object.delete()", PLAN_SAFE))
        self.assertTrue(tu.call_changes("build_node_tree_from_code", "", PLAN_SAFE))
        self.assertTrue(tu.call_changes("set_strip_frame_range", "", PLAN_SAFE))
        self.assertTrue(tu.call_changes("", "", PLAN_SAFE), "unknown: counted")


class TestReloadAllowed(unittest.TestCase):

    def test_only_for_the_turns_first_change_and_nobody_else(self):
        self.assertTrue(tu.reload_allowed(True, True, [], []))
        self.assertFalse(tu.reload_allowed(False, True, [], []), "an earlier change of the turn would go too")
        self.assertFalse(tu.reload_allowed(True, False, [], []), "you changed the scene meanwhile")
        self.assertFalse(tu.reload_allowed(True, True, ["t2"], []), "another chat stepped since")
        self.assertFalse(tu.reload_allowed(True, True, [], ["t2"]), "another chat's changes not yet on the list")


class TestRevertTarget(unittest.TestCase):

    def test_one_step_back(self):
        names = ["Original", "Probe setup", "Coworker (3D)'s turn"]
        self.assertEqual(tu.revert_target(1, names, 2, "Coworker (3D)'s turn"), (1, ""))
        self.assertEqual(tu.revert_target(1, names, None, "Coworker (3D)'s turn"), (1, ""), "no index: the newest")

    def test_several_steps_back(self):
        names = ["Original", "Move", "Coworker (3D): execute blender code", "Coworker (Node): execute blender code"]
        self.assertEqual(tu.revert_target(2, names, 3, "Coworker (Node): execute blender code"), (1, ""))

    def test_refusals(self):
        names = ["Original", "Coworker (3D)'s turn", "Move"]
        target, why = tu.revert_target(1, names, 2, "Coworker (3D)'s turn")
        self.assertEqual(target, -1)
        self.assertIn("Undo History", why, "something else stepped after the turn")
        target, why = tu.revert_target(1, names[:2] + ["x"], 1, "Coworker (3D)'s turn")
        self.assertEqual(target, -1, "not at the newest step (undone meanwhile)")
        target, why = tu.revert_target(3, ["Coworker (3D)'s turn"], 0, "Coworker (3D)'s turn")
        self.assertIn("more steps than the undo history keeps", why)
        self.assertEqual(tu.revert_target(0, names, 2, "Move")[0], -1)

    def test_long_names_are_cut_by_blender(self):
        long = "Coworker (A very long chat title that goes on and on)'s turn and more"
        shown = long.encode("utf-8")[:63].decode("utf-8", "ignore")
        self.assertTrue(tu.same_step(shown, long))
        self.assertEqual(tu.revert_target(1, ["Original", shown], 1, long), (0, ""))
        self.assertFalse(tu.same_step("Coworker", "Coworker (3D)'s turn"))


class _TurnCase(unittest.TestCase):

    def setUp(self):
        self.calls = []
        saved = cw._undo
        cw._undo = lambda message="", to_item=-1: self.calls.append(message or to_item) or True
        self.addCleanup(setattr, cw, "_undo", saved)
        cw._turns.clear()
        cw._turns[""] = cw._turn
        cw._turn.update(before=False, after=False, edits=0, name="", mark=0, writes=0, steps=0, pushes=0)
        cw._ended.clear()
        sbs.JOURNAL.clear()
        saved_working = list(sbs.WORKING)
        sbs.WORKING[:] = [lambda: list(self.working)]
        self.addCleanup(lambda: sbs.WORKING.__setitem__(slice(None), saved_working))
        self.working = []

    def pushes(self):
        return [c for c in self.calls if isinstance(c, str)]

    def stack(self):
        names = ["Original"] + self.pushes()
        wm = types.SimpleNamespace(undo_stack=types.SimpleNamespace(
            steps=[types.SimpleNamespace(name=n) for n in names], active_index=len(names) - 1))
        sys.modules["bpy"] = types.SimpleNamespace(context=types.SimpleNamespace(window_manager=wm))
        self.addCleanup(sys.modules.pop, "bpy", None)
        return names

    def write(self, thread="", tool="execute_blender_code", code="import bpy\nbpy.ops.mesh.primitive_cube_add()",
              step=""):
        sbs.JOURNAL.note(thread)            # as the bridge's _note_step does first
        cw.note_call(thread, tool, code, step=step)


class TestOneStepPerTurn(_TurnCase):

    def test_a_turn_that_changed_nothing_pushes_no_step(self):
        cw.turn_started("Coworker's turn")
        cw.note_call("", "execute_blender_code", "import bpy\nprint(len(bpy.data.objects))")
        cw.turn_finished("Coworker's turn")
        self.assertEqual(self.calls, [], "no step at the start, none at the end")
        self.assertFalse(cw.can_revert(), "nothing to revert")
        self.assertIn("no finished turn", cw.revert_turn())

    def test_a_turn_with_two_tool_steps_is_one_step(self):
        cw.turn_started("Coworker's turn")
        cw.turn_started("Coworker's turn")      # the timer seeing it again: nothing
        self.assertEqual(self.calls, [], "no step before the turn")
        self.write(tool="build_node_tree_from_code", code="")
        self.write()
        cw.turn_finished("Coworker's turn")
        cw.turn_finished("Coworker's turn")     # seen again: nothing
        self.assertEqual(self.pushes(), ["Coworker's turn"], "one step, at the end")
        self.assertTrue(cw.can_revert())

    def test_revert_without_the_undo_list_is_one_undo(self):
        cw.turn_started("Coworker's turn")
        self.write()
        cw.turn_finished("Coworker's turn")
        self.assertEqual(cw.revert_turn(), "")
        self.assertEqual(self.calls[-1], -1, "one plain undo: the turn is exactly one step (5.2.x)")
        self.assertEqual(self.calls.count(-1), 1)
        self.assertFalse(cw.can_revert())

    def test_revert_with_the_undo_list_goes_to_the_step_before_the_turns(self):
        cw.turn_started("Coworker's turn")
        self.write()
        cw.turn_finished("Coworker's turn")
        names = self.stack()
        self.assertEqual(cw.revert_turn(), "")
        self.assertEqual(self.calls[-1], len(names) - 2, "undo_history to the step under the turn's")

    def test_revert_refused_when_something_else_stepped_after_the_turn(self):
        cw.turn_started("Coworker's turn")
        self.write()
        cw.turn_finished("Coworker's turn")
        self.calls.append("Move")              # a step that isn't Coworker's (no edit counted)
        self.stack()
        self.assertIn("Undo History", cw.revert_turn())
        self.assertEqual(self.calls[-1], "Move", "nothing undone")

    def test_tools_skip_their_own_step_only_inside_a_turn(self):
        with cw.for_thread("t1"):
            self.assertFalse(cw.turn_owns_undo(), "no turn: a plain MCP call keeps its step")
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        with cw.for_thread("t1"):
            self.assertTrue(cw.turn_owns_undo())
        with cw.for_thread("t2"):
            self.assertFalse(cw.turn_owns_undo(), "another chat's call: not this turn's")
        cw.turn_finished("Coworker (3D)'s turn", thread="t1")
        with cw.for_thread("t1"):
            self.assertFalse(cw.turn_owns_undo())

    def test_a_loop_turn_shorter_than_a_tick_still_ends(self):
        cw.turn_started("Coworker's turn")
        cw.loop_turn_ended("")
        self.assertTrue(cw.turn_open(""))
        self.assertTrue(cw.take_ended(""))
        self.assertFalse(cw.take_ended(""), "asked once")
        self.assertFalse(cw.take_ended("t9"))


class TestSideBySide(_TurnCase):

    def test_each_write_call_its_own_step_and_no_empty_end_step(self):
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        cw.turn_started("Coworker (Node)'s turn", thread="t2")
        self.write("t1", step="Coworker (3D): execute blender code")
        self.write("t2", step="Coworker (Node): execute blender code")
        cw.turn_finished("Coworker (3D)'s turn", thread="t1")
        cw.turn_finished("Coworker (Node)'s turn", thread="t2")
        self.assertEqual(self.pushes(), ["Coworker (3D): execute blender code",
                                         "Coworker (Node): execute blender code"], "no end steps on top")
        self.assertTrue(cw.can_revert("t1") and cw.can_revert("t2"))

    def test_a_change_after_the_last_step_gets_the_end_step(self):
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        self.write("t1", step="Coworker (3D): execute blender code")
        self.write("t1")                       # alone again: no step of its own
        cw.turn_finished("Coworker (3D)'s turn", thread="t1")
        self.assertEqual(self.pushes(), ["Coworker (3D): execute blender code", "Coworker (3D)'s turn"])
        names = self.stack()
        self.assertEqual(cw.revert_turn("t1"), "")
        self.assertEqual(self.calls[-1], len(names) - 3, "both of the turn's steps")

    def test_revert_both_goes_back_past_the_other_chats_steps(self):
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        self.write("t1", step="Coworker (3D): execute blender code")
        cw.turn_started("Coworker (Node)'s turn", thread="t2")
        self.write("t2", step="Coworker (Node): execute blender code")
        cw.turn_finished("Coworker (3D)'s turn", thread="t1")
        cw.turn_finished("Coworker (Node)'s turn", thread="t2")
        self.assertEqual(cw.revert_choice("t1").kind, "choose", "never a silent partial revert")
        self.assertIn("Revert Both", cw.revert_turn("t1"))
        names = self.stack()
        self.assertEqual(cw.revert_turn("t1", both=True), "")
        self.assertEqual(self.calls[-1], len(names) - 3, "back to the step under the 3D turn's first")
        self.assertEqual(cw.reverted_with, ["t2"])
        self.assertFalse(cw.can_revert("t2"), "its steps went too")

    def test_revert_both_refused_without_the_undo_list(self):
        cw.turn_started("Coworker (3D)'s turn", thread="t1")
        self.write("t1")
        self.write("t2", step="Coworker (Node): execute blender code")
        cw.turn_finished("Coworker (3D)'s turn", thread="t1")
        before = list(self.calls)
        self.assertIn("Undo History", cw.revert_turn("t1", both=True))
        self.assertEqual(self.calls, before)


class TestToolsSkipTheirStep(unittest.TestCase):
    """build_node_tree_from_code / wire_node_group / the VSE tools: no own step from a chat turn."""

    def setUp(self):
        self.owns = False
        fake = types.ModuleType("bl_ext.user_default.bfa_coworker.coworker_api")
        fake.turn_owns_undo = lambda: self.owns
        sys.modules[fake.__name__] = fake
        self.addCleanup(sys.modules.pop, fake.__name__, None)

    def _toolcode(self, tool):
        sys.path.insert(0, str(_ROOT / "mcp"))
        self.addCleanup(sys.path.remove, str(_ROOT / "mcp"))
        from blmcp.tools_helpers import toolcode_load_from_filepath
        source = toolcode_load_from_filepath(str(_ROOT / "mcp" / "blmcp" / "tools" / (tool + ".py")))
        namespace = {"__name__": "toolcode"}
        exec(compile(source, tool, "exec"), namespace)  # pylint: disable=exec-used
        return namespace

    @staticmethod
    def _bpy(pushed):
        window = object()
        ctx = types.SimpleNamespace(window=window, window_manager=types.SimpleNamespace(windows=[window]),
                                    temp_override=lambda **kw: __import__("contextlib").nullcontext())
        return types.SimpleNamespace(context=ctx, ops=types.SimpleNamespace(ed=types.SimpleNamespace(
            undo_push=lambda message="": pushed.append(message))))

    def test_build_node_tree(self):
        ns = self._toolcode("build_node_tree_from_code")
        pushed = []
        self.assertTrue(ns["_undo_push"](self._bpy(pushed), "Build node tree 'A'"))
        self.owns = True
        self.assertFalse(ns["_undo_push"](self._bpy(pushed), "Build node tree 'B'"))
        self.assertEqual(pushed, ["Build node tree 'A'"])

    def test_wire_node_group_and_vse(self):
        self.assertFalse(self._toolcode("wire_node_group")["_in_coworker_turn"]())
        self.owns = True
        self.assertTrue(self._toolcode("wire_node_group")["_in_coworker_turn"]())
        pushed = []
        ns = self._toolcode("set_strip_frame_range")
        sys.modules["bpy"] = self._bpy(pushed)
        self.addCleanup(sys.modules.pop, "bpy", None)
        self.assertFalse(ns["_vse_undo_push"]("Coworker: moved strip 'Title'"))
        self.owns = False
        self.assertTrue(ns["_vse_undo_push"]("Coworker: moved strip 'Title'"))
        self.assertEqual(pushed, ["Coworker: moved strip 'Title'"])


if __name__ == "__main__":
    unittest.main()
