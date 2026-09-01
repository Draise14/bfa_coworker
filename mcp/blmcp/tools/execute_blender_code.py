# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# pylint: disable=C0114  # See tool doc-string.

__all__ = (
    "register",
)

from blmcp.tools_helpers.blender_cli import run_blender_cli, synced_blend_for_cli
from blmcp.tools_helpers.connection import send_code
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Execute Python Code",
            destructiveHint=True,
        )
    )
    def execute_blender_code(code: str) -> dict[str, object]:
        """
        Execute Python code in the connected Blender instance.

        The code runs in Blender's Python environment with full access to ``bpy``.
        To return data, assign a JSON-serialisable dict to a variable named ``result``.
        Deferred completion via ``check_is_finished`` is only supported by the
        interactive addon server, and is rejected in background mode.
        """
        # Not strict: LLM-generated code may return non-JSON-serializable values
        # (e.g. Blender objects). Use `repr` as a fallback instead of erroring.
        return send_code(code, strict_json=False)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Execute Python Code for Command-Line",
            destructiveHint=True,
        )
    )
    def execute_blender_code_for_cli(blend_file: str, code: str) -> dict[str, object]:
        """
        Execute Python code in a background Blender process.

        Opens *blend_file* with ``blender --background`` and runs *code*.
        Assign a dict to ``result`` to return data.
        """
        # LLM-generated code may return non-JSON-serializable values
        # (e.g. Blender objects), handled by `run_blender_cli` via `default=repr`.
        with synced_blend_for_cli(blend_file) as synced_path:
            value = run_blender_cli(synced_path, code)
            assert isinstance(value, dict), "Expected dict from `run_blender_cli`, got {!r}".format(type(value))
            return value
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Execute Blender Plan",
            destructiveHint=True,
        )
    )
    def execute_blender_plan(steps: list) -> dict[str, object]:
        """
        Execute a structured plan of Blender operations.

        Each step is a dict with either:
        - {"template": "name", "params": {...}} -- use a tested template
        - {"code": "..."} -- custom Python code

        Available templates: create_torus, create_cube, create_uv_sphere,
        create_cylinder, create_plane, add_material, three_point_lighting,
        add_subsurf, add_array, add_bevel, add_solidify, add_smooth,
        add_remesh, smooth_shade, auto_smooth, set_render_engine,
        setup_camera, keyframe_location, keyframe_rotation.

        Templates are pre-tested for Blender 5.3 and auto-correct common
        mistakes.  Use this instead of execute_blender_code when possible.
        """
        # Import the plan-to-code converter from the addon's
        # blender_templates module.
        try:
            from bfa_coworker.blender_templates import _plan_to_code, _render_template, _TEMPLATES
        except ImportError:
            return {"status": "error", "message": "Template system unavailable in the connected Blender addon."}
        code = _plan_to_code(steps)
        return send_code(code, strict_json=False)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="List Blender Templates",
            destructiveHint=False,
        )
    )
    def list_blender_templates() -> dict[str, object]:
        """
        List all available Blender code templates with their default parameters.

        Use execute_blender_plan() with template names from this list.
        Each template is pre-tested for Blender 5.3 and handles common
        API pitfalls automatically.
        """
        try:
            from bfa_coworker.blender_templates import _TEMPLATES, _TEMPLATE_DEFAULTS
        except ImportError:
            return {"status": "error", "message": "Template registry not available"}
        templates = {}
        for name, tmpl in _TEMPLATES.items():
            # Extract parameter names from the template string.
            import re
            params = list(set(re.findall(r"\{(\w+)\}", tmpl)))
            defaults = {k: _TEMPLATE_DEFAULTS.get(k, "?") for k in params}
            templates[name] = {"params": params, "defaults": defaults}
        return {"status": "ok", "templates": templates, "count": len(templates)}
