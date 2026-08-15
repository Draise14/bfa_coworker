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

## VSE / Sequencer — Use `strips` (Blender 5.x+)

In Blender 5.x, `SequenceEditor.sequences` was renamed to `SequenceEditor.strips`
(top-level) and `SequenceEditor.strips_all` (all strips recursively).

```python
# Blender 5.x (correct)
editor = bpy.context.scene.sequence_editor
if editor:
    for strip in editor.strips:  # NOT editor.sequences
        print(strip.name, strip.type)
```

The old `sequence_editor.sequences` does NOT exist in Blender 5.x.
Always use `strips` or `strips_all` when accessing VSE content.

## Operators vs Data API

- Prefer `bpy.ops.*` for standard actions (adding primitives, applying modifiers).
- Use `bpy.data.*` for precise control or to avoid side effects (batch creation).
- Many operators depend on current mode — wrong mode silently does nothing.

## Delete

Delete objects with unlinking enabled to cleanly remove from all collections:
`bpy.data.objects.remove(obj, do_unlink=True)`.

## Script Authoring Workflow (Avoiding Duplicates)

The system tracks what you create during a turn and tells you what already exists.
When you iterate on the same task, you'll see a context message listing what you've
already made — modify those entities instead of creating duplicates.

1. **Inspect first** — use dedicated scene exploration tools before writing code.
2. **Plan the complete script** — think through all steps before executing.
3. **Execute once** — include all desired properties, modifiers, and materials
   in a single `execute_blender_code` call.
4. **If it fails** — the system undoes the failed attempt automatically.
   Just fix the code and retry.
5. **If you see a context message** listing entities you've already created,
   modify those existing entities rather than creating new ones.

For multiple independent objects, either:
- **Batch them** into one `execute_blender_code` call, or
- **Use different operators** for each (e.g., `primitive_cube_add` for a cube,
  `primitive_uv_sphere_add` for a sphere) — the system detects different
  operators and keeps both results.
