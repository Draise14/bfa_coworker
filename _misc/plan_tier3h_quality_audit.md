# BFA Coworker — Tier 3h: Quality Audit & Pre-Launch Review

**Date**: 2026-09-01
**Status**: ✅ Audit Complete — Fix Phase Pending
**Depends on**: All Tier 3 work (3a–3g)
**References**: Issues [#50](https://github.com/Draise14/bfa_coworker/issues/50), [#54](https://github.com/Draise14/bfa_coworker/issues/54), [#57](https://github.com/Draise14/bfa_coworker/issues/57); plans tier3d, tier3e, tier3f, tier3g

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Critical Bugs — Must Fix Before Launch](#2-critical-bugs--must-fix-before-launch)
3. [High-Priority Issues](#3-high-priority-issues)
4. [Orchestration Layer Deep-Dive](#4-orchestration-layer-deep-dive)
5. [Orchestration → Tier 4 Bridge Analysis](#5-orchestration--tier-4-bridge-analysis)
6. [Security Assessment & Tier 4 Recommendations](#6-security-assessment--tier-4-recommendations)
7. [Agent Controller Assessment](#7-agent-controller-assessment)
8. [MCP Tools Assessment](#8-mcp-tools-assessment)
9. [Chat UI Assessment](#9-chat-ui-assessment)
10. [LLM Manager Assessment](#10-llm-manager-assessment)
11. [Issue Resolution Status](#11-issue-resolution-status)
12. [Launch Readiness Verdict](#12-launch-readiness-verdict)
13. [Fix Phase Plan](#13-fix-phase-plan)

---

## 1. Executive Summary

A comprehensive audit of all accumulated Tier 3 code across 4 sub-systems (orchestration, agent controller, MCP tools, chat UI) was conducted on 2026-09-01. The audit examined ~15,000 lines of code across ~40 files, verified against 4 implementation plans (tier3d, tier3e, tier3f, tier3g), and cross-referenced 3 GitHub issues (#50, #54, #57).

### Bottom Line

**The system is functional and launch-ready after fixing 3 critical bugs.** The local LLM chat works, remote API works, external harness works. The orchestration layer (templates, plans, auto-fix) is a proof-of-concept — not the full intent-driven architecture described in plan_tier3g — but the fallback to raw `execute_blender_code` works reliably. The full orchestration vision is a Tier 4 effort.

### Key Numbers

| Metric | Value |
|--------|-------|
| Total files audited | ~40 |
| Critical bugs found | 3 |
| High-priority issues | 5 |
| Medium/low issues | ~15 |
| Tests passing | ~273 (all green) |
| Tools with zero test coverage | 15 |
| Templates implemented | 18 of 135 planned |
| Auto-fix rules (dead code) | 12 |
| Issues resolved | 2 of 3 (#50 ✅, #54 ✅, #57 🟡) |

---

## 2. Critical Bugs — Must Fix Before Launch

### 2.1 `execute_blender_plan` — Import Path Broken

**Severity**: 🔴 CRITICAL
**File**: `mcp/blmcp/tools/execute_blender_code.py` lines 84–88
**Impact**: The `execute_blender_plan` MCP tool always fails. The LLM is told this tool exists but it can never succeed.

**Root cause**: The tool tries to import from the wrong module:
```python
from bfa_coworker.mcp_to_blender_server import _plan_to_code, _render_template, _TEMPLATES
```
These symbols live in `bfa_coworker.blender_templates`, **not** `mcp_to_blender_server`. Confirmed by grep: `mcp_to_blender_server.py` has zero references to `blender_templates`, `_plan_to_code`, `_render_template`, or `_TEMPLATES`.

**Compounding bug**: The `except ImportError` fallback calls `_generate_plan_code(steps)` — a function that is **never defined anywhere** in the codebase. This would raise `NameError` at runtime.

**Fix**: Change the import to:
```python
from bfa_coworker.blender_templates import _plan_to_code, _render_template, _TEMPLATES
```
Remove the dead `_generate_plan_code` fallback.

### 2.2 `list_blender_templates` — Same Import Bug

**Severity**: 🔴 CRITICAL
**File**: `mcp/blmcp/tools/execute_blender_code.py` line 113
**Impact**: The `list_blender_templates` MCP tool always returns `{"status": "error", "message": "Template registry not available"}`. The LLM cannot discover available templates.

**Root cause**: Same wrong import path:
```python
from bfa_coworker.mcp_to_blender_server import _TEMPLATES, _TEMPLATE_DEFAULTS
```

**Fix**: Change to:
```python
from bfa_coworker.blender_templates import _TEMPLATES, _TEMPLATE_DEFAULTS
```

### 2.3 `autofix.py` — Dead Code (Never Wired In)

**Severity**: 🟡 HIGH
**File**: `addon/bfa_coworker/autofix.py` (23 lines, 12 rules)
**Impact**: The 12 auto-correction rules exist but are **never imported or called** anywhere in the entire codebase. Confirmed by grep across all `.py` files: zero results for `from.*autofix|import.*autofix`.

The rules are correct and useful — they fix common LLM mistakes (lamps→lights, EEVEE→BLENDER_EEVEE, subdivisions→levels, etc.) — but they're completely disconnected from the execution pipeline. The preflight checker detects these same errors but **rejects** the code instead of fixing it.

**Fix**: Wire `_autofix_code()` into `mcp_to_blender_server._execute_code()`, applied **before** preflight. This way, auto-fixed code passes preflight instead of being rejected. The function already returns `(fixed_code, fixes_applied)` — log the fixes for debugging.

---

## 3. High-Priority Issues

### 3.1 `_shutting_down` Set on Wrong Class

**File**: `addon/bfa_coworker/llm_manager.py` lines 2916, 2970
**Impact**: Low (Python dynamically adds the attribute — no crash). But the flag is never read anywhere in `llm_manager.py`. The actual `_shutting_down` flag lives on `AgentState` in `agent_controller.py`.

**Fix**: Either add `_shutting_down: bool = False` to `LLMState` or remove the dead assignments.

### 3.2 `_DEEP_MAX_TOKENS = 4096` — Dead Code

**File**: `addon/bfa_coworker/agent_controller.py` line 75
**Impact**: None (defined but never referenced). Clean up.

### 3.3 `get_polyhaven_status` Returns `str` Instead of `dict`

**File**: `mcp/blmcp/tools/get_polyhaven_status.py`
**Impact**: Inconsistent API — all other tools return `dict[str, object]`. The LLM may misinterpret the response format.

### 3.4 `_bundled_dll_handle` May Be Garbage-Collected

**File**: `addon/bfa_coworker/llm_manager.py` (in `start_local_llama()`)
**Impact**: The handle from `os.add_dll_directory()` is stored in a local variable that goes out of scope. Python may GC it, unloading the DLL directory and causing `DLL_NOT_FOUND` errors mid-session.

**Fix**: Store the handle in a module-level variable or on `LLMState`.

### 3.5 No Tool Call Timeout

**File**: `addon/bfa_coworker/agent_controller.py` (`_call_mcp_tool_sync`)
**Impact**: If an MCP tool hangs (infinite loop in user code), there's no kill mechanism. The HTTP timeout is 60s but the bridge server could hang indefinitely.

---

## 4. Orchestration Layer Deep-Dive

### 4.1 What plan_tier3g Promised

The plan described a sophisticated 4-layer architecture:

```
Layer 4: Operator UI Layer — One-click contextual actions
Layer 3: Orchestrator Engine — Intent → Plan → Template Chain → Code → Execute
Layer 2: Template + Auto-fix + Preflight — 135+ tested code blocks
Layer 1: Context Gathering — Editor-aware tool scoping, resource injection
```

With:
- **135 templates** across all Blender editors (VIEW_3D, NODE_EDITOR, DOPESHEET, SEQUENCE_EDITOR, etc.)
- **Template chains** (e.g., stonehenge = [create_torus × N, arrange_in_circle, add_material × N])
- **Intent classifier** — keyword-based routing before the LLM is called
- **Plan validator** — template existence, mode compatibility, parameter validation, selection check, data-block conflict
- **3 complexity tiers** — T1 (atomic, 1-2 ops), T2 (contextual, 3-5 ops), T3 (scene-level, 6+ ops)
- **Plugin architecture** — `plugins/` directory with `CoworkerPlugin` base class
- **Editor-aware tool scoping** — reading `bpy.context.area.type` and `context.mode`
- **5-level fallback ladder** — template chain → single template → raw code → doc search → clarify

### 4.2 What Was Actually Built

| Component | Planned | Built | Status |
|-----------|---------|-------|--------|
| Templates | 135 across all editors | 18 (all VIEW_3D basics) | 🟡 Minimal |
| Template metadata | tier, editor, mode, creates_datablocks, is_destructive, chainable | None | ❌ Missing |
| Template chains | Curated multi-step sequences | None | ❌ Missing |
| Intent classifier | Keyword-based routing | None | ❌ Missing |
| Plan validator | 5 validation checks | None | ❌ Missing |
| Complexity tiers | T1/T2/T3 | None | ❌ Missing |
| Plugin architecture | `plugins/` directory | None | ❌ Missing |
| Editor-aware scoping | `bpy.context.area.type` | Domain system (keyword-based) | 🟡 Approximation |
| Fallback ladder | 5 levels | 2 levels (plan→code, spiral detection) | 🟡 Partial |
| Auto-fix rules | 12 rules, wired in | 12 rules, dead code | 🔴 Broken |
| `execute_blender_plan` | Working MCP tool | Broken (import path) | 🔴 Broken |
| `list_blender_templates` | Working MCP tool | Broken (import path) | 🔴 Broken |

### 4.3 What Actually Works for Orchestration

The system has **two functional paths** for the LLM to execute code:

1. **`execute_blender_code`** — Raw Python code. Goes through preflight (27 checks) + weak sandbox. Works reliably. This is the primary path the LLM uses.

2. **Domain system** — Keyword + scene-content-based tool filtering. Reduces the tool list from 30+ to 5-8 per domain. This is the main "orchestration" that actually helps local models — fewer choices = better decisions.

The domain system is the **unsung hero** of the orchestration layer. It doesn't match the plan's vision of intent→plan→template chains, but it achieves the same goal (helping local models make better tool choices) through a different mechanism.

### 4.4 Verdict

The orchestration layer is a **proof-of-concept**, not the full system described in plan_tier3g. However, the system is functional because:

- The domain system provides effective tool filtering for local models
- `execute_blender_code` with preflight + sandbox is a reliable fallback
- Spiral detection catches error loops quickly (threshold 2)
- Entity tracking prevents duplicate creation
- The 18 templates that DO work cover the most common operations (primitives, modifiers, materials, basic animation)

The full orchestration vision (135 templates, chains, classifier, validator, plugins) is a **Tier 4 effort** — see Section 5.

---

## 5. Orchestration → Tier 4 Bridge Analysis

### 5.1 How the Orchestration Gap Ties Into Tier 4

The Tier 4 plans (4 sub-plans + master coordination) focus on **editor integration and UX**:

| Tier 4 Sub-Plan | Focus | Orchestration Dependency |
|-----------------|-------|--------------------------|
| **Tier 4** — Editor Integration | Coworker workspace, viewport overlays, GPU/panel hybrid | Needs per-editor templates to populate contextual panels |
| **Tier 4b** — Competitor UX | Markdown, code blocks, sessions, right-click explain, CHOYA | CHOYA guided buttons need template chains to offer sensible next steps |
| **Tier 4c** — Text Editor IDE Agent | Code gen, fix, edit selection, prompt templates | Text Editor templates (14 planned in tier3g) are the foundation |
| **Tier 4d** — Moodboard Editor | Image canvas, agent context bridge | Needs vision pipeline (already built in Tier 3) |

**The orchestration gap directly blocks Tier 4c and partially blocks Tier 4b:**

- **Tier 4c (Text Editor)** needs the 14 Text Editor templates from plan_tier3g (addon skeleton, operator template, panel template, modal operator, property group, menu template, bmesh operation, node tree setup, import/export script, keymap addon, handler template, gizmo template, render script, register current). Without these, the Text Editor agent can only generate raw code — no structured templates.

- **Tier 4b (CHOYA)** needs template chains to offer sensible next-step buttons. "Add Modifier" after creating an object requires knowing which modifiers are available and how to apply them. The current 18 templates cover this partially (add_subsurf, add_array, add_bevel, add_solidify, add_smooth, add_remesh).

- **Tier 4 (Editor Integration)** needs per-editor template registries to populate contextual panels. The plan's "Mode 2: Contextual Agent Sidebar" requires knowing which templates are valid for the current editor/mode.

### 5.2 What Tier 4 Plans Already Cover

The Tier 4 plans are **well-structured** and account for the current state:

- **`plan_tier4_master_coordination.md`** correctly identifies shared systems needed (CHOYA, ui_components.py, translation, macros).
- **`plan_tier4c_text_editor_ide_agent.md`** has been revised to remove hotkey conflicts and focus on sidebar/context-menu access — good.
- **`plan_tier4b_competitor_ux_analysis.md`** has a thorough competitor analysis and correctly identifies markdown rendering as already done in Tier 3f.
- **`plan_tier4_editor_integration.md`** has the breakthrough discovery about USERPREF center panels — this is the right architecture.

### 5.3 What Tier 4 Plans Are Missing (Orchestration-Related)

| Gap | Why It Matters | Recommendation |
|-----|---------------|----------------|
| **No template expansion plan** | Tier 4c needs 14 Text Editor templates. Tier 4 needs per-editor templates for contextual panels. | Add a "Template Expansion" phase to Tier 4 master coordination — expand from 18 to ~50 templates covering the editors Tier 4 touches (Text Editor, Node Editor, VIEW_3D). |
| **No template metadata retrofit** | CHOYA needs to know which templates are chainable, which create datablocks, which are destructive. | Add metadata fields to existing templates as part of the shared `ui_components.py` work. |
| **No intent classifier plan** | CHOYA guided buttons need to know what the user might want next based on what just happened. | The CHOYA option generation logic (`choya.py`) is essentially an intent classifier — make it template-aware. |
| **No plan validator plan** | When CHOYA offers "Add Modifier → Subdivision Surface", the system should validate that the object supports modifiers. | Add validation to CHOYA option generation — don't offer invalid next steps. |

### 5.4 Recommended Tier 4 Scope Addition

Add a **Phase 0: Orchestration Foundation** to the Tier 4 master coordination plan (~400 LOC):

1. **Template metadata retrofit** (~100 LOC) — Add `tier`, `editor`, `mode`, `creates_datablocks`, `is_destructive`, `chainable` fields to all 18 existing templates.
2. **Template expansion** (~200 LOC) — Add the 14 Text Editor templates from plan_tier3g + 10 Node Editor templates + 8 Outliner templates = ~32 new templates.
3. **Template chain system** (~100 LOC) — Add `TEMPLATE_CHAINS` dict with 5-10 curated chains (stonehenge, cinematic_look, three_point_lighting, clean_mesh_for_print, organize_scene).

This gives Tier 4c the templates it needs, gives CHOYA the chain awareness it needs, and brings the total to ~50 templates — enough to be useful without the full 135.

---

## 6. Security Assessment & Tier 4 Recommendations

### 6.1 Current Security Posture

| Layer | What It Does | Verdict |
|-------|-------------|---------|
| **Weak sandbox** | Blocks `sys.exit()` + 4 dangerous Blender operators (`wm.quit_blender`, `wm.read_factory_settings`, `wm.read_factory_userpref`, `wm.read_userpref`) | ✅ Always active during code execution |
| **Preflight** | 27 regex patterns catch common dangerous patterns before execution | ✅ Runs before every LLM-generated code execution |
| **Thread isolation** | LLM-generated code runs in daemon thread with 30s timeout | ✅ Prevents main-thread hangs |
| **Toolcode trust** | Repository-controlled toolcode runs on main thread (needed for `bpy.context`) but carries `# blmcp-toolcode-skip-preflight` marker | ✅ Trusted code is reviewed |
| **API key storage** | `StringProperty(subtype='PASSWORD')` — masked in UI | ✅ Standard Blender practice |
| **No `[Run]` button** | Code blocks in chat have `[Copy]` only — no `[Run]` | ✅ Prevents accidental re-execution |

### 6.2 What's NOT Protected

| Risk | Severity | Likelihood |
|------|----------|------------|
| **Filesystem access** — LLM code can read/write any file the Blender process can | 🟡 Medium | Low (LLMs don't typically try to delete files) |
| **Network access** — LLM code can make HTTP requests | 🟡 Medium | Low (LLMs don't typically exfiltrate data) |
| **Unrestricted imports** — `import os`, `import subprocess`, etc. are available | 🟡 Medium | Low (preflight catches hallucinated modules) |
| **Resource exhaustion** — No memory/CPU limits on LLM code | 🟢 Low | Low (30s timeout limits damage) |
| **Malicious model** — A compromised GGUF could inject harmful code | 🟢 Low | Very Low (user provides their own model) |

### 6.3 Security Verdict for v1 Launch

**Appropriate for a local-first tool.** The weak sandbox + preflight prevents the most dangerous operations. The user is running their own model on their own machine — the threat model is "LLM makes mistakes," not "LLM is actively malicious." The 30s timeout + daemon thread isolation prevents the most common failure mode (infinite loops).

### 6.4 Tier 4 Security Recommendations

As the addon gains more power (Text Editor code generation, moodboard file operations, CHOYA automated actions), the attack surface grows. Recommended Tier 4 security additions:

| Priority | Addition | LOC | Rationale |
|----------|----------|-----|-----------|
| 🔴 P0 | **`[Run]` button with confirmation dialog** | ~50 | Tier 4c will generate code into the Text Editor. Users will want to run it. Add a "Run" button that shows a confirmation dialog listing what the code will do (parse top-level `bpy.ops.*` calls). |
| 🔴 P0 | **File operation audit in preflight** | ~30 | Add preflight patterns for `open()`, `os.remove()`, `shutil.rmtree()` — warn (don't block) when LLM code tries filesystem operations outside the blend file directory. |
| 🟡 P1 | **Import whitelist** | ~40 | Restrict LLM-generated code to a whitelist of safe imports (`bpy`, `bmesh`, `math`, `mathutils`, `random`, `json`, `os.path` only). Block `os.system`, `subprocess`, `socket`, `requests`, `urllib`. |
| 🟡 P1 | **Operator audit** | ~30 | Log all `bpy.ops.*` calls made by LLM code. Surface in the Workshop panel so users can see exactly what the agent did. |
| 🟢 P2 | **Memory limit** | ~20 | Set `resource.setrlimit(RLIMIT_AS, ...)` before executing LLM code (Unix only — Windows needs a job object). |
| 🟢 P2 | **Dry-run mode** | ~80 | Add a "Preview" button that shows what code would be executed without running it. Parse templates and show the rendered code. |

**Total Tier 4 security additions: ~250 LOC.** These are lightweight, additive, and don't change the existing security model — they add defense-in-depth.

### 6.5 Security Philosophy

The addon's security model should follow the **Swiss Cheese Model**: each layer has holes, but the layers overlap so no single failure is catastrophic.

```
User trusts their own model (outer layer)
  → Weak sandbox blocks dangerous ops
    → Preflight catches common mistakes
      → 30s timeout prevents hangs
        → Daemon thread prevents UI freeze
          → Import whitelist (Tier 4)
            → File operation audit (Tier 4)
              → Operator audit log (Tier 4)
```

No single layer is impenetrable, but together they make it very unlikely that LLM-generated code causes harm.

---

## 7. Agent Controller Assessment

### 7.1 What Works Well

| Feature | Implementation | Verdict |
|---------|---------------|---------|
| **Conversation loop** | Re-entrancy guard, history capping (20 messages), sanitization, auto-continue (2 retries on `finish_reason=length`) | ✅ Robust |
| **Tool calling** | OpenAI-compatible tool loop, `load_tools` meta-tool interception, smart undo on failure, entity tracking, screenshot injection | ✅ Well-designed |
| **Spiral detection** | Threshold 2, 7 specialized corrective messages, auto-export session log, history truncation | ✅ Excellent |
| **Domain system** | 8 domains, keyword + scene-content detection, skill injection, tool filtering | ✅ Good for local models |
| **Entity tracking** | 12 datablock types, duplicate prevention, cleanup code on undo failure | ✅ Well-implemented |
| **Sampling parameters** | Temperature auto-switch (0.2 code / 0.35 prose), top_k=20, top_p=0.8, repeat_penalty=1.1 | ✅ Battle-tested |
| **Remote API** | OpenRouter + 10 curated models, BYOK profiles, model list fetching | ✅ Full support |

### 7.2 Concerns

| Concern | Impact | Recommendation |
|---------|--------|----------------|
| `_DEEP_MAX_TOKENS = 4096` never used | Dead code | Remove or wire into deep-thinking mode |
| No tool call timeout beyond HTTP 60s | Bridge server could hang indefinitely | Add `threading.Timer` watchdog in `_call_mcp_tool_sync` |
| `_build_cleanup_code` silently fails if datablocks have users | Orphan datablocks accumulate | Log cleanup failures, offer manual purge |
| Entity snapshot silently swallows exceptions | Corrupted datablocks give incomplete results | Log snapshot errors at DEBUG level |
| Message queue has no overflow protection | Memory grows unbounded if user queues hundreds of messages | Cap queue at 50 messages |

---

## 8. MCP Tools Assessment

### 8.1 Tool Inventory

**39 tools** across 10 categories. 29 use the bridge's toolcode mechanism; 10 generate code dynamically via `send_code()`.

| Category | Tools | Tested? |
|----------|-------|---------|
| Assets | 7 (search, tags, load, place, libraries, catalogs, jump) | ✅ 64 unit + 13 integration |
| Animation | 1 (batch_keyframe_insert) | ❌ |
| Code Execution | 4 (execute_blender_code, _for_cli, _plan, list_templates) | ⚠️ 2 broken |
| Docs | 3 (python_api, search_api, search_manual) | ✅ |
| Lighting | 1 (three_point_lighting_rig) | ❌ |
| Navigation | 5 (jump_to_*) | ✅ (smoke) |
| Node Trees | 3 (get_active, get_interface, wire) | ✅ |
| Poly Haven | 4 (search, download, status, setup_pbr) | ❌ (4 untested) |
| Rendering | 2 (render_thumbnail, render_viewport) | ❌ (unit) |
| Scene Info | 7 (summaries, history) | ✅ (smoke) |
| Other | 2 (assign_material, set_collection_color) | ❌ |

### 8.2 Test Coverage Gaps (15 tools with zero dedicated tests)

`batch_keyframe_insert`, `three_point_lighting_rig`, `setup_pbr_material`, `download_polyhaven_asset`, `get_operation_history`, `get_polyhaven_status`, `search_polyhaven_assets`, `assign_material_to_objects`, `set_collection_color_tag`, `place_asset_in_scene`, `jump_to_asset_browser`, `render_thumbnail_to_path`, `render_viewport_to_path`, `execute_blender_plan`, `list_blender_templates`

### 8.3 Preflight Validation

27 regex patterns, 43 unit tests. Catches: missing imports, wrong attribute names, hallucinated modules, mode mismatches, bmesh errors, vector arithmetic errors, deprecated APIs. **Well-implemented.**

---

## 9. Chat UI Assessment

### 9.1 What Works Well

- **Markdown rendering**: Code blocks with `[Copy]`, pipe tables, headings (H1-H6), lists, blockquotes, horizontal rules, LaTeX→Unicode. Auto-close trailing fences.
- **Turn grouping**: `turn_start` flag with backward compat. Workshop collapsible panel.
- **Chat persistence**: JSON files with versioned copies (10 retained), thread-safe saves.
- **@Mention system**: 6 categories, auto-open popup.
- **Project rules**: Global + per-blend-file markdown rules.
- **Harness mode**: Clean separation — bridge controls only.

### 9.2 Limitations

- **No bold/italic**: Blender UI limitation — all inline formatting stripped. Not fixable.
- **No `[Run]` button**: Only `[Copy]`. Deferred to Tier 4b for security review.
- **Conclusion markdown inconsistency**: When a turn has no user message, conclusion renders as plain text.
- **`_WRAP_WIDTH = 60`** hardcoded.

---

## 10. LLM Manager Assessment

### 10.1 What Works Well

- **9 curated presets** across 3 VRAM tiers + 10 remote provider models
- **Download system**: SHA-256, HTTP Range resume, disk space preflight, cancel, progress with ETA
- **GPU auto-detection**: CUDA/Vulkan/CPU, GGUF layer count parser, proportional GPU layer calc
- **Server lifecycle**: Port fallback (10 ports), health polling with exponential backoff, crash diagnostics with log tail
- **Vision/mmproj**: Per-model projector naming, cross-contamination prevention
- **Smart local detection**: Scans models dir + HF cache, auto-selects downloaded variants

### 10.2 Concerns

| Concern | Impact |
|---------|--------|
| `_shutting_down` on wrong class | Harmless (Python adds dynamically) but dead code |
| `download_model()` returns `None` — async, docstring unclear | Callers must poll state |
| `_check_llama_version()` has unreachable code | Copy-paste artifact |
| `_hardware_info()` and `_detect_hardware_cached()` duplicate logic | Maintenance burden |
| `_popen_new_console()` uses raw `ctypes.CreateProcessW` | Fragile on Windows updates |
| No overall download timeout | Server hang = indefinite block |
| `_bundled_dll_handle` may be GC'd | DLL directory could unload |

---

## 11. Issue Resolution Status

### Issue #50 — Tier3d: Asset Browser Intelligence
**Status**: ✅ **RESOLVED**. All phases implemented:
- Phase 1: Asset tools wired into domain system ✅
- Phase 2: Enhanced tools (tag search, GN/compositor loading, positioning) ✅
- Phase 2B: Node-group inspection + wiring (`get_active_node_tree`, `get_node_group_interface`, `wire_node_group`) ✅
- Phase 3: New tools (`place_asset_in_scene`, `jump_to_asset_browser`) ✅
- Phase 4: System prompt + skill updates ✅
- Phase 5: Tests (64 unit + 13 integration) ✅
- Phase A: Headless integration tests + Blender 5.3 compat ✅
- Phase B: In-session self-tests ✅
- Phase C: Asset metadata index ✅

### Issue #54 — Tier 3d: Asset Browser Intelligence (detailed plan)
**Status**: ✅ **RESOLVED**. Same as #50 — the detailed implementation plan was executed.

### Issue #57 — Console catches all Llama server messages
**Status**: 🟡 **IN PROGRESS**. The `_popen_new_console()` with `CreateProcessW` and titled window ("BFA Coworker — llama-server") was implemented. User noted "I think I am nearly done here."

---

## 12. Launch Readiness Verdict

### What's Launch-Ready ✅

- Local LLM chat with 9 curated presets
- Remote API with OpenRouter + BYOK
- External harness (Opencode)
- Download system (SHA-256, resume, cancel)
- GPU auto-detection
- Markdown rendering in chat
- Turn grouping + Workshop panel
- Spiral detection + corrective messages
- Domain system + tool filtering
- Entity tracking
- Asset browser intelligence (13 tools, 77 tests)
- Poly Haven PBR integration
- Preflight validation (27 patterns, 43 tests)
- Weak sandbox
- Deferred tools (renders)
- Chat persistence
- @Mention system
- Project rules
- ~273 tests passing

### What Needs Fix Before Launch 🔴

1. `execute_blender_plan` — broken import path
2. `list_blender_templates` — broken import path
3. `autofix.py` — dead code, needs wiring

### What Can Ship As-Is (Documented Limitations) 🟡

- Template system is minimal (18 of 135 planned)
- No intent classifier, plan validator, template chains, plugin architecture
- Editor-aware scoping is domain-based (not context-based)
- 15 tools lack dedicated tests
- `_shutting_down` on wrong class (harmless)
- `_DEEP_MAX_TOKENS` dead code
- No filesystem/network sandbox (acceptable for local-first v1)

---

## 13. Fix Phase Plan

### Phase A: Critical Bug Fixes (~30 LOC, 2 files) — EST. 30 MINUTES

| Step | File | Change | LOC |
|------|------|--------|-----|
| A1 | `mcp/blmcp/tools/execute_blender_code.py:84` | Fix `execute_blender_plan` import: `bfa_coworker.mcp_to_blender_server` → `bfa_coworker.blender_templates` | ~2 |
| A2 | `mcp/blmcp/tools/execute_blender_code.py:88` | Remove dead `_generate_plan_code` fallback | ~2 |
| A3 | `mcp/blmcp/tools/execute_blender_code.py:113` | Fix `list_blender_templates` import: same change | ~2 |
| A4 | `addon/bfa_coworker/mcp_to_blender_server.py` | Wire `_autofix_code()` into `_execute_code()`, applied before `_preflight_check()` | ~15 |
| A5 | `addon/bfa_coworker/mcp_to_blender_server.py` | Import `autofix` at top of `_execute_code()` | ~1 |

**Verification**:
1. `python tests/tool_smoke_test.py --filter execute_blender_plan` → PASS
2. `python tests/tool_smoke_test.py --filter list_blender_templates` → PASS
3. `python -m pytest tests/test_preflight.py` → 43/43 PASS (auto-fix runs before preflight, so preflight should still catch unfixed issues)
4. Manual: Send "Create 3 torus objects with random materials" in local mode → should use templates or raw code successfully

### Phase B: High-Priority Cleanup (~40 LOC, 3 files) — EST. 30 MINUTES

| Step | File | Change | LOC |
|------|------|--------|-----|
| B1 | `addon/bfa_coworker/llm_manager.py:2916,2970` | Remove dead `_state._shutting_down` assignments (or add field to `LLMState`) | ~5 |
| B2 | `addon/bfa_coworker/agent_controller.py:75` | Remove dead `_DEEP_MAX_TOKENS` | ~1 |
| B3 | `addon/bfa_coworker/llm_manager.py` (in `start_local_llama()`) | Store `_bundled_dll_handle` in module-level variable to prevent GC | ~5 |
| B4 | `mcp/blmcp/tools/get_polyhaven_status.py` | Return `dict` instead of `str` for API consistency | ~10 |
| B5 | `addon/bfa_coworker/agent_controller.py` (`_call_mcp_tool_sync`) | Add `threading.Timer` watchdog (120s) for tool call timeout | ~15 |

### Phase C: Test Coverage (~200 LOC, 3 files) — EST. 2 HOURS

| Step | File | Change | LOC |
|------|------|--------|-----|
| C1 | `tests/tool_smoke_test.py` | Add test args for `execute_blender_plan` and `list_blender_templates` | ~10 |
| C2 | `tests/test_templates.py` (new) | Unit tests for all 18 templates: verify they render valid Python, verify `_plan_to_code()` produces executable code | ~80 |
| C3 | `tests/test_autofix.py` (new) | Unit tests for all 12 auto-fix rules: verify each pattern matches and replaces correctly | ~60 |
| C4 | `tests/tool_smoke_test.py` | Add test args for `batch_keyframe_insert`, `three_point_lighting_rig`, `setup_pbr_material` | ~15 |
| C5 | `tests/test_preflight.py` | Add test: auto-fix applied before preflight → previously-rejected code now passes | ~20 |

### Phase D: Documentation (~50 LOC, 2 files) — EST. 30 MINUTES

| Step | File | Change | LOC |
|------|------|--------|-----|
| D1 | `CHANGELOG.md` | Add "Fixed" entries for the 3 critical bugs | ~20 |
| D2 | `_misc/plan_tier3g_mcp_intent_architecture.md` | Add "Known Limitations" section documenting what was built vs. planned | ~30 |

### Phase E: Optional — Template Metadata Retrofit (~100 LOC, 1 file) — EST. 1 HOUR

If time permits before launch, add metadata to existing templates to prepare for Tier 4:

| Step | File | Change | LOC |
|------|------|--------|-----|
| E1 | `addon/bfa_coworker/blender_templates.py` | Add `_TEMPLATE_META` dict with `tier`, `editor`, `mode`, `creates_datablocks`, `is_destructive`, `chainable` for all 18 templates | ~80 |
| E2 | `addon/bfa_coworker/blender_templates.py` | Add `get_templates_for_editor(editor, mode)` helper | ~20 |

---

## Summary of Fix Phase

| Phase | What | Files | LOC | Time |
|-------|------|-------|-----|------|
| A | Critical bug fixes | 2 | ~30 | 30 min |
| B | High-priority cleanup | 3 | ~40 | 30 min |
| C | Test coverage | 3 | ~200 | 2 hr |
| D | Documentation | 2 | ~50 | 30 min |
| E | Template metadata (optional) | 1 | ~100 | 1 hr |
| **Total** | | **11** | **~420** | **~4.5 hr** |

---

## Decisions

| Decision | Rationale |
|----------|-----------|
| **Fix critical bugs before launch** | `execute_blender_plan` and `list_blender_templates` are advertised to the LLM as available tools. Having them always fail undermines trust in the tool system. |
| **Wire autofix before launch** | The 12 rules are correct and useful. Wiring them in (before preflight) silently fixes common LLM mistakes instead of rejecting the code. This reduces round-trips. |
| **Ship minimal template system as-is** | The full orchestration layer (135 templates, chains, tiers, plugins) is a Tier 4 effort. The current 18 templates + domain system + raw code fallback is functional. |
| **Accept security model for v1** | Local-first tool, user runs their own model. Weak sandbox + preflight is appropriate. Tier 4 adds defense-in-depth (import whitelist, file audit, operator audit). |
| **Document known limitations** | Be honest with users about what the orchestration layer can and can't do. The CHANGELOG should note that templates are minimal and the plan system is basic. |
| **Tier 4 needs a Phase 0** | The orchestration gap directly blocks Tier 4c (Text Editor) and partially blocks Tier 4b (CHOYA). Add ~400 LOC of template expansion + metadata retrofit to Tier 4 master coordination. |

---

## Further Considerations

1. **The domain system is the real orchestration**: The keyword + scene-content-based tool filtering is what actually helps local models. Expanding the domain system (more domains, better keywords, context-aware detection) may be more impactful than building the full template chain architecture.

2. **Template quality over quantity**: 18 well-tested templates that work 100% of the time are better than 135 untested templates that work 80% of the time. Focus Tier 4 template expansion on the editors Tier 4 actually touches (Text Editor, Node Editor, VIEW_3D).

3. **The `_popen_new_console()` fragility**: Using raw `ctypes.CreateProcessW` bypasses Python's subprocess safety layers. If Microsoft changes the console host behavior in a Windows update, this could break. Consider contributing a `CREATE_NEW_CONSOLE` flag upstream to Python's `subprocess` module.

4. **Test coverage for generated-code tools**: Tools like `batch_keyframe_insert`, `three_point_lighting_rig`, `setup_pbr_material`, and `download_polyhaven_asset` generate code dynamically. Testing them requires either mocking the bridge or running against a live Blender. Consider adding integration tests in Tier 4.

5. **The orchestration gap is a feature, not a bug**: The plan_tier3g described a Rolls-Royce orchestration layer. What was built is a reliable Honda Civic. The Honda gets you there. The Rolls-Royce can come in Tier 4-5 when the foundation is solid.