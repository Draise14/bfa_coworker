# Tier 4/5 — Artist automation: domain tools, orchestration, quick tools & wizards

**Status**: 📝 Ideas catalogue (2026-10-20). Picks for the next major release are marked
**R** (release) and go on the task board; the rest are **T5** (Tier 5) candidates.
**Clean room**: our own ideas from artist needs; implement from the bundled API docs and
live probes (`plan_tier4s_skills_cleanroom.md` §2).

## 1. The strategy in four rules

1. **Tool first, model optional.** Day-to-day chores (rename, organize, apply scale,
   prep for export) should be **deterministic tools** that also run *without* a model:
   from a menu, a wizard or a button. The model's job is to pick, parameterize and chain
   them, and to explain the result. That makes them fast on 7–14B models, testable, and
   useful even when no engine is running.
2. **Preview → apply → one undo.** Every tool that changes the scene reports what it
   *would* do first (a dry run), applies on confirm, and lands as one undo step. This is
   what makes automation trustworthy for artists.
3. **One implementation, three doors.** Each capability is one MCP tool (server side, so
   every engine gets it) with three doors: the chat, a **quick menu / wizard** in the
   editor, and the scene co-pilot. No logic lives in a door.
4. **Selection is the scope.** Tools act on the selection (or the active collection, or
   the editor's context) by default, so "do this to these" needs no typing.

## 2. Expanded domain tools

Grouped by the chore they remove. **R** = proposed for the release; size S/M.

### 2.1 Housekeeping — "organize my scene" (**R**, the flagship)
| Tool | What it does | Size |
|---|---|---|
| `scene_audit` | One compact report: unnamed/default-named objects, unapplied scale/rotation, empty collections, orphan data, missing files, duplicated materials (`.001`), objects outside collections, huge poly counts | S |
| `organize_scene(rules)` | Sort objects into collections by type / name prefix / material / custom rule; colour-tag collections; dry run lists the moves | M |
| `batch_rename(pattern)` | Prefix/suffix/numbering/find-replace/case over the selection or a collection; follows `skills/naming.md` | S |
| `cleanup(scope)` | Purge orphans, merge duplicate materials/images, remove empty collections, apply transforms where safe | S |
| `relink_missing(folder)` | Find and relink missing textures/libraries by filename | S |

### 2.2 Placement & layout (**R**, reopens #219)
| Tool | What it does | Size |
|---|---|---|
| `place(object, on_top_of / next_to / inside, gap)` | World-space bounding boxes, so the model never does the maths | S |
| `align / distribute(axis, mode)` | Align min/center/max; distribute with equal gaps | S |
| `snap_to_ground`, `drop_to_floor` | Raycast down onto the nearest surface | S |
| `array_along_curve`, `scatter_on_surface(density, seed)` | Through a Geometry Nodes group we ship (procedural, editable after) | M |

### 2.3 Look-dev & rendering
| Tool | What it does | Tier |
|---|---|---|
| `frame_camera(objects)`, `make_turntable(frames)` | Camera that frames the selection; orbit with a clean loop | **R** S |
| `lighting_preset(name)` | Studio / outdoor / product presets beyond the 3-point rig | T5 |
| `material_from_folder(path)` | Build PBR from a texture set by name (extends `setup_pbr_material`) | **R** S |
| `render_queue(jobs)` | Several cameras/frames/view layers, background with progress (G6) | T5 |
| `contact_sheet / review_sheet` | Tools 5 (G5) | **R** |

### 2.4 Modeling & prep for export
| Tool | What it does | Tier |
|---|---|---|
| `check_mesh_quality` | Skills 7 (S6) | **R** |
| `fix_common(scope)` | Recalculate normals, apply scale, merge by distance, remove loose | **R** S |
| `uv_unwrap_pack(margin, texel)` | Smart unwrap + pack + texel density | T5 |
| `make_lods(ratios)`, `export_preset(target)` | Game/web export with presets (glTF/FBX/USD) | T5 |

### 2.5 Animation, rigging, VSE, text (existing lanes)
Tools 1 (VSE), Tools 2 (Text + recipes), Tools 3 (Nodes), Rig 1–2. Additions:
`subtitles_from_text` (text block → text strips), `markers_from_audio_peaks`,
`simplify_fcurves`, `bake_action` (T5).

## 3. Orchestration ideas (#71)

| Idea | What it is | Tier |
|---|---|---|
| **Map over selection** | Run one instruction per selected object/strip/node with a progress row; stop on first error or continue; one undo | **R** M |
| **Mission = plan + checkpoints** | Goal & Plan steps each get a scene checkpoint; a failed step reverts only itself | **R** (extends Engine 6) |
| **Validator pass** | After a mission, run `scene_audit` + `check_mesh_quality` and report numbers ("finish with evidence") | **R** S |
| Planner → specialists → validator | Teams 1 (X1) | T5 |
| **Recipes / macros** | Turn a finished chat turn into a replayable recipe (tool calls + parameters, no model needed to replay) | T5 |
| **Batch across files** | Run a recipe headless over a folder of .blend files with a report | T5 |
| Scene co-pilot / render critic | Teams 2–3 | T5 |
| Scheduled runs | Auto mode exists; add "run this recipe nightly" via the OS scheduler | T5 |

## 4. Quick tools in every editor — the "Coworker" quick menu

**Need**: reach the common chores in two clicks without opening a chat, and start a chat
*about this editor* in one.

**Design (to spike — Quick 1 on the board)**:
- **One entry point per editor**: a small Coworker button at the right of the editor's
  header (popover), and the same content as a **Coworker ▸** submenu in the editor's
  context menu. One place, so menus don't fill up with single items.
- **Contents, top to bottom**:
  1. *Explain this* / *Translate…* (context-dependent; Chat 2/3)
  2. **Quick actions for this editor** from a catalogue file (below), e.g. in the 3D View
     "Organize scene…", "Fix common problems", "Frame camera"; in the Node editor "Explain
     this tree", "Tidy nodes"; in the VSE "Subtitles from text"; in the Text editor
     "Run & fix", "Edit with Coworker".
  3. **Recent prompts** in this editor (one click re-sends).
  4. *Open chat* (focuses the sidebar chat or the Coworker editor).
- **Catalogue** (`quick_actions/<editor>.toml`, user-extendable): each entry names a
  *tool* (runs directly, with a preview/confirm), a *wizard*, or a *prompt template*
  (goes to the chat). This merges Text Editor 5 (prompt templates) and the Coworker
  editor's toolshelf templates into one system.
- **No custom hotkeys** (master §6.4); users can bind the menu themselves.

## 5. Wizards (guided flows in the chat — GPU 6)

A wizard = a short sequence of question cards (Chat 5 buttons) around one or more tools,
with a preview before apply. Runs on every engine; the deterministic ones need no model.

| Wizard | Steps | Tier |
|---|---|---|
| **Organize my scene** | audit → choose rules (by type / prefix / material) → preview moves + renames → apply → report | **R** (first wizard) |
| **Prepare for export** | target (Unity/Unreal/Godot/web) → checks (scale, normals, UVs, poly budget) → fixes → export | T5 |
| **Make a turntable** | pick objects → length / background / lights → render preview → queue | T5 |
| **Light this** | mood → preset → tweak with buttons | T5 |
| **Material from photos/folder** | pick folder → map detection → preview sphere → assign | T5 |
| **New add-on from a script** | Text editor: wrap the active script as an operator + panel + manifest (#52) | T5 |

## 6. What goes in the release vs Tier 5

**Release** (keeps it shippable): housekeeping tools (2.1), placement (2.2),
`frame_camera`/`make_turntable`, `material_from_folder`, `fix_common`, map-over-selection,
mission checkpoints + validator pass, the quick menu (spike + catalogue with the release
tools), and **one** wizard (Organize my scene) as the proof of GPU 6.

**Tier 5**: recipes/macros and batch across files, teams/co-pilot/critic, export/UV/LOD
pipeline, the other wizards, lighting presets, render queue.
