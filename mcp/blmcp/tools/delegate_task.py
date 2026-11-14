# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

__all__ = (
    "register",
)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.delegate_task_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Delegate Task",
            readOnlyHint=False,
        )
    )
    def delegate_task(
        task: str,
        editor: str = "",
        title: str = "",
    ) -> dict[str, object]:
        """
        Hand one independent part of the job to a helper chat in Coworker (a sub-agent).

        The helper opens as its own chat tab with the task as its first message and answers
        at once: don't wait or poll. When every helper you started has finished, their
        results come back to this chat as one message, and you carry on from there. On a
        local model helpers run one after another; online they may run side by side.

        Delegate independent parts (different editors, a long side job). Don't delegate
        small jobs or steps that need each other's results. Helpers can't start helpers.

        Args:
            task: What the helper should do, complete on its own (names, values, where).
            editor: The editor whose context it works in: VIEW_3D, NODE_EDITOR,
                SEQUENCE_EDITOR, TEXT_EDITOR, IMAGE_EDITOR, CLIP_EDITOR, DOPESHEET_EDITOR,
                GRAPH_EDITOR, NLA_EDITOR, SPREADSHEET, or "" for All editors.
            title: A short title for its tab (a few words).

        Returns:
            ``status`` ("ok" or "error"), the helper's ``helper_id`` and ``title``, and a message.
        """
        return send_code(
            toolcode_format_call(_TOOL_CALL, Params(task=task, editor=editor, title=title)),
            strict_json=True,
        )
