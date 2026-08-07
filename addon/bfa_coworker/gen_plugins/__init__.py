# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Generative plugin auto-discovery and registry.

Scans ``gen_plugins/<media_type>/`` for ``GenPlugin`` subclasses
and populates ``PLUGIN_REGISTRY``.  Plugins are discovered at import
time — dropping a ``.py`` file into the right folder is all that is
needed to register a new model.
"""

__all__ = (
    "PLUGIN_REGISTRY",
    "discover",
    "get_plugin",
    "get_plugins_by_type",
    "get_enum_items",
)

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

from .base import GenPlugin

# ---------------------------------------------------------------------------
# Registry

#: Map of ``MODEL_ID`` → ``GenPlugin`` instance.
PLUGIN_REGISTRY: dict[str, GenPlugin] = {}

#: Map of ``MODEL_TYPE`` → list of ``(MODEL_ID, DISPLAY_NAME, DESCRIPTION)``
#: tuples suitable for Blender ``EnumProperty`` items.
_ENUM_ITEMS: dict[str, list[tuple[str, str, str]]] = {}

#: Set to ``True`` after the first call to ``discover()``.
_discovered: bool = False


# ---------------------------------------------------------------------------
# Discovery

def discover() -> None:
    """Scan ``gen_plugins/`` for ``GenPlugin`` subclasses.

    Idempotent — subsequent calls are no-ops.  Plugins are loaded
    via ``importlib`` so they can use relative imports to access
    shared utilities.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True

    plugins_dir = Path(__file__).resolve().parent

    for py_file in sorted(plugins_dir.rglob("*.py")):
        # Skip private modules, templates, and the base/init files.
        if py_file.name.startswith("_"):
            continue
        if py_file.parent.name.startswith("_"):
            continue
        if py_file.name == "base.py" or py_file.name == "__init__.py":
            continue

        # Build a synthetic module name so relative imports work.
        # e.g. gen_plugins/image/flux_klein_9b.py
        #   → bfa_coworker.gen_plugins.image.flux_klein_9b
        rel = py_file.relative_to(plugins_dir.parent.parent)
        mod_name = "bfa_coworker." + str(
            rel.with_suffix("")
        ).replace("\\", ".").replace("/", ".")

        try:
            spec = importlib.util.spec_from_file_location(
                mod_name, str(py_file)
            )
            if spec is None or spec.loader is None:
                print(
                    "[🛠️Coworker] gen_plugins: cannot load spec for {:s}".format(
                        str(py_file)
                    )
                )
                continue

            mod = importlib.util.module_from_spec(spec)

            # Register synthetic parent packages so relative imports
            # like ``from ...gen_controller import ...`` resolve.
            _register_synthetic_parents(mod_name)

            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)

            # Find all GenPlugin subclasses in the module.
            for _name, obj in vars(mod).items():
                if not isinstance(obj, type):
                    continue
                if obj is GenPlugin:
                    continue
                if not issubclass(obj, GenPlugin):
                    continue
                if not obj.MODEL_ID:
                    continue

                instance = obj()
                _validate_plugin(instance)
                PLUGIN_REGISTRY[instance.MODEL_ID] = instance

                # Build enum items.
                media_type = instance.MODEL_TYPE
                if media_type not in _ENUM_ITEMS:
                    _ENUM_ITEMS[media_type] = []
                _ENUM_ITEMS[media_type].append(
                    (
                        instance.MODEL_ID,
                        instance.DISPLAY_NAME,
                        instance.DESCRIPTION,
                    )
                )

                print(
                    "[🛠️Coworker] gen_plugins: registered {:s} ({:s})".format(
                        instance.MODEL_ID, instance.MODEL_TYPE
                    )
                )

        except Exception as ex:
            print(
                "[🛠️Coworker] gen_plugins: error loading {:s}: {:s}".format(
                    str(py_file), str(ex)
                )
            )


def _register_synthetic_parents(mod_name: str) -> None:
    """Register synthetic parent packages in ``sys.modules``.

    This allows plugins to use relative imports like
    ``from ...gen_controller import GenController`` even though
    the parent packages may not have been imported yet.
    """
    parts = mod_name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            # Create a minimal namespace package.
            import types
            ns = types.ModuleType(parent)
            ns.__path__ = []  # type: ignore[attr-defined]
            ns.__package__ = parent
            sys.modules[parent] = ns


def _validate_plugin(plugin: GenPlugin) -> None:
    """Check that a plugin has all required attributes."""
    if not plugin.MODEL_ID:
        raise ValueError("Plugin has empty MODEL_ID")
    if not plugin.DISPLAY_NAME:
        raise ValueError(
            "Plugin {:s} has empty DISPLAY_NAME".format(plugin.MODEL_ID)
        )
    if not plugin.MODEL_TYPE:
        raise ValueError(
            "Plugin {:s} has empty MODEL_TYPE".format(plugin.MODEL_ID)
        )
    valid_types = {"image", "video", "audio", "text", "3d"}
    if plugin.MODEL_TYPE not in valid_types:
        raise ValueError(
            "Plugin {:s} has unknown MODEL_TYPE '{:s}'".format(
                plugin.MODEL_ID, plugin.MODEL_TYPE
            )
        )


# ---------------------------------------------------------------------------
# Public accessors

def get_plugin(model_id: str) -> GenPlugin | None:
    """Return the plugin for *model_id*, or ``None``."""
    discover()
    return PLUGIN_REGISTRY.get(model_id)


def get_plugins_by_type(model_type: str) -> list[GenPlugin]:
    """Return all plugins of the given *model_type*."""
    discover()
    return [
        p for p in PLUGIN_REGISTRY.values()
        if p.MODEL_TYPE == model_type
    ]


def get_enum_items(model_type: str) -> list[tuple[str, str, str]]:
    """Return ``EnumProperty`` items for *model_type*.

    Suitable for use as the ``items`` parameter of a Blender
    ``EnumProperty``.  Returns a static list — callers should not
    modify it.
    """
    discover()
    return _ENUM_ITEMS.get(model_type, [])


# ---------------------------------------------------------------------------
# Re-export for convenience

def _reload_registry() -> None:
    """Clear the registry and re-discover (for development)."""
    global _discovered
    PLUGIN_REGISTRY.clear()
    _ENUM_ITEMS.clear()
    _discovered = False
    discover()