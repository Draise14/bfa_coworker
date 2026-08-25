# Asset Browser Tools

These tools provide lightweight access to Blender's asset browser system.

## Tools

### `get_asset_libraries`
Lists all configured asset libraries with their names, paths, and blend file counts.

**Returns:** List of libraries with `name`, `path`, and `blend_file_count`.

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

## Catalog Path Conventions

Asset libraries use directory structures for organization:
- `Materials/` — Material assets
- `NodeGroups/` — Geometry/shader node groups
- `Objects/` — Mesh and object assets
- `Worlds/` — World/environment assets
- `Collections/` — Collection assets

## Usage Examples

```python
# List available libraries
get_asset_libraries()

# Search for wood materials
search_assets(query="wood", asset_type="MATERIAL")

# Load a material onto the active object
load_asset_in_context(
    library_name="My Assets",
    asset_name="Wood_Floor",
    asset_type="MATERIAL"
)

# Load a world as scene environment
load_asset_in_context(
    library_name="My Assets",
    asset_name="Sunset_HDRI",
    asset_type="WORLD"
)
```
