# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for reframe_strips (#606): Reframe's layouts for the selected or named strips.  The add-on's
``coworker_api.reframe_strips`` does the work, the same code as the Sequencer's Reframe buttons.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    layout: str = ""
    strips: tuple[str, ...] = ()
    swap: bool = False


class Result(NamedTuple):
    status: str
    message: str
    layout: str | None = None
    changed: list[str] | None = None
    created: list[str] | None = None
    removed: list[str] | None = None
    unchanged: list[str] | None = None
    skipped: list[Any] | None = None
    swapped: list[str] | None = None
    tracking: str | None = None
    layouts: dict[str, Any] | None = None
    scene: str | None = None


# @include_begin: _coworker_api_shared.py
# @include_end


def main(params: Params) -> Any:
    strips = params.strips
    if isinstance(strips, str):
        strips = [strips] if strips else []
    try:
        out = _coworker_api().reframe_strips(str(params.layout or ""), [str(s) for s in (strips or ())],
                                             bool(params.swap))
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return Result(status="error", message=str(ex))
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
