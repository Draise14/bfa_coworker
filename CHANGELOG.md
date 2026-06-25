# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Model Presets** — Curated dropdown of 13 recommended GGUF models (Gemma 4,
  Qwen3.6, Qwen3-Coder, Llama 4 Scout, Qwen 2.5, Qwen3, DeepSeek-R1) with
  tooltips showing RAM requirements, disk size, and capability rating.
  Selecting a preset auto-fills the repo ID and filename.
- **Existing Model Scanner** — Scans both the configured models directory and
  HuggingFace cache for `.gguf` files. Found models appear in a popup menu for
  one-click selection. Access via "Scan" button in preferences.
- **"Last Used" Model Recall** — The last selected preset or existing model path
  is persisted across Blender sessions via built-in property storage.
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

### TODO (Future Plans)

#### High Priority
- [ ] **Addon Branding Rename** — Change all `blmcp` / `blender_mcp` references to
  `bfa_coworker` / `bfacw`. Update operator IDs, panel IDs, class names, and
  UI labels.
- [ ] **Module Rename** — Rename `blender_mcp_addon` directory to `mcp_addon`
  and update all internal imports.
- [ ] **Interface & Operator Modularization** — Split `__init__.py` into
  separate modules (`preferences.py`, `operators_server.py`, `operators_llm.py`,
  `operators_agent.py`, `operators_hf.py`) for easier maintenance.

#### Medium Priority
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
