# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

__all__ = (
    "register",
)

from blmcp.tools_helpers import bounded_output
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Read Tool Output",
            readOnlyHint=True,
        )
    )
    def read_tool_output(
        output_id: str,
        offset: int = 0,
        limit: int = bounded_output.PAGE_MAX,
    ) -> dict[str, object]:
        """
        Read more of a long tool result that was shortened.

        A result longer than about 8000 characters arrives as a preview with an
        ``output_id``. Call this only when the preview isn't enough, and read
        just the part you need.

        Args:
            output_id: The ``output_id`` from the shortened result.
            offset: Character offset to start from (0 = the beginning).
            limit: Characters to return (at most 6000).

        Returns:
            ``text``, its ``offset``, ``next_offset`` (None at the end) and ``total_chars``.
        """
        return bounded_output.read(output_id, offset, limit)
