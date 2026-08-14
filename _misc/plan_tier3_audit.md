# BFA Coworker — Tier 3: Stability Audit + Tooling Optimization

**Date**: 2026-08-14
**Status**: Ready for Execution — Items Cross-Referenced with Code Audit
**Scope**: v1.1.37 stability fixes + tool system optimization for local models
**Cross-References**: CHANGELOG.md v1.1.37, `_misc/plan_tier4_editor_integration.md`, `_misc/plan_tier5_generative_local_systems.md`

---

## Executive Summary

The current system works well overall: the tiered tool loading is sound, Blender 5.3 compatibility is handled, and the smart-undo + entity-snapshot system is honest. The "two balls" (duplicate objects) bug has **three specific root causes**, all fixable. This document sequences fixes by priority, then lays out optimization strategy for future tool expansion.

---

## Part 1: Tiered Tool Loading System Audit

### How It Works Today

```
Surface tools (4) ── execute_blender_code, get_blendfile_summary_datablocks,
                      get_object_detail_summary, get_objects_summary
    │
    ▼ always loaded
Pre-detected domain (1 via _detect_domain())
    ─ _DOMAIN_KEYWORDS: 7 domains, first match wins
    ▼ mid-turn
load_tools meta-tool ── intercepted by agent_controller, adds domain's tools
```

### Verdict: Mostly Working, Needs Expansion Strategy

| Aspect | Status | Notes |
|--------|--------|-------|
| Undersized initial tool set | ✅ Good | 4-6 tools vs 30 keeps context small |
| Keyword detection | ⚠️ Limited | First-match-only; multi-domain prompts miss tools until `load_tools` |
| Domain definitions | ⚠️ Sparse | Only 7 domains with overlapping tools |
| Local-model reach | ✅ Good | Works with 32768 ctx window |
| Remote-mode skip | ✅ Correct | Remote mode gets full tool list |
| Tool availability | ✅ Complete | All 45 registered tools reachable via surface ∪ domains |

### Gap Analysis: What "Smarter and Contextual" Needs

The current domain system reduces token crunches but doesn't yet give the LLM **what it needs before asking**. Three missing layers:

1. **Auto-skills injection** — When keyword-detecting a domain, also auto-load the matching `blender_53.md` / `blender_52.md` skills. Today skills only appear after the user tells the LLM where to look. The LLM should see version-aware API rules upfront.
2. **Scene-aware domain pre-selection** — The system should scan the current scene before tool listing and pre-load domains based on what exists. If there's an armature + animation data, load "animation" domain automatically without the user typing "animate".
3. **Tool-result context trimming** — Right now tool results are truncated at 500 chars. Better: strip boilerplate from results (e.g., `{"status": "ok", "result": ...}` → just `result`), keep only structured fields the LLM needs.

---

## Part 2: Critical Fixes (The "Two Balls" / Duplicate-Object Bugs)

These are the root causes of duplicate object creation. Fix in this order.

### 🔴 Fix 1 — Add `_turn_active` Re-Entrancy Guard

**Problem**: `run_conversation_turn()` is designed to be called once per user message, but both chat send and test step buttons spawn daemon threads. No guard prevents overlapping turns.

**Impact**: Two concurrent turns interleave appends to `_agent_state.conversation_history`, corrupting tool-call pairing. A tool call from turn A might execute twice if turn B's thread picks it up from the same shared state.

**Files**:
- `addon/bfa_coworker/agent_controller.py` — `run_conversation_turn()` function entry
- `addon/bfa_coworker/ui_chat.py` — `BFACW_OT_chat_send.execute()`

**What to do**:
1. Add `turn_active: bool = False` to `AgentState`
2. Guard at top of `run_conversation_turn()`:
   ```python
   if _agent_state.turn_active:
       return history
   _agent_state.turn_active = True
   ```
3. Set `_agent_state.turn_active = False` in all return paths + a `finally` clause
4. Guard chat send: if `turn_active`, show "Already processing" and return `CANCELLED`

---

### 🔴 Fix 2 — Fix `_undo_code()` Silent Fallback

**Problem**: The generated undo code tries VIEW_3D first, then any area via `for...else`. If NO windows or areas exist (headless mode, startup before UI init), the entire `if action == "undo"` branch silently does nothing. `result` is still `{"status": "ok"}`.

**Impact**: The smart-undo system thinks it undid, but the scene stays dirty. Retry then creates duplicates.

**Files**:
- `addon/bfa_coworker/agent_controller.py` — `_undo_code()` function (~line 1890)

**What to do**:
1. Add an explicit error path when no area is found:
   ```python
   "    else:\n"
   "        result = {'status': 'error', 'message': 'No window/area available for undo'}\n"
   ```
2. Verify the result dict in the calling code — check for this `"error"` status and log it loudly, not silently.

---

### 🔴 Fix 3 — `_error_is_code_bug()` Too Generous for `ValueError`

**Problem**: `ValueError` can fire **after** creating objects (e.g., `bpy.ops.mesh.primitive_cube_add()` then bad math). The current code skips undo, leaving the created object.

**Impact**: Objects persist despite "code bug" classification — the exact "two balls" scenario.

**Files**:
- `addon/bfa_coworker/agent_controller.py` — `_error_is_code_bug()` (~line 1639)

**What to do** (choose ONE):

- **Option A (safest)**: Remove `ValueError:` from the pattern list. Other patterns (KeyError, AttributeError, etc.) are almost always thrown before side effects.
- **Option B (smarter)**: Before skipping undo, take a quick `len(bpy.data.objects)` snapshot vs the pre-execution count. If objects were created, don't treat as code bug.

---

### 🟡 Fix 4 — Auto-Continue Tool Call Deduplication

**Problem**: When `finish_reason=length`, partial content is appended, "Continue." sent, and `tool_calls` are merged via `msg.tool_calls = existing + cont_tool_calls`. If the continuation re-emits the same tool call (same or different ID), it executes twice.

**Files**:
- `addon/bfa_coworker/agent_controller.py` — auto-continue block (~line 2180-2191)

**What to do**:
```python
seen_ids = {tc.get("id") for tc in existing}
for tc in cont_tool_calls:
    if tc.get("id") not in seen_ids:
        msg["tool_calls"].append(tc)
        seen_ids.add(tc.get("id"))
```

---

### 🟡 Fix 5 — Test Suite Progress/Thread Sync

**Problem**: `_BFACW_OT_test_step.execute()` advances `_test_suite_progress` immediately after spawning the thread. The step advances even if the thread fails mid-run. Clicking two step buttons in rapid succession launches two concurrent turns.

**Files**:
- `addon/bfa_coworker/operators_agent.py` — `_BFACW_OT_test_step.execute()` (~line 348)

**What to do**:
1. Add simple busy-guard: track `_test_suite_running: dict[str, bool]` — don't spawn new thread if one is active for that suite
2. Advance progress when thread completes, not when spawned:
   - Wrap `_do_step` in a `finally:` that schedules a `bpy.app.timers.register(lambda: advance_and_redraw(...))` on the main thread

---

## Part 3: Medium-Priority Stabilization

### 🟠 Fix 6 — History Save Lock

**Files**: `addon/bfa_coworker/ui_chat.py` — `_save_chat_history()` (~line 263)

Acquire a `threading.Lock` around history serialization to prevent concurrent threads from writing partial dumps.

### 🟠 Fix 7 — Test File Tail Cleanup

**Files**: `tests/test_blender_mcp_with_blender.py` — lines 1101-1169

Remove the duplicated `TestForegroundServer`/`TestInteractiveServer` class definitions and fix the malformed line: `exit(1)    unittest.main()` → two statements on one line.

### 🟠 Fix 8 — Collection Mutation Heuristic Hardening

**Files**: `addon/bfa_coworker/mcp_to_blender_server.py` — `_code_touches_collections()` (~line 264)

The `jump_to_view3d_object_by_name_toolcode` template mutates `layer_col.exclude` / `hide_viewport` via generated code. String-matching heuristic should catch these, but the matching is against the rendered code string after template expansion. Verify this by testing in Blender — add debug print in `_execute_code` when `_code_touches_collections()` returns True, check that it fires when jump_to_view3d tools are invoked.

---

## Part 4: Tool Expansion Strategy (Future "Many More Tools")

This is the framework for adding tools without sinking the context window.

### Architecture: Layered Tool Loading

```
Layer 0 — Surface (always loaded): 4 tools
    execute_blender_code, get_blendfile_summary_datablocks, get_object_detail_summary, get_objects_summary

Layer 1 — Domain (detected from prompt): 7 domains today, expandable
    animation, material, modeling, lighting, rendering, vse, geometry_nodes

Layer 2 — On-demand (load_tools): call when needed
    The LLM asks; up to 2 domain loads per turn

Layer 3 — Passive (skill injection): no MCP tool
    blender_53.md, user custom skills — injected at system prompt, no round-trips
```

### Roadmap for New Tools

| Phase | What | Effort |
|-------|------|--------|
| Phase 1 | **Scene-aware domain pre-detection** — analyze `bpy.data` before turn start, pre-load domains | Low |
| Phase 2 | **Domain-aware skill injection** — when domain pre-detected, auto-inject matching `blender_5X.md` skills | Low |
| Phase 3 | **Result trimming middleware** — strip boilerplate from tool results before appending to history | Medium |
| Phase 4 | **Composite tool wrappers** — tools that call other tools (e.g., `setup_pbr_material` calls `download_polyhaven_asset` + node wiring) | Medium |
| Phase 5 | **User skill loader** — allow custom `.md` skills in a user directory, loaded at startup like `blender_53.md` | Medium |

### Specific Tool Candidates (Domain-Aligned)

| Domain | Tool Idea | Complexity |
|--------|-----------|------------|
| material | `batch_set_material_viewport_display` | Low |
| animation | `batch_keyframe_insert` (set many keyframes in one call) | Low |
| modeling | `mesh_cleanup` (merge by distance + dissolve degenerate) | Low |
| rendering | `set_render_quality_preset` (fast/final/draft) | Low |
| lighting | `three_point_lighting_rig` (key/fill/rim at angles) | Medium |
| geometry_nodes | `apply_gn_modifier_preset` (common setups) | Medium |
| vse | `add_crossfade_between_strips` | Medium |

### Context Budget Rules (Hard Limits)

For local models, enforce these in `_build_tool_set()`:

- **Max 8 tools** in surface + pre-detected domain (already there)
- **Max 12 tools** after `load_tools` domain additions
- **Max 2 `load_tools` calls** per turn (add counter to prevent spiraling)
- **Result trim at 500 chars** (already there) — make it smarter: prefer JSON fields over raw text

---

## Part 5: Performance Notes

| Hot Path | Current Cost | Optimization |
|----------|---------------|--------------|
| Entity snapshot (per successful code exec) | 12 datablock name extractions × 2 per turn | Only snapshot mutating tools; skip for read-only results |
| `_extract_code_operations` regex scans | 8 patterns × ~2 calls per overlap check | Compile patterns to single combined regex; cache result per turn |
| `_safe_depsgraph_sync` object tagging | O(n) objects, cheap per-object | Fine as-is; skip entire call when code is read-only (`get_` prefix) |
| Screenshot downscale probe writing | Binary search over divisors, writes PNG each probe | Add early-exit if image < 1MB, skip probe loop entirely |

---

## Checklist Sequence

Execute in this order. Each fix is independently verifiable.

- [x] Fix 1: `_turn_active` re-entrancy guard
- [x] Fix 2: `_undo_code()` error return on no-area
- [x] Fix 3: `_error_is_code_bug()` side-effect check (remove ValueError)
- [x] Fix 4: Auto-continue tool call deduplication
- [x] Fix 5: Test suite busy-guard + progress sync
- [x] Fix 6: History save lock
- [x] Fix 7: Test file tail cleanup (remove duplicates)
- [x] Fix 8: Verify collection heuristic catches jump_to_view3d mutations
- [ ] Perf 1: Skip snapshot on read-only tool results
- [ ] Phase 1: Scene-aware domain pre-detection
- [ ] Phase 2: Domain-aware skill auto-injection
- [ ] Phase 3: Result trimming middleware
- [ ] Phase 4: Composite tool wrappers
- [ ] Phase 5: User skill loader

---

## Verification Strategy

After each critical fix (1-5), run the stepped benchmark suites:

1. Scene Build suite — steps 1-2 (ground + props)
2. Animation suite — steps 1-2 (bounce ball + squash stretch)
3. Modifiers suite — step 1 (subdiv cube)
4. Error Handling suite — all 3 steps

Watch console for "Coworker" log messages. Verify no duplicate objects in Outliner after each step.

---

*End of Tier 3 Audit Plan*