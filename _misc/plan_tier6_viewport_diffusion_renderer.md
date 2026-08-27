# BFA Coworker - Tier 6: Viewport Diffusion Renderer

**Date**: 2026-08-27
**Status**: Planning - Architecture Revised (Render Engine, Dreamer branding, compositor)
**Depends on**: Tier 5a (Gen Plugin Foundation), Tier 4b (Viewport Overlays)
**Inspiration**: Krea Unbound.o, NVIDIA DiffusionRenderer, ComfyUI Live, Cycles/EEVEE pass system

---

## Table of Contents

1. Vision and Goals
2. Dreamer Branding and Render Settings
3. Architecture: Render Engine (NOT Side-by-Side)
4. Compositor Integration and Custom Render Passes
5. Technical Feasibility Analysis
6. Viewport Overlay and Gizmo Integration
7. Camera and Viewport Modes
8. Performance Strategy: Making It Real-Time
9. Seed-Consistent Rendering to Disk
10. ControlNet Integration
11. Implementation Plan
12. Hardware Requirements
13. Key Decisions

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

## 2. Dreamer Branding and Render Settings

### 2.1 The Name

The AI render engine is called **Dreamer** -- like Cycles and EEVEE,
it is a first-class render engine with its own identity. The name
suggests imagination and visual interpretation, fitting for a diffusion
renderer that reinterprets 3D scenes.

### 2.2 Render Settings Integration

Dreamer registers as a render engine with Blender, so it appears in
the Render Properties panel alongside Cycles and EEVEE. Standard
render settings work out of the box:

| Setting | Dreamer Behavior |
|---------|-----------------|
| Resolution | Controls diffusion output resolution |
| Resolution % | Scales generation resolution |
| Frame Range | Used for animation export |
| Output Path | Where exported frames are saved |
| Color Management | Applied via bind_display_space_shader() |
| Film: Transparent | Alpha channel from diffusion (if supported) |
| Render Passes | Custom Dreamer passes (see Section 4) |

### 2.3 Dreamer-Specific Settings Panel

A custom panel in Render Properties provides Dreamer-specific controls:

```
Render Properties > Dreamer
+-- Model: [SDXL Turbo | LCM-LoRA | FLUX-schnell | StreamDiffusion]
+-- Quality: [Draft | Standard | High | Ultra]
+-- Steps: 1 | 4 | 8 | 16
+-- Resolution: [512 | 768 | 1024]
+-- CFG Scale: [1.0 - 15.0]
+-- Prompt: [text field]
+-- Negative Prompt: [text field]
+-- Seed: [int] [Random] [Lock]
+-- ControlNet:
|   +-- Depth: [ON/OFF] Strength: [0.0-1.0]
|   +-- Normal: [ON/OFF] Strength: [0.0-1.0]
|   +-- Canny: [ON/OFF] Strength: [0.0-1.0]
|   +-- Segmentation: [ON/OFF] Strength: [0.0-1.0]
+-- Style:
|   +-- IP-Adapter: [ON/OFF]
|   +-- Reference Image: [image selector]
+-- Viewport:
|   +-- Mode: [AI Render | AI+OpenGL Blend | Camera Bounds]
|   +-- Blend: [0.0 - 1.0]
|   +-- Adaptive Quality: [ON/OFF]
+-- Export:
    +-- Resolution: [Current | 2x | 4x]
    +-- Format: [PNG | EXR | JPEG]
```

### 2.4 Render Settings Properties

```python
class DreamerSettings(bpy.types.PropertyGroup):
    model: EnumProperty(
        name="Model",
        items=[
            ("SDXL_TURBO", "SDXL Turbo", "Fastest, 1-step"),
            ("LCM_LORA", "LCM-LoRA", "Balanced, 4-step"),
            ("FLUX_SCHNELL", "FLUX.1-schnell", "Best quality, 4-step"),
            ("STREAM", "StreamDiffusion", "Real-time, pipeline parallel"),
        ],
        default="SDXL_TURBO"
    )
    quality: EnumProperty(...)
    steps: IntProperty(min=1, max=32, default=1)
    resolution: IntProperty(default=512)
    cfg_scale: FloatProperty(min=1.0, max=15.0, default=1.0)
    prompt: StringProperty(name="Prompt")
    negative_prompt: StringProperty(name="Negative Prompt")
    seed: IntProperty(default=-1)
    seed_locked: BoolProperty(default=False)
    blend: FloatProperty(min=0.0, max=1.0, default=1.0)
    viewport_mode: EnumProperty(...)
    cn_depth: BoolProperty(default=True)
    cn_depth_strength: FloatProperty(default=0.7, min=0.0, max=1.0)
    cn_normal: BoolProperty(default=False)
    cn_normal_strength: FloatProperty(default=0.6, min=0.0, max=1.0)
    cn_canny: BoolProperty(default=False)
    cn_canny_strength: FloatProperty(default=0.7, min=0.0, max=1.0)

bpy.types.Scene.dreamer = bpy.props.PointerProperty(type=DreamerSettings)
```

### 2.5 How It Feels

When the user selects Dreamer as the render engine:
1. Render Properties shows Dreamer settings (like Cycles shows samples)
2. The viewport updates to show Dreamer output (like switching to Rendered)
3. Gizmos, overlays, selection all work normally
4. F12 renders produce a Dreamer beauty pass (like Cycles F12)
5. Compositor can post-process the Dreamer output (like Cycles compositor)

The experience is identical to switching between Cycles and EEVEE --
just select Dreamer in the dropdown and everything works.

---
## 3. Architecture: Render Engine (NOT Side-by-Side)

### 3.1 Why RenderEngine API

The original plan used side-by-side rendering with GPU overlay compositing.
This was WRONG. The correct approach is a Blender RenderEngine subclass,
the same mechanism Cycles and EEVEE use.

When the user selects Coworker AI as the viewport render engine:
1. Blender calls view_draw() every viewport redraw
2. We draw the diffusion output as a fullscreen texture
3. Blender draws overlays (gizmos, selection, wireframes) on top AUTOMATICALLY
4. The viewport looks and feels like a real render engine

### 3.2 RenderEngine Callbacks

```python
class DreamerEngine(bpy.types.RenderEngine):
    bl_idname = "DREAMER"
    bl_label = "Dreamer"
    bl_use_preview = False
    bl_use_postprocess = True  # Enable compositor

    def view_update(self, context, depsgraph):
        self._extract_aovs(context, depsgraph)

    def view_draw(self, context, depsgraph):
        region = context.region
        region_data = context.space_data.region_3d
        self._draw_diffusion_result(region, region_data)
```

### 3.3 Why This Works

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

### 3.4 Architecture Overview

```
+--------------------------------------------------+
|                   BLENDER UI                      |
|  +----------------------------------------------+|
|  |            VIEWPORT (3D View)                 ||
|  |  +------------------------------------------+||
|  |  |   OVERLAYS (drawn by Blender)            |||
|  |  |   - Gizmos  - Selection  - Wireframes    |||
|  |  |   - 3D Cursor  - Annotations             |||
|  |  +------------------------------------------+||
|  |  +------------------------------------------+||
|  |  |   DIFFUSION OUTPUT (drawn by Dreamer)    |||
|  |  |   - Full viewport texture                |||
|  |  |   - Camera bounds clipping               |||
|  |  +------------------------------------------+||
|  +----------------------------------------------+|
|  +----------------------------------------------+|
|  |   HEADER BAR [AI On/Off] [Model] [Seed]     ||
|  +----------------------------------------------+|
+--------------------------------------------------+
```

### 3.5 Thread Architecture

```
Main Thread (Blender)          Background Thread (Diffusion)
      |                                |
      |-- view_update() -->            |
      |   (extract AOVs)              |
      |   (queue AOVs) ----->         |
      |                       (encode + diffusion + decode)
      |<---- (latest result) -------- |
      |-- view_draw() -->             |
      |   (draw latest texture)       |
      |   (Blender draws overlays)    |
```

---

## 4. Compositor Integration and Custom Render Passes

### 4.1 Why Compositor Integration

Dreamer should work out of the box (beauty pass, like Cycles/EEVEE),
but ALSO expose its data to Blender compositor for advanced workflows.
Same pattern as Cycles: beauty pass is the default, users fine-tune
via compositor nodes.

### 4.2 Custom Render Passes

| Pass Name | Channels | Description |
|-----------|----------|-------------|
| **AI Output** | RGBA (4) | Final diffusion beauty pass |
| **AI Depth** | Z (1) | Depth map for ControlNet |
| **AI Normal** | RGB (3) | Normal map for ControlNet |
| **AI Albedo** | RGB (3) | Base color / albedo |
| **AI Canny** | BW (1) | Edge detection result |
| **AI Latent** | RGBA (4) | Latent space visualization |
| **AI Segmentation** | RGB (3) | Object/material segmentation |

### 4.3 Render Pass Registration

```python
class DreamerEngine(bpy.types.RenderEngine):
    def update_render_passes(self, scene=None, renderlayer=None):
        self.add_pass("AI Output", 4, "RGBA")
        self.add_pass("AI Depth", 1, "Z")
        self.add_pass("AI Normal", 3, "RGB")
        self.add_pass("AI Albedo", 3, "RGB")
        self.add_pass("AI Canny", 1, "BW")
        self.add_pass("AI Latent", 4, "RGBA")
        self.add_pass("AI Segmentation", 3, "RGB")
```

### 4.4 How Passes Flow to Compositor

```
Dreamer F12 Render
    |
    v
render(depsgraph):
    ai_output = pipeline.generate(aovs, prompt, seed)
    result = self.begin_result(0, 0, w, h)
    layer = result.layers[0]
    layer.passes["AI Output"].rect = ai_output.rgb
    layer.passes["AI Depth"].rect = aovs.depth
    layer.passes["AI Normal"].rect = aovs.normal
    self.end_result(result)
    |
    v
Compositor (Render Layers node):
    AI Output --> [Glare] --> [Color Balance] --> Composite
    AI Depth  --> [Map Value] --> [Blur] --> Depth of Field
    AI Normal --> [Normal] --> Lighting Adjust
```

### 4.5 Dreamer Compositor Nodes

Custom compositor nodes that interact with Dreamer:

- **Dreamer LoRA Node**: Input LoRA file + Strength, modifies pipeline
- **Dreamer Prompt Weight Node**: Spatial prompt emphasis via IP-Adapter
- **Dreamer ControlNet Strength Node**: Per-pass ControlNet influence
- **Dreamer Seed Node**: Lock seed for reproducible re-renders

These nodes read their settings and pass them back to the Dreamer
pipeline before generation. The compositor becomes a visual control
panel for Dreamer.

### 4.6 Latent Space Data

The latent space cannot flow directly through compositor nodes
(compositor works with pixels, not tensors). Instead:

- **Latent Visualization Pass**: Color-mapped latent as pixel image
- **Compositor as Control Interface**: Nodes control Dreamer settings
- **Latent-to-Pixel Node**: VAE decode for inspecting intermediates

### 4.7 Viewport Compositor Integration

Blender 4.0+ has a real-time viewport compositor. When Dreamer is active:
1. Dreamer generates AI output in view_draw()
2. Viewport compositor applies post-processing in real-time
3. Users see composited result live as they tweak nodes

**Caveat**: Viewport compositor has limited support for custom render
passes. The "AI Output" beauty pass works, but "AI Latent" may only
be available in F12 renders.

### 4.8 Workflow Summary

| Workflow | How It Works | Who Its For |
|----------|-------------|-------------|
| **Out-of-the-box** | Beauty pass displays directly | 90% of users |
| **Compositor post** | Beauty + Glare + Color Balance | Artists |
| **Pass-based** | All Dreamer passes in compositor tree | Tech artists |
| **Control nodes** | LoRA/Weight nodes control generation | Power users |
| **Full pipeline** | Dreamer generates, compositor post-processes | Production |

---

## 5. Technical Feasibility Analysis

### 5.1 AOV Extraction from Viewport

| AOV | Method | Latency | Quality |
|-----|--------|---------|----------|
| **Depth** | GPUOffScreen.read_depth() | <1ms | Perfect |
| **Normal** | Custom shader (normals->RGB) | <2ms | Perfect |
| **Albedo** | Viewport solid + matcap | <1ms | Good |
| **Canny edges** | OpenCV on screenshot | ~5ms | Good |
| **Segmentation** | Material index shader | <2ms | Perfect |

### 5.2 Diffusion Model Performance

| Model | Steps | Res | RTX 3060 | RTX 4090 | Quality |
|-------|-------|-----|----------|----------|----------|
| SDXL Turbo | 1 | 512 | ~80ms (12 FPS) | ~20ms (50 FPS) | Good |
| SDXL Turbo | 1 | 1024 | ~300ms (3 FPS) | ~80ms (12 FPS) | Very Good |
| LCM-LoRA SDXL | 4 | 512 | ~300ms (3 FPS) | ~80ms (12 FPS) | Very Good |
| FLUX.1-schnell | 4 | 1024 | ~3s (<1 FPS) | ~800ms (1 FPS) | Excellent |
| StreamDiffusion | 1-2 | 512 | ~50ms (20 FPS) | ~15ms (66 FPS) | Good |

### 5.3 ControlNet Compatibility

| ControlNet | Input | SDXL Turbo | LCM-LoRA |
|------------|-------|------------|----------|
| controlnet-depth-sdxl-1.0 | Depth map | Yes | Yes |
| controlnet-normal-sdxl-1.0 | Normal map | Yes | Yes |
| controlnet-canny-sdxl-1.0 | Canny edges | Yes | Yes |
| controlnet-seg-sdxl-1.0 | Segmentation | Yes | Yes |

### 5.4 StreamDiffusion

- 91 FPS on RTX 4090 at 512x512 with 1-step generation
- 60 FPS at 480p with 4-step generation
- Pipeline parallelism: encode N while decode N-1

---

## 6. Viewport Overlay and Gizmo Integration

### 6.1 How Blender Overlays Work

| Overlay | Rendered By | Condition |
|---------|------------|-----------|
| Selection outline | Blender | Overlay enabled |
| Wireframe | Blender | Wireframe overlay enabled |
| 3D Cursor | Blender | Cursor overlay enabled |
| Gizmos | Blender | Gizmo overlay enabled |
| Annotations | Blender | Annotation overlay enabled |
| Floor grid | Blender | Floor overlay enabled |

### 6.2 Camera Bounds Rendering

```python
def view_draw(self, context, depsgraph):
    region_data = context.space_data.region_3d
    if region_data.view_perspective == "CAMERA":
        gpu.state.scissor_set(x, y, width, height)
        draw_texture_2d(self._texture, (0, 0), w, h)
        gpu.state.scissor_set(0, 0, region.width, region.height)
    else:
        draw_texture_2d(self._texture, (0, 0), w, h)
```

### 6.3 View Modes

| Mode | What User Sees |
|------|----------------|
| **AI Render** | Full viewport diffusion output |
| **AI + OpenGL Blend** | Blended mix of both |
| **Camera Bounds** | Diffusion clipped to camera frame |
| **Off** | Standard EEVEE/Cycles |

### 6.4 Toggle Without Engine Switching

```python
class COWORKER_OT_toggle_ai_render(bpy.types.Operator):
    bl_idname = "coworker.toggle_ai_render"
    _previous_engine = "BLENDER_EEVEE_NEXT"

    def execute(self, context):
        if context.scene.render.engine == "DREAMER":
            context.scene.render.engine = self._previous_engine
        else:
            self._previous_engine = context.scene.render.engine
            context.scene.render.engine = "DREAMER"
        return {"FINISHED"}
```

---

## 7. Camera and Viewport Modes

(Covered in Section 6.3 View Modes and Section 6.2 Camera Bounds)

---

## 8. Performance Strategy: Making It Real-Time

### 8.1 The Performance Budget

| Stage | Budget | Method |
|-------|--------|--------|
| AOV extraction | <5ms | GPU readback (async PBO) |
| ControlNet encode | <5ms | Pre-computed, cached |
| Diffusion (1-step) | <50ms | SDXL Turbo or StreamDiffusion |
| VAE decode | <20ms | Optimized VAE |
| Viewport composite | <1ms | GPU overlay via RenderEngine |
| **Total** | **<80ms** | **12+ FPS** |

### 8.2 Optimization Strategies

- **Resolution scaling**: Generate at 512px, upscale to viewport
- **Skip frames**: Skip if viewport unchanged (AOV hash)
- **Pipeline parallelism**: StreamDiffusion encode N / decode N-1
- **Model caching**: Keep model in VRAM between frames
- **Adaptive quality**: Active=512px/1-step, Idle=768px/4-step, Stopped=1024px/8-step

### 8.3 GPU Memory

| Component | VRAM (SDXL) | VRAM (FLUX) |
|-----------|-------------|-------------|
| Base model | ~6.5 GB | ~12 GB |
| ControlNet | ~2.5 GB | ~5 GB |
| LCM-LoRA | ~0.5 GB | N/A |
| VAE + Working | ~1.1 GB | ~2.1 GB |
| **Total** | **~10.6 GB** | **~19 GB** |

---

## 9. Seed-Consistent Rendering to Disk

1. **Seed lock**: User clicks Lock to fix the current seed
2. **Preview**: All subsequent frames use the same seed
3. **Export**: Render at higher resolution with locked seed
4. **Frame sequence**: Per-frame seeds derived from base seed

Same seed + same AOVs + same prompt = same output (deterministic).

---

## 10. ControlNet Integration

| AOV | ControlNet Model | Best For | Strength |
|-----|------------------|----------|----------|
| Depth | controlnet-depth-sdxl-1.0 | Spatial layout | 0.5-0.9 |
| Normal | controlnet-normal-sdxl-1.0 | Surface detail | 0.5-0.8 |
| Canny | controlnet-canny-sdxl-1.0 | Edge preservation | 0.6-0.9 |
| Segmentation | controlnet-seg-sdxl-1.0 | Material separation | 0.5-0.7 |

Multi-ControlNet stacking combines Depth + Normal for best results.
IP-Adapter adds style consistency from reference images.

---

## 11. Implementation Plan

### Phase 1: Core Render Engine (~450 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 1.1 | DreamerEngine RenderEngine subclass | dreamer_engine.py (new) | ~100 |
| 1.2 | view_update(): AOV extraction | dreamer_engine.py | ~120 |
| 1.3 | view_draw(): Draw diffusion texture | dreamer_engine.py | ~80 |
| 1.4 | DiffusionPipeline (SDXL Turbo, LCM-LoRA) | dreamer_engine.py | ~100 |
| 1.5 | Background thread + model caching | dreamer_engine.py | ~50 |

### Phase 2: Viewport Integration (~350 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 2.1 | Camera bounds clipping (scissor) | dreamer_engine.py | ~60 |
| 2.2 | View modes (AI, AI+OpenGL, Camera bounds) | dreamer_engine.py | ~80 |
| 2.3 | Toggle operator | dreamer_engine.py | ~40 |
| 2.4 | Header bar with AI controls | dreamer_ui.py (new) | ~80 |
| 2.5 | Sidebar panel + Render Properties | dreamer_ui.py | ~90 |

### Phase 3: ControlNet (~250 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 3.1 | ControlNet model loading | dreamer_engine.py | ~60 |
| 3.2 | Multi-ControlNet stacking | dreamer_engine.py | ~80 |
| 3.3 | IP-Adapter style injection | dreamer_engine.py | ~60 |
| 3.4 | Strength sliders in sidebar | dreamer_ui.py | ~50 |

### Phase 4: Seed and Export (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 4.1 | Seed lock/randomize UI | dreamer_ui.py | ~40 |
| 4.2 | Deterministic generation | dreamer_engine.py | ~40 |
| 4.3 | Export to disk (single frame) | dreamer_engine.py | ~60 |
| 4.4 | Frame sequence export | dreamer_engine.py | ~60 |

### Phase 5: Compositor Integration (~300 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 5.1 | update_render_passes() + add_pass() | dreamer_engine.py | ~40 |
| 5.2 | Write passes to RenderResult | dreamer_engine.py | ~60 |
| 5.3 | Dreamer LoRA compositor node | dreamer_nodes.py (new) | ~80 |
| 5.4 | Dreamer Prompt Weight node | dreamer_nodes.py | ~80 |
| 5.5 | Dreamer Seed node | dreamer_nodes.py | ~40 |

### Phase 6: StreamDiffusion (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 6.1 | StreamDiffusion pipeline | dreamer_engine.py | ~100 |
| 6.2 | Pipeline parallelism | dreamer_engine.py | ~50 |
| 6.3 | Adaptive quality | dreamer_engine.py | ~50 |

### Phase 7: Agent Integration (~150 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 7.1 | MCP tool: render_viewport | mcp/blmcp/tools/ | ~60 |
| 7.2 | Agent auto-prompt | agent_controller.py | ~50 |
| 7.3 | CHOYA buttons | dreamer_ui.py | ~40 |

---

## 12. Hardware Requirements

| Tier | GPU | VRAM | FPS (512px) | FPS (1024px) | ControlNet |
|------|-----|------|-------------|--------------|------------|
| Minimum | GTX 1660 / RTX 3050 | 6 GB | 8 FPS | 1 FPS | No |
| Recommended | RTX 3060 / RX 6700 | 12 GB | 12 FPS | 3 FPS | Yes (1) |
| Good | RTX 3080 / RX 7800 | 16 GB | 20 FPS | 8 FPS | Yes (2) |
| Ideal | RTX 4090 / RX 7900 XTX | 24 GB | 50+ FPS | 15+ FPS | Yes (3) |

### 12.1 VRAM Auto-Configuration

On first activation, detect GPU VRAM and recommend settings automatically.

---

## 13. Key Decisions

| Decision | Rationale |
|----------|-----------|
| **Dreamer as first-class engine** | Named like Cycles/EEVEE. Full pass support. |
| **RenderEngine API, NOT overlay** | Blender draws overlays/gizmos automatically. |
| **SDXL Turbo as default** | 1-step, 12+ FPS on consumer GPUs. |
| **AOV from viewport, not Cycles** | Viewport AOVs are instant (<5ms). |
| **Depth as primary ControlNet** | Strongest spatial constraint. |
| **Toggle without engine switching** | Quick toggle saves/restores previous engine. |
| **Camera bounds via scissor test** | Efficient GPU clipping. |
| **Seed lock for export** | Reproducible results for production. |
| **Compositor integration** | Custom passes + Dreamer LoRA/Weight nodes. |
| **Background thread** | Diffusion runs separate from Blender UI. |

---

## Summary

Dreamer transforms the Blender viewport into an AI-enhanced preview
that updates in real-time, drawing directly over the viewport like
Cycles or EEVEE.

**Why RenderEngine approach wins:**
- Gizmos, overlays, selection all work automatically
- Feels like a native render engine, not a hack
- Camera bounds via standard OpenGL scissor
- Compositor integration via custom render passes

**Key technical enablers:**
- SDXL Turbo: 1-step generation at 12+ FPS
- ControlNet: Spatial consistency from viewport AOVs
- StreamDiffusion: Pipeline parallelism for 60+ FPS
- Custom render passes: Compositor integration

**Implementation**: 7 phases, ~1,900 LOC, 3 new files (dreamer_engine.py + dreamer_ui.py + dreamer_nodes.py)
