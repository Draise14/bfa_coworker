# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
MCP tool for checking Poly Haven API availability.
"""

__all__ = (
    "register",
)

import json
import urllib.request
import urllib.error

from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


_POLYHAVEN_API = "https://api.polyhaven.com"


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(  # type: ignore[attr-defined]
            title="Poly Haven Status",
            readOnlyHint=True,
        )
    )
    def get_polyhaven_status() -> dict[str, object]:
        """
        Check whether the Poly Haven API is accessible.

        Returns:
            A dict with ``status`` and a ``message`` describing API
            availability and asset counts (consistent with other tools).
        """
        try:
            req = urllib.request.Request(
                _POLYHAVEN_API + "/assets?type=hdris&limit=1",
                headers={"User-Agent": "bfa-coworker/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            hdri_count = len(data) if data else 0
        except (urllib.error.URLError, json.JSONDecodeError) as ex:
            return {"status": "error", "message": "Poly Haven API is not accessible: {:s}".format(str(ex))}

        message = (
            "Poly Haven API is accessible.\n"
            "  - HDRIs: {:d}+ available\n"
            "  - Textures: thousands available (full PBR maps)\n"
            "  - Models: thousands available (glTF, blend, FBX)\n"
            "  - License: All CC0 (public domain, no attribution required)\n"
            "  - No API key required.\n\n"
            "PBR Texture Downloads:\n"
            "  When you download a texture, ALL maps are fetched automatically:\n"
            "  Diffuse (Base Color), Normal (OpenGL), Roughness, AO, Displacement.\n"
            "  If Roughness is unavailable, the ARM packed texture is used instead.\n\n"
            "Available Resolutions: 512, 1k, 2k (default), 4k, 8k\n"
            "  Resolution is set in Blender addon preferences and injected automatically.\n\n"
            "Use `search_polyhaven_assets` to find assets, "
            "and `download_polyhaven_asset` to download and import them."
        ).format(hdri_count)
        return {"status": "ok", "message": message}
