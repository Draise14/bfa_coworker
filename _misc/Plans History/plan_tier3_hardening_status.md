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

**Post-verification fix (2026-10-01)** — two regressions introduced by the ASCII sweep,
caught during a live addon load:

- The sweep mapped the box-drawing corner `U+2514` to a bare backslash, producing
  `col.label(text="\ ...")` in `preferences.py` (an invalid escape -> `SyntaxWarning`).
  Mapped to `-` instead; the two affected labels are fixed.
- The sweep expanded `ui_chat._SUPERSCRIPT_TR`'s superscript glyphs to multi-character
  `^n` strings, breaking `str.maketrans` ("arguments must have equal length") at register
  time. Restored via `\uXXXX` escapes (ASCII source, real superscripts at runtime:
  `^2` -> `2` superscript).

The sweep tool now protects `str.maketrans(...)` tables and no longer emits a bare
backslash for corner glyphs; a repo-wide `py_compile -W error::SyntaxWarning` pass over
the non-vendor source is clean.

**Live-UI fix (2026-10-01)** — `BFACW_PT_chat_session.draw` raised
`TypeError: UILayout.textbox(): ... invalid keyword argument(s) (text)` on every
redraw, breaking the Session panel. The bound memory editor used
`textbox(props, "session_memory_edit", text=...)`; `UILayout.textbox()` accepts
`data, property, initial_visible_lines, placeholder, ...` — no `text`. Fixed to
`textbox(props, "session_memory_edit", placeholder=...)`.

**First-turn robustness (2026-10-01)** — a first turn on a tight/small context could
still be refused if the fixed tool schema plus the system prompt exceeded the budget.
`_build_send_messages` now has a last-resort step after dropping domain skills: reduce
the tool schema to the always-available surface tools + `load_tools` (the model re-loads
domains on demand) and re-check before refusing. The refusal message now also reports
the message/tool/budget token counts so the cause is diagnosable.

**Auto-recovery for a full context (2026-10-01, from a live session)** — a fresh
2-turn session on the default 16K window filled up: the system prompt (built-in skills,
~10.7k tokens) plus the tool schema (~4k) exceeded the 16K window, so *no* amount of
turn-compaction could help (there were no old turns to retire) and the turn stopped with
the "no longer fits" message. Two changes make it self-heal with no user action:

- `_build_send_messages` gained a final degradation rung: when even the minimal tool set
  does not fit, drop the `## Built-in Skills` section from the **sent** system copy
  (`_strip_builtin_skills`; the stored prompt is untouched) and re-check. This reclaims
  several thousand tokens, so the turn runs. The API-docs tools remain for lookups.
- On a preflight refusal the turn loop now **auto-forces a compaction and rebuilds once**
  before surfacing any error — the user should never have to press "Compact Now".

Note: the always-loaded skills are now **budgeted to the window** rather than
dropped whole (see below), so a 16K window keeps the version-drift files + best
practices instead of losing every skill. A larger context keeps everything.

**Window-aware skills + 32K default (2026-10-01)** — the built-in skills
(~7k tokens) plus the tool schema did not fit a 16K window, so the send-time
fallback dropped the entire skills block and the agent lost all its API
guidance. Two changes:

- `skills.get_always_loaded_skills(..., max_tokens=...)` now includes only the
  **whole files that fit**, in priority order (newest version-drift file first --
  it prevents hard API crashes -- then older drift files, best practices, MCP
  tool guide, naming). A file is never truncated. On 16K this keeps ~6.3k tokens
  (all drift files + best practices + naming); on 32K it keeps everything.
  `_get_system_prompt_with_rules` sets the budget to ~40% of the configured
  window. *(Later tightened for prefill latency: 15% of the window, clamped
  to 1,024-4,500 tokens -- `_SKILLS_LOAD_RATIO` / `_SKILLS_LOAD_MIN` /
  `_SKILLS_LOAD_MAX` in `agent_controller.py`.)*
- `LLMConfig.local_ctx_size` default raised **16384 -> 32768** (the Preferences
  default was already 32768, so this aligns the two, and matches the "hardware
  unknown" recommendation).

**Follow-up pass (2026-10-01, later)** -- the remaining memory-hygiene and proof gaps:

- MED -- turn-scoped `[System: ...]` context messages (entity warnings, the tool-result
  filler prompt) were fed to the memory writer and heuristic verbatim at compaction, so a
  warning that was only true for its own turn ("You have already created these entities
  this turn") became a false memory in the block -- the exact mangled-prompt session-log
  confusion. They are now excluded from memory building, and the filler prompt carries the
  same `[System: ...]` marker.
- LOW (S3, previously deferred) -- the session turn counter incremented per tool-loop
  iteration (in `_maybe_compact_session`); it now increments once per user turn in the turn
  entry, so the memory "Last updated: turn N" stamp is accurate.
- LOW -- the exported session log dumped every `[REASONING]` entry untruncated; entries are
  now bounded to a 600-char excerpt with a truncation note.
- PROOF -- the exact live HTTP 500 tool-call fault ("Failed to parse tool call arguments as
  JSON ... column 1525 ... missing closing quote") is now replayed mid-tool-loop through
  the real turn loop: one nudge retry recovers in the same turn, and a persistent fault
  ends the turn with the friendly actionable message and the history intact.
- TEST HARNESS -- fixed a latent integration-harness defect the new tests exposed: the
  per-class transport reload updated `sys.modules` but not the package attribute, so
  `bind()`/`bind_helpers()` landed on one module instance while the turn loop's request
  functions came from another (any class after the first ran against an unbound
  transport).

**Co-work Phases 5 & 6 (2026-10-01, latest)** -- the two previously deferred scene-safety
phases are now implemented and verified through the real turn loop against the stub
bridge (no live Blender needed for the decision logic; the generated toolcode is
string-tested):

- Phase 5 -- user-edit detection & re-sync: `_EntitySnapshot` gains a co-work
  fingerprint (`active_object`, `selected_object_names`, `mode`), reported by the merged
  undo+snapshot toolcode (guarded `_act` helper) and read defensively by `from_dict`. At
  every successful step boundary `_detect_foreign_edit` compares the pre-step and
  post-step fingerprints, attributing a change to the step only when the step's code
  plausibly performed it (`mode_set`, `select_set`, an explicit active assignment, or an
  object the step just created). A foreign edit injects a turn-scoped
  `[System: The user changed the scene ...]` re-sync message (excluded from session
  memory by the `[System:]` filter) and disarms the global undo.
- Phase 6 -- scoped auto-undo (D8): after a failed non-code-bug step the loop first
  captures the failed step's partial diff with a snapshot-ONLY toolcode (no undo
  bookmark -- pushing first would make the undo pop our own bookmark), then runs the
  global undo ONLY when no foreign edit was seen since the baseline; the idempotent
  `_build_cleanup_code` then removes the failed step's own datablocks (whole-turn diff
  only as a legacy fallback when the capture failed AND the undo did not run);
  `_build_scene_restore_code` restores the pre-step active object and selection by name;
  and a fresh baseline push WITH snapshot re-arms diffing and resets the undo guard.
  In-place edits (modifier_apply, mesh edits) remain deliberately unreverted (D9).
- Tests: `TestCoWorkForeignEditDetection` (detector attribution rules, restore and
  snapshot-only toolcode shapes, fingerprint keys) and
  `TestCoWorkUserEditAndScopedUndo` (user edit detected and re-synced through the real
  loop; scoped recovery order capture-before-undo; global undo skipped when the user
  edited). The stub bridge reports the fingerprint, supports snapshot-only capture and
  one-shot tool failures.

**Deferred / by design**

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
- A dedicated `check_namespace.py` cleanup pass (pre-existing `__all__` noise).
- ~~Co-work plan Phases 5 and 6 deferred~~ -- implemented and tested through the real turn
  loop (see "Co-work Phases 5 & 6" in section 3); only the live-Blender sign-off remains,
  tracked in the co-work plan's manual checklist.

---

## 6. Release check for 1.1.37 (2026-10-06)

Re-audited all Tier 3 plans against the code before the 1.1.37 release:

| Check | Result |
|---|---|
| Unit suite (Python 3.11, `pytest tests`) | 729+ pass; only failures are environment-only (`mcp.server` missing: `test_rst_search`, `test_rst_parse`, `test_mcp_server`, `test_harness_config`, `test_tool_listing`) |
| `python _misc/check_ascii.py` | exit 0 (fixed a regression of 7 glyphs in `ui_chat.py`; the checker now skips local `.venv` dirs and cannot crash on a cp1252 console) |
| SPDX (`check_license.py`) | only the known `autofix.py` / `blender_templates.py` misses |
| `-W error::SyntaxWarning` parse of non-vendor source | clean |
| ruff vs `main` | ~80 new findings, all in categories the repo already carries (UP032, BLE001, S110); the one B023 is a false positive (closure called in the same iteration) |
| Fixed in this pass | `process_guard.linux_preexec` loaded libc inside the forked child (dlopen after fork in multi-threaded Blender can deadlock) -- now loaded in the parent |

Still open before tagging: the manual in-Blender checklists of each Tier 3 plan, and
regenerating the wiki (`_misc/generate_wiki.py`) once the UI is final.
