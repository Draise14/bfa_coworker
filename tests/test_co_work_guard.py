# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the co-work scene lock (scene safety Phase 1).

While a turn runs on a background thread the user stays interactive and can
re-select objects the agent is working on -- the root cause of the
``Operator ...poll() failed, context is incorrect`` errors.  The lock sets
``hide_select`` on the coworker's own objects/collections for the duration of
the turn and restores the exact prior value when the turn ends.

The module is bpy-free (it emits toolcode strings + manages plain dicts), so
these tests execute the generated code against a fake ``bpy`` and prove the
lock -> unlock round-trip restores values, plus the registry transitions.

Run with::

    python -m unittest tests.test_co_work_guard -v
"""

__all__ = ()

import importlib.util
import os
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CW_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "co_work_guard.py")


def _load_guard():
    spec = importlib.util.spec_from_file_location("co_work_guard_test", _CW_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


_cw = _load_guard()


class _Datablock:
    def __init__(self, hide_select: bool = False) -> None:
        self.hide_select = hide_select


class _FakeData:
    def __init__(self) -> None:
        self.objects: dict = {}
        self.collections: dict = {}


class _FakeBPY:
    """Minimal bpy stand-in exposing ``data.objects`` / ``data.collections``."""

    def __init__(self, objects=None, collections=None) -> None:
        self.data = _FakeData()
        for name, hide in (objects or {}).items():
            self.data.objects[name] = _Datablock(hide)
        for name, hide in (collections or {}).items():
            self.data.collections[name] = _Datablock(hide)


def _run(code: str, bpy_mod) -> dict:
    """Execute generated toolcode (stripping its ``import bpy``) and return result."""
    ns = {"bpy": bpy_mod}
    exec(code.replace("import bpy\n", ""), ns)  # noqa: S102 (test-controlled code)
    return ns["result"]


class TestRegistry(unittest.TestCase):
    def setUp(self) -> None:
        _cw.clear()

    def tearDown(self) -> None:
        _cw.clear()

    def test_starts_unlocked(self):
        self.assertFalse(_cw.is_locked())
        self.assertEqual(_cw.managed_names(), (set(), set()))

    def test_record_managed_locks_and_names(self):
        _cw.record_managed({"Cube", "Sphere"}, {"Props"})
        self.assertTrue(_cw.is_locked())
        objs, colls = _cw.managed_names()
        self.assertEqual(objs, {"Cube", "Sphere"})
        self.assertEqual(colls, {"Props"})

    def test_record_managed_is_idempotent(self):
        _cw.record_managed({"Cube"}, set())
        _cw.record_managed({"Cube"}, set())
        self.assertEqual(_cw.managed_names()[0], {"Cube"})

    def test_clear_unlocks(self):
        _cw.record_managed({"Cube"}, set())
        _cw.clear()
        self.assertFalse(_cw.is_locked())


class TestSessionMemory(unittest.TestCase):
    """The session set survives clear() so the next turn can re-lock it."""

    def setUp(self) -> None:
        _cw.clear()
        _cw.clear_session()

    def tearDown(self) -> None:
        _cw.clear()
        _cw.clear_session()

    def test_remember_session_persists_across_clear(self):
        _cw.remember_session({"Ground"}, {"Props"})
        _cw.clear()
        objs, colls = _cw.session_names()
        self.assertEqual(objs, {"Ground"})
        self.assertEqual(colls, {"Props"})
        # clear() forgets the *managed* (priors) registry but not the session.
        self.assertEqual(_cw.managed_names(), (set(), set()))

    def test_remember_session_is_idempotent(self):
        _cw.remember_session({"Ground"}, set())
        _cw.remember_session({"Ground"}, set())
        self.assertEqual(_cw.session_names()[0], {"Ground"})

    def test_clear_session_forgets_everything(self):
        _cw.remember_session({"Ground", "Tree"}, {"Props"})
        _cw.clear_session()
        self.assertEqual(_cw.session_names(), (set(), set()))

    def test_ignores_empty_names(self):
        _cw.remember_session({"", "Ground", None}, {""})
        self.assertEqual(_cw.session_names()[0], {"Ground"})
        self.assertEqual(_cw.session_names()[1], set())


class TestLockUnlockRoundTrip(unittest.TestCase):
    """The generated code must lock then restore the exact prior values."""

    def setUp(self) -> None:
        _cw.clear()

    def tearDown(self) -> None:
        _cw.clear()

    def test_lock_sets_hide_select_and_records_prior(self):
        bpy = _FakeBPY(objects={"Cube": False, "Sphere": True},
                       collections={"Props": False})
        _cw.record_managed({"Cube", "Sphere"}, {"Props"})
        result = _run(_cw.build_lock_code({"Cube", "Sphere"}, {"Props"}), bpy)
        self.assertEqual(result["locked"], 3)
        self.assertTrue(bpy.data.objects["Cube"].hide_select)
        self.assertTrue(bpy.data.objects["Sphere"].hide_select)
        self.assertTrue(bpy.data.collections["Props"].hide_select)
        # Prior values captured faithfully (including a pre-hidden object).
        self.assertEqual(result["prior_hide_select"]["Cube"], False)
        self.assertEqual(result["prior_hide_select"]["Sphere"], True)

    def test_unlock_restores_prior_values(self):
        bpy = _FakeBPY(objects={"Cube": False, "Sphere": True},
                       collections={"Props": False})
        _cw.record_managed({"Cube", "Sphere"}, {"Props"})
        lock_result = _run(_cw.build_lock_code({"Cube", "Sphere"}, {"Props"}), bpy)
        _cw.record_prior(
            lock_result["prior_hide_select"],
            lock_result["prior_hide_select_coll"])
        unlock_result = _run(_cw.build_unlock_code(), bpy)
        self.assertEqual(unlock_result["unlocked"], 3)
        # Exact prior state restored -- Cube was selectable, Sphere was not.
        self.assertFalse(bpy.data.objects["Cube"].hide_select)
        self.assertTrue(bpy.data.objects["Sphere"].hide_select)
        self.assertFalse(bpy.data.collections["Props"].hide_select)

    def test_missing_datablocks_are_skipped(self):
        bpy = _FakeBPY(objects={"Cube": False}, collections={})
        _cw.record_managed({"Cube", "Gone"}, {"MissingColl"})
        result = _run(_cw.build_lock_code({"Cube", "Gone"}, {"MissingColl"}), bpy)
        self.assertEqual(result["locked"], 1)

    def test_codegen_carries_toolcode_marker(self):
        self.assertIn("# blmcp-toolcode-skip-preflight", _cw.build_lock_code({"A"}, set()))
        self.assertIn("# blmcp-toolcode-skip-preflight", _cw.build_unlock_code())


if __name__ == "__main__":
    unittest.main()
