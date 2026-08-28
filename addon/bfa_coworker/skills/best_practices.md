# Best Practices for Blender Automation

These patterns save tokens by avoiding the most common `execute_blender_code` failures.

## Look Up APIs Before Using Them

Before writing code that uses Blender APIs you're unsure about, **look them up first**.
The bundled doc tools are always available and return accurate results:

- `get_python_api_docs('bpy.types.ShaderNodeBsdfPrincipled')` — exact API reference
- `search_api_docs('base color material')` — keyword search across all API docs
- `search_manual_docs('principled bsdf')` — search the user manual

This avoids retry loops from guessing wrong attribute names.

## Blender 5.3 API Changes

These APIs changed in Blender 5.x — guessing will cause AttributeError loops:

**Subdivision Surface Modifier** — attributes renamed:
    mod = obj.modifiers.new("Subsurf", 'SUBSURF')
    mod.levels = 3           # viewport levels (was 'subdivisions')
    mod.render_levels = 3    # render levels
    mod.subdivision_type = 'CATMULL_CLARK'

**Material Nodes** — Principled BSDF has NO  attribute:
    principled.inputs['Base Color'].default_value = (R, G, B, 1.0)

**Sequencer** —  renamed to  in Blender 5.x

**Auto Smooth** —  removed, use 

When in doubt, use  or  to check the
actual API before writing code.

## Object References

Names auto-append `.001`, `.002` on collision. **Always capture references immediately**
after creation — never look up by assumed name:

```python
obj = bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.active_object  # Capture now, not later
```

## Mode & Selection

- The **active object** and **selection** are distinct. Many operators require both.
- Set mode and selection explicitly before each operator call.
- Operators change selection/active state as side effects — re-set between sequential calls.

## Dependency Graph

Call `bpy.context.view_layer.update()` after changes before reading computed properties
(world matrices, modifier results, evaluated meshes).

## Rotation

Always check an object's `rotation_mode` before writing to rotation properties.
Writing to `rotation_euler` when mode is `'QUATERNION'` is silently ignored.

## Edit Mode / BMesh

In edit mode use the bmesh API, not the regular mesh data API.
Always call `bmesh.update_edit_mesh(mesh)` or `bm.to_mesh(mesh)` after edits —
forgetting this silently loses all changes.

`bpy.context` has NO `selected_edges` / `selected_faces` / `selected_verts` attributes —
edit-mode selections live on the mesh data, not on context. Read them with bmesh:

```python
import bmesh
bm = bmesh.from_edit_mesh(bpy.context.active_object.data)
sel_edges = [e for e in bm.edges if e.select]
sel_faces = [f for f in bm.faces if f.select]
sel_verts = [v for v in bm.verts if v.select]
```

To write selections, set `e.select` / `f.select` / `v.select` then call
`bm.select_flush_mode()`, or use `bmesh.ops.select_*`. `bpy.context.selected_objects`
IS valid — but only in object mode, for objects.

## Return Values

Return structured data (dicts, lists) from executed code — not print() output.
Assign to a dict named `result` for easy parsing.

## GP Brush Access

Wrap ALL GP brush access in `try/except AttributeError`. The `gpencil_paint.brush`,
`gpencil_sculpt.brush`, `gpencil_vertex.brush`, and `gpencil_weight.brush` paths
may not exist on all Blender versions.

## VSE / Sequencer — Use `strips` (Blender 5.x+)

In Blender 5.x, `SequenceEditor.sequences` was renamed to `SequenceEditor.strips`
(top-level) and `SequenceEditor.strips_all` (all strips recursively).

```python
# Blender 5.x (correct)
editor = bpy.context.scene.sequence_editor
if editor:
    for strip in editor.strips:  # NOT editor.sequences
        print(strip.name, strip.type)
```

The old `sequence_editor.sequences` does NOT exist in Blender 5.x.
Always use `strips` or `strips_all` when accessing VSE content.

## Operators vs Data API

- Prefer `bpy.ops.*` for standard actions (adding primitives, applying modifiers).
- Use `bpy.data.*` for precise control or to avoid side effects (batch creation).
- Many operators depend on current mode — wrong mode silently does nothing.

## Delete

Delete objects with unlinking enabled to cleanly remove from all collections:
`bpy.data.objects.remove(obj, do_unlink=True)`.

## Script Authoring Workflow (Avoiding Duplicates)

The system tracks what you create during a turn and tells you what already exists.
When you iterate on the same task, you'll see a context message listing what you've
already made — modify those entities instead of creating duplicates.

1. **Inspect first** — use dedicated scene exploration tools before writing code.
2. **Plan the complete script** — think through all steps before executing.
3. **Execute once** — include all desired properties, modifiers, and materials
   in a single `execute_blender_code` call.
4. **If it fails** — the system undoes the failed attempt automatically.
   Just fix the code and retry.
5. **If you see a context message** listing entities you've already created,
   modify those existing entities rather than creating new ones.

For multiple independent objects, either:
- **Batch them** into one `execute_blender_code` call, or
- **Use different operators** for each (e.g., `primitive_cube_add` for a cube,
  `primitive_uv_sphere_add` for a sphere) — the system detects different
  operators and keeps both results.

## Primitive Operator Keywords

Different primitive operators use DIFFERENT size keywords — using the wrong
one raises ``TypeError: keyword "..." unrecognized``:

| Operator | Keywords (there is no `ring_segments` — that does NOT exist) |
|---|---|
| `primitive_cube_add` | `size=` |
| `primitive_monkey_add` | `size=` |
| `primitive_plane_add` | `size=` |
| `primitive_grid_add` | `size=` + `x_subdivisions=` + `y_subdivisions=` |
| `primitive_uv_sphere_add` | `radius=` + `segments=` + `ring_count=` |
| `primitive_circle_add` | `radius=` + `vertices=` |
| `primitive_cylinder_add` | `radius=` + `depth=` + `vertices=` |
| `primitive_cone_add` | `radius1=` + `radius2=` + `depth=` + `vertices=` |
| `primitive_ico_sphere_add` | `radius=` + `subdivisions=` |
| `primitive_torus_add` | `major_radius=` + `minor_radius=` + `major_segments=` + `minor_segments=` |

When in doubt, call the operator with NO keyword arguments first to use
defaults, then read ``bpy.context.active_object`` to inspect/set dimensions,
or print the operator docstring: ``print(bpy.ops.mesh.primitive_uv_sphere_add.__doc__)``
to see its real parameters.

## Stale References ("StructRNA has been removed")

Never reuse a reference to an object/material/mesh captured in an **earlier tool
call**. Between calls the system may auto-undo a failed attempt — which
**deletes** the objects it created — so old handles point at removed datablocks
and raise ``ReferenceError: StructRNA of type ... has been removed``.

Always re-fetch references fresh right before each use:

```python
obj = bpy.data.objects.get("Bouncing Ball")  # or bpy.context.active_object
if obj is None:
    obj = bpy.ops.mesh.primitive_uv_sphere_add()  # recreate if missing
```

Guard lookups with ``try/except ReferenceError`` and re-acquire on failure.

## Material Nodes — Principled BSDF

ShaderNodeBsdfPrincipled has **NO** `base_color`, `base_color_input`, or
`color` attribute. Access all properties through the node's `inputs`
dictionary using the socket name:

```python
# CORRECT — use inputs dictionary:
mat = bpy.data.materials.new("MyMat")
mat.use_nodes = True
nodes = mat.node_tree.nodes
principled = nodes.new('ShaderNodeBsdfPrincipled')
principled.inputs['Base Color'].default_value = (0.8, 0.2, 0.2, 1.0)
principled.inputs['Metallic'].default_value = 0.0
principled.inputs['Roughness'].default_value = 0.5

# List all available inputs:
print([i.name for i in principled.inputs])
```

Common input names: `'Base Color'`, `'Metallic'`, `'Roughness'`, `'Alpha'`,
`'Emission Color'`, `'Emission Strength'`, `'Subsurface Weight'`.

Prefer the `setup_pbr_material` MCP tool over raw node code when available.

## Collection Color Tags

Use the `set_collection_color_tag` tool to organize collections visually in the Outliner.

**Valid color values:** `NONE`, `COLOR_01` through `COLOR_08`

**Example:**
```python
# Set collection color via MCP tool
set_collection_color_tag(collection_name="Props", color="COLOR_01")
```

**Reading color tags:**
```python
import bpy
col = bpy.data.collections.get("Props")
if col:
    print(col.color_tag)  # e.g., "COLOR_01"
```

**Convention suggestions:**
- `COLOR_01` (Red) — Active/Important objects
- `COLOR_02` (Orange) — Props/Decorations
- `COLOR_03` (Yellow) — Lighting
- `COLOR_04` (Green) — Environment/Terrain
- `COLOR_05` (Blue) — Characters/Animation
- `COLOR_06` (Purple) — Cameras/Effects
- `COLOR_07` (Pink) — Audio
- `COLOR_08` (Brown) — Reference/Temp

## World Orientation

Blender uses a **Z-up, right-handed** coordinate system:

- **Up**: +Z (vertical)
- **Forward**: -Y (into the screen in front view)
- **Right**: +X

Common mistakes:
- Don't confuse Blender's Z-up with game engines that use Y-up.
- `location=(0, 0, 1)` places 1 unit **above** the origin, not forward.
- `rotation_euler=(0, 0, pi)` rotates around the **Z axis** (yaw), not X.
- `primitive_plane_add()` creates on the XY plane (facing up).
- `primitive_cube_add()` centers on the origin — half extends ±Z.

## Collection Operations (Move vs Link)

Blender collections support **multiple parents** — an object can exist in
multiple collections simultaneously. This means:

- `collection.objects.link(obj)` adds the object to a collection but does
  NOT remove it from its original collection.
- To **move** an object, you must BOTH link to the new collection AND
  unlink from the original.

```python
# WRONG — object ends up in both collections:
new_col.objects.link(obj)

# CORRECT — move (link + unlink):
new_col.objects.link(obj)
old_col.objects.unlink(obj)
```

**Moving an object to a new collection:**
```python
obj = bpy.data.objects.get("MyObject")
new_col = bpy.data.collections.get("TargetCollection")

# Find and unlink from all current collections.
for col in obj.users_collection:
    col.objects.unlink(obj)

# Link to the new collection.
new_col.objects.link(obj)
```

**Moving a collection (reparent):**
```python
child_col = bpy.data.collections.get("Child")
parent_col = bpy.data.collections.get("Parent")

# Unlink from current parent.
scene_col = bpy.context.scene.collection
for parent in child_col.children.values():
    parent.children.unlink(child_col)

# Link under new parent.
parent_col.children.link(child_col)
```

