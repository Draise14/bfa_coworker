# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Auto Captions in the Sequencer (Tier 4, Video 2, #376): caption lines become styled
text strips, SRT in and out, and Translate for many captions at once.

* **Auto Captions** (Strip menu, Coworker > Video panel): the selected sound / movie strips' words
  (from local transcription when this build has it, or a transcript file) become
  one text strip per caption line on a channel named *Captions*, in a style
  (:data:`captions.STYLES`).  *Word-by-word highlight* adds, per word, a strip on
  the channel above showing that word in the highlight colour exactly over the
  same word of the line (the line is measured with ``blf`` in the same font).
  One undo step; running it again replaces that source's captions.
* **Export SRT** writes every caption line (and any text strip on the *Captions*
  channel, so hand edits go out too); **Import SRT** makes styled captions at the
  cue times.  ``sequencer.export_subtitles`` needs a window and there is no import
  operator, so both are ours (they work in the background and in tools too).
* **Translate Captions** (right-click): one question for all caption lines through
  the existing Translate dialog; each translated line is written back into its own
  strip (same timing, style and tag).

Every caption strip has the custom property :data:`captions.TAG`:
``{"source", "style", "role": "line"|"word", "chars", "lines"}`` (``"line"``: the
caption's strip name, on a word strip); a line made since #497 also has ``"frame"`` (the
output size it was laid out for) and ``"word_frames"`` (each word's first frame from the
line's start), which Make Vertical uses to lay it out again (:func:`relayout_captions`).
"""

from __future__ import annotations

__all__ = (
    "add_captions",
    "apply_translation",
    "caption_cues",
    "import_srt",
    "register",
    "relayout_captions",
    "remove_captions",
    "seq_scene",
    "text_changed",
    "tool_add_captions",
    "transcription_available",
    "unregister",
)

import importlib
import json
import os
import re
import textwrap
from typing import Any

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty  # pylint: disable=import-error
from bpy.types import Menu, Operator  # pylint: disable=import-error
from bpy_extras.io_utils import ExportHelper, ImportHelper  # pylint: disable=import-error

from . import captions as cap
from .captions import CaptionsError
from .shared import icon as safe_icon
from .wand import scene as _seq

_NAME_RE = re.compile(r"^cap_(\d{4,})$")


# -- the Sequencer's scene and strips ---------------------------------------------------

def seq_scene(context: Any = None) -> Any:
    """The scene the Sequencer shows (``wand.scene``, #504), else the context scene (tools, timers)."""
    ctx = context or bpy.context
    return _seq.sequencer_scene(ctx) or ctx.scene


def _top(ed: Any) -> Any:
    return ed.strips


def _all(ed: Any) -> Any:
    return ed.strips_all


def _left(s: Any) -> int:
    return s.left_handle


def _right(s: Any) -> int:
    return s.right_handle


def _content_start(s: Any) -> float:
    return s.content_start


def _fps(scene: Any) -> float:
    return scene.render.fps / (scene.render.fps_base or 1.0)


def tag_of(strip: Any) -> dict | None:
    value = strip.get(cap.TAG) if hasattr(strip, "get") else None
    if value is None:
        return None
    try:
        return value.to_dict()
    except AttributeError:
        return dict(value) if isinstance(value, dict) else None


def _media_strips(strips: Any) -> list:
    return [s for s in strips if s.type in {'SOUND', 'MOVIE'}]


def media_path(strip: Any) -> str:
    if strip.type == 'MOVIE':
        path, lib = strip.filepath, None
    elif strip.type == 'SOUND' and strip.sound is not None:
        path, lib = strip.sound.filepath, strip.sound.library
    else:
        return ""
    if not path:
        return ""
    return os.path.normpath(bpy.path.abspath(path, library=lib))


def source_key(path: str, kind: str = "media") -> str:
    return "{:s}:{:s}".format(kind, os.path.normcase(os.path.normpath(path)))


def _font() -> Any:
    """Blender's bundled Inter (the same file in Bforartists), or None."""
    folder = bpy.utils.system_resource('DATAFILES', path="fonts")
    path = os.path.join(folder, cap.FONT_FILE) if folder else ""
    if not path or not os.path.isfile(path):
        return None
    try:
        return bpy.data.fonts.load(path, check_existing=True)
    except (RuntimeError, OSError):
        return None


_BLF_IDS: dict[str, int] = {}


def _measure(font: Any) -> Any:
    """``measure(text, px) -> width`` in the strip's font, or None (no font, no blf)."""
    if font is None:
        return None
    path = bpy.path.abspath(font.filepath)
    try:
        import blf  # pylint: disable=import-error
    except ImportError:
        return None
    fid = _BLF_IDS.get(path)
    if fid is None:
        fid = blf.load(path)
        _BLF_IDS[path] = fid
    if fid is None or fid < 0:
        return None

    def measure(text: str, px: float) -> float:
        blf.size(fid, px)
        return float(blf.dimensions(fid, text)[0])
    return measure


# -- transcripts (#375 adds local transcription; until then a transcript file) ---------------

def _transcripts() -> Any:
    try:
        return importlib.import_module(__package__ + ".transcripts")
    except ImportError:
        return None


def transcription_available() -> bool:
    mod = _transcripts()
    return mod is not None and hasattr(mod, "load_for_media")


def load_words(media: str, language: str | None, transcript_path: str = "") -> tuple[list, str | None]:
    """The timed words for *media*: from *transcript_path* (``bfa_coworker.transcript/1``
    JSON) when given, else from local transcription; ``(words, language)``."""
    if transcript_path:
        path = bpy.path.abspath(transcript_path)
        try:
            with open(path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except OSError as ex:
            raise CaptionsError("Couldn't read the transcript {:s}: {!s}".format(path, ex)) from None
        except ValueError:
            raise CaptionsError("The transcript {:s} isn't JSON.".format(os.path.basename(path))) from None
        return cap.transcript_words(data), (data.get("language") if isinstance(data, dict) else None)
    mod = _transcripts()
    if mod is None or not hasattr(mod, "load_for_media"):
        raise CaptionsError(cap.NO_TRANSCRIPTION)
    # #375's load_for_media reads the cached transcript (None: never transcribed); the
    # transcription itself is its transcribe_strip, a long job.
    try:
        try:
            transcript = mod.load_for_media(media, model=None, language=language, fallback_dir=_fallback_dir())
        except TypeError:
            transcript = mod.load_for_media(media, model=None, language=language)
        if transcript is None:
            raise CaptionsError(cap.NOT_TRANSCRIBED.format(os.path.basename(media)))
        words = list(mod.words(transcript))
    except CaptionsError:
        raise
    except Exception as ex:  # pylint: disable=broad-exception-caught
        raise CaptionsError("Couldn't read the transcript: {!s}".format(ex)) from None
    lang = transcript.get("language") if isinstance(transcript, dict) else language
    return words, lang


def _fallback_dir() -> str:
    """Where #375 keeps transcripts of an unsaved .blend (its whisper data folder), or ""."""
    try:
        manager = importlib.import_module(__package__ + ".whisper_manager")
        return str(manager.data_dir())
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


# -- transcribing first: Auto Captions in one action (#375 + #376) -------------------------

def _speech() -> Any:
    try:
        return importlib.import_module(__package__ + ".speech")
    except ImportError:
        return None


def untranscribed(sources: list, language: str | None) -> list:
    """The media strips of *sources* (one per media file) that have no cached transcript."""
    mod = _transcripts()
    if mod is None or not hasattr(mod, "load_for_media"):
        return []
    out, seen = [], set()
    for s in _media_strips(sources):
        path = media_path(s)
        key = source_key(path) if path else ""
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            try:
                found = mod.load_for_media(path, model=None, language=language, fallback_dir=_fallback_dir())
            except TypeError:
                found = mod.load_for_media(path, model=None, language=language)
        except Exception:  # pylint: disable=broad-exception-caught
            found = None
        if found is None:
            out.append(s)
    return out


def transcription_problem(language: str | None = None) -> str:
    """"" when local (or Remote) transcription can run now, else what is missing."""
    sp = _speech()
    if sp is None or not transcription_available():
        return cap.NO_TRANSCRIPTION
    try:
        settings = sp.settings_from_prefs(sp._prefs())  # pylint: disable=protected-access
        return sp.setup_problem(sp.plan(settings, language=language or ""), settings)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return str(ex) or cap.NO_TRANSCRIPTION


# Auto Captions waiting for its transcriptions (#516): {"scene", "uid", "sources", "params",
# "checkers", "errors", "undo", "t0", "state": running / done / error / stopped, "message", "warn"}.
_PENDING: list[dict[str, Any]] = []
_POLL_S = 0.5
STOPPED = "Auto Captions stopped: no captions were made."
NOT_SET_UP = ("{:s} hasn't been transcribed yet. {:s} Set Up Speech in the Auto Captions dialog opens that "
              "page; or pick a transcript file (.json).")


def _show_job(sp: Any, checker: Any) -> None:
    """The transcription in the job row too (#309), like the tool's (best effort)."""
    try:
        from . import long_jobs
        long_jobs.start(getattr(checker, "job_kind", sp.JOB_KIND), getattr(checker, "job_label", "Transcribe"),
                        checker)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _tick_jobs() -> None:
    """End the job row's transcriptions that are over, even with the bridge (whose timer
    does this) stopped; already loaded, never imported here (best effort)."""
    import sys
    api = sys.modules.get(__package__ + ".coworker_api")
    try:
        if api is not None:
            api.jobs_tick()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def transcribe_then_caption(scene: Any, sources: list, params: dict[str, Any], undo: bool = True) -> dict[str, Any]:
    """Start transcribing the untranscribed clips of *sources* (``transcribe_strip``'s job);
    the captions are made when the last one is done (:func:`_poll_pending`).
    *params*: ``style``, ``language``, ``max_chars``, ``max_lines`` of :func:`add_captions`.
    *undo*: the captions push their own undo step when they come (False: the operator waiting
    for them does).  Returns the job: ``state`` "running" with ``message`` what to tell the
    artist now, or "done" when nothing had to wait (the captions are made; no undo step)."""
    sp = _speech()
    todo = untranscribed(sources, params.get("language"))
    checkers, errors = [], []
    for s in todo:
        # Auto Captions' own: Esc stops these, Stop in a chat doesn't (#522).
        res = sp.transcribe_strip(s.name, language=params.get("language") or "",
                                  owner=getattr(sp, "CAPTIONS", "captions"))
        if callable(res):
            checkers.append(res)
            _show_job(sp, res)
        elif isinstance(res, dict) and res.get("status") != "ok":
            errors.append(str(res.get("message") or "Transcribing {:s} failed.".format(s.name)))
    job = {"scene": scene.name, "uid": getattr(scene, "session_uid", None),
           "sources": [s.name for s in _media_strips(sources)], "params": dict(params), "checkers": checkers,
           "errors": errors, "undo": undo, "t0": _clock(), "state": "running", "message": "", "warn": False}
    if not checkers:                    # already done (a background run waits; a cached file)
        job["undo"] = False
        job.update(state="done", message=_caption_job(job, []))
        return job
    _PENDING.append(job)
    if not bpy.app.timers.is_registered(_poll_pending):
        bpy.app.timers.register(_poll_pending, first_interval=_POLL_S)
    job["message"] = "Transcribing {:d} clip{:s} first; the captions follow when it's done.".format(
        len(checkers), "" if len(checkers) == 1 else "s")
    return job


def _clock() -> float:
    import time
    return time.monotonic()


def _caption_job(job: dict[str, Any], results: list) -> str:
    """The captions of a finished *job* (main thread); what to tell the artist."""
    errors = list(job["errors"]) + [str(r.get("message") or "") for r in results
                                    if isinstance(r, dict) and r.get("status") != "ok"]
    scene = bpy.data.scenes.get(job["scene"])
    if scene is not None and job.get("uid") is not None and getattr(scene, "session_uid", None) != job["uid"]:
        scene = None                    # another file was loaded meanwhile: its scene has the same name
    ed = getattr(scene, "sequence_editor", None)
    if ed is None:
        raise CaptionsError("The scene “{:s}” is gone; no captions were made.".format(job["scene"]))
    pool = _all(ed)
    sources = [s for s in (pool.get(n) for n in job["sources"]) if s is not None]
    if not sources:
        raise CaptionsError("The clips to caption are gone; no captions were made.")
    p = job["params"]
    try:
        result = add_captions(scene, sources, p.get("style", "CLEAN"), p.get("language"), "",
                              int(p.get("max_chars", 0) or 0), int(p.get("max_lines", 0) or 0))
    except CaptionsError as ex:
        raise CaptionsError(" ".join([str(ex)] + [e for e in errors if e and e not in str(ex)])) from None
    if job.get("undo", True):
        try:
            bpy.ops.ed.undo_push(message="Auto Captions")
        except RuntimeError:
            pass
    if errors:
        result["note"] = " ".join(errors + ([result["note"]] if "note" in result else []))
    job["warn"] = bool(result.get("note"))
    return "Auto Captions: " + _summary(result)


def _poll_pending() -> float | None:
    """The timer: a job whose transcriptions are all over gets its captions.  Nothing pops
    up: the operator waiting for the job reports it (status bar, Info), else the console."""
    _tick_jobs()
    for job in list(_PENDING):
        results = [check() for check in job["checkers"]]
        if any(r is None for r in results):
            continue
        _PENDING[:] = [j for j in _PENDING if j is not job]
        if any(_was_stopped(r) for r in results):
            # Stop Speech Server (Preferences) while it waited: stopped, not an error, and no
            # captions from what was there before (#522).
            job.update(state="stopped", message=STOPPED)
            print("[Coworker] " + job["message"])
            continue
        try:
            job.update(state="done", message=_caption_job(job, results))
        except CaptionsError as ex:
            job.update(state="error", message="Auto Captions: {!s}".format(ex))
        except Exception as ex:  # pylint: disable=broad-exception-caught
            job.update(state="error", message="Auto Captions failed: {!s}".format(ex))
        print("[Coworker] " + job["message"])
    return _POLL_S if _PENDING else None


def _was_stopped(result: Any) -> bool:
    """A transcription's answer says it was stopped (``speech.was_stopped``)."""
    test = getattr(_speech(), "was_stopped", None)
    try:
        return bool(test(result)) if callable(test) else False
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _unregister_timer() -> None:
    try:
        if bpy.app.timers.is_registered(_poll_pending):
            bpy.app.timers.unregister(_poll_pending)
    except (AttributeError, ValueError):
        pass


def stop_pending() -> int:
    """Stop every Auto Captions still waiting for its transcription (Esc, a file load,
    unregister): no captions, no timer, its transcriptions stopped (they save nothing; this
    window's whisper-server stops unless another job still needs it) and their job rows ended.
    A transcription a chat's turn started carries on (#522).  How many were waiting."""
    jobs = list(_PENDING)
    _PENDING.clear()
    _unregister_timer()
    for job in jobs:
        job.update(state="stopped", message=STOPPED)
    if not jobs:
        return 0
    sp = _speech()
    try:
        if sp is not None and hasattr(sp, "stop_jobs"):
            sp.stop_jobs(getattr(sp, "CAPTIONS", "captions"))
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    # Their checkers answer "cancelled" now, so the job rows end on this tick: not by kind
    # (long_jobs.cancelled), which ended a chat's transcription row too (#522).
    _tick_jobs()
    print("[Coworker] " + STOPPED)
    return len(jobs)


def job_status_text(job: dict[str, Any]) -> str:
    """The status bar while Auto Captions waits: what whisper does, the time, how to stop."""
    text = ""
    try:
        from . import long_jobs
        rows = [j for j in long_jobs.running() if j.get("kind") == "TRANSCRIBE"]
        text = str(rows[0].get("progress_text") or "") if rows else ""
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    # Its first part ("Starting whisper-server (small, CPU) on port 8481 ... The first CUDA start ..."),
    # short: the job row cuts it at 120 characters, maybe mid-word.
    text = textwrap.shorten(text.split(" ...")[0], 90, placeholder="...") if text else ""
    if not text:
        text = "Transcribing " + ", ".join(job["sources"][:2]) + ("..." if len(job["sources"]) > 2 else "")
    return "Auto Captions: {:s}, {:d} s. Esc to stop.".format(text, int(_clock() - job["t0"]))


# -- making and removing caption strips ---------------------------------------------------

def _drop_fcurves(scene: Any, names: list[str]) -> None:
    """The animation of removed caption strips (their paths are name-keyed).

    Blender 5.2 drops a removed strip's fcurves itself, but the evaluated copy of the action
    keeps them until the action is tagged: a new strip with the same name (``cap_0001`` again)
    then got the old Bold Pop font size back on the next frame change (live check, #376)."""
    ad = scene.animation_data
    action = ad.action if ad is not None else None
    if action is None or not names:
        return
    prefixes = tuple('{:s}["{:s}"]'.format(base, bpy.utils.escape_identifier(n))
                     for n in names for base in ("sequence_editor.strips_all",))
    bags = []
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                bags.append(bag.fcurves)
    for fcurves in bags:
        for fc in [fc for fc in fcurves if fc.data_path.startswith(prefixes)]:
            fcurves.remove(fc)
    try:
        action.update_tag()
    except (AttributeError, RuntimeError):
        pass


def _owned(ed: Any, keys: set[str]) -> list:
    return [s for s in _top(ed) if cap.owned_by(tag_of(s), keys)]


def remove_captions(scene: Any, keys: set[str]) -> int:
    """Remove the caption strips made from *keys* (and their animation); the number of lines."""
    ed = scene.sequence_editor
    if ed is None:
        return 0
    doomed = _owned(ed, keys)
    lines = sum(1 for s in doomed if (tag_of(s) or {}).get("role") == "line")
    names = [s.name for s in doomed]
    top = _top(ed)
    for s in doomed:
        top.remove(s)
    _drop_fcurves(scene, names)
    return lines


def _next_number(ed: Any, skip: set[str]) -> int:
    best = 0
    for s in _all(ed):
        m = _NAME_RE.match(s.name)
        if m and s.name not in skip:
            best = max(best, int(m.group(1)))
    return best


def _text_strip(ed: Any, name: str, channel: int, fs: int, fe: int, text: str, style: dict, font: Any) -> Any:
    top = _top(ed)
    s = top.new_effect(name, 'TEXT', channel, fs, length=max(1, fe - fs))
    s.text = text
    for key, value in style["props"].items():
        try:
            setattr(s, key, value)
        except (AttributeError, TypeError, ValueError):
            pass
    if font is not None:
        s.font = font
    if style["highlight"]:
        s.wrap_width = 1.0          # the line is laid out by us: no wrap that would move the words
    s.select = False
    return s


def _add_highlights(scene: Any, line: Any, framed: dict, style: dict, font: Any, measure: Any) -> int:
    """The word strips of a Word-by-word caption on the channel above *line*."""
    ed = scene.sequence_editor
    lines = framed.get("lines") or [line.text]
    if measure is None or len(lines) != 1 or not framed.get("words"):
        return 0
    px = float(line.font_size)
    words = [w["w"].upper() if style["uppercase"] else w["w"] for w in framed["words"]]
    text = " ".join(words)
    width = measure(text, px)
    x0, y0 = line.location[0], line.location[1]
    made = 0
    tag = tag_of(line) or {}
    for i, a, b in cap.highlight_windows(framed):
        prefix = " ".join(words[:i]) + " " if i else ""
        x = cap.highlight_left(x0, width, measure(prefix, px) if prefix else 0.0, scene.render.resolution_x)
        h = _text_strip(ed, "{:s}_w{:02d}".format(line.name, i + 1), line.channel + 1, a, b, words[i], style, font)
        h.color = tuple(style["highlight"])
        h.anchor_x, h.alignment_x = 'LEFT', 'LEFT'
        h.location = (x, y0)
        h.wrap_width = 1.0
        h[cap.TAG] = {"source": tag.get("source", ""), "style": style["key"], "role": "word", "line": line.name}
        made += 1
    return made


def _line_tag(source: str, style: str, chars: int, lines: int, scene: Any, frame_start: int,
              words: Any = None) -> dict[str, Any]:
    """A caption line's tag: also the frame it was laid out for and its words' first frames
    (from the line's start), so Make Vertical can lay it out again (#497)."""
    tag = {"source": source, "style": style, "role": "line", "chars": int(chars), "lines": int(lines),
           "frame": [int(scene.render.resolution_x), int(scene.render.resolution_y)]}
    offsets = [int(w["frame"]) - int(frame_start) for w in words or () if "frame" in w]
    if offsets:
        tag["word_frames"] = offsets
    return tag


def _occupied(ed: Any, skip: set[str]) -> dict[int, list[tuple[float, float]]]:
    out: dict[int, list[tuple[float, float]]] = {}
    for s in _top(ed):
        if s.name not in skip:
            out.setdefault(s.channel, []).append((_left(s), _right(s)))
    return out


def _place(scene: Any, groups: list[tuple[str, list[dict]]], style: dict, chars: int, lines: int,
           above: int) -> dict[str, Any]:
    """Text strips for framed captions: one per caption (and the word strips), on the
    *Captions* channel above *above*; the earlier captions of the same sources go."""
    ed = scene.sequence_editor
    framed_all = [c for _key, framed in groups for c in framed]
    if not framed_all:
        raise CaptionsError("No words fall inside the visible part of the strip.")
    keys = {key for key, _framed in groups}
    doomed = {s.name for s in _owned(ed, keys)}
    span = (min(c["frame_start"] for c in framed_all), max(c["frame_end"] for c in framed_all))
    need = 2 if style["highlight"] else 1
    names = {i: ch.name for i, ch in enumerate(ed.channels)}
    channel = cap.pick_channel(_occupied(ed, doomed), names, above, span, need)
    if channel is None:
        raise CaptionsError("There's no free channel above the clip for the captions.")
    replaced = remove_captions(scene, keys)
    ed.channels[channel].name = cap.CHANNEL_NAME
    if need == 2:
        ed.channels[channel + 1].name = cap.HIGHLIGHT_CHANNEL_NAME
    font = _font()
    measure = _measure(font) if style["highlight"] else None
    number = _next_number(ed, set())
    made = words = 0
    for key, framed in groups:
        for c in framed:
            number += 1
            text = c["text"].upper() if style["uppercase"] else c["text"]
            s = _text_strip(ed, "cap_{:04d}".format(number), channel, c["frame_start"], c["frame_end"], text,
                            style, font)
            s[cap.TAG] = _line_tag(key, style["key"], chars, lines, scene, c["frame_start"], c.get("words"))
            made += 1
            if style["highlight"]:
                words += _add_highlights(scene, s, c, style, font, measure)
            if style["pop"] and c["frame_end"] - c["frame_start"] >= 5:
                size = s.font_size
                s.font_size = size * 0.78
                s.keyframe_insert("font_size", frame=c["frame_start"])
                s.font_size = size
                s.keyframe_insert("font_size", frame=c["frame_start"] + 3)
    result = {"status": "ok", "lines_created": made, "highlight_strips": words, "channel": channel,
              "first_frame": span[0], "last_frame": span[1] - 1, "style": style["label"], "replaced": replaced}
    if style["highlight"] and measure is None:
        result["note"] = "The bundled font couldn't be measured here, so there are no word highlights."
    return result


def _limits(style: dict, scene: Any, max_chars: int, max_lines: int) -> tuple[int, int]:
    lines = 1 if style["highlight"] else (max_lines or style["max_lines"])
    chars = max_chars or cap.default_max_chars(style["key"], scene.render.resolution_x, scene.render.resolution_y)
    return int(chars), (1 if lines <= 1 else 2)


def add_captions(scene: Any, sources: list, style: str = "CLEAN", language: str | None = None,
                 transcript_path: str = "", max_chars: int = 0, max_lines: int = 0) -> dict[str, Any]:
    """Caption *sources* (sound / movie strips) in *style*; the result for the artist / the tool."""
    ed = scene.sequence_editor
    if ed is None:
        raise CaptionsError("The scene “{:s}” has no strips.".format(scene.name))
    st = cap.resolve_style(style, scene.render.resolution_x, scene.render.resolution_y)
    chars, lines = _limits(st, scene, max_chars, max_lines)
    unique: dict[str, tuple[Any, str]] = {}
    for s in _media_strips(sources):
        path = media_path(s)
        if path:
            unique.setdefault(source_key(path), (s, path))     # a movie and its sound: once
    if not unique:
        raise CaptionsError("Select a sound or movie strip to caption.")
    notes = []
    if transcript_path and len(unique) > 1:
        active = ed.active_strip
        key = next((k for k, (s, _p) in unique.items() if s == active), next(iter(unique)))
        unique = {key: unique[key]}
        notes.append("A transcript file captions one clip: {:s}.".format(unique[key][0].name))
    groups, errors, langs = [], [], []
    fps = _fps(scene)
    for key, (s, path) in unique.items():
        try:
            words, lang = load_words(path, language, transcript_path)
        except CaptionsError as ex:
            errors.append(str(ex))
            continue
        caps = cap.build_captions(words, chars, lines)
        groups.append((key, cap.to_frames(caps, fps, _content_start(s), _left(s), _right(s))))
        langs.append(lang or language or "")
    if not groups:
        raise CaptionsError(errors[0] if errors else "There are no words to caption.")
    above = max(s.channel for s in _media_strips(sources))      # above every selected clip, kept or not
    result = _place(scene, groups, st, chars, lines, above)
    result.update(sources=[s.name for s, _p in unique.values()], language=next((x for x in langs if x), None),
                  max_chars=chars, max_lines=lines)
    if errors or notes:
        result["note"] = " ".join(notes + errors + ([result["note"]] if "note" in result else []))
    return result


def _read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def import_srt(scene: Any, path: str, style: str = "CLEAN", max_chars: int = 0, max_lines: int = 0,
               above: int | None = None) -> dict[str, Any]:
    """Styled captions at the cue times of the SRT file *path* (frame = scene start + t x fps)."""
    path = bpy.path.abspath(path)
    try:
        text = _read_text(path)
    except OSError as ex:
        raise CaptionsError("Couldn't read {:s}: {!s}".format(path, ex)) from None
    cues, skipped = cap.parse_srt(text)
    if not cues:
        raise CaptionsError("No captions in {:s}{:s}.".format(
            os.path.basename(path), " ({:d} broken cues skipped)".format(skipped) if skipped else ""))
    if scene.sequence_editor is None:
        scene.sequence_editor_create()
    st = cap.resolve_style(style, scene.render.resolution_x, scene.render.resolution_y)
    chars, lines = _limits(st, scene, max_chars, max_lines)
    caps = []
    for cue in cues:
        flat = " ".join(cue["text"].split())
        timed = cap.even_words(flat, cue["start"], cue["end"])
        if st["highlight"]:         # one-line captions inside the cue, the words spread over it
            caps.extend(cap.build_captions(timed, chars, 1, max_duration=1e9, min_duration=0.0))
        else:
            cue_lines = cue["text"].split("\n")
            caps.append({"text": cue["text"], "lines": cue_lines, "start": cue["start"], "end": cue["end"],
                         "words": timed})
    framed = cap.to_frames(caps, _fps(scene), scene.frame_start)
    ed = scene.sequence_editor
    if above is None:
        chosen = [s.channel for s in _top(ed) if s.select]
        above = max(chosen) if chosen else 0
    result = _place(scene, [(source_key(path, "srt"), framed)], st, chars, lines, above)
    result.update(cues=len(cues), skipped=skipped, file=path)
    return result


def caption_lines(scene: Any, selected_only: bool = False) -> list:
    """The caption line strips (tagged, or any text strip on a *Captions* channel), in time order."""
    ed = scene.sequence_editor
    if ed is None:
        return []
    out = []
    for s in _top(ed):
        if s.type != 'TEXT' or (selected_only and not s.select):
            continue
        role = (tag_of(s) or {}).get("role")
        if role == "word":
            continue
        on_channel = 0 <= s.channel < len(ed.channels) and ed.channels[s.channel].name == cap.CHANNEL_NAME
        if role == "line" or on_channel:
            out.append(s)
    out.sort(key=lambda s: (_left(s), s.channel))
    return out


def caption_cues(scene: Any, selected_only: bool = False) -> list[tuple[float, float, str]]:
    """``(start, end, text)`` in seconds from the scene start, for SRT."""
    fps = _fps(scene)
    return [((_left(s) - scene.frame_start) / fps, (_right(s) - scene.frame_start) / fps, s.text)
            for s in caption_lines(scene, selected_only)]


# -- another frame size: Make Vertical (#497) ----------------------------------------------

# What a caption piece made by a split takes over from its caption (colours and look the
# artist may have changed; the layout comes from the style).
_LOOK = ("color", "use_bold", "use_italic", "use_outline", "outline_color", "outline_width", "use_shadow",
         "shadow_color", "shadow_offset", "shadow_angle", "shadow_blur", "use_box", "box_color", "box_margin",
         "box_roundness", "anchor_x", "anchor_y", "alignment_x", "blend_type", "blend_alpha", "mute")


def _font_size_curves(scene: Any, name: str) -> list:
    """The fcurves animating strip *name*'s font size (Bold Pop's pop) in *scene*'s action."""
    ad = scene.animation_data
    action = ad.action if ad is not None else None
    if action is None:
        return []
    paths = {'{:s}["{:s}"].font_size'.format(base, bpy.utils.escape_identifier(name))
             for base in ("sequence_editor.strips_all",)}
    bags = []
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                bags.append(bag.fcurves)
    return [fc for fcurves in bags for fc in fcurves if fc.data_path in paths]


def _steady_size(scene: Any, strip: Any) -> float:
    """The font size a caption shows once it's up: a popping caption's last key, else its size."""
    for fc in _font_size_curves(scene, strip.name):
        points = sorted(fc.keyframe_points, key=lambda k: k.co[0])
        if points:
            return float(points[-1].co[1])
    return float(strip.font_size)


def _scale_font_keys(scene: Any, strip: Any, ratio: float) -> None:
    for fc in _font_size_curves(scene, strip.name):
        for k in fc.keyframe_points:
            k.co[1] *= ratio
            k.handle_left[1] *= ratio
            k.handle_right[1] *= ratio
        fc.update()


def _set_right(strip: Any, frame: int) -> None:
    strip.right_handle = frame


def _look_of(scene: Any, strip: Any) -> dict[str, Any]:
    tr = getattr(strip, "transform", None)
    look = {"location": tuple(strip.location), "font_size": _steady_size(scene, strip),
            "wrap_width": strip.wrap_width, "anchor_x": strip.anchor_x, "anchor_y": strip.anchor_y}
    if tr is not None:
        look["transform"] = (tr.offset_x, tr.offset_y, tr.scale_x, tr.scale_y, tr.rotation)
    return look


def relayout_captions(scene: Any, old_res: tuple[int, int] | None = None) -> dict[str, Any]:
    """Every Coworker caption line of *scene* laid out again for the scene's output size (Make
    Vertical's 9:16 copy): the style's size, place and line breaks for that frame
    (:func:`captions.relayout`); its words, timing, colours and channel stay.  Words that no
    longer fit the style's lines become several captions inside the same time (each word at
    its own time); Word-by-word highlights are made again over the new lines.

    A caption moved, resized or transformed by hand (:func:`captions.hand_edits`) is left as it
    is and counted.  *old_res*: the frame captions made before #497 were laid out for (they
    don't store it).  Returns ``{"laid_out", "split", "left", "left_names", "message"}``."""
    out: dict[str, Any] = {"laid_out": 0, "split": 0, "left": 0, "left_names": [], "message": ""}
    ed = scene.sequence_editor
    if ed is None:
        return out
    res = (int(scene.render.resolution_x), int(scene.render.resolution_y))
    top = _top(ed)
    lines, words_of = [], {}
    for s in top:
        tag = tag_of(s) or {}
        if s.type == 'TEXT' and tag.get("role") == "line":
            lines.append(s)
        elif tag.get("role") == "word":
            words_of.setdefault(tag.get("line"), []).append(s)
    lines.sort(key=lambda s: (_left(s), s.channel))
    number = _next_number(ed, set())
    for line in lines:
        tag = tag_of(line) or {}
        key = cap.style_key(tag.get("style"))
        made_for = tuple(int(v) for v in (tag.get("frame") or old_res or res))
        if made_for == res:
            continue                                    # already laid out for this frame
        if key is None or cap.hand_edits(_look_of(scene, line), key, *made_for):
            out["left"] += 1
            out["left_names"].append(line.name)
            continue
        fs, fe = _left(line), _right(line)
        old_words = sorted(words_of.get(line.name, ()), key=lambda w: w.name)
        frames = None
        if old_words and len(old_words) == len(line.text.split()):
            frames = [_left(w) for w in old_words]
        elif tag.get("word_frames"):
            frames = [fs + int(v) for v in tag["word_frames"]]
        lay = cap.relayout(line.text, key, res[0], res[1], fs, fe, int(tag.get("chars") or 0),
                           int(tag.get("lines") or 0), made_for, frames)
        st, pieces = lay["style"], lay["pieces"]
        highlight = tuple(old_words[0].color) if old_words else st["highlight"]
        names = [w.name for w in old_words]
        for w in old_words:
            top.remove(w)
        if names:
            _drop_fcurves(scene, names)
        props = st["props"]
        old_size = _steady_size(scene, line)
        font = line.font
        for i, piece in enumerate(pieces):
            if i == 0:
                s = line
                if len(pieces) > 1:
                    _set_right(s, piece["frame_end"])
                s.text = piece["text"]
                s.font_size = props["font_size"]
                if old_size > 0:
                    _scale_font_keys(scene, s, props["font_size"] / old_size)
            else:
                number += 1
                s = _text_strip(ed, "cap_{:04d}".format(number), line.channel, piece["frame_start"],
                                piece["frame_end"], piece["text"], st, font)
                for attr in _LOOK:
                    try:
                        setattr(s, attr, getattr(line, attr))
                    except (AttributeError, TypeError, ValueError):
                        pass
                if st["pop"] and piece["frame_end"] - piece["frame_start"] >= 5:
                    size = s.font_size
                    s.font_size = size * 0.78
                    s.keyframe_insert("font_size", frame=piece["frame_start"])
                    s.font_size = size
                    s.keyframe_insert("font_size", frame=piece["frame_start"] + 3)
            s.location = props["location"]
            s.wrap_width = 1.0 if st["highlight"] else props["wrap_width"]
            s[cap.TAG] = _line_tag(tag.get("source", ""), key, lay["chars"], lay["lines"], scene,
                                   piece["frame_start"], piece["words"])
            if st["highlight"] and old_words:
                style = dict(st, uppercase=False, highlight=highlight)     # the text already is as shown
                _add_highlights(scene, s, piece, style, font or _font(), _measure(font or _font()))
        out["laid_out"] += 1
        out["split"] += 1 if len(pieces) > 1 else 0
    if out["laid_out"] or out["left"]:
        out["message"] = cap.relayout_report(out["laid_out"], out["left"], out["split"])
    return out


# -- Translate (#42 for many captions) ---------------------------------------------------

def translate_request(scene: Any, strips: list) -> tuple[str, str, str]:
    """``(path, current, what)`` for ``bfacw.translate`` over *strips*."""
    path = cap.TRANSLATE_PREFIX + json.dumps({"scene": scene.name, "strips": [s.name for s in strips]})
    what = ("the {:d} captions below (one per line, each starting with its number in brackets: keep every "
            "number, and give one translated caption per line)").format(len(strips))
    return path, cap.batch_text([s.text for s in strips]), what


def set_caption_text(scene: Any, strip: Any, text: str) -> None:
    """*text* into a caption strip, laid out for its style; its word strips follow."""
    tag = tag_of(strip) or {}
    style = cap.STYLES.get(tag.get("style") or "", cap.STYLES["CLEAN"])
    if style["highlight"]:      # one line: the word strips are placed along it
        new = " ".join(text.split())
    else:
        new = "\n".join(cap.layout_lines(text.split(), int(tag.get("chars") or 36), int(tag.get("lines") or 2),
                                         force=True) or [text])
    strip.text = new.upper() if style["uppercase"] else new
    text_changed(strip, scene)


def text_changed(strip: Any, scene: Any = None) -> None:
    """A Word-by-word caption's text changed (Translate): its word strips are made again,
    the words spread over the line's time."""
    tag = tag_of(strip) or {}
    if tag.get("role") != "line":
        return
    key = tag.get("style") or ""
    if not (cap.STYLES.get(key) or {}).get("highlight"):
        return
    scene = scene or strip.id_data
    ed = scene.sequence_editor
    top = _top(ed)
    old = [s for s in top if (tag_of(s) or {}).get("line") == strip.name]
    names = [s.name for s in old]
    for s in old:
        top.remove(s)
    _drop_fcurves(scene, names)
    st = cap.resolve_style(key, scene.render.resolution_x, scene.render.resolution_y)
    text = " ".join(strip.text.split())
    if strip.text != text:
        strip.text = text           # one line, or the word strips would sit off their words
    left, right = _left(strip), _right(strip)
    timed = [dict(w, frame=int(round(w["start"]))) for w in cap.even_words(text, left, right)]
    framed = {"frame_start": left, "frame_end": right, "lines": [text], "words": timed}
    style = dict(st, uppercase=False)       # the text already is as it should be shown
    font = strip.font or _font()
    _add_highlights(scene, strip, framed, style, font, _measure(font))


def apply_translation(path: str, reply: str) -> tuple[int, int]:
    """Write a :func:`translate_request` reply back, one undo step; ``(written, missing)``."""
    data = json.loads(path[len(cap.TRANSLATE_PREFIX):])
    scene = bpy.data.scenes.get(data.get("scene", ""))
    names = list(data.get("strips") or [])
    if scene is None or scene.sequence_editor is None:
        return 0, len(names)
    got = cap.parse_batch_reply(reply, len(names))
    strips = _all(scene.sequence_editor)
    written = missing = 0
    for i, name in enumerate(names, 1):
        strip, text = strips.get(name), got.get(i)
        if strip is None or not text:
            missing += 1
            continue
        set_caption_text(scene, strip, text)
        written += 1
    if missing:
        print("[Coworker] Translate Captions: {:d} of {:d} captions came back without a translation".format(
            missing, len(names)))
    try:
        bpy.ops.ed.undo_push(message="Translate Captions")
    except RuntimeError:
        pass
    return written, missing


# -- the MCP tool ----------------------------------------------------------------------

def _turn_owns_undo() -> bool:
    """A Coworker chat turn runs this tool call (#501): the turn's own undo step covers it."""
    try:
        from . import coworker_api
    except ImportError:
        return False
    return coworker_api.turn_owns_undo()


def tool_add_captions(strips: Any = None, style: str = "CLEAN", language: str | None = None,
                      transcript_path: str | None = None) -> dict[str, Any]:
    """``add_captions`` (MCP): a result dict, ``status`` ``"ok"`` or ``"error"`` + ``message``."""
    try:
        names, key, lang = cap.check_tool_params(strips, style, language)
        scene = seq_scene()
        ed = scene.sequence_editor
        if ed is None:
            raise CaptionsError("The scene “{:s}” has no strips.".format(scene.name))
        if names:
            pool = _all(ed)
            missing = [n for n in names if pool.get(n) is None]
            if missing:
                raise CaptionsError("No strip named {:s}.".format(", ".join(repr(n) for n in missing)))
            found = [pool.get(n) for n in names]
            wrong = [s.name for s in found if s.type not in {'SOUND', 'MOVIE'}]
            if wrong:
                raise CaptionsError("Only sound and movie strips can be captioned: {:s}.".format(", ".join(wrong)))
        else:
            found = [s for s in _media_strips(_top(ed)) if s.select]
            if not found:
                raise CaptionsError("Name the sound or movie strips to caption (strips=[...]), or select them.")
        result = add_captions(scene, found, key, lang, transcript_path or "")
        result["message"] = "{:d} caption lines on channel {:d} ({:s}), frames {:d}-{:d}.".format(
            result["lines_created"], result["channel"], result["style"], result["first_frame"], result["last_frame"])
        try:
            if not _turn_owns_undo():       # from a chat turn its own step covers it (#501)
                bpy.ops.ed.undo_push(message="Add Captions")
        except RuntimeError:
            pass
        return result
    except CaptionsError as ex:
        return {"status": "error", "message": str(ex)}


# -- operators and menus ---------------------------------------------------------------

_STYLE_ITEMS = [(k, s["label"], s["description"]) for k, s in cap.STYLES.items()]
_LINES_ITEMS = [("STYLE", "Style's", "As many lines as the style uses"), ("1", "1", "One line"),
                ("2", "2", "Up to two lines")]


def _language_items() -> list[tuple[str, str, str]]:
    from .preferences import _LANGUAGES
    items = [("auto", "Detect", "Let the transcription find the language")]
    items += [(code, name, "Spoken in " + name) for code, name in _LANGUAGES]
    return items


_LANGUAGE_ITEMS = _language_items()


def _lines_value(value: str) -> int:
    return 0 if value == "STYLE" else int(value)


class BFACW_OT_auto_captions(Operator):  # type: ignore[misc]
    """Caption the selected sound or movie strips: styled text strips on a Captions channel, in sync with the speech"""
    bl_idname = "bfacw.auto_captions"
    bl_label = "Auto Captions"
    bl_options = {'REGISTER', 'UNDO'}

    style: EnumProperty(name="Style", items=_STYLE_ITEMS, default="CLEAN")  # type: ignore[valid-type]
    language: EnumProperty(name="Language", items=_LANGUAGE_ITEMS, default="auto")  # type: ignore[valid-type]
    max_chars: IntProperty(  # type: ignore[valid-type]
        name="Max Characters", default=0, min=0, max=80,
        description="Characters per line (0: what fits the frame in this style)")
    lines: EnumProperty(name="Lines", items=_LINES_ITEMS, default="STYLE")  # type: ignore[valid-type]
    transcript_path: StringProperty(  # type: ignore[valid-type]
        name="Transcript", subtype='FILE_PATH', default="",
        description="A transcript file (.json, bfa_coworker.transcript/1) to caption from instead of transcribing")

    @classmethod
    def poll(cls, context: Any) -> bool:
        if not _seq.poll(cls, context):        # the Sequencer's scene, with strips (#504)
            return False
        ed = getattr(seq_scene(context), "sequence_editor", None)
        if ed is None or not any(s.select for s in _media_strips(_top(ed))):
            cls.poll_message_set("Select a sound or movie strip first")
            return False
        return True

    def invoke(self, context: Any, _event: Any) -> set[str]:
        _DIALOG.clear()                 # transcribed or set up since the last time: look again
        return context.window_manager.invoke_props_dialog(self, width=380, title="Auto Captions",
                                                          confirm_text="Add Captions")

    def draw(self, context: Any) -> None:
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "style")
        layout.prop(self, "language")
        row = layout.row()
        row.prop(self, "max_chars")
        if self.max_chars == 0:
            scene = seq_scene(context)
            row.label(text="Auto: {:d}".format(cap.default_max_chars(
                self.style, scene.render.resolution_x, scene.render.resolution_y)))
        sub = layout.row()
        sub.enabled = not cap.STYLES[self.style]["highlight"]
        sub.prop(self, "lines")
        layout.prop(self, "transcript_path")
        if not self.transcript_path and not transcription_available():
            col = layout.column(align=True)
            col.label(text="Transcription isn't in this build yet:", icon=safe_icon('INFO'))
            col.label(text="pick a transcript file, or use Import SRT.")
        elif not self.transcript_path:
            todo, problem = _dialog_state(context, self.language)
            if todo and problem:
                _wrapped(layout.column(align=True), "{:s} isn't transcribed yet. {:s}".format(
                    todo[0], problem), 'ERROR')
                op = layout.operator("bfacw.open_addon_prefs", text="Set Up Speech...",
                                     icon=safe_icon('PREFERENCES'))
                op.pref_tab = "SPEECH"
            elif todo:
                _wrapped(layout.column(align=True), "{:s} isn't transcribed yet: Auto Captions transcribes it first "
                         "(the status bar shows how far; Esc stops).".format(todo[0]), 'INFO')

    def execute(self, context: Any) -> set[str]:
        scene = seq_scene(context)
        sources = [s for s in _media_strips(_top(scene.sequence_editor)) if s.select]
        language = None if self.language == "auto" else self.language
        todo = [] if self.transcript_path else untranscribed(sources, language)
        if todo:
            problem = transcription_problem(language)
            if problem and problem != cap.NO_TRANSCRIPTION:
                # Speech isn't set up: say what is missing; the dialog has the button that sets it up.
                self.report({'ERROR'}, NOT_SET_UP.format(todo[0].name, problem))
                return {'CANCELLED'}
            if not problem:
                return self._transcribe_first(context, scene, sources, language)
        try:
            result = add_captions(scene, sources, self.style, language,
                                  self.transcript_path, self.max_chars, _lines_value(self.lines))
        except CaptionsError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'WARNING'} if result.get("note") else {'INFO'}, _summary(result))
        return {'FINISHED'}

    # -- transcribing first (#516) --
    def _transcribe_first(self, context: Any, scene: Any, sources: list, language: str | None) -> set[str]:
        """Start the transcription; with a window, wait for the captions here (modal: Esc stops,
        the status bar shows the progress, this operator's undo step and report hold the
        captions).  Without one (a script), or from Adjust Last Operation (no modal there), a
        timer makes them on its own."""
        params = {"style": self.style, "language": language, "max_chars": self.max_chars,
                  "max_lines": _lines_value(self.lines)}
        # options.is_repeat: the redo panel.  Not Operator.is_repeat(), which is true whenever the
        # last operator run was Auto Captions too.
        options = getattr(self, "options", None)
        redo = bool(getattr(options, "is_repeat", False) or getattr(options, "is_repeat_last", False))
        wait = getattr(context, "window", None) is not None and not bpy.app.background and not redo
        try:
            job = transcribe_then_caption(scene, sources, params, undo=not wait)
        except CaptionsError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        if job["state"] != "running" or not wait:
            self.report({'WARNING'} if job["warn"] else {'INFO'}, job["message"])
            return {'FINISHED'}
        self._job = job
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        self._status(context, job_status_text(job))
        return {'RUNNING_MODAL'}

    def modal(self, context: Any, event: Any) -> set[str]:
        job = self._job
        if job["state"] == "running" and event.type == 'ESC' and event.value == 'PRESS':
            stop_pending()
        if job["state"] == "running":
            if event.type == 'TIMER':
                self._status(context, job_status_text(job))
            return {'PASS_THROUGH'}
        self._end(context)
        if job["state"] == "done":
            self.report({'WARNING'} if job["warn"] else {'INFO'}, job["message"])
            return {'FINISHED'}
        self.report({'WARNING'} if job["state"] == "stopped" else {'ERROR'}, job["message"])
        return {'CANCELLED'}

    def cancel(self, context: Any) -> None:
        """Blender ends the wait (a file load, the window closes): no captions come later."""
        if getattr(self, "_job", None) is not None and self._job["state"] == "running":
            stop_pending()
        self._end(context)

    def _end(self, context: Any) -> None:
        timer, self._timer = getattr(self, "_timer", None), None
        if timer is not None:
            try:
                context.window_manager.event_timer_remove(timer)
            except (AttributeError, ReferenceError, RuntimeError):
                pass
        self._status(context, None)

    @staticmethod
    def _status(context: Any, text: str | None) -> None:
        try:
            context.workspace.status_text_set(text)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass


def _wrapped(col: Any, text: str, icon: str) -> None:
    """*text* as labels the dialog's width holds, the icon on the first."""
    for i, line in enumerate(textwrap.wrap(text, 58)):
        col.label(text=line, icon=safe_icon(icon) if i == 0 else 'BLANK1')


# What the dialog shows about transcription, worked out once per selection and language:
# {"key": (scene, clips, language), "todo": [clip names], "problem": "..."}.
_DIALOG: dict[str, Any] = {}


def _dialog_state(context: Any, language: str) -> tuple[list[str], str]:
    """``(untranscribed clips, what speech is missing or "")`` for the dialog (#516)."""
    scene = seq_scene(context)
    ed = getattr(scene, "sequence_editor", None)
    sources = [s for s in _media_strips(_top(ed)) if s.select] if ed is not None else []
    lang = None if language == "auto" else language
    key = (scene.name, tuple(s.name for s in sources), lang)
    if _DIALOG.get("key") != key:
        todo = [s.name for s in untranscribed(sources, lang)]
        problem = transcription_problem(lang) if todo else ""
        _DIALOG.update(key=key, todo=todo, problem="" if problem == cap.NO_TRANSCRIPTION else problem)
    return list(_DIALOG["todo"]), _DIALOG["problem"]


def _summary(result: dict) -> str:
    text = "{:d} captions on channel {:d}".format(result["lines_created"], result["channel"])
    if result.get("replaced"):
        text += " (replacing {:d})".format(result["replaced"])
    if result.get("skipped"):
        text += "; {:d} broken cues skipped".format(result["skipped"])
    if result.get("note"):
        text += ". " + result["note"]
    return text


class BFACW_OT_captions_import_srt(Operator, ImportHelper):  # type: ignore[misc]
    """Import an SRT subtitle file as styled caption strips at its cue times"""
    bl_idname = "bfacw.captions_import_srt"
    bl_label = "Import SRT"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".srt"
    filter_glob: StringProperty(default="*.srt", options={'HIDDEN'})  # type: ignore[valid-type]
    style: EnumProperty(name="Style", items=_STYLE_ITEMS, default="CLEAN")  # type: ignore[valid-type]
    max_chars: IntProperty(  # type: ignore[valid-type]
        name="Max Characters", default=0, min=0, max=80,
        description="Characters per line for Word-by-word highlight (0: what fits the frame)")

    @classmethod
    def poll(cls, context: Any) -> bool:
        if _seq.sequencer_scene(context) is None:      # strips are made if there are none (#504)
            cls.poll_message_set(_seq.NO_SCENE)
            return False
        return True

    def execute(self, context: Any) -> set[str]:
        scene = seq_scene(context)
        try:
            result = import_srt(scene, self.filepath, self.style, self.max_chars)
        except CaptionsError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'WARNING'} if result.get("skipped") else {'INFO'}, _summary(result))
        return {'FINISHED'}


class BFACW_OT_captions_export_srt(Operator, ExportHelper):  # type: ignore[misc]
    """Export the captions (the Captions channel, hand edits too) as an SRT subtitle file"""
    bl_idname = "bfacw.captions_export_srt"
    bl_label = "Export SRT"

    filename_ext = ".srt"
    filter_glob: StringProperty(default="*.srt", options={'HIDDEN'})  # type: ignore[valid-type]
    selected_only: BoolProperty(name="Selected Only", default=False,  # type: ignore[valid-type]
                                description="Only the selected caption strips")

    @classmethod
    def poll(cls, context: Any) -> bool:
        if not _seq.poll(cls, context):
            return False
        if not caption_lines(seq_scene(context)):
            cls.poll_message_set("No captions: add some with Auto Captions or Import SRT")
            return False
        return True

    def execute(self, context: Any) -> set[str]:
        cues = caption_cues(seq_scene(context), self.selected_only)
        if not cues:
            self.report({'ERROR'}, "No captions to export")
            return {'CANCELLED'}
        try:
            with open(self.filepath, "w", encoding="utf-8", newline="\r\n") as fh:
                fh.write(cap.format_srt(cues))
        except OSError as ex:
            self.report({'ERROR'}, "Couldn't write {:s}: {!s}".format(self.filepath, ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "{:d} captions written to {:s}".format(len(cues), os.path.basename(self.filepath)))
        return {'FINISHED'}


class BFACW_MT_captions(Menu):  # type: ignore[misc]
    bl_idname = "BFACW_MT_captions"
    bl_label = "Captions"

    def draw(self, _context: Any) -> None:
        layout = self.layout
        layout.operator_context = 'INVOKE_DEFAULT'
        # Auto Captions itself is next to this menu, in the Strip menu's Coworker section
        # (wand.sequencer), so it shows once.
        layout.operator("bfacw.captions_import_srt", text="Import SRT...", icon=safe_icon('IMPORT'))
        layout.operator("bfacw.captions_export_srt", text="Export SRT...", icon=safe_icon('EXPORT'))


def _draw_context_menu(self: Any, context: Any) -> None:
    """Right-click: Translate Captions (the selected ones, else all) through Translate (#42)."""
    scene = seq_scene(context)
    strips = caption_lines(scene, selected_only=True) or caption_lines(scene)
    if not strips:
        return
    layout = self.layout
    layout.separator()
    layout.operator_context = 'INVOKE_DEFAULT'
    path, current, what = translate_request(scene, strips)
    op = layout.operator("bfacw.translate", text="Translate Captions ({:d})...".format(len(strips)),
                         icon=safe_icon('FONT_DATA'))
    op.path, op.current, op.what = path, current, what


_CLASSES = (BFACW_OT_auto_captions, BFACW_OT_captions_import_srt, BFACW_OT_captions_export_srt, BFACW_MT_captions)
# The Strip menu's entries are drawn by wand.sequencer's Coworker section (one place for every
# Coworker video tool); the right-click menu keeps Translate Captions.
_MENUS = (("SEQUENCER_MT_context_menu", _draw_context_menu),)
_HOOKED: list[tuple[Any, Any]] = []


def register() -> None:
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
    try:
        stop_pending()
    except Exception:  # pylint: disable=broad-exception-caught
        _PENDING.clear()
        _unregister_timer()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
