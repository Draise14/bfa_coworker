# BFA Coworker — Tier 3g: MCP Intent Architecture — From Code Writer to Intent Driver

**Date**: 2026-08-29
**Status**: ✅ Implemented
**Depends on**: Tier 3f work (preflight, auto-fix, templates, tool domains)
**Reference**: Blender Buddy v9.13.1 architecture patterns, Blender 5.2 LTS Manual

---

## Table of Contents

1. [Tier 3f Audit — What We Built](#1-tier-3f-audit--what-we-built)
2. [The Core Problem](#2-the-core-problem)
3. [The Three Paradigms of 3D Work](#3-the-three-paradigms-of-3d-work)
4. [Architecture: The Orchestrator Pattern](#4-architecture-the-orchestrator-pattern)
5. [Editor-Aware Tool Scoping](#5-editor-aware-tool-scoping)
6. [Per-Editor Template Registry](#6-per-editor-template-registry)
7. [The Orchestrator Engine](#7-the-orchestrator-engine)
8. [Data Block Type System](#8-data-block-type-system)
9. [Editor Mode Awareness](#9-editor-mode-awareness)
10. [Operator-Based UI Design](#10-operator-based-ui-design)
11. [Creative Workflows](#11-creative-workflows)
12. [Text Editor as IDE Agent](#12-text-editor-as-ide-agent)
13. [Documentation Coverage Strategy](#13-documentation-coverage-strategy)
14. [Scalability & Plugin Architecture](#14-scalability--plugin-architecture)
15. [Template Complexity Tiers](#15-template-complexity-tiers)
16. [Multi-Editor Pathway Planning](#16-multi-editor-pathway-planning)
17. [The Self-Expanding System: Recorder & Macros](#17-the-self-expanding-system-recorder--macros)
18. [Asset-First Templates: Procedural Assets as Building Blocks](#18-asset-first-templates-procedural-assets-as-building-blocks)
19. [Benchmarks & Development Roadmap](#19-benchmarks--development-roadmap)
20. [Implementation Plan](#20-implementation-plan)
21. [Technical Feasibility Review](#21-technical-feasibility-review)
22. [Success Criteria](#22-success-criteria)

---

## 1. Tier 3f Audit — What We Built

### 1.1 Commit Summary (30 commits on branch)

| Category | Commits | What |
|----------|---------|------|
| LLM Server | 8 | CUDA/Vulkan auto-detect, DLL extraction, GGUF parser, llama-server install/uninstall UX |
| Preflight System | 10 | 27 regex patterns catching common LLM mistakes before exec |
| Agent Hardening | 5 | Spiral detection (threshold 3→2), corrective messages, doc tool access, domain system |
| MCP Safety | 3 | Remove crash-prone timer, auto-correction module, template system |
| Chat UI | 2 | Multiline text wrapping, mode switch lock during agent run |
| Tests | 2 | GGUF parser tests, preflight validation tests (63 passing) |

### 1.2 What Works

| Feature | Status | Impact |
|---------|--------|--------|
| CUDA/Vulkan auto-detection | ✅ Working | Eliminates manual GPU config |
| DLL companion extraction | ✅ Working | Fixes DLL_NOT_FOUND crashes |
| GGUF layer count parser | ✅ Working | Enables GPU layer auto-sizing |
| Preflight checks (27 patterns) | ✅ Working | Catches errors before exec |
| Spiral detection (threshold 2) | ✅ Working | Breaks error loops faster |
| Doc tools (3 always loaded) | ✅ Working | Agent can look up APIs |
| Template system (17 templates) | ✅ Working (module) | Tested Blender 5.3 code blocks |
| Auto-correction module | ✅ Working (module) | Silently fixes common mistakes |
| Execute_blender_plan MCP tool | ✅ Working | Two-phase: plan → tested code |
| Chat text wrapping | ✅ Working | Readable message width |
| Mode lock during agent run | ✅ Working | Prevents premature stops |

### 1.3 What's Not Wired Yet

| Feature | Status | Blocker |
|---------|--------|---------|
| Auto-fix → _execute_code | 🔌 Module exists, not imported | Shell quoting prevented inline wiring |
| Template tests | 🔌 Tests written, not in test file | Same shell quoting issue |
| Editor-aware scoping | ❌ Not started | Needs MCP server context |
| Resource injection | ❌ Not started | Needs MCP server context |
| Server-side orchestration | ❌ Not started | Needs architecture design |

### 1.4 Tier 3f Verdict

**The original task (Issue #29) is complete.** The branch improves llama-server downloading UX with auto-detection, DLL extraction, and GGUF parsing. The hardening work (preflight, spiral detection, templates) was scope creep that happened to be valuable.

**Ready to merge?** Yes — the core deliverable (llama/HF download UX) is solid. The extra hardening is a bonus. The unwired modules (auto-fix, templates) can be integrated in Tier 3g.

---

## 2. The Core Problem

### 2.1 Why Local Models Struggle

| Problem | Big Model (Claude/GPT-4) | Local Model (Qwen 27B) |
|---------|--------------------------|------------------------|
| Tool selection (30+ tools) | Reads all, picks correctly | Overwhelmed, picks wrong |
| API knowledge | Knows Blender 5.3 APIs | Guesses wrong, loops |
| Error recovery | Reads hint, fixes code | Reads hint, tries different wrong thing |
| Context window | 200K tokens | 8-32K tokens |
| Multi-step planning | Plans 5 steps ahead | Plans 1 step, loses track |

### 2.2 What We've Tried So Far

| Approach | Result | Why |
|----------|--------|-----|
| More system prompt text | Diminishing returns | Model ignores text it can't process |
| More tools | Decision paralysis | More choices = worse decisions |
| Preflight rejection | Model retries same mistake | Error message too verbose |
| Spiral detection | Catches loops late | Already wasted 6+ round-trips |
| Doc tools | Model doesn't call them | Has to decide to call them |

### 2.3 The Fundamental Insight

**The model doesn't need to be smart if the server is smart enough.**

Instead of asking "write correct Blender Python code", ask "describe what you want in English" and let the server translate.

### 2.4 The Deeper Insight: Three Kinds of "Smart"

A local LLM can be smart in three different ways, and we should use each where it excels:

| Kind of Smart | What It Means | Where It Excels | Where It Fails |
|---------------|---------------|-----------------|----------------|
| **Semantic Smart** | Understanding natural language intent | "Make it look cinematic", "Organize this mess" | Writing correct API calls |
| **Structural Smart** | Understanding hierarchies, relationships | Outliner organization, node tree layout | Remembering exact parameter names |
| **Creative Smart** | Aesthetic judgment, composition | Camera framing, lighting mood, color grading | Multi-step procedural execution |

**The orchestrator's job is to let the model be semantically and creatively smart while the server handles structural and procedural correctness.**

---

## 3. The Three Paradigms of 3D Work

Every operation an artist performs in Blender falls into one of three paradigms. The Coworker system must understand and operate within all three.

### 3.1 Paradigm 1: Linear Destructive Forward Building

```
User adds vertices → User extrudes → User bevels → User subdivides
                                                    ↓
                                              Only undo can go back
```

**Characteristics:**
- Sequential, irreversible operations
- Each step builds on the previous
- Undo is the only safety net
- Examples: Edit Mode modeling, sculpting, texture painting, weight painting

**Coworker Strategy:**
- Push undo states before each destructive operation (`bpy.ops.ed.undo_push()`)
- Batch multiple operations into a single undo step when they form a logical unit
- Validate selection state before each operation
- Report what changed so the user can undo intelligently

### 3.2 Paradigm 2: Procedural Non-Destructive Building

```
User adds modifier → User adds another → User tunes parameters
        ↓                                     ↓
   Modifier stack                        Node tree
   (always editable)                     (always editable)
```

**Characteristics:**
- Operations are stacked, not baked
- Any parameter can be tuned at any time
- Reproducible and shareable
- Examples: Modifiers, Geometry Nodes, Shader Nodes, Compositor, Animation Nodes

**Coworker Strategy:**
- Add/configure modifiers and nodes rather than baking geometry
- Prefer GN modifier chains over destructive mesh operations
- Organize node trees with frames, reroutes, and color coding
- Name everything meaningfully so the artist can find it later

### 3.3 Paradigm 3: Serial Contextual Operations (The Coworker's Domain)

```
User expresses intent → Server reads context → Server plans operations → Server executes
                             ↓                      ↓                      ↓
                      Editor, selection,       Template chain,         Undo push,
                      data blocks, assets,     parameter fill,         execute each,
                      scene state              validation              report results
```

**Characteristics:**
- The user doesn't specify *how*, only *what*
- The server understands context the user may not even see
- Operations are chained intelligently based on current state
- The server knows about assets, techniques, and APIs the user doesn't
- Examples: "Organize the outliner", "Make it look cinematic", "Clean up this mesh", "Apply materials by object name"

**This is the ultimate goal of the local model + MCP system.**

The Coworker bridges all three paradigms:
- It can do **destructive** work safely (with undo management)
- It can set up **procedural** systems (modifiers, nodes)
- It specializes in **contextual serial operations** that combine both

---

## 4. Architecture: The Orchestrator Pattern

### 4.1 Current Flow (Model Writes Code)

```
User: "Make a stonehenge"
  → Model sees 30+ tools
  → Model picks execute_blender_code
  → Model writes 50 lines of Python
  → Code hits AttributeError (wrong API)
  → Preflight catches it, returns error
  → Model tries again with different wrong API
  → Spiral detection fires after 2 failures
  → Corrective message injected
  → Model finally gets it right (maybe)

  Total: 4-8 round-trips, 2000+ tokens wasted
```

### 4.2 Target Flow (Model Describes Intent, Server Executes)

```
User: "Make a stonehenge"
  → Server detects: editor=VIEW_3D, mode=OBJECT, 0 objects selected
  → Server injects: scene context (empty scene, no materials)
  → Server loads: 6 relevant tools (not 30)
  → Model sees: create_torus, arrange_in_circle, add_material, ...
  → Model picks: arrange_in_circle(count=8) + create_torus × 8
  → Server translates to tested template code
  → Code runs first time

  Total: 1-2 round-trips, 200 tokens used
```

### 4.3 The Four-Layer Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Layer 4: Operator UI Layer                                   │
│  - Blender native operators in menus, sidebars, context menus │
│  - One-click contextual actions (no typing needed)            │
│  - Chat panel for freeform exploration                        │
│  - Operators gather context → send intent → execute plan      │
├──────────────────────────────────────────────────────────────┤
│  Layer 3: Orchestrator Engine                                 │
│  - Intent → Plan → Template Chain → Code → Execute            │
│  - Manages undo pushes before destructive ops                 │
│  - Handles apply/rename/reorder automatically                 │
│  - Two-phase: Planner (heavy) → Runner (light)                │
│  - Falls back: template → auto-fix → raw code → error         │
├──────────────────────────────────────────────────────────────┤
│  Layer 2: Template + Auto-fix + Preflight                     │
│  - 111+ tested code blocks (expandable via plugins)           │
│  - 12 auto-correction rules (expandable)                      │
│  - 27 preflight validation patterns                           │
│  - Data-block type registry (25+ types)                       │
│  - Editor mode registry (15+ modes)                           │
├──────────────────────────────────────────────────────────────┤
│  Layer 1: Context Gathering                                   │
│  - Editor-aware tool scoping (5-8 tools per editor)           │
│  - Resource injection (scene state, selection, assets)        │
│  - Data-block awareness (what types exist, their relationships)│
│  - Mode awareness (OBJECT, EDIT, SCULPT, POSE, etc.)          │
│  - Asset library awareness (what's available, tags, catalogs) │
└──────────────────────────────────────────────────────────────┘
```

### 4.4 The Two-Agent Question: Planner + Runner?

**Should we use two separate model calls — a "planner" and a "runner"?**

| Approach | Pros | Cons |
|----------|------|------|
| **Single model, two-phase** | Simpler, one connection | Planner context bleeds into runner |
| **Two models (planner heavy, runner light)** | Specialized prompts, cheaper runner | Complex orchestration, two model loads |
| **Single model, single call** | Fastest | Highest failure rate |

**Recommendation: Single model, two-phase with context reset.**

The same model is called twice:
1. **Planning Phase** — Model sees full context + available templates. Returns a JSON plan (list of template names + params). No code generation.
2. **Execution Phase** — Server takes the plan, renders templates, applies auto-fix, runs preflight, executes. Model is not involved.

If execution fails, the error is fed back to the planning phase for a single retry. If that fails, fall back to raw `execute_blender_code`.

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────┐
│ User Intent  │ ──→ │ Planner (Model) │ ──→ │ JSON Plan        │
│ + Context    │     │ "Pick templates │     │ [{template,      │
│ + Templates  │     │  and params"    │     │   params}, ...]  │
└──────────────┘     └─────────────────┘     └───────┬──────────┘
                                                     │
                                                     ▼
┌──────────────┐     ┌─────────────────┐     ┌──────────────────┐
│ Result to    │ ←── │ Runner (Server) │ ←── │ Template Chain   │
│ User         │     │ Render → Fix →  │     │ + Auto-fix       │
│              │     │ Validate → Exec │     │ + Preflight      │
└──────────────┘     └─────────────────┘     └──────────────────┘
```

**Why this works for local models:**
- The planning phase is a **classification problem** (pick the right templates), not a code generation problem
- The model only needs to understand intent → template mapping, which can be learned from examples
- The execution phase is **deterministic** — no model involvement means no hallucinations
- A 7B-14B model can do intent → template classification reliably
- The context window is used for template descriptions, not for holding API knowledge

### 4.5 The Fallback Ladder

When the orchestrator can't handle an intent, it falls back gracefully:

```
1. Intent matches known template chain? → Execute plan
   ↓ NO
2. Intent matches single template? → Fill params, execute
   ↓ NO
3. Intent is code-level? → Pass to execute_blender_code with auto-fix + preflight
   ↓ NO
4. Intent is informational? → Search docs, return results
   ↓ NO
5. Nothing matches? → Model responds with clarifying question
```

---

## 5. Editor-Aware Tool Scoping

### 5.1 The Principle

**A model choosing from 5 tools is 6× more accurate than choosing from 30.**

The MCP server detects the active editor and filters the tool list before sending it to the model. The model never sees irrelevant tools.

### 5.2 Detection Mechanism

The bridge server (`mcp_to_blender_server.py`) already runs inside Blender and has access to `bpy.context`. We add a lightweight context probe that runs before each tool list is sent:

```python
# Context probe — runs in Blender, returns to MCP server
def _probe_context():
    import bpy
    ctx = bpy.context
    return {
        "active_editor": ctx.area.type if ctx.area else "UNKNOWN",
        "active_space": ctx.space_data.type if ctx.space_data else "UNKNOWN",
        "mode": ctx.mode if ctx.object else "OBJECT",
        "object_type": ctx.object.type if ctx.object else None,
        "selected_count": len(ctx.selected_objects),
        "scene_objects": len(ctx.scene.objects),
        "active_node_editor_type": (
            ctx.space_data.tree_type
            if ctx.space_data and hasattr(ctx.space_data, "tree_type")
            else None
        ),
    }
```

### 5.3 Editor → Tool Mapping (Full Matrix)

| Editor (`area.type`) | Space/Mode | Tools Shown (5-8) | Always Available |
|-----------------------|------------|-------------------|------------------|
| `VIEW_3D` | OBJECT mode | create_primitives, add_modifier, add_material, arrange_objects, setup_lighting, organize_outliner | get_screenshot, get_objects_summary, search_docs, execute_blender_code |
| `VIEW_3D` | EDIT_MESH | extrude_region, bevel_edges, loop_cut, subdivide, recalculate_normals, merge_vertices, remove_doubles | (same always-available) |
| `VIEW_3D` | SCULPT | sculpt_brush_ops, remesh, dyntopo_toggle, mask_ops, face_set_ops | (same always-available) |
| `VIEW_3D` | POSE | keyframe_pose, add_constraint, ik_setup, copy_pose, mirror_pose | (same always-available) |
| `VIEW_3D` | WEIGHT_PAINT | assign_weights, smooth_weights, mirror_weights, gradient_weights | (same always-available) |
| `VIEW_3D` | TEXTURE_PAINT | setup_brush, paint_layer, stencil_project, clone_brush | (same always-available) |
| `VIEW_3D` | OBJECT mode + camera selected | frame_camera, camera_rig, depth_of_field, turntable_setup | (same always-available) |
| `NODE_EDITOR` | ShaderNodeTree | principled_setup, glass_material, emission_material, image_texture, noise_texture, color_ramp, frame_nodes, mix_materials | get_screenshot, search_docs, execute_blender_code |
| `NODE_EDITOR` | GeometryNodeTree | scatter_on_faces, instance_on_points, boolean_operation, array_geometry, set_material, transform_geometry, capture_attribute | (same always-available) |
| `NODE_EDITOR` | CompositorNodeTree | glare_node, color_balance, lens_distortion, blur_node, mix_rgb, viewer_node, render_layers, composite_output | (same always-available) |
| `NODE_EDITOR` | TextureNodeTree | noise_texture, voronoi_texture, brick_texture, gradient_texture, color_ramp, mapping_node | (same always-available) |
| `DOPESHEET_EDITOR` | — | keyframe_location, keyframe_rotation, keyframe_scale, set_interpolation, add_fcurve_modifier, bake_animation, nla_track | get_screenshot, search_docs, execute_blender_code |
| `GRAPH_EDITOR` | — | set_interpolation, add_fcurve_modifier, smooth_keys, ease_in_out, cycle_modifier, noise_modifier | (same always-available) |
| `NLA_EDITOR` | — | nla_track, blend_transition, time_remap, action_stash, bake_action | (same always-available) |
| `SEQUENCE_EDITOR` | — | add_movie_strip, add_sound_strip, add_image_strip, add_effect_strip, split_strip, trim_strip, add_transition, set_strip_speed | get_screenshot, search_docs, execute_blender_code |
| `IMAGE_EDITOR` | — | unwrap_uv, pack_islands, stitch_uvs, paint_texture, save_image, resize_image | (same always-available) |
| `UV_EDITOR` | — | unwrap_uv, pack_islands, stitch_uvs, pin_uvs, align_uvs, straighten_uvs | (same always-available) |
| `CLIP_EDITOR` | — | track_marker, solve_camera, set_floor, setup_tracking_scene, orient_scene | (same always-available) |
| `TEXT_EDITOR` | — | blender_addon_skeleton, operator_template, panel_template, modal_operator, property_group, bmesh_operation, node_tree_setup, import_export_script | get_screenshot, search_docs, execute_blender_code |
| `OUTLINER` | — | rename_by_type, sort_by_name, sort_by_type, sort_by_material, sort_by_location, add_color_tag, group_selected, delete_unused_data | get_screenshot, get_objects_summary, search_docs, execute_blender_code |
| `PROPERTIES` | — | set_render_engine, setup_world, setup_output, manage_collections, add_modifier (contextual), add_constraint (contextual) | (same always-available) |
| `FILE_BROWSER` | — | import_model, export_selected, batch_import, setup_asset_library, pack_resources | (same always-available) |
| `PREFERENCES` | — | install_addon, configure_keymap, setup_theme, import_config | (same always-available) |
| `UNKNOWN` / fallback | — | get_screenshot, get_objects_summary, search_docs, execute_blender_code, list_templates | (all always-available) |

### 5.4 Tool Description Format

Each tool description sent to the model includes:
- **What it does** (one line)
- **When to use it** (context hints)
- **Parameters** (with defaults)
- **What it returns**

This keeps descriptions under 100 tokens each, so 8 tools = ~800 tokens of tool context.

---

## 6. Per-Editor Template Registry

### 6.1 Template Structure

Each template is a Python function that:
1. Takes a `params` dict with defaults
2. Returns a string of tested, working Blender Python code
3. Is registered in a global `_TEMPLATES` dict
4. Has a docstring describing its purpose and parameters

```python
def _tmpl_create_torus(params=None):
    """Template: create_torus — Add a torus primitive.
    
    Params: name, major_radius, minor_radius, major_segments, minor_segments, x, y, z
    Editor: VIEW_3D, Mode: OBJECT
    Data blocks created: MESH, OBJECT
    """
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'bpy.ops.mesh.primitive_torus_add('
        'major_radius={major_radius}, minor_radius={minor_radius}, '
        'major_segments={major_segments}, minor_segments={minor_segments})\n'
        'obj = bpy.context.active_object\n'
        'obj.name = "{name}"\n'
        'obj.location = ({x}, {y}, {z})\n'
    ).format(**p)
```

### 6.2 Template Metadata

Each template carries metadata used by the orchestrator:

| Metadata Field | Purpose | Example |
|---------------|---------|---------|
| `name` | Unique identifier | `"create_torus"` |
| `editor` | Which editor this belongs to | `"VIEW_3D"` |
| `mode` | Which mode required | `"OBJECT"` |
| `creates_datablocks` | What data blocks are created | `["MESH", "OBJECT"]` |
| `modifies_datablocks` | What data blocks are modified | `[]` |
| `requires_selection` | Whether objects must be selected | `False` |
| `is_destructive` | Whether undo should be pushed | `True` |
| `category` | Grouping for UI | `"primitives"` |
| `chainable` | Can be part of a multi-step chain | `True` |
| `blender_version_min` | Minimum Blender version | `(5, 0, 0)` |

### 6.3 3D Viewport — Object Mode (25 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `create_cube` | Add cube primitive | name, size, location | MESH, OBJECT |
| `create_uv_sphere` | Add UV sphere | name, segments, ring_count | MESH, OBJECT |
| `create_icosphere` | Add icosphere | name, subdivisions | MESH, OBJECT |
| `create_cylinder` | Add cylinder | name, vertices, radius, depth | MESH, OBJECT |
| `create_cone` | Add cone | name, vertices, radius1, radius2, depth | MESH, OBJECT |
| `create_torus` | Add torus | name, major_radius, minor_radius | MESH, OBJECT |
| `create_plane` | Add plane | name, size, location | MESH, OBJECT |
| `create_circle` | Add circle | name, vertices, radius | MESH, OBJECT |
| `create_monkey` | Add Suzanne | name, size, location | MESH, OBJECT |
| `create_empty` | Add empty | name, type, display_size | OBJECT |
| `create_camera` | Add camera | name, location, rotation, lens | CAMERA, OBJECT |
| `create_light` | Add light | name, type, energy, color | LIGHT, OBJECT |
| `create_text` | Add text object | name, text, font, extrude | FONT, OBJECT |
| `create_metaball` | Add metaball | name, type, radius | META, OBJECT |
| `create_lattice` | Add lattice | name, u, v, w | LATTICE, OBJECT |
| `create_armature_single` | Add single bone | name, location | ARMATURE, OBJECT |
| `duplicate_objects` | Duplicate selection | count, offset_x, offset_y, offset_z | OBJECT |
| `join_objects` | Join selected into one | (uses selection) | MESH |
| `set_origin` | Set object origin | origin_type (CENTER, BOTTOM, CURSOR) | OBJECT |
| `apply_transform` | Apply location/rotation/scale | location, rotation, scale | OBJECT |
| `parent_objects` | Parent selection to active | keep_transform | OBJECT |
| `add_collection` | New collection | name, color_tag | COLLECTION |
| `move_to_collection` | Move selected to collection | collection_name | COLLECTION, OBJECT |
| `randomize_transform` | Random offset/rotation | loc_range, rot_range, scale_range | OBJECT |
| `select_by_type` | Select all objects of type | object_type | OBJECT |

### 6.4 3D Viewport — Edit Mode (20 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `extrude_region` | Extrude selected faces/edges | offset_x, offset_y, offset_z | MESH |
| `extrude_individual` | Extrude each face separately | offset | MESH |
| `inset_faces` | Inset selected faces | thickness, depth | MESH |
| `bevel_edges` | Bevel selected edges | width, segments | MESH |
| `loop_cut` | Add loop cut | number_cuts, edge_index | MESH |
| `merge_vertices` | Merge vertices by distance | merge_distance | MESH |
| `remove_doubles` | Remove duplicate vertices | threshold | MESH |
| `recalculate_normals` | Recalculate normals | inside (bool) | MESH |
| `fill_holes` | Fill holes with faces | sides | MESH |
| `grid_fill` | Grid fill between edge loops | span, offset | MESH |
| `triangulate_mesh` | Triangulate faces | quad_method, ngon_method | MESH |
| `subdivide_mesh` | Subdivide selected | number_cuts, smoothness | MESH |
| `smooth_vertices` | Smooth vertex positions | factor, iterations | MESH |
| `bridge_edge_loops` | Bridge between edge loops | twist, number_cuts | MESH |
| `spin_tool` | Spin extrude around cursor | steps, angle, axis | MESH |
| `knife_project` | Knife project from view | cut_through | MESH |
| `separate_selection` | Separate selection to new object | by_selection, by_material, by_loose | MESH, OBJECT |
| `flip_normals` | Flip face normals | (uses selection) | MESH |
| `shade_flat` | Set flat shading | (uses selection) | MESH |
| `shade_smooth` | Set smooth shading + auto smooth | angle_degrees | MESH |

### 6.5 3D Viewport — Sculpt Mode (8 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `remesh_voxel` | Voxel remesh | voxel_size | MESH |
| `remesh_quadriflow` | Quadriflow retopology | target_faces | MESH |
| `dyntopo_toggle` | Toggle dynamic topology | detail_size | MESH |
| `mask_from_cavity` | Mask by cavity | factor, blur | MESH |
| `mask_expand` | Expand mask by topology | steps | MESH |
| `face_set_from_visible` | Face sets from visible | (uses view) | MESH |
| `sculpt_symmetry` | Set sculpt symmetry | axis, mirror | MESH |
| `apply_base` | Apply sculpt to base mesh | (uses sculpt data) | MESH |

### 6.6 Shader Editor (18 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `principled_basic` | Basic PBR material | base_color, roughness, metallic | MATERIAL, NODETREE |
| `principled_full` | Full PBR with clearcoat/sheen | base_color, roughness, metallic, clearcoat, sheen | MATERIAL, NODETREE |
| `glass_material` | Glass/transmission | color, ior, roughness | MATERIAL, NODETREE |
| `emission_material` | Emissive shader | color, strength | MATERIAL, NODETREE |
| `metallic_material` | Metal with edge tint | color, roughness, edge_tint | MATERIAL, NODETREE |
| `sss_material` | Subsurface scattering | color, sss_radius, sss_color | MATERIAL, NODETREE |
| `toon_material` | Toon/cel shader | color, size, smooth | MATERIAL, NODETREE |
| `noise_texture` | Procedural noise | scale, detail, roughness, distortion | NODETREE |
| `voronoi_texture` | Procedural voronoi | scale, randomness, feature | NODETREE |
| `brick_texture` | Procedural brick | mortar_size, bias, brick_width, row_height | NODETREE |
| `gradient_texture` | Gradient ramp | type (LINEAR, RADIAL, SPHERICAL) | NODETREE |
| `image_texture` | Image texture node | image_path, colorspace, projection | NODETREE |
| `color_ramp` | Color ramp with stops | colors, interpolation | NODETREE |
| `mapping_node` | Mapping transform | location, rotation, scale | NODETREE |
| `texture_coordinate` | Texture coordinate | mapping_type (UV, Object, Generated) | NODETREE |
| `frame_nodes` | Frame selected nodes | label, color | NODETREE |
| `mix_materials` | Mix two materials | factor, blend_type | MATERIAL, NODETREE |
| `organize_node_tree` | Layout nodes, add frames, color-code | layout_style (LEFT_TO_RIGHT, TOP_TO_BOTTOM) | NODETREE |

### 6.7 Geometry Nodes (14 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `gn_scatter_on_faces` | Distribute points on faces | density, seed | NODETREE, MODIFIER |
| `gn_instance_on_points` | Instance objects on points | instance_collection, scale, rotation | NODETREE, MODIFIER |
| `gn_boolean_operation` | Boolean geometry operation | operation (UNION, DIFFERENCE, INTERSECT) | NODETREE, MODIFIER |
| `gn_array_geometry` | Array via GN | count, offset_x, offset_y, offset_z | NODETREE, MODIFIER |
| `gn_curve_to_mesh` | Profile along curve | profile_curve, fill_caps | NODETREE, MODIFIER |
| `gn_set_material` | Assign material by selection | material_name | NODETREE, MODIFIER |
| `gn_transform_geometry` | Transform geometry | translation, rotation, scale | NODETREE, MODIFIER |
| `gn_delete_geometry` | Delete by selection/domain | domain, selection | NODETREE, MODIFIER |
| `gn_capture_attribute` | Capture named attribute | data_type, domain, name | NODETREE, MODIFIER |
| `gn_extrude_mesh` | Extrude mesh faces | offset_scale, individual | NODETREE, MODIFIER |
| `gn_subdivide_mesh` | Subdivide mesh | level | NODETREE, MODIFIER |
| `gn_mesh_to_volume` | Convert mesh to volume | voxel_size, density | NODETREE, MODIFIER |
| `gn_volume_to_mesh` | Convert volume to mesh | threshold, adaptivity | NODETREE, MODIFIER |
| `gn_store_named_attribute` | Store named attribute | name, data_type, domain, value | NODETREE, MODIFIER |

### 6.8 Compositor (14 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `comp_glare_node` | Glare/bloom effect | glare_type, quality, threshold, size | NODETREE |
| `comp_color_balance` | Lift/gamma/gain | lift, gamma, gain | NODETREE |
| `comp_lens_distortion` | Lens distortion | distortion, dispersion | NODETREE |
| `comp_blur_node` | Gaussian blur | size_x, size_y, relative | NODETREE |
| `comp_filter_sharpen` | Sharpen filter | factor | NODETREE |
| `comp_mix_rgb` | Mix two inputs | blend_type, factor | NODETREE |
| `comp_color_correction` | Full color correction | saturation, contrast, gamma, gain, lift | NODETREE |
| `comp_viewer_node` | Connect to viewer | (connects active to viewer) | NODETREE |
| `comp_render_layers` | Input render layers | scene, layer, pass | NODETREE |
| `comp_composite_output` | Final composite output | (connects to composite) | NODETREE |
| `comp_vignette` | Ellipse mask vignette | width, height, softness | NODETREE |
| `comp_chromatic_aberration` | RGB shift | red_offset, blue_offset | NODETREE |
| `comp_denoise` | Denoise render | prefilter | NODETREE |
| `comp_setup_cinematic` | Full cinematic look | (chains: color_balance + glare + vignette + lens_distortion) | NODETREE |

### 6.9 Video Sequencer (12 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `vse_add_movie_strip` | Add movie clip | filepath, channel, frame_start | MOVIE |
| `vse_add_sound_strip` | Add sound file | filepath, channel, frame_start | SOUND |
| `vse_add_image_strip` | Add image/sequence | filepath, channel, frame_start, frame_end | IMAGE |
| `vse_add_color_strip` | Add solid color | color, channel, frame_start, frame_end | — |
| `vse_add_text_strip` | Add text overlay | text, channel, frame_start, frame_end | TEXT |
| `vse_add_effect_strip` | Add effect between strips | effect_type (GAUSSIAN_BLUR, GLOW, TRANSFORM, etc.) | EFFECT |
| `vse_split_strip` | Split strip at frame | frame, channel | — |
| `vse_trim_strip` | Trim strip start/end | frame_start, frame_end | — |
| `vse_add_transition` | Add cross/wipe transition | transition_type, duration | EFFECT |
| `vse_set_strip_speed` | Speed control | speed_factor | — |
| `vse_add_fade` | Fade in/out | fade_duration, fade_type | — |
| `vse_setup_proxy` | Setup proxy for selected | proxy_size, quality | — |

### 6.10 Animation (16 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `anim_keyframe_location` | Keyframe position | x, y, z, frame | ACTION, FCURVE |
| `anim_keyframe_rotation` | Keyframe rotation | rx_deg, ry_deg, rz_deg, frame | ACTION, FCURVE |
| `anim_keyframe_scale` | Keyframe scale | sx, sy, sz, frame | ACTION, FCURVE |
| `anim_keyframe_visibility` | Keyframe visibility | visible, frame | ACTION, FCURVE |
| `anim_keyframe_material` | Keyframe material property | input_name, value, frame | ACTION, FCURVE |
| `anim_set_interpolation` | Set keyframe interpolation | interpolation_type (LINEAR, BEZIER, CONSTANT, BOUNCE, ELASTIC) | FCURVE |
| `anim_set_easing` | Set easing type | easing_type (EASE_IN, EASE_OUT, EASE_IN_OUT) | FCURVE |
| `anim_add_fcurve_modifier` | Add FCurve modifier | modifier_type (NOISE, ENVELOPE, CYCLES, LIMITS, STEPPED) | FCURVE |
| `anim_add_driver` | Add driver to property | target_property, expression | DRIVER, FCURVE |
| `anim_bake_action` | Bake animation to keyframes | frame_start, frame_end, step | ACTION |
| `anim_nla_track` | Add action as NLA track | action_name, frame_start, blend_type | NLA_TRACK |
| `anim_nla_transition` | Blend between NLA tracks | track1, track2, duration | NLA_TRACK |
| `anim_cycle_animation` | Loop animation via cycles modifier | cycles_before, cycles_after | FCURVE |
| `anim_bake_sound_to_fcurve` | Audio amplitude to FCurve | sound_file, freq_min, freq_max | FCURVE |
| `anim_retime_keyframes` | Scale keyframe timing | scale_factor, frame_start, frame_end | FCURVE |
| `anim_setup_walk_cycle` | Basic walk cycle on armature | armature_name, frame_start, stride_length | ACTION, FCURVE |

### 6.11 Outliner / Scene Management (12 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `outliner_rename_by_type` | Rename objects by type | prefix, use_numbering | OBJECT |
| `outliner_sort_by_name` | Alphabetical sort in outliner | reverse | OBJECT |
| `outliner_sort_by_type` | Group objects by type | (mesh, light, camera, etc.) | OBJECT |
| `outliner_sort_by_material` | Group by material assignment | — | OBJECT |
| `outliner_sort_by_location` | Group by world position | axis, threshold | OBJECT |
| `outliner_add_color_tag` | Color tag collections | color, collection_name | COLLECTION |
| `outliner_group_selected` | Group into new collection | collection_name | COLLECTION, OBJECT |
| `outliner_delete_unused_data` | Purge orphan data blocks | data_type (ALL, MATERIAL, TEXTURE, ACTION, etc.) | ALL |
| `outliner_organize_scene` | Full scene organization | (chains: sort + rename + color_tag + purge) | ALL |
| `outliner_select_hierarchy` | Select entire hierarchy | (uses active object) | OBJECT |
| `outliner_instance_collection` | Instance collection at cursor | collection_name | OBJECT |
| `outliner_link_collection` | Link collection from another file | filepath, collection_name | COLLECTION |

### 6.12 Text Editor — Code Writing (14 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `text_addon_skeleton` | Full addon template | name, bl_idname, category, author | TEXT |
| `text_operator_template` | Simple operator | class_name, bl_idname, bl_label | TEXT |
| `text_panel_template` | UI Panel class | class_name, bl_space_type, bl_region_type | TEXT |
| `text_modal_operator` | Modal operator with event loop | class_name, bl_idname | TEXT |
| `text_property_group` | PropertyGroup definition | class_name, properties | TEXT |
| `text_menu_template` | Menu class | class_name, bl_idname | TEXT |
| `text_bmesh_operation` | BMesh edit script | description | TEXT |
| `text_node_tree_setup` | Programmatic node tree | tree_type (SHADER, GEOMETRY, COMPOSITOR) | TEXT |
| `text_import_export_script` | IO script template | format (OBJ, FBX, GLTF, custom) | TEXT |
| `text_keymap_addon` | Keymap registration | keymap_items | TEXT |
| `text_handler_template` | App handler (save, load, render, etc.) | handler_type | TEXT |
| `text_gizmo_template` | Custom gizmo | class_name | TEXT |
| `text_render_script` | Batch render script | output_dir, file_format | TEXT |
| `text_register_current` | Register current text as addon | (uses active text) | TEXT |

### 6.13 Physics (8 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `physics_add_cloth` | Cloth simulation | quality, mass, air_damping | MODIFIER |
| `physics_add_soft_body` | Soft body simulation | mass, speed, bending | MODIFIER |
| `physics_add_rigid_body` | Rigid body (active/passive) | mass, friction, bounciness | OBJECT |
| `physics_add_collision` | Collision modifier | (uses selection) | MODIFIER |
| `physics_add_particle_system` | Particle emitter | count, lifetime, velocity | PARTICLES |
| `physics_add_fluid_domain` | Fluid simulation domain | resolution, cache_dir | MODIFIER |
| `physics_add_force_field` | Force field (wind, vortex, etc.) | force_type, strength, flow | OBJECT |
| `physics_bake_all` | Bake all physics | (bakes all simulations) | ALL |

### 6.14 Rendering (10 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `render_set_engine` | Set render engine | engine, device (CPU/GPU) | SCENE |
| `render_set_resolution` | Set output resolution | x, y, percentage | SCENE |
| `render_set_output` | Set output path + format | filepath, file_format, color_mode | SCENE |
| `render_setup_world` | Setup world shader | color, strength, use_sky | WORLD, NODETREE |
| `render_setup_hdri` | HDRI environment | hdri_path, rotation, strength | WORLD, NODETREE |
| `render_setup_eevee` | EEVEE settings | samples, shadows, ao, bloom, ssr | SCENE |
| `render_setup_cycles` | Cycles settings | samples, denoiser, light_paths | SCENE |
| `render_setup_turntable` | Turntable animation setup | frames, output_dir | SCENE, ACTION |
| `render_batch_render` | Batch render multiple scenes | scene_names, output_dir | SCENE |
| `render_view_layer_setup` | View layer management | layer_name, collections, passes | VIEW_LAYER |

### 6.15 Lighting (8 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `light_three_point` | Three-point lighting rig | key_intensity, fill_intensity, rim_intensity | LIGHT, OBJECT |
| `light_studio_soft` | Soft studio lighting | size, intensity, color_temp | LIGHT, OBJECT |
| `light_hdri_studio` | HDRI + area light combo | hdri_path, fill_intensity | WORLD, LIGHT, OBJECT |
| `light_rim_only` | Dramatic rim lighting | intensity, color, angle | LIGHT, OBJECT |
| `light_product` | Product photography setup | top_intensity, front_intensity | LIGHT, OBJECT |
| `light_outdoor_sun` | Sun + sky outdoor | sun_elevation, sun_rotation, turbidity | LIGHT, WORLD |
| `light_night_scene` | Moonlit night | moon_intensity, ambient_color | LIGHT, WORLD |
| `light_match_reference` | Match lighting from reference | reference_description | LIGHT, WORLD |

### 6.16 Camera (6 templates)

| Template | What It Does | Key Params | Data Blocks |
|----------|-------------|------------|-------------|
| `camera_frame_selected` | Frame camera on selection | margin, aspect_ratio | CAMERA |
| `camera_frame_from_angle` | Camera from direction | azimuth, elevation, distance, lens | CAMERA |
| `camera_dolly_zoom` | Dolly zoom effect | target_distance, lens_start, lens_end | CAMERA |
| `camera_add_dof` | Depth of field setup | focus_object, f_stop, blades | CAMERA |
| `camera_track_to` | Track-to constraint | target_object | CAMERA |
| `camera_setup_cinematic` | Cinematic camera rig | (chains: frame + dof + track) | CAMERA |

---

## 7. The Orchestrator Engine

### 7.1 Core Pipeline

```
┌──────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATOR PIPELINE                         │
│                                                                  │
│  User Intent ──→ Context Probe ──→ Intent Classifier ──→ Planner │
│  (text/button)   (editor, mode,    (template? code?    (model    │
│                   selection,        doc? clarify?)      picks     │
│                   data blocks)                           plan)    │
│                                                                  │
│  Planner ──→ Plan Validator ──→ Template Renderer ──→ Auto-fix   │
│  (JSON     (check params,     (render each        (apply 12      │
│   plan)     template exists,  template to         correction     │
│             mode matches)     Python code)        rules)         │
│                                                                  │
│  Auto-fix ──→ Preflight ──→ Undo Push ──→ Execute ──→ Report    │
│  (silent    (27 regex   (if            (run in    (what changed, │
│   fixes)    patterns)    destructive)  Blender)   success/fail)  │
└──────────────────────────────────────────────────────────────────┘
```

### 7.2 Intent Classifier

The intent classifier is a lightweight first pass that runs before the model is called. It uses keyword matching and context to route the intent:

```python
INTENT_PATTERNS = {
    "create_primitive": [
        r"\b(add|create|make|spawn|place)\b.*\b(cube|sphere|cylinder|cone|torus|plane|monkey|suzanne)\b",
        r"\b(new|fresh)\b.*\b(scene|project)\b",
    ],
    "modify_mesh": [
        r"\b(extrude|bevel|inset|loop.cut|subdivide|merge|bridge|fill)\b",
        r"\b(smooth|flatten|straighten|bend|twist|taper)\b",
    ],
    "add_modifier": [
        r"\b(add|apply|put)\b.*\b(modifier|subsurf|array|bevel|solidify|remesh|mirror|screw|skin|wireframe)\b",
    ],
    "add_material": [
        r"\b(add|assign|apply|give|set.up)\b.*\b(material|shader|texture|pbr|glass|metal|emission)\b",
        r"\b(make|render)\b.*\b(look like|appear|seem)\b",
    ],
    "lighting": [
        r"\b(light|lighting|illuminate|three.point|studio|rim|key|fill|hdri|sun|sky)\b",
        r"\b(make|set)\b.*\b(mood|atmosphere|dramatic|cinematic|bright|dark)\b",
    ],
    "camera": [
        r"\b(camera|frame|view|angle|shot|perspective|dolly|zoom|track)\b",
        r"\b(look at|see|view from|point at)\b",
    ],
    "organize": [
        r"\b(organize|organise|sort|rename|clean|tidy|purge|group|arrange)\b.*\b(outliner|scene|collection|object)\b",
        r"\b(clean up|tidy up|fix)\b.*\b(scene|file|project)\b",
    ],
    "animate": [
        r"\b(animate|keyframe|move|rotate|spin|bounce|cycle|loop|walk)\b",
        r"\b(make|set)\b.*\b(move|spin|rotate|fly|float)\b",
    ],
    "render": [
        r"\b(render|export|output|save.image|screenshot)\b",
        r"\b(set.up)\b.*\b(render|output|format)\b",
    ],
    "code": [
        r"\b(write|code|script|program|addon|add-on|operator|panel|register)\b.*\b(python|blender|bpy)\b",
        r"\b(generate|create)\b.*\b(script|addon|add-on)\b",
    ],
    "search": [
        r"\b(what|how|where|find|search|look.up|explain|tell|show)\b.*\b(blender|bpy|api|manual|doc)\b",
    ],
}
```

### 7.3 Plan Format

The planner model outputs a JSON plan:

```json
{
  "intent": "create_stonehenge",
  "reasoning": "User wants a stonehenge. I'll create 8 torus pillars arranged in a circle, then add a flat ground plane.",
  "steps": [
    {
      "template": "create_plane",
      "params": {"name": "Ground", "size": 30, "x": 0, "y": 0, "z": -0.5}
    },
    {
      "template": "create_torus",
      "params": {"name": "Pillar_1", "major_radius": 0.4, "minor_radius": 0.15, "x": 0, "y": 5, "z": 2}
    },
    {
      "template": "add_array",
      "params": {"count": 8, "offset_x": 0, "offset_y": 0}
    },
    {
      "template": "add_material",
      "params": {"mat_name": "Stone", "r": 0.6, "g": 0.55, "b": 0.45, "roughness": 0.9}
    }
  ],
  "fallback": "If templates don't cover this, use execute_blender_code with a script that creates torus objects in a circle."
}
```

### 7.4 Plan Validator

Before execution, the plan is validated:

1. **Template existence** — Every `template` name must exist in `_TEMPLATES`
2. **Mode compatibility** — Template's required mode must match current mode (or mode switch is auto-inserted)
3. **Parameter validation** — Required params must be present, types must match
4. **Selection check** — If template requires selection, verify objects are selected
5. **Data-block conflict** — If template creates a data-block with a name that already exists, auto-rename

### 7.5 Template Chain Composer

Some intents map to pre-defined chains of templates. These are curated, tested sequences:

```python
TEMPLATE_CHAINS = {
    "stonehenge": [
        ("create_plane", {"name": "Ground", "size": 30, "z": -0.5}),
        ("create_torus", {"name": "Pillar", "major_radius": 0.4, "minor_radius": 0.15, "y": 5, "z": 2}),
        ("add_array", {"count": 8}),
        ("add_bevel", {"width": 0.02, "segments_bevel": 3}),
        ("add_material", {"mat_name": "Stone", "r": 0.6, "g": 0.55, "b": 0.45, "roughness": 0.9}),
    ],
    "cinematic_look": [
        ("render_set_engine", {"engine": "CYCLES"}),
        ("render_set_resolution", {"x": 1920, "y": 1080}),
        ("camera_setup_cinematic", {}),
        ("light_three_point", {"key_intensity": 1000, "fill_intensity": 300, "rim_intensity": 800}),
        ("comp_setup_cinematic", {}),
    ],
    "clean_scene": [
        ("outliner_organize_scene", {}),
        ("outliner_delete_unused_data", {"data_type": "ALL"}),
    ],
    "product_shot": [
        ("light_product", {}),
        ("camera_frame_selected", {"margin": 0.2}),
        ("render_set_engine", {"engine": "CYCLES"}),
        ("render_set_resolution", {"x": 1920, "y": 1920}),
    ],
    "prepare_for_3d_print": [
        ("apply_transform", {"location": True, "rotation": True, "scale": True}),
        ("recalculate_normals", {"inside": False}),
        ("remove_doubles", {"threshold": 0.0001}),
        ("add_solidify", {"thickness": 0.002}),
    ],
}
```

### 7.6 Execution Engine

```python
def execute_plan(plan: list[dict]) -> dict:
    """Execute a validated plan, returning results for each step."""
    results = []
    for i, step in enumerate(plan):
        template_name = step["template"]
        params = step.get("params", {})
        
        # 1. Push undo if destructive
        tmpl_meta = get_template_meta(template_name)
        if tmpl_meta.get("is_destructive"):
            push_undo()
        
        # 2. Switch mode if needed
        required_mode = tmpl_meta.get("mode")
        if required_mode and required_mode != get_current_mode():
            switch_mode(required_mode)
        
        # 3. Render template to code
        code = render_template(template_name, params)
        
        # 4. Auto-fix
        code, fixes = autofix_code(code)
        
        # 5. Preflight
        errors = preflight_check(code)
        if errors:
            results.append({"step": i, "template": template_name, "status": "preflight_failed", "errors": errors})
            continue
        
        # 6. Execute
        try:
            output = execute_blender_code(code)
            results.append({"step": i, "template": template_name, "status": "ok", "fixes": fixes, "output": output})
        except Exception as e:
            results.append({"step": i, "template": template_name, "status": "error", "error": str(e), "fixes": fixes})
    
    return {"status": "ok" if all(r["status"] == "ok" for r in results) else "partial",
            "steps": results}
```

---

## 8. Data Block Type System

### 8.1 Why Data Blocks Matter

Blender's data model is built on **data blocks** — named, reference-counted units of data. Every template creates, modifies, or reads specific data block types. The orchestrator must understand this to:

- Avoid name collisions
- Manage references (don't delete a material that's in use)
- Purge orphan data correctly
- Understand what "organize the scene" actually means

### 8.2 Data Block Type Registry

| Type | `bpy.data` accessor | Created By | Modified By | Deleted By |
|------|---------------------|------------|-------------|------------|
| `ACTION` | `bpy.data.actions` | Keyframe operations, baking | Keyframe editing | `outliner_delete_unused_data` |
| `ARMATURE` | `bpy.data.armatures` | `create_armature_single` | Pose mode ops, rigging | `outliner_delete_unused_data` |
| `BRUSH` | `bpy.data.brushes` | Sculpt/paint setup | Brush parameter changes | `outliner_delete_unused_data` |
| `CAMERA` | `bpy.data.cameras` | `create_camera` | Camera settings, DOF | `outliner_delete_unused_data` |
| `COLLECTION` | `bpy.data.collections` | `add_collection`, `group_selected` | Collection operations | `outliner_delete_unused_data` |
| `CURVE` | `bpy.data.curves` | Curve primitives | Curve editing | `outliner_delete_unused_data` |
| `FONT` | `bpy.data.fonts` | `create_text` | Text editing | `outliner_delete_unused_data` |
| `GREASE_PENCIL` | `bpy.data.grease_pencils` | GP drawing | GP editing | `outliner_delete_unused_data` |
| `IMAGE` | `bpy.data.images` | Render, texture load | Image editing | `outliner_delete_unused_data` |
| `LATTICE` | `bpy.data.lattices` | `create_lattice` | Lattice editing | `outliner_delete_unused_data` |
| `LIGHT` | `bpy.data.lights` | `create_light` | Light settings | `outliner_delete_unused_data` |
| `MATERIAL` | `bpy.data.materials` | `add_material`, shader templates | Material editing | `outliner_delete_unused_data` |
| `MESH` | `bpy.data.meshes` | Primitive creation, edit mode | Edit mode, modifiers | `outliner_delete_unused_data` |
| `META` | `bpy.data.metaballs` | `create_metaball` | Metaball editing | `outliner_delete_unused_data` |
| `MOVIE` | `bpy.data.movieclips` | VSE import | Tracking | `outliner_delete_unused_data` |
| `NODETREE` | `bpy.data.node_groups` | Shader/GN/Compositor templates | Node editing | `outliner_delete_unused_data` |
| `OBJECT` | `bpy.data.objects` | All creation templates | Transform, parent, etc. | `outliner_delete_unused_data` |
| `PARTICLES` | `bpy.data.particles` | `physics_add_particle_system` | Particle settings | `outliner_delete_unused_data` |
| `SCENE` | `bpy.data.scenes` | New scene | Render settings, world | Scene deletion |
| `SOUND` | `bpy.data.sounds` | `vse_add_sound_strip` | Sound settings | `outliner_delete_unused_data` |
| `SPEAKER` | `bpy.data.speakers` | Speaker creation | Speaker settings | `outliner_delete_unused_data` |
| `TEXT` | `bpy.data.texts` | Text editor templates | Text editing | `outliner_delete_unused_data` |
| `TEXTURE` | `bpy.data.textures` | Texture creation | Texture settings | `outliner_delete_unused_data` |
| `VOLUME` | `bpy.data.volumes` | Volume import, GN conversion | Volume settings | `outliner_delete_unused_data` |
| `WORLD` | `bpy.data.worlds` | `render_setup_world`, `render_setup_hdri` | World shader | `outliner_delete_unused_data` |
| `WINDOW_MANAGER` | — | (system) | UI state | — |
| `WORKSPACE` | `bpy.data.workspaces` | Workspace ops | Workspace layout | — |

### 8.3 Data Block Relationship Graph

```
SCENE
├── WORLD
│   └── NODETREE (world shader)
├── COLLECTION (master)
│   ├── COLLECTION (child)
│   │   ├── OBJECT
│   │   │   ├── MESH / CURVE / ARMATURE / LATTICE / META / FONT / VOLUME
│   │   │   │   └── MODIFIER[]
│   │   │   │       └── NODETREE (GN modifier)
│   │   │   ├── MATERIAL[]
│   │   │   │   └── NODETREE (shader)
│   │   │   ├── ACTION (animation data)
│   │   │   │   └── FCURVE[]
│   │   │   ├── CONSTRAINT[]
│   │   │   └── PARTICLES[]
│   │   ├── CAMERA (as OBJECT.data)
│   │   ├── LIGHT (as OBJECT.data)
│   │   └── SPEAKER (as OBJECT.data)
│   └── ...
├── VIEW_LAYER[]
└── SEQUENCE_EDITOR
    └── STRIP[] (MOVIE, SOUND, IMAGE, TEXT, EFFECT)
```

---

## 9. Editor Mode Awareness

### 9.1 Mode Registry

Blender has ~15 interactive modes. The orchestrator must know which mode it's in and which templates are valid:

| Mode | `context.mode` | Editor | Valid Template Categories |
|------|---------------|--------|--------------------------|
| `OBJECT` | `'OBJECT'` | VIEW_3D | primitives, modifiers, materials, lighting, camera, organize, physics, render |
| `EDIT_MESH` | `'EDIT_MESH'` | VIEW_3D | edit_mesh, modifiers (limited) |
| `EDIT_CURVE` | `'EDIT_CURVE'` | VIEW_3D | curve_edit |
| `EDIT_ARMATURE` | `'EDIT_ARMATURE'` | VIEW_3D | armature_edit |
| `EDIT_METABALL` | `'EDIT_METABALL'` | VIEW_3D | metaball_edit |
| `EDIT_LATTICE` | `'EDIT_LATTICE'` | VIEW_3D | lattice_edit |
| `EDIT_TEXT` | `'EDIT_TEXT'` | VIEW_3D | text_edit |
| `POSE` | `'POSE'` | VIEW_3D | pose, animation, constraints |
| `SCULPT` | `'SCULPT'` | VIEW_3D | sculpt |
| `PAINT_WEIGHT` | `'PAINT_WEIGHT'` | VIEW_3D | weight_paint |
| `PAINT_VERTEX` | `'PAINT_VERTEX'` | VIEW_3D | vertex_paint |
| `PAINT_TEXTURE` | `'PAINT_TEXTURE'` | VIEW_3D | texture_paint |
| `PAINT_GPENCIL` | `'PAINT_GPENCIL'` | VIEW_3D | gpencil |
| `SCULPT_GPENCIL` | `'SCULPT_GPENCIL'` | VIEW_3D | gpencil_sculpt |
| `WEIGHT_GPENCIL` | `'WEIGHT_GPENCIL'` | VIEW_3D | gpencil_weight |

### 9.2 Mode Switching

The orchestrator can auto-switch modes when a template requires it:

```python
MODE_SWITCH_MAP = {
    "OBJECT": ("object.mode_set", {"mode": "OBJECT"}),
    "EDIT_MESH": ("object.mode_set", {"mode": "EDIT"}),
    "SCULPT": ("object.mode_set", {"mode": "SCULPT"}),
    "POSE": ("object.mode_set", {"mode": "POSE"}),
    # ... etc
}
```

Mode switches are inserted automatically between plan steps when the required mode changes.

---

## 10. Operator-Based UI Design

### 10.1 The Problem with Chat-Only

Chat is great for exploration and novel requests. But for repetitive tasks, an artist should not have to type the same request every time. They need a button.

**Chat is the discovery mechanism. Operators are the productivity mechanism.**

### 10.2 Operator Categories

| Category | Trigger | Examples |
|----------|---------|----------|
| **Quick Actions** | One-click button in sidebar | Organize Outliner, Clean Scene, Setup Lighting |
| **Contextual Actions** | Right-click menu, depends on selection | Frame Camera on Selected, Apply Material to Selected |
| **Guided Actions** | Button opens a dialog with options | Sort Outliner (dropdown: by name/type/material/location) |
| **Creative Actions** | Button triggers AI planning | Make Cinematic, Match Lighting, Generate Variations |
| **Chat Actions** | Freeform text input | "Make a stonehenge", "Light this like a Rembrandt painting" |

### 10.3 Menu Integration

| Menu | Submenu | Operators |
|------|---------|-----------|
| **Object > Coworker** | — | Organize Scene, Rename by Type, Group Selected, Clean Up |
| **Mesh > Coworker** | — | Clean Mesh, Optimize for 3D Print, Apply All Modifiers, Recalculate Normals |
| **Add > Coworker** | Primitives | Cube, Sphere, Cylinder, Cone, Torus, Plane, Monkey, Text, Empty, Camera, Light |
| **Add > Coworker** | Scene Setups | Three-Point Lighting, Studio Lighting, HDRI Environment, Turntable Setup |
| **Render > Coworker** | — | Quick Render Setup, Cinematic Look, Product Shot Setup, Batch Render |
| **Node > Coworker** | — | Frame Selected Nodes, Organize Node Tree, Color-Code Nodes, Add Node Preset |
| **Text Editor > Coworker** | — | New Addon, New Operator, New Panel, New Modal Operator, Register Current Text |
| **Outliner > Coworker** | — | Sort by Name, Sort by Type, Sort by Material, Color Tag Collections, Purge Unused Data |
| **3D Viewport > Coworker** | — | Frame Camera, Setup Lighting, Apply Material, Add Modifier Stack |
| **Sidebar (N-Panel) > Coworker** | Quick Actions | (All contextual quick actions based on current editor/mode) |
| **Sidebar (N-Panel) > Coworker** | Chat | (Full chat interface) |
| **Sidebar (N-Panel) > Coworker** | Assets | (Asset browser integration) |

### 10.4 Operator Pattern

Each operator follows a consistent pattern:

```python
class BFACW_OT_organize_outliner(Operator):
    """Organize the outliner: sort, rename, color-tag, and purge unused data."""
    bl_idname = "bfacw.organize_outliner"
    bl_label = "Organize Outliner"
    bl_description = "Sort objects by type, rename with prefixes, color-tag collections, and purge orphan data"
    bl_options = {'REGISTER', 'UNDO'}
    
    sort_method: EnumProperty(
        name="Sort By",
        items=[
            ('TYPE', "Type", "Group by object type"),
            ('NAME', "Name", "Sort alphabetically"),
            ('MATERIAL', "Material", "Group by material"),
            ('LOCATION', "Location", "Group by world position"),
        ],
        default='TYPE',
    )
    
    def execute(self, context):
        # 1. Gather context
        scene_info = get_scene_summary()
        
        # 2. Build plan based on user's sort choice
        plan = [
            {"template": "outliner_sort_by_type", "params": {}},
            {"template": "outliner_rename_by_type", "params": {"prefix": "", "use_numbering": True}},
            {"template": "outliner_add_color_tag", "params": {"color": "AUTO"}},
            {"template": "outliner_delete_unused_data", "params": {"data_type": "ALL"}},
        ]
        
        # 3. Execute plan via orchestrator
        result = execute_plan(plan)
        
        # 4. Report
        if result["status"] == "ok":
            self.report({'INFO'}, f"Outliner organized: {len(result['steps'])} steps completed")
        else:
            self.report({'WARNING'}, f"Partial success: some steps failed")
        
        return {'FINISHED'}
```

### 10.5 Contextual Operator Discovery

The sidebar panel dynamically shows operators based on context:

```python
def draw_contextual_operators(layout, context):
    """Draw operator buttons relevant to the current editor and mode."""
    editor = context.area.type if context.area else None
    mode = context.mode if context.object else 'OBJECT'
    
    if editor == 'VIEW_3D' and mode == 'OBJECT':
        layout.operator("bfacw.organize_outliner", icon='OUTLINER')
        layout.operator("bfacw.setup_lighting", icon='LIGHT')
        layout.operator("bfacw.frame_camera", icon='CAMERA_DATA')
        if context.selected_objects:
            layout.operator("bfacw.apply_material_to_selected", icon='MATERIAL')
    
    elif editor == 'VIEW_3D' and mode == 'EDIT_MESH':
        layout.operator("bfacw.clean_mesh", icon='MESH_DATA')
        layout.operator("bfacw.optimize_for_print", icon='TOOL_SETTINGS')
    
    elif editor == 'NODE_EDITOR':
        layout.operator("bfacw.organize_nodes", icon='NODETREE')
        layout.operator("bfacw.frame_selected_nodes", icon='FRAME')
    
    elif editor == 'SEQUENCE_EDITOR':
        layout.operator("bfacw.organize_strips", icon='SEQUENCE')
    
    # Always available
    layout.separator()
    layout.operator("bfacw.clean_scene", icon='BRUSH_DATA')
```

---

## 11. Creative Workflows

### 11.1 Camera Framing

The model understands spatial relationships and can translate natural language into camera positions:

| User Intent | Camera Setup | Technical Details |
|-------------|-------------|-------------------|
| "Frame from above" | Camera at (0, 0, 15), rotation (0, 0, 0), 35mm | Top-down ortho-like perspective |
| "Close-up on face" | Find object with "face"/"head" in name, 2 units away, 85mm | Portrait lens, shallow DOF |
| "Wide establishing shot" | Camera at distance, 24mm wide lens | Deep focus, shows environment |
| "Dutch angle" | Camera tilted 15-30° on Z, slight low angle | Dramatic, unsettling feel |
| "Over-the-shoulder" | Camera behind and to side of subject | Two-shot composition |
| "Follow the action" | Track To constraint on moving object | Dynamic tracking |
| "Rule of thirds composition" | Frame subject at 1/3 intersection | Classic composition |
| "Golden ratio composition" | Frame subject at golden spiral point | Natural composition |
| "Symmetrical / Wes Anderson" | Camera perfectly centered, level | Stylized symmetry |
| "Low angle heroic" | Camera below subject, looking up | Empowering perspective |

### 11.2 Lighting from Description

The model interprets lighting descriptions and maps them to Blender light setups:

| Description | Light Setup | Technical Details |
|-------------|------------|-------------------|
| "Rembrandt painting" | Key: warm 2700K, 45° right, intensity 800. Fill: cool 5600K, 45° left, intensity 200. Rim: 3200K, behind, intensity 400 | Classic triangle of light on cheek |
| "Studio product photography" | Key: soft area light above, intensity 500. Fill: large area front, intensity 200. Rim: strip lights sides, intensity 300. Background: separate light | Clean, commercial look |
| "Moonlit night" | Single area light, cool blue (10000K), intensity 50, positioned high. World: dark blue, strength 0.1 | Low-key, atmospheric |
| "Golden hour sunset" | Sun lamp: warm 3000K, elevation 15°, intensity 5. World: orange-pink gradient, strength 0.5 | Warm, long shadows |
| "Cyberpunk / neon" | Multiple point lights: magenta, cyan, yellow. Intensity 200 each. World: dark, strength 0.05. Emission materials on scene elements | High contrast, colored shadows |
| "Overcast / soft" | Single large area light above, intensity 300. World: grey-blue, strength 0.3. No harsh shadows | Flat, even lighting |
| "Film noir" | Single spot light, hard shadows, intensity 1000. World: black, strength 0. Volumetric for light rays | High contrast B&W feel |
| "Match reference image" | Analyze reference: detect light direction from shadows, color temperature from highlights, intensity from exposure | Replicates real lighting |

### 11.3 Asset-Aware Workflows

The MCP knows what assets are available in the user's libraries:

| Intent | Asset Strategy | Implementation |
|--------|---------------|----------------|
| "Add wood material" | Search asset libraries for "wood", "oak", "timber", "veneer". Apply best match with UV mapping | `search_assets` → `load_asset_in_context` → `assign_material_to_objects` |
| "Scatter rocks on ground" | Find rock/stone assets, use GN scatter on selected ground plane, randomize scale/rotation | `search_assets` → `gn_scatter_on_faces` with instance collection |
| "Furnish this room" | Search for furniture assets by tags (chair, table, lamp, etc.), place in scene | `search_assets` → `load_asset_in_context` × N |
| "Add vegetation" | Find plant/tree assets, scatter on terrain with GN, vary scale | `search_assets` → `gn_scatter_on_faces` |
| "Use brick texture" | Check asset libraries for brick materials. If found, apply with displacement. If not, use procedural brick | Asset-first, procedural fallback |
| "Match this style" | Analyze existing materials in scene, find similar assets, apply consistently | `get_objects_summary` → `search_assets` → batch apply |

### 11.4 Scene Composition Workflows

| Intent | What Happens | Templates Used |
|--------|-------------|----------------|
| "Make it cinematic" | Set Cycles, 1920×1080, 24fps, three-point lighting, camera with DOF, compositor with glare + color balance + vignette | `render_set_engine` + `render_set_resolution` + `light_three_point` + `camera_add_dof` + `comp_setup_cinematic` |
| "Turntable render" | Camera on empty at center, empty rotates 360° over N frames, three-point lighting, output PNG sequence | `render_setup_turntable` + `light_three_point` + `render_set_output` |
| "Product shot" | White background, soft overhead lighting, camera at 45° elevation, 85mm lens, DOF on product | `light_product` + `camera_frame_selected` + `render_set_engine` |
| "Architectural visualization" | Sun+sky lighting, wide camera, ambient occlusion, render layers for compositing | `light_outdoor_sun` + `camera_frame_from_angle` + `render_view_layer_setup` |
| "Character showcase" | Three-point lighting, turntable, DOF, neutral background | `light_three_point` + `render_setup_turntable` + `camera_add_dof` |

---

## 12. Text Editor as IDE Agent

### 12.1 The Vision

The Text Editor is where Blender power-users write scripts, addons, and expressions. The Coworker should be a first-class IDE agent in this space — not just generating code, but understanding Blender's API surface deeply.

### 12.2 Text Editor-Specific Capabilities

| Capability | What It Does | How It Works |
|-----------|-------------|--------------|
| **Generate Addon** | Create a complete, registered addon from description | Template chain: skeleton → operator → panel → register |
| **Generate Operator** | Create a single operator class | Template: `text_operator_template` |
| **Explain Selection** | Explain what selected code does, line by line | Model reads selection, explains in plain English |
| **Fix API Errors** | Detect and fix deprecated/incorrect API usage | Auto-fix rules + preflight applied to text buffer |
| **Add Type Annotations** | Add Python type hints to script | Model analyzes code, adds annotations |
| **Convert to Addon** | Take a script and wrap it as a registered addon | Template: `text_addon_skeleton` with code inserted |
| **Generate Keymap** | Create keymap registration for an operator | Template: `text_keymap_addon` |
| **Document Code** | Add docstrings and comments | Model reads code, generates documentation |
| **Refactor** | Rename variables, extract functions, reorganize | Model understands code structure |
| **Write Expression** | Generate a driver expression or scripted expression | Model writes expression, explains it |
| **Search API** | Find the right bpy API for a task | `search_api_docs` tool |
| **Generate Tests** | Create test cases for an operator/addon | Model generates test boilerplate |

### 12.3 Text Editor Context

When the user is in the Text Editor, the orchestrator injects:

- The current text buffer content (or selection)
- The text name
- Whether the text is registered
- Available bpy modules relevant to the code

### 12.4 Code Generation Pipeline

```
User: "Write an operator that randomizes the location of selected objects"
  → Context: TEXT_EDITOR, empty buffer
  → Intent classifier: "code" → "generate_operator"
  → Planner: picks text_operator_template with params filled from description
  → Template renders:
      class OBJECT_OT_randomize_location(bpy.types.Operator):
          bl_idname = "object.randomize_location"
          bl_label = "Randomize Location"
          ...
  → Code inserted into text buffer
  → Model explains what was generated and how to register it
```

---

## 13. Documentation Coverage Strategy

### 13.1 Blender Manual Structure (15 sections, ~80 subsections)

| # | Manual Section | Subsections | Template Coverage Target | Priority |
|---|---------------|-------------|--------------------------|----------|
| 1 | User Interface | Windows, menus, panels, shortcuts, status bar | Low (informational) | P3 |
| 2 | Editors | 3D Viewport, Image, UV, Shader, Compositor, Texture, Geometry Node, Video Sequencer, Clip, Dope Sheet, Graph, NLA, Text, Outliner, Properties, File Browser, Preferences | Very High (per-editor templates) | P0 |
| 3 | Scenes & Objects | Scenes, objects, collections, view layers, instancing | High (organize, manage) | P0 |
| 4 | Modeling | Meshes, curves, surfaces, metaballs, text, volumes, modifiers | Very High (20+ templates) | P0 |
| 5 | Sculpting & Painting | Sculpting, texture paint, vertex paint, weight paint | High (8+ templates) | P1 |
| 6 | Grease Pencil | Drawing, animation, modifiers, materials | Medium (6+ templates) | P2 |
| 7 | Animation & Rigging | Keyframes, armatures, constraints, drivers, shape keys, motion paths | Very High (16+ templates) | P0 |
| 8 | Physics | Particles, rigid body, cloth, soft body, fluid, force fields, collision | High (8+ templates) | P1 |
| 9 | Rendering | EEVEE, Cycles, materials, textures, lighting, world, output, layers/passes | Very High (18+ templates) | P0 |
| 10 | Compositing | Node types, render layers, filters, matte, distort, color, vector | High (14+ templates) | P1 |
| 11 | Motion Tracking | Tracking, masking, solving, stabilization | Medium (4+ templates) | P2 |
| 12 | Video Editing | Strips, effects, transitions, audio, proxies | High (12+ templates) | P1 |
| 13 | Assets, Files, Data | Data-blocks, asset libraries, import/export, linked libraries | High (data-block system) | P0 |
| 14 | Add-ons | Built-in addons, installation | Low (informational) | P3 |
| 15 | Advanced | Scripting, command-line, keymap customization | High (text editor templates) | P1 |

### 13.2 Coverage Targets

| Priority | Sections | Template Count | Coverage Goal |
|----------|----------|---------------|---------------|
| **P0** (Must have) | Editors, Scenes/Objects, Modeling, Animation, Rendering, Data System | ~80 templates | 80% of common operations |
| **P1** (Should have) | Sculpting, Physics, Compositing, Video Editing, Advanced | ~40 templates | 60% of common operations |
| **P2** (Nice to have) | Grease Pencil, Motion Tracking | ~15 templates | 40% of common operations |
| **P3** (Informational) | UI, Add-ons | 0 templates | Doc search only |

**Total target: ~135 templates across all sections.**

### 13.3 Template → Documentation Mapping

Every template should link to the relevant documentation section. When the orchestrator uses a template, it can offer the user a "Learn more" link:

```python
TEMPLATE_DOCS = {
    "create_torus": "https://docs.blender.org/manual/en/latest/modeling/meshes/primitives.html#torus",
    "add_subsurf": "https://docs.blender.org/manual/en/latest/modeling/modifiers/generate/subdivision_surface.html",
    "principled_basic": "https://docs.blender.org/manual/en/latest/render/materials/components/principled.html",
    # ... etc
}
```

---

## 14. Scalability & Plugin Architecture

### 14.1 The Plugin System

Templates, auto-fix rules, intent patterns, and template chains should all be extensible via a plugin system. This allows:

- **Community contributions** — Users can add templates for their workflows
- **Domain-specific packs** — Architecture, character art, VFX, motion graphics
- **Version-specific plugins** — Blender 5.x vs 6.x API differences
- **Asset library integrations** — Polyhaven, BlenderKit, custom libraries

### 14.2 Plugin Structure

```
addon/bfa_coworker/
├── plugins/                    # Plugin directory
│   ├── __init__.py             # Plugin discovery
│   ├── _base.py                # Base plugin class
│   ├── core/                   # Built-in plugins (always loaded)
│   │   ├── primitives.py       # Mesh primitives
│   │   ├── modifiers.py        # Modifier templates
│   │   ├── materials.py        # Material/shader templates
│   │   ├── lighting.py         # Lighting templates
│   │   ├── camera.py           # Camera templates
│   │   ├── animation.py        # Animation templates
│   │   ├── compositing.py      # Compositor templates
│   │   ├── vse.py              # Video sequencer templates
│   │   ├── outliner.py         # Outliner/scene management
│   │   ├── text_editor.py      # Text editor templates
│   │   ├── physics.py          # Physics templates
│   │   └── rendering.py        # Rendering templates
│   └── community/              # User-installed plugins
│       └── .gitkeep
```

### 14.3 Plugin API

```python
class CoworkerPlugin:
    """Base class for template plugins."""
    
    # Plugin metadata
    name: str = ""
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    
    # What this plugin provides
    templates: dict[str, Callable] = {}       # name → template function
    chains: dict[str, list] = {}              # name → list of (template, params)
    autofix_rules: list[tuple] = []           # (pattern, replacement, description)
    intent_patterns: dict[str, list] = {}     # category → list of regex patterns
    operators: list[type] = []                # Blender operator classes
    
    def register(self) -> None:
        """Called when the plugin is loaded."""
        ...
    
    def unregister(self) -> None:
        """Called when the plugin is unloaded."""
        ...
```

### 14.4 Plugin Discovery

Plugins are auto-discovered at startup:

```python
def discover_plugins() -> list[CoworkerPlugin]:
    """Find all plugins in the plugins/ directory."""
    plugins = []
    plugins_dir = Path(__file__).parent / "plugins"
    
    # Core plugins
    for plugin_file in (plugins_dir / "core").glob("*.py"):
        if plugin_file.stem.startswith("_"):
            continue
        plugin = load_plugin(plugin_file)
        if plugin:
            plugins.append(plugin)
    
    # Community plugins
    community_dir = plugins_dir / "community"
    if community_dir.exists():
        for plugin_file in community_dir.glob("*.py"):
            if plugin_file.stem.startswith("_"):
                continue
            plugin = load_plugin(plugin_file)
            if plugin:
                plugins.append(plugin)
    
    return plugins
```

### 14.5 Adding a New Template (Developer Workflow)

1. **Create a plugin file** (or add to existing):
   ```python
   # plugins/core/primitives.py
   class PrimitivesPlugin(CoworkerPlugin):
       name = "Mesh Primitives"
       version = "1.0.0"
       
       templates = {
           "create_torus": _tmpl_create_torus,
           "create_cube": _tmpl_create_cube,
           # ...
       }
   ```

2. **Write the template function** with metadata in docstring.

3. **Add tests** in `tests/test_templates.py`.

4. **Done.** The orchestrator discovers it automatically. No other files need to change.

---

## 15. Template Complexity Tiers

### 15.1 Why Tiers Matter

Not all templates are equal. A "subdivide mesh" template is fundamentally different from a "make this scene cinematic" template. The orchestrator must understand complexity to:

- **Price plans correctly** — A simple template costs 1 step. A high-level chain costs 5-10 steps.
- **Set user expectations** — "This will take a moment" vs "This is instant"
- **Manage undo granularity** — Simple ops get per-step undo. High-level chains get one undo for the whole chain.
- **Enable composition** — Simple templates compose into moderate chains. Moderate chains compose into high-level workflows.
- **Guide the model** — The planner model needs to know which templates are atomic and which are compound.

### 15.2 The Three Tiers

| Tier | Name | Operator Count | Scope | User Mental Model | Example |
|------|------|---------------|-------|-------------------|---------|
| **T1** | Simple / Atomic | 1-2 ops | Single object, isolated context | "I have A, take it to B" | Subdivide mesh, add bevel, create cube |
| **T2** | Moderate / Contextual | 3-5 ops | Group of objects, relative to each other or scene | "I have A with intention B, I need C applied to A relative to B" | Arrange objects in circle + merge + organize outliner |
| **T3** | High-Level / Scene | 6+ ops | Cross-editor, cross-datablock, from nothing to result | "I have A, take it to B, with systems like C I can tune later" | Full cinematic scene setup, product shot from scratch |

### 15.3 Tier 1: Simple / Atomic Templates

**Characteristics:**
- 1-2 Blender operators chained in a single context
- Operates on a selected object in isolated context
- The user could do this manually with 1-2 clicks/keystrokes
- Useful as shorthand for repetitive linear workflows
- Low priority for standalone use, HIGH priority as building blocks for T2/T3

**Examples:**

| Template | Ops | What It Does | Why T1 |
|----------|-----|-------------|--------|
| `subdivide_mesh` | 1 | `bpy.ops.mesh.subdivide()` | Single operator, selected mesh |
| `bevel_edges` | 1 | `bpy.ops.mesh.bevel()` | Single operator, selected edges |
| `shade_smooth` | 1 | `bpy.ops.object.shade_smooth()` | Single toggle |
| `remove_doubles` | 1 | `bpy.ops.mesh.remove_doubles()` | Single cleanup op |
| `create_cube` | 1 | `bpy.ops.mesh.primitive_cube_add()` | Single primitive |
| `add_subsurf` | 1 | `obj.modifiers.new(type="SUBSURF")` | Single modifier |
| `set_origin` | 1 | `bpy.ops.object.origin_set()` | Single origin change |
| `apply_transform` | 1-2 | Apply loc/rot/scale | Single apply operation |
| `duplicate_objects` | 1-2 | Duplicate + offset | Simple duplication |
| `keyframe_location` | 1-2 | Set location + keyframe | Single keyframe |

**T1 Metadata:**
```python
{
    "tier": 1,
    "atomic": True,           # Cannot be broken down further
    "composable": True,       # Can be used in T2/T3 chains
    "standalone_useful": False,  # Usually used as part of something bigger
    "undo_granularity": "per_step",
}
```

### 15.4 Tier 2: Moderate / Contextual Templates

**Characteristics:**
- 3-5 operators chained in context
- Operates on a group of objects with relationships to 3D space, each other, or multiple editors
- The chain of operations is done in context relative to other objects, workflows, or editors
- This is the **most useful tier** for the Coworker — complex enough to save real time, simple enough for local models to plan reliably
- Bridges linear and procedural workflows

**Examples:**

| Template | Ops | What It Does | Cross-Editor? |
|----------|-----|-------------|---------------|
| `arrange_in_circle` | 3-4 | Select objects → calculate circle positions → move each → (optional) parent to empty | VIEW_3D only |
| `clean_mesh_for_print` | 4 | Apply transforms → recalculate normals → remove doubles → add solidify | VIEW_3D only |
| `organize_outliner` | 4-5 | Sort by type → rename with prefix → color-tag collections → purge orphan data | VIEW_3D → OUTLINER |
| `setup_material_from_assets` | 3-4 | Search assets → load best match → assign to selected → setup UV if needed | VIEW_3D → ASSET_BROWSER |
| `three_point_lighting` | 4-5 | Create key light → create fill light → create rim light → position all → set intensities | VIEW_3D only |
| `frame_camera_cinematic` | 3-4 | Frame on selection → add DOF → add track-to constraint → set composition guide | VIEW_3D → PROPERTIES |
| `scatter_assets_on_terrain` | 4-5 | Search assets → create collection → setup GN scatter → randomize scale/rot → assign materials | VIEW_3D → NODE_EDITOR → ASSET_BROWSER |
| `setup_shot_in_sequencer` | 4-5 | Add movie strip → add sound strip → trim both → add fade in/out → add text overlay | SEQUENCE_EDITOR only |
| `apply_material_by_name` | 3-4 | Parse object names → match to material keywords → search/create materials → assign to matching objects | VIEW_3D → OUTLINER |
| `audit_and_optimize_material` | 3-5 | Analyze node tree → count nodes → identify bottlenecks → simplify where possible → report savings | NODE_EDITOR only |

**T2 Metadata:**
```python
{
    "tier": 2,
    "atomic": False,
    "composable": True,        # Can be used in T3 workflows
    "standalone_useful": True, # The sweet spot for operator buttons
    "undo_granularity": "per_chain",  # One undo for the whole chain
    "cross_editor": False,     # Usually single-editor (or editor + properties)
    "estimated_time": "1-3 seconds",
}
```

### 15.5 Tier 3: High-Level / Scene Templates

**Characteristics:**
- 6+ operators spanning multiple editors, data-block types, and modes
- Goes from nothing (or minimal starting state) to a complete usable result
- Works at all levels: linear workflows → procedural systems → usable scene/asset results
- The user gets procedural systems they can manually tune later
- These are **pre-built chains** that the model selects as a single decision

**Examples:**

| Template | Ops | What It Does | Editors Touched |
|----------|-----|-------------|-----------------|
| `cinematic_look` | 8-10 | Set render engine → resolution → three-point lights → camera with DOF → compositor with glare/balance/vignette → world setup | VIEW_3D → PROPERTIES → NODE_EDITOR (compositor) → NODE_EDITOR (shader) |
| `product_shot_setup` | 7-9 | Create backdrop plane → product lighting → frame camera at 45° → 85mm lens → DOF on product → render settings → output path | VIEW_3D → PROPERTIES |
| `archviz_scene_setup` | 8-12 | Sun+sky lighting → wide camera → AO setup → render layers → material assignment from assets → proxy setup → view layer management | VIEW_3D → PROPERTIES → NODE_EDITOR → OUTLINER |
| `character_turntable` | 8-10 | Create empty at origin → parent camera → three-point lights → neutral background → 360° rotation over N frames → output PNG sequence → render settings | VIEW_3D → PROPERTIES → DOPESHEET |
| `shot_to_master_edit` | 6-8 | Render current frame → add to VSE as strip → add scene strip for full sequence → setup proxy → add metadata text → organize channels | VIEW_3D → SEQUENCE_EDITOR |
| `version_nodetree` | 5-7 | Copy node group → append version suffix → store in blend file text block → update all users to new version → keep old as backup | NODE_EDITOR → TEXT_EDITOR → OUTLINER |
| `scene_audit_and_optimize` | 8-15 | Count all data-blocks → identify orphan data → analyze material complexity → check texture sizes → suggest view layer optimizations → report with recommendations → optionally apply fixes | ALL |
| `props_to_grid` | 5-7 | Select props → calculate bounding boxes → arrange in grid with spacing → randomize slight rotation → group into collection → name by type | VIEW_3D → OUTLINER |
| `update_shot_with_assets` | 6-9 | Identify objects in shot → search asset library for replacements → swap meshes while keeping transforms → update materials → adjust lighting for new assets → report changes | VIEW_3D → ASSET_BROWSER → NODE_EDITOR |

**T3 Metadata:**
```python
{
    "tier": 3,
    "atomic": False,
    "composable": False,       # Terminal — produces a complete result
    "standalone_useful": True, # The "one-click magic" tier
    "undo_granularity": "single_undo",  # One undo for everything
    "cross_editor": True,      # Always spans multiple editors
    "estimated_time": "5-15 seconds",
    "requires_confirmation": True,  # "This will modify your scene. Continue?"
}
```

### 15.6 Tier-Aware Planning

The orchestrator's planner model receives tier information with each template:

```
Available templates:
  [T1] create_cube — Add a cube (1 op, single object)
  [T1] subdivide_mesh — Subdivide selected (1 op, edit mode)
  [T2] arrange_in_circle — Arrange objects in circle (4 ops, multi-object)
  [T2] three_point_lighting — Classic 3-point light setup (5 ops)
  [T3] cinematic_look — Full cinematic scene setup (10 ops, cross-editor)
```

The model learns to:
- **Prefer T2 for most requests** — They're the sweet spot of power vs reliability
- **Use T1 as building blocks** — When no T2/T3 matches, compose T1s into a plan
- **Use T3 for clear intent matches** — "Make it cinematic" → one decision
- **Avoid T3 when uncertain** — If the intent is vague, ask clarifying questions rather than guessing a T3

### 15.7 Tier Composition Rules

```
T1 + T1 + T1 → can form a T2 chain (model composes them)
T2 + T2 + T2 → can form a T3 chain (model composes them)
T1 + T2 → valid (T2 absorbs T1 context)
T3 + anything → invalid (T3 is terminal — it produces a complete result)
```

The orchestrator enforces these rules during plan validation. If the model proposes `cinematic_look` followed by `add_bevel`, the validator rejects it: "T3 templates are terminal and cannot be followed by other steps."

---

## 16. Multi-Editor Pathway Planning

### 16.1 The Problem

Blender is notorious for multi-editor workflows. Common examples:

| Task | Editors Involved | Why It's Painful |
|------|-----------------|------------------|
| Model then shade | VIEW_3D → NODE_EDITOR | Must leave modeling context, open shader editor, find material |
| Animate then render | VIEW_3D → DOPESHEET → PROPERTIES | Must set keyframes, tune curves, then configure render |
| Sculpt then retopo then UV | VIEW_3D (sculpt) → VIEW_3D (edit) → UV_EDITOR | Three different modes, two different editors |
| Comp then VSE | NODE_EDITOR (compositor) → SEQUENCE_EDITOR | Render → find strips → assemble |
| Scene setup | VIEW_3D → PROPERTIES → OUTLINER → NODE_EDITOR | Properties for render settings, outliner for organization, nodes for world |
| Asset workflow | ASSET_BROWSER → VIEW_3D → NODE_EDITOR | Find asset → place → assign materials |

The orchestrator must handle these pathways seamlessly. The user shouldn't have to think about which editor they're in — they describe the goal, and the orchestrator navigates the editors.

### 16.2 The Editor Graph

Editors in Blender have natural affinities. The orchestrator models these as a weighted graph:

```python
EDITOR_AFFINITIES = {
    "VIEW_3D": {
        "PROPERTIES": 0.9,     # Constantly used together (modifiers, materials, render)
        "OUTLINER": 0.8,       # Organization, visibility, collections
        "NODE_EDITOR": 0.7,    # Shader setup, geometry nodes
        "UV_EDITOR": 0.6,      # UV unwrapping after modeling
        "IMAGE_EDITOR": 0.5,   # Texture painting, image viewing
        "DOPESHEET_EDITOR": 0.4,  # Animation
        "SEQUENCE_EDITOR": 0.2,   # Rarely direct
    },
    "NODE_EDITOR": {
        "PROPERTIES": 0.8,     # Material/texture settings
        "VIEW_3D": 0.7,        # Preview results
        "OUTLINER": 0.4,       # Material assignment visibility
        "IMAGE_EDITOR": 0.5,   # Texture reference
    },
    "SEQUENCE_EDITOR": {
        "PROPERTIES": 0.7,     # Strip properties, proxy settings
        "IMAGE_EDITOR": 0.6,   # Preview rendered frames
        "OUTLINER": 0.5,       # Scene strips
        "VIEW_3D": 0.3,        # Rarely direct
    },
    # ... etc for all editors
}
```

### 16.3 Pathway Resolution

When the orchestrator receives a multi-editor intent, it resolves the pathway:

```
User: "Set up this shot in the master edit"
  → Intent classifier: "shot_setup" (T3)
  → Context probe: editor=VIEW_3D, camera selected, VSE has existing strips
  → Pathway resolver:
      1. VIEW_3D: frame camera on selection ✓
      2. PROPERTIES: set render resolution, output path ✓
      3. VIEW_3D: render viewport to image ✓
      4. SEQUENCE_EDITOR: add rendered image as strip ✓
      5. SEQUENCE_EDITOR: position after last strip ✓
      6. SEQUENCE_EDITOR: add text overlay with shot name ✓
  → Plan: [camera_frame_selected, render_set_resolution, render_set_output,
            render_viewport, vse_add_image_strip, vse_add_text_strip]
  → Execute with editor switches between steps 3→4
```

### 16.4 Editor Switching Protocol

When a plan requires switching editors, the orchestrator:

1. **Completes all operations in the current editor** before switching
2. **Switches via `bpy.context.area.type`** or by changing workspace
3. **Preserves selection state** across switches (store object names, re-select)
4. **Injects a "context restored" message** so the model knows where it is

```python
def _switch_editor(target_editor: str) -> bool:
    """Switch the active area to the target editor type."""
    import bpy
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == target_editor:
                # Found existing editor of this type — make it active
                area.tag_redraw()
                return True
    # No existing editor — change current area type
    if bpy.context.area:
        bpy.context.area.type = target_editor
        return True
    return False
```

### 16.5 Multi-Editor Plan Format

Plans that span editors include editor context in each step:

```json
{
  "intent": "setup_shot_in_master_edit",
  "pathway": ["VIEW_3D", "PROPERTIES", "VIEW_3D", "SEQUENCE_EDITOR"],
  "steps": [
    {
      "template": "camera_frame_selected",
      "params": {"margin": 0.1},
      "editor": "VIEW_3D",
      "mode": "OBJECT"
    },
    {
      "template": "render_set_resolution",
      "params": {"x": 1920, "y": 1080},
      "editor": "PROPERTIES",
      "mode": null
    },
    {
      "template": "render_viewport_to_path",
      "params": {"filepath": "//shots/shot_001.png"},
      "editor": "VIEW_3D",
      "mode": "OBJECT"
    },
    {
      "template": "vse_add_image_strip",
      "params": {"filepath": "//shots/shot_001.png", "channel": 1, "frame_start": 100},
      "editor": "SEQUENCE_EDITOR",
      "mode": null
    }
  ]
}
```

### 16.6 Common Multi-Editor Pathways

| User Intent | Pathway | Templates Used |
|-------------|---------|----------------|
| "Model this, then shade it" | VIEW_3D(EDIT) → VIEW_3D(OBJECT) → NODE_EDITOR | edit_mesh ops → add_material → principled_basic |
| "Animate and render this" | VIEW_3D → DOPESHEET → PROPERTIES | keyframe_location → set_interpolation → render_set_engine → render_set_output |
| "Sculpt, retopo, and UV this" | VIEW_3D(SCULPT) → VIEW_3D(EDIT) → UV_EDITOR | remesh_voxel → subdivide_mesh → unwrap_uv → pack_islands |
| "Comp this render and add to edit" | NODE_EDITOR → VIEW_3D → SEQUENCE_EDITOR | comp_setup_cinematic → render_viewport → vse_add_image_strip |
| "Find assets and furnish this room" | ASSET_BROWSER → VIEW_3D → OUTLINER | search_assets → load_asset × N → outliner_group_selected |
| "Light this scene for night" | VIEW_3D → NODE_EDITOR(world) → PROPERTIES | light_night_scene → render_setup_world → render_setup_eevee |
| "Prepare this character for animation" | VIEW_3D → OUTLINER → DOPESHEET | apply_transform → outliner_rename_by_type → anim_setup_walk_cycle |
| "Version this node tree and update scene" | NODE_EDITOR → TEXT_EDITOR → OUTLINER | (copy node group) → text_register_current → outliner_organize_scene |

### 16.7 Pathway Learning

The orchestrator records which pathways users take. Over time, it learns:

- **Common pathways** — "Users who model in EDIT_MESH usually then switch to OBJECT and add materials"
- **Pathway suggestions** — After a modeling operation, suggest: "Want me to set up materials for this?"
- **Pathway shortcuts** — Frequently used multi-editor workflows become one-click operators

This data is stored locally in the user's preferences (never leaves the machine).

---

## 17. The Self-Expanding System: Recorder & Macros

### 17.1 The Core Problem with Static Templates

No matter how many templates we write, we'll never cover every workflow. Every artist has unique needs. The system must be **self-expanding** — users must be able to create new templates without writing Python.

**The vision: An artist does something once, the Coworker learns it, and from then on it's a one-click operation.**

### 17.2 The Action Recorder

The Coworker includes an **action recorder** that watches the Blender Python API for operator calls and property changes, then serializes them into a reusable template.

```
┌──────────────────────────────────────────────────────────────┐
│                     ACTION RECORDER                           │
│                                                              │
│  User does work ──→ Recorder watches bpy API ──→ Logs ops   │
│  (modeling,        (monkey-patches operator calls,           │
│   shading,          property sets, mode switches,            │
│   lighting,         selection changes, data-block creates)   │
│   organizing)                                                │
│                                                              │
│  User stops ──→ Recorder analyzes log ──→ Generates template │
│  recording       (deduplicates, finds patterns,              │
│                  identifies parameters to expose,            │
│                  groups into logical steps)                  │
│                                                              │
│  Template saved ──→ Available as operator button ──→ Usable  │
│  to user library   in sidebar, menus, chat, chains           │
└──────────────────────────────────────────────────────────────┘
```

### 17.3 What the Recorder Captures

| Event Type | What's Recorded | Example |
|-----------|----------------|---------|
| `bpy.ops.*()` calls | Operator name + parameters | `bpy.ops.mesh.primitive_cube_add(size=2)` |
| Property sets | Object, property path, old value, new value | `obj.location = (1, 2, 3)` |
| Mode switches | From mode → to mode | `OBJECT → EDIT_MESH` |
| Selection changes | Object names selected/deselected | Selected: ["Cube", "Sphere"] |
| Data-block creation | Type, name | New MATERIAL: "RedPaint" |
| Modifier adds | Object, modifier type, parameters | Cube.SUBSURF(levels=2) |
| Node tree changes | Node adds, links, parameter sets | Added Principled BSDF, linked to output |
| Context switches | Editor changes | VIEW_3D → NODE_EDITOR |

### 17.3a The C++ Blind Spot: What Can't Be Recorded Directly

**Critical limitation**: Many Blender interface actions are executed directly in C++ and never pass through the Python API. These actions are invisible to a Python-level monkey-patcher.

| Action | Why It's Invisible | Workaround |
|--------|-------------------|------------|
| **Reordering modifiers** (drag in UI) | C++ `MODIFIER_OT_move_up/down` doesn't fire Python hooks reliably | **Before/After diff**: snapshot modifier stack before and after, detect reordering |
| **Outliner drag-drop** (reparenting, collection moves) | Outliner operations are C++ native, no `bpy.ops` call | **Before/After diff**: snapshot hierarchy, detect changes |
| **Node editor drag** (repositioning nodes) | Node position changes are C++ transforms, no Python event | **Before/After diff**: snapshot node locations, detect layout changes |
| **Viewport navigation** (pan, orbit, zoom) | Pure C++ view matrix manipulation | Not needed for macros (view state is rarely part of a workflow) |
| **Gizmo interactions** (transform gizmo, GN gizmos) | Gizmo callbacks are C++ with optional Python hooks | **Before/After diff**: snapshot transform values before and after |
| **Asset Browser drag-drop** | Asset placement is C++ native | **Before/After diff**: snapshot scene objects before and after |
| **Timeline scrubbing** | Pure C++ playback | Not needed for macros |
| **Color picker interactions** | C++ color management | **Before/After diff**: snapshot color values |
| **Curve/spline editing in Viewport** | C++ curve manipulation | **Before/After diff**: snapshot curve data |
| **Weight painting strokes** | C++ paint system | **Before/After diff**: snapshot vertex weights |

### 17.3b The Before/After Diff Strategy

To handle the C++ blind spot, the recorder uses a **dual capture strategy**:

```
┌─────────────────────────────────────────────────────────────────┐
│                    DUAL CAPTURE STRATEGY                         │
│                                                                 │
│  Python API Layer                    C++ / UI Layer              │
│  ┌──────────────────┐               ┌──────────────────┐        │
│  │ Monkey-patch     │               │ Before Snapshot  │        │
│  │ bpy.ops.*()      │               │ - Object states  │        │
│  │ Property sets    │               │ - Modifier stacks│        │
│  │ Mode switches    │               │ - Node trees     │        │
│  │ Data-block creates│              │ - Collections    │        │
│  └──────┬───────────┘               │ - Transforms     │        │
│         │                           └──────┬───────────┘        │
│         │                                  │                    │
│         ▼                                  ▼                    │
│  ┌──────────────────────────────────────────────────┐           │
│  │              MERGED EVENT LOG                     │           │
│  │  - Python events (precise, parameter-rich)        │           │
│  │  - Diff events (detected changes, inferred ops)   │           │
│  └──────────────────────────────────────────────────┘           │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────────────────────────────────────────┐           │
│  │           ANALYSIS & DEDUPLICATION                │           │
│  │  - Python events explain the diff → keep Python   │           │
│  │  - Diff has changes Python didn't see → keep diff │           │
│  │  - Python + diff overlap → prefer Python          │           │
│  └──────────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

**Before Snapshot** (taken when recording starts and after each Python event):
```python
def _snapshot_scene_state():
    """Capture complete scene state for diff comparison."""
    import bpy
    return {
        "objects": {
            obj.name: {
                "location": tuple(obj.location),
                "rotation_euler": tuple(obj.rotation_euler),
                "scale": tuple(obj.scale),
                "parent": obj.parent.name if obj.parent else None,
                "modifiers": [
                    {"name": m.name, "type": m.type, "order": i}
                    for i, m in enumerate(obj.modifiers)
                ],
                "material_slots": [ms.material.name if ms.material else None for ms in obj.material_slots],
            }
            for obj in bpy.data.objects
        },
        "collections": {
            col.name: [obj.name for obj in col.objects]
            for col in bpy.data.collections
        },
        "node_trees": {
            ng.name: {
                "nodes": {n.name: tuple(n.location) for n in ng.nodes},
                "links": [(l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier) for l in ng.links],
            }
            for ng in bpy.data.node_groups
        },
        "active_object": bpy.context.active_object.name if bpy.context.active_object else None,
        "mode": bpy.context.mode,
        "editor": bpy.context.area.type if bpy.context.area else None,
    }
```

**Diff Analysis** (compares two snapshots, infers what happened):
```python
def _diff_snapshots(before, after):
    """Compare two scene snapshots and infer what changed."""
    changes = []
    
    # Detect modifier reordering
    for obj_name, after_state in after["objects"].items():
        before_state = before["objects"].get(obj_name)
        if not before_state:
            changes.append({"type": "object_added", "name": obj_name})
            continue
        before_mods = [m["name"] for m in before_state["modifiers"]]
        after_mods = [m["name"] for m in after_state["modifiers"]]
        if before_mods != after_mods:
            changes.append({"type": "modifier_reorder", "object": obj_name, "order": after_mods})
    
    # Detect reparenting
    for obj_name, after_state in after["objects"].items():
        before_state = before["objects"].get(obj_name)
        if before_state and before_state["parent"] != after_state["parent"]:
            changes.append({"type": "reparent", "object": obj_name, "from": before_state["parent"], "to": after_state["parent"]})
    
    # Detect node layout changes
    for ng_name, after_ng in after["node_trees"].items():
        before_ng = before["node_trees"].get(ng_name)
        if before_ng:
            for node_name, after_loc in after_ng["nodes"].items():
                before_loc = before_ng["nodes"].get(node_name)
                if before_loc and before_loc != after_loc:
                    changes.append({"type": "node_moved", "node_tree": ng_name, "node": node_name, "from": before_loc, "to": after_loc})
    
    return changes
```

### 17.3c Reference Implementation: ActionRecorder Addon

The [ActionRecorder addon by InamuraJIN](https://github.com/InamuraJIN/ActionRecorder) provides a proven pattern for Blender macro recording. Key techniques we can adopt:

| Technique | What It Does | How We Adapt It |
|-----------|-------------|-----------------|
| `bpy.app.handlers` hooks | Intercepts operator execution via `undo_post`, `depsgraph_update_post` | Use for detecting C++-side changes after each UI action |
| `msgbus` subscriptions | Watches specific RNA properties for changes | Subscribe to `object.location`, `modifier.order`, `node.location` for diff detection |
| Operator override registry | Maps C++ operator names to Python equivalents | Build a lookup table: `MODIFIER_OT_move_up` → `bpy.ops.object.modifier_move_up()` |
| Modal operator for recording | A running modal that captures events between user actions | Use for "Record" mode — modal operator polls state, takes snapshots |

**Key insight from ActionRecorder**: The addon uses `bpy.app.handlers.depsgraph_update_post` to detect *any* scene change, then diffs to find what changed. This is the same Before/After strategy we need, but we extend it with LLM-assisted analysis to infer *intent* from the diff, not just replay operations.

### 17.4 Recording Modes

| Mode | What It Does | Use Case |
|------|-------------|----------|
| **Full Record** | Captures every API call | "I want to automate exactly this workflow" |
| **Smart Record** | Captures only semantically meaningful changes, deduplicates | "I did this a few times, make it a template" |
| **Diff Record** | Records only what changed between state A and state B | "I organized this scene, capture what I changed" |
| **Guided Record** | User describes intent, recorder suggests what to capture | "I want to make a template for setting up product shots" |

### 17.5 From Recording to Template

The recorder's analysis phase converts raw API logs into a clean template:

```
RAW LOG:
  bpy.ops.object.select_all(action='DESELECT')
  bpy.data.objects['Cube'].select_set(True)
  bpy.context.view_layer.objects.active = bpy.data.objects['Cube']
  bpy.ops.object.mode_set(mode='EDIT')
  bpy.ops.mesh.select_all(action='SELECT')
  bpy.ops.mesh.subdivide(number_cuts=2)
  bpy.ops.object.mode_set(mode='OBJECT')
  bpy.ops.object.shade_smooth()
  bpy.context.object.data.auto_smooth_angle = 0.523599  # 30°

ANALYZED TEMPLATE:
  Name: "subdivide_and_smooth"
  Tier: 1
  Description: "Subdivide mesh 2 times and apply smooth shading with 30° auto-smooth"
  Params to expose: number_cuts, smooth_angle
  Editor: VIEW_3D
  Mode: EDIT_MESH (auto-switches from OBJECT)
  Requires selection: True
```

### 17.6 Parameter Inference

The recorder intelligently identifies which values should become parameters:

| Value Type | Parameter? | Reasoning |
|-----------|-----------|-----------|
| Hardcoded numbers (2, 3, 1.0) | **Yes** — expose as param | User might want different values next time |
| Object names ("Cube", "RedPaint") | **Yes** — expose as param | Different scene, different names |
| File paths | **Yes** — expose as param | Different project, different paths |
| Enum values ("CYCLES", "EDIT") | **Maybe** — expose if changed during recording | If user changed it, it's a parameter |
| Booleans (True/False) | **Maybe** — expose if toggled | If user toggled it, it's a parameter |
| Structural constants (mode='OBJECT') | **No** — keep fixed | Required for the operation to work |

### 17.7 The Macro Library

User-recorded templates are stored in a **Macro Library**:

```
addon/bfa_coworker/
├── macros/                      # User macro library
│   ├── index.json               # Macro registry (name, description, tier, tags)
│   ├── my_product_shot.py       # User-recorded macro
│   ├── organize_my_way.py       # User-recorded macro
│   └── ...                      # Grows over time
```

Each macro is a standard `CoworkerPlugin` — fully compatible with the plugin system:

```python
# macros/my_product_shot.py — auto-generated from recording
class MyProductShot(CoworkerPlugin):
    name = "My Product Shot Setup"
    version = "1.0.0"
    author = "user"  # Recorded from user's actions
    description = "Sets up a product shot with my preferred lighting and camera angle"
    
    templates = {
        "my_product_shot": _tmpl_my_product_shot,
    }
    
    chains = {
        "my_product_shot": [
            ("create_plane", {"name": "Backdrop", "size": 10, "z": -1}),
            ("light_product", {"top_intensity": 500, "front_intensity": 200}),
            ("camera_frame_selected", {"margin": 0.15}),
            ("render_set_engine", {"engine": "CYCLES"}),
            ("render_set_resolution", {"x": 1920, "y": 1920}),
        ],
    }
```

### 17.8 The "Teach Me" Workflow

The ultimate self-expanding workflow:

```
1. Artist: "Watch what I do"
   → Recorder starts in Smart Record mode

2. Artist: models, shades, lights, sets up camera
   → Recorder captures semantically meaningful operations

3. Artist: "Make this a template called 'my product shot'"
   → Recorder analyzes, deduplicates, infers parameters
   → Generates template + chain
   → Saves to macro library
   → Registers as operator: bfacw.my_product_shot

4. Artist (next project): clicks "My Product Shot" button
   → Same setup, different objects, instant result

5. Artist: "Also apply my product shot to these 5 objects"
   → Model composes: for each object, run my_product_shot
```

### 17.9 LLM-Assisted Macro Refinement

After recording, the user can ask the Coworker to improve the macro:

| User Request | What Happens |
|-------------|--------------|
| "Make this work on any selection" | Model generalizes object references to `bpy.context.selected_objects` |
| "Add error handling" | Model wraps operations in try/except with useful error messages |
| "Make it faster" | Model identifies redundant operations, suggests bmesh for speed |
| "Add a progress report" | Model adds print statements or UI progress indicators |
| "Expose these as parameters" | Model identifies hardcoded values and creates operator properties |
| "Make this a proper addon" | Model wraps the macro in full addon boilerplate, ready for Text Editor |

### 17.10 The Macro Marketplace (Future)

Long-term vision: a community marketplace where users share macros:

```
Coworker > Macro Library > Browse Community Macros
  ├── "Architectural Visualization Setup" by @archviz_pro (4.8★, 12K downloads)
  ├── "Character Rig Cleanup" by @rigger_daily (4.6★, 8K downloads)
  ├── "Procedural Rock Scatter" by @environment_artist (4.9★, 15K downloads)
  └── ... 
```

Macros are just Python files — safe to share, easy to review, no binary blobs.

### 17.11 Deferred to Tier 4e: Full Recorder & Macro System

**The recorder and macro system described above is deferred to Tier 4e** to keep Tier 3g in scope for a 1-2 month release. Tier 3g will ship with:

- Static template library (80+ hand-authored templates)
- Plugin system for community template contributions
- The orchestrator pipeline (intent → plan → execute)

Tier 4e will add:
- Full action recorder with Before/After diff capture
- Macro library with save/load/index
- "Teach Me" workflow
- LLM-assisted macro refinement
- Macro marketplace

**Why defer?** The recorder is a complex subsystem (3 weeks estimated) that depends on the orchestrator being stable first. Shipping the orchestrator + static templates first gives users immediate value, and the recorder can be built on top of a proven foundation.

---

## 18. Asset-First Templates: Procedural Assets as Building Blocks

### 18.1 The Insight: Assets ARE Templates

Every procedural asset in Blender — a Geometry Nodes modifier, a shader node group, a compositor node group — is already a template. It has:

- **Named inputs** (parameters the user can tune)
- **Deterministic behavior** (same inputs → same output)
- **Reusability** (can be applied to any compatible object)
- **Composability** (can be chained with other assets)

The orchestrator should treat procedural assets as **first-class templates**. Instead of writing Python code to create a node tree from scratch, it should search the asset library for a matching node group and apply it with parameters.

### 18.2 The Asset Hierarchy

Procedural assets exist at three levels, mirroring our template tiers:

| Asset Level | Template Tier | Example | How the Orchestrator Uses It |
|-------------|--------------|---------|------------------------------|
| **Low-Level** (atomic nodes) | T1 | Noise Texture, Color Ramp, Math node | Building blocks for procedural materials. The orchestrator rarely uses these directly — they're composed into node groups. |
| **Mid-Level** (node groups) | T2 | Animation Preset node, Array System, Trigger System, Curve Customization | **The sweet spot.** Self-contained, parameterized, composable. The orchestrator chains these to build complex behaviors. |
| **High-Level** (complete setups) | T3 | Text Presets, Transition Presets, Camera Setups, Architecture Generators | One-click solutions. The orchestrator applies these as terminal operations. |

### 18.3 Bforartists Default Asset Libraries

Bforartists ships with curated procedural asset libraries ([Addon-Default-Libraries](https://github.com/Draise14/Addon-Default-Libraries)) that the orchestrator can leverage:

#### Geometry Nodes Assets

| Category | Assets | Orchestrator Use |
|----------|--------|-----------------|
| **Animation Presets** | Animation Preset Node (ease types, A→B keyframe logic, trigger input, field support) | Drives any GN-based animation. The orchestrator uses this as the animation backbone for T2/T3 chains. |
| **Array Systems** | Grid Array, Brick Array, Circular Array, Curve Array, Linear Array (with gizmos, randomization, curve deformation, instance collections) | Replaces `add_array` modifier template. More powerful — supports curves, randomization, instance biasing. |
| **Trigger Systems** | Effector-based animation triggers (sphere, cube, cylinder, custom shapes with falloff) | Enables "animate when near" workflows. Orchestrator chains: Trigger → Animation Preset → Array. |
| **Curve Customization** | Outline, Dropshadow Mesh, Extrusion Advanced, Chamfer/Inset | Text and curve effects. Orchestrator uses for "make this text pop" intents. |
| **2D Primitive Shapes** | Curve-based shapes with gizmos, extrusion, chamfer | Replaces mesh primitives for 2D workflows. |
| **Text Presets** | Full text effects (outline, shadow, extrude, inset, self-boolean, material controls, camera-fixed, typewriter, fade, transforms) | High-level: "Add a title card with typewriter effect" → one asset. |
| **Transition Presets** | Lower-thirds, stingers, custom transitions (After Effects-style) | High-level: "Add a stinger transition between these clips" → one asset. |
| **Arrow Presets** | Animated arrows with end types, line types, extrude, outline, shadow | "Add an arrow pointing at this" → one asset. |
| **Data Graph** | CSV to bars, pie charts, graph charts, counters, string selectors | "Visualize this CSV data" → one asset. |
| **Camera Setups** | Camera cull, distance switch, billboard, flatten, fix-to-camera, A→B move, handheld effects (closeup, walking, running, car, spaceship, interview), delay, overshoot, auto-focus, orbit | Replaces camera templates. More powerful — includes handheld presets, auto-focus, orbit. |
| **Grease Pencil** | Screenspace radius, taper strokes, advanced smoothness/opacity/color, billboard textures, GP-to-mesh (stroke, volume, patch, strip) | GP workflows. |
| **Modeling Tools** | Sorcar-style procedural modeling utilities | Edit-mode procedural alternatives. |
| **Layout Tools** | Raycast scattering, decal system, hierarchical prefab system | Scene layout and dressing. |
| **Generators** | Architecture (house, bridge, rail, building, street, castle, tower, factory, warehouse), Landscape (stones, rocks, boulders, pillars, cliffs, terrain, water, scattering), Vehicles (wheel/steering/rolling rigs, chassis randomizer, suspension, traffic system), Sci-Fi (weapons, ships, suits, buildings, space structures), Characters (anatomy, clothes, crowds), Flora/Fauna (biomes, 2/4/6/8-legged) | **The ultimate T3 assets.** "Generate a castle on this hill" → one asset with parameters. |

#### Shader Node Groups

| Category | Assets | Orchestrator Use |
|----------|--------|-----------------|
| **Procedural Materials** | Wood, metal, stone, fabric, glass, emission, SSS, toon | Replaces material templates. "Make it look like worn copper" → search assets → apply with parameters. |
| **Utility Nodes** | Mapping, color correction, normal tools, UV tools | Building blocks for custom materials. |

#### Compositor Node Groups

| Category | Assets | Orchestrator Use |
|----------|--------|-----------------|
| **Color Grading** | Lift/gamma/gain, color balance, teal-orange, filmic | Replaces compositor templates. |
| **Effects** | Glare, bloom, vignette, chromatic aberration, lens distortion, denoise | "Add bloom and vignette" → apply node groups. |
| **Full Looks** | Cinematic, vintage, cyberpunk, noir, dream | High-level: "Give this a cyberpunk look" → one node group. |

### 18.4 Asset-Aware Planning

The orchestrator's planner model receives asset availability as part of the context:

```
Available in your asset library:
  [GN] Animation Preset — Drive any animation with easing (T2)
  [GN] Circular Array — Arrange objects in circle with layers (T2)
  [GN] Camera Handheld — Add realistic camera shake presets (T2)
  [GN] Text Preset: Typewriter — Animated typewriter text effect (T3)
  [SHADER] Worn Copper — Procedural copper with patina (T2)
  [COMP] Cinematic Look — Full cinematic color grade (T3)
  ...
```

The model learns to:
- **Prefer assets over code templates** when an asset exists for the intent
- **Chain assets together** — Animation Preset + Circular Array + Camera Handheld = animated turntable with shake
- **Fall back to code templates** when no asset matches
- **Suggest asset creation** — "I don't have a 'worn copper' material, but I can make one. Want me to save it as an asset?"

### 18.5 Asset → Template Bridge

When the orchestrator uses an asset, it generates a template that:

1. **Loads the asset** from the library (by name or catalog path)
2. **Applies it** to the target object (as modifier, material, node group, etc.)
3. **Configures parameters** based on user intent
4. **Names everything** meaningfully

```python
def _tmpl_apply_gn_asset(params=None):
    """Template: apply_gn_asset — Apply a Geometry Nodes asset from the library.
    
    This template bridges the asset library with the template system.
    Instead of generating GN nodes from scratch, it loads a pre-built asset.
    """
    p = dict(_TEMPLATE_DEFAULTS)
    if params: p.update(params)
    return (
        'import bpy\n'
        'obj = bpy.context.active_object\n'
        '# Find the asset in the library\n'
        'asset_name = "{asset_name}"\n'
        'mod = obj.modifiers.new(name=asset_name, type="NODES")\n'
        '# Load the node group from the asset library\n'
        'ng = bpy.data.node_groups.get(asset_name)\n'
        'if ng:\n'
        '    mod.node_group = ng\n'
        '    # Configure exposed parameters\n'
        '    mod["{input_1}"] = {value_1}\n'
        '    mod["{input_2}"] = {value_2}\n'
    ).format(**p)
```

### 18.6 Deduplication: Assets vs Code Templates

**Rule: If a procedural asset exists that does the same thing as a code template, prefer the asset.**

| Code Template | Equivalent Asset | Decision |
|---------------|-----------------|----------|
| `arrange_in_circle` | Circular Array (GN) | **Use asset** — more powerful (layers, randomization, curve deformation) |
| `add_array` | Grid/Linear Array (GN) | **Use asset** — more powerful (gizmos, instance collections, randomization) |
| `camera_setup_cinematic` | Camera Handheld + A→B Move (GN) | **Use assets** — more flexible, composable |
| `light_three_point` | (No equivalent asset yet) | **Use code template** — but flag as "would benefit from an asset" |
| `principled_basic` | Worn Copper / procedural materials (SHADER) | **Use asset if matching description**, fall back to code template |
| `comp_setup_cinematic` | Cinematic Look (COMP) | **Use asset** — pre-built, tested, artist-curated |

The orchestrator's plan validator checks for asset equivalents before falling back to code templates. This keeps the code template library lean and avoids redundant maintenance.

### 18.7 The Asset + Template Synergy

The most powerful workflows combine procedural assets with code templates:

```
User: "Make a product turntable with a handheld camera feel"
  → Orchestrator:
      1. [ASSET] Circular Array — arrange products in circle
      2. [ASSET] Animation Preset — drive 360° rotation with easing
      3. [ASSET] Camera Handheld — add "interview" preset shake
      4. [TEMPLATE] light_product — setup product lighting
      5. [TEMPLATE] render_setup_turntable — configure render output
  → Result: 5 operations, 3 assets + 2 templates, 1 round-trip
```

**The orchestrator doesn't care whether a step is an asset or a code template.** Both are just named operations with parameters. The asset library is an extension of the template library, and the template library fills gaps the asset library doesn't cover.

### 18.8 Dependency on Tier 3d

This asset-first strategy depends on **Tier 3d** (asset browser visibility and tooling), which provides:

- `search_assets` — find assets by name, tag, catalog
- `get_asset_tags` — detailed metadata including editor type
- `load_asset_in_context` — apply asset to current selection
- `list_asset_catalogs` — browse available asset categories

Without Tier 3d, the orchestrator can still use code templates but loses the asset-first advantage. **Tier 3d should be completed before or in parallel with Tier 3g Milestone B (templates).**

---

## 19. Benchmarks & Development Roadmap

### 18.1 Why New Benchmarks

The current benchmark (stonehenge) is a single T2 test. We need a comprehensive benchmark suite that tests:

- **All three tiers** (T1 atomic, T2 contextual, T3 scene-level)
- **All major editors** (VIEW_3D, NODE_EDITOR, SEQUENCE_EDITOR, DOPESHEET, etc.)
- **Multi-editor pathways** (VIEW_3D → NODE_EDITOR, VIEW_3D → SEQUENCE_EDITOR, etc.)
- **Local model capability boundaries** (what works reliably, what needs remote)
- **Regression prevention** (new templates shouldn't break old ones)

### 18.2 Benchmark Suite

#### T1 Benchmarks (Atomic Operations)

| ID | Test | Editor | Mode | Expected Result | Current | Target |
|----|------|--------|------|----------------|---------|--------|
| `T1-01` | Create a cube at origin | VIEW_3D | OBJECT | Cube at (0,0,0) named "Cube" | N/A | 1 attempt |
| `T1-02` | Subdivide selected mesh 2 times | VIEW_3D | EDIT_MESH | Mesh subdivided, vertex count 4× | N/A | 1 attempt |
| `T1-03` | Add Subsurf modifier level 2 | VIEW_3D | OBJECT | Subsurf modifier added, levels=2 | N/A | 1 attempt |
| `T1-04` | Apply rotation and scale | VIEW_3D | OBJECT | Rotation=(0,0,0), Scale=(1,1,1) | N/A | 1 attempt |
| `T1-05` | Set smooth shading with 30° auto-smooth | VIEW_3D | OBJECT | Smooth shading on, auto_smooth_angle=30° | N/A | 1 attempt |
| `T1-06` | Keyframe location at frame 10 | VIEW_3D | OBJECT | Keyframe at frame 10 with current location | N/A | 1 attempt |
| `T1-07` | Create a point light at (5,5,5) | VIEW_3D | OBJECT | POINT light at (5,5,5), energy=1000 | N/A | 1 attempt |
| `T1-08` | Add Principled BSDF material | NODE_EDITOR | — | New material with Principled BSDF, named | N/A | 1 attempt |

#### T2 Benchmarks (Contextual Operations)

| ID | Test | Editors | Expected Result | Current | Target |
|----|------|---------|----------------|---------|--------|
| `T2-01` | Arrange 8 cubes in a circle | VIEW_3D | 8 cubes evenly spaced on circle radius 5 | N/A | 1 attempt |
| `T2-02` | Three-point lighting on selected object | VIEW_3D | Key, fill, rim lights positioned around selection | N/A | 1 attempt |
| `T2-03` | Clean mesh for 3D printing | VIEW_3D | Transforms applied, normals recalculated, doubles removed, solidify added | N/A | 1 attempt |
| `T2-04` | Organize outliner by type | VIEW_3D → OUTLINER | Objects renamed with type prefix, collections color-tagged, orphan data purged | N/A | 1 attempt |
| `T2-05` | Apply material by object name pattern | VIEW_3D | Objects with "wood" in name get wood material, "metal" get metal | N/A | 1 attempt |
| `T2-06` | Frame camera on selection with DOF | VIEW_3D → PROPERTIES | Camera framed on selection, f-stop=2.8, focus on active | N/A | 1 attempt |
| `T2-07` | Setup PBR material from description | NODE_EDITOR | "Glossy red plastic" → Principled BSDF with roughness=0.3, base=(0.8,0.1,0.1) | N/A | 1-2 attempts |
| `T2-08` | Add movie strip with fade in/out | SEQUENCE_EDITOR | Movie strip added, 1s fade in, 1s fade out | N/A | 1 attempt |
| `T2-09` | Scatter rocks on terrain | VIEW_3D → NODE_EDITOR | GN modifier added, points distributed, rock assets instanced | N/A | 1-2 attempts |
| `T2-10` | Setup walk cycle on armature | VIEW_3D → DOPESHEET | 24-frame walk cycle with contact/down/passing/up poses | N/A | 2-3 attempts |

#### T3 Benchmarks (Scene-Level Operations)

| ID | Test | Editors | Expected Result | Current | Target |
|----|------|---------|----------------|---------|--------|
| `T3-01` | Cinematic look from scratch | VIEW_3D → PROPERTIES → NODE_EDITOR | Cycles, 1920×1080, three-point lights, camera DOF, compositor glare+balance+vignette | N/A | 1-2 attempts |
| `T3-02` | Product shot setup | VIEW_3D → PROPERTIES | Backdrop, product lighting, 45° camera, 85mm, DOF, render settings | N/A | 1-2 attempts |
| `T3-03` | Scene audit and optimize | ALL | Report of data-block counts, orphan data, material complexity, optimization suggestions | N/A | 1-2 attempts |
| `T3-04` | Shot to master edit | VIEW_3D → SEQUENCE_EDITOR | Render viewport, add to VSE, position, add text overlay | N/A | 1-2 attempts |
| `T3-05` | Character turntable setup | VIEW_3D → PROPERTIES → DOPESHEET | Empty at origin, camera parented, 360° over 120 frames, three-point lights, PNG output | N/A | 1-2 attempts |

#### Multi-Editor Pathway Benchmarks

| ID | Test | Pathway | Expected Result | Current | Target |
|----|------|---------|----------------|---------|--------|
| `ME-01` | Model → Shade | VIEW_3D(EDIT) → VIEW_3D(OBJECT) → NODE_EDITOR | Edit mesh, exit edit, create material, setup nodes | N/A | 1-2 attempts |
| `ME-02` | Sculpt → Retopo → UV | VIEW_3D(SCULPT) → VIEW_3D(EDIT) → UV_EDITOR | Remesh, edit mode, unwrap, pack islands | N/A | 2-3 attempts |
| `ME-03` | Animate → Render | VIEW_3D → DOPESHEET → PROPERTIES | Keyframe, tune curves, set render engine, set output | N/A | 1-2 attempts |
| `ME-04` | Comp → VSE | NODE_EDITOR → VIEW_3D → SEQUENCE_EDITOR | Setup compositor, render, add to VSE | N/A | 1-2 attempts |
| `ME-05` | Assets → Scene → Organize | ASSET_BROWSER → VIEW_3D → OUTLINER | Search assets, place in scene, organize outliner | N/A | 2-3 attempts |

### 18.3 Per-Editor Benchmark Suites

Each editor gets its own focused benchmark suite to validate template coverage:

| Editor | # Benchmarks | Focus |
|--------|-------------|-------|
| VIEW_3D (OBJECT) | 10 | Primitives, modifiers, materials, lighting, camera, organization |
| VIEW_3D (EDIT_MESH) | 8 | Extrude, bevel, loop cut, subdivide, merge, bridge, normals, separate |
| VIEW_3D (SCULPT) | 4 | Remesh, dyntopo, mask, face sets |
| NODE_EDITOR (Shader) | 8 | PBR, glass, emission, SSS, noise, voronoi, image texture, organize |
| NODE_EDITOR (Geometry) | 6 | Scatter, instance, boolean, array, curve to mesh, capture attribute |
| NODE_EDITOR (Compositor) | 6 | Glare, color balance, lens distortion, blur, vignette, cinematic |
| SEQUENCE_EDITOR | 6 | Movie strip, sound strip, split, trim, transition, speed |
| DOPESHEET_EDITOR | 5 | Keyframe, interpolation, modifier, bake, NLA |
| UV_EDITOR | 4 | Unwrap, pack, stitch, align |
| TEXT_EDITOR | 6 | Addon skeleton, operator, panel, modal, bmesh, IO script |
| OUTLINER | 5 | Sort, rename, color tag, group, purge |

**Total: ~68 per-editor benchmarks + 20 tier benchmarks + 5 pathway benchmarks = ~93 benchmarks**

### 18.4 Development Roadmap: Branches & Milestones

#### Tier 3g Scope (This Release — 1-2 Months)

Tier 3g ships the orchestrator, static templates, operator UI, and benchmarks. The recorder/macro system is deferred to Tier 4e.

#### Milestone A: Foundation (Tier 3g-A) — "The Wires Connect"

**Branch**: `tier3g-a-foundation`
**Duration**: 1-2 weeks
**Goal**: Wire existing modules, context probe, editor scoping

| Task | File(s) | Est. |
|------|---------|------|
| Wire autofix into execute_blender_code | `mcp_to_blender_server.py` | 1h |
| Add template metadata to existing 17 templates | `blender_templates.py` | 1h |
| Create `_probe_context()` in bridge server | `mcp_to_blender_server.py` | 2h |
| Add context injection to system prompt | `agent_controller.py` | 1h |
| Create editor → tool mapping registry | `editor_context.py` (new) | 3h |
| Filter tool list by editor context | `agent_controller.py` | 2h |
| Add `get_editor_context` MCP tool | `mcp/blmcp/tools/` | 1h |
| Tests for all of the above | `tests/` | 3h |

**Deliverable**: Model sees 5-8 tools instead of 30+. Context is injected automatically.

#### Milestone B: Template Core (Tier 3g-B) — "The Library Opens"

**Branch**: `tier3g-b-templates`
**Duration**: 2-3 weeks
**Goal**: Build out the P0 template library (~80 templates), asset-aware

| Task | File(s) | Est. |
|------|---------|------|
| T1 templates: Edit Mode (20) | `plugins/core/edit_mesh.py` | 4h |
| T1 templates: Primitives (9) | `plugins/core/primitives.py` | 2h |
| T1 templates: Modifiers (12) | `plugins/core/modifiers.py` | 3h |
| T2 templates: Shader Editor (18) | `plugins/core/materials.py` | 4h |
| T2 templates: Outliner (12) | `plugins/core/outliner.py` | 3h |
| T2 templates: Lighting (8) | `plugins/core/lighting.py` | 2h |
| T2 templates: Camera (6) | `plugins/core/camera.py` | 2h |
| T2 templates: Animation (16) | `plugins/core/animation.py` | 4h |
| T2 templates: Rendering (10) | `plugins/core/rendering.py` | 3h |
| T2 templates: Compositor (14) | `plugins/core/compositing.py` | 3h |
| Asset bridge templates (apply GN/shader/comp assets) | `plugins/core/assets.py` | 3h |
| T3 chains: cinematic, product, archviz, turntable, audit | `orchestrator.py` | 3h |
| Template tests (all 80+) | `tests/test_templates.py` | 6h |

**Deliverable**: 80+ tested templates covering P0 documentation areas. Asset-aware planning.

#### Milestone C: Orchestrator (Tier 3g-C) — "The Brain"

**Branch**: `tier3g-c-orchestrator`
**Duration**: 2-3 weeks
**Goal**: Full orchestrator pipeline with tier-aware planning

| Task | File(s) | Est. |
|------|---------|------|
| Intent classifier with keyword + regex | `orchestrator.py` | 3h |
| Plan builder (model-assisted template selection) | `orchestrator.py` | 4h |
| Plan validator (params, modes, tiers, data-blocks) | `orchestrator.py` | 3h |
| Template chain composer (TEMPLATE_CHAINS) | `orchestrator.py` | 2h |
| Execution engine (render → autofix → preflight → exec) | `orchestrator.py` | 4h |
| Fallback ladder implementation | `orchestrator.py` | 2h |
| Undo management (push before destructive) | `orchestrator.py` | 1h |
| Mode auto-switching between plan steps | `orchestrator.py` | 2h |
| Tier composition rules enforcement | `orchestrator.py` | 2h |
| Multi-editor pathway resolution | `orchestrator.py` | 4h |
| Editor switching protocol | `orchestrator.py` | 2h |
| Asset-aware planning (prefer assets over code templates) | `orchestrator.py` | 2h |
| Orchestrator tests | `tests/test_orchestrator.py` | 4h |

**Deliverable**: Full orchestrator pipeline. Model describes intent, server executes.

#### Milestone D: Operator UI (Tier 3g-D) — "The Buttons"

**Branch**: `tier3g-d-operators`
**Duration**: 1-2 weeks
**Goal**: Contextual operator buttons in menus and sidebar

| Task | File(s) | Est. |
|------|---------|------|
| Operator base class + registration | `operators/__init__.py` | 2h |
| Organize operators | `operators/coworker_organize.py` | 2h |
| Mesh operators | `operators/coworker_mesh.py` | 2h |
| Material operators | `operators/coworker_material.py` | 2h |
| Lighting operators | `operators/coworker_lighting.py` | 2h |
| Camera operators | `operators/coworker_camera.py` | 2h |
| Scene operators | `operators/coworker_scene.py` | 2h |
| Node operators | `operators/coworker_nodes.py` | 2h |
| Animation operators | `operators/coworker_animation.py` | 2h |
| Render operators | `operators/coworker_render.py` | 2h |
| Text Editor operators | `operators/coworker_text_editor.py` | 2h |
| Contextual sidebar panel | `ui_sidebar.py` (new) | 3h |
| Context menu extensions | `ui_context_menu.py` (new) | 2h |
| Menu integration (Object, Mesh, Add, Render, Node, etc.) | `__init__.py` | 3h |

**Deliverable**: One-click buttons for 60% of common tasks. Contextual sidebar.

#### Milestone E: Benchmarks & Polish (Tier 3g-E) — "The Proof"

**Branch**: `tier3g-e-benchmarks`
**Duration**: 1-2 weeks
**Goal**: Comprehensive benchmark suite, documentation, release prep

| Task | File(s) | Est. |
|------|---------|------|
| T1 benchmarks (8 tests) | `tests/benchmarks/` | 2h |
| T2 benchmarks (10 tests) | `tests/benchmarks/` | 3h |
| T3 benchmarks (5 tests) | `tests/benchmarks/` | 2h |
| Multi-editor pathway benchmarks (5 tests) | `tests/benchmarks/` | 2h |
| Per-editor benchmarks (68 tests) | `tests/benchmarks/` | 8h |
| Benchmark runner + reporting | `tests/run_benchmarks.py` | 3h |
| Plugin developer documentation | `docs/plugins.md` | 3h |
| User guide updates | Wiki | 4h |
| Release notes | `CHANGELOG.md` | 2h |

**Deliverable**: 93 benchmarks passing. Full documentation. Release-ready.

### 18.5 Milestone Dependency Graph

```
Milestone A (Foundation)
    ↓
Milestone B (Templates) ←── Can start in parallel with A's later tasks
    ↓                         ↑
Milestone C (Orchestrator)    Tier 3d (Asset Browser) — complete before B
    ↓
┌───────────┬───────────┐
↓           ↓           ↓
Milestone D  Milestone E  (parallel)
(Operators)  (Benchmarks)
│           │
└───────────┘
    ↓
Release Tier 3g
    ↓
Tier 4e: Recorder & Macros (deferred)
```

Milestones D and E can run in parallel after C is complete.

### 18.6 Total Estimated Effort (Tier 3g)

| Milestone | Weeks | New Files | Modified Files |
|-----------|-------|-----------|----------------|
| A: Foundation | 1-2 | 1 | 3 |
| B: Templates | 2-3 | 15 | 2 |
| C: Orchestrator | 2-3 | 1 | 2 |
| D: Operators | 1-2 | 12 | 2 |
| E: Benchmarks | 1-2 | 20+ | 2 |
| **Total Tier 3g** | **7-12 weeks** | **49+** | **11** |

### 18.7 Deferred to Tier 4

| Item | Target Tier | Rationale |
|------|------------|-----------|
| Full Action Recorder (Before/After diff, monkey-patching, modal recording) | **Tier 4e** | Complex subsystem, depends on stable orchestrator |
| Macro Library (save/load/index, user macros as plugins) | **Tier 4e** | Depends on recorder |
| "Teach Me" workflow | **Tier 4e** | Depends on recorder + macro library |
| LLM-assisted macro refinement | **Tier 4e** | Depends on macro library |
| Macro marketplace | **Tier 5** | Community feature, needs critical mass |
| Plugin community marketplace | **Tier 5** | Community feature |
| Full Grease Pencil templates | **Tier 4** | Lower priority, needs GP-specific testing |
| Full Motion Tracking templates | **Tier 4** | Lower priority, niche use case |
| Procedural generator assets (architecture, landscape, vehicles, characters, flora/fauna) | **Tier 4-5** | These are asset creation tasks, not template tasks. The orchestrator will *use* them once they exist. |

### 18.8 Tier 3g Release Criteria

To ship Tier 3g, the following must be true:

1. ✅ Model sees 5-8 tools per editor (not 30+)
2. ✅ Context is injected automatically (editor, mode, selection, scene summary)
3. ✅ 80+ templates covering P0 documentation areas
4. ✅ Orchestrator pipeline: intent → plan → validate → execute → report
5. ✅ Fallback ladder: template chain → single template → raw code → doc search → clarify
6. ✅ Tier composition rules enforced (T1+T1→T2, T2+T2→T3, T3 is terminal)
7. ✅ Multi-editor pathways resolved automatically
8. ✅ Asset-aware planning (prefer assets over code templates when available)
9. ✅ Contextual operator buttons in sidebar and menus
10. ✅ 93 benchmarks passing
11. ✅ First-attempt success rate ≥ 80% on T1/T2 benchmarks
12. ✅ First-attempt success rate ≥ 60% on T3 benchmarks

---

## 20. Implementation Plan

### Phase 1: Wire Auto-fix + Templates (1-2 hours) — Foundation

**Goal**: Make the existing modules actually work end-to-end.

1. Wire `autofix._autofix_code()` into `execute_blender_code` in `mcp_to_blender_server.py`
2. Add import for `autofix` module in the bridge server
3. Add tests for `autofix` and `blender_templates`
4. Verify all 70+ tests pass
5. Add template metadata (editor, mode, data_blocks) to existing 17 templates

### Phase 2: Context Probe (2-3 hours) — Awareness

**Goal**: The MCP server knows what the user is doing.

1. Create `_probe_context()` function in bridge server
2. Add context injection to system prompt before each model call
3. Include: active editor, mode, object type, selection count, scene summary
4. Keep context under 200 tokens
5. Add `get_editor_context` MCP tool for model to query

### Phase 3: Editor-Aware Tool Scoping (3-4 hours) — Focus

**Goal**: Show 5-8 tools instead of 30+.

1. Create editor → tool mapping registry (all 20+ editors)
2. Filter tool list before sending to model based on `_probe_context()`
3. Add `editor_context` to tool descriptions
4. Update system prompt to mention editor context
5. Test: model in Shader Editor should not see mesh creation tools

### Phase 4: Template Expansion — Core (4-6 hours) — Coverage

**Goal**: Cover 80% of P0 documentation areas.

Priority templates to add (in order):
1. Edit Mode operations (extrude, bevel, loop cut, subdivide, merge, bridge, fill, normals) — 8 templates
2. Shader Editor (glass, emission, metallic, SSS, toon, noise, voronoi, brick, gradient, image_texture, color_ramp, mapping, tex_coord, frame, mix, organize) — 16 templates
3. Outliner (rename, sort × 4, color_tag, group, purge, organize, hierarchy, instance, link) — 12 templates
4. Lighting (three_point, studio, hdri, rim, product, outdoor, night, match_reference) — 8 templates
5. Camera (frame_selected, frame_from_angle, dolly_zoom, dof, track_to, cinematic) — 6 templates
6. Animation (keyframe × 4, interpolation, easing, fcurve_modifier, driver, bake, nla × 2, cycle, sound_to_fcurve, retime, walk_cycle) — 16 templates
7. Rendering (engine, resolution, output, world, hdri, eevee, cycles, turntable, batch, view_layer) — 10 templates
8. Compositor (glare, color_balance, lens_distortion, blur, sharpen, mix, color_correction, viewer, render_layers, composite, vignette, chromatic, denoise, cinematic) — 14 templates

**Total new templates: ~90**

### Phase 5: Orchestrator Engine (4-6 hours) — Intelligence

**Goal**: Server chains operations, model describes intent.

1. Create `orchestrator.py` with the full pipeline:
   - `classify_intent()` — keyword + regex matching
   - `build_plan()` — model-assisted template selection
   - `validate_plan()` — parameter and mode checking
   - `execute_plan()` — render → autofix → preflight → execute
2. Create `TEMPLATE_CHAINS` for common multi-step operations
3. Implement the fallback ladder
4. Add undo management (push before destructive ops)
5. Add mode auto-switching between plan steps

### Phase 6: Operator UI (3-4 hours) — Accessibility

**Goal**: One-click buttons for common tasks.

1. Create `operators/` directory with operator modules:
   - `coworker_organize.py` — Outliner organization
   - `coworker_mesh.py` — Mesh cleanup
   - `coworker_material.py` — Material assignment
   - `coworker_lighting.py` — Lighting setup
   - `coworker_camera.py` — Camera framing
   - `coworker_scene.py` — Scene setup
   - `coworker_nodes.py` — Node tree operations
   - `coworker_animation.py` — Animation helpers
2. Register operators in Blender menus (Object, Mesh, Add, Render, Node, Text Editor)
3. Add contextual sidebar panel that shows relevant operators
4. Add right-click context menu extensions

### Phase 7: Text Editor Agent (2-3 hours) — Code

**Goal**: First-class code generation in the Text Editor.

1. Add Text Editor-specific templates (addon skeleton, operator, panel, modal, property group, menu, bmesh, node tree, IO, keymap, handler, gizmo, render script)
2. Add "Explain Selection" and "Fix API Errors" operators
3. Add "Generate Addon from Description" guided operator
4. Wire text buffer context into the orchestrator

### Phase 8: Plugin System (2-3 hours) — Extensibility

**Goal**: Community can add templates without touching core code.

1. Create `plugins/` directory structure
2. Implement `CoworkerPlugin` base class
3. Implement plugin discovery
4. Migrate existing templates to core plugins
5. Document plugin API

### Phase 9: Template Expansion — Extended (ongoing)

**Goal**: Reach 80% coverage across all documentation areas.

1. Physics templates (cloth, soft_body, rigid_body, collision, particles, fluid, force_field, bake) — 8 templates
2. Geometry Nodes (scatter, instance, boolean, array, curve_to_mesh, set_material, transform, delete, capture, extrude, subdivide, mesh_to_volume, volume_to_mesh, store_attribute) — 14 templates
3. VSE (movie, sound, image, color, text, effect, split, trim, transition, speed, fade, proxy) — 12 templates
4. Sculpting (remesh_voxel, remesh_quadriflow, dyntopo, mask_cavity, mask_expand, face_sets, symmetry, apply_base) — 8 templates
5. Grease Pencil (TBD)
6. Motion Tracking (TBD)

---

## 21. Technical Feasibility Review

### 16.1 Can Local Models Actually Do This?

**Short answer: Yes, if we design for their strengths.**

| Task | Local Model Capability | Our Strategy |
|------|----------------------|--------------|
| Intent classification | **Strong** — 7B+ models classify text well | Keyword pre-filter + model confirmation |
| Template selection | **Strong** — choosing from 5-8 options is easy | Editor-scoped tool lists |
| Parameter filling | **Moderate** — needs clear defaults | Rich defaults in `_TEMPLATE_DEFAULTS` |
| Code generation | **Weak** — hallucinates APIs | Don't ask model to write code |
| Multi-step planning | **Weak** — loses track after 2-3 steps | Pre-built chains, model only picks the chain |
| Creative judgment | **Surprisingly good** — aesthetic sense | Use for lighting, camera, composition decisions |
| API knowledge | **Very weak** — guesses wrong | Templates are pre-tested, auto-fix catches mistakes |

### 16.2 The Key Design Decisions

1. **Model never writes Python.** It picks templates and fills parameters. The server generates the code.
2. **Model sees 5-8 tools, not 30+.** Editor-aware scoping eliminates decision paralysis.
3. **Context is injected, not requested.** The model doesn't need to know to ask for scene state.
4. **Chains are pre-built for common tasks.** "Make it cinematic" is one decision, not 10.
5. **Fallback ladder ensures graceful degradation.** If templates don't cover it, raw code execution is still available.

### 16.3 What Local Models Will Still Struggle With

| Challenge | Mitigation |
|-----------|------------|
| Very novel requests ("make a working Rube Goldberg machine") | Fall back to raw code execution with preflight |
| Extremely specific API calls | Auto-fix catches common mistakes; doc search helps |
| Long multi-turn conversations | Context window management, summarize history |
| Understanding complex scene state | Inject structured summary, not raw data |
| Version-specific API differences | Skills system provides version-aware guidance |

### 16.4 Remote vs Local Model Comparison

| Aspect | Remote (Claude/GPT-4) | Local (Qwen 27B / Llama 14B) |
|--------|----------------------|------------------------------|
| Intent classification | Near-perfect | Good (90%+) |
| Template selection | Excellent | Good (85%+) |
| Parameter filling | Excellent | Good with defaults |
| Creative judgment | Excellent | Good |
| Code generation (fallback) | Excellent | Mediocre (but rarely needed) |
| Speed | 1-3s latency | 0.5-2s latency (GPU) |
| Cost | Per-token | Free |
| Privacy | Data leaves machine | All local |

**Bottom line: The orchestrator architecture makes local models viable for ~85% of tasks. Remote models still win for the most complex 15%, but the gap is much smaller than with raw code generation.**

### 16.5 Will This Really Make the System More Useful?

**Yes, dramatically.** The current system requires the model to be a Blender Python expert. The target system requires the model to understand English and pick from a menu. This is the difference between:

- "Write a Python script that creates 8 torus objects arranged in a circle with stone materials" → **Model fails 70% of the time**
- "The user wants a stonehenge. Available templates: create_torus, arrange_in_circle, add_material. Which do you use?" → **Model succeeds 90%+ of the time**

The orchestrator doesn't make the model smarter. It makes the task easier. And that's the whole point.

---

## 22. Success Criteria

### 17.1 Benchmark Tests

| Test | Current Score | Target Score |
|------|---------------|--------------|
| Create 8 torus pillars in circle | 6+ attempts | 1 attempt |
| Add materials to objects | 4+ attempts | 1 attempt |
| Set up three-point lighting | 3+ attempts | 1 attempt |
| Apply modifier chain | 5+ attempts | 1 attempt |
| Keyframe animation | 4+ attempts | 1 attempt |
| Stonehenge full scene | 10+ attempts | 2-3 attempts |
| Organize outliner (50 objects) | N/A (not possible) | 1 click |
| Generate Blender addon from description | N/A (not possible) | 1-2 attempts |
| Setup cinematic look | N/A (not possible) | 1 attempt |
| Clean up scene (purge, rename, sort) | N/A (not possible) | 1 click |

### 17.2 Metrics

| Metric | Current | Target |
|--------|---------|--------|
| First-attempt success rate | ~30% | ~80% |
| Average round-trips per task | 5 | 1.5 |
| Average tokens per task | 3000 | 400 |
| Hard crashes per session | 1-2 | 0 |
| User intervention required | Often | Rarely |
| Tasks possible without typing | 0% | 60% |
| Template coverage of Blender manual | ~10% | 80% (P0 areas) |

### 17.3 User Experience Goals

| Goal | How We Achieve It |
|------|-------------------|
| Artist doesn't need to know Python | Templates handle all code generation |
| Artist doesn't need to know Blender API | Auto-fix + preflight catch mistakes silently |
| Artist doesn't need to type for common tasks | Contextual operator buttons |
| Artist can explore freely with chat | Chat panel for novel requests |
| Artist's work is never lost | Undo push before every destructive operation |
| Artist can learn from the system | "Learn more" links to documentation |
| System works offline | All local — no API keys required |
| System respects privacy | No data leaves the machine in local mode |

### 17.4 The Vision

An artist opens Blender. The Coworker sidebar shows contextual buttons based on what they're doing:

- **Modeling a character?** Buttons for: Clean Mesh, Add Subsurf, Mirror Modifier, Symmetrize
- **Setting up a scene?** Buttons for: Three-Point Light, Frame Camera, HDRI Environment
- **In the Shader Editor?** Buttons for: PBR Setup, Glass Material, Organize Nodes
- **In the Text Editor?** Buttons for: New Addon, New Operator, Explain Code, Fix API Errors
- **Always available:** Organize Scene, Clean Up, Chat with Coworker

They click **Organize Outliner** and their scene is instantly tidy — objects renamed by type, collections color-tagged, orphan data purged.

They type **"make it look cinematic"** and the lighting, camera, depth of field, and compositing are set up in one shot.

They select a mesh and click **"Prepare for 3D Print"** — transforms applied, normals recalculated, doubles removed, solidify added.

The model does not write Python. The model understands intent. The server understands Blender. Together, they make the artist's dream come true.

---

## Appendix A: File Structure (Target)

```
addon/bfa_coworker/
├── __init__.py                  # Thin registration hub
├── shared.py                    # Shared constants, port management
├── preferences.py               # AddonPreferences
├── agent_controller.py          # Conversation loop, MCP server management
├── mcp_to_blender_server.py     # TCP bridge server (add context probe)
├── blender_templates.py         # Template registry (135+ templates)
├── autofix.py                   # Auto-correction rules (12+ rules)
├── orchestrator.py              # NEW: Intent → Plan → Execute pipeline
├── editor_context.py            # NEW: Editor detection + context gathering
├── data_block_registry.py       # NEW: Data block type system
├── mode_registry.py             # NEW: Editor mode awareness
├── plugin_base.py               # NEW: CoworkerPlugin base class
├── llm_manager.py               # LLM lifecycle
├── ui_chat.py                   # Chat panel UI
├── ui_sidebar.py                # NEW: Contextual sidebar panel
├── ui_context_menu.py           # NEW: Right-click menu extensions
├── operators/
│   ├── __init__.py
│   ├── coworker_organize.py     # Outliner organization operators
│   ├── coworker_mesh.py         # Mesh cleanup operators
│   ├── coworker_material.py     # Material assignment operators
│   ├── coworker_lighting.py     # Lighting setup operators
│   ├── coworker_camera.py       # Camera framing operators
│   ├── coworker_scene.py        # Scene setup operators
│   ├── coworker_nodes.py        # Node tree operators
│   ├── coworker_animation.py    # Animation operators
│   ├── coworker_render.py       # Render setup operators
│   └── coworker_text_editor.py  # Text editor operators
├── plugins/
│   ├── __init__.py              # Plugin discovery
│   ├── _base.py                 # CoworkerPlugin base class
│   └── core/
│       ├── primitives.py        # Mesh primitives (9 templates)
│       ├── edit_mesh.py         # Edit mode operations (20 templates)
│       ├── modifiers.py         # Modifier templates (12 templates)
│       ├── materials.py         # Material/shader templates (18 templates)
│       ├── lighting.py          # Lighting templates (8 templates)
│       ├── camera.py            # Camera templates (6 templates)
│       ├── animation.py         # Animation templates (16 templates)
│       ├── compositing.py       # Compositor templates (14 templates)
│       ├── geometry_nodes.py    # GN templates (14 templates)
│       ├── vse.py               # Video sequencer templates (12 templates)
│       ├── outliner.py          # Outliner/scene management (12 templates)
│       ├── text_editor.py       # Text editor templates (14 templates)
│       ├── physics.py           # Physics templates (8 templates)
│       ├── rendering.py         # Rendering templates (10 templates)
│       └── sculpting.py         # Sculpting templates (8 templates)
├── skills/
│   └── __init__.py              # Version-aware skills system
└── gen_plugins/
    └── __init__.py              # Generative plugin foundation

mcp/blmcp/
├── __init__.py                  # FastMCP server setup
├── tools/
│   ├── execute_blender_code.py  # Updated with orchestrator integration
│   ├── execute_blender_plan.py  # Plan → template chain execution
│   ├── get_editor_context.py    # NEW: Context probe tool
│   ├── list_templates.py        # NEW: List available templates
│   └── ... (existing tools)
└── data/
    ├── prompts.yml              # System prompt
    ├── api/                     # API reference
    └── manual/                  # User manual search index

tests/
├── test_templates.py            # Template rendering tests
├── test_orchestrator.py         # Orchestrator pipeline tests
├── test_autofix.py              # Auto-fix tests
├── test_preflight.py            # Preflight tests
└── test_plugins.py              # Plugin discovery tests
```

## Appendix B: Comparison Tables

### B.1 Current vs Target Architecture

| Aspect | Current | Target |
|--------|---------|--------|
| Model's job | Write Python code | Describe intent in English |
| Server's job | Execute code, catch errors | Interpret intent, generate code, execute |
| Tools visible | 30+ always | 5-8 per editor |
| Context | Model must request | Automatically injected |
| Error handling | Reject → retry → spiral | Auto-fix → template → succeed |
| Round-trips per task | 4-8 | 1-2 |
| Token usage per task | 2000-5000 | 200-500 |
| First-attempt success | ~30% | ~80% |
| UI interaction | Chat only | Chat + contextual buttons + menu operators |

### B.2 Tool Complexity Comparison

| Approach | Tools | Model Decision | Failure Modes |
|----------|-------|----------------|---------------|
| Current: flat list | 30+ | Pick 1 of 30 | 29 wrong choices |
| Domain-scoped | 8-12 | Pick 1 of 8 | 7 wrong choices |
| Template-based | 5-8 templates | Pick template + params | 4 wrong choices |
| Server-orchestrated | 1 intent tool | Describe what you want | Near zero |

### B.3 Blender Buddy vs BFA Coworker vs Target

| Feature | Blender Buddy | BFA Coworker (Current) | BFA Coworker (Target) |
|---------|---------------|------------------------|----------------------|
| Model selection | Simple (3 presets) | Complex (9 presets + custom) | Simple (auto-detect VRAM) |
| Tool count | ~10 curated | 30+ | 5-8 per editor |
| Code execution | Direct | Preflight + spiral | Template + auto-fix + orchestrate |
| Scene context | Injected | Model must request | Injected automatically |
| Editor awareness | None | None | Full (20+ editors) |
| Mode awareness | None | None | Full (15+ modes) |
| Data-block awareness | None | None | Full (25+ types) |
| Error recovery | Basic | Spiral detection | Auto-fix → template fallback |
| Asset-first | Yes (built-in assets) | Partial (6 tools) | Yes (server biases toward assets) |
| Operator UI | None | None | Contextual buttons + menus |
| Plugin system | No | No | Yes (community-extensible) |
| Offline capable | No (requires API key) | Yes (local LLM) | Yes (local LLM) |


---

## Known Limitations (Post-Tier 3h Audit)

The Tier 3h quality audit (2026-09-01) compared what this plan promised against what shipped.
The orchestration layer is a **proof-of-concept**, not the full intent-driven architecture below.

### What Was Built vs. Planned

| Component | Planned | Built | Status |
|-----------|---------|-------|--------|
| Templates | 135 across all editors | 18 (VIEW_3D basics) | Minimal |
| Template metadata | tier/editor/mode/creates_datablocks/is_destructive/chainable | None | Missing |
| Template chains | Curated multi-step sequences | None | Missing |
| Intent classifier | Keyword-based routing | None (domain system approximates) | Missing |
| Plan validator | 5 validation checks | None | Missing |
| Complexity tiers | T1/T2/T3 | None | Missing |
| Plugin architecture | plugins/ directory | None | Missing |
| Editor-aware scoping | context.area.type | Domain system (keyword + scene content) | Approximation |
| Fallback ladder | 5 levels | 2 (plan → raw code, spiral detection) | Partial |
| Auto-fix rules | 12 rules wired in | 12 rules wired in (Tier 3h) | Fixed |
| execute_blender_plan / list_blender_templates | Working MCP tools | Working (Tier 3h import fix) | Fixed |

### What Actually Works

1. **`execute_blender_code`** — raw Python through preflight (27 checks) + weak sandbox. Reliable primary path.
2. **Domain system** — keyword + scene-content tool filtering reduces 30+ tools to 5-8 per domain. This is the
   main "orchestration" that helps local models (fewer choices = better decisions).
3. **Spiral detection** (threshold 2), entity tracking, and the 18 working templates cover the most common ops.

### Tier 4 Phase 0 Recommendation

The orchestration gap directly blocks Tier 4c (Text Editor templates) and partially blocks Tier 4b (CHOYA
next-step buttons). A Phase 0 in the Tier 4 master coordination plan should add: template metadata retrofit
(~100 LOC), template expansion to ~50 (~200 LOC: Text Editor + Node Editor + Outliner), and a template chain
system (~100 LOC). See `plan_tier3h_quality_audit.md` §5.4 for details.
