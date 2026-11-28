# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Zoom To (#542): zoom the selected shots into a region -- the centre or a corner -- with a quick preset, and
optionally key it as a punch-in over the shot (ease in, hold, ease out) for tutorials.

It reuses Reframe's own framing (#632): a strip's *crop and transform*, made by :func:`reframe_plain.plain_part`, so
the shot stays covered with no black areas whatever the zoom and corner.  Offset X / Y are the modifier's units:
-1 is the picture's left (bottom) edge, 1 its right (top) edge.  A corner is the offsets (-1 or 1, -1 or 1); the
zoom is 1.5 or 2 over what just covers the frame.

* **Static**: the framing is set on the strip (``reframe.build_plain``); Reframe's Pan and Zoom buttons go on from
  it.
* **Punch In**: over the strip's range, keys on ``transform.scale_x / scale_y / offset_x / offset_y`` at the
  strip's first frame (the plain Centre Crop framing), after the ease-in (the zoomed framing), before the
  ease-out (the same) and at its last frame (the plain framing).  Every key is the framing's own numbers and the
  picture is covered at each: a pan range grows in step with the scale, so a straight blend between two covered
  framings stays covered.  The crop is zero while keyed (a crop that fits the zoomed key would cut into the picture
  at the ends).  The strip carries ``coworker_punch_in`` so Reframe's nudges leave it alone and Reset Framing takes
  every key and the tag away.

Reframe's Follow Subject keys the same strip (its pan); a strip that is followed is not zoomed (Reset Framing
first), and one that is zoomed is not followed.
"""

from __future__ import annotations

__all__ = (
    "BFACW_MT_reframe_zoom_to",
    "BFACW_OT_reframe_zoom_to",
    "REGIONS",
    "ZOOMS",
    "punch_frames",
    "region_id",
    "register",
    "unregister",
    "zoom_offsets",
    "zoom_strips",
    "zoom_to",
)

from typing import Any, Iterable

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, EnumProperty, FloatProperty  # pylint: disable=import-error
from bpy.types import Menu, Operator  # pylint: disable=import-error

from . import reframe_plain as rp
from .shared import icon
from .wand import reframe_apply as _layouts
from .wand import scene as _seq
from .wand.spec import agent_line

# (id, label, tooltip, icon): the corners share an icon with the edge they touch (Blender has no corner icon);
# the label says which.
REGIONS = (
    ("CENTER", "Centre", "Zoom into the middle of the shot", 'ANCHOR_CENTER'),
    ("TOP_LEFT", "Top Left", "Zoom into the top left corner of the shot", 'ANCHOR_TOP'),
    ("TOP_RIGHT", "Top Right", "Zoom into the top right corner of the shot", 'ANCHOR_TOP'),
    ("BOTTOM_LEFT", "Bottom Left", "Zoom into the bottom left corner of the shot", 'ANCHOR_BOTTOM'),
    ("BOTTOM_RIGHT", "Bottom Right", "Zoom into the bottom right corner of the shot", 'ANCHOR_BOTTOM'),
)
ZOOMS = (
    ("2.0", "2x", "Twice as close as the frame just covering the shot", 'ZOOM_IN'),
    ("1.5", "1.5x", "One and a half times as close as the frame just covering the shot", 'ZOOM_IN'),
)
# The picture fraction the frame is pushed to, in the modifier's units: -1 left / bottom edge, 1 right / top edge.
_OFFSETS = {"CENTER": (0.0, 0.0), "TOP_LEFT": (-1.0, 1.0), "TOP_RIGHT": (1.0, 1.0),
            "BOTTOM_LEFT": (-1.0, -1.0), "BOTTOM_RIGHT": (1.0, -1.0)}
DEFAULT_EASE_S = 0.5
MIN_PUNCH_FRAMES = 6            # a shot shorter than this cannot ease in and out
_KEYED = ("scale_x", "scale_y", "offset_x", "offset_y")


def region_id(text: Any) -> str:
    """The id of a region as an agent or a dialog writes it (``top left``, ``Top-Left``, ``TOP_LEFT``, ``center``), or ``""``."""
    word = str(text or "").strip().upper().replace("-", "_").replace(" ", "_").replace("CENTRE", "CENTER")
    return word if word in _OFFSETS else ""


def zoom_offsets(region: str) -> tuple[float, float]:
    """Offset X, Offset Y (the modifier's units) of *region*."""
    return _OFFSETS[region]


def punch_frames(left: int, right: int, fps: float, ease_s: float = DEFAULT_EASE_S) -> list[int] | None:
    """The four key frames of a punch-in over the shot ``left..right`` (``right`` exclusive): the first frame, the end
    of the ease-in, the start of the ease-out and the last frame; ``None`` when the shot is too short to ease in and out.
    The ease is *ease_s* seconds, at most a third of the shot."""
    first, last = int(left), int(right) - 1
    if last - first + 1 < MIN_PUNCH_FRAMES:
        return None
    ease = max(1, min(int(round(float(ease_s) * float(fps))), (last - first) // 3))
    return [first, first + ease, last - ease, last]


def _error(message: str) -> Exception:
    from .reframe import ReframeError
    return ReframeError(message)


def _plain_ready(scene: Any, strip: Any) -> None:
    """Give *strip* the plain Centre Crop framing Zoom To works on, or say why it cannot."""
    from . import reframe
    if _layouts.is_split_part(strip):
        raise _error("'{:s}' is part of a split: use the Reframe layouts (Strip > Reframe Layout) to change it.".format(strip.name))
    if strip.type not in reframe.STRIP_TYPES:
        raise _error("'{:s}' is not a movie, image or scene strip.".format(strip.name))
    if reframe.find_modifier(strip) is not None and not reframe.to_plain(scene, strip):
        raise _error("'{:s}' has Blur Fill or keys on its Reframe modifier: Zoom To works on Centre Crop "
                     "(Reset Framing first).".format(strip.name))
    if reframe.plain_marker(strip) is None:
        reframe.reframe_strip(scene, strip, "CROP")
    if reframe.plain_keyed(scene, strip) and strip.get(rp.PUNCH) is None:
        raise _error("'{:s}' follows a subject (its pan is keyed): Reset Framing first, then zoom.".format(strip.name))


def zoom_to(scene: Any, strip: Any, region: str, zoom: float = 2.0, punch_in: bool = False,
            ease_s: float = DEFAULT_EASE_S) -> str:
    """Zoom *strip* into *region* by *zoom* (1.5 or 2).  Static: the framing of the strip.  *punch_in*: keyed over the
    strip's range, easing in and out (:func:`punch_frames`).  Returns ``"zoomed"`` or ``"punched"``.  Raises
    ``reframe.ReframeError`` when it cannot."""
    from . import reframe
    if region not in _OFFSETS:
        raise _error("Unknown region {!r}: use one of {:s}.".format(region, ", ".join(_OFFSETS)))
    zoom = float(zoom)
    if not 1.0 < zoom <= 10.0:
        raise _error("The zoom is more than 1 and up to 10 (1.5 and 2 are the presets).")
    frames = None
    if punch_in:
        fps = float(scene.render.fps) / (float(scene.render.fps_base) or 1.0)
        frames = punch_frames(int(strip.left_handle), int(strip.right_handle), fps, ease_s)
        if frames is None:
            raise _error("'{:s}' is too short to punch in ({:d} frames): zoom it without Punch In.".format(
                strip.name, MIN_PUNCH_FRAMES - 1))
    _plain_ready(scene, strip)
    reframe.clear_framing_keys(scene, strip)
    ox, oy = _OFFSETS[region]
    if frames is None:
        reframe.build_plain(scene, strip, {"Offset X": ox, "Offset Y": oy, "Zoom": zoom})
        return "zoomed"
    sw, sh = reframe.source_size(strip)
    fw, fh = reframe.frame_size(scene)
    base = rp.plain_part(sw, sh, fw, fh)
    near = rp.plain_part(sw, sh, fw, fh, ox, oy, zoom)
    _layouts.set_part(strip, base._replace(crop=(0, 0, 0, 0)))       # no crop while keyed: it would fit one key only
    t = strip.transform
    for frame, part in zip(frames, (base, near, near, base)):
        t.scale_x = t.scale_y = part.scale
        t.offset_x, t.offset_y = part.offset
        for prop in _KEYED:
            t.keyframe_insert(prop, frame=frame)
    reframe.ease_framing_keys(scene, strip)
    t.scale_x = t.scale_y = base.scale
    t.offset_x, t.offset_y = base.offset
    strip[rp.PUNCH] = {"region": region, "zoom": zoom, "ease": float(ease_s)}
    return "punched"


def zoom_strips(scene: Any, strips: Iterable[Any], region: str, zoom: float = 2.0, punch_in: bool = False,
                ease_s: float = DEFAULT_EASE_S) -> dict[str, Any]:
    """:func:`zoom_to` for each of *strips*: ``{"done": [names], "skipped": [{strip, reason}], "message": text}``."""
    from .reframe import ReframeError
    done, skipped = [], []
    for strip in strips:
        try:
            zoom_to(scene, strip, region, zoom, punch_in, ease_s)
            done.append(strip.name)
        except ReframeError as ex:
            skipped.append({"strip": strip.name, "reason": str(ex)})
    label = next((r[1] for r in REGIONS if r[0] == region), region)
    message = ""
    if done:
        message = "Zoomed {:d} strip{:s} to {:s} at {:g}x{:s}".format(
            len(done), "" if len(done) == 1 else "s", label, float(zoom), " as a punch-in (eases in, holds, eases out)" if punch_in else "")
    if skipped:
        message += ("; " if message else "") + "left alone: " + "; ".join(s["reason"] for s in skipped[:3])
    return {"done": done, "skipped": skipped, "message": message or "Nothing to zoom"}


# -- the operator and its menu -----------------------------------------------------------------

def _items(rows: Iterable[tuple]) -> list[tuple]:
    return [(ident, label, tip, icon(name), number) for number, (ident, label, tip, name) in enumerate(rows)]


class BFACW_OT_reframe_zoom_to(Operator):
    """Zoom the selected shots into a region (the centre or a corner) at 1.5x or 2x, the picture still covering the frame; optionally keyed as a punch-in that eases in, holds and eases out"""
    bl_idname = "bfacw.reframe_zoom_to"
    bl_label = "Zoom To"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER', 'UNDO'}

    region: EnumProperty(name="Region", items=_items(REGIONS), default="CENTER")  # type: ignore[valid-type]
    zoom: EnumProperty(name="Zoom", items=_items(ZOOMS), default="2.0")  # type: ignore[valid-type]
    punch_in: BoolProperty(  # type: ignore[valid-type]
        name="Punch In", default=False,
        description="Key the zoom over the shot: it eases in, holds, and eases out to the framing it had")
    ease: FloatProperty(  # type: ignore[valid-type]
        name="Ease", default=DEFAULT_EASE_S, min=0.1, max=5.0, unit='TIME_ABSOLUTE',
        description="Seconds the zoom takes to ease in and again to ease out (at most a third of the shot each)")
    dialog: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _seq.poll(cls, context)

    def invoke(self, context: Any, _event: Any) -> set[str]:
        if self.dialog:
            return context.window_manager.invoke_props_dialog(self, width=340, confirm_text="Zoom To")
        return self.execute(context)

    def draw(self, _context: Any) -> None:
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        box = layout.box()
        box.label(text="Where: the part of the shot to zoom into", icon=icon('ANCHOR_CENTER'))
        box.prop(self, "region", text="")
        box.prop(self, "zoom", expand=True)
        box = layout.box()
        box.label(text="Punch In: key it over the shot", icon=icon('KEYFRAME_HLT'))
        box.prop(self, "punch_in")
        row = box.row()
        row.active = self.punch_in
        row.prop(self, "ease")

    def execute(self, context: Any) -> set[str]:
        from . import reframe
        scene = reframe.sequencer_scene(context)
        editor = scene.sequence_editor
        strips = [s for s in editor.strips_all if s.select and s.type in reframe.STRIP_TYPES]
        if not strips:
            self.report({'WARNING'}, "Select movie, image or scene strips to zoom.")
            return {'CANCELLED'}
        out = zoom_strips(scene, strips, self.region, float(self.zoom), self.punch_in, self.ease)
        self.report({'INFO'} if out["done"] else {'WARNING'}, out["message"])
        return {'FINISHED'} if out["done"] else {'CANCELLED'}


class BFACW_MT_reframe_zoom_to(Menu):
    bl_idname = "BFACW_MT_reframe_zoom_to"
    bl_label = "Zoom To"

    def draw(self, _context: Any) -> None:
        layout = self.layout
        layout.operator_context = 'EXEC_DEFAULT'
        for z_id, z_label, _tip, _icon in ZOOMS:
            for r_id, r_label, _t, r_icon in REGIONS:
                op = layout.operator("bfacw.reframe_zoom_to", text="{:s} {:s}".format(r_label, z_label), icon=icon(r_icon))
                op.region, op.zoom, op.punch_in = r_id, z_id, False
            layout.separator()
        layout.operator_context = 'INVOKE_DEFAULT'
        layout.operator("bfacw.reframe_zoom_to", text="Zoom To...", icon=icon('KEYFRAME_HLT')).dialog = True


_CLASSES = (BFACW_OT_reframe_zoom_to, BFACW_MT_reframe_zoom_to)
_DONE: list[type] = []


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
        _DONE.append(cls)


def unregister() -> None:
    for cls in reversed(_DONE):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _DONE.clear()
