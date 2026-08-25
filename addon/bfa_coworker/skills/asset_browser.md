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

### `get_asset_tags`
Get detailed tags and metadata for an asset, including node group editor type.

**Parameters:**
- `library_name` (required): Name of the asset library.
- `asset_name` (required): Name of the asset to inspect.
- `asset_type` (optional): Type hint. Auto-detected if omitted.

**Returns:**
- `tags`: List of user-defined tags.
- `editor_type`: For NODETREE assets, returns `GeometryNodeTree`, `ShaderNodeTree`, or `CompositorNodeTree`.
- `color_tag`: Asset color tag (NONE, RED, ORANGE, YELLOW, etc.).
- `description`: Asset description text.
- `metadata`: Additional info (node_count, input_count, output_count, editor_name).

**Node Group Editor Types:**
| Editor Type | Human Name | Use Case |
|-------------|------------|----------|
| `GeometryNodeTree` | Geometry Nodes | Procedural modeling, modifiers |
| `ShaderNodeTree` | Shader Editor | Materials, textures |
| `CompositorNodeTree` | Compositor | Post-processing, effects |

## Usage Examples

```python
# List available libraries
get_asset_libraries()

# Search for wood materials
search_assets(query="wood", asset_type="MATERIAL")

# Check what editor a node group is for
get_asset_tags(
    library_name="My Assets",
    asset_name="MyNodeGroup",
    asset_type="NODETREE"
)
# Returns: editor_type="GeometryNodeTree", editor_name="Geometry Nodes"

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
