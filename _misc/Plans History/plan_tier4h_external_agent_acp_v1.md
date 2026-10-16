# Tier 4h — External Agent Chat Inside Blender (ACP)

**Status**: 📝 Draft (research done 2026-10-06; not scheduled — after 1.1.37)
**Related**: `plan_tier4_master_coordination.md`, `plan_tier4f_opencode_comparison.md`,
External Harness mode (`shared.py` `_HARNESS_PRESETS`, `harness_testing_guide.md`)

## 1. Goal

In External Harness mode today the external tool drives and Blender is only a tool
server, so the in-Blender chat (streaming, Workshop, Goal & Plan, images, queue)
disappears. Goal: keep chatting **from the Blender interface** while the work is
done by the user's own agent and **subscription** (Claude, ChatGPT/Codex, Gemini,
OpenCode, Copilot, ...).

Desired user flow:

1. Pick **External Agent** mode and an agent preset (e.g. "Claude Code").
2. Coworker checks the agent CLI is installed and logged in (shows install hints if not).
3. Press **Start**: the bridge + MCP server start, the agent is launched, and the
   Blender MCP server is handed to it automatically (no manual MCP config needed —
   the existing "copy MCP config" flow stays as a fallback).
4. Chat in the normal Coworker panel.

## 2. Approach: Blender as an ACP client

The **Agent Client Protocol** (ACP, created by Zed) is a JSON-RPC 2.0 protocol over
stdio in which the *editor* launches the agent as a subprocess and drives it. Blender
becomes the editor-side client.

| ACP | Coworker |
| --- | --- |
| `initialize` (protocol version, capabilities) | On Start; we do **not** advertise `fs`/`terminal` (the agent works through our MCP tools) |
| `session/new` with `mcpServers` | Pass the managed `bfa-coworker` MCP server (HTTP, or stdio command) — this is the "MCP added to the harness" step, done for the user |
| `session/load` (if `loadSession` capability) | Resume after a Blender restart |
| `session/prompt` (text + image content blocks) | Send button; the image socket feeds image blocks when the agent reports `promptCapabilities.image` |
| `session/update` → `agent_message_chunk` | Live readout / final reply |
| `session/update` → thought chunks | Workshop reasoning |
| `session/update` → `tool_call` / `tool_call_update` | Workshop tool entries (status, result) |
| `session/update` → `plan` | **Goal & Plan** steps (`GoalPlan.apply_update`) |
| `session/request_permission` | Inline **Allow once / Always / Deny** row in the chat panel (no modal popups) |
| `session/cancel` | Stop button |
| `session/set_mode` / model selection | Optional selectors (phase 2) |

Agents with ACP today (2026-10): Claude Code (`claude-agent-acp` adapter), Codex
(`codex-acp`, community), Gemini CLI (`gemini --experimental-acp`), OpenCode
(`opencode`), GitHub Copilot CLI (`copilot`, preview), Cursor (`agent`), Goose,
Kiro, Mistral Vibe, Qwen Code, Cline. Neovim's CodeCompanion is an existing ACP
client for the same set and is a useful reference.

**Fallbacks** (only if a key agent lacks ACP): Claude Code headless
`claude -p --input-format stream-json --output-format stream-json --mcp-config ...`
(bidirectional stream, resumable); OpenCode `opencode serve` HTTP API + `/event` SSE.

## 3. What is kept and what is shed in this mode

**Kept**: chat panel, live streaming, Workshop, Goal & Plan (fed by the agent's plan),
image attachments, Queue, Stop, session log export, all Blender MCP tools, the bridge,
crash-safe process handling (`process_guard`), co-work rules delivered through the
MCP server `instructions`.

**Shed** (owned by the agent): our turn loop — compaction/memory/checkpoints, stop
rules and nudges, domain tool filtering, prompt calibration, llama-server and remote
API settings. The Session panel shows only what applies (or hides).

## 4. Phases

1. **MVP** (~1-2 weeks): `acp_client.py` (bpy-free, stdlib JSON-RPC over stdio on a
   worker thread; no new vendored deps), agent presets + install/login detection,
   session lifecycle, streaming into existing chat state, Workshop mapping, inline
   permissions, Stop, image blocks. Tests against a **fake ACP agent** script (like
   the fake llama-server harness).
2. **Polish**: `session/load` resume, plan → Goal & Plan, mode/model selectors,
   per-agent "supported / preview" badges, docs + wiki page.
3. **Optional**: Claude headless / OpenCode server fallbacks if needed.

## 5. Risks / open questions

- Users must install the agent CLI and, for Claude/Codex, an adapter (npm → Node).
- Adapter maturity varies (Copilot preview; `codex-acp` community-maintained).
- Cannot attach to a chat **already open** in VS Code / Claude Desktop — Blender
  starts its own agent session (same login, settings and MCP config).
- **Test targets available to the maintainer**: OpenCode (native ACP — first target);
  Claude Desktop is an MCP *client* app, not an ACP agent — use Claude Code via
  `claude-agent-acp` with the same subscription; Freebuff — ACP support to verify
  (otherwise stays on the current External Harness flow).
- Permission UX inside a Blender panel; long-running tool calls vs. UI timers.
- Windows: launching `.cmd` shims (npm) via `subprocess`, PATH discovery.

## 6. Sources

- https://agentclientprotocol.com/overview/agents
- https://agentclientprotocol.com/registry
- https://www.philschmid.de/acp-overview
- https://codecompanion.olimorris.dev/configuration/adapters-acp
- https://zed.dev/docs/ai/external-agents
- https://code.claude.com/docs/en/headless
- https://opencode.ai/docs/server/
