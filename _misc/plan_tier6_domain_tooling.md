# BFA Coworker — Tier 6: Domain-Specific MCP Tooling Plan

**Date**: 2026-08-11
**Status**: Skills System Implemented — Tools Not Started
**Depends on**: Existing MCP tool infrastructure (toolcode pattern, auto-discovery, bridge server)

---

## Skills System (Implemented 2026-08-11)

A version-aware skill system was implemented alongside this plan. See `addon/bfa_coworker/skills/` for the loader and core skill files, and `mcp/blmcp/data/skills/` for searchable domain skills.

### Files Created

| File | Purpose |
|---|---|
| `addon/bfa_coworker/skills/__init__.py` | Skills loader — caching, version matching, custom skills injection |
| `addon/bfa_coworker/skills/blender_50_51.md` | 5.0-5.1 API reference (GN ID dict, GP brush) |
| `addon/bfa_coworker/skills/blender_52.md` | 5.2 breaking changes (properties.inputs, socket enums) |
| `addon/bfa_coworker/skills/blender_53.md` | 5.3 specifics (sandbox, label_multiline, online_access) |
| `addon/bfa_coworker/skills/best_practices.md` | Token-saving patterns (capture refs, try/except, mode) |
| `addon/bfa_coworker/skills/naming.md` | Naming conventions (COL_, MAT_, LGT_, CAM_) |
| `addon/bfa_coworker/skills/mcp_tools.md` | MCP tool usage guide (when to use which tool) |
| `mcp/blmcp/data/skills/materials.md` | PBR workflow, Principled BSDF, node patterns |
| `mcp/blmcp/data/skills/modifiers.md` | Modifier stack, GN version differences |
| `mcp/blmcp/data/skills/mesh_editing.md` | BMesh workflow, edit mode, common ops |
| `mcp/blmcp/data/skills/rendering.md` | Cycles/Eevee, render settings, output |
| `mcp/blmcp/data/skills/animation.md` | Keyframes, F-curves, frame range |
| `mcp/blmcp/data/skills/collections.md` | Hierarchy, linking, 3 visibility states |
| `mcp/blmcp/data/skills/transformations.md` | Coordinate spaces, rotation modes, parenting |
| `mcp/blmcp/data/skills/operators.md` | Common operator signatures, mode dependencies |

### Files Modified

| File | Change |
|---|---|
| `addon/bfa_coworker/agent_controller.py` | `_get_system_prompt_with_rules()` now injects version string + built-in skills + user custom skills; `_clear_system_prompt_cache()` also clears skills cache |
| `addon/bfa_coworker/preferences.py` | Added `custom_skills_text` StringProperty; Skills + Custom Skills boxes in Advanced tab |
| `addon/bfa_coworker/operators_agent.py` | Added `BFACW_OT_reload_skills` operator |
| `addon/bfa_coworker/__init__.py` | Registered `BFACW_OT_reload_skills` in class list |
| `mcp/blmcp/data/prompts.yml` | Added `data/skills/` to Bundled Manuals section |

---

## Overview

Tier 6 extends BFA Coworker's MCP tool set from **22 general-purpose tools** (scene inspection, screenshots, navigation, code execution, doc search) to **48 tools** by adding **26 domain-specific tools** across four editor domains. The goal is to make the agent **smarter without distilling models** — instead of the LLM generating correct `bpy` code from scratch for every editor operation, pre-authored, tested toolcode with structured `NamedTuple` inputs/outputs lets the LLM simply pick the right tool and parameters.

**Core insight**: The existing toolcode pattern (`MCP-facing .py` + `Blender-facing *_toolcode.py`) is the answer to "making the MCP smarter." Each tool bundles domain knowledge, error handling, and structured return types. The LLM only needs to understand the tool's description and parameter schema — not the Blender Python API for that domain. This is especially critical for smaller local models that struggle to generate correct `bpy` code.

**Design principle**: Read tools first (situational awareness), then write tools (action), then feedback tools (vision loop). Each domain is self-contained — implementable in any order.

---

## Architecture Strategy: Toolcode Pattern, Not LLM-Generated Code

```
LLM (local/remote)
    │  decides which tool to call
    ▼
MCP Server (FastMCP on port 9191)
    │  routes to registered tool function
    ▼
Tool .py file (e.g., get_vse_strips_summary.py)
    │  constructs Params NamedTuple, calls send_code()
    ▼
Bridge Server (TCP on port 9876)
    │  exec()'s the toolcode in Blender's Python
    ▼
Toolcode *_toolcode.py (e.g., get_vse_strips_summary_toolcode.py)
    │  main(params) → Result NamedTuple
    ▼
Structured dict returned to LLM
```

Each new tool follows the **exact same pattern** as existing tools:
1. `my_tool.py` — registers with `@mcp.tool()`, imports `Params` from toolcode, calls `send_code(toolcode_format_call(...))`
2. `my_tool_toolcode.py` — defines `class Params(NamedTuple)`, `class Result(NamedTuple)`, and `def main(params: Params) -> Result`
3. `ToolAnnotations` — sets `readOnlyHint=True` for inspection tools, `destructiveHint=True` for write tools
4. Auto-discovered via `pkgutil.iter_modules()` — no manual registration needed

---

## Domain Coverage Matrix

| Domain | Read Tools | Navigate Tools | Write Tools | Feedback Tools | Total |
|---|---|---|---|---|---|
| **6a: VSE / Sequencer** | 3 | 0 | 1 | 1 | **5** |
| **6b: Text Editor** | 3 | 0 | 1 | 1 | **5** |
| **6c: Asset Browser** | 5 | 1 | 2 | 1 | **9** |
| **6d: Shader / Node Editor** | 3 | 0 | 3 | 1 | **7** |
| **Total** | **14** | **1** | **7** | **4** | **26** |

---

## Phase 6a: VSE / Sequencer Tools (Est. 500 LOC) ❌ NOT STARTED

*The Sequencer has the most complete bundled API + manual docs (30+ strip types, modifiers, channels, retiming), but zero dedicated tools. These give the LLM the ability to see, navigate, and manipulate strips without generating `bpy.ops.sequencer.*` code from scratch.*

| Step | Description | Files | LOC |
|---|---|---|---|
| 6a.1 | `get_vse_strips_summary` — list all strips: channel, frame range, type, name, selection state, mute, lock | `get_vse_strips_summary.py` + `_toolcode.py` | ~100 |
| 6a.2 | `get_vse_strip_detail` — full detail on one strip: transforms, crop, modifiers, source file path, speed, opacity, blend mode, color balance, proxy settings | `get_vse_strip_detail.py` + `_toolcode.py` | ~120 |
| 6a.3 | `get_vse_timeline_overview` — timeline metadata: resolution, frame range, channels in use, gaps/overlaps, active strip, preview frame | `get_vse_timeline_overview.py` + `_toolcode.py` | ~100 |
| 6a.4 | `set_strip_frame_range` — move/trim a strip by setting `frame_start` and/or `frame_end`; optionally set channel | `set_strip_frame_range.py` + `_toolcode.py` | ~90 |
| 6a.5 | `render_vse_preview` — render a single VSE frame to a temp PNG file; returns file path for LLM vision feedback (uses deferred tool pattern) | `render_vse_preview.py` + `_toolcode.py` | ~90 |

### Files Created (10 new files)

```
mcp/blmcp/tools/
├── get_vse_strips_summary.py
├── get_vse_strips_summary_toolcode.py
├── get_vse_strip_detail.py
├── get_vse_strip_detail_toolcode.py
├── get_vse_timeline_overview.py
├── get_vse_timeline_overview_toolcode.py
├── set_strip_frame_range.py
├── set_strip_frame_range_toolcode.py
├── render_vse_preview.py
└── render_vse_preview_toolcode.py
```

### Agent-Orchestrated VSE Flow

```
User: "What strips are on my timeline? Move the title strip to start at frame 50."

Agent loop:
  1. LLM decides: need get_vse_strips_summary
  2. Calls get_vse_strips_summary()
  3. Tool returns: [{"name": "Title", "type": "TEXT", "channel": 1,
     "frame_start": 1, "frame_end": 120, "selected": True}, ...]
  4. LLM sees "Title" strip, decides to move it
  5. Calls set_strip_frame_range(name="Title", frame_start=50)
  6. Tool returns: {"status": "ok", "name": "Title",
     "frame_start": 50, "frame_end": 120}
  7. Agent: "Done. The Title strip now starts at frame 50."
```

---

## Phase 6b: Text Editor Tools (Est. 450 LOC) ❌ NOT STARTED

*Enables VS Code-style agent interaction: read scripts, make targeted edits, search, run code. The Text Editor has the thinnest manual docs but the API reference covers `bpy.ops.text.*` and `bpy.types.Text` well. These tools are the foundation for the agent being able to write and modify its own scripts.*

| Step | Description | Files | LOC |
|---|---|---|---|
| 6b.1 | `get_text_documents` — list all text datablocks: name, line count, modified flag, syntax highlighting type, active state, file path (if external) | `get_text_documents.py` + `_toolcode.py` | ~80 |
| 6b.2 | `get_text_content` — read content of a text datablock with optional line range (`start_line`, `end_line`); returns lines as list of strings | `get_text_content.py` + `_toolcode.py` | ~90 |
| 6b.3 | `set_text_content` — replace text in a line range, insert at line, or overwrite entire document; supports append mode | `set_text_content.py` + `_toolcode.py` | ~110 |
| 6b.4 | `search_in_text` — search a text datablock for a string or regex; return matching line numbers with surrounding context lines | `search_in_text.py` + `_toolcode.py` | ~80 |
| 6b.5 | `run_text_script` — execute a text datablock as Python in Blender, capturing stdout/stderr and returning any result dict | `run_text_script.py` + `_toolcode.py` | ~90 |

### Files Created (10 new files)

```
mcp/blmcp/tools/
├── get_text_documents.py
├── get_text_documents_toolcode.py
├── get_text_content.py
├── get_text_content_toolcode.py
├── set_text_content.py
├── set_text_content_toolcode.py
├── search_in_text.py
├── search_in_text_toolcode.py
├── run_text_script.py
└── run_text_script_toolcode.py
```

### Agent-Orchestrated Text Editor Flow

```
User: "Read the script 'auto_rig.py' and add error handling around the
       main function."

Agent loop:
  1. Calls get_text_documents()
  2. Tool returns: [{"name": "auto_rig.py", "lines": 245, "modified": False}, ...]
  3. Calls get_text_content(name="auto_rig.py", start_line=1, end_line=50)
  4. Reads the function signature and first ~50 lines
  5. Calls search_in_text(name="auto_rig.py", query="def main")
  6. Finds line 42
  7. Calls set_text_content(name="auto_rig.py",
     start_line=42, end_line=42,
     text="def main():\n    try:\n        ...")
  8. Calls run_text_script(name="auto_rig.py")
  9. Returns: {"status": "ok", "stdout": "", "stderr": ""}
  10. Agent: "Done. Added try/except around main() and verified it runs."
```

---

## Phase 6c: Asset Browser Tools (Est. 800 LOC) ❌ NOT STARTED

*The richest domain. Assets can be materials, node groups, objects, worlds, HDRI environments, etc. Tools need to handle catalog browsing, metadata reading, and type-aware import. The existing Poly Haven tools provide a partial reference pattern for the import logic.*

| Step | Description | Files | LOC |
|---|---|---|---|
| 6c.1 | `get_asset_libraries` — list all asset libraries: current file, user library, custom paths, with total asset counts per library | `get_asset_libraries.py` + `_toolcode.py` | ~80 |
| 6c.2 | `get_asset_catalogs` — catalog tree for a library: catalog paths, UUIDs, parent-child hierarchy | `get_asset_catalogs.py` + `_toolcode.py` | ~90 |
| 6c.3 | `list_assets_in_catalog` — assets in a catalog: name, type, tags, author, description snippet, preview thumbnail path | `list_assets_in_catalog.py` + `_toolcode.py` | ~100 |
| 6c.4 | `search_assets` — search across libraries by name/tag/type; returns matching assets with metadata and library location | `search_assets.py` + `_toolcode.py` | ~100 |
| 6c.5 | `get_asset_detail` — full metadata for one asset: description, author, tags, datablock type, preview image path, library reference | `get_asset_detail.py` + `_toolcode.py` | ~90 |
| 6c.6 | `import_asset_to_scene` — type-aware import: auto-detects asset type and applies correctly (material→active object, GN→modifier, object→scene collection, world→scene world, HDRI→world environment) | `import_asset_to_scene.py` + `_toolcode.py` | ~140 |
| 6c.7 | `link_asset_node_group` — link a node group asset into a specific node tree editor (shader/compositor/geometry nodes); accepts `tree_type` and `node_tree_name` | `link_asset_node_group.py` + `_toolcode.py` | ~80 |
| 6c.8 | `create_asset_from_selection` — mark the current selection (object/material/node group) as an asset with user-provided metadata (description, tags) | `create_asset_from_selection.py` + `_toolcode.py` | ~60 |
| 6c.9 | `jump_to_asset_browser` — switch to the Asset Browser workspace; optionally navigate to a specific catalog path | `jump_to_asset_browser.py` + `_toolcode.py` | ~60 |

### Files Created (18 new files)

```
mcp/blmcp/tools/
├── get_asset_libraries.py
├── get_asset_libraries_toolcode.py
├── get_asset_catalogs.py
├── get_asset_catalogs_toolcode.py
├── list_assets_in_catalog.py
├── list_assets_in_catalog_toolcode.py
├── search_assets.py
├── search_assets_toolcode.py
├── get_asset_detail.py
├── get_asset_detail_toolcode.py
├── import_asset_to_scene.py
├── import_asset_to_scene_toolcode.py
├── link_asset_node_group.py
├── link_asset_node_group_toolcode.py
├── create_asset_from_selection.py
├── create_asset_from_selection_toolcode.py
├── jump_to_asset_browser.py
└── jump_to_asset_browser_toolcode.py
```

### Agent-Orchestrated Asset Browser Flow

```
User: "Find a brick wall material in my asset library and apply it to the
       selected object."

Agent loop:
  1. Calls get_asset_libraries()
  2. Tool returns: [{"name": "User Library", "path": "...", "asset_count": 150}, ...]
  3. Calls search_assets(query="brick wall", type="material")
  4. Tool returns: [{"name": "Brick Wall 02", "library": "User Library",
     "catalog": "Materials/Walls", "tags": ["brick", "wall", "pbr"]}, ...]
  5. Calls get_asset_detail(name="Brick Wall 02", library="User Library")
  6. Tool returns: {"description": "PBR brick wall with mortar detail...",
     "type": "MATERIAL", "tags": [...], "preview": "..."}
  7. Calls import_asset_to_scene(name="Brick Wall 02", library="User Library")
  8. Tool returns: {"status": "ok", "type": "material",
     "applied_to": "MyWall", "material_name": "Brick Wall 02"}
  9. Agent: "Applied 'Brick Wall 02' to the selected object. It's a PBR
     material with bump and roughness maps."
```

---

## Phase 6d: Shader / Node Editor Tools (Est. 650 LOC) ❌ NOT STARTED

*Generic across Shader Editor, Compositor, and Geometry Nodes. All tools accept a `tree_type` parameter (`"ShaderNodeTree"`, `"CompositorNodeTree"`, `"GeometryNodeTree"`) — this avoids 3× duplication. The bundled API docs cover every node type exhaustively (~200+ node RST files).*

| Step | Description | Files | LOC |
|---|---|---|---|
| 6d.1 | `get_active_node_tree` — full node tree structure for the active context: nodes (type, name, location, mute, color), links (from→to), frames (name, size, node membership), group inputs/outputs | `get_active_node_tree.py` + `_toolcode.py` | ~130 |
| 6d.2 | `get_node_detail` — full properties of one node: all input socket values/types/defaults, output sockets, internal settings dict, label, color, mute state | `get_node_detail.py` + `_toolcode.py` | ~110 |
| 6d.3 | `get_node_group_interface` — interface of a node group: input/output sockets with names, types, default values, min/max ranges, descriptions | `get_node_group_interface.py` + `_toolcode.py` | ~90 |
| 6d.4 | `create_node` — add a node by `bl_idname` at a location in a specific node tree; returns the created node's name and socket list | `create_node.py` + `_toolcode.py` | ~100 |
| 6d.5 | `connect_nodes` — link an output socket of one node to an input socket of another; validates socket types before connecting | `connect_nodes.py` + `_toolcode.py` | ~90 |
| 6d.6 | `set_node_input_value` — set the value of a named input socket on a node; handles float, int, color (RGBA), vector (XYZ), boolean, and string types | `set_node_input_value.py` + `_toolcode.py` | ~80 |
| 6d.7 | `mute_node` — toggle the mute state of a node; optionally set to a specific state | `mute_node.py` + `_toolcode.py` | ~50 |

### Files Created (14 new files)

```
mcp/blmcp/tools/
├── get_active_node_tree.py
├── get_active_node_tree_toolcode.py
├── get_node_detail.py
├── get_node_detail_toolcode.py
├── get_node_group_interface.py
├── get_node_group_interface_toolcode.py
├── create_node.py
├── create_node_toolcode.py
├── connect_nodes.py
├── connect_nodes_toolcode.py
├── set_node_input_value.py
├── set_node_input_value_toolcode.py
├── mute_node.py
└── mute_node_toolcode.py
```

### Agent-Orchestrated Shader Editor Flow

```
User: "Add a Noise Texture to the active material and connect its Fac output
       to the Roughness input of the Principled BSDF."

Agent loop:
  1. Calls get_active_node_tree(tree_type="ShaderNodeTree")
  2. Tool returns: {"tree_name": "Material.001", "nodes": [
     {"name": "Principled BSDF", "type": "ShaderNodeBsdfPrincipled",
      "location": [0, 0], "inputs": [{"name": "Roughness", "value": 0.5}, ...]},
     {"name": "Material Output", "type": "ShaderNodeOutputMaterial", ...}], ...}
  3. LLM identifies the Principled BSDF node and its Roughness input
  4. Calls create_node(tree_type="ShaderNodeTree",
     bl_idname="ShaderNodeTexNoise", location=[-300, 0])
  5. Tool returns: {"status": "ok", "node_name": "Noise Texture",
     "inputs": [{"name": "Scale", "type": "FLOAT"}, ...],
     "outputs": [{"name": "Fac", "type": "FLOAT"}, {"name": "Color", "type": "RGBA"}]}
  6. Calls connect_nodes(tree_type="ShaderNodeTree",
     from_node="Noise Texture", from_socket="Fac",
     to_node="Principled BSDF", to_socket="Roughness")
  7. Tool returns: {"status": "ok", "link": "Noise Texture.Fac → Principled BSDF.Roughness"}
  8. Agent: "Added a Noise Texture and connected its Fac to Roughness.
     The material will now have a noisy roughness variation."
```

---

## Phase 6e: System Prompt & Cross-Domain Integration (Est. 200 LOC) ❌ NOT STARTED

*After all tools are built, update the system prompt and screenshot enrichment to make the LLM aware of the new capabilities and provide better context.*

| Step | Description | Files | LOC |
|---|---|---|---|
| 6e.1 | Update `prompts.yml` — add domain-specific guidance chapters for VSE, Text Editor, Asset Browser, and Shader/Node Editor; include tool usage patterns and cross-domain examples | `prompts.yml` | ~100 |
| 6e.2 | Enrich `get_screenshot_of_window_as_json` — add VSE context (strip count, current frame, active strip name) and node editor detail (tree type, node count, active node name) to area info | `get_screenshot_of_window_as_json_toolcode.py` | ~60 |
| 6e.3 | Add cross-domain examples to system prompt — e.g., "To add a geometry node modifier from the asset browser, use search_assets then import_asset_to_scene" | `prompts.yml` | ~40 |

### Files Modified (2 existing files)

```
mcp/blmcp/data/prompts.yml                                    # Domain chapters + cross-domain examples
mcp/blmcp/tools/get_screenshot_of_window_as_json_toolcode.py  # VSE + node editor area enrichment
```

---

## Phase 6f: Competitor UX Features — Advanced Intelligence (Est. 1,100 LOC) ❌ NOT STARTED

*Derived from the Tier 4b competitor analysis. These are the most ambitious features — the ones that separate a "chat assistant" from an "intelligent coworker." They require infrastructure (vision models, multi-agent orchestration, background polling) that's being built across Tiers 5-6.*

### 6f.1 Agent Teams with Planner (Pattern P, BlenderMCP Pro) 🔴

**Source**: BlenderMCP Pro 2.0 — Planner agent → specialist agents (Layout, Modeling, Materials, Lighting, Rigging, Geometry Nodes, Rendering) → Validator agent. Dependency-ordered task list, parallel execution, live task list, single undo checkpoint.

**What**: The user describes a large goal ("Build a campfire scene — ground plane, three logs, stone circle, warm light, dark rocky material"). A planner agent decomposes it into dependency-ordered tasks. Specialist agents execute tasks in parallel where possible. A validator agent checks the result against the goal and auto-fixes issues.

**Why Tier 6**: This is the most architecturally complex feature in the competitive landscape. It requires: multi-agent orchestration, dependency resolution, parallel execution with tool access scoping, validator heuristics, and undo checkpoint management. BlenderMCP Pro's implementation is the only reference — and it's a paid product. Getting this right in a free, open-source tool is a major differentiator.

**Implementation** (~500 LOC):
- `PlannerAgent` — takes a goal string, returns a `TaskPlan` (ordered list of `Task` objects with dependencies)
- `TaskPlan` dataclass: tasks with id, description, domain, dependencies, status, assigned_agent
- `SpecialistAgent` — scoped tool access (e.g., Materials agent only sees material/shader tools)
- `ValidatorAgent` — compares final scene state against goal, returns `ValidationReport` (pass/fail/warn items)
- `AgentOrchestrator` — executes tasks in dependency order, runs independent tasks in parallel threads
- `BFACW_PT_mission_panel` — live task list with status icons, progress, cancel button
- Single undo checkpoint: push before mission starts, one Ctrl+Z rolls back everything
- Auto-fix: validator runs one repair pass before reporting done

**Files**: `agent_teams.py` (new — Planner, Specialist, Validator, Orchestrator), `ui_chat.py` (mission panel), `agent_controller.py` (orchestrator integration)

**Reference**: BlenderMCP Pro's Agent Teams 2.0 documentation at quadify3d.com

---

### 6f.2 Scene Co-Pilot — Passive Issue Detection (Pattern T, BlenderMCP Pro) 🔴

**Source**: BlenderMCP Pro — passive background scanner that flags common issues (unapplied scale, missing UVs, non-manifold geo) with one-click fixes where safe.

**What**: A background scanner that runs periodically (every 5s when idle) and checks the scene for common issues. Issues appear in a non-intrusive status bar in the chat panel. Each issue has a "Fix" button that applies a safe, pre-authored correction. Think: a spell-checker for your 3D scene.

**Why Tier 6**: Requires background polling infrastructure, a library of issue detection heuristics, and safe auto-fix logic for each issue type. The detection heuristics need to be fast (sub-100ms for large scenes) and the fixes need to be non-destructive. This is complex but high-value — it catches problems before they cause downstream failures.

**Implementation** (~300 LOC):
- `SceneScanner` class with registered `IssueDetector` plugins
- Issue types (initial set):
  - `unapplied_scale` — objects with non-uniform scale and modifiers → "Apply Scale" fix
  - `missing_uvs` — mesh objects with materials but no UV maps → "Smart UV Project" fix
  - `non_manifold` — mesh objects with non-manifold edges → "Select non-manifold" (no auto-fix, just highlight)
  - `zero_area_faces` — faces with zero area → "Merge by Distance" fix
  - `missing_material` — mesh objects with no material → "Assign Default Material" fix
  - `ngons_over_N` — faces with >4 vertices → "Triangulate" fix
- `BFACW_PT_scene_health` panel: issue list with severity icons, Fix/Ignore buttons
- Timer-based polling: `bpy.app.timers.register(scanner_tick, first_interval=5.0)`
- Only scan when agent is idle (not during active turns)

**Files**: `scene_co_pilot.py` (new — Scanner, detectors, fixers), `ui_chat.py` (health panel)

**Reference**: BlenderMCP Pro's Scene Co-Pilot documentation

---

### 6f.3 Render Critic with Iterative Refinement (Pattern U, BlenderMCP Pro + BlendAI) 🔴

**Source**: BlenderMCP Pro (structured critique with quality score /10, 5 focus modes, "Fix with AI" button, iterative refinement loop). BlendAI (render suggestions).

**What**: Render the current frame, send it to a vision-capable LLM for critique, get back a structured report with quality score and prioritized fixes. The user can click "Fix with AI" to apply the top fix, or enable iterative mode where the agent renders → critiques → fixes → re-renders until a target score is reached.

**Why Tier 6**: Requires vision model support (already planned for Tier 4b Phase 6), render pipeline integration, structured critique parsing, and an iterative refinement loop. The iterative mode is particularly complex — it needs a termination condition (target score or max iterations) and must avoid infinite loops.

**Implementation** (~300 LOC):
- `BFACW_OT_render_critic` operator: renders current frame, encodes as base64, sends to vision LLM
- Structured critique prompt: returns JSON with `score` (0-10), `issues` array (each with `category`, `severity`, `description`, `suggested_fix`)
- 5 focus modes: Full, Lighting, Composition, Materials, Technical
- `BFACW_OT_critic_fix` — sends the top issue to the agent for fixing
- Iterative mode: `BFACW_OT_critic_iterative` — loop until score ≥ target or max 5 iterations
- Critique history: save past critiques with before/after renders for comparison
- Integration with Tier 5 generative systems: critique generated images/video frames

**Files**: `render_critic.py` (new), `ui_chat.py` (critic panel), `agent_controller.py` (vision message support)

**Reference**: BlenderMCP Pro's Render Critic documentation at quadify3d.com

---

### 6f.4 Voice Input (BlenderMCP Pro) 🟡

**Source**: BlenderMCP Pro — local Whisper integration, no API key needed, no internet after setup.

**What**: Click a microphone icon in the chat input to dictate a message. Audio is transcribed locally using Whisper (no data leaves the machine). The transcribed text populates the chat input field. The user can edit before sending.

**Why Tier 6**: Requires Whisper model download (~1.5 GB for `tiny.en`), audio capture from Blender (non-trivial — may need a small external helper), and real-time transcription. Valuable for accessibility and hands-free workflows, but not critical for core agent functionality.

**Implementation** (~150 LOC):
- `VoiceInputManager` — manages Whisper model download, loading, and inference
- `BFACW_OT_voice_input` — modal operator: click to start recording, click again to stop
- Audio capture: use `pyaudio` or `sounddevice` for microphone access
- Transcription: `faster-whisper` with `tiny.en` model (~1.5 GB, ~2s latency)
- Populate `chat_input` with transcribed text
- Visual feedback: microphone icon pulses during recording

**Files**: `voice_input.py` (new), `ui_chat.py` (microphone button), `llm_manager.py` (Whisper model download)

**Reference**: BlenderMCP Pro's Voice Input documentation

---

### 6f.5 Text-to-Speech Output (Chat Companion) 🟡

**Source**: Chat Companion — reads answers aloud. Unique among current competitors.

**What**: A "Read Aloud" button on each assistant message that speaks the response using a local TTS engine. Useful for accessibility and for users who want to listen while working in the viewport.

**Why Tier 6**: Requires TTS model download, audio playback from Blender, and queue management (don't speak over yourself). Chat Companion is the only addon with this feature — it's a differentiator. But it's quality-of-life, not core functionality.

**Implementation** (~100 LOC):
- `TTSManager` — manages TTS model download and inference
- `BFACW_OT_read_aloud` — operator on each assistant message
- TTS engine: `piper-tts` (lightweight, ~50MB per voice, local)
- Audio playback: `bpy.ops.sound.play()` or `playsound` library
- Queue: if a message is already playing, stop it before starting new one
- Speed control: normal (1.0x) / fast (1.5x) toggle

**Files**: `tts_manager.py` (new), `ui_chat.py` (Read Aloud button)

**Reference**: Chat Companion's TTS feature

---

### 6f.6 External Client Config (BlenderMCP Pro) 🟡

**Source**: BlenderMCP Pro — one-click config writing for Claude Desktop, Cursor, Windsurf, Claude.ai Web (via Cloudflare tunnel).

**What**: A dropdown in the Coworker preferences to select an external MCP client (Claude Desktop, Cursor, Windsurf). Clicking "Write Config" auto-generates the correct JSON config file and writes it to the client's config directory. For Claude.ai Web, start a Cloudflare tunnel and display the public URL.

**Why Tier 6**: We already have an MCP server and external harness mode. One-click config writing removes the friction of manually editing JSON config files. BlenderMCP Pro does this well — it's a polish feature that makes the MCP server actually usable by non-technical users.

**Implementation** (~100 LOC):
- `BFACW_OT_write_mcp_config` operator with client type dropdown
- Auto-detect config paths: `%APPDATA%/Claude/claude_desktop_config.json`, `~/.cursor/mcp.json`, `~/.codeium/windsurf/mcp_config.json`
- Write `mcpServers` entry pointing at `http://127.0.0.1:{port}/mcp`
- Cloudflare tunnel for Claude.ai Web: detect `cloudflared` on PATH, start tunnel, display URL
- "Copy MCP URL" button for manual clients

**Files**: `ui_chat.py` (config operator), `preferences.py` (client dropdown), `mcp_to_blender_server.py` (tunnel support)

**Reference**: BlenderMCP Pro's Connecting Clients documentation at quadify3d.com

---

### 6f.7 Document Loading with Vector Search (Pattern AC, BuddyCode GPT) 🟢

**Source**: BuddyCode GPT (load documents, query with FAISS vector search for context-aware generation)

**What**: Let users load project documents (markdown, text, Python files) and have the agent retrieve the *relevant* chunks when answering — instead of injecting everything into context. This is RAG-style retrieval over project docs.

**Why Tier 6**: We already inject project rules (markdown) wholesale. The gap is *retrieval* — today everything goes in; BuddyCode retrieves only the relevant chunk. For typical Blender scripts this is overkill, but valuable once docs grow large. Requires a vector-store dependency — evaluate a lightweight chunk + scoring approach before adopting FAISS.

**Implementation** (~250 LOC):
- `BFACW_OT_index_documents` — scan a folder for `.md`, `.txt`, `.py` files, chunk into ~500-token segments
- Lightweight retrieval: TF-IDF / keyword scoring over chunks (no FAISS dependency initially)
- `@doc <query>` mention or toggle in the input row to enable retrieval mode
- Retrieved chunks injected into the agent context as "Project Docs (retrieved)" section
- Optional: swap in FAISS later if chunk counts grow large

**Files**: `agent_controller.py` (retrieval), `ui_chat.py` (doc toggle + results display), `preferences.py` (doc folder setting)

---

## Total Estimated: ~3,950 LOC across 58+ new files + modifications to 4 existing files

| Phase | LOC | New Files | Status |
|---|---|---|---|
| 6a: VSE / Sequencer | ~500 | 10 | ❌ Not started |
| 6b: Text Editor | ~450 | 10 | ❌ Not started |
| 6c: Asset Browser | ~800 | 18 | ❌ Not started |
| 6d: Shader / Node Editor | ~650 | 14 | ❌ Not started |
| 6e: System Prompt & Integration | ~200 | 0 | ❌ Not started |
| 6f: Competitor UX — Advanced Intelligence | ~1,350 | 6 | ❌ Not started |

---

## Key Decisions

| Decision | Rationale |
|---|---|
| **Node tools are generic, not shader-specific** | A `tree_type` parameter (`"ShaderNodeTree"`, `"CompositorNodeTree"`, `"GeometryNodeTree"`) avoids 3× duplication. The LLM can determine `tree_type` from context. |
| **Asset import is type-aware** | One `import_asset_to_scene` tool auto-detects material/object/node_group/world and applies correctly. The LLM doesn't need to know the Blender import API for each type. |
| **Text editing uses line-range targeting** | `set_text_content` with `start_line`/`end_line` enables surgical edits instead of full rewrites. Reduces output token usage and avoids accidental data loss. |
| **Read tools return NamedTuples, not repr()** | Structured data increases reliability over `execute_blender_code` where the LLM must parse `repr()` output. All read tools use `strict_json=True`. |
| **Deferred pattern only where needed** | Only `render_vse_preview` uses the deferred tool pattern (background render). All other tools are synchronous — the LLM gets immediate results. |
| **Follow existing toolcode pattern exactly** | Every tool is a `.py` + `*_toolcode.py` pair, auto-discovered via `pkgutil.iter_modules()`. No changes to the MCP server or bridge infrastructure. |
| **Pruned to highest-value tools** | Cut from ~33 to 26 tools. Excluded: `jump_to_vse_strip` (use existing tab switch), `add_strip_modifier` (rare op, use `execute_blender_code`), `create_text_document` (trivial via `execute_blender_code`), `delete_nodes` (risky), `arrange_node_tree` (nice-to-have), `preview_node_output` (nice-to-have). |

---

## Further Considerations

1. **Tool count growth**: 22 existing + 26 new = 48 total tools. Smaller local models (3B-7B parameters) may struggle with that many function definitions. If needed, add **domain filtering** — only register tools whose domain matches the user's current workspace (e.g., VSE tools only appear when in the Video Editing workspace). This is low-effort because each tool is a separate file.

2. **Bforartists VSE compatibility**: The VSE API is largely identical between Blender and Bforartists, but test strip creation, channel assignment, and the 3D Sequencer workspace on Bforartists before committing. The `SequencerTimelineChannel` API may differ.

3. **Asset browser in Bforartists**: Bforartists may have a different asset browser layout or catalog system. Test `bpy.ops.asset.*` and `bpy.types.AssetMetaData` on the target version.

4. **Node tree context resolution**: `get_active_node_tree` needs to determine the "active" node tree from context. In the Shader Editor this is the active material's node tree; in Compositor it's the scene's compositor node tree; in Geometry Nodes it's the active modifier's node group. The toolcode must handle all three cases.

5. **Socket name ambiguity**: Node sockets can have different internal vs. display names (e.g., `"Surface"` vs `"BSDF"`). The `connect_nodes` toolcode should try both the exact name and a fuzzy match, returning available socket names on failure.

6. **Asset import failure modes**: `import_asset_to_scene` must handle: asset not found, asset type mismatch (e.g., trying to import a world as a material), missing dependencies (e.g., a node group that references missing node groups), and permission errors. Each failure should return a clear error message the LLM can act on.

7. **Text editor undo**: `set_text_content` should use `bpy.ops.text.move()` and `bpy.ops.text.replace()` operators where possible, or wrap changes in an undo step, so the user can Ctrl+Z the agent's edits.

8. **Cross-domain orchestration**: The most powerful workflows combine tools across domains. Example: "Find a brick wall material in my asset library, apply it to the selected object, then add a Noise Texture to its roughness" → `search_assets` → `import_asset_to_scene` → `get_active_node_tree` → `create_node` → `connect_nodes`. The system prompt should include these cross-domain patterns.

9. **Testing strategy**: Each tool should be tested in isolation via `tools/call` JSON-RPC, then in end-to-end LLM conversations. A test script in `tests/` can automate the per-tool smoke tests by calling the MCP server directly.

10. **Future expansion**: These 26 tools are the foundation. Future tiers could add: VSE strip modifiers (add/edit/remove), text editor diff view, asset batch operations, node group creation from selection, and node tree diff/merge.

---

## Testing Guide

### Skills System — Verification Steps

Run these tests after implementing the skills system (Track A). All tests are manual — no test framework needed.

#### 1. Preferences UI — Skills Display

| Step | Expected Result |
|---|---|
| Open Blender → Edit → Preferences → Add-ons → Coworker → Advanced tab | "Skills" box visible with "Blender 5.3.0" version label |
| Check "Loaded Skills" list | Shows: `blender_50_51.md`, `blender_52.md`, `blender_53.md`, `best_practices.md`, `naming.md`, `mcp_tools.md` |
| Click "Reload Skills" | Operator runs without error; system prompt cache cleared |

#### 2. Custom Skills Text Field

| Step | Expected Result |
|---|---|
| In Advanced tab → "Custom Skills" box, type: `Always use metric units.` | Text field accepts input |
| Open the Chat panel, send: "What custom skills are loaded?" | LLM responds referencing "Always use metric units" |
| Clear the text field, click Reload Skills, send the same question | LLM no longer references the custom text |

#### 3. Version-Aware API Knowledge

| Step | Expected Result |
|---|---|
| Send: "Create a cube and add a Geometry Nodes modifier with a float input socket set to 2.5" | LLM uses `modifier.properties.inputs.Socket_N.value` syntax (5.2+ path), NOT `modifier['["Socket_N"]']` |
| Send: "What Blender version are you connected to?" | LLM responds "Blender 5.3.0" (or whatever version is running) |

#### 4. Best Practices Injection

| Step | Expected Result |
|---|---|
| Send: "Create a cube, then rotate it 45 degrees on Z" | LLM checks `rotation_mode` before writing rotation |
| Send: "Delete all objects in the scene" | LLM uses `do_unlink=True` |
| Send: "Add a GP sculpt brush" | LLM wraps brush access in `try/except AttributeError` |

#### 5. MCP Tool Guidance

| Step | Expected Result |
|---|---|
| Send: "What tools are available to explore the scene?" | LLM references `get_objects_summary`, `get_blendfile_summary_datablocks`, `get_object_detail_summary` |
| Send: "Show me the viewport" | LLM uses `get_screenshot_of_window_as_image` instead of `execute_blender_code` |

#### 6. Searchable Domain Skills

| Step | Expected Result |
|---|---|
| Send: "Search the bundled docs for materials guidance" | LLM finds and references `data/skills/materials.md` |
| Send: "How do I use bmesh in edit mode?" | LLM references `data/skills/mesh_editing.md` |
| Send: "What's the correct way to set up a PBR material?" | LLM references `data/skills/materials.md` |

#### 7. Reload Flow

| Step | Expected Result |
|---|---|
| Edit `addon/bfa_coworker/skills/best_practices.md` (add a line) | File saved |
| In Blender Advanced tab → click "Reload Skills" | Cache cleared |
| Send a message | New skills content is picked up |

### Tier 6 Tools — Verification Steps

Run these after implementing each tool domain (Track B). Each tool should be tested in isolation first, then in end-to-end LLM conversations.

#### Automated Smoke Test (Recommended)

A standalone smoke test script is provided at `tests/tool_smoke_test.py`. It calls every
MCP tool with minimal arguments and reports pass/fail for each.

**Prerequisites**: Blender running with the Coworker addon enabled and the MCP server
started (default port 9191).

```bash
# Run all tool tests
python tests/tool_smoke_test.py

# Verbose output (show errors)
python tests/tool_smoke_test.py --verbose

# Test specific tools
python tests/tool_smoke_test.py --filter get_objects_summary,execute_blender_code

# List available tools without testing
python tests/tool_smoke_test.py --list

# Custom port
python tests/tool_smoke_test.py --port 9192
```

Exit code 0 = all passed, 1 = any failed. Expected failures (tools that need
specific arguments like a valid blend file path) are tracked separately and
don't count as failures.

#### Per-Tool Manual Smoke Test

For manual verification of a single tool via direct HTTP call:

```python
# Using the MCP server's HTTP endpoint
import requests
resp = requests.post("http://127.0.0.1:9191/", json={
    "jsonrpc": "2.0",
    "id": "test",
    "method": "tools/call",
    "params": {
        "name": "<tool_name>",
        "arguments": {<required_params>}
    }
})
print(resp.json())
```

| Tool | Test Arguments | Expected Result |
|---|---|---|
| `get_vse_strips_summary` | `{}` | List of strips or empty list |
| `get_text_documents` | `{}` | List of text datablocks |
| `get_asset_libraries` | `{}` | List of asset libraries |
| `get_active_node_tree` | `{"tree_type": "ShaderNodeTree"}` | Node tree structure |
| `create_node` | `{"tree_type": "ShaderNodeTree", "bl_idname": "ShaderNodeTexNoise", "location": [0, 0]}` | Created node info |
| `connect_nodes` | `{"tree_type": "ShaderNodeTree", "from_node": "...", "from_socket": "...", "to_node": "...", "to_socket": "..."}` | Link confirmation |

#### End-to-End LLM Conversation Tests

| Test | Prompt | Expected Agent Behavior |
|---|---|---|
| VSE | "What strips are on my timeline?" | Calls `get_vse_strips_summary`, returns formatted list |
| VSE | "Move the title strip to frame 50" | Calls `get_vse_strips_summary` → `set_strip_frame_range` |
| Text | "List all scripts in the Text Editor" | Calls `get_text_documents`, returns formatted list |
| Text | "Read lines 1-20 of script.py" | Calls `get_text_content` with line range |
| Asset | "Find a brick wall material" | Calls `get_asset_libraries` → `search_assets` → `get_asset_detail` |
| Asset | "Import that brick wall material" | Calls `import_asset_to_scene` |
| Node | "Add a Noise Texture to the active material" | Calls `get_active_node_tree` → `create_node` |
| Node | "Connect Noise Fac to Principled Roughness" | Calls `connect_nodes` |
| Cross-domain | "Find a brick wall material, apply it, then add noise to roughness" | Calls `search_assets` → `import_asset_to_scene` → `get_active_node_tree` → `create_node` → `connect_nodes` |

#### Regression Tests

| Test | Expected Result |
|---|---|
| Existing tools still work (screenshots, scene summary, navigation) | No change in behavior |
| `execute_blender_code` still works for fallback cases | No change in behavior |
| System prompt still loads correctly | No errors in console |
| Tool count in UI is correct | 30 existing + N new = total |

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Skills not appearing in system prompt | Cache not cleared | Click "Reload Skills" in Advanced tab |
| LLM uses old API (ID dict instead of properties.inputs) | Version skill not loaded | Check `list_loaded_skills()` includes `blender_52.md` |
| Custom skills text not injected | Text field empty | Type something in the Custom Skills field |
| Tool not found by LLM | Tool not registered | Check `tools/call` directly via HTTP |
| Tool returns error | Toolcode has bug | Check Blender console for traceback |
| LLM keeps calling `execute_blender_code` instead of tool | Tool description unclear | Update tool description in `.py` file| Smoke test can't connect | MCP server not running | Start the agent from the Chat panel or Preferences |
| Smoke test shows "expected failure" | Tool needs specific args | Check `_TOOL_EXPECTED_FAILURES` in `tool_smoke_test.py` |
| Smoke test shows unexpected failure | Tool or server bug | Run with `--verbose` to see the error, check Blender console |