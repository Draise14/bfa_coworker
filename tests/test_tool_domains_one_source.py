# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""#651: the tool-to-domain map is hand-edited in ONE file, ``mcp/blmcp/tool_domains.py``.

``agent_controller._TOOL_DOMAINS`` is read from that file (dev checkout:
``mcp/blmcp``; the built zip: ``vendor/blmcp``).  So a pull request that adds a
tool never edits ``agent_controller.py``.  The tool lists sent to the model must
stay byte-identical (Local's prompt cache, #546), so the map as it was on
``main`` before the change is pinned here: keys in order, every member.

bpy-free.  Run with::

    python -m unittest tests.test_tool_domains_one_source -v
"""

__all__ = ()

import ast
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_SERVER_FILE = _REPO / "mcp" / "blmcp" / "tool_domains.py"

# The loop's own _TOOL_DOMAINS on main before #651 (members sorted; key order as it was).
_BEFORE = json.loads(r"""{
 "animation": [
  "batch_keyframe_insert",
  "jump_to_view3d_object_by_name",
  "jump_to_view3d_object_data_by_name",
  "render_viewport_to_path"
 ],
 "material": [
  "assign_material_to_objects",
  "bake_textures",
  "build_node_tree_from_code",
  "download_polyhaven_asset",
  "get_node_tree_as_code",
  "get_screenshot_of_area_as_image",
  "load_asset_in_context",
  "render_viewport_to_path",
  "search_polyhaven_assets",
  "setup_pbr_material"
 ],
 "modeling": [
  "get_screenshot_of_area_as_image",
  "jump_to_tab_by_name",
  "jump_to_tab_by_space_type",
  "jump_to_view3d_object_by_name",
  "jump_to_view3d_object_data_by_name",
  "set_collection_color_tag"
 ],
 "lighting": [
  "download_polyhaven_asset",
  "get_screenshot_of_area_as_image",
  "render_viewport_to_path",
  "search_polyhaven_assets",
  "three_point_lighting_rig"
 ],
 "rendering": [
  "bake_textures",
  "build_node_tree_from_code",
  "get_node_tree_as_code",
  "get_screenshot_of_area_as_image",
  "render_viewport_to_path",
  "three_point_lighting_rig"
 ],
 "vse": [
  "add_captions",
  "apply_transcript_edit",
  "get_transcript_text",
  "get_vse_strip_detail",
  "get_vse_strips_summary",
  "get_vse_timeline_overview",
  "jump_to_tab_by_name",
  "jump_to_tab_by_space_type",
  "reframe_strips",
  "reframe_vertical",
  "remove_silences",
  "render_vse_preview",
  "set_strip_frame_range",
  "transcribe_strip"
 ],
 "geometry_nodes": [
  "build_node_tree_from_code",
  "get_active_node_tree",
  "get_node_group_interface",
  "get_node_tree_as_code",
  "get_screenshot_of_area_as_image",
  "jump_to_view3d_object_by_name",
  "jump_to_view3d_object_data_by_name",
  "wire_node_group"
 ],
 "web": [
  "read_web_page"
 ],
 "assets": [
  "assign_material_to_objects",
  "download_polyhaven_asset",
  "get_active_node_tree",
  "get_asset_libraries",
  "get_asset_tags",
  "get_node_group_interface",
  "jump_to_asset_browser",
  "list_asset_catalogs",
  "load_asset_in_context",
  "place_asset_in_scene",
  "search_assets",
  "search_polyhaven_assets",
  "wire_node_group"
 ]
}""")


def _loader(file_path: Path) -> Any:
    """``_load_tool_domains`` from agent_controller.py (it needs bpy as a whole), run as if at *file_path*."""
    tree = ast.parse((_ADDON / "agent_controller.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_load_tool_domains")
    ns: dict[str, Any] = {"Path": Path, "__file__": str(file_path)}
    exec(compile(ast.Module([node], []), "agent_controller.py", "exec"), ns)  # noqa: S102
    return ns["_load_tool_domains"]


def _canon(domains: dict) -> str:
    return json.dumps({k: sorted(v) for k, v in domains.items()}, ensure_ascii=False)


class TestOneSource(unittest.TestCase):

    def test_same_domains_as_before(self):
        """Keys in the same order, every member the same: the tool lists do not move."""
        got = _loader(_ADDON / "agent_controller.py")()
        self.assertEqual(list(got), list(_BEFORE))
        for key, members in _BEFORE.items():
            self.assertIsInstance(got[key], frozenset, key)
            self.assertEqual(sorted(got[key]), members, key)
        self.assertEqual(_canon(got), json.dumps(_BEFORE, ensure_ascii=False))

    def test_the_loop_keeps_no_copy(self):
        text = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
        self.assertEqual(text.count("_TOOL_DOMAINS: dict[str, frozenset[str]]"), 1)
        self.assertIn("_TOOL_DOMAINS: dict[str, frozenset[str]] = _load_tool_domains()", text)
        self.assertNotIn('"vse": frozenset', text)

    def test_the_loop_gets_the_servers_own_object(self):
        server = ast.parse(_SERVER_FILE.read_text(encoding="utf-8"))
        names = {t.target.id for t in server.body if isinstance(t, ast.AnnAssign)}
        self.assertIn("DOMAINS", names)
        got = _loader(_ADDON / "agent_controller.py")()
        import importlib.util
        spec = importlib.util.spec_from_file_location("_td_651", _SERVER_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(got, module.DOMAINS)

    def test_the_zip_layout_finds_vendor_blmcp(self):
        """An installed add-on has no ``mcp/`` sibling: the file comes from ``vendor/blmcp``."""
        tmp = Path(tempfile.mkdtemp())
        try:
            pkg = tmp / "addons" / "bfa_coworker"
            (pkg / "vendor" / "blmcp").mkdir(parents=True)
            shutil.copy(str(_SERVER_FILE), str(pkg / "vendor" / "blmcp" / "tool_domains.py"))
            got = _loader(pkg / "agent_controller.py")()
            self.assertEqual(_canon(got), json.dumps(_BEFORE, ensure_ascii=False))
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_a_missing_file_says_so(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(ImportError):
                _loader(tmp / "a" / "b" / "agent_controller.py")()
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_the_builder_copies_the_file_into_the_zip(self):
        build = (_REPO / "build_addon.py").read_text(encoding="utf-8")
        self.assertIn('blmcp_src, VENDOR_BLMCP_DIR', build)
        self.assertNotIn("tool_domains", build.replace("ignore_patterns", ""))   # copied whole, not filtered


if __name__ == "__main__":
    unittest.main()
