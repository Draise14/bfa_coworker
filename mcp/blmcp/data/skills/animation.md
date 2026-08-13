# Animation

## Keyframe Insertion (SAFE — always use this)

`keyframe_insert()` is the **only safe way** to create keyframes in Blender 5.x.
It handles action, slot, layer, strip, and channelbag creation internally.
**Never manually create slots, layers, strips, or channelbags** — doing so can
leave the animation data in a corrupted state that causes a hard crash
(EXCEPTION_ACCESS_VIOLATION) during EEVEE viewport redraw.

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

## Modifying Existing Keyframes

To modify keyframe interpolation or handles after insertion, use the
read-only helper below to find existing F-Curves. **Never create new slots
or channelbags** — only read what `keyframe_insert()` already created.

```python
import bpy
obj = bpy.context.active_object
action = obj.animation_data.action

# Use the read-only helper to get existing F-Curves
slot = obj.animation_data.action_slot
if slot is not None and action is not None:
    layer = action.layers[0] if action.layers else None
    if layer is not None:
        strip = layer.strips[0] if layer.strips else None
        if strip is not None:
            channelbag = strip.channelbag(slot, ensure=False)
            if channelbag is not None:
                for fc in channelbag.fcurves:
                    if fc.data_path == "location" and fc.array_index == 0:
                        for kf in fc.keyframe_points:
                            kf.interpolation = 'LINEAR'
                            kf.handle_left_type = 'AUTO'
                            kf.handle_right_type = 'AUTO'
```

### Version-Aware Helper (READ-ONLY — never creates slots)

```python
def get_action_fcurves(action, obj):
    """Get all F-Curves from an action for a given object, works across Blender 5.0+.
    
    READ-ONLY: This helper never creates slots, layers, strips, or channelbags.
    If any part of the chain is missing, it returns [] safely.
    """
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

## Deleting Keyframes

```python
obj.keyframe_delete(data_path="location", frame=10)
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

- **`Action.fcurves` REMOVED** — do NOT access `action.fcurves` directly.
  Use `keyframe_insert()` for all keyframe creation.
- **`keyframe_insert()` API unchanged** — this is the safe path.
- **WARNING**: Manually creating slots with `action.slots.new()` or channelbags
  with `strip.channelbag(slot, ensure=True)` can corrupt the animation data
  and cause a hard crash during EEVEE viewport redraw. Only use
  `keyframe_insert()` for writes.
