# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Inline Video Sequencer helpers (Tools 1, #304), shared by the five ``*vse*`` tool-codes.
#
# Blender 5.2 renamed the strip frame properties; the old names still work there but
# warn ("expected to be removed in Blender 6.0"):
#
#   left_handle  (was frame_final_start)    right_handle (was frame_final_end, exclusive)
#   duration     (was frame_final_duration) content_start (was frame_start)
#   content_duration (was frame_duration)   left/right_handle_offset (was frame_offset_*)
#   content_trim_start/end (was animation_offset_start/end)
#
# The pure helpers below take plain rows (dicts) so they are tested without Blender.

__all__ = ()

import difflib
from typing import Any

# Rows a list returns unless the caller asks for more (and the most it ever returns).
_VSE_LIMIT_DEFAULT = 50
_VSE_LIMIT_MAX = 500
# Blender's channel range.
_VSE_CHANNEL_MAX = 128

# Strip frame properties: (5.2 name, older name).
_VSE_FRAME_NAMES = {
    "start": ("left_handle", "frame_final_start"),
    "end": ("right_handle", "frame_final_end"),
    "duration": ("duration", "frame_final_duration"),
    "content_start": ("content_start", "frame_start"),
    "content_duration": ("content_duration", "frame_duration"),
    "start_offset": ("left_handle_offset", "frame_offset_start"),
    "end_offset": ("right_handle_offset", "frame_offset_end"),
}


# -- Pure helpers (no bpy) -------------------------------------------------------------

def _vse_clamp_limit(limit: Any) -> int:
    """*limit* as a row count: 0 / bad -> the default, never above the maximum."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        value = _VSE_LIMIT_DEFAULT
    return min(value, _VSE_LIMIT_MAX)


def _vse_apply_limit(rows: list[Any], limit: Any) -> tuple[list[Any], int]:
    """The first *limit* rows and how many more there are."""
    n = _vse_clamp_limit(limit)
    return rows[:n], max(0, len(rows) - n)


def _vse_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Do two frame ranges overlap?  Ends are exclusive, as Blender's right handle."""
    return a_start < b_end and b_start < a_end


def _vse_filter_rows(
        rows: list[dict[str, Any]],
        channel: int = 0,
        strip_type: str = "",
        frame_start: int | None = None,
        frame_end: int | None = None,
        name_contains: str = "",
) -> list[dict[str, Any]]:
    """Rows on *channel* (0 = any), of *strip_type*, touching the frame range, with
    *name_contains* in their name (case-insensitive)."""
    kind = (strip_type or "").strip().upper()
    needle = (name_contains or "").strip().lower()
    lo = frame_start if frame_start is not None else None
    hi = frame_end if frame_end is not None else None
    out = []
    for row in rows:
        if channel and row["channel"] != channel:
            continue
        if kind and row["type"] != kind:
            continue
        if needle and needle not in row["name"].lower():
            continue
        if lo is not None and row["end"] <= lo:
            continue
        if hi is not None and row["start"] > hi:
            continue
        out.append(row)
    return out


def _vse_sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (r.get("meta", ""), r["channel"], r["start"], r["name"]))


def _vse_near_names(name: str, names: list[str], limit: int = 6) -> list[str]:
    """Strip names that look like *name*: same name in another case, containing it,
    then close spellings."""
    want = (name or "").strip().lower()
    out: list[str] = []
    for candidate in names:
        if candidate.lower() == want and candidate not in out:
            out.append(candidate)
    for candidate in names:
        low = candidate.lower()
        if want and (want in low or low in want) and candidate not in out:
            out.append(candidate)
    lowered = {c.lower(): c for c in names}
    for match in difflib.get_close_matches(want, list(lowered), n=limit, cutoff=0.5):
        if lowered[match] not in out:
            out.append(lowered[match])
    return out[:limit]


def _vse_not_found_message(name: str, rows: list[dict[str, Any]], scene: str, channel: int = 0) -> str:
    """Say what to do when no strip is called *name*: near names, and what is on the channel."""
    if not rows:
        return ("Scene '{:s}' has no strips in the Sequencer. Add strips first, or pass scene_name "
                "for another scene.".format(scene))
    names = [r["name"] for r in rows]
    parts = ["No strip named '{:s}' in scene '{:s}'.".format(name, scene)]
    near = _vse_near_names(name, names)
    if near:
        parts.append("Did you mean: {:s}?".format(", ".join("'{:s}'".format(n) for n in near)))
    if channel:
        on = [r["name"] for r in rows if r["channel"] == channel]
        parts.append("Strips on channel {:d}: {:s}.".format(
            channel, ", ".join("'{:s}'".format(n) for n in on[:10]) if on else "none"))
    elif not near:
        shown = names[:10]
        parts.append("Strips: {:s}{:s}.".format(
            ", ".join("'{:s}'".format(n) for n in shown), ", ..." if len(names) > len(shown) else ""))
    parts.append("Names are case-sensitive; get_vse_strips_summary lists them all.")
    return " ".join(parts)


def _vse_overlaps(rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    """Strips on the same channel (and meta level) whose ranges overlap."""
    out = []
    by_channel: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        by_channel.setdefault((row.get("meta", ""), row["channel"]), []).append(row)
    for (_meta, channel), items in sorted(by_channel.items()):
        items = sorted(items, key=lambda r: r["start"])
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if b["start"] >= a["end"]:
                    break
                out.append({"channel": channel, "a": a["name"], "b": b["name"],
                            "frames": [max(a["start"], b["start"]), min(a["end"], b["end"])]})
                if len(out) >= limit:
                    return out
    return out


def _vse_gaps(rows: list[dict[str, Any]], frame_start: int, frame_end: int, limit: int = 20) -> list[list[int]]:
    """Frame ranges ``[first, last]`` inside the scene range where no strip plays (black frames).
    Muted strips and sound strips don't count as picture; strips inside metas are covered by their meta."""
    spans = sorted((r["start"], r["end"]) for r in rows
                   if not r.get("meta") and not r.get("mute") and r["type"] != "SOUND")
    gaps: list[list[int]] = []
    cursor = frame_start
    for start, end in spans:
        if start > cursor and cursor <= frame_end:
            gaps.append([cursor, min(start, frame_end + 1) - 1])
        cursor = max(cursor, end)
        if cursor > frame_end:
            break
    if cursor <= frame_end:
        gaps.append([cursor, frame_end])
    return [g for g in gaps if g[1] >= g[0]][:limit]


def _vse_plan_range(
        cur_start: int, cur_end: int,
        start: int | None, end: int | None, trim: bool,
) -> tuple[int, int, str]:
    """Where a strip goes: ``(new_start, new_end, error)``.

    Without *trim*, *start* moves the whole strip (its length is kept) and *end* then sets its
    last frame.  With *trim*, *start* moves only the left handle (the content stays put).
    *end* is exclusive, like Blender's right handle.
    """
    new_start, new_end = cur_start, cur_end
    if start is not None:
        if trim:
            new_start = int(start)
        else:
            new_start = int(start)
            new_end = cur_end + (new_start - cur_start)
    if end is not None:
        new_end = int(end)
    if new_end <= new_start:
        return new_start, new_end, (
            "end ({:d}) must be after start ({:d}): the strip would be empty. End is exclusive "
            "(a strip on frames 1-10 has start=1, end=11).".format(new_end, new_start))
    return new_start, new_end, ""


def _vse_conflicts(rows: list[dict[str, Any]], name: str, meta: str, channel: int,
                   start: int, end: int) -> list[dict[str, Any]]:
    """Other strips on *channel* (same meta level) that the range would overlap."""
    return [r for r in rows if r["name"] != name and r.get("meta", "") == meta
            and r["channel"] == channel and _vse_overlap(start, end, r["start"], r["end"])]


def _vse_free_channel(rows: list[dict[str, Any]], name: str, meta: str, start: int, end: int,
                      near: int) -> int:
    """The channel nearest *near* (above first, as Blender shuffles) where the range is free; 0 if none."""
    for step in range(0, _VSE_CHANNEL_MAX):
        for channel in (near + step, near - step):
            if 1 <= channel <= _VSE_CHANNEL_MAX and not _vse_conflicts(rows, name, meta, channel, start, end):
                return channel
    return 0


# -- Blender helpers (import bpy inside) -------------------------------------------------

def _vse_get(strip: Any, key: str, default: Any = None) -> Any:
    """A frame property by its 5.2 name, else the older name."""
    new, old = _VSE_FRAME_NAMES[key]
    if hasattr(strip, new):
        return getattr(strip, new)
    return getattr(strip, old, default)


def _vse_set(strip: Any, key: str, value: Any) -> None:
    new, old = _VSE_FRAME_NAMES[key]
    setattr(strip, new if hasattr(strip, new) else old, value)


def _vse_meta_name(strip: Any) -> str:
    try:
        parent = strip.parent_meta()
    except (AttributeError, RuntimeError, TypeError):
        return ""
    return parent.name if parent is not None else ""


def _vse_row(strip: Any) -> dict[str, Any]:
    """A strip as a plain summary row."""
    return {
        "name": strip.name,
        "type": str(strip.type),
        "channel": int(strip.channel),
        "start": int(_vse_get(strip, "start", 0)),
        "end": int(_vse_get(strip, "end", 0)),
        "duration": int(_vse_get(strip, "duration", 0)),
        "mute": bool(getattr(strip, "mute", False)),
        "lock": bool(getattr(strip, "lock", False)),
        "select": bool(getattr(strip, "select", False)),
        "meta": _vse_meta_name(strip),
    }


def _vse_rows(scene: Any) -> list[dict[str, Any]]:
    ed = scene.sequence_editor
    if ed is None:
        return []
    return [_vse_row(s) for s in _vse_all_strips(ed)]


def _vse_all_strips(ed: Any) -> list[Any]:
    """Every strip, metas' contents included (``strips_all``; ``sequences_all`` before 5.0)."""
    seq = getattr(ed, "strips_all", None)
    if seq is None:
        seq = getattr(ed, "sequences_all", [])
    return list(seq)


def _vse_top_strips(ed: Any) -> list[Any]:
    seq = getattr(ed, "strips", None)
    if seq is None:
        seq = getattr(ed, "sequences", [])
    return list(seq)


def _vse_shown_scene() -> Any:
    """The scene the Sequencer editor shows (5.x: the workspace's sequencer scene), or None."""
    import bpy  # pylint: disable=import-error,no-name-in-module
    scene = getattr(bpy.context, "sequencer_scene", None)
    if scene is not None:
        return scene
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm and wm.windows else None)
    workspace = getattr(window, "workspace", None) if window else None
    return getattr(workspace, "sequencer_scene", None) if workspace else None


def _vse_resolve_scene(scene_name: str = "") -> tuple[Any, str]:
    """``(scene, error)``: *scene_name*, else the scene the Sequencer shows, else the active scene.

    When the Sequencer shows a scene without strips and the active scene has some, the active
    scene wins (strips added from Python land there)."""
    import bpy  # pylint: disable=import-error,no-name-in-module
    if scene_name:
        scene = bpy.data.scenes.get(scene_name)
        if scene is None:
            return None, "No scene named '{:s}'. Scenes: {:s}.".format(
                scene_name, ", ".join("'{:s}'".format(s.name) for s in bpy.data.scenes))
        return scene, ""
    shown = _vse_shown_scene()
    active = bpy.context.scene
    if active is None:
        wm = bpy.context.window_manager
        active = wm.windows[0].scene if wm and wm.windows else None
    for scene in (shown, active):
        if scene is not None and scene.sequence_editor is not None and _vse_all_strips(scene.sequence_editor):
            return scene, ""
    scene = shown or active
    if scene is None:
        return None, "No scene to read (no window and no active scene)."
    return scene, ""


def _vse_find_strip(scene: Any, name: str) -> Any:
    ed = scene.sequence_editor
    if ed is None:
        return None
    for strip in _vse_all_strips(ed):
        if strip.name == name:
            return strip
    return None


def _vse_undo_push(message: str) -> bool:
    """One named undo step (in a window's context when there is one)."""
    import bpy  # pylint: disable=import-error,no-name-in-module
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm and wm.windows else None)
    try:
        if window is not None:
            with bpy.context.temp_override(window=window):
                bpy.ops.ed.undo_push(message=message)
        else:
            bpy.ops.ed.undo_push(message=message)
        return True
    except (RuntimeError, AttributeError):
        return False
