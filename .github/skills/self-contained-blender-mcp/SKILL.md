---
name: self-contained-blender-mcp
description: "**WORKFLOW SKILL** — Transform the Blender MCP add-on into a self-contained system that downloads, configures, and runs a local LLM (llama.cpp) with a built-in Blender chat UI. Also supports remote LLM providers (OpenAI-compatible, Anthropic/OpenRouter via API key). Use when: adding auto-setup of local LLM, building a Blender chat panel, bundling MCP server startup, or making the addon self-sufficient. DO NOT use for: general Blender scripting, modifying existing MCP tools, or fixing bridge server bugs."
---

# Self-Contained Blender MCP Skill

## Overview

This skill converts the existing Blender MCP add-on from a **thin bridge** (user must manually start the MCP server + LLM externally) into a **self-contained system** where the add-on itself manages the entire pipeline:

```
Blender Add-on (new chat UI + agent controller)
    │
    ├── manages → MCP Server (blender-mcp process via subprocess)
    │                  │
    │                  └── connects → MCP Bridge Server (existing TCP listener inside Blender)
    │
    ├── manages → LLM Backend (llama-server subprocess, OR remote API)
    │
    └── provides → Chat Panel (multi-line input + streaming response in Blender UI)
```

## Prerequisites

- The `blender_mcp` workspace with existing add-on and MCP server code.
- Blender 5.1+ (with `bpy.app.online_access` support).
- Windows (paths/exe detection is Windows-native; adapt for macOS/Linux in `platformdirs` or hardcoded paths).

## Architecture & Components

### 1. LLM Manager (`llm_manager.py`)

A new module inside `addon/blender_mcp_addon/` that handles LLM lifecycle:

| Responsibility | Detail |
|---|---|
| **Local mode** | Detect `llama-server.exe` (from PATH or configurable `llama_path`), download GGUF models via `llama-cli --hf-repo` / `--hf-file`, start/stop `llama-server` subprocess, health-check via `http://127.0.0.1:8080/health`. |
| **Remote mode** | Store API key + base URL, validate connectivity with a lightweight request. Provider presets (OpenRouter) auto-fill the API URL. Model name is a text field — use **Browse Models** to find model IDs on the provider's website. |
| **State** | Expose `is_running`, `current_mode` ("local"/"remote"/"off"), `model_name`, `error` properties. |

**Key functions:**

```python
def find_llama_server() -> str | None:
    """Search PATH and common install locations for llama-server.exe."""
    ...

def download_model(repo_id: str, filename: str, progress_callback=None) -> Path:
    """Download a GGUF model via llama-cli. Returns the local path."""
    ...

def start_local_llama(model_path: Path, port: int = 8080) -> subprocess.Popen:
    """Launch llama-server as a subprocess. Return the Popen handle."""
    ...

def stop_local_llama() -> None:
    """Gracefully terminate the llama-server subprocess."""
    ...

def health_check(url: str = "http://127.0.0.1:8080") -> bool:
    """Ping the LLM backend to confirm it is ready."""
    ...

def check_remote_api(base_url: str, api_key: str) -> bool:
    """Validate a remote API connection (OpenAI-compatible / Anthropic)."""
    ...
```

### 2. Agent Controller (`agent_controller.py`)

A new module that orchestrates the conversation loop **inside Blender** (i.e. no external chat client needed):

| Responsibility | Detail |
|---|---|
| **MCP Server subprocess** | Launch `blender-mcp` with `--transport http` as a subprocess. HTTP (streamable-http) is the primary transport so both the in-Blender agent and external clients (e.g. llama.cpp web UI) can connect. |
| **MCP Client session** | Use `mcp` stdlib to connect to the local MCP server's HTTP endpoint, list tools, call tools. |
| **LLM API calls** | Send conversation history + tool definitions to the LLM backend (local `llama-server` Open AI-compatible endpoint or remote API). |
| **Tool execution** | Parse LLM tool_calls, invoke via MCP session over HTTP, return results to LLM. |
| **Streaming** | Stream text responses to the Blender chat UI via a callback. |

**Key functions:**

```python
async def start_mcp_server(port: int = 9191) -> int:
    """Launch blender-mcp --transport http --port <port> as subprocess.
    Returns the port the server is listening on.
    The server shares the same port so external clients can also connect."""
    ...

async def stop_mcp_server() -> None:
    """Terminate the blender-mcp subprocess."""
    ...

async def run_conversation_turn(
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], None],
    on_tools: Callable[[list], None],
) -> tuple[list[dict], list[dict]]:
    """
    Send messages to LLM, handle tool calls via MCP over HTTP,
    stream text to on_text callback.
    Returns (updated_messages, tool_results).
    """
    ...

async def list_mcp_tools() -> list[dict]:
    """Return the list of tools from the MCP server (via HTTP /tools endpoint)."""
    ...
```

### 3. Blender Chat Panel (`ui_chat.py`)

A new module that registers a Blender UI panel with a chat interface:

```
┌─────────────────────────────────────┐
│  Blender MCP Chat          [≡] [⚙] │
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
class BLMCP_PT_chat_panel(bpy.types.Panel):
    bl_label = "MCP Chat"
    bl_idname = "BLMCP_PT_chat_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MCP"

    def draw(self, context):
        layout = self.layout
        # Draw conversation history
        # Draw input box
        # Draw send/clear buttons
        # Draw status bar
```

**Operators:**

| Operator ID | Action |
|---|---|
| `blmcp.chat_send` | Send the current input to the LLM agent. |
| `blmcp.chat_clear` | Clear conversation history. |
| `blmcp.chat_stop` | Stop the current generation. |
| `blmcp.agent_start` | Start the agent (LLM + MCP server). |
| `blmcp.agent_stop` | Stop the agent and cleanup subprocesses. |

**Storage:** Chat history is **persistent to disk** — survives Blender restarts:

- **File location**: `<addon_user_dir>/chat_history/<blend_filename_or_session>/history.json`
  - Use `bpy.utils.user_resource('SCRIPTS')` or `bpy.app.tempdir` for the base path.
  - One JSON file per blend file (or a "default" session for unsaved files).
- **In-memory**: A `bpy.types.PropertyGroup` (`ChatHistoryProperties`) holds:
  - `chat_messages` — A `StringProperty` that stores the path to the history JSON file.
  - `chat_input` — The current user input text (a `StringProperty` with `subtype='TEXT'`).
  - `chat_status` — One of "idle", "thinking", "error".
  - `chat_streaming_text` — The current in-progress response chunk.
- **History format** (JSON):
  ```json
  [
    {"role": "user", "content": "create a red cube", "timestamp": "..."},
    {"role": "assistant", "content": "I'll help you with that...", "tool_calls": [...]},
    {"role": "tool", "name": "execute_blender_code", "result": "...", "tool_call_id": "..."}
  ]
  ```
- **Load on open**: When Blender opens a blend file (via `load_post` handler), load the matching history file.
- **Auto-save**: After each completed turn, write the history to disk.

### 4. Preferences Integration (`__init__.py`)

Extend the existing `_BlenderMCPPreferences` with new settings:

| Property | Type | Purpose |
|---|---|---|
| `llm_mode` | Enum (`"local"`, `"remote"`) | Select local or remote LLM. |
| `llama_path` | String | Path to `llama-server.exe` (auto-detected). |
| `model_repo_id` | String | Hugging Face repo ID (e.g. `HeYujie/Qwen3.5-35B-A3B-abliterated-GGUF`). |
| `model_filename` | String | GGUF filename in the repo. |
| `downloaded_models` | String | Directory to store downloaded models. |
| `remote_api_url` | String | Base URL for remote API (auto-filled from provider preset). |
| `remote_api_key` | String | API key (stored, masked in UI). |
| `remote_model` | String | Model ID to use with the remote API (e.g. `openai/gpt-4o`). |
| `remote_provider` | Enum | Provider preset ("OpenRouter" or "Custom"). |
| `mcp_server_port` | Int | Port for the internal MCP server. |
| `auto_start_agent` | Bool | Start agent automatically with Blender. |

The preferences `draw()` method gets new sections:

```python
# In _BlenderMCPPreferences.draw():
layout.label(text="LLM Configuration", icon="SETTINGS")
layout.prop(self, "llm_mode", expand=True)

if self.llm_mode == "local":
    layout.prop(self, "llama_path")
    layout.prop(self, "model_repo_id")
    layout.prop(self, "model_filename")
    layout.prop(self, "downloaded_models")
    if not _llm_manager.is_running:
        layout.operator("blmcp.download_model")
        layout.operator("blmcp.start_llm")
    else:
        layout.operator("blmcp.stop_llm")
else:  # remote
    layout.prop(self, "remote_provider")   # OpenRouter / Custom
    layout.prop(self, "remote_api_url")    # auto-filled from provider
    layout.prop(self, "remote_api_key")    # masked password field
    layout.prop(self, "remote_model")       # model ID text field
    layout.operator("blmcp.refresh_remote_models")  # fetch model count
    layout.operator("blmcp.open_model_browser")     # browse openrouter.ai/models
    layout.operator("blmcp.test_remote_api")        # test connection

layout.separator()
layout.label(text="Agent Control", icon="WORKSPACE")
layout.prop(self, "mcp_server_port")
layout.prop(self, "auto_start_agent")
```

### 5. Async Execution Strategy

Blender's main thread is **not async-safe**. The agent controller must run async I/O in a **background thread** and communicate results back via `bpy.app.timers`:

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

## Step-by-Step Implementation

### Phase 1: LLM Manager Module

1. Create `addon/blender_mcp_addon/llm_manager.py`:
   - `find_llama_server()` — search `PATH`, `Program Files`, `LOCALAPPDATA`, user-configured path.
   - `download_model()` — shell out to `llama-cli --hf-repo ... --hf-file ... --hf-dir ...`.
   - `start_local_llama()` — `subprocess.Popen(['llama-server', '--jinja', '--hf-repo', ..., '--hf-file', ...])`.
   - `stop_local_llama()` — `process.terminate()` + `process.wait()`.
   - `health_check()` — `urllib.request.urlopen('http://127.0.0.1:8080/health')`.
   - Thread-safe state via `threading.Lock` or `queue.Queue`.

2. Create `addon/blender_mcp_addon/llm_manager_types.py` (or add to `llm_manager.py`):
   - `LLMConfig` dataclass: `mode`, `model_path`, `api_url`, `api_key`, `model_name`.
   - `LLMState` dataclass: `is_running`, `current_mode`, `error`.

3. Create operators for model download and LLM start/stop in the add-on's `__init__.py`:
   - `BLMCP_OT_download_model`
   - `BLMCP_OT_start_llm`
   - `BLMCP_OT_stop_llm`

### Phase 2: Agent Controller Module

1. Create `addon/blender_mcp_addon/agent_controller.py`:
   - `start_mcp_server()` — spawn `blender-mcp` as subprocess with `--transport http --port <port>`.
   - `stop_mcp_server()` — terminate subprocess.
   - `list_mcp_tools()` — call the HTTP endpoint `GET /tools/list` or use `mcp` client library to connect via streamable-http.
   - `run_conversation_turn()` — the core loop:
     1. Build OpenAI-compatible request payload with conversation + tools.
     2. POST to LLM endpoint (`http://127.0.0.1:8080/v1/chat/completions` or remote).
     3. Stream response via SSE if supported.
     4. Parse `tool_calls` from response.
     5. Call MCP tools over HTTP (POST to the blender-mcp streamable-http endpoint).
     6. Send results back to LLM.
     7. Repeat until `finish_reason != "tool_calls"`.
   - Callback-based text streaming to update the Blender UI.

2. **Important**: The agent controller must handle `asyncio` properly — all MCP client operations are async. Use `_ensure_event_loop()` pattern.

### Phase 3: Chat UI Panel

1. Create `addon/blender_mcp_addon/ui_chat.py`:
   - `BLMCP_PT_chat_panel` — A `VIEW_3D` sidebar panel.
   - `BLMCP_OT_chat_send` — Read input from `context.window_manager.chat_input`, append to history, schedule agent turn via `_schedule_coro()`.
   - `BLMCP_OT_chat_clear` — Clear history.
   - `BLMCP_OT_chat_stop` — Cancel the current Future.
   - `BLMCP_OT_agent_start` / `BLMCP_OT_agent_stop` — Start/stop the MCP server + LLM.
   - Custom `bpy.types.PropertyGroup` (`ChatHistoryProperties`) to persist conversation.

2. **Drawing approach**: Because Blender's UI doesn't support rich text natively:
   - Use `layout.box()` for each message bubble.
   - Use `row.label(text=...)` for text content (multi-line via `split()`).
   - Use `row.operator()` with icon for tool call entries.
   - Use `layout.template_text()` or a multi-line `StringProperty` for input.
   - A `UIList` or manually-drawn scroll region for conversation history.

3. **Polling for streaming updates**: Register a `bpy.app.timer` that checks a shared `deque` or `list` for new text chunks and redraws the panel via `context.area.tag_redraw()`.

### Phase 4: Integration with Add-on Registration

1. Update `addon/blender_mcp_addon/__init__.py`:
   - Import and register new classes (`BLMCP_PT_chat_panel`, new operators).
   - Add new preferences properties.
   - Extend `draw()` with LLM & Agent sections.
   - Hook `auto_start_agent` into the `_autostart_timer()` callback.
   - Add `register()` / `unregister()` entries for new classes.

2. Update `addon/blender_mcp_addon/blender_manifest.toml`:
   - No changes strictly required since this is all Python module additions within the existing addon.

### Phase 5: Chat Client as Text Editor Integration (Alternative UI)

1. In `ui_chat.py`, add a **Text Editor** integration mode:
   - Register a custom `SPACE_TEXT` panel in the Text Editor.
   - Operators: "Send as MCP Prompt", "Insert Response", "Chat History".
   - The user writes prompt text in a Text Editor buffer, clicks "Send", response is appended as a comment block or at the cursor.

## Decision Points & Branching

### Local vs. Remote LLM

```
LLM Mode?
├── Local → Is llama-server installed?
│   ├── Yes → Is a model downloaded?
│   │   ├── Yes → Start llama-server with model → Health check → Ready
│   │   └── No  → Show "Download Model" button → Download → Start
│   └── No  → Show llama_path config → Auto-detect or manual path
│               → If not found, show download/install instructions
│
└── Remote → Is API URL configured?
    ├── Yes → Validate with test request → Ready
    └── No  → Show configuration fields (URL, key, model)
```

### Startup Sequence

```
Auto-start enabled?
├── Yes → Agent starts after _AUTOSTART_DELAY:
│   1. Start MCP Bridge Server (existing TCP listener).
│   2. Start blender-mcp (HTTP subprocess on port 9191 or configurable).
│   3. If local mode: start llama-server.
│   4. Wait for health check on MCP server (retry up to N times).
│   5. Wait for LLM health check (retry up to N times).
│   6. Set status to "connected".
│   7. If any step fails → Store error in _State, show in prefs.
│
└── No  → User must press "Start Agent" in prefs or chat panel.
```

## Quality Checks

Before considering the implementation complete, verify:

| Check | Criteria |
|---|---|
| **Subprocess lifecycle** | `llama-server` and `blender-mcp` are started/stopped cleanly. No zombie processes on agent stop or Blender exit. |
| **Health check timeout** | Startup waits max 30s for `llama-server` to respond. Shows clear error if not ready. |
| **Chat input/output** | Multi-line text input works. Long responses scroll. Tool calls are visible in the UI. |
| **Streaming** | Text appears incrementally, not all at once after the full turn. |
| **Thread safety** | Async event loop runs on a daemon thread. UI updates via `bpy.app.timers`. No direct `bpy` calls from the background thread. |
| **Error handling** | LLM disconnects, MCP server crashes, and invalid responses show a clear error in the panel, not a hang. |
| **Stop mid-generation** | The "Stop" button cancels the current Future and prevents further tool calls. |
| **Preferences persistence** | All new prefs survive Blender restart. Downloaded model path is remembered. |
| **Remote API** | API key is stored (masked in UI). Connection test button works. Clear error if key is invalid. |
| **No external client needed** | User never needs to run `python chat_client.py` or `blender-mcp` manually. Everything starts from the add-on. |

## Edge Cases

| Situation | Handling |
|---|---|
| `llama-server` not on PATH | Show `llama_path` property with browse button. Auto-search common locations. |
| Model download interrupted | Partial file is cleaned up. User can retry. |
| Blender closes while LLM is running | Use `bpy.app.handlers.load_post` or `unregister()` to kill subprocesses. |
| Multiple Blender instances | Each has its own port for MCP server (configurable). LLM backend is shared (single `llama-server`). |
| Low VRAM / OOM | Recommend small GGUF model (Q4_K_M, 3B-8B params) in instructions. Consider adding VRAM check. |
| Remote API rate limit | Show error, suggest retry. Store last error in state. |
| Port conflicts | Auto-increment port if default is in use. Show which port was selected. |

## Suggested File Structure (Additions)

```
addon/blender_mcp_addon/
├── __init__.py              # Extended: new prefs, operators, registration
├── llm_manager.py           # NEW: LLM lifecycle management
├── agent_controller.py      # NEW: MCP client + conversation orchestrator
├── ui_chat.py               # NEW: Blender chat panel and Text Editor integration
├── mcp_to_blender_server.py # Existing (unchanged)
├── execute_interactive.py   # Existing (unchanged)
└── ...                      # Existing files unchanged
```

No existing files need structural changes — only `__init__.py` needs edits to register new classes and extend preferences.