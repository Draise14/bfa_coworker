# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for remove_silences (#539): the add-on's Remove Silences (``wand/sequencer.py``)
does the work, the same code as the Sequencer's Strip > Remove Silences, without a UI.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    strips: tuple[str, ...] = ()
    threshold_db: float = -40.0
    min_silence: float = 0.4
    padding: float = 0.15
    min_keep: float = 0.1
    close_gaps: bool = True
    update_scene_end: bool = True


class Result(NamedTuple):
    status: str
    message: str
    cuts: int = 0
    seconds_removed: float = 0.0
    frames_removed: int = 0
    cut_ranges: list[Any] | None = None
    new_names: dict[str, Any] | None = None
    end_frame: int | None = None
    content_end: int | None = None
    scene_end_changed: bool = False
    refused: list[Any] | None = None
    problems: list[str] | None = None
    notes: list[str] | None = None
    listened: list[str] | None = None
    captions_trimmed: list[str] | None = None
    captions_dropped: list[str] | None = None
    scene: str | None = None


def _sequencer_wand() -> Any:
    import sys
    for name, module in list(sys.modules.items()):
        if name == "bfa_coworker.wand.sequencer" or name.endswith(".bfa_coworker.wand.sequencer"):
            return module
    return None


def main(params: Params) -> Result:
    wand = _sequencer_wand()
    if wand is None or not hasattr(wand, "tool_remove_silences"):
        return Result(status="error", message="Coworker's Remove Silences isn't loaded in this Blender.")
    strips = params.strips
    if isinstance(strips, str):
        strips = [strips] if strips else []
    out = wand.tool_remove_silences(list(strips or ()), params.threshold_db, params.min_silence, params.padding,
                                    params.min_keep, bool(params.close_gaps), bool(params.update_scene_end))
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
