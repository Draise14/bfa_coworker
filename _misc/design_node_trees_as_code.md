# Node trees as code (#480) -- design note

Our own design, from the need (white-room): Blender's Python API (RNA introspection)
and this repository only.

## Why

The model used to see a tree as JSON (`get_active_node_tree`) and change it with `bpy`
code written from memory: many tokens, and wrong node / socket names.  A compact text
that Blender itself checks costs far fewer tokens, reads like the code models know, and
round-trips: read a tree, change two lines, write it back.

## Pieces

| File | What |
|---|---|
| `mcp/blmcp/tools/_node_code_format.py` | bpy-free: specs, printer, parser, name rules, suggestions, layout, change list |
| `mcp/blmcp/tools/_node_code_bpy.py` | Blender side: read a tree into specs, build specs into a tree |
| `get_node_tree_as_code` | read-only tool (both modules spliced into its tool-code) |
| `build_node_tree_from_code` | destructive tool, one undo step |
| `tests/test_node_code.py` | round trips, errors, names, layout, tools |

## The text

```
tree "Inner" (GeometryNodeTree)
output Geometry: Geometry
input Geometry: Geometry
input Offset: Float(min_value=0.0, max_value=2.0) = 0.5
"Group Input" = GroupInput()
"Combine XYZ" = CombineXYZ(Z="Group Input".Offset)
"Set Position" = SetPosition(Geometry="Group Input".Geometry, Offset="Combine XYZ".Vector)
"Group Output" = GroupOutput(Geometry="Set Position".Geometry)

tree "Main" (GeometryNodeTree, is_modifier=True)
output Geometry: Geometry
input Geometry: Geometry
panel Settings
input Count: Int(min_value=1, max_value=10) = 3
end panel
"Group Input" = GroupInput()
"Repeat Input" = RepeatInput(paired_output="Repeat Output", Iterations="Group Input".Count, Geometry="Group Input".Geometry)
Group = Group(node_tree="Inner", Geometry="Repeat Input".Geometry, Offset=0.25)
"Repeat Output" = RepeatOutput(repeat_items=[("Geometry", "GEOMETRY"), ("Height", "FLOAT")], Geometry=Group.Geometry)
Join = JoinGeometry(Geometry=["Repeat Output".Geometry, muted(Cube.Mesh)])
"Group Output" = GroupOutput(Geometry=Join.Geometry)
frame Frame (label="Twist"): Group, Join
```

Statements (one per line; a line continues while a bracket is open; `#` comments):
`tree NAME (TreeType, prop=value)`, `material NAME (prop=value)`,
`input|output NAME: Type(option=value) = default`, `panel NAME (option=value)` ... `end panel`,
`VAR = NodeType(key=value, ...)`, `frame VAR (key=value): child, ...`, `del VAR, ...`.

Rules:

* Names are identifiers or double-quoted strings; `VAR` is the node's name in Blender.
* Node type = `bl_idname` minus a prefix, tried per tree type in a fixed order
  (Geometry Nodes: `GeometryNode`, `FunctionNode`, `ShaderNode`, `Node`).  A short name is
  printed only if it resolves back to the same `bl_idname`; the full name is always accepted.
* A bare key that is one of the node's RNA properties (beyond the base `Node` ones) is a
  setting; any other key, and every quoted key, is an input socket.  Sockets are matched by
  identifier first, then the name of an enabled socket, then any name, then any case.  The
  printer writes the name when that finds the socket back, else the identifier
  (`Value_001` on Math).
* Node-level keys: `label`, `mute`, `hide`, `color`, `width` (+ a few base flags when not
  default), `paired_output` (zones), `outputs={...}` (values kept on outputs, e.g. Value, RGB),
  `hidden_inputs=[...]` / `hidden_outputs=[...]` (sockets hidden with Ctrl+H).
* Values: literals, tuples, lists, sets (enum flags), dicts, `1e999` for infinity; datablocks
  by name; nested RNA structs as `Name(field=value)` (`ColorRamp(elements=[(pos, rgba)])`,
  `CurveMapping(curves=[[(x, y), (x, y, "VECTOR")]])`, `ImageUser(...)`); dynamic item lists
  from the collection's own `new()` signature (`repeat_items=[("Name", "TYPE")]`,
  `enum_items=["A"]`, `index_switch_items=3`).
* Links are values: `node.Socket`, `node["Socket Name"]`, `node[0]`, `muted(...)`, a list for
  multi-input sockets (in link order).
* Parsing uses `ast` on each statement with a whitelist of node kinds; the text is never run.

## Defaults

Only what differs from a *new node of the same type* is printed.  The reader creates those
nodes in a temporary node group (removed before it returns).  For socket values the
reference node also gets the node's printed settings (so a Mix in Color mode is compared
with a new Mix in Color mode); sockets the reference doesn't have use the RNA default.

## Building

* Phase 1: every block is built into a temporary datablock -- a new tree for `create`, a copy
  of the existing tree (or material) for `replace` / `update`.  Any error (unknown node type,
  setting, socket, node, enum choice; a name clash) stops here, the temporaries are removed
  and the scene is unchanged.  `dry_run` stops here too and returns the change list (the
  temporary result read back and compared with the current tree).
* Phase 2: `create` keeps the temporary under its final name; `replace` / `update` run the
  same build on the real tree (same datablock: modifiers and materials keep using it;
  interface sockets matched by in/out + name keep their identifiers, so modifier values stay).
* Values Blender refuses (a missing image, a value out of range) don't stop the build; they
  are listed in `not_set`.
* Nested groups: built first.  An existing group whose text is the same is reused; a
  different one is an error in `create` (with the choices) and rebuilt in `replace` / `update`.
* Layout: columns by the longest path to an output (each node sits just left of its nearest
  consumer, zone inputs left of their outputs), rows ordered by the nodes they feed.
  `replace` / `update` keep the place of nodes that already existed.
* One undo step (`ed.undo_push` after the build, when a window exists).

## Not covered (known gaps)

See the PR for the sweep numbers.  By design: node locations are not in the text (the builder
lays out); frame height, custom properties, drivers / animation on node
values, and link-only `default_value`s (a linked input's hidden value) are not carried.
