# Tier 4h — External Agent: Chat in Blender, Work Done by Your Own Agent (ACP)

**Revised**: 2026-10-16. **Priority: first Tier 4 lane** (was "draft, not scheduled")
**Status**: 📝 Ready for review — no code started
**Previous version**: `Plans History/plan_tier4h_external_agent_acp_v1.md`
**Depends on**: Tier 4i engine seam (§4i.1). The two are built together, see `plan_tier4i_engine_parity.md`
**Tasks**: `plan_tier4_task_board.md` lane H

---

## 1. Goal

Today **External Harness** mode turns Blender into a tool server. The chat happens in
Claude Desktop / VS Code / a terminal, and the Coworker panel (streaming, Workshop,
Goal & Plan, images, queue, checkpoints) is gone.

**External Agent** mode keeps the user **in the Coworker panel** while the work is done
by **their own agent and subscription**: Claude Code, Codex, Gemini CLI, OpenCode,
Copilot CLI and others that speak the Agent Client Protocol (ACP). Blender is the
*client* (the "editor" in ACP terms) and launches the agent as a subprocess.

**User flow (the whole UX):**

1. Mode selector: **Local LLM · Remote API · External Agent · External Harness**. The
   names stay; tooltips explain them in plain words. *External Agent*: "Chat here in
   Bforartists; your own Claude / ChatGPT / Gemini / OpenCode does the work".
   *External Harness*: "Chat in another app; it uses Bforartists' tools".
2. External Agent → pick a preset (e.g. *Claude Code*). Coworker checks: installed?
   version? logged in? and shows one install/login hint line if not.
3. **Start**: bridge + MCP server start, the agent launches, and our MCP server is
   handed to it automatically (no JSON editing).
4. Chat as usual. The engine chip reads *Agent · Claude Code*. Approvals appear inline.
   Stop works.

**Done when**: on Windows, with OpenCode and with Claude Code (via its ACP adapter), a
user goes from a clean install to "make a low-poly campfire" in the Coworker panel, with
live text, Workshop tool rows, plan ticks, an inline approval and a working Stop, and
never edits a config file.

---

## 2. Why this lane is first

- It is the largest UX gap: users who pay for a frontier subscription currently get the
  *worst* Coworker experience (no panel).
- It forces the **engine seam** (Tier 4i) that makes Local, Remote and External behave
  the same. Building it later would mean retrofitting every 4b/4c feature.
- Domain tools (4g) give a frontier agent more to work with than they give a 7B model,
  so 4h + 4g together show the fastest visible improvement.
- Risk is contained: the work is a new module (`acp_client.py`) plus a new engine behind
  the existing UI, with no rewrite of the local loop.

---

## 3. Protocol mapping (ACP → Coworker)

ACP is JSON-RPC 2.0 over the agent's stdin/stdout. Coworker is the client.

| ACP | Direction | Coworker behaviour |
|---|---|---|
| `initialize` (protocolVersion, clientCapabilities) | → agent | Sent on Start. We advertise **no** `fs` and **no** `terminal` capability, so the agent works through our MCP tools. Read back `agentCapabilities` (`loadSession`, `promptCapabilities.image`, `mcpCapabilities.http`) and `authMethods`. |
| `authenticate` | → agent | Only if the agent requires it and offers a method. Normally the CLI is already logged in; otherwise show the agent's own login hint ("run `claude` once and log in"). |
| `session/new` {cwd, mcpServers} | → agent | `cwd` = project folder (see §5.3). `mcpServers` = **our server as stdio**: `{name: "bfa-coworker", command: <blender python>, args: ["-m","blmcp","--transport","stdio"], env: {BFACW_TOKEN…}}`. Stdio is the one MCP transport every ACP agent must support; use HTTP only when `mcpCapabilities.http` is true. |
| `session/load` | → agent | Resume after a restart when `loadSession` is supported; otherwise a new session seeded with the resume brief (4b B3). |
| `session/prompt` {prompt: content blocks} | → agent | Send. Text + context chip + attached images (only if `promptCapabilities.image`; otherwise a one-line warning and text only). The response's `stopReason` ends the turn. |
| `session/update` `agent_message_chunk` | ← agent | Live reply text (`AgentEvent.text`). |
| `session/update` `agent_thought_chunk` | ← agent | Workshop reasoning (`AgentEvent.thought`). |
| `session/update` `tool_call` / `tool_call_update` | ← agent | Workshop tool rows: title, kind, status pending→in_progress→completed/failed, short content. Our own MCP calls also show up on the bridge side, so we match them to avoid duplicate rows (§5.4). |
| `session/update` `plan` | ← agent | Goal & Plan steps (`GoalPlan.apply_update`): entries with status. The plan panel ticks live. |
| `session/update` `available_commands_update` | ← agent | Optional: the agent's slash commands shown in the `/` menu (phase 2). |
| `session/update` `current_mode_update` | ← agent | Mode chip (e.g. *plan* / *build*) (phase 2). |
| `session/request_permission` {toolCall, options} | ← agent (request) | **Inline approval row** in the chat: the option kinds `allow_once / allow_always / reject_once / reject_always` map to our shared `ApprovalRequest` (4i.3). No modal popups. Timeout → reject_once. |
| `session/cancel` | → agent (notification) | Stop button. Pending permission requests are answered `cancelled`. |
| `session/set_mode`, model selection | → agent | Optional selectors (phase 2), shown only if the agent advertises them. |

> Method and field names follow the ACP spec as of 2026-10. The **4h.0 spike records real
> transcripts** from OpenCode and the Claude adapter as test fixtures; implementation
> follows the fixtures, not this table, wherever they differ.

---

## 4. What applies in this mode (and what doesn't)

The agent owns its turn loop, so features split into three groups:

| Kept as is (Blender-side) | Fed from the agent | Owned by the agent (hidden) |
|---|---|---|
| Chat panel, Workshop, image socket, Queue, Stop, session log export | Reply/thought stream, tool rows, Goal & Plan (from `plan` updates **and** our `update_plan` MCP tool), approvals, usage (if reported) | Compaction/memory, stop rules and nudges, model/temperature, llama-server and remote API settings |
| All MCP tools, preflight, autofix, co-work lock, scene undo checkpoints, bridge, `process_guard` | | Local domain filtering (replaced by server-side progressive tools, 4i.4) |
| Co-work rules via MCP `instructions` + project `AGENTS.md` (§5.3) | | |

The Session panel shows only what applies: in External Agent mode, *Context* shows the
agent-reported usage (or "not reported"), and *Memory & Checkpoints* shows **scene**
checkpoints only.

---

## 5. Design details

### 5.1 `acp_client.py` (bpy-free)

- Stdlib only: `subprocess` + a reader thread + a writer lock; newline-delimited JSON-RPC.
  It handles requests, responses, notifications, request ids, and agent→client requests
  (permission, plus `fs/*`, which we answer with method-not-found since we don't
  advertise it).
- Emits `AgentEvent`s into a thread-safe queue. A `bpy.app.timers` drain applies them to
  `AgentState` on the main thread, the same pattern as today's streaming. **Nothing on
  the reader thread touches `bpy`.**
- Stderr is captured to the Coworker log (Debug mode shows it). Startup or crash errors
  surface as one clear status line.
- Under `process_guard`, the agent dies with Blender (Windows job object / Linux PDEATHSIG).
- Unit-tested against `tests/fake_acp_agent.py`, a scripted agent that replays the
  recorded fixtures, like the fake llama-server harness.

### 5.2 Agent presets + detection

`AgentPreset(identifier, name, command, args, detect, version_args, login_hint, docs_url,
supports: {image, loadSession, http}, maturity: supported|preview)`.

| Preset | Launch (verify in 4h.0) | Notes |
|---|---|---|
| OpenCode | `opencode acp` | Native ACP; **first test target** (maintainer has it) |
| Claude Code | `claude-agent-acp` adapter (npm) → uses the user's Claude login | Needs Node; Claude Desktop is *not* an ACP agent |
| Codex CLI | `codex-acp` adapter | Check maintainer/maturity |
| Gemini CLI | `gemini --experimental-acp` | Flag may graduate |
| GitHub Copilot CLI | ACP mode (preview) | Preview badge |
| Generic | user-supplied command + args | Escape hatch; also covers Goose, Kiro, Qwen Code, Cline, Mistral Vibe, Freebuff if they speak ACP |

Detection: `shutil.which` (PATHEXT-aware), then `--version` with a 5 s timeout. Results
are cached per session and re-checked on Start. **Windows**: npm installs `.cmd` shims.
Resolve the full path and, where the shim points at a Node script, launch `node <script>`
directly. Otherwise pass arguments as a list and never build a shell string (`.cmd`
argument escaping is unsafe). Login state is best-effort: if `session/new` fails with an
auth error, show the preset's `login_hint`.

### 5.3 Project folder and instructions (token-smart)

- `cwd` = the folder of the saved `.blend`. Unsaved files use
  `~/.cache/bfa_coworker/acp/<session>/`. The agent's own file tools then see the
  project, not the user's home folder.
- On Start, Coworker writes a **compact `AGENTS.md`** (and `CLAUDE.md` pointing to it) into a
  `.coworker/` subfolder. It never overwrites the user's own files, and the content is
  generated from `prompts_compact.yml`, around 400 tokens: who you are working with,
  use the bfa-coworker tools, read the scene digest first, prefer domain tools over
  `execute_blender_code`, ask with `ask_user` when a choice matters, keep the plan
  updated with `update_plan`. The MCP server `instructions` carry the same text for
  agents that ignore files.
- Option **"Only Coworker tools"** (default on): where the agent supports it, start it
  with only our MCP server (like claude-in-blender's strict MCP config), so other MCP
  servers don't inflate its context. Document per preset what is possible.

### 5.4 Workshop mapping without duplicates

The agent reports a `tool_call` and our MCP server also sees the call arrive. Each MCP
call carries the tool name and arguments. Match the `tool_call_update` to the bridge-side
execution by name + argument hash within the turn, and show **one** row: the agent's
title with our structured result summary. Agent-internal tools (its own file reads, web
search) show as plain rows with the agent's status only.

### 5.5 Security

- **MCP session token** (4i.6): the MCP HTTP endpoint and the bridge accept a per-start
  random token, which ACP agents receive via `env`. Copy MCP Config includes it for
  External Harness mode. This closes "any local process can drive Blender".
- Code execution is gated by the shared permission setting (4f.3): default **Ask** for
  `execute_blender_code` on External Agent? → open question 2.
- Pressing Send counts as approval for the read-only tools in that turn. Write tools follow
  the approval policy.

---

## 6. Phases

| Phase | What | Size | Done when |
|---|---|---|---|
| **4h.0 Spike** | Run `opencode acp` and the Claude adapter by hand from a Python script on Windows. Record JSON transcripts (init → new → prompt with tool use → permission → cancel). Confirm stdio `mcpServers` launches `blmcp`. Note real field names. | S (1–2 days) | Fixtures committed in `tests/fixtures/acp/`; table §3 corrected |
| **4h.1 Engine seam** | `AgentEvent` + `Engine` protocol; Local/Remote wrapped with no behaviour change (this is 4i.1) | M | All existing tests green; UI renders from events |
| **4h.2 ACP client core** | `acp_client.py` + `fake_acp_agent.py` + unit tests | M | Fake agent conversation passes incl. cancel and crash |
| **4h.3 Presets + detection** | Presets, detection, install/login hints, Generic preset | S | Each preset shows correct status on a machine without it |
| **4h.4 Chat integration** | `ExternalAgentEngine`: prompt (text+images), stream, Workshop, plan, Stop, Queue, engine chip | M | Done-when of §1 with OpenCode |
| **4h.5 Approvals** | Inline approval row via shared `ApprovalRequest` | S | Permission request answered inline; timeout safe |
| **4h.6 Sessions + project** | cwd, `.coworker/AGENTS.md`, `session/load` or resume brief, session index entry | S | Restart Blender → continue the same work |
| **4h.7 Hardening** | MCP token (4i.6), duplicate-row matching, stderr surfacing, process_guard, Windows shims | S | Adversarial checklist passes (kill agent mid-turn, kill Blender, bad command, no login) |
| **4h.8 Polish** | Modes/models selectors, slash commands, maturity badges, wiki page + harness guide update | S | Docs reviewed |
| **4h.9 Fallbacks (only if needed)** | Claude headless stream-json; OpenCode `serve` HTTP/SSE | M | Only if a key agent lacks usable ACP |

Total: roughly 3–4 weeks of focused work, of which 4h.1 (the seam) is the riskiest refactor.

---

## 7. Risks and open questions

| Risk | Mitigation |
|---|---|
| Adapter maturity varies (Copilot preview, Codex adapter) | Maturity badges; OpenCode + Claude as "supported", others "preview" |
| Node required for npm adapters | Detection explains it in one line; the installer (#86) may offer it later |
| Cannot attach to a chat already open in VS Code / Claude Desktop | State it in the docs. Blender starts its own session with the same login and settings |
| Long tool calls vs UI | Already solved by deferred tools; ACP stays async |
| Agent ignores `ask_user` / `update_plan` | Instructions + tool descriptions; plan also arrives via ACP `plan` updates |
| Two sources of truth for the plan (agent `plan` vs our `update_plan`) | Last writer wins, shown in the plan panel with a source label |
| Usage/cost not reported by all agents | Show "usage not reported by this agent" instead of zeros |

**Review decisions (2026-10-16)**

1. Mode naming: ✅ **keep "External Harness"** (MCP only, chat elsewhere) and add
   **"External Agent"** (ACP, chat in Blender). No rename; tooltips explain both (§1).
2. Code execution on External Agent: ✅ **Allow**, backed by the co-work guard and the
   turn-start scene checkpoint (Revert turn, 4b B4).
3. "Only Coworker tools": ✅ **on by default**, user can turn it off.
4. Session token: ✅ **on by default** (4i.6).
5. Still open: test matrix beyond OpenCode + Claude Code. Is Freebuff an ACP agent?
   (verify in 4h.0).

---

## 8. Sources

- https://agentclientprotocol.com (overview, protocol, schema, registry)
- https://zed.dev/docs/ai/external-agents
- https://codecompanion.olimorris.dev/configuration/adapters-acp (an existing non-Zed ACP client)
- https://opencode.ai/docs (ACP + server)
- https://code.claude.com/docs/en/headless (fallback only)
