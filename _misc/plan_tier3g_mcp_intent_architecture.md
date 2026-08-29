# BFA Coworker — Tier 3g: MCP Intent Architecture — From Code Writer to Intent Driver

**Date**: 2026-08-28
**Status**: Planning — Not Started
**Depends on**: Tier 3f work (preflight, auto-fix, templates, tool domains)
**Reference**: Blender Buddy v9.13.1 architecture patterns

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

---

## 3. Architecture: MCP as Intent Interpreter

### 3.1 Current Flow (Model Writes Code)

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

### 3.2 Target Flow (Model Describes Intent)

```
User: "Make a stonehenge"
  → Server detects: editor=3D_VIEWPORT, 0 objects
  → Server loads: 6 relevant tools (not 30)
  → Server injects: scene context (empty scene)
  → Model sees: create_torus, add_material, arrange_in_circle, ...
  → Model picks: arrange_in_circle + create_torus
  → Server translates to tested template code
  → Code runs first time
  
  Total: 1-2 round-trips, 200 tokens used
```

### 3.3 The Three Layers

```
┌─────────────────────────────────────────────────────┐
│  Layer 3: Server Orchestration                       │
│  - Chains templates into multi-step operations       │
│  - Handles apply/rename/reorder automatically        │
│  - Manages undo pushes before destructive ops        │
├─────────────────────────────────────────────────────┤
│  Layer 2: Template + Auto-fix                        │
│  - 17+ tested code blocks (expandable)               │
│  - 12 auto-correction rules (expandable)             │
│  - Preflight validation (27 patterns)                │
├─────────────────────────────────────────────────────┤
│  Layer 1: Intent Interpretation                      │
│  - Editor-aware tool scoping                         │
│  - Resource injection (scene state)                  │
│  - Model sees 5-8 tools, not 30+                     │
└─────────────────────────────────────────────────────┘
```

---

## 4. Editor-Aware Tool Scoping

### 4.1 Editor → Tool Mapping

| Editor | Tools Shown | Tools Hidden | Why |
|--------|-------------|--------------|-----|
| **3D Viewport** | create_torus, create_cube, add_material, add_modifier, smooth_shade, arrange_in_circle | sequencer, texture, animation tools | User is modeling/shading |
| **Shader Editor** | setup_pbr_material, add_node, link_nodes, preview_material | mesh, object, animation tools | User is editing materials |
| **Timeline** | keyframe_location, keyframe_rotation, set_fcurve, bake_animation | mesh, material tools | User is animating |
| **Sequencer** | add_strip, split_strip, add_effect, set_strip_speed | mesh, material, animation tools | User is video editing |
| **UV Editor** | unwrap, pack_islands, stitch, select_geometry | mesh creation, animation tools | User is UV mapping |
| **Compositor** | add_compositor_node, link_compositor, render_layer | mesh, object tools | User is compositing |
| **Any Editor** | get_screenshot, get_objects_summary, execute_blender_code | — | Fallback always available |

### 4.2 How Detection Works

The MCP server already has access to `bpy.context.area.type`. This tells us which editor the user is in. The server filters tools before sending to the model.

```python
# Pseudo-logic in MCP server
editor = bpy.context.area.type  # "VIEW_3D", "NODE_EDITOR", etc.

if editor == "VIEW_3D":
    tools = SURFACE_TOOLS + MESH_TOOLS + OBJECT_TOOLS
elif editor == "NODE_EDITOR":
    tools = SURFACE_TOOLS + MATERIAL_TOOLS + NODE_TOOLS
elif editor == "SEQUENCE_EDITOR":
    too
```
---

## 5. Per-Editor Template Registry

### 5.1 3D Viewport (Object Mode)

| Template | What It Does | Params |
|----------|-------------|--------|
| create_cube | Add cube primitive | name, size, location |
| create_uv_sphere | Add UV sphere | name, segments, ring_count |
| create_cylinder | Add cylinder | name, vertices, radius, depth |
| create_cone | Add cone | name, vertices, radius1, radius2, depth |
| create_torus | Add torus | name, major_radius, minor_radius |
| create_plane | Add plane | name, size, location |
| create_monkey | Add Suzanne | name, size, location |
| create_camera | Add camera | name, location, rotation, lens |
| create_light | Add light | name, type, location, energy, color |
| create_empty | Add empty | name, type, display_size |
| duplicate_objects | Duplicate selection | count, offset |
| join_objects | Join selected | (uses selection) |
| set_origin | Set origin | origin_type |
| apply_transform | Apply transforms | location, rotation, scale |
| arrange_in_line | Line up objects | axis, spacing |
| arrange_in_grid | Grid layout | columns, spacing |
| arrange_in_circle | Circle layout | radius, axis |
| add_collection | New collection | name, color_tag |
| randomize_transform | Random offset | location_range, rotation_range |

### 5.2 3D Viewport (Edit Mode)

| Template | What It Does | Params |
|----------|-------------|--------|
| extrude_region | Extrude selection | thickness |
| inset_faces | Inset faces | thickness |
| bevel_edges | Bevel edges | width, segments |
| loop_cut | Add loop cut | number_cuts |
| merge_vertices | Merge vertices | type |
| remove_doubles | Remove doubles | distance |
| recalculate_normals | Recalc normals | inside |
| fill_holes | Fill holes | sides |
| triangulate_mesh | Triangulate | quad_method |
| subdivide_mesh | Subdivide | cuts, smoothness |
| smooth_vertices | Smooth vertices | factor, iterations |
| bridge_edge_loops | Bridge loops | (uses selection) |
| spin_tool | Spin extrude | steps, angle |

### 5.3 Shader Editor

| Template | What It Does | Params |
|----------|-------------|--------|
| principled_basic | Basic principled | base_color, roughness, metallic |
| glass_material | Glass/transmission | color, ior, roughness |
| emission_material | Emissive shader | color, strength |
| metallic_material | Metal material | color, roughness |
| noise_texture | Procedural noise | scale, detail, roughness |
| voronoi_texture | Procedural voronoi | scale, randomness |
| image_texture | Image texture | image_path, projection |
| color_ramp | Color ramp | colors, interpolation |
| mapping_node | Mapping transform | location, rotation, scale |
| texture_coordinate | UV/Object coords | mapping_type |
| frame_nodes | Frame selected nodes | label, color |
| mix_materials | Mix two materials | factor |

### 5.4 Geometry Nodes

| Template | What It Does | Params |
|----------|-------------|--------|
| scatter_on_faces | Scatter points | density, instance_object |
| instance_on_points | Instance objects | instance, scale |
| boolean_operation | Boolean geometry | operation |
| array_geometry | GN array | count, offset |
| curve_to_mesh | Profile along curve | profile_curve |
| set_material | Assign material | material_name |
| transform_geometry | Transform | translation, rotation, scale |
| delete_geometry | Delete by selection | mode |
| capture_attribute | Capture attribute | data_type, domain |

### 5.5 Compositor

| Template | What It Does | Params |
|----------|-------------|--------|
| glare_node | Glare/bloom | quality, threshold, size |
| color_balance | Color grading | lift, gamma, gain |
| lens_distortion | Lens distortion | dispersion |
| blur_node | Gaussian blur | size_x, size_y |
| filter_sharpen | Sharpen | factor |
| mix_rgb | Mix images | blend_type, factor |
| color_correction | Color correction | shadows, midtones |
| viewer_node | Preview output | (connects to viewer) |
| render_layers | Input render layers | layer, pass |
| composite_output | Final output | (connects to composite) |

### 5.6 Video Sequencer

| Template | What It Does | Params |
|----------|-------------|--------|
| add_movie_strip | Add movie clip | filepath, channel |
| add_sound_strip | Add sound | filepath, channel |
| add_image_strip | Add image sequence | filepath, channel |
| add_effect_strip | Add effect | type (BLUR, GLOW, TRANSFORM) |
| split_strip | Split at frame | frame, channel |
| trim_strip | Trim start/end | start, end |
| add_transition | Add transition | type (CROSS, WIPE) |
| set_strip_speed | Speed control | factor |

### 5.7 Animation

| Template | What It Does | Params |
|----------|-------------|--------|
| keyframe_location | Keyframe position | x, y, z, frame |
| keyframe_rotation | Keyframe rotation | rx, ry, rz, frame |
| keyframe_scale | Keyframe scale | sx, sy, sz, frame |
| keyframe_material | Keyframe material | input_name, value, frame |
| add_fcurve_modifier | Add FCurve modifier | type (NOISE, ENVELOPE) |
| set_interpolation | Set keyframe interp | type (LINEAR, BEZIER) |
| add_driver | Add driver | property, expression |
| nla_track | Add NLA track | action_name, start_frame |

### 5.8 Outliner

| Template | What It Does | Params |
|----------|-------------|--------|
| rename_by_type | Rename by type | prefix, use_numbering |
| sort_by_name | Alphabetical sort | reverse |
| sort_by_type | Group by type | (mesh, light, camera) |
| sort_by_material | Group by material | |
| sort_by_location | Group by position | axis, threshold |
| add_color_tag | Color tag collection | color |
| group_selected | Group into collection | name |
| delete_unused_data | Purge orphan data | data_type |

### 5.9 Text Editor (Code Writing)

| Template | What It Does | Params |
|----------|-------------|--------|
| blender_addon_skeleton | New addon template | name, bl_idname |
| operator_template | Custom operator | class_name |
| panel_template | UI Panel | class_name, bl_space_type |
| modal_operator | Modal operator | class_name |
| property_group | Property group | class_name |
| bmesh_operation | BMesh edit | description |
| node_tree_setup | Programmatic nodes | tree_type |
| import_export_script | IO script template | format |


---

## 6. Documentation Area Mapping

Blender documentation covers ~15 major sections with ~80 subsections.
Each maps to template categories and operator potential:

| Doc Section | Template Coverage | Operator Potential |
|------------|-------------------|-------------------|
| User Interface | Low (informational) | Medium (keymap lookup) |
| Editors | High (per-editor templates) | High (editor operators) |
| Scenes and Objects | High (organize, rename) | Very High (outliner ops) |
| Modeling | Very High (20+ templates) | Very High (edit mode ops) |
| Animation and Rigging | High (10+ templates) | High (animation ops) |
| Rendering | High (setup templates) | High (render ops) |
| Compositing | High (node templates) | High (compositor ops) |
| Video Editing | Medium (strip templates) | High (VSE ops) |
| Advanced (Scripting) | High (text editor templates) | High (code gen) |

**Total**: ~67 base templates + ~44 composition chains = ~111 templates
**Operator potential**: ~50 contextual operators across all editors

---

## 7. Operator-Based UI Design

### 7.1 The Problem with Chat

Chat is great for exploration. But for repetitive tasks, an artist
should not have to type the same request every time. They need a button.

### 7.2 Contextual Operators

Register operators in Blender native menus:



### 7.3 Menu Integration

| Menu | Operators |
|------|-----------|
| Object > Coworker | Organize, Rename, Group, Clean |
| Mesh > Coworker | Clean Mesh, Optimize, Prepare for Print |
| Add > Coworker | Template primitives, Scene setups |
| Render > Coworker | Quick render setup, Camera framing |
| Node > Coworker | Frame nodes, Organize tree |
| Sidebar > Coworker | All quick actions, Chat, Assets |

### 7.4 Operator Pattern

Each operator detects context, gathers parameters, sends to orchestrator:



---

## 8. Creative Workflows

### 8.1 Camera Framing

The model understands spatial relationships:

- Frame from above: Camera at (0, 0, 15), looking down, 35mm lens
- Close-up face: Find face object, 2 units away, 85mm portrait lens
- Dutch angle: Camera tilted 15-30 degrees on Z, slight low angle
- Follow action: Camera tracks target with Track To constraint

### 8.2 Lighting from Reference

The model interprets lighting descriptions:

- Rembrandt painting: Key light warm (2700K), 45 degrees, cool fill at 1/4
- Studio product: Three-point with soft boxes, rim light, grey background
- Moonlit night: Single cool blue area light, low energy, no fill
- Match reference image: Analyze light direction/color, replicate in 3D

### 8.3 Asset-Aware Workflows

The MCP knows what assets are available:

- Add wood material: Search asset library, find Oak_Veneer, apply with UV
- Scatter rocks: Find rock assets, use geometry nodes, randomize scale
- Use brick texture: Check library, apply with displacement if available

### 8.4 Scene Composition

- Cinematic: Rule of thirds, shallow DOF, teal/orange grading, lens distortion
- Turntable render: Camera on empty, 360 rotation, three-point lighting
- Mood board: Arrange reference images in 3D, camera sees all

---

## 9. Scalability and Maintenance

### 9.1 Adding New Templates

1. Write template function in blender_templates.py
2. Register with register_template()
3. Add test in tests/test_templates.py
4. Template is automatically available to orchestrator

No other files need to change. The system discovers templates at runtime.

### 9.2 Adding New Operators

1. Create operators/coworker_<name>.py
2. Define operator class
3. Register in __init__.py
4. Add to appropriate menu

### 9.3 Documentation Coverage

| Area | Doc Section | Templates | Coverage |
|------|------------|-----------|----------|
| Mesh Primitives | Modeling/Meshes | 9/9 | 100% |
| Modifiers | Modeling/Modifiers | 12/20 | 60% |
| Materials | Rendering/Materials | 6/15 | 40% |
| Animation | Animation/Keyframes | 10/25 | 40% |
| Compositing | Compositing/Nodes | 12/30 | 40% |
| Sequencer | Video Editing | 9/15 | 60% |
| Outliner | Scenes/Objects | 6/12 | 50% |
| Text Editor | Advanced/Scripting | 8/12 | 67% |

Target: 80% coverage across all documentation areas.

### 9.4 The Vision

An artist opens Blender. The Coworker sidebar shows contextual
buttons based on what they are doing. They click Organize Outliner
and their scene is instantly tidy. They type make it look cinematic
and the lighting, camera, and post-processing are set up.

The model does not write Python. The model understands intent.
The server understands Blender. Together, they make the artists
dream come true.

---

## 10. Comparison Tables

### 5.1 Current vs Target Architecture

| Aspect | Current | Target |
|--------|---------|--------|
| Model's job | Write Python code | Describe intent in English |
| Server's job | Execute code, catch errors | Interpret intent, generate code |
| Tools visible | 30+ always | 5-8 per editor |
| Context | Model must request | Automatically injected |
| Error handling | Reject -> retry -> spiral | Auto-fix -> template -> succeed |
| Round-trips per task | 4-8 | 1-2 |
| Token usage per task | 2000-5000 | 200-500 |
| First-attempt success | ~30% | ~80% |

### 5.2 Tool Complexity Comparison

| Approach | Tools | Model Decision | Failure Modes |
|----------|-------|----------------|---------------|
| Current: flat list | 30+ | Pick 1 of 30 | 29 wrong choices |
| Domain-scoped | 8-12 | Pick 1 of 8 | 7 wrong choices |
| Template-based | 5-8 templates | Pick template + params | 4 wrong choices |
| Server-orchestrated | 1 intent tool | Describe what you want | Near zero |

### 5.3 Blender Buddy vs BFA Coworker vs Target

| Feature | Blender Buddy | BFA Coworker (Current) | BFA Coworker (Target) |
|---------|---------------|------------------------|----------------------|
| Model selection | Simple (3 presets) | Complex (9 presets + custom) | Simple (auto-detect VRAM) |
| Tool count | ~10 curated | 30+ | 5-8 per editor |
| Code execution | Direct | Preflight + spiral | Template + auto-fix + orchestrate |
| Scene context | Injected | Model must request | Injected automatically |
| Editor awareness | None | None | Full (7 editors) |
| Error recovery | Basic | Spiral detection | Auto-fix -> template fallback |
| Asset-first | Yes (built-in assets) | Partial (6 tools) | Yes (server biases toward assets) |

---

## 11. Implementation Plan

### Phase 1: Wire Auto-fix + Templates (1-2 hours)

**Goal**: Make the existing modules actually work.

1. Wire  into  in 
2. Add import for  module
3. Add tests for  and 
4. Verify all 70+ tests pass

### Phase 2: Editor-Aware Tool Scoping (3-4 hours)

**Goal**: Show 5-8 tools instead of 30+.

1. Detect  in MCP server
2. Create editor -> tool mapping (7 editors)
3. Filter tool list before sending to model
4. Add  to tool descriptions
5. Update system prompt to mention editor context

### Phase 3: Resource Injection (2-3 hours)

**Goal**: Model sees scene context automatically.

1. Create  in MCP server
2. Inject as system message before each model call
3. Include: active object, object count, collections, materials, modifiers
4. Keep context under 200 tokens

### Phase 4: Server-Side Orchestration (4-6 hours)

**Goal**: Server chains operations, model just describes intent.

1. Create  that maps common intents to template chains
2. Example chains:
   -  -> create_torus + add_array + apply + remesh
   -  -> three_point_lighting template
   -  -> loop through objects + add_material
3. Model's tool call becomes: 

### Phase 5: Template Expansion (ongoing)

**Goal**: Cover 80% of common Blender operations.

Priority templates to add:
-  (monkey head)
-  (cut one mesh from another)
-  (merge multiple objects)
-  (move object origin)
-  (basic rig)
-  (automatic weights)
-  (basic node tree)
-  (basic particles)

---

## 12. Success Criteria

### 7.1 Benchmark Tests

| Test | Current Score | Target Score |
|------|---------------|--------------|
| Create 8 torus pillars in circle | 6+ attempts | 1 attempt |
| Add materials to objects | 4+ attempts | 1 attempt |
| Set up three-point lighting | 3+ attempts | 1 attempt |
| Apply modifier chain | 5+ attempts | 1 attempt |
| Keyframe animation | 4+ attempts | 1 attempt |
| Stonehenge full scene | 10+ attempts | 2-3 attempts |

### 7.2 Metrics

| Metric | Current | Target |
|--------|---------|--------|
| First-attempt success rate | ~30% | ~80% |
| Average round-trips per task | 5 | 1.5 |
| Average tokens per task | 3000 | 400 |
| Hard crashes per session | 1-2 | 0 |
| User intervention required | Often | Rarely |

### 7.3 The Vision

The model becomes a **creative director**, not a **programmer**:
- "Make it look ancient" -> server applies weathering materials, displacement
- "Add some drama" -> server adjusts lighting, camera angle, DOF
- "Make it move" -> server adds keyframes, easing, physics
- "Make it look professional" -> server sets up render, resolution, lighting

The model provides **intent**. The server provides **execution**. The user provides **direction**.

---

## Appendix: File Structure

addon/bfa_coworker/
  blender_templates.py      # Template registry (111+ templates)
  autofix.py                # Auto-correction rules (12+ rules)
  orchestrator.py           # Intent -> plan -> execution pipeline
  editor_context.py         # Editor detection + context gathering
  operators/
    coworker_organize.py    # Outliner organization operators
    coworker_mesh.py        # Mesh cleanup operators
    coworker_material.py    # Material assignment operators
    coworker_lighting.py    # Lighting setup operators
    coworker_camera.py      # Camera framing operators
    coworker_scene.py       # Scene setup operators
    coworker_nodes.py       # Node tree operators
    coworker_animation.py   # Animation operators
  ui/
    sidebar_panel.py        # N-panel with contextual buttons
    context_menu.py         # Right-click menu extensions
mcp/blmcp/tools/
  execute_blender_code.py   # Updated with intent execution
  execute_blender_plan.py   # Plan -> template chain execution
tests/
  test_preflight.py         # Preflight + auto-fix tests
  test_templates.py         # Template rendering tests
  test_orchestrator.py      # Orchestrator pipeline tests
