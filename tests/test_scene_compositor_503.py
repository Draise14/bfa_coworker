# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Blender 5.3+ deprecations in the tool-code (#503).

- ``_scene_compositor``: the scene's compositor tree through
  ``scene.compositor_effects`` (the active effect's node group) where it
  exists, ``scene.compositing_node_group`` otherwise; the deprecated property
  is never touched on a build that has the new one.
- Every tool-code that reads or sets the compositor tree gets the helper
  spliced in and runs without ``blmcp`` (as inside Blender).
- The node-code reader skips a setting Blender deprecated for an input socket
  the node has (``GeometryNodeMergeLayers.mode`` -> ``inputs["Mode"]``), and
  reads it as before where it is not deprecated (Blender 5.2).

Stand-ins for the RNA; does not require Blender.  Run with::

    python -m unittest tests.test_scene_compositor_503 -v
"""

__all__ = ()

import builtins
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "mcp"))

from blmcp.tools import _scene_compositor as sc  # noqa: E402  pylint: disable=wrong-import-position

# The tool-codes that read (or set) the scene's compositor tree.
_TOOLS = ("get_active_node_tree", "wire_node_group", "get_node_tree_as_code", "build_node_tree_from_code",
          "get_blendfile_summary_usage_guess", "load_asset_in_context")


class _Tree:
    def __init__(self, name):
        self.name = name


class _OldScene:
    """Blender 5.2: only ``compositing_node_group``."""

    def __init__(self, tree=None):
        self.compositing_node_group = tree


class _Effect:
    def __init__(self, name, node_group=None):
        self.name = name
        self.node_group = node_group


class _Effects(list):
    """``scene.compositor_effects``: ``active`` and ``new(name)`` (the new effect is the active one)."""

    def __init__(self, effects=(), active=None):
        super().__init__(effects)
        self.active = active

    def new(self, name, effect=None):
        del effect
        made = _Effect(name)
        self.append(made)
        self.active = made
        return made


class _NewScene:
    """Blender 5.3+ / Bforartists 5.2: the deprecated property must not be touched."""

    def __init__(self, effects):
        self.compositor_effects = effects

    def __getattr__(self, name):
        if name == "compositing_node_group":
            raise AssertionError("the deprecated Scene.compositing_node_group was used")
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name == "compositing_node_group":
            raise AssertionError("the deprecated Scene.compositing_node_group was set")
        object.__setattr__(self, name, value)


class SceneCompositorTree(unittest.TestCase):

    def test_old_api(self):
        tree = _Tree("Compositing Nodetree")
        self.assertIs(sc.scene_compositor_tree(_OldScene(tree)), tree)
        self.assertIsNone(sc.scene_compositor_tree(_OldScene()))
        scene = _OldScene()
        sc.set_scene_compositor_tree(scene, tree)
        self.assertIs(scene.compositing_node_group, tree)

    def test_new_api_reads_the_active_effect(self):
        first, second = _Tree("A"), _Tree("B")
        effects = _Effects([_Effect("Scene Effect", first), _Effect("Second", second)])
        effects.active = effects[1]
        self.assertIs(sc.scene_compositor_tree(_NewScene(effects)), second)

    def test_new_api_without_an_effect_or_a_group(self):
        self.assertIsNone(sc.scene_compositor_tree(_NewScene(_Effects())))
        effects = _Effects([_Effect("Empty")])
        effects.active = effects[0]
        self.assertIsNone(sc.scene_compositor_tree(_NewScene(effects)))
        self.assertEqual(len(effects), 1, "reading creates nothing")

    def test_an_empty_effect_list_is_not_mistaken_for_the_old_api(self):
        """An empty bpy collection is falsy: the helper tests for None, not truth."""
        scene = _NewScene(_Effects())
        sc.set_scene_compositor_tree(scene, _Tree("T"))
        self.assertEqual([e.name for e in scene.compositor_effects], ["Scene Effect"])

    def test_set_makes_a_scene_effect_when_there_is_none(self):
        tree = _Tree("Compositing Nodetree")
        scene = _NewScene(_Effects())
        sc.set_scene_compositor_tree(scene, tree)
        self.assertEqual([(e.name, e.node_group) for e in scene.compositor_effects], [("Scene Effect", tree)])
        self.assertIs(sc.scene_compositor_tree(scene), tree)

    def test_set_uses_the_active_effect(self):
        tree = _Tree("New")
        effects = _Effects([_Effect("Mine", _Tree("Old"))])
        effects.active = effects[0]
        scene = _NewScene(effects)
        sc.set_scene_compositor_tree(scene, tree)
        self.assertEqual(len(effects), 1)
        self.assertIs(effects[0].node_group, tree)


class SplicedIntoEveryTool(unittest.TestCase):

    def test_every_tool_gets_the_helper_and_needs_no_blmcp(self):
        from blmcp.tools_helpers import toolcode_load_from_filepath
        real = builtins.__import__

        def no_blmcp(name, *args, **kwargs):
            if name.startswith("blmcp"):
                raise ImportError("blmcp is not inside Blender")
            return real(name, *args, **kwargs)

        helper = (_ROOT / "mcp" / "blmcp" / "tools" / "_scene_compositor.py").read_text(encoding="utf-8")
        for tool in _TOOLS:
            with self.subTest(tool=tool):
                source = toolcode_load_from_filepath(str(_ROOT / "mcp" / "blmcp" / "tools" / (tool + ".py")))
                self.assertIn("def scene_compositor_tree(", source)
                # The deprecated property only in the helper's fallback (for Blender 5.2).
                self.assertEqual(source.count("compositing_node_group"), helper.count("compositing_node_group"))
                namespace = {"__name__": "toolcode"}
                builtins.__import__ = no_blmcp
                try:
                    exec(compile(source, tool, "exec"), namespace)  # pylint: disable=exec-used
                finally:
                    builtins.__import__ = real
                self.assertTrue(callable(namespace["main"]))
                self.assertTrue(callable(namespace["scene_compositor_tree"]))


# -- The node-code reader and deprecated settings ------------------------------


class _Prop:
    def __init__(self, identifier, type="ENUM", default="", is_deprecated=False, deprecated_note=""):
        self.identifier = identifier
        self.type = type
        self.default = default
        self.is_readonly = False
        self.is_enum_flag = False
        self.array_length = 0
        self.is_deprecated = is_deprecated
        self.deprecated_note = deprecated_note


class _Socket:
    def __init__(self, name, identifier=None):
        self.name = name
        self.identifier = identifier or name


class _Node:
    def __init__(self, props, values, inputs=()):
        self.bl_rna = SimpleNamespace(properties=props)
        self.inputs = [_Socket(n) for n in inputs]
        self.reads = []
        for k, v in values.items():
            object.__setattr__(self, k, v)

    def __getattribute__(self, name):
        if name in ("mode", "depth_order", "data_type"):
            object.__getattribute__(self, "reads").append(name)
        return object.__getattribute__(self, name)


class ReaderSkipsReplacedSettings(unittest.TestCase):

    def setUp(self):
        from blmcp.tools import _node_code_bpy as ncb
        self.ncb = ncb
        self.bpy = SimpleNamespace(types=SimpleNamespace(Node=SimpleNamespace(bl_rna=SimpleNamespace(properties=[]))))
        saved = getattr(ncb._base_names, "_cache", None)
        ncb._base_names._cache = frozenset()            # the stand-in Node has no base properties

        def restore():
            ncb._base_names._cache = saved
        self.addCleanup(restore)

    def _read(self, mode_prop, inputs):
        props = [mode_prop, _Prop("data_type", default="FLOAT")]
        node = _Node(props, {"mode": "MERGE_BY_ID", "data_type": "INT"}, inputs)
        fresh = _Node(props, {"mode": "MERGE_BY_NAME", "data_type": "FLOAT"}, inputs)
        return self.ncb.read_settings(self.bpy, node, fresh), node

    def test_blender_5_4_skips_mode_with_its_socket(self):
        note = "Replaced by '.inputs[\"Mode\"]'."
        out, node = self._read(_Prop("mode", default="MERGE_BY_NAME", is_deprecated=True, deprecated_note=note),
                               ["Grease Pencil", "Selection", "Mode", "Group ID"])
        self.assertEqual(out, [("data_type", "INT")])
        self.assertNotIn("mode", node.reads, "the deprecated property is never read (no DeprecationWarning)")

    def test_socket_named_by_identifier_or_with_spaces(self):
        note = "Replaced by '.inputs[\"Depth Order\"]'."
        out, _node = self._read(_Prop("mode", is_deprecated=True, deprecated_note=note),
                                ["Grease Pencil", "Depth Order"])
        self.assertEqual(out, [("data_type", "INT")])

    def test_blender_5_2_reads_it_as_before(self):
        out, _node = self._read(_Prop("mode", default="MERGE_BY_NAME"), ["Grease Pencil", "Selection", "Group ID"])
        self.assertEqual(out, [("mode", "MERGE_BY_ID"), ("data_type", "INT")])

    def test_deprecated_without_the_socket_is_still_read(self):
        out, _node = self._read(_Prop("mode", default="MERGE_BY_NAME", is_deprecated=True,
                                      deprecated_note="Replaced by '.inputs[\"Mode\"]'."),
                                ["Grease Pencil", "Selection"])
        self.assertEqual(out, [("mode", "MERGE_BY_ID"), ("data_type", "INT")])
        out, _node = self._read(_Prop("mode", default="MERGE_BY_NAME", is_deprecated=True,
                                      deprecated_note="No longer used."), ["Mode"])
        self.assertEqual(out, [("mode", "MERGE_BY_ID"), ("data_type", "INT")])


if __name__ == "__main__":
    unittest.main()
