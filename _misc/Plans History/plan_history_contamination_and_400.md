# Plan — Conversation History Contamination & the Persistent 400

> ✅ **DONE — fully implemented & audited (2026-09-30).** Archived to
> `_misc/Plans History/`. Every Phase A/B/0 item is present in the codebase and
> covered by tests; see [§8 Audit](#8-audit--closure-2026-09-30) for the
> file:line verification.

**Status**: ✅ Complete — all phases (A, B, 0, C, D) implemented, tested, audited
**Date**: 2026-09-17 (implemented) · 2026-09-30 (audited + archived)
**Related**: issue #62 (history slicing), issue #63 (500 fault classification)

---

## 0.5 Phase 0 — Post-fix test findings (2026-09-17)

Phase B worked: the user message is recalled and the model answers it. Three
new issues surfaced from the same test run.

### 0.5.1 Preflight false positive: comma-separated `import bmesh` ✅ FIXED

```
[Preflight] Found 1 issue(s):
  - Code uses bmesh but does not 'import bmesh'.
```

The model wrote `import bpy, bmesh, math`. The check is a substring test:

```python
if re.search(r'\bbmesh\.', code) and 'import bmesh' not in code:
```

`'import bmesh'` is **not** a substring of `'import bpy, bmesh, math'`, so the
guard fires on perfectly valid code. The model then had to burn a retry
rewriting working code.

**Fix**: detect the import properly — match `import bmesh` as a module in a
comma-separated list, and `from bmesh import ...`.

**Implemented**: new `_imports_module(code, module)` helper in
`mcp_to_blender_server.py` uses two regexes — one for `import a, b, c` (with
optional `as` alias) and one for `from x import ...` — and is applied to both
the `bpy` (check #1) and `bmesh` (check #24) guards. It correctly rejects
lookalikes such as `import bmesh_utils`. Six new tests in
`tests/test_preflight.py`.

### 0.5.2 Tool results are truncated to 500 chars in *history*, so the UI shows them truncated ✅ FIXED

`agent_controller.py`:

```python
_MAX_TOOL_RESULT_CHARS = 500
truncated = _trim_tool_result(result_text, max_chars=_MAX_TOOL_RESULT_CHARS)
history.append({"role": "tool", ..., "content": truncated, ...})
```

The chat panel renders from `conversation_history`, so the **user sees the
truncated 500-char version** — not just the model. The user's report is
explicit: *"Ideally a user should be able to see it all."*

The truncation exists for a good reason (context bloat), but it is applied at
the wrong layer: it should constrain **what is sent to the LLM**, not what is
stored for display.

**Fix**: store the full result in history; trim only when building the request.

Additional UI truncations found:

| Location | Limit | Effect |
|---|---|---|
| `ui_chat.py` `streaming_text[:300]` | 300 | Live output cut mid-sentence |
| `ui_chat.py` `d[:200]` | 200 | Tool detail cut |
| `ui_chat.py` `content[:80]` | 80 | History preview cut |
| `ui_chat.py` `raw_preview = content[:300]` | 300 | Raw tool content cut |

**Implemented**: `_MAX_TOOL_RESULT_CHARS` is now a module constant (2000, up
from 500); the store site keeps `result_text` in full; new
`_trim_history_tool_results()` trims only `tool` messages at request-build time
and is wired into `_run_conversation_turn_inner` right after
`_strip_ui_only_from_history`. Spiral detection now trims locally via
`_trim_tool_result(result_text, ...)` instead of the removed `truncated` local.
All four UI truncations above were removed — the live streaming preview, the
tool detail, and the tool "Details" section now render in full. The 10-message
History overview keeps its 80-char preview deliberately: it is an explicit
compact list, not the message body. Seven new tests in
`tests/test_orchestration_helpers.py::TestTrimHistoryToolResults`.

### 0.5.3 HTTP 500: malformed tool-call JSON from the model ✅ FIXED

```
Failed to parse tool call arguments as JSON: ... invalid string: missing closing quote
```

llama-server could not parse the model's own tool-call arguments — the model
emitted an unterminated string (its code block was cut mid-generation). This is
a **model-side generation failure**, not our bug, but it currently surfaces as
an opaque 500 with no recovery.

**Fix (defensive)**: classify this as a distinct fault and retry once with a
nudge, rather than reporting a raw 500. Lower priority than 0.5.1/0.5.2.

**Implemented**: new `_FAULT_TOOLCALL` class in `_classify_llm_500()`, with
`_TOOLCALL_FAULT_MARKERS` checked **after** the server markers (an OOM that
mentions a tool call is still a server fault) but **before** the generic
template markers — the real llama-server message contains the word "template",
so without this ordering it was misclassified as a template fault and would
have triggered the tools-as-text downgrade, which cannot help a malformed
*output*. The 500 handler gained a guarded one-shot retry (`_toolcall_nudged`)
that appends an explicit "emit complete, well-formed JSON arguments" nudge to
the system prompt and resends; if it still fails, new
`_toolcall_fault_message()` reports that this is a model generation failure and
suggests lowering Reasoning Effort or raising the Context Window. The
llama-server log cross-check is now skipped for specific matches
(`_fault not in (_FAULT_TEMPLATE, _FAULT_TOOLCALL)`) so a stale OOM in the log
tail cannot override a clear diagnosis. 4 new classifier tests, 4 message
tests, 5 wiring tests, and the marker-sync guard extended to the new tuple.

### 0.5.4 The tool-call recovery could not actually recover ✅ FIXED

The `_FAULT_TOOLCALL` handling above classified the fault correctly but could
not fix it. A benchmark run (`scene_build` step 2, "scatter props") failed
after **248 s** with the same 500. The model tried to emit the whole scatter
script as **one** `execute_blender_code` call, ran out of output tokens
mid-string, and the arguments were unparseable. Four defects compounded:

| # | Defect | Effect |
|---|---|---|
| 1 | The nudge said "emit the tool call again with complete JSON" | The call was too large to fit in one response, so the retry truncated in the same place |
| 2 | The generic retry path re-sent the identical nudged payload 4 more times | ~60 s each, all failing identically — the 248 s |
| 3 | Auto-continue sent the **raw** `history` | Still carried `ui_only` + `reasoning` entries → `400 Unexpected message role` |
| 4 | Auto-continue omitted `thinking_budget_tokens` | Reasoning could eat all of `max_tokens`, truncating the tool call again |

**Fix**: the nudge now names the real cause and tells the model to **split the
work into smaller calls** (~40 lines or fewer per script); once the one-shot
nudge has been used the retry path surfaces the error immediately instead of
re-sending an identical payload; the auto-continue path strips `reasoning` and
`ui_only` and sanitizes roles (matching the main request path) and forwards the
thinking budget; and the bare `"Continue."` prompt now caps the size of the
next step. The surfaced error message and both system prompts
(`prompts.yml`, `prompts_compact.yml`) also instruct the model to keep tool
calls small and split large jobs across several calls. 12 new tests.

---

## 0. RESOLVED — Phase A results (2026-09-17)

Phase A logging produced decisive evidence. **Both root causes are now
confirmed**, and the original hypotheses were partly wrong.

### 0.1 The 400 root cause: `reasoning` messages reach the API

The flatten log is unambiguous:

```
flattened roles = system,assistant,user,reasoning,assistant,user,reasoning,...
  messages   = 27
  empty      = 2
```

**`reasoning` is a non-standard role.** The model's Jinja template has branches
for `system`/`user`/`assistant`/`tool` only, so `reasoning` falls through to the
`else` branch → `raise_exception('Unexpected message role.')` at line 172.

Why it survived every guard:

| Guard | Why it missed |
|---|---|
| `_strip_reasoning_from_history()` | Runs on `history_to_send` **before** the request. But `_openai_chat_completions` is called with the **raw `history`** (line ~5050, the forced-summary path), which still contains `reasoning` entries. |
| `_flatten_for_plain_chat()` | Only special-cases `tool`. Every other role is copied through verbatim — including `reasoning`. |
| `_sanitize_message_roles()` | Would map it to `user`, but is only invoked on the **500** path, not the 400 path. |

So the 400 fallback flattened the conversation but **preserved the offending
role**, guaranteeing the retry failed identically. This also explains why the
one-shot guard then reported "persists after flattening" — correct behaviour,
wrong input.

**H3 (empty assistant turns) is real but secondary**: `empty = 2` confirms two
blank messages survive flattening. They are a plausible second trip, but
`reasoning` is the primary cause.

### 0.2 The hallucination root cause: the prompt budget is negative

```
prompt budget 4096 tokens (ctx 16384 - max_tokens 16384 - overhead 512)
```

`max_tokens` (16384) equals `ctx_size` (16384), so the budget computes to
**-512** and the floor kicks in at `ctx_size // 4 = 4096`.

The consequence is visible in the log:

```
trimmed prompt 4909 -> 4870 tokens (1 messages)
  messages   = 1
  roles      = system
  first user = (none)
```

**The user's message is trimmed away entirely.** Only the system prompt is sent.
The model therefore has no user turn at all — and invents one, drawing on
whatever the system prompt and its priors suggest (a temple, a cabin, a living
room). This is not stale history; it is **no history**.

This also explains why the hallucinated tasks varied between runs: the model is
free-associating from the system prompt alone.

**H1/H2 are REFUTED.** The loaded history was not the cause. The `first user`
line in the request-shape log showed `(none)` — the prompt was already gone
before the request was built.

### 0.3 Why the budget is negative

`local_max_tokens` defaults to **16384** and `local_ctx_size` to **32768**, but
the log shows `ctx 16384`. Either the user lowered the context window, or a
preset applied a smaller one. With `max_tokens == ctx_size` there is no room for
a prompt at all — the configuration is self-defeating.

---

## 1. Symptom (original report)

Two distinct failures, reported together:

1. **Hallucinated task.** The user asked for *"create a rounded corner ground mesh
   named Ground"*. The model's reasoning planned a **"modern minimalist living
   room"** — sofa, coffee table, TV stand, rug, `SM_Wall_North`, `COL_Furniture`.
   The user's actual request appears to be ignored.

2. **`400 Unexpected message role` still occurs** after the Phase 1/3 fixes
   (greeting `ui_only` + error-evidence preservation).

---

## 2. Verified findings

### 2.1 The hallucinated prompt is NOT in the codebase

`grep` for `living room`, `minimalist`, `sofa`, `coffee table`, `TV stand`,
`SM_Wall`, `COL_Furniture` across the repo finds **no such benchmark prompt or
system-prompt text**. The only matches are the *naming convention* skill
(`addon/bfa_coworker/skills/naming.md`), which documents `SM_`, `COL_`, `MAT_`,
`LGT_`, `CAM_` prefixes.

**Conclusion**: the model is inventing the task. See §0.2 — the user message is
trimmed away, so the model has nothing to answer.

### 2.2 Conversation history is persisted per blend file and reloaded wholesale

`addon/bfa_coworker/ui_chat.py`:

| Function | Behaviour |
|---|---|
| `_chat_history_dir()` | `<SCRIPTS>/bfa_coworker_chat_history/` |
| `_chat_history_path()` | `<dir>/<blend_stem>.json` (or `default.json`) |
| `_save_chat_history()` | Dumps `_agent_state.conversation_history` to disk, plus a timestamped copy (last 10 kept) |
| `_load_chat_history()` | Reads the JSON back |

And at `ui_chat.py:1613`, on agent start:

```python
history = _load_chat_history()
if history:
    agent_controller._agent_state.conversation_history = history
```

**The loaded history is assigned wholesale, with no validation.** This is a real
latent defect (a saved `reasoning` entry or orphaned tool call would be restored
verbatim), but it is **not** the cause of either reported symptom.

### 2.3 The 400 is explained — see §0.1

---

## 3. Hypotheses — final status

| # | Hypothesis | Status |
|---|---|---|
| H1 | Persisted history contains a stale user prompt | **REFUTED** — `first user = (none)`; the prompt was trimmed, not stale |
| H2 | Loaded history begins with an assistant message | **REFUTED** — same |
| H3 | Flatten leaves empty-content assistant turns | **CONFIRMED (secondary)** — `empty = 2` |
| H4 | Two consecutive `user` messages are rejected | **Not observed** — roles alternate correctly |
| **H5** | **`reasoning` role reaches the API and trips the template** | **CONFIRMED (primary 400 cause)** |
| **H6** | **Negative prompt budget trims the user message away** | **CONFIRMED (primary hallucination cause)** |

---

## 4. Plan

### Phase A — Make the request shape observable ✅ COMPLETE

Delivered `_count_empty_content_messages()`, `_describe_history_for_log()`, and
three log points (history load, per-request shape, flatten result). This is what
produced the evidence above.

### Phase B — Fix the two confirmed root causes

**B1. Strip non-standard roles in the flatten path (fixes the 400).**

- `_flatten_for_plain_chat()` must **drop** `reasoning` entries (they are
  UI-only chain-of-thought, already stripped on the normal path) and map any
  other unknown role to `user` — mirroring `_sanitize_message_roles()`.
- Add a final assertion-style guard: after flattening, if any role is outside
  `{system, user, assistant}`, log it loudly. The flattened output must be
  provably template-safe.
- Drop empty-content assistant turns (H3) — an assistant message with no content
  and no tool calls carries nothing.

**B2. Fix the prompt budget (fixes the hallucination).**

- The budget must never trim away the **current user message**. Reserve the last
  user turn unconditionally: trim only *older* exchanges.
- Clamp `max_tokens` so it cannot consume the whole context window. If
  `max_tokens >= ctx_size`, reduce it to leave room for the prompt (e.g. cap at
  `ctx_size // 2`) and log the adjustment.
- Guard the floor: `prompt_budget = max(_ctx_size // 4, 1024)` is too small to
  hold a system prompt (17042 chars ≈ 4870 tokens) plus a user turn. Raise the
  floor and warn when the configuration is self-defeating.
- Consider validating the preference pair in the UI: warn when
  `local_max_tokens >= local_ctx_size`.

**B3. Sanitize history on load (defensive, not a symptom fix).**

- New `_sanitize_loaded_history()`: drop `ui_only`, leading assistant,
  `reasoning`, and orphaned tool pairs; ensure it starts with the system prompt.
- Call it in `ui_chat.py` before assigning the loaded history.

### Phase C — Verify

- Re-run the benchmark suite; confirm the request-shape log shows a real
  `first user` line and no `reasoning` role.
- Confirm the 400 no longer occurs.
- Confirm the model answers the actual prompt.

### Phase D — Tests

- `_flatten_for_plain_chat` drops `reasoning` and maps unknown roles to `user`.
- Flattened output contains only `{system, user, assistant}`.
- `_fit_history_to_budget` never drops the last user message.
- `max_tokens >= ctx_size` is clamped, not left to produce a negative budget.
- `_sanitize_loaded_history` drops `ui_only`, leading assistant, reasoning,
  orphaned tool pairs.

---

## 5. Files

| File | Phase | Change |
|---|---|---|
| `addon/bfa_coworker/agent_controller.py` | A ✅, B | flatten role handling, budget clamp, `_sanitize_loaded_history` |
| `addon/bfa_coworker/ui_chat.py` | A ✅, B | sanitize the loaded history |
| `addon/bfa_coworker/preferences.py` | B | warn when `max_tokens >= ctx_size` |
| `tests/test_orchestration_helpers.py` | D | New tests |
| `CHANGELOG.md` | — | Entry under Fixed |

---

## 6. Open questions

1. **Should `max_tokens` be clamped silently or surfaced?** Recommend clamping
   with a visible warning — the user set a self-defeating pair.
2. **Should the budget reserve the last user turn, or fail loudly?** Recommend
   reserve; a turn with no user message is never useful.
3. **Is the 400 model-specific?** The `reasoning` role is non-standard for *any*
   template, so the fix is correct regardless — but the template's strictness
   varies.

---

## 7. Verification

1. `python -m unittest tests.test_orchestration_helpers -v`
2. Run the `scene_build` benchmark suite; confirm:
   - `first user = I'm setting up a scene to render...` (not `(none)`)
   - `roles` contains no `reasoning`
   - no 400
3. Confirm the model builds a ground mesh, not a temple/cabin/living room.

---

## 8. Audit & closure (2026-09-30)

Independent audit of this plan against the current source. **Every claimed
fix is present and wired** — nothing in the plan was left partial.

| Plan item | Claim | Verified in |
|---|---|---|
| 0.5.1 comma-import false positive | `_imports_module()` checks `import a, b` + `from x import` | `mcp_to_blender_server.py:365`; used for bpy (`:401`) and bmesh (`:676`); `tests/test_preflight.py` |
| 0.5.2 full tool result stored | `_MAX_TOOL_RESULT_CHARS` = 2000; store full, trim at send | `agent_controller.py:126`, `_trim_history_tool_results()` `:614`; `tests/test_orchestration_helpers.py::TestTrimHistoryToolResults` `:1018` |
| 0.5.3 malformed tool-call 500 | `_FAULT_TOOLCALL` class + marker ordering + one-shot nudge | `llm_transport.py:147,213,546`; `_toolcall_fault_message()` |
| 0.5.4 recovery actually recovers | nudge says "split the work"; one-shot guard surfaces immediately; auto-continue sanitizes + forwards thinking budget | `llm_transport.py:546,760`; `agent_controller.py:5065-5067` |
| B1 flatten drops `reasoning`, maps unknown → `user`, drops empty | yes | `_flatten_for_plain_chat()` `agent_controller.py:767` (reasoning drop `:801`) |
| B2 budget reserves last user turn + clamps `max_tokens` | yes | `_fit_history_to_budget()` (pins last user turn); `_compute_prompt_budget()` clamps `max_tokens` |
| B3 sanitize history on load | `_sanitize_loaded_history()` called before assignment | `agent_controller.py:571`; `ui_chat.py:1694` |
| D tests | orchestration-helper coverage | `tests/test_orchestration_helpers.py`, `tests/test_preflight.py` |

**Regression status**: the whole `tests/test_orchestration_helpers.py` and
`tests/test_context_budget.py` suites pass (see CI). The 400 root cause
(`reasoning` role reaching the template) and the hallucination root cause
(negative prompt budget trimming the user turn away) are both closed.

**Related follow-up (same area, later branch)**: the transport-400 graceful
handling and budget hardening for live llama-server context overflows are in
`plan_tier3_session_memory_checkpoints.md` and the scene-safety plan — this
document covers the *reasoning-role / negative-budget* 400 only.

