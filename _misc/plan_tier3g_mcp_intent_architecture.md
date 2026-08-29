# BFA Coworker — Tier 3g: MCP Intent Architecture — From Code Writer to Intent Driver

**Date**: 2026-08-28
**Status**: Planning — Not Started
**Depends on**: Tier 3f work (preflight, auto-fix, templates, tool domains)
**Reference**: Blender Buddy v9.13.1 architecture patterns

---

## Table of Contents

1. [Tier 3f Audit — What We Built](#1-tier-3f-audit--what-we-built)
2. [The Core Problem](#2-the-core-problem)
3. [Architecture: MCP as Intent Interpreter](#3-architecture-mcp-as-intent-interpreter)
4. [Editor-Aware Tool Scoping](#4-editor-aware-tool-scoping)
5. [Comparison Tables](#5-comparison-tables)
6. [Implementation Plan](#6-implementation-plan)
7. [Success Criteria](#7-success-criteria)

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

## 5. Comparison Tables

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

## 6. Implementation Plan

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

## 7. Success Criteria

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

## Appendix: Files to Modify

| File | Change | Phase |
|------|--------|-------|
|  | Wire auto-fix, editor detection, resource injection | 1-3 |
|  | Expand template library | 5 |
|  | Already complete | - |
|  | Update surface tools, reduce tool count | 2 |
|  | Add intent-based execution tool | 4 |
|  | Update with intent-based workflow | 2 |
|  | Add auto-fix and template tests | 1 |
