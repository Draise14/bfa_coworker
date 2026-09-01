# Blender 5.3 Specifics

This addon was developed on Blender 5.3. These are the known specifics.

## Sandbox Policy

Blender 5.3+ enforces a sandbox policy for addon directories. Any subdirectory
matching a known top-level Python package name (rich/, click/, httpx/, etc.)
will trigger a policy violation — even if never imported.

Dependencies are stored outside the addon tree at `~/.cache/bfa_coworker/vendor_deps/`.

## UI Layout

`UILayout.label_multiline(text=...)` is available for native multi-line text wrapping.
Prefer this over manual `textwrap.fill()` when drawing multi-line content.

## Online Access

`bpy.app.online_access` controls whether the addon can make network requests.
Auto-start is skipped when offline unless `--online-mode` is passed at launch.

## Blender 5.x API Changes

### VSE / Sequencer — `strips` replaces `sequences`

In Blender 5.x, `SequenceEditor.sequences` was renamed to `SequenceEditor.strips`
(top-level strips only) and `SequenceEditor.strips_all` (all strips recursively
including metastrips).

```python
# Blender 5.x (correct)
editor = bpy.context.scene.sequence_editor
if editor:
    for strip in editor.strips:       # Top-level strips
        print(strip.name, strip.type)
    for strip in editor.strips_all:   # All strips (including inside metastrips)
        print(strip.name, strip.type)
```

The old `sequence_editor.sequences` does NOT exist in Blender 5.x.
Always use `strips` or `strips_all` when accessing VSE content.

### NodesModifier — `panels` Removed

`NodesModifier.panels` was removed in 5.3. Use `NodeTreeInterface.root_panel`
to access the top-level panel in the node tree interface hierarchy:

```python
# 5.3+ (correct)
panel = mod.node_group.interface.root_panel
```

### Object & PoseBone — `convert_rotation_mode()`

New method to convert rotation between modes:

```python
obj.convert_rotation_mode(mode='QUATERNION')
pose_bone.convert_rotation_mode(mode='XYZ')
```

### Preferences — Renamed Properties

- `geometry_nodes_stack_limit` → `nodes_stack_limit` (on PreferencesSystem)
- `use_inverse_smooth_pressure` → `use_smooth_pressure` (on Brush)

### Theme — Removed Properties

- `ThemeSpaceGeneric.header_text`, `header_text_hi`, `title` — removed
- `ThemeSpaceGradient.header_text`, `header_text_hi`, `title` — removed
- `ThemeFileBrowser.selected_file` — removed

### WindowManager — Undo Stack

Read-only access to the undo stack:

```python
stack = bpy.context.window_manager.undo_stack
```

### bpy.data.all_ids — Order Changed

The order of IDs in `bpy.data.all_ids` is now an internal implementation detail.
Do NOT rely on or assume any specific ordering.

### Mesh — `use_auto_smooth` Removed

`Mesh.use_auto_smooth` was removed in Blender 5.3. Auto-smooth is now implicit
when `auto_smooth_angle > 0`. Set the angle directly:

```python
# Blender 5.3+ (correct)
mesh.auto_smooth_angle = radians(30)  # Enables auto-smooth at 30°

# To disable auto-smooth:
mesh.auto_smooth_angle = 0
```

The old `mesh.use_auto_smooth = True` / `mesh.use_auto_smooth = False` pattern
will raise `AttributeError: 'Mesh' object has no attribute 'use_auto_smooth'`.

### Material Nodes — Default Nodes Already Exist

When you create a new material and set `mat.use_nodes = True`, Blender
automatically creates a Principled BSDF node and a Material Output node
already connected.  Do NOT try to create them manually with
`nodes.new('BSDF_PRINCIPLED')` or `nodes.new('OUTPUT_MATERIAL')` — these
node type identifiers may not work in Bforartists 5.3.

Instead, find the existing nodes by iterating:

```python
mat = bpy.data.materials.new(name="MyMat")
mat.use_nodes = True
nodes = mat.node_tree.nodes
principled = None
for node in nodes:
    if node.type == 'BSDF_PRINCIPLED':
        principled = node
        break
# Now set inputs on principled:
principled.inputs["Base Color"].default_value = (1, 0, 0, 1)
principled.inputs["Roughness"].default_value = 0.2
```

To inspect available input names on a node, use:
```python
print([i.name for i in principled.inputs])
```

### Modifier Creation

In Bforartists, use `bpy.ops.object.modifier_add(type='NODES')` to add a
Geometry Nodes modifier. The `obj.modifiers.new("GN", 'NODES')` pattern
may not work the same way.

```python
# Bforartists: use operator
bpy.ops.object.modifier_add(type='NODES')
mod = bpy.context.view_layer.objects.active.modifiers[-1]  # Capture last added

# Then set the node group
mod.node_group = bpy.data.node_groups.get("MyGroup")
```

> **MCP bridge note:** code sent through `execute_blender_code` runs in a
> worker thread where `bpy.context.active_object` does NOT exist (Blender
> context is thread-local). Use `bpy.context.view_layer.objects.active`
> instead — operators still set the active object on the view layer, so this
> works right after `primitive_*_add` and similar calls.

### Sequencer Modifiers

Sequencer strip modifiers in Bforartists may use different type identifiers
than Blender. Always check available modifier types on a strip before
assuming a specific type exists.

```python
# Check available modifier types
strip = bpy.context.scene.sequence_editor.active_strip
if strip and hasattr(strip, 'modifiers'):
    for mod in strip.modifiers:
        print(mod.type, mod.name)
```

### Animation — Layered Animation System (5.0+)

`Action.fcurves` was **removed** in Blender 5.0. F-Curves now live in the
layered animation system: `action.layers → strips → channelbag(slot) → fcurves`.

**CRITICAL**: Never manually create slots, layers, strips, or channelbags.
The `keyframe_insert()` API handles all of this internally and is the **only
safe way** to create keyframes. Manually creating slots with
`action.slots.new()` or channelbags with `strip.channelbag(slot, ensure=True)`
can leave the animation data in a corrupted state that causes a **hard crash**
(EXCEPTION_ACCESS_VIOLATION in `channelbag_for_action_slot`) during EEVEE
viewport redraw.

```python
# SAFE — always use keyframe_insert()
obj.keyframe_insert(data_path="location", frame=10)

# DANGEROUS — never do this manually:
#   action.slots.new(...)
#   strip.channelbag(slot, ensure=True)
```

To read existing F-Curves (read-only), use the helper from the `animation.md`
skill. Never create slots or channelbags yourself.

### General Rule

When in doubt, prefer `bpy.ops.*` operators over direct data API access.
Bforartists maintains operator parity with Blender but may differ in
internal data structures. Operators handle defaults and context correctly
on both platforms.
