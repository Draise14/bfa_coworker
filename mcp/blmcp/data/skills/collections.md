# Collections & Visibility

## Hierarchy

```
Scene
└── View Layer
    └── Collection (root)
        ├── Child Collection A
        │   ├── Object 1
        │   └── Object 2
        └── Child Collection B
            └── Object 3
```

Objects can belong to multiple collections. Deleting from one collection
does not delete the object if linked elsewhere.

## Creating & Managing Collections

```python
# Create
new_col = bpy.data.collections.new("COL_MyCollection")
bpy.context.scene.collection.children.link(new_col)

# Link object to collection
new_col.objects.link(obj)

# Unlink from collection (not delete)
new_col.objects.unlink(obj)

# Check membership
if obj.name in new_col.objects:
    pass
```

## Three Visibility States

Blender has three independent visibility controls:

1. **Viewport hidden** — `obj.hide_viewport = True` (still fully scriptable)
2. **Disabled in view layer** — `obj.hide_get()` (excluded; operators may skip)
3. **Disabled for render** — `obj.hide_render = True` (skipped during render)

When objects seem "missing" or operators skip them, check all three states.

## Searching the Hierarchy

Walk collections recursively for scene exploration:

```python
def walk_collections(collection, depth=0):
    print("  " * depth + collection.name)
    for obj in collection.objects:
        print("  " * (depth+1) + obj.name)
    for child in collection.children:
        walk_collections(child, depth+1)

walk_collections(bpy.context.scene.collection)
```

## Version Notes (5.2+)

- Collection API unchanged
- View layer visibility unchanged
