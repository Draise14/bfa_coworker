# Sources of Coworker's operator icons (#563)

These icons are built from **Bforartists' own icon SVGs**
(`release/datafiles/icons_svg/` in the Bforartists source), used with the permission of
the Bforartists icon maintainer. Bforartists is licensed under the GNU GPL
(GPL-3.0-or-later, see Bforartists' `COPYING` and `release/license/license.md`; the icon
SVGs carry no licence of their own), the same licence as this add-on (`LICENSE`,
`blender_manifest.toml`: `SPDX:GPL-3.0-or-later`).

Unchanged copies of the files used are in `bfa_src/`, so the sources stay self-contained;
`build_addon.py` leaves that folder out of the built zip (the composed SVGs and their PNGs
ship). `_misc/compose_wand_icons.py` takes their paths (moved, scaled, recoloured, clipped)
into the icons here; each copied path keeps a `data-src="<file>#<path id>"` attribute, and
every part drawn by us is labelled "(ours)". The style rules are in plan 4m section 3.3
(`_misc/plan_tier4m_coworker_operators.md`).

| Icon | Bforartists shapes used (`bfa_src/`) | Added by us, drawn to the same rules |
|---|---|---|
| `wand` (the mark; also the badge) | `shaderfx.svg`: the stick (outline, body, band, tip), thickened 1.6x across, body grey, tip white; `particles.svg`: its big sparkle (clipped out), recoloured to the wizard purple | the stick's lit edge |
| `remove_silences` | `seq_remove_gaps.svg`: the green join; `seq_strip.svg`: the strip colours and construction (outline `#320000`, lit `#fecb32`, shade `#fe9832`) | the two strip blocks, a coarse sound pulse in each |
| `captions` | `image.svg`: the whole picture (frame, sky, sun, hills) | the subtitle band and its two lines |
| `transcribe` | `play_sound.svg`: the speaker and its green waves (outline made black); `file_text.svg`: the page (outline made black) | -- |
| `translate` (badge) | `small_caps.svg`: the "A", recoloured `#323232`; `file_text.svg`: the card construction (white page, `#323232` outline) | the two cards, a simple four-bar ideogram (no font glyph copied) |
| `vertical` | `image.svg`: the picture, seen through a 9:16 window (clipped), and its white border for the wide frame; `seq_strip.svg`: the tall frame's colours | the tall frame, the wide frame behind it |
| `explain` (badge) | `question.svg`: the whole icon (disc, ring, "?"), scaled 0.84 | the speech-bubble tail |
| `tidy_nodes` (badge) | `node_top.svg`: the node (outline, header, body, socket; its top bar clipped off), three times; `nodetree.svg`: the link colour | the guide bar (as in Bforartists' `node_side`), the links |

The icons marked "(badge)" also carry the `wand` shapes, small, in the bottom-right
corner: they are the tools that need Coworker's AI.

## Reframe layout icons (#606)

The seven `reframe_*` icons (Full Frame, Blur Fill, Tracked, the three splits, Track All) are drawn by
`_misc/build_reframe_icons.py` from scratch, as PNGs only (no SVG), in Bforartists' palette steps: the tall
frame uses the strip colours of `seq_strip` (outline `#320000`, lit `#fecb32`, shade `#fe9832`), the picture
inside the colours of `image` (sky `#75b6fe`, sun `#fdfe00` / `#fec100`, hills `#3c983c` / `#1e4c1e`). No path
is copied from a Bforartists icon: every shape is a rectangle, circle or triangle drawn by us. They carry no
wand badge (every layout works without the agent started; the tracking of Full Frame, Tracked needs a model).
