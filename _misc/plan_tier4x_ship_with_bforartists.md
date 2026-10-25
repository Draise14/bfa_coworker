# Tier 4x — Ship Coworker inside Bforartists (built-in copy + update repository, no installer)

**Status**: 📝 For review (2026-10-25, revised the same day). This week's priority, next to
the video week. Issue #380.
**Rule**: no PR is merged automatically, in this repo or in Bforartists'. Every PR waits
for your review.

## 1. Goal

Bforartists ships with Coworker **out of the box, switched off**, without freezing it to
the Bforartists release cycle. Bforartists doesn't need the Coworker Installer for this:
it can set up at build time everything the installer does on first run. Only one step is
left, the hand-over to an updatable copy, and Coworker does that itself.

| Piece | Where | How |
|---|---|---|
| **The built-in copy** | Bforartists' read-only **System** extensions repository (in the install folder) | The build unpacks `bfa_coworker-<version>.zip` from the submodule's pinned release. Off by default; on in Preferences like any bundled add-on. Works offline |
| **The update repository** | Bforartists' **default preferences** | *Bforartists Coworker* (`…/Draise14/bfa_coworker/main/extensions/index.json`) registered like extensions.blender.org, *Check for Updates on Startup* on |
| **The hand-over** | Coworker's own Updates box | When a newer version is listed: **Enable Updates** installs it into the update repository (settings kept), switches to it, and leaves the built-in copy off as a fallback |
| **Plain Blender users** | — | Keep using the Coworker Installer (#86), unchanged |

```
Bforartists repo
 └─ (submodule) bfa_coworker @ v1.1.41
        │  build step: unpack releases' bfa_coworker-1.1.41.zip
        ▼
 <install>/<version>/extensions/system/bfa_coworker      (built-in copy, off by default)
 default prefs: repository "Bforartists Coworker" ──► extensions/index.json (async updates)
        │  a newer version is listed → Coworker's Updates box: [Enable Updates]
        ▼
 <user>/extensions/<coworker repo>/bfa_coworker          (updatable copy, now the one in use)
```

## 2. Why the hand-over is needed

The built-in copy lives in the install folder, which is read-only, so it can't be updated
in place. An update has to go into the update repository's folder in the user's profile,
and then the same Coworker (`bfa_coworker`) exists in two repositories. Today the
installer avoids that by moving Coworker into the update repository on first run; here
Coworker does it the moment there is something to update to.

## 3. Open questions the spike answers

| Question | What we check | If it fails |
|---|---|---|
| Does Bforartists 5.3 load extensions from the System repository? | Unpack Coworker there in a 5.3 build; it lists, enables, runs | Ship it as a core add-on instead (needs a small `bl_info` shim) |
| **Same id in two repositories** | Install a newer `bfa_coworker` into the update repository while the System copy exists: does Bforartists warn, refuse, or allow only one enabled? | If it refuses: keep the installer route (ship the installer instead of Coworker) |
| Switching copies | Disable System copy → enable repository copy in one step, with settings kept (`settings_transfer.py`) | Ask for a restart |
| Default repository | Register the update repository in Bforartists' default preferences (`userdef_default` / startup prefs) | Coworker registers it on first enable |
| Offline | Built-in copy enables and runs with no network | — |

## 4. Tasks

### Bundle 0 — Spike (#380, S)
The five checks of §3 in a Bforartists 5.3 build. **Done when** each has a result written
up here and the route (built-in copy + hand-over, or installer) is chosen.

### Bundle 1 — Enable Updates in Coworker (S)
Coworker knows it's the built-in copy (its path is inside the System repository). The
Updates box (`preferences._draw_updates`) shows *Built in to Bforartists* and, when the
update repository lists a newer version, **Enable Updates**: install into that repository
(the extensions install operators, addressed by repository directory as in #95; probe the
exact call), restore settings
(`settings_transfer`), disable the built-in copy, enable the new one. bpy-free tests for
the decision ("which copy is in use, is there a newer one"); a by-hand check in a
Bforartists build.
**Done when** a built-in 1.1.41 hands over to 1.1.42 from the repository with settings
kept, and stays updatable afterwards.

### Bundle 2 — Bforartists build step + submodule (M)
- This repo: `BUNDLING.md` for Bforartists maintainers (pinning, unpacking, the default
  repository line), and a check that the release zip is the one in `extensions/index.json`.
- Bforartists repo: the submodule at a tag; a build step that unpacks the pinned
  `bfa_coworker-<version>.zip` into the System extensions folder; the default
  repository entry. A **PR to Bforartists for you to review**; nothing merges by itself.
**Done when** a fresh Bforartists build has Coworker available offline (off by default),
and turning it on and then updating reaches the next Coworker release without a new
Bforartists build.

### Bundle 3 — Release routine (S)
Each Coworker release: tag → GitHub release → `extensions/index.json` PR (as today) →
optionally bump the submodule for Bforartists' next release. Documented in
`extensions/README.md`.
