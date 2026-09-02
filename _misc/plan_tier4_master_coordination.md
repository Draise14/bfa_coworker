# BFA Coworker - Tier 4 Master Coordination Plan

**Date**: 2026-08-27
**Status**: Planning - Consolidated from audit of tier4a, 4b, 4c, 4d
**Purpose**: Coordinate all Tier 4 sub-plans, fill identified gaps, establish shared systems

---

## Table of Contents

1. [Tier 4 Overview and Sub-Plan Map](#1-tier-4-overview-and-sub-plan-map)
   - [1.1 Before / After Matrix — What Tier 4 Changes](#11-before--after-matrix--what-tier-4-changes)
2. [Priority Reorder: Tier 6 Domain Tooling First](#2-priority-reorder-tier-6-domain-tooling-first)
3. [Gap: CHOYA Guided Prompting System](#3-gap-choya-guided-prompting-system)
4. [Gap: Shared UI Component Library](#4-gap-shared-ui-component-library)
   - [4.5 Note: Native Blender Markdown Rendering Coming (Defer Draw Work to Tier 5)](#45-note-native-blender-markdown-rendering-coming-defer-draw-work-to-tier-5)
5. [Gap: Translation Integration](#5-gap-translation-integration)
   - [5.5 "Explain This to Me" — Documentation-Grounded UI Explainer](#55-explain-this-to-me--documentation-grounded-ui-explainer)
6. [Gap: Macro System Design (Implementation Deferred to Tier 5)](#6-gap-macro-system-design-implementation-deferred-to-tier-5)
7. [Revised Scope: Tier 4c - Text Editor Artist Tooling](#7-revised-scope-tier-4c---text-editor-artist-tooling)
8. [Moodboard Moved to Tier 5](#8-moodboard-moved-to-tier-5)
9. [Tier 4e - Artist Workflow Tooling (Rigging, Animation, Smart Save)](#9-tier-4e---artist-workflow-tooling-rigging-animation-smart-save)
10. [Hotkey Policy](#10-hotkey-policy)
11. [Brand Detection Across Editors](#11-brand-detection-across-editors)
12. [Implementation Order](#12-implementation-order)
13. [Dependency Graph](#13-dependency-graph)

---

## 1. Tier 4 Overview and Sub-Plan Map

| Sub-Plan | Document | Scope | Est. LOC |
|----------|----------|-------|----------|
| **Tier 4 (domain first)** | plan_tier6_domain_tooling.md (6a/6b/6d/6e lanes) | VSE + Text Editor + Node tools + prompt enrichment — pulled forward, see Section 2 | ~1,530 |
| **Tier 4** | plan_tier4_editor_integration.md | **Agent dedicated central editor** (Coworker workspace), viewport overlays | ~1,350 |
| **Tier 4b** | plan_tier4b_competitor_ux_analysis.md | Chat UX, "Explain this to me", sessions, right-click explain | ~860 |
| **Tier 4c** | plan_tier4c_text_editor_ide_agent.md | **Text Editor IDE agent**: code gen, fix, edit selection — Tier 4 focus area | ~880 (revised) |
| **Tier 4e** | plan_tier4e_nice_to_haves.md | Rigging, animation, smart-save tooling | ~950 (est) |
| **This doc** | plan_tier4_master_coordination.md | CHOYA, shared components, translation, macros, explainer | ~400 |
| **→ Tier 5** | plan_tier5_moodboard_storyboarding.md | **Moodboard editor + UX moved out of Tier 4 entirely** — see Section 8 | ~520 (MVP) |

**Total Tier 4 estimate (revised)**: ~5,970 LOC (Moodboard ~520 LOC removed → Tier 5).

> **Ordering decisions (2026-09-01):**
> 1. Tier 6 domain tooling is the **first implementation lane** (Section 2) — tooling breadth is what makes local models (and external harnesses) smart and reliable.
> 2. **Moodboard editor + UX moved out of Tier 4 entirely** → Tier 5 (Section 8). Tier 4 focuses on **agent access** (chat/Ask/explainer), the **Text Editor IDE agent** (Tier 4c), and the **agent dedicated central editor** (Coworker workspace).

### 1.1 Before / After Matrix — What Tier 4 Changes

| # | Feature | Before (Tier 3) | After (Tier 4) | Difficulty | Order |
|---|---------|-----------------|----------------|------------|-------|
| 1 | **Domain tooling** (VSE, Text Editor, Node) | Agent hallucinates `bpy.ops.sequencer.*` / node wiring / text ops from memory; high spiral rate | Pre-authored toolcodes with structured params; agent picks tool + params, server does the how | 🟢 Easy–Medium (toolcode pattern already proven) | **1st** (Phase 0) |
| 2 | **Smart-save tooling** (Tier 4e quick win) | Agent cannot save, check unsaved state, pack resources, or export — data-loss risk | `save_blend_file`, `check_unsaved_changes`, `pack_resources`, `export_selection`, `incremental_save` | 🟢 Easy (5 simple toolcodes) | **2nd** (Phase 0.5) |
| 3 | **Shared UI component library** (`ui_components.py`) | Markdown/code-block/status rendering duplicated in every panel | One shared module; all editors import from it. **Markdown draw mechanics deferred to Tier 5** (native `label_markdown()` inbound — see §4.5); components compose on either renderer | 🟢 Easy (extract + consolidate) | **3rd** (Phase 1) |
| 4 | **Brand detection** (`shared.py`) | `_is_bfa` / `AGENT_ICON` defined locally in `ui_chat.py` | Shared constant imported everywhere | 🟢 Easy (~10 LOC) | **3rd** (Phase 1) |
| 5 | **Chat UX** (Tier 4b: code blocks + Run, error-fix, sessions, right-click explain, vision) | Markdown done in Tier 3; no Run button, no error-fix loop, no session history, no right-click explain | Competitor-parity chat: Run with confirmation, error→fix loop, sessions, explain, screenshot/vision. Markdown draw stays on Tier 3 impl (deferred) | 🟡 Medium (mostly UI + agent loop wiring) | **4th** (Phase 2) |
| 6 | **CHOYA guided prompting** | Agent concludes, user must think of next step and type it | Contextual action buttons after every conclusion; one click sends a new message | 🟡 Medium (option generation + UI) | **5th** (Phase 2.8) |
| 7 | **Text Editor IDE agent** (Tier 4c) | Text Editor panel is a duplicate chat; no code tools | Artist-friendly code tools: generate, execute, error-fix, edit/explain selection, prompt templates | 🟡 Medium (needs 6b text tools first) | **6th** (Phase 3) |
| 8 | **Agent dedicated central editor** (Tier 4) | Agent feedback is chat-only; no dedicated workspace | Coworker workspace (USERPREF-pattern center panels), viewport status overlay, focus highlight, CHOYA in viewport | 🟡 Medium (GPU draw handlers + workspace setup) | **7th** (Phase 4) |
| 9 | **Rigging tooling** (Tier 4e) | Agent writes `parent_set`, `constraint_add`, IK chains from scratch — high hallucination | 6 toolcodes: add armature/bone/constraint, IK setup, mirror pose, bake | 🟡 Medium (bone/constraint domain knowledge) | **8th** (Phase 5+) |
| 10 | **Animation tooling** (Tier 4e) | Agent writes `keyframe_insert` boilerplate, F-curve modifiers, NLA from memory | 5 toolcodes: batch keyframe, interpolation, F-curve modifier, NLA track, bake | 🟡 Medium | **9th** (Phase 5+) |
| 11 | **Translation integration** | No translation support | Right-click translate with target-language preference | 🟢 Easy (reuses right-click explain plumbing) | **10th** (Phase 2.6) |
| 12 | **"Explain this to me"** (docs-grounded UI explainer) | New users must search the manual / watch tutorials to learn what a button or concept does | Right-click any UI element / object / node → agent explains what → how → when to use → pitfalls, grounded in bundled docs via `search_manual_docs`/`search_api_docs`. Also `/explain` in Ask mode | 🟢 Easy (reuses right-click explain + doc tools + Ask mode) | **11th** (Phase 2.5) |
| 13 | **Macro system** (design now, impl Tier 5) | No reusable action sequences | Data model + recording/replay designed; implementation deferred | 🟢 Easy (design only) | **12th** (design) |
| 14 | **Advanced intelligence** (Tier 6f: Agent Teams, Scene Co-Pilot, Render Critic) | Single-agent loop only | Planner→specialists→validator, passive scene issue detection, render critique loop | 🔴 Hard (multi-agent orchestration) | **13th** (capstone) |
| — | **Moodboard editor** (→ Tier 5) | No visual reference board; agent has no image context | **Moved out of Tier 4 entirely** — GPU-canvas image board with agent vision bridge lands in Tier 5 (see §8) | 🔴 Hard (GPU takeover, custom canvas) | **Tier 5** |

**Reading the order column**: 1–2 are tooling (the foundation — makes the agent
smart), 3–4 are shared infrastructure (makes UI work cheap), 5–6 are chat UX
(the visible polish), 7–8 are the Tier 4 focus areas (Text Editor IDE agent +
agent dedicated central editor), 9–10 are more tooling (completes the domain
matrix), 11–12 are the learning/accessibility pair (translate + explain — both
ride the same right-click plumbing), 13–14 are nice-to-haves and the capstone.
Difficulty ramps from 🟢 → 🔴 as the order progresses, so early wins build
momentum before the hard GPU/multi-agent work. The Moodboard (🔴 hardest) is
deliberately pushed to Tier 5 so Tier 4 stays focused on agent access + IDE +
central editor.

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

### 4.5 Note: Native Blender Markdown Rendering Coming (Defer Draw Work to Tier 5)

> **Update (2026-09-01):** Blender PR
> [#163254](https://projects.blender.org/blender/blender/pulls/163254) adds a
> native `layout.label_markdown()` API (MD4C parser, MIT-licensed `extern/md4c`).
> It supports **bold, italic, inline code, fenced code blocks, lists, headings,
> blockquotes, horizontal rules, and clickable links** — with theme-aware colors,
> code-box/quote-line drawing, wrap-width layout, layout caching across redraws,
> and even a dev-config panel to live-tweak the md_style namespace (debug value
> 4002). The PR is gated behind `G.debug_value == 4002` for the dev-config, but
> the core API ships regardless.

**Decision:** Our own markdown drawing work is **deferred to Tier 5**, not done
in Tier 4. Rationale:

| Factor | Why defer |
|--------|-----------|
| **Native API inbound** | Once `label_markdown()` lands in stock Blender, our hand-rolled `_render_markdown()` layout simulation is obsolete for new features — building more of it in Tier 4 is wasted effort |
| **Ship now, adopt later** | The current `_render_markdown()` in `ui_chat.py` (box/column/label simulation with icons, indentation, LaTeX→Unicode) is good enough for v1 and works on stock builds today |
| **Adoption strategy** | Tier 5: keep our implementation as the fallback, but switch conclusions/chat rendering to `layout.label_markdown()` when available (feature-detect like `_can_multiline()` does for `label_multiline`) |
| **Tier 4 stays additive** | Tier 4's shared library draws *components* (code-block boxes, guided-button rows, reasoning panels) — not the inline markdown itself. Those components compose on top of either renderer |

**What this changes:**
- `ui_components.py` (4.2) keeps `draw_code_block`, `draw_guided_options`, `draw_reasoning_panel`, `draw_tool_inline`, `draw_agent_status` — but **not** a standalone `draw_markdown`; instead it exposes a thin wrapper that prefers `label_markdown()` when present and falls back to the existing `_render_markdown()`.
- Tier 4b (Chat UX) already has markdown in Tier 3 — no new markdown-drawing LOC in Tier 4.
- The **`[Run]`, error-fix, sessions, right-click explain, vision** parts of Tier 4b remain in scope; only the *markdown draw mechanics* are deferred.
- Add a Tier 5 task: "Adopt native `label_markdown()` for assistant conclusions" (feature-detect + fallback).

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

### 5.5 "Explain This to Me" — Documentation-Grounded UI Explainer

**What**: Right-click any UI element (button, panel, property, menu item, editor
area, or even a 3D object/string/text node) → "Explain this with Coworker". The
agent responds with: **what it is** (grounded in the official docs), **how it
works** (the concept behind it), **when/how to use it** (practical steps), and
**common pitfalls**. This is the single most useful feature for new users.

**Why it matters**: Blender/Bforartists has a notoriously steep learning curve.
New users don't know what a "work surface", "Dope Sheet", "principled BSDF", or
"Collection Instancer" is, or why they'd use it. Today they'd have to search the
manual, watch tutorials, or ask a forum. With this feature, they hover/right-click
and get an instant, context-aware explanation from an agent that has access to
the bundled docs.

**Power source — the existing doc tools** (`get_python_api_docs`,
`search_api_docs`, `search_manual_docs`) are already always-loaded surface tools.
The explainer prompts the agent to search the bundled manual/API docs for the
target, then synthesizes an answer. This grounds the explanation in the official
documentation instead of the LLM's (possibly wrong) memory.

**Where it plugs in (Tier 4b Phase 5 — Right-Click Explain)**:

| Entry point | How | Target passed to agent |
|-------------|-----|------------------------|
| **Right-click any UI button/panel** | `BFACW_OT_explain` on the UI context menu (`WM_OT_ui_context_menu`) | `context.ui_item` — label, ident, tooltip, class, RNA path |
| **Right-click a 3D object** | Object context menu → "Explain with Coworker" | Object type, name, parents, modifiers, materials |
| **Right-click a Text object / string property** | String-style explain — "what does this field do" | The property name + description + current value |
| **Right-click a node** (Shader/GN/Compositor) | Node context menu → "Explain with Coworker" | Node `bl_idname`, socket list, current values |
| **Chat "Ask" mode slash** | Type `/explain <thing>` in Ask mode | The literal text after the slash |
| **Hover tooltip "?"** | Optional: a `?` button next to advanced settings | The property path |

**Two interaction modes:**

1. **Direct answer in chat** (Ask mode / right-click) — agent searches docs, returns
   a formatted explanation: what → how → when to use → pitfalls → "try this" steps.
2. **Guided next steps with CHOYA** — after the explanation, CHOYA offers
   "Apply it to my selection", "Show me the docs", "Give me an example", "Explain
   the next option". A new user can learn *while doing*.

**Implementation** (~150 LOC):

| Step | What | LOC |
|------|------|-----|
| 1 | `BFACW_OT_explain` operator (right-click entry, collects `context.ui_item` / object / node / property target) | ~60 |
| 2 | Explain prompt template in `prompts.yml` — instructs the agent to search docs first, then answer with the what/how/when/pitfalls structure | ~40 |
| 3 | Wire into right-click context menus (UI menu, object menu, node menu) | ~30 |
| 4 | `/explain` slash command handler in Ask mode | ~20 |

**Grounding rule (anti-hallucination):** the explain prompt requires the agent
to call `search_manual_docs`/`search_api_docs` for the target before answering,
and to cite the doc section it found. If no doc hit, the agent says so instead of
inventing API details. This reuses the exact doc-search plumbing already built.

**Relationship to Translation (5.3):** the same operator plumbing serves both —
`BFACW_OT_explain` and `BFACW_OT_translate` share the right-click dispatch; one
asks "what is this / how do I use it", the other asks "translate this label into
my language".

**Files modified**: `operators_agent.py` (new operators), `ui_chat.py` (Ask-mode
slash, menu entries), `prompts.yml` (explain prompt template),
`ui_components.py` (CHOYA options after explanation).

**Est. LOC**: ~150 (operator + prompt + menu wiring + slash command; CHOYA
integration reuses 3.0).

---

## 6. Gap: Macro System Design

### 6.1 What

BlenderMCP Pro has Macros - save action sequences as reusable named tools. You mentioned this should be designed for Tier 4 even if implementation is Tier 5.

### 6.2 Design (Implementation Deferred to Tier 5)

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

### 6.3 Why Design Now?

- Tier 4 features (code blocks, error-fix, guided options) should record actions that can become macros later
- The agent controller tool call logging should be macro-aware from the start
- Session history (Tier 4b Phase 4) should store enough data to reconstruct macros

### 6.4 Files (Tier 5)

- addon/bfa_coworker/macros.py (new) - Macro data model, storage, replay
- addon/bfa_coworker/ui_chat.py - Macro panel, record/stop buttons
- addon/bfa_coworker/agent_controller.py - Recording hooks in tool call pipeline

### 6.5 Est. LOC (Tier 5)

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

## 8. Moodboard Moved to Tier 5

> **Decision (2026-09-01):** The Moodboard editor and its UX are **moved out of
> Tier 4 entirely** and land in Tier 5. Tier 4 focuses on **agent access** (chat,
> Ask mode, explainer), the **Text Editor IDE agent** (Tier 4c), and the **agent
> dedicated central editor** (Coworker workspace).

### 8.1 Why Move It

| Factor | Rationale |
|--------|-----------|
| **Scope focus** | Tier 4's core value is *agent access* — making the agent reachable and useful everywhere (chat, Ask, explainer, Text Editor IDE, central editor). The Moodboard is a *content workspace*, not an access surface. |
| **Difficulty** | The Moodboard is the 🔴 hardest item (GPU takeover, custom canvas, image cards, drag/scale/pan/zoom, agent vision bridge). Putting it last in Tier 4 delayed the accessible wins. |
| **Dependency fit** | The Moodboard's real power comes from **generation** (Tier 5a gen plugins) and **storyboarding** (Tier 5 moodboard_storyboarding plan). It belongs with its dependencies, not before them. |
| **Native markdown inbound** | Tier 5 will adopt `label_markdown()` (PR #163254) — the Moodboard's agent panels will look native without custom GPU drawing. |
| **Tier 4 stays lean** | Removing ~520 LOC of GPU-canvas work keeps Tier 4 focused and shippable. |

### 8.2 What Moves to Tier 5

The full Image Moodboard MVP from `plan_tier4d_moodboard_editor.md`:

| Phase | Feature | LOC |
|-------|---------|-----|
| 1 | Moodboard data model + blend-file persistence | ~100 |
| 2 | Node Editor canvas shell (GPU takeover) | ~150 |
| 3 | Image card rendering (thumbnails, selection, gizmos) | ~120 |
| 4 | Import UX (file browser drag-drop, paste) | ~80 |
| 5 | Agent context bridge (send selected images to vision LLM) | ~50 |
| 6 | Annotation support (reuse Node Editor annotation brush) | ~20 |
| **Total** | | **~520 LOC** |

This becomes **Tier 5 milestone M1** in `plan_tier5_moodboard_storyboarding.md`
(which already lists "What is Deferred from Tier 4d" — now the *entire* MVP is
deferred, not just the storyboarding layers).

### 8.3 What Tier 4 Keeps (the Focus)

| Focus area | Where | Status |
|------------|-------|--------|
| **Agent access** | Chat panel, Ask mode, `/explain`, right-click explain, translation | Tier 4b + §5.5 |
| **Text Editor IDE agent** | Code gen, execute, error-fix, edit/explain selection, prompt templates | Tier 4c (Section 7) |
| **Agent dedicated central editor** | Coworker workspace (USERPREF-pattern center panels), viewport status overlay, focus highlight, CHOYA in viewport | Tier 4 (Section 1, row 8) |

### 8.4 Files

- `plan_tier4d_moodboard_editor.md` — **superseded**; content folded into Tier 5. Keep as reference, mark as moved.
- `plan_tier5_moodboard_storyboarding.md` — add the full MVP as milestone M1 (data model → canvas → cards → import → vision bridge → annotation).

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

### Phase 4: Tier 4 Agent Dedicated Central Editor + Viewport (Week 5-6)

> Moodboard moved to Tier 5 — this phase is now the agent's dedicated central
> editor (Coworker workspace) + viewport overlays.

| Step | What | Depends On | LOC |
|------|------|------------|-----|
| 4.1 | Coworker workspace setup (USERPREF-pattern center panels) | 1.1 | ~40 |
| 4.2 | Viewport status overlay | Nothing | ~60 |
| 4.3 | Agent focus highlight | 4.2 | ~80 |
| 4.4 | CHOYA in viewport overlay | 4.2 | ~50 |

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

Tier 4 Central Editor + Viewport:
  Coworker Workspace -> Center Panels (chat, queue, status)
  Viewport Status -> Focus Highlight
  Viewport Status -> CHOYA in Viewport

CHOYA (shared):
  CHOYA in Chat -> CHOYA in Text Editor
  CHOYA in Chat -> CHOYA in Viewport

(Tier 4d Moodboard dependency chain moved to Tier 5 — see
 plan_tier5_moodboard_storyboarding.md)
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
- **Moodboard moved to Tier 5 (2026-09-01)**: the entire Image Moodboard MVP + UX is out of Tier 4. Tier 4 focuses on agent access (chat/Ask/explainer), the Text Editor IDE agent (4c), and the agent dedicated central editor (Coworker workspace). See §8.
- **Tier 4e** added: rigging, animation, and smart-save tooling (~950 LOC, 3 new domains)
- **Hotkey policy**: no custom hotkeys in Tier 4; all features via sidebar/context menus
- **Brand detection**: moved to shared.py for use across all editors
- **Priority reorder (2026-09-01)**: Tier 6 domain tooling (VSE, Text Editor, Node) is now the first implementation lane — tooling breadth is what makes local models and external harnesses smart and reliable; interface polish builds on top.
- **Markdown draw deferred to Tier 5 (2026-09-01)**: Blender PR #163254 adds native `layout.label_markdown()` (MD4C, bold/italic/code/lists/links, theme-aware). Tier 4 keeps the Tier 3 `_render_markdown()` as-is and only builds *components*; Tier 5 adopts the native API with feature-detect + fallback.
- **"Explain this to me" added (2026-09-01)**: docs-grounded right-click explainer for any UI element / object / node + `/explain` in Ask mode — the highest-value feature for new users. Reuses the right-click plumbing, bundled doc tools, and Ask mode; grounded in `search_manual_docs`/`search_api_docs` to prevent hallucination. See §5.5.
