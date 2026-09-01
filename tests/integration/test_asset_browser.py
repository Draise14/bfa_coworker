# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
End-to-end integration test for the Tier 3d asset tools.

Verifies: MCPClient -> MCP server -> addon TCP bridge -> real Bforartists.

Deliberately runs **headless**: ``blender --background --command bfa_coworker``
with an isolated HOME. No agent, no LLM, no display. The tools are called
directly over JSON-RPC (stdio) with ``tests.mcp_client.MCPClient``.

A fixture asset library is created at startup (``TestLib`` with an OBJECT,
a MATERIAL and a Geometry-Node-Tree asset) and the full workflow is round-
tripped: libraries -> catalogs -> search (name + tag) -> tags -> load ->
place -> wire -> interface -> error paths.

Run with::

    BLENDER_BIN="C:\\3D_Stuff\\Devbuild\\bforartists.exe" \
        python -m unittest tests.integration.test_asset_browser -v

``BLENDER_BIN`` must be set (the test skips otherwise). ``BFACW_MCP``
overrides the MCP server binary (default: ``bfa-coworker-mcp`` on PATH).

Phase C note: the first ``get_asset_tags`` call may build the asset metadata
index by spawning a second headless Bforartists instance, so per-request
timeouts are scaled up via ``GLOBAL_TIMEOUT_SCALE`` (set before the
``MCPClient`` import below).
"""

__all__ = ()

import glob
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest

import inspect

import sys

# Root of the repository.
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure the repository root is on the path so `tests` resolves as a package.
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

# Scale up per-request timeouts before importing MCPClient (it reads this at
# import time).  The first get_asset_tags call can spawn a headless
# Bforartists subprocess to build the metadata index (Tier 3d Phase C).
os.environ.setdefault("GLOBAL_TIMEOUT_SCALE", "6")

from tests.mcp_client import MCPClient  # noqa: E402

# Fixed port for this test class (avoids 9876-9881 used by other suites).
_PORT = 9882


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_blender_mcp_with_blender.py).

def _blender_env(tmpdir: str) -> dict[str, str]:
    """Return an environment for Blender sub-processes with an isolated HOME."""
    env = os.environ.copy()
    env["HOME"] = tmpdir
    env["ASAN_OPTIONS"] = ":".join(filter(None, [
        env.get("ASAN_OPTIONS", ""),
        "alloc_dealloc_mismatch=0",
        "leak_check_at_exit=0",
    ]))
    return env


def _run_blender(args: list[str], env: dict[str, str]) -> None:
    """Run a Blender command and raise on failure, including stderr."""
    result = subprocess.run(args, capture_output=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed (exit {:d}):\n  {:s}\n{:s}".format(
                result.returncode,
                " ".join(args),
                result.stderr.decode("utf-8", errors="replace"),
            )
        )


def _drain_stdout(proc: subprocess.Popen) -> list[str]:
    """Read *proc* stdout lines into a list in a daemon thread."""
    output: list[str] = []

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            output.append(line.decode("utf-8", errors="replace").rstrip())

    threading.Thread(target=_reader, daemon=True).start()
    return output


def _wait_for_port(port: int, timeout: int, proc: subprocess.Popen) -> None:
    """Wait until *port* accepts TCP connections on localhost."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            raise RuntimeError(
                "Bforartists exited with code {:d} before the bridge became reachable".format(rc)
            )
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                sock.connect(("localhost", port))
                return
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    raise RuntimeError(
        "Port {:d} not reachable within {:d}s".format(port, timeout)
    )


def _python_fn_body_as_string(fn) -> str:
    """Return the body of *fn* as a dedented string (runs in Blender)."""
    source = inspect.getsource(fn)
    lines = source.splitlines()
    body_lines = lines[1:]
    code = textwrap.dedent("\n".join(body_lines))
    assert code.strip(), "Function body is empty"
    return code


# ---------------------------------------------------------------------------
# Test class.

@unittest.skipUnless(
    os.environ.get("BLENDER_BIN"),
    "BLENDER_BIN environment variable must be set (path to Bforartists/Blender)",
)
class TestAssetBrowser(unittest.TestCase):
    """Live, headless round-trip of the Tier 3d asset tools."""

    _tmpdir: tempfile.TemporaryDirectory
    _client: MCPClient
    _blender_proc: subprocess.Popen
    _lib_dir: str
    _env: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        print()
        blender_bin = os.environ["BLENDER_BIN"]
        blender_mcp = os.environ.get("BFACW_MCP", "bfa-coworker-mcp")

        cls._tmpdir = tempfile.TemporaryDirectory()
        tmpdir = cls._tmpdir.name
        cls.addClassCleanup(cls._tmpdir.cleanup)

        env = _blender_env(tmpdir)

        # ---- Build the addon extension zip ----
        print("  building addon extension ...", end="", flush=True)
        addon_src = os.path.join(_REPO_DIR, "addon", "bfa_coworker")
        _run_blender(
            [
                blender_bin, "--command", "extension", "build",
                "--source-dir=" + addon_src,
                "--output-dir=" + tmpdir,
            ],
            env=env,
        )
        zips = glob.glob(os.path.join(tmpdir, "bfa_coworker-*.zip"))
        if not zips:
            raise RuntimeError("Extension build did not produce a zip")
        print(" ok")

        # ---- Install + enable into the isolated HOME ----
        print("  installing addon ...", end="", flush=True)
        _run_blender(
            [
                blender_bin, "--online-mode", "--background", "--factory-startup",
                "--command", "extension", "install-file",
                zips[0], "--repo", "user_default", "--enable",
            ],
            env=env,
        )
        print(" ok")

        # ---- Launch Bforartists headless with the bridge server ----
        print("  starting headless Bforartists (port {:d}) ...".format(_PORT), end="", flush=True)
        cls._blender_proc = subprocess.Popen(
            [
                blender_bin, "--online-mode", "--background",
                "--command", "bfa_coworker", "--port", str(_PORT),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        cls.addClassCleanup(cls._cleanup_blender)
        _drain_stdout(cls._blender_proc)
        _wait_for_port(_PORT, 180, cls._blender_proc)
        print(" ok")

        # ---- Start the MCP server client (stdio JSON-RPC) ----
        print("  connecting MCP client ...", end="", flush=True)
        mcp_env = _blender_env(tmpdir)
        mcp_env["BFACW_PORT"] = str(_PORT)
        mcp_env["BLENDER_PATH"] = blender_bin
        cls._client = MCPClient([blender_mcp], env=mcp_env)
        cls.addClassCleanup(cls._client.close)
        cls._client.initialize()
        tools = cls._client.list_tools()
        print(" ok ({:d} tools)".format(len(tools)))

        expected = {
            "get_asset_libraries", "list_asset_catalogs", "search_assets",
            "get_asset_tags", "load_asset_in_context", "place_asset_in_scene",
            "jump_to_asset_browser", "assign_material_to_objects",
            "get_node_group_interface", "get_active_node_tree", "wire_node_group",
        }
        missing = sorted(expected - set(tools))
        if missing:
            raise RuntimeError(
                "Asset tools missing from server: {:s} (have {:d} tools)".format(
                    ", ".join(missing), len(tools),
                )
            )

        # ---- Build the fixture asset library ----
        cls._lib_dir = os.path.join(tmpdir, "lib")
        os.makedirs(cls._lib_dir, exist_ok=True)
        cls._blender_reset()
        cls._build_fixture_library()
        if os.environ.get("BFACW_DEBUG"):
            blend = os.path.join(cls._lib_dir, "assets.blend")
            print("\n  fixture file exists: {:s}".format(
                "yes ({:d} bytes)".format(os.path.getsize(blend)) if os.path.exists(blend) else "NO"
            ), flush=True)
            try:
                state = cls._exec(
                    "import bpy; lib = bpy.context.preferences.filepaths.asset_libraries.get('TestLib'); "
                    "result = {'name': lib.name if lib else None, "
                    "'path': str(lib.path) if lib and lib.path else None}"
                )
                print("  bridge sees lib: {:s}".format(str(state)), flush=True)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                print("  lib probe failed: {:s}".format(str(ex)), flush=True)
            def live_state() -> None:
                """Report the live session's datablocks (post-fixture)."""
                import bpy  # pylint: disable=import-error
                result = {
                    "objects": [o.name for o in bpy.data.objects],
                    "materials": [m.name for m in bpy.data.materials],
                    "node_groups": [n.name for n in bpy.data.node_groups],
                    "asset_objects": [
                        o.name for o in bpy.data.objects if o.asset_data
                    ],
                }

            def file_state() -> None:
                """Report what the fixture blend file exposes via libraries.load."""
                import bpy  # pylint: disable=import-error
                import os
                with bpy.data.libraries.load(os.environ["BFACW_PROBE_BLEND"]) as (df, _dt):
                    result = {
                        "mats": list(df.materials),
                        "objs": list(df.objects),
                        "ngs": list(df.node_groups),
                    }

            try:
                print("  live state: {:s}".format(
                    str(cls._exec(_python_fn_body_as_string(live_state))), flush=True))
                cls._exec(
                    "import os\nos.environ['BFACW_PROBE_BLEND'] = {!r}\n{:s}\n".format(
                        blend, _python_fn_body_as_string(file_state),
                    )
                )
                print("  file contents: {:s}".format(str(
                    cls._exec(_python_fn_body_as_string(file_state)),
                )), flush=True)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                print("  probe failed: {:s}".format(str(ex)[:3000]), flush=True)
        print("  fixture library 'TestLib' ready at {:s}".format(cls._lib_dir))

        cls._env = env

    @classmethod
    def _cleanup_blender(cls) -> None:
        cls._blender_proc.terminate()
        try:
            cls._blender_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            cls._blender_proc.kill()
            cls._blender_proc.wait(timeout=15)
        if cls._blender_proc.stdout is not None:
            cls._blender_proc.stdout.close()

    # -----------------------------------------------------------------
    # Blender-side helpers (via the bridge).

    @classmethod
    def _exec(cls, code: str) -> dict:
        """Run *code* in Blender and return the parsed tool result."""
        data = cls._call("execute_blender_code", {"code": code})
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(
                "execute_blender_code failed: {!r}".format(data)[:3000]
            )
        return data

    @classmethod
    def _call(cls, name: str, arguments: dict) -> dict:
        """Call an MCP tool and return its parsed JSON result (errors kept)."""
        result = cls._client.call_tool(name, arguments)
        content = result.get("content", []) if isinstance(result, dict) else []
        if not content:
            raise RuntimeError(
                "Empty content from {:s}: {!r}".format(name, result)
            )
        text_item = content[0]
        if text_item.get("type") != "text":
            raise RuntimeError(
                "Expected text content from {:s}, got {!r}".format(name, text_item)
            )
        try:
            data = json.loads(text_item["text"])
        except json.JSONDecodeError as ex:
            raise RuntimeError(
                "Non-JSON response from {:s}: {!r}...".format(
                    name, text_item.get("text", "")[:500],
                )
            ) from ex
        if data.get("status") == "ok":
            return data.get("result", data)
        return data

    @classmethod
    def _blender_reset(cls):
        """Reset the scene in-process (no file reload — a reload would restart
        the addon and drop the bridge connection)."""
        def reset() -> None:
            import bpy  # pylint: disable=import-error
            _dbg = []
            # Unlink every object from scene collections (operations like
            # bpy.ops.object.delete do not run in the bridge's exec context).
            for scene in bpy.data.scenes:
                for coll in list(bpy.data.collections) + [scene.collection]:
                    for obj in list(coll.objects):
                        try:
                            coll.objects.unlink(obj)
                        except Exception as exc:
                            _dbg.append("unlink failed: {:s}".format(str(exc)))
            for attr in ("objects", "node_groups", "materials", "meshes",
                         "actions", "worlds", "images", "curves", "lattices",
                         "armatures", "cameras", "lights", "metaballs"):
                coll = getattr(bpy.data, attr, None)
                if coll is None:
                    continue
                for datablock in list(coll):
                    # asset_mark() sets fake user, so unused assets would
                    # otherwise survive the purge and cause append renames
                    # (.001) later on.
                    try:
                        if getattr(datablock, "use_fake_user", False):
                            datablock.use_fake_user = False
                    except Exception as exc:
                        _dbg.append("fake-user clear failed: {:s}".format(str(exc)))
                    if datablock.users == 0:
                        try:
                            coll.remove(datablock)
                        except Exception as exc:
                            _dbg.append("remove failed ({:s}): {:s}".format(attr, str(exc)))
            result = {"reset": True, "dbg": _dbg,
                      "objects": [o.name for o in bpy.data.objects]}

        return cls._exec(_python_fn_body_as_string(reset))

    @classmethod
    def _build_fixture_library(cls) -> None:
        """Create TestLib (object / material / geometry node group assets)."""
        def fixture() -> None:
            import bpy  # pylint: disable=import-error

            # Object asset: Crate_Cube (tags: prop, wood).
            bpy.ops.mesh.primitive_cube_add()
            ob = bpy.context.view_layer.objects.active
            ob.name = ob.data.name = "Crate_Cube"
            ob.asset_mark()
            ob.asset_data.tags.new("prop")
            ob.asset_data.tags.new("wood")
            ob.asset_data.description = "A wooden crate prop"
            ob.asset_data.author = "Tier3d Tests"
            ob.asset_data.license = "CC0"

            # Material asset: Brick_Mat (tags: material, brick).
            mat = bpy.data.materials.new("Brick_Mat")
            mat.asset_mark()
            mat.asset_data.tags.new("material")
            mat.asset_data.tags.new("brick")
            mat.asset_data.description = "A brick wall material"

            # Geometry node group asset: Tree_Foliage (tags: geometry, foliage).
            ng = bpy.data.node_groups.new("Tree_Foliage", "GeometryNodeTree")
            # Explicit per-socket creation (avoid loops so the fixture code
            # passes the preflight validator, which is aimed at LLM output).
            ng.interface.new_socket(name="Scale", in_out="INPUT", socket_type="NodeSocketFloat")
            ng.interface.new_socket(name="Seed", in_out="INPUT", socket_type="NodeSocketInt")
            ng.interface.new_socket(name="Color", in_out="INPUT", socket_type="NodeSocketColor")
            ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
            ng.asset_mark()
            ng.asset_data.tags.new("geometry")
            ng.asset_data.tags.new("foliage")

            # Save the fixture blend + register the library. `copy=True`
            # keeps the saved file OUT of the current-file slot, so
            # `bpy.data.libraries.load(...)` can still read it back
            # (loading from the current blend file raises ValueError).
            lib_dir = __import__("os").environ["BFACW_LIB_DIR"]
            bpy.ops.wm.save_as_mainfile(
                filepath=__import__("os").path.join(lib_dir, "assets.blend"), copy=True,
            )
            lib = bpy.context.preferences.filepaths.asset_libraries.new(
                name="TestLib", directory=lib_dir)
            result = {"library": lib.name, "assets": [
                "Crate_Cube", "Brick_Mat", "Tree_Foliage",
            ]}

        env = os.environ.copy()
        env["BFACW_LIB_DIR"] = cls._lib_dir
        # The bridge executes in the addon's process; env is not forwarded, so
        # pass the lib dir through the generated code instead.
        cls._exec(
            "import os\nos.environ['BFACW_LIB_DIR'] = {!r}\n{:s}\n".format(
                cls._lib_dir, _python_fn_body_as_string(fixture),
            )
        )

    # -----------------------------------------------------------------
    # Helpers for assertions.

    def setUp(self) -> None:
        """Start each test from a clean scene (the fixture library persists)."""
        self._blender_reset()

    def _tool(self, name: str, arguments: dict) -> dict:
        """Call a tool and fail the test on a non-ok status."""
        data = self._call(name, arguments)
        self.assertEqual(
            data.get("status"), "ok",
            "{:s} failed: {:s}".format(name, str(data)),
        )
        return data

    def _blender_state(self, code: str) -> dict:
        """Evaluate JSON-able state in Blender."""
        return self._exec(code)

    # -----------------------------------------------------------------
    # Tests

    def test_libraries_listed(self) -> None:
        """get_asset_libraries returns the TestLib fixture library."""
        data = self._tool("get_asset_libraries", {})
        found = False
        for lib in data if isinstance(data, list) else data.get("libraries", []):
            if isinstance(lib, dict) and lib.get("name") == "TestLib":
                found = True
        self.assertTrue(found, "TestLib not in: {:s}".format(str(data)))

    def test_list_catalogs(self) -> None:
        """list_asset_catalogs runs for the fixture library."""
        data = self._tool("list_asset_catalogs", {"library_name": "TestLib"})
        self.assertTrue(isinstance(data, dict))

    def test_search_by_name(self) -> None:
        """search_assets finds Brick_Mat by name."""
        data = self._tool("search_assets", {"query": "Brick", "library_name": "TestLib"})
        names = [m.get("name") for m in data.get("matches", [])]
        self.assertIn("Brick_Mat", names, "matches: {:s}".format(str(names)))

    def test_search_by_tag(self) -> None:
        """search_assets finds Tree_Foliage via the 'foliage' tag (not its name)."""
        data = self._tool("search_assets", {"query": "foliage", "library_name": "TestLib"})
        names = [m.get("name") for m in data.get("matches", [])]
        self.assertIn("Tree_Foliage", names, "matches: {:s}".format(str(names)))

    def test_get_asset_tags_nodetype(self) -> None:
        """get_asset_tags reports the node group editor type + tags."""
        data = self._tool("get_asset_tags", {
            "library_name": "TestLib",
            "asset_name": "Tree_Foliage",
        })
        self.assertEqual(data["asset_type"], "NODETREE")
        self.assertEqual(data["editor_type"], "GeometryNodeTree")
        tag_names = [t for t in data["tags"]]
        self.assertIn("foliage", tag_names, "tags: {:s}".format(str(tag_names)))

    def test_load_material_explicit_object(self) -> None:
        """load_asset_in_context assigns a material asset to a named object."""
        self._blender_state(
            "import bpy; bpy.ops.mesh.primitive_cube_add(); "
            "bpy.context.view_layer.objects.active.name = 'Target'; "
            "result = {'ok': True}"
        )
        data = self._tool("load_asset_in_context", {
            "library_name": "TestLib",
            "asset_name": "Brick_Mat",
            "asset_type": "MATERIAL",
            "object_name": "Target",
        })
        self.assertIn("Target", data.get("loaded_into", ""))
        state = self._blender_state(
            "import bpy; ob = bpy.data.objects['Target']; "
            "result = {'materials': [s.name for s in ob.data.materials]}"
        )
        # The fixture keeps an in-session copy of Brick_Mat (fake user), so
        # the appended library copy may be renamed to Brick_Mat.001.
        materials = state.get("materials", [])
        self.assertTrue(
            any(name == "Brick_Mat" or name.startswith("Brick_Mat.")
                for name in materials),
            "materials: {:s}".format(str(materials)),
        )

    def test_place_object_transform(self) -> None:
        """place_asset_in_scene appends Crate_Cube at the requested location."""
        data = self._tool("place_asset_in_scene", {
            "library_name": "TestLib",
            "asset_name": "Crate_Cube",
            "asset_type": "OBJECT",
            "location": [2.0, 3.0, 4.0],
        })
        self.assertEqual(data["link_mode"], "APPEND")
        state = self._blender_state(
            "import bpy; ob = bpy.data.objects.get('Crate_Cube'); "
            "result = {'loc': list(ob.location) if ob else None, "
            "'names': [o.name for o in bpy.data.objects], "
            "'types': [o.type for o in bpy.data.objects]}"
        )
        loc = state.get("loc")
        self.assertIsNotNone(
            loc, "appended object not found: {:s}".format(str(state)),
        )
        self.assertAlmostEqual(loc[0], 2.0, places=3, msg=str(state) + str(data))
        self.assertAlmostEqual(loc[1], 3.0, places=3, msg=str(state) + str(data))
        self.assertAlmostEqual(loc[2], 4.0, places=3, msg=str(state) + str(data))

    def test_wire_group_add_top_level(self) -> None:
        """wire_node_group loads the asset and inserts it into an explicit tree."""
        self._blender_state(
            "import bpy; bpy.ops.mesh.primitive_cube_add(); "
            "ob = bpy.context.view_layer.objects.active; "
            "ng = bpy.data.node_groups.new('WireTree', 'GeometryNodeTree'); "
            "ob.modifiers.new('GN', 'NODES').node_group = ng; "
            "result = {'tree': ng.name}"
        )
        data = self._tool("wire_node_group", {
            "library_name": "TestLib",
            "asset_name": "Tree_Foliage",
            "tree_type": "GeometryNodeTree",
            "node_tree_name": "WireTree",
            "insert_mode": "add_top_level",
        })
        self.assertTrue(data.get("group_node"), "no group node created: {:s}".format(str(data)))
        state = self._blender_state(
            "import bpy; tree = bpy.data.node_groups['WireTree']; "
            "result = {'has_group': any(n.type == 'GROUP' and n.node_tree "
            "and n.node_tree.name == 'Tree_Foliage' for n in tree.nodes)}"
        )
        self.assertTrue(state.get("has_group"))

    def test_wire_group_connect_to_output(self) -> None:
        """wire_node_group connect_to_output links Geometry to Group Output."""
        self._blender_state(
            "import bpy; bpy.ops.mesh.primitive_cube_add(); "
            "ob = bpy.context.view_layer.objects.active; "
            "ob.modifiers.new('GN', 'NODES'); "
            "ng = bpy.data.node_groups.new('WireTree2', 'GeometryNodeTree'); "
            "result = {'tree': ng.name}"
        )
        data = self._tool("wire_node_group", {
            "library_name": "TestLib",
            "asset_name": "Tree_Foliage",
            "tree_type": "GeometryNodeTree",
            "node_tree_name": "WireTree2",
            "insert_mode": "connect_to_output",
        })
        self.assertTrue(
            data.get("links_created"),
            "expected links: {:s}".format(str(data)),
        )

    def test_node_interface_after_load(self) -> None:
        """get_node_group_interface lists the asset's named sockets."""
        # Load the group into a local material tree first so it exists in bpy.data.
        self._blender_state(
            "import bpy; bpy.ops.mesh.primitive_cube_add(); "
            "bpy.context.view_layer.objects.active.name = 'MatHolder'; "
            "mat = bpy.data.materials.new('IMat'); "
            "mat.use_nodes = True; "
            "bpy.context.view_layer.objects.active.active_material = mat; "
            "result = {'ok': True}"
        )
        self._tool("load_asset_in_context", {
            "library_name": "TestLib",
            "asset_name": "Tree_Foliage",
            "asset_type": "NODETREE",
            "tree_name": "IMat",
        })
        data = self._tool("get_node_group_interface", {"group_name": "Tree_Foliage"})
        inputs = [s.get("name") for s in data.get("inputs", [])]
        for expected in ("Scale", "Seed", "Color"):
            self.assertIn(expected, inputs, "inputs: {:s}".format(str(inputs)))

    def test_error_unknown_asset(self) -> None:
        """Loading an asset that does not exist returns a clean error."""
        data = self._call("load_asset_in_context", {
            "library_name": "TestLib",
            "asset_name": "Does_Not_Exist",
        })
        self.assertEqual(data.get("status"), "error")
        self.assertIn("not found", data.get("message", "").lower())

    def test_wire_error_missing_tree(self) -> None:
        """Wiring into a tree that does not exist returns a clean error."""
        data = self._call("wire_node_group", {
            "library_name": "TestLib",
            "asset_name": "Tree_Foliage",
            "tree_type": "GeometryNodeTree",
            "node_tree_name": "NoSuchTree",
            "insert_mode": "add_top_level",
        })
        self.assertEqual(data.get("status"), "error")

    def test_jump_asset_browser_background_graceful(self) -> None:
        """jump_to_asset_browser reports cleanly in background mode."""
        data = self._call("jump_to_asset_browser", {"allow_edits": False})
        self.assertEqual(data.get("status"), "error")
        self.assertIn("background", data.get("message", "").lower())


if __name__ == "__main__":
    unittest.main()