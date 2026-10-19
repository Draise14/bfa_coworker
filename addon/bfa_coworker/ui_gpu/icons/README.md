# Chat icons

These PNGs are icons from the **Bforartists** icon set
(`release/datafiles/icons_svg`, https://github.com/Bforartists/Bforartists),
GPL-3.0-or-later like this add-on. Bforartists' icons are drawn by the
Bforartists team, many of them based on Blender's icons.

The GPU chat draws them as textures, because Python can't draw built-in icons
from `gpu` code and Blender can't read SVG at runtime. Don't edit them by
hand: add the icon's name to `IMAGE_ICONS` in `../icons.py` and re-export:

    python _misc/build_chat_icons.py --source C:/path/to/Bforartists

The files are named `<blender icon name, lower case>_<pixels>.png`.
