# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
#480: node trees as code -- the text format (bpy-free).

1. Printer / parser round trips over hand-written trees with every construct
   (header, interface with panels, settings, quoted names and keys, links,
   multi-input lists, muted links, zones, item lists, nested structures,
   frames, deletes, nested group blocks, a text without a header).
2. Parser errors carry line numbers; nothing is evaluated.
3. The "nearest names" suggestions, socket and node-type name rules.
4. The layered layout and the dry-run change list.
5. The two tools: parameters, results, the spliced tool-code needs no
   ``blmcp`` inside Blender, and where they are offered.

Run with::

    python -m unittest tests.test_node_code -v
"""

__all__ = ()

import builtins
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MCP_DIR = str(_ROOT / "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from blmcp.tools import _node_code_format as ncf  # noqa: E402  pylint: disable=wrong-import-position
from blmcp.tools._node_code_format import (  # noqa: E402  pylint: disable=wrong-import-position
    CodeError, CodeSpec, IfaceSpec, NodeSpec, Ref, Struct, TreeSpec, diff_trees, format_value, layout_nodes,
    nearest_names, parse_code, print_code, resolve_socket, resolve_type_name, short_type_name, socket_key,
)


# Every construct, already in the printer's own form.
_FULL = '''tree "Inner" (GeometryNodeTree)
output Geometry: Geometry
input Geometry: Geometry
input Offset: Float(min_value=0.0, max_value=2.0, subtype="DISTANCE") = 0.5
"Group Input" = GroupInput()
"Combine XYZ" = CombineXYZ(Z="Group Input".Offset)
"Set Position" = SetPosition(Geometry="Group Input".Geometry, Offset="Combine XYZ".Vector)
"Group Output" = GroupOutput(Geometry="Set Position".Geometry)

tree "Main" (GeometryNodeTree, is_modifier=True, description="Twists")
output Geometry: Geometry
input Geometry: Geometry
input "Use Twist": Bool = True
panel Settings (default_closed=True)
input Count: Int(min_value=1, max_value=10) = 3
panel Deep
input Seed: Int
end panel
end panel
"Group Input" = GroupInput()
"Repeat Input" = RepeatInput(paired_output="Repeat Output", Iterations="Group Input".Count, Geometry="Group Input".Geometry)
Group = Group(node_tree="Inner", Geometry="Repeat Input".Geometry, Offset=0.25)
Math = Math(label="Height +1", mute=True, operation="MULTIPLY", Value="Repeat Input".Height, Value_001=-1.5)
"Repeat Output" = RepeatOutput(repeat_items=[("Geometry", "GEOMETRY"), ("Height", "FLOAT", {"attribute_domain": "FACE"})], Geometry=Group.Geometry, Height=Math.Value)
Switch = Switch(input_type="GEOMETRY", Switch="Group Input"["Use Twist"], "False"="Repeat Output".Geometry, "True"="Repeat Output"[0])
Menu = MenuSwitch(enum_items=["A", "B", "C"])
Index = IndexSwitch(index_switch_items=3, "0"=Switch.Output)
Cube = MeshCube(color=(0.8, 0.2, 0.2), width=180.0, Size=(0.3, 0.3, 0.3), hidden_inputs=["Vertices X"], hidden_outputs=["UV Map"])
Ramp = ValToRGB(color_ramp=ColorRamp(interpolation="EASE", elements=[(0.0, (0.0, 0.0, 0.0, 1.0)), (0.5, (0.9, 0.4, 0.1, 1.0))]))
Curve = FloatCurve(mapping=CurveMapping(curves=[[(0.0, 0.0), (0.4, 0.6, "VECTOR"), (1.0, 1.0)]], use_clip=False))
Flags = CurveSetHandles(mode={"LEFT", "RIGHT"}, other=set(), big=1e999, small=-1e999)
Value = Value(outputs={"Value": 0.75})
Join = JoinGeometry(Geometry=[muted("Ico Sphere".Mesh), Cube.Mesh, Switch.Output])
"Group Output" = GroupOutput(Geometry=Join.Geometry, "label"=Cube.Mesh)
frame Frame (label="Twist", shrink=False): Switch, Menu
frame Outer: Frame, Index
del "Old Node", gone
'''


class TestRoundTrip(unittest.TestCase):

    def test_the_printer_reproduces_its_own_text(self):
        code = parse_code(_FULL)
        self.assertEqual(print_code(code), _FULL)
        self.assertEqual(parse_code(print_code(code)), code)

    def test_what_the_parser_reads(self):
        code = parse_code(_FULL)
        inner, main = code.trees
        self.assertEqual((inner.kind, inner.name, inner.tree_type), ("tree", "Inner", "GeometryNodeTree"))
        self.assertEqual(main.props, [("is_modifier", True), ("description", "Twists")])
        kinds = [(i.kind, i.in_out, i.name) for i in main.interface]
        self.assertEqual(kinds, [("socket", "output", "Geometry"), ("socket", "input", "Geometry"),
                                 ("socket", "input", "Use Twist"), ("panel", "", "Settings"),
                                 ("socket", "input", "Count"), ("panel", "", "Deep"), ("socket", "input", "Seed"),
                                 ("end", "", ""), ("end", "", "")])
        count = main.interface[4]
        self.assertEqual((count.type, count.options, count.default, count.has_default),
                         ("Int", [("min_value", 1), ("max_value", 10)], 3, True))
        self.assertFalse(main.interface[6].has_default)
        rep_in = main.node("Repeat Input")
        self.assertEqual(rep_in.arg("paired_output"), "Repeat Output")   # a bare quoted name reads as a string
        self.assertEqual(rep_in.arg("Iterations"), Ref("Group Input", "Count"))
        switch = main.node("Switch")
        self.assertEqual(switch.args[1], ("Switch", Ref("Group Input", "Use Twist"), False))
        self.assertEqual(switch.args[2], ("False", Ref("Repeat Output", "Geometry"), True))
        self.assertEqual(switch.arg("True"), Ref("Repeat Output", 0))
        self.assertEqual(main.node("Join").arg("Geometry"),
                         [Ref("Ico Sphere", "Mesh", muted=True), Ref("Cube", "Mesh"), Ref("Switch", "Output")])
        ramp = main.node("Ramp").arg("color_ramp")
        self.assertIsInstance(ramp, Struct)
        self.assertEqual(ramp.get("interpolation"), "EASE")
        self.assertEqual(main.node("Flags").arg("mode"), {"LEFT", "RIGHT"})
        self.assertEqual(main.node("Flags").arg("other"), set())
        self.assertEqual(main.node("Flags").arg("big"), float("inf"))
        self.assertEqual(main.node("Index").arg("index_switch_items"), 3)
        self.assertEqual(main.node("Value").arg("outputs"), {"Value": 0.75})
        out = main.node("Group Output")
        self.assertEqual(out.args[1], ("label", Ref("Cube", "Mesh"), True))   # a quoted key is a socket
        frame = main.node("Frame")
        self.assertTrue(frame.is_frame)
        self.assertEqual((frame.children, frame.arg("label")), (["Switch", "Menu"], "Twist"))
        self.assertEqual(main.node("Outer").children, ["Frame", "Index"])
        self.assertEqual(main.deletes, ["Old Node", "gone"])

    def test_specs_print_and_parse_back(self):
        tree = TreeSpec("material", "Wood", "ShaderNodeTree", nodes=[
            NodeSpec("Principled BSDF", "BsdfPrincipled", [("Base Color", Ref("Mix", "Result"), False),
                                                          ("Roughness", 0.35, False)]),
            NodeSpec("Mix", "Mix", [("data_type", "RGBA", False), ("A", (0.8, 0.8, 0.8, 1.0), False)]),
            NodeSpec("Material Output", "OutputMaterial", [("Surface", Ref("Principled BSDF", "BSDF"), False)]),
        ])
        text = print_code(CodeSpec([tree]))
        self.assertTrue(text.startswith('material "Wood"\n'))
        self.assertIn('"Principled BSDF" = BsdfPrincipled("Base Color"=Mix.Result, Roughness=0.35)', text)
        self.assertEqual(parse_code(text), CodeSpec([tree]))

    def test_comments_blank_lines_and_continued_lines(self):
        text = ('# a comment\n\ntree "T" (ShaderNodeTree)  # trailing\n'
                'm = Math(operation="ADD",\n'
                '         Value=0.5,  # half\n'
                '         Value_001=n.Value)\n'
                'n = Value()\n')
        tree = parse_code(text).trees[0]
        self.assertEqual([n.name for n in tree.nodes], ["m", "n"])
        self.assertEqual(tree.node("m").args, [("operation", "ADD", False), ("Value", 0.5, False),
                                               ("Value_001", Ref("n", "Value"), False)])
        self.assertEqual(tree.node("m").line, 4)

    def test_a_text_without_a_header_is_one_block(self):
        code = parse_code('Math = Math(operation="ADD")\ndel "Ico Sphere"\n')
        self.assertEqual(len(code.trees), 1)
        self.assertEqual(code.trees[0].kind, "")
        self.assertEqual(code.trees[0].deletes, ["Ico Sphere"])

    def test_full_bl_idnames_and_odd_names(self):
        text = ('tree "T" (GeometryNodeTree)\n'
                '"Math.001" = ShaderNodeMath(Value=1.0)\n'
                '"input" = GeometryNodeSetPosition()\n'
                '"x \\"y\\"" = NodeReroute(Input="Math.001".Value)\n')
        tree = parse_code(text).trees[0]
        self.assertEqual([(n.name, n.type) for n in tree.nodes],
                         [("Math.001", "ShaderNodeMath"), ("input", "GeometryNodeSetPosition"),
                          ('x "y"', "NodeReroute")])
        self.assertEqual(print_code(parse_code(text)), text)

    def test_names_that_must_be_quoted(self):
        self.assertEqual(ncf.format_name("Group Input"), '"Group Input"')
        self.assertEqual(ncf.format_name("Math_2"), "Math_2")
        for word in ("True", "None", "in", "frame", "input", "del", "end"):
            self.assertTrue(ncf.format_name(word).startswith('"'), word)
        self.assertEqual(format_value(Ref("Switch", "True")), 'Switch["True"]')
        self.assertEqual(format_value(Ref("Node", "Base Color")), 'Node["Base Color"]')
        self.assertEqual(format_value(Ref("Group Input", 2)), '"Group Input"[2]')


class TestValues(unittest.TestCase):

    def test_floats_are_short_and_stable(self):
        self.assertEqual(format_value(0.800000011920929), "0.8")
        self.assertEqual(format_value(1 / 3), "0.333333")
        self.assertEqual(format_value(2.0), "2.0")
        self.assertEqual(format_value(-0.0), "0.0")
        self.assertEqual(format_value(1e-07), "1e-07")
        self.assertEqual(format_value(3.4028234663852886e+38), "3.40282e+38")
        self.assertEqual(format_value(float("inf")), "1e999")
        self.assertEqual(format_value(float("-inf")), "-1e999")
        for value in (0.1, 1 / 3, 12.5, 1e-07, 123456.7):
            once = format_value(value)
            self.assertEqual(format_value(float(once)), once)

    def test_other_values(self):
        self.assertEqual(format_value(True), "True")
        self.assertEqual(format_value(3), "3")
        self.assertEqual(format_value(None), "None")
        self.assertEqual(format_value("a\"b"), '"a\\"b"')
        self.assertEqual(format_value((1.0,)), "(1.0,)")
        self.assertEqual(format_value({"B", "A"}), '{"A", "B"}')
        self.assertEqual(format_value(set()), "set()")
        self.assertEqual(format_value(Struct("ImageUser", [("frame_start", 3)])), "ImageUser(frame_start=3)")
        self.assertEqual(format_value(Ref("a", "b", muted=True)), "muted(a.b)")


class TestParseErrors(unittest.TestCase):

    def errors(self, text):
        with self.assertRaises(CodeError) as ctx:
            parse_code(text)
        return ctx.exception.errors

    def test_every_error_names_its_line(self):
        errors = self.errors('tree "T" (GeometryNodeTree)\n'
                             'a = Math(1.0)\n'
                             'b = Math(Value=1 + 2)\n'
                             'c = Math(Value=__import__("os"))\n'
                             'd = Math(**x)\n'
                             'what is this\n'
                             'e = Math(Value=(1,\n'
                             '  2 +))\n')
        self.assertEqual([e.split(":")[0] for e in errors],
                         ["line 2", "line 3", "line 4", "line 5", "line 6", "line 8"])   # 8: the line inside
        self.assertIn("key=value", errors[0])
        self.assertIn("unsupported value `1 + 2`", errors[1])
        self.assertIn("unsupported value", errors[2])
        self.assertIn("`**`", errors[3])
        self.assertIn("expected `name = NodeType(...)`", errors[4])

    def test_header_and_panel_errors(self):
        self.assertIn("tree type is missing", self.errors('tree "T"\n')[0])
        self.assertIn("`end panel` without a `panel`", self.errors('tree "T" (ShaderNodeTree)\nend panel\n')[0])
        self.assertIn("`end panel` missing", self.errors('tree "T" (ShaderNodeTree)\npanel P\ninput a: Float\n')[0])
        self.assertIn("write `input Name: Type`", self.errors('tree "T" (ShaderNodeTree)\ninput Geometry\n')[0])
        self.assertIn("defined twice", self.errors('a = Math()\na = Math()\n')[0])
        self.assertIn("socket type must be a name", self.errors('input a: 3\n')[0])

    def test_nothing_is_evaluated(self):
        # A call to a builtin is only a structure name; nothing runs.
        called = []
        real = builtins.__import__

        def spy(name, *args, **kwargs):
            called.append(name)
            return real(name, *args, **kwargs)

        builtins.__import__ = spy
        try:
            code = parse_code('x = Math(Value=open(path="nope"))\n')
        finally:
            builtins.__import__ = real
        self.assertEqual(code.trees[0].node("x").arg("Value"), Struct("open", [("path", "nope")]))
        self.assertNotIn("os", called)


class TestNames(unittest.TestCase):

    _GN = {"GeometryNodeSetPosition", "GeometryNodeGroup", "ShaderNodeGroup", "ShaderNodeMath", "FunctionNodeCompare",
           "NodeGroupInput", "NodeFrame", "ShaderNodeTexNoise", "GeometryNodeMeshCube", "FunctionNodeInputVector",
           "GeometryNodeInputVector"}

    def test_short_names_are_reversible(self):
        for idname in self._GN:
            short = short_type_name(idname, "GeometryNodeTree", self._GN)
            self.assertEqual(resolve_type_name(short, "GeometryNodeTree", self._GN), idname, short)
        self.assertEqual(short_type_name("GeometryNodeSetPosition", "GeometryNodeTree", self._GN), "SetPosition")
        self.assertEqual(short_type_name("ShaderNodeMath", "GeometryNodeTree", self._GN), "Math")
        self.assertEqual(short_type_name("NodeGroupInput", "GeometryNodeTree", self._GN), "GroupInput")
        # "Group" is the tree's own group node; the other one keeps its full name.
        self.assertEqual(short_type_name("GeometryNodeGroup", "GeometryNodeTree", self._GN), "Group")
        self.assertEqual(short_type_name("ShaderNodeGroup", "GeometryNodeTree", self._GN), "ShaderNodeGroup")
        # "InputVector" is the GeometryNode one, so the FunctionNode one stays full.
        self.assertEqual(short_type_name("FunctionNodeInputVector", "GeometryNodeTree", self._GN),
                         "FunctionNodeInputVector")
        self.assertEqual(short_type_name("ShaderNodeGroup", "ShaderNodeTree", self._GN), "Group")

    def test_full_names_and_unknown_names(self):
        self.assertEqual(resolve_type_name("ShaderNodeMath", "GeometryNodeTree", self._GN), "ShaderNodeMath")
        self.assertIsNone(resolve_type_name("SetPositon", "GeometryNodeTree", self._GN))

    def test_sockets_by_identifier_then_name(self):
        math = [("Value", "Value", True), ("Value_001", "Value", True), ("Value_002", "Value", False)]
        self.assertEqual(resolve_socket("Value", math), 0)
        self.assertEqual(resolve_socket("Value_001", math), 1)
        self.assertEqual([socket_key(i, math) for i in range(3)], ["Value", "Value_001", "Value_002"])
        mix = [("Factor_Float", "Factor", True), ("Factor_Vector", "Factor", False), ("A_Float", "A", False),
               ("A_Color", "A", True), ("B_Color", "B", True)]
        self.assertEqual(resolve_socket("A", mix), 3)            # the enabled one
        self.assertEqual(resolve_socket("a", mix), 3)            # any case
        self.assertEqual(resolve_socket("Factor", mix), 0)
        self.assertEqual(socket_key(3, mix), "A")
        self.assertEqual(socket_key(2, mix), "A_Float")          # the hidden one needs its identifier
        self.assertEqual(resolve_socket(4, mix), 4)
        self.assertIsNone(resolve_socket(9, mix))
        self.assertIsNone(resolve_socket("Fac", mix))
        group = [("Socket_0", "Geometry", True), ("Socket_1", "Socket_0", True)]
        self.assertEqual(resolve_socket("Socket_0", group), 0)   # identifiers win
        self.assertEqual(socket_key(1, group), "Socket_1")

    def test_nearest_names(self):
        names = ["Geometry", "Selection", "Position", "Offset", "Radius", "Tilt", "Normal", "Curve"]
        self.assertEqual(nearest_names("Geometri", names)[0], "Geometry")
        self.assertEqual(nearest_names("ofset", names)[0], "Offset")
        self.assertEqual(nearest_names("x", ["A", "B"]), ["A", "B"])           # a few: all of them
        self.assertTrue(nearest_names("zzzz", names))                          # always some choices
        self.assertLessEqual(len(nearest_names("o", names)), 6)
        shorts = ["MeshCube", "MeshCone", "SetPosition", "BsdfPrincipled", "TexNoise", "Math"]
        aliases = {"Cube": "MeshCube", "Principled BSDF": "BsdfPrincipled", "Noise Texture": "TexNoise"}
        self.assertEqual(nearest_names("MeshCub", shorts, aliases=aliases)[0], "MeshCube")
        self.assertEqual(nearest_names("PrincipledBSDF", shorts, aliases=aliases)[0], "BsdfPrincipled")
        self.assertEqual(nearest_names("Noise Texture", shorts, aliases=aliases)[0], "TexNoise")


class TestLayout(unittest.TestCase):

    def test_columns_follow_the_links(self):
        names = ["in", "a", "b", "c", "out", "lonely"]
        edges = [("in", "a"), ("a", "b"), ("b", "out"), ("in", "c"), ("c", "out")]
        pos = layout_nodes(names, {n: (140.0, 100.0) for n in names}, edges)
        self.assertLess(pos["in"][0], pos["a"][0])
        self.assertLess(pos["a"][0], pos["b"][0])
        self.assertLess(pos["b"][0], pos["out"][0])
        # c feeds the output directly: it sits next to it, not next to the input.
        self.assertEqual(pos["c"][0], pos["b"][0])
        self.assertEqual(pos["lonely"][0], pos["out"][0])

    def test_no_overlap_in_a_column(self):
        names = ["a{:d}".format(i) for i in range(5)] + ["out"]
        sizes = {n: (140.0, 80.0 + 10 * i) for i, n in enumerate(names)}
        pos = layout_nodes(names, sizes, [(n, "out") for n in names[:-1]])
        self.assertEqual(len({pos[n][0] for n in names[:-1]}), 1)
        column = sorted(names[:-1], key=lambda n: -pos[n][1])        # top to bottom (y up)
        for upper, lower in zip(column, column[1:]):
            self.assertGreaterEqual(pos[upper][1] - sizes[upper][1], pos[lower][1])

    def test_zone_input_left_of_output_and_cycles_end(self):
        pos = layout_nodes(["ri", "ro", "x"], {}, [("x", "ro")], pairs=[("ri", "ro")])
        self.assertLess(pos["ri"][0], pos["ro"][0])
        pos = layout_nodes(["a", "b"], {}, [("a", "b"), ("b", "a")])
        self.assertEqual(set(pos), {"a", "b"})

    def test_wider_nodes_push_the_next_column(self):
        pos = layout_nodes(["a", "b"], {"a": (400.0, 100.0), "b": (140.0, 100.0)}, [("a", "b")])
        self.assertGreaterEqual(pos["b"][0] - pos["a"][0], 400.0)


class TestChangeList(unittest.TestCase):

    def test_create_and_changes(self):
        old = parse_code('tree "T" (GeometryNodeTree)\noutput Geometry: Geometry\n'
                         'a = Math(operation="ADD")\nb = Value()\n"c c" = Math(Value=a.Value)\n').trees[0]
        new = parse_code('tree "T" (GeometryNodeTree)\noutput Geometry: Geometry\ninput Count: Int = 2\n'
                         'a = Math(operation="MULTIPLY")\nb = SetPosition()\n"c c" = Math(Value=a.Value)\n'
                         'd = Value()\n').trees[0]
        changes = diff_trees(old, new)
        self.assertIn("interface: add `input Count: Int = 2`", changes)
        self.assertIn('change node "a": operation "ADD" -> "MULTIPLY"', changes)
        self.assertIn('replace node "b": Value -> SetPosition', changes)
        self.assertIn('add node "d" (Value)', changes)
        self.assertFalse(any('"c c"' in c for c in changes))
        self.assertEqual(diff_trees(old, old), [])
        removed = diff_trees(new, old)
        self.assertIn('remove node "d" (Value)', removed)
        self.assertEqual(diff_trees(None, new)[0], 'create tree "T": 4 nodes, 2 interface sockets, 1 linked inputs')


class _Param:
    def __init__(self, identifier, type="STRING", is_output=False, is_readonly=False, default=""):
        self.identifier = identifier
        self.type = type
        self.is_output = is_output
        self.is_readonly = is_readonly
        self.default = default
        self.array_length = 0
        self.is_enum_flag = False


class _Item:
    def __init__(self, name, socket_type, domain="POINT"):
        self.name = name
        self.socket_type = socket_type
        self.domain = domain


class _Items(list):
    def new(self, socket_type, name):
        item = _Item(name, socket_type)
        self.append(item)
        return item


class _CollectionProp:
    """A stand-in for an item-list property (repeat_items...): its new() signature and item fields."""

    def __init__(self, new_params):
        from types import SimpleNamespace
        self.srna = SimpleNamespace(functions={"new": SimpleNamespace(parameters=new_params)})
        self.fixed_type = SimpleNamespace(properties=[
            _Param("rna_type", "POINTER", is_readonly=True), _Param("name"), _Param("socket_type", "ENUM"),
            _Param("color", "FLOAT", is_readonly=True), _Param("domain", "ENUM", default="POINT"),
        ])


class TestBlenderSide(unittest.TestCase):
    """The Blender-side helpers that need no Blender (stand-ins for the RNA)."""

    def setUp(self):
        from blmcp.tools import _node_code_bpy as ncb
        self.ncb = ncb
        self.prop = _CollectionProp([_Param("socket_type", "ENUM"), _Param("name"), _Param("item", "POINTER", True)])

    def test_items_read_name_first_with_extras(self):
        items = _Items([_Item("Geometry", "GEOMETRY"), _Item("Height", "FLOAT", domain="FACE")])
        self.assertEqual(self.ncb.read_items(self.prop, items),
                         [("Geometry", "GEOMETRY"), ("Height", "FLOAT", {"domain": "FACE"})])

    def test_items_change_in_place_add_and_remove(self):
        items = _Items([_Item("Geometry", "GEOMETRY"), _Item("Old", "INT"), _Item("Gone", "INT")])
        first = items[0]
        report = self.ncb.BuildReport()
        self.ncb.apply_items(self.prop, items, [("Geometry", "GEOMETRY"), ("Height", "FLOAT", {"domain": "EDGE"})],
                             report, 3, "Repeat Output.repeat_items")
        self.assertEqual([(i.name, i.socket_type, i.domain) for i in items],
                         [("Geometry", "GEOMETRY", "POINT"), ("Height", "FLOAT", "EDGE")])
        self.assertIs(items[0], first)        # kept: links to it would stay
        self.assertEqual((report.errors, report.not_set), ([], []))
        self.ncb.apply_items(self.prop, items, [("A", "INT", {"wrong": 1})], report, 4, "x")
        self.assertTrue(report.errors[0].startswith("line 4: x item 0: no setting 'wrong'"))

    def test_report_sorts_errors_by_line(self):
        report = self.ncb.BuildReport()
        report.error(9, "b")
        report.error(0, "general")
        report.error(3, "a")
        self.assertEqual(report.sorted_errors(), ["general", "line 3: a", "line 9: b"])

    def test_tree_type_names(self):
        for alias, full in (("geometry", "GeometryNodeTree"), ("GEOMETRY", "GeometryNodeTree"),
                            ("shader", "ShaderNodeTree"), ("material", "ShaderNodeTree"),
                            ("compositor", "CompositorNodeTree"), ("COMPOSITING", "CompositorNodeTree"),
                            ("ShaderNodeTree", "ShaderNodeTree")):
            self.assertEqual(self.ncb.tree_type_name(alias), full)

    def test_plain_values(self):
        self.assertEqual(self.ncb.plain([0.5, (1, 2)]), (0.5, (1, 2)))
        self.assertEqual(self.ncb.plain({"A"}), {"A"})
        self.assertEqual(self.ncb.plain("x"), "x")


class TestTools(unittest.TestCase):

    def test_params_and_results(self):
        from blmcp.tools import build_node_tree_from_code_toolcode as build
        from blmcp.tools import get_node_tree_as_code_toolcode as read
        self.assertEqual(read.Params(), read.Params("", "", "", "", "", None, True, 6000, 0))
        self.assertEqual(build.Params(), build.Params("", "create", False, "", ""))
        self.assertIn("next_node_offset", read.Result._fields)
        self.assertIn("cut_nodes", read.Result._fields)
        for field in ("changes", "not_set", "errors", "status"):
            self.assertIn(field, build.Result._fields)
        # The tool-code gets its parameters as a repr: lists and strings survive it.
        params = read.Params(nodes=["Math", "Group Input"], max_chars=900)
        self.assertEqual(eval(repr(params), {"Params": read.Params}), params)  # pylint: disable=eval-used

    def test_the_spliced_tool_code_needs_no_blmcp(self):
        from blmcp.tools_helpers import toolcode_load_from_filepath
        real = builtins.__import__

        def no_blmcp(name, *args, **kwargs):
            if name.startswith("blmcp"):
                raise ImportError("blmcp is not inside Blender")
            return real(name, *args, **kwargs)

        for tool in ("get_node_tree_as_code", "build_node_tree_from_code"):
            source = toolcode_load_from_filepath(str(_ROOT / "mcp" / "blmcp" / "tools" / (tool + ".py")))
            self.assertIn("def parse_code(", source)
            self.assertIn("def build_tree(", source)
            namespace = {"__name__": "toolcode"}
            builtins.__import__ = no_blmcp
            try:
                exec(compile(source, tool, "exec"), namespace)  # pylint: disable=exec-used
            finally:
                builtins.__import__ = real
            self.assertTrue(callable(namespace["main"]))
            self.assertTrue(callable(namespace["Scratch"]))

    def test_offered_in_the_node_domains(self):
        sys.path.insert(0, str(_ROOT / "mcp"))
        from blmcp import tool_domains
        for domain in ("geometry_nodes", "material", "rendering"):
            for tool in ("get_node_tree_as_code", "build_node_tree_from_code"):
                self.assertIn(tool, tool_domains.DOMAINS[domain], (domain, tool))
        text = (_ROOT / "addon" / "bfa_coworker" / "engine.py").read_text(encoding="utf-8")
        start = text.index("PLAN_SAFE_TOOLS")
        block = text[start:text.index("})", start)]
        self.assertIn('"get_node_tree_as_code"', block)       # reading is plan-safe
        self.assertNotIn('"build_node_tree_from_code"', block)

    def test_the_prompt_has_a_chapter(self):
        text = (_ROOT / "mcp" / "blmcp" / "data" / "prompts.yml").read_text(encoding="utf-8")
        self.assertTrue("get_node_tree_as_code" in text and "build_node_tree_from_code" in text)


if __name__ == "__main__":
    unittest.main()
