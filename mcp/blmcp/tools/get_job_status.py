# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

__all__ = (
    "register",
)

import anyio

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.get_job_status_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Job Status",
            readOnlyHint=True,
        )
    )
    async def get_job_status(
        job_id: str,
        wait_seconds: float = 25.0,
    ) -> dict[str, object]:
        """
        How a long job (a background render or bake) is doing; waits for it to finish.

        Long jobs answer with a ``job_id`` instead of holding the call open. Call this
        with that id: it answers as soon as the job is over, or after ``wait_seconds``
        with ``state`` "running" and the progress so far. While it runs, call it again
        (don't poll faster than every few seconds; the wait does that for you).

        Args:
            job_id: The ``job_id`` a long job returned.
            wait_seconds: How long to wait for the job to finish (0 = answer now, at most 240).

        Returns:
            ``status``, ``job_id``, ``state`` ("running", "done", "error" or "cancelled"),
            ``kind``, ``label``, ``elapsed_seconds``, ``progress_percent`` (when Blender
            reports it), ``result`` once done (e.g. the ``filepath`` or baked images),
            and a ``message``.
        """
        code = toolcode_format_call(_TOOL_CALL, Params(job_id=job_id, wait_seconds=wait_seconds))
        # Waiting must not block the server: the bridge call runs on a worker thread.
        return await anyio.to_thread.run_sync(lambda: send_code(code, strict_json=True))
