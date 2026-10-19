# Tier 4s — Skills uplift, clean room

**Status**: 📝 Draft (2026-10-18). Tasks S0–S7 on `plan_tier4_task_board.md` (lane S).
**Why now**: an outside review of a public Blender agent-skill catalogue, plus live
checks of our own tools on Bforartists 5.3 (2026-10-18), showed two broken tools and
API areas our skills never mention. S0/S1 are bugs and belong in **1.1.39**.

## 1. What triggered this (facts, no third-party content)

| Finding | Evidence | Task |
|---|---|---|
| `execute_blender_plan` and `list_blender_templates` fail in **every** mode | Live, Bforartists 5.3 via External Harness: "Template system unavailable" / "Template registry not available". The MCP server runs in its own process and imports `bfa_coworker.blender_templates`, which never resolves there; inside Blender the add-on is `bl_ext.<repo>.bfa_coworker`. Not intercepted by the in-app loop either (`agent_controller` lists both as surface tools). The tool text tells models to prefer it, so they waste a call | S0 |
| `get_active_node_tree` breaks for the compositor on 5.x | Live: `Scene.node_tree` does not exist on 5.3 (`compositing_node_group` does). The tool code reads `scene.node_tree` and *sets* `scene.use_nodes = True` -- a read tool that edits the scene | S1 |
| Our skills teach `mat.use_nodes = True` / `world.use_nodes = True` | `best_practices.md`, `blender_53.md`, Poly Haven tool. Deprecated since 5.0 (still present in 5.3); new materials already have nodes | S1 |
| Always-loaded skills cost ~1,900 words every turn; no descriptions to build an index from | `skills/__init__.py` `_ALWAYS_FILES` | S4 → F2 |
| No skill covers several 5.x areas | `grep` of `skills/`, `prompts.yml`, tools: 0 hits for compositor node group, bone collections, evaluated mesh / `to_mesh_clear`, `surface_render_method`, render `media_type`, `gpu.init` in background, bulk `foreach_get` | S5 |
| Agents claim "done" without numbers or a geometry check | Observed in our own benchmark transcripts; the stop rules (3k) know *when* to stop, not *how to prove* it | S5, S6 |

The external catalogue is MIT-licensed, so copying would be legal with a notice. We
still don't: Tier 4 is white room (4b §4) for a clean provenance record and our own design.

## 2. The clean-room method (how every S task is done)

Two roles. One person can play both on different days, or use two separate agent
sessions; what matters is that **the implementer never sees the reference**.

1. **Reader** (once, already done for this plan). Reads the reference and writes only a
   **needs list**: user problems and topic names, in our own words. No code, no
   wording, no headings, no structure, no file names, no example values. The needs list
   in §3 is the *only* thing that crosses over. The reader then stops consulting it.
2. **Implementer** (fresh session, no access to the reference; don't paste its URL
   into the session). Works only from first sources:
   - the bundled API reference and manual (`mcp/blmcp/data`, `search_api_docs`,
     `search_manual_docs`);
   - **live probes** on Blender/Bforartists 5.3 through our own MCP
     (`execute_blender_code` introspection: `bl_rna.properties`, enum items, a
     throwaway-scene try/except);
   - Blender's release notes for the version that changed the API.
3. **Every claim carries a source**: a probe id or an RST path, kept in the skill's
   front matter (`sources:`). This doubles as B7's doc-drift record.
4. **Every API claim is tested** (S3): a probe asserts it on 5.3; CI and the gate runs
   re-check it, so drift shows up as a failing probe instead of a wrong answer.
5. **Similarity check before merge** (reader role, mechanical): a small script compares
   word 8-gram shingles of the new skill files against the reference and reports the
   maximum overlap. Anything above a few incidental phrases (API names excepted) is
   rewritten by the implementer. The script reports numbers only, never text, so it
   leaks nothing back.
6. **PR line**: "Clean room: implemented from the S needs list and first sources; no
   third-party code or text consulted while implementing. Overlap check: max N shingles."

## 3. Needs list (the only input from the reference)

Written by the reader, 2026-10-18. Our words; topics, not content.

1. Agents use APIs removed or renamed in 5.x; they need a short "this changed" list
   they can check when code fails, and the always-on version skill should stay small.
2. Agents should prefer data access over operators and know how to give an operator
   the context it needs when one is unavoidable.
3. Agents hold Python references to data that Blender has freed (after removing or
   applying something) and crash or read garbage.
4. Agents read stale or un-evaluated geometry (edit mode, modifiers not applied) and
   report wrong numbers.
5. Agents need to finish with **evidence**: counts, dimensions, files written, and a
   geometry sanity check, plus a look at the result.
6. A pre-delivery geometry check is valuable on its own: overlapping faces, loose
   parts, open edges, flipped normals, unapplied scale.
7. Node lookups break on localized builds when done by display name.
8. Skills should be loadable on demand: a one-line description for routing, a short
   body, deeper references only when needed; size budgets enforced by a checker.
9. Domain gaps users ask about: compositor in 5.x, bone collections, material render
   method, render output format order, GPU module in background, fast bulk mesh access.
10. Third-party agent skill packs exist that call a tool named `execute_blender_code`;
    keeping our tool names stable lets users combine them with our server (their
    choice, their install).

## 4. Tasks (mirrored on the board, lane S)

- **S0** Move the template registry into the server (`blmcp/templates/`, bpy-free
  strings + renderer); `execute_blender_plan` renders server-side and sends code over
  the bridge. Delete the add-on copy or make it import the server one. Until it lands,
  hide both tools from the surface list. **1.1.39.**
- **S1** `get_active_node_tree`: compositor via `scene.compositing_node_group`; a read
  tool never creates or enables nodes (report "no compositor tree" instead). Remove the
  `use_nodes = True` advice from skills and the Poly Haven world setup. **1.1.39.**
- **S2** Needs list: §3 (done with this plan).
- **S3** API-claims probes: `_misc/api_probes.py` (runs inside Blender via the bridge
  or headless) + `tests/test_api_claims.py` (bpy-free parser that checks every skill
  claim has a probe). Run at each test gate.
- **S4** Skill format + checker: front matter `name`, `description` (≤ 400 chars),
  `sources`, `reviewed_revision`; body budget (~2k tokens); `references/` loaded on
  request only; `_misc/check_skills.py` (budgets, links, front matter, probes present).
  F2 builds its index from these descriptions.
- **S5** New chapters from first sources for needs 1–4, 7, 9, and a short
  "finish with evidence" rule set (need 5) tied to the stop rules' wrap-up.
- **S6** `check_mesh_quality(objects)` MCP tool (need 6), bpy/bmesh from the API docs,
  compact JSON result; reused by X2 co-pilot and G5 review sheets.
- **S7** Docs: tool-name stability note + "using other skill packs with External
  Harness" (need 10), with no recommendation of any specific pack.

## 5. Suggested improvements to existing tasks

| Task | Suggestion | Why |
|---|---|---|
| **T0 baseline** | Land S0 + S1 first | Otherwise the baseline measures two broken tools and wasted calls |
| **F2** on-demand skills | Depend on S4; done-when adds "first-request prompt tokens on Local 16K, before/after" and "External Harness gets the `skill` tool" | Index needs descriptions; the saving must be measured |
| **B7** doc drift | Use S3 probes as the drift signal (`sources` = probe ids); manual review only for prose | Turns drift into a failing test |
| **Lane G** domain chapters (4g.5) | Write each with the §2 method and add probes | Same provenance and drift story for every new chapter |
| **I4** progressive tools | Hide `execute_blender_plan`/`list_blender_templates` until S0, and add a tool-health check at server start (each surface tool's import path resolves) | Broken tools should never be advertised |
| **X2** scene co-pilot | Reuse S6 detectors | One implementation |
| **Every PR** | Add "skill/tool text claims have probes" to the checklist | Keeps the clean-room and drift rules cheap to follow |
