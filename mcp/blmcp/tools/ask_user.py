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
from blmcp.tools.ask_user_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Ask User",
            readOnlyHint=True,
        )
    )
    async def ask_user(
        question: str,
        choices: list[str] | None = None,
        allow_text: bool = True,
        wait_seconds: float = 240.0,
    ) -> dict[str, object]:
        """
        Ask the user a short question in Coworker's chat, with buttons; waits for the answer.

        Ask when a choice really matters (style, which object, overwrite or keep) - a
        question beats a wrong turn. Give 2-5 short ``choices``, the one you recommend FIRST
        (the chat marks it as the recommended path); with ``allow_text`` the user may also
        type an answer.

        Args:
            question: One short question.
            choices: Button labels (2-5 short options), your recommended one first.
            allow_text: Let the user type an answer instead.
            wait_seconds: How long to wait (at most 280).

        Returns:
            ``status`` ("ok", "no_answer" or "cancelled") and the ``answer`` text.
        """
        code = toolcode_format_call(_TOOL_CALL, Params(question=question, choices=choices or [],
                                                        allow_text=allow_text, wait_seconds=wait_seconds))
        # Waiting must not block the server: the bridge call runs on a worker thread.
        return await anyio.to_thread.run_sync(lambda: send_code(code, strict_json=True))
