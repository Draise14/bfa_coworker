# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Transcribe a Sequencer strip (Tier 4, task "Video 1", #375): the ``transcribe_strip`` tool.

On the main thread (the bridge runs tool-code there): find the strip and its media file,
key it (:func:`transcripts.media_key`) and answer from the cache when the file was
transcribed before.  Otherwise a worker thread reads the sound with Blender's ``aud`` and
writes a 16 kHz mono WAV (no ffmpeg; the whole file, since the cache is per media file), starts
whisper-server when needed, POSTs the WAV and stores the transcript.  The tool answers
with a checker (the ``deferred_tool`` convention): a short clip answers when it is done; a
long one is handed to ``long_jobs`` after 45 s and the agent gets a ``job_id`` to wait on
with ``get_job_status``.

The words come back in media seconds plus timeline frames for the strip
(:func:`transcripts.strip_words`), only those where the strip is visible, a page at a
time (*limit* / *offset*).

``bpy`` / ``aud`` are only imported inside the functions that need Blender, so the rest is
unit-tested with stand-ins.
"""

from __future__ import annotations

__all__ = (
    "ANY_CHAT",
    "CAPTIONS",
    "COLUMNS",
    "JOB_KIND",
    "chat_owner",
    "plan",
    "repeat_context",
    "setup_problem",
    "settings_from_prefs",
    "shape_result",
    "stop_chat_jobs",
    "stop_jobs",
    "strip_media",
    "strip_timing",
    "transcribe_strip",
    "was_stopped",
)

import os
import tempfile
import threading
import time
from typing import Any, Callable

from . import transcripts
from . import whisper_manager

JOB_KIND = "TRANSCRIBE"
# A word in the tool's answer: the word, seconds in the media file, frames on the timeline.
COLUMNS = ("w", "start", "end", "frame_start", "frame_end")
DEFAULT_LIMIT = 150
MAX_LIMIT = 2000
_TEXT_MAX = 6000

_jobs_lock = threading.Lock()
_jobs: dict[tuple[str, str, str], "_Job"] = {}
# Held while a worker checks "stopped?" and starts whisper-server, and while stop_jobs stops
# it: a Stop can't fall between the two and leave a server started after it (#516).
_start_lock = threading.Lock()
STOPPED = "Stopped before it finished; nothing was saved."

# Who waits for a transcription (#522): a chat's turn ("chat:" + its tag; the bridge says which
# chat calls), Auto Captions, or "" (anything else).  A Stop ends the waiting of its own callers
# only; the job itself ends when nobody waits for it any more.
CAPTIONS = "captions"
ANY_CHAT = "chat:*"         # stop_jobs: every chat's (Stop All Chats)


def chat_owner(tag: str) -> str:
    """The owner of a transcription a chat's turn started (*tag*: the chat's, "" = the default chat)."""
    return "chat:" + str(tag or "")


# -- Settings -----------------------------------------------------------------------------

def settings_from_prefs(prefs: Any) -> dict[str, Any]:
    """The speech settings (main thread) for the worker, which must not touch ``bpy``."""
    from .shared import DEFAULT_WHISPER_PORT, effective_whisper_port
    g = lambda name, default="": getattr(prefs, name, default) if prefs is not None else default  # noqa: E731
    return {
        "device": str(g("speech_device", "AUTO") or "AUTO"),
        "model": str(g("speech_model", "AUTO") or "AUTO"),
        "language": str(g("speech_language", "auto") or "auto"),
        "prompt": str(g("speech_prompt", "") or ""),
        "use_remote": bool(g("speech_use_remote", False)),
        "remote_model": str(g("speech_remote_model", "whisper-1") or "whisper-1"),
        "remote_url": str(g("remote_api_url", "") or ""),
        "remote_key": str(g("remote_api_key", "") or ""),
        "port": int(effective_whisper_port(prefs)) if prefs is not None else DEFAULT_WHISPER_PORT,
    }


_last_settings: list[dict[str, Any]] = []      # the last ones read on the main thread


def repeat_context() -> str:
    """What a ``transcribe_strip`` answer depends on besides its input, for the loop guard
    (#499): the speech settings and the builds / models on disk.  Transcribing a strip again
    after setting speech up or picking another model, device or language is a new call, not
    a repeat.  Off the main thread (the local loop's worker) the preferences are not read
    (that races Blender's context): the settings last read on the main thread are used."""
    if threading.current_thread() is threading.main_thread():
        settings = settings_from_prefs(_prefs())
        _last_settings[:] = [settings]
    else:
        settings = _last_settings[0] if _last_settings else {}
    ready = [p.identifier for p in whisper_manager.MODEL_PRESETS
             if whisper_manager.model_status(p.identifier) == "ready"]
    return "|".join(str(part) for part in (
        settings.get("device", ""), settings.get("model", ""), settings.get("language", ""),
        settings.get("use_remote", ""), settings.get("remote_model", ""),
        bool(settings.get("remote_url")) and bool(settings.get("remote_key")),
        ",".join(whisper_manager.installed_builds()), ",".join(ready)))


def plan(settings: dict[str, Any], model: str = "", language: str = "") -> dict[str, Any]:
    """Which route, build, model and language a call uses (no side effects).

    A server already running (started from Preferences) is used as it is, unless *model*
    asks for another one."""
    lang = (str(language or settings.get("language") or "auto").strip().lower()) or "auto"
    if settings.get("use_remote"):
        name = str(settings.get("remote_model") or "whisper-1")
        return {"route": "remote", "model": name, "cache_model": "remote-" + name, "language": lang, "build": ""}
    st = whisper_manager.get_state()
    alive = st.proc is not None and st.proc.poll() is None
    if alive and st.model and (not model or model == st.model):
        build, name = st.build, st.model
    else:
        build = whisper_manager.resolve_build(str(settings.get("device") or "AUTO"))
        name = whisper_manager.resolve_model(model or str(settings.get("model") or "AUTO"), build)
    return {"route": "local", "model": name, "cache_model": name, "language": lang, "build": build}


def setup_problem(p: dict[str, Any], settings: dict[str, Any]) -> str:
    """"" or what is missing before this plan can run, and how to get it."""
    if p["route"] == "remote":
        if not settings.get("remote_url") or not settings.get("remote_key"):
            return ("Use Remote for speech is on, but no Remote API URL / key is set: Preferences > Add-ons > "
                    "Coworker > Remote Settings.")
        return ""
    problem = whisper_manager.platform_problem()
    if problem:
        return problem
    if whisper_manager.server_exe(p["build"]) is None:
        return ("Speech isn't set up yet: whisper-server ({:s}) isn't downloaded. Open Preferences > Add-ons > "
                "Coworker > Speech and press Download (the CPU build is 9 MB), then download the {:s} model.".format(
                    whisper_manager.BUILD_LABELS.get(p["build"], p["build"]), p["model"]))
    status = whisper_manager.model_status(p["model"])
    if status != "ready":
        preset = whisper_manager.get_preset(p["model"])
        return ("The {:s} speech model isn't downloaded{:s}: Preferences > Add-ons > Coworker > Speech > "
                "Download ({:s}).".format(p["model"], " completely" if status == "incomplete" else "",
                                          whisper_manager._fmt_bytes(preset.size) if preset else "?"))
    return ""


# -- The strip ----------------------------------------------------------------------------

def _sequencer_scene() -> Any:
    import bpy  # pylint: disable=import-error,import-outside-toplevel
    ctx = bpy.context
    scene = getattr(ctx, "sequencer_scene", None)
    if scene is None:
        win = ctx.window or (ctx.window_manager.windows[0] if ctx.window_manager.windows else None)
        scene = getattr(getattr(win, "workspace", None), "sequencer_scene", None) if win else None
    return scene or ctx.scene


def _find_strip(name: str) -> tuple[Any, Any, str]:
    """``(scene, strip, error)``: the strip *name* in the Sequencer's scene (or any scene)."""
    import bpy  # pylint: disable=import-error,import-outside-toplevel
    scenes = [_sequencer_scene()] + [s for s in bpy.data.scenes]
    seen = set()
    for scene in scenes:
        if scene is None or scene.name in seen:
            continue
        seen.add(scene.name)
        ed = scene.sequence_editor
        if ed is None:
            continue
        strips = getattr(ed, "strips_all", None) or getattr(ed, "sequences_all", None) or []
        strip = strips.get(name) if hasattr(strips, "get") else None
        if strip is not None:
            return scene, strip, ""
    scene = _sequencer_scene()
    ed = scene.sequence_editor if scene is not None else None
    if ed is None:
        return None, None, "The scene '{:s}' has no Sequencer strips.".format(scene.name if scene else "?")
    names = [s.name for s in (getattr(ed, "strips_all", None) or [])
             if s.type in ("SOUND", "MOVIE")][:12]
    return None, None, "There is no strip '{:s}'.{:s}".format(
        name, " Sound / movie strips: {:s}.".format(", ".join(names)) if names else " The Sequencer has no sound or movie strips.")


def strip_media(strip: Any) -> tuple[str, str]:
    """``(absolute media path, error)`` for a sound or movie strip."""
    kind = str(getattr(strip, "type", ""))
    if kind == "SOUND":
        sound = getattr(strip, "sound", None)
        raw = getattr(sound, "filepath", "") if sound is not None else ""
        lib = getattr(sound, "library", None)
    elif kind == "MOVIE":
        raw = getattr(strip, "filepath", "")
        lib = None
    else:
        return "", ("'{:s}' is a {:s} strip: it has no sound. Pass a sound or movie strip.".format(
            strip.name, kind.lower() or "non-media"))
    if not raw:
        return "", "'{:s}' has no media file.".format(strip.name)
    try:
        import bpy  # pylint: disable=import-error,import-outside-toplevel
        path = bpy.path.abspath(raw, library=lib)
    except (ImportError, AttributeError, TypeError):
        path = raw
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        return "", "The media file of '{:s}' is missing: {:s}".format(strip.name, path)
    return path, ""


def strip_timing(scene: Any, strip: Any) -> dict[str, float]:
    """Where the media sits on the timeline (5.2 names, older ones as a fallback)."""
    def first(*names: str) -> float:
        for n in names:
            if hasattr(strip, n):
                return float(getattr(strip, n))
        return 0.0
    render = scene.render
    return {"content_start": first("content_start", "frame_start"),
            "left": first("left_handle", "frame_final_start"),
            "right": first("right_handle", "frame_final_end"),
            "fps": float(render.fps) / float(render.fps_base or 1.0)}


WAV_RATE = 16000            # what whisper takes: 16 kHz mono 16-bit PCM
PIECE_S = 300.0             # read 5 minutes at a time: memory doesn't grow with the length of the file
_RESAMPLE_QUALITY = 2       # aud's resampler: 0 fastest .. 3 best


def wav_pieces(frames: int, rate: float, piece_s: float = PIECE_S,
               out_rate: int = WAV_RATE) -> list[tuple[float, float, int]]:
    """``(start s, end s, frames out)`` pieces that cover a sound of *frames* at *rate*.

    The frames out of a piece are its rounded end less its rounded start, so the pieces add
    up to the whole sound exactly and every word keeps its time in the media file.  The last
    piece runs to the end of the sound, whatever its length (``end`` -1.0, frames out -1): a
    length Blender only estimated can't cut the end off.  An unknown length is one piece."""
    if frames <= 0 or rate <= 0 or piece_s <= 0:
        return [(0.0, -1.0, -1)]
    total = frames / float(rate)
    out, start = [], 0.0
    while total - start > piece_s:
        end = start + piece_s
        out.append((start, end, int(round(end * out_rate)) - int(round(start * out_rate))))
        start = end
    out.append((start, -1.0, -1))
    return out


def sound_format(media: str, specs: Any = None) -> str:
    """What the media is, for a message: ``".wav, 44.1 kHz stereo"`` (the extension alone when
    Blender couldn't read its specs)."""
    ext = os.path.splitext(media)[1].lower() or "no extension"
    try:
        rate, channels = float(specs[0]), int(specs[1])
    except (TypeError, ValueError, IndexError):
        return ext
    names = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}
    return "{:s}, {:g} kHz {:s}".format(ext, rate / 1000.0, names.get(channels, "{:d} channels".format(channels)))


def _pcm16(samples: Any, frames: int) -> bytes:
    """*samples* (aud's ``data()``: a float numpy array, one column) as 16-bit PCM, cut or padded
    with silence to *frames* (all of them when *frames* is negative)."""
    import numpy as np  # pylint: disable=import-error,import-outside-toplevel
    mono = np.asarray(samples, dtype=np.float32).reshape(-1)
    if frames >= 0:
        mono = mono[:frames] if len(mono) >= frames else np.concatenate(
            (mono, np.zeros(frames - len(mono), np.float32)))
    mono = np.clip(mono, -1.0, 1.0)
    mono *= 32767.0
    return mono.astype("<i2").tobytes()


def _write_wav(media: str, out: str, stop: Any = None) -> None:
    """The whole media file as a 16 kHz mono 16-bit WAV, for whisper (#520).

    Blender's audaspace reads the file (no ffmpeg of our own) and converts it in the reader
    (``rechannel`` and ``resample``); Python's ``wave`` writes it.  Not ``Sound.write``: its
    FFmpeg writer fails ("filling the audio frame failed") whenever a read comes back empty at
    the end of the sound, which depends on the length, the rate and the codec -- most 44.1 kHz
    files, FLAC / MP3 / Ogg / AAC, and some MP4s -- on 5.2 to 5.4 alike.

    *stop* (a ``threading.Event``, #522): set, the next piece isn't read (``_Stopped``), so a
    long file stops within one piece; the caller removes the partial WAV."""
    import wave
    import aud  # pylint: disable=import-error,import-outside-toplevel
    name = os.path.basename(media)
    specs = None
    try:
        src = aud.Sound.file(media)
        specs = src.specs
        pieces = wav_pieces(int(getattr(src, "length", 0) or 0), float(specs[0]))
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(WAV_RATE)
            for start, end, frames in pieces:
                if stop is not None and stop.is_set():
                    raise _Stopped()
                part = src if start <= 0.0 and end < 0.0 else src.limit(start, end)
                wav.writeframes(_pcm16(part.rechannel(aud.CHANNELS_MONO).resample(WAV_RATE, _RESAMPLE_QUALITY).data(),
                                       frames))
    except _Stopped:
        raise
    except Exception as ex:  # pylint: disable=broad-exception-caught
        why = str(ex).strip().rstrip(".") or type(ex).__name__
        raise RuntimeError("Blender could not read the sound of {:s} ({:s}): {:s}. It may have no audio track; "
                           "a WAV, FLAC, MP3 or the sound of an MP4 work.".format(
                               name, sound_format(media, specs), why)) from None
    if not os.path.isfile(out) or os.path.getsize(out) <= 44:
        raise RuntimeError("{:s} ({:s}) has no sound.".format(name, sound_format(media, specs)))


# -- Results ------------------------------------------------------------------------------

def shape_result(doc: dict[str, Any], timing: dict[str, float], strip_name: str, limit: int = DEFAULT_LIMIT,
                 offset: int = 0, cached: bool = False, path: str = "", seconds: float = 0.0) -> dict[str, Any]:
    """The tool's answer: a page of the words the strip shows, with timeline frames."""
    all_words = transcripts.words(doc)
    shown = transcripts.strip_words(all_words, timing["content_start"], timing["fps"], timing["left"], timing["right"])
    try:
        limit = max(0, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    page = shown[offset:offset + limit] if limit else []
    engine = doc.get("engine") or {}
    out: dict[str, Any] = {
        "status": "ok", "strip": strip_name, "cached": bool(cached),
        "language": doc.get("language", ""), "model": engine.get("model", ""), "engine": engine.get("name", ""),
        "timing": engine.get("timing", ""), "duration_s": (doc.get("media") or {}).get("duration_s"),
        "fps": round(timing["fps"], 3), "word_count": len(shown), "words_in_media": len(all_words),
        "offset": offset,
        # One short row per word (token economy: ~25 characters instead of ~90).
        "columns": list(COLUMNS),
        "words": [[w["w"], w["start"], w["end"], int(round(w["frame_start"])), int(round(w["frame_end"]))]
                  for w in page],
        "transcript_path": path, "seconds": round(float(seconds), 2),
    }
    more = bool(limit) and offset + len(page) < len(shown)
    if more:
        out["next_offset"] = offset + len(page)
    if not limit:
        out["text"] = " ".join(w["w"] for w in shown)[:_TEXT_MAX]
    how = "from the cache" if cached else "transcribed in {:.1f} s".format(seconds)
    out["message"] = ("{:d} words in '{:s}' ({:s}). Each word is [w, start, end, frame_start, frame_end]: "
                      "seconds in the media file, frames on the timeline.{:s}").format(
        len(shown), strip_name, how,
        " Call again with offset={:d} for the next words (limit=0 gives the plain text).".format(
            out["next_offset"]) if more else "")
    return out


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return dict({"status": "error", "message": message}, **extra)


# -- The job ------------------------------------------------------------------------------

class _Job:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.doc: dict[str, Any] | None = None
        self.path = ""
        self.error = ""
        self.seconds = 0.0
        self.t0 = time.monotonic()
        self.stopped = threading.Event()        # stop_jobs (#516): nobody waits for it any more
        self.local = False                      # on whisper-server (not Remote)
        self.waiters: list[_Waiter] = []        # its callers (#522), under _jobs_lock


class _Waiter:
    """One call waiting for a job (#522): who made it, and whether its Stop came."""

    __slots__ = ("owner", "stopped")

    def __init__(self, owner: str) -> None:
        self.owner = str(owner or "")
        self.stopped = False


class _Stopped(Exception):
    pass


def _note(text: str) -> None:
    try:
        from . import long_jobs
        long_jobs.note_progress(JOB_KIND, text)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _work(job: _Job, media: str, key: str, size: int, p: dict[str, Any], settings: dict[str, Any],
          blend_path: str, prompt: str) -> None:
    # Per job: one that was stopped may still be writing its WAV while the next one starts (#516).
    wav = os.path.join(tempfile.gettempdir(), "bfa_coworker_speech", "{:s}.{:x}.16k.wav".format(key, id(job)))
    job.local = p["route"] != "remote"
    stops = None
    try:
        os.makedirs(os.path.dirname(wav), exist_ok=True)
        _note("Reading the sound of {:s}".format(os.path.basename(media)))
        _write_wav(media, wav, stop=job.stopped)
        if job.stopped.is_set():
            raise _Stopped()
        if p["route"] == "remote":
            _note("Sending the audio to the Remote provider")
            resp = whisper_manager.transcribe_remote(wav, settings["remote_url"], settings["remote_key"], p["model"],
                                                     p["language"], prompt)
            doc = transcripts.from_openai(resp, media, key, size, p["model"], language=p["language"])
        else:
            with _start_lock:
                if job.stopped.is_set():
                    raise _Stopped()
                problem = whisper_manager.start_server(p["model"], p["build"], int(settings["port"]))
            if problem:
                raise RuntimeError(problem)
            stops = whisper_manager.stop_count()
            if not whisper_manager.is_ready():
                _note(whisper_manager.get_state().status_text or "Starting whisper-server")
                if not whisper_manager.wait_ready(timeout=300.0):
                    raise RuntimeError(whisper_manager.get_state().error or "whisper-server did not start")
            _note("Transcribing with {:s} ({:s})".format(p["model"], whisper_manager.BUILD_LABELS.get(p["build"], "")))
            vj = whisper_manager.transcribe_wav(wav, p["language"], prompt)
            info = whisper_manager.build_info(p["build"])
            doc = transcripts.from_verbose_json(vj, media, key, size, p["model"],
                                                build=whisper_manager.BUILD_LABELS.get(p["build"], p["build"]),
                                                version=str(info.get("tag", "")), language=p["language"])
        if job.stopped.is_set():
            raise _Stopped()
        lang = p["language"] if p["language"] != "auto" else (doc.get("language") or "auto")
        path = transcripts.cache_path(key, p["cache_model"], lang, blend_path, whisper_manager.data_dir())
        transcripts.write(path, doc)
        job.doc, job.path = doc, str(path)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        job.error = str(ex) or type(ex).__name__
        # #499: the server was stopped while this job used it (Stop in the Speech tab): say so
        # plainly, not with the socket's or the start's own error.
        if isinstance(ex, whisper_manager.Stopped) or (stops is not None and whisper_manager.stop_count() != stops):
            job.error = whisper_manager.STOPPED
        # #516 / #522: stop_jobs stopped this very job (nobody waits for it any more).
        if job.stopped.is_set():
            job.error = STOPPED
    finally:
        job.seconds = time.monotonic() - job.t0
        try:
            os.remove(wav)
        except OSError:
            pass
        job.done.set()


def _prefs() -> Any:
    try:
        import bpy  # pylint: disable=import-error,import-outside-toplevel
        addon = bpy.context.preferences.addons.get(__package__)
        return addon.preferences if addon is not None else None
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def transcribe_strip(strip: str, language: str = "", limit: int = DEFAULT_LIMIT, offset: int = 0,
                     prompt: str = "", model: str = "",
                     owner: str = "") -> dict[str, Any] | Callable[[], dict[str, Any] | None]:
    """The tool (main thread): a result dict, or a checker while the transcription runs.

    *owner*: who waits for it (:func:`chat_owner`, :data:`CAPTIONS`), so a Stop ends only its
    own callers' transcriptions (#522)."""
    import bpy  # pylint: disable=import-error,import-outside-toplevel
    scene, st, problem = _find_strip(str(strip or "").strip())
    if problem:
        return _error(problem)
    media, problem = strip_media(st)
    if problem:
        return _error(problem)
    timing = strip_timing(scene, st)
    try:
        key, size = transcripts.media_key(media)
    except OSError as ex:
        return _error("Could not read {:s}: {:s}".format(media, str(ex)))
    settings = settings_from_prefs(_prefs())
    p = plan(settings, model=str(model or ""), language=str(language or ""))
    blend_path = str(bpy.data.filepath or "")
    fallback = whisper_manager.data_dir()
    t0 = time.monotonic()
    found = transcripts.find_cached(key, p["cache_model"], p["language"], blend_path, fallback)
    if found is None and blend_path:
        found = transcripts.find_cached(key, p["cache_model"], p["language"], "", fallback)
    if found is not None:
        try:
            doc = transcripts.read(found)
            return shape_result(doc, timing, st.name, limit, offset, cached=True, path=str(found),
                                seconds=time.monotonic() - t0)
        except (OSError, ValueError):
            pass        # a broken file: transcribe again
    problem = setup_problem(p, settings)
    if problem:
        return _error(problem)
    words_prompt = str(prompt or settings.get("prompt") or "")
    job_key = (key, p["cache_model"], p["language"])
    with _jobs_lock:
        job = _jobs.get(job_key)
        if job is None or job.done.is_set():
            job = _Job()
            job.local = p["route"] != "remote"      # known before its thread runs: stop_jobs reads it
            _jobs[job_key] = job
            threading.Thread(target=_work, args=(job, media, key, size, p, settings, blend_path, words_prompt),
                             daemon=True, name="bfacw-transcribe").start()
        # A second call for the same file (another chat, Auto Captions) waits for the same job.
        waiter = _Waiter(owner)
        job.waiters.append(waiter)
    name = st.name

    def check_is_finished() -> dict[str, Any] | None:
        if waiter.stopped:
            # #522: this caller's Stop (its chat's, or Auto Captions' Esc): answer now.  The job
            # ended with it, or goes on for another caller still waiting for the same file.
            return {"status": "cancelled", "strip": name, "message": whisper_manager.STOPPED}
        if not job.done.is_set():
            return None
        with _jobs_lock:
            if _jobs.get(job_key) is job:
                _jobs.pop(job_key, None)
        if job.stopped.is_set():
            return {"status": "cancelled", "message": "Transcribing '{:s}': {:s}".format(name, STOPPED)}
        if job.error == whisper_manager.STOPPED:        # Stop pressed: no socket error text (#499)
            return _error(whisper_manager.STOPPED)
        if job.error or job.doc is None:
            return _error("Transcribing '{:s}' failed: {:s}".format(name, job.error or "no result"))
        return shape_result(job.doc, timing, name, limit, offset, cached=False, path=job.path, seconds=job.seconds)

    check_is_finished.job_kind = JOB_KIND  # type: ignore[attr-defined]
    check_is_finished.job_label = "Transcribe {:s}".format(name)  # type: ignore[attr-defined]
    check_is_finished.job_on_finish = None  # type: ignore[attr-defined]
    if bpy.app.background:
        job.done.wait(timeout=3600.0)
        return check_is_finished() or _error("Transcribing '{:s}' took longer than an hour.".format(name))
    return check_is_finished


def _matches(owner: str, which: str | None) -> bool:
    if which is None:
        return True
    if which == ANY_CHAT:
        return owner.startswith("chat:")
    return owner == which


def stop_jobs(owner: str | None = None) -> int:
    """Stop the transcriptions still running for *owner* (#516, #522); how many it touched.

    *owner*: :func:`chat_owner` (Stop in that chat), :data:`ANY_CHAT` (Stop All Chats),
    :data:`CAPTIONS` (Auto Captions' Esc) or ``None`` (every one).  Its callers answer
    ``cancelled`` ("Transcription stopped.") at once.  A job nobody else waits for ends too: it
    writes no transcript and its WAV goes, and the next call starts afresh.  One another caller
    still waits for (the same file) goes on for that caller.

    whisper-server can't drop a request it is working on, so this window's server is stopped
    when a stopped job ran on it -- also one kept running from before (the next transcription
    starts it again) -- but never while another job still needs it: then the stopped job's
    request runs out on the server, its result is thrown away and nothing is saved.

    Locks: ``_jobs_lock`` is let go before ``_start_lock`` (then whisper_manager's) is taken."""
    with _jobs_lock:
        touched: list[_Job] = []
        ended: list[_Job] = []
        for key, job in list(_jobs.items()):
            if job.done.is_set() or job.stopped.is_set():
                continue
            mine = [w for w in job.waiters if not w.stopped and _matches(w.owner, owner)]
            if not mine and owner is not None:
                continue
            for w in mine:
                w.stopped = True
            touched.append(job)
            if all(w.stopped for w in job.waiters):
                ended.append(job)
                del _jobs[key]      # the next call starts afresh, not on the stopped job
        # Another job that still needs the server: one still in _jobs, not over, on whisper-server.
        needed = any(j.local and not j.done.is_set() and not j.stopped.is_set()
                     for j in _jobs.values())
    if ended:
        with _start_lock:
            for job in ended:
                job.stopped.set()
            if any(job.local for job in ended) and not needed:
                whisper_manager.stop_server()
    return len(touched)


def stop_chat_jobs(tag: str | None = None) -> int:
    """Stop in a chat (*tag*, "" = the default chat) or, with ``None``, Stop All Chats (#522):
    the transcriptions that chat's (every chat's) turns started; Auto Captions' carry on."""
    return stop_jobs(ANY_CHAT if tag is None else chat_owner(tag))


def was_stopped(result: Any) -> bool:
    """A transcription's answer says it was stopped (a Stop, or whisper-server stopped under it)."""
    return isinstance(result, dict) and (result.get("status") == "cancelled"
                                         or result.get("message") == whisper_manager.STOPPED)
