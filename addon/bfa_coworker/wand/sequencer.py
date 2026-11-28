# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Remove Silences, the first Coworker operator (Tier 4m, Wand 0, #331).

``coworker.sequencer_remove_silences`` cuts the quiet parts out of the selected
sound strips -- and the same time out of every other strip, so the whole edit
stays in sync -- and closes the gaps.  No engine is needed.

* **Gather**: the selected sound strips (a selected movie brings its sound,
  ``silences.is_partner``): the sounds listened to.
* **Read**: the sound is read with ``aud`` (mono, resampled to 8 kHz, in 20 s
  chunks so a long file never sits in memory whole) into a loudness envelope of
  10 ms windows, cached per file; the window cursor and the status bar show the
  progress.
* **Preview**: the dialog shows the settings and a live line ("12 silences,
  0:41 removed").
* **Plan** (#539, ``cut_plan``): with sounds from several files, only where all
  of them are quiet; the same cuts go through every strip at that level, selected
  or not (picture, voice-over, music, titles).  A locked, retimed, scene or meta
  strip that a cut touches refuses the whole edit before anything changes, and
  says which.  Captions and other text strips over a cut are trimmed, not cut in
  pieces; one left too short to read goes.
* **Apply**: data-level ``Strip.split`` + ``strips.remove`` (no operator context
  needed), then the ripple moves what follows left; the first piece of a strip
  keeps its name; the scene end follows when it was at the end; one undo step.
* **Tool**: :func:`tool_remove_silences` is the agents' ``remove_silences``.

Places (``SPEC.places``): the end of the Sequencer's *Strip* menu, the
*Coworker* tab of Bforartists' tabbed toolshelf (only where
``show_toolshelf_tabs`` exists and is on) and the *Coworker* tab of the
Sequencer's sidebar (Blender, and Bforartists with the toolshelf tabs off).

The same panel and Strip-menu section also hold Coworker's other video tools when
their modules are registered (:data:`OTHERS`, :data:`SUBMENUS`): Auto Captions (#376)
and Make Vertical (#333), so each shows once, in one place.
"""

from __future__ import annotations

__all__ = (
    "CATEGORY",
    "CHUNK_S",
    "COWORKER_OT_sequencer_remove_silences",
    "COWORKER_PT_sequencer_sidebar",
    "COWORKER_PT_sequencer_toolshelf",
    "CoworkerEntry",
    "NO_DIALOG",
    "OTHERS",
    "PANEL",
    "RATE",
    "SPEC",
    "SUBMENUS",
    "VIEWS",
    "WINDOW_S",
    "apply_cuts",
    "blocked_in",
    "move_frames",
    "register",
    "remove_silences",
    "tool_remove_silences",
    "unregister",
)

import os
import tempfile
from typing import Any, Callable

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, FloatProperty  # pylint: disable=import-error
from bpy.types import Operator, Panel  # pylint: disable=import-error

from . import cut_plan, icons, silences
from .scene import poll as _sequencer_poll, sequencer_scene
from .spec import OperatorSpec, Param, Place, agent_line

CATEGORY = "Coworker"
PANEL = "Video"
HEADER_ICON = "FILE_MOVIE"      # the toolshelf panel's header (#504)
# The Sequencer views with a timeline: where the panel and the Strip-menu section show (#504).
VIEWS = frozenset({"SEQUENCER", "SEQUENCER_PREVIEW"})

SPEC = OperatorSpec(
    id="sequencer.remove_silences",
    label="Remove Silences",
    description=("Cut the quiet parts of the selected sound strips out of the whole edit (picture, other "
                 "sounds, titles, captions), so it all stays in sync, and close the gaps"),
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
        Param("padding", 0.15, "Padding",
              "Quiet time kept before and after the sound, so words aren't clipped", "TIME_ABSOLUTE", 0.0, 1.0),
        Param("min_keep", 0.1, "Minimum Clip",
              "What's left between two cuts is removed too when it is shorter than this (a click, a breath)",
              "TIME_ABSOLUTE", 0.0, 2.0),
        Param("ripple", True, "Close the Gaps",
              "Move everything after each cut left, so no gaps are left (ripple)"),
        Param("update_scene_end", True, "Update Scene End",
              "When the scene ended at the end of the edit, it ends at the new end (the preview range too)"),
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


# -- strip timing (the 5.2 names) -----------------------------------------------

def _left(strip: Any) -> int:
    return int(strip.left_handle)


def _right(strip: Any) -> int:
    return int(strip.right_handle)


def _content_start(strip: Any) -> float:
    return float(strip.content_start)


def _content_duration(strip: Any) -> float:
    return float(strip.content_duration)


def _move(strip: Any, delta: int) -> None:
    strip.content_start = strip.content_start + delta


def _split(strip: Any, frame: int) -> Any:
    right = strip.split(frame=int(frame), split_method="SOFT", ignore_connections=True)
    if right is None:
        raise RuntimeError("could not split {!r} at frame {:d}".format(strip.name, int(frame)))
    return right


def _fps(scene: Any) -> float:
    return scene.render.fps / (scene.render.fps_base or 1.0)


def _scene(context: Any) -> Any:
    return sequencer_scene(context) or context.scene


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

def _listen(every: list, chosen: list) -> list:
    """The sound strips to listen to: the *chosen* sounds and the sounds of the chosen pictures."""
    facts = {s.name: _facts(s) for s in every}
    sounds = [s for s in chosen if s.type == "SOUND"]
    for strip in chosen:                    # a picture brings its sound
        if strip.type in silences.VISUAL_TYPES:
            for other in every:
                if other.type == "SOUND" and other not in sounds and \
                        silences.is_partner(facts[other.name], facts[strip.name]):
                    sounds.append(other)
    return sorted(sounds, key=_left)


def _gather(context: Any) -> tuple[list, list[str]]:
    """The selected sound strips to listen to (with the sounds of the selected pictures), and notes."""
    editor = _scene(context).sequence_editor
    strips, _channels = _level(editor)
    every = list(strips)
    return _listen(every, [s for s in every if s.select]), []


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


def _speed_changed(strip: Any, fps: float) -> bool:
    """Any strip: retimed (#539).  A sound is measured against its file; for a picture
    Blender's own test is used (two or more retiming keys)."""
    keys = getattr(strip, "retiming_keys", None)
    if keys is None or len(keys) < 2:
        return False
    if strip.type == "SOUND" and strip.sound is not None:
        try:
            return _retimed(strip, _source(strip.sound)[1], fps)
        except Exception:  # pylint: disable=broad-exception-caught
            return True
    return True


def _analyse(scene: Any, sounds: list, context: Any = None,
             report_progress: bool = True) -> tuple[list[dict], list[str], list[tuple]]:
    """Read each sound: ``[{"sound", "env", "t0", "t1", ...}]``, problems for the artist,
    and the spans of the sounds that couldn't be read (never cut: their words are unknown)."""
    fps = _fps(scene)
    wm = getattr(context, "window_manager", None) if report_progress else None
    infos, problems, blocked = [], [], []
    plans = []
    for sound in sounds:
        if sound.sound is None:
            problems.append("“{:s}” has no sound data-block".format(sound.name))
            blocked.append((_left(sound), _right(sound)))
            continue
        try:
            key, factory = _source(sound.sound)
        except _Problem as ex:
            problems.append(str(ex))
            blocked.append((_left(sound), _right(sound)))
            continue
        except Exception as ex:  # pylint: disable=broad-exception-caught
            problems.append("Can't read the sound of “{:s}”: {!s}".format(sound.name, ex))
            blocked.append((_left(sound), _right(sound)))
            continue
        if _retimed(sound, factory, fps):
            problems.append("“{:s}” is retimed (its speed changed): not cut".format(sound.name))
            blocked.append((_left(sound), _right(sound)))
            continue
        t0, t1 = silences.sound_window(_left(sound), _right(sound), _content_start(sound),
                                       getattr(sound, "sound_offset", 0.0), fps)
        plans.append((sound, key + (round(t0, 4), round(t1, 4)), factory, t0, t1))
    total = sum(max(0.0, p[4] - p[3]) for p in plans) or 1.0
    done = 0.0
    if wm is not None and plans:
        wm.progress_begin(0, 1000)
    try:
        for index, (sound, key, factory, t0, t1) in enumerate(plans):
            env = _ENVELOPES.get(key)
            if env is None:
                name = sound.name

                def progress(seconds, _base=done, _name=name, _i=index):
                    if wm is not None:
                        wm.progress_update(int(1000 * min(1.0, (_base + seconds) / total)))
                        _status(context, "Remove Silences: reading “{:s}” ({:d}/{:d}), {:d}%".format(
                            _name, _i + 1, len(plans), int(100 * min(1.0, (_base + seconds) / total))))
                try:
                    env = _envelope(factory, t0, t1, progress)
                except Exception as ex:  # pylint: disable=broad-exception-caught
                    problems.append("Can't read the sound of “{:s}”: {!s}".format(name, ex))
                    blocked.append((_left(sound), _right(sound)))
                    continue
                if len(_ENVELOPES) >= _ENVELOPE_LIMIT:
                    _ENVELOPES.pop(next(iter(_ENVELOPES)))
                _ENVELOPES[key] = env
            done += max(0.0, t1 - t0)
            infos.append({"sound": sound, "env": env, "t0": t0, "t1": t1, "fps": fps,
                          "content_start": _content_start(sound), "offset": getattr(sound, "sound_offset", 0.0),
                          "left": _left(sound), "right": _right(sound), "name": sound.name})
    finally:
        if wm is not None and plans:
            wm.progress_end()
            _status(context, None)
    return infos, problems, blocked


def _status(context: Any, text: str | None) -> None:
    try:
        context.workspace.status_text_set(text)
    except (AttributeError, RuntimeError):
        pass


def _cuts(info: dict, threshold_db: float, min_silence: float, padding: float, min_keep: float) -> list[tuple]:
    """The quiet timeline frames of one sound (empty when all of it is quiet: then it isn't listened to)."""
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


def _common(infos: list[dict], blocked: list[tuple], settings: tuple) -> tuple[list[tuple], list[str]]:
    """The cuts for every strip: where every sound listened to is quiet (#539); the names of
    the sounds quiet all the way through (not listened to)."""
    listeners, quiet = [], []
    for info in infos:
        frames = _cuts(info, *settings)
        if info.get("all_quiet"):
            quiet.append(info["name"])
            continue
        span = info.get("span")
        if span:                            # its picture is a frame or two longer
            listeners.append(cut_plan.stretch_edges(info["left"], info["right"], frames, span[0], span[1]))
        else:
            listeners.append((info["left"], info["right"], frames))
    listeners.extend((left, right, []) for left, right in blocked)
    return cut_plan.common_cuts(listeners), quiet


OVERHANG = 2        # frames a movie's picture may run past its own sound (container rounding)


def _overhang(infos: list[dict], every: list) -> None:
    """Each sound's span stretched to the picture of its file when that is a frame or two
    longer, so a trailing silence takes the picture's last frames too (no 1-frame piece)."""
    facts = {s.name: _facts(s) for s in every if s.type in ("MOVIE", "SOUND")}
    for info in infos:
        mine = facts.get(info["name"])
        if mine is None:
            continue
        left, right = info["left"], info["right"]
        for other in every:
            if other.type != "MOVIE" or not silences.is_partner(mine, facts[other.name]):
                continue
            l2, r2 = _left(other), _right(other)
            if abs(l2 - info["left"]) <= OVERHANG and abs(r2 - info["right"]) <= OVERHANG:
                left, right = min(left, l2), max(right, r2)
        if (left, right) != (info["left"], info["right"]):
            info["span"] = (left, right)


# -- apply -------------------------------------------------------------------------

_TAG = "bfacw_caption"      # captions.TAG: a caption strip's tag (its role, the line of a word strip)


def _describe(every: list, channels: Any, fps: float) -> list[dict]:
    """Every strip at the level as :func:`cut_plan.plan` reads it."""
    transitions = {}
    for strip in every:
        if getattr(strip, "input_2", None) is not None:
            for item in (strip.input_1, strip.input_2):
                if item is not None:
                    transitions[item.name] = strip.name
    out = []
    for strip in every:
        follows = getattr(strip, "input_1", None) is not None
        refuse = ""
        if strip.type == "SCENE":
            refuse = "is a scene strip: cut it by hand"
        elif strip.type == "META":
            refuse = "is a meta strip: cut inside it (Tab), or by hand"
        elif getattr(strip, "input_2", None) is not None:
            refuse = "is a transition over a cut: remove it first"
        elif strip.name in transitions:
            refuse = "has a transition (“{:s}”) on it: remove it first".format(transitions[strip.name])
        elif _speed_changed(strip, fps):
            refuse = "is retimed (its speed changed): reset its retiming first"
        row = {"name": strip.name, "left": _left(strip), "right": _right(strip),
               "kind": "follow" if follows else ("text" if strip.type == "TEXT" else "media"),
               "locked": _locked(strip, channels), "refuse": refuse}
        if strip.type == "TEXT":
            tag = strip.get(_TAG)
            if tag is not None and hasattr(tag, "get") and tag.get("role") == "word":
                row["line"] = str(tag.get("line", ""))
        out.append(row)
    return out


def _cut_member(strips: Any, strip: Any, frames: list[tuple]) -> list:
    """Remove *frames* from *strip* (split + remove); the pieces kept, in time order."""
    # Strip.split() and strips.remove() free strips without stopping the Sequencer's
    # prefetch first; it is stopped here, before the first split (#536).
    from .prefetch import remove_strips, stop_prefetch
    stop_prefetch(strip)
    piece, kept = strip, []
    for first, end in frames:
        if piece is None:
            break
        left, right = _left(piece), _right(piece)
        if end <= left:
            continue
        if first >= right:
            break
        first, end = max(first, left), min(end, right)
        if first > left:
            middle = _split(piece, first)
            kept.append(piece)
        else:
            middle = piece
        rest = _split(middle, end) if end < right else None
        remove_strips(strips, [middle])
        piece = rest
    if piece is not None:
        kept.append(piece)
    return kept


def _sweep(strips: Any, cuts: Any, text_too: bool) -> None:
    """What a split of an effect's input split along with it (Blender splits the whole chain):
    the parts inside a cut go too."""
    for _round in range(3):
        todo = []
        for strip in strips:
            if getattr(strip, "input_1", None) is not None or (strip.type == "TEXT" and not text_too):
                continue
            touched = cuts.touching(_left(strip), _right(strip))
            if touched:
                todo.append((strip, touched))
        if not todo:
            return
        for strip, touched in todo:
            _cut_member(strips, strip, touched)


def _drop(scene: Any, strips: Any, doomed: list) -> list[str]:
    """Remove the text strips that keep too little to read, and their animation."""
    from .prefetch import remove_strips
    names = [s.name for s in doomed]
    remove_strips(strips, doomed)         # with the prefetch stopped first (#536)
    if names:
        try:
            from ..captions_ops import _drop_fcurves  # #376: the evaluated action keeps them otherwise
            _drop_fcurves(scene, names)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    return names


def _signature(strip: Any) -> str:
    """The caption's signature as it is now (#552: ``captions.caption_signature``)."""
    from ..captions import caption_signature
    return caption_signature(strip.text, _right(strip) - _left(strip), strip.channel, tuple(strip.location))


def _unedited(strip: Any) -> bool:
    """A caption still as Coworker made it (its signature matches): Remove Silences may re-sign it."""
    tag = strip.get(_TAG) if strip.type == "TEXT" else None
    sig = tag.get("sig") if tag is not None and hasattr(tag, "get") else None
    if not sig:
        return False
    try:
        return sig == _signature(strip)
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _resign(strip: Any) -> None:
    """Trimmed or cut by Remove Silences is not a hand edit: the caption's signature follows (#552)."""
    tag = strip.get(_TAG)
    if tag is not None:
        try:
            tag["sig"] = _signature(strip)
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def _refit(scene: Any, names: list[str]) -> None:
    """Captions trimmed or cut here, still as Coworker made them: their animation preset fits
    their frames again (#542: a fade-out at the new end, not past it)."""
    if not names:
        return
    try:
        from ..captions_ops import refit_captions
        refit_captions(scene, names)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _text_pieces(every: list, pieces: dict, new_names: dict, fresh: set) -> None:
    """Close the Gaps off: the pieces of a cut caption keep it unedited (re-signed), and its
    word strips name the piece they are in, not the first one."""
    lines = {}
    for original, ps in pieces.items():
        if original in fresh:
            for piece in ps:
                _resign(piece)
        if len(ps) > 1:
            lines[original] = [(_left(p), _right(p), n) for p, n in zip(ps, new_names.get(original, []))]
    if not lines:
        return
    for strip in every:
        tag = strip.get(_TAG) if strip.type == "TEXT" else None
        if tag is None or not hasattr(tag, "get") or tag.get("role") != "word":
            continue
        spans = lines.get(str(tag.get("line", "")))
        if not spans:
            continue
        home = next((n for a, b, n in spans if a <= _left(strip) < b), None)
        if home and home != tag.get("line"):
            fixed = _unedited(strip)
            tag["line"] = home
            if fixed:
                _resign(strip)


def _ripple(strips: Any, cuts: Any, trims: dict) -> None:
    """Close the cuts up: everything moves left by the time cut before it, and a text strip
    over a cut is trimmed to what it keeps (in time order, so nothing lands on another strip)."""
    for strip in sorted(strips, key=_left):
        if getattr(strip, "input_1", None) is not None:
            continue                        # effects follow their inputs
        left = _left(strip)
        new = trims.get(strip.name)
        if new is None:
            shift = left - int(cuts.map(left))
            if shift:
                _move(strip, -shift)
            continue
        new_left, new_right = int(new[0]), int(new[1])
        fresh = _unedited(strip)
        if new_right - new_left < _right(strip) - left:
            strip.right_handle = left + (new_right - new_left)
        if new_left != left:
            _move(strip, new_left - left)   # its keys (Bold Pop) move with it
        tag = strip.get(_TAG) if strip.type == "TEXT" else None
        offsets = tag.get("word_frames") if tag is not None and hasattr(tag, "get") else None
        if offsets:                         # #497: Make Vertical lays the words out again from these
            try:
                tag["word_frames"] = [int(cuts.map(left + int(o))) - new_left for o in offsets]
            except (TypeError, ValueError, KeyError):
                pass
        if fresh:                           # trimmed by the cut, not by hand (#552)
            _resign(strip)


def _rename(editor: Any, pieces: dict) -> dict[str, list[str]]:
    """The first piece of each strip keeps its name, the others are numbered from it (#539)."""
    moving = {p.as_pointer() for ps in pieces.values() for p in ps}
    every = editor.strips_all if hasattr(editor, "strips_all") else editor.sequences_all
    used = {s.name for s in every if s.as_pointer() not in moving} | set(pieces)
    wanted = {}
    for original, ps in pieces.items():
        names = cut_plan.piece_names(original, len(ps), used - {original})
        used.update(names)
        wanted[original] = names
    pairs = [(p, name) for original, ps in pieces.items() for p, name in zip(ps, wanted[original])]
    for index, (p, name) in enumerate(pairs):       # out of the way first: no clash among them
        if p.name != name:
            p.name = "bfacw_piece_{:d}".format(index)
    for p, name in pairs:
        if p.name != name:
            p.name = name
    return {original: [p.name for p in ps] for original, ps in pieces.items()}


def _content_end(strips: Any) -> int:
    return max((_right(s) for s in strips), default=0)


def remove_silences(scene: Any, settings: tuple, names: list[str] | None = None, ripple: bool = True,
                    update_end: bool = True, context: Any = None, report_progress: bool = True,
                    frame_range: tuple[int, int] | None = None) -> dict[str, Any]:
    """Remove the silences of *names* (or of the selected strips) from the whole edit (#539).

    *settings*: (threshold dB, minimum silence, padding, minimum clip).  The answer has
    ``status`` (``ok``, ``nothing``, ``refused`` or ``error``), a ``message``, and with
    ``ok`` the cuts, the time removed, the new names per strip and the new end frame.
    Nothing changes unless the status is ``ok`` (or ``error`` with ``changed``).

    *frame_range* (#615): ``(first, end)``, end exclusive.  Only the silences inside these frames go
    (Make Short polishes a pick without touching the rest of the edit); a strip that can't follow a cut
    outside the range is not asked.
    """
    editor = scene.sequence_editor
    if editor is None:
        return {"status": "nothing", "message": "The scene “{:s}” has no strips".format(scene.name)}
    strips, _channels = _level(editor)
    every = list(strips)
    if names:
        pool = {s.name: s for s in every}
        missing = [n for n in names if n not in pool]
        if missing:
            return {"status": "error", "message": "No strip named {:s} here".format(
                ", ".join("“{:s}”".format(n) for n in missing))}
        chosen = [pool[n] for n in names]
    else:
        chosen = [s for s in every if s.select]
    sounds = _listen(every, chosen)
    if not sounds:
        return {"status": "nothing", "message": "Select one or more sound strips (or the movie strips that go with them)"}
    infos, problems, blocked = _analyse(scene, sounds, context, report_progress)
    _overhang(infos, every)
    cuts, quiet = _common(infos, blocked, settings)
    if frame_range is not None:
        cuts = cut_plan.within(cuts, int(frame_range[0]), int(frame_range[1]))
    notes = ["“{:s}” is quiet all the way through: not listened to".format(n) for n in quiet]
    base = {"problems": problems, "notes": notes, "listened": [s.name for s in sounds]}
    if not cuts:
        return dict(base, status="nothing", message=(
            "Nothing is quiet enough to remove (below {:.0f} dB for {:.2f} s or more{:s}): try a higher threshold "
            "or a shorter minimum silence".format(settings[0], settings[1],
                                                  ", in every sound at once" if len(sounds) > 1 else "")))
    out = apply_cuts(scene, cuts, ripple, update_end, "Remove Silences")
    if out["status"] != "ok":
        return dict(base, **out)
    seconds = out["seconds_removed"]
    message = "Removed {:d} silence{:s} ({:s}) from the edit{:s}".format(
        out["cuts"], "" if out["cuts"] == 1 else "s", silences.format_duration(seconds),
        "" if ripple else ", gaps left open")
    dropped = out["captions_dropped"]
    if dropped:
        message += "; {:d} caption{:s} too short to read went".format(len(dropped), "" if len(dropped) == 1 else "s")
    if out["scene_end_changed"]:
        message += "; the scene now ends at frame {:d}".format(scene.frame_end)
    return dict(base, **dict(out, message=message))


def apply_cuts(scene: Any, cuts: list[tuple], ripple: bool = True, update_end: bool = True,
               what: str = "Remove Silences") -> dict[str, Any]:
    """Remove the timeline frames *cuts* (``[(first, end)]``) from every strip at the level the
    artist is looking at, with Remove Silences' plan (#539): picture and sound together, every
    other track in step, captions trimmed (one too short to read goes), locked / retimed /
    scene / meta strips over a cut refusing the whole edit before anything changes, the first
    piece keeping its name, the gaps closed (*ripple*) and the scene end following.  Edit by
    Transcript (#498) cuts words with it.  No undo step of its own.

    ``status``: ``ok``, ``nothing``, ``refused`` or ``error`` (``changed``: press Ctrl+Z)."""
    editor = scene.sequence_editor
    if editor is None or not cuts:
        return {"status": "nothing", "message": "Nothing to cut", "refused": []}
    strips, channels = _level(editor)
    every = list(strips)
    fps = _fps(scene)
    the_plan = cut_plan.plan(_describe(every, channels, fps), cuts, ripple, fps)
    if the_plan["refused"]:
        refused = [{"strip": n, "reason": r} for n, r in the_plan["refused"]]
        listed = "; ".join("“{:s}” {:s}".format(n, r) for n, r in the_plan["refused"][:4])
        more = len(the_plan["refused"]) - 4
        return {"status": "refused", "refused": refused, "message": (
            "Nothing was cut, so the edit stays in sync: {:s}{:s}".format(
                listed, " (and {:d} more)".format(more) if more > 0 else ""))}
    end0 = _content_end(editor.strips)
    by_name = {s.name: s for s in every}
    cuts_index = cut_plan.Cuts(the_plan["cuts"])
    texts = {n for n in the_plan["cut"] if by_name[n].type == "TEXT"}  # read now: a cut may free the strip
    fresh = {n for n in texts if _unedited(by_name[n])}               # captions cut with the gaps left
    trimmed = [n for n in the_plan["trim"] if n in by_name and by_name[n].type == "TEXT" and _unedited(by_name[n])]
    try:
        pieces = {name: _cut_member(strips, by_name[name], ranges) for name, ranges in the_plan["cut"].items()}
        _sweep(strips, cuts_index, not ripple)
        dropped = _drop(scene, strips, [by_name[n] for n in the_plan["drop"]])
        if ripple:
            _ripple(strips, cuts_index, the_plan["trim"])
        new_names = _rename(editor, pieces)
        if texts:
            _text_pieces(list(strips), {n: ps for n, ps in pieces.items() if n in texts}, new_names, fresh)
        _refit(scene, [p for n in sorted(fresh) for p in new_names.get(n, [n])] + trimmed)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return {"status": "error", "changed": True, "refused": [], "message": (
            "{:s} stopped part-way ({!s}): press Ctrl+Z to get the edit back".format(what, ex))}
    end1 = _content_end(editor.strips)
    end_changed = False
    if update_end and end1 < end0:
        new_end = cut_plan.follow_end(scene.frame_end, end0, end1, scene.frame_start)
        if new_end is not None and new_end != scene.frame_end:
            scene.frame_end = new_end
            end_changed = True
        if scene.use_preview_range:
            new_preview = cut_plan.follow_end(scene.frame_preview_end, end0, end1, scene.frame_preview_start)
            if new_preview is not None:
                scene.frame_preview_end = new_preview
    frames = silences.removed_frames(the_plan["cuts"])
    seconds = frames / fps
    changed = sorted(n for n, ps in new_names.items() if ps != [n])
    return dict(status="ok", message="", cuts=len(the_plan["cuts"]), frames_removed=frames,
                seconds_removed=round(seconds, 2), cut_ranges=[list(r) for r in the_plan["cuts"]],
                new_names=new_names, renamed=changed, captions_trimmed=sorted(the_plan["trim"]),
                captions_dropped=dropped, end_frame=scene.frame_end, content_end=end1 - 1,
                scene_end_changed=end_changed, refused=[])


def blocked_in(scene: Any, lo: int, hi: int) -> list[tuple[str, str]]:
    """``[(strip, why)]`` of the strips at the level over frames ``lo..hi`` that can't be cut or
    moved (locked, retimed, scene, meta, transition, effect): asked before an edit starts."""
    editor = scene.sequence_editor
    if editor is None:
        return []
    strips, channels = _level(editor)
    out = []
    for row in _describe(list(strips), channels, _fps(scene)):
        if row["right"] <= lo or row["left"] >= hi:
            continue
        if row["locked"]:
            out.append((row["name"], "is locked"))
        elif row["refuse"]:
            out.append((row["name"], row["refuse"]))
        elif row["kind"] == "follow":
            out.append((row["name"], "is an effect strip: move it by hand"))
    return out


def move_frames(scene: Any, first: int, end: int, to: int, what: str = "Edit by Transcript") -> dict[str, Any]:
    """Move the frames ``first..end`` of every strip at the level to frame *to* (#498: a
    paragraph moved in the transcript), the time between closing up behind them: picture,
    sound, captions and every other track move together, so nothing drifts and the edit keeps
    its length.  Strips are split (prefetch stopped first, #536) at the three frames; the first
    piece of each keeps its name; captions split on the way stay Coworker's (re-signed).

    A locked, retimed, scene, meta, transition or effect strip in the frames that change
    refuses before anything changes.  ``status``: ``ok``, ``nothing``, ``refused``, ``error``."""
    editor = scene.sequence_editor
    first, end, to = int(first), int(end), int(to)
    if editor is None or end <= first or first <= to <= end:
        return {"status": "nothing", "message": "Nothing to move", "refused": []}
    strips, _channels = _level(editor)
    every = list(strips)
    lo, hi = min(first, to), max(end, to)
    width = end - first
    refused = blocked_in(scene, lo, hi)
    if refused:
        listed = "; ".join("“{:s}” {:s}".format(n, r) for n, r in refused[:4])
        return {"status": "refused", "refused": [{"strip": n, "reason": r} for n, r in refused],
                "message": "Nothing was moved, so the edit stays in sync: {:s}".format(listed)}
    from .prefetch import stop_prefetch
    texts = {s.name for s in every if s.type == "TEXT"}
    fresh = {s.name for s in every if s.type == "TEXT" and _unedited(s)}
    try:
        if every:
            stop_prefetch(every[0])
        pieces: dict[str, list] = {}
        for strip in every:
            parts = [strip]
            for frame in sorted({first, end, to}):
                last = parts[-1]
                if _left(last) < frame < _right(last):
                    parts.append(_split(last, frame))
            if len(parts) > 1:
                pieces[strip.name] = parts
        now = list(strips)
        block = [s for s in now if first <= _left(s) and _right(s) <= end]
        if to < first:
            between = sorted((s for s in now if to <= _left(s) and _right(s) <= first), key=_left, reverse=True)
            shift, dest = width, to
        else:
            between = sorted((s for s in now if end <= _left(s) and _right(s) <= to), key=_left)
            shift, dest = -width, to - width
        far = max(_content_end(now), hi) + width + 10 - first
        for s in block:
            _move(s, far)
        for s in between:
            _move(s, shift)
        for s in block:
            _move(s, dest - first - far)
        for parts in pieces.values():
            parts.sort(key=_left)
        new_names = _rename(editor, pieces)
        if texts & set(pieces):
            _text_pieces(list(strips), {n: ps for n, ps in pieces.items() if n in texts}, new_names, fresh)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return {"status": "error", "changed": True, "refused": [], "message": (
            "{:s} stopped part-way ({!s}): press Ctrl+Z to get the edit back".format(what, ex))}
    return {"status": "ok", "message": "", "first": first, "end": end, "to": dest, "frames": width,
            "new_names": new_names, "refused": []}


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
    """Cut the quiet parts of the selected sound strips out of the whole edit, so it all stays in sync, and close the gaps"""

    bl_idname = SPEC.bl_idname
    bl_label = SPEC.label
    bl_description = agent_line(SPEC.description)
    bl_options = {"REGISTER", "UNDO"}

    threshold_db: _prop("threshold_db", FloatProperty, min=-120.0, max=0.0, precision=1)  # type: ignore[valid-type]
    min_silence: _prop("min_silence", FloatProperty, min=0.01, max=60.0, precision=2)  # type: ignore[valid-type]
    padding: _prop("padding", FloatProperty, min=0.0, max=10.0, precision=2)  # type: ignore[valid-type]
    min_keep: _prop("min_keep", FloatProperty, min=0.0, max=10.0, precision=2)  # type: ignore[valid-type]
    ripple: _prop("ripple", BoolProperty)  # type: ignore[valid-type]
    update_scene_end: _prop("update_scene_end", BoolProperty)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _sequencer_poll(cls, context)      # the Sequencer's scene, with strips (#504)

    def _settings(self) -> tuple[float, float, float, float]:
        return self.threshold_db, self.min_silence, self.padding, self.min_keep

    def _prepare(self, context) -> list[dict] | None:
        sounds, notes = _gather(context)
        if not sounds:
            self.report({"WARNING"}, notes[0] if notes else
                        "Select one or more sound strips (or the movie strips that go with them)")
            return None
        infos, problems, blocked = _analyse(_scene(context), sounds, context)
        for text in problems:
            self.report({"WARNING"}, text)
        if not infos:
            if not problems:
                self.report({"WARNING"}, "Nothing to read")
            return None
        editor = _scene(context).sequence_editor
        _overhang(infos, list(_level(editor)[0]))
        _PREVIEW["groups"] = infos
        _PREVIEW["blocked"] = blocked
        return infos

    def invoke(self, context, _event):
        if self._prepare(context) is None:
            return {"CANCELLED"}
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=360, title=SPEC.label, confirm_text=SPEC.label)

    def draw(self, _context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        for name in ("threshold_db", "min_silence", "padding", "min_keep", "ripple", "update_scene_end"):
            col.prop(self, name)
        layout.separator()
        infos = _PREVIEW.get("groups") or []
        cuts, quiet = _common(infos, _PREVIEW.get("blocked") or [], self._settings())
        fps = infos[0]["fps"] if infos else 24.0
        box = layout.box()
        heard = len(infos) - len(quiet)
        strips = "in {:d} sound{:s}".format(heard, "" if heard == 1 else "s") if heard > 1 else ""
        if cuts:
            box.label(text="{:s} {:s}".format(silences.summary(len(cuts), silences.removed_frames(cuts), fps),
                                              strips).strip(), icon="INFO")
        else:
            box.label(text="Nothing is quiet enough: try a higher threshold", icon="INFO")
        if quiet:
            box.label(text="{:d} sound{:s} quiet all through: not listened to".format(
                len(quiet), "" if len(quiet) == 1 else "s"), icon="ERROR")

    def execute(self, context):
        out = remove_silences(_scene(context), self._settings(), None, self.ripple, self.update_scene_end,
                              context=context)
        for text in out.get("problems", []) + out.get("notes", []):
            self.report({"WARNING"}, text)
        status = out["status"]
        if status == "ok":
            self.report({"INFO"}, out["message"])
            return {"FINISHED"}
        if status == "nothing":
            self.report({"INFO"} if out.get("listened") else {"WARNING"}, out["message"])
            return {"CANCELLED"}
        self.report({"ERROR"}, out["message"])
        return {"FINISHED"} if out.get("changed") else {"CANCELLED"}


# -- the agent tool (#539) -----------------------------------------------------------

def _tool_scene() -> Any:
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm and wm.windows else None)
    scene = getattr(getattr(window, "workspace", None), "sequencer_scene", None) if window else None
    if scene is None:
        scene = window.scene if window is not None else bpy.context.scene
    return scene


def _push_undo(name: str) -> None:
    """One undo step for a call from outside a chat turn; a chat turn's own step covers it (#501)."""
    try:
        from .. import coworker_api
    except ImportError:
        coworker_api = None
    if coworker_api is not None and coworker_api.turn_owns_undo():
        return
    undo = getattr(coworker_api, "_undo", None)
    if undo is not None and undo(push_message=name):
        return
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm and wm.windows else None)
    try:
        with bpy.context.temp_override(window=window):
            bpy.ops.ed.undo_push(message=name)
    except (RuntimeError, TypeError, AttributeError):
        pass


def tool_remove_silences(strips: Any = None, threshold_db: float = -40.0, min_silence: float = 0.4,
                         padding: float = 0.15, min_keep: float = 0.1, close_gaps: bool = True,
                         update_scene_end: bool = True) -> dict[str, Any]:
    """``remove_silences`` (MCP): the operator's work without a UI, one undo step."""
    try:
        values = (float(threshold_db), float(min_silence), float(padding), float(min_keep))
    except (TypeError, ValueError):
        return {"status": "error", "message": "threshold_db, min_silence, padding and min_keep are numbers"}
    if not -120.0 <= values[0] <= 0.0 or values[1] <= 0.0 or values[2] < 0.0 or values[3] < 0.0:
        return {"status": "error", "message": "threshold_db is -120..0 (dB); min_silence above 0; "
                                              "padding and min_keep 0 or more (seconds)"}
    if isinstance(strips, str):
        strips = [strips] if strips else []
    names = [str(n) for n in strips or ()]
    scene = _tool_scene()
    editor = scene.sequence_editor if scene is not None else None
    if editor is None:
        return {"status": "error", "message": "The Sequencer's scene has no strips"}
    note = ""
    if not names:
        level, _channels = _level(editor)
        if not any(s.select for s in level):
            names = [s.name for s in level if s.type == "SOUND"]
            note = "No strip named or selected: listened to every sound strip"
    out = remove_silences(scene, values, names or None, bool(close_gaps), bool(update_scene_end),
                          context=bpy.context, report_progress=False)
    if note:
        out["notes"] = [note] + list(out.get("notes", []))
    if out["status"] == "ok" or out.get("changed"):
        _push_undo("Remove Silences")
    if out["status"] == "nothing":
        out["status"] = "ok"
        out.setdefault("cuts", 0)
    out["scene"] = scene.name
    return out


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


# Coworker's other video operators, in the same Video panel and Strip-menu section when
# their modules are registered (#376 Auto Captions, #333 Make Vertical, #498 Edit Transcript):
# (operator, its class name in bpy.types, text, wand icon, Blender fallback icon).
OTHERS = (
    ("bfacw.auto_captions", "BFACW_OT_auto_captions", "Auto Captions", "captions", "FONT_DATA"),
    ("bfacw.make_vertical", "BFACW_OT_make_vertical", "Make Vertical", "vertical", "SEQ_PREVIEW"),
    ("bfacw.open_transcript", "BFACW_OT_open_transcript", "Edit Transcript", "transcribe", "TEXT"),
)
# Of those, the ones that open no dialog (no "..." in the Strip menu).
NO_DIALOG = frozenset({"bfacw.open_transcript"})
# Their submenus in the section: (menu, wand icon or "", Blender icon).
SUBMENUS = (("BFACW_MT_captions", "captions", "FONT_DATA"), ("BFACW_MT_reframe", "", "VIEW_PAN"),
            ("BFACW_MT_reframe_layout", "reframe_split_half", "SPLIT_HORIZONTAL"))


def _registered(name: str) -> bool:
    return getattr(bpy.types, name, None) is not None


def _entries() -> tuple:
    out = [CoworkerEntry(SPEC.bl_idname, SPEC.label, SPEC.icon, SPEC.fallback_icon)]
    for idname, cls_name, text, icon, fallback in OTHERS:
        if _registered(cls_name):
            out.append(CoworkerEntry(idname, text, icon, fallback))
    return tuple(out)


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
        # Both views with a timeline (#504); not the Preview alone, where Bforartists' toolshelf
        # keeps its strip tools out too (only the Image tab) and the Strip menu has no timeline part.
        space = context.space_data
        return bool(getattr(space, "show_toolshelf_tabs", False)) and \
            getattr(space, "view_type", "") in VIEWS

    def draw_header(self, _context):
        # The toolshelf's default width leaves room for about two letters ("Vi", like
        # Bforartists' own "Cl" and "Im"): an icon names the panel there (#504).
        from ..shared import icon as safe_icon
        self.layout.label(icon=safe_icon(HEADER_ICON))

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
    # First in the tab: Queue and the chat's panels are 0 and up, and Blender keeps bl_order >= 0, so
    # equal orders go by registration: the add-on registers wand before the chat (#504).
    bl_order = 0

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return getattr(space, "view_type", "") in VIEWS and \
            not getattr(space, "show_toolshelf_tabs", False)

    def draw(self, context):
        col = self.layout.column(align=True)
        col.scale_y = 1.4
        for entry in _entries():
            entry.draw(col, as_icon=False)


def _draw_strip_menu(self: Any, context: Any) -> None:
    space = context.space_data
    if getattr(space, "view_type", "SEQUENCER") not in VIEWS:
        return
    layout = self.layout
    layout.separator()
    layout.operator_context = "INVOKE_DEFAULT"
    # One Coworker section for every Coworker video tool (#331, #376, #333): each once.
    icons.operator(layout, SPEC.bl_idname, name=SPEC.icon, fallback=SPEC.fallback_icon, text=SPEC.label + "...")
    for idname, cls_name, text, icon, fallback in OTHERS:
        if _registered(cls_name):
            icons.operator(layout, idname, name=icon, fallback=fallback,
                           text=text + ("" if idname in NO_DIALOG else "..."))
    for menu_id, icon, fallback in SUBMENUS:
        if _registered(menu_id):
            if icon:
                icons.menu(layout, menu_id, name=icon, fallback=fallback)
            else:
                from ..shared import icon as safe_icon
                layout.menu(menu_id, icon=safe_icon(fallback))


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
