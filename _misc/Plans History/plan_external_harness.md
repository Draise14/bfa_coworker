# BFA Coworker — External Harness + Upstream Sync + Feature Roadmap

**Date**: 2026-08-05
**Status**: Planning — Ready for Implementation

---

## Overview

Three parallel workstreams:

1. **Git Remote** — Add the original Blender MCP upstream as a git remote for cherry-pick merging
2. **External Harness "Advanced" Mode** — Restore the original bridge-only mode as a third preferences tab, supporting stdio and network MCP server transports
3. **Feature Roadmap** — Map competing MCP features (ahujasid/blender-mcp, Mixar-AI/mixar-app) and create adoption tiers

---

## Phase 1: Git Remote & Upstream Tracking

### 1.1 Add Upstream as Git Remote

**Command**:
```bash
git remote add upstream https://projects.blender.org/lab/blender_mcp.git
git fetch upstream
git remote -v
git log upstream/main --oneline -20
```

**Notes**:
- The upstream is on Blender's Gitea (not GitHub). Verify the clone URL works — Gitea may use `https://projects.blender.org/lab/blender_mcp.git` or a different pattern.
- The fork already contains the original code in `mcp/blmcp/` and `addon/bfa_coworker/vendor/blmcp/`. The remote is for tracking future upstream changes (if any) and cherry-picking.
- If the repo has been archived/made read-only, document this in `UPSTREAM_SYNC.md`.

### 1.2 Create `_misc/UPSTREAM_SYNC.md`

**File**: `_misc/UPSTREAM_SYNC.md`

**Contents**:
```markdown
# Upstream Sync Guide

## Remote
- **Name**: `upstream`
- **URL**: `https://projects.blender.org/lab/blender_mcp.git`
- **Status**: [Active / Archived / Read-Only]

## Directory Mapping

| Upstream Path | Fork Path | Notes |
|---|---|---|
| `addon/blender_mcp_addon/` | `addon/bfa_coworker/` | Heavily modified — cherry-pick with care |
| `mcp/blmcp/` | `mcp/blmcp/` | Mostly preserved — safe to cherry-pick |
| `backend/` | `backend/` | Preserved |
| `chat_client/` | `chat_client/` | Preserved |
| `tests/` | `tests/` | Preserved |

## Fork-Specific Additions (DO NOT OVERWRITE)
- `addon/bfa_coworker/llm_manager.py` — Local LLM lifecycle
- `addon/bfa_coworker/agent_controller.py` — Conversation loop + MCP server management
- `addon/bfa_coworker/ui_chat.py` — In-Blender chat panel
- `addon/bfa_coworker/operators_*.py` — UI operators
- `addon/bfa_coworker/shared.py` — Shared constants and helpers
- `addon/bfa_coworker/preferences.py` — Heavily extended preferences
- `addon/bfa_coworker/vendor/` — Vendored dependencies

## Workflow

### Review upstream changes
```bash
git fetch upstream
git log upstream/main --oneline -20
git diff HEAD..upstream/main --stat
```

### Cherry-pick a specific commit
```bash
git cherry-pick <commit-hash>
```

### Conflict resolution
- Conflicts in `mcp/blmcp/` are usually safe to accept upstream
- Conflicts in `addon/bfa_coworker/` require careful review — most changes are fork-specific
- Never accept upstream changes that remove `llm_manager.py`, `agent_controller.py`, `ui_chat.py`, or `shared.py`
```

---

## Phase 2: Restore External Harness "Advanced" Mode

### Architecture Overview

The current addon has a **three-layer self-contained stack**:

```
Blender Chat UI → agent_controller → LLM (local/remote)
                       ↓
              MCP Server (HTTP :9191)
                       ↓
              Bridge Server (TCP :9876)
                       ↓
              Blender Python API
```

The **External Harness** mode restores the original two-layer bridge-only model:

```
External MCP Client (Claude Desktop, Cursor, VS Code, etc.)
         │
         │ MCP Protocol (stdio or HTTP)
         ▼
   MCP Server (blmcp) — managed subprocess or user-managed
         │
         │ TCP Socket (null-delimited JSON)
         ▼
   Bridge Server (TCP :9876) — inside Blender
         │
         ▼
   Blender Python API
```

### 2.1 Add `agent_mode` Enum to `shared.py`

**File**: `addon/bfa_coworker/shared.py`

**Add after `REMOTE_PROVIDER_ITEMS`** (around line 120):

```python
# ── Agent Mode EnumProperty Items ────────────────────────────────────────

AGENT_MODE_ITEMS: list[tuple[str, str, str]] = [
    (
        "SELF_CONTAINED",
        "Self-Contained",
        "Built-in chat UI with managed local LLM or remote API — "
        "everything runs inside Blender (recommended for new users)",
    ),
    (
        "EXTERNAL_HARNESS",
        "External Harness",
        "Bridge-only mode — run the TCP bridge server inside Blender "
        "and connect an external MCP client (Claude Desktop, Cursor, "
        "VS Code, or any MCP-compatible tool)",
    ),
]

# ── MCP Server Mode EnumProperty Items ───────────────────────────────────

MCP_SERVER_MODE_ITEMS: list[tuple[str, str, str]] = [
    (
        "MANAGED",
        "Managed (HTTP)",
        "The addon manages the MCP server as a subprocess with HTTP transport — "
        "used by the built-in chat UI",
    ),
    (
        "STDIO",
        "Stdio (External Client)",
        "The MCP server runs via stdio — for external MCP clients like "
        "Claude Desktop, Cursor, or VS Code. The addon provides config snippets "
        "but does NOT manage the server process",
    ),
    (
        "NETWORK",
        "Network (HTTP Server)",
        "The MCP server listens on a configurable host:port with HTTP transport — "
        "for browser-based clients or remote connections",
    ),
]
```

### 2.2 Restructure Preferences into Three Tabs

**File**: `addon/bfa_coworker/preferences.py`

**Current structure** (single `draw()` method with sections):
```
LLM Configuration (local/remote toggle)
  ├── Local: llama-server status, model presets, download, existing models, advanced
  └── Remote: provider, API URL/key, model, test connection
Agent Control (autostart, ping)
Advanced Port Settings (offset, overrides, effective ports)
Diagnostics (debug only)
```

## ✅ Done

**New structure** (three tabs via `layout.prop_tabs_enum()` or manual buttons):

#### Tab 1: "Local LLM"
```
┌─ Local LLM ─────────────────────────────────────────────┐
│ llama-server: [Installed ✓]  [Download llama-server]     │
│                                                          │
│ Pick a Model                                             │
│ ┌─ Flagship (24 GB+ VRAM) ────────────────────────────┐ │
│ │ ○ Gemma 4 26B A4B (Q8_0)    [24-28 GB RAM]         │ │
│ │ ○ DeepSeek R1 Distill 32B   [20-24 GB RAM]         │ │
│ │ ○ Qwen 2.5 Coder 32B        [20-24 GB RAM]         │ │
│ └─────────────────────────────────────────────────────┘ │
│ ┌─ Mid-Range (12-20 GB VRAM) ─────────────────────────┐ │
│ │ ● Mistral Small 3.1 24B     [12-16 GB RAM]         │ │
│ │ ○ Gemma 4 26B A4B (Q4)      [16-20 GB RAM]         │ │
│ │ ...                                                  │ │
│ └─────────────────────────────────────────────────────┘ │
│ ┌─ Lightweight (≤ 8 GB VRAM) ─────────────────────────┐ │
│ │ ...                                                  │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                          │
│ Custom Model: [Mistral Small 3.1 24B (Q4_K_M)      ▼]  │
│                                                          │
│ [Download Model]  [Cancel]                               │
│                                                          │
│ Or use an existing model:                                │
│ [Scan]  [Open Folder]                                    │
│ Models Directory: [/home/user/bfa_coworker_models    📁] │
│                                                          │
│ Advanced                                                 │
│   Model Repo ID: [unsloth/Mistral-Small-3.1...       ]  │
│   Model Filename: [Mistral-Small-3.1-24B-...         ]  │
│   Context Window: [32768]  Max Output: [16384]          │
│   HF Token: [••••••••                                ]  │
└──────────────────────────────────────────────────────────┘
```

#### Tab 2: "Remote API"
```
┌─ Remote API ────────────────────────────────────────────┐
│ Provider: [OpenRouter                            ▼]     │
│   One key → 300+ models (OpenAI, Anthropic, etc.)       │
│                                                          │
│ API URL: [https://openrouter.ai/api                  ]  │
│ API Key: [••••••••                                   ]  │
│ API Key Help: Get your key at openrouter.ai/keys        │
│                                                          │
│ Model: [openai/gpt-4o                                ]  │
│ [Refresh Models]  [Browse Models]                        │
│ 245 models available from the API                        │
│                                                          │
│ [Test Connection]                                        │
└──────────────────────────────────────────────────────────┘
```

#### Tab 3: "Advanced"
```
┌─ Advanced ───────────────────────────────────────────────┐
│ Agent Mode:                                              │
│   ● Self-Contained  ○ External Harness                   │
│                                                          │
│ ── Bridge Server ─────────────────────────────────────── │
│ Host: [localhost]  Port: [9876]                          │
│ Status: ● Running on localhost:9876                      │
│ [Start Bridge]  [Stop Bridge]                            │
│                                                          │
│ ── MCP Server (External Harness) ─────────────────────── │
│ Mode: ○ Managed  ● Stdio  ○ Network                      │
│                                                          │
│ When "Stdio" is selected:                                │
│   ┌─ Claude Desktop Config ───────────────────────────┐ │
│   │ {                                                  │ │
│   │   "mcpServers": {                                  │ │
│   │     "bfa-coworker": {                              │ │
│   │       "command": "python",                         │ │
│   │       "args": ["-m", "blmcp", "--transport",       │ │
│   │                "stdio"],                            │ │
│   │       "env": {                                     │ │
│   │         "BFACW_HOST": "localhost",                 │ │
│   │         "BFACW_PORT": "9876"                       │ │
│   │       }                                            │ │
│   │     }                                              │ │
│   │   }                                                │ │
│   │ }                                                  │ │
│   │ [Copy to Clipboard]                                │ │
│   └───────────────────────────────────────────────────┘ │
│                                                          │
│ When "Network" is selected:                              │
│   MCP Host: [127.0.0.1]  MCP Port: [9191]               │
│   ⚠ Binding to non-localhost exposes the MCP server      │
│   to your network. Only do this on trusted networks.     │
│   [Start MCP Server]  [Stop MCP Server]                  │
│   Status: ● Listening on 127.0.0.1:9191                  │
│                                                          │
│ ── Agent Control ─────────────────────────────────────── │
│ Auto-Start Agent: [✓]                                    │
│ [Check Status]                                           │
│   Bridge: OK (port 9876)                                 │
│   MCP:    OK (port 9191)                                 │
│   LLM:    OK (port 8081)                                 │
│   Chat:   OK                                             │
│                                                          │
│ ── Port Settings ─────────────────────────────────────── │
│ Port Offset: [0]                                         │
│ Effective: Bridge 9876 | MCP 9191 | LLM 8081            │
│ Bridge Port: [0]  MCP Port: [0]  LLM Port: [0]          │
│                                                          │
│ ── Diagnostics (debug) ───────────────────────────────── │
│ [Check Ports]  [Diagnose]                                │
│ Benchmarks: [Objects] [Scene] [Animation] [Collections]  │
└──────────────────────────────────────────────────────────┘
```

**Implementation approach**:

The `draw()` method in `_BFACW_Preferences` will be refactored to use a tab selector. Blender doesn't have a built-in `prop_tabs_enum()` for preferences, so we'll use a manual approach:

```python
def draw(self, context):
    layout = self.layout

    # Tab selector row.
    row = layout.row(align=True)
    row.scale_y = 1.3
    for tab_id, tab_label, tab_icon in _PREF_TABS:
        is_active = (self.pref_tab == tab_id)
        op = row.operator(
            "bfacw.pref_tab_select",
            text=tab_label,
            icon=tab_icon,
            depress=is_active,
        )
        op.tab_id = tab_id

    layout.separator()

    # Draw the active tab.
    if self.pref_tab == 'LOCAL_LLM':
        self._draw_tab_local_llm(context)
    elif self.pref_tab == 'REMOTE_API':
        self._draw_tab_remote_api(context)
    elif self.pref_tab == 'ADVANCED':
        self._draw_tab_advanced(context)
```

**New properties to add to `_BFACW_Preferences`**:

```python
# ── Preferences Tab ──────────────────────────────────────────────────

pref_tab: EnumProperty(
    name="Tab",
    items=[
        ("LOCAL_LLM", "Local LLM", "Configure and download local models", 'CONSOLE', 0),
        ("REMOTE_API", "Remote API", "Configure remote API access", 'WORLD', 1),
        ("ADVANCED", "Advanced", "External harness, ports, and diagnostics", 'SETTINGS', 2),
    ],
    default="LOCAL_LLM",
)

# ── Agent Mode ───────────────────────────────────────────────────────

agent_mode: EnumProperty(
    name="Agent Mode",
    description="How the agent operates",
    items=AGENT_MODE_ITEMS,
    default="SELF_CONTAINED",
)

# ── MCP Server Mode (for External Harness) ───────────────────────────

mcp_server_mode: EnumProperty(
    name="MCP Server Mode",
    description="How the MCP server is launched in External Harness mode",
    items=MCP_SERVER_MODE_ITEMS,
    default="STDIO",
)

# ── MCP Server Network Settings ──────────────────────────────────────

mcp_server_host: StringProperty(
    name="MCP Server Host",
    description="Host for the MCP HTTP server in Network mode",
    default="127.0.0.1",
)

mcp_server_port_override: IntProperty(
    name="MCP Server Port",
    description="Port for the MCP HTTP server in Network mode (0 = use default 9191 + offset)",
    default=0,
    min=0,
    max=65535,
)
```

**New operator for tab switching** (in `preferences.py` or a new `operators_prefs.py`):

```python
class BFACW_OT_pref_tab_select(bpy.types.Operator):
    bl_idname = "bfacw.pref_tab_select"
    bl_label = "Select Preferences Tab"
    bl_description = "Switch to this preferences tab"
    bl_options = {'INTERNAL'}

    tab_id: bpy.props.StringProperty()

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        prefs.pref_tab = self.tab_id
        return {'FINISHED'}
```

### 2.3 Refactor `draw()` into Three Methods

**File**: `addon/bfa_coworker/preferences.py`

The current `draw()` method (~300 lines) will be split into:

1. **`draw()`** — Tab selector + dispatcher (~20 lines)
2. **`_draw_tab_local_llm(self, context)`** — Current local mode UI (~150 lines, extracted from existing `draw()`)
3. **`_draw_tab_remote_api(self, context)`** — Current remote mode UI (~60 lines, extracted from existing `draw()`)
4. **`_draw_tab_advanced(self, context)`** — NEW: agent mode, bridge, MCP server, ports, diagnostics (~120 lines)

The existing local/remote sections in `draw()` are already well-structured — they just need to be extracted into separate methods with minimal changes.

### 2.4 Implement Stdio-Mode MCP Server Support

**File**: `addon/bfa_coworker/agent_controller.py`

**New function**: `generate_mcp_client_config()`

```python
def generate_mcp_client_config(
    client_type: str = "claude",
    blender_host: str = "localhost",
    blender_port: int = 9876,
) -> str:
    """Generate MCP client configuration JSON for external tools.

    *client_type*: ``"claude"`` (Claude Desktop), ``"vscode"`` (VS Code / Cursor),
    or ``"generic"`` (generic JSON-RPC config).

    Returns a JSON string suitable for the client's config file.
    """
    import json

    if client_type == "claude":
        config = {
            "mcpServers": {
                "bfa-coworker": {
                    "command": "python",
                    "args": ["-m", "blmcp", "--transport", "stdio"],
                    "env": {
                        "BFACW_HOST": blender_host,
                        "BFACW_PORT": str(blender_port),
                    },
                }
            }
        }
    elif client_type == "vscode":
        config = {
            "servers": {
                "bfa-coworker": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "blmcp", "--transport", "stdio"],
                    "env": {
                        "BFACW_HOST": blender_host,
                        "BFACW_PORT": str(blender_port),
                    },
                }
            }
        }
    else:
        config = {
            "command": "python",
            "args": ["-m", "blmcp", "--transport", "stdio"],
            "env": {
                "BFACW_HOST": blender_host,
                "BFACW_PORT": str(blender_port),
            },
        }

    return json.dumps(config, indent=2)
```

**New operator**: `BFACW_OT_copy_mcp_config` (in `operators_agent.py` or `ui_chat.py`)

```python
class BFACW_OT_copy_mcp_config(bpy.types.Operator):
    """Copy MCP client configuration to the clipboard."""
    bl_idname = "bfacw.copy_mcp_config"
    bl_label = "Copy MCP Config"
    bl_description = "Copy the MCP client configuration to the clipboard"

    client_type: bpy.props.EnumProperty(
        name="Client",
        items=[
            ("claude", "Claude Desktop", "Claude Desktop config format"),
            ("vscode", "VS Code / Cursor", "VS Code / Cursor config format"),
        ],
        default="claude",
    )

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        bridge_port, _, _ = effective_ports(prefs)
        config = agent_controller.generate_mcp_client_config(
            client_type=self.client_type,
            blender_host=prefs.host,
            blender_port=bridge_port,
        )
        context.window_manager.clipboard = config
        self.report({"INFO"}, "MCP config copied to clipboard")
        return {"FINISHED"}
```

### 2.5 Implement Network-Accessible MCP Server Mode

**File**: `addon/bfa_coworker/agent_controller.py`

**New function**: `start_mcp_server_network()`

```python
def start_mcp_server_network(
    host: str = "127.0.0.1",
    port: int = 9191,
    blender_host: str = "localhost",
    blender_port: int = 9876,
) -> subprocess.Popen | None:
    """Launch the MCP server in network (HTTP) mode for external clients.

    This is similar to ``start_mcp_server()`` but binds to a configurable
    *host*:*port* instead of always using 127.0.0.1.  Useful for:
    - Browser-based MCP clients on the same machine
    - Remote MCP clients on the same network (use with caution)

    Returns the ``Popen`` handle, or ``None`` on failure.
    """
    global _mcp_server_process, _mcp_shutting_down

    if _mcp_shutting_down:
        print("[🛠️Coworker] start_mcp_server_network: shutdown in progress — skipping")
        return None

    # Kill existing process if known.
    if _mcp_server_process is not None:
        try:
            _mcp_server_process.terminate()
            _mcp_server_process.wait(timeout=3)
        except Exception:
            try:
                _mcp_server_process.kill()
            except Exception:
                pass
        _mcp_server_process = None
        import time
        time.sleep(0.5)

    # Kill any stale process on the port.
    _kill_process_on_port(port)
    import time
    time.sleep(0.5)

    env = os.environ.copy()
    env["BFACW_HOST"] = blender_host
    env["BFACW_PORT"] = str(blender_port)

    # Use the same Python resolution as start_mcp_server().
    mcp_exe, use_module = _resolve_mcp_python()

    if not mcp_exe:
        _agent_state.error = "Cannot find Python to run MCP server"
        return None

    try:
        if use_module:
            proc = subprocess.Popen(
                [mcp_exe, "-m", "blmcp", "--transport", "http",
                 "--host", host, "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        else:
            proc = subprocess.Popen(
                [mcp_exe, "--transport", "http",
                 "--host", host, "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
    except (FileNotFoundError, OSError) as ex:
        _agent_state.error = "Failed to launch MCP server: {:s}".format(str(ex))
        return None

    _mcp_server_process = proc
    _agent_state.mcp_server_running = True
    _agent_state.error = ""

    # Drain pipes.
    _start_pipe_drainer(proc)

    # Wait for port.
    import time
    time.sleep(0.5)
    if proc.poll() is not None:
        _agent_state.error = "MCP server exited immediately"
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    port_ready = _wait_for_port(host, port, timeout=15.0, interval=1.0)
    if not port_ready:
        _agent_state.error = "MCP server port {:d} never accepted connections".format(port)
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    return proc
```

**Refactor**: Extract `_resolve_mcp_python()` from `start_mcp_server()` to avoid code duplication:

```python
def _resolve_mcp_python() -> tuple[str | None, bool]:
    """Resolve the Python executable and whether to use ``-m blmcp``.

    Returns ``(python_path, use_module)`` where *use_module* is True
    when the MCP server should be launched via ``python -m blmcp``.
    """
    mcp_exe = (
        shutil.which("bfa-coworker-mcp") or
        shutil.which("bfa-coworker-mcp.exe") or
        shutil.which("bfa-coworker-mcp.bat")
    )
    if mcp_exe:
        return (mcp_exe, False)

    if not _ensure_vendor_deps():
        return (None, False)

    blender_py = _find_blender_python()
    if blender_py:
        return (blender_py, True)

    mcp_exe = shutil.which("python") or "python"
    return (mcp_exe, True)
```

### 2.6 Add Bridge-Only Mode

**File**: `addon/bfa_coworker/__init__.py`

**Modify `_autostart_agent_timer()`** to check `agent_mode`:

```python
def _autostart_agent_timer() -> None:
    """Deferred timer callback that starts the full agent (MCP server + LLM)."""
    from . import ui_chat

    if bpy.app.background:
        return

    prefs = bpy.context.preferences.addons[__package__].preferences
    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)

    # In External Harness mode, only the bridge server is needed.
    # The MCP server and LLM are managed externally.
    if prefs.agent_mode == "EXTERNAL_HARNESS":
        print("Agent auto-start: External Harness mode — bridge only")
        return

    # ... rest of existing self-contained startup logic ...
```

**Modify `BFACW_OT_agent_start.execute()`** in `ui_chat.py` to check `agent_mode`:

```python
def execute(self, context):
    prefs = context.preferences.addons[__package__].preferences

    # In External Harness mode, only start the bridge.
    if prefs.agent_mode == "EXTERNAL_HARNESS":
        return self._start_bridge_only(context)

    # ... existing self-contained startup logic ...
```

**New method**: `_start_bridge_only()` on `BFACW_OT_agent_start`:

```python
def _start_bridge_only(self, context):
    """Start only the bridge server (External Harness mode)."""
    wm = context.window_manager
    props = wm.bfacw_chat_props

    if mcp_to_blender_server.is_running():
        self.report({"INFO"}, "Bridge server already running")
        props.chat_status = "External Harness — Bridge on port {:d}".format(
            effective_ports(context.preferences.addons[__package__].preferences)[0])
        return {"FINISHED"}

    if bpy.app.background:
        self.report({"ERROR"}, "Cannot start in background mode")
        return {"CANCELLED"}

    prefs = context.preferences.addons[__package__].preferences
    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
    try:
        mcp_to_blender_server.start(prefs.host, _bridge_port)
    except Exception as ex:
        self.report({"ERROR"}, "Bridge server failed: {:s}".format(str(ex)))
        return {"CANCELLED"}

    from . import execute_interactive
    bpy.app.timers.register(
        execute_interactive.run,
        first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
        persistent=True,
    )

    props.chat_status = "External Harness — Bridge on port {:d}".format(_bridge_port)
    self.report({"INFO"}, "Bridge server started on port {:d}".format(_bridge_port))
    _redraw_areas(context)
    return {"FINISHED"}
```

### 2.7 Update Chat UI for External Harness Mode

**File**: `addon/bfa_coworker/ui_chat.py`

**Modify `BFACW_PT_chat_panel.draw()`** to show harness-specific UI:

```python
def draw(self, context):
    layout = self.layout
    wm = context.window_manager
    props = wm.bfacw_chat_props
    state = agent_controller._agent_state

    prefs = context.preferences.addons[__package__].preferences
    is_harness = (prefs.agent_mode == "EXTERNAL_HARNESS")

    # ── Agent control buttons ──
    row = layout.row(align=True)
    row.scale_y = 2.0
    if state.mcp_server_running or mcp_to_blender_server.is_running():
        row.operator("bfacw.agent_stop", icon="CANCEL", text="Stop Agent")
    else:
        row.operator("bfacw.agent_start", icon="PLAY",
                     text="Start Bridge" if is_harness else "Start Agent")

    # ── Status ──
    if is_harness:
        bridge_running = mcp_to_blender_server.is_running()
        if bridge_running:
            prefs = context.preferences.addons[__package__].preferences
            _bridge_port, _, _ = effective_ports(prefs)
            status = "External Harness — Bridge on port {:d}".format(_bridge_port)
        else:
            status = "Bridge Offline"
    else:
        # ... existing status logic ...

    row = layout.row()
    row.label(text="Status: {:s}".format(status), icon=(
        'CHECKMARK' if (mcp_to_blender_server.is_running() if is_harness else state.mcp_server_running) else 'X'
    ))

    # ── External Harness: Config & Instructions ──
    if is_harness and mcp_to_blender_server.is_running():
        box = layout.box()
        box.label(text="Connect an External MCP Client", icon='WORLD')

        # Copy config buttons.
        row = box.row(align=True)
        op = row.operator("bfacw.copy_mcp_config", text="Claude Desktop Config", icon='COPYDOWN')
        op.client_type = "claude"
        op = row.operator("bfacw.copy_mcp_config", text="VS Code Config", icon='COPYDOWN')
        op.client_type = "vscode"

        # Instructions.
        box.label(text="1. Copy the config above to your clipboard", icon='DOT')
        box.label(text="2. Paste into your MCP client's config file", icon='DOT')
        box.label(text="3. Restart your MCP client", icon='DOT')
        box.label(text="4. The client will connect to Blender's bridge", icon='DOT')

        # MCP server mode selector.
        box.separator()
        box.label(text="MCP Server Mode:", icon='SETTINGS')
        box.prop(prefs, "mcp_server_mode", expand=True)

        if prefs.mcp_server_mode == "NETWORK":
            box.prop(prefs, "mcp_server_host")
            row = box.row(align=True)
            row.prop(prefs, "mcp_server_port_override")
            if prefs.mcp_server_host not in ("127.0.0.1", "localhost", "::1"):
                box.label(
                    text="⚠ Binding to non-localhost exposes the MCP server to your network!",
                    icon='ERROR',
                )
            row = box.row(align=True)
            if agent_controller._agent_state.mcp_server_running:
                row.operator("bfacw.mcp_server_stop", icon="CANCEL", text="Stop MCP Server")
            else:
                row.operator("bfacw.mcp_server_start", icon="PLAY", text="Start MCP Server")

        layout.separator()

    # ── In harness mode, disable chat input ──
    if is_harness:
        layout.label(text="Chat is handled by your external MCP client.", icon='INFO')
        layout.label(text="Messages below are read-only monitoring.", icon='INFO')
    else:
        # ... existing input area and action buttons ...
        layout.textbox(props, "chat_input")
        row = layout.row(align=True)
        row.scale_y = 1.5
        if state.is_thinking:
            row.operator("bfacw.chat_stop", icon="PAUSE", text="Stop")
        else:
            row.operator("bfacw.chat_send", icon="PLAY", text="Send")
        row.operator("bfacw.chat_clear", icon="X", text="Clear")

    layout.separator()

    # ── Conversation history (always shown, read-only in harness mode) ──
    # ... existing history display logic ...
```

### 2.8 New Operators for MCP Server Management

**File**: `addon/bfa_coworker/operators_agent.py` (or new `operators_server.py` section)

```python
class BFACW_OT_mcp_server_start(bpy.types.Operator):
    """Start the MCP server in Network mode."""
    bl_idname = "bfacw.mcp_server_start"
    bl_label = "Start MCP Server"
    bl_description = "Start the MCP HTTP server for external clients"

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _mcp_port, _ = effective_ports(prefs)

        mcp_host = prefs.mcp_server_host
        mcp_port = prefs.mcp_server_port_override if prefs.mcp_server_port_override > 0 else _mcp_port

        proc = agent_controller.start_mcp_server_network(
            host=mcp_host,
            port=mcp_port,
            blender_host=prefs.host,
            blender_port=_bridge_port,
        )
        if proc is None:
            self.report({"ERROR"}, agent_controller._agent_state.error)
            return {"CANCELLED"}

        self.report({"INFO"}, "MCP server started on {:s}:{:d}".format(mcp_host, mcp_port))
        return {"FINISHED"}


class BFACW_OT_mcp_server_stop(bpy.types.Operator):
    """Stop the MCP server."""
    bl_idname = "bfacw.mcp_server_stop"
    bl_label = "Stop MCP Server"
    bl_description = "Stop the MCP HTTP server"

    def execute(self, context):
        agent_controller.stop_mcp_server()
        self.report({"INFO"}, "MCP server stopped")
        return {"FINISHED"}
```

### 2.9 Registration Updates

**File**: `addon/bfa_coworker/__init__.py`

Add new classes to `_classes` tuple:

```python
_classes = (
    # ... existing classes ...
    BFACW_OT_pref_tab_select,      # NEW
    BFACW_OT_copy_mcp_config,       # NEW
    BFACW_OT_mcp_server_start,      # NEW
    BFACW_OT_mcp_server_stop,       # NEW
)
```

---

## Phase 3: Feature Roadmap from Competing MCPs

### 3.1 Feature Comparison Matrix

**File**: `_misc/FEATURE_ROADMAP.md`

| # | Feature | ahujasid/blender-mcp | Mixar-AI/mixar-app | Original Blender MCP | BFA Coworker (current) | Tier | Effort |
|---|---|---|---|---|---|---|---|
| **Core Infrastructure** |
| 1 | TCP Bridge Server | ✅ (port 9876) | ❌ (WS-based) | ✅ (port 9876) | ✅ (port 9876) | — | — |
| 2 | MCP Server (FastMCP) | ✅ (stdio) | ❌ (custom backend) | ✅ (stdio + HTTP) | ✅ (HTTP managed) | — | — |
| 3 | Local LLM (llama.cpp) | ❌ | ❌ (hosted backend) | ❌ | ✅ (llama-server) | — | — |
| 4 | Remote API (OpenAI-compat) | ❌ (via MCP client) | ✅ (BYOK) | ❌ | ✅ (OpenRouter) | — | — |
| 5 | Self-Contained (no external deps) | ❌ | ❌ (needs backend) | ❌ | ✅ | — | — |
| 6 | External Harness Mode | ✅ (default) | ❌ | ✅ (default) | ❌ → **Phase 2** | — | — |
| **Chat & UX** |
| 7 | In-Blender Chat Panel | ❌ | ✅ (C++ native) | ❌ | ✅ (Python) | — | — |
| 8 | Agent/Ask Mode Toggle | ❌ | ✅ | ❌ | ❌ | **T1** | Low |
| 9 | @Mention Autocomplete | ❌ | ✅ (C++) | ❌ | ❌ | **T2** | Med |
| 10 | Project Rules System | ❌ | ✅ | ❌ | ❌ | **T2** | Med |
| 11 | Floating Agent Bubble | ❌ | ✅ (C++) | ❌ | ❌ | T3 | High |
| 12 | Conversation History | ❌ (client-side) | ✅ | ❌ | ✅ (JSON files) | — | — |
| 13 | Streaming Text Display | ❌ (client-side) | ✅ (SSE) | ❌ | ❌ (polling) | T3 | Med |
| **Tools & Execution** |
| 14 | Execute Blender Code | ✅ | ✅ (WS script) | ✅ | ✅ | — | — |
| 15 | Scene/Object Info | ✅ | ✅ | ✅ | ✅ | — | — |
| 16 | Viewport Screenshot | ✅ (GPU offscreen) | ✅ | ✅ (window grab) | ✅ | — | — |
| 17 | Deferred/Long-Running Tools | ❌ | ✅ (job queue) | ✅ (deferred) | ✅ (deferred) | — | — |
| 18 | Operation History Log | ❌ | ✅ (JSONL) | ❌ | ❌ | **T1** | Low |
| 19 | Documentation Search | ❌ | ❌ | ✅ (RST) | ✅ (RST) | — | — |
| **Asset & 3D Generation** |
| 20 | Poly Haven Integration | ✅ | ❌ | ❌ | ❌ | **T1** | Low |
| 21 | Sketchfab Integration | ✅ | ❌ | ❌ | ❌ | **T2** | Med |
| 22 | AI 3D Generation (Hyper3D) | ✅ | ✅ (Hunyuan/Tripo) | ❌ | ❌ | T3 | High |
| 23 | AI 3D Generation (Hunyuan3D) | ✅ | ✅ | ❌ | ❌ | T3 | High |
| 24 | Auto-Rig / Auto-UV | ❌ | ✅ (Tripo) | ❌ | ❌ | T3 | High |
| **Infrastructure** |
| 25 | Transport Liveness Monitor | ❌ | ✅ | ❌ | ❌ (basic ping) | **T1** | Low |
| 26 | BYOK Multi-Provider | ❌ | ✅ | ❌ | ❌ (single provider) | **T2** | Med |
| 27 | Reconnect-Resume (SSE) | ❌ | ✅ | ❌ | ❌ | T3 | High |
| 28 | Telemetry | ✅ (Supabase) | ❌ | ❌ | ❌ | T4 | — |
| 29 | Crash Resilience | ❌ | ✅ (battle-tested) | ❌ | ❌ | T3 | High |
| **Distribution** |
| 30 | PyPI Package | ✅ (`blender-mcp`) | ❌ (Blender fork) | ❌ | ❌ | T4 | Med |
| 31 | Single-File Addon | ✅ (`addon.py`) | ❌ | ❌ | ❌ | T4 | Med |
| 32 | Blender Extension (.mcpb) | ❌ | ❌ | ✅ | ✅ (build_addon.py) | — | — |

### 3.2 Adoption Tiers

#### Tier 1 — High Value / Low Effort (Implement Soon)

| # | Feature | Source | Files to Modify | Est. LOC | Description |
|---|---|---|---|---|---|
| 8 | **Agent/Ask Mode Toggle** | Mixar | `ui_chat.py`, `agent_controller.py`, `shared.py` | ~50 | Simple enum toggle in chat panel header. Agent mode: LLM can execute tools. Ask mode: read-only Q&A. |
| 18 | **Operation History Log** | Mixar | `agent_controller.py`, `mcp_to_blender_server.py` | ~30 | Append each tool execution to a local JSONL file. Agent can read its own history for context. |
| 25 | **Transport Liveness Monitor** | Mixar | `agent_controller.py`, `ui_chat.py` | ~40 | Separate fast liveness check (~20s) from slow connection state (~45s). Enhanced status pill in chat UI. |
| 20 | **Poly Haven Integration** | ahujasid | `mcp/blmcp/tools/` (new files) | ~200 | Free, no API key. Search + download HDRIs/textures/models. Full PBR node setup. MIT-licensed reference code available. |

#### Tier 2 — Medium Value / Medium Effort (Plan for Next Release)

| # | Feature | Source | Files to Modify | Est. LOC | Description |
|---|---|---|---|---|---|
| 9 | **@Mention Autocomplete** | Mixar | `ui_chat.py` (new operator) | ~150 | Typing `@` in chat shows scene objects dropdown. Pure Python via `bpy.types.Operator` search popup. |
| 10 | **Project Rules System** | Mixar | `ui_chat.py`, `agent_controller.py` | ~130 | Per-file and global rules prepended to system prompt. JSON file storage. |
| 21 | **Sketchfab Integration** | ahujasid | `mcp/blmcp/tools/` (new files) | ~250 | Requires API key. Search + thumbnail preview + download with size normalization. |
| 26 | **BYOK Multi-Provider** | Mixar | `preferences.py`, `llm_manager.py` | ~100 | Extend remote provider system to support multiple saved provider profiles. |

#### Tier 3 — High Value / High Effort (Long-Term Consideration)

| # | Feature | Source | Est. LOC | Notes |
|---|---|---|---|---|
| 22-23 | AI 3D Generation | ahujasid | ~500+ | Requires API keys, async polling, complex import. Only if user demand. |
| 11 | Floating Agent Bubble | Mixar | ~400+ | GPU drawing, modal operators, window management. Complex. |
| 27 | Reconnect-Resume SSE | Mixar | ~300+ | Requires backend infrastructure. Not applicable to self-contained mode. |
| 13 | Streaming Text Display | Mixar | ~200 | SSE-based streaming instead of polling. |
| 29 | Crash Resilience | Mixar | ~200 | Edge-case fixes for save/load, temp Main frees, etc. |

#### Tier 4 — Not Applicable or Low Priority

| # | Feature | Reason |
|---|---|---|
| 4 (Mixar) | Custom C++ Editor Spaces | Fork-only — requires Blender source modification |
| 4 (Mixar) | Native Keyring Integration | Fork-only — C++ auth callbacks |
| 4 (Mixar) | Viewport Lock with Breathing Glow | Fork-only — C++ viewport enhancements |
| 4 (Mixar) | Custom Blender Build System | Fork-only — CMake overlay pattern |
| 28 | Telemetry | Not aligned with BFA Coworker's privacy goals |
| 30 | PyPI Package | Already distributed as Blender extension |
| 31 | Single-File Addon | Already modularized into multiple files |

### 3.3 Tier 1 Implementation Plans

#### 3.3.1 Agent/Ask Mode Toggle

**File**: `_misc/features/agent_ask_mode.md`

```markdown
# Agent/Ask Mode Toggle

**Source**: Mixar-AI/mixar-app
**Tier**: 1 — High Value / Low Effort
**Est. LOC**: ~50

## Motivation
Users sometimes want to ask the LLM questions without it modifying their scene.
An Agent/Ask toggle gives them control: Agent mode allows tool execution, Ask mode
is read-only Q&A.

## Reference Implementation
Mixar's `modules/agent/` — `AgentMode.AGENT` vs `AgentMode.ASK`. In Ask mode,
the backend skips tool execution and returns text-only responses.

## Implementation Steps

1. **Add `chat_mode` enum to `shared.py`**:
   ```python
   CHAT_MODE_ITEMS = [
       ("AGENT", "Agent", "LLM can execute tools and modify the scene"),
       ("ASK", "Ask", "LLM answers questions without modifying anything"),
   ]
   ```

2. **Add `chat_mode` property to `ChatHistoryProperties`** in `ui_chat.py`:
   ```python
   chat_mode: EnumProperty(
       name="Mode",
       items=CHAT_MODE_ITEMS,
       default="AGENT",
   )
   ```

3. **Add toggle button in chat panel header** (in `BFACW_PT_chat_panel.draw()`):
   ```python
   row = layout.row(align=True)
   row.prop(props, "chat_mode", expand=True)
   ```

4. **Modify `run_conversation_turn()`** in `agent_controller.py`:
   - When `chat_mode == "ASK"`, skip tool execution loop
   - Pass `tool_choice="none"` to the LLM API call
   - Return text response directly

5. **Files to modify**:
   - `addon/bfa_coworker/shared.py` — add `CHAT_MODE_ITEMS`
   - `addon/bfa_coworker/ui_chat.py` — add property + toggle button
   - `addon/bfa_coworker/agent_controller.py` — skip tools in Ask mode
```

#### 3.3.2 Operation History Log

**File**: `_misc/features/operation_history.md`

```markdown
# Operation History Log

**Source**: Mixar-AI/mixar-app
**Tier**: 1 — High Value / Low Effort
**Est. LOC**: ~30

## Motivation
The LLM sometimes repeats operations it already performed because it has no memory
of what it did. A local JSONL log of every tool execution gives the agent awareness
of its own history.

## Reference Implementation
Mixar's `operation_history/core/tools.py:run_tool()` — appends to a local
`operations.jsonl` file. The agent can read its own history via a tool.

## Implementation Steps

1. **Add `_log_operation()` to `agent_controller.py`**:
   ```python
   def _log_operation(tool_name: str, params: dict, result: str) -> None:
       import json, time
       log_path = Path(bpy.utils.user_resource("SCRIPTS")) / "bfa_coworker_operations.jsonl"
       entry = {
           "timestamp": time.time(),
           "tool": tool_name,
           "params": params,
           "result": result[:500],  # Truncate for log size.
       }
       with open(log_path, "a", encoding="utf-8") as f:
           f.write(json.dumps(entry) + "\n")
   ```

2. **Call `_log_operation()` from `_call_mcp_tool_sync()`** after each tool execution.

3. **Add `get_operation_history` tool** to `mcp/blmcp/tools/`:
   - Reads the JSONL file
   - Returns last N operations as text
   - The LLM can call this to check what it already did

4. **Files to modify**:
   - `addon/bfa_coworker/agent_controller.py` — add logging
   - `mcp/blmcp/tools/get_operation_history.py` — NEW tool
```

#### 3.3.3 Transport Liveness Monitor

**File**: `_misc/features/transport_liveness.md`

```markdown
# Transport Liveness Monitor

**Source**: Mixar-AI/mixar-app
**Tier**: 1 — High Value / Low Effort
**Est. LOC**: ~40

## Motivation
The current status display only updates when the user manually checks or when
an operation completes. A fast liveness monitor gives real-time feedback on
connection health.

## Reference Implementation
Mixar's WebSocket transport: `is_transport_live` (~20s recv timeout) separate
from `is_connected` (45s watchdog). A 2s main-thread timer repaints the status
pill on liveness flips.

## Implementation Steps

1. **Add liveness tracking to `AgentState`** in `agent_controller.py`:
   ```python
   last_bridge_activity: float = 0.0
   last_mcp_activity: float = 0.0
   last_llm_activity: float = 0.0
   bridge_live: bool = False
   mcp_live: bool = False
   llm_live: bool = False
   ```

2. **Update timestamps on activity**:
   - Bridge: update on each successful `send_code()` call
   - MCP: update on each successful tool list/call
   - LLM: update on each successful chat completion

3. **Add `_check_liveness()` to `agent_controller.py`**:
   ```python
   def _check_liveness() -> None:
       import time
       now = time.monotonic()
       _agent_state.bridge_live = (now - _agent_state.last_bridge_activity) < 20
       _agent_state.mcp_live = (now - _agent_state.last_mcp_activity) < 20
       _agent_state.llm_live = (now - _agent_state.last_llm_activity) < 20
   ```

4. **Enhance status display in `ui_chat.py`**:
   - Show per-service liveness dots (🟢/🔴)
   - Auto-refresh via existing `chat_timer_update()` at 0.5s interval

5. **Files to modify**:
   - `addon/bfa_coworker/agent_controller.py` — liveness tracking
   - `addon/bfa_coworker/ui_chat.py` — enhanced status display
```

#### 3.3.4 Poly Haven Integration

**File**: `_misc/features/polyhaven_integration.md`

```markdown
# Poly Haven Integration

**Source**: ahujasid/blender-mcp (MIT License)
**Tier**: 1 — High Value / Low Effort
**Est. LOC**: ~200

## Motivation
Poly Haven provides free, high-quality HDRIs, textures, and 3D models.
No API key required. The ahujasid implementation is MIT-licensed and can be
adapted directly.

## Reference Implementation
- `ahujasid/blender-mcp` → `src/blender_mcp/tools/polyhaven.py`
- Public REST API: `https://api.polyhaven.com/`

## Implementation Steps

1. **Create `mcp/blmcp/tools/search_polyhaven_assets.py`**:
   - MCP tool: `search_polyhaven_assets(category, query)`
   - Calls `https://api.polyhaven.com/assets?type={category}`
   - Returns top 20 results with names, thumbnails, and download URLs

2. **Create `mcp/blmcp/tools/download_polyhaven_asset.py`**:
   - MCP tool: `download_polyhaven_asset(asset_id, asset_type, resolution)`
   - Downloads asset files from `https://dl.polyhaven.com/file/ph-assets/`
   - For HDRIs: creates world environment shader
   - For textures: creates full PBR material node tree
   - For models: imports glTF/FBX/OBJ

3. **Create `mcp/blmcp/tools/set_polyhaven_texture.py`**:
   - MCP tool: `set_polyhaven_texture(object_name, texture_path)`
   - Applies downloaded texture to object with PBR node setup
   - Handles Blender version differences (4.0+ `ShaderNodeSeparateColor` vs pre-4.0)

4. **Create `mcp/blmcp/tools/get_polyhaven_status.py`**:
   - MCP tool: `get_polyhaven_status()`
   - Checks Poly Haven API availability

5. **Files to create**:
   - `mcp/blmcp/tools/search_polyhaven_assets.py`
   - `mcp/blmcp/tools/download_polyhaven_asset.py`
   - `mcp/blmcp/tools/set_polyhaven_texture.py`
   - `mcp/blmcp/tools/get_polyhaven_status.py`

6. **Auto-discovery**: Tools are auto-registered via `pkgutil.iter_modules()` —
   no changes needed to `mcp/blmcp/__init__.py`.
```

---

## Implementation Order

### Recommended Sequence

1. **Phase 1** (git remote) — 5 minutes, no code changes
   - Add upstream remote
   - Create `_misc/UPSTREAM_SYNC.md`

2. **Phase 2** (external harness) — core code changes
   - 2.1: Add `AGENT_MODE_ITEMS` and `MCP_SERVER_MODE_ITEMS` to `shared.py`
   - 2.2-2.3: Restructure preferences into 3 tabs
   - 2.4: Add `generate_mcp_client_config()` and `BFACW_OT_copy_mcp_config`
   - 2.5: Add `start_mcp_server_network()` and refactor `_resolve_mcp_python()`
   - 2.6: Add bridge-only mode to `__init__.py` and `ui_chat.py`
   - 2.7: Update chat UI for harness mode
   - 2.8: Add MCP server start/stop operators
   - 2.9: Update registration in `__init__.py`

3. **Phase 3** (feature roadmap) — documentation only
   - 3.1-3.2: Create `_misc/FEATURE_ROADMAP.md`
   - 3.3: Create `_misc/features/*.md` for Tier 1 features

### Files Modified (Summary)

| File | Phase | Changes |
|---|---|---|
| `addon/bfa_coworker/shared.py` | 2.1 | Add `AGENT_MODE_ITEMS`, `MCP_SERVER_MODE_ITEMS` |
| `addon/bfa_coworker/preferences.py` | 2.2-2.3 | Add `pref_tab`, `agent_mode`, `mcp_server_mode`, `mcp_server_host`, `mcp_server_port_override`; refactor `draw()` into 3 tab methods; add `BFACW_OT_pref_tab_select` |
| `addon/bfa_coworker/agent_controller.py` | 2.4-2.5 | Add `generate_mcp_client_config()`, `start_mcp_server_network()`, refactor `_resolve_mcp_python()` |
| `addon/bfa_coworker/ui_chat.py` | 2.6-2.7 | Add harness mode UI, `BFACW_OT_copy_mcp_config`, bridge-only start |
| `addon/bfa_coworker/operators_agent.py` | 2.8 | Add `BFACW_OT_mcp_server_start`, `BFACW_OT_mcp_server_stop` |
| `addon/bfa_coworker/__init__.py` | 2.6, 2.9 | Modify `_autostart_agent_timer()`, register new classes |
| `_misc/UPSTREAM_SYNC.md` | 1.2 | NEW — upstream sync documentation |
| `_misc/FEATURE_ROADMAP.md` | 3.1-3.2 | NEW — feature comparison and roadmap |
| `_misc/features/agent_ask_mode.md` | 3.3 | NEW — Tier 1 implementation plan |
| `_misc/features/operation_history.md` | 3.3 | NEW — Tier 1 implementation plan |
| `_misc/features/transport_liveness.md` | 3.3 | NEW — Tier 1 implementation plan |
| `_misc/features/polyhaven_integration.md` | 3.3 | NEW — Tier 1 implementation plan |

---

## Verification Checklist

- [ ] `git remote -v` shows `upstream` pointing to `https://projects.blender.org/lab/blender_mcp.git`
- [ ] `git fetch upstream` succeeds
- [ ] Preferences show 3 distinct tabs: Local LLM, Remote API, Advanced
- [ ] Switching tabs works correctly (state preserved between tabs)
- [ ] External Harness mode: bridge starts on configured port
- [ ] External Harness mode: MCP server does NOT auto-start
- [ ] External Harness mode: chat panel shows harness status + config buttons
- [ ] "Copy MCP Config" produces valid JSON for Claude Desktop
- [ ] "Copy MCP Config" produces valid JSON for VS Code
- [ ] Network mode: MCP server starts on configured host:port
- [ ] Network mode: `curl http://localhost:9191/health` responds
- [ ] Network mode: security warning shows for non-localhost binding
- [ ] Self-Contained mode still works: download → chat → tool execution
- [ ] `_misc/FEATURE_ROADMAP.md` covers all three competing projects
- [ ] `_misc/features/*.md` exist for all Tier 1 features
- [ ] `build_addon.py` succeeds

---

## Decisions

| Decision | Rationale |
|---|---|
| **Git remote + cherry-pick** (not subtree) | Simpler. Upstream is likely frozen. Cherry-pick gives fine-grained control. |
| **Three tabs** (not prop_tabs_enum) | Blender preferences don't support `prop_tabs_enum()` natively. Manual tab buttons are more reliable. |
| **Self-Contained remains default** | Preserves current UX for new users. External harness is opt-in via Advanced tab. |
| **No code removal** | All existing self-contained functionality is preserved. External harness is additive. |
| **MCP server already supports stdio + HTTP** | The `mcp/blmcp/__init__.py` already has both transports via `--transport` flag. We just expose them via preferences. |
| **Feature roadmap is documentation-only** | Tier 1 implementation plans are created but not committed to code yet. This keeps the current PR focused. |
| **`_resolve_mcp_python()` refactor** | Extracted from `start_mcp_server()` to avoid duplicating the Python resolution logic in `start_mcp_server_network()`. |

---

## Further Considerations

1. **Blender Gitea git access**: Verify the clone URL. If the repo has been archived, document this in `UPSTREAM_SYNC.md` and skip the remote add.

2. **MCP server transport flags**: The current `mcp/blmcp/__init__.py` `main()` already accepts `--transport`, `--host`, and `--port`. No changes needed to the MCP server itself.

3. **Security warning for network mode**: When MCP server binds to `0.0.0.0` or non-localhost, show a prominent warning. Consider adding a confirmation dialog before enabling non-localhost binding.

4. **Backward compatibility**: The `agent_mode` property defaults to `"SELF_CONTAINED"`. Existing users upgrading will see no change in behavior.

5. **Port conflict handling**: The existing `check_ports_available()` and `_kill_process_on_port()` utilities already handle port conflicts. The new network mode reuses these.

6. **PYTHONPATH for stdio mode**: When users run `python -m blmcp --transport stdio` externally, they need `vendor/deps/` and `vendor/blmcp/` on `PYTHONPATH`. The "Copy Config" button should include `PYTHONPATH` in the `env` section of the config.