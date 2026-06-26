# Bforartists Coworker (Blender MCP Fork)

> **⚠️ WORK IN PROGRESS** — This is an active fork under heavy development.
> See [TODO](#todo) below for planned features and known gaps.


A self-contained Blender add-on that bundles an LLM agent, MCP server, and
in-Blender chat UI — no external tools, no manual server setup, no Python
environment wrangling. Install the add-on, pick a model, start chatting.

**⚠️ Windows only for now**

---

## Quick Start

### 1. Install the Add-on

- Open Blender → **Edit → Preferences → Add-ons**
- Click **Install from Disk...** and select the built `.zip` (or point to the
  `addon/blender_mcp_addon/` directory for development)
- Search for **"MCP"** and enable the add-on

### 2. Pick a Model

In the add-on preferences you'll see the **LLM Configuration** section:

- **Recommended Models** — A curated dropdown of tested GGUF models
  (Gemma 4, Qwen3, Llama 4 Scout, GPT OSS. etc.) with tooltips showing RAM
  requirements, disk size, and capability rating. Select one and it
  auto-fills the repo ID and filename.
- **Scan for Existing Models** — Click the **Scan** button to search your
  configured models directory and HuggingFace cache for `.gguf` files you
  already have. Found models appear in a popup for one-click selection.
- **Advanced Settings** — Manually enter a HuggingFace repo ID and filename to download automatically.

### 3. Download & Start

Click **Download & Start**. The add-on launches `llama-server` which
auto-downloads the model from HuggingFace (progress visible in the
llama-server console window). Once the model is ready, the server stays
running in the background.

### 4. Start the Agent

In the **Agent Control** section, click **Start Agent**. This launches the
MCP bridge server and connects it to the local LLM. The status indicators
should all show green.

### 5. Chat!

Open the **3D Viewport sidebar** (press `N` if hidden) and find the
**MCP** tab. Type your message in the input box and press **Send**. The
agent will think, call Blender tools as needed, and respond.

That's it. No command line, no Docker, no separate Python installs.

---

## How It's Self-Contained

This fork wraps the original [Blender MCP](https://www.blender.org/lab/mcp-server/)
into a single add-on experience:

| What you'd normally need to set up manually | What this add-on does for you |
|---|---|
| Install & configure `llama.cpp` separately | Detects `llama-server` on PATH or lets you set the path — then launches it automatically |
| Download GGUF models manually | Built-in download via `llama-server`, or scan for models you already have |
| Run an MCP bridge server | Auto-started when you click **Start Agent** |
| Run a separate chat client | Chat UI lives in Blender's 3D Viewport sidebar |
| Wire up API keys (optional) | Remote API mode with URL + key fields in preferences |

Alternatively, you can use a remote API (OpenAI, OpenRouter, Anthropic) by
entering the URL and API key in preferences — no local LLM required.

Original upstream documentation: [blender.org/lab/mcp-server](https://www.blender.org/lab/mcp-server/)

---

## Architecture

The project is deliberately small, maintainable, and does no more than
necessary. It has two components that communicate over a TCP socket:

- A **Blender add-on** that runs inside Blender and executes requests.
- An **MCP server** that runs as a separate process.

The data flow is:

```
MCP Client  ⇐ MCP/stdio ⇒  blender-mcp  ⇐ TCP socket ⇒  Blender Add-on
```

In this fork the "MCP Client" is the built-in agent controller, which
talks to the local LLM or remote API, then relays tool calls to the
MCP server over HTTP:

```
Chat UI → Agent Controller → [Local LLM / Remote API]
                ↓
        MCP Server (blender-mcp)
                ↓
        Bridge Server (inside Blender)
                ↓
        Blender executes the code
```

---

## MCP Tools

The MCP server provides the following tools that the LLM can call:

- **execute_blender_code** — Execute Python code in the connected Blender instance
- **get_blendfile_summary_datablocks** — Summary of the blend file: data-block counts,
  active workspace, and render engine
- **get_blendfile_summary_missing_files** — Report missing external file references
  (images, libraries, fonts, sounds, movie clips, caches, sequences)
- **get_blendfile_summary_of_linked_libraries** — Tree of directly and indirectly
  linked library files
- **get_blendfile_summary_path_info** — Blend file's path, save status, age, backups
- **get_blendfile_summary_usage_guess** — Guess primary use-cases (scored 0–100)
- **get_object_detail_summary** — Structured summary of an object by name
- **get_objects_summary** — Scene collection hierarchy and their objects
- **get_python_api_docs** — Blender Python API docs for a given identifier
- **get_screenshot_of_area_as_image** — Screenshot of a single Blender area (PNG)
- **get_screenshot_of_window_as_image** — Screenshot of the entire Blender window (PNG)
- **get_screenshot_of_window_as_json** — JSON description of window layout and selection
- **jump_to_tab_by_name** — Switch the active workspace tab
- **jump_to_tab_by_space_type** — Switch to a workspace by space type
- **jump_to_view3d_object_by_name** — Focus the 3D viewport on an object
- **render_thumbnail_to_path** — Render a small low-quality thumbnail
- **render_viewport_to_path** — Render the current scene to a path

CLI variants (suffixed `_for_cli`) are also available for background Blender
mode.

---

## MCP Server

Located in ``mcp/blmcp/``, installed as a Python package with the
entry point ``blender-mcp``. The server connects to the add-on's TCP
socket to relay requests to Blender.

``mcp/blmcp/data/``
   - ``prompts.yml`` — instructions sent to the LLM at connection time
   - ``api/`` — Blender Python API reference in RST format
   - ``manual/`` — Blender user manual excerpts in RST format

``mcp/blmcp/tools/`` — Each tool is a single module, auto-discovered at startup.
Modules ending in ``_toolcode`` contain code that runs inside Blender and are
skipped during discovery.

``mcp/blmcp/tools_helpers/`` — Shared utilities used by tools. Tools do not
import from each other; shared logic lives here.

---

## Features

- **No external dependencies** — `llama-server` is the only requirement
- **Built-in model download** — auto-downloads from HuggingFace
- **Curated model presets** — 13 tested GGUF models with RAM/disk/capability info
- **Existing model scanner** — finds `.gguf` files on your machine
- **Adjustable context window** — tune `--ctx-size` per model (2048–262144) (WIP)
- **Remote API support** — also works with OpenAI, OpenRouter, Anthropic
- **In-Blender chat UI** — 3D Viewport sidebar, no separate client needed

---

## Requirements

- **Blender 5.1+** (or Bforartists equivalent)
- **llama-server** — from [llama.cpp](https://github.com/ggml-org/llama.cpp)
  releases. Must be on your PATH or set in preferences.
- **~4–28 GB RAM** depending on model (see preset tooltips)
- **~5–37 GB disk** for model storage

---

## Configuration

All settings are in **Edit → Preferences → Add-ons → MCP**.

| Section | Setting | Description |
|---------|---------|-------------|
| LLM Config | Mode | Local (llama.cpp) or Remote API |
| | Recommended Model | Curated preset dropdown |
| | Scan | Find existing `.gguf` files |
| | Models Directory | Where downloaded models live |
| | Advanced | Repo ID, filename, context window size |
| Agent Control | Auto-Start | Launch agent when Blender starts |
| | Start/Stop | Manual agent control |
| | Ping | Check all connections (bridge, MCP, LLM) |

---

## TODO

The following items are tracked in [CHANGELOG.md](CHANGELOG.md):

### High Priority
- [ ] **Addon Branding Rename** — Rename blender branding references
- [ ] **Interface Modularization** — Split `__init__.py` into separate
      preference/operator modules
- [ ] **Get going on Linux and Mac** at the moment this is Windows only.

### Medium Priority
- [ ] **Chat History Export** — Save conversation log to a text file
- [ ] **SKILL.md Update** — Rewrite agent skill file for current branding
- [ ] **User Documentation** — Full install/usage/troubleshooting guide
- [ ] **GGUF Header Parsing** — Auto-detect model params for non-presets

### Low Priority
- [ ] **System RAM Detection** — Filter presets that exceed available RAM
- [ ] **Download Progress Bar** — Visual progress in preferences panel
- [ ] **Local Model Generator** — Integrate Ultrashape / Hunyuan / Trellis2
- [ ] **CC0 Resource Downloader** — Polyhaven, AmbientCG, Sketchfab
- [ ] **Integrated pre-prompted tools for UX operators** to allow repetitive work with contextual application that an MCP can do in a smart way. this requires some design and integration into different editors and interface. 

---

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).