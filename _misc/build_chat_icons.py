# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Export the GPU chat's icons from Bforartists' own SVG icon set (Tier 4, GPU 2).

The chat draws real, full-colour Bforartists icons.  Blender can't read SVG
at runtime and Python can't draw its built-in icons from ``gpu`` code, so
this script renders the few icons the chat uses to small PNGs with Inkscape,
once, into ``addon/bfa_coworker/ui_gpu/icons/``.  Run it again after adding a
name to ``ui_gpu.icons.IMAGE_ICONS`` or when the icon set changes::

    python _misc/build_chat_icons.py --source C:/path/to/Bforartists

*--source* is a Bforartists source checkout (it has
``release/datafiles/icons_svg``).  Inkscape is found on PATH or in its usual
Windows folder; ``--inkscape`` overrides that.
"""

from __future__ import annotations

import argparse
import ast
import os
import shutil
import subprocess
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_ICONS_PY = os.path.join(_REPO, "addon", "bfa_coworker", "ui_gpu", "icons.py")
_OUT = os.path.join(_REPO, "addon", "bfa_coworker", "ui_gpu", "icons")
_SIZES = (16, 24, 32, 48, 64)


def _image_icons() -> tuple[str, ...]:
    """IMAGE_ICONS from ui_gpu/icons.py, read without importing it."""
    tree = ast.parse(open(_ICONS_PY, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(t, "id", "") == "IMAGE_ICONS" for t in targets):
                return tuple(ast.literal_eval(node.value))
    raise SystemExit("IMAGE_ICONS not found in " + _ICONS_PY)


def _normalized(svg: str) -> str:
    """A copy of *svg* with bare hex colours fixed (``fill="fff"`` -> ``fill="#fff"``).

    Blender reads the bare form (e.g. the white Outliner collection icon), but
    Inkscape renders it black.  Returns the original path when nothing changes.
    """
    import re
    import tempfile
    text = open(svg, encoding="utf-8").read()
    fixed = re.sub(r'((?:fill|stroke)=")([0-9a-fA-F]{3}|[0-9a-fA-F]{6})"', r'\1#\2"', text)
    if fixed == text:
        return svg
    out = os.path.join(tempfile.gettempdir(), "bfacw_icon_" + os.path.basename(svg))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(fixed)
    return out


def _inkscape(given: str | None) -> str:
    for candidate in (given, shutil.which("inkscape"), r"C:\Program Files\Inkscape\bin\inkscape.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    raise SystemExit("Inkscape not found; pass --inkscape")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True, help="Bforartists source checkout")
    parser.add_argument("--inkscape", help="path to the inkscape executable")
    args = parser.parse_args()
    svg_dir = os.path.join(args.source, "release", "datafiles", "icons_svg")
    if not os.path.isdir(svg_dir):
        raise SystemExit("no icons_svg folder in " + args.source)
    inkscape = _inkscape(args.inkscape)
    os.makedirs(_OUT, exist_ok=True)
    names = _image_icons()
    missing = []
    for name in names:
        svg = os.path.join(svg_dir, name.lower() + ".svg")
        if not os.path.isfile(svg):
            missing.append(name)
            continue
        svg = _normalized(svg)
        for size in _SIZES:
            png = os.path.join(_OUT, "{:s}_{:d}.png".format(name.lower(), size))
            subprocess.run([inkscape, svg, "--export-type=png", "--export-filename=" + png,
                            "-w", str(size), "-h", str(size)], check=True, capture_output=True)
    print("exported {:d} icons x {:d} sizes to {:s}".format(len(names) - len(missing), len(_SIZES), _OUT))
    if missing:
        print("missing (kept as drawn shapes): " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
