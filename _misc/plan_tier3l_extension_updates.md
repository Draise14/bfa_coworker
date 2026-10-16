# Tier 3l -- Updates through Blender's extension system (issue #86)

**Status**: 🚧 In progress -- implemented and tested outside Blender (2026-10-16);
**in-Bforartists verification pending** (see §6). Move to `Plans History/` with an
Audit section once verified.
**Branch**: `feat/86-extension-updates`

## 1. Decision

Use Blender's own extension repositories instead of a built-in updater
(CG Cookie style). Blender already does version display, update checks on
startup, in-place upgrade without restart, and stale-file cleanup for files
Windows keeps locked during an upgrade. A custom updater would re-implement all
of that while llama-server and vendored `.pyd` files are in use.

| Option | Verdict |
| --- | --- |
| Built-in updater (download zip, swap files, backup for revert) | ❌ duplicates Blender, fragile on Windows file locks |
| Coworker registers the repository and moves *itself* into it | ❌ moving a running add-on between repositories |
| **Carrier "Coworker Installer" + static repository** | ✅ chosen (user, 2026-10-16) |

Hosting: `extensions/index.json` on `main`, served by raw.githubusercontent.com
(no Pages setup needed; a GitHub Pages page can serve the same file later).
Zips live on GitHub Releases (`archive_url` is absolute). Stable channel only.

## 2. Verified against Blender's source (`scripts/addons_core/bl_pkg`)

* A dropped link carrying `?repository=<index url>` makes Blender offer
  **Add Repository...** for an unknown repository, then install (`_invoke_for_drop`,
  `_draw_override_repo_add`).
* Installing a zip into a *remote* repository from disk is supported
  (`rna_prop_repo_enum_valid_only_itemf` lists remote repos for `package_install_files`).
* Absolute `archive_url` values are downloaded as-is; `./` ones are relative to the index.
* `preferences.extension_repo_add(name, remote_url, type='REMOTE', use_sync_on_startup)`
  derives the module from the name (`Bforartists Coworker` -> `Bforartists_Coworker`).

## 3. Pieces

| Piece | File |
| --- | --- |
| Listing generator / installer builder (pure Python) | `extension_repo.py` |
| The repository | `extensions/index.json`, `extensions/README.md` |
| Build integration (`--repo-tag`, installer by default) | `build_addon.py` |
| Carrier add-on | `installer/bfa_coworker_installer/` |
| Settings carry-over (module path changes with the repository) | `addon/bfa_coworker/settings_transfer.py`, `__init__.py` (`_save_settings_backup` on unregister, `_restore_settings_backup` in register), `preferences.py` (`settings_restored`) |
| "Updates" box (where this copy updates from; copy repo URL) | `preferences.py` `_draw_updates`, `BFACW_OT_copy_update_url`; `shared.py` `UPDATE_REPO_URL`, `update_repo_of_this_install` |
| Docs | wiki generator (Quick Start step 1, Installation: download, manual repo, updating, rollback, uninstalling), CHANGELOG |

### Installer behaviour
* First enable (once, `setup_done`; opt-out `auto_setup`): add repo -> if Coworker
  is not in it, save settings of any copy installed from a file, uninstall that copy,
  install the bundled zip into the repo (offline), sync if online access is on.
* A **newer** copy from a file (a development build) is left alone.
* An updated copy in the repository is never downgraded; **Reinstall Bundled** is
  the explicit rollback.
* **Uninstall Coworker** / **Uninstall Everything** (Coworker, repository with files,
  then the installer itself from a timer). Blender has no uninstall hook, so removing
  the installer from the list leaves Coworker in place -- documented.

## 4. Tests
`tests/test_extension_updates.py` (23):
* listing entries, flat vs folder zips, one version per id, drag link, committed
  listing valid, repo URL identical in all three places, installer bundles the zip
  with the matching version;
* settings round trip, skipped keys, no overwrite on upgrade in place, bad values,
  installer writes the format Coworker reads;
* installer against a fake `bpy` that really moves files: fresh install, migration
  from a file install with settings kept and uninstall-before-install order,
  idempotent + no downgrade, rollback, newer dev copy untouched, offline, uninstall everything;
* **end to end with Blender's own extension client** (`blender_ext.py`, set
  `BLENDER_EXT_CLI`): sync over HTTP, install 1.1.37, publish 1.1.38, upgrade;
  install the installer zip and its payload.
Also run by hand with the real 24 MB 1.1.37 build: sync, install, upgrade.

## 5. Release flow
See `extensions/README.md`: build with `--repo-tag`, attach both zips to the
release unchanged, publish, then merge the `index.json` PR.

## 6. In-Bforartists verification still needed
1. Drag `bfa_coworker_installer-1.1.37.zip` into Bforartists with 1.1.36 installed from
   a zip: repository added, 1.1.37 installed into it, API key / model settings kept.
2. Get Extensions lists Coworker under *Bforartists Coworker*; Preferences > Coworker >
   Advanced > Updates says "Automatic".
3. After 1.1.38 is published: update badge on startup, Update works without restart
   (servers stopped/restarted; no locked-file errors on Windows).
4. Installer buttons: Reinstall Bundled (rollback), Uninstall Coworker, Uninstall Everything.
5. Bforartists still shows Get Extensions / repositories like Blender 5.1.
