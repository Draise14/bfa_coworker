# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the co-work lock while Coworker's own calls run (#548).

``hide_select`` keeps the user from picking the agent's objects, but it also
makes ``select_set()`` a no-op and selection operators (``modifier_apply``)
end CANCELLED for the agent itself.  So the lock is lifted while a call runs
on Blender's main thread and put back before it returns.

1. Bookkeeping (``co_work_guard``): only locked names whose own value is known
   and False are lifted; relock puts back exactly those; a name released
   meanwhile stays released; nothing is lifted while the unlock is on its way;
   the objects' own values come back exactly at the end (also after an error
   inside a lifted call and when the lock code itself runs lifted).  While the
   unlock is on its way only the sandbox holds back (an undo step pushed then
   still gets the own values).
2. The bridge's sandbox (``weak_sandbox``): it lifts around the code it runs
   and puts the lock back also when the code raises; off the main thread it
   doesn't touch the scene.
3. An operator that ends ``{'CANCELLED'}`` in the code is said in the call's
   output (once per operator), with the reason when the active object is the
   cause; other results say nothing, and the operator's own attributes still
   read through.
4. An undo step Coworker pushes is taken with the lock lifted.

Run with::

    python -m unittest tests.test_co_work_lift_548 -v
"""

__all__ = ()

import contextlib
import importlib
import io
import json
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_lift_548_pkg"


def _load(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


cg = _load("co_work_guard")


class _Block:
    def __init__(self, hide_select=False):
        self.hide_select = hide_select
        self.writes = 0

    def __setattr__(self, key, value):
        if key == "hide_select" and "hide_select" in self.__dict__:
            self.__dict__["writes"] = self.__dict__.get("writes", 0) + 1
        super().__setattr__(key, value)


class _Data:
    def __init__(self, objects=None, collections=None):
        self.objects = {n: _Block(v) for n, v in (objects or {}).items()}
        self.collections = {n: _Block(v) for n, v in (collections or {}).items()}

    def values(self):
        return ({n: b.hide_select for n, b in self.objects.items()},
                {n: b.hide_select for n, b in self.collections.items()})


def _run(call, data):
    """Run generated lock / unlock code ``(code, data)`` against *data* (a fake bpy.data)."""
    code, args = call
    ns = {"bpy": types.SimpleNamespace(data=data), "data": json.loads(json.dumps(args))}
    exec(code.replace("import bpy\n", ""), ns)  # noqa: S102 (test-controlled code)
    return ns["result"]


def _lock(data, objects=(), collections=()):
    """What a turn step does: register, run the lock code, record the priors."""
    new_o, new_c = cg.record_managed(objects, collections)
    result = _run(cg.build_lock_code(new_o, new_c), data)
    cg.record_prior(result["prior_hide_select"], result["prior_hide_select_coll"])


def _release(data):
    with cg.releasing():
        result = _run(cg.build_unlock_code(), data)
    if result["status"] == "ok":
        cg.clear()


class TestLiftBookkeeping(unittest.TestCase):

    def setUp(self):
        cg.clear()
        self.data = _Data({"A": False, "B": True, "User": False}, {"Coll": False, "OwnColl": True})
        _lock(self.data, ("A", "B"), ("Coll", "OwnColl"))

    def tearDown(self):
        cg.clear()

    def test_lift_gives_back_the_own_values_and_relock_locks_again(self):
        self.assertEqual(self.data.values(), ({"A": True, "B": True, "User": False}, {"Coll": True, "OwnColl": True}))
        held = cg.lift(self.data)
        self.assertEqual(sorted(held), [("collections", "Coll"), ("objects", "A")])
        self.assertEqual(self.data.values(), ({"A": False, "B": True, "User": False},
                                              {"Coll": False, "OwnColl": True}))
        cg.relock(self.data, held)
        self.assertEqual(self.data.values(), ({"A": True, "B": True, "User": False}, {"Coll": True, "OwnColl": True}))
        self.assertEqual(self.data.objects["B"].writes, 1)          # its own True: never touched by the lift

    def test_while_the_unlock_is_on_its_way_an_undo_step_still_gets_the_own_values(self):
        # The turn's undo step is pushed by the UI timer, often after the worker started the
        # release but before the unlock code ran (seen live): it must not hold the lock.
        with cg.releasing():
            self.assertTrue(cg.is_releasing())
            with cg.lifted(self.data):
                self.assertFalse(self.data.objects["A"].hide_select)
            self.assertTrue(self.data.objects["A"].hide_select)
        self.assertFalse(cg.is_releasing())

    def test_nothing_to_lift_when_unlocked(self):
        _release(self.data)
        self.assertEqual(cg.lift(self.data), [])
        self.assertEqual(self.data.values(), ({"A": False, "B": True, "User": False},
                                              {"Coll": False, "OwnColl": True}))

    def test_a_name_whose_prior_isnt_known_yet_is_left_alone(self):
        cg.record_managed(("User",), ())                          # the lock code hasn't reported yet
        self.data.objects["User"].hide_select = True               # say the user's own value is True
        held = cg.lift(self.data)
        self.assertNotIn(("objects", "User"), held)
        self.assertTrue(self.data.objects["User"].hide_select)
        cg.relock(self.data, held)

    def test_relock_skips_what_was_released_or_removed_meanwhile(self):
        held = cg.lift(self.data)
        del self.data.objects["A"]                                 # the agent's code removed it
        cg.relock(self.data, held)
        self.assertTrue(self.data.collections["Coll"].hide_select)
        held = cg.lift(self.data)
        cg.clear()                                                 # released meanwhile
        cg.relock(self.data, held)
        self.assertFalse(self.data.collections["Coll"].hide_select)

    def test_lifted_context_relocks_after_an_error(self):
        with self.assertRaises(RuntimeError):
            with cg.lifted(self.data):
                self.assertFalse(self.data.objects["A"].hide_select)
                raise RuntimeError("the code failed")
        self.assertTrue(self.data.objects["A"].hide_select)

    def test_lock_code_run_lifted_keeps_every_prior(self):
        # The next step's lock runs through the bridge too: lifted around it.
        self.data.objects["New"] = _Block(True)
        self.data.objects["New2"] = _Block(False)
        with cg.lifted(self.data):
            _lock(self.data, ("A", "New", "New2"), ())
        self.assertEqual(self.data.values()[0], {"A": True, "B": True, "User": False, "New": True, "New2": True})
        _release(self.data)
        self.assertEqual(self.data.values(), ({"A": False, "B": True, "User": False, "New": True, "New2": False},
                                              {"Coll": False, "OwnColl": True}))

    def test_many_calls_then_release_restore_exactly(self):
        for _ in range(5):
            with cg.lifted(self.data):
                self.data.objects["A"].hide_select = False
        _release(self.data)
        self.assertEqual(self.data.values(), ({"A": False, "B": True, "User": False},
                                              {"Coll": False, "OwnColl": True}))


# -- the bridge's sandbox ------------------------------------------------------------------------------
class _FakeOp:
    """A stand-in for Blender's BPyOpsCallable."""

    def __init__(self, module, func, answer):
        self.module, self.func, self.answer = module, func, answer
        self.calls = []
        self.__doc__ = "bpy.ops.{:s}.{:s}(modifier='')\nApply modifier".format(module, func)

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.answer

    def poll(self):
        return True

    def idname(self):
        return "{:s}_OT_{:s}".format(self.module.upper(), self.func)

    def __repr__(self):
        return "# Call\nbpy.ops.{:s}.{:s}(modifier='')".format(self.module, self.func)


class _Sandbox(unittest.TestCase):

    def setUp(self):
        cg.clear()
        self.answers = {"object.modifier_apply": {"CANCELLED"}, "object.select_all": {"FINISHED"}}
        self.made = {}

        def create(module, func):
            op = _FakeOp(module, func, self.answers.get(module + "." + func, {"FINISHED"}))
            self.made[module + "." + func] = op
            return op
        self.ops_mod = types.ModuleType("bpy.ops")
        self.ops_mod._op_create_function = create
        self.active = types.SimpleNamespace(name="Cube", hide_select=False, visible_get=lambda: True,
                                            select_get=lambda: True)
        self.data = _Data({"A": False}, {})
        bpy = types.ModuleType("bpy")
        bpy.ops = self.ops_mod
        bpy.data = self.data
        bpy.context = types.SimpleNamespace(view_layer=types.SimpleNamespace(
            objects=types.SimpleNamespace(active=self.active)))
        self.patch = mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.ops": self.ops_mod})
        self.patch.start()
        self.sandbox = _load("weak_sandbox")

    def tearDown(self):
        self.patch.stop()
        cg.clear()

    def op(self, module, func):
        """``bpy.ops.<module>.<func>`` the way bpy.ops looks it up: through _op_create_function."""
        return self.ops_mod._op_create_function(module, func)


class TestSandboxLifts(_Sandbox):

    def test_lifts_while_the_code_runs_and_locks_again_after(self):
        _lock(self.data, ("A",))
        with self.sandbox.WeakSandboxForLLM():
            self.assertFalse(self.data.objects["A"].hide_select)
        self.assertTrue(self.data.objects["A"].hide_select)

    def test_locks_again_when_the_code_raises(self):
        _lock(self.data, ("A",))
        with self.assertRaises(ValueError):
            with self.sandbox.WeakSandboxForLLM():
                raise ValueError("the model's code failed")
        self.assertTrue(self.data.objects["A"].hide_select)

    def test_the_unlock_run_through_the_sandbox_isnt_undone(self):
        # The unlock code runs inside the sandbox as well; while it is on its way the sandbox
        # lifts nothing, so it has nothing to lock again when the code returns.
        _lock(self.data, ("A",))
        with cg.releasing():
            with self.sandbox.WeakSandboxForLLM():
                self.assertTrue(self.data.objects["A"].hide_select)
                result = _run(cg.build_unlock_code(), self.data)
        self.assertEqual(result["status"], "ok")
        cg.clear()
        self.assertFalse(self.data.objects["A"].hide_select)

    def test_off_the_main_thread_the_scene_isnt_touched(self):
        _lock(self.data, ("A",))
        seen = []

        def run():
            with self.sandbox.WeakSandboxForLLM():
                seen.append(self.data.objects["A"].hide_select)
        t = threading.Thread(target=run)
        t.start()
        t.join()
        self.assertEqual(seen, [True])

    def test_unlocked_scene_is_left_alone(self):
        with self.sandbox.WeakSandboxForLLM():
            pass
        self.assertEqual(self.data.objects["A"].writes, 0)


class TestCancelledIsSaid(_Sandbox):

    def _run(self, calls):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.sandbox.WeakSandboxForLLM():
            results = [self.op(m, f)(*a) for m, f, a in calls]
        return results, out.getvalue()

    def test_a_cancelled_operator_is_said_once_with_the_reason(self):
        self.active.hide_select = True
        results, said = self._run([("object", "modifier_apply", ()), ("object", "modifier_apply", ()),
                                   ("object", "select_all", ())])
        self.assertEqual(results, [{"CANCELLED"}, {"CANCELLED"}, {"FINISHED"}])
        self.assertEqual(said.count("CANCELLED"), 1)
        self.assertIn("bpy.ops.object.modifier_apply", said)
        self.assertIn("did nothing", said)
        self.assertIn("'Cube'", said)
        self.assertIn("hide_select", said)
        self.assertNotIn("select_all", said)

    def test_no_active_object_and_a_fitting_context(self):
        self.active = None
        sys.modules["bpy"].context.view_layer.objects.active = None
        _r, said = self._run([("object", "modifier_apply", ())])
        self.assertIn("no active object", said)
        sys.modules["bpy"].context.view_layer.objects.active = types.SimpleNamespace(
            name="Cube", hide_select=False, visible_get=lambda: True, select_get=lambda: True)
        _r, said = self._run([("mesh", "fake_op", ())])
        self.assertEqual(said, "")
        self.answers["sequencer.split"] = {"CANCELLED"}
        _r, said = self._run([("sequencer", "split", ())])
        self.assertIn("bpy.ops.sequencer.split", said)
        self.assertNotIn("active object", said)

    def test_finished_and_other_results_say_nothing(self):
        self.answers["view3d.modal"] = {"RUNNING_MODAL"}
        _r, said = self._run([("object", "select_all", ()), ("view3d", "modal", ())])
        self.assertEqual(said, "")

    def test_the_operator_still_reads_like_itself(self):
        with contextlib.redirect_stdout(io.StringIO()), self.sandbox.WeakSandboxForLLM():
            op = self.op("object", "modifier_apply")
            self.assertTrue(op.poll())
            self.assertEqual(op.idname(), "OBJECT_OT_modifier_apply")
            self.assertIn("Apply modifier", op.__doc__)
            self.assertIn("bpy.ops.object.modifier_apply", repr(op))
            self.assertEqual(op(modifier="Bevel"), {"CANCELLED"})
        self.assertEqual(self.made["object.modifier_apply"].calls[-1], ((), {"modifier": "Bevel"}))

    def test_blocked_operators_stay_blocked(self):
        with self.sandbox.WeakSandboxForLLM():
            with self.assertRaises(RuntimeError):
                self.op("wm", "quit_blender")()

    def test_after_the_sandbox_operators_are_blenders_own(self):
        with self.sandbox.WeakSandboxForLLM():
            pass
        self.assertIsInstance(self.op("object", "modifier_apply"), _FakeOp)


class TestUndoStepTakenLifted(unittest.TestCase):

    def test_push_lifts_around_undo_push(self):
        cg.clear()
        data = _Data({"A": False})
        _lock(data, ("A",))
        seen = []
        ops = types.SimpleNamespace(ed=types.SimpleNamespace(
            undo_push=lambda message="": seen.append((message, data.objects["A"].hide_select)) or {"FINISHED"}))
        window = object()
        bpy = types.ModuleType("bpy")
        bpy.ops, bpy.data = ops, data
        bpy.context = types.SimpleNamespace(window=window, window_manager=types.SimpleNamespace(windows=[window]),
                                            temp_override=lambda **kw: contextlib.nullcontext())
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            api = _load("coworker_api")
            self.assertTrue(api._undo("Coworker: a turn"))
        self.assertEqual(seen, [("Coworker: a turn", False)])
        self.assertTrue(data.objects["A"].hide_select)
        cg.clear()

    def test_a_file_saved_while_a_turn_runs_keeps_the_own_values(self):
        cg.clear()
        data = _Data({"A": False, "B": True})
        _lock(data, ("A", "B"))
        bpy = types.ModuleType("bpy")
        bpy.data = data
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            api = _load("coworker_api")
            api._on_save_pre("x.blend")
            written = {n: b.hide_select for n, b in data.objects.items()}
            api._on_save_post("x.blend")
        self.assertEqual(written, {"A": False, "B": True})
        self.assertEqual({n: b.hide_select for n, b in data.objects.items()}, {"A": True, "B": True})
        cg.clear()


if __name__ == "__main__":
    unittest.main()
