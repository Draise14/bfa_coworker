# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for get_scene_digest: a short scene summary with a hash.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    since: str


class Result(NamedTuple):
    status: str
    digest: Any


# @include_begin: _coworker_api_shared.py
# @include_end


def main(params: Params) -> Result:
    return Result(status="ok", digest=_coworker_api().scene_digest(params.since))
