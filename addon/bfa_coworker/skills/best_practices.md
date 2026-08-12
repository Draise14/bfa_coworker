# Best Practices for Blender Automation

These patterns save tokens by avoiding the most common `execute_blender_code` failures.

## Object References

Names auto-append `.001`, `.002` on collision. **Always capture references immediately**
after creation — never look up by assumed name:

```python
obj = bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.active_object  # Capture now, not later
```

## Mode & Selection

- The **active object** and **selection** are distinct. Many operators require both.
- Set mode and selection explicitly before each operator call.
- Operators change selection/active state as side effects — re-set between sequential calls.

## Dependency Graph

Call `bpy.context.view_layer.update()` after changes before reading computed properties
(world matrices, modifier results, evaluated meshes).

## Rotation

Always check an object's `rotation_mode` before writing to rotation properties.
Writing to `rotation_euler` when mode is `'QUATERNION'` is silently ignored.

## Edit Mode / BMesh

In edit mode use the bmesh API, not the regular mesh data API.
Always call `bmesh.update_edit_mesh(mesh)` or `bm.to_mesh(mesh)` after edits —
forgetting this silently loses all changes.

## Return Values

Return structured data (dicts, lists) from executed code — not print() output.
Assign to a dict named `result` for easy parsing.

## GP Brush Access

Wrap ALL GP brush access in `try/except AttributeError`. The `gpencil_paint.brush`,
`gpencil_sculpt.brush`, `gpencil_vertex.brush`, and `gpencil_weight.brush` paths
may not exist on all Blender versions.

## Operators vs Data API

- Prefer `bpy.ops.*` for standard actions (adding primitives, applying modifiers).
- Use `bpy.data.*` for precise control or to avoid side effects (batch creation).
- Many operators depend on current mode — wrong mode silently does nothing.

## Delete

Delete objects with unlinking enabled to cleanly remove from all collections:
`bpy.data.objects.remove(obj, do_unlink=True)`.
