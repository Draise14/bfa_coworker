# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
MCP tool for searching Poly Haven assets.

Poly Haven provides free, high-quality HDRIs, textures, and 3D models.
No API key required.  Uses the public REST API at ``https://api.polyhaven.com/``.
"""

__all__ = (
    "register",
)

import json
import urllib.parse
import urllib.request
import urllib.error

from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


_POLYHAVEN_API = "https://api.polyhaven.com"


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(  # type: ignore[attr-defined]
            title="Search Poly Haven Assets",
            readOnlyHint=True,
        )
    )
    def search_polyhaven_assets(
        category: str = "hdris",
        query: str = "",
    ) -> str:
        """
        Search Poly Haven for free assets (HDRIs, textures, or models).

        Poly Haven provides free, high-quality CC0 assets.  No API key needed.

        Args:
            category: Asset category — ``"hdris"``, ``"textures"``, or ``"models"``.
            query: Optional search term to filter results (e.g. ``"sunset"``, ``"brick"``).

        Returns:
            A formatted list of matching assets with IDs, names, and download info.
        """
        if category not in ("hdris", "textures", "models"):
            return "Invalid category '{:s}'. Choose 'hdris', 'textures', or 'models'.".format(category)

        url = "{:s}/assets?type={:s}".format(_POLYHAVEN_API, category)
        if query:
            url += "&q={:s}".format(urllib.parse.quote(query))

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bfa-coworker/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.URLError as ex:
            return "Error contacting Poly Haven API: {:s}".format(str(ex))
        except json.JSONDecodeError:
            return "Error parsing Poly Haven API response."

        if not data:
            return "No assets found for '{:s}' with query '{:s}'.".format(category, query)

        results = []
        for i, (asset_id, info) in enumerate(sorted(data.items())[:20]):
            name = info.get("name", asset_id)
            results.append(
                "{:d}. **{:s}** (ID: `{:s}`)\n   Categories: {:s}".format(
                    i + 1,
                    name,
                    asset_id,
                    ", ".join(info.get("categories", [])),
                )
            )

        return "Found {:d} asset(s) in '{:s}':\n\n{:s}\n\nUse `download_polyhaven_asset` to download.".format(
            len(results), category, "\n\n".join(results)
        )
