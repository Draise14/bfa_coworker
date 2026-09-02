# BFA Coworker - Tier 5: Moodboard Storyboarding & Generative Integration

**Date**: 2026-08-27
**Status**: Planning - Not Started
**Depends on**: ~~Tier 4d~~ → **Moodboard MVP now in Tier 5** (Phase 0 / Milestone M1, moved 2026-09-01), Tier 5a (Gen Plugin Foundation)
**Supersedes**: plan_tier4d_moodboard_editor.md (entire MVP folded in as Phase 0)

---

## Table of Contents

1. Vision & Goals
2. What is Deferred from Tier 4d
3. Storyboarding System: Shots, Sequences & Multi-Board
4. Generative Image Pipeline (T2I, I2I, Image Edit)
5. VSE Strip Integration (Animatic Export, Strip Creation)
6. Frame Tools Integration
7. Agent-Orchestrated Story Workflows
8. Data Model Extensions
9. Implementation Plan
10. Summary of Changes
11. Key Decisions

---

## 1. Vision & Goals

### 1.1 The Big Picture

Tier 5 transforms the Tier 4d image moodboard from a static reference board into a **dynamic preproduction studio**. The moodboard becomes the visual hub where:



The agent orchestrates the entire pipeline: generating reference images, arranging them into storyboard shots, linking shots into sequences, and exporting to the VSE as animatic strips.

### 1.2 Core User Stories

| # | Story | Priority |
|---|-------|----------|
| 1 | "Generate 4 variations of this concept, then arrange the best ones into a shot sequence" | CRITICAL |
| 2 | "Take this storyboard and export it as a VSE animatic with timed strips" | CRITICAL |
| 3 | "Edit this reference image - remove the background and adjust colors" | HIGH |
| 4 | "I have 3 boards for 3 different scenes - manage them separately" | HIGH |
| 5 | "Use frame tools to annotate which parts of this image should become 3D" | HIGH |
| 6 | "Generate a video clip from this storyboard shot" | MEDIUM |
| 7 | "Write dialogue for this character based on the moodboard" | MEDIUM |

### 1.3 Design Principles

1. **Generative is a tool, not a requirement** - the moodboard works fully without generation. Generation is an accelerator.
2. **Strip-first export** - the VSE is the final destination. Every storyboard shot maps to a VSE strip.
3. **Agent-driven, not panel-driven** - users describe what they want; the agent orchestrates generation, arrangement, and export.
4. **Non-destructive** - original images are preserved. Edits are layers/modifiers, not permanent changes.
5. **Incremental complexity** - start with image generation, add I2I, then video, then full animatic export.

---

## 2. What is Deferred from Tier 4d

> **Update (2026-09-01):** The *entire* Tier 4d Image Moodboard MVP is now
> deferred to Tier 5 (Phase 0 / Milestone M1 above). The table below lists what
> was already deferred from the original Tier 4d scope — these build on top of
> the MVP in later phases.

| Feature | Why Deferred | Target Phase |
|---------|-------------|--------------|
| Shot sequences and narrative chains | Requires data model design beyond MVP | Phase 1 |
| Storyboard to 3D scene conversion | Requires agent orchestration + camera tools | Phase 3 |
| VSE animatic export | Requires strip creation + timing system | Phase 2 |
| Generation placeholders (storybuilding) | Requires gen plugin foundation (Tier 5a) | Phase 1 |
| Frame tools with markup | Requires Blender 5.x frame tool API | Phase 3 |
| Multi-board management | Requires persistence layer redesign | Phase 1 |
| Style guide extraction | Requires vision model + color analysis | Phase 4 |

---

## 3. Storyboarding System: Shots, Sequences & Multi-Board

### 3.1 Data Model: Shot

A **Shot** is a single storyboard panel - one image with metadata:

```python
@dataclass
class Shot:
    id: str                    # UUID
    board_id: str              # Parent board ID
    image_index: int           # Index in board images list
    order: int                 # Position in sequence (0-based)
    duration: float            # Seconds (default 3.0 for animatic)
    description: str           # "Wide shot of the cabin"
    camera_angle: str          # "wide" | "medium" | "close-up" | "extreme-close" | "over-shoulder" | "pov"
    transition: str            # "cut" | "dissolve" | "fade" | "wipe"
    dialogue: str              # Optional dialogue/narration
    sound_notes: str           # "wind, crackling fire"
    annotations: list[dict]    # Frame tool annotations (position, type, label)
    generated_variants: list[str]  # IDs of generated alternative images
```

### 3.2 Data Model: Sequence

A **Sequence** is an ordered collection of shots - a scene or act:

```python
@dataclass
class Sequence:
    id: str                    # UUID
    board_id: str              # Parent board ID
    name: str                  # "Act 1 - Discovery"
    description: str           # "The protagonist finds the hidden cabin"
    shot_ids: list[str]        # Ordered list of shot IDs
    color: str                 # UI color tag: "#4A90D9"
    mood: str                  # "tense" | "calm" | "energetic" | "melancholy"
    target_duration: float     # Target total duration in seconds
```

### 3.3 Data Model: Board (Extended)

The Tier 4d board is extended with story structure:

```python
@dataclass
class Board:
    id: str                    # UUID
    name: str                  # "Concept Art - Forest Scene"
    images: list[MoodboardImage]  # From Tier 4d
    shots: list[Shot]          # NEW: storyboard shots
    sequences: list[Sequence]  # NEW: shot groupings
    style_guide: dict          # NEW: extracted color palette, mood tags
    created_from: str          # "user" | "generated" | "imported"
```

### 3.4 Multi-Board Management

Users can have multiple boards for different scenes, acts, or projects:



Board switching preserves the current arrangement. Boards are stored as separate Text datablocks:

-  - JSON document
-  - JSON document
-  - master index of all boards

---

## 4. Generative Image Pipeline

### 4.1 Three Modes of Image Generation

| Mode | Input | Output | Plugin | Use Case |
|------|-------|--------|--------|----------|
| **Text-to-Image (T2I)** | Text prompt | New image | FLUX.2 Klein, SDXL Turbo | Generate reference concepts |
| **Image-to-Image (I2I)** | Image + prompt + strength | Modified image | FLUX.2 Klein, SDXL Turbo | Style variations, color edits |
| **Inpaint/Outpaint** | Image + mask + prompt | Filled region | FLUX.2 Klein | Remove objects, extend canvas |

### 4.2 T2I: Generate Reference Concepts

The most common workflow - describe what you want, get reference images:



**Integration point**: The gen controller routes output to the moodboard via:

```python
# In gen_controller.py
def route_output(image: bpy.types.Image, target: str, **kwargs):
    if target == "moodboard":
        board = get_active_board()
        board.images.append(MoodboardImage(
            name=image.name,
            image=image,
            position=kwargs.get("position", (0.5, 0.5)),
            scale=1.0,
            source="generated",
        ))
    elif target == "vse":
        # Phase 2: VSE strip creation
        pass
```

### 4.3 I2I: Style Variations and Color Edits

Take an existing reference image and transform it:



**Strength parameter** controls how much the output deviates from the input:
- : Subtle changes (color grading, minor style shifts)
- : Moderate changes (season, lighting, mood)
- : Major changes (art style, composition)

### 4.4 Inpaint: Remove and Replace Elements

Mask a region and regenerate just that part:



### 4.5 Image Edit Pipeline (Non-Destructive)

All edits are stored as **edit layers** on the original image:

```python
@dataclass
class ImageEdit:
    id: str                    # UUID
    edit_type: str             # "i2i" | "inpaint" | "color_grade" | "upscale"
    prompt: str                # Generation prompt used
    strength: float            # How much the edit deviates
    input_hash: str            # Hash of input image (for cache invalidation)
    output_image: bpy.types.Image  # The result
    created_at: str            # ISO timestamp
    reversible: bool           # Can undo this edit
```

Users can browse edit history, revert to any previous version, or compare side-by-side.

---

## 5. VSE Strip Integration

### 5.1 Animatic Export: Storyboard to VSE

The primary export path - convert storyboard shots into VSE strips with timing:



### 5.2 VSE Strip Creation API

```python
def create_animatic_strip(
    shot: Shot,
    channel: int = 1,
    start_frame: int = 1,
    fps: int = 24,
) -> bpy.types.Sequence:
    """Create a VSE image strip from a storyboard shot."""
    scene = bpy.context.scene
    seq = scene.sequence_editor.sequences.new_image(
        name=f"Shot {shot.order}: {shot.description[:30]}",
        image=shot.image,
        channel=channel,
        frame_start=start_frame,
    )
    seq.frame_final_duration = int(shot.duration * fps)

    # Add transition to next shot
    if shot.transition == "dissolve":
        seq.blend_type = "ALPHA_OVER"
        # Transition duration: 0.5 seconds
        seq.blend_alpha = 0.0  # Start transparent

    return seq
```

### 5.3 Generated Video Strips

For shots that need motion, generate video clips instead of static images:



### 5.4 Audio Strip Integration

Add ambient sound or narration to the animatic:

- **Ambient sound**: Agent generates background audio via Chatterbox/Wan 2.1
- **Narration**: Agent generates TTS from dialogue text
- **Music**: Agent generates background music (future: Tier 6)

Audio strips are placed in VSE channel 2 (or higher for layered audio).

### 5.5 Multi-Channel VSE Layout



---

## 6. Frame Tools Integration

### 6.1 What Are Frame Tools?

Blender 5.x introduces frame tools with markup - annotation-style markup on frames. The moodboard uses these as the grouping mechanism for images.

### 6.2 Frame Tool Usage in Moodboard

Each **Shot** can have frame tool annotations that mark regions of interest:



### 6.3 Annotation-to-3D Pipeline

Frame annotations can be converted to 3D elements:



### 6.4 Integration with the Moodboard Annotation Brush

The Moodboard MVP annotation brush (Phase 0.6) is extended with shot-aware tools:

| Tool | Icon | Purpose |
|------|------|---------|
| Frame | SQUARES | Define camera framing region |
| Arrow | ARROW_RIGHT | Mark motion direction |
| Focus | CIRCLE | Mark focus point for camera |
| Dialogue | TEXT | Place dialogue bubble position |
| Zone | GRIP | Mark area for inpainting/generation |

---

## 7. Agent-Orchestrated Story Workflows

### 7.1 Workflow: Generate and Storyboard



### 7.2 Workflow: Refine and Iterate



### 7.3 Workflow: Export to Sequencer



### 7.4 Workflow: Generate Video Clips



---

## 8. Data Model Extensions

### 8.1 New Fields on Existing Classes

| Class | New Field | Type | Purpose |
|-------|-----------|------|----------|
| MoodboardImage | shots | list[str] | Shot IDs that use this image |
| MoodboardImage | edits | list[ImageEdit] | Edit history |
| MoodboardImage | variants | list[str] | IDs of generated variants |
| Board | shots | list[Shot] | All shots in this board |
| Board | sequences | list[Sequence] | Shot groupings |
| Board | style_guide | dict | Extracted colors, mood tags |

### 8.2 New Classes

| Class | Module | Purpose |
|-------|--------|----------|
| Shot | moodboard.py | Single storyboard panel |
| Sequence | moodboard.py | Ordered shot collection |
| ImageEdit | moodboard.py | Non-destructive edit record |
| Board | moodboard.py | Extended with story structure |
| AnimaticExport | vse_export.py (new) | VSE export configuration |
| FrameAnnotation | moodboard.py | Frame tool annotation data |

### 8.3 Persistence

All new data is stored in the same Text datablock system as Tier 4d:

- Each board is a separate Text datablock with JSON
- Board index tracks all boards
- Shot and sequence data is embedded in the board JSON
- Edit history is embedded per image
- VSE export config is stored in the sequence object

---

## 9. Implementation Plan

### Phase 0: Moodboard MVP (Milestone M1, ~520 LOC) — moved from Tier 4d

> **Update (2026-09-01):** The entire Image Moodboard MVP was moved out of Tier 4
> into Tier 5 (see `plan_tier4_master_coordination.md` §8). This is now the
> **first milestone** of Tier 5 — the foundation everything else builds on.
> `plan_tier4d_moodboard_editor.md` is superseded; its content lives here.

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 0.1 | Moodboard data model + blend-file persistence (Text datablocks) | moodboard.py (new) | ~100 |
| 0.2 | Node Editor canvas shell (GPU takeover) | ui_moodboard.py (new) | ~150 |
| 0.3 | Image card rendering (thumbnails, selection, gizmos) | ui_moodboard.py | ~120 |
| 0.4 | Import UX (file browser drag-drop, paste) | ui_moodboard.py | ~80 |
| 0.5 | Agent context bridge (send selected images to vision LLM) | agent_controller.py | ~50 |
| 0.6 | Annotation support (reuse Node Editor annotation brush) | ui_moodboard.py | ~20 |

**M1 exit criteria**: load images onto a canvas, arrange (drag/scale/pan/zoom),
select and send to agent as vision context, annotate, save/load with the .blend
file, basic linking between images. This is the visual hub for all later phases.

### Phase 1: Storyboarding Core (~350 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 1.1 | Shot and Sequence dataclasses | moodboard.py | ~80 |
| 1.2 | Board extension (shots, sequences, style_guide) | moodboard.py | ~60 |
| 1.3 | Multi-board management (index, switching) | moodboard.py | ~80 |
| 1.4 | Shot creation UI (right-click image -> Create Shot) | ui_moodboard.py | ~60 |
| 1.5 | Sequence editor UI (shot list, reorder, durations) | ui_moodboard.py | ~70 |

### Phase 2: VSE Animatic Export (~300 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 2.1 | create_animatic_strip() function | vse_export.py (new) | ~100 |
| 2.2 | Transition effects (dissolve, fade, wipe) | vse_export.py | ~60 |
| 2.3 | Audio strip integration (narration, ambient) | vse_export.py | ~60 |
| 2.4 | Export operator and UI | ui_moodboard.py | ~50 |
| 2.5 | Multi-channel layout | vse_export.py | ~30 |

### Phase 3: Generative Image Pipeline (~400 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 3.1 | route_output() to moodboard | gen_controller.py | ~50 |
| 3.2 | T2I generation -> moodboard cards | gen_controller.py, ui_moodboard.py | ~80 |
| 3.3 | I2I style variations | gen_controller.py | ~60 |
| 3.4 | Inpaint/outpaint with mask | gen_controller.py | ~80 |
| 3.5 | ImageEdit dataclass and history | moodboard.py | ~50 |
| 3.6 | Edit history UI (revert, compare) | ui_moodboard.py | ~80 |

### Phase 4: Frame Tools & Annotation-to-3D (~250 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 4.1 | Frame annotation tools (Frame, Arrow, Focus, Dialogue, Zone) | ui_moodboard.py | ~80 |
| 4.2 | Annotation-to-camera conversion | moodboard.py | ~80 |
| 4.3 | Annotation-to-empty/path conversion | moodboard.py | ~50 |
| 4.4 | Agent integration (read annotations for scene setup) | agent_controller.py | ~40 |

### Phase 5: Video Generation Integration (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 5.1 | I2V from shot image -> VSE video strip | gen_controller.py | ~80 |
| 5.2 | T2V for new shots | gen_controller.py | ~50 |
| 5.3 | Strip replacement (image -> video) | vse_export.py | ~40 |
| 5.4 | CHOYA buttons for video generation | ui_moodboard.py | ~30 |

### Phase 6: Agent Orchestration & Polish (~200 LOC)

| Step | Feature | Files | LOC |
|------|---------|-------|-----|
| 6.1 | MCP tools: generate_storyboard, export_animatic | mcp/blmcp/tools/ | ~100 |
| 6.2 | Style guide extraction (color palette, mood) | moodboard.py | ~50 |
| 6.3 | Story-to-3D scene conversion (camera, lights) | agent_controller.py | ~50 |

---

## 10. Summary of Changes

| Phase | Feature | New Files | Modified Files | LOC |
|-------|---------|-----------|----------------|-----|
| 0 | **Moodboard MVP (moved from Tier 4d)** | 2 | 1 | ~520 |
| 1 | Storyboarding core (shots, sequences, multi-board) | 0 | 2 | ~350 |
| 2 | VSE animatic export | 1 | 1 | ~300 |
| 3 | Generative image pipeline (T2I, I2I, inpaint) | 0 | 2 | ~400 |
| 4 | Frame tools and annotation-to-3D | 0 | 3 | ~250 |
| 5 | Video generation integration | 0 | 2 | ~200 |
| 6 | Agent orchestration and MCP tools | 1 | 2 | ~200 |
| **Total** | | **3** | **~7** | **~2,220** |

### Files Created

| File | Purpose |
|------|----------|
| addon/bfa_coworker/moodboard.py | Moodboard data model + blend-file persistence (Phase 0) |
| addon/bfa_coworker/ui_moodboard.py | Node Editor canvas GPU takeover, image cards, import UX (Phase 0) |
| addon/bfa_coworker/vse_export.py | VSE animatic export, strip creation, transitions |
| mcp/blmcp/tools/storyboard_tools.py | MCP tools: generate_storyboard, export_animatic |

### Files Modified

| File | Changes |
|------|---------|
| addon/bfa_coworker/moodboard.py | Shot, Sequence, ImageEdit, Board extensions, FrameAnnotation |
| addon/bfa_coworker/ui_moodboard.py | Shot creation, sequence editor, edit history, frame tools, export UI |
| addon/bfa_coworker/gen_controller.py | route_output(), I2I, inpaint, moodboard integration |
| addon/bfa_coworker/agent_controller.py | Vision bridge (Phase 0), story-to-3D, annotation reading, MCP tool handling |
| addon/bfa_coworker/preferences.py | Generation preferences (model, quality, auto-route) |
| addon/bfa_coworker/__init__.py | Register new classes, MCP tools |

---

## 11. Key Decisions

| Decision | Rationale |
|----------|-----------|
| **Shot references images by index** | Lightweight, survives JSON serialization. Image objects are looked up at render time. |
| **Non-destructive edits** | Original images preserved. Users can always revert. Edit history is per-image. |
| **Strip-first VSE export** | The VSE is the standard Blender timeline. Every shot maps to a strip. Transitions are strip effects. |
| **Multi-channel VSE layout** | Visual (ch1), narration (ch2), ambient (ch3), music (ch4). Standard animation pipeline. |
| **Frame annotations as data** | Stored as dicts in Shot.annotations. Rendered by moodboard canvas. Converted to 3D by agent. |
| **Agent-driven, not panel-driven** | Users describe intent. Agent orchestrates generation, arrangement, export. Panels are for inspection. |
| **Incremental phases** | Storyboard first, then VSE export, then generation. Each phase is independently useful. |
| **Leverages Tier 5a gen plugins** | FLUX.2 Klein for T2I/I2I, LTX-2.3 for video. No new model infrastructure needed. |
| **Moodboard MVP moved to Tier 5 (2026-09-01)** | The entire Tier 4d MVP is now Phase 0 / Milestone M1 here. Tier 4 focuses on agent access, Text Editor IDE, and the central editor. The Moodboard's real power comes from generation (5a) + storyboarding — it belongs with its dependencies. |
| **Text datablock persistence** | Same system as Tier 4d. No new file formats. Survives .blend save/load. |
| **CHOYA at every workflow step** | After generation, after storyboard creation, after VSE export. Always offer next logical action. |

---

## Relationship to Existing Tier 5 Plan

This plan extends  which covers:

- Phase 5a: Gen plugin foundation (DONE)
- Phase 5b: VSE sidebar panel + generation UI
- Phase 5c: MCP tools for generation
- Phase 5d: Video + audio generation plugins
- Phase 5e: Pallaidium bridge
- Phase 5f: Competitor UX features (macros, popup chat, etc.)

This plan focuses specifically on the **moodboard-to-storyboard-to-VSE pipeline** with generative integration. It overlaps with Phase 5b (VSE panel) and Phase 5c (MCP tools) but adds the storyboarding layer on top.

**Recommended implementation order**:
1. Phase 5a (DONE) -> Phase 5b (VSE panel) -> This plan Phase 1-2 (storyboard + export)
2. This plan Phase 3 (gen pipeline) overlaps with Phase 5c (MCP tools)
3. This plan Phase 5 (video gen) overlaps with Phase 5d (video plugins)
4. This plan Phase 6 (agent orchestration) overlaps with Phase 5c (agent integration)

