# Transformations & Coordinate Spaces

## Coordinate Spaces

- **World** — global origin. Use `obj.matrix_world` for reliable world-space positions.
- **Local** — relative to parent. `obj.location`/`rotation_euler`/`scale` operate here.
- **Object** — the object's own coordinate system. Mesh vertices are in object space.

## Reading World Position

```python
world_pos = obj.matrix_world.translation  # Vector
# Or for any point in object space:
world_point = obj.matrix_world @ Vector((0, 0, 0))
```

## Setting Transform

```python
obj.location = (1.0, 2.0, 3.0)        # Local translation
obj.rotation_euler = (0, 0, 1.57)     # Euler rotation in radians
obj.scale = (2.0, 2.0, 2.0)           # Uniform scale
```

## Rotation Modes

Always check `rotation_mode` before writing. Writing to the wrong property is silently ignored:

```python
obj.rotation_mode = 'XYZ'             # Set Euler mode
obj.rotation_euler = (0, 0, 1.57)

obj.rotation_mode = 'QUATERNION'      # Set Quaternion mode
obj.rotation_quaternion = (1, 0, 0, 0)
```

Modes: `'QUATERNION'`, `'XYZ'`, `'XZY'`, `'YXZ'`, `'YZX'`, `'ZXY'`, `'ZYX'`, `'AXIS_ANGLE'`

## Parenting

```python
# Parent child to parent (preserve visual position)
child.parent = parent
child.matrix_parent_inverse = parent.matrix_world.inverted()
```

## Applying Transforms

```python
# Apply all transforms (need to be in Object mode, obj selected + active)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
```

## The 3D Cursor

```python
bpy.context.scene.cursor.location = (0, 0, 0)  # World-space reference
```

## Dependency Graph Warning

After transform changes, call `bpy.context.view_layer.update()` before reading
`matrix_world` or other computed properties.
