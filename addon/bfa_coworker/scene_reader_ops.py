# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Scene reader, the Blender side (Highlights H1, #611): walks the Sequencer's scene, hands the speaking
strips and their transcripts to :mod:`scene_reader` and answers ``read_edit_sentences``.  Changes nothing.

Which strips speak: a media file's sound strips, else its movie strips (as Edit by Transcript counts
them).  Muted strips and strips on a muted channel, strips changed in speed (a Speed effect on them, or
retiming keys) and strips inside metas are handed over with their reason, so the report can say how much
of the edit was left out.
"""

from __future__ import annotations

__all__ = (
    "collect_sources",
    "context_tokens",
    "read",
    "tool_read_edit_sentences",
)

import os
from typing import Any

from . import scene_reader as sr

MAX_ROWS = 400          # sentences one call returns
DEFAULT_ROWS = 150


def _ops() -> Any:
    from . import transcript_edit_ops
    return transcript_edit_ops


def _channel_muted(ed: Any, strip: Any) -> bool:
    try:
        return bool(ed.channels[int(strip.channel)].mute)
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _speed_inputs(ed: Any) -> set[str]:
    """Names of the strips a Speed effect works on."""
    out: set[str] = set()
    for s in ed.strips_all:
        if s.type == 'SPEED':
            for attr in ("input_1", "input_2"):
                inp = getattr(s, attr, None)
                if inp is not None:
                    out.add(inp.name)
    return out


def _retimed(strip: Any, speed_inputs: set[str]) -> bool:
    if strip.name in speed_inputs:
        return True
    try:
        keys = strip.retiming_keys
        return len(keys) > 2
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _inner_speakers(strip: Any) -> bool:
    """A meta strip with sound or movie strips somewhere inside."""
    try:
        return any(s.type in {'SOUND', 'MOVIE'} or (s.type == 'META' and _inner_speakers(s)) for s in strip.strips)
    except Exception:  # pylint: disable=broad-exception-caught
        return True


def collect_sources(scene: Any) -> list[dict[str, Any]]:
    """The speaking strips of *scene* as :func:`scene_reader.read_scene` takes them."""
    ops = _ops()
    from . import captions, transcripts
    ed = scene.sequence_editor
    if ed is None:
        return []
    speed = _speed_inputs(ed)
    by_path: dict[str, dict[str, list]] = {}
    metas: list[Any] = []
    for s in ed.strips:
        if s.type == 'META':
            if _inner_speakers(s):
                metas.append(s)
        elif s.type in {'SOUND', 'MOVIE'}:
            path = ops.strip_path(s)
            if path:
                by_path.setdefault(ops._norm(path), {"SOUND": [], "MOVIE": [], "path": path})[s.type].append(s)
    sources: list[dict[str, Any]] = []
    for norm_path, group in sorted(by_path.items()):
        strips = group["SOUND"] or group["MOVIE"]
        path = group["path"]
        transcript = ops.find_transcript(path)
        words = None
        if transcript:
            try:
                words = ops.load_words(transcript)
            except (OSError, ValueError):
                words = None
        try:
            key = transcripts.media_key(path)[0]
        except OSError:
            key = norm_path
        pieces = []
        for s in strips:
            piece = ops._piece(s)
            if s.mute or _channel_muted(ed, s):
                piece["skip"] = "muted"
            elif _retimed(s, speed):
                piece["skip"] = "retimed"
            pieces.append(piece)
        sources.append({"key": key, "path": path, "name": os.path.basename(path), "words": words,
                        "pieces": pieces})
    for m in metas:
        piece = {"name": m.name, "left": int(m.left_handle), "right": int(m.right_handle), "content_start": 0.0,
                 "offset": 0.0, "channel": int(m.channel), "skip": "muted" if (m.mute or _channel_muted(ed, m)) else "meta"}
        sources.append({"key": "meta:" + m.name, "path": "", "name": m.name, "words": None, "pieces": [piece]})
    speaking = [s for s in sources if s["words"]]
    if len(speaking) > 1:           # one file one speaker, as the captions name them (#542)
        for src in speaking:
            for p in src["pieces"]:
                p["speaker"] = captions.speaker_name(src["name"])
    return sources


def context_tokens() -> int | None:
    """The context of the Local server (its real value when it runs), None when unknown."""
    try:
        from . import llm_manager
        return llm_manager.get_runtime_ctx() or int(llm_manager._config.local_ctx_size)  # pylint: disable=protected-access
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def read(scene: Any, context: int | None = None) -> dict[str, Any]:
    """``{"sentences", "report", "windows"}`` for *scene* (windows sized for *context* tokens)."""
    ops = _ops()
    out = sr.read_scene(collect_sources(scene), ops._fps(scene), scene.frame_start)  # pylint: disable=protected-access
    out["windows"] = sr.windows(out["sentences"], context)
    return out


def tool_read_edit_sentences(first: int = 1, count: int = DEFAULT_ROWS) -> dict[str, Any]:
    """``read_edit_sentences`` (MCP): the whole edit's speech as numbered sentences, as it plays now
    (every strip, every media file, merged by time), *count* of them from number *first*.  Changes nothing."""
    ops = _ops()
    try:
        scene = ops._tool_scene()  # pylint: disable=protected-access
        ed = scene.sequence_editor
        if ed is None:
            return {"status": "error", "message": "The Sequencer's scene “{:s}” has no strips".format(scene.name)}
        if len(ed.meta_stack):
            return {"status": "error", "message": "Leave the meta strip first (Tab): the edit is read from its top level"}
        got = read(scene, context_tokens())
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return {"status": "error", "message": "Couldn't read the edit: {!s}".format(ex)}
    sentences, report = got["sentences"], got["report"]
    total = len(sentences)
    first = max(1, int(first or 1))
    count = max(1, min(MAX_ROWS, int(count or DEFAULT_ROWS)))
    page = sentences[first - 1:first - 1 + count]
    last = page[-1]["n"] if page else first - 1
    msg = "{:d} sentences ({:.0f} s of speech) in “{:s}”".format(total, report["speech_seconds"], scene.name)
    return {
        "status": "ok" if total else "error", "scene": scene.name, "total": total, "first": first, "last": last,
        "next_first": last + 1 if last < total else None, "version": sr.version(sentences),
        "text": sr.format_sentences(page), "windows": got["windows"], "report": {
            k: v for k, v in report.items() if k not in {"sentences", "words"}},
        "message": msg if total else "No speech to read: " + " ".join(report["messages"]),
    }
