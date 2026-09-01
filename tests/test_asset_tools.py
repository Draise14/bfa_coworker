"""
Unit tests for the Tier 3d asset + node-group toolcodes.

The toolcodes under test are plain Python modules that only ``import bpy``
*inside* ``main()`` (they run in Blender via the bridge).  This file
injects a synthetic ``bpy`` module into ``sys.modules`` and executes each
``*_toolcode.py`` source directly, then calls ``main(Params(...))`` and
asserts on the returned ``Result``.

Covered:
- ``search_assets`` — name / tag / description matching, type + library filters.
- ``load_asset_in_context`` — all six asset types, append vs link,
  positioning, smart material-slot handling, editor-aware node groups.
- ``wire_node_group`` — all four insert modes, deterministic socket
  auto-mapping (exact name -> fuzzy -> compatible type), type validation.
- ``get_node_group_interface`` — interface serialization (new + legacy API).
- ``get_active_node_tree`` — tree resolution by editor type + serialization.

Run with::

    python -m unittest tests.test_asset_tools -v
"""

__all__ = ()

import json
import os
import shutil
import sys
import tempfile
import types
import unittest

# ---------------------------------------------------------------------------
# Synthetic `bpy` module.
#
# The stub keeps state per test-case instance (a fresh `bpy` is installed in
# `setUp`), so tests never leak assets/trees into each other.


class _Vec2:
    """Node location with `x`/`y` attributes and iteration (like mathutils.Vector)."""

    def __init__(self, x=0.0, y=0.0):
        self.x = float(x)
        self.y = float(y)

    def __iter__(self):
        return iter((self.x, self.y))


class _Socket:
    """A node socket with `name`, `type`, `node` back-ref and links."""

    def __init__(self, name, sock_type, default=None, node=None):
        self.name = name
        self.type = sock_type
        self.default_value = default
        self.hide = False
        self.node = node
        self.links = []


class _Sockets(list):
    """Socket collection supporting ``inputs["Name"]`` lookup."""

    def __getitem__(self, key):
        if isinstance(key, str):
            for sock in self:
                if sock.name == key:
                    return sock
            raise KeyError(key)
        return list.__getitem__(self, key)


class _Node:
    """A node with inputs/outputs, location, mute state and a node_tree."""

    def __init__(self, name, bl_idname, node_type, location=(0.0, 0.0)):
        self.name = name
        self.label = ""
        self.bl_idname = bl_idname
        self.type = node_type
        self._location = _Vec2(*location)
        self.mute = False
        self.inputs = _Sockets()
        self.outputs = _Sockets()
        self._node_tree = None

    @property
    def location(self):
        return self._location

    @location.setter
    def location(self, value):
        # Real Blender keeps `node.location` vector-like after assignment.
        self._location = value if isinstance(value, _Vec2) else _Vec2(*value)

    @property
    def node_tree(self):
        return self._node_tree

    @node_tree.setter
    def node_tree(self, tree):
        """Attaching a node group materializes its sockets on this node."""
        self._node_tree = tree
        self.inputs = _Sockets()
        self.outputs = _Sockets()
        if tree is None:
            return
        for sock in getattr(tree, "inputs", []):
            self.inputs.append(_Socket(sock.name, sock.type, node=self))
        for sock in getattr(tree, "outputs", []):
            self.outputs.append(_Socket(sock.name, sock.type, node=self))

    def add_input(self, name, sock_type, default=None):
        sock = _Socket(name, sock_type, default, node=self)
        self.inputs.append(sock)
        return sock

    def add_output(self, name, sock_type):
        sock = _Socket(name, sock_type, node=self)
        self.outputs.append(sock)
        return sock


class _Link:
    """A link between two sockets, tracking both ends."""

    def __init__(self, from_node, from_socket, to_node, to_socket):
        self.from_node = from_node
        self.from_socket = from_socket
        self.to_node = to_node
        self.to_socket = to_socket
        from_socket.links.append(self)
        to_socket.links.append(self)


class _Nodes(list):
    """Node collection: `.new()`, `.get()`, `.remove()`, `.active`."""

    def __init__(self):
        super().__init__()
        self.active = None
        self._seq = 0

    def new(self, type=None):
        node = _Node("Node.{:03d}".format(self._seq), type or "Unknown", type or "UNKNOWN")
        self._seq += 1
        self.append(node)
        return node

    def get(self, name):
        for node in self:
            if node.name == name:
                return node
        return None


class _Links(list):
    """Link collection: `.new()` and `.remove()`."""

    def new(self, from_socket, to_socket):
        link = _Link(from_socket.node, from_socket, to_socket.node, to_socket)
        self.append(link)
        return link

    def remove(self, link):
        if link in self:
            list.remove(self, link)
            link.from_socket.links.remove(link)
            link.to_socket.links.remove(link)


class _NodeTree:
    """A node tree: `nodes`, `links`, `type`, `name`, optional `interface`."""

    def __init__(self, name, tree_type, interface=None):
        self.name = name
        self.type = tree_type
        self.nodes = _Nodes()
        self.links = _Links()
        self.interface = interface
        # Legacy interface (inputs/outputs socket lists).
        self.inputs = _Sockets()
        self.outputs = _Sockets()


class NodeTreeInterfaceSocket:
    """Blender 4.x style interface item (name matters: the toolcode checks it)."""

    def __init__(self, name, socket_type, in_out, default=None, description=""):
        self.name = name
        self.socket_type = socket_type
        self.in_out = in_out
        self.default_value = default
        self.description = description


class _Interface:
    def __init__(self, items):
        self.items_tree = items


class _LibraryData:
    """What `bpy.data.libraries.load(path)` exposes for one blend file."""

    def __init__(self, materials=(), node_groups=(), objects=(),
                 worlds=(), collections=(), actions=()):
        self.materials = list(materials)
        self.node_groups = list(node_groups)
        self.objects = list(objects)
        self.worlds = list(worlds)
        self.collections = list(collections)
        self.actions = list(actions)

    def all_names(self):
        return (
            list(self.materials) + list(self.node_groups) + list(self.objects)
            + list(self.worlds) + list(self.collections) + list(self.actions)
        )


class _Tag:
    def __init__(self, name):
        self.name = name


class _AssetData:
    def __init__(self, tags=(), description=""):
        self.tags = [_Tag(t) for t in tags]
        self.description = description


class _DataBlock:
    """A datablock produced by loading from a library."""

    def __init__(self, name, kind, tree_type=None, asset_data=None):
        self.name = name
        self.kind = kind
        self.type = tree_type
        self.asset_data = asset_data
        self.description = asset_data.description if asset_data else ""
        self.location = (0.0, 0.0, 0.0)
        self.select = False
        self.objects = []
        self.children = []
        self.animation_data = None
        self.use_nodes = False
        self.node_tree = None

    def select_set(self, value):
        self.select = value


# Metadata for datablocks (tags/description) keyed by (path, kind, name).
_ASSET_META: dict[tuple, _AssetData] = {}


class _LoadTarget:
    """The `data_to` half of a library load.

    Assignments record what to load; attribute access materializes the
    datablocks lazily, so reads inside the ``with`` block (as the toolcodes
    do) return real datablocks rather than name strings.
    """

    _KINDS = ("materials", "node_groups", "objects", "worlds",
              "collections", "actions")

    def __init__(self, data_from, path):
        self._data_from = data_from
        self._path = path
        self._requests = {}

    def __setattr__(self, kind, value):
        if kind.startswith("_"):
            object.__setattr__(self, kind, value)
        elif kind in self._KINDS:
            self._requests[kind] = list(value)
        else:
            object.__setattr__(self, kind, value)

    def __getattr__(self, kind):
        if kind not in self._KINDS:
            raise AttributeError(kind)
        names = self._requests.get(kind, [])
        source_names = set(getattr(self._data_from, kind))
        loaded = []
        for name in names:
            if name in source_names:
                tree_type = None
                if kind == "node_groups":
                    tree_type = _TREE_TYPES.get(name, "ShaderNodeTree")
                asset_data = _ASSET_META.get((self._path, kind, name))
                loaded.append(_DataBlock(name, kind, tree_type=tree_type, asset_data=asset_data))
            else:
                loaded.append(None)
        return loaded


class _LoadCtx:
    """Context manager returned by the stub `libraries.load`."""

    def __init__(self, lib_data, path, link=False):
        self._data = lib_data
        self._path = path
        self._link = link
        self.data_from = lib_data
        self.data_to = _LoadTarget(lib_data, path)

    def __enter__(self):
        return self.data_from, self.data_to

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class _Libraries:
    """`bpy.data.libraries.load(path, link=...)` dispatcher."""

    def __init__(self, blend_path_to_data):
        self._map = blend_path_to_data

    def load(self, path, link=False, pack=False, assets_only=False, **kwargs):
        del pack, assets_only
        data = self._map.get(path)
        if data is None:
            raise FileNotFoundError(path)
        return _LoadCtx(data, path, link=link)


class _Modifiers(list):
    def new(self, name, type=None):
        mod = _Modifier(name, type)
        self.append(mod)
        return mod


class _Modifier:
    def __init__(self, name, mod_type):
        self.name = name
        self.type = mod_type
        self.node_group = None


class _Children(list):
    def link(self, col):
        if col not in self:
            self.append(col)


class _Collection:
    def __init__(self, name="Scene Collection"):
        self.name = name
        self.objects = _Objects()
        self.children = _Children()

    def link(self, obj):
        if obj not in self.objects:
            self.objects.append(obj)


class _Scene:
    """Scene whose `use_nodes` auto-creates the compositor tree (like Blender)."""

    def __init__(self):
        self.collection = _Collection()
        self.world = None
        self._use_nodes = False
        self.node_tree = None

    @property
    def use_nodes(self):
        return self._use_nodes

    @use_nodes.setter
    def use_nodes(self, value):
        self._use_nodes = bool(value)
        if value and self.node_tree is None:
            self.node_tree = _NodeTree("Compositor", "CompositorNodeTree")


class _MeshData:
    def __init__(self):
        self.materials = []


class _Obj:
    def __init__(self, name="Cube", obj_type="MESH"):
        self.name = name
        self.type = obj_type
        self.data = _MeshData()
        self.modifiers = _Modifiers()
        self.active_material = None
        self.animation_data = None
        self.location = (0.0, 0.0, 0.0)
        self.select = False

    def select_set(self, value):
        self.select = value

    def animation_data_create(self):
        self.animation_data = _AnimData()
        return self.animation_data


class _AnimData:
    def __init__(self):
        self.action = None


class _Objects(list):
    active = None

    def link(self, obj):
        if obj not in self:
            self.append(obj)


class _ViewLayer:
    def __init__(self):
        self.objects = _Objects()


class _OpsMesh:
    def primitive_cube_add(self):
        obj = _Obj("Cube", "MESH")
        return obj


class _OpsED:
    def __init__(self):
        self.undo_pushed = []

    def undo_push(self, message=""):
        self.undo_pushed.append(message)


def _make_bpy():
    """Build a fresh synthetic `bpy` module."""
    bpy = types.ModuleType("bpy")
    bpy.data = types.SimpleNamespace(
        node_groups={},
        materials={},
        libraries=_Libraries({}),
    )
    bpy.context = types.SimpleNamespace(
        active_object=None,
        object=None,
        scene=None,
        view_layer=None,
        preferences=types.SimpleNamespace(
            filepaths=types.SimpleNamespace(asset_libraries=[]),
        ),
    )
    bpy.ops = types.SimpleNamespace(
        mesh=_OpsMesh(),
        ed=_OpsED(),
    )
    return bpy


# Map of node-group asset names to their tree types (used by the load stub).
_TREE_TYPES: dict[str, str] = {}


def _install_bpy(test_case):
    """Give *test_case* a fresh stub bpy and register it as `sys.modules["bpy"]`.

    ``bpy.utils.cache_path`` returns a per-test temp dir so the Phase C
    asset-index helpers can read/write a cache without touching the real
    user profile.
    """
    bpy = _make_bpy()
    cache_dir = tempfile.mkdtemp(prefix="bfacw_cache_")
    test_case._cache_dir = cache_dir
    test_case.addCleanup(
        lambda: shutil.rmtree(cache_dir, ignore_errors=True))
    bpy.utils = types.SimpleNamespace(
        cache_path=lambda user=False: cache_dir,
    )
    sys.modules["bpy"] = bpy
    test_case.bpy = bpy


_INCLUDE_BEGIN_PREFIX = "# @include_begin: "
_INCLUDE_END = "# @include_end"


def _expand_includes(toolcode_path, source):
    """Splice ``# @include_begin`` blocks, mirroring ``tools_helpers``."""
    toolcode_dir = os.path.dirname(toolcode_path)
    lines = source.splitlines(True)
    result = []
    skip = False
    for line in lines:
        if line.startswith(_INCLUDE_BEGIN_PREFIX):
            include_name = line[len(_INCLUDE_BEGIN_PREFIX):].rstrip()
            include_path = os.path.join(toolcode_dir, include_name)
            with open(include_path, "r", encoding="utf-8") as fh:
                result.append(fh.read())
            if result[-1] and not result[-1].endswith("\n"):
                result.append("\n")
            skip = True
        elif skip:
            if line.startswith(_INCLUDE_END):
                skip = False
        else:
            result.append(line)
    if skip:
        raise ValueError("Missing {:s} in {:s}".format(_INCLUDE_END, toolcode_path))
    return "".join(result)


def _load_toolcode(name):
    """Return the toolcode module object by executing its source directly."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "mcp", "blmcp", "tools", "{:s}_toolcode.py".format(name),
    )
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    source = _expand_includes(path, source)
    module = types.ModuleType("{:s}_toolcode".format(name))
    exec(compile(source, path, "exec"), module.__dict__)
    return module


def _call(name, **params):
    """Execute toolcode `main()` and return the `Result` as a dict."""
    module = _load_toolcode(name)
    return module.main(module.Params(**params))._asdict()


# ---------------------------------------------------------------------------
# Shared helpers to build stub scenes / libraries.


def _make_lib_path(tmpdir, blend_name="library.blend"):
    path = os.path.join(tmpdir, blend_name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("stub")
    return path


def _register_library(bpy, name, path):
    lib = types.SimpleNamespace(name=name, path=path)
    bpy.context.preferences.filepaths.asset_libraries.append(lib)
    return lib


def _register_asset(bpy, blend_path, kind, name, tree_type=None,
                    tags=(), description=""):
    """Register *name* of *kind* in the stub library at *blend_path*."""
    data = bpy.data.libraries._map.setdefault(blend_path, _LibraryData())
    getattr(data, kind).append(name)
    if kind == "node_groups":
        _TREE_TYPES[name] = tree_type or "ShaderNodeTree"
    # Store asset_data for tag/description lookup (search_assets path).
    _ASSET_META[(blend_path, kind, name)] = _AssetData(tags=tags, description=description)


# ---------------------------------------------------------------------------
# Test cases.


class TestSearchAssets(unittest.TestCase):
    """`search_assets`: name, tag, and description matching."""

    def setUp(self):
        _install_bpy(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = _make_lib_path(self._tmp.name)
        _register_library(self.bpy, "MyLib", self._tmp.name)
        _register_asset(
            self.bpy, path, "materials", "BrickWall",
            tags=["brick"], description="modular brick wall")
        _register_asset(
            self.bpy, path, "materials", "Oak_Floor",
            tags=["wood"], description="dark oak floor")
        _register_asset(
            self.bpy, path, "node_groups", "BrickTiles",
            tree_type="ShaderNodeTree",
            tags=["brick", "tile"], description="brick tile shader")

    def test_name_match(self):
        result = _call("search_assets", query="brick")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_found"], 2)  # BrickWall + BrickTiles
        names = {m["name"] for m in result["matches"]}
        self.assertIn("BrickWall", names)

    def test_tag_match_without_name(self):
        # "wood" appears only in Oak_Floor's tag/description, not its name.
        result = _call("search_assets", query="wood")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_found"], 1)
        self.assertEqual(result["matches"][0]["name"], "Oak_Floor")

    def test_description_match(self):
        result = _call("search_assets", query="modular")
        self.assertEqual(result["total_found"], 1)
        self.assertEqual(result["matches"][0]["name"], "BrickWall")

    def test_type_filter(self):
        result = _call("search_assets", query="brick", asset_type="NODETREE")
        self.assertEqual(result["total_found"], 1)
        self.assertEqual(result["matches"][0]["type"], "NODETREE")

    def test_library_filter(self):
        result = _call("search_assets", query="brick", library_name="OtherLib")
        self.assertEqual(result["total_found"], 0)

    def test_no_matches(self):
        result = _call("search_assets", query="nonexistent_xyz")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_found"], 0)
        self.assertEqual(result["matches"], [])

    def test_missing_library_dir(self):
        # Library path does not exist -> skipped, no crash.
        _register_library(self.bpy, "Gone", os.path.join(self._tmp.name, "missing"))
        result = _call("search_assets", query="brick", library_name="Gone")
        self.assertEqual(result["total_found"], 0)


class TestLoadAssetInContext(unittest.TestCase):
    """`load_asset_in_context`: all six types, append/link, positioning."""

    def setUp(self):
        _install_bpy(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._blend = _make_lib_path(self._tmp.name)
        _register_library(self.bpy, "MyLib", self._tmp.name)

    def _load(self, asset_name, asset_type="", link_mode="APPEND", location=None):
        return _call(
            "load_asset_in_context",
            library_name="MyLib", asset_name=asset_name,
            asset_type=asset_type, link_mode=link_mode, location=location,
        )

    def test_library_not_found(self):
        result = _call(
            "load_asset_in_context",
            library_name="Nope", asset_name="X", asset_type="MATERIAL")
        self.assertEqual(result["status"], "error")
        self.assertIn("not found", result["message"])

    def test_asset_not_found(self):
        result = self._load("MissingAsset", asset_type="MATERIAL")
        self.assertEqual(result["status"], "error")
        self.assertIn("not found", result["message"])

    def test_material_assigns_to_active_object(self):
        _register_asset(self.bpy, self._blend, "materials", "Wood")
        obj = _Obj("Hero", "MESH")
        self.bpy.context.active_object = obj
        result = self._load("Wood", asset_type="MATERIAL")
        self.assertEqual(result["status"], "ok")
        self.assertIn("appended", result["message"])
        self.assertEqual([m.name for m in obj.data.materials], ["Wood"])  # appended

    def test_material_replaces_slot_zero(self):
        _register_asset(self.bpy, self._blend, "materials", "Wood")
        obj = _Obj("Hero", "MESH")
        obj.data.materials = ["OldMat"]
        self.bpy.context.active_object = obj
        result = self._load("Wood", asset_type="MATERIAL")
        self.assertEqual(result["status"], "ok")
        self.assertEqual([m.name for m in obj.data.materials], ["Wood"])  # replaced slot 0

    def test_link_mode(self):
        _register_asset(self.bpy, self._blend, "materials", "Wood")
        obj = _Obj("Hero", "MESH")
        self.bpy.context.active_object = obj
        result = self._load("Wood", asset_type="MATERIAL", link_mode="LINK")
        self.assertEqual(result["status"], "ok")
        self.assertIn("linked", result["message"])

    def test_gn_node_group_adds_modifier(self):
        _register_asset(self.bpy, self._blend, "node_groups", "GNGrow",
                        tree_type="GeometryNodeTree")
        obj = _Obj("Hero", "MESH")
        self.bpy.context.active_object = obj
        result = self._load("GNGrow", asset_type="NODETREE")
        self.assertEqual(result["status"], "ok")
        self.assertIn("modifier", result["loaded_into"])
        self.assertEqual(len(obj.modifiers), 1)
        self.assertEqual(obj.modifiers[0].node_group.name, "GNGrow")

    def test_shader_node_group_into_material(self):
        _register_asset(self.bpy, self._blend, "node_groups", "BrickTiles",
                        tree_type="ShaderNodeTree")
        tree = _NodeTree("Mat.001", "ShaderNodeTree")
        mat = _DataBlock("Mat.001", "materials")
        mat.use_nodes = True
        mat.node_tree = tree
        obj = _Obj("Hero", "MESH")
        obj.active_material = mat
        self.bpy.context.active_object = obj
        result = self._load("BrickTiles", asset_type="NODETREE")
        self.assertEqual(result["status"], "ok")
        self.assertIn("material", result["loaded_into"])
        self.assertEqual(len(tree.nodes), 1)
        self.assertEqual(tree.nodes[0].bl_idname, "ShaderNodeGroup")

    def test_compositor_node_group(self):
        _register_asset(self.bpy, self._blend, "node_groups", "CompGrade",
                        tree_type="CompositorNodeTree")
        scene = _Scene()
        self.bpy.context.scene = scene
        result = self._load("CompGrade", asset_type="NODETREE")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(scene.use_nodes, True)
        self.assertEqual(scene.node_tree.nodes[0].bl_idname, "CompositorNodeGroup")

    def test_collection_append_and_position(self):
        _register_asset(self.bpy, self._blend, "collections", "BrickWall")
        scene = _Scene()
        self.bpy.context.scene = scene
        result = self._load("BrickWall", asset_type="COLLECTION", location=(5.0, 0.0, 0.0))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["loaded_into"], "scene")
        self.assertIn("5.0", result["message"])

    def test_object_append_select_and_position(self):
        _register_asset(self.bpy, self._blend, "objects", "Chair")
        scene = _Scene()
        self.bpy.context.scene = scene
        self.bpy.context.view_layer = _ViewLayer()
        result = self._load("Chair", asset_type="OBJECT", location=(1.0, 2.0, 3.0))
        self.assertEqual(result["status"], "ok")
        self.assertIn("(1.0, 2.0, 3.0)", result["message"])
        self.assertEqual(len(scene.collection.objects), 1)
        self.assertTrue(scene.collection.objects[0].select)

    def test_world_assign(self):
        _register_asset(self.bpy, self._blend, "worlds", "Sunset")
        scene = _Scene()
        self.bpy.context.scene = scene
        result = self._load("Sunset", asset_type="WORLD")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["loaded_into"], "scene_world")

    def test_action_assign(self):
        _register_asset(self.bpy, self._blend, "actions", "RunCycle")
        obj = _Obj("Hero", "MESH")
        self.bpy.context.active_object = obj
        result = self._load("RunCycle", asset_type="ACTION")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(obj.animation_data.action.name, "RunCycle")

    def test_auto_detect_type(self):
        _register_asset(self.bpy, self._blend, "materials", "Wood")
        obj = _Obj("Hero", "MESH")
        self.bpy.context.active_object = obj
        result = self._load("Wood")  # asset_type=""
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["asset_type"], "MATERIAL")


class TestWireNodeGroup(unittest.TestCase):
    """`wire_node_group`: insert modes + deterministic socket auto-mapping."""

    def setUp(self):
        _install_bpy(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._blend = _make_lib_path(self._tmp.name)
        _register_library(self.bpy, "MyLib", self._tmp.name)

    def _make_group(self, name, tree_type, inputs=(), outputs=()):
        ng = _NodeTree(name, tree_type)
        ng.inputs = _Sockets(_Socket(n, t) for (n, t) in inputs)
        ng.outputs = _Sockets(_Socket(n, t) for (n, t) in outputs)
        return ng

    def _material_tree(self):
        tree = _NodeTree("Mat.001", "ShaderNodeTree")
        principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
        principled.add_input("Base Color", "RGBA")
        principled.add_input("Roughness", "VALUE")
        principled.add_output("BSDF", "SHADER")
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.add_input("Surface", "SHADER")
        tree.links.new(principled.outputs[0], output.inputs[0])
        tree.nodes.active = principled
        mat = _DataBlock("Mat.001", "materials")
        mat.use_nodes = True
        mat.node_tree = tree
        obj = _Obj("Hero", "MESH")
        obj.active_material = mat
        self.bpy.context.active_object = obj
        self.bpy.context.object = obj
        return tree

    def test_add_top_level(self):
        ng = self._make_group(
            "BrickWall", "ShaderNodeTree",
            inputs=[("Scale", "VALUE")], outputs=[("BSDF", "SHADER")])
        self.bpy.data.node_groups["BrickWall"] = ng
        tree = self._material_tree()

        result = _call(
            "wire_node_group",
            asset_name="BrickWall", tree_type="ShaderNodeTree",
            node_tree_name="", insert_mode="add_top_level",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["links_created"], [])
        self.assertEqual(len(tree.nodes), 3)  # + group node

    def test_real_blender_types_normalized(self):
        """Blender's ``NodeTree.type`` is 'SHADER'/'COMPOSITING'/'GEOMETRY',
        not the friendly names — the toolcode must normalize both sides."""
        _register_asset(self.bpy, self._blend, "node_groups", "RealTypeGroup",
                        tree_type="SHADER")
        tree = self._material_tree()
        tree.type = "SHADER"  # real enum value on the target tree

        for given_type in ("ShaderNodeTree", ""):
            result = _call(
                "wire_node_group",
                library_name="MyLib",
                asset_name="RealTypeGroup", tree_type=given_type,
                node_tree_name="", insert_mode="add_top_level",
            )
            self.assertEqual(
                result["status"], "ok",
                "tree_type={!r}: {:s}".format(given_type, result["message"]),
            )
            self.assertEqual(result["tree_type"], "ShaderNodeTree")
        # One group node inserted per call (2 initial nodes + 2 inserts).
        self.assertEqual(len(tree.nodes), 4)

    def test_replace_active_wraps_principled(self):
        ng = self._make_group(
            "BrickWall", "ShaderNodeTree",
            inputs=[("Base Color", "RGBA"), ("Scale", "VALUE")],
            outputs=[("BSDF", "SHADER")])
        self.bpy.data.node_groups["BrickWall"] = ng
        tree = self._material_tree()
        principled = tree.nodes.get("Node.000")

        result = _call(
            "wire_node_group",
            asset_name="BrickWall", tree_type="ShaderNodeTree",
            insert_mode="replace_active", target_node=principled.name,
        )
        self.assertEqual(result["status"], "ok")
        # Output Surface still receives a SHADER link, now from the group.
        output = tree.nodes.get("Node.001")
        self.assertTrue(output.inputs[0].links)
        self.assertNotIn(principled, tree.nodes)  # wrapped node removed
        self.assertTrue(result["links_created"])

    def test_insert_between(self):
        ng = self._make_group(
            "BrickWall", "ShaderNodeTree",
            inputs=[("Shader", "SHADER")], outputs=[("BSDF", "SHADER")])
        self.bpy.data.node_groups["BrickWall"] = ng
        tree = self._material_tree()
        principled = tree.nodes.get("Node.000")
        output = tree.nodes.get("Node.001")

        result = _call(
            "wire_node_group",
            asset_name="BrickWall", tree_type="ShaderNodeTree",
            insert_mode="insert_between",
            from_node=principled.name, to_node=output.name,
            from_socket="BSDF", to_socket="Surface",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["links_created"]), 2)
        # Original link removed, two new links through the group.
        self.assertEqual(len(tree.links), 2)
        # Output's Surface socket now fed from the group node.
        self.assertEqual(output.inputs[0].links[0].from_node.name, "Node.002")

    def test_insert_between_missing_node_errors(self):
        ng = self._make_group("BrickWall", "ShaderNodeTree", outputs=[("BSDF", "SHADER")])
        self.bpy.data.node_groups["BrickWall"] = ng
        tree = self._material_tree()
        output = tree.nodes.get("Node.001")

        result = _call(
            "wire_node_group",
            asset_name="BrickWall", tree_type="ShaderNodeTree",
            insert_mode="insert_between",
            from_node="Ghost", to_node=output.name,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("not found", result["message"])

    def test_connect_to_output(self):
        ng = self._make_group(
            "BrickWall", "ShaderNodeTree",
            inputs=[("Scale", "VALUE")], outputs=[("BSDF", "SHADER")])
        self.bpy.data.node_groups["BrickWall"] = ng
        tree = self._material_tree()
        output = tree.nodes.get("Node.001")
        # Disconnect so the output Surface is free.
        for link in list(tree.links):
            tree.links.remove(link)

        result = _call(
            "wire_node_group",
            asset_name="BrickWall", tree_type="ShaderNodeTree",
            insert_mode="connect_to_output",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["links_created"]), 1)
        self.assertTrue(output.inputs[0].links)
        self.assertEqual(output.inputs[0].links[0].from_node.name, "Node.002")

    def test_socket_type_mismatch_reports_unmapped(self):
        # The group's only input is GEOMETRY, so the incoming RGBA Base Color
        # link from the Noise node cannot be mapped -> reported, no crash.
        ng = self._make_group(
            "Wrong", "ShaderNodeTree",
            inputs=[("Geo", "GEOMETRY")], outputs=[("BSDF", "SHADER")])
        self.bpy.data.node_groups["Wrong"] = ng

        tree = _NodeTree("Mat.001", "ShaderNodeTree")
        noise = tree.nodes.new("ShaderNodeTexNoise")
        noise.add_output("Color", "RGBA")
        principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
        principled.add_input("Base Color", "RGBA")
        principled.add_input("Roughness", "VALUE")
        principled.add_output("BSDF", "SHADER")
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.add_input("Surface", "SHADER")
        tree.links.new(noise.outputs[0], principled.inputs[0])
        tree.links.new(principled.outputs[0], output.inputs[0])
        tree.nodes.active = principled
        mat = _DataBlock("Mat.001", "materials")
        mat.use_nodes = True
        mat.node_tree = tree
        obj = _Obj("Hero", "MESH")
        obj.active_material = mat
        self.bpy.context.active_object = obj
        self.bpy.context.object = obj

        result = _call(
            "wire_node_group",
            asset_name="Wrong", tree_type="ShaderNodeTree",
            insert_mode="replace_active", target_node=principled.name,
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["unmapped"])

    def test_missing_group_errors(self):
        result = _call(
            "wire_node_group",
            asset_name="Ghost", tree_type="ShaderNodeTree",
            insert_mode="add_top_level",
        )
        self.assertEqual(result["status"], "error")

    def test_unknown_insert_mode_errors(self):
        ng = self._make_group("BrickWall", "ShaderNodeTree")
        self.bpy.data.node_groups["BrickWall"] = ng
        self._material_tree()
        result = _call(
            "wire_node_group",
            asset_name="BrickWall", tree_type="ShaderNodeTree",
            insert_mode="bogus_mode",
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("insert_mode", result["message"])

    def test_undo_push_before_mutation(self):
        ng = self._make_group(
            "BrickWall", "ShaderNodeTree",
            inputs=[("Scale", "VALUE")], outputs=[("BSDF", "SHADER")])
        self.bpy.data.node_groups["BrickWall"] = ng
        self._material_tree()
        _call(
            "wire_node_group",
            asset_name="BrickWall", tree_type="ShaderNodeTree",
            insert_mode="add_top_level",
        )
        self.assertTrue(self.bpy.ops.ed.undo_pushed)


class TestSocketAutoMapping(unittest.TestCase):
    """Deterministic socket matching: exact -> fuzzy -> compatible type."""

    def setUp(self):
        _install_bpy(self)
        self._mod = _load_toolcode("wire_node_group")

    def _pick(self, sockets, hint_name, hint_type, used=(), direction="in", auto_map=True):
        class _Hint:
            pass
        hint = _Hint()
        hint.name = hint_name
        hint.type = hint_type
        return self._mod._pick_socket(
            list(sockets), hint, set(used), auto_map, direction)

    def test_exact_name_wins(self):
        sockets = [_Socket("Scale", "VALUE"), _Socket("Strength", "VALUE")]
        match = self._pick(sockets, "scale", "VALUE")
        self.assertEqual(match.name, "Scale")

    def test_fuzzy_name_substring(self):
        sockets = [_Socket("Brick Scale", "VALUE"), _Socket("Seed", "VALUE")]
        match = self._pick(sockets, "Scale", "VALUE")
        self.assertEqual(match.name, "Brick Scale")

    def test_compatible_type_fallback(self):
        sockets = [_Socket("Strength", "VALUE"), _Socket("Color", "RGBA")]
        match = self._pick(sockets, "Scale", "VALUE")
        self.assertEqual(match.name, "Strength")

    def test_type_incompatible_returns_none(self):
        sockets = [_Socket("BSDF", "SHADER")]
        match = self._pick(sockets, "Color", "RGBA")
        self.assertIsNone(match)

    def test_used_sockets_skipped(self):
        sockets = [_Socket("Scale", "VALUE"), _Socket("Strength", "VALUE")]
        match = self._pick(sockets, "Scale", "VALUE", used={"Scale"})
        self.assertEqual(match.name, "Strength")

    def test_value_to_vector_conversion(self):
        sockets = [_Socket("Vector", "VECTOR")]
        match = self._pick(sockets, "Scale", "VALUE", direction="in")
        self.assertEqual(match.name, "Vector")

    def test_auto_map_disabled(self):
        # With auto_map=False only exact names are considered; the compatible
        # type fallback is skipped.
        sockets = [_Socket("Strength", "VALUE")]
        match = self._pick(sockets, "Scale", "VALUE", auto_map=False)
        self.assertIsNone(match)


class TestGetNodeGroupInterface(unittest.TestCase):
    """`get_node_group_interface`: new + legacy interface serialization."""

    def setUp(self):
        _install_bpy(self)

    def test_new_interface_api(self):
        interface = _Interface([
            NodeTreeInterfaceSocket("Scale", "NodeSocketFloat", "INPUT", default=1.0),
            NodeTreeInterfaceSocket("BSDF", "NodeSocketShader", "OUTPUT"),
        ])
        ng = _NodeTree("BrickWall", "ShaderNodeTree", interface=interface)
        self.bpy.data.node_groups["BrickWall"] = ng

        result = _call("get_node_group_interface", group_name="BrickWall")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["editor_type"], "ShaderNodeTree")
        self.assertEqual(len(result["inputs"]), 1)
        self.assertEqual(result["inputs"][0]["name"], "Scale")
        self.assertEqual(result["inputs"][0]["type"], "FLOAT")
        self.assertEqual(len(result["outputs"]), 1)
        self.assertEqual(result["outputs"][0]["name"], "BSDF")
        self.assertEqual(result["outputs"][0]["type"], "SHADER")

    def test_legacy_inputs_outputs(self):
        ng = _NodeTree("Old", "CompositorNodeTree")
        ng.inputs = _Sockets([_Socket("Image", "IMAGE")])
        ng.outputs = _Sockets([_Socket("Image", "IMAGE")])
        self.bpy.data.node_groups["Old"] = ng

        result = _call("get_node_group_interface", group_name="Old")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["editor_type"], "CompositorNodeTree")
        self.assertEqual(result["inputs"][0]["name"], "Image")
        self.assertEqual(result["inputs"][0]["type"], "IMAGE")

    def test_missing_group(self):
        result = _call("get_node_group_interface", group_name="Ghost")
        self.assertEqual(result["status"], "error")


class TestGetActiveNodeTree(unittest.TestCase):
    """`get_active_node_tree`: tree resolution + serialization."""

    def setUp(self):
        _install_bpy(self)

    def test_resolve_shader_tree(self):
        tree = _NodeTree("Mat.001", "ShaderNodeTree")
        n1 = tree.nodes.new("ShaderNodeTexNoise")
        n1.add_output("Fac", "VALUE")
        mat = _DataBlock("Mat.001", "materials")
        mat.use_nodes = True
        mat.node_tree = tree
        obj = _Obj("Hero", "MESH")
        obj.active_material = mat
        self.bpy.context.object = obj

        result = _call("get_active_node_tree", tree_type="ShaderNodeTree")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tree_name"], "Mat.001")
        self.assertEqual(result["node_count"], 1)
        self.assertEqual(result["nodes"][0]["name"], "Node.000")
        self.assertEqual(result["nodes"][0]["outputs"][0]["type"], "VALUE")

    def test_explicit_node_tree_name(self):
        tree = _NodeTree("Custom", "CompositorNodeTree")
        self.bpy.data.node_groups["Custom"] = tree
        result = _call("get_active_node_tree", node_tree_name="Custom")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tree_name"], "Custom")
        self.assertEqual(result["tree_type"], "CompositorNodeTree")

    def test_no_tree_resolves_error(self):
        self.bpy.context.object = None
        result = _call("get_active_node_tree", tree_type="ShaderNodeTree")
        self.assertEqual(result["status"], "error")

    def test_links_serialized(self):
        tree = _NodeTree("Mat.001", "ShaderNodeTree")
        a = tree.nodes.new("A")
        a.add_output("Out", "VALUE")
        b = tree.nodes.new("B")
        b.add_input("In", "VALUE")
        tree.links.new(a.outputs[0], b.inputs[0])
        mat = _DataBlock("Mat.001", "materials")
        mat.use_nodes = True
        mat.node_tree = tree
        obj = _Obj("Hero", "MESH")
        obj.active_material = mat
        self.bpy.context.object = obj

        result = _call("get_active_node_tree", tree_type="ShaderNodeTree")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["link_count"], 1)
        link = result["links"][0]
        self.assertEqual(link["from_node"], "Node.000")
        self.assertEqual(link["to_node"], "Node.001")


class TestAssetIndex(unittest.TestCase):
    """Phase C metadata index: freshness, lookups, background build."""

    def setUp(self):
        _install_bpy(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._mod = _load_shared("_asset_index_shared")
        # Real stub file that the fingerprints point at.
        self.blend = os.path.join(self._tmp.name, "assets.blend")
        with open(self.blend, "wb") as fh:
            fh.write(b"fixture-blend")

    def _write_index(self, assets, mtime_ns=None, size=None, schema=1):
        stat_i = os.stat(self.blend)
        lib_path = self._tmp.name
        index = {
            "schema": schema,
            "library_path": lib_path,
            "built_at": 1.0,
            "files": {
                "assets.blend": {
                    "mtime_ns": mtime_ns or stat_i.st_mtime_ns,
                    "size": size if size is not None else stat_i.st_size,
                },
            },
            "assets": assets,
        }
        path = self._mod._blmcp_index_path(lib_path, self.bpy)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(index, fh)
        return path

    def test_lookup_from_fresh_index(self):
        self._write_index({
            "Brick_Mat": {
                "name": "Brick_Mat", "type": "MATERIAL", "file": "assets.blend",
                "tags": ["brick"], "description": "brick wall",
                "preferred_import_method": "LINK",
                "use_preferred_import_method": True,
            },
        })
        entry = self._mod._blmcp_index_lookup(
            self._tmp.name, "Brick_Mat", self.bpy)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["type"], "MATERIAL")
        self.assertEqual(entry["preferred_import_method"], "LINK")

    def test_lookup_type_filter(self):
        self._write_index({
            "Crate": {"name": "Crate", "type": "OBJECT", "file": "assets.blend"},
        })
        entry = self._mod._blmcp_index_lookup(
            self._tmp.name, "Crate", self.bpy, asset_type="MATERIAL")
        self.assertIsNone(entry)
        entry = self._mod._blmcp_index_lookup(
            self._tmp.name, "Crate", self.bpy, asset_type="OBJECT")
        self.assertIsNotNone(entry)

    def test_stale_fingerprint_returns_none(self):
        self._write_index({"Crate": {"name": "Crate", "type": "OBJECT",
                                      "file": "assets.blend"}})
        # Bump the file so fingerprints no longer match.  Modify the file in a
        # way that changes both mtime_ns and size.
        with open(self.blend, "wb") as fh:
            fh.write(b"fixture-blend-changed")
        self.assertIsNone(self._mod._blmcp_index_read(self._tmp.name, self.bpy))
        self.assertIsNone(self._mod._blmcp_index_lookup(
            self._tmp.name, "Crate", self.bpy))

    def test_schema_mismatch_returns_none(self):
        self._write_index({"Crate": {"name": "Crate", "type": "OBJECT"}}, schema=99)
        self.assertIsNone(self._mod._blmcp_index_read(self._tmp.name, self.bpy))

    def test_background_build_triggers_and_ttl(self):
        calls = []
        class _FakeSubprocess:
            DEVNULL = -3

            @staticmethod
            def Popen(cmd, **kwargs):
                calls.append((cmd, kwargs))
        original = getattr(self._mod, "subprocess", None)
        self._mod.subprocess = _FakeSubprocess
        self.addCleanup(
            lambda: setattr(self._mod, "subprocess", original))
        self.bpy.app = types.SimpleNamespace(binary_path=self.blend)
        started, msg = self._mod._blmcp_trigger_index_build(self._tmp.name, self.bpy)
        self.assertTrue(started, msg)
        self.assertEqual(len(calls), 1)
        cmd = calls[0][0]
        self.assertIn("--background", cmd)
        # Second call within TTL reports in-progress without respawning.
        started2, msg2 = self._mod._blmcp_trigger_index_build(self._tmp.name, self.bpy)
        self.assertFalse(started2)
        self.assertIn("in progress", msg2)
        self.assertEqual(len(calls), 1)

    def test_no_binary_falls_back_gracefully(self):
        self.bpy.app = types.SimpleNamespace(binary_path="")
        started, msg = self._mod._blmcp_trigger_index_build(self._tmp.name, self.bpy)
        self.assertFalse(started)
        self.assertIn("no Bforartists binary", msg)

    def test_ensure_missing_index_triggers_build(self):
        calls = []
        class _FakeSubprocess2:
            DEVNULL = -3

            @staticmethod
            def Popen(cmd, **kwargs):
                calls.append(cmd)
        original = getattr(self._mod, "subprocess", None)
        self._mod.subprocess = _FakeSubprocess2
        self.addCleanup(
            lambda: setattr(self._mod, "subprocess", original))
        self.bpy.app = types.SimpleNamespace(binary_path=self.blend)
        result = self._mod._blmcp_index_ensure(self._tmp.name, self.bpy)
        self.assertIsNone(result)  # build is async, index not ready yet
        self.assertEqual(len(calls), 1)

    def test_indexer_script_compiles(self):
        compile(self._mod._BLMCP_INDEXER_SCRIPT, "<indexer>", "exec")


def _load_shared(name):
    """Load a non-toolcode shared module (e.g. ``_asset_index_shared``)."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "mcp", "blmcp", "tools", "{:s}.py".format(name),
    )
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    module = types.ModuleType(name)
    exec(compile(source, path, "exec"), module.__dict__)
    return module


class TestAssetSelftests(unittest.TestCase):
    """Phase B in-session diagnostics suite (addon side)."""

    def setUp(self):
        self._ast = _load_addon_module("asset_selftests")

    def test_auto_steps_cover_the_asset_tools(self):
        keys = [key for key, _label, _fn in self._ast._AUTO_STEPS]
        for expected in ("libraries", "search_name", "search_tag", "tags",
                         "load_material", "place_object", "wire_add",
                         "wire_output", "interface"):
            self.assertIn(expected, keys)

    def test_manual_steps_map_to_ui_checklist(self):
        for _key, label, instructions in self._ast.MANUAL_STEPS:
            self.assertTrue(label)
            self.assertTrue(instructions)

    def test_fixture_cleanup_scoped(self):
        # Diagnostics run in the user's LIVE session: the purge must only
        # touch distinctive fixture names, never arbitrary user content.
        self.assertTrue(self._ast._is_fixture_name("Crate_Cube.001"))
        self.assertTrue(self._ast._is_fixture_name("BFACW_WireTree"))
        self.assertFalse(self._ast._is_fixture_name("My_Crate"))
        self.assertFalse(self._ast._is_fixture_name("Hero"))

    def test_results_state_seed(self):
        self.assertEqual(self._ast.get_results(), [])
        self.assertFalse(self._ast.is_running())


def _load_addon_module(name):
    """Load an addon-side module (e.g. ``asset_selftests``) standalone."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "addon", "bfa_coworker", "{:s}.py".format(name),
    )
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    module = types.ModuleType(name)
    exec(compile(source, path, "exec"), module.__dict__)
    return module



class _WorkspaceStub:
    """Minimal workspace with screens (for the space-type lookup)."""

    def __init__(self, name):
        self.name = name
        self.screens = []


class _WorkspacesStub:
    """dict-like `bpy.data.workspaces` with .get() and iteration."""

    def __init__(self, names):
        self._map = {n: _WorkspaceStub(n) for n in names}

    def get(self, name, default=None):
        return self._map.get(name, default)

    def __iter__(self):
        return iter(self._map.values())


class _WindowStub:
    def __init__(self, workspace):
        self.workspace = workspace
        self.width = 800
        self.height = 600


class _AreaStub:
    def __init__(self, area_type, width=100, height=100):
        self.type = area_type
        self.width = width
        self.height = height
        self.ui_type = ""


class _ScreenStub:
    def __init__(self, name, areas):
        self.name = name
        self.areas = areas


class TestTabSwitchTools(unittest.TestCase):
    """jump_to_tab_by_name / jump_to_tab_by_space_type toolcodes."""

    def setUp(self):
        _install_bpy(self)
        self.bpy.app = types.SimpleNamespace(background=False)

    def _call_by_name(self, tool_name, **params):
        """Call a tab toolcode whose Params itself has a `name` field."""
        module = _load_toolcode(tool_name)
        return module.main(module.Params(**params))._asdict()

    def _install_workspaces(self, names):
        ws = _WorkspacesStub(names)
        self.bpy.data.workspaces = ws
        self.bpy.context.window = _WindowStub(ws._map[names[0]])
        return ws

    def test_by_name_switches_tab(self):
        self._install_workspaces(["Main", "Modeling", "Geometry Nodes"])
        result = self._call_by_name("jump_to_tab_by_name", name="Modeling")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["workspace"], "Modeling")
        self.assertEqual(self.bpy.context.window.workspace.name, "Modeling")
        self.assertIn("available_workspaces", result)
        self.assertIn("Geometry Nodes", result["available_workspaces"])

    def test_by_name_case_insensitive(self):
        self._install_workspaces(["Main", "Modeling"])
        result = self._call_by_name("jump_to_tab_by_name", name="modeling")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["workspace"], "Modeling")

    def test_by_name_not_found(self):
        self._install_workspaces(["Main", "Modeling"])
        result = self._call_by_name("jump_to_tab_by_name", name="Nope")
        self.assertEqual(result["status"], "error")
        self.assertIn("not found", result["message"])
        self.assertIn("Main", result["available_workspaces"])

    def test_by_name_background_mode(self):
        self._install_workspaces(["Main"])
        self.bpy.app.background = True
        result = self._call_by_name("jump_to_tab_by_name", name="Main")
        self.assertEqual(result["status"], "error")
        self.assertIn("background", result["message"])

    def test_by_space_type_existing(self):
        ws = self._install_workspaces(["Main", "Layout"])
        ws._map["Main"].screens = [_ScreenStub("Main", [_AreaStub("PROPERTIES")])]
        ws._map["Layout"].screens = [_ScreenStub("Layout", [_AreaStub("VIEW_3D")])]
        result = _call(
            "jump_to_tab_by_space_type", space_type="VIEW_3D", allow_edits=False)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["workspace"], "Layout")
        self.assertEqual(self.bpy.context.window.workspace.name, "Layout")

    def test_by_space_type_not_found_no_edits(self):
        ws = self._install_workspaces(["Main"])
        ws._map["Main"].screens = [_ScreenStub("Main", [_AreaStub("VIEW_3D")])]
        result = _call(
            "jump_to_tab_by_space_type", space_type="PROPERTIES", allow_edits=False)
        self.assertEqual(result["status"], "error")
        self.assertIn("space type", result["message"])
        self.assertIn("VIEW_3D", result["available_space_types"])

    def test_by_space_type_creates(self):
        ws = self._install_workspaces(["Main"])
        ws._map["Main"].screens = [_ScreenStub("Main", [_AreaStub("VIEW_3D")])]
        self.bpy.context.screen = ws._map["Main"].screens[0]

        def _duplicate():
            new_name = "Texture Paint"
            new_ws = _WorkspaceStub(new_name)
            new_ws.screens = [self.bpy.context.screen]
            ws._map[new_name] = new_ws
            self.bpy.context.window.workspace = new_ws

        self.bpy.ops.workspace = types.SimpleNamespace(duplicate=_duplicate)
        result = _call(
            "jump_to_tab_by_space_type", space_type="TEXT_EDITOR", allow_edits=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["created"], True)
        self.assertEqual(result["space_type"], "TEXT_EDITOR")
        self.assertEqual(self.bpy.context.window.workspace.name, "Text Editor")


@unittest.skipIf(
    not os.environ.get("BFACW_RUN_BRIDGE_TESTS") == "1",
    "skipped unless BFACW_RUN_BRIDGE_TESTS=1 (needs addon internals)",
)
class TestBridgeExecThreading(unittest.TestCase):
    """Verify _execute_code chooses inline (main-thread) exec for toolcode."""

    def setUp(self):
        src_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "addon", "bfa_coworker", "mcp_to_blender_server.py")
        with open(src_path, "r", encoding="utf-8") as fh:
            self.source = fh.read()

    def test_toolcode_marker_runs_inline(self):
        idx = self.source.find("is_toolcode = ")
        self.assertGreater(idx, 0)
        # The inline branch executes in the *calling* thread — no
        # threading.Thread spawn for the trusted-marker path.
        segment = self.source[idx:idx + 400]
        self.assertIn("if is_toolcode:", segment)




if __name__ == "__main__":
    unittest.main()
