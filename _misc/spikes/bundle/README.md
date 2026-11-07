# Bundle 0 spike (#380): Coworker as a built-in System extension

Checks whether Bforartists can ship Coworker pre-installed in the read-only **System**
extensions repository, and what happens when a newer copy lands in the update repository.
Results are written up in `_misc/plan_tier4x_ship_with_bforartists.md`, section
"Bundle 0 results".

## Files

| File | What it does |
|---|---|
| `prepare.py` | Plain Python. Wipes and lays out the scratch profile, unpacks the release zip into `sysext/system/bfa_coworker` (what a build step would do), builds a "next release" zip with the manifest version bumped |
| `spike_bfa.py` | Runs inside Bforartists (`--background`). `phase1`: enable the built-in copy, set settings, add the update repository, install the next release into it, switch copies, enable both, save prefs. `phase2`: restart with those prefs, try to uninstall the System copy from Python |
| `run_spike.ps1` | Sets the environment, makes the System folder read-only, takes the shared live-test lock, runs both phases 5 s apart, restores the folder, keeps the results |
| `results/readonly`, `results/writable` | The JSON results of the runs written up in the plan |

## Rerun

```powershell
gh release download v.1.1.41 -R Draise14/bfa_coworker -p bfa_coworker-1.1.41.zip -D F:\bfa_coworker_wt\_scratch\bundle-380\dl
powershell -ExecutionPolicy Bypass -File run_spike.ps1 -Zip F:\bfa_coworker_wt\_scratch\bundle-380\dl\bfa_coworker-1.1.41.zip
# A portable (writable) install instead of Program Files:
powershell -ExecutionPolicy Bypass -File run_spike.ps1 -Zip <same zip> -Writable
```

Options: `-Scratch <dir>` (must end in `bundle-380`; default `F:\bfa_coworker_wt\_scratch\bundle-380`),
`-Exe <bforartists.exe>` (default `C:\3D_Stuff\Devbuild\bforartists.exe`).

## Isolation

Nothing touches the real profile. The script sets `BLENDER_USER_RESOURCES` and
`BLENDER_USER_CONFIG / SCRIPTS / EXTENSIONS / DATAFILES` to `<scratch>\res`,
`BLENDER_SYSTEM_EXTENSIONS` to `<scratch>\sysext` (the System repository is its `system`
subfolder; see `appdir.cc` `BLENDER_SYSTEM_EXTENSIONS` and `preferences.cc`
`BKE_preferences_extension_repo_dirpath_get`), and `USERPROFILE`, `HOME`, `APPDATA`,
`LOCALAPPDATA` to `<scratch>\home`, because Coworker keeps caches and logs in
`~/.cache/bfa_coworker`. Both runs use `--offline-mode`.

Read-only is simulated with `icacls <system> /inheritance:r /grant:r <user>:(OI)(CI)(RX)`
and undone with `icacls <system> /reset /T`. Don't use a deny ACE for this: icacls turns
`WD,AD,WEA,WA` into the generic `W`, which includes SYNCHRONIZE, so it blocks reads too. A
deny of `D` blocks reads as well, and a deny of `DC` alone doesn't stop deleting files.

## Reading the output

Each check prints `SPIKE|<key>|<json>`; `snapshot` entries give, for each copy, whether
it's enabled, its `host` / `port` settings, which copy owns the registered operator
(`class_owner`), and how many of each copy's own classes are registered (`registered`, e.g.
`44/44` against `1/44`).
