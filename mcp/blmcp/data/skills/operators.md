# Common Operators Reference

Prefer `bpy.ops.*` for standard actions — they handle defaults and context.
Use the data API (`bpy.data.*`) for precise control or batch operations.

## Object Creation

```python
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0,0,0))
bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(0,0,0))
bpy.ops.mesh.primitive_cylinder_add(radius=1.0, depth=2.0, location=(0,0,0))
bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0,0,0))
bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0,0,0))
```

## Mode Switching

```python
bpy.ops.object.mode_set(mode='OBJECT')    # From any mode
bpy.ops.object.mode_set(mode='EDIT')      # Enter edit mode
bpy.ops.object.mode_set(mode='SCULPT')    # Enter sculpt mode
bpy.ops.object.mode_set(mode='POSE')      # Enter pose mode (armature)
```

## Modifier Operations

```python
bpy.ops.object.modifier_apply(modifier="Subdivision")
bpy.ops.object.modifier_remove(modifier="Array")
bpy.ops.object.modifier_move_up(modifier="Bevel")
bpy.ops.object.modifier_move_down(modifier="Bevel")
```

## Parenting

```python
bpy.ops.object.parent_set(type='OBJECT', keep_transform=True)
bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')
```

## Joining/Separating

```python
bpy.ops.object.join()                    # Join selected into active
bpy.ops.mesh.separate(type='SELECTED')   # Separate selection from mesh
```

## Object Operations

```python
bpy.ops.object.duplicate()               # Duplicate selected (linked=False by default)
bpy.ops.object.delete(use_global=False)  # Delete selected
bpy.ops.object.shade_smooth()            # Smooth shading
bpy.ops.object.shade_flat()              # Flat shading
bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')  # Set origin to geometry center
```

## Mode-Dependent Operators

Many operators require specific modes:
- `bpy.ops.mesh.*` — Edit mode only
- `bpy.ops.sculpt.*` — Sculpt mode only
- `bpy.ops.pose.*` — Pose mode only (armature selected)

Wrong mode → operator fails silently or does nothing. Always verify mode first.

## Node Group Creation (5.x Interface API)

In Blender 5.x, node group inputs/outputs are managed through `NodeTreeInterface`,
NOT by adding `NodeGroupInput`/`NodeGroupOutput` nodes directly.

### Creating a Node Group
```python
# Create the group (type can be 'ShaderNodeTree', 'CompositorNodeTree', 'GeometryNodeTree')
group = bpy.data.node_groups.new(name="MyGroup", type='ShaderNodeTree')

# Add input sockets via the interface
group.interface.new_socket(
    name="Color",
    description="Input color",
    in_out='INPUT',
    socket_type='NodeSocketColor',
)

# Add output sockets
group.interface.new_socket(
    name="Output",
    description="Shader output",
    in_out='OUTPUT',
    socket_type='NodeSocketShader',
)

# Group Input and Output nodes are auto-created by the interface
group_input = group.nodes.get("Group Input")
group_output = group.nodes.get("Group Output")
```

### Socket Types
| socket_type | Use |
|-------------|-----|
| `NodeSocketFloat` | Float value |
| `NodeSocketInt` | Integer |
| `NodeSocketBool` | Boolean |
| `NodeSocketColor` | RGBA color |
| `NodeSocketVector` | XYZ vector |
| `NodeSocketShader` | Shader socket |
| `NodeSocketString` | String |

### Compositor Node Groups
For compositor node groups, use `type='CompositorNodeTree'` and socket types like
`NodeSocketColor`, `NodeSocketFloat`, `NodeSocketVector`.

### Geometry Node Groups
For geometry node groups, use `type='GeometryNodeTree'` and geometry-specific
socket types like `NodeSocketGeometry`, `NodeSocketMaterial`.
