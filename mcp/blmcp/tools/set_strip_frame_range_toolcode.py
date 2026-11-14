# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for moving / trimming a Video Sequencer strip (Tools 1, #304).

Uses the strip's frame properties (no operator, so no editor context is needed) and
pushes one named undo step.  A move onto another strip is refused before anything
changes: Blender itself would push the strip to another channel without saying so.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    strip_name: str
    start: int | None = None
    end: int | None = None
    channel: int | None = None
    trim: bool = False
    scene_name: str = ""


class Result(NamedTuple):
    status: str
    scene: str
    strip: str
    before: dict[str, int] | None
    after: dict[str, int] | None
    undo_step: bool
    message: str


# @include_begin: _template_vse_shared.py
from blmcp.tools._template_vse_shared import (  # noqa: E402  (the real helpers, inlined in Blender)
    _VSE_CHANNEL_MAX, _vse_conflicts, _vse_find_strip, _vse_free_channel, _vse_get, _vse_meta_name,
    _vse_not_found_message, _vse_plan_range, _vse_resolve_scene, _vse_rows, _vse_set, _vse_undo_push,
)
# @include_end


def _where(strip: Any) -> dict[str, int]:
    return {"start": int(_vse_get(strip, "start", 0)), "end": int(_vse_get(strip, "end", 0)),
            "channel": int(strip.channel)}


def _error(scene: str, name: str, message: str, before: dict[str, int] | None = None) -> Result:
    return Result(status="error", scene=scene, strip=name, before=before, after=before, undo_step=False,
                  message=message)


def _apply(strip: Any, new_start: int, new_end: int, channel: int, trim: bool) -> None:
    """Set the frames, then the channel (Blender may shuffle a strip that overlaps on the way)."""
    cur_start = int(_vse_get(strip, "start", 0))
    if trim:
        # Order so the strip never becomes empty half-way.
        if new_start < int(_vse_get(strip, "end", 0)):
            _vse_set(strip, "start", new_start)
            _vse_set(strip, "end", new_end)
        else:
            _vse_set(strip, "end", new_end)
            _vse_set(strip, "start", new_start)
    else:
        if new_start != cur_start:
            _vse_set(strip, "content_start", _vse_get(strip, "content_start", 0) + (new_start - cur_start))
        if int(_vse_get(strip, "end", 0)) != new_end:
            _vse_set(strip, "end", new_end)
    for _attempt in range(2):
        if int(strip.channel) == channel:
            break
        strip.channel = channel


def main(params: Params) -> Result:
    scene, error = _vse_resolve_scene(params.scene_name)
    name = (params.strip_name or "").strip()
    if scene is None:
        return _error("", name, error)
    if not name:
        return _error(scene.name, name, "Give strip_name (get_vse_strips_summary lists the strips).")
    if params.start is None and params.end is None and params.channel is None:
        return _error(scene.name, name, "Nothing to change: give start, end and/or channel.")
    rows = _vse_rows(scene)
    strip = _vse_find_strip(scene, name)
    if strip is None:
        return _error(scene.name, name, _vse_not_found_message(name, rows, scene.name, int(params.channel or 0)))
    before = _where(strip)
    if getattr(strip, "lock", False):
        return _error(scene.name, name, (
            "Strip '{:s}' is locked. Ask the user to unlock it (Strip > Lock/Mute), or leave it.".format(name)),
            before)
    channel = int(params.channel) if params.channel is not None else before["channel"]
    if not 1 <= channel <= _VSE_CHANNEL_MAX:
        return _error(scene.name, name, "channel must be 1-{:d}, not {:d}.".format(_VSE_CHANNEL_MAX, channel), before)
    new_start, new_end, error = _vse_plan_range(before["start"], before["end"], params.start, params.end,
                                                bool(params.trim))
    if error:
        return _error(scene.name, name, error, before)
    meta = _vse_meta_name(strip)
    conflicts = _vse_conflicts(rows, name, meta, channel, new_start, new_end)
    if conflicts:
        free = _vse_free_channel(rows, name, meta, new_start, new_end, channel)
        hint = (" Channel {:d} is free for those frames: pass channel={:d}, or move the other strip first."
                .format(free, free) if free else " No channel is free for those frames.")
        return _error(scene.name, name, (
            "Frames {:d}-{:d} on channel {:d} would overlap {:s}; nothing was changed.{:s}".format(
                new_start, new_end, channel,
                ", ".join("'{:s}' ({:d}-{:d})".format(c["name"], c["start"], c["end"]) for c in conflicts[:5]),
                hint)), before)
    if (new_start, new_end, channel) == (before["start"], before["end"], before["channel"]):
        return Result(status="ok", scene=scene.name, strip=name, before=before, after=before, undo_step=False,
                      message="Strip '{:s}' is already at frames {:d}-{:d} on channel {:d}.".format(
                          name, new_start, new_end, channel))
    try:
        _apply(strip, new_start, new_end, channel, bool(params.trim))
    except (AttributeError, RuntimeError, TypeError, ValueError) as ex:
        return _error(scene.name, name, "Blender refused the change: {:s}".format(str(ex)), _where(strip))
    after = _where(strip)
    verb = "Trimmed" if params.trim else ("Moved" if params.start is not None or params.channel is not None
                                          else "Set the end of")
    undo = _vse_undo_push("Coworker: {:s} strip '{:s}'".format(verb.lower(), name))
    want = {"start": new_start, "end": new_end, "channel": channel}
    if after != want:
        return Result(status="ok", scene=scene.name, strip=name, before=before, after=after, undo_step=undo, message=(
            "Asked for frames {:d}-{:d} on channel {:d}; Blender placed '{:s}' at {:d}-{:d} on channel {:d} "
            "(a strip's content or an effect's inputs can limit it). Undo puts it back.".format(
                new_start, new_end, channel, name, after["start"], after["end"], after["channel"])))
    return Result(status="ok", scene=scene.name, strip=name, before=before, after=after, undo_step=undo, message=(
        "{:s} '{:s}': frames {:d}-{:d} on channel {:d} (was {:d}-{:d} on {:d}).{:s}".format(
            verb, name, after["start"], after["end"], after["channel"], before["start"], before["end"],
            before["channel"], " One undo step." if undo else "")))
