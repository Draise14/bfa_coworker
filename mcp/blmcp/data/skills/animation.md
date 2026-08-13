# Animation

## Keyframe Insertion

Always set the current frame first:

```python
scene = bpy.context.scene
obj = bpy.context.active_object

scene.frame_set(1)
obj.location = (0, 0, 0)
obj.keyframe_insert(data_path="location", frame=1)

scene.frame_set(50)
obj.location = (5, 0, 0)
obj.keyframe_insert(data_path="location", frame=50)
```

## F-Curves

```python
# In Blender 5.0+, Action.fcurves was replaced by the layered animation system.
# F-Curves now live in: action.layers -> strips -> channelbag(slot) -> fcurves

import bpy
obj = bpy.context.active_object
action = obj.animation_data.action

# Get the slot for this object
slot = action.slots.new(obj.id_type, obj.name) if not obj.animation_data.action_slot else obj.animation_data.action_slot

# Get or create the first keyframe layer's strip channelbag
layer = action.layers[0] if action.layers else action.layers.new("Layer")
strip = layer.strips[0] if layer.strips else layer.strips.new(type='KEYFRAME')
channelbag = strip.channelbag(slot, ensure=True)

# Find an F-Curve by data_path and array_index
loc_curve = None
for fc in channelbag.fcurves:
    if fc.data_path == "location" and fc.array_index == 0:  # X
        loc_curve = fc
        break

# Set interpolation
for kf in loc_curve.keyframe_points:
    kf.interpolation = 'LINEAR'  # CONSTANT, LINEAR, BEZIER

# Modify handles (Bezier only)
kf.handle_left_type = 'AUTO'
kf.handle_right_type = 'AUTO'
```

### Version-Aware Helper

```python
def get_action_fcurves(action, obj):
    """Get all F-Curves from an action for a given object, works across Blender 5.0+."""
    slot = obj.animation_data.action_slot
    if slot is None:
        return []
    layer = action.layers[0] if action.layers else None
    if layer is None:
        return []
    strip = layer.strips[0] if layer.strips else None
    if strip is None:
        return []
    channelbag = strip.channelbag(slot, ensure=False)
    if channelbag is None:
        return []
    return list(channelbag.fcurves)
```

## Frame Range

```python
bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = 250
```

## Auto Keying

```python
bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
```

## Common Data Paths

| Property | data_path |
|----------|-----------|
| Location | `"location"` |
| Rotation | `"rotation_euler"` or `"rotation_quaternion"` |
| Scale | `"scale"` |
| Visibility | `"hide_viewport"` |
| Shape Key | `'key_blocks["Key 1"].value'` |

## Version Notes (5.0+)

- **`Action.fcurves` REMOVED** — use `layer.strips[i].channelbag(slot).fcurves` instead
- `keyframe_insert()` API unchanged
