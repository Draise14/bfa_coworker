# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

### TODO (Future Plans)

#### High Priority
- [x] **Bug:** Make local model detection and "link" more robust
- [x] **Download Progress Bar** — Replace text-based download progress with a visual progress bar in the preferences panel.
- [x] **Cancel Downloads** — Add a cancel button for in-progress downloads.

#### Medium Priority
- [ ] **Add history chat to a text file with a button to open it in a floating window** - so we can copy and paste the results and save the log from the chat
- [x] **SKILL.md Update** — Rewrite `.github/skills/self-contained-blender-mcp/SKILL.md` to reflect current project goals and branding.
- [x] **DOCUMENTATION.md** — Create user-facing documentation covering installation, quick start, model management, remote API setup, and troubleshooting.
- [ ] **GGUF Header Parsing** — Read GGUF file headers to detect parameter count and quantization for non-preset models, enabling auto-populated RAM/disk estimates.

#### Low Priority
- [ ] **System RAM Detection** — Use platform-specific API to detect available RAM and filter/hide presets that exceed system capacity.
- [ ] **Add Model Generator** locally, Ultrashape, Hunyuan, similar to here: https://github.com/ahujasid/blender-mcp
- [x] **Add CC0 resource downloader** from Polyhaven, AmbientC00, Sketchfab, etc — Polyhaven tools implemented in v1.1.37.

## [Unreleased]

### Added

- **The sidebar now keeps the WHOLE conversation visible — a compaction can no longer look like the chat vanished.** Retired turns are kept in memory (and persisted in the session-memory sidecar) and rendered as ORDINARY turns in ONE continuous conversation — your prompt, the reasoning (surface), the Workshop (deeper), then the conclusion — whether or not the turn has since been retired from the model's context. Grouping the retired and live messages together also recombines a turn whose prompt was retired while its reply stayed live back into a single bubble. When a compaction happens, an **"Archive — N turn(s) compacted"** node is recorded in the Workshop timeline (with the memory summary) so you can audit exactly where your context was compressed. Copying a message works on retired turns too. The model's context is unchanged (retired turns are never re-sent); this is purely so you can always read your own history. Failed/spiral attempts are excluded as duplicates, and **New Thread** / checkpoint restore clear the aggregate.
- **Thinking spinner changed to a four-phase animation.** A braille dot expands into a full cell, contracts back, then a single dot orbits the cell one way and back the other, looping seamlessly. The chat redraw ticks faster (0.15s vs 0.5s) while a turn is running so the motion reads as a spin instead of a slideshow.
- **Chat panel is now robust on long turns and clearer to read.** The conversation history is snapshotted once per frame — the turn loop mutates it on a worker thread, which previously raised "list changed size during iteration" and blanked the *whole* history for the rest of a long turn (it reappeared when the turn ended). An out-of-range copy index no longer raises, and each turn is drawn in isolation so one bad turn cannot hide the rest. Each turn now labels your input (`You:`) and shows the **live** reply while the coworker works, swapping to the final message only when the turn ends; the startup greeting is shown once with its agent icon.
- **Per-turn cost readout (latency groundwork).** Every turn logs a one-line cost breakdown — prompt prefill vs generation tokens/seconds, tool calls, nudges, wall time — and the Status & Diagnostics panel shows a "Last turn" line, so a slow local turn can be attributed to prefill, generation, or tool overhead instead of guessed at.
- **Co-working, safely: the agent now notices when you touch the scene mid-turn (scene-safety Phases 5 & 6).** Every step boundary compares a scene fingerprint (active object, selection, mode -- carried by the merged undo+snapshot toolcode) against the previous one, attributing a change to the agent's own code only when that code plausibly performed it. When *you* re-target the scene while the coworker works, it is told exactly what changed (`[System: The user changed the scene while you were working -- active object is now X ...]`) and re-fetches by name instead of fighting your edits. The failed-step recovery is now **scoped**: the failed attempt's partial creations are captured with a snapshot-only toolcode (no undo bookmark, so the undo cannot pop our own), the global `bpy.ops.ed.undo()` fires **only** when no foreign edit was seen since the baseline -- it can no longer revert YOUR last edit -- and an idempotent cleanup removes only the failed step's own datablocks, restores the pre-step active object and selection, and re-baselines the snapshot. In-place edits (modifier_apply, mesh edits) remain deliberately unreverted.

### Fixed

- **Compaction could retire the CURRENT turn's prompt, leaving the chat panel with no active turn.** The addon's injected context messages (`[System: The tool results are above ...]`, entity warnings) are stored with the `user` role, and the retire-boundary logic treated ANY `user`-role message as a turn boundary. Mid-turn, the boundary could land on one of those notes and retire your real prompt instead — so the live window was anchored on a note, the panel showed no turn ("new messages" but nothing visible), and your prompt sat in the archive. The boundary now recognizes only REAL user messages, so the current turn's prompt is never retired; the threshold path also retires older turns mid-turn (keeping the current one).
- **The conversation could "nuke itself" on a long turn.** The automatic context-overflow recovery called the compaction helper *without* the "nothing retirable" guard the threshold path and the manual **Compact Now** button both have. When a long single-turn run had no `user`-role boundary in the recent window, that retired **everything except the system prompt** — and took no checkpoint, so it was unrecoverable. The helper is now a safe no-op when nothing can be retired, the forced path forwards the current turn instead of wiping, and it only snapshots/changes anything when something actually retires. (Part of the issue #74 context-hardening work.)
- **The console spammed `ERROR: Python context internal state bug. this should not happen!`.** The conversation turn runs on a worker thread, and it was touching Blender's C API off the main thread (writing the `chat_status` UI property, building the system prompt from `bpy.app.version` / `bpy.context.preferences` / `bpy.utils.user_resource`, and reading the Polyhaven resolution from preferences). Each off-main call races Blender's single global Python context counter. The worker now records plain-Python state only — the status is mirrored into the UI by the main-thread timer, and the bpy-derived values are prefetched on the main thread before the worker starts.
- **The co-work scene lock could stay on after a turn**, leaving the objects the coworker built un-selectable ("I can't edit what it made"). Two fixes: the lock toolcode no longer re-records an already-locked datablock's `hide_select` as its "prior" (which made the turn-end unlock *re-lock* it forever), and a failed unlock now keeps its saved values for a later retry instead of discarding them while the scene stayed locked. Everything is released when the turn ends — FINISH, error, Stop, or exception.
- **The lock no longer takes control of what *you* are working on.** The objects selected — and the active object — when a turn starts are excluded from the lock, so an element you are editing stays selectable even if the coworker created it in an earlier turn.
- **The local model is no longer told to "think aloud / be thorough."** That verbose STYLE line was prepended in *every* mode, contradicting the compact prompt's "be concise and decisive"; local mode now gets a concise style (remote keeps the verbose one).
- **The startup greeting lost its agent icon**, and the empty-history hint did not wrap; both fixed.
- **Turn-scoped system notes could become false session memories.** The addon's injected context messages ("[System: WARNING -- You already created these entities this turn ...]", the tool-result filler prompt) were stored as `user` messages, and at compaction they were fed to the memory writer and the heuristic summarizer verbatim -- so a warning that was only true for the turn that produced it was carried into the memory block as "Recent context". In a later turn, or in a brand-new thread, the model then reasoned in circles about entities it was told it "already created this turn" (visible as the confused mangled-prompt session log). Turn-scoped `[System: ...]` messages are now excluded from memory building, and the tool-result filler prompt carries the same `[System: ...]` marker so it is excluded too.
- **The session turn counter incremented per tool-loop iteration**, so the memory block's "Last updated: turn N" stamp could jump by several per user turn (adversarial review S3). It now counts once per user turn.
- **Session logs dumped full chain-of-thought.** The exported session log printed every `[REASONING]` entry untruncated -- thousands of characters of (sometimes garbled) model reasoning. Reasoning entries are now bounded to a 600-char excerpt with a "[truncated, N chars total]" note.
- **The HTTP 500 malformed-tool-call recovery is now proven mid-tool-loop.** The live failure (llama-server 500 "Failed to parse tool call arguments as JSON ... column 1525 ... missing closing quote", the bouncing-ball benchmark) is replayed through the real turn loop against a scripted server: one retry with the split-into-smaller-calls nudge succeeds in the same turn, and if the nudge retry also fails the turn ends with the friendly actionable message (history intact, retry budget not burned on the same oversized payload).
- **A test-harness defect could run the turn loop against an unbound transport.** The integration harness re-loaded `llm_transport` per test class but only updated `sys.modules`, not the package attribute used by `from . import llm_transport` -- so `bind()`/`bind_helpers()` landed on one instance while the request functions came from another. Any test class running after the first was exposed; the harness now keeps both resolutions on the same instance.
- **A full context now self-heals instead of stopping the user.** On a fresh session that filled the window, the turn stopped with "conversation no longer fits" and did **not** auto-compact — because there were no old turns to retire: the system prompt (its built-in skills section, ~10.7k tokens) plus the tool schema (~4k) exceeded the context window on their own. Two changes make it recover without any user action: (1) the payload builder now has a final degradation rung that drops the `## Built-in Skills` section from the **sent** system copy (the stored prompt is untouched) when even the minimal tool set does not fit, reclaiming several thousand tokens, and (2) a preflight refusal now **auto-forces a compaction and rebuilds once** before surfacing any error, so the user never has to press "Compact Now". On a 16K window the built-in skills are dropped for most turns (they do not fit alongside the tool schema); a larger context window keeps them.
- **Session panel crashed on every redraw** — the bound memory editor called `UILayout.textbox(props, "session_memory_edit", text=...)`, but `UILayout.textbox()` does not accept a `text` keyword (it takes `data, property, initial_visible_lines, placeholder, ...`). This raised `TypeError` in `BFACW_PT_chat_session.draw` and left the Session panel unusable. Fixed to pass `placeholder=` instead.
- **A first turn on a tight context could be refused** — when the system prompt plus the fixed tool schema exceeded the prompt budget, the turn failed with "conversation no longer fits" even though nothing had been said. The payload builder now falls back to a minimal tool schema (the always-available surface tools plus `load_tools`, so the model can re-load a domain on demand) before refusing, and the refusal message reports the message/tool/budget token counts so the cause is visible.
- **Two ASCII-sweep regressions** — the sweep mapped the box-drawing corner `└` to a bare backslash (an invalid Python escape: `SyntaxWarning` in `preferences.py`) and expanded the `ui_chat._SUPERSCRIPT_TR` superscript glyphs to multi-character `^n` strings, breaking `str.maketrans` ("the first two maketrans arguments must have equal length"). Corner glyphs now map to `-`, and the sweep tool preserves `str.maketrans(...)` tables by escaping their non-ASCII to `\uXXXX` (as it already did for the LaTeX symbol table).
- **Quality pass: session memory, checkpoints, scene guards, context budgeting, remote tool selection.** A verification-first audit of the co-work hardening (issue #74) found and fixed a set of real defects:
  - **Checkpoints could restore the wrong state.** `restore()` validated the index, then added a `pre-restore` snapshot (which trims the list to the last 10), then read `checkpoints[index]` — so at capacity it restored a *shifted* checkpoint, and selecting the newest restored the just-created snapshot (a silent no-op). The target is now captured *before* snapshotting, and `memory_updated_turn` is restored too.
  - **Auto-checkpoints captured the post-compaction state**, so restoring a `compaction` checkpoint gave the already-summarized window and could not rewind before the summary. Automatic checkpoints (threshold, overflow, and manual) now snapshot the **pre-compaction** state.
  - **"Compact Now" could wipe the conversation.** It used the default 20-turn window and guarded only `len(history) < 4`, so any history ≤ 21 messages was retired down to the system prompt — with no checkpoint that could undo it. It now uses a smaller manual window (8), refuses to run when there is nothing genuinely retirable, and snapshots the pre-compaction state first.
  - **The "Branch" operator silently overwrote the live session** despite promising not to. It has been removed for now.
  - **"New Thread" left the previous session's memory block, checkpoints and turn counter in place**, so stale context leaked into the new thread. It now resets all session state (and the loaded-domain set).
  - **The "View / Edit Memory" button was unusable** — it wrote to a property that had no bound textbox. The Session panel now has a real multiline memory editor with an Apply button.
  - **Compaction could archive the system prompt**; it never does now.
  - **The session-memory archive grew without bound**; it is now capped and rotated (newest-N), and `restore`/sidecar writes are guarded by a shared lock so UI actions cannot race a turn.
  - **Streamed inline `thinking` blocks were appended to the message content twice**; the tagged text is now routed to reasoning and the visible text added exactly once.
  - **The reply allowance shrank monotonically across tool-loop iterations** (the capped value was fed back as the requested value); it is now always recomputed from the configured size.
  - **Non-retryable HTTP 4xx (401/403/404/422) were retried five times**, masking the real error; they now fail fast. The 503 retry budget is bounded to ~120 s, a successful request clears stale error state, and a streaming failure before the first token records its reason.
  - **Version-drift skills never loaded.** `_version_loaded` compared the filename version (e.g. `53`) against the *minor* (`3`), so no `blender_*.md` file ever matched. It now compares against `major*10 + minor`. The skills cache is also keyed on the Blender version, so drawing Preferences (which calls the loader without a version) can no longer poison the versioned build.
  - **The scene asset domain was never consumed** (scene detection emitted `asset_browser`, the maps use `assets`), and `_detect_domain` returned only the first match. Detection now accumulates all matching domains and uses the correct `assets` key.
  - **Five MCP tools were unreachable** (in no surface or domain set): `get_blendfile_summary_missing_files`, `get_blendfile_summary_of_linked_libraries`, `get_blendfile_summary_path_info`, `get_blendfile_summary_usage_guess`, `get_polyhaven_status`. They are now always available; a coverage guard test fails if any tool becomes orphaned again.
  - **Unguarded-index preflight was defeated by any `if`/`.get()`** anywhere in the script; the guard must now cover the same collection. Active-object preflight no longer accepts a bare `.get()` as proof an active object was set.
  - **A failed scene unlock leaked `hide_select`** (the priors were cleared while objects stayed locked, so a saved `.blend` could keep them un-selectable). Priors are now kept on failure so a later attempt can restore them; the lock registry is thread-safe.

### Added

- **Window-aware built-in skills + 32K default context.** The always-loaded skills (~7k tokens) plus the tool schema did not fit a 16K window, so the agent lost all its API guidance on small contexts. `get_always_loaded_skills()` now takes a token budget and includes only the **whole files that fit**, in priority order (the current version's drift file first, then older drift files, best practices, MCP tool guide, naming) — a file is never truncated. The system prompt budgets them to ~40% of the configured window, so a 16K window keeps the crash-critical version-drift files and best practices instead of dropping everything, while a 32K window keeps all of them. The default context size (`LLMConfig.local_ctx_size`) is raised from 16384 to 32768 to match the Preferences default and the "hardware unknown" recommendation.
- **Remote/harness domain intelligence.** Remote (API) mode now gets the same smart domain pipeline as local: keyword + scene detection, tool-schema filtering, and domain-skill injection, at a conservative flat allowance (`_SKILLS_REMOTE_MAX_TOKENS`). Domains are **session-sticky** (the set only grows) and the tool list is sorted by name, so a provider's prompt-cache prefix stays stable across turns. The `load_tools` meta-tool is now offered to remote providers too (previously local-only), and the "New Thread" action resets the loaded-domain set.
- **Tool-result storage cap** (`_MAX_STORED_TOOL_RESULT_CHARS = 20000`): the in-memory session can no longer grow without bound from a pathological traceback, while the chat panel still shows the real output.
- New tests: `tests/test_tool_coverage.py` (every MCP tool reachable), `tests/test_skills_cache.py` (version-keyed cache), plus regressions for checkpoint capacity/rotation, manual compaction, inline think-tags, preflight suppression, and the streaming fallback.

- **Domain-skill reference no longer floods the context, and tunes itself** — the version-aware skill files (`animation.md`, `materials.md`, …) were appended straight into the **stored** system prompt. Because scene detection pulls in a domain for almost any scene (a mesh → modelling, a material → materials, …), several large files accumulated in the system prompt over a session (up to ~6.5k tokens) and, on a smaller window, made the context fill — and the first turn be refused — almost immediately. The skills are now injected into the **sent** system copy only (never stored, so they cannot accumulate), so the model still reads them on every request and keeps its domain know-how. The allowance is **self-tuning** — it takes whatever is genuinely spare after the messages, the tool schema, and a conversation reserve (`_SKILLS_RESERVE_RATIO` 30%, floor 1024 tokens), so a bigger window or a shorter conversation automatically keeps more skills and there is no ratio or ceiling for the user to configure. Only **whole** skill files are included — a rule is never truncated mid-file (a file that does not fit is skipped, falling back to the always-available `get_python_api_docs` / `search_api_docs` tools); the reference is dropped entirely as a last resort before a turn is ever refused. The system prompt stays small and stable across turns.
- **"This conversation no longer fits the local context window" on the very first message** — the prompt budget reserved the *configured* reply size (`local_max_tokens`, which defaults to the whole context size) for the model's answer, clamped to half the window. On an effective 16K window that left barely a third for the system prompt, skills and the tool schema, so a turn's fixed overhead could exceed the budget and the first message was refused even though nothing had been said. The budget now reserves only a small reply floor and guarantees the prompt the rest of the window; the actual reply is capped to whatever space genuinely remains after the prompt is built (never below a small floor), so `prompt + reply` always fits and a normal first turn is never rejected. The session-memory compaction trigger was also fixed: it compared an estimate that *included* the tool schema against a budget that had the tool schema *subtracted* (firing far too early), and it could fire a no-op compaction — and show a "Compacting…" status — on the first turn when there was nothing old enough to retire. It now compares consistently and only compacts when there is something to retire.
- **Local model refused to start: `unknown value for --flash-attn` / `invalid argument: --flash-attn=on`** — in current llama.cpp `--flash-attn` takes an *optional* value (`[on|off|auto]`), which is a startup hazard in every form the launcher could emit: the bare flag before another argument makes the parser read that argument as its value (llama-server exits 1 with `unknown value for --flash-attn: '--batch-size'`), while the `=` form is rejected outright by the pinned build (`invalid argument: --flash-attn=on`). The launcher no longer passes `--flash-attn` at all and relies on llama-server's **`auto`** default (flash-attention enabled when the backend supports it — the desired behaviour on GPU anyway), eliminating the whole bug class. This was the real cause behind the earlier "llama-server exited during startup (exit code 1)" reports — it only became visible once the Windows launch output was captured.
- **Graceful HTTP 400/500 handling on the local LLM path** — A request that exceeded llama-server's context window was reported to the user as a bare `LLM request failed: HTTP Error 400: Bad Request` (the error body was read once, never cached, then re-read from an already-exhausted stream), retried five times identically, and eventually surfaced with no actionable cause. The transport now reads and caches the error body a single time so the real server reason always survives, recognises llama-server's context-window 400 (`exceed_context_size_error`) and flags it as `context_overflow`, fails fast on non-template 400s instead of retrying an identical payload, and reports the true cause. The turn loop then **auto-compacts the conversation and retries once**, and only shows a friendly "conversation no longer fits the local context window" message if the reduced window still cannot fit.
- **Every request path is now budgeted** — the viewport **screenshot** is counted against the budget (it was previously appended *after* the preflight and was therefore unbudgeted — a direct route to a 400), screenshots are costed at a fixed vision-token count rather than their base64 length, the **auto-continue** (`finish_reason=length`) and **forced-summary** requests are trimmed and preflighted like the main request, and budget enforcement stays on with a conservative fallback context when `/props` is unavailable instead of silently disabling trimming.
- **Non-fatal memory-writer failures no longer poison the turn** — a transient failure of the session-memory writer LLM call is handled by the heuristic fallback but no longer leaves its error in the agent state; a request that succeeds after an overflow recovery also clears the stale error.
- **Qwen preset launch flags are actually applied** — `start_local_llama` called `_current_preset_extra_args()`, a function that did not exist, so the Qwen presets' `--no-context-shift` was never applied and launch could raise `NameError`. The helper is implemented (matches config repo + filename against `PRESET_MODELS`, returns `()` for custom models, never raises).
- **Addon failed to import in Blender (`NameError`)** — two stacked missing-import bugs, each hidden behind the other, stopped the addon loading at all: `agent_controller` used `types.SimpleNamespace` without `import types` (dropped by the transport split), and `ui_chat` used the bare `IntProperty` without importing it from `bpy.props`. The first crashed at import so the second never ran; fixing one exposed the other. Both are fixed, and new `tests/test_addon_imports.py` scans every addon module for a bare `bpy.props` call whose name is not imported (plus a parse check) so the class of bug fails in CI, not in Blender.
- **Agent start crashed with `AttributeError: '_BFACW_Preferences' object has no attribute 'lock_scene_while_working'`** — the addon files were updated while the extension stayed enabled, so Blender's running session still had the older preferences class registered. The scene-lock preference is now read defensively (`getattr(..., True)`) at every sync site, so a stale preferences class can never block agent start. (Restart Bforartists to pick up the new preference row.)
- **Spurious "llama-server exited during startup (exit code 1) — check ... port" with a healthy server log** — when the configured LLM port was already taken, `start_local_llama` launched the server on the next free port but did **not** update the config, so the readiness probe (and later chat requests) kept targeting the busy port; and `wait_until_ready` reported a startup failure whenever the launched process exited, even if a server was actually answering. The chosen port is now written back to the config, a busy configured port is logged, the error names the port and a possible port conflict, and an exit is treated as ready when a server is still serving. This makes agent start reliable when a stale llama-server holds the port.
- **Misleading startup-failure diagnostics on Windows (stale log; invisible real cause)** — the Windows launcher runs llama-server in a brand-new console and never wrote `llama-server.log`, so the error tail showed a **stale file from a previous run** next to a real exit code, hiding the actual reason. The log file is now cleared at every launch and the launcher captures llama-server's own stdout/stderr into it directly (on Windows the launcher now uses `CREATE_NO_WINDOW` with redirected stdio instead of a detached console whose output the addon could not read), so the failure tail reflects the real launch on every platform. When no log is available the message says so instead of showing stale content.
- **Unsupported launch flags could kill startup** — optional flags (e.g. Qwen's `--no-context-shift`, `--flash-attn`, KV-quant, batch knobs) are now verified against the binary's own `--help` output (cached per executable) and any flag the build does not advertise is dropped with a log line, instead of making llama-server exit 1 on an unknown argument. A missing/empty model file now fails early with an actionable message rather than a doomed launch, an abandoned launch terminates its process so a timed-out load cannot hold the port for the next start, every launch-critical flag is protected from the help-filter so a probe miss can never strip the port, model, or context size, and the readiness probe grants a short grace period before declaring a failure.

### Added

- **llama-server console window in Debug mode** — normal launches stay silent (output is captured to `llama-server.log` for the failure tail), but with **Debug / Diagnostics** enabled the local server now starts in its own visible console window so you can watch it load a model live (token/s, GPU offload, errors). The log file is still written in this mode (via `--log-file`), so the failure tail remains available either way.
- **Conversation history lives with the chat input again; the separate panel is now Session** — the message history was moved out of the input panel in the previous pass, which made the Send button feel disconnected from the conversation. The history (turns, Workshop, conclusions) is drawn back **inside the Coworker panel**, directly under the input and Start/Send/Stop controls, so the chat reads as one continuous thread. The dedicated side panel is now **Session** (`BFACW_PT_chat_session`, ordered after the chat panel), holding the context-usage bar, the memory note (View / Edit), **Compact Now**, and the checkpoint list with **Restore** / **Branch**. The old `BFACW_PT_chat_history` panel is removed.
- **Soft co-work scene lock (scene safety Phase 1)** — while a turn runs, the objects and collections the coworker created or touched are temporarily made un-selectable in the UI (`hide_select`) so the user cannot re-target them mid-turn; programmatic access is unaffected and everything is restored when the turn ends (FINISH, error, Stop, or exception). New `co_work_guard.py` (bpy-free, unit-tested), a **Lock Scene While Working** preference (default on), and a `lock_scene_while_working` config field.
- **Scene preflight checks (Phases 2–3)** — the code validator now flags `bpy.ops.object.join`/`join_shapes` without a preceding selection, active-object operators (`modifier_apply`/`modifier_remove`/`shade_smooth`/`shade_flat`/`convert`/`origin_set`/`make_single_user`) with no active object set, and literal indexing into Blender collection roots (`selected_objects`, `bpy.data.*`, `scene.objects`, `view_layer.objects`) when unguarded — each with an actionable fix, anchored to known roots to avoid false positives.
- **Operator-aware error hints & collapsing (Phase 4)** — `poll() failed, context is incorrect` is collapsed to an **operator-specific** one-line fix (join / modifier_apply / mode_set / …) with a generic `temp_override` fallback; `IndexError: list index out of range` is collapsed to a guard-your-collection hint; both are wired into tool-result storage, the spiral corrective message, and the runtime traceback HINT block.
- **Prompt guidance** — the compact and full system prompts and `skills/best_practices.md` now state that *the selection is not yours* (the user edits the same scene, so re-fetch by name and set selection/active object explicitly in the same script) and describe the temporary co-work lock.
- **Benchmark harness** — the fake turn-loop server supports scriptable per-request HTTP error injection, and new `TestBenchmarkErrorSurvival` tests prove a multi-turn run survives injected 400/500 errors (context-overflow recovery and streaming fallback). A repeatable `make benchmark` target runs the offline suite `BENCH_RUNS` times; `tests/test_llm_transport_errors.py` covers the transport error paths directly.
- Mode-aware tool-iteration budget (12 local / 8 remote) instead of a single hard cap.

## [Unreleased - v1.1.37]

### Fixed

- **Turn-loop verification pass (Tier 3 follow-up)** — A new integration harness (`tests/test_turn_loop_integration.py`) runs a *real* conversation turn through `run_conversation_turn` against a fake llama-server HTTP endpoint and proved the Tier-3 wiring behaviourally, exposing three real defects that are now fixed: (1) `llm_transport.py` could not even be imported outside Blender (missing `Callable` import) and raised `NameError` on its first real request (`_CHAT_SAMPLING` / `_DEFAULT_TEMPERATURE_*` / `_DEFAULT_MAX_TOKENS` / `_STREAM_TIMEOUT` were left behind in `agent_controller.py` by the #77 transport split — the constants now live in `llm_transport` and are re-exported by `agent_controller`); (2) the session-memory writer passed `tools=None` to the transport, whose `len(tools)` crashed with `TypeError` that compaction's broad `except` silently swallowed into the heuristic fallback — so the LLM-written memory note never worked in production; (3) the memory-block injection mutated the *stored* system message in place (it aliases the send copy), accumulating the block in the history and doubling it on every subsequent request — the injection now copies before mutating and is marker-guarded. The harness also pins the full firing sequence in order: prompt preflight shapes the request → ~60% compaction trigger fires with a real memory-writer LLM call → `role:"reasoning"` entries are pruned from *stored* history once they leave the verbatim window (not only dropped at send time) → an automatic `reason="compaction"` checkpoint is recorded → the memory block is injected into the following request exactly once without re-compacting. The harness now also drives full **AGENT tool-loop turns** end to end: the fake server doubles as the MCP JSON-RPC bridge so the real `_call_mcp_tool_sync` HTTP path executes every `execute_blender_code` call, and the tests prove preflight runs on every request including mid-tool-loop, ~60% compaction re-firing across a multi-iteration turn without corrupting the in-flight assistant(tool_calls)/tool pair sequence, the memory block injected exactly once per tool request and never accumulating in stored history, reasoning entries pruned from storage once out of the verbatim window, and the friendly "conversation no longer fits the local context window" error firing only when even the pinned current request cannot fit (no LLM request sent). The harness also closes the last two proof gaps: the entity-diff context injection branch is now exercised end to end (snapshot-aware bridge stub + a live stub scene make the step diffs non-empty) and proven to store the warning exactly once and never duplicate it per request or accumulate it in stored turns; and the memory-writer's LLM note is asserted to land verbatim in the stored memory block and in the next request's system prompt (exactly once), not merely to fire. Suite: 261 green.

### Added

- **Local Session Memory & Context Checkpoints (Tier 3)** — The agent now remembers the conversation across compactions instead of silently forgetting it. Prompt budgeting counts *everything* in the request (message history, tool schema, screenshots, skills) against the context size the server **actually** applied (queried live from llama-server `/props`), replacing the old configured-size guess; a final pre-flight before every POST re-trims the history and, if even the pinned turn cannot fit, shows a friendly "conversation no longer fits — compacting" error instead of a raw server 400. The silent 8192→32768 context auto-bump at server start is gone: the configured size is applied verbatim, small contexts get a startup warning, and Preferences now warns (never restarts) when the chosen context cannot fit the detected KV-cache memory. New **session memory engine** (`session_memory.py`): when the prompt approaches ~60% of the safe budget, old turns are retired into a disk archive (`archive.jsonl`), a compact structured memory note (Goal / Decisions / Objects & files / Pending / Errors) is rewritten in a dedicated small LLM call (heuristic fallback) and injected into the system prompt, and an automatic checkpoint snapshots the session. The chat panel's new **Session** section shows a live context-usage bar, a memory preview/editor, and the checkpoint list with **Restore** (non-destructive — current state is saved first) and **Branch** actions, plus a manual **Compact Now** button. Qwen-family presets now carry guarded llama-server flags (`--no-context-shift`); GPU launches gain `--flash-attn`, optional q8_0 KV-cache quantization (new preference, off by default), batch-size knobs, and `--cache-reuse` — all filtered against the pinned server build so an unknown flag can never crash startup. `poll() failed, context is incorrect` tracebacks are collapsed to a one-line corrective hint before storage. New tests: `tests/test_context_budget.py` (18), `tests/test_session_memory.py` (26), and `tests/test_llm_manager.py` extensions (validate_ctx_against_hardware, /props parsing, flag guard) — full suite 251 green.

- **Reasoning Effort presets** — The numeric Thinking Budget row is now a friendlier Reasoning Effort toggle (Off / Low / Medium / High / Custom) sitting next to the Context Window row. Each level maps to a per-reply token budget (0 / 512 / 1024 / 2048) sent to llama-server as `thinking_budget_tokens`; per-button tooltips show the exact count, and Custom reveals the precise slider. The value stays wired through `LLMConfig` end-to-end (prefs sync, `set_config`/`get_config`) and is sent on the local path only — never to remote APIs.
- **Asset Tool Self-Tests in the Diagnostics UI (Tier 3d Phase B)** — Preferences → Advanced → Diagnostics now has an "Asset Tool Self-Tests (no LLM)" box: one click runs every asset tool deterministically in-session against a throwaway fixture library (no MCP server, no agent, no LLM) and shows per-step PASS/FAIL with timings, updating live. Covers `get_asset_libraries`, `search_assets` (name + tag), `get_asset_tags` (editor type), `load_asset_in_context` (material onto explicit object), `place_asset_in_scene` (position check), `wire_node_group` (add_top_level + connect_to_output), and `get_node_group_interface` — all via the exact same toolcode the MCP layer runs (include-expanded, vendored path). Steps that genuinely need a live editor/UI (opening the Asset Browser, visual load verification, a render smoke test) are listed as a manual checklist inside the same box; cleanup removes only fixture-owned datablocks and never touches your scene.

- **Asset Metadata Index (Tier 3d Phase C)** — `search_assets`, `get_asset_tags` and `load_asset_in_context` now answer from an on-disk metadata index instead of appending datablocks into the live session. The index (stored under the user cache in `bfa_coworker/asset_index/`, never inside library folders) captures the full Asset Details region — tags, description, author, copyright, license, catalog, color tag, and the asset's self-declared `preferred_import_method` — plus per-type facts (node count, node-group socket interface, material blend method, vertex counts, action frame range). Entries are fingerprinted per `.blend` by mtime+size; a stale or missing index is rebuilt lazily by a disposable `--background --factory-startup` subprocess (deduplicated by a 60 s marker TTL), so your session is never polluted and read-only/network libraries are never written to. `get_asset_tags` returns the full metadata with zero loading, `search_assets` matches name/tag/description from the index, and `import_method="auto"` now honors the asset's declared method even when the asset has never been loaded. Live append inspection remains as the documented fallback when no index can be built.

- **Blender 5.3 Compatibility (Tier 3d Phase A)** — The live headless run against the Bforartists 5.3 dev build surfaced six real bugs, all fixed: `NodeTree.inputs/outputs` were removed in 5.x (`get_asset_tags` reads the socket interface instead of crashing); `bpy.context.active_object` no longer exists in 5.x (object resolution falls back to the view layer); fresh `GeometryNodeTree`s have no nodes and an empty interface (`wire_node_group` `connect_to_output` now creates the Group Output node and the `Geometry` interface socket — `in_out="OUTPUT"`, tree-relative — then links end to end); `get_node_group_interface`'s class-name filter was too strict for 5.x `NodeTreeInterfaceSocket*` subclasses; `connect_to_output` now replaces an occupied output link deterministically instead of reporting "already in use"; and `NodeTree.type` enum normalization (see below). Also: `tests/mcp_client` on Windows cannot `select()` on pipes (WinError 10038) — responses now arrive through a reader thread + queue; and the preflight validator skips repository-controlled toolcode via a `# blmcp-toolcode-skip-preflight` marker (the tools' own generated scripts were previously rejected wholesale).

- **Headless Asset Integration Tests (Tier 3d Phase A)** — New `tests/integration/test_asset_browser.py`: end-to-end asset workflow against real Bforartists in pure `--background` mode (no agent, no LLM, no display), gated on `BLENDER_BIN`. Builds/installs the addon into an isolated HOME, launches `--command bfa_coworker`, then round-trips `get_asset_libraries`, `list_asset_catalogs`, `search_assets` (name + tag), `get_asset_tags` (editor type), `load_asset_in_context` onto an explicit object, `place_asset_in_scene` with a transform check, `wire_node_group` (add_top_level + connect_to_output), `get_node_group_interface`, and the error paths (unknown asset, missing tree, background-mode jump graceful failure).

- **Explicit-Target + Import-Method Params** — `load_asset_in_context` gained `object_name` (explicit MATERIAL / Geometry-Nodes / ACTION target), `tree_name` (explicit shader compositor target), and `import_method`; `place_asset_in_scene` gained `import_method` (`auto`/`append`/`link`/`pack`, default `auto` = honour the asset's `preferred_import_method` when metadata is available, else fall back to `link_mode`). All backward-compatible (defaults preserve previous behavior) and enabling context-free use in background/headless flows.

- **NodeTree.type Normalization** — Real Blender reports `NodeTree.type` as `SHADER`/`COMPOSITING`/`GEOMETRY`, not the friendly `ShaderNodeTree`/`CompositorNodeTree`/`GeometryNodeTree`. All five affected toolcodes (`get_asset_tags`, `get_node_group_interface`, `get_active_node_tree`, `load_asset_in_context`, `wire_node_group`) now normalize both directions, so editor-type detection and explicit-`node_tree_name` matching work against real Blender. Regression-tested with realistic enum values in the bpy stub (a latent bug all Phase 2B tooling shared).

- **Asset Tool Unit Tests (Tier 3d Phase 5)** — New `tests/test_asset_tools.py` with 43 unit tests for the asset + node-group toolcodes, run via a synthetic `bpy` stub (no Blender needed): `search_assets` name/tag/description matching and type/library filters; `load_asset_in_context` across all six asset types with append-vs-link, positioning, material-slot handling, and editor-aware node groups; `wire_node_group` all four insert modes, undo push, and deterministic socket auto-mapping (exact name → fuzzy → compatible type, incl. type validation and `auto_map=False`); `get_node_group_interface` (new + legacy API); `get_active_node_tree` resolution and serialization. Also added the missing asset-tool args to `tool_smoke_test.py` (`list_asset_catalogs`, `search_assets`, `get_asset_tags`, `load_asset_in_context`, `assign_material_to_objects`, `place_asset_in_scene`).

- **Asset-First System Prompt (Tier 3d Phase 4)** — The "Asset-First Workflow" section in `prompts.yml` was rewritten to bias the agent toward using the MCP asset tools: a decision tree (search `get_asset_libraries`/`list_asset_catalogs`/`search_assets`/`get_asset_tags` before creating anything, then Poly Haven, then from-scratch), link-vs-append-vs-instance guidance, contextual node-group wiring guidance (`get_node_group_interface`/`get_active_node_tree`/`wire_node_group`), and object/collection placement via `place_asset_in_scene`/`jump_to_asset_browser`.

- **Node-Group Intelligence Tools (Tier 3d Phase 2B)** — Three new MCP tools that make node-group assets actually usable:
  - `get_node_group_interface` — reads a loaded node group's interface (editor type + every input/output socket with type, default, min/max, description), giving the agent the group's wiring manual.
  - `get_active_node_tree` — serializes the resolved node tree (active material / GN modifier / compositor, or an explicit `bpy.data.node_groups` name) with nodes, sockets, links, and frames.
  - `wire_node_group` — loads a node-group asset and splices it **into** a target tree with validated, undo-able links. Insert modes: `add_top_level`, `replace_active` (wrap a node), `insert_between` (splice into a link), `connect_to_output` (attach to Material Output/Composite/Group Output). Socket matching is deterministic (exact name → fuzzy → first compatible-type) and unmappable sockets are reported instead of silently failing; `bpy.ops.ed.undo_push` precedes every mutation. Registered in the `assets` and `geometry_nodes` domains, documented in the asset browser skill (with asset-author socket-naming conventions: `Scale`, `Seed`, `Strength`, `Color`).

- **Markdown Rendering in Chat** -- Assistant messages now render with code blocks (with syntax-highlighted headers and Copy buttons), tables, headings (H1-H4), bold/italic, unordered/ordered lists, blockquotes, and inline code. Ported from Blender Buddy reference implementation.
- **LaTeX → Plain Text Conversion** — The chat renderer now converts stray `$$…$$` blocks, `\frac{a}{b}`, `\sqrt{}`, and common LaTeX symbols (`\cdot`, `\times`, Greek letters, etc.) into readable ASCII/Unicode equivalents, since Blender UI labels can't render math.
- **Preflight Code Validation** — 27 regex-based checks that catch common LLM mistakes *before* execution: missing `import bpy`, wrong subdivision attribute (`subdivisions` → `levels`), wrong Principled BSDF attribute access, wrong torus keywords, sequencer `sequences` → `strips`, removed `use_auto_smooth`, removed `action.fcurves`, `bpy.ops` in loops, no output/print, `bpy.data.lamps` → `lights`, `'EEVEE'` → `'BLENDER_EEVEE'`, `render.eevee` → `scene.eevee`, wrong BSDF input names, `.active` on data collections, object creation in loops without existence checks, `mode_set` enum validation, hallucinated module imports, wrong material hierarchy, wrong world/environment node types, MCP tools called as Python functions, `world["Use Nodes"]`, transform kwargs on primitive operators, missing `bmesh` import, bmesh edit-mode mismatch, vector arithmetic type errors, and `update_edit_mesh()` argument count. Each check returns targeted guidance so the LLM can fix the code instead of retrying blindly.
- **Blender Code Template System** — 18 pre-tested Blender 5.3 templates (`create_torus`, `create_cube`, `create_uv_sphere`, `create_cylinder`, `create_plane`, `add_material`, `smooth_shade`, `auto_smooth`, `add_subsurf`, `add_array`, `add_bevel`, `add_solidify`, `add_smooth`, `add_remesh`, `set_render_engine`, `setup_camera`, `keyframe_location`, `keyframe_rotation`) with parameter defaults. Two new MCP tools: `execute_blender_plan` (two-phase: plan → tested code) and `list_blender_templates` (discover templates + defaults). Templates are pre-validated for Blender 5.3 and auto-correct common API pitfalls.
- **Auto-Correction Module** — New `autofix.py` silently rewrites common LLM mistakes before execution: `bpy.data.lamp` → `bpy.data.lights`, `"EEVEE"` → `"BLENDER_EEVEE"`, `render.eevee` → `scene.eevee`, `use_auto_smooth` → `auto_smooth_angle`, `action.fcurves` → `keyframe_insert`, `ShaderNodeEnvironment` → `ShaderNodeTexEnvironment`, `ShaderNodeWorldOutput` → `ShaderNodeOutputWorld`, `["Use Nodes"]` → `.use_nodes = True`, `subdivisions` → `levels`, `base_color` → `inputs['Base Color'].default_value`, `update_edit_mesh(a, b)` → `update_edit_mesh()`, and `data.material_slots` → `material_slots`.
- **llama-server Management** — New Remove and Open Folder operators (`bfacw.remove_llama_server`, `bfacw.open_llama_server_folder`). Preferences now show the resolved llama-server path with an Open Folder button, plus a Bundled/Custom source toggle: "Bundled" is addon-managed (Download/Update/Remove buttons shown), "Custom" uses your own llama.cpp build that the addon never modifies.
- **llama-server Binary Hardening** — `find_llama_server()` now prefers the bundled backend-specific binary (`llama-server-cuda.exe` etc.) over PATH, validates the build number against a minimum supported version (warns when too old for Qwen3 SSM models), and falls back through PATH and known install directories. CUDA runtime DLLs (cudart) are extracted alongside the binary so llama-server actually finds them.
- **GGUF Header Parser** — `_gguf_layer_count()` reads `block_count` from GGUF file headers (v3 format, uint32/uint64) to auto-size GPU layer counts. Unit-tested against synthetic Qwen3 and Gemma3 headers.
- **CUDA/Vulkan Auto-Detection Hardening** — Backend detection now checks nvidia-smi for NVIDIA, wmic for AMD/Intel, and falls back to CPU. DLL companion extraction handles subdirectory prefixes and case-insensitive matches.
- **Download Safety Guards** — SHA-256 verification for model downloads. HTTP Range resume via .part files (interrupted downloads resume where they left off). Atomic rename (.part → final) prevents corrupt partial files. Cancel preserves .part for resume. Network errors preserve .part instead of deleting. HTTP 416 handling for already-complete downloads.
- **GPU Auto-Detection** — Automatic `--n-gpu-layers` calculation based on GPU VRAM, model size, KV cache requirements, and runtime overhead. Eliminates OOM crashes from hardcoded values. Falls back gracefully when GPU detection fails.
- **Inference Sampling Overhaul** — Temperature auto-switches (0.2 for Agent/code, 0.35 for Ask/prose). Tuned sampling: top_k=20, top_p=0.8, repeat_penalty=1.1. Default max_tokens lowered to 1024 for more efficient tool rounds. Default context raised to 16384.
- **Custom Model URL Flow** — Paste any HuggingFace URL or direct .gguf link to download. URL auto-parsed for repo/filename. Reuses existing download infrastructure with SHA-256 verification.
- **Server Port Fallback** — Automatic port selection when configured port is busy. Scans upward from configured port, clear error when all ports exhausted.
- **Spiral Detection Hardening** — Error-loop detection threshold lowered from 3 to 2 consecutive identical errors. Corrective messages now include targeted API guidance (e.g. Principled BSDF `inputs` dictionary, subdivision modifier attributes, "no output" diagnosis) so the LLM fixes the code instead of retrying it verbatim.
- **Thinking Budget for Local Models** — New "Thinking Budget" preference (default 1024) caps
  chain-of-thought reasoning tokens per API call via llama-server's `thinking_budget_tokens`
  parameter. Prevents reasoning from eating the entire `max_tokens` budget, which caused
  tool calls to be truncated mid-generation. Set to 0 to disable.

- **Compact System Prompt for Local Models** — Auto-detects local LLM and loads a compact
  system prompt (2.4K chars vs 14K) that keeps essential rules but removes verbose reference
  material. Says "Be concise and decisive" instead of "Think aloud in full paragraphs."
  Saves ~3K input tokens per turn.

- **Bundled Blender API Docs Always Available** — `get_python_api_docs`, `search_api_docs`, and `search_manual_docs` are now always loaded as surface tools, so the agent can look up correct APIs on error without needing to load a domain first.
- **Mode Switch Lock** — Operating mode (Local/Remote/Harness) and GPU backend can no longer be changed while the agent is running; the selector is disabled with a "Stop the agent first" hint, preventing mid-flight MCP server kills.
- **Chat UI Polish** — Multiline text wrapping with constrained width, enhanced markdown heading visual hierarchy (keyframe dot icons per level), loading icon shown only on the active item, consistent open-folder icons, and fixed separator rendering.
- **Debug Mode & Diagnostics** — New user-facing `debug_mode` toggle and `log_level` enum (DEBUG/INFO/WARNING/ERROR) in preferences. Open Log button for quick access to log file. Multiline custom skills text editor for easier editing.
- **Benchmark Expansion** — Timing measurements for all benchmark suites. 6 new editor benchmark suites. Split assets_materials into separate tests. Auto-reset on completion. Results persistence to JSON files with comparison support. The Modifiers suite was rewritten as a torus modifier-chain workflow (Array → Bevel → Remesh → Solidify → Smooth → Subdivision Surface building a mechanical part).
- **Session Logging & Memory Bank** — Export session log to text block or clipboard. Auto-save on spiral detection. Error code bank for pattern tracking. Versioned session history (last 10).
- **`place_asset_in_scene` Tool** - Place COLLECTION or OBJECT assets at an explicit world position/rotation/scale (rotation in degrees). Defaults to `APPEND` (full copy, positioned directly; collections are anchored by their centroid, with rotation/scale applied around it). `LINK` for collections creates an empty + collection instance for shared references.
- **`jump_to_asset_browser` Tool** - Switch to (or create) the Asset Browser editor. Reuses an open Asset Browser, otherwise duplicates the current workspace (user layout preserved) and converts its main area; optionally preselects a library and catalog (best-effort).
- **Tier 3d Plan Update** - `_misc/plan_tier3d_asset_browser_intelligence.md` gained Phase 2B (node-group intelligence: `get_active_node_tree`, `get_node_group_interface`, `wire_node_group` with insert modes, interface auto-mapping and undo) and the Phase 1 domain name was corrected to `assets` to match the implementation.
- **Asset Browser Tools** — 3 new MCP tools: `get_asset_libraries`, `search_assets`, `load_asset_in_context`. Type-aware loading for materials, node groups, collections, objects, worlds, actions. `get_asset_tags` for reading node group editor type (Geometry Nodes, Shader, Compositor). Asset browser domain registration with skills documentation.
- **Start/Stop UX Hardening** — Graceful shutdown with timeout. Health dots (Bridge/MCP/LLM liveness indicators). Restart button. Stop-during-thinking guard with user feedback.
- **Thinking Indicator Polish** — Unicode spinner animation for thinking state. Model loading progress bar with percentage. Timer optimization for UI updates.
- **Conditional Advanced Settings** — Per-mode section gating in Advanced tab. Mode hint label showing current operating mode context.
- **Collection Color Tag Tool** — `set_collection_color_tag` MCP tool. Color tag in scene summary. Skills documentation. Readonly detection.
- **Message Queue** — Full message queue system with `MessageQueue` dataclass. Auto-queue when turn is active. Auto-dequeue after turn completes. Queue UI with Show/Clear buttons. Popup display of queued messages.
- **Mention System Overhaul** — Multi-category support (objects, materials, collections, node groups, worlds, actions). Category filter buttons in popup. Auto-detect filter from @typing in input. Smart partial mention replacement.
- **Chat Panel Modularization** — Split monolithic panel into main panel (input + messages) and status sub-panel (health, diagnostics). Status panel defaults to closed as advanced sub-panel.
- **External Harness Preset System** — 8 curated MCP client presets (Claude Desktop, Claude Code, Codex CLI, Cursor, Windsurf, Cline, OpenCode, Generic STDIO) with inline setup steps, config file locations, and documentation links. Select your harness from a dropdown in Advanced preferences and get a ready-to-paste config.
- **Blender's Python in Harness Configs** — Harness configs now emit the full path to Blender's bundled Python with PYTHONPATH set to vendor dependencies. No pip install needed. A "Use System Python" toggle is available for power users.
- **"Configure Harness" Button** — In harness mode, the chat panel now has a "Configure Harness" button that opens preferences directly to the step-by-step harness setup wizard. A quick-copy dropdown is also available for power users.
- **Developer Documentation** — `_misc/harness_testing_guide.md` covers manual STDIO testing, bridge verification, and how to add new presets.
- **User Troubleshooting Guide** — `_misc/harness_troubleshooting.md` covers per-harness common issues, config file locations, and a quick checklist.

### Fixed

- **Oversized Tool Calls Wasted the Whole Retry Budget** — A benchmark step that asked for a scene full of scattered props failed after 248 s. The model tried to emit the entire scatter script as **one** `execute_blender_code` call, ran out of output tokens mid-string, and llama-server could not parse the arguments as JSON (`500 ... invalid string: missing closing quote`). Four separate defects turned that into a dead end:
  1. **The nudge asked for the same call again.** The `_FAULT_TOOLCALL` recovery told the model to "emit the tool call again with complete, well-formed JSON arguments" — but the call was too large to fit in one response, so re-emitting it truncated in exactly the same place. The nudge now names the real cause and tells the model to **split the work into smaller calls** (roughly 40 lines or fewer per script).
  2. **The identical payload was re-sent four more times.** A malformed tool call is a *generation* failure, not a transient one, but the generic retry path re-sent the same nudged payload up to 4 more times (~60 s each) and failed identically. Once the one-shot nudge has been used, the retry path now surfaces the error immediately instead of burning the budget.
  3. **The auto-continue path sent the raw history.** `finish_reason=length` recovery called `_openai_chat_completions` with the live `history`, which still carries `ui_only` greeting entries and non-standard `reasoning` messages — the exact shapes a strict Jinja template rejects with `400 Unexpected message role`. It now strips both and sanitizes roles, matching the main request path.
  4. **The continuation dropped the thinking budget.** The auto-continue call omitted `thinking_budget_tokens`, so the continuation could spend the entire `max_tokens` on chain-of-thought and leave nothing for the tool call — truncating it again, the very failure the path exists to recover from. It now forwards the budget, and the bare `"Continue."` prompt was replaced with one that caps the size of the next step.

  The surfaced error message and both system prompts (`prompts.yml`, `prompts_compact.yml`) now also tell the model to keep tool calls small and split large jobs across several calls.

- **Malformed Tool Calls Surfaced as an Opaque HTTP 500** — When the model emitted a tool call whose arguments were not valid JSON (typically a string left unterminated because generation was cut off), llama-server returned a bare `500 Failed to parse tool call arguments as JSON: ... invalid string: missing closing quote`. The existing classifier saw the word "template" in that message and misread it as a chat-template fault, which would have downgraded the session to text-based tool calling — a change that cannot possibly help, since the request was valid and the *model's output* was malformed. New `_FAULT_TOOLCALL` class (checked before the generic template markers, after the server markers) names the real cause, retries **once** with an explicit "emit complete, well-formed JSON arguments" nudge, and — if it still fails — reports a message that says this is a model generation failure and suggests lowering Reasoning Effort or raising the Context Window. The llama-server log cross-check is now skipped for specific matches, so a stale OOM in the log tail cannot override a clear tool-call diagnosis.

- **Tool Results Were Truncated in the Chat Panel** — Tool results were capped at 500 characters *when stored* into `conversation_history`, and the chat panel renders from that history — so a scene dump, a file listing, or a long error trace was permanently cut to a stub the user could never expand. The full result is now stored, and trimming moved to request-build time via new `_trim_history_tool_results()` (cap raised to 2000 chars), so the panel shows everything while the model's context stays bounded. The live streaming preview, the tool "Details" section, and the inline tool result are no longer chopped either.

- **Preflight False Positive on Comma-Separated Imports** — The `missing_bmesh_import` and `missing_bpy` checks tested `'import bmesh' not in code`, a plain substring match that misses `import bpy, bmesh, math` — valid code the model then had to waste a retry on. New `_imports_module()` helper matches `import a, b, c`, `import x as y`, and `from x import ...` via regex, while still rejecting lookalikes such as `import bmesh_utils`.

- **External Harness MCP Startup Failures Were Undiagnosable** — A user in External Harness mode hit `MCP server exited immediately: Traceback ... File "<frozen runpy>", line 189, in _run_module_as_main` with no indication of the cause. Three separate defects combined to hide it:
  1. **The exception was truncated away.** `start_mcp_server()` reported `error_detail[:200]` — the *first* 200 characters of a traceback, i.e. the `Traceback (most recent call last)` header and the `runpy` frames — cutting off the actual exception on the last line. New `_summarize_mcp_failure()` extracts the **last non-empty line** (the exception) and keeps the untruncated text in `_agent_state.error_full` for copy-to-clipboard.
  2. **The NETWORK path reported nothing at all.** `start_mcp_server_network()` discarded the pipe-drainer output entirely and set a bare `"MCP server exited immediately"`. It now collects stderr/stdout, logs the tail, and reports the exception line — matching the managed path.
  3. **`vendor/blmcp/` was never checked.** `_ensure_vendor_deps()` only verified `deps/mcp/__init__.py`, so a source install with deps but no `vendor/blmcp/` passed the check and failed later as an opaque traceback. New `_check_vendor_layout()` validates `vendor/blmcp/__init__.py` and `data/prompts.yml` too, and `_copy_dev_blmcp_into_vendor()` copies `<repo>/mcp/blmcp` across when running from a source checkout (`blmcp` is source, not a wheel, so pip cannot supply it).

  Also added `_classify_mcp_failure()`, which maps known signatures to an actionable hint appended to the error message. The traceback shape itself is diagnostic: a genuinely missing module makes Python print a one-line `No module named blmcp` with **no traceback**, so the reported `runpy` frames mean `blmcp` *was* found and the failure is *inside* the package — most likely **mcp 2.x**, which removed `FastMCP` while `blmcp` imports `from mcp.server.fastmcp import FastMCP`. That signature (plus `pydantic_core`/`pywintypes` native-extension mismatches, `Address already in use`, `PermissionError`, and bad interpreter paths) now produces a specific fix instead of a bare traceback.

- **Harness Configs Are Now Preflighted Before You Paste Them** — The add-on previously handed out MCP client configs it had never executed, so a wrong interpreter path or a missing `PYTHONPATH` entry only surfaced as a traceback inside the user's MCP client. New `validate_mcp_client_config()` resolves the same interpreter/PYTHONPATH the config generator uses, checks the interpreter exists, checks every `PYTHONPATH` entry exists, then actually runs `python -m blmcp --help` with the generated environment to prove the import chain (`blmcp` → `mcp.server.fastmcp` → `yaml` → `data/prompts.yml`). It reports `python_ok`, `missing`, `import_ok`, `bridge_ok`, `stderr_tail`, and an actionable `hint`. Surfaced as a **Test Config** button beside *Copy to Clipboard* (with inline result and server-output tail), a **Test Harness Config** button in the Diagnostics panel, and a **Config** row in *Check Status*. `Copy to Clipboard` now runs the check and warns when the config would fail — it still copies, since power users may target a different machine.

- **Vendor Layout Warnings** — `_find_vendor_pythonpath()` silently omitted directories that did not exist, so a broken layout produced an empty `PYTHONPATH` with no warning. New `_vendor_pythonpath_report()` returns the missing entries alongside the path. `_vendor_native_compat()` now uses `rglob` instead of `glob`, so native extensions in subdirectories (`pywin32_system32/`, `win32/lib/`) are checked for Python-version mismatches instead of being missed.

- **`400 Unexpected message role` — the Flatten Fallback Preserved the Offending Role** — The chat-template fallback for llama-server's `400 Unable to generate parser for this template ... Unexpected message role` flattened the conversation but copied every non-`tool` role through verbatim — including `reasoning`, a **non-standard role** used for UI-only chain-of-thought. The model's Jinja template has branches for `system`/`user`/`assistant`/`tool` only, so `reasoning` fell through to the `else` branch and raised the very error the fallback was meant to rescue. The retry therefore failed identically to the original request, and the one-shot guard correctly reported "persists after flattening". `_flatten_for_plain_chat()` now **drops** `reasoning` entries, maps any other unrecognised role to `user` (preserving its content), drops assistant turns left blank after `tool_calls` are stripped, and strips the `ui_only` marker — guaranteeing the output contains only `system`/`user`/`assistant`. Reproduced on both QWEN3.8 and GPT-OSS, so this was not model-specific.

- **Hallucinated Tasks — the Prompt Budget Trimmed Away the User's Message** — With `local_max_tokens` (16384) equal to `local_ctx_size` (16384), the prompt budget computed to **-512** and fell back to a floor of `ctx_size // 4` = 4096 tokens. That floor cannot hold the system prompt (~4870 tokens), so `_fit_history_to_budget()` trimmed the conversation down to **the system prompt alone** — the log showed `messages = 1, roles = system, first user = (none)`. With no user turn to answer, the model invented one, which is why the reported "hallucinated task" varied between runs (a temple, a cabin, a living room). Two fixes: `max_tokens` is now clamped to at most half the context window so the prompt always has room, and `_fit_history_to_budget()` **pins the last user message** — only history *before* it may be trimmed. A prompt with no user turn is never useful, so the budget is allowed to overflow rather than produce one. Both corrections are silent: the configuration "just works" instead of surfacing a warning the user cannot act on mid-run.

- **Persisted History Is Now Sanitized on Load** — A chat history restored from disk was assigned to the live conversation verbatim, so a history written by an older build could reintroduce `ui_only` greeting entries, a leading assistant message, `reasoning` entries, or half-finished tool-call exchanges — any of which trips a strict Jinja template. New `_sanitize_loaded_history()` drops all four and also removes a trailing unanswered `user` message (an interrupted turn, which would otherwise create a `user, user` run once the new request is appended).

- **Startup Greeting Polluted the Conversation History** — The "Ok, now we are ready! How can I help?" greeting was appended straight to `conversation_history` as a plain assistant message. Because the system prompt is only inserted when `history[0]` is not already a system message, the result was `[system, assistant(greeting), user, ...]` — a phantom assistant turn ahead of the user's first request. The model answered the *greeting* instead of the actual prompt, and the extra leading assistant message also tripped strict Jinja chat templates. The greeting is now marked `ui_only`: the chat panel still renders it (the UX is unchanged), but new `_strip_ui_only_from_history()` removes it — and the marker itself — before every LLM request. A defensive guard in `_run_conversation_turn_inner` also drops any leading non-`ui_only` assistant message, repairing sessions already running in the polluted shape.

- **Benchmark Runs Destroyed Their Own Error Evidence** — The step runner cleared `_agent_state.error` at the start of each step, which wiped the failure reason *before* the session log exported it — so a failed run showed an empty `--- Error Info ---` section, hiding exactly what went wrong. The runner now snapshots the error before the call and only attributes a failure when the message actually changed, so a stale error from an earlier step is not misreported as this step's.

- **Benchmark Steps Reported Failures as Successes** — A failed benchmark step was indistinguishable from a successful one in the Diagnostics panel: both showed only a timing, so a suite that failed instantly looked like it had simply run quickly. The cause was that `run_conversation_turn()` does **not** raise on an LLM failure — it returns normally and records the reason in `_agent_state.error` — while the step runner only caught exceptions. Every failed step was therefore recorded as a success, and the error text went to `_agent_state.error`, which the Diagnostics panel never displayed. New `_record_step_outcome()` / `_suite_failure_count()` track a per-step `ok`/`failed` status and message; the panel now shows a red `CANCEL` icon on failed steps, the failure reason inline, and an "N failed" badge on the suite header. Reset clears a suite's recorded outcomes so a re-run starts clean.

- **`400 Unexpected message role` Fallback Could Never Converge** — The chat-template fallback for llama-server's `400 Unable to generate parser for this template ... Unexpected message role` set `body["messages"]` but never reassigned `messages`, so every retry re-flattened the *same original* list and failed identically — burning the entire retry budget on a payload that could not succeed. It now reassigns `messages` so retries converge, and logs the flattened role sequence (`system,user,assistant,...`) so the offending shape is visible. All three request-reshaping fallbacks (flatten, role-sanitize, tools-as-text) gained one-shot guards: a second identical failure now surfaces the real error instead of looping.

- **`_flatten_for_plain_chat()` Still Emitted Rejected Shapes** — Two gaps let the flattened output trip the same `Unexpected message role` error it was meant to avoid: the same-role merge required both contents to be `str`, so a multimodal (list) content block left a `user, user` run; and `system` was excluded from merging, so a second system message (e.g. injected tool text) sat mid-conversation. Merging now normalises content via the new `_content_as_text()` helper and includes `system`, so the output always alternates roles.

- **Compact System Prompt Never Loaded (#62)** - The local-model prompt auto-detection read
  `LLMConfig.local_llm_port`, a field that does not exist (the real field is `local_port`). The
  resulting `AttributeError` was swallowed by a bare `except`, so the compact prompt silently never
  loaded and every local model got the full 3K-token prompt. Detection now keys off the LLM *mode*
  (`mode == "local"`) — deliberately not `local_port`, which always carries a default of 8081 and
  would therefore have selected the compact prompt even in remote mode. Also fixed the deployed
  path: the candidate list only searched the dev-checkout layout (`mcp/blmcp/data/`), so an
  installed addon fell through to the brief built-in prompt; it now also searches
  `vendor/blmcp/data/` where `prompts_compact.yml` actually ships. The prompt cache is now keyed
  per variant, so switching local <-> remote no longer reuses the other variant's text.

- **Agent Choked by `400 Unexpected message role` After 1-2 Prompts (#62)** - The history slicer
  trimmed the conversation to the last 20 messages, which can cut a tool-call exchange in half.
  The existing guard dropped orphaned `tool` results but not the inverse case — an `assistant`
  message carrying `tool_calls` whose `tool` replies had been sliced away. llama-server's Jinja
  template rejects that shape, returning HTTP 400 and killing the next turn. New
  `_repair_tool_call_pairs()` drops half-finished exchanges in *both* directions (matching only the
  run of `tool` messages directly following a call, so a later unrelated result cannot satisfy an
  earlier call).

- **Prompt Now Bounded by the Context Window, Not a Message Count** - The old guard capped history
  at 20 messages, a blunt instrument: a few large tool results could still overflow a small local
  context window, and the only signal was a `>30000 bytes` warning that took no action. New
  `_message_text_length()` / `_estimate_messages_tokens()` / `_fit_history_to_budget()` estimate
  tokens (~3.5 chars/token, deliberately over-estimating for code so it trims slightly early),
  reserve room for `max_tokens` plus template scaffolding, and drop the oldest exchanges until the
  prompt fits. The system prompt is always preserved. Applied on the local path only, where
  `local_ctx_size` is known; remote providers are untouched.

- **Injected Tool Text No Longer Corrupts the System Prompt** - When a chat template rejected the
  `tools` parameter with HTTP 500, the fallback appended the full tool listing to the last system
  message — mutating the caller's dict in place. Because short histories are sent as-is (no copy),
  `messages` *was* the live conversation history, so the real system prompt stayed polluted with
  tool text for the rest of the session. The fallback now copies the list and the target message.

- **Flattened Conversations Could Emit Consecutive Same-Role Messages** - The plain-chat fallback
  maps `tool` results to `user` messages; combined with the injected "please respond" prompt this
  produced `user, user` runs, which strict templates reject. Consecutive `user`/`assistant`
  messages are now merged. New tests in `tests/test_orchestration_helpers.py` (25 total, green).

- **HTTP 500 No Longer Misread as a Template Problem (#63)** - llama-server returns a bare HTTP 500
  for two very different failures, and the old handler treated every one of them as "the chat
  template rejected `tools`": it re-sent the conversation with the tool listing inlined as text.
  That is correct for a template fault, but it silently downgraded the whole session to
  text-based tool calling (far less reliable on small models) whenever the real cause was a
  resource/hardware fault such as a GPU OOM — and it masked the cause, since the log pointed at
  the template. The 500 path now classifies the response first via `_classify_llm_500()`: server
  markers (OOM, `CUDA error`, `cudaMalloc`, `ggml_assert`, allocation failures) are checked
  *before* the generic template markers, so an OOM message that happens to mention "template" is
  still a server fault. A server fault keeps native tool calling and retries with backoff; if it
  persists, `_server_fault_message()` surfaces the response body, the llama-server log tail, and
  the existing GPU-OOM hint with its context-window advice — instead of a generic
  "LLM request failed". An empty or unrecognised body is treated as a server fault, so an error we
  don't understand can never trigger the downgrade. The downgrade itself is now visible: a
  `warning` on `AgentState` (shown in the chat sidebar and cleared each turn) tells the user that
  text-based tool calling is in use and why. Also fixed a latent bug: the body was read with
  `ex.read()` in up to four separate branches, but `HTTPError` bodies are single-use, so every
  read after the first silently returned empty and the diagnostics for the real cause were lost.
  New tests in `tests/test_orchestration_helpers.py` (34 total, green), including a marker-sync
  guard that parses the real constants out of the source so the test's mirrored lists cannot
  drift.

- **Tier 3h Quality Audit: 3 Critical Bugs** - Two MCP tools advertised to the LLM always failed:
  `execute_blender_plan` and `list_blender_templates` imported `_plan_to_code` / `_render_template` /
  `_TEMPLATES` / `_TEMPLATE_DEFAULTS` from the wrong module (`mcp_to_blender_server` instead of
  `blender_templates`), so the plan tool errored and the template list returned "Template registry
  not available" on every call. Imports now point at `bfa_coworker.blender_templates`, and the dead
  `_generate_plan_code` fallback (which would have raised NameError) was removed in favor of a clean
  error dict. Also wired the 12 auto-fix rules from `autofix.py` into `_execute_code()` BEFORE
  preflight: corrected code (lamps→lights, EEVEE→BLENDER_EEVEE, subdivisions→levels, base_color→
  inputs, ...) now passes validation instead of being rejected, reducing LLM round-trips.

- **`bpy.context.active_object` Sweep** — Replaced all remaining `bpy.context.active_object`
  references in toolcode files with `bpy.context.view_layer.objects.active`, which is available
  in the MCP bridge worker thread. Fixed in: `polyhaven_pbr.py` (generated PBR material code
  used by both `download_polyhaven_asset` and `setup_pbr_material`),
  `assign_material_to_objects_toolcode.py` (fallback when no object names given),
  `three_point_lighting_rig.py` (target fallback in generated code), and
  `get_screenshot_of_window_as_json_toolcode.py` (active object metadata).
  This eliminates the preflight rejection that blocked all Poly Haven texture downloads and

- **Python Version Compatibility Check** — `_get_blender_python_for_config()` and
  `_resolve_mcp_python()` now inspect vendor deps' native extensions (.pyd/.so) and compare
  the cpython tag against the target interpreter's version. When there is a mismatch (e.g.
  Blender 5.3 ships Python 3.13 but vendor deps were compiled for 3.12), the addon
  automatically falls back to a compatible system Python instead of recommending Blender's
  bundled Python, which would fail on `import mcp`. A warning is logged when no compatible
  Python is found.
  PBR material creation from the LLM.

- **Tier 3h Quality Audit: Cleanup & Hardening** - `get_polyhaven_status` now returns a dict
  (consistent with every other tool); the `os.add_dll_directory()` handle is kept in module state
  so the bundled DLL search directory can't be garbage-collected mid-session (Windows DLL_NOT_FOUND
  hardening); `_call_mcp_tool_sync` gained a 120s daemon watchdog so a hung tool call is reported
  instead of blocking the conversation loop forever; dead code removed (`_DEEP_MAX_TOKENS` in
  `agent_controller.py`, stray `_shutting_down` assignments on `LLMState` in `llm_manager.py`).

- **Chat & Messages Render Natively Multi-Line (No More Chopped Rows)** - The sidebar chat
  manually chopped every message at a fixed character width and drew each chunk as a
  full-height `UILayout.label` row, which wasted vertical space and looked like ragged
  line breaks. The renderers (`_draw_multiline` and the markdown paragraph emitter) now use
  the native `UILayout.label_multiline` API from Blender PR #154351 (workshop/ios-workshop
  builds) on hosts that expose it - text wraps to the real layout width with a tight
  0.75 UI_UNIT_Y line height, keeps the markup icons/alignment, and the chat condenses
  vertically. Detection is done once via RNA, and the character-chop renderer remains as a
  fallback on stock builds that lack the API. Live-verified in a real UI session on the
  Bforartists 5.3 dev build (`label_multiline` draws with icon + alignment).

- **Agent Error Loops No Longer Blind or Stuck (Run-Loop Orchestration)** - Two defects made
  repeated-tool-call spirals much worse. (1) Tool-result errors were head-truncated at 500 chars,
  and Python tracebacks keep the actual exception on the LAST line - so the model never saw the
  real error and kept retrying variants of the same broken code. Error results are now trimmed
  to keep the tail (the exception line and the model's own failing source line) within the same
  token budget. (2) The smart-undo engine's internal payloads (`_undo_code` / `_build_cleanup_code`,
  generated by the run loop for undo, undo-push bookmarks, entity snapshots, and failure cleanup)
  lacked the `# blmcp-toolcode-skip-preflight` marker, so they executed in the bridge's worker
  thread where `bpy.context.window` is None - `bpy.ops.ed.undo()`, undo-push, and the snapshot
  all reported "No window/area available", every undo was "FAILED - falling back to entity
  cleanup", and the metadata snapshot never landed so cleanup had no data. They now carry the
  marker and run inline on the main thread, so undo/snapshot/cleanup actually work. Also relaxed
  preflight check #15: it no longer blocks legitimate `primitive_*_add` loops (Blender
  auto-uniquifies names) - it only flags the real duplicate hazard, `bpy.data.objects.new(...)`
  with a static name and no `get()`/unique-name guard - and the spiral-detection message now
  reports the true consecutive-error count. New `tests/test_orchestration_helpers.py` (8 tests)
  plus 5 new preflight tests.

- **Console Stays Clean (No More Blank Lines, No Agent Talk)** - With Debug Mode OFF, the
  Blender console now works like the stock Bforartists console again. Root cause of the stray
  empty lines: `print("[Coworker] ...")` writes two separate chunks - the prefixed message
  (suppressed) and a trailing bare newline (which has no prefix, so it leaked through as a
  blank line). The output tee now remembers the last suppressed addon line and swallows its
  trailing newline chunk too. Also swept the last un-prefixed addon prints: the Agent auto-start
  diagnostics in `__init__.py` now carry the `[🛠️Coworker]` prefix (log-only when debug is OFF),
  with the MCP-server-failure line promoted to `[⚠️Coworker]` so real problems still surface
  on screen. Warnings/errors always pass through; Blender's own messages and other addons are
  untouched. Debug Mode ON still shows everything. New `tests/test_log_suppression.py` (11 tests)
  covers the classifier and the newline-swallowing behavior.

- **Workspace-Tab Tools Now Work Through the Harness** - `jump_to_tab_by_name` /
  `jump_to_tab_by_space_type` (and every other window/context-dependent tool: asset-browser
  jump, viewport jumps, screenshots) returned "No active window" (or crashed) whenever called
  from an MCP client. Root cause: the addon bridge executed *all* code - including trusted,
  repository-controlled toolcode - in a daemon worker thread, and `bpy.context` is only populated
  on Blender's main thread (probe-verified: window=None, screen=None, `active_object` raises
  AttributeError in the worker thread). The bridge now runs toolcode-marked payloads inline in
  the calling thread (the socket is serviced from `bpy.app.timers` on the main thread), while
  LLM-generated code keeps the 30s hang-timeout worker thread. Also hardened the two tab tools:
  workspace lookup tolerates case/whitespace differences, and the success result now includes
  `available_workspaces` so the agent can discover tabs. Live-verified end-to-end through the
  real bridge in a UI session (tab switched Main -> Animation, workspaces listed). New unit tests
  for both tab toolcodes in `tests/test_asset_tools.py` (64 total).

- **Harness Step 3: Chat-Paste Hint** - The external-harness wizard now shows a per-client tip in
  Step 3 (Paste into your client) telling you when you can paste the config directly into the
  client's chat/MCP settings instead of editing a config file: Windsurf (Cascade chat), Claude
  Code (/mcp), Cline (Paste Configuration), Cursor (MCP Servers settings), OpenCode (paste
  into the TUI), Codex (`mcp add`), and a note that Claude Desktop is file-only.

- **Harness CLI Tools Find Bforartists** - `execute_blender_code_for_cli` and friends
  failed with "Blender executable not found at 'blender'" because generated harness
  configs never set `BLENDER_PATH`, so the CLI fell back to a literal `blender` on
  PATH (which does not exist when Blender is installed as `bforartists.exe`). The
  config generator now emits `BLENDER_PATH` pointing at the running binary. Also
  fixed a latent Windows crash: the CLI subprocess decoded output with the locale
  codec (cp1252), which chokes on Bforartists' UTF-8 console output (the addon
  prints emoji) and killed the reader thread - it now decodes UTF-8 with
  replacement. Verified end-to-end against the Bforartists dev build.

- **Copy Error Button in the Chat Sidebar** - When the agent reports an error, the sidebar status line
  now shows a **Copy Error** button that puts the full error text on the clipboard for troubleshooting.
  The sidebar itself keeps showing the compact 500-char preview (raw JSON bodies rendered inline looked
  garbled), while the full untruncated message is preserved on the agent state and copied instead - the
  session-log export also includes the full text now. Also fixed the inverted status icon (it showed a
  warning icon when an error was present).

- **Agent Torus Loop + Template Crash (audit fix)** - Live debugging of the agent log exposed three
  root causes that combined into a 6-turn failure spiral:
  - **`bpy.context.active_object` unavailable in the MCP bridge** - LLM code runs in a worker thread,
    and Blender's context is thread-local there, so `bpy.context.active_object` raises
    `AttributeError` (verified against the dev build). All 18 `blender_templates.py` templates,
    the preflight hints, and the skills examples used that pattern - every template now uses
    `bpy.context.view_layer.objects.active` (which works in-thread), a new preflight check
    (`context_active_object_thread`) teaches the replacement before execution, and the skills
    gained a bridge-thread note.
  - **Preflight false positive on primitive transform kwargs** - check #23 banned
    `location`/`rotation`/`scale` on `primitive_*_add`, but the live build accepts them
    (verified: `location`+`rotation` on all 10 mesh primitives, `scale` on 9 of 10). Only
    `rotation_euler`/`rotation_mode` are invalid, and those are still flagged. The agent was
    blocked from its valid fix and forced back into the crashing pattern.
  - **llama-server 400 template/parser error killed the next turn** - the log ended with
    `400 Unable to generate parser for this template ... Unexpected message role`. The retry
    logic only handled 500s; a new fallback now catches 400s and retries with the conversation
    flattened to plain system/user/assistant (tool results merged into user messages, no
    `tools` parameter), so the agent survives templates that cannot represent tool-calling.
  Tests updated/added in `tests/test_preflight.py` (43 total, green); all unit suites pass.

- **Console Severity Filtering** — With Debug Mode off, the Blender terminal now receives only this addon's warnings (`[⚠️Coworker]`) and error-level lines (ERROR/FAILED/FATAL/TRACEBACK) instead of the full `[Coworker]` diagnostic stream; routine diagnostics are log-only (still in `coworker.log`). Everything that is not the addon's own output (Blender's C-level log, other addons, user scripts, tracebacks) is unaffected. Debug Mode on still shows every line.

- **llama-server Console Window Title (#57 follow-up)** — The dedicated llama-server console window is now titled **"BFA Coworker — llama-server"** at creation. The title is passed via `STARTUPINFOW.lpTitle` on `CreateProcessW` (with `CREATE_NEW_CONSOLE`), because `subprocess.STARTUPINFO` exposes no `lpTitle` and calling `SetConsoleTitleW` from the parent was retitling the Blender/Bforartists terminal instead of the new window. The parent console is never touched: its title stays as-is and all of its output still streams through unchanged — llama-server's stdout/stderr continue to go to the new window with no redirection, and the only console lines the addon itself suppresses remain the intentional `[Coworker]`-prefixed diagnostics when Debug Mode is off.

- **Collection Color Tag Tool Marshaling Fixed** - `set_collection_color_tag` built its Blender-side call from a raw dict instead of the `Params` named-tuple its toolcode's `main()` expects, so every call raised a type error in Blender. It now uses the standard `Params(collection_name=..., color=...)` convention, matching the rest of the tool suite. A full sweep of all 24 bridge-backed wrappers found no other marshaling mismatches; the composite tools (`batch_keyframe_insert`, `three_point_lighting_rig`, `setup_pbr_material`, `download_polyhaven_asset`) generate their own code with a `result` variable and are unaffected. Smoke-test args added for the tool.

- **Asset Tool Parameter Marshaling Fixed** - The six asset-library tools (`get_asset_libraries`, `list_asset_catalogs`, `search_assets`, `get_asset_tags`, `load_asset_in_context`, `assign_material_to_objects`) built their Blender-side calls from dicts, the `send_code` helper, or `None` instead of the `Params` named-tuple the toolcode's `main()` expects, so every call raised a type error in Blender and the tools failed (often misreported as "library not found" / "no catalogs found"). They now use the same `Params` marshaling as the working `jump_to_*` tools. Part of Tier 3d Phase 3.

- **llama-server Console Output Routed to New Window** (#57) — On Windows, llama-server output now appears in its dedicated console window instead of being silently redirected to a log file. The new console shows model loading progress, health checks, and errors in real time. On Linux/macOS, output still goes to the log file as before. The Blender console is now clean of  diagnostic noise when Debug mode is OFF; toggle Debug mode in Preferences to restore full verbosity.

- **llama-server Actually Launches** — `start_local_llama()` now prepends `server_exe` to the subprocess args so llama-server is invoked correctly instead of silently failing to launch.
- **llama-server Download Progress Bar Cleared** — The download progress bar is now cleared after llama-server download finishes (previously it lingered).
- **llama-server Installed-State Validation** — Preferences now validate that the llama-server binary still exists before showing "Installed"; remove/update operators handle PATH-installed binaries correctly.
- **Corrected GGUF Preset Filenames** — Fixed Fable Fusion 27B IQ4_XS filename (was missing `MAX-NEO` in path) and corrected Qwen3.8 / Fable Fusion preset filenames.
- **Stale Extension Cleanup Before Install** — `build_addon.py` now removes stale installed extensions before installing, preventing import errors from mismatched `__init__.py` versions.
- **Deferred Layer-Collection Sync** — Layer-collection sync is deferred to prevent an outliner crash after object creation.
- **Removed Unsafe Deferred Depsgraph Sync Timer** — The crash-prone deferred depsgraph sync timer was removed from the MCP server.
- **ShaderNodeBsdfPrincipled Error Loop Breaker** — The agent now detects `ShaderNodeBsdfPrincipled` `base_color` attribute errors and injects targeted guidance (use `inputs['Base Color'].default_value`) instead of letting the LLM retry the same wrong code.
- **Tool Domain System Overhaul** — The tool domain system was reworked for full coverage: surface tools (code execution, scene inspection, screenshots, API docs) are always available; domain tools are pre-detected from prompt keywords *and* scene content, or loaded on-demand via `load_tools`. Domain skill files are auto-injected into the system prompt when matching domains are detected.
- **Harness Mode Ping** — `ping_agent()` now accepts an `operating_mode` parameter. In harness mode, MCP and LLM probes are skipped (returning "N/A (harness mode)") instead of failing with confusing "connection refused" errors. Fixes the misleading diagnostics in issue #48.
- **_list_tools_sync Harness Guard** — `_list_tools_sync()` now returns `[]` immediately when called in harness mode, preventing the 5-retry log spam seen in issue #48.
- **Diagnostics Display** — The diagnostics panel and Agent Control section in preferences now show N/A values with INFO icons instead of ERROR icons in harness mode.

- **Multi-Instance Port Conflict Detection** — Bridge server now detects when another Blender session already owns the default port (9876) and raises a clear error pointing to `port_offset` in Preferences. Uses `SO_EXCLUSIVEADDRUSE` on Windows to prevent silent port sharing that previously caused code-execution requests from a second session to be non-deterministically routed to the first session.

- **Curated 9-Model Preset List** — Replaced 14-model list with 9 curated presets (3 flagship / 3 mid / 3 light) tuned for Blender agentic work. New entries: Qwen3.8-27B, Fable Fusion 27B, Nail 35B A3B, Gemma 4 E4B, Qwen3.5-9B DeepSeek-V4-Flash. Each preset carries native context window, vision flag, mmproj filename, hardware recommendation, and "why pick this" rationale. Removed Gemma 3 12B Vision (#23) and Phi-4 14B Q3 (#32). GPT-OSS 20B is the new default (best Blender benchmarked model).
- **GPU Backend Selector** — New `llama_backend` dropdown in preferences (Auto / CUDA 12.4 / Vulkan / CPU). Auto-detect checks nvidia-smi for NVIDIA, wmic for AMD/Intel, and falls back to CPU. `download_llama_server()` selects the correct asset per backend and extracts cudart DLLs for CUDA. `find_llama_server()` prefers the active backend's bundled binary. `start_local_llama()` passes `--n-gpu-layers` (99 for GPU, 0 for CPU).
- **Unified Download Progress Bars** — Added `download_kind` field to LLMState. Replaced fragile string-matching progress logic with a single progress block driven by `download_kind` + `download_progress_pct` + ETA. Cancel button now works for llama-server downloads too.
- **Hybrid Tool Domain System** — Cascaded tool loading for local models: surface tools (code execution + scene inspection) always loaded; domain tools (animation, material, modeling, lighting, rendering, VSE, geometry nodes) pre-detected from user prompt keywords or loaded on-demand via `load_tools` meta-tool. Keeps context window small (~8 tools vs 30) while giving the LLM access to all tools when needed. Remote mode unaffected.
- **Smart Undo Skip for Code-Bug Errors** — `_error_is_code_bug()` detects pure code-bug errors (KeyError, AttributeError, TypeError, NameError, ValueError, "Node type undefined") that fail before creating any objects. Skips the undo+push round-trips, saving 2 HTTP calls per retry and avoiding depsgraph crashes from undo on empty scenes.
- **Blender 5.3 Material Node Hint** — Added to `skills/blender_53.md`: when `mat.use_nodes = True`, Blender auto-creates Principled BSDF + Material Output already connected. LLM should find them by iterating `node.type` instead of `nodes.new('BSDF_PRINCIPLED')` which doesn't work in Bforartists 5.3.
- **Reasoning Content Stripped from LLM Requests** — `_strip_reasoning_from_history()` removes non-standard `"reasoning"`-role messages before sending to the LLM, saving 500-3,000 tokens per request. Reasoning is still stored in full history for UI display.
- **Polyhaven Tools** — New tools for downloading CC0 resources from Polyhaven (models, HDRIs, textures) directly from the agent. Supports URL-based setup and test build.
- **Generative Plugin Foundation** — Tier 5 foundation: image gen plugins with auto-discovery, controller, and plugin base classes. Supports audio, image, text, and video plugin types, usability still WIP (not usable).
- **Version-Aware Skills System** — New searchable domain skills system with version-aware Blender API skills (`blender_50_51.md`, `blender_52.md`, `blender_53.md`). User custom skills support.
- **Copy Content Button** — New button in chat UI that copies the full conversation history to clipboard.
- **Stepped Benchmark Tests** — Replaced single-prompt benchmarks with 8 multi-step test suites (Scene Build, Animation, Modifiers, Assets+Mat, Baseline, Error Handling, Vision: Camera, Vision: Place). Steps are clicked in order, each building on the last. Progress tracked per-suite with Reset support.
- **Vision Test Suites** — Two new multi-step suites for vision-capable models: `vision_camera` (build a stage, then place/reframe/verify a camera by looking at `get_screenshot_of_area_as_image` viewport screenshots) and `vision_relative` (place a cup on a table, stack a marble on it, center a cone on a pedestal, and butt a cube flush — each verified visually via screenshots with iterative adjustment).
- **Context Size Presets** — Replaced the free-form context slider with one-click preset buttons (4K/8K/16K/32K/64K/128K) plus a Custom override slider. Picking a model preset now auto-selects a hardware-aware safe context size: the add-on detects system RAM (and VRAM via nvidia-smi for GPU backends), budgets the model weights + KV cache against it, and shows the recommendation in the panel — preventing the GPU-OOM startup crash caused by oversized contexts (e.g. 256K on a 27B model).
- **Tool Testing** — Added `tests/tool_smoke_test.py` for automated tool smoke testing.

### Changed

- **Model Selection UI Restructured** — Primary model family + local detection split. "Show More Models" toggle for the full preset list. Cleaner current-model selector UX.
- **HuggingFace Props Own Rows** — Model download properties now each get their own row in preferences for readability.
- **README Rewritten** — Minimal sales overview; details now live in the wiki.
- **Build Script Repacks Zip** — `build_addon.py` now repacks the built zip so all files sit under a top-level `bfa_coworker/` folder (required for Blender's drag-and-drop installer), cleans stale installed extensions before install, and updates the default Blender path.
- **"Agent" → "Coworker" Branding** — All user-facing display strings now say "Coworker" (buttons, labels, mode toggle, error messages) for branding consistency. Tooltips/descriptions retain "AI agent" for discoverability. Internal identifiers unchanged.
- **Removed Redundant Operating Mode Panel** — Removed the duplicate Operating Mode selector from the Advanced preferences tab; the top-level selector and tab buttons already handle mode switching.
- **New Addon Interface** — Redesigned preferences panel with 4 tabs (General, LLM, Remote, Diagnostics). Debugging panel moved out of tabs into its own section.
- **Unified Operating Mode Selector** — Combined local/remote mode into a single dropdown selector. Improved reasoning content display with better verbosity.
- **Skills Improvements** — Multiple skill file enhancements: animation curve understanding, material creation smarts, operator mode switching, Blender 5.3 API nuances, sequencer versioning API.
- **Polyhaven URL Setup** — Right URL configuration for Polyhaven integration.

### Fixed

- **Preferences Button Now Filters to Coworker** — The ⚙️ preferences button in the chat header now opens Blender preferences filtered directly to the Coworker addon (search box pre-filled with "Coworker", addon expanded, preferences visible). Previously it only opened the Add-ons tab unfiltered because `bpy.ops.preferences.addon_show` silently fails for extension add-ons (which get a `bl_ext.` module prefix) and races the preferences window build. The harness "Configure" button now also filters to the addon and selects the Advanced tab.
- **Depsgraph Crashes in Blender 5.3** — Replaced crash-prone `view_layer.update()` calls with `_safe_depsgraph_sync()` that uses `update_tag()` on each object (lightweight, no full rebuild). Added `_code_touches_collections()` and `_code_is_undo_or_push()` heuristics to skip full depsgraph sync after collection-manipulation and undo/push operations. Removed before-exec depsgraph sync entirely — tagging objects before smart undo caused stale-object crashes in `pyrna_struct_CreatePyObject`.
- **Defensive Entity Snapshots** — `_SNAPSHOT_EXTRA` now wraps each datablock iteration in try/except via `_sn()` helper, so a single corrupted datablock doesn't crash the entire snapshot.
- **Remote API Mode** — Fixed remote API mode not working correctly. Unified Operating Mode selector resolves mode conflicts.
- **Python Context Internal State Bug** — Fixed internal state bug in Python context handling.
- **No Text Content in Tool Result** — Fixed error when screenshot tool returns no text content.
- **Debugging Panel Layout** — Moved debugging panel out of tabs per user feedback.
- **Re-Entrancy Guard for Conversation Turns** — Added `turn_active` flag to `AgentState` preventing overlapping `run_conversation_turn()` calls from corrupting shared history. Chat send and test step buttons now check `turn_active` before spawning threads, eliminating the primary "two balls" duplicate-object root cause.
- **Undo Code Silent Fallback Fix** — `_undo_code()` now returns an explicit `{"status": "error"}` when no window/area is available for undo/push, instead of silently returning `{"status": "ok"}` and leaving the scene dirty.
- **`ValueError` Removed from Code-Bug Skip** — `_error_is_code_bug()` no longer treats `ValueError` as a pure code-bug, since it can fire after objects have been created. Prevents the smart-undo system from skipping undo when side effects exist.
- **`TypeError` Removed from Code-Bug Skip** — `_error_is_code_bug()` no longer treats `TypeError` as a pure code-bug, since enum validation errors (e.g. `enum "4" not found in ('NONE', 'COLOR_01', ...)`) can fire after objects are created. Prevents duplicate objects from skipped undo on type errors.
- **Retroactive Entity Cleanup Fallback** — Added `_build_cleanup_code()` that generates Blender Python code to delete orphaned datablocks when `bpy.ops.ed.undo()` fails (no window/area, empty undo stack). The smart-undo system now checks the undo result and falls back to direct entity deletion using the snapshot diff.
- **Auto-Continue Tool Call Deduplication** — When `finish_reason=length` triggers auto-continue, tool calls from the continuation are now deduplicated by ID before merging, preventing duplicate tool execution.
- **Test Suite Busy Guard** — Added `_test_suite_running` tracking to prevent launching concurrent test steps for the same suite. Progress no longer advances until the step thread completes.
- **Thread-Safe History Save** — Added `_history_save_lock` to `_save_chat_history()` preventing concurrent threads from writing partial conversation dumps.
- **Test File Tail Cleanup** — Removed duplicated `TestForegroundServer`/`TestInteractiveServer` class definitions and malformed `exit(1)    unittest.main()` line from `tests/test_blender_mcp_with_blender.py`.
- **Collection Heuristic Hardening** — Added `layer_col.exclude` and `layer_col.hide_viewport` to `_code_touches_collections()` patterns, catching collection mutations from `jump_to_view3d_*` tool templates that were previously missed. Prevents Blender 5.3 depsgraph crash after full `view_layer.update()` following collection edits.
- **Read-Only Snapshot Skip** — Added `_code_is_readonly()` heuristic that detects read-only code (no `bpy.ops`, `.new()`, `.remove()`, etc.) and skips the costly 12-datablock entity snapshot on those calls. Saves significant overhead when the LLM makes many inspection calls between mutation calls.
- **Scene-Aware Domain Pre-Detection** — Added `_detect_domain_from_scene()` that scans `bpy.data` for armatures, actions, materials, node groups, lights, cameras, meshes, sequencer strips, and geometry nodes modifiers. Pre-loads matching domains before the turn starts, so the LLM has the right tools even when the user's prompt is vague about what exists in the scene.
- **Domain-Aware Skill Auto-Injection** — Added `_DOMAIN_SKILL_MAP` and `get_domain_skills()` to bundle domain skill files (`animation.md`, `materials.md`, `mesh_editing.md`, `modifiers.md`, `rendering.md`) into the system prompt when matching domains are detected. The LLM gets version-aware API rules upfront without needing to search for them.
- **Result Trimming Middleware** — Added `_trim_tool_result()` that strips JSON boilerplate (`{"status": "ok", "result": ...}`) and keeps only the meaningful inner data when truncating tool results for LLM context. Error messages are preserved in full within the budget; success results have their inner `result` fields extracted, giving the LLM more structured data at the same 500-char limit.
- **Composite Tool Wrappers** — Three new MCP tools that combine multi-step operations into a single call:
  - `setup_pbr_material` — creates a PBR material with Principled BSDF + normal map + displacement, optionally downloading Polyhaven textures. Saves 3-5 round-trips.
  - `batch_keyframe_insert` — keyframes multiple objects across multiple frames with location/rotation/scale in one call. Saves N round-trips per object per frame.
  - `three_point_lighting_rig` — creates key, fill, and rim lights tracking a target object. Saves 3-5 round-trips.
  All three registered in their respective domains (animation, material, lighting, rendering).
- **User Skill Loader** — Added `get_user_skills()` that scans `SCRIPTS/bfa_coworker_skills/` for `.md` files and injects them into the system prompt alongside built-in skills. Users can drop custom skill files into this directory to teach the LLM project-specific conventions, API overrides, or workflow patterns without modifying the addon.

## [v1.1.36] - 2026-08-12

### Added

- **Cancel Downloads** — New `cancel_download()` function and `_BFACW_OT_cancel_download` operator. Thread-safe cancellation via `threading.Event`. Partial files are cleared on abort. Cancel button appears in UI while a download is active.
- **Disk Space Detection** — Pre-flight disk space check before multi-GB downloads via `_check_disk_space()` using `shutil.disk_usage()`. Requires file size + 5% margin. Sets actionable error if insufficient space.
- **Download Progress Bar** — Real-time visual progress bar (`row.progress(factor=..., type='BAR')`) in the preferences panel showing percentage, speed, ETA, and file size. Replaces the old text-only progress display.
- **Always-Visible Download Button** — The "Download & Start" button never disappears. States: "Download & Start" → "Downloading…" (disabled) → "Already Downloaded" (disabled). Progress/error always shown below.
- **Clear HTTP Error Messages** — 401 → suggests HF_TOKEN, 403 → suggests granting access at huggingface.co, 404 → suggests checking repo/file name.
- **HF_TOKEN Support** — New `hf_token` field (password-masked) in Advanced preferences. Passed to both direct download and llama-server subprocess. Also auto-detects `HF_TOKEN` / `HUGGINGFACE_TOKEN` environment variables.
- **Fallback Download** — If direct download fails for non-auth reasons (network restrictions, proxy), falls back to `llama-server --hf-repo/--hf-file` with 15-minute timeout and subprocess crash detection.
- **Direct GGUF Download** — New `_download_gguf_direct()` function streams model files from HuggingFace in 64 KB chunks with real-time progress (percentage, speed, ETA, progress bar). No more reliance on `llama-server` console for download progress.
- **llama-server Binary Download** — One-click download and extraction of `llama-server` from GitHub releases (tag `b10154`). Supports Windows (zip), macOS/Linux (tar.gz). Extracts to `~/.cache/bfa_coworker_llama/`.
- **File Size Pre-Fetch** — `_get_hf_file_size()` performs HEAD request to HuggingFace to get file size before download, enabling accurate progress estimates.
- **ETA Formatting** — `_format_eta()` shows human-readable ETA ("3m 24s remaining") during downloads.
- **Model Presets (14 curated)** — Categorized visual sections: Flagship (24 GB+ VRAM), Mid-Range (12-20 GB), Lightweight (≤ 8 GB). Includes Gemma 4 26B, DeepSeek R1 Distill 32B, Qwen 2.5 Coder 32B, Mistral Small 3.1 24B, Gemma 3 27B, Qwen3.6 35B A3B, GPT-OSS 20B, Phi-4 14B, Qwen3.5 9B Heretic, Gemma 3 12B Vision, Qwen3 8B, Phi-4 14B Q3. Each preset has pre-configured `context_window` and `max_tokens` values.
- **Remote Provider Presets** — OpenRouter provider with 10 curated models (Claude 4.6 Sonnet, GPT-4.1, GPT-4o, DeepSeek Chat V3, Gemini 2.5 Flash/Pro, Llama 4 Maverick, Qwen3.6 35B, Mistral Small 3.1, GPT-5 Mini). Live model listing via `/v1/models` endpoint.
- **Existing Model Scanner** — Scans both the configured models directory and HuggingFace cache for `.gguf` files. Found models appear in a dropdown for one-click selection.
- **"Last Used" Model Recall** — The last selected preset or existing model path is persisted across Blender sessions via built-in property storage.
- **Port Configuration** — Individual port overrides (bridge, MCP, LLM) plus a global `port_offset`. Effective ports shown as read-only labels via `_draw_effective_ports()`.
- **Max Output Tokens** — New `local_max_tokens` field in config and preferences. Per-preset default values (16384 flagship, 8192 mid-range, 4096 lightweight). Auto-continue on `finish_reason=length` with concatenation (max 2 attempts).
- **Context Window Auto-Set** — Selecting a preset auto-sets `--ctx-size` to `min(preset.context_window, 65536)`. Default raised from 8192 to 32768. Existing users auto-upgraded on first server start.
- **Reasoning Content Logging** — Full chain-of-thought from reasoning models (Qwen, DeepSeek, Gemma 4) logged to console for debugging. No truncation.
- **Benchmark Tests** — Four benchmark buttons in Diagnostics section: Objects (random objects in colored groups), Scene (ground + columns + lighting), Animation (torus with keyframes), Collections (SET/LIT/ANIM with color tags). Each runs through the full agent pipeline.
- **System Prompt Path Fix** — `_get_system_prompt()` now searches both dev layout (`mcp/blmcp/data/prompts.yml`) and deployed layout (`vendor/blmcp/data/prompts.yml`). The full 3000+ char Blender system prompt is now loaded in deployed addons.
- **Orphaned Tool Message Cleanup** — `_drop_orphaned_tool_messages()` removes tool-role messages without preceding assistant `tool_calls`, preventing Jinja template errors from llama-server on sliced conversation history.
- **SSE Parser** — `_parse_sse_json()` / `_parse_sse_text_response()` for FastMCP stateless_http mode (used by `streamable-http` transport).
- **Thread-Safe Stop Mechanism** — `request_stop()` / `clear_stop()` for clean interruption of the conversation loop.
- **Pipe Drainer** — Background threads (`_start_pipe_drainer()`) to drain stdout/stderr pipes from subprocesses, preventing deadlock.
- **Port Killer** — `_kill_process_on_port()` uses netstat+taskkill (Windows) or fuser (Linux) to clean up orphaned processes.
- **Port Availability Check** — `check_ports_available()` tests bridge/MCP/LLM ports by attempting to bind with `SO_EXCLUSIVEADDRUSE`.
- **Logging Infrastructure** — File-based logging via `print()` tee (`log.install_print_tee()`). Blender 5.3+ policy violation warning coalescer (`log.install_policy_warning_filter()`).
- **Timer Interval Configuration** — `timer_interval_active` (0.05–5.0s), `timer_interval_idle` (0.1–10.0s), `timer_interval_idle_delay` (1.0–60.0s) for fine-tuning polling behavior.
- **Tool Logging Toggle** — `use_log` preference to log every tool request/response to the console.

### Changed

- **Download Model** — Rewritten from polling `llama-server` health endpoint to direct HTTP chunked download with real progress data. llama-server is started after download completes.
- **No new dependencies** — All download logic uses only `urllib.request` (stdlib).
- **Model Preset Refinement** — All 14 presets now carry `context_window` and `max_tokens` metadata. Preset selection auto-configures both the server's `--ctx-size` and the API's `max_tokens`.
- **Server Launch Refinement** — `start_local_llama()` resolves model from local file → HF cache → `--hf-repo/--hf-file`. Auto-upgrades `ctx_size` from 8192 to 32768. Redirects HF cache to models dir. Passes `HF_TOKEN` for gated models. Uses `CREATE_NEW_CONSOLE` on Windows for proper PID tracking.
- **Server Stop Refinement** — `stop_local_llama()` now has graceful terminate + fallback `taskkill`/`pkill` for orphaned processes.
- **Port Handling Refinement** — Migrated from hardcoded ports to configurable offsets + per-port overrides with effective-port display.
- **MCP Server Python Path Resolution** — Replaced the bundled `uv`-managed `.venv` (non-portable, hardcoded machine-specific paths) with a portable approach:
  - `build_addon.py` installs pure-Python dependencies into `vendor/deps/` via `pip install --target` and copies `blmcp` source into `vendor/blmcp/`.
  - `agent_controller.py` uses Blender's own Python (`sys.prefix/bin/python.exe`) with `vendor/deps/` and `vendor/` on `PYTHONPATH`.
  - `_ensure_vendor_deps()` auto-install fallback for source installs.
  - `_find_python_with_pip()` in `build_addon.py` finds a Python with pip even when `sys.executable` is a uv-managed venv.
  - Removed the old `vendor/python_env/` layout entirely.
- **`max_tokens` Default** — Raised from 4096 to 16384 (per-preset: 16384 flagship, 8192 mid-range, 4096 lightweight). Replaces hardcoded value with configurable parameter.
- **`--ctx-size` Default** — Raised from 8192 to 32768. Auto-set from preset's `context_window` (capped at 65536 for consumer GPU safety).
- **Interface Modularization** — Split `__init__.py` (~1,437 lines) into separate focused modules: `shared.py`, `preferences.py`, `operators_server.py`, `operators_llm.py`, `operators_agent.py`, `operators_hf.py`.
- **Duplicated code removed** — `ui_chat.py` now imports `effective_ports()` from `.shared` instead of duplicating port constants.
- **Diagnostics Section** — Expanded with Check Ports, Diagnose (ping), and four Benchmark buttons — all gated behind `BFACW_DEBUG` flag.

### Fixed

- **401/403/404 errors** from HuggingFace are now surfaced immediately with actionable messages, instead of silently failing inside the llama-server subprocess.
- **Download button disappearing** — No longer disappears after clicking. Always remains visible with correct state (Downloading… / Already Downloaded / Download & Start).
- **`finish_reason=length` truncation** — Reasoning models (Qwen, DeepSeek, Gemma 4) that hit the token limit mid-reasoning now auto-continue: partial output is appended, "Continue." is sent, results are concatenated (max 2 attempts).
- **System prompt not loading in deployed addon** — Added `vendor/blmcp/data/prompts.yml` path so the full 3000+ char prompt is found in installed builds.
- **Orphaned tool messages in sliced history** — `_drop_orphaned_tool_messages()` prevents Jinja template errors when history truncation breaks tool-call pairs.
- **Subprocess pipe deadlock** — Pipe drainer threads prevent Blender from hanging when subprocess output fills the pipe buffer.
- **Orphaned server processes** — Port killer + fallback taskkill/pkill clean up stale processes on port conflicts.
- **Traceback verbosity** — Error tracebacks from failed Blender code execution are truncated to last 3 frames + exception message, preserving context window space for the LLM.
- **UI not refreshing after download** — Both llama-server and model download operators now redraw all `PREFERENCES` areas on completion instead of only `context.area`.

## [v1.1.35]

### Changed

- **Interface Modularization** — Split `__init__.py` (~1,437 lines) into
  separate focused modules:
  - `shared.py` — Constants, port helpers, lazy import wrappers, model/remote
    preset items
  - `preferences.py` — `_State` runtime state class + `_BlenderMCPPreferences`
    (properties + `draw()` method)
  - `operators_server.py` — Bridge server start/stop operators + autostart
    timer + CLI handler
  - `operators_llm.py` — LLM operators (download model, start/stop, download
    llama-server, scan, select preset/existing model)
  - `operators_agent.py` — Remote API operators (test connection, refresh
    models, browse models, ping agent)
  - `operators_hf.py` — HuggingFace cache operators (open, clear)
  - `__init__.py` — Thin registration hub (~100 lines) importing all classes
    and wiring `register()`/`unregister()`.
- **Duplicated code removed** — `ui_chat.py` now imports `effective_ports()`
  from `.shared` instead of duplicating the port constants and helper function.

### Added

- **llama-server Auto-Download** — One-click download and extraction of
  `llama-server` from GitHub releases (ggml-org/llama.cpp) into
  `~/.cache/blender_mcp_llama/`. No manual `llama.cpp` install needed.
- **Model Presets** — Curated dropdown of 13 recommended GGUF models (Gemma 4,
  Qwen3.6, Qwen3-Coder, Llama 4 Scout, Qwen 2.5, Qwen3, DeepSeek-R1) with
  tooltips showing RAM requirements, disk size, and capability rating.
  Selecting a preset auto-fills the repo ID and filename.
- **Existing Model Scanner** — Scans both the configured models directory and
  HuggingFace cache for `.gguf` files. Found models appear in a popup menu for
  one-click selection. Access via "Scan" button in preferences.
- **"Last Used" Model Recall** — The last selected preset or existing model path
  is persisted across Blender sessions via built-in property storage.
- **Categorized Model Presets** — Presets reorganized into visual sections
  (Flagship / Mid-Range / Lightweight) with per-category clickable buttons
  replacing the flat dropdown. New presets: Mistral Small 3.1 24B, DeepSeek R1
  Distill 32B, Qwen 2.5 Coder 32B, Gemma 3 27B, Phi-4 14B, Llama 3.1 8B.
  Default preset is now Mistral Small 3.1 24B (RTX 4090 sweet spot).
- **Persistent Repo Memory** — Architecture, conventions, decisions, and
  deferred tasks tracked in `/memories/repo/` for the AI coding agent.
- **CHANGELOG.md** — This file, tracking project changes.

### Changed

- LLM Configuration UI redesigned: "Recommended Models" dropdown first, then
  "Existing Models" scanner with Scan button, with manual repo/filename fields
  under "Advanced Settings".
- `_BLMCP_OT_start_llm` now respects `existing_model_path` — if set, passes the
  path directly to `start_local_llama()`.
- `_autostart_agent_timer` also respects `existing_model_path` for auto-start.

