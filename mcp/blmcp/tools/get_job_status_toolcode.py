# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for get_job_status: how a long render or bake is doing (Tools 6, #309).

With *wait_seconds* it returns a checker, so the bridge answers once the job
is over or the wait is up (see the add-on's deferred_tool.py and long_jobs.py).
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    job_id: str
    wait_seconds: float


class Result(NamedTuple):
    status: str
    job_id: str
    state: str
    kind: str | None = None
    label: str | None = None
    elapsed_seconds: int | None = None
    progress_percent: int | None = None
    progress_text: str | None = None
    result: Any = None
    message: str | None = None


# @include_begin: _coworker_api_shared.py
# @include_end


# The longest the bridge holds the answer: under the MCP client's 300 s socket timeout.
_WAIT_MAX = 240.0


def main(params: Params) -> Any:
    import time
    import bpy  # pylint: disable=import-error,no-name-in-module
    api = _coworker_api()
    first = api.job_status(params.job_id)
    try:
        wait = max(0.0, min(float(params.wait_seconds or 0.0), _WAIT_MAX))
    except (TypeError, ValueError):
        wait = 0.0
    if first.get("state") != "running" or wait <= 0.0 or bpy.app.background:
        return Result(**first)
    deadline = time.monotonic() + wait

    def check_is_finished() -> dict[str, object] | None:
        now = api.job_status(params.job_id)
        if now.get("state") != "running" or time.monotonic() > deadline:
            return now
        return None
    return check_is_finished
