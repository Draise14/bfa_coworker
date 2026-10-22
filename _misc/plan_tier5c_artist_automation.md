# Tier 5 — Artist automation: spells, wizards, missions, teams (+ the Tier 4 tool ideas)

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

*2026-10-21: the release's orchestration layer is **threads per editor**, side by side
(`plan_tier4o_orchestration_threads.md`). The ideas below run inside a thread; missions
and teams are Tier 5 and are built on threads.*

| Idea | What it is | Tier |
|---|---|---|
| **Map over selection** | Run one instruction per selected object/strip/node with a progress row; stop on first error or continue; one undo | **R** M |
| **Mission = plan + checkpoints** | Goal & Plan steps each get a scene checkpoint; a failed step reverts only itself | **R** (extends Engine 6) |
| **Validator pass** | After a mission, run `scene_audit` + `check_mesh_quality` and report numbers ("finish with evidence") | **R** S |
| Planner → specialists → validator | Teams 1 (X1) | T5 |
| **Recipes / macros** | Turn a finished chat turn into a replayable recipe (tool calls + parameters, no model needed to replay) | T5 |
| **Batch across files** | Run a recipe headless over a folder of .blend files with a report | T5 |
| Scene co-pilot / render critic | Teams 2–3 | T5 |
| Direct in-process tool calls for Local / Remote | Skip the HTTP round-trip to our own MCP server when the built-in loop calls a tool | T5 |
| Call receipts | Each changing tool call gets an id; a retried call returns the first result instead of acting twice | T5 |
| Scheduled runs | Auto mode exists; add "run this recipe nightly" via the OS scheduler | T5 |

## 4–5. Quick tools and wizards → Coworker operators

Superseded by **`plan_tier4m_coworker_operators.md`**: ordinary-looking operators with a
wand-badged icon in their native menus and Bforartists toolshelf tabs, with packs
(Sequencer first, then Nodes; organize, bake, asset library) and the **Spell Book**, where
users record their own and publish them into the toolshelf (no model needed to replay). The tools in §2 are what those operators call.

## 6. What goes in the release vs Tier 5

Superseded by master §0.1 (2026-10-21). **Tier 4**: threads per editor (Threads 0–3),
housekeeping and placement tools (Tools 7–8), Wand 0–3 with *Sequencer basics*,
Procedural 1, Security 1–4; map-over-selection as stretch. **Tier 5** (this doc's home):
the Spell Book (Spell 1–3, quick prompts #300), missions and teams on top of threads,
recording clicks, Edit as Code, Make Parametric and the nudge, the big wizards (Edit from Footage, bake, asset library),
look-dev helpers, batch across files, export/UV/LOD, lighting presets, render queue.

## 7. Asset library ecosystem — built in tandem with the other add-on (notes, 2026-10-21)

Procedural first (master §0) only works if there are good node-group assets to reach
for. Your plan: a strong asset library built **for the other add-on in tandem**, shipped
with Bforartists, offered through the online extensions, and later available as a
subscription through an API key.

| Piece | Notes | When |
|---|---|---|
| **Default Library add-on (Bforartists)** | Today's node-group library you biased the agent towards. Coworker reads it through the asset tools; Procedural 1 makes it the first place to look | Now |
| **Online essentials** | Online asset libraries, served like the extension repository (#86): a listing + downloads, hash-checked (Security 5) | Soon |
| **Subscription libraries** | Premium libraries unlocked with an API key; Coworker only needs a library path or a listing URL, so access control stays in the other add-on | Later |
| **Metadata for agents** | Each node group carries what an agent needs to pick and drive it: a one-line description, tags, catalog, input names with units and ranges, a preview. Write it once in the library; every engine benefits | With the library |
| **Feedback loop** | *Make Parametric* (Procedural 2) can save new groups into your own library with the same metadata, so the library grows from real work | After Procedural 2 |

Open question to work out together: which node groups to build first (fence/wall along a
curve, scatter, array/kit-bash, stairs, cables, procedural materials?) — the ones the
Coworker most often builds linearly today are the best candidates (the benchmark
transcripts can tell us).

## 8. Guided tours — "show me how", never "do it for me" (Tier 5, #368)

An agent that drives the UI is the opposite of a coworker; teaching a workflow with your
own hands is what a coworker does. So Coworker never clicks for you: a tour **highlights**
the button, panel or menu entry for each step (ring or spotlight + a one-line hint), waits
for **you** to click it, checks the step happened, and moves on.

| Piece | How |
|---|---|
| **A tour is a spell in teaching mode** | The Spell Book's steps (#337), shown instead of run: shipped tours (first steps in Bforartists, a first node material, cutting video) and tours made from your own spells, so a technical artist records a workflow once and the team learns it |
| **Wizards** | Offer *Show me how* next to *Do it for me* |
| **Highlight** | Builds on Explain This's red ring (#355): find the region and rectangle, draw a ring / spotlight, a small card with the hint and Back / Skip / Stop |
| **Step check** | Each step names the state it expects (operator ran, property changed, editor opened); *Do this step for me* only as an explicit fallback |
| **Narration** | Optional: ask the active engine "why this step?"; the tour itself needs no model |

