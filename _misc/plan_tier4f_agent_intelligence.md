# BFA Coworker — Tier 4f: Agent Intelligence (OpenCode-Derived)

**Date**: 2026-09-14
**Status**: Planning — Not Started
**Depends on**: Tier 3g (tool domains, preflight, spiral detection), Tier 4 master §14.3/§14.6
**Extends**: Tier 6f.1 (Agent Teams), Tier 4 master §14.3 (budget readout), §14.6 (checkpoint)
**Comparison**: `plan_tier4f_opencode_comparison.md`
**Purpose**: Adopt the transferable orchestration, context, and safety architecture from OpenCode, and go beyond it on GGUF-level inference tuning that OpenCode cannot reach.

---

## Table of Contents

1. [Why Tier 4f](#1-why-tier-4f)
2. [Scope and Non-Goals](#2-scope-and-non-goals)
3. [Phase 4f.0 — Comparison Document](#3-phase-4f0--comparison-document)
4. [Phase 4f.1 — GGUF / Inference Optimization](#4-phase-4f1--gguf--inference-optimization)
5. [Phase 4f.2 — Context Management](#5-phase-4f2--context-management)
6. [Phase 4f.3 — Permissions + Doom-Loop Guard](#6-phase-4f3--permissions--doom-loop-guard)
7. [Phase 4f.4 — Orchestration: Subagents + Planner](#7-phase-4f4--orchestration-subagents--planner)
8. [Dependency Graph](#8-dependency-graph)
9. [Implementation Order](#9-implementation-order)
10. [Testing Strategy](#10-testing-strategy)
11. [Summary of Changes](#11-summary-of-changes)
12. [Open Questions](#12-open-questions)

---

## 1. Why Tier 4f

Tier 4a–4e cover editor integration, competitor UX, the Text Editor IDE agent, the
moodboard (moved to Tier 5), and artist workflow tooling. **None of them address the
agent's own runtime** — how it manages context, how it stays safe, how it decomposes
work, and how fast its local model runs.

Tier 4f fills that gap. It is the "make the agent itself smarter" lane.

**Placement note**: Tier 6b is already taken (Text Editor tools). Tier 4f is the free
slot and sits naturally beside 4c (IDE agent) and 4e (workflow tooling).

### 1.1 The Core Problem

| Problem | Today | Tier 4f Fix |
|---|---|---|
| Local model runs at 30–40 tok/s with a 16K context | Fixed llama-server flags | 4f.1: KV quant, flash-attn, cache-reuse, batching → 2–3× faster, 2× context |
| Tool results are lossily trimmed to 500 chars | `_MAX_TOOL_RESULT_CHARS = 500` | 4f.2: bounded preview + managed output file |
| History is hard-sliced at 20 messages | `_MAX_HISTORY_MESSAGES = 20` | 4f.2: compaction with a durable anchor |
| Skills are injected in full every turn | `get_always_loaded_skills()` | 4f.2: on-demand loading via a `skill` tool |
| Any tool can run any time | No permission layer | 4f.3: allow/ask/deny with glob patterns |
| Repeated identical calls loop | Spiral detection (errors only, 2×) | 4f.3: doom-loop guard (any tool, 3×) |
| One agent does everything | Single loop | 4f.4: planner → specialists → validator |

### 1.2 Design Principles

1. **Local-first** — optimize for 8–32K-context small models. Remote providers get the
   same features but no special tuning.
2. **Extend, don't duplicate** — 4f.2 builds on the existing §14.3/§14.6 checkpoint
   design; 4f.4 builds on Tier 6f.1.
3. **Never regress** — the existing strengths (domain scoping, smart undo, vision,
   reasoning, tool-pair repair) must keep working.
4. **Deterministic over clever** — prefer explicit rules (permissions, doom-loop) over
   model-dependent behavior.

---

## 2. Scope and Non-Goals

### In Scope

- GGUF / llama-server inference optimization + auto-tuning
- Context management: compaction, pruning, managed output files, on-demand skills
- Permissions: allow/ask/deny, glob patterns, external-directory guard, doom-loop guard
- Orchestration: subagents, Task tool, planner → specialists → validator

### Non-Goals (see comparison doc §7)

- Provider abstraction rewrite (75+ providers)
- Config file layering
- LSP integration
- Git-based snapshots
- TUI / desktop / web front-ends
- File-editing tools (`edit`/`write`/`apply_patch`)

---

## 3. Phase 4f.0 — Comparison Document

**Status**: ✅ Complete (`plan_tier4f_opencode_comparison.md`)

**Deliverables**:
1. `_misc/plan_tier4f_opencode_comparison.md` — architecture deep-dive, gap matrix,
   "where we can beat OpenCode", non-goals, transferable-pattern ranking.
2. Cross-reference pointer added to `_misc/plan_tier6_domain_tooling.md` §6f.

**Effort**: ~0 LOC (documentation only).

---

## 4. Phase 4f.1 — GGUF / Inference Optimization

**Est. ~450 LOC, 3 files**
**Dependencies**: None — can start first.
**Why first**: It is the highest-value, lowest-risk phase. It makes every subsequent
phase feel faster, and it is the one thing OpenCode structurally cannot do.

### 4.1.1 Extended llama-server flags

**File**: `addon/bfa_coworker/llm_manager.py` — `start_local_llama()` (~line 2798)

Current:
```python
args = [
    server_exe, '--jinja', '--verbose',
    '--host', '127.0.0.1', '--port', str(port),
    '--ctx-size', str(ctx_size),
    '--n-gpu-layers', str(ngpu_layers),
]
```

Add a curated, hardware-aware flag set:

| Flag | Purpose | Default |
|---|---|---|
| `--cache-type-k q8_0` | KV-cache quantization (K) | `q8_0` when VRAM-constrained, else `f16` |
| `--cache-type-v q8_0` | KV-cache quantization (V) | same as K |
| `--flash-attn` | Faster attention, lower memory | on when backend is CUDA/Vulkan |
| `--cache-reuse <n>` | Reuse system-prompt prefix across turns | `256` |
| `--batch-size <n>` | Prompt-processing batch | `2048` |
| `--ubatch-size <n>` | Micro-batch | `512` |
| `--parallel <n>` | Concurrent slots (for 4f.4 subagents) | `1` (raise to 2–4 in 4f.4) |
| `--no-mmap` | Avoid page-fault stalls | off by default; on if model > RAM |
| `--mlock` | Lock model in RAM | off by default |
| `--defrag-thold <f>` | KV defragmentation threshold | `0.1` |
| `--metrics` | Expose `/metrics` endpoint | **on** (feeds 4f.2 readout) |

**Implementation notes**:
- Build the flag list from a `_build_inference_args(cfg, model_path, backend)` helper so
  it is unit-testable without launching a process.
- Every flag must be overridable from preferences (see 4.1.3).
- Guard against unsupported flags: probe `llama-server --help` once and cache the
  supported set, so older builds don't fail on unknown args.

### 4.1.2 Hardware-aware auto-tuner

**File**: `addon/bfa_coworker/llm_manager.py`

Extend the existing `autodetect_gpu_layers()` / `_gguf_layer_count()` /
`_detect_gpu_backend()` trio with a `autotune_inference(cfg, model_path, backend)` that:

1. Reads GGUF metadata (layer count, context length, embedding size) via the existing
   header parser.
2. Detects available VRAM and system RAM.
3. Estimates KV-cache size: `2 × layers × ctx × kv_heads × head_dim × bytes_per_element`.
4. Chooses:
   - KV quantization (`f16` if it fits, `q8_0` if not, `q4_0` as last resort)
   - `--n-gpu-layers` (existing logic, now KV-aware)
   - `--batch-size` / `--ubatch-size` from VRAM headroom
   - `--parallel` slots from remaining VRAM
5. Logs the decision with the reasoning so users can see *why*.

**Reuse**: `_gguf_layer_count()` already parses `<arch>.block_count`. Extend it to a
general `_gguf_metadata(path) -> dict` returning the fields above.

### 4.1.3 Preferences UI

**File**: `addon/bfa_coworker/preferences.py`

Add an "Inference Tuning" box under the existing LLM tab:

| Property | Type | Default |
|---|---|---|
| `inference_autotune` | Bool | `True` |
| `inference_cache_type_k` | Enum (`f16`/`q8_0`/`q4_0`) | `q8_0` |
| `inference_cache_type_v` | Enum | `q8_0` |
| `inference_flash_attn` | Bool | `True` |
| `inference_cache_reuse` | Int | `256` |
| `inference_batch_size` | Int | `2048` |
| `inference_ubatch_size` | Int | `512` |
| `inference_parallel` | Int | `1` |
| `inference_extra_args` | String | `""` (power-user escape hatch) |

When `inference_autotune` is on, the manual fields are shown read-only with the
auto-chosen values.

### 4.1.4 Metrics collection

**File**: `addon/bfa_coworker/llm_manager.py` + `agent_controller.py`

- Poll `GET /metrics` (Prometheus text format) from llama-server.
- Parse: `llamacpp:prompt_tokens_total`, `llamacpp:tokens_predicted_total`,
  `llamacpp:kv_cache_usage_ratio`, `llamacpp:prompt_seconds_total`,
  `llamacpp:tokens_predicted_seconds_total`.
- Expose via `get_inference_metrics() -> dict` for the UI readout (4f.2) and the
  benchmark tests.

### 4.1.5 Speculative decoding readiness

Tier 5a (DFlash2) is blocked on llama.cpp PR #27342. Design 4.1.1's flag builder so
speculative decoding drops in as two more flags (`--model-draft`, `--draft-max`) when
the PR lands — no refactor needed.

### 4.1.6 Files

| File | Change | LOC |
|---|---|---|
| `llm_manager.py` | `_build_inference_args()`, `autotune_inference()`, `_gguf_metadata()`, `get_inference_metrics()`, flag-support probe | ~300 |
| `preferences.py` | Inference Tuning box + 9 properties | ~100 |
| `operators_llm.py` | "Auto-tune inference" operator + metrics display | ~50 |

### 4.1.7 Done when

- llama-server launches with the new flags on both a CUDA box and a CPU-only box.
- `/metrics` reports tok/s and KV usage.
- A benchmark shows measurable tok/s improvement over the current flag set.
- `tests/test_llm_manager.py` covers `_build_inference_args()` and `autotune_inference()`.

---

## 5. Phase 4f.2 — Context Management

**Est. ~600 LOC, 4 files**
**Dependencies**: 4f.1 (metrics drive the readout and the compaction trigger).
**Extends**: Tier 4 master §14.3 (budget readout) and §14.6 (checkpoint/context-flush).

### 5.2.1 Managed tool output files

**File**: `addon/bfa_coworker/agent_controller.py` — replace `_trim_tool_result()`

Current behavior: lossy 500-char trim (`_MAX_TOOL_RESULT_CHARS = 500`), tail-preserving
for errors.

New behavior (OpenCode's Tool Registry pattern):

1. Apply **one aggregate limit** — max lines *or* UTF-8 bytes, whichever is hit first.
2. Generic truncation **preserves the beginning and the end**.
3. If truncated, write the **complete** text to a managed output file under
   `~/.cache/bfa_coworker/tool_outputs/<uuid>.txt`.
4. The bounded preview in history includes the managed path so the model can re-read it
   via a new `read_tool_output` tool.
5. **Failure to write the file must not fail the tool call** — record a lossy bounded
   output without a path and log a diagnostic.

**New tool**: `read_tool_output(path, offset, limit)` — reads a managed output file with
line-range support. Registered in `_SURFACE_TOOLS`.

**Retention**: prune managed files older than N days on startup (default 7).

### 5.2.2 Compaction

**File**: `addon/bfa_coworker/agent_controller.py`

Replace the hard `_MAX_HISTORY_MESSAGES = 20` slice with OpenCode's compaction model:

| Setting | Default | Behavior |
|---|---|---|
| `compaction_auto` | `True` | Compact when the prompt budget is nearly full |
| `compaction_prune` | `True` | Drop stale tool outputs before compacting |
| `compaction_reserved` | `10000` | Token buffer so compaction itself doesn't overflow |

**Flow** (reuses the §14.6 `Checkpoint` design):

1. **DETECT** — `_estimate_messages_tokens()` exceeds `prompt_budget - reserved`.
2. **PRUNE** (if enabled) — replace old tool results with a one-line stub
   (`[tool result pruned — see <managed path>]`). Cheapest win; often enough on its own.
3. **COMPACT** — if still over budget, ask the model to write a checkpoint:
   *"Summarize: what was accomplished, what is in the scene, what remains. Be specific
   about object/material names."*
4. **REPLACE** — rebuild `history_to_send` as `[system] + [checkpoint] + [last 2–3 turns]`.
   This is the practical part of OpenCode's **Context Epoch** — the system prompt prefix
   stays stable, and compaction replaces the whole baseline at once.
5. **PERSIST** — checkpoint to `~/.cache/bfa_coworker/checkpoints.json` (survives restarts).
6. **RESUME** — `/checkpoint` and `/resume` slash commands in Ask mode.

**Interaction with spiral detection**: on a spiral, prefer checkpoint-then-flush over the
current truncate-and-corrective approach — it preserves intent instead of discarding it.

### 5.2.3 Token budget readout

**Files**: `agent_controller.py`, `ui_chat.py`

Wire the §14.3 design to the 4f.1 metrics:

- `AgentState.token_budget: dict` — `prompt`, `reasoning`, `output`, `tools`, `kv_usage`.
- Live counter row in the chat panel: `prompt 2.1k · reasoning 1.4k · output 0.8k · tools 0.3k · kv 62%`.
- Budget warning injected into the system prompt at 80%: *"You are at 80% of your token
  budget — prefer short answers, avoid re-listing scene contents."*

### 5.2.4 On-demand skill loading

**File**: `addon/bfa_coworker/skills/__init__.py`

Current: `get_always_loaded_skills()` injects **full bodies** every turn.

New (OpenCode's `skill` tool pattern):

1. Inject only **names + descriptions** into the system prompt as an
   `<available_skills>` block.
2. Add a `skill` meta-tool (intercepted like `load_tools`) that loads a body on demand.
3. Keep version-specific `blender_*.md` files **always loaded** — they are small and
   prevent the most common API errors.
4. Move `best_practices.md`, `naming.md`, `mcp_tools.md` and all domain skills to
   on-demand.

**Expected saving**: several thousand tokens per turn on the local path.

### 5.2.5 Files

| File | Change | LOC |
|---|---|---|
| `agent_controller.py` | Managed output files, `read_tool_output` tool, compaction, pruning, budget readout, `skill` meta-tool | ~400 |
| `skills/__init__.py` | `get_skill_index()` (names+descriptions), `load_skill_body(name)` | ~80 |
| `ui_chat.py` | Token readout row, `/checkpoint` + `/resume` commands | ~80 |
| `preferences.py` | Compaction settings (auto/prune/reserved), retention days | ~40 |

### 5.2.6 Done when

- A long session past 20 messages compacts instead of silently dropping turns.
- A large tool result spills to a managed file and the model can re-read it.
- The token readout shows live prompt/reasoning/output/kv numbers.
- Skills load on demand; the system prompt shrinks measurably.
- `tests/test_orchestration_helpers.py` extended for compaction + managed outputs.

---

## 6. Phase 4f.3 — Permissions + Doom-Loop Guard

**Est. ~400 LOC, 3 files**
**Dependencies**: None — can run in parallel with 4f.1/4f.2.

### 6.3.1 Permission layer

**New file**: `addon/bfa_coworker/permissions.py`

```python
class PermissionAction(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

def resolve_permission(tool_name: str, args: dict, rules: dict) -> PermissionAction:
    """Last matching glob rule wins (OpenCode semantics)."""
```

**Rule keys** (mapped to our tools):

| Key | Covers |
|---|---|
| `read` | `get_*`, `search_*`, `list_*` tools |
| `execute` | `execute_blender_code`, `execute_blender_plan` |
| `write` | `assign_material_to_objects`, `setup_pbr_material`, `wire_node_group`, `batch_keyframe_insert`, `place_asset_in_scene`, `set_collection_color_tag` |
| `download` | `download_polyhaven_asset` |
| `external_directory` | Any tool touching paths outside the project |
| `doom_loop` | Repeated identical calls |

**Matching**: simple wildcards (`*`, `?`), evaluated in order, **last match wins**.

**Defaults** (recommended posture — see Open Questions):

| Mode | `read` | `execute` | `write` | `download` |
|---|---|---|---|---|
| **Trusted** (today's behavior) | allow | allow | allow | allow |
| **Safe** (new default) | allow | ask | ask | ask |

### 6.3.2 Doom-loop guard

**File**: `addon/bfa_coworker/agent_controller.py`

Generalize the existing spiral detection:

| | Spiral (today) | Doom-loop (new) |
|---|---|---|
| Scope | `execute_blender_code` only | **Any** tool |
| Trigger | 2 consecutive identical **errors** | 3 identical **calls** (name + args hash) |
| Action | Truncate + corrective message | Escalate: inject a "you are repeating yourself" message, then block the call if it happens again |

**Implementation**: maintain `_recent_calls: list[str]` of `sha1(name + json.dumps(args, sort_keys=True))`.
On the 3rd identical hash, inject a corrective message. On the 4th, return a synthetic
tool error without executing.

**Keep** the existing error-based spiral detection — it catches a different failure mode
(same error, different code).

### 6.3.3 External-directory guard

**File**: `addon/bfa_coworker/permissions.py`

Any tool argument that looks like a filesystem path is checked against the project
directory. Paths outside it resolve to `ask` (or `deny` in strict mode). Applies to
`export_selection`, `save_blend_file`, `render_*_to_path`, `download_polyhaven_asset`.

### 6.3.4 Preferences UI

**File**: `addon/bfa_coworker/preferences.py`

- Operating-mode-adjacent "Safety" selector: **Trusted** / **Safe** / **Strict**.
- Per-tool override table (tool name → allow/ask/deny).
- "Auto-approve for this session" toggle (OpenCode's `--auto`).

### 6.3.5 Files

| File | Change | LOC |
|---|---|---|
| `permissions.py` | **New** — `PermissionAction`, `resolve_permission()`, glob matcher, path guard | ~180 |
| `agent_controller.py` | Permission check in tool dispatch, doom-loop guard, ask-prompt plumbing | ~150 |
| `preferences.py` | Safety mode selector, per-tool overrides, auto-approve toggle | ~70 |

### 6.3.6 Done when

- In Safe mode, `execute_blender_code` prompts before running.
- A repeated identical call 3× triggers the doom-loop guard.
- A tool writing outside the project directory prompts.
- `tests/test_permissions.py` covers glob matching, last-match-wins, and the doom-loop counter.

---

## 7. Phase 4f.4 — Orchestration: Subagents + Planner

**Est. ~900 LOC, 4 files**
**Dependencies**: 4f.3 (tool scoping per agent) + 4f.2 (per-subagent context isolation).
**Extends**: Tier 6f.1 (Agent Teams with Planner).

### 7.4.1 Subagent infrastructure

**File**: `addon/bfa_coworker/agent_controller.py`

Add a `Task` meta-tool (intercepted like `load_tools`):

```json
{
  "name": "task",
  "description": "Launch a specialized subagent for a focused subtask.",
  "parameters": {
    "description": "Short task description",
    "prompt": "Full instructions for the subagent",
    "subagent_type": "explore | modeling | materials | lighting | rigging | animation | rendering | validator"
  }
}
```

**Execution**: spawn a scoped sub-conversation with:
- Its own `conversation_history` (isolated context — the key benefit)
- A tool subset from `_TOOL_DOMAINS[subagent_type]`
- Its own model/temperature/steps (from the agent definition)
- A hard iteration cap (`steps`)
- A return value: the subagent's final text summary, injected as the Task tool result

**`subagent_depth`**: default `1` (primary → subagent, no nesting). `0` disables.

**Parallelism**: independent subagents run on threads, bounded by llama-server's
`--parallel` slots (4f.1). On a single-slot server, run sequentially.

### 7.4.2 Agent definitions

**New file**: `addon/bfa_coworker/agent_defs.py`

Support markdown definitions with YAML frontmatter (OpenCode pattern):

```markdown
---
name: materials
description: Creates and assigns PBR materials
model: (inherit)
temperature: 0.2
steps: 6
tools: [setup_pbr_material, assign_material_to_objects, get_active_node_tree, wire_node_group]
permission:
  execute: deny
---

You are a materials specialist. Focus on physically-based shading...
```

**Locations**: `addon/bfa_coworker/agents/*.md` (built-in) and a user directory
(`~/.config/bfa_coworker/agents/*.md`).

**Built-in specialists** (mirroring our `_TOOL_DOMAINS`):
`explore` (read-only), `modeling`, `materials`, `lighting`, `rigging`, `animation`,
`rendering`, `validator`.

### 7.4.3 Planner → specialists → validator

**New file**: `addon/bfa_coworker/agent_teams.py`

```
User goal
   │
   ▼
┌─────────────┐
│  PLANNER    │  Decomposes the goal into a dependency-ordered TaskPlan
└─────────────┘
   │  TaskPlan: [Task(id, description, domain, depends_on, status)]
   ▼
┌─────────────┐
│ ORCHESTRATOR│  Executes in dependency order; independent tasks in parallel
└─────────────┘
   │
   ├──► Specialist A (modeling)   ─┐
   ├──► Specialist B (materials)  ─┤  parallel where deps allow
   └──► Specialist C (lighting)   ─┘
   │
   ▼
┌─────────────┐
│  VALIDATOR  │  Checks the result against the goal; auto-fixes issues
└─────────────┘
   │
   ▼
Single undo checkpoint + live task list in the UI
```

**Data model**:

```python
@dataclass
class Task:
    id: str
    description: str
    domain: str
    depends_on: list[str]
    status: str  # pending | running | done | failed

@dataclass
class TaskPlan:
    goal: str
    tasks: list[Task]
```

**Undo**: one checkpoint for the whole plan (not per task) so the user can revert the
entire operation.

**UI**: a mission panel in `ui_chat.py` showing the live task list with status icons.

### 7.4.4 Files

| File | Change | LOC |
|---|---|---|
| `agent_teams.py` | **New** — `Task`, `TaskPlan`, `PlannerAgent`, `AgentOrchestrator`, `ValidatorAgent` | ~450 |
| `agent_defs.py` | **New** — markdown agent definition loader | ~150 |
| `agent_controller.py` | `Task` meta-tool, subagent spawning, depth limit, parallel execution | ~200 |
| `ui_chat.py` | Mission panel with live task list | ~100 |
| `agents/*.md` | 8 built-in specialist definitions | ~100 |

### 7.4.5 Done when

- "Build a campfire scene — ground plane, three logs, stone circle, warm light, dark
  rocky material" produces a dependency-ordered plan and executes it.
- Subagents have isolated context (verified by token counts).
- `subagent_depth = 0` disables subagents cleanly.
- A user-authored `agents/*.md` file is discovered and usable.
- One undo reverts the whole plan.

---

## 8. Dependency Graph

```
4f.0 (comparison doc) ──► 4f.1 (inference) ──► 4f.2 (context) ──┐
                                                               ├──► 4f.4 (orchestration)
                          4f.3 (permissions) ──────────────────┘
```

- **4f.0** — complete.
- **4f.1** — independent; start first.
- **4f.2** — needs 4f.1's metrics for the readout and the compaction trigger.
- **4f.3** — independent; can run in parallel with 4f.1/4f.2.
- **4f.4** — needs 4f.3 (per-agent tool scoping) and 4f.2 (context isolation).

---

## 9. Implementation Order

| Order | Phase | Rationale | Est. LOC |
|---|---|---|---|
| 1 | **4f.1** Inference | Highest value, lowest risk, unblocks the readout | ~450 |
| 2 | **4f.3** Permissions | Independent, small, immediately useful | ~400 |
| 3 | **4f.2** Context | Needs 4f.1 metrics; biggest quality-of-life win | ~600 |
| 4 | **4f.4** Orchestration | Needs 4f.2 + 4f.3; the capstone | ~900 |

**Total**: ~2,350 LOC across ~14 files (4 new).

**Milestone checkpoints** (keep `main` green at each):
- After 4f.1: faster local inference, metrics visible.
- After 4f.3: Safe mode + doom-loop guard shipped.
- After 4f.2: long sessions stay sharp; skills load on demand.
- After 4f.4: multi-agent scene building works.

---

## 10. Testing Strategy

### Unit tests (extend existing)

| Test file | New coverage |
|---|---|
| `tests/test_llm_manager.py` | `_build_inference_args()`, `autotune_inference()`, `_gguf_metadata()`, metrics parsing |
| `tests/test_orchestration_helpers.py` | Compaction trigger, pruning, managed output spill, budget readout |
| `tests/test_permissions.py` (**new**) | Glob matching, last-match-wins, doom-loop counter, path guard |
| `tests/test_agent_teams.py` (**new**) | TaskPlan dependency ordering, parallel scheduling, depth limit |

### Integration tests

| Test | Verifies |
|---|---|
| `tests/test_tool_listing.py` | Tool count after permission layer; `task`, `skill`, `read_tool_output` present |
| `tests/tool_smoke_test.py` | All tools still callable |
| `tests/test_mcp_server.py` | MCP server unaffected |

### Manual verification

1. Start llama-server with new flags on CUDA and CPU-only; confirm load + `/metrics`.
2. Run a session past 20 messages; confirm compaction fires and scene state survives.
3. Trigger a repeated identical tool call 3×; confirm the doom-loop guard escalates.
4. Run a multi-part goal; confirm planner → specialists → validator.
5. Verify one undo reverts a whole plan.

### Regression guards

- The existing `test_orchestration_helpers.py` suite (tool-pair repair, token budget,
  flatten, prompt variants, main-thread marker) **must stay green**.
- Domain tool scoping must not regress — `_SURFACE_TOOLS` + `_TOOL_DOMAINS` behavior is
  load-bearing for local models.

---

## 11. Summary of Changes

### New files

| File | Purpose | Phase |
|---|---|---|
| `_misc/plan_tier4f_opencode_comparison.md` | Comparison document | 4f.0 |
| `_misc/plan_tier4f_agent_intelligence.md` | This plan | 4f.0 |
| `addon/bfa_coworker/permissions.py` | Permission resolution + glob matcher + path guard | 4f.3 |
| `addon/bfa_coworker/agent_teams.py` | Planner, orchestrator, validator | 4f.4 |
| `addon/bfa_coworker/agent_defs.py` | Markdown agent definition loader | 4f.4 |
| `addon/bfa_coworker/agents/*.md` | 8 built-in specialist definitions | 4f.4 |
| `tests/test_permissions.py` | Permission tests | 4f.3 |
| `tests/test_agent_teams.py` | Orchestration tests | 4f.4 |

### Modified files

| File | Change | Phase |
|---|---|---|
| `addon/bfa_coworker/llm_manager.py` | Inference args, auto-tuner, GGUF metadata, metrics | 4f.1 |
| `addon/bfa_coworker/preferences.py` | Inference tuning, compaction, safety mode | 4f.1/4f.2/4f.3 |
| `addon/bfa_coworker/operators_llm.py` | Auto-tune operator, metrics display | 4f.1 |
| `addon/bfa_coworker/agent_controller.py` | Managed outputs, compaction, permissions, doom-loop, Task tool | 4f.2/4f.3/4f.4 |
| `addon/bfa_coworker/skills/__init__.py` | On-demand skill loading | 4f.2 |
| `addon/bfa_coworker/ui_chat.py` | Token readout, mission panel, slash commands | 4f.2/4f.4 |
| `_misc/plan_tier6_domain_tooling.md` | Cross-reference to 4f | 4f.0 |
| `_misc/plan_tier4_master_coordination.md` | Link 4f; note §14.3/§14.6 extension | 4f.0 |

---

## 12. Open Questions

| # | Question | Options | Recommendation |
|---|---|---|---|
| 1 | **Skill loading** — convert to on-demand? | A: convert in 4f.2 / B: keep always-injected, on-demand only for domain skills / C: defer | **A** — several thousand tokens/turn saved on the local path |
| 2 | **Permission default posture** | A: new Safe/Trusted toggle / B: always `ask` for destructive tools / C: keep permissive | **A** — Safe default, Trusted preserves today's behavior |
| 3 | **Speculative decoding overlap** | A: design 4f.1 for it now / B: keep separate | **A** — two extra flags, no refactor when Tier 5a lands |
| 4 | **Compaction trigger threshold** | 80% of budget (matches §14.6) vs. `budget - reserved` | **`budget - reserved`** — matches OpenCode and is self-tuning |
| 5 | **Subagent parallelism on single-slot servers** | A: sequential fallback / B: require `--parallel > 1` | **A** — never break single-GPU users |
| 6 | **Managed output retention** | 7 days vs. session-scoped | **7 days** — allows cross-session debugging |

---

## Appendix: Relationship to Existing Plans

| Existing plan | Relationship |
|---|---|
| Tier 4 master §14.3 (budget readout) | **Extended** by 4f.2.3 — same design, now fed by real llama-server metrics |
| Tier 4 master §14.6 (checkpoint/flush) | **Extended** by 4f.2.2 — same `Checkpoint` dataclass, now triggered by compaction |
| Tier 4b Phase 2.1 (token streaming) | **Complementary** — streaming is perceived performance; 4f.1 is real performance |
| Tier 6f.1 (Agent Teams) | **Extended** by 4f.4 — adds OpenCode's concrete mechanics (Task tool, depth limit, markdown defs) |
| Tier 5a (speculative decoding) | **Prepared for** by 4f.1.5 — flag builder designed to accept it |
| Tier 3g (spiral detection) | **Generalized** by 4f.3.2 — error-based spiral kept, call-based doom-loop added |