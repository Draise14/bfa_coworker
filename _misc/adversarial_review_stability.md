# Adversarial Review — Launch / Run / Memory / Session Stability

**Date**: 2026-09-30
**Branch**: `fix/scene-safety-local-hardening`
**Scope**: launching, running, memory, sessions, logging, caching, compacting,
smooth chat turns regardless of context size, and the checkpoint system.

This is a pass-for-findings review: each item was reproduced or reasoned from
the source, then either fixed on this branch or explicitly deferred with a
rationale. "Fixed" means the change is committed and covered by tests.

---

## 1. Launching

| # | Sev | Finding | Status |
|---|---|---|---|
| L1 | HIGH | On Windows the launcher used `CreateProcessW` + `CREATE_NEW_CONSOLE`, so llama-server's stdout/stderr went to a console window the addon could not read. A startup crash therefore reported an exit code **with no reason**, and the "log tail" shown was a **stale file from a previous run** — actively misleading. | **Fixed** — launch with `subprocess.Popen` + `CREATE_NO_WINDOW`, stdout/stderr redirected to `llama-server.log` on all platforms. A **visible console is restored in Debug / Diagnostics mode** (`_show_llama_console()`), where the log is still captured via the server's own `--log-file`. |
| L2 | HIGH | The `--log-file` fallback depended on the `--help` probe; if that probe failed, nothing was captured at all (the "No server log was captured" case). | **Fixed** — normal capture no longer depends on any flag (stdio is redirected); `--log-file` is only added in Debug mode (where stdio goes to the console), so the failure tail is available either way. |
| L3 | MED | `start_local_llama` auto-selected a free port via `_find_free_port` but never wrote it back to `_config.local_port`, so health checks and chat requests kept targeting the **busy configured port** while the server ran elsewhere. | **Fixed** — the chosen port is written back under the lock; a busy configured port is logged. |
| L4 | MED | `wait_until_ready` reported "exited during startup" whenever the launched process exited — even when a server was still answering on the port (another instance) or the exit was a fast handle-read. | **Fixed** — a short grace period re-checks `/health`; a serving server is treated as ready. |
| L5 | MED | A server that timed out during load was left running, holding the port — the next start then hit "already running" or a port conflict. | **Fixed** — `_abandon_launched_server` terminates the process (if alive), clears `_llama_process`, and resets `is_running`. |
| L6 | MED | An optional flag the build does not accept makes llama-server exit 1 before logging anything (the earlier "unsupported flag" class). | **Fixed** — optional flags are verified against the binary's own `--help` (cached per exe) and dropped if unknown; **required** flags (`_REQUIRED_LLAMA_FLAGS`) are never dropped, so a help-probe miss cannot launch a server missing its port/model/ctx. |
| L7 | MED | Launching with a missing/empty model file made llama-server exit 1 with no captured reason. | **Fixed** — fails early with "Model file not found: …". |
| L8 | LOW | The `--help` / `--version` probes could flash a console window from the Blender GUI process. | **Fixed** — probes run with `CREATE_NO_WINDOW`. |
| L9 | **CRITICAL** | `--flash-attn` takes an optional value (`[on|off|auto]`) in current llama.cpp, a startup hazard in every emitted form: bare before another flag → the parser reads that flag as its value (`unknown value for --flash-attn: '--batch-size'`); the `=` form → rejected (`invalid argument: --flash-attn=on`); the space form works on the pinned build but breaks on older boolean-only builds. | **Fixed** — the launcher no longer passes `--flash-attn` at all and relies on llama-server's `auto` default (enable when the backend supports it). Only became diagnosable once L1/L2 exposed the real server output. |

**Root cause of the series of "agent crashes on start" reports**: the Windows
launcher's output went to a window the addon could not read, so every real
failure was reported as an opaque exit code — and the log tail shown was stale.
Fixing L1/L2 made the *next* failure self-explanatory, which immediately
surfaced **L9** (`--flash-attn` eating `--batch-size`) as the actual blocker.

---

## 2. Memory, sessions, compaction

| # | Sev | Finding | Status |
|---|---|---|---|
| S1 | MED | The prompt budget under-counted (screenshot appended *after* the preflight; `prompt_budget == 0` silently disabled trimming; the auto-continue and forced-summary requests bypassed the budget). | **Fixed** (earlier on this branch) — screenshots are costed before the preflight, a conservative fallback ctx keeps enforcement on, and every POST path is trimmed + preflighted. |
| S2 | MED | A live context-overflow `400` was surfaced as a bare `HTTP Error 400: Bad Request`, with no recovery. | **Fixed** (earlier) — the transport caches the error body once, classifies the overflow, and the turn loop **auto-compacts and retries once** before showing a friendly message. |
| S3 | LOW | `_maybe_compact_session` increments the session turn counter **per tool-loop iteration**, not per user turn, so the memory block's "Last updated: turn N" stamp can over-count. | **Deferred** — cosmetic; the stamp is informational. Would touch compaction wiring, so left for a dedicated pass. |
| S4 | **HIGH** | The prompt budget reserved the *configured* reply size (`local_max_tokens`, default == the whole context) — clamped to half the window — for the answer. On an effective 16K window that left ~a third for the system prompt, skills and tool schema, so the **first message was refused** with "This conversation no longer fits…" even though nothing had been said. | **Fixed** — the budget reserves only a small reply floor and gives the prompt the rest; the reply is capped to what genuinely remains (`_cap_reply_tokens`), never below a floor. `prompt + reply` now always fits. |
| S5 | MED | `_maybe_compact_session` double-counted the tool schema (`estimated` added it, `safe` = budget − it), firing the trigger far too early, and could fire a **no-op compaction** (and show a "Compacting…" status) on the first turn when there was nothing old enough to retire. | **Fixed** — compare (messages + tools) against the whole budget; require that there is actually something to retire (`find_retire_boundary`). |
| S6 | **HIGH** | Domain-skill reference files were appended to the **stored** system prompt. Scene detection selects a domain for almost any scene (mesh → modelling, material → materials, world → lighting), so several large files (~up to 6.5k tokens total) accumulated in the system prompt across a session — filling the context and, on a smaller window, refusing the first turn. | **Fixed** — injected into the **sent** system copy only (never stored), so the model still reads them every request. The allowance scales with the context (`_SKILLS_BUDGET_RATIO` 8%, floor/ceiling 600/2000 tokens); only **whole** files are included (never truncated mid-rule); dropped as a last resort before refusing. |
| S7 | MED | The first cut of the fix truncated skill text at a fixed 1200-token character boundary. Cutting a rule in half is lossy and harms model quality — worse than omitting the file. | **Fixed** — `get_domain_skills(domains, max_chars=…)` now includes whole files only; a file that does not fit is skipped (API-docs tools remain the fallback). Budget is context-scaled. |

---

## 3. Logging

| # | Sev | Finding | Status |
|---|---|---|---|
| G1 | MED | The addon prints a 🛠️ emoji and em/en-dashes throughout; on a cp1252 stdout this can raise `UnicodeEncodeError` inside the turn loop or a background thread. Blender's console is normally UTF-8 (so it has not bitten in-app), but it is a latent crash vector and `_misc/check_ascii.py` already flags it repo-wide. | **Deferred** — a repo-wide ASCII sweep is out of scope for this branch; noted as a known, pre-existing risk. |
| G2 | LOW | A stale `llama-server.log` was displayed next to a live error. | **Fixed** — the log is cleared at every launch, and when no log is captured the message says so instead of showing stale content. |

---

## 4. Chat turns regardless of context size

| # | Sev | Finding | Status |
|---|---|---|---|
| C1 | MED | Long local sessions overflowed the window and the model appeared to "forget" or hallucinate. | **Addressed** — real-`/props` budgeting, ~60% compaction into a memory block, on-disk archive, and overflow-retry make conversations effectively bounded-and-continuous. |
| C2 | LOW | Remote mode keeps `prompt_budget = 0` (no client-side trimming); it relies on the provider returning a detectable overflow 400. | **Deferred (by design)** — remote windows are large; the transport still classifies and recovers context-length 400s. |

---

## 5. Checkpoints

| # | Sev | Finding | Status |
|---|---|---|---|
| K1 | LOW | Auto-checkpoint at compaction; `restore` is non-destructive (current state snapshotted first); `branch` forks; payload persisted with the memory note. | **Verified** — no change needed; covered by `tests/test_session_memory.py`. |
| K2 | LOW | No automated cross-session (Blender-restart) restore test. | **Deferred** — requires a live Blender; the JSON round-trip is unit-tested. |

---

## What changed on this branch (this pass)

- **Windows launch capture** (`llm_manager.py`): `CREATE_NO_WINDOW` + stdio →
  `llama-server.log`; removed the `CreateProcessW`/`CREATE_NEW_CONSOLE`
  launcher (~170 lines of now-dead ctypes) and the `--log-file` reliance.
- **Debug console** (`llm_manager.py`): `_show_llama_console()` gives the server
  a **visible console window when Debug / Diagnostics mode is on** (via
  `--log-file` the failure tail is still captured); silent otherwise.
- **`--flash-attn` removed** (`llm_manager.py`, L9): the optional-value flag is no longer passed at all — every emitted form could kill startup — so llama-server's `auto` default applies. This was the actual startup crash.
- **Flag safety**: `_filter_flags_against_help(..., keep=_REQUIRED_LLAMA_FLAGS)`
  — optional flags verified against `--help`, required flags never dropped.
- **Orphan cleanup**: `_abandon_launched_server` on readiness failure/timeout.
- **Early model guard** and **port write-back** (from the previous pass).
- **Tests**: `tests/test_llm_manager.py` (`_filter_flags_against_help`,
  required-flag protection, `TestLaunchFlagSafety` for the flash-attn form +
  debug-console helper), plus the earlier `_current_preset_extra_args` and
  `TestTransportBindOrdering` guards.

## Verification

```
python -m unittest tests.test_llm_manager tests.test_addon_imports \
  tests.test_llm_transport_errors tests.test_context_budget \
  tests.test_session_memory tests.test_orchestration_helpers \
  tests.test_turn_loop_integration tests.test_preflight \
  tests.test_prompt_rules tests.test_co_work_guard tests.test_streaming_llm
```

Then rebuild + install and start the agent once. With Debug / Diagnostics on,
watch the llama-server console window; with it off, any failure tail shows the
real server output.
