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

## Where we are

| Status | Task | PR |
|---|---|---|
| ✅ Done | Green test baseline + kickoff | #108 |
| ✅ Done | Skills 1 + 2: template tools work everywhere; node tools read the 5.x compositor | #109 (#110) |
| ✅ Done | Agent 1: real ACP sessions recorded with OpenCode | #111 |
| ✅ Done | Engine 1: the engine's building blocks | #112 |
| ✅ Done | Engine 2: Local & Remote run through the engine | #114 |
| 🔍 In review | Engine 3: events become chat messages | #120 (issue #119) |
| 🔍 In review | Agent 2: ACP client (checked live with OpenCode) | #118 |
| ⏭ Next (parallel, prototypes only) | Editors 1: Coworker editor spike (#26) | |
| ⛔ Blocked | Chat 1: native markdown. `UILayout.label_markdown` is **not in** the Bforartists 5.3 dev build of 2026-10-08 yet (probed) | |
| ❓ Waiting on you | Agent 1, part 2: record one Claude Code session (uses your Claude login) | |

---

## Test gates (you test, with benchmarks)

Method in master §3.3. Benchmarks: the 6 core + 3 quick challenges.

| ☐ | Gate | When | Benchmarks | You test by hand |
|---|---|---|---|---|
| ☑ | **Test 0: Baseline** | Done 2026-10-18 | Local (small + medium), Remote (cheap + frontier) | External Harness smoke; rating sheet. *Rerun `nodes`: Skills 2 fixed the compositor read* |
| ☐ | **Test 1: Nothing broke** | After Engine 3 + Chat 1 | Full set, Local + Remote; must match Test 0 | The chat looks right (native markdown, tables, Copy rows) |
| ☐ | **Test 2: Bring your own agent** | After Agent 5, Engine 8, Engine 10, Agent 9 | Full set on **External Agent** (OpenCode, Claude Code) for the first time | Artist script A; External Harness with the activity view |
| ☐ | **Test 3: Same Coworker everywhere** | After Engine 6–7, Chat 2–6, Tools 1–3 | All engines; first-request tokens vs Test 0 | Artist script B: explain, translate, questions, Revert, plan |
| ☐ | **Test 4: Deeper tools** | After Tools 4–6, Chat 7, Runtime 1–3, Text Editor 1–4 | Domain suites, all engines | Recipes, sessions browser, Safe mode |
| ☐ | **Test 5: Everywhere in the UI** | After Editors 2–5 | Quick set, all engines | Artist script C: Coworker editor + sidebars |
| ☐ | **Test 6: Agent teams** | After Teams 1–3 | Full set + a new "campfire" suite | Teams, co-pilot, critic |

---

## External Agent — chat here, your own agent does the work (#93)

Plan: `plan_tier4h_external_agent_acp.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☑ | **Agent 1**: Record real sessions | H0 | Drive `opencode acp` from a script, record fixtures, correct the plan. *Claude Code part waits for your go-ahead* | — | S | Fixtures + findings in 4h §3.1 | #111 |
| ☐ | **Agent 2**: ACP client | H2 | `acp_client.py` (stdlib JSON-RPC over stdio, reader thread, cancel, crash handling), tested by replaying the recorded fixtures | Engine 1 | M | Replay of every fixture passes, incl. cancel, permission, crash | |
| ☐ | **Agent 3**: Agent presets | H3 | Presets + "is it installed / logged in" + install hints + Generic preset; resolve npm `.cmd` shims to the real exe; per-preset isolation switches | Agent 2 | S | Correct status per preset on a machine without the agent | |
| ☐ | **Agent 4**: External Agent in the chat | H4 | The mode in the selector next to External Harness (plain tooltips). Text, images, context chip, streaming, Workshop rows (no duplicates), plan (incl. OpenCode's `todowrite`), Stop, Queue | Agent 2, Engine 4 | M | Campfire flow works with OpenCode, then Claude Code | |
| ☐ | **Agent 5**: Approvals from the agent | H5 | The agent's permission requests use the inline approval row | Agent 4, Engine 5 | S | Allow once / always / deny each behave | |
| ☐ | **Agent 6**: Project folder & resume | H6 | Project folder, compact `.coworker/AGENTS.md`, "Only Coworker tools", resume after a restart, session list entry | Agent 4 | S | Restart Bforartists → continue the same work | |
| ☐ | **Agent 7**: Hardening | H7 | Kill the agent mid-turn, kill Bforartists, bad command, no login, error output shown | Agents 4–6 | S | Checklist passes | |
| ☐ | **Agent 8**: Polish & docs | H8 | Model/mode pickers (from the agent's `configOptions`), slash commands, "supported/preview" badges, wiki page | Agent 7 | S | Docs reviewed | |
| ☐ | **Agent 9**: External Harness activity view | H10 | Read-only live Workshop for External Harness (tool, args, time, ok/error, preview), Copy log / Open log. The debug view for external clients | Engine 2 | S | Claude Desktop / OpenCode calls appear live; a failing call shows its error | |
| ☐ | **Agent 10**: Fallbacks *(only if needed)* | H9 | Claude headless / OpenCode server, if a key agent's ACP is unusable | Agent 7 | M | — | |

## One engine for all modes — the shared seam (#93)

Plan: `plan_tier4i_engine_parity.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☑ | **Engine 1**: Building blocks | I1a | `engine.py`: events, event bus (merges stream chunks), turn record, fake engine | — | S | Tests for order, merging, cancel | #112 |
| ☑ | **Engine 2**: Local & Remote through the engine | I1b | Send / Queue / Stop go through `LoopEngine`; turns also reported as events | Engine 1 | M | No visible change; unit tests + quick challenges as before | #114 |
| ☐ | **Engine 3**: Events become chat messages | I1c | *Revised 2026-10-19:* instead of rewriting the panel's drawing, `chat_state.py` writes engine events into the same chat state and message shapes our loop writes, so an external agent's turn draws exactly like a local one and the panel code is untouched. The engine chip moves to Engine 4 | Engine 2 | S | A turn applied from events splits into user / Workshop / reply with the panel's own code | |
| ☐ | **Engine 4**: Shared UI pieces | I2 | `ui_components.py`: tool row, reasoning, plan, approval, question, engine chip; brand detection → `shared.py` | Engine 3 | S | No per-engine branches in turn drawing | |
| ☐ | **Engine 5**: Approval row | I3 | One approval model + inline row; "always" per session; prepare → apply for risky tools | Engine 4 | S | Same row for agent permissions, Safe mode and risky tools | |
| ☐ | **Engine 6**: Shared tools in the server | I5 | MCP tools `update_plan`, `ask_user`, scene checkpoint (auto at turn start), `get_scene_digest(since)` | Engine 2 | M | An external agent updates the plan, asks with buttons; digest says `unchanged` on repeat | |
| ☐ | **Engine 7**: Load tools only when needed | I4 | Surface tools listed, domains loaded on demand (`tools/list_changed`), full list as fallback. **Saves up to ~14K tokens per request** for external agents | Engine 6 | M | First request carries surface tools only (measured) | |
| ☐ | **Engine 8**: Session token | I6 | MCP + bridge require a per-start token; Copy MCP Config includes it | — | S | Tokenless request refused; every mode still works | |
| ☐ | **Engine 9**: Short tool results | I7 | Big results become a short preview + `read_tool_output` handle | — | S | 50 KB node dump → ≤ 2 KB preview on every engine | |
| ☐ | **Engine 10**: Benchmarks on every engine | I8 | Benchmark steps go through the engine, so the same set runs on Local, Remote, External Agent; Compare groups by mode | Engine 2, Agent 4 | S | One suite side by side on three engines | |

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

## Chat features — explain, translate, ask, undo, sessions

Plan: `plan_tier4b_competitor_ux_analysis.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Chat 1**: Native markdown, 5.3 minimum | B0 | Draw chat text with Blender's `label_markdown()`; keep Copy rows per code block, tables as aligned text, math; delete our renderer; minimum 5.3. **Blocked**: not in the 5.3 dev build yet | Engine 3 | M | Replies draw natively on Blender + Bforartists 5.3 | |
| ☐ | **Chat 2**: Explain this | B1 | Right-click a property, node or object → a short answer from the bundled docs, with the source; `/explain` | — | M | Property, node, object each give a cited answer on all engines | |
| ☐ | **Chat 3**: Translate text in the scene (#42) | B1 | Right-click a text field, 3D text or text strip → **Translate with Coworker…** → language (remembered) → one small model call → confirm → written back as one undo step. Fast on light models; no full agent turn | — | S | A 3D text, a strip and a node label translate and undo cleanly | |
| ☐ | **Chat 4**: "Where am I" chip | B6 | A line above the input (editor · mode · selection), read live at send time | — | S | Same prompt in two editors picks different correct tools | |
| ☐ | **Chat 5**: Questions as buttons | B2 | `ask_user` choices as buttons; the suggested next step as [Do it] [Something else] | Engine 6 | S | A click resumes the same turn on all engines | |
| ☐ | **Chat 6**: Undo the agent's turn | B4 | Revert turn (chat + scene), Edit & resend, Retry | Engine 6 | M | Revert restores chat + scene (Local), scene + note (External) | |
| ☐ | **Chat 7**: Sessions browser | B3 | Search, rename, delete, pin; resume brief; optional plan file next to the `.blend` | Agent 6 for External | M | Yesterday's session continues on each engine | |
| ☐ | **Chat 8**: Run & fix shown code | B5 | Run button on code blocks (Ask mode) with a safety scan, undo, and Fix | — | M | Broken snippet → Run → Fix → Run works | |
| ☐ | **Chat 9**: Keep docs honest | B7 | Docs and skills record their sources; a checker flags drift; review the pre-5.3 skills | Chat 1, Skills 4 | S | Drifted docs listed; old version skills trimmed or justified | |

## Agent runtime — safety, skills, speed

Plan: `plan_tier4f_agent_intelligence.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☐ | **Runtime 1**: Safe mode & loop guard | F1 | Trusted / Safe / Strict, path guard, stop the same call repeating 3× | Engine 5 | M | Safe mode asks before running code; 3× repeat escalates | |
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
| ☐ | **Text Editor 5**: Prompt templates | C5 | File-based templates per editor | Text Editor 1 | S | A user template appears and works | |

## Editors everywhere — Coworker editor and sidebars (#26)

Plan: `plan_tier4a_editor_integration.md`

| ☐ | Task | Ref | What | Needs | Size | Done when | PR |
|---|---|---|---|---|---|---|---|
| ☑ | **Editors 1**: Coworker editor spike | A0 | Three throwaway prototypes. Your preference order: (1) Preferences-editor clone with Python panels in the center, if it can't leak into your real Preferences; (2) Text / Node editor clone with the chat in native panels; a custom node-tree type gets its own Editor Type entry; (3) GPU drawing last | — | S | Screenshots + recommendation on #26 | |
| ☐ | **Editors 2**: One chat component | A1 | `draw_chat_interface()` that every panel uses | Engine 4 | M | Existing panels use it; no visual change | |
| ☐ | **Editors 3**: Chat in every sidebar | A2 | *Moved to **GPU 5** (compact sidebars), 2026-10-19* | — | — | — | |
| ☐ | **Editors 4**: The Coworker editor | A3 | *Moved to **GPU 4** (the editor around the GPU chat), 2026-10-19* | — | — | — | |
| ☐ | **Editors 5**: Viewport status | A4 | Status dot + focus highlight in theme colours (toggle) | Engine 3 | S | Overlay follows the agent | |
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
| ☑ | **Skills 1**: Template tools work everywhere | S0 | Registry moved into the server; `list_blender_templates` crash fixed | — | S | Both tools work in a build and a checkout | #109 (#110) |
| ☑ | **Skills 2**: Nodes on 5.x | S1 | Compositor via `compositing_node_group` in four tools; reads never edit; no `use_nodes` advice | — | S | Live on 5.3: read works, a read without a compositor changes nothing | #109 |
| ☑ | **Skills 3**: Needs list | S2 | Written in the plan | — | S | Done | — |
| ☐ | **Skills 4**: API probes | S3 | Every skill claim gets a probe that runs inside Bforartists | — | S | Probes pass on 5.3; a removed property fails a probe | |
| ☐ | **Skills 5**: Skill format | S4 | Front matter, size budget, references on request; a checker | Skills 4 | S | Checker green; Runtime 2 can build its index | |
| ☐ | **Skills 6**: New chapters | S5 | 5.x changes, data over operators, compositor, bone collections, render settings, bulk mesh access, "finish with evidence" | Skills 4, 5 | M | Every claim has a probe; quick challenges no worse | |
| ☐ | **Skills 7**: Mesh quality check | S6 | `check_mesh_quality`: overlaps, loose parts, open edges, flipped normals, unapplied scale | — | S | Finds each seeded fault | |
| ☐ | **Skills 8**: Docs for skill packs | S7 | Tool-name stability; using other skill packs with External Harness | — | S | Wiki page reviewed | |

## Agent teams & critics — orchestration layer (#71)

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
| ☑ | **GPU 0**: Spike | — | Prototype both sizes in Bforartists 5.3 with simulated typing | — | S | Go/no-go + screenshots | (this PR) |
| ☐ | **GPU 1**: Toolkit | — | `ui_gpu/`: theme reader (colours, roundness, UI scale × zoom), fonts (UI + mono), text measuring and wrapping, boxes, buttons, hover/press, hit areas. Layout bpy-free and unit-tested; thin `gpu`/`blf` layer | — | M | A test page draws buttons and wrapped text in the default and a light theme | |
| ☐ | **GPU 2**: Chat view | — | Turns from the chat state (your message, Workshop collapsible, tool rows, reply), our own markdown subset (bold, italic, code, lists, headings, links), code blocks with Copy (Run later), live streaming, follow the newest, scroll | GPU 1, Engine 3 | M | A recorded turn draws like the panel does today, in both sizes | |
| ☐ | **GPU 3**: Input box | — | Click to focus / Esc to leave, multi-line, **Enter sends, Shift+Enter new line**, cursor + selection, copy/paste, undo, **`@` suggestions** while typing, attachment chip, Send/Stop, "edit in a native box" for IME, restart after opening a file | GPU 1 | M | Type, edit, `@` and send without the mouse; node/viewport shortcuts never fire while focused | |
| ☐ | **GPU 4**: The Coworker editor | A3 | Register the "Coworker" editor type; one-click setup; full chat in the center; toolshelf icon actions (templates, explain, wizards); sidebar panels (session, model, plan); header | GPU 2, 3, Editors 1 | M | Pick "Coworker" in the Editor Type menu and chat | |
| ☐ | **GPU 5**: Compact sidebars | A2 | The 3D View "Coworker" tab draws the compact chat (replaces today's sidebar drawing), then Node, VSE and Text editors; native scrolling list as fallback where painting misbehaves | GPU 2, 3 | M | Same conversation, same look in the editor and in four sidebars | |
| ☐ | **GPU 6**: Guided flows | — | The same toolkit for question buttons (Chat 5), Explain answers (Chat 2) and step-by-step wizards in the chat | GPU 4 | M | A three-step wizard runs inside the chat | |

