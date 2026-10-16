# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Coworker Installer -- one download that installs Coworker *and* keeps it
updated through Blender's own extension system (issue #86).

Drag the installer zip into Blender / Bforartists (or Install from Disk).
When it is enabled the first time it:

1. adds the **Bforartists Coworker** online repository (with "Check for
   Updates on Startup" on);
2. installs the Coworker copy bundled inside this zip into that repository --
   offline, nothing is downloaded -- and enables it;
3. if an older Coworker was installed from a file, saves its settings (API
   keys, model paths, ports...) for the new copy and replaces it.

From then on new Coworker versions appear in Preferences > Get Extensions
like any other extension.  The installer's preferences show the status and
offer repairs: re-add the repository, reinstall the bundled Coworker (also a
rollback), uninstall Coworker only, or uninstall everything.

Blender gives add-ons no "on uninstall" hook, so removing the installer from
the extensions list leaves Coworker and its repository in place -- use
*Uninstall Everything* here to remove all three.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty  # pylint: disable=import-error

__all__ = (
    "register",
    "unregister",
    "REPO_URL",
    "REPO_NAME",
    "PKG_ID",
    "SETTINGS_FILE",
    "PAYLOAD_DIR",
    "payload_zip",
    "payload_version",
    "find_repo",
    "installed_copies",
    "backup_settings",
    "ensure_repo",
    "install_bundled",
    "uninstall_copy",
    "sync_repo",
    "setup",
    "BFACWI_Preferences",
    "BFACWI_OT_setup",
    "BFACWI_OT_reinstall",
    "BFACWI_OT_uninstall_coworker",
    "BFACWI_OT_uninstall_all",
)

REPO_URL = "https://raw.githubusercontent.com/Draise14/bfa_coworker/main/extensions/index.json"
REPO_NAME = "Bforartists Coworker"
PKG_ID = "bfa_coworker"
SETTINGS_FILE = "bfa_coworker_settings.json"   # same file Coworker itself uses
PAYLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "payload")

_SETUP_DELAY = 1.0


# ---------------------------------------------------------------------------
# Small helpers


def _version_tuple(text: str) -> tuple[int, ...]:
    out = []
    for part in str(text or "").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _read_manifest_version(directory: str) -> str:
    try:
        import tomllib
        with open(os.path.join(directory, "blender_manifest.toml"), "rb") as fh:
            return str(tomllib.load(fh).get("version", ""))
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def payload_zip() -> str:
    """The Coworker zip bundled inside this installer ('' if missing).

    The file name carries no version (``payload/bfa_coworker.zip``); the
    version is read from the zip's own manifest.  Older versioned names
    (``bfa_coworker-<version>.zip``) are still found.
    """
    found = sorted(glob.glob(os.path.join(PAYLOAD_DIR, PKG_ID + "*.zip")),
                   key=lambda p: _version_tuple(_zip_manifest_version(p)))
    return found[-1] if found else ""


def _zip_manifest_version(path: str) -> str:
    try:
        import tomllib
        import zipfile
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.endswith("blender_manifest.toml") and n.count("/") <= 1]
            if not names:
                return ""
            return str(tomllib.loads(zf.read(sorted(names, key=len)[0]).decode("utf-8")).get("version", ""))
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def payload_version() -> str:
    path = payload_zip()
    return _zip_manifest_version(path) if path else ""


def _repos() -> Any:
    return bpy.context.preferences.extensions.repos


def find_repo() -> tuple[int, Any]:
    """(index, repo) of the Coworker update repository, or (-1, None)."""
    for i, repo in enumerate(_repos()):
        if repo.use_remote_url and repo.remote_url.rstrip("/") == REPO_URL:
            return i, repo
    return -1, None


def installed_copies() -> list[dict[str, Any]]:
    """Every installed Coworker: {index, repo, version, module, enabled}."""
    out = []
    enabled = set(bpy.context.preferences.addons.keys())
    for i, repo in enumerate(_repos()):
        try:
            directory = repo.directory
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if not directory:
            continue
        pkg_dir = os.path.join(directory, PKG_ID)
        if not os.path.isfile(os.path.join(pkg_dir, "blender_manifest.toml")):
            continue
        module = "bl_ext.{:s}.{:s}".format(repo.module, PKG_ID)
        out.append({"index": i, "repo": repo, "version": _read_manifest_version(pkg_dir),
                    "module": module, "enabled": module in enabled})
    return out


def _settings_dir() -> str:
    return os.path.join(bpy.utils.user_resource('CONFIG'), "bfa_coworker")


def backup_settings(module: str) -> bool:
    """Save an enabled Coworker's preferences for the copy that replaces it.

    Coworker 1.1.37+ also does this itself when it is disabled; this covers
    older copies.  Same format as Coworker's settings_transfer module.
    """
    addon = bpy.context.preferences.addons.get(module)
    prefs = getattr(addon, "preferences", None) if addon else None
    if prefs is None:
        return False
    data: dict[str, Any] = {}
    for prop in prefs.bl_rna.properties:
        ident = prop.identifier
        if ident in ("rna_type", "settings_restored", "bl_idname") or ident.startswith(("show_", "ui_", "_")):
            continue
        if prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"} or prop.is_readonly:
            continue
        if getattr(prop, "is_array", False):
            continue
        try:
            value = getattr(prefs, ident)
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if isinstance(value, (bool, int, float, str)):
            data[ident] = value
    try:
        os.makedirs(_settings_dir(), exist_ok=True)
        path = os.path.join(_settings_dir(), SETTINGS_FILE)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"format": 1, "version": "", "settings": data}, fh, indent=1, sort_keys=True)
        os.replace(path + ".tmp", path)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Actions (each returns a list of human-readable messages)


def ensure_repo() -> tuple[int, Any, bool]:
    index, repo = find_repo()
    if repo is not None:
        if not repo.enabled:
            repo.enabled = True
        return index, repo, False
    bpy.ops.preferences.extension_repo_add(
        name=REPO_NAME, remote_url=REPO_URL, type='REMOTE', use_sync_on_startup=True)
    index, repo = find_repo()
    if repo is None:
        raise RuntimeError("Blender did not add the repository")
    return index, repo, True


def install_bundled(repo: Any) -> None:
    path = payload_zip()
    if not path:
        raise RuntimeError("This installer has no bundled Coworker zip")
    # A freshly added remote repository has no folder until its first sync
    # (never, while online access is off) and Blender's installer refuses a
    # missing destination -- create it.
    if repo.directory:
        os.makedirs(repo.directory, exist_ok=True)
    result = bpy.ops.extensions.package_install_files(
        filepath=path, repo=repo.module, enable_on_install=True)
    if "FINISHED" not in result:
        raise RuntimeError("Installing the bundled Coworker failed ({:s})".format(str(result)))


# NOTE: the extensions operators' ``repo_index`` counts only ENABLED
# repositories (``extension_repos_read_index``), not the position in
# ``preferences.extensions.repos`` -- a disabled repository earlier in the list
# would make an index point at the wrong one.  Address repositories by their
# directory instead (``repo_index`` left at its default of -1).


def uninstall_copy(copy: dict[str, Any]) -> None:
    result = bpy.ops.extensions.package_uninstall(repo_directory=copy["repo"].directory, pkg_id=PKG_ID)
    if "FINISHED" not in result:
        raise RuntimeError("Uninstalling Coworker {:s} failed ({:s})".format(copy["version"], str(result)))


def sync_repo(repo: Any) -> bool:
    """Fetch the listing so newer versions show up now (needs online access)."""
    if repo is None or not getattr(bpy.app, "online_access", False):
        return False
    try:
        os.makedirs(repo.directory, exist_ok=True)
        bpy.ops.extensions.repo_sync(repo_directory=repo.directory)
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def setup(reinstall: bool = False) -> list[str]:
    """Add the repository and make sure Coworker lives in it.

    * Coworker already in the repository -> leave it (it may be newer than the
      bundled copy) unless *reinstall* is set (rollback / repair).
    * An older or equal copy installed from a file -> settings saved, replaced.
    * A NEWER copy installed from a file (e.g. a development build) -> left
      alone; *Reinstall Bundled* replaces it on request.
    """
    msgs: list[str] = []
    index, repo, created = ensure_repo()
    msgs.append("Added the {:s} repository".format(REPO_NAME) if created
                else "{:s} repository is set up".format(REPO_NAME))
    copies = installed_copies()
    ours = [c for c in copies if c["repo"].module == repo.module]
    others = [c for c in copies if c["repo"].module != repo.module]
    bundled = payload_version()

    if ours and not reinstall:
        msgs.append("Coworker {:s} is installed and updates automatically".format(ours[0]["version"]))
    else:
        newer = [c for c in others if _version_tuple(c["version"]) > _version_tuple(bundled)]
        if newer and not reinstall:
            msgs.append("A newer Coworker {:s} is installed from a file (a development build?) -- "
                        "left as is; Reinstall Bundled replaces it".format(newer[0]["version"]))
            sync_repo(repo)
            return msgs
        for copy in others:
            if copy["enabled"]:
                backup_settings(copy["module"])
            uninstall_copy(copy)
            msgs.append("Replaced Coworker {:s} installed from a file (settings kept)".format(copy["version"]))
        if ours and reinstall:
            uninstall_copy(ours[0])
        install_bundled(repo)
        msgs.append("Installed Coworker {:s}".format(bundled))
    if sync_repo(repo):
        msgs.append("Checked for updates")
    elif not getattr(bpy.app, "online_access", False):
        msgs.append("Online access is off: allow it in Preferences > System to get updates")
    return msgs


# ---------------------------------------------------------------------------
# Preferences UI & operators


class BFACWI_Preferences(bpy.types.AddonPreferences):  # type: ignore[misc]
    bl_idname = __package__

    auto_setup: BoolProperty(  # type: ignore[valid-type]
        name="Set Up Automatically",
        description="When enabled for the first time, add the update repository and install the bundled Coworker",
        default=True,
    )
    setup_done: BoolProperty(  # type: ignore[valid-type]
        name="Setup Done", default=False, options={'HIDDEN'})

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        _index, repo = find_repo()
        copies = installed_copies()
        box = layout.box()
        col = box.column(align=True)
        if repo is not None:
            col.label(text="Repository: {:s}".format(repo.name), icon='CHECKMARK')
            if not repo.use_sync_on_startup:
                col.label(text="Tip: turn on \"Check for Updates on Startup\" for it in Get Extensions",
                          icon='INFO')
        else:
            col.label(text="Repository: not added", icon='ERROR')
        ours = [c for c in copies if repo is not None and c["repo"].module == repo.module]
        for c in copies:
            where = "updates automatically" if c in ours else "installed from a file -- no updates"
            col.label(text="Coworker {:s} ({:s}){:s}".format(
                c["version"], where, "" if c["enabled"] else ", disabled"),
                icon='CHECKMARK' if c in ours else 'INFO')
        if not copies:
            col.label(text="Coworker: not installed", icon='ERROR')
        col.label(text="Bundled copy: Coworker {:s}".format(payload_version() or "missing"), icon='PACKAGE')
        if not getattr(bpy.app, "online_access", False):
            col.label(text="Online access is off -- updates need it (Preferences > System)", icon='INTERNET_OFFLINE')

        layout.prop(self, "auto_setup")
        row = layout.row(align=True)
        row.operator("bfacw_installer.setup", text="Re-add Repository & Install", icon='FILE_REFRESH')
        row.operator("bfacw_installer.reinstall", text="Reinstall Bundled {:s}".format(payload_version()),
                     icon='LOOP_BACK')
        row = layout.row(align=True)
        row.operator("bfacw_installer.uninstall_coworker", text="Uninstall Coworker", icon='TRASH')
        row.operator("bfacw_installer.uninstall_all", text="Uninstall Everything", icon='CANCEL')


def _report_all(op: bpy.types.Operator, msgs: list[str]) -> None:
    for msg in msgs:
        op.report({'INFO'}, msg)
        print("[Coworker Installer] " + msg)


class BFACWI_OT_setup(bpy.types.Operator):  # type: ignore[misc]
    """Add the Coworker update repository and install Coworker into it if missing"""
    bl_idname = "bfacw_installer.setup"
    bl_label = "Set Up Coworker Updates"

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            _report_all(self, setup())
        except Exception as ex:  # pylint: disable=broad-exception-caught
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class BFACWI_OT_reinstall(bpy.types.Operator):  # type: ignore[misc]
    """Replace the installed Coworker with the copy bundled in this installer (repair or roll back)"""
    bl_idname = "bfacw_installer.reinstall"
    bl_label = "Reinstall Bundled Coworker"

    def invoke(self, context: bpy.types.Context, event: Any) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            _report_all(self, setup(reinstall=True))
        except Exception as ex:  # pylint: disable=broad-exception-caught
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class BFACWI_OT_uninstall_coworker(bpy.types.Operator):  # type: ignore[misc]
    """Uninstall Coworker (its settings are kept for a later reinstall); the repository stays"""
    bl_idname = "bfacw_installer.uninstall_coworker"
    bl_label = "Uninstall Coworker"

    def invoke(self, context: bpy.types.Context, event: Any) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        copies = installed_copies()
        try:
            for copy in copies:
                if copy["enabled"]:
                    backup_settings(copy["module"])
                uninstall_copy(copy)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "Uninstalled {:d} Coworker cop{:s}".format(
            len(copies), "y" if len(copies) == 1 else "ies"))
        return {'FINISHED'}


def _uninstall_everything() -> None:
    """Runs from a timer, after the operator returned: removes this add-on too."""
    for copy in installed_copies():
        if copy["enabled"]:
            backup_settings(copy["module"])
        uninstall_copy(copy)
    index, _repo = find_repo()
    if index >= 0:
        bpy.ops.preferences.extension_repo_remove(index=index, remove_files=True)
    parts = (__package__ or "").split(".")
    if len(parts) == 3 and parts[0] == "bl_ext":
        for repo in _repos():
            if repo.module == parts[1]:
                bpy.ops.extensions.package_uninstall(repo_directory=repo.directory, pkg_id=parts[2])
                break
    print("[Coworker Installer] Coworker, its repository and the installer were removed")


class BFACWI_OT_uninstall_all(bpy.types.Operator):  # type: ignore[misc]
    """Uninstall Coworker, remove its update repository, then uninstall this installer"""
    bl_idname = "bfacw_installer.uninstall_all"
    bl_label = "Uninstall Coworker, Repository and Installer"

    def invoke(self, context: bpy.types.Context, event: Any) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        # Run outside this operator: uninstalling the installer unregisters
        # the very class that is executing.
        bpy.app.timers.register(_uninstall_everything, first_interval=0.1)
        self.report({'INFO'}, "Removing Coworker, its repository and the installer...")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# First-run setup


def _first_run() -> None:
    try:
        addon = bpy.context.preferences.addons.get(__package__)
        prefs = getattr(addon, "preferences", None) if addon else None
        if prefs is None or prefs.setup_done or not prefs.auto_setup:
            return None
        for msg in setup():
            print("[Coworker Installer] " + msg)
        prefs.setup_done = True
        bpy.context.preferences.is_dirty = True
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker Installer] setup failed: {:s}".format(str(ex)))
    return None


_classes = (
    BFACWI_Preferences,
    BFACWI_OT_setup,
    BFACWI_OT_reinstall,
    BFACWI_OT_uninstall_coworker,
    BFACWI_OT_uninstall_all,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        # Deferred: Blender may still be finishing the install of this zip.
        bpy.app.timers.register(_first_run, first_interval=_SETUP_DELAY)


def unregister() -> None:
    if bpy.app.timers.is_registered(_first_run):
        bpy.app.timers.unregister(_first_run)
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
