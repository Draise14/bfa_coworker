# BFA Coworker — Tier 4: Deep Editor Integration Plan

**Date**: 2026-08-26
**Status**: Implementation Plan — Fusion (GPU Takeover + Hybrid, BOTH)
**Depends on**: Tier 1-3 features (chat UI, agent controller, MCP tools)

---

## Overview

This plan builds **both** approaches into one coherent system — not "one or the other."
The goal is a deeply integrated Blender editor with a consistent UX that feels like
natural language interfacing with the rest of the interface. All implementations are
**pure Python addon** — no C++ fork required.

**Three pillars**:
1. **Coworker Editor** — A dedicated editor with rich chat, prompt system, and agent controls
2. **Viewport Agent Feedback** — Real-time visual overlays showing what the agent is doing
3. **Moodboard Editor** — A visual reference board integrated with the agent (deferred to Tier 5)

**Two delivery modes, both shipped**:
- **Mode 1: Dedicated Agent Editor** — Full-area GPU takeover in the center + sidebar panels (queue, status, diagnostics) + optional Bforartists-style iconized macro toolshelf on the left
- **Mode 2: Contextual Agent Sidebar** — Per-editor shorthand chat in every editor's N-panel, with editor-specific templates

Both modes share the same `_draw_chat_interface()` core component, so feature parity
is guaranteed. The GPU takeover is the "dedicated editor" experience; the sidebar
panels are the "contextual agent everywhere" experience.

---

## Technical Foundation: What's Possible from Python

| Capability | Feasibility | Approach |
|---|---|---|
| Custom Space types | ❌ C++ only | Use existing spaces + GPU takeover or sidebar panels |
| **GPU draw handler takeover** of `SpaceTextEditor` | ✅ | Hide native regions, paint full UI with `gpu`/`blf` |
| **Custom Header** registration | ✅ | `bpy.types.Header` subclass — registerable like Panel |
| GPU 2D overlays (text, rects, images) | ✅ | `draw_handler_add(POST_PIXEL)` + `gpu` + `blf` |
| GPU 3D overlays (lines, wireframes) | ✅ | `draw_handler_add(POST_VIEW)` + built-in shaders |
| Custom GLSL shaders | ✅ | `gpu.types.GPUShaderCreateInfo` |
| Offscreen rendering | ✅ | `gpu.types.GPUOffScreen` |
| Modal operators (input capture) | ✅ | `invoke()` → `modal_handler_add()` → `modal()` |
| Modal + timer hybrid | ✅ | `bpy.app.timers.register()` inside modal |
| Programmatic workspace creation | ✅ | `bpy.data.workspaces.new()` + `bpy.ops.screen` operators |
| Screen layout manipulation | ⚠️ | `bpy.ops.screen.area_split/join/close` — needs pixel coordinates |
| Image Editor as canvas | ✅ | Create composite images + GPU overlay annotations |
| Local image generation | ⚠️ | `diffusers` library — large models (2-6 GB), CPU or GPU |

### The GPU Takeover Pattern — Key Insight

**Blender Python addons CANNOT create new `bpy.types.Space` subclasses** (C++ only).
But they **CAN** take over an existing Space type completely via GPU draw handlers:

1. Pick a space type (e.g., `SpaceTextEditor`)
2. Hide all native regions: `show_region_header = False`, `show_region_ui = False`, etc.
3. Register a `POST_PIXEL` draw handler that paints the entire area with `gpu`/`blf`
4. Register a custom `bpy.types.Header` subclass for that space type
5. Use a modal operator (`invoke()` → `modal_handler_add()` → `modal()` event loop)
   to capture keyboard/mouse input for the custom UI

This is the closest a Python addon can get to a "custom editor" — **80% of the native
feel with 0% of the C++ build system headache**. Blender's own interactive tools
(Knife, Transform, Annotation) use this exact pattern. FreePencil2 also uses this
approach — it doesn't create a custom space, it uses standard sidebar panels with
GPU-enhanced drawing.

**Why `SpaceTextEditor` is the best takeover target:**
- Has minimal native UI (just a text area with line numbers)
- `region_location_from_cursor(line, column)` gives cursor-to-pixel mapping
- `font_size` and `show_line_numbers` properties for text rendering control
- Can set `text` to a `bpy.data.texts` data-block programmatically
- Users intuitively understand it as a "text/code" context — natural fit for chat

### Bforartists Region Architecture (Verified from Source)

**Text Editor regions** (`space_text.cc`):
- `RGN_TYPE_HEADER` (top)
- `RGN_TYPE_FOOTER` (bottom)
- `RGN_TYPE_UI` (right sidebar — where our chat panel lives)
- `RGN_TYPE_WINDOW` (main area — C++-drawn `text_main_region_draw`)
- **NO `RGN_TYPE_TOOLS`** — Text Editor has no native left tools region

**Editors WITH a native `RGN_TYPE_TOOLS` (left bar)**:
- `VIEW_3D`, `IMAGE_EDITOR`, `NODE_EDITOR`, `SEQUENCE_EDITOR`, `CLIP_EDITOR`, `SPREADSHEET`, `FILE_BROWSER`

**File Browser** has both left (`RGN_TYPE_TOOLS`) and right (`RGN_TYPE_UI`) bars, plus `RGN_TYPE_TOOL_PROPS` and `RGN_TYPE_EXECUTE`.

### BREAKTHROUGH: Python Panels CAN Draw in the Center of USERPREF & PROPERTIES

**This is the single most important discovery for the design.**

The **Preferences editor** (`space_userpref.cc`) and **Properties editor**
(`space_buttons.cc`) draw their **center (WINDOW region) with `ED_region_panels_draw`**:

```c
// space_userpref.cc — Preferences editor
art->regionid = RGN_TYPE_WINDOW;
art->init = userpref_main_region_init;
art->layout = userpref_main_region_layout;   // → ED_region_panels_layout_ex
art->draw = ED_region_panels_draw;           // ← Python panels render here!
userpref_panels_register(*art);

// space_buttons.cc — Properties editor
art->regionid = RGN_TYPE_WINDOW;
art->draw = ED_region_panels_draw;           // ← Python panels render here!
```

This means **Python `bpy.types.Panel` classes CAN render in the center** of these
editors — with `bl_space_type='USERPREF'` (or `'PROPERTIES'`) and
`bl_region_type='WINDOW'`. The `bl_space_type` enum includes `USERPREF` and
`PROPERTIES` (verified in `rna_ui.cc`), and `bl_region_type` includes `WINDOW`
(verified in `DNA_screen_types.h`).

**The Preferences editor layout is exactly what we want:**
- **Left (UI region)**: `userpref_navigation_region_draw` — the nav tabs (Interface, Editing, Themes, Add-ons, etc.)
- **Center (WINDOW region)**: `ED_region_panels_draw` — Python panels filtered by `bl_context`
- **Bottom (EXECUTE region)**: action buttons

**This is the "clone the Preferences editor" approach the user proposed.**

### The "Clone the Preferences Editor" Architecture

Instead of GPU-painting the Text Editor center (fickle to maintain), we build the
dedicated Coworker editor by **cloning the Preferences editor pattern**:

```
┌──────────────────────────────────────────────────────────────────┐
│  [Coworker]  🟢 Connected           [Start] [Stop] [Floating]   │  ← Custom Header
├──────────┬───────────────────────────────────────────┬───────────┤
│  Left    │  Center (Python panels, WINDOW region)    │  Right    │
│  Nav     │  ┌─────────────────────────────────────┐  │  Sidebar  │
│  (UI)    │  │ Chat Panel (bl_context="chat")       │  │  (UI)     │
│          │  │  ┌───────────────────────────────┐   │  │           │
│  [Chat]  │  │  │ ✅ Turn 1  Create a cube      │   │  │  Queue    │
│  [Queue] │  │  │ ▶ Running Tools (collapsed)   │   │  │  Status   │
│  [Status]│  │  │ ✨ Coworker: I've created...   │   │  │  Diag     │
│  [Rules] │  │  └───────────────────────────────┘   │  │  Rules    │
│  [Log]   │  │  ┌───────────────────────────────┐   │  │           │
│  [Macros]│  │  │ > Make it spin...             │   │  │           │
│          │  │  └───────────────────────────────┘   │  │           │
│          │  │  [Send] [Clear] [Stop]  Mode: ●Agent │  │           │
│          │  └─────────────────────────────────────┘  │           │
└──────────┴───────────────────────────────────────────┴───────────┘
```

**How it works:**
1. **Space type**: `USERPREF` (Preferences editor) — the only editor where Python
   panels render in the center AND there's a left nav region
2. **Left nav (UI region)**: Our own nav tabs — Chat, Queue, Status, Rules, Log, Macros
   - These are Python panels with `bl_region_type='UI'` + `bl_context` matching
3. **Center (WINDOW region)**: The chat panel — Python panel with
   `bl_space_type='USERPREF'`, `bl_region_type='WINDOW'`, `bl_context='chat'`
4. **Right sidebar**: Optional — the USERPREF space has a UI region on the right too
   (or we use the left nav only, like the real Preferences editor)

**Why this is better than GPU takeover:**
- ✅ **100% native theming** — every widget inherits the active theme automatically
- ✅ **Native text input** — Blender's textbox handles cursor, selection, clipboard, IME
- ✅ **Native scrolling** — Blender's panel scrolling works out of the box
- ✅ **Native panel drawing** — `layout.box()`, `layout.panel()`, `layout.prop()`
- ✅ **Low maintenance** — survives Blender version updates
- ✅ **No modal operator conflicts** — works alongside all other tools
- ✅ **Accessibility** — screen readers can see panel content
- ✅ **Left nav for macros** — the nav region gives us the Bforartists-style iconized shelf for free
- ✅ **~200 LOC** — no GPU drawing, no modal operator, no theme_utils.py needed for the center

**What we lose vs GPU takeover:**
- ❌ No custom bubble styling (uses standard `layout.box()`)
- ❌ No full-area immersive canvas (center is panel-width, not full-width)
- ❌ The center is panel-based, not pixel-based

**The tradeoff is clearly worth it** — the user's instinct is correct. This is the
way forward.

### Sidebar Placement: Left by Default, Flippable to Right (Verified from Source)

The USERPREF navigation region defaults to the **left** (`RGN_ALIGN_LEFT`, verified
at `space_userpref.cc:66`). But the **F5 key** toggles it to the right via
`SCREEN_OT_region_flip` (`screen_ops.cc:6133`), which flips `region->alignment`
between `RGN_ALIGN_LEFT` and `RGN_ALIGN_RIGHT`.

This gives us a full "blank slate" for the Coworker editor:
- **Left nav default** — matches the real Preferences editor, feels native
- **F5 flip** — users can move the nav to the right if they prefer (like other sidebars)
- Our setup operator could call `SCREEN_OT_region_flip` after area creation to
  default the nav to the right if we ever want that (e.g., to match the N-panel
  convention where the chat sidebar lives on the right in Mode 2)

The layout is effectively: **nav region (left, flippable) + Python-panel center +
execution region (bottom)** — a complete, native, theme-consistent canvas.

### Window Title Behavior (Verified from Source)

From `wm_window.cc` `wm_window_title_text()`:

```c
if (win->parent || WM_window_is_temp_screen(win)) {
    /* Not a main window. */
    bScreen *screen = WM_window_get_active_screen(win);
    const bool is_single = screen && BLI_listbase_is_single(&screen->areabase);
    ScrArea *area = (screen) ? static_cast<ScrArea *>(screen->areabase.first) : nullptr;
    if (is_single && area && area->spacetype != SPACE_EMPTY) {
        return IFACE_(ED_area_name(area).c_str());
    }
    return "Bforartists";
}
```

**Key finding**: When a window is a **temp screen** (floating window created via
`wm.window_new()`), and it has a **single area**, the title is `ED_area_name(area)`.
`ED_area_name()` uses `area->type->space_name_get(area)` if defined, else the
`rna_enum_space_type_items` name.

- **Text Editor** does NOT define `space_name_get` → title is **"Text"**
- **3D Viewport** → title is **"3D Viewport"**
- **File Browser** → title is **"File Browser"**

**Implication for floating window**: If we duplicate the Coworker editor (a Text
Editor area) to a floating window, the title will be **"Text"** — NOT "Coworker".
To get a "Coworker" title, we have two options:
1. **Accept "Text"** — the custom header inside the window says "Coworker", so the
   title is secondary. Simple, zero risk.
2. **Use a custom SpaceType name** — not possible from Python (C++ only).
3. **Rename via OS window title** — not exposed to Python.

**Recommendation**: Accept the "Text" window title for the floating window. The
custom header + panel branding inside the window makes the identity clear. This
matches how Blender itself names temp windows (e.g., a floating Image Editor says
"Image Editor", not a custom name).

---

## Design Proposal: Pro-Con Analysis

### Theming Consistency — The Core Design Principle

**User requirement**: The center of the editor must feel "built in" — using common
theme settings and panel drawing like all other editors. No visual deviation.

**Technical reality** (verified from source): The **Preferences editor** and
**Properties editor** draw their center (WINDOW region) with `ED_region_panels_draw`
— meaning **Python panels CAN render in the center of those editors**. This is the
breakthrough that makes the "clone the Preferences editor" approach viable.

| Approach | Theming | Feels Built-In? | Effort |
|----------|---------|:---------------:|:------:|
| **A. Clone Preferences editor** — `USERPREF` space, Python panels in center (WINDOW) + left nav (UI) | ✅ 100% native `layout.box()`, `layout.panel()` | ✅ Exactly like the real Preferences editor | ~250 LOC |
| **B. Native sidebar only** — chat in `UI` region panels, center shows welcome/empty | ✅ 100% native | ✅ Exactly like every other panel | ~200 LOC |
| **C. GPU-painted center** — `draw_handler_add(POST_PIXEL)` + `gpu`/`blf`, colors via `theme_utils.py` | ⚠️ Close, but manual theme reading | ⚠️ Trained eye can tell | ~800 LOC |

**The "native-first" principle** (adopted):
1. **All interactive elements** (input, buttons, panels, templates) use **native Blender widgets** — `layout.box()`, `layout.panel()`, `layout.prop()`, `layout.operator()`. These inherit the active theme automatically.
2. **GPU drawing is reserved for decorative accents only** — bubble backgrounds, status dots, separators — and even those read theme colors via `theme_utils.py`.
3. **No hardcoded colors anywhere.** Every drawn pixel comes from `bpy.context.preferences.themes[0]`.
4. **If a native widget can do it, use the native widget.** GPU drawing is the last resort, not the default.

**Recommendation**: **Approach A (clone the Preferences editor)** is the way
forward. It gives us:
- **Center (WINDOW region)**: Python panels — the chat canvas, fully native
- **Left nav (UI region)**: Nav tabs — Chat, Queue, Status, Rules, Log, Macros
- **Bottom (EXECUTE region)**: Action buttons
- **100% native theming** — no GPU drawing, no `theme_utils.py` needed for the center

This directly answers the user's question: **yes, the center CAN be drawn with
Python panels using common theme settings** — by using the `USERPREF` space type
instead of `TEXT_EDITOR`.

---

### Mode 1: Dedicated Agent Editor (Clone the Preferences Editor)

The dedicated Coworker editor uses the **`USERPREF` (Preferences) space type** —
the only editor where Python panels render in the center (WINDOW region) with a
left nav (UI region).

```
┌──────────────────────────────────────────────────────────────────┐
│  [Coworker]  🟢 Connected           [Start] [Stop] [Floating]   │  ← Custom Header
├──────────┬───────────────────────────────────────────┬───────────┤
│  Left    │  Center (Python panels, WINDOW region)    │  Right    │
│  Nav     │  ┌─────────────────────────────────────┐  │  Sidebar  │
│  (UI)    │  │ Chat Panel (bl_context="chat")       │  │  (UI)     │
│          │  │  ┌───────────────────────────────┐   │  │           │
│  [Chat]  │  │  │ ✅ Turn 1  Create a cube      │   │  │  Queue    │
│  [Queue] │  │  │ ▶ Running Tools (collapsed)   │   │  │  Status   │
│  [Status]│  │  │ ✨ Coworker: I've created...   │   │  │  Diag     │
│  [Rules] │  │  └───────────────────────────────┘   │  │  Rules    │
│  [Log]   │  │  ┌───────────────────────────────┐   │  │           │
│  [Macros]│  │  │ > Make it spin...             │   │  │           │
│          │  │  └───────────────────────────────┘   │  │           │
│          │  │  [Send] [Clear] [Stop]  Mode: ●Agent │  │           │
│          │  └─────────────────────────────────────┘  │           │
└──────────┴───────────────────────────────────────────┴───────────┘
```

**How it works:**
1. **Space type**: `USERPREF` (Preferences editor) — Python panels render in the center
2. **Left nav (UI region)**: Our own nav tabs — Chat, Queue, Status, Rules, Log, Macros
   - Python panels with `bl_region_type='UI'` + `bl_context` matching
3. **Center (WINDOW region)**: The chat panel — Python panel with
   `bl_space_type='USERPREF'`, `bl_region_type='WINDOW'`, `bl_context='chat'`
4. **Right sidebar**: Optional — the USERPREF space has a UI region on the right too

**Pros:**
- **100% native theming** — every widget inherits the active theme automatically
- **Native text input** — Blender's textbox handles cursor, selection, clipboard, IME
- **Native scrolling** — Blender's panel scrolling works out of the box
- **Native panel drawing** — `layout.box()`, `layout.panel()`, `layout.prop()`
- **Low maintenance** — survives Blender version updates
- **No modal operator conflicts** — works alongside all other tools
- **Accessibility** — screen readers can see panel content
- **Left nav for macros** — the nav region gives us the Bforartists-style iconized shelf for free
- **~250 LOC** — no GPU drawing, no modal operator, no `theme_utils.py` needed for the center

**Cons:**
- **No custom bubble styling** — uses standard `layout.box()` appearance
- **No full-area immersive canvas** — center is panel-width, not full-width
- **The center is panel-based, not pixel-based** — less "wow factor" than GPU

**Best for:** Users who want the chat available everywhere with minimal friction
and maximum theme consistency. The pragmatic choice — works today, works tomorrow,
works in every editor.

---

### Mode 2: Contextual Agent Sidebar (Per-Editor N-Panel)

The contextual agent lives in every editor's N-panel (right sidebar) as a compact
chat panel with editor-specific templates. Uses Blender's native UI system entirely.

**Pros:**
- **Zero custom drawing** — all native Blender widgets
- **Native theming** — panels inherit theme automatically
- **Native text input** — Blender's textbox handles cursor, selection, clipboard, IME
- **Native scrolling** — Blender's panel scrolling works out of the box
- **Low maintenance** — survives Blender version updates
- **No modal operator conflicts** — works alongside all other tools
- **Accessibility** — screen readers can see panel content
- **~200 LOC** for the editor registration (vs ~800 for GPU takeover)
- **Works in all editor types** — not just Text Editor

**Cons:**
- **Sidebar width constraints** — chat is limited to the N-panel width
- **No custom bubble styling** — uses standard `layout.box()` appearance
- **No tabs** — Chat, Prompts, Settings must be separate panels or collapsible sections
- **Less "wow factor"** — looks like a panel, not a custom editor
- **Sidebar must be open** — takes up screen real estate in the N-panel

**Best for:** The "agent everywhere" experience — quick contextual help in whatever
editor the user is working in.

---

### Decision Matrix — Both, with Different Roles

| Criterion | Mode 1 (Dedicated Editor, native-first) | Mode 2 (Contextual Sidebar) |
|-----------|:-------------------------:|:---------------------------:|
| Visual polish | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| Blender version resilience | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Implementation effort | ~200 LOC | ~200 LOC |
| Maintenance burden | Low | Low |
| Works in all editors | ❌ (Text Editor only) | ✅ (15 editor types) |
| Native text input | ✅ | ✅ |
| Native theming | ✅ (automatic) | ✅ (automatic) |
| Accessibility | ✅ | ✅ |
| Floating window support | ✅ (native window) | ✅ (native window) |
| "Wow factor" | Moderate | Moderate |
| **Role** | **Immersive workspace** | **Everywhere context** |

**Decision**: **Ship BOTH.** Mode 1 is the dedicated Coworker Editor (native
sidebars + themed center). Mode 2 is the contextual agent sidebar in every editor.
Both share the same `_draw_chat_interface()` core, so feature parity is guaranteed.
Mode 1 uses the `USERPREF` space type with Python panels in the center — no GPU
drawing needed for the main canvas.

---

## Pillar 1: The Coworker Editor — BOTH Modes

### Vision

A chat interface that follows the user everywhere — a **dedicated editor** for the
immersive agent workspace (Mode 1) AND a **contextual sidebar** in every editor (Mode 2).

### Architecture: Shared Core, Two Delivery Modes

```
                    ┌─────────────────────────────┐
                    │  _draw_chat_interface()     │  ← Shared core (Phase 1)
                    │  (bubbles, input, history)  │
                    └─────────────┬───────────────┘
                                  │
                 ┌────────────────┴────────────────┐
                 │                                 │
        ┌────────▼─────────┐             ┌─────────▼──────────┐
        │ Mode 1:           │             │ Mode 2:            │
        │ Dedicated Editor  │             │ Contextual Sidebar │
        │ (USERPREF clone)  │             │ (all editors)      │
        │                   │             │                    │
        │ Python panels in  │             │ Native N-panel     │
        │ center (WINDOW)   │             │ in 15 editor types │
        │ + left nav (UI)   │             │ + editor templates │
        └───────────────────┘             └────────────────────┘
```

### Mode 1: Dedicated Editor Design (Clone the Preferences Editor)

```
┌──────────────────────────────────────────────────────────────────┐
│  [Coworker]  🟢 Connected           [Start] [Stop] [Floating]   │  ← Custom Header
├──────────┬───────────────────────────────────────────┬───────────┤
│  Left    │  Center (Python panels, WINDOW region)    │  Right    │
│  Nav     │  ┌─────────────────────────────────────┐  │  Sidebar  │
│  (UI)    │  │ Chat Panel (bl_context="chat")       │  │  (UI)     │
│          │  │  ┌───────────────────────────────┐   │  │           │
│  [Chat]  │  │  │ ✅ Turn 1  Create a cube      │   │  │  Queue    │
│  [Queue] │  │  │ ▶ Running Tools (collapsed)   │   │  │  Status   │
│  [Status]│  │  │ ✨ Coworker: I've created...   │   │  │  Diag     │
│  [Rules] │  │  └───────────────────────────────┘   │  │  Rules    │
│  [Log]   │  │  ┌───────────────────────────────┐   │  │           │
│  [Macros]│  │  │ > Make it spin...             │   │  │           │
│          │  │  └───────────────────────────────┘   │  │           │
│          │  │  [Send] [Clear] [Stop]  Mode: ●Agent │  │           │
│          │  └─────────────────────────────────────┘  │           │
└──────────┴───────────────────────────────────────────┴───────────┘
```

**Key design decisions for Mode 1 (clone the Preferences editor)**:
- **Space type**: `USERPREF` — the only editor where Python panels render in the center (WINDOW region) with a left nav (UI region)
- **Center (WINDOW region)**: The chat panel — Python panel with `bl_space_type='USERPREF'`, `bl_region_type='WINDOW'`, `bl_context='chat'`
  - Uses `layout.box()`, `layout.panel()`, `layout.prop()` — 100% native theming
  - Native text input (textbox), native scrolling, native panel headers
- **Left nav (UI region)**: Our own nav tabs — Chat, Queue, Status, Rules, Log, Macros
  - Python panels with `bl_region_type='UI'` + `bl_context` matching
  - This IS the Bforartists-style iconized toolshelf — native, not GPU-painted
- **Right sidebar (UI region, optional)**: Extra panels if needed
- **Custom header**: `BFACW_HT_coworker_header` — branding + status + quick actions (native Header subclass)
- **NO GPU drawing needed for the main canvas** — the user's instinct is confirmed correct

### Mode 2: Contextual Sidebar Design

```
┌──────────────────────────────────────────────────────────────────┐
│  3D Viewport  [Layout] [Modeling] [Sculpting]                   │
├──────────────────────────────────────────────────────────────────┤
│                                                  │  Coworker     │
│                                                  │  ┌─────────┐  │
│                                                  │  │Templates│  │
│                                                  │  │[Add Obj] │  │
│                                                  │  │[Add Mat] │  │
│                                                  │  └─────────┘  │
│                                                  │  ┌─────────┐  │
│                                                  │  │Q&A      │  │
│                                                  │  │> Make it│  │
│                                                  │  │spin     │  │
│                                                  │  │[Send]   │  │
│                                                  │  └─────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### VS Code-Style Prompting

**Inline agent commands** — Type `/` in the chat input to access commands:
- `/fix` — "Fix the selected object/modifier/setting"
- `/explain` — "Explain what this node tree does"
- `/create` — "Create a new [object/material/scene]"
- `/search` — "Search the Blender manual for..."
- `/optimize` — "Optimize the selected mesh"
- `/doc` — "Generate documentation for this node group"

**Agent suggestions** — The agent proactively suggests next steps:
- After creating an object: "Would you like me to add a material?"
- After an error: "I noticed the modifier failed. Would you like me to fix it?"
- On idle: "You have 3 objects without materials. Want me to set them up?"

**Context-aware prompts** — The system prompt is dynamically enriched with:
- Current selection (object names, types)
- Active tool/mode
- Recent operations (from operation history log)
- Scene statistics (vertex count, material count, etc.)

---

## Per-Editor Integration Design

The chat panel must feel native in every editor type. Each editor has different
screen real estate, different user workflows, and different expectations. Below
is the per-editor integration strategy.

### Editor Type Catalog

| Editor | Left Bar? | Right Panel | Templates | Context Enrichment | Notes |
|--------|:---------:|------------|-----------|--------------------|-------|
| **VIEW_3D** | ✅ native tools | Sidebar (existing) | Add Object, Add Material, Add Modifier, Sculpt, Animate, Render | Active object, mode, viewport shading | Primary chat location. Keep existing panel. Can also add icons to native toolshelf. |
| **TEXT_EDITOR** | ❌ (paint our own) | Sidebar + Custom Header + GPU center | Run Script, Explain Code, Fix Errors, Document | Active text datablock, cursor position | **Dedicated Coworker editor target.** GPU takeover center + GPU macro shelf. |
| **NODE_EDITOR** | ✅ native tools | Sidebar | Add Node, Connect, Explain, Clean Up | Active node tree, selected nodes, tree type | Tree-type detection for template relevance. |
| **IMAGE_EDITOR** | ✅ native tools | Sidebar | Edit Image, Add Reference, Generate | Active image, paint mode, UV mode | Useful for moodboard workflows (Tier 5). |
| **SEQUENCE_EDITOR (VSE)** | ✅ native tools | Sidebar | Add Strip, Edit Strip, Render, Add Effect | Active strip, timeline range | VSE users need quick strip operations. |
| **CLIP_EDITOR** | ✅ native tools | Sidebar | Track, Solve, Clean Up | Active clip, tracking data | Niche but valuable for VFX workflows. |
| **DOPESHEET_EDITOR** | ❌ | Sidebar | Add Keyframe, Edit Keys, Simplify | Active action, selected keyframes | Animation-focused templates. |
| **GRAPH_EDITOR** | ❌ | Sidebar | Add Keyframe, Smooth Curve, Simplify | Active F-curve, selected keyframes | Curve editing templates. |
| **NLA_EDITOR** | ❌ | Sidebar | Add Track, Push Down, Stitch | Active NLA tracks, strips | NLA-specific operations. |
| **OUTLINER** | ❌ | Sidebar | Select, Rename, Delete, Organize | Selected datablocks, display mode | Quick datablock operations. |
| **PROPERTIES** | ❌ | Sidebar | Explain Setting, Optimize, Apply | Active property tab, active object | "Explain this setting" is valuable here. |
| **FILE_BROWSER** | ✅ native tools | Sidebar | Open, Import, Link, Browse Assets | Current directory, selected file | File Browser supports BOTH left (TOOLS) and right (UI) bars natively. |
| **SPREADSHEET** | ✅ native tools | Sidebar | Explain Data, Filter, Export | Active data set, selected rows | Geometry data analysis. |
| **CONSOLE** | ❌ | Sidebar | Run Command, Explain Error, History | Last output, error text | Quick error lookup. |
| **ASSET_BROWSER** | ✅ native tools | Sidebar | Search Assets, Import, Preview | Active library, selected asset | Asset-first workflows (Tier 3d). |

**Note on left bars**: Editors with a native `RGN_TYPE_TOOLS` (VIEW_3D, IMAGE_EDITOR,
NODE_EDITOR, SEQUENCE_EDITOR, CLIP_EDITOR, SPREADSHEET, FILE_BROWSER) already have a
left toolbar. For these, our chat "macro" buttons could integrate into the existing
tools region (via a panel with `bl_region_type='TOOLS'`) rather than painting our own.

**Text Editor** has NO native tools region — so for the dedicated Coworker editor
we paint the iconized macro shelf as part of the GPU takeover canvas.

### Panel Registration Strategy

**Approach**: Dynamic registration via a mixin base class.

```python
# All editor types that support sidebar panels
_EDITOR_TYPES = [
    'VIEW_3D', 'TEXT_EDITOR', 'NODE_EDITOR', 'IMAGE_EDITOR',
    'SEQUENCE_EDITOR', 'CLIP_EDITOR', 'DOPESHEET_EDITOR',
    'GRAPH_EDITOR', 'NLA_EDITOR', 'OUTLINER', 'PROPERTIES',
    'FILE_BROWSER', 'SPREADSHEET', 'CONSOLE', 'ASSET_BROWSER',
]

def _make_chat_panel(space_type: str) -> type:
    """Factory: create a BFACW_PT_chat_panel subclass for *space_type*."""
    class BFACW_PT_chat_panel_universal(Panel):
        bl_label = "Coworker"
        bl_idname = f"BFACW_PT_chat_{space_type.lower()}"
        bl_space_type = space_type
        bl_region_type = 'UI'
        bl_category = "Coworker"
        bl_options = {'DEFAULT_CLOSED'}

        @classmethod
        def poll(cls, context):
            return not bpy.app.background

        def draw(self, context):
            _draw_chat_interface(
                self.layout, context,
                context.window_manager.bfacw_chat_props,
                agent_controller._agent_state,
                context.preferences.addons[__package__].preferences,
                editor_type=space_type,
            )
    return type(f"BFACW_PT_chat_{space_type.lower()}", (Panel,), dict(BFACW_PT_chat_panel_universal.__dict__))
```

### Floating Window / Popup Assessment

**Requirement**: A floating chat window that can be called on demand.

**User preference**: "Not too keen on popup, prefer it docked."

**Assessment**:

| Aspect | Docked (Sidebar) | Floating Window | Popup (Modal) |
|--------|:----------------:|:---------------:|:-------------:|
| Always visible | ✅ (if sidebar open) | ✅ (separate window) | ❌ (must summon) |
| Context-agnostic | ✅ (in every editor) | ✅ | ✅ |
| Screen real estate | ⚠️ (uses N-panel) | ⚠️ (separate window) | ❌ (covers content) |
| Multi-monitor | ❌ (same window) | ✅ (drag to second monitor) | ❌ |
| Workflow interruption | Low | Low | High (modal blocks) |
| Implementation effort | Low (panels exist) | Medium (wm.window_new) | High (modal operator) |
| Blender-native feel | ✅ | ✅ (native window) | ❌ (feels like a dialog) |

**Recommendation**: Implement the **floating window** (Phase 4) as a native Blender
window that can be docked to a second monitor or kept as a separate window. This
is the best compromise — it's always available, doesn't cover the viewport, and
feels native. The floating window button lives in the panel header for discoverability.

**Do NOT implement** a modal popup (like BlendAI's Ctrl+Shift+A). The user prefers
docked/always-visible over summon-on-demand. The floating window is the closest
we get to "docked but separate."

**Window title when duplicated to floating window** (verified from `wm_window.cc`):
- A temp-screen floating window with a single area gets the title from `ED_area_name(area)`
- Text Editor does NOT define `space_name_get` → title will be **"Text"**, not "Coworker"
- This is acceptable — the custom header inside the window says "Coworker"
- We cannot rename the OS window title from Python (C++ only)

---

## Pillar 2: Viewport Agent Feedback

### Vision

When the agent is working, the 3D Viewport provides real-time visual feedback:
what the agent is looking at, what it's modifying, and what it's about to do.

### 2.1 Agent Focus Highlight

When the agent is about to modify an object, it's highlighted with a pulsing glow:

```
Implementation:
- GPU overlay (POST_VIEW) drawing a wireframe outline around the target object
- Animated alpha (pulse via timer) — breathing effect
- Color-coded: blue = reading, orange = modifying, green = done, red = error
```

```python
def draw_agent_focus_highlight():
    """Draw a pulsing outline around objects the agent is working on."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    import time

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    gpu.state.line_width_set(3.0)

    # Pulse alpha based on time.
    alpha = 0.5 + 0.5 * abs(math.sin(time.monotonic() * 2.0))

    for obj_name in _agent_state.focused_objects:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue

        # Get world-space bounding box edges.
        bbox = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        edges = [...]  # 12 edges of the bounding box.

        # Color based on action type.
        if _agent_state.action_type == 'modify':
            color = (1.0, 0.6, 0.0, alpha)  # Orange
        elif _agent_state.action_type == 'read':
            color = (0.3, 0.5, 1.0, alpha)  # Blue
        else:
            color = (0.0, 1.0, 0.0, alpha)  # Green

        shader.bind()
        shader.uniform_float("color", color)
        batch = batch_for_shader(shader, 'LINES', {"pos": edges})
        batch.draw(shader)
```

### 2.2 Agent Planning Visualization

When the agent is "thinking," show a visual indicator of its plan:

- **Spatial markers**: Small dots or crosses where the agent plans to place objects
- **Path preview**: Dashed lines showing planned movements/animations
- **Text labels**: Floating text near objects showing what the agent intends to do

```
┌─ Planning Visualization ──────────────────────────────────────┐
│                                                                │
│   ·  ← "Create cube here"                                     │
│      \                                                        │
│       ·  ← "Add sphere"                                       │
│        \                                                      │
│         ·  ← "Add light"                                      │
│                                                                │
│   ─ ─ ─ →  (dashed line = planned animation path)             │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 2.3 Tool Execution Progress

When a tool is executing (especially long-running ones like renders):

- **Progress ring** around the cursor or in the viewport corner
- **Status text** overlay: "Rendering thumbnail... 45%"
- **Flash effect** on completion: brief green flash on modified objects

### 2.4 Agent "Breathing" Indicator

A subtle ambient indicator that the agent is alive and listening:

- **Corner glow**: Soft pulsing gradient in the bottom-right corner of the viewport
- **Status dot**: Small colored dot in the viewport corner
  - Green pulsing = idle, listening
  - Blue spinning = thinking
  - Orange solid = executing tool
  - Red = error

```python
def draw_agent_status_indicator():
    """Draw a small status indicator in the viewport corner."""
    import gpu
    from gpu_extras.presets import draw_circle_2d

    # Position: bottom-right corner, 20px margin.
    x = region.width - 30
    y = 30

    # Color based on state.
    if _agent_state.is_thinking:
        color = (0.3, 0.5, 1.0, 0.8)  # Blue
    elif _agent_state.error:
        color = (1.0, 0.2, 0.2, 0.8)  # Red
    elif _agent_state.mcp_server_running:
        color = (0.2, 1.0, 0.3, 0.8)  # Green
    else:
        color = (0.5, 0.5, 0.5, 0.5)  # Gray

    draw_circle_2d((x, y), color, 8.0)
```

### 2.5 Implementation Architecture

```python
class AgentViewportOverlay:
    """Manages all agent-related viewport overlays."""

    _handler: object | None = None
    _enabled: bool = False

    @classmethod
    def enable(cls):
        if cls._handler is not None:
            return
        cls._handler = bpy.types.SpaceView3D.draw_handler_add(
            cls._draw_overlay, (), 'WINDOW', 'POST_PIXEL'
        )
        cls._enabled = True

    @classmethod
    def disable(cls):
        if cls._handler is None:
            return
        bpy.types.SpaceView3D.draw_handler_remove(cls._handler, 'WINDOW')
        cls._handler = None
        cls._enabled = False

    @classmethod
    def _draw_overlay(cls):
        # Draw status indicator.
        cls._draw_status_indicator()
        # Draw tool progress.
        cls._draw_tool_progress()
        # Draw planning markers (if thinking).
        if _agent_state.is_thinking:
            cls._draw_planning_markers()

    @classmethod
    def _draw_status_indicator(cls):
        ...  # See 2.4

    @classmethod
    def _draw_tool_progress(cls):
        ...  # See 2.3

    @classmethod
    def _draw_planning_markers(cls):
        ...  # See 2.2
```

---

## Pillar 3: The Moodboard Editor

### Vision

A visual reference board inside Blender where users can:
- Drag and drop reference images
- Arrange and annotate them
- Use them as context for the AI agent (vision models)
- Generate new images from text prompts (local or API)
- Use images to guide 3D creation workflows

### 3.1 Moodboard as Image Editor Extension

**Approach**: Use Blender's Image Editor as the canvas, enhanced with GPU overlays
and a companion panel.

```
┌──────────────────────────────────────────────────────────────────┐
│  Moodboard Workspace                                              │
├──────────────────────────────────────────────────────────────────┤
│                            │                                      │
│   Image Editor              │   Moodboard Panel (sidebar)         │
│   ┌────────────────────┐   │   ┌─────────────────────────────┐   │
│   │                    │   │   │ Images (5)                   │   │
│   │  [ref1.jpg]        │   │   │ ┌─────┐ ┌─────┐ ┌─────┐   │   │
│   │                    │   │   │ │ img1│ │ img2│ │ img3│   │   │
│   │       [ref2.jpg]   │   │   │ └─────┘ └─────┘ └─────┘   │   │
│   │                    │   │   │ ┌─────┐ ┌─────┐            │   │
│   │  [ref3.jpg]        │   │   │ │ img4│ │ img5│  [+ Add]  │   │
│   │                    │   │   │ └─────┘ └─────┘            │   │
│   │           [ref4]   │   │   └─────────────────────────────┘   │
│   │                    │   │                                      │
│   └────────────────────┘   │   Selected: ref1.jpg                 │
│                            │   ┌─────────────────────────────┐   │
│                            │   │ [Use as Context]            │   │
│                            │   │ [Generate Similar]          │   │
│                            │   │ [Create 3D from Image]      │   │
│                            │   │ [Remove]                    │   │
│                            │   └─────────────────────────────┘   │
│                            │                                      │
│                            │   Generate:                          │
│                            │   ┌─────────────────────────────┐   │
│                            │   │ A cozy cabin in the woods    │   │
│                            │   └─────────────────────────────┘   │
│                            │   [Generate Image]                   │
│                            │                                      │
└────────────────────────────┴──────────────────────────────────────┘
```

### 3.2 Moodboard Data Model

```python
@dataclass
class MoodboardImage:
    """A single image on the moodboard."""
    name: str
    image: bpy.types.Image       # The Blender image data-block
    position: tuple[float, float] # Position on the board (0-1 UV)
    scale: float                  # Display scale
    notes: str                    # User annotations
    tags: list[str]               # Searchable tags
    source: str                   # 'file', 'generated', 'clipboard', 'url'

@dataclass
class Moodboard:
    """A collection of reference images."""
    name: str
    images: list[MoodboardImage]
    background_color: tuple[float, float, float, float]
    grid_size: tuple[int, int]    # Grid for auto-arrange
```

### 3.3 Moodboard Features

#### Drag & Drop Import
- Accept images dragged from file browser
- Paste from clipboard
- Load from URL
- Import from Poly Haven / Sketchfab thumbnails (Tier 1-2 integrations)

#### Image Arrangement
- Auto-grid layout
- Free-form placement (drag to reposition)
- Scale/rotate individual images
- Bring to front / send to back

#### Annotation
- Draw on images (using Blender's annotation system)
- Text notes per image
- Color-coded tags
- Link images together (visual connections)

#### AI Context Integration
- Select images to include as context for the LLM
- Vision models (Gemma 3 Vision, GPT-4V, Claude) can "see" the moodboard
- Agent can reference images: "Make the material look like ref1.jpg"
- Image-to-3D workflows: "Create a 3D model based on this reference"

### 3.4 Local Image Generation

#### Can Local Models Run Image Generation?

**Yes, but with significant caveats:**

| Model | Size | RAM Needed | Speed (CPU) | Speed (GPU) | Quality |
|---|---|---|---|---|---|
| Stable Diffusion 1.5 | ~4 GB | 4-8 GB | ~2 min/img | ~5 sec/img | Good |
| SDXL | ~7 GB | 8-16 GB | ~5 min/img | ~15 sec/img | Very Good |
| SDXL Turbo | ~7 GB | 8-16 GB | ~1 min/img | ~2 sec/img | Good (fast) |
| FLUX.1-schnell | ~12 GB | 16-24 GB | ~10 min/img | ~30 sec/img | Excellent |
| FLUX.1-dev | ~12 GB | 16-24 GB | ~10 min/img | ~30 sec/img | Excellent |

**Recommendation**: For a self-contained addon, **SDXL Turbo** or **Stable Diffusion 1.5**
are the most practical. They run on consumer GPUs and produce good results quickly.

#### Integration Approach

```python
def generate_image_local(
    prompt: str,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 512,
    steps: int = 4,  # SDXL Turbo only needs 1-4 steps!
    seed: int = -1,
) -> bpy.types.Image | None:
    """Generate an image using a local Stable Diffusion model.

    Requires ``diffusers`` and ``torch`` to be installed in the vendor deps.
    Downloads the model on first use (~7 GB for SDXL Turbo).
    """
    import torch
    from diffusers import AutoPipelineForText2Image

    pipe = AutoPipelineForText2Image.from_pretrained(
        "stabilityai/sdxl-turbo",
        torch_dtype=torch.float16,
        variant="fp16",
    )
    pipe.to("cuda" if torch.cuda.is_available() else "cpu")

    if seed >= 0:
        generator = torch.Generator().manual_seed(seed)
    else:
        generator = None

    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=0.0,  # SDXL Turbo uses 0.0
        generator=generator,
    ).images[0]

    # Convert PIL Image to Blender Image.
    import numpy as np
    img_array = np.array(result)
    img_array = np.flipud(img_array)  # Blender uses bottom-left origin.
    img_array = img_array.astype(np.float32) / 255.0

    bpy_image = bpy.data.images.new(
        name="generated_" + prompt[:20],
        width=width,
        height=height,
        alpha=False,
    )
    bpy_image.pixels = img_array.ravel()
    bpy_image.pack()

    return bpy_image
```

#### Alternative: API-Based Generation

For users who don't want to download 7 GB models:

```python
def generate_image_remote(
    prompt: str,
    provider: str = "openrouter",
    api_key: str = "",
) -> bpy.types.Image | None:
    """Generate an image using a remote API (OpenRouter, Stability AI, etc.)."""
    # Use existing remote API infrastructure from llm_manager.py.
    ...
```

### 3.5 Agentic Moodboard Workflows

The moodboard isn't just a passive reference board — it's an active agent tool:

#### Workflow 1: Reference → 3D
```
1. User adds reference images to moodboard
2. User selects images and clicks "Create 3D from References"
3. Agent (with vision) analyzes the images
4. Agent creates matching 3D scene:
   - Blocks out geometry matching the reference
   - Sets up lighting matching the reference mood
   - Creates materials matching the reference colors/textures
   - Positions camera to match the reference composition
```

#### Workflow 2: Moodboard → Variations
```
1. User has a moodboard with a design direction
2. User clicks "Generate Variations"
3. Agent uses the moodboard as image-to-image input
4. Local or remote image generation creates variations
5. New images are added to the moodboard
6. User selects favorites and iterates
```

#### Workflow 3: Text → Moodboard → 3D
```
1. User types: "A steampunk airship interior, warm lighting, brass and wood"
2. Agent generates 4 reference images (local or API)
3. Images appear on moodboard
4. User selects the best one
5. Agent creates 3D scene based on the selected reference
```

#### Workflow 4: Scene Analysis → Moodboard
```
1. User has an existing 3D scene
2. User clicks "Analyze Scene Style"
3. Agent renders viewport thumbnails from multiple angles
4. Agent extracts color palette, material references, lighting mood
5. Results appear on moodboard as a "style guide"
6. User can apply this style to other scenes
```

---

## Implementation Plan — 8 Phases

> **Status update (2026-09-01):** Mapping to master plan §15 pathways. The
> Moodboard (Pillar 3) is **moved to Tier 5** (master plan §8). The remaining
> phases map to the master plan's Phase 4 (Agent Dedicated Central Editor +
> Viewport) and Phase 1 (Foundation):
>
> | 4a Phase | Master Plan Pathway | Notes |
> |----------|---------------------|-------|
> | Theme utilities | Phase 1.1 (with `ui_components.py`) | Shared theming for all editors |
> | 1 — Core chat component refactor | Phase 1.1/1.3 | `ui_components.py` extraction |
> | 2 — Register panels in all editors | Phase 4.1 | Coworker workspace |
> | 3 — Dedicated Coworker editor | Phase 4.1 | USERPREF-pattern center panels |
> | 3b — GPU center enhancement | Deferred | Optional, not in Tier 4 scope |
> | 4 — Floating chat window | Tier 5 | Deferred with popup/quick-chat |
> | 5 — Per-editor context enrichment | Phase 0.4 | Rides domain tooling |
> | 6 — Template/prompt system | Phase 3.5 | Text Editor prompt templates |
> | 7 — Feature parity & polish | Phase 2.10 | Per-message actions |
> | 8 — Viewport agent feedback | Phase 4.2–4.4 | Status overlay + focus + CHOYA |

### Phase 1: Core Chat Component Refactor (~400 LOC, 2 files)

**Goal**: Extract the chat UI into a reusable component that can be embedded in any panel or window.

**Steps**:
1. Extract `_draw_chat_interface()` from `BFACW_PT_chat_panel.draw()` into a standalone function
   - Signature: `_draw_chat_interface(layout, context, props, state, prefs, editor_type="VIEW_3D")`
   - All current chat UI (input, send/stop, history, mode toggle, status) goes into this function
   - `editor_type` controls which template buttons to show

2. Create `_draw_editor_templates()` — per-editor quick-action buttons:
   - `VIEW_3D`: "Add Object", "Add Material", "Add Modifier", "Sculpt", "Animate"
   - `TEXT_EDITOR`: "Run Script", "Explain Code", "Fix Errors"
   - `NODE_EDITOR`: "Add Node", "Connect Nodes", "Explain Node Tree"
   - `IMAGE_EDITOR`: "Edit Image", "Add Reference"
   - `SEQUENCE_EDITOR`: "Add Strip", "Edit Strip"
   - `SHADER_EDITOR`: "Add Material", "Setup PBR"
   - `COMPOSITOR`: "Add Node", "Setup Render"
   - `GEOMETRY_NODES`: "Add Modifier", "Explain Setup"

3. Simplify `BFACW_PT_chat_panel.draw()` to call `_draw_chat_interface()`

**Files**: `ui_chat.py`, `shared.py`

---

### Phase 2: Register Chat Panel in All Editor Types (~150 LOC, 2 files)

**Goal**: Chat sidebar available in EVERY editor type, not just VIEW_3D and TEXT_EDITOR.

**Steps**:
1. Create a mixin/base panel class and register subclasses for each editor type
2. Register panels for: `VIEW_3D`, `TEXT_EDITOR`, `NODE_EDITOR`, `IMAGE_EDITOR`, `SEQUENCE_EDITOR`, `CLIP_EDITOR`, `DOPESHEET_EDITOR`, `GRAPH_EDITOR`, `NLA_EDITOR`, `OUTLINER`, `PROPERTIES`, `FILE_BROWSER`, `SPREADSHEET`, `CONSOLE`, `ASSET_BROWSER`
3. Each panel calls `_draw_chat_interface()` with its `editor_type`
4. Keep existing panels for backward compatibility (they delegate to the new system)

**Files**: `ui_chat.py`, `__init__.py`

---

### Phase 3: Dedicated Coworker Editor — Clone the Preferences Editor (~250 LOC, 4 files)

**Goal**: Register the Coworker Chat as a first-class editor option. Users manually add a **Preferences editor** area to any workspace — the addon does NOT create workspaces. This is **Mode 1** (dedicated editor).

**The key insight**: The Preferences editor (`USERPREF` space) draws its center
(WINDOW region) with `ED_region_panels_draw` — so **Python panels render in the
center**. We "clone" this pattern for the Coworker editor.

**The blank slate**: The `USERPREF` space starts with:
- **Nav region (UI)** on the **left** (`RGN_ALIGN_LEFT`) — F5 flips it to the right
- **Center (WINDOW)** — Python panels, empty until we register ours
- **Exec region (EXECUTE)** at the bottom
- **Header** at the bottom (we register our own custom header)

So a raw `USERPREF` area is already a blank, Python-drawn canvas. We fill it with
Coworker panels.

**Steps**:
1. **Register custom Header** `BFACW_HT_coworker_header` for `USERPREF` space
   - Shows: Coworker branding, connection status (🟢/🔴), quick actions (Start/Stop, Floating)
   - Inherits theme automatically via `bpy.types.Header` subclass

2. **Center panels** (native, `bl_space_type='USERPREF'`, `bl_region_type='WINDOW'`):
   - `BFACW_PT_chat_center` — the chat panel with `bl_context='chat'`
   - Uses `layout.box()`, `layout.panel()`, `layout.prop()` — 100% native theme
   - Native text input (textbox), native scrolling, native panel headers

3. **Left nav panels** (native, `bl_region_type='UI'`):
   - `BFACW_PT_chat_nav_chat` — Chat tab (`bl_context='chat'`)
   - `BFACW_PT_chat_nav_queue` — Queue tab (`bl_context='queue'`)
   - `BFACW_PT_chat_nav_status` — Status tab (`bl_context='status'`)
   - `BFACW_PT_chat_nav_rules` — Rules tab (`bl_context='rules'`)
   - `BFACW_PT_chat_nav_log` — Log tab (`bl_context='log'`)
   - `BFACW_PT_chat_nav_macros` — Macros tab (`bl_context='macros'`)
   - This IS the Bforartists-style iconized toolshelf — native, not GPU-painted
   - Nav starts on the **left**; users can press **F5** to flip it to the right

4. **Create `BFACW_OT_setup_coworker_editor`** operator — one-click configures a Preferences editor for chat (shows panels, sets active context, optionally flips nav to right)

5. **Add "Add Coworker Editor" button** to the panel header

**Theming**: 100% native — every widget inherits the active theme automatically.
No GPU drawing, no `theme_utils.py` needed for the main canvas.

**Files**: `ui_chat.py`, `__init__.py`, `shared.py`

---

### Phase 3b: GPU Center Enhancement (Optional, Deferred)

**Only if user testing shows the panel-based center is too cramped** — and only
with `theme_utils.py` mandatory for every color. This is the fallback if the
USERPREF-clone approach doesn't feel immersive enough.

- `CoworkerEditorOverlay` GPU-painted center — chat bubbles in the WINDOW region
- Modal operator for input capture
- **`theme_utils.py` is MANDATORY** — every color reads from the active theme:
  - `get_space_color("text_editor", "back")` → center background
  - `get_ui_color("wcol_box", "inner")` → chat bubble background
  - `get_ui_color("wcol_box", "inner_sel")` → user bubble (highlight)
  - `get_ui_color("wcol_regular", "text")` → default text
  - `get_ui_color("wcol_regular", "text_sel")` → selected text
  - `get_ui_color("wcol_text", "inner")` → input background
  - `get_status_color("success"|"idle"|"thinking"|"error")` → status dot
- The GPU canvas matches the native panels' theme exactly (same source colors)
- Sidebar panels (queue/status/diagnostics) stay native regardless

**Files**: `coworker_editor.py` (new — overlay + modal)

---

### Phase 4: Floating Chat Window (~250 LOC, 2 files)

**Goal**: A floating window callable from any workspace, context-agnostic.

**Steps**:
1. **Create `BFACW_OT_open_floating_chat`** operator
   - Uses `bpy.ops.wm.window_new()` to create a new window
   - Window is smaller (e.g., 400×600), positioned at cursor or screen center
   - Minimal UI (no toolbars, no headers)
2. **Create minimal "Coworker Chat" workspace** for the floating window
   - Single area: Preferences editor with the Coworker panels visible
   - No 3D viewport — pure chat window
3. **Add "Floating Window" button** to the chat panel header
4. **Track floating windows** — prevent duplicates, detect when closed

**Window title note**: The floating window title will be **"Userpref"** (from
`ED_area_name` for the `USERPREF` space), not "Coworker". This is acceptable —
the custom header inside says "Coworker". Cannot be renamed from Python.

**Files**: `operators_agent.py`, `ui_chat.py`

---

### Phase 5: Per-Editor Context Enrichment (~200 LOC, 3 files)

**Goal**: The agent knows which editor the user is in and tailors responses accordingly.

**Steps**:
1. Add `current_editor_type` and `current_editor_name` to `AgentState`
2. Update `_get_system_prompt_with_rules()` to include: "The user is currently in the {editor_name}."
3. Pass editor context when sending messages via `_draw_chat_interface()`
4. Add lightweight editor-specific context enrichment:
   - `VIEW_3D`: Active object, mode, viewport shading
   - `NODE_EDITOR`: Active node tree, selected nodes
   - `TEXT_EDITOR`: Active text datablock
   - `IMAGE_EDITOR`: Active image
   - `SEQUENCE_EDITOR`: Active strip

**Files**: `agent_controller.py`, `ui_chat.py`, `shared.py`

---

### Phase 6: Template/Prompt System (~300 LOC, 3 files)

**Goal**: Per-editor template buttons like Blender Buddy's toggles, but more extensive. Also powers the macro toolshelf icons in the dedicated editor.

**Steps**:
1. Design `EditorTemplate` dataclass in `shared.py` (id, label, icon, prompt, mode, category)
2. Define curated per-editor template sets
3. Draw compact template button row above the input area via `_draw_editor_templates()`
4. Add `BFACW_OT_edit_templates` operator — file-based customization
   - Templates stored in `SCRIPTS/bfa_coworker_templates/`
   - Users can edit template text files
5. **Macro toolshelf** (Mode 1 dedicated editor): the same template data drives the
   GPU-painted iconized left shelf — icons map to `EditorTemplate.icon`, click triggers
   the template prompt. One data source, two renderers (GPU icons + native buttons).

**Files**: `shared.py`, `ui_chat.py`, `operators_agent.py`

---

### Phase 7: Feature Parity & Polish (~200 LOC, 3 files)

**Goal**: Ensure 1:1 feature parity with the current sidebar chat across all editor types.

**Steps**:
1. Audit all current features work in all editors:
   - ✅ Agent control, status, mode toggle, input, @mention, send/clear/stop/queue
   - ✅ Conversation history, turn grouping, reasoning display, tool call display
   - ✅ Copy message, newest first toggle, queue panel, status panel, project rules, session log
   - ❌ Template buttons (new in Phase 6)
   - ❌ Floating window button (new in Phase 4)
2. Add preferences for new features:
   - `show_editor_templates`, `show_workspace_button`
   - `floating_window_width`, `floating_window_height`

**Files**: `preferences.py`, `ui_chat.py`, `shared.py`

---

### Phase 8: Viewport Agent Feedback (~350 LOC, 2 files)

**Goal**: Real-time visual overlays in the 3D Viewport showing agent activity.

**Steps**:
1. Create `viewport_overlay.py` — `AgentViewportOverlay` class
   - Status indicator (corner dot with pulse animation)
   - Agent focus highlight (pulsing wireframe on target objects)
   - Tool execution progress ring
   - Planning markers (spatial dots for planned placements)
2. Integrate with agent state — update overlay on tool calls
3. All GPU-drawn colors read from theme via `theme_utils.py`

**Files**: `viewport_overlay.py` (new), `agent_controller.py`

---

## Theming Architecture — Blender 5.3 Theme Integration

**Goal**: Every visual element uses the user's active Blender theme. No hardcoded colors.

### How Blender 5.3 Themes Work

```
bpy.context.preferences.themes['Default']
├── view_3d          # 3D Viewport colors (back, text, header, grid, wire, ...)
├── text_editor      # Text Editor colors (back, text, text_hi, line_numbers, ...)
├── node_editor      # Node Editor colors (back, text, grid, wire, node_backdrop, ...)
├── ui               # Global UI colors
│   ├── wcol_regular     # Regular widget (outline, inner, inner_sel, text, text_sel, item)
│   ├── wcol_tool        # Tool button colors
│   ├── wcol_box         # Box widget colors
│   ├── wcol_menu        # Menu colors
│   ├── wcol_state       # State indicator colors
│   ├── wcol_tab         # Tab colors
│   ├── wcol_scroll      # Scrollbar colors
│   ├── wcol_list_item   # List item colors
│   ├── wcol_text        # Text input colors
│   ├── panel_header     # Panel header background
│   ├── menu_back        # Menu background
│   ├── menu_text        # Menu text
│   ├── editor_border    # Editor border
│   └── ...
└── ...
```

### Theme-Aware Color Access — `theme_utils.py` (~100 LOC)

```python
# theme_utils.py — Theme-aware color access for GPU drawing and custom UI

import bpy
from typing import Any


def _theme() -> Any:
    """Return the active Blender theme, or None if unavailable."""
    try:
        return bpy.context.preferences.themes[0]
    except Exception:
        return None


def get_ui_color(widget: str = "wcol_regular", attr: str = "inner") -> tuple:
    """Read a UI widget color from the active theme.
    widget: wcol_regular, wcol_box, wcol_tool, wcol_menu, etc.
    attr: inner, inner_sel, text, text_sel, outline, item
    Returns RGBA tuple (0.0–1.0), or safe fallback.
    """
    theme = _theme()
    if theme is None:
        return _fallback_color(widget, attr)
    try:
        wcol = getattr(theme.ui, widget)
        return getattr(wcol, attr)
    except AttributeError:
        return _fallback_color(widget, attr)


def get_space_color(space: str = "view_3d", attr: str = "back") -> tuple:
    """Read a space-specific color from the active theme.
    space: view_3d, text_editor, node_editor, etc.
    attr: back, text, text_hi, header, grid, wire, etc.
    """
    theme = _theme()
    if theme is None:
        return (0.1, 0.1, 0.12, 1.0)
    try:
        space_theme = getattr(theme, space)
        return getattr(space_theme, attr)
    except AttributeError:
        return (0.1, 0.1, 0.12, 1.0)


def _fallback_color(widget: str, attr: str) -> tuple:
    fallbacks = {
        ("wcol_regular", "inner"):      (0.25, 0.25, 0.27, 1.0),
        ("wcol_regular", "inner_sel"):  (0.35, 0.55, 0.85, 1.0),
        ("wcol_regular", "text"):       (0.8,  0.8,  0.83, 1.0),
        ("wcol_regular", "text_sel"):   (1.0,  1.0,  1.0,  1.0),
        ("wcol_regular", "outline"):    (0.08, 0.08, 0.10, 1.0),
        ("wcol_box", "inner"):          (0.18, 0.18, 0.20, 1.0),
        ("wcol_box", "inner_sel"):      (0.28, 0.28, 0.30, 1.0),
    }
    return fallbacks.get((widget, attr), (0.2, 0.2, 0.22, 1.0))


def get_status_color(state: str = "idle") -> tuple:
    """Semantic status color: idle, thinking, success, error, warning."""
    theme = _theme()
    if theme is None:
        return {
            "idle":     (0.5, 0.5, 0.5, 0.8),
            "thinking": (0.3, 0.5, 1.0, 0.8),
            "success":  (0.2, 1.0, 0.3, 0.8),
            "error":    (1.0, 0.2, 0.2, 0.8),
            "warning":  (1.0, 0.6, 0.0, 0.8),
        }.get(state, (0.5, 0.5, 0.5, 0.8))
    try:
        wcol = theme.ui.wcol_state
        base = getattr(wcol, "inner" if state == "idle" else "inner_sel")
        if state == "success":
            return (base[0] * 0.5, base[1] + 0.3, base[2] * 0.5, base[3])
        if state == "error":
            return (base[0] + 0.3, base[1] * 0.5, base[2] * 0.5, base[3])
        if state == "warning":
            return (base[0] + 0.3, base[1] + 0.1, base[2] * 0.3, base[3])
        return base
    except AttributeError:
        return (0.5, 0.5, 0.5, 0.8)
```

### How Each Component Uses Theme Colors

| Component | Theme Source | What It Reads |
|-----------|-------------|---------------|
| **Mode 1 center panels** (USERPREF WINDOW region) | Automatic — Blender's UI system | Inherits `theme.ui.wcol_*` for all widgets. No custom code. |
| **Mode 1 left nav panels** (USERPREF UI region) | Automatic — Blender's UI system | Inherits `theme.ui.wcol_*`. No custom code. |
| **Mode 2 contextual panels** (all editors UI region) | Automatic — Blender's UI system | Inherits `theme.ui.wcol_*`. No custom code. |
| **Custom Header** (`BFACW_HT_coworker_header`) | Automatic — `bpy.types.Header` subclass | Inherits `theme.ui.header` colors. No custom code. |
| **GPU center canvas** (Phase 3b, deferred) | Manual — `theme_utils.get_space_color()` + `get_ui_color()` | Reads `theme.text_editor.back` for background, `theme.ui.wcol_box.inner` for bubbles, `theme.ui.wcol_regular.text` for text. Matches native panels exactly. |
| **Viewport overlays** (Phase 8) | Manual — `theme_utils.get_space_color()` | Reads `theme.view_3d.back`, `theme.view_3d.wire`, `get_status_color()`. |
| **Floating window** (Phase 4) | Automatic — native Blender window | Inherits all theme colors. No custom code. |

### Theming Consistency Guarantee

The "clone the Preferences editor" principle ensures the Coworker editor is
**indistinguishable** from other editors in the same theme:

1. **Center panels** (Mode 1) use `layout.box()`, `layout.panel()`, `layout.prop()` — identical to the real Preferences editor panels
2. **Left nav** (Mode 1) is native UI-region panels — identical to the Preferences nav tabs
3. **Header** is a `bpy.types.Header` subclass — identical to native headers
4. **GPU canvas** (Phase 3b, deferred) reads the SAME theme sources the native widgets use — so dark/light/custom themes all render identically
5. **No hardcoded colors anywhere** — verified by code review checklist

### Why This Matters

1. **Dark theme users**: Overlays use dark background colors, not hardcoded brights
2. **Light theme users**: Overlays use light background colors, not hardcoded darks
3. **Custom theme users**: Every color adapts to their palette (Bforartists, Monokai, etc.)
4. **Future-proof**: When Blender 5.4 adds new theme properties, just add a fallback
5. **No GPU drawing needed for the main canvas** — the USERPREF-clone approach is fully native

---

## Summary of All Changes

> **Revised (2026-09-01)**: Moodboard (Pillar 3) moved to Tier 5 — see
> `plan_tier5_moodboard_storyboarding.md` (milestone M1). Floating chat window
> (Phase 4) deferred to Tier 5 (popup/quick-chat). Phase 3b GPU enhancement
> remains optional/deferred. The table below reflects the Tier 4 in-scope
> subset.

| Phase | What | Files Changed | Files New | LOC | Status |
|-------|------|:-------------:|:---------:|:---:|--------|
| — | Theme utilities (`theme_utils.py`) | 0 | 1 | ~100 | In scope (Phase 1.1) |
| 1 | Core chat component refactor | 2 | 0 | ~400 | In scope (Phase 1.1/1.3) |
| 2 | Register panels in all editors (Mode 2) | 2 | 0 | ~150 | In scope (Phase 4.1) |
| 3 | Dedicated Coworker editor — clone USERPREF (Mode 1) | 3 | 0 | ~250 | In scope (Phase 4.1) |
| 3b | GPU center enhancement (optional, deferred) | 1 | 1 | ~300 | Deferred |
| 4 | Floating chat window | 2 | 0 | ~250 | → Tier 5 |
| 5 | Per-editor context enrichment | 3 | 0 | ~200 | In scope (Phase 0.4) |
| 6 | Template/prompt system | 3 | 0 | ~300 | In scope (Phase 3.5) |
| 7 | Feature parity & polish | 3 | 0 | ~200 | In scope (Phase 2.10) |
| 8 | Viewport agent feedback | 1 | 1 | ~350 | In scope (Phase 4.2–4.4) |
| ~~3–6~~ | ~~Moodboard (Pillar 3)~~ | — | — | ~~~520~~ | **→ Tier 5** (milestone M1) |

## Dependencies

```
Phase 1 → Phase 2 → Phase 5
        ↘ Phase 3 (parallel with 2)
        ↘ Phase 4 (parallel with 2)
        ↘ Phase 6 (parallel with 2-5)
              ↘ Phase 7 (depends on 1-6)
Phase 8 (independent, parallel with 1-7)
```

## Decisions

| Decision | Rationale |
|----------|-----------|
| **Ship BOTH modes** | Mode 1 (dedicated editor) for immersive workspace, Mode 2 (contextual sidebar) for everywhere context. They share the same core component. |
| **Clone the Preferences editor for Mode 1** | The `USERPREF` space draws its center (WINDOW) with `ED_region_panels_draw` — Python panels render there. This is THE way to get Python-drawn chat in the center with native theming. |
| **Native theming is the #1 principle** | The user wants it to feel "built in." All panels use native Blender widgets. GPU drawing is deferred/decorative-only. |
| **Single chat component, multiple renderers** | One `_draw_chat_interface()` drives native panels. Feature parity guaranteed. |
| **USERPREF center panels + left nav** | Center (WINDOW) = chat panel. Left (UI) = nav tabs (Chat, Queue, Status, Rules, Log, Macros). Both native Python panels. |
| **GPU center canvas deferred (Phase 3b)** | Only if user testing demands it. Uses `theme_utils.py` colors that match native panels exactly. |
| **Per-editor templates, not per-editor panels** | Templates are lightweight data (just prompt strings). One data source drives native buttons. |
| **No workspace registration** | Bforartists users build their own layouts. Addon registers panels in the USERPREF space. |
| **USERPREF as the chat canvas space** | Only editor where Python panels draw in the center. The "clone the Preferences editor" approach. |
| **Floating window via `wm.window_new()`** | Blender's native window system, most reliable. Title will be "Userpref" — acceptable, header says "Coworker". |
| **Viewport overlays optional** | Phase 8 GPU overlays are the ONLY exception to native-first — they're transient feedback, toggleable in preferences. |
| **Template system file-based** | Users customize by editing text files. |
| **Theme-aware via `theme_utils.py`** | Only GPU-drawn elements (Phase 3b, Phase 8) read from `bpy.context.preferences.themes[0]`. No hardcoded colors anywhere. |
| **Native UI inherits theme automatically** | Panels, headers, text editors use Blender's built-in theming. Only GPU overlays need manual theme access. |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| USERPREF space conflicts with real Preferences | Medium | Medium | Panels use unique `bl_idname` prefixes (`BFACW_PT_`), filter by our custom header/poll |
| Users confuse the Coworker USERPREF with real Preferences | Low | Medium | Custom header + branding makes it distinct |
| GPU overlay performance impact (Phase 3b/8) | Medium | Medium | Throttle redraws, simple shaders, disable when not visible |
| Blender version incompatibility | Low | High | Version-guard any GPU code, test on 5.0-5.2 |
| Panel registration in 15+ editors (Mode 2) | Low | Medium | Use mixin pattern, test each editor type |
| Modal operator conflicts (Phase 3b, deferred) | Medium | Medium | Only active when takeover is on; Esc to exit; pass-through for middle-mouse |

## Further Considerations

1. **Hotkey for floating window**: Register default hotkey (Ctrl+Shift+C) like Blender Buddy?
2. **Template scope**: Per-editor only, or also per-mode (Edit Mode vs Object Mode)?
3. **Moodboard Editor**: Deferred to Tier 5 — not in scope for this plan.
4. **USERPREF space dual-use**: The real Preferences editor and the Coworker editor both use `USERPREF`. Need a way to distinguish them — the Coworker panels' `poll()` can check for a flag set by our setup operator.
5. **Multi-monitor**: Floating window can be placed on a second monitor natively.
6. **Left toolshelf for editors with native TOOLS region**: For VIEW_3D, NODE_EDITOR, etc., the macro buttons could integrate into the native tools region (`bl_region_type='TOOLS'`) — a native panel, fully theme-consistent.
7. **Bforartists iconized toolshelf reference**: The native implementation lives at `C:\3D_Stuff\Bforartists_sync\source\blender\editors\space_view3d\space_view3d.cc` (`view3d_tools_region_init/draw`, `UI_TOOLBAR_WIDTH_DOUBLE`). We mimic the *style* with native `layout.operator(icon=...)` buttons wherever possible, not GPU drawing.

---

## Appendix: GPU Takeover Architecture (Phase 3b — Optional Enhancement)

This appendix documents the full GPU takeover approach for `SpaceTextEditor`.
This is **Phase 3b** of the fused design — an optional enhancement to the dedicated
Coworker editor, gated on user testing. The native-first approach (Phase 3a) ships
first; the GPU center canvas is only added if the sidebar-only layout proves too
cramped. When implemented, **every color reads from `theme_utils.py`** so the GPU
canvas matches the native panels exactly.

### CoworkerEditorOverlay Class

```python
class CoworkerEditorOverlay:
    """GPU-powered takeover of SpaceTextEditor for dedicated chat UI.

    This is the core pattern for making the Coworker editor feel native.
    Instead of a sidebar panel, we paint the ENTIRE Text Editor area with
    our own GPU-drawn UI, capturing all keyboard/mouse input via a modal
    operator.
    """

    _draw_handler: object | None = None
    _modal_op: object | None = None
    _active: bool = False

    # ── State ──
    _chat_messages: list[dict] = []       # Message history
    _input_text: str = ""                 # Current input line
    _cursor_pos: int = 0                  # Cursor in input
    _scroll_offset: int = 0               # Chat scroll position
    _selected_tab: str = "chat"           # chat / prompts / settings
    _hovered_element: str | None = None   # For click detection

    @classmethod
    def activate(cls, context: bpy.types.Context) -> None:
        """Take over the current Text Editor area."""
        if cls._active:
            return

        area = context.area
        if area.type != 'TEXT_EDITOR':
            return

        space = area.spaces[0]

        # 1. Hide all native regions.
        space.show_region_header = False
        space.show_region_ui = False       # Sidebar
        space.show_region_footer = False
        space.show_line_numbers = False
        space.show_syntax_highlight = False
        space.show_word_wrap = True
        space.show_margin = False

        # 2. Register our custom draw handler.
        cls._draw_handler = bpy.types.SpaceTextEditor.draw_handler_add(
            cls._draw_editor, (context,), 'WINDOW', 'POST_PIXEL'
        )

        # 3. Launch modal operator for input capture.
        bpy.ops.bfacw.coworker_editor_modal('INVOKE_DEFAULT')

        cls._active = True

    @classmethod
    def deactivate(cls) -> None:
        """Restore native Text Editor."""
        if cls._draw_handler is not None:
            bpy.types.SpaceTextEditor.draw_handler_remove(
                cls._draw_handler, 'WINDOW')
            cls._draw_handler = None
        cls._active = False

    @classmethod
    def _draw_editor(cls, context: bpy.types.Context) -> None:
        """Main draw callback — called every frame."""
        import gpu
        import blf
        from gpu_extras.presets import draw_texture_2d

        region = context.region
        width = region.width
        height = region.height

        # ── Background (theme-aware) ──
        from .theme_utils import get_space_color, get_ui_color, get_status_color
        bg_color = get_space_color("text_editor", "back")
        _draw_rect((0, 0), (width, height), bg_color)

        # ── Tab bar ──
        tab_bar_h = 36
        header_bg = get_ui_color("wcol_regular", "inner")
        _draw_rect((0, height - tab_bar_h), (width, tab_bar_h), header_bg)
        tabs = [("chat", "Chat"), ("prompts", "Prompts"), ("settings", "Settings")]
        tab_w = width // len(tabs)
        for i, (tab_id, tab_label) in enumerate(tabs):
            x = i * tab_w
            is_active = (cls._selected_tab == tab_id)
            if is_active:
                active_bg = get_ui_color("wcol_regular", "inner_sel")
                _draw_rect((x, height - tab_bar_h), (tab_w, tab_bar_h), active_bg)
            text_color = get_ui_color("wcol_regular", "text_sel" if is_active else "text")
            _draw_text(tab_label, x + tab_w // 2, height - tab_bar_h // 2,
                       size=14, align='CENTER', valign='CENTER', color=text_color)

        # ── Status indicator (right side of tab bar) ──
        status_color = get_status_color("success" if _agent_state.mcp_server_running else "idle")
        from gpu_extras.presets import draw_circle_2d
        draw_circle_2d((width - 60, height - 18), status_color, 6.0)

        # ── Content area ──
        content_y_start = 60
        content_h = height - tab_bar_h - content_y_start

        if cls._selected_tab == "chat":
            cls._draw_chat_messages(0, content_y_start, width, content_h)
        elif cls._selected_tab == "prompts":
            cls._draw_prompts_tab(0, content_y_start, width, content_h)
        elif cls._selected_tab == "settings":
            cls._draw_settings_tab(0, content_y_start, width, content_h)

        # ── Input area ──
        input_h = 50
        input_bg = get_ui_color("wcol_text", "inner")
        input_outline = get_ui_color("wcol_text", "outline")
        _draw_rect((10, 5), (width - 20, input_h), input_bg)
        _draw_rect((10, 5), (width - 20, input_h), input_outline, is_outline=True)
        text_color = get_ui_color("wcol_text", "text")
        _draw_text("> " + cls._input_text, 20, 20, size=16, color=text_color)
        # Blinking cursor.
        import time
        if int(time.monotonic() * 2) % 2 == 0:
            cursor_color = get_ui_color("wcol_text", "text")
            cursor_x = 20 + cls._cursor_pos * 9 + 15
            _draw_rect((cursor_x, 15), (2, 20), cursor_color)

        # ── Action buttons ──
        btn_y = 5
        btn_w = 60
        btn_h = 40
        send_color = get_ui_color("wcol_tool", "inner_sel")
        clear_color = get_ui_color("wcol_tool", "inner")
        stop_color = get_ui_color("wcol_state", "inner")
        _draw_button("Send", width - 190, btn_y, btn_w, btn_h, send_color)
        _draw_button("Clear", width - 120, btn_y, btn_w, btn_h, clear_color)
        _draw_button("Stop", width - 50, btn_y, btn_w, btn_h, stop_color)

    @classmethod
    def _draw_chat_messages(cls, x, y, w, h) -> None:
        """Draw the chat message history with bubble styling."""
        # Clip to content area.
        # For each message in _chat_messages (scrolled by _scroll_offset):
        #   - User messages: right-aligned, blue-ish bubble
        #   - Agent messages: left-aligned, dark bubble, with code blocks
        #   - Tool messages: compact, gray, indented
        ...
```

### Custom Header Registration

```python
class BFACW_HT_coworker_header(bpy.types.Header):
    """Custom header for the Coworker Text Editor takeover."""
    bl_idname = "BFACW_HT_coworker_header"
    bl_space_type = 'TEXT_EDITOR'
    bl_label = "Coworker"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        # Only show our header when the Coworker takeover is active.
        return CoworkerEditorOverlay._active

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.label(text="Coworker", icon='CONSOLE')

        # Agent status.
        state = agent_controller._agent_state
        if state.mcp_server_running:
            layout.label(text="🟢 Connected", icon='CHECKMARK')
        else:
            layout.label(text="🔴 Offline", icon='X')

        # Quick actions.
        layout.operator("bfacw.agent_start", text="", icon='PLAY')
        layout.operator("bfacw.agent_stop", text="", icon='CANCEL')
```

### Modal Operator for Input Capture

```python
class BFACW_OT_coworker_editor_modal(bpy.types.Operator):
    """Modal operator that captures input for the Coworker GPU editor."""
    bl_idname = "bfacw.coworker_editor_modal"
    bl_label = "Coworker Editor Modal"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        overlay = CoworkerEditorOverlay

        if event.type == 'ESC':
            overlay.deactivate()
            return {'CANCELLED'}

        # ── Mouse handling ──
        if event.type == 'MOUSEMOVE':
            overlay._mouse_x = event.mouse_x
            overlay._mouse_y = event.mouse_y
            context.area.tag_redraw()

        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            handled = overlay._handle_click(event.mouse_x, event.mouse_y)
            if handled:
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}

        elif event.type == 'WHEELUPMOUSE':
            overlay._scroll_offset = max(0, overlay._scroll_offset - 3)
            context.area.tag_redraw()

        elif event.type == 'WHEELDOWNMOUSE':
            overlay._scroll_offset += 3
            context.area.tag_redraw()

        # ── Keyboard handling ──
        elif event.type == 'RET' and event.value == 'PRESS':
            if event.shift:
                overlay._input_text += '\n'
                overlay._cursor_pos += 1
            else:
                overlay._send_message()
            context.area.tag_redraw()

        elif event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if overlay._cursor_pos > 0:
                overlay._input_text = (
                    overlay._input_text[:overlay._cursor_pos - 1] +
                    overlay._input_text[overlay._cursor_pos:]
                )
                overlay._cursor_pos -= 1
            context.area.tag_redraw()

        elif event.type == 'DEL' and event.value == 'PRESS':
            if overlay._cursor_pos < len(overlay._input_text):
                overlay._input_text = (
                    overlay._input_text[:overlay._cursor_pos] +
                    overlay._input_text[overlay._cursor_pos + 1:]
                )
            context.area.tag_redraw()

        elif event.type == 'LEFT_ARROW' and event.value == 'PRESS':
            overlay._cursor_pos = max(0, overlay._cursor_pos - 1)
            context.area.tag_redraw()

        elif event.type == 'RIGHT_ARROW' and event.value == 'PRESS':
            overlay._cursor_pos = min(
                len(overlay._input_text), overlay._cursor_pos + 1)
            context.area.tag_redraw()

        elif event.value == 'PRESS' and event.unicode:
            overlay._input_text = (
                overlay._input_text[:overlay._cursor_pos] +
                event.unicode +
                overlay._input_text[overlay._cursor_pos:]
            )
            overlay._cursor_pos += 1
            context.area.tag_redraw()

        return {'PASS_THROUGH'} if event.type in {
            'MIDDLEMOUSE', 'NUMPAD_PERIOD'
        } else {'RUNNING_MODAL'}
```

### Registration Pattern

```python
# In __init__.py register():
_classes = (
    # ... existing classes ...
    BFACW_HT_coworker_header,          # Custom header
    BFACW_OT_coworker_editor_modal,    # Modal for input capture
)

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
```

**Key points about this approach:**
- The `CoworkerEditorOverlay` class manages the GPU draw handler lifecycle
- The custom `bpy.types.Header` replaces the native Text Editor header
- The modal operator captures ALL keyboard/mouse input for the custom UI
- All colors read from theme via `theme_utils.py` — no hardcoded colors
- Users can switch back to any workspace — the takeover is per-area, not global
- **No C++ required** — 100% Python addon