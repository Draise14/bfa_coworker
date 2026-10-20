# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for update_plan: the plan in Coworker's Goal & Plan panel.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    steps: Any
    done: Any
    current: Any


class Result(NamedTuple):
    status: str
    message: str


# @include_begin: _coworker_api_shared.py
# @include_end


def main(params: Params) -> Result:
    args = {k: v for k, v in params._asdict().items() if v not in (None, [], "")}
    return Result(status="ok", message=_coworker_api().update_plan(args))
