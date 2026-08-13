# Common Operators Reference

Prefer `bpy.ops.*` for standard actions — they handle defaults and context.
Use the data API (`bpy.data.*`) for precise control or batch operations.

## Object Creation

All use `bpy.ops.<category>.<name>(location=(0,0,0), ...)`:

| Category | Pattern |
|----------|---------|
| Mesh | `mesh.primitive_{cube,uv_sphere,cylinder,plane,monkey,torus,ico_sphere,cone,grid}_add` |
| Light | `object.light_add(type={POINT,SUN,SPOT,AREA})` |
| Camera | `object.camera_add` |
| Empty | `object.empty_add(type=PLAIN_AXES)` |
| Curve | `curve.primitive_{bezier_curve,bezier_circle}_add` |
| Text | `object.text_add` |
| Armature | `object.armature_add` |
| Lattice | `object.lattice_add` |
| GPencil | `object.grease_pencil_add` |
| Metaball | `object.metaball_add(type=BALL)` |
| Light Probe | `object.lightprobe_add(type=SPHERE)` |
| Force Field | `object.effector_add(type=FORCE)` |
| Speaker | `object.speaker_add` |

## Mode Switching

All use `bpy.ops.object.mode_set(mode=...)`:

| Mode | Use For |
|------|---------|
| `OBJECT` | Default object mode |
| `EDIT` | Mesh, curve, armature editing |
| `SCULPT` | Mesh sculpting |
| `VERTEX_PAINT` | Vertex colors |
| `WEIGHT_PAINT` | Vertex group weights |
| `TEXTURE_PAINT` | Image texture painting |
| `POSE` | Armature posing |
| `GPENCIL_EDIT` | Grease Pencil editing |
| `GPENCIL_SCULPT` | Grease Pencil sculpting |
| `GPENCIL_PAINT` | Grease Pencil drawing |
| `GPENCIL_WEIGHT` | Grease Pencil weight paint |
| `GPENCIL_VERTEX` | Grease Pencil vertex paint |
| `EDIT_CURVES` | Curves object editing |
| `SCULPT_CURVES` | Curves object sculpting |

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

| Operator prefix | Required mode |
|----------------|---------------|
| `bpy.ops.mesh.*` | `EDIT` |
| `bpy.ops.sculpt.*` | `SCULPT` |
| `bpy.ops.pose.*` | `POSE` |
| `bpy.ops.paint.*` | `VERTEX_PAINT`, `WEIGHT_PAINT`, or `TEXTURE_PAINT` |
| `bpy.ops.gpencil.*` / `bpy.ops.grease_pencil.*` | Any `GPENCIL_*` mode |
| `bpy.ops.curves.*` | `EDIT_CURVES` or `SCULPT_CURVES` |
| `bpy.ops.texture.*` | `TEXTURE_PAINT` |

Wrong mode → operator fails silently. Always verify mode first.

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

`NodeSocket{Float,Int,Bool,Color,Vector,Shader,String}`

### Compositor Node Groups
For compositor node groups, use `type='CompositorNodeTree'` and socket types like
`NodeSocketColor`, `NodeSocketFloat`, `NodeSocketVector`.

### Geometry Node Groups
For geometry node groups, use `type='GeometryNodeTree'` and geometry-specific
socket types like `NodeSocketGeometry`, `NodeSocketMaterial`.
