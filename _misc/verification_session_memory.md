# Verification Checklist — Tier 3: Local Session Memory & Context Checkpoints

Items that can only be exercised inside Blender/Bforartists with a live
llama-server. Everything testable outside Blender is already covered by the
unit suite (`python -m unittest tests.test_context_budget
tests.test_session_memory tests.test_llm_manager
tests.test_turn_loop_integration` — 255 tests green). The integration
harness in `tests/test_turn_loop_integration.py` additionally drives a real
`run_conversation_turn` against a fake llama-server HTTP endpoint and proves,
behaviourally and in order: prompt preflight shapes the request, the ~60%
compaction trigger fires with a real memory-writer LLM call, reasoning
entries are pruned from stored history once outside the verbatim window, a
`reason="compaction"` checkpoint is recorded, and the memory block is
injected into the following request exactly once (no accumulation in the
stored system prompt).

## Setup

- [ ] Start Bforartists, enable the Coworker addon, download/launch a local
      model (any Qwen-family preset recommended), start the Coworker agent.

## Phase 1 — Budget against the real context

- [ ] Watch the Blender console on the first turn: the log line
      `prompt budget ... tokens (ctx N, max_tokens M)` shows N matching what
      llama-server actually applied (check the llama-server console window
      for `n_ctx`), not merely the configured preference.
- [ ] On a 16K context, run a long session (20+ turns, several tool calls).
      No server 400 ("context window exceeded" / "request too large") should
      appear; if the prompt truly cannot fit, the chat shows the friendly
      "conversation no longer fits the local context window — compacting"
      error instead of a raw traceback.

## Phase 2 — Runtime ctx sizing, no silent restart

- [ ] Set Context Size to 8K in Preferences and start the local LLM: the
      console warns that 8K is small for agent work, and the server starts
      with 8192 (verify `n_ctx` in the llama-server window) — no silent
      auto-upgrade to 32768 anymore, and no mid-session restart.
- [ ] With a large model + large context that exceeds detected memory, the
      Preferences Context Window box shows the "Context may not fit your
      hardware" error lines; lowering the context or enabling KV
      quantization clears it.

## Phase 3 — Qwen / GPU launch flags

- [ ] Start a Qwen-family preset: the llama-server console shows
      `--no-context-shift` among the launch args; a non-Qwen preset (e.g.
      GPT-OSS or Gemma) does not.
- [ ] On a CUDA/Vulkan backend the launch args include `--flash-attn`,
      `--batch-size 2048`, `--ubatch-size 512`, `--cache-reuse 256`; with
      "Quantize KV Cache (q8_0)" enabled they also include
      `--cache-type-k q8_0 --cache-type-v q8_0`.
- [ ] With a custom/older llama-server binary (source = custom), startup
      still succeeds — unsupported flags are dropped with a
      `_filter_flags_for_build: dropping ...` console line instead of
      crashing the server.
- [ ] CPU backend: no `--flash-attn` / KV-quant flags are added.

## Phase 4/5 — Session memory, checkpoints, UI

- [ ] Chat until the Session section's context bar approaches ~60% (or the
      history exceeds 20 messages): status shows "Compacting conversation…",
      the console logs `retired N messages, archived, checkpoint saved`, and
      the chat keeps working with earlier context still summarized in the
      memory note (ask "what did we do earlier?" — the agent should recall
      the goal/objects from the memory block).
- [ ] The Session section shows a context-usage percentage bar that rises
      during long sessions and a memory preview line.
- [ ] "View / Edit Memory" opens the current memory block; editing and
      confirming applies it (the next reply reflects the edited note).
- [ ] The checkpoint list grows automatically (one per compaction); pick one
      and press **Restore**: the conversation returns to that point and the
      prior state appears as a new "pre-restore" checkpoint. Press
      **Branch**: the current thread is replaced by the checkpoint's
      history without adding a pre-restore snapshot.
- [ ] **Compact Now** retires old turns immediately, reports how many
      messages were retired, and the chat history panel shows the shorter
      window.
- [ ] Close and reopen Bforartists (same .blend / default session): the
      chat history is restored; the memory note survives within the
      session JSON payload, and `archive.jsonl` exists next to the chat
      history JSON with the retired turns.

## Phase 6 — poll() error collapse

- [ ] Have the agent call an operator with a wrong context (e.g. ask it to
      shade-smooth with no active object). The stored tool result shows the
      one-line "failed its poll() check — ... use bpy.context.temp_override()"
      hint, not the full Python traceback; the agent's corrective next step
      references the override pattern.

## Regression

- [ ] New Thread (chat_clear) still works; remote-API mode still works
      (no budget trimming, no compaction trigger, Session section shows no
      context bar).
