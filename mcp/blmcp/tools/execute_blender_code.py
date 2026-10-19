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


def _load_blender_templates():  # type: ignore[no-untyped-def]
    """Load the add-on's ``blender_templates.py`` by file path, or return None.

    This MCP server runs in its own Python process where the add-on package
    (``bl_ext.<repo>.bfa_coworker``) is not importable, so the old
    ``from bfa_coworker.blender_templates import ...`` always failed and
    execute_blender_plan / list_blender_templates never worked (#101).
    The module is plain Python; find it next to this package instead:
    ``<addon>/vendor/blmcp`` in a build, ``<repo>/mcp/blmcp`` in a checkout.
    """
    import importlib.util
    import sys
    from pathlib import Path

    cached = sys.modules.get("_bfacw_blender_templates")
    if cached is not None:
        return cached
    here = Path(__file__).resolve().parent.parent  # .../blmcp
    for candidate in (
            here.parent.parent / "blender_templates.py",
            here.parent.parent / "addon" / "bfa_coworker" / "blender_templates.py",
    ):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("_bfacw_blender_templates", candidate)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules["_bfacw_blender_templates"] = module
            return module
    return None


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

        Use it for what the dedicated tools don't cover: creating and editing
        objects, modifiers, materials, animation.  Check first whether one of
        these fits -- they are faster and pre-tested on Blender 5.x:
        get_objects_summary / get_object_detail_summary (inspect the scene),
        execute_blender_plan (primitives, modifiers, lights, camera, keys),
        setup_pbr_material, download_polyhaven_asset, three_point_lighting_rig,
        search_api_docs (when unsure of an API).

        The code runs with full access to ``bpy`` on Blender's main thread
        (``bpy.context`` has the window, scene and active object).  Keep
        scripts short (about 40 lines).  To return data, assign a
        JSON-serialisable dict to ``result``.  An ``error`` lists the fix;
        ``preflight_notes`` in a successful result are advice, not failures.
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
        templates = _load_blender_templates()
        if templates is None:
            return {"status": "error", "message": "Template system unavailable in the connected Blender addon."}
        code = templates._plan_to_code(steps)
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
        module = _load_blender_templates()
        if module is None:
            return {"status": "error", "message": "Template registry not available"}
        _TEMPLATE_DEFAULTS = module._TEMPLATE_DEFAULTS
        templates = {}
        for name, tmpl in module._TEMPLATES.items():
            # Extract parameter names from the template string.
            import re
            params = list(set(re.findall(r"\{(\w+)\}", tmpl)))
            defaults = {k: _TEMPLATE_DEFAULTS.get(k, "?") for k in params}
            templates[name] = {"params": params, "defaults": defaults}
        return {"status": "ok", "templates": templates, "count": len(templates)}
