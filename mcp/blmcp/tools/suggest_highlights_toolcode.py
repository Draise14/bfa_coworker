# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for suggest_highlights (#616): start the search for pieces of the edit that would stand alone as a short.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    kind: str = "auto"
    max_seconds: float = 60.0
    count: int = 5


class Result(NamedTuple):
    status: str
    message: str
    job_id: str | None = None
    scene: str | None = None
    sentences: int = 0
    privacy: str | None = None
    mode: str | None = None
    job_running: bool = False


def _module() -> Any:
    import sys
    for name, module in list(sys.modules.items()):
        if name == "bfa_coworker.highlight_tools" or name.endswith(".bfa_coworker.highlight_tools"):
            return module
    return None


def main(params: Params) -> Result:
    mod = _module()
    if mod is None or not hasattr(mod, "tool_suggest_highlights"):
        return Result(status="error", message="Coworker's Highlights isn't loaded in this Blender.")
    out = mod.tool_suggest_highlights(params.kind, params.max_seconds, params.count)
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
