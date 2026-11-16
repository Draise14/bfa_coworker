# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Compose Coworker's operator icons (#563) from Bforartists' own icon SVGs.

The pieces come from copies of Bforartists source icons in
``addon/bfa_coworker/icons/wand/bfa_src/`` (GPL-3.0-or-later, used with the
Bforartists icon maintainer's permission; see ``icons/wand/SOURCES.md``).
Each recipe below takes paths from those files (moved, scaled, recoloured or
clipped) and adds the few parts no source icon has, drawn with the same rules
(16-unit grid, flat fills, a light/shade step, a dark outline one unit wide).
The result is one plain SVG per icon in ``icons/wand/``, which Inkscape can
open and edit::

    python _misc/compose_wand_icons.py            # all icons
    python _misc/compose_wand_icons.py captions   # only some

Once an SVG has been edited by hand, stop composing it (name the others only).
``_misc/build_icons.py`` renders the PNGs the add-on loads.

The wand badge (plan 4m section 3.3) marks the tools that need Coworker's AI: an
engine or a language / vision model call does the work.  Tools that run on their
own, even if they come with Coworker, have no badge (``BADGED``).
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT_DIR = os.path.join(_REPO, "addon", "bfa_coworker", "icons", "wand")
SRC_DIR = os.path.join(OUT_DIR, "bfa_src")
_SVG = "{http://www.w3.org/2000/svg}"

# -- palette (Bforartists' own icon colours; style guide: plan 4m section 3.3) -------------
OUTLINE = "#323232"         # neutral outline
BLACK = "#000000"
WHITE = "#fefefe"
WHITE_SHADE = "#cbcbcb"
# The wand's stick: one colour for every theme (a mid grey with a lit edge and a black
# outline reads on light grey, dark grey and the dark toolbar buttons of a light theme).
STICK = "#656565"
STICK_LIT = "#989898"
# The magic: the purple of Bforartists' wizard icon (the agent's icon in the chat), on the
# palette steps.  Rim (the outline step), fill, core.
PURPLES = {
    "wizard": ("#653298", "#9865cb", "#fefefe"),
    "vivid": ("#320065", "#9832fe", "#cb98fe"),
    "lavender": ("#6532cb", "#cb98fe", "#fefefe"),
}
PURPLE = "wizard"
# Sequencer / video (seq_strip, seq_remove_gaps)
SEQ_LIT, SEQ, SEQ_DARK = "#fecb32", "#fe9832", "#320000"
# Help (question)
HELP_LIT, HELP, HELP_DARK = "#98cbfe", "#6598fe", "#003265"
# Nodes (node_top, nodetree)
NODE_LINK = "#3265fe"


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
                    name, pid or "-", fill, extra, tr, " ".join(el.get("d").split())))
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


def _clip(cid: str, d: str) -> str:
    """A clip for a bfa() group, given in 16-unit coordinates (bfa() scales its group by 1/100)."""
    return '<clipPath id="{}" clipPathUnits="userSpaceOnUse"><path d="{}" transform="scale(100)"/></clipPath>'.format(cid, d)


CLIPS = {
    "clip-big-sparkle": "M 7.05,5.6 L 12.6,11.1 L 7.05,16.4 L 1.6,11.1 Z",   # particles' big sparkle
    "clip-node": "M 3,5.2 H 13 V 14 H 3 Z",                                  # node_top without its top bar
    "clip-tall": "M 4.625,2 H 11.375 V 14 H 4.625 Z",                        # a 9:16 window
}


# -- the wand ------------------------------------------------------------------------------

def _across(k: float, cx: float, cy: float) -> str:
    """Scale by *k* across a 45-degree stick through (cx, cy), keeping its length."""
    return "translate({0:g},{1:g}) rotate(-45) scale(1,{2:g}) rotate(45) translate({3:g},{4:g})".format(
        cx, cy, k, -cx, -cy)


SHADERFX_STICK = ("path819", "path821", "path827", "path829")


def wand() -> str:
    """The mark: shaderfx's stick 1.6x thicker, grey with a white tip, one particles sparkle in purple."""
    t = "translate(0.4,0.4) " + _across(1.6, 5.2, 10.8)
    rim, fill, core = PURPLES[PURPLE]
    return "\n".join([
        bfa("shaderfx", only=SHADERFX_STICK, recolour={"#007ffe": STICK, "#a9d8fe": WHITE, "#fefefe": WHITE},
            transform=t, label="stick (shaderfx, thicker)"),
        '<path inkscape:label="lit edge (ours)" fill="{}" transform="{}" '
        'd="M 1.75,13.5 L 6.2,9.05 L 6.5,9.35 L 2.05,13.8 Z"/>'.format(STICK_LIT, t),
        # particles' big sparkle (centre 7.05, 11.1; half height 5), clipped out of the three
        bfa("particles", clip="clip-big-sparkle", label="sparkle (particles)",
            recolour={"#fecb32": rim, "#fefe00": fill, "#fefefe": core},
            transform="translate(11,4.8) scale(0.84) translate(-7.05,-11.1)"),
    ])


BADGE_TRANSFORM = "translate(7.2,7.2) scale(0.55)"   # the bottom-right 8.8 x 8.8 units


def badge() -> tuple[str, str]:
    """(the badge, its knock-out silhouette for the base's mask)."""
    art = wand()
    body = group("wand badge", art, ' transform="{}"'.format(BADGE_TRANSFORM))
    # knock-out: the badge art in black, grown by a stroke (1.4 badge units = 0.77 icon units;
    # the source paths carry their own x100 matrix, so their local unit is a grid unit)
    knock = re.sub(r'fill="#[0-9a-fA-F]{6}"', 'fill="#000" stroke="#000" stroke-width="1.4" '
                   'stroke-linejoin="round"', art)
    knock = re.sub(r'inkscape:label="[^"]*"', "", knock)
    return body, '<g transform="{}">{}</g>'.format(BADGE_TRANSFORM, knock)


# -- the base glyphs -----------------------------------------------------------------------

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
    # join between them: the gap closed.  One coarse sound pulse per strip (ours).
    pulse = ('<path inkscape:label="sound pulses (ours)" fill="{}" '
             'd="M 2.5,6 h 1.5 v 4 h -1.5 Z M 4.5,4.5 h 1.5 v 7 h -1.5 Z '
             'M 10,4.5 h 1.5 v 7 h -1.5 Z M 12,6 h 1.5 v 4 h -1.5 Z"/>').format(SEQ_DARK)
    return "\n".join([
        strip_block(0, 7.5, 2, 14, "left strip"),
        strip_block(8.5, 16, 2, 14, "right strip"),
        pulse,
        bfa("seq_remove_gaps", only=("path821",), transform="translate(0.5,0)", label="join (seq_remove_gaps)"),
    ])


def captions() -> str:
    # image: the picture and its frame; a subtitle band across its foot (ours): white text on black.
    return "\n".join([
        bfa("image", label="picture (image)"),
        '<path inkscape:label="subtitle band (ours)" fill="{}" d="M 1,9.5 h 14 v 5 h -14 Z"/>'.format(BLACK),
        '<path inkscape:label="subtitle lines (ours)" fill="{}" d="M 2.5,10.5 h 11 v 1.4 h -11 Z '
        'M 4.5,12.6 h 7 v 1.2 h -7 Z"/>'.format(WHITE),
    ])


def transcribe() -> str:
    # play_sound (speaker with its green waves; outline made black for contrast) on the left,
    # file_text (a page of text, outline black) on the right: sound becomes text.
    return "\n".join([
        bfa("file_text", recolour={"#323232": BLACK}, transform="translate(9.5,3.6) scale(0.48)",
            label="page (file_text)"),
        bfa("play_sound", recolour={"#323232": BLACK},
            transform="translate(0,3.2) scale(0.6)", label="speaker and waves (play_sound)"),
    ])


def translate_() -> str:
    # small_caps' "A" on a white card, and a card behind it with a simple ideogram drawn by us
    # (four bars, no font glyph copied): one text, two languages.  Cards built as file_text's page.
    back = ('<path inkscape:label="back card outline (ours)" fill="{o}" d="M 6,0 h 10 v 9 h -10 Z"/>'
            '<path inkscape:label="back card (ours)" fill="{w}" d="M 7,1 h 8 v 7 h -8 Z"/>').format(
                o=OUTLINE, w=WHITE_SHADE)
    # (kept above y 6.5, clear of the badge's knock-out)
    glyph = ('<path inkscape:label="ideogram (ours)" fill="{o}" d="M 10.5,1.4 h 1.1 v 1 h -1.1 Z '
             'M 8.2,2.7 h 5.7 v 0.9 h -5.7 Z '
             'M 9,3.6 L 10.1,3.6 L 13,6.4 L 11.9,6.4 Z M 13.1,3.6 L 12,3.6 L 9.1,6.4 L 10.2,6.4 Z"/>'
             ).format(o=OUTLINE)
    front = ('<path inkscape:label="front card outline (ours)" fill="{o}" d="M 0,5 h 10 v 11 h -10 Z"/>'
             '<path inkscape:label="front card (ours)" fill="{w}" d="M 1,6 h 8 v 9 h -8 Z"/>').format(
                 o=OUTLINE, w=WHITE)
    letter = bfa("small_caps", recolour={"#fefefe": OUTLINE}, transform="translate(0.8,6.1) scale(0.52)",
                 label="A (small_caps)")
    return "\n".join([back, glyph, front, letter])


def vertical() -> str:
    # image's picture seen through a 9:16 window, in a Sequencer-orange frame (seq_strip's colours);
    # behind it the wide frame it was cut from: an empty screen with image's white border (ours).
    wide = ('<path inkscape:label="wide frame outline (ours)" fill="{b}" d="M 0,3 H 16 V 13 H 0 Z"/>'
            '<path inkscape:label="wide frame border (image white)" fill="{w}" d="M 0.6,3.6 H 15.4 V 12.4 H 0.6 Z"/>'
            '<path inkscape:label="wide frame inside (ours)" fill="{o}" d="M 1.6,4.6 H 14.4 V 11.4 H 1.6 Z"/>'
            ).format(b=BLACK, w=WHITE, o=OUTLINE)
    frame = ('<path inkscape:label="tall frame outline" fill="{o}" d="M 2.625,0 H 13.375 V 16 H 2.625 Z"/>'
             '<path inkscape:label="tall frame" fill="{lit}" d="M 3.625,1 H 12.375 V 15 H 3.625 Z"/>'
             '<path inkscape:label="tall frame shade" fill="{sh}" d="M 3.625,14 H 12.375 V 15 H 3.625 Z"/>').format(
                 o=SEQ_DARK, lit=SEQ_LIT, sh=SEQ)
    pic = bfa("image", skip=("path833",), clip="clip-tall", transform="translate(-1.5,0)", label="picture (image)")
    return "\n".join([wide, frame, '<g transform="translate(1.5,0)">' + pic + "</g>"])


def explain() -> str:
    # question: the blue disc and "?", moved up-left out of the badge's corner; a small
    # speech-bubble tail (ours, same outline and fill).
    tail = ('<path inkscape:label="bubble tail outline (ours)" fill="{d}" d="M 1,9 L 0,15.4 L 5.6,12.4 Z"/>'
            '<path inkscape:label="bubble tail (ours)" fill="{l}" d="M 2,10.2 L 1.5,13.6 L 4.3,12 Z"/>').format(
                d=BLACK, l=HELP)
    return "\n".join([tail, bfa("question", transform="translate(0.4,-0.2) scale(0.84)", label="disc + ? (question)")])


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
# The wand badge marks the tools that need Coworker's AI (style guide, plan 4m section 3.3):
#   translate   bfacw.translate asks the active engine for the translation
#   explain     bfacw.explain / explain_spot ask Coworker in the chat
#   tidy_nodes  Tidy & Explain Tree (plan 4m section 4.2): the engine groups and labels the nodes
# No badge: remove_silences (model "none"), vertical (Centre Crop / Blur Fill run on their own;
# Follow Subject is an option), captions and transcribe (the local speech model only, no engine).
BADGED = ("translate", "explain", "tidy_nodes")

SOURCES = {
    "wand": ("shaderfx", "particles"),
    "remove_silences": ("seq_strip", "seq_remove_gaps"),
    "captions": ("image",),
    "transcribe": ("play_sound", "file_text"),
    "translate": ("small_caps", "file_text"),
    "vertical": ("image", "seq_strip"),
    "explain": ("question",),
    "tidy_nodes": ("node_top", "nodetree"),
}


def document(name: str, body: str, defs: str = "") -> str:
    srcs = SOURCES[name] + (SOURCES["wand"] if name in BADGED else ())
    used = set(re.findall(r'clip-path="url\(#([a-z-]+)\)"', body))
    clips = "\n    ".join(_clip(c, d) for c, d in CLIPS.items() if c in used)
    return """<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="32" height="32" viewBox="0 0 32 32">
  <!-- Coworker icon "{name}" (#563), composed by _misc/compose_wand_icons.py from Bforartists
       icon SVGs ({srcs}), GPL-3.0-or-later, used with the Bforartists icon maintainer's
       permission; see SOURCES.md.  Drawn on Bforartists' 16-unit grid (scale(2) below). -->
  <defs>
    {clips}
    {defs}
  </defs>
  <g inkscape:label="16-unit grid" transform="scale(2)">
{body}
  </g>
</svg>
""".format(name=name, srcs=", ".join(s + ".svg" for s in dict.fromkeys(srcs)), clips=clips, defs=defs, body=body)


def compose(name: str) -> str:
    if name == "wand":
        return document(name, wand())
    if name not in BADGED:
        return document(name, group("base", BASES[name]()))
    art, knock = badge()
    mask = ('<mask id="badge-cut" maskUnits="userSpaceOnUse" x="0" y="0" width="16" height="16">'
            '<rect width="16" height="16" fill="#fff"/>{}</mask>').format(knock)
    base = group("base", BASES[name](), ' mask="url(#badge-cut)"')
    return document(name, base + "\n" + art, defs=mask)


def main(argv: list[str], out_dir: str = OUT_DIR) -> int:
    names = argv or ["wand"] + list(BASES)
    for name in names:
        with open(os.path.join(out_dir, name + ".svg"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(compose(name))
        print("composed", name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
