# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

### TODO (Future Plans)

#### High Priority
- [x] **Bug:** Make local model detection and "link" more robust
- [x] **Context Window Setting & Tool-Message Safety Fix** — Added
  `local_ctx_size` IntProperty (2048-262144, default 8192) exposed in
  Advanced Settings so users can tune the context window per model to
  avoid Jinja errors and OOM. The preferences value is now passed to
  `llama-server --ctx-size`. Also fixed history slicing to drop orphaned
  `tool`-role messages that lost their `assistant`/`tool_calls` pair
  during conversation trimming — preventing the fatal `Message has tool
  role, but there was no previous assistant message with a tool call!`
  Jinja exception.
- [ ] **Addon Branding Rename** — Change all `blmcp` / `blender_mcp` references to
  `bfa_coworker` / `bfacw`. Update operator IDs, panel IDs, class names, and
  UI labels.
- [ ] **Module Rename** — Rename `blender_mcp_addon` directory to `mcp_addon`
  and update all internal imports.
- [ ] **Interface & Operator Modularization** — Split `__init__.py` into
  separate modules (`preferences.py`, `operators_server.py`, `operators_llm.py`,
  `operators_agent.py`, `operators_hf.py`) for easier maintenance

#### Medium Priority
- [ ] **Add history chat to a text file with a button to open it in a floating window** - so we can copy and paste the results and save the log from the chat
- [ ] **SKILL.md Update** — Rewrite `.github/skills/self-contained-blender-mcp/SKILL.md`
  to reflect current project goals and branding.
- [ ] **DOCUMENTATION.md** — Create user-facing documentation covering
  installation, quick start, model management, remote API setup, and
  troubleshooting.
- [ ] **GGUF Header Parsing** — Read GGUF file headers to detect parameter
  count and quantization for non-preset models, enabling auto-populated
  RAM/disk estimates.

#### Low Priority
- [ ] **System RAM Detection** — Use platform-specific API to detect available
  RAM and filter/hide presets that exceed system capacity.
- [ ] **Download Progress Bar** — Replace text-based download progress with a
  visual progress bar in the preferences panel.
- [ ] **Add Model Generator** locally, Ultrashape, Hunyuan, similar to here: https://github.com/ahujasid/blender-mcp
- [ ] **Add CC0 resource downloader** from Polyhaven, AmbientC00, Sketchfab, etc, similar to here: https://github.com/ahujasid/blender-mcp

## [Unreleased - v1.1.36]

### Fixed

- **llama-server download URL** — Updated from ancient tag `b5027` (404) to
  `b10154`. Fixed asset naming to match current release convention
  (`win-cpu-x64.zip`, `macos-arm64.tar.gz`, `ubuntu-cpu-x64.tar.gz`).
  Added `tarfile` support for `.tar.gz` extraction on macOS/Linux.
- **Model download poll signal** — The "Download & Start" button was
  disappearing immediately because the poll timer checked `state.is_running`
  (set on process launch) instead of waiting for the actual download to
  complete. Added `download_active` flag to `LLMState` so the UI correctly
  distinguishes "server is running" from "a download is in progress".
- **UI refresh after download** — Both llama-server and model download
  operators now redraw all `PREFERENCES` areas on completion instead of
  only `context.area`, so the green checkmark / status text appears
  immediately without requiring a Blender restart.
- **Download button visibility during download** — The "Download & Start"
  button and progress bar now stay visible while `download_active` is true,
  even though the server process has already started.

- **MCP server Python path resolution** — The bundled `uv`-managed `.venv` was
  not portable across machines because `pyvenv.cfg` hardcoded a machine-specific
  Python path (e.g. `C:\Users\USER\AppData\Roaming\uv\python\cpython-3.12.9-...`).
  Replaced with a portable approach:
  - `build_addon.py` now installs pure-Python dependencies into `vendor/deps/`
    via `pip install --target` and copies `blmcp` source into `vendor/blmcp/`.
  - `agent_controller.py` uses Blender's own Python (`sys.prefix/bin/python.exe`)
    to launch the MCP server, with `vendor/deps/` and `vendor/` on `PYTHONPATH`.
  - Added `_ensure_vendor_deps()` auto-install fallback for source installs.
  - Added `_find_python_with_pip()` to `build_addon.py` so the build script
    finds a Python with pip even when `sys.executable` is a uv-managed venv
    that lacks pip.
  - Removed the old `vendor/python_env/` layout entirely.

## [v1.1.35]

### Changed

- **Interface Modularization** — Split `__init__.py` (~1,437 lines) into
  separate focused modules:
  - `shared.py` — Constants, port helpers, lazy import wrappers, model/remote
    preset items
  - `preferences.py` — `_State` runtime state class + `_BlenderMCPPreferences`
    (properties + `draw()` method)
  - `operators_server.py` — Bridge server start/stop operators + autostart
    timer + CLI handler
  - `operators_llm.py` — LLM operators (download model, start/stop, download
    llama-server, scan, select preset/existing model)
  - `operators_agent.py` — Remote API operators (test connection, refresh
    models, browse models, ping agent)
  - `operators_hf.py` — HuggingFace cache operators (open, clear)
  - `__init__.py` — Thin registration hub (~100 lines) importing all classes
    and wiring `register()`/`unregister()`.
- **Duplicated code removed** — `ui_chat.py` now imports `effective_ports()`
  from `.shared` instead of duplicating the port constants and helper function.

### Added

- **llama-server Auto-Download** — One-click download and extraction of
  `llama-server` from GitHub releases (ggml-org/llama.cpp) into
  `~/.cache/blender_mcp_llama/`. No manual `llama.cpp` install needed.
- **Model Presets** — Curated dropdown of 13 recommended GGUF models (Gemma 4,
  Qwen3.6, Qwen3-Coder, Llama 4 Scout, Qwen 2.5, Qwen3, DeepSeek-R1) with
  tooltips showing RAM requirements, disk size, and capability rating.
  Selecting a preset auto-fills the repo ID and filename.
- **Existing Model Scanner** — Scans both the configured models directory and
  HuggingFace cache for `.gguf` files. Found models appear in a popup menu for
  one-click selection. Access via "Scan" button in preferences.
- **"Last Used" Model Recall** — The last selected preset or existing model path
  is persisted across Blender sessions via built-in property storage.
- **Categorized Model Presets** — Presets reorganized into visual sections
  (Flagship / Mid-Range / Lightweight) with per-category clickable buttons
  replacing the flat dropdown. New presets: Mistral Small 3.1 24B, DeepSeek R1
  Distill 32B, Qwen 2.5 Coder 32B, Gemma 3 27B, Phi-4 14B, Llama 3.1 8B.
  Default preset is now Mistral Small 3.1 24B (RTX 4090 sweet spot).
- **Persistent Repo Memory** — Architecture, conventions, decisions, and
  deferred tasks tracked in `/memories/repo/` for the AI coding agent.
- **CHANGELOG.md** — This file, tracking project changes.

### Changed

- LLM Configuration UI redesigned: "Recommended Models" dropdown first, then
  "Existing Models" scanner with Scan button, with manual repo/filename fields
  under "Advanced Settings".
- `_BLMCP_OT_start_llm` now respects `existing_model_path` — if set, passes the
  path directly to `start_local_llama()`.
- `_autostart_agent_timer` also respects `existing_model_path` for auto-start.

