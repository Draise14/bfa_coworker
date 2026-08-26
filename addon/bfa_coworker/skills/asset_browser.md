# Asset Browser Tools

These tools provide access to Blender's asset browser system for browsing,
searching, and loading materials, node groups, objects, worlds, and actions.

## Tools

### `get_asset_libraries`
Lists all configured asset libraries with their names, paths, and blend file counts.

**Returns:** List of libraries with `name`, `path`, and `blend_file_count`.

### `list_asset_catalogs`
Shows the directory structure of asset libraries, with counts of each asset type
per folder. Use this to understand how assets are organized before searching.

**Parameters:**
- `library_name` (optional): Limit to a specific library.

**Returns:** Catalogs with paths, blend file counts, and asset type counts per directory.

### `search_assets`
Search across asset libraries by name, tag, or type.

**Parameters:**
- `query` (required): Search term to match against asset names.
- `library_name` (optional): Limit search to a specific library.
- `asset_type` (optional): Filter by type: `MATERIAL`, `NODETREE`, `OBJECT`, `WORLD`, `ACTION`.

**Returns:** Top 20 matches with name, type, library, and source file.

### `load_asset_in_context`
Load an asset from the asset browser into the current context.

**Parameters:**
- `library_name` (required): Name of the asset library.
- `asset_name` (required): Name of the asset to load.
- `asset_type` (optional): Type hint. Auto-detected if omitted.

**Type-aware loading behavior:**
| Type | Behavior |
|------|----------|
| `MATERIAL` | Assigns to active object (creates cube if none) |
| `NODETREE` | Inserts into active material's node tree |
| `COLLECTION` | Links collection to scene |
| `OBJECT` | Appends object to scene, selects it |
| `WORLD` | Sets as scene world |
| `ACTION` | Assigns to active object's animation data |

### `assign_material_to_objects`
Assign an existing material to one or more objects by name. Use after loading
a material with `load_asset_in_context` or creating one with `setup_pbr_material`.

**Parameters:**
- `material_name` (required): Name of the material datablock in the scene.
- `object_names` (optional): List of object names. Empty = active object.
- `slot_index` (optional): Material slot index (default 0).

### `get_asset_tags`
Get detailed tags and metadata for an asset, including node group editor type.

**Parameters:**
- `library_name` (required): Name of the asset library.
- `asset_name` (required): Name of the asset to inspect.
- `asset_type` (optional): Type hint. Auto-detected if omitted.

**Returns:** Tags, editor type, color tag, description, and metadata.

## Workflow: Assigning Materials from Asset Libraries

```python
# 1. Browse library structure
list_asset_catalogs(library_name="My Assets")

# 2. Search for materials
search_assets(query="wood", asset_type="MATERIAL", library_name="My Assets")

# 3. Load material onto active object
load_asset_in_context(
    library_name="My Assets",
    asset_name="Wood_Floor",
    asset_type="MATERIAL"
)

# 4. Or assign to specific objects
assign_material_to_objects(
    material_name="Wood_Floor",
    object_names=["SM_Floor", "SM_Wall"]
)

# 5. Check node group editor type before using
get_asset_tags(
    library_name="My Assets",
    asset_name="MyNodeGroup",
    asset_type="NODETREE"
)
# Returns: editor_type="GeometryNodeTree" or "ShaderNodeTree"
```

## Catalog Path Conventions

Asset libraries use directory structures for organization:
- `Materials/` — Material assets
- `NodeGroups/` — Geometry/shader node groups
- `Objects/` — Mesh and object assets
- `Worlds/` — World/environment assets
- `Collections/` — Collection assets

Use `list_asset_catalogs` to see the actual structure of each library.
