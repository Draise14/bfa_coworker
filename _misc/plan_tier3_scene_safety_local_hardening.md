# BFA Coworker — Tier 3: Scene Safety & Local-Mode Hardening (Quality Pass)

**Date**: 2026-10-01
**Branch**: `fix/scene-safety-local-hardening`
**Issue**: Drowse14/bfa_coworker#74 — Harden local-mode context handling (session memory & auto-checkpoints)
**Status**: Planned — verification-first audit of the co-work / session / context hardening work
**Relates to**: `_misc/adversarial_review_stability.md` (this pass closes its deferred LOW items), Tier 3
session-memory plan (`Plans History/plan_tier3_session_memory_checkpoints.md`)

---

## 1. Summary

The issue-#74 work added a bounded verbatim window, an always-injected memory block, an on-disk archive,
automatic checkpoints, a soft co-work scene lock, scene preflight checks, tool-output slimming, real-context
budgeting, and llama-server launch/error hardening. It succeeds at its core goal — a long local session no
longer dies on a raw `400`.

This pass is a second pair of eyes. It reproduces the remaining defects in the *new* subsystems, fixes them,
and adds a regression test per fix. The north star is unchanged: **a user can talk forever and run every
benchmark in one session without hitting an error**, with no leaked scene state and no silent corruption.

Each finding below was reproduced or read directly from source; file:line references are the audit anchors.

---

## 2. The failure being fixed

From issue #74 and the adversarial review:

```
E srv send_error: task id = 23921, error: request (34678 tokens) exceeds the available context size (16384 tokens)
```

Root causes: the budget ignored the real server ctx, the tool schema, injected skills and the screenshot;
history was stored in full and never pruned; three divergent ctx defaults (one silently auto-bumped).

The follow-up hardening (this branch) added real-`/props` budgeting, ~60% compaction, an on-disk archive,
overflow-retry, the co-work lock and preflight checks. The audit finds the *edges* of those additions.

---

## 3. Verified findings

Severity: **HIGH** = data loss / wrong result / crash-class; **MED** = incorrect or fragile behaviour;
**LOW** = cosmetic, docs, latent.

### A. `session_memory.py` — engine

| # | Sev | Finding | Anchor |
|---|---|---|---|
| A1 | HIGH | `restore()` validates `index`, then `snapshot()` trims the list to `MAX_CHECKPOINTS` (10), then reads `checkpoints[index]` — at capacity this restores a *different* checkpoint; `index == 9` restores the just-created `pre-restore` snapshot (a silent no-op). | `session_memory.py:303-337` |
| A2 | HIGH | Auto-checkpoints are snapshotted *after* `history[:] = kept`, so restoring a `compaction` checkpoint yields the already-summarized window — it cannot rewind before the summary. | `agent_controller.py:4445-4446`, `:4489-4490` |
| A3 | MED | `compact_history` archives the system prompt (index 0) when `boundary == len(history)`; the system message lands in `archive.jsonl`. | `session_memory.py:238-241` |
| A4 | LOW | `restore()` restores `memory_block` but not `memory_updated_turn`, so the freshness stamp is wrong after a restore. | `session_memory.py:335` |
| A5 | LOW | `archive.jsonl` grows unbounded and `load_archive` reads the whole file; `append_archive` documents "bytes written" but returns the file size. | `session_memory.py:348-367` |
| A6 | LOW | `estimate_history_tokens` is dead in production and divergent (counts a base64 image as text), unlike `agent_controller._message_text_length` (fixed `_SCREENSHOT_TOKENS`). | `session_memory.py:173-197` |

### B. `ui_chat.py` — Session panel & operators

| # | Sev | Finding | Anchor |
|---|---|---|---|
| B1 | HIGH | **Compact Now wipes the conversation.** It guards only `len(history) < 4`, then `compact_history` uses the default `keep_recent=20`, so any `n <= 21` retires every non-`ui_only` message — including the system prompt — keeping only `[system]`. The post-hoc snapshot captures the destroyed state, so nothing can undo it. | `ui_chat.py:2649-2676`; `session_memory.py:201-220` |
| B2 | HIGH/MED | **Branch overwrites the live session**, contradicting its own tooltip ("without touching the current session") and the plan; it also does not snapshot the current state first. | `ui_chat.py:2605-2626` |
| B3 | MED | **New Thread does not reset session memory** — `memory_block`, `checkpoints`, archive path and the turn counter survive, then inject into the new thread. | `ui_chat.py:960-970` |
| B4 | MED | **View / Edit Memory is unusable** — the operator reads/writes `props.session_memory_edit`, but no `layout.textbox(...)` binds that property anywhere, so the user can never see or edit it. | `ui_chat.py:2629-2647`, prop at `:720`, panel `:2516-2575` |
| B5 | MED | No lock on the `session_memory.store` singleton; turn-thread mutations race UI operators (unlike the history save lock). | `session_memory.store`; `ui_chat.py:2589-2674` |

### C. `llm_transport.py` / budgeting (`agent_controller.py`)

| # | Sev | Finding | Anchor |
|---|---|---|---|
| C1 | HIGH | A streamed `content` delta containing ` thinking` is appended to `content` **twice**; the tagged delta is also pushed to reasoning (and the `.replace()` usually does not match). | `llm_transport.py:855`, `:863` |
| C2 | MED | `max_tokens` decays monotonically across tool-loop iterations: the loop reassigns the already-capped value as `requested`, so it can never grow back. | `agent_controller.py:5010`, retry `:5054` |
| C3 | MED | 503 backoff is unbounded relative to the documented ~120 s (`max_retries + max_503_retries = 65`, worst case ~600 s). | `llm_transport.py:404`, `:746` |
| C4 | MED | Non-retryable 4xx (401/403/404/422) hit the generic retry branch and are retried ~5x, masking the real error. | `llm_transport.py:753` |
| C5 | MED | `error` / `error_full` are not cleared by the transport on a later success (only `error_kind` is), so a stale error can be read between calls. | `llm_transport.py:359`, `:955` |
| C6 | MED | The streaming path returns `None` for any pre-first-token `HTTPError` without setting `error_kind` / reason. | `llm_transport.py:1057-1076` |
| C7 | LOW-MED | `--log-file` is appended *after* the `--help` flag filter, so a build lacking it would exit 1 in Debug mode. | `llm_manager.py:3085` then `:3130` |
| C8 | LOW | If the tool schema alone exceeds the budget, `_prompt_preflight` still sends it in full while flooring the reply, so the invariant can break at a tiny/oversized-schema window. | `agent_controller.py:577` |
| C9 | LOW | Auto-continue / forced-summary reuse the capped `max_tokens` without re-capping for the (larger) continuation prompt. | `agent_controller.py:5180-5290`, `:5575-5593` |
| C10 | LOW | Docstring/constant drift (`_DEFAULT_MAX_TOKENS` vs the "16384 default" doc; retry sleep text). | `llm_transport.py:70` vs `:335` |
| C11 | LOW | Unbound `_agent_state` writes in the transport (safe only because `bind()` runs first). | `llm_transport.py:606`, `:1067` |
| C12 | LOW | `_detect_gpu_backend` subprocess calls lack `CREATE_NO_WINDOW` (minor console flash). | `llm_manager.py:1080+` |
| C13 | LOW | The budget invariant holds only in estimator space (`_CHARS_PER_TOKEN = 3.5`); denser tokenization relies on 400-recovery. Document. | — |

### D. Scene guards / skills / tool output

| # | Sev | Finding | Anchor |
|---|---|---|---|
| D1 | HIGH | Skills cache is **not keyed on `bpy_version`**; `list_loaded_skills()` (Preferences draw) builds it without a version, so every later versioned call hits the cache and the session loses all `blender_*.md` version-drift skills. | `skills/__init__.py:34-92`; `preferences.py:2116` |
| D2 | MED | Scene detection adds domain key `"asset_browser"`, but `_TOOL_DOMAINS` and `_DOMAIN_SKILL_MAP` use `"assets"`, so scene-detected asset work loads no asset tools or skill. | `agent_controller.py:~1112` vs `:921`; `skills/__init__.py:155` |
| D3 | MED | `_detect_domain` returns the first keyword match only; multi-domain prompts rely on the scene heuristic. | `agent_controller.py:1055-1065` |
| D4 | MED | Domain skills are injected only in local mode; remote/harness get none. | `agent_controller.py:4790-4805` |
| D5 | MED | "Touched" (not created) objects are never locked, contradicting the module docstring — the exact re-targeting hazard the lock targets. | `agent_controller.py:3847-3859`, `:5444`; `co_work_guard.py:15` |
| D6 | MED-HIGH | If the MCP unlock call fails, the `finally` still `clear()`s the priors while `hide_select=True` remains; `cleanup()` clears without unlocking — a saved `.blend` can keep objects permanently un-selectable. | `agent_controller.py:4271-4283`, `:5622` |
| D7 | MED | Renamed datablocks are not restored on unlock (restore is by stored name). | `co_work_guard.py:150-168` |
| D8 | MED | Stop re-entrancy: the registry dicts and `_active_lock_mcp_port` are mutated from worker threads with no guard; a new turn can race the old one's lock/release. | `agent_controller.py:4316-4322` |
| D9 | LOW-MED | If the initial entity snapshot fails, locking is silently disabled for the whole turn with no diagnostic. | `agent_controller.py:5442` |
| D10 | LOW-MED | If `record_prior` fails to parse, the unlock writes `hide_select=False`, un-hiding an object the user had deliberately hidden. | `co_work_guard.py:63-70`; `agent_controller.py:4255` |
| D11 | LOW | `_active_lock_mcp_port` is never reset after release. | `agent_controller.py:4242` |
| D12 | MED | Unguarded-index preflight is suppressed by *any* `if` / `len(` / `.get(` anywhere in the block, so `selected_objects[0]` slips through in almost any realistic script. | `mcp_to_blender_server.py:782` |
| D13 | LOW-MED | Active-object preflight suppression is similarly weak (any `bpy.data.objects.get(` defeats it). | `mcp_to_blender_server.py:741` |
| D14 | LOW | Preflight anchors false-positive on user variables (`selected_objects`) and fire inside comments/strings. | `mcp_to_blender_server.py:737`, `:761-768` |
| D15 | MED | Large tool results are stored untrimmed (only the send copy is capped); non-poll/index tracebacks stack up in the retained window. | `agent_controller.py:5483` vs `:652` |
| D16 | LOW | `get_domain_skills` re-reads files from disk on every request. | `skills/__init__.py:167` |
| D17 | **HIGH** | **Five MCP tools are unreachable even in local mode**: `get_blendfile_summary_missing_files`, `get_blendfile_summary_of_linked_libraries`, `get_blendfile_summary_path_info`, `get_blendfile_summary_usage_guess`, `get_polyhaven_status` appear in neither `_SURFACE_TOOLS` nor any `_TOOL_DOMAINS`, and `load_tools` only offers domain keys — so they can never be selected. Filtering remote would hide them there too. | `agent_controller.py:899-921`, `:1025-1055` |
| D18 | MED | The `load_tools` interception is gated `llm_port_local is not None`, so filtering remote requires including `_LOAD_TOOLS_SCHEMA` and lifting this gate or a `load_tools` call is forwarded to MCP and fails. | `agent_controller.py:5305` |

### E. ASCII sweep (closes deferred `G1`)

`_misc/check_ascii.py` is pre-existing red: emoji (🛠️), em/en-dashes and box-drawing characters remain in
tracked `.py`/`.toml` under `mcp/`, `addon/`, `chat_client/`. On a cp1252 stdout this is a latent
`UnicodeEncodeError` crash vector. This pass makes the checker exit 0.

---

## 4. Remote-mode tool-filtering deep dive

Extending domain intelligence to remote mode (decision D4) has three non-obvious hazards.

1. **Orphan tools (D17) must be fixed first.** Remote filtering hides any tool that is not in `_SURFACE_TOOLS`
   or a `_TOOL_DOMAINS` entry. The five D17 tools are already unreachable locally; fix them before filtering remote.
2. **The `load_tools` gate (D18).** Remote currently sends the full unfiltered schema and never includes
   `load_tools`; the interceptor rejects `llm_port_local is None`. Filtering remote *requires* including
   `_LOAD_TOOLS_SCHEMA` and lifting the gate, or the model can call a tool the loop forwards to MCP and fails.
3. **Prompt-cache stability.** A tool schema that varies per turn invalidates provider prompt-cache prefixes
   (Anthropic/OpenAI/OpenRouter) and can cost *more* than a stable full schema. Mitigation: make remote domains
   **session-sticky** (the union of detected domains only grows) and **sort the final tool list by name**, so
   the schema prefix is stable and rarely changes.

Cost/benefit: filtering trades "full schema every request" for "small schema + a rare `load_tools` call".
Scene detection is broad (it pulls most domains for any real scene), so misses are uncommon; with D2/D3 fixed
the quality is high and the net token cost is lower.

**Harness mode is out of scope.** `_list_tools_sync` returns `[]` for `EXTERNAL_HARNESS` (`agent_controller.py:3011`)
and the external harness owns the prompt, so there is nothing to filter. Documented as N/A.

**Remote skills allowance.** `prompt_budget == 0` on remote, so the local `spare - reserve` formula has no
input. Use `min(_SKILLS_REMOTE_MAX_TOKENS, remote_ctx * small_ratio)` when a remote ctx is configured, else a
flat whole-files-only cap. Version-drift skills are *more* valuable on remote (remote models predate the
Blender 5.2/5.3 API changes).

---

## 5. Confirmed decisions

- **Branch operator** — remove entirely for now (its behaviour contradicts its tooltip).
- **Compact Now** — dedicated smaller manual `keep_recent`, require a non-trivial retire set, and snapshot
  *before* retiring.
- **Checkpoint snapshot timing** — record the pre-compaction state so auto-checkpoints are a true rewind point.
- **Remote/harness domain intelligence** — extend to remote (smart, token-efficient); harness is N/A.
- **"Touched" locking (D5)** — implement if the step's touched/bookmark data is already available; else correct
  the docstring.
- **Archive policy (A5)** — cap + rotate newest-N internally (not user-facing).
- **ASCII sweep (E)** — in scope; make `check_ascii.py` exit 0.
- **Cross-session restart test (K2)** — deferred; keep the JSON round-trip unit test only.

---

## 6. Implementation phases

Dependencies: `1 -> 2`; `3` and `4` parallel; `4.5` after `4`; `5` independent; `6` after all.

### Phase 1 — session-memory engine (A)
1. Fix `restore()` index shift: capture the target record *before* adding the `pre-restore` snapshot (or key by
   identity), so the requested checkpoint is always restored.
2. `compact_history`: never retire/archive the system message; guarantee `kept` begins with it.
3. Move checkpoint snapshots to *before* `history[:] = kept` at all three sites (compaction, overflow, manual).
4. `restore()` also restores `memory_updated_turn`.
5. Archive: correct `append_archive`'s return/doc, and add a size/line cap with newest-N rotation.
6. Remove the dead divergent `estimate_history_tokens` (+ its test).

### Phase 2 — UI operators (B) [after 1]
1. Remove Branch: class, button, registration, docs.
2. Compact Now: small manual `keep_recent`, require a real retire set, snapshot pre-state.
3. New Thread: reset `memory_block`, `checkpoints`, `memory_updated_turn`, the turn counter and archive path.
4. Add a bound multiline textbox for `session_memory_edit` in the Session panel.
5. Add a shared lock around store mutations (turn thread + UI operators) and the sidecar write.

### Phase 3 — transport & budgeting (C) [parallel]
1. C1 think double-append; C2 re-cap from the original `max_tokens`; C3 bound 503 backoff; C4 fail fast on
   non-retryable 4xx; C5 clear stale errors; C6 streaming 4xx reason; C7 filter `--log-file`; C8/C9 re-cap
   auto-continue & forced-summary; C10-C12 docs/console.

### Phase 4 — scene guards / skills / tool output (D) [parallel]
1. D1 version-key the skills cache; D2/D3 accumulate + normalize domains; D17 add orphan tools to
   `_SURFACE_TOOLS`; D5 touched-lock (or docstring); D6 non-destructive unlock + `cleanup()` unlock;
   D7-D11 guard robustness; D12-D14 tighten preflight suppression/anchors; D15 cap *stored* results; D16 cache reads.

### Phase 4.5 — remote/harness domain intelligence (D4) [after 4]
1. Fix D18: include `_LOAD_TOOLS_SCHEMA` and lift the `load_tools` gate for remote.
2. Move detection out of the local-only gate; apply `_build_tool_set` filtering on remote.
3. Make domains session-sticky; sort the final tool list by name.
4. Remote skills allowance as in section 4; inject into the sent copy with the estimator `_fits` guard.
5. Optional "Optimize remote tool set" preference (default on) with a documented prompt-cache trade-off.

### Phase 5 — ASCII sweep (E) [independent]
Run `python _misc/check_ascii.py`, replace every flagged character across `.py`/`.toml`, and update any test
that asserted non-ASCII prompt/log text. The checker must exit 0.

**Done.** `_misc/check_ascii.py` now exits 0. `_misc/ascii_sweep.py` (idempotent, with `--check`) performs the
transliteration and skips the same dirs as the checker (vendored deps, upstream API examples). The console
emoji prefixes became **ANSI-colored ASCII tags** (cyan/yellow/red, `NO_COLOR` respected) — see `log.py`'s
`_colorize` — and `ui_chat._LATEX_SYMBOLS` was protected by escaping its glyph values to `\uXXXX`.

### Phase 6 — tests & docs [after all]
New regression tests (section 7), expanded benchmark error injection (500 template/toolcall fault, 503, OOM,
stale-error clear), CHANGELOG, wiki/docs, and adversarial-doc status updates.

---

## 7. Files touched & new regression tests

**Files**
- `addon/bfa_coworker/session_memory.py` — engine (A).
- `addon/bfa_coworker/agent_controller.py` — compaction wiring, budgeting, scene lock, tool storage, domains.
- `addon/bfa_coworker/ui_chat.py` — Session panel & operators (B).
- `addon/bfa_coworker/llm_transport.py` — streaming/error handling (C).
- `addon/bfa_coworker/llm_manager.py` — launch flags, ctx (C7, C12).
- `addon/bfa_coworker/skills/__init__.py` — skill cache/domain loading (D1, D2, D16).
- `addon/bfa_coworker/co_work_guard.py` — lock registry (D5-D7, D10).
- `addon/bfa_coworker/mcp_to_blender_server.py` — preflight checks (D12-D14).
- `addon/bfa_coworker/preferences.py` — skill list draw (D1).

**New/extended tests**
- A1 restore at capacity returns the selected checkpoint; A2 auto-checkpoint history == pre-compaction; A3
  system prompt never in the archive; A5 archive rotation integrity.
- B1 Compact Now on `n <= 21` keeps recent context and snapshots pre-state; B3 New Thread clears memory;
  B4 memory editor round-trips through the bound property.
- C1 inline ` thinking` is not duplicated; C2 `max_tokens` never shrinks when space frees; C4 non-retryable 4xx
  = one request; C5 success clears prior error.
- D1 versioned skills not dropped by `list_loaded_skills()`; D2 scene `assets` domain loads asset tools/skills;
  D6 unlock failure retains priors; D12 guarded-index preflight not defeated by an unrelated `if`; D15 large
  traceback bounded in stored history; D17 allowed-set superset of every real MCP tool name; D18 remote
  `load_tools` intercepted; D4 remote schema stable/sorted + skills within allowance.

---

## 8. Verification

1. `$env:PYTHONIOENCODING="utf-8"; python -m unittest tests.test_session_memory tests.test_context_budget
   tests.test_llm_transport_errors tests.test_streaming_llm tests.test_llm_manager tests.test_turn_loop_integration
   tests.test_co_work_guard tests.test_preflight tests.test_prompt_rules tests.test_addon_imports
   tests.test_orchestration_helpers`
2. `python _misc/check_ascii.py` and `python _misc/check_namespace.py` -> exit 0.
3. `make benchmark` (`BENCH_RUNS` iterations) -> no failures.
4. Rebuild + install + enable; run one long session incl. a `poll() failed` traceback and a forced overflow.
   Confirm: no 400, visible "Compacting", restore recovers pre-compaction turns, Start/Stop leaves no object
   un-selectable, fresh low-RAM start has no OOM.
5. Walk issue #74 acceptance criteria one by one.

---

## 9. Risks & out of scope

**Risks**
- The ASCII sweep touches many files/tests — do it last, isolated, so it cannot mask logic regressions.
- Pre-compaction snapshots change checkpoint contents — update tests that assert post-compaction records.
- Removing Branch removes a UI control — update docs/wiki/changelog.
- Locking "touched" objects needs reliable touched-entity data; fall back to correcting the docstring if not.
- Remote filtering + prompt caching: sticky, sorted tool sets mitigate per-turn cache invalidation.

**Out of scope**
- A live-Blender cross-session (restart) restore test (K2) — deferred; the JSON round-trip stays unit-tested.
- Any mid-session llama-server restart (D4 of the original plan stands: startup-only sizing).
