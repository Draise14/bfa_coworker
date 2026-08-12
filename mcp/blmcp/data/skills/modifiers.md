# Modifiers

## Modifier Stack Order

Order matters. The stack evaluates top-to-bottom:
1. **Generate** (Mirror, Array, Subdivision) — create geometry, work on base mesh
2. **Deform** (Simple Deform, Shrinkwrap, Lattice) — move existing vertices
3. **Physics/Sim** (Cloth, Soft Body) — simulation always runs last
4. **Constructive** (Solidify, Bevel) — put after deformation for best results

## Geometry Nodes Modifier

### 5.0-5.1
```python
mod = obj.modifiers.new("GN", 'NODES')
mod['["Socket_3"]'] = 1.0  # Set float input
if "Socket_3" in mod:       # Check existence
    pass
```

### 5.2+
```python
mod = obj.modifiers.new("GN", 'NODES')
socket = getattr(mod.properties.inputs, "Socket_3")
socket.value = 1.0  # Set float input
# Check existence:
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
