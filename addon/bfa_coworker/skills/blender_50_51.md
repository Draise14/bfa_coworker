# Blender 5.0 – 5.1 API Reference

These patterns work in Blender 5.0 and 5.1. They do NOT work in 5.2+.

## Geometry Nodes Modifier Socket Access

In 5.0-5.1, modifier ID properties use a dict-like interface:

```python
# Reading a socket value
value = modifier['["Socket_3"]']

# Setting via .prop()
row.prop(modifier, '["Socket_3"]', text="Label")

# Checking socket existence
if "Socket_3" in modifier:
    # socket exists
```

## Socket Type Values

Node group interface socket types use the `"VALUE"` identifier for float sockets.

## GP Sculpt Brush

`context.tool_settings.gpencil_sculpt.brush` exists and is directly accessible.

## VSE / Sequencer

In 5.0-5.1, use `sequence_editor.sequences` to access strips.
This was renamed to `sequence_editor.strips` in 5.x.
