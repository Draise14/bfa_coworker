# Materials & Shading

## PBR Workflow

Blender uses the Principled BSDF shader for physically-based rendering:

- **Base Color** — albedo/diffuse color (sRGB, no lighting info baked in)
- **Metallic** — 0.0 for dielectrics, 1.0 for metals (use grayscale values in between sparingly)
- **Roughness** — 0.0 mirror, 1.0 fully diffuse
- **Specular** — leave at default 0.5 for most cases (controls IOR at normal incidence)
- **Normal** — tangent-space normal map (use Normal Map node, Color Space: Non-Color)

## Creating Materials

```python
mat = bpy.data.materials.new(name="MAT_MyMaterial")
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links
nodes.clear()  # Remove default nodes if starting fresh
```

## Correct Node Type Identifiers

Use `nodes.new(type=...)` with these bl_idname values:

| Category | bl_idname values |
|----------|-----------------|
| Shaders | `ShaderNodeBsdfPrincipled`, `ShaderNodeEmission`, `ShaderNodeMixShader`, `ShaderNodeAddShader` |
| Output | `ShaderNodeOutputMaterial` |
| Textures | `ShaderNodeTexNoise`, `ShaderNodeTexImage`, `ShaderNodeTexCoord` |
| Color | `ShaderNodeValToRGB`, `ShaderNodeSeparateColor`, `ShaderNodeCombineColor`, `ShaderNodeRGBCurve` |
| Vector/Math | `ShaderNodeMapping`, `ShaderNodeNormalMap`, `ShaderNodeBump`, `ShaderNodeMath` |
| Group | `NodeGroupInput`, `NodeGroupOutput`, `NodeFrame` |

## Common Node Patterns

### Emission Material (no lighting needed)
```python
mat = bpy.data.materials.new(name="MAT_Emit")
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links
nodes.clear()

emit = nodes.new('ShaderNodeEmission')
emit.location = (-200, 0)
emit.inputs[0].default_value = (0.55, 0.60, 0.81, 1.0)  # RGBA color
emit.inputs[1].default_value = 1.0  # Strength

output = nodes.new('ShaderNodeOutputMaterial')
output.location = (0, 0)
links.new(emit.outputs['Emission'], output.inputs['Surface'])
```

### Texture → Principled
```
Image Texture (Color: Non-Color) → Normal Map → Principled BSDF Normal
Image Texture (Color: sRGB) → Principled BSDF Base Color
Image Texture (Color: Non-Color) → Principled BSDF Roughness
```

### Procedural Noise
```
Noise Texture (Fac) → ColorRamp → Principled BSDF Roughness
```

### Glass / Transparent
Set Principled BSDF Transmission to 1.0, Roughness low. Use Cycles for best results.

## Node Groups (5.x Interface API)

In Blender 5.x, node group inputs/outputs are managed through `NodeTreeInterface`,
NOT by adding `NodeGroupInput`/`NodeGroupOutput` nodes directly.

### Creating a Node Group with Custom Inputs
```python
# Create the node group
group = bpy.data.node_groups.new(name="MyGroup", type='ShaderNodeTree')

# Add input sockets via the interface (5.x API)
group.interface.new_socket(
    name="Color",
    description="Input color",
    in_out='INPUT',
    socket_type='NodeSocketColor',
)
group.interface.new_socket(
    name="Strength",
    description="Input strength",
    in_out='INPUT',
    socket_type='NodeSocketFloat',
)

# Add output sockets
group.interface.new_socket(
    name="Output",
    description="Shader output",
    in_out='OUTPUT',
    socket_type='NodeSocketShader',
)

# Now add nodes to the group
nodes = group.nodes
links = group.links
nodes.clear()

# Group Input and Output nodes are auto-created by the interface
group_input = group.nodes.get("Group Input")
group_output = group.nodes.get("Group Output")

# Add internal nodes
emit = nodes.new('ShaderNodeEmission')
emit.location = (-200, 0)
links.new(group_input.outputs['Color'], emit.inputs['Color'])
links.new(group_input.outputs['Strength'], emit.inputs['Strength'])
links.new(emit.outputs['Emission'], group_output.inputs['Output'])
```

### Socket Types for `new_socket()`
| socket_type | Purpose |
|-------------|---------|
| `NodeSocketFloat` | Float value |
| `NodeSocketInt` | Integer |
| `NodeSocketBool` | Boolean |
| `NodeSocketColor` | RGBA color |
| `NodeSocketVector` | XYZ vector |
| `NodeSocketShader` | Shader socket |
| `NodeSocketString` | String |

### Assigning a Material to Objects
```python
# Single material to one object
obj.data.materials.append(mat)

# Replace first slot
obj.data.materials[0] = mat

# Assign to all objects in a collection
for obj in collection.objects:
    if obj.type == 'MESH':
        if not obj.data.materials:
            obj.data.materials.append(mat)
        else:
            obj.data.materials[0] = mat
```

## Version Notes (5.2+)

- Node sockets are RNA structs — set `.default_value` on the socket, not the node
- When connecting, check `socket.type` compatibility before linking
- `NodeTreeInterface.new_socket()` replaces the old `group_inputs.new()` pattern
- Group Input/Output nodes are auto-created by the interface — do NOT create them manually
