# Bforartists Coworker (Blender MCP Fork)

> **⚠️ WORK IN PROGRESS** — This is an active fork under heavy development.
> See [TODO](#todo) below for planned features and known gaps.


A self-contained Blender add-on that bundles an LLM agent, MCP server, and
in-Blender chat UI — no external tools, no manual server setup, no Python
environment wrangling. Install the add-on, pick a model, start chatting.

**⚠️ Windows tested only for now**

---

## Quick Start

### 1. Install the Add-on

- Open Blender → **Edit → Preferences → Add-ons**
- Click **Install from Disk...** and select the built `.zip` (or point to the
  `addon/bfa_coworker/` directory for development)
- Search for **"Coworker"** and enable the add-on

### 2. Install llama-server (one click)

If you don't have `llama-server` installed, the add-on preferences show a
**"Download llama-server"** button. Click it — the add-on downloads the
latest release from GitHub and unpacks it automatically to
`~/.cache/bfa_coworker_llama/`. No manual download, no PATH setup.

### 3. Pick a Model

In the add-on preferences you'll see the **LLM Configuration** section with
models organized into three categories:

| Category | VRAM | Best for |
|---|---|---|
| **Flagship** (24 GB+) | High-end GPUs (RTX 5090) | DeepSeek R1 32B, Qwen 2.5 Coder 32B, Gemma 4 26B Q8 |
| **Mid-Range** (12-20 GB) | RTX 4090 / 3090 sweet spot | Mistral Small 3.1 24B (new default), Gemma 4/Gemma 3, Phi-4, GPT-OSS |
| **Lightweight** (≤ 8 GB) | Any GPU / integrated | Llama 3.1 8B, Qwen3 8B, Phi-4 Q3 |

Click a preset name to select it. The **Mid-Range** section is the default
selection optimized for an **RTX 4090**.

- **Custom Model** — Use the dropdown below the presets to manually enter a
  repo ID and filename.
- **Scan for Existing Models** — Click the **Scan** button to search your
  configured models directory and HuggingFace cache for `.gguf` files you
  already have. Found models appear in a popup for one-click selection.
- **Advanced Settings** — Manually enter a HuggingFace repo ID and filename to download automatically.

### 4. Download & Start

Click **Download & Start**. The add-on downloads the GGUF model directly
from HuggingFace in 64 KB chunks with a real-time progress bar showing
percentage, speed, and ETA. Once the download completes, `llama-server`
starts automatically and loads the model. The server stays running in the
background.

> **Tip:** If the model requires authentication, set your **HuggingFace
> Token** in the Advanced section. The add-on also checks the `HF_TOKEN`
> and `HUGGINGFACE_TOKEN` environment variables.

### 5. Start the Agent

In the **Agent Control** section, click **Start Agent**. This launches the
MCP bridge server and connects it to the local LLM. The status indicators
should all show green.

### 5. Chat!

Open the **3D Viewport sidebar** (press `N` if hidden) and find the
**Coworker** tab. Type your message in the input box and press **Send**. The
agent will think, call Blender tools as needed, and respond.

That's it. No command line, no Docker, no separate Python installs.

---

## How It's Self-Contained

This fork wraps the original [Blender MCP](https://www.blender.org/lab/mcp-server/)
into a single add-on experience:
| What you'd normally need to set up manually | What this add-on does for you |
|---|---|
| Install & configure `llama.cpp` separately | **Auto-downloads** `llama-server` from GitHub with one click, or detects it on PATH |
| Download GGUF models manually | Direct HTTP download with real-time progress bar, or scan for models you already have |
| Run an MCP bridge server | Auto-started when you click **Start Agent** |
| Run a separate chat client | Chat UI lives in Blender's 3D Viewport sidebar |
| Wire up API keys (optional) | Remote API mode with provider presets, URL auto-fill, and browseable model IDs |

Alternatively, you can use a remote API (OpenRouter, OpenAI, Anthropic, etc.)
by selecting the **Remote API** mode in preferences. Choose OpenRouter from
the provider dropdown (auto-fills the API URL), paste your API key, enter a
model ID (e.g. `openai/gpt-4o`), and click **Browse Models** to find models
on openrouter.ai — no local LLM required.

Original upstream documentation: [blender.org/lab/mcp-server](https://www.blender.org/lab/mcp-server/)

---

## Architecture

The project is deliberately small, maintainable, and does no more than
necessary. It has two components that communicate over a TCP socket:

- A **Blender add-on** that runs inside Blender and executes requests.
- An **MCP server** that runs as a separate process.

The data flow is:

```
MCP Client  ⇐ MCP/stdio ⇒  bfa-coworker-mcp  ⇐ TCP socket ⇒  Blender Add-on
```

In this fork the "MCP Client" is the built-in agent controller, which
talks to the local LLM or remote API, then relays tool calls to the
MCP server over HTTP:

```
Chat UI → Agent Controller → [Local LLM / Remote API]
                ↓
        MCP Server (bfa-coworker-mcp)
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
- **jump_to_view3d_object_data_by_name** — Focus the 3D viewport on an object's data
- **render_thumbnail_to_path** — Render a small low-quality thumbnail
- **render_viewport_to_path** — Render the current scene to a path
- **search_api_docs** — Search the Blender Python API reference
- **search_manual_docs** — Search the Blender user manual

CLI variants (suffixed `_for_cli`) are also available for background Blender
mode.

---

## MCP Server

Located in ``mcp/blmcp/``, installed as a Python package with the
entry point ``bfa-coworker-mcp``. The server connects to the add-on's TCP
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
- **One-click llama-server download** — auto-downloads from GitHub releases
- **Direct GGUF download** — streams from HuggingFace in 64 KB chunks with real-time
  progress bar (percentage, speed, ETA)
- **Cancel downloads** — abort in-progress downloads; partial files are cleaned up
- **Disk space pre-check** — verifies sufficient space before multi-GB downloads
- **HF_TOKEN support** — access gated models via token field or environment variable
- **Fallback download** — if direct download fails, falls back to `llama-server --hf-repo`
- **Curated model presets** — 14 tested GGUF models with RAM/disk/capability info,
  organized into Flagship / Mid-Range / Lightweight categories
- **Existing model scanner** — finds `.gguf` files in HF cache and custom directories
- **Adjustable context window** — tune `--ctx-size` per model (4096–262144), auto-set from preset
- **Max output tokens** — per-preset defaults (16384 flagship, 8192 mid-range, 4096 lightweight),
  with auto-continue on truncation
- **Remote API support** — OpenRouter, OpenAI, Anthropic with provider presets and model browser
- **In-Blender chat UI** — 3D Viewport sidebar + Text Editor panel, no separate client needed
- **Port configuration** — individual port overrides plus global offset
- **Timer interval tuning** — configurable polling intervals for active/idle states
- **Tool logging toggle** — log every tool request/response to console
- **Diagnostics section** — port checks, ping, and four benchmark tests (Objects, Scene,
  Animation, Collections)
- **Reasoning content logging** — full chain-of-thought from reasoning models logged to console
- **Portable vendor deps** — pure-Python deps installed to `vendor/deps/` (no hardcoded paths)

---

## Requirements

- **Blender 5.1+** (or Bforartists equivalent)
- **llama-server** — from [llama.cpp](https://github.com/ggml-org/llama.cpp)
  releases. Must be on your PATH or set in preferences.
- **~4–28 GB RAM** depending on model (see preset tooltips)
- **~5–37 GB disk** for model storage

---

## Configuration

All settings are in **Edit → Preferences → Add-ons → Coworker**.

| Section | Setting | Description |
|---------|---------|-------------|
| LLM Config | Mode | Local (llama.cpp) or Remote API |
| | Provider | Remote API provider preset (auto-fills URL) |
| | API URL / Key / Model | Remote API connection settings |
| | Refresh Models | Fetch live model count from the API |
| | Browse Models | Open openrouter.ai/models in browser |
| | Recommended Model | Curated local model preset (Flagship/Mid/Light) |
| | Scan | Find existing `.gguf` files on your machine |
| | Models Directory | Where downloaded models are stored |
| | Advanced | Repo ID, filename, context window, max tokens, HF token |
| Agent Control | Auto-Start Agent | Launch agent when Blender starts |
| | Start/Stop | Manual agent control |
| | Ping | Check all connections (bridge, MCP, LLM) |
| Ports | Port Offset | Global offset added to all default ports |
| | Bridge / MCP / LLM | Individual port overrides (0 = default + offset) |
| Diagnostics | Check Ports | Verify ports are available |
| | Diagnose | Full connectivity test (ping all endpoints) |
| | Benchmarks | Run Objects/Scene/Animation/Collections benchmarks |
| Timer | Active Interval | Polling rate while processing (0.05–5.0s) |
| | Idle Interval | Polling rate while idle (0.1–10.0s) |
| | Idle Delay | Seconds before switching to idle interval |
| Misc | Log | Toggle tool request/response logging |

---

## Test Suites (QA / Benchmarking)

The add-on includes built-in multi-step test suites accessible from the
**Diagnostics** panel in preferences (visible when `BFACW_DEBUG=True`).
Each suite simulates a real artist workflow — steps must be clicked in
order, and each builds on the previous one.

### Available Suites

| Suite | Steps | What It Tests |
|---|---|---|
| **Scene Build** | 6 | Ground plane → scatter props → colored collections → materials (metallic/rough/glass) → lighting → camera + render |
| **Animation** | 5 | Bouncing ball → floor → keyframed bounce → squash & stretch → orbiting camera |
| **Modifiers** | 6 | Sculpt-ready head: UV sphere → Subdiv → Mirror → cut & re-mirror → shape jaw/chin → Multires |
| **Assets+Mat** | 5 | Poly Haven HDRI download, shaderball creation, texture download, glass material, three-point lighting + render |
| **Baseline** | 6 | Mini Stonehenge: stone ring → lintels → ground → stone material → dramatic lighting → camera + render |
| **Errors** | 3 | Vague prompt ("make it nicer"), impossible request, contradiction — tests graceful failure |

### How to Run

1. Enable **BFACW_DEBUG** in `shared.py` (or set the env var).
2. Open **Edit → Preferences → Add-ons → Coworker**.
3. Scroll to the **Diagnostics** section at the bottom.
4. Pick a suite and click **Step 1** — the agent processes it.
5. Once it completes, **Step 2** becomes clickable, and so on.
6. Use **Reset** to restart a suite from step 1.

### What to Look For

- **Correctness** — Does the result match what you asked for?
- **Latency** — How long does each step take from click to completion?
- **Chaining** — Does the agent remember context from previous steps?
- **Error handling** — Does it fail gracefully on bad prompts?
- **Model comparison** — Run the same suite on different models (e.g.
  Mistral 24B vs. Gemma 4 26B) and compare results.

### Logging

All test steps are logged to the Blender console with the prefix
`[🛠️Coworker] test suite '<name>':`. Check the console for timing,
status updates, and any errors.

---

## TODO

The following items are tracked in [CHANGELOG.md](CHANGELOG.md):

### High Priority
- [x] **Addon Branding Rename** — Rename blender_mcp_addon branding references to "bfa_coworker"
- [x] **Interface Modularization** — Split `__init__.py` into separate modules
- [x] **Download Progress Bar** — Visual progress bar in preferences panel
- [x] **Cancel Downloads** — Abort in-progress downloads with cleanup
- [x] **HF_TOKEN Support** — Access gated models via token field
- [x] **Portable Vendor Deps** — Replaced non-portable bundled .venv with pip --target
- [ ] **Get going on Linux and Mac** — Currently Windows only

### Medium Priority
- [ ] **Chat History Export** — Save conversation log to a text file
- [ ] Add better chat drawing in sidebar based on: https://projects.blender.org/blender/blender/pulls/154351
- [ ] **SKILL.md Update** — Rewrite agent skill file for current branding
- [ ] **User Documentation** — Full install/usage/troubleshooting guide
- [ ] **GGUF Header Parsing** — Auto-detect model params for non-presets

### Low Priority
- [ ] **System RAM Detection** — Filter presets that exceed available RAM
- [ ] **Local Model Generator** — Integrate Ultrashape / Hunyuan / Trellis2
- [ ] **CC0 Resource Downloader** — Polyhaven, AmbientCG, Sketchfab
- [ ] **Integrated pre-prompted tools for UX operators** to allow repetitive work with contextual application that an MCP can do in a smart way. this requires some design and integration into different editors and interface.

---

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).