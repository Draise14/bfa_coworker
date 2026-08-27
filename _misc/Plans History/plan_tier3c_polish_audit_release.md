# Plan: Tier 3c — Polish, Audit & Release

## ✅ Done

**TL;DR** — Tie up all Tier 3 loose ends into a shippable milestone: debugging toggle, expanded benchmarks, session logging, lightweight asset browser tools, full message queue, mention system overhaul, collection color tag tool, and UX hardening. Then finalize docs, changelog, build, and smoke test for a v1.1.37 release — clearing the deck for Tier 4.

---

## Phase 1: Debugging Toggle & Diagnostics
*Small, quick wins — no dependencies.*

**Steps**
1. **Make `BFACW_DEBUG` a user-facing toggle** — Add `debug_mode` BoolProperty to `_BlenderMCPPreferences` (default `False`). Replace the hardcoded `BFACW_DEBUG = True` in `shared.py` with a function `is_debug_mode()` that reads from preferences. Update `_draw_diagnostics()` to check the preference instead of the constant.
2. **Add tool-level logging granularity** — Extend `use_log` (currently binary) to an enum: `OFF` / `ERRORS_ONLY` / `ALL`. When `ERRORS_ONLY`, only log tool calls that returned errors. Wire into `mcp_to_blender_server.py`'s logging path. **Multiline labels** for enum items to avoid truncation.
3. **Add "Open Log" button** — Button in Diagnostics panel that opens the Blender console/terminal or a text block with recent log output.
4. **Custom skills multiline entry** — Convert `custom_skills_text` from a single-line `StringProperty` to a multiline-capable entry using `layout.textbox()` — the same pattern already used for the chat input in `ui_chat.py`. In the preferences draw method, replace `custom_box.prop(self, "custom_skills_text")` with `layout.textbox(self, "custom_skills_text")`. The `StringProperty` itself stays unchanged (it already stores `\n` line breaks); only the UI widget changes. Add `row.scale_y = 3` for comfortable editing height.

**Relevant files**
- `addon/bfa_coworker/shared.py` — replace `BFACW_DEBUG` constant with `is_debug_mode()` function
- `addon/bfa_coworker/preferences.py` — add `debug_mode` BoolProperty, `log_level` EnumProperty, update `_draw_diagnostics()`, convert `custom_skills_text` to multiline
- `addon/bfa_coworker/mcp_to_blender_server.py` — wire `log_level` into bridge logging

**Verification**
1. Toggle debug mode in Preferences → Diagnostics panel appears/disappears
2. Set log level to ERRORS_ONLY → only error tool calls appear in console
3. "Open Log" button opens console/text block

---

## Phase 2: Benchmark Expansion
*Depends on Phase 1 (debug toggle helps benchmark visibility).*

**Steps**
1. **Add timing to all existing 8 suites** — Wrap each step in `_run_test_step()` with `time.monotonic()` timing. Store elapsed time per step in `_test_suite_timings` dict keyed by `(suite_name, step_number)`. Display elapsed time next to each completed step in the Diagnostics UI. **No pass/fail — timing only; the user judges success visually.**
2. **Add editor-specific benchmark suites** — New suites in `_TEST_SUITES`:
   - `shader_nodes` (4 steps): Create material, build node tree (Noise → ColorRamp → BSDF), assign to object, render preview
   - `geometry_nodes` (4 steps): Create GN modifier, build node tree (distribute points, instance, random scale), apply to grid, verify
   - `sequencer` (4 steps): Create color strip, add transform effect, add text overlay, set render range
   - `image_editor` (3 steps): Create new image, draw colored rectangle via pixels, save to disk
   - `compositor` (4 steps): Enable compositor nodes, add Render Layers + Blur + Viewer, set up basic comp, render and verify
   - `multi_editor_cross` (4 steps): Create mesh object, assign GN modifier, create material for it, render frame — tests agent's ability to work across editors
   **Multiline labels** for suite names in the Diagnostics UI — use `layout.label(text=..., icon=...)` with `row.scale_y = 1.5` or split across two rows to avoid truncation.
3. **Expand `assets_materials` suite** — Split into two suites:
   - `assets_browser` (6 steps): Search asset browser for assets, create a cube and assign a material asset, load a node group into the material, load a collection asset into scene, load a mesh asset into scene, load a world asset
   - `polyhaven` (5 steps): Search Polyhaven for a texture, download and create PBR material, search for a model, download and import model, search for an HDRI and set as world
   **Multiline labels** for suite names in the Diagnostics UI — use `layout.label(text=..., icon=...)` with `row.scale_y = 1.5` or split across two rows to avoid truncation.
4. **Reset-on-completion** — When the user clicks the last step's button and it completes, the suite auto-resets to step 1 (progress indicator shows all steps cleared). The Reset button also works at any time to manually reset.
5. **Results persistence** — Save benchmark timings to a JSON file in the addon directory (`benchmark_results.json`) with timestamps, model name, and per-step timing. Add a "Compare" button that shows delta from last run.

**Relevant files**
- `addon/bfa_coworker/operators_agent.py` — `_TEST_SUITES` (add 6 new suites, split assets_materials into assets_browser + polyhaven), `_run_test_step()` (add timing, auto-reset on last step), new `_test_suite_timings` dict
- `addon/bfa_coworker/preferences.py` — `_draw_diagnostics()` (show elapsed time per step, Compare button)

**Verification**
1. Run each existing suite → see elapsed time per step
2. Run each new editor-specific suite → all steps complete, timing shown
3. Run `assets_browser` suite → agent searches asset browser, assigns material, loads node group, loads collection/mesh/world assets
4. Run `polyhaven` suite → agent searches/downloads texture, model, HDRI
5. Complete last step of any suite → auto-resets to step 1
6. Click Reset mid-suite → resets to step 1
7. Run same suite twice → Compare shows timing delta
8. `benchmark_results.json` written with correct data

---

## Phase 3: Session Logging & Memory Bank
*No dependencies on Phase 1-2, can run in parallel.*

**Steps**
1. **Create `export_session_log()` function** — New function in `agent_controller.py` that gathers:
   - System prompt (full text from `_build_system_prompt()`)
   - Full conversation history (all messages, untruncated tool results)
   - Error signatures and spiral detection events
   - llama-server log tail (last 50 lines from `_state.llm_stderr`)
   - Addon version, Blender version, OS info, model info
   - Writes to a timestamped text block: `Coworker_Session_2026-08-24_14-30-00`
2. **Auto-trigger on error spirals** — When `_consecutive_errors >= 3` (spiral detected), automatically call `export_session_log()` and append a note: "AUTO-SAVED: Error spiral detected."
3. **Add "Export Session Log" button** — New operator `BFACW_OT_export_session_log` with a button in the chat panel (next to Clear). Also add a "Copy Session Log" variant that copies to clipboard.
4. **Fix code bank to include errors** — Currently `_save_code_to_text_block()` only saves successful `execute_blender_code` calls. Add a `include_errors` parameter (default `True` for the memory bank). Error-producing code gets saved with an `# ERROR: ...` comment prefix.
5. **Versioned history saves** — Instead of overwriting `default.json`, save timestamped copies (`default_2026-08-24_14-30-00.json`). Keep last 10 sessions, auto-prune oldest.

**Relevant files**
- `addon/bfa_coworker/agent_controller.py` — new `export_session_log()`, auto-trigger in spiral detection, update `_save_code_to_text_block()`, update `_save_chat_history()`
- `addon/bfa_coworker/ui_chat.py` — new `BFACW_OT_export_session_log` operator, button in chat panel, new `BFACW_OT_copy_session_log` operator

**Verification**
1. Send a few messages → click "Export Session Log" → text block created with full history, system prompt, version info
2. Trigger an error spiral (send 3 impossible requests) → auto-saved session log appears
3. Code bank now includes error-producing code with `# ERROR:` prefix
4. Multiple sessions → `default_*.json` files accumulate, oldest pruned at 10

---

## Phase 4: Lightweight Asset Browser Tools
*No dependencies, can run in parallel with Phase 3.*

**Steps**
1. **`get_asset_libraries` tool** — New tool that lists all asset libraries (name, path, asset count). Uses `bpy.context.preferences.filepaths.asset_libraries` to enumerate. Returns JSON with library name, path, and total asset count.
2. **`list_assets_in_catalog` tool** — New tool that lists assets in a specific catalog. Parameters: `library_name`, `catalog_path` (e.g., "Characters/Humans"). Uses `bpy.ops.asset.library_refresh()` then iterates asset handles. Returns asset name, type, tags, and preview availability.
3. **`search_assets` tool** — New tool that searches across libraries by name/tag/type. Parameters: `query`, `library_name` (optional, default all), `asset_type` (optional filter). Client-side filtering of the catalog listing. Returns top 20 matches with relevance scoring.
4. **`load_asset_in_context` tool** — New tool that loads an asset into the current context. Parameters: `library_name`, `asset_name`, `asset_type` (auto-detected if omitted). Type-aware loading:
   - **Material**: Assigns to active object (or creates a cube if none selected)
   - **Node Group**: Opens a node editor, loads the node group into the active node tree
   - **Collection**: Appends the collection instance to the scene
   - **Mesh/Object**: Appends the object to the scene at cursor/3D cursor
   - **World**: Sets as the scene world
   - **Action**: Assigns to active object's animation data
   Uses `bpy.ops.asset.open_containing_blend_file()` + `bpy.data.libraries.load()` or `bpy.ops.wm.append()` under the hood. Returns what was loaded and where.
5. **Skills documentation** — Add `asset_browser.md` to skills directory documenting the 4 tools, catalog path conventions, asset type enum values, and loading behavior per type.
6. **Register tools in hybrid domain system** — Add `"asset_browser"` to `_DOMAIN_SKILL_MAP` and `_detect_domain_from_scene()` so the tools auto-load when asset libraries are configured.

**Relevant files**
- `mcp/blmcp/tools/get_asset_libraries.py` + `get_asset_libraries_toolcode.py` — NEW
- `mcp/blmcp/tools/list_assets_in_catalog.py` + `list_assets_in_catalog_toolcode.py` — NEW
- `mcp/blmcp/tools/search_assets.py` + `search_assets_toolcode.py` — NEW
- `mcp/blmcp/tools/load_asset_in_context.py` + `load_asset_in_context_toolcode.py` — NEW
- `addon/bfa_coworker/skills/asset_browser.md` — NEW
- `addon/bfa_coworker/agent_controller.py` — add `asset_browser` to domain map and scene detection

**Verification**
1. `get_asset_libraries` returns configured libraries with counts
2. `list_assets_in_catalog` returns assets for a known catalog
3. `search_assets` with query "wood" returns matching assets
4. `load_asset_in_context` loads a material asset onto active object
5. `load_asset_in_context` loads a node group into active node editor
6. `load_asset_in_context` loads a collection/mesh/world asset correctly
7. Agent can discover and use all 4 asset browser tools when libraries exist

---

## Phase 5: UX Refinement
*Largest phase. Sub-phases can run in parallel with each other but depend on Phase 1-4 being complete for integration testing.*

### 5a: Full Message Queue
1. **Add `MessageQueue` dataclass** — New class in `agent_controller.py` holding: `messages: list[dict]` (each with `id`, `text`, `timestamp`, `status`), `current_id: str | None`, `lock: threading.Lock`.
2. **Queue UI panel** — New collapsible section in chat panel: "Queue (N)" with list of queued messages. Each row shows truncated text + cancel (X) button. Drag-to-reorder via up/down arrow buttons (Blender doesn't support drag-reorder natively).
3. **Queue operators** — `BFACW_OT_queue_message` (add to queue), `BFACW_OT_cancel_queued` (remove by ID), `BFACW_OT_reorder_queue` (move up/down). Send button adds to queue instead of directly executing when `turn_active` is True.
4. **Auto-dequeue** — When a turn completes, `run_conversation_turn()` checks the queue and auto-starts the next message. Queue panel updates in real-time via the existing 0.5s timer.
5. **Queue persistence** — Queue survives Blender session via `WindowManager` PointerProperty (not disk persistence — queue is ephemeral).

### 5b: Mention System Overhaul

**Technical feasibility of inline `@` trigger:**
Blender's `StringProperty` in a panel layout doesn't fire per-keystroke callbacks, so true inline "type `@` and a dropdown appears" like Discord/Slack isn't directly possible. Two viable approaches:

- **Approach A — Timer-polling (recommended for Tier 3c):** A fast timer (~0.15s) watches `chat_input` for `@` followed by partial text. When detected, it opens a `wm.popup_menu` with filtered results near the input field. This is ~100 LOC and reliable. The popup shows items matching the partial text after `@`; typing more characters in the input field refines the filter on the next timer tick.
- **Approach B — Modal operator takeover (defer to Tier 4):** When `@` is detected, a modal operator captures all subsequent keystrokes for filtering, then commits the selection back to `chat_input`. This feels more native but is significantly more complex (~300+ LOC) and has edge cases with Blender's keymap conflicts.

**Implementation (Approach A):**

1. **Timer-based `@` detection** — Add to `chat_timer_update()` (already runs every 0.5s; increase to 0.15s when `chat_input` contains `@`). When `@` is detected in `chat_input`, parse the text after the last `@` as the filter query. Open a `wm.popup_menu` with mentionable items filtered by that query.
2. **Search/filter** — The popup shows items matching the filter in real-time (updated when the popup is re-opened on the next timer tick). Default shows all mentionable items sorted alphabetically by category then name.
3. **Keep the button as fallback** — The "@ Mention" button remains as a secondary entry point. Clicking it opens the same popup but with no filter (shows all items). This also serves as a fallback if the timer approach has edge cases.
4. **Add collections + materials + textures support** — Extend mentionable items to four categories, each with an icon prefix:
   - 🔷 Objects (current)
   - 📁 Collections (NEW)
   - 🎨 Materials (NEW)
   - 🖼️ Textures/Images (NEW)
5. **Smart mention resolution** — Before sending to LLM, resolve `@Name` references via a `_resolve_mentions()` function. For each `@Name` in the message, look up the datablock, determine its type, and append a context hint to the system message. E.g., `@MyCollection` → appends `"Note: @MyCollection refers to collection 'MyCollection' in the current scene, containing 12 objects."`
6. **Scene filtering** — Only show items from `bpy.context.scene` (or all scenes with a toggle in the popup header). Cap raised from 50 to 200 — with search, the cap is rarely hit.

**Autosave safety** — `wm.popup_menu` blocks the event loop while open, which pauses Blender's autosave timer. This is a known limitation of all Blender modal popups. Mitigations:
- **Short exposure window**: The popup only opens when the timer detects `@` followed by text. It closes immediately when the user picks an item or clicks away. Typical exposure is 2-5 seconds.
- **Auto-dismiss guard**: The timer also checks whether the `@` has been removed from `chat_input` (user deleted it). If the `@` is gone, any open popup is dismissed on the next tick. This prevents "stuck" popups.
- **Risk assessment**: Blender autosave fires every 2 minutes by default. The probability of the popup blocking autosave at the exact moment it would fire is very low (~2-5 seconds out of 120 seconds = ~2-4% per mention use). For auto-save safety, the user would need to leave the popup open for the full 2-minute window, which requires intentionally not dismissing it.
- **Tier 4 improvement**: Approach B (modal operator) would have the same issue but worse — a modal operator can stay open indefinitely. If we implement Approach B in Tier 4, we should add an explicit `bpy.ops.wm.save_mainfile()` call inside the modal's `modal()` loop, triggered by a timer that respects the user's autosave interval preference.

**Relevant files**
- `addon/bfa_coworker/ui_chat.py` — `BFACW_OT_mention_search` (rewrite with filter support, categories), `chat_timer_update()` (add @ detection), `BFACW_OT_mention_insert` (update for multi-category)
- `addon/bfa_coworker/agent_controller.py` — new `_resolve_mentions()` function

### 5c: Start/Stop UX Hardening
1. **Graceful LLM shutdown** — Add timeout + force-kill to `stop_local_llama()`. Show "Stopping LLM..." status during shutdown. If process doesn't exit within 10s, force kill and show warning.
2. **Health status indicators** — Add colored dots (🟢/🟡/🔴) next to each component in the chat panel status area: Bridge, MCP, LLM. Update via the 0.5s timer by checking port availability and process liveness.
3. **Restart button** — Add "Restart Coworker" button that calls Stop → waits for all components to exit → calls Start. Show progress: "Stopping... → Starting bridge... → Starting LLM... → Ready."
4. **Stop-during-thinking guard** — When user clicks Stop while `is_thinking`, immediately call `request_stop()` (sets stop event), show "Stopping after current thought..." status, then proceed with full shutdown once the turn exits.
5. **Start-while-stopping guard** — Add `_shutting_down` flag to `AgentState`. Start button checks this and shows "Please wait, shutting down..." if True.

### 5e: Conditional Advanced Settings (per-mode)
*Currently the Advanced tab shows ALL settings — harness config, ports, skills, etc. — regardless of mode. This is noisy. Make sections conditionally visible based on `operating_mode`.*

**Rule table:**

| Section | Local LLM | Remote API | External Harness |
|---|---|---|---|
| Bridge Server | ✅ | ✅ | ✅ |
| MCP Server (Harness) | ❌ | ❌ | ✅ |
| Agent Control / Diagnostics | ✅ | ✅ | ✅ (N/A-aware) |
| Port Settings | ✅ | ✅ | ❌ (not user-configurable in harness mode) |
| Skills | ✅ | ✅ | ❌ (no LLM to inject skills into) |
| Custom Skills | ✅ | ✅ | ❌ |
| Text Editor Memory Bank | ✅ | ✅ | ❌ (no code execution in harness) |

**Implementation:**
1. **Gate each section** — In `_draw_tab_advanced()`, wrap each `layout.box()` section in an `if` check based on `self.operating_mode`. Use the rule table above.
2. **Collapse hidden sections** — Don't just hide with `return`; use `if operating_mode != "EXTERNAL_HARNESS":` to skip drawing the section entirely. This keeps the UI clean.
3. **Harness-specific sections** — The MCP Server (Harness) section should only draw when `operating_mode == "EXTERNAL_HARNESS"`. The Bridge Server and Port Settings are always visible (harness needs bridge port info too).
4. **Add mode hint** — At the top of the Advanced tab, add a label showing which mode is active, e.g., `"Currently in: Local LLM mode — some settings are hidden"` with `icon='INFO'`. This prevents confusion when settings disappear after switching modes.

**Relevant files**
- `addon/bfa_coworker/preferences.py` — `_draw_tab_advanced()` (add per-section mode gating, add mode hint label)

### 5d: Thinking Indicator Polish
1. **Spinner widget** — Replace text dots with a Unicode spinner character that cycles: `◐ ◓ ◑ ◒`. More visible than dots.
2. **Progress bar for model loading** — During `start_local_llama()`, report loading progress via `_state.llm_progress` (0.0-1.0). Draw a thin progress bar in the chat panel status area.
3. **Optimize timer** — Only run `chat_timer_update` when `is_thinking` or queue is non-empty. Register/unregister the timer dynamically instead of running unconditionally every 0.5s.

**Relevant files**
- `addon/bfa_coworker/agent_controller.py` — `MessageQueue` dataclass, queue methods, `_resolve_mentions()`, `_shutting_down` flag, `llm_progress` field, timer optimization hooks
- `addon/bfa_coworker/ui_chat.py` — Queue UI panel, mention search popup, health dots, restart button, spinner, progress bar, new operators
- `addon/bfa_coworker/llm_manager.py` — `stop_local_llama()` graceful shutdown, loading progress reporting
- `addon/bfa_coworker/shared.py` — new state fields
- `addon/bfa_coworker/preferences.py` — `_draw_tab_advanced()` (mode-gated sections, mode hint label)

**Verification**
1. Send message while thinking → queued, appears in queue panel, auto-starts when turn completes
2. Cancel a queued message → removed from queue
3. Type `@` in chat → search popup appears, type "col" → collections filtered, select one → `@CollectionName` inserted
4. `@CollectionName` sent → LLM receives resolved context
5. Health dots show correct live/dead state for all 3 components
6. Click Restart → all components cycle down and up, status updates at each stage
7. Click Stop while thinking → "Stopping after current thought..." → clean shutdown
8. Click Start while stopping → "Please wait, shutting down..."
9. Spinner visible during thinking, progress bar during model load
10. Timer only fires when needed (check console for reduced tick frequency when idle)
11. **Switch to Local LLM mode → Advanced tab shows: Bridge, Agent Control, Ports, Skills, Custom Skills, Memory Bank. No Harness section.**
12. **Switch to Remote API mode → Advanced tab shows: same as Local LLM. No Harness section.**
13. **Switch to External Harness mode → Advanced tab shows: Bridge, Harness section (with full 4-step wizard), Agent Control, Ports. No Skills, Custom Skills, or Memory Bank.**

---

## Phase 6: Collection Color Tag Tool
*No dependencies, can run in parallel with Phase 4-5.*

**Steps**
1. **`set_collection_color_tag` tool** — New tool. Parameters: `collection_name` (str), `color` (enum: NONE, COLOR_01–COLOR_08). Uses `bpy.data.collections[name].color_tag = color`. Wraps in try/except with clear error messages. Registered in the `scene` domain.
2. **Expose `color_tag` in `get_objects_summary`** — Add `"color_tag": col.color_tag` to the collection dict in the summary output. The LLM can now see collection colors without a separate call.
3. **Skills documentation** — Add collection color tag section to `best_practices.md` or a new `collections.md` skill file. Document the enum values, the API pattern, and the depsgraph caveat (safe to use, the addon handles the depsgraph sync).
4. **Add `color_tag` to read-only detection** — Update `_code_is_readonly()` to NOT flag `color_tag` as a mutation when it appears in a read context (e.g., `if col.color_tag == 'COLOR_01'`). Currently it's always treated as mutation.

**Relevant files**
- `mcp/blmcp/tools/set_collection_color_tag.py` + `set_collection_color_tag_toolcode.py` — NEW
- `mcp/blmcp/tools/get_objects_summary_toolcode.py` — add `color_tag` to collection output
- `addon/bfa_coworker/skills/best_practices.md` — add collection color tag section
- `addon/bfa_coworker/agent_controller.py` — refine `_code_is_readonly()` for `color_tag` read vs write detection

**Verification**
1. Agent calls `set_collection_color_tag("MyCollection", "COLOR_03")` → collection color changes
2. `get_objects_summary` output includes `"color_tag": "COLOR_03"` for the collection
3. Agent can read `color_tag` without triggering mutation snapshot overhead
4. Invalid color enum → clear error message returned

---

## Phase 7: Release Preparation
*Depends on ALL previous phases being complete and verified.*

**Steps**
1. **Finalize CHANGELOG.md** — Move all `[Unreleased - v1.1.37]` entries to a new `[v1.1.37]` section with release date. Add entries for all Tier 3c work. Verify the "Copy Content Button" entry — either implement it or remove the claim.
2. **Update wiki documentation** — Use the `bfa-coworker-wiki-docs` skill to regenerate wiki pages. Key pages to update: Home (feature list), Getting Started (model presets, GPU backend), Chat Interface (mention system, queue, session export), Tools Reference (new asset browser tools, collection color tag).
3. **Take screenshots** — Use the `bfa-coworker-screenshots` skill. Required shots: new mention search popup, message queue panel, health status dots, benchmark results UI, asset browser tools in action, session log export.
4. **Update version numbers** — Bump version in `addon/bfa_coworker/blender_manifest.toml`, `addon/pyproject.toml`, `mcp/pyproject.toml`, `chat_client/pyproject.toml`.
5. **Build addon** — Run `python build_addon.py` to produce the release zip. Verify the zip contains all new files.
6. **Smoke test** — Fresh install of the built zip on a clean Blender session:
   - Install addon → preferences load with defaults
   - Download a model (or use existing) → start coworker → send a chat message → agent responds
   - Test mention system → test queue → test session export
   - Test asset browser tools → test collection color tag
   - Run one benchmark suite → results display
   - Stop coworker → clean shutdown
7. **Update repo memory** — Update `/memories/repo/todo.md` and `/memories/repo/changelog.md` with Tier 3c completion status.

**Relevant files**
- `CHANGELOG.md` — finalize v1.1.37
- `addon/bfa_coworker/blender_manifest.toml` — version bump
- `addon/pyproject.toml` — version bump
- `mcp/pyproject.toml` — version bump
- `chat_client/pyproject.toml` — version bump
- `build_addon.py` — verify build
- Wiki pages (via bfa-coworker-wiki-docs skill)
- Screenshots (via bfa-coworker-screenshots skill)

**Verification**
1. CHANGELOG is complete and accurate for v1.1.37
2. Wiki pages reflect all new features
3. Screenshots are current and show new UI
4. Build produces a valid zip
5. Smoke test passes all checks
6. Repo memory files updated

---

## Dependency Graph

```
Phase 1 (Debug Toggle) ─────────────────────────────────────────┐
     │                                                          │
Phase 2 (Benchmarks) ──┐                                        │
                        │                                        │
Phase 3 (Session Log) ─┤─ All parallel ──► Phase 7 (Release)   │
                        │                                        │
Phase 4 (Asset Tools) ──┤                                        │
                        │                                        │
Phase 5 (UX Refinement) ┘                                        │
     │                                                          │
Phase 6 (Color Tag) ────────────────────────────────────────────┘
```

Phases 1-6 can all run in parallel (different files, no code conflicts). Phase 7 is the integration gate.

---

## Decisions

- **Asset browser scope**: Lightweight — 4 tools (~300 LOC): `get_asset_libraries`, `list_assets_in_catalog`, `search_assets`, `load_asset_in_context`. Full 9-tool suite stays in Tier 6.
- **Message queue**: Full multi-message queue with list, cancel, reorder. Queue is ephemeral (session-only, not disk-persisted).
- **Benchmarks**: Timing only (no pass/fail — user judges visually). 6 new editor-specific suites + split assets_materials into assets_browser + polyhaven. Auto-reset on last step completion. Manual click-through, not headless CI.
- **Mention inline `@` trigger**: Timer-polling approach (Approach A) — fast timer watches `chat_input` for `@`, opens filtered popup. ~100 LOC. The "@ Mention" button stays as fallback. Modal operator approach deferred to Tier 4.
- **Release**: Docs + screenshots + changelog + build + smoke test. No GitHub release tag (per user selection).
- **Copy Content Button**: CHANGELOG claims it exists but code search found no implementation. Phase 3 adds "Copy Session Log" which supersedes it.
- **Polyhaven**: Already implemented (plan_polyhaven_pbr_improvement.md marked "Implemented"). User is testing it — no Tier 3c work needed beyond smoke test verification and the new `polyhaven` benchmark suite.

## Further Considerations

1. **The mention system overhaul (5b) is the riskiest sub-phase** — the timer-polling approach for inline `@` detection is reliable but has a 0.15s latency between typing and popup appearing. If this feels too sluggish, the timer interval can be reduced to 0.1s. The "@ Mention" button remains as a zero-latency fallback. The modal operator approach (Approach B) is deferred to Tier 4.
2. **The message queue (5a) adds significant UI complexity** — consider whether the queue panel should be in the sidebar or a floating popup. Sidebar is simpler but takes space. Floating popup is more "chat-like" but harder to implement in Blender.
3. **`load_asset_in_context` (Phase 4) is the most technically complex new tool** — Blender's asset API (`bpy.ops.asset.*`) is operator-based and context-sensitive. The tool needs to handle: no active object (create one), no node editor open (open one), asset type mismatch (clear error). The `bpy.ops.wm.append()` fallback path is more reliable but loses asset metadata. Recommend implementing the append path first, then adding `bpy.ops.asset.*` integration as a refinement.