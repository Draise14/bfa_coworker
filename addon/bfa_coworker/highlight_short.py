# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Highlights H5 (#615), Make Short: a pick becomes a ready-to-render vertical scene.

Not destructive.  The short is a **copy of the scene** (``reframe.copy_scene``, the method Make Vertical uses)
whose frame range is the pick, with the borders in the pauses.  Nothing is deleted: the artist can move the
range.  The long edit is not touched, and the operator runs as one undo step, so one undo removes the whole short.

The polish chain, each step optional and each an existing tool, limited to the range:

1. **Remove Silences** (``frame_range`` mode, tighter settings than for a long edit);
2. **Make Vertical** inside the short (9:16, not a second copy), the layout the kind of video suggests;
3. **Auto Captions** in the style last used, for the speech inside the range;
4. a **hook title** over the first three seconds (a text strip, the pick's hook line).

Then the **output preset**: an MP4 file, H.264 video and AAC sound, named after the short, next to the .blend.
Rendering stays the artist's click.  One report line per step; a step that cannot run says why and the chain goes on.

The decisions (names, borders, settings, file name) are ``short_plan.py`` (no ``bpy``).  Highlight picks stay as
they are; Trim to Range (cut the short down to its range) is a follow-up.
"""

from __future__ import annotations

__all__ = (
    "ShortError",
    "build",
    "make_short",
)

import os
import tempfile
from typing import Any

import bpy  # pylint: disable=import-error

from . import highlight_picks as hp
from . import highlight_picks_ops as ops
from . import reframe
from . import scene_reader_ops
from . import short_plan as sp
from .wand import sequencer as _wand


class ShortError(Exception):
    """The short can't be built (nothing was changed, or the half-made copy was taken away again)."""


def _fps(scene: Any) -> float:
    return scene.render.fps / (scene.render.fps_base or 1.0)


def _media(ed: Any, first: int, end: int) -> list[Any]:
    """The sound and movie strips (top level, not muted) that play inside the frames ``first..end``."""
    return [s for s in ed.strips if s.type in ("SOUND", "MOVIE") and not s.mute
            and s.right_handle > first and s.left_handle < end]


def _show(window: Any, scene: Any, short: Any) -> None:
    """Make the Sequencer of *window* show *short* (the way Make Vertical does, and its undo handler too)."""
    if window is None:
        return
    ws = getattr(window, "workspace", None)
    if ws is not None:
        if window.scene == scene:
            window.scene = short
        ws.sequencer_scene = short
        reframe._SHOWN_BEFORE[ws.name] = scene.name       # pylint: disable=protected-access
    else:
        window.scene = short


# -- the steps -------------------------------------------------------------------------------------

def _silences(short: Any, first: int, last: int) -> tuple[str, int]:
    """Remove Silences inside the range.  ``(report line, frames removed)``."""
    names = [s.name for s in _media(short.sequence_editor, first, last + 1)]
    if not names:
        return sp.step_line("Remove Silences", "skipped", "no sound in the range"), 0
    out = _wand.remove_silences(short, sp.SILENCES, names, ripple=True, update_end=False, report_progress=False,
                                frame_range=(first, last + 1))
    if out["status"] == "ok":
        return sp.step_line("Remove Silences", "done", out["message"].replace(" from the edit", "")), int(out["frames_removed"])
    if out["status"] == "nothing":
        return sp.step_line("Remove Silences", "skipped", "no pause long enough to remove"), 0
    if out.get("changed"):
        raise ShortError(out["message"])
    return sp.step_line("Remove Silences", "failed", out["message"]), 0


def _vertical(short: Any, kind: str) -> str:
    mode = sp.layout_for(kind)
    try:
        done = reframe.make_vertical(short, mode, in_copy=False, resolution="1080x1920")
    except reframe.ReframeError as ex:
        return sp.step_line("Make Vertical", "failed", str(ex))
    count = len(done["added"]) + len(done["converted"]) + len(done["reset"]) + len(done["kept"])
    return sp.step_line("Make Vertical", "done", "9:16, {:s}, {:d} strip{:s}".format(
        "Blur Fill" if mode == "BLUR_FILL" else "Centre Crop", count, "" if count == 1 else "s"))


def _last_caption_style() -> str:
    """The style Auto Captions was last used with (Blender keeps the last settings of an operator), else Clean."""
    try:
        props = bpy.context.window_manager.operator_properties_last("bfacw.auto_captions")
        return str(props.style) if props is not None else "CLEAN"
    except (AttributeError, RuntimeError, TypeError):
        return "CLEAN"


def _captions(short: Any, first: int, last: int) -> str:
    from . import captions_ops
    sources = _media(short.sequence_editor, first, last + 1)
    if not sources:
        return sp.step_line("Auto Captions", "skipped", "no sound in the range")
    style = _last_caption_style()
    try:
        result = captions_ops.add_captions(short, sources, style=style, frame_range=(first, last + 1))
    except captions_ops.CaptionsError as ex:
        return sp.step_line("Auto Captions", "failed", str(ex))
    return sp.step_line("Auto Captions", "done", "{:d} caption line{:s}, {:s}".format(
        result["lines_created"], "" if result["lines_created"] == 1 else "s", result.get("style", style)))


def _hook(short: Any, pick: dict[str, Any], first: int, last: int) -> str:
    """The hook title: a text strip over the first three seconds, in the upper part of the frame."""
    text = sp.hook_text(pick)
    if not text:
        return sp.step_line("Hook Title", "skipped", "the pick has no hook line")
    ed = short.sequence_editor
    a, b = sp.hook_frames(_fps(short), first, last)
    channel = min(128, max((s.channel for s in ed.strips), default=0) + 1)
    strip = ed.strips.new_effect("Hook Title", 'TEXT', channel, a, length=max(1, b - a))
    strip.text = text
    h = short.render.resolution_y
    for key, value in (("font_size", max(12, int(h * 0.05))), ("color", (1.0, 1.0, 1.0, 1.0)),
                       ("use_outline", True), ("outline_color", (0.0, 0.0, 0.0, 1.0)), ("outline_width", 0.12),
                       ("use_bold", True), ("location", (0.5, 0.78)), ("anchor_x", "CENTER"), ("anchor_y", "CENTER"),
                       ("wrap_width", 0.86)):
        try:
            setattr(strip, key, value)
        except (AttributeError, TypeError, ValueError):
            pass                     # a Blender build without that property: the default stays
    strip.select = False
    return sp.step_line("Hook Title", "done", "\"{:s}\" over the first {:.0f} s".format(
        text, (b - a) / _fps(short)))


def output_preset(short: Any, name: str) -> str:
    """MP4, H.264 video, AAC sound, named after the short, next to the .blend.  Returns the report line."""
    r = short.render
    if hasattr(r.image_settings, "media_type"):          # Blender 5: video is a media type of its own first
        r.image_settings.media_type = 'VIDEO'
    r.image_settings.file_format = 'FFMPEG'
    ff = r.ffmpeg
    ff.format = 'MPEG4'
    ff.codec = 'H264'
    ff.audio_codec = 'AAC'
    for key, value in (("constant_rate_factor", 'MEDIUM'), ("ffmpeg_preset", 'GOOD'), ("audio_channels", 'STEREO'),
                       ("audio_mixrate", 48000), ("audio_bitrate", 192), ("pixel_format", 'YUV420P')):
        try:
            setattr(ff, key, value)
        except (AttributeError, TypeError, ValueError):
            pass
    r.use_file_extension = True
    file_name = sp.file_stem(name) + ".mp4"
    if bpy.data.filepath:
        r.filepath = "//" + file_name                    # relative: it follows the .blend when that is moved
        where = "next to the .blend"
    else:
        r.filepath = sp.output_path(tempfile.gettempdir(), name)
        where = "in the temp folder (the .blend is not saved yet: set the folder in Output properties)"
    return sp.step_line("Output", "done", "MP4, H.264 + AAC, {:s}, {:s}".format(file_name, where))


# -- the whole -------------------------------------------------------------------------------------

def build(scene: Any, pick: dict[str, Any], steps: dict[str, bool], window: Any = None) -> dict[str, Any]:
    """Make the short for *pick* (a stored pick that is placed, state ``ok``) from *scene*.

    *steps*: ``silences``, ``vertical``, ``captions``, ``hook``.  Returns ``{"scene", "first", "last", "lines"}``.
    Raises :class:`ShortError` (and leaves nothing behind) when the short can't be made.  No undo step of its own:
    the operator's is the one that removes it."""
    ed = scene.sequence_editor
    if ed is None:
        raise ShortError("This scene has no strips.")
    if len(ed.meta_stack):
        raise ShortError("Leave the meta strip first (Tab): a short is made from the top level of the edit.")
    before = ops.edit_signature(scene)
    try:
        got = scene_reader_ops.read(scene)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        raise ShortError("Couldn't read the edit's speech: {!s}".format(ex)) from ex
    prev_end, next_start = sp.neighbours(got["sentences"], pick["start"], pick["end"])
    first, last = sp.borders(pick["start"], pick["end"], prev_end, next_start, _fps(scene),
                             scene.frame_start, scene.frame_end)
    store = ops.load(scene)
    name = sp.short_name(sp.next_number([s.name for s in bpy.data.scenes]), pick["title"])
    short = reframe.copy_scene(scene, name, {sp.TAG_FROM: scene.name, sp.TAG_WORDS: sp.tag(scene.name, pick)})
    try:
        for key in (hp.PROP, reframe.VERTICAL_TAG):          # what a short does not inherit
            if key in short:
                del short[key]
        mine = set(store.get("markers") or [])
        for m in [m for m in short.timeline_markers if m.name in mine]:
            short.timeline_markers.remove(m)
        short.use_preview_range = False
        short.frame_start, short.frame_end, short.frame_current = first, last, first
        lines = ["Range: frames {:d}-{:d} of '{:s}', {:.0f} s ({:s} to {:s})".format(
            first, last, scene.name, (last - first + 1) / _fps(scene), hp.clock(first - scene.frame_start, _fps(scene)),
            hp.clock(last + 1 - scene.frame_start, _fps(scene)))]
        if steps.get("silences"):
            line, removed = _silences(short, first, last)
            last -= removed
            short.frame_end = last
            lines.append(line)
        if steps.get("vertical"):
            lines.append(_vertical(short, pick.get("kind", "")))
        if steps.get("captions"):
            lines.append(_captions(short, first, last))
        if steps.get("hook"):
            lines.append(_hook(short, pick, first, last))
        lines.append(output_preset(short, name))
    except Exception:
        _discard(short)
        raise
    if ops.edit_signature(scene) != before:
        lines.append("Warning: the long edit's strips changed while the short was made. Press Ctrl+Z.")
    _show(window, scene, short)
    return {"scene": short, "first": first, "last": last, "lines": lines}


def _discard(short: Any) -> None:
    try:
        bpy.data.scenes.remove(short)
    except (RuntimeError, ReferenceError):
        pass


def make_short(op: Any, scene: Any, pick: dict[str, Any]) -> set[str]:
    """What the Make Short button does (``highlight_picks_ops``): the steps are the operator's switches."""
    steps = {k: bool(getattr(op, k, True)) for k in ("silences", "vertical", "captions", "hook")}
    try:
        got = build(scene, pick, steps, getattr(bpy.context, "window", None))
    except ShortError as ex:
        op.report({'ERROR'}, str(ex))
        return {'CANCELLED'}
    except Exception as ex:  # pylint: disable=broad-exception-caught
        op.report({'ERROR'}, "Make Short stopped: {!s}".format(ex))
        return {'CANCELLED'}
    for line in got["lines"]:
        op.report({'WARNING' if "failed" in line or line.startswith("Warning") else 'INFO'}, line)
    ops._redraw()      # pylint: disable=protected-access
    return {'FINISHED'}
