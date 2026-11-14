# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Transcripts: the word list of a media file, cached as JSON (Tier 4, task "Video 1", #375).

The format is ``bfa_coworker.transcript/1``, frozen by the Video 0 spike (#374, plan 4v
section 6.4).  One file per media file, model and language::

    <blend dir>/coworker_transcripts/<media key>.<model>.<language>.json

(an unsaved .blend uses the add-on's cache folder).  The media key is the first 16 hex of
sha1(size + first MiB + last MiB): fast on multi-GB movies, the same after a rename or move.

Times are **seconds in the media file**, not the timeline: :func:`media_to_frame` turns one
into a timeline frame for a strip.  Captions (#376), Remove Silences and Suggest read the
same file, so nothing is transcribed twice.  They import this module lazily and use
:func:`load_for_media`, :func:`words` and :data:`SCHEMA`.

Words come from whisper-server's ``verbose_json`` (:func:`from_verbose_json`, the frozen
route: ``max_len=1`` + ``split_on_word`` on a server started with ``-nfa --dtw <preset>``) or
from an OpenAI-compatible ``/v1/audio/transcriptions`` answer (:func:`from_openai`).

Deliberately free of ``bpy`` (only :func:`load_for_media` asks Blender where the .blend is,
when it is not told).
"""

from __future__ import annotations

__all__ = (
    "SCHEMA",
    "DTW_OFFSET_S",
    "MAX_WORD_S",
    "cache_dir",
    "cache_path",
    "find_cached",
    "from_openai",
    "from_verbose_json",
    "language_code",
    "load_for_media",
    "media_key",
    "media_to_frame",
    "read",
    "strip_words",
    "validate",
    "verbose_json_words",
    "words",
    "write",
)

import datetime
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

SCHEMA = "bfa_coworker.transcript/1"
CACHE_FOLDER = "coworker_transcripts"

# DTW onsets lag the speech by ~0.24 s (base, small and large-v3-turbo alike, spike #374).
DTW_OFFSET_S = -0.24
# A word's end is the next word's start, at most this long after its own start.
MAX_WORD_S = 1.0
_MIB = 1 << 20
_KEY_RE = re.compile(r"^[0-9a-f]{16}$")
_NAME_PART_RE = re.compile(r"[^A-Za-z0-9_.-]+")

# whisper answers with the language's English name; the cache and the format use the code.
_LANGUAGE_NAMES = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de", "italian": "it",
    "portuguese": "pt", "dutch": "nl", "russian": "ru", "polish": "pl", "turkish": "tr",
    "ukrainian": "uk", "japanese": "ja", "chinese": "zh", "korean": "ko", "arabic": "ar",
    "hindi": "hi", "swedish": "sv", "danish": "da", "norwegian": "no", "finnish": "fi",
    "czech": "cs", "greek": "el", "hebrew": "he", "hungarian": "hu", "indonesian": "id",
    "romanian": "ro", "vietnamese": "vi", "thai": "th", "catalan": "ca", "persian": "fa",
}


# -- Media key and cache paths ------------------------------------------------------------

def media_key(path: str | os.PathLike) -> tuple[str, int]:
    """``(key, size)``: sha1 of the size, the first MiB and the last MiB, 16 hex chars.

    The same as the spike's reference converter (a file of 2 MiB or less is hashed once).
    """
    size = os.path.getsize(path)
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(_MIB))
        if size > 2 * _MIB:
            fh.seek(-_MIB, 2)
            h.update(fh.read(_MIB))
    return h.hexdigest()[:16], size


def _name_part(text: str) -> str:
    return _NAME_PART_RE.sub("-", str(text or "").strip()) or "auto"


def cache_dir(blend_path: str = "", fallback_dir: str | os.PathLike = "") -> Path:
    """``<blend dir>/coworker_transcripts``; for an unsaved .blend ``<fallback_dir>/coworker_transcripts``."""
    if blend_path:
        return Path(blend_path).resolve().parent / CACHE_FOLDER
    base = Path(fallback_dir) if fallback_dir else _default_fallback_dir()
    return base / CACHE_FOLDER


def _default_fallback_dir() -> Path:
    """The add-on's cache folder (the same root whisper_manager keeps its files in)."""
    return Path.home() / ".cache" / "bfa_coworker_whisper"


def cache_path(key: str, model: str, language: str, blend_path: str = "",
               fallback_dir: str | os.PathLike = "") -> Path:
    """``<cache dir>/<key>.<model>.<language>.json``."""
    return cache_dir(blend_path, fallback_dir) / "{:s}.{:s}.{:s}.json".format(
        key, _name_part(model), _name_part(language))


def find_cached(key: str, model: str | None = None, language: str | None = None,
                blend_path: str = "", fallback_dir: str | os.PathLike = "") -> Path | None:
    """The newest valid cache file for *key* (any model / language when ``None`` or "auto")."""
    folder = cache_dir(blend_path, fallback_dir)
    if not folder.is_dir():
        return None
    want_model = _name_part(model) if model and model != "auto" else None
    want_lang = _name_part(language) if language and language != "auto" else None
    found = []
    for path in folder.glob("{:s}.*.json".format(key)):
        parts = path.name[len(key) + 1:-len(".json")].rsplit(".", 1)
        if len(parts) != 2:
            continue
        p_model, p_lang = parts
        if want_model and p_model != want_model:
            continue
        if want_lang and p_lang != want_lang:
            continue
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _mtime, path in sorted(found, reverse=True):
        try:
            if not validate(read(path)):
                return path
        except (OSError, ValueError):
            continue
    return None


# -- The format ---------------------------------------------------------------------------

def validate(doc: Any) -> list[str]:
    """What is wrong with *doc* as a ``bfa_coworker.transcript/1``; ``[]`` when it is valid."""
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["not an object"]
    for name in ("schema", "media", "engine", "language", "words"):
        if name not in doc:
            errs.append("missing {:s}".format(name))
    if errs:
        return errs
    if doc["schema"] != SCHEMA:
        errs.append("schema is {!r}, not {:s}".format(doc["schema"], SCHEMA))
    media = doc["media"]
    if not isinstance(media, dict):
        errs.append("media is not an object")
    else:
        for name in ("path", "key", "duration_s"):
            if name not in media:
                errs.append("media.{:s} missing".format(name))
        if not isinstance(media.get("path", ""), str):
            errs.append("media.path is not a string")
        if "key" in media and not (isinstance(media["key"], str) and _KEY_RE.match(media["key"])):
            errs.append("media.key is not 16 hex characters")
        if "size" in media and not (isinstance(media["size"], int) and not isinstance(media["size"], bool)):
            errs.append("media.size is not an integer")
        if "duration_s" in media and not _is_number(media["duration_s"]):
            errs.append("media.duration_s is not a number")
    engine = doc["engine"]
    if not isinstance(engine, dict):
        errs.append("engine is not an object")
    else:
        for name in ("name", "model"):
            if not isinstance(engine.get(name), str):
                errs.append("engine.{:s} missing".format(name))
        if "timing" in engine and engine["timing"] not in ("dtw", "token", "remote"):
            errs.append("engine.timing is {!r}".format(engine["timing"]))
    if not isinstance(doc["language"], str):
        errs.append("language is not a string")
    if not isinstance(doc["words"], list):
        errs.append("words is not a list")
    else:
        for i, w in enumerate(doc["words"]):
            if not isinstance(w, dict) or not isinstance(w.get("w"), str) \
                    or not _is_number(w.get("start")) or not _is_number(w.get("end")):
                errs.append("words[{:d}] needs w, start and end".format(i))
                break
            if "p" in w and not _is_number(w["p"]):
                errs.append("words[{:d}].p is not a number".format(i))
                break
    return errs


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def read(path: str | os.PathLike) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write(path: str | os.PathLike, doc: dict[str, Any]) -> Path:
    """Write *doc* (validated) atomically; returns the path."""
    errs = validate(doc)
    if errs:
        raise ValueError("not a {:s}: {:s}".format(SCHEMA, "; ".join(errs)))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def words(transcript: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The word list of *transcript* (``[]`` for ``None`` or a broken one)."""
    if not isinstance(transcript, dict) or not isinstance(transcript.get("words"), list):
        return []
    return [w for w in transcript["words"] if isinstance(w, dict) and "w" in w]


def language_code(name: str) -> str:
    """``"english"`` -> ``"en"``; a code stays a code."""
    low = str(name or "").strip().lower()
    return _LANGUAGE_NAMES.get(low, low)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _media_block(media_path: str, key: str, size: int, duration: float) -> dict[str, Any]:
    return {"path": str(media_path).replace("\\", "/"), "key": key, "size": int(size),
            "duration_s": round(float(duration or 0.0), 3)}


# -- whisper-server verbose_json -> words -------------------------------------------------

def _is_special(token_text: str) -> bool:
    t = str(token_text or "").strip()
    return t.startswith("[_") or t.startswith("<|")


def _has_letters(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def _finish(raw: list[list[Any]], duration: float) -> list[dict[str, Any]]:
    """Monotonic starts; end = the next start, at most MAX_WORD_S after the start."""
    for i in range(1, len(raw)):
        raw[i][0] = max(raw[i][0], raw[i - 1][0] + 0.01)
    out = []
    for i, (start, _end, text, p) in enumerate(raw):
        nxt = raw[i + 1][0] if i + 1 < len(raw) else (duration or start + MAX_WORD_S)
        word = {"w": text, "start": round(start, 3), "end": round(min(nxt, start + MAX_WORD_S), 3)}
        if p is not None:
            word["p"] = round(p, 3)
        out.append(word)
    return out


def verbose_json_words(vj: dict[str, Any], dtw_offset_s: float = DTW_OFFSET_S) -> tuple[list[dict[str, Any]], str]:
    """``(words, timing)`` from a whisper-server ``verbose_json``.

    With ``max_len=1`` + ``split_on_word`` every non-empty segment is one word, and its
    first token carries the DTW onset (``t_dtw``, 10 ms units; -1 without DTW).  A segment
    that is only punctuation joins the word before it.  *timing* is "dtw" when every word
    had a DTW onset, else "token".
    """
    raw: list[list[Any]] = []
    all_dtw = True
    for seg in vj.get("segments") or []:
        text = str(seg.get("text") or "").strip()
        toks = [t for t in (seg.get("words") or []) if isinstance(t, dict) and not _is_special(t.get("word"))]
        if not text or not toks:
            continue
        probs = [float(t["probability"]) for t in toks if _is_number(t.get("probability"))]
        p = min(probs) if probs else None
        if raw and not _has_letters(text):
            prev = raw[-1]
            prev[2] += text
            if p is not None:
                prev[3] = p if prev[3] is None else min(prev[3], p)
            continue
        t_dtw = toks[0].get("t_dtw", -1)
        if _is_number(t_dtw) and t_dtw >= 0:
            start = t_dtw / 100.0 + dtw_offset_s
        else:
            all_dtw = False
            start = float(seg.get("start") or 0.0)
        raw.append([max(0.0, start), float(seg.get("end") or start), text, p])
    duration = float(vj.get("duration") or 0.0)
    return _finish(raw, duration), ("dtw" if all_dtw else "token")


def from_verbose_json(vj: dict[str, Any], media_path: str, key: str, size: int, model: str,
                      build: str = "", version: str = "", language: str = "") -> dict[str, Any]:
    """A ``bfa_coworker.transcript/1`` from whisper-server's ``verbose_json`` answer."""
    word_list, timing = verbose_json_words(vj)
    langs = vj.get("language_probabilities") or {}
    lang = max(langs, key=langs.get) if isinstance(langs, dict) and langs else language_code(
        vj.get("language") or language)
    engine: dict[str, Any] = {
        "name": "whisper.cpp", "model": model,
        "route": "whisper-server /inference verbose_json max_len=1 split_on_word -nfa --dtw",
        "timing": timing,
    }
    if version:
        engine["version"] = version
    if build:
        engine["build"] = build
    if timing == "dtw":
        engine["dtw_offset_s"] = DTW_OFFSET_S
    return {"schema": SCHEMA, "media": _media_block(media_path, key, size, vj.get("duration") or 0.0),
            "engine": engine, "language": lang or language_code(language) or "auto",
            "created": _now(), "words": word_list}


# -- OpenAI-compatible /v1/audio/transcriptions -> words ----------------------------------

def from_openai(resp: dict[str, Any], media_path: str, key: str, size: int, model: str,
                provider: str = "", language: str = "") -> dict[str, Any]:
    """A transcript from an OpenAI-style ``verbose_json`` with ``timestamp_granularities[]=word``.

    Its words carry no punctuation; the punctuation of the segment text is not re-attached.
    """
    raw: list[list[Any]] = []
    for w in resp.get("words") or []:
        if not isinstance(w, dict):
            continue
        text = str(w.get("word") or "").strip()
        if not text or not _is_number(w.get("start")):
            continue
        start = float(w["start"])
        end = float(w["end"]) if _is_number(w.get("end")) else start
        raw.append([max(0.0, start), end, text, None])
    for i in range(1, len(raw)):
        raw[i][0] = max(raw[i][0], raw[i - 1][0] + 0.01)
    duration = float(resp.get("duration") or 0.0)
    word_list = []
    for s, e, text, _p in raw:  # the provider's own ends
        word_list.append({"w": text, "start": round(s, 3), "end": round(max(e, s), 3)})
    engine = {"name": "remote", "model": model, "route": "/v1/audio/transcriptions verbose_json word",
              "timing": "remote"}
    if provider:
        engine["build"] = provider
    lang = language_code(resp.get("language") or "") or language_code(language) or "auto"
    return {"schema": SCHEMA, "media": _media_block(media_path, key, size, duration),
            "engine": engine, "language": lang, "created": _now(), "words": word_list}


# -- Media time -> timeline ---------------------------------------------------------------

def media_to_frame(t: float, content_start: float, fps: float) -> float:
    """The timeline frame of media time *t* (s) for a strip whose content starts at
    *content_start* (``strip.content_start``), media at the scene's frame rate *fps*."""
    return float(content_start) + float(t) * float(fps)


def strip_words(word_list: list[dict[str, Any]], content_start: float, fps: float,
                left_handle: float | None = None, right_handle: float | None = None) -> list[dict[str, Any]]:
    """*word_list* with ``frame_start`` / ``frame_end`` on the timeline; with the handles,
    only the words that start where the strip is visible (``left <= frame < right``)."""
    out = []
    for w in word_list:
        f0 = media_to_frame(w["start"], content_start, fps)
        f1 = media_to_frame(w["end"], content_start, fps)
        if left_handle is not None and f0 < left_handle:
            continue
        if right_handle is not None and f0 >= right_handle:
            continue
        out.append(dict(w, frame_start=round(f0, 2), frame_end=round(f1, 2)))
    return out


# -- For other features (#376 captions, Remove Silences, Suggest) -------------------------

def _blend_path() -> str:
    try:
        import bpy  # pylint: disable=import-outside-toplevel,import-error
        return str(bpy.data.filepath or "")
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def load_for_media(path: str, model: str | None = None, language: str | None = None,
                   blend_path: str | None = None, fallback_dir: str | os.PathLike = "") -> dict[str, Any] | None:
    """The cached transcript of the media file *path* (any model / language when ``None``),
    or ``None`` when it was never transcribed (or the file is gone).

    *blend_path* defaults to the open .blend (unsaved: the add-on's cache folder).
    """
    try:
        key, _size = media_key(path)
    except OSError:
        return None
    if blend_path is None:
        blend_path = _blend_path()
    found = find_cached(key, model, language, blend_path or "", fallback_dir)
    if found is None and blend_path:
        # Transcribed before the .blend was saved: the add-on's cache folder has it.
        found = find_cached(key, model, language, "", fallback_dir)
    if found is None:
        return None
    try:
        return read(found)
    except (OSError, ValueError):
        return None
