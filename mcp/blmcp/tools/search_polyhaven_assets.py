# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
MCP tool for searching Poly Haven assets with smart client-side filtering.

Poly Haven provides free, high-quality HDRIs, textures, and 3D models.
No API key required.  Uses the public REST API at ``https://api.polyhaven.com/``.

The API returns the full asset catalog without functional server-side search,
so we fetch once (cached for 5 minutes) and filter/score client-side.
"""

__all__ = (
    "register",
)

import json
import math
import time
import urllib.parse
import urllib.request
import urllib.error

from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


_POLYHAVEN_API = "https://api.polyhaven.com"

# ── In-memory catalog cache (5 min TTL) ──────────────────────────────
_catalog_cache: dict[str, dict] = {}
_catalog_timestamps: dict[str, float] = {}
_CACHE_TTL = 300.0  # seconds


def _fetch_catalog(category: str) -> dict:
    """Fetch the full asset catalog for *category*, with caching."""
    now = time.monotonic()
    cached = _catalog_cache.get(category)
    ts = _catalog_timestamps.get(category, 0.0)
    if cached is not None and (now - ts) < _CACHE_TTL:
        return cached

    url = f"{_POLYHAVEN_API}/assets?type={category}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bfa-coworker/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError):
        return cached or {}

    _catalog_cache[category] = data
    _catalog_timestamps[category] = now
    return data


def _score_asset(
    asset_id: str,
    info: dict,
    query_words: list[str],
    tag_filter: list[str],
) -> float:
    """Score an asset for relevance against query and tag filters.

    Returns 0.0 if the asset should be excluded (tag filter mismatch).
    """
    name = info.get("name", asset_id).lower()
    tags = [t.lower() for t in info.get("tags", [])]
    categories = [c.lower() for c in info.get("categories", [])]
    description = info.get("description", "").lower()
    downloads = info.get("download_count", 0)

    # ── Tag filter: asset must match ALL filter tags ──
    if tag_filter:
        tag_set = set(tags)
        cat_set = set(categories)
        for ft in tag_filter:
            ft_lower = ft.lower().strip()
            if ft_lower not in tag_set and ft_lower not in cat_set:
                # Also check substring match in tags/categories.
                if not any(ft_lower in t for t in tag_set | cat_set):
                    return 0.0

    # ── Relevance scoring ──
    score = 0.0
    if not query_words:
        # No query — sort purely by popularity.
        score = math.log10(max(downloads, 1))
        return score

    name_lower = name.replace("_", " ").replace("-", " ")
    for word in query_words:
        wl = word.lower()
        # Exact name match (after normalizing separators).
        if wl == name_lower or wl == name_lower.replace(" ", ""):
            score += 100
        # Name starts with query word.
        elif name_lower.startswith(wl):
            score += 50
        # Name contains query word.
        elif wl in name_lower:
            score += 30
        # Tag matches.
        if any(wl in t for t in tags):
            score += 30
        # Category matches.
        if any(wl in c for c in categories):
            score += 20
        # Description matches.
        if wl in description:
            score += 10

    # Popularity tiebreaker.
    if downloads > 0:
        score += math.log10(downloads) * 0.5

    return score


def _format_downloads(count: int) -> str:
    """Format download count for compact display."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}K"
    return str(count)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(  # type: ignore[attr-defined]
            title="Search Poly Haven Assets",
            readOnlyHint=True,
        )
    )
    def search_polyhaven_assets(
        category: str = "textures",
        query: str = "",
        tags: str = "",
        sort_by: str = "relevance",
    ) -> str:
        """
        Search Poly Haven for free assets (HDRIs, textures, or models).

        Poly Haven provides free, high-quality CC0 assets.  No API key needed.

        Uses smart client-side search with relevance scoring across asset
        names, tags, categories, and descriptions.

        Args:
            category: Asset type — ``"hdris"``, ``"textures"``, or ``"models"``.
            query: Search term to find matching assets (e.g. ``"brick wall"``,
                ``"sunset"``, ``"wood floor"``).
            tags: Comma-separated tags to filter by (e.g. ``"brick, outdoor"``).
                Asset must match at least one tag or category.
            sort_by: ``"relevance"`` (default) for best match, or ``"popular"``
                for most downloaded first.

        Returns:
            A formatted list of up to 10 matching assets with IDs, names,
            tags, resolution, and download info.
        """
        if category not in ("hdris", "textures", "models"):
            return (
                f"Invalid category '{category}'. "
                "Choose 'hdris', 'textures', or 'models'."
            )

        catalog = _fetch_catalog(category)
        if not catalog:
            return (
                f"No assets found for category '{category}'. "
                "The Poly Haven API may be temporarily unavailable."
            )

        # Parse query into words for multi-word matching.
        query_words = [w.strip() for w in query.split() if w.strip()] if query else []

        # Parse tag filter.
        tag_filter = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        # Score all assets.
        scored: list[tuple[float, str, dict]] = []
        for asset_id, info in catalog.items():
            score = _score_asset(asset_id, info, query_words, tag_filter)
            if score > 0:
                scored.append((score, asset_id, info))

        # Sort by score descending.
        if sort_by == "popular":
            scored.sort(key=lambda x: x[2].get("download_count", 0), reverse=True)
        else:
            scored.sort(key=lambda x: x[0], reverse=True)

        # Take top 10.
        top = scored[:10]

        if not top:
            hint = ""
            if query:
                hint = f" for query '{query}'"
            if tag_filter:
                hint += f" with tags [{', '.join(tag_filter)}]"
            return f"No assets found in '{category}'{hint}. Try different search terms."

        # Format results.
        results = []
        for i, (_score, asset_id, info) in enumerate(top, 1):
            name = info.get("name", asset_id)
            asset_tags = info.get("tags", [])
            max_res = info.get("max_resolution", [])
            downloads = info.get("download_count", 0)
            thumb = info.get("thumbnail_url", "")

            # Format max resolution.
            res_str = ""
            if max_res and len(max_res) >= 1:
                res_val = max_res[0]
                if isinstance(res_val, (int, float)):
                    res_str = f"{int(res_val)}px"

            parts = [
                f"{i}. **{name}** (`{asset_id}`)",
            ]
            details = []
            if asset_tags:
                details.append(f"Tags: {', '.join(asset_tags[:6])}")
            if res_str:
                details.append(f"Max: {res_str}")
            if downloads:
                details.append(f"Downloads: {_format_downloads(downloads)}")
            if details:
                parts.append(f"   {' | '.join(details)}")
            if thumb:
                parts.append(f"   {thumb}")

            results.append("\n".join(parts))

        header = f"Found {len(top)} {'result' if len(top) == 1 else 'results'}"
        if query:
            header += f" for \"{query}\""
        header += f" in '{category}':"

        return (
            f"{header}\n\n"
            + "\n\n".join(results)
            + "\n\nUse `download_polyhaven_asset` with the asset ID to download."
        )
