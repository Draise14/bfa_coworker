# BFA Coworker — Tier 4d: The Moodboard Editor — Preproduction Ideation System

**Date**: 2026-08-27 (rev. 3 — blend-file persistence, frames, dual node systems, logic/string bricks, milestones)
**Status**: Planning — Not Started
**Depends on**: Tier 3e (Chat UI Refinement), Tier 4 (Editor Integration — shared `_draw_chat_interface()`), Tier 5a (Gen Plugin Foundation — `gen_controller.py`, `GenPlugin`), existing vision pipeline (`agent_controller.py` `_pending_image`)

**Reference Inspiration**: [Mixar](https://www.mixar.app) — "Moodboard in-canvas: generate references, pin style anchors, and prompt scene changes inside the editor, alongside your work." Also PureRef (reference board UX), Blender's built-in Storyboarding app template, and the Node Editor's annotation system.

---

## Table of Contents

1. [Vision & Goals](#1-vision--goals)
2. [Current State Analysis](#2-current-state-analysis)
3. [Design Proposal: Two Canvas Approaches](#3-design-proposal-two-canvas-approaches)
4. [Data Model](#4-data-model)
5. [GPU Rendering Architecture](#5-gpu-rendering-architecture)
6. [UX Design: Regions, Sidebars & Panels](#6-ux-design-regions-sidebars--panels)
7. [Node Editor Integration: Two Node Systems](#7-node-editor-integration-two-node-systems)
8. [Agent Integration: Vision, Context & Story Workflows](#8-agent-integration-vision-context--story-workflows)
9. [Storyboarding System: Shots, Sequences & Animatics](#9-storyboarding-system-shots-sequences--animatics)
10. [Scene Tooling: From Storyboard to 3D](#10-scene-tooling-from-storyboard-to-3d)
11. [Generation Placeholders: Storybuilding (Tier 5+)](#11-generation-placeholders-storybuilding-tier-5)
12. [Milestones, Branches & Functional Steps](#12-milestones-branches--functional-steps)
13. [Implementation Plan — Phases](#13-implementation-plan--phases)
14. [Summary of Changes](#14-summary-of-changes)
15. [Key Decisions](#15-key-decisions)
16. [Risk Assessment](#16-risk-assessment)
17. [Further Considerations](#17-further-considerations)


> **REVISION (2026-08-27)**: See plan_tier4_master_coordination.md for updated scope.
> - Storyboarding, shot sequences, scene tooling, and generation placeholders **pushed to Tier 5**
> - Tier 4d is now **Image Moodboard MVP only** (~520 LOC)
> - In scope: image cards, canvas, agent vision bridge, annotations, blend-file persistence
> - Deferred: shot sequences, VSE animatic export, frame tools, multi-board management

---

## 1. Vision & Goals

### 1.1 The Big Idea

The Moodboard is **the second self-contained editor** in BFA Coworker (after the Coworker Chat editor). It is a **preproduction ideation system** for animation and design — a color moodboard, design board, reference board, and story blockout system all in one. It is the visual hub where:

```
Reference → Sketching → Building
   │           │           │
   ▼           ▼           ▼
Moodboard → Storyboard → Scene Tooling → 3D Space
   │           │           │
   └─────── Agent (vision, dialogue, storylines) ───────┘
```

**Tier 4d goal**: Make the moodboard a *usable experience* — load images, arrange them with PureRef-style gizmos, annotate with the built-in annotation brush, link images into sequences, and feed everything to the agent for story-driven work.

**Tier 5+ goal (primer)**: The moodboard becomes a **generative ideation area** — the agent generates reference images, storyboard panels, dialogue, and storylines *into* the board, and the board drives scene setup in the 3D space.

### 1.2 Core User Stories

| # | Story | Tier |
|---|-------|------|
| 1 | "Drag 20 reference images onto the board, arrange them like PureRef, zoom/pan the canvas" | 4d |
| 2 | "Draw arrows and notes on the board with the annotation brush" | 4d |
| 3 | "Select an image and ask the agent: 'Describe this image' / 'Create a color grade based on this image in the compositor'" | 4d |
| 4 | "Link 6 images into a shot sequence, then ask the agent to write a script based on the chain" | 4d |
| 5 | "Generate 4 variations of this reference image" | 5+ |
| 6 | "Turn this storyboard into a 3D scene — block out the shots, set up cameras" | 5+ |
| 7 | "Write dialogue for this character based on the moodboard" | 5+ |

### 1.3 Design Principles

1. **Native-first theming** — every panel, button, and widget uses Blender's native UI system. GPU drawing is reserved for the canvas itself (images, gizmos, annotations) and reads colors from `theme_utils.py` (the active theme).
2. **Reuse Blender's built-in systems** — annotation brush, node editor patterns, file browser drag-drop, image datablocks, VSE strips, **and the upcoming frame tools with markup**. Don't reinvent what Blender already does well.
3. **The agent is a first-class citizen** — the board is not just a passive reference board; it's an active agent tool. Selected images become vision context automatically.
4. **Performance is a feature** — thumbnails are cached, downscaled, and GPU-uploaded once. The canvas stays smooth with 100+ images.
5. **Story-first data model** — images, notes, and links are organized into *shots* and *sequences* from day one, so the board can grow into a storyboard without a data migration.
6. **The blend file is the source of truth** — the board lives *inside* the `.blend` file (as Text datablocks), so loading a file brings the moodboard with it. No external sidecar files to lose or forget.
7. **Two node systems, one board** — a *visual* system (images, frames, annotations) and a *narrative* system (shot chains, descriptions, dialogue). They convert into each other and both feed the Sequencer.

---

## 2. Current State Analysis

### 2.1 What Exists Today

| Component | Location | Status |
|---|---|---|
| Vision pipeline (`_pending_image`, `image_url` injection) | `agent_controller.py:597, 3209-3222, 3557-3567` | ✅ Works — screenshots are injected as `image_url` content blocks for vision-capable models |
| Gen plugin foundation (`GenPlugin`, `GenInputs`, `GenController`) | `gen_controller.py`, `gen_plugins/` | ✅ Works — FLUX.2 Klein 9B + SDXL Turbo plugins, async job queue |
| Gen output routing mentions Moodboard | `gen_controller.py:11` | ⚠️ Referenced but not implemented — no `moodboard.py` exists yet |
| Chat UI (`_draw_chat_interface()` shared core) | `ui_chat.py` | ✅ Works — Mode 1/2 from Tier 4 |
| Annotation brush (built-in) | Blender core | ✅ Available in Node Editor, Image Editor, 3D Viewport |
| Storyboarding app template | Blender core | ✅ Exists — Grease Pencil based, 2D workspaces + master timeline |
| File Browser drag-drop | Blender core | ✅ Native — `bpy.ops.wm.path_open`, file select events |
| Image datablocks + thumbnails | Blender core | ✅ `bpy.data.images`, `image.preview` |
| VSE strips (Blender 5.x `strips` API) | Blender core | ✅ Available for animatic export |

### 2.2 What's Missing

1. **No `moodboard.py`** — no data model, no persistence, no canvas.
2. **No moodboard UI** — no panels, no toolshelf, no canvas drawing.
3. **No image → agent context bridge** — the vision pipeline only handles screenshots, not board images.
4. **No storyboard structure** — no shots, sequences, or timeline concepts.
5. **No scene tooling** — no storyboard → 3D bridge.
6. **No generation routing to the board** — gen plugins can't target the moodboard yet.
7. **No blend-file persistence** — the board must live inside the `.blend` file (Text datablocks), not in external JSON files.
8. **No frame tools** — Blender's upcoming frame tools with markup should be the grouping mechanism, not a custom system.

### 2.3 Key Technical Facts (Verified)

- **Vision injection**: `agent_controller.py` injects `{"type": "image_url", "image_url": {"url": data_uri}}` into the last user message when `_pending_image` is set. The moodboard can reuse this exact mechanism — set `_pending_image` to a board image's data URI.
- **Node Editor annotation**: `SpaceNodeEditor.show_annotation` exists (verified in `bpy.types.SpaceNodeEditor`). The annotation brush is available in the Node Editor.
- **File Browser regions**: Has BOTH left (`RGN_TYPE_TOOLS`) and right (`RGN_TYPE_UI`) bars, plus `RGN_TYPE_TOOL_PROPS` and `RGN_TYPE_EXECUTE` — the "full editor" layout.
- **Node Editor regions**: Has native left tools region (`RGN_TYPE_TOOLS`), right sidebar (`RGN_TYPE_UI`), header, and footer.
- **Gen plugin routing**: `gen_controller.py` docstring already says "routes generated media to the appropriate Blender workspace (Sequencer, Image Editor, or Moodboard)" — the moodboard is an anticipated destination.
- **Blender 5.x VSE**: `strips` replaces `sequences` (verified in `skills/blender_53.md`). Animatic export must use the new API.
- **Text datablocks**: `bpy.data.texts` are embedded in the `.blend` file and survive save/load. They are the natural home for the board's JSON document — no external files, no "load the moodboard separately" friction.
- **Frame tools with markup**: Blender is adding frame tools with markup (annotation-style markup on frames). The moodboard should use these as the grouping mechanism — images grouped into frames, frames annotated with markup.
- **Node frames**: `bpy.types.NodeFrame` exists — native node frames can group nodes visually. The moodboard's "frame" concept maps to this.

---

## 3. Design Proposal: Two Canvas Approaches

The user proposed two ways to build the moodboard canvas. Both are viable; this plan recommends a **hybrid** with a clear primary.

### 3.1 Approach A: File Browser as the Editor Shell

**Use the File Browser space type** as the moodboard's home — it has both left and right sidebars, a powerful header and footer, and the standard editor controls.

```
┌──────────────────────────────────────────────────────────────────────┐
│ [Moodboard]  Board: [Concept Art ▼]  [Add] [Import] [Generate]      │ ← Header
├──────────┬──────────────────────────────────────────────┬────────────┤
│  Left    │  Canvas (GPU-drawn: images, gizmos, notes)   │  Right     │
│  Tools   │  ┌────────────────────────────────────────┐  │  Sidebar   │
│  (TOOLS) │  │  [ref1.jpg]        [ref2.jpg]          │  │  (UI)      │
│          │  │                                        │  │            │
│  [Select]│  │        [ref3.jpg]  ← selected          │  │  Selected  │
│  [Move]  │  │                    (gizmo handles)     │  │  Image     │
│  [Scale] │  │                                        │  │  ┌──────┐  │
│  [Rotate]│  │  [ref4.jpg]  ────link────  [ref5.jpg]  │  │  │thumb │  │
│  [Annot] │  │                                        │  │  └──────┘  │
│  [Link]  │  │  [note: "warm lighting"]               │  │  [Describe]│
│  [Group] │  │                                        │  │  [Send to] │
│          │  └────────────────────────────────────────┘  │  [Generate]│
├──────────┴──────────────────────────────────────────────┴────────────┤
│  [Shot 1] [Shot 2] [Shot 3]  ← storyboard strip bar (footer)         │ ← Footer
└──────────────────────────────────────────────────────────────────────┘
```

**Pros:**
- ✅ Both left and right sidebars natively — full editor feel
- ✅ Powerful header and footer — standard editor controls
- ✅ Native file browser drag-drop integration (images from disk)
- ✅ `RGN_TYPE_TOOL_PROPS` for tool-specific settings (brush size, etc.)

**Cons:**
- ❌ The File Browser's center is a native file list — we must fully GPU-takeover the WINDOW region (hide the file list, draw our canvas)
- ❌ File Browser has no native annotation brush in the center
- ❌ The file list UI is complex to hide cleanly across versions

### 3.2 Approach B: Node Editor as the Canvas

**Use the Node Editor space type** — it has a native left toolshelf (with minimal tools), a right sidebar, and — critically — **the annotation brush is already built in**.

```
┌──────────────────────────────────────────────────────────────────────┐
│ [Moodboard]  Board: [Concept Art ▼]  [Add] [Import] [Generate]      │ ← Header
├──────────┬──────────────────────────────────────────────┬────────────┤
│  Left    │  Canvas (Node Editor backdrop + GPU overlays)│  Right     │
│  Tools   │  ┌────────────────────────────────────────┐  │  Sidebar   │
│  (TOOLS) │  │  [ref1.jpg]        [ref2.jpg]          │  │  (UI)      │
│          │  │                                        │  │            │
│  [Select]│  │        [ref3.jpg]  ← selected          │  │  Selected  │
│  [Move]  │  │                    (gizmo handles)     │  │  Image     │
│  [Scale] │  │                                        │  │  ┌──────┐  │
│  [Annot] │  │  [ref4.jpg]  ────link────  [ref5.jpg]  │  │  │thumb │  │
│  [Link]  │  │                                        │  │  └──────┘  │
│  [Group] │  │  [note: "warm lighting"]               │  │  [Describe]│
│          │  │                                        │  │  [Send to] │
│          │  └────────────────────────────────────────┘  │  [Generate]│
├──────────┴──────────────────────────────────────────────┴────────────┤
│  [Shot 1] [Shot 2] [Shot 3]  ← storyboard strip bar (footer)         │ ← Footer
└──────────────────────────────────────────────────────────────────────┘
```

**Pros:**
- ✅ Native left toolshelf — minimal tools ready to go
- ✅ **Annotation brush built in** — `SpaceNodeEditor.show_annotation` + `bpy.ops.node.annotate` — zero work to get drawing
- ✅ Node Editor backdrop is a natural "infinite canvas" (pan/zoom already works)
- ✅ Node Editor already supports GPU draw handlers (`draw_handler_add` on `SpaceNodeEditor`)
- ✅ Node-style linking (the user's "link images" idea maps to node links)
- ✅ Custom node types possible — image nodes, string/text nodes, logic bricks

**Cons:**
- ❌ No native right sidebar in some configs (but `RGN_TYPE_UI` exists)
- ❌ Node Editor's native node grid may need hiding or repurposing
- ❌ The node editor's native node-drawing system is C++ — we draw our own image cards via GPU

### 3.3 Recommendation: Hybrid — Node Editor Shell + GPU Canvas + File Browser Import

**The Node Editor is the primary shell.** Rationale:

1. **Annotation is free** — the user explicitly wants "use the annotation brush to draw ideas, which is already built into Blender in the Node Editors." This is the single strongest argument.
2. **The toolshelf is ready** — minimal tools (Select, Move, Scale, Annotate, Link, Group) map perfectly to the native `RGN_TYPE_TOOLS` region.
3. **Node-style linking** — the user's "link images into a sequence" maps to node links. We get link-dragging UX patterns for free.
4. **Infinite canvas** — the node editor's pan/zoom backdrop is the PureRef-style canvas.

**The File Browser is the import path.** Drag-drop from the File Browser into the moodboard canvas (or a "Import from File Browser" button that opens a file select dialog). We do NOT take over the File Browser space — we use its native drag-drop and file select operators.

**The GPU canvas draws the image cards.** We register a `POST_PIXEL` draw handler on `SpaceNodeEditor` that:
- Draws image cards (GPU textures from `bpy.data.images`)
- Draws selection gizmos (move/scale/rotate handles)
- Draws links between images (bezier curves like node links)
- Draws notes and text labels
- Draws the storyboard strip bar (footer overlay)

**The native node grid is hidden** (`space.show_backdrop`-style toggles or a custom backdrop color) — the canvas is our own.

---

## 4. Data Model

### 4.1 Core Dataclasses (`moodboard.py`)

```python
# moodboard.py — Data model for the Moodboard Editor

from dataclasses import dataclass, field

@dataclass
class MoodboardImage:
    """A single image card on the board."""
    id: str                       # UUID
    name: str                     # Display name
    image: bpy.types.Image        # Blender image datablock (or preview)
    source_path: str              # Original file path ("" if generated)
    source: str                   # 'file' | 'generated' | 'clipboard' | 'url' | 'viewport'
    position: tuple[float, float] # Canvas position (node-editor units)
    scale: float                  # Display scale (1.0 = native)
    rotation: float               # Rotation in radians
    z_order: int                  # Stacking order (bring to front / send to back)
    notes: str                    # User annotation text
    tags: list[str]               # Searchable tags
    frame_id: str | None          # Which frame this image belongs to (None = loose on board)
    shot_id: str | None           # Which shot this image belongs to (None = reference area)
    linked_to: list[str]          # IDs of linked images (sequence edges)
    color_grade: str              # Optional: color grade preset name (for compositor push)
    hidden: bool = False          # Hidden from canvas (but kept in board)


@dataclass
class MoodboardFrame:
    """A visual grouping container — like a node frame.

    Frames group images visually on the canvas. They are the *visual*
    organization layer ("these images belong together"). Frames can be
    converted into shots (the *narrative* layer).
    """
    id: str                       # UUID
    name: str                     # "Frame 01", "Character Ref", "Palette"
    position: tuple[float, float] # Frame top-left corner (node-editor units)
    size: tuple[float, float]     # Frame size (node-editor units)
    color: tuple[float, float, float, float]  # Frame tint (theme-aware)
    image_ids: list[str]          # Images inside this frame (ordered)
    markup: str                   # Frame markup text (annotation-style, from frame tools)
    collapsed: bool = False       # Collapsed frame (like node frames)


@dataclass
class MoodboardShot:
    """A storyboard shot — a group of images + notes + timing.

    Shots are the *narrative* layer. A shot can be created from a frame
    (convert frame → shot) or from a chain of linked images.
    """
    id: str                       # UUID
    name: str                     # "Shot 01"
    description: str              # Agent-generated or user-written shot description
    dialogue: str                 # Dialogue/script for this shot
    camera: str                   # Camera description ("close-up", "wide", "dolly in")
    duration_frames: int          # Duration in frames (for animatic)
    image_ids: list[str]          # Images in this shot (ordered)
    frame_id: str | None          # Source frame (if converted from a frame)
    notes: str                    # Director notes
    color_tag: str                # Collection color tag (matches Blender's COLOR_01-08)


@dataclass
class MoodboardSequence:
    """An ordered chain of shots — the story spine."""
    id: str
    name: str                     # "Sequence 01"
    shot_ids: list[str]           # Ordered shots
    description: str              # Agent-generated logline / synopsis
    status: str                   # 'draft' | 'review' | 'locked'


@dataclass
class MoodboardLogicBrick:
    """A logic brick — conditional story routing (Tier 5+).

    Images link INTO the brick (as condition references). The brick
    routes TO shots (true/false outputs). This is the story-branching
    layer: "IF the hero fails the check, go to shot 7."
    """
    id: str                       # UUID
    name: str                     # "Branch: Hero Check"
    condition: str                # 'IF' | 'IF_NOT' | 'AND' | 'OR' | 'SEQUENCE'
    condition_text: str           # Natural-language condition ("hero fails the check")
    image_ids: list[str]          # Images linked in as condition references
    true_shot_id: str | None      # Route when condition is true
    false_shot_id: str | None     # Route when condition is false
    notes: str                    # Director notes on the branch


@dataclass
class MoodboardStringBrick:
    """A string brick — dialogue / text for a shot (Tier 5+).

    Links INTO a shot (or logic brick) to provide dialogue. The
    dialogue feeds the VSE text strip and (Tier 5) TTS audio.
    """
    id: str                       # UUID
    name: str                     # "Dialogue: Hero"
    speaker: str                  # Character name
    text: str                     # The dialogue line(s)
    shot_id: str | None           # Shot this dialogue belongs to
    emotion: str                  # Optional delivery note ("whispered", "angry")


@dataclass
class Moodboard:
    """A complete board — the persisted document."""
    name: str                     # "Concept Art", "Film 01", etc.
    images: list[MoodboardImage]
    frames: list[MoodboardFrame]
    shots: list[MoodboardShot]
    sequences: list[MoodboardSequence]
    logic_bricks: list[MoodboardLogicBrick] = field(default_factory=list)
    string_bricks: list[MoodboardStringBrick] = field(default_factory=list)
    background_color: tuple[float, float, float, float]  # Theme-aware
    grid_enabled: bool = True
    snap_enabled: bool = True
    zoom: float                   # Canvas zoom level
    pan: tuple[float, float]      # Canvas pan offset
    created: str                  # ISO timestamp
    modified: str                 # ISO timestamp
```

### 4.2 Persistence — Inside the Blend File (Text Datablocks)

**The critical design decision**: the moodboard lives **inside the `.blend` file**, not in external JSON files. This is what the user wants — "we work in the blend file. It would be annoying to load a file, and then have to load moodboard, which is disconnected."

**Primary storage**: The board document is serialized to JSON and stored in a **Blender Text datablock** (`bpy.data.texts`). Text datablocks are:
- ✅ Embedded in the `.blend` file — they save/load with the file automatically
- ✅ Backwards compatible with Blender's file system — no custom binary format
- ✅ Human-readable — users can inspect/edit the JSON in the Text Editor
- ✅ Diffable — version control friendly
- ✅ Already used by the addon — `_save_code_to_text_editor_deferred()` writes `Coworker_*` text blocks

```python
# moodboard.py — Persistence via Text datablocks

_TEXT_PREFIX = "MB_"  # e.g. "MB_Concept Art"


def save_board_to_text(board: Moodboard) -> None:
    """Serialize the board to JSON and store it in a Text datablock."""
    import bpy
    import json

    text_name = _TEXT_PREFIX + board.name
    text = bpy.data.texts.get(text_name)
    if text is None:
        text = bpy.data.texts.new(text_name)
    text.clear()
    text.write(json.dumps(board.to_dict(), indent=2))


def load_board_from_text(name: str) -> Moodboard | None:
    """Load a board from a Text datablock."""
    import bpy
    import json

    text = bpy.data.texts.get(_TEXT_PREFIX + name)
    if text is None:
        return None
    try:
        return Moodboard.from_dict(json.loads(text.as_string()))
    except (json.JSONDecodeError, KeyError):
        return None


def list_boards_in_blend() -> list[str]:
    """List all moodboards stored in the current .blend file."""
    import bpy
    return [t.name[len(_TEXT_PREFIX):] for t in bpy.data.texts
            if t.name.startswith(_TEXT_PREFIX)]
```

**How it works in practice**:
1. User creates a board → `MB_Concept Art` text block appears in `bpy.data.texts`
2. User saves the `.blend` file → the text block is saved with it
3. User reopens the file → the board is right there, no separate load step
4. User can see all boards in the header dropdown (from `list_boards_in_blend()`)

**Image storage**: Images are loaded as `bpy.data.images` datablocks (packed or external). The board JSON references them by name. Packed images are embedded in the `.blend` file; external images are referenced by path (with a "missing files" warning like Blender's own).

**Runtime state**: A `bpy.types.PropertyGroup` (`MoodboardProperties`) on the WindowManager for the *active* board's runtime state (selected image, active shot, tool, zoom/pan). This follows the existing `ChatHistoryProperties` pattern.

**Export/Import (optional)**: A "Export Board" operator writes the JSON to an external file (for sharing or backup). A "Import Board" operator reads it back. This is *optional* — the default is blend-file storage.

**Migration**: If a board was previously stored as an external JSON file (from an earlier prototype), a "Import from File" operator can load it into the blend file. One-way migration — the blend file becomes the source of truth.

### 4.3 Property Groups (Blender-registered)

```python
class MoodboardProperties(PropertyGroup):
    """Runtime state for the moodboard editor (WindowManager)."""
    active_board: StringProperty(default="")          # Name of active board
    selected_image_id: StringProperty(default="")     # Selected image card
    active_frame_id: StringProperty(default="")       # Active frame (visual grouping)
    active_shot_id: StringProperty(default="")        # Active shot (narrative grouping)
    active_tool: EnumProperty(items=MOODBOARD_TOOL_ITEMS)  # select/move/scale/rotate/annotate/link/group/frame
    canvas_zoom: FloatProperty(default=1.0)
    canvas_pan_x: FloatProperty(default=0.0)
    canvas_pan_y: FloatProperty(default=0.0)
    show_grid: BoolProperty(default=True)
    show_annotations: BoolProperty(default=True)
    show_shot_bar: BoolProperty(default=True)
    annotate_color: FloatVectorProperty(size=4, default=(1, 0.3, 0.3, 1))
    annotate_thickness: FloatProperty(default=2.0)
```

---

## 5. GPU Rendering Architecture

### 5.1 The Canvas Draw Handler

```python
# ui_moodboard.py — Canvas rendering

class MoodboardCanvas:
    """GPU rendering for the moodboard canvas (SpaceNodeEditor overlay)."""

    _draw_handler: object | None = None
    _active: bool = False

    @classmethod
    def enable(cls, context):
        """Register the POST_PIXEL draw handler on SpaceNodeEditor."""
        if cls._active:
            return
        cls._draw_handler = bpy.types.SpaceNodeEditor.draw_handler_add(
            cls._draw_canvas, (context,), 'WINDOW', 'POST_PIXEL'
        )
        cls._active = True

    @classmethod
    def disable(cls):
        if cls._draw_handler is not None:
            bpy.types.SpaceNodeEditor.draw_handler_remove(cls._draw_handler, 'WINDOW')
            cls._draw_handler = None
            cls._active = False

    @classmethod
    def _draw_canvas(cls, context):
        """Main draw callback — called every frame."""
        import gpu
        import blf
        from gpu_extras.batch import batch_for_shader
        from .theme_utils import get_space_color, get_ui_color

        region = context.region
        space = context.space_data

        # ── Background (theme-aware) ──
        bg = get_space_color("node_editor", "back")
        _draw_rect((0, 0), (region.width, region.height), bg)

        # ── Grid (optional) ──
        if props.show_grid:
            _draw_grid(space, region, get_space_color("node_editor", "grid"))

        # ── Frames (visual grouping containers) ──
        for frame in board.frames:
            _draw_frame(frame, space, region)

        # ── Image cards ──
        for img in board.images:
            if img.hidden:
                continue
            _draw_image_card(img, space, region)

        # ── Links between images ──
        for img in board.images:
            for target_id in img.linked_to:
                _draw_link(img, board.get_image(target_id), space, region)

        # ── Annotations (native node annotations render separately) ──
        # The annotation brush draws via Blender's native system — we only
        # need to ensure show_annotation is on.

        # ── Selection gizmo ──
        if props.selected_image_id:
            _draw_selection_gizmo(selected_img, space, region)

        # ── Shot bar (footer overlay) ──
        if props.show_shot_bar:
            _draw_shot_bar(board, region)
```

### 5.2 Image Card Rendering — The Performance Core

**The critical performance question**: how do we draw 100+ images smoothly?

**Answer: GPU texture caching with downscaled thumbnails.**

```python
class _ThumbnailCache:
    """GPU texture cache for image cards.

    Each image is downscaled ONCE to a max dimension (e.g. 512px) and
    uploaded to the GPU as a texture. The texture is reused every frame.
    Full-resolution textures are only used for the selected image.
    """

    _cache: dict[str, gpu.types.GPUTexture] = {}

    @classmethod
    def get(cls, image: bpy.types.Image) -> gpu.types.GPUTexture | None:
        """Return the cached GPU texture for an image, or None."""
        key = image.name
        if key in cls._cache:
            return cls._cache[key]
        # Downscale + upload once.
        tex = cls._upload_thumbnail(image)
        if tex is not None:
            cls._cache[key] = tex
        return tex

    @classmethod
    def _upload_thumbnail(cls, image: bpy.types.Image) -> gpu.types.GPUTexture | None:
        """Downscale the image to max 512px and upload as a GPU texture."""
        import numpy as np
        # Use image.pixels (or image.preview) → numpy array → downscale
        # → gpu.types.GPUTexture((w, h), format='RGBA8', data=...)
        ...

    @classmethod
    def invalidate(cls, image_name: str):
        cls._cache.pop(image_name, None)
```

**Drawing a card**:

```python
def _draw_image_card(img, space, region):
    """Draw one image card with its frame and label."""
    from gpu_extras.presets import draw_texture_2d

    tex = _ThumbnailCache.get(img.image)
    if tex is None:
        return

    # Convert node-editor coordinates to pixel coordinates.
    x, y = _node_to_pixel(img.position, space, region)
    w = tex.width * img.scale
    h = tex.height * img.scale

    # Draw the texture (flipped — Blender images are bottom-left origin).
    draw_texture_2d(tex, (x, y), w, h, flip=(True, False))

    # Draw a subtle frame (theme-aware).
    frame_color = get_ui_color("wcol_regular", "outline")
    _draw_rect_outline((x, y), (w, h), frame_color)

    # Draw the label below the card.
    _draw_text(img.name, x + w / 2, y - 14, size=11, align='CENTER', color=label_color)
```

**Performance budget**:
- 100 cards × (1 texture draw + 1 outline + 1 label) ≈ 300 draw calls/frame — trivial for GPU
- Thumbnail downscale happens once per image (on first display), in a background thread
- Full-res textures only for the selected image (1 at a time)
- `bpy.app.timers` throttles redraws to ~30 FPS when idle, full speed during interaction

### 5.3 Selection Gizmo (PureRef-style)

```python
def _draw_selection_gizmo(img, space, region):
    """Draw move/scale/rotate handles around the selected image."""
    from gpu_extras.presets import draw_circle_2d

    x, y = _node_to_pixel(img.position, space, region)
    w = tex.width * img.scale
    h = tex.height * img.scale

    accent = get_ui_color("wcol_regular", "inner_sel")  # Theme accent

    # Corner handles (scale).
    for cx, cy in [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]:
        draw_circle_2d((cx, cy), accent, 5.0)

    # Edge handles (rotate).
    for ex, ey in [(x + w/2, y + h), (x + w/2, y)]:
        draw_circle_2d((ex, ey), accent, 4.0)

    # Selection outline.
    _draw_rect_outline((x, y), (w, h), accent, thickness=2.0)
```

### 5.3b Frame Rendering (Visual Grouping)

```python
def _draw_frame(frame, space, region):
    """Draw a frame container — like a node frame."""
    x, y = _node_to_pixel(frame.position, space, region)
    w = frame.size[0] * space.zoom
    h = frame.size[1] * space.zoom

    # Frame background (theme-aware, semi-transparent).
    bg = get_ui_color("wcol_box", "inner")
    _draw_rect((x, y), (w, h), (bg[0], bg[1], bg[2], 0.15))

    # Frame border.
    border = get_ui_color("wcol_regular", "outline")
    _draw_rect_outline((x, y), (w, h), border, thickness=1.5)

    # Frame title bar.
    title_bg = get_ui_color("wcol_box", "inner_sel")
    _draw_rect((x, y + h - 24), (w, 24), title_bg)
    _draw_text(frame.name, x + 8, y + h - 12, size=12, align='LEFT', color=text_color)

    # Frame markup (from frame tools with markup).
    if frame.markup:
        _draw_text(frame.markup, x + 8, y + h - 36, size=10, align='LEFT', color=text_color)
```

### 5.4 Links Between Images (Node-Style)

```python
def _draw_link(img_a, img_b, space, region):
    """Draw a bezier link between two image cards (like node links)."""
    import gpu
    from gpu_extras.batch import batch_for_shader

    a = _node_to_pixel(img_a.position, space, region)
    b = _node_to_pixel(img_b.position, space, region)

    # Bezier control points (horizontal tangents, like node links).
    dx = max(40.0, abs(b[0] - a[0]) * 0.5)
    points = _bezier_points(a, (a[0] + dx, a[1]), (b[0] - dx, b[1]), b, segments=24)

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    gpu.state.line_width_set(2.0)
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": points})
    shader.bind()
    shader.uniform_float("color", get_ui_color("wcol_regular", "text"))
    batch.draw(shader)
```

### 5.5 Coordinate Systems

| System | Used For | Conversion |
|---|---|---|
| **Node editor units** | Image positions, links, shot layout | Stored in data model |
| **Pixel coordinates** | GPU drawing | `_node_to_pixel()` via `space.cursor_location` + `region.width/height` |
| **UV (0-1)** | Board export / composite | `_node_to_uv()` for composite image generation |

```python
def _node_to_pixel(pos, space, region):
    """Convert node-editor coordinates to region pixel coordinates."""
    # Node editor: origin is center of the view, zoom scales units.
    # cursor_location is the node-space point at the center of the view.
    cx, cy = space.cursor_location
    zoom = space.zoom
    px = (pos[0] - cx) * zoom + region.width / 2
    py = (pos[1] - cy) * zoom + region.height / 2
    return (px, py)
```

---

## 6. UX Design: Regions, Sidebars & Panels

### 6.1 Editor Layout (Node Editor Shell)

```
┌──────────────────────────────────────────────────────────────────────┐
│ [Moodboard]  Board: [Concept Art ▼]  [New] [Open] [Save] [Import]   │ ← Header (custom)
├──────────┬──────────────────────────────────────────────┬────────────┤
│  Left    │  Canvas (GPU-drawn)                          │  Right     │
│  Tools   │                                              │  Sidebar   │
│  (TOOLS) │                                              │  (UI)      │
│  ┌─────┐ │                                              │  ┌───────┐ │
│  │Select│ │                                              │  │ Board │ │
│  │Move  │ │                                              │  │ Info  │ │
│  │Scale │ │                                              │  ├───────┤ │
│  │Rotate│ │                                              │  │Selected│ │
│  │Annot │ │                                              │  │ Image │ │
│  │Link  │ │                                              │  ├───────┤ │
│  │Group │ │                                              │  │ Shot  │ │
│  │      │ │                                              │  │ Panel │ │
│  └─────┘ │                                              │  ├───────┤ │
│          │                                              │  │ Agent │ │
│          │                                              │  │ Chat  │ │
├──────────┴──────────────────────────────────────────────┴────────────┤
│  [Shot 1] [Shot 2] [Shot 3] [+ Add Shot]  [▶ Play Animatic]         │ ← Footer (custom)
└──────────────────────────────────────────────────────────────────────┘
```

### 6.2 Left Toolshelf (Native `RGN_TYPE_TOOLS`)

A native panel with `bl_region_type='TOOLS'` on `SpaceNodeEditor`:

| Tool | Icon | Operator | Behavior |
|---|---|---|---|
| **Select** | `RESTRICT_SELECT_OFF` | `bfacw.moodboard_select` | Click to select, Shift-click to multi-select, drag to box-select |
| **Move** | `ORIENTATION_GIMBAL` | `bfacw.moodboard_move` | Drag to reposition (PureRef-style) |
| **Scale** | `FULLSCREEN_ENTER` | `bfacw.moodboard_scale` | Drag corner handles |
| **Rotate** | `ORIENTATION_GLOBAL` | `bfacw.moodboard_rotate` | Drag edge handles |
| **Annotate** | `GREASEPENCIL_ANNOTATE` | `bfacw.moodboard_annotate` | Enables native annotation brush (`bpy.ops.node.annotate`) |
| **Frame** | `FULLSCREEN` | `bfacw.moodboard_frame` | Draw a frame container around selected images (visual grouping) |
| **Link** | `LINKED` | `bfacw.moodboard_link` | Drag from one image to another to create a sequence link |
| **Group** | `GROUP` | `bfacw.moodboard_group` | Group selected images into a shot (narrative grouping) |
| **Convert** | `CONVERT` | `bfacw.moodboard_convert` | Convert frame → shot, or shot → frame |

Tool settings (in `RGN_TYPE_TOOL_PROPS` or the right sidebar):
- Annotate: color, thickness
- Move: snap to grid toggle
- Scale: uniform toggle
- Frame: color, markup text

### 6.3 Right Sidebar Panels (Native `RGN_TYPE_UI`)

| Panel | Contents |
|---|---|
| **Board Info** | Board name, image count, shot count, background color, grid/snap toggles, export composite |
| **Selected Image** | Thumbnail preview, name, source, tags, notes, position/scale/rotation numeric fields, [Describe] [Send to Shader] [Send to Image Editor] [Send to Compositor] [Generate Variations] [Remove] |
| **Shot Panel** | Active shot: name, description, dialogue, camera, duration, color tag, image list (ordered), [Add to Shot] [Remove from Shot] |
| **Agent Chat** | The shared `_draw_chat_interface()` — the moodboard's chat panel. Selected images are auto-attached as vision context. |

### 6.4 Custom Header & Footer

```python
class BFACW_HT_moodboard_header(bpy.types.Header):
    """Custom header for the Moodboard editor."""
    bl_idname = "BFACW_HT_moodboard_header"
    bl_space_type = 'NODE_EDITOR'
    bl_label = "Moodboard"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Moodboard", icon='IMAGE_REFERENCE')
        layout.separator()
        layout.prop(props, "active_board", text="")   # Board selector
        layout.operator("bfacw.moodboard_new", text="", icon='ADD')
        layout.operator("bfacw.moodboard_open", text="", icon='FILE_FOLDER')
        layout.operator("bfacw.moodboard_save", text="", icon='FILE_TICK')
        layout.operator("bfacw.moodboard_import", text="", icon='IMPORT')
        layout.separator()
        # Agent status (reuse from chat).
        state = agent_controller._agent_state
        if state.mcp_server_running:
            layout.label(text="🟢", icon='CHECKMARK')
        else:
            layout.label(text="🔴", icon='X')
```

```python
class BFACW_HT_moodboard_footer(bpy.types.Header):
    """Custom footer — the storyboard strip bar."""
    bl_idname = "BFACW_HT_moodboard_footer"
    bl_space_type = 'NODE_EDITOR'
    bl_label = "Moodboard Shots"

    def draw(self, context):
        layout = self.layout
        for shot in board.shots:
            op = layout.operator("bfacw.moodboard_set_shot", text=shot.name)
            op.shot_id = shot.id
        layout.operator("bfacw.moodboard_add_shot", text="", icon='ADD')
        layout.separator()
        layout.operator("bfacw.moodboard_play_animatic", text="Play Animatic", icon='PLAY')
```

### 6.5 Import UX

**Drag & drop from File Browser**: Blender's file browser supports drag-drop of image files into other editors. We register a drop handler that imports dropped files as image cards.

**File select operator**: `bfacw.moodboard_import` opens a file browser (`bpy.ops.file.select_all` + `invoke`), multi-select images, imports each as a card.

**Clipboard paste**: `bfacw.moodboard_paste` — paste an image from the clipboard (Windows: `PIL.ImageGrab` or `bpy.ops.image.clipboard`).

**Viewport capture**: `bfacw.moodboard_capture_viewport` — render the current viewport to an image and add it as a card (reuses `render_viewport_to_path` toolcode logic).

---

## 7. Node Editor Integration: Two Node Systems

The user's framing is right: the storyboarding has **two distinct node concerns** that should be **separate systems** (or nodes), with conversion between them:

1. **System 1 — The Image/Frame System**: images, frames, and annotations. This is the *visual / spatial* language — "these images belong together," "this is the palette," "this is my reference board."
2. **System 2 — The Storyboard Shot-Sequence Chain**: linked image chains with descriptions, dialogue, camera notes, and timing. This is the *narrative / temporal* language — "this image leads to that image," "this is shot 3, it's a close-up, 96 frames."

**The conversion pair** (`frame → shot` and `shot → frame`) ties them together, and **both** feed the Sequencer as scene strips.

### 7.1 System 1: The Image/Frame System (Visual Layer)

This is the **PureRef-like** experience. Images are cards; frames group them visually.

| Concept | Node Concept | Data Model |
|---|---|---|
| Image card | `MoodboardImageNode` | `MoodboardImage` |
| Frame (visual group) | `MoodboardFrameNode` | `MoodboardFrame` |
| Annotation | Native node annotation | `space.annotation` |
| Markup on frames | Frame tools with markup (upcoming Blender) | `MoodboardFrame.markup` |
| Note / text | `MoodboardTextNode` | `MoodboardImage.notes` |

```python
class MoodboardImageNode(bpy.types.Node):
    """System 1 node: displays an image card."""
    bl_idname = "MoodboardImageNode"
    bl_label = "Image"
    bl_icon = 'IMAGE_DATA'

    image: bpy.props.PointerProperty(type=bpy.types.Image)
    scale: bpy.props.FloatProperty(default=1.0)
    rotation: bpy.props.FloatProperty(default=0.0)

    def init(self, context):
        self.outputs.new("NodeSocketColor", "Image")

    def draw_buttons(self, context, layout):
        layout.prop(self, "image")
        layout.prop(self, "scale")
        layout.prop(self, "rotation")


class MoodboardFrameNode(bpy.types.Node):
    """System 1 node: a visual grouping container (like NodeFrame).

    Frames group images visually. They can hold arbitrary images —
    reference batches, palettes, character sheets. A frame can be
    converted to a shot (narrative layer) at any time.
    """
    bl_idname = "MoodboardFrameNode"
    bl_label = "Frame"
    bl_icon = 'FULLSCREEN'

    label: bpy.props.StringProperty(default="Frame")
    markup: bpy.props.StringProperty(default="", subtype='MULTILINE')
    frame_color: bpy.props.FloatVectorProperty(size=4, default=(0.4, 0.6, 0.8, 0.2))

    def draw_buttons(self, context, layout):
        layout.prop(self, "label")
        layout.prop(self, "markup")
        layout.prop(self, "frame_color")
```

**Frame tools with markup**: Blender is adding frame tools with markup (annotation-style markup on frames). The moodboard **uses these natively** — a frame's `markup` text is drawn on the frame, editable via the frame tools. This is the "built in system first, GPU drawing only for the node editor clone" principle the user stated.

### 7.2 System 2: The Storyboard Shot-Sequence Chain (Narrative Layer)

This is the **storyboard** experience. Images are chained into shots; shots chain into sequences. Each link carries narrative meaning (transition, cause, beat).

| Concept | Node Concept | Data Model |
|---|---|---|
| Shot (narrative group) | `MoodboardShotNode` | `MoodboardShot` |
| Sequence (shot chain) | `MoodboardSequenceNode` | `MoodboardSequence` |
| Link between shots | Node link (with label = transition) | `MoodboardSequence.shot_ids` |
| Description | String socket / text node on shot | `MoodboardShot.description` |
| Dialogue | `MoodboardTextNode` inside shot | `MoodboardShot.dialogue` |
| Logic brick (branching) | `MoodboardLogicNode` | Future (Tier 5+) |

```python
class MoodboardShotNode(bpy.types.Node):
    """System 2 node: a storyboard shot (narrative unit).

    A shot groups images + description + dialogue + camera + timing.
    It is created from a frame (frame → shot) or built directly.
    """
    bl_idname = "MoodboardShotNode"
    bl_label = "Shot"
    bl_icon = 'SHADERFX'

    name: bpy.props.StringProperty(default="Shot")
    description: bpy.props.StringProperty(default="", subtype='MULTILINE')
    dialogue: bpy.props.StringProperty(default="", subtype='MULTILINE')
    camera: bpy.props.StringProperty(default="close-up")
    duration_frames: bpy.props.IntProperty(default=96)

    def init(self, context):
        self.inputs.new("NodeSocketColor", "Image")
        self.outputs.new("NodeSocketColor", "Image")

    def draw_buttons(self, context, layout):
        layout.prop(self, "name")
        layout.prop(self, "description")
        layout.prop(self, "dialogue")
        layout.prop(self, "camera")
        layout.prop(self, "duration_frames")
```

```python
class MoodboardSequenceNode(bpy.types.Node):
    """System 2 node: an ordered chain of shots (the story spine)."""
    bl_idname = "MoodboardSequenceNode"
    bl_label = "Sequence"
    bl_icon = 'SEQUENCE'

    name: bpy.props.StringProperty(default="Sequence")
    description: bpy.props.StringProperty(default="", subtype='MULTILINE')
    status: bpy.props.EnumProperty(items=[
        ('DRAFT', "Draft", ""),
        ('REVIEW', "Review", ""),
        ('LOCKED', "Locked", ""),
    ])

    def init(self, context):
        self.inputs.new("NodeSocketColor", "Shot")
        self.outputs.new("NodeSocketColor", "Shot")

    def draw_buttons(self, context, layout):
        layout.prop(self, "name")
        layout.prop(self, "description")
        layout.prop(self, "status")
```

```python
class MoodboardLogicNode(bpy.types.Node):
    """System 2 node (future, Tier 5+): story branching.

    A logic brick — conditional routing for story branching:
    "IF the hero fails the check, go to shot 7."
    """
    bl_idname = "MoodboardLogicNode"
    bl_label = "Logic"
    bl_icon = 'LOGIC'

    condition: bpy.props.EnumProperty(items=[
        ('AND', "AND", ""),
        ('OR', "OR", ""),
        ('IF', "IF", ""),
        ('SEQUENCE', "Sequence", ""),
    ])

    def init(self, context):
        self.inputs.new("NodeSocketBool", "A")
        self.inputs.new("NodeSocketBool", "B")
        self.outputs.new("NodeSocketBool", "Result")
```

### 7.2b The Logic Brick UX — Comparison & Flowcharts

The user's instinct: **logic bricks for the story** — link images (or various images) to logic bricks (which could be shots), and string bricks for dialogue. This is the *branching narrative* layer. Let's mature it with UX comparisons and flowcharts.

#### The Brick Vocabulary

| Brick | What it holds | What links in | What links out | Analogy |
|---|---|---|---|---|
| **Image brick** | An image card | — | → logic brick (condition ref), → shot (content) | PureRef card |
| **Frame brick** | A visual group | images | → shot (convert), → logic brick (group condition) | Node frame |
| **Shot brick** | A narrative unit (images + desc + dialogue + camera + timing) | images, string bricks (dialogue) | → sequence, → logic brick (branch target) | VSE strip |
| **Sequence brick** | An ordered shot chain | shots | → animatic, → 3D scene | Timeline |
| **Logic brick** | A condition + true/false routes | images (condition refs), shots (branch targets) | → shot (routed next) | IF/ELSE |
| **String brick** | Dialogue / text | — | → shot (dialogue), → VSE text strip, → TTS (Tier 5) | Text node |

#### UX Comparison: How Other Tools Do Branching

| Tool | Branching Model | UX Pattern | Strengths | Weaknesses |
|---|---|---|---|---|
| **Blender Logic Nodes (VSE)** | Node graph with condition sockets | Visual node editor, sockets + links | Familiar to Blender users, scriptable | No story semantics — generic bools |
| **Twine** | Text-based branching | Hyperlinked passages, visual map | Zero learning curve, story-first | Not visual for images, no 3D tie-in |
| **Unreal Blueprints** | Event-driven node graph | Exec pins + data pins | Powerful, industry-standard | Heavy, intimidating for non-coders |
| **Ren'Py** | Script-based branching | Text labels + jumps | Simple, dialogue-first | No node graph, no image board |
| **Mixar Moodboard** | Linear reference board | In-canvas images, no branching | Simple, focused | No story structure at all |
| **Our proposal** | **Hybrid: image board + brick graph** | Cards on canvas + logic/string bricks | Story-first, image-first, agent-scriptable | New pattern — needs good onboarding |

**The key differentiator**: Twine and Ren'Py are *text-first*; Blueprints are *code-first*. Our moodboard is **image-first** — the images ARE the story, and the bricks add narrative structure on top. The agent (vision) can read the images and fill the bricks.

#### Flowchart 1: The Linear Story (No Branching)

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ Shot 01  │──▶│ Shot 02  │──▶│ Shot 03  │──▶│ Shot 04  │
│ (img1)   │   │ (img2)   │   │ (img3)   │   │ (img4)   │
│ "intro"  │   │ "setup"  │   │ "climax" │   │ "resolve"│
└──────────┘   └──────────┘   └──────────┘   └──────────┘
     │              │              │              │
     ▼              ▼              ▼              ▼
  [dialogue]    [dialogue]    [dialogue]    [dialogue]   ← string bricks
     │              │              │              │
     └──────────────┴──────────────┴──────────────┘
                          │
                          ▼
                   [Animatic → VSE]
```

This is the **default** — a sequence of shots, each with dialogue. No logic bricks needed. The footer shot bar IS this linear view.

#### Flowchart 2: The Branching Story (Logic Bricks)

```
                    ┌─────────────────────────────┐
                    │  LOGIC BRICK                │
                    │  "Hero fails the check?"    │
                    │                             │
  ┌──────────┐      │  inputs: [img5] (the check) │
  │ Shot 02  │─────▶│  condition: IF              │
  │ "setup"  │      └──────────┬──────────┬───────┘
  └──────────┘                 │          │
                    TRUE       │          │       FALSE
                    ▼          │          │          ▼
              ┌──────────┐    │          │    ┌──────────┐
              │ Shot 03a │◀───┘          └───▶│ Shot 03b │
              │ "fail"   │                    │ "succeed"│
              │ (img6)   │                    │ (img7)   │
              └──────────┘                    └──────────┘
                    │                              │
                    └──────────────┬───────────────┘
                                   ▼
                            ┌──────────┐
                            │ Shot 04  │  ← both paths converge
                            │ "resolve"│
                            └──────────┘
```

**How it works in the UI**:
1. User drags a **Logic brick** onto the canvas (or the agent creates it)
2. User links images INTO the brick — these are the *condition references* (what the condition is about)
3. User links shots OUT of the brick — the TRUE and FALSE routes
4. The brick's `condition_text` is natural language: "Hero fails the check"
5. The agent (vision) can *evaluate* the condition by reading the linked images: "Based on img5, the hero looks cornered — route to Shot 03a"

#### Flowchart 3: The Dialogue Flow (String Bricks)

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ STRING BRICK │     │ STRING BRICK │     │ STRING BRICK │
│ "Hero: ..."  │     │ "Villain:..."│     │ "Hero: ..."  │
│ speaker: Hero│     │ speaker: Vil │     │ speaker: Hero│
│ emotion: angry│    │ emotion: calm│     │ emotion: sad │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       ▼                    ▼                    ▼
  ┌─────────────────────────────────────────────────┐
  │              SHOT 03 (the scene)                │
  │  images: [img6]  camera: "close-up"            │
  │  dialogue: "Hero: You'll never get away!"       │
  │            "Villain: I already have."           │
  │            "Hero: ..."                          │
  └──────────────────────┬──────────────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  VSE TEXT STRIP     │  ← animatic export
              │  "Hero: You'll..."  │
              └─────────────────────┘
              ┌─────────────────────┐
              │  TTS (Tier 5)       │  ← dialogue audio
              │  Chatterbox voice   │
              └─────────────────────┘
```

**String bricks are the dialogue layer**: each brick is one character's line (speaker + text + emotion). Multiple string bricks link into a shot, forming the shot's dialogue. The shot's `dialogue` field is the *concatenation* of its string bricks. This feeds:
- The VSE text strip (animatic)
- The TTS audio (Tier 5 — Chatterbox)
- The agent's script-writing (it reads/writes string bricks)

#### The Brick Interaction Model (UX)

```
┌─────────────────────────────────────────────────────────────┐
│  Canvas interaction (Card Mode)                              │
│                                                              │
│  [img1] ──link──▶ [img2] ──link──▶ [img3]                    │
│     │                │                │                      │
│     │                ▼                │                      │
│     │          ┌─────────────┐        │                      │
│     └─────────▶│ LOGIC BRICK │◀───────┘                      │
│                │ "IF hero..." │                             │
│                └──────┬──────┘                              │
│                       │                                     │
│              ┌────────┴────────┐                             │
│              ▼                 ▼                             │
│        ┌──────────┐      ┌──────────┐                       │
│        │ Shot 03a │      │ Shot 03b │                       │
│        └──────────┘      └──────────┘                       │
│                                                              │
│  Drag from an image's edge → creates a link                  │
│  Drag from a shot's edge → creates a branch                  │
│  Double-click a brick → opens its panel (condition/dialogue) │
└─────────────────────────────────────────────────────────────┘
```

**Interaction rules**:
- **Link** (image → image): sequence edge (System 2 chain)
- **Link** (image → logic brick): condition reference
- **Link** (logic brick → shot): branch route (TRUE/FALSE sockets)
- **Link** (string brick → shot): dialogue assignment
- **Convert** (frame → shot): visual → narrative
- **Convert** (shot → frame): narrative → visual

#### The Agent's Role in the Brick System

The bricks are **agent-scriptable** — the agent can:
1. **Create** logic bricks from a prompt: "Add a branch where the hero fails the check"
2. **Fill** string bricks: "Write dialogue for the villain in shot 3"
3. **Evaluate** conditions: "Based on the images, which path should the story take?"
4. **Re-route** branches: "Make shot 3b the canon path"
5. **Export** the branching story: "Export this as a Twine-style outline"

This is the **storybuilding leverage** — the agent doesn't just write text, it *structures the narrative graph*.

### 7.3 The Conversion Pair: Frame ↔ Shot

The two systems convert into each other. This is the key insight the user surfaced.

```
┌─ System 1 (Visual) ──────────────┐     ┌─ System 2 (Narrative) ──────────┐
│                                  │     │                                  │
│  ┌─ Frame: "Character" ────────┐ │  →  │  ┌─ Shot 03 ─────────────────┐ │
│  │  img1  img2  img3           │ │     │  │  images: img1,img2,img3   │ │
│  │  (markup: "hero refs")      │ │  →  │  │  description: "Hero intro"│ │
│  └─────────────────────────────┘ │     │  │  dialogue: "..."          │ │
│                                  │     │  │  camera: "close-up"       │ │
└──────────────────────────────────┘     │  │  duration: 96 frames      │ │
                                         │  └───────────────────────────┘ │
                                         └──────────────────────────────────┘
        frame → shot: "make a shot from this frame"
        shot → frame: "flatten this shot back to a frame"
```

**`bfacw.moodboard_frame_to_shot`** — takes the active frame, creates a `MoodboardShot` with the frame's images, copies the frame's markup into the shot description, and sets `shot.frame_id` for traceability.

**`bfacw.moodboard_shot_to_frame`** — takes the active shot, creates a frame containing its images, links `frame.image_ids` to the shot's ordered images.

**Why two systems**: A reference board is *not* a storyboard. Users collect 100 images of environments, props, and characters before any story exists. If everything were shots, the board would be a rigid timeline. The frame system keeps the board fluid and visual; the shot system adds narrative structure when the story emerges. Conversion preserves the "keep it loose, then tighten it" workflow of real preproduction.

### 7.4 Why Custom Nodes (vs. Pure GPU Cards)?

**Two rendering modes, one data model:**

1. **Card Mode (default)**: GPU-drawn image cards — fast, PureRef-like, no node chrome. Frames render as GPU rectangles with markup. This is the "moodboard" experience.
2. **Node Mode (toggle)**: The same data as native node widgets — full node editor power: sockets, links, groups, frame nodes, annotation. This is the "logic bricks and string nodes" experience.

**The toggle** (`space.show_backdrop`-style property or a header button "Node Mode") switches between them. Both modes read the same `Moodboard` data model — a card in card mode is a `MoodboardImageNode` in node mode; a shot in the footer bar is a `MoodboardShotNode` in node mode.

**Why this is powerful**: The agent can manipulate the node graph directly (add nodes, link them, set text values) via `execute_blender_code` — the same way it manipulates shader/geometry node trees today. The moodboard becomes scriptable by the agent.

### 7.5 The Annotation Brush

The Node Editor's native annotation system (`bpy.ops.node.annotate`) works out of the box. We just need to:
1. Ensure `space.show_annotation = True`
2. Set the active annotation tool when the user picks the Annotate tool
3. Optionally store annotations per-shot (annotations are stored in `space.annotation` — we can snapshot them per shot)

---

## 8. Agent Integration: Vision, Context & Story Workflows

### 8.1 The Vision Bridge — Reusing the Existing Pipeline

The existing vision pipeline (`agent_controller.py`) injects `image_url` content blocks into the next user message. The moodboard reuses this **exactly**:

```python
# In the moodboard's chat send path:
def _attach_selected_image_to_message():
    """Attach the selected moodboard image as vision context."""
    img = get_selected_image()
    if img is None:
        return
    # Convert the image to a base64 data URI.
    data_uri = _image_to_data_uri(img.image)
    # Set the pending image — the agent controller injects it automatically.
    agent_controller._agent_state._pending_image = data_uri
```

**Result**: When the user selects an image and types "Describe this image" or "Create a color grade based on this image in the compositor", the vision-capable LLM sees the image automatically. **Zero changes to the agent controller needed** — the mechanism already exists.

### 8.2 Automatic Context Tagging

When the user selects an image and sends a chat message, the message is **auto-tagged** with the image reference:

```
User: "Create a color grade based on this image in the compositor"
      [attached: moodboard://Concept Art/ref3.jpg]
```

Implementation: the moodboard chat panel prepends a context line to the message text (or sets `_pending_image` + appends a text note). The agent sees both the image AND the reference path.

### 8.3 Agent Tools for the Moodboard (MCP)

New MCP tools (following the toolcode pattern from Tier 6):

| Tool | Purpose |
|---|---|
| `moodboard_get_summary` | List boards, images, shots, sequences (read-only) |
| `moodboard_get_image` | Get an image's metadata + data URI (for vision) |
| `moodboard_add_image` | Add an image from a path/URL to the board |
| `moodboard_add_text` | Add a text/note card |
| `moodboard_link_images` | Link two images (sequence edge) |
| `moodboard_create_shot` | Create a shot from selected images |
| `moodboard_create_sequence` | Create a sequence from shots |
| `moodboard_set_shot_data` | Set shot description/dialogue/camera/duration |
| `moodboard_create_logic_brick` | Create a logic brick (condition + true/false routes) |
| `moodboard_create_string_brick` | Create a string brick (speaker + dialogue line) |
| `moodboard_link_to_brick` | Link an image/shot to a logic or string brick |
| `moodboard_evaluate_branch` | Agent evaluates a logic brick's condition from linked images |
| `moodboard_export_composite` | Render the board (or a shot) to a composite image |
| `moodboard_to_animatic` | Export shots to VSE strips (animatic) |
| `moodboard_to_scene` | Create 3D scene from storyboard (Tier 5+) |

### 8.4 Agentic Story Workflows (The Core Value)

#### Workflow A: Image → Description → Direction

```
1. User selects ref3.jpg on the board
2. User types: "Describe this image and suggest a color palette"
3. Agent (vision) sees the image, describes it, extracts a palette
4. Agent optionally creates a palette card on the board (text node)
5. Agent offers: "Want me to apply this palette as a color grade in the compositor?"
```

#### Workflow B: Image Chain → Script

```
1. User links 6 images into a sequence (shot 1 → shot 2 → ... → shot 6)
2. User types: "Write a script based on this image chain"
3. Agent reads the linked images (vision), infers the story arc
4. Agent writes a script — one beat per shot — into text nodes on the board
5. Agent fills each shot's description + dialogue fields
6. User reviews, edits, re-prompts
```

#### Workflow C: Text → Storyboard Panels

```
1. User types: "A detective walks into a rainy neon alley at midnight"
2. Agent writes a 6-beat story outline (text nodes)
3. Agent generates 6 reference images (Tier 5 gen plugins) — one per beat
4. Images land on the board as a shot sequence
5. Agent fills shot descriptions + camera notes
```

#### Workflow D: Storyboard → Dialogue

```
1. User has a shot sequence with images
2. User types: "Write dialogue for the two characters in shots 3-5"
3. Agent (vision) identifies characters, writes dialogue per shot
4. Dialogue lands in each shot's dialogue field
5. Agent offers: "Want me to export this as a screenplay text block?"
```

#### Workflow E: Storyboard → Scene (Tier 5+)

```
1. User has a locked shot sequence
2. User types: "Block out this storyboard in 3D"
3. Agent creates a scene per shot:
   - Camera per shot (matching the shot's camera description)
   - Blockout geometry (from shot descriptions)
   - Lighting matching the moodboard palette
   - VSE timeline with shot durations (animatic)
```

### 8.5 The System Prompt Enrichment

The moodboard context is injected into the system prompt (following the existing `_get_system_prompt_with_rules()` pattern):

```
The user is working in the Moodboard editor.
Active board: "Concept Art" (12 images, 3 shots, 1 sequence)
Selected image: ref3.jpg (tags: lighting, warm)
Active shot: "Shot 02 — Close-up of the protagonist"
```

---

## 9. Storyboarding System: Shots, Sequences & Animatics

### 9.0 The Two-Layer Storyboard Model

The storyboard system has two layers that map to the two node systems (§7):

| Layer | Node System | UI | Data |
|---|---|---|---|
| **Visual** (frames) | System 1 | Canvas frames + markup | `MoodboardFrame` |
| **Narrative** (shots/chains) | System 2 | Footer shot bar + Shot Panel | `MoodboardShot`, `MoodboardSequence` |

**Frames are fluid** — collect refs, group them, no timeline pressure.
**Shots are structured** — a shot has description, dialogue, camera, duration.
**Conversion is one click** — frame → shot, shot → frame.

### 9.1 The Shot Bar (Footer)

The footer strip bar shows all shots as clickable tabs. Each shot is a colored tab (using Blender's `COLOR_01`-`COLOR_08` collection color tags). Clicking a shot:
- Highlights the shot's images on the canvas
- Loads the shot's data into the Shot Panel
- Sets the active shot for new image additions

### 9.2 Shot Workflow (Two Paths)

**Path A — From frames (visual-first):**
```
1. User collects reference images and groups them into frames
   (e.g. a "Hero Character" frame, a "Neon Alley" frame, a "Rain" frame)
2. User selects a frame and clicks the Convert tool (or presses C)
3. Frame becomes a shot: images preserved, markup → description
4. Shot appears in the footer bar
5. User (or agent) fills in: dialogue, camera, duration
6. User reorders shots by dragging them in the footer bar
```

**Path B — From image chains (narrative-first):**
```
1. User links 6 images into a chain (img1 → img2 → ... → img6)
2. User selects the chain and clicks "Create Shot Sequence"
3. Each link becomes a shot boundary; each image becomes a shot image
4. Agent (optional) fills in descriptions from the image content
5. Shots appear in the footer bar in chain order
```

**Both paths converge** on the same `MoodboardShot` data — the Sequencer doesn't care how a shot was born.

### 9.3 Shot → Scene Strips (The Sequencer Bridge)

This is the "new nodes useful for making scene strips for the sequencer" the user described. Each shot becomes **one or more VSE strips**:

```python
def export_shots_to_sequencer(board, scene):
    """Export the board's shots to VSE strips as an animatic.

    Each shot becomes:
      - Channel 1: IMAGE strip (the shot's first image)
      - Channel 1: TRANSITION strip (crossfade between shots)
      - Channel 2: TEXT strip (the shot's dialogue)
      - Timeline marker at the shot's start frame
    """
    import bpy

    seq_editor = scene.sequence_editor
    if seq_editor is None:
        seq_editor = scene.sequence_editor_create()

    frame_start = 1
    for i, shot in enumerate(board.shots):
        # Image strip (first image of the shot).
        img = board.get_image(shot.image_ids[0])
        strip = seq_editor.strips.new(
            type='IMAGE', name=shot.name,
            filepath=img.source_path,
            channel=1, frame_start=frame_start,
        )
        strip.frame_final_duration = shot.duration_frames

        # Transition between shots (crossfade).
        if i > 0:
            transition = seq_editor.strips.new(
                type='TRANSITION', name=f"{board.shots[i-1].name}_to_{shot.name}",
                channel=2, frame_start=frame_start - 12,  # 12-frame overlap
            )
            transition.sequence_1 = board.shots[i-1].name
            transition.sequence_2 = shot.name

        # Text strip with the shot's dialogue.
        text_strip = seq_editor.strips.new(
            type='TEXT', name=shot.name + "_dialogue",
            channel=3, frame_start=frame_start,
        )
        text_strip.text = shot.dialogue
        text_strip.frame_final_duration = shot.duration_frames

        # Timeline marker.
        scene.timeline_markers.new(shot.name, frame=frame_start)

        frame_start += shot.duration_frames
```

**The reverse bridge** (Sequencer → board): `mcp/blmcp/tools/moodboard_from_sequencer.py` — converts existing VSE strips back into shots on the board. This makes the moodboard a true two-way preproduction hub: build storyboards on the board, refine timing in the Sequencer, send the timeline back to the board.

### 9.4 The "Play Animatic" Operator

`bfacw.moodboard_play_animatic` — exports the board to VSE strips, switches to the Sequencer workspace, and plays the timeline. This is the "story blockout system" moment — the user sees their storyboard as a timed animatic.

### 9.5 Agent-Driven Shot Filling (The Story Leverage)

The real power: **the agent fills the narrative layer from the visual layer**. The two-system split gives the agent clean hooks:

```
1. User has frames on the board (visual layer): "Hero", "Alley", "Rain"
2. User types: "Turn these into a scene where the hero tracks the killer through the rain"
3. Agent (vision) reads the frame images via moodboard_get_image
4. Agent creates shots: frame → shot conversion is agent-driven
5. Agent writes per-shot: description, dialogue, camera notes
6. Agent links shots into a sequence (shot chain)
7. Agent exports the animatic → user reviews timing
8. Agent offers: "Want me to block this out in 3D?"
```

---

## 10. Scene Tooling: From Storyboard to 3D

### 10.1 The Bridge Concept

The moodboard is the **preproduction** layer; the 3D space is the **production** layer. The bridge is a set of operators + agent tools that translate storyboard data into scene data.

### 10.2 Scene Setup Operators

| Operator | Purpose |
|---|---|
| `bfacw.moodboard_to_scene` | Create a scene per shot (or one scene with shot markers) |
| `bfacw.moodboard_setup_cameras` | Create a camera per shot, positioned per the shot's camera description |
| `bfacw.moodboard_blockout` | Create blockout geometry (cubes/planes) from shot descriptions |
| `bfacw.moodboard_apply_palette` | Apply the board's color palette to world/lighting |
| `bfacw.moodboard_to_animatic` | Export to VSE (see §9.3) |

### 10.3 The Agent-Driven Flow (Tier 5+)

```
User: "Block out this storyboard in 3D"

Agent loop:
  1. Reads the board (moodboard_get_summary + moodboard_get_image per shot)
  2. For each shot, creates a scene:
     - Camera: "close-up" → focal length 50mm, positioned per shot framing
     - Blockout: "rainy alley" → two wall planes + ground plane + light
     - Lighting: "neon" → colored point lights matching the palette
  3. Creates a VSE timeline with shot durations (animatic)
  4. Reports: "Blocked out 6 shots. Shot 3 needs a character — want me to add a placeholder?"
```

### 10.4 Shot Markers in the Timeline

Each shot gets a timeline marker (`scene.timeline_markers.new(shot.name, frame)`) so the animatic and the 3D scene stay in sync. This mirrors Blender's built-in Storyboarding template's "master storyboard timeline" concept.

---

## 11. Generation Placeholders: Storybuilding (Tier 5+)

The moodboard is the **primer for Tier 5 generative systems**. This section sketches the placeholder architecture — the seams where generation plugs in later — and calls out the **Tier 5 plan updates needed** to serve the moodboard properly.

### 11.0 Tier 5 Plan Updates Required (from this design)

The Tier 5 plan (`plan_tier5_generative_local_systems.md`) assumed a simple "Moodboard = image drop zone." This design — two node systems, frames, shots, chains — requires Tier 5 updates:

| Tier 5 Component | Current Plan | Needed Update |
|---|---|---|
| Gen output routing | `route_output() → "moodboard"` placeholder | Route to a *frame* or *shot* — a generated image knows its destination (which frame, which shot, which slot) |
| Img2img input | `GenInputSpec.IMAGE` (single ref) | Multi-ref from a frame (`MULTI_IMAGE` — the frame's image set as style anchors) |
| Storyboard panel gen | "Agent generates one image per beat" | The generated images land as a *shot chain* directly in System 2 (not loose cards) |
| Character consistency | Not in Tier 5 plan | IP-Adapter/face-folder from a character frame — the board's character frame becomes the consistency anchor |
| Video (animatic → video) | T2V / I2V plugins | An animatic *is* an I2V input — each shot's image + duration becomes a video-gen segment |
| Audio (dialogue) | Chatterbox TTS | Shot dialogue fields are ready-made TTS prompts — one click per shot |
| 3D (blockout) | Image-to-3D | The shot chain is the input — consistent camera/scene per shot |

**This is why the project is exponentially complex**: each generation mode multiplies with the moodboard's structure (per-image, per-frame, per-shot, per-sequence, per-board). The milestone plan (§12) sequences this carefully — the board ships first as a *usable tool* (Tier 4d), then generation *adds value* (Tier 5), never the reverse.

### 11.1 The Generation Bridge (Already Anticipated)

`gen_controller.py` already routes output to "Sequencer, Image Editor, or Moodboard." The moodboard needs:

```python
# gen_controller.py — output routing (placeholder)
def route_output(job, destination, target_id=None):
    """Route a generated image to the board, optionally into a frame or shot."""
    if destination == "moodboard":
        from . import moodboard as _mb
        _mb.add_generated_image(job.result_path, prompt=job.prompt, target_id=target_id)
```

`target_id` is the `MoodboardFrame.id` or `MoodboardShot.id` the image belongs to.

### 11.2 Generation Placeholders on the Board

| Placeholder | What It Becomes (Tier 5+) |
|---|---|
| **"Generate Variations"** button on Selected Image panel | `gen_controller.generate_async()` with the selected image as img2img input (`GenInputSpec.IMAGE`) |
| **"Generate Variations for Frame"** on Frame panel | Multi-ref generation (`GenInputSpec.MULTI_IMAGE`) — the frame's images as style anchors |
| **"Generate Reference"** button in header | Text-to-image generation landing on the board (default frame) |
| **"Generate Storyboard Panels"** | Agent writes beats → generates one image per beat → creates a *shot chain* in System 2 |
| **"Generate Fill Frame"** on a frame | Generates images to complete a frame (e.g. "fill this palette frame with 3 more warm-toned refs") |
| **"Generate Character Sheet"** | Multi-image generation (IP-Adapter face refs) from a character frame |
| **"Generate Palette"** | Color extraction → palette card |
| **"Generate Dialogue Audio"** on a shot | Chatterbox TTS from `shot.dialogue` — the animatic gets voice |
| **"Generate Hero Image"** on a shot | I2V video gen from the shot's image + duration (animatic → live action) |

### 11.3 The Storybuilding Loop (Vision for Tier 5+)

```
┌─────────────────────────────────────────────────────────────────┐
│                    THE STORYBUILDING LOOP                        │
│                                                                  │
│  User prompt → Agent writes story beats (text nodes)             │
│      ↓                                                           │
│  Agent generates reference images (gen plugins) → board frames   │
│      ↓                                                           │
│  User collects favorites → groups into frames (System 1)         │
│      ↓                                                           │
│  Frames → Shots (convert) → shot chain (System 2)                │
│      ↓                                                           │
│  Agent writes dialogue + camera notes per shot                    │
│      ↓                                                           │
│  Agent exports animatic (VSE) → user reviews timing               │
│      ↓                                                           │
│  [Tier 5] Generate dialogue audio per shot (TTS)                  │
│      ↓                                                           │
│  [Tier 5] Generate video segments per shot (I2V)                  │
│      ↓                                                           │
│  Agent blocks out 3D scenes from the storyboard                   │
│      ↓                                                           │
│  User refines in 3D → renders → feeds renders back to board       │
│      ↓                                                           │
│  (loop: new renders become new reference images)                  │
└─────────────────────────────────────────────────────────────────┘
```

### 11.4 Tie-In to the 3D Space

- **Renders → Board**: `render_viewport_to_path` output can be added as a card ("capture viewport" button). The board becomes a living document that includes production renders.
- **Board → 3D**: The palette, camera notes, and shot descriptions drive scene setup (§10).
- **3D → Board**: Scene screenshots become reference cards for the next iteration.

---

## 12. Milestones, Branches & Functional Steps

The user's instinct is right: this is an **exponentially complex project**. The full vision (moodboard + dual node systems + agent story workflows + Tier 5 generation + scene tooling) is a multi-release effort. This section sequences it into **milestones** (what ships when), **branches** (parallel workstreams), and **functional steps** (testable increments).

### 12.1 The Milestone Map

```
Milestone A (Tier 4d core)     ── the usable moodboard
Milestone B (Tier 4d story)    ── the storyboard
Milestone C (Tier 5 gen-lite)  ── generation lands on the board
Milestone D (Tier 5 gen-full)  ── storybuilding loop
Milestone E (post-Tier 5)      ── the 3D story pipeline
```

### 12.2 Milestone A — "The Board" (Usable Moodboard) — Tier 4d core

**Goal**: Users can *actually use* the moodboard — import, arrange, annotate, save with the blend file. No agent, no story, no generation.

**Functional steps (each shippable/testable):**

| Step | What Works | Verifiable Outcome |
|---|---|---|
| A1 | Data model + Text datablock persistence | Board saves/loads with the `.blend` file |
| A2 | GPU canvas — image cards, grid, pan/zoom | 100 images at 60 FPS |
| A3 | Select/Move/Scale/Rotate tools | PureRef-style manipulation |
| A4 | Import (file select, drag-drop, clipboard, viewport capture) | Images enter the board |
| A5 | Frames + markup (visual grouping) | Group images, annotate the frame |
| A6 | Annotation brush (native node annotation) | Draw directly on the board |
| A7 | Panels, header, footer, preferences | Full editor feel |
| A8 | Composite export | Board renders to a single image |

**Exit criteria**: "I can load my .blend and see my board exactly as I left it."

**Branch**: `tier4d-moodboard-core` (from `main`)

### 12.3 Milestone B — "The Story" (Storyboard) — Tier 4d story

**Goal**: Frames convert to shots, shots chain into sequences, animatics export. The two node systems (§7) are real.

**Functional steps:**

| Step | What Works | Verifiable Outcome |
|---|---|---|
| B1 | Link tool — image-to-image chains (System 2) | Chains visible on canvas |
| B2 | Shot creation (from frame, from chain) | Shots appear in footer bar |
| B3 | Shot data — description, dialogue, camera, duration | Shot panel edits persist |
| B4 | Sequence creation — shot chains | Sequences listed |
| B5 | Frame ↔ Shot conversion (both directions) | One-click conversion |
| B6 | Animatic export → VSE strips + transitions + markers | Timeline plays the storyboard |
| B7 | Sequencer → board import (ship-on-branch) | Two-way bridge |
| B8 | Node Mode — custom node types (Image, Frame, Shot, Sequence, Text, Logic) | Agent-scriptable graph |
| B9 | Logic bricks — condition + true/false routes (branching) | Branching story graph on canvas |
| B10 | String bricks — speaker + dialogue lines per shot | Dialogue flows into animatic text strips |

**Exit criteria**: "I turned my reference frames into a timed animatic with dialogue."

**Branch**: `tier4d-moodboard-story` (from `tier4d-moodboard-core`)

### 12.4 Milestone C — "The Agent Sees" (Agent + Vision) — Tier 4d/5a bridge

**Goal**: The agent sees the board and acts on it (vision context, story workflows, MCP tools). No generation yet.

**Functional steps:**

| Step | What Works | Verifiable Outcome |
|---|---|---|
| C1 | Vision bridge — selected image → `_pending_image` | Agent describes a selected board image |
| C2 | Moodboard chat panel (shared `_draw_chat_interface()`) | Chat in the moodboard editor |
| C3 | MCP read tools (`moodboard_get_summary`, `moodboard_get_image`) | Agent sees the board layout |
| C4 | MCP write tools (`moodboard_add_image`, `moodboard_add_text`, `moodboard_create_shot`, `moodboard_create_sequence`, `moodboard_set_shot_data`) | Agent builds story structure |
| C5 | MCP brick tools (`moodboard_create_logic_brick`, `moodboard_create_string_brick`, `moodboard_link_to_brick`, `moodboard_evaluate_branch`) | Agent builds branching story graphs |
| C6 | Story workflows A/B (image → description, image chain → script) | Agent writes story text onto the board |
| C7 | System prompt enrichment (board context) | Agent knows the active board |

**Exit criteria**: "The agent reads my board and writes a script into my shots."

**Branch**: `tier4d-moodboard-agent` (from `tier4d-moodboard-story`, parallel with Tier 5a gen)

### 12.5 Milestone D — "The Generator" (Generation on the Board) — Tier 5

**Goal**: Generation lands *into* the board's structure (frames, shots, chains). Requires Tier 5b/5c (gen UI panels + MCP gen tools).

**Functional steps:**

| Step | What Works | Verifiable Outcome |
|---|---|---|
| D1 | `route_output("moodboard", target_id)` | Generated images land in the right frame/shot |
| D2 | "Generate Variations" per image (img2img) | Variations appear beside the source |
| D3 | "Generate Variations for Frame" (multi-ref) | Frame style anchors drive multi-ref gen |
| D4 | "Generate Storyboard Panels" (shot chain gen) | Agent: beats → images → shot chain |
| D5 | "Generate Palette" (color extraction) | Palette card from a selection |
| D6 | "Generate Character Sheet" (IP-Adapter from character frame) | Consistent character images |
| D7 | Gen job progress shown in the moodboard chat | Async generation visible on the board |

**Exit criteria**: "I asked the agent for 6 storyboard panels — they landed as a shot chain with descriptions."

**Branch**: `tier5-moodboard-gen` (from `tier5-gen-panels` + `tier4d-moodboard-agent`)

### 12.6 Milestone E — "The Pipeline" (Full Storybuilding Loop) — post-Tier 5

**Goal**: The storybuilding loop (§11.3) runs end-to-end: text → beats → images → frames → shots → dialogue → animatic → audio → video → 3D → renders back to board.

**Functional steps (each a sub-milestone):**

| Step | What Works | Verifiable Outcome |
|---|---|---|
| E1 | Shot → dialogue audio (TTS per shot) | The animatic speaks |
| E2 | Shot → video segment (I2V from shot image + duration) | The animatic moves |
| E3 | Storyboard → 3D blockout (scene per shot, cameras, palette lighting) | 3D scenes match the storyboard |
| E4 | Renders → board (viewport capture to frame) | The production loop closes |
| E5 | Story branching evaluation (agent evaluates logic bricks from linked images) | Agent routes the story through branches |
| E6 | Batch storyboard generation (multi-variant storylines) | Agent proposes 3 story arcs from one board |
| E7 | Branching animatic export (logic bricks → VSE scene strips per branch) | Non-linear animatics playable in VSE |

**Exit criteria**: "From one prompt, my board grew into a voiced, timed, blocked-out animatic — and I can iterate."

**Branch**: `tier5-story-pipeline` (from `tier5-moodboard-gen`)

### 12.7 Branch Dependencies & Merge Strategy

```
main
 └── tier4d-moodboard-core        ← Milestone A (merge to main)
      └── tier4d-moodboard-story  ← Milestone B (merge to main)
           ├── tier4d-moodboard-agent  ← Milestone C (merge to main)
           └── tier5-gen-panels (Tier 5, parallel)
                └── tier5-moodboard-gen ← Milestone D (merge to main)
                     └── tier5-story-pipeline ← Milestone E (merge to main)
```

**Keep `main` green at every milestone**: each milestone is independently shippable. This is the antidote to "exponentially complex" — the complexity is always contained behind a working, usable increment.

### 12.8 Suggested Task Breaking (per feature)

Each functional step above breaks into the **same task template**:

1. **Task 1 — Data**: extend `moodboard.py` (dataclass field, JSON round-trip)
2. **Task 2 — Draw**: extend `ui_moodboard.py` (GPU rendering for the feature)
3. **Task 3 — Interact**: extend `operators_moodboard.py` (modal/tool/operator)
4. **Task 4 — Panel**: extend panels/header/footer (UI control surface)
5. **Task 5 — Agent**: extend MCP tool or story workflow (agent access)
6. **Task 6 — Test**: extend `tests/` (smoke test, save/load round-trip, animatic export)
7. **Task 7 — Docs**: update wiki + skill docs

Example for **B5 (Frame ↔ Shot conversion)**:
```
T1: add frame_id to MoodboardShot; from_dict/to_dict round-trip   (moodboard.py)
T2: draw shot badge on frames originating from shots              (ui_moodboard.py)
T3: BFACW_OT_moodboard_frame_to_shot + shot_to_frame operators    (operators_moodboard.py)
T4: Convert button in Frame panel + Shot panel                    (ui_moodboard.py)
T5: moodboard_frame_to_shot MCP tool                              (mcp/blmcp/tools/)
T6: test: frame → shot → frame preserves images + notes           (tests/)
T7: wiki: "Frames and Shots" section                              (_misc/generate_wiki/)
```

---

## 13. Implementation Plan — Phases

> **Note**: The phases below map to **Milestones A–C** (§12). Milestones D–E (generation + pipeline) are Tier 5 work — see §11.0 for the Tier 5 plan updates.

### Phase 4d.1: Data Model + Persistence (~350 LOC, 1 new file)

**Goal**: The `Moodboard` document model with **Text datablock persistence** (inside the `.blend` file).

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.1.1 | `MoodboardImage`, `MoodboardFrame`, `MoodboardShot`, `MoodboardSequence`, `Moodboard` dataclasses | `moodboard.py` (new) | ~140 |
| 4d.1.2 | `MoodboardLogicBrick`, `MoodboardStringBrick` dataclasses (branching + dialogue) | `moodboard.py` | ~50 |
| 4d.1.3 | Text datablock save/load (`MB_` prefix, JSON round-trip) | `moodboard.py` | ~90 |
| 4d.1.4 | `MoodboardProperties` PropertyGroup (WindowManager) | `moodboard.py` | ~50 |
| 4d.1.5 | Optional external export/import (sharing/backup) | `moodboard.py` | ~20 |

### Phase 4d.2: Canvas Rendering (~450 LOC, 1 new file)

**Goal**: The GPU canvas — image cards, frames, grid, links, selection gizmo.

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.2.1 | `MoodboardCanvas` draw handler (enable/disable) | `ui_moodboard.py` (new) | ~60 |
| 4d.2.2 | `_ThumbnailCache` — downscale + GPU texture cache | `ui_moodboard.py` | ~100 |
| 4d.2.3 | Image card drawing (texture + frame + label) | `ui_moodboard.py` | ~80 |
| 4d.2.4 | Frame drawing (container + title + markup) | `ui_moodboard.py` | ~60 |
| 4d.2.5 | Grid + background (theme-aware via `theme_utils.py`) | `ui_moodboard.py` | ~60 |
| 4d.2.6 | Selection gizmo (move/scale/rotate handles) | `ui_moodboard.py` | ~60 |
| 4d.2.7 | Link drawing (bezier curves) | `ui_moodboard.py` | ~40 |

### Phase 4d.3: Tools + Modal Operators (~400 LOC, 1 new file)

**Goal**: PureRef-style interaction — select, move, scale, rotate, link, group, frame, convert.

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.3.1 | `BFACW_OT_moodboard_select` — click/box select | `operators_moodboard.py` (new) | ~60 |
| 4d.3.2 | `BFACW_OT_moodboard_move` — modal drag (PureRef-style) | `operators_moodboard.py` | ~80 |
| 4d.3.3 | `BFACW_OT_moodboard_scale` / `rotate` — modal gizmo drag | `operators_moodboard.py` | ~80 |
| 4d.3.4 | `BFACW_OT_moodboard_link` — drag link between images | `operators_moodboard.py` | ~50 |
| 4d.3.5 | `BFACW_OT_moodboard_frame` — draw frame around selection | `operators_moodboard.py` | ~50 |
| 4d.3.6 | `BFACW_OT_moodboard_group` — group selection into shot | `operators_moodboard.py` | ~40 |
| 4d.3.7 | `BFACW_OT_moodboard_convert` — frame ↔ shot conversion | `operators_moodboard.py` | ~40 |
| 4d.3.8 | `BFACW_OT_moodboard_annotate` — enable native annotation | `operators_moodboard.py` | ~40 |

### Phase 4d.4: Panels + Header + Footer (~350 LOC, 1 new file)

**Goal**: The full editor chrome — toolshelf, sidebars, header, footer.

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.4.1 | `BFACW_PT_moodboard_tools` — left toolshelf (`RGN_TYPE_TOOLS`) | `ui_moodboard.py` | ~70 |
| 4d.4.2 | `BFACW_PT_moodboard_board` — board info panel | `ui_moodboard.py` | ~50 |
| 4d.4.3 | `BFACW_PT_moodboard_selected` — selected image panel | `ui_moodboard.py` | ~70 |
| 4d.4.4 | `BFACW_PT_moodboard_frame` — frame panel (markup, convert) | `ui_moodboard.py` | ~50 |
| 4d.4.5 | `BFACW_PT_moodboard_shot` — shot panel | `ui_moodboard.py` | ~60 |
| 4d.4.6 | `BFACW_HT_moodboard_header` + `BFACW_HT_moodboard_footer` | `ui_moodboard.py` | ~60 |

### Phase 4d.5: Import + Export (~250 LOC, 2 files)

**Goal**: Get images in and out of the board.

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.5.1 | `BFACW_OT_moodboard_import` — file select multi-import | `operators_moodboard.py` | ~60 |
| 4d.5.2 | Drag-drop handler (File Browser → canvas) | `operators_moodboard.py` | ~50 |
| 4d.5.3 | `BFACW_OT_moodboard_capture_viewport` — viewport → card | `operators_moodboard.py` | ~40 |
| 4d.5.4 | `BFACW_OT_moodboard_export_composite` — board → composite image | `moodboard.py` | ~60 |
| 4d.5.5 | `BFACW_OT_moodboard_to_animatic` — shots → VSE strips | `moodboard.py` | ~40 |

### Phase 4d.6: Agent Integration (~300 LOC, 3 files)

**Goal**: The board talks to the agent — vision context + story workflows.

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.6.1 | `_attach_selected_image_to_message()` — vision bridge (reuses `_pending_image`) | `ui_moodboard.py` | ~40 |
| 4d.6.2 | Moodboard chat panel (shared `_draw_chat_interface()`) | `ui_moodboard.py` | ~60 |
| 4d.6.3 | System prompt enrichment (board context) | `agent_controller.py` | ~40 |
| 4d.6.4 | MCP tools: `moodboard_get_summary`, `moodboard_get_image`, `moodboard_add_image`, `moodboard_add_text`, `moodboard_link_images`, `moodboard_create_shot`, `moodboard_create_sequence`, `moodboard_set_shot_data`, `moodboard_create_logic_brick`, `moodboard_create_string_brick`, `moodboard_link_to_brick`, `moodboard_evaluate_branch`, `moodboard_frame_to_shot`, `moodboard_shot_to_frame` | `mcp/blmcp/tools/moodboard_*.py` (14 files) | ~280 |

### Phase 4d.7: Storyboard System (~250 LOC, 2 files)

**Goal**: Shots, sequences, frame↔shot conversion, and the animatic export.

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.7.1 | Shot bar in footer (clickable shot tabs, reorder) | `ui_moodboard.py` | ~60 |
| 4d.7.2 | Shot data editing (description, dialogue, camera, duration) | `ui_moodboard.py` | ~50 |
| 4d.7.3 | Frame ↔ Shot conversion operators | `operators_moodboard.py` | ~50 |
| 4d.7.4 | `BFACW_OT_moodboard_play_animatic` — export + play | `operators_moodboard.py` | ~50 |
| 4d.7.5 | Timeline markers per shot | `moodboard.py` | ~40 |

### Phase 4d.8: Node Mode + Custom Nodes (~450 LOC, 2 files)

**Goal**: The node-editor-native view — two node systems (Image/Frame + Shot/Sequence), logic bricks, string bricks, scriptable graph.

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.8.1 | System 1 nodes: `MoodboardImageNode`, `MoodboardFrameNode` | `nodes_moodboard.py` (new) | ~100 |
| 4d.8.2 | System 2 nodes: `MoodboardShotNode`, `MoodboardSequenceNode` | `nodes_moodboard.py` | ~100 |
| 4d.8.3 | Brick nodes: `MoodboardLogicNode` (condition + TRUE/FALSE sockets), `MoodboardStringNode` (speaker + text), `MoodboardTextNode` | `nodes_moodboard.py` | ~120 |
| 4d.8.4 | Card ↔ Node sync (same data model, two renderers) | `nodes_moodboard.py` | ~90 |
| 4d.8.5 | "Node Mode" toggle in header | `ui_moodboard.py` | ~40 |
| 4d.8.6 | Annotation integration (`show_annotation` + tool) | `operators_moodboard.py` | ~60 |

### Phase 4d.9: Polish + Preferences (~150 LOC, 2 files)

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.9.1 | Preferences: thumbnail size, default board, frame color | `preferences.py` | ~40 |
| 4d.9.2 | Performance: redraw throttling, cache invalidation | `ui_moodboard.py` | ~40 |
| 4d.9.3 | Error handling (no GPU, missing images, corrupt JSON) | `moodboard.py` | ~40 |
| 4d.9.4 | Registration wiring in `__init__.py` | `__init__.py` | ~30 |

### Total Estimated: ~2,650 LOC across 5 new files + modifications to 4 existing files

| Phase | LOC | New Files | Milestone | Status |
|---|---|---|---|---|
| 4d.1: Data model | ~350 | 1 | A | ❌ Not started |
| 4d.2: Canvas rendering | ~450 | 1 | A | ❌ Not started |
| 4d.3: Tools + modal ops | ~400 | 1 | A | ❌ Not started |
| 4d.4: Panels + chrome | ~350 | 0 | A | ❌ Not started |
| 4d.5: Import/export | ~250 | 0 | A | ❌ Not started |
| 4d.6: Agent integration | ~300 | 14 (MCP tools) | C | ❌ Not started |
| 4d.7: Storyboard system | ~250 | 0 | B | ❌ Not started |
| 4d.8: Node mode | ~450 | 1 | B | ❌ Not started |
| 4d.9: Polish + prefs | ~150 | 0 | A | ❌ Not started |

---

## 14. Summary of Changes

| File | Change |
|---|---|
| `addon/bfa_coworker/moodboard.py` (new) | Data model (Image/Frame/Shot/Sequence/Board), Text datablock persistence, composite export, animatic export |
| `addon/bfa_coworker/ui_moodboard.py` (new) | Canvas rendering, thumbnail cache, frame drawing, panels, header, footer, chat panel |
| `addon/bfa_coworker/operators_moodboard.py` (new) | Select/move/scale/rotate/link/frame/group/convert/annotate/import/export operators |
| `addon/bfa_coworker/nodes_moodboard.py` (new) | Custom node types (Image, Frame, Shot, Sequence, Text, Logic) + card↔node sync |
| `addon/bfa_coworker/theme_utils.py` (new, from Tier 4) | Theme-aware color access for GPU drawing |
| `addon/bfa_coworker/agent_controller.py` | System prompt enrichment (board context) |
| `addon/bfa_coworker/preferences.py` | Moodboard preferences (thumbnail size, defaults, frame color) |
| `addon/bfa_coworker/__init__.py` | Registration wiring |
| `mcp/blmcp/tools/moodboard_*.py` (15 new) | MCP tools for the agent (incl. brick tools) |
| `_misc/plan_tier5_generative_local_systems.md` | **Update**: generation routing to frames/shots, multi-ref from frames, shot-chain gen, character consistency, animatic→video, dialogue→TTS, storyboard→3D |

---

## 15. Key Decisions

| Decision | Rationale |
|---|---|
| **Node Editor is the shell** | Annotation brush is built in, toolshelf is ready, node-style linking matches the "link images" requirement, infinite canvas pan/zoom is native |
| **File Browser is the import path** | Native drag-drop + file select. We do NOT take over the File Browser space — too much native UI to hide |
| **GPU canvas for cards** | Native node widgets are too heavy for 100+ image cards. GPU textures are the performance answer |
| **Two render modes, one data model** | Card mode (PureRef-like) + Node mode (logic bricks/string nodes). The agent can script the node graph via `execute_blender_code` |
| **Reuse the `_pending_image` vision pipeline** | Zero changes to the agent controller — the moodboard just sets the pending image data URI |
| **Text datablock persistence (inside the `.blend` file)** | The board lives with the file — no external sidecar files, no "load the moodboard separately" friction. JSON is embedded in `bpy.data.texts` |
| **Text datablocks are backwards compatible** | They are standard Blender data — save/load with the file, visible in the Outliner, diffable, editable in the Text Editor |
| **Thumbnail cache with downscale-once** | 100+ images at 60 FPS. Full-res only for the selected image |
| **Shots/sequences in the data model from day one** | The board grows into a storyboard without migration |
| **Two node systems (visual + narrative), one data model** | Frames stay fluid; shots add structure. Conversion preserves the "keep it loose, then tighten it" workflow |
| **Logic bricks for branching, string bricks for dialogue** | Story structure is first-class, not a text dump. Images link INTO logic bricks as condition refs; string bricks feed dialogue to shots AND the animatic AND (Tier 5) TTS |
| **Animatic export to VSE** | Blender's native timeline is the storyboard's "play" surface |
| **Theme-aware via `theme_utils.py`** | Every GPU-drawn pixel reads from the active theme. No hardcoded colors |
| **MCP tools follow the toolcode pattern** | Same as Tier 6 — read tools first, then write tools, then feedback tools |

---

## 16. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Node Editor space conflicts with real node editing | Medium | Medium | Custom header + `poll()` checks; the moodboard is opt-in (user opens it deliberately) |
| GPU overlay performance with many images | Medium | Medium | Thumbnail cache, redraw throttling, downscale-once |
| Annotation brush conflicts with our canvas | Low | Medium | Annotations are native (`space.annotation`) — they render above our canvas naturally |
| Custom node types break across Blender versions | Medium | Medium | Version-guard node registration; card mode is the fallback |
| Vision models without vision support | Medium | Low | Graceful degradation — text-only models get the image path + description instead of the image |
| Text datablocks bloat the `.blend` file | Low | Low | Board JSON is tiny (images referenced by name, notes/tags are small). Packed images are the same size as any packed image |
| VSE API differences (Bforartists vs Blender) | Medium | Medium | Use `strips` API (5.x), test on Bforartists before committing |
| Memory pressure from many full-res images | Medium | Medium | Auto-downscale on import (pref: max import resolution), warn on memory pressure |
| Logic brick branching gets complex | Medium | Medium | The **linear default** (no bricks) always works; bricks are an opt-in layer. The agent helps create/evaluate branches |

---

## 17. Further Considerations

1. **Hotkeys**: Register a keymap for the moodboard tools (G = group, L = link, A = annotate, V = move). Follows Blender conventions.
2. **Multi-board workflow**: Users can have multiple boards (Concept Art, Color Script, Character Design) and switch via the header selector. Boards are independent `MB_` Text datablocks in the same `.blend` file — they all travel with the file.
3. **Board sharing**: Future — export a board as a single composite image + JSON bundle for sharing (or MCP transfer).
4. **The "Send to" menu**: Selected image → Shader (create image texture node), Image Editor (set active image), Compositor (create image node), VSE (create strip). Each is a small operator.
5. **Color script boards**: A specialized board type where images are ordered by scene/beat and tagged with palette colors — the "color moodboard" from the user's vision.
6. **The storyboard ↔ Grease Pencil bridge**: Blender's built-in Storyboarding template uses Grease Pencil. Our moodboard could export shots as GP frames for hand-drawn refinement.
7. **Agent "board awareness"**: The agent should be able to *see* the board layout (image positions, links) via `moodboard_get_summary` — not just individual images. This enables "rearrange my board by story order" prompts.
8. **Tier 5 dependency**: The generation placeholders (§11) depend on Tier 5b/5c (UI panels + MCP gen tools). The moodboard's `route_output("moodboard")` seam is ready now.
9. **Performance target**: 100 images at 60 FPS on a mid-range GPU. Test with 500 images to find the ceiling.
10. **The logic/string brick system (§7.2b)**: Images link INTO logic bricks as condition references; shots link OUT as TRUE/FALSE routes; string bricks feed dialogue to shots, animatic text strips, and (Tier 5) TTS audio. The agent creates, fills, evaluates, and re-routes branches — this is the storybuilding leverage.
11. **Brick → Twine/outline export**: A logic-brick board can export as a Twine-style branching outline (markdown or JSON) for text-based review or sharing.