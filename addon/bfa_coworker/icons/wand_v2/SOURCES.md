# Sources of the wand icons, second set (#563)

These icons are built from **Bforartists' own icon SVGs**
(`release/datafiles/icons_svg/` in the Bforartists source), used with the permission of
the Bforartists icon maintainer. Bforartists is licensed under the GNU GPL
(GPL-3.0-or-later, see Bforartists' `COPYING` and `release/license/license.md`; the icon
SVGs carry no licence of their own), the same licence as this add-on (`LICENSE`,
`blender_manifest.toml`: `SPDX:GPL-3.0-or-later`).

Unchanged copies of the files used are in `bfa_src/`, so the add-on stays self-contained.
`_misc/compose_wand_v2.py` takes their paths (moved, scaled, recoloured, clipped) into the
icons here; each copied path keeps a `data-src="<file>#<path id>"` attribute, and every
part drawn by us is labelled "(ours)". The style rules are in plan 4m section 3.3
(`_misc/plan_tier4m_coworker_operators.md`).

| Icon | Bforartists shapes used (`bfa_src/`) | Added by us, drawn to the same rules |
|---|---|---|
| `wand_a` (variant A) | `shaderfx.svg`: the stick (outline, body, band, tip), body recoloured dark, tip white; `particles.svg`: the three sparkles, scaled 0.58 | the stick's lit edge |
| `wand_b` (variant B; the badge) | `shaderfx.svg`: the stick, thickened 1.6x across, recoloured as in A; `particles.svg`: its big sparkle (clipped out), scaled 0.84 | the stick's lit edge |
| `wand_c` (variant C) | `shaderfx.svg`: the whole icon (stick and its three sparkles), only the stick recoloured | the stick's lit edge |
| `wand` | as `wand_b` | as `wand_b` |
| `remove_silences` | `seq_remove_gaps.svg`: the green join; `seq_strip.svg`: the strip colours and construction (outline `#320000`, lit `#fecb32`, shade `#fe9832`) | the two strip blocks, the waveforms |
| `captions` | `image.svg`: the whole picture (frame, sky, sun, hills) | the subtitle band and its two lines |
| `transcribe` | `sound.svg`: the speaker and its two inner waves (outer wave clipped off); `file_text.svg`: the page | -- |
| `translate` | `small_caps.svg`: the "A", recoloured `#323232`; `file_text.svg`: the card construction (white page, `#323232` outline) | the two cards, a simple four-bar ideogram (no font glyph copied) |
| `vertical` | `image.svg`: the picture, seen through a 9:16 window (clipped); `seq_strip.svg`: the frame colours | the tall frame, the dim wide frame behind it |
| `explain` | `question.svg`: the whole icon (disc, ring, "?"), scaled 0.88 | the speech-bubble tail |
| `tidy_nodes` | `node_top.svg`: the node (outline, header, body, socket; its top bar clipped off), three times; `nodetree.svg`: the link colour | the guide bar (as in Bforartists' `node_side`), the links |

Every icon except the `wand*` ones also carries the badge, i.e. the `wand_b` shapes from
`shaderfx.svg` and `particles.svg`.
