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
created for the duration of the turn, so the user cannot re-select the
managed datablocks in the viewport/outliner.  Everything is restored when
the turn ends (including on stop, error, or exception).

``hide_select`` gates more than UI picking: ``obj.select_set()`` is ignored
on such an object and operators that need a selection end CANCELLED
(``modifier_apply``), without an error (#548).  So the lock is *lifted*
while one of Coworker's calls runs on Blender's main thread and put back
before the call returns (:func:`lifted`, used by the bridge's sandbox and
around Coworker's own undo steps): the user can't click in between -- the
main thread is busy -- and a click made meanwhile is handled after the lock
is back.  The undo steps Coworker pushes never hold the lock either.

NOTE: only *created* datablocks are locked.  Pre-existing objects that the
agent *modifies* (but does not create) are not tracked by the created-entity
snapshot diff, so they remain user-selectable; the created-only scope is a
deliberate limit of the snapshot-diff implementation.

The objects the USER is working on -- their selection and active object at
the moment a turn starts -- are NEVER locked (see :func:`lockable_names`).
The lock protects what the AGENT creates/edits; it must not take control
away from the user of the thing they are editing, so those names are always
excluded from both the per-step lock and the session re-lock.

The registry is *session state* held in this module, not scene state: a
Blender crash cannot leave objects permanently locked, and ``unregister``
clears it.  This module is deliberately free of ``bpy`` -- it only produces
toolcode strings (their names go as data, #414) and manages plain dicts --
so it is unit-testable outside Blender.
"""

__all__ = (
    "is_locked",
    "managed_names",
    "record_managed",
    "record_prior",
    "remember_session",
    "session_names",
    "clear",
    "clear_session",
    "build_lock_code",
    "build_unlock_code",
    "lockable_names",
    "is_releasing",
    "lift",
    "lifted",
    "releasing",
    "relock",
)

import contextlib
import threading
from typing import Any, Iterator

# Managed datablock names -> the ``hide_select`` value they had before the
# lock, so the unlock can restore the exact prior state (not a blanket
# False, which would clobber a user's own hidden-from-selection object).
_MANAGED_OBJECTS: dict[str, bool] = {}
_MANAGED_COLLECTIONS: dict[str, bool] = {}
_LOCKED: bool = False

# Names whose true prior ``hide_select`` has ALREADY been captured for the
# current lock cycle.  Re-locking an already-locked name would read its
# now-``True`` hide_select as the "prior" and clobber the real value, so the
# unlock would re-lock the object forever (the reported "can't edit after a
# turn").  Capture a prior once, then ignore later reads for that name.
_PRIOR_KNOWN_OBJECTS: set[str] = set()
_PRIOR_KNOWN_COLLECTIONS: set[str] = set()

# Guards the registry against the turn worker thread (lock/release) racing a
# new turn or UI action after a Stop (the re-entrancy guard clears
# ``turn_active`` while the old thread may still be in flight).
_registry_lock = threading.RLock()

# The unlock is on its way (#548): the bridge's sandbox doesn't lift meanwhile, or around
# the unlock code it would lock again what that code just restored.
_RELEASING = [False]


def is_locked() -> bool:
    """True while the coworker holds at least one managed datablock locked."""
    return _LOCKED


def managed_names() -> tuple[set[str], set[str]]:
    """Return ``(object_names, collection_names)`` currently managed."""
    with _registry_lock:
        return set(_MANAGED_OBJECTS), set(_MANAGED_COLLECTIONS)


def record_managed(
    objects: object = (),
    collections: object = (),
) -> tuple[set[str], set[str]]:
    """Register *objects*/*collections* as managed.

    New names are recorded with an assumed prior ``hide_select = False``;
    :func:`record_prior` then replaces that with the true prior value once
    the lock toolcode reports it.  Idempotent.

    Returns ``(new_object_names, new_collection_names)`` -- the names added
    by THIS call.  Callers should lock only these, so an already-locked name
    is never re-locked (which would read ``hide_select = True`` as its prior
    and clobber the real value -- see :func:`record_prior`).
    """
    new_objs: set[str] = set()
    new_colls: set[str] = set()
    global _LOCKED
    with _registry_lock:
        for name in objects or ():
            if name and str(name) not in _MANAGED_OBJECTS:
                _MANAGED_OBJECTS[str(name)] = False
                new_objs.add(str(name))
        for name in collections or ():
            if name and str(name) not in _MANAGED_COLLECTIONS:
                _MANAGED_COLLECTIONS[str(name)] = False
                new_colls.add(str(name))
        _LOCKED = bool(_MANAGED_OBJECTS or _MANAGED_COLLECTIONS)
    return new_objs, new_colls


def record_prior(
    prior_objects: object = None,
    prior_collections: object = None,
) -> None:
    """Store the true prior ``hide_select`` values reported by the lock code.

    A prior is captured **once per name**: a later lock of an already-locked
    datablock reports ``hide_select = True``, and writing that back would make
    the unlock re-lock the object permanently.  Best-effort: unknown names are
    ignored.  Never raises.
    """
    try:
        with _registry_lock:
            if isinstance(prior_objects, dict):
                for name, value in prior_objects.items():
                    if name in _MANAGED_OBJECTS and name not in _PRIOR_KNOWN_OBJECTS:
                        _MANAGED_OBJECTS[name] = bool(value)
                        _PRIOR_KNOWN_OBJECTS.add(name)
            if isinstance(prior_collections, dict):
                for name, value in prior_collections.items():
                    if name in _MANAGED_COLLECTIONS and name not in _PRIOR_KNOWN_COLLECTIONS:
                        _MANAGED_COLLECTIONS[name] = bool(value)
                        _PRIOR_KNOWN_COLLECTIONS.add(name)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def clear() -> None:
    """Forget every managed datablock (call after unlocking)."""
    global _LOCKED
    with _registry_lock:
        _MANAGED_OBJECTS.clear()
        _MANAGED_COLLECTIONS.clear()
        _PRIOR_KNOWN_OBJECTS.clear()
        _PRIOR_KNOWN_COLLECTIONS.clear()
        _LOCKED = False


# -- Session-scoped memory ------------------------------------------------
# Names the coworker created, remembered ACROSS turns (they survive
# ``clear()``).  At each turn start they are re-locked, so an object made in
# an earlier turn stays protected while the agent works on it in a later one
# -- the per-step lock only covers entities created in the current turn, so
# cross-turn objects were user-selectable (how an accidental deletion of the
# agent's own "Ground" happened).  Cleared only on New Thread / Stop.
# Threads 1 (#357): each name remembers the thread that made it, so New Thread / Stop
# in one thread forgets only its own (thread "" = every thread).
_SESSION_OBJECTS: dict[str, str] = {}
_SESSION_COLLECTIONS: dict[str, str] = {}


def remember_session(objects: object = (), collections: object = (), thread: str = "") -> None:
    """Remember names the coworker created for the rest of the session.

    Persists across :func:`clear` so the next turn can re-lock them.
    Idempotent; never raises.
    """
    with _registry_lock:
        for name in objects or ():
            if name:
                _SESSION_OBJECTS[str(name)] = thread
        for name in collections or ():
            if name:
                _SESSION_COLLECTIONS[str(name)] = thread


def session_names(thread: str | None = None) -> tuple[set[str], set[str]]:
    """Return ``(object_names, collection_names)`` created this session (by *thread*)."""
    with _registry_lock:
        return ({n for n, t in _SESSION_OBJECTS.items() if thread is None or t == thread},
                {n for n, t in _SESSION_COLLECTIONS.items() if thread is None or t == thread})


def clear_session(thread: str | None = None) -> None:
    """Forget the session-remembered names (New Thread / Stop): *thread*'s, or all."""
    with _registry_lock:
        for names in (_SESSION_OBJECTS, _SESSION_COLLECTIONS):
            for name in [n for n, t in names.items() if thread is None or t == thread]:
                del names[name]


def lockable_names(names: object = (), protect: object = ()) -> set[str]:
    """Return *names* minus *protect* -- the datablocks safe to lock.

    *protect* holds the objects the USER is working on (their selection and
    active object when the turn started).  Locking those would take control
    of them away from the user mid-turn, so they are always excluded: the
    lock protects what the AGENT creates/edits, not what the user is editing.
    Pure and never raises; returns plain names.
    """
    blocked = {str(p) for p in (protect or ()) if p}
    return {str(n) for n in (names or ()) if n and str(n) not in blocked}


def build_lock_code(
    object_names: object = (),
    collection_names: object = (),
) -> tuple[str, dict]:
    """Return ``(code, data)`` that locks *object_names*/*collection_names*.

    The code records each datablock's prior ``hide_select`` into the
    ``result`` dict (``prior_hide_select`` / ``prior_hide_select_coll``) so
    the caller can restore it exactly, and sets ``hide_select = True``.
    It goes by Coworker's own route (:mod:`internal_route`, #414): the names
    travel as *data*, which the code reads, never pasted into the code.
    """
    data = {"objects": sorted(str(n) for n in (object_names or ()) if n),
            "collections": sorted(str(n) for n in (collection_names or ()) if n)}
    return (
        "# blmcp-toolcode-skip-preflight\n"
        "import bpy\n"
        "_objs = data['objects']\n"
        "_colls = data['collections']\n"
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
    ), data


def build_unlock_code() -> tuple[str, dict]:
    """Return ``(code, data)`` that restores every managed ``hide_select`` value
    (the names and their priors travel as *data*, #414)."""
    with _registry_lock:
        data = {"objects": {k: bool(v) for k, v in _MANAGED_OBJECTS.items()},
                "collections": {k: bool(v) for k, v in _MANAGED_COLLECTIONS.items()}}
    return (
        "# blmcp-toolcode-skip-preflight\n"
        "import bpy\n"
        "_priors = data['objects']\n"
        "_coll_priors = data['collections']\n"
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
    ), data


# -- Lifting the lock while Coworker's own call runs (#548) ----------------
_KINDS = (("objects", _MANAGED_OBJECTS, _PRIOR_KNOWN_OBJECTS),
          ("collections", _MANAGED_COLLECTIONS, _PRIOR_KNOWN_COLLECTIONS))


def lift(blend_data: Any) -> list[tuple[str, str]]:
    """Main thread: give each locked datablock its own ``hide_select`` back for one call.

    Only names whose own value is known (the lock code reported it) and is False, and
    that are locked now; a datablock that was unselectable on its own stays so.  Returns
    ``[(kind, name)]`` for :func:`relock`.  Also while the unlock is on its way: an undo
    step or a save then still gets the own values (the unlock can't run in between -- both
    are on the main thread); only the bridge's sandbox holds back then (:func:`is_releasing`).
    Never raises."""
    if not _LOCKED:
        return []
    with _registry_lock:
        wanted = [(kind, name) for kind, managed, known in _KINDS
                  for name in sorted(known) if managed.get(name) is False]
    held: list[tuple[str, str]] = []
    for kind, name in wanted:
        try:
            block = getattr(blend_data, kind).get(name)
            if block is not None and block.hide_select:
                block.hide_select = False
                held.append((kind, name))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    return held


def relock(blend_data: Any, held: Any) -> None:
    """Main thread: lock again what :func:`lift` gave back -- by name, and only what is
    still managed (a release meanwhile wins; a renamed or removed one is skipped).
    Never raises."""
    for kind, name in held or ():
        with _registry_lock:
            still = name in (_MANAGED_OBJECTS if kind == "objects" else _MANAGED_COLLECTIONS)
        if not still:
            continue
        try:
            block = getattr(blend_data, kind).get(name)
            if block is not None:
                block.hide_select = True
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@contextlib.contextmanager
def lifted(blend_data: Any) -> Iterator[list[tuple[str, str]]]:
    """Main thread: the lock lifted for the ``with`` body, put back after it (also when
    it raises)."""
    held = lift(blend_data)
    try:
        yield held
    finally:
        relock(blend_data, held)


def is_releasing() -> bool:
    """The unlock is on its way: the bridge's sandbox doesn't lift (it runs the unlock code
    too, and would lock again what that code restored)."""
    return _RELEASING[0]


@contextlib.contextmanager
def releasing() -> Iterator[None]:
    """Around the unlock call (worker thread): no bridge call lifts the lock meanwhile."""
    _RELEASING[0] = True
    try:
        yield
    finally:
        _RELEASING[0] = False
