# BFA Coworker - Tier 6: Generative 3D Systems

**Date**: 2026-08-27
**Status**: Planning - Research Complete
**Depends on**: Tier 5a (Gen Plugin Foundation), Tier 4d (Moodboard)
**Philosophy**: Local-first. No cloud APIs required. All models run on user GPU.

---

## Table of Contents

1. Vision and Goals
2. Technology Landscape (Local Open Source)
3. 3D Model Generation (Text-to-3D, Image-to-3D)
4. Texture and Material Generation
5. AI Retopology
6. Segmentation and Object Detection
7. Render-to-3D (Neural Rendering)
8. Integration Architecture
9. Implementation Plan
10. Hardware Requirements
11. Key Decisions

---

## 1. Vision and Goals

### 1.1 The Big Picture

BFA Coworker becomes a **full generative 3D pipeline** - from text description to textured, retopologized, production-ready 3D assets, all running locally on the user GPU.



### 1.2 Core User Stories

| # | Story | Priority |
|---|-------|----------|
| 1 | "Generate a 3D chair from this reference image" | CRITICAL |
| 2 | "Create a stone wall texture for this material" | CRITICAL |
| 3 | "Retopologize this sculpt for animation" | HIGH |
| 4 | "Segment this render into individual objects" | HIGH |
| 5 | "Generate a 3D model from text: a steampunk gear mechanism" | HIGH |
| 6 | "Turn this clay render into a textured 3D model" | MEDIUM |
| 7 | "Auto-UV and texture this mesh" | MEDIUM |

### 1.3 Design Principles

1. **Local-first** - all models run on user GPU. No cloud APIs required.
2. **Pipeline, not magic** - each step is explicit and controllable.
3. **Agent-orchestrated** - user describes intent, agent runs the pipeline.
4. **Production-ready output** - clean quads, proper UVs, PBR materials.
5. **Incremental** - each feature works independently.

---

## 2. Technology Landscape (Local Open Source)

### 2.1 3D Model Generation

| Model | Input | Output | VRAM | Speed | Quality | License |
|-------|-------|--------|------|-------|---------|----------|
| **TRELLIS 2** (Microsoft) | Text/Image | GLB/OBJ mesh | 8-16 GB | ~30s | Excellent | MIT |
| **TripoSR** (Stability AI) | Image | OBJ mesh | 8 GB | ~5s | Good | MIT |
| **Hunyuan3D 2.1** (Tencent) | Image/Multi-view | Textured mesh | 12-24 GB | ~60s | Excellent | Tencent |
| **InstantMesh** | Image | Mesh | 8 GB | ~10s | Good | Apache 2.0 |
| **Shap-E** (OpenAI) | Text | Point cloud/mesh | 8 GB | ~5s | Basic | MIT |

**Recommendation**: TripoSR for fast preview, TRELLIS 2 for quality, Hunyuan3D for textured output.

### 2.2 Texture Generation

| Tool | Approach | VRAM | Quality | Blender Integration |
|------|----------|------|---------|--------------------|
| **Dream Textures** | SD/SDXL projected onto UV | 8 GB | Good | Native addon |
| **DeepBump** | Normal/AO/height from single image | 4 GB | Good | Native addon |
| **StableMaterial** | PBR map generation from text | 8 GB | Very Good | Via gen plugin |
| **PolyHaven AI** | AI-enhanced PBR from photos | 0 GB (API) | Excellent | API only |

**Recommendation**: StableMaterial for local PBR generation, DeepBump for map extraction.

### 2.3 Retopology

| Tool | Approach | Speed | Quality | Open Source |
|------|----------|-------|---------|-------------|
| **QuadriFlow** | Automatic quad remeshing | Fast | Good | Yes (MIT) |
| **Quad Remesher** | Commercial auto-retopo | Fast | Excellent | No (00) |
| **Instant Meshes** | Field-aligned remeshing | Fast | Good | Yes (MIT) |
| **BMesh voxel remesh** | Built-in Blender | Fast | Basic (tris) | Yes |
| **AI retopo (research)** | Learning-based quad layout | Slow | Excellent | Research only |

**Recommendation**: QuadriFlow for automatic, with manual refinement tools.

### 2.4 Segmentation

| Tool | Approach | VRAM | Quality | Use Case |
|------|----------|------|---------|----------|
| **SAM 2** (Meta) | Segment Anything | 4-8 GB | Excellent | Object segmentation |
| **GroundingDINO** | Text-prompted detection | 4 GB | Very Good | Find objects by name |
| **CLIPSeg** | CLIP-based segmentation | 2 GB | Good | Text-prompted masks |
| **Material ID pass** | Render pass | 0 GB | Perfect | Material segmentation |

**Recommendation**: SAM 2 for general segmentation, GroundingDINO for text-prompted.

---

## 3. 3D Model Generation

### 3.1 Image-to-3D Pipeline

The most practical workflow - take a reference image and generate a 3D model:



### 3.2 Text-to-3D Pipeline

Generate a 3D model from a text description:



### 3.3 Multi-View Generation (Advanced)

For higher quality, generate multiple views first, then reconstruct:



### 3.4 Plugin Architecture

Each 3D generator is a gen plugin (same pattern as Tier 5a image plugins):

```python
class TripoSRPlugin(GenPlugin):
    name = "TripoSR"
    plugin_type = GenPluginType.IMAGE_TO_3D
    inputs = GenInputSpec.IMAGE
    vram_required = 8  # GB
    disk_required = 4  # GB

    def generate(self, inputs: GenInputs) -> GenOutputs:
        # Load model, run inference, return mesh
        ...
```

Auto-discovery: drop a .py in  and it is registered.

---

## 4. Texture and Material Generation

### 4.1 PBR Material Pipeline

Generate complete PBR materials from text or images:



### 4.2 Texture Projection (Dream Textures Style)

Project AI-generated textures onto geometry from camera view:



### 4.3 Auto-UV + Texture

For meshes without UVs, auto-unwrap and texture:



---

## 5. AI Retopology

### 5.1 Automatic Quad Remeshing

Convert high-poly sculpts or messy geometry into clean quad meshes:



### 5.2 Detail Transfer

Transfer high-poly details to retopologized mesh via normal map:



### 5.3 Face Count Presets

| Preset | Target Faces | Use Case |
|--------|-------------|----------|
| Mobile | 500-1,000 | Mobile games, VR |
| Game Ready | 2,000-5,000 | PC/console games |
| Film | 10,000-50,000 | Film/VFX |
| Auto | Based on surface area | Agent decides |

---

## 6. Segmentation and Object Detection

### 6.1 Viewport Segmentation

Segment the viewport render into individual objects or materials:



### 6.2 Text-Prompted Segmentation

Find and select objects by description:



### 6.3 Material Segmentation

Segment by material type for batch operations:



---

## 7. Render-to-3D (Neural Rendering)

### 7.1 What is Render-to-3D?

Convert a 2D render or photo into a 3D model by understanding the scene geometry from the image. This is the most advanced capability - inferring 3D structure from a single image.

### 7.2 Approaches

| Method | Input | Output | Quality | Speed | Local? |
|--------|-------|--------|---------|-------|--------|
| **DUSt3R** | 2+ images | Point cloud + camera | Excellent | ~10s | Yes |
| **MASt3R** | 2+ images | Mesh + texture | Excellent | ~15s | Yes |
| **LRM** | Single image | Mesh | Good | ~5s | Yes |
| **TRELLIS** | Single image | Mesh + texture | Excellent | ~30s | Yes |
| **TripoSR** | Single image | Mesh | Good | ~5s | Yes |

### 7.3 Multi-Image Reconstruction

For best results, use multiple views of the same object:



### 7.4 Turntable Reconstruction

Automate multi-view capture for reconstruction:



---

## 8. Integration Architecture

### 8.1 Gen Plugin Extension

All generative 3D systems integrate through the existing gen plugin architecture:

```
gen_plugins/
  image/          # Tier 5a (done)
    flux_klein_9b.py
    sdxl_turbo.py
  video/          # Tier 5d
    ltx_23.py
  audio/          # Tier 5d
    chatterbox.py
  3d/             # NEW: Tier 6
    triposr.py    # Image-to-3D (fast)
    trellis.py    # Text/Image-to-3D (quality)
    hunyuan3d.py  # Multi-view-to-3D (textured)
  texture/        # NEW: Tier 6
    stable_material.py  # PBR generation
    deepbump.py         # Map extraction
  retopo/         # NEW: Tier 6
    quadriflow.py       # Auto quad remesh
  segment/        # NEW: Tier 6
    sam2.py             # Segment Anything
    grounding_dino.py   # Text-prompted detection
```

### 8.2 MCP Tool Integration

Each capability is exposed as an MCP tool:

| Tool | Input | Output | Plugin Used |
|------|-------|--------|-------------|
| generate_3d_model | prompt/image | mesh object | TRELLIS/TripoSR/Hunyuan3D |
| generate_texture | prompt/image | PBR material | StableMaterial/DeepBump |
| retopologize | mesh object | clean quad mesh | QuadriFlow |
| segment_viewport | viewport render | object masks | SAM 2 |
| detect_objects | text prompt | object list | GroundingDINO |
| reconstruct_3d | 2+ images | mesh + texture | DUSt3R/MASt3R |

### 8.3 Agent Orchestration

The agent chains tools for complex workflows:



---

## 9. Implementation Plan

### Phase 1: 3D Model Generation (~400 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 1.1 | TripoSR plugin (fast image-to-3D) | gen_plugins/3d/triposr.py | ~120 |
| 1.2 | TRELLIS plugin (quality text/image-to-3D) | gen_plugins/3d/trellis.py | ~150 |
| 1.3 | GLB/OBJ import into Blender scene | gen_controller.py | ~50 |
| 1.4 | MCP tool: generate_3d_model | mcp/blmcp/tools/ | ~60 |
| 1.5 | Agent orchestration for 3D generation | agent_controller.py | ~20 |

### Phase 2: Texture and Material Generation (~300 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 2.1 | StableMaterial plugin (PBR from text) | gen_plugins/texture/stable_material.py | ~120 |
| 2.2 | DeepBump plugin (maps from image) | gen_plugins/texture/deepbump.py | ~80 |
| 2.3 | Principled BSDF material creation | gen_controller.py | ~50 |
| 2.4 | MCP tool: generate_texture | mcp/blmcp/tools/ | ~50 |

### Phase 3: AI Retopology (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 3.1 | QuadriFlow integration | gen_plugins/retopo/quadriflow.py | ~100 |
| 3.2 | Face count presets and auto-selection | gen_controller.py | ~30 |
| 3.3 | Detail transfer (normal map baking) | gen_controller.py | ~40 |
| 3.4 | MCP tool: retopologize | mcp/blmcp/tools/ | ~30 |

### Phase 4: Segmentation (~250 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 4.1 | SAM 2 plugin | gen_plugins/segment/sam2.py | ~100 |
| 4.2 | GroundingDINO plugin | gen_plugins/segment/grounding_dino.py | ~80 |
| 4.3 | Mask-to-3D-object mapping | gen_controller.py | ~40 |
| 4.4 | MCP tools: segment_viewport, detect_objects | mcp/blmcp/tools/ | ~30 |

### Phase 5: Render-to-3D (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 5.1 | DUSt3R/MASt3R plugin | gen_plugins/3d/dust3r.py | ~120 |
| 5.2 | Multi-view capture automation | gen_controller.py | ~40 |
| 5.3 | MCP tool: reconstruct_3d | mcp/blmcp/tools/ | ~40 |

### Phase 6: Hunyuan3D + Polish (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 6.1 | Hunyuan3D plugin (textured mesh) | gen_plugins/3d/hunyuan3d.py | ~120 |
| 6.2 | Full pipeline: text -> model -> retopo -> texture | agent_controller.py | ~50 |
| 6.3 | CHOYA buttons for each workflow stage | ui_chat.py | ~30 |

---

## 10. Hardware Requirements

| Feature | Min VRAM | Recommended | Notes |
|---------|----------|-------------|-------|
| TripoSR (image-to-3D) | 8 GB | 12 GB | Fast, good quality |
| TRELLIS (text/image-to-3D) | 8 GB | 16 GB | Higher quality, slower |
| Hunyuan3D (textured mesh) | 12 GB | 24 GB | Best quality, textured output |
| StableMaterial (PBR) | 8 GB | 12 GB | 5 PBR maps per generation |
| DeepBump (map extraction) | 4 GB | 8 GB | Lightweight |
| QuadriFlow (retopo) | 0 GB | 0 GB | CPU-only, fast |
| SAM 2 (segmentation) | 4 GB | 8 GB | Segment Anything |
| GroundingDINO (detection) | 4 GB | 8 GB | Text-prompted |
| DUSt3R/MASt3R (reconstruction) | 8 GB | 16 GB | Multi-view 3D |

**Total VRAM for full pipeline**: 12-24 GB (depends on which models are loaded simultaneously)

**Disk space**: ~30 GB for all models (downloaded on first use)

---

## 11. Key Decisions

| Decision | Rationale |
|----------|-----------|
| **Local-first** | No cloud APIs required. All models run on user GPU. Privacy, offline capability, no API costs. |
| **Plugin architecture** | Same pattern as Tier 5a image plugins. Drop a .py in gen_plugins/3d/ and it is auto-discovered. |
| **TripoSR for fast, TRELLIS for quality** | TripoSR: 5s, good quality. TRELLIS: 30s, excellent quality. User chooses based on needs. |
| **QuadriFlow for retopo** | Open source, MIT license, CPU-only. No GPU required. Fast and reliable. |
| **SAM 2 for segmentation** | State-of-the-art, open source, works on any image. Best general-purpose segmentation. |
| **DUSt3R for reconstruction** | Multi-view reconstruction from 2+ images. Most practical for real-world use. |
| **Agent-orchestrated pipelines** | User describes intent. Agent chains tools. Each step is explicit and controllable. |
| **GLB as interchange format** | GLB supports mesh + texture + materials. Standard format for 3D assets. |
| **Incremental phases** | Each feature works independently. 3D generation first, then texture, then retopo, then segmentation. |
| **Reuse gen plugin infrastructure** | No new model loading code. Same GenPlugin base class, same auto-discovery, same UI integration. |

---

## Summary

This plan transforms BFA Coworker into a **full generative 3D pipeline** - from text/image input to production-ready 3D assets, all running locally.

**6 capabilities**:
1. 3D Model Generation (TripoSR, TRELLIS, Hunyuan3D)
2. Texture and Material Generation (StableMaterial, DeepBump)
3. AI Retopology (QuadriFlow)
4. Segmentation and Object Detection (SAM 2, GroundingDINO)
5. Render-to-3D / Neural Rendering (DUSt3R, MASt3R)
6. Full Pipeline Orchestration (agent chains tools end-to-end)

**Implementation**: 6 phases, ~1,550 LOC, ~10 new plugin files + modifications to 4 existing files

**The agent becomes a 3D production assistant** - it can generate models from images, create PBR materials, retopologize sculpts, segment scenes, and reconstruct 3D from photos, all from natural language instructions.
