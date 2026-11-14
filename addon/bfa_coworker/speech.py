# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Transcribe a Sequencer strip (Tier 4, task "Video 1", #375): the ``transcribe_strip`` tool.

On the main thread (the bridge runs tool-code there): find the strip and its media file,
key it (:func:`transcripts.media_key`) and answer from the cache when the file was
transcribed before.  Otherwise a worker thread writes a 16 kHz mono WAV with Blender's
``aud`` (no ffmpeg; the whole file, since the cache is per media file), starts
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
    "COLUMNS",
    "JOB_KIND",
    "plan",
    "setup_problem",
    "settings_from_prefs",
    "shape_result",
    "strip_media",
    "strip_timing",
    "transcribe_strip",
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


def _write_wav(media: str, out: str) -> None:
    """16 kHz mono S16 WAV with Blender's audaspace (``write`` resamples itself; no ffmpeg)."""
    import aud  # pylint: disable=import-error,import-outside-toplevel
    try:
        aud.Sound.file(media).write(out, 16000, aud.CHANNELS_MONO, aud.FORMAT_S16, aud.CONTAINER_WAV, aud.CODEC_PCM)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        raise RuntimeError("Blender could not read the sound of {:s} ({:s}); it may have no audio track.".format(
            os.path.basename(media), str(ex))) from None
    if not os.path.isfile(out) or os.path.getsize(out) <= 44:
        raise RuntimeError("{:s} has no sound.".format(os.path.basename(media)))


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


def _note(text: str) -> None:
    try:
        from . import long_jobs
        long_jobs.note_progress(JOB_KIND, text)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _work(job: _Job, media: str, key: str, size: int, p: dict[str, Any], settings: dict[str, Any],
          blend_path: str, prompt: str) -> None:
    wav = os.path.join(tempfile.gettempdir(), "bfa_coworker_speech", "{:s}.16k.wav".format(key))
    try:
        os.makedirs(os.path.dirname(wav), exist_ok=True)
        _note("Reading the sound of {:s}".format(os.path.basename(media)))
        _write_wav(media, wav)
        if p["route"] == "remote":
            _note("Sending the audio to the Remote provider")
            resp = whisper_manager.transcribe_remote(wav, settings["remote_url"], settings["remote_key"], p["model"],
                                                     p["language"], prompt)
            doc = transcripts.from_openai(resp, media, key, size, p["model"], language=p["language"])
        else:
            problem = whisper_manager.start_server(p["model"], p["build"], int(settings["port"]))
            if problem:
                raise RuntimeError(problem)
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
        lang = p["language"] if p["language"] != "auto" else (doc.get("language") or "auto")
        path = transcripts.cache_path(key, p["cache_model"], lang, blend_path, whisper_manager.data_dir())
        transcripts.write(path, doc)
        job.doc, job.path = doc, str(path)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        job.error = str(ex) or type(ex).__name__
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
                     prompt: str = "", model: str = "") -> dict[str, Any] | Callable[[], dict[str, Any] | None]:
    """The tool (main thread): a result dict, or a checker while the transcription runs."""
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
            _jobs[job_key] = job
            threading.Thread(target=_work, args=(job, media, key, size, p, settings, blend_path, words_prompt),
                             daemon=True, name="bfacw-transcribe").start()
    name = st.name

    def check_is_finished() -> dict[str, Any] | None:
        if not job.done.is_set():
            return None
        with _jobs_lock:
            if _jobs.get(job_key) is job:
                _jobs.pop(job_key, None)
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
