# BFA Coworker — Tier 4e: Artist Workflow Tooling (Rigging, Animation, Smart Save)
**Date**: 2026-09-01
**Status**: Planning
**Depends on**: Tier 3 domain system, existing toolcode pattern
**Purpose**: Quick-win domain tooling that completes the matrix for the three most common artist-adjacent workflows that today still require raw `bpy` from the LLM.

---
## Table of Contents
1. [Why These Three Domains](#1-why-these-three-domains)
2. [Phase 4e.1: Rigging Tools](#2-phase-4e1-rigging-tools)
3. [Phase 4e.2: Animation Tools](#3-phase-4e2-animation-tools)
4. [Phase 4e.3: Smart Save Tools](#4-phase-4e3-smart-save-tools)
5. [Domain Registration & Prompt Updates](#5-domain-registration--prompt-updates)
6. [Kimodo Integration Research](#6-kimodo-integration-research)
7. [Summary](#7-summary)

---
## 1. Why These Three Domains

| Domain | Current State | Problem | Tier 4e Fix |
|--------|--------------|---------|-------------|
| **Rigging** | LLM must write `bpy.ops.object.parent_set()`, `bpy.ops.pose.constraint_add()`, IK chain setup from scratch | High hallucination rate; bone names, constraint types, and axis conventions are easy to get wrong | 6 pre-authored toolcodes: add armature, add bone, add constraint, IK setup, mirror pose, bake animation |
| **Animation** | LLM must write `keyframe_insert()` calls, F-curve modifier setup, NLA track management | Repetitive boilerplate; easy to miss frame ranges or interpolation types | 5 toolcodes: batch keyframe, set interpolation, add F-curve modifier, NLA track, bake action |
| **Smart Save** | LLM has no file-management tools at all | Can't save, can't check for unsaved changes, can't pack resources, can't export | 5 toolcodes: save blend, check unsaved, pack resources, export selection, incremental save |

All follow the same toolcode pattern as existing Tier 3 tools: `Params` NamedTuple → `send_code()` → `main(params)` → `Result` NamedTuple. Registered in `rigging`, `animation`, and `file_management` domains.

---
## 2. Phase 4e.1: Rigging Tools (~350 LOC, 12 files)

### 2.1 `add_armature` — Create an armature with a single bone at a location
- **Params**: `name`, `location`, `rotation`, `bone_length`
- **Returns**: armature name, bone name
- **Domain**: `rigging`
- **ReadOnly**: False

### 2.2 `add_bone` — Add a bone to an existing armature
- **Params**: `armature_name`, `bone_name`, `parent_name` (optional), `head_location`, `tail_location`, `use_connect`
- **Returns**: bone name, parent name
- **Domain**: `rigging`

### 2.3 `add_constraint` — Add a constraint to an object or bone
- **Params**: `target_name`, `constraint_type` (enum: COPY_LOCATION, COPY_ROTATION, COPY_TRANSFORMS, TRACK_TO, IK, CHILD_OF, DAMPED_TRACK, STRETCH_TO, LIMIT_LOCATION, LIMIT_ROTATION, LIMIT_SCALE, TRANSFORM, CLAMP_TO, FOLLOW_PATH, PIVOT, SHRINKWRAP), `subtarget` (bone name for pose constraints), `influence`, `target_object`
- **Returns**: constraint name, status
- **Domain**: `rigging`

### 2.4 `setup_ik_chain` — Set up an IK constraint on a chain of bones
- **Params**: `armature_name`, `chain_length`, `target_bone`, `pole_target` (optional), `pole_angle`, `iterations`
- **Returns**: IK constraint name, chain bones list
- **Domain**: `rigging`

### 2.5 `mirror_pose` — Mirror the current pose across X axis
- **Params**: `armature_name`, `only_selected`
- **Returns**: mirrored bone count
- **Domain**: `rigging`

### 2.6 `bake_animation` — Bake constraints/IK to keyframes on the armature
- **Params**: `armature_name`, `frame_start`, `frame_end`, `step`, `only_selected`
- **Returns**: keyframe count, frame range
- **Domain**: `rigging`

---
## 3. Phase 4e.2: Animation Tools (~350 LOC, 10 files)

### 3.1 `batch_keyframe_insert` — Keyframe multiple properties at once
- **Params**: `object_name`, `properties` (list of `{data_path, value, frame}`), `interpolation` (BEZIER/LINEAR/CONSTANT)
- **Returns**: keyframe count
- **Domain**: `animation`

### 3.2 `set_interpolation` — Set interpolation type for selected keyframes
- **Params**: `object_name`, `data_path` (optional), `interpolation` (BEZIER/LINEAR/CONSTANT/BOUNCE/ELASTIC/QUAD/CUBIC/QUART/QUINT/SINE/EXPO/CIRC), `easing` (AUTO/EASE_IN/EASE_OUT/EASE_IN_OUT)
- **Returns**: modified F-curve count
- **Domain**: `animation`

### 3.3 `add_fcurve_modifier` — Add a modifier to an F-curve
- **Params**: `object_name`, `data_path`, `modifier_type` (NOISE/ENVELOPE/CYCLES/LIMITS/STEPPED/GENERATOR/FN_GENERATOR), `parameters` (dict of modifier-specific settings)
- **Returns**: modifier name, status
- **Domain**: `animation`

### 3.4 `nla_track_add` — Add an NLA track with an action strip
- **Params**: `object_name`, `track_name`, `action_name`, `frame_start`, `blend_type`, `influence`, `auto_blend`
- **Returns**: track name, strip name, frame range
- **Domain**: `animation`

### 3.5 `bake_action_to_nla` — Bake the active action into an NLA track and clear the action
- **Params**: `object_name`, `track_name`, `frame_start`, `frame_end`, `step`
- **Returns**: track name, keyframe count
- **Domain**: `animation`

---
## 4. Phase 4e.3: Smart Save Tools (~250 LOC, 10 files)

### 4.1 `save_blend_file` — Save the current blend file
- **Params**: `filepath` (optional — if empty, saves to current path; if new path, saves-as)
- **Returns**: filepath, file_size, was_modified
- **Domain**: `file_management`

### 4.2 `check_unsaved_changes` — Check if there are unsaved changes
- **Params**: none
- **Returns**: `has_unsaved_changes`, `modified_data_blocks` (list of names), `last_save_time`
- **Domain**: `file_management`

### 4.3 `pack_resources` — Pack external resources into the blend file
- **Params**: `resource_types` (IMAGES/ALL), `remove_original_files` (optional)
- **Returns**: packed_count, total_size_mb
- **Domain**: `file_management`

### 4.4 `export_selection` — Export selected objects to a file
- **Params**: `filepath`, `format` (FBX/OBJ/GLTF/STL/USD/PLY), `apply_modifiers`, `selected_only`, `include_animation`
- **Returns**: filepath, file_size, exported_count
- **Domain**: `file_management`

### 4.5 `incremental_save` — Save an incremental version (appends _001, _002, etc.)
- **Params**: `max_versions` (default 10, auto-prunes oldest), `prefix` (optional)
- **Returns**: filepath, version_number, file_size
- **Domain**: `file_management`

---
## 5. Domain Registration & Prompt Updates

### 5.1 New Domains

Add to `_TOOL_DOMAINS` in `agent_controller.py`:

```python
"rigging": [
    "add_armature", "add_bone", "add_constraint",
    "setup_ik_chain", "mirror_pose", "bake_animation",
],
"animation": [
    "batch_keyframe_insert", "set_interpolation",
    "add_fcurve_modifier", "nla_track_add", "bake_action_to_nla",
],
"file_management": [
    "save_blend_file", "check_unsaved_changes",
    "pack_resources", "export_selection", "incremental_save",
],
```

### 5.2 Domain Keywords

```python
"rigging": ["rig", "armature", "bone", "constraint", "ik", "pose", "fk"],
"animation": ["animate", "keyframe", "fcurve", "nla", "interpolation", "bake"],
"file_management": ["save", "export", "pack", "backup", "version", "incremental"],
```

### 5.3 Prompt Updates

Add to `prompts.yml`:
- **Rigging section**: "Use `add_armature` to create a rig, `add_bone` to extend it, `add_constraint` for IK/FK/TRACK_TO, `setup_ik_chain` for full IK chains, `mirror_pose` for symmetry, `bake_animation` to lock in constraints."
- **Animation section**: "Use `batch_keyframe_insert` for bulk keyframing, `set_interpolation` to change curve types, `add_fcurve_modifier` for noise/cycles/limits, `nla_track_add` for non-destructive layering."
- **Smart Save section**: "Use `incremental_save` before destructive operations. Use `check_unsaved_changes` to verify state. Use `pack_resources` before sharing .blend files."

---
## 6. Kimodo Integration Research

### 6.1 What Kimodo Is

Kimodo is NVIDIA's **kinematic motion diffusion model** — text-to-motion for human(oid) characters. It generates 3D joint animations from natural language prompts ("a person jogs in a circle") with extensive constraint support (end-effector positions, full-body keyframes, 2D paths).

### 6.2 Licensing

| Component | License | GPL-Compatible? |
|-----------|---------|-----------------|
| **Kimodo research code** (nv-tlabs/kimodo) | **Apache 2.0** | ✅ Yes — can be called from GPL addon |
| **Kimodo model weights** (HuggingFace) | **NVIDIA Open Model License** | ⚠️ Separate — permissive for use, but check redistribution terms |
| **Kimodo_Blender_Bridge** (lewdineer) | **GPL-2.0-or-later** (from https://github.com/lewdineer/Kimodo_Blender_Bridge/tree/main/blender_manifest.toml) | ✅ Yes — same license as bfa_coworker |
| **Kimodo-SMPLX-RP-v1** | **NVIDIA R&D Model License** | ⚠️ Research-only — cannot ship in Bforartists |

**Verdict**: The Kimodo codebase (Apache 2.0) and the existing bridge addon (GPL-2.0) are both compatible with bfa_coworker's GPL-3.0. The model weights use NVIDIA's Open Model License which is permissive for use but restricts redistribution — users would download weights themselves (same pattern as GGUF models today). The SMPLX variant is research-only and cannot be shipped.

### 6.3 Integration Architecture

Kimodo already has a mature Blender bridge (`Kimodo_Blender_Bridge`) with:
- Auto-installer (creates managed venv, downloads PyTorch + model weights)
- Two-process bridge (Blender ↔ subprocess with Kimodo loaded)
- Retargeting system (fuzzy bone matching, constraint-based, bake-to-clean)
- Timeline segment system (multi-prompt with smooth transitions)
- Motion constraints (end-effector waypoints, full-body keyframes)

**We don't need to build this from scratch.** The integration path is:

1. **Option A: Recommend existing addon** — Document that Kimodo_Blender_Bridge is a companion addon. The Coworker agent can detect it and use its operators via `bpy.ops.kimodo.*`. No code needed from us.
2. **Option B: Wrap as gen plugin** — Create a `gen_plugins/kimodo.py` that wraps the bridge's subprocess client. The Coworker agent calls `gen_controller.generate()` with a text prompt, and the plugin talks to the Kimodo bridge. ~200 LOC.
3. **Option C: Ship as bundled companion** — Include Kimodo_Blender_Bridge source as a bundled addon (both GPL-2.0, compatible). The Coworker agent auto-starts the Kimodo bridge when rigging/animation domain is active. ~50 LOC for integration.

**Recommendation**: Option A for v1 (documentation), Option B for Tier 5 (gen plugin wrapper), Option C only if users consistently ask for it.

### 6.4 Can Kimodo Be an "Animation Generator"?

**Yes, but with caveats:**

| Strength | Limitation |
|----------|------------|
| Excellent text-to-motion quality (700h mocap dataset) | Human/robot motion only — no creature, no object animation |
| Rich constraint system (end-effectors, keyframes, paths) | ~17 GB VRAM for full GPU; text encoder can be offloaded to CPU |
| Multi-segment timeline with smooth transitions | 30 FPS fixed — scene must match or use "Set to 30 FPS" button |
| Retargeting to any rig via constraint baking | Requires manual bone mapping for non-standard rigs |
| Apache 2.0 code + permissive model weights | SMPLX variant is research-only |

**For Bforartists Coworker**: Kimodo fills the "character animation from text" gap. It's not a general animation generator (no object animation, no physics, no facial animation), but for the specific use case of "make this character walk/run/dance/jump" it's the best local option available.

### 6.5 ARDY — Real-Time Alternative

NVIDIA's **ARDY** (released July 2026) builds on Kimodo for **real-time** motion generation. It's faster but less controllable. If ARDY's license is also Apache 2.0, it could be a lighter-weight alternative for preview scenarios.

---
## 7. Summary

| Phase | Tools | Files | LOC | Domain |
|-------|-------|-------|-----|--------|
| 4e.1 | Rigging (6 tools) | 12 | ~350 | `rigging` |
| 4e.2 | Animation (5 tools) | 10 | ~350 | `animation` |
| 4e.3 | Smart Save (5 tools) | 10 | ~250 | `file_management` |
| **Total** | **16 tools** | **32** | **~950** | **3 new domains** |

### Key Decisions

| Decision | Rationale |
|----------|-----------|
| **Toolcode pattern, not gen plugin** | These are deterministic Blender operations, not generative AI. The existing toolcode pattern is the right fit. |
| **Kimodo as companion, not rebuild** | The existing Kimodo_Blender_Bridge is mature and GPL-compatible. Document it, don't duplicate it. |
| **Smart Save as a domain** | File management is a common failure point for LLMs (they can't save, can't check state). Giving them tools for this prevents data loss. |
| **Incremental save as CHOYA trigger** | After any destructive operation, CHOYA should offer "Save incremental version" — this is the highest-value single tool in the set. |