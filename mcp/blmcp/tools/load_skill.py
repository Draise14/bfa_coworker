# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

__all__ = (
    "register",
)

from blmcp import skill_index
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Load Skill",
            readOnlyHint=True,
        )
    )
    def load_skill(name: str = "") -> str:
        """
        Read a skill (reference notes for a kind of task) from the list in your instructions,
        before the task it covers. No name: lists them.

        Args:
            name: A skill name from the list, e.g. "materials".
        """
        if name:
            body = skill_index.load_skill_body(name)
            if body is not None:
                return body
        skills = skill_index.list_skills()
        if not skills:
            return "No skills are installed with this server."
        head = "Unknown skill {!r}. ".format(name) if name else ""
        return head + "Skills: " + "; ".join("{:s} ({:s})".format(n, line) for n, line in skills)
