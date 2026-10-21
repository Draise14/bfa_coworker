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
`ui_chat.py`. Sizes: **S** ≤ 2 days · **M** ≤ 1 week · **L** more. **⏩** = moved to Tier 5 (master §0.4).

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
| ✅ Done | **2026-10-20:** choice buttons wrap (keyframe icons, green = recommended), Allow this turn + permissions dropdown, permissions **Strict / Permissive / Auto**, History Open loads the .blend at once, **Explain This** in the right-click menus (#290), **Translate** in the right-click menus (#42), the chat's voice is always Coworker, **the Coworker tab in every editor's sidebar** + *Use editor context only* (#291) | #288 #289 #292 #293 #294 #296 #297 #298 #325 |
| 📝 Planned | **2026-10-21: the four workflows** (master §0): Coworker operators in native places (Wand 0–5, Sequencer first), the **Spell Book** (Spell 1–3; #300 becomes prompt spells), **procedural first** (Procedural 1–3), new tools (Tools 7–9), Footer 1, Orchestrate 1–2, Security 1–5. Review doc: `plan_tier4m_coworker_operators.md` | #327-#349 |
| 📝 Planned | **2026-10-21 (later): one thread per editor** — the orchestration layer: each editor's job in its own thread, side by side (Threads 0–3, `plan_tier4o_orchestration_threads.md`). **Moved to Tier 5**: Edit from Footage + the big wizards, Spell 2–3, Procedural 2–3, Tools 9, Orchestrate 2, Teams, Security 5 (master §0.4) | #356-#359 |
| ⏭ Next | **The rest of Tier 4, one issue per task** (below): #299-#324. Tier 4a review: what's left is Chat 4 (#299) and Quick prompts (#300); Viewport feedback and the moodboard are dropped / Tier 5 | |

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
| ☐ | **Test 7: Side by side** | After Threads 3 | Quick set per thread, all engines | A scene build (3D View), a node-group tidy and a strip translation at the same time; Stop one; a deliberate conflict; Revert |
| ☐ | **Test 6: Agent teams** *(Tier 5)* | After Teams 1–3 | Full set + a new "campfire" suite | Teams, co-pilot, critic |

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
| ☐ | **Tools 1** (#304): Video Sequencer | G1 | 5 tools: strips, strip detail, timeline, move/trim, preview frame | — | M | "Move the title strip to frame 50" works on Local | |
| ☐ | **Tools 2** (#305): Text Editor + recipes | G2 | 5 tools: list, read lines, edit lines, search, run. Plus **recipes**: scripts with a parameter block, rerun instead of regenerate | — | M | Agent edits lines 40–45 and reruns; a recipe reruns with a new number | |
| ☐ | **Tools 3** (#306): Nodes | G3 | 5 tools: node detail, add, connect (fuzzy socket names), set value, mute | — | M | Noise → Roughness flow works on Local | |
| ☐ | **Tools 4** (#307): Saving & export | G4 | Save, unsaved check, pack, export selection, incremental save; risky ones ask first | Engine 5 (ship the read-only check first) | M | Export asks; incremental save versions correctly | |
| ☐ | **Tools 5** (#308): Review sheet | G5 | One tiled image of several views (front, back, below, camera) for vision checks | — | S | One labelled image; costs about one image | |
| ☐ | **Tools 6** (#309): Long jobs | G6 | Long bakes/renders run in the background with progress | — | S | A 10-minute bake doesn't time out the agent | |
| ☐ | **Tools 7** (#327): Placement & orientation | 4m §4.3 | `place(on_top_of / next_to / inside, gap)`, `align`, `distribute`, `drop_to_floor` from world-space bounds. *#219 shipped only a guidance rule (#257)* | — | S | "Put the lamp on the table, 5 cm from the edge" is right on a 7B model | |
| ☐ | **Tools 8** (#328): Organize my scene | 4m §4.3 | `scene_audit`, `organize_scene(rules)`, `batch_rename`, `cleanup`, `relink_missing`; dry run, one undo, works without a model | — | M | A messy scene audited, sorted, renamed, cleaned in one step; numbers before/after | |
| ⏩ | **Tools 9** (#329): Look-dev & prep helpers *(Tier 5)* | 4m §4.3 | `frame_camera`, `make_turntable`, `material_from_folder`, `fix_common` | — | M | Each runs from the chat and as an operator with a preview | |

## Chat features — explain, translate, ask, undo, sessions

Plan: `plan_tier4b_competitor_ux_analysis.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Chat 1** (#301): Native markdown, 5.3 minimum | B0 | Minimum 5.3; use Blender's `label_markdown()` wherever chat text is still drawn by **panels** (native list fallback, panel answers) and delete our old panel renderer. The GPU chat draws its own markdown (GPU 2), since `label_markdown` only works inside panels | Engine 3 | S | Panel-drawn replies use `label_markdown` on Blender + Bforartists 5.3 | |
| ✅ | **Chat 2**: Explain this | B1 | Right-click a setting, an operator or an editor → **Explain This** asks in Ask mode (greyed out until Coworker runs). *Citing the bundled docs is left for Chat 9* | — | M | Property, operator, editor each give an answer on all engines | #294 (#290) |
| ✅ | **Chat 3**: Translate text in the scene (#42) | B1 | Right-click a text field, 3D text, text strip or the Text Editor (selection or whole text) → **Translate...** → language (remembered) → written back by the agent (Revert undoes it) | — | S | A 3D text, a strip and a text field translate | #296 |
| ☐ | **Chat 4** (#299): "Where am I" chip | B6 | A line above the input (editor · mode · selection), read live at send time. *The sidebar chats already send it (#298); this shows it* | — | S | Same prompt in two editors picks different correct tools | |
| ✅ | **Chat 5**: Questions as buttons | B2 | `ask_user` choices as buttons; the suggested next step as buttons (two-choice, recommended in green) | Engine 6 | S | A click resumes the same turn on all engines | #168 #288 (issues #220 #270) |
| ✅ | **Chat 6**: Undo the agent's turn | B4 | Revert turn (chat + scene) on every reply, to the named "Before Coworker's turn" undo step. *Edit & resend / Retry: later, if wanted* | Engine 6 | M | Revert restores chat + scene (Local), scene + note (External) | #170 #242 |
| ✅ | **Chat 7**: Chat history panel | B3 | Your chats in one list, like VS Code / Claude: **not tied to the .blend**. A collapsible **History** panel (like Queue) in the Coworker editor and the 3D View tab: title (your first message, renamable), when, which engine / agent, the .blend it started in. Click to continue it (External Agent resumes its own session); New Thread keeps the old chat in the list; delete, pin, search. Opening a .blend still shows the chat you last used there. One file per chat + an index (today's per-.blend `default.json` files move in) | Agent 6 | M | Yesterday's chat continues from the list, in another .blend, on each engine | #187 |
| ☐ | **Chat 8** (#302): Run & fix shown code | B5 | Run button on code blocks (Ask mode) with a safety scan, undo, and Fix | — | M | Broken snippet → Run → Fix → Run works | |
| ☐ | **Chat 9** (#303): Keep docs honest | B7 | Docs and skills record their sources; a checker flags drift; review the pre-5.3 skills | Chat 1, Skills 4 | S | Drifted docs listed; old version skills trimmed or justified | |

## Agent runtime — safety, skills, speed

Plan: `plan_tier4f_agent_intelligence.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Runtime 1** (#310): Loop guard & path guard | F1 | *Permissions are done: Strict / Permissive / Auto (#292).* Left: path guard, stop the same call repeating 3× | Engine 5 | M | Safe mode asks before running code; 3× repeat escalates | |
| ☐ | **Runtime 2** (#311): Skills on demand | F2 | Only skill names + descriptions in the prompt, bodies loaded when needed (also for external clients) | Skills 5 | S | Prompt shrinks measurably on Local 16K | |
| ☐ | **Runtime 3** (#312): Local speed | F3 | llama-server metrics, parallel slots, auto-tune | — | M | tok/s in Status; no benchmark regression | |

## Text Editor coding agent (#52)

Plan: `plan_tier4c_text_editor_ide_agent.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Text Editor 1** (#313): Code control panel | C1 | Sidebar shows the active text, cursor, selection; Generate / Fix / Explain / Edit | Tools 2, Engine 4 | S | Panel shows file info; actions work | |
| ☐ | **Text Editor 2** (#314): Edit with Coworker | C2 | Right-click → rewrite the selection in place, streamed, one undo step | Text Editor 1 | M | Selection rewritten; Ctrl+Z restores | |
| ☐ | **Text Editor 3** (#315): Run → fix | C3 | Run the script, jump to the error, fix the range | Chat 8 | S | Error line found; fix applied | |
| ☐ | **Text Editor 4** (#316): Recipes panel | C4 | The script's parameter block as fields; "Rerun with…" | Tools 2 | S | Change a number → script reruns | |
| ➜ | **Text Editor 5**: Prompt templates | C5 | *Moved into **Editors 7** (quick prompts per editor, #300)* | Text Editor 1 | S | A user template appears and works | |

## Editors everywhere — Coworker editor and sidebars (#26)

Plan: `plan_tier4a_editor_integration.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ✅ | **Editors 1**: Coworker editor spike | A0 | Three throwaway prototypes. Your preference order: (1) Preferences-editor clone with Python panels in the center, if it can't leak into your real Preferences; (2) Text / Node editor clone with the chat in native panels; a custom node-tree type gets its own Editor Type entry; (3) GPU drawing last | — | S | Screenshots + recommendation on #26 | |
| ✅ | **Editors 2**: One chat component | A1 | *Done differently:* one GPU chat surface (`ui_gpu/`) draws the editor and every sidebar | Engine 4 | M | One component, both sizes | GPU 1-5 |
| ✅ | **Editors 3**: Chat in every sidebar | A2 | The Coworker tab (Queue, Plan, chat, History, Status, Coworker) in the Node, Image, Sequencer, Clip, Dope Sheet, Graph, NLA, Text and Spreadsheet sidebars; a message says which editor it came from; *Use editor context only* toggle | GPU 5 | M | Every editor with a sidebar has the tab | #298 #325 (#291) |
| ➜ | **Editors 4**: The Coworker editor | A3 | *Moved to **GPU 4** (the editor around the GPU chat), 2026-10-19* | — | — | — | |
| ✖ | **Editors 5**: Viewport status | A4 | *Dropped 2026-10-20: less in your face; the chat and the editor are enough* | — | — | — | |
| ✅ | **Editors 6**: Floating chat window | A5 | *Done differently:* the Coworker editor opens in its own window (Open Editor), with capture and drops working there | Editors 4 | S | A separate chat window | issues #215 #266 |
| ☐ | **Editors 7** (#300): Quick prompts per editor | A Phase 6, C5 | One-click prompts under each sidebar chat and on the Coworker editor's toolshelf; user templates from a folder. *2026-10-21: built as **prompt spells** in the Spell Book (Spell 1), so they share its storage, icons and editing* | Editors 3, Spell 1 | S | Each editor shows its prompts; a user file adds one | |
| ☐ | **Footer 1** (#330): Agent status in the status bar | 4o §5 | One item in the global status bar with **one icon per live thread**: state (*3/7*, ✔, ⏸), a pulse while one waits for you; the popover lists threads with Stop / answer / focus; toggle. With one thread it is a single item. *Not the dropped viewport dot* | Threads 1 | S | Each thread's status follows its turn in every workspace; an approval is visible with the chat closed | |

## Rigging & animation tools

Plan: `plan_tier4e_nice_to_haves.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Rig 1** (#317): Rigging tools | E1 | Armature, bone, constraint, IK chain, mirror pose, bake | Tools pattern | M | "Rig this 3-bone arm with IK" works on Local | |
| ☐ | **Rig 2** (#318): Animation tools | E2 | Interpolation, F-curve modifier, NLA track, bake to NLA (keyframing tool exists) | Tools pattern | M | Bounce with ease-out + noise via tools only | |

## Skills cleanup — clean room

Plan: `plan_tier4s_skills_cleanroom.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ✅ | **Skills 1**: Template tools work everywhere | S0 | Registry moved into the server; `list_blender_templates` crash fixed | — | S | Both tools work in a build and a checkout | #109 (#110) |
| ✅ | **Skills 2**: Nodes on 5.x | S1 | Compositor via `compositing_node_group` in four tools; reads never edit; no `use_nodes` advice | — | S | Live on 5.3: read works, a read without a compositor changes nothing | #109 |
| ✅ | **Skills 3**: Needs list | S2 | Written in the plan | — | S | Done | — |
| ☐ | **Skills 4** (#319): API probes | S3 | Every skill claim gets a probe that runs inside Bforartists | — | S | Probes pass on 5.3; a removed property fails a probe | |
| ☐ | **Skills 5** (#320): Skill format | S4 | Front matter, size budget, references on request; a checker | Skills 4 | S | Checker green; Runtime 2 can build its index | |
| ☐ | **Skills 6** (#321): New chapters | S5 | 5.x changes, data over operators, compositor, bone collections, render settings, bulk mesh access, "finish with evidence" | Skills 4, 5 | M | Every claim has a probe; quick challenges no worse | |
| ☐ | **Skills 7** (#322): Mesh quality check | S6 | `check_mesh_quality`: overlaps, loose parts, open edges, flipped normals, unapplied scale | — | S | Finds each seeded fault | |
| ☐ | **Skills 8** (#323): Docs for skill packs | S7 | Tool-name stability; using other skill packs with External Harness | — | S | Wiki page reviewed | |

## Coworker operators & the Spell Book — the four workflows (master §0)

Review doc: `plan_tier4m_coworker_operators.md`. Operators look native (conventional
names, a **wand badge** on a self-contained icon), sit in the menus and Bforartists
toolshelf tabs where related native operators live, run gather → decide (active engine,
only when needed) → preview → apply (one undo) → report. The **Spell Book** is the one
dedicated panel: shipped wizard spells, prompt spells (#300) and your own, replayed with
no model.

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Wand 0** (#331): Spike + icon set | 4m §3.3 | Remove Silences end to end in its native places with a wand-badged icon button; first 8 self-contained icons (SVG + PNG); probe the 5.3 sound API and node notes | — | S | Screenshots in Bforartists and plain Blender 5.3; spec frozen | |
| ☐ | **Wand 1** (#332): Operator framework | 4m §3 | Spec, registry, factory, native placement (menus, toolshelf icon buttons, context menus), icons, the 5-stage pipeline, `run_operator` MCP tool | Wand 0 | M | A test pack of every kind runs from its menu, toolshelf and the chat | |
| ☐ | **Wand 2** (#333): Sequencer basics · *first* | 4m §4.1 | Remove Silences, Reframe for Vertical (centre-crop compositor node group, editable), Subtitles from Text — all work with no engine | Wand 1, Tools 1 | M | Each runs from its native menu / toolshelf with a preview and one undo, no engine | |
| ⏩ | **Wand 2b**: Edit from Footage *(Tier 5)* | 4m §4.1 | The wizard (look → propose cuts + per-shot reframes → choose → build → review), Assemble Timeline, Export Review Video | Wand 2, Tools 5, GPU 6, threads | L | 3 clips → a 30 s vertical edit with editable reframes | |
| ☐ | **Wand 3** (#334): Node pack · *second* | 4m §4.2 | Tidy & Explain Tree, Frame Selected by Purpose | Wand 1, Tools 3 | M | A 60-node tree framed into labelled groups with notes on a 7B model | |
| ⏩ | **Wand 4** (#335): Bake wizard *(Tier 5)* | 4m §4.3 | Bake Texture Sets… by question cards | Wand 1 | M | Two material sets bake from three answered cards | |
| ⏩ | **Wand 5** (#336): Asset library wizard *(Tier 5)* | 4m §4.3 | Prepare Assets for a Library… (thumbnails, location, author, catalogs) | Wand 1 | M | 10 props become a library with thumbnails and catalogs | |
| ☐ | **Spell 1** (#337): The Spell Book | 4m §5.1 | Shipped spells, prompt spells (#300), your own per user (**Save as Spell from a chat reply**); icon, name, run mode, steps, fields, place picker, **Publish** as toolshelf icon buttons / menu items, Test, Export / Import | Wand 1, Security 2 | M | A spell shows as an icon button after a restart, runs with no engine, one undo | |
| ⏩ | **Spell 2** (#338): Recording spells *(Tier 5)* | 4m §5.2 | Record … Stop; Add Last Action; Add to Spell Book on right-click; control steps; optional one-time model pass at save | Spell 1 | M | Recorded from chat and from clicks, both replay with no engine | |
| ⏩ | **Spell 3** (#339): Edit as Code + package *(Tier 5)* | 4m §5.2 | A spell as a readable script + spec in the Text Editor; package as an extension | Spell 1, Text Editor 1–2 | M | An edited spell republishes; a set ships as a zip | |

## Procedural first — promote work to assets (master §0)

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Procedural 1** (#340): Procedural first | 4m §4.4 | Prefer node-group assets (Default Library add-on, your libraries, online later); `find_procedural_asset(intent)`; read group inputs; linear only when asked | — | S | "A stone wall along this curve" uses a library node group with inputs set | |
| ⏩ | **Procedural 2** (#341): Make Parametric *(Tier 5)* | 4m §4.4 | Linear result → Geometry Nodes asset with inputs + gizmos, saved to your library with agent-friendly metadata | Tools 3, Procedural 1 | M | A chat-built fence becomes an asset with inputs and a length gizmo | |
| ⏩ | **Procedural 3** (#342): The nudge *(Tier 5)* | 4m §4.4 | *Save as Spell?* on repeats, *Make Parametric?* after a build; one ignorable button | Spell 1, Procedural 2 | S | At most once per pattern per session; never interrupts | |

## Orchestration — threads per editor (#71)

Plan: `plan_tier4o_orchestration_threads.md`. Each editor's job runs in its **own
thread** (history, Stop, Queue, plan, questions), side by side; tool calls interleave on
Blender's main thread; claims stop threads overwriting each other; Revert is honest when
undo steps interleave. Missions and agent teams (Tier 5) are built on threads.

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Threads 0** (#356): Spike | 4o §5 | Measure: llama-server slots, two ACP sessions at once, interleaved tool calls + undo, the per-thread state list | — | S | Numbers in 4o §6; state list frozen | |
| ☐ | **Threads 1** (#357): The thread model | 4o §5 | bpy-free `threads.py` (Thread, ThreadRegistry); today's globals move behind it; thread id on MCP calls; per-thread plan / ask_user / digest / domains on the server | Threads 0 | M→L | One thread behaves exactly like today; two fake-engine threads keep separate history, Queue, Stop | |
| ☐ | **Threads 2** (#358): Each editor has its own thread | 4o §2.1 | Sidebar shows its editor's thread; header (New thread here, Switch…, Open in Coworker editor); right-click operators start in their editor's thread; thread list in the Coworker editor; saved per .blend | Threads 1 | M | A 3D and a node question become two chats; the .blend restores both | |
| ☐ | **Threads 3** (#359): Side by side, safely | 4o §3 | Runner (cap, fair model queue, Local slots option, ACP session per thread, Remote back-off); claims + conflict asking; undo names per thread + honest Revert; Stop all; "other threads" context line | Threads 1, 2 | L | **Test 7**: 3D build + node tidy + strip translation at once; Stop one; a conflict asks; Revert never silently undoes another thread | |
| ☐ | **Orchestrate 1** (#343): Map over selection *(stretch)* | 4m §7 | One instruction or operator per selected item, inside the editor's thread; progress row, stop/continue on error, one undo | Tools 8, Threads 3 | M | 30 rocks get individual materials in one undo | |
| ⏩ | **Orchestrate 2** (#344): Missions *(Tier 5)* | 4o §4 | A mission thread spawns helper threads per plan step, each with a checkpoint; ends with a validator pass | Threads, Skills 7 | M | A failing step reverts only itself; the mission ends with an audit | |

## Security checks

Done: session token (Engine 8), permissions Strict / Permissive / Auto (#292), risky
steps ask first (#189), per-window session ids (#85), crash-safe servers.

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Security 1** (#345): Threat model + audit | 4m §7 | Bridge, MCP (loopback + origin), ACP processes, harness configs with the token, downloads, extension repo, spell folders | — | S | `_misc/security_model.md` reviewed; findings filed | |
| ☐ | **Security 2** (#346): Secrets | 4m §7 | No keys/tokens in logs, exports, Copy Error, recorded spells | Security 1 | S | Grep after a full session finds none | |
| ☐ | **Security 3** (#347): Code & file guard | 4m §7 | Writes outside project/temp ask; network from generated code flagged; repeat guard on every engine (with Runtime 1, #310) | Security 1 | M | A write to a system folder asks | |
| ☐ | **Security 4** (#348): Untrusted content | 4m §7 | Scene/file/download text is data, never instructions | Security 1 | S | A text block saying "delete everything" changes nothing | |
| ⏩ | **Security 5** (#349): Supply chain + shared spells *(Tier 5, with sharing)* | 4m §7 | Hash-check downloads; shared spells and online libraries show their source and ask once | — | S | A tampered download and an unknown spell pack are stopped | |

## Agent teams & critics — orchestration layer (#71) · *Tier 5*

Built on threads (4o §4): a planner thread starts specialist threads in the right editors; the co-pilot and the critic are background threads.

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Teams 1**: Agent teams | X1 | Planner → specialists → validator, one undo for the whole mission | Threads 3, Runtime 1, Engine 9, Engine 6 | L | Campfire scene built by a team | |
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
| ✅ | **GPU 5**: Compact sidebars | A2 | 3D View (1.1.40; Preferences > Sidebar Chat switches back to Classic there and in the Text Editor), then **every editor with a sidebar** (#298) | GPU 2, 3 | M | Same conversation, same look in the editor and in four sidebars | |
| ☐ | **GPU 6** (#324): Guided flows | — | The same toolkit for question buttons (Chat 5), Explain answers (Chat 2) and step-by-step wizards in the chat | GPU 4 | M | A three-step wizard runs inside the chat | |

