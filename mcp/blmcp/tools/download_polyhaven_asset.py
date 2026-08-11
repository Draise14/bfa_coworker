# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
MCP tool for downloading Poly Haven assets and importing them into Blender.

Supports HDRIs (world environment), textures (PBR material), and models (glTF/FBX/OBJ).
"""

__all__ = (
    "register",
)

import json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module
from blmcp.tools_helpers.connection import send_code  # pylint: disable=import-error


_POLYHAVEN_API = "https://api.polyhaven.com"
_POLYHAVEN_DL = "https://dl.polyhaven.org/file/ph-assets"
_CACHE_DIR = Path.home() / ".cache" / "bfa_coworker" / "polyhaven"


def _download_file(url: str, dest: Path) -> str | None:
    """Download *url* to *dest*, returning error string or None on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bfa-coworker/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(str(dest), "wb") as fh:
                fh.write(resp.read())
        return None
    except (urllib.error.URLError, OSError) as ex:
        return str(ex)


def _resolve_polyhaven_file_url(asset_id: str, asset_type: str, resolution: str) -> tuple[str, str] | tuple[None, None]:
    """Resolve a Poly Haven asset download URL via the public API."""
    url = f"{_POLYHAVEN_API}/files/{asset_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bfa-coworker/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError):
        return None, None

    def _pick_url(entry: dict[str, object], preferred_ext: tuple[str, ...]) -> str | None:
        if not isinstance(entry, dict):
            return None
        desired = entry.get(resolution)
        if not isinstance(desired, dict):
            return None
        for ext in preferred_ext:
            candidate = desired.get(ext)
            if isinstance(candidate, dict) and "url" in candidate:
                return candidate["url"]
        for candidate in desired.values():
            if isinstance(candidate, dict) and "url" in candidate:
                return candidate["url"]
        return None

    asset_url: str | None = None
    if asset_type == "hdris":
        entry = data.get("hdri", {})
        asset_url = _pick_url(entry, ("hdr", "exr"))
    elif asset_type == "textures":
        texture_entry = None
        for color_key in ("Diffuse", "BaseColor", "Color", "Albedo"):
            if color_key in data and isinstance(data[color_key], dict):
                texture_entry = data[color_key]
                break
        if texture_entry is None:
            for value in data.values():
                if isinstance(value, dict) and resolution in value:
                    texture_entry = value
                    break
        if texture_entry is not None:
            asset_url = _pick_url(texture_entry, ("jpg", "png", "exr"))
    else:
        for model_key in ("gltf", "glb", "fbx", "obj", "usd"):
            candidate = data.get(model_key)
            if candidate is not None:
                if isinstance(candidate, dict) and "url" in candidate:
                    asset_url = candidate["url"]
                elif isinstance(candidate, dict):
                    for value in candidate.values():
                        if isinstance(value, dict) and "url" in value:
                            asset_url = value["url"]
                            break
                elif isinstance(candidate, str):
                    asset_url = candidate
                break

    if not asset_url:
        return None, None

    filename = Path(urllib.parse.urlparse(asset_url).path).name
    return asset_url, filename


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

        For HDRIs: creates a world environment shader.
        For textures: creates a PBR material node tree.
        For models: imports the 3D model.

        Args:
            asset_id: The asset ID from ``search_polyhaven_assets`` (e.g. ``"sunset_meadow"``).
            asset_type: ``"hdris"``, ``"textures"``, or ``"models"``.
            resolution: Download resolution — ``"1k"``, ``"2k"``, ``"4k"``, ``"8k"`` (HDRIs/textures).

        Returns:
            A summary of what was downloaded and imported.
        """
        if asset_type not in ("hdris", "textures", "models"):
            return "Invalid asset_type '{:s}'. Choose 'hdris', 'textures', or 'models'.".format(asset_type)

        if resolution not in ("1k", "2k", "4k", "8k"):
            return "Invalid resolution '{:s}'. Choose '1k', '2k', '4k', or '8k'.".format(resolution)

        # Resolve the current download URL for this asset via the Poly Haven API.
        file_url, filename = _resolve_polyhaven_file_url(asset_id, asset_type, resolution)
        if not file_url:
            return "Download failed: could not resolve Poly Haven download URL for asset '{:s}'".format(asset_id)

        dest = _CACHE_DIR / asset_type / filename

        # Download if not cached.
        if not dest.exists():
            error = _download_file(file_url, dest)
            if error:
                return "Download failed: {:s}".format(error)

        # Import into Blender via the bridge.
        if asset_type == "hdris":
            code = _import_hdri_code(str(dest))
        elif asset_type == "textures":
            code = _import_texture_code(str(dest), asset_id)
        else:
            code = _import_model_code(str(dest))

        result = send_code(code, strict_json=True)
        status = result.get("status", "error")
        if status == "ok":
            return "Downloaded and imported '{:s}' ({:s}, {:s}) successfully.".format(
                asset_id, asset_type, resolution
            )
        else:
            return "Downloaded '{:s}' but import failed: {:s}".format(
                asset_id, result.get("message", str(result))
            )


def _import_hdri_code(filepath: str) -> str:
    """Return Blender Python code to set up an HDRI world environment."""
    safe_path = filepath.replace("\\", "\\\\")
    name = Path(filepath).stem
    return (
        'import bpy\n'
        '\n'
        '# Ensure world exists.\n'
        'if not bpy.data.worlds:\n'
        '    world = bpy.data.worlds.new("World")\n'
        '    bpy.context.scene.world = world\n'
        'else:\n'
        '    world = bpy.context.scene.world\n'
        '    if world is None:\n'
        '        world = bpy.data.worlds["World"]\n'
        '\n'
        '# Clear existing nodes.\n'
        'world.use_nodes = True\n'
        'nodes = world.node_tree.nodes\n'
        'links = world.node_tree.links\n'
        'nodes.clear()\n'
        '\n'
        '# Create nodes.\n'
        'tex_coord = nodes.new("ShaderNodeTexCoord")\n'
        'mapping = nodes.new("ShaderNodeMapping")\n'
        'env_tex = nodes.new("ShaderNodeTexEnvironment")\n'
        'bg = nodes.new("ShaderNodeBackground")\n'
        'output = nodes.new("ShaderNodeOutputWorld")\n'
        '\n'
        '# Set filepath.\n'
        'env_tex.image = bpy.data.images.load("' + safe_path + '")\n'
        '\n'
        '# Connect nodes.\n'
        'links.new(tex_coord.outputs["Generated"], mapping.inputs["Vector"])\n'
        'links.new(mapping.outputs["Vector"], env_tex.inputs["Vector"])\n'
        'links.new(env_tex.outputs["Color"], bg.inputs["Color"])\n'
        'links.new(bg.outputs["Background"], output.inputs["Surface"])\n'
        '\n'
        'result = {"status": "ok", "message": "HDRI \'' + name + '\' applied to world"}\n'
    )


def _import_texture_code(filepath: str, asset_id: str) -> str:
    """Return Blender Python code to create a PBR material from a texture."""
    safe_path = filepath.replace("\\", "\\\\")
    return (
        'import bpy\n'
        '\n'
        '# Create a new material.\n'
        'mat = bpy.data.materials.new("PH_' + asset_id + '")\n'
        'mat.use_nodes = True\n'
        'nodes = mat.node_tree.nodes\n'
        'links = mat.node_tree.links\n'
        'nodes.clear()\n'
        '\n'
        '# Create nodes.\n'
        'tex_node = nodes.new("ShaderNodeTexImage")\n'
        'tex_node.image = bpy.data.images.load("' + safe_path + '")\n'
        'bsdf = nodes.new("ShaderNodeBsdfPrincipled")\n'
        'output = nodes.new("ShaderNodeOutputMaterial")\n'
        '\n'
        '# Connect.\n'
        'links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])\n'
        'links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])\n'
        '\n'
        '# Assign to active object if one is selected.\n'
        'obj = bpy.context.active_object\n'
        'if obj and obj.type == \'MESH\':\n'
        '    if obj.data.materials:\n'
        '        obj.data.materials[0] = mat\n'
        '    else:\n'
        '        obj.data.materials.append(mat)\n'
        '\n'
        'result = {"status": "ok", "message": "Texture \'' + asset_id + '\' applied as material"}\n'
    )


def _import_model_code(filepath: str) -> str:
    """Return Blender Python code to import a 3D model."""
    ext = Path(filepath).suffix.lower()
    safe_path = filepath.replace("\\", "\\\\")
    name = Path(filepath).stem
    # Build the import operator line based on extension.
    if ext in (".gltf", ".glb"):
        import_line = 'bpy.ops.import_scene.gltf(filepath="' + safe_path + '")'
    elif ext == ".fbx":
        import_line = 'bpy.ops.import_scene.fbx(filepath="' + safe_path + '")'
    elif ext == ".obj":
        import_line = 'bpy.ops.import_scene.obj(filepath="' + safe_path + '")'
    else:
        import_line = 'result = {"status": "error", "message": "Unsupported format: ' + ext + '"}\n    raise SystemExit(0)'

    return (
        'import bpy\n'
        '\n'
        '# Import the model.\n'
        + import_line + '\n'
        '\n'
        'result = {"status": "ok", "message": "Imported model from \'' + name + '\'"}\n'
    )
