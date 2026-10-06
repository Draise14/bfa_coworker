# BFA Coworker — Tier 6: Mixar-Inspired Agent & Connector Parity

**Date**: 2026-10-04
**Status**: 📋 Planning — Research Complete
**Depends on**: 🧱 Tier 4b/4g (chat UX, domain tooling), Tier 5 (generative local systems, ComfyUI bridge), Tier 6a (generative 3D systems), Tier 6b (viewport diffusion renderer)
**Philosophy**: 🏠 Local-first, out-of-the-box, Blender-native UI. Remote/BYOK is an optional add-on, never a requirement.
**Subject**: 🔎 Mixar (https://www.mixar.app) — release analysis and integration mapping.

---

## Table of Contents

- 🎯 [1. Executive Summary](#1-executive-summary)
- 🧭 [2. Method, Sources, and Confidence](#2-method-sources-and-confidence)
- 🧩 [3. What Mixar Is](#3-what-mixar-is)
- 🗺️ [4. Mixar Release Map](#4-mixar-release-map)
- 🏗️ [5. How Mixar Built It](#5-how-mixar-built-it)
- 🧰 [6. What Mixar's Tools Are](#6-what-mixars-tools-are)
- ✨ [7. UX Patterns Worth Adopting](#7-ux-patterns-worth-adopting)
- ✅ [8. Coworker Today — What We Already Have](#8-coworker-today--what-we-already-have)
- 🧮 [9. Gap Analysis](#9-gap-analysis)
- 🗂️ [10. Tier Mapping — Where Each Idea Lands](#10-tier-mapping--where-each-idea-lands)
- 🛠️ [11. Proposed Tier 6 Plan](#11-proposed-tier-6-plan)
- 🏠 [12. Local-First Translation](#12-local-first-translation)
- ⚖️ [13. Key Decisions](#13-key-decisions)
- 🚫 [14. Non-Goals](#14-non-goals)
- ⚠️ [15. Risks and Open Questions](#15-risks-and-open-questions)
- 📚 [16. Sources](#16-sources)
- 🏁 [Summary](#summary)

---

## 1. Executive Summary

Mixar is a **Blender 5.2 fork shipped as its own application** with an AI agent ("Mixie")
running **inside the editor process**, plus a layer-based texture paint system, a moodboard,
an embedded asset index, orchestrated generation, and a polished MCP connector so *external*
AI apps can drive the same scene.

Mixar's marketing headline is *"Concept to final render without switching software."* Its
engineering argument (from their own comparison page) is narrower and more useful to us:

> "Blender MCP is a bridge. Mixar is an editor with the agent inside it… there is no protocol
> between the agent and the scene because there is nothing to bridge."

BFA Coworker is already an **in-editor** add-on — so we start ahead on that axis. But three
gaps are real and worth closing:

1. **Our own agent still talks to the scene through a loopback MCP HTTP bridge.** The built-in
   agent calls `http://127.0.0.1:9191` tools. That is a self-imposed round-trip Mixar removed.
2. **Mixar's agent *UX* is a first-class product**: Plan Mode (draft → approve → revise),
   per-turn checkpoints, per-tab undo, an opt-in UI-control layer, and a connector dialog that
   sets up any MCP client in one click.
3. **Mixar makes local models zero-setup and asset reuse semantic** — pinned `llama.cpp` +
   curated GGUF with RAM-fit selection, plus a rendered-preview embedding index the agent
   searches *before* it models. Both map cleanly onto our local-first stack.

**Recommendation:** file this as **Tier 6c** (alongside 6a generative 3D, 6b viewport diffusion),
and implement it as **eight phases** (6c.1–6c.8). Everything is achievable with the stack we
already committed to (llama.cpp, ComfyUI, local 3D models); nothing here requires a Mixar
account or any hosted service. Remote providers stay optional via the existing BYOK path.

---

## 2. Method, Sources, and Confidence

**Verified from primary sources** (repo, docs, product pages):

- Repo layout, licence (GPL-3.0-or-later), Blender 5.2 fork, overlay build pattern, `VERSION` = `4.2.2`.
- The complete `src/scripts/mixar/modules/` inventory (33 modules).
- `mcp_bridge/` internals: `README.md`, `tool_snapshot.py`, and the stdio launcher `mcp.py`.
- Named MCP tools appearing in Mixar's own `mcp_bridge/README.md`.
- `local_models/README.md` (zero-setup local LLM: pinned llama.cpp + GGUF, Ollama/LM Studio/oMLX detection).
- Release branch history `v2.0.0 … v4.2.2` and the 100-branch feature inventory.
- Product pages: `/blender-agent`, `/compare/mixar-vs-blender-mcp`, `/docs`, `/blog/agent-asset-library`, homepage.

**Reported but not independently re-verified** (product guide pages, summarised):

- "sixteen tools" for UV work and "sixteen bake types".
- Per-pass workflow details for the nine specialist guides.

**Not discoverable (do not invent):**

- Mixar's **full backend tool list** — the tool schemas are served by their closed-source
  hosted backend and cached client-side in `tools.json`; the repo only ships the client.
- The live **changelog** and **pricing** tables (client-rendered from the backend; no static data).
- Exact credit prices.

> ℹ️ Confidence note: the website hero still reads "v3.2 Out Now!" while the repo `VERSION` is
> **4.2.2** and release branches run to `release/v4.2.2`. Treat **4.2.2** as the source-verified
> latest; treat "v3.2" as stale marketing copy.

---

## 3. What Mixar Is

| Aspect | Mixar |
|---|---|
| Nature | Custom **fork of Blender 5.2**, shipped as its own desktop app (Win/macOS/Linux) |
| Licence | GPL-3.0-or-later (Blender-derived files GPL-2.0-or-later); paint module seeded by ucupaint |
| Agent | **Mixie**, runs **in-process** with the scene; reads live scene state |
| Tool surface | Split across **specialist agents**, each seeing a narrow, relevant tool set |
| Safety | **Plan Mode** toggle: drafts a step plan and waits for approval before touching the file |
| Generation | Orchestrated in-editor (text→image w/ refs, depth-guided render, image→3D, segmentation→mesh, whole-scene from one reference, PBR map gen, procedural material, auto-rig) |
| Texturing | Layer-based paint (ucupaint lineage): stacked layers, masks, modifiers, baking, UDIM, decals |
| Assets | Trained visual index (rendered previews + embeddings); semantic + image search; agent reuse |
| Extensibility | External AI apps connect via MCP (Claude Code/Codex/Cursor/VS Code/…); optional UI control |
| LLM providers | Hosted Mixar credits, **or BYOK** (OpenAI/Anthropic/Gemini, OpenRouter 400+, Codex sub), **or local** (managed llama.cpp / detected Ollama/LM Studio) |
| Backend | Hosted service (closed source) for agent + generation; desktop client is open source |

---

## 4. Mixar Release Map

### 🏷️ 4.1 Version history (verified via release branches)

```
v2.0.0  v2.1.0  v3.0.2  v3.0.7  v3.0.17
v3.1.0  v3.1.30 v3.1.34 v3.1.35 v3.1.46 v3.1.49 v3.1.57 v3.1.59
v3.2.0  v3.2.1
v3.3.1  v3.3.3  v3.3.4  v3.3.5  v3.3.6  v3.3.71
v3.4.0  v3.4.1  v3.4.2  v3.4.5
v4.0.0  v4.1.1  v4.1.2  v4.1.3  v4.2.0  v4.2.1  v4.2.2   ← latest (VERSION file)
```

Cadence is fast and continuous; the public repo mirrors `main`/`develop` and the release branches.

### 📋 4.2 Feature inventory (from the 100-branch list + recent merges)

This is the "what they have done" map, grouped by area. Branch names are Mixar's own.

**🧠 Agent & orchestration**
- `mixar-agent-harness`, `parallel-agents`, `parallel-agent-texturing`, `texturing-with-agent`
- `turn-checkpoints`, `per-tab-undo`, `operation-history`, `scene-forensics-logging`
- `async-agent-preview`, `agent-activity-images`, `agent-render-to-moodboard`
- `selection-assist`, `detect-multi-views-in-image`

**🔌 MCP / external clients**
- `mixar-mcp`, `mcp-ui-control`, `mcp-free-and-usage`, `agent-ui-control`
- Connector dialog: one-click **Add to Claude Code / Add to Codex**, per-app setup, `tools.json` snapshot

**💾 Local models & BYOK**
- `byok-local-provider`, `byok-dialog-seamless`, `platform-model-choice`
- `local_models` module: pinned `llama.cpp` + curated GGUF, RAM-fit ladder, resumable verified downloads, Ollama/LM Studio/oMLX detection

**🖼️ Assets**
- `asset-library`, `asset-library-search`, `gallery-newest-first`, `cat-catches-attachments`
- Training by rendered preview + embedding; "Library Mode" in chat; agent reuse + threshold slider

**🎨 Moodboard**
- `moodboard-ui-overhaul`, `moodboard-ui-rebuild`, `moodboard-sharing`, `moodboard-drawer-*`
- `moodboard-video-seedance-foundation`, `moodboard-video-node-duration`, `moodboard-add-mesh-node`,
  `moodboard-character-components`, `sam3-moodboard-segmentation`

**🖌️ Painting / texturing**
- `paint-armor-ucupaint-improvements`, `procedural-texturing-tools`,
  `750-pbr-gen-using-patina`, `784-patina-pipeline-with-agent`

**🧊 Modeling / 3D**
- `procedura-modeling`, `visculpt-mesh-editing`, `tripo-mesh-segmentation`, `splats-support`, `terrain`

**📐 CAD / gaming workflows**
- `agent-cad-cleanup`, `cad-video-worklow`, `gaming-workflow` 1/2/3, `agent-stl-export`

**🎬 Scene, camera, motion**
- `mixar-parallel-scenes`, `mixar-scenes-drawer-push`, `scenes-drawer-delete-ux`,
  `scene-to-video-camera-directing`, `virtual-camera-control`, `cinema-mode`, `director-explore-add-camera`

**🪟 Zen / UI**
- `zen-scene-toolbar`, `zen-guides-toggle`, `zen-motion-system`, `zen-transform-tool-toggles`,
  `zen-ui-list-navigation`, `zen-annotate-tool`, `dynamic-property-menu`, `liquid-glass`, `mixar-ui-reduce-motion`

**💬 Chat UX**
- `new-chat-ui`, `mixie-chat-ui-refresh`, `mixie-chat-mention-autocomplete`, `chat-history-ux`,
  `chat-prompt-history`, `compact-agent-bubble`, `scribble-instant-text`, `scribble-handwriting-input`,
  `mixie-instant-dictation`, `mixie-cloud-dictation`, `agent-bubble` / `agent-panel`

**🎞️ Generation & media**
- `minimax-h3-video-gen`, `scene-web-publish`, `generate-cost-and-board-utilities`

**📈 Account / growth**
- `credit-topups-without-subscription`, `credits-exhausted-banner`, `profile-usage-credits`,
  `profile-usage-summary`, `low-credit-referral-nudge`, `refer-a-friend`, `interactive-onboarding-tour`

**🖥️ Platform**
- `linux-build`, `cycles-performance`, `lore-project-versioning`, `qa-gui-harness`, `qa-gui-harness-v2`,
  `ux-observability`, `scene-web-publish`, `trial-device-guard`

### 🚀 4.3 What "latest release" emphasises

From the most recent merges (develop HEAD and the `mixar-blastoff` train, PRs #1649–#1792), the
current release cycle is dominated by:

- **MCP hardening & UX**: opt-in UI control, one-click app setup, survive File→New during a
  session, call-id receipts, free-vs-credit split, "any tool call may start Mixar; listing never does".
- **Onboarding**: narrated multi-language tour, founder take with Scenes + Cinema Mode beats.
- **Chat polish**: attach folders as context, image drops anywhere on the agent island, step rows
  keyed by call id, WebSocket reconnect, resume prompt.
- **Stability**: render-job races, off-thread `bpy` walks, in-draw bubble resize, Windows splash hang,
  Cycles engine resolution.
- **Parallel scenes**: scene drawer, per-session client state, fair script lanes.

---

## 5. How Mixar Built It

### 🧱 5.1 Blender fork + overlay build (not an add-on)

```
upstream/   Blender 5.2 source (git submodule)
src/        Mixar overlay — Python addon + C++ additions
source/     Generated tree: upstream copied, then src rsync'd on top
build/<env>/  CMake build directory (MIXAR_ENV=Prod|Dev)
```

`make init` → `make build`; `src/scripts/mixar/` is the Python addon, `src/source/blender/` adds
C++ editor spaces and the paint kernel. Upgrades from upstream Blender stay clean because the
overlay is re-applied. This is **the opposite of our strategy** and deliberately so: we ship as an
add-on so users keep their exact Bforartists install. We take the *ideas*, not the fork.

### 🧠 5.2 In-editor agent, not a bridge

Mixie runs in-process and reads object hierarchy, mesh statistics, transform state, normal
orientation, UV layers, material assignments and modifier stacks **directly**. Mixar's stated
failure modes of the bridge model — scene-state drift over long chains, per-step round-trip cost
(they count ~480 round trips for a 40-object pass), tool-call timeouts on heavy work — are exactly
the things we can reduce by giving our agent a **direct in-process tool path** while keeping MCP
for external clients.

### 🎛️ 5.3 Specialist routing (narrow tool surfaces)

Work is routed to specialists, each seeing only its tools: *modelling, modelling edits,
environments, terrain, texturing, UV unwrapping, UV layout, rigging, animation, camera, lighting,
rendering, scene administration.* Rationale they give: "a UV task never has to reason past a
rigging tool it will not use."

> **We already have the primitive.** `agent_controller.py` has `_detect_domains(prompt)`,
> `_detect_domain_from_scene()`, `_TOOL_DOMAINS`, `_SURFACE_TOOLS` and a synthetic `load_tools`
> tool. Mixar's contribution is *coverage* (13 domains incl. UV/rig/terrain/camera/admin) and
> treating the split as a product principle rather than a token-saving heuristic.

### 🧾 5.4 Plan Mode (approval as a tool property)

A first-class toggle: the agent analyses the request, drafts a step-by-step plan, and **waits**.
Approve, or give feedback and it revises. Nothing touches the file until approved. Mixar pairs it
with **turn checkpoints** and **per-tab undo**, so "that whole pass it just did" is one action to
reverse.

> **We have a seed:** `execute_blender_plan` (two-phase: plan → tested code) and an ask-mode
> (`_ASK_MODE_PROMPT_ADDENDUM`, `_PERMISSION_ASK_RE`). Tier 6c generalises these into a session-wide
> Plan Mode + checkpoint/rollback.

### 🔌 5.5 MCP connector (external clients, done properly)

- Per-user launcher: `~/.mixar/connector/mixar-mcp` (`mixar-mcp.cmd` on Windows).
- stdio MCP server → local HTTP to `127.0.0.1` with a bearer token; **the config holds no token**.
- **Scene pinning**: each connection works in one scene tab; `mixar_scene_new` / `mixar_scene_switch` move it.
- **Projects**: `mixar_projects` / `mixar_project_open` reopen recent work; unsaved-changes prompt.
- **Tool snapshot** (`tools.json`): the backend tool list is cached so tools list even when Mixar is
  closed/offline; a call before readiness says why.
- **Call receipts**: every call carries a UUID; a timed-out mutation is never auto-replayed — the
  receipt is retrieved by id. `mixar_call_status` / `mixar_ui_call_status`.
- **Usage**: every call reports usage; `mixar_credit_balance` inspects balance.
- **UI control is opt-in**: default is scene tools only; ticking it adds see/click/type/drag, and
  "your own mouse or keyboard always takes control back."

### 💾 5.6 Zero-setup local models + BYOK

`modules/local_models/README.md` is the most directly reusable artefact for us:

- Downloads a **pinned `llama.cpp` `llama-server`** build and a **curated GGUF**, verified by
  SHA-256 + size; a failed verification is discarded, never executed.
- **Resumable** downloads (`.part` + `Range`, rolled SHA-256, `os.replace` after verify).
- Supervises the server on `127.0.0.1` with a per-install `--api-key`; health watch auto-restarts.
- **Detects servers the user already runs** (Ollama, LM Studio, oMLX, stock llama.cpp).
- A **RAM-fit ladder + recommendation** picks a model for the machine.
- Reached through the BYOK dialog → "Local (this computer)"; managed or custom mode.

### 🖼️ 5.7 Asset index (rendered previews + embeddings)

- Enrol libraries in the Assets workspace → **Train Model** renders a preview of each asset and
  embeds it into a visual index. Incremental re-index on change; unenrol removes entries.
- Search by **meaning and appearance** (text or reference image), not filename.
- Three surfaces: Assets-workspace search, **Library Mode in chat** (thumbnail cards; click places
  at 3D cursor; works offline), and **agent reuse while building** (clear match → place; ambiguous
  → thumbnail picker + "model from scratch"; **match-threshold slider**).
- Completed Image→3D / Model Gen outputs are archived into a **"Mixar Generations"** library automatically.
- Privacy: previews + metadata are uploaded to build the index; **`.blend` files stay local**; a
  delete control wipes the server-side index.

### ⚙️ 5.8 Job queue for heavy work

Heavy operations (remesh, 4K bake, generation) are **editor jobs** with their own timeouts, retry
handling and credit refund on terminal failure — so the editor stays responsive and the agent is
never blocked by a running render. (Recent commits explicitly fix "a running render never blocks
the agent again".)

---

## 6. What Mixar's Tools Are

### 📇 6.1 Named MCP tools (verified in `mcp_bridge/README.md`)

| Tool | Purpose |
|---|---|
| `mixar_scene_new` | Create a new scene tab (use instead of a script) |
| `mixar_scene_switch` | Move the connection to a scene tab |
| `mixar_projects` | List recently opened/saved projects |
| `mixar_project_open` | Reopen a recent project (asks before discarding unsaved changes) |
| `mixar_ui_context` | Report/select which Mixar process/UI context is driven |
| `mixar_call_status` | Retrieve the recorded outcome of a backend tool call by UUID |
| `mixar_ui_call_status` | Same, for native UI actions |
| `mixar_credit_balance` | Inspect available credits |
| `create_layered_material` | Create a layered material (costs credits — a generation call) |

### 🧩 6.2 Capability groups (from `/docs` and `/blender-agent`)

- **Inspect & build**: scene and geometry inspection, Blender scripting, materials and layers,
  UVs, animation via scripting, rendering and export.
- **Generation**: text→image with reference images, **depth-guided rendering from a viewport
  blockout**, image→3D, **image segmentation into separate meshes**, **whole-scene assembly from one
  reference**, **PBR map generation onto a selected mesh**, **procedural material generation**, **auto-rigging**.
- **Assets**: asset search and placement (semantic + image).
- **Local UI tools**: inspect visible controls, return window images, and operate observed controls
  through native clicks, keyboard input and bounded gestures (opt-in).

### 🔢 6.3 Reported tool counts

- **16 UV tools** (work *around* seam marking; flattening/packing automated fully).
- **16 bake types** exposed as a menu rather than a setup exercise.

### 🗃️ 6.4 Module → capability map (the real inventory)

| Module | Capability |
|---|---|
| `agent_panel`, `agent_bubble`, `space_mixie`, `space_mixie_chat` | Chat agent UI (panel, bubble, dedicated spaces) |
| `agent_viewport_lock` | Keep the agent's view stable while it works |
| `mixar-agent-harness` (branch) | Agent harness / tool execution |
| `scene_graph` | Live scene-state reader |
| `operation_history` | Operation history / undo context |
| `workflow` | Multi-step workflow orchestration |
| `director`, `virtual_camera`, `scene_render` | Camera direction, virtual camera, render jobs |
| `moodboard` | Reference boards, video nodes, segmentation |
| `paint`, `space_texture_sets`, `texel_density`, `uv_editor` | Layered painting, texture sets, texel density, UV editing |
| `mesh_segment`, `sculpt_agent` | Segmentation, sculpting assistance |
| `hunyuan` | Hunyuan 3D generation integration |
| `asset_search` | Embedding asset search |
| `byok`, `local_models`, `auth`, `connector`, `mcp_bridge` | Providers, local LLM, auth, MCP connector |
| `context_folder`, `plugin_import`, `addon_project` | Folder context, plugin import, add-on authoring |
| `scribble_mark` | Handwriting/scribble input |
| `referrals`, `onboarding`, `testing`, `common` | Growth, onboarding, QA, shared |

> The full backend tool schema (dozens of scene/generation/UI tools) lives in the closed-source
> backend and is **not enumerable** from the repo. What we can copy is the *shape*: narrow specialist
> sets, named scene-tab/project/status/balance tools, opt-in UI control, and per-call receipts.

---

## 7. UX Patterns Worth Adopting

Ranked by fit with "better integrated and consistent with the Blender interface":

1. **Plan Mode as a visible toggle** with an approve/revise step — not a hidden prompt convention.
2. **Turn checkpoints + per-turn undo** so a multi-object pass reverses in one action.
3. **Call receipts (UUID) instead of blind retries** for any long-running tool call.
4. **Heavy work as editor jobs**, never blocking the agent or the UI.
5. **Opt-in UI control**, with "user input always takes control back".
6. **Narrow specialist tool surfaces** with an explicit domain list.
7. **Asset reuse before generation** (search own library, then model), with a threshold the user owns.
8. **One-click MCP client setup** + per-app instructions + "config holds no token".
9. **Zero-setup local model manager** (pinned, verified, resumable, RAM-fit) and detection of servers
   the user already runs.
10. **Honest limits in the UI/docs** — Mixar documents where automation stops (deformation loops,
    seams, art direction). We should too.

---

## 8. Coworker Today — What We Already Have

| Capability | Where | Status |
|---|---|---|
| In-editor agent (add-on) | `agent_controller.py`, `ui_chat.py` | ✅ (advantage over the fork model) |
| Domain detection + narrow tool sets | `_detect_domains`, `_detect_domain_from_scene`, `_TOOL_DOMAINS`, `_SURFACE_TOOLS`, `load_tools` | ✅ (8 domains) |
| Two-phase plan → tested code | `execute_blender_plan` | ✅ (partial Plan Mode) |
| Ask-mode / permission prompt | `_ASK_MODE_PROMPT_ADDENDUM`, `_PERMISSION_ASK_RE` | ✅ (partial) |
| MCP tool surface (~75+ tools) | `mcp/blmcp/tools/` | ✅ |
| Loopback MCP server for external clients | `mcp_to_blender_server.py`, `agent_controller` (127.0.0.1:9191) | ✅ |
| Local LLM (llama.cpp) + remote + external harness | `llm_manager.py`, `llm_transport.py`, `preferences.py` | ✅ |
| Chat UX: markdown, reasoning, tool rows, history, session memory, message queue, @mentions, project rules | `ui_chat.py` | ✅ (strong) |
| Generation plugin system (image done; video/audio/text stubs) | `gen_plugins/`, `gen_controller.py` | ✅ 5a / ❌ 5b–5e |
| Gen 3D plan (TripoSR/TRELLIS/Hunyuan3D/PBR/retopo/SAM2/DUSt3R) | `plan_tier6_generative_3d_systems.md` | 📋 planned |
| Viewport diffusion renderer ("Dreamer") | `plan_tier6_viewport_diffusion_renderer.md` | 📋 planned |
| Moodboard | `plan_tier4d_moodboard_editor.md`, `plan_tier5_moodboard_storyboarding.md` | 📋 planned |
| Skills + project rules | `skills/`, `ui_chat.py` | ✅ |
| Sandbox for code execution | `weak_sandbox.py` | ✅ (basic) |

**Net:** we are not behind on capability breadth — we are behind on **agent-loop UX, connector
polish, local-model onboarding, and semantic asset reuse**.

---

## 9. Gap Analysis

| Mixar capability | Coworker today | Gap | Effort |
|---|---|---|---|
| Agent reads scene in-process | Agent calls loopback MCP HTTP | Add **direct in-process tool path** for the built-in agent | 🟡 M |
| 13 specialist domains | 8 domains | Extend to UV, rigging, terrain, camera, scene-admin, retopo, bake, texturing | 🟡 M |
| Plan Mode (session-wide) | `execute_blender_plan` (single call) | Generalise to a toggle + plan review UI | 🟡 M |
| Turn checkpoints / per-turn undo | None (Blender undo only) | Snapshot + rollback per turn | 🟡 M |
| Call receipts / no blind retry | None | Call-id receipts + status tool | 🟢 S |
| Heavy work as jobs | Partial (gen job queue) | Unify: renders/bakes/gen as jobs | 🟡 M |
| Connector dialog + one-click setup | Manual external-harness config | Add setup dialog + launcher + snapshot | 🟠 L |
| Scene tabs / project reopen | Single scene; no project list | Scenes drawer + recent projects | 🟠 L |
| Zero-setup local models | llama.cpp support, manual config | Pinned runtime + GGUF catalog + detection + RAM-fit | 🟡 M |
| Asset embedding index + Library Mode | Blender asset browser + PolyHaven only | Local CLIP index + chat cards + agent reuse | 🟠 L |
| Opt-in UI control | Screenshot + jump tools | Add bounded input control (opt-in) | 🟡 M |
| Cleanup / batch-export passes | Ad-hoc via code execution | First-class `scene_cleanup`, `batch_export` tools | 🟢 S |
| Terrain from description | None | Geometry-nodes terrain tool | 🟡 M |
| Auto-rig | None | Local auto-rig (e.g. Rigify + heuristics) | 🟠 L |
| Retopo + UV rebuild + bake automation | Planned (QuadriFlow) | Add UV rebuild + bake menu (16 types) | 🟡 M |
| Add-on Project mode (staged patches) | `weak_sandbox` + Tier 4c text editor | Staged patch + approve + rollback mode | 🟡 M |
| Layered texture painting | None | **Large** — defer to Tier 7 | 🔴 XL |
| Parallel scenes / parallel agents | Single | Ambitious; partial (multi-scene switch) | 🔴 XL |

**Effort key:** 🟢 small · 🟡 medium · 🟠 large · 🔴 extra-large

---

## 10. Tier Mapping — Where Each Idea Lands

| # | Idea (from Mixar) | Proposed tier | Rationale |
|---|---|---|---|
| 1 | Direct in-process tool path for the built-in agent | **Tier 6c** | Removes our self-imposed round-trip; small, high-leverage |
| 2 | Specialist domain expansion (UV/rig/terrain/camera/admin/retopo/bake) | **Tier 6c** (+ feeds 4g) | Extends the domain system we already own |
| 3 | Plan Mode (session-wide) + plan review UI | **Tier 6c** | Natural evolution of `execute_blender_plan` |
| 4 | Turn checkpoints + per-turn undo | **Tier 6c** | Safety prerequisite for Plan Mode |
| 5 | Call receipts + `call_status` | **Tier 6c** | Cheap, prevents duplicate mutations |
| 6 | Unified heavy-work job queue | **Tier 6c** (+ Tier 5 job queue) | Needed for renders/bakes/gen |
| 7 | MCP connector dialog + launcher + tool snapshot | **Tier 6c** | Polishes our external-harness story |
| 8 | Scene tabs / scenes drawer / recent projects | **Tier 6c** | Multi-scene is a real workflow gap |
| 9 | Zero-setup local model manager | **Tier 6c** (overlaps Tier 5) | Core "local-first, out of the box" promise |
| 10 | Asset embedding index + Library Mode + agent reuse | **Tier 6c** | Big win; local CLIP keeps it offline |
| 11 | Opt-in UI control (click/type/drag) | **Tier 6c** | Extends existing screenshot/jump tools |
| 12 | `scene_cleanup` + `batch_export` first-class tools | **Tier 6c** (+ 4g) | Directly matches our cleanup/export domain |
| 13 | Terrain from description | **Tier 6c** | Geometry-nodes domain extension |
| 14 | Auto-rig | **Tier 6c** (stretch) | Local auto-rig is the hard part |
| 15 | Retopo + UV rebuild + bake automation | **Tier 6a** (extend) | Already planned in 6a; add UV/bake |
| 16 | Depth-guided render from viewport blockout | **Tier 6b** (extend) | Already the "Dreamer" thesis |
| 17 | Whole-scene assembly from one reference | **Tier 5/6a** (extend) | Composes gen + placement + asset reuse |
| 18 | Procedural material / PBR gen onto mesh (patina-style) | **Tier 5b/6a** | Fits ComfyUI material workflow |
| 19 | Add-on Project mode (staged patches + rollback) | **Tier 6c** (overlaps 4c) | Extends text-editor agent + sandbox |
| 20 | Usage/cost readout per call | **Tier 4b** (already planned) | Confirm parity, no new work |
| 21 | Context folders as agent context | **Tier 4c/6c** | Extends project rules/skills |
| 22 | Layered texture painting (ucupaint-class) | **Tier 7** (proposed) | XL subsystem; own plan |
| 23 | Moodboard video/segmentation/sharing parity | **Tier 4d/5** (extend) | Already planned; absorb specifics |
| 24 | Zen/Cinema/Liquid-Glass native spaces | **Not planned** | Fork-only UX; we stay Blender-native |
| 25 | Hosted credits / referral growth loops | **Not planned** | Not our model (free + OSS) |

**Conclusion:** the cluster of Mixar ideas that are *ours to take* is best filed as a single
**Tier 6c — "Agent Loop, Connector & Local-First Parity"**, cross-linking 4g (domains), 5/5b
(generation), 6a (3D/retopo/bake), 6b (viewport diffusion), 4c (add-on authoring), 4d/5 (moodboard).
Layered painting is explicitly pushed to a future **Tier 7**.

---

## 11. Proposed Tier 6 Plan

### ⚡ Phase 6c.1 — In-process tool path (~450 LOC)

| Step | Feature | Files | LOC |
|---|---|---|---|
| 1.1 | Tool dispatch abstraction: `DirectTransport` vs `HttpTransport` | `agent_controller.py`, new `tool_transport.py` | ~200 |
| 1.2 | Register built-in tools with in-process callables (reuse `mcp/blmcp/tools`) | `tool_transport.py`, `mcp_to_blender_server.py` | ~150 |
| 1.3 | Prefer direct for built-in agent; HTTP for external clients | `agent_controller.py`, `preferences.py` | ~60 |
| 1.4 | Fallback to HTTP if direct call is unavailable | `tool_transport.py` | ~40 |

### 🎛️ Phase 6c.2 — Specialist domains (~500 LOC)

| Step | Feature | Files | LOC |
|---|---|---|---|
| 2.1 | Add domains: `uv`, `rigging`, `terrain`, `camera`, `scene_admin`, `retopo`, `bake`, `texturing` | `agent_controller.py` | ~200 |
| 2.2 | Extend `_DOMAIN_KEYWORDS` + `_detect_domain_from_scene` | `agent_controller.py` | ~120 |
| 2.3 | New domain tools: `scene_cleanup`, `batch_export`, `uv_unwrap_pack`, `bake_maps`, `terrain_generate` | `mcp/blmcp/tools/` (5 new) | ~150 |
| 2.4 | Domain skill snippets in `skills/` | `skills/*.md` | ~30 |

### 🧾 Phase 6c.3 — Plan Mode + checkpoints (~700 LOC)

| Step | Feature | Files | LOC |
|---|---|---|---|
| 3.1 | Plan Mode toggle (preference + UI) | `preferences.py`, `ui_chat.py` | ~80 |
| 3.2 | Plan draft → approve/revise → execute loop | `agent_controller.py` | ~250 |
| 3.3 | Turn checkpoint: snapshot touched datablocks | new `turn_checkpoints.py` | ~200 |
| 3.4 | Per-turn undo operator + UI row | `ui_chat.py`, `operators_agent.py` | ~120 |
| 3.5 | Generalise `execute_blender_plan` into Plan Mode | `mcp/blmcp/tools/execute_blender_code.py` | ~50 |

### 🎫 Phase 6c.4 — Call receipts + job queue (~450 LOC)

| Step | Feature | Files | LOC |
|---|---|---|---|
| 4.1 | Call-id receipts for every tool call; `get_call_status` tool | `agent_controller.py`, `mcp/blmcp/tools/` | ~180 |
| 4.2 | Never auto-replay a timed-out mutation | `agent_controller.py` | ~60 |
| 4.3 | Unify heavy work (render/bake/gen) as editor jobs | `execute_blocking.py`, `gen_controller.py` | ~150 |
| 4.4 | Job progress surfaced in chat + status pill | `ui_chat.py` | ~60 |

### 🔌 Phase 6c.5 — Connector UX (~800 LOC)

| Step | Feature | Files | LOC |
|---|---|---|---|
| 5.1 | "Connect AI Apps" dialog with per-client setup + copy config | new `ui_connector.py` | ~300 |
| 5.2 | One-click add for Claude Code / Codex | `ui_connector.py`, `operators_server.py` | ~150 |
| 5.3 | Per-user launcher script (no token in config) | new `connector_launcher.py` | ~150 |
| 5.4 | Tool snapshot cache (`tools.json`) served when Blender is closed | `mcp_to_blender_server.py` | ~120 |
| 5.5 | Disable/revoke control | `ui_connector.py`, `preferences.py` | ~80 |

### 🗂️ Phase 6c.6 — Scenes drawer + projects (~700 LOC)

| Step | Feature | Files | LOC |
|---|---|---|---|
| 6.1 | Scenes drawer UI (list/switch/rename/new) | new `ui_scenes.py` | ~300 |
| 6.2 | Recent-projects list + reopen with unsaved-changes prompt | `ui_scenes.py`, `operators_agent.py` | ~200 |
| 6.3 | Agent tools: `scene_new`, `scene_switch`, `projects`, `project_open` | `mcp/blmcp/tools/` (4 new) | ~150 |
| 6.4 | Pin agent context to the active scene | `agent_controller.py` | ~50 |

### 💾 Phase 6c.7 — Zero-setup local models (~800 LOC)

| Step | Feature | Files | LOC |
|---|---|---|---|
| 7.1 | Pinned `llama.cpp` runtime + curated GGUF catalog (SHA-256 + size) | new `local_models/catalog.py` | ~250 |
| 7.2 | Verified resumable downloader | new `local_models/download.py` | ~180 |
| 7.3 | Server supervisor (health watch, auto-restart, port) | new `local_models/supervisor.py` | ~180 |
| 7.4 | Detect existing Ollama / LM Studio / llama.cpp servers | new `local_models/detect.py` | ~100 |
| 7.5 | RAM-fit ladder + recommendation | `local_models/catalog.py` | ~60 |
| 7.6 | Preferences/UI: Download → Start → Save | `preferences.py`, `ui_chat.py` | ~30 |

### 🖼️ Phase 6c.8 — Semantic asset index + UI control (~1,100 LOC)

| Step | Feature | Files | LOC |
|---|---|---|---|
| 8.1 | Local CLIP embedder (open_clip, CPU/GPU) | new `asset_index/embedder.py` | ~200 |
| 8.2 | Render preview + embed on enrol; incremental re-index | new `asset_index/index.py` | ~300 |
| 8.3 | Search by text and reference image | `asset_index/index.py`, `mcp/blmcp/tools/search_assets.py` | ~150 |
| 8.4 | Library Mode chat cards (click places at 3D cursor) | `ui_chat.py` | ~200 |
| 8.5 | Agent reuse with match-threshold slider | `agent_controller.py`, `preferences.py` | ~150 |
| 8.6 | Auto-archive generations into a "Coworker Generations" library | `gen_controller.py` | ~100 |

**Totals:** 📊 ~5,500 LOC across ~20 new files + modifications to ~10 existing files.

**Ordering:** 🧭 6c.1 → 6c.2 → 6c.4 → 6c.3 → 6c.7 → 6c.5 → 6c.6 → 6c.8. (1 and 2 are cheap and
unblock everything; 7 delivers the "out of the box" promise early; 8 is the largest and depends on 5/6.)

---

## 12. Local-First Translation

Every Mixar capability above has a local substitute; none requires their backend.

| Mixar (hosted) | Coworker (local-first) |
|---|---|
| Hosted agent LLM + credits | `llama.cpp` local (existing) + zero-setup manager (6c.7); remote/BYOK optional |
| Cloud embedding index for assets | Local CLIP / open_clip embeddings (6c.8); index on disk |
| Server-side generation engines (Tripo/Hunyuan) | ComfyUI bridge (Tier 5) + local 3D models (Tier 6a: TripoSR/TRELLIS/Hunyuan3D) |
| Depth-guided render | Tier 6b "Dreamer" local diffusion renderer |
| PBR / procedural material gen | Local StableMaterial/DeepBump (6a) + ComfyUI material workflows (5b) |
| Auto-rig | Rigify + heuristics, or a local auto-rig model (6c.2 stretch) |
| Cloud dictation / vision | Optional; local Whisper for dictation, local VLM for vision |

**Principle:** 🏠 local path is the default and works offline; remote providers are an opt-in
enhancement via existing preferences — mirroring Mixar's BYOK philosophy without their dependency.

---

## 13. Key Decisions

| Decision | Rationale |
|---|---|
| **Stay an add-on, do not fork** | Users keep their Bforartists install, add-ons and preferences. We take the in-editor *advantage* Mixar gets from forking, without the fork cost. |
| **Direct tool path, MCP for externals** | Removes our own round-trip for the built-in agent while preserving the MCP tool surface (our moat). |
| **Plan Mode as a first-class toggle** | Approval must not depend on the model or a prompt convention. |
| **Checkpoints before Plan Mode ships** | An approve step is only safe if a mistake reverses in one action. |
| **Narrow specialist domains** | Cheaper prompts, fewer wrong-tool mistakes, matches Mixar's stated rationale. |
| **Local models zero-setup, pinned + verified** | Copy Mixar's policy: pin runtime+model, verify SHA-256, discard failures, resume downloads. |
| **Local embeddings, on-disk index** | Semantic asset reuse without uploading anything. |
| **UI control is opt-in** | Safety; user input always regains control. |
| **Blender-native UI, not custom spaces** | "Better integrated and consistent with the Blender interface" is the explicit goal. |
| **Layered paint deferred to Tier 7** | XL subsystem; would derail Tier 6. |

---

## 14. Non-Goals

- No Blender fork, no C++ overlay, no custom window manager / Zen / Liquid Glass spaces.
- No hosted backend, credits, subscription, referral or growth loops.
- No re-implementation of Blender's asset browser; we add a search index *on top of* it.
- No layer-based texture painting in Tier 6 (Tier 7 candidate).
- No automatic replay of timed-out mutations.
- No change to the existing MCP tool contract for external clients.

---

## 15. Risks and Open Questions

1. **Direct tool path vs. isolation** — in-process calls skip the MCP boundary that currently
   isolates the agent. Mitigation: keep the same tool schemas and the `weak_sandbox` gate for code.
2. **Checkpoint cost on big scenes** — snapshotting datablocks can be heavy. Mitigation: snapshot
   only touched datablocks + a size cap, fall back to Blender's undo push.
3. **Local CLIP on CPU** — indexing a large library on CPU is slow. Mitigation: background job +
   incremental index + optional GPU.
4. **Scene tabs in Blender** — Blender has `bpy.data.scenes` but no native tab UI. A drawer is
   feasible; "parallel agents per scene" is not (single main thread). Scope 6c.6 to switching, not
   parallelism.
5. **Auto-rig quality** — genuinely hard; treat as stretch and document limits (as Mixar does).
6. **UI-control safety** — needs a hard "user input wins" rule and an audit log.
7. **Open question** — should Plan Mode be per-session, per-project, or global? (Recommend
   per-session, default off.)
8. **Open question** — do we adopt Mixar's "tool snapshot served while closed" for our connector, or
   is listing-only-when-running acceptable for an add-on? (Recommend: not needed; Blender must be
   running to act anyway.)

---

## 16. Sources

- Product home — https://www.mixar.app/
- Docs (MCP connect UX) — https://www.mixar.app/docs
- Agent overview / nine passes / Plan Mode / asset reuse / add-on mode — https://www.mixar.app/blender-agent
- Architecture comparison — https://www.mixar.app/compare/mixar-vs-blender-mcp
- Asset library training & search — https://www.mixar.app/blog/agent-asset-library
- Sitemap (page inventory) — https://www.mixar.app/sitemap.xml
- Source repo — https://github.com/Mixar-AI/mixar-app
  - `README.md`, `VERSION` (4.2.2), `src/scripts/mixar/` module tree
  - `src/scripts/mixar/mcp.py` (stdio launcher)
  - `src/scripts/mixar/modules/mcp_bridge/README.md`, `core/tool_snapshot.py`
  - `src/scripts/mixar/modules/local_models/README.md`
  - Branches: 100 feature branches + `release/v2.0.0 … release/v4.2.2`

**Not verifiable from public sources:** full backend tool schemas; live changelog; pricing/credit table.

---

## Summary

Mixar proves a thesis we already half-hold: the agent belongs **inside** the editor. Our job is
not to copy their fork — it is to close the four gaps they expose in us:

1. 🧾 **Agent loop UX** — Plan Mode, checkpoints, receipts, jobs.
2. 🔌 **Connector polish** — one-click external-client setup, scenes drawer, project reopen.
3. 💾 **Local-first onboarding** — zero-setup pinned local models.
4. 🖼️ **Semantic asset reuse** — local embedding index the agent searches before it generates.

Filed as **Tier 6c** (~5,500 LOC, 8 phases), with layered painting deferred to a proposed **Tier 7**.

---

🔝 [Back to top](#table-of-contents)
