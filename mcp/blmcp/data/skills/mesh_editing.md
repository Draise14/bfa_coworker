# Mesh Editing

## BMesh Workflow

In Edit Mode, always use the bmesh API:

```python
import bpy
import bmesh

obj = bpy.context.active_object
mesh = obj.data

bm = bmesh.from_edit_mesh(mesh)  # Get BMesh in edit mode

# --- Do edits ---
bm.verts.new((1.0, 0.0, 0.0))
bmesh.ops.extrude_face_region(bm, geom=[...])
# ...

bmesh.update_edit_mesh(mesh)  # REQUIRED — flushes edits to mesh
```

**Critical**: Forgetting `bmesh.update_edit_mesh()` silently loses all changes.

## Entering/Exiting Edit Mode

```python
# Enter
bpy.ops.object.mode_set(mode='EDIT')

# Exit back to Object
bpy.ops.object.mode_set(mode='OBJECT')
```

## Common Operations

```python
# Select all
bpy.ops.mesh.select_all(action='SELECT')

# Delete geometry
bpy.ops.mesh.delete(type='VERT')  # VERT, EDGE, FACE

# Extrude
bpy.ops.mesh.extrude_region_move()

# Merge by distance
bpy.ops.mesh.remove_doubles(threshold=0.001)

# Create edge/face
bpy.ops.mesh.edge_face_add()  # Fill or join

# Subdivide
bpy.ops.mesh.subdivide(number_cuts=1)

# Loop cut
bpy.ops.mesh.loopcut_slide(MESH_OT_loopcut={...})

# UV unwrap
bpy.ops.uv.unwrap(method='ANGLE_BASED')
```

## Mesh Data Access (Object Mode)

```python
obj = bpy.context.active_object
mesh = obj.data

# Vertices (only accessible in Object mode via evaluated mesh)
depsgraph = bpy.context.evaluated_depsgraph_get()
eval_obj = obj.evaluated_get(depsgraph)
eval_mesh = eval_obj.data
for v in eval_mesh.vertices:
    print(v.co)
```

## Version Notes (5.2+)

- Normal editing operators unchanged
- Sculpt mode mesh access unchanged
