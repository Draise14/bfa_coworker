# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Composite tool: set up a PBR material from Polyhaven textures in one call.

Downloads a Polyhaven texture set, creates a material, and wires up the
PBR node tree (Principled BSDF + Normal Map + displacement) — all in a
single ``execute_blender_code`` call.  Saves the LLM 3-5 round-trips.
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
            title="Set Up PBR Material",
            destructiveHint=True,
        )
    )
    def setup_pbr_material(
        material_name: str = "PBR_Material",
        base_color: str = "0.8, 0.8, 0.8, 1.0",
        metallic: float = 0.0,
        roughness: float = 0.5,
        use_polyhaven_textures: bool = False,
        polyhaven_asset_id: str = "",
    ) -> dict[str, object]:
        """
        Create a physically-based material with optional Polyhaven textures.

        Creates a new material with Principled BSDF, normal map, and
        displacement nodes wired up.  If *use_polyhaven_textures* is True,
        downloads the given *polyhaven_asset_id* texture set from Polyhaven
        and connects it to the Principled BSDF.

        *base_color* is a comma-separated RGBA string (e.g. "0.9, 0.5, 0.1, 1.0").
        """
        code = _build_pbr_code(
            material_name=material_name,
            base_color=base_color,
            metallic=metallic,
            roughness=roughness,
            use_polyhaven=use_polyhaven_textures,
            polyhaven_id=polyhaven_asset_id,
        )
        return send_code(code, strict_json=True)


def _build_pbr_code(
    material_name: str,
    base_color: str,
    metallic: float,
    roughness: float,
    use_polyhaven: bool,
    polyhaven_id: str,
) -> str:
    """Generate Blender Python code for the PBR material setup."""
    lines = [
        "import bpy, math, os, json, urllib.request",
        "from pathlib import Path",
        "",
        "result = {'status': 'ok', 'message': ''}",
        "",
        "# Create material",
        "mat = bpy.data.materials.new(name='{:s}')".format(material_name),
        "mat.use_nodes = True",
        "nodes = mat.node_tree.nodes",
        "links = mat.node_tree.links",
        "nodes.clear()",
        "",
        "# Create Principled BSDF and Material Output",
        "bsdf = nodes.new('ShaderNodeBsdfPrincipled')",
        "bsdf.location = (0, 0)",
        "output = nodes.new('ShaderNodeOutputMaterial')",
        "output.location = (400, 0)",
        "links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])",
        "",
        "# Set base parameters",
        "rgba = [float(x) for x in '{:s}'.split(',')]".format(base_color),
        "bsdf.inputs['Base Color'].default_value = rgba + [1.0] * (4 - len(rgba))",
        "bsdf.inputs['Metallic'].default_value = {:f}".format(metallic),
        "bsdf.inputs['Roughness'].default_value = {:f}".format(roughness),
        "",
        "# Normal map node",
        "normal_map = nodes.new('ShaderNodeNormalMap')",
        "normal_map.location = (-300, -300)",
        "links.new(normal_map.outputs['Normal'], bsdf.inputs['Normal'])",
        "",
    ]

    if use_polyhaven and polyhaven_id:
        lines += [
            "# Download Polyhaven textures",
            "_CACHE = Path.home() / '.cache' / 'bfa_coworker' / 'polyhaven'",
            "_CACHE.mkdir(parents=True, exist_ok=True)",
            "_BASE = 'https://dl.polyhaven.org/file/ph-assets'",
            "",
            "# Download texture maps",
            "_tex_types = {",
            "    'diffuse': ('diffuse_1k.jpg', 'Base Color'),",
            "    'rough': ('rough_1k.jpg', 'Roughness'),",
            "    'ao': ('ao_1k.jpg', 'None'),",
            "    'displacement': ('disp_1k.jpg', 'None'),",
            "}",
            "_tex_nodes = {}",
            "for _key, (_fname, _input) in _tex_types.items():",
            "    _url = '{:s}/textures/{:s}/{:s}'.format(_BASE, '{:s}', _fname)".format(polyhaven_id),
            "    _dest = _CACHE / '{:s}_{:s}'.format('{:s}', _fname)".format(polyhaven_id),
            "    if not _dest.exists():",
            "        try:",
            "            urllib.request.urlretrieve(_url, str(_dest))",
            "        except Exception:",
            "            continue",
            "    if _dest.exists():",
            "        _tex = bpy.data.images.load(str(_dest))",
            "        _tex_node = nodes.new('ShaderNodeTexImage')",
            "        _tex_node.image = _tex",
            "        _tex_node.location = (-600, -300 * len(_tex_nodes))",
            "        _tex_nodes[_key] = _tex_node",
            "        if _input != 'None':",
            "            links.new(_tex_node.outputs['Color'], bsdf.inputs[_input])",
            "",
            "if 'displacement' in _tex_nodes:",
            "    disp = nodes.new('ShaderNodeDisplacement')",
            "    disp.location = (200, -400)",
            "    links.new(_tex_nodes['displacement'].outputs['Color'], disp.inputs['Height'])",
            "    if not output.inputs.get('Displacement'):",
            "        output.inputs.new('NodeSocketFloat', 'Displacement')",
            "    links.new(disp.outputs['Displacement'], output.inputs['Displacement'])",
            "",
        ]

    lines += [
        "result['message'] = 'Created material: {:s}'".format(material_name),
        "result['material_name'] = '{:s}'".format(material_name),
    ]

    return "\n".join(lines)