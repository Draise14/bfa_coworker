# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Make Vertical (9:16) and Reframe in the Sequencer (Video 3, #333).

*Make Vertical* sets the output to 9:16 -- in a copy of the scene by default, so the
16:9 edit stays -- and gives every visible movie, image and scene strip a **Reframe**
compositor modifier: Centre Crop, or *Blur Fill* (the whole shot over a blurred,
dimmed copy of itself).  The framing stays editable: it is just the modifier's
inputs (Properties editor > Strip Modifiers tab, or Strip > Reframe), one value set per shot.

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
    "ReframeError",
    "ensure_group",
    "find_modifier",
    "get_inputs",
    "make_vertical",
    "register",
    "set_inputs",
    "unregister",
)

import os
import tempfile
import threading
from typing import Any, Iterable

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty  # pylint: disable=import-error
from bpy.types import Menu, Operator  # pylint: disable=import-error

from . import reframe_follow
from . import reframe_math as rm
from .shared import icon

GROUP_NAME = "Coworker Reframe v1"
GROUP_TAG = "coworker_reframe"          # custom property on the group: its layout version
GROUP_VERSION = 1
MOD_NAME = "Reframe"
VERTICAL_TAG = "coworker_vertical_of"   # on a vertical copy: the scene it was made from
STRIP_TYPES = {"MOVIE", "IMAGE", "SCENE"}
# Blur Fill blurs a quarter-size copy (much less work for the same look), then scales it back up.
BLUR_DOWNSCALE = 0.25

RESOLUTIONS = (
    ("1080x1920", "1080 x 1920 (Full HD)", "9:16 at 1080 x 1920, the usual size for short-form video"),
    ("720x1280", "720 x 1280 (HD)", "9:16 at 720 x 1280: smaller files, faster renders"),
    ("2160x3840", "2160 x 3840 (4K)", "9:16 at 2160 x 3840"),
)
MODES = (
    ("CROP", "Centre Crop", "Fill the 9:16 frame with the middle of each shot (pan it afterwards with Offset X)"),
    ("BLUR_FILL", "Blur Fill", "Show each whole shot over a blurred, dimmed copy of itself"),
)

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

def supported() -> str:
    """``""`` when this Blender can do it, else why not."""
    if not hasattr(bpy.types, "SequencerCompositorModifierData"):
        return "Make Vertical needs the Sequencer's compositor modifier (Blender 5.2 or later)."
    if "properties" not in bpy.types.SequencerCompositorModifierData.bl_rna.properties:
        return "Make Vertical needs per-strip modifier inputs (Blender 5.2 or later)."
    return ""


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
    scene = getattr(context, "sequencer_scene", None)
    if scene is None:
        scene = getattr(getattr(context, "workspace", None), "sequencer_scene", None)
    return scene or getattr(context, "scene", None)


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


def reframe_strip(scene: Any, strip: Any, mode: str, reset: bool = False) -> str:
    """Give *strip* its Reframe modifier (or update its sizes); returns added / kept / reset."""
    ng = ensure_group()
    w, h = canvas_size(scene)
    sw, sh = source_size(strip)
    mod = find_modifier(strip)
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


def _copy_scene(scene: Any) -> Any:
    copy = scene.copy()
    copy.name = scene.name + " Vertical"
    copy[VERTICAL_TAG] = scene.name
    # scene.copy() shares the action: keyframes on the copy's modifiers would land in
    # the original's.  Give the copy its own.
    ad = copy.animation_data
    if ad is not None and ad.action is not None:
        ad.action = ad.action.copy()
    return copy


def make_vertical(scene: Any, mode: str = "CROP", in_copy: bool = True, resolution: str = "1080x1920",
                  reset: bool = False, names: Iterable[str] | None = None, window: Any = None) -> dict[str, Any]:
    """Make *scene*'s edit 9:16; returns what was done (see the MCP tool's Result)."""
    why = supported()
    if why:
        raise ReframeError(why)
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
    if not strips:
        raise ReframeError("No visible movie, image or scene strips in '{:s}'.".format(scene.name))
    names = [s.name for s in strips]
    target, copied = scene, False
    if in_copy and not scene.get(VERTICAL_TAG):
        target, copied = _copy_scene(scene), True
    target.render.resolution_x, target.render.resolution_y = w, h
    ed = target.sequence_editor
    done: dict[str, list[str]] = {"added": [], "kept": [], "reset": []}
    for name in names:
        strip = ed.strips_all.get(name)
        if strip is not None:
            done[reframe_strip(target, strip, mode, reset)].append(name)
    if copied and window is not None:
        ws = getattr(window, "workspace", None)
        if ws is not None and hasattr(ws, "sequencer_scene"):
            if window.scene == scene:
                window.scene = target
            ws.sequencer_scene = target
            # Undo removes the copy but leaves the workspace's Sequencer pointing at
            # nothing (5.2.4): remember what it showed, the undo handler puts it back.
            _SHOWN_BEFORE[ws.name] = scene.name
        else:
            window.scene = target
    return {"scene": target.name, "source_scene": scene.name, "copied": copied, "mode": mode,
            "resolution": [w, h], "added": done["added"], "kept": done["kept"], "reset": done["reset"]}


# -- Nudge / mode / reset ------------------------------------------------------------------

def _set_animated(scene: Any, mod: Any, name: str, value: Any) -> None:
    """Set an input; if it's animated, key it on the current frame (like auto-keying)."""
    set_inputs(mod, {name: value})
    if fcurve_for(scene, mod, name) is not None:
        item = getattr(mod.properties.inputs, _input_ids(mod.node_group)[name])
        item.keyframe_insert("value", frame=scene.frame_current)


def apply_reframe(scene: Any, strip: Any, action: str, step: float = 0.1) -> str:
    mod = find_modifier(strip)
    if mod is None:
        reframe_strip(scene, strip, "BLUR_FILL" if action == "BLUR_FILL" else "CROP")
        if action in {"CROP", "BLUR_FILL", "RESET_FRAMING"}:
            return "added"
        mod = find_modifier(strip)
    vals = get_inputs(mod)
    if action == "RESET_FRAMING":
        reframe_strip(scene, strip, "BLUR_FILL" if vals.get("Blur Fill") else "CROP", reset=True)
        return "reset"
    if action in {"CROP", "BLUR_FILL"}:
        _set_animated(scene, mod, "Blur Fill", action == "BLUR_FILL")
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
    """Render *strip*'s own picture (no Reframe, the whole frame) at *frames*, small, to PNGs."""
    sw, sh = source_size(strip)
    tmp = bpy.data.scenes.new("Coworker Reframe Sample")
    out = []
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
        bpy.data.scenes.remove(tmp)
    return out


# A running Follow subject: one at a time.
_FOLLOW: dict[str, Any] = {}


def _follow_targets(scene: Any, strips: Iterable[Any]) -> tuple[list[Any], list[str]]:
    ok, skipped = [], []
    for s in strips:
        mod = find_modifier(s)
        if mod is None:
            skipped.append("{:s} (no Reframe modifier)".format(s.name))
        elif get_inputs(mod).get("Blur Fill"):
            skipped.append("{:s} (Blur Fill shows the whole shot)".format(s.name))
        else:
            ok.append(s)
    return ok, skipped


def start_follow(scene: Any, strips: Iterable[Any], samples: int = 4, on_done: Any = None) -> str:
    """Sample frames, ask the vision model in the background, key Offset X when it answers.

    Returns a short status line; raises :class:`ReframeError` when it can't start.
    """
    if _FOLLOW.get("thread") is not None and _FOLLOW["thread"].is_alive():
        raise ReframeError("Follow subject is already running.")
    targets, skipped = _follow_targets(scene, strips)
    if not targets:
        raise ReframeError("Nothing to follow: " + ("; ".join(skipped) or "select strips with a Reframe modifier"))
    ask = reframe_follow.resolve_asker()          # raises reframe_follow.Unavailable with the reason
    outdir = tempfile.mkdtemp(prefix="bfacw_follow_", dir=bpy.app.tempdir or None)
    jobs = []
    for s in targets:
        frames = rm.sample_frames(int(s.left_handle), int(s.right_handle), samples)
        for f, path in render_samples(s, frames, outdir):
            jobs.append((s.name, f, path))
    state: dict[str, Any] = {"results": None, "error": "", "scene": scene.name, "skipped": skipped,
                             "on_done": on_done}

    def work() -> None:
        try:
            state["results"] = reframe_follow.run(jobs, ask)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            state["error"] = str(ex)
    thread = threading.Thread(target=work, name="bfacw-follow-subject", daemon=True)
    _FOLLOW.clear()
    _FOLLOW.update(thread=thread, state=state)
    thread.start()
    bpy.app.timers.register(_follow_poll, first_interval=0.5)
    return "Following the subject in {:d} shot(s) ({:d} frames)...".format(len(targets), len(jobs))


def apply_follow(scene: Any, results: dict[str, list[tuple[int, Any]]]) -> dict[str, int]:
    """Key Offset X from sampled subject positions; returns keys per strip."""
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    w, h = canvas_size(scene)
    keyed = {}
    for name, samples in results.items():
        strip = scene.sequence_editor.strips_all.get(name) if scene.sequence_editor else None
        mod = find_modifier(strip) if strip is not None else None
        if mod is None:
            continue
        sw, sh = source_size(strip)
        zoom = float(get_inputs(mod).get("Zoom", 1.0))
        keys = rm.smooth_track(samples, fps)
        offsets = rm.track_to_offsets(keys, sw, w, rm.mode_scale("CROP", sw, sh, w, h, zoom))
        clear_keys(scene, mod, ["Offset X"])
        ident = _input_ids(mod.node_group)["Offset X"]
        for f, o in offsets:
            item = getattr(mod.properties.inputs, ident)
            item.value = o
            item.keyframe_insert("value", frame=f)
        keyed[name] = len(offsets)
    return keyed


def _follow_poll() -> float | None:
    thread, state = _FOLLOW.get("thread"), _FOLLOW.get("state")
    if thread is None or state is None:
        return None
    if thread.is_alive():
        return 0.5
    _FOLLOW.clear()
    scene = bpy.data.scenes.get(state["scene"])
    msg = state["error"]
    keyed: dict[str, int] = {}
    if not msg and scene is not None and state["results"] is not None:
        try:
            keyed = apply_follow(scene, state["results"])
            bpy.ops.ed.undo_push(message="Follow Subject")
            msg = "Follow subject: keyed Offset X on {:d} shot(s) ({:s}).".format(
                len(keyed), ", ".join("{:s} {:d} keys".format(k, v) for k, v in keyed.items()) or "none")
        except Exception as ex:  # pylint: disable=broad-exception-caught
            msg = "Follow subject failed: {!s}".format(ex)
    if state["skipped"]:
        msg += " Skipped: " + "; ".join(state["skipped"]) + "."
    print("[Coworker] " + msg)
    _FOLLOW["last"] = {"message": msg, "keyed": keyed}
    if state.get("on_done"):
        try:
            state["on_done"](msg, keyed)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    _tell(msg)
    return None


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

def _seq_poll(context: Any) -> bool:
    scene = sequencer_scene(context)
    return scene is not None and scene.sequence_editor is not None


class BFACW_OT_make_vertical(Operator):
    """Make this edit vertical (9:16): every shot gets an editable Reframe modifier, Centre Crop or Blur Fill"""
    bl_idname = "bfacw.make_vertical"
    bl_label = "Make Vertical (9:16)"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name="Mode", items=MODES, default="CROP")  # type: ignore[valid-type]
    in_copy: BoolProperty(  # type: ignore[valid-type]
        name="In a Copy of the Scene", default=True,
        description="Make a vertical copy of the scene ('<name> Vertical') and switch the Sequencer to it, "
                    "so the 16:9 edit stays as it is")
    resolution: EnumProperty(name="Resolution", items=RESOLUTIONS, default="1080x1920")  # type: ignore[valid-type]
    reset: BoolProperty(  # type: ignore[valid-type]
        name="Reset Framing", default=False,
        description="Strips that already have a Reframe modifier get fresh framing too "
                    "(otherwise they keep what you set)")
    follow: BoolProperty(  # type: ignore[valid-type]
        name="Follow Subject", default=False,
        description="Centre Crop only: sample a few frames per shot, ask the vision model where the subject is, "
                    "and key Offset X to follow it (needs a connected vision-capable engine)")

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _seq_poll(context)

    def invoke(self, context: Any, _event: Any) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, _context: Any) -> None:
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "mode", expand=True)
        layout.prop(self, "resolution")
        layout.prop(self, "in_copy")
        layout.prop(self, "reset")
        row = layout.row()
        row.active = self.mode == "CROP"
        row.prop(self, "follow")

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
        self.report({'INFO'}, "Make Vertical: " + ", ".join(parts))
        if self.follow and self.mode == "CROP":
            target = bpy.data.scenes.get(done["scene"])
            strips = [target.sequence_editor.strips_all[n] for n in done["added"] + done["kept"] + done["reset"]]
            try:
                self.report({'INFO'}, start_follow(target, strips))
            except (ReframeError, reframe_follow.Unavailable) as ex:
                self.report({'WARNING'}, "Follow subject: {!s}".format(ex))
        return {'FINISHED'}


_ACTIONS = (
    ("LEFT", "Pan Left", "Pan the frame left (Offset X down)"),
    ("RIGHT", "Pan Right", "Pan the frame right (Offset X up)"),
    ("UP", "Pan Up", "Pan the frame up (Offset Y up)"),
    ("DOWN", "Pan Down", "Pan the frame down (Offset Y down)"),
    ("ZOOM_IN", "Zoom In", "Zoom in"),
    ("ZOOM_OUT", "Zoom Out", "Zoom out"),
    ("CROP", "Centre Crop", "Crop the shot to fill the frame"),
    ("BLUR_FILL", "Blur Fill", "Show the whole shot over a blurred copy of itself"),
    ("RESET_FRAMING", "Reset Framing", "Centre the shot again with zoom 1 (removes its framing keyframes)"),
)


class BFACW_OT_reframe(Operator):
    """Change the framing of the selected strips' Reframe modifier (also in Properties > Strip Modifiers)"""
    bl_idname = "bfacw.reframe"
    bl_label = "Reframe"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(name="Action", items=_ACTIONS, default="RIGHT")  # type: ignore[valid-type]
    step: FloatProperty(name="Step", default=0.1, min=0.01, max=1.0)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _seq_poll(context) and not supported()

    def execute(self, context: Any) -> set[str]:
        scene = sequencer_scene(context)
        strips = [s for s in scene.sequence_editor.strips_all if s.select and s.type in STRIP_TYPES]
        if not strips:
            self.report({'WARNING'}, "Select movie, image or scene strips to reframe.")
            return {'CANCELLED'}
        try:
            for s in strips:
                apply_reframe(scene, s, self.action, self.step)
        except ReframeError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class BFACW_OT_reframe_follow_subject(Operator):
    """Key Offset X so the frame follows the subject: samples a few frames per selected shot and asks the
    connected vision model where the subject is"""
    bl_idname = "bfacw.reframe_follow_subject"
    bl_label = "Follow Subject"
    bl_options = {'REGISTER'}

    samples: IntProperty(name="Frames per Shot", default=4, min=2, max=24)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _seq_poll(context) and not supported()

    def execute(self, context: Any) -> set[str]:
        scene = sequencer_scene(context)
        strips = [s for s in scene.sequence_editor.strips_all if s.select and s.type in STRIP_TYPES]
        try:
            self.report({'INFO'}, start_follow(scene, strips, self.samples))
        except (ReframeError, reframe_follow.Unavailable) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
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
        layout.operator("bfacw.reframe", text="Reset Framing", icon=icon('LOOP_BACK')).action = "RESET_FRAMING"
        layout.separator()
        layout.operator("bfacw.reframe_follow_subject", icon=icon('TRACKER'))


def _draw_strip_menu(self: Any, _context: Any) -> None:
    layout = self.layout
    layout.separator()
    layout.operator("bfacw.make_vertical", icon=icon('SEQ_PREVIEW'))
    layout.menu("BFACW_MT_reframe", icon=icon('VIEW_PAN'))


def _draw_view_menu(self: Any, _context: Any) -> None:
    layout = self.layout
    layout.separator()
    layout.operator("bfacw.make_vertical", icon=icon('SEQ_PREVIEW'))


_CLASSES = (BFACW_OT_make_vertical, BFACW_OT_reframe, BFACW_OT_reframe_follow_subject, BFACW_MT_reframe)
_MENUS = (("SEQUENCER_MT_strip", _draw_strip_menu), ("SEQUENCER_MT_view", _draw_view_menu))
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


def unregister() -> None:
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
    if bpy.app.timers.is_registered(_follow_poll):
        bpy.app.timers.unregister(_follow_poll)
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
