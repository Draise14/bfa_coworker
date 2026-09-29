# Manual Verification Checklist — Streaming Remote/Local Chat (tier 3)

Automated coverage (172 unit tests) exercises the streaming transport,
stop-abort, fallback, usage capture, elapsed status, and the rendered-state
wiring at the module level. It cannot exercise Blender's UI loop or a real
provider's SSE quirks. Run this checklist once per release candidate.

## 1. Local mode (llama-server) — full turn

1. Start a local model, open the Coworker panel (3D View sidebar).
2. Send a prompt that requires one tool call (e.g. "add a cube").
   - [ ] Workshop shows reasoning live while the model thinks (if the model
     emits reasoning) and text builds up word-by-word, not all at once.
   - [ ] Status shows `Contacting <model>...` then `Reasoning... (Ns)` /
     `Generating... (Ns)` with a ticking second counter.
   - [ ] Completed message in history is complete (no truncated tail).
   - [ ] Console shows `usage prompt=... completion=... total=...`.
   - [ ] Status & Diagnostics panel: per-turn and session token counters
     incremented.

## 2. Local mode — Stop mid-generation

1. Send a long generation prompt, click Stop after ~2 s.
   - [ ] Stream aborts within a second (console: `stop requested mid-stream`).
   - [ ] Partial text stays in the panel, marked as partial.
   - [ ] Next turn works normally (no stuck spinner).

## 3. Remote API mode (OpenRouter/OpenAI)

1. Configure a remote key + model, send a tool-call prompt.
   - [ ] Same live streaming behavior as local mode.
   - [ ] `Reasoning... (Ns)` appears while the reasoning model thinks.
   - [ ] Token usage appears in Status & Diagnostics (remote providers send
     `usage` on the final chunk).
2. Set an intentionally invalid `stream_options`-hostile model (or observe
   the console) — a provider that rejects streaming must fall back to the
   non-streaming path with no user-visible failure (console:
   `falling back to non-streaming`).
3. Kill network mid-generation.
   - [ ] Console: `stream dropped mid-generation ... returning partial`;
     warning text appears; partial content kept.

## 4. External Harness mode

1. Switch to External Harness mode.
   - [ ] Queue panel is hidden.
   - [ ] Chat header shows mode label only (no model name).

## 5. Status & Diagnostics

- [ ] Model line shows the mode-correct model (local file/preset in Local,
  remote model in Remote API); never shown in the chat panel header.
- [ ] Token usage section renders per-turn + session totals; session log
  includes the usage section after a turn.

## 6. Failure taxonomy (local 500s — exercised by unit tests, spot-check live)

- [ ] OOM-style 500 surfaces the server-fault message with the llama-server
  log tail (no silent downgrade to text tool calling).
- [ ] Template-fault 500 downgrades once to text-based tool calling with the
  warning banner; behavior recovers on the next turn.
