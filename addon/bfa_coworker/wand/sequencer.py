# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Remove Silences, the first Coworker operator (Tier 4m, Wand 0, #331).

``coworker.sequencer_remove_silences`` cuts the quiet parts out of the selected
sound strips -- and out of the picture strips that go with them, so picture and
sound stay in sync -- and closes the gaps.  No engine is needed.

* **Gather**: the selected sound strips (a selected movie brings its sound), each
  with its partners (``silences.is_partner``).  Locked and retimed strips are
  left out and said so.
* **Read**: the sound is read with ``aud`` (mono, resampled to 8 kHz, in 20 s
  chunks so a long file never sits in memory whole) into a loudness envelope of
  10 ms windows, cached per file; the window cursor and the status bar show the
  progress.
* **Preview**: the dialog shows the settings and a live line ("12 silences,
  0:41 removed").
* **Apply**: data-level ``Strip.split`` + ``strips.remove`` (no operator context
  needed), then the ripple moves what follows left; one undo step.

Places (``SPEC.places``): the end of the Sequencer's *Strip* menu, the
*Coworker* tab of Bforartists' tabbed toolshelf (only where
``show_toolshelf_tabs`` exists and is on) and the *Coworker* tab of the
Sequencer's sidebar (Blender, and Bforartists with the toolshelf tabs off).
"""

from __future__ import annotations

__all__ = (
    "CATEGORY",
    "CHUNK_S",
    "COWORKER_OT_sequencer_remove_silences",
    "COWORKER_PT_sequencer_sidebar",
    "COWORKER_PT_sequencer_toolshelf",
    "CoworkerEntry",
    "PANEL",
    "RATE",
    "SPEC",
    "WINDOW_S",
    "register",
    "unregister",
)

import os
import tempfile
from typing import Any, Callable

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, FloatProperty  # pylint: disable=import-error
from bpy.types import Operator, Panel  # pylint: disable=import-error

from . import icons, silences
from .spec import OperatorSpec, Param, Place

CATEGORY = "Coworker"
PANEL = "Video"

SPEC = OperatorSpec(
    id="sequencer.remove_silences",
    label="Remove Silences",
    description=("Cut the quiet parts out of the selected sound strips, and out of the picture "
                 "that goes with them, and close the gaps"),
    icon="remove_silences",
    fallback_icon="SPEAKER",
    places=(Place.menu("SEQUENCER_MT_strip", after="sequencer.split"),
            Place.toolshelf("SEQUENCE_EDITOR", CATEGORY, PANEL),
            Place.sidebar("SEQUENCE_EDITOR", CATEGORY, PANEL)),
    kind="tool", model="none", scope="selection", preview=True, undo=True,
    params=(
        Param("threshold_db", -40.0, "Threshold (dB)",
              "Quieter than this counts as silence (dB below full scale)", "", -80.0, -10.0),
        Param("min_silence", 0.4, "Minimum Silence",
              "Only quiet stretches at least this long are cut", "TIME_ABSOLUTE", 0.05, 5.0),
        Param("padding", 0.08, "Padding",
              "Quiet time kept before and after the sound, so words aren't clipped", "TIME_ABSOLUTE", 0.0, 1.0),
        Param("min_keep", 0.1, "Minimum Clip",
              "What's left between two cuts is removed too when it is shorter than this (a click, a breath)",
              "TIME_ABSOLUTE", 0.0, 2.0),
        Param("ripple", True, "Close the Gaps",
              "Move everything after each cut left, so no gaps are left (ripple)"),
    ),
)

RATE = 8000           # Hz: the envelope's sample rate, plenty for speech loudness
WINDOW_S = 0.01       # one loudness value per 10 ms
CHUNK_S = 20.0        # seconds read at a time
_ENVELOPES: dict[tuple, list[float]] = {}
_ENVELOPE_LIMIT = 16
_PREVIEW: dict[str, Any] = {"groups": []}


class _Problem(Exception):
    """A strip that can't be read: the message is for the artist."""


# -- strips across Blender versions (5.2 renamed the timing properties) -----------

def _left(strip: Any) -> int:
    return int(strip.left_handle if hasattr(strip, "left_handle") else strip.frame_final_start)


def _right(strip: Any) -> int:
    return int(strip.right_handle if hasattr(strip, "right_handle") else strip.frame_final_end)


def _content_start(strip: Any) -> float:
    return float(strip.content_start if hasattr(strip, "content_start") else strip.frame_start)


def _content_duration(strip: Any) -> float:
    return float(strip.content_duration if hasattr(strip, "content_duration") else strip.frame_duration)


def _move(strip: Any, delta: int) -> None:
    if hasattr(strip, "content_start"):
        strip.content_start = strip.content_start + delta
    else:
        strip.frame_start = strip.frame_start + delta


def _split(strip: Any, frame: int) -> Any:
    try:
        right = strip.split(frame=int(frame), split_method="SOFT", ignore_connections=True)
    except TypeError:
        right = strip.split(frame=int(frame), split_method="SOFT")
    if right is None:
        raise RuntimeError("could not split {!r} at frame {:d}".format(strip.name, int(frame)))
    return right


def _fps(scene: Any) -> float:
    return scene.render.fps / (scene.render.fps_base or 1.0)


def _scene(context: Any) -> Any:
    return getattr(context, "sequencer_scene", None) or context.scene


def _level(editor: Any) -> tuple[Any, Any]:
    """(strips, channels) of the level the artist is looking at (inside a meta strip, its own)."""
    if len(editor.meta_stack):
        meta = editor.meta_stack[-1]
        return meta.strips, getattr(meta, "channels", None)
    return editor.strips, getattr(editor, "channels", None)


def _locked(strip: Any, channels: Any) -> bool:
    if strip.lock:
        return True
    try:
        return bool(channels[strip.channel].lock)
    except (TypeError, IndexError, KeyError, AttributeError):
        return False


def _abspath(path: str, library: Any = None) -> str:
    try:
        return os.path.normpath(bpy.path.abspath(path, library=library)) if path else ""
    except (TypeError, ValueError):
        return path


def _facts(strip: Any) -> dict:
    path = ""
    if strip.type == "MOVIE":
        path = _abspath(getattr(strip, "filepath", ""))
    elif strip.type == "SOUND" and strip.sound is not None:
        path = _abspath(strip.sound.filepath, strip.sound.library)
    connected = []
    for item in getattr(strip, "connections", ()) or ():
        name = getattr(item, "name", None) or getattr(getattr(item, "strip", None), "name", None)
        if name:
            connected.append(name)
    return {"name": strip.name, "type": strip.type, "channel": strip.channel, "left": _left(strip),
            "right": _right(strip), "content_start": _content_start(strip), "path": path,
            "connected": connected}


# -- gather ------------------------------------------------------------------------

def _gather(context: Any) -> tuple[list[dict], list[str]]:
    """The groups to cut, ``[{"sound": strip, "members": [strips]}]``, and notes for the artist."""
    editor = _scene(context).sequence_editor
    strips, channels = _level(editor)
    every = list(strips)
    facts = {s.name: _facts(s) for s in every}
    notes: list[str] = []
    sounds = [s for s in every if s.select and s.type == "SOUND"]
    for strip in every:                     # a selected picture brings its sound
        if strip.select and strip.type in silences.VISUAL_TYPES:
            for other in every:
                if other.type == "SOUND" and other not in sounds and \
                        silences.is_partner(facts[other.name], facts[strip.name]):
                    sounds.append(other)
    groups, claimed = [], set()
    for sound in sorted(sounds, key=_left):
        if _locked(sound, channels):
            notes.append("“{:s}” is locked: left as it is".format(sound.name))
            continue
        members = [sound]
        claimed.add(sound.name)
        for other in every:
            if other.name not in claimed and silences.is_partner(facts[sound.name], facts[other.name]):
                if _locked(other, channels):
                    notes.append("“{:s}” goes with “{:s}” but is locked: not cut".format(other.name, sound.name))
                    continue
                members.append(other)
                claimed.add(other.name)
        groups.append({"sound": sound, "members": members})
    return groups, notes


# -- read --------------------------------------------------------------------------

def _source(sound: Any) -> tuple[tuple, Any]:
    """(cache key, aud.Sound) for *sound*, from its file or its packed data."""
    import aud  # pylint: disable=import-error
    path = _abspath(sound.filepath, sound.library)
    packed = sound.packed_file
    if packed is None:
        if not path or not os.path.isfile(path):
            raise _Problem("The sound file of “{:s}” is missing: {:s}".format(sound.name, path or "(no path)"))
        return (path, os.path.getmtime(path), os.path.getsize(path)), aud.Sound(path)
    key = ("packed", sound.name_full, packed.size)
    try:
        factory = sound.evaluated_get(bpy.context.evaluated_depsgraph_get()).factory
    except (AttributeError, RuntimeError):
        factory = None
    if factory is None:                     # write the packed data out once and read that
        out = os.path.join(tempfile.gettempdir(), "bfacw_sound_{:d}_{:s}".format(
            abs(hash(key)), os.path.basename(path) or "packed.wav"))
        if not os.path.isfile(out):
            with open(out, "wb") as fh:
                fh.write(packed.data)
        factory = aud.Sound(out)
    return key, factory


def _envelope(factory: Any, start_s: float, end_s: float, progress: Callable[[float], None]) -> list[float]:
    """dB per WINDOW_S of *factory* between *start_s* and *end_s*, read CHUNK_S at a time."""
    try:
        import numpy as np  # pylint: disable=import-error
    except ImportError:
        np = None
    mono = factory.rechannel(1)
    try:
        mono = mono.resample(RATE, False)
    except TypeError:
        mono = mono.resample(RATE)
    window = int(round(RATE * WINDOW_S))
    out: list[float] = []
    carry: Any = []
    t = start_s
    while t < end_s - 1e-6:
        stop = min(t + CHUNK_S, end_s)
        data = mono.limit(t, stop).data()
        if np is not None:
            x = data[:, 0] if getattr(data, "ndim", 1) == 2 else data
            if len(carry):
                x = np.concatenate((carry, x))
            whole = (len(x) // window) * window
            if whole:
                blocks = x[:whole].astype(np.float64).reshape(-1, window)
                rms = np.sqrt((blocks * blocks).mean(axis=1))
                out.extend(float(v) for v in 20.0 * np.log10(np.maximum(rms, 1e-10)))
            carry = x[whole:]
        else:                               # no numpy: slower, same numbers
            x = list(carry) + [float(row[0]) for row in data]
            whole = (len(x) // window) * window
            out.extend(silences.db(v) for v in silences.window_rms(x[:whole], window))
            carry = x[whole:]
        progress(stop - start_s)
        if len(data) == 0:
            break
        t = stop
    return out


def _retimed(strip: Any, factory: Any, fps: float) -> bool:
    """Retiming keys that change the speed: the sound no longer maps 1:1 to frames."""
    keys = getattr(strip, "retiming_keys", None)
    if keys is None or len(keys) == 0:
        return False
    if len(keys) > 2:
        return True
    try:
        rate = factory.specs[0]
        natural = factory.length / rate * fps if factory.length > 0 and rate > 0 else None
    except (AttributeError, TypeError, ZeroDivisionError):
        natural = None
    return natural is not None and abs(_content_duration(strip) - natural) > 1.5


def _analyse(context: Any, groups: list[dict], report_progress: bool = True) -> tuple[list[dict], list[str]]:
    """Read each group's sound: ``[{"group", "env", "t0", "t1", ...}]`` and problems."""
    scene = _scene(context)
    fps = _fps(scene)
    wm = context.window_manager
    infos, problems = [], []
    plans = []
    for group in groups:
        sound = group["sound"]
        if sound.sound is None:
            problems.append("“{:s}” has no sound data-block".format(sound.name))
            continue
        try:
            key, factory = _source(sound.sound)
        except _Problem as ex:
            problems.append(str(ex))
            continue
        except Exception as ex:  # pylint: disable=broad-exception-caught
            problems.append("Can't read the sound of “{:s}”: {!s}".format(sound.name, ex))
            continue
        if _retimed(sound, factory, fps):
            problems.append("“{:s}” is retimed (its speed changed): not cut".format(sound.name))
            continue
        t0, t1 = silences.sound_window(_left(sound), _right(sound), _content_start(sound),
                                       getattr(sound, "sound_offset", 0.0), fps)
        plans.append((group, key + (round(t0, 4), round(t1, 4)), factory, t0, t1))
    total = sum(max(0.0, p[4] - p[3]) for p in plans) or 1.0
    done = 0.0
    if report_progress and plans:
        wm.progress_begin(0, 1000)
    try:
        for index, (group, key, factory, t0, t1) in enumerate(plans):
            env = _ENVELOPES.get(key)
            if env is None:
                name = group["sound"].name

                def progress(seconds, _base=done, _name=name, _i=index):
                    if report_progress:
                        wm.progress_update(int(1000 * min(1.0, (_base + seconds) / total)))
                        _status(context, "Remove Silences: reading “{:s}” ({:d}/{:d}), {:d}%".format(
                            _name, _i + 1, len(plans), int(100 * min(1.0, (_base + seconds) / total))))
                try:
                    env = _envelope(factory, t0, t1, progress)
                except Exception as ex:  # pylint: disable=broad-exception-caught
                    problems.append("Can't read the sound of “{:s}”: {!s}".format(name, ex))
                    continue
                if len(_ENVELOPES) >= _ENVELOPE_LIMIT:
                    _ENVELOPES.pop(next(iter(_ENVELOPES)))
                _ENVELOPES[key] = env
            done += max(0.0, t1 - t0)
            sound = group["sound"]
            infos.append({"group": group, "env": env, "t0": t0, "t1": t1, "fps": fps,
                          "content_start": _content_start(sound), "offset": getattr(sound, "sound_offset", 0.0),
                          "left": _left(sound), "right": _right(sound), "name": sound.name})
    finally:
        if report_progress and plans:
            wm.progress_end()
            _status(context, None)
    return infos, problems


def _status(context: Any, text: str | None) -> None:
    try:
        context.workspace.status_text_set(text)
    except (AttributeError, RuntimeError):
        pass


def _cuts(info: dict, threshold_db: float, min_silence: float, padding: float, min_keep: float) -> list[tuple]:
    """Timeline frame ranges to remove from one group (empty when all of it is quiet)."""
    ranges = silences.find_silences(info["env"], WINDOW_S, threshold_db, min_silence, padding, min_keep,
                                    total_s=max(0.0, info["t1"] - info["t0"]))
    ranges = [(s + info["t0"], e + info["t0"]) for s, e in ranges]
    frames = silences.to_frames(ranges, info["fps"], info["content_start"], info["offset"],
                                info["left"], info["right"])
    if frames == [(info["left"], info["right"])]:
        info["all_quiet"] = True
        return []
    info["all_quiet"] = False
    return frames


# -- apply -------------------------------------------------------------------------

def _cut_member(strips: Any, strip: Any, frames: list[tuple]) -> int:
    """Remove *frames* from *strip* (split + remove); the number of pieces removed."""
    piece, removed = strip, 0
    for first, end in frames:
        if piece is None:
            break
        left, right = _left(piece), _right(piece)
        if end <= left:
            continue
        if first >= right:
            break
        first, end = max(first, left), min(end, right)
        middle = piece if first <= left else _split(piece, first)
        rest = _split(middle, end) if end < right else None
        strips.remove(middle)
        removed += 1
        piece = rest
    return removed


def _ripple(strips: Any, channels: Any, removed: list[tuple], members: set) -> list[str]:
    """Move every strip after a cut left by the time cut before it; notes for the artist."""
    notes = []
    every = sorted(strips, key=_left)
    starts = [_left(s) for s in every]
    shifts = silences.ripple_shifts(starts, removed)
    spanning = [s.name for s in every if s.name not in members and
                any(_left(s) < a and _right(s) > b for a, b in removed)]
    for strip, shift in zip(every, shifts):
        if not shift or getattr(strip, "input_1", None) is not None:
            continue                        # effects follow their inputs
        if _locked(strip, channels):
            notes.append("“{:s}” is locked: not moved".format(strip.name))
            continue
        _move(strip, -shift)
    if spanning:
        notes.append("{:d} other strip{:s} span{:s} a cut and kept {:s} length ({:s})".format(
            len(spanning), "" if len(spanning) == 1 else "s", "s" if len(spanning) == 1 else "",
            "its" if len(spanning) == 1 else "their", ", ".join(spanning[:3]) + ("…" if len(spanning) > 3 else "")))
    return notes


# -- the operator ------------------------------------------------------------------

def _prop(name: str, kind: Any, **extra: Any) -> Any:
    p = SPEC.param(name)
    kwargs = {"name": p.label, "description": p.description, "default": p.default}
    if p.unit:
        kwargs["unit"] = p.unit
    if p.soft_min is not None:
        kwargs["soft_min"] = p.soft_min
    if p.soft_max is not None:
        kwargs["soft_max"] = p.soft_max
    kwargs.update(extra)
    return kind(**kwargs)


class COWORKER_OT_sequencer_remove_silences(Operator):  # type: ignore[misc]
    """Cut the quiet parts out of the selected sound strips, and out of the picture that goes with them, and close the gaps"""

    bl_idname = SPEC.bl_idname
    bl_label = SPEC.label
    bl_description = SPEC.description
    bl_options = {"REGISTER", "UNDO"}

    threshold_db: _prop("threshold_db", FloatProperty, min=-120.0, max=0.0, precision=1)  # type: ignore[valid-type]
    min_silence: _prop("min_silence", FloatProperty, min=0.01, max=60.0, precision=2)  # type: ignore[valid-type]
    padding: _prop("padding", FloatProperty, min=0.0, max=10.0, precision=2)  # type: ignore[valid-type]
    min_keep: _prop("min_keep", FloatProperty, min=0.0, max=10.0, precision=2)  # type: ignore[valid-type]
    ripple: _prop("ripple", BoolProperty)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        scene = _scene(context)
        if scene is None or scene.sequence_editor is None:
            cls.poll_message_set("The scene has no sequencer strips")
            return False
        return True

    def _settings(self) -> tuple[float, float, float, float]:
        return self.threshold_db, self.min_silence, self.padding, self.min_keep

    def _prepare(self, context) -> list[dict] | None:
        groups, notes = _gather(context)
        if not groups:
            self.report({"WARNING"}, notes[0] if notes else
                        "Select one or more sound strips (or the movie strips that go with them)")
            return None
        infos, problems = _analyse(context, groups)
        for text in problems:
            self.report({"WARNING"}, text)
        if not infos:
            if not problems:
                self.report({"WARNING"}, "Nothing to read")
            return None
        _PREVIEW["groups"] = infos
        return infos

    def invoke(self, context, _event):
        if self._prepare(context) is None:
            return {"CANCELLED"}
        wm = context.window_manager
        try:
            return wm.invoke_props_dialog(self, width=360, title=SPEC.label, confirm_text=SPEC.label)
        except TypeError:                   # before 4.1: no title / confirm text
            return wm.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        for name in ("threshold_db", "min_silence", "padding", "min_keep", "ripple"):
            col.prop(self, name)
        layout.separator()
        infos = _PREVIEW.get("groups") or []
        count, frames, quiet = 0, 0, 0
        for info in infos:
            cuts = _cuts(info, *self._settings())
            count += len(cuts)
            frames += silences.removed_frames(cuts)
            quiet += 1 if info.get("all_quiet") else 0
        fps = infos[0]["fps"] if infos else 24.0
        box = layout.box()
        strips = "in {:d} strip{:s}".format(len(infos), "" if len(infos) == 1 else "s")
        if count:
            box.label(text="{:s} {:s}".format(silences.summary(count, frames, fps), strips), icon="INFO")
        else:
            box.label(text="Nothing is quiet enough: try a higher threshold", icon="INFO")
        if quiet:
            box.label(text="{:d} strip{:s} quiet all through: left as is".format(quiet, "" if quiet == 1 else "s"),
                      icon="ERROR")

    def execute(self, context):
        groups, notes = _gather(context)
        if not groups:
            self.report({"WARNING"}, notes[0] if notes else
                        "Select one or more sound strips (or the movie strips that go with them)")
            return {"CANCELLED"}
        infos, problems = _analyse(context, groups)
        for text in problems:
            self.report({"WARNING"}, text)
        if not infos:
            return {"CANCELLED"}
        plan = [(info, _cuts(info, *self._settings())) for info in infos]
        for info, _cut in plan:
            if info.get("all_quiet"):
                notes.append("“{:s}” is quiet all the way through: left as it is".format(info["name"]))
        if not any(cuts for _info, cuts in plan):
            self.report({"INFO"}, "Nothing is quiet enough to remove (below {:.0f} dB for {:.2f} s or more): "
                        "try a higher threshold or a shorter minimum silence".format(self.threshold_db,
                                                                                      self.min_silence))
            return {"CANCELLED"}
        editor = _scene(context).sequence_editor
        strips, channels = _level(editor)
        members, removed, count = set(), [], 0
        for info, cuts in plan:
            if not cuts:
                continue
            for strip in info["group"]["members"]:
                members.add(strip.name)
            for strip in list(info["group"]["members"]):
                _cut_member(strips, strip, cuts)
            removed.extend(cuts)
            count += len(cuts)
        removed = silences.merge_ranges(removed)
        if self.ripple:
            notes.extend(_ripple(strips, channels, removed, members))
        frames = silences.removed_frames(removed)
        for text in notes:
            self.report({"WARNING"}, text)
        strips_cut = sum(1 for _info, cuts in plan if cuts)
        self.report({"INFO"}, "Removed {:d} silence{:s} ({:s}) from {:d} strip{:s}{:s}".format(
            count, "" if count == 1 else "s", silences.format_duration(frames / _fps(_scene(context))),
            strips_cut, "" if strips_cut == 1 else "s", "" if self.ripple else ", gaps left open"))
        return {"FINISHED"}


# -- placement -----------------------------------------------------------------------

class CoworkerEntry:
    """A toolshelf entry for Bforartists' ``draw_entries``: it only needs ``poll`` and
    ``draw(layout, as_icon=...)`` (see ``bl_ui.space_toolsystem_common``).  Bforartists'
    ``OperatorEntry`` takes icon names only; this one draws the wand icon by value."""

    __slots__ = ("operator", "text", "icon", "fallback", "poll")

    def __init__(self, operator: str, text: str, icon: str, fallback: str, poll: bool = True):
        self.operator, self.text, self.icon, self.fallback, self.poll = operator, text, icon, fallback, poll

    def draw(self, layout: Any, *, as_icon: bool) -> None:
        if not self.poll:
            return
        icons.operator(layout, self.operator, name=self.icon, fallback=self.fallback,
                       text="" if as_icon else self.text)


def _entries() -> tuple:
    return (CoworkerEntry(SPEC.bl_idname, SPEC.label, SPEC.icon, SPEC.fallback_icon),)


def _has_toolshelf_tabs() -> bool:
    """Bforartists' Sequencer has a tabbed toolshelf; Blender's has none."""
    space = getattr(bpy.types, "SpaceSequenceEditor", None)
    return space is not None and "show_toolshelf_tabs" in space.bl_rna.properties


class COWORKER_PT_sequencer_toolshelf(Panel):  # type: ignore[misc]
    """Coworker's video tools in Bforartists' toolshelf (ToolsystemPanel conventions)"""

    bl_label = PANEL
    bl_idname = "COWORKER_PT_sequencer_toolshelf"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = CATEGORY
    bl_options = {"HIDE_BG"}

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(getattr(space, "show_toolshelf_tabs", False)) and \
            getattr(space, "view_type", "") in {"SEQUENCER"}

    def draw(self, context):
        try:
            from bl_ui.space_toolsystem_common import draw_entries  # pylint: disable=import-error
        except ImportError:
            draw_entries = None
        if draw_entries is not None:
            draw_entries(self.layout, context, _entries())
            return
        col = self.layout.column(align=True)
        for entry in _entries():
            entry.draw(col, as_icon=False)


class COWORKER_PT_sequencer_sidebar(Panel):  # type: ignore[misc]
    """Coworker's video tools in the Sequencer's sidebar (Blender; Bforartists without toolshelf tabs)"""

    bl_label = PANEL
    bl_idname = "COWORKER_PT_sequencer_sidebar"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 0                        # at the top of the tab, above the chat

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return getattr(space, "view_type", "") in {"SEQUENCER", "SEQUENCER_PREVIEW"} and \
            not getattr(space, "show_toolshelf_tabs", False)

    def draw(self, context):
        col = self.layout.column(align=True)
        col.scale_y = 1.4
        for entry in _entries():
            entry.draw(col, as_icon=False)


def _draw_strip_menu(self: Any, context: Any) -> None:
    space = context.space_data
    if getattr(space, "view_type", "SEQUENCER") not in {"SEQUENCER", "SEQUENCER_PREVIEW"}:
        return
    layout = self.layout
    layout.separator()
    layout.operator_context = "INVOKE_DEFAULT"
    icons.operator(layout, SPEC.bl_idname, name=SPEC.icon, fallback=SPEC.fallback_icon, text=SPEC.label + "...")


_REGISTERED: list[type] = []
_HOOKED: list[tuple[Any, Callable]] = []


def register() -> None:
    classes = [COWORKER_OT_sequencer_remove_silences, COWORKER_PT_sequencer_sidebar]
    if _has_toolshelf_tabs():               # Bforartists only: Blender refuses a TOOLS category
        classes.append(COWORKER_PT_sequencer_toolshelf)
    for cls in classes:
        bpy.utils.register_class(cls)
        _REGISTERED.append(cls)
    for place in SPEC.places:
        if place.kind == "menu":
            menu = getattr(bpy.types, place.target, None)
            if menu is not None:
                menu.append(_draw_strip_menu)
                _HOOKED.append((menu, _draw_strip_menu))


def unregister() -> None:
    for menu, fn in _HOOKED:
        try:
            menu.remove(fn)
        except (ValueError, RuntimeError):
            pass
    _HOOKED.clear()
    for cls in reversed(_REGISTERED):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _REGISTERED.clear()
    _ENVELOPES.clear()
    _PREVIEW["groups"] = []
    icons.free()
