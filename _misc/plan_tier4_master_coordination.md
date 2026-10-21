# BFA Coworker — Tier 4 Master Plan

**Revised**: 2026-10-20 (§0: release goals, strategy, cut line, audit against `main` @ 2a5cb56). Earlier full audit: 2026-10-16 against v1.1.37 @ deb417b
**Status**: 🚧 In progress. Engines/agents half done (Test 0–2 passed); §0 plans the rest of the release
**Previous version**: `Plans History/plan_tier4_master_coordination_v1.md` (historical
decisions, detailed streaming/checkpoint designs that have since shipped)
**Task board**: `plan_tier4_task_board.md`

---

## 0. Next major release — goals, strategy, cut line (audit 2026-10-20)

**Where we are.** The engine and agent half of Tier 4 is done: three engines behind one
seam, External Agent over ACP, shared plan/questions/Revert/short results/tools on
demand, the GPU Coworker editor + compact 3D View sidebar, history, Auto mode,
permissions. Test gates 0–2 passed. What's left is the **"where you work"** half (the
editors), the **tools** half (domain + orchestration) and **security**.

### 0.1 The goals and what each still needs

| # | Goal | Status | Release tasks (board) |
|---|---|---|---|
| 1 | Refinement | ✅ | Keep fixing from the test list |
| 2 | Dedicated global multi-domain chat editor | ✅ | — |
| 3 | CHOYA guided buttons + prompt injection while talking | ✅ | Wizards via GPU 6 + the wizard operators (Wand 4–5) |
| 4 | Text Editor as artist→developer IDE (#52) | ☐ | Tools 2 → Text Editor 1–4 |
| 5 | Translate this (#42) | 🟡 in progress (another agent) | Chat 3 |
| 6 | Explain this, every context menu (#290) | 🟡 in progress (another agent) | Chat 2 |
| 7 | Sidebar chat in all editors (#291) | 🟡 in progress (another agent) | Sidebar 1 |
| 8 | Footer integration of agent status | ☐ | Footer 1 |
| 9 | Orchestration layer + expanded domain tools (#71) | ☐ | Tools 1–3, 5, 7–9; Orchestrate 1–2 |
| 10 | Quick tools in every editor → **Coworker operators** (wand icon, native places) | ☐ for review | Wand 0–3, 6 (+ 4–5 stretch, 7 with Text Editor) — `plan_tier4m_coworker_operators.md` |
| 11 | Security checks | 🟡 | Security 1–5 |

### 0.2 Strategy

1. **Operators, not canned prompts.** Day-to-day chores become ordinary-looking
   operators — conventional names and tooltips, a **wand badge** on the icon — placed in
   the menus and Bforartists toolshelf tabs where related native operators live (context
   menus only where that is the native place: Explain, Translate, Properties). Mostly
   tested code; the **active engine** answers only the judgment calls; preview and one
   undo. Users can record one from a good chat turn or write one in the Text Editor's
   **Make Operator** panel, the only dedicated panel (`plan_tier4m_coworker_operators.md`).
2. **Tool first, model optional.** Day-to-day chores (organize, rename, place, fix,
   frame) are deterministic tools with a dry run and one undo, usable without a model.
   The model chooses and chains them. This is what makes 7–14B local models reliable
   and gives the release value even with no engine running. Catalogue:
   `plan_tier5c_artist_automation.md`.
3. **Context comes from where you click.** Sidebar 1 + Chat 4 pass the editor, mode and
   selection, so the same words do the right thing in the VSE, the Node editor or the
   Text Editor.
4. **Security before reach.** Security 1–4 land before the quick menu and sidebars put
   the agent one click away from every editor.

### 0.3 Order (critical path)

```
Security 1 ─► Security 2–4
Chat 4 (context) ─► Sidebar 1 ─► Footer 1
Wand 0 spike + icons ─► Wand 1 framework ─► Wand 2 Sequencer ─► Wand 3 Nodes ─► Wand 6 recorder
Tools 1 ─► Wand 2 ; Tools 3 ─► Wand 3
Tools 7 + 8 + 9 ─► organize / fix operators ─► Orchestrate 1 ─► Orchestrate 2
Text Editor 1–2 ─► Wand 7
Tools 2 ─► Text Editor 1–4
Tools 1, 3, 5, 9 in parallel (one domain per PR)
```

Suggested batches for my lanes (each ends in a test round; the 🟡 lanes run in
parallel with the other agent): **A** Security 1–2, Wand 0, Tools 1, Footer 1 ·
**B** Wand 1, Wand 2 (Sequencer), Tools 3, Tools 7 · **C** Wand 3 (Nodes), Tools 8–9,
Orchestrate 1 · **D** Wand 6, Tools 2 + Text Editor 1–4, Wand 7, Orchestrate 2,
Security 3–5 · stretch Wand 4–5 → **Test 3 + Test 5** → release.

### 0.4 Cut line

**In the release**: the table above. **Moved to Tier 5**: agent teams, co-pilot and
critic (Teams 1–3), recipes/macros and batch across files, export/UV/LOD pipeline,
further wizards, Rig 1–2 (unless Tools 1–3 finish early), Editors 6 (floating window),
Runtime 3, migrating the wand icon set into Bforartists. Ideas for those: `plan_tier5c_artist_automation.md`.

### 0.5 Audit corrections (2026-10-20)

| Finding | Action |
|---|---|
| Board showed Chat 5 open | Done (#168 #278 #288) |
| Board showed Chat 6 open | Revert done (#170); Edit & resend / Retry remain |
| Board showed Runtime 1 open | Mostly done (#189 #289 #292); path guard + External repeat guard → Security 3 |
| #219 (placement tools) closed as completed | Only a guidance rule shipped (#257); no tools → **Tools 7** |
| Chat 1 (native markdown) marked unblocked | Still 0 uses of `label_markdown` in the add-on; open |
| Editors 5 (viewport dot) | Replaced by **Footer 1** (status bar) |
| Text Editor 5 (prompt templates) | Folded into **Wand 7** (Make Operator panel) |
| Quick prompt menu (canned prompts) | Replaced by **Coworker operators** in native places (4m §1: prompts alone are slow, inconsistent on small models, no preview/undo); no "Magic" branding, the wand badge marks them |
| §3 lanes table below predates the engine work | Kept for history; the release order is §0.3 |

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
| — | Moodboard | `plan_tier4d_moodboard_editor.md` → Tier 5 M1 | Superseded in Tier 4 | — |

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
