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

Values are written straight into the property storage (``prefs[key] = value``)
so the preferences' update callbacks -- which auto-fill *other* fields (model
presets, provider URLs) -- don't run and overwrite what is being restored.

Also used by the Coworker Installer (it has its own copy of ``dump``) to save
the settings of an old copy before replacing it.  ``bpy`` is only touched
through the objects passed in, so this module is testable without Blender.
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
    """Write *data* into *prefs* without firing update callbacks.

    Unknown keys (older/newer versions), bad types and enum identifiers that
    no longer exist are skipped.  Returns the keys that were applied.
    """
    applied: list[str] = []
    props = {p.identifier: p for p in _props(prefs)}
    for key, value in data.items():
        prop = props.get(key)
        if prop is None:
            continue
        try:
            if prop.type == "ENUM":
                if getattr(prop, "is_enum_flag", False):
                    continue  # rare; not used by Coworker's preferences
                items = {item.identifier: item.value for item in prop.enum_items}
                if not isinstance(value, str) or value not in items:
                    continue  # dynamic enum or renamed option: keep the default
                prefs[key] = items[value]
            elif prop.type == "BOOLEAN":
                prefs[key] = bool(value)
            elif prop.type == "INT":
                if isinstance(value, bool) or not isinstance(value, int):
                    continue
                prefs[key] = int(value)
            elif prop.type == "FLOAT":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                prefs[key] = float(value)
            elif prop.type == "STRING":
                if not isinstance(value, str):
                    continue
                prefs[key] = value
            else:
                continue
            applied.append(key)
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return applied


def save(prefs: Any, config_dir: str, version: str = "") -> bool:
    """Write the settings backup.  Never raises."""
    try:
        os.makedirs(config_dir, exist_ok=True)
        payload = {"format": 1, "version": version, "settings": dump(prefs)}
        path = settings_path(config_dir)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def load(config_dir: str) -> dict[str, Any]:
    try:
        with open(settings_path(config_dir), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        settings = payload.get("settings") if isinstance(payload, dict) else None
        return settings if isinstance(settings, dict) else {}
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
        prefs[RESTORED_FLAG] = True
        return applied
    except Exception:  # pylint: disable=broad-exception-caught
        return []
