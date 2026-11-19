# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Render the Auto Captions dialog's own icons to PNG (#605).

* ``hl_pop``, ``hl_scale``, ``hl_rise``, ``hl_fade_in``: the "Highlight + ..." word animations.  Each is the icon
  of its plain motion (Bforartists' ``particles``, ``fullscreen_enter``, ``tria_up_bar``, ``image_alpha``) with
  Bforartists' own ``color`` wheel as a badge in the corner, so the pair reads as "the motion, and the colour".
* ``lang_<code>``: one badge per language of the Language list (the two-letter code on a coloured rounded
  square, drawn here), so no two languages share an icon.

Unchanged copies of the Bforartists SVGs used are in ``addon/bfa_coworker/icons/captions/src/`` (taken from
``release/datafiles/icons_svg/`` of the Bforartists source; GPL-3.0-or-later like this add-on; the folder is left
out of the built zip).  Output: ``addon/bfa_coworker/icons/captions/<name>_64.png`` (one palette for both themes,
as the wand icons).  Needs Inkscape (on PATH or in its usual Windows folder)::

    python _misc/build_caption_icons.py
"""

from __future__ import annotations

import colorsys
import os
import shutil
import subprocess
import sys
import tempfile

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ICON_DIR = os.path.join(_REPO, "addon", "bfa_coworker", "icons", "captions")
SRC = os.path.join(ICON_DIR, "src")
SIZE = 64
HIGHLIGHTS = {"hl_pop": "particles", "hl_scale": "fullscreen_enter", "hl_rise": "tria_up_bar",
              "hl_fade_in": "image_alpha"}
# Same order as preferences._LANGUAGES; zh_hans / zh_hant get a third letter to tell them apart.
LANGUAGES = ("en", "es", "fr", "de", "it", "pt", "nl", "pl", "ru", "uk", "tr", "ar", "hi", "id", "vi", "th",
             "zh_hans", "zh_hant", "ja", "ko")
LABELS = {"zh_hans": "ZHs", "zh_hant": "ZHt"}


def _uri(path: str) -> str:
    return "file:///" + path.replace("\\", "/")


def highlight_svg(base: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1600" '
            'height="1600" viewBox="0 0 1600 1600">'
            '<image xlink:href="{:s}" x="0" y="0" width="1160" height="1160"/>'
            '<image xlink:href="{:s}" x="760" y="760" width="840" height="840"/></svg>'
            .format(_uri(os.path.join(SRC, base + ".svg")), _uri(os.path.join(SRC, "color.svg"))))


def language_svg(code: str, index: int) -> str:
    label = LABELS.get(code, code.split("_")[0].upper())
    r, g, b = (int(255 * c) for c in colorsys.hls_to_rgb((index * 0.137) % 1.0, 0.36, 0.62))
    size = 560 if len(label) == 2 else 440
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1600" viewBox="0 0 1600 1600">'
            '<rect x="100" y="260" width="1400" height="1080" rx="260" fill="#323232"/>'
            '<rect x="150" y="310" width="1300" height="980" rx="220" fill="#{:02x}{:02x}{:02x}"/>'
            '<text x="800" y="{:d}" font-family="DejaVu Sans, Arial, sans-serif" font-weight="bold" '
            'font-size="{:d}" fill="#ffffff" text-anchor="middle">{:s}</text></svg>'
            .format(r, g, b, 800 + int(size * 0.36), size, label))


def _inkscape() -> str:
    for candidate in (shutil.which("inkscape"), r"C:\Program Files\Inkscape\bin\inkscape.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    raise SystemExit("Inkscape not found")


def main() -> int:
    inkscape = _inkscape()
    jobs = {name: highlight_svg(base) for name, base in HIGHLIGHTS.items()}
    jobs.update({"lang_" + code: language_svg(code, i) for i, code in enumerate(LANGUAGES)})
    with tempfile.TemporaryDirectory() as tmp:
        for name, svg in jobs.items():
            path = os.path.join(tmp, name + ".svg")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(svg)
            subprocess.run([inkscape, "--export-type=png", "--export-filename=" + os.path.join(
                ICON_DIR, "{:s}_{:d}.png".format(name, SIZE)), "-w", str(SIZE), "-h", str(SIZE), path],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("rendered", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
