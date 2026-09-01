# Tier3b - Improve Polyhaven PBR Integration (#27)

**Status:** ✅ Implemented
**Date:** August 2026

## Problem Summary

The Polyhaven integration had 5 interconnected bugs that prevented proper PBR material workflow:

1. **Search returned 20 random results** — alphabetical sort, no filtering, no relevance scoring
2. **Texture download fetched only the Albedo map** — no Normal, Roughness, AO, or Displacement
3. **Material creation connected only Base Color** — flat albedo material, not PBR
4. **`setup_pbr_material` had hardcoded broken file paths** — wrong filenames, wrong directory structure
5. **No resolution preference** — no user-facing setting, `arm` packed texture never used

## API Reference

Polyhaven API: `https://api.polyhaven.com`

**Key endpoints:**
- `GET /assets?type={hdris|textures|models}` — full catalog (no functional search/pagination)
- `GET /files/{asset_id}` — file URLs for all maps at all resolutions

**Texture map keys from `/files/{id}`:**
| Key | Blender Usage | Color Space |
|---|---|---|
| `Diffuse` | Base Color | sRGB |
| `nor_gl` | Normal Map (OpenGL) | Non-Color |
| `nor_dx` | Normal Map (DirectX) | Non-Color |
| `Roughness` | Roughness | Non-Color |
| `arm` | Packed AO/Roughness/Metallic | Non-Color |
| `AO` | Ambient Occlusion | Non-Color |
| `Displacement` | Height/Displacement | Non-Color |
| `Bump` | Bump (alt to Normal) | Non-Color |
| `blend` | Pre-made Blender file | N/A |

**Packed ARM texture:** `R = AO`, `G = Roughness`, `B = Metallic`

**Available resolutions:** 1k, 2k, 4k, 8k (varies per asset — `max_resolution` field)

**API quirks:** The `q` search param and `limit` param on `/assets` are non-functional — the API returns the full catalog regardless. Client-side search is required.

## Implementation

### Files Modified/Created

| # | File | Action | Purpose |
|---|---|---|---|
| 1 | `mcp/blmcp/tools_helpers/polyhaven_pbr.py` | **NEW** | Shared PBR helpers: API resolution, download, code generation |
| 2 | `mcp/blmcp/tools/search_polyhaven_assets.py` | Rewrite | Smart client-side search with relevance scoring |
| 3 | `mcp/blmcp/tools/download_polyhaven_asset.py` | Rewrite | Full PBR download + material creation + model .blend import |
| 4 | `mcp/blmcp/tools/setup_pbr_material.py` | Rewrite | Dynamic API-based PBR (removed hardcoded paths) |
| 5 | `mcp/blmcp/tools/get_polyhaven_status.py` | Update | Enhanced status info (PBR maps, resolution options) |
| 6 | `addon/bfa_coworker/preferences.py` | Add | Resolution EnumProperty + UI row |
| 7 | `addon/bfa_coworker/agent_controller.py` | Update | Resolution injection + tool metadata |

### 1. Shared Helper Module (`polyhaven_pbr.py`)

Centralizes all Polyhaven logic used by both `download_polyhaven_asset` and `setup_pbr_material`:

- **`resolve_polyhaven_files(asset_id, asset_type, resolution)`** — resolves download URLs for all available PBR maps
- **`download_texture_set(asset_id, resolution, cache_dir)`** — downloads all maps to cache
- **`build_pbr_material_code(material_name, texture_map_paths)`** — generates complete Blender PBR node tree
- **`build_blend_import_code(blend_path, asset_id)`** — generates code to append from .blend files

### 2. Smart Search (`search_polyhaven_assets.py`)

**New parameters:** `tags` (comma-separated), `sort_by` ("relevance"|"popular")

**Algorithm:**
1. Fetch full catalog (cached in-memory for 5 minutes)
2. Client-side filter by category
3. Score assets: name match (+50-100), tag match (+30), category match (+20), description match (+10), download bonus (log10)
4. Return top 10 with compact info (name, ID, tags, max resolution, downloads, thumbnail)

### 3. Full PBR Download (`download_polyhaven_asset.py`)

**Textures:** Downloads all maps, builds complete Principled BSDF material:
- Diffuse → Base Color (sRGB)
- nor_gl → Normal Map node → Normal (Non-Color)
- Roughness → Roughness (Non-Color), or ARM.G via Separate RGB
- AO → Multiply with Diffuse → Base Color (Non-Color)
- Displacement → Displacement node → Material Output (Non-Color)

**Models:** Tries `.blend` import first (append objects/collections with materials), falls back to glTF/FBX/OBJ with texture dependencies.

**HDRIs:** Unchanged — environment texture → background → world output.

### 4. Resolution Preference

**Addon preferences** (`preferences.py`):
- `polyhaven_resolution` EnumProperty: 512, 1k, 2k (default), 4k, 8k
- UI row in the Poly Haven section of preferences

**Agent controller** (`agent_controller.py`):
- Injects resolution from preferences into `download_polyhaven_asset` and `setup_pbr_material` calls
- LLM never picks resolution — user's preference always wins

## Blender PBR Node Graph

```
[Diffuse] ─sRGB→ [MixRGB:Multiply] → [Principled BSDF:Base Color]
[AO] ─NonColor→ ───────────────────┘

[nor_gl] ─NonColor→ [Normal Map] → [BSDF:Normal]

[Roughness] ─NonColor→ [BSDF:Roughness]
  (or [ARM] → [Separate RGB] → G channel)

[Displacement] ─NonColor→ [Disp Node] → [Output:Displacement]
```

Fallbacks:
- No Roughness → use ARM G channel
- No AO → skip Multiply, connect Diffuse directly
- No Displacement → skip displacement node
- No Normal → skip normal map

## Testing

- Existing smoke tests pass (tool args unchanged, new params have defaults)
- Manual: "Download Test HDRI" button → HDRI applied to world
- Manual: "Download Test Texture" button → full PBR material with all maps
- Manual: Search "brick wall" → relevant results with tags/resolution
- Resolution preference respected from addon settings
- Color spaces correct: sRGB for Diffuse, Non-Color for everything else
