# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for add_captions (Auto Captions, #376): the add-on's captions_ops does the work.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    strips: tuple[str, ...] = ()
    style: str = "CLEAN"
    language: str | None = None
    transcript_path: str | None = None


class Result(NamedTuple):
    status: str
    message: str
    lines_created: int = 0
    highlight_strips: int = 0
    channel: int | None = None
    first_frame: int | None = None
    last_frame: int | None = None
    style: str | None = None
    language: str | None = None
    sources: list[str] | None = None
    replaced: int = 0
    note: str | None = None


def _captions_ops() -> Any:
    import sys
    for name, module in list(sys.modules.items()):
        if name == "bfa_coworker.captions_ops" or name.endswith(".bfa_coworker.captions_ops"):
            return module
    return None


def main(params: Params) -> Result:
    ops = _captions_ops()
    if ops is None:
        return Result(status="error", message="Coworker's captions aren't loaded in this Blender.")
    out = ops.tool_add_captions(list(params.strips or ()), params.style, params.language, params.transcript_path)
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
