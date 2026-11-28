# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for read_edit_sentences (#616): the whole edit's speech as numbered sentences, as it plays now (the scene reader, H1 #611).
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    first: int = 1
    count: int = 150


class Result(NamedTuple):
    status: str
    message: str
    scene: str | None = None
    total: int = 0
    first: int = 1
    last: int = 0
    next_first: int | None = None
    version: str | None = None
    text: str | None = None
    windows: list[Any] | None = None
    windows_total: int = 0
    report: dict[str, Any] | None = None


def _module() -> Any:
    import sys
    for name, module in list(sys.modules.items()):
        if name == "bfa_coworker.scene_reader_ops" or name.endswith(".bfa_coworker.scene_reader_ops"):
            return module
    return None


def main(params: Params) -> Result:
    mod = _module()
    if mod is None or not hasattr(mod, "tool_read_edit_sentences"):
        return Result(status="error", message="Coworker's Highlights isn't loaded in this Blender.")
    out = mod.tool_read_edit_sentences(params.first, params.count)
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
