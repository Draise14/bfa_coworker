# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for delegate_task: a helper chat in Coworker takes a part of the job (#479).
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import NamedTuple


class Params(NamedTuple):
    task: str
    editor: str
    title: str


class Result(NamedTuple):
    status: str
    message: str
    helper_id: str = ""
    title: str = ""


# @include_begin: _coworker_api_shared.py
# @include_end


def main(params: Params) -> Result:
    return Result(**_coworker_api().delegate_task(params._asdict()))
