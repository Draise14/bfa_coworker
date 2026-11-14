# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Render the Coworker operators' wand icons to PNG (Tier 4m, Wand 0, #331).

The sources are our own SVGs in ``addon/bfa_coworker/icons/wand/``: a familiar
base glyph with a small wand badge in the bottom-right corner.  Each SVG is
drawn in its dark-theme colours; this script renders it at 32 and 64 px for a
dark theme, then swaps the glyph and star colours and renders the light-theme
variant, next to the source::

    remove_silences.svg -> remove_silences_dark_32.png, remove_silences_dark_64.png,
                           remove_silences_light_32.png, remove_silences_light_64.png

No Blender needed.  The renderer is Inkscape (as ``_misc/build_chat_icons.py``),
found on PATH or in its usual Windows folder; ``--inkscape`` overrides that.
Run it again after adding or editing an SVG::

    python _misc/build_icons.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ICON_DIR = os.path.join(_REPO, "addon", "bfa_coworker", "icons", "wand")
SIZES = (32, 64)
# Dark-theme colour -> light-theme colour.  The accents (blue, green, orange,
# purple) read on both, so they stay.
LIGHT_SWAP = {
    "#e6e6e6": "#3d3d3d",       # the glyph and the wand's stick
    "#ffc93c": "#d18f00",       # the wand's star: deeper so it shows on light grey
}


def light_variant(svg_text: str) -> str:
    """*svg_text* with the dark-theme colours swapped for the light theme's."""
    for dark, light in LIGHT_SWAP.items():
        svg_text = svg_text.replace(dark, light)
    return svg_text


def _inkscape(given: str | None) -> str:
    for candidate in (given, shutil.which("inkscape"), r"C:\Program Files\Inkscape\bin\inkscape.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    raise SystemExit("Inkscape not found; pass --inkscape")


def _render(inkscape: str, svg: str, png: str, size: int) -> None:
    subprocess.run([inkscape, "--export-type=png", "--export-filename=" + png,
                    "-w", str(size), "-h", str(size), svg],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--inkscape", help="path to the inkscape executable")
    args = parser.parse_args()
    inkscape = _inkscape(args.inkscape)
    names = sorted(f[:-4] for f in os.listdir(ICON_DIR) if f.endswith(".svg"))
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            source = os.path.join(ICON_DIR, name + ".svg")
            light = os.path.join(tmp, name + "_light.svg")
            with open(source, encoding="utf-8") as fh, open(light, "w", encoding="utf-8") as out:
                out.write(light_variant(fh.read()))
            for theme, svg in (("dark", source), ("light", light)):
                for size in SIZES:
                    _render(inkscape, svg, os.path.join(ICON_DIR, "{:s}_{:s}_{:d}.png".format(name, theme, size)), size)
            print("rendered", name)
    print("{:d} icons x 2 themes x {:d} sizes -> {:s}".format(len(names), len(SIZES), ICON_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
