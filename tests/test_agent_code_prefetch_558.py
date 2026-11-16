# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #558: code an agent writes itself (``execute_blender_code``) runs with the
Sequencer's prefetch stopped when it may remove or split strips.

``strips.remove()`` and ``Strip.split()`` free strips before they stop prefetch, which
crashed Blender (#536 fixed Coworker's own tools; this covers agent-written code).

1. The text match (``wand/prefetch.may_free_strips``): strip removal, split, scene removal,
   ``batch_remove`` and the Sequencer's delete / split / meta operators match; ordinary
   code without any of those does not.  Broad on purpose: ``list.remove`` / ``str.split``
   match too, which costs a no-op without a sequence editor.
2. ``stop_every_prefetch``: once per scene whose sequence editor has strips (the
   ``'COMPOSITE'`` invalidation), nothing for a scene without one or without strips, and a
   scene or strip that can't be asked is skipped.
3. The bridge (the real ``_execute_code``, a stand-in ``bpy``): prefetch is stopped before
   the code runs, once per call; Coworker's own tool-code and pure-Python code skip it; an
   error in the guard never keeps the code from running; the Prefetch setting is never
   written.

Run with::

    python -m unittest tests.test_agent_code_prefetch_558 -v
"""

__all__ = ()

import contextlib
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_558_pkg"
_MARK = "# blmcp-toolcode-skip-preflight\n"


def _load(*names):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return [importlib.import_module(_PKG + "." + n) for n in names]


P, bridge, sandbox = _load("wand.prefetch", "mcp_to_blender_server", "weak_sandbox")


# -- stand-ins ------------------------------------------------------------------------------

class _Strip:
    def __init__(self, name, log, fail=None):
        self.name, self.log, self.fail = name, log, fail

    def invalidate_cache(self, kind):
        if self.fail:
            raise self.fail
        self.log.append(("stop", self.name, kind))


class _Strips(list):
    """``ed.strips``: a list whose remove() records."""

    def __init__(self, items, log):
        super().__init__(items)
        self.log = log

    def remove(self, strip):
        self.log.append(("remove", strip.name))
        super().remove(strip)


class _Editor:
    """A sequence editor stand-in that records every write to ``use_prefetch``."""

    def __init__(self, strips):
        self.__dict__["strips"] = strips
        self.__dict__["writes"] = []

    use_prefetch = property(lambda self: True)

    def __setattr__(self, name, value):
        self.writes.append((name, value))


class _Scene:
    def __init__(self, name, log, n=0, editor=True):
        self.name = name
        strips = _Strips([_Strip("{:s}_{:d}".format(name, i), log) for i in range(n)], log)
        self.sequence_editor = _Editor(strips) if editor else None


class _GoneScene:
    """A scene removed under the guard: every attribute raises ReferenceError."""

    def __getattr__(self, name):
        raise ReferenceError("StructRNA of type Scene has been removed")


def _fake_bpy(scenes):
    bpy = types.ModuleType("bpy")
    bpy.data = types.SimpleNamespace(scenes=scenes, objects=[])
    bpy.context = types.SimpleNamespace(view_layer=None)
    return bpy


# -- 1. the text match -----------------------------------------------------------------------

class TestMatch(unittest.TestCase):

    def test_what_can_free_strips_matches(self):
        for code in (
            "ed.strips.remove(s)",
            "for s in old:\n    sc.sequence_editor.strips.remove(s)",
            "meta.strips.remove(inner)",
            "right = strip.split(frame=120, split_method='SOFT')",
            "strip.split (frame=10)",
            "ed.strips . remove (s)",
            "bpy.ops.sequencer.delete()",
            "bpy.ops.sequencer.split(frame=40, type='SOFT')",
            "bpy.ops.sequencer.split_multicam(camera=2)",
            "bpy.ops.sequencer.meta_make()",
            "bpy.ops.sequencer.meta_separate()",
            "bpy.data.scenes.remove(bpy.data.scenes['Edit'])",
            "bpy.data.batch_remove(ids=[s for s in bpy.data.scenes])",
            "ops = bpy.ops\nops.sequencer.delete()",
        ):
            self.assertTrue(P.may_free_strips(code), code)

    def test_ordinary_code_does_not_match(self):
        for code in (
            "",
            "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2)\nresult = {'ok': True}",
            "obj = bpy.data.objects.get('Cube')\nobj.location.x = 1.0",
            "bpy.ops.sequencer.select_all(action='SELECT')",
            "bpy.ops.sequencer.duplicate_move()",
            "ed.strips.new_effect(name='t', type='TEXT', channel=2, frame_start=1, length=10)",
            "removed = 3\nsplit_at = 12\nresult = {'removed': removed}",
            "def remove_all():\n    pass\nremove_all()",
            "text = 'remove( or split( in a string with no dot'",
            "bpy.ops.sequencer.deselect_all()",
        ):
            self.assertFalse(P.may_free_strips(code), code)

    def test_list_and_str_calls_match_on_purpose(self):
        # Cheap no-op without a sequence editor with strips (TestBridge.test_ordinary_code_*).
        for code in ("names.remove('Cube')", "words = line.split()", "bpy.data.objects.remove(o)"):
            self.assertTrue(P.may_free_strips(code), code)

    def test_module_needs_no_bpy(self):
        src = (_ADDON / "wand" / "prefetch.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?m)^\s*import bpy|^\s*from bpy")
        self.assertNotIn("use_prefetch =", src)


# -- 2. stop_every_prefetch ------------------------------------------------------------------

class TestStopEveryPrefetch(unittest.TestCase):

    def test_once_per_scene_with_strips(self):
        log = []
        scenes = [_Scene("Edit", log, 3), _Scene("Empty", log, 0), _Scene("Model", log, editor=False),
                  _Scene("B", log, 1)]
        self.assertEqual(P.stop_every_prefetch(scenes), 2)
        self.assertEqual(log, [("stop", "Edit_0", "COMPOSITE"), ("stop", "B_0", "COMPOSITE")])
        for sc in scenes:
            if sc.sequence_editor is not None:
                self.assertEqual(sc.sequence_editor.writes, [])      # Prefetch setting untouched

    def test_nothing_to_stop(self):
        log = []
        self.assertEqual(P.stop_every_prefetch([]), 0)
        self.assertEqual(P.stop_every_prefetch([_Scene("A", log, editor=False), _Scene("B", log, 0)]), 0)
        self.assertEqual(log, [])

    def test_a_scene_or_strip_that_cant_be_asked_is_skipped(self):
        log = []
        bad = _Scene("Bad", log, 1)
        bad.sequence_editor.strips[0].fail = ReferenceError("removed")
        scenes = [_GoneScene(), bad, _Scene("Good", log, 2)]
        self.assertEqual(P.stop_every_prefetch(scenes), 1)
        self.assertEqual(log, [("stop", "Good_0", "COMPOSITE")])

    def test_stop_prefetch_keeps_raw_for_its_callers(self):
        log = []
        self.assertTrue(P.stop_prefetch(_Strip("a", log)))
        self.assertTrue(P.stop_prefetch(_Strip("b", log), 'COMPOSITE'))
        self.assertEqual(log, [("stop", "a", "RAW"), ("stop", "b", "COMPOSITE")])


# -- 3. the bridge ---------------------------------------------------------------------------

_AGENT_REMOVES = (
    "import bpy\n"
    "ed = bpy.data.scenes[0].sequence_editor\n"
    "for s in list(ed.strips):\n"
    "    ed.strips.remove(s)\n"
    "result = {'left': len(ed.strips)}\n"
)


class TestBridge(unittest.TestCase):
    """The real ``_execute_code`` (bpy stand-in, the sandbox left out)."""

    def setUp(self):
        self.log = []
        self.scenes = [_Scene("Edit", self.log, 3), _Scene("Other", self.log, 1), _Scene("Model", self.log,
                                                                                          editor=False)]
        for patcher in (mock.patch.dict(sys.modules, {"bpy": _fake_bpy(self.scenes)}),
                        mock.patch.object(sandbox, "WeakSandboxForLLM", contextlib.nullcontext)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, code):
        result = bridge._execute_code(code, strict_json=False)
        self.assertEqual(result.response.get("status"), "ok", result.response)
        return result.response

    def test_prefetch_stopped_once_before_the_first_removal(self):
        response = self._run(_AGENT_REMOVES)
        self.assertEqual(response["result"], {"left": 0})
        self.assertEqual(self.log, [("stop", "Edit_0", "COMPOSITE"), ("stop", "Other_0", "COMPOSITE"),
                                    ("remove", "Edit_0"), ("remove", "Edit_1"), ("remove", "Edit_2")])
        for sc in self.scenes:
            if sc.sequence_editor is not None:
                self.assertEqual(sc.sequence_editor.writes, [])

    def test_once_per_call(self):
        self._run(_AGENT_REMOVES)
        self._run(_AGENT_REMOVES)               # "Edit" has no strips left: only "Other" is asked
        stops = [e[1] for e in self.log if e[0] == "stop"]
        self.assertEqual(stops, ["Edit_0", "Other_0", "Other_0"])

    def test_no_sequence_editor_is_a_no_op(self):
        self.scenes[:] = [_Scene("Model", self.log, editor=False), _Scene("Empty", self.log, 0)]
        self.assertEqual(bridge._stop_prefetch_for(_AGENT_REMOVES), 0)
        self._run("import bpy\nnames = ['a', 'b']\nnames.remove('a')\nresult = {'n': names}\n")
        self.assertEqual(self.log, [])

    def test_ordinary_code_stops_nothing(self):
        self._run("import bpy\nresult = {'n': len(bpy.data.scenes)}\n")
        self.assertEqual(self.log, [])

    def test_coworkers_own_tool_code_is_skipped(self):
        self._run(_MARK + _AGENT_REMOVES)
        self.assertEqual([e for e in self.log if e[0] == "stop"], [])
        self.assertEqual([e[0] for e in self.log], ["remove"] * 3)

    def test_pure_python_code_is_skipped(self):
        self._run("names = ['a', 'b']\nnames.remove('a')\nresult = {'n': names}\n")
        self.assertEqual(self.log, [])

    def test_an_error_in_the_guard_never_blocks_the_code(self):
        for where in ("may_free_strips", "stop_every_prefetch"):
            self.log.clear()
            with mock.patch.object(P, where, side_effect=RuntimeError("boom")):
                self.assertEqual(bridge._stop_prefetch_for(_AGENT_REMOVES), 0)
                for sc in self.scenes:          # fresh strips for the run
                    if sc.sequence_editor is not None and not sc.sequence_editor.strips:
                        sc.sequence_editor.strips.extend([_Strip(sc.name + "_x", self.log)])
                response = self._run(_AGENT_REMOVES)
            self.assertEqual(response["result"], {"left": 0}, where)
            self.assertEqual([e for e in self.log if e[0] == "stop"], [], where)

    def test_a_scene_list_that_raises_never_blocks_the_code(self):
        class Broken:
            def __iter__(self):
                raise RuntimeError("bpy.data is restricted")
        sys.modules["bpy"].data.scenes = Broken()
        self.assertEqual(bridge._stop_prefetch_for(_AGENT_REMOVES), 0)

    def test_the_guard_sits_right_before_the_inline_exec(self):
        src = (_ADDON / "mcp_to_blender_server.py").read_text(encoding="utf-8")
        body = src[src.index("def _execute_code("):]
        branch = body.index("if is_toolcode or _code_uses_bpy(code):")
        guard = body.index("_stop_prefetch_for(code)")
        self.assertLess(branch, guard)
        self.assertLess(guard, body.index("exec(code, namespace)", branch))
        self.assertIn("if not is_toolcode:", body[branch:guard])


if __name__ == "__main__":
    unittest.main()
