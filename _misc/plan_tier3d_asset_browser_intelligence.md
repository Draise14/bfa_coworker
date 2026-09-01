# BFA Coworker — Tier 3d: Asset Browser Intelligence

**Date**: 2026-08-25
**Status**: Planning — Not Started
**Depends on**: Existing MCP tool infrastructure (toolcode pattern, auto-discovery, bridge server), existing asset browser tools (6 tools already built)

---

## Case

At the moment the asset library could be powerful if the system can see an asset. But right now there is little or no way to test or use the asset library and to be smart enough to use and apply all the assets.

## Solution

Make the agent asset-first: use pre-built or available assets on hand before trying to make anything which may be harder to do in the API. The agent should:

- See registered asset libraries and their assets, categories, tags, descriptions
- Know how to apply each asset type correctly (materials, node groups, collections, objects, worlds, actions)
- Know how to add collections or objects to different positions in the world
- Have a bias to the Default Asset Library assets and essentials from Bforartists
- Use the asset as a source of authoring — procedural assets make the model smarter as the library expands

## Method

1. Wire the 6 existing asset tools into the agent's domain system so they auto-load  [Done]
2. Enhance existing tools to be smarter (tag search, GN/compositor loading, positioning)  [Done]
3. Add missing tools (place assets at positions, navigate to asset browser)  [Done]
4. Make node-group assets usable: inspect node trees and wire node groups contextually (Phase 2B)
5. Update the system prompt to bias the agent toward asset-first workflows
6. Add tests

---

## Phase 1: Wire Existing Tools into the Domain System (~30 LOC, 3 files) - Done (commit `bec647f`)

The asset tools exist but the agent can't auto-load them. This is the critical first step — without it, the agent literally cannot see asset tools unless the LLM guesses to call `load_tools("asset_browser")`, which would fail anyway.

### Steps

1. **Add `assets` to `_TOOL_DOMAINS`** in `agent_controller.py` — map to the 6 existing tools:
   - `get_asset_libraries`
   - `list_asset_catalogs`
   - `search_assets`
   - `load_asset_in_context`
   - `get_asset_tags`
   - `assign_material_to_objects`

2. **Add `assets` to `_DOMAIN_KEYWORDS`** in `agent_controller.py` — keywords:
   - `"asset"`, `"library"`, `"catalog"`, `"browse"`, `"preview"`
   - `"preset"`, `"template"`, `"stock"`, `"material library"`

3. **Add `assets` to `_DOMAIN_SKILL_MAP`** in `skills/__init__.py` — map to `["asset_browser.md"]` so the skill doc is auto-injected when the domain activates

### Files Modified

| File | Change |
|------|--------|
| `addon/bfa_coworker/agent_controller.py` | Add `assets` entry to `_TOOL_DOMAINS` dict |
| `addon/bfa_coworker/agent_controller.py` | Add `assets` entry to `_DOMAIN_KEYWORDS` dict |
| `addon/bfa_coworker/skills/__init__.py` | Add `assets` entry to `_DOMAIN_SKILL_MAP` dict |

### Verification

1. Start the agent with asset libraries configured → verify the `assets` domain is detected in logs
2. Type "find me a brick material" → verify asset tools appear in the tool set
3. Check that `asset_browser.md` skill content appears in the system prompt when domain is active

---

## Phase 2: Enhance Existing Tools (~300 LOC, 4 files) - Done (commit `60f3994`)

The existing tools work but have gaps that prevent the "smart" behavior described in the issue.

### Steps

1. **Enhance `search_assets_toolcode.py`** — search by tags and description in addition to name. Currently only matches `asset_name.lower()`. Add: iterate `asset_data.tags` and `asset_data.description` for each asset. This is the key to "sees an asset, its tags, name, and description — to know then how to use it."

2. **Enhance `load_asset_in_context_toolcode.py`** — add smarter type-aware loading:
   - **NODETREE**: Detect editor type (`GeometryNodeTree` → add as GN modifier on active object; `CompositorNodeTree` → add to compositor node tree, enable AOVs if needed; `ShaderNodeTree` → add to active material's node tree). Currently only handles shader node trees.
   - **COLLECTION**: Support `instance` vs `link` mode parameter. Support `location` parameter for positioning.
   - **OBJECT**: Support `location` parameter for positioning.
   - **MATERIAL**: When object has no material slots, append; when it has slots, offer to replace slot 0 or append new slot.

3. **Enhance `get_asset_tags_toolcode.py`** — add COLLECTION and WORLD type metadata inspection (currently only NODETREE, MATERIAL, OBJECT are fully supported). Add `preview_image_path` to metadata when available.

4. **Enhance `list_asset_catalogs_toolcode.py`** — add `asset_names` list per catalog (first 10 asset names) so the agent can see what's in each folder without a separate search call.

### Files Modified

| File | Change |
|------|--------|
| `mcp/blmcp/tools/search_assets_toolcode.py` | Add tag/description search |
| `mcp/blmcp/tools/load_asset_in_context_toolcode.py` | GN modifier, compositor, positioning, material slot control |
| `mcp/blmcp/tools/get_asset_tags_toolcode.py` | COLLECTION/WORLD metadata |
| `mcp/blmcp/tools/list_asset_catalogs_toolcode.py` | Asset name previews per catalog |

### Verification

1. Search for an asset by tag → verify it finds assets even when the tag isn't in the name
2. Load a Geometry Nodes asset → verify it creates a GN modifier on the active object
3. Load a collection asset with `location=(5,0,0)` → verify it's placed at that world position
4. Load a compositor node group → verify it's added to the compositor tree
5. Get tags for a COLLECTION asset → verify description and tags are returned

---

## Phase 2B: Node-Group Intelligence - Inspection + Wiring (~450 LOC, 7 files) - Done (commits `6988178`+`ce6d343` plan, tools landed in one commit)

> **Status**: Implemented. Tools: `get_active_node_tree`, `get_node_group_interface`,
> `wire_node_group` (insert modes `add_top_level` / `replace_active` /
> `insert_between` / `connect_to_output`, deterministic socket auto-mapping
> with exact → fuzzy → compatible-type order, undo push before mutation).
> Registered in the `assets` + `geometry_nodes` domains, documented in
> `skills/asset_browser.md` (incl. asset-author socket-naming conventions).
> A live-Bender smoke run is still the final gate (`--filter get_node_group_interface,get_active_node_tree,wire_node_group`).

Node-group assets are the hardest asset type to apply. Unlike a material (assign to a slot) or an object (link to the scene), a node group is only useful when it is wired into an existing node tree - often mid-chain, *between* two nodes - and its interface (input/output sockets) must be mapped to the tree at the wire points. Dumping a group at `(0,0)` unconnected (Phase 2 behavior) is a dead end users notice immediately.

### Design constraint: local models choose, tools do the how

Current local models (7-32B, see `llm_manager.py`) hallucinate socket names, node types, and link topology when asked to "wire it intuitively". Tier 3d therefore splits the problem:

- **Inspection tools feed ground truth** - the model reads a real, compact serialization of the tree and the group's interface; it never invents from memory.
- **Wiring tools apply deterministically** - the model picks from enumerated options (closed lists), the tool validates socket types before linking, and pushes an undo step.

### Steps

1. **Pull the Tier 6 inspection tools forward** (small subset of `plan_tier6_domain_tooling.md` 6d): `get_active_node_tree(tree_type, node_tree_name)` - nodes/links/frames summary; `get_node_group_interface(group_name)` - input/output sockets with names, types, defaults, ranges, and the group's editor type.
2. **New `wire_node_group` tool** - load a node-group asset and wire it into a target tree:
   - Insert modes: `replace_active` (wrap the selected node), `insert_between` (splice into a selected link), `connect_to_output` (attach to an empty Material Output surface), `add_top_level` (Phase 2 behavior as fallback).
   - Interface auto-mapping: deterministic fuzzy name match ("Scale" input hooks to the tree's Texture Coordinate / Noise chain when present; BSDF output replaces an unconnected Principled BSDF).
   - Socket-type validation before linking; "no socket named X - did you mean Y?" errors instead of silent failure.
   - `bpy.ops.ed.undo_push()` before mutation so a bad wire is one Ctrl+Z away.
3. **Context fallback** - when the user gives no insertion target, keep the current editor-context behavior (GN modifier / compositor / material top-level).
4. **Asset-author conventions** - document in the skill: name group-interface inputs `Scale`, `Seed`, `Strength`, `Color`; write a one-line usage note in the asset description. Mostly free and the single biggest LLM-success multiplier.

### Files

| File | Purpose |
|------|---------|
| `mcp/blmcp/tools/get_active_node_tree.py` + `_toolcode.py` | Serialize a node tree (nodes, sockets, links, frames) |
| `mcp/blmcp/tools/get_node_group_interface.py` + `_toolcode.py` | Group interface: input/output sockets, types, defaults |
| `mcp/blmcp/tools/wire_node_group.py` + `_toolcode.py` | Load + wire with insert modes, auto-map, undo |
| `mcp/blmcp/tools/load_asset_in_context_toolcode.py` | Keep top-level fallback, expose the loaded group |

### Verification

1. `get_node_group_interface` on a brick-wall group -> inputs `Brick Color` / `Mortar Color` / `Scale` / `Seed`, output `BSDF`.
2. `wire_node_group(insert_between=...)` on a selected link -> group spliced in with valid socket links.
3. `replace_active` on a Principled BSDF -> Principled wrapped by the group, output chain intact.
4. Bad socket name -> tool returns "did you mean" error, no link created, scene unchanged (undo).

---

## Phase 3: New Tools - Position and Navigate (~320 LOC, 7 files) - In progress

The issue calls for "know how to add collections or objects to different positions of the world based on what the user asks." These are missing tools.

### Steps

1. **New tool: `place_asset_in_scene`** - place a collection or object asset at a specific world position/rotation. Self-contained toolcode (mirrors `load_asset_in_context`'s library lookup; toolcode files run standalone in Blender so it cannot call that tool).
   - Supported types: `OBJECT`, `COLLECTION` only (others: route to `load_asset_in_context`).
   - Parameters: `library_name`, `asset_name`, `asset_type`, `location` (x,y,z), `rotation` (x,y,z, degrees), `scale` (x,y,z).
   - Default is `APPEND` (full copy, positioned at the requested transform); `LINK` for collections creates an empty + collection instance at the transform.

2. **New tool: `jump_to_asset_browser`** — switch to the Asset Browser editor. Optionally navigate to a specific library/catalog. Uses `bpy.ops.screen.area_dupli()` or area type switching.

### Files Created

| File | Purpose |
|------|---------|
| `mcp/blmcp/tools/place_asset_in_scene.py` | MCP tool wrapper |
| `mcp/blmcp/tools/place_asset_in_scene_toolcode.py` | Blender-side toolcode |
| `mcp/blmcp/tools/jump_to_asset_browser.py` | MCP tool wrapper |
| `mcp/blmcp/tools/jump_to_asset_browser_toolcode.py` | Blender-side toolcode |

### Verification

1. Call `place_asset_in_scene` with a collection asset at (10, 0, 5) → verify the collection instance is at that position
2. Call `place_asset_in_scene` with `link_mode="LINK"` → verify it's a linked instance (not appended)
3. Call `jump_to_asset_browser` → verify the Asset Browser editor opens

---

## Phase 4: System Prompt & Skill Updates (~100 LOC, 2 files) - Done (commit `a8254c5`)

> **Status**: `prompts.yml` "Asset-First Workflow" section expanded with the
> MCP asset tools decision tree (search → inspect → load), link/append/instance
> guidance, node-group contextual wiring guidance, and object/collection
> placement flow. The skill half (step 2) was already delivered across Phases 2,
> 2B, and 3 (`asset_browser.md` documents all 13 asset tools + node wiring
> workflow + author conventions).

The agent needs to be *biased* toward using assets. The system prompt must make this explicit and give the agent a clear decision tree.

### Steps

1. **Update `prompts.yml`** — expand the "Asset-First Workflow" section with:
   - Explicit decision tree: "Before creating ANY material, node group, world, or object, FIRST search the asset library. Only create from scratch if nothing suitable exists."
   - Bias toward "Default" / "Essentials" asset libraries registered in the Asset Browser settings
   - Guidance on when to link vs append vs instance collections
   - Guidance on node group types (shader vs GN vs compositor) and how to apply each
   - Cross-reference to `asset_browser.md` skill

2. **Update `asset_browser.md` skill** — add the new tools (`place_asset_in_scene`, `jump_to_asset_browser`), add workflow examples for each asset type, add catalog organization best practices.

### Files Modified

| File | Change |
|------|--------|
| `mcp/blmcp/data/prompts.yml` | Expand "Asset-First Workflow" section |
| `addon/bfa_coworker/skills/asset_browser.md` | Add new tools, workflow examples |

### Verification

1. Start a fresh conversation → verify the system prompt includes the expanded asset-first guidance
2. Ask "I need a wood material" → agent should call `search_assets` before attempting `execute_blender_code`
3. Ask "add a brick wall to my scene" → agent should search assets, find a collection, and place it

---

## Phase 5: Tests (~150 LOC, 2 files) - Done (commit `a8254c5`)

### Steps

1. **Add asset tool entries to `tool_smoke_test.py`** — add test args for all asset tools:
   - `get_asset_libraries`, `list_asset_catalogs`, `search_assets`
   - `load_asset_in_context`, `get_asset_tags`, `assign_material_to_objects`
   - `place_asset_in_scene`, `jump_to_asset_browser`

2. **Add integration test** in `tests/integration/` — a test that: configures a temp asset library, searches for an asset, loads it, and verifies it's in the scene. Uses the Bforartists Default asset library if available.

### Files Modified / Created

| File | Change |
|------|--------|
| `tests/tool_smoke_test.py` | Add `_TOOL_TEST_ARGS` entries for all asset tools |
| `tests/test_asset_tools.py` (new) | Unit tests for asset + node-group toolcodes via a `bpy` stub |
| `tests/integration/test_asset_browser.py` (new) | End-to-end asset workflow test (live Blender) |

### Verification

1. Run `python tests/tool_smoke_test.py --filter asset` → all asset tools pass (live Blender)
2. Run the integration test with a real asset library → full workflow succeeds (live Blender)
3. `python -m unittest tests.test_asset_tools` → 43 unit tests pass (no Blender needed)

---

## Summary of All Changes

| Phase | What | Files Changed | Files New | LOC |
|-------|------|:-------------:|:---------:|:---:|
| 1 | Wire into domain system | 2 | 0 | ~30 |
| 2 | Enhance existing tools | 4 | 0 | ~300 |
| 2B | Node-group inspection + wiring | 1 | 6 | ~450 |
| 3 | New tools (place, jump) | 3 | 4 | ~320 |
| 4 | System prompt & skill | 2 | 0 | ~100 |
| 5 | Tests | 1 | 1 | ~150 |
| **Total** | | **11** | **11** | **~1250** |

---

## Design Decisions

1. **Reuse existing tools rather than rewrite**: The 6 existing asset tools are solid. We enhance them rather than replacing them. The Tier 6 plan's Phase 6c names differ slightly (`get_asset_catalogs` vs `list_asset_catalogs`, `import_asset_to_scene` vs `load_asset_in_context`) but the functionality maps 1:1.

2. **No background Blender instances for asset storage (yet)**: The issue mentions "background instances of blender" for organizing assets. This is complex (subprocess management, file locking) and is deferred to a follow-up. The `store_as_asset` tool will be built into Bforartists first as a native operator.

3. **AOV activation for compositor assets is best-effort**: The tool will note in its result which AOVs are needed, but automatic AOV setup is complex and deferred.

4. **"Bias to Default Asset Library" is prompt-level, not code-level**: We don't hardcode library names in tools. Instead, the system prompt guides the agent to check the Default/Essentials library first.

5. **Tool calling convention (Params named-tuple)**: All asset tools marshal parameters as a `Params` named-tuple - the toolcode declares `def main(params: Params)` and the MCP wrapper sends `Params(...)` through the bridge. Phase 3 fixed the six asset wrappers to this convention: the earlier dict / `send_code`-mangled / `None` forms generated invalid `main(...)` calls in Blender (type errors), so every asset-library tool errored instead of running.

6. **Node-group smarts live in the tool, not the model**: local 7-32B models cannot reliably invent socket names or node topology. The inspection tools enumerate real interfaces and options, the agent chooses from them, and `wire_node_group` applies deterministically with validation + undo (see Phase 2B).

---

## Further Considerations

1. **The `search_assets` tool currently walks the filesystem** — it uses `os.walk` + `bpy.data.libraries.load` which is slow for large libraries. A future optimization could cache asset indexes, but this is out of scope for this plan.

2. **The `get_asset_tags` tool loads the asset to inspect it** — this is expensive (appends the datablock). A lighter-weight approach using `bpy.data.libraries.load` read-only inspection could be a follow-up, but the current approach works correctly.

3. **`store_as_asset` deferred** — The ability to mark assets and store them to libraries will be built as a native Bforartists operator first, then wired into the MCP tool system in a follow-up tier.
