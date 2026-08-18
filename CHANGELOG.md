# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

### TODO (Future Plans)

#### High Priority
- [x] **Bug:** Make local model detection and "link" more robust
- [x] **Download Progress Bar** — Replace text-based download progress with a visual progress bar in the preferences panel.
- [x] **Cancel Downloads** — Add a cancel button for in-progress downloads.

#### Medium Priority
- [ ] **Add history chat to a text file with a button to open it in a floating window** - so we can copy and paste the results and save the log from the chat
- [x] **SKILL.md Update** — Rewrite `.github/skills/self-contained-blender-mcp/SKILL.md` to reflect current project goals and branding.
- [ ] **DOCUMENTATION.md** — Create user-facing documentation covering installation, quick start, model management, remote API setup, and troubleshooting.
- [ ] **GGUF Header Parsing** — Read GGUF file headers to detect parameter count and quantization for non-preset models, enabling auto-populated RAM/disk estimates.

#### Low Priority
- [ ] **System RAM Detection** — Use platform-specific API to detect available RAM and filter/hide presets that exceed system capacity.
- [ ] **Add Model Generator** locally, Ultrashape, Hunyuan, similar to here: https://github.com/ahujasid/blender-mcp
- [x] **Add CC0 resource downloader** from Polyhaven, AmbientC00, Sketchfab, etc — Polyhaven tools implemented in v1.1.37.

## [Unreleased - v1.1.37]

### Added

- **Curated 9-Model Preset List** — Replaced 14-model list with 9 curated presets (3 flagship / 3 mid / 3 light) tuned for Blender agentic work. New entries: Qwen3.8-27B, Fable Fusion 27B, Nail 35B A3B, Gemma 4 E4B, Qwen3.5-9B DeepSeek-V4-Flash. Each preset carries native context window, vision flag, mmproj filename, hardware recommendation, and "why pick this" rationale. Removed Gemma 3 12B Vision (#23) and Phi-4 14B Q3 (#32). GPT-OSS 20B is the new default (best Blender benchmarked model).
- **GPU Backend Selector** — New `llama_backend` dropdown in preferences (Auto / CUDA 12.4 / Vulkan / CPU). Auto-detect checks nvidia-smi for NVIDIA, wmic for AMD/Intel, and falls back to CPU. `download_llama_server()` selects the correct asset per backend and extracts cudart DLLs for CUDA. `find_llama_server()` prefers the active backend's bundled binary. `start_local_llama()` passes `--n-gpu-layers` (99 for GPU, 0 for CPU).
- **Unified Download Progress Bars** — Added `download_kind` field to LLMState. Replaced fragile string-matching progress logic with a single progress block driven by `download_kind` + `download_progress_pct` + ETA. Cancel button now works for llama-server downloads too.
- **Hybrid Tool Domain System** — Cascaded tool loading for local models: surface tools (code execution + scene inspection) always loaded; domain tools (animation, material, modeling, lighting, rendering, VSE, geometry nodes) pre-detected from user prompt keywords or loaded on-demand via `load_tools` meta-tool. Keeps context window small (~8 tools vs 30) while giving the LLM access to all tools when needed. Remote mode unaffected.
- **Smart Undo Skip for Code-Bug Errors** — `_error_is_code_bug()` detects pure code-bug errors (KeyError, AttributeError, TypeError, NameError, ValueError, "Node type undefined") that fail before creating any objects. Skips the undo+push round-trips, saving 2 HTTP calls per retry and avoiding depsgraph crashes from undo on empty scenes.
- **Blender 5.3 Material Node Hint** — Added to `skills/blender_53.md`: when `mat.use_nodes = True`, Blender auto-creates Principled BSDF + Material Output already connected. LLM should find them by iterating `node.type` instead of `nodes.new('BSDF_PRINCIPLED')` which doesn't work in Bforartists 5.3.
- **Reasoning Content Stripped from LLM Requests** — `_strip_reasoning_from_history()` removes non-standard `"reasoning"`-role messages before sending to the LLM, saving 500-3,000 tokens per request. Reasoning is still stored in full history for UI display.
- **Polyhaven Tools** — New tools for downloading CC0 resources from Polyhaven (models, HDRIs, textures) directly from the agent. Supports URL-based setup and test build.
- **Generative Plugin Foundation** — Tier 5 foundation: image gen plugins with auto-discovery, controller, and plugin base classes. Supports audio, image, text, and video plugin types, usability still WIP (not usable).
- **Version-Aware Skills System** — New searchable domain skills system with version-aware Blender API skills (`blender_50_51.md`, `blender_52.md`, `blender_53.md`). User custom skills support.
- **Copy Content Button** — New button in chat UI that copies the full conversation history to clipboard.
- **Stepped Benchmark Tests** — Replaced single-prompt benchmarks with 8 multi-step test suites (Scene Build, Animation, Modifiers, Assets+Mat, Baseline, Error Handling, Vision: Camera, Vision: Place). Steps are clicked in order, each building on the last. Progress tracked per-suite with Reset support.
- **Vision Test Suites** — Two new multi-step suites for vision-capable models: `vision_camera` (build a stage, then place/reframe/verify a camera by looking at `get_screenshot_of_area_as_image` viewport screenshots) and `vision_relative` (place a cup on a table, stack a marble on it, center a cone on a pedestal, and butt a cube flush — each verified visually via screenshots with iterative adjustment).
- **Tool Testing** — Added `tests/tool_smoke_test.py` for automated tool smoke testing.

### Changed

- **New Addon Interface** — Redesigned preferences panel with 4 tabs (General, LLM, Remote, Diagnostics). Debugging panel moved out of tabs into its own section.
- **Unified Operating Mode Selector** — Combined local/remote mode into a single dropdown selector. Improved reasoning content display with better verbosity.
- **Skills Improvements** — Multiple skill file enhancements: animation curve understanding, material creation smarts, operator mode switching, Blender 5.3 API nuances, sequencer versioning API.
- **Polyhaven URL Setup** — Right URL configuration for Polyhaven integration.

### Fixed

- **Depsgraph Crashes in Blender 5.3** — Replaced crash-prone `view_layer.update()` calls with `_safe_depsgraph_sync()` that uses `update_tag()` on each object (lightweight, no full rebuild). Added `_code_touches_collections()` and `_code_is_undo_or_push()` heuristics to skip full depsgraph sync after collection-manipulation and undo/push operations. Removed before-exec depsgraph sync entirely — tagging objects before smart undo caused stale-object crashes in `pyrna_struct_CreatePyObject`.
- **Defensive Entity Snapshots** — `_SNAPSHOT_EXTRA` now wraps each datablock iteration in try/except via `_sn()` helper, so a single corrupted datablock doesn't crash the entire snapshot.
- **Remote API Mode** — Fixed remote API mode not working correctly. Unified Operating Mode selector resolves mode conflicts.
- **Python Context Internal State Bug** — Fixed internal state bug in Python context handling.
- **No Text Content in Tool Result** — Fixed error when screenshot tool returns no text content.
- **Debugging Panel Layout** — Moved debugging panel out of tabs per user feedback.
- **Re-Entrancy Guard for Conversation Turns** — Added `turn_active` flag to `AgentState` preventing overlapping `run_conversation_turn()` calls from corrupting shared history. Chat send and test step buttons now check `turn_active` before spawning threads, eliminating the primary "two balls" duplicate-object root cause.
- **Undo Code Silent Fallback Fix** — `_undo_code()` now returns an explicit `{"status": "error"}` when no window/area is available for undo/push, instead of silently returning `{"status": "ok"}` and leaving the scene dirty.
- **`ValueError` Removed from Code-Bug Skip** — `_error_is_code_bug()` no longer treats `ValueError` as a pure code-bug, since it can fire after objects have been created. Prevents the smart-undo system from skipping undo when side effects exist.
- **`TypeError` Removed from Code-Bug Skip** — `_error_is_code_bug()` no longer treats `TypeError` as a pure code-bug, since enum validation errors (e.g. `enum "4" not found in ('NONE', 'COLOR_01', ...)`) can fire after objects are created. Prevents duplicate objects from skipped undo on type errors.
- **Retroactive Entity Cleanup Fallback** — Added `_build_cleanup_code()` that generates Blender Python code to delete orphaned datablocks when `bpy.ops.ed.undo()` fails (no window/area, empty undo stack). The smart-undo system now checks the undo result and falls back to direct entity deletion using the snapshot diff.
- **Auto-Continue Tool Call Deduplication** — When `finish_reason=length` triggers auto-continue, tool calls from the continuation are now deduplicated by ID before merging, preventing duplicate tool execution.
- **Test Suite Busy Guard** — Added `_test_suite_running` tracking to prevent launching concurrent test steps for the same suite. Progress no longer advances until the step thread completes.
- **Thread-Safe History Save** — Added `_history_save_lock` to `_save_chat_history()` preventing concurrent threads from writing partial conversation dumps.
- **Test File Tail Cleanup** — Removed duplicated `TestForegroundServer`/`TestInteractiveServer` class definitions and malformed `exit(1)    unittest.main()` line from `tests/test_blender_mcp_with_blender.py`.
- **Collection Heuristic Hardening** — Added `layer_col.exclude` and `layer_col.hide_viewport` to `_code_touches_collections()` patterns, catching collection mutations from `jump_to_view3d_*` tool templates that were previously missed. Prevents Blender 5.3 depsgraph crash after full `view_layer.update()` following collection edits.
- **Read-Only Snapshot Skip** — Added `_code_is_readonly()` heuristic that detects read-only code (no `bpy.ops`, `.new()`, `.remove()`, etc.) and skips the costly 12-datablock entity snapshot on those calls. Saves significant overhead when the LLM makes many inspection calls between mutation calls.
- **Scene-Aware Domain Pre-Detection** — Added `_detect_domain_from_scene()` that scans `bpy.data` for armatures, actions, materials, node groups, lights, cameras, meshes, sequencer strips, and geometry nodes modifiers. Pre-loads matching domains before the turn starts, so the LLM has the right tools even when the user's prompt is vague about what exists in the scene.
- **Domain-Aware Skill Auto-Injection** — Added `_DOMAIN_SKILL_MAP` and `get_domain_skills()` to bundle domain skill files (`animation.md`, `materials.md`, `mesh_editing.md`, `modifiers.md`, `rendering.md`) into the system prompt when matching domains are detected. The LLM gets version-aware API rules upfront without needing to search for them.
- **Result Trimming Middleware** — Added `_trim_tool_result()` that strips JSON boilerplate (`{"status": "ok", "result": ...}`) and keeps only the meaningful inner data when truncating tool results for LLM context. Error messages are preserved in full within the budget; success results have their inner `result` fields extracted, giving the LLM more structured data at the same 500-char limit.
- **Composite Tool Wrappers** — Three new MCP tools that combine multi-step operations into a single call:
  - `setup_pbr_material` — creates a PBR material with Principled BSDF + normal map + displacement, optionally downloading Polyhaven textures. Saves 3-5 round-trips.
  - `batch_keyframe_insert` — keyframes multiple objects across multiple frames with location/rotation/scale in one call. Saves N round-trips per object per frame.
  - `three_point_lighting_rig` — creates key, fill, and rim lights tracking a target object. Saves 3-5 round-trips.
  All three registered in their respective domains (animation, material, lighting, rendering).
- **User Skill Loader** — Added `get_user_skills()` that scans `SCRIPTS/bfa_coworker_skills/` for `.md` files and injects them into the system prompt alongside built-in skills. Users can drop custom skill files into this directory to teach the LLM project-specific conventions, API overrides, or workflow patterns without modifying the addon.

## [v1.1.36] - 2026-08-12

### Added

- **Cancel Downloads** — New `cancel_download()` function and `_BFACW_OT_cancel_download` operator. Thread-safe cancellation via `threading.Event`. Partial files are cleared on abort. Cancel button appears in UI while a download is active.
- **Disk Space Detection** — Pre-flight disk space check before multi-GB downloads via `_check_disk_space()` using `shutil.disk_usage()`. Requires file size + 5% margin. Sets actionable error if insufficient space.
- **Download Progress Bar** — Real-time visual progress bar (`row.progress(factor=..., type='BAR')`) in the preferences panel showing percentage, speed, ETA, and file size. Replaces the old text-only progress display.
- **Always-Visible Download Button** — The "Download & Start" button never disappears. States: "Download & Start" → "Downloading…" (disabled) → "Already Downloaded" (disabled). Progress/error always shown below.
- **Clear HTTP Error Messages** — 401 → suggests HF_TOKEN, 403 → suggests granting access at huggingface.co, 404 → suggests checking repo/file name.
- **HF_TOKEN Support** — New `hf_token` field (password-masked) in Advanced preferences. Passed to both direct download and llama-server subprocess. Also auto-detects `HF_TOKEN` / `HUGGINGFACE_TOKEN` environment variables.
- **Fallback Download** — If direct download fails for non-auth reasons (network restrictions, proxy), falls back to `llama-server --hf-repo/--hf-file` with 15-minute timeout and subprocess crash detection.
- **Direct GGUF Download** — New `_download_gguf_direct()` function streams model files from HuggingFace in 64 KB chunks with real-time progress (percentage, speed, ETA, progress bar). No more reliance on `llama-server` console for download progress.
- **llama-server Binary Download** — One-click download and extraction of `llama-server` from GitHub releases (tag `b10154`). Supports Windows (zip), macOS/Linux (tar.gz). Extracts to `~/.cache/bfa_coworker_llama/`.
- **File Size Pre-Fetch** — `_get_hf_file_size()` performs HEAD request to HuggingFace to get file size before download, enabling accurate progress estimates.
- **ETA Formatting** — `_format_eta()` shows human-readable ETA ("3m 24s remaining") during downloads.
- **Model Presets (14 curated)** — Categorized visual sections: Flagship (24 GB+ VRAM), Mid-Range (12-20 GB), Lightweight (≤ 8 GB). Includes Gemma 4 26B, DeepSeek R1 Distill 32B, Qwen 2.5 Coder 32B, Mistral Small 3.1 24B, Gemma 3 27B, Qwen3.6 35B A3B, GPT-OSS 20B, Phi-4 14B, Qwen3.5 9B Heretic, Gemma 3 12B Vision, Qwen3 8B, Phi-4 14B Q3. Each preset has pre-configured `context_window` and `max_tokens` values.
- **Remote Provider Presets** — OpenRouter provider with 10 curated models (Claude 4.6 Sonnet, GPT-4.1, GPT-4o, DeepSeek Chat V3, Gemini 2.5 Flash/Pro, Llama 4 Maverick, Qwen3.6 35B, Mistral Small 3.1, GPT-5 Mini). Live model listing via `/v1/models` endpoint.
- **Existing Model Scanner** — Scans both the configured models directory and HuggingFace cache for `.gguf` files. Found models appear in a dropdown for one-click selection.
- **"Last Used" Model Recall** — The last selected preset or existing model path is persisted across Blender sessions via built-in property storage.
- **Port Configuration** — Individual port overrides (bridge, MCP, LLM) plus a global `port_offset`. Effective ports shown as read-only labels via `_draw_effective_ports()`.
- **Max Output Tokens** — New `local_max_tokens` field in config and preferences. Per-preset default values (16384 flagship, 8192 mid-range, 4096 lightweight). Auto-continue on `finish_reason=length` with concatenation (max 2 attempts).
- **Context Window Auto-Set** — Selecting a preset auto-sets `--ctx-size` to `min(preset.context_window, 65536)`. Default raised from 8192 to 32768. Existing users auto-upgraded on first server start.
- **Reasoning Content Logging** — Full chain-of-thought from reasoning models (Qwen, DeepSeek, Gemma 4) logged to console for debugging. No truncation.
- **Benchmark Tests** — Four benchmark buttons in Diagnostics section: Objects (random objects in colored groups), Scene (ground + columns + lighting), Animation (torus with keyframes), Collections (SET/LIT/ANIM with color tags). Each runs through the full agent pipeline.
- **System Prompt Path Fix** — `_get_system_prompt()` now searches both dev layout (`mcp/blmcp/data/prompts.yml`) and deployed layout (`vendor/blmcp/data/prompts.yml`). The full 3000+ char Blender system prompt is now loaded in deployed addons.
- **Orphaned Tool Message Cleanup** — `_drop_orphaned_tool_messages()` removes tool-role messages without preceding assistant `tool_calls`, preventing Jinja template errors from llama-server on sliced conversation history.
- **SSE Parser** — `_parse_sse_json()` / `_parse_sse_text_response()` for FastMCP stateless_http mode (used by `streamable-http` transport).
- **Thread-Safe Stop Mechanism** — `request_stop()` / `clear_stop()` for clean interruption of the conversation loop.
- **Pipe Drainer** — Background threads (`_start_pipe_drainer()`) to drain stdout/stderr pipes from subprocesses, preventing deadlock.
- **Port Killer** — `_kill_process_on_port()` uses netstat+taskkill (Windows) or fuser (Linux) to clean up orphaned processes.
- **Port Availability Check** — `check_ports_available()` tests bridge/MCP/LLM ports by attempting to bind with `SO_EXCLUSIVEADDRUSE`.
- **Logging Infrastructure** — File-based logging via `print()` tee (`log.install_print_tee()`). Blender 5.3+ policy violation warning coalescer (`log.install_policy_warning_filter()`).
- **Timer Interval Configuration** — `timer_interval_active` (0.05–5.0s), `timer_interval_idle` (0.1–10.0s), `timer_interval_idle_delay` (1.0–60.0s) for fine-tuning polling behavior.
- **Tool Logging Toggle** — `use_log` preference to log every tool request/response to the console.

### Changed

- **Download Model** — Rewritten from polling `llama-server` health endpoint to direct HTTP chunked download with real progress data. llama-server is started after download completes.
- **No new dependencies** — All download logic uses only `urllib.request` (stdlib).
- **Model Preset Refinement** — All 14 presets now carry `context_window` and `max_tokens` metadata. Preset selection auto-configures both the server's `--ctx-size` and the API's `max_tokens`.
- **Server Launch Refinement** — `start_local_llama()` resolves model from local file → HF cache → `--hf-repo/--hf-file`. Auto-upgrades `ctx_size` from 8192 to 32768. Redirects HF cache to models dir. Passes `HF_TOKEN` for gated models. Uses `CREATE_NEW_CONSOLE` on Windows for proper PID tracking.
- **Server Stop Refinement** — `stop_local_llama()` now has graceful terminate + fallback `taskkill`/`pkill` for orphaned processes.
- **Port Handling Refinement** — Migrated from hardcoded ports to configurable offsets + per-port overrides with effective-port display.
- **MCP Server Python Path Resolution** — Replaced the bundled `uv`-managed `.venv` (non-portable, hardcoded machine-specific paths) with a portable approach:
  - `build_addon.py` installs pure-Python dependencies into `vendor/deps/` via `pip install --target` and copies `blmcp` source into `vendor/blmcp/`.
  - `agent_controller.py` uses Blender's own Python (`sys.prefix/bin/python.exe`) with `vendor/deps/` and `vendor/` on `PYTHONPATH`.
  - `_ensure_vendor_deps()` auto-install fallback for source installs.
  - `_find_python_with_pip()` in `build_addon.py` finds a Python with pip even when `sys.executable` is a uv-managed venv.
  - Removed the old `vendor/python_env/` layout entirely.
- **`max_tokens` Default** — Raised from 4096 to 16384 (per-preset: 16384 flagship, 8192 mid-range, 4096 lightweight). Replaces hardcoded value with configurable parameter.
- **`--ctx-size` Default** — Raised from 8192 to 32768. Auto-set from preset's `context_window` (capped at 65536 for consumer GPU safety).
- **Interface Modularization** — Split `__init__.py` (~1,437 lines) into separate focused modules: `shared.py`, `preferences.py`, `operators_server.py`, `operators_llm.py`, `operators_agent.py`, `operators_hf.py`.
- **Duplicated code removed** — `ui_chat.py` now imports `effective_ports()` from `.shared` instead of duplicating port constants.
- **Diagnostics Section** — Expanded with Check Ports, Diagnose (ping), and four Benchmark buttons — all gated behind `BFACW_DEBUG` flag.

### Fixed

- **401/403/404 errors** from HuggingFace are now surfaced immediately with actionable messages, instead of silently failing inside the llama-server subprocess.
- **Download button disappearing** — No longer disappears after clicking. Always remains visible with correct state (Downloading… / Already Downloaded / Download & Start).
- **`finish_reason=length` truncation** — Reasoning models (Qwen, DeepSeek, Gemma 4) that hit the token limit mid-reasoning now auto-continue: partial output is appended, "Continue." is sent, results are concatenated (max 2 attempts).
- **System prompt not loading in deployed addon** — Added `vendor/blmcp/data/prompts.yml` path so the full 3000+ char prompt is found in installed builds.
- **Orphaned tool messages in sliced history** — `_drop_orphaned_tool_messages()` prevents Jinja template errors when history truncation breaks tool-call pairs.
- **Subprocess pipe deadlock** — Pipe drainer threads prevent Blender from hanging when subprocess output fills the pipe buffer.
- **Orphaned server processes** — Port killer + fallback taskkill/pkill clean up stale processes on port conflicts.
- **Traceback verbosity** — Error tracebacks from failed Blender code execution are truncated to last 3 frames + exception message, preserving context window space for the LLM.
- **UI not refreshing after download** — Both llama-server and model download operators now redraw all `PREFERENCES` areas on completion instead of only `context.area`.

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

