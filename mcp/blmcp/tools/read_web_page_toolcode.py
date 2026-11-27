# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for read_web_page (#637): it only asks the user.

The bridge shows the approval card for ``read_web_page`` before this runs; this code does
nothing but answer "allowed".  The page itself is fetched afterwards by the MCP server's own
process, not by Blender, so Blender's interface never waits for a download.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import NamedTuple


class Params(NamedTuple):
    url: str


class Result(NamedTuple):
    status: str
    message: str


def main(params: Params) -> Result:
    return Result(status="ok", message="The user allowed reading {:s}.".format(params.url[:200]))
