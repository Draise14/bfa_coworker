# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Skills Loader — injects version-aware built-in skill files into the system prompt.

Always-loaded skills (injected into every conversation):
- Version-specific API changes (``blender_*.md``, cumulative ≤ current version)
- Best practices (``best_practices.md``)
- Naming conventions (``naming.md``)
- MCP tool guidance (``mcp_tools.md``)

Searchable domain skills live in ``mcp/blmcp/data/skills/`` and are
discovered by the LLM through the existing bundled-docs search tools.
"""

__all__ = (
    "get_always_loaded_skills",
    "get_domain_skills",
    "get_user_skills",
    "list_loaded_skills",
    "clear_cache",
)

from pathlib import Path

# ---------------------------------------------------------------------------
# Cache

_skills_cache: str | None = None
_skills_list: list[str] | None = None


# ---------------------------------------------------------------------------
# Public API

def get_always_loaded_skills(
    bpy_version: tuple[int, int, int] | None = None,
    custom_text: str = "",
) -> str:
    """Return concatenated built-in skill content for the system prompt.

    *bpy_version* — ``(5, 3, 0)`` or ``None`` to skip version-specific files.
    *custom_text* — optional user-provided custom skills text injected after
    built-in skills.

    Result is cached until ``clear_cache()`` is called.
    """
    # pylint: disable=global-statement
    global _skills_cache, _skills_list

    if _skills_cache is not None and _skills_list is not None:
        return _build_final(_skills_cache, custom_text)

    skills_dir = _get_skills_dir()
    parts: list[str] = []
    loaded: list[str] = []

    # 1. Version-specific files (cumulative: 5.3 loads 5.0-5.1 + 5.2 + 5.3).
    if bpy_version is not None:
        _major, _minor, _patch = bpy_version[:3]
        for fname in sorted(skills_dir.glob("blender_*.md")):
            # Parse version from filename like "blender_50_51.md" or "blender_52.md".
            ver_part = fname.stem[len("blender_"):]  # e.g. "50_51" or "52"
            if _version_loaded(ver_part, _minor):
                text = _read_skill(fname)
                if text:
                    parts.append(text)
                    loaded.append(fname.name)

    # 2. Always-loaded reference files.
    for name in ("best_practices.md", "naming.md", "mcp_tools.md"):
        fpath = skills_dir / name
        text = _read_skill(fpath)
        if text:
            parts.append(text)
            loaded.append(name)

    _skills_cache = "\n\n".join(parts) if parts else ""
    _skills_list = loaded

    return _build_final(_skills_cache, custom_text)


def list_loaded_skills() -> list[str]:
    """Return the list of built-in skill file names currently loaded."""
    # pylint: disable=global-statement
    global _skills_list
    if _skills_list is None:
        get_always_loaded_skills()
    return _skills_list or []


def clear_cache() -> None:
    """Clear the skills cache so the next call rebuilds from disk."""
    # pylint: disable=global-statement
    global _skills_cache, _skills_list
    _skills_cache = None
    _skills_list = None


# ---------------------------------------------------------------------------
# Helpers

def _get_skills_dir() -> Path:
    """Return the absolute path to the skills/ directory."""
    this_dir = Path(__file__).resolve().parent
    return this_dir


def _read_skill(path: Path) -> str | None:
    """Read a skill .md file, returning ``None`` if missing or unreadable."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _version_loaded(ver_part: str, current_minor: int) -> bool:
    """Return True if *ver_part* should be loaded for *current_minor*.

    Examples:
        "50_51" with current_minor=3 → True
        "52"     with current_minor=3 → True
        "53"     with current_minor=3 → True
        "54"     with current_minor=3 → False
    """
    # Extract the highest minor version from the filename.
    # "50_51" → 51, "52" → 52, "53_preview" → 53
    parts = ver_part.replace("_", " ").replace("-", " ").split()
    max_ver = 0
    for p in parts:
        try:
            v = int(p)
            max_ver = max(max_ver, v)
        except ValueError:
            continue
    return max_ver > 0 and max_ver <= current_minor


def _build_final(built_in: str, custom_text: str) -> str:
    """Assemble the final skills block."""
    result = built_in
    if custom_text and custom_text.strip():
        result += "\n\n## User Custom Skills\n{:s}".format(custom_text.strip())
    return result


# ── Domain-to-skill mapping ────────────────────────────────────────
# Maps domain keys (from _TOOL_DOMAINS in agent_controller.py) to
# skill filenames in mcp/blmcp/data/skills/ (or vendor/blmcp/data/skills/
# in deployed builds).

_DOMAIN_SKILL_MAP: dict[str, list[str]] = {
    "animation": ["animation.md"],
    "material": ["materials.md"],
    "modeling": ["mesh_editing.md", "modifiers.md"],
    "lighting": [],  # No dedicated skill file yet.
    "rendering": ["rendering.md"],
    "vse": [],
    "geometry_nodes": ["modifiers.md"],
    "assets": ["asset_browser.md"],
}


def get_domain_skills(domains: set[str]) -> str:
    """Load and concatenate skill files for the given domain set.

    Searches both the dev layout (``mcp/blmcp/data/skills/``) and the
    deployed layout (``vendor/blmcp/data/skills/``) for each skill file.
    Returns empty string if no domain skills are found or if no matching
    files exist.
    """
    if not domains:
        return ""

    # Collect skill filenames from all requested domains.
    filenames: set[str] = set()
    for d in domains:
        for fname in _DOMAIN_SKILL_MAP.get(d, []):
            filenames.add(fname)

    if not filenames:
        return ""

    # Search paths: dev layout first, then deployed layout.
    this_dir = Path(__file__).resolve().parent
    search_paths = [
        this_dir.parent.parent.parent / "mcp" / "blmcp" / "data" / "skills",  # dev
        this_dir.parent.parent / "vendor" / "blmcp" / "data" / "skills",  # deployed
    ]

    parts: list[str] = []
    for fname in sorted(filenames):
        for sp in search_paths:
            fpath = sp / fname
            text = _read_skill(fpath)
            if text:
                parts.append(text)
                break

    if not parts:
        return ""

    return "## Domain Skills\n{:s}".format("\n\n---\n\n".join(parts))


# ── User skill loader ──────────────────────────────────────────────
# Loads custom .md skill files from the user's SCRIPTS directory.
# Users can drop .md files into SCRIPTS/bfa_coworker_skills/ and they
# will be loaded alongside the built-in skills on every conversation.

_USER_SKILLS_DIR_NAME = "bfa_coworker_skills"


def get_user_skills() -> str:
    """Load and concatenate user skill files from the user skills directory.

    Scans ``SCRIPTS/bfa_coworker_skills/`` for ``.md`` files and returns
    their content as a single block prefixed with ``## User Skills``.
    Returns empty string if no files are found or the directory doesn't
    exist.
    """
    try:
        import bpy  # pylint: disable=import-error
        skills_dir = Path(bpy.utils.user_resource("SCRIPTS")) / _USER_SKILLS_DIR_NAME
    except Exception:
        return ""

    if not skills_dir.is_dir():
        return ""

    parts: list[str] = []
    for fpath in sorted(skills_dir.glob("*.md")):
        text = _read_skill(fpath)
        if text:
            parts.append(text)

    if not parts:
        return ""

    return "## User Skills\n{:s}".format("\n\n---\n\n".join(parts))
