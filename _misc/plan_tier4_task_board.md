# Tier 4 — Task Board

**Created**: 2026-10-16 · **Source of truth for order**: `plan_tier4_master_coordination.md` §3
**Status**: ⏸ Waiting for plan review. Nothing is branched yet.
**Review decisions applied (2026-10-16)**: ACP + parity is lane 1 · "External Harness" keeps its name (tooltips explain it), "External Agent" is added · code runs without asking on External Agent, backed by a turn checkpoint and Revert · "Only Coworker tools" on by default · session token on by default · **minimum Blender/Bforartists 5.3**, chat drawn with native `label_markdown()` (B0).

**How to use**: one branch per row-group (the **Branch** column). Branch from `main` and
open a PR when the row's *Done when* holds. Tick the box and add the PR number. Sizes:
**S** ≤ 2 days · **M** ≤ 1 week · **L** > 1 week. IDs match the plan documents.

Every PR: tests for bpy-free code · cost-ledger before/after if prompts or tools change ·
white-room line in the PR body · skill/tool text claims backed by a probe (S3) · wiki/docs if user-facing. A PR that touches the turn
loop, prompts or tools also runs the **quick benchmark set** (`baseline`, `scene_build`,
`error_handling`) on Local.

---

## Test gates (user testing + benchmarks)

Defined in master §3.3. Tick a gate when its runs are saved and its rating sheet is filled.

| ☐ | Gate | When | Benchmarks (modes) | You test by hand |
|---|---|---|---|---|
| ☐ | **T0 Baseline** | **Now, before the first branch** (after S0 + S1, so the baseline doesn't measure two broken tools), on Blender/Bforartists 5.3 | All 15 suites: Local (small + medium model), Remote (cheap + frontier) | External Harness smoke with Claude Desktop or OpenCode (harness guide); fill the rating sheet for every mode |
| ☐ | **T1 Seam regression** | After I1 + B0 merge | Full suites Local + Remote; must match T0 (± noise) | Chat looks right with native markdown (tables, code Copy rows) |
| ☐ | **T2 = M1 "Bring your own agent"** | After H5 + I6 + I8 + H10 | Full suites on **External Agent** (OpenCode, Claude Code) for the first time; Local/Remote quick set | Artist script A (§3.3); External Harness via the new activity view |
| ☐ | **T3 = M2 "Same Coworker everywhere"** | After I4 + I5 + B1/B2/B4 + G1–G3 | All suites, all engines; first-request tokens with progressive tools vs T0 | Artist script B: explain, ask_user, Revert, plan on each engine |
| ☐ | **T4 = M3 "Deeper tools"** | After G4–G6, B3, F1–F3, C1–C4 | Domain suites (sequencer, shader/geometry nodes, image, compositor + new text/VSE steps), all engines | Recipes in the Text Editor; sessions browser; Safe mode |
| ☐ | **T5 = M4 "Everywhere in the UI"** | After A1–A4 | Quick set, all engines (regression only) | Artist script C: Coworker editor (#26) + sidebars in five editors |
| ☐ | **T6 = M5 Capstone** | After X1–X3 | Full suites + a new multi-step "campfire" suite, all engines | Agent teams, co-pilot, critic |

---

## Lane H + I — External Agent (ACP) + Engine Parity  · *priority*

Plans: `plan_tier4h_external_agent_acp.md`, `plan_tier4i_engine_parity.md`

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | H0 | Spike: drive `opencode acp` + Claude ACP adapter from a Python script on Windows; record fixtures; confirm stdio `mcpServers` → `blmcp`; verify preset commands; check whether Freebuff speaks ACP | `spike/4h-acp-transcripts` | — | S | `tests/fixtures/acp/*.jsonl` committed; 4h §3/§5.2 corrected |
| ☐ | I1 | `AgentEvent` + `Engine` seam; wrap Local/Remote with no behaviour change; event drain on main thread; fake engine for UI tests | `feat/4i-engine-seam` | H0 (event shape) | M | Full test suite + benchmark green; turn rendering reads events only |
| ☐ | I2 | `ui_components.py` parity set (tool row, reasoning, plan, approval, question, engine chip) + brand detection → `shared.py` | `feat/4i-engine-seam` | I1 | S | No engine branches in turn rendering |
| ☐ | H2 | `acp_client.py` (stdlib JSON-RPC over stdio, reader thread, cancel, crash handling) + `tests/fake_acp_agent.py` | `feat/4h-acp-client` | H0, I1 | M | Fake-agent conversation incl. cancel, permission, crash passes |
| ☐ | H3 | Agent presets + detection + install/login hints + Generic preset; Windows `.cmd` shim handling | `feat/4h-acp-client` | H2 | S | Correct status per preset on a machine without the agent |
| ☐ | H4 | `ExternalAgentEngine`: prompt (text, images, context chip), streaming, Workshop rows (dedupe vs bridge), plan updates, Stop, Queue, engine chip; mode selector adds **External Agent** next to **External Harness** (names kept). Plain-language tooltips: *External Agent: chat here, your own Claude/ChatGPT/Gemini/OpenCode does the work*; *External Harness: chat in another app, it uses Blender's tools* | `feat/4h-acp-engine` | H2, I2 | M | 4h §1 done-when with OpenCode, then Claude Code |
| ☐ | I3 | `ApprovalRequest` model + inline row; "always" per session | `feat/4h-acp-engine` | I2 | S | ACP permission answered inline; timeout → reject |
| ☐ | H5 | Wire ACP `session/request_permission` → I3 | `feat/4h-acp-engine` | H4, I3 | S | Allow once/always/deny each behave correctly |
| ☐ | I6 | MCP + bridge session token; included in Copy MCP Config; passed to ACP via env | `feat/4i-session-token` | — | S | Tokenless request refused; all engines + config copy work |
| ☐ | H6 | Project cwd + `.coworker/AGENTS.md` from `prompts_compact.yml`; "Only Coworker tools"; `session/load` or resume brief; session index entry | `feat/4h-acp-sessions` | H4 | S | Restart Blender → continue the same work |
| ☐ | I5 | MCP tools `update_plan`, `ask_user` (deferred), `scene_checkpoint` + turn-start auto checkpoint, `get_scene_digest(since_hash)`; switch local loop's plan to `update_plan` | `feat/4i-shared-tools` | I1 | M | ACP agent updates plan, asks with buttons; digest returns `unchanged` on repeat |
| ☐ | I4 | Server-side progressive tools: surface listing, `load_tools` as MCP tool + `tools/list_changed`, fallback to full list; local loop uses it | `feat/4i-progressive-tools` | I5 | M | ACP first request = surface schemas only (measured); VSE loads on demand |
| ☐ | H7 | Hardening: kill agent mid-turn, kill Blender, bad command, no login, stderr surfacing, process_guard | `feat/4h-acp-hardening` | H4–H6 | S | Adversarial checklist passes |
| ☐ | H8 | Polish: mode/model selectors, slash commands, maturity badges; wiki page + `harness_testing_guide.md` update | `feat/4h-acp-polish` | H7 | S | Docs reviewed |
| ☐ | I8 | **Benchmarks through the engine seam**: the test-suite runner sends steps via `Engine.send` instead of `run_conversation_turn`, so the same 15 suites run on Local, Remote and External Agent. Each result records mode, engine, model, pass/fail, wall time, prompt/completion tokens (or "not reported"), iterations, approvals asked. Compare Benchmarks groups by mode | `feat/4i-benchmarks-all-modes` | I1, H4 | S | One suite runs on all three engines; Compare Benchmarks shows them side by side |
| ☐ | H10 | **External Harness activity view**: a read-only live Workshop for External Harness mode, fed by the bridge (tool name, short args, duration, ok/error, result preview, screenshot thumbnails), plus "Copy session log" and an "Open log" shortcut. This is the debug view for external clients | `feat/4h-harness-activity` | I1 | S | Claude Desktop/OpenCode calls appear live in the Coworker panel; a failing call shows its error |
| ☐ | I7 | Bounded tool outputs + `read_tool_output` in the server (from 4f.2.1) | `feat/4i-bounded-outputs` | — | S | 50 KB node dump → ≤ 2 KB preview + handle on every engine |
| ☐ | H9 | *(only if needed)* Claude headless / OpenCode serve fallback | `feat/4h-fallbacks` | H7 | M | Only if a key agent's ACP is unusable |

---

## Lane G — Domain Tooling (parallel with lane 1)

Plan: `plan_tier4g_domain_tooling.md` (+ 4e.3). Each task includes its domain chapter in
`prompts.yml` / skills (4g.5) and registers its domain for progressive loading (I4).

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | G1 | VSE tools (5): strips summary, strip detail, timeline overview, set frame range, render preview (deferred) + `vse` domain chapter + screenshot JSON enrichment | `feat/4g-vse-tools` | — | M | Tool listing shows 5; "move the title strip to 50" works on Local |
| ☐ | G2 | Text tools (5): list, read range, set range, search, run (stdout/stderr) + **recipe convention** (parameter block at top of a text block; skill text explaining "edit numbers, rerun") | `feat/4g-text-tools` | — | M | Agent edits lines 40–45 of a script and reruns it; recipe rerun with a changed number |
| ☐ | G3 | Node tools (5): get_node_detail, create_node, connect_nodes (socket fuzzy match, returns available names on miss), set_node_input_value, mute_node | `feat/4g-node-tools` | — | M | Noise → Roughness flow from 4g plan works on Local |
| ☐ | G4 | Smart save tools (5, from 4e.3): save, check unsaved, pack, export selection, incremental save. File-writing ones use prepare/apply (I3) | `feat/4g-save-tools` | I3 for approvals (ship read-only `check_unsaved_changes` first) | M | Export asks for approval; incremental save versions correctly |
| ☐ | G5 | `render_review_sheet(objects?, views[front,back,below,camera,45° steps], size)`: one tiled image for vision review | `feat/4g-review-sheet` | — | S | One image with labelled views; tokens ≈ one image |
| ☐ | G6 | Long jobs: document deferred-tool limits; background-process option for long bakes/renders with progress | `feat/4g-long-jobs` | — | S | A 10-minute bake does not time out the agent |

---

## Lane B — Chat UX

Plan: `plan_tier4b_competitor_ux_analysis.md`

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | B0 | **Native markdown + Blender 5.3 minimum**: set `blender_version_min = "5.3.0"`. Replace `_render_markdown` with `layout.label_markdown(text=...)` (PR #163254: headers, paragraphs, bold/italic/monospace, links, code blocks, inline code, lists). Fill the gaps the native API leaves: split replies at fenced code blocks so each block keeps its Copy (later Run) row; convert tables to an aligned monospace block (no native tables); keep the LaTeX→Unicode pre-pass. Delete the old renderer and the `_can_multiline()` / pre-5.3 fallbacks. Update README, wiki, extension listing | `feat/4b-native-markdown` | — | M | Chat, Workshop and Explain answers draw through `label_markdown` on Blender 5.3 + Bforartists 5.3; old renderer removed; tables readable; unit tests for the segmenter/table converter |
| ☐ | B7 | Doc-drift records (`sources`, `reviewed_revision`) for `skills/blender_5x.md` and the wiki pages Tier 4 touches, with a small checker in `_misc/`. Review whether the pre-5.3 version skills are still needed with the 5.3 minimum. Use S3 probes as the drift signal for API claims | `chore/4b-doc-drift` | B0, S3 | S | Checker lists drifted docs; version skills trimmed or justified; API drift shows as a failing probe |
| ☐ | B1 | Explain this + Translate: context-menu operators, target card, docs-grounded Ask turn, `/explain` | `feat/4b-explain` | — | M | Property, node, object each give a cited answer on all engines |
| ☐ | B6 | Live context chip (editor · mode · selection), toggleable | `feat/4b-context-chip` | — | S | Same prompt in two editors picks different correct tools |
| ☐ | B2 | `ask_user` button row + next-step [Do it] [Something else] | `feat/4b-ask-user` | I5 | S | Click resumes the same turn on all engines |
| ☐ | B4 | Revert turn (history + scene checkpoint), Edit & resend, Retry | `feat/4b-message-actions` | I5 (checkpoint) | M | Revert restores chat+scene (Local), scene+note (External) |
| ☐ | B3 | Session browser (search, rename, delete, pin, engine chip), resume brief, optional project plan file | `feat/4b-sessions` | H6 for ACP part | M | Yesterday's session continues on each engine |
| ☐ | B5 | Run / safety scan / undo / Fix for code blocks (Ask mode) reusing server preflight | `feat/4b-run-fix` | — | M | Broken snippet → Run → Fix → Run succeeds |

---

## Lane F — Agent Runtime (remaining 4f)

Plan: `plan_tier4f_agent_intelligence.md` (see its audit banner for what already shipped)

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | F1 | Permissions (Trusted / Safe / Strict, glob rules, external-directory guard) + doom-loop guard (3× identical call, any tool) → shared approval row | `feat/4f-permissions` | I3 | M | Safe mode asks before `execute_blender_code`; 3× repeat escalates |
| ☐ | F2 | On-demand skills: name+description index in the prompt, `skill` tool (server-side so External benefits), keep version skills always-on | `feat/4f-skills-on-demand` | S4; I4 helpful | S | System prompt shrinks measurably (first-request tokens on Local 16K, before/after); skill loads when needed; External Harness gets the `skill` tool |
| ☐ | F3 | llama-server `--metrics`, `--parallel` readiness, hardware auto-tune of KV type/layers | `feat/4f-inference-tuning` | — | M | `/metrics` tok/s in Status; benchmark shows no regression |

---

## Lane C — Text Editor IDE

Plan: `plan_tier4c_text_editor_ide_agent.md`

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | C1 | Text Editor sidebar becomes a code control panel (active text, cursor, Generate/Fix/Explain/Edit, templates) | `feat/4c-text-panel` | G2, I2 | S | Panel shows file info; actions present |
| ☐ | C2 | Generate at cursor / Edit selection, streaming via events into the text block, single undo step | `feat/4c-generate-edit` | C1 | M | Selection rewritten in place; Ctrl+Z restores |
| ☐ | C3 | Run → error → Fix in the Text Editor (shares B5 code) | `feat/4c-run-fix` | B5 | S | Error jumps to line; Fix applies a patch to the range |
| ☐ | C4 | Recipes UI: detect parameter block, "Rerun with…" fields in the sidebar | `feat/4c-recipes` | G2 | S | Change a number in the sidebar → script reruns |
| ☐ | C5 | Prompt templates (file-based, per editor) | `feat/4c-templates` | C1 | S | User template appears and works |

---

## Lane A — Editors Everywhere

Plan: `plan_tier4a_editor_integration.md`

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | A0 | **Spike for issue #26 (Coworker chat editor)**: build three throwaway prototypes and pick one. (1) **Custom `NodeTree` type "Coworker"**: gets its own entry + icon in the Editor Type menu, can be placed in any area; chat in the sidebar, center canvas free for status/plan (and the Tier 5 moodboard). (2) **Text Editor host** (the issue's idea): chat in the sidebar; center shows a live transcript text block or stays empty; no own menu entry. (3) **Preferences-editor center panels**: check for leaking into the real Preferences and for the global section state. Criteria: own menu entry, native text input, usable center, native theme, no leaks, Blender + Bforartists 5.3 | `spike/4a-coworker-editor` | — | S | Decision + screenshots in the 4a banner, posted on #26 |
| ☐ | A1 | `draw_chat_interface(layout, ctx, editor_type)` extraction | `feat/4a-chat-component` | I2 | M | Existing panels delegate; no visual change |
| ☐ | A2 | Sidebar chat in all editor types (factory) + per-editor templates | `feat/4a-sidebars` | A1 | S | Chat in Node, VSE, Image, Graph… sidebars |
| ☐ | A3 | Coworker editor per A0 decision + setup operator | `feat/4a-coworker-editor` | A0, A1 | M | One click turns an area into the Coworker editor |
| ☐ | A4 | Viewport status dot + agent focus highlight (theme colours, toggle) | `feat/4a-viewport-overlay` | I1 | S | Overlay follows agent state; off by default? (decide) |
| ☐ | A5 | *(optional)* Floating chat window | `feat/4a-floating-window` | A3 | S | Window opens once; second click focuses it |

---

## Lane E — Rigging & Animation Tools

Plan: `plan_tier4e_nice_to_haves.md` (Save moved to G4)

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | E1 | Rigging (6): armature, bone, constraint, IK chain, mirror pose, bake | `feat/4e-rigging-tools` | G pattern | M | "Rig this 3-bone arm with IK" works on Local |
| ☐ | E2 | Animation (4 new; `batch_keyframe_insert` exists): interpolation, F-curve modifier, NLA track, bake to NLA | `feat/4e-animation-tools` | G pattern | M | Bounce with ease-out + noise modifier via tools only |

---

## Lane S — Skills uplift, clean room · *S0/S1 in 1.1.39*

Plan: `plan_tier4s_skills_cleanroom.md` (method §2, needs list §3, notes on other tasks §5).
Every S task follows the two-role clean-room method: implementer works from first sources only.

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | S0 | **Bug**: template registry moved into the server (`blmcp`); `execute_blender_plan` / `list_blender_templates` work in every mode (today they fail everywhere: the MCP process imports the add-on package). Hide both from the surface list until fixed | `fix/4s-templates-server-side` | — | S | Both tools work on Local and External Harness (live); bpy-free test imports the registry without the add-on |
| ☐ | S1 | **Bug**: `get_active_node_tree` uses `scene.compositing_node_group` (5.x) and never edits the scene; drop `use_nodes = True` advice (skills, Poly Haven world) | `fix/4s-node-tree-5x` | — | S | Compositor read works on 5.3; a read on a scene without a compositor changes nothing |
| ☑ | S2 | Needs list written by the reader role (plan §3) | — | — | S | Done in the plan |
| ☐ | S3 | API-claims probes: `_misc/api_probes.py` (live/headless) + `tests/test_api_claims.py` (every skill claim has a probe) | `feat/4s-api-probes` | — | S | Probes pass on 5.3; a removed property makes a probe fail |
| ☐ | S4 | Skill format + `_misc/check_skills.py`: front matter (`name`, `description` ≤ 400 chars, `sources`, `reviewed_revision`), body budget, on-demand `references/` | `feat/4s-skill-format` | S3 | S | Checker green on all skills; F2 can build its index |
| ☐ | S5 | New chapters from first sources: 5.x changes list, data-over-operators + context, freed data, evaluated geometry, localized node lookup, compositor / bone collections / render method / output format / background GPU / bulk mesh access; "finish with evidence" rules | `feat/4s-skill-chapters` | S3, S4 | M | Each claim has a probe; overlap check reports incidental only; quick benchmark set no worse |
| ☐ | S6 | `check_mesh_quality(objects)` tool: overlapping faces, loose parts, open edges, flipped normals, unapplied scale; compact JSON | `feat/4s-mesh-quality` | — | S | Detects each seeded fault in a test scene; reused by X2 |
| ☐ | S7 | Docs: tool-name stability + using other skill packs with External Harness | `docs/4s-skill-packs` | — | S | Wiki page reviewed |

---

## Capstone

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | X1 | Agent teams: Task tool, specialists, validator, one undo (4f.4) | `feat/4f-agent-teams` | F1, I7, I5 | L | Campfire scene via plan → specialists → validator |
| ☐ | X2 | Scene co-pilot (4g.6.1) | `feat/4g-scene-copilot` | X1 detectors | M | Issues listed with safe one-click fixes |
| ☐ | X3 | Render critic loop (4g.6.2) using review sheets | `feat/4g-render-critic` | G5, X1 | M | Score improves over ≤ 5 iterations or stops |
