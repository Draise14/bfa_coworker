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

## Common Node Patterns

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

## Version Notes (5.2+)

- Node sockets are RNA structs — set `.default_value` on the socket, not the node
- When connecting, check `socket.type` compatibility before linking

## Assigning Materials

```python
obj.data.materials.append(mat)        # Add to material slots
obj.data.materials[0] = mat           # Replace first slot
```
