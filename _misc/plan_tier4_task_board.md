# Tier 4 — Task Board

Tracking issue **#97** · Order and reasons: `plan_tier4_master_coordination.md` §3.

**How to read this board.** Tasks are named *group + step number* ("Engine 2", "Chat 3").
The small **Ref** column holds the short code the plan documents use (e.g. `I1b`), so
you can look a task up there. Each PR gets its own easy-to-read tracking issue, and its
first line says **"Merge: anytime"** or **"Merge after #N"**.

**Decisions in force (2026-10-16):** ACP + one engine for all modes come first ·
"External Harness" keeps its name and a tooltip explains it; "External Agent" is added ·
code runs without asking on External Agent, backed by a turn checkpoint and Revert ·
"Only Coworker tools" on by default · session token on by default · minimum
Blender/Bforartists 5.3, chat drawn with Blender's own `label_markdown()`.

**Every PR:** tests for bpy-free code · token cost before/after if prompts or tools
change · a white-room line · skill/tool claims backed by a probe · docs if user-facing ·
the 3 quick challenges on Local if it touches the turn loop, prompts or tools. Branch
from `main`, no stacked PRs. Only one open PR at a time on `agent_controller.py` /
`ui_chat.py`. Sizes: **S** ≤ 2 days · **M** ≤ 1 week · **L** more.

---

## Release goals — the next major release (audit 2026-10-20)

Your Tier 4 goals, checked against `main` (2a5cb56). This is the cut line for the
release; anything not listed here waits for Tier 5. Strategy and order: master §0.
**Legend**: ✅ done · 🟡 in progress (you + another agent; not re-planned here) ·
◐ partly done · ☐ not started. Coworker operators, tools, orchestration, footer and
security are written up for review in `plan_tier4m_coworker_operators.md`.

| Goal | Status | Tasks left for the release |
|---|---|---|
| Refinement | ✅ | Polish / test rounds done (#203–#286); keep fixing from your test list |
| Dedicated global multi-domain chat editor | ✅ | — (GPU 4, Chat 7) |
| CHOYA guided buttons + prompt injection while talking | ✅ | Chat 5 done (#168 #278 #288); Queue + Plan mode in place. Wizards = GPU 6 + Wand 4–5 |
| Text Editor as an artist→developer IDE (#52) | ☐ | Tools 2 → Text Editor 1–4 + Wand 7 (Text Editor 5 folds into Wand 7) |
| Translate this (#42) | 🟡 in progress | Chat 3 |
| Explain this, in every context menu (#290) | 🟡 in progress | Chat 2 |
| Contextual sidebar chat in all editors (#291) | 🟡 in progress | Sidebar 1 |
| Footer integration of agent status | ☐ | Footer 1 (status bar; replaces Editors 5) |
| Orchestration layer (#71) + expanded domain tools | ☐ | Tools 1–3, 5, **7–9** (new); Orchestrate 1–2 (new). Teams 1–3 → Tier 5 |
| Quick tools in every editor → **Coworker operators** (wand icon, native places) | ☐ for review | Wand 0–3, 6 (release; Sequencer pack first); Wand 4–5 (stretch); Wand 7 with the Text Editor lane |
| Security checks | 🟡 token + permissions done | Security 1–5 (new) |

---

## Where we are

| Status | Task | PR |
|---|---|---|
| ✅ Done | Green test baseline + kickoff | #108 |
| ✅ Done | Skills 1 + 2: template tools work everywhere; node tools read the 5.x compositor | #109 (#110) |
| ✅ Done | Agent 1: real ACP sessions recorded with OpenCode | #111 |
| ✅ Done | Engine 1: the engine's building blocks | #112 |
| ✅ Done | Engine 2: Local & Remote run through the engine | #114 |
| ✅ Done | Engine 3: events become chat messages | #120 (issue #119) |
| ✅ Done | Agent 2: ACP client (checked live with OpenCode) | #118 |
| ✅ Done | Agent 3: agent presets and detection | #131 |
| ✅ Done | Engine 8: session token | #124 |
| ✅ Done | Editors 1 + GPU 0–5: the Coworker editor and the compact sidebar chat (1.1.40) | #122 #126 #129 #133 #135 #137 #138 |
| ✅ Done | Agent 4: External Agent in the chat: pick, install / update / remove any agent; images, Queue, the agent's plan in Goal & Plan | #140 #142 #144 #146 (issue #139) |
| ✅ Done | Agents 5–7: approvals in the chat, resume after a restart, hardening (crash, login, Stop) | #150 #152 #154 |
| ✅ Done | Agents 8–9: mode / model / effort pickers, slash commands, the External Agent wiki page; the External Harness activity view | #158 #173 #175 #176 |
| ✅ Done | Engines 4, 6, 7, 9, 10: who answered; plan, questions, scene digest and Revert turn as tools; tools on demand (~8K tokens saved per request); short tool results; benchmarks on every engine | #167 #168 #170 #172 #174 #179 |
| ⏭ Now | **You test it**: `_misc/manual_test_tier4_agents_engines.md` (sections A-N; M = the feedback round) (round 3 at the top: "What's left") | |
| ✅ Done | GUI hardening: Workshop Show all, Workshop under Thinking + fold button, panel order + Plan panel, compact chat in a short sidebar | #183 #184 #185 |
| ✅ Done | Chat 7: chat history panel; Auto mode for overnight runs; ask before risky steps (Engine 5) | #187 #188 #189 |
| ✅ Done | Your feedback round: mode switcher + order, Model / Effort for every engine, Plan mode + Carry Out, Workshop fold, Queue cards, sidebar scroll blip, images in External Harness, Attach Screen area capture, Actions panel | #203 #204 #205 #206 #207 #208 #209 #210 (issues #192-#201) |
| ✅ Done | Polish round: panel order, image controls on the input, sidebar button column, drop with a focused input, Workshop button icons, capture veil per window, long-Workshop speed, suggestion buttons, chat modes drive the agent's mode | #222-#231 (issues #212-#221; #219 orientation tools open) |
| ✅ Done | Test round 2: Approval mode (Ask / Risky only / Auto) for every engine, Plan card, mode popover, History icon + Open .blend, chat tooltips, square icons, short tool results, size guidance, harness list scroll, Revert fix, no duplicate controls, mode tooltips, queue image icon | #245-#260 (issues #233-#249; #219 open) |
| ✅ Done | Refinement pass: capture across windows, render without a 3D View, every-button tooltips, two-choice suggestions, approval hot swap, Plan Edit, History Open for older chats, bottom toolbar + joined rows, full-width sidebar chat, faint scrollbar | #262-#286 (issues #265-#279) |
| ✅ Unblocked | Chat 1: native markdown. `UILayout.label_markdown` **is** in the 5.3 dev builds; the earlier probe was wrong (`hasattr` on the class can't see RNA functions; `bl_rna.functions` can) | |
| ✅ Done | Agent 1, part 2: record one Claude Code session (uses your Claude login) | #148 #152 |

---

## Test gates (you test, with benchmarks)

Method in master §3.3. Benchmarks: the 6 core + 3 quick challenges.

| ☐ | Gate | When | Benchmarks | You test by hand |
|---|---|---|---|---|
| ✅ | **Test 0: Baseline** | Done 2026-10-18 | Local (small + medium), Remote (cheap + frontier) | External Harness smoke; rating sheet. *Rerun `nodes`: Skills 2 fixed the compositor read* |
| ✅ | **Test 1: Nothing broke** | After Engine 3 + Chat 1 | Full set, Local + Remote; must match Test 0 | The chat looks right (native markdown, tables, Copy rows) |
| ✅ | **Test 2: Bring your own agent** | After Agent 5, Engine 8, Engine 10, Agent 9 | Full set on **External Agent** (OpenCode, Claude Code) for the first time | Artist script A; External Harness with the activity view |
| ☐ | **Test 3: Same Coworker everywhere** | After Engine 6–7, Chat 2–6, Tools 1–3 | All engines; first-request tokens vs Test 0 | Artist script B: explain, translate, questions, Revert, plan |
| ☐ | **Test 4: Deeper tools** | After Tools 4–6, Chat 7, Runtime 1–3, Text Editor 1–4 | Domain suites, all engines | Recipes, sessions browser, Safe mode |
| ☐ | **Test 5: Everywhere in the UI** | After Editors 2–5 | Quick set, all engines | Artist script C: Coworker editor + sidebars |
| ☐ | **Test 6: Agent teams** | After Teams 1–3 | Full set + a new "campfire" suite | Teams, co-pilot, critic |

---

## External Agent — chat here, your own agent does the work (#93)

Plan: `plan_tier4h_external_agent_acp.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ✅ | **Agent 1**: Record real sessions | H0 | Drive `opencode acp` from a script, record fixtures, correct the plan. *Claude Code part waits for your go-ahead* | — | S | Fixtures + findings in 4h §3.1 | #111 |
| ✅ | **Agent 2**: ACP client | H2 | `acp_client.py` (stdlib JSON-RPC over stdio, reader thread, cancel, crash handling), tested by replaying the recorded fixtures | Engine 1 | M | Replay of every fixture passes, incl. cancel, permission, crash | |
| ✅ | **Agent 3**: Agent presets | H3 | Presets + "is it installed / logged in" + install hints + Generic preset; resolve npm `.cmd` shims to the real exe; per-preset isolation switches | Agent 2 | S | Correct status per preset on a machine without the agent | |
| ✅ | **Agent 4**: External Agent in the chat | H4 | The mode in the selector next to External Harness (plain tooltips). Text, images, context chip, streaming, Workshop rows (no duplicates), plan (incl. OpenCode's `todowrite`), Stop, Queue | Agent 2, Engine 4 | M | Campfire flow works with OpenCode, then Claude Code | #140 #142 #144 #146 #148 |
| ✅ | **Agent 5**: Approvals from the agent | H5 | The agent's permission requests use the inline approval row | Agent 4, Engine 5 | S | Allow once / always / deny each behave | #150 |
| ✅ | **Agent 6**: Project folder & resume | H6 | Resume after a restart (`session/load`), New Thread = fresh agent session, the saved chat comes back. *Left out on purpose:* the .blend folder as working folder (moot with Only Coworker Tools) and a generated AGENTS.md (the MCP instructions carry the rules); the session list entry moves to **Chat 7** | Agent 4 | S | Restart Bforartists → continue the same work | #152 |
| ✅ | **Agent 7**: Hardening | H7 | Kill the agent mid-turn, kill Bforartists, bad command, no login, error output shown | Agents 4–6 | S | Checklist passes | #154 |
| ✅ | **Agent 8**: Polish & docs | H8 | Model/mode pickers (from the agent's `configOptions`), slash commands, "supported/preview" badges, wiki page | Agent 7 | S | Docs reviewed | #158 #175 #176 |
| ✅ | **Agent 9**: External Harness activity view | H10 | Read-only live Workshop for External Harness (tool, args, time, ok/error, preview), Copy log / Open log. The debug view for external clients | Engine 2 | S | Claude Desktop / OpenCode calls appear live; a failing call shows its error | #173 |
| ➜ | **Agent 10**: Fallbacks *(only if needed)* | H9 | *Not needed (2026-10-20): Claude Code and OpenCode work well over ACP.* Claude headless / OpenCode server, if a key agent's ACP is unusable | Agent 7 | M | — | |

## One engine for all modes — the shared seam (#93)

Plan: `plan_tier4i_engine_parity.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ✅ | **Engine 1**: Building blocks | I1a | `engine.py`: events, event bus (merges stream chunks), turn record, fake engine | — | S | Tests for order, merging, cancel | #112 |
| ✅ | **Engine 2**: Local & Remote through the engine | I1b | Send / Queue / Stop go through `LoopEngine`; turns also reported as events | Engine 1 | M | No visible change; unit tests + quick challenges as before | #114 |
| ✅ | **Engine 3**: Events become chat messages | I1c | *Revised 2026-10-19:* instead of rewriting the panel's drawing, `chat_state.py` writes engine events into the same chat state and message shapes our loop writes, so an external agent's turn draws exactly like a local one and the panel code is untouched. The engine chip moves to Engine 4 | Engine 2 | S | A turn applied from events splits into user / Workshop / reply with the panel's own code | |
| ✅ | **Engine 4**: Shared UI pieces | I2 | `ui_components.py`: tool row, reasoning, plan, approval, question, engine chip; brand detection → `shared.py` | Engine 3 | S | No per-engine branches in turn drawing | #174 |
| ✅ | **Engine 5**: Approval row | I3 | *Model (`approvals.py`) + the chat's card done in Agent 5 (#150); the card also asks ask_user questions (#168). Coworker's own risky steps ask first too (opt-in *Ask Before Risky Steps*, every engine; #189). Safe mode (Runtime 1) and Tools 4 can use it as they come.* One approval model + inline row; "always" per session; prepare → apply for risky tools | Engine 4 | S | Same row for agent permissions, Safe mode and risky tools | #150 #189 |
| ✅ | **Engine 6**: Shared tools in the server | I5 | MCP tools `update_plan`, `ask_user`, scene checkpoint (auto at turn start), `get_scene_digest(since)` | Engine 2 | M | An external agent updates the plan, asks with buttons; digest says `unchanged` on repeat | #168 #170 |
| ✅ | **Engine 7**: Load tools only when needed | I4 | Surface tools listed, domains loaded on demand (`tools/list_changed`), full list as fallback. **Saves up to ~14K tokens per request** for external agents | Engine 6 | M | First request carries surface tools only (measured) | #172 |
| ✅ | **Engine 8**: Session token | I6 | MCP + bridge require a per-start token; Copy MCP Config includes it | — | S | Tokenless request refused; every mode still works | |
| ✅ | **Engine 9**: Short tool results | I7 | Big results become a short preview + `read_tool_output` handle | — | S | 50 KB node dump → ≤ 2 KB preview on every engine | #167 |
| ✅ | **Engine 10**: Benchmarks on every engine | I8 | Benchmark steps go through the engine, so the same set runs on Local, Remote, External Agent; Compare groups by mode | Engine 2, Agent 4 | S | One suite side by side on three engines | #179 |

## Domain tools — the agent picks a tool instead of writing `bpy`

Plan: `plan_tier4g_domain_tooling.md`. Each task ships its prompt chapter and registers its domain for Engine 7.

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Tools 1**: Video Sequencer | G1 | 5 tools: strips, strip detail, timeline, move/trim, preview frame | — | M | "Move the title strip to frame 50" works on Local | |
| ☐ | **Tools 2**: Text Editor + recipes | G2 | 5 tools: list, read lines, edit lines, search, run. Plus **recipes**: scripts with a parameter block, rerun instead of regenerate | — | M | Agent edits lines 40–45 and reruns; a recipe reruns with a new number | |
| ☐ | **Tools 3**: Nodes | G3 | 5 tools: node detail, add, connect (fuzzy socket names), set value, mute | — | M | Noise → Roughness flow works on Local | |
| ☐ | **Tools 4**: Saving & export | G4 | Save, unsaved check, pack, export selection, incremental save; risky ones ask first | Engine 5 (ship the read-only check first) | M | Export asks; incremental save versions correctly | |
| ☐ | **Tools 5**: Review sheet | G5 | One tiled image of several views (front, back, below, camera) for vision checks | — | S | One labelled image; costs about one image | |
| ☐ | **Tools 6**: Long jobs | G6 | Long bakes/renders run in the background with progress | — | S | A 10-minute bake doesn't time out the agent | |
| ☐ | **Tools 7**: Placement & orientation (#219) | 5c §2.2 | `place(on_top_of / next_to / inside, gap)`, `align`, `distribute`, `drop_to_floor`, reading world-space bounds. *#219 was closed with only a guidance rule (#257); no tools exist yet* | — | S | "Put the lamp on the table, 5 cm from the edge" lands correctly on a 7B model | |
| ☐ | **Tools 8**: Organize my scene | 5c §2.1 | `scene_audit`, `organize_scene(rules)`, `batch_rename`, `cleanup`, `relink_missing`. Dry run first, apply as one undo; also runnable without a model | — | M | A messy test scene is sorted, renamed and cleaned in one confirmed step; audit numbers before/after | |
| ☐ | **Tools 9**: Look-dev & prep helpers | 5c §2.3–2.4 | `frame_camera`, `make_turntable`, `material_from_folder`, `fix_common` | — | M | Each runs from chat and from the quick menu with a preview | |

## Chat features — explain, translate, ask, undo, sessions

Plan: `plan_tier4b_competitor_ux_analysis.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Chat 1**: Native markdown, 5.3 minimum | B0 | Minimum 5.3; use Blender's `label_markdown()` wherever chat text is still drawn by **panels** (native list fallback, panel answers) and delete our old panel renderer. The GPU chat draws its own markdown (GPU 2), since `label_markdown` only works inside panels | Engine 3 | S | Panel-drawn replies use `label_markdown` on Blender + Bforartists 5.3 | |
| 🟡 | **Chat 2**: Explain this (#290) | B1 | Right-click a property, node or object → a short answer from the bundled docs, with the source; `/explain`. *In progress with another agent* | — | M | Property, node, object each give a cited answer on all engines | |
| 🟡 | **Chat 3**: Translate text in the scene (#42) | B1 | Right-click a text field, 3D text or text strip → **Translate with Coworker…** → language (remembered) → one small model call → confirm → written back as one undo step. Fast on light models; no full agent turn | — | S | A 3D text, a strip and a node label translate and undo cleanly | |
| ☐ | **Chat 4**: "Where am I" chip | B6 | A line above the input (editor · mode · selection), read live at send time | — | S | Same prompt in two editors picks different correct tools | |
| ✅ | **Chat 5**: Questions as buttons | B2 | `ask_user` choices as buttons; the suggested next step as [Do it] [Something else] | Engine 6 | S | A click resumes the same turn on all engines | #168 #278 #288 |
| ◐ | **Chat 6**: Undo the agent's turn | B4 | Revert turn (chat + scene), Edit & resend, Retry | Engine 6 | M | Revert restores chat + scene (Local), scene + note (External). *Revert done (#170, one undo step per turn, every engine); Edit & resend and Retry left* | #170 |
| ✅ | **Chat 7**: Chat history panel | B3 | Your chats in one list, like VS Code / Claude: **not tied to the .blend**. A collapsible **History** panel (like Queue) in the Coworker editor and the 3D View tab: title (your first message, renamable), when, which engine / agent, the .blend it started in. Click to continue it (External Agent resumes its own session); New Thread keeps the old chat in the list; delete, pin, search. Opening a .blend still shows the chat you last used there. One file per chat + an index (today's per-.blend `default.json` files move in) | Agent 6 | M | Yesterday's chat continues from the list, in another .blend, on each engine | #187 |
| ☐ | **Chat 8**: Run & fix shown code | B5 | Run button on code blocks (Ask mode) with a safety scan, undo, and Fix | — | M | Broken snippet → Run → Fix → Run works | |
| ☐ | **Chat 9**: Keep docs honest | B7 | Docs and skills record their sources; a checker flags drift; review the pre-5.3 skills | Chat 1, Skills 4 | S | Drifted docs listed; old version skills trimmed or justified | |

## Agent runtime — safety, skills, speed

Plan: `plan_tier4f_agent_intelligence.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ◐ | **Runtime 1**: Safe mode & loop guard | F1 | Trusted / Safe / Strict, path guard, stop the same call repeating 3×. *Done: Permissions Strict / Permissive / Auto and Allow this turn (#289 #292), risky steps ask first (#189), the local loop's repeat stop rule (1.1.37). Left: path guard for file-writing code, the repeat guard for External Agent turns → Security 3* | Engine 5 | M | Safe mode asks before running code; 3× repeat escalates on every engine | #189 #289 #292 |
| ☐ | **Runtime 2**: Skills on demand | F2 | Only skill names + descriptions in the prompt, bodies loaded when needed (also for external clients) | Skills 5 | S | Prompt shrinks measurably on Local 16K | |
| ☐ | **Runtime 3**: Local speed | F3 | llama-server metrics, parallel slots, auto-tune | — | M | tok/s in Status; no benchmark regression | |

## Text Editor coding agent (#52)

Plan: `plan_tier4c_text_editor_ide_agent.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Text Editor 1**: Code control panel | C1 | Sidebar shows the active text, cursor, selection; Generate / Fix / Explain / Edit | Tools 2, Engine 4 | S | Panel shows file info; actions work | |
| ☐ | **Text Editor 2**: Edit with Coworker | C2 | Right-click → rewrite the selection in place, streamed, one undo step | Text Editor 1 | M | Selection rewritten; Ctrl+Z restores | |
| ☐ | **Text Editor 3**: Run → fix | C3 | Run the script, jump to the error, fix the range | Chat 8 | S | Error line found; fix applied | |
| ☐ | **Text Editor 4**: Recipes panel | C4 | The script's parameter block as fields; "Rerun with…" | Tools 2 | S | Change a number → script reruns | |
| ➜ | **Text Editor 5**: Prompt templates | C5 | *Folded into **Wand 7** (Make Operator panel), 2026-10-20* | — | — | — | |

## Editors everywhere — Coworker editor and sidebars (#26)

Plan: `plan_tier4a_editor_integration.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ✅ | **Editors 1**: Coworker editor spike | A0 | Three throwaway prototypes. Your preference order: (1) Preferences-editor clone with Python panels in the center, if it can't leak into your real Preferences; (2) Text / Node editor clone with the chat in native panels; a custom node-tree type gets its own Editor Type entry; (3) GPU drawing last | — | S | Screenshots + recommendation on #26 | |
| ➜ | **Editors 2**: One chat component | A1 | `draw_chat_interface()` that every panel uses | Engine 4 | M | Existing panels use it; no visual change | |
| ➜ | **Editors 3**: Chat in every sidebar | A2 | *Moved to **GPU 5** (compact sidebars), 2026-10-19* | — | — | — | |
| ➜ | **Editors 4**: The Coworker editor | A3 | *Moved to **GPU 4** (the editor around the GPU chat), 2026-10-19* | — | — | — | |
| ➜ | **Editors 5**: Viewport status | A4 | *Replaced by **Footer 1** (2026-10-20): one status in the status bar instead of a dot per viewport* | — | — | — | |
| ☐ | **Editors 6**: Floating chat window *(optional)* | A5 | A separate chat window | Editors 4 | S | Opens once; second click focuses it | |

## Rigging & animation tools

Plan: `plan_tier4e_nice_to_haves.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Rig 1**: Rigging tools | E1 | Armature, bone, constraint, IK chain, mirror pose, bake | Tools pattern | M | "Rig this 3-bone arm with IK" works on Local | |
| ☐ | **Rig 2**: Animation tools | E2 | Interpolation, F-curve modifier, NLA track, bake to NLA (keyframing tool exists) | Tools pattern | M | Bounce with ease-out + noise via tools only | |

## Skills cleanup — clean room

Plan: `plan_tier4s_skills_cleanroom.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ✅ | **Skills 1**: Template tools work everywhere | S0 | Registry moved into the server; `list_blender_templates` crash fixed | — | S | Both tools work in a build and a checkout | #109 (#110) |
| ✅ | **Skills 2**: Nodes on 5.x | S1 | Compositor via `compositing_node_group` in four tools; reads never edit; no `use_nodes` advice | — | S | Live on 5.3: read works, a read without a compositor changes nothing | #109 |
| ✅ | **Skills 3**: Needs list | S2 | Written in the plan | — | S | Done | — |
| ☐ | **Skills 4**: API probes | S3 | Every skill claim gets a probe that runs inside Bforartists | — | S | Probes pass on 5.3; a removed property fails a probe | |
| ☐ | **Skills 5**: Skill format | S4 | Front matter, size budget, references on request; a checker | Skills 4 | S | Checker green; Runtime 2 can build its index | |
| ☐ | **Skills 6**: New chapters | S5 | 5.x changes, data over operators, compositor, bone collections, render settings, bulk mesh access, "finish with evidence" | Skills 4, 5 | M | Every claim has a probe; quick challenges no worse | |
| ☐ | **Skills 7**: Mesh quality check | S6 | `check_mesh_quality`: overlaps, loose parts, open edges, flipped normals, unapplied scale | — | S | Finds each seeded fault | |
| ☐ | **Skills 8**: Docs for skill packs | S7 | Tool-name stability; using other skill packs with External Harness | — | S | Wiki page reviewed | |

## Editors, footer and Coworker operators — Coworker where you work

Review document: `plan_tier4m_coworker_operators.md` (decisions, diagrams, packs, task
cards). Operators look native: conventional names and tooltips, a **wand badge** on the
icon, placed in the menus and Bforartists toolshelf tabs where related native operators
live. Explain and Translate are operators of this kind. One dedicated panel only, for
making new operators.

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| 🟡 | **Sidebar 1**: Chat in every sidebar (#291) | A2 | Compact chat in the Coworker tab of every editor with a sidebar. *In progress with another agent* | GPU 5 | M | — | |
| ☐ | **Footer 1**: Agent status in the status bar | A4 | One item in the global status bar: engine icon, state (*Thinking · step 3/7 · Waiting for you*), a pulse while an approval waits; click focuses the chat; toggle. Replaces the viewport dot | Engine 3 | S | Status follows a turn in every workspace; an approval is visible with the chat closed | |
| ☐ | **Wand 0**: Spike + icon set | 4m §3.3, §7 | Remove Silences end to end in its native places (*Strip* menu, Bforartists *Strip* tab) with a wand-badged icon; first 8 self-contained icons (SVG → PNG, light/dark); probe the 5.3 sound-sample API and node notes | — | S | Screenshots in Bforartists and plain Blender 5.3; spec frozen | |
| ☐ | **Wand 1**: Operator framework | 4m §3 | `OperatorSpec`, registry (shipped + user folder), operator factory, native placement (menus; Bforartists toolshelf **icon buttons** via a `CoworkerEntry` that plugs into `draw_entries`, no dropdown; menus only in plain Blender; context menus), icons via `icon_value`, gather → decide (active engine, schema-checked; heuristic fallback) → preview → apply (one undo) → report, `run_operator` MCP tool | Wand 0 | M | A test pack (tool / hybrid / wizard / macro) runs from its menu, its toolshelf panel and the chat; Explain/Translate can adopt the spec | |
| ☐ | **Wand 2**: Sequencer pack · *first* | 4m §4.1 | Remove Silences, Convert to Vertical (9:16), Subtitles from Text, Assemble Timeline from Scenes, Export Review Video | Wand 1, Tools 1 | M | A talk clip loses its pauses (gaps closed) in one undo; a 16:9 edit becomes 9:16 | |
| ☐ | **Wand 3**: Node pack · *second* | 4m §4.2 | Tidy & Explain Tree, Frame Selected by Purpose | Wand 1, Tools 3 | M | A 60-node tree framed into labelled groups with notes on a 7B model; one undo | |
| ☐ | **Wand 4**: Bake wizard *(stretch)* | 4m §4.3 | Bake Texture Sets… by question cards → bake → wire a material | Wand 1 | M | Two material sets bake normal + AO + base colour from three cards | |
| ☐ | **Wand 5**: Asset library wizard *(stretch)* | 4m §4.3 | Prepare Assets for a Library… (thumbnail style, location, author, catalogs) | Wand 1 | M | 10 props become a library with thumbnails and catalogs | |
| ☐ | **Wand 6**: Save Turn as Operator | 4m §5.1 | Per user: the last good turn's tool calls → `operator.toml` + `steps.json` in the user folder; save card (steps, fields, selection, name, place, icon); model-free replay, one undo; Edit / Export / Import | Wand 1, Security 2 | M | Recorded once, replays on another selection with no engine, from its menu, after a restart | |
| ☐ | **Wand 7**: Make Operator panel (Text Editor) | 4m §5 | The one dedicated panel: template, checks, place picker, Register, Package as extension (#52) | Wand 1, Text Editor 1–2 | M | A user-written operator appears in its native menu and ships as a zip | |

*Logged for Tier 5 (after the tools are built):* migrate the wand icon set into
Bforartists so operators can use native icon names there; the add-on keeps its own copies
for plain Blender.

## Orchestration — first layer (#71)

The release ships the first layer; agent teams (Teams 1–3) move to Tier 5.

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Orchestrate 1**: Map over selection | 4m §7 | Run one instruction or operator per selected object / strip / node with a progress row; stop or continue on error; one undo for the batch | Tools 8 | M | "Give each selected rock a random grey material" on 30 objects, one undo | |
| ☐ | **Orchestrate 2**: Missions with evidence | 4m §7 | Each plan step gets a scene checkpoint (a failed step reverts only itself); the mission ends with a validator pass (`scene_audit` + Skills 7) and reports numbers | Engine 6, Skills 7 | M | A 5-step mission with one failing step recovers and ends with an audit report | |

## Security checks

Done: session token (Engine 8), Permissions Strict / Permissive / Auto (#289 #292), risky
steps ask first (#189), per-window session ids (#85), crash-safe servers.

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Security 1**: Threat model + audit | — | One page: bridge, MCP HTTP, ACP agent processes, External Harness configs, downloads (llama-server, models, Poly Haven), the extension repository. Who can reach what; findings become tasks | — | S | `_misc/security_model.md` reviewed; findings filed | |
| ☐ | **Security 2**: Secrets | — | API keys / HF token / session token never in logs, session exports, Copy Error or screenshots of the prefs; settings backup (#86) stores them separately or not at all | Security 1 | S | Grep of every log/export after a session finds no secret | |
| ☐ | **Security 3**: Code & file guard | F1 | Path guard for generated code (writes outside the project/temp ask first), network calls from generated code flagged, the 3× repeat guard on every engine | Security 1 | M | A script writing to `C:\Windows` asks; a repeat loop stops on External Agent | |
| ☐ | **Security 4**: Untrusted content | — | Text from the scene, files and downloads (text blocks, names, asset descriptions) is passed as data, never as instructions; risky steps triggered right after reading such content ask first | Security 1 | S | A text block saying "delete everything" doesn't make the agent do it | |
| ☐ | **Security 5**: Supply chain + shared operator packs | — | Pin and hash-check the llama-server download and vendored deps; keep the extension listing's SHA-256 (#86); shared operator packs show their source and ask once before first run | — | S | A tampered download and an unknown operator pack are both stopped | |

## Agent teams & critics — orchestration layer (#71) · *Tier 5*

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Teams 1**: Agent teams | X1 | Planner → specialists → validator, one undo for the whole mission | Runtime 1, Engine 9, Engine 6 | L | Campfire scene built by a team | |
| ☐ | **Teams 2**: Scene co-pilot | X2 | Background scan for common problems with safe one-click fixes | Teams 1, Skills 7 | M | Issues listed with fixes | |
| ☐ | **Teams 3**: Render critic | X3 | Render → critique → fix loop, using review sheets | Tools 5, Teams 1 | M | Score improves within 5 rounds, or it stops | |

## Coworker chat — one GPU module, full editor + compact sidebars (#26)

Decision: `_misc/spikes/gpu_chat/DECISION.md` (spike **GPU 0**). The chat is drawn by our
own module in your theme's colours, bevels, fonts and UI scale, because Python panels
can't pin the input to the bottom, send on Enter, or show `@` suggestions while typing. It
comes in **two sizes**: *full* in the center of the Coworker editor (a custom "Coworker"
node-editor type, see Editors 1), and *compact* in the "Coworker" tab of an editor's
sidebar. Both read the same chat data (Engine 3), so every engine and both sizes always
show the same conversation. Replaces Editors 3 and 4, and the planned sidebar quick fixes
(`@`, reading order).

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ✅ | **GPU 0**: Spike | — | Prototype both sizes in Bforartists 5.3 with simulated typing | — | S | Go/no-go + screenshots | (this PR) |
| ✅ | **GPU 1**: Toolkit | — | `ui_gpu/`: theme reader (colours, roundness, UI scale × zoom), fonts (UI + mono), text measuring and wrapping, boxes, buttons, hover/press, hit areas. Layout bpy-free and unit-tested; thin `gpu`/`blf` layer | — | M | A test page draws buttons and wrapped text in the default and a light theme | |
| ✅ | **GPU 2**: Chat view | — | Turns from the chat state (your message, Workshop collapsible, tool rows, reply), our own markdown subset (bold, italic, code, lists, headings, links), code blocks with Copy (Run later), **icons first** (the native chat's icon vocabulary, full-colour Bforartists icons exported from its SVGs; keyframe-coloured heading markers), headings sized like 5.3's label_markdown, live streaming, follow the newest, scroll | GPU 1, Engine 3 | M | A recorded turn draws like the panel does today, in both sizes | |
| ✅ | **GPU 3**: Input box | — | Click to focus / Esc to leave, multi-line, **Enter sends, Shift+Enter new line**, cursor + selection, copy/paste, undo, **`@` suggestions** while typing, attachment chip, Send/Stop, "edit in a native box" for IME, restart after opening a file | GPU 1 | M | Type, edit, `@` and send without the mouse; node/viewport shortcuts never fire while focused | |
| ✅ | **GPU 4**: The Coworker editor | A3 | Register the "Coworker" editor type; one-click setup; full chat in the center; toolshelf icon actions (templates, explain, wizards); sidebar panels (session, model, plan); header | GPU 2, 3, Editors 1 | M | Pick "Coworker" in the Editor Type menu and chat | |
| ✅ | **GPU 5**: Compact sidebars | A2 | **Done for the 3D View** (1.1.40; Preferences > Sidebar Chat switches back to Classic). The 3D View "Coworker" tab draws the compact chat (replaces today's sidebar drawing), then Node, VSE and Text editors; native scrolling list as fallback where painting misbehaves | GPU 2, 3 | M | Same conversation, same look in the editor and in four sidebars | |
| ☐ | **GPU 6**: Guided flows | — | The same toolkit for question buttons (Chat 5), Explain answers (Chat 2) and step-by-step wizards in the chat | GPU 4 | M | A three-step wizard runs inside the chat | |

