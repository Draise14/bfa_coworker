# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the External Harness hardening in issue #101.

* Preflight style checks are advisory (code runs, guidance attached).
* Poly Haven: HDRI never edits another scene's world, ARM maps use
  ShaderNodeSeparateColor, glTF include files keep their relative paths.
* execute_blender_plan / list_blender_templates find the templates module.

Does not require Blender or the MCP SDK (``mcp`` is stubbed).  The generated
Blender code was also run in Bforartists 5.3 when this was written.  Run with::

    python -m unittest tests.test_bridge_hardening -v
"""

__all__ = ()

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_DIR = os.path.join(_REPO, "mcp")
_BRIDGE = os.path.join(_REPO, "addon", "bfa_coworker", "mcp_to_blender_server.py")


def _stub_mcp() -> None:
    """Just enough of the MCP SDK for the tool modules to import."""
    if "mcp.server.fastmcp" in sys.modules:
        return
    for name in ("mcp", "mcp.server", "mcp.server.fastmcp", "mcp.types"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["mcp.server.fastmcp"].FastMCP = object
    sys.modules["mcp.types"].ToolAnnotations = lambda **_kw: None


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_tool_modules():
    """Import the three modules under test without leaving stubs behind.

    Other suites import the real ``mcp`` SDK and ``blmcp``; anything added to
    ``sys.modules`` here is removed again so they are unaffected.
    """
    before = set(sys.modules)
    try:
        import mcp.server.fastmcp  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import
    except ImportError:
        _stub_mcp()
    if _MCP_DIR not in sys.path:
        sys.path.insert(0, _MCP_DIR)
    # blmcp/__init__ starts the server machinery; a bare package is enough.
    pkg = types.ModuleType("blmcp")
    pkg.__path__ = [os.path.join(_MCP_DIR, "blmcp")]
    saved_blmcp = sys.modules.get("blmcp")
    sys.modules["blmcp"] = pkg
    try:
        return (
            importlib.import_module("blmcp.tools_helpers.polyhaven_pbr"),
            importlib.import_module("blmcp.tools.download_polyhaven_asset"),
            importlib.import_module("blmcp.tools.execute_blender_code"),
        )
    finally:
        for name in set(sys.modules) - before:
            del sys.modules[name]
        if saved_blmcp is not None:
            sys.modules["blmcp"] = saved_blmcp


pbr, dl, ebc = _load_tool_modules()


class TestAdvisoryPreflight(unittest.TestCase):
    """Style checks must not refuse working code (#101)."""

    @classmethod
    def setUpClass(cls):
        source = Path(_BRIDGE).read_text(encoding="utf-8")
        start = source.index("_ADVISORY_PREFLIGHT = frozenset(")
        end = source.index("))", start) + 2
        ns: dict = {}
        exec(source[start:end], ns)
        cls.advisory = ns["_ADVISORY_PREFLIGHT"]
        cls.source = source

    def test_guard_checks_are_advisory(self):
        for name in ("no_existence_check", "unguarded_list_index", "ops_in_loop", "no_output",
                     "op_requires_selection", "op_requires_active_object"):
            self.assertIn(name, self.advisory)

    def test_certain_failures_still_block(self):
        for name in ("missing_bpy", "wrong_fcurves", "manual_channelbag", "hallucinated_module",
                     "mcp_tool_as_function", "wrong_lamps_api"):
            self.assertNotIn(name, self.advisory)

    def test_every_advisory_name_is_a_real_check(self):
        for name in self.advisory:
            self.assertIn('"{:s}"'.format(name), self.source.split("def _preflight_check(", 1)[1])

    def test_active_object_block_is_gone(self):
        self.assertNotIn("context_active_object_thread", self.source)

    def test_notes_reach_the_response(self):
        self.assertEqual(self.source.count('response["preflight_notes"] = list(preflight_notes)'), 2)


class TestHdriWorld(unittest.TestCase):
    def setUp(self):
        self.code = dl._import_hdri_code(r"C:\cache\hdris\studio_small_09\1k\studio_small_09_1k.hdr")

    def test_compiles(self):
        compile(self.code, "<hdri>", "exec")

    def test_never_falls_back_to_another_scenes_world(self):
        self.assertNotIn('worlds["World"]', self.code)
        self.assertNotIn("bpy.data.worlds[", self.code)

    def test_assigns_its_own_world_to_the_scene(self):
        self.assertIn("worlds.new('HDRI studio_small_09_1k')", self.code)
        self.assertIn("scene.world = world", self.code)
        self.assertIn("use_fake_user = True", self.code)


class TestPbrArm(unittest.TestCase):
    def test_arm_uses_separate_color(self):
        code = pbr.build_pbr_material_code("M", {
            "diffuse": Path("d.png"), "normal": Path("n.png"), "arm": Path("arm.png")})
        compile(code, "<pbr>", "exec")
        self.assertNotIn("ShaderNodeSeparateRGB", code)
        self.assertIn("ShaderNodeSeparateColor", code)
        self.assertIn("outputs['Green']", code)
        self.assertIn("outputs['Blue']", code)


class TestGltfIncludes(unittest.TestCase):
    INCLUDE = {
        "textures/rock_diff_1k.jpg": {"url": "https://dl.example/jpg/rock_diff_1k.jpg"},
        "rock.bin": {"url": "https://dl.example/gltf/8k/rock.bin"},
        "../escape.txt": {"url": "https://dl.example/x"},
        "C:/abs.txt": {"url": "https://dl.example/y"},
    }

    def test_relative_paths_kept_and_escapes_skipped(self):
        files = pbr._collect_include_files(self.INCLUDE)
        self.assertEqual(files["textures/rock_diff_1k.jpg"][1], "textures/rock_diff_1k.jpg")
        self.assertEqual(files["rock.bin"][1], "rock.bin")
        self.assertNotIn("../escape.txt", files)
        if sys.platform == "win32":
            self.assertNotIn("C:/abs.txt", files)

    def test_includes_downloaded_next_to_model(self):
        import unittest.mock as mock
        resolved = {"include": pbr._collect_include_files(self.INCLUDE)}
        fetched = []

        def _fake(url, dest):
            fetched.append((url, dest))
            return None

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(dl, "_download_with_progress", _fake):
            errors = dl._download_includes(resolved, Path(tmp))
            dests = {d.relative_to(tmp).as_posix() for _u, d in fetched}
        self.assertEqual(errors, [])
        self.assertEqual(dests, {"textures/rock_diff_1k.jpg", "rock.bin"})

    def test_include_failure_is_reported(self):
        import unittest.mock as mock
        resolved = {"include": pbr._collect_include_files({"rock.bin": {"url": "u"}})}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(dl, "_download_with_progress", lambda _u, _d: "HTTP 404"):
            errors = dl._download_includes(resolved, Path(tmp))
        self.assertEqual(errors, ["rock.bin: HTTP 404"])


class TestTemplatesLoad(unittest.TestCase):
    """The template registry is part of the server package (#97 S0)."""

    def test_templates_found_from_checkout(self):
        module = ebc._load_blender_templates()
        self.assertIsNotNone(module)
        self.assertIn("create_cube", module._TEMPLATES)
        code = module._plan_to_code([{"template": "create_cube", "params": {}}])
        self.assertIn("primitive_cube_add", code)

    def test_no_package_import_left(self):
        source = Path(ebc.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^\s*from bfa_coworker\.blender_templates import", source, re.M))

    def test_list_and_plan_tools(self):
        tools = _registered_tools()
        listing = tools["list_blender_templates"]()
        self.assertEqual(listing["status"], "ok")
        self.assertEqual(listing["count"], 18)
        cube = listing["templates"]["create_cube"]
        self.assertEqual(cube["params"], ["name", "size", "x", "y", "z"])
        self.assertNotIn("?", cube["defaults"].values())
        sent = []
        def fake_send(code, strict_json=False):
            del strict_json
            sent.append(code)
            return {"status": "ok"}
        with unittest.mock.patch.object(ebc, "send_code", fake_send):
            result = tools["execute_blender_plan"]([{"template": "create_cube", "params": {"size": 3}}])
        self.assertEqual(result["status"], "ok")
        self.assertIn("primitive_cube_add(size=3)", sent[0])

    def test_build_layout(self):
        """``<addon>/vendor/blmcp`` alone (no add-on source next to it) lists templates."""
        script = "; ".join((
            "import sys",
            "sys.path.insert(0, sys.argv[1])",
            "import blmcp.tools.execute_blender_code as ebc",
            "m = ebc._load_blender_templates()",
            "print(len(m._TEMPLATES), m.template_params('create_cube'))",
        ))
        with tempfile.TemporaryDirectory() as tmp:
            vendor = Path(tmp) / "addon" / "vendor"
            shutil.copytree(Path(ebc.__file__).resolve().parents[1], vendor / "blmcp",
                            ignore=shutil.ignore_patterns("__pycache__", "data"))
            out = subprocess.run([sys.executable, "-c", script, str(vendor)],
                                 capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("18 ['name', 'size', 'x', 'y', 'z']", out.stdout)


def _registered_tools():
    """Register execute_blender_code's tools on a stand-in FastMCP."""
    tools = {}

    class _MCP:  # pylint: disable=too-few-public-methods
        def tool(self, **_kw):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    ebc.register(_MCP())
    return tools


if __name__ == "__main__":
    unittest.main()
