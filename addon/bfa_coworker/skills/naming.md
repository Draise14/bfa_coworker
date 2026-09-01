# Naming Conventions

Use consistent prefixes to keep scenes organized and searchable.

## Collection Prefixes

| Prefix | Purpose |
|--------|---------|
| `COL_` | Collections |
| `SM_` | Static meshes |
| `MAT_` | Materials |
| `LGT_` | Lights |
| `CAM_` | Cameras |
| `ARM_` | Armatures |
| `AN_` | Animations |

## Examples

- `COL_Environment`, `COL_Props`
- `SM_Wall`, `SM_Furniture_Chair`
- `MAT_BrickWall`, `MAT_Metal_Rust`
- `LGT_Sun`, `LGT_Point_Warm`
- `CAM_Main`, `CAM_CloseUp`

## Auto-Append Behavior

Blender auto-appends `.001` when a name collides. Always capture the returned
reference immediately after creation — don't assume the original name:

```python
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.view_layer.objects.active  # obj.name might be "Cube.001"
```
