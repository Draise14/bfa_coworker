# Coworker extension repository

`index.json` in this folder **is** the Bforartists Coworker online extension
repository (Blender's static "v1" listing format). Blender / Bforartists read it from:

```
https://raw.githubusercontent.com/Draise14/bfa_coworker/main/extensions/index.json
```

That URL is baked into the add-on (`addon/bfa_coworker/shared.py`), the
Coworker Installer (`installer/bfa_coworker_installer/__init__.py`) and
`extension_repo.py`. **Never move or rename this file** -- every installed copy
checks this URL for updates.

The zips themselves are **not** stored in git: each entry's `archive_url`
points at a zip attached to a GitHub release.

## Publishing a release

1. Bump `version` in `addon/bfa_coworker/blender_manifest.toml`.
2. Build on Windows (the vendored dependencies are Windows builds):

   ```
   python build_addon.py --repo-tag v1.1.38
   ```

   This writes `releases/bfa_coworker-1.1.38.zip`, `releases/bfa_coworker_installer.zip`
   (no version in the installer's name; its manifest carries 1.1.38)
   and updates `extensions/index.json` to point at
   `https://github.com/Draise14/bfa_coworker/releases/download/v1.1.38/bfa_coworker-1.1.38.zip`.
   (Already built? `python extension_repo.py add releases/bfa_coworker-1.1.38.zip --tag v1.1.38`.)
3. Create the GitHub release `v1.1.38` and attach **both zips, exactly as built**
   (the listing stores the zip's size and SHA-256; a rebuilt zip will not match).
4. Publish the release, **then** merge the PR that changes `extensions/index.json`.
   From that moment every installed copy sees the update (raw.githubusercontent.com
   caches for about 5 minutes).

To pull a broken version: point the entry back at the previous release
(`extension_repo.py add <previous zip> --tag <previous tag>`) or add the id to
`blocklist` with a reason.

## Installing (for users)

- **Easiest:** download `bfa_coworker_installer.zip` -- always the newest at
  https://github.com/Draise14/bfa_coworker/releases/latest/download/bfa_coworker_installer.zip --
  and drag it into Blender. It adds this repository and installs Coworker into it.
- **Manually:** Preferences > Get Extensions > Repositories > Add Remote Repository,
  paste the URL above, enable *Check for Updates on Startup*, then install Coworker
  from Get Extensions.
- **Drag a link:** a link such as
  `https://github.com/Draise14/bfa_coworker/releases/download/v1.1.37/bfa_coworker-1.1.37.zip?repository=https%3A%2F%2Fraw.githubusercontent.com%2FDraise14%2Fbfa_coworker%2Fmain%2Fextensions%2Findex.json&blender_version_min=5.1.0`
  dragged from a browser into Blender offers "Add Repository..." and then installs.
  `extension_repo.py add` prints this link for each release.
