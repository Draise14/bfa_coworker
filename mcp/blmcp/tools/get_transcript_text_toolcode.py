# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for get_transcript_text (#498): the add-on's Edit by Transcript
(``transcript_edit_ops.py``) renders the clip's transcript as the Text Editor shows it.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    strip: str = ""


class Result(NamedTuple):
    status: str
    message: str
    strip: str | None = None
    scene: str | None = None
    media: str | None = None
    text_block: str | None = None
    pending_edits: bool = False
    lines: list[Any] | None = None
    text: str | None = None
    version: str | None = None


def _module() -> Any:
    import sys
    for name, module in list(sys.modules.items()):
        if name == "bfa_coworker.transcript_edit_ops" or name.endswith(".bfa_coworker.transcript_edit_ops"):
            return module
    return None


def main(params: Params) -> Result:
    mod = _module()
    if mod is None or not hasattr(mod, "tool_get_transcript_text"):
        return Result(status="error", message="Coworker's Edit by Transcript isn't loaded in this Blender.")
    out = mod.tool_get_transcript_text(params.strip or None)
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
