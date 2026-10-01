# BFA Coworker — Tier 3 Hardening Status (issue #74)

**Date**: 2026-10-01
**Branch**: `fix/scene-safety-local-hardening`
**Status**: COMPLETE — all planned phases landed, verified, and committed
**Scope**: session memory & compaction, checkpoints, co-work scene guards, context
budgeting, transport error handling, skills loading, remote tool selection, and the
repo-wide ASCII sweep.

This is the status check for the whole Tier 3 co-work / local-mode hardening effort.
Prior planning documents are archived under `_misc/Plans History/`:

- `plan_tier3_session_memory_checkpoints.md` — the original session-memory & checkpoints plan.
- `plan_cowork_scene_safety_and_local_hardening.md` — the co-work scene-safety plan (Phases 1-4, 7, 8 done; 5, 6 deferred).
- `plan_tier3_scene_safety_local_hardening.md` — the verification-first quality-audit plan for this pass.
- `adversarial_review_stability.md` — the adversarial launch/run/memory/session review.

---

## 1. Status

| Area | State |
|---|---|
| Real-context budgeting + prompt preflight | Done |
| Session memory (bounded window + memory block + archive) | Done |
| Automatic checkpoints + restore | Done |
| Co-work soft scene lock | Done |
| Scene preflight operator/index checks | Done |
| Tool-output slimming (poll/index collapsing) | Done |
| llama-server launch & error hardening | Done |
| Skills loading (version-drift + domain) | Done |
| Remote/harness domain intelligence | Done |
| Console color (replacing emoji) | Done |
| Repo-wide ASCII sweep (`check_ascii.py` exits 0) | Done |

**Verification (this pass)**

| Check | Result |
|---|---|
| `python -m unittest <full list>` | **528 pass**, 1 skip |
| `python _misc/check_ascii.py` | exit 0 |
| `_misc/ascii_sweep.py --check` | 0 files to change (idempotent) |
| `python _misc/check_namespace.py addon` | pre-existing noise (`__all__` policy), no new non-vendor entries added by this work |
| `tests.test_mcp_server` / `tests.test_harness_config` | fail to import — **pre-existing** environment issue (broken/absent `mcp` package), not caused by this work |
| `make benchmark` | not run — `make` is not installed in this environment; run `unittest` directly |

---

## 2. What shipped

### Session memory, compaction, checkpoints
- Checkpoints now snapshot the **pre-compaction** state (threshold, overflow, and manual),
  so `restore` can genuinely rewind before a summary. Previously they captured the
  already-reduced window.
- `restore()` captured the target checkpoint *after* adding the `pre-restore` snapshot — at
  the 10-checkpoint cap the list trim shifted indices, so it restored the wrong checkpoint
  (or, at the newest index, the just-created snapshot — a no-op). Captured before now, and
  `memory_updated_turn` is restored too.
- `compact_history` never retires or archives the system prompt.
- `archive.jsonl` is capped and rotated (newest-N); `append_archive` reports the bytes it wrote.
- Removed the dead, divergent `estimate_history_tokens`.
- "Compact Now" no longer wipes a short conversation: smaller manual window (8), refuses when
  nothing is retirable, and snapshots the pre-compaction state first.
- The "Branch" operator (which silently overwrote the live session) was removed.
- "New Thread" resets memory, checkpoints, turn counter, and loaded domains.
- The memory block is editable through a real bound multiline textbox.
- A shared lock guards the store against turn-thread / UI races.

### Context budgeting & transport
- The reply allowance is recomputed from the configured size each tool-loop iteration (it could
  only shrink before).
- Streamed inline `thinking` content is added exactly once (was duplicated).
- Non-retryable 4xx fail fast; 503 backoff is bounded (~120 s); stale error state is cleared on
  success; a streaming failure before the first token records its reason.

### llama-server launch
- `--log-file` is validated against the build's `--help` before being appended in Debug mode.

### Scene guards
- A failed unlock no longer clears the priors while objects stay `hide_select = True` (which
  could persist the lock into a saved `.blend`); the registry is thread-safe.
- Preflight no longer lets an unrelated `if` / `.get()` defeat the unguarded-index or
  active-object checks.

### Skills
- **Version-drift skills never loaded**: `_version_loaded` compared the filename version
  (`53`) against the *minor* (`3`), so no `blender_*.md` ever matched. Now `major*10 + minor`.
- The skills cache is keyed on the Blender version, so `Preferences` drawing
  (`list_loaded_skills()` with no version) can no longer poison the versioned build.
- Skill files are cached (no per-request disk reads).

### Tool reachability & remote mode
- Five MCP tools were in no surface/domain set and were unreachable
  (`get_blendfile_summary_missing_files`, `_of_linked_libraries`, `_path_info`,
  `_usage_guess`, `get_polyhaven_status`). Now always available; a coverage guard test fails if
  any tool becomes orphaned again.
- Remote API mode now gets the same domain pipeline as local: keyword + scene detection,
  tool-schema filtering, and whole-file domain-skill injection at a conservative flat
  allowance. Domains are **session-sticky** and the tool list is **sorted by name**, so a
  provider's prompt-cache prefix stays stable. `load_tools` is offered to remote providers too.

### Console & source hygiene
- The `[emoji]Coworker` console prefixes (the only non-ASCII that could reach a `print()`) were
  replaced with ASCII tags **and ANSI color**: cyan `[Coworker]`, yellow `[Coworker][WARN]`,
  red for error lines (`NO_COLOR` respected; console-only, never the log file).
- `check_ascii.py` now skips the vendored deps and upstream API examples, and the remaining
  non-ASCII in our own tracked source was swept to ASCII via the idempotent
  `_misc/ascii_sweep.py`. `ui_chat._LATEX_SYMBOLS` (a deliberate glyph table) was protected by
  escaping its values to `\uXXXX`, so the LaTeX converter still emits the same glyphs.

---

## 3. Findings log (adversarial, this pass)

Severity: HIGH = data loss / wrong result / crash-class; MED = incorrect/fragile; LOW = cosmetic.

**Fixed**

- HIGH — `restore()` returned the wrong checkpoint at capacity, and did not restore the turn stamp.
- HIGH — auto-checkpoints captured the post-compaction state (could not rewind).
- HIGH — "Compact Now" could retire the whole conversation down to the system prompt, with no undo.
- HIGH — "Branch" overwrote the live session (contradicting its tooltip) — removed.
- HIGH — streamed inline `thinking` content appended twice.
- HIGH — version-drift skills never loaded (`53 <= 3`).
- HIGH — five MCP tools unreachable (orphaned from surface + domains).
- MED — New Thread leaked memory / checkpoints / turn count / domains.
- MED — "View / Edit Memory" wrote to an unbound property (no textbox).
- MED — compaction could archive the system prompt; archive unbounded; no lock around the store.
- MED — reply allowance decayed across tool-loop iterations.
- MED — non-retryable 4xx retried 5x; 503 backoff unbounded; stale errors not cleared; stream
  pre-first-token failures lost their reason.
- MED — scene asset domain key mismatch (`asset_browser` vs `assets`); `_detect_domain` first-match only.
- MED — remote mode had no domain filtering/skills; `load_tools` was local-only.
- MED — preflight suppression defeated by any `if` / `.get(`.
- MED-HIGH — failed scene unlock leaked `hide_select`; lock registry not thread-safe.
- LOW — stored tool results unbounded; skills cache not version-keyed; skill files re-read every request.

**Deferred / by design**

- LOW — the session turn counter still increments per tool-loop iteration, so the memory
  "Last updated: turn N" stamp can over-count on multi-iteration AGENT turns. Cosmetic.
- LOW — no automated cross-session (Blender-restart) checkpoint restore test; the JSON
  round-trip is unit-tested.
- By design — `co_work_guard` locks only *created* datablocks (not merely *modified* ones);
  the module docstring was corrected to say so.
- By design — remote mode keeps `prompt_budget = 0` (no client-side trimming) but now gets
  domain filtering + skills; overflow recovery still classifies server 400s.

---

## 4. Recommendation: rebuild, install, and run one long session

The unit suite and static checks are green, but the acceptance criteria for issue #74 also
include a live run. After compiling:

1. Start the agent with **Debug / Diagnostics** on; confirm the llama-server console opens and
   the log tail is captured either way.
2. Run one long session including a `poll() failed` traceback and a forced context overflow.
   Confirm: no raw `400`, a visible "Compacting conversation...", restore recovers pre-compaction
   turns, and Start/Stop leaves no object un-selectable.
3. Start once on a low-RAM configuration and confirm no OOM.

---

## 5. Follow-ups (nice to have)

- A live-Blender cross-session checkpoint restore test (issue-#74 K2).
- Per-user-turn (not per-iteration) turn counting for the memory stamp (S3).
- A dedicated `check_namespace.py` cleanup pass (pre-existing `__all__` noise).
- Co-work plan Phases 5 (user-edit detection) and 6 (scoped auto-undo) remain deferred —
  they mutate the destructive global-undo path and need a live Blender to verify.
