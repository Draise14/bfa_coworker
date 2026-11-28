# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Make Vertical (9:16) and Reframe in the Sequencer (Video 3, #333).

*Make Vertical* sets the output to 9:16 -- in a copy of the scene by default, so the
16:9 edit stays -- and frames every visible movie, image and scene strip: Centre Crop
with the strip's own crop and transform (no compositor, so preview playback keeps its
speed, #632), or *Blur Fill* (the whole shot over a blurred, dimmed copy of itself)
with a **Reframe** compositor modifier, which Blur Fill needs.  The framing stays
editable: for Blur Fill it is the modifier's inputs (Properties editor > Strip
Modifiers tab, or Strip > Reframe), for Centre Crop the strip's Transform and Crop
(or Strip > Reframe), one value set per shot.  Scenes made before #632 have a modifier
on every strip: both forms are read, and a Centre Crop modifier that is not keyed is
turned into the plain crop the next time Make Vertical runs.
Coworker captions in the edit are laid out again for the new frame (#497,
``captions_ops.relayout_captions``); text strips get no Reframe modifier.

The node group ``Coworker Reframe v1`` is built once and shared; each modifier keeps
its own values (``mod.properties.inputs.<socket identifier>.value``).  Blender 5.2.4
crashes when a ``mod.properties.inputs`` handle is kept across an interface change,
so: the interface is finished before any modifier uses the group, it is never changed
afterwards (a new layout gets a new name: ``... v2``), and ``mod.properties.inputs``
is fetched again on every access, never stored.

The maths live in :mod:`reframe_math` (bpy-free, tested); Follow subject's request
in :mod:`reframe_follow`.
"""

from __future__ import annotations

__all__ = (
    "GROUP_NAME",
    "plain_framing_of",
    "plain_keyed",
    "plain_marker",
    "ReframeError",
    "ensure_group",
    "find_modifier",
    "get_inputs",
    "copy_scene",
    "make_vertical",
    "register",
    "set_inputs",
    "unregister",
)

import os
from typing import Any, Iterable

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, EnumProperty, FloatProperty  # pylint: disable=import-error
from bpy.types import Menu, Operator  # pylint: disable=import-error

from . import reframe_follow
from . import reframe_math as rm
from . import reframe_plain as rp
from .shared import icon
from .wand import reframe_apply as _layouts
from .wand import scene as _seq
from .wand.spec import agent_line, model_line

GROUP_NAME = "Coworker Reframe v1"
GROUP_TAG = "coworker_reframe"          # custom property on the group: its layout version
GROUP_VERSION = 1
MOD_NAME = "Reframe"
VERTICAL_TAG = "coworker_vertical_of"   # on a vertical copy: the scene it was made from
STRIP_TYPES = {"MOVIE", "IMAGE", "SCENE"}
# Blur Fill blurs a quarter-size copy (much less work for the same look), then scales it back up.
BLUR_DOWNSCALE = 0.25

# Drop-down entries: (id, label, tooltip, Blender icon).  Every entry has an icon (wand rules, #610).
RESOLUTIONS = (
    ("1080x1920", "1080 x 1920 (Full HD)", "9:16 at 1080 x 1920, the usual size for short-form video",
     'SEQ_PREVIEW'),
    ("720x1280", "720 x 1280 (HD)", "9:16 at 720 x 1280: smaller files, faster renders", 'SEQ_PREVIEW'),
    ("2160x3840", "2160 x 3840 (4K)", "9:16 at 2160 x 3840", 'SEQ_PREVIEW'),
)
MODES = (
    ("CROP", "Centre Crop", "Fill the 9:16 frame with the middle of each shot, with the strip's own crop and transform (pan it afterwards with Strip > Reframe)",
     'FULLSCREEN_ENTER'),
    ("BLUR_FILL", "Blur Fill", "Show each whole shot over a blurred, dimmed copy of itself", 'MATFLUID'),
)


def _enum_items(entries: Iterable[tuple]) -> list[tuple]:
    """Drop-down items ``(id, label, tooltip, icon, number)`` from ``(id, label, tooltip, icon name)`` rows."""
    return [(ident, label, tip, icon(name), number) for number, (ident, label, tip, name) in enumerate(entries)]

# The group's inputs: (name, socket type, default, min, max, description, in the Frame panel).
INPUTS = (
    ("Offset X", "NodeSocketFloat", 0.0, -1.0, 1.0,
     "Pan the frame across the picture: -1 shows its left edge, 1 its right edge, 0 is centred. "
     "Inside -1..1 the picture always covers the frame", False),
    ("Offset Y", "NodeSocketFloat", 0.0, -1.0, 1.0,
     "Pan the frame up and down: -1 shows the picture's bottom edge, 1 its top edge "
     "(no room to pan until you zoom in, for a 16:9 shot)", False),
    ("Zoom", "NodeSocketFloat", 1.0, 0.1, 10.0,
     "1 = the picture just covers the frame (Blur Fill: the whole picture just fits); higher zooms in", False),
    ("Blur Fill", "NodeSocketBool", False, None, None,
     "Show the whole picture over a blurred, dimmed copy of itself instead of cropping it", False),
    ("Blur Amount", "NodeSocketFloat", 40.0, 0.0, 400.0,
     "How soft the Blur Fill background is, in pixels of the picture", False),
    ("Background Dim", "NodeSocketFloat", 0.3, 0.0, 1.0,
     "How much darker the Blur Fill background is than the shot (0 = as bright, 1 = black)", False),
    ("Frame Width", "NodeSocketFloat", 0.5625, 0.001, 1000.0,
     "The output frame's width relative to this strip's picture (set by Make Vertical: 1080 / 1920 for a "
     "Full HD shot on a 9:16 frame). Make Vertical again after changing the resolution", True),
    ("Frame Height", "NodeSocketFloat", 1.7778, 0.001, 1000.0,
     "The output frame's height relative to this strip's picture (set by Make Vertical)", True),
)


class ReframeError(Exception):
    """A clear, user-facing reason Make Vertical / Reframe can't run."""


# -- The node group -------------------------------------------------------------------

def _input_ids(ng: Any) -> dict[str, str]:
    return {item.name: item.identifier for item in ng.interface.items_tree
            if item.item_type == 'SOCKET' and item.in_out == 'INPUT'}


def _is_ours(ng: Any) -> bool:
    try:
        return ng is not None and int(ng.get(GROUP_TAG, 0)) == GROUP_VERSION
    except (TypeError, ValueError):
        return False


def _build_interface(ng: Any) -> None:
    it = ng.interface
    it.new_socket("Image", in_out='INPUT', socket_type='NodeSocketColor')
    panel = None
    for name, kind, default, lo, hi, desc, in_panel in INPUTS:
        parent = None
        if in_panel:
            if panel is None:
                panel = it.new_panel("Frame (set by Make Vertical)", default_closed=True)
            parent = panel
        sock = it.new_socket(name, in_out='INPUT', socket_type=kind, parent=parent) if parent is not None \
            else it.new_socket(name, in_out='INPUT', socket_type=kind)
        sock.description = desc
        sock.default_value = default
        if lo is not None:
            sock.min_value, sock.max_value = lo, hi
        if name == "Background Dim":
            try:
                sock.subtype = 'FACTOR'
            except (AttributeError, TypeError):
                pass
    it.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')


def _socket(sockets: Any, name: str, kind: str | None = None) -> Any:
    for s in sockets:
        if s.name == name and (kind is None or s.type == kind) and getattr(s, "enabled", True):
            return s
    raise KeyError(name)


def _build_nodes(ng: Any) -> None:
    """Offset / zoom / Blur Fill, everything derived from the inputs (see reframe_math)."""
    nodes, links = ng.nodes, ng.links
    gi = nodes.new("NodeGroupInput")
    go = nodes.new("NodeGroupOutput")
    gi.location, go.location = (-1400, 0), (900, 0)
    g = {s.name: s for s in gi.outputs if s.name}
    x = [-1100]

    def node(kind: str, **inputs: Any) -> Any:
        n = nodes.new(kind)
        n.location = (x[0], 0)
        x[0] += 180
        for key, value in inputs.items():
            sock = n.inputs[key]
            if hasattr(value, "is_output"):
                links.new(value, sock)
            else:
                sock.default_value = value
        return n

    def math(op: str, a: Any, b: Any = 0.0) -> Any:
        n = nodes.new("ShaderNodeMath")
        n.operation = op
        n.location = (x[0], -300)
        x[0] += 90
        for sock, value in zip(n.inputs, (a, b)):
            if hasattr(value, "is_output"):
                links.new(value, sock)
            else:
                sock.default_value = value
        return n.outputs[0]

    info = node("ShaderNodeSeparateXYZ")
    image_info = node("CompositorNodeImageInfo", Image=g["Image"])
    links.new(image_info.outputs["Dimensions"], info.inputs[0])
    width, height = info.outputs["X"], info.outputs["Y"]
    fw, fh = g["Frame Width"], g["Frame Height"]
    cover = math('MAXIMUM', fw, fh)
    fit = math('MINIMUM', fw, fh)
    crop_scale = math('MULTIPLY', cover, rm.BLEED)
    # base = crop_scale + BlurFill * (fit - crop_scale)
    base = math('ADD', crop_scale, math('MULTIPLY', g["Blur Fill"], math('SUBTRACT', fit, crop_scale)))
    scale = math('MULTIPLY', base, g["Zoom"])
    # Pan range in the picture's pixels: |scale - frame| * size / 2, one pixel short so the picture's
    # soft last row never shows at -1 / 1; the picture moves against the offset.
    def pan(frame: Any, size: Any) -> Any:
        half = math('MULTIPLY', math('MULTIPLY', math('ABSOLUTE', math('SUBTRACT', scale, frame)), size), 0.5)
        return math('MAXIMUM', math('SUBTRACT', half, 1.0), 0.0)
    range_x, range_y = pan(fw, width), pan(fh, height)
    clamp_x = node("ShaderNodeClamp", Value=g["Offset X"], Min=-1.0, Max=1.0)
    clamp_y = node("ShaderNodeClamp", Value=g["Offset Y"], Min=-1.0, Max=1.0)
    fg = node("CompositorNodeTransform", Image=g["Image"],
              X=math('MULTIPLY', math('MULTIPLY', clamp_x.outputs[0], range_x), -1.0),
              Y=math('MULTIPLY', math('MULTIPLY', clamp_y.outputs[0], range_y), -1.0), Scale=scale)
    # Blur Fill background: quarter size, blurred, back up to cover (a little past the edges), dimmed.
    down = node("CompositorNodeScale", Image=g["Image"], X=BLUR_DOWNSCALE, Y=BLUR_DOWNSCALE)
    blur_px = math('MULTIPLY', g["Blur Amount"], BLUR_DOWNSCALE)
    size = node("ShaderNodeCombineXYZ", X=blur_px, Y=blur_px)
    blur = node("CompositorNodeBlur", Image=down.outputs[0])
    links.new(size.outputs[0], blur.inputs["Size"])
    up = node("CompositorNodeTransform", Image=blur.outputs[0],
              Scale=math('MULTIPLY', cover, rm.BACKGROUND_OVERSCAN / BLUR_DOWNSCALE))
    dim = nodes.new("ShaderNodeMix")
    dim.data_type = 'RGBA'
    dim.location = (x[0], 300)
    x[0] += 180
    links.new(g["Background Dim"], _socket(dim.inputs, "Factor", 'VALUE'))
    links.new(up.outputs[0], _socket(dim.inputs, "A", 'RGBA'))
    _socket(dim.inputs, "B", 'RGBA').default_value = (0.0, 0.0, 0.0, 1.0)
    over = node("CompositorNodeAlphaOver", Background=_socket(dim.outputs, "Result", 'RGBA'),
                Foreground=fg.outputs[0])
    switch = node("CompositorNodeSwitch", Switch=g["Blur Fill"], Off=fg.outputs[0], On=over.outputs[0])
    links.new(switch.outputs[0], go.inputs[0])


def ensure_group() -> Any:
    """The shared Reframe group, built once (interface first, then nodes)."""
    ng = bpy.data.node_groups.get(GROUP_NAME)
    if ng is not None and _is_ours(ng) and ng.bl_idname == "CompositorNodeTree":
        return ng
    for ng in bpy.data.node_groups:         # renamed, or the name was taken by another group
        if ng.bl_idname == "CompositorNodeTree" and _is_ours(ng) and ng.library is None:
            return ng
    ng = bpy.data.node_groups.new(GROUP_NAME, "CompositorNodeTree")
    _build_interface(ng)
    _build_nodes(ng)
    ng[GROUP_TAG] = GROUP_VERSION
    ng.use_fake_user = False
    return ng


# -- Per-modifier values (fetch mod.properties.inputs every time) ------------------------

def find_modifier(strip: Any) -> Any:
    for mod in getattr(strip, "modifiers", ()):
        if mod.type == 'COMPOSITOR' and _is_ours(getattr(mod, "node_group", None)):
            return mod
    return None


def get_inputs(mod: Any) -> dict[str, Any]:
    out = {}
    for name, ident in _input_ids(mod.node_group).items():
        if name == "Image":
            continue
        item = getattr(mod.properties.inputs, ident, None)
        if item is not None:
            out[name] = item.value
    return out


def set_inputs(mod: Any, values: dict[str, Any]) -> None:
    ids = _input_ids(mod.node_group)
    for name, value in values.items():
        item = getattr(mod.properties.inputs, ids[name])
        item.value = bool(value) if name == "Blur Fill" else float(value)


def _data_path(mod: Any, name: str) -> str:
    item = getattr(mod.properties.inputs, _input_ids(mod.node_group)[name])
    return item.path_from_id("value")


def _channelbags(scene: Any) -> list[Any]:
    ad = getattr(scene, "animation_data", None)
    act = ad.action if ad else None
    if act is None:
        return []
    bags = []
    for layer in getattr(act, "layers", ()):
        for st in layer.strips:
            bags.extend(getattr(st, "channelbags", ()))
    return bags


def clear_keys(scene: Any, mod: Any, names: Iterable[str] | None = None) -> int:
    """Remove the keyframes of *mod*'s inputs (all, or *names*); returns how many curves went."""
    paths = {_data_path(mod, n) for n in (names or [n for n in _input_ids(mod.node_group) if n != "Image"])}
    gone = 0
    for bag in _channelbags(scene):
        for fc in list(bag.fcurves):
            if fc.data_path in paths:
                bag.fcurves.remove(fc)
                gone += 1
    return gone


def fcurve_for(scene: Any, mod: Any, name: str) -> Any:
    path = _data_path(mod, name)
    for bag in _channelbags(scene):
        for fc in bag.fcurves:
            if fc.data_path == path:
                return fc
    return None


# -- Strips and sizes ----------------------------------------------------------------------

def sequencer_scene(context: Any) -> Any:
    """The scene the Sequencer shows (``wand.scene``, #504), else the context scene (tools, timers)."""
    return _seq.sequencer_scene(context) or getattr(context, "scene", None)


def source_size(strip: Any) -> tuple[int, int]:
    """The strip's picture size in pixels (what the modifier receives)."""
    if strip.type in {"MOVIE", "IMAGE"}:
        elems = getattr(strip, "elements", None)
        if elems and elems[0].orig_width and elems[0].orig_height:
            return int(elems[0].orig_width), int(elems[0].orig_height)
        if strip.type == "IMAGE" and elems:
            path = bpy.path.abspath(os.path.join(strip.directory, elems[0].filename))
            img = bpy.data.images.load(path, check_existing=True)
            try:
                if img.size[0] and img.size[1]:
                    return int(img.size[0]), int(img.size[1])
            finally:
                if img.users == 0:
                    bpy.data.images.remove(img)
        if strip.type == "MOVIE":
            clip = bpy.data.movieclips.load(bpy.path.abspath(strip.filepath))
            try:
                if clip.size[0] and clip.size[1]:
                    return int(clip.size[0]), int(clip.size[1])
            finally:
                bpy.data.movieclips.remove(clip)
    elif strip.type == "SCENE" and strip.scene is not None:
        r = strip.scene.render
        pct = r.resolution_percentage / 100.0
        return max(1, int(round(r.resolution_x * pct))), max(1, int(round(r.resolution_y * pct)))
    raise ReframeError("Can't tell the picture size of '{:s}'.".format(strip.name))


def canvas_size(scene: Any) -> tuple[int, int]:
    r = scene.render
    pct = r.resolution_percentage / 100.0
    return max(1, int(round(r.resolution_x * pct))), max(1, int(round(r.resolution_y * pct)))


def _visible(ed: Any, strip: Any) -> bool:
    if strip.mute or strip.type not in STRIP_TYPES:
        return False
    try:
        if strip in ed.strips.values() and ed.channels[strip.channel].mute:
            return False
    except (IndexError, KeyError, AttributeError):
        pass
    return True


def target_strips(ed: Any, names: Iterable[str] | None = None) -> tuple[list[Any], list[str]]:
    """The strips to reframe and the names asked for that aren't reframable."""
    if names:
        wanted, missing = [], []
        for n in names:
            s = ed.strips_all.get(n)
            if s is None or s.type not in STRIP_TYPES:
                missing.append(n)
            else:
                wanted.append(s)
        return wanted, missing
    return [s for s in ed.strips_all if _visible(ed, s)], []


def _reset_transform(strip: Any) -> None:
    t = strip.transform
    t.offset_x = t.offset_y = 0.0
    t.scale_x = t.scale_y = 1.0
    t.rotation = 0.0
    try:
        t.origin = (0.5, 0.5)
    except (AttributeError, TypeError):
        pass


# -- Centre Crop without the modifier (#632) -------------------------------------------------

def frame_size(scene: Any) -> tuple[int, int]:
    """The frame in whole-frame pixels, whatever Resolution % says (what strip transforms are in)."""
    return max(1, int(scene.render.resolution_x)), max(1, int(scene.render.resolution_y))


def plain_marker(strip: Any) -> Any:
    """The strip's plain-crop marker (``{"w", "h"}``: the frame it was made for), or ``None``."""
    try:
        raw = strip.get(rp.MARKER)
    except (AttributeError, TypeError):
        return None
    return raw if raw is not None and hasattr(raw, "get") else None


def _offset_path(strip: Any) -> str:
    return strip.transform.path_from_id("offset_x")


def plain_keyed(scene: Any, strip: Any) -> bool:
    """True when the strip's pan is keyed (Follow Subject)."""
    path = _offset_path(strip)
    return any(fc.data_path == path for bag in _channelbags(scene) for fc in bag.fcurves)


def _clear_plain_keys(scene: Any, strip: Any) -> int:
    path = _offset_path(strip)
    gone = 0
    for bag in _channelbags(scene):
        for fc in list(bag.fcurves):
            if fc.data_path == path:
                bag.fcurves.remove(fc)
                gone += 1
    return gone


def clear_plain(scene: Any, strip: Any) -> None:
    """Take the plain crop off *strip*: keys, marker, crop and transform back to neutral."""
    _clear_plain_keys(scene, strip)
    try:
        del strip[rp.MARKER]
    except (KeyError, TypeError, AttributeError):
        pass
    _reset_transform(strip)
    c = strip.crop
    c.min_x = c.max_x = c.min_y = c.max_y = 0


def plain_framing_of(scene: Any, strip: Any) -> dict[str, float]:
    """Offset X / Y and Zoom of a plain-crop strip, read from its crop and transform (so a picture
    dragged by hand is read as it is now), in the frame the strip was made for."""
    sw, sh = source_size(strip)
    fw, fh = rp.marker_canvas(plain_marker(strip), frame_size(scene))
    t = strip.transform
    return rp.plain_framing(sw, sh, fw, fh, float(t.scale_x), (float(t.offset_x), float(t.offset_y)))


def _apply_part(strip: Any, part: Any) -> None:
    _layouts.set_part(strip, part)


def _set_marker(scene: Any, strip: Any) -> None:
    """Say which frame size the strip's numbers are for (so a later read uses the right cover scale)."""
    fw, fh = frame_size(scene)
    strip[rp.MARKER] = {"w": fw, "h": fh}


def _offset_fcurves(scene: Any, strip: Any) -> list[Any]:
    path = _offset_path(strip)
    return [fc for bag in _channelbags(scene) for fc in bag.fcurves if fc.data_path == path]


def _key_fractions(scene: Any, strip: Any) -> list[tuple[Any, list[float]]]:
    """Each pan key of a followed plain strip as the picture fraction at the frame's middle (0..1), read
    with the strip's scale as it is now: call before the numbers change."""
    sw, _sh = source_size(strip)
    span = max(1e-9, sw * float(strip.transform.scale_x))
    return [(fc, [0.5 - float(kp.co[1]) / span for kp in fc.keyframe_points]) for fc in _offset_fcurves(scene, strip)]


def _restore_keys(scene: Any, strip: Any, saved: list[tuple[Any, list[float]]]) -> None:
    """Write the saved subject fractions back as pan keys for the strip's new scale and frame."""
    sw, sh = source_size(strip)
    fw, fh = frame_size(scene)
    f = plain_framing_of(scene, strip)
    for fc, fractions in saved:
        for kp, u in zip(fc.keyframe_points, fractions):
            part = rp.subject_part(sw, sh, fw, fh, u, f["Offset Y"], f["Zoom"])
            delta = part.offset[0] - float(kp.co[1])
            kp.co[1] = part.offset[0]
            kp.handle_left[1] += delta
            kp.handle_right[1] += delta
        fc.update()


def build_plain(scene: Any, strip: Any, framing: dict[str, float] | None = None, tracked: bool = False) -> None:
    """Give *strip* a plain Centre Crop for the scene's frame, at *framing* (default centred).
    *tracked*: a followed strip (no horizontal crop): its pan keys are kept, in the new frame's pixels."""
    sw, sh = source_size(strip)
    fw, fh = frame_size(scene)
    f = framing or {}
    saved = _key_fractions(scene, strip) if tracked else []
    part = rp.plain_part(sw, sh, fw, fh, f.get("Offset X", 0.0), f.get("Offset Y", 0.0), f.get("Zoom", 1.0),
                         tracked=tracked)
    _apply_part(strip, part)
    _set_marker(scene, strip)
    if saved:
        _restore_keys(scene, strip, saved)


def _key_free(scene: Any, mod: Any) -> bool:
    """No keyframes on the three framing inputs of *mod*."""
    return all(fcurve_for(scene, mod, n) is None for n in ("Offset X", "Offset Y", "Zoom"))


def _modifier_framing(mod: Any) -> dict[str, float]:
    vals = get_inputs(mod)
    return {k: float(vals.get(k, rm.DEFAULTS[k])) for k in ("Offset X", "Offset Y", "Zoom")}


def to_plain(scene: Any, strip: Any, from_blur: bool = False) -> bool:
    """Turn a Centre Crop modifier (not Blur Fill, nothing keyed) into the plain crop at the same framing.
    *from_blur*: a Blur Fill one too (the user asked for Centre Crop).  False when the strip has to keep
    its modifier."""
    mod = find_modifier(strip)
    if mod is None or (get_inputs(mod).get("Blur Fill") and not from_blur) or not _key_free(scene, mod):
        return False
    framing = _modifier_framing(mod)
    strip.modifiers.remove(mod)
    _reset_transform(strip)
    build_plain(scene, strip, framing)
    return True


def reframe_strip(scene: Any, strip: Any, mode: str, reset: bool = False) -> str:
    """Frame *strip* in *mode*; returns added / kept / reset / converted.

    ``CROP`` is the strip's own crop and transform (no modifier, #632); ``BLUR_FILL`` is the Reframe
    modifier.  A Centre Crop modifier from before #632 is turned into the plain crop (``converted``,
    same framing) unless it is Blur Fill or keyed."""
    if _layouts.is_split_part(strip):
        raise ReframeError("'{:s}' is part of a split: use the Reframe layouts (Strip > Reframe Layout) to change it.".format(strip.name))
    mod = find_modifier(strip)
    plain = plain_marker(strip) is not None
    if mode == "CROP":
        if mod is None:
            if plain and not reset:
                # A followed strip keeps its pan keys (rescaled to this frame), not a horizontal crop.
                build_plain(scene, strip, plain_framing_of(scene, strip), tracked=plain_keyed(scene, strip))
                return "kept"
            if plain:
                clear_plain(scene, strip)
            _reset_transform(strip)
            build_plain(scene, strip)
            return "reset" if plain else "added"
        if reset:
            clear_keys(scene, mod)
            strip.modifiers.remove(mod)
            _reset_transform(strip)
            build_plain(scene, strip)
            return "reset"
        if to_plain(scene, strip):
            return "converted"
        # Blur Fill or keyed: the modifier stays, its sizes follow the frame (as before).
        w, h = canvas_size(scene)
        sw, sh = source_size(strip)
        _action, values = rm.plan_modifier(get_inputs(mod), mode, sw, sh, w, h, False)
        set_inputs(mod, values)
        return "kept"
    ng = ensure_group()
    w, h = canvas_size(scene)
    sw, sh = source_size(strip)
    if mod is None and plain:
        clear_plain(scene, strip)
    action, values = rm.plan_modifier(get_inputs(mod) if mod else None, mode, sw, sh, w, h, reset)
    if mod is None:
        mod = strip.modifiers.new(MOD_NAME, type='COMPOSITOR')
        mod.node_group = ng
    elif action == "reset":
        clear_keys(scene, mod)
    # The framing maths assume the picture sits unscaled in the middle (the modifier
    # works in the picture's pixels, before the strip's own transform).
    _reset_transform(strip)
    set_inputs(mod, values)
    return action


def copy_scene(scene: Any, name: str, tags: dict[str, Any] | None = None) -> Any:
    """A copy of *scene* called *name* that survives a save, with its own animation action and the custom
    properties *tags* (Make Vertical's " Vertical" copy and Make Short's scene, #615, are made with it)."""
    copy = scene.copy()
    # ID.copy() hands back a copy with no user (the caller is meant to assign it), and
    # nothing counts as a scene's user -- not the window, not the workspace's Sequencer --
    # so a 0-user copy is dropped on save (#537).  Give it the extra user Blender gives
    # every scene it makes itself (bpy.data.scenes.new, Scene > New): not a fake user, so
    # deleting the copy by hand still removes it and leaves nothing behind.
    copy.use_extra_user = True
    copy.name = name
    for key, value in (tags or {}).items():
        copy[key] = value
    # scene.copy() shares the action: keyframes on the copy's modifiers would land in
    # the original's.  Give the copy its own.
    ad = copy.animation_data
    if ad is not None and ad.action is not None:
        ad.action = ad.action.copy()
    return copy


def _copy_scene(scene: Any) -> Any:
    return copy_scene(scene, scene.name + " Vertical", {VERTICAL_TAG: scene.name})


# Make Vertical frames the whole edit (every visible shot), not the selection: said in the dialog and the result (#541).
SCOPE_NOTE = "Make Vertical frames every visible shot in the edit, not only the selected ones."
# Centre Crop keeps the middle of the picture: a subject in a corner (a facecam) needs the pan (#541).
CROP_HINT = ("Centre Crop keeps the middle of each shot. If the subject is off-centre (a facecam in a corner), "
             "pan it with Strip > Reframe > Pan, or use Follow Subject.")


def make_vertical(scene: Any, mode: str = "CROP", in_copy: bool = True, resolution: str = "1080x1920",
                  reset: bool = False, names: Iterable[str] | None = None, window: Any = None) -> dict[str, Any]:
    """Make *scene*'s edit 9:16; returns what was done (see the MCP tool's Result)."""
    if mode not in rm.MODES:
        raise ReframeError("mode must be CROP or BLUR_FILL, not {!r}.".format(mode))
    if scene is None or scene.sequence_editor is None:
        raise ReframeError("This scene has no Sequencer strips to make vertical.")
    try:
        w, h = (int(v) for v in resolution.lower().split("x"))
    except ValueError:
        raise ReframeError("resolution must look like 1080x1920, not {!r}.".format(resolution)) from None
    strips, missing = target_strips(scene.sequence_editor, names)
    if missing:
        raise ReframeError("No movie, image or scene strip named {:s} in '{:s}'.".format(
            ", ".join(repr(n) for n in missing), scene.name))
    # Parts of a split (#606) have crop and transform, not the modifier: Make Vertical leaves them alone.
    left_alone = [s.name for s in strips if _layouts.is_split_part(s)]
    strips = [s for s in strips if s.name not in left_alone]
    if not strips and left_alone:
        raise ReframeError("All the strips are parts of a split: use the Reframe layouts (Strip > "
                           "Reframe Layout) to change them.")
    if not strips:
        raise ReframeError("No visible movie, image or scene strips in '{:s}'.".format(scene.name))
    names = [s.name for s in strips]
    target, copied = scene, False
    if in_copy and not scene.get(VERTICAL_TAG):
        target, copied = _copy_scene(scene), True
    old_res = (target.render.resolution_x, target.render.resolution_y)
    target.render.resolution_x, target.render.resolution_y = w, h
    ed = target.sequence_editor
    done: dict[str, list[str]] = {"added": [], "kept": [], "reset": [], "converted": []}
    for name in names:
        strip = ed.strips_all.get(name)
        if strip is not None:
            done[reframe_strip(target, strip, mode, reset)].append(name)
    # Coworker captions laid out again for the new frame (#497); text strips get no Reframe.
    from .captions_ops import relayout_captions
    captions = relayout_captions(target, old_res)
    if copied and window is not None:
        ws = getattr(window, "workspace", None)
        if ws is not None:
            if window.scene == scene:
                window.scene = target
            ws.sequencer_scene = target
            # Undo removes the copy but leaves the workspace's Sequencer pointing at
            # nothing (5.2.4): remember what it showed, the undo handler puts it back.
            _SHOWN_BEFORE[ws.name] = scene.name
        else:
            window.scene = target
    return {"scene": target.name, "source_scene": scene.name, "copied": copied, "mode": mode,
            "resolution": [w, h], "added": done["added"], "kept": done["kept"], "reset": done["reset"],
            "converted": done["converted"], "captions": captions, "left_alone": left_alone,
            "scope": SCOPE_NOTE, "hint": CROP_HINT if mode == "CROP" else ""}


# -- Nudge / mode / reset ------------------------------------------------------------------

def _set_animated(scene: Any, mod: Any, name: str, value: Any) -> None:
    """Set an input; if it's animated, key it on the current frame (like auto-keying)."""
    set_inputs(mod, {name: value})
    if fcurve_for(scene, mod, name) is not None:
        item = getattr(mod.properties.inputs, _input_ids(mod.node_group)[name])
        item.keyframe_insert("value", frame=scene.frame_current)


def _nudge_plain(scene: Any, strip: Any, action: str, step: float) -> str:
    """Pan / zoom a plain-crop strip: the same steps as the modifier's inputs.  A keyed pan gets a key on the
    current frame (like auto-keying)."""
    f = plain_framing_of(scene, strip)
    if action in {"LEFT", "RIGHT", "UP", "DOWN"}:
        name, d = {"LEFT": ("Offset X", -step), "RIGHT": ("Offset X", step), "UP": ("Offset Y", step),
                   "DOWN": ("Offset Y", -step)}[action]
        f[name] = rm.clamp(f[name] + d)
        result = "nudged"
    else:
        z = f["Zoom"] * ((1.0 + step) if action == "ZOOM_IN" else 1.0 / (1.0 + step))
        f["Zoom"] = min(10.0, max(1.0, z))
        result = "zoomed"
    keyed = plain_keyed(scene, strip)
    # Every pan key follows the new scale / frame (a zoom or a resolution change moves them all).
    build_plain(scene, strip, f, tracked=keyed)
    if keyed and result == "nudged":                 # a zoom keeps each key's subject, it adds none
        strip.transform.keyframe_insert("offset_x", frame=scene.frame_current)
    return result


def apply_reframe(scene: Any, strip: Any, action: str, step: float = 0.1) -> str:
    if _layouts.is_split_part(strip):          # a split's parts have crop and transform, not the modifier (#606)
        raise ReframeError("'{:s}' is part of a split: use the Reframe layouts (Strip > Reframe Layout) to change it.".format(strip.name))
    mod = find_modifier(strip)
    if mod is None and plain_marker(strip) is None:
        reframe_strip(scene, strip, "BLUR_FILL" if action == "BLUR_FILL" else "CROP")
        if action in {"CROP", "BLUR_FILL", "RESET_FRAMING"}:
            return "added"
        mod = find_modifier(strip)
    if mod is None:                             # the plain crop (#632)
        if action == "RESET_FRAMING":
            reframe_strip(scene, strip, "CROP", reset=True)
            return "reset"
        if action == "CROP":
            return "mode"
        if action == "BLUR_FILL":
            reframe_strip(scene, strip, "BLUR_FILL")
            return "mode"
        if action in {"LEFT", "RIGHT", "UP", "DOWN", "ZOOM_IN", "ZOOM_OUT"}:
            return _nudge_plain(scene, strip, action, step)
        raise ReframeError("Unknown Reframe action {!r}.".format(action))
    vals = get_inputs(mod)
    if action == "RESET_FRAMING":
        reframe_strip(scene, strip, "BLUR_FILL" if vals.get("Blur Fill") else "CROP", reset=True)
        return "reset"
    if action == "CROP":
        if not to_plain(scene, strip, from_blur=True):          # keyed: the modifier stays, Blur Fill off
            _set_animated(scene, mod, "Blur Fill", False)
        return "mode"
    if action == "BLUR_FILL":
        _set_animated(scene, mod, "Blur Fill", True)
        return "mode"
    deltas = {"LEFT": ("Offset X", -step), "RIGHT": ("Offset X", step), "UP": ("Offset Y", step),
              "DOWN": ("Offset Y", -step)}
    if action in deltas:
        name, d = deltas[action]
        _set_animated(scene, mod, name, rm.clamp(float(vals[name]) + d))
        return "nudged"
    if action in {"ZOOM_IN", "ZOOM_OUT"}:
        z = float(vals["Zoom"]) * ((1.0 + step) if action == "ZOOM_IN" else 1.0 / (1.0 + step))
        _set_animated(scene, mod, "Zoom", min(10.0, max(0.1, z)))
        return "zoomed"
    raise ReframeError("Unknown Reframe action {!r}.".format(action))


# -- Follow subject ------------------------------------------------------------------------

def render_samples(strip: Any, frames: list[int], outdir: str, width: int = 384) -> list[tuple[int, str]]:
    """Render *strip*'s own picture (no Reframe, the whole frame) at *frames*, small, to PNGs.

    A scene strip's 3D scene renders under the preview profile's caps (samples, time limit,
    Simplify, the passes; #527), put back when the samples are done."""
    sw, sh = source_size(strip)
    tmp = bpy.data.scenes.new("Coworker Reframe Sample")
    out = []
    capped: list[Any] = []
    try:
        r = tmp.render
        r.resolution_x, r.resolution_y = width, max(2, int(round(width * sh / sw)))
        r.resolution_percentage = 100
        r.image_settings.file_format = 'PNG'
        tmp.view_settings.view_transform = 'Standard'
        ed = tmp.sequence_editor_create()
        if strip.type == "MOVIE":
            src = ed.strips.new_movie("src", bpy.path.abspath(strip.filepath), 1, 1, fit_method='FIT')
        elif strip.type == "IMAGE":
            first = bpy.path.abspath(os.path.join(strip.directory, strip.elements[0].filename))
            src = ed.strips.new_image("src", first, 1, 1, fit_method='FIT')
            for el in list(strip.elements)[1:]:
                src.elements.append(el.filename)
        else:
            src = ed.strips.new_scene("src", strip.scene, 1, 1)
            src.transform.scale_x = src.transform.scale_y = width / float(sw)
        src.right_handle = max(src.right_handle, 2 + max(int(f - strip.content_start) for f in frames))
        capped = _cap_strip_scenes(tmp)
        for f in frames:
            tmp.frame_set(1 + int(f - strip.content_start))
            path = os.path.join(outdir, "{:s}_{:05d}.png".format(bpy.path.clean_name(strip.name), f))
            r.filepath = path
            # render.render(scene=...) renders the context scene's frame, not tmp's (5.2.4).
            with bpy.context.temp_override(scene=tmp):
                bpy.ops.render.render(write_still=True)
            if os.path.isfile(path):
                out.append((f, path))
    finally:
        _uncap(capped)
        bpy.data.scenes.remove(tmp)
    return out


def _cap_strip_scenes(scene: Any) -> list[Any]:
    """The preview profile on the scenes *scene*'s scene strips pull in (#527); the undo list
    for :func:`_uncap`.  The profile is the tool-code's own file (``chat_attachments`` reads
    it); without it nothing is capped and the render runs as before."""
    from . import chat_attachments
    apply = chat_attachments._preview_profile().get("_preview_profile_apply_strip_scenes")
    if apply is None:
        return []
    saved, _names = apply(scene)
    return saved


def _uncap(saved: list[Any]) -> None:
    """Put back what :func:`_cap_strip_scenes` changed."""
    from . import chat_attachments
    restore = chat_attachments._preview_profile().get("_preview_profile_restore")
    if restore is not None:
        restore(saved)


def _follow_targets(scene: Any, strips: Iterable[Any]) -> tuple[list[Any], list[str]]:
    ok, skipped = [], []
    for s in strips:
        mod = find_modifier(s)
        if mod is None and plain_marker(s) is not None:
            ok.append(s)                                   # the plain crop (#632)
        elif mod is None:
            skipped.append("{:s} (not reframed yet)".format(s.name))
        elif get_inputs(mod).get("Blur Fill"):
            skipped.append("{:s} (Blur Fill shows the whole shot)".format(s.name))
        else:
            ok.append(s)
    return ok, skipped


def start_follow(scene: Any, strips: Iterable[Any], follow: str = "auto", layout: str = "single",
                 on_done: Any = None) -> str:
    """Find the subject(s) of the shots and key the framing (``reframe_subjects.start``, #592): faces and people
    from the vision model when one is connected, what moves (screen activity, the pointer) without one.

    Returns a short status line; raises :class:`ReframeError` when it can't start."""
    from . import reframe_subjects
    return reframe_subjects.start(scene, strips, follow, layout, on_done)


def _follow_plain(scene: Any, strip: Any, samples: list[tuple[int, Any]], fps: float, smooth: bool = True) -> int:
    """Key the pan of a plain-crop strip (``transform.offset_x``) from the subject positions."""
    sw, sh = source_size(strip)
    fw, fh = frame_size(scene)
    f = plain_framing_of(scene, strip)
    keys = rm.smooth_track(samples, fps) if smooth else [(int(fr), float(x)) for fr, x in samples if x is not None]
    if not keys:
        return 0
    _clear_plain_keys(scene, strip)
    for frame, x in keys:
        part = rp.subject_part(sw, sh, fw, fh, x, f["Offset Y"], f["Zoom"])
        _apply_part(strip, part)
        strip.transform.keyframe_insert("offset_x", frame=frame)
    _set_marker(scene, strip)
    return len(keys)


def apply_follow(scene: Any, results: dict[str, list[tuple[int, Any]]], smooth: bool = True) -> dict[str, int]:
    """Key Offset X from sampled subject positions; returns keys per strip.  *smooth* False: the positions
    are already keys (smoothed per shot by ``subject_find``, which never smooths across a cut)."""
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    w, h = canvas_size(scene)
    keyed = {}
    for name, samples in results.items():
        strip = scene.sequence_editor.strips_all.get(name) if scene.sequence_editor else None
        if strip is None:
            continue
        mod = find_modifier(strip)
        if mod is None:
            if plain_marker(strip) is not None:
                keyed[name] = _follow_plain(scene, strip, samples, fps, smooth)
            continue
        sw, sh = source_size(strip)
        zoom = float(get_inputs(mod).get("Zoom", 1.0))
        keys = rm.smooth_track(samples, fps) if smooth else [(int(fr), float(x)) for fr, x in samples if x is not None]
        offsets = rm.track_to_offsets(keys, sw, w, rm.mode_scale("CROP", sw, sh, w, h, zoom))
        clear_keys(scene, mod, ["Offset X"])
        ident = _input_ids(mod.node_group)["Offset X"]
        for f, o in offsets:
            item = getattr(mod.properties.inputs, ident)
            item.value = o
            item.keyframe_insert("value", frame=f)
        keyed[name] = len(offsets)
    return keyed


def _tell(msg: str) -> None:
    wm = bpy.context.window_manager
    if bpy.app.background or wm is None or not wm.windows:
        return

    def draw(self: Any, _context: Any) -> None:
        self.layout.label(text=msg)
    try:
        wm.popup_menu(draw, title="Coworker", icon='INFO')
    except Exception:  # pylint: disable=broad-exception-caught
        pass


# -- Operators -----------------------------------------------------------------------------

def _seq_poll(context: Any, cls: Any) -> bool:
    """The Sequencer's scene, with strips; else greyed out with the reason (#504)."""
    return _seq.poll(cls, context)


def follow_made(done: dict[str, Any], follow: str = "auto", layout: str = "auto") -> str:
    """Start Follow Subject on the strips Make Vertical just framed (``done``: its answer); the status line."""
    target = bpy.data.scenes.get(done["scene"])
    if target is None or target.sequence_editor is None:
        raise ReframeError("The vertical scene was not found.")
    found = target.sequence_editor.strips_all
    strips = [found[n] for n in done["added"] + done["kept"] + done["reset"] if n in found]
    return start_follow(target, strips, follow, layout)


def _engine_notice(follow: str) -> str:
    """One line for the dialog: where the sampled pictures go."""
    if follow == "screen":
        return "Nothing is sent anywhere: only what moves is looked at."
    try:
        mode = str(bpy.context.preferences.addons[__package__].preferences.operating_mode)
    except (KeyError, AttributeError):
        mode = ""
    return reframe_follow.engine_notice(mode)


class BFACW_OT_make_vertical(Operator):
    """Make this edit vertical (9:16): every shot is framed and editable, Centre Crop (the strip's own crop, fast) or Blur Fill (a Reframe modifier)"""
    bl_idname = "bfacw.make_vertical"
    bl_label = "Make Vertical (9:16)"
    bl_description = agent_line(__doc__, "Follow Subject, if ticked, which needs a connected vision model")
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name="Mode", items=_enum_items(MODES), default="CROP")  # type: ignore[valid-type]
    in_copy: BoolProperty(  # type: ignore[valid-type]
        name="In a Copy of the Scene", default=True,
        description="Make a vertical copy of the scene ('<name> Vertical') and switch the Sequencer to it, "
                    "so the 16:9 edit stays as it is")
    resolution: EnumProperty(name="Resolution", items=_enum_items(RESOLUTIONS), default="1080x1920")  # type: ignore[valid-type]
    reset: BoolProperty(  # type: ignore[valid-type]
        name="Reset Framing", default=False,
        description="Strips that already have a framing get a fresh one too "
                    "(otherwise they keep what you set)")
    follow: BoolProperty(  # type: ignore[valid-type]
        name="Follow Subject", default=False,
        description="Centre Crop only: look at a few frames per shot for the subject (a face, a person, what "
                    "changes on a screen, the pointer) and key the crop to follow it. Faces need a connected "
                    "vision-capable engine; what moves needs nothing")
    follow_kind: EnumProperty(  # type: ignore[valid-type]
        name="Find", items=[("auto", "Faces and Screen", "Faces and people when a vision model is connected, and what moves"),
                            ("face", "Faces Only", "Faces and people only (needs a connected vision model)"),
                            ("screen", "Screen Only", "What moves on screen and the pointer: no model, nothing is sent")],
        default="auto")
    subject_layout: EnumProperty(  # type: ignore[valid-type]
        name="Layout", items=[("auto", "Automatic", "Two crops, top and bottom, when two subjects are apart; else one"),
                              ("single", "One Crop", "One crop that follows the main subject"),
                              ("split", "Two Crops", "Top and bottom: each crop follows its own subject")],
        default="auto")

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _seq_poll(context, cls)

    def invoke(self, context: Any, _event: Any) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, _context: Any) -> None:
        layout = self.layout
        layout.use_property_split = True
        layout.label(text="Frames every visible shot, not only the selection.", icon='INFO')
        layout.prop(self, "mode", expand=True)
        if self.mode == "CROP":
            layout.label(text="Centre Crop keeps the middle: pan off-centre subjects", icon='INFO')
            layout.label(text="(Strip > Reframe > Pan) or tick Follow Subject.")
        layout.prop(self, "resolution")
        layout.prop(self, "in_copy")
        layout.prop(self, "reset")
        row = layout.row()
        row.active = self.mode == "CROP"
        row.prop(self, "follow")
        if self.follow and self.mode == "CROP":
            layout.prop(self, "follow_kind")
            layout.prop(self, "subject_layout")
            layout.label(text=_engine_notice(self.follow_kind), icon='INFO')

    def execute(self, context: Any) -> set[str]:
        scene = sequencer_scene(context)
        try:
            done = make_vertical(scene, self.mode, self.in_copy, self.resolution, self.reset,
                                 window=context.window)
        except ReframeError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        parts = ["{:s} at {:d} x {:d}".format(done["scene"], *done["resolution"])]
        if done["added"] or done["reset"]:
            parts.append("{:d} shot(s) reframed".format(len(done["added"]) + len(done["reset"])))
        if done["kept"]:
            parts.append("{:d} kept their framing".format(len(done["kept"])))
        if done.get("left_alone"):
            parts.append("{:d} part(s) of a split left alone".format(len(done["left_alone"])))
        if done.get("captions", {}).get("message"):
            parts.append(done["captions"]["message"])
        self.report({'INFO'}, "Make Vertical: " + ", ".join(parts))
        if done.get("hint") and not self.follow:
            self.report({'INFO'}, done["hint"])
        if self.follow and self.mode == "CROP":
            try:
                self.report({'INFO'}, follow_made(done, self.follow_kind, self.subject_layout))
            except (ReframeError, reframe_follow.Unavailable) as ex:
                self.report({'WARNING'}, "Follow subject: {!s}".format(ex))
        return {'FINISHED'}


_ACTIONS = (
    ("LEFT", "Pan Left", "Pan the frame left (Offset X down)", 'TRIA_LEFT'),
    ("RIGHT", "Pan Right", "Pan the frame right (Offset X up)", 'TRIA_RIGHT'),
    ("UP", "Pan Up", "Pan the frame up (Offset Y up)", 'TRIA_UP'),
    ("DOWN", "Pan Down", "Pan the frame down (Offset Y down)", 'TRIA_DOWN'),
    ("ZOOM_IN", "Zoom In", "Zoom in", 'ZOOM_IN'),
    ("ZOOM_OUT", "Zoom Out", "Zoom out", 'ZOOM_OUT'),
    ("CROP", "Centre Crop", "Crop the shot to fill the frame", 'FULLSCREEN_ENTER'),
    ("BLUR_FILL", "Blur Fill", "Show the whole shot over a blurred copy of itself", 'MATFLUID'),
    ("RESET_FRAMING", "Reset Framing", "Centre the shot again with zoom 1 (removes its framing keyframes)",
     'LOOP_BACK'),
)


class BFACW_OT_reframe(Operator):
    """Change the framing of the selected strips: pan, zoom, Centre Crop, Blur Fill (also in Properties > Strip Modifiers)"""
    bl_idname = "bfacw.reframe"
    bl_label = "Reframe"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(name="Action", items=_enum_items(_ACTIONS), default="RIGHT")  # type: ignore[valid-type]
    step: FloatProperty(name="Step", default=0.1, min=0.01, max=1.0)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _seq_poll(context, cls)

    def execute(self, context: Any) -> set[str]:
        scene = sequencer_scene(context)
        strips = [s for s in scene.sequence_editor.strips_all if s.select and s.type in STRIP_TYPES]
        if not strips:
            self.report({'WARNING'}, "Select movie, image or scene strips to reframe.")
            return {'CANCELLED'}
        parts = [s.name for s in strips if _layouts.is_split_part(s)]
        if parts:                   # #606: a split's parts keep to the Reframe layouts
            strips = [s for s in strips if s.name not in parts]
            if not strips:
                self.report({'ERROR'}, "This strip is part of a split: use the Reframe layouts "
                                       "(Strip > Reframe Layout).")
                return {'CANCELLED'}
            self.report({'WARNING'}, "{:d} part(s) of a split left alone: use the Reframe layouts.".format(len(parts)))
        try:
            for s in strips:
                apply_reframe(scene, s, self.action, self.step)
        except ReframeError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class BFACW_OT_reframe_follow_subject(Operator):
    """Key the crop so the frame follows the subject: looks at a few frames of each selected shot for a face, a person, what moves on the screen or the pointer"""
    bl_idname = "bfacw.reframe_follow_subject"
    bl_label = "Follow Subject"
    bl_description = model_line(
        "Key the crop so the frame follows the subject: looks at a few frames of each selected shot for a face, a "
        "person, what moves on the screen or the pointer",
        "Faces need a vision model: a Remote one, or Coworker started with a Local one that can see. What moves "
        "needs none.")
    bl_options = {'REGISTER'}

    follow_kind: EnumProperty(  # type: ignore[valid-type]
        name="Find", items=[("auto", "Faces and Screen", "Faces and people when a vision model is connected, and what moves"),
                            ("face", "Faces Only", "Faces and people only (needs a connected vision model)"),
                            ("screen", "Screen Only", "What moves on screen and the pointer: no model, nothing is sent")],
        default="auto")
    subject_layout: EnumProperty(  # type: ignore[valid-type]
        name="Layout", items=[("single", "One Crop", "One crop that follows the main subject"),
                              ("auto", "Automatic", "Two crops, top and bottom, when two subjects are apart; else one"),
                              ("split", "Two Crops", "Top and bottom: each crop follows its own subject")],
        default="single")

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _seq_poll(context, cls)

    def invoke(self, context: Any, _event: Any) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, _context: Any) -> None:
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "follow_kind")
        layout.prop(self, "subject_layout")
        layout.label(text=_engine_notice(self.follow_kind), icon='INFO')

    def execute(self, context: Any) -> set[str]:
        scene = sequencer_scene(context)
        strips = [s for s in scene.sequence_editor.strips_all if s.select and s.type in STRIP_TYPES]
        try:
            self.report({'INFO'}, start_follow(scene, strips, self.follow_kind, self.subject_layout))
        except (ReframeError, reframe_follow.Unavailable) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class BFACW_OT_reframe_follow_stop(Operator):
    """Stop the running Follow Subject after the sample it is on; nothing is keyed"""
    bl_idname = "bfacw.reframe_follow_stop"
    bl_label = "Stop Follow Subject"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, _context: Any) -> bool:
        from . import reframe_subjects
        return bool(reframe_subjects.progress())

    def execute(self, _context: Any) -> set[str]:
        from . import reframe_subjects
        reframe_subjects.cancel()
        return {'FINISHED'}


class BFACW_MT_reframe(Menu):
    bl_idname = "BFACW_MT_reframe"
    bl_label = "Reframe"

    def draw(self, _context: Any) -> None:
        layout = self.layout
        for ident, label, ic in (("LEFT", "Pan Left", 'TRIA_LEFT'), ("RIGHT", "Pan Right", 'TRIA_RIGHT'),
                                 ("UP", "Pan Up", 'TRIA_UP'), ("DOWN", "Pan Down", 'TRIA_DOWN'),
                                 ("ZOOM_IN", "Zoom In", 'ZOOM_IN'), ("ZOOM_OUT", "Zoom Out", 'ZOOM_OUT')):
            layout.operator("bfacw.reframe", text=label, icon=icon(ic)).action = ident
        layout.separator()
        layout.operator("bfacw.reframe", text="Centre Crop", icon=icon('FULLSCREEN_ENTER')).action = "CROP"
        layout.operator("bfacw.reframe", text="Blur Fill", icon=icon('MATFLUID')).action = "BLUR_FILL"
        layout.separator()
        layout.operator("bfacw.reframe_follow_subject", icon=icon('TRACKER'))
        from . import reframe_subjects
        running = reframe_subjects.progress()
        if running:
            layout.operator("bfacw.reframe_follow_stop", text="Stop Follow Subject ({:s})".format(running),
                            icon=icon('CANCEL'))
        layout.separator()
        # the one that throws framing away goes last (wand rules, #610)
        layout.operator("bfacw.reframe", text="Reset Framing", icon=icon('LOOP_BACK')).action = "RESET_FRAMING"


_CLASSES = (BFACW_OT_make_vertical, BFACW_OT_reframe, BFACW_OT_reframe_follow_subject,
            BFACW_OT_reframe_follow_stop, BFACW_MT_reframe)
# Make Vertical and the Reframe menu are drawn by wand.sequencer's Coworker section of the
# Strip menu and its Video panel (one place for every Coworker video tool, each once).
_MENUS: tuple = ()
_HOOKED: list[tuple[Any, Any]] = []


# workspace name -> the scene its Sequencer showed before Make Vertical switched it to a copy.
_SHOWN_BEFORE: dict[str, str] = {}


@bpy.app.handlers.persistent
def _after_undo(*_args: Any) -> None:
    """Ctrl+Z after Make Vertical (copy) removes the copy; show the 16:9 edit again
    instead of an empty Sequencer."""
    if not _SHOWN_BEFORE:
        return
    for ws in bpy.data.workspaces:
        name = _SHOWN_BEFORE.get(ws.name)
        if name and getattr(ws, "sequencer_scene", True) is None:
            scene = bpy.data.scenes.get(name)
            if scene is not None:
                ws.sequencer_scene = scene


_UNDO_HANDLERS = ("undo_post", "redo_post")


def register() -> None:
    for kind in _UNDO_HANDLERS:
        handlers = getattr(bpy.app.handlers, kind)
        if _after_undo not in handlers:
            handlers.append(_after_undo)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    for name, draw in _MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.append(draw)
            _HOOKED.append((menu, draw))
    from . import reframe_layouts           # #606: Reframe layouts
    reframe_layouts.register()


def unregister() -> None:
    from . import reframe_layouts
    reframe_layouts.unregister()
    for menu, draw in _HOOKED:
        try:
            menu.remove(draw)
        except (ValueError, RuntimeError):
            pass
    _HOOKED.clear()
    for kind in _UNDO_HANDLERS:
        handlers = getattr(bpy.app.handlers, kind)
        while _after_undo in handlers:
            handlers.remove(_after_undo)
    from . import reframe_subjects
    reframe_subjects.unregister()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
