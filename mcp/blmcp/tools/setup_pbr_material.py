# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Composite tool: set up a PBR material from Polyhaven textures in one call.

Downloads a Polyhaven texture set, creates a material, and wires up the
PBR node tree (Principled BSDF + Normal Map + Roughness + AO + Displacement)
— all in a single ``send_code`` call.  Saves the LLM 3-5 round-trips.

For non-Polyhaven use, also supports manual PBR parameters (base color,
metallic, roughness) without textures.
"""

__all__ = (
    "register",
)

from blmcp.tools_helpers.connection import send_code  # pylint: disable=import-error
from blmcp.tools_helpers.polyhaven_pbr import (  # pylint: disable=import-error
    CACHE_DIR,
    build_pbr_material_code,
    download_texture_set,
)
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
        polyhaven_resolution: str = "2k",
    ) -> dict[str, object]:
        """
        Create a physically-based material with optional Polyhaven textures.

        **Without Polyhaven** (manual mode):
        Creates a Principled BSDF material with the given base color,
        metallic, and roughness values.  Useful for quick material setup
        without external textures.

        **With Polyhaven** (texture mode):
        Downloads the full PBR texture set for the given asset from
        Polyhaven (Diffuse, Normal, Roughness, AO, Displacement) and
        builds a complete material with all maps connected.

        Args:
            material_name: Name for the new material datablock.
            base_color: Comma-separated RGBA string (e.g. ``"0.9, 0.5, 0.1, 1.0"``).
                Used as fallback when no Polyhaven diffuse texture is available.
            metallic: Metallic value (0.0 - 1.0).  Overridden by ARM texture
                when using Polyhaven textures.
            roughness: Roughness value (0.0 - 1.0).  Overridden by Polyhaven
                roughness/ARM texture when available.
            use_polyhaven_textures: Set ``True`` to download and apply
                Polyhaven textures for the given asset.
            polyhaven_asset_id: Polyhaven asset ID (e.g. ``"concrete_floor_01"``).
                Required when *use_polyhaven_textures* is ``True``.
            polyhaven_resolution: Download resolution — ``"512"``, ``"1k"``,
                ``"2k"``, ``"4k"``, or ``"8k"``.  Typically injected from
                addon preferences.

        Returns:
            A dict with ``status``, ``message``, and ``material_name``.
        """
        if use_polyhaven_textures and polyhaven_asset_id:
            return _setup_with_polyhaven(
                material_name=material_name,
                asset_id=polyhaven_asset_id,
                resolution=polyhaven_resolution,
                base_color=base_color,
                metallic=metallic,
                roughness=roughness,
            )
        return _setup_manual(
            material_name=material_name,
            base_color=base_color,
            metallic=metallic,
            roughness=roughness,
        )


def _setup_manual(
    material_name: str,
    base_color: str,
    metallic: float,
    roughness: float,
) -> dict[str, object]:
    """Create a PBR material with manual parameters (no textures)."""
    code = build_pbr_material_code(
        material_name=material_name,
        texture_map_paths={},  # No textures — uses fallback values.
        base_color=base_color,
        metallic=metallic,
        roughness=roughness,
    )
    return send_code(code, strict_json=True)


def _setup_with_polyhaven(
    material_name: str,
    asset_id: str,
    resolution: str,
    base_color: str,
    metallic: float,
    roughness: float,
) -> dict[str, object]:
    """Download Polyhaven textures and create a full PBR material."""
    # Download all texture maps.
    downloaded = download_texture_set(
        asset_id, "textures", resolution, CACHE_DIR
    )
    if not downloaded:
        # Fallback: create manual material and report the download failure.
        result = _setup_manual(material_name, base_color, metallic, roughness)
        if isinstance(result, dict):
            result["warning"] = (
                f"Failed to download Polyhaven textures for '{asset_id}'. "
                "Created material with manual parameters instead."
            )
        return result

    # Build PBR material code from downloaded maps.
    tex_paths = {}
    for key in ("diffuse", "normal", "roughness", "arm", "ao", "displacement"):
        if key in downloaded:
            tex_paths[key] = downloaded[key]

    code = build_pbr_material_code(
        material_name=material_name,
        texture_map_paths=tex_paths,
        base_color=base_color,
        metallic=metallic,
        roughness=roughness,
    )

    result = send_code(code, strict_json=True)

    # Enrich the result with download info.
    if isinstance(result, dict):
        maps_used = [k for k in tex_paths if not k.startswith("tex_")]
        result["maps_used"] = maps_used
        result["asset_id"] = asset_id
        result["resolution"] = resolution

    return result