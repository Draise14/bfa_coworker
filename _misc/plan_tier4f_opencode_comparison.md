# BFA Coworker — Tier 4f: OpenCode Harness Comparison

**Date**: 2026-09-14
**Status**: Research complete — feeds `plan_tier4f_agent_intelligence.md`
**Reference**: [anomalyco/opencode](https://github.com/anomalyco/opencode) (MIT, TypeScript/Bun, v1.18.31)
**Purpose**: Extract the transferable architecture from a mature open-source coding agent and map it onto BFA Coworker's local-first Blender agent.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What OpenCode Actually Is](#2-what-opencode-actually-is)
3. [OpenCode Architecture Deep-Dive](#3-opencode-architecture-deep-dive)
4. [BFA Coworker Current State](#4-bfa-coworker-current-state)
5. [Gap Matrix](#5-gap-matrix)
6. [Where We Can Beat OpenCode](#6-where-we-can-beat-opencode)
7. [Deliberate Non-Goals](#7-deliberate-non-goals)
8. [Transferable Patterns Summary](#8-transferable-patterns-summary)

---

## 1. Executive Summary

OpenCode is the most architecturally mature open-source coding agent available. It is
**not** a local-inference system — it is a *client* that talks to llama-server, Ollama,
LM Studio, or 75+ remote providers through the Vercel AI SDK. It does **no GGUF-level
tuning whatsoever**.

That is the key insight for this plan:

| Layer | OpenCode | BFA Coworker |
|---|---|---|
| **Inference (GGUF/llama.cpp)** | ❌ Delegates entirely to an external server | ✅ **Owns llama-server** — can tune flags, KV cache, batching |
| **Orchestration (agents/subagents)** | ✅ Mature (primary/subagent, Task tool, planner) | ❌ Single-agent loop only |
| **Context management** | ✅ Mature (compaction, pruning, managed output files) | 🟡 Basic (hard slice + lossy trim) |
| **Permissions / safety** | ✅ Mature (allow/ask/deny, doom-loop guard) | 🟡 Partial (spiral detection only) |
| **Provider abstraction** | ✅ 75+ providers, model catalog | 🟡 OpenRouter + OpenAI-compatible |
| **Domain tooling** | ❌ Generic file/bash tools | ✅ **48+ Blender-specific tools** |

**Conclusion**: Adopt OpenCode's *orchestration, context, and permission* systems.
Ignore its provider abstraction (we don't need 75 providers). And go **beyond** it on
inference tuning, because we control the server it can't reach.

---

## 2. What OpenCode Actually Is

- **Language**: TypeScript (75%), Bun runtime, MDX docs (21.7%)
- **License**: MIT
- **Scale**: 207k stars, 27.2k forks, 1,007 contributors, 873 releases
- **Distribution**: TUI, desktop app (Electron), IDE extension, web, GitHub Action
- **Package layout** (monorepo, `packages/`):

| Package | Role |
|---|---|
| `core` | Session runtime, tool registry, permissions, filesystem |
| `server` | HTTP API host, middleware, handlers |
| `protocol` | Endpoint construction, payloads, envelopes, cursors, streams |
| `schema` | Shared semantic values (lightweight leaf) |
| `client` | Generated Promise + Effect clients |
| `sdk` / `sdk-next` | Embedded in-process host |
| `llm` | Provider protocol adapters |
| `plugin` | Plugin system |
| `tui` / `app` / `desktop` / `web` | Front-ends |
| `codemode` | OpenAPI tool adapter |

**Dependency direction** (from `AGENTS.md`): `Schema → Core/Protocol → Server`.
Client may depend on Schema + Protocol but **never** Core or Server.

---

## 3. OpenCode Architecture Deep-Dive

### 3.1 Session Runtime V2 — the context algebra

This is the most sophisticated part and the most transferable. OpenCode models the
system prompt not as a string but as a **composed, typed, versioned value**.

| Concept | Definition |
|---|---|
| **System Context** | Structured collection of contextual facts presented to the model |
| **Context Source** | One independently observed typed value: stable key, JSON codec, infallible loader, pure baseline/update/removal renderers |
| **System Context Registry** | Location-scoped registry of ordered, scoped producers |
| **Context Epoch** | Span during which one rendered System Context remains the immutable provider-cache baseline |
| **Baseline System Context** | The full System Context rendered at the start of an epoch |
| **Context Snapshot** | Model-hidden JSON state used to diff each source against what was last admitted |
| **Mid-Conversation System Message** | Durable chronological instruction telling the model the newly effective state of a changed source |
| **Safe Provider-Turn Boundary** | Point immediately before a provider call where context changes may be admitted |
| **Admitted Prompt** | Durable user input accepted into the inbox but not yet in history |
| **Prompt Promotion** | Transition that moves an admitted prompt into visible history |
| **Session Drain** | One process-local execution span; no durable identity |
| **Model Tool Output** | Bounded projection of a tool result persisted in history |
| **Managed Tool Output File** | Temp file holding full output too large for history |

**Key relationships worth stealing:**

- Context changes are **sampled lazily at a Safe Provider-Turn Boundary**, never pushed
  asynchronously. This keeps the provider cache prefix stable.
- The first turn renders the baseline and initializes the snapshot **without** emitting a
  redundant update message.
- **Compaction starts a new Context Epoch** with a freshly rendered baseline; prior
  mid-conversation messages remain durable audit history but leave model history.
- Unavailable context uses **stale-while-revalidate** semantics — distinct from a
  successfully loaded absence (which emits removal text).
- **Steering vs queueing**: prompts steer by default and promote at the next safe
  boundary; an explicit `queue` input waits until the session would otherwise go idle.
- Promoting any new user input **resets the agent's provider-turn allowance**.

### 3.2 Agents — primary, subagent, hidden

| Agent | Mode | Purpose |
|---|---|---|
| `build` | primary | Full tool access, default |
| `plan` | primary | Read-only; edits + bash set to `ask` |
| `general` | subagent | Multi-step research, full tools except todo |
| `explore` | subagent | Fast read-only codebase exploration |
| `scout` | subagent | External docs / dependency research |
| `compaction` | primary (hidden) | Compacts long context |
| `title` | primary (hidden) | Generates session titles |
| `summary` | primary (hidden) | Creates session summaries |

**Per-agent options**: `description`, `temperature`, `steps` (max agentic iterations),
`disable`, `prompt`, `model`, `permission`, `mode`, `hidden`, `color`, `top_p`, plus
arbitrary provider passthrough (e.g. `reasoningEffort`).

**Subagent invocation**: automatically by the primary agent via the **Task tool**, or
manually via `@mention`. `subagent_depth` defaults to `1` (primary → subagent, no
nesting); `0` disables subagents entirely.

**Task permissions**: `permission.task` uses glob patterns. When set to `deny`, the
subagent is **removed from the Task tool description entirely** so the model never
attempts it.

**Definitions**: JSON in `opencode.json`, or markdown with YAML frontmatter in
`~/.config/opencode/agents/` or `.opencode/agents/`. The filename becomes the agent name.

### 3.3 Permissions — allow / ask / deny

| Permission key | Covers |
|---|---|
| `read` | Reading a file (matches path) |
| `edit` | `write`, `edit`, `apply_patch` |
| `glob` / `grep` / `list` | File discovery |
| `bash` | Shell commands (matches parsed command) |
| `task` | Launching subagents |
| `external_directory` | Any tool touching paths outside the worktree |
| `todowrite` | Todo list mutation |
| `webfetch` / `websearch` | Network |
| `lsp` | Language server queries |
| `skill` | Loading a skill |
| `question` | Asking the user questions |
| `doom_loop` | **Same tool call repeated 3× with identical input** |

**Matching**: simple wildcards (`*` = zero or more chars, `?` = exactly one). Rules are
evaluated in order, **last matching rule wins** — so put `"*"` first and specifics after.

**Defaults**: most `allow`; `doom_loop` and `external_directory` default to `ask`;
`.env` files denied by default.

**`--auto` mode**: auto-approves anything not explicitly `deny`.

### 3.4 Tools — built-in set

`bash`, `edit`, `write`, `read`, `grep`, `glob`, `lsp` (experimental), `apply_patch`,
`skill`, `todowrite`, `webfetch`, `websearch`, `question`.

**Tool output bounding** (highly relevant to us):

- One tool settlement receives **one aggregate textual limit** — max lines *or* UTF-8
  bytes, whichever is reached first. Provider-independent.
- Generic truncation **preserves the beginning and the end** of textual output.
- A truncated output identifies its complete text both in the bounded preview **and** as a
  typed managed output path.
- **Failure to retain the managed file does not turn a successful tool operation into a
  failed one** — the session records an explicitly lossy bounded output without a path.
- Managed files use globally unique names in one shared flat directory; their absolute
  paths are readable by ordinary tools.

### 3.5 Skills — on-demand loading

- `SKILL.md` with YAML frontmatter: `name` (required), `description` (required),
  `license`, `compatibility`, `metadata`.
- **Only name + description enter the context**, listed in the `skill` tool description.
- The body is loaded **on demand** when the agent calls `skill({ name })`.
- Permission-gated with glob patterns (`internal-*: deny`).
- Discovery walks up from cwd to the git worktree, plus global dirs.

### 3.6 Providers — abstraction we don't need

- Vercel AI SDK + [models.dev](https://models.dev) catalog; 75+ providers.
- `limit.context` / `limit.output` let OpenCode know how much context remains.
- `small_model` for cheap tasks (title generation).
- Per-provider `timeout`, `headerTimeout`, `chunkTimeout`, `setCacheKey`.
- Local models via `@ai-sdk/openai-compatible` — llama.cpp, LM Studio, Ollama, Atomic Chat.
- **No GGUF parsing, no GPU tuning, no KV-cache configuration.**

### 3.7 Compaction

```json
{ "compaction": { "auto": true, "prune": false, "reserved": 10000 } }
```

- `auto` — compact when context is full (default `true`)
- `prune` — remove old tool outputs to save tokens (default `false`)
- `reserved` — token buffer so compaction itself doesn't overflow

### 3.8 Config layering

Precedence (later overrides earlier): remote `.well-known/opencode` → global
`~/.config/opencode/opencode.json` → `OPENCODE_CONFIG` → project `opencode.json` →
`.opencode/` dirs → `OPENCODE_CONFIG_CONTENT` → managed files → macOS MDM.

Supports `{env:VAR}` and `{file:path}` substitution, and an `instructions` array of
glob patterns for project rules.

### 3.9 Snapshots

An internal git repository tracks file changes so `/undo` and `/redo` work. Disableable
via `"snapshot": false`.

---

## 4. BFA Coworker Current State

### 4.1 `agent_controller.py` — the conversation loop

| Constant | Value | Effect |
|---|---|---|
| `_MAX_TOOL_ITERATIONS` | 8 | Hard cap on agentic iterations |
| `_MAX_HISTORY_MESSAGES` | 20 | **Hard slice** — old turns silently dropped |
| `_MAX_TOOL_RESULT_CHARS` | 500 | **Lossy trim** of tool results |
| `_CHARS_PER_TOKEN` | 3.5 | Token estimation |
| `_TEMPLATE_OVERHEAD_TOKENS` | 512 | Chat-template headroom |
| `_STREAM_TIMEOUT` | 600.0 | HTTP timeout |
| `_DEFAULT_MAX_TOKENS` | 1024 | Output cap |
| `_DEFAULT_TEMPERATURE_CODE` | 0.2 | Agent mode |
| `_DEFAULT_TEMPERATURE_PROSE` | 0.35 | Ask mode |

**Existing strengths** (do not regress):

- **Domain tool scoping** — `_SURFACE_TOOLS` (always on) + `_TOOL_DOMAINS` (8 domains:
  animation, material, modeling, lighting, rendering, vse, geometry_nodes, assets) +
  `_LOAD_TOOLS_SCHEMA` meta-tool for on-demand loading. Keyword + scene detection.
- **Smart undo** — `_undo_code()`, `_build_cleanup_code()`, `_EntitySnapshot`,
  `_EntityDiff`; auto-undo before retry, entity cleanup fallback.
- **Spiral detection** — 2 consecutive identical errors on `execute_blender_code`,
  truncates failed attempts and injects a corrective message.
- **Vision pipeline** — `_pending_image`, `_extract_image_from_tool_result()`.
- **Reasoning handling** — `reasoning_content` / `reasoning`, `_strip_think_tags()`.
- **Robustness** — `_repair_tool_call_pairs()`, `_sanitize_message_roles()`,
  `_strip_reasoning_from_history()`, `_fit_history_to_budget()`,
  `_estimate_messages_tokens()`, `_flatten_for_plain_chat()`.
- **Auto-continue** on `finish_reason=length` (2 attempts); empty-response retry with
  doubled thinking budget (2 retries).
- **Tail-preserving error trim** — `_trim_tool_result()` keeps the traceback tail.

### 4.2 `llm_manager.py` — llama-server lifecycle

Current launch args:

```
llama-server --jinja --verbose --host 127.0.0.1 --port <p>
             --ctx-size <n> --n-gpu-layers <n>
             [--model <path> [--mmproj <path>]] | [--hf-repo <r> --hf-file <f>]
```

Existing infrastructure: `autodetect_gpu_layers()`, `_gguf_layer_count()` (reads
`<arch>.block_count` from the GGUF header), `_detect_gpu_backend()` (CUDA/Vulkan),
`PRESET_MODELS`, `PRESET_REMOTE_PROVIDERS` (OpenRouter), `fetch_remote_models()`,
`check_remote_api()`.

**Not used**: KV-cache quantization, flash attention, cache reuse, batch/ubatch sizing,
parallel slots, mmap/mlock control, defrag threshold, metrics endpoint.

### 4.3 `skills/__init__.py` — always-injected

`get_always_loaded_skills()` injects **full skill bodies** into the system prompt every
turn (version-aware `blender_*.md` cumulative + `best_practices.md` + `naming.md` +
`mcp_tools.md`). `get_domain_skills()` injects domain skills on detection.

### 4.4 Operating modes

`local` (llama-server), `remote` (OpenAI-compatible/OpenRouter), `EXTERNAL_HARNESS`
(bridge-only, MCP managed externally).

### 4.5 Ports

Bridge `9876`, MCP `9191`, LLM `8081`.

---

## 5. Gap Matrix

| Capability | OpenCode | BFA Coworker | Gap | Tier 4f Phase |
|---|---|---|---|---|
| **GGUF/inference tuning** | ❌ none | 🟡 basic flags | **We lead** | 4f.1 |
| KV-cache quantization | ❌ | ❌ | Missing | 4f.1 |
| Flash attention | ❌ | ❌ | Missing | 4f.1 |
| Cache reuse / prompt caching | ❌ | ❌ | Missing | 4f.1 |
| Batch/ubatch sizing | ❌ | ❌ | Missing | 4f.1 |
| Parallel slots | ❌ | ❌ | Missing | 4f.1 |
| Metrics endpoint (tok/s) | ❌ | ❌ | Missing | 4f.1 |
| **Tool output bounding** | ✅ preview + managed file | 🟡 lossy 500-char trim | Partial | 4f.2 |
| **Compaction** | ✅ auto/prune/reserved | ❌ hard 20-msg slice | Missing | 4f.2 |
| Tool-output pruning | ✅ | ❌ | Missing | 4f.2 |
| Token budget readout | ✅ (via limits) | 🟡 planned §14.3 | Partial | 4f.2 |
| Checkpoint / context flush | ✅ (epochs) | 🟡 planned §14.6 | Partial | 4f.2 |
| **Permissions allow/ask/deny** | ✅ | ❌ | Missing | 4f.3 |
| Glob permission patterns | ✅ | ❌ | Missing | 4f.3 |
| Per-agent permission override | ✅ | ❌ | Missing | 4f.3 |
| `external_directory` guard | ✅ | ❌ | Missing | 4f.3 |
| **Doom-loop guard (3× identical)** | ✅ | 🟡 spiral (errors only, 2×) | Partial | 4f.3 |
| **Subagents + Task tool** | ✅ | ❌ | Missing | 4f.4 |
| Planner → specialists → validator | ✅ | 🟡 planned 4g.6 / 4f.4 | Partial | 4f.4 |
| `subagent_depth` limit | ✅ | ❌ | Missing | 4f.4 |
| Per-agent model/temperature/steps | ✅ | ❌ | Missing | 4f.4 |
| Markdown agent definitions | ✅ | ❌ | Missing | 4f.4 |
| **On-demand skill loading** | ✅ | ❌ (always injected) | Missing | 4f.2 |
| Provider abstraction (75+) | ✅ | 🟡 2 paths | Non-goal | — |
| Config file layering | ✅ | 🟡 Blender prefs | Non-goal | — |
| LSP integration | ✅ | ❌ | Non-goal | — |
| Git snapshots | ✅ | 🟡 Blender undo | Non-goal | — |
| **Blender domain tooling** | ❌ | ✅ 48+ tools | **We lead** | — |

---

## 6. Where We Can Beat OpenCode

OpenCode cannot tune inference because it doesn't own the server. We do. This is a
genuine, defensible advantage for local-model users.

| Optimization | Why it matters for Blender | OpenCode | Us |
|---|---|---|---|
| **KV-cache quantization** (`--cache-type-k/v q8_0`) | Halves KV memory → bigger context on the same GPU | ❌ | ✅ 4f.1 |
| **Flash attention** (`--flash-attn`) | Faster attention, lower memory | ❌ | ✅ 4f.1 |
| **Cache reuse** (`--cache-reuse`) | Reuses the system-prompt prefix across turns — huge for our large tool schemas | ❌ | ✅ 4f.1 |
| **Batch/ubatch tuning** | Prompt-processing speed on long tool results | ❌ | ✅ 4f.1 |
| **Parallel slots** (`--parallel`) | Concurrent subagent execution (4f.4) | ❌ | ✅ 4f.1 |
| **mmap/mlock control** | Avoids page-fault stalls on spinning disks | ❌ | ✅ 4f.1 |
| **Metrics endpoint** | Real tok/s + KV usage → drives the budget readout | ❌ | ✅ 4f.1 |
| **Speculative decoding** (Tier 5a) | 2–3× speedup, lossless | ❌ | ✅ when upstream lands |

**Combined effect**: a 27B Q4 model that today runs at ~30–40 tok/s with a 16K context
could run at ~70–100 tok/s with a 32K context on the same hardware. That is a
qualitative UX change, not an incremental one.

---

## 7. Deliberate Non-Goals

| OpenCode feature | Why we skip it |
|---|---|
| 75+ provider abstraction | We need 2 paths (local llama-server, OpenAI-compatible remote). A catalog adds maintenance for no user benefit. |
| Config file layering (8 tiers) | Blender's `AddonPreferences` already persists settings. A parallel config system would confuse users. |
| LSP integration | Blender's Python API is not LSP-served. Our bundled API/manual doc tools serve the same purpose. |
| Git snapshots for undo | Blender's native undo + our entity snapshots already cover scene state. A git repo per session is heavy. |
| Effect/Schema type system | TypeScript-specific. Our equivalent is the `NamedTuple` toolcode pattern. |
| TUI / desktop / web front-ends | We are a Blender addon; the chat panel is the front-end. |
| `apply_patch` / `edit` / `write` file tools | We operate on Blender datablocks, not source files. `execute_blender_code` is the analogue. |

---

## 8. Transferable Patterns Summary

Ranked by value-to-effort for BFA Coworker:

| # | Pattern | Source | Value | Effort | Phase |
|---|---|---|---|---|---|
| 1 | **Doom-loop guard** (3× identical call) | `permission.doom_loop` | 🔴 High | 🟢 Low | 4f.3 |
| 2 | **Permission allow/ask/deny + globs** | `permission` config | 🔴 High | 🟡 Med | 4f.3 |
| 3 | **Managed tool output files** | Tool Registry bounding | 🔴 High | 🟡 Med | 4f.2 |
| 4 | **Compaction with reserved buffer** | `compaction` config | 🔴 High | 🟡 Med | 4f.2 |
| 5 | **KV-cache + flash-attn + cache-reuse** | (our own) | 🔴 High | 🟢 Low | 4f.1 |
| 6 | **Metrics-driven budget readout** | `limit.context` | 🟡 Med | 🟢 Low | 4f.1/4f.2 |
| 7 | **On-demand skill loading** | `skill` tool | 🟡 Med | 🟡 Med | 4f.2 |
| 8 | **Subagent + Task tool** | Agents | 🔴 High | 🔴 High | 4f.4 |
| 9 | **Planner → specialists → validator** | Agents + 4g.6 | 🔴 High | 🔴 High | 4f.4 |
| 10 | **Per-agent model/temperature/steps** | Agent options | 🟡 Med | 🟡 Med | 4f.4 |
| 11 | **Markdown agent definitions** | `agents/*.md` | 🟡 Med | 🟢 Low | 4f.4 |
| 12 | **`external_directory` guard** | Permissions | 🟡 Med | 🟢 Low | 4f.3 |
| 13 | **Context Epoch / baseline caching** | Session Runtime V2 | 🟡 Med | 🔴 High | 4f.2 (partial) |
| 14 | **Steering vs queueing prompts** | Session Runtime V2 | 🟢 Low | 🟡 Med | Defer |

**Note on #13**: Full Context Epoch machinery is over-engineered for our scale. We adopt
the *practical* part — a stable system-prompt prefix that compaction replaces wholesale —
without the typed Context Source registry.

---

## Appendix: Source References

- Repository: https://github.com/anomalyco/opencode
- Session runtime spec: `CONTEXT.md` (225 lines, 31.3 KB)
- Contributor conventions: `AGENTS.md` (161 lines)
- Docs: https://opencode.ai/docs/ — agents, permissions, tools, skills, config, providers, mcp-servers