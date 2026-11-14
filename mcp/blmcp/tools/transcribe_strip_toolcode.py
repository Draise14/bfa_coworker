# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for transcribe_strip: the words of a sound / movie strip with times (#375).

Answers from the transcript cache at once; otherwise returns a checker, so the bridge
answers when whisper is done, or hands a long clip to the job registry after a while
(see the add-on's speech.py, deferred_tool.py and long_jobs.py).
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    strip: str
    language: str = ""
    limit: int = 150
    offset: int = 0
    prompt: str = ""
    model: str = ""


class Result(NamedTuple):
    status: str
    message: str | None = None
    strip: str | None = None
    cached: bool | None = None
    language: str | None = None
    model: str | None = None
    engine: str | None = None
    timing: str | None = None
    duration_s: float | None = None
    fps: float | None = None
    word_count: int | None = None
    words_in_media: int | None = None
    offset: int | None = None
    next_offset: int | None = None
    columns: Any = None
    words: Any = None
    text: str | None = None
    transcript_path: str | None = None
    seconds: float | None = None


# @include_begin: _coworker_api_shared.py
# @include_end


def main(params: Params) -> Any:
    out = _coworker_api().transcribe_strip(
        params.strip, language=params.language, limit=params.limit, offset=params.offset,
        prompt=params.prompt, model=params.model)
    if callable(out):
        return out
    return Result(**{k: v for k, v in out.items() if k in Result._fields})
