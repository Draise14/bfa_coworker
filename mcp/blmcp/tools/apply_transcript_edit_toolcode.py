# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for apply_transcript_edit (#498): the add-on's Edit by Transcript
(``transcript_edit_ops.py``) does the work, the same code as the Text Editor's Apply.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    strip: str = ""
    cut_lines: tuple[int, ...] = ()
    text: str | None = None
    dry_run: bool = False
    version: str | None = None
    lines: tuple[Any, ...] = ()
    confirm_large_cut: bool = False


class Result(NamedTuple):
    status: str
    message: str
    strip: str | None = None
    scene: str | None = None
    text_block: str | None = None
    cut_words: int = 0
    seconds: float = 0.0
    percent: int = 0
    needs_confirm: bool = False
    version: str | None = None
    seconds_removed: float = 0.0
    moved: bool = False
    moves: int = 0
    retexted: int = 0
    captions_updated: int = 0
    captions_kept_edited: int = 0
    flags: list[Any] | None = None
    ops: list[Any] | None = None
    refused: list[Any] | None = None
    new_names: dict[str, Any] | None = None
    end_frame: int | None = None
    not_on_timeline: int = 0
    note: str | None = None
    text_now: str | None = None


def _module() -> Any:
    import sys
    for name, module in list(sys.modules.items()):
        if name == "bfa_coworker.transcript_edit_ops" or name.endswith(".bfa_coworker.transcript_edit_ops"):
            return module
    return None


def main(params: Params) -> Result:
    mod = _module()
    if mod is None or not hasattr(mod, "tool_apply_transcript_edit"):
        return Result(status="error", message="Coworker's Edit by Transcript isn't loaded in this Blender.")
    out = mod.tool_apply_transcript_edit(params.strip or None, params.text, list(params.cut_lines or ()),
                                         bool(params.dry_run), params.version or None, list(params.lines or ()),
                                         bool(params.confirm_large_cut))
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
