# BFA Coworker — Tier 4 Master Plan

**Revised**: 2026-10-21 (§0: the four workflows; §0.1–0.5: release goals, strategy, order, cut line; later the same day: **threads per editor** become the orchestration layer; then the **re-tier against feature creep**: the Spell Book and wizards go to Tier 5, generative work to Tier 6, §0.4). Earlier full audit: 2026-10-16 against v1.1.37 @ deb417b
**Status**: 🚧 In progress. Engines/agents and editors half done; §0 plans the rest of the release
**Previous version**: `Plans History/plan_tier4_master_coordination_v1.md` (historical
decisions, detailed streaming/checkpoint designs that have since shipped)
**Task board**: `plan_tier4_task_board.md`

---

## 0. The goal: four workflows, one Coworker (2026-10-21)

Coworker doesn't replace the chat, procedural assets or add-ons: it **opens technical-
artist workflows to every artist** without them knowing the workflow beforehand, and
stays out of a seasoned artist's way.

| # | When the job is… | Best medium | Coworker's part |
|---|---|---|---|
| 1 | Serialized actions **once**, applied in context | **The chat** | Engines, tools, plan, Revert (done) |
| 2 | Serialized actions **many times**, regardless of context | **A script / add-on** | *(Tier 5)* Spell Book: record a spell from a chat turn or your clicks, replay with no model; Edit as Code; package as an extension. Chat already writes scripts without the bridge; the bridge lets it **run and fix** them |
| 3 | An asset **parameterized** for modular reuse | **Nodes + gizmos** | Procedural first (prefer library node groups), *Make Parametric* (results → node-group assets with inputs + gizmos) |
| 4 | **Parameterized + serialized** operations for advanced work | **All three together — the goal of this system** | *(Tier 5)* Wizard spells that look at the material, propose, and build with tools + node groups (e.g. *Edit from Footage*) |

**Graduation path** — promote work up the ladder only when you need it:

```
Chat result (1)
  ├─ "I'll do this again"     → Save as Spell     → a button in the toolshelf          (2)
  ├─ "I'll tweak this again"  → Make Parametric   → node-group asset, inputs + gizmos  (3)
  └─ "This is a whole flow"   → a wizard spell that drives assets + asks a few things  (4)
```

**Rules**: procedural first, linear only when asked · operators and spells work without
the chat and sit in native places · suggestions (*Save as Spell?*, *Make Parametric?*)
are one ignorable button, never a pop-up. Detail: `plan_tier4m_coworker_operators.md`.

**One job, one thread.** A scene build in the 3D View, a node-group tidy and a strip
translation are separate trains of thought: each editor gets its **own thread** (history,
Stop, Queue, plan, questions) and threads run **side by side**. This is the orchestration
layer of this release; agent teams and missions (Tier 5) are built on it. Detail:
`plan_tier4o_orchestration_threads.md`.

**Tier 4 is the foundation; the rest waits its turn.** The four workflows are the
direction, not one release. Tier 4 makes the Coworker solid in every editor, side by side;
Tier 5 automates on top of it (spells, wizards, missions, teams); Tier 6 adds generation.

### 0.1 The tiers at a glance (re-tiered 2026-10-21 against feature creep)

| Tier | Theme | What's in it | Plans |
|---|---|---|---|
| **4 — this release** | **One Coworker, every editor, side by side** | Refinement · **threads per editor** (Threads 0–3) + Footer 1 · domain tools (Tools 1–8) · Coworker operators (Wand 0–3: framework, Sequencer basics, Nodes) · Text Editor IDE (Tools 2, Text Editor 1–4) · Chat 1, 4, 8, 9 · GPU 6 guided flows · Runtime 1–2 · Skills 4–8 · Security 1–4 · Procedural 1 · *stretch*: Orchestrate 1 | this doc, `plan_tier4_task_board.md`, 4o, 4m (§3–4.2, §4.4 Procedural 1 only) |
| **5 — automation** | **Make it repeatable** | **Spell Book** (Spell 1–3, quick prompts #300 as prompt spells, *Save as Spell*, recording) · wizard spells (Edit from Footage, Bake, Asset library) · Procedural 2–3 + the asset-library ecosystem · Orchestrate 2 missions · Teams 1–3 (on threads) · Tools 9 · Security 5 · Rig 1–2 · Runtime 3 + Turbo mode · batch across files · icon migration into Bforartists | `plan_tier5c_artist_automation.md`, 4m §4.1 wizards + §5, `plan_tier5a_turbo_mode_speculative_decoding.md` |
| **6 — generative** | **Make new things** | Generative local systems (the gen-plugin foundation already shipped stays as is) · generative 2D (ComfyUI) · moodboard & storyboarding · generative 3D · viewport diffusion renderer · Mixar-style connector parity | `plan_tier6_generative_local_systems.md`, `plan_tier6_generative_2d.md`, `plan_tier6_moodboard_storyboarding.md`, `plan_tier6_generative_3d_systems.md`, `plan_tier6_viewport_diffusion_renderer.md`, `plan_tier6_mixar_agent_parity.md` |

Rule against creep: a new idea goes to the **lowest tier whose foundation it needs**, and
into Tier 4 only if it fixes or finishes something already in Tier 4.

### 0.2 Tier 4 goals and what each still needs (2026-10-21)

Checked against `main` (897b7db). 🟡 rows run with another agent and aren't re-planned here.

| # | Goal | Status | Tier 4 tasks (board) |
|---|---|---|---|
| 1 | Refinement | ✅ | Keep fixing from the test list |
| 2 | Dedicated global multi-domain chat editor | ✅ | — |
| 3 | CHOYA guided buttons + prompt injection while talking | ✅ | GPU 6 (#324) guided flows |
| 4 | Text Editor as artist→developer IDE (#52) | ☐ | Tools 2 → Text Editor 1–4 |
| 5 | Translate this (#42) | ✅ | #296 |
| 6 | Explain this in the context menus (#290) | ✅ | #294 (citing docs: Chat 9) |
| 7 | Sidebar chat in all editors (#291) | ✅ | #298 #325; Chat 4 (#299) shows the context chip |
| 8 | Footer integration of agent status | ☐ | **Footer 1**: one item in the global status bar, one icon per live thread |
| 9 | Orchestration layer + expanded domain tools (#71) | ☐ | **Threads 0–3** (`plan_tier4o_orchestration_threads.md`); Tools 1–8; Orchestrate 1 (stretch) |
| 10 | Quick tools in every editor | ☐ | **Coworker operators** Wand 0–3 in native menus / toolshelf tabs. Quick prompts (#300) and the Spell Book → Tier 5 |
| 11 | Security checks | ◐ token + permissions done | Security 1–4 |
| 12 | Procedural first | ☐ new | Procedural 1 (Make Parametric, the nudge → Tier 5) |

### 0.3 Strategy (Tier 4)

1. **One thread per job.** Each editor's work runs in its own thread, side by side; tool
   calls interleave on Blender's main thread, claims stop threads overwriting each other,
   and Revert never silently undoes another thread (4o §3).
2. **Tools, then operators.** Chores become tested tools (Tools 1–8), then ordinary-looking
   operators (wand badge) in the native menus and Bforartists toolshelf tabs; the active
   engine answers only judgment calls; preview and one undo. No user-made operators yet
   (Spell Book, Tier 5).
3. **Procedural first.** Prefer library node groups; linear only when asked.
4. **Security before reach.** Security 1–2 land before threads run side by side.

### 0.4 Order (critical path) and batches

```
Threads 0 spike ─► Threads 1 thread model ─► Threads 2 editors ─► Threads 3 side by side ─► (Orchestrate 1)
                              └─► Footer 1 (status per thread)
Security 1 ─► Security 2–4
Wand 0 spike + icons ─► Wand 1 framework ─► Wand 2 Sequencer basics ─► Wand 3 Nodes
Tools 1 ─► Wand 2 · Tools 3 ─► Wand 3 · Tools 7 + 8 ─► organize / fix operators
Tools 2 ─► Text Editor 1–4
Procedural 1 (anytime)
```

Batches (each ends in a test round): **A** Threads 0, Security 1–2, Wand 0, Tools 1,
Procedural 1 · **B** Threads 1, Wand 1, Tools 3, Footer 1 · **C** Threads 2, Wand 2
(basics), Tools 7, Tools 2 · **D** Threads 3, Wand 3, Tools 8, Security 3–4 · **E** Text
Editor 1–4, GPU 6 · stretch Orchestrate 1 → **Test 3 + Test 5 + Test 7 (side by side)** →
release.

Threads 1 and 3 rework `agent_controller.py` / `ui_chat.py`: the board's one-open-PR rule
on those files applies, so they are scheduled between the other agent's PRs.

### 0.5 What moved (2026-10-21)

| Moved | To | Why |
|---|---|---|
| Spell Book: Spell 1–3, quick prompts per editor (#300) as prompt spells, *Save as Spell* | **Tier 5** | Macros are automation on top of a solid Coworker; feature creep for this release |
| Edit from Footage, Assemble Timeline, Export Review Video; Wand 4–5 wizards | **Tier 5** | Wizard spells need the Spell Book and a settled operator framework |
| Procedural 2–3 (Make Parametric, the nudge), Tools 9, Orchestrate 2, Teams 1–3, Security 5 | **Tier 5** | Build on threads / the asset library / sharing |
| Generative local systems, generative 2D, moodboard & storyboarding (were `plan_tier5*`) | **Tier 6** | Generation joins generative 3D and the diffusion renderer; files renamed to `plan_tier6_*` |
| Turbo mode (speculative decoding) | stays **Tier 5** | An LLM accelerator, blocked upstream; not generative |

### 0.6 Audit notes (2026-10-20/21)

| Finding | Action |
|---|---|
| #219 (placement tools) closed as completed | Only a guidance rule shipped (#257); no tools → **Tools 7** |
| Chat 1 (native markdown) | Still no `label_markdown` use in the add-on; open as #301 |
| Quick prompts (#300) vs operators | #300 becomes **prompt spells** in the Spell Book — Tier 5 with it |
| Editors 5 (viewport dot) dropped | Footer 1 is the status bar instead — your goal 8 |
| One conversation behind every editor (`turn_active` guard, module-level Stop and Queue, one chat shown in every sidebar) | **Threads 0–3** (4o): one thread per editor, side by side |

---

## 1. Tier 4 in one paragraph

Tier 4 turns Coworker from "a good local agent with a chat panel" into **one coworker
that runs on any engine** (local model, remote API, or the user's own agent subscription
over ACP) and **acts through domain tools instead of guessing `bpy`**. It works from
anywhere in the interface: right-click explain, every sidebar, the Text Editor, a
dedicated Coworker editor. The yardsticks are **UX parity across engines** and **tokens
per finished task**.

### Principles (apply to every lane)

1. **One Coworker, three engines.** UI renders a shared event stream. Blender-side
   intelligence lives in the MCP server so every engine gets it (`plan_tier4i_engine_parity.md`).
2. **Tools over code.** A pre-authored, tested tool beats generated `bpy`, especially on 7–14B models.
3. **Token economy is measured.** Cost ledger (3i.0) or agent-reported usage before/after.
   The checklist is in 4i §4.
4. **Native UI.** Native panels and widgets first; GPU drawing only for transient overlays.
   No custom hotkeys (§6.4).
5. **White room.** Ideas from other products are recorded as *need → our design* and
   implemented from the Blender/ACP/MCP specs. No code, prompts or texts are copied
   (rules in 4b §4).

---

## 2. Audit summary (what changed since the v1 plans)

### 2.1 Already shipped in Tier 3 that Tier 4 plans still listed as work

| Planned in | Item | Shipped as |
|---|---|---|
| 4b P1 / master §14 | Token streaming (SSE) | `llm_transport.py` streaming + live readout |
| 4b P7 / 4f.2.3 | Token budget + readout | Usage per turn/session, context indicator, self-calibrating budget (issue #69, 3i.0, 3k) |
| 4b P9 / master §14.6 / 4f.2.2 | Checkpoint / compaction | `session_memory.py` compaction, memory block, titled checkpoints, restore (3k) |
| 4b P6 | Screenshot / vision input | Image socket, capture, drag-and-drop, sticky (#88/#90/#91) |
| master §4.5 (deferred) | Markdown | Tier 3 renderer with tables, headings, code blocks + Copy |
| 4f.1.1 | KV quant, flash-attn, batch/ubatch, cache-reuse | `llm_manager.start_local_llama` with build-aware flag filtering |
| 4g.6.3 | External client config | Harness presets + Copy/Test MCP Config |
| 4e.2.1 | `batch_keyframe_insert` | Tool exists in `mcp/blmcp/tools/` |
| 4f "500-char trim" | Tool result trim | Now `_MAX_TOOL_RESULT_CHARS = 2000` with tail-preserving errors |
| — | Goal & Plan, stop rules, crash-safe servers, extension updates | 3k, #85, #86 |

### 2.2 Corrections and risks found

| Plan | Finding | Action |
|---|---|---|
| **All** | Loop intelligence (domain loading, plan, compaction, spiral recovery) is addon-only. External clients get all schemas at once and no plan | New **Tier 4i** moves scene-side intelligence into the server |
| **4h** | Was "draft, not scheduled" | **Promoted to first lane** (user priority); expanded design |
| **4b** | Feature table had broken column counts and stale "Now" values; the phase list was half shipped | Rewritten: needs → designs, one clean matrix |
| **4a** | The "clone the Preferences editor" center relies on custom `bl_context="chat"`. The Preferences center filters panels by the **fixed** Preferences sections, and panels without a context show in *every* section, including the user's real Preferences. Section state is global | **Spike first (task A0)** before building on it; alternatives listed in 4a banner |
| **4a** | Editor catalog still says the Text Editor is the dedicated-editor target (GPU takeover) while Decisions say USERPREF | Resolve in A0 |
| **4f** | "Current state" tables predate 3k | Remaining 4f scope trimmed (lane F) |
| **4g** | Node tools list says "4 remaining" but names 5 | 5 tools (get_node_detail, create_node, connect_nodes, set_node_input_value, mute_node) |
| **4c** | Line references (`agent_controller.py:2764` etc.) are stale (file is now ~7,900 lines) | Re-anchor at implementation; no plan change |
| **Security** | MCP HTTP + bridge accept any local process | 4i.6 session token (before ACP ships) |
| **Markdown** | Blender PR #163254 (`layout.label_markdown`) merged to `main` 2026-09-11 → ships in 5.3. It has no tables, syntax highlighting, LaTeX or images | **Minimum version becomes 5.3**; task B0 switches the chat to the native API and fills those gaps; the hand-written renderer is removed |

---

## 3. Lanes and order

| Order | Lane | Plan | Why here | Can start |
|---|---|---|---|---|
| **1** | **H + I — External Agent (ACP) + engine parity** | `plan_tier4h_external_agent_acp.md`, `plan_tier4i_engine_parity.md` | User priority; largest UX gap; the seam every later UI lane builds on | Now (4h.0 spike) |
| **2** | **G — Domain tooling** (VSE, Text, Node, Save, review sheets) | `plan_tier4g_domain_tooling.md` (+ 4e.3 Save) | Independent of UI; immediately helps all three engines | Now, in parallel with lane 1 |
| **2b** | **S — Skills uplift, clean room** | `plan_tier4s_skills_cleanroom.md` | S0/S1 are live-confirmed bugs (templates broken in every mode; compositor read broken on 5.x) -- fix in 1.1.39, before the T0 baseline; S3–S5 feed F2 and B7 | Now |
| **3** | **B — Chat UX** (explain, ask_user, revert, sessions, context chip, run/fix) | `plan_tier4b_competitor_ux_analysis.md` | Visible wins; B2/B4 depend on 4i.1/4i.5 | B0/B1/B6 now; B2+ after 4i.1 |
| **4** | **F — Agent runtime** (permissions, doom-loop, on-demand skills, metrics) | `plan_tier4f_agent_intelligence.md` | Permissions feed the shared approval row; skills cut local tokens | After 4i.3 (F1); F2/F3 anytime |
| **5** | **C — Text Editor IDE** | `plan_tier4c_text_editor_ide_agent.md` | Needs 4g.2 text tools + B5 run/fix | After G2 |
| **6** | **A — Editors everywhere + Coworker editor + viewport overlay** | `plan_tier4a_editor_integration.md` | Needs 4i.2 components; A0 spike decides the editor approach | A0 spike anytime |
| **7** | **E — Rigging & animation tools** | `plan_tier4e_nice_to_haves.md` | Completes domain matrix (Save moved to lane G) | After G pattern settles |
| **8** | **Capstone — Agent teams, scene co-pilot, render critic** | 4f.4, 4g.6 | Needs approvals, bounded outputs, review sheets | Last |
| — | Moodboard | `plan_tier4d_moodboard_editor.md` → Tier 6 (`plan_tier6_moodboard_storyboarding.md`) M1 | Superseded in Tier 4 | — |

### 3.1 Dependency graph

```
4h.0 spike ─► 4i.1 seam ─┬─► 4h.2-4h.4 ACP engine ─► 4h.5 approvals ─► 4h.6 sessions ─► 4h.7/8
                         ├─► 4i.2 components ─► 4a lanes
                         ├─► 4i.3 approval model ─► 4f.3 permissions
                         └─► 4i.5 plan/ask/checkpoint/digest tools ─► 4b B2, B4
4i.6 token ─► (before ACP release)
4i.4 progressive tools ◄── 4g domains (each new domain registers here)
4g.2 text tools ─► 4c Text Editor ─► recipes
4g G5 review sheet ─► 4g.6 render critic
4i.7 bounded outputs ─► 4g node/asset reads, 4f.4 subagents
```

### 3.2 Milestones (releases)

| Milestone | Contents | Exit criteria |
|---|---|---|
| **M1 — "Bring your own agent" preview** | 4h.0–4h.5, 4i.1–4i.3, 4i.6, B0, **I8** (benchmarks on every engine), **H10** (External Harness activity view) | OpenCode + Claude Code chat in the panel on Windows, approvals inline, Stop works; External Harness calls visible live; Local/Remote unchanged (tests + benchmark) |
| **M2 — Same Coworker everywhere** | 4h.6–4h.8, 4i.4, 4i.5, B1, B2, B4, G1–G3 | Explain, ask_user, Revert and plan work on all three engines; surface-only first request measured |
| **M3 — Deeper tools** | G4–G6, 4i.7, B3, B6, F1–F3, C1–C4 | Recipes in the Text Editor; sessions browser; permissions; review sheets |
| **M4 — Everywhere in the UI** | A1–A5, B5, E1–E2 | Sidebars in all editors; Coworker editor per A0 decision; viewport status |
| **M5 — Capstone** | 4f.4, 4g.6.1/.2 | Planner → specialists → validator; co-pilot; critic loop |

### 3.3 User testing and benchmark gates

Tier 4 succeeds if, compared with the T0 baseline, every mode **looks better, feels
better, is more integrated, smarter, and more contextual to the editor you're in**.
Each gate measures all five, the first four by benchmark and all five by hand. The gate
list (T0–T6) and when to run it is in the task board.

**Benchmark setup (same every gate, so runs compare):**

| Mode | Configurations | How |
|---|---|---|
| Local | One small model at 16K, one medium model at 32K (keep the same two all tier) | Benchmark suites in Status & Diagnostics |
| Remote | One cheap model, one frontier model (OpenRouter) | Same |
| External Agent | OpenCode, Claude Code (from T2, needs I8) | Same suites through the engine seam |
| External Harness | Claude Desktop or OpenCode as MCP client | Manual (an external client can't be scripted from Blender); watch the activity view (H10, from T2) |

Record per run: pass/fail per step, wall time, prompt/completion tokens, iterations,
approvals asked. Save with Compare Benchmarks and keep the T0 file as the reference.
**Quick set** = the 3 quick challenges (`quick_sanity`, `quick_judgement`, `quick_2d`); **full** = the 6 core benchmarks (`scene`, `animation`, `modeling`, `nodes`, `assets`, `vision`) + the 3 quick challenges (#104).

**Rating sheet (by hand, per mode, 1–5):**

| Criterion | What to look for |
|---|---|
| Looks | Text, code and tables read cleanly; nothing clipped; theme matches |
| Feels | First text appears fast; Stop is instant; no frozen UI; approvals don't nag |
| Integrated | You never leave the panel/editor to finish the task; right-click and sidebars work where you are |
| Smart | Fewer retries and spirals, right tool first time, asks only when it matters |
| Contextual | Answers fit the editor and selection (VSE vs Shader vs Edit Mode) without you explaining |

**Artist scripts (5 minutes each, same wording every time):**

- **A (T2)**: in External Agent mode: "make a low-poly campfire on a ground plane". Approve
  one action, Stop once mid-turn, then continue. Repeat the prompt in External Harness
  and watch the activity view.
- **B (T3)**: right-click the Subdivision modifier's *Levels*, then Explain. Ask "give my
  campfire a material", answer its question by button, Revert the turn, check the scene.
  Run it once on each engine.
- **C (T5)**: open the Coworker editor (#26) in a new area; chat from the Node Editor, VSE
  and Text Editor sidebars; check the viewport status overlay.

---

## 4. Branching and workflow

- One branch per **task group** in the task board, named `feat/4<lane>-<slug>` (e.g.
  `feat/4h-acp-client`, `feat/4g-vse-tools`) or `spike/4<lane>-<slug>` for spikes.
  Branch from `main`, keep `main` green, squash-merge via PR (as #92–#95).
- Each PR: tests for the new pure-Python parts (bpy-free modules as in `acp_client.py`),
  a cost-ledger before/after for anything touching prompts/tools, a white-room line in
  the PR body, and a wiki/docs touch if user-facing.
- Each task group ends with its plan's banner updated (✅ + date) and moved to
  `Plans History/` with an Audit section once fully shipped, as with Tier 3.

---

## 5. Engine parity & token economy

Defined in `plan_tier4i_engine_parity.md`: the `AgentEvent`/`Engine` seam, one approval
model, server-side progressive tools, plan/ask/checkpoint/digest MCP tools, session
token, bounded outputs, and the token-economy checklist (4i §4) that applies to every
lane below.

---

## 6. Shared designs retained from v1 (condensed)

### 6.1 Next-step buttons (was "CHOYA")
Merged into **4b B2**: `ask_user` choices + the stop-rule "one suggested next step"
rendered as **[Do it] [Something else]**. The v1 static per-context option tables are
dropped. The agent proposes options that fit the moment, which is better than a fixed list.

### 6.2 Explain this / Translate
**4b B1**. Docs-grounded (must call `search_manual_docs`/`search_api_docs` and cite),
answer shape what → how → when → pitfalls → try this. Entry points: button context menu,
object, node, Outliner menus, `/explain` in Ask mode. Translate shares the operator
plumbing and adds a target-language preference.

### 6.3 Shared UI components
`ui_components.py` starts with the parity set in **4i.2** (tool row, reasoning, plan,
approval, question, engine chip). The full extraction of the chat into
`draw_chat_interface()` happens in **4a A1**. Brand detection (`_is_bfa`, `AGENT_ICON`)
moves to `shared.py` in the same PR (≈10 LOC).

### 6.4 Hotkey policy
No custom default hotkeys in Tier 4. Everything is reachable through sidebar panels,
context menus and header buttons. Optional user-assignable keymap entries can come in
Tier 5 on request.

### 6.5 Macros (design only; implementation Tier 5)
Data model `Macro{name, description, steps[MacroStep{tool_name, parameters,
description}], target_parameter, tags}`, stored in `bfa_coworker_macros.json`. Tier 4
requirement: tool calls in the event stream (4i.1) carry name + args + result status, so
a session can be turned into a macro later. **Recipes** (4g G2) cover the most common
case, "rerun this pipeline with new numbers", already in Tier 4.

### 6.6 Native markdown, minimum version 5.3 (Tier 4, task B0)
Blender's `layout.label_markdown(text=...)` (PR #163254, merged for 5.3) draws headers,
paragraphs, bold/italic/monospace, links, code blocks, inline code and lists, with
native theming and layout caching. With **Blender/Bforartists 5.3 as the minimum**
we use it everywhere and delete our own renderer, with no feature detection or fallback.
We keep three thin layers on top: (1) split replies at fenced code blocks so each block
keeps a Copy (later Run) row, (2) convert tables to an aligned monospace block, since the
native API has no tables, (3) keep the LaTeX→Unicode pre-pass. Moved from Tier 5 into Tier 4.

---

## 7. Review decisions

| # | Decision | Outcome (2026-10-16) |
|---|---|---|
| 1 | Promote ACP (4h) + parity (4i) to lane 1, domain tools (4g) in parallel | ✅ Accepted |
| 2 | Mode naming | ✅ **Keep "External Harness"** (MCP only, chat elsewhere); add **"External Agent"** (ACP, chat in Blender). No rename; plain-language **tooltips** explain both, since "harness" and "client" are both technical words an artist may not know |
| 3 | Default code-execution policy on External Agent | ✅ Allow, backed by the turn checkpoint + Revert (4h §7 Q2) |
| 4 | "Only Coworker tools" default on for ACP agents | ✅ Accepted |
| 5 | Session token on by default | ✅ Accepted. Copy MCP Config includes it; one-time notice for hand-written configs |
| 6 | Minimum version | ✅ **Blender/Bforartists 5.3**, native `label_markdown()` (§6.6, task B0) |
| 7 | 4a dedicated editor | ⏳ Decide after the A0 spike |
| 8 | Smart Save tools move from lane E to lane G | ⏳ Not answered yet. The board assumes yes (G4) |

---

## 8. Decision log (condensed from v1)

- 2026-09-01: Domain tooling first; moodboard → Tier 5; markdown draw deferred to native; Explain added; streaming, budget and checkpoints designed (all since shipped in Tier 3).
- 2026-09-14: Domain tooling plan renamed Tier 4g; Agent Teams → 4f.4; voice/TTS → Tier 5; Tier 4f added from the OpenCode comparison.
- 2026-10-01: Tier 3i/3j latency + tool discovery items deferred into 4f/4g.
- 2026-10-06: ACP research (4h draft).
- **2026-10-16: Full audit. ACP + new Tier 4i engine parity become lane 1; 4b rewritten; task board created; fishing case study and Scenario added to the comparison.**
- **2026-10-16 (review)**: "External Harness" name kept (tooltips instead of a rename); defaults for code execution, Coworker-only tools and session token accepted; minimum version 5.3 with native `label_markdown()` replacing our renderer (moved from Tier 5 to task B0); the Blender Buddy provenance item dropped.
- **2026-10-21: The four workflows (§0).** Coworker operators in native places (wand badge, no "Magic" brand), the Spell Book (shipped wizard spells, prompt spells for #300, your own; replay with no model), procedural first + Make Parametric, Sequencer pack first with a creative *Edit from Footage* wizard; plans in `plan_tier4m_coworker_operators.md`, ideas and the asset-library notes in `plan_tier5c_artist_automation.md`.
- **2026-10-21 (later): One thread per editor.** The orchestration layer of this release is threads: each editor's job runs in its own conversation, side by side, with claims and honest Revert (`plan_tier4o_orchestration_threads.md`, Threads 0–3). Footer 1 shows one icon per thread. Moved to Tier 5: Edit from Footage and the other big wizards, Spell 2–3, Procedural 2–3, Tools 9, Orchestrate 2, Teams 1–3, Security 5 (§0.4).
- **2026-10-21 (re-tier against feature creep).** Threads stay in Tier 4. The Spell Book (with #300 prompt spells and *Save as Spell*) and the wizard spells go to Tier 5; generative local systems, generative 2D and moodboard/storyboarding go to Tier 6 next to generative 3D and the diffusion renderer (files renamed `plan_tier6_*`). Rule: a new idea goes to the lowest tier whose foundation it needs (§0.1).
- **2026-10-20: Tier 4a review.** Lane A (editors) is nearly closed: the Coworker editor, the compact chat in every editor's sidebar with *Use editor context only* (#298), Explain This (#290) and Translate (#42) in the right-click menus, permissions Strict / Permissive / Auto (#292). **Dropped:** viewport agent feedback (Editors 5), less in your face. **Moodboard** stays in Tier 5. Contextual, domain-aware operators per editor come with lanes F / G. The remaining Tier 4 tasks are logged one issue per task: #299-#324 (see the task board).
