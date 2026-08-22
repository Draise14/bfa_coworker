# Harness Testing Guide — Developer Documentation

This guide explains how to test the External Harness mode of bfa_coworker
without a real MCP client, how to verify the bridge is working, and how to
debug common issues.

## Quick Start: Manual STDIO Test

The fastest way to verify the bridge is working is to run the MCP server
manually in stdio mode and send it a `tools/list` request.

### 1. Start the Bridge in Blender

1. Open Blender with the bfa_coworker addon enabled
2. Set Operating Mode to **External Harness** in the addon preferences
3. Click **Start Bridge** in the chat panel (or it auto-starts)
4. Verify the status shows: `External Harness — Bridge on port 9876`

### 2. Run the MCP Server Manually

Open a terminal and run:

```bash
# Windows (using Blender's Python — recommended)
"C:\Program Files\Blender Foundation\Blender\4.3\python\bin\python.exe" ^
  -m blmcp --transport stdio

# Linux/macOS
/path/to/blender/4.3/python/bin/python3 -m blmcp --transport stdio
```

Set these environment variables so the MCP server knows where Blender is:

```bash
# Windows (PowerShell)
$env:BFACW_HOST="localhost"
$env:BFACW_PORT="9876"
$env:PYTHONPATH="C:\Users\<you>\AppData\Roaming\bfa_coworker\vendor_deps;C:\Users\<you>\AppData\Roaming\bfa_coworker\vendor"
"C:\Program Files\Blender Foundation\Blender\4.3\python\bin\python.exe" -m blmcp --transport stdio

# Linux/macOS
BFACW_HOST=localhost BFACW_PORT=9876 \
PYTHONPATH=~/.cache/bfa_coworker/vendor_deps:~/path/to/addon/vendor \
/path/to/blender/python/bin/python3 -m blmcp --transport stdio
```

### 3. Send a `tools/list` Request

Once the MCP server is running, it will print `MCP server initialized` and
wait for JSON-RPC requests on stdin. Type or pipe this:

```json
{"jsonrpc": "2.0", "id": "1", "method": "tools/list"}
```

Press Enter twice (the second newline terminates the JSON-RPC message).

You should see a response like:

```json
{"jsonrpc": "2.0", "id": "1", "result": {"tools": [{"name": "execute_blender_code", ...}, ...]}}
```

If you get an empty `"tools": []` or an error, see the troubleshooting section.

### 4. Test a Tool Call

Send a simple tool call to verify end-to-end connectivity:

```json
{"jsonrpc": "2.0", "id": "2", "method": "tools/call", "params": {"name": "get_objects_summary", "arguments": {}}}
```

Expected response: a JSON object with scene object information.

## Verifying the Bridge with Telnet/Python

### Using Python (recommended)

```python
import socket, json

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(5)
sock.connect(("localhost", 9876))

# Send a simple code execution request
request = json.dumps({
    "type": "execute",
    "code": "import bpy; print('Hello from Blender:', bpy.app.version_string)",
    "strict_json": False
}) + "\0"

sock.sendall(request.encode("utf-8"))

# Read response
buf = bytearray()
while True:
    chunk = sock.recv(65536)
    if not chunk:
        break
    buf.extend(chunk)
    if b"\0" in buf:
        break

response = json.loads(buf.partition(b"\0")[0].decode("utf-8"))
print("Response:", json.dumps(response, indent=2))
sock.close()
```

### Using Telnet

```bash
telnet localhost 9876
```

Then paste:
```
{"type": "execute", "code": "print('hello')", "strict_json": false}
```

Followed by a null byte (Ctrl+@ on Windows, Ctrl+Shift+U 0000 on Linux).

## Common Failure Modes

### "Connection refused" on port 9876

- The bridge server is not running in Blender
- Start it from the chat panel or preferences
- Check the port offset in Advanced settings

### "No tools returned" from `tools/list`

- The MCP server can't connect to the bridge
- Check `BFACW_HOST` and `BFACW_PORT` environment variables
- Verify the bridge is running (telnet test above)
- Check that `blmcp` is importable (see PYTHONPATH section)

### `ModuleNotFoundError: No module named 'blmcp'`

- PYTHONPATH is not set correctly
- The vendor directory must include `vendor/` (parent of `vendor/blmcp/`)
- Use the "Use Blender's Python" toggle in preferences — it sets PYTHONPATH automatically

### `ModuleNotFoundError: No module named 'mcp'`

- The vendor deps directory is missing or not on PYTHONPATH
- Run `python build_addon.py` to build the extension and populate vendor deps
- Or manually: `pip install --target ~/.cache/bfa_coworker/vendor_deps/ mcp[cli] pyyaml docutils`

## Adding a New Harness Preset

To add a new harness preset for contributors:

1. Open `addon/bfa_coworker/shared.py`
2. Find the `_HARNESS_PRESETS` list
3. Add a new `HarnessPreset(...)` entry with:
   - `identifier`: unique string key
   - `name`: display name
   - `description`: one-line summary
   - `icon`: Blender icon identifier
   - `is_open_source`: True/False
   - `config_path_help`: where the config file lives (one path per line)
   - `setup_steps`: numbered list of setup instructions
   - `docs_url`: link to the harness's documentation
   - `notes`: any caveats or gotchas
4. Open `addon/bfa_coworker/agent_controller.py`
5. Add a config generator branch in `generate_mcp_client_config()` for the new preset's config format
6. Test by selecting the preset in preferences and copying the config

## Verifying the Config Format

Each harness preset generates a JSON config. Verify it matches the expected
schema for that harness:

- **Claude Desktop / Claude Code**: `{ "mcpServers": { "bfa-coworker": {...} } }`
- **Cursor / Windsurf / Cline**: `{ "servers": { "bfa-coworker": { "type": "stdio", ... } } }`
- **Codex / OpenCode**: `{ "mcpServers": { "bfa-coworker": {...} } }`
- **Generic**: raw `{ "command": "...", "args": [...], "env": {...} }`