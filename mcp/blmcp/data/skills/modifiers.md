# Modifiers

## Modifier Stack Order

Order matters. The stack evaluates top-to-bottom:
1. **Generate** (Mirror, Array, Subdivision) — create geometry, work on base mesh
2. **Deform** (Simple Deform, Shrinkwrap, Lattice) — move existing vertices
3. **Physics/Sim** (Cloth, Soft Body) — simulation always runs last
4. **Constructive** (Solidify, Bevel) — put after deformation for best results

## Geometry Nodes Modifier

### ⚠️ CRITICAL: `in modifier` is BROKEN in 5.2+

**Never** use `"Socket_3" in modifier` or `identifier in modifier` — it raises
`TypeError: bpy_prop_collection.__contains__: expected a string or a tuple of strings`
in Blender 5.2+. The old ID property dict was completely removed.

### 5.0-5.1 (old, do NOT use on 5.2+)
```python
mod = obj.modifiers.new("GN", 'NODES')
mod['["Socket_3"]'] = 1.0  # Set float input
if "Socket_3" in mod:       # ❌ CRASHES on 5.2+
    pass
```

### 5.2+ (correct)
```python
mod = obj.modifiers.new("GN", 'NODES')
socket = getattr(mod.properties.inputs, "Socket_3")
socket.value = 1.0  # Set float input
# Check existence (use try/except, NOT `in`):
try:
    getattr(mod.properties.inputs, "Socket_3")
except AttributeError:
    pass  # Socket doesn't exist
```

## Common Modifier Operations

```python
# Add
mod = obj.modifiers.new(name="Array", type='ARRAY')
mod.count = 3

# Apply (must be in Object mode, active obj selected)
bpy.context.view_layer.objects.active = obj
bpy.ops.object.modifier_apply(modifier="Array")

# Remove
obj.modifiers.remove(mod)

# Toggle visibility
mod.show_viewport = False
mod.show_render = True
```

## Version-Aware Helper

Always check `bpy.app.version` before accessing GN modifier inputs:

```python
if bpy.app.version >= (5, 2, 0):
    socket = getattr(mod.properties.inputs, name)
    return socket.value
else:
    return mod['["{:s}"]'.format(name)]
```
