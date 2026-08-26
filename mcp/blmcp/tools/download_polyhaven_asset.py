# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
MCP tool for downloading Poly Haven assets and importing them into Blender.

Supports:
- HDRIs — world environment shader with proper node tree.
- Textures — full PBR material (Diffuse, Normal, Roughness, AO, Displacement).
- Models — glTF/FBX/OBJ import with textures, or .blend append.
"""

__all__ = (
    "register",
)

from pathlib import Path

from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module
from blmcp.tools_helpers.connection import send_code  # pylint: disable=import-error
from blmcp.tools_helpers.polyhaven_pbr import (  # pylint: disable=import-error
    CACHE_DIR,
    download_texture_set,
    resolve_polyhaven_files,
    build_pbr_material_code,
    build_blend_import_code,
)


_VALID_RESOLUTIONS = ("512", "1k", "2k", "4k", "8k")


def _import_hdri_code(filepath: str) -> str:
    """Return Blender Python code to set up an HDRI world environment."""
    safe_path = filepath.replace("\\", "\\\\")
    name = Path(filepath).stem
    return (
        "import bpy\n"
        "\n"
        "# Ensure world exists.\n"
        "if not bpy.data.worlds:\n"
        '    world = bpy.data.worlds.new("World")\n'
        "    bpy.context.scene.world = world\n"
        "else:\n"
        "    world = bpy.context.scene.world\n"
        '    if world is None:\n'
        '        world = bpy.data.worlds["World"]\n'
        "\n"
        "# Clear existing nodes.\n"
        "world.use_nodes = True\n"
        "nodes = world.node_tree.nodes\n"
        "links = world.node_tree.links\n"
        "nodes.clear()\n"
        "\n"
        "# Create nodes.\n"
        'tex_coord = nodes.new("ShaderNodeTexCoord")\n'
        'mapping = nodes.new("ShaderNodeMapping")\n'
        'env_tex = nodes.new("ShaderNodeTexEnvironment")\n'
        'bg = nodes.new("ShaderNodeBackground")\n'
        'output = nodes.new("ShaderNodeOutputWorld")\n'
        "\n"
        "# Set filepath.\n"
        f"env_tex.image = bpy.data.images.load('{safe_path}')\n"
        "\n"
        "# Connect nodes.\n"
        'links.new(tex_coord.outputs["Generated"], mapping.inputs["Vector"])\n'
        'links.new(mapping.outputs["Vector"], env_tex.inputs["Vector"])\n'
        'links.new(env_tex.outputs["Color"], bg.inputs["Color"])\n'
        'links.new(bg.outputs["Background"], output.inputs["Surface"])\n'
        "\n"
        "result = {'status': 'ok', 'message': 'HDRI applied to world'}\n"
    )


def _import_model_code(filepath: str) -> str:
    """Return Blender Python code to import a 3D model."""
    ext = Path(filepath).suffix.lower()
    safe_path = filepath.replace("\\", "\\\\")
    name = Path(filepath).stem
    if ext in (".gltf", ".glb"):
        import_line = 'bpy.ops.import_scene.gltf(filepath="' + safe_path + '")'
    elif ext == ".fbx":
        import_line = 'bpy.ops.import_scene.fbx(filepath="' + safe_path + '")'
    elif ext == ".obj":
        import_line = 'bpy.ops.import_scene.obj(filepath="' + safe_path + '")'
    else:
        return (
            "import bpy\n"
            'result = {"status": "error", "message": "Unsupported format: ' + ext + '"}\n'
        )

    return (
        "import bpy\n"
        "\n"
        "# Import the model.\n"
        + import_line + "\n"
        "\n"
        "result = {'status': 'ok', 'message': 'Imported model'}\n"
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(  # type: ignore[attr-defined]
            title="Download Poly Haven Asset",
            destructiveHint=True,
        )
    )
    def download_polyhaven_asset(
        asset_id: str,
        asset_type: str = "hdris",
        resolution: str = "2k",
    ) -> str:
        """
        Download a Poly Haven asset and import it into the current Blender scene.

        For **HDRIs**: creates a world environment shader with the HDRI mapped
        to a background node.

        For **textures**: downloads all PBR maps (Diffuse, Normal, Roughness,
        AO, Displacement) and builds a complete Principled BSDF material with
        all maps properly connected.  Uses the ``arm`` packed texture as a
        fallback for missing Roughness/Metallic maps.

        For **models**: imports the 3D model (glTF/FBX/OBJ) with textures.
        If a ``.blend`` file is available, appends objects/collections instead.

        The resolution is typically set by the user in Blender addon
        preferences and injected automatically.  You can override it here
        if needed.

        Args:
            asset_id: The asset ID from ``search_polyhaven_assets``
                (e.g. ``"concrete_floor_01"``).
            asset_type: ``"hdris"``, ``"textures"``, or ``"models"``.
            resolution: Download resolution — ``"512"``, ``"1k"``, ``"2k"``,
                ``"4k"``, or ``"8k"`` (HDRIs/textures).  Models always use
                the resolution that best matches.

        Returns:
            A summary of what was downloaded and imported, including which
            PBR maps were used.
        """
        if asset_type not in ("hdris", "textures", "models"):
            return (
                f"Invalid asset_type '{asset_type}'. "
                "Choose 'hdris', 'textures', or 'models'."
            )

        if resolution not in _VALID_RESOLUTIONS:
            return (
                f"Invalid resolution '{resolution}'. "
                f"Choose from: {', '.join(_VALID_RESOLUTIONS)}."
            )

        # ── Resolve all file URLs via the Polyhaven API ──
        resolved = resolve_polyhaven_files(asset_id, asset_type, resolution)
        if not resolved:
            return (
                f"Could not resolve Poly Haven download URLs for asset "
                f"'{asset_id}'. Check the asset ID and try again."
            )

        # ── HDRIs ──
        if asset_type == "hdris":
            hdri_info = resolved.get("hdri")
            if not hdri_info:
                return f"No HDRI files found for asset '{asset_id}' at {resolution}."

            url, filename = hdri_info
            dest = CACHE_DIR / "hdris" / asset_id / resolution / filename
            if not dest.exists():
                err = _download_with_progress(url, dest)
                if err:
                    return f"Download failed: {err}"

            code = _import_hdri_code(str(dest))
            result = send_code(code, strict_json=True)
            status = result.get("status", "error")
            if status == "ok":
                return (
                    f"Downloaded and applied HDRI '{asset_id}' ({resolution}) "
                    f"as world environment."
                )
            return (
                f"Downloaded '{asset_id}' but import failed: "
                f"{result.get('message', str(result))}"
            )

        # ── Textures (full PBR) ──
        if asset_type == "textures":
            downloaded = download_texture_set(
                asset_id, asset_type, resolution, CACHE_DIR
            )
            if not downloaded:
                return (
                    f"Failed to download texture maps for '{asset_id}'. "
                    "Check your network connection and try again."
                )

            # Build the PBR material code.
            # Map download keys to the paths expected by build_pbr_material_code.
            tex_paths = {}
            for key in ("diffuse", "normal", "roughness", "arm", "ao", "displacement"):
                if key in downloaded:
                    tex_paths[key] = downloaded[key]

            material_name = f"PH_{asset_id}"
            code = build_pbr_material_code(material_name, tex_paths)
            result = send_code(code, strict_json=True)
            status = result.get("status", "error")

            maps_used = [k for k in tex_paths if not k.startswith("tex_")]
            if status == "ok":
                return (
                    f"Created PBR material '{material_name}' from '{asset_id}' "
                    f"({resolution}).\n"
                    f"Maps: {', '.join(maps_used)}.\n"
                    f"Assigned to active object."
                )
            return (
                f"Downloaded {len(maps_used)} texture maps for '{asset_id}' "
                f"but material creation failed: "
                f"{result.get('message', str(result))}"
            )

        # ── Models ──
        # Check for .blend file first (highest quality — pre-made materials).
        blend_info = resolved.get("blend")
        if blend_info:
            url, filename = blend_info
            dest = CACHE_DIR / "models" / asset_id / resolution / filename
            if not dest.exists():
                err = _download_with_progress(url, dest)
                if err:
                    # Fall through to glTF/FBX import.
                    blend_info = None
                else:
                    # Download included textures into relative subdirectory.
                    tex_data = resolved.get("textures")
                    if tex_data and isinstance(tex_data, dict):
                        tex_dir = dest.parent / "textures"
                        tex_dir.mkdir(parents=True, exist_ok=True)
                        for _key, (tex_url, tex_fname) in tex_data.items():
                            tex_dest = tex_dir / tex_fname
                            if not tex_dest.exists():
                                _download_with_progress(tex_url, tex_dest)

                    code = build_blend_import_code(str(dest), asset_id)
                    result = send_code(code, strict_json=True)
                    status = result.get("status", "error")
                    if status == "ok":
                        return (
                            f"Appended model '{asset_id}' from .blend file "
                            f"({resolution})."
                        )
                    # .blend import failed — fall through to glTF.
                    print(
                        f"[Coworker] .blend import failed for {asset_id}: "
                        f"{result.get('message', '')} — trying glTF"
                    )

        # Fall back to glTF/FBX/OBJ import.
        for fmt_key in ("gltf", "glb", "fbx", "obj"):
            fmt_info = resolved.get(fmt_key)
            if fmt_info:
                url, filename = fmt_info
                dest = CACHE_DIR / "models" / asset_id / resolution / filename
                if not dest.exists():
                    err = _download_with_progress(url, dest)
                    if err:
                        return f"Download failed for {fmt_key}: {err}"

                code = _import_model_code(str(dest))
                result = send_code(code, strict_json=True)
                status = result.get("status", "error")
                if status == "ok":
                    return (
                        f"Imported model '{asset_id}' ({fmt_key}, {resolution})."
                    )
                return (
                    f"Downloaded '{asset_id}' but import failed: "
                    f"{result.get('message', str(result))}"
                )

        return f"No importable files found for model '{asset_id}'."


def _download_with_progress(url: str, dest: Path) -> str | None:
    """Download *url* to *dest*, returning error string or None on success."""
    from blmcp.tools_helpers.polyhaven_pbr import _download_file  # pylint: disable=import-error
    return _download_file(url, dest)
