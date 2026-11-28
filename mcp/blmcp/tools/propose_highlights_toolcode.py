# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for propose_highlights (#616): the agent's own picks go through the search's checks into the picks list.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    picks: tuple[Any, ...] = ()
    kind: str = "auto"
    max_seconds: float = 60.0
    version: str = ""
    replace: bool = False


class Result(NamedTuple):
    status: str
    message: str
    added: int = 0
    refused: Any = None
    version: str | None = None
    picks: list[Any] | None = None
    next: str | None = None


def _module() -> Any:
    import sys
    for name, module in list(sys.modules.items()):
        if name == "bfa_coworker.highlight_tools" or name.endswith(".bfa_coworker.highlight_tools"):
            return module
    return None


def main(params: Params) -> Result:
    mod = _module()
    if mod is None or not hasattr(mod, "tool_propose_highlights"):
        return Result(status="error", message="Coworker's Highlights isn't loaded in this Blender.")
    out = mod.tool_propose_highlights(list(params.picks), params.kind, params.max_seconds, params.version, params.replace)
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
