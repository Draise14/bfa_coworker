# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# pylint: disable=C0114  # See tool doc-string.

__all__ = (
    "register",
)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.suggest_highlights_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Suggest Highlights",
            readOnlyHint=False,
        )
    )
    def suggest_highlights(
        kind: str = "auto",
        max_seconds: float = 60.0,
        count: int = 5,
    ) -> dict[str, object]:
        """
        Find pieces of the Sequencer edit that would stand alone as a short (Highlights), as a
        background job: it answers at once with a ``job_id`` and takes minutes. Wait for it with
        get_job_status(job_id, wait_seconds); the finished job's ``result`` lists the picks (id,
        title, hook, reason, length, place, score) and they are kept on the scene (the Highlights
        panel shows them too). Nothing is built or changed in the edit: make_short builds one.
        Uses Coworker's own model (Local or Remote), so the transcript text goes to a Remote
        service (``privacy``). With an External Agent or Harness it answers ``mode``
        "read_and_propose": read_edit_sentences and propose_highlights are the way there.

        Args:
            kind: ``auto`` (decided from the first minutes), ``podcast``, ``tutorial`` or ``talk``.
            max_seconds: The longest a short may be, 15 to 60.
            count: How many picks to look for (1 to 10).

        Returns:
            ``job_id`` and ``sentences`` (the search started), or an ``error`` message saying what to do.
        """
        p = Params(kind=kind, max_seconds=float(max_seconds), count=int(count))
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
