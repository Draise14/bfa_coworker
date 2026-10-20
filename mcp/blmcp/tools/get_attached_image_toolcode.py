# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for get_attached_image: the image the user attached in Coworker's chat.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import NamedTuple


class Params(NamedTuple):
    pass


class Result(NamedTuple):
    status: str
    name: str = ""
    mime: str = ""
    image_base64: str | None = None
    message: str | None = None


# @include_begin: _coworker_api_shared.py
# @include_end


def main(_params: Params) -> Result:
    return Result(**_coworker_api().attached_image())
