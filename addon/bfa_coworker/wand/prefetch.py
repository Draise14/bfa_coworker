# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Removing Sequencer strips while the Sequencer's prefetch is rendering (#536).

Prefetch renders frames ahead in a background thread and, for its cache, looks the strips
it renders up in the scene's original strips.  ``strips.remove()`` frees a strip's data
first and only then stops prefetch (``seq_strip_free_ex`` frees ``strip->data``, then
``relations_invalidate_cache_raw`` calls ``prefetch_stop``), so a removal while prefetch
renders that strip crashes Blender (access violation in ``give_frame_index``).  Blender's
own Delete operator stops prefetch before it frees anything.

:func:`remove_strips` does the same: ``Strip.invalidate_cache()`` ends in
``prefetch_stop(scene)``, which waits until the prefetch thread has stopped; nothing
starts it again until the Sequencer draws, which is after the caller's operator.  The
artist's Prefetch Frames setting is not touched.  Switching ``use_prefetch`` off is not
enough: the thread looks at that flag only after the frame it is rendering is done
(live loop: crashed at the second removal, like no guard at all).

``Strip.split()`` frees strips too (the parts of an effect chain outside the cut) and
doesn't stop prefetch either: call :func:`stop_prefetch` before splitting.

Only the prefetch of the scene the strips belong to reads them (other scenes' prefetch
renders their own copies of a scene strip's scene), so that one scene's prefetch is the
one stopped.  Removing a whole scene needs nothing: ``bpy.data.scenes.remove`` stops
that scene's prefetch before it frees its strips.

Code an agent writes itself (``execute_blender_code``, #558) can call ``strips.remove()``
or ``split()`` too.  The bridge asks :func:`may_free_strips` about the code text and, when
it says yes, calls :func:`stop_every_prefetch` once just before the exec.  Once per exec is
enough: prefetch starts again only from a Sequencer draw (``render_give_ibuf`` ->
``seq_prefetch_start``), and nothing draws while the exec holds the main thread.  The match
is on purpose broad (``list.remove``, ``str.split`` match as well); in a file without a
sequence editor with strips that costs one loop over the scenes.  It uses the
``'COMPOSITE'`` invalidation: it ends in the same ``prefetch_stop`` as ``'RAW'`` but keeps
the strip's source images and thumbnails.

Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "may_free_strips",
    "remove_strips",
    "stop_every_prefetch",
    "stop_prefetch",
)

import re
from typing import Any, Iterable

# What in agent-written code can free strips: a collection's remove() (strips, scenes, a
# meta's strips), Strip.split(), bpy.data.batch_remove(), and the Sequencer operators that
# delete, split or regroup strips.
_FREES_STRIPS = re.compile(
    r"\.\s*(?:remove|split)\s*\("
    r"|\bbatch_remove\s*\("
    r"|\bsequencer\s*\.\s*(?:delete|split|meta_make|meta_separate)"     # split_multicam too
)


def may_free_strips(code: str) -> bool:
    """Whether *code* (Python text) might remove or split Sequencer strips (a cheap text match)."""
    return bool(code) and _FREES_STRIPS.search(code) is not None


def stop_every_prefetch(scenes: Iterable[Any]) -> int:
    """Stop the Sequencer prefetch of every scene in *scenes* (``bpy.data.scenes``) whose
    sequence editor has strips, and wait for it; how many scenes were stopped.  Never raises
    for a scene or strip that can't be asked."""
    stopped = 0
    for scene in scenes:
        try:
            ed = scene.sequence_editor
            first = next(iter(ed.strips), None) if ed is not None else None
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            continue
        if first is not None and stop_prefetch(first, 'COMPOSITE'):
            stopped += 1
    return stopped


def stop_prefetch(strip: Any, kind: str = 'RAW') -> bool:
    """Stop the prefetch of *strip*'s scene and wait for it; False when it couldn't be asked.
    *kind*: the cache invalidated on the way (``'RAW'`` or ``'COMPOSITE'``; both stop it)."""
    try:
        strip.invalidate_cache(kind)        # the cache it invalidates goes with the strip anyway
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False
    return True


def remove_strips(strips: Any, doomed: Iterable[Any]) -> int:
    """Remove *doomed* from the strip collection *strips* (``ed.strips``, a meta's ``strips``)
    with the scene's prefetch stopped first; the number removed."""
    doomed = list(doomed)
    if strips is None or not doomed:
        return 0
    stop_prefetch(doomed[0])
    for strip in doomed:
        strips.remove(strip)
    return len(doomed)
