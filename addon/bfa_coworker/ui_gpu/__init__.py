# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
GPU-drawn, theme-matched UI toolkit for the Coworker chat (Tier 4, "GPU 1").

* :mod:`.style`  -- colours, roundness, font size and scale from the theme (bpy-free)
* :mod:`.layout` -- rectangles, wrapping, the immediate-mode canvas (bpy-free)
* :mod:`.render` -- draws a canvas's commands with ``gpu`` / ``blf``

``render`` is not imported here: the bpy-free parts stay importable in tests
and in background mode.  See ``_misc/spikes/gpu_chat/DECISION.md``.
"""

__all__ = (
    "layout",
    "style",
)

from . import layout, style
