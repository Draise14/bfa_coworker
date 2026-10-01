# BFA Coworker - Tier 3i: Reasoning Latency & Token Efficiency

**Date**: 2026-10-01
**Status**: In progress - Phase 3i.0 (measurement ledger) IMPLEMENTED; 3i.1-3i.7 proposed (data-gated)
**Branch**: `fix/scene-safety-local-hardening`
**Depends on**: timings instrumentation (llama-server `timings` capture + Speed readout)
**Related**: Tier 4f.1 (inference flags), 4f.2 (on-demand skills), 4f.4 (subagents),
Tier 4g (domain tooling), Tier 5a (speculative decoding - upstream-blocked)
**Scope**: measurement (done) + ~150-400 LOC of targeted changes + tests

---

## 1. Problem

Local (Qwen) turns take **200-600 s** while llama-server reports **fast generation
tok/s** (the "Speed: N tok/s gen" readout). Wall time is therefore **not
bandwidth-bound**: it is token *volume* plus *per-request overhead*. The user is
now "almost infinite" (stable, surviving long sessions), so the goal shifts from
robustness to **latency**.

## 2. Diagnosis (from source)

| # | Hypothesis | Evidence |
|---|---|---|
| H1 | **Over-generation dominates.** | `local_max_tokens=16384` (`llm_manager.py:639`); `_cap_reply_tokens` allows ~20k on a 32k window; auto-continue retries **2x** on `finish_reason=length` (`agent_controller.py` continue loop); up to **12** iterations (`_LOCAL_MAX_TOOL_ITERATIONS`). One iteration can emit 40-60k tokens. |
| H2 | **A prompt line tells it to over-think.** | `_get_system_prompt_with_rules` prepends a `STYLE:` header: *"Think aloud in full paragraphs... Be thorough but not repetitive."* to **every** mode -- contradicting the compact prompt's *"Be concise and decisive."* |
| H3 | **Reasoning budget large / unbounded.** | `thinking_budget_tokens` default 1024 (`llm_manager.py:640`); Reasoning Effort "Off" = `0` = **no cap** (`preferences.py`). The model reasons on *every* tool-loop iteration. |
| H4 | **Fixed prompt prefill.** | Built-in skills ~7k + tool schema ~4k + session memory + domain skills, re-sent every request. Prefill on CPU is slow and invisible in the gen readout. |
| H5 | **Round-trip / scheduling overhead.** | Many `_call_mcp_tool_sync` calls per iteration (undo+snapshot, tool, lock, cleanup, re-push), each serviced by the main-thread `bpy.app.timers` pump (0.05 s) which CPU inference starves. |

Most likely: **H1 amplified by H2**. Confirmed or rejected by the Phase 3i.0 data.

## 3. Phase 3i.0 - Measurement ledger (IMPLEMENTED)

A per-turn cost ledger so the cause is **proven, not guessed**:
- `AgentState.record_request_timings(timings)` accumulates `prompt_n/prompt_ms/
  predicted_n/predicted_ms` and the request count across every request in a turn.
- `AgentState.bump_turn_cost(key)` counts `tools`, `nudges`, `continues`,
  `malformed`; `reset_turn_cost()` starts fresh and stamps the wall-clock start.
- `_log_turn_cost()` prints a one-line summary at turn end (called from the
  `run_conversation_turn` `finally`, so FINISH / error / Stop all report).
- The Status & Diagnostics panel shows a **"Last turn"** line.
- Example line:
  `turn cost: 7 req | prefill 41k tok / 22s (1.9k tok/s) | gen 58k tok / 300s (190 tok/s) | tools 19 | nudges 2 | cont 1 | bad 0 | wall 610s`

**Acceptance**: >=90% of a real benchmark step's wall time is attributable to
prefill / generation / tool overhead.

**Tests**: `tests/test_turn_cost.py` (ledger math, reset, logging, empty case).

## 4. Candidate changes (implement only what 3i.0 supports)

- **3i.1 Reply allowance** - cap the LOCAL reply to a small default (e.g. 2-4k)
  with a "continue if needed" auto-continue; keep large for remote.
- **3i.2 Prompt STYLE** - make the "think aloud / be thorough" line remote-only,
  or a preference; local gets "be concise / decisive".
- **3i.3 Reasoning budget** - context-aware default; document Off=unbounded;
  optionally scale down on later iterations of a turn.
- **3i.4 Iteration economy** - reduce iterations when the turn is healthy, or add
  a "no-progress" cut-out (same tool + no scene change, N times).
- **3i.5 Prefill reduction** - adopt Tier 4f.2 on-demand skill loading; trim
  stored/sent tool results; keep the tool list stable (cache-prefix friendly).
- **3i.6 Tool-over-code** - add high-frequency helper tools so common operations
  are a tool call, not generated code (each ~10-50 output tokens vs a whole
  script). Candidates:
  - `scene_tidy` (delete/merge/hide by location | size | name pattern | type)
  - `batch_transform` / `scatter_by_rule`
  - `rename_by_rule`, `sort_outliner_by_rule`
  - `apply_and_cleanup_modifiers`
  Pre-authored toolcode (Tier 4g pattern) with `NamedTuple` I/O.
- **3i.7 Overhead** - audit redundant round-trips per iteration (snapshot/push/
  lock/cleanup); merge where safe; consider a faster pump interval while a turn
  is active.

## 5. Non-goals

- No model changes; no distillation. Speculative decoding is Tier 5a (blocked).
- No correctness regressions: auto-continue and the execution guarantee stay.

## 6. Verification

- `tests/test_turn_cost.py` (new), `tests/test_context_budget.py`,
  `tests/test_streaming_llm.py`; full suite green; `check_ascii` clean.
- Manual: one benchmark suite end-to-end; report per-turn cost before/after each
  change (the 3i.0 line makes this a number, not a feeling).

## 7. Risks

- Capping the reply could truncate long code -> mitigate via "small steps"
  prompt + auto-continue.
- Fewer iterations could under-execute -> keep the action nudge + summary path.
- On-demand skills could drop crash-preventing version drift -> keep the
  version-drift files always-loaded.
- Many new tools grow the schema -> measure schema tokens vs generation tokens
  saved before committing.

## 8. Relationship to other tiers

- **4f.1** (inference flags / metrics) and **5a** (speculative decoding) change
  *raw speed*; 3i changes *how many tokens* are produced and pre-filled.
- **4f.2** (on-demand skills) is the concrete implementation of 3i.5.
- **4g** (domain tools) is the concrete implementation of 3i.6.

## 9. Further considerations

1. **Shrink the reasoning budget on later iterations?** The first iteration of a
   turn needs the full budget (understand + plan); iterations 2-12 are usually
   mechanical follow-ups that re-think the whole plan. Candidate: a high budget
   for iteration 1, a lower one (e.g. 256-512) afterwards. Tradeoff: less room to
   recover from a genuinely hard later sub-problem. Cheap/reversible; data-gated.
2. **Per-turn wall/token budget?** RESOLVED: **let the turn finish** -- no
   auto-stop. Do not prod the agent; it must complete its job. (Stop remains a
   manual escape hatch; the end-of-turn execution nudge helps it finish.)
3. **Tools without a ceiling -- see Tier 3j below.**

## 10. Tier 3j (proposed): Tool Discovery -- no ceiling, cheap

The tool schema is currently fully resident, so every added tool permanently
costs context -- that is the ceiling. Replace "catalogue dump" with a
**two-tier working set**:

| Tier | Resident | Cost |
|---|---|---|
| Index | every tool `name` + one-line purpose (grouped by domain) | ~15-30 tok/tool (~1.5k for all) |
| Active | full JSON schema for tools in play | ~200-800 tok/active tool |

- **Always available**: a compact index, and/or a `find_tools(query)` search tool
  returning ranked `name + one-line` matches. The model knows what exists without
  paying for every schema.
- **On demand**: `load_tools(domain)` / `load_tools(names=[...])` promotes chosen
  tools to full schema for the rest of the session (session-sticky -> prompt-cache
  stable; already the `load_tools`/domain behaviour, generalized).
- **Smarts not brute force**: existing keyword + scene domain detection pre-loads
  the likely tools; the index lets the model discover the rest mid-task.

**Why it removes the ceiling**: tool #61 costs one index line (~20 tok), not a
full schema (~500 tok). Context scales with tools *used*, not tools *available*.

**Measurement**: with the 3i.0 ledger, `prompt_n` already includes the tool
schema -- compare `prompt_n` with/without a tool vs the `predicted_n` it saves on
repetitive tasks. With discovery the schema cost is ~0 until use, so
tool-over-code (3i.6) becomes unambiguously a net win.
