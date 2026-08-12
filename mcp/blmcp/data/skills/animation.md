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
# Get fcurve for a property
fcurves = obj.animation_data.action.fcurves
loc_curve = None
for fc in fcurves:
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

## Version Notes (5.2+)

- F-Curve API unchanged
- `keyframe_insert()` API unchanged
