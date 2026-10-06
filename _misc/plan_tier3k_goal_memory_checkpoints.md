# Tier 3k — Goal & Plan Memory, Reliable Checkpoints, Long Requests

**Status**: 🚧 In progress — Phases 1-6 implemented (2026-10-05); follow-ups
2026-10-06 (Session sub-panels, live plan ticking, Goal & Plan in the chat,
sticky image pin, stop rules, crash-safe servers) implemented and tested;
**final in-Blender verification pending** (see §6 and §7). Move to
`Plans History/` with an Audit section once verified in Bforartists.
**Branch**: `fix/goal-memory-checkpoint-hardening`
**Scope**: local mode first (small windows), and therefore remote mode too.
External Harness mode is untouched (the external client owns the loop).

## 1. Why

Two field logs from 2026-10-05 (local, 16K window) plus quick user tests:

| Symptom | Root cause found in code |
| --- | --- |
| The chat "talked to itself" -- an agent prompt showed up as a new user turn | The `finish_reason=length` auto-continue injected `"Continue. Keep this next step small..."` with the user role and **no `[System:` prefix**. On a failed continuation it stayed in history; the panel, compaction and goal capture all treated it as a real user message, splitting the turn. |
| Self-prompts cancelled the live conclusion | (a) the 12-iteration cap always forced `"Please summarize what was done in 1-2 sentences"`, even when the final reply arrived **on** the 12th iteration (log 2 ended on a promise it never kept); (b) the action-nudge regex fired on closing offers ("If you'd like, I'll add a chimney"); (c) after every empty-content tool round the note said *"Please provide a helpful response to the user"*, which told a mid-task model to stop. |
| The goal erased on long turns / compaction | (a) the 20-message slice in `_build_send_messages` dropped the turn's real request once one turn exceeded 20 messages; (b) `_fit_history_to_budget` pinned the last *user-role* message -- an injected note -- so trimming removed the request; (c) the memory note kept the goal at 160 chars, cut the newest lines when over budget, and had a fixed 600-token cap. |
| `request (16483 tokens) exceeds the available context size (16384 tokens)` | The 3.5 chars/token heuristic under-counts JSON-number dumps and images; a single long turn had nothing older to retire, so the forced compaction could not recover. |
| Spurious "you already created texts: Coworker_006" warning | Entity tracking counted the add-on's own code-history text blocks. |

## 2. Design

### 2.1 Injected notes are structural, not textual
* `session_memory.make_system_note(text, kind)` -> `{"role": "user", "content": "[System: ...]", "system_note": kind}`.
* `is_system_note()` keys on the flag, then the prefix, then the legacy unprefixed "Continue." text.
* Every injection site uses it (`continue`, `followup`, `nudge`, `entity`, `scene_change`, `spiral`, `wrapup`).
* Notes raised mid-batch (scene change, entity) are **deferred** until after all of the batch's tool results (a user note between `tool_calls` and its results breaks pairing).
* Follow-ups replace older follow-ups (`_drop_stale_notes`) -- no pile-up, no user/user pairs.
* The follow-up carries the current request and the next plan step, and says to reply only **when done**.
* Persisted histories are migrated on load (`_sanitize_loaded_history`).
* Addon bookkeeping keys (`turn_start`, `summary`, `system_note`, ...) are stripped from the wire copy.

### 2.2 Pinned goal & plan (`goal_plan.py`, new, bpy-free)
* `session_goal` (first request, up to 700 chars), `turn_goal` (latest request, 500), `steps` (<= 12), `user_notes`.
* Rendered as a bounded `[Goal & plan -- pinned]` block appended to the **sent** system prompt every request (local: ~4% of the window, 900-2400 chars; remote: 2400). Over budget: finished steps collapse first, then goal texts shrink.
* Local meta-tool `update_plan(steps?, done?, current?)` -- intercepted like `load_tools`, never sent to MCP; lenient argument shapes for small models.
* Stored on `CheckpointStore.goal`; included in checkpoints (restore rewinds the plan), the persisted payload, and cleared by New Thread.
* Mirrored to an editable **`Coworker Plan.md`** text block (created only when the user clicks *Edit in Text Editor*); user edits are parsed back on the chat timer and win over agent updates.

### 2.3 Long requests: automatic rounds
* When the iteration cap is hit, `_try_start_round()` resets the counter (up to `auto_continue_rounds`, default 3, pref *Auto-Continue Rounds*) **only if** the round made progress (a successful tool call or a finished plan step) and the plan, if any, still has open steps.
* Otherwise a **progress report** is requested ("what is done, what remains, say continue"); if that fails, a conclusion is synthesized from the plan. The wall-clock stop also leaves a synthesized conclusion.
* A final reply on the last iteration is kept (`_finished` flag).

### 2.4 Reliable fitting & compaction on small windows
* Message-count slice always keeps the current request. Over the 20-message cap it drops the oldest messages in blocks of 8 (`_HISTORY_DROP_STEP`), not one per request, so the prompt prefix -- and llama-server's KV cache -- stays reusable across several requests of a long turn.
* `_fit_history_to_budget` pins the last **real** request and, if the pinned turn still overflows, sheds that turn's own older tool results -> stale notes -> old tool-call code (send copy only; the panel keeps everything). Replaces the freebuff worktree's in-history shedding.
* Prompt-size **calibration**: every local response's `usage.prompt_tokens` is compared with the estimate; the effective budget is `base / calibration` (rises immediately, decays slowly, max 2.5). A context-overflow 400 feeds `n_prompt_tokens` into it before the retry, and the doomed payload is no longer re-sent non-streaming.
* Memory note scales with the window (`set_memory_budget`: 4% of ctx, 400-1500 tokens); trimming drops the **oldest** lines; writer output is validated (chatty/empty replies fall back to the heuristic); the writer reads the **newest** retired text and no longer restates the goal.

### 2.5 UX
* Workshop shows notes as small titled entries ("Coworker -> itself: keep going", "Continued after the output limit", ...), never as user bubbles.
* "Compaction"/"Archive" -> **Checkpoint** wording ("Checkpoint -- N earlier message(s) summarized", *Checkpoint Now*).
* Session panel gets a **Goal & Plan** sub-panel (goal, current request, step checklist with progress bar, notes, *Edit in Text Editor*, *Clear Plan*). The chat itself is not bloated.
* **Thinking** (reasoning effort) selector in the chat panel and the Text Editor chat panel (local mode only -- remote providers ignore it).
* Preferences: Context Window + KV-cache quantization are greyed out while llama-server is running (fixed at model load) with a *Stop Model* button; Reasoning Effort stays editable and applies from the next message.

## 3. Answers to the open questions
* **Context window** -- baked into llama-server's KV cache at load; budgeting already reads the server's real `n_ctx`. Locked while the model runs.
* **Thinking effort** -- sent with every request and re-synced on every Send, so it can change any time; now also in the chat panel.

## 4. Relation to Tier 4
`GoalPlan` is deliberately small. Tier 4f's planner/specialists/validator (`plan_tier4f_agent_intelligence.md`) can adopt it as the persisted `TaskPlan` surface instead of inventing a second store. Exposing the plan to External Harness clients (an MCP `update_plan` tool) is a Tier 4 follow-up.

## 5. Tests
* `tests/test_goal_plan_hardening.py` (new, 24 tests): goal capture (incl. images), lenient plan tool, markdown round-trip & user edits, bounded block, checkpoint/payload/restore/reset, legacy "Continue." never splits a turn, failed continuation leaves no scaffold, follow-ups don't pile up and carry the goal, closing offers aren't nudged, last-iteration reply kept, progress report with no user/user pairs, auto rounds keep the request past the 20-message slice, no round without progress, plan tool stays local + pinned in the system prompt, 16K window with dense dumps fits and calibrates, overflow 400 calibrates and retries, add-on text blocks excluded from entity tracking, memory-note trimming/validation/scaling.
* Mutation checks: removing the slice fix, the continuation-scaffold undo, or calibration makes the corresponding tests fail.
* Updated existing tests for the intended wording/behaviour changes (`test_session_memory`, `test_orchestration_helpers`, `test_context_budget`, `test_turn_loop_integration`).
* Full suite: all tests pass except the pre-existing environment-only failures (`mcp`/Blender not installed: `test_mcp_server`, `test_rst_*`, `test_tool_listing`, `test_harness_config`, `test_blender_mcp_with_blender`) -- identical to `main`.

## 6. In-Blender verification still needed
1. Re-run the "floating parts + reference image" request on the 16K local model: no 400, no agent-authored user turn, a real conclusion.
2. A request needing > 12 tool calls: status shows "Long request -- continuing (round 2 of 4)"; Goal & Plan sub-panel ticks steps.
3. *Edit in Text Editor*: edit a step / add a note; next request reflects it.
4. Preferences with the model running: Context Window greyed + *Stop Model*; Thinking selector in the chat changes the next request's budget (console: `thinking_budget_tokens=`).
5. Restart Bforartists: goal, plan and checkpoints restored.

## 7. Follow-ups (2026-10-06)

| Item | Where |
| --- | --- |
| Session panel: summary + Context / Memory & Checkpoints sub-panels; plain-language memory; checkpoint titles + one-click restore | `ui_chat.py` `_draw_session_section`, `_draw_context_section`, `_draw_memory_section`, `_checkpoint_title`, `_humanize_memory` |
| Image marker shown as an icon | `ui_chat.py` `_split_attachment_marker`, `_draw_user_text` |
| Plan ticks live mid-turn (narration parsing, resend keeps progress, text refs, active step) | `goal_plan.py` `note_progress_from_text`, `mark_active_if_idle`, `_step_index`; hooks in `agent_controller.py` |
| Goal & Plan moved into the chat panel | `ui_chat.py` `_draw_goal_plan_inline` |
| Sticky image pin / re-link | `ui_chat.py` `_remember_attachment`, `_restore_attachment`; `chat_attachments.persist_image` |
| Stop rules 1-5 (plan done, real progress only, one no-plan round, idle/loop guard, next-step question) | `agent_controller.py` `_try_start_round`, `_tool_makes_progress`, `_code_changes_scene`, stop block after each tool batch, wrap-up reasons |
| Crash-safe servers | `process_guard.py` |

Additional in-Blender checks: steps tick while a turn runs; a finished request
ends with a next-step question instead of polishing; undo during a turn does not
clear a sticky image; killing `bforartists.exe` also ends `llama-server.exe`.
