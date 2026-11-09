# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Carry Coworker's preferences across install locations (issue #86).

Blender stores an add-on's preferences under its full module path, which for
an extension includes the repository: ``bl_ext.user_default.bfa_coworker`` and
``bl_ext.Bforartists_Coworker.bfa_coworker`` are two different add-ons as far
as preferences go.  Moving Coworker into the update repository (the Coworker
Installer does this once) would otherwise reset the API key, model paths,
ports, ...

So the preferences are written to a small JSON file in Blender's user config
folder whenever the add-on is disabled (upgrade, uninstall, quit), and a fresh
install with untouched preferences reads them back once.

Values are set like any setting (``setattr``): since Blender 5.0 the property
storage (``prefs[key] = value``) no longer reaches ``bpy.props`` values (#396).
Some update callbacks auto-fill *other* fields (model presets, provider URLs),
so those settings are set first and the fields they fill are restored after
them, ending with their saved values.

Also used by the Coworker Installer (it has its own copy of ``dump``) to save
the settings of an old copy before replacing it.  ``bpy`` is only touched
through the objects passed in, so this module is testable without Blender.

The backup keeps your API keys and Hugging Face token on purpose (that is its
job), in their own ``secrets`` field (#346).  The file is readable by your
account only (mode 600 on Linux / macOS; on Windows Blender's config folder is
in your user profile, which is already private), and its contents are never
printed or logged.
"""

from __future__ import annotations

import json
import os
from typing import Any

__all__ = (
    "FILENAME",
    "RESTORED_FLAG",
    "settings_path",
    "dump",
    "apply",
    "save",
    "load",
    "restore_once",
)

FILENAME = "bfa_coworker_settings.json"
# Hidden BoolProperty on the preferences: True once this install has either
# restored a backup or decided there was nothing to restore.
RESTORED_FLAG = "settings_restored"

# Never carried over: bookkeeping, UI-only toggles, and live status text.
_SKIP = {"rna_type", RESTORED_FLAG, "bl_idname"}
_SKIP_PREFIXES = ("show_", "ui_", "_")
_VALUE_TYPES = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}
# Restored first: their update callbacks fill in other fields (mode -> agent / llm mode,
# preset -> model, context and token fields, provider -> URL), which come after.
_DRIVERS = ("operating_mode", "llm_mode", "remote_provider", "model_preset", "local_ctx_preset")
# Kept in the backup's "secrets" field (#346): these, and any other PASSWORD field.
SECRET_KEYS = ("remote_api_key", "hf_token", "gen_remote_key")


def _is_secret(prop: Any) -> bool:
    return prop.identifier in SECRET_KEYS or getattr(prop, "subtype", "") == "PASSWORD"


def _write_private(path: str, text: str) -> None:
    """Like ``session_identity.write_private`` (this module also loads on its own):
    mode 600 from creation on POSIX; Windows ignores it (the user profile is private)."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        os.chmod(tmp, 0o600)    # a .tmp left over from before keeps its old mode
    except OSError:
        pass
    os.replace(tmp, path)


def settings_path(config_dir: str) -> str:
    return os.path.join(config_dir, FILENAME)


def _props(prefs: Any) -> list[Any]:
    out = []
    for prop in prefs.bl_rna.properties:
        ident = prop.identifier
        if ident in _SKIP or ident.startswith(_SKIP_PREFIXES):
            continue
        if prop.type not in _VALUE_TYPES or getattr(prop, "is_readonly", False):
            continue
        if getattr(prop, "is_array", False) or getattr(prop, "array_length", 0):
            continue
        out.append(prop)
    return out


def dump(prefs: Any) -> dict[str, Any]:
    """Plain-JSON snapshot of every user setting on *prefs*."""
    data: dict[str, Any] = {}
    for prop in _props(prefs):
        try:
            value = getattr(prefs, prop.identifier)
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if prop.type == "ENUM" and getattr(prop, "is_enum_flag", False):
            value = sorted(value)
        if isinstance(value, (bool, int, float, str, list)):
            data[prop.identifier] = value
    return data


def apply(prefs: Any, data: dict[str, Any]) -> list[str]:
    """Set *data* on *prefs*, the fields-filling settings (:data:`_DRIVERS`) first.

    Unknown keys (older/newer versions), bad types and enum identifiers that
    no longer exist are skipped; a setting that can't be set is printed.
    Returns the keys that were applied.
    """
    applied: list[str] = []
    props = {p.identifier: p for p in _props(prefs)}
    order = [k for k in _DRIVERS if k in data] + [k for k in data if k not in _DRIVERS]
    for key in order:
        value, prop = data[key], props.get(key)
        if prop is None:
            continue
        try:
            if prop.type == "ENUM":
                if getattr(prop, "is_enum_flag", False):
                    continue  # rare; not used by Coworker's preferences
                if not isinstance(value, str) or value not in {item.identifier for item in prop.enum_items}:
                    continue  # dynamic enum or renamed option: keep the default
            elif prop.type == "BOOLEAN":
                value = bool(value)
            elif prop.type == "INT":
                if isinstance(value, bool) or not isinstance(value, int):
                    continue
            elif prop.type == "FLOAT":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                value = float(value)
            elif prop.type != "STRING" or not isinstance(value, str):
                continue
            setattr(prefs, key, value)
            applied.append(key)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            # A secret's error could quote its value: name the error only (#346).
            reason = type(ex).__name__ if _is_secret(prop) else str(ex)
            print("[Coworker] settings restore: {:s} not restored ({:s})".format(key, reason))
    return applied


def save(prefs: Any, config_dir: str, version: str = "") -> bool:
    """Write the settings backup, secrets in their own field, owner-only.  Never raises."""
    try:
        os.makedirs(config_dir, exist_ok=True)
        settings = dump(prefs)
        secret_keys = {p.identifier for p in _props(prefs) if _is_secret(p)}
        secrets = {k: settings.pop(k) for k in sorted(secret_keys) if k in settings}
        payload = {"format": 2, "version": version, "settings": settings, "secrets": secrets}
        _write_private(settings_path(config_dir), json.dumps(payload, indent=1, sort_keys=True))
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def load(config_dir: str) -> dict[str, Any]:
    """The saved settings, secrets included (format 1, from older copies and the
    Installer, kept them among the settings)."""
    try:
        with open(settings_path(config_dir), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return {}
        settings, secrets = payload.get("settings"), payload.get("secrets")
        out = dict(settings) if isinstance(settings, dict) else {}
        if isinstance(secrets, dict):
            out.update(secrets)
        return out
    except (OSError, ValueError):
        return {}


def restore_once(prefs: Any, config_dir: str) -> list[str]:
    """Restore the backup into a FRESH install, once.

    A fresh install is one whose ``settings_restored`` flag is still False.
    Upgrades in place keep their preferences and already have the flag set,
    so they are never overwritten by an older backup.
    """
    try:
        if getattr(prefs, RESTORED_FLAG, True):
            return []
        applied = apply(prefs, load(config_dir))
        setattr(prefs, RESTORED_FLAG, True)
        return applied
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] settings restore: {:s}".format(str(ex)))
        return []
