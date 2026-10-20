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
from blmcp.tools.update_plan_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Update Plan",
            readOnlyHint=False,
        )
    )
    def update_plan(
        steps: list[str] | None = None,
        done: list[int] | None = None,
        current: int | None = None,
    ) -> dict[str, object]:
        """
        Keep a short step plan for multi-step requests (3+ steps); the user sees it in
        Coworker's Goal & Plan panel and watches it tick off.

        Call once with ``steps`` to set the plan, then with ``done`` (step numbers) as you
        finish steps.

        Args:
            steps: Full ordered list of short steps (replaces the plan).
            done: 1-based numbers of steps that are now finished.
            current: 1-based number of the step you are working on now.

        Returns:
            Status and the plan as it stands.
        """
        return send_code(
            toolcode_format_call(_TOOL_CALL, Params(steps=steps, done=done, current=current)),
            strict_json=True,
        )
