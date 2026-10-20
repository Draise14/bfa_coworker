# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Load tools only when needed (Tier 4, task "Engine 7").

Measured: the full tool list costs an agent ~14.5K tokens on every request.
With tools on demand (``BFACW_TOOLS_ON_DEMAND=1``, set by Coworker for its
External Agent) the server lists the **surface** tools only; a domain's tools
appear after ``load_tools(domains)``, which tells the client the list changed
(``notifications/tools/list_changed``).  Hidden tools stay callable by name,
and a tool in no domain is always listed, so nothing can get lost.

The sets are the same as the local loop's (``agent_controller._SURFACE_TOOLS``
and ``_TOOL_DOMAINS``); ``tests/test_tool_coverage.py`` keeps them equal.
"""

__all__ = (
    "DOMAINS",
    "SURFACE",
    "enable_on_demand",
    "load",
    "visible",
)

from typing import Any

SURFACE = frozenset({
    "execute_blender_code", "get_blendfile_summary_datablocks", "get_blendfile_summary_missing_files",
    "get_blendfile_summary_of_linked_libraries", "get_blendfile_summary_path_info",
    "get_blendfile_summary_usage_guess", "get_object_detail_summary", "get_objects_summary",
    "get_operation_history", "read_tool_output", "ask_user", "get_scene_digest", "update_plan",
    "get_attached_image",
    "get_polyhaven_status", "get_screenshot_of_window_as_image", "get_screenshot_of_window_as_json",
    "render_thumbnail_to_path", "get_python_api_docs", "search_api_docs", "search_manual_docs",
    "load_tools",
})

DOMAINS: dict[str, frozenset[str]] = {
    "animation": frozenset({
        "jump_to_view3d_object_by_name", "jump_to_view3d_object_data_by_name", "render_viewport_to_path",
        "batch_keyframe_insert",
    }),
    "material": frozenset({
        "download_polyhaven_asset", "search_polyhaven_assets", "get_screenshot_of_area_as_image",
        "render_viewport_to_path", "setup_pbr_material", "assign_material_to_objects", "load_asset_in_context",
    }),
    "modeling": frozenset({
        "jump_to_view3d_object_by_name", "jump_to_view3d_object_data_by_name", "jump_to_tab_by_name",
        "jump_to_tab_by_space_type", "get_screenshot_of_area_as_image", "set_collection_color_tag",
    }),
    "lighting": frozenset({
        "download_polyhaven_asset", "search_polyhaven_assets", "render_viewport_to_path",
        "get_screenshot_of_area_as_image", "three_point_lighting_rig",
    }),
    "rendering": frozenset({
        "render_viewport_to_path", "get_screenshot_of_area_as_image", "three_point_lighting_rig",
    }),
    "vse": frozenset({
        "jump_to_tab_by_name", "jump_to_tab_by_space_type",
    }),
    "geometry_nodes": frozenset({
        "jump_to_view3d_object_by_name", "jump_to_view3d_object_data_by_name", "get_screenshot_of_area_as_image",
        "get_active_node_tree", "get_node_group_interface", "wire_node_group",
    }),
    "assets": frozenset({
        "search_assets", "get_asset_libraries", "get_asset_tags", "list_asset_catalogs", "load_asset_in_context",
        "download_polyhaven_asset", "search_polyhaven_assets", "assign_material_to_objects",
        "place_asset_in_scene", "jump_to_asset_browser", "get_active_node_tree", "get_node_group_interface",
        "wire_node_group",
    }),
}

_DOMAIN_TOOLS = frozenset().union(*DOMAINS.values())
_state: dict[str, Any] = {"on_demand": False, "loaded": set()}


def on_demand() -> bool:
    return bool(_state["on_demand"])


def visible(name: str) -> bool:
    """Listed now: always without on-demand; else surface, loaded, or in no domain."""
    if not _state["on_demand"] or name in SURFACE or name not in _DOMAIN_TOOLS:
        return True
    return any(name in DOMAINS[d] for d in _state["loaded"])


def load(domains: list[str]) -> tuple[list[str], list[str]]:
    """Load *domains*; returns ``(loaded, unknown)``."""
    known = [d for d in domains if d in DOMAINS]
    _state["loaded"].update(known)
    return known, [d for d in domains if d not in DOMAINS]


def enable_on_demand(mcp: Any) -> None:
    """List the surface tools only (domains by load_tools), and say the list can change."""
    from mcp.server.lowlevel.server import NotificationOptions  # pylint: disable=import-error,no-name-in-module
    _state["on_demand"] = True
    manager = mcp._tool_manager  # pylint: disable=protected-access
    list_all = manager.list_tools
    manager.list_tools = lambda: [t for t in list_all() if visible(t.name)]
    server = mcp._mcp_server  # pylint: disable=protected-access
    make_options = server.create_initialization_options

    def options(notification_options: Any = None, experimental_capabilities: Any = None) -> Any:
        return make_options(notification_options or NotificationOptions(tools_changed=True),
                            experimental_capabilities or {})
    server.create_initialization_options = options
