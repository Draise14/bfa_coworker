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

Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "remove_strips",
    "stop_prefetch",
)

from typing import Any, Iterable


def stop_prefetch(strip: Any) -> bool:
    """Stop the prefetch of *strip*'s scene and wait for it; False when it couldn't be asked."""
    try:
        strip.invalidate_cache('RAW')       # the cache it invalidates goes with the strip anyway
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
