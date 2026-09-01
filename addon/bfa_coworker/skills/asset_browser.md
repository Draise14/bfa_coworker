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

### `get_node_group_interface`
Return the interface of a node group loaded in the current blend file (e.g. via
`load_asset_in_context`). This is the **wiring manual** for a node-group asset:

**Parameters:**
- `group_name` (required): Name of the node group in `bpy.data.node_groups`.

**Returns:** Editor type (`GeometryNodeTree` / `ShaderNodeTree` /
`CompositorNodeTree`) plus every input/output socket with its type, default
value, min/max range, and description.

Call this **before** `wire_node_group` — the socket names it returns are what
the auto-mapping keys on.

### `get_active_node_tree`
Serialize a node tree for the LLM. Resolves the target like the editors do:
`"ShaderNodeTree"` → active material, `"GeometryNodeTree"` → active Geometry
Nodes modifier, `"CompositorNodeTree"` → scene compositor tree. An explicit
`node_tree_name` overrides resolution.

**Parameters:**
- `tree_type` (optional): `"ShaderNodeTree"`, `"GeometryNodeTree"`,
  `"CompositorNodeTree"` (empty = auto-detect first available).
- `node_tree_name` (optional): Exact `bpy.data.node_groups` name.

**Returns:** Nodes (name, type, location, mute, socket lists), links
(from-node/socket → to-node/socket), and frames.

Use this to find wire targets (node names, existing links) before wiring.

### `wire_node_group`
Load a node-group asset and splice it **into** a node tree with validated,
undo-able links — the difference from `load_asset_in_context`, which only
drops the group unconnected at top level.

**Parameters:**
- `library_name` (optional): Asset library (empty = group already loaded).
- `asset_name` (required): Node group asset name.
- `tree_type` (optional): Target editor type; empty = the group's own type.
- `node_tree_name` (optional): Explicit target tree; empty = context (active
  material / compositor / GN modifier).
- `insert_mode` (required): One of:
  - `add_top_level` — place unconnected near the active node (fallback).
  - `replace_active` — wrap `target_node` (default: active node): its incoming
    links re-route through the group inputs, its outgoing links through the
    group outputs, then the target node is removed.
  - `insert_between` — splice into the link between `from_node`/`from_socket`
    and `to_node`/`to_socket` (socket names optional).
  - `connect_to_output` — attach to the tree's output: SHADER → Material
    Output *Surface*, IMAGE → Composite *Image*, GEOMETRY → Group Output.
- `target_node` (optional): Node to wrap for `replace_active`.
- `from_node`, `from_socket`, `to_node`, `to_socket` (optional): Link
  endpoints for `insert_between`.
- `link_mode` (optional): `APPEND` (default) or `LINK`.
- `auto_map` (optional): Deterministic socket auto-mapping (default `True`).

**Socket matching order:** exact name → fuzzy name → first unused socket of a
compatible type. Unmappable sockets are returned in `unmapped` rather than
failing silently. An undo step is pushed before any mutation.

## Node-Group Wiring Workflow

```python
# 1. Find and load the node group asset
search_assets(query="brick wall", asset_type="NODETREE", library_name="My Assets")
load_asset_in_context(library_name="My Assets", asset_name="BrickWall", asset_type="NODETREE")

# 2. Read its interface — socket names the mapping keys on
get_node_group_interface(group_name="BrickWall")
# -> inputs: Brick Color, Mortar Color, Scale, Seed; outputs: BSDF

# 3. Inspect the target tree to find wire points
get_active_node_tree(tree_type="ShaderNodeTree")

# 4a. Wrap the active Principled BSDF with the group
wire_node_group(asset_name="BrickWall", insert_mode="replace_active")

# 4b. Or splice into an existing link
wire_node_group(
    asset_name="BrickWall",
    insert_mode="insert_between",
    from_node="Noise Texture", from_socket="Fac",
    to_node="Principled BSDF", to_socket="Roughness",
)

# 4c. Or attach the group straight to the material output
wire_node_group(asset_name="BrickWall", insert_mode="connect_to_output")
```

**Asset-author conventions** (single biggest LLM-success multiplier): name
node-group interface inputs `Scale`, `Seed`, `Strength`, `Color`; write a
one-line usage note in the asset description. Deterministic matching works
best when socket names are short, plain nouns.

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
