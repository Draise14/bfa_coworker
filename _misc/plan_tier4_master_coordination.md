# BFA Coworker - Tier 4 Master Coordination Plan

**Date**: 2026-08-27
**Status**: Planning - Consolidated from audit of tier4a, 4b, 4c, 4d
**Purpose**: Coordinate all Tier 4 sub-plans, fill identified gaps, establish shared systems

---

## Table of Contents

1. Tier 4 Overview and Sub-Plan Map
2. Priority Reorder: Tier 6 Domain Tooling First
3. Gap: CHOYA Guided Prompting System
4. Gap: Shared UI Component Library
5. Gap: Translation Integration
6. Gap: Macro System Design (Implementation Deferred to Tier 5)
7. Revised Scope: Tier 4c - Text Editor Artist Tooling
8. Revised Scope: Tier 4d - Image Moodboard MVP
9. Tier 4e - Artist Workflow Tooling (Rigging, Animation, Smart Save)
10. Hotkey Policy
11. Brand Detection Across Editors
12. Implementation Order
13. Dependency Graph

---

## 1. Tier 4 Overview and Sub-Plan Map

| Sub-Plan | Document | Scope | Est. LOC |
|----------|----------|-------|----------|
| **Tier 4 (domain first)** | plan_tier6_domain_tooling.md (6a/6b/6d/6e lanes) | VSE + Text Editor + Node tools + prompt enrichment — pulled forward, see Section 2 | ~1,530 |
| **Tier 4** | plan_tier4_editor_integration.md | Coworker workspace, viewport overlays | ~1,350 |
| **Tier 4b** | plan_tier4b_competitor_ux_analysis.md | Chat UX: markdown, code blocks, sessions, explain | ~860 |
| **Tier 4c** | plan_tier4c_text_editor_ide_agent.md | Text Editor: code gen, fix, edit selection | ~880 (revised) |
| **Tier 4d** | plan_tier4d_moodboard_editor.md | Image moodboard: GPU canvas, agent context | ~520 (revised MVP) |
| **Tier 4e** 🆕 | plan_tier4e_nice_to_haves.md | Rigging, animation, smart-save tooling | ~950 (est) |
| **This doc** | plan_tier4_master_coordination.md | CHOYA, shared components, translation, macros | ~400 |

**Total Tier 4 estimate (revised)**: ~6,490 LOC across ~20 new files + modifications to ~10 existing files.

> **Ordering decision (2026-09-01):** Tier 6 domain tooling is now the **first
> implementation lane** (Section 2). Tooling breadth is what makes local models
> (and external harnesses) smart and reliable — UI alone cannot fix a model that
> has to hallucinate `bpy` calls for domains it has no tools for.

---

## 2. Priority Reorder: Tier 6 Domain Tooling First

> **Decision (2026-09-01):** The Tier 6 domain tooling plan is pulled forward to
> the **first implementation priority** of Tier 4. The tooling foundation
> (domain MCP tools) is what makes the agent — especially local models —
> efficient, smart and reliable. Interface polish without tooling breadth leaves
> the agent guessing; tooling breadth alone improves every surface that talks to
> it, including external harnesses.

### 2.1 Why Tooling Before Interface

| Ordering | Result |
|----------|--------|
| Interface first, tooling later | Local models still hallucinate `bpy` calls for VSE/node/text ops; chat shows prettier failures |
| **Tooling first, interface later** | Every surface (chat, Text Editor, harness, CHOYA) instantly benefits; interface work has real tools behind it |

The core insight from `plan_tier6_domain_tooling.md` still holds: each
pre-authored toolcode bundles domain knowledge, error handling, and a structured
return type, so the LLM only needs to understand the tool description + parameter
schema — not the Blender Python API for that domain.

### 2.2 What We Pull Forward (and What We Skip)

| Tier 6 Phase | Tools | Status in Tier 3 | Pull Forward? |
|---|---|---|---|
| 6a — VSE / Sequencer | 5 tools (~500 LOC) | Not started | ✅ Yes — Reads + 1 write + render feedback. The Sequencer is invisible to the agent today |
| 6b — Text Editor | 5 tools (~450 LOC) | Not started | ✅ Yes — Feeds Tier 4c; the agent learns to read/edit/run its own scripts |
| 6c — Asset Browser | 9 tools (~800 LOC) | ✅ Done in Tier 3d (13 tools incl. index + wiring) | ❌ Skip — already delivered |
| 6d — Shader / Node Editor | 7 tools (~650 LOC) | 🟡 Partial (`get_active_node_tree`, `get_node_group_interface`, `wire_node_group` done) | ✅ Yes — Add `get_node_detail`, `create_node`, `connect_nodes`, `set_node_input_value`, `mute_node` (~380 LOC). Highest-leverage domain for local models: node wiring is the #1 hallucination source |
| 6e — Prompt + cross-domain | 2 files (~200 LOC) | Not started | ✅ Yes — Domain chapters + screenshot enrichment so the new tools actually get used |
| 6f — Advanced intelligence | ~1,100 LOC | Not started | 🟡 Later in Tier 4 — Agent Teams, Scene Co-Pilot, Render Critic as the capstone; Voice Input deferred to Tier 5 |

**Recommended first Tier 4 milestone: 6a + 6b + 6d-tools + 6e (~1,530 LOC, ~28 files).**

### 2.3 What the Domain Tools Buy Us

- **Read-then-write discipline** — the plan's "read tools first" principle gives the agent situational awareness before acting (fewer spirals, fewer wrong guesses).
- **Local model leverage** — a 7–14B model reliably picks a tool + params; it cannot reliably write `bpy.ops.sequencer.*` or wire nodes from memory.
- **External harness parity** — every MCP tool is available to external harnesses (Opencode, Claude Desktop) for free.
- **Feed-forward into Tier 4c** — Text Editor tools (6b) are the prerequisite for the IDE-agent experience.
- **CHOYA subscriber** — guided options ("Add a speed ramp to strip X") become one-click tool invocations instead of raw code.
- **Tier 4e synergy** — rigging/animation/save tooling (see Section 9) completes the domain matrix on top of the same toolcode pattern.

---

## 3. Gap: CHOYA Guided Prompting System

### 3.1 What Is CHOYA?

CHOYA = **Contextual Helpful Options You Act on** - clickable action buttons that appear after agent responses, offering the user sensible next steps based on what the agent just did and what is in the scene.

This is the guided prompting pattern from your overview: CHOYA guided buttons and prompt injection while talking.

### 3.2 Why It Matters

Without CHOYA, the agent says I have created a cube and the user has to think about what to do next and type a new message. With CHOYA, the agent offers:

```
Coworker: I have created a red cube at the origin with a Principled BSDF material.

What is next?
[Add Modifier] [Duplicate] [Export FBX] [Set Origin]
[Add to Collection] [Apply Transform] [UV Unwrap]
```

One click sends a new message. The user does not have to know Blender API or type anything.

### 3.3 Design

**Data model** - each guided option is:

```python
@dataclass
class GuidedOption:
    label: str              # Button text: Add Modifier
    prompt: str             # What gets sent to the agent
    icon: str               # Blender icon name: MOD_SUBSURF
    category: str           # modifier | transform | export | material | mesh | scene
```

**Generation** - the agent conclusion message includes a guided_options field:

```python
# In agent_controller.py, after the agent responds:
guided_options = _generate_guided_options(conclusion_text, scene_snapshot)
# Returns 4-6 relevant options based on:
# - What the agent just did (created object -> offer material/modifier/transform)
# - What is selected (mesh selected -> offer edit mode, UV unwrap)
# - What is missing (no materials -> offer Add Material)
```

### 3.4 Option Categories

| Context | Options Offered |
|---------|----------------|
| **Object created** | Add Material, Add Modifier, Duplicate, Set Origin, Export |
| **Material assigned** | Change Color, Add Texture, Copy to Others, Remove |
| **Modifier added** | Apply, Duplicate, Remove, Show in Viewport |
| **Error occurred** | Fix with Coworker, Show Details, Retry |
| **Nothing selected** | Select Object (with @mention), Create New, Import |
| **Code generated** | Run Code, Edit Code, Explain Code |
| **Image analyzed** | Create 3D from Image, Generate Variations, Describe Colors |

### 3.5 Where CHOYA Appears

| Location | When | How |
|----------|------|-----|
| Chat panel | After every agent conclusion | Button row below conclusion text |
| Viewport overlay | When agent is idle | Floating button cluster (toggleable) |
| Text Editor | After code generation | Inline buttons below code block |
| Moodboard | After image analysis | Context menu on image card |

### 3.6 Files

- addon/bfa_coworker/choya.py (new) - GuidedOption dataclass, option generation logic
- addon/bfa_coworker/ui_chat.py - render button row after conclusions
- Other editors - render in their respective panels

### 3.7 Est. LOC

~150 (option generation) + ~50 (UI rendering) = **~200 LOC**

---

## 4. Gap: Shared UI Component Library

### 4.1 Problem

All four Tier 4 sub-plans create UI independently. Markdown rendering, code blocks, agent status indicators, and guided buttons will be duplicated across the chat panel, Text Editor, and Moodboard.

### 4.2 Solution: ui_components.py

Create a shared module with reusable UI drawing functions:

```python
# addon/bfa_coworker/ui_components.py

def draw_markdown(layout, text, width=60):
    """Render markdown text in a Blender layout. Shared across all editors."""

def draw_code_block(layout, code, language="python", message_index=-1):
    """Render a code block with copy button. Used by chat and Text Editor."""

def draw_agent_status(layout, state):
    """Render agent status indicator (thinking/done/error). Used everywhere."""

def draw_guided_options(layout, options, max_visible=6):
    """Render CHOYA guided button row. Used by all editors."""

def draw_reasoning_panel(layout, text, label, is_thinking, ...):
    """Render collapsible reasoning panel. Moved from ui_chat.py."""

def draw_tool_inline(layout, tool_name, summary, is_error, ...):
    """Render inline tool call. Moved from ui_chat.py."""

def draw_copy_button(layout, text, message_index=-1):
    """Render a copy-to-clipboard button. Used everywhere."""
```

### 4.3 Migration Path

1. Create ui_components.py with the shared functions
2. Move existing functions from ui_chat.py (_draw_multiline, _draw_reasoning, _draw_tool_inline) into the new module
3. Update ui_chat.py to import from ui_components.py
4. New Tier 4 features (Text Editor, Moodboard) import from ui_components.py directly

### 4.4 Est. LOC

~300 (extract + consolidate existing functions into shared module)

---

## 5. Gap: Translation Integration

### 5.1 What

Issue #42: Translation integration - the agent helps translate UI labels, documentation, or user-facing text within Blender.

### 5.2 Design (Placeholder)

This is a lightweight feature that fits naturally into the chat panel:

- **Right-click any UI label** -> Translate with Coworker (reuses Tier 4b right-click Explain plumbing)
- **Agent receives**: the label text, the current UI language, and the target language (from preferences)
- **Agent responds**: translated text + option to apply it (via a custom translation override in preferences)

### 5.3 Implementation

- **Phase**: Tier 4b Phase 5 (bundled with Right-Click Explain)
- **Additional operator**: BFACW_OT_translate - similar to BFACW_OT_explain but with translation-specific prompt
- **Preferences**: translation_target_language EnumProperty (English, Spanish, French, German, Japanese, Chinese, Korean, etc.)
- **Override storage**: bfa_coworker_translations.json in addon prefs dir

### 5.4 Est. LOC

~80 (operator + prompt + preferences)

---

## 6. Gap: Macro System Design

### 5.1 What

BlenderMCP Pro has Macros - save action sequences as reusable named tools. You mentioned this should be designed for Tier 4 even if implementation is Tier 5.

### 5.2 Design (Implementation Deferred to Tier 5)

**Data model**:

```python
@dataclass
class MacroStep:
    tool_name: str              # MCP tool name: create_object
    parameters: dict            # Tool parameters with placeholders
    description: str            # Human-readable: Create a cube

@dataclass
class Macro:
    name: str                   # LOD Pipeline
    description: str            # Generate LODs for the selected object
    steps: list[MacroStep]      # Ordered list of tool calls
    target_parameter: str       # Which parameter gets substituted
    created_from: str           # Session ID where this was recorded
    tags: list[str]             # [mesh, optimization, lod]
```

**Recording flow**:

1. User says Start recording macro (chat command or button)
2. Agent executes tool calls as normal
3. Each tool call is recorded as a MacroStep
4. User says Stop recording - call it LOD Pipeline
5. Macro is saved to bfa_coworker_macros.json

**Invocation flow**:

1. User types /macro LOD Pipeline or clicks a macro button
2. Macro target_parameter is filled with the current selection
3. Steps are replayed through the agent tool system
4. Results are shown in the chat panel

**Storage**: bfa_coworker_macros.json in addon prefs dir (portable, not .blend-embedded).

### 5.3 Why Design Now?

- Tier 4 features (code blocks, error-fix, guided options) should record actions that can become macros later
- The agent controller tool call logging should be macro-aware from the start
- Session history (Tier 4b Phase 4) should store enough data to reconstruct macros

### 5.4 Files (Tier 5)

- addon/bfa_coworker/macros.py (new) - Macro data model, storage, replay
- addon/bfa_coworker/ui_chat.py - Macro panel, record/stop buttons
- addon/bfa_coworker/agent_controller.py - Recording hooks in tool call pipeline

### 5.5 Est. LOC (Tier 5)

~300 (data model + storage + recording + replay + UI)

---

## 7. Revised Scope: Tier 4c - Text Editor Artist Tooling

### 7.1 Changes from Original

| Original | Revised | Rationale |
|----------|---------|-----------|
| Phase 8: Text Editor File Browser | **Removed** | Not artist-friendly tooling; out of scope |
| Ctrl+Space for generate | **Removed** | Conflicts with Blender native maximize area shortcut |
| F8 for fix | **Removed** | Conflicts with Blender native reload scripts shortcut |
| All hotkeys | **Removed entirely** | Per user request: lenient on new hotkeys, none if possible |
| Phase 5: Keyboard Shortcuts and Keymap | **Removed** | No custom hotkeys per policy |
| Focus: IDE-style code editing | **Focus: Artist-friendly Text Editor tooling** | Sidebar panel and context menus only |

### 7.2 Revised Phase List

| Phase | Feature | LOC | Priority |
|-------|---------|-----|----------|
| 1 | Replace Text Editor panel with artist-friendly code tools | ~200 | CRITICAL |
| 2 | Inline code generation (via sidebar button, no hotkey) | ~200 | CRITICAL |
| 3 | Execute, Error, Fix (via sidebar button) | ~150 | CRITICAL |
| 4 | Right-click Edit/Explain with Coworker on selection | ~100 | CRITICAL |
| 5 | Prompt template system (configurable in preferences) | ~100 | HIGH |
| 6 | Queue and session integration | ~80 | HIGH |
| 7 | CHOYA guided buttons after code generation | ~50 | HIGH |

**Total revised**: ~880 LOC (down from ~1,050)

### 7.3 Access Pattern (No Hotkeys)

| Action | How to Access |
|--------|---------------|
| Generate code | Sidebar panel, Generate button |
| Fix errors | Sidebar panel, Fix button |
| Edit selection | Right-click menu, Edit with Coworker |
| Explain selection | Right-click menu, Explain with Coworker |
| Execute code | Sidebar panel, Run button |

---

## 8. Revised Scope: Tier 4d

### 8.1 Split: Image Moodboard (Tier 4d) vs. Storyboarding (Tier 5)

The original plan combined image moodboarding with storyboarding, shot sequences, scene tooling, and generation placeholders. This is too much for one tier.

**Tier 4d - Image Moodboard MVP** (this tier):

| Phase | Feature | LOC |
|-------|---------|-----|
| 1 | Moodboard data model + blend-file persistence | ~100 |
| 2 | Node Editor canvas shell (GPU takeover) | ~150 |
| 3 | Image card rendering (thumbnails, selection, gizmos) | ~120 |
| 4 | Import UX (file browser drag-drop, paste) | ~80 |
| 5 | Agent context bridge (send selected images to vision LLM) | ~50 |
| 6 | Annotation support (reuse Node Editor annotation brush) | ~20 |
| **Total** | | **~520 LOC** |

**Tier 5 - Storyboarding and Scene Tooling** (next tier):

- Shot sequences and narrative node system
- Storyboard to 3D scene conversion
- VSE animatic export
- Generation placeholders (storybuilding)
- Frame tools with markup integration

### 8.2 What is In Scope for Tier 4d

- Load images onto a canvas
- Arrange images (drag, scale, pan/zoom)
- Select images and send to agent as vision context
- Annotate with Blender annotation brush
- Save/load with .blend file (Text datablocks)
- Basic linking between images (visual lines)

### 8.3 What is Deferred to Tier 5

- Shot sequences and narrative chains
- Storyboard to 3D scene conversion
- VSE animatic export
- Generation placeholders
- Frame tools with markup
- Multi-board management
- Style guide extraction

---

## 9. Tier 4e - Artist Workflow Tooling (Rigging, Animation, Smart Save)

> See `plan_tier4e_nice_to_haves.md` for the full plan. Quick-win tooling that
> completes the domain matrix for the three most common artist-adjacent
> workflows that today still require raw `bpy` from the LLM: rigging, animation,
> and smart file saving. Same toolcode pattern, registered in the `rigging` /
> `animation` / `file_management` domains, with default_closed panels and
> CHOYA-friendly write tools.

---

## 10. Hotkey Policy

### 10.1 Principle

**No custom hotkeys in Tier 4.** All features are accessible through:
- Sidebar panels (N-panel)
- Right-click context menus
- Header buttons
- Chat panel buttons

### 10.2 Rationale

- Blender users have deeply ingrained muscle memory for default hotkeys
- Custom hotkeys risk conflicts with other addons or user customizations
- The sidebar panel is the primary access point - it is always visible when the user wants it
- Right-click context menus are the natural place for context-sensitive actions

### 10.3 Future Exception

If users request hotkeys for specific workflows (e.g., I use Generate 50 times a day, I need a shortcut), hotkeys can be added in Tier 5 as optional, configurable keymap entries - not hardcoded defaults.

---

## 11. Brand Detection Across Editors

### 11.1 Current State

Brand detection (_is_ba) is in ui_chat.py and checks VIEW3D_MT_view. This works for the 3D Viewport but should be shared.

### 11.2 Shared Constant

Move to shared.py:

```python
# addon/bfa_coworker/shared.py
import bpy

_is_bfa: bool = hasattr(bpy.types, "VIEW3D_MT_view")
AGENT_ICON: str = "WIZARD" if _is_bfa else "GHOST_ENABLED"
```

All editors import from shared.py:

```python
from .shared import _is_bfa, AGENT_ICON
```

### 11.3 Files Modified

- addon/bfa_coworker/shared.py - add _is_bfa and AGENT_ICON
- addon/bfa_coworker/ui_chat.py - import from shared instead of defining locally

---

## 12. Implementation Order

### Phase 0: Domain Tooling (Week 1-2) — pulled forward from Tier 6

> Build the tooling lanes FIRST so every interface work has real tools behind it.

| Step | What | Depends On | LOC |
|------|------|------------|-----|
| 0.1 | Tier 6a: VSE tools (5) | Nothing | ~500 |
| 0.2 | Tier 6b: Text Editor tools (5) | Nothing | ~450 |
| 0.3 | Tier 6d: remaining Node tools (4) | Nothing | ~380 |
| 0.4 | Tier 6e: prompt chapters + screenshot enrichment | 0.1-0.3 | ~200 |
| 0.5 | Tier 4e quick win: smart-save tooling | Nothing | ~250 |

### Phase 1: Foundation (Week 2-3)

| Step | What | Depends On | LOC |
|------|------|------------|-----|
| 1.1 | Create ui_components.py shared library | Nothing | ~300 |
| 1.2 | Move _is_bfa/AGENT_ICON to shared.py | Nothing | ~10 |
| 1.3 | Migrate ui_chat.py to use ui_components.py | 1.1 | ~50 |

### Phase 2: Tier 4b Chat UX (Week 3-4)

| Step | What | Depends On | LOC |
|------|------|------------|-----|
| 2.1 | Markdown rendering (Phase 1 of 4b) | 1.1 | ~150 |
| 2.2 | Code blocks + Run (Phase 2 of 4b) | 2.1 | ~120 |
| 2.3 | Error-Fix loop (Phase 3 of 4b) | 2.2 | ~60 |
| 2.4 | Session history (Phase 4 of 4b) | Nothing | ~200 |
| 2.5 | Right-click Explain (Phase 5 of 4b) | Nothing | ~100 |
| 2.6 | Translation integration | 2.5 | ~80 |
| 2.7 | Screenshot/vision (Phase 6 of 4b) | 2.1 | ~150 |
| 2.8 | CHOYA buttons in chat panel | 1.1 | ~100 |

### Phase 3: Tier 4c Text Editor (Week 4-5)

| Step | What | Depends On | LOC |
|------|------|------------|-----|
| 3.1 | Replace Text Editor panel | 1.1 | ~200 |
| 3.2 | Code generation (sidebar button) | 3.1 | ~200 |
| 3.3 | Execute, Error, Fix | 3.2 | ~150 |
| 3.4 | Right-click Edit/Explain | 2.5 | ~100 |
| 3.5 | Prompt templates | 3.1 | ~100 |
| 3.6 | Queue integration | 3.2 | ~80 |
| 3.7 | CHOYA buttons after code gen | 2.8 | ~50 |

### Phase 4: Tier 4d Moodboard (Week 5-6)

| Step | What | Depends On | LOC |
|------|------|------------|-----|
| 4.1 | Data model + persistence | Nothing | ~100 |
| 4.2 | Node Editor canvas shell | Nothing | ~150 |
| 4.3 | Image card rendering | 4.2 | ~120 |
| 4.4 | Import UX | 4.3 | ~80 |
| 4.5 | Agent vision bridge | 4.3 | ~50 |
| 4.6 | Annotation support | 4.2 | ~20 |

### Phase 5: Tier 4 Viewport and Workspace (Week 6-7)

| Step | What | Depends On | LOC |
|------|------|------------|-----|
| 5.1 | Viewport status overlay | Nothing | ~60 |
| 5.2 | Agent focus highlight | 5.1 | ~80 |
| 5.3 | Coworker workspace setup | Nothing | ~40 |
| 5.4 | CHOYA in viewport overlay | 5.1 | ~50 |

---

## 13. Dependency Graph

```
Foundation:
  Domain tools (6a/6b/6d lanes) -> 4b CHOYA, 4c Text Editor, 4e tools
  Domain tools (6a/6b/6d lanes) -> VSE / Node / Text chat capability
  ui_components.py -> Markdown, CHOYA (all editors)
  shared.py -> Brand detection (all editors)

Tier 4b Chat UX:
  Markdown -> Code Blocks -> Error-Fix
  Markdown -> Screenshot
  Session History -> Queue Integration
  Right-Click Explain -> Translation
  Right-Click Explain -> Text Editor Edit/Explain

Tier 4c Text Editor:
  Text Editor Panel -> Code Generation -> Execute+Fix
  Text Editor Panel -> Prompt Templates
  Code Generation -> Queue Integration

Tier 4d Moodboard:
  Data Model -> Node Canvas -> Image Cards -> Import UX
  Image Cards -> Agent Vision Bridge
  Node Canvas -> Annotation

Tier 4 Viewport:
  Viewport Status -> Focus Highlight
  Viewport Status -> CHOYA in Viewport

CHOYA (shared):
  CHOYA in Chat -> CHOYA in Text Editor
  CHOYA in Chat -> CHOYA in Viewport
```

---

## Summary

This master plan fills four gaps identified in the audit:

1. **CHOYA guided prompting** - contextual action buttons after agent responses, shared across all editors (~200 LOC)
2. **Shared UI component library** - ui_components.py with markdown, code blocks, guided options, agent status (~300 LOC)
3. **Translation integration** - right-click translate, bundled with Phase 5 of Tier 4b (~80 LOC)
4. **Macro system design** - data model and flow designed now, implementation deferred to Tier 5 (~300 LOC planned)

Additionally:
- **Tier 4c** revised: no file browser, no custom hotkeys, focus on artist-friendly Text Editor tooling
- **Tier 4d** revised: image moodboard MVP only, storyboarding pushed to Tier 5
- **Tier 4e** added: rigging, animation, and smart-save tooling (~950 LOC, 3 new domains)
- **Hotkey policy**: no custom hotkeys in Tier 4; all features via sidebar/context menus
- **Brand detection**: moved to shared.py for use across all editors
- **Priority reorder (2026-09-01)**: Tier 6 domain tooling (VSE, Text Editor, Node) is now the first implementation lane — tooling breadth is what makes local models and external harnesses smart and reliable; interface polish builds on top.
