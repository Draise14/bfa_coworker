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
    def get_polyhaven_status() -> str:
        """
        Check whether the Poly Haven API is accessible.

        Returns:
            A status message indicating API availability and asset counts.
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
            return "Poly Haven API is not accessible: {:s}".format(str(ex))

        return (
            "Poly Haven API is accessible.\n"
            "  - HDRIs: {:d}+ available\n"
            "  - Textures: thousands available\n"
            "  - Models: thousands available\n"
            "  - License: All CC0 (public domain, no attribution required)\n"
            "  - No API key required.\n\n"
            "Use `search_polyhaven_assets` to find assets, "
            "and `download_polyhaven_asset` to download and import them."
        ).format(hdri_count)
