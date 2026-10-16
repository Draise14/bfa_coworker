# Tier 4 — Task Board

**Created**: 2026-10-16 · **Source of truth for order**: `plan_tier4_master_coordination.md` §3
**Status**: ⏸ Waiting for plan review. Nothing is branched yet.

**How to use**: one branch per row-group (the **Branch** column). Branch from `main` and
open a PR when the row's *Done when* holds. Tick the box and add the PR number. Sizes:
**S** ≤ 2 days · **M** ≤ 1 week · **L** > 1 week. IDs match the plan documents.

Every PR: tests for bpy-free code · cost-ledger before/after if prompts or tools change ·
white-room line in the PR body · wiki/docs if user-facing.

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
| ☐ | H4 | `ExternalAgentEngine`: prompt (text, images, context chip), streaming, Workshop rows (dedupe vs bridge), plan updates, Stop, Queue, engine chip; mode selector entries (rename to External Client) | `feat/4h-acp-engine` | H2, I2 | M | 4h §1 done-when with OpenCode, then Claude Code |
| ☐ | I3 | `ApprovalRequest` model + inline row; "always" per session | `feat/4h-acp-engine` | I2 | S | ACP permission answered inline; timeout → reject |
| ☐ | H5 | Wire ACP `session/request_permission` → I3 | `feat/4h-acp-engine` | H4, I3 | S | Allow once/always/deny each behave correctly |
| ☐ | I6 | MCP + bridge session token; included in Copy MCP Config; passed to ACP via env | `feat/4i-session-token` | — | S | Tokenless request refused; all engines + config copy work |
| ☐ | H6 | Project cwd + `.coworker/AGENTS.md` from `prompts_compact.yml`; "Only Coworker tools"; `session/load` or resume brief; session index entry | `feat/4h-acp-sessions` | H4 | S | Restart Blender → continue the same work |
| ☐ | I5 | MCP tools `update_plan`, `ask_user` (deferred), `scene_checkpoint` + turn-start auto checkpoint, `get_scene_digest(since_hash)`; switch local loop's plan to `update_plan` | `feat/4i-shared-tools` | I1 | M | ACP agent updates plan, asks with buttons; digest returns `unchanged` on repeat |
| ☐ | I4 | Server-side progressive tools: surface listing, `load_tools` as MCP tool + `tools/list_changed`, fallback to full list; local loop uses it | `feat/4i-progressive-tools` | I5 | M | ACP first request = surface schemas only (measured); VSE loads on demand |
| ☐ | H7 | Hardening: kill agent mid-turn, kill Blender, bad command, no login, stderr surfacing, process_guard | `feat/4h-acp-hardening` | H4–H6 | S | Adversarial checklist passes |
| ☐ | H8 | Polish: mode/model selectors, slash commands, maturity badges; wiki page + `harness_testing_guide.md` update | `feat/4h-acp-polish` | H7 | S | Docs reviewed |
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
| ☐ | B0 | Provenance: Blender Buddy licence check for the markdown renderer → attribution header or rewrite; reword 1.1.37 note. Plus doc-drift records (`sources`, `reviewed_revision`) for `skills/blender_5x.md` and touched wiki pages, with a small checker script in `_misc/` | `chore/4b-provenance` | — | S | Header/notice in place or module rewritten; checker lists drifted docs |
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
| ☐ | F2 | On-demand skills: name+description index in the prompt, `skill` tool (server-side so External benefits), keep version skills always-on | `feat/4f-skills-on-demand` | I4 helpful | S | System prompt shrinks measurably; skill loads when needed |
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
| ☐ | A0 | **Spike**: can a Python panel own the Preferences-editor center *without* appearing in the user's real Preferences (fixed sections, global section state)? Compare: USERPREF + area-scoped poll · Properties editor · wide sidebar in an Image/Text editor | `spike/4a-coworker-editor` | — | S | Written decision with screenshots in the 4a banner |
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

## Capstone

| ☐ | ID | Task | Branch | Depends | Size | Done when |
|---|---|---|---|---|---|---|
| ☐ | X1 | Agent teams: Task tool, specialists, validator, one undo (4f.4) | `feat/4f-agent-teams` | F1, I7, I5 | L | Campfire scene via plan → specialists → validator |
| ☐ | X2 | Scene co-pilot (4g.6.1) | `feat/4g-scene-copilot` | X1 detectors | M | Issues listed with safe one-click fixes |
| ☐ | X3 | Render critic loop (4g.6.2) using review sheets | `feat/4g-render-critic` | G5, X1 | M | Score improves over ≤ 5 iterations or stops |
