# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Composite tool: set up a three-point lighting rig in one call.

Creates a key light, fill light, and rim light with proper positioning,
colors, and energy — all in a single ``execute_blender_code`` call.
Saves the LLM 3-5 round-trips.
"""

__all__ = (
    "register",
)

from blmcp.tools_helpers.connection import send_code
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Three-Point Lighting Rig",
            destructiveHint=True,
        )
    )
    def three_point_lighting_rig(
        target_object: str = "",
        key_energy: float = 1000.0,
        fill_energy: float = 500.0,
        rim_energy: float = 800.0,
        key_color: str = "1.0, 0.95, 0.9",
        fill_color: str = "0.9, 0.95, 1.0",
        rim_color: str = "1.0, 1.0, 1.0",
        distance: float = 5.0,
    ) -> dict[str, object]:
        """
        Create a three-point lighting rig (key, fill, rim) targeting an object.

        *target_object* — name of the object to light (empty string = active object).
        *distance* — how far from the target the lights are placed.
        Colors are comma-separated RGB strings (e.g. "1.0, 0.95, 0.9").
        """
        code = _build_rig_code(
            target=target_object,
            key_energy=key_energy,
            fill_energy=fill_energy,
            rim_energy=rim_energy,
            key_color=key_color,
            fill_color=fill_color,
            rim_color=rim_color,
            dist=distance,
        )
        return send_code(code, strict_json=True)


def _build_rig_code(
    target: str,
    key_energy: float,
    fill_energy: float,
    rim_energy: float,
    key_color: str,
    fill_color: str,
    rim_color: str,
    dist: float,
) -> str:
    """Generate Blender Python code for the three-point lighting rig."""
    return (
        "import bpy\n"
        "import math\n"
        "result = {'status': 'ok', 'lights': []}\n"
        "\n"
        "# Find target object\n"
        "if '{:s}':\n"
        "    target = bpy.data.objects.get('{:s}')\n"
        "elif bpy.context.active_object:\n"
        "    target = bpy.context.active_object\n"
        "else:\n"
        "    target = None\n"
        "\n"
        "if target is None:\n"
        "    result = {{'status': 'error', 'message': 'No target object found'}}\n"
        "else:\n"
        "    # Key light (45 degrees right, 45 degrees up)\n"
        "    key = bpy.data.lights.new(name='Key_Light', type='AREA')\n"
        "    key.energy = {key_energy:f}\n"
        "    _kc = [{key_color}]\n"
        "    key.color = (_kc[0], _kc[1], _kc[2])\n"
        "    key_obj = bpy.data.objects.new(name='Key_Light', object_data=key)\n"
        "    bpy.context.collection.objects.link(key_obj)\n"
        "    angle = math.radians(45)\n"
        "    key_obj.location = (\n"
        "        target.location.x + {dist:f} * math.cos(angle),\n"
        "        target.location.y + {dist:f} * math.sin(angle),\n"
        "        target.location.z + {dist:f} * 0.7,\n"
        "    )\n"
        "    key_obj.constraints.new(type='TRACK_TO')\n"
        "    key_obj.constraints['Track To'].target = target\n"
        "    result['lights'].append('Key_Light')\n"
        "\n"
        "    # Fill light (45 degrees left, 30 degrees up, softer)\n"
        "    fill = bpy.data.lights.new(name='Fill_Light', type='AREA')\n"
        "    fill.energy = {fill_energy:f}\n"
        "    _fc = [{fill_color}]\n"
        "    fill.color = (_fc[0], _fc[1], _fc[2])\n"
        "    fill_obj = bpy.data.objects.new(name='Fill_Light', object_data=fill)\n"
        "    bpy.context.collection.objects.link(fill_obj)\n"
        "    fill_obj.location = (\n"
        "        target.location.x - {dist:f} * math.cos(angle),\n"
        "        target.location.y + {dist:f} * math.sin(angle) * 0.5,\n"
        "        target.location.z + {dist:f} * 0.5,\n"
        "    )\n"
        "    fill_obj.constraints.new(type='TRACK_TO')\n"
        "    fill_obj.constraints['Track To'].target = target\n"
        "    result['lights'].append('Fill_Light')\n"
        "\n"
        "    # Rim light (behind and above, blueish/cool)\n"
        "    rim = bpy.data.lights.new(name='Rim_Light', type='AREA')\n"
        "    rim.energy = {rim_energy:f}\n"
        "    _rc = [{rim_color}]\n"
        "    rim.color = (_rc[0], _rc[1], _rc[2])\n"
        "    rim_obj = bpy.data.objects.new(name='Rim_Light', object_data=rim)\n"
        "    bpy.context.collection.objects.link(rim_obj)\n"
        "    rim_obj.location = (\n"
        "        target.location.x,\n"
        "        target.location.y - {dist:f} * 1.2,\n"
        "        target.location.z + {dist:f} * 0.8,\n"
        "    )\n"
        "    rim_obj.constraints.new(type='TRACK_TO')\n"
        "    rim_obj.constraints['Track To'].target = target\n"
        "    result['lights'].append('Rim_Light')\n"
        "\n"
        "    result['message'] = 'Created 3-point lighting rig: Key, Fill, Rim'\n"
    ).format(
        target=target, target=target,
        key_energy=key_energy, key_color=key_color,
        fill_energy=fill_energy, fill_color=fill_color,
        rim_energy=rim_energy, rim_color=rim_color,
        dist=dist,
    )