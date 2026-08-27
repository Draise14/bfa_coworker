# BFA Coworker - Tier 6: Viewport Diffusion Renderer

**Date**: 2026-08-27
**Status**: Planning - Research Complete
**Depends on**: Tier 5a (Gen Plugin Foundation), Tier 4b (Viewport Overlays)
**Inspiration**: Krea Unbound.o side-by-side render, DiffusionRenderer (NVIDIA)

---

## Table of Contents

1. Vision & Goals
2. Technical Feasibility Analysis
3. Architecture: AOV Extraction -> ControlNet -> Diffusion -> Compositing
4. Performance Strategy: Making It Real-Time
5. UI Design: Side-by-Side View with Slider
6. Seed-Consistent Rendering to Disk
7. ControlNet Integration
8. Implementation Plan
9. Hardware Requirements
10. Key Decisions

---

## 1. Vision & Goals

### 1.1 The Big Idea

A real-time AI-enhanced viewport that shows a diffusion-rendered version of the 3D scene alongside the raw OpenGL viewport. The user sees both the traditional 3D view and an AI-interpreted photorealistic (or stylized) rendering, updated live as they work.

Like Krea Unbound.o, but integrated into Blender:



### 1.2 Core Value

- **See the final look while working** - no need to render to see materials, lighting, and atmosphere
- **Iterate faster** - move objects, change lights, see the AI interpretation update in real-time
- **Art direction tool** - explore styles (photorealistic, painterly, cinematic) without changing materials
- **Client presentation** - show clients a polished preview during the blocking phase
- **Seed-consistent export** - render the same view to disk with a solid seed for reproducibility

### 1.3 What This Is NOT

- Not a replacement for Cycles/EEVEE - it is a creative preview tool
- Not pixel-perfect - it is an AI interpretation that preserves spatial structure
- Not a final renderer - use Cycles for final output
- Not frame-to-frame consistent - each frame is independently generated (temporal coherence is a Tier 7 problem)

---

## 2. Technical Feasibility Analysis

### 2.1 AOV Extraction from Viewport

Blender Python can extract AOVs (Arbitrary Output Variables) from the viewport using GPU offscreen rendering:

| AOV | Method | Latency | Quality |
|-----|--------|---------|----------|
| **Depth** | gpu.types.GPUOffScreen.read_depth() | <1ms | Perfect |
| **Normal** | Custom shader (world-space normals -> RGB) | <2ms | Perfect |
| **Albedo** | Viewport solid mode + matcap | <1ms | Good (no lighting) |
| **Canny edges** | OpenCV on viewport screenshot | ~5ms | Good |
| **Segmentation** | Material index pass (custom shader) | <2ms | Perfect |
| **Pose (skeleton)** | Armature bone positions -> skeleton image | ~3ms | Perfect |

Key insight: We do NOT need Cycles render passes. The viewport OpenGL output is sufficient as ControlNet input. The diffusion model interprets the spatial structure and adds photorealism.

### 2.2 Diffusion Model Performance

| Model | Steps | Resolution | RTX 3060 | RTX 4090 | Quality |
|-------|-------|------------|----------|----------|----------|
| SDXL Turbo | 1 | 512x512 | ~80ms (12 FPS) | ~20ms (50 FPS) | Good |
| SDXL Turbo | 1 | 1024x1024 | ~300ms (3 FPS) | ~80ms (12 FPS) | Very Good |
| LCM-LoRA SDXL | 4 | 512x512 | ~300ms (3 FPS) | ~80ms (12 FPS) | Very Good |
| LCM-LoRA SDXL | 4 | 1024x1024 | ~1.2s (<1 FPS) | ~300ms (3 FPS) | Excellent |
| FLUX.1-schnell | 4 | 1024x1024 | ~3s (<1 FPS) | ~800ms (1 FPS) | Excellent |
| StreamDiffusion | 1-2 | 512x512 | ~50ms (20 FPS) | ~15ms (66 FPS) | Good |

**Key finding**: SDXL Turbo at 512x512 achieves 12+ FPS on consumer GPUs. This is real-time territory.

### 2.3 ControlNet Compatibility

| ControlNet Model | Input | Compatible with SDXL Turbo | Compatible with LCM-LoRA |
|-------------------|-------|---------------------------|--------------------------|
| controlnet-depth-sdxl-1.0 | Depth map | Yes | Yes |
| controlnet-normal-sdxl-1.0 | Normal map | Yes | Yes |
| controlnet-canny-sdxl-1.0 | Canny edges | Yes | Yes |
| controlnet-seg-sdxl-1.0 | Segmentation | Yes | Yes |

All major ControlNet models are compatible with SDXL-based turbo/LCM models.

### 2.4 StreamDiffusion: The Real-Time Breakthrough

StreamDiffusion (2024-2025) is a pipeline-level optimization that achieves:

- **91 FPS on RTX 4090** at 512x512 with 1-step generation
- **60 FPS at 480p** with 4-step generation (StreamDiffusionV2)
- **Pipeline parallelism**: encodes frame N while decoding frame N-1
- **Key innovation**: Stochastic length filter + residual CFG for quality at speed

This is the most promising approach for true real-time viewport diffusion.

---

## 3. Architecture: AOV -> ControlNet -> Diffusion -> Compositing

### 3.1 Pipeline Overview



### 3.2 Thread Architecture



### 3.3 Data Flow

```python
@dataclass
class ViewportAOVs:
    depth: np.ndarray           # H x W x 1 (float32, 0-1)
    normal: np.ndarray          # H x W x 3 (float32, -1 to 1)
    albedo: np.ndarray          # H x W x 3 (uint8, 0-255)
    canny: np.ndarray | None    # H x W x 1 (uint8, optional)
    seg: np.ndarray | None      # H x W x 3 (uint8, optional)
    camera_matrix: np.ndarray   # 4x4 world-to-camera
    projection: np.ndarray      # 4x4 projection matrix
    timestamp: float            # time.monotonic()

@dataclass
class DiffusionResult:
    image: np.ndarray           # H x W x 3 (uint8, RGB)
    seed: int                   # Seed used for generation
    prompt: str                 # Text prompt used
    aov_hash: str               # Hash of input AOVs (for change detection)
    generation_time_ms: float   # Time to generate
```

---

## 4. Performance Strategy: Making It Real-Time

### 4.1 The Performance Budget

For interactive use, we need at least 10 FPS (100ms per frame). The budget:

| Stage | Budget | Method |
|-------|--------|--------|
| AOV extraction | <5ms | GPU readback (async PBO) |
| ControlNet encode | <5ms | Pre-computed, cached |
| Diffusion (1-step) | <50ms | SDXL Turbo or StreamDiffusion |
| VAE decode | <20ms | Optimized VAE |
| Viewport composite | <1ms | GPU overlay |
| **Total** | **<80ms** | **12+ FPS** |

### 4.2 Optimization Strategies

**Strategy 1: Resolution scaling**
- Generate at 512x512 (fast), upscale to viewport size (fast bilinear)
- User can choose: 512 (fastest), 768 (balanced), 1024 (quality)

**Strategy 2: Skip frames**
- If viewport has not changed (same AOV hash), skip generation
- Only regenerate when camera moves or objects change
- This gives instant feedback when static

**Strategy 3: Pipeline parallelism (StreamDiffusion)**
- Encode frame N while decoding frame N-1
- Overlaps compute stages for maximum throughput
- Achieves 60+ FPS on RTX 4090

**Strategy 4: Model caching**
- Keep model in VRAM between frames
- Only unload when user disables the feature
- First activation: ~5s load time, then instant

**Strategy 5: Adaptive quality**
- When user is actively manipulating (dragging objects): 512px, 1-step
- When user pauses (100ms idle): 768px, 4-step
- When user stops (1s idle): 1024px, 8-step (highest quality)

### 4.3 GPU Memory Management

| Component | VRAM (SDXL) | VRAM (FLUX) |
|-----------|-------------|-------------|
| Base model | ~6.5 GB | ~12 GB |
| ControlNet | ~2.5 GB | ~5 GB |
| LCM-LoRA | ~0.5 GB | N/A |
| VAE | ~0.1 GB | ~0.1 GB |
| Working memory | ~1 GB | ~2 GB |
| **Total** | **~10.6 GB** | **~19 GB** |

**Minimum GPU**: 8 GB VRAM (SDXL Turbo without ControlNet)
**Recommended**: 12 GB VRAM (SDXL Turbo + ControlNet)
**Ideal**: 16+ GB VRAM (FLUX + ControlNet + high resolution)

---

## 5. UI Design: Side-by-Side View with Slider

### 5.1 View Modes

| Mode | Layout | Use Case |
|------|--------|----------|
| **Side-by-side** | Split viewport: left=OpenGL, right=Diffusion | Comparing raw vs AI |
| **Overlay** | Diffusion rendered on top, slider blends | Quick toggle between views |
| **Diffusion only** | Full viewport shows diffusion output | Presentation mode |
| **Picture-in-picture** | Small diffusion preview in corner | While working in full OpenGL |

### 5.2 The Slider

A horizontal slider at the bottom of the viewport controls the blend:



- **0%**: Pure OpenGL viewport (no AI)
- **50%**: 50/50 blend of OpenGL and diffusion
- **100%**: Pure diffusion output

The slider is drawn via GPU overlay (POST_PIXEL handler) and is draggable via modal operator.

### 5.3 Header Bar

When diffusion rendering is active, a thin header bar appears at the top of the viewport:



| Control | Purpose |
|---------|----------|
| AI Render toggle | Enable/disable diffusion rendering |
| Model dropdown | Switch between SDXL Turbo, LCM-LoRA, FLUX |
| ControlNet dropdown | Select which AOV to use (depth, normal, canny, none) |
| FPS counter | Current generation speed |
| Seed field | Set or randomize seed |
| Random button | Generate new random seed |
| Lock button | Lock seed for consistent export |

### 5.4 Prompt Input

The text prompt is entered in the sidebar panel (not the viewport header). This keeps the viewport clean:



---

## 6. Seed-Consistent Rendering to Disk

### 6.1 The Problem

Real-time preview uses a random seed for each frame. When the user wants to export a high-quality version, they need the same seed to get consistent results.

### 6.2 The Solution

1. **Seed lock**: User clicks Lock to fix the current seed
2. **Preview with locked seed**: All subsequent frames use the same seed
3. **Export**: Render at higher resolution (1024x1024) with the locked seed
4. **Frame sequence**: Render animation frames with per-frame seeds derived from a base seed

### 6.3 Seed Determinism

For reproducible results:

- Same seed + same AOVs + same prompt = same output
- Use  for determinism
## 7. ControlNet Integration

### 7.1 AOV -> ControlNet Mapping

| AOV | ControlNet Model | Best For | Strength Range |
|-----|------------------|----------|----------------|
| Depth | controlnet-depth-sdxl-1.0 | Spatial layout, perspective | 0.5-0.9 |
| Normal | controlnet-normal-sdxl-1.0 | Surface detail, lighting | 0.5-0.8 |
| Canny | controlnet-canny-sdxl-1.0 | Edge preservation, structure | 0.6-0.9 |
| Segmentation | controlnet-seg-sdxl-1.0 | Material/object separation | 0.5-0.7 |

### 7.2 Multi-ControlNet Stacking

For best results, combine multiple AOVs:



This gives the diffusion model rich spatial information while allowing creative interpretation.

### 7.3 IP-Adapter for Style Consistency

Use IP-Adapter to maintain style consistency across frames:

- Load a style reference image (from moodboard)
- IP-Adapter extracts style features and injects them into the generation
- Combined with ControlNet for spatial + style control
- User selects style image from moodboard or file

### 7.4 ControlNet Strength Slider

A per-ControlNet strength slider lets users control how tightly the AI follows the AOV:

- **Low (0.3)**: AI has more creative freedom, may deviate from geometry
- **Medium (0.6)**: Balanced - preserves structure, allows style interpretation
- **High (0.9)**: Strict adherence to AOV, less creative variation

---

## 8. Implementation Plan

### Phase 1: Core Infrastructure (~400 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 1.1 | ViewportAOVExtractor class (depth, normal, albedo) | viewport_diffusion.py (new) | ~120 |
| 1.2 | DiffusionPipeline class (SDXL Turbo, LCM-LoRA) | viewport_diffusion.py | ~150 |
| 1.3 | Background thread with queue and model caching | viewport_diffusion.py | ~80 |
| 1.4 | bpy.app.timers integration for frame polling | viewport_diffusion.py | ~50 |

### Phase 2: Viewport Overlay (~300 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 2.1 | GPU POST_PIXEL draw handler for side-by-side | viewport_diffusion.py | ~80 |
| 2.2 | Slider overlay with modal drag operator | viewport_diffusion.py | ~80 |
| 2.3 | Header bar with model/ControlNet/seed controls | ui_viewport.py (new) | ~80 |
| 2.4 | View modes (side-by-side, overlay, pip) | viewport_diffusion.py | ~60 |

### Phase 3: ControlNet Integration (~250 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 3.1 | ControlNet model loading and caching | viewport_diffusion.py | ~60 |
| 3.2 | Multi-ControlNet stacking | viewport_diffusion.py | ~80 |
| 3.3 | IP-Adapter style injection | viewport_diffusion.py | ~60 |
| 3.4 | Per-ControlNet strength sliders | ui_viewport.py | ~50 |

### Phase 4: Seed & Export (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 4.1 | Seed lock/randomize UI | ui_viewport.py | ~40 |
| 4.2 | Deterministic generation with torch.Generator | viewport_diffusion.py | ~40 |
| 4.3 | Export to disk (single frame, multiple resolutions) | viewport_diffusion.py | ~60 |
| 4.4 | Frame sequence export (animation) | viewport_diffusion.py | ~60 |

### Phase 5: StreamDiffusion (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 5.1 | StreamDiffusion pipeline integration | viewport_diffusion.py | ~100 |
| 5.2 | Pipeline parallelism (encode N while decode N-1) | viewport_diffusion.py | ~50 |
| 5.3 | Adaptive quality (idle detection, resolution scaling) | viewport_diffusion.py | ~50 |

### Phase 6: Agent Integration (~150 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 6.1 | MCP tool: render_viewport (agent-triggered export) | mcp/blmcp/tools/ | ~60 |
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

**CPU fallback**: SDXL Turbo can run on CPU (~5s per frame). Not real-time, but useful for export.

### 9.1 VRAM Detection and Auto-Configuration

On first activation, detect GPU VRAM and recommend settings:



---

## 10. Key Decisions

| Decision | Rationale |
|----------|-----------|
| **SDXL Turbo as default model** | 1-step generation, 12+ FPS on consumer GPUs, good quality. Best speed/quality tradeoff. |
| **AOV from viewport, not Cycles** | Viewport AOVs are instant (<5ms). Cycles render passes require a full render. The diffusion model interprets the spatial structure. |
| **Depth as primary ControlNet** | Depth provides the strongest spatial constraint. Normal and Canny are optional enhancements. |
| **Side-by-side as default view** | Most intuitive for comparing raw vs AI. Overlay mode for quick toggle. |
| **Slider for blend ratio** | Gives users fine control over AI influence. 0% = raw, 100% = pure AI. |
| **Seed lock for export** | Reproducible results. Same seed + same AOVs = same output. Critical for production use. |
| **StreamDiffusion for max FPS** | Pipeline parallelism achieves 60+ FPS. Best for users with high-end GPUs. |
| **Adaptive quality** | Automatically adjusts resolution and steps based on user activity. Balances quality and responsiveness. |
| **IP-Adapter for style** | Style reference from moodboard maintains visual consistency across frames. |
| **Background thread** | Diffusion runs in a separate thread to avoid blocking Blender UI. Queue-based communication. |
| **GPU memory management** | Model stays in VRAM between frames. Auto-unload when disabled. Warning on low VRAM. |
| **Export to disk with solid seed** | Users can render high-quality stills or frame sequences with deterministic results. |

---

## Summary

The Viewport Diffusion Renderer is the most ambitious feature in the BFA Coworker roadmap. It transforms Blender viewport into an AI-enhanced preview that updates in real-time.

**Key technical enablers**:
- SDXL Turbo: 1-step generation at 12+ FPS
- ControlNet: Spatial consistency from viewport AOVs (depth, normal, canny)
- StreamDiffusion: Pipeline parallelism for 60+ FPS on high-end GPUs
- GPU offscreen: Instant AOV extraction from viewport (<5ms)

**Key UX features**:
- Side-by-side view with blend slider
- Model/ControlNet/seed controls in viewport header
- Seed lock for reproducible export
- Adaptive quality based on user activity
- Export to disk at multiple resolutions

**Implementation**: 6 phases, ~1,500 LOC, 1 new file (viewport_diffusion.py) + 1 new file (ui_viewport.py)
