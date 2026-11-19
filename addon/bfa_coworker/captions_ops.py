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
  Every selected strip gets the captions of the part of its media it shows (#538: the pieces
  of a cut clip, a clip used twice); muted and retimed strips and those with no speech are
  skipped and named in the report.  One undo step; running it again replaces the captions
  over those strips, but keeps the ones edited by hand (unless *Replace Edited Captions*).
* **Export SRT** writes every caption line (and any text strip on the *Captions*
  channel, so hand edits go out too); **Import SRT** makes styled captions at the
  cue times.  ``sequencer.export_subtitles`` needs a window and there is no import
  operator, so both are ours (they work in the background and in tools too).
* **Translate Captions** (right-click): one question for all caption lines through
  the existing Translate dialog; each translated line is written back into its own
  strip (same timing, style and tag).

Word strips follow their caption line (#594): :func:`refit_words` lays them out again, from the line
strip's own font, bold, italic, size, alignment, wrap width, place, transform and text
(:func:`captions.word_layout`), whenever the layout signature kept in the line's tag (``"lay"``) no longer
matches the strip: a depsgraph handler notes a change and a debounced timer looks, never while playing,
rendering or an agent step holds the co-work lock; *Refit Word Highlights* is the manual way.  The style
choices of the dialog and the tool (letter case, font, bold, italic, size, text and highlight colour) are
kept in the tag as ``"choices"``.

Every caption strip has the custom property :data:`captions.TAG`:
``{"source", "style", "role": "line"|"word", "chars", "lines"}`` (``"line"``: the
caption's strip name, on a word strip); a line made since #497 also has ``"frame"`` (the
output size it was laid out for) and ``"word_frames"`` (each word's first frame from the
line's start), which Make Vertical uses to lay it out again (:func:`relayout_captions`); since
#538 also ``"sig"`` (:func:`captions.caption_signature`: what it looked like when made, so a
re-run tells a caption edited by hand); since #542 also its look: ``"anim"`` (the animation
preset, keyed on the line and its word strips by :func:`_animate`, made again over new frames by
:func:`reanimate`), ``"position"``, ``"margin"`` and a speaker's ``"speaker"`` / ``"label"``.
The presets key only properties outside the signature (blend alpha, transform offset / scale,
crop, font size), so an animation never reads as an edit by hand.
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
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,  # pylint: disable=import-error
                       FloatVectorProperty, IntProperty, StringProperty)
from bpy.types import Menu, Operator, PropertyGroup  # pylint: disable=import-error
from bpy_extras.io_utils import ExportHelper, ImportHelper  # pylint: disable=import-error

from . import captions as cap
from .captions import CaptionsError
from .shared import icon as safe_icon
from .wand import scene as _seq
from .wand.spec import agent_line

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


def _muted(ed: Any, strip: Any) -> bool:
    """The strip, or its channel, is muted: none of it is heard."""
    if getattr(strip, "mute", False):
        return True
    channels = getattr(ed, "channels", None) or ()
    return 0 <= strip.channel < len(channels) and bool(getattr(channels[strip.channel], "mute", False))


def _retimed(strip: Any) -> bool:
    """The strip has retiming keys (#538).  Its speed can't be read back reliably: the Python
    API gives a key's timeline frame only, not the frame of the media it holds, so captions
    couldn't be timed on it; Remove Silences refuses such strips too."""
    keys = getattr(strip, "retiming_keys", None)
    try:
        return keys is not None and len(keys) > 0
    except TypeError:
        return False


def _signature(strip: Any) -> str:
    """A caption line as it is now (:func:`captions.caption_signature`)."""
    return cap.caption_signature(strip.text, _right(strip) - _left(strip), strip.channel, tuple(strip.location))


def _edited(strip: Any) -> bool:
    """Changed since Coworker made it (text, length, channel or place); a caption made before
    #538 has no signature and counts as not edited."""
    sig = (tag_of(strip) or {}).get("sig")
    return bool(sig) and sig != _signature(strip)


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
_BLF_KEEP = 8                       # fonts kept loaded for measuring: the least recently used are unloaded


def _blf_forget(all_: bool = False) -> None:
    """Unload measured fonts beyond :data:`_BLF_KEEP` (all of them with *all_*: unregister, file load)."""
    try:
        import blf  # pylint: disable=import-error
    except ImportError:
        _BLF_IDS.clear()
        return
    while _BLF_IDS and (all_ or len(_BLF_IDS) > _BLF_KEEP):
        path = next(iter(_BLF_IDS))
        _BLF_IDS.pop(path)
        try:
            blf.unload(path)
        except (RuntimeError, ValueError, TypeError):
            pass


def _measure(font: Any) -> Any:
    """``measure(text, px) -> width`` in the strip's font, or None (no font, no blf)."""
    if font is not None and str(font.filepath).startswith("<builtin"):
        font = _font()                  # Blender's default font is the bundled Inter
    if font is None:
        return None
    path = bpy.path.abspath(font.filepath)
    try:
        import blf  # pylint: disable=import-error
    except ImportError:
        return None
    fid = _BLF_IDS.pop(path, None)
    if fid is None:
        fid = blf.load(path)
    _BLF_IDS[path] = fid            # most recently used last
    _blf_forget()
    if fid is None or fid < 0:
        return None

    def measure(text: str, px: float) -> float:
        blf.size(fid, px)
        return float(blf.dimensions(fid, text)[0])
    return measure


def _line_measure(line: Any) -> Any:
    """``measure(text, px) -> width`` as *line*'s text strip draws it: in its font, and wider when it is
    bold (:func:`captions.bold_measure`); None when the font can't be measured (#594)."""
    font = line.font or _font()
    base = _measure(font)
    if base is None:
        return None
    if not line.use_bold:
        return base
    return cap.bold_measure(base, cap.font_family(font.filepath if font is not None else ""))


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
    *params*: ``style``, ``language``, ``max_chars``, ``max_lines``, ``replace_edited`` of
    :func:`add_captions`, and its #542 ``animation``, ``position``, ``margin``, ``speakers``,
    ``speaker_settings``, ``speaker_labels``.
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
                              int(p.get("max_chars", 0) or 0), int(p.get("max_lines", 0) or 0),
                              bool(p.get("replace_edited", False)), **_look_params(p))
    except CaptionsError as ex:
        raise CaptionsError(" ".join([str(ex)] + [e for e in errors if e and e not in str(ex)])) from None
    if job.get("undo", True):
        try:
            bpy.ops.ed.undo_push(message="Auto Captions")
        except RuntimeError:
            pass
    if errors:
        result["note"] = " ".join(errors + ([result["note"]] if "note" in result else []))
    job["warn"] = _warn(result)
    return "Auto Captions: " + _summary(result)


def _look_params(p: dict[str, Any]) -> dict[str, Any]:
    """The #542 arguments of :func:`add_captions` out of a job's *params*."""
    return {"animation": p.get("animation", cap.ANIM_STYLE), "animate": p.get("animate"),
            "line_animation": p.get("line_animation"), "word_animation": p.get("word_animation"),
            "position": p.get("position", "BOTTOM"),
            "margin": float(p.get("margin", 0.0) or 0.0), "speakers": bool(p.get("speakers", False)),
            "speaker_settings": list(p.get("speaker_settings") or ()),
            "speaker_labels": bool(p.get("speaker_labels", False)), "choices": dict(p.get("choices") or {})}


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


def _strip_curves(scene: Any, name: str, paths: Any = None) -> dict[str, list]:
    """``{path relative to the strip: [fcurves]}`` animating strip *name* in *scene*'s action
    (only *paths*, when given)."""
    ad = getattr(scene, "animation_data", None)
    action = ad.action if ad is not None else None
    if action is None:
        return {}
    prefix = 'sequence_editor.strips_all["{:s}"].'.format(bpy.utils.escape_identifier(name))
    out: dict[str, list] = {}
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                for fc in bag.fcurves:
                    if fc.data_path.startswith(prefix):
                        rel = fc.data_path[len(prefix):]
                        if paths is None or rel in paths:
                            out.setdefault(rel, []).append(fc)
    return out


def _owner(strip: Any, path: str) -> tuple[Any, str]:
    """The struct holding *path* (``"transform.offset_y"`` -> ``strip.transform``) and the
    property's name; ``(None, "")`` when this strip has no such property."""
    *parents, prop = path.split(".")
    owner = strip
    try:
        for p in parents:
            owner = getattr(owner, p)
        getattr(owner, prop)
    except AttributeError:
        return None, ""
    return owner, prop


def _set(owner: Any, prop: str, value: float) -> None:
    current = getattr(owner, prop)
    setattr(owner, prop, int(round(value)) if isinstance(current, int) and not isinstance(current, bool) else value)


_PENDING_MODES: dict[str, dict[str, str]] | None = None   # a run in progress: name -> {path: interpolation}


def _set_modes(scene: Any, name: str, modes: dict[str, str]) -> None:
    """The interpolation of strip *name*'s new keys: at once, or, in a run that keys many strips
    (:func:`_batch_modes`), noted and set in one pass over the action at its end (a scan of the whole action
    per strip is quadratic in the number of strips)."""
    if _PENDING_MODES is not None:
        _PENDING_MODES.setdefault(name, {}).update(modes)
        return
    _apply_modes(scene, {name: modes})


def _apply_modes(scene: Any, wanted: dict[str, dict[str, str]]) -> None:
    ad = getattr(scene, "animation_data", None)
    action = ad.action if ad is not None else None
    if action is None or not wanted:
        return
    escaped = {bpy.utils.escape_identifier(n): m for n, m in wanted.items()}
    prefix = 'sequence_editor.strips_all["'
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                for fc in bag.fcurves:
                    path = fc.data_path
                    if not path.startswith(prefix):
                        continue
                    name, sep, rel = path[len(prefix):].partition('"].')
                    mode = escaped.get(name, {}).get(rel) if sep else None
                    if mode is None:
                        continue
                    for k in getattr(fc, "keyframe_points", ()):
                        k.interpolation = mode
                    if hasattr(fc, "update"):
                        fc.update()


def _batched(fn: Any) -> Any:
    """*fn*'s keys get their interpolation in one pass when it returns (its first argument is the scene)."""
    import functools

    @functools.wraps(fn)
    def wrapper(scene: Any, *args: Any, **kw: Any) -> Any:
        with _batch_modes(scene):
            return fn(scene, *args, **kw)
    return wrapper


class _batch_modes:     # pylint: disable=invalid-name
    """``with _batch_modes(scene):`` keyed strips get their interpolation in one pass when it ends."""

    def __init__(self, scene: Any) -> None:
        self.scene = scene
        self.outer = False

    def __enter__(self) -> "_batch_modes":
        global _PENDING_MODES  # pylint: disable=global-statement
        self.outer = _PENDING_MODES is not None
        if not self.outer:
            _PENDING_MODES = {}
        return self

    def __exit__(self, *_exc: Any) -> None:
        global _PENDING_MODES  # pylint: disable=global-statement
        if not self.outer:
            wanted, _PENDING_MODES = _PENDING_MODES or {}, None
            _apply_modes(self.scene, wanted)


def _key_tracks(scene: Any, strips: list, tracks: list[dict]) -> int:
    """Key *tracks* (:func:`captions.animation_tracks`) on each of *strips*, with their own
    interpolation (not the Preferences' default); each property is left at its steady value.
    How many keys were set."""
    made = 0
    for s in strips:
        for t in tracks:
            owner, prop = _owner(s, t["path"])
            if owner is None:
                continue
            try:
                for frame, value in t["keys"]:
                    _set(owner, prop, value)
                    owner.keyframe_insert(prop, frame=frame)
                    made += 1
                _set(owner, prop, t["steady"])
            except (AttributeError, TypeError, ValueError, RuntimeError):
                continue
        _set_modes(scene, s.name, {t["path"]: t.get("interpolation", "BEZIER") for t in tracks})
    return made


def _clear_animation(scene: Any, strips: list, paths: Any = None, colour: Any = None) -> None:
    """The presets' animation off *strips*, each property back at rest (the font size at its
    steady size).  *paths*: only these of :data:`captions.ANIMATED_PATHS` (the ones a preset
    keys); the others, and what the user set on them, are left as they are.  *colour*: the text
    colour a word strip's ``color`` goes back to (#594)."""
    paths = set(cap.ANIMATED_PATHS if paths is None else paths)
    rest = {"blend_alpha": 1.0, "transform.offset_x": 0.0, "transform.offset_y": 0.0, "transform.scale_x": 1.0,
            "transform.scale_y": 1.0, "crop.max_x": 0.0}
    if colour is not None:
        rest["color"] = tuple(colour)
    sizes = {s.name: _steady_size(scene, s) for s in strips if hasattr(s, "font_size") and "font_size" in paths}
    _remove_curves(scene, {s.name for s in strips}, paths)       # one pass over the action for all of them
    for s in strips:
        size = sizes.get(s.name)
        for rel, value in rest.items():
            if rel not in paths:
                continue
            owner, prop = _owner(s, rel)
            if owner is not None:
                try:
                    _set(owner, prop, value)
                except (AttributeError, TypeError, ValueError):
                    pass
        if size is not None:
            s.font_size = size
    ad = getattr(scene, "animation_data", None)
    if ad is not None and ad.action is not None:
        try:
            ad.action.update_tag()
        except (AttributeError, RuntimeError):
            pass


def _remove_curves(scene: Any, names: set[str], paths: set[str]) -> None:
    """The F-curves of *paths* of the strips *names* out of *scene*'s action, in one pass over it."""
    ad = getattr(scene, "animation_data", None)
    action = ad.action if ad is not None else None
    if action is None or not names:
        return
    wanted = {bpy.utils.escape_identifier(n) for n in names}
    prefix = 'sequence_editor.strips_all["'
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                doomed = []
                for fc in bag.fcurves:
                    name, sep, rel = fc.data_path[len(prefix):].partition('"].')                         if fc.data_path.startswith(prefix) else ("", "", "")
                    if sep and name in wanted and rel in paths:
                        doomed.append(fc)
                for fc in doomed:
                    bag.fcurves.remove(fc)


def _remove_fcurve(fc: Any) -> None:
    """*fc* out of the channelbag that holds it."""
    action = fc.id_data
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                for other in bag.fcurves:
                    if other == fc:
                        bag.fcurves.remove(fc)
                        return


def _words_of(ed: Any, line: Any) -> list:
    return [s for s in _top(ed) if (tag_of(s) or {}).get("role") == "word" and (tag_of(s) or {}).get("line") == line.name]


def _animate(scene: Any, line: Any, style: dict, animation: str, words: list | None = None,
             words_only: bool = False) -> int:
    """Animation *animation* (:data:`captions.ANIMATIONS`) on a caption *line* in *style* and
    on its word strips (the same keys, so they move as one), over the line's own frames.
    *words_only*: only the word strips (made again after the line was keyed, #594)."""
    render = scene.render
    lines = [ln for ln in str(line.text).split("\n") if ln] or [""]
    reveal = None
    if cap.animation_key(animation) == "TYPEWRITER":
        measure = _measure(line.font or _font()) or cap.measure_fallback
        widest = max(lines, key=lambda ln: measure(ln, float(line.font_size)))
        reveal = cap.reveal_crops(widest, measure, float(line.font_size), render.resolution_x, line.location[0])
    tracks = cap.animation_tracks(animation, style["key"], _left(line), _right(line), _fps(scene),
                                  render.resolution_x, render.resolution_y, float(line.font_size), len(lines),
                                  str(getattr(line, "anchor_y", "BOTTOM")), tuple(line.location), reveal)
    if not tracks:
        if not words_only:
            _remember_keys(scene, line, ())
        return 0
    targets = ([] if words_only else [line]) + list(words or ())
    # Bold Pop's own pop is the line's font size: the word strips (none in that style) keep theirs.
    if any(t["path"] == "font_size" for t in tracks):
        targets = [] if words_only else [line]
    made = _key_tracks(scene, targets, tracks)
    if not words_only:
        _remember_keys(scene, line, {t["path"] for t in tracks})
    return made


def _animate_any(scene: Any, line: Any, style: dict, words: list | None = None, words_only: bool = False) -> int:
    """The caption *line*'s animation as its tag says: the line animation on the line and, when the
    words only light up, on their strips too; with word motions (#594) the line strip is only the anchor and
    every word strip gets the keys (:func:`_animate_words`)."""
    tag = tag_of(line) or {}
    if cap.tag_words(tag, style["key"])[0] == "motion":
        return _animate_words(scene, line, tag, style)
    return _animate(scene, line, style, tag.get("anim", cap.ANIM_STYLE), words, words_only)


def _word_middles(line: Any, words: list, measure: Any, res: tuple[float, float]) -> tuple[list, tuple[float, float]]:
    """``([(x, y) of each word strip's middle], the line's middle)`` in pixels from the frame's bottom-left,
    from where the word strips sit now."""
    measure = measure or cap.measure_fallback
    mids, left, right = [], [], []
    for w in words:
        width = float(measure(str(w.text), float(w.font_size)))
        x = float(w.location[0]) * res[0]
        mids.append((x + width / 2.0, cap.pop_middle(1, float(w.font_size), res[1], str(w.anchor_y),
                                                    tuple(w.location)) * res[1]))
        left.append(x)
        right.append(x + width)
    if not mids:
        return [], (res[0] / 2.0, res[1] / 2.0)
    ys = [m[1] for m in mids]
    return mids, ((min(left) + max(right)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _word_spans(line: Any, tag: dict, words: list) -> list[tuple[int, int]]:
    """Each word strip's own time ``(start, end)`` in frames, from the line's *current* ``word_frames``
    (Remove Silences' cuts and moves keep those up to date; the ``at`` of a word's own tag is from when it was
    made and is only the fallback), inside the line; the label's words start with it."""
    fs, fe = _left(line), _right(line)
    lead, frames = _label_words(tag), tag.get("word_frames")
    if frames and len(frames) == len(words) - lead:
        starts = [fs] * lead + [fs + int(v) for v in frames]
    else:
        starts = [fs + int((tag_of(w) or {}).get("at", 0)) for w in words]
    out, prev = [], fs
    for a in starts:
        prev = min(max(int(a), prev), fe)
        out.append(prev)
    return [(a, out[i + 1] if i + 1 < len(out) else fe) for i, a in enumerate(out)]


def _animate_words(scene: Any, line: Any, tag: dict, style: dict) -> int:
    """Keys on each word strip of *line* (made with word motions: the line strip is only the anchor): its
    word animation at the word's own time, the line animation over it, and with Highlight its colour at
    its own time (:func:`captions.word_tracks`).  The line strip gets no keys.  How many keys were set."""
    ed = scene.sequence_editor
    pool = _all(ed)
    name = line.name
    names = sorted(_word_names(ed).get(name, ()))
    words = [w for w in (pool.get(n) for n in names) if w is not None]
    if not words:
        return 0
    render = scene.render
    res = (float(render.resolution_x), float(render.resolution_y))
    fs, fe = _left(line), _right(line)
    _mode, wanim = cap.tag_words(tag, style["key"])
    anim = cap.animation_key(tag.get("anim")) or cap.ANIM_STYLE
    mids, centre = _word_middles(line, words, _line_measure(line), res)
    reveal = (cap.typewriter_frames([str(w.text) for w in words], fs, fe, _fps(scene))
              if anim == "TYPEWRITER" else [None] * len(words))
    base, hi = tuple(style["props"]["color"]), _hi_colour(style)
    made = 0
    spans = _word_spans(line, tag, words)
    for w, mid, rev, (a, b) in zip(words, mids, reveal, spans):
        tracks = cap.word_tracks(anim, wanim, fs, fe, a, b, _fps(scene), res[0], res[1], mid, centre, rev, base, hi)
        made += _key_tracks(scene, [w], tracks)
    return made


def _reanimate_words(scene: Any, line: Any, tag: dict) -> bool:
    """A word-motion caption's keys made again over the words as they sit now (the line trimmed, laid out
    again, its words moved by a new font or size); the words are ours, so no key of the user's is looked
    for.  No strip is added or removed."""
    key = cap.style_key(tag.get("style")) or "CLEAN"
    st = cap.resolve_style(key, scene.render.resolution_x, scene.render.resolution_y, tag.get("position", "BOTTOM"),
                           float(tag.get("margin") or 0.0), tag.get("choices") or None)
    ed = scene.sequence_editor
    words = [w for w in (_all(ed).get(n) for n in sorted(_word_names(ed).get(line.name, ()))) if w is not None]
    if not words:
        return False
    _clear_animation(scene, words, _WORD_CLEAR, colour=st["props"]["color"])
    _animate_words(scene, line, tag, st)
    return True


_WORD_CLEAR = tuple(p for p in cap.WORD_PATHS)


def _hi_colour(style: dict) -> tuple:
    """The highlight colour of *style* (resolved): the artist's, the style's own, else the default."""
    if style.get("highlight"):
        return tuple(style["highlight"])
    mine = (style.get("choices") or {}).get("highlight")
    return tuple(mine) if mine else tuple(cap.DEFAULT_HIGHLIGHT)


def _hide_anchor(line: Any) -> None:
    """The line strip of a caption with word motions draws nothing (#594): its text is clear, and its
    outline, shadow and box are off.  (Blender draws no box behind clear text, so a boxed style has no box with a
    word motion; a box per word would cut into the letters of its neighbours, so the words have none either.)  It stays the caption's anchor: its text, tag, font, bold, size and
    place are what the words follow."""
    colour = tuple(line.color)
    line.color = (colour[0], colour[1], colour[2], 0.0)
    for prop in ("use_outline", "use_shadow", "use_box"):
        try:
            setattr(line, prop, False)
        except (AttributeError, TypeError, ValueError):
            pass


def reanimate(scene: Any, line: Any) -> bool:
    """A caption line's animation made again over its frames now (trimmed or cut by Remove
    Silences, laid out again by Make Vertical): its preset's keys and its word strips'.  A
    caption made before #542 (no ``"anim"`` in its tag) is left as it is; False then.  So is a
    caption whose preset keys nothing (None, or the style's own on a style that doesn't pop):
    what the user set on it stays; and a caption whose keys the user added to or removed from
    (not as many as Coworker wrote, ``"keys"`` in its tag): Blender moves them with the strip."""
    tag = tag_of(line) or {}
    anim = tag.get("anim")
    if tag.get("role") != "line":
        return False
    if cap.tag_words(tag)[0] == "motion":
        return _reanimate_words(scene, line, tag)
    if anim is None:
        return False
    key = cap.style_key(tag.get("style")) or "CLEAN"
    paths = cap.animation_paths(anim, key)
    if not paths:
        return False
    written = tag.get("keys")
    if isinstance(written, dict):
        have = _key_counts(scene, line, paths)
        if any(have.get(p, 0) != int(written.get(p, 0)) for p in paths):
            return False
    st = cap.resolve_style(key, scene.render.resolution_x, scene.render.resolution_y,
                           tag.get("position", "BOTTOM"), float(tag.get("margin") or 0.0))
    words = _words_of(scene.sequence_editor, line)
    _clear_animation(scene, [line] + words, paths)
    _animate(scene, line, st, anim, words)
    return True


def _key_counts(scene: Any, line: Any, paths: Any) -> dict[str, int]:
    """How many keys *line* has on each of *paths*."""
    return {rel: sum(len(getattr(fc, "keyframe_points", ())) for fc in fcurves)
            for rel, fcurves in _strip_curves(scene, line.name, set(paths)).items()}


def _remember_keys(scene: Any, line: Any, paths: Any) -> None:
    """The number of keys Coworker wrote on *line* into its tag (not into the signature of
    #552), so a later refit can tell keys the user added or removed."""
    tag = tag_of(line)
    if tag is None:
        return
    tag["keys"] = _key_counts(scene, line, paths)
    line[cap.TAG] = tag


@_batched
def refit_captions(scene: Any, names: Any) -> int:
    """Remove Silences trimmed or cut these caption lines (#542): each one's animation fits its
    frames again (:func:`reanimate`).  How many were animated again."""
    ed = scene.sequence_editor
    if ed is None:
        return 0
    pool = _all(ed)
    done = 0
    for name in dict.fromkeys(names or ()):
        strip = pool.get(name)
        if strip is not None and strip.type == 'TEXT' and reanimate(scene, strip):
            done += 1
    return done


def _owned(ed: Any, keys: set[str]) -> list:
    return [s for s in _top(ed) if cap.owned_by(tag_of(s), keys)]


def remove_captions(scene: Any, keys: set[str], only: set[str] | None = None) -> int:
    """Remove the caption strips made from *keys* (and their animation); the number of lines.
    *only*: just these caption lines (and their word strips) of those sources (#538)."""
    ed = scene.sequence_editor
    if ed is None:
        return 0
    doomed = [s for s in _owned(ed, keys) if only is None or s.name in only
              or (tag_of(s) or {}).get("line") in only]
    lines = sum(1 for s in doomed if (tag_of(s) or {}).get("role") == "line")
    names = [s.name for s in doomed]
    top = _top(ed)
    from .wand.prefetch import remove_strips    # the Sequencer's prefetch stopped first (#536)
    remove_strips(top, doomed)
    _drop_fcurves(scene, names)
    return lines


def _next_number(ed: Any, skip: set[str]) -> int:
    best = 0
    for s in _all(ed):
        m = _NAME_RE.match(s.name)
        if m and s.name not in skip:
            best = max(best, int(m.group(1)))
    return best


def _choice_font(choice: Any) -> Any:
    """The font the artist chose (#594): the name of a font in this file, or a font file (a path, loaded
    once); None for "" (the style's own, the bundled Inter).  Raises :class:`CaptionsError` when there is
    no such font."""
    text = str(choice or "").strip()
    if not text:
        return None
    found = bpy.data.fonts.get(text)
    if found is not None:
        return found
    path = os.path.normpath(bpy.path.abspath(text))
    if not os.path.isfile(path):
        have = ", ".join(repr(f.name) for f in bpy.data.fonts if not f.filepath.startswith("<builtin"))
        raise CaptionsError("No font {!r}: give the name of a font in this file ({:s}) or the path of a font "
                            "file.".format(text, have or "none loaded"))
    try:
        return bpy.data.fonts.load(path, check_existing=True)
    except (RuntimeError, OSError):
        raise CaptionsError("{:s} can't be loaded as a font.".format(os.path.basename(path))) from None


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


_TRANSFORM_PATHS = ("offset_x", "offset_y", "scale_x", "scale_y", "rotation")


def _preset_paths(tag: dict) -> frozenset[str]:
    """The properties the caption's animation preset keys, as ``"transform.offset_y"``: they change with
    the frame, so they are no part of the layout signature and the word strips are not given their
    values (each has the same keys).  From the tag; no F-curve is looked at."""
    if tag.get("anim") is None:
        return frozenset()
    if cap.tag_words(tag)[0] == "motion":       # the line strip is only the anchor: its words are keyed (#594)
        return frozenset(cap.word_animation_paths(tag["anim"], _word_key(tag)))
    return frozenset(cap.animation_paths(tag["anim"], cap.style_key(tag.get("style")) or "CLEAN"))


def _word_key(tag: dict) -> str:
    """The word animation key of a caption line's *tag*."""
    return cap.tag_words(tag)[1]


def _sig_paths(tag: dict) -> frozenset[str]:
    """The properties of the line strip itself that its animation keys: none for a caption with word
    motions (its words are keyed, not the anchor)."""
    if cap.tag_words(tag)[0] != "motion":
        return _preset_paths(tag)
    key = cap.style_key(tag.get("style")) or "CLEAN"
    return frozenset()


def _label_words(tag: dict) -> int:
    """How many words of a line's text are a speaker's label (``"Ana:"``): laid out, but no word strip
    (with word motions the line strip draws nothing, so the label's words are strips too: :func:`_skip`)."""
    return len(str(tag.get("label") or "").split())


def _skip(tag: dict) -> int:
    """How many leading words of a caption line's text get no word strip."""
    return 0 if cap.tag_words(tag)[0] == "motion" else _label_words(tag)


def _signature_of(scene: Any, line: Any, tag: dict, animated: Any = None) -> str:
    """The layout signature of a caption *line* as its strip is now (:func:`captions.layout_signature`).
    Reads properties only: no font is measured or loaded.  *animated*: the property paths its preset keys
    (left out; None: from the tag)."""
    if animated is None:
        animated = _sig_paths(tag)
    render = scene.render
    tr = getattr(line, "transform", None)
    trans = tuple(None if "transform." + p in animated else round(float(getattr(tr, p)), 4)
                  for p in _TRANSFORM_PATHS) if tr is not None else ()
    font = line.font
    return cap.layout_signature(str(line.text), font.filepath if font else "", bool(line.use_bold),
                                bool(line.use_italic), float(line.font_size), str(line.anchor_x),
                                str(line.anchor_y), str(line.alignment_x), float(line.wrap_width),
                                tuple(line.location)[:2], trans, int(render.resolution_x),
                                int(render.resolution_y), str(tag.get("label") or ""))


def _layout_of(scene: Any, line: Any, tag: dict, animated: Any = ()) -> tuple[str, list[dict[str, Any]], Any]:
    """``(signature, word positions, the line's measure)`` of a caption *line* as its strip is now
    (:func:`captions.word_layout`); positions are empty when the font can't be measured.  This measures
    every word: :func:`refit_words` only calls it for a line whose :func:`_signature_of` changed."""
    render = scene.render
    sig = _signature_of(scene, line, tag)
    measure = _line_measure(line)
    if measure is None:
        return sig, [], None
    pos = cap.word_layout(str(line.text), measure, float(line.font_size), render.resolution_x, render.resolution_y,
                          tuple(line.location), str(line.anchor_x), str(line.anchor_y), str(line.alignment_x),
                          float(line.wrap_width), _skip(tag), italic=bool(line.use_italic))
    return sig, pos, measure


def _sync_word(word: Any, line: Any, pos: dict[str, Any], animated: Any = ()) -> None:
    """A word strip takes over from its line (#594): font, bold, italic and size, the place the layout
    gives it, and the transform values that are not animated."""
    word.font = line.font
    word.use_bold, word.use_italic = line.use_bold, line.use_italic
    word.font_size = line.font_size
    word.anchor_x, word.alignment_x, word.anchor_y = 'LEFT', 'LEFT', line.anchor_y
    word.wrap_width = 1.0
    word.location = (pos["x"], pos["y"])
    tr, mine = getattr(line, "transform", None), getattr(word, "transform", None)
    if tr is not None and mine is not None:
        for prop in _TRANSFORM_PATHS:
            if "transform." + prop not in animated:
                try:
                    setattr(mine, prop, getattr(tr, prop))
                except (AttributeError, TypeError, ValueError):
                    pass


def _remember_layout(ed: Any, name: str, sig: str) -> None:
    """The layout signature into the tag of caption line *name* (looked up again by name)."""
    line = _all(ed).get(name)
    tag = tag_of(line) if line is not None else None
    if tag is not None and tag.get("lay") != sig:
        tag["lay"] = sig
        line[cap.TAG] = tag


def _add_highlights(scene: Any, line: Any, framed: dict, style: dict, font: Any, measure: Any) -> int:
    """The word strips of a caption on the channels above *line*, laid out from the line strip's current
    settings (:func:`_layout_of`; #594).  *measure* only says the font can be measured (None: no word
    strips).

    Highlight only (the caption's tag says so): one strip per word on the channel above, over the word's
    own frames, in the highlight colour, over the visible line.  With a word motion: one strip per word
    (the speaker's label too), each on a channel of its own, over the caption's whole time, in the text
    colour, and the line strip is hidden (:func:`_hide_anchor`); the keys are the caller's
    (:func:`_animate_any`)."""
    ed = scene.sequence_editor
    if measure is None or not framed.get("words"):
        return 0
    tag = tag_of(line) or {}
    if framed.get("label") is not None:
        tag = dict(tag, label=framed["label"])
    name, channel, source = line.name, line.channel + 1, tag.get("source", "")
    motion = cap.tag_words(tag, style["key"])[0] == "motion"
    animated = _preset_paths(tag)
    sig, pos, _measure_line = _layout_of(scene, line, tag, animated)
    fs, fe = framed["frame_start"], framed["frame_end"]
    if motion:
        lead = _label_words(tag)
        spans = [(j, fs, fs) for j in range(lead)] + [(lead + i, a, b) for i, a, b in cap.word_spans(framed)]
        windows = [(j, a, b) for j, a, b in spans if j < len(pos)]
    else:
        windows = [(i, a, b) for i, a, b in cap.highlight_windows(framed) if i < len(pos)]
    colour = _hi_colour(style)
    made = 0
    for i, a, b in windows:         # only names and numbers from here: strips are added
        if motion:
            h = _text_strip(ed, "{:s}_w{:02d}".format(name, i + 1), channel + i, fs, fe, pos[i]["text"], style, font)
            if style["props"].get("use_box"):
                h.use_box = False           # the anchor's one box is behind the whole line
        else:
            h = _text_strip(ed, "{:s}_w{:02d}".format(name, i + 1), channel, a, b, pos[i]["text"], style, font)
            h.color = colour
        line = _all(ed).get(name)
        if line is not None:
            _sync_word(h, line, pos[i], animated)
        h[cap.TAG] = {"source": source, "style": style["key"], "role": "word", "line": name, "i": i,
                      "at": int(a) - int(fs), "to": int(b) - int(fs)}
        made += 1
    _remember_layout(ed, name, sig)
    if motion and made:
        line = _all(ed).get(name)
        if line is not None:
            _hide_anchor(line)
    return made


def _line_tag(source: str, style: str, chars: int, lines: int, scene: Any, frame_start: int,
              words: Any = None, look: dict | None = None) -> dict[str, Any]:
    """A caption line's tag: also the frame it was laid out for and its words' first frames
    (from the line's start), so Make Vertical can lay it out again (#497); and since #542 its
    *look*: ``anim``, ``position``, ``margin`` and a speaker's ``speaker`` / ``label``."""
    tag = {"source": source, "style": style, "role": "line", "chars": int(chars), "lines": int(lines),
           "frame": [int(scene.render.resolution_x), int(scene.render.resolution_y)]}
    offsets = [int(w["frame"]) - int(frame_start) for w in words or () if "frame" in w]
    if offsets:
        tag["word_frames"] = offsets
    for k, v in (look or {}).items():
        if k in _LOOK_KEYS and v is not None and v != "" and v != {}:
            tag[k] = {kk: list(vv) if isinstance(vv, tuple) else vv for kk, vv in v.items()} if isinstance(v, dict) else v
    return tag


_LOOK_KEYS = ("anim", "wanim", "position", "margin", "speaker", "label", "choices")


def _look_tag(tag: dict) -> dict[str, Any]:
    """The #542 look of a caption line's *tag* (to hand on to the pieces it is split into)."""
    return {k: tag[k] for k in _LOOK_KEYS if k in tag}


def _occupied(ed: Any, skip: set[str]) -> dict[int, list[tuple[float, float]]]:
    out: dict[int, list[tuple[float, float]]] = {}
    for s in _top(ed):
        if s.name not in skip:
            out.setdefault(s.channel, []).append((_left(s), _right(s)))
    return out


@_batched
def _place(scene: Any, groups: list[tuple[str, list[dict]]], style: dict, chars: int, lines: int,
           above: int, targets: list[tuple] | None = None, replace_edited: bool = False,
           animation: str = cap.ANIM_STYLE, lanes: dict[str, dict] | None = None,
           font: Any = None, animate: Any = None, line_animation: Any = None,
           word_animation: Any = None) -> dict[str, Any]:
    """Text strips for framed captions: one per caption (and the word strips), on the
    *Captions* channel above *above*; the earlier captions of the same sources go.

    *targets*: ``(key, left, right)`` of the strips captioned now (#538): then only the earlier
    captions over those strips (or over no strip of their source any more) go, and one edited
    by hand is kept unless *replace_edited* (no new caption is put over it).

    *animation*: an :data:`captions.ANIMATIONS` key (#542); *animate*, *line_animation* and *word_animation*
    (#594, :func:`captions.resolve_animation`): what moves, and how.  *lanes*: one per speaker (#542),
    ``{key: {"name", "style" (resolved), "chars", "lines", "label", "channel",
    "highlight_channel"}}``: each speaker's captions on a channel of their own, in their style;
    None: every source on the *Captions* channel in *style*.  *font*: the artist's own font (#594;
    None: the bundled Inter)."""
    ed = scene.sequence_editor
    keys = {key for key, _framed in groups}
    old = [s for s in _owned(ed, keys) if (tag_of(s) or {}).get("role") == "line"]
    present = []
    if targets is not None:
        for s in _media_strips(_top(ed)):
            path = media_path(s)
            if path and source_key(path) in keys:
                present.append((source_key(path), _left(s), _right(s)))
    doomed_lines, kept = cap.replace_plan(
        [{"name": s.name, "key": (tag_of(s) or {}).get("source"), "left": _left(s), "right": _right(s),
          "edited": _edited(s)} for s in old], targets, present, replace_edited)
    kept_spans = [(_left(s), _right(s)) for s in old if s.name in kept]
    if lanes is not None:       # a channel per speaker (#542): a speaker's edited caption only holds their own place
        kept_by: dict[str, list[tuple[float, float]]] = {}
        for s in old:
            if s.name in kept:
                kept_by.setdefault((tag_of(s) or {}).get("source"), []).append((_left(s), _right(s)))
    framed_all = [c for _key, framed in groups for c in framed]
    under_kept = [c for key, framed in groups for c in framed
                  if any(a < c["frame_end"] and c["frame_start"] < b
                         for a, b in (kept_spans if lanes is None else kept_by.get(key, ())))]
    if under_kept:
        ids = {id(c) for c in under_kept}
        groups = [(key, [c for c in framed if id(c) not in ids]) for key, framed in groups]
        framed_all = [c for c in framed_all if id(c) not in ids]
    if not framed_all:
        if kept:
            raise CaptionsError("Every caption there was edited by hand and is kept ({:d}); turn on Replace "
                                "Edited Captions to replace them.".format(len(kept)))
        raise CaptionsError("No words fall inside the visible part of the strip.")
    doomed = {s.name for s in _owned(ed, keys)
              if s.name in doomed_lines or (tag_of(s) or {}).get("line") in doomed_lines}
    spans = [(c["frame_start"], c["frame_end"]) for c in framed_all]
    span = (min(a for a, _b in spans), max(b for _a, b in spans))
    if lanes is None:
        plan = [({"name": "", "style": style, "chars": chars, "lines": lines, "label": "",
                  "channel": cap.CHANNEL_NAME, "highlight_channel": cap.HIGHLIGHT_CHANNEL_NAME}, groups)]
    else:
        plan = [(lanes[key], [(key, framed)]) for key, framed in groups if key in lanes]
    # The animation of each lane (a speaker may have a style of their own), and the word motions' limits,
    # before anything is removed or made (over a limit: nothing changes).
    def plan_of(lane: dict) -> dict[str, str]:
        return cap.resolve_animation(lane["style"]["key"], animate, line_animation, word_animation, animation)
    fps = _fps(scene)
    for lane, lane_groups in plan:
        lp = plan_of(lane)
        if lp["words"] == "motion":
            counts = [cap.label_words_in(lane["label"]) + len(c.get("words") or ())
                      for _k, framed in lane_groups for c in framed]
            refusal = cap.check_word_plan(counts, lp["line"], lp["word"], fps)
            if refusal:
                raise CaptionsError(refusal)
    # Each lane's channel, before anything is removed (no room: nothing changes).
    names = {i: ch.name for i, ch in enumerate(ed.channels)}
    occupied = _occupied(ed, doomed)
    lane_names = {n for lane, _g in plan for n in (lane["channel"], lane["highlight_channel"])}
    taken: set[int] = set()
    picked = []
    for lane, lane_groups in plan:
        lane_spans = [(c["frame_start"], c["frame_end"]) for _k, framed in lane_groups for c in framed]
        if not lane_spans:
            continue
        lp = plan_of(lane)
        need = 1
        if lp["words"] == "highlight":
            need = 2
        elif lp["words"] == "motion":       # a channel for each word of the longest line, over the anchor's
            need = 1 + max(cap.label_words_in(lane["label"]) + len(c.get("words") or ())
                           for _k, framed in lane_groups for c in framed)
        # Not another speaker's channel (#542), nor one taken by a speaker before this one.
        others = {c for c, n in names.items() if n in lane_names and n not in (lane["channel"], lane["highlight_channel"])}
        channel = cap.pick_channel(occupied, names, above, lane_spans, need, name=lane["channel"],
                                   skip=taken | others)
        if channel is None:
            if lp["words"] == "motion":
                raise CaptionsError("There's no free block of {:d} channels above the clip for the word motions "
                                    "(one for each word of the longest line, and the caption's own). Use "
                                    "Highlight instead of a word motion, or fewer words per line (Max "
                                    "Characters).".format(need))
            raise CaptionsError("There's no free channel above the clip for the captions.")
        taken.update(range(channel, channel + need))
        picked.append((lane, lane_groups, channel, need, lp))
    replaced = remove_captions(scene, keys, None if targets is None else doomed_lines)
    font = font or _font()
    number = _next_number(ed, set())
    made = words = 0
    notes = []
    speakers = []
    for lane, lane_groups, channel, need, lp in picked:
        st = lane["style"]
        _name_channel(ed.channels[channel], lane["channel"])
        for extra in range(1, need):
            _name_channel(ed.channels[channel + extra], lane["highlight_channel"])
        measure = _measure(font) if lp["words"] != "none" else None
        if lp["words"] != "none" and measure is None:
            notes.append("The bundled font couldn't be measured here, so there are no word highlights.")
        look = {"anim": lp["line"], "wanim": lp["word"], "position": st.get("position", "BOTTOM"), "margin": st.get("margin", 0.0),
                "speaker": lane["name"], "label": lane["label"], "choices": st.get("choices")}
        lane_made = 0
        for key, framed in lane_groups:
            for c in framed:
                number += 1
                text = cap.apply_case(cap.with_label(c["text"], lane["label"]), st["case"])
                s = _text_strip(ed, "cap_{:04d}".format(number), channel, c["frame_start"], c["frame_end"], text,
                                st, font)
                name = s.name
                s[cap.TAG] = dict(_line_tag(key, st["key"], lane["chars"], lane["lines"], scene, c["frame_start"],
                                            c.get("words"), look),
                                  sig=_signature(s))      # what it is now: a later run tells hand edits (#538)
                made += 1
                lane_made += 1
                hl = []
                if lp["words"] != "none":
                    words += _add_highlights(scene, s, dict(c, label=lane["label"]), st, font, measure)
                    s = _all(ed).get(name)              # strips were added: look the line up again
                    hl = _words_of(ed, s)
                # After the signature: the keys change none of what it holds (#542).
                _animate_any(scene, s, st, hl)
        if lanes is not None:
            speakers.append({"name": lane["name"], "channel": channel, "lines_created": lane_made,
                             "style": st["label"], "position": st.get("position", "BOTTOM")})
    result = {"status": "ok", "lines_created": made, "highlight_strips": words, "channel": picked[0][2],
              "first_frame": span[0], "last_frame": span[1] - 1, "style": style["label"], "replaced": replaced,
              "animation": cap.ANIMATIONS[picked[0][4]["line"]]["label"],
              "animate": cap.ANIMATE[picked[0][4]["animate"]]["label"],
              "word_animation": cap.WORD_ANIMATIONS[picked[0][4]["word"]]["label"],
              "position": style.get("position", "BOTTOM")}
    if speakers:
        result["speakers"] = speakers
    if targets is not None:
        result["kept_edited"] = len(kept)
    if notes:
        result["note"] = notes[0]
    return result


def _limits(style: dict, scene: Any, max_chars: int, max_lines: int,
            animation: str = cap.ANIM_STYLE, plan: dict | None = None) -> tuple[int, int]:
    """Characters per line and lines: one line for Word-by-word and for Typewriter (#542); with the
    *plan* of #594 (:func:`captions.resolve_animation`) for Word-by-word only while its words light up, and
    for Typewriter only when it crops the visible line."""
    if plan is not None:
        one = (bool(style["highlight"]) and plan["words"] != "none") or cap.plan_one_line(plan)
    else:
        one = bool(style["highlight"]) or cap.one_line(animation)
    lines = 1 if one else (max_lines or style["max_lines"])
    chars = max_chars or cap.default_max_chars(style["key"], scene.render.resolution_x, scene.render.resolution_y,
                                               style.get("choices"))
    return int(chars), (1 if lines <= 1 else 2)


def _strip_item(ed: Any, s: Any, path: str) -> dict[str, Any]:
    """A media strip as :func:`captions.plan_strips` / :func:`captions.captions_for_strips` take it."""
    return {"name": s.name, "type": s.type, "key": source_key(path), "path": path,
            "left": _left(s), "right": _right(s), "content_start": float(_content_start(s)),
            "offset": float(getattr(s, "sound_offset", 0.0) or 0.0) if s.type == 'SOUND' else 0.0,
            "muted": _muted(ed, s), "retimed": _retimed(s)}


def add_captions(scene: Any, sources: list, style: str = "CLEAN", language: str | None = None,
                 transcript_path: str = "", max_chars: int = 0, max_lines: int = 0,
                 replace_edited: bool = False, animation: str = cap.ANIM_STYLE, position: str = "BOTTOM",
                 margin: float = 0.0, speakers: bool = False, speaker_settings: Any = (),
                 speaker_labels: bool = False, choices: dict[str, Any] | None = None, animate: Any = None,
                 line_animation: Any = None, word_animation: Any = None) -> dict[str, Any]:
    """Caption *sources* (sound / movie strips) in *style*; the result for the artist / the tool.

    Every selected strip gets the captions of the part of its media it shows (#538); the
    transcript is read once per media file.  A movie is heard through a selected sound strip of
    the same file over its time; muted and retimed strips, and those with no speech or no
    transcript, are skipped and named (``strips_skipped``).  Earlier captions over the strips
    captioned now are replaced, except those edited by hand (``kept_edited``) unless
    *replace_edited*.

    #542: *animation* (:data:`captions.ANIMATIONS`), *position* (``BOTTOM`` / ``TOP`` /
    ``MIDDLE``) and *margin* (fraction of the frame's height, 0: the safe area's).  With
    *speakers* each media file is a speaker with a channel of their own
    (:func:`captions.plan_speakers`; *speaker_settings*: their names, styles, positions;
    *speaker_labels*: "Name:" in front of each caption).

    #594: *choices* (:func:`captions.check_style_params`): the artist's own letter case, font, bold,
    italic, size, text and highlight colour; whatever is left out is as the style has it.  They are kept
    in each caption's tag, so Make Vertical, a rewrite by Edit by Transcript and Translate keep them.

    #594: *animate* (:data:`captions.ANIMATE`), *line_animation* (:data:`captions.ANIMATIONS`) and
    *word_animation* (:data:`captions.WORD_ANIMATIONS`) say what moves and how (:func:`captions.resolve_animation`);
    *animation* is the single list of before, the line animation, and works as it did."""
    ed = scene.sequence_editor
    if ed is None:
        raise CaptionsError("The scene “{:s}” has no strips.".format(scene.name))
    animation = cap.animation_key(animation) or cap.ANIM_STYLE
    animate = cap.animate_key(animate) if animate is not None else None
    line_animation = cap.animation_key(line_animation) if line_animation is not None else None
    word_animation = cap.word_animation_key(word_animation) if word_animation is not None else None
    anim_args = (animate, line_animation, word_animation, animation)
    plan0 = cap.resolve_animation(style, *anim_args)
    res = (scene.render.resolution_x, scene.render.resolution_y)
    choices = dict(choices or {})
    font = _choice_font(choices.get("font", ""))
    st = cap.resolve_style(style, res[0], res[1], position, margin, choices)
    chars, lines = _limits(st, scene, max_chars, max_lines, animation, plan0)
    items = []
    for s in _media_strips(sources):
        path = media_path(s)
        if path:
            items.append(_strip_item(ed, s, path))
    if not items:
        raise CaptionsError("Select a sound or movie strip to caption.")
    notes = []
    only_key = None
    keys = list(dict.fromkeys(it["key"] for it in items))
    if transcript_path and len(keys) > 1:
        active = getattr(ed.active_strip, "name", None)
        only_key = next((it["key"] for it in items if it["name"] == active), keys[0])
        notes.append("A transcript file captions one clip: {:s}.".format(
            next(it["name"] for it in items if it["key"] == only_key)))
    todo, outcome, partners = cap.plan_strips(items)
    lanes = None
    if speakers:                        # one channel per speaker (#542)
        lanes = {}
        for key, sp in cap.plan_speakers(todo, st["key"], st["position"], speaker_settings or (),
                                         speaker_labels).items():
            sst = cap.resolve_style(sp["style"], res[0], res[1], sp["position"], margin, choices)
            c, n = _limits(sst, scene, max_chars, max_lines, animation,
                           cap.resolve_animation(sst["key"], *anim_args))
            lanes[key] = dict(sp, style=sst, chars=c, lines=n)
    groups, errors, langs = [], [], []
    left_out = 0
    fps = _fps(scene)
    for key in dict.fromkeys(it["key"] for it in todo):
        strips = [it for it in todo if it["key"] == key]
        if only_key is not None and key != only_key:
            outcome.update((it["name"], cap.SKIP_NO_TRANSCRIPT) for it in strips)
            continue
        try:
            words, lang = load_words(strips[0]["path"], language, transcript_path)   # once per file
        except CaptionsError as ex:
            errors.append(str(ex))
            outcome.update((it["name"], cap.SKIP_NO_TRANSCRIPT) for it in strips)
            continue
        if lanes is not None:
            lane = lanes[key]
            got = cap.captions_for_strips(words, strips, fps, cap.label_chars(lane["chars"], lane["label"])
                                          if lane["label"] else lane["chars"], lane["lines"])
        else:
            got = cap.captions_for_strips(words, strips, fps, chars, lines)
        outcome.update((n, "captioned") for n in got["captioned"])
        outcome.update((n, cap.SKIP_NO_SPEECH if words else cap.SKIP_SILENT) for n in got["no_speech"])
        left_out += got["left_out"]
        if got["captions"]:
            groups.append((key, got["captions"]))
            langs.append(lang or language or "")
    for movie, sounds in partners.items():
        outcome[movie] = cap.partner_outcome([outcome.get(n, cap.SKIP_NO_SPEECH) for n in sounds])
    if lanes is None:
        # Two files heard at once (two speakers): their captions share the channel, so none overlaps.
        flat, dropped = cap.separate([dict(c, key=key) for key, framed in groups for c in framed])
        left_out += dropped
        groups = [(key, [c for c in flat if c["key"] == key]) for key, _framed in groups]
    else:
        # A channel per speaker (#542): only one speaker's own captions must not overlap.
        sep = []
        for key, framed in groups:
            kept_caps, dropped = cap.separate(framed)
            left_out += dropped
            sep.append((key, kept_caps))
        groups = sep
    groups = [(key, framed) for key, framed in groups if framed]
    captioned = [it["name"] for it in items if outcome.get(it["name"]) == "captioned"]
    skipped = [{"strip": it["name"], "reason": outcome.get(it["name"], cap.SKIP_NO_SPEECH)}
               for it in items if outcome.get(it["name"]) != "captioned"]
    if not groups:
        if errors:
            raise CaptionsError(errors[0])
        raise CaptionsError("There's no speech to caption ({:s}).".format(
            cap.strips_report(captioned, skipped).split("; ", 1)[-1]))
    # The strips captioned now, or found silent now: earlier captions over them go.
    captioned_keys = {k for k, _f in groups}
    targets = [(it["key"], it["left"], it["right"]) for it in items if it["key"] in captioned_keys
               and outcome.get(it["name"]) in ("captioned", cap.SKIP_NO_SPEECH)]
    above = max(s.channel for s in _media_strips(sources))      # above every selected clip, kept or not
    result = _place(scene, groups, st, chars, lines, above, targets, replace_edited, animation, lanes, font,
                    animate, line_animation, word_animation)
    firsts = [next(it["name"] for it in items if it["key"] == k) for k, _f in groups]
    result.update(sources=firsts, language=next((x for x in langs if x), None), max_chars=chars, max_lines=lines,
                  strips_captioned=captioned, strips_skipped=skipped)
    if left_out:
        notes.append("{:d} caption{:s} overlapped another strip's and {:s} left out.".format(
            left_out, "" if left_out == 1 else "s", "was" if left_out == 1 else "were"))
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
    """A caption's layout as :func:`captions.hand_edits` reads it: an animated transform (Slide
    Up, Pop, #542) counts with its value in the middle of the caption, where it is up."""
    tr = getattr(strip, "transform", None)
    look = {"location": tuple(strip.location), "font_size": _steady_size(scene, strip),
            "wrap_width": strip.wrap_width, "anchor_x": strip.anchor_x, "anchor_y": strip.anchor_y}
    if tr is not None:
        curves = _strip_curves(scene, strip.name, {"transform." + p for p in
                                                   ("offset_x", "offset_y", "scale_x", "scale_y", "rotation")})
        middle = (_left(strip) + _right(strip) - 1) / 2.0

        def steady(prop: str) -> float:
            fcs = curves.get("transform." + prop)
            return float(fcs[0].evaluate(middle)) if fcs else float(getattr(tr, prop))
        look["transform"] = tuple(steady(p) for p in ("offset_x", "offset_y", "scale_x", "scale_y", "rotation"))
    return look


@_batched
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
            words_of.setdefault(tag.get("line"), []).append(s.name)
    lines.sort(key=lambda s: (_left(s), s.channel))
    line_names = [s.name for s in lines]        # only names from here: strips are added and removed
    lines = []
    number = _next_number(ed, set())
    for line_name in line_names:
        line = _all(ed).get(line_name)
        if line is None:
            continue
        tag = tag_of(line) or {}
        key = cap.style_key(tag.get("style"))
        made_for = tuple(int(v) for v in (tag.get("frame") or old_res or res))
        if made_for == res:
            continue                                    # already laid out for this frame
        position, margin = tag.get("position", "BOTTOM"), float(tag.get("margin") or 0.0)
        label = str(tag.get("label") or "")
        choices = tag.get("choices") or None
        if key is None or cap.hand_edits(_look_of(scene, line), key, *made_for, position, margin, choices):
            out["left"] += 1
            out["left_names"].append(line.name)
            continue
        fs, fe = _left(line), _right(line)
        mode = cap.tag_words(tag, key)[0]
        old_words = [w for w in (_all(ed).get(n) for n in sorted(words_of.get(line.name, ()))) if w is not None]
        frames = None
        if mode != "motion" and old_words and len(old_words) == len(cap.without_label(line.text, label).split()):
            frames = [_left(w) for w in old_words]
        elif tag.get("word_frames"):
            frames = [fs + int(v) for v in tag["word_frames"]]
        lay = cap.relayout(line.text, key, res[0], res[1], fs, fe, int(tag.get("chars") or 0),
                           int(tag.get("lines") or 0), made_for, frames, position, margin, label, choices)
        # #542: made again below, over each piece; a caption whose preset keys nothing keeps
        # what the user set on it (and a Bold Pop split piece gets its pop only before #542).
        paths = cap.animation_paths(tag["anim"], key) if tag.get("anim") is not None else ()
        animated = bool(paths)
        pre_542 = tag.get("anim") is None
        st, pieces = lay["style"], lay["pieces"]
        highlight = tuple(old_words[0].color) if old_words and mode != "motion" else st["highlight"]
        names = [w.name for w in old_words]
        from .wand.prefetch import remove_strips    # the Sequencer's prefetch stopped first (#536)
        remove_strips(top, old_words)
        had_words = bool(old_words)
        old_words = None
        if names:
            _drop_fcurves(scene, names)
        line = _all(ed).get(line_name)          # strips were removed: look the caption up again
        if line is None:
            continue
        props = st["props"]
        old_size = _steady_size(scene, line)
        font = line.font
        for i, piece in enumerate(pieces):
            if i == 0:
                s = line
                if len(pieces) > 1:
                    _set_right(s, piece["frame_end"])
                s.text = piece["text"]
                if animated:
                    _clear_animation(scene, [s], paths)   # its keys (a pop's sizes) are for the old frame
                s.font_size = props["font_size"]
                if old_size > 0 and not animated:
                    _scale_font_keys(scene, s, props["font_size"] / old_size)
            else:
                number += 1
                s = _text_strip(ed, "cap_{:04d}".format(number), line.channel, piece["frame_start"],
                                piece["frame_end"], piece["text"], st, font)
                line = _all(ed).get(line_name)          # a strip was added: look the caption up again
                for attr in _LOOK:
                    try:
                        setattr(s, attr, getattr(line, attr))
                    except (AttributeError, TypeError, ValueError):
                        pass
                if st["pop"] and pre_542 and piece["frame_end"] - piece["frame_start"] >= 5:
                    size = s.font_size
                    s.font_size = size * 0.78
                    s.keyframe_insert("font_size", frame=piece["frame_start"])
                    s.font_size = size
                    s.keyframe_insert("font_size", frame=piece["frame_start"] + 3)
            s.location = props["location"]
            s.anchor_y = props["anchor_y"]
            s.wrap_width = 1.0 if st["highlight"] else props["wrap_width"]
            s[cap.TAG] = dict(_line_tag(tag.get("source", ""), key, lay["chars"], lay["lines"], scene,
                                        piece["frame_start"], piece["words"], _look_tag(tag)), sig=_signature(s))
            piece_name = s.name
            got = 0
            if mode != "none" and had_words:
                style = dict(st, uppercase=False, highlight=highlight)     # the text already is as shown
                got = _add_highlights(scene, s, piece, style, font or _font(), _measure(font or _font()))
                s = _all(ed).get(piece_name)    # strips were added: look the caption up again
                line = _all(ed).get(line_name)
            if mode == "motion" and not got and s is not None:
                _demote(scene, piece_name, "NONE", "it got no words in the new layout")    # never a clear caption
                s = _all(ed).get(piece_name)
            if s is not None and (animated or mode == "motion"):
                reanimate(scene, s)
        out["laid_out"] += 1
        out["split"] += 1 if len(pieces) > 1 else 0
    if out["laid_out"] or out["left"]:
        out["message"] = cap.relayout_report(out["laid_out"], out["left"], out["split"])
    return out


# -- word strips follow their line (#594) ---------------------------------------------------

def _word_names(ed: Any) -> dict[str, list[str]]:
    """``{caption line name: [its word strips' names]}`` in one light pass (no tag copies)."""
    out: dict[str, list[str]] = {}
    for s in _top(ed):
        value = s.get(cap.TAG)
        if value is not None and value.get("role") == "word":
            out.setdefault(str(value.get("line")), []).append(s.name)
    return out


NOTES: list[str] = []       # what the last rebuilds fell back from, for the operator to report (#594)


_DEFAULT_CHANNEL = re.compile(r"^Channel \d+$")


def _name_channel(channel: Any, name: str) -> None:
    """Name *channel* for the captions, unless the artist named it themselves (it still has Blender's own
    name, no name, or ours already)."""
    if _DEFAULT_CHANNEL.match(str(channel.name)) or not str(channel.name) or str(channel.name) == name:
        channel.name = name


def _block_problem(scene: Any, line: Any, count: int, ignore: Any = ()) -> str:
    """Why *count* word strips can't go on the channels above caption *line* ("" when they can): over the
    word limit, past channel 128, or a strip of somebody else on one of those channels over the caption's
    time.  *ignore*: names of strips that are going away."""
    if count > cap.MAX_WORD_CHANNELS:
        return "{:d} words are over the limit of {:d} (one channel each)".format(count, cap.MAX_WORD_CHANNELS)
    top = line.channel + count
    if top > cap.MAX_CHANNEL:
        return "{:d} word channels above channel {:d} would pass channel {:d}".format(count, line.channel,
                                                                                     cap.MAX_CHANNEL)
    fs, fe, skip = _left(line), _right(line), set(ignore) | {line.name}
    for s in _top(scene.sequence_editor):
        if s.name not in skip and line.channel < s.channel <= top and _left(s) < fe and fs < _right(s):
            return "channel {:d} is taken by {:s}".format(s.channel, s.name)
    return ""


def _show_anchor(line: Any, st: dict) -> None:
    """The line strip draws again, as the style has it (it was the clear anchor of word motions)."""
    props = st["props"]
    line.color = tuple(props["color"])
    for prop in ("use_outline", "use_shadow", "use_box"):
        try:
            setattr(line, prop, bool(props[prop]))
        except (AttributeError, TypeError, ValueError, KeyError):
            pass


def _demote(scene: Any, name: str, wanim: str, why: str) -> None:
    """Caption *name* can't keep its word motions (#594): its line strip draws again and its word animation
    becomes *wanim* ("HIGHLIGHT" or "NONE"); the line animation keys the line.  Nothing is added or removed."""
    line = _all(scene.sequence_editor).get(name)
    tag = tag_of(line) if line is not None else None
    if tag is None:
        return
    key = cap.style_key(tag.get("style")) or "CLEAN"
    st = cap.resolve_style(key, scene.render.resolution_x, scene.render.resolution_y, tag.get("position", "BOTTOM"),
                           float(tag.get("margin") or 0.0), tag.get("choices") or None)
    tag["wanim"] = wanim
    line[cap.TAG] = tag
    _show_anchor(line, st)
    _clear_animation(scene, [line], cap.animation_paths(tag.get("anim", cap.ANIM_STYLE), key))
    _animate(scene, line, st, tag.get("anim", cap.ANIM_STYLE), None)
    msg = "{:s}: {:s}; it is {:s} now.".format(name, why, "Highlight only" if wanim == "HIGHLIGHT" else "plain")
    NOTES.append(msg)
    print("[Coworker] " + msg)


def _rebuild_words(scene: Any, name: str, even: bool = False) -> bool:
    """The word strips of caption line *name* made again, from the line as it is now: the words of its
    text, each over its own frames (the line's stored word frames when they still fit the words, else
    spread over its time), laid out by :func:`_layout_of`, and keyed like the line.  Only names are held
    across the strips removed and added.

    With word motions the new words must fit the channel block (the word limit, channel 128, nothing of
    somebody else's in the way): checked before anything is removed.  If they don't, the caption drops to
    Highlight only (its line draws again) or, if even that has no room, to plain, and says so in
    :data:`NOTES`: never a clear caption with no words."""
    ed = scene.sequence_editor
    pool = _all(ed)
    line = pool.get(name)
    if line is None or line.type != 'TEXT':
        return False
    tag = tag_of(line) or {}
    key = cap.style_key(tag.get("style")) or "CLEAN"
    label = str(tag.get("label") or "")
    own = sorted(_word_names(ed).get(name, ()))
    mode = cap.tag_words(tag, key)[0]
    if mode == "motion":
        count = len(str(line.text).split())
        why = _block_problem(scene, line, count, own)
        if why:
            fallback = "NONE" if _block_problem(scene, line, 1, own) else "HIGHLIGHT"
            _demote(scene, name, fallback, "the new text doesn't fit its word motions (" + why + ")")
            line = pool.get(name)
            tag = tag_of(line) or {}
            mode = cap.tag_words(tag, key)[0]
    old = [w for w in (pool.get(n) for n in own) if w is not None]
    highlight = tuple(old[0].color) if old and mode != "motion" else None
    old_names = [w.name for w in old]
    top = _top(ed)
    from .wand.prefetch import remove_strips    # the Sequencer's prefetch stopped first (#536)
    remove_strips(top, old)
    old = None
    if old_names:
        _drop_fcurves(scene, old_names)
    line = pool.get(name)
    if line is None:
        return False
    if mode == "none":
        return False
    st = cap.resolve_style(key, scene.render.resolution_x, scene.render.resolution_y, tag.get("position", "BOTTOM"),
                           float(tag.get("margin") or 0.0), tag.get("choices") or None)
    text = str(line.text)               # a line break the artist typed stays: the layout handles lines
    left, right = _left(line), _right(line)
    words = text.split()[_label_words(tag):]            # the label's words are strips with word motions: _add_highlights
    frames = [left + int(v) for v in tag.get("word_frames") or ()]
    if even or len(frames) != len(words):
        timed = [dict(w, frame=int(round(w["start"]))) for w in cap.even_words(" ".join(words), left, right)]
    else:
        timed = [{"w": w, "frame": f} for w, f in zip(words, frames)]
    tag["word_frames"] = [int(w["frame"]) - left for w in timed]     # the one source of every word's time
    line[cap.TAG] = tag
    framed = {"frame_start": left, "frame_end": right, "lines": [text], "words": timed, "label": label}
    style = dict(st, uppercase=False, highlight=highlight or st["highlight"])     # the text already is as shown
    font = line.font or _font()
    made = _add_highlights(scene, line, framed, style, font, _measure(font))
    anim = tag.get("anim")
    if made and (anim is not None or mode == "motion"):
        line = pool.get(name)
        news = [pool.get(n) for n in sorted(_word_names(ed).get(name, ()))]
        if line is not None:
            _animate_any(scene, line, st, [w for w in news if w is not None], words_only=True)
    return bool(made)


def _reposition_words(scene: Any, name: str, word_names: list[str], pos: list[dict[str, Any]], animated: Any) -> None:
    """Caption line *name*'s word strips put on the positions *pos* with the line's font, bold, italic and
    size (no strip is added or removed here)."""
    pool = _all(scene.sequence_editor)
    line = pool.get(name)
    if line is None:
        return
    for n, p in zip(sorted(word_names), pos):
        word = pool.get(n)
        if word is not None:
            if word.text != p["text"]:
                word.text = p["text"]
            _sync_word(word, line, p, animated)


def _hidden_anchors(ed: Any) -> list[str]:
    """The names of the caption lines made with word motions (their line strip draws nothing), found by their
    tag: also those that have lost all their words."""
    out = []
    for s in _top(ed):
        value = s.get(cap.TAG)
        if value is not None and value.get("role") == "line" and s.type == 'TEXT':
            if cap.tag_words(dict(value))[0] == "motion":
                out.append(s.name)
    return out


def _adopt_words(scene: Any) -> int:
    """After a hard cut (Shift+K) through an anchor and its words, every piece of a word still names the first
    anchor: each word strip goes to the anchor piece on its channels that it starts in, and a piece's word
    frames (copied from the first piece) are moved to its own start.  Their keys follow the new times.  Only
    tags change, no strip is added or removed.  How many anchors got words."""
    ed = scene.sequence_editor
    pool = _all(ed)
    lines: dict[str, Any] = {}
    words = []
    for s in _top(ed):
        value = s.get(cap.TAG)
        if value is None:
            continue
        role = value.get("role")
        if role == "line" and s.type == 'TEXT':
            lines[s.name] = s
        elif role == "word":
            words.append(s)
    moved: dict[str, str] = {}
    for w in words:
        tag = tag_of(w) or {}
        home = lines.get(str(tag.get("line")))
        if home is None or _left(home) <= _left(w) < _right(home):
            continue
        other = next((a for a in lines.values() if a.channel == home.channel and a.name != home.name
                      and _left(a) <= _left(w) < _right(a)), None)
        if other is None:
            continue
        tag["line"] = other.name
        w[cap.TAG] = tag
        moved.setdefault(other.name, home.name)
    for name, origin in moved.items():
        line, first = pool.get(name), pool.get(origin)
        if line is None or first is None:
            continue
        tag, first_tag = tag_of(line) or {}, tag_of(first) or {}
        if tag.get("word_frames") and tag.get("word_frames") == first_tag.get("word_frames"):
            shift = _left(line) - _left(first)
            tag["word_frames"] = [int(v) - shift for v in tag["word_frames"]]
            line[cap.TAG] = tag
        if cap.tag_words(tag)[0] == "motion":
            _reanimate_words(scene, line, tag_of(line) or tag)
    return len(moved)


@_batched
def refit_words(scene: Any, names: Any = None, force: bool = False, rebuild: bool = False) -> int:
    """Word strips laid out again where their caption line's settings changed (#594): its font, bold,
    italic, size, alignment, wrap width, place, transform or text, or the frame size.  *names*: only
    these caption lines (None: all of *scene*); *force*: also those whose settings did not change.

    The words are moved and restyled in place when there are as many as the text has words (their own
    frames stay).  When the words changed, the strips are made again only with *rebuild* (the Refit
    operator; ``text_changed`` does it by itself): the automatic path never adds or removes a strip,
    so it leaves such a caption as it is.  A caption with no signature (made before #594) only has its
    signature noted, unless *force*: its word strips are not touched by opening the file.  How many
    captions were fitted again.  Cheap when nothing changed: one signature per caption with word strips,
    and no font is measured or loaded."""
    ed = scene.sequence_editor
    if ed is None:
        return 0
    done = _adopt_words(scene)
    by_line = _word_names(ed)
    wanted = None if names is None else set(names)
    pool = _all(ed)
    for name in _hidden_anchors(ed):
        if name in by_line or (wanted is not None and name not in wanted):
            continue
        # A clear anchor whose words are all gone: its words again (the operator), else it draws again.
        if rebuild:
            if _rebuild_words(scene, name, even=False):
                done += 1
                continue
        _demote(scene, name, "NONE", "its word strips are gone")
        done += 1
    todo = [n for n in by_line if wanted is None or n in wanted]
    if not todo:
        return done
    for name in todo:
        line = pool.get(name)
        if line is None or line.type != 'TEXT':
            continue
        tag = tag_of(line) or {}
        if tag.get("role") != "line":
            continue
        animated = _preset_paths(tag)
        sig = _signature_of(scene, line, tag)
        if not force and tag.get("lay") == sig:
            continue
        if not force and tag.get("lay") is None:
            _remember_layout(ed, name, sig)         # made before #594: just note it
            continue
        words = str(line.text).split()[_skip(tag):]
        same = len(words) == len(by_line[name])
        if not same and not rebuild:
            continue                                # needs strips added or removed: not from a timer
        if same:
            _sig, pos, _measure_line = _layout_of(scene, line, tag, animated)
            if not pos or len(pos) != len(words):
                continue                            # the font can't be measured: nothing to place by
            _reposition_words(scene, name, by_line[name], pos, animated)
            _remember_layout(ed, name, sig)
            if cap.tag_words(tag)[0] == "motion":   # the words moved: their offsets (a pop around a middle) follow
                line = _all(ed).get(name)
                if line is not None:
                    _reanimate_words(scene, line, tag)
        elif not _rebuild_words(scene, name, even=False):
            continue
        done += 1
    return done


# The word strips follow their line by themselves: a cheap depsgraph handler notes that something
# changed, a timer a moment later (debounced) compares each caption's layout signature, and never
# while playing, rendering, a modal operator runs, or an agent turn is open; the timer only moves and
# restyles existing word strips, it never adds or removes one.
_WATCH: dict[str, Any] = {"dirty": set(), "t": 0.0}
DEBOUNCE_S = 0.4
RETRY_S = 1.5


def _playing() -> bool:
    """A window is playing the animation."""
    try:
        return any(win.screen is not None and win.screen.is_animation_playing
                   for win in bpy.context.window_manager.windows)
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _busy() -> str:
    """Why captions must not be touched now ("" when they can): playback, a render, or the co-work
    lock or an open turn of an agent, or a modal operator (a drag) running in a window."""
    if _playing():
        return "playing"
    try:
        # Coworker's own chat input is a modal operator that is always running: not that one.
        if any(not str(op.bl_idname).startswith("BFACW_OT_") for win in bpy.context.window_manager.windows
               for op in win.modal_operators):
            return "a modal operator"
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    try:
        if bpy.app.is_job_running('RENDER'):
            return "rendering"
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        from . import co_work_guard
        if co_work_guard.is_locked():
            return "an agent step"
    except ImportError:
        pass
    try:
        from . import coworker_api
        if coworker_api.any_turn_open():
            return "an agent turn"
    except ImportError:
        pass
    return ""


def _visible_scenes() -> set[str]:
    """The names of the scenes being edited: each window's scene and Sequencer scene."""
    names = set()
    for win in getattr(bpy.context.window_manager, "windows", ()):
        names.add(win.scene.name)
        seq = getattr(win.workspace, "sequencer_scene", None)
        if seq is not None:
            names.add(seq.name)
    return names or {bpy.context.scene.name}


def _watch_tick() -> float | None:
    """The timer: look at the scenes that changed, once nothing has changed for :data:`DEBOUNCE_S`."""
    if not _WATCH["dirty"]:
        return None
    wait = DEBOUNCE_S - (_clock() - _WATCH["t"])
    if wait > 0:
        return wait
    if _busy():
        return RETRY_S
    names, _WATCH["dirty"] = _WATCH["dirty"], set()
    try:
        for name in names & _visible_scenes():
            scene = bpy.data.scenes.get(name)
            if scene is not None and scene.sequence_editor is not None:
                refit_words(scene)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] Word highlights could not be fitted again: {!s}".format(ex))
    return DEBOUNCE_S if _WATCH["dirty"] else None


@bpy.app.handlers.persistent
def _on_depsgraph(scene: Any, _depsgraph: Any = None) -> None:
    """``depsgraph_update_post``: a scene with strips changed.  Nothing is looked at here; the scene is
    only noted (also while playing: the timer holds it back, so a change during playback is not lost)
    and the timer, a moment after the last change, compares each caption's signature."""
    try:
        if scene.sequence_editor is None:
            return
        _WATCH["dirty"].add(scene.name)
        _WATCH["t"] = _clock()
        if not bpy.app.timers.is_registered(_watch_tick):
            bpy.app.timers.register(_watch_tick, first_interval=DEBOUNCE_S)
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _watch_start() -> None:
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def _watch_stop() -> None:
    try:
        if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
        if bpy.app.timers.is_registered(_watch_tick):
            bpy.app.timers.unregister(_watch_tick)
    except (AttributeError, ValueError):
        pass
    _WATCH["dirty"] = set()
    _blf_forget(all_=True)


# -- Translate (#42 for many captions) ---------------------------------------------------

def translate_request(scene: Any, strips: list) -> tuple[str, str, str]:
    """``(path, current, what)`` for ``bfacw.translate`` over *strips*."""
    path = cap.TRANSLATE_PREFIX + json.dumps({"scene": scene.name, "strips": [s.name for s in strips]})
    what = ("the {:d} captions below (one per line, each starting with its number in brackets: keep every "
            "number, and give one translated caption per line)").format(len(strips))
    return path, cap.batch_text([s.text for s in strips]), what


def set_caption_text(scene: Any, strip: Any, text: str) -> None:
    """*text* into a caption strip, laid out for its style (and the letter case it was made in, #594);
    its word strips follow."""
    tag = tag_of(strip) or {}
    key = tag.get("style") or ""
    style = cap.STYLES.get(key, cap.STYLES["CLEAN"])
    if style["highlight"]:      # one line: the word strips are placed along it
        new = " ".join(text.split())
    else:
        new = "\n".join(cap.layout_lines(text.split(), int(tag.get("chars") or 36), int(tag.get("lines") or 2),
                                         force=True) or [text])
    strip.text = cap.apply_case(new, cap.effective_case(key, (tag.get("choices") or {}).get("case")))
    text_changed(strip, scene)


def text_changed(strip: Any, scene: Any = None) -> None:
    """A Word-by-word caption's text changed (Translate): its word strips are made again,
    the words spread over the line's time."""
    tag = tag_of(strip) or {}
    if tag.get("role") != "line":
        return
    if cap.tag_words(tag)[0] == "none":
        return
    scene = scene or strip.id_data
    _rebuild_words(scene, strip.name, even=True)


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

def _look_text(result: dict) -> str:
    """``"Clean"``, ``"Clean, Fade, top"`` -- the style, and the animation / position when
    they aren't the defaults (#542)."""
    parts = [str(result.get("style") or "")]
    anim, word = result.get("animation"), result.get("word_animation")
    if anim and anim != cap.ANIMATIONS[cap.ANIM_STYLE]["label"]:
        parts.append(anim)
    if word and word not in (cap.WORD_ANIMATIONS["STYLE"]["label"], cap.WORD_ANIMATIONS["NONE"]["label"]):
        parts.append("words: " + word)
    if result.get("position") and result["position"] != "BOTTOM":
        parts.append(cap.POSITIONS[result["position"]]["label"].lower())
    return ", ".join(p for p in parts if p)


def _speakers_text(speakers: list) -> str:
    """``"Speakers: Ana on channel 4 (12), Ben on channel 5 (9)"``."""
    return "Speakers: " + ", ".join("{:s} on channel {:d} ({:d})".format(s["name"], s["channel"], s["lines_created"])
                                    for s in speakers)


def _turn_owns_undo() -> bool:
    """A Coworker chat turn runs this tool call (#501): the turn's own undo step covers it."""
    try:
        from . import coworker_api
    except ImportError:
        return False
    return coworker_api.turn_owns_undo()


def tool_add_captions(strips: Any = None, style: str = "CLEAN", language: str | None = None,
                      transcript_path: str | None = None, replace_edited: bool = False,
                      animation: Any = cap.ANIM_STYLE, position: Any = "BOTTOM", margin: Any = 0,
                      speakers: bool = False, speaker_labels: bool = False,
                      speaker_settings: Any = None, case: Any = None, font: Any = None, bold: Any = None,
                      italic: Any = None, size: Any = None, text_color: Any = None,
                      highlight_color: Any = None, animate: Any = None, line_animation: Any = None,
                      word_animation: Any = None) -> dict[str, Any]:
    """``add_captions`` (MCP): a result dict, ``status`` ``"ok"`` or ``"error"`` + ``message``.
    *margin* in percent of the frame's height (#542); *case*, *font*, *bold*, *italic*, *size*,
    *text_color* and *highlight_color* are the style choices of #594 (none: as the style has it);
    *animate*, *line_animation* and *word_animation* (#594) are what moves and how: *animation* is the
    single list of before and still works (it is the line animation)."""
    try:
        names, key, lang = cap.check_tool_params(strips, style, language)
        anim, pos, margin_frac = cap.check_look_params(animation, position, margin)
        choices = cap.check_style_params(case, font, bold, italic, size, text_color, highlight_color)
        animate_k, line_k, word_k = cap.check_animation_params(animate, line_animation, word_animation)
        per_speaker, settings = cap.check_speakers(speakers, speaker_settings)
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
        result = add_captions(scene, found, key, lang, transcript_path or "", replace_edited=bool(replace_edited),
                              animation=anim, position=pos, margin=margin_frac, speakers=per_speaker,
                              speaker_settings=settings, speaker_labels=bool(speaker_labels), choices=choices,
                              animate=animate_k, line_animation=line_k, word_animation=word_k)
        result["message"] = "{:d} caption lines on channel {:d} ({:s}), frames {:d}-{:d}, {:s}.".format(
            result["lines_created"], result["channel"], _look_text(result), result["first_frame"],
            result["last_frame"],
            cap.strips_report(result["strips_captioned"], result["strips_skipped"], result.get("kept_edited", 0)))
        if result.get("speakers"):
            result["message"] += " " + _speakers_text(result["speakers"]) + "."
        if result.get("note"):
            result["message"] += " " + result["note"]
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


def _lines_items_for(count: int) -> list[tuple]:
    """The Lines drop-down for a style that uses *count* lines: every entry says what it counts."""
    return [("STYLE", "As the Style ({:d} Line{:s})".format(count, "" if count == 1 else "s"),
             "Use the number of lines the style has", 'PRESET', 0),
            ("1", "1 Line", "Every caption on one line (the words are split into more captions)", 'NONE', 1),
            ("2", "2 Lines", "Up to two lines per caption", 'NONE', 2)]


_LINES_BY_COUNT = {1: _lines_items_for(1), 2: _lines_items_for(2)}


def _lines_items(self: Any, _context: Any) -> list[tuple]:
    """Lines: "As the Style (N Lines)" with the number the chosen style and animation give."""
    key = cap.style_key(getattr(self, "style", "CLEAN")) or "CLEAN"
    plan = _plan_of(self)
    one = (cap.STYLES[key]["highlight"] and plan["words"] != "none") or cap.plan_one_line(plan)
    return _LINES_BY_COUNT[1 if one or cap.STYLES[key]["max_lines"] <= 1 else 2]


def _plan_of(op: Any) -> dict[str, str]:
    """:func:`captions.resolve_animation` of the dialog's Animate, Line Animation and Word Animation (the
    two lists only count when Animate isn't As the Style); the old single Animation list is the line animation
    while Animate is left as the Style."""
    animate = getattr(op, "animate", "STYLE")
    own = animate == "STYLE"
    return cap.resolve_animation(getattr(op, "style", "CLEAN"), animate,
                                 None if own else getattr(op, "line_animation", None),
                                 None if own else getattr(op, "word_animation", None),
                                 getattr(op, "animation", None) if own else None)


def _language_items() -> list[tuple[str, str, str]]:
    from .preferences import _LANGUAGES
    items = [("auto", "Detect Automatically", "Let the transcription find the language that is spoken")]
    items += [(code, name, "Spoken in " + name) for code, name in _LANGUAGES]
    return items


_LANGUAGE_ITEMS = _language_items()


def _lines_value(value: str) -> int:
    return 0 if value == "STYLE" else int(value)


_ANIM_ICONS = {cap.ANIM_STYLE: 'PRESET', "NONE": 'X', "FADE": 'IMAGE_ALPHA', "SLIDE_UP": 'TRIA_UP',
               "SLIDE_DOWN": 'TRIA_DOWN', "TYPEWRITER": 'TEXT', "POP": 'ZOOM_IN'}
_ANIMATE_ICONS = {"STYLE": 'PRESET', "LINE": 'ALIGN_JUSTIFY', "WORD": 'SMALL_CAPS', "BOTH": 'ANIM', "NONE": 'X'}
_WORD_ICONS = {"STYLE": 'PRESET', "NONE": 'X', "HIGHLIGHT": 'COLOR', "POP": 'ZOOM_IN', "SCALE": 'FULLSCREEN_ENTER',
               "RISE": 'TRIA_UP', "FADE_IN": 'IMAGE_ALPHA'}
_POSITION_ICONS = {"BOTTOM": 'ANCHOR_BOTTOM', "TOP": 'ANCHOR_TOP', "MIDDLE": 'ANCHOR_CENTER'}
_CASE_ICONS = {cap.STYLE_CASE: 'PRESET', "AS_SPOKEN": 'TEXT', "UPPER": 'SMALL_CAPS', "LOWER": 'FONT_DATA',
               "TITLE": 'SORTALPHA'}
# Icons through safe_icon (a Blender icon in every build; none of them is Bforartists-only), one per entry.
_ANIM_ITEMS = [(k, a["label"], a["description"], safe_icon(_ANIM_ICONS.get(k, 'NONE')), i)
               for i, (k, a) in enumerate(cap.ANIMATIONS.items())]
_ANIMATE_ITEMS = [(k, a["label"], a["description"], safe_icon(_ANIMATE_ICONS.get(k, 'NONE')), i)
                  for i, (k, a) in enumerate(cap.ANIMATE.items())]
_WORD_ITEMS = [(k, a["label"], a["description"], safe_icon(_WORD_ICONS.get(k.split("_", 1)[-1] if k.startswith("HIGHLIGHT_")
                                                                       else k, 'NONE')), i)
               for i, (k, a) in enumerate(cap.WORD_ANIMATIONS.items())]
_POSITION_ITEMS = [(k, p["label"], p["description"], safe_icon(_POSITION_ICONS.get(k, 'NONE')), i)
                   for i, (k, p) in enumerate(cap.POSITIONS.items())]
_CASE_ITEMS = [(k, c["label"], c["description"], safe_icon(_CASE_ICONS.get(k, 'NONE')), i)
               for i, (k, c) in enumerate(cap.CASES.items())]
_TRI_ITEMS = {name: [("STYLE", "As the Style", "The style's own: " + what[0], 'NONE', 0),
                     ("ON", "On", what[1], 'NONE', 1), ("OFF", "Off", what[2], 'NONE', 2)]
              for name, what in (("bold", ("bold or not, as the style has it", "Bold text", "Regular weight text")),
                                 ("italic", ("never italic", "Slanted text", "Upright text")))}
_TRI_VALUE = {"STYLE": None, "ON": True, "OFF": False}
_SAME = ("SAME", "Same as Above", "As set above for every speaker")
_FONT_ITEMS: list[tuple] = []       # kept alive: Blender reads the strings of a dynamic enum later


def _font_items(_self: Any, _context: Any) -> list[tuple]:
    """Font: the style's own, each font loaded in this file, or a font file."""
    items = [("STYLE", "As the Style", "The style's own font: Blender's bundled Inter", safe_icon('FONT_DATA'), 0)]
    for f in bpy.data.fonts:
        if not str(f.filepath).startswith("<builtin"):
            items.append(("FONT:" + f.name, f.name, "A font loaded in this file", safe_icon('FONT_DATA'),
                          len(items)))
    items.append(("FILE", "A Font File...", "Pick a font file on disk (.ttf, .otf, .woff2...); it is loaded "
                                            "into this file", safe_icon('FILE_FONT'), len(items)))
    _FONT_ITEMS[:] = items
    return _FONT_ITEMS


class BFACW_PG_caption_speaker(PropertyGroup):  # type: ignore[misc]
    """One speaker of Auto Captions (#542): a sound file, their name, style and position."""
    strip: StringProperty(name="Strip", default="", options={'HIDDEN'})  # type: ignore[valid-type]
    name: StringProperty(  # type: ignore[valid-type]
        name="Name", default="", description="The speaker's name: their caption channel's, and the label in front "
                                             "of their captions")
    style: EnumProperty(  # type: ignore[valid-type]
        name="Style", items=[_SAME] + _STYLE_ITEMS, default="SAME",
        description="How this speaker's captions look (Same as Above: the style chosen above)")
    position: EnumProperty(  # type: ignore[valid-type]
        name="Position", items=[_SAME] + [(i[0], i[1], i[2]) for i in _POSITION_ITEMS], default="SAME",
        description="Where this speaker's captions sit (Same as Above: the position chosen above)")


def speaker_rows(scene: Any, sources: list) -> list[tuple[str, str]]:
    """``(strip, name)`` per speaker of *sources* (one per media file, as Auto Captions plans
    them: a movie heard through its sound strip is that sound's speaker)."""
    ed = scene.sequence_editor
    items = []
    for s in _media_strips(sources):
        path = media_path(s)
        if path:
            items.append(_strip_item(ed, s, path))
    todo, _skipped, _partners = cap.plan_strips(items)
    seen, out = set(), []
    for it in todo:
        if it["key"] not in seen:
            seen.add(it["key"])
            out.append((it["name"], cap.speaker_name(it["name"])))
    return out


def _fill_speakers(op: Any, scene: Any, sources: list) -> None:
    """The operator's speaker rows for *sources*: names, styles and positions set before for
    the same strips are kept."""
    rows = speaker_rows(scene, sources)
    old = {it.strip: (it.name, it.style, it.position) for it in op.speaker_list}
    if [it.strip for it in op.speaker_list] == [r[0] for r in rows]:
        return
    op.speaker_list.clear()
    for strip, name in rows:
        item = op.speaker_list.add()
        item.strip = strip
        item.name, item.style, item.position = old.get(strip, (name, "SAME", "SAME"))


class BFACW_OT_auto_captions(Operator):  # type: ignore[misc]
    """Caption the selected sound or movie strips: styled text strips on a Captions channel, in sync with the speech"""
    bl_idname = "bfacw.auto_captions"
    bl_label = "Auto Captions"
    bl_description = agent_line(__doc__, also="Needs Speech set up once (Preferences) to transcribe.")
    bl_options = {'REGISTER', 'UNDO'}

    style: EnumProperty(  # type: ignore[valid-type]
        name="Style", items=_STYLE_ITEMS, default="CLEAN",
        description="How the captions look: size, colours, outline, box and whether the spoken word lights up")
    language: EnumProperty(  # type: ignore[valid-type]
        name="Language", items=_LANGUAGE_ITEMS, default="auto",
        description="The language that is spoken, so it is transcribed right (Detect: let the transcription find it)")
    max_chars: IntProperty(  # type: ignore[valid-type]
        name="Max Characters", default=0, min=0, max=80,
        description="Characters per line (0: what fits the frame in this style)")
    lines: EnumProperty(  # type: ignore[valid-type]
        name="Lines", items=_lines_items,
        description="How many lines a caption may have; words that don't fit become another caption")
    transcript_path: StringProperty(  # type: ignore[valid-type]
        name="Transcript", subtype='FILE_PATH', default="",
        description="A transcript file (.json, bfa_coworker.transcript/1) to caption from instead of transcribing")
    replace_edited: BoolProperty(  # type: ignore[valid-type]
        name="Replace Edited Captions", default=False,
        description="Replace captions changed by hand since Auto Captions made them (reworded, moved, trimmed); "
                    "off: they are kept")
    animation: EnumProperty(  # type: ignore[valid-type]
        name="Animation", items=_ANIM_ITEMS, default=cap.ANIM_STYLE, options={'HIDDEN'},
        description="The single animation list of before: the line animation (use Animate, Line Animation and Word "
                    "Animation instead)")
    animate: EnumProperty(  # type: ignore[valid-type]
        name="Animate", items=_ANIMATE_ITEMS, default="STYLE",
        description="What moves: each line as a whole, each word at its own time, both, or nothing (As the Style: "
                    "Bold Pop pops in, Word-by-word lights the spoken word, the others stand still)")
    line_animation: EnumProperty(  # type: ignore[valid-type]
        name="Line Animation", items=_ANIM_ITEMS, default="FADE",
        description="How each caption line moves in and out as a whole")
    word_animation: EnumProperty(  # type: ignore[valid-type]
        name="Word Animation", items=_WORD_ITEMS, default="HIGHLIGHT",
        description="What each word does at its own time: light up in the highlight colour, pop, scale, rise or "
                    "fade in (a word motion makes every word its own strip)")
    position: EnumProperty(  # type: ignore[valid-type]
        name="Position", items=_POSITION_ITEMS, default="BOTTOM",
        description="Where the captions sit in the frame: near the bottom, near the top or in the middle")
    margin: IntProperty(  # type: ignore[valid-type]
        name="Margin", default=0, min=0, max=45, subtype='PERCENTAGE',
        description="How far the captions stay from the frame's edge, in percent of its height (0: the safe area "
                    "of a wide or vertical frame)")
    case: EnumProperty(  # type: ignore[valid-type]
        name="Letter Case", items=_CASE_ITEMS, default=cap.STYLE_CASE,
        description="Capital and small letters of the caption text (As the Style: capitals for Bold Pop, the "
                    "words as spoken for the others)")
    font_choice: EnumProperty(  # type: ignore[valid-type]
        name="Font", items=_font_items,
        description="The caption font: the style's own, a font already in this file, or a font file")
    font_file: StringProperty(  # type: ignore[valid-type]
        name="Font File", subtype='FILE_PATH', default="",
        description="A font file (.ttf, .otf, .woff2...) for the captions; it is loaded into this file")
    bold: EnumProperty(  # type: ignore[valid-type]
        name="Bold", items=_TRI_ITEMS["bold"], default="STYLE",
        description="Bold captions or not (As the Style: bold only where the style is)")
    italic: EnumProperty(  # type: ignore[valid-type]
        name="Italic", items=_TRI_ITEMS["italic"], default="STYLE",
        description="Italic (slanted) captions or not (As the Style: upright)")
    size: FloatProperty(  # type: ignore[valid-type]
        name="Size", default=1.0, min=cap.MIN_SIZE_SCALE, max=cap.MAX_SIZE_SCALE, step=5, precision=2,
        description="The text size as a scale of the style's own size (1 = as the style has it, 1.5 = half "
                    "again as big)")
    set_text_color: BoolProperty(  # type: ignore[valid-type]
        name="Own Text Colour", default=False,
        description="Use the colour chosen here for the text instead of the style's own")
    text_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="Text Colour", subtype='COLOR', size=4, min=0.0, max=1.0, default=(1.0, 1.0, 1.0, 1.0),
        description="The colour of the caption text")
    set_highlight_color: BoolProperty(  # type: ignore[valid-type]
        name="Own Highlight Colour", default=False,
        description="Use the colour chosen here for the word that is spoken (Word-by-word highlight) instead of "
                    "the style's own")
    highlight_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="Highlight Colour", subtype='COLOR', size=4, min=0.0, max=1.0, default=(1.0, 0.82, 0.1, 1.0),
        description="The colour of the word that is being spoken")
    speakers: BoolProperty(  # type: ignore[valid-type]
        name="A Channel per Speaker", default=False,
        description="Each selected sound file is one speaker: their captions go on a channel of their own, with "
                    "their own style and position if you like")
    speaker_labels: BoolProperty(  # type: ignore[valid-type]
        name="Name Labels", default=False, description="Each caption starts with its speaker's name (\"Ana: ...\")")
    speaker_list: CollectionProperty(type=BFACW_PG_caption_speaker)  # type: ignore[valid-type]

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
        scene = seq_scene(context)
        _fill_speakers(self, scene, [s for s in _media_strips(_top(scene.sequence_editor)) if s.select])
        return context.window_manager.invoke_props_dialog(self, width=380, title="Auto Captions",
                                                          confirm_text="Add Captions")

    def draw(self, context: Any) -> None:
        layout = self.layout
        layout.use_property_split = True
        scene = seq_scene(context)
        plan = _plan_of(self)
        highlight = cap.word_parts(plan["word"])[0]
        # Style: how the captions look.
        box = layout.box()
        box.label(text="Style: how the captions look", icon=safe_icon('FONT_DATA'))
        col = box.column()
        col.use_property_split = True
        col.prop(self, "style")
        col.prop(self, "case")
        col.prop(self, "font_choice")
        if self.font_choice == "FILE":
            col.prop(self, "font_file")
        col.prop(self, "bold")
        col.prop(self, "italic")
        col.prop(self, "size")
        row = col.row(align=True)
        row.prop(self, "set_text_color", text="Text")
        sub = row.row(align=True)
        sub.enabled = self.set_text_color
        sub.prop(self, "text_color", text="")
        row = col.row(align=True)
        row.prop(self, "set_highlight_color", text="Highlight")
        sub = row.row(align=True)
        sub.enabled = self.set_highlight_color and highlight
        sub.prop(self, "highlight_color", text="")
        if not highlight:
            col.label(text="The highlight colour is for a Highlight word animation.", icon=safe_icon('INFO'))
        col.prop(self, "position")
        row = col.row()
        row.enabled = self.position != "MIDDLE"
        row.prop(self, "margin")
        if self.margin == 0 and self.position != "MIDDLE":
            kind = cap.frame_kind(scene.render.resolution_x, scene.render.resolution_y)
            row.label(text="Auto: {:d} %".format(int(round(cap.default_margin(self.position, kind) * 100))))
        # Motion: how they move.
        box = layout.box()
        box.label(text="Motion: how the captions move", icon=safe_icon('ANIM'))
        col = box.column()
        col.use_property_split = True
        col.prop(self, "animate")
        if self.animate in ("LINE", "BOTH"):
            col.prop(self, "line_animation")
        if self.animate in ("WORD", "BOTH"):
            col.prop(self, "word_animation")
        if plan["words"] == "motion":
            _wrapped(col.column(align=True), "Each word is a strip of its own on a channel of its own, and the "
                     "caption's line strip stays as the anchor and draws nothing.", 'INFO')
        # The words: what is transcribed and how it is split.
        layout.prop(self, "language")
        row = layout.row()
        row.prop(self, "max_chars")
        if self.max_chars == 0:
            row.label(text="Auto: {:d}".format(cap.default_max_chars(
                self.style, scene.render.resolution_x, scene.render.resolution_y, self._choices_or_none())))
        sub = layout.row()
        sub.enabled = not (cap.STYLES[self.style]["highlight"] and plan["words"] != "none")             and not cap.plan_one_line(plan)
        sub.prop(self, "lines")
        layout.prop(self, "transcript_path")
        layout.prop(self, "replace_edited")
        if len(self.speaker_list) > 1 or self.speakers:
            layout.prop(self, "speakers")
            if self.speakers:
                layout.prop(self, "speaker_labels")
                col = layout.column(align=True)
                col.use_property_split = False
                col.label(text="Each speaker: name, style, position")
                for item in self.speaker_list:
                    r = col.row(align=True)
                    r.prop(item, "name", text="")
                    r.prop(item, "style", text="")
                    r.prop(item, "position", text="")
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
                                  self.transcript_path, self.max_chars, _lines_value(self.lines), self.replace_edited,
                                  **self._look(scene, sources))
        except CaptionsError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        try:
            from . import transcript_edit_ops    # a transcript's changed words follow its captions (#595)
            transcript_edit_ops.captions_made(scene, self.transcript_path)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] Auto Captions: the transcript's words not applied: {!s}".format(ex))
        self.report({'WARNING'} if _warn(result) else {'INFO'}, _summary(result))
        return {'FINISHED'}

    def _look(self, scene: Any, sources: list) -> dict[str, Any]:
        """The #542 arguments of :func:`add_captions` from the dialog."""
        settings = []
        if self.speakers:
            _fill_speakers(self, scene, sources)
            for it in self.speaker_list:
                row = {"strip": it.strip, "name": " ".join(it.name.split())[:cap.SPEAKER_NAME_CHARS]}
                if it.style != "SAME":
                    row["style"] = it.style
                if it.position != "SAME":
                    row["position"] = it.position
                settings.append(row)
        own = self.animate == "STYLE"
        return {"animation": self.animation if own else cap.ANIM_STYLE, "animate": self.animate,
                "line_animation": None if own else self.line_animation,
                "word_animation": None if own else self.word_animation,
                "position": self.position, "margin": self.margin / 100.0,
                "speakers": bool(self.speakers), "speaker_settings": settings,
                "speaker_labels": bool(self.speaker_labels), "choices": self._choices()}

    def _choices(self) -> dict[str, Any]:
        """The dialog's style choices (#594), as :func:`captions.check_style_params` checks them;
        raises :class:`CaptionsError`."""
        font = ""
        if self.font_choice.startswith("FONT:"):
            font = self.font_choice[len("FONT:"):]
        elif self.font_choice == "FILE":
            if not self.font_file.strip():
                raise CaptionsError("Pick a font file, or choose another font.")
            font = self.font_file
        return cap.check_style_params(
            self.case, font, _TRI_VALUE[self.bold], _TRI_VALUE[self.italic], self.size,
            tuple(self.text_color) if self.set_text_color else None,
            tuple(self.highlight_color) if self.set_highlight_color else None)

    def _choices_or_none(self) -> dict[str, Any] | None:
        try:
            return self._choices()
        except CaptionsError:
            return None

    # -- transcribing first (#516) --
    def _transcribe_first(self, context: Any, scene: Any, sources: list, language: str | None) -> set[str]:
        """Start the transcription; with a window, wait for the captions here (modal: Esc stops,
        the status bar shows the progress, this operator's undo step and report hold the
        captions).  Without one (a script), or from Adjust Last Operation (no modal there), a
        timer makes them on its own."""
        try:
            look = self._look(scene, sources)
        except CaptionsError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        params = dict({"style": self.style, "language": language, "max_chars": self.max_chars,
                       "max_lines": _lines_value(self.lines), "replace_edited": self.replace_edited}, **look)
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


def _warn(result: dict) -> bool:
    """The report is a warning: a note, a strip skipped or a hand-edited caption kept (#538)."""
    return bool(result.get("note") or result.get("strips_skipped") or result.get("kept_edited"))


def _summary(result: dict) -> str:
    text = "{:d} captions on channel {:d}".format(result["lines_created"], result["channel"])
    if result.get("replaced"):
        text += " (replacing {:d})".format(result["replaced"])
    if "strips_captioned" in result:            # Auto Captions: which strips, which skipped and why (#538)
        text += " " + cap.strips_report(result["strips_captioned"], result.get("strips_skipped") or (),
                                        result.get("kept_edited", 0))
    if result.get("speakers"):                  # a channel per speaker (#542)
        text += "; " + _speakers_text(result["speakers"])
    if result.get("skipped"):
        text += "; {:d} broken cues skipped".format(result["skipped"])
    if result.get("note"):
        text += ". " + result["note"]
    return text


class BFACW_OT_captions_import_srt(Operator, ImportHelper):  # type: ignore[misc]
    """Import an SRT subtitle file as styled caption strips at its cue times"""
    bl_idname = "bfacw.captions_import_srt"
    bl_label = "Import SRT"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".srt"
    filter_glob: StringProperty(default="*.srt", options={'HIDDEN'})  # type: ignore[valid-type]
    style: EnumProperty(  # type: ignore[valid-type]
        name="Style", items=_STYLE_ITEMS, default="CLEAN",
        description="How the imported captions look: size, colours, outline, box")
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
    bl_description = agent_line(__doc__)

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


class BFACW_OT_captions_refit_words(Operator):  # type: ignore[misc]
    """Put the word highlights of Word-by-word captions back on their words, from the caption's current font, bold, italic, size, alignment, wrap width and position"""
    bl_idname = "bfacw.captions_refit_words"
    bl_label = "Refit Word Highlights"
    bl_description = agent_line("Put the word highlights of Word-by-word captions back on their words: after the "
                                "caption's font, bold, italic, size, alignment, wrap width, position or text changed. "
                                "This happens by itself a moment after the change; this is the manual way. Selected "
                                "captions, else all")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Any) -> bool:
        if not _seq.poll(cls, context):
            return False
        if not _has_word_strips(seq_scene(context)):
            cls.poll_message_set("No word highlights: they come with the Word-by-word highlight style")
            return False
        return True

    def execute(self, context: Any) -> set[str]:
        scene = seq_scene(context)
        names = _selected_caption_names(scene)
        del NOTES[:]
        done = refit_words(scene, names or None, force=True, rebuild=True)
        self.report({'INFO'}, "Word highlights fitted again on {:d} caption{:s}".format(done, "" if done == 1 else "s"))
        if NOTES:
            self.report({'WARNING'}, " ".join(NOTES[:3]))
        return {'FINISHED'}


def _has_word_strips(scene: Any) -> bool:
    """The scene has word strips (a light look: no tag copies)."""
    ed = getattr(scene, "sequence_editor", None)
    if ed is None:
        return False
    for s in _top(ed):
        value = s.get(cap.TAG)
        if value is not None and (value.get("role") == "word"
                                  or (value.get("role") == "line" and cap.tag_words(dict(value))[0] == "motion")):
            return True
    return False


def _selected_caption_names(scene: Any) -> list[str]:
    """The caption lines of the selected caption strips (a selected word strip stands for its line)."""
    ed = scene.sequence_editor
    out = []
    for s in _top(ed):
        value = s.get(cap.TAG)
        if s.select and value is not None:
            line = value.get("line") if value.get("role") == "word" else s.name
            if line:
                out.append(str(line))
    return list(dict.fromkeys(out))


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
        if _has_word_strips(seq_scene(_context)):
            layout.operator("bfacw.captions_refit_words", text="Refit Word Highlights", icon=safe_icon('FILE_REFRESH'))


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
    if _has_word_strips(scene):
        layout.operator("bfacw.captions_refit_words", text="Refit Word Highlights", icon=safe_icon('FILE_REFRESH'))


_CLASSES = (BFACW_PG_caption_speaker, BFACW_OT_auto_captions, BFACW_OT_captions_import_srt, BFACW_OT_captions_export_srt,
            BFACW_OT_captions_refit_words, BFACW_MT_captions)
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
    _watch_start()


def unregister() -> None:
    _watch_stop()
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
