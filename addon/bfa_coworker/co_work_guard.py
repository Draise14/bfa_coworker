# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Soft co-work scene lock (scene safety Phase 1).

While a conversation turn is active the coworker runs on a background
thread and the user stays fully interactive, so the user can re-select or
re-target objects the agent is working on between tool calls.  That is the
root cause of the ``Operator ...poll() failed, context is incorrect`` class
of error: the agent's next script acts on a selection/active object that no
longer matches its assumptions.

The lock sets ``hide_select`` on the objects and collections the coworker
created or touched for the duration of the turn.  ``hide_select`` gates UI
picking only; programmatic ``obj.select_set()`` and ``bpy.ops`` context
overrides still work, so the agent is unaffected while the user cannot
re-select the managed datablocks in the viewport/outliner.  Everything is
restored when the turn ends (including on stop, error, or exception).

The registry is *session state* held in this module, not scene state: a
Blender crash cannot leave objects permanently locked, and ``unregister``
clears it.  This module is deliberately free of ``bpy`` -- it only produces
toolcode strings and manages plain dicts -- so it is unit-testable outside
Blender.
"""

__all__ = (
    "is_locked",
    "managed_names",
    "record_managed",
    "record_prior",
    "clear",
    "build_lock_code",
    "build_unlock_code",
)

# Managed datablock names -> the ``hide_select`` value they had before the
# lock, so the unlock can restore the exact prior state (not a blanket
# False, which would clobber a user's own hidden-from-selection object).
_MANAGED_OBJECTS: dict[str, bool] = {}
_MANAGED_COLLECTIONS: dict[str, bool] = {}
_LOCKED: bool = False


def is_locked() -> bool:
    """True while the coworker holds at least one managed datablock locked."""
    return _LOCKED


def managed_names() -> tuple[set[str], set[str]]:
    """Return ``(object_names, collection_names)`` currently managed."""
    return set(_MANAGED_OBJECTS), set(_MANAGED_COLLECTIONS)


def record_managed(
    objects: object = (),
    collections: object = (),
) -> None:
    """Register *objects*/*collections* as managed.

    New names are recorded with an assumed prior ``hide_select = False``;
    :func:`record_prior` then replaces that with the true prior value once
    the lock toolcode reports it.  Idempotent.
    """
    for name in objects or ():
        if name and name not in _MANAGED_OBJECTS:
            _MANAGED_OBJECTS[str(name)] = False
    for name in collections or ():
        if name and name not in _MANAGED_COLLECTIONS:
            _MANAGED_COLLECTIONS[str(name)] = False
    global _LOCKED
    _LOCKED = bool(_MANAGED_OBJECTS or _MANAGED_COLLECTIONS)


def record_prior(
    prior_objects: object = None,
    prior_collections: object = None,
) -> None:
    """Store the true prior ``hide_select`` values reported by the lock code.

    Best-effort: unknown names are ignored.  Never raises.
    """
    try:
        if isinstance(prior_objects, dict):
            for name, value in prior_objects.items():
                if name in _MANAGED_OBJECTS:
                    _MANAGED_OBJECTS[name] = bool(value)
        if isinstance(prior_collections, dict):
            for name, value in prior_collections.items():
                if name in _MANAGED_COLLECTIONS:
                    _MANAGED_COLLECTIONS[name] = bool(value)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def clear() -> None:
    """Forget every managed datablock (call after unlocking)."""
    global _LOCKED
    _MANAGED_OBJECTS.clear()
    _MANAGED_COLLECTIONS.clear()
    _LOCKED = False


def build_lock_code(
    object_names: object = (),
    collection_names: object = (),
) -> str:
    """Return toolcode that locks *object_names*/*collection_names*.

    The code records each datablock's prior ``hide_select`` into the
    ``result`` dict (``prior_hide_select`` / ``prior_hide_select_coll``) so
    the caller can restore it exactly, and sets ``hide_select = True``.
    Carries the ``blmcp-toolcode-skip-preflight`` marker so it runs inline
    on the main thread, like the other internally generated payloads.
    """
    objs = repr(sorted(str(n) for n in (object_names or ()) if n))
    colls = repr(sorted(str(n) for n in (collection_names or ()) if n))
    return (
        "# blmcp-toolcode-skip-preflight\n"
        "import bpy\n"
        "_objs = " + objs + "\n"
        "_colls = " + colls + "\n"
        "_prior = {}\n"
        "_prior_coll = {}\n"
        "_n = 0\n"
        "for _name in _objs:\n"
        "    _o = bpy.data.objects.get(_name)\n"
        "    if _o is None:\n"
        "        continue\n"
        "    _prior[_name] = bool(_o.hide_select)\n"
        "    _o.hide_select = True\n"
        "    _n += 1\n"
        "for _name in _colls:\n"
        "    _c = bpy.data.collections.get(_name)\n"
        "    if _c is None:\n"
        "        continue\n"
        "    _prior_coll[_name] = bool(_c.hide_select)\n"
        "    _c.hide_select = True\n"
        "    _n += 1\n"
        "result = {'status': 'ok', 'locked': _n, "
        "'prior_hide_select': _prior, 'prior_hide_select_coll': _prior_coll}\n"
    )


def build_unlock_code() -> str:
    """Return toolcode that restores every managed ``hide_select`` value."""
    priors = repr({k: bool(v) for k, v in _MANAGED_OBJECTS.items()})
    coll_priors = repr({k: bool(v) for k, v in _MANAGED_COLLECTIONS.items()})
    return (
        "# blmcp-toolcode-skip-preflight\n"
        "import bpy\n"
        "_priors = " + priors + "\n"
        "_coll_priors = " + coll_priors + "\n"
        "_n = 0\n"
        "for _name, _val in _priors.items():\n"
        "    _o = bpy.data.objects.get(_name)\n"
        "    if _o is None:\n"
        "        continue\n"
        "    _o.hide_select = bool(_val)\n"
        "    _n += 1\n"
        "for _name, _val in _coll_priors.items():\n"
        "    _c = bpy.data.collections.get(_name)\n"
        "    if _c is None:\n"
        "        continue\n"
        "    _c.hide_select = bool(_val)\n"
        "    _n += 1\n"
        "result = {'status': 'ok', 'unlocked': _n}\n"
    )
