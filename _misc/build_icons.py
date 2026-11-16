# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Render Coworker's operator icons to PNG (Tier 4m, Wand 0 #331; Bforartists style #563).

The sources are the SVGs in ``addon/bfa_coworker/icons/wand/``, composed from
Bforartists' own icon SVGs by ``_misc/compose_wand_icons.py`` (see ``SOURCES.md``
there).  This script renders each at 32 and 64 px, in a dark-theme and a
light-theme variant, next to the source::

    remove_silences.svg -> remove_silences_dark_32.png, remove_silences_dark_64.png,
                           remove_silences_light_32.png, remove_silences_light_64.png

The icons are filled with a dark outline, so one palette serves both themes, as
Bforartists' own icons do (style guide: plan 4m section 3.3): ``LIGHT_SWAP`` is
empty and the two variants are the same picture.  The add-on still loads the
variant that fits the theme, so a colour that has to change per theme can be
added to ``LIGHT_SWAP`` later without touching the add-on.

No Blender needed.  The renderer is Inkscape (as ``_misc/build_chat_icons.py``),
found on PATH or in its usual Windows folder; ``--inkscape`` overrides that.
Run it again after adding or editing an SVG::

    python _misc/build_icons.py
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ICON_DIR = os.path.join(_REPO, "addon", "bfa_coworker", "icons", "wand")
SIZES = (32, 64)
# Dark-theme colour -> light-theme colour.  Empty: one palette for both themes (#563).
LIGHT_SWAP: dict[str, str] = {}


def light_variant(svg_text: str, swap: dict[str, str] | None = None) -> str:
    """*svg_text* with the dark-theme colours swapped for the light theme's (all at once,
    so a swap's result is never swapped again)."""
    swap = {k.lower(): v for k, v in (LIGHT_SWAP if swap is None else swap).items()}
    if not swap:
        return svg_text
    pattern = re.compile("|".join(re.escape(k) for k in swap), re.IGNORECASE)
    return pattern.sub(lambda m: swap[m.group(0).lower()], svg_text)


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
