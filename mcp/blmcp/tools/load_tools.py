# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

__all__ = (
    "register",
)

from blmcp import tool_domains
from mcp.server.fastmcp import Context, FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Load Tools",
            readOnlyHint=True,
        )
    )
    async def load_tools(
        domains: list[str],
        ctx: Context,
    ) -> dict[str, object]:
        """
        Make a group of specialised tools available when the task needs them. If a tool
        you need isn't in your list (materials, nodes, assets, animation, lighting...),
        call this first; the tools appear right after.

        Domains: animation, material, modeling, lighting, rendering, vse,
        geometry_nodes, assets. Load only what the request needs; the basic tools
        (scene, code, docs, screenshots, plan, questions) are always there.

        Args:
            domains: One or more domain names from the list above.

        Returns:
            The domains loaded and the tools they added (name and what it does).
        """
        loaded, unknown = tool_domains.load(domains)
        names = sorted(set().union(*(tool_domains.DOMAINS[d] for d in loaded))) if loaded else []
        if tool_domains.on_demand() and loaded:
            await ctx.session.send_tool_list_changed()
        described = []
        for tool in mcp._tool_manager.list_tools():  # pylint: disable=protected-access
            if tool.name in names:
                described.append({"name": tool.name,
                                  "does": (tool.description or "").strip().split("\n")[0][:160]})
        result: dict[str, object] = {"status": "ok" if loaded else "error", "loaded": loaded, "tools": described}
        if unknown:
            result["unknown"] = unknown
            result["domains"] = sorted(tool_domains.DOMAINS)
        if not tool_domains.on_demand():
            result["note"] = "All tools were already available."
        return result
