# Tier 4b — Chat UX: What We Take From the Field, and What We Build

**Revised**: 2026-10-16 (audit against v1.1.37 + `main` @ deb417b)
**Status**: 📝 Ready for review — no code started
**Previous version**: `Plans History/plan_tier4b_competitor_ux_analysis_v1.md` (full
competitor deep-dives, kept as the research record)
**Master plan**: `plan_tier4_master_coordination.md` · **Tasks**: `plan_tier4_task_board.md` (lane B)

---

## 1. The task of Tier 4b, in one paragraph

Tier 4b makes the **chat panel the one place where an artist works with the agent**,
whichever engine is behind it: local llama-server, a remote API, or an external agent
(Claude Code, Codex, Gemini, OpenCode) over ACP (Tier 4h). It fills the gaps that remain
after Tier 3: **explain what I'm pointing at**, **ask me instead of guessing**,
**let me undo the agent's last turn**, **let me find and resume old sessions**, and
**let me run or fix code the agent only showed me**. Each item has to work the same way
on all three engines and must cost few tokens. Anything that only competes on the list
of features, with no gain for the user, is left out.

**Done when** an artist can (1) right-click anything and get a docs-grounded answer,
(2) answer the agent's questions with one click, (3) revert a turn including its scene
changes, (4) reopen last week's session and continue, on Local, Remote *and* External
Agent, with the same panel.

---

## 2. Audit: what v1 of this plan proposed vs. what has shipped

Half of the v1 phase list has landed during Tier 3, so v1's "Now" column is out of date.

| v1 phase | Feature | Status on `main` | Where |
|---|---|---|---|
| (P1, deferred) | Markdown rendering | ✅ Shipped (headings, lists, tables, code blocks + Copy) | `ui_chat.py` `_render_markdown` |
| 1 | Token streaming (SSE) | ✅ Shipped | `llm_transport.py`, `agent_controller.py` |
| 6 | Image / screenshot input | ✅ Shipped: image socket, open, capture render/screen, drag-and-drop, sticky or send-once | `chat_attachments.py`, #88/#90/#91 |
| 7 | Token budget + readout | ✅ Shipped: per-turn/session usage, context indicator, self-calibrating budget, cost ledger | issue #69, Tier 3i.0, Tier 3k |
| 9 | Checkpoint / context flush | ✅ Shipped: compaction, session memory, titled checkpoints, one-click restore | `session_memory.py`, Tier 3k |
| — | Goal & Plan, stop rules | ✅ Shipped (was not in v1) | `goal_plan.py`, Tier 3k |
| 4 | Session history | 🟡 Partial: versioned history (last 10) + History panel; no browse/rename/search | `ui_chat.py` |
| 8 | Per-message actions | 🟡 Partial: Copy only | `BFACW_OT_copy_message` |
| 2 | Code blocks + Run | 🟡 Partial: Copy only, no Run | `BFACW_OT_copy_code_block` |
| 3 | Error → Fix loop | ❌ Not started (exists inside the agent loop, not for shown code) | — |
| 5 | Right-click Explain | ❌ Not started | — |
| — | Translation | ❌ Not started | master §5 |
| — | CHOYA next-step buttons | ❌ Not started (3k makes the agent *end with one suggested next step as a question* — not clickable yet) | master §3 |

**Correction to v1:** v1's matrix marked Coworker "❌ Markdown, ❌ token counter, ❌ file
attachments, ❌ external client config". All four are now ✅ (Copy MCP Config + harness
presets cover client config).

---

## 3. Landscape at a glance

One row per product. Columns are what matters to us; detail is in the v1 archive.

| Product | Kind | Engine | Acts on scene? | Best idea for us |
|---|---|---|---|---|
| **Chat Companion** | Chat + code run | Remote + LM Studio/Ollama | Code only | Right-click "What's this?"; Run → Fix loop on shown code |
| **BlendAI** | Polished chat | Remote (credits) | Code only | Explain popup with follow-up reply; per-message Edit/Remove; script presets per editor |
| **Blender Buddy** | Local-first chat | Bundled llama.cpp | Search tools only | Zero-config local setup; run-safety scan before Run; area-picker capture |
| **BlenderMCP Pro** | Agentic (75 tools) | Remote + Ollama | ✅ | Session CRUD; project memory; macros; scene co-pilot; render critic; cost readout |
| **BuddyCode GPT** | Text Editor helper | LM Studio/Ollama/Gemini | Code only | Batch prompts; doc retrieval (Tier 4g.6.4/T5) |
| **claude-in-blender** | Claude Code bridge | User's Claude subscription | ✅ (MCP) | **Context toggles that read live state at check time**; strict MCP isolation; "Send = approval" |
| **MCP for Blender** *(case study: "I Just Wanted to Fish")* | MCP server, driven by Claude Code | User's subscription | ✅ | **Scripts as re-runnable recipes**; per-feature `PLAN.md` resume; **multi-angle verification renders**; agent asks short multiple-choice questions; long jobs in a background process |
| **Scenario for Blender** | Generative + local MCP | Scenario cloud | ✅ (MCP) | **Prepare → approve → apply** handles (approval invalidated by scene change); quote before spend; authenticated loopback MCP; Python execution off by default; doc-drift checker |
| **BFA Coworker (us)** | Agentic + chat | Local, Remote, External | ✅ (~45 tools) | — |

**Note on Scenario's skills folder.** `.agents/skills/` holds *developer* skills (PR
summary, squash message, artifact download, knowledge refresh), not artist-facing Blender
skills. The transferable idea is the **knowledge-refresh discipline**: every doc records
its `sources` and `reviewed_revision`, and a checker flags drift when sources change. We
should apply it to our version skills (`skills/blender_5x.md`) and wiki (task B9).

### 3.1 What the fishing case study tells us (it is the closest thing to our target user)

A solo developer, one Claude Code session, Blender driven only over MCP, a full game's
assets in ~10 days. What made it work, in our terms:

| Observed | Why it worked | What Coworker does with it |
|---|---|---|
| Every asset is a **chain of scripts** (build → rig → bake → animate → export); changes = edit a number, rerun | The agent never regenerates 300 lines to move a claw; tokens go to *deltas* | **Recipe pattern**: agent writes parametrised text blocks and reruns them (needs 4g.2 text tools; task G2/C4) |
| `PLAN.md` per feature, "continue with the crabs" resumes | Durable, human-readable intent outside the chat | Goal & Plan already exists per session; add **project plans** saved beside the `.blend` (task B3) |
| Agent asks **short multiple-choice questions** on real design decisions | User answers in seconds; no guessing spirals | `ask_user` choices rendered as buttons in the chat (task B2), for every engine |
| Renders **from below, behind, game camera** caught defects | Vision review of *specific* views beats one hero shot | `render_review_sheet`: one contact-sheet image of N angles = one image's tokens (task G5) |
| Long bakes exceeded one MCP call's timeout → background Blender process | Tool calls must not block | Our `deferred_tool.py` holds connections up to 1h; document + extend to background jobs (task G6) |
| "Describe the feel, not the build steps" | Model chooses technique | Prompt guidance in the skills; no code |

---

## 4. White-room rules (how we take ideas without taking code)

Several references are GPL like us, so copying would be *legal*, but we want our own
design and a clean provenance record. These rules apply to every Tier 4 task:

1. **Allowed inputs**: public product pages, docs, videos, case studies, and our own
   observation of how a product *behaves*. Write down the **user need**, not their
   mechanism.
2. **Not allowed**: copying or line-by-line porting of code, prompts, skill texts, UI
   strings, icons or test fixtures from any other project, whatever its licence.
3. **Implement from first sources**: the Blender/Bforartists API and manual (bundled
   in `mcp/blmcp/data`), the ACP / MCP specifications.
4. **Record it**: each borrowed idea appears in §5 below as *need → our design*, and
   PR descriptions state "no third-party code consulted while implementing".
5. **Prefer native Blender APIs** over our own copies of common UI. With 5.3 as the
   minimum, chat text uses Blender's own `layout.label_markdown()` (B0).

---

## 5. The Tier 4b feature set (sharpened)

Each item: the **need**, **our design**, how it behaves on each **engine**, and its
**token cost**. Order = build order.

### B1 — Explain this (right-click, docs-grounded) + Translate

- **Need**: "What is this button / node / modifier, and when would I use it?" without leaving Blender.
- **Design**: `Explain with Coworker` on the button context menu, object, node and
  Outliner context menus. The operator collects a compact **target card** (RNA path,
  label, tooltip, type, current value, editor). It sends an Ask-mode turn with a fixed
  answer shape: what → how → when → pitfalls → *try this*. It must call
  `search_manual_docs`/`search_api_docs` first and cite the section; when nothing is
  found it says so. `Translate with Coworker` uses the same plumbing with a target
  language preference.
- **Engines**: Local/Remote run it as an Ask turn. External Agent sends the same prompt;
  the docs tools are already in our MCP server.
- **Tokens**: one target card (~150 tokens) + one doc hit, instead of a scene dump. Ask
  mode skips the tool schemas.
- **Done when**: right-clicking a property, a node and an object each gives a cited
  answer on all three engines.

### B2 — Ask me, don't guess: choice buttons + next step

- **Need**: the agent should ask quick questions on real decisions, and the artist
  should reach the next step in one click (this merges the v1 "CHOYA" idea).
- **Design**: one **`ask_user(question, choices[], allow_text)`** tool, served by *our
  MCP server*. It renders as a button row in the chat and holds the call open until the
  user clicks, using the deferred-tool pattern. The stop rules already make the agent end
  a turn with one suggested next step; render it as a **[Do it] [Something else]** pair
  instead of plain text.
- **Engines**: identical on all three, because the tool lives in the MCP server: Claude
  Code over ACP calls it like any other tool. It even works for a passive external
  client, since the question appears in Blender.
- **Tokens**: saves whole wrong turns; the tool costs one small schema.
- **Done when**: a turn with a real design choice shows buttons, and the click resumes
  the same turn.

### B3 — Sessions you can find again (+ project plans)

- **Need**: browse, search, rename, delete and resume past sessions; resume a feature
  across days.
- **Design**: a session index over the existing versioned history, with an auto-title
  (from the checkpoint titles 3k already writes), date, engine chip, and Load / Rename /
  Delete / Pin. Resuming sends a **resume brief** (Goal & Plan + memory block), never the
  raw transcript. Optional **project plan**: Goal & Plan can be saved as
  `<blend>_coworker_plan.md` next to the `.blend`, so a new session starts with
  "continue with…".
- **Engines**: Local/Remote as above. External Agent stores the ACP `sessionId`. It uses
  `session/load` when the agent supports it, otherwise a new session seeded with the
  resume brief.
- **Tokens**: the resume brief is a few hundred tokens, compared with a transcript that
  can reach tens of thousands.
- **Done when**: a session from yesterday reopens and continues on each engine.

### B4 — Undo the agent's last turn (scene included)

- **Need**: "That was wrong, take it back" with one click, without clearing the thread.
- **Design**: per-message actions: **Revert turn** (drops the pair from history *and*
  restores the scene to the turn-start checkpoint that co-work/auto-undo already
  records), **Edit & resend** (fills the input with the message), **Retry**. Copy stays.
- **Engines**: scene revert is Blender-side, so the same everywhere. The history part
  is local for Local/Remote; for an ACP agent we cannot delete its memory, so we revert
  the scene and send "the user reverted your last turn" as a short note.
- **Tokens**: a revert removes the bad turn from context instead of arguing with it.
- **Done when**: Revert restores both the chat and the scene on Local, and the scene plus
  a note on External.

### B5 — Run / Fix code that was only shown (Ask mode)

- **Need**: in Ask mode, or when the agent prints a snippet, run it safely and get it fixed.
- **Design**: a **Run** button on python code blocks. Before running, a safety scan
  reports `os/subprocess/socket/eval/exec`, file writes outside the project, and unknown
  `bpy` identifiers (the server-side preflight already has most of these checks; reuse
  them, don't duplicate). Each run pushes an undo step. On error, **Fix with Coworker**
  sends code + traceback tail as a new turn. "Trust for this session" is available.
- **Engines**: same everywhere. Execution goes through our bridge, not the agent.
- **Tokens**: the traceback tail only (≤ 20 lines), not the full log.
- **Done when**: a broken snippet → Run → Fix → Run succeeds.

### B6 — "Where am I" context chip

- **Need**: answers that fit the editor I'm in, without me describing it.
- **Design**: a one-line context chip above the input (*3D Viewport · Edit Mode · Cube
  selected*), sent as ~30 tokens with each request and toggleable. It is read **live at
  send time**, like claude-in-blender's toggles, not from a stale snapshot.
- **Done when**: "make this smoother" in the Shader Editor vs. Edit Mode gets different
  correct tools.

### B0 — Native markdown, minimum version 5.3

- **Need**: replies that look like the rest of Blender, with less code for us to maintain.
- **Design**: set the minimum to Blender/Bforartists **5.3** and draw all chat text
  (replies, Workshop, Explain answers) with `layout.label_markdown(text=...)` (PR
  #163254: headers, paragraphs, bold/italic/monospace, links, code blocks, inline code,
  lists). Delete our own renderer and the pre-5.3 fallbacks. Three thin layers fill
  the native gaps:
  1. **Segmenter**: split a reply at fenced code blocks so each block is drawn natively
     *and* keeps its Copy (later Run, B5) button row.
  2. **Tables**: the native API has none, so convert them to an aligned monospace block.
  3. **Math**: keep the LaTeX→Unicode pre-pass.
- **Engines**: identical (pure UI).
- **Done when**: Blender 5.3 and Bforartists 5.3 show native-drawn replies with working
  Copy rows and readable tables, and the old renderer is gone.

### B7 — Doc-drift records

- `sources` + `reviewed_revision` for the version skills and the wiki pages this lane
  touches, plus a small checker. With 5.3 as the minimum, review whether the pre-5.3
  version skills are still needed.

---

## 6. Feature matrix: Coworker before and after Tier 4

Rows are user needs. **Ref** = who does it best today (initials: CC Chat Companion, BA
BlendAI, BB Blender Buddy, MP BlenderMCP Pro, CB claude-in-blender, MB MCP for Blender,
SC Scenario).

| User need | Ref | Coworker now | After Tier 4 | Task |
|---|---|---|---|---|
| **Talk to the agent** | | | | |
| Chat in the 3D Viewport sidebar | all | ✅ | ✅ | — |
| Chat in other editors / dedicated editor | BB | 🟡 3D View + Text Editor | ✅ every sidebar + Coworker editor | 4a |
| Use my own Claude/ChatGPT/Gemini subscription, chat in Blender | CB | ❌ (external drives, chat lives elsewhere) | ✅ External Agent (ACP) | **4h** |
| Same panel and features on Local / Remote / External | — | ❌ | ✅ | **4i** |
| Live streaming + thinking display | BB | ✅ | ✅ | — |
| Attach images / screen captures | BB, MP | ✅ | ✅ (+ review sheets) | G5 |
| **Understand** | | | | |
| Readable answers (markdown) | BB | ✅ own renderer | ✅ native `label_markdown` (5.3 minimum) | B0 |
| Right-click "explain this", docs-grounded | CC, BA | ❌ | ✅ | B1 |
| Translate a label | — | ❌ | ✅ | B1 |
| Knows which editor/mode I'm in | BA | 🟡 scene snapshot only | ✅ live context chip | B6 |
| **Decide & control** | | | | |
| Agent asks multiple-choice questions | MB | ❌ | ✅ `ask_user` buttons | B2 |
| One-click next step | — | 🟡 text suggestion | ✅ button | B2 |
| Approve risky actions inline | SC, CB | ❌ | ✅ one approval row (ACP + local permissions + prepare/apply) | 4i / 4f.3 |
| Stop mid-turn | all | ✅ | ✅ (also cancels ACP) | 4h |
| Undo the agent's last turn incl. scene | MP (mission undo) | 🟡 auto-undo on retry only | ✅ Revert turn | B4 |
| Edit & resend / Retry | BA | ❌ | ✅ | B4 |
| Run & fix shown code safely | CC, BB | 🟡 Copy only | ✅ Run + scan + Fix | B5 |
| **Remember** | | | | |
| Goal & Plan pinned, ticks live | MB (PLAN.md) | ✅ | ✅ (+ project plan file) | B3 |
| Long sessions stay sharp (compaction/checkpoints) | MP | ✅ | ✅ | — |
| Browse / search / resume sessions | MP, BA | 🟡 last 10, no browser | ✅ | B3 |
| Project memory / rules | MP | ✅ | ✅ | — |
| **Do more with fewer tokens** | | | | |
| Domain tools instead of raw `bpy` | MP | 🟡 ~45 tools | ✅ +VSE/Text/Node/Save/Rig/Anim | 4g/4e |
| Scripts as re-runnable recipes | MB | ❌ | ✅ | G2/C4 |
| Progressive tool loading for *every* client | — | 🟡 local/remote only | ✅ server-side | 4i |
| Token / cost readout | BB, MP | ✅ local/remote | ✅ + agent-reported usage on External | 4h |
| **Setup & trust** | | | | |
| One-click local setup + model download | BB | ✅ (+ installer #86) | ✅ | — |
| Updates through Blender's extension system | SC | ✅ (#86) | ✅ | — |
| MCP server protected from other local processes | SC | ❌ open loopback | ✅ session token | 4i |
| Python execution can be gated | SC | ❌ | ✅ (setting, default per engine) | 4f.3 |

**Deliberately not in Tier 4** (and where they went): popup/quick chat → 4a floating
window or T5 · macros / script presets → T5 (design in master §6) · voice in / TTS → T5 ·
provider auto-fallback → T5 · scene co-pilot, render critic → 4g.6 · agent teams → 4f.4 ·
cross-DCC bridge → out of scope · batch prompts → T5.

---

## 7. Build order and dependencies

```
B0 native markdown + 5.3 minimum ─────────────────────────────── (first; B5 Run rows build on its segmenter)
B7 doc drift ── after B0
4i engine seam (shared events + approval row) ──► B2, B4-external, B3-external
B1 Explain/Translate ── independent (uses existing doc tools)
B2 ask_user ── needs MCP deferred-tool path (exists) + chat button row
B3 Sessions ── independent locally; ACP part after 4h.6
B4 Revert/Edit/Retry ── needs turn-start checkpoint id on each turn (co-work guard has it)
B5 Run/Fix ── reuses server preflight
B6 Context chip ── independent
```

Recommended order: **B0 → B1 → B2 → B4 → B3 → B6 → B5 → B7**. B1 and B2 give the biggest
visible gain for the least work. B2 and B4 also prove the engine-neutral design that
Tier 4h needs. B5 goes last because Agent mode already executes code itself.

---

## 8. Open questions for review

| # | Question | Recommendation |
|---|---|---|
| 1 | Keep Run on code blocks in **Agent** mode, or Ask mode only? | Ask mode only. In Agent mode the agent runs code itself, so a Run button would split responsibility. |
| 2 | Revert turn on External Agent: scene-only + note acceptable? | Yes. Say clearly in the tooltip that the agent keeps its own memory. |
| 3 | Project plan file beside the `.blend` by default? | Off by default, one toggle. Some users don't want extra files next to their work. |
| 4 | Explain answers: in the chat thread or a popover? | In the thread (searchable, follow-ups work); Shift+click popover deferred to T5. |
