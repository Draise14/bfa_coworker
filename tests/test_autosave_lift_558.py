# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The autosave the GPU chat writes during a turn drops the co-work lock (#558).

Blender's autosave runs no save handlers, so the lift that ``save_pre`` /
``save_post`` do for a real save (#548) never ran for it: an autosave written
while a turn held the lock stored the agent's objects as unselectable.

1. While ``wm.save_auto_save`` runs, the locked objects and collections have
   their own ``hide_select`` back (an object that was unselectable on its own
   stays so); after it the lock is on again.
2. A failed autosave puts the lock back too, and is still reported, not raised.
3. With no turn running nothing is touched.

Stand-in ``bpy``; run with::

    python -m unittest tests.test_autosave_lift_558 -v
"""

__all__ = ()

import contextlib
import importlib
import io
import json
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_autosave_lift_558_pkg"


class _Block:
    def __init__(self, hide_select=False):
        self.hide_select = hide_select


class _Data:
    def __init__(self, objects, collections):
        self.is_dirty = True
        self.objects = {n: _Block(v) for n, v in objects.items()}
        self.collections = {n: _Block(v) for n, v in collections.items()}

    def values(self):
        return ({n: b.hide_select for n, b in self.objects.items()},
                {n: b.hide_select for n, b in self.collections.items()})


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Writer:
    """``bpy.ops.wm.save_auto_save``: what the file would store; *fail* raises like Blender."""

    def __init__(self, data):
        self.data, self.saw, self.fail = data, [], False

    def __call__(self):
        self.saw.append(self.data.values())
        if self.fail:
            raise RuntimeError("Error: cannot write the autosave")
        return {"FINISHED"}


def _fake_bpy(data, writer):
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(Operator=object)
    bpy.props = types.SimpleNamespace(StringProperty=lambda **_kw: None)
    bpy.app = types.SimpleNamespace(
        timers=types.SimpleNamespace(register=lambda *_a, **_kw: None, is_registered=lambda _fn: True,
                                     unregister=lambda _fn: None),
        handlers=types.SimpleNamespace(persistent=lambda fn: fn, load_pre=[], save_post=[]))
    windows = [types.SimpleNamespace(modal_operators=[types.SimpleNamespace(bl_idname="BFACW_OT_gpu_input")])]
    bpy.context = types.SimpleNamespace(
        window_manager=types.SimpleNamespace(windows=windows), mode="OBJECT",
        preferences=types.SimpleNamespace(
            filepaths=types.SimpleNamespace(use_auto_save_temporary_files=True, auto_save_time=2),
            edit=types.SimpleNamespace(use_global_undo=True)),
        temp_override=lambda **_kw: _NullCtx())
    bpy.ops = types.SimpleNamespace(wm=types.SimpleNamespace(save_auto_save=writer),
                                    bfacw=types.SimpleNamespace(gpu_input=lambda _mode: None))
    bpy.data = data
    return bpy


def _load(bpy):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    saved = sys.modules.get("bpy")
    sys.modules["bpy"] = bpy
    try:
        sys.modules.pop(_PKG + ".ui_gpu.input_capture", None)
        return importlib.import_module(_PKG + ".ui_gpu.input_capture")
    finally:
        if saved is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = saved


def _guard():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + ".co_work_guard")


cg = _guard()


def _run(call, data):
    """Run generated lock code ``(code, data)`` against *data* (a fake bpy.data)."""
    code, args = call
    ns = {"bpy": types.SimpleNamespace(data=data), "data": json.loads(json.dumps(args))}
    exec(code.replace("import bpy\n", ""), ns)  # noqa: S102 (test-controlled code)
    return ns["result"]


def _lock(data, objects=(), collections=()):
    """What a turn step does: register, run the lock code, record the priors."""
    new_o, new_c = cg.record_managed(objects, collections)
    result = _run(cg.build_lock_code(new_o, new_c), data)
    cg.record_prior(result["prior_hide_select"], result["prior_hide_select_coll"])


_OWN = ({"Agent": False, "AgentOwnLocked": True, "User": False}, {"AgentColl": False})
_LOCKED = ({"Agent": True, "AgentOwnLocked": True, "User": False}, {"AgentColl": True})


class TestAutosaveDropsTheLock(unittest.TestCase):

    def setUp(self):
        cg.clear()
        self.addCleanup(cg.clear)
        self.data = _Data(*_OWN)
        self.writer = _Writer(self.data)
        self.ic = _load(_fake_bpy(self.data, self.writer))
        self.ic._AUTOSAVE.update(since=0.0)     # an autosave is due

    def test_an_autosave_during_a_turn_stores_the_own_values(self):
        _lock(self.data, ("Agent", "AgentOwnLocked"), ("AgentColl",))
        self.assertEqual(self.data.values(), _LOCKED)
        self.ic._autosave_tick()
        self.assertEqual(self.writer.saw, [_OWN])
        self.assertEqual(self.data.values(), _LOCKED, "the lock wasn't put back after the autosave")

    def test_a_failed_autosave_puts_the_lock_back(self):
        _lock(self.data, ("Agent", "AgentOwnLocked"), ("AgentColl",))
        self.writer.fail = True
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.ic._autosave_tick()
        self.assertIn("autosave failed", out.getvalue())
        self.assertEqual(self.writer.saw, [_OWN])
        self.assertEqual(self.data.values(), _LOCKED)

    def test_no_turn_no_change(self):
        self.ic._autosave_tick()
        self.assertEqual(self.writer.saw, [_OWN])
        self.assertEqual(self.data.values(), _OWN)


if __name__ == "__main__":
    unittest.main()
