# BFA Coworker — Co-work Scene Safety & Local (Qwen) Run Hardening

**Date**: 2026-09-30
**Status**: Planning — Approved in principle, ready for implementation
**Depends on**: Tier 3 (session memory & context budget), Tier 3g (MCP intent architecture / preflight), Tier 3h (quality audit)
**Blocks**: Nothing — hardens existing local-mode behaviour
**Branch**: `6117603f` worktree
**Estimated scope**: ~700–1,000 LOC + tests

---

## Table of Contents

1. [Summary](#1-summary)
2. [The Failures We Are Fixing](#2-the-failures-we-are-fixing)
3. [Root Cause Analysis](#3-root-cause-analysis)
4. [Design Decisions](#4-design-decisions)
5. [Architecture](#5-architecture)
6. [Implementation Plan](#6-implementation-plan)
   - [Phase 0 — This Document](#phase-0--this-document)
   - [Phase 1 — Co-work Scene Lock](#phase-1--co-work-scene-lock)
   - [Phase 2 — Preflight: Operator Context Preconditions](#phase-2--preflight-operator-context-preconditions)
   - [Phase 3 — Preflight: Unguarded List Indexing](#phase-3--preflight-unguarded-list-indexing)
   - [Phase 4 — Error-Side Hints & Collapsing](#phase-4--error-side-hints--collapsing)
   - [Phase 5 — User-Edit Detection & Re-sync](#phase-5--user-edit-detection--re-sync)
   - [Phase 6 — Scoped Auto-Undo](#phase-6--scoped-auto-undo)
   - [Phase 7 — Local (Qwen) Run Hardening](#phase-7--local-qwen-run-hardening)
   - [Phase 8 — Prompts, Skills & Documentation](#phase-8--prompts-skills--documentation)
   - [Phase 9 — Tests & Static Checks](#phase-9--tests--static-checks)
7. [Files Touched](#7-files-touched)
8. [Verification Plan](#8-verification-plan)
9. [Risks & Mitigations](#9-risks--mitigations)
10. [Out of Scope](#10-out-of-scope)

---

## 1. Summary

Two independent problems surfaced during real local-model (Qwen) runs:

1. **Precondition failures in generated code.** Three tracebacks repeated across a session:

   ```
   RuntimeError: Operator bpy.ops.object.join.poll() failed, context is incorrect
   RuntimeError: Operator bpy.ops.object.modifier_apply.poll() failed, context is incorrect
   IndexError: list index out of range
   ```

   Each is a *precondition* error: a missing active object, a missing/invalid selection, or an
   index into an empty collection. The generator emits code that assumes those preconditions hold.

2. **The user and the agent edit the same scene at the same time.** The turn runs on a
   background daemon thread while Blender's event loop stays live, so **between tool calls the
   user can change the selection, the active object, the mode, and add/delete objects**. The
   agent's next script then acts on a scene that no longer matches its assumptions — producing
   exactly the class of error above. Worse, two existing recovery mechanisms are unsafe when the
   user is editing:

   - **Auto-undo uses global LIFO undo** (`bpy.ops.ed.undo()`), so it can revert the *user's* last
     edit rather than the agent's failed work.
   - **The cleanup fallback is a name-diff**, so it cannot distinguish agent-created from
     *user-created* datablocks and can **delete the user's objects**.

This plan fixes both, in four layers:

- **Soft scene lock** — while a turn is active, objects/collections the coworker created or
  touched are `hide_select`-locked so the *user* cannot re-select and re-target them in the
  viewport, while the agent's programmatic access keeps working. Optionally disableable.
- **Static preflight hardening** — new checks catch the precondition and indexing mistakes
  *before* execution, with actionable fixes.
- **Error-side hints + collapsing** — when something still fails, the traceback is turned into
  an operator-specific corrective message (and spiral messages cover the new cases).
- **Scoped undo + user-edit detection** — the agent undoes *only its own* step, restores the
  selection/active object it assumed, and, when it detects the user changed the scene, tells the
  model to re-fetch references by name instead of fighting the user.

Finally, a long-standing local-mode bug is fixed: the Qwen preset launch flags
(`--no-context-shift`) were never applied because the helper that reads them was never defined.

---

## 2. The Failures We Are Fixing

### 2.1 Observed tracebacks (local Qwen run)

```json
{"status": "error", "message": "... RuntimeError: Operator bpy.ops.object.join.poll() failed, context is incorrect"}
{"status": "error", "message": "... RuntimeError: Operator bpy.ops.object.modifier_apply.poll() failed, context is incorrect"}
{"status": "error", "message": "... IndexError: list index out of range"}
```

### 2.2 The three Blender preconditions

| Error | Precondition that was false | Typical bad code |
|---|---|---|
| `object.join.poll()` | >= 2 objects selected **and** one object active | `bpy.ops.object.join()` after the user changed the selection |
| `object.modifier_apply.poll()` | an active object that owns modifiers | `bpy.ops.object.modifier_apply(modifier=m.name)` with no active object |
| `IndexError` | a non-empty collection | `bpy.context.selected_objects[0]`, `bpy.data.objects[0]` |

`bpy.ops.object.join` takes **no arguments** (it acts on the current selection/active object),
so it is inherently selection-dependent — the most fragile operator in the set.

### 2.3 The concurrency race

* The turn is spawned on a daemon thread (`ui_chat.py:943`), and `run_conversation_turn`
  is documented as blocking (`agent_controller.py:4051`).
* Scene mutations are serviced on Blender's main thread by a `bpy.app.timers` pump
  (`execute_interactive.run` -> `mcp_to_blender_server.poll`, registered at `ui_chat.py:1543`).
* Between tool calls the user is fully interactive. Only *during* one code execution is the main
  thread blocked (`_exec_thread.join(timeout=30)`, `mcp_to_blender_server.py:797-813`).

Result: a user click between the LLM's response and the code's execution can invalidate the
code's assumptions.

---

## 3. Root Cause Analysis

### 3.1 Preflight covers API shape, not operator context

`_preflight_check` (`addon/bfa_coworker/mcp_to_blender_server.py:390`) has 27 regex checks. It
catches *renamed attributes* (`subdivisions` -> `levels`), *removed APIs* (`action.fcurves`), and
*missing imports*, but it has **no check that an operator's context preconditions are met**:

- `wrong_collection_active` (`:513`) — only covers `bpy.data.<coll>.active`.
- `context_active_object_thread` (`:521`) — only forbids `bpy.context.active_object`.
- `wrong_mode_set` (`:557`) — only validates `mode_set` with `POSE`.

Nothing flags `object.join` without a preceding selection, or `modifier_apply` without an active
object, or an unguarded `[0]`.

### 3.2 The error path is generic

When a poll failure does occur, `_collapse_poll_failed_error`
(`agent_controller.py:3283`) collapses the traceback to **one generic hint** about
`temp_override()`. It does not say which operator needs which context. The generic
`_spiral_corrective_message` fallback (`:3434`) has no branch for `poll() failed` or
`IndexError`.

### 3.3 Auto-undo is global

`agent_controller.py:4906-4924`:

```python
if should_undo:
    _undo_result = _call_mcp_tool_sync(
        "execute_blender_code", {"code": _undo_code("undo")}, mcp_port)   # bpy.ops.ed.undo()
    if '"status": "error"' in _undo_result:
        _cleanup_code = _build_cleanup_code(_turn_entities)
        ...
    _call_mcp_tool_sync("execute_blender_code",
        {"code": _undo_code("push", "bfa_coworker_pre_script")}, mcp_port)
```

`bpy.ops.ed.undo()` pops the most recent undo step **regardless of who created it**. If the user
edited after the agent's `bfa_coworker_step` push, the agent reverts the user's work.

### 3.4 The cleanup fallback cannot attribute authorship

`_build_cleanup_code(diff)` (`agent_controller.py:3709`) deletes every datablock in
`_turn_entities` — a **name-only set diff** (`_diff_snapshots`, `:3682`) taken since the turn
started. A user-created object that appears after the initial snapshot is indistinguishable from
an agent-created one, so the fallback can delete it.

### 3.5 Nothing prevents user re-targeting

There is no lock system. The only "disable while running" precedent is the Mode Switch Lock
(`preferences.py:298-316`). `Object.hide_select` / `Collection.hide_select` appear only in bundled
Blender API docs, never set by the addon.

### 3.6 The Qwen preset flags are never applied (latent bug)

`llm_manager.py:3000` calls `_current_preset_extra_args()`, which is **not defined anywhere in the
repository** (single occurrence = the call site). `NameError` is not caught by the surrounding
`except OSError`/`FileNotFoundError`, so it propagates out of `start_local_llama`. Presets still
carry `extra_server_args=("--no-context-shift",)` (`llm_manager.py:611,633,655,699,721,765,787`)
and `tests/test_llm_manager.py:606` validates the *data* only, so the defect is untested.

---

## 4. Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| **D1** | Scope of the code-execution hardening | **Shared (local and remote)** | The preflight checks and hints are mode-agnostic; remote models make the same mistakes. Simpler than maintaining two paths. |
| **D2** | Co-work protection | **Soft lock + robustness fixes** | Locking alone does not fix undo attribution; name-pinning alone does not stop re-targeting. Together they cover both. |
| **D3** | Lock granularity | **Managed objects/collections only** | The coworker locks what it created or touched; the user keeps full control of everything else. Preserves co-working. |
| **D4** | Lock mechanism | **`Object.hide_select` / `Collection.hide_select` only** | Blocks user picking in the viewport/outliner while programmatic `select_set()` and `bpy.ops` context overrides still work. |
| **D5** | Transform locks | **Not used** (`lock_location/rotation/scale` off) | They break `bpy.ops.transform.*`-based agent code and are unnecessary for selection conflicts. |
| **D6** | Whole-build freeze (Mixar-style) | **Rejected** | Requires Blender source modification (`_misc/Plans History/plan_external_harness.md:967` marks it fork-only) and defeats co-working. |
| **D7** | On user edits mid-turn | **Detect + re-sync + inform the model** | Never block the user and never abort. The model is told the scene changed and re-fetches by name. |
| **D8** | Undo strategy | **Scoped cleanup first; global undo only when safe** | Deleting only the failed step's datablocks can never touch user work. Global undo is retained solely as a verified-safe fallback (no foreign edit detected). |
| **D9** | In-place edits (`modifier_apply`, mesh edits) | **Not reverted by scoped cleanup** | They cannot be undone per-datablock; the agent may leave them applied. Documented behaviour, not a regression. |
| **D10** | Lock preference default | **ON**, user-disableable | Safe default; escape hatch for users who want zero interference. |
| **D11** | Local iteration budget | **12 local / 8 remote** | Qwen needs more repair rounds; remote models do not. |

---

## 5. Architecture

### 5.1 Lock lifecycle

```
turn start
  |
  +-- first execute_blender_code
  |     +-- initial entity snapshot (unchanged)
  |     +-- undo_push("bfa_coworker_pre_script")   (unchanged)
  |     +-- LOCK: hide_select on names in the step diff            <-- NEW
  |
  +-- each successful step
  |     +-- undo_push("bfa_coworker_step")          (unchanged)
  |     +-- LOCK: merge the step diff into the managed set         <-- NEW
  |
  +-- finally (always: FINISH / error / Stop / exception)
        +-- UNLOCK: restore every managed flag from its saved value <-- NEW
```

The lock is **session state**, not scene state: the registry lives in the addon, so a Blender
crash cannot leave objects permanently locked (flags reset on file reload anyway).

### 5.2 Three layers of protection

| Layer | When it acts | Mechanism |
|---|---|---|
| **Prevent** | Before the user can re-target | `hide_select` soft lock on managed objects (Phase 1) |
| **Detect** | Before the agent acts on stale assumptions | Snapshot fingerprint of active object / selection / mode (Phase 5) |
| **Recover** | After a failure | Operator-aware hints (Phase 4) + scoped undo (Phase 6) |

Static preflight (Phases 2–3) reduces how often the recover layer is needed at all.

### 5.3 Scoped-undo algorithm

On a failed step with `_prev_code_errored` and not `_error_is_code_bug`:

1. Build the **diff for the failed step only** (not the whole-turn merge).
2. Run `_build_cleanup_code(step_diff)` — removes only datablocks created in that step.
3. Restore the **pre-step active object and selection** from the snapshot.
4. Use global `bpy.ops.ed.undo()` **only if** Phase 5's foreign-edit detector reports no user
   change since the pre-step push (then the top step is guaranteed to be the agent's own).
5. Re-push `bfa_coworker_pre_script` and refresh the lock.

### 5.4 Snapshot extension

`_EntitySnapshot` (`agent_controller.py:3594`) gains three fields, captured by the same
main-thread snapshot toolcode:

| Field | Type | Purpose |
|---|---|---|
| `active_object` | `str` | Detect the user re-targeting the active object |
| `selected_object_names` | `set[str]` | Detect the user changing the selection |
| `mode` | `str` | Detect the user entering Edit/Pose mode |

`from_dict` (`:3610`) must read these defensively (older payloads lack them).

---

## 6. Implementation Plan

### Phase 0 — This Document

- [ ] Write and commit `_misc/plan_cowork_scene_safety_and_local_hardening.md`.
- [ ] Confirm D1–D11 with the maintainer (done — see section 4).

### Phase 1 — Co-work Scene Lock

**Files:** `addon/bfa_coworker/co_work_guard.py` (new), `addon/bfa_coworker/agent_controller.py`,
`addon/bfa_coworker/llm_manager.py`, `addon/bfa_coworker/preferences.py`,
`addon/bfa_coworker/ui_chat.py`, `addon/bfa_coworker/__init__.py`

- [ ] **Phase 1a — Verify `hide_select` semantics (gate).** In Blender 5.3, on a locked object
      (`hide_select = True`), confirm the agent's own `obj.select_set(True)` and
      `bpy.ops.object.join()` still work programmatically.
      - Expected: **yes, they still work** (`hide_select` gates UI picking only).
      - If they do **not** work, add a lift/reapply step in `mcp_to_blender_server._execute_code`
        around the exec so the lock is lifted for the sub-second exec window only.
- [ ] Create `addon/bfa_coworker/co_work_guard.py` with SPDX header and `__all__`:
      - `_MANAGED_OBJECTS: dict[str, dict]` — name -> saved `hide_select`.
      - `_MANAGED_COLLECTIONS: dict[str, bool]` — name -> saved `Collection.hide_select`.
      - `is_locked() -> bool`, `managed_names() -> tuple[set[str], set[str]]`,
        `record_managed(objects: Iterable[str], collections: Iterable[str]) -> None`,
        `clear() -> None`.
      - `build_lock_code(object_names, collection_names) -> str` — toolcode
        (`# blmcp-toolcode-skip-preflight`) that sets `hide_select = True`, records the prior
        value into the `result` dict, and returns the count.
      - `build_unlock_code() -> str` — toolcode that restores every saved value.
      - Keep it **pure string generation + plain dicts** so it is unit-testable without `bpy`.
- [ ] Add `lock_scene_while_working: bool = True` to `LLMConfig` (`llm_manager.py:493`), pipe it
      through `set_config` (`:1111`) and `get_config` (`:1132`).
- [ ] Add the `lock_scene_while_working` BoolProperty to `preferences.py` with a tooltip that
      explains the coworker temporarily makes its own objects un-selectable, and that it
      restores them when the turn ends.
- [ ] Wire the preference through `ui_chat._sync_prefs_to_config` (`:62-83`),
      `preferences._update_*` handlers, and `__init__.py` (mirror the existing
      `thinking_budget_tokens` plumbing at `preferences.py:762`, `__init__.py:255`).
- [ ] In `_run_conversation_turn_inner`, after the first code step's snapshot/push: if the
      preference is on, call `co_work_guard.record_managed(...)` with the step diff's
      `object_names` / `collection_names`, then run `build_lock_code(...)` as a toolcode call.
- [ ] After each successful step, merge the step diff into the managed set and re-run the lock
      code for the new names only.
- [ ] **Unlock in a `finally`** around `_run_conversation_turn_inner` inside
      `run_conversation_turn` (`agent_controller.py:4081-4091`), so FINISH, error, Stop, and
      exceptions all unlock.
- [ ] Also unlock from `request_stop()` (`agent_controller.py:1343`) and `unregister()`
      (`__init__.py`).
- [ ] Add a UI hint (chat panel status text) that says the coworker has temporarily locked N
      objects while it works.

**Deliverable:** the user cannot re-select/re-target coworker-managed objects during a turn, and
they are always unlocked when the turn ends.

### Phase 2 — Preflight: Operator Context Preconditions

**Files:** `addon/bfa_coworker/mcp_to_blender_server.py` (`_preflight_check`, `:390`)

- [ ] Add `op_requires_selection`: flag `bpy.ops.object.join` / `join_shapes` when neither
      `select_set(` nor `select_all(` appears earlier in the same code block.
      Guidance: *"`object.join()` needs >= 2 selected objects and one active object. Select
      explicitly first: `for o in objs: o.select_set(True)` then
      `bpy.context.view_layer.objects.active = objs[0]`."*
- [ ] Add `op_requires_active_object`: flag `modifier_apply`, `modifier_remove`, `shade_smooth`,
      `shade_flat`, `convert`, `origin_set`, `make_single_user`, and `mode_set` when no
      `view_layer.objects.active` assignment and no `bpy.data.objects.get(` guard appears earlier.
      Guidance names the operator-specific fix.
- [ ] Emit at most one hint per code block (match the existing single-hint style for
      `wrong_bsdf_input`, `:466-476`).
- [ ] Ensure the checks do **not** fire for repository toolcode (`# blmcp-toolcode-skip-preflight`).

### Phase 3 — Preflight: Unguarded List Indexing

**Files:** `addon/bfa_coworker/mcp_to_blender_server.py`

- [ ] Add `unguarded_list_index` for Blender collection expressions indexed with a literal int:
      `bpy.context.selected_objects[`, `selected_objects[`, `bpy.data.<coll>[`,
      `scene.objects[`, `view_layer.objects[`, `view_layer.objects.active` free code paths.
- [ ] Suppress when a guard is present earlier in the block: `len(`, `if <expr>`, `.get(`,
      `next(iter(`.
- [ ] **Do not** match arbitrary `name[0]` (verts, materials, node lists) to avoid false
      positives — anchor on the known Blender collection roots only.
- [ ] Guidance: *"`selected_objects`/`bpy.data.<coll>` may be empty. Guard with `len()` or use
      `next(iter(...), None)`; and note the selection may have changed since the last call."*

### Phase 4 — Error-Side Hints & Collapsing

**Files:** `addon/bfa_coworker/mcp_to_blender_server.py` (traceback HINT block `:842-976`),
`addon/bfa_coworker/agent_controller.py` (`_collapse_poll_failed_error` `:3283`,
`_spiral_corrective_message` `:3434`)

- [ ] Add a `poll() failed` HINT to the traceback block with an operator table:
      - `join` -> needs >= 2 selected + active object.
      - `modifier_apply` / `modifier_remove` -> set `view_layer.objects.active`; confirm the
        modifier exists.
      - `mode_set` -> needs an active object and the correct mode.
      - generic -> `bpy.context.temp_override(...)`.
- [ ] Add an `IndexError: list index out of range` HINT: the collection was empty; guard with
      `.get()` / `len()`; note that auto-undo or a user edit may have removed the objects.
- [ ] Extend `_collapse_poll_failed_error` with an operator -> fix map keyed on the operator name
      extracted by the existing regex `Operator bpy\.ops\.([\w.]+)\.poll\(\) failed`.
- [ ] Generalise collapsing: rename/extend to handle index errors too (e.g. a sibling
      `_collapse_index_error`, or a `_collapse_known_errors` dispatcher). Keep the call site at
      `agent_controller.py:5013` unchanged in behaviour for other errors.
- [ ] Add `_spiral_corrective_message` branches:
      - `poll() failed` -> re-select and set active, then retry.
      - `index out of range` -> the collection is empty; re-fetch by name and guard.
      - `scene changed` -> the user edited the scene; re-inspect and re-fetch references.

### Phase 5 — User-Edit Detection & Re-sync

**Files:** `addon/bfa_coworker/agent_controller.py`

- [ ] Extend `_EntitySnapshot` (`:3594`) with `active_object: str = ""`,
      `selected_object_names: set[str] = field(default_factory=set)`, `mode: str = ""`.
- [ ] Update `from_dict` (`:3610`) to read the new keys defensively.
- [ ] Update the snapshot toolcode generator so it also reports `active_object`
      (via `bpy.context.view_layer.objects.active`), `selected_object_names`
      (via `bpy.context.selected_objects`), and `mode` (via `bpy.context.mode`).
- [ ] At each step boundary, compare the pre-step and post-step fingerprints **outside** the
      step's own diff; a change that is not attributable to the step is treated as a user edit.
- [ ] On detecting a foreign change:
      - refresh the managed-lock registry from the new state,
      - suppress global undo for the next failure (D8 fallback branch),
      - inject `history` message:
        `[System: The user changed the scene while you were working — active object is now X, selection is [Y], mode is Z. Re-fetch references by name (bpy.data.objects.get) and re-select explicitly before acting. Do not assume your previous selection still holds.]`
- [ ] Keep detection best-effort: wrap in `try/except` and never break the turn.
- [ ] Skip detection in Ask mode (no code execution).

### Phase 6 — Scoped Auto-Undo

**Files:** `addon/bfa_coworker/agent_controller.py` (`:4906-4924`)

- [ ] Track the **per-step diff** (`_step_diff`) separately from the whole-turn merge
      (`_turn_entities`), so cleanup can target only the failed step.
- [ ] Replace the unconditional global undo with the section 5.3 algorithm:
      1. `_build_cleanup_code(_step_diff)` (always).
      2. Restore the pre-step active object + selection (generated toolcode).
      3. Global `bpy.ops.ed.undo()` **only** when Phase 5 reported no foreign edit.
- [ ] Keep the existing `_error_is_code_bug` fast path (`:3365`) unchanged.
- [ ] Update `_build_cleanup_code`'s docstring to state that it is now the primary path and that
      it only removes step-attributed datablocks.
- [ ] Re-push `bfa_coworker_pre_script` after cleanup (as today) and refresh the lock.
- [ ] Document D9 (in-place edits such as `modifier_apply` are not reverted) in the module
      comment so the behaviour is intentional.

### Phase 7 — Local (Qwen) Run Hardening

**Files:** `addon/bfa_coworker/llm_manager.py`, `addon/bfa_coworker/agent_controller.py`

- [ ] Implement `_current_preset_extra_args()` (`llm_manager.py:3000`):
      - return the current model preset's `extra_server_args` by matching
        `ModelPreset.repo_id == _config.model_repo_id` **and**
        `ModelPreset.filename == _config.model_filename` (do **not** depend on the
        `preferences.model_preset` enum, which `LLMConfig` does not carry);
      - return `()` for custom / unknown models;
      - wrap in `try/except Exception` and log on failure so a lookup error can never break launch.
- [ ] Verify in the launch log that Qwen presets now show
      `start_local_llama: preset extra args = ('--no-context-shift',)`.
- [ ] Make `_MAX_TOOL_ITERATIONS` (`agent_controller.py:73`) mode-aware: 12 for local, 8 for
      remote, resolved once per turn next to the existing local/remote branch
      (`:4363-4373`) and used at the loop guard (`:4499`) and the final-summary check (`:5115`).
- [ ] Re-check the auto-continue / empty-response retry caps (`:4685-4779`) against the new
      iteration budget so they interact sanely (no unbounded growth).
- [ ] Re-run the prompt-budget regression after Phase 1 adds lock/snapshot toolcode, to confirm
      the compact prompt still fits (see `tests/test_context_budget.py`).

### Phase 8 — Prompts, Skills & Documentation

**Files:** `mcp/blmcp/data/prompts_compact.yml`, `mcp/blmcp/data/prompts.yml`,
`addon/bfa_coworker/skills/best_practices.md`, `CHANGELOG.md`

- [ ] Add a "Selection is not yours" rule to `prompts_compact.yml` and `prompts.yml`:
      never rely on the current selection/active object; resolve by name or set the selection
      explicitly in the same script; assume the user may have changed the scene.
- [ ] Add the same guidance to `skills/best_practices.md` under the existing context guidance.
- [ ] Add a short "Co-working with the scene" subsection to the compact prompt's Stability
      block (objects the coworker locks, and that they are released when the turn ends).
- [ ] Add a `CHANGELOG.md` entry (Unreleased).
- [ ] Regenerate wiki pages if the UI/preferences change is user-visible
      (per the `bfa-coworker-release-docs` skill).

### Phase 9 — Tests & Static Checks

**Files:** `tests/test_co_work_guard.py` (new), `tests/test_preflight.py`,
`tests/test_orchestration_helpers.py`, `tests/test_llm_manager.py`

- [ ] `tests/test_co_work_guard.py` — lock/unlock codegen round-trip (lock then unlock restores
      values); registry add/clear; `is_locked` transitions; no `bpy` import required.
- [ ] `tests/test_preflight.py` — positive cases for each new check (join without selection,
      `modifier_apply` without active, `selected_objects[0]` unguarded) **and** negative cases
      (join with an explicit selection; `verts[0]`; a guarded index) to prove no false positives.
- [ ] `tests/test_orchestration_helpers.py` — `_collapse_poll_failed_error` operator-specific
      output; index-error collapsing; new `_spiral_corrective_message` branches; extended
      `_EntitySnapshot.from_dict` with and without the new keys.
- [ ] `tests/test_llm_manager.py` — `_current_preset_extra_args()` returns
      `("--no-context-shift",)` for a Qwen preset, `()` for a custom model, and never raises.
- [ ] `tests/test_context_budget.py` — regression: the extended snapshot toolcode still fits.
- [ ] Run the ASCII and namespace guards (`_misc/check_ascii.py`, `_misc/check_namespace.py`) —
      note that `check_ascii` scans `addon/` and rejects non-ASCII, so new user-facing strings
      must use `\u` / `\U` escapes; `co_work_guard.py` needs `__all__`.
- [ ] Run `make check_all`.

---

## 7. Files Touched

| File | Change |
|---|---|
| `addon/bfa_coworker/co_work_guard.py` | **New** — managed-object registry + lock/unlock codegen |
| `addon/bfa_coworker/agent_controller.py` | Lock lifecycle, snapshot fingerprint fields, foreign-edit detection, scoped undo, poll/index collapsing, spiral messages, mode-aware iteration cap |
| `addon/bfa_coworker/mcp_to_blender_server.py` | `_preflight_check` context + indexing rules; traceback HINT block; optional lock lift/reapply (Phase 1a) |
| `addon/bfa_coworker/llm_manager.py` | `_current_preset_extra_args()` fix; `lock_scene_while_working` config field |
| `addon/bfa_coworker/preferences.py` | `lock_scene_while_working` BoolProperty + sync |
| `addon/bfa_coworker/ui_chat.py` | Prefs -> config sync; lock status hint in the panel |
| `addon/bfa_coworker/__init__.py` | Unlock on unregister; pref sync |
| `mcp/blmcp/data/prompts_compact.yml` | "Selection is not yours" + co-work rule |
| `mcp/blmcp/data/prompts.yml` | Same rule (full prompt) |
| `addon/bfa_coworker/skills/best_practices.md` | Same guidance |
| `tests/test_co_work_guard.py` | **New** — lock codegen/registry tests |
| `tests/test_preflight.py` | New checks + false-positive guards |
| `tests/test_orchestration_helpers.py` | Collapsing, spiral, snapshot tests |
| `tests/test_llm_manager.py` | Preset extra-args test |
| `CHANGELOG.md` | Unreleased entry |

---

## 8. Verification Plan

### Unit

```text
python -m unittest tests.test_co_work_guard tests.test_preflight \
    tests.test_orchestration_helpers tests.test_llm_manager \
    tests.test_context_budget -v
make test
make check_all
```

### Manual (Blender 5.3, local Qwen preset)

- [ ] Launch log shows `preset extra args = ('--no-context-shift',)` (Phase 7).
- [ ] Start a turn that creates several objects; try to select one in the viewport while the turn
      is running — it cannot be picked (Phase 1).
- [ ] Stop the turn mid-flight and confirm everything is selectable again (Phase 1 `finally`).
- [ ] Edit a *different* object while the turn runs; confirm the model is told the scene changed
      and re-fetches by name (Phase 5).
- [ ] Force a failing step; confirm the user's object is not deleted and the user's last edit is
      not reverted (Phase 6).
- [ ] Reproduce all three original tracebacks and confirm each now produces the operator-specific
      hint (Phases 2–4).
- [ ] Confirm the compact prompt still fits the configured context on a small preset (Phase 7).

---

## 9. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| `hide_select` also blocks the agent's `select_set()` | Agent cannot operate on locked objects | Phase 1a gate: verify first; if blocked, lift/reapply in `_execute_code` around exec |
| Lock changes push undo steps, polluting the undo stack | Scoped undo / user undo steps misaligned | Apply and restore lock flags with global undo temporarily disabled (`preferences.edit.use_global_undo = False` around the lock code) and/or restore in `finally` |
| Lock left active after a crash | Objects un-selectable | Lock is addon session state, not scene state; flags reset on file reload; `unregister()` unlocks |
| Snapshot fingerprint adds tokens/round-trips | Prompt bloat on small contexts | Fold the three fields into the **existing** snapshot toolcode call (no new round-trip); regression-test the budget |
| False positives from the new preflight checks block valid code | Agent refuses correct scripts | Anchor index checks to known Blender collection roots; keep one hint per block; add negative tests |
| Scoped cleanup misses in-place edits (`modifier_apply`) | Scene left partially modified | Documented (D9); global undo remains available on the verified-safe path |
| Foreign-edit detector misclassifies agent changes as user edits | Unnecessary "scene changed" messages | Compare only outside the step's own diff; best-effort and non-blocking |
| `_current_preset_extra_args` lookup fails for custom models | Missing flags | Return `()`; wrap in `try/except` so launch never breaks |

---

## 10. Out of Scope

- **Whole-build / viewport freeze** (Mixar-style) — fork-only, requires Blender source changes (D6).
- **Transform locks** (`lock_location/rotation/scale`) — can break agent transform operators (D5).
- **Full property journaling** for perfect in-place undo — too invasive (D9).
- **A modal event-blocking overlay** — the lock is data-level, not input-level.
- **Multi-user / networked co-editing** — single-session co-working only.
- **Replacing the LLM's code generation with a restricted DSL** — the preflight + hints approach
  is retained.
