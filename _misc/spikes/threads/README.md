# Threads 0 spike (#356)

Throwaway measuring scripts for `_misc/plan_tier4o_orchestration_threads.md` §6–7.
Not shipped, nothing here is imported by the add-on. Results from the run on
2026-11-07 are in 4o §6.

| Script | Runs in | What it measures |
|---|---|---|
| `bridge_interleave_spike.py` | Bforartists (GUI) | Two `engine.FakeEngine` turns calling tools through `mcp_to_blender_server` at once: serialization, main thread, the `bpy.app.timers` path, named undo steps when they interleave, the loop's one-step failure undo, Revert, and today's single-slot `coworker_api` turn checkpoint |
| `acp_sessions_spike.py` | plain Python | Two `session/new` sessions on one ACP agent process (`acp_client`): concurrent or serialized, and whether `session/cancel` on one leaves the other alone |
| `llama_slots_spike.py` | plain Python | llama-server with `auto` (today) / `-np 1` / `2` / `3`: slots and context per slot, decode tok/s per request with 1..N at once, a prompt bigger than ctx/N, a small exact-answer quiz solo vs N at once |

## Rerun

Use absolute paths; put output outside the repo.

```sh
# 1. Bridge + undo (one launch, quits itself ~3 s after the last check).
#    Take the shared live lock first; remove it after the app quits.
mkdir "$TEMP/bfa_live.lock" && \
  bforartists --factory-startup --python _misc/spikes/threads/bridge_interleave_spike.py -- <out_dir>; \
  rmdir "$TEMP/bfa_live.lock"
#    -> <out_dir>/bridge_results.json

# 2. ACP sessions (needs claude-agent-acp and/or opencode on PATH, logged in).
#    Picks the cheapest model the agent lists (Claude Code: haiku); tiny prompts.
python -I _misc/spikes/threads/acp_sessions_spike.py <out_dir> claude_code opencode
#    -> <out_dir>/acp_results.json

# 3. Local slots (any GGUF; ctx is the TOTAL --ctx-size).
python -I _misc/spikes/threads/llama_slots_spike.py \
  ~/.cache/bfa_coworker_llama/llama-server-cuda.exe <model.gguf> <out_dir> 32768 auto 1 2 3
#    -> <out_dir>/slots_<model>.json (+ one server log per setting)
```

The bridge script imports the add-on package from this checkout (`addon/`) without
registering it, starts the bridge on a free port near 19876, and turns the session
token check off for its own in-process client. It never touches your installed
Coworker or its ports.
