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

### `place_asset_in_scene`
Place a `COLLECTION` or `OBJECT` asset at an explicit world transform. Use this
when the user wants an asset at a specific position/rotation/scale ("add the
brick wall at x=10, facing the camera"). For materials, node groups, worlds,
or actions use `load_asset_in_context` instead.

**Parameters:**
- `library_name` (required): Name of the asset library.
- `asset_name` (required): Name of the object or collection asset.
- `asset_type` (optional): `OBJECT` or `COLLECTION`. Auto-detected if omitted.
- `link_mode` (optional): `APPEND` (default, full copy, positioned directly) or `LINK` (linked; collections become an empty + collection instance).
- `location` (optional): [x, y, z] world position.
- `rotation` (optional): [x, y, z] Euler rotation in **degrees**.
- `scale` (optional): [x, y, z] scale.

Appended collections are positioned so their **centroid** lands at `location`, with rotation/scale applied around that centroid.

### `jump_to_asset_browser`
Switch to (or create) the Asset Browser editor. Reuses an open Asset Browser;
otherwise creates a new "Asset Browser" workspace (duplicating the current one,
so the user's layout is preserved). Optionally preselects a library and catalog.

**Parameters:**
- `library_name` (optional): Asset library to select (best-effort).
- `catalog_path` (optional): Catalog path or UUID to select (best-effort).
- `allow_edits` (optional): Allow creating a workspace/area (default `True`).

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

## Workflow: Placing Assets at a Position

```python
# 1. Find the collection asset
search_assets(query="brick wall", asset_type="COLLECTION", library_name="My Assets")

# 2. Place it (appended copy, centroid at the target)
place_asset_in_scene(
    library_name="My Assets",
    asset_name="Brick_Wall",
    asset_type="COLLECTION",
    location=[10, 0, 5],
    rotation=[0, 0, 90],  # degrees
    scale=[2, 2, 2],
)

# 3. Keep a large props library linked (instance, editable source)
place_asset_in_scene(
    library_name="Props Library",
    asset_name="Street_Lamp",
    asset_type="COLLECTION",
    link_mode="LINK",
    location=[-4, 2, 0],
)
```

## Catalog Path Conventions

Asset libraries use directory structures for organization:
- `Materials/` — Material assets
- `NodeGroups/` — Geometry/shader node groups
- `Objects/` — Mesh and object assets
- `Worlds/` — World/environment assets
- `Collections/` — Collection assets

Use `list_asset_catalogs` to see the actual structure of each library.
