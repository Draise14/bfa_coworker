# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Compose the second wand icon set (#563) from Bforartists' own icon SVGs.

The pieces come from copies of Bforartists source icons in
``addon/bfa_coworker/icons/wand_v2/bfa_src/`` (GPL-3.0-or-later, used with the
Bforartists icon maintainer's permission; see ``icons/wand_v2/SOURCES.md``).
Each recipe below takes paths from those files (moved, scaled, recoloured or
clipped) and adds the few parts no source icon has, drawn with the same rules
(16-unit grid, flat fills, a light/shade step, a dark outline one unit wide).
The result is one plain SVG per icon in ``icons/wand_v2/``, which Inkscape can
open and edit::

    python _misc/compose_wand_v2.py            # all icons and the wand variants
    python _misc/compose_wand_v2.py captions   # only some

Once an SVG has been edited by hand, stop composing it (name the others only).
``_misc/build_icons.py --set wand_v2`` renders the PNGs.
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT_DIR = os.path.join(_REPO, "addon", "bfa_coworker", "icons", "wand_v2")
SRC_DIR = os.path.join(OUT_DIR, "bfa_src")
_SVG = "{http://www.w3.org/2000/svg}"

# -- palette (Bforartists' own icon colours; style guide: plan 4m section 3.3) -------------
OUTLINE = "#323232"         # neutral outline
BLACK = "#000000"
WHITE = "#fefefe"
WHITE_SHADE = "#cbcbcb"
# Theme tokens: drawn in the dark-theme colour; build_icons.py swaps them for the light theme.
STICK = "#666666"           # the wand's stick (light theme: #323232)
STICK_LIT = "#999999"       # its lit edge (light theme: #666666)
# Sequencer / video (seq_strip, seq_remove_gaps)
SEQ_LIT, SEQ, SEQ_DARK, SEQ_GREEN = "#fecb32", "#fe9832", "#320000", "#00fe00"
# Help (question)
HELP_LIT, HELP, HELP_DARK = "#98cbfe", "#6598fe", "#003265"
# Nodes (node_top, nodetree)
NODE_HEAD, NODE_BODY, NODE_LINK, NODE_SOCKET = "#c5c5c5", "#65a4fe", "#3265fe", "#f3fe00"
# Sparkle (particles, shaderfx)
SPARK_RIM, SPARK, SPARK_CORE = "#fecb32", "#fefe00", "#fefefe"


# -- reading source icons -----------------------------------------------------------------

def _style(el) -> dict:
    out = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    if el.get("fill") and "fill" not in out:
        out["fill"] = el.get("fill")
    return out


def bfa(name: str, *, only=None, skip=(), recolour=None, transform: str = "", clip: str | None = None,
        label: str = "") -> str:
    """The visible paths of Bforartists icon *name* as a ``<g>`` in 16-unit coordinates.

    *only* / *skip*: path ids to keep or drop.  *recolour*: ``{old_fill: new_fill}``.
    *transform*: applied on top (16-unit space).  *clip*: a clip-path id.
    """
    root = ET.parse(os.path.join(SRC_DIR, name + ".svg")).getroot()
    vb = [float(v) for v in (root.get("viewBox") or "0 0 1600 1600").split()]
    unit = 16.0 / vb[2]
    recolour = {k.lower(): v for k, v in (recolour or {}).items()}
    paths = []

    def walk(el, chain):
        t = el.get("transform")
        chain = chain + ([t] if t else [])
        tag = el.tag.replace(_SVG, "")
        if tag == "path":
            st = _style(el)
            fill = st.get("fill", "#000000").lower()
            pid = el.get("id", "")
            if fill != "none" and (only is None or pid in only) and pid not in skip:
                fill = recolour.get(fill, fill)
                extra = ""
                if st.get("fill-rule"):
                    extra += ' fill-rule="{}"'.format(st["fill-rule"])
                if st.get("fill-opacity") and st["fill-opacity"] != "1":
                    extra += ' fill-opacity="{}"'.format(st["fill-opacity"])
                tr = ' transform="{}"'.format(" ".join(chain)) if chain else ""
                paths.append('<path data-src="{}#{}" fill="{}"{}{} d="{}"/>'.format(
                    name, pid, fill, extra, tr, " ".join(el.get("d").split())))
        for child in el:
            if child.tag.replace(_SVG, "") in ("defs", "metadata"):
                continue
            walk(child, chain)

    walk(root, [])
    attrs = ' transform="{} scale({:g})"'.format(transform, unit).replace('" ', '"')
    if clip:
        attrs = ' clip-path="url(#{})"'.format(clip) + attrs
    lab = label or name
    return '<g inkscape:label="{}"{}>\n    {}\n  </g>'.format(lab, attrs, "\n    ".join(paths))


def group(label: str, body: str, attrs: str = "") -> str:
    return '<g inkscape:label="{}"{}>\n  {}\n</g>'.format(label, attrs, body)


# -- the wand ------------------------------------------------------------------------------

def _across(k: float, cx: float, cy: float) -> str:
    """Scale by *k* across a 45-degree stick through (cx, cy), keeping its length."""
    return "translate({0:g},{1:g}) rotate(-45) scale(1,{2:g}) rotate(45) translate({3:g},{4:g})".format(
        cx, cy, k, -cx, -cy)


STICK_RECOLOUR = {"#007ffe": STICK, "#a9d8fe": WHITE, "#fefefe": WHITE}
SHADERFX_STICK = ("path819", "path821", "path827", "path829")


def stick_lit(transform: str) -> str:
    """The lit edge of shaderfx's stick: a thin strip along its upper-left side (our addition)."""
    return ('<path inkscape:label="lit edge (ours)" fill="{}" transform="{}" '
            'd="M 1.75,13.5 L 6.2,9.05 L 6.5,9.35 L 2.05,13.8 Z"/>').format(STICK_LIT, transform)


def sparkle(cx: float, cy: float, r: float, label: str = "sparkle") -> str:
    """particles' big sparkle (clipped out of the three) centred on (cx, cy), *r* = half its height."""
    # the big sparkle in particles.svg: centre (7.05, 11.1), half height 5.0
    s = r / 5.0
    return bfa("particles", clip="clip-big-sparkle", label=label,
               transform="translate({:g},{:g}) scale({:g}) translate(-7.05,-11.1)".format(cx, cy, s))


def _clip(cid: str, d: str) -> str:
    """A clip for a bfa() group, given in 16-unit coordinates (bfa() scales its group by 1/100)."""
    return '<clipPath id="{}" clipPathUnits="userSpaceOnUse"><path d="{}" transform="scale(100)"/></clipPath>'.format(cid, d)


CLIPS = "\n    ".join([
    _clip("clip-big-sparkle", "M 7.05,5.6 L 12.6,11.1 L 7.05,16.4 L 1.6,11.1 Z"),   # particles' big sparkle
    _clip("clip-sound-inner", "M 0,0 H 11.6 V 16 H 0 Z"),                           # sound without its outer wave
    _clip("clip-node", "M 3,5.2 H 13 V 14 H 3 Z"),                                   # node_top without its top bar
    _clip("clip-tall", "M 4.625,2 H 11.375 V 14 H 4.625 Z"),                         # a 9:16 window
])


def wand_a() -> str:
    """Variant A, 'sparkle wand': shaderfx's stick made dark with a white tip, particles' three sparkles."""
    t = "translate(0.6,0.4)"
    return "\n".join([
        bfa("shaderfx", only=SHADERFX_STICK, recolour=STICK_RECOLOUR, transform=t, label="stick (shaderfx)"),
        stick_lit(t),
        bfa("particles", transform="translate(6.9,-0.2) scale(0.58)", label="sparkles (particles)"),
    ])


def wand_b() -> str:
    """Variant B, 'bold': shaderfx's stick 1.6x thicker, one big particles sparkle at the tip."""
    t = "translate(0.4,0.4) " + _across(1.6, 5.2, 10.8)
    return "\n".join([
        bfa("shaderfx", only=SHADERFX_STICK, recolour=STICK_RECOLOUR, transform=t, label="stick (shaderfx, thicker)"),
        stick_lit(t),
        sparkle(11.0, 4.8, 4.2, "sparkle (particles)"),
    ])


def wand_c() -> str:
    """Variant C, 'Bforartists wand': shaderfx itself, stick and sparkles, only the stick recoloured."""
    return bfa("shaderfx", recolour=STICK_RECOLOUR, label="shaderfx (stick recoloured)") + "\n" + stick_lit("")


WAND_VARIANTS = {"wand_a": wand_a, "wand_b": wand_b, "wand_c": wand_c}
BADGE_VARIANT = "wand_b"
BADGE_TRANSFORM = "translate(7.2,7.2) scale(0.55)"   # the bottom-right 8.8 x 8.8 units


def badge() -> tuple[str, str]:
    """(the badge, its knock-out silhouette for the base's mask)."""
    art = WAND_VARIANTS[BADGE_VARIANT]()
    body = group("wand badge ({})".format(BADGE_VARIANT), art, ' transform="{}"'.format(BADGE_TRANSFORM))
    # knock-out: the badge art in black, grown by a stroke (1.4 badge units = 0.77 icon units; the source paths carry their own x100 matrix, so their local unit is a grid unit)
    knock = re.sub(r'fill="#[0-9a-fA-F]{6}"', 'fill="#000" stroke="#000" stroke-width="1.4" '
                   'stroke-linejoin="round"', art)
    knock = re.sub(r'inkscape:label="[^"]*"', "", knock)
    return body, '<g transform="{}">{}</g>'.format(BADGE_TRANSFORM, knock)


# -- the base glyphs -----------------------------------------------------------------------

def waveform(x0: float, x1: float, cy: float, heights, colour: str) -> str:
    """Bars of a waveform, centred on *cy* (ours)."""
    step = (x1 - x0) / len(heights)
    bars = []
    for i, h in enumerate(heights):
        x, w = x0 + i * step + 0.15 * step, step * 0.7
        bars.append("M {:.3g},{:.3g} h {:.3g} v {:.3g} h {:.3g} Z".format(x, cy - h / 2, w, h, -w))
    return '<path inkscape:label="waveform (ours)" fill="{}" d="{}"/>'.format(colour, " ".join(bars))


def strip_block(x0: float, x1: float, y0: float, y1: float, label: str) -> str:
    """A Sequencer strip drawn as seq_strip is: dark-red outline, lit top, shaded lower third (ours)."""
    shade_y = y1 - 1 - (y1 - y0 - 2) / 3
    return ('<path inkscape:label="{l} outline" fill="{o}" d="M {a},{b} H {c} V {d} H {a} Z"/>'
            '<path inkscape:label="{l}" fill="{lit}" d="M {a1},{b1} H {c1} V {d1} H {a1} Z"/>'
            '<path inkscape:label="{l} shade" fill="{sh}" d="M {a1},{sy} H {c1} V {d1} H {a1} Z"/>').format(
                l=label, o=SEQ_DARK, lit=SEQ_LIT, sh=SEQ, a=x0, b=y0, c=x1, d=y1,
                a1=x0 + 1, b1=y0 + 1, c1=x1 - 1, d1=y1 - 1, sy=round(shade_y, 2))


def remove_silences() -> str:
    # Two sound strips built like seq_strip, their quiet middle gone, and seq_remove_gaps' green
    # join between them: the gap closed.
    return "\n".join([
        strip_block(0, 7.5, 2, 12, "left strip"),
        strip_block(8.5, 16, 2, 12, "right strip"),
        waveform(1.2, 6.4, 6.6, (3, 7, 4.5, 6), SEQ_DARK),
        waveform(9.6, 14.8, 6.6, (6, 4, 7, 3), SEQ_DARK),
        bfa("seq_remove_gaps", only=("path821",), transform="translate(0.5,0)", label="join (seq_remove_gaps)"),
    ])


def captions() -> str:
    # image: the picture and its frame; a subtitle band across its foot (ours): white text on black.
    return "\n".join([
        bfa("image", transform="translate(0,-1)", label="picture (image)"),
        '<path inkscape:label="subtitle band (ours)" fill="{}" d="M 1,9 h 14 v 5 h -14 Z"/>'.format(BLACK),
        '<path inkscape:label="subtitle lines (ours)" fill="{}" d="M 2.5,10 h 8.5 v 1.25 h -8.5 Z M 2.5,12 h 5.5 v 1 h -5.5 Z"/>'.format(WHITE),
    ])


def transcribe() -> str:
    # sound (speaker and its two inner waves) on the left, file_text (a page of text) upper right.
    return "\n".join([
        bfa("file_text", transform="translate(9.4,0) scale(0.47)", label="page (file_text)"),
        bfa("sound", clip="clip-sound-inner", transform="translate(-0.1,2.2) scale(0.8)", label="speaker (sound)"),
    ])


def translate_() -> str:
    # small_caps' "A" on a white card, and a card behind it with a simple ideogram drawn by us
    # (four bars, no font glyph copied): one text, two languages.  Cards built as file_text's page.
    back = ('<path inkscape:label="back card outline (ours)" fill="{o}" d="M 6,0 h 10 v 10 h -10 Z"/>'
            '<path inkscape:label="back card (ours)" fill="{w}" d="M 7,1 h 8 v 8 h -8 Z"/>').format(
                o=OUTLINE, w=WHITE_SHADE)
    glyph = ('<path inkscape:label="ideogram (ours)" fill="{o}" d="M 10.5,1.7 h 1.1 v 1.2 h -1.1 Z '
             'M 8.2,3.3 h 5.7 v 1 h -5.7 Z '
             'M 8.6,4.3 L 9.8,4.3 L 14.1,8.4 L 12.8,8.4 Z M 13.5,4.3 L 12.3,4.3 L 8.0,8.4 L 9.3,8.4 Z"/>'
             ).format(o=OUTLINE)
    front = ('<path inkscape:label="front card outline (ours)" fill="{o}" d="M 0,5 h 10 v 11 h -10 Z"/>'
             '<path inkscape:label="front card (ours)" fill="{w}" d="M 1,6 h 8 v 9 h -8 Z"/>').format(
                 o=OUTLINE, w=WHITE)
    letter = bfa("small_caps", recolour={"#fefefe": OUTLINE}, transform="translate(0.8,6.1) scale(0.52)",
                 label="A (small_caps)")
    return "\n".join([back, glyph, front, letter])


def vertical() -> str:
    # image's picture seen through a 9:16 window, in a Sequencer-orange frame (seq_strip's colours);
    # the cropped-away sides of the wide picture stay as dim wings (ours).
    wings = ('<path inkscape:label="wide frame outline (ours)" fill="{o}" d="M 0,3.5 H 16 V 12.5 H 0 Z"/>'
             '<path inkscape:label="wide frame, cropped away (ours)" fill="#656565" d="M 1,4.5 H 15 V 11.5 H 1 Z"/>'
             ).format(o=OUTLINE)
    frame = ('<path inkscape:label="tall frame outline" fill="{o}" d="M 2.625,0 H 13.375 V 16 H 2.625 Z"/>'
             '<path inkscape:label="tall frame" fill="{lit}" d="M 3.625,1 H 12.375 V 15 H 3.625 Z"/>'
             '<path inkscape:label="tall frame shade" fill="{sh}" d="M 3.625,14 H 12.375 V 15 H 3.625 Z"/>').format(
                 o=SEQ_DARK, lit=SEQ_LIT, sh=SEQ)
    pic = bfa("image", skip=("path833",), clip="clip-tall", transform="translate(-1.5,0)", label="picture (image)")
    return "\n".join([wings, frame, '<g transform="translate(1.5,0)">' + pic + "</g>"])


def explain() -> str:
    # question: the blue disc and "?"; a small speech-bubble tail (ours, same outline and fill).
    tail = ('<path inkscape:label="bubble tail outline (ours)" fill="{d}" d="M 1.2,9.6 L 0.2,15.6 L 6,13.2 Z"/>'
            '<path inkscape:label="bubble tail (ours)" fill="{l}" d="M 2.2,10.8 L 1.7,13.9 L 4.6,12.6 Z"/>').format(
                d=BLACK, l=HELP)
    return "\n".join([tail, bfa("question", transform="translate(1.2,-0.2) scale(0.88)", label="disc + ? (question)")])


def tidy_nodes() -> str:
    # node_top's node (outline, header, body, socket) three times, tidied to a grid against a
    # guide bar like node_side's (ours); links in nodetree's link blue.
    def node(x, y):
        return bfa("node_top", clip="clip-node", transform="translate({:g},{:g}) scale(0.6)".format(
            x - 3.5 * 0.6, y - 5.5 * 0.6), label="node (node_top)")
    link = ('<path inkscape:label="links (ours, nodetree blue)" fill="none" stroke="{}" stroke-width="1" '
            'd="M 6.9,3 H 9.5 M 6.9,11 H 8.2 V 3"/>').format(NODE_LINK)
    guide = '<path inkscape:label="guide bar (ours, as in node_side)" fill="{}" d="M 0,0 h 1 v 16 h -1 Z"/>'.format(WHITE)
    return "\n".join([guide, link, node(1.5, 0.5), node(1.5, 8.5), node(9.5, 0.5)])


BASES = {
    "remove_silences": remove_silences,
    "captions": captions,
    "transcribe": transcribe,
    "translate": translate_,
    "vertical": vertical,
    "explain": explain,
    "tidy_nodes": tidy_nodes,
}

SOURCES = {
    "wand_a": ("shaderfx", "particles"),
    "wand_b": ("shaderfx", "particles"),
    "wand_c": ("shaderfx",),
    "remove_silences": ("seq_strip", "seq_remove_gaps"),
    "captions": ("image",),
    "transcribe": ("file_text", "sound"),
    "translate": ("small_caps",),
    "vertical": ("image", "seq_strip"),
    "explain": ("question",),
    "tidy_nodes": ("node_top", "nodetree"),
}


def document(name: str, body: str, defs: str = "") -> str:
    srcs = SOURCES.get(name, ()) + (SOURCES[BADGE_VARIANT] if name in BASES else ())
    return """<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="32" height="32" viewBox="0 0 32 32">
  <!-- Coworker wand icon "{name}" (#563), composed by _misc/compose_wand_v2.py from Bforartists
       icon SVGs ({srcs}), GPL-3.0-or-later, used with the Bforartists icon maintainer's
       permission; see SOURCES.md.  Drawn on Bforartists' 16-unit grid (scale(2) below).
       Colours #666666 and #999999 are the wand stick's dark-theme tokens; build_icons.py
       --set wand_v2 swaps them for the light theme. -->
  <defs>
    {clips}
    {defs}
  </defs>
  <g inkscape:label="16-unit grid" transform="scale(2)">
{body}
  </g>
</svg>
""".format(name=name, srcs=", ".join(s + ".svg" for s in dict.fromkeys(srcs)), clips=CLIPS, defs=defs, body=body)


def compose(name: str) -> str:
    if name == "wand":                 # the generic wand: the chosen variant on its own
        return document(BADGE_VARIANT, WAND_VARIANTS[BADGE_VARIANT]()).replace(
            'icon "{}"'.format(BADGE_VARIANT), 'icon "wand" (variant {})'.format(BADGE_VARIANT))
    if name in WAND_VARIANTS:
        return document(name, WAND_VARIANTS[name]())
    art, knock = badge()
    mask = ('<mask id="badge-cut" maskUnits="userSpaceOnUse" x="0" y="0" width="16" height="16">'
            '<rect width="16" height="16" fill="#fff"/>{}</mask>').format(knock)
    base = group("base", BASES[name](), ' mask="url(#badge-cut)"')
    return document(name, base + "\n" + art, defs=mask)


def main(argv: list[str]) -> int:
    names = argv or list(WAND_VARIANTS) + ["wand"] + list(BASES)
    for name in names:
        svg = compose(name)
        with open(os.path.join(OUT_DIR, name + ".svg"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(svg)
        print("composed", name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
