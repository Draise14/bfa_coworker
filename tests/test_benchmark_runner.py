# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the benchmark-runner options in operators_agent.py.

Two runner behaviours are pinned here:

1. Refusal steps -- ``_REFUSAL_STEPS`` marks the error_handling prompts whose
   correct reply is to ask or decline; those steps must not be force-nudged
   into acting.
2. Reset-on-failure -- ``_reset_scene_to_before`` deletes the objects a FAILED
   step left behind so they do not corrupt the next step.  It runs against a
   fake ``bpy`` so it is testable outside Blender.
"""

__all__ = ()

import os
import types
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OA_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "operators_agent.py")


def _load_source() -> str:
    with open(_OA_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _load_refusal_steps() -> set:
    """Exec the ``_REFUSAL_STEPS`` set literal from the source."""
    src = _load_source()
    start = src.index("_REFUSAL_STEPS: set")
    end = src.index("\n}", start) + 2
    ns: dict = {}
    exec(compile(src[start:end], _OA_PATH, "exec"), ns)  # noqa: S102
    return ns["_REFUSAL_STEPS"]


class _FakeBlock:
    def __init__(self, users: int = 0) -> None:
        self.users = users


class _FakeObjects(list):
    def remove(self, obj, do_unlink: bool = False) -> None:  # noqa: ARG002
        super().remove(obj)


class _FakeOpsObject:
    def mode_set(self, mode: str = "OBJECT") -> None:
        pass


class _FakeOps:
    def __init__(self) -> None:
        self.object = _FakeOpsObject()


class _FakeContext:
    mode = "OBJECT"


class _FakeBpy:
    def __init__(self, objects) -> None:
        self.data = types.SimpleNamespace(
            objects=objects,
            meshes=[_FakeBlock(0), _FakeBlock(2)],
            materials=[],
            curves=[],
            lights=[],
            cameras=[],
            images=[],
            node_groups=[],
            collections=[],
        )
        self.context = _FakeContext()
        self.ops = _FakeOps()


def _load_reset_func(bpy_mod):
    src = _load_source()
    start = src.index("\ndef _reset_scene_to_before(") + 1
    end = src.index("\ndef _run_test_step(", start)
    mod = types.ModuleType("_oa_reset")
    mod.__dict__["bpy"] = bpy_mod
    mod.__dict__["__name__"] = "_oa_reset"
    exec(compile(src[start:end], _OA_PATH, "exec"), mod.__dict__)  # noqa: S102
    return mod._reset_scene_to_before


class TestRefusalSteps(unittest.TestCase):
    """The error_handling prompts must be marked refusal-expected."""

    def test_error_handling_steps_marked(self):
        marked = _load_refusal_steps()
        self.assertIn(("error_handling", 1), marked)
        self.assertIn(("error_handling", 2), marked)
        self.assertIn(("error_handling", 3), marked)

    def test_unrelated_steps_not_marked(self):
        marked = _load_refusal_steps()
        for key in (
            ("scene_build", 1), ("animation", 1), ("baseline", 1),
            ("vision_camera", 2),
        ):
            self.assertNotIn(key, marked)


class TestResetSceneToBefore(unittest.TestCase):
    """A failed step's newly-created objects are removed."""

    def test_removes_only_new_objects(self):
        keep = object()
        existing = types.SimpleNamespace(name="Ground")
        added = types.SimpleNamespace(name="Rock")
        objs = _FakeObjects([existing, added])
        bpy_mod = _FakeBpy(objs)
        reset = _load_reset_func(bpy_mod)

        reset({"Ground"})

        names = [o.name for o in objs]
        self.assertIn("Ground", names)
        self.assertNotIn("Rock", names)

    def test_purges_orphan_datablocks(self):
        objs = _FakeObjects()
        bpy_mod = _FakeBpy(objs)
        reset = _load_reset_func(bpy_mod)

        reset(set())

        # The zero-user mesh is purged; the used one (users=2) is kept.
        self.assertEqual(len(bpy_mod.data.meshes), 1)
        self.assertEqual(bpy_mod.data.meshes[0].users, 2)

    def test_no_before_set_is_safe(self):
        existing = types.SimpleNamespace(name="Ground")
        objs = _FakeObjects([existing])
        bpy_mod = _FakeBpy(objs)
        reset = _load_reset_func(bpy_mod)
        # Empty "before" means everything is considered new -- must not raise.
        reset(set())
        self.assertEqual(len(objs), 0)


if __name__ == "__main__":
    unittest.main()
