# BFA Coworker — Tier 5: Full Generative Local Systems Plan

**Date**: 2026-08-06
**Status**: Phase 5a Complete — Foundation Implemented
**Depends on**: Tier 4 (Moodboard Editor + Viewport Overlays) + Phase 2 (External Harness)

---

## Overview

Tier 5 extends BFA Coworker from a text/code agent into a **full generative media studio** — image, video, audio, and text generation — all running **locally** with models downloaded from HuggingFace, integrated across the Moodboard, Sequencer, Image Editor, and Viewport. The user experience is unified: natural language in the chat panel drives everything, with visual workspaces for each media type.

**Reference implementation**: [Pallaidium](https://github.com/tin2tin/Pallaidium) (GPL-3.0) by tin2tin — a generative AI movie studio in Blender's VSE with 1.5k stars, 150 forks, and a mature plugin architecture supporting 50+ models across image/video/audio/text/3D.

**Core insight**: Pallaidium proves the concept works inside Blender. BFA Coworker's advantage is the **agent layer** — instead of manually configuring models and parameters in panels, users describe what they want in natural language and the agent orchestrates the pipeline.

---

## Architecture Strategy: Plugin Bridge, Not Fork

```
BFA Coworker (Agent Layer)
    │
    ├── Built-in Gen Plugins (~10 curated models)
    │   └── FLUX.2 Klein 9B, SDXL Turbo, LTX-2.3, Wan 2.1, Chatterbox, Whisper
    │
    ├── Pallaidium Bridge (detect → delegate)
    │   └── If user has Pallaidium installed, bridge to its 50+ models
    │
    ├── ComfyUI Backend (connect → discover workflows)
    │
    └── Remote Backend (OpenAI /v1 dialect)
```

This mirrors the existing LLM architecture: local (llama.cpp) + remote (OpenAI-compatible) + external harness (MCP clients).

---

## Generation Matrix (Target Coverage)

| Input ↓ / Output → | Image | Video | Audio | Text | 3D |
|---|---|---|---|---|---|
| **Text (prompt)** | ✅ T2I | ✅ T2V | ✅ T2A / TTS | ✅ Chat | ✅ Text-to-3D |
| **Image (reference)** | ✅ I2I | ✅ I2V | — | ✅ Caption | ✅ Image-to-3D |
| **Video (reference)** | ✅ Frame extract | ✅ V2V | ✅ Video-to-Audio | ✅ Caption | — |
| **Audio (reference)** | — | ✅ Audio-to-Video | ✅ Stem split | ✅ Transcribe | — |
| **Moodboard (multi-ref)** | ✅ Multi-ref gen | ✅ Style transfer | — | ✅ Describe | ✅ Concept-to-3D |

---

## Phase 5a: Foundation — Plugin System + Image Generation ✅ DONE

### What Was Built

| Step | Description | Files | LOC |
|---|---|---|---|
| 5a.1 | `GenPlugin` base class with `GenInputs`, `GenInputSpec` (bitflag), `GenUISection` (enum), `GenParams` (defaults), `GenPluginError` | `gen_plugins/base.py` | ~200 |
| 5a.2 | Auto-discovery system — `discover()`, `PLUGIN_REGISTRY`, `get_plugin()`, `get_plugins_by_type()`, `get_enum_items()` | `gen_plugins/__init__.py` | ~160 |
| 5a.3 | `GenController` — model loading/caching, `generate()` sync, `generate_async()` with background worker thread + job queue, `cancel_job()`, `get_job_status()` | `gen_controller.py` | ~350 |
| 5a.4 | `GenState` dataclass — runtime state following `AgentState` pattern | `gen_controller.py` | ~40 |
| 5a.5 | `GenModelPreset` + `GEN_MODEL_PRESETS` — 8 curated presets (4 image, 2 video, 2 audio) with VRAM/disk/capability metadata | `gen_controller.py` | ~80 |
| 5a.6 | Preferences: `gen_backend`, `gen_models_dir`, `gen_output_dir`, `gen_auto_download`, `gen_comfyui_url`, `gen_remote_url`, `gen_remote_key` + Generation section in `draw()` | `preferences.py` | ~100 |
| 5a.7 | FLUX.2 Klein 9B plugin — 4-step distilled, 12 GB VRAM, T2I + I2I + inpaint | `gen_plugins/image/flux_klein_9b.py` | ~120 |
| 5a.8 | SDXL Turbo plugin — single-step, 8 GB VRAM, T2I + I2I | `gen_plugins/image/sdxl_turbo.py` | ~110 |
| 5a.9 | Plugin template — copy-paste starter for new plugins | `gen_plugins/_templates/_template_plugin.py` | ~120 |
| 5a.10 | Sub-package `__init__.py` files for image/video/audio/text | 4 files | ~4 |
| 5a.11 | `shared.py` — `get_gen_controller()` lazy import, `GEN_BACKEND_ITEMS` enum | `shared.py` | ~30 |
| 5a.12 | `__init__.py` — plugin discovery in `register()`, gen_controller cleanup in `unregister()` | `__init__.py` | ~20 |

### Files Created (10 new files)

```
addon/bfa_coworker/
├── gen_controller.py              # Orchestrator: GenConfig/GenState, job queue, presets
├── gen_plugins/
│   ├── __init__.py                # Auto-discovery + PLUGIN_REGISTRY
│   ├── base.py                    # GenPlugin, GenInputs, GenInputSpec, GenUISection, GenParams
│   ├── image/
│   │   ├── __init__.py
│   │   ├── flux_klein_9b.py       # FLUX.2 Klein 9B plugin
│   │   └── sdxl_turbo.py          # SDXL Turbo plugin
│   ├── video/__init__.py
│   ├── audio/__init__.py
│   ├── text/__init__.py
│   └── _templates/
│       └── _template_plugin.py    # Copy-paste template
```

### Files Modified (3 existing files)

```
addon/bfa_coworker/__init__.py     # Plugin discovery + cleanup wiring
addon/bfa_coworker/preferences.py  # 7 gen properties + Generation section in draw()
addon/bfa_coworker/shared.py       # get_gen_controller() + GEN_BACKEND_ITEMS
```

### Architecture Highlights

- **Plugin auto-discovery**: Drop a `.py` file in `gen_plugins/image/` → auto-registered. No manual registration. Same pattern as MCP tools.
- **Config/State split**: `GenConfig` (persisted) / `GenState` (runtime) — same thread-safe pattern as `LLMConfig`/`LLMState`
- **Curated presets**: 8 `GenModelPreset` entries with VRAM/disk/capability metadata
- **Async job queue**: `generate_async()` → background daemon thread → timer-based progress → `get_job_status()`
- **GPU cleanup**: `unload()` on each plugin calls `pipe.to("cpu")` + `gc.collect()` + `torch.cuda.empty_cache()`
- **Opt-in**: Nothing downloaded until user explicitly uses a model

---

## Phase 5b: UI Panels + Sequencer Integration (Est. 600 LOC) ❌ NOT STARTED

| Step | Description | Files | LOC |
|---|---|---|---|
| 5b.1 | `BFACW_PT_gen_panel` — VSE sidebar panel (dynamic, plugin-driven) | `ui_gen.py` (new) | ~200 |
| 5b.2 | `BFACW_PT_gen_image_editor` — Image Editor sidebar panel | `ui_gen.py` | ~80 |
| 5b.3 | Generation operators: `generate`, `queue_generate`, `stop_generation` | `operators_gen.py` (new) | ~120 |
| 5b.4 | Async job queue UI — `GenJob` PropertyGroup, timer-based progress display | `gen_controller.py` | ~150 |
| 5b.5 | Output routing: generated media → VSE strip / Image Editor / Moodboard | `gen_controller.py` | ~50 |

### UI Mockup (VSE Sidebar)

```
┌─ Generative AI ───────────────────────────────────────────┐
│ Output: ○ Image  ● Video  ○ Audio  ○ Text                 │
│                                                             │
│ Model: [FLUX.2 Klein 9B                           ▼]      │
│   VRAM: 12 GB  |  Disk: 14 GB  |  Status: ● Cached         │
│                                                             │
│ ── Input ───────────────────────────────────────────────── │
│ Input: ● Prompts  ○ Strips  ○ Moodboard                    │
│                                                             │
│ Prompt:                                                     │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ A serene sunset over the ocean, golden hour...          ││
│ └─────────────────────────────────────────────────────────┘│
│ Negative:                                                   │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ blurry, low quality, distorted                          ││
│ └─────────────────────────────────────────────────────────┘│
│ Style: [Cinematic                                    ▼]    │
│                                                             │
│ ── Settings ────────────────────────────────────────────── │
│ Resolution: [1280] × [720]  Frames: [120]  Steps: [4]     │
│ Seed: [-1]  Guidance: [3.50]  Strength: [0.80]            │
│                                                             │
│ ── Advanced ────────────────────────────────────────────── │
│ LoRA: [Select Folder...]  Weight: [1.00]                   │
│ ☐ Enable ControlNet  ☐ Enable IP-Adapter                   │
│                                                             │
│ [Generate]  [Queue]  [Stop]                                │
│                                                             │
│ ── Queue ───────────────────────────────────────────────── │
│ #1 PENDING   sunset video        wan_21_t2v     —          │
│ #2 COMPLETED ocean still         flux_klein_9b  00:42s     │
└─────────────────────────────────────────────────────────────┘
```

---

## Phase 5c: MCP Tools + Agent Orchestration (Est. 400 LOC) ❌ NOT STARTED

| Step | Description | Files | LOC |
|---|---|---|---|
| 5c.1 | `generate_image` MCP tool — auto-discovered via `pkgutil.iter_modules()` | `mcp/blmcp/tools/generate_image.py` | ~60 |
| 5c.2 | `generate_video` MCP tool | `mcp/blmcp/tools/generate_video.py` | ~60 |
| 5c.3 | `generate_audio` MCP tool | `mcp/blmcp/tools/generate_audio.py` | ~60 |
| 5c.4 | `list_gen_models` + `get_gen_status` + `cancel_gen_job` management tools | `mcp/blmcp/tools/gen_management.py` | ~80 |
| 5c.5 | Agent controller integration — `run_conversation_turn()` handles gen tool calls | `agent_controller.py` | ~40 |
| 5c.6 | Chat panel generation progress display (inline progress bar) | `ui_chat.py` | ~60 |
| 5c.7 | Moodboard ↔ Generation bridge (send/receive images) | `moodboard.py` (from Tier 4) | ~40 |

### Agent-Orchestrated Generation Flow

```
User: "Create a 5-second video of a sunset over the ocean,
       with gentle waves and seagulls"

Agent loop:
  1. LLM decides: need generate_video tool
  2. Calls generate_video(
       prompt="sunset over ocean, gentle waves, seagulls flying,
               golden hour lighting, cinematic",
       model="wan_21_t2v",
       frames=120,  # 5 sec @ 24fps
       width=1280,
       height=720
     )
  3. Tool returns: {"status": "queued", "job_id": "gen_001"}
  4. Agent: "Generating video... this may take a few minutes."
  5. [Background: model downloads if needed, generation runs]
  6. Timer-based status check → job complete
  7. Agent: "Video generated and added to VSE channel 3.
     Would you like me to add background audio?"
```

---

## Phase 5d: Video + Audio Generation (Est. 500 LOC) ❌ NOT STARTED

| Step | Description | Files | LOC |
|---|---|---|---|
| 5d.1 | LTX-2.3 plugin (video: T2V, I2V, V2V, extend) | `gen_plugins/video/ltx_23.py` | ~150 |
| 5d.2 | Wan 2.1 plugin (video: T2V, I2V) | `gen_plugins/video/wan_21.py` | ~100 |
| 5d.3 | Chatterbox TTS plugin (audio) | `gen_plugins/audio/chatterbox.py` | ~80 |
| 5d.4 | Faster Whisper plugin (transcription) | `gen_plugins/audio/faster_whisper.py` | ~70 |
| 5d.5 | Video I/O utilities (frame extraction, strip rendering, VSE helpers) | `gen_controller.py` | ~100 |

---

## Phase 5e: Advanced Features + Pallaidium Bridge (Est. 500 LOC) ❌ NOT STARTED

| Step | Description | Files | LOC |
|---|---|---|---|
| 5e.1 | Pallaidium bridge backend — detect Pallaidium installation, bridge to its pipelines | `gen_controller.py` | ~100 |
| 5e.2 | ComfyUI backend — connect to local ComfyUI server, discover workflows | `gen_controller.py` | ~100 |
| 5e.3 | Remote backend — OpenAI `/v1/images/generations` + `/v1/audio/speech` | `gen_controller.py` | ~80 |
| 5e.4 | ControlNet + IP-Adapter support in plugin base class | `gen_plugins/base.py` | ~60 |
| 5e.5 | LoRA management UI (folder picker, weight sliders, multi-LoRA) | `ui_gen.py` | ~80 |
| 5e.6 | Batch processing (multiple prompts → multiple outputs) | `operators_gen.py` | ~80 |

---

## Total Estimated: ~2,800 LOC across 12+ new files + modifications to 5 existing files

| Phase | LOC | New Files | Status |
|---|---|---|---|
| 5a: Foundation + Image Gen | ~1,200 | 10 | ✅ **DONE** |
| 5b: UI Panels + Sequencer | ~600 | 2 | ❌ Not started |
| 5c: MCP Tools + Agent | ~400 | 4 | ❌ Not started |
| 5d: Video + Audio | ~500 | 4 | ❌ Not started |
| 5e: Advanced + Bridge | ~500 | 0 | ❌ Not started |

---

## Key Decisions

| Decision | Rationale |
|---|---|
| **Plugin bridge, not fork** | Pallaidium updates weekly with 50+ models. Bridge gives users choice without perpetual rebase. |
| **Adopt Pallaidium's plugin pattern** | Proven with 50+ plugins. Declarative, zero-registration. Fits existing MCP tool auto-discovery pattern. |
| **Built-in plugins are curated (~10)** | Quality over quantity. Power users use Pallaidium bridge or ComfyUI for more. |
| **Opt-in dependency tiers** | Nothing downloaded until user opts in. Base addon stays lean. Matches existing LLM download pattern. |
| **All generation in background threads** | Following existing `threading.Thread(daemon=True)` pattern. Blender UI never blocks. |
| **Moodboard is the visual hub** | Images flow: Chat → Moodboard → Sequencer/Image Editor/Viewport. |

---

## Further Considerations

1. **GPU sharing**: Blender + torch compete for VRAM. Coordinate with LLM — unload LLM from GPU during generation, reload after. Pallaidium's `enable_model_cpu_offload()` is the reference.

2. **"Render in viewport" feasibility**: Real-time AI generation isn't possible yet (SDXL Turbo = ~2s). But "generate in background, overlay result when done" is feasible and matches the Tier 4 viewport overlay architecture.

3. **Pallaidium's `pallaidium_mcp_tools.py`**: Already provides MCP-compatible functions. BFA Coworker could call these directly when Pallaidium is installed, avoiding tool duplication.

4. **Shared HF cache**: Both addons download from HuggingFace. Coordinate via `HF_HOME` env var to avoid duplicate downloads.

5. **Bforartists VSE compatibility**: Test all VSE integration points on Bforartists before committing — strip creation, channel assignment, metadata may differ from Blender.

6. **Pallaidium license**: GPL-3.0. BFA Coworker is also GPL-3.0. The bridge approach (detect-and-delegate, not copy code) avoids license entanglement while still enabling interoperability.

7. **The "Pallaidium is tricky to setup" problem**: BFA Coworker's value-add is making this easier — one-click dependency install, curated presets with clear VRAM/disk requirements, and agent-guided setup.