# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Node trees as code (#480), the Blender side: read a tree into specs, build a tree from specs.

Spliced into ``get_node_tree_as_code`` and ``build_node_tree_from_code`` after
``_node_code_format.py`` (``# @include_begin``).  Everything is found through
Blender's own RNA (``bl_rna.properties``, ``functions["new"]``), not a
hand-kept list: a node's *settings* are its properties beyond the base
``Node`` ones; its *defaults* are those of a new node of the same type in a
temporary tree (removed again before the tool returns).
"""

try:
    parse_code  # noqa: B018 -- defined when spliced into a tool-code
except NameError:  # imported by the server or the tests
    from blmcp.tools._node_code_format import *  # noqa: F401,F403  pylint: disable=wildcard-import,unused-wildcard-import


TREE_TYPE_ALIASES = {
    "GEOMETRY": "GeometryNodeTree", "GEOMETRY_NODES": "GeometryNodeTree", "GEOMETRYNODETREE": "GeometryNodeTree",
    "SHADER": "ShaderNodeTree", "MATERIAL": "ShaderNodeTree", "SHADERNODETREE": "ShaderNodeTree",
    "COMPOSITING": "CompositorNodeTree", "COMPOSITOR": "CompositorNodeTree",
    "COMPOSITORNODETREE": "CompositorNodeTree",
    "TEXTURE": "TextureNodeTree", "TEXTURENODETREE": "TextureNodeTree",
}
EDITOR_NAMES = {
    "GeometryNodeTree": "Geometry Nodes",
    "ShaderNodeTree": "Shader Editor",
    "CompositorNodeTree": "Compositor",
}

# Base Node properties printed when they differ from a new node's.
_BASE_KEYS = ("label", "mute", "hide", "color", "width", "warning_propagation", "show_options", "show_preview",
              "show_texture")
_SPECIAL_KEYS = frozenset(_BASE_KEYS + ("paired_output", "outputs", "hidden_inputs", "hidden_outputs"))
# Properties that are UI state or derived data, not settings.
_SKIP_PROPS = frozenset((
    "paired_output", "inspection_index", "ui_shortcut", "enum_definition", "textbox_state", "bytecode",
    "bytecode_hash", "has_layers", "has_views", "internal_links", "rna_type",
))
_SKIP_TREE_PROPS = frozenset(("nodes", "links", "interface", "annotation", "animation_data", "type", "view_center",
                              "rna_type"))
_SKIP_IFACE = frozenset(("rna_type", "item_type", "parent", "position", "index", "name", "identifier", "socket_type",
                         "in_out", "select", "bl_socket_idname", "default_value", "persistent_uid",
                         "interface_items"))
_ABSTRACT_NODES = frozenset(("Node", "NodeInternal", "GeometryNode", "ShaderNode", "CompositorNode", "FunctionNode",
                             "TextureNode", "NodeCustomGroup", "GeometryNodeCustomGroup", "ShaderNodeCustomGroup",
                             "CompositorNodeCustomGroup"))
_ID_COLLECTIONS = {
    "Object": "objects", "Image": "images", "Collection": "collections", "Material": "materials",
    "Texture": "textures", "VectorFont": "fonts", "NodeTree": "node_groups", "Text": "texts", "Scene": "scenes",
    "MovieClip": "movieclips", "Mask": "masks", "Sound": "sounds", "Mesh": "meshes", "World": "worlds",
}
_SOCKET_TYPES = ("Float", "Int", "Bool", "Vector", "Color", "String", "Rotation", "Matrix", "Menu", "Object",
                 "Collection", "Image", "Material", "Texture", "Geometry", "Shader", "Bundle", "Closure", "Font")
_MISSING = object()


def tree_type_name(value):
    """``GEOMETRY`` / ``geometry`` / ``GeometryNodeTree`` -> ``GeometryNodeTree`` (unknown: as given)."""
    text = str(value or "")
    return TREE_TYPE_ALIASES.get(text.upper().replace(" ", "_"), text)


def node_types(bpy):
    """{bl_idname: label} of every node type Blender registers (cached)."""
    cache = getattr(node_types, "_cache", None)
    if cache is not None:
        return cache
    out = {}
    for attr in dir(bpy.types):
        cls = getattr(bpy.types, attr, None)
        try:
            if not (isinstance(cls, type) and issubclass(cls, bpy.types.Node)):
                continue
        except TypeError:
            continue
        idname = getattr(cls, "bl_idname", "") or attr
        if idname in _ABSTRACT_NODES:
            continue
        try:
            label = cls.bl_rna.name
        except AttributeError:
            label = ""
        out[idname] = label or idname
    node_types._cache = out
    return out


def _is_id_type(srna):
    while srna is not None:
        if srna.identifier == "ID":
            return True
        srna = srna.base
    return False


def _is_node_type(srna):
    while srna is not None:
        if srna.identifier == "Node":
            return True
        srna = srna.base
    return False


def id_lookup(bpy, type_identifier, name):
    """The datablock *name* of RNA type *type_identifier* (None when missing)."""
    coll = getattr(bpy.data, _ID_COLLECTIONS.get(type_identifier, ""), None)
    if coll is None:
        for attr in _ID_COLLECTIONS.values():
            item = getattr(bpy.data, attr).get(name)
            if item is not None and _is_type_or_sub(item.bl_rna, type_identifier):
                return item
        return None
    return coll.get(name)


def _is_type_or_sub(srna, identifier):
    while srna is not None:
        if srna.identifier == identifier:
            return True
        srna = srna.base
    return False


def plain(value):
    """A Blender value as a format value (vectors -> tuples, datablocks -> names)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (set, frozenset)):
        return set(value)
    rna = getattr(value, "bl_rna", None)
    if rna is not None:
        return value.name if _is_id_type(rna) else None
    try:
        return tuple(plain(v) for v in value)
    except TypeError:
        return str(value)


def _same(a, b):
    return format_value(a) == format_value(b)


def _prop_default(prop):
    if prop.type in ("BOOLEAN", "INT", "FLOAT") and getattr(prop, "array_length", 0):
        return tuple(prop.default_array)
    if prop.type == "ENUM":
        return set(prop.default_flag) if prop.is_enum_flag else prop.default
    if prop.type in ("POINTER", "COLLECTION"):
        return None
    return prop.default


# ---------------------------------------------------------------------------
# Finding the tree (same addressing as get_active_node_tree)


def user_names(coll):
    return [i.name for i in coll if not i.name.startswith(".coworker_")]


def resolve_target(bpy, tree_type="", node_tree_name="", material_name="", object_name="", modifier_name=""):
    """``(tree, kind, name, "")`` or ``(None, "", "", why)``.  Never creates a tree.

    *kind* is "material" for a material's tree (``name`` = the material), else "tree".
    """
    wanted = tree_type_name(tree_type) if tree_type else ""
    if wanted and wanted not in EDITOR_NAMES:
        return None, "", "", "Unknown tree type {!r}: use geometry, shader or compositor".format(tree_type)
    if node_tree_name:
        tree = bpy.data.node_groups.get(node_tree_name)
        if tree is None:
            return None, "", "", "No node group named {!r}{:s}".format(
                node_tree_name, choices_text(node_tree_name, user_names(bpy.data.node_groups)))
        if wanted and tree.bl_idname != wanted:
            return None, "", "", "'{:s}' is a {:s}, not a {:s}".format(tree.name, tree.bl_idname, wanted)
        return tree, "tree", tree.name, ""
    if material_name:
        mat = bpy.data.materials.get(material_name)
        if mat is None:
            return None, "", "", "No material named {!r}{:s}".format(
                material_name, choices_text(material_name, user_names(bpy.data.materials)))
        if mat.node_tree is None:
            return None, "", "", "Material '{:s}' has no node tree".format(mat.name)
        return mat.node_tree, "material", mat.name, ""
    if object_name:
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            return None, "", "", "No object named {!r}{:s}".format(
                object_name, choices_text(object_name, user_names(bpy.data.objects)))
    else:
        obj = getattr(bpy.context, "object", None)
        if obj is None:
            view_layer = getattr(bpy.context, "view_layer", None)
            obj = view_layer.objects.active if view_layer is not None else None
    order = [wanted] if wanted else ["ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree"]
    if object_name and not wanted:
        order = ["GeometryNodeTree", "ShaderNodeTree"]
    for candidate in order:
        if candidate == "GeometryNodeTree" and obj is not None:
            mods = [m for m in obj.modifiers if m.type == "NODES" and m.node_group is not None]
            if modifier_name:
                mods = [m for m in mods if m.name == modifier_name]
            if mods:
                return mods[0].node_group, "tree", mods[0].node_group.name, ""
        elif candidate == "ShaderNodeTree" and obj is not None and not modifier_name:
            mat = obj.active_material
            if mat is not None and mat.node_tree is not None:
                return mat.node_tree, "material", mat.name, ""
        elif candidate == "CompositorNodeTree" and not object_name:
            scene = bpy.context.scene
            # 5.x: the compositor is a node group on the scene; read only -- never create one.
            tree = getattr(scene, "compositing_node_group", None)
            if tree is not None:
                return tree, "tree", tree.name, ""
    if wanted == "CompositorNodeTree":
        return None, "", "", "The scene has no compositor node tree (create one in the Compositor first)"
    if wanted == "GeometryNodeTree":
        return None, "", "", "{:s} has no Geometry Nodes modifier{:s}".format(
            "'{:s}'".format(obj.name) if obj is not None else "No active object:",
            " named {!r}".format(modifier_name) if modifier_name else "")
    if wanted == "ShaderNodeTree":
        return None, "", "", "No material: give material_name, or an object with a material"
    return None, "", "", ("Could not find a node tree: no active material, no Geometry Nodes modifier "
                          "and no compositor tree; give node_tree_name or material_name")


# ---------------------------------------------------------------------------
# Scratch trees (defaults come from new nodes)


class Scratch:
    """Temporary trees to compare against new nodes; ``close()`` removes them."""

    def __init__(self, bpy):
        self.bpy = bpy
        self.trees = {}
        self.fresh = {}
        self.iface = {}

    def tree(self, tree_type):
        tree = self.trees.get(tree_type)
        if tree is None:
            import uuid
            tree = self.bpy.data.node_groups.new(".coworker_scratch_" + uuid.uuid4().hex[:8], tree_type)
            self.trees[tree_type] = tree
        return tree

    def fresh_node(self, tree_type, bl_idname):
        key = (tree_type, bl_idname)
        if key not in self.fresh:
            try:
                self.fresh[key] = self.tree(tree_type).nodes.new(bl_idname)
            except Exception:  # pylint: disable=broad-except
                self.fresh[key] = None
        return self.fresh[key]

    def temp_node(self, tree_type, bl_idname):
        try:
            return self.tree(tree_type).nodes.new(bl_idname)
        except Exception:  # pylint: disable=broad-except
            return None

    def drop(self, tree_type, node):
        try:
            self.tree(tree_type).nodes.remove(node)
        except Exception:  # pylint: disable=broad-except
            pass

    def fresh_iface(self, tree_type, socket_type, in_out):
        key = (tree_type, socket_type, in_out)
        if key not in self.iface:
            try:
                self.iface[key] = self.tree(tree_type).interface.new_socket(
                    "x", in_out=in_out, socket_type=socket_type)
            except Exception:  # pylint: disable=broad-except
                self.iface[key] = None
        return self.iface[key]

    def fresh_panel(self, tree_type):
        key = (tree_type, "PANEL", "")
        if key not in self.iface:
            try:
                self.iface[key] = self.tree(tree_type).interface.new_panel("x")
            except Exception:  # pylint: disable=broad-except
                self.iface[key] = None
        return self.iface[key]

    def close(self):
        for tree in self.trees.values():
            try:
                self.bpy.data.node_groups.remove(tree)
            except Exception:  # pylint: disable=broad-except
                pass
        self.trees.clear()
        self.fresh.clear()
        self.iface.clear()


# ---------------------------------------------------------------------------
# Settings (RNA properties beyond the base Node ones)


def _base_names(bpy):
    cache = getattr(_base_names, "_cache", None)
    if cache is None:
        cache = frozenset(p.identifier for p in bpy.types.Node.bl_rna.properties)
        _base_names._cache = cache
    return cache


def setting_props(bpy, node):
    """[(prop, kind)] with kind "value", "id", "struct" or "items"."""
    base = _base_names(bpy)
    out = []
    for prop in node.bl_rna.properties:
        ident = prop.identifier
        if ident in base or ident in _SKIP_PROPS or ident.startswith(("active_", "bl_")):
            continue
        if prop.type == "COLLECTION":
            srna = prop.srna
            if srna is not None and "new" in srna.functions:
                out.append((prop, "items"))
        elif prop.type == "POINTER":
            fixed = prop.fixed_type
            if _is_node_type(fixed):
                continue
            if _is_id_type(fixed):
                if not prop.is_readonly:
                    out.append((prop, "id"))
            elif prop.is_readonly:
                out.append((prop, "struct"))
        elif not prop.is_readonly:
            out.append((prop, "value"))
    return out


def _read_value(owner, prop):
    value = getattr(owner, prop.identifier)
    if prop.type == "ENUM" and prop.is_enum_flag:
        return set(value)
    return plain(value)


def _item_args(prop):
    """(new() argument names with "name" first, item writable extras)."""
    fn = prop.srna.functions["new"]
    args = [q.identifier for q in fn.parameters if not q.is_output]
    if "name" in args:
        args = ["name"] + [a for a in args if a != "name"]
    extras = []
    for q in prop.fixed_type.properties:
        if q.identifier in args or q.identifier in ("rna_type", "identifier", "color", "select") or q.is_readonly:
            continue
        if q.type in ("POINTER", "COLLECTION"):
            continue
        extras.append(q)
    return args, extras


def read_items(prop, coll):
    args, extras = _item_args(prop)
    entries = []
    for item in coll:
        entry = [plain(getattr(item, a)) for a in args]
        extra = {}
        for q in extras:
            value = _read_value(item, q)
            if not _same(value, _prop_default(q)):
                extra[q.identifier] = value
        if extra:
            entry.append(extra)
        entries.append(tuple(entry))
    if not args and all(not e for e in entries):
        return len(entries)
    if all(len(e) == 1 for e in entries):
        return [e[0] for e in entries]
    return list(entries)


def _read_ramp(ramp, ref):
    fields = []
    for key in ("color_mode", "interpolation", "hue_interpolation"):
        value = getattr(ramp, key)
        if ref is None or value != getattr(ref, key):
            fields.append((key, value))
    elements = [(plain(e.position), plain(e.color)) for e in ramp.elements]
    ref_elements = [(plain(e.position), plain(e.color)) for e in ref.elements] if ref is not None else None
    if not _same(elements, ref_elements):
        fields.append(("elements", elements))
    return fields


def _read_curves(mapping):
    curves = []
    for curve in mapping.curves:
        points = []
        for p in curve.points:
            x, y = plain(p.location)
            points.append((x, y) if p.handle_type == "AUTO" else (x, y, p.handle_type))
        curves.append(points)
    return curves


def read_struct(obj, ref, depth=0):
    """A nested structure's values that differ from *ref* (a new node's), or None."""
    if obj is None:
        return None
    ident = obj.bl_rna.identifier
    if ident == "ColorRamp":
        fields = _read_ramp(obj, ref)
        return Struct(ident, fields) if fields else None
    fields = []
    for prop in obj.bl_rna.properties:
        key = prop.identifier
        if key == "rna_type":
            continue
        if prop.type == "COLLECTION":
            if ident == "CurveMapping" and key == "curves":
                curves = _read_curves(obj)
                if ref is None or not _same(curves, _read_curves(ref)):
                    fields.append((key, curves))
            continue
        if prop.type == "POINTER":
            sub = getattr(obj, key)
            if _is_id_type(prop.fixed_type):
                if prop.is_readonly:
                    continue
                value = plain(sub)
                if ref is None or not _same(value, plain(getattr(ref, key))):
                    fields.append((key, value))
            elif prop.is_readonly and depth < 2 and sub is not None and not _is_node_type(prop.fixed_type):
                nested = read_struct(sub, getattr(ref, key, None) if ref is not None else None, depth + 1)
                if nested is not None:
                    fields.append((key, nested))
            continue
        if prop.is_readonly:
            continue
        try:
            value = _read_value(obj, prop)
        except Exception:  # pylint: disable=broad-except
            continue
        ref_value = _read_value(ref, prop) if ref is not None else _prop_default(prop)
        if not _same(value, ref_value):
            fields.append((key, value))
    return Struct(ident, fields) if fields else None


def _unused_choice(node, prop):
    """An enum with nothing to choose: ``layer`` while ``has_layers`` is False, or no valid value."""
    flag = "has_" + prop.identifier + "s"
    if hasattr(node, flag) and not getattr(node, flag):
        return True
    return prop.type == "ENUM" and not prop.is_enum_flag and getattr(node, prop.identifier) == ""


def read_settings(bpy, node, fresh):
    """[(key, value)] of the settings that differ from *fresh* (a new node of the same type)."""
    out = []
    for prop, kind in setting_props(bpy, node):
        key = prop.identifier
        if kind == "value" and _unused_choice(node, prop):
            continue
        try:
            if kind == "items":
                value = read_items(prop, getattr(node, key))
                ref = read_items(prop, getattr(fresh, key)) if fresh is not None else None
            elif kind == "struct":
                value = read_struct(getattr(node, key), getattr(fresh, key, None) if fresh is not None else None)
                if value is not None:
                    out.append((key, value))
                continue
            elif kind == "id":
                value = plain(getattr(node, key))
                ref = plain(getattr(fresh, key)) if fresh is not None else None
            else:
                value = _read_value(node, prop)
                ref = _read_value(fresh, prop) if fresh is not None else _prop_default(prop)
        except Exception:  # pylint: disable=broad-except
            continue
        if not _same(value, ref):
            out.append((key, value))
    return out


# ---------------------------------------------------------------------------
# Reading


def _sockets(sockets):
    return [(s.identifier, s.name, not s.is_unavailable and s.bl_idname != "NodeSocketVirtual") for s in sockets]


def _socket_index(sockets, socket):
    ptr = socket.as_pointer()
    for i, s in enumerate(sockets):
        if s.as_pointer() == ptr:
            return i
    return -1


def _socket_default(sock):
    """The default a socket gets with no reference node: its RNA default."""
    prop = sock.bl_rna.properties.get("default_value")
    return _prop_default(prop) if prop is not None else None


def _node_order(tree):
    """Nodes (frames last) in a stable topological order: Kahn's algorithm, lowest index first."""
    nodes = [n for n in tree.nodes if n.bl_idname != "NodeFrame"]
    index = {n.as_pointer(): i for i, n in enumerate(nodes)}
    preds = {i: set() for i in range(len(nodes))}
    for link in tree.links:
        a = index.get(link.from_node.as_pointer())
        b = index.get(link.to_node.as_pointer())
        if a is not None and b is not None and a != b:
            preds[b].add(a)
    done = []
    placed = set()
    import heapq
    ready = [i for i in range(len(nodes)) if not preds[i]]
    heapq.heapify(ready)
    succ = {i: set() for i in range(len(nodes))}
    for b, ps in preds.items():
        for a in ps:
            succ[a].add(b)
    remaining = {i: len(ps) for i, ps in preds.items()}
    while ready:
        i = heapq.heappop(ready)
        if i in placed:
            continue
        placed.add(i)
        done.append(i)
        for b in succ[i]:
            remaining[b] -= 1
            if remaining[b] == 0:
                heapq.heappush(ready, b)
    done.extend(i for i in range(len(nodes)) if i not in placed)  # a cycle (invalid tree)
    frames = [n for n in tree.nodes if n.bl_idname == "NodeFrame"]
    return [nodes[i] for i in done] + frames


class ReadCtx:
    def __init__(self, bpy, scratch, tree):
        self.bpy = bpy
        self.scratch = scratch
        self.tree = tree
        self.tree_type = tree.bl_idname
        self.known = node_types(bpy)
        self.links_to = {}
        for link in tree.links:
            self.links_to.setdefault(link.to_socket.as_pointer(), []).append(link)


def _base_args(node, fresh):
    args = []
    if node.label:
        args.append(("label", node.label, False))
    if node.mute:
        args.append(("mute", True, False))
    if node.hide:
        args.append(("hide", True, False))
    if node.use_custom_color:
        args.append(("color", plain(node.color), False))
    if fresh is not None:
        if abs(node.width - fresh.width) > 0.5:
            args.append(("width", round(float(node.width), 1), False))
        for key in ("warning_propagation", "show_options", "show_preview", "show_texture"):
            if hasattr(node, key) and getattr(node, key) != getattr(fresh, key):
                args.append((key, plain(getattr(node, key)), False))
    return args


def read_node(node, ctx):
    """A node as a :class:`NodeSpec`."""
    tree_type = ctx.tree_type
    fresh = ctx.scratch.fresh_node(tree_type, node.bl_idname)
    if node.bl_idname == "NodeFrame":
        args = _base_args(node, fresh)
        args.extend((k, v, False) for k, v in read_settings(ctx.bpy, node, fresh))
        children = [n.name for n in ctx.tree.nodes if n.parent is not None and n.parent.as_pointer() == node.as_pointer()]
        return NodeSpec(node.name, "Frame", args, is_frame=True, children=children)
    args = _base_args(node, fresh)
    paired = getattr(node, "paired_output", None)
    if paired is not None:
        args.append(("paired_output", Ref(paired.name), False))
    settings = read_settings(ctx.bpy, node, fresh)
    args.extend((k, v, False) for k, v in settings)
    setting_names = {p.identifier for p, _k in setting_props(ctx.bpy, node)} | _SPECIAL_KEYS
    group_io = node.bl_idname in ("NodeGroupInput", "NodeGroupOutput")
    ref_node = fresh
    temp = None
    if settings and not group_io:
        temp = ctx.scratch.temp_node(tree_type, node.bl_idname)
        if temp is not None:
            sink = BuildReport()
            apply_settings(ctx.bpy, temp, settings, sink, {}, tree_type)
            ref_node = temp
    try:
        infos = _sockets(node.inputs)
        ref_inputs = {s.identifier: s for s in ref_node.inputs} if ref_node is not None else {}
        for i, sock in enumerate(node.inputs):
            if sock.bl_idname == "NodeSocketVirtual":
                continue
            key = socket_key(i, infos)
            quoted = key in setting_names or not is_ident(key)
            links = ctx.links_to.get(sock.as_pointer())
            if links:
                links = sorted(links, key=lambda l: getattr(l, "multi_input_sort_id", 0))
                refs = [_link_ref(l) for l in links]
                args.append((key, refs if sock.is_multi_input else refs[0], quoted))
                continue
            if group_io or sock.is_unavailable or not hasattr(sock, "default_value"):
                continue
            value = plain(sock.default_value)
            ref_sock = ref_inputs.get(sock.identifier)
            ref_value = plain(ref_sock.default_value) if ref_sock is not None and hasattr(ref_sock, "default_value") \
                else _socket_default(sock)
            if not _same(value, ref_value):
                args.append((key, value, quoted))
        if not group_io:
            outs = {}
            ref_outputs = {s.identifier: s for s in ref_node.outputs} if ref_node is not None else {}
            infos_out = _sockets(node.outputs)
            for i, sock in enumerate(node.outputs):
                if sock.bl_idname == "NodeSocketVirtual" or not hasattr(sock, "default_value"):
                    continue
                ref_sock = ref_outputs.get(sock.identifier)
                if ref_sock is None or not hasattr(ref_sock, "default_value"):
                    continue
                value = plain(sock.default_value)
                if not _same(value, plain(ref_sock.default_value)):
                    outs[socket_key(i, infos_out)] = value
            if outs:
                args.append(("outputs", outs, False))
        # Sockets hidden in the editor (Ctrl+H), when that differs from a new node's.
        for side, key in (("inputs", "hidden_inputs"), ("outputs", "hidden_outputs")):
            sockets = getattr(node, side)
            ref = {s.identifier: s.hide for s in getattr(ref_node, side)} if ref_node is not None else {}
            hidden = [i for i, s in enumerate(sockets) if s.bl_idname != "NodeSocketVirtual" and s.hide]
            if any(s.hide != ref.get(s.identifier, False) for s in sockets if s.bl_idname != "NodeSocketVirtual"):
                infos_side = _sockets(sockets)
                args.append((key, [socket_key(i, infos_side) for i in hidden], False))
    finally:
        if temp is not None:
            ctx.scratch.drop(tree_type, temp)
    return NodeSpec(node.name, short_type_name(node.bl_idname, tree_type, ctx.known), args)


def _link_ref(link):
    node = link.from_node
    index = _socket_index(node.outputs, link.from_socket)
    key = socket_key(index, _sockets(node.outputs)) if index >= 0 else link.from_socket.identifier
    return Ref(node.name, key, muted=bool(getattr(link, "is_muted", False)))


def _iface_options(item, fresh):
    options = []
    for prop in item.bl_rna.properties:
        key = prop.identifier
        if key in _SKIP_IFACE or prop.is_readonly or prop.type in ("POINTER", "COLLECTION"):
            continue
        try:
            value = _read_value(item, prop)
        except Exception:  # pylint: disable=broad-except
            continue
        ref = _MISSING
        if fresh is not None and hasattr(fresh, key):
            try:
                ref = _read_value(fresh, prop)
            except Exception:  # pylint: disable=broad-except
                ref = _MISSING
        if ref is _MISSING:
            ref = _prop_default(prop)
        if not _same(value, ref):
            options.append((key, value))
    return options


def _short_socket_type(socket_type):
    return socket_type[len("NodeSocket"):] if socket_type.startswith("NodeSocket") else socket_type


def read_interface(tree, scratch):
    items = list(tree.interface.items_tree)
    out = []
    if not items:
        return out
    root = items[0].parent.as_pointer() if items[0].parent is not None else 0
    stack = [root]
    for item in items:
        parent = item.parent.as_pointer() if item.parent is not None else 0
        while len(stack) > 1 and stack[-1] != parent:
            out.append(IfaceSpec("end"))
            stack.pop()
        if item.item_type == "PANEL":
            out.append(IfaceSpec("panel", name=item.name,
                                 options=_iface_options(item, scratch.fresh_panel(tree.bl_idname))))
            stack.append(item.as_pointer())
            continue
        in_out = "input" if item.in_out == "INPUT" else "output"
        fresh = scratch.fresh_iface(tree.bl_idname, item.socket_type, item.in_out)
        options = _iface_options(item, fresh)
        has_default = False
        default = None
        if hasattr(item, "default_value"):
            default = plain(item.default_value)
            ref = plain(fresh.default_value) if fresh is not None and hasattr(fresh, "default_value") else None
            has_default = not _same(default, ref)
        out.append(IfaceSpec("socket", in_out, item.name, _short_socket_type(item.socket_type), options,
                             default if has_default else None, has_default))
    while len(stack) > 1:
        out.append(IfaceSpec("end"))
        stack.pop()
    return out


def read_tree_props(tree, scratch):
    fresh = scratch.tree(tree.bl_idname)
    out = []
    for prop in tree.bl_rna.properties:
        key = prop.identifier
        if key in _SKIP_TREE_PROPS or key.startswith("bl_") or prop.is_readonly \
                or prop.type in ("POINTER", "COLLECTION"):
            continue
        if _is_id_prop(tree, key):
            continue
        try:
            value = _read_value(tree, prop)
            ref = _read_value(fresh, prop)
        except Exception:  # pylint: disable=broad-except
            continue
        if not _same(value, ref):
            out.append((key, value))
    return out


def _is_id_prop(tree, key):
    import bpy  # pylint: disable=import-error
    cache = getattr(_is_id_prop, "_cache", None)
    if cache is None:
        cache = frozenset(p.identifier for p in bpy.types.ID.bl_rna.properties)
        _is_id_prop._cache = cache
    return key in cache


def read_tree(bpy, tree, scratch, kind="tree", name="", only=None):
    """A tree as a :class:`TreeSpec` (*only*: node names to print, None = all)."""
    ctx = ReadCtx(bpy, scratch, tree)
    spec = TreeSpec(kind, name or tree.name, tree.bl_idname, read_tree_props(tree, scratch),
                    read_interface(tree, scratch) if only is None else [])
    for node in _node_order(tree):
        if only is not None and node.name not in only:
            continue
        spec.nodes.append(read_node(node, ctx))
    return spec


def used_groups(tree):
    """Node groups *tree* uses (nested first, each once), excluding linked-library ones."""
    out = []
    seen = {tree.as_pointer()}

    def walk(t):
        for node in t.nodes:
            group = getattr(node, "node_tree", None)
            if group is None or group.as_pointer() in seen:
                continue
            seen.add(group.as_pointer())
            walk(group)
            if group.library is None:
                out.append(group)

    walk(tree)
    return out


# ---------------------------------------------------------------------------
# Building


class BuildReport:
    """What a build couldn't do: ``errors`` stop it, ``not_set`` are listed."""

    def __init__(self):
        self.errors = []
        self.not_set = []

    def sorted_errors(self):
        import re
        def line_of(text):
            m = re.match(r"line (\d+):", text)
            return int(m.group(1)) if m else 0
        return sorted(self.errors, key=line_of)

    def error(self, line, text):
        self.errors.append(("line {:d}: ".format(line) if line else "") + text)

    def skip(self, line, text):
        self.not_set.append(("line {:d}: ".format(line) if line else "") + text)


def choices_text(word, names, aliases=None):
    picks = nearest_names(word, [n for n in names if n], aliases=aliases)
    return " -- nearest: " + ", ".join(picks) if picks else ""


def _coerce_id(bpy, prop_or_type, value):
    type_identifier = prop_or_type if isinstance(prop_or_type, str) else prop_or_type.fixed_type.identifier
    if value is None or value == "":
        return None, True
    if not isinstance(value, str):
        return None, False
    found = id_lookup(bpy, type_identifier, value)
    return found, found is not None


def _apply_ramp(ramp, struct):
    for key, value in struct.fields:
        if key == "elements":
            elements = [tuple(e) for e in value]
            if not elements:
                continue
            while len(ramp.elements) > 1:
                ramp.elements.remove(ramp.elements[len(ramp.elements) - 1])
            first = ramp.elements[0]
            first.position = float(elements[0][0])
            first.color = tuple(elements[0][1])
            for pos, color in elements[1:]:
                element = ramp.elements.new(float(pos))
                element.color = tuple(color)
        else:
            setattr(ramp, key, value)


def _apply_curves(mapping, curves):
    for curve, points in zip(mapping.curves, curves):
        points = sorted([tuple(p) for p in points], key=lambda p: p[0])
        if len(points) < 2:
            continue
        while len(curve.points) > 2:
            curve.points.remove(curve.points[len(curve.points) - 1])
        curve.points[0].location = (float(points[0][0]), float(points[0][1]))
        curve.points[1].location = (float(points[-1][0]), float(points[-1][1]))
        for p in points[1:-1]:
            curve.points.new(float(p[0]), float(p[1]))
        try:
            mapping.update()
        except Exception:  # pylint: disable=broad-except
            pass
        ordered = sorted(curve.points, key=lambda pt: pt.location[0])
        for pt, p in zip(ordered, points):
            pt.handle_type = p[2] if len(p) > 2 else "AUTO"
    try:
        mapping.update()
    except Exception:  # pylint: disable=broad-except
        pass


def apply_struct(bpy, obj, struct, report, line, where):
    if obj is None:
        report.skip(line, "{:s}: nothing to set".format(where))
        return
    if obj.bl_rna.identifier == "ColorRamp":
        try:
            _apply_ramp(obj, struct)
        except Exception as ex:  # pylint: disable=broad-except
            report.skip(line, "{:s}: {:s}".format(where, str(ex)))
        return
    props = obj.bl_rna.properties
    for key, value in struct.fields:
        prop = props.get(key)
        if prop is None:
            report.error(line, "{:s} has no setting '{:s}'{:s}".format(
                where, key, choices_text(key, [p.identifier for p in props if p.identifier != "rna_type"])))
            continue
        try:
            if obj.bl_rna.identifier == "CurveMapping" and key == "curves":
                _apply_curves(obj, value)
            elif isinstance(value, Struct):
                apply_struct(bpy, getattr(obj, key), value, report, line, where + "." + key)
            elif prop.type == "POINTER" and _is_id_type(prop.fixed_type):
                found, ok = _coerce_id(bpy, prop, value)
                if not ok:
                    report.skip(line, "{:s}.{:s}: no {:s} named {!r}".format(where, key, prop.fixed_type.identifier, value))
                else:
                    setattr(obj, key, found)
            else:
                setattr(obj, key, value)
        except Exception as ex:  # pylint: disable=broad-except
            report.skip(line, "{:s}.{:s} = {:s}: {:s}".format(where, key, format_value(value), str(ex)))


def apply_items(prop, coll, value, report, line, where):
    args, extras = _item_args(prop)
    extra_names = {q.identifier for q in extras}
    if isinstance(value, int) and not isinstance(value, bool):
        wanted = [()] * value
    elif isinstance(value, (list, tuple)):
        wanted = [tuple(v) if isinstance(v, (tuple, list)) else (v,) for v in value]
    else:
        report.error(line, "{:s}: write a list of items, e.g. [(\"Name\", \"FLOAT\")]".format(where))
        return
    for i, entry in enumerate(wanted):
        extra = {}
        if entry and isinstance(entry[-1], dict):
            extra = entry[-1]
            entry = entry[:-1]
        if len(entry) > len(args):
            report.error(line, "{:s}: item {:d} has {:d} values; items here take ({:s})".format(
                where, i, len(entry), ", ".join(args) or "nothing: give a count"))
            continue
        kwargs = dict(zip(args, entry))
        try:
            if i < len(coll):
                item = coll[i]
                for k, v in kwargs.items():
                    if getattr(item, k) != v:
                        setattr(item, k, v)
            else:
                item = coll.new(**kwargs)
        except Exception as ex:  # pylint: disable=broad-except
            report.skip(line, "{:s} item {:d} {:s}: {:s}".format(where, i, format_value(tuple(entry)), str(ex)))
            continue
        for k, v in extra.items():
            if k not in extra_names:
                report.error(line, "{:s} item {:d}: no setting '{:s}'{:s}".format(where, i, k, choices_text(k, extra_names)))
                continue
            try:
                setattr(item, k, v)
            except Exception as ex:  # pylint: disable=broad-except
                report.skip(line, "{:s} item {:d} {:s}: {:s}".format(where, i, k, str(ex)))
    while len(coll) > len(wanted):
        try:
            coll.remove(coll[len(coll) - 1])
        except Exception as ex:  # pylint: disable=broad-except
            report.skip(line, "{:s}: can't remove items ({:s})".format(where, str(ex)))
            break


def apply_settings(bpy, node, settings, report, groups, tree_type, line=0):
    """Set *settings* ([(key, value)]) on *node*; a setting that fails is tried once more at the end."""
    props = {p.identifier: (p, k) for p, k in setting_props(bpy, node)}
    pending = []
    for key, value in settings:
        pending.append((key, value))
    for attempt in (0, 1):
        failed = []
        for key, value in pending:
            prop, kind = props[key]
            where = "{:s}.{:s}".format(node.name, key)
            try:
                if kind == "items":
                    apply_items(prop, getattr(node, key), value, report, line, where)
                elif kind == "struct":
                    if not isinstance(value, Struct):
                        report.error(line, "{:s}: write {:s}(key=value, ...)".format(where, prop.fixed_type.identifier))
                    else:
                        apply_struct(bpy, getattr(node, key), value, report, line, where)
                elif kind == "id":
                    if key == "node_tree" and isinstance(value, str) and value in groups:
                        found, ok = groups[value], True
                    else:
                        found, ok = _coerce_id(bpy, prop, value)
                    if not ok:
                        names = []
                        coll = getattr(bpy.data, _ID_COLLECTIONS.get(prop.fixed_type.identifier, ""), None)
                        if coll is not None:
                            names = [i.name for i in coll if not i.name.startswith(".coworker_")]
                        if key == "node_tree":
                            report.error(line, "{:s}: no node group named {!r}{:s}".format(
                                where, value, choices_text(str(value), names + list(groups))))
                        else:
                            report.skip(line, "{:s}: no {:s} named {!r}{:s}".format(
                                where, prop.fixed_type.identifier, value, choices_text(str(value), names)))
                    else:
                        setattr(node, key, found)
                else:
                    setattr(node, key, set(value) if prop.type == "ENUM" and prop.is_enum_flag else value)
            except Exception as ex:  # pylint: disable=broad-except
                failed.append((key, value, ex))
        if not failed:
            return
        if attempt == 0:
            pending = [(k, v) for k, v, _e in failed]
            continue
        for key, value, ex in failed:
            prop, _kind = props[key]
            msg = str(ex)
            if prop.type == "ENUM" and not prop.is_enum_flag:
                try:
                    items = [e.identifier for e in prop.enum_items]
                except Exception:  # pylint: disable=broad-except
                    items = []
                if isinstance(value, str) and value not in items and items:
                    report.error(line, "{:s}.{:s}: {!r} isn't a choice{:s}".format(
                        node.name, key, value, choices_text(value, items)))
                    continue
            report.skip(line, "{:s}.{:s} = {:s}: {:s}".format(node.name, key, format_value(value), msg))


def _set_socket_value(bpy, sock, value, report, line, where):
    prop = sock.bl_rna.properties.get("default_value")
    if prop is None:
        report.skip(line, "{:s}: this socket has no value to set".format(where))
        return
    try:
        if prop.type == "POINTER":
            found, ok = _coerce_id(bpy, prop, value)
            if not ok:
                coll = getattr(bpy.data, _ID_COLLECTIONS.get(prop.fixed_type.identifier, ""), None)
                names = [i.name for i in coll] if coll is not None else []
                report.skip(line, "{:s}: no {:s} named {!r}{:s}".format(
                    where, prop.fixed_type.identifier, value, choices_text(str(value), names)))
                return
            sock.default_value = found
        elif prop.type == "ENUM":
            sock.default_value = value
        else:
            sock.default_value = value
    except Exception as ex:  # pylint: disable=broad-except
        report.skip(line, "{:s} = {:s}: {:s}".format(where, format_value(value), str(ex)))


def _first_output(node):
    for i, s in enumerate(node.outputs):
        if s.bl_idname != "NodeSocketVirtual" and not s.is_unavailable:
            return i
    return None


def _socket_choices(sockets):
    names = []
    for ident, name, enabled in _sockets(sockets):
        if not enabled:
            continue
        names.append(name)
        if ident != name:
            names.append(ident)
    return names


def _estimate_size(node):
    rows = sum(1 for s in node.outputs if s.bl_idname != "NodeSocketVirtual" and not s.is_unavailable)
    rows += sum(1 for s in node.inputs if s.bl_idname != "NodeSocketVirtual" and not s.is_unavailable)
    return float(node.width), 60.0 + 22.0 * rows


class BuildCtx:
    def __init__(self, bpy, scratch, report, groups):
        self.bpy = bpy
        self.scratch = scratch
        self.report = report
        self.groups = groups   # {name: tree} for node_tree= lookups


def _socket_type_full(name):
    return name if name.startswith("NodeSocket") else "NodeSocket" + name


def _iface_set_options(obj, options, report, line, where):
    props = obj.bl_rna.properties
    for key, value in options:
        prop = props.get(key)
        if prop is None or key in _SKIP_IFACE:
            names = [p.identifier for p in props if p.identifier not in _SKIP_IFACE and not p.is_readonly]
            report.error(line, "{:s}: no option '{:s}'{:s}".format(where, key, choices_text(key, names)))
            continue
        try:
            setattr(obj, key, set(value) if prop.type == "ENUM" and prop.is_enum_flag else value)
        except Exception as ex:  # pylint: disable=broad-except
            report.skip(line, "{:s}.{:s} = {:s}: {:s}".format(where, key, format_value(value), str(ex)))


def build_interface(tree, items, ctx):
    """Make the tree's interface match *items*; matching sockets (same in/out and name) are kept."""
    report = ctx.report
    iface = tree.interface
    desired = []
    stack = [None]
    for it in items:
        if it.kind == "end":
            if len(stack) > 1:
                stack.pop()
        elif it.kind == "panel":
            desired.append((it, stack[-1]))
            stack.append(len(desired) - 1)
        else:
            desired.append((it, stack[-1]))
    existing = list(iface.items_tree)
    used = set()
    matched = []
    for it, _parent in desired:
        found = None
        for item in existing:
            if item.as_pointer() in used:
                continue
            if it.kind == "panel" and item.item_type == "PANEL" and item.name == it.name:
                found = item
                break
            if it.kind == "socket" and item.item_type == "SOCKET" and item.name == it.name \
                    and item.in_out == it.in_out.upper():
                found = item
                break
        if found is not None:
            used.add(found.as_pointer())
        matched.append(found)
    keep = used
    for item in [i for i in existing if i.item_type == "SOCKET" and i.as_pointer() not in keep] + \
            [i for i in existing if i.item_type == "PANEL" and i.as_pointer() not in keep]:
        try:
            iface.remove(item)
        except Exception:  # pylint: disable=broad-except
            pass
    objs = []
    moved = False
    for (it, parent_index), found in zip(desired, matched):
        parent = objs[parent_index] if parent_index is not None else None
        where = "{:s} {:s}".format(it.in_out or "panel", format_name(it.name))
        obj = found
        try:
            if it.kind == "panel":
                if obj is None:
                    obj = iface.new_panel(it.name)
                    if parent is not None:
                        iface.move_to_parent(obj, parent, len(parent.interface_items))
                else:
                    moved = True
            else:
                socket_type = _socket_type_full(it.type)
                if obj is not None and obj.socket_type != socket_type:
                    obj.socket_type = socket_type
                if obj is None:
                    kwargs = {"name": it.name, "in_out": it.in_out.upper(), "socket_type": socket_type}
                    if parent is not None:
                        kwargs["parent"] = parent
                    obj = iface.new_socket(**kwargs)
                else:
                    moved = True
        except Exception as ex:  # pylint: disable=broad-except
            if it.kind == "socket":
                valid = [t for t in _SOCKET_TYPES if hasattr(ctx.bpy.types, "NodeSocket" + t)]
                report.error(it.line, "{:s}: unknown socket type '{:s}'{:s}".format(where, it.type, choices_text(it.type, valid)))
            else:
                report.error(it.line, "{:s}: {:s}".format(where, str(ex)))
            objs.append(None)
            continue
        _iface_set_options(obj, it.options, report, it.line, where)
        objs.append(obj)
    if moved:
        counts = {}
        for (it, parent_index), obj in zip(desired, objs):
            if obj is None:
                continue
            parent = objs[parent_index] if parent_index is not None else None
            position = counts.get(parent_index, 0)
            counts[parent_index] = position + 1
            try:
                if parent is None:
                    root = obj.parent
                    while root is not None and root.parent is not None:
                        root = root.parent
                    if obj.parent is not None and root is not None and obj.parent.as_pointer() != root.as_pointer():
                        iface.move_to_parent(obj, root, position)
                    else:
                        iface.move(obj, position)
                else:
                    iface.move_to_parent(obj, parent, position)
            except Exception:  # pylint: disable=broad-except
                pass
    return [(it, obj) for (it, _p), obj in zip(desired, objs)]


def _set_iface_defaults(pairs, ctx):
    for it, obj in pairs:
        if obj is None or it.kind != "socket" or not it.has_default:
            continue
        where = "{:s} {:s}".format(it.in_out, format_name(it.name))
        if not hasattr(obj, "default_value"):
            ctx.report.skip(it.line, "{:s}: a {:s} socket has no default value".format(where, it.type))
            continue
        _set_socket_value(ctx.bpy, obj, it.default, ctx.report, it.line, where)


def _split_args(bpy, node, spec, report):
    """(base [(k, v)], settings [(k, v)], sockets [(k, v, quoted)], paired, outputs, hidden {key: names})."""
    props = {p.identifier for p, _k in setting_props(bpy, node)}
    base, settings, sockets = [], [], []
    paired = None
    outputs = None
    hidden = {}
    for key, value, quoted in spec.args:
        if not quoted and key == "paired_output":
            paired = value
        elif not quoted and key in ("hidden_inputs", "hidden_outputs"):
            hidden[key] = value
        elif not quoted and key == "outputs":
            outputs = value
        elif not quoted and key in _BASE_KEYS:
            base.append((key, value))
        elif not quoted and key in props:
            settings.append((key, value))
        else:
            sockets.append((key, value, quoted))
    return base, settings, sockets, paired, outputs, hidden


def _apply_hidden(node, hidden, report, line):
    """``hidden_inputs`` / ``hidden_outputs``: exactly these sockets are hidden (Ctrl+H)."""
    for key, names in hidden.items():
        sockets = node.inputs if key == "hidden_inputs" else node.outputs
        if not isinstance(names, (list, tuple)):
            report.error(line, "{:s}.{:s}: write a list of socket names".format(node.name, key))
            continue
        infos = _sockets(sockets)
        wanted = set()
        for name in names:
            index = resolve_socket(name, infos)
            if index is None:
                report.error(line, "{:s}.{:s}: no socket '{:s}'{:s}".format(
                    node.name, key, str(name), choices_text(str(name), _socket_choices(sockets))))
                continue
            wanted.add(index)
        for i, sock in enumerate(sockets):
            if sock.bl_idname == "NodeSocketVirtual":
                continue
            try:
                if sock.hide != (i in wanted):
                    sock.hide = i in wanted
            except Exception as ex:  # pylint: disable=broad-except
                report.skip(line, "{:s}.{:s}: {:s}".format(node.name, key, str(ex)))


def _apply_base(node, base, report, line):
    for key, value in base:
        try:
            if key == "color":
                node.use_custom_color = True
                node.color = tuple(value)[:3]
            else:
                setattr(node, key, value)
        except Exception as ex:  # pylint: disable=broad-except
            report.skip(line, "{:s}.{:s} = {:s}: {:s}".format(node.name, key, format_value(value), str(ex)))


def build_tree(bpy, spec, tree, mode, ctx, layout="auto"):
    """Build *spec* into *tree*.  *mode*: "create" / "replace" (tree emptied first) or "update"."""
    report = ctx.report
    tree_type = tree.bl_idname
    known = node_types(bpy)
    # Tree settings.
    for key, value in spec.props:
        prop = tree.bl_rna.properties.get(key)
        if prop is None or prop.is_readonly or key in _SKIP_TREE_PROPS:
            names = [p.identifier for p in tree.bl_rna.properties if not p.is_readonly
                     and p.identifier not in _SKIP_TREE_PROPS and not p.identifier.startswith("bl_")]
            report.error(spec.line, "tree setting '{:s}' doesn't exist{:s}".format(key, choices_text(key, names)))
            continue
        try:
            setattr(tree, key, value)
        except Exception as ex:  # pylint: disable=broad-except
            report.skip(spec.line, "tree {:s} = {:s}: {:s}".format(key, format_value(value), str(ex)))
    old_pos = {}
    if mode in ("create", "replace"):
        for node in tree.nodes:
            old_pos[node.name] = tuple(node.location_absolute) if hasattr(node, "location_absolute") \
                else tuple(node.location)
        tree.nodes.clear()
    iface_pairs = []
    if mode in ("create", "replace") or spec.interface:
        iface_pairs = build_interface(tree, spec.interface, ctx)
    # Deletions (update).
    for name in spec.deletes:
        node = tree.nodes.get(name)
        if node is None:
            report.error(spec.line, "del: no node named {!r}{:s}".format(name, choices_text(name, [n.name for n in tree.nodes])))
        else:
            tree.nodes.remove(node)
    # Nodes.
    made = []          # [(node, spec, is_new)]
    relink = []        # links of nodes whose type changed: (out node, out id, out name, in node, in id, in name)
    keep_place = {}    # name -> (parent name, location) of nodes whose type changed
    for nspec in spec.nodes:
        bl_idname = "NodeFrame" if nspec.is_frame else resolve_type_name(nspec.type, tree_type, known)
        if bl_idname is None:
            shorts = {i: short_type_name(i, tree_type, known) for i in known}
            aliases = {label: shorts[i] for i, label in known.items()}
            aliases.update({i: s for i, s in shorts.items()})
            report.error(nspec.line, "unknown node type '{:s}'{:s}".format(
                nspec.type, choices_text(nspec.type, sorted(set(shorts.values())), aliases)))
            continue
        existing = tree.nodes.get(nspec.name) if mode == "update" else None
        if existing is not None and existing.bl_idname != bl_idname:
            # A new type: keep its links (matched again by identifier, then name), frame and place.
            for link in tree.links:
                if link.from_node == existing or link.to_node == existing:
                    relink.append((link.from_node.name, link.from_socket.identifier, link.from_socket.name,
                                   link.to_node.name, link.to_socket.identifier, link.to_socket.name))
            keep_place[nspec.name] = (existing.parent.name if existing.parent is not None else None,
                                      tuple(existing.location_absolute))
            tree.nodes.remove(existing)
            existing = None
        node = existing
        if node is None:
            try:
                node = tree.nodes.new(bl_idname)
            except Exception as ex:  # pylint: disable=broad-except
                report.error(nspec.line, "{:s} can't be added to a {:s}: {:s}".format(
                    nspec.type, EDITOR_NAMES.get(tree_type, tree_type), str(ex)))
                continue
            node.name = nspec.name
            if node.name != nspec.name:
                report.skip(nspec.line, "node {!r} was named {!r} by Blender".format(nspec.name, node.name))
        made.append((node, nspec, existing is None and nspec.name not in keep_place))
    by_name = {n.name: n for n in tree.nodes}
    for name, node in [(s.name, n) for n, s, _new in made]:
        by_name[name] = node
    node_names = list(by_name)
    # Pass A: settings and node-level keys.
    parts = []
    for node, nspec, _is_new in made:
        base, settings, sockets, paired, outputs, hidden = _split_args(bpy, node, nspec, report)
        _apply_base(node, base, report, nspec.line)
        apply_settings(bpy, node, settings, report, ctx.groups, tree_type, nspec.line)
        parts.append((node, nspec, sockets, paired, outputs, hidden))
    # Pass B: zones.
    for node, nspec, _sockets_, paired, _outputs, _hidden in parts:
        if paired is None:
            continue
        # A bare quoted node name reads back as a string: both mean the node.
        target_name = paired.node if isinstance(paired, Ref) else paired if isinstance(paired, str) else None
        target = by_name.get(target_name) if target_name is not None else None
        if target is None:
            report.error(nspec.line, "paired_output: no node {!r}{:s}".format(
                target_name, choices_text(str(target_name), node_names)))
            continue
        if not hasattr(node, "pair_with_output"):
            report.error(nspec.line, "{:s} isn't a zone input (paired_output)".format(node.name))
            continue
        try:
            if not node.pair_with_output(target):
                report.skip(nspec.line, "{:s}: can't pair with {:s}".format(node.name, target.name))
        except Exception as ex:  # pylint: disable=broad-except
            report.skip(nspec.line, "{:s}: can't pair with {:s}: {:s}".format(node.name, target.name, str(ex)))
    # Pass C: inputs (values and links), output values.
    made_links = []
    for node, nspec, sockets, _paired, outputs, hidden in parts:
        if hidden:
            _apply_hidden(node, hidden, report, nspec.line)
        infos = _sockets(node.inputs)
        for key, value, _quoted in sockets:
            index = resolve_socket(key, infos)
            if index is None or node.inputs[index].bl_idname == "NodeSocketVirtual":
                names = _socket_choices(node.inputs) + [p.identifier for p, _k in setting_props(bpy, node)]
                report.error(nspec.line, "{:s} ({:s}) has no input or setting '{:s}'{:s}".format(
                    nspec.name, nspec.type, key, choices_text(str(key), names)))
                continue
            sock = node.inputs[index]
            where = "{:s}.{:s}".format(nspec.name, key)
            refs = value if isinstance(value, list) and value and all(isinstance(v, Ref) for v in value) \
                else [value] if isinstance(value, Ref) else None
            if mode == "update":
                for link in [l for l in tree.links if l.to_socket == sock]:
                    tree.links.remove(link)
            if refs is None:
                _set_socket_value(bpy, sock, value, report, nspec.line, where)
                continue
            for ref in refs:
                src = by_name.get(ref.node)
                if src is None:
                    report.error(nspec.line, "{:s}: no node named {!r}{:s}".format(
                        where, ref.node, choices_text(ref.node, node_names)))
                    continue
                out_index = _first_output(src) if ref.socket is None else resolve_socket(ref.socket, _sockets(src.outputs))
                if out_index is None or src.outputs[out_index].bl_idname == "NodeSocketVirtual":
                    report.error(nspec.line, "{:s}: {:s} has no output '{:s}'{:s}".format(
                        where, ref.node, str(ref.socket), choices_text(str(ref.socket), _socket_choices(src.outputs))))
                    continue
                try:
                    link = tree.links.new(src.outputs[out_index], sock)
                    if ref.muted:
                        link.is_muted = True
                    made_links.append((link, nspec.line, where, ref))
                except Exception as ex:  # pylint: disable=broad-except
                    report.skip(nspec.line, "{:s}: can't link {:s}: {:s}".format(where, format_value(ref), str(ex)))
        if outputs is not None:
            if not isinstance(outputs, dict):
                report.error(nspec.line, "{:s}.outputs: write a dict, e.g. outputs={{\"Value\": 0.5}}".format(nspec.name))
                continue
            out_infos = _sockets(node.outputs)
            for key, value in outputs.items():
                index = resolve_socket(key, out_infos)
                if index is None:
                    report.error(nspec.line, "{:s} has no output '{:s}'{:s}".format(
                        nspec.name, key, choices_text(key, _socket_choices(node.outputs))))
                    continue
                _set_socket_value(bpy, node.outputs[index], value, report, nspec.line,
                                  "{:s}.outputs[{:s}]".format(nspec.name, key))
    # Re-link nodes whose type changed (update), unless the text gave that input itself.
    given = set()
    for node, nspec, sockets, *_rest in parts:
        infos = _sockets(node.inputs)
        for key, _value, _quoted in sockets:
            index = resolve_socket(key, infos)
            if index is not None:
                given.add((nspec.name, node.inputs[index].identifier))
    for name, ident, sname, to_name, to_ident, to_sname in relink:
        src, dst = by_name.get(name), by_name.get(to_name)
        if src is None or dst is None:
            continue
        index = resolve_socket(ident, _sockets(src.outputs))
        if index is None:
            index = resolve_socket(sname, _sockets(src.outputs))
        to_index = resolve_socket(to_ident, _sockets(dst.inputs))
        if to_index is None:
            to_index = resolve_socket(to_sname, _sockets(dst.inputs))
        if index is None or to_index is None:
            report.skip(0, "{:s}.{:s} -> {:s}.{:s}: no matching socket after the type change".format(
                name, sname, to_name, to_sname))
            continue
        if (to_name, dst.inputs[to_index].identifier) in given:
            continue
        tree.links.new(src.outputs[index], dst.inputs[to_index])
    for name, (parent, location) in keep_place.items():
        node = by_name.get(name)
        if node is None:
            continue
        if parent is not None and by_name.get(parent) is not None and node.parent is None:
            node.parent = by_name[parent]
        node.location_absolute = location
    # Frames.
    for node, nspec, _is_new in made:
        if not nspec.is_frame:
            continue
        for child in nspec.children:
            target = by_name.get(child)
            if target is None:
                report.error(nspec.line, "frame {:s}: no node named {!r}{:s}".format(
                    nspec.name, child, choices_text(child, node_names)))
                continue
            try:
                target.parent = node
            except Exception as ex:  # pylint: disable=broad-except
                report.skip(nspec.line, "frame {:s}: {:s}: {:s}".format(nspec.name, child, str(ex)))
    _set_iface_defaults(iface_pairs, ctx)
    _place(tree, made, old_pos, mode, layout)
    # Links Blender keeps but marks invalid (drawn red): the socket types don't connect.
    for link, line, where, ref in made_links:
        try:
            valid = link.is_valid
        except Exception:  # pylint: disable=broad-except
            continue
        if not valid:
            report.skip(line, "{:s}: the link from {:s} is invalid in Blender ({:s} -> {:s})".format(
                where, format_value(ref), link.from_socket.type, link.to_socket.type))


def _place(tree, made, old_pos, mode, layout):
    nodes = [n for n in tree.nodes if n.bl_idname != "NodeFrame"]
    names = [n.name for n in nodes]
    sizes = {n.name: _estimate_size(n) for n in nodes}
    edges = [(l.from_node.name, l.to_node.name) for l in tree.links]
    pairs = []
    for n in nodes:
        paired = getattr(n, "paired_output", None)
        if paired is not None:
            pairs.append((n.name, paired.name))
    auto = layout_nodes(names, sizes, edges, pairs)
    new_names = {n.name for n, _s, is_new in made if is_new and n.bl_idname != "NodeFrame"}
    if mode == "update":
        keep = {n.name: tuple(n.location_absolute) for n in nodes if n.name not in new_names}
    elif layout == "keep":
        keep = {k: v for k, v in old_pos.items() if k in sizes}
    else:
        keep = {}
    if keep:
        dx = sum(keep[k][0] - auto[k][0] for k in keep) / len(keep)
        dy = sum(keep[k][1] - auto[k][1] for k in keep) / len(keep)
    else:
        dx = dy = 0.0
    for n in nodes:
        if n.name in keep:
            if mode != "update":
                n.location_absolute = keep[n.name]
            continue
        x, y = auto[n.name]
        n.location_absolute = (x + dx, y + dy)
