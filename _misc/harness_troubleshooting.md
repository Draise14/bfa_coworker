# Harness Troubleshooting Guide — User-Facing

This guide helps you diagnose and fix common issues when connecting an
external MCP client (harness) to Blender's bridge server.

## Quick Checklist

Before diving into specific issues, verify these basics:

- [ ] Blender is running with bfa_coworker enabled
- [ ] Operating Mode is set to **External Harness**
- [ ] The bridge server is running (status shows "Bridge on port 9876")
- [ ] You've copied the config for your harness and pasted it in the right file
- [ ] You've **fully restarted** your MCP client (not just closed/reopened a window)

## "MCP server exited immediately" with a `runpy` traceback

If your client reports something like:

```
MCP server exited immediately: Traceback (most recent call last):
  File "<frozen runpy>", line 189, in _run_module_as_main
  File "<frozen runpy>", line 148, in _get_module_details
  File "<frozen importlib._bootstrap>", line 112, in _get_module
```

…then `blmcp` **was found**, but importing it failed. This is an important
distinction: when a module is genuinely missing, Python prints a one-line
message (`No module named blmcp`) with **no traceback at all**. A traceback
with these `runpy` frames means Python descended into `blmcp/__main__.py` and
the failure happened *inside* the package.

The real cause is the **last** line of the traceback, which older versions of
the add-on truncated away. The most common causes, in order:

1. **The MCP SDK is version 2.x.** `mcp` 2.0 removed `FastMCP`, but `blmcp`
   imports `from mcp.server.fastmcp import FastMCP`. The last line reads:
   ```
   ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x,
   where FastMCP was renamed to MCPServer ...
   ```
   **Fix**: reinstall the pinned vendor deps:
   ```bash
   python build_addon.py
   ```
   or, for a system-Python setup:
   ```bash
   pip install "mcp[cli]>=1.2.0,<2.0.0"
   ```

2. **`vendor/blmcp/` is missing.** This happens when the add-on was installed
   from source without running the build script. Run `python build_addon.py`,
   then re-copy the config from preferences.

3. **`PYTHONPATH` is missing the `vendor/` directory.** The config must point
   at the *parent* of `vendor/blmcp/`, not at `vendor/blmcp/` itself. Enable
   **"Use Blender's Python"** in preferences → Advanced → MCP Server and
   re-copy the config — it emits the correct `PYTHONPATH` automatically.

4. **`pydantic_core` or another native extension does not match your Python.**
   The vendored deps are compiled for a specific Python version. The last line
   reads `No module named 'pydantic_core._pydantic_core'`. Run
   `python build_addon.py` to rebuild them for Blender's Python.

5. **`pywintypes` is missing (Windows).** pywin32's importable modules live in
   `win32/` and `win32/lib/`, which are normally exposed via a `.pth` file —
   and `PYTHONPATH` ignores `.pth` files. The add-on adds those directories
   automatically; re-copy the config from preferences.

6. **The config is stale.** A config copied from an older Blender install
   points at a Python path that no longer exists. Re-copy the config.

### Verify before you paste

Use the **Test Config** button next to *Copy to Clipboard* (preferences →
Advanced → MCP Server → Step 2). It checks the interpreter path, every
`PYTHONPATH` entry, the resolved MCP SDK version, and actually launches the
server with `--help` to prove the import chain works — reporting the exact
failure and a fix instead of a bare traceback. The same check runs
automatically when you click *Copy to Clipboard*, and appears as **Config** in
the *Check Status* output.

## "Bridge is running but my client says no tools"

### 1. Check the Python command

The most common issue: your MCP client is using system `python` which doesn't
have `blmcp` installed.

**Fix**: In preferences → Advanced → MCP Server, make sure **"Use Blender's
Python"** is ON (default). This emits the full path to Blender's bundled Python
with PYTHONPATH set automatically.

If you prefer to use system Python, install the MCP server package:
```bash
pip install bfa-coworker-mcp
```

### 2. Verify the config was pasted correctly

Open your MCP client's config file and check:
- The JSON is valid (no trailing commas, matching braces)
- The `command` path points to a real Python executable
- The `args` include `-m blmcp --transport stdio`
- The `env` block has `BFACW_HOST` and `BFACW_PORT`

### 3. Test the command manually

Open a terminal and run the exact command from your config:
```bash
"<path-to-python>" -m blmcp --transport stdio
```
If this fails with `ModuleNotFoundError`, the PYTHONPATH is wrong. Enable
"Use Blender's Python" in preferences.

## "Connection refused" on port 9876

### Bridge not started

The bridge server must be running inside Blender before your MCP client
can connect.

**Fix**: In the chat panel, click **Start Bridge**. The status should change
to "External Harness — Bridge on port 9876".

### Wrong port

If you changed the port offset or individual port overrides, the bridge
might be on a different port.

**Fix**: Check the "Effective Ports" display in Advanced preferences.
The bridge port is shown there. Update your MCP client config to match.

### Firewall

Some firewalls block localhost TCP connections. Try temporarily disabling
your firewall to test. If that fixes it, add an exception for Blender's
Python on the bridge port.

## "Command not found: python"

The MCP client can't find the `python` command. This happens when:
- Python is not on the system PATH
- The config uses a relative path

**Fix**: Enable **"Use Blender's Python"** in preferences. This emits the
full absolute path to Blender's bundled Python, which always works.

## "Blender executable not found at 'blender'"

The `execute_blender_code_for_cli` (and related CLI) tools spawn a background
Blender to run code, using the `BLENDER_PATH` environment variable. If it is
not set (or your Blender is installed under another name, e.g.
`bforartists.exe`), the tool falls back to a literal `blender` and fails.

**Fix**: regenerate the MCP config from the addon (it now sets `BLENDER_PATH`
to your running Bforartists binary automatically), or set `BLENDER_PATH`
manually in the client config env block, e.g.:
```
"env": { "BLENDER_PATH": "C:/3D_Stuff/Devbuild/bforartists.exe" }
```
full absolute path to Blender's bundled Python, which always works.

## Per-Harness Common Issues

### Claude Desktop

- **Config not loading**: Claude Desktop must be **fully restarted** (File →
  Exit, then re-open). Closing the window is not enough on Windows.
- **Config file location**: `%APPDATA%\Claude\claude_desktop_config.json`
  (Windows), `~/Library/Application Support/Claude/claude_desktop_config.json`
  (macOS), `~/.config/Claude/claude_desktop_config.json` (Linux)
- **Hammer icon not appearing**: The config JSON is invalid. Check for
  trailing commas or missing braces.

### Claude Code

- **Uses same config as Claude Desktop**: Claude Code reads MCP servers from
  the same `claude_desktop_config.json` file.
- **Restart required**: Run `claude` fresh after config changes.

### Codex CLI

- **PYTHONPATH is critical**: Codex uses system Python by default. Enable
  "Use Blender's Python" in preferences.
- **Config file**: `~/.codex/config.json`
- **Verify**: Run `codex` and ask "list available MCP tools"

### Cursor

- **Config format**: Cursor uses `"servers"` key (not `"mcpServers"`).
  The preset generates the correct format automatically.
- **Config file**: `~/.cursor/mcp.json` or `.cursor/mcp.json` in project root
- **Restart**: Close and reopen Cursor after config changes

### Windsurf

- **Config file**: `~/.codeium/windsurf/mcp_config.json`
- **Auto-created**: The config file is created on first launch if it doesn't exist
- **Restart**: Fully restart Windsurf after config changes

### Cline

- **Config file**: `~/.vscode/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
- **Permission model**: Cline requires approval for each tool call by default.
  You'll see a prompt in VS Code when Blender tools are invoked.
- **Restart**: Reload VS Code window after config changes

### OpenCode

- **Config file**: `~/.config/opencode/mcp.json`
- **Auto-discovers**: OpenCode scans for MCP servers on startup
- **Verify**: Run `opencode` and check the logs for MCP server discovery

## Still Stuck?

1. Run the **Check Status** button in preferences → Advanced → Agent Control
2. In harness mode, only "Bridge" needs to show OK — MCP and LLM will show
   "N/A (harness mode)" which is expected
3. Try the **Generic STDIO** preset — it works with any MCP-compatible client
4. Open an issue at https://github.com/Draise14/bfa_coworker/issues with:
   - Your MCP client name and version
   - The config you're using (redact API keys)
   - The exact error message
   - Your OS and Blender version