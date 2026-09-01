# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Shared helpers for Polyhaven PBR texture workflow.

Used by both ``download_polyhaven_asset`` and ``setup_pbr_material`` to avoid
duplicating the texture resolution, download, and Blender code-generation
logic.
"""

__all__ = (
    "POLYHAVEN_API",
    "POLYHAVEN_DL",
    "CACHE_DIR",
    "resolve_polyhaven_files",
    "download_texture_set",
    "build_pbr_material_code",
    "build_blend_import_code",
)

import json
import math
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path


POLYHAVEN_API = "https://api.polyhaven.com"
POLYHAVEN_DL = "https://dl.polyhaven.org/file/ph-assets"
CACHE_DIR = Path.home() / ".cache" / "bfa_coworker" / "polyhaven"

_USER_AGENT = "bfa-coworker/1.0"


# ── API helpers ────────────────────────────────────────────────────────


def _api_get(endpoint: str, timeout: int = 15) -> dict | None:
    """Fetch JSON from the Polyhaven API, returning ``None`` on error."""
    url = f"{POLYHAVEN_API}/{endpoint}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


def _download_file(url: str, dest: Path) -> str | None:
    """Download *url* to *dest*, returning error string or ``None`` on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=180) as resp:
            with open(str(dest), "wb") as fh:
                fh.write(resp.read())
        return None
    except (urllib.error.URLError, OSError) as ex:
        return str(ex)


# ── Texture map resolution ─────────────────────────────────────────────


def _pick_file_url(
    entry: dict,
    resolution: str,
    preferred_ext: tuple[str, ...] = ("jpg", "png", "exr"),
) -> tuple[str, str] | tuple[None, None]:
    """Pick a download URL from a Polyhaven file entry for *resolution*.

    Returns ``(url, filename)`` or ``(None, None)``.
    """
    desired = entry.get(resolution)
    if not isinstance(desired, dict):
        return None, None
    # Try preferred extensions first.
    for ext in preferred_ext:
        candidate = desired.get(ext)
        if isinstance(candidate, dict) and "url" in candidate:
            return candidate["url"], _url_filename(candidate["url"])
    # Fall back to any available extension.
    for val in desired.values():
        if isinstance(val, dict) and "url" in val:
            return val["url"], _url_filename(val["url"])
    return None, None


def _url_filename(url: str) -> str:
    """Extract the filename from a URL path."""
    return Path(urllib.parse.urlparse(url).path).name


def resolve_polyhaven_files(
    asset_id: str,
    asset_type: str,
    resolution: str,
) -> dict[str, tuple[str, str]]:
    """Resolve download URLs for all PBR maps of a Polyhaven asset.

    Parameters
    ----------
    asset_id:
        The Polyhaven asset slug (e.g. ``"concrete_floor_01"``).
    asset_type:
        One of ``"hdris"``, ``"textures"``, or ``"models"``.
    resolution:
        Desired resolution — ``"512"``, ``"1k"``, ``"2k"``, ``"4k"``, ``"8k"``.

    Returns
    -------
    dict
        Map name → ``(url, filename)`` for every available map.
        For HDRIs the key is ``"hdri"``.
        For models the key is the format (``"gltf"``, ``"blend"``, etc.)
        and may include ``"textures"`` with the full include dict.
    """
    data = _api_get(f"files/{asset_id}")
    if not data:
        return {}

    result: dict[str, tuple[str, str]] = {}

    if asset_type == "hdris":
        entry = data.get("hdri", {})
        url, fname = _pick_file_url(entry, resolution, ("hdr", "exr"))
        if url:
            result["hdri"] = (url, fname)
        return result

    if asset_type == "textures":
        # ── Diffuse / Base Color ──
        for color_key in ("Diffuse", "BaseColor", "Color", "Albedo"):
            if color_key in data and isinstance(data[color_key], dict):
                url, fname = _pick_file_url(data[color_key], resolution)
                if url:
                    result["diffuse"] = (url, fname)
                break

        # ── Normal Map (OpenGL — Blender default) ──
        if "nor_gl" in data and isinstance(data["nor_gl"], dict):
            url, fname = _pick_file_url(data["nor_gl"], resolution)
            if url:
                result["normal"] = (url, fname)

        # ── Roughness (separate map) ──
        if "Roughness" in data and isinstance(data["Roughness"], dict):
            url, fname = _pick_file_url(data["Roughness"], resolution)
            if url:
                result["roughness"] = (url, fname)

        # ── ARM packed texture (AO/Roughness/Metallic) ──
        if "arm" in data and isinstance(data["arm"], dict):
            url, fname = _pick_file_url(data["arm"], resolution)
            if url:
                result["arm"] = (url, fname)

        # ── Ambient Occlusion ──
        if "AO" in data and isinstance(data["AO"], dict):
            url, fname = _pick_file_url(data["AO"], resolution)
            if url:
                result["ao"] = (url, fname)

        # ── Displacement / Height ──
        for disp_key in ("Displacement", "displacement"):
            if disp_key in data and isinstance(data[disp_key], dict):
                url, fname = _pick_file_url(data[disp_key], resolution)
                if url:
                    result["displacement"] = (url, fname)
                break

        return result

    # ── Models ──
    # Try glTF first, then blend, fbx, obj.
    for model_key in ("gltf", "glb", "blend", "fbx", "obj", "usd"):
        candidate = data.get(model_key)
        if candidate is None:
            continue
        if isinstance(candidate, dict):
            # Models may be nested: {resolution: {format: {url, include, ...}}}
            res_entry = candidate.get(resolution, candidate)
            if isinstance(res_entry, dict):
                for fmt_key, fmt_val in res_entry.items():
                    if isinstance(fmt_val, dict) and "url" in fmt_val:
                        result[model_key] = (fmt_val["url"], _url_filename(fmt_val["url"]))
                        # Capture include textures if present.
                        if "include" in fmt_val:
                            result["textures"] = _collect_include_textures(
                                fmt_val["include"]
                            )
                        break
        if model_key in result:
            break

    return result


def _collect_include_textures(
    include: dict,
) -> dict[str, tuple[str, str]]:
    """Extract texture URLs from a glTF/blend ``include`` dict."""
    textures: dict[str, tuple[str, str]] = {}
    for rel_path, info in include.items():
        if isinstance(info, dict) and "url" in info:
            # Use the relative path stem as the key.
            key = Path(rel_path).stem
            textures[key] = (info["url"], _url_filename(info["url"]))
    return textures


# ── Download ───────────────────────────────────────────────────────────


def download_texture_set(
    asset_id: str,
    asset_type: str,
    resolution: str,
    cache_dir: Path | None = None,
) -> dict[str, Path]:
    """Download all PBR maps for a Polyhaven asset.

    Returns
    -------
    dict
        Map name → local file path for each downloaded map.
    """
    if cache_dir is None:
        cache_dir = CACHE_DIR

    resolved = resolve_polyhaven_files(asset_id, asset_type, resolution)
    if not resolved:
        return {}

    downloaded: dict[str, Path] = {}

    for map_name, (url, filename) in resolved.items():
        if map_name == "textures":
            # Download include textures into a subdirectory.
            sub_dir = cache_dir / asset_type / asset_id / resolution / "textures"
            for tex_key, (tex_url, tex_fname) in filename.items():
                dest = sub_dir / tex_fname
                if not dest.exists():
                    err = _download_file(tex_url, dest)
                    if err:
                        continue
                downloaded[f"tex_{tex_key}"] = dest
            continue

        dest = cache_dir / asset_type / asset_id / resolution / filename
        if not dest.exists():
            err = _download_file(url, dest)
            if err:
                continue
        downloaded[map_name] = dest

    return downloaded


# ── Blender PBR code generation ────────────────────────────────────────


def build_pbr_material_code(
    material_name: str,
    texture_map_paths: dict[str, Path],
    base_color: str = "0.8, 0.8, 0.8, 1.0",
    metallic: float = 0.0,
    roughness: float = 0.5,
) -> str:
    """Generate Blender Python code for a complete PBR material.

    Parameters
    ----------
    material_name:
        Name for the new material datablock.
    texture_map_paths:
        Map name → local file path.  Recognised keys:
        ``diffuse``, ``normal``, ``roughness``, ``arm``, ``ao``,
        ``displacement``.
    base_color:
        Comma-separated RGBA fallback when no diffuse texture is provided.
    metallic:
        Fallback metallic value (0-1).
    roughness:
        Fallback roughness value (0-1).
    """
    # Escape backslashes for Blender string literals.
    def _safe(p: Path) -> str:
        return str(p).replace("\\", "\\\\")

    has_diffuse = "diffuse" in texture_map_paths
    has_normal = "normal" in texture_map_paths
    has_roughness = "roughness" in texture_map_paths
    has_arm = "arm" in texture_map_paths and not has_roughness
    has_ao = "ao" in texture_map_paths
    has_displacement = "displacement" in texture_map_paths

    lines = [
        "import bpy",
        "",
        "# ── Create material ──",
        f"mat = bpy.data.materials.new(name='{material_name}')",
        "mat.use_nodes = True",
        "nodes = mat.node_tree.nodes",
        "links = mat.node_tree.links",
        "nodes.clear()",
        "",
        "# ── Principled BSDF and Material Output ──",
        "bsdf = nodes.new('ShaderNodeBsdfPrincipled')",
        "bsdf.location = (0, 0)",
        "output = nodes.new('ShaderNodeOutputMaterial')",
        "output.location = (600, 0)",
        "links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])",
        "",
    ]

    # ── Base Color / Diffuse ──
    if has_diffuse:
        safe_path = _safe(texture_map_paths["diffuse"])
        lines += [
            "# ── Diffuse / Base Color ──",
            f"_diff_img = bpy.data.images.load('{safe_path}')",
            "_diff_img.colorspace_settings.name = 'sRGB'",
            "_diff_node = nodes.new('ShaderNodeTexImage')",
            "_diff_node.image = _diff_img",
            "_diff_node.location = (-800, 300)",
            "",
        ]
        if has_ao:
            # Multiply diffuse × AO for ambient occlusion.
            safe_ao = _safe(texture_map_paths["ao"])
            lines += [
                "# ── AO mixed with Diffuse ──",
                f"_ao_img = bpy.data.images.load('{safe_ao}')",
                "_ao_img.colorspace_settings.name = 'Non-Color'",
                "_ao_node = nodes.new('ShaderNodeTexImage')",
                "_ao_node.image = _ao_img",
                "_ao_node.location = (-800, -100)",
                "_ao_mix = nodes.new('ShaderNodeMixRGB')",
                "_ao_mix.blend_type = 'MULTIPLY'",
                "_ao_mix.inputs['Fac'].default_value = 1.0",
                "_ao_mix.location = (-400, 200)",
                "links.new(_diff_node.outputs['Color'], _ao_mix.inputs['Color1'])",
                "links.new(_ao_node.outputs['Color'], _ao_mix.inputs['Color2'])",
                "links.new(_ao_mix.outputs['Color'], bsdf.inputs['Base Color'])",
                "",
            ]
        else:
            lines += [
                "links.new(_diff_node.outputs['Color'], bsdf.inputs['Base Color'])",
                "",
            ]
    else:
        # Fallback: use base_color parameter.
        rgba = [float(x) for x in base_color.split(",")]
        rgba = (rgba + [1.0] * 4)[:4]
        lines += [
            "# ── Base Color (fallback — no texture) ──",
            f"bsdf.inputs['Base Color'].default_value = {rgba}",
            "",
        ]

    # ── Metallic ──
    lines += [
        f"bsdf.inputs['Metallic'].default_value = {metallic}",
        "",
    ]

    # ── Normal Map ──
    if has_normal:
        safe_nor = _safe(texture_map_paths["normal"])
        lines += [
            "# ── Normal Map ──",
            f"_nor_img = bpy.data.images.load('{safe_nor}')",
            "_nor_img.colorspace_settings.name = 'Non-Color'",
            "_nor_tex = nodes.new('ShaderNodeTexImage')",
            "_nor_tex.image = _nor_img",
            "_nor_tex.location = (-800, -400)",
            "_normal_map = nodes.new('ShaderNodeNormalMap')",
            "_normal_map.location = (-400, -400)",
            "links.new(_nor_tex.outputs['Color'], _normal_map.inputs['Color'])",
            "links.new(_normal_map.outputs['Normal'], bsdf.inputs['Normal'])",
            "",
        ]

    # ── Roughness ──
    if has_roughness:
        safe_rough = _safe(texture_map_paths["roughness"])
        lines += [
            "# ── Roughness ──",
            f"_rough_img = bpy.data.images.load('{safe_rough}')",
            "_rough_img.colorspace_settings.name = 'Non-Color'",
            "_rough_tex = nodes.new('ShaderNodeTexImage')",
            "_rough_tex.image = _rough_img",
            "_rough_tex.location = (-800, -700)",
            "links.new(_rough_tex.outputs['Color'], bsdf.inputs['Roughness'])",
            "",
        ]
    elif has_arm:
        # Extract Roughness from ARM packed texture (Green channel).
        safe_arm = _safe(texture_map_paths["arm"])
        lines += [
            "# ── Roughness from ARM (Green channel) ──",
            f"_arm_img = bpy.data.images.load('{safe_arm}')",
            "_arm_img.colorspace_settings.name = 'Non-Color'",
            "_arm_tex = nodes.new('ShaderNodeTexImage')",
            "_arm_tex.image = _arm_img",
            "_arm_tex.location = (-800, -700)",
            "_arm_sep = nodes.new('ShaderNodeSeparateRGB')",
            "_arm_sep.location = (-500, -700)",
            "links.new(_arm_tex.outputs['Color'], _arm_sep.inputs['Image'])",
            "links.new(_arm_sep.outputs['G'], bsdf.inputs['Roughness'])",
            "",
            "# Extract Metallic from ARM (Blue channel).",
            "links.new(_arm_sep.outputs['B'], bsdf.inputs['Metallic'])",
            "",
        ]
    else:
        lines += [
            f"bsdf.inputs['Roughness'].default_value = {roughness}",
            "",
        ]

    # ── Displacement ──
    if has_displacement:
        safe_disp = _safe(texture_map_paths["displacement"])
        lines += [
            "# ── Displacement ──",
            f"_disp_img = bpy.data.images.load('{safe_disp}')",
            "_disp_img.colorspace_settings.name = 'Non-Color'",
            "_disp_tex = nodes.new('ShaderNodeTexImage')",
            "_disp_tex.image = _disp_img",
            "_disp_tex.location = (-800, -1000)",
            "_disp_node = nodes.new('ShaderNodeDisplacement')",
            "_disp_node.location = (200, -400)",
            "links.new(_disp_tex.outputs['Color'], _disp_node.inputs['Height'])",
            "links.new(_disp_node.outputs['Displacement'], output.inputs['Displacement'])",
            "",
        ]

    # ── Assign to active object ──
    lines += [
        "# ── Assign to active object ──",
        "_obj = bpy.context.view_layer.objects.active",
        "if _obj and _obj.type == 'MESH':",
        "    if _obj.data.materials:",
        "        _obj.data.materials[0] = mat",
        "    else:",
        "        _obj.data.materials.append(mat)",
        "",
        f"result = {{'status': 'ok', 'message': 'Created PBR material: {material_name}', "
        f"'material_name': '{material_name}', "
        f"'maps_used': {[k for k in texture_map_paths if not k.startswith('tex_')]}}}",
    ]

    return "\n".join(lines)


def build_blend_import_code(
    blend_path: str,
    asset_id: str,
) -> str:
    """Generate Blender Python code to append objects from a Polyhaven .blend file.

    Polyhaven model .blend files contain pre-made objects and collections
    with materials already set up.  This code appends them into the current
    scene.
    """
    safe_path = blend_path.replace("\\", "\\\\")
    return (
        "import bpy\n"
        "\n"
        f"_blend_path = '{safe_path}'\n"
        "\n"
        "# Append all objects from the .blend file.\n"
        "with bpy.data.libraries.load(_blend_path) as (data_src, data_dst):\n"
        "    data_dst.objects = data_src.objects\n"
        "    data_dst.materials = data_src.materials\n"
        "    data_dst.collections = data_src.collections\n"
        "\n"
        "# Link appended objects to the active collection.\n"
        "_col = bpy.context.collection\n"
        "_appended = []\n"
        "for _obj in data_dst.objects:\n"
        "    if _obj is not None:\n"
        "        _col.objects.link(_obj)\n"
        "        _appended.append(_obj.name)\n"
        "\n"
        "# Link appended collections.\n"
        "for _coll in data_dst.collections:\n"
        "    if _coll is not None and _coll.name not in bpy.context.scene.collection.children:\n"
        "        bpy.context.scene.collection.children.link(_coll)\n"
        "\n"
        f"result = {{'status': 'ok', 'message': 'Appended model from {asset_id}', "
        f"'objects': _appended}}"
    )
