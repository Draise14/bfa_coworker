# Tier 4x — Ship Coworker inside Bforartists (submodule + bundled installer, updates stay async)

**Status**: 📝 For review (2026-10-25). This week's priority, next to the video week.
**Rule**: no PR is merged automatically, in this repo or in Bforartists'. Every PR waits
for your review.

## 1. Goal

Bforartists ships with Coworker **out of the box**, without freezing it to the
Bforartists release cycle:

- The Bforartists source tree carries this repo as a **git submodule**, pinned to a
  release tag (e.g. `v1.1.41`).
- The Bforartists build packs the **Coworker Installer** (with the pinned Coworker
  bundled inside, as today's `bfa_coworker_installer.zip`) into the shipped add-ons.
- On first enable, the installer does what it does today (#86): adds the *Bforartists
  Coworker* extension repository and installs the bundled Coworker into it.
- From then on **updates stay asynchronous**: new Coworker versions arrive through
  *Get Extensions* from `extensions/index.json`, whenever we release, not when
  Bforartists does. Offline, the bundled copy works.

```
Bforartists repo
 └─ (submodule) bfa_coworker @ v1.1.41
        │  Bforartists build step
        ▼
 <install>/<version>/…/bfa_coworker_installer   (shipped, enabled by default)
        │  first enable
        ▼
 user extensions repo "Bforartists Coworker"  ── updates ◄── extensions/index.json (async)
```

## 2. Open questions the spike answers

| Question | Options |
|---|---|
| Where a shipped extension lives | **System extensions repository** of the install (read-only, shipped with the app), or `scripts/addons_core` (legacy add-ons need `bl_info`; ours are manifest-based extensions). Probe what 5.3 / Bforartists supports |
| Same id in two repos | A system copy of the *installer* is fine (it has its own id); Coworker itself stays only in the user repo, so the system copy and the updated copy never collide |
| Enabled by default | Bforartists' default preferences enable the installer; first run installs Coworker. Or the installer's own first-run prompt |
| Offline first run | The bundled zip installs without network; the repo is added for later |
| Build integration | A CMake / `make` step that extracts the submodule's `releases/bfa_coworker_installer.zip` into the install tree |
| Version shown | The installer's manifest version = the pinned Coworker version (already true since #95) |

## 3. Tasks

### Bundle 0 — Spike (#380, S)
In a Bforartists 5.3 build: put the installer in the system extensions location, then in
`addons_core`. Check it registers, can be enabled by default, installs Coworker into the
user repo, and that a newer Coworker from *Get Extensions* updates cleanly afterwards.
**Done when** one placement works end to end, written up here.

### Bundle 1 — Build step + submodule (M)
- This repo: the tagged tree already carries `releases/bfa_coworker_installer.zip` (the
  installer with Coworker and its Windows dependencies inside), so Bforartists' build only
  **copies / extracts that file**; no Python vendoring in the Bforartists build. Add a
  short `BUNDLING.md` for Bforartists maintainers (and per-platform installers once macOS /
  Linux builds exist).
- Bforartists repo: add the submodule at a tag; a build step copies or builds the
  installer into the install tree; default preferences enable it. A **PR to Bforartists**
  for you to review; nothing merges by itself.
**Done when** a fresh Bforartists build starts with Coworker available offline, and
updates to the next Coworker release through *Get Extensions*.

### Bundle 2 — Release routine (S)
Each Coworker release: tag → GitHub release → `extensions/index.json` PR (as today) →
optionally bump the submodule in Bforartists for its next release. Documented in
`extensions/README.md`.
