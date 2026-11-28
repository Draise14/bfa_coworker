# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for make_short (#616): build a pick as its own scene (the Make Short button's code).
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    pick: str = ""
    steps: tuple[str, ...] | None = None


class Result(NamedTuple):
    status: str
    message: str
    scene: str | None = None
    pick: str | None = None
    title: str | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    seconds: float | None = None
    steps: list[Any] | None = None
    picks: list[Any] | None = None


def _module() -> Any:
    import sys
    for name, module in list(sys.modules.items()):
        if name == "bfa_coworker.highlight_tools" or name.endswith(".bfa_coworker.highlight_tools"):
            return module
    return None


def main(params: Params) -> Result:
    mod = _module()
    if mod is None or not hasattr(mod, "tool_make_short"):
        return Result(status="error", message="Coworker's Highlights isn't loaded in this Blender.")
    out = mod.tool_make_short(params.pick, None if params.steps is None else list(params.steps))
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
