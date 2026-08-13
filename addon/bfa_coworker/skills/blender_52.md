# Blender 5.2 API Changes

These are breaking changes from 5.0-5.1. All patterns below apply to 5.2+.

## Geometry Nodes Modifier — ID Property Dict REMOVED

⚠️ **CRITICAL: `identifier in modifier` is BROKEN in 5.2+**

The old `modifier['["Socket_N"]']` dict-style access is completely gone.
Using `"Socket_3" in modifier` raises:
`TypeError: bpy_prop_collection.__contains__: expected a string or a tuple of strings`

Use `getattr(modifier.properties.inputs, ...)` instead:

```python
# Reading a socket value (5.2+)
socket_ptr = getattr(modifier.properties.inputs, "Socket_3")
value = socket_ptr.value

# Checking socket existence (5.2+)
try:
    socket_ptr = getattr(modifier.properties.inputs, identifier)
except AttributeError:
    # socket does not exist
```

**Never** use `identifier in modifier` — it raises TypeError in 5.2+.

## Panel Drawing (5.2+)

```python
socket_ptr = getattr(modifier.properties.inputs, socket_id)
row.prop(socket_ptr, "value", text="Label")
```

The socket is an RNA struct, so you draw `.value` on it — not the modifier directly.

## Socket Type Enum Values

Use RNA enum identifiers: `"FLOAT"`, `"INT"`, `"BOOLEAN"`, `"VECTOR"`, `"RGBA"`.
Do NOT use `"VALUE"` — it does not exist anymore.

## GP Sculpt Brush

`context.tool_settings.gpencil_sculpt` exists but `.brush` does NOT exist in 5.2+.
Use `try/except AttributeError` for ALL GP tool_settings brush paths:
`gpencil_paint.brush`, `gpencil_sculpt.brush`, `gpencil_vertex.brush`, `gpencil_weight.brush`.

## VSE / Sequencer — `strips` replaces `sequences`

In Blender 5.x, `SequenceEditor.sequences` was renamed to `SequenceEditor.strips`.
Use `editor.strips` (top-level) or `editor.strips_all` (all strips recursively).
