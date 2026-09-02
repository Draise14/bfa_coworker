# BFA Coworker — Tier 4b: Competitor UX Analysis & Implementation Plan

**Date**: 2026-08-26
**Status**: Planning — Not Started
**Depends on**: Tier 3e (Chat UI Refinement), existing chat panel (`ui_chat.py`), agent controller, preferences

---

## Table of Contents

1. [Competitor Deep-Dive Analysis](#1-competitor-deep-dive-analysis)
2. [Feature Comparison Matrix](#2-feature-comparison-matrix)
3. [UX Pattern Catalog](#3-ux-pattern-catalog)
4. [Gap Analysis: BFA Coworker vs. Competition](#4-gap-analysis-bfa-coworker-vs-competition)
5. [Implementation Plan](#5-implementation-plan)
6. [Summary of Changes](#6-summary-of-changes)

---

## 1. Competitor Deep-Dive Analysis

### 1.1 Chat Companion (Polygoningenieur)

**Market position**: The veteran. 3+ years old, 44,500+ downloads, 89 ratings at 5/5. The most downloaded AI Blender addon.

**Pricing**: Free tier + $19.90 Full

**Blender support**: 3.3 – 5.0

**UX Architecture**:
- **Chat panel**: N-panel sidebar with input + history
- **Multi-provider**: OpenAI, Google Gemini, Anthropic Claude, LM Studio, Ollama
- **Code execution**: "Run" button on every code block; if it errors, a "Fix" button appears that sends the traceback back to the LLM
- **Code completion**: Inside the Text Editor, can ask for code completion
- **Right-click explain**: On any UI element, right-click → "What's this?" sends context to the LLM
- **Text-to-speech**: Reads answers aloud
- **Attachments**: File attachments supported
- **Output formatting**: Paragraphs, code segments, lists rendered cleanly
- **Copy destinations**: Copy code to new text file, current text file, or clipboard

**Strengths**:
- Deep Blender integration (right-click on UI elements, Text Editor code completion)
- Error→fix loop is a tight feedback cycle
- TTS is unique among competitors
- Mature, battle-tested (3+ years)

**Weaknesses**:
- No tool-calling / agentic capabilities (pure chat + code exec)
- No session history management
- No vision/screenshot support
- Blender 5.0 max (may not work on 5.1+)
- No local-only mode without LM Studio/Ollama

---

### 1.2 Together AI: All-In-One AI Toolbox

**Market position**: Could not fetch details (HTTP 429). Appears to be a newer entrant bundling multiple AI features.

**Note**: Insufficient data for deep analysis. Listed on Superhive as an "All-In-One AI Toolbox."

---

### 1.3 Suzanne AI (trisual)

**Market position**: Early experiment. 3+ years old, only 50+ sales, 1 rating at 1/5. Effectively abandoned.

**Pricing**: $1

**Blender support**: 3.2 – 3.5

**UX Architecture**:
- N-panel chatbot
- OpenAI-only (ChatGPT)
- Conversation stored in a user-selected `.txt` file
- "Try Again" button sends error message back to the LLM
- Very basic — no tool calling, no vision, no code execution safety

**Strengths**:
- Simple, approachable concept
- Conversation persistence via txt file is transparent

**Weaknesses**:
- Effectively abandoned (last updated for Blender 3.5)
- No tool calling, no vision, no local models
- Single-provider (OpenAI)
- No session management
- No code execution safety

---

### 1.4 BlendAI (Ruben Messerschmidt)

**Market position**: The polished commercial offering. 700+ sales, 14,500+ downloads, 14 ratings at 4/5. Most feature-rich non-MCP addon.

**Pricing**: Free tier + $19.95 Pro + $89.99 Enterprise + credit packs ($4.99–$9.99)

**Blender support**: 5.1

**UX Architecture**:

**Chat System**:
- **Dual interface**: Sidebar panel AND popup window (Ctrl+Shift+A)
- **Popup behavior**: Follows cursor during generation (redraws every frame); Esc cancels
- **Input features**: Auto-send on Enter+Shift, auto-reply on Alt
- **File attachments**: txt, xlsx, xls, csv, py, png, jpg, jpeg (max 2 images, 1000 char total input)
- **Quality selector**: Inline in chat input (High/Balanced)
- **Context awareness**: Knows which editor space you're in; knows your modified hotkeys

**Conversation Management**:
- **Chat history**: Search, load, edit name, delete
- **Per-message actions**: Edit (re-populates input), Remove (deletes message+response), Reply (threaded follow-up)
- **Response info**: Generation time, quality settings used
- **New chat**: Creates new empty chat, saves active to history

**Script System**:
- **Generate Script**: Create bpy scripts from natural language
- **Edit Script**: Modify existing scripts
- **Fix Script**: Send error + code back for correction
- **Test Script**: Run and validate
- **Save Script Preset**: Save as reusable preset with name, icon, space filter
- **Preset management**: Search, filter by space, execute, show in context menu, edit name/icon, delete

**Explain Feature**:
- Right-click any UI property/operator/node → "Explain"
- Shift+Explain → answers in popup instead of sidebar
- Can reply to explanations for follow-up

**Generative Features** (out of scope for this analysis but noted):
- Texture Generation, Reference Images, Upscale Image, Inpaint Image
- Render Suggestions

**Preferences**:
- Profile picture and name
- Language (auto-detect or manual)
- Custom instructions (personality — e.g., "Talk to me in Yoda style")
- Quality settings (High/Balanced/Custom per-feature)
- Bring-your-own API keys (OpenAI, Replicate)
- Popup width
- Merge panel with other addons from same author
- Keymap customization
- Auto-update with check-on-startup
- Early access opt-in

**Credit System**:
- Credits consumed per message (1 credit balanced, 5 credits high quality)
- Credit packs: 4000 for $4.99, 10000 for $9.99
- Recharge codes

**Strengths**:
- Most polished chat UX of all competitors
- Dual interface (sidebar + popup) is clever
- Script preset system is unique and powerful
- Right-click Explain is deeply integrated
- Context awareness (space, hotkeys) is thoughtful
- Credit system is transparent
- Custom instructions add personality

**Weaknesses**:
- No tool-calling / agentic capabilities (pure chat + code gen)
- No local model support (OpenAI-only for chat)
- Credit system adds friction
- No vision/screenshot input for chat
- No session persistence across Blender restarts (chat history is in-memory)
- 1000 char input limit is restrictive

---

### 1.5 Blender Buddy (CGMatter) — Free & Local

**Market position**: The local-first free alternative. 4 months old, 6,400+ downloads, 6 ratings at 4/5. Most technically ambitious local-only addon.

**Pricing**: Free

**Blender support**: 5.1

**UX Architecture**:

**Local-First Design**:
- Fully self-contained: downloads and manages llama.cpp + model weights
- Three text model tiers: Low (~9.7 GB), Medium (~14.7 GB, default), High (~21.7 GB)
- Vision model: Qwen3-VL-8B (~5.8 GB) with mmproj
- GPU backend auto-detection: CUDA, Metal, Vulkan, ROCm, CPU
- Pinned llama.cpp release tag for reproducibility
- SHA-256 verification of all downloads
- Resumeable downloads with progress bar
- Legacy model cleanup on upgrade

**Chat Panel UX**:
- **Sidebar in ALL editor types**: 10 space types (VIEW_3D, IMAGE_EDITOR, NODE_EDITOR, SEQUENCE_EDITOR, CLIP_EDITOR, TEXT_EDITOR, GRAPH_EDITOR, DOPESHEET_EDITOR, NLA_EDITOR, SPREADSHEET)
- **Global hotkey**: Ctrl+Shift+Q toggles Buddy sidebar in the editor under cursor
- **Toggle grid**: 2×2 grid of toggles — Web, Action, Deep, Vision
- **Prompt row**: Single-line text input with Enter-to-send
- **Newest-pair-first**: Most recent Q&A at top, history scrolls down
- **Animated icon**: Buddy icon animates during thinking/loading
- **Token counter**: "~X / Yk" estimate in bottom-right

**Message Rendering**:
- **Markdown rendering**: Bold, italic, code, lists, links parsed and displayed
- **Code blocks**: Syntax-highlighted with Run button
- **Safety scanner**: Pre-execution scan for dangerous calls (os.system, subprocess, etc.) and unknown bpy identifiers
- **Trust session**: "Don't ask again this session" checkbox
- **Undo support**: Each Run pushes an undo step
- **Collapse long responses**: 15+ line responses get a collapse toggle
- **Per-message copy**: Icon button on each assistant turn
- **URL extraction**: Clickable link buttons for URLs in responses
- **Revert**: Remove last Q&A pair
- **Clear (Esc)**: Full conversation reset

**Vision/Screenshot**:
- Three capture modes: Select Area (click editor), Full Window, Custom Image
- Area picker: cursor becomes eyedropper, click the editor to capture
- Status bar instructions during pick mode
- Custom image: file path with drag-drop, shows dimensions + size

**Tool System**:
- `search_api`: Lexical search over Blender API index (JSONL)
- `search_web`: DuckDuckGo web search (when online access enabled)
- `fetch_url`: Fetch and read web pages
- `get_scene`: Scene snapshot for context
- `list_info_log`: Recent Blender info-log entries
- Tool-call loop with configurable iterations (15 default, 30 deep mode)
- Fallback regex parsing for tool calls when server doesn't support native tool calling

**Server Management**:
- "Load Buddy" button (first launch loads model ~20-30s)
- "Unload" button in header
- Download progress with cancel
- One-click Preferences setup flow
- Server log access (copy last 400 lines)

**System Prompt**:
- Editable via external text file
- "Reset to Default" button
- Action Mode addendum appended dynamically

**Strengths**:
- Best local-only experience — zero configuration for non-technical users
- Sidebar in every editor type is unique
- Hotkey toggle is well-implemented
- Code safety scanner is best-in-class
- Markdown rendering is polished
- Vision/screenshot with area picker is clever
- Token counter helps users manage context
- Free and fully offline

**Weaknesses**:
- No tool-calling beyond search/fetch (no Blender manipulation tools)
- No session history — conversation is in-memory only, lost on unload
- No multi-turn persistence across Blender sessions
- Single conversation (no session management)
- No agentic capabilities (can't modify the scene)
- Large download (~15 GB for default model)
- Vision model is separate download (~5.8 GB)
- No remote API provider support (local only)

---

### 1.6 BlenderMCP Pro (Anvil Interactive Solutions / Quadify)

**Market position**: The agentic powerhouse. Newest entrant (2 months), 20+ sales, $50. Most technically sophisticated.

**Pricing**: $50 (includes 12 months support + updates)

**Blender support**: 4.2 – 5.2

**UX Architecture**:

**Dual-Mode Architecture**:
- **Built-in chat**: N-panel chat that can call all 75 tools
- **MCP server**: JSON-RPC 2.0 on localhost:7842 for external clients (Claude Desktop, Cursor, Windsurf, Claude.ai Web)
- One-click client config writing
- Cloudflare tunnel for Claude.ai web access
- Quadify Bridge for cross-DCC (UnrealMCP Pro, UnityMCP Pro)

**Agent Teams 2.0**:
- **Planner agent**: Splits goal into dependency-ordered task list
- **Specialist agents**: Layout, Modeling, Materials, Lighting, Rigging, Geometry Nodes, Rendering
- **Validator agent**: Compares final scene against goal, reports concrete problems
- **Auto-fix**: One repair pass before mission reports done
- **Parallel execution**: Independent tasks run concurrently
- **Live task list**: Watch progress, cancel anytime
- **Single undo checkpoint**: One Ctrl+Z rolls back entire mission

**Background Tasks**:
- Queue long jobs, keep working
- Live status, per-job cancel
- Results with cost readout
- Optional multi-agent planner routing

**Chat Panel**:
- Natural language input
- Tool calls shown inline with results
- Viewport screenshot attachment (auto-downscaled to 1280px JPEG)
- Voice input (local Whisper, no API key needed)
- Token optimizer (auto-prunes tool schema, compresses context, summarizes old history)

**Session Management**:
- Auto-titled sessions from first message
- History panel: paginated list with Load, Rename, Favorite, Delete
- New Session button
- Sessions saved locally as individual files
- Auto-reopen last session on Blender start

**Provider System**:
- 5 providers: Claude, Gemini Flash (1M tokens/day free), Groq (free tier), GPT-4o-mini, Ollama (local)
- Auto-fallback: silent switch on rate limit
- Economy/Quality model routing
- Live cost readout per mission/task
- Prompt caching built-in

**Project Memory**:
- Facts and conventions remembered across sessions
- Saved directly on .blend file
- "Remember that this project targets mobile VR — every mesh under 5,000 tris"

**Macros**:
- Save any sequence of AI actions as reusable named tool
- Target object exposed as parameter
- "Run my LOD pipeline on this new prop"

**Scene Co-Pilot**:
- Passive background scan for common issues
- One-click fixes where safe
- Flags: unapplied scale, missing UVs, non-manifold geo

**Render Critic**:
- Structured critique with quality score /10
- 5 focus modes: Full, Lighting, Composition, Materials, Technical
- "Fix with AI" button
- Iterative refinement: render → critique → fix → re-render until target score

**Tool System** (75 tools, 14 categories):
- Scene, Mesh, Material, UV, Export, Render, Pipeline, Light, Object, Python, Asset, Armature, Shader Nodes, Geometry Nodes
- Meshy AI integration (text-to-3D, image-to-3D)
- Auto-discovered addon tools (up to 50 operators from other addons)
- Python sandbox fallback (no file I/O, subprocess, or network)

**Client Connectivity**:
- Recent Tool Calls list with timestamp, tool name, success/failure, execution time
- Source tags: native tools, addon-discovered ("Ext"), Quadify Bridge ("QB")

**Strengths**:
- Most comprehensive tool set (75 tools)
- Agent Teams is the most advanced agentic architecture
- Session management is best-in-class
- Project Memory + Macros create compounding value
- Render Critic with iterative refinement is unique
- Cross-DCC bridge is visionary
- Cost controls are transparent and well-designed
- Provider auto-fallback is robust
- Background tasks enable non-blocking workflows

**Weaknesses**:
- Most expensive ($50)
- Complexity can be overwhelming
- Newer product (2 months), smaller user base
- No right-click explain on UI elements
- No code safety scanner for Python fallback
- No markdown rendering in chat (tool results are plain text)
- No text-to-speech
- No popup/quick-access chat (must open N-panel)

---

### 1.7 BuddyCode GPT (EasyLight)

**Market position**: The Text Editor specialist. 2+ years old, $10. Focused exclusively on code generation and completion inside Blender's Text Editor.

**Pricing**: Free tier (LM Studio only) + $10 Full (LM Studio + Ollama)

**Blender support**: 3.4 – 4.1

**UX Architecture**:

**Text Editor Integration**:
- **Built-in file browser**: Navigate and manage files within the Text Editor sidebar — no need to leave Blender
- **Sidebar tab**: "BuddyCode Browser" for file management, "BudyGPT" for AI interaction
- **Code completion**: Real-time, context-aware suggestions as you type
- **Code generation**: Generate code snippets from natural language prompts
- **Text completion**: Complete sentences or paragraphs based on context

**Multi-Pair Execution**:
- Define multiple pairs of (input text, system prompt, temperature)
- "Run All Pairs with Context" — processes pairs with document context
- "Run All Pairs No Context" — processes pairs without context
- Concurrent execution of multiple tasks

**AI Features**:
- **Multi-provider**: LM Studio, Ollama, Google Gemini
- **Vision support**: Toggle image vision processing, set image path for visual context
- **CSV analysis**: Load CSV files for data analysis
- **Document loading**: Load documents for context-aware generation using FAISS vector search
- **Chat history**: Keep track of conversations with the AI
- **Module installation**: Install Python modules directly from within Blender (pip install from the addon)

**Preferences**:
- Server type (LM Studio, Ollama, Gemini)
- Model type selection
- Endpoint URL configuration
- API keys for Gemini
- Enable vision toggle
- Image path and CSV path
- Temperature control

**Strengths**:
- Best Text Editor file browser of any AI addon — built-in file management
- Multi-pair execution is unique — batch process multiple prompts
- Document loading with FAISS vector search for context-aware generation
- Vision support for image-guided code generation
- CSV analysis capability
- Module installation from within Blender
- MIT license

**Weaknesses**:
- No tool-calling / agentic capabilities (pure chat + code gen)
- No N-panel chat panel — Text Editor only
- No session history management
- No screenshot/vision for the 3D viewport (only image files)
- Blender 4.1 max (may not work on 5.1+)
- No local model management (requires external LM Studio/Ollama)
- Requires langchain, FAISS, pyperclip — external Python dependencies
- 2+ years old, minimal updates

---

### 1.8 claude-in-blender (haw2fregel-lab)

**Market position**: The Claude Code bridge. Brand new (v1.0.0, 2 days old), 1 star, 1 contributor. Unique architecture: instead of bundling an LLM or calling an API, it forks your existing Claude Code session and drives Blender through a bundled MCP server.

**Pricing**: Free (GPL-3.0). No API keys needed — rides on your Claude Code subscription/API account.

**Blender support**: 4.2+ (tested on 5.1.2 / Windows + macOS)

**UX Architecture**:

**Panel Design**:
- **N-panel "Claude" tab**: Minimal panel with work directory dropdown, context toggles, text input, and Send button
- **Work directory**: Dropdown at the top selects which project's `CLAUDE.md` and skills apply. Can point at any repo, not just the addon's source. The `+` button picks a one-off directory via file browser. Registered directories persist (latest 5)
- **Context toggles**: Four checkboxes per request — "Target my selection", "Check the scene info first", "Look up the docs first", "Check a screenshot first". Toggles point at **live state** (what's selected when Claude checks, not when you press Send)
- **Model selection**: Dropdown to pick which Claude model handles the request (for new sessions)
- **Fork from recent sessions**: Lists last 5 sessions from the project's Claude Code transcripts. Clicking one forks it — the panel works from a copy, the original is never written to

**Session Architecture**:
- **Forked sessions**: The panel forks your desktop Claude Code session. Conversation context and `CLAUDE.md` carry over. The panel uses its own model selection and restricted tool/MCP config
- **File-switch notification**: If you open a different `.blend`, the panel warns before the next request. The tool response reports the switch as `file_switched` — operations are reported, never silently refused
- **Bridge registration**: `~/.claude/blender-bridge-session.json` stores work directory, addon source repo, recent directories, Claude executable, fork source/session ID, model, and registration time

**MCP Server** (bundled, `mcp_server/server.py`):
- `execute_code` — run arbitrary Python in Blender (no sandbox, main thread, no cancellation)
- `execute_file` — run a scratch `.py` file from temp directory
- `write_scratch` / `edit_scratch` — write/edit scratch files for execution
- `get_selection` — live selection state
- `get_scene_info` — scene objects, types, positions, settings
- `get_object_info` — detailed object info by name
- `get_doc` — Blender API documentation lookup
- `get_viewport_screenshot` — capture viewport screenshot
- `get_bridge_status` — check if Blender is connected
- `get_request_status` — check status of a previous request
- `search_session_history` — search past Claude Code session transcripts for this project
- `capture_after` — optional screenshot after execution for visual verification

**Security Model**:
- **No API keys**: Authentication and billing run through your logged-in Claude Code account
- **Send = approval**: Pressing Send pre-approves all bundled MCP tools for that request. No second confirmation
- **Strict MCP isolation**: `--strict-mcp-config` discards all other MCP servers. Only the bundled server is visible
- **Built-in tools limited to Skill**: No file reads, writes, shell, or web from Claude's built-in tools
- **`execute_code` is not a sandbox**: Runs Python inside Blender — full file and network access. Can be disabled with `CLAUDE_BRIDGE_EXECUTE=0`
- **No cancellation**: Long operations freeze Blender's UI. No hard cancellation, no rollback
- **Logging**: `claude_bridge_log` text datablock in Blender stores submitted code, 200-char result excerpts, error messages. Trims at 5,000 lines. Stored inside the `.blend` file

**Skills System** (`.claude/skills/`):
- **blender-modeling**: Modeling conventions for Blender 5.x — where names/enums moved, translated UI behavior, Geometry Nodes verification. CC0 licensed
- **blender-quick-edit**: One-shot edits you retune with F9
- **blender-param-panel**: Small generators with a live parameter panel
- **blender-modifier-inject**: Non-destructive changes as modifiers / Geometry Nodes
- **blender-setup**: One-command setup (Python deps, addon, bridge, connectivity check)
- **blender-update**: Update workflow
- **blender-bridge**: Point the panel at a different conversation

**Testing**: Extensive contract tests (`test_contract_panel_cwd.py`) verifying:
- MCP config structure and isolation
- Session history management (5-item limit, no duplicates, newest-first)
- Tool permission boundaries (allowed/denied skills, strict MCP config)
- Timeout handling (preserves session_id and body)
- Backward compatibility with old registration format

**Strengths**:
- Most elegant security model — no API keys, no credentials to configure
- Forked sessions preserve desktop conversation context
- Context toggles pointing at live state (not snapshots) is genuinely novel
- Strict MCP isolation is best-in-class security
- Skills system (CC0 licensed) is reusable outside the addon
- Work directory flexibility — use any project's CLAUDE.md and skills
- Comprehensive contract tests
- Free and open-source (GPL-3.0)

**Weaknesses**:
- Requires Claude Code (subscription or API) — not self-contained
- No built-in LLM — entirely dependent on external Claude Code
- No session history management within the panel (relies on Claude Code's transcripts)
- No markdown rendering in chat (tool results are plain text)
- No code safety scanner for `execute_code`
- No undo/rollback for executed code
- No popup/quick-access chat
- No right-click explain on UI elements
- No text-to-speech or voice input
- Very new (v1.0.0, 2 days old) — small user base, unproven
- Linux untested

---

## 2. Feature Comparison Matrix

| Feature | Chat Companion | Suzanne AI | BlendAI | Blender Buddy | BlenderMCP Pro | BuddyCode GPT | claude-in-blender | **BFA Coworker** |
|---|---|---|---|---|---|---|---|---|---|
| **Chat Interface** | | | | | | | | |
| N-panel sidebar | ✅ | ✅ | ✅ | ✅ (10 spaces) | ✅ | ❌ (Text Ed only) | ✅ | ✅ |
| Popup/quick chat | ❌ | ❌ | ✅ (Ctrl+Shift+A) | ✅ (hotkey toggle) | ❌ | ❌ | ❌ | ❌ |
| Multi-line input | ? | ? | ✅ | ❌ (single line) | ✅ | ✅ | ✅ | ✅ |
| File attachments | ✅ | ❌ | ✅ (8 types) | ✅ (screenshot) | ✅ (screenshot) | ❌ | ❌ | ❌ |
| Voice input | ❌ | ❌ | ❌ | ❌ | ✅ (Whisper) | ❌ | ❌ | ❌ |
| TTS (read aloud) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | | | | | | | | |
| **Message Display** | | | | | | | | |
| Markdown rendering | ✅ (basic) | ❌ | ❌ | ✅ (full) | ❌ (plain) | ❌ (plain) | ❌ (plain) | ❌ (plain) |
| Code blocks + Run | ✅ | ✅ (basic) | ✅ | ✅ (+ safety scan) | ✅ (sandbox) | ✅ (multi-pair) | ✅ (execute_code) | ❌ |
| Collapse long msgs | ❌ | ❌ | ❌ | ✅ (15+ lines) | ❌ | ❌ | ❌ | ❌ |
| Per-message copy | ✅ | ❌ | ✅ | ✅ | ? | ✅ | ❌ | ✅ |
| URL link buttons | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Error→fix loop | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | | | | | | | | |
| **Conversation Mgmt** | | | | | | | | |
| Session history | ❌ | ❌ | ✅ (search/load) | ❌ | ✅ (full CRUD) | ✅ (chat history) | ✅ (via Claude transcripts) | ✅ (basic) |
| Auto-title sessions | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ (Claude auto-titles) | ❌ |
| Multi-session | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ (fork from any) | ❌ |
| New/clear thread | ? | ❌ | ✅ | ✅ (Clear/Revert) | ✅ | ✅ | ✅ | ✅ |
| Token counter | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Persist across restart | ❌ | ✅ (txt file) | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ |
| | | | | | | | | |
| **Agentic/Tool System** | | | | | | | | |
| Tool calling | ❌ | ❌ | ❌ | ✅ (search/fetch) | ✅ (75 tools) | ❌ | ✅ (MCP tools) | ✅ (MCP tools) |
| Agent teams/planning | ❌ | ❌ | ❌ | ❌ | ✅ (Planner+Specialists) | ❌ | ❌ | ❌ |
| Background tasks | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ (queue) |
| Project memory/rules | ❌ | ❌ | ❌ | ❌ | ✅ (.blend) | ❌ | ✅ (CLAUDE.md) | ✅ (markdown) |
| Macros/reusable tools | ❌ | ❌ | ✅ (script presets) | ❌ | ✅ | ❌ | ❌ | ❌ |
| Scene co-pilot | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Render critic | ❌ | ❌ | ✅ (suggestions) | ❌ | ✅ (iterative) | ❌ | ❌ | ❌ |
| | | | | | | | | |
| **Blender Integration** | | | | | | | | |
| Right-click explain | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Context-aware (space) | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| @Mention system | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Text Editor integration | ✅ (completion) | ❌ | ❌ | ❌ | ❌ | ✅ (file browser + completion) | ❌ | ✅ (sidebar) |
| | | | | | | | | |
| **Provider/Model** | | | | | | | | |
| Local models | ✅ (LM Studio/Ollama) | ❌ | ❌ | ✅ (built-in) | ✅ (Ollama) | ✅ (LM Studio/Ollama) | ❌ (requires Claude Code) | ✅ (built-in) |
| Remote APIs | ✅ (3 providers) | ✅ (OpenAI) | ✅ (OpenAI) | ❌ | ✅ (4 providers) | ✅ (Gemini) | ✅ (Claude only) | ✅ |
| Auto-fallback | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Cost display | ❌ | ❌ | ✅ (credits) | ❌ | ✅ (live $) | ❌ | ❌ | ❌ |
| | | | | | | | | |
| **Setup Experience** | | | | | | | | |
| One-click install | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ✅ (/blender-setup) | ❌ |
| Auto-download models | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ |
| GPU auto-detect | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Download progress | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ |
| | | | | | | | | |
| **MCP/External** | | | | | | | | |
| MCP server | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ | ✅ |
| External client config | ❌ | ❌ | ❌ | ❌ | ✅ (one-click) | ❌ | ❌ | ❌ |
| Cross-DCC bridge | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |

### 2.1 BFA Coworker Proposal — After Tier 4b

This table shows what BFA Coworker will look like **after** implementing all Tier 4b phases. The `✅` column is the target state — what we ship.

| Feature | Chat Companion | Suzanne AI | BlendAI | Blender Buddy | BlenderMCP Pro | BuddyCode GPT | claude-in-blender | **BFA Coworker (Now)** | **BFA Coworker (After 4b)** |
|---|---|---|---|---|---|---|---|---|---|---|
| **Chat Interface** | | | | | | | | | | | | |
| N-panel sidebar | ✅ | ✅ | ✅ | ✅ (10 spaces) | ✅ | ❌ (Text Ed only) | ✅ | ✅ | ✅ |
| Popup/quick chat | ❌ | ❌ | ✅ | ✅ (hotkey) | ❌ | ❌ | ❌ | ❌ | ❌ → T5 |
| Multi-line input | ? | ? | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ |
| File attachments | ✅ | ❌ | ✅ (8 types) | ✅ (screenshot) | ✅ (screenshot) | ❌ | ❌ | ❌ | ❌ | ✅ (screenshot) |
| Voice input | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ → T6 |
| TTS (read aloud) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ → T6 |
| | | | | | | | | |
| **Message Display** | | | | | | | | | | | | |
| Markdown rendering | ✅ (basic) | ❌ | ❌ | ✅ (full) | ❌ (plain) | ❌ (plain) | ❌ (plain) | ❌ | ✅ P1 |
| Code blocks + Run | ✅ | ✅ (basic) | ✅ | ✅ (+ safety) | ✅ (sandbox) | ✅ (multi-pair) | ✅ (execute_code) | ❌ | ✅ P2 |
| Collapse long msgs | ❌ | ❌ | ❌ | ✅ (15+ lines) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ → post |
| Per-message copy | ✅ | ❌ | ✅ | ✅ | ? | ✅ | ❌ | ❌ | ✅ | ✅ |
| URL link buttons | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (via md) |
| Error→fix loop | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ P3 |
| | | | | | | | | |
| **Conversation Mgmt** | | | | | | | | | | | | |
| Session history | ❌ | ❌ | ✅ (search/load) | ❌ | ✅ (full CRUD) | ✅ (chat history) | ✅ (via Claude transcripts) | ✅ (basic) | ✅ P4 |
| Auto-title sessions | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ (Claude auto-titles) | ❌ | ✅ P4 |
| Multi-session | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ (fork from any) | ❌ | ✅ P4 |
| New/clear thread | ? | ❌ | ✅ | ✅ (Clear/Revert) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Token counter | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ → post |
| Persist across restart | ❌ | ✅ (txt file) | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | | | | | | | | |
| **Agentic/Tool System** | | | | | | | | | | | | |
| Tool calling | ❌ | ❌ | ❌ | ✅ (search) | ✅ (75 tools) | ❌ | ✅ (MCP tools) | ✅ (MCP) | ✅ |
| Agent teams/planning | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ → T5/6 |
| Background tasks | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ (queue) | ✅ |
| Project memory/rules | ❌ | ❌ | ❌ | ❌ | ✅ (.blend) | ❌ | ✅ (CLAUDE.md) | ✅ (md) | ✅ |
| Macros/reusable tools | ❌ | ❌ | ✅ (presets) | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ → T5 |
| Scene co-pilot | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ → T6 |
| Render critic | ❌ | ❌ | ✅ (suggestions) | ❌ | ✅ (iterative) | ❌ | ❌ | ❌ | ❌ | ❌ → T6 |
| | | | | | | | | |
| **Blender Integration** | | | | | | | | | | | | |
| Right-click explain | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ P5 |
| Context-aware (space) | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ → post |
| @Mention system | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Text Editor integration | ✅ (completion) | ❌ | ❌ | ❌ | ❌ | ✅ (file browser + completion) | ❌ | ❌ | ✅ (sidebar) | ✅ (Tier 4c) |
| | | | | | | | | |
| **Provider/Model** | | | | | | | | | | | | |
| Local models | ✅ (LM Studio) | ❌ | ❌ | ✅ (built-in) | ✅ (Ollama) | ✅ (LM Studio/Ollama) | ❌ (requires Claude Code) | ✅ (built-in) | ✅ |
| Remote APIs | ✅ (3) | ✅ (OpenAI) | ✅ (OpenAI) | ❌ | ✅ (4) | ✅ (Gemini) | ✅ (Claude only) | ✅ | ✅ |
| Auto-fallback | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ → T5 |
| Cost display | ❌ | ❌ | ✅ (credits) | ❌ | ✅ (live $) | ❌ | ❌ | ❌ | ❌ | ❌ → T5 |
| | | | | | | | | |
| **Setup Experience** | | | | | | | | | | | | |
| One-click install | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ✅ (/blender-setup) | ❌ | ❌ | ❌ → T5 |
| Auto-download models | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| GPU auto-detect | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ → T5 |
| Download progress | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| | | | | | | | | |
| **MCP/External** | | | | | | | | | | | | |
| MCP server | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ | ✅ | ✅ |
| External client config | ❌ | ❌ | ❌ | ❌ | ✅ (one-click) | ❌ | ❌ | ❌ | ❌ | ❌ → T5 |
| Cross-DCC bridge | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | Out of scope |

**Key**: `P1`–`P7` = Tier 4b Phase number. `T5`/`T6` = deferred to that Tier. `post` = post-Tier 4b polish. `→` = not in Tier 4b scope.

**Summary**: After Tier 4b, BFA Coworker closes **all 5 critical gaps** and **4 of 7 important gaps**. The only remaining gaps vs. BlenderMCP Pro are Agent Teams (T5/6), Macros (T5), Scene Co-Pilot (T6), and Render Critic (T6) — all advanced features that most users won't miss in a free tool. BuddyCode GPT's unique features (Text Editor file browser, multi-pair execution, doc vector search) are all deferred to Tier 4c/5/6 — none are chat-panel blockers.

---

## 3. UX Pattern Catalog

This section catalogs the specific UX patterns found across competitors, organized by what they achieve. Each pattern is evaluated for BFA Coworker applicability with a priority icon:

| Icon | Meaning |
|---|---|
| 🔴 | **Critical** — Must implement in Tier 4b. Core to the chat UX. Without this, the chat panel feels incomplete. |
| 🟡 | **High** — Should implement in Tier 4b. Major UX differentiator. Ships in this tier. |
| 🟢 | **Medium** — Nice to have. Ships in Tier 4b if time allows, otherwise Tier 5. |
| ⚪ | **Low / Deferred** — Noted for future. Not in Tier 4b scope. |

---

### 3.1 Input & Prompting Patterns

#### A. Toggle Grid (Blender Buddy) 🟡

A 2×2 grid of toggle buttons next to the input: Web, Action, Deep, Vision. Each toggle changes the behavior of the next message without needing to open settings.

**Source**: Blender Buddy

**Why high**: We already have Agent/Ask mode toggle. Adding Web, Deep, and Vision toggles in a compact grid would give users quick control over message behavior without opening preferences. However, it's an accelerator — the core chat works without it.

**BFA applicability**: Add toggles next to the input row: Web (online access), Deep (extended thinking), Vision (screenshot). Reuses existing preference flags.

**Implementation phase**: Phase 7 (polish)

---

#### B. Popup Chat (BlendAI) ⚪

A floating popup window (Ctrl+Shift+A) that follows the cursor during generation. Allows quick questions without opening the sidebar.

**Source**: BlendAI

**Why deferred**: Useful but adds significant complexity (modal operators, cursor tracking, redraw management). The N-panel is sufficient for Tier 4b. Revisit in Tier 5.

**BFA applicability**: Deferred to Tier 5.

---

#### C. Auto-Send Modifiers (BlendAI) ⚪

Hold Shift+Enter to auto-send, Alt to auto-reply to last message. Reduces clicks for power users.

**Source**: BlendAI

**Why deferred**: Our textbox already supports Enter-to-send. Modifier keys add discoverability issues for minimal gain. Low priority.

**BFA applicability**: Not planned.

---

#### D. Inline Quality Selector (BlendAI) ⚪

A dropdown in the chat input to switch between High/Balanced quality before sending.

**Source**: BlendAI

**Why deferred**: Could map to temperature/model selection, but adds UI clutter. Most users don't change quality per-message. Deferred to Tier 5.

**BFA applicability**: Deferred to Tier 5.

---

#### E. File Attachments (BlendAI, Chat Companion) 🟢

Attach text files, images, spreadsheets to provide context. BlendAI supports 8 file types with a file browser.

**Source**: BlendAI, Chat Companion

**Why medium**: Image attachments (screenshots) are covered by Phase 6. Text/code file attachments are lower priority — users can paste code directly. Nice to have but not critical.

**BFA applicability**: Covered by Phase 6 (screenshot). Text file attachments deferred.

---

### 3.2 Message Display Patterns

#### F. Markdown Rendering (Blender Buddy) 🔴

Parse and render markdown in LLM responses: bold, italic, code blocks, lists, links. Makes responses significantly more readable.

**Source**: Blender Buddy

**Why critical**: Our current plain-text rendering is the single biggest UX gap. Every competitor except BlenderMCP Pro has some form of rich text rendering. LLM responses are naturally markdown-formatted — rendering them as plain text loses structure, readability, and trust.

**BFA applicability**: Direct. Lightweight regex-based parser (no external lib). Support bold, italic, inline code, fenced code blocks, lists, links.

**Implementation phase**: Phase 1

---

#### G. Code Blocks with Run Button (Blender Buddy, Chat Companion) 🔴

Every Python code block gets a "Run" button. Blender Buddy adds a safety scanner that checks for dangerous calls and unknown bpy identifiers before execution.

**Source**: Blender Buddy, Chat Companion, BlendAI

**Why critical**: This is the bridge between "the agent told me what to do" and "the agent did it." Without Run buttons, users must copy-paste code to the Text Editor and run it manually. The safety scanner is essential — AI-generated code can contain hallucinated bpy identifiers or dangerous calls.

**BFA applicability**: Direct. Parse ```python fences, render with Run button, pre-scan for safety, execute in sandboxed namespace with undo support.

**Implementation phase**: Phase 2

---

#### H. Error→Fix Loop (Chat Companion, BlendAI, Suzanne AI) 🔴

When executed code throws an error, a "Fix" button appears that sends the error + code back to the LLM for correction.

**Source**: Chat Companion, BlendAI, Suzanne AI

**Why critical**: This closes the feedback loop. Code execution without error recovery is frustrating — the user has to manually copy the error, paste it into a new message, and ask for a fix. The Fix button automates this into one click.

**BFA applicability**: Direct. Capture traceback from Run execution, display with "Fix with Coworker" button, send as structured prompt.

**Implementation phase**: Phase 3

---

#### I. Collapse Long Responses (Blender Buddy) 🟢

Responses over 15 lines get a collapse/expand toggle. Shows first line + "Show full response (N lines)" or "Collapse" button.

**Source**: Blender Buddy

**Why medium**: Our turn-based layout already provides some collapsing via the turn panel system. Per-message collapse would be a nice refinement but isn't blocking. The turn panel already prevents long responses from dominating the viewport.

**BFA applicability**: Could enhance existing turn panel collapse with per-message collapse. Deferred to post-Tier 4b polish.

---

#### J. URL Link Buttons (Blender Buddy) ⚪

Extract URLs from responses and render them as clickable buttons that open in the OS browser.

**Source**: Blender Buddy

**Why deferred**: Nice-to-have. Covered partially by markdown link rendering (Phase 1). Explicit URL buttons are low priority.

**BFA applicability**: Covered by Phase 1 markdown link support.

---

#### K. Token Usage Counter (Blender Buddy) 🟢

A subtle "~X / Yk" display showing estimated token usage against context window. Helps users know when to clear conversation.

**Source**: Blender Buddy

**Why medium**: Useful for local models with limited context windows. Less critical for remote APIs with large contexts. Would be a helpful addition to the Status panel.

**BFA applicability**: Add to Status & Diagnostics panel. Simple chars/4 heuristic. Deferred to post-Tier 4b polish.

---

### 3.3 Conversation Management Patterns

#### L. Session History with CRUD (BlenderMCP Pro, BlendAI) 🔴

Full session management: auto-titled sessions, search, load, rename, favorite, delete. Sessions persist across Blender restarts.

**Source**: BlenderMCP Pro, BlendAI

**Why critical**: We currently have basic save/load but no session management UI. Users can't browse past conversations, switch between them, or delete old ones. This is a major UX gap — every conversation feels disposable.

**BFA applicability**: Direct. Add `sessions_index.json` metadata file, `BFACW_PT_session_history` panel with paginated list, Load/Rename/Delete operators.

**Implementation phase**: Phase 4

---

#### M. Auto-Title from First Message (BlenderMCP Pro) 🔴

Session title is automatically derived from the user's first message. Eliminates the need to manually name sessions.

**Source**: BlenderMCP Pro

**Why critical**: Manual session naming is friction nobody wants. Auto-titling from the first message (first 60 chars) makes session history browsable without any user effort. This is bundled with Phase 4 — you can't have session history without auto-titling.

**BFA applicability**: Direct. Extract first 60 chars of first user message as session title. Update on save.

**Implementation phase**: Phase 4 (bundled with session history)

---

#### N. Revert Last Pair (Blender Buddy) 🟢

A "Revert" button that removes the last Q&A pair from conversation. Allows quick undo of a bad interaction without full clear.

**Source**: Blender Buddy

**Why medium**: Our "New Thread" clears everything. A softer undo would be useful for recovering from a single bad turn. But it's quality-of-life, not critical.

**BFA applicability**: Add "Revert" button next to "New Thread". Pops last user+assistant pair from history.

**Implementation phase**: Phase 7 (polish)

---

#### O. Per-Message Actions (BlendAI) 🟡

Each message has: Edit (re-populates input), Remove (deletes message+response), Reply (threaded follow-up), Copy, Info (generation time/quality).

**Source**: BlendAI

**Why high**: We already have Copy. Edit and Remove are the two most impactful additions — Edit lets users fix typos without re-typing, Remove lets them delete bad turns. These are small operators with big UX impact.

**BFA applicability**: Add Edit and Remove operators. Edit re-populates the input field. Remove deletes the message pair with confirmation.

**Implementation phase**: Phase 7

---

### 3.4 Agentic & Tool Patterns

#### P. Agent Teams with Planner (BlenderMCP Pro) ⚪

A planner agent decomposes a goal into dependency-ordered tasks. Specialist agents execute tasks in parallel where possible. A validator checks the result.

**Source**: BlenderMCP Pro

**Why deferred**: The most advanced pattern in the competitive landscape. Requires significant architecture changes (multi-agent orchestration, dependency resolution, parallel execution). Tier 5/6 material.

**BFA applicability**: Deferred to Tier 5/6.

---

#### Q. Background Tasks with Queue (BlenderMCP Pro) 🟢

Long-running tasks are queued and run in background. User keeps working. Live status with cancel.

**Source**: BlenderMCP Pro

**Why medium**: We already have a message queue. Extending to background task queue is a natural evolution but not critical for Tier 4b — the message queue already handles the most common case (queueing messages while agent is busy).

**BFA applicability**: Extend existing message queue with task-level tracking. Deferred to Tier 5.

---

#### R. Project Memory (BlenderMCP Pro, BFA Coworker) ✅

Rules and conventions remembered across sessions. BlenderMCP Pro saves on .blend file. We use markdown files.

**Source**: BlenderMCP Pro, BFA Coworker

**Why already have**: Our project rules system is comparable. Markdown files are more portable than .blend-embedded data. Could improve with auto-detection of conventions in future tiers.

**BFA applicability**: Already implemented. Enhancement deferred.

---

#### S. Macros / Script Presets (BlenderMCP Pro, BlendAI) ⚪

Save sequences of actions as reusable named tools. BlendAI's script presets are searchable, filterable by space, and can appear in context menus.

**Source**: BlenderMCP Pro, BlendAI

**Why deferred**: BlendAI's script preset system is the most polished implementation. Valuable for power users but requires a persistence layer and UI that's out of scope for Tier 4b. Tier 5 material.

**BFA applicability**: Deferred to Tier 5.

---

#### T. Scene Co-Pilot (BlenderMCP Pro) ⚪

Passive background scanner that flags common issues (unapplied scale, missing UVs, non-manifold geo) with one-click fixes.

**Source**: BlenderMCP Pro

**Why deferred**: Interesting but complex. Requires background polling, issue detection heuristics, and safe auto-fix logic. Tier 6 material.

**BFA applicability**: Deferred to Tier 6.

---

#### U. Render Critic (BlenderMCP Pro, BlendAI) ⚪

AI critiques a render with structured feedback and quality score. BlenderMCP Pro adds iterative refinement (render→critique→fix→re-render).

**Source**: BlenderMCP Pro, BlendAI

**Why deferred**: Requires vision model support and render pipeline integration. Tier 6 material.

**BFA applicability**: Deferred to Tier 6.

---

### 3.5 Blender Integration Patterns

#### V. Right-Click Explain (Chat Companion, BlendAI) 🔴

Right-click any UI element → "Explain" or "What's this?" sends context to LLM for explanation. BlendAI also supports Shift+click for popup response.

**Source**: Chat Companion, BlendAI

**Why critical**: This is one of the most praised features in both Chat Companion (89 ratings, 5/5) and BlendAI (14 ratings, 4/5). It's the fastest way to get help — no typing, no context-switching. Relatively simple to implement (context menu operator on existing Blender menus).

**BFA applicability**: Direct. Register operator on `WM_MT_button_context_menu`, `NODE_MT_context_menu`, `VIEW3D_MT_object_context_menu`. Capture element identity, send as structured prompt.

**Implementation phase**: Phase 5

---

#### W. Context Awareness (BlendAI) 🟢

The LLM knows which editor space you're in and what your modified hotkeys are. Provides more relevant answers.

**Source**: BlendAI

**Why medium**: We already inject scene context via the agent's scene snapshot. Adding active space and keymap info would make explanations more precise but isn't blocking.

**BFA applicability**: Enhance existing scene context with active editor type and keymap overrides. Deferred to post-Tier 4b polish.

---

#### X. @Mention System (BFA Coworker) ✅

Type @ to search and insert scene item names (objects, materials, collections, etc.).

**Source**: BFA Coworker (unique)

**Why already have**: This is a competitive advantage — no other addon has it. Already implemented and working.

**BFA applicability**: Already implemented.

---

### 3.5b BuddyCode GPT Patterns (Text Editor Specialist)

BuddyCode GPT is a Text Editor-centric tool, so its patterns are most relevant to **Tier 4c** (Text Editor / IDE Agent) rather than Tier 4b. None are critical for the chat panel itself.

#### AA. Text Editor File Browser (BuddyCode GPT) 🟢

A built-in file browser rendered inside the Text Editor sidebar. Navigate the filesystem, open/close scripts, and manage files without leaving Blender.

**Source**: BuddyCode GPT

**Why medium**: Only BuddyCode GPT has this. Chat Companion has Text Editor completion but no browser. Direct fit for Tier 4c's Text Editor integration — the sidebar we already have can gain a file list. Deferred to Tier 4c; a file browser is out of scope for chat-panel polish.

**BFA applicability**: In Tier 4c, add a "Files" sub-panel to the Text Editor sidebar listing project scripts with open/close actions.

**Implementation phase**: Tier 4c (Phase 3 or later)

---

#### AB. Multi-Pair Execution (BuddyCode GPT) 🟢

Define multiple (input, system prompt, temperature) pairs and run them all in one click, with or without document context. Batch-processes several prompts concurrently.

**Source**: BuddyCode GPT

**Why medium**: Unique among all competitors — genuinely novel. Useful for batch code generation (e.g., "generate 5 variations of this operator"). Not critical for Tier 4b, but a strong differentiator for power users. Best fit after session history (Phase 4) exists, so batch results can be stored.

**BFA applicability**: Add a "Batch" mode to the agent: a list editor for multiple prompts, run sequentially through the message queue, results appended as one session.

**Implementation phase**: Tier 5 (post-4b)

---

#### AC. Document Loading with Vector Search (BuddyCode GPT) 🟢

Load documents (markdown, text) and query them with FAISS vector search so the LLM answers with project-local context.

**Source**: BuddyCode GPT

**Why medium**: Nice RAG-style feature. We already support project rules (markdown files) injected into context — the gap is *retrieval*: today we inject everything; BuddyCode retrieves the relevant chunk. For typical Blender scripts this is overkill, but valuable once docs grow large. Requires a vector store dependency (FAISS) — evaluate before adopting.

**BFA applicability**: Deferred. If adopted, use a lightweight approach: chunk project rules + TF-IDF/embedding-free scoring, no FAISS dependency.

**Implementation phase**: Tier 5 or 6

---

#### AD. In-App Module Installation (BuddyCode GPT) 🟢

Install Python modules via `pip` directly from the addon's preferences — no terminal required.

**Source**: BuddyCode GPT

**Why medium**: Solves a real problem: users paste code that imports third-party modules and can't run it. A safe "Install dependency" action on a failed import is high-value. Needs careful sandboxing (we already have `weak_sandbox.py`) and clear user confirmation, since installing into Blender's Python can break the environment.

**BFA applicability**: In Phase 2 (Code Blocks + Run), when execution fails with `ModuleNotFoundError`, offer "Install module" with explicit warning dialog. Otherwise defer to Tier 5.

**Implementation phase**: Phase 2 optional / Tier 5

---

### 3.6 Setup & Onboarding Patterns

#### Y. One-Click Setup Flow (Blender Buddy, BlenderMCP Pro) 🟢

Guided setup that auto-detects hardware, downloads models, and configures everything.

**Source**: Blender Buddy, BlenderMCP Pro

**Why medium**: We already have model download. Blender Buddy's GPU auto-detection and tiered model options are the gold standard. Would improve first-run experience but isn't blocking for Tier 4b.

**BFA applicability**: Enhance existing download flow with GPU detection and model tier recommendations. Deferred to Tier 5.

---

#### Z. Provider Auto-Fallback (BlenderMCP Pro) ⚪

If one provider hits a rate limit, silently switch to the next. Session never breaks.

**Source**: BlenderMCP Pro

**Why deferred**: Nice-to-have but adds complexity. Most users stick to one provider. Tier 5 material.

**BFA applicability**: Deferred to Tier 5.

---

### 3.7 Priority-Ordered Implementation Roadmap

This table shows the top-down evaluation of what to implement, in what order, and why. Each row builds on the ones above it.

| Step | Pattern | Phase | What Changes | Why This Order | User-Visible Result |
|---|---|---|---|---|---|
| **1** | F — Markdown Rendering | 1 | New `_render_markdown()` in `ui_chat.py` | Foundation for all message display. Every other display feature (code blocks, links, lists) depends on markdown parsing. Must come first. | Agent responses render with bold, italic, code, lists, links instead of plain text. |
| **2** | G — Code Blocks + Run | 2 | Parse ```python fences, add Run button, safety scanner | Second foundation. Code blocks are the most common markdown element in agent responses. The Run button is the bridge from "tell me" to "do it." | Python code blocks have a Run button. Safety scanner warns about dangerous calls. |
| **3** | H — Error→Fix Loop | 3 | Capture traceback, display Fix button, send structured prompt | Closes the loop opened by Phase 2. Without this, code execution errors are dead ends. Depends on Phase 2's Run operator existing. | Failed code shows error + "Fix with Coworker" button. One click sends to agent. |
| **4** | L+M — Session History + Auto-Title | 4 | `sessions_index.json`, `BFACW_PT_session_history` panel, Load/Rename/Delete operators | Independent of Phases 1-3. Can be built in parallel. Session management is the biggest missing infrastructure feature. | History panel shows past sessions with auto-titles. Load, rename, delete any session. |
| **5** | V — Right-Click Explain | 5 | `BFACW_OT_explain` operator, context menu registration | Independent of Phases 1-4. Can be built in parallel. High impact, relatively low effort. | Right-click any UI element → "Explain with Coworker" → agent explains it. |
| **6** | E — Screenshot/Vision Input | 6 | Screenshot toggle, area picker, base64 encode, multimodal send | Depends on markdown rendering (Phase 1) for displaying results. Vision is a differentiator — only Blender Buddy and BlenderMCP Pro have it. | Toggle screenshot, click editor to capture, agent sees what you see. |
| **7** | A+N+O — Polish (Toggles, Revert, Per-Message Actions) | 7 | Toggle grid, Revert button, Edit/Remove operators | Ships last. All quality-of-life improvements that depend on the core operators (Phases 1-6) existing. | Toggle grid for quick settings. Revert undoes last turn. Edit/Remove on each message. |

### 3.8 Dependency Graph

```mermaid
graph TD
    P1[Phase 1: Markdown] --> P2[Phase 2: Code Blocks + Run]
    P2 --> P3[Phase 3: Error→Fix]
    P1 --> P6[Phase 6: Screenshot]
    P4[Phase 4: Session History] --> P7[Phase 7: Polish]
    P5[Phase 5: Right-Click Explain] --> P7
    P3 --> P7
    P6 --> P7
```

**Key**: Phases 1-2-3 form a linear chain (each builds on the previous). Phases 4 and 5 are independent and can be built in parallel with the chain. Phase 6 depends on Phase 1. Phase 7 is the final integration point.

### 3.9 Effort vs. Impact Matrix

| | Low Effort | Medium Effort | High Effort |
|---|---|---|---|
| **High Impact** | V — Right-Click Explain<br>M — Auto-Title Sessions | F — Markdown Rendering<br>L — Session History UI | G — Code Blocks + Run<br>H — Error→Fix Loop |
| **Medium Impact** | N — Revert Last Pair<br>O — Per-Message Actions | E — Screenshot/Vision<br>A — Toggle Grid | |
| **Low Impact** | J — URL Links<br>K — Token Counter | I — Collapse Long Msgs<br>W — Context Awareness | |

**Takeaway**: Phase 5 (Right-Click Explain) is the highest ROI single feature — low effort, high impact. Phase 1 (Markdown) is the highest impact foundation. Phases 2-3 (Code Blocks + Error→Fix) are the most complex but essential for the agentic experience.

---

## 4. Gap Analysis: BFA Coworker vs. Competition

### 4.1 Critical Gaps (Must Fix — Tier 4b) 🔴

| # | Gap | Pattern | Phase | Competitors Who Have It | Impact |
|---|---|---|---|---|---|
| 1 | **Markdown rendering in chat** | F | 1 | Blender Buddy, Chat Companion | Responses are hard to read as plain text |
| 2 | **Code blocks with Run button** | G | 2 | Blender Buddy, Chat Companion, BlendAI | Users must copy-paste code to Text Editor |
| 3 | **Error→Fix loop** | H | 3 | Chat Companion, BlendAI, Suzanne AI | No way to iteratively fix broken code |
| 4 | **Session history UI** | L+M | 4 | BlenderMCP Pro, BlendAI | Can't browse/load past sessions |
| 5 | **Right-click Explain** | V | 5 | Chat Companion, BlendAI | No quick way to ask "what does this button do?" |

### 4.2 Important Gaps (Should Fix — Tier 4b or 5) 🟡

| # | Gap | Pattern | Phase | Competitors Who Have It | Impact |
|---|---|---|---|---|---|
| 6 | **Screenshot/vision input** | E | 6 | Blender Buddy, BlenderMCP Pro | Can't show the agent what you're looking at |
| 7 | **Per-message actions (Edit, Remove)** | O | 7 | BlendAI | Can't edit a sent message or remove a pair |
| 8 | **Toggle grid (Web, Deep, Vision)** | A | 7 | Blender Buddy | Must open preferences to change behavior |
| 9 | **Revert last pair** | N | 7 | Blender Buddy | Must clear entire conversation to undo one turn |
| 10 | **Collapse/expand long messages** | I | — | Blender Buddy | Long responses clutter the panel |
| 11 | **Token usage counter** | K | — | Blender Buddy | No warning before context window fills |
| 12 | **Popup/quick chat** | B | — | BlendAI, Blender Buddy | Must open N-panel to chat |

### 4.3 Nice-to-Have Gaps (Future Tiers) 🚀

These are the features we're deliberately NOT implementing in Tier 4b. They're organized by target tier with rationale for why they belong there.

#### Tier 5 — Power User & Infrastructure (post-4b, ~3-4 weeks)

| # | Gap | Pattern | Source | Effort | Why Tier 5 |
|---|---|---|---|---|---|
| 13 | **Popup/quick chat window** | B | BlendAI | Medium | Requires modal operator + cursor tracking. N-panel is sufficient for now. High value for quick questions. |
| 14 | **Macros / reusable tool sequences** | S | BlendAI, BlenderMCP Pro | High | Requires persistence layer + macro editor UI. BlendAI's script preset system is the reference. |
| 15 | **Background task queue** | Q | BlenderMCP Pro | Medium | Extends existing message queue. We already have queue — this makes it task-level with live status. |
| 16 | **Provider auto-fallback** | Z | BlenderMCP Pro | Medium | Silent switch on rate limit. Most users stick to one provider, but power users with multiple keys benefit. |
| 17 | **GPU auto-detection for local models** | Y | Blender Buddy | Medium | Blender Buddy's GPU detection is the gold standard. Improves first-run experience for local users. |
| 18 | **One-click setup flow** | Y | Blender Buddy | Medium | Bundled with GPU auto-detection. Guided setup that auto-configures everything. |
| 19 | **Multi-pair / batch execution** | AB | BuddyCode GPT | Medium | Run multiple prompts in one click. Genuinely novel (no other competitor has it). Needs session history (Phase 4) first so batch results can be stored. |
| 20 | **In-app module installation** | AD | BuddyCode GPT | Medium | One-click pip install on `ModuleNotFoundError` from executed code. Requires sandboxing care — we already have `weak_sandbox.py`. |

**Tier 5 delivers**: Quick chat access, reusable automation, robust queue, smoother onboarding, batch prompting, and in-app dependency install. This is the "power user" tier.

#### Tier 6 — Advanced Intelligence (post-Tier 5, ~4-6 weeks)

| # | Gap | Pattern | Source | Effort | Why Tier 6 |
|---|---|---|---|---|---|
| 21 | **Agent Teams with planner** | P | BlenderMCP Pro | Very High | Multi-agent orchestration, dependency resolution, parallel execution. The most complex feature. |
| 22 | **Scene Co-Pilot (passive issue detection)** | T | BlenderMCP Pro | High | Background polling, issue detection heuristics, safe auto-fix logic. |
| 23 | **Render Critic with iterative refinement** | U | BlenderMCP Pro | High | Requires vision model + render pipeline integration + iterative loop. |
| 24 | **Voice input** | — | BlenderMCP Pro | Medium | Local Whisper integration. No API key needed. |
| 25 | **Text-to-speech output** | — | Chat Companion | Medium | Reads answers aloud. Unique among current competitors. |
| 26 | **Document loading with vector search** | AC | BuddyCode GPT | High | RAG-style retrieval over project docs. Requires vector-store dependency; evaluate a lightweight chunk + scoring approach instead of FAISS. |

**Tier 6 delivers**: Multi-agent orchestration, passive scene monitoring, render feedback loops, multimodal I/O, and project-doc retrieval. This is the "intelligence" tier.

#### Out of Scope

| # | Gap | Source | Why Out of Scope |
|---|---|---|---|
| 27 | **Cross-DCC bridge** | BlenderMCP Pro | BFA-specific. Requires Unreal/Unity integration. Not relevant to our user base. |

### 4.4 BFA Coworker's Unique Advantages

These are capabilities that NO competitor has — they're our moat:

| Advantage | Why It Matters |
|---|---|
| **Full MCP tool system (75+ tools)** | Only BlenderMCP Pro matches this. The agent doesn't just chat — it manipulates the scene. Chat Companion, BlendAI, and Blender Buddy are chat-only. |
| **@Mention system** | Type `@` to insert scene item references. No other addon has this. Makes context injection effortless. |
| **Project rules (markdown files)** | Persistent conventions across sessions. BlenderMCP Pro has this (.blend-embedded), but ours is more portable and editable. |
| **Local + remote LLM** | Works offline with local models AND online with remote APIs. Blender Buddy is local-only. BlendAI is remote-only. We do both. |
| **Message queue** | Queue messages while agent is busy. Only BlenderMCP Pro has background tasks. |
| **Free and open-source** | No $50 license (BlenderMCP Pro). No credit system (BlendAI). No vendor lock-in. |

**Note on BuddyCode GPT**: Its Text Editor file browser is the one genuinely novel pattern we don't have. It's a Tier 4c item (Text Editor / IDE Agent), not a Tier 4b chat-panel item — see Pattern AA.

### 4.5 Competitive Positioning After Tier 4b

After implementing Phases 1-7, BFA Coworker will have:

| Capability | Status |
|---|---|
| Best-in-class agentic tool system (MCP) | ✅ Already have |
| @Mention system | ✅ Already have |
| Project rules | ✅ Already have |
| Message queue | ✅ Already have |
| Local + remote LLM support | ✅ Already have |
| Markdown rendering | ✅ Phase 1 |
| Code blocks + Run + safety scan | ✅ Phase 2 |
| Error→Fix loop | ✅ Phase 3 |
| Session history with auto-title | ✅ Phase 4 |
| Right-click Explain | ✅ Phase 5 |
| Screenshot/vision input | ✅ Phase 6 |
| Per-message Edit/Remove + Revert + Toggle grid | ✅ Phase 7 |

This positions BFA Coworker as the **most complete agentic AI addon for Blender** — combining the best chat UX patterns from BlendAI and Blender Buddy with the agentic capabilities that only BlenderMCP Pro currently offers, all in a free and open-source package.

## 5. Implementation Plan

> **Update (2026-09-01):** Phase 1 (Markdown) is **deferred to Tier 5** — Blender
> PR #163254 adds native `layout.label_markdown()` (MD4C). Tier 4 keeps the
> Tier 3 `_render_markdown()` as-is and only builds *components* in
> `ui_components.py`. See `plan_tier4_master_coordination.md` §4.5. The phases
> below are renumbered to match the master plan's Phase 2 pathway (§15).

### Phase Map (4b ↔ Master Plan Phase 2)

| 4b Phase | Master Plan Step | Feature |
|----------|------------------|---------|
| 1 | 2.1 | Token streaming (SSE) |
| 2 | 2.2 | Code blocks + Run button |
| 3 | 2.3 | Error→Fix loop |
| 4 | 2.4 | Session history UI |
| 5 | 2.5 | Right-click Explain |
| 6 | 2.7 | Screenshot/Vision input |
| 7 | 2.9 | Token budget + readout |
| 8 | 2.10 | Per-message actions & polish |
| 9 | 2.11 | Checkpoint / context-flush |

> Master plan steps 2.6 (Translation) and 2.8 (CHOYA) live in the master plan
> (§5.3, §3) — they share the right-click plumbing (2.5) and `ui_components.py`
> (1.1) respectively.

### Phase 1: Token Streaming (SSE) (~120 LOC, 1 file) — NEW

**What**: Stream LLM responses token-by-token from llama-server over SSE, so
text appears live in the chat panel instead of after a 10–60s wait.

**Reference**: Master plan §14.4 (Phase 2.1). This is the *perceived-performance*
+ *early-abort* win — it does not make the model smarter, but it makes every
other chat feature feel dramatically better.

**Implementation**:

**Step 1.1 — Add `stream` param to `_openai_chat_completions()`**
- Add `stream: bool = False` parameter
- When True, set `"stream": True` in the request body
- Keep the existing retry/fallback logic (503 backoff, text-tool fallback, XML
  tool-call fallback) — streaming must degrade gracefully to non-streaming on
  any parse failure

**Step 1.2 — Incremental SSE reader**
- Read the response in chunks: `resp.read(4096)` loop
- Split on `\n`, parse `data:` lines
- Accumulate `choices[0].delta.content` into `AgentState.streaming_text`
- Call `on_text` per chunk, throttled to ~10/s to avoid UI flooding

**Step 1.3 — Live reasoning**
- Accumulate `delta.reasoning_content` into `AgentState.reasoning_text`
- Call `on_reasoning` per chunk — the reasoning panel fills live

**Step 1.4 — Tool-call streaming**
- Accumulate `delta.tool_calls` partial JSON
- When complete, parse and return as normal `tool_calls`
- Show "calling `create_object`…" the moment the tool name appears

**Step 1.5 — Termination + fallback**
- Handle `data: [DONE]` and error chunks
- On any parse failure, fall back to the existing non-streaming path

**Files modified**: `addon/bfa_coworker/agent_controller.py`

**Verification**:
1. Send a message → text appears live, token by token
2. Reasoning panel fills live for reasoning models
3. Stop button kills generation mid-stream
4. Tool calls still work (streamed tool_calls parsed correctly)
5. Remote APIs (OpenRouter) also stream

---

### Phase 2: Code Blocks with Run Button (~120 LOC, 2 files)

**What**: Detect Python code blocks in responses and add a "Run" button that executes the code in Blender.

**Reference**: Blender Buddy's `BB_OT_run_code_block` with safety scanner.

**Implementation**:

**Step 2.1 — Code block detection**
- Parse fenced code blocks (`` ```python ... ``` ``) from assistant messages
- Render each block with a "Run" button

**Step 2.2 — Safety scanner**
- Before execution, scan for dangerous calls: `os.system`, `subprocess`, `eval`, `exec`, `__import__`, file I/O
- Scan for unknown bpy identifiers (compare against `dir(bpy.ops)`, `dir(bpy.data)`, etc.)
- Show a confirmation dialog with findings

**Step 2.3 — Execution**
- Execute in a sandboxed namespace with `bpy`, `context`, `D`, `C` in scope
- Push an undo step before execution
- Use `context.temp_override` for 3D Viewport context
- Catch and display errors

**Step 2.4 — Trust session**
- "Don't ask again this session" checkbox in confirmation dialog
- Session-scoped flag that skips confirmation for subsequent runs

**Step 2.5 — ModuleNotFoundError handling (optional, Pattern AD)**
- When execution fails with `ModuleNotFoundError: <name>`, show an "Install module" button
- Opens a confirmation dialog explaining the pip install into Blender's Python
- Runs `pip install <name>` via `subprocess` with output captured
- Deferred to Tier 5 if time doesn't allow in Phase 2 — see Pattern AD

**Files modified**:
- `addon/bfa_coworker/ui_chat.py` — code block rendering + Run operator
- `addon/bfa_coworker/agent_controller.py` — optional: code safety scanner utility

**Verification**:
1. Response with Python code block → Run button visible
2. Click Run → confirmation dialog shows safety scan results
3. Safe code executes and modifies scene
4. Dangerous code shows warning in dialog
5. Ctrl+Z undoes the code execution
6. "Don't ask again" skips future confirmations

---

### Phase 3: Error→Fix Loop (~60 LOC, 2 files)

**What**: When executed code throws an error, show a "Fix" button that sends the error + code back to the agent.

**Reference**: Chat Companion's error→fix pattern.

**Implementation**:

**Step 3.1 — Capture execution errors**
- When `BB_OT_run_code_block` catches an exception, store the traceback alongside the code
- Display the error in the chat panel with a "Fix with Coworker" button

**Step 3.2 — Send fix request**
- Clicking "Fix" sends a message to the agent: "The following code produced an error:\n```python\n{code}\n```\nError:\n{traceback}\n\nPlease fix it."
- The agent's response replaces the error display

**Files modified**:
- `addon/bfa_coworker/ui_chat.py` — error display + Fix button
- `addon/bfa_coworker/agent_controller.py` — optional: structured fix request

**Verification**:
1. Run code that produces an error → error displayed with Fix button
2. Click Fix → agent receives error context
3. Agent responds with corrected code
4. New code block has Run button

---

### Phase 4: Session History UI (~200 LOC, 2 files)

**What**: A session management panel that lets users browse, load, rename, and delete past sessions.

**Reference**: BlenderMCP Pro's History panel + BlendAI's Chat History.

**Implementation**:

**Step 4.1 — Session metadata storage**
- Add a `sessions_index.json` file that maps session IDs to metadata (title, created date, message count, last active)
- Auto-title sessions from the first user message (first 60 chars)
- Update metadata on each save

**Step 4.2 — Session History panel**
- New panel: `BFACW_PT_session_history` (collapsible, below chat)
- Paginated list of sessions (most recent first)
- Each entry shows: auto-title, date, message count
- Actions: Load, Rename, Delete (with confirmation)
- "New Session" button to start fresh without losing current

**Step 4.3 — Session switching**
- Loading a session replaces current conversation history
- Current session is auto-saved before switching
- Visual indicator of which session is active

**Files modified**:
- `addon/bfa_coworker/ui_chat.py` — session history panel + operators
- `addon/bfa_coworker/agent_controller.py` — session metadata helpers

**Verification**:
1. Send messages → session auto-titled from first message
2. Click New Session → current session saved, new empty session active
3. Open History panel → see list of past sessions
4. Load a past session → conversation restored
5. Rename a session → new title persists
6. Delete a session → removed from list and disk

---

### Phase 5: Right-Click Explain (~100 LOC, 2 files)

**What**: Right-click any UI element (operator button, property, node) and select "Explain with Coworker" to get an AI explanation.

**Reference**: BlendAI's Explain feature + Chat Companion's "What's this?".

**Implementation**:

**Step 5.1 — Context menu operator**
- Add `BFACW_OT_explain` operator that can be invoked from context menus
- Captures: operator name, property path, node type/name, current editor space
- Sends a structured prompt: "Explain what the '{name}' {type} does in Blender. What is it used for? What are the key settings?"

**Step 5.2 — Registration on context menus**
- Register the operator on relevant context menus:
  - `WM_MT_button_context_menu` (property buttons)
  - `NODE_MT_context_menu` (nodes)
  - `VIEW3D_MT_object_context_menu` (3D view)
- Use poll to only show when Coworker is running

**Step 5.3 — Response display**
- Response appears in the chat panel as a new turn
- If chat panel is closed, open it (or show in popup — Phase 2)

**Files modified**:
- `addon/bfa_coworker/ui_chat.py` — Explain operator
- `addon/bfa_coworker/operators_agent.py` or new file — context menu registration

**Verification**:
1. Right-click a property in the Properties panel → "Explain with Coworker" appears
2. Click it → agent receives explanation request
3. Response appears in chat panel
4. Right-click a node → "Explain with Coworker" appears
5. Works for operators, properties, and nodes

---

### Phase 6: Screenshot/Vision Input (~150 LOC, 2 files)

**What**: Attach a viewport screenshot to a chat message so the agent can see what you're looking at.

**Reference**: Blender Buddy's screenshot system + BlenderMCP Pro's viewport capture.

**Implementation**:

**Step 6.1 — Screenshot capture**
- Add a "📷" toggle button next to the chat input
- Three capture modes (like Blender Buddy):
  - **Area**: Click an editor to capture (eyedropper cursor)
  - **Window**: Capture full Blender window
  - **Viewport**: Capture just the 3D Viewport
- Auto-downscale to max 1280px width, compress to JPEG

**Step 6.2 — Send with message**
- When screenshot toggle is on, capture before sending
- Encode as base64 data URL
- Send as multimodal content (requires vision-capable provider)
- If provider doesn't support vision, show warning

**Step 6.3 — Provider compatibility**
- Check if configured provider supports vision (Claude, Gemini, GPT-4o, GPT-4o-mini)
- Local models: check if vision model is available
- Show clear error if vision isn't supported

**Files modified**:
- `addon/bfa_coworker/ui_chat.py` — screenshot toggle + capture UI
- `addon/bfa_coworker/agent_controller.py` — multimodal message support

**Verification**:
1. Enable screenshot toggle → cursor changes to eyedropper
2. Click an editor → screenshot captured
3. Send message → agent receives image + text
4. Agent responds with image-aware answer
5. Non-vision provider → clear error message

---

### Phase 7: Token Budget + Readout (~150 LOC, 3 files) — NEW

**What**: A per-turn token envelope the agent must stay inside, plus a live
readout of where tokens are going (prompt / reasoning / output / tools).

**Reference**: Master plan §14.3. This is the *smarts* win — keeping small local
models (7–14B) inside their context window reduces hallucinations and spirals.

**Implementation**:

**Step 7.1 — `TokenBudget` dataclass**
```python
@dataclass
class TokenBudget:
    max_prompt: int = 8192      # Configurable via preferences
    max_output: int = 2048
    used_prompt: int = 0
    used_output: int = 0
    used_tools: int = 0
    warnings_given: int = 0
```

**Step 7.2 — Read `usage` from every response**
- llama-server returns `usage.prompt_tokens` / `completion_tokens` / `total_tokens`
- Both streaming (final chunk) and non-streaming responses carry this
- Accumulate into `AgentState.token_budget`

**Step 7.3 — Budget-aware trimming**
- `_trim_tool_result()` currently truncates to 500 chars — make the cap shrink
  as the turn grows (e.g. 500 → 300 → 150)
- Prevents context bloat from tool results accumulating across multiple calls

**Step 7.4 — Budget warning injection**
- When a turn exceeds 80% of budget, append a system hint before the next call:
  "You are at 80% of your token budget — prefer short answers, avoid re-listing
  scene contents, and skip redundant tool calls."
- This directly steers the model toward economy on small contexts

**Step 7.5 — UI readout**
- Live counter row in chat panel: `prompt 2.1k · reasoning 1.4k · output 0.8k · tools 0.3k`
- Reads from `AgentState.token_budget`, refreshed on each redraw

**Step 7.6 — Preferences**
- `token_budget_enabled` (default True), `token_budget_max` (default 8192)

**Files modified**: `addon/bfa_coworker/agent_controller.py`,
`addon/bfa_coworker/ui_chat.py`, `addon/bfa_coworker/preferences.py`

**Verification**:
1. Run a long conversation → token counter updates live
2. Watch the counter approach 80% → budget warning appears in the agent's next response
3. Tool results visibly shrink late in a long turn
4. Disabling the budget in preferences removes the counter + warnings

---

### Phase 8: Per-Message Actions & Polish (~80 LOC, 1 file)

**What**: Add Edit and Remove buttons to each user message, and improve the overall message action bar.

**Reference**: BlendAI's per-message actions.

**Implementation**:

**Step 7.1 — Edit message**
- "Edit" button on user messages that re-populates the input field
- Preserves any attachments/screenshot

**Step 7.2 — Remove message pair**
- "Remove" button that deletes the user message + assistant response
- Confirmation dialog to prevent accidents

**Step 7.3 — Improved action bar**
- Consistent icon-only buttons: Copy, Edit, Remove
- Tooltips explain each action

**Files modified**: `addon/bfa_coworker/ui_chat.py`

**Verification**:
1. Click Edit on a user message → input field populated with message text
2. Click Remove → confirmation dialog → message pair deleted
3. All action buttons have clear tooltips

---

### Phase 9: Checkpoint / Context-Flush (~180 LOC, 2 files) — NEW

**What**: When the token budget (Phase 7) approaches its cap, the agent writes
a **checkpoint** — a compact summary of what was done, what exists in the
scene, and what remains — then **flushes the model window** (drops old turns
from the prompt, keeps them in the UI history) and **resumes** with the summary
as the new context anchor. Also `/checkpoint` + `/resume` slash commands.

**Reference**: Master plan §14.6 (Phase 2.11). This is the "checkpoint to reset
context, save progress, then carry on" pattern from long-running chat systems.

**Why it matters**: Without it, a long session degrades — context bloat makes
small local models hallucinate and spiral. With it, the window stays small and
sharp, and the session is resumable even across restarts.

**Implementation**:

**Step 9.1 — `Checkpoint` dataclass**
```python
@dataclass
class Checkpoint:
    id: str                    # "cp_20260901_1432"
    timestamp: float
    summary: str               # LLM-generated: what was done, what's next
    entities: str              # _EntityDiff.summary() — what exists in the scene
    plan: list[str]            # remaining steps (from the LLM's own plan)
    history_tail: list[dict]   # last 2-3 turns kept verbatim (recent context)
    token_usage: dict          # prompt/completion/total at checkpoint time
```

**Step 9.2 — `_write_checkpoint()`**
- Builds a summary prompt: "You are at 80% of your token budget. Write a
  checkpoint: (a) what has been accomplished, (b) what entities exist in the
  scene, (c) what remains to do, (d) the next step."
- Calls the LLM (a normal call — the model summarizes its own work)
- Stores the result in `AgentState.checkpoints[]`

**Step 9.3 — `_flush_history(checkpoint)`**
- Rebuilds `history_to_send` as: `[system prompt] + [checkpoint summary] +
  [last 2-3 turns verbatim]`
- Old turns are dropped from the *prompt* but remain in
  `conversation_history` for the UI (they're just not sent)

**Step 9.4 — Trigger wiring**
- Check the budget (Phase 7) in the loop; auto-checkpoint at 80% (once per turn)
- Also a recovery point: on spiral detection, checkpoint-then-flush instead of
  just truncating

**Step 9.5 — Persistence**
- `checkpoints.json` in `~/.cache/bfa_coworker/` — survives restarts
- Load on startup; `/resume` restores the latest checkpoint as the anchor

**Step 9.6 — Slash commands**
- `/checkpoint` — force a checkpoint now
- `/resume` — restore the latest checkpoint and continue

**Files modified**: `addon/bfa_coworker/agent_controller.py`,
`addon/bfa_coworker/ui_chat.py`

**Verification**:
1. Run a long conversation → at 80% budget, a checkpoint is written (visible in UI)
2. The model window flushes — old turns no longer sent, but still visible in chat
3. The agent resumes with the summary — it knows what was done and what's next
4. `/checkpoint` forces one; `/resume` restores after restart
5. `checkpoints.json` exists in `~/.cache/bfa_coworker/`

---

## 6. Summary of Changes

| Phase | Pattern | Feature | Files Changed | LOC | Priority |
|---|---|---|---|---|---|
| 1 | — | Token streaming (SSE) | 1 | ~120 | 🔴 CRITICAL |
| 2 | G | Code blocks + Run button | 2 | ~120 | 🔴 CRITICAL |
| 3 | H | Error→Fix loop | 2 | ~60 | 🔴 CRITICAL |
| 4 | L+M | Session history UI + auto-title | 2 | ~200 | 🔴 CRITICAL |
| 5 | V | Right-click Explain | 2 | ~100 | 🔴 CRITICAL |
| 6 | E | Screenshot/Vision input | 2 | ~150 | 🟡 HIGH |
| 7 | K | Token budget + readout | 3 | ~150 | 🟡 HIGH |
| 8 | A+N+O | Toggle grid + Revert + Per-message actions | 1 | ~80 | 🟡 HIGH |
| 9 | — | Checkpoint / context-flush | 2 | ~180 | 🟡 HIGH |
| **Total** | | | **3-4** | **~1,160** | |

> **Note (2026-09-01):** Markdown rendering (originally Phase 1) is deferred to
> Tier 5 — native `label_markdown()` is inbound (PR #163254). The Tier 3
> `_render_markdown()` stays as-is. Total LOC revised from ~860 → ~1,160 with
> the three new phases (streaming + token budget + checkpoint).

### Files Modified

| File | Phases |
|---|---|
| `addon/bfa_coworker/ui_chat.py` | 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| `addon/bfa_coworker/agent_controller.py` | 1, 2, 3, 4, 6, 7, 9 |
| `addon/bfa_coworker/operators_agent.py` (or new) | 5 |
| `addon/bfa_coworker/preferences.py` | 7 |

### Key Design Decisions

| Decision | Rationale |
|---|---|
| **Markdown deferred to Tier 5** | Blender PR #163254 adds native `label_markdown()` (MD4C). Tier 4 keeps Tier 3 `_render_markdown()` as-is; only components in `ui_components.py`. |
| **Streaming via llama-server SSE** | `"stream": true` on the existing chat completions endpoint. SSE parsers (`_parse_sse_json`) + `streaming_text`/`on_reasoning` already exist — no new transport needed. |
| **Token budget as the smarts lever** | Streaming improves *perceived* speed; the budget envelope (readout + budget-aware trimming + warning injection) keeps small models inside context → fewer hallucinations/spirals. |
| **Checkpoint before flush** | The model summarizes its own work (what was done, entities, plan) *before* old turns are dropped — nothing is lost, and the agent resumes with a compact anchor instead of a blank slate. |
| **Safety scanner before code execution** | Blender Buddy's scanner pattern is proven. Catches dangerous calls AND hallucinated bpy identifiers before they cause errors. |
| **Session auto-title from first message** | BlenderMCP Pro's approach. Eliminates manual naming friction. |
| **Right-click Explain on existing menus** | BlendAI's approach. Leverages Blender's built-in context menu system rather than custom UI. |
| **Screenshot with area picker** | Blender Buddy's approach. More flexible than full-window-only capture. |
| **Error→Fix as structured prompt** | Chat Companion's approach. Sends code + traceback as a new turn rather than requiring special API. |
| **Budget readout from `usage` field** | llama-server returns prompt/completion/total tokens in every response — the readout is free, no extra calls. |
| **Budget-aware `_trim_tool_result()`** | Shrinks tool-result truncation (500 → 300 → 150 chars) as a turn grows — prevents context bloat that degrades small models. |
| **80% budget warning injection** | Append a system hint ("You are at 80% of budget — prefer short answers") before the next call — steers the model toward economy. |

### What We're NOT Doing (Yet)

These patterns are noted but deferred to future tiers. Each has a concrete implementation plan in the target tier's document.

| Pattern | Source | Target Tier | Phase | Plan Document |
|---|---|---|---|---|
| **Markdown rendering (native)** | Blender PR #163254 | Tier 5 | 5.x | `plan_tier5_generative_local_systems.md` — adopt `label_markdown()` with feature-detect + fallback |
| **Popup/quick chat window** | BlendAI, Blender Buddy | Tier 5 | 5f.1 | `plan_tier5_generative_local_systems.md` |
| **Macros / reusable tool sequences** | BlendAI, BlenderMCP Pro | Tier 5 | 5f.2 | `plan_tier5_generative_local_systems.md` |
| **Background task queue** | BlenderMCP Pro | Tier 5 | 5f.3 | `plan_tier5_generative_local_systems.md` |
| **Provider auto-fallback** | BlenderMCP Pro | Tier 5 | 5f.4 | `plan_tier5_generative_local_systems.md` |
| **GPU auto-detection + one-click setup** | Blender Buddy | Tier 5 | 5f.5 | `plan_tier5_generative_local_systems.md` |
| **Multi-pair / batch execution** | BuddyCode GPT | Tier 5 | 5f.6 | `plan_tier5_generative_local_systems.md` |
| **In-app module installation** | BuddyCode GPT | Tier 5 | 5f.7 | `plan_tier5_generative_local_systems.md` |
| **Agent Teams with planner** | BlenderMCP Pro | Tier 6 | 6f.1 | `plan_tier6_domain_tooling.md` |
| **Scene Co-Pilot** | BlenderMCP Pro | Tier 6 | 6f.2 | `plan_tier6_domain_tooling.md` |
| **Render Critic with iterative refinement** | BlenderMCP Pro, BlendAI | Tier 6 | 6f.3 | `plan_tier6_domain_tooling.md` |
| **Voice input** | BlenderMCP Pro | Tier 6 | 6f.4 | `plan_tier6_domain_tooling.md` |
| **Text-to-speech output** | Chat Companion | Tier 6 | 6f.5 | `plan_tier6_domain_tooling.md` |
| **External client config (one-click)** | BlenderMCP Pro | Tier 6 | 6f.6 | `plan_tier6_domain_tooling.md` |
| **Document loading with vector search** | BuddyCode GPT | Tier 6 | 6f.7 | `plan_tier6_domain_tooling.md` |
| **Text Editor file browser** | BuddyCode GPT | Out of scope | — | Removed from Tier 4c (2026-09-01) — not artist-friendly tooling |
| **Cross-DCC bridge** | BlenderMCP Pro | Out of scope | — | BFA-specific, not relevant |

### Competitive Positioning After Tier 4b

After implementing Phases 1-8, BFA Coworker will have:

| Capability | Status |
|---|---|
| Best-in-class agentic tool system (MCP) | ✅ Already have |
| @Mention system | ✅ Already have |
| Project rules | ✅ Already have |
| Message queue | ✅ Already have |
| Local + remote LLM support | ✅ Already have |
| Markdown rendering | ✅ Phase 1 |
| Code blocks + Run + safety scan | ✅ Phase 2 |
| Error→Fix loop | ✅ Phase 3 |
| Session history with auto-title | ✅ Phase 4 |
| Right-click Explain | ✅ Phase 5 |
| Screenshot/vision input | ✅ Phase 6 |
| Toggle grid + Revert + Per-message Edit/Remove | ✅ Phase 7 |

This positions BFA Coworker as the **most complete agentic AI addon for Blender** — combining the best chat UX patterns from BlendAI and Blender Buddy with the agentic capabilities that only BlenderMCP Pro currently offers, all in a free and open-source package.