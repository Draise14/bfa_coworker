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
        ctx: Context,
        domains: list[str] | None = None,
    ) -> dict[str, object]:
        """
        Make a group of specialised tools available when the task needs them. If a tool
        you need isn't in your list (rendering, baking, materials, nodes, assets,
        animation, lighting...), call this first; the tools appear right after.

        Domains and the tools they add:
        - rendering: render to a file, bake textures, lighting rig (render_viewport_to_path,
          bake_textures, three_point_lighting_rig, get_screenshot_of_area_as_image)
        - material: PBR materials, Polyhaven textures, bake textures (setup_pbr_material,
          assign_material_to_objects, download_polyhaven_asset, search_polyhaven_assets,
          load_asset_in_context, bake_textures, render_viewport_to_path,
          get_screenshot_of_area_as_image)
        - lighting: lights, HDRIs (three_point_lighting_rig, download_polyhaven_asset,
          search_polyhaven_assets, render_viewport_to_path, get_screenshot_of_area_as_image)
        - animation: keyframes (batch_keyframe_insert, jump_to_view3d_object_by_name,
          jump_to_view3d_object_data_by_name, render_viewport_to_path)
        - modeling: navigate and organise (jump_to_view3d_object_by_name,
          jump_to_view3d_object_data_by_name, jump_to_tab_by_name, jump_to_tab_by_space_type,
          set_collection_color_tag, get_screenshot_of_area_as_image)
        - geometry_nodes: node trees (get_active_node_tree, get_node_group_interface,
          wire_node_group, jump_to_view3d_object_by_name, jump_to_view3d_object_data_by_name,
          get_screenshot_of_area_as_image)
        - assets: asset libraries (search_assets, get_asset_libraries, get_asset_tags,
          list_asset_catalogs, load_asset_in_context, place_asset_in_scene,
          jump_to_asset_browser, download_polyhaven_asset, search_polyhaven_assets,
          assign_material_to_objects, get_active_node_tree, get_node_group_interface,
          wire_node_group)
        - vse: Video Sequencer navigation (jump_to_tab_by_name, jump_to_tab_by_space_type) and captions (add_captions)

        Load only what the request needs; the basic tools (scene, code, docs,
        screenshots, plan, questions, bake_textures, render_viewport_to_path,
        get_job_status) are always there. Called with no domains, it lists them.

        Args:
            domains: One or more domain names from the list above (none: list them).

        Returns:
            The domains loaded and the tools they added (name and what it does).
        """
        if not domains:     # #422: an empty call answers with the catalog, not a validation error
            return {"status": "ok", "loaded": [],
                    "domains": {d: sorted(tools) for d, tools in sorted(tool_domains.DOMAINS.items())},
                    "note": "Call load_tools again with the domains you need, e.g. [\"rendering\"]."}
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
