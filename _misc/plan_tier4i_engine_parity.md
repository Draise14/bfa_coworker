# Tier 4i — One Coworker, Three Engines (UX Parity + Token Economy)

**Created**: 2026-10-16 (from the Tier 4 audit)
**Status**: 📝 Ready for review — no code started
**Built with**: Tier 4h (ACP). 4i.1 is 4h's first implementation step
**Tasks**: `plan_tier4_task_board.md` lane I

---

## 1. The problem the audit found

The Coworker's intelligence is split across two places:

| Lives in the **MCP server / bridge** (any client gets it) | Lives only in the **addon turn loop** (`agent_controller.py`, Local/Remote only) |
|---|---|
| ~45 tools, preflight code checks, bundled docs search, deferred long jobs | Domain tool filtering (`_SURFACE_TOOLS` / `_TOOL_DOMAINS` / `load_tools`), Goal & Plan, stop rules, compaction + memory, spiral recovery, tool-result trimming, usage ledger, auto-undo on retry |

So an external agent gets **every tool schema at once, no plan, and no scene checkpoints**.
The UI also renders straight from loop internals, which means a third engine (ACP) would
need its own UI code paths.

**Goal of Tier 4i:** the user sees **one Coworker**: same panel, same Workshop, same plan,
same approval row, same Revert, same session list. Behind it, each engine contributes
what it can, and **anything that saves tokens or protects the scene moves into the
server**, where every engine benefits.

---

## 2. Principles

1. **Events, not internals.** Every engine emits the same `AgentEvent` stream; the UI
   renders events only.
2. **Scene-side features live in the server.** If a feature is about Blender (plan state,
   checkpoints, approvals, questions, scene digest), it is an MCP tool or a bridge
   service, not loop code.
3. **Loop-side features stay in the loop.** Compaction, stop rules and prompt calibration
   are Local/Remote concerns. An ACP agent has its own versions, so we don't fight them.
4. **Measure every token change** with the Tier 3i.0 cost ledger (Local/Remote) and the
   agent-reported usage (External). If it isn't measured, it isn't shipped.
5. **One capability table.** The UI reads `Engine.capabilities` to show or hide controls.
   Never `if mode == ...` scattered through `ui_chat.py`.

---

## 3. Phases

### 4i.1 — Engine seam (`AgentEvent` + `Engine`)

```python
@dataclass
class AgentEvent:
    kind: str     # text | thought | tool_start | tool_update | tool_end | plan |
                  # approval | question | usage | turn_end | error | status
    data: dict
    turn_id: str

class Engine(Protocol):
    name: str                      # "Local · Qwen3 14B", "Agent · Claude Code"
    capabilities: EngineCaps       # images, cancel, load_session, usage, modes, models
    def start(self) -> None: ...
    def send(self, prompt: Prompt) -> None: ...   # text + images + context chip
    def cancel(self) -> None: ...
    def stop(self) -> None: ...
```

- `LocalEngine` and `RemoteEngine` wrap `run_conversation_turn` and translate its
  callbacks (`on_text`, `on_reasoning`, tool hooks, GoalPlan updates) into events.
  **No behaviour change**; this is a refactor guarded by the existing tests.
- `ExternalAgentEngine` (4h) emits the same events from ACP.
- A single main-thread drain applies events to `AgentState`, so threading stays as it is
  today.
- **Done when**: Local and Remote pass the full test suite and the benchmark harness with
  the UI reading only from events, and a fake engine in tests drives the panel.

### 4i.2 — Shared UI components (the minimum of master §4)

Extract only what the three engines and the new 4b items need: `draw_tool_row`,
`draw_reasoning`, `draw_plan`, `draw_approval_row`, `draw_question_row`,
`draw_engine_chip`. Leave the rest of `ui_chat.py` alone until 4a. **Done when**: no
engine-specific branches remain in the turn rendering.

### 4i.3 — One approval model

`ApprovalRequest(id, title, detail, options[allow_once, allow_always, reject_once,
reject_always], source, expires_s)`, rendered by one inline row. Fed by:

- ACP `session/request_permission` (4h.5),
- the local permission layer (4f.3, "ask" rules),
- **prepare → apply handles** for destructive server tools (idea from Scenario, written
  in our own terms): a write tool can return `needs_approval` with a one-shot handle. The
  handle is invalid once the scene changes (depsgraph update counter / file reload). The
  user approves in the panel and the server applies it once. Use it first for
  file-touching tools (save-as/export/pack, 4e) and bulk deletes.

**Done when**: the three sources produce identical rows and "always" persists per session.

### 4i.4 — Progressive tool loading in the server (token economy for every client)

Today `load_tools` is intercepted by the local loop. Move the mechanism into the MCP server:

- The server lists the **surface set** (inspection, docs, screenshots, `execute_blender_code`,
  `load_tools`, `ask_user`, `update_plan`). Domain tools stay registered and callable but
  are **hidden from `tools/list`** until loaded.
- `load_tools(domains[])` becomes a real MCP tool. It enables the domains for this MCP
  session and sends `notifications/tools/list_changed`.
- **Fallback**: if the client didn't declare `listChanged` support, list everything as
  today. Some clients (Claude Code) already defer schemas on their own.
- The local loop then calls the same server-side mechanism. Its keyword pre-detection
  stays as a *hint* that pre-loads domains.
- **Done when**: an ACP agent's first request carries the surface schemas only (measured),
  and a VSE request loads the VSE domain on demand.

### 4i.5 — Plan, questions and scene checkpoints as MCP tools

| Tool | Purpose | Engines |
|---|---|---|
| `update_plan(goal?, steps[{text,status}])` | Writes the same `GoalPlan` the panel shows; ticks live | All (local loop already has it internally; switch it to this) |
| `ask_user(question, choices[], allow_text)` | Button row in the chat; deferred until answered (4b B2) | All |
| `scene_checkpoint(label)` / turn-start auto checkpoint | Blender-side undo point the panel's **Revert turn** uses (4b B4) | All |
| `get_scene_digest(since_hash?)` | Compact scene summary with a hash. Called again with the hash, it returns `unchanged` or a short diff. It replaces repeated full `get_objects_summary` calls | All |

**Done when**: an ACP agent keeps the plan panel current, asks a question with buttons,
and the user can Revert its turn.

### 4i.6 — MCP session token

A random token is generated on Start and required by the MCP HTTP endpoint and the
bridge. It is passed to ACP agents via `env`, to the local loop internally, and included
in Copy MCP Config. Stdio launches inherit it. **Done when**: a request without the
token is refused, and all three engines and the config copy still work.

### 4i.7 — Bounded tool output for every client (from 4f.2, moved here)

The server applies one aggregate limit per tool result, keeping head and tail. The full
text goes to `~/.cache/bfa_coworker/tool_outputs/<id>.txt`, and `read_tool_output(id,
offset, limit)` lets the agent page through it. A failure to write the file never fails
the tool. Node trees, asset lists and long tracebacks benefit most. **Done when**: a 50 KB
node-tree dump arrives as a ≤ 2 KB preview plus a handle on every engine.

---

## 4. Token-economy checklist (applies to every Tier 4 tool and prompt)

| Lever | Rule |
|---|---|
| Schemas | A new tool joins a domain, never the surface set, unless it is used in most turns |
| Results | Structured, bounded, with names (not `repr`), plus a handle for the full text |
| Scene reads | `get_scene_digest(since_hash)` before any full listing |
| Images | Downscaled. One **review sheet** (N views tiled into one image, 4g G5) instead of N images. Send an image once and refer back to it |
| Edits | Recipes: change parameters in a text block and rerun; don't regenerate code (4g G2) |
| Instructions | `prompts_compact.yml` for External/remote-small, full prompts for local only where measured better |
| Cache | Keep the system/tool prefix stable across turns (llama `--cache-reuse`, provider prompt caching): no timestamps or counters in the prefix |
| Questions | `ask_user` beats a wrong turn |

---

## 5. Capability table (what each engine shows)

| Control / feature | Local | Remote | External Agent | External Harness |
|---|---|---|---|---|
| Chat panel, Workshop, Stop, Queue, images | ✅ | ✅ | ✅ | — (chat elsewhere) |
| Goal & Plan panel | ✅ | ✅ | ✅ (ACP plan + `update_plan`) | ✅ via `update_plan` (panel visible) |
| `ask_user` buttons | ✅ | ✅ | ✅ | ✅ (question appears in Blender) |
| Inline approvals | ✅ (4f.3) | ✅ (4f.3) | ✅ | ✅ (prepare/apply only) |
| Revert turn (scene) | ✅ | ✅ | ✅ | ✅ (manual: Revert last agent change) |
| Compaction / memory / stop rules | ✅ | ✅ | agent-owned | client-owned |
| Usage readout | ✅ | ✅ | if reported | — |
| Model / thinking selectors | ✅ | ✅ | if advertised | — |
| Progressive tools | ✅ | ✅ | ✅ | ✅ (if `listChanged`) |

---

## 6. Order

`4i.1 → (4h.2–4h.4 in parallel with 4i.2) → 4i.3 → 4i.5 → 4i.4 → 4i.6 → 4i.7`.
4i.6 can also go first if External Harness use grows before ACP ships.
