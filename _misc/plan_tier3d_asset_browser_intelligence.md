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

1. Wire the 6 existing asset tools into the agent's domain system so they auto-load
2. Enhance existing tools to be smarter (tag search, GN/compositor loading, positioning)
3. Add missing tools (place assets at positions, navigate to asset browser)
4. Update the system prompt to bias the agent toward asset-first workflows
5. Add tests

---

## Phase 1: Wire Existing Tools into the Domain System (~30 LOC, 3 files)

The asset tools exist but the agent can't auto-load them. This is the critical first step — without it, the agent literally cannot see asset tools unless the LLM guesses to call `load_tools("asset_browser")`, which would fail anyway.

### Steps

1. **Add `asset_browser` to `_TOOL_DOMAINS`** in `agent_controller.py` — map to the 6 existing tools:
   - `get_asset_libraries`
   - `list_asset_catalogs`
   - `search_assets`
   - `load_asset_in_context`
   - `get_asset_tags`
   - `assign_material_to_objects`

2. **Add `asset_browser` to `_DOMAIN_KEYWORDS`** in `agent_controller.py` — keywords:
   - `"asset"`, `"library"`, `"catalog"`, `"browse"`, `"preview"`
   - `"preset"`, `"template"`, `"stock"`, `"material library"`

3. **Add `asset_browser` to `_DOMAIN_SKILL_MAP`** in `skills/__init__.py` — map to `["asset_browser.md"]` so the skill doc is auto-injected when the domain activates

### Files Modified

| File | Change |
|------|--------|
| `addon/bfa_coworker/agent_controller.py` | Add `asset_browser` entry to `_TOOL_DOMAINS` dict |
| `addon/bfa_coworker/agent_controller.py` | Add `asset_browser` entry to `_DOMAIN_KEYWORDS` dict |
| `addon/bfa_coworker/skills/__init__.py` | Add `asset_browser` entry to `_DOMAIN_SKILL_MAP` dict |

### Verification

1. Start the agent with asset libraries configured → verify `asset_browser` domain is detected in logs
2. Type "find me a brick material" → verify asset tools appear in the tool set
3. Check that `asset_browser.md` skill content appears in the system prompt when domain is active

---

## Phase 2: Enhance Existing Tools (~300 LOC, 4 files)

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

## Phase 3: New Tools — Position and Navigate (~250 LOC, 4 files)

The issue calls for "know how to add collections or objects to different positions of the world based on what the user asks." These are missing tools.

### Steps

1. **New tool: `place_asset_in_scene`** — place a collection or object asset at a specific world position/rotation. Wraps `load_asset_in_context` but adds explicit transform control.
   - Parameters: `library_name`, `asset_name`, `asset_type`, `location` (x,y,z), `rotation` (x,y,z), `scale` (x,y,z)
   - Supports `link_mode` for collections: `"LINK"` (linked instance) or `"APPEND"` (full copy)

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

## Phase 4: System Prompt & Skill Updates (~100 LOC, 2 files)

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

## Phase 5: Tests (~150 LOC, 2 files)

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
| `tests/integration/test_asset_browser.py` (new) | End-to-end asset workflow test |

### Verification

1. Run `python tests/tool_smoke_test.py --filter asset` → all asset tools pass
2. Run the integration test with a real asset library → full workflow succeeds

---

## Summary of All Changes

| Phase | What | Files Changed | Files New | LOC |
|-------|------|:-------------:|:---------:|:---:|
| 1 | Wire into domain system | 2 | 0 | ~30 |
| 2 | Enhance existing tools | 4 | 0 | ~300 |
| 3 | New tools (place, jump) | 0 | 4 | ~250 |
| 4 | System prompt & skill | 2 | 0 | ~100 |
| 5 | Tests | 1 | 1 | ~150 |
| **Total** | | **9** | **5** | **~830** |

---

## Design Decisions

1. **Reuse existing tools rather than rewrite**: The 6 existing asset tools are solid. We enhance them rather than replacing them. The Tier 6 plan's Phase 6c names differ slightly (`get_asset_catalogs` vs `list_asset_catalogs`, `import_asset_to_scene` vs `load_asset_in_context`) but the functionality maps 1:1.

2. **No background Blender instances for asset storage (yet)**: The issue mentions "background instances of blender" for organizing assets. This is complex (subprocess management, file locking) and is deferred to a follow-up. The `store_as_asset` tool will be built into Bforartists first as a native operator.

3. **AOV activation for compositor assets is best-effort**: The tool will note in its result which AOVs are needed, but automatic AOV setup is complex and deferred.

4. **"Bias to Default Asset Library" is prompt-level, not code-level**: We don't hardcode library names in tools. Instead, the system prompt guides the agent to check the Default/Essentials library first.

---

## Further Considerations

1. **The `search_assets` tool currently walks the filesystem** — it uses `os.walk` + `bpy.data.libraries.load` which is slow for large libraries. A future optimization could cache asset indexes, but this is out of scope for this plan.

2. **The `get_asset_tags` tool loads the asset to inspect it** — this is expensive (appends the datablock). A lighter-weight approach using `bpy.data.libraries.load` read-only inspection could be a follow-up, but the current approach works correctly.

3. **`store_as_asset` deferred** — The ability to mark assets and store them to libraries will be built as a native Bforartists operator first, then wired into the MCP tool system in a follow-up tier.
