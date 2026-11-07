# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Bundle 0 spike (#380), the Bforartists side.  Run by ``run_spike.ps1``:

    bforartists --background --offline-mode --python spike_bfa.py -- <phase> <results dir>

phase1: the built-in copy (System repository) loads and enables offline; settings set;
        the update repository is added and the "next release" installed into it from a
        file; clean switch System -> repository -> System with settings; then both copies
        enabled at once; preferences saved with both enabled.
phase2: a restart with those preferences: which copy wins, what persisted; the System
        repository's read-only behaviour (uninstall from Python).

Prints ``SPIKE|<key>|<json>`` lines and writes ``results_<phase>.json``.
"""

import json
import os
import sys
import time
import traceback

import bpy
import addon_utils

PKG = "bfa_coworker"
M_SYS = "bl_ext.system." + PKG
REPO_NAME = "Bforartists Coworker"
REPO_URL = "https://raw.githubusercontent.com/Draise14/bfa_coworker/main/extensions/index.json"
PROBE_CLASSES = ("_BFACW_Preferences", "BFACW_OT_copy_update_url")
SPIKE_HOST = "spike.host.380"
SPIKE_PORT = 9381

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PHASE = argv[0] if argv else "phase1"
OUT_DIR = argv[1] if len(argv) > 1 else os.path.dirname(os.path.abspath(__file__))
RESULTS = {}


def record(key, value):
    RESULTS[key] = value
    print("SPIKE|{:s}|{:s}".format(key, json.dumps(value, default=str)), flush=True)


def step(name, fn):
    print("\n===== SPIKE STEP: {:s} =====".format(name), flush=True)
    try:
        fn()
    except Exception as ex:  # keep going, record the failure
        record(name + ".EXCEPTION", "{:s}: {:s}".format(type(ex).__name__, str(ex)))
        traceback.print_exc()


def repos():
    out = []
    for r in bpy.context.preferences.extensions.repos:
        out.append({
            "name": r.name, "module": r.module, "source": r.source, "enabled": r.enabled,
            "use_remote_url": r.use_remote_url, "remote_url": r.remote_url,
            "use_sync_on_startup": r.use_sync_on_startup, "directory": r.directory,
        })
    return out


def update_repo():
    for r in bpy.context.preferences.extensions.repos:
        if r.use_remote_url and r.remote_url.rstrip("/") == REPO_URL:
            return r
    return None


def m_repo():
    r = update_repo()
    return "bl_ext.{:s}.{:s}".format(r.module, PKG) if r else None


def copies():
    out = []
    for mod in addon_utils.modules(refresh=True):
        if mod.__name__.split(".")[-1] == PKG:
            info = addon_utils.module_bl_info(mod)
            out.append({"module": mod.__name__, "version": info.get("version"), "file": mod.__file__})
    return out


def prefs_of(module):
    addon = bpy.context.preferences.addons.get(module)
    if addon is None:
        return {"in_prefs_addons": False}
    p = addon.preferences
    if p is None:
        return {"in_prefs_addons": True, "preferences": None}
    return {"in_prefs_addons": True, "host": p.host, "port": p.port,
            "settings_restored": p.settings_restored}


def state(module):
    if module is None:
        return None
    default, loaded = addon_utils.check(module)
    mod = sys.modules.get(module)
    return {"enabled_default": default, "loaded": loaded,
            "addon_enabled_flag": bool(getattr(mod, "__addon_enabled__", False)) if mod else None,
            **prefs_of(module)}


def owners():
    """Which copy's class is the one registered under each probe name."""
    out = {}
    for name in PROBE_CLASSES:
        cls = getattr(bpy.types, name, None)
        if cls is None:
            out[name] = None
            continue
        who = "?"
        for module in (M_SYS, m_repo()):
            mod = sys.modules.get(module or "")
            if mod is not None and any(c is cls for c in getattr(mod, "_classes", ())):
                who = module
        out[name] = who
    return out


def registered():
    """How many of each copy's own classes are registered right now."""
    out = {}
    for module in (M_SYS, m_repo()):
        mod = sys.modules.get(module or "")
        classes = getattr(mod, "_classes", ()) if mod else ()
        out[module] = "{:d}/{:d}".format(
            sum(1 for c in classes if getattr(c, "is_registered", False)), len(classes))
    return out


def snapshot(key):
    record(key, {"sys": state(M_SYS), "repo": state(m_repo()), "class_owner": owners(),
                 "registered": registered(),
                 "prefs_addons": [a.module for a in bpy.context.preferences.addons
                                  if a.module.endswith(PKG)]})


def op(key, fn, **kw):
    try:
        result = fn(**kw)
    except Exception as ex:
        result = "{:s}: {:s}".format(type(ex).__name__, str(ex))
    record(key, sorted(result) if isinstance(result, set) else result)
    return result


def sys_dir():
    for r in bpy.context.preferences.extensions.repos:
        if r.source == 'SYSTEM' and not r.use_remote_url:
            return r.directory
    return ""


def settings_json():
    path = os.path.join(bpy.utils.user_resource('CONFIG'), "bfa_coworker", "bfa_coworker_settings.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    s = data.get("settings", {})
    return {"version": data.get("version"), "host": s.get("host"), "port": s.get("port")}


def vendor_state():
    home_cache = os.path.join(os.path.expanduser("~"), ".cache", "bfa_coworker", "vendor_deps")
    return {"system_vendor_deps_present": os.path.isdir(os.path.join(sys_dir(), PKG, "vendor", "deps")),
            "home_cache_present": os.path.isdir(home_cache)}


# ---------------------------------------------------------------------------

def phase1():
    def env():
        record("app", {"version": bpy.app.version_string, "online_access": bpy.app.online_access,
                       "online_access_override": bpy.app.online_access_override,
                       "user_extensions": bpy.utils.user_resource('EXTENSIONS'),
                       "system_extensions_dir": sys_dir()})
        record("repos_factory", repos())
    step("env", env)

    def q1_listed():
        record("q1.copies_listed", copies())
        snapshot("q1.before_enable")
    step("q1_listed", q1_listed)

    def q1_enable():
        op("q1.enable_system", bpy.ops.preferences.addon_enable, module=M_SYS)
        snapshot("q1.after_enable")
        record("q1.vendor_after_enable", vendor_state())
        p = bpy.context.preferences.addons[M_SYS].preferences
        p.host = SPIKE_HOST
        p.port = SPIKE_PORT
        record("q3.set_on_system", prefs_of(M_SYS))
    step("q1_enable", q1_enable)

    def q1_readonly():
        # Install-from-file can't target the System repository (enum excludes it).
        nxt = next_zip()
        op("q1.install_files_into_system", bpy.ops.extensions.package_install_files,
           filepath=nxt, repo="system", enable_on_install=False)
        record("q1.uninstall_system_poll", bpy.ops.extensions.package_uninstall_system.poll())
    step("q1_readonly", q1_readonly)

    def q5_offline():
        record("q5.online_access", bpy.app.online_access)
        record("q5.package_install_poll", bpy.ops.extensions.package_install.poll())
        record("q5.repo_sync_all_poll", bpy.ops.extensions.repo_sync_all.poll())
        record("q5.package_install_files_poll", bpy.ops.extensions.package_install_files.poll())
    step("q5_offline", q5_offline)

    def q2_add_repo():
        op("q4.repo_add", bpy.ops.preferences.extension_repo_add,
           name=REPO_NAME, remote_url=REPO_URL, type='REMOTE', use_sync_on_startup=True)
        r = update_repo()
        record("q4.repo_after_add", {"name": r.name, "module": r.module, "directory": r.directory,
                                    "use_sync_on_startup": r.use_sync_on_startup})
        os.makedirs(r.directory, exist_ok=True)
        op("q2.install_next_into_repo", bpy.ops.extensions.package_install_files,
           filepath=next_zip(), repo=r.module, enable_on_install=False)
        record("q2.copies_listed", copies())
        record("q2.legacy_error_duplicates", list(addon_utils.error_duplicates))
        warn = addon_utils._extensions_warnings_get()
        record("q2.extension_warnings", {k: v for k, v in warn.items() if k.endswith(PKG)})
        snapshot("q2.after_install")
    step("q2_add_repo", q2_add_repo)

    def q3_clean_switch():
        op("q3.disable_system", bpy.ops.preferences.addon_disable, module=M_SYS)
        record("q3.settings_json_after_disable", settings_json())
        op("q3.enable_repo", bpy.ops.preferences.addon_enable, module=m_repo())
        snapshot("q3.after_switch_to_repo")
        restore_probe()
        # And back: the built-in copy as a fallback.
        op("q3.disable_repo", bpy.ops.preferences.addon_disable, module=m_repo())
        op("q3.enable_system_again", bpy.ops.preferences.addon_enable, module=M_SYS)
        snapshot("q3.after_switch_back")
    step("q3_clean_switch", q3_clean_switch)

    def q2_both():
        # System is enabled; now enable the repository copy too, without disabling.
        op("q2.enable_repo_while_system_on", bpy.ops.preferences.addon_enable, module=m_repo())
        snapshot("q2.both_enabled")
        op("q2.disable_system_while_repo_on", bpy.ops.preferences.addon_disable, module=M_SYS)
        snapshot("q2.after_disabling_system")
        op("q2.enable_system_while_repo_on", bpy.ops.preferences.addon_enable, module=M_SYS)
        snapshot("q2.both_enabled_again")
        # The other order: the copy that owns the classes stays, the other one goes.
        op("q2.disable_repo_while_system_on", bpy.ops.preferences.addon_disable, module=m_repo())
        snapshot("q2.after_disabling_repo")
        op("q2.enable_repo_again", bpy.ops.preferences.addon_enable, module=m_repo())
        snapshot("q2.both_enabled_repo_last")
        bpy.context.preferences.is_dirty = True
        op("save_userpref", bpy.ops.wm.save_userpref)
    step("q2_both", q2_both)


def restore_probe():
    """Why the repository copy came up with default settings, and a write that works.

    Blender 5.0 moved bpy.props storage to "system" ID properties; AddonPreferences
    has no user ID properties any more, so ``prefs[key] = value`` (settings_transfer.apply)
    raises TypeError.  ``bl_system_properties_get(do_create=True)`` is the same storage
    the properties read from, written without update callbacks.
    """
    p = bpy.context.preferences.addons[m_repo()].preferences
    try:
        p["host"] = "probe"
        record("q3.dict_write", "ok")
    except Exception as ex:
        record("q3.dict_write", "{:s}: {:s}".format(type(ex).__name__, str(ex)))
    st = sys.modules[m_repo() + ".settings_transfer"]
    config_dir = os.path.join(bpy.utils.user_resource('CONFIG'), "bfa_coworker")

    class SystemProps:
        def __init__(self, prefs):
            self._prefs = prefs
            self._group = prefs.bl_system_properties_get(do_create=True)

        def __getattr__(self, name):
            return getattr(self._prefs, name)

        def __setitem__(self, key, value):
            self._group[key] = value

    applied = st.apply(SystemProps(p), st.load(config_dir))
    record("q3.system_props_apply", {"applied": len(applied), "host": p.host, "port": p.port})


def phase2():
    def env():
        record("app", {"version": bpy.app.version_string, "online_access": bpy.app.online_access})
        record("repos_after_restart", repos())
        record("copies_listed", copies())
        snapshot("startup_with_both_enabled")
        record("prefs_addons_order", [a.module for a in bpy.context.preferences.addons
                                      if a.module.endswith(PKG)])
        record("vendor_after_restart", vendor_state())
    step("q2_restart", env)

    def q1_uninstall_from_python():
        d = sys_dir()
        op("q1.package_uninstall_system_dir", bpy.ops.extensions.package_uninstall,
           repo_directory=d, pkg_id=PKG)
        record("q1.system_copy_still_present", os.path.isfile(os.path.join(d, PKG, "__init__.py")))
    step("q1_readonly_uninstall", q1_uninstall_from_python)


def next_zip():
    pkgs = os.path.join(os.path.dirname(OUT_DIR.rstrip("\\/")), "pkgs")
    return os.path.join(pkgs, sorted(os.listdir(pkgs))[-1])


def main():
    {"phase1": phase1, "phase2": phase2}[PHASE]()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "results_{:s}.json".format(PHASE)), "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, indent=1, default=str)
    print("SPIKE|done|{:s}".format(json.dumps(PHASE)), flush=True)
    time.sleep(3)  # live-test pacing: settle before quitting
    bpy.ops.wm.quit_blender()


main()
