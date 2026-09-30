# BFA Coworker — Tier 3: Local Session Memory & Context Checkpoints

> ✅ **DONE — implemented, merged, and audited (2026-09-30).** Archived to
> `_misc/Plans History/`. All phases shipped on `main` (PRs #78/#84); the one
> remaining gap — the never-applied Qwen `--no-context-shift` preset flags —
> and the live-overflow budget work were completed on
> `fix/scene-safety-local-hardening`. See [§11 Closure](#11-closure-2026-09-30).

**Date**: 2026-09-29

**Status**: ✅ Complete — Phases 1–7 implemented, tested, audited

**Depends on**: Tier 3b (llama-server & models), Tier 3f (Buddy Optimizations — GPU auto-detection, context recommendation), Tier 4f (Agent Intelligence — context management)

**Blocks**: Nothing — this hardens existing local-mode behaviour

**Branch**: `6117603f` worktree

**Estimated scope**: ~1,200–1,600 LOC + tests

---

## Table of Contents

1. [Summary](#1-summary)
2. [The Failure We Are Fixing](#2-the-failure-we-are-fixing)
3. [Root Cause Analysis](#3-root-cause-analysis)
4. [Design Decisions](#4-design-decisions)
5. [Architecture: Session Memory & Auto-Checkpoints](#5-architecture-session-memory--auto-checkpoints)
6. [Implementation Plan](#6-implementation-plan)
   - [Phase 0 — This Document](#phase-0--this-document)
   - [Phase 1 — Real Context Accounting](#phase-1--real-context-accounting)
   - [Phase 2 — Startup Context Validation & First-Run Defaults](#phase-2--startup-context-validation--first-run-defaults)
   - [Phase 3 — Qwen llama.cpp Flag Hardening](#phase-3--qwen-llamacpp-flag-hardening)
   - [Phase 4 — Session Memory & Automatic Checkpoint Engine](#phase-4--session-memory--automatic-checkpoint-engine)
   - [Phase 5 — UI & Operators](#phase-5--ui--operators)
   - [Phase 6 — Tool-Output Slimming](#phase-6--tool-output-slimming)
   - [Phase 7 — Tests & Documentation](#phase-7--tests--documentation)
7. [Files Touched](#7-files-touched)
8. [Verification Plan](#8-verification-plan)
9. [Risks & Mitigations](#9-risks--mitigations)
10. [Out of Scope](#10-out-of-scope)

---

## 1. Summary

Local mode currently fails fast on longer conversations: llama-server is launched with a fixed
`--ctx-size`, and when a request exceeds it the server returns a hard `400` ("request exceeds the
available context size"). The client-side budget that is supposed to prevent this **under-counts**
everything the prompt actually contains and **never reads the server's real context**. Meanwhile the
conversation history is stored in full and never pruned, so a single large tool traceback plus
accumulated reasoning blocks silently push each subsequent request past the window.

This plan does four things:

1. **Budgets against the server's real context** and counts everything that enters the prompt
   (tool schema, injected skills, screenshot, memory block).
2. **Sizes context correctly at first run** from detected RAM/VRAM, with a single source of truth and
   a warning — never a silent mid-session restart.
3. **Hardens llama.cpp flags** for the Qwen models (flash-attn, KV quant, batch sizing, prompt cache).
4. **Adds an automatic session-memory + checkpoint system** so conversations feel effectively
   infinite: old turns condense into an always-injected memory block, raw turns archive to disk, and
   the system auto-checkpoints itself — with **no manual bookkeeping required from the user**.

---

## 2. The Failure We Are Fixing

Reproduced during a first benchmark run. The llama-server log shows:

```
slot operator(): id 0 | task 23921 | new prompt, n_ctx_slot = 16384, n_keep = 0, task.n_tokens = 34678
E srv send_error: task id = 23921, error: request (34678 tokens) exceeds the available context size (16384 tokens), try increasing it
```

The server was started with `n_ctx = 16384`, but the request contained **34,678 tokens** — more than
double the window. The user's perception: "I hit a token context limit quite quickly," and the
conversation is effectively dead because every retry re-sends the same oversized history.

Two tool outputs in the same session were large tracebacks from `execute_blender_code`:

```
RuntimeError: Operator bpy.ops.object.join.poll() failed, context is incorrect
RuntimeError: Operator bpy.ops.object.modifier_apply.poll() failed, context is incorrect
```

These are stored **in full** in the conversation history and re-sent on every turn.

---

## 3. Root Cause Analysis

Four independent defects compound to produce the overflow:

### 3.1 The budget ignores the real context size

`addon/bfa_coworker/agent_controller.py` (~line 5047, `_run_conversation_turn_inner`) computes:

```python
_max_allowed = max(_ctx_size // 2, 512)
if max_tokens > _max_allowed:
    max_tokens = _max_allowed
prompt_budget = _ctx_size - max_tokens - _TEMPLATE_OVERHEAD_TOKENS
```

`_ctx_size` comes from `LLMConfig.local_ctx_size` — a **configured** value that may not equal what the
server actually applied. `TEMPLATE_OVERHEAD_TOKENS = 512` is a small flat allowance.

### 3.2 The budget ignores what the prompt actually contains

`_estimate_messages_tokens()` / `_message_text_length()` count only `content` and `tool_calls` text.
They do **not** count:

- the **MCP tool schema** injected as `tools=[...]` in the request body (large for the full tool set);
- the **injected domain skills** appended to the system prompt by `_detect_domain*`;
- the **base64 screenshot** prepended to the last user message;
- the chat-template scaffolding beyond the flat 512-token allowance.

For a tool-heavy local prompt these unaccounted items are easily tens of thousands of characters.

### 3.3 History is stored in full and never pruned

- Tool results are stored **untruncated** (`agent_controller.py:5581`); `_MAX_TOOL_RESULT_CHARS = 2000`
  is applied only when the request is built, not when stored.
- `reasoning`-role blocks are appended to history (`:5368`) and **persisted to JSON**, removed only at
  send time by `_strip_reasoning_from_history()`.
- Nothing ever shrinks `conversation_history` in memory, so the persisted `default.json` grows without
  bound and is reloaded verbatim next session.

### 3.4 Divergent, silently-mutating defaults

Three different "default" context sizes exist and the runtime mutates one of them silently:

| Location | Default |
|---|---|
| `LLMConfig.local_ctx_size` (`llm_manager.py:444`) | `16384` |
| `LLMPreferences.local_ctx_size` (`preferences.py:667`) | `32768` |
| Runtime fallback in `start_local_llama` (`llm_manager.py:~2786`) | `16384`, **silently bumped to 32768 if ≤ 8192** |

The silent bump means the value the agent budgets against and the value the server was launched with
can diverge, and the user is never told.

---

## 4. Design Decisions

Decisions confirmed with the product owner:

| # | Decision | Choice |
|---|---|---|
| D1 | Infinite-context approach | **Full checkpoint/restore UI** (VS Code / OpenCode style) |
| D2 | Who writes the session summary | **Local LLM generates it in a dedicated turn** on threshold |
| D3 | Compaction UX | **Always show a visible "Compacting conversation…" status** so the user knows it is happening |
| D4 | Context auto-bump | **Startup-only validation & recommendation.** No mid-session llama-server restart |
| D5 | Checkpoint creation | **Automatic only** — snapshots taken at each compaction. Restore/branch are optional actions on the auto list; the user is never asked to create checkpoints |
| D6 | Hardening scope | All four: real-ctx budget + full prompt accounting; tool-output slimming + reasoning prune; per-preset Qwen flags; context-usage indicator |
| D7 | Document name | `plan_tier3_session_memory_checkpoints.md` |

**Guiding principle:** the user should never have to manage context. The system compacts, checkpoints,
and continues on its own; manual controls exist only as advanced escape hatches (restore, branch,
"compact now").

---

## 5. Architecture: Session Memory & Auto-Checkpoints

### 5.1 Continuity model

The conversation is no longer "one unbounded list". It becomes a **bounded verbatim window plus a
persistent memory block**:

```
┌───────────────────────────────────────────────────────────────┐
│  SYSTEM PROMPT                                                 │
│  (rules + Blender version + injected domain skills)            │
├───────────────────────────────────────────────────────────────┤
│  SESSION MEMORY BLOCK   ← always injected, always current      │
│   • Goal                                                       │
│   • Decisions                                                  │
│   • Objects & files touched                                    │
│   • Pending / next steps                                       │
│   • Errors seen (and their fixes)                              │
├───────────────────────────────────────────────────────────────┤
│  VERBATIM WINDOW        ← last N turns, exact and unmodified   │
│   user / assistant / tool / (reasoning for UI only)            │
├───────────────────────────────────────────────────────────────┤
│  NEW USER TURN                                                 │
└───────────────────────────────────────────────────────────────┘
        ▲                                        │
        │  when the window crosses ~60% of the    │
        │  safe budget, a compaction turn:        ▼
        │  ┌──────────────────────────────────────────────┐
        └──┤ 1. LLM rewrites the memory block from the    │
           │    turns about to retire                     │
           │ 2. Retired raw turns are appended to the     │
           │    on-disk archive (archive.jsonl)           │
           │ 3. An automatic checkpoint is snapshotted    │
           │ 4. UI shows "Compacting conversation…"       │
           └──────────────────────────────────────────────┘
```

The user sees the chat panel scroll as normal; on disk the full transcript is always preserved
(`archive.jsonl`), so nothing is lost — it is only *not sent to the model*.

### 5.2 Memory block format

A single terse system-adjacent message, kept small (target < ~600 tokens) and structured so a small
local model can consume it reliably:

```
[Session memory — earlier turns condensed]
Goal: <one line>
Decisions: <bullet list>
Objects & files touched: <datablock names, files, collections>
Pending: <what is still to do>
Errors seen: <error signature -> resolution>
Last updated: turn <N>
```

### 5.3 Checkpoints (automatic)

A checkpoint is a lightweight snapshot record:

```
{ turn_index, timestamp, memory_block, window_hash, message_count }
```

Created automatically at each compaction (and at session start). The checkpoint list is presented in
the UI so the user can:

- **Restore** — rewind `conversation_history` + memory to that point (the current state is itself
  checkpointed first, so restore is non-destructive).
- **Branch** — fork the current session into a new thread seeded from that checkpoint.

No "create checkpoint" button — that is the whole point of D5.

---

## 6. Implementation Plan

### Phase 0 — This Document

- [ ] Write and commit `_misc/plan_tier3_session_memory_checkpoints.md`.

### Phase 1 — Real Context Accounting

**Goal:** the client never sends a prompt larger than the server can accept.

**File:** `addon/bfa_coworker/agent_controller.py`

- [ ] Add `_estimate_tools_tokens(tools)` — count the JSON size of the OpenAI-format tool schema
      (`name`, `description`, `parameters`) at the same conservative ratio.
- [ ] Extend the prompt estimate to include: system prompt **with injected skills**, memory block,
      tool schema, screenshot base64, and history.
- [ ] Consume the **runtime context size** from Phase 2 (`get_runtime_ctx()`) instead of trusting the
      configured value alone.
- [ ] Replace the ad-hoc `prompt_budget` formula with a single helper
      `_compute_prompt_budget(ctx, max_tokens) -> int` that reserves `max_tokens`,
      `_TEMPLATE_OVERHEAD_TOKENS`, and a ~10% safety margin.
- [ ] Add `_prompt_preflight(...)` called immediately before every POST. If the estimate exceeds the
      safe ceiling, trim again via `_fit_history_to_budget`; if it *still* cannot fit, return a
      **friendly, actionable error** ("This conversation no longer fits the local context window —
      compacting…") instead of letting the server emit a 400.
- [ ] Record actual usage (from the response / `stream_options.include_usage`) into `AgentState` so the
      Phase 5 indicator can render it.

**Acceptance:** a session containing the two `poll() failed` tracebacks plus ≥20 turns no longer
produces a server 400.

### Phase 2 — Startup Context Validation & First-Run Defaults

**Goal:** one source of truth for context size; correct first-run recommendation; warning, never a
silent restart.

**Files:** `addon/bfa_coworker/llm_manager.py`, `addon/bfa_coworker/preferences.py`

- [ ] Add `get_runtime_ctx(port) -> int | None` that queries `http://127.0.0.1:<port>/props` after
      launch, reads the applied `n_ctx`, and logs any mismatch with the configured value.
- [ ] Pipe the runtime value through `LLMConfig` / `get_config()` so Phase 1 can use it.
- [ ] **Remove the silent auto-bump** (`ctx_size <= 8192 → 32768`) in `start_local_llama`
      (`llm_manager.py:~2785`).
- [ ] Reconcile the defaults (`LLMConfig.local_ctx_size` vs `LLMPreferences.local_ctx_size`) into a
      single constant; ensure the pref is always the value synced to config.
- [ ] Add `validate_ctx_against_hardware(model_gb, backend, ctx) -> str | None` returning a warning
      string when the configured ctx is too large for detected memory. Surface it in preferences.
- [ ] Improve `recommend_context_size()` to use detected free memory and include KV-growth headroom,
      so the first-run default "just works" (no startup OOM).
- [ ] Do **not** add any runtime llama-server restart (per D4).

**Acceptance:** with an over-large preset the preferences/panel show a clear warning; with a fresh
config on a low-RAM machine the recommended value starts cleanly.

### Phase 3 — Qwen llama.cpp Flag Hardening

**Goal:** better memory and prompt-cache behaviour for the Qwen presets.

**File:** `addon/bfa_coworker/llm_manager.py` (`ModelPreset`, `PRESET_MODELS`, `start_local_llama`)

- [ ] Add `extra_server_args: tuple[str, ...] = ()` to `ModelPreset` and populate it for Qwen entries.
- [ ] Add global flags in the `args` list built by `start_local_llama`:
      `--flash-attn`, optional `--cache-type-k q8_0 --cache-type-v q8_0` (KV quant, exposed as a
      preference for tight-memory machines), `--batch-size` / `--ubatch-size`, and prompt-cache reuse
      (`--cache-reuse`).
- [ ] **Verify every flag name against the pinned build** `_LLAMA_SERVER_VERSION = "b10154"` before
      shipping — llama.cpp flag names drift across builds. Guard unknown flags behind a version check.
- [ ] Keep CPU backend unaffected (flash-attn / KV quant are GPU-oriented).

**Acceptance:** server starts with the new flags on CUDA and CPU; a repeated turn reuses the prompt
cache (visible in server log) and KV memory is reduced when quant is enabled.

### Phase 4 — Session Memory & Automatic Checkpoint Engine

**Goal:** conversations feel infinite; continuity is preserved without user effort.

**New file:** `addon/bfa_coworker/session_memory.py`

- [ ] **Bounded verbatim window** — define `MAX_WINDOW_TURNS`; older turns are retired from the window.
- [ ] **Disk archive** — `archive.jsonl` written next to the existing chat-history JSON
      (see `ui_chat.py:658–760`); retired raw turns appended at full fidelity.
- [ ] **Memory block builder** — `build_memory_block(retired_turns, prior_memory) -> str` producing the
      structured format in §5.2. Injected as a pinned message right after the system prompt.
- [ ] **Compaction trigger** — when the estimated prompt crosses ~60% of the safe budget (Phase 1),
      start a compaction turn:
  - show `on_status("Compacting conversation…")` (D3);
  - run a dedicated small-`max_tokens` LLM call that rewrites the memory block from the turns about to
    retire (D2);
  - swap the retired turns out of the live window and append them to the archive;
  - snapshot an automatic checkpoint (§5.3).
- [ ] **Checkpoint API** — `snapshot(reason)`, `list_checkpoints()`, `restore(index)`,
      `branch(index)`. Restore auto-checkpoints the current state first (non-destructive).
- [ ] **Persistence** — extend save/load to store `memory`, `checkpoints`, and the archive path so the
      memory block survives a Blender restart.

**Acceptance:** crossing the threshold injects a current memory block, archives the retired turns,
shows the compaction status, and a follow-up question referencing an old decision is still answered
correctly.

### Phase 5 — UI & Operators

**Files:** `addon/bfa_coworker/ui_chat.py`, `addon/bfa_coworker/preferences.py`

- [ ] New operators: `bfacw.session_checkpoint_restore`, `bfacw.session_checkpoint_branch`,
      `bfacw.session_memory_view_edit`, and `bfacw.session_compact_now` (convenience trigger only).
- [ ] Add a **Session** section to `BFACW_PT_chat_panel` (`ui_chat.py:1798`) containing:
  - the automatic checkpoint list (with Restore / Branch),
  - a read-mostly memory preview (editable via the view/edit operator),
  - the **context-usage (%) bar** fed by Phase 1 usage,
  - the **"Compacting conversation…"** status line.
- [ ] Keep the existing `bfacw.chat_clear` ("New Thread", `ui_chat.py:887`) as-is.
- [ ] Extend `_save_chat_history` / `_load_chat_history` for the new fields.

**Acceptance:** the panel shows usage %, checkpoint list, and memory preview; restore rewinds;
branch creates an independent thread; compaction status is visible while it runs.

### Phase 6 — Tool-Output Slimming

**File:** `addon/bfa_coworker/agent_controller.py` (`_trim_tool_result` ~`:4025`, spiral handling
~`:5614`)

- [ ] Detect the `Operator ...poll() failed, context is incorrect` class and collapse it to a
      **one-line corrective hint** (e.g. suggest `context.temp_override` / setting the active object)
      instead of re-storing the full traceback. Keep head+tail smart-trim for other error classes.
- [ ] Prune `role:"reasoning"` entries from **storage** once they scroll out of the verbatim window
      (they are UI-only and already stripped at send time), so the persisted JSON stops growing.

**Acceptance:** the two `poll()` errors from §2 occupy a few lines, not kilobytes, in the next request.

### Phase 7 — Tests & Documentation

- [ ] `tests/test_context_budget.py` — tool-schema counting, budget math against a fake runtime ctx,
      preflight returns a friendly error instead of overflowing.
- [ ] `tests/test_session_memory.py` — compaction trigger, archive write, memory-block build,
      checkpoint restore/branch round-trips.
- [ ] Extend `tests/test_llm_manager.py` — `/props` parsing, `validate_ctx_against_hardware`, flag
      assembly (guarded by pinned build version).
- [ ] Update `CHANGELOG.md` and regenerate wiki pages per the `bfa-coworker-release-docs` skill.
- [ ] Run `_misc/check_ascii.py` and `_misc/check_namespace.py` guards.

---

## 7. Files Touched

| File | Change |
|---|---|
| `addon/bfa_coworker/agent_controller.py` | Real-ctx budget, prompt preflight, tool-output slimming, reasoning prune, usage reporting |
| `addon/bfa_coworker/llm_manager.py` | `/props` runtime ctx, remove silent bump, `validate_ctx_against_hardware`, Qwen flags |
| `addon/bfa_coworker/preferences.py` | Single ctx default, over-size warning, KV-quant toggle |
| `addon/bfa_coworker/ui_chat.py` | Session panel section, checkpoint/restore/branch operators, usage bar, compaction status |
| `addon/bfa_coworker/shared.py` | Enum mirrors if new preferences are added |
| **`addon/bfa_coworker/session_memory.py`** | **NEW** — window/archive/memory/checkpoint engine |
| `tests/test_context_budget.py` | **NEW** |
| `tests/test_session_memory.py` | **NEW** |
| `tests/test_llm_manager.py` | Extended |
| `CHANGELOG.md`, wiki | Updated |

---

## 8. Verification Plan

1. **Runtime ctx** — launch local mode; confirm `/props` reports the configured `n_ctx` and
   `get_runtime_ctx()` matches. Repeat with an over-large preset → Phase 2 warning appears.
2. **No overflow** — reproduce a long session including an intentional `object.join` `poll()` error;
   confirm **no 400** and the usage bar stays under the ceiling.
3. **Compaction continuity** — cross the threshold; confirm the memory block is injected, retired
   turns land in `archive.jsonl`, the "Compacting…" status shows, and a follow-up referencing an old
   decision is answered correctly.
4. **Checkpoints** — verify an automatic checkpoint exists after compaction; `restore` rewinds
   history + memory; `branch` yields an independent thread. No manual creation is required.
5. **Flags** — verify the new llama.cpp flags are accepted by build `b10154` on CUDA and CPU.
6. **Tests** — `pytest tests/test_context_budget.py tests/test_session_memory.py tests/test_llm_manager.py`;
   plus `_misc/check_ascii.py` / `check_namespace.py`.
7. **Cold start** — fresh config on a low-RAM machine: recommended ctx starts with no OOM.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| llama.cpp flag names differ in the pinned build | Verify against `b10154`; version-guard and fall back to omitting unknown flags |
| Compaction LLM turn produces a poor summary and loses continuity | Structured template (§5.2); keep verbatim window large enough that recent detail is never summarised; archive preserves everything for restore |
| Compaction adds latency the user notices | Visible "Compacting conversation…" status (D3); trigger lazily at the threshold with the most recent window still verbatim |
| `/props` not available on some builds | `get_runtime_ctx()` falls back to the configured value and logs the fallback |
| KV quant degrades output quality for some users | Exposed as an opt-in preference, off by default except on very low VRAM |
| Checkpoint/restore corrupts history | Restore snapshots the current state first (non-destructive); reuses existing `_sanitize_loaded_history()` on load |

---

## 10. Out of Scope

- Remote-provider context management (remote keeps `prompt_budget = 0`; only gains the tool-schema
  accounting shared with local).
- Any runtime llama-server restart or hot ctx resize (explicitly declined, D4).
- Cross-session/global memory shared between different `.blend` files.
- Changes to the MCP tool set itself.

---

## 11. Closure (2026-09-30)

All phases are implemented and covered by tests. Phase → code map:

| Phase | Status | Where |
|---|---|---|
| 1 — Real context accounting | ✅ | `agent_controller.py`: `_compute_prompt_budget` `:463`, `_estimate_tools_tokens` `:446`, `_prompt_preflight` `:490`; `tests/test_context_budget.py` |
| 2 — Startup ctx validation | ✅ | `llm_manager.py`: `get_runtime_ctx` `:1803`, `validate_ctx_against_hardware` `:1754`; silent auto-bump removed; `tests/test_llm_manager.py` |
| 3 — Qwen llama.cpp flags | ✅ | `llm_manager.py`: preset `extra_server_args`; `_current_preset_extra_args()` (this branch); `_filter_flags_for_build` |
| 4 — Session memory & checkpoints | ✅ | `addon/bfa_coworker/session_memory.py`; `_maybe_compact_session`; `tests/test_session_memory.py` |
| 5 — UI & operators | ✅ | `ui_chat.py` Session section + restore/branch/view-edit/compact-now |
| 6 — Tool-output slimming | ✅ | `_collapse_known_errors` (poll + index), reasoning prune from storage |
| 7 — Tests & docs | ✅ | `tests/test_context_budget.py`, `tests/test_session_memory.py`, `tests/test_llm_manager.py`; CHANGELOG + wiki |

**Closed gap (this branch)**: `_current_preset_extra_args()` was called at
`llm_manager.py:3000` but never defined, so Qwen presets' `--no-context-shift`
was never applied and launch raised `NameError`. Now implemented and tested.

**Beyond the plan (this branch)**: live llama-server context-overflow 400s are now
handled gracefully — the transport caches the error body once, classifies
`exceed_context_size_error` as `context_overflow`, and the turn loop
**auto-compacts and retries once**; every POST path (screenshot, auto-continue,
forced-summary) is budgeted; and a conservative fallback ctx keeps enforcement on
when `/props` is unreachable. See `plan_cowork_scene_safety_and_local_hardening.md`
§11 and the CHANGELOG.
