# BFA Coworker — Tier 5b: Generative 2D (ComfyUI Backend)

**Date**: 2026-09-02
**Status**: Planning — Not Started
**Depends on**: Tier 5a (Gen Plugin Foundation — ✅ DONE), Tier 4d Moodboard MVP (deferred)

---

## 0. Competitor Analysis

### 0.1 ComfyUI-Blender (alexisrolland) — 189 ★, 20 forks, v4.5.1

**Architecture**: Two-part system — ComfyUI custom nodes + Blender addon.

**How it works**:
1. Install custom nodes on ComfyUI side (`BlenderInput*`, `BlenderOutput*`)
2. Build workflow in ComfyUI using those special nodes (they define what shows in Blender)
3. Export workflow as API JSON (File → Export API)
4. Import JSON into Blender addon
5. Blender auto-generates UI from the workflow's input/output nodes
6. User tweaks params in Blender, clicks Run → workflow sent to ComfyUI

**Key insight**: Workflow is BUILT in ComfyUI, USED in Blender. The Blender panel is
auto-generated from the workflow's input/output nodes. No generative UI code needed
on our side — the workflow JSON IS the UI definition.

**Custom node types** (installed on ComfyUI side):
- **Inputs**: `BlenderInputBoolean`, `BlenderInputCombo`, `BlenderInputFloat`,
  `BlenderInputInt`, `BlenderInputString`, `BlenderInputStringMultiline`,
  `BlenderInputSeed`, `BlenderInputSampler`, `BlenderInputLoadImage`,
  `BlenderInputLoadCheckpoint`, `BlenderInputLoadLora`, `BlenderInputLoad3D`,
  `BlenderInputLoadMask`, `BlenderInputLoadDiffusionModel`, `BlenderInputGroup`
- **Outputs**: `BlenderOutputSaveImage`, `BlenderOutputSaveGLB`, `BlenderOutputString`,
  `BlenderOutputDownload3D`

**Pros**: Clean separation of concerns. ComfyUI handles the nodes, Blender handles the UI.
**Cons**: Requires installing custom nodes on ComfyUI side. Two-step workflow
(build in ComfyUI → export → import). No agent integration.

### 0.2 ComfyUI-BlenderAI-node (AIGODLIKE) — 1.5k ★, 104 forks, v2.0.0

**Architecture**: Converts ComfyUI nodes INTO Blender nodes. You see ComfyUI's node
graph INSIDE Blender's own node editor.

**How it works**:
1. Set ComfyUI path and Python path in addon preferences
2. Open "ComfyUI Node Editor" in Blender (a custom node editor space)
3. Add ComfyUI nodes via Shift+A (same as ComfyUI web UI, but inside Blender)
4. Blender-specific nodes: camera input, viewport render as input, grease pencil
   masks, AI materials, mesh import/export
5. Click "Execute Node Tree" → workflow sent to ComfyUI → results come back

**Key Blender-specific nodes**:
- `Input Image`: from directory, render, viewport (real-time refresh)
- `Mask`: from Grease Pencil, object projection, collection projection
- `Mat Image`: texture from object/collection
- `Save Image`: to folder OR replace image in Blender
- `Multiline Textbox`: for long prompts

**Features**: Node tree presets, batch queue, live preview, 70+ tested custom node
packs (IPAdapter, ControlNet, AnimateDiff, ReActor, etc.)

**Critical quote from their README**:
> "Incorrect installation of ComfyUI and third-party custom nodes (the reason 90%
> of users fail to use this tool)"

**Pros**: Full ComfyUI experience inside Blender. Deep Blender integration (camera,
masks, materials). 1.5k stars.
**Cons**: MASSIVE maintenance burden (mirrors ComfyUI's entire node system). Heavy
setup (ComfyUI path, Python path, custom nodes). 90% failure rate on install.

### 0.3 Our Positioning (BFA Coworker)

| | ComfyUI-Blender | BlenderAI-node | BFA Coworker (our plan) |
|---|---|---|---|
| **Setup** | Install custom nodes on ComfyUI | Set paths, install ComfyUI | Auto-detect + optional download |
| **UI** | Auto-generated from workflow | Full node editor in Blender | Simple panels in Image Editor / 3D View |
| **Agent** | None | None | Full agent orchestration |
| **Cold start** | None (manual) | None (90% fail) | **Bootstrap system** (download models) |
| **Workflow model** | Build in ComfyUI, use in Blender | Build in Blender | Both: use curated templates OR build in ComfyUI |
| **Maintenance** | Low (thin bridge) | Very high (mirrors all nodes) | Low (thin bridge + curated templates) |
| **Scope** | Any workflow | Any workflow | Image generation (T2I, I2I, Inpaint, Outpaint) |

**Our advantage**: We're the ONLY solution that handles the cold-start problem. We
auto-detect ComfyUI, download it if needed, download starter models, and provide
curated templates that work out of the box. Plus we have the agent layer.

---

## 1. Overview

Tier 5b adds **ComfyUI Desktop** as a managed backend for 2D image generation inside
Bforartists/Blender. The addon auto-detects (or optionally downloads) ComfyUI,
connects to its REST/WebSocket API, ships curated workflow JSON templates, provides
integrated UI panels in the Image Editor and 3D View sidebar, and enables the agent
to orchestrate generation via natural language.

**Scope**: Image generation only — T2I, I2I, Inpaint, Outpaint. Video deferred.

**Core insight**: ComfyUI is the de facto standard (131k stars). We don't reinvent
model management, VRAM handling, or pipeline loading. We speak ComfyUI's API, it
does the heavy lifting, and we make results seamlessly available inside Blender.

---

## 2. Critical Design Decisions

### 2.1 Q: Do the gen tools need the agent running?

**A: No.** The generation UI panels are completely standalone. They work regardless
of whether the LLM agent is running. The user opens the Image Editor sidebar, types
a prompt, clicks Generate — the image appears. No agent needed.

The agent is an **additional** interaction path. Both paths call the same
`gen_controller.generate()` function:

```
User                    Agent (optional)
  │                        │
  ├── UI Panels ──────────┤
  │   (prompt + params)   │   "Generate an image of..."
  │        │              │        │
  │        ▼              │        ▼
  │   gen_controller.generate()
  │        │
  │        ▼
  │   ComfyUI Client (HTTP + WebSocket)
  │        │
  │        ▼
  └── Result → Image Editor / Moodboard / File
```

The MCP tools (`generate_image`, `edit_image`) are just thin wrappers that call the
same `gen_controller.generate()` function. The agent calls the MCP tools; the UI
panels call the function directly. Same code path, two entry points.

### 2.2 Q: How do we handle the cold-start problem (models, LoRAs, etc.)?

**A: This is the hardest problem and the one we're uniquely positioned to solve.**
Both competitor addons fail here — BlenderAI-node's own README says 90% of users
fail at installation. Our JSON workflow templates reference model filenames like
`flux1-dev.safetensors`, but those files don't exist until the user downloads them.

**Our solution: A three-tier bootstrap system**:

```
Tier 1: "Just Works" (Zero-setup)
  └── ComfyUI Desktop + SDXL Turbo (single model, 7 GB)
      └── "Start Here" button downloads everything in one go
      └── Works on any GPU with 8 GB+ VRAM

Tier 2: "Curated Templates" (Opt-in downloads)
  └── FLUX.1-dev, ControlNet, LoRA styles
      └── Each template declares its required models
      └── "Download Required Models" button per template
      └── Shows disk space, VRAM, download size before starting

Tier 3: "Bring Your Own" (Power users)
  └── Custom workflow JSON import
      └── Scan for model references → show what's missing
      └── "Open in ComfyUI Manager" to download
```

**The bootstrap sequence**:

```
1. User opens Image Editor → Generative panel
2. If ComfyUI not detected: "ComfyUI not found. [Download ComfyUI Desktop]"
   └── Downloads ComfyUI Desktop (~2 GB), installs it
3. If no models: "No models detected. [Download Starter Pack: SDXL Turbo]"
   └── Downloads SDXL Turbo (~7 GB) → ready to generate
4. User can now:
   a. Generate with SDXL Turbo immediately (T2I, I2I)
   b. Download additional models for FLUX, etc.
   c. Import custom workflows
```

**Model download mechanism**: We use ComfyUI's own model download system. ComfyUI
has built-in model management (ComfyUI-Manager, HuggingFace integration). We
trigger it via:
- Telling the user to use ComfyUI-Manager (with a button that opens it)
- OR: downloading models directly to ComfyUI's `models/` directory (like we do for
  llama.cpp models)
- Preferred: Use ComfyUI-Manager's API if available, direct download as fallback

**For our curated templates, each declares its requirements**:

```json
{
    "_bfa_meta": {
        "id": "t2i_flux",
        "requires_models": [
            {
                "filename": "flux1-dev.safetensors",
                "path": "models/checkpoints/",
                "download_url": "https://huggingface.co/...",
                "size_gb": 23.8,
                "required": true
            },
            {
                "filename": "ae.safetensors",
                "path": "models/vae/",
                "download_url": "https://huggingface.co/...",
                "size_gb": 0.3,
                "required": true
            }
        ],
        "requires_custom_nodes": [],
        "optional_loras": [
            {
                "name": "SDXL Film Photography Style",
                "filename": "film_photo.safetensors",
                "path": "models/loras/",
                "download_url": "https://civitai.com/...",
                "size_gb": 0.14
            }
        ]
    }
}
```

The system checks these before offering the template. Missing required models →
"Download Required" button. Missing optional LoRAs → shown as greyed-out options.

### 2.3 Q: How do we handle styles, LoRAs, ControlNets, and compositor integration?

**A: Styles and LoRAs** are handled natively by ComfyUI workflow nodes. We just
need to include them in our templates:

- **Styles**: Use `SDXLPromptStyler` node (from `SDXL_prompt_styler` custom node
  pack) or `CLIPTextEncode` with style prefixes. We ship a "style library" as a
  JSON file with curated style prompts (cinematic, watercolor, anime, etc.).
- **LoRAs**: `LoraLoader` node in the workflow. Template variable `${lora_name}`
  and `${lora_strength}`. We ship a curated list of recommended LoRA URLs.
- **ControlNets**: `ControlNetLoader` + `ControlNetApply` nodes. Most valuable for
  Blender integration: depth maps, normal maps, canny edges from viewport renders.

**Compositor integration**: I recommend we do NOT try to mirror ComfyUI nodes in
Blender's compositor. That's the BlenderAI-node approach and it's a maintenance
nightmare. Instead, we take the **data bridge approach**:

```
Blender Data Sources          ComfyUI Workflow (our JSON templates)
  ├── Viewport render  ──────→ LoadImage node (reference_image)
  ├── Depth pass       ──────→ ControlNet (depth) input
  ├── Normal pass      ──────→ ControlNet (normal) input
  ├── Camera view      ──────→ IP-Adapter reference
  ├── Grease pencil    ──────→ Mask image (inpaint)
  └── Object mask      ──────→ Mask image (inpaint)
```

We render Blender data to temporary images, upload them to ComfyUI, and reference
them in the workflow JSON. This is simple, reliable, and doesn't require any custom
ComfyUI nodes. This is exactly what our plan already does with `reference_image`
and `mask_image` variables.

**For the compositor specifically**: We can add a "Render Input for Dream" operator
that renders the current viewport (or compositor output) to a temp image and feeds
it to the selected workflow. This is a one-button bridge, not a node mirror.

---

## 3. Architecture

```
User (Blender UI)                    User (Agent Chat)
    │                                     │
    ├── Image Editor Sidebar              ├── "Generate an image of..."
    │   ├── Prompt textbox                │
    │   ├── Mode: T2I / I2I / Inpaint     │
    │   ├── Workflow selector             │
    │   ├── Resolution / Steps / Seed     │
    │   └── [Generate] button             │
    │        │                            │
    │        ▼                            ▼
    │   gen_controller.generate()    MCP generate_image tool
    │        │                            │
    │        └────────────┬───────────────┘
    │                     ▼
    │   ComfyUI Client (comfyui_client.py)
    │   ├── Upload reference image (if I2I)
    │   ├── Apply template variables
    │   ├── POST /prompt
    │   ├── WebSocket progress
    │   └── GET /view → download result
    │                     │
    │                     ▼
    │   Blender Output Routing
    │   ├── Image → Image Editor datablock
    │   ├── Image → Moodboard card
    │   └── Image → File on disk
    │
    └── ComfyUI Backend Manager
        ├── detect_comfyui()
        ├── download_comfyui() (optional)
        ├── start_comfyui() / stop_comfyui()
        └── health_check() → connection status
            │
            ▼
    ComfyUI Server (HTTP :8188 + WebSocket)
        ├── POST /prompt           — submit workflow JSON
        ├── WS /ws?clientId=...    — real-time progress & previews
        ├── GET /history/{id}      — retrieve results
        ├── GET /view              — download generated images
        ├── POST /upload/image     — upload reference images (I2I)
        └── GET /system_stats      — GPU, VRAM, version info
```

---

## 4. The ComfyUI Workflow JSON System (Deep Dive)

### 4.1 How ComfyUI's API Works

ComfyUI exposes a REST API on `http://127.0.0.1:8188`. The core flow:

```
POST /prompt  { "prompt": <workflow_json>, "client_id": "..." }
    → 200 { "prompt_id": "abc-123", "number": 42, "node_errors": {} }

WebSocket /ws?clientId=...  (listen for progress)
    ← { "type": "execution_start", "data": { "prompt_id": "abc-123" } }
    ← { "type": "progress", "data": { "value": 5, "max": 20, "node": "8" } }
    ← { "type": "executing", "data": { "node": "8", "prompt_id": "abc-123" } }
    ← { "type": "executed", "data": { "node": "9", "output": { "images": [{
            "filename": "ComfyUI_00001_.png",
            "subfolder": "",
            "type": "output" }] } } }
    ← { "type": "execution_success", "data": { "prompt_id": "abc-123" } }

GET /view?filename=ComfyUI_00001_.png&subfolder=&type=output
    → 200 <image/png binary data>
```

### 4.2 Anatomy of a Workflow JSON

A ComfyUI workflow is a JSON object where each key is a node ID (stringified integer)
and each value is a node definition:

```json
{
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": "flux1-dev.safetensors"
        }
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "a beautiful sunset over mountains",
            "clip": ["4", 1]
        }
    },
    "8": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 42,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0]
        }
    },
    "9": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["8", 0],
            "vae": ["4", 2]
        }
    },
    "10": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "ComfyUI",
            "images": ["9", 0]
        }
    }
}
```

**Key concepts**:

| Concept | Explanation |
|---------|-------------|
| **Node ID** | String key (e.g., `"4"`, `"6"`). Unique. Convention: sequential integers. |
| **`class_type`** | Node class name (`"KSampler"`, `"CLIPTextEncode"`). Defined in ComfyUI's `nodes.py` and custom nodes. |
| **`inputs`** | Dict of input name → value. Scalars (`"euler"`, `42`, `7.0`) or references (`["source_node_id", output_index]`). |
| **Node reference** | `["4", 0]` = "output slot 0 of node 4". `["4", 1]` = "output slot 1". |
| **Output node** | Must be `SaveImage` or `PreviewImage`. API returns the filenames. |

### 4.3 Common Node Classes Reference

**Model Loaders**:

| `class_type` | Key Inputs | Outputs |
|---|---|---|
| `CheckpointLoaderSimple` | `ckpt_name` (filename) | `[0]=MODEL`, `[1]=CLIP`, `[2]=VAE` |
| `UNETLoader` | `unet_name`, `weight_dtype` | `[0]=MODEL` |
| `CLIPLoader` | `clip_name`, `type` | `[0]=CLIP` |
| `VAELoader` | `vae_name` | `[0]=VAE` |
| `DualCLIPLoader` | `clip_name1`, `clip_name2`, `type` | `[0]=CLIP` |
| `LoraLoader` | `model`, `clip`, `lora_name`, `strength_model`, `strength_clip` | `[0]=MODEL`, `[1]=CLIP` |
| `ControlNetLoader` | `control_net_name` | `[0]=CONTROL_NET` |

**Text Encoding**:

| `class_type` | Key Inputs | Outputs |
|---|---|---|
| `CLIPTextEncode` | `text` (string), `clip` (ref) | `[0]=CONDITIONING` |
| `CLIPTextEncodeFlux` | `clip_l`, `t5xxl`, `guidance`, `text` | `[0]=CONDITIONING` |
| `SDXLPromptStyler` | `text_positive`, `text_negative`, `style` | `[0]=text_pos`, `[1]=text_neg` |

**Latent Image**:

| `class_type` | Key Inputs | Outputs |
|---|---|---|
| `EmptyLatentImage` | `width`, `height`, `batch_size` | `[0]=LATENT` |
| `EmptySD3LatentImage` | `width`, `height`, `batch_size` | `[0]=LATENT` |

**Sampling**:

| `class_type` | Key Inputs | Outputs |
|---|---|---|
| `KSampler` | `model`, `seed`, `steps`, `cfg`, `sampler_name`, `scheduler`, `denoise`, `positive`, `negative`, `latent_image` | `[0]=LATENT` |
| `KSamplerAdvanced` | `model`, `noise_seed`, `steps`, `cfg`, `sampler_name`, `scheduler`, `start_at_step`, `end_at_step`, `positive`, `negative`, `latent_image` | `[0]=LATENT` |

**Image I/O**:

| `class_type` | Key Inputs | Outputs |
|---|---|---|
| `LoadImage` | `image` (filename) | `[0]=IMAGE`, `[1]=MASK` |
| `SaveImage` | `images` (ref), `filename_prefix` | (output node) |
| `PreviewImage` | `images` (ref) | (output node) |

**VAE**:

| `class_type` | Key Inputs | Outputs |
|---|---|---|
| `VAEDecode` | `samples` (ref), `vae` (ref) | `[0]=IMAGE` |
| `VAEEncode` | `pixels` (ref), `vae` (ref) | `[0]=LATENT` |

**Image Processing**:

| `class_type` | Key Inputs | Outputs |
|---|---|---|
| `VAEEncodeForInpaint` | `pixels`, `vae`, `mask`, `grow_mask_by` | `[0]=LATENT` |
| `ImageScale` | `image`, `upscale_method`, `width`, `height`, `crop` | `[0]=IMAGE` |
| `ImagePadForOutpaint` | `image`, `left`, `top`, `right`, `bottom`, `feathering` | `[0]=IMAGE`, `[1]=MASK` |
| `SetLatentNoiseMask` | `samples`, `mask` | `[0]=LATENT` |

**ControlNet**:

| `class_type` | Key Inputs | Outputs |
|---|---|---|
| `ControlNetLoader` | `control_net_name` | `[0]=CONTROL_NET` |
| `ControlNetApply` | `conditioning`, `control_net`, `image`, `strength` | `[0]=CONDITIONING` |
| `ControlNetApplyAdvanced` | `positive`, `negative`, `control_net`, `image`, `strength`, `start_percent`, `end_percent` | `[0]=positive`, `[1]=negative` |

### 4.4 Our Template System

Each template is a `.json` file in `addon/bfa_coworker/gen_workflows/`. Templates
use `${variable}` placeholders replaced at runtime. Metadata is stored in a
`_bfa_meta` key that gets stripped before sending to ComfyUI.

**Template variable conventions**:

| Variable | Type | Description | Example |
|---|---|---|---|
| `${prompt}` | string | Positive text prompt | `"a majestic mountain landscape"` |
| `${negative_prompt}` | string | Negative text prompt | `"blurry, low quality"` |
| `${seed}` | int | Random seed (-1 = random) | `42` |
| `${width}` | int | Output width in pixels | `1024` |
| `${height}` | int | Output height in pixels | `1024` |
| `${steps}` | int | Sampling steps | `20` |
| `${cfg}` | float | CFG scale (guidance) | `7.0` |
| `${denoise}` | float | Denoising strength (I2I) | `0.75` |
| `${sampler}` | string | Sampler name | `"euler"` |
| `${scheduler}` | string | Scheduler name | `"normal"` |
| `${ckpt_name}` | string | Model checkpoint filename | `"flux1-dev.safetensors"` |
| `${filename_prefix}` | string | Output filename prefix | `"bfa_gen"` |
| `${reference_image}` | string | Input image filename (I2I) | `"bfa_input_001.png"` |
| `${mask_image}` | string | Mask image filename (inpaint) | `"bfa_mask_001.png"` |
| `${lora_name}` | string | LoRA filename | `"film_photo.safetensors"` |
| `${lora_strength}` | float | LoRA strength | `0.75` |
| `${style}` | string | Style preset name | `"Cinematic"` |
| `${pad_left/right/top/bottom}` | int | Outpaint padding (pixels) | `256` |

**Template application engine** (`apply_template()`):

```python
def apply_template(template: WorkflowTemplate, inputs: dict) -> dict:
    """Fill a workflow template with user inputs.

    1. Load the JSON template
    2. Strip the _bfa_meta key (ComfyUI doesn't need it)
    3. Walk every node's inputs, recursively replacing ${var} placeholders
    4. Handle special cases:
       - ${seed} with value -1 → random.randint(0, 2**32 - 1)
       - ${width}/${height} → ensure they're multiples of 8 (latent requirement)
       - Node references (lists) → pass through unchanged
    5. Return the filled workflow JSON, ready for POST /prompt
    """
```

**I2I/Inpaint/Outpaint pre-flight**:
1. Save reference image/mask as temporary PNG
2. Upload to ComfyUI via `POST /upload/image` with `overwrite=true`
3. Set the filename in the template variables
4. Call `apply_template()` → submit

### 4.5 Curated Templates (6 image workflows)

| File | Mode | Model | VRAM | Custom Nodes |
|---|---|---|---|---|
| `t2i_sdxl.json` | t2i | SDXL 1.0 | 8 GB | none |
| `t2i_flux.json` | t2i | FLUX.1-dev | 12 GB | none |
| `i2i_sdxl.json` | i2i | SDXL 1.0 | 8 GB | none |
| `i2i_flux.json` | i2i | FLUX.1-dev | 12 GB | none |
| `inpaint_flux.json` | inpaint | FLUX.1-dev | 12 GB | none |
| `outpaint_flux.json` | outpaint | FLUX.1-dev | 12 GB | ComfyUI_essentials |

Each template includes `requires_models` and `optional_loras` in `_bfa_meta` for
the bootstrap system.

### 4.6 User Custom Workflows

Users can import their own `.json` files. The system:
1. Validates it's a ComfyUI workflow (has `class_type` nodes)
2. Auto-detects mode from node types (LoadImage → I2I, VAEEncodeForInpaint → inpaint)
3. Scans for `${var}` patterns → presents as UI controls
4. Hardcoded strings → wrapped in `${var}` placeholders with editable default
5. Saved to `gen_workflows/custom/` for auto-discovery

---

## 5. Style, LoRA, and ControlNet Strategy

### 5.1 Styles

**Approach**: Ship a curated style library as a JSON file, not custom nodes.

```json
// gen_workflows/styles.json
{
    "cinematic": {
        "prompt_prefix": "cinematic lighting, 35mm film, anamorphic lens, ",
        "negative_prompt": "video game, 3d render, cartoon, "
    },
    "watercolor": {
        "prompt_prefix": "watercolor painting, wet on wet, artistic, ",
        "negative_prompt": "photorealistic, 3d render, digital art, "
    },
    "anime": {
        "prompt_prefix": "anime style, studio ghibli, makoto shinkai, ",
        "negative_prompt": "photorealistic, western cartoon, 3d, "
    }
    // ... 20+ curated styles
}
```

The UI shows a style dropdown. Selecting a style prepends/append to the prompt.
For SDXL workflows, we also support `SDXLPromptStyler` node with `${style}`.

### 5.2 LoRAs

**Approach**: Templates include optional `LoraLoader` nodes controlled by `${lora_name}`
and `${lora_strength}`. The `_bfa_meta.optional_loras` array lists recommended LoRAs
with download URLs. The UI shows a LoRA dropdown (greyed out if not downloaded).

### 5.3 ControlNets

**Approach**: Templates can include `ControlNetLoader` + `ControlNetApply` nodes.
The input image comes from Blender data — we render viewport depth/normal/canny
passes to temp images and upload them.

**Key use cases for Blender**:
- **Depth ControlNet**: Render depth pass → control image composition
- **Canny Edge ControlNet**: Render viewport with freestyle/edge detect → maintain structure
- **Normal ControlNet**: Render normal pass → control lighting direction
- **IP-Adapter**: Use viewport render as style reference → "match this look"

This is the "data bridge" approach — render Blender data to images, feed to
ControlNet as input. No custom ComfyUI nodes needed.

### 5.4 Compositor Integration

We do NOT mirror ComfyUI nodes in Blender's compositor. Instead, we provide a
"Render Input for Dream" button that:
1. Renders the current viewport or compositor output
2. Saves as a temp image
3. Feeds it to the selected workflow as `reference_image` or ControlNet input

This is a one-click bridge, not a node mirror. Simple, reliable, zero maintenance.

---

## 6. Interface Design — Per-Editor Integration

### 6.0 Design Philosophy: Three-Tier Complexity + Toolshelf/Properties Split

The interface follows two organizing principles:

**Principle 1: Three-tier complexity**. Every editor offers the same feature at
three depths, so new users aren't overwhelmed but professionals have full control:

| Tier | Name | Where | What |
|---|---|---|---|
| **High** | "Just Works" | Toolshelf (T-panel, left sidebar) | Simple buttons. Click and it works. Minimal controls — a prompt dialog, a strength slider, a "Generate" button. |
| **Mid** | "Tune It" | Properties sidebar (N-panel, right sidebar) | Full settings. Resolution, steps, seed, style, LoRA, ControlNet toggles, output routing. |
| **Low** | "Build It" | ComfyUI (external) + import | Custom workflow JSON. Build in ComfyUI, export, import into the addon. Full node-graph control. |

**Principle 2: Toolshelf vs Properties split**. Following the Bforartists/Blender
convention (as seen in the Default Libraries addon and the existing Coworker chat
panel):

- **Toolshelf (T-panel, `bl_region_type = 'TOOLS'`)**: Action buttons. Operators
  the user clicks to DO things. "Generate", "Capture from Camera", "Send to
  Moodboard". Compact, icon-heavy, column layout.
- **Properties sidebar (N-panel, `bl_region_type = 'UI'`)**: Settings and state.
  Prompt textboxes, dropdowns, sliders, progress bars, history. The "tuning"
  interface.

**Principle 3: The 3D View is for 3D models**. The 3D View sidebar does NOT
contain image generation controls. It contains:
- A "Capture from Active Camera" button (feeds the current camera view into the
  generation pipeline)
- A "Dream on Selection" button (applies Dream-generated textures to selected objects)
- Status indicators showing what's happening in the generation pipeline

The actual generation happens in the Image Editor, Compositor, Shader Editor, or
Texture Node Editor — whichever is the natural home for that type of output.

---

### 6.1 The Data Flow: Camera → ControlNet → Output

The core pipeline that ties all editors together:

```
┌─────────────────────────────────────────────────────────────────┐
│                        THE PIPELINE                              │
│                                                                  │
│  Active Camera                                                  │
│       │                                                          │
│       ├── Beauty pass ─────────────────────┐                    │
│       ├── Depth pass ─── ControlNet depth ─┤                    │
│       ├── Normal pass ── ControlNet normal ┤                    │
│       └── Object ID ──── Mask / region ────┤                    │
│                                             │                    │
│                                             ▼                    │
│                                   ComfyUI Workflow               │
│                                   (our JSON templates)           │
│                                             │                    │
│                         ┌───────────────────┼───────────────┐   │
│                         ▼                   ▼               ▼   │
│                   Image Editor        Compositor         File   │
│                   (image datablock)   (render pass)     (disk)  │
│                         │                   │                    │
│                         ▼                   ▼                    │
│                   Accessible           Mixed with                │
│                   anywhere in          native render              │
│                   Blender              passes                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**How it's triggered**:

1. Artist positions the camera, sets up the scene
2. In the **3D View toolshelf**: clicks "Capture from Active Camera"
   → Renders beauty/depth/normal/ObjectID passes to temp images
   → Uploads to ComfyUI
3. In the **Image Editor** or **Compositor**: the generation panel picks up
   the captured passes automatically
4. Artist writes a prompt, adjusts settings, clicks Generate
5. Result appears as an image datablock (Image Editor) or render pass (Compositor)

**The "Capture from Active Camera" button is the bridge**. It lives in the 3D View
toolshelf because that's where the camera lives. But the generation controls live
in the output editors.

---

### 6.2 3D View — Camera Capture + 3D Model Tools

**Where**: 
- **Toolshelf** (T-panel, left): `bl_space_type = 'VIEW_3D'`, `bl_region_type = 'TOOLS'`, tab "Dream"
- **Properties sidebar** (N-panel, right): `bl_space_type = 'VIEW_3D'`, `bl_region_type = 'UI'`, tab "Dream"

**What it does**: Bridge between the 3D scene and the Dream pipeline. Captures camera
data for generation. Applies Dream results to 3D models. Does NOT do image generation
itself.

#### Toolshelf (T-panel) — Action Buttons

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ComfyUI: 🟢 Connected                                      │
│                                                             │
│  ── Camera ─────────────────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   📷 Capture from   │  │   🎬 Capture         │          │
│  │   Active Camera     │  │   Sequence           │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── 3D Model ───────────────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🎨 Dream on       │  │   🧵 Generate       │          │
│  │   Selection         │  │   Texture           │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── Quick Actions ──────────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🖼️ Open Image     │  │   🎞️ Open           │          │
│  │   Editor Gen Panel  │  │   Compositor        │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── Status ─────────────────────────────────────────────── │
│  Last capture: frame 12, 1920×1080, 3 passes                │
│  Active workflow: FLUX.1 T2I (12 GB)                        │
│  Queue: 0 jobs                                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Button behaviors**:

- **📷 Capture from Active Camera**: Renders the current frame from the active
  camera. Saves beauty, depth, normal, and Object ID passes to a temp directory.
  Uploads them to ComfyUI. Sets them as the "current capture" available to all
  other editor panels. This is the bridge — after clicking this, the Image Editor
  and Compositor panels see the captured data.

- **🎬 Capture Sequence**: Same as above but for a frame range. Renders frames
  1-250 (or user-defined range), uploads all passes. Used for animation work.

- **🎨 Dream on Selection**: Opens a simple dialog: "What should this look like?"
  → generates a texture → applies it to the selected object's material. The
  high-level "just make it look good" button.

- **🧵 Generate Texture**: Opens the Texture Node Editor or Shader Editor with
  the generative panel focused, pre-populated with the selected object's material.

- **🖼️ Open Image Editor Gen Panel**: Switches to Image Editor and opens the
  Generative tab in the properties sidebar.

- **🎞️ Open Compositor**: Switches to Compositor editor.

#### Properties Sidebar (N-panel) — Settings

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ComfyUI: 🟢 Connected (RTX 4090, 18 GB free)              │
│                                                             │
│  ── Capture Settings ───────────────────────────────────── │
│  Camera: [Camera ▼]  (active)                               │
│                                                             │
│  Passes to capture:                                         │
│  ☑ Beauty (reference image)                                 │
│  ☑ Depth (ControlNet depth)                                 │
│  ☑ Normal (ControlNet normal)                               │
│  ☐ Object ID (mask by object)                               │
│  ☐ Material ID (mask by material)                           │
│  ☐ Edge detect (ControlNet canny)                           │
│                                                             │
│  Resolution: ● Render resolution  ○ Custom: [1920]×[1080]  │
│  Samples: [32]  (viewport preview quality)                  │
│                                                             │
│  ── ControlNet Defaults ────────────────────────────────── │
│  Depth strength: [0.85 ═══════]                             │
│  Normal strength: [0.50 ═══════]                            │
│  Edge strength: [0.60 ═══════]                              │
│                                                             │
│  ── Output Routing ─────────────────────────────────────── │
│  Send captures to:                                          │
│  ☑ Image Editor (as image datablock)                        │
│  ☑ Compositor (as render pass)                              │
│  ☐ File (//captures/)                                       │
│                                                             │
│  ── Dream on Selection Defaults ────────────────────────── │
│  Workflow: [SDXL Turbo ▼]                                   │
│  Style: [Photorealistic ▼]                                  │
│  Strength: [0.75 ═══════]                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Key behaviors**:

- **Capture settings**: These are the "mid-level" controls. The toolshelf button
  uses these settings. The user can tune which passes to capture, at what
  resolution, with what ControlNet strengths.

- **Output routing**: Captured passes can be sent to multiple destinations
  simultaneously. The Image Editor gets them as image datablocks (accessible
  anywhere). The Compositor gets them wired into the node tree as render passes.

- **Dream on Selection defaults**: Pre-configured settings for the quick "Dream"
  button. The user sets their preferred workflow and style once, then the
  toolshelf button uses them.

---

### 6.3 Image Editor — The Primary Generation Workspace

**Where**:
- **Toolshelf** (T-panel, left): `bl_space_type = 'IMAGE_EDITOR'`, `bl_region_type = 'TOOLS'`, tab "Dream"
- **Properties sidebar** (N-panel, right): `bl_space_type = 'IMAGE_EDITOR'`, `bl_region_type = 'UI'`, tab "Dream"

**What it does**: The main workspace for image generation AND latent image editing.
Results open directly as image datablocks, accessible from anywhere in Blender.
The Image Editor is the natural home for all 2D pixel operations — generation,
inpainting, outpainting, seam fixing, content-aware fill, and brush-masked
regeneration.

**Design philosophy**: The toolshelf is a **2-step workflow** (Prepare → Dream).
The properties sidebar is a **hierarchical stack** of panels (Dream → Status →
Template → Mode Settings → Output & History). Each panel is independently
collapsible. The user sees only what they need at their skill level.

---

#### Toolshelf (T-panel) — The 2-Step Workflow

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ── Step 1: Prepare ────────────────────────────────────── │
│                                                             │
│  Mode:                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ 🎨 T2I  │ │ 🔄 I2I  │ │ 🖌️ Mask │ │ 📐 Out- │      │
│  │  Text→  │ │ Image→  │ │  Inpaint│ │  paint  │      │
│  │  Image  │ │  Image  │ │         │ │         │      │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘      │
│                                                             │
│  Mask Tools (active in Mask Inpaint mode):                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ 🖌️ Paint │ │ 🧹 Erase│ │ ✨ Auto- │ │ 📋 Invert│      │
│  │  Mask   │ │  Mask   │ │  Mask   │ │  Mask   │      │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘      │
│                                                             │
│  Reference (for I2I / Mask modes):                          │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   📷 Use Current    │  │   🎬 Use Captured   │          │
│  │   Image             │  │   Camera Frame      │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── Step 2: Dream ──────────────────────────────────────── │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  What should this look like?                            ││
│  │  ┌─────────────────────────────────────────────────┐    ││
│  │  │ a sci-fi corridor, neon lights, cyberpunk       │    ││
│  │  └─────────────────────────────────────────────────┘    ││
│  │                                                         ││
│  │  Dream strength: [━━━━━━━━━╋━━━━━━━━━] 0.75            ││
│  │  (how much the Dream changes the image)                    ││
│  │                                                         ││
│  │  ┌──────────────────────┐                              ││
│  │  │      ✨ Dream It      │                              ││
│  │  └──────────────────────┘                              ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  ── Quick Fixes ────────────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🧩 Fix Seam       │  │   🩹 Heal Region    │          │
│  │   (paint over seam) │  │   (paint over area) │          │
│  └─────────────────────┘  └─────────────────────┘          │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🎲 4 Variations   │  │   🔁 Re-Dream       │          │
│  │                     │  │   (same seed)       │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── Template ───────────────────────────────────────────── │
│  Active: [FLUX.1 T2I ▼]  [✏️ Edit in ComfyUI]             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**2-Step Workflow**:

**Step 1 — Prepare**: The user sets up WHAT to generate on.
- **Mode buttons**: Select the generation mode. This changes which mask tools
  and reference options are available.
- **Mask Tools** (Mask Inpaint mode): Paint a mask, erase mask, auto-mask
  (detect edges/objects), invert mask. These use Blender's native image paint
  tools in mask mode — the user paints directly on the image.
- **Reference** (I2I/Mask modes): Choose the source image.

**Step 2 — Dream**: The user describes WHAT to generate and HOW strongly.
- **Prompt textbox**: What should appear.
- **Dream strength slider**: The ONLY mandatory control. 0.0 = barely any
  change, 1.0 = complete regeneration. This is the "how heavy things dream"
  control.
- **✨ Dream It**: The big button. This is what the user clicks after Steps 1+2.

**Quick Fixes** — specialized one-click tools:

- **🧩 Fix Seam**: The user paints a stroke over a visible seam or transition
  area. The addon captures the painted region + surrounding context, sends it
  to ComfyUI with a seam-blending workflow, and the Dream generates a seamless
  transition. The painted stroke defines the blend zone.

- **🩹 Heal Region**: The user paints over a blemish, unwanted object, or
  damaged area. The addon sends the painted region + surrounding context to
  ComfyUI with an inpainting workflow. The Dream fills the region with content
  that matches the surroundings.

**How Fix Seam works internally**:

```
1. User paints a stroke over the seam (using Blender's paint tool in mask mode)
2. The stroke is expanded by N pixels (feather radius) to create a blend zone
3. The blend zone + surrounding context (2x the zone size) is cropped and sent
   to ComfyUI as the reference image
4. The mask is the painted stroke (feathered)
5. ComfyUI runs an inpaint workflow with a prompt like:
   "seamless texture, matching colors and patterns, smooth transition"
6. The result is blended back into the original image, only within the blend zone
7. The seam is gone
```

**How Heal Region works internally**:

```
1. User paints over the blemish/object to remove
2. The addon creates a mask from the painted area + padding
3. The mask + surrounding context is sent to ComfyUI
4. ComfyUI runs an inpaint workflow with a prompt like:
   "matching surrounding texture, no seams, natural continuation"
5. The result replaces only the masked region
6. The blemish is gone
```

**Template selector** at the bottom:
- Quick dropdown to switch between installed templates
- **✏️ Edit in ComfyUI** button (see Section 6.3.3 below)

---

#### Properties Sidebar (N-panel) — Hierarchical Panels

The properties sidebar is organized as a **collapsible hierarchy**:

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ▼ Dream ────────────────────────────────────────────────── │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ a majestic mountain landscape at golden hour,           ││
│  │ snow-capped peaks, dramatic clouds,                     ││
│  │ 8k, photorealistic, national geographic                ││
│  └─────────────────────────────────────────────────────────┘│
│  Negative:                                                  │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ blurry, low quality, distorted, watermark               ││
│  └─────────────────────────────────────────────────────────┘│
│  Dream Strength: [━━━━━━━━━╋━━━━━━━━━] 0.75               │
│  [✨ Generate]  [⏹ Stop]  [🎲 Random Seed]                 │
│                                                             │
│  ▶ Status ───────────────────────────────────────────────── │
│                                                             │
│  ▶ Template: FLUX.1 Text-to-Image ───────────────────────── │
│                                                             │
│  ▶ Mode Settings: I2I ───────────────────────────────────── │
│                                                             │
│  ▶ Output & History ─────────────────────────────────────── │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

When expanded, each panel reveals its full controls:

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ▼ Dream ────────────────────────────────────────────────── │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ a majestic mountain landscape at golden hour,           ││
│  │ snow-capped peaks, dramatic clouds,                     ││
│  │ 8k, photorealistic, national geographic                ││
│  └─────────────────────────────────────────────────────────┘│
│  Negative:                                                  │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ blurry, low quality, distorted, watermark               ││
│  └─────────────────────────────────────────────────────────┘│
│  Dream Strength: [━━━━━━━━━╋━━━━━━━━━] 0.75               │
│  [✨ Generate]  [⏹ Stop]  [🎲 Random Seed]                 │
│                                                             │
│  ▼ Status ───────────────────────────────────────────────── │
│  ComfyUI: 🟢 Connected (RTX 4090, 18 GB free)              │
│  Current image: captured_frame_012 (1920×1080)              │
│  ████████████████████░  Step 18/20  ETA: 00:03             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                   [Live Preview]                         ││
│  └─────────────────────────────────────────────────────────┘│
│  [Cancel Generation]                                        │
│                                                             │
│  ▼ Template: FLUX.1 Text-to-Image ───────────────────────── │
│  Workflow: [FLUX.1 Text-to-Image          ▼]  ✓ Ready      │
│  VRAM: 12 GB required (18 GB available)                    │
│  Model: flux1-dev.safetensors ✓                            │
│  VAE: ae.safetensors ✓                                     │
│                                                             │
│  Resolution: [1024] × [1024]  [1:1 ▼]                     │
│  Steps: [20]  CFG: [7.0]  Seed: [42 🎲]                  │
│  Sampler: [euler ▼]  Scheduler: [normal ▼]                │
│                                                             │
│  Style: [Cinematic ▼]  [Edit Styles...]                    │
│  Preview: "cinematic lighting, 35mm film, anamorphic..."   │
│                                                             │
│  LoRA: [Film Photography Style ▼]  Strength: [0.75 ═══]  │
│  ⬇ Download: "Moody Landscape" (144 MB)                    │
│                                                             │
│  [✏️ Edit Template in ComfyUI]  [📥 Import Template...]    │
│                                                             │
│  ▼ Mode Settings: I2I ──────────────────────────────────── │
│  Reference: captured_frame_012.png (1920×1080)              │
│  Denoising Strength: [0.75 ═══════]                        │
│  ☐ Preserve colors (color matching)                        │
│                                                             │
│  ▼ Output & History ─────────────────────────────────────── │
│  Send to: ● Image Editor  ○ Moodboard  ○ File              │
│  Filename prefix: [bfa_gen________]                        │
│                                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│  │ thumbnail │ │ thumbnail │ │ thumbnail │ │ thumbnail │     │
│  │ mountain  │ │ mountain  │ │ forest    │ │ lake      │     │
│  │ seed: 42  │ │ seed: 87  │ │ seed: 5   │ │ seed: 99  │     │
│  │ [Open]    │ │ [Open]    │ │ [Open]    │ │ [Open]    │     │
│  │ [Re-Gen]  │ │ [Re-Gen]  │ │ [Re-Gen]  │ │ [Re-Gen]  │     │
│  │ [→MB]     │ │ [→MB]     │ │ [→MB]     │ │ [→MB]     │     │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘     │
│  [Clear History]  [Export History as JSON]                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Panel hierarchy explained**:

| Panel | Default State | What It Contains | Who Uses It |
|---|---|---|---|
| **Dream** | Always open | Prompt, negative prompt, Dream Strength slider, Generate button | Everyone, every time |
| **Status** | Collapsed | Connection status, progress bar, live preview, cancel button | Expands automatically during generation |
| **Template** | Collapsed | Workflow selector, model status, resolution, steps, seed, sampler, scheduler, style, LoRA, "Edit in ComfyUI" button | Mid-level users tuning settings |
| **Mode Settings** | Collapsed | Mode-specific controls (I2I denoising, inpaint mask settings, outpaint direction) | Changes based on selected mode |
| **Output & History** | Collapsed | Output routing, filename prefix, history grid | Checking past results |

**The Dream panel is always open**. It's the top-level interface. Users who just
want to type a prompt and click Generate never need to expand anything else.

**The Status panel auto-expands** when generation starts and auto-collapses when
it finishes. It shows progress without cluttering the interface.

**The Template panel** is the mid-level tuning interface. Users who want to
change the model, resolution, style, or add a LoRA expand this panel. At the
bottom, the "Edit in ComfyUI" button provides the bridge to the low-level
custom workflow system.

---

#### 6.3.3 "Edit in ComfyUI" — The Template Round-Trip Workflow

This is the low-level access point. Professional users can open the current
template in ComfyUI, edit the node graph visually, and bring the modified
workflow back into the addon.

**The round-trip flow**:

```
     Blender Addon                          ComfyUI (Browser)
     ─────────────                          ─────────────────
                                             
1. [✏️ Edit Template in ComfyUI]            
   │                                         
   ├── Saves template JSON to temp file      
   ├── Opens ComfyUI in browser              
   └── Opens temp file location in OS        
                                             
                                         2. User drags JSON file
                                            into ComfyUI canvas
                                             
                                         3. User edits the node graph
                                            (add/remove nodes, change
                                            models, wire ControlNets,
                                            add LoRA loaders, etc.)
                                             
                                         4. User tests the workflow
                                            (Queue Prompt in ComfyUI)
                                             
                                         5. File → Export (API)
                                            → saves modified JSON
                                             
4. [📥 Import Edited Template...]            
   │                                         
   ├── File picker: select exported JSON     
   ├── Validates the workflow                
   ├── Auto-detects mode from node types     
   ├── Scans for ${var} placeholders         
   ├── Wraps hardcoded strings as ${var}     
   ├── Saves to gen_workflows/custom/        
   └── Template appears in dropdown          
```

**What the user can do in ComfyUI**:
- Switch models (SDXL → FLUX, or any custom model)
- Add ControlNet nodes (depth, normal, canny, IP-Adapter)
- Add LoRA loaders with custom LoRA files
- Change sampler, scheduler, steps
- Add upscaling nodes
- Add style transfer nodes
- Completely redesign the node graph
- Test with Queue Prompt to verify it works

**What happens on import**:
- The addon scans the JSON for `class_type` nodes
- Detects the mode: `LoadImage` → I2I, `VAEEncodeForInpaint` → Inpaint,
  `ImagePadForOutpaint` → Outpaint, otherwise → T2I
- Detects model requirements from `CheckpointLoaderSimple.ckpt_name`
- Detects custom node requirements from unknown `class_type` values
- Scans all string inputs for `${var}` patterns → presents as UI controls
- Hardcoded strings (like a fixed prompt) → wrapped in `${var}` with the
  original value as the default
- The template is saved to `gen_workflows/custom/` and appears in all
  workflow dropdowns

**Template variables the user can use in ComfyUI**:

| Variable | What it controls in the addon UI |
|---|---|
| `${prompt}` | The Dream panel's prompt textbox |
| `${negative_prompt}` | The Dream panel's negative prompt |
| `${seed}` | The seed field (or Random Seed button) |
| `${width}`, `${height}` | Resolution controls |
| `${steps}` | Steps slider |
| `${cfg}` | CFG scale slider |
| `${denoise}` | Dream Strength slider (maps to denoising) |
| `${sampler}`, `${scheduler}` | Sampler/scheduler dropdowns |
| `${ckpt_name}` | Model checkpoint filename |
| `${filename_prefix}` | Output filename prefix |
| `${reference_image}` | The reference image filename (set automatically) |
| `${mask_image}` | The mask image filename (set automatically) |
| `${lora_name}`, `${lora_strength}` | LoRA controls |
| `${style}` | Style preset dropdown |
| `${pad_left}`, `${pad_top}`, `${pad_right}`, `${pad_bottom}` | Outpaint direction |

**If the user doesn't use `${var}` placeholders**, the addon wraps the hardcoded
values. For example, if the ComfyUI workflow has `"text": "masterpiece, best quality"`,
the addon creates a `${prompt}` variable with `"masterpiece, best quality"` as
the default. The user can still edit it in the Dream panel.

**Technical notes**:
- The temp file is saved to `~/.cache/bfa_coworker_comfyui/temp/edit_workflow.json`
- The addon opens both the browser (to ComfyUI) and the file explorer (to the
  temp file location) so the user can drag-and-drop
- On import, the original template is NOT overwritten — it's saved as a new
  custom template. The user can rename it.
- Custom templates are stored in `gen_workflows/custom/` and auto-discovered
  on next Blender session

---

#### 6.3.4 Mask-Based Latent Editing Tools

The Image Editor is the natural home for latent image editing — using brush
masks to control WHERE the Dream generates, combined with prompts to control WHAT
it generates. This is the core workflow: **paint a mask → write a prompt →
generate**.

**Available mask tools** (in the toolshelf Step 1 section):

| Tool | What it does | Use Case |
|---|---|---|
| **🖌️ Paint Mask** | Enter mask paint mode. User paints directly on the image. The painted area becomes the generation region. | "I want to replace just this object" |
| **🧹 Erase Mask** | Erase parts of the mask. Refine the generation region. | "Actually, not that part" |
| **✨ Auto-Mask** | Assisted mask creation. Click an object/region → the Dream detects edges and creates a mask. Uses SAM (Segment Anything) via ComfyUI if available, or simple edge detection as fallback. | "Select this car automatically" |
| **📋 Invert Mask** | Flip the mask. Generate outside the painted area instead of inside. | "Keep this object, change everything else" |
| **🪶 Feather Mask** | Soften the mask edges by N pixels. Prevents hard seams. | "Blend the generated region smoothly" |
| **📐 Expand Mask** | Grow the mask by N pixels. Ensures the Dream has context around the region. | "Give the Dream some surrounding context" |
| **💾 Save Mask** | Save the mask as a separate image datablock for reuse. | "I'll need this mask again later" |
| **📂 Load Mask** | Load a previously saved mask. | "Use the same mask from before" |

**Masked generation workflow**:

```
1. User opens an image in the Image Editor
2. User clicks 🖌️ Paint Mask → enters mask paint mode
3. User paints the area they want to change
4. (Optional) User clicks 🪶 Feather Mask → sets 8px feather
5. User writes a prompt: "red sports car, glossy paint"
6. User sets Dream Strength: 0.85 (heavy regeneration)
7. User clicks ✨ Dream It
8. The addon:
   a. Captures the image + mask
   b. Uploads both to ComfyUI
   c. Submits the inpaint workflow
   d. Downloads the result
   e. Opens the result as a new image datablock (non-destructive)
9. User sees the car replaced, background unchanged
```

**Seam Fix workflow**:

```
1. User has a texture with a visible seam (e.g., from UV mapping)
2. User clicks 🧩 Fix Seam in the toolshelf
3. User paints a stroke over the seam
4. The addon:
   a. Captures the painted stroke + surrounding context
   b. Creates a feathered mask from the stroke
   c. Sends to ComfyUI with a seam-blending workflow
   d. Downloads the result
   e. Blends the result back into the original (only the blend zone)
5. The seam is gone, the texture looks continuous
```

**Heal Region workflow**:

```
1. User has a photo with an unwanted object (e.g., a person in the background)
2. User clicks 🩹 Heal Region in the toolshelf
3. User paints over the unwanted object
4. The addon:
   a. Captures the painted area + surrounding context
   b. Creates a mask from the painted area
   c. Sends to ComfyUI with content-aware fill workflow
   d. Downloads the result
   e. Replaces only the masked region
5. The object is gone, filled with matching background
```

**Key design decisions for mask tools**:

- **Non-destructive by default**: Generated results open as NEW image datablocks.
  The original image is never modified unless the user explicitly chooses
  "Replace Original."
- **Mask is always visible**: The mask is shown as a semi-transparent red overlay
  in the Image Editor. The user always sees what will be regenerated.
- **Feather by default**: Masks are automatically feathered by 4px to prevent
  hard seams. The user can adjust this.
- **Context-aware**: The inpaint workflow always receives the surrounding context
  (2x the mask area) so the Dream can match colors, lighting, and patterns.

---

### 6.4 Properties Editor → View Layer → AOV Panel — Dream Pass Settings

**Where**: `Properties Editor → View Layer Properties → AOV panel` (new sub-panel).

**What it does**: Configures how the 3D scene data maps into the Dream latent
space. This is where the "render properties for Dream" live — NOT in the 3D View
sidebar. The View Layer is the natural home because it already controls render
passes (AOVs, Cryptomatte, etc.). Dream generation is treated as an extension of
the render pipeline.

**Panel layout** (sub-panel of the existing View Layer AOV panel):

```
┌─ View Layer ───────────────────────────────────────────────┐
│                                                             │
│  ── Dream Passes ───────────────────────────────────────── │
│                                                             │
│  ☑ Enable Dream Passes                                      │
│                                                             │
│  ── Latent Inputs (sent to ComfyUI) ────────────────────── │
│                                                             │
│  ☑ Beauty pass → Reference Image                            │
│    AOV: [Combined ▼]                                        │
│                                                             │
│  ☑ Depth pass → ControlNet Depth                            │
│    AOV: [Depth ▼]  Strength: [0.85 ═══════]               │
│                                                             │
│  ☑ Normal pass → ControlNet Normal                          │
│    AOV: [Normal ▼]  Strength: [0.50 ═══════]               │
│                                                             │
│  ☐ Edge pass → ControlNet Canny                             │
│    AOV: [Freestyle ▼]  Strength: [0.60 ═══════]            │
│                                                             │
│  ☐ Object ID → Mask by Object                               │
│    AOV: [CryptoObject ▼]  Object: [Cube ▼]                 │
│                                                             │
│  ☐ Material ID → Mask by Material                           │
│    AOV: [CryptoMaterial ▼]  Material: [Rock_Wall ▼]        │
│                                                             │
│  ── Dream Output (received from ComfyUI) ───────────────── │
│                                                             │
│  ☑ Create "Dream_Beauty" render pass                        │
│    Blend with original: [Mix ▼]  Factor: [0.50 ═══════]   │
│                                                             │
│  ☐ Create "Dream_Depth" render pass                         │
│  ☐ Create "Dream_Normal" render pass                        │
│                                                             │
│  ── Workflow ───────────────────────────────────────────── │
│  [FLUX.1 Text-to-Image                    ▼]                │
│  Prompt:                                                    │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ enhance the lighting, add atmospheric fog,              ││
│  │ cinematic color grade                                   ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  ── Animation ──────────────────────────────────────────── │
│  ☐ Generate per frame                                       │
│    Frame range: [1] to [250]  Step: [1]                     │
│    ☑ Cache results to disk                                  │
│                                                             │
│  [▶ Generate Dream Pass]  [🎬 Generate Animation]          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**How it works**:

1. Artist enables "Dream Passes" in the View Layer
2. They map existing AOVs (or built-in passes) to Dream inputs:
   - Beauty → reference image for I2I
   - Depth → ControlNet depth conditioning
   - Normal → ControlNet normal conditioning
   - Object/Material ID → masks for region-specific generation
3. They configure the Dream output: which render passes to create, how to blend
4. They write a prompt and select a workflow
5. Click "Generate Dream Pass" → the addon:
   a. Renders the configured AOVs for the current frame
   b. Uploads them to ComfyUI
   c. Submits the workflow
   d. Downloads the result
   e. Creates new render passes ("Dream_Beauty", "Dream_Depth", etc.)
6. These passes appear in the Compositor automatically, wired into the Render
   Layers node as additional outputs

**Key behaviors**:

- **AOV mapping**: Uses Blender's existing AOV system. No custom render engine
  needed. Works with EEVEE and Cycles.

- **Per-frame generation**: For animation, generates Dream passes for each frame.
  Results are cached to disk so re-rendering doesn't re-generate.

- **Compositor integration**: The Dream passes appear as additional outputs on the
  Render Layers node. The artist composites them with native nodes.

- **Non-destructive**: The original render passes are untouched. Dream passes are
  additional outputs that can be mixed, masked, or ignored.

**When to use this**:
- "I want Dream-enhanced lighting on my render, but keep the 3D geometry"
- "Apply a consistent style to every frame of my animation"
- "Generate a background plate that matches my 3D camera movement"

---

### 6.5 Compositor — Dream Nodes

**Where**: Compositor node editor.
- **Add menu**: Nodes are added via `Shift+A → Dream → [node type]` following
  Blender's standard node-add convention.
- **Bforartists Add tab**: In Bforartists, Dream nodes appear in the Node Add
  sidebar tab under their own collapsible section appended below existing categories.
- **Toolshelf** (T-panel, left): `bl_space_type = 'NODE_EDITOR'`, `bl_region_type = 'TOOLS'`, tab "Dream" — OPERATIONS only, not node adding.
- **Properties sidebar** (N-panel, right): `bl_space_type = 'NODE_EDITOR'`, `bl_region_type = 'UI'`, tab "Dream" — settings for the selected Dream node.

**What it does**: Adds Dream generation as compositor nodes that plug into the
existing render pipeline. The Dream passes configured in the View Layer (Section 6.4)
appear as outputs on the Render Layers node. The toolshelf provides quick
operations on Dream nodes; the properties sidebar provides the same hierarchical
Dream → Status → Template → Output panel structure as the Image Editor.

**Design philosophy**: We do NOT build a masking system. Blender already has a
world-class compositor masking system — Cryptomatte, ID Mask, Box/Ellipse Mask,
Channel Key, Color Key, Difference Key, and the dedicated Mask Editor. Our Dream
nodes accept mask inputs from ANY of these native Blender sources. The Dream node
just needs an image input and an optional mask input — how those are created is
entirely up to the artist using Blender's native tools.

---

#### 6.5.1 How Dream Nodes Connect to the Compositor

Each Dream node is a **node group** with standardized sockets:

```
┌─────────────────────────────────────────────────────────────┐
│                    Dream Generation Node                     │
│                                                              │
│  Input Sockets:                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  ▶ Image (RGBA)        ← Render Layers beauty pass   │   │
│  │  ▶ Mask (BW, optional) ← Cryptomatte / ID Mask / etc │   │
│  │  ▶ Depth (BW, optional)← Render Layers depth pass    │   │
│  │  ▶ Normal (RGBA, opt)  ← Render Layers normal pass   │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  Output Sockets:                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  ▶ Image (RGBA)         → Mix / Alpha Over / Viewer  │   │
│  │  ▶ Mask (BW)            → The mask, passed through    │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ── Node Properties (shown in N-panel) ──────────────────── │
│                                                              │
│  ▼ Dream ────────────────────────────────────────────────── │
│  Prompt: "enhance lighting, add fog..."                      │
│  Dream Strength: [━━━━━━━━━╋━━━━━━━━━] 0.65                │
│  [▶ Generate]  [⏹ Stop]                                    │
│                                                              │
│  ▶ Status ───────────────────────────────────────────────── │
│  ▶ Template: FLUX.1 I2I ─────────────────────────────────── │
│  ▶ Output ───────────────────────────────────────────────── │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Socket conventions**:

| Socket | Type | Required | What to connect |
|---|---|---|---|
| **Image** | RGBA | Yes | Render Layers beauty pass, or any image source |
| **Mask** | BW | No | Cryptomatte → ID Mask, Box Mask, Ellipse Mask, painted mask from Mask Editor, or any black/white image. White = generate, Black = preserve. |
| **Depth** | BW | No | Render Layers depth pass. Used as ControlNet depth conditioning. |
| **Normal** | RGBA | No | Render Layers normal pass. Used as ControlNet normal conditioning. |

**If no Mask is connected**: The Dream generates over the ENTIRE image (full-frame
I2I or style transfer).

**If a Mask IS connected**: The Dream generates ONLY within the white areas of the
mask. Black areas are preserved from the original image. This is the masked
generation workflow — and it works with ANY mask source in Blender.

---

#### 6.5.2 Mask Sources — Using Blender's Native Masking

The artist creates masks using Blender's existing compositor nodes. Our Dream nodes
simply accept the mask as an input. Here are the most common mask workflows:

**Workflow A: Cryptomatte → ID Mask → Dream Region Replace**

```
Render Layers                    Dream Region Replace
    │                                  │
    ├── Image ────────────────────────▶ Image
    ├── CryptoObject ──▶ ID Mask ────▶ Mask
    │                    (pick "Car")   │
    ├── Depth ────────────────────────▶ Depth
    └── Normal ───────────────────────▶ Normal
                                         │
                                         ▼
                                    Dream-generated car
                                    (only where mask is white)
```

1. Artist adds a Cryptomatte node, picks the "Car" object
2. Cryptomatte outputs a matte → ID Mask node converts it to black/white
3. ID Mask output → Dream node's Mask socket
4. Dream generates a new car ONLY in the masked region
5. Background, lighting, everything else is preserved

**Workflow B: Box Mask → Dream Background**

```
Render Layers                    Dream Background
    │                                  │
    ├── Image ────────────────────────▶ Image
    │                                  │
    Box Mask ─────────────────────────▶ Mask
    (drawn over background)            │
    │                                  │
    Depth ────────────────────────────▶ Depth
                                         │
                                         ▼
                                    Dream-generated background
                                    (only where box mask is white)
```

1. Artist adds a Box Mask node, positions it over the background area
2. Box Mask output → Dream node's Mask socket
3. Dream generates a new background ONLY in the boxed region
4. Foreground character is preserved

**Workflow C: Mask Editor → Painted Mask → Dream Inpaint**

```
Render Layers                    Dream Inpaint
    │                                  │
    ├── Image ────────────────────────▶ Image
    │                                  │
    Mask Node ────────────────────────▶ Mask
    (painted in Mask Editor)           │
                                         │
                                         ▼
                                    Dream-filled region
                                    (only where painted)
```

1. Artist opens the Mask Editor, creates a new mask datablock
2. Paints the area to regenerate using Blender's mask painting tools
3. Adds a Mask node in the compositor, selects the painted mask
4. Mask node output → Dream node's Mask socket
5. Dream fills the painted region

**Workflow D: No Mask → Full-Frame Dream Style**

```
Render Layers                    Dream Style
    │                                  │
    ├── Image ────────────────────────▶ Image
    ├── Depth ────────────────────────▶ Depth
    └── Normal ───────────────────────▶ Normal
                                         │
                                         ▼
                                    Stylized full frame
                                    (depth/normal preserve 3D structure)
```

1. No mask connected → Dream processes the entire frame
2. Depth and Normal passes act as ControlNet conditioning
3. The Dream applies the style while preserving 3D geometry

---

#### 6.5.3 Toolshelf (T-panel) — Operations Only

The toolshelf in the Compositor (and all node editors) is for OPERATIONS, not
for adding nodes. Nodes are added via the standard `Shift+A → Dream` menu
(or the Bforartists Node Add sidebar tab).

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ── Generate ───────────────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   ▶ Generate        │  │   🎬 Generate       │          │
│  │   Selected Node     │  │   All Frames        │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── Manage ─────────────────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🔗 Open Template  │  │   📋 Send Result    │          │
│  │   in ComfyUI        │  │   to Image Editor   │          │
│  └─────────────────────┘  └─────────────────────┘          │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🗑️ Clear Cache   │  │   🔄 Refresh        │          │
│  │   Selected Node     │  │   All Cached Nodes  │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── Status ─────────────────────────────────────────────── │
│  Nodes in tree: 2 (Dream Beauty, Dream Region Replace)     │
│  Last generation: frame 12, 00:08s                          │
│  Cached frames: 12/250                                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Button behaviors**:

- **▶ Generate Selected Node**: Runs generation for the currently selected Dream
  node. Only that node processes — other Dream nodes are skipped.

- **🎬 Generate All Frames**: Runs generation for all Dream nodes across the
  animation frame range. Results are cached per-frame.

- **🔗 Open Template in ComfyUI**: Opens the currently selected Dream node's
  template in the ComfyUI browser for editing.

- **📋 Send Result to Image Editor**: Sends the output of the selected Dream
  node to the Image Editor as a new image datablock.

- **🗑️ Clear Cache Selected Node**: Clears the cached frames for the selected
  Dream node, forcing re-generation on next render.

- **🔄 Refresh All Cached Nodes**: Clears all Dream node caches.

---

#### 6.5.4 Properties Sidebar (N-panel) — Hierarchical Panels

When a Dream node is selected, the properties sidebar shows the same hierarchical
panel structure as the Image Editor:

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  Selected Node: "Dream Beauty Enhance"                      │
│  ComfyUI: 🟢 Connected (RTX 4090, 18 GB free)              │
│                                                             │
│  ▼ Dream ────────────────────────────────────────────────── │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ enhance the lighting, add atmospheric fog,              ││
│  │ cinematic color grade, 8k detail                       ││
│  └─────────────────────────────────────────────────────────┘│
│  Negative:                                                  │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ blurry, low quality, distorted                          ││
│  └─────────────────────────────────────────────────────────┘│
│  Dream Strength: [━━━━━━━━━╋━━━━━━━━━] 0.65               │
│  [▶ Generate]  [⏹ Stop]  [🎲 Random Seed]                 │
│                                                             │
│  ▶ Status ───────────────────────────────────────────────── │
│                                                             │
│  ▼ Template: FLUX.1 I2I ─────────────────────────────────── │
│  Workflow: [FLUX.1 I2I                       ▼]  ✓ Ready   │
│  VRAM: 12 GB required (18 GB available)                    │
│  Model: flux1-dev.safetensors ✓                            │
│                                                             │
│  Resolution: [Match Input ▼]  (auto-detected: 1920×1080)   │
│  Steps: [20]  CFG: [7.0]  Seed: [42 🎲]                  │
│  Sampler: [euler ▼]  Scheduler: [normal ▼]                │
│                                                             │
│  Style: [Cinematic ▼]                                      │
│  LoRA: [None ▼]                                            │
│                                                             │
│  [✏️ Edit Template in ComfyUI]  [📥 Import Template...]    │
│                                                             │
│  ▼ Mask & ControlNet ────────────────────────────────────── │
│  Mask connected: ✓ (from ID Mask "Car")                     │
│  ☐ Invert mask   ☐ Feather mask: [4] px                    │
│                                                             │
│  ControlNet Inputs:                                         │
│  ☑ Depth connected (auto-detected)  Strength: [0.85 ═══]  │
│  ☑ Normal connected (auto-detected) Strength: [0.50 ═══]  │
│  ☐ Edge pass (not connected)                                │
│                                                             │
│  ▼ Output ───────────────────────────────────────────────── │
│  Blend mode: [Mix ▼]  Factor: [0.50 ═══════]              │
│  ☐ Output mask as separate socket                           │
│                                                             │
│  ── Progress ───────────────────────────────────────────── │
│  ████████████████████░  Step 18/20  ETA: 00:03             │
│  [Cancel Generation]                                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Panel hierarchy** (same structure as Image Editor):

| Panel | Default | Content |
|---|---|---|
| **Dream** | Always open | Prompt, negative prompt, Dream Strength, Generate button |
| **Status** | Auto-expands during gen | Connection, progress, live preview, cancel |
| **Template** | Collapsed | Workflow selector, resolution, steps, seed, style, LoRA, "Edit in ComfyUI" |
| **Mask & ControlNet** | Collapsed | Mask status (connected/not, source, invert, feather), ControlNet inputs (auto-detected from connected sockets) |
| **Output** | Collapsed | Blend mode, factor, output routing |

**Key behaviors**:

- **Socket auto-detection**: The properties panel detects which sockets are
  connected. If Depth is connected, the Depth ControlNet toggle is enabled
  automatically. If Mask is connected, the mask section shows the source.

- **Resolution matching**: Default is "Match Input" — the Dream output resolution
  matches the input image resolution automatically. The user can override this.

- **Multiple Dream nodes**: Each Dream node has its own independent settings. Select
  a node → the properties sidebar shows THAT node's settings. Generate only
  affects the selected node (or all nodes if "Generate All Frames" is used).

- **Caching**: Generated frames are cached to disk. If the input image, mask,
  prompt, or seed haven't changed, the cached result is reused. This makes
  compositor re-renders fast.

- **Non-destructive**: The Dream output is a separate image stream. The original
  render passes are untouched. The artist blends with native Mix, Alpha Over,
  etc. nodes.

---

#### 6.5.5 Consistent UX Between Image Editor and Compositor

The Image Editor and Compositor share the same underlying generation engine and
template system. The UX is deliberately consistent:

| Feature | Image Editor | Compositor |
|---|---|---|
| **Dream panel** | Prompt + Dream Strength + Generate | Same (in N-panel when Dream node selected) |
| **Template panel** | Workflow selector, resolution, steps, seed, style, LoRA | Same |
| **"Edit in ComfyUI"** | Opens template in browser for editing | Same |
| **Mask source** | Painted in Image Editor (brush tools) | Any compositor mask node (Cryptomatte, ID Mask, Box Mask, Mask Editor, etc.) |
| **ControlNet inputs** | Captured from camera (Section 6.2) | Connected via node sockets (Depth, Normal) |
| **Output** | New image datablock | Node output socket → compositor tree |
| **Progress** | Progress bar in N-panel | Same |
| **History** | Thumbnail grid in N-panel | Cached frames on disk |
| **Adding a Dream node** | Toolshelf button ("Dream from Text") | Shift+A → Dream → [node type] |

**The key difference**: In the Image Editor, the user paints masks with brushes.
In the Compositor, the user builds masks with nodes. But the Dream generation
experience — the Dream panel, the Template selector, the Dream Strength slider,
the Generate button — is identical.

---

#### 6.5.6 Adding Dream Nodes — Shift+A Menu Structure

Dream nodes are registered into Blender's node system and appear via:

**Standard Blender**: `Shift+A → Dream →`
```
Dream
├── Dream Beauty Enhance
├── Dream Style Transfer
├── Dream Region Replace
├── Dream Background
├── Dream Content Fill
└── Dream Seam Fix
```

**Bforartists Add sidebar tab**: The Dream category appears as a collapsible
section appended below the existing node categories in the Node Add tab.

Each Dream node type is registered with Blender's node system using:
- A unique `bl_idname` (e.g., `DreamBeautyEnhanceNode`)
- A `bl_label` for the menu
- Proper input/output socket definitions
- A reference to its default template workflow

---

#### 6.5.7 Multi-Node Compositor Example

A real-world compositor setup with multiple Dream nodes:

```
Render Layers (1920×1080, frame 12)
    │
    ├── Image ─────────────────────────────────────────────┐
    ├── CryptoObject ──▶ ID Mask ("Car") ──────────────────┤
    ├── CryptoObject ──▶ ID Mask ("Building") ─────────────┤
    ├── Depth ─────────────────────────────────────────────┤
    └── Normal ────────────────────────────────────────────┤
                                                           │
    ┌──────────────────────────────────────────────────────┤
    │                                                      │
    ▼                                                      ▼
┌──────────────────┐                            ┌──────────────────┐
│ Dream Region     │                            │ Dream Region     │
│ Replace          │                            │ Replace          │
│ "Car" →          │                            │ "Building" →      │
│ "red sports car" │                            │ "glass skyscraper"│
│                    │                            │                    │
│ Image: ◀──────────┤                            │ Image: ◀──────────┤
│ Mask:  ◀── ID Mask│                            │ Mask:  ◀── ID Mask│
│ Depth: ◀──────────┤                            │ Depth: ◀──────────┤
│ Normal:◀──────────┤                            │ Normal:◀──────────┤
│                    │                            │                    │
│ Output ────────────┤                            │ Output ────────────┤
└──────────────────┘                            └──────────────────┘
    │                                                      │
    ▼                                                      ▼
┌──────────────────┐                            ┌──────────────────┐
│ Mix (Factor: 1.0)│                            │ Mix (Factor: 1.0)│
│ Original × Dream │                            │ Original × Dream │
└──────────────────┘                            └──────────────────┘
    │                                                      │
    └──────────────────────┬───────────────────────────────┘
                           ▼
                    ┌──────────────────┐
                    │ Dream Style      │
                    │ Transfer         │
                    │ "watercolor      │
                    │  painting style" │
                    │                    │
                    │ Image: ◀──────────┤
                    │ Depth: ◀──────────┤
                    │ Normal:◀──────────┤
                    │ (no mask = full   │
                    │  frame)           │
                    │                    │
                    │ Output ────────────┤
                    └──────────────────┘
                           │
                           ▼
                    ┌──────────────────┐
                    │ Mix (Factor: 0.3)│
                    │ Composite × Style│
                    └──────────────────┘
                           │
                           ▼
                    ┌──────────────────┐
                    │ Composite Output │
                    │ → Viewer         │
                    └──────────────────┘
```

Each Dream node operates independently. The artist can:
- Enable/disable individual nodes (mute toggle)
- Adjust blend factors per node
- Re-order nodes in the compositor tree
- Generate only specific nodes ("Generate Selected Node")
- Cache results per node, per frame

---

### 6.6 Shader Editor — Dream Material Generator

**Where**: Shader Editor.
- **Add menu**: Dream nodes are added via `Shift+A → Dream → [node type]`
  following Blender's standard node-add convention.
- **Bforartists Add tab**: In Bforartists, Dream nodes appear in the Node Add
  sidebar tab under their own collapsible section appended below existing categories.
- **Toolshelf** (T-panel, left): `bl_space_type = 'NODE_EDITOR'`, `bl_region_type = 'TOOLS'`, tab "Dream" — OPERATIONS only, not node adding.
- **Properties sidebar** (N-panel, right): `bl_space_type = 'NODE_EDITOR'`, `bl_region_type = 'UI'`, tab "Dream" — settings for the selected Dream node.

**What it does**: Generates PBR material textures from text descriptions or
reference images. Results are wired into the active material's node tree. The
toolshelf provides operations (generate, update, apply); the properties sidebar
provides the same hierarchical Dream → Status → Template → Output panel structure.

#### Toolshelf (T-panel) — Operations Only

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  Active Material: "Rock_Wall_01"                            │
│                                                             │
│  ── Quick Material ─────────────────────────────────────── │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  What material is this?                                 ││
│  │  ┌─────────────────────────────────────────────────┐    ││
│  │  │ weathered granite rock face, moss in crevices   │    ││
│  │  └─────────────────────────────────────────────────┘    ││
│  │                                                         ││
│  │  ┌─────────────────────┐                               ││
│  │  │     🎨 Make Material │                               ││
│  │  └─────────────────────┘                               ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  ── Quick Actions ──────────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🎨 Generate All   │  │   🖼️ Generate from  │          │
│  │   PBR Maps          │  │   Reference Photo   │          │
│  └─────────────────────┘  └─────────────────────┘          │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🧵 Generate       │  │   🔄 Update         │          │
│  │   Single Map        │  │   Existing Material  │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── Status ─────────────────────────────────────────────── │
│  Last material: "Granite_Wall" (4 maps, 2048², 00:45s)     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### Properties Sidebar (N-panel) — Mid-Level Settings

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  Active Material: "Rock_Wall_01"                            │
│  ComfyUI: 🟢 Connected                                      │
│                                                             │
│  ── Dream Material Generator ───────────────────────────── │
│                                                             │
│  Generate from: ● Text Prompt  ○ Reference Image            │
│                                                             │
│  Prompt:                                                    │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ weathered granite rock face, sharp edges,               ││
│  │ moss in crevices, natural stone wall,                   ││
│  │ PBR material, seamless tileable                         ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  ── Texture Maps to Generate ───────────────────────────── │
│  ☑ Base Color (Albedo)     ☑ Normal (OpenGL)               │
│  ☑ Roughness               ☑ Metallic                       │
│  ☑ Height (Displacement)   ☐ Ambient Occlusion              │
│                                                             │
│  Resolution: [2048 ▼]  ☑ Tileable (seamless)               │
│  Style: [Photorealistic ▼]                                  │
│                                                             │
│  ── Reference Image (optional) ─────────────────────────── │
│  [Load from Image Editor]  [Capture from Viewport]          │
│  No reference image selected                                │
│                                                             │
│  ── Output ─────────────────────────────────────────────── │
│  ☑ Auto-wire into active material                           │
│    Target material: Rock_Wall_01                             │
│  ☐ Save to disk: [//textures/________]                     │
│                                                             │
│  ── Progress ───────────────────────────────────────────── │
│  Generating Normal map... ████████████░░░░  Step 15/20     │
│  ✓ Base Color complete                                      │
│  ✓ Roughness complete                                       │
│  ▶ Normal in progress...                                    │
│  ☐ Metallic pending                                         │
│  ☐ Height pending                                           │
│                                                             │
│  ── Generated Materials ────────────────────────────────── │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                   │
│  │ thumbnail │ │ thumbnail │ │ thumbnail │                   │
│  │ Granite   │ │ Brick     │ │ Wood      │                   │
│  │ 2048²     │ │ 2048²     │ │ 1024²     │                   │
│  │ [Apply]   │ │ [Apply]   │ │ [Apply]   │                   │
│  │ [Edit]    │ │ [Edit]    │ │ [Edit]    │                   │
│  └──────────┘ └──────────┘ └──────────┘                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Key behaviors**:

- **Auto-wiring**: Generated textures are automatically wired into the active
  material's Principled BSDF. Normal maps get a Normal Map node. Displacement
  goes to the Material Output.

- **Tileable option**: Workflow includes "Make Seamless" preprocessing.

- **Reference image mode**: Provide a photo → extract material properties →
  generate tileable PBR maps.

---

### 6.7 Texture Node Editor — Dream Brush Generator

**Where**: Texture Node Editor.
- **Add menu**: Dream nodes are added via `Shift+A → Dream → [node type]`
  following Blender's standard node-add convention.
- **Bforartists Add tab**: In Bforartists, Dream nodes appear in the Node Add
  sidebar tab under their own collapsible section appended below existing categories.
- **Toolshelf** (T-panel, left): `bl_space_type = 'NODE_EDITOR'`, `bl_region_type = 'TOOLS'`, tab "Dream" — OPERATIONS only, not node adding.
- **Properties sidebar** (N-panel, right): `bl_space_type = 'NODE_EDITOR'`, `bl_region_type = 'UI'`, tab "Dream" — settings for the selected Dream node.

**What it does**: Generates textures specifically for Blender's brush system
(sculpt, paint, grease pencil). The Texture Node Editor in Blender is only
accessible to the brush engine — textures created here feed into brush
stamps, paint textures, sculpt alphas, and grease pencil materials. The
generative panel focuses on this workflow: generate a texture → create a
brush that uses it.

#### Toolshelf (T-panel) — Operations Only

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ── Quick Brush Texture ────────────────────────────────── │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  What kind of brush texture?                            ││
│  │  ┌─────────────────────────────────────────────────┐    ││
│  │  │ rocky surface detail, sharp cracks, organic     │    ││
│  │  │ sculpting alpha, seamless tileable              │    ││
│  │  └─────────────────────────────────────────────────┘    ││
│  │                                                         ││
│  │  Brush Type: [Sculpt Alpha ▼]                           ││
│  │                                                         ││
│  │  ┌─────────────────────┐                               ││
│  │  │     🖌️ Make Brush    │                               ││
│  │  └─────────────────────┘                               ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  ── Brush Types ────────────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🪨 Sculpt Alpha   │  │   🎨 Paint Texture  │          │
│  │   (height map)      │  │   (color stamp)     │          │
│  └─────────────────────┘  └─────────────────────┘          │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   ✏️ Grease Pencil  │  │   🧹 Eraser Alpha   │          │
│  │   Brush Stamp       │  │   (invert height)   │          │
│  └─────────────────────┘  └─────────────────────┘          │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🎭 Stencil Map    │  │   🧵 Generate from  │          │
│  │   (RGBA overlay)    │  │   Reference Photo   │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── Brush Management ───────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   📋 Create New     │  │   🔄 Update Active  │          │
│  │   Brush from Texture│  │   Brush Texture     │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ── Status ─────────────────────────────────────────────── │
│  Active brush: "Rock Detail" (Sculpt, 1024² alpha)          │
│  Last generated: "Crack Alpha" (00:06s)                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Button behaviors**:

- **🖌️ Make Brush**: The high-level "just make it work" button. Generates the
  texture AND creates a new brush that uses it. The user describes what they
  want, picks a brush type, clicks — a new brush appears in the active paint/sculpt
  workspace, ready to use.

- **🪨 Sculpt Alpha**: Generates a height map suitable for sculpt mode brushes.
  Black = no displacement, white = full displacement. Auto-sets the texture to
  the active sculpt brush's texture slot.

- **🎨 Paint Texture**: Generates a color texture for texture paint / image paint
  mode. Creates a tiled stamp pattern. Auto-assigns to the active paint brush.

- **✏️ Grease Pencil Brush Stamp**: Generates a stamp texture for grease pencil
  brushes. RGBA with alpha for stamp shape.

- **🧹 Eraser Alpha**: Same as Sculpt Alpha but inverted — generates a texture
  designed for erasing/cutting into surfaces.

- **🎭 Stencil Map**: Generates an RGBA overlay texture for stencil-based painting.
  Includes alpha channel for transparency.

- **🧵 Generate from Reference Photo**: Takes a photo (e.g., of real rock, bark,
  fabric) and converts it into a tileable brush alpha.

- **📋 Create New Brush from Texture**: Takes the generated texture and creates
  a fully configured brush in the active paint/sculpt workspace. Sets the texture,
  strength, falloff, and spacing to sensible defaults.

- **🔄 Update Active Brush Texture**: Replaces the active brush's texture with
  the newly generated one. Useful for iterating: generate → test → tweak prompt →
  regenerate.

#### Properties Sidebar (N-panel) — Mid-Level Settings

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ComfyUI: 🟢 Connected                                      │
│                                                             │
│  ── Dream Brush Texture Generator ──────────────────────── │
│                                                             │
│  Brush Type: [Sculpt Alpha ▼]                               │
│  (Sculpt Alpha / Paint Texture / Grease Pencil Stamp /      │
│   Eraser Alpha / Stencil Map)                               │
│                                                             │
│  Generate from: ● Text Prompt  ○ Reference Photo            │
│                                                             │
│  Prompt:                                                    │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ rocky surface detail, sharp cracks, organic patterns,   ││
│  │ natural stone texture, sculpting alpha,                 ││
│  │ high contrast, seamless tileable                        ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  Resolution: [1024 ▼]  ☑ Tileable (seamless)               │
│  Style: [Photorealistic ▼]                                  │
│                                                             │
│  ── Brush Settings (applied on creation) ────────────────── │
│  Brush Strength: [0.75 ═══════]                             │
│  Brush Falloff: [Smooth ▼]                                  │
│  Spacing: [15]%                                             │
│  ☑ Auto-create brush after generation                       │
│  ☐ Replace active brush texture                             │
│                                                             │
│  ── Sculpt Alpha Options (visible for sculpt types) ─────── │
│  ☑ Auto-levels (normalize contrast)                         │
│  ☐ Invert (black = high, white = low)                       │
│  Mid-level: [0.50 ═══════]                                  │
│                                                             │
│  ── Paint Texture Options (visible for paint types) ─────── │
│  Color mode: ● Grayscale  ○ Color                           │
│  Color hint: [brown, earthy, warm________]                  │
│                                                             │
│  ── Output ─────────────────────────────────────────────── │
│  ☑ Create brush in active workspace                         │
│    Workspace: [Sculpt Mode ▼]                               │
│  ☐ Save to brush library: [//brushes/________]             │
│  ☐ Save texture to disk: [//textures/________]             │
│                                                             │
│  ── Progress ───────────────────────────────────────────── │
│  ████████████████████░  Step 18/20                          │
│                                                             │
│  ── Recent Brushes ─────────────────────────────────────── │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                   │
│  │ thumbnail │ │ thumbnail │ │ thumbnail │                   │
│  │ Rock      │ │ Bark      │ │ Scales    │                   │
│  │ Sculpt    │ │ Sculpt    │ │ Sculpt    │                   │
│  │ 1024²     │ │ 1024²     │ │ 512²      │                   │
│  │ [Use]     │ │ [Use]     │ │ [Use]     │                   │
│  │ [Re-Gen]  │ │ [Re-Gen]  │ │ [Re-Gen]  │                   │
│  └──────────┘ └──────────┘ └──────────┘                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Key behaviors**:

- **Brush-type-aware generation**: The prompt is automatically augmented based on
  brush type. Sculpt Alpha adds "height map, black and white, high contrast."
  Paint Texture adds "color texture, seamless tileable." Grease Pencil adds
  "stamp shape, alpha channel, silhouette."

- **Auto-create brush operator**: After generation, a new brush is created in the
  active paint/sculpt workspace with the texture assigned. The brush name is
  derived from the prompt. All brush settings (strength, falloff, spacing) are
  pre-configured from the properties sidebar defaults.

- **Reference photo mode**: Provide a photo of a real surface (rock, bark, fabric)
  → the workflow extracts the height/texture information → generates a tileable
  brush alpha that matches.

- **Iteration workflow**: Generate → the brush appears → test it on the model →
  tweak the prompt → click "Update Active Brush Texture" → test again. Fast
  feedback loop without leaving the Texture Node Editor.

- **Brush library**: Generated brushes can be saved to a brush library for reuse
  across projects. The texture and brush settings are packaged together.

**When to use this**:
- "I need a rock surface sculpt brush for this cliff"
- "Generate a fabric weave stamp for texture painting"
- "Create a custom grease pencil brush from this bark photo"
- "I want 5 variations of a dragon scale alpha to find the best one"

---

### 6.8 Video Sequencer — Dream Clip Generator (Future Tier)

**Where**: Video Sequencer.
- **Toolshelf** (T-panel, left): `bl_space_type = 'SEQUENCE_EDITOR'`, `bl_region_type = 'TOOLS'`, tab "Dream"
- **Properties sidebar** (N-panel, right): `bl_space_type = 'SEQUENCE_EDITOR'`, `bl_region_type = 'UI'`, tab "Dream"

Deferred to a future tier (video generation). Design included for architectural
completeness.

#### Toolshelf (T-panel) — High-Level Actions

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ── Quick Clip ─────────────────────────────────────────── │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  Describe the video clip:                               ││
│  │  ┌─────────────────────────────────────────────────┐    ││
│  │  │ gentle ocean waves at sunset, slow motion       │    ││
│  │  └─────────────────────────────────────────────────┘    ││
│  │                                                         ││
│  │  Duration: [5.0] sec                                    ││
│  │                                                         ││
│  │  ┌─────────────────────┐                               ││
│  │  │     🎬 Make Clip     │                               ││
│  │  └─────────────────────┘                               ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  ── Quick Actions ──────────────────────────────────────── │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🎬 Text to Video  │  │   🖼️ Image to Video │          │
│  └─────────────────────┘  └─────────────────────┘          │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │   🎞️ Video to Video │  │   📋 Send to        │          │
│  │   (style transfer)  │  │   Image Editor      │          │
│  └─────────────────────┘  └─────────────────────┘          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### Properties Sidebar (N-panel) — Mid-Level Settings

```
┌─ Dream ──────────────────────────────────────────────────┐
│                                                             │
│  ComfyUI: 🟢 Connected  |  ⚠ Video generation is CPU-heavy │
│                                                             │
│  ── Dream Video Generator ───────────────────────────────── │
│                                                             │
│  Mode: [Text-to-Video ▼]                                    │
│  (Text-to-Video / Image-to-Video / Video-to-Video)          │
│                                                             │
│  Prompt:                                                    │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ gentle ocean waves at sunset, slow motion,              ││
│  │ golden hour, seagulls flying, cinematic                 ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  Duration: [5.0] sec  FPS: [24]  Total frames: 120         │
│  Resolution: [1280] × [720]                                 │
│                                                             │
│  ── Reference (I2V/V2V mode) ───────────────────────────── │
│  [Use selected strip]  [Use Image Editor image]             │
│                                                             │
│  ── Output ─────────────────────────────────────────────── │
│  Insert at: ● Playhead  ○ Channel [3]  ○ End of channel    │
│  Channel: [3]                                               │
│                                                             │
│  ── Progress ───────────────────────────────────────────── │
│  Generating frame 87/120  ██████████░░░░░░  ETA: 03:42     │
│  [Cancel]                                                   │
│                                                             │
│  ── Generated Clips ────────────────────────────────────── │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                   │
│  │ thumbnail │ │ thumbnail │ │ thumbnail │                   │
│  │ Ocean     │ │ Forest    │ │ City      │                   │
│  │ 5.0s/24fps│ │ 3.0s/24fps│ │ 10.0s/30fp│                   │
│  │ [Insert]  │ │ [Insert]  │ │ [Insert]  │                   │
│  └──────────┘ └──────────┘ └──────────┘                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

### 6.9 Low-Level: Custom Workflow Templates

The third tier of complexity — for professional users who want full control.

**How it works**:

1. User builds a workflow in ComfyUI's node graph editor
2. File → Export (API) → saves the JSON
3. In any Blender editor's Generative properties sidebar: "Import Workflow" button
4. The addon:
   - Validates the JSON
   - Auto-detects the mode from node types (LoadImage → I2I, VAEEncodeForInpaint → inpaint, etc.)
   - Scans for `${var}` patterns → presents as UI controls in the properties sidebar
   - Hardcoded strings → wrapped in `${var}` placeholders with editable defaults
   - Saves to `gen_workflows/custom/` for auto-discovery
5. The imported workflow appears in the workflow dropdown in all editor panels
6. The user can now use their custom workflow from the toolshelf buttons (high-level)
   or tune its parameters in the properties sidebar (mid-level)

**Template variable conventions** (for workflow authors):

| Variable | Type | Description |
|---|---|---|
| `${prompt}` | string | Positive text prompt |
| `${negative_prompt}` | string | Negative text prompt |
| `${seed}` | int | Random seed (-1 = random) |
| `${width}` | int | Output width |
| `${height}` | int | Output height |
| `${steps}` | int | Sampling steps |
| `${cfg}` | float | CFG scale |
| `${denoise}` | float | Denoising strength (I2I) |
| `${sampler}` | string | Sampler name |
| `${scheduler}` | string | Scheduler name |
| `${ckpt_name}` | string | Model checkpoint filename |
| `${filename_prefix}` | string | Output filename prefix |
| `${reference_image}` | string | Input image filename (I2I) |
| `${mask_image}` | string | Mask image filename (inpaint) |
| `${lora_name}` | string | LoRA filename |
| `${lora_strength}` | float | LoRA strength |
| `${style}` | string | Style preset name |
| `${pad_left/right/top/bottom}` | int | Outpaint padding |

---

### 6.10 Cross-Editor Integration Patterns

**Pattern 1: "Send to..." routing**

Every panel has output routing. Generated content can be sent to:
- **Image Editor**: Opens as a new image datablock (accessible anywhere)
- **Moodboard**: Adds as a card to the active moodboard
- **File**: Saves to disk at the configured output path
- **Shader Editor**: Wires into the active material
- **Compositor**: Creates a new render pass on the Render Layers node
- **VSE**: Inserts as a strip (Sequencer only)

**Pattern 2: "Open in ComfyUI"**

Every panel has an "Open in ComfyUI" button. Opens the ComfyUI web interface
in the browser with the current workflow loaded.

**Pattern 3: Progress feedback**

Every panel shows:
- Progress bar with step count (from WebSocket `progress` messages)
- Elapsed time counter
- Cancel button
- Optional live preview image (if ComfyUI has `--preview-method taesd`)

**Pattern 4: Model status awareness**

Every panel shows:
- ComfyUI connection status (🟢/🟡/🔴)
- GPU name and free VRAM
- Whether the selected workflow's models are downloaded
- Download buttons for missing models with size estimates

**Pattern 5: History + Re-generation**

Every panel maintains a history of the last N generations:
- Thumbnail preview
- Prompt snippet
- Seed value
- Resolution
- "Re-Generate" button (same seed, same params)
- "Send to..." routing buttons

**Pattern 6: Toolshelf = Actions, Properties = Settings**

Following the Bforartists/Blender convention:
- **Toolshelf (T-panel, left, `bl_region_type = 'TOOLS'`)**: Operator buttons.
  Column layout. Icon-heavy. "Do things" — Generate, Capture, Dream, Send.
- **Properties sidebar (N-panel, right, `bl_region_type = 'UI'`)**: Settings
  and state. Prompt textboxes, dropdowns, sliders, progress, history. "Tune
  things."

---

## 7. Milestones

### M1: Bootstrap System — ComfyUI + Starter Models (Week 1-2)

**Goal**: User can install ComfyUI and generate their first image with zero manual
setup.

**Files to create**:
- `addon/bfa_coworker/comfyui_manager.py` (~400 LOC)
  - `ComfyUIConfig`, `ComfyUIState` dataclasses
  - `detect_comfyui()`, `download_comfyui()`, `start_comfyui()`, `stop_comfyui()`
  - `health_check()` → `GET /system_stats`
  - `check_model_exists(model_filename)` → scan ComfyUI's `models/` directory
  - `download_model(url, dest_path)` → direct HTTP download with progress
  - `bootstrap_starter_pack()` → download ComfyUI + SDXL Turbo in one go
- `addon/bfa_coworker/operators_comfyui.py` (~150 LOC)
  - `BFACW_OT_comfyui_detect`, `BFACW_OT_comfyui_download`, `BFACW_OT_comfyui_start`,
    `BFACW_OT_comfyui_stop`, `BFACW_OT_comfyui_open`, `BFACW_OT_comfyui_bootstrap`
- `addon/bfa_coworker/preferences.py` — Enhanced GENERATIVE tab (~200 LOC)

**Verification**:
- `detect_comfyui()` on system with ComfyUI Desktop → finds path, version
- `detect_comfyui()` on clean system → `found: False`
- Bootstrap button → downloads ComfyUI + SDXL Turbo → shows progress → success
- Start/Stop → subprocess lifecycle, health check, status indicator

### M2: ComfyUI Client + Workflow Templates (Week 2-3)

**Goal**: Submit workflows to ComfyUI and get results back.

**Files to create**:
- `addon/bfa_coworker/comfyui_client.py` (~350 LOC)
  - `ComfyUIClient` class: `submit_workflow()`, `wait_for_result()`, `get_progress()`,
    `download_image()`, `upload_image()`, `cancel_job()`, `get_queue()`, `get_history()`,
    `get_system_stats()`, `is_connected()`
- `addon/bfa_coworker/gen_workflows/__init__.py` (~150 LOC)
  - `WorkflowTemplate` dataclass
  - `discover_workflows()`, `get_workflows_by_mode()`, `apply_template()`
  - `check_template_requirements()` → verify models exist, return missing list
  - `get_style_library()` → load styles.json
- `addon/bfa_coworker/gen_workflows/styles.json` (~50 lines)
  - 20+ curated style presets with prompt/negative prefixes
- `addon/bfa_coworker/gen_workflows/t2i_sdxl.json` — SDXL T2I template
- `addon/bfa_coworker/gen_workflows/t2i_flux.json` — FLUX T2I template
- `addon/bfa_coworker/gen_workflows/i2i_sdxl.json` — SDXL I2I template
- `addon/bfa_coworker/gen_workflows/i2i_flux.json` — FLUX I2I template
- `addon/bfa_coworker/gen_workflows/inpaint_flux.json` — FLUX Inpaint template
- `addon/bfa_coworker/gen_workflows/outpaint_flux.json` — FLUX Outpaint template
- `addon/bfa_coworker/gen_plugins/comfyui/__init__.py` — package init
- `addon/bfa_coworker/gen_plugins/comfyui/comfyui_image.py` (~150 LOC)
  - `ComfyUIImagePlugin(GenPlugin)` — wraps the workflow template system
  - `load()` → validates connection, checks model availability
  - `generate()` → upload reference if I2I → apply template → submit → wait → download → save
  - `unload()` → no-op

**Verification**:
- `submit_workflow()` with t2i_sdxl → prompt_id returned
- `wait_for_result()` → image downloaded, saved to disk
- `get_progress()` during generation → step progress
- `cancel_job()` → job cancelled
- All 6 JSON templates parse, `apply_template()` fills correctly
- `check_template_requirements()` → correctly identifies missing models
- Stopped ComfyUI → friendly error message

### M3: UI Panels — Standalone, No Agent Needed (Week 3-4)

**Goal**: Artist-friendly UI panels that work COMPLETELY INDEPENDENTLY of the agent.

**Files to create**:
- `addon/bfa_coworker/ui_gen.py` (~500 LOC)
  - `BFACW_PT_gen_image_editor` — Image Editor sidebar panel
    - Mode selector: T2I / I2I / Inpaint / Outpaint
    - T2I: prompt + negative + style dropdown + resolution + steps + seed
    - I2I: uses current image + prompt + strength slider
    - Inpaint: uses current image (mask from paint mode) + prompt
    - Outpaint: direction buttons (←↑↓→) + pixel amounts + prompt
    - Workflow selector: dropdown filtered by mode, shows VRAM requirement
    - Model status: "✓ SDXL Turbo ready" / "⬇ FLUX.1-dev (23.8 GB)" with download button
    - LoRA dropdown: greyed out if not downloaded, with download link
    - [Generate] button → result opens in Image Editor
    - [Send to Moodboard] button
    - Progress bar with status, cancel button
    - [Open in ComfyUI] button
  - `BFACW_PT_gen_3dview` — 3D View sidebar (compact quick access)
    - Prompt + [Generate] button
    - Result as reference image in viewport
    - Minimal controls; power users use Image Editor panel
  - Moodboard integration — "Generate for Board" / "Variations" buttons
- `addon/bfa_coworker/ui_gen_components.py` (~200 LOC)
  - `draw_prompt_section()`, `draw_resolution_controls()`, `draw_model_selector()`,
    `draw_style_selector()`, `draw_lora_selector()`, `draw_progress_bar()`,
    `draw_output_routing()`, `draw_result_preview()`, `draw_model_status()`
- `addon/bfa_coworker/operators_gen.py` (~250 LOC)
  - `BFACW_OT_gen_t2i`, `BFACW_OT_gen_i2i`, `BFACW_OT_gen_inpaint`,
    `BFACW_OT_gen_outpaint`, `BFACW_OT_gen_stop`, `BFACW_OT_gen_open_in_comfyui`,
    `BFACW_OT_gen_render_input` (viewport → temp image → feed to workflow)
- `addon/bfa_coworker/__init__.py` — register new panels, operators (~40 LOC)

**Design principle**: The UI panel calls `gen_controller.generate()` directly. It
does NOT go through the agent. The agent is a separate path that calls the same
function via MCP tools.

**Verification**:
- Image Editor sidebar: T2I, I2I, Inpaint, Outpaint controls visible
- Model status shows "Ready" / "Not downloaded" with download button
- Prompt + Generate → image appears in Image Editor
- 3D View sidebar: compact quick gen panel works
- Progress bar during generation, cancel button works
- "Send to Moodboard" routes correctly
- Everything works with the agent STOPPED

### M4: MCP Tools + Agent Orchestration (Week 4)

**Goal**: Agent can generate images via natural language. This is an ADDITIONAL
path, not a replacement for the UI.

**Files to create**:
- `mcp/blmcp/tools/generate_image.py` (~60 LOC)
  - Parameters: `prompt`, `negative_prompt`, `workflow`, `width`, `height`,
    `steps`, `cfg`, `seed`, `denoise`, `target`, `reference_image`, `style`, `lora`
  - Returns: `{status, job_id, image_name, output_path}`
  - Calls `gen_controller.generate()` — same function as the UI
- `mcp/blmcp/tools/edit_image.py` (~60 LOC)
  - Parameters: `image_name`, `mode`, `prompt`, `mask_source`, `direction`, `pixels`
  - Returns: `{status, edited_image_name}`
- `mcp/blmcp/tools/list_comfyui_workflows.py` (~40 LOC)
  - Returns: `[{id, name, media_type, mode, model_requirement, min_vram_gb, models_ready}, ...]`
- `mcp/blmcp/tools/get_comfyui_status.py` (~40 LOC)
  - Returns: `{connected, version, gpu, vram_free_gb, queue_size, models_installed}`
- `addon/bfa_coworker/agent_controller.py` — tool domain: "generative" (~20 LOC)
  - Keywords: "generate|create|make an image|render|inpaint|outpaint"

**Image-to-Text (I2T)**: Already built-in via vision LLMs. Document the flow:
screenshot tool → vision LLM → description. No new code.

**Verification**:
- Chat: "Generate an image of a sunset" → agent calls `generate_image` → appears
- Chat: "Remove the background" → agent calls `edit_image` → edited image appears
- `list_comfyui_workflows` → returns all 6 templates with model status
- `get_comfyui_status` → connected, version, GPU info
- UI panels still work independently (agent stopped) — verify same code path

### M5: Polish, Error Handling, Power User (Week 5)

**Goal**: Production-quality UX.

**Tasks**:
- Friendly errors: "ComfyUI not running. Start it?" with action button
- Model missing: "Download FLUX.1-dev (23.8 GB)?" with progress bar
- VRAM warnings before generation
- Real-time step progress + preview images from WebSocket
- Job history: last N generations with prompts/seeds, "Re-generate" button
- Custom workflow import via file picker
- ComfyUI-Manager integration: detect, suggest missing custom nodes
- "Render Input for Dream" operator: viewport → temp image → feed to ControlNet/Reference

**Files**: `ui_gen.py` (additions ~200 LOC), `comfyui_client.py` (additions ~100 LOC)

---

## 8. File Inventory

### New files (22):

| File | LOC | Purpose |
|---|---|---|
| `addon/bfa_coworker/comfyui_manager.py` | ~400 | ComfyUI lifecycle + bootstrap system |
| `addon/bfa_coworker/comfyui_client.py` | ~350 | HTTP/WebSocket client for ComfyUI API |
| `addon/bfa_coworker/operators_comfyui.py` | ~150 | Operators: detect, download, start, stop, bootstrap |
| `addon/bfa_coworker/gen_workflows/__init__.py` | ~150 | Template discovery, WorkflowTemplate, apply_template(), check_requirements() |
| `addon/bfa_coworker/gen_workflows/styles.json` | ~50 | 20+ curated style presets |
| `addon/bfa_coworker/gen_workflows/t2i_sdxl.json` | ~50 | SDXL T2I template |
| `addon/bfa_coworker/gen_workflows/t2i_flux.json` | ~50 | FLUX T2I template |
| `addon/bfa_coworker/gen_workflows/i2i_sdxl.json` | ~60 | SDXL I2I template |
| `addon/bfa_coworker/gen_workflows/i2i_flux.json` | ~60 | FLUX I2I template |
| `addon/bfa_coworker/gen_workflows/inpaint_flux.json` | ~60 | FLUX Inpaint template |
| `addon/bfa_coworker/gen_workflows/outpaint_flux.json` | ~60 | FLUX Outpaint template |
| `addon/bfa_coworker/gen_plugins/comfyui/__init__.py` | ~5 | Package init |
| `addon/bfa_coworker/gen_plugins/comfyui/comfyui_image.py` | ~150 | ComfyUI GenPlugin adapter |
| `addon/bfa_coworker/ui_gen.py` | ~700 | UI panels: Image Editor, 3D View, Moodboard |
| `addon/bfa_coworker/ui_gen_components.py` | ~200 | Shared UI components |
| `addon/bfa_coworker/operators_gen.py` | ~250 | Generation + render-input operators |
| `mcp/blmcp/tools/generate_image.py` | ~60 | MCP tool |
| `mcp/blmcp/tools/edit_image.py` | ~60 | MCP tool |
| `mcp/blmcp/tools/list_comfyui_workflows.py` | ~40 | MCP tool |
| `mcp/blmcp/tools/get_comfyui_status.py` | ~40 | MCP tool |

### Modified files (6):

| File | LOC | Changes |
|---|---|---|
| `addon/bfa_coworker/__init__.py` | ~40 | Register panels, operators, gen_plugins, gen_workflows |
| `addon/bfa_coworker/preferences.py` | ~200 | Enhanced GENERATIVE tab with ComfyUI section |
| `addon/bfa_coworker/gen_controller.py` | ~80 | ComfyUI backend routing in generate() |
| `addon/bfa_coworker/shared.py` | ~30 | New constants, lazy imports |
| `addon/bfa_coworker/agent_controller.py` | ~20 | Tool domain: "generative" |
| `_misc/plan_tier5_generative_local_systems.md` | ~10 | Update Phase 5b status |

---

## 9. Estimated LOC

| Milestone | Description | LOC | New Files |
|---|---|---|---|
| M1 | Bootstrap System (ComfyUI + Models) | ~750 | 3 |
| M2 | Client + Templates + Styles | ~1,050 | 12 |
| M3 | UI Panels (standalone, no agent) | ~1,150 | 3 |
| M4 | MCP Tools + Agent (optional path) | ~240 | 4 |
| M5 | Polish + Error Handling | ~300 | 0 |
| **Total** | | **~3,490** | **22** |

---

## 10. Testing Plan

### M1 Tests
- `detect_comfyui()` on clean system → `found: False`
- `detect_comfyui()` on system with ComfyUI Desktop → finds path, version, type
- Bootstrap button → downloads ComfyUI + SDXL Turbo → SHA-256 verified → success
- Disk space check: < 10 GB free → clear error message
- Start/Stop → subprocess lifecycle, health check, status indicator
- `check_model_exists("sd_xl_turbo_1.0_fp16.safetensors")` → True after download

### M2 Tests
- `submit_workflow()` with t2i_sdxl → prompt_id returned
- `wait_for_result()` → image downloaded, saved to disk
- `get_progress()` during generation → step progress
- `cancel_job()` → job cancelled
- All 6 JSON templates parse correctly
- `apply_template()` with prompt, seed, dimensions → JSON filled correctly
- `check_template_requirements()` → correctly identifies missing models
- `upload_image()` → file appears in ComfyUI's input directory
- I2I flow: upload reference → submit → result matches reference
- Inpaint flow: upload image + mask → result fills masked region
- Stopped ComfyUI → `ComfyUIError` with friendly message

### M3 Tests (ALL with agent STOPPED)
- Image Editor sidebar: all controls visible, mode switching works
- Model status: "Ready" / "Not downloaded" with correct download button
- Style dropdown: 20+ styles, selecting prepends/appends correctly
- LoRA dropdown: greyed out when not downloaded, shows download link
- T2I: prompt + Generate → image appears in Image Editor
- I2I: open image → prompt + strength → Generate → variant appears
- Inpaint: paint mask → prompt → Generate → mask region filled
- Outpaint: direction buttons + pixels → Generate → expanded canvas
- 3D View sidebar: compact panel works
- Progress bar during generation, cancel button works
- "Send to Moodboard" routes correctly
- "Render Input for Dream" → viewport render → feeds to workflow
- Agent never started → everything still works

### M4 Tests
- Agent started, chat: "Generate an image of a sunset" → `generate_image` called → appears
- Agent: "Remove the background from this image" → `edit_image` called → edited appears
- `list_comfyui_workflows` → returns all 6 templates with model status
- `get_comfyui_status` → connected, version, GPU, models installed
- UI panels still work with agent running (same code path, no conflict)

### M5 Tests
- Stopped ComfyUI during generation → "Start ComfyUI?" prompt with action button
- Missing model → "Download FLUX.1-dev (23.8 GB)?" with progress bar
- Tight VRAM → warning before generation
- Job history → last N generations saved with prompts/seeds
- "Re-generate" → same seed, same result
- Custom workflow import → appears in selector, mode auto-detected
- ComfyUI-Manager detected → "Install Missing Nodes" button appears

---

## 11. Key Decisions

| Decision | Rationale |
|---|---|
| **UI panels work WITHOUT the agent** | Gen tools are standalone. Agent is an additional, optional path. Both call the same `gen_controller.generate()`. |
| **Bootstrap system (Tier 1-2-3)** | Solves the #1 pain point (90% failure rate). Tier 1 = zero-setup. Tier 2 = curated with download buttons. Tier 3 = custom. |
| **ComfyUI Desktop as backend** | 131k stars, manages models/VRAM. We integrate, don't compete. |
| **Detect existing + optional download** | Most users have ComfyUI. Auto-download is a convenience. |
| **Workflow templates are plain JSON** | ComfyUI's native format. Users export from ComfyUI, import to us. |
| **`_bfa_meta` key for metadata + model requirements** | Self-contained. One file = one template + its dependencies. |
| **`${var}` placeholders** | Simple, obvious, matches shell conventions. |
| **Image-to-Text is documented, not built** | Agent already has vision LLMs. No redundant tool. |
| **Image-only scope for Tier 5b** | Video deferred. Get image generation right first. |
| **6 curated templates + custom import** | SDXL for broad compatibility, FLUX for quality. |
| **ComfyUI plugin is a GenPlugin subclass** | Reuses existing plugin architecture. Auto-discovery. |
| **Separate manager from client** | Manager = lifecycle. Client = API. Same pattern as llm_manager vs MCP. |
| **No model management in our addon** | ComfyUI handles models. We only handle the connection + bootstrap. |
| **Data bridge, not node mirror** | Render Blender data to images, feed to ComfyUI. No custom ComfyUI nodes needed. |
| **Style library as JSON, not custom nodes** | Simple, extensible. Users can add their own styles. |

---

## 12. Further Considerations

1. **ComfyUI Desktop vs Portable vs Manual**: Desktop is primary. Detection handles all three.

2. **Comfy API key**: ComfyUI has paid API nodes. Pass via `extra_data.api_key_comfy_org`.

3. **GPU sharing with LLM**: If running local LLM, VRAM may conflict. Document; optionally stop
   LLM during generation.

4. **Pallaidium bridge**: Deferred. ComfyUI is the more powerful backend.

5. **Bforartists compatibility**: Image Editor must be tested on Bforartists.

6. **ComfyUI version pinning**: Weekly release cycle. Pin to minimum version. Detect at connect.

7. **Workflow template maintenance**: Curated JSONs may need updating. Version in `_bfa_meta`.

8. **Custom nodes**: Detect missing custom nodes at template load. Show actionable error.

9. **Seed -1 handling**: Generate random seed at submission. Include in result metadata.

10. **Model download URLs**: HuggingFace URLs may change. We should maintain a curated
    list with fallback mirrors. The `requires_models` in `_bfa_meta` can point to multiple
    download URLs.

11. **ComfyUI-Manager API**: If available, prefer it for model downloads. Fall back to
    direct HTTP download. ComfyUI-Manager handles model placement, symlinks, etc.