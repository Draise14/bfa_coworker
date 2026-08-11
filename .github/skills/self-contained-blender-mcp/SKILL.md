---
name: self-contained-blender-mcp
description: "**WORKFLOW SKILL** — Maintain and extend the Bforartists Coworker add-on, a self-contained Blender agent system that downloads, configures, and runs a local LLM (llama.cpp) with a built-in Blender chat UI. Also supports remote LLM providers (OpenAI-compatible, Anthropic/OpenRouter via API key). Use when: adding auto-setup of local LLM, building Blender chat panels, bundling MCP server startup, making the addon self-sufficient, or merging upstream Blender MCP changes. DO NOT use for: general Blender scripting, modifying existing MCP tools, or fixing bridge server bugs."
---

# Bforartists Coworker — Agent Skill

## Overview

This skill documents the **Bforartists Coworker** add-on (a fork of Blender MCP),
a self-contained system where the add-on itself manages the entire pipeline:

```
Blender Add-on (chat UI + agent controller)
    │
    ├── manages → MCP Server (bfa-coworker-mcp process via subprocess)
    │                  │
    │                  └── connects → MCP Bridge Server (TCP listener inside Blender)
    │
    ├── manages → LLM Backend (llama-server subprocess, OR remote API)
    │
    └── provides → Chat Panel (multi-line input + streaming response in Blender UI)
```

## Prerequisites

- The `bfa_coworker` workspace with existing add-on and MCP server code.
- Blender 5.1+ (with `bpy.app.online_access` support).
- Windows (paths/exe detection is Windows-native; adapt for macOS/Linux).

## Architecture & Components

### Current File Layout

```
addon/bfa_coworker/           # Blender add-on package
├── __init__.py               # Thin registration hub (~100 lines)
├── shared.py                 # Constants, port helpers, lazy imports, preset items
├── preferences.py            # _BFACW_Preferences (all config properties + draw())
├── operators_server.py       # Bridge server start/stop + autostart timer
├── operators_llm.py          # LLM operators (download, start/stop, scan, select)
├── operators_agent.py        # Remote API operators (test, refresh, browse, ping)
├── operators_hf.py           # HuggingFace cache operators
├── llm_manager.py            # LLM lifecycle management
├── agent_controller.py       # MCP client + conversation orchestrator
├── ui_chat.py                # Blender chat panel + Text Editor integration
├── mcp_to_blender_server.py  # TCP socket bridge server
├── execute_blocking.py       # Background mode execution
├── execute_interactive.py    # Interactive mode execution
├── deferred_tool.py          # Background job handling
├── weak_sandbox.py           # LLM code safety sandbox
├── capture_output.py         # stdout/stderr capture
├── cli.py                    # CLI entry point
├── log.py                    # Logging infrastructure
├── blender_manifest.toml     # Add-on manifest
└── vendor/
    ├── deps/                 # Pure-Python deps (pip install --target)
    └── blmcp/                # blmcp source package (copied from mcp/blmcp/)
```

### 1. LLM Manager (`llm_manager.py`)

Handles LLM lifecycle with thread-safe state:

| Responsibility | Detail |
|---|---|
| **Local mode** | Detect `llama-server.exe`, download GGUF models via direct HTTP streaming, start/stop `llama-server` subprocess, health-check. |
| **Remote mode** | Store API key + base URL, validate connectivity. Provider presets (OpenRouter) auto-fill the API URL. |
| **State** | Thread-safe `LLMConfig` / `LLMState` dataclasses protected by `threading.Lock`. |

**Key functions:**

```python
def find_llama_server() -> str | None:
    """Search PATH and common install locations for llama-server.exe."""

def download_model(repo_id: str, filename: str, hf_token: str = "") -> Path:
    """Direct HTTP download from HuggingFace in 64 KB chunks with real-time progress."""

def cancel_download() -> None:
    """Cancel an in-progress download via threading.Event. Cleans up partial files."""

def download_llama_server() -> None:
    """One-click download and extraction of llama-server from GitHub releases."""

def start_local_llama(model_path: Path, port: int = 8081, ctx_size: int = 32768,
                      max_tokens: int = 16384, hf_token: str = "") -> subprocess.Popen:
    """Launch llama-server as a subprocess with configured parameters."""

def stop_local_llama() -> None:
    """Gracefully terminate llama-server; fallback to taskkill/pkill for orphans."""

def health_check(url: str = "http://127.0.0.1:8081") -> bool:
    """Ping the LLM backend to confirm it is ready."""

def check_remote_api(base_url: str, api_key: str) -> bool:
    """Validate a remote API connection (OpenAI-compatible)."""

def scan_existing_models(models_dir: str) -> list[dict]:
    """Scan HF cache + models directory for .gguf files."""

def get_presets() -> list[ModelPreset]:
    """Return all curated model presets with metadata."""

def get_preset_by_id(identifier: str) -> ModelPreset | None:
    """Look up a preset by its identifier string."""

def fetch_remote_models(api_url: str, api_key: str) -> list[RemoteModelPreset]:
    """Fetch available models from a remote API's /v1/models endpoint."""

def get_config() -> LLMConfig:
    """Thread-safe copy of current config."""

def set_config(cfg: LLMConfig) -> None:
    """Thread-safe config update."""
```

**Model Presets:** 14 curated GGUF models organized into three categories:
- **Flagship** (24 GB+ VRAM): DeepSeek R1 Distill 32B, Qwen 2.5 Coder 32B, Gemma 4 26B Q8
- **Mid-Range** (12-20 GB VRAM): Mistral Small 3.1 24B (default), Gemma 4 26B, Gemma 3 27B, Qwen3.6 35B A3B, GPT-OSS 20B, Phi-4 14B
- **Lightweight** (≤ 8 GB VRAM): Llama 3.1 8B, Gemma 3 12B Vision, Qwen3.5 9B Heretic, Qwen3 8B, Phi-4 14B Q3

Each preset carries `context_window` and `max_tokens` metadata. Selecting a preset auto-configures both `--ctx-size` and `max_tokens`.

**Download Strategy:**
1. **Primary**: `_download_gguf_direct()` — streams from HuggingFace in 64 KB chunks via `urllib.request`. Real-time progress (percentage, speed, ETA, progress bar). Pre-fetches file size via HEAD request.
2. **Fallback**: If direct download fails for non-auth reasons, falls back to `llama-server --hf-repo/--hf-file` with 15-minute timeout.
3. **HF_TOKEN**: Checked in order: config field → `HF_TOKEN` env → `HUGGINGFACE_TOKEN` env.
4. **Disk space**: Pre-flight check via `shutil.disk_usage()` requires file size + 5% margin.
5. **Cancel**: `threading.Event`-based cancellation. Partial files cleaned up on abort.

### 2. Agent Controller (`agent_controller.py`)

Orchestrates the conversation loop inside Blender:

| Responsibility | Detail |
|---|---|
| **MCP Server subprocess** | Launch `bfa-coworker-mcp` with `--transport http` as a subprocess. |
| **LLM API calls** | Send conversation history + tool definitions to LLM backend. |
| **Tool execution** | Parse LLM tool_calls, invoke via MCP over HTTP, return results. |
| **Streaming** | Stream text responses to Blender chat UI via callback. |
| **Port management** | Port conflict detection, orphan cleanup, availability checks. |

**Key functions:**

```python
async def start_mcp_server(port: int = 9191) -> int:
    """Launch bfa-coworker-mcp --transport http --port <port> as subprocess."""

async def stop_mcp_server() -> None:
    """Terminate the bfa-coworker-mcp subprocess."""

async def run_conversation_turn(
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], None],
    on_tools: Callable[[list], None],
) -> tuple[list[dict], list[dict]]:
    """Core loop: send to LLM, handle tool_calls via MCP, stream text."""

async def list_mcp_tools() -> list[dict]:
    """Return tools from MCP server via HTTP /tools endpoint."""

def ping_agent() -> dict:
    """Test all connectivity endpoints (bridge, MCP, LLM). Returns status dict."""

def check_ports_available(bridge: int, mcp: int, llm: int) -> dict:
    """Test port availability by attempting to bind with SO_EXCLUSIVEADDRUSE."""

def migrate_vendor_deps() -> bool:
    """Ensure vendor/deps/ and vendor/blmcp/ are present and up-to-date."""

def request_stop() -> None:
    """Thread-safe stop request for the conversation loop."""

def clear_stop() -> None:
    """Clear the stop request flag."""
```

**Additional features:**
- **Orphaned tool message cleanup**: `_drop_orphaned_tool_messages()` removes tool-role messages without preceding assistant `tool_calls`, preventing Jinja template errors.
- **Auto-continue on truncation**: If `finish_reason=length`, sends "Continue." and concatenates results (max 2 attempts).
- **Reasoning content logging**: Full chain-of-thought from reasoning models logged to console.
- **SSE parser**: `_parse_sse_json()` / `_parse_sse_text_response()` for FastMCP stateless_http mode.
- **Pipe drainer**: Background threads drain stdout/stderr pipes from subprocesses, preventing deadlock.
- **Port killer**: `_kill_process_on_port()` uses netstat+taskkill (Windows) or fuser (Linux) to clean up orphaned processes.
- **System prompt path**: Searches both dev layout (`mcp/blmcp/data/prompts.yml`) and deployed layout (`vendor/blmcp/data/prompts.yml`).

### 3. Blender Chat Panel (`ui_chat.py`)

Registers two Blender UI panels with a chat interface:

```
┌─────────────────────────────────────┐
│  Coworker Chat            [≡] [⚙]  │
├─────────────────────────────────────┤
│                                     │
│  User: create a red cube            │
│  ───────────────────────────────    │
│  Agent: I'll create a cube...       │
│  [Tool] execute_blender_code(...)   │
│  Agent: Done! A red cube...         │
│                                     │
├─────────────────────────────────────┤
│ [Send]  [Clear]                   │
│ ┌─────────────────────────────────┐ │
│ │ Type your message here...       │ │
│ └─────────────────────────────────┘ │
│ Status: 🟢 Connected  Model: Qwen  │
└─────────────────────────────────────┘
```

**Registration:**

```python
class BFACW_PT_chat_panel(bpy.types.Panel):
    bl_label = "Coworker Chat"
    bl_idname = "BFACW_PT_chat_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Coworker"

class BFACW_PT_chat_text_editor(bpy.types.Panel):
    bl_label = "Coworker Chat"
    bl_idname = "BFACW_PT_chat_text_editor"
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Coworker"
```

**Operators:**

| Operator ID | Action |
|---|---|
| `bfacw.chat_send` | Send the current input to the LLM agent. |
| `bfacw.chat_clear` | Clear conversation history. |
| `bfacw.chat_stop` | Stop the current generation. |
| `bfacw.agent_start` | Start the agent (LLM + MCP server). |
| `bfacw.agent_stop` | Stop the agent and cleanup subprocesses. |

**Storage:** Chat history is persistent to disk — survives Blender restarts:
- **File location**: `<SCRIPTS>/bfa_coworker_chat_history/<blend_filename>/history.json`
- **In-memory**: `ChatHistoryProperties` PropertyGroup with `chat_input`, `chat_status`, `chat_streaming_text`.
- **Auto-save**: After each completed turn, write history to disk.

### 4. Preferences (`preferences.py`)

The `_BFACW_Preferences` class (extends `bpy.types.AddonPreferences`) contains all configuration:

| Property | Type | Purpose |
|---|---|---|
| `llm_mode` | Enum | Local (llama.cpp) or Remote API |
| `llama_path` | String | Path to `llama-server.exe` |
| `model_repo_id` | String | HuggingFace repo ID |
| `model_filename` | String | GGUF filename |
| `downloaded_models_dir` | String | Directory for downloaded models |
| `model_preset` | Enum | Curated model preset selector |
| `model_preset_info` | String | Read-only preset metadata display |
| `existing_model_path` | String | Absolute path to an existing .gguf file |
| `local_ctx_size` | Int | Context window size (4096–262144) |
| `local_max_tokens` | Int | Max output tokens (512–131072) |
| `hf_token` | String | HuggingFace token for gated models (password-masked) |
| `remote_api_url` | String | Remote API base URL |
| `remote_api_key` | String | API key (password-masked) |
| `remote_model` | String | Remote model ID |
| `remote_provider` | Enum | Provider preset (OpenRouter / Custom) |
| `remote_models_count` | Int | Live model count from API |
| `agent_autostart` | Bool | Auto-start agent with Blender |
| `port_offset` | Int | Global port offset (0–100) |
| `bridge_port` | Int | Bridge port override (0 = default + offset) |
| `mcp_port` | Int | MCP port override |
| `llm_port` | Int | LLM port override |
| `timer_interval_active` | Float | Polling rate while active (0.05–5.0s) |
| `timer_interval_idle` | Float | Polling rate while idle (0.1–10.0s) |
| `timer_interval_idle_delay` | Float | Idle delay (1.0–60.0s) |
| `use_log` | Bool | Toggle tool request/response logging |

The `draw()` method renders a categorized UI:
1. **LLM Configuration** box with mode toggle
2. **Local mode**: llama-server status + download button, categorized model presets (Flagship/Mid/Lightweight), custom model dropdown, download button with progress bar, existing model scanner, advanced settings expander
3. **Remote mode**: provider dropdown, API URL, API key, model name, refresh/browse/test buttons
4. **Agent Control** box with autostart toggle, start/stop buttons, ping button
5. **Ports** box with offset + individual overrides + effective port display
6. **Diagnostics** box (when `BFACW_DEBUG=True`): check ports, diagnose, benchmarks
7. **Timer** box with active/idle/idle delay sliders
8. **Log** toggle

### 5. Async Execution Strategy

Blender's main thread is **not async-safe**. The agent controller runs async I/O in a **background thread** and communicates results back via `bpy.app.timers`:

```python
import asyncio
import threading

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None

def _run_async_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()

def _ensure_event_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    if _loop is None or not _loop.is_running():
        _loop = asyncio.new_event_loop()
        _thread = threading.Thread(target=_run_async_loop, args=(_loop,), daemon=True)
        _thread.start()
    return _loop

def _schedule_coro(coro) -> concurrent.futures.Future:
    """Schedule a coroutine on the background event loop and return a Future."""
    loop = _ensure_event_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop)
```

Use `bpy.app.timers.register(...)` to poll the Future and update the UI when complete.

## Backward Compatibility with Upstream Blender MCP

This is a fork of the upstream [Blender MCP](https://www.blender.org/lab/mcp-server/).
The following guidelines help maintain merge compatibility.

### Upstream-Tracking Files (keep close to upstream)

These files should have **minimal fork changes** to simplify merging upstream updates:

| File | Notes |
|---|---|
| `mcp/blmcp/` (entire package) | The `blmcp` Python package name was **intentionally kept** — do NOT rename to `bfa_coworker` |
| `mcp/blmcp/tools/*.py` | Auto-discovered at startup — upstream additions merge directly |
| `mcp/blmcp/data/prompts.yml` | System prompt — review carefully when merging (affects agent behavior) |
| `mcp_to_blender_server.py` | TCP socket bridge — minimal fork changes |
| `execute_blocking.py` | Background mode execution |
| `execute_interactive.py` | Interactive mode execution |
| `deferred_tool.py` | Background job handling |
| `weak_sandbox.py` | LLM code safety sandbox |
| `capture_output.py` | stdout/stderr capture |
| `cli.py` | CLI entry point |

### Fork-Specific Files (safe from upstream conflicts)

These files have **no upstream equivalent** — no merge conflicts expected:

| File | Purpose |
|---|---|
| `llm_manager.py` | LLM lifecycle (download, start/stop, presets) |
| `agent_controller.py` | Conversation orchestrator |
| `ui_chat.py` | Chat panel |
| `preferences.py` | Preferences (was part of upstream `__init__.py`) |
| `shared.py` | Shared constants and helpers |
| `log.py` | Logging infrastructure |
| `build_addon.py` | Build script |
| `vendor/` | Vendored deps |

### Merge Strategy

1. **Upstream `__init__.py` changes**: The upstream `__init__.py` has a completely different structure (monolithic). When merging, port changes to the **correct operator module**:
   - Server-related changes → `operators_server.py`
   - LLM-related changes → `operators_llm.py`
   - Agent-related changes → `operators_agent.py`
   - HF cache changes → `operators_hf.py`
   - Preferences changes → `preferences.py`
   - Shared constants → `shared.py`

2. **Upstream tool additions**: New files in `mcp/blmcp/tools/` can be merged directly — they auto-discover at startup. No registration needed.

3. **Upstream `prompts.yml` changes**: Review carefully. System prompt changes affect agent behavior, tool descriptions, and response formatting.

4. **Upstream bridge server changes**: `mcp_to_blender_server.py` changes can usually be merged directly if they don't touch the port constants (those are now in `shared.py`).

## Step-by-Step Implementation Guide

### Phase 1: LLM Manager Module

1. Create `addon/bfa_coworker/llm_manager.py`:
   - `find_llama_server()` — search PATH, Program Files, LOCALAPPDATA, user-configured path.
   - `download_model()` — direct HTTP download from HuggingFace with progress streaming.
   - `cancel_download()` — threading.Event-based cancellation.
   - `download_llama_server()` — download and extract from GitHub releases.
   - `start_local_llama()` — subprocess.Popen with configured parameters.
   - `stop_local_llama()` — terminate + fallback taskkill/pkill.
   - `health_check()` — urllib.request to /health endpoint.
   - `scan_existing_models()` — scan HF cache + models dir for .gguf files.
   - `get_presets()` / `get_preset_by_id()` — curated model preset access.
   - `fetch_remote_models()` — query /v1/models endpoint.
   - Thread-safe state via threading.Lock with copy-on-read pattern.
   - `LLMConfig` dataclass: all config fields.
   - `LLMState` dataclass: runtime state (is_running, error, download_progress, etc.).
   - `ModelPreset` dataclass: identifier, name, repo_id, filename, category, ram_gb, disk_gb, capability, context_window, max_tokens, description.

2. Create operators in `operators_llm.py`:
   - `_BFACW_OT_download_model` — modal operator with progress polling
   - `_BFACW_OT_cancel_download` — triggers cancel event
   - `_BFACW_OT_start_llm` — starts llama-server
   - `_BFACW_OT_stop_llm` — stops llama-server
   - `_BFACW_OT_download_llama_server` — one-click binary download
   - `_BFACW_OT_scan_existing_models` — scans for .gguf files
   - `_BFACW_OT_select_preset` — selects a model preset
   - `_BFACW_OT_select_existing_model` — selects a found .gguf file
   - `_BFACW_OT_open_models_dir` — opens models directory in file browser

### Phase 2: Agent Controller Module

1. Create `addon/bfa_coworker/agent_controller.py`:
   - `start_mcp_server()` — spawn `bfa-coworker-mcp` as subprocess.
   - `stop_mcp_server()` — terminate subprocess.
   - `list_mcp_tools()` — HTTP endpoint or mcp client library.
   - `run_conversation_turn()` — the core loop:
     1. Build OpenAI-compatible request payload.
     2. POST to LLM endpoint.
     3. Stream response via SSE.
     4. Parse tool_calls.
     5. Call MCP tools over HTTP.
     6. Send results back to LLM.
     7. Repeat until finish_reason != "tool_calls".
   - `ping_agent()` — test all connectivity endpoints.
   - `check_ports_available()` — port conflict detection.
   - `migrate_vendor_deps()` — portable deps management.
   - `request_stop()` / `clear_stop()` — thread-safe stop.
   - `_drop_orphaned_tool_messages()` — history cleanup.
   - `_parse_sse_json()` / `_parse_sse_text_response()` — SSE parsing.
   - `_start_pipe_drainer()` — subprocess pipe deadlock prevention.
   - `_kill_process_on_port()` — orphan cleanup.
   - `_get_system_prompt()` — load prompts.yml from dev or deployed layout.

2. Create operators in `operators_agent.py`:
   - `_BFACW_OT_test_remote_api` — test remote API connection
   - `_BFACW_OT_refresh_remote_models` — fetch model count
   - `_BFACW_OT_open_model_browser` — open openrouter.ai/models
   - `_BFACW_OT_ping_agent` — full connectivity test
   - `_BFACW_OT_check_ports` — port availability check
   - `_BFACW_OT_benchmark_*` — four benchmark operators

### Phase 3: Chat UI Panel

1. Create `addon/bfa_coworker/ui_chat.py`:
   - `BFACW_PT_chat_panel` — VIEW_3D sidebar panel.
   - `BFACW_PT_chat_text_editor` — TEXT_EDITOR sidebar panel.
   - `BFACW_OT_chat_send` — send input to agent.
   - `BFACW_OT_chat_clear` — clear history.
   - `BFACW_OT_chat_stop` — cancel generation.
   - `BFACW_OT_agent_start` / `BFACW_OT_agent_stop` — start/stop agent.
   - `ChatHistoryProperties` PropertyGroup for persistence.

2. Drawing approach:
   - Use `layout.box()` for message bubbles.
   - Use `row.label()` with text wrapping for content.
   - Use `row.operator()` with icon for tool call entries.
   - Polling timer for streaming updates via `context.area.tag_redraw()`.

### Phase 4: Integration with Add-on Registration

1. `addon/bfa_coworker/__init__.py` is a thin registration hub:
   - Imports all classes from operator modules.
   - Registers/unregisters all classes.
   - No business logic — all in the split modules.

2. `addon/bfa_coworker/shared.py` contains:
   - Port constants and `effective_ports()` helper.
   - `MODEL_PRESET_ITEMS` and `REMOTE_PROVIDER_ITEMS` static lists.
   - Lazy import wrappers (`get_llm_manager()`, `get_agent_controller()`).
   - `BFACW_DEBUG` flag.

### Phase 5: Vendor Dependencies (Portable)

The old approach bundled `mcp/.venv` (created by `uv`) into `vendor/python_env/`.
This was not portable because `pyvenv.cfg` hardcodes a machine-specific base Python path.

**Current approach:**
- `vendor/deps/` — pip-installed pure-Python deps via `pip install --target`
- `vendor/blmcp/` — blmcp source package copied from `mcp/blmcp/`
- At runtime, use Blender's own Python with `vendor/deps/` and `vendor/` on `PYTHONPATH`
- `_ensure_vendor_deps()` auto-install fallback for source installs

## Decision Points & Branching

### Local vs. Remote LLM

```
LLM Mode?
├── Local → Is llama-server installed?
│   ├── Yes → Is a model downloaded?
│   │   ├── Yes → Start llama-server with model → Health check → Ready
│   │   └── No  → Show "Download & Start" button → Direct HTTP download → Start
│   └── No  → Show "Download llama-server" button → Auto-download from GitHub
│               → Then proceed to model download
│
└── Remote → Is API URL configured?
    ├── Yes → Validate with test request → Ready
    └── No  → Show configuration fields (URL, key, model)
```

### Startup Sequence

```
Auto-start enabled?
├── Yes → Agent starts after AUTOSTART_DELAY:
│   1. Start MCP Bridge Server (TCP listener).
│   2. Start bfa-coworker-mcp (HTTP subprocess).
│   3. If local mode: start llama-server.
│   4. Wait for health check on MCP server (retry up to N times).
│   5. Wait for LLM health check (retry up to N times).
│   6. Set status to "connected".
│   7. If any step fails → Store error in _State, show in prefs.
│
└── No  → User must press "Start Agent" in prefs or chat panel.
```

## Quality Checks

| Check | Criteria |
|---|---|
| **Subprocess lifecycle** | `llama-server` and `bfa-coworker-mcp` started/stopped cleanly. No zombie processes. |
| **Health check timeout** | Startup waits max 30s for `llama-server`. Clear error if not ready. |
| **Chat input/output** | Multi-line text input works. Long responses scroll. Tool calls visible. |
| **Streaming** | Text appears incrementally, not all at once. |
| **Thread safety** | Async loop on daemon thread. UI updates via `bpy.app.timers`. No direct `bpy` from background thread. |
| **Error handling** | LLM disconnects, MCP crashes show clear error, not a hang. |
| **Stop mid-generation** | Stop button cancels current Future. |
| **Preferences persistence** | All prefs survive Blender restart. |
| **Remote API** | API key stored (masked). Connection test works. Clear error if invalid. |
| **Download progress** | Progress bar updates in real-time. Speed, ETA, percentage shown. |
| **Cancel download** | Partial file cleaned up. State resets correctly. |
| **Disk space check** | Pre-flight check prevents download if insufficient space. |
| **HF_TOKEN** | Gated models accessible with token. Clear error on 401/403. |
| **Port conflicts** | Port killer cleans up orphans. Availability check before start. |
| **Vendor deps** | Auto-install fallback works for source installs. |
| **No external client needed** | Everything starts from the add-on. |

## Edge Cases

| Situation | Handling |
|---|---|
| `llama-server` not on PATH | Show download button. Auto-search common locations. |
| Model download interrupted | Partial file cleaned up. User can retry. |
| 401 from HuggingFace | Suggest setting HF_TOKEN. |
| 403 from HuggingFace | Suggest granting access at huggingface.co. |
| 404 from HuggingFace | Suggest checking repo/file name. |
| Insufficient disk space | Pre-flight check prevents download. Actionable error message. |
| Blender closes while LLM running | `unregister()` kills subprocesses. Port killer cleans orphans. |
| Multiple Blender instances | Each has configurable ports. LLM backend is shared. |
| Low VRAM / OOM | Recommend lightweight presets (Q4_K_M, 3B-8B params). |
| Remote API rate limit | Show error, suggest retry. Store last error in state. |
| Port conflicts | Port killer + availability check. Effective ports shown in UI. |
| Subprocess pipe deadlock | Pipe drainer threads prevent Blender hang. |
| Orphaned tool messages in history | `_drop_orphaned_tool_messages()` prevents Jinja errors. |
| `finish_reason=length` truncation | Auto-continue with concatenation (max 2 attempts). |
| System prompt not loading | Searches both dev and deployed paths. |
| Bundled .venv not portable | Replaced with vendor/deps/ + vendor/blmcp/ layout. |