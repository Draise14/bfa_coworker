# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Skills on demand (Tier 4, tasks "Runtime 2" #311 and #635).

Instead of pasting whole skill files into every prompt, a client gets a short
INDEX (each skill's name and one line) and reads a skill's full text with
``load_skill(name)`` when the task needs it.  One implementation for every
engine: the server's ``load_skill`` tool and its ``initial_instructions`` (External
Agent, External Harness) and the add-on's own loop (Local, Remote), which reads
this file by path as it reads ``tool_domains.py`` (``addon/.../skills/__init__.py``).

Where the one-line descriptions come from, in this order:

1. the file's front matter, ``description:`` (the skill format of #320 -- once
   the skill files carry it, nothing else here has to change);
2. ``_DESCRIPTIONS`` below, a small hand-edited table;
3. the file's first heading (a skill file nobody described still shows up).

The version-drift files (``blender_*.md``) are not skills on demand: they stay in
the prompt, they prevent hard API errors (the add-on loads them itself).

Keep this file importable with the standard library alone.
"""

__all__ = (
    "DOMAIN_SKILLS",
    "get_skill_index",
    "list_skills",
    "load_skill_body",
    "render_index",
)

import re
from pathlib import Path

# One line per skill (<= ~100 characters; the index is sent with every request).
_DESCRIPTIONS: dict[str, str] = {
    "best_practices": "Avoid the usual execute_blender_code failures: look APIs up, mode and selection, stale references, no duplicates.",
    "mcp_tools": "Which dedicated tool to use for what (scene, docs, navigation, screenshots, long jobs, sequencer, web).",
    "naming": "Prefixes for collections and objects so scenes stay organised and searchable.",
    "asset_browser": "Asset libraries: search, load, place assets, assign materials, node-group wiring.",
    "animation": "Keyframes: the safe way to insert, change and delete them; frame range, auto keying, data paths.",
    "collections": "Collection hierarchy, creating and moving them, the three visibility states.",
    "materials": "Materials and shading: asset-first workflow, PBR, node type names, node groups.",
    "mesh_editing": "Edit Mode and bmesh: entering and leaving, common operations, mesh data in Object Mode.",
    "modifiers": "Modifier stack order, geometry-nodes modifier, common operations, version-aware helper.",
    "operators": "Common bpy.ops calls: creating objects, modes, modifiers, parenting, join and separate.",
    "rendering": "Render engines and settings, rendering to a file, devices, world and environment.",
    "transformations": "Coordinate spaces, reading a world position, setting transforms, applying them, the 3D cursor.",
}

# Which skills go with which tool domain (``tool_domains.DOMAINS``): a loaded
# domain's result points at them.  The add-on's ``get_domain_skills`` reads this too.
DOMAIN_SKILLS: dict[str, tuple[str, ...]] = {
    "animation": ("animation",),
    "material": ("materials",),
    "modeling": ("mesh_editing", "modifiers"),
    "lighting": (),
    "rendering": ("rendering",),
    "vse": (),
    "geometry_nodes": ("modifiers",),
    "assets": ("asset_browser",),
}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Files in a skill folder that code reads and the chat never should (an editable chapter of a
# tool, #613): not in the index, not loadable.  A file's front matter can say it too: ``index: no``.
_NOT_SKILLS = frozenset({"video_highlights"})


def _skill_dirs() -> list[Path]:
    """Folders with skill files: the server's own (``data/skills``) and the add-on's
    (``addon/bfa_coworker/skills``; a checkout, or ``vendor/blmcp`` inside the zip)."""
    here = Path(__file__).resolve().parent
    return [
        here / "data" / "skills",
        here.parent.parent / "addon" / "bfa_coworker" / "skills",     # checkout: <repo>/mcp/blmcp
        here.parent.parent / "skills",                                 # zip: <addon>/vendor/blmcp
    ]


def _front_matter(text: str) -> dict[str, str]:
    """``key: value`` lines between the leading ``---`` lines (#320's format), or {}."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            out[key.strip().lower()] = value.strip().strip("\"'")
    return out


def _body(text: str) -> str:
    """The skill text without its front matter."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            rest = text[end + 4:]
            return rest.lstrip("\r\n").strip()
    return text.strip()


def _first_heading(text: str) -> str:
    for line in _body(text).splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip()
    return ""


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _files() -> dict[str, Path]:
    """name -> file, for every skill on demand (the first folder with a name wins)."""
    found: dict[str, Path] = {}
    for folder in _skill_dirs():
        try:
            paths = sorted(folder.glob("*.md"))
        except OSError:
            continue
        for path in paths:
            name = path.stem.lower()
            if name.startswith("blender_") or not _NAME_RE.match(name):
                continue            # the version-drift files stay in the prompt
            if name in _NOT_SKILLS or _front_matter(_read(path)).get("index", "").lower() in ("no", "false"):
                continue
            found.setdefault(name, path)
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def list_skills() -> list[tuple[str, str]]:
    """``[(name, one line)]`` sorted by name; see the module doc for the line's source."""
    out: list[tuple[str, str]] = []
    for name, path in sorted(_files().items()):
        text = _read(path)
        if not text:
            continue
        line = _front_matter(text).get("description") or _DESCRIPTIONS.get(name) or _first_heading(text) or name
        out.append((name, _one_line(line)))
    return out


def render_index(skills: list[tuple[str, str]] | None = None) -> str:
    """The index as a prompt section: ``## Skills on demand``, a line on how to use it, then
    one ``- name: line`` per skill.  The add-on puts it in the system prompt as its own
    section (so leaving the built-in notes out to fit keeps it), the server after its instructions."""
    skills = list_skills() if skills is None else skills
    if not skills:
        return ""
    lines = ["## Skills on demand",
             "Reference notes you can read when the task needs them. "
             "Call load_skill(name) first (it returns the full text) instead of guessing:"]
    lines.extend("- {:s}: {:s}".format(name, line) for name, line in skills)
    return "\n".join(lines)


def get_skill_index() -> str:
    """The index text for a prompt (empty when no skill file is found)."""
    return render_index()


def load_skill_body(name: str) -> str | None:
    """A skill's full text (without front matter), or ``None`` for an unknown name.

    *name* is the index name; ``best_practices.md``, a different case or spaces
    around it are taken too.  Only names from the index load: never a path."""
    key = str(name or "").strip().lower()
    if key.endswith(".md"):
        key = key[:-3]
    path = _files().get(key)
    if path is None:
        return None
    text = _read(path)
    return _body(text) if text else None
