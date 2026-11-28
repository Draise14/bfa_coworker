# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Reframe layouts on strips (#606): build a layout on the picture strips asked for, replacing the
earlier one, swap a split's two crops, and put a part back to its default crop.  Free of ``bpy``:
what needs Blender (strip duplicates, the Reframe modifier, removing strips with the prefetch
stopped, Follow Subject) is a *backend* object the caller passes; the geometry is
``reframe_layout``.

Every picture strip carries the layout it is in as a tag (``coworker_layout``): ``layout`` (the
id), ``part`` (0 = the strip itself, the top part; 1 = its duplicate, the bottom one), ``group``
(the name of the part-0 strip when the layout was made) and ``partner`` (the other part's name).
A strip without a tag but with the Reframe modifier is Make Vertical's: Full Frame, or Blur Fill.

Changing a layout always starts from a clean strip: the partner strip is removed, the Reframe
modifier and its Follow Subject keys, the crop and the transform are cleared, then the new layout
is built.  So going through every layout and back leaves exactly what the last one gives directly.

A strip reference is never kept across a call that adds or removes strips (a freed strip read
from Python crashes Blender): groups are collected as names and each strip is looked up again.

A cut (Remove Silences, Edit by Transcript) cuts every strip at the level alike, so the two parts
are cut together; the pieces keep the tag, and a piece's partner is the strip of the same group,
the other part, over the same frames (:func:`find_partner`), not the name it had before the cut.
"""

from __future__ import annotations

__all__ = (
    "PICTURE_TYPES",
    "TAG",
    "Backend",
    "LayoutError",
    "apply_layout",
    "current_layout",
    "find_partner",
    "free_channel",
    "is_split_part",
    "layouts_of",
    "partners_of",
    "plan_channel",
    "reset_framing",
    "swap_parts",
    "tag_of",
)

from typing import Any, Callable

from . import reframe_layout as rl

TAG = "coworker_layout"
PICTURE_TYPES = frozenset({"MOVIE", "IMAGE", "SCENE"})
PARTNER_SUFFIX = " (bottom)"
MAX_CHANNEL = 128
NEEDS_SUBJECTS = "needs subject finding (coming)"


class LayoutError(Exception):
    """A reason the layout can't be built or can't start (shown to the artist as it is)."""


class Backend:
    """What the Blender side provides.  The methods raise ``LayoutError`` with the reason on failure."""

    def source_size(self, strip: Any) -> tuple[int, int]:
        raise NotImplementedError

    def canvas_size(self, scene: Any) -> tuple[int, int]:
        raise NotImplementedError

    def duplicate(self, strips: Any, strip: Any, name: str, channel: int) -> Any:
        """A second strip of the same source and timing on *channel* (no audio)."""
        raise NotImplementedError

    def remove(self, strips: Any, doomed: list) -> None:
        raise NotImplementedError

    def shift_channels(self, strips: Any, first: int) -> None:
        """Move every strip on channel *first* and above up by one channel (top one first), the whole level."""
        raise NotImplementedError

    def split_problem(self, strip: Any) -> str:
        """Why *strip* can't be copied exactly to a second part ("" when it can)."""
        return ""

    def align(self, strips: Any, top: Any, partner: Any) -> None:
        """Put *partner* over exactly the frames of *top* (content start and both handles)."""
        raise NotImplementedError

    def modifier_state(self, strip: Any) -> dict | None:
        """``None`` without the Reframe modifier, else ``{"blur": bool, "keyed": bool}``."""
        raise NotImplementedError

    def clear_modifier(self, scene: Any, strip: Any) -> None:
        """Take the Reframe modifier and its keys off *strip*."""
        raise NotImplementedError

    def make_full(self, scene: Any, strip: Any, mode: str) -> None:
        """Give *strip* a fresh Reframe modifier in *mode* (``CROP`` / ``BLUR_FILL``)."""
        raise NotImplementedError

    def reset_full(self, scene: Any, strip: Any) -> None:
        """Back to the centred framing, keys removed (the modifier's Reset Framing)."""
        raise NotImplementedError

    def start_tracking(self, scene: Any, strips: list) -> str:
        """Start Follow Subject on *strips*; the status line, or ``LayoutError`` with the reason."""
        raise NotImplementedError

    def clear_keys(self, scene: Any, strip: Any) -> None:
        """Take the crop and transform keys (a Track All part's) off *strip*; nothing to do where none are made."""


# -- tags ----------------------------------------------------------------------------------

def tag_of(strip: Any) -> dict | None:
    """The strip's layout tag as a plain dict, or ``None``."""
    try:
        raw = strip.get(TAG)
    except (AttributeError, TypeError):
        return None
    if raw is None or not hasattr(raw, "get"):
        return None
    layout_id = str(raw.get("layout", ""))
    if not layout_id:
        return None
    return {"layout": layout_id, "part": int(raw.get("part", 0) or 0), "group": str(raw.get("group", "")),
            "partner": str(raw.get("partner", ""))}


def _write_tag(strip: Any, layout_id: str, part: int, group: str, partner: str) -> None:
    strip[TAG] = {"layout": layout_id, "part": int(part), "group": group, "partner": partner}


def _clear_tag(strip: Any) -> None:
    try:
        del strip[TAG]
    except (KeyError, TypeError, AttributeError):
        pass


def _span(strip: Any) -> tuple[int, int]:
    return int(strip.left_handle), int(strip.right_handle)


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return min(a[1], b[1]) - max(a[0], b[0])


def _is_split(layout_id: str) -> bool:
    try:
        return bool(rl.layout(layout_id).parts[1:])
    except KeyError:
        return False


def is_split_part(strip: Any) -> bool:
    """True when *strip* carries a split layout's tag (Make Vertical and the old Reframe entries leave it alone)."""
    tag = tag_of(strip)
    return tag is not None and _is_split(tag["layout"])


def partners_of(strips: Any, strip: Any) -> list:
    """The other part(s) of *strip*'s split, best first: the strip of the same group and the other part over
    exactly the same frames; else one overlapping it in time (the artist trimmed a part, or dragged the top part
    and its sound but not the duplicate); else the one whose name the tag stores.  A strip that is exactly the
    partner of another one is not taken."""
    tag = tag_of(strip)
    if tag is None or not _is_split(tag["layout"]):
        return []
    span = _span(strip)
    peers = []
    for other in strips:
        if other is strip or other.name == strip.name:
            continue
        other_tag = tag_of(other)
        if other_tag is not None and other_tag["group"] == tag["group"] and other_tag["part"] != tag["part"]                 and _is_split(other_tag["layout"]):
            peers.append((other, other_tag))

    def exact_elsewhere(candidate: Any, candidate_tag: dict) -> bool:
        return any(o is not strip and o.name != strip.name and o is not candidate
                   and tag_of(o) is not None and tag_of(o)["group"] == tag["group"]
                   and tag_of(o)["part"] != candidate_tag["part"] and _span(o) == _span(candidate)
                   for o in strips)

    ranked = []
    for other, other_tag in peers:
        over = _overlap(span, _span(other))
        if _span(other) == span:
            ranked.append((0, 0, other))
        elif exact_elsewhere(other, other_tag):
            continue
        elif over > 0:
            ranked.append((1, -over, other))
        elif other.name == tag["partner"]:
            ranked.append((2, 0, other))
    ranked.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in ranked]


def find_partner(strips: Any, strip: Any) -> Any:
    """The other part of *strip*'s split (:func:`partners_of`'s first), or ``None``."""
    found = partners_of(strips, strip)
    return found[0] if found else None


def current_layout(strip: Any, backend: Backend) -> str:
    """The layout *strip* is in: a split's tag, else what its Reframe modifier shows (Full Frame, Blur Fill, or
    Tracked once Offset X is keyed), else ``""`` (a tag whose modifier the artist removed counts for nothing)."""
    tag = tag_of(strip)
    if tag is not None and _is_split(tag["layout"]):
        return tag["layout"]
    state = backend.modifier_state(strip)
    if state is None:
        return ""
    return "full_blur" if state.get("blur") else ("full_tracked" if state.get("keyed") else "full")


def plan_channel(strips: Any, strip: Any, ignore: Any = ()) -> tuple[int, bool]:
    """``(channel, shift)`` for the second part of *strip*'s split.  Opaque and above a caption it would hide
    it, so it goes below every text strip that overlaps the clip in time: on a free channel over the clip's
    frames above *strip*'s, else below it; with none, *shift* is True: every strip from that channel up moves up
    by one first (:meth:`Backend.shift_channels`) and the part takes the freed one.  ``LayoutError`` when the
    128 channels leave no room."""
    left, right = _span(strip)
    others = [o for o in strips if o is not strip and o.name != strip.name and o.name not in ignore
              and _span(o)[0] < right and left < _span(o)[1]]
    texts = [o.channel for o in others if o.type == "TEXT"]
    ceiling = min(texts) if texts else MAX_CHANNEL + 1
    taken = {o.channel for o in others}
    for channel in range(int(strip.channel) + 1, min(ceiling, MAX_CHANNEL + 1)):
        if channel not in taken:
            return channel, False
    for channel in range(min(int(strip.channel), ceiling) - 1, 0, -1):
        if channel not in taken:
            return channel, False
    insert = min(int(strip.channel) + 1, ceiling)
    for o in strips:
        if int(o.channel) >= insert and getattr(o, "lock", False) and o.name not in ignore:
            raise LayoutError("'{:s}' is locked and in the way of the second part of '{:s}': unlock it first".format(
                o.name, strip.name))
    highest = max((int(o.channel) for o in strips if o.name not in ignore), default=0)
    if highest + 1 > MAX_CHANNEL or insert > MAX_CHANNEL:
        raise LayoutError("There is no room for the second part of '{:s}': all {:d} channels are in use".format(
            strip.name, MAX_CHANNEL))
    return insert, True


def free_channel(strips: Any, strip: Any) -> int:
    """The channel :func:`plan_channel` finds without moving anything (``LayoutError`` when it would have to)."""
    channel, shift = plan_channel(strips, strip)
    if shift:
        raise LayoutError("No free channel for the second part of '{:s}'".format(strip.name))
    return channel


def _by_name(strips: Any) -> dict[str, Any]:
    return {s.name: s for s in strips}


def _partner_name(base: str, used: set[str]) -> str:
    stem = base[:63 - len(PARTNER_SUFFIX)]
    name = stem + PARTNER_SUFFIX
    n = 0
    while name in used:
        n += 1
        tail = " (bottom).{:03d}".format(n)
        name = base[:63 - len(tail)] + tail
    return name


# -- strip numbers ---------------------------------------------------------------------------

def _set_part(strip: Any, part: rl.Part) -> None:
    strip.transform.scale_x = strip.transform.scale_y = part.scale
    strip.transform.offset_x, strip.transform.offset_y = part.offset
    _no_rotation(strip)
    strip.crop.min_x, strip.crop.max_x, strip.crop.min_y, strip.crop.max_y = part.crop


def set_part(strip: Any, part: rl.Part) -> None:
    """Give *strip* the crop, scale and offset of *part* (Make Vertical's plain Centre Crop uses it too, #632)."""
    _set_part(strip, part)


def _no_rotation(strip: Any) -> None:
    t = strip.transform
    t.rotation = 0.0
    try:
        t.origin = (0.5, 0.5)
    except (AttributeError, TypeError):
        pass


def _neutral(strip: Any) -> None:
    t = strip.transform
    t.offset_x = t.offset_y = 0.0
    t.scale_x = t.scale_y = 1.0
    _no_rotation(strip)
    strip.crop.min_x = strip.crop.max_x = strip.crop.min_y = strip.crop.max_y = 0


def _problem(strip: Any) -> str:
    """Why *strip* can't be given a layout ("" when it can)."""
    if strip.type not in PICTURE_TYPES:
        return "is not a movie, image or scene strip"
    if getattr(strip, "lock", False):
        return "is locked"
    if len(getattr(strip, "retiming_keys", ()) or ()):
        return "is retimed (its speed changed): reset its retiming first"
    return ""


def _groups(strips: Any, names: list[str], skipped: list[dict]) -> list[tuple[str, str]]:
    """``[(base name, partner name or "")]`` of the picture strips asked for; a part stands for its group."""
    found = _by_name(strips)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in names:
        strip = found.get(name)
        if strip is None:
            skipped.append({"strip": name, "reason": "no strip of that name here"})
            continue
        why = _problem(strip)
        if why:
            skipped.append({"strip": name, "reason": why})
            continue
        tag = tag_of(strip)
        base, partner = strip, None
        if tag is not None:
            other = find_partner(strips, strip)
            if other is not None:
                base, partner = (strip, other) if tag["part"] == 0 else (other, strip)
        if base.name in seen:
            continue
        seen.add(base.name)
        if partner is not None:
            seen.add(partner.name)
        out.append((base.name, partner.name if partner is not None else ""))
    return out


# -- build -----------------------------------------------------------------------------------

def _clean(scene: Any, strips: Any, base_name: str, backend: Backend, removed: list[str]) -> None:
    """Take everything the earlier layout made off the group (every other part over the strip's frames, the
    modifier and its keys, crop, transform, tag), leaving the base strip as it was before any layout."""
    base = _by_name(strips)[base_name]
    doomed = partners_of(strips, base)
    if doomed:
        names = [d.name for d in doomed]
        backend.remove(strips, doomed)
        removed.extend(names)
    base = _by_name(strips)[base_name]               # looked up again: the removal above freed strips
    backend.clear_modifier(scene, base)
    backend.clear_keys(scene, base)
    _neutral(base)
    _clear_tag(base)


def _build(scene: Any, strips: Any, base_name: str, layout_id: str, backend: Backend, created: list[str]) -> bool:
    """Build *layout_id* on the clean strip *base_name*; True when it is a tracked layout to start."""
    item = rl.layout(layout_id)
    base = _by_name(strips)[base_name]
    sw, sh = backend.source_size(base)
    fw, fh = backend.canvas_size(scene)
    parts = rl.plan(layout_id, sw, sh, fw, fh).parts
    if item.modifier:
        backend.make_full(scene, base, item.modifier)
        # The tag never says Tracked: that is read from the keys, once they exist.
        _write_tag(base, "full_blur" if item.modifier == "BLUR_FILL" else "full", 0, base_name, "")
        return item.tracked
    channel, shift = plan_channel(strips, base)
    if shift:
        backend.shift_channels(strips, channel)
        base = _by_name(strips)[base_name]
    twin_name = _partner_name(base_name, {s.name for s in strips})
    twin = backend.duplicate(strips, base, twin_name, channel)    # adds a strip: `base` is looked up again below
    twin_name = twin.name
    _set_part(twin, parts[1])
    _write_tag(twin, layout_id, 1, base_name, base_name)
    created.append(twin_name)
    twin.select = True
    base = _by_name(strips)[base_name]
    base.select = True
    _set_part(base, parts[0])
    _write_tag(base, layout_id, 0, base_name, twin_name)
    return item.tracked


def _summary(layout_id: str, done: list[str], created: list[str], skipped: list[dict], note: str) -> str:
    item = rl.layout(layout_id)
    text = "{:s}: {:d} strip{:s}".format(item.label, len(done), "" if len(done) == 1 else "s")
    if created:
        text += ", {:d} second part{:s} made".format(len(created), "" if len(created) == 1 else "s")
    if skipped:
        text += "; skipped " + ", ".join("'{:s}' ({:s})".format(s["strip"], s["reason"]) for s in skipped[:4])
    if note:
        text += ". " + note
    return text


def apply_layout(scene: Any, strips: Any, layout_id: str, names: list[str], backend: Backend,
                 on_step: Callable[[], None] | None = None) -> dict[str, Any]:
    """Give the picture strips *names* (of the level *strips*) the layout *layout_id*.

    ``status``: ``ok``, ``nothing`` (nothing usable, nothing changed) or ``error``.  The answer has
    ``changed`` (the strips that got the layout), ``created`` and ``removed`` (strip names), ``skipped``
    (``{"strip", "reason"}``), ``unchanged`` (already in that layout) and a ``message``."""
    try:
        item = rl.layout(layout_id)
    except KeyError:
        return {"status": "error", "message": "Unknown layout '{!s}'. Use {:s}.".format(
            layout_id, ", ".join(rl.buildable()))}
    if not item.ready:
        return {"status": "error", "message": "{:s} {:s}.".format(item.label, NEEDS_SUBJECTS)}
    skipped: list[dict] = []
    groups = _groups(strips, [str(n) for n in names], skipped)
    if not groups:
        why = "; ".join("'{:s}' {:s}".format(s["strip"], s["reason"]) for s in skipped[:3])
        return {"status": "nothing", "changed": [], "created": [], "removed": [], "skipped": skipped,
                "unchanged": [], "message": "Nothing to reframe: select movie, image or scene strips" +
                                            (" ({:s})".format(why) if why else "")}
    done: list[str] = []
    unchanged: list[str] = []
    created: list[str] = []
    removed: list[str] = []
    track: list[str] = []
    todo = []
    for base_name, partner_name in groups:
        base = _by_name(strips)[base_name]
        now = current_layout(base, backend)
        intact = bool(partner_name) or not item.parts[1:]
        if now == layout_id and intact and not item.tracked:     # already so; Tracked runs again
            unchanged.append(base_name)
        else:
            todo.append(base_name)
    if item.parts[1:]:                  # a second part that can be copied exactly, and room for it: before any change
        for base_name in list(todo):
            why = backend.split_problem(_by_name(strips)[base_name])
            if why:
                skipped.append({"strip": base_name, "reason": why})
                todo.remove(base_name)
        if not todo and not unchanged:
            return {"status": "nothing", "changed": [], "created": [], "removed": [], "skipped": skipped,
                    "unchanged": [], "message": "Nothing to reframe: " + "; ".join(
                        "'{:s}' {:s}".format(x["strip"], x["reason"]) for x in skipped[:3])}
        try:
            for base_name in todo:
                base = _by_name(strips)[base_name]
                plan_channel(strips, base, ignore={p.name for p in partners_of(strips, base)})
        except LayoutError as ex:
            return {"status": "error", "changed": [], "created": [], "removed": [], "skipped": skipped,
                    "unchanged": unchanged, "message": "Nothing changed: {!s}".format(ex)}
    for base_name in todo:
        try:
            _clean(scene, strips, base_name, backend, removed)
            if _build(scene, strips, base_name, layout_id, backend, created):
                track.append(base_name)
        except LayoutError as ex:
            return {"status": "error", "changed": done, "created": created, "removed": removed, "skipped": skipped,
                    "unchanged": unchanged, "partial": True,
                    "message": "{!s}. Stopped part-way: Ctrl+Z puts everything back".format(ex)}
        done.append(base_name)
        if on_step is not None:
            on_step()
    note = ""
    if track:
        try:
            status = backend.start_tracking(scene, [_by_name(strips)[n] for n in track])
            note = status
        except LayoutError as ex:
            note = "Tracking did not start ({!s}): the strips stay Full Frame".format(ex)
    message = _summary(layout_id, done, created, skipped, note)
    if unchanged and not done:
        message = "{:s}: already in this layout ({:s})".format(item.label, ", ".join(unchanged[:4]))
    return {"status": "ok", "layout": layout_id, "changed": done, "created": created, "removed": removed,
            "skipped": skipped, "unchanged": unchanged, "tracking": note, "message": message}


# -- swap and reset --------------------------------------------------------------------------

def _rect_of(layout_id: str, index: int, fw: int, fh: int) -> rl.Rect:
    return rl.part_rects(layout_id, fw, fh)[index]


def _framing(strip: Any, layout_id: str, index: int, sw: int, sh: int, fw: int, fh: int) -> rl.Framing:
    t = strip.transform
    return rl.framing_of(_rect_of(layout_id, index, fw, fh), float(t.scale_x), (float(t.offset_x), float(t.offset_y)),
                         sw, sh, fw, fh)


def _show(strip: Any, layout_id: str, index: int, framing: rl.Framing, sw: int, sh: int, fw: int, fh: int) -> None:
    _set_part(strip, rl.part_for(index, _rect_of(layout_id, index, fw, fh), sw, sh, fw, fh, framing))


def _split_pairs(strips: Any, names: list[str], skipped: list[dict]) -> list[tuple[str, str]]:
    pairs = []
    for base_name, partner_name in _groups(strips, names, skipped):
        base = _by_name(strips).get(base_name)
        tag = tag_of(base) if base is not None else None
        if partner_name and tag is not None and rl.layout(tag["layout"]).parts[1:]:
            pairs.append((base_name, partner_name))
        else:
            skipped.append({"strip": base_name, "reason": "is not in a split layout"})
    return pairs


def swap_parts(scene: Any, strips: Any, names: list[str], backend: Backend) -> dict[str, Any]:
    """Exchange which framing of the picture is shown top and bottom in each split among *names*."""
    skipped: list[dict] = []
    pairs = _split_pairs(strips, [str(n) for n in names], skipped)
    if not pairs:
        return {"status": "nothing", "changed": [], "skipped": skipped,
                "message": "Nothing to swap: select the strips of a split (Split, Half and Half and the like)"}
    tracked = [b for b, _p in pairs if tag_of(_by_name(strips)[b])["layout"] == "track_all"]       # type: ignore[index]
    if tracked:                          # the parts are keyed to their subjects: a swap would leave stale keys
        return {"status": "nothing", "changed": [], "skipped": skipped,
                "message": "Swap is not available for Track All ({:s}): run Track All again, or Reset a part and use a "
                           "plain split".format(", ".join(tracked[:3]))}
    fw, fh = backend.canvas_size(scene)
    done = []
    for base_name, partner_name in pairs:
        found = _by_name(strips)
        top, bottom = found[base_name], found[partner_name]
        if _span(top) != _span(bottom):                         # the artist moved or trimmed one: back in step
            backend.align(strips, top, bottom)
        layout_id = tag_of(top)["layout"]                       # type: ignore[index]
        sw, sh = backend.source_size(top)
        f_top = _framing(top, layout_id, 0, sw, sh, fw, fh)
        f_bottom = _framing(bottom, layout_id, 1, sw, sh, fw, fh)
        _show(top, layout_id, 0, f_bottom, sw, sh, fw, fh)
        _show(bottom, layout_id, 1, f_top, sw, sh, fw, fh)
        done.append(base_name)
    return {"status": "ok", "changed": done, "skipped": skipped,
            "message": "Swapped top and bottom in {:d} split{:s}".format(len(done), "" if len(done) == 1 else "s")}


def reset_framing(scene: Any, strips: Any, names: list[str], backend: Backend) -> dict[str, Any]:
    """Put each strip named back to the default framing of its part (a Full Frame strip: the centred
    framing of its Reframe modifier, Follow Subject keys removed).  A split's partner is not touched
    unless it is named too."""
    found = _by_name(strips)
    fw, fh = backend.canvas_size(scene)
    done: list[str] = []
    skipped: list[dict] = []
    for name in [str(n) for n in names]:
        strip = found.get(name)
        if strip is None:
            skipped.append({"strip": name, "reason": "no strip of that name here"})
            continue
        why = _problem(strip)
        if why:
            skipped.append({"strip": name, "reason": why})
            continue
        tag = tag_of(strip)
        if tag is not None and rl.layout(tag["layout"]).parts[1:]:
            sw, sh = backend.source_size(strip)
            backend.clear_keys(scene, strip)                    # a Track All part: its keys go, then the default
            _show(strip, tag["layout"], tag["part"], rl.DEFAULT_FRAMING, sw, sh, fw, fh)
            done.append(name)
        elif backend.modifier_state(strip) is not None:
            backend.reset_full(scene, strip)
            done.append(name)
        else:
            skipped.append({"strip": name, "reason": "has no layout yet"})
    if not done:
        return {"status": "nothing", "changed": [], "skipped": skipped,
                "message": "Nothing to reset: select strips that have a layout"}
    return {"status": "ok", "changed": done, "skipped": skipped,
            "message": "Reset the framing of {:d} strip{:s}".format(len(done), "" if len(done) == 1 else "s")}


def layouts_of(scene: Any, strips: Any, names: list[str], backend: Backend) -> dict[str, Any]:
    """What the agent reads: for each picture strip named, its layout (``""``: none) and, in a split, which part
    it is and its partner."""
    found = _by_name(strips)
    out: dict[str, Any] = {}
    for name in names:
        strip = found.get(name)
        if strip is None or strip.type not in PICTURE_TYPES:
            continue
        row: dict[str, Any] = {"layout": current_layout(strip, backend)}
        tag = tag_of(strip)
        if tag is not None and _is_split(tag["layout"]):
            partner = find_partner(strips, strip)
            row.update(part=tag["part"], partner=partner.name if partner is not None else "")
        out[name] = row
    return out
