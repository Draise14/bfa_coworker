# BFA Coworker - Tier 6: Viewport Diffusion Renderer

**Date**: 2026-08-27
**Status**: Planning - Architecture Revised (Render Engine Approach)
**Depends on**: Tier 5a (Gen Plugin Foundation), Tier 4b (Viewport Overlays)
**Inspiration**: Krea Unbound.o, NVIDIA DiffusionRenderer, ComfyUI Live rendering

---

## Table of Contents

1. Vision and Goals
2. Architecture: Render Engine (NOT Side-by-Side)
3. Technical Feasibility Analysis
4. Viewport Overlay and Gizmo Integration
5. Camera and Viewport Modes
6. Performance Strategy: Making It Real-Time
7. Seed-Consistent Rendering to Disk
8. ControlNet Integration
9. Implementation Plan
10. Hardware Requirements
11. Key Decisions

---

## 1. Vision and Goals

### 1.1 The Big Idea

A real-time AI render engine that replaces the viewport drawing entirely.
When active, the diffusion output draws DIRECTLY over the viewport like
Cycles or EEVEE. Gizmos, overlays, selection highlights, and wireframes
all draw on top automatically via Blender RenderEngine API.

The user works in a viewport that LOOKS like a photorealistic render
but is actually a live AI interpretation, updated every frame.

### 1.2 Core Value

- **See the final look while working** - no need to F12 to see materials, lighting, atmosphere
- **Iterate faster** - move objects, change lights, see the AI interpretation update live
- **Art direction tool** - explore styles (photorealistic, painterly, cinematic) without changing materials
- **Client presentation** - show clients a polished preview during the blocking phase
- **Seed-consistent export** - render the same view to disk with a solid seed for reproducibility
- **Full viewport integration** - gizmos, overlays, selection all work normally on top

### 1.3 What This Is NOT

- Not a replacement for Cycles/EEVEE - it is a creative preview tool
- Not pixel-perfect - it is an AI interpretation that preserves spatial structure
- Not a final renderer - use Cycles for final output
- Not frame-to-frame consistent - each frame is independently generated (temporal coherence is Tier 7)

---
## 2. Architecture: Render Engine (NOT Side-by-Side)

### 2.1 Why RenderEngine API

The original plan used side-by-side rendering with GPU overlay compositing.
This was WRONG. The correct approach is a Blender RenderEngine subclass,
the same mechanism Cycles and EEVEE use.

When the user selects Coworker AI as the viewport render engine:
1. Blender calls view_draw() every viewport redraw
2. We draw the diffusion output as a fullscreen texture
3. Blender draws overlays (gizmos, selection, wireframes) on top AUTOMATICALLY
4. The viewport looks and feels like a real render engine

This is exactly how the Scratchpad addon works - a Python RenderEngine that
draws custom GLSL shaders into the viewport.

### 2.2 RenderEngine Callbacks



### 2.3 Why This Works

Key insight from the Blender RenderEngine API:

> Blender will draw overlays for selection and editing on top of the
> rendered image automatically.

This means:
- Gizmos render on top of our AI output
- Selection outlines render on top
- Wireframes render on top (if viewport overlay enabled)
- 3D cursor renders on top
- Annotations render on top
- Navigation works normally (orbit, pan, zoom)

We do NOT need to handle any of this - Blender does it for us.

### 2.4 Architecture Overview



### 2.5 Thread Architecture



---

## 3. Technical Feasibility Analysis

### 3.1 AOV Extraction from Viewport

Blender Python can extract AOVs from the viewport using GPU offscreen:

| AOV | Method | Latency | Quality |
|-----|--------|---------|----------|
| **Depth** | gpu.types.GPUOffScreen.read_depth() | <1ms | Perfect |
| **Normal** | Custom shader (world-space normals -> RGB) | <2ms | Perfect |
| **Albedo** | Viewport solid mode + matcap | <1ms | Good (no lighting) |
| **Canny edges** | OpenCV on viewport screenshot | ~5ms | Good |
| **Segmentation** | Material index pass (custom shader) | <2ms | Perfect |
| **Pose (skeleton)** | Armature bone positions -> skeleton image | ~3ms | Perfect |

Key insight: We do NOT need Cycles render passes. The viewport OpenGL
output is sufficient as ControlNet input. The diffusion model interprets
the spatial structure and adds photorealism.

### 3.2 Diffusion Model Performance

| Model | Steps | Resolution | RTX 3060 | RTX 4090 | Quality |
|-------|-------|------------|----------|----------|----------|
| SDXL Turbo | 1 | 512x512 | ~80ms (12 FPS) | ~20ms (50 FPS) | Good |
| SDXL Turbo | 1 | 1024x1024 | ~300ms (3 FPS) | ~80ms (12 FPS) | Very Good |
| LCM-LoRA SDXL | 4 | 512x512 | ~300ms (3 FPS) | ~80ms (12 FPS) | Very Good |
| LCM-LoRA SDXL | 4 | 1024x1024 | ~1.2s (<1 FPS) | ~300ms (3 FPS) | Excellent |
| FLUX.1-schnell | 4 | 1024x1024 | ~3s (<1 FPS) | ~800ms (1 FPS) | Excellent |
| StreamDiffusion | 1-2 | 512x512 | ~50ms (20 FPS) | ~15ms (66 FPS) | Good |

**Key finding**: SDXL Turbo at 512x512 achieves 12+ FPS on consumer GPUs.
This is real-time territory for a viewport render engine.

### 3.3 ControlNet Compatibility

| ControlNet Model | Input | SDXL Turbo | LCM-LoRA |
|-------------------|-------|------------|----------|
| controlnet-depth-sdxl-1.0 | Depth map | Yes | Yes |
| controlnet-normal-sdxl-1.0 | Normal map | Yes | Yes |
| controlnet-canny-sdxl-1.0 | Canny edges | Yes | Yes |
| controlnet-seg-sdxl-1.0 | Segmentation | Yes | Yes |

### 3.4 StreamDiffusion: The Real-Time Breakthrough

StreamDiffusion (2024-2025) achieves:
- **91 FPS on RTX 4090** at 512x512 with 1-step generation
- **60 FPS at 480p** with 4-step generation (StreamDiffusionV2)
- **Pipeline parallelism**: encodes frame N while decoding frame N-1

This is the most promising approach for true real-time viewport diffusion.

---

## 4. Viewport Overlay and Gizmo Integration

### 4.1 How Blender Overlays Work

When using the RenderEngine API, Blender handles overlays automatically:

| Overlay | Rendered By | Condition |
|---------|------------|-----------|
| Object selection outline | Blender | Overlay enabled |
| Wireframe | Blender | Wireframe overlay enabled |
| 3D Cursor | Blender | Cursor overlay enabled |
| Gizmos (rotate, scale, translate) | Blender | Gizmo overlay enabled |
| Annotations | Blender | Annotation overlay enabled |
| Object names | Blender | Name overlay enabled |
| Floor grid | Blender | Floor overlay enabled |
| Axis indicator | Blender | Axis overlay enabled |

The user controls all of these through the standard Viewport Overlays
dropdown - exactly like Cycles/EEVEE.

### 4.2 Camera Bounds Rendering

When the user wants to see only what the camera sees, we use OpenGL
scissor test to clip the diffusion output to the camera frame:

```python
def view_draw(self, context, depsgraph):
    region = context.region
    region_data = context.space_data.region_3d

    if region_data.view_perspective == "CAMERA":
        # Calculate camera bounds in screen space
        camera = context.scene.camera
        # Project camera corners to pixel coordinates
        # Apply scissor rect
        gpu.state.scissor_set(x, y, width, height)
        # Draw diffusion texture within scissor
        draw_texture_2d(self._texture, (0, 0), w, h)
        gpu.state.scissor_set(0, 0, region.width, region.height)
    else:
        # Full viewport diffusion
        draw_texture_2d(self._texture, (0, 0), w, h)
```

### 4.3 View Modes

| Mode | What User Sees | How It Works |
|------|----------------|--------------|
| **AI Render** | Full viewport diffusion output | view_draw() draws diffusion texture fullscreen |
| **AI + OpenGL Blend** | Blended mix of both | view_draw() draws blended texture |
| **OpenGL + AI Overlay** | Raw viewport with AI ghosted on top | Alpha-blended overlay at 30-50% |
| **Camera Bounds Only** | Diffusion clipped to camera frame | Scissor test limits drawing to camera bounds |
| **Off** | Standard EEVEE/Cycles | User switches render engine in dropdown |

### 4.4 Toggle Without Engine Switching

Quick toggle button saves/restores the previous engine:

```python
class COWORKER_OT_toggle_ai_render(bpy.types.Operator):
    bl_idname = "coworker.toggle_ai_render"
    _previous_engine = "BLENDER_EEVEE_NEXT"

    def execute(self, context):
        if context.scene.render.engine == "COWORKER_AI":
            context.scene.render.engine = self._previous_engine
        else:
            self._previous_engine = context.scene.render.engine
            context.scene.render.engine = "COWORKER_AI"
        return {"FINISHED"}
```

---

## 5. Performance Strategy

### 5.1 The Performance Budget

| Stage | Budget | Method |
|-------|--------|--------|
| AOV extraction | <5ms | GPU readback (async PBO) |
| ControlNet encode | <5ms | Pre-computed, cached |
| Diffusion (1-step) | <50ms | SDXL Turbo or StreamDiffusion |
| VAE decode | <20ms | Optimized VAE |
| Viewport composite | <1ms | GPU overlay via RenderEngine |
| **Total** | **<80ms** | **12+ FPS** |

### 5.2 Optimization Strategies

**Strategy 1: Resolution scaling** - Generate at 512x512 (fast), upscale to viewport size

**Strategy 2: Skip frames** - If viewport unchanged (same AOV hash), skip generation

**Strategy 3: Pipeline parallelism (StreamDiffusion)** - Encode N while decoding N-1

**Strategy 4: Model caching** - Keep model in VRAM between frames

**Strategy 5: Adaptive quality**
- Active manipulation: 512px, 1-step
- Paused (100ms): 768px, 4-step
- Stopped (1s): 1024px, 8-step

### 5.3 GPU Memory

| Component | VRAM (SDXL) | VRAM (FLUX) |
|-----------|-------------|-------------|
| Base model | ~6.5 GB | ~12 GB |
| ControlNet | ~2.5 GB | ~5 GB |
| LCM-LoRA | ~0.5 GB | N/A |
| VAE | ~0.1 GB | ~0.1 GB |
| Working memory | ~1 GB | ~2 GB |
| **Total** | **~10.6 GB** | **~19 GB** |

---

## 6. Seed-Consistent Rendering to Disk

1. **Seed lock**: User clicks Lock to fix the current seed
2. **Preview with locked seed**: All subsequent frames use the same seed
3. **Export**: Render at higher resolution (1024x1024) with the locked seed
4. **Frame sequence**: Render animation frames with per-frame seeds from base seed

Same seed + same AOVs + same prompt = same output (deterministic).

---

## 7. ControlNet Integration

### 7.1 AOV to ControlNet Mapping

| AOV | ControlNet Model | Best For | Strength |
|-----|------------------|----------|----------|
| Depth | controlnet-depth-sdxl-1.0 | Spatial layout, perspective | 0.5-0.9 |
| Normal | controlnet-normal-sdxl-1.0 | Surface detail, lighting | 0.5-0.8 |
| Canny | controlnet-canny-sdxl-1.0 | Edge preservation | 0.6-0.9 |
| Segmentation | controlnet-seg-sdxl-1.0 | Material/object separation | 0.5-0.7 |

### 7.2 Multi-ControlNet Stacking

Combine multiple AOVs for best results:
- Depth AOV -> controlnet-depth -> conditioning
- Normal AOV -> controlnet-normal -> conditioning
- Combined conditioning -> SDXL Turbo -> diffusion output

### 7.3 IP-Adapter for Style Consistency

Load a style reference image (from moodboard or file). IP-Adapter
extracts style features and injects them into generation, combined
with ControlNet for spatial + style control.

---

## 8. Implementation Plan

### Phase 1: Core Render Engine (~450 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 1.1 | CoworkerAIEngine RenderEngine subclass | viewport_ai_engine.py (new) | ~100 |
| 1.2 | view_update(): AOV extraction | viewport_ai_engine.py | ~120 |
| 1.3 | view_draw(): Draw diffusion texture fullscreen | viewport_ai_engine.py | ~80 |
| 1.4 | DiffusionPipeline class (SDXL Turbo, LCM-LoRA) | viewport_ai_engine.py | ~100 |
| 1.5 | Background thread with queue and model caching | viewport_ai_engine.py | ~50 |

### Phase 2: Viewport Integration (~350 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 2.1 | Camera bounds clipping (scissor test) | viewport_ai_engine.py | ~60 |
| 2.2 | View modes (AI Render, AI+OpenGL blend, Camera bounds) | viewport_ai_engine.py | ~80 |
| 2.3 | Toggle operator (AI and previous engine) | viewport_ai_engine.py | ~40 |
| 2.4 | Header bar with AI Render controls | ui_viewport.py (new) | ~80 |
| 2.5 | Sidebar panel with prompt, model, seed, export | ui_viewport.py | ~90 |

### Phase 3: ControlNet Integration (~250 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 3.1 | ControlNet model loading and caching | viewport_ai_engine.py | ~60 |
| 3.2 | Multi-ControlNet stacking | viewport_ai_engine.py | ~80 |
| 3.3 | IP-Adapter style injection | viewport_ai_engine.py | ~60 |
| 3.4 | Per-ControlNet strength sliders | ui_viewport.py | ~50 |

### Phase 4: Seed and Export (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 4.1 | Seed lock/randomize UI | ui_viewport.py | ~40 |
| 4.2 | Deterministic generation | viewport_ai_engine.py | ~40 |
| 4.3 | Export to disk (single frame) | viewport_ai_engine.py | ~60 |
| 4.4 | Frame sequence export (animation) | viewport_ai_engine.py | ~60 |

### Phase 5: StreamDiffusion (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 5.1 | StreamDiffusion pipeline integration | viewport_ai_engine.py | ~100 |
| 5.2 | Pipeline parallelism | viewport_ai_engine.py | ~50 |
| 5.3 | Adaptive quality (idle detection) | viewport_ai_engine.py | ~50 |

### Phase 6: Agent Integration (~150 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 6.1 | MCP tool: render_viewport | mcp/blmcp/tools/ | ~60 |
| 6.2 | Agent auto-prompt from scene analysis | agent_controller.py | ~50 |
| 6.3 | CHOYA buttons after viewport render | ui_viewport.py | ~40 |

---

## 9. Hardware Requirements

| Tier | GPU | VRAM | FPS (512px) | FPS (1024px) | ControlNet | Experience |
|------|-----|------|-------------|--------------|------------|------------|
| Minimum | GTX 1660 / RTX 3050 | 6 GB | 8 FPS | 1 FPS | No | Usable, laggy |
| Recommended | RTX 3060 / RX 6700 | 12 GB | 12 FPS | 3 FPS | Yes (1 CN) | Good |
| Good | RTX 3080 / RX 7800 | 16 GB | 20 FPS | 8 FPS | Yes (2 CN) | Smooth |
| Ideal | RTX 4090 / RX 7900 XTX | 24 GB | 50+ FPS | 15+ FPS | Yes (3 CN) | Excellent |

### 9.1 VRAM Auto-Configuration

On first activation, detect GPU VRAM and recommend settings automatically.

---

## 10. Key Decisions

| Decision | Rationale |
|----------|-----------|
| **RenderEngine API, NOT overlay** | Blender draws overlays/gizmos automatically. Feels native. |
| **SDXL Turbo as default** | 1-step generation, 12+ FPS on consumer GPUs. |
| **AOV from viewport, not Cycles** | Viewport AOVs are instant (<5ms). |
| **Depth as primary ControlNet** | Strongest spatial constraint. |
| **Toggle without engine switching** | Quick toggle button saves/restores previous engine. |
| **Camera bounds via scissor test** | Efficient GPU clipping. Standard OpenGL technique. |
| **Seed lock for export** | Reproducible results for production use. |
| **StreamDiffusion for max FPS** | Pipeline parallelism achieves 60+ FPS. |
| **Adaptive quality** | Auto-adjusts resolution/steps based on user activity. |
| **Background thread** | Diffusion runs separate from Blender UI. |

---

## Summary

The Viewport Diffusion Renderer transforms the Blender viewport into an
AI-enhanced preview that updates in real-time, drawing directly over
the viewport like Cycles or EEVEE.

**Why RenderEngine approach wins:**
- Gizmos, overlays, selection all work automatically
- Feels like a native render engine, not a hack
- Camera bounds clipping via standard OpenGL scissor
- No manual compositing or double-buffering needed
- User toggles it on/off like any render engine

**Key technical enablers:**
- SDXL Turbo: 1-step generation at 12+ FPS
- ControlNet: Spatial consistency from viewport AOVs
- StreamDiffusion: Pipeline parallelism for 60+ FPS
- GPU offscreen: Instant AOV extraction (<5ms)

**Implementation**: 6 phases, ~1,600 LOC, 2 new files (viewport_ai_engine.py + ui_viewport.py)
