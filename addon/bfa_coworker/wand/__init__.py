# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Coworker operators: ordinary-looking Blender operators with a wand badge on a
familiar icon, in the menus and toolshelf panels where native operators of that
kind live (Tier 4m).  Wand 0 (#331) ships the first one, *Remove Silences* in the
Sequencer (``sequencer.py``), its spec (``spec.py``), the silence logic
(``silences.py``, bpy-free) and the first icons (``icons.py``).

Importing the package needs no ``bpy``; :func:`register` brings the operator in.
"""

from __future__ import annotations

__all__ = (
    "register",
    "unregister",
)


def register() -> None:
    from . import sequencer
    sequencer.register()


def unregister() -> None:
    from . import sequencer
    sequencer.unregister()
