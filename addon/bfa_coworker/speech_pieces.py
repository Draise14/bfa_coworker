# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Long audio in pieces that can't loop (#540).

One whole-file request to whisper-server carries the text of each 30 s window into the next,
and from about minute 6 of a 12.5 minute file that context looped: a four-word sequence came
back about 250 times.  So the 16 kHz WAV is cut into pieces and each piece is its own request
with no text context (``max_context=0``):

* :func:`wav_envelope` -- the loudness of the WAV, one dB value per 10 ms.
* :func:`plan_pieces` -- pieces of at most :data:`PIECE_MAX_S` (one whisper window, so not
  even a piece carries text from one window to the next), each cut in a pause near the end
  of its window (the latest pause of 0.5 s or more wins; without one, the quietest 150 ms):
  never mid-word.
* :func:`speech_span` -- the part of a piece that is sent: from just before its first loud
  frame to just after its last, so whisper gets no silent tail to "hear" words in.
* :func:`has_speech` -- the no-speech gate: a piece without loud frames (digital silence),
  or loud without the syllable-like dips of speech (a tone, a steady music bed), is not sent
  and gives no words.  Relative to the file's own level, so a quiet speaker still counts.
* :func:`degenerate` -- the repeat check of a piece's words: one short sequence back to back
  many times, or more words than the piece's length allows.  Such a piece is retried once
  with other settings and, if it still loops, kept empty (and said so in the result).
* :func:`shift_verbose_json` / :func:`merge_verbose_json` -- a piece's answer moved back to
  file time and the pieces joined into one ``verbose_json`` for
  :func:`transcripts.from_verbose_json`.
* :func:`refine_words` -- words in silence are dropped, and a word ends where its sound
  ends, not at the next word's start across a pause.

Free of ``bpy``; numpy is used when it is there (Blender has it) and plain Python otherwise.
"""

from __future__ import annotations

__all__ = (
    "HOP_S",
    "PIECE_MAX_S",
    "PIECE_MIN_S",
    "degenerate",
    "envelope_db",
    "has_speech",
    "merge_verbose_json",
    "plan_pieces",
    "quiet_level",
    "refine_words",
    "repeat_run",
    "shift_verbose_json",
    "speech_level",
    "speech_span",
    "syllables",
    "wav_envelope",
    "write_piece",
)

import math
import re
import wave
from array import array
from typing import Any

HOP_S = 0.01                # one loudness value per 10 ms
FLOOR_DB = -100.0           # digital silence
# A piece is one whisper window (30 s) at most: no text carried from one window to the next.
PIECE_MAX_S = 29.5
PIECE_MIN_S = 12.0          # pieces aren't cut shorter (each one costs a whole window to encode)
# A frame this far below the file's speech level is a pause; never above -30 dBFS (a loud
# file), never below -75 dBFS (a very quiet one still has pauses below its noise).
PAUSE_BELOW_DB = 25.0
_PAUSE_MAX_DB = -30.0
_PAUSE_MIN_DB = -75.0
GATE_DB = -60.0             # quieter than this everywhere: no sound to transcribe at all
_GOOD_PAUSE_S = 0.5         # a pause this long is as good a cut as any
_MIN_PAUSE_S = 0.1
_PAD_BEFORE_S = 0.3         # sent before the first loud frame of a piece ...
_PAD_AFTER_S = 0.5          # ... and after its last
# Speech rises and falls with its syllables (3-6 per second); a tone or a steady music bed
# doesn't.  A dip / rise of this many dB counts as one syllable-like peak.
_SYLLABLE_DB = 6.0
_MIN_SYLLABLES = 3
_SMOOTH_FRAMES = 5          # 50 ms
# The repeat check: more words per second than anyone says, or one sequence of n words back to
# back k times with k >= 4 and k * n >= 12 ("very very very" is speech; 12 x "the end" isn't).
MAX_WORDS_PER_S = 6.0
_REPEAT_MIN_TIMES = 4
_REPEAT_MIN_WORDS = 12
_REPEAT_MAX_N = 8
# A word ends at the first pause of this length after it started (at least _WORD_MIN_S long).
_WORD_PAUSE_S = 0.12
_WORD_MIN_S = 0.08
_WORD_TAIL_S = 0.03


# -- Loudness -----------------------------------------------------------------------------

def _db(rms: float) -> float:
    return 20.0 * math.log10(rms) if rms > 1e-5 else FLOOR_DB


def envelope_db(pcm: bytes, hop: int) -> list[float]:
    """The RMS loudness in dBFS of each *hop* samples of 16-bit mono PCM *pcm* (the last
    partial hop too)."""
    if not pcm or hop <= 0:
        return []
    try:
        import numpy as np  # pylint: disable=import-outside-toplevel
    except ImportError:
        np = None
    if np is not None:
        a = np.frombuffer(pcm[:len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float32) / 32768.0
        pad = (-len(a)) % hop
        if pad:
            a = np.concatenate((a, np.zeros(pad, np.float32)))
        rms = np.sqrt(np.mean(a.reshape(-1, hop) ** 2, axis=1))
        db = 20.0 * np.log10(np.maximum(rms, 1e-5))
        db[rms <= 1e-5] = FLOOR_DB
        return [float(v) for v in db]
    samples = array("h")
    samples.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if samples.itemsize == 2 and array("h", [1]).tobytes() != b"\x01\x00":
        samples.byteswap()
    out = []
    for i in range(0, len(samples), hop):
        part = samples[i:i + hop]
        out.append(_db(math.sqrt(sum(s * s for s in part) / float(hop)) / 32768.0))
    return out


def wav_envelope(path: str, hop_s: float = HOP_S, stop: Any = None) -> dict[str, Any] | None:
    """``{"db", "hop_s", "rate", "frames"}`` of a 16-bit mono WAV, read a minute at a time;
    ``None`` when it isn't one (then the whole file is one piece, as before #540).
    *stop* set: ``None`` too (the caller checks it)."""
    try:
        w = wave.open(str(path), "rb")
    except (wave.Error, EOFError, OSError):
        return None
    with w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() <= 0:
            return None
        rate = w.getframerate()
        hop = max(1, int(round(rate * hop_s)))
        db: list[float] = []
        while True:
            if stop is not None and stop.is_set():
                return None
            raw = w.readframes(hop * 6000)
            if not raw:
                break
            db.extend(envelope_db(raw, hop))
        return {"db": db, "hop_s": hop / float(rate), "rate": rate, "frames": w.getnframes()}


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return FLOOR_DB
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def speech_level(db: list[float]) -> float:
    """The file's loud level: the 95th percentile of its frames that aren't digital silence."""
    return _percentile([v for v in db if v > FLOOR_DB + 1.0], 0.95)


def quiet_level(db: list[float]) -> float:
    """Below this a frame is a pause (see :data:`PAUSE_BELOW_DB`)."""
    return max(_PAUSE_MIN_DB, min(_PAUSE_MAX_DB, speech_level(db) - PAUSE_BELOW_DB))


# -- Pieces -------------------------------------------------------------------------------

def _best_cut(db: list[float], lo: int, hi: int, quiet: float, hop_s: float) -> int:
    """The frame to cut at in ``[lo, hi)``: the middle of the best pause (a pause of
    :data:`_GOOD_PAUSE_S` anywhere beats a shorter one; later beats earlier), else the middle
    of the quietest 150 ms."""
    best: tuple[float, int] | None = None
    min_run = max(1, int(round(_MIN_PAUSE_S / hop_s)))
    good = _GOOD_PAUSE_S / hop_s
    span = float(max(1, hi - lo))
    i = lo
    while i < hi:
        if db[i] >= quiet:
            i += 1
            continue
        j = i
        while j < hi and db[j] < quiet:
            j += 1
        if j - i >= min_run:
            score = min(1.0, (j - i) / good) + (i - lo) / span
            if best is None or score >= best[0]:
                best = (score, (i + j) // 2)
        i = j
    if best is not None:
        return best[1]
    width = max(1, int(round(0.15 / hop_s)))
    if hi - lo <= width:
        return (lo + hi) // 2
    run = sum(db[lo:lo + width])
    best_sum, best_at = run, lo
    for k in range(lo + 1, hi - width + 1):
        run += db[k + width - 1] - db[k - 1]
        if run <= best_sum:
            best_sum, best_at = run, k
    return best_at + width // 2


def plan_pieces(db: list[float], hop_s: float = HOP_S, max_s: float = PIECE_MAX_S, min_s: float = PIECE_MIN_S,
                quiet: float | None = None) -> list[tuple[float, float]]:
    """``(start s, end s)`` pieces that cover the whole sound back to back, each at most
    *max_s* long and cut in a pause (see :func:`_best_cut`)."""
    n = len(db)
    total = round(n * hop_s, 3)
    max_f, min_f = max(2, int(max_s / hop_s)), max(1, int(min_s / hop_s))
    if n <= max_f:
        return [(0.0, total)]
    if quiet is None:
        quiet = quiet_level(db)
    out, start = [], 0
    while n - start > max_f:
        cut = _best_cut(db, start + min(min_f, max_f - 1), start + max_f, quiet, hop_s)
        out.append((round(start * hop_s, 3), round(cut * hop_s, 3)))
        start = cut
    out.append((round(start * hop_s, 3), total))
    return out


def _frames(db: list[float], start_s: float, end_s: float, hop_s: float) -> tuple[int, int]:
    a = max(0, int(math.floor(start_s / hop_s)))
    b = min(len(db), int(math.ceil(end_s / hop_s)))
    return a, max(a, b)


def speech_span(db: list[float], start_s: float, end_s: float, hop_s: float = HOP_S,
                quiet: float | None = None) -> tuple[float, float]:
    """The part of the piece ``[start_s, end_s)`` to send: from :data:`_PAD_BEFORE_S` before
    its first loud frame to :data:`_PAD_AFTER_S` after its last (the whole piece when it has
    none)."""
    if quiet is None:
        quiet = quiet_level(db)
    a, b = _frames(db, start_s, end_s, hop_s)
    loud = [i for i in range(a, b) if db[i] >= quiet]
    if not loud:
        return start_s, end_s
    return (round(max(start_s, loud[0] * hop_s - _PAD_BEFORE_S), 3),
            round(min(end_s, (loud[-1] + 1) * hop_s + _PAD_AFTER_S), 3))


def syllables(db: list[float], quiet: float, rise_db: float = _SYLLABLE_DB) -> int:
    """Syllable-like peaks: the loudness rises *rise_db* above the last dip, above *quiet*,
    and falls *rise_db* again (hysteresis on a 50 ms power average: three sines of a chord
    repeat every 18 ms, so their 10 ms loudness swings by itself).  A tone or a steady chord
    has none; speech has a few per second (the test footage: about 35 per 25 s)."""
    if len(db) < 3:
        return 0
    half = _SMOOTH_FRAMES // 2
    power = [0.0]
    for v in db:
        power.append(power[-1] + (10.0 ** (v / 10.0) if v > FLOOR_DB else 0.0))
    smooth = []
    for i in range(len(db)):
        a, b = max(0, i - half), min(len(db), i + half + 1)
        mean = (power[b] - power[a]) / (b - a)
        smooth.append(10.0 * math.log10(mean) if mean > 1e-10 else FLOOR_DB)
    count, low, high, rising = 0, smooth[0], smooth[0], False
    for v in smooth:
        if not rising:
            low = min(low, v)
            if v - low >= rise_db and v >= quiet:
                rising, high = True, v
        else:
            high = max(high, v)
            if high - v >= rise_db:
                count += 1
                rising, low = False, v
    return count


def has_speech(db: list[float], start_s: float, end_s: float, hop_s: float = HOP_S,
               quiet: float | None = None) -> bool:
    """The no-speech gate of a piece: loud frames (above :data:`GATE_DB` and the pause level)
    for 0.2 s or more, with at least :data:`_MIN_SYLLABLES` syllable-like peaks."""
    if quiet is None:
        quiet = quiet_level(db)
    a, b = _frames(db, start_s, end_s, hop_s)
    part = db[a:b]
    floor = max(GATE_DB, quiet)
    if sum(1 for v in part if v >= floor) * hop_s < 0.2:
        return False
    return syllables(part, floor) >= _MIN_SYLLABLES


def write_piece(src: str, out: str, start_s: float, end_s: float) -> float:
    """Copy ``[start_s, end_s)`` of the WAV *src* to *out* (same format); its length in s."""
    with wave.open(str(src), "rb") as w:
        rate = w.getframerate()
        a = max(0, int(round(start_s * rate)))
        b = min(w.getnframes(), int(round(end_s * rate))) if end_s >= 0 else w.getnframes()
        w.setpos(min(a, w.getnframes()))
        data = w.readframes(max(0, b - a))
        params = w.getparams()
    with wave.open(str(out), "wb") as o:
        o.setnchannels(params.nchannels)
        o.setsampwidth(params.sampwidth)
        o.setframerate(rate)
        o.writeframes(data)
    return max(0, b - a) / float(rate)


# -- The repeat check ---------------------------------------------------------------------

_NORM_RE = re.compile(r"[^\w']+", re.UNICODE)


def _norm(word: str) -> str:
    return _NORM_RE.sub("", str(word or "").lower())


def repeat_run(texts: list[str], max_n: int = _REPEAT_MAX_N) -> tuple[int, int, int]:
    """``(times, n, index)``: the longest run of one sequence of *n* words repeated back to
    back (most words covered; ``(1, 0, 0)`` when nothing repeats)."""
    norm = [_norm(t) for t in texts]
    best = (1, 0, 0)
    for n in range(1, max_n + 1):
        i = 0
        while i + 2 * n <= len(norm):
            k = 1
            while i + (k + 1) * n <= len(norm) and norm[i + k * n:i + (k + 1) * n] == norm[i:i + n]:
                k += 1
            if k > 1 and k * n > best[0] * best[1]:
                best = (k, n, i)
            i += (k - 1) * n if k > 1 else 1
    return best


def degenerate(words: list[dict[str, Any]] | list[str], length_s: float) -> str:
    """"" or why the words of a piece *length_s* long are whisper looping, not speech."""
    texts = [w["w"] if isinstance(w, dict) else str(w) for w in words]
    texts = [t for t in texts if _norm(t)]
    if length_s > 0 and len(texts) > MAX_WORDS_PER_S * length_s + 8:
        return "{:d} words in {:.0f} s".format(len(texts), length_s)
    times, n, _i = repeat_run(texts)
    if n and times >= _REPEAT_MIN_TIMES and times * n >= _REPEAT_MIN_WORDS:
        return "a {:d}-word sequence repeated {:d} times".format(n, times)
    return ""


# -- Back to file time --------------------------------------------------------------------

def shift_verbose_json(vj: dict[str, Any], offset_s: float, length_s: float | None = None) -> list[dict[str, Any]]:
    """The segments of a piece's ``verbose_json`` moved by *offset_s* to file time (``start``,
    ``end`` and the tokens' ``t_dtw`` in 10 ms, ``start`` / ``end`` / ``t0`` / ``t1``); with
    *length_s*, the segments whisper put after the piece's end are dropped."""
    out = []
    for seg in vj.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        start = float(seg.get("start") or 0.0)
        if length_s is not None and start > length_s + 0.5:
            continue
        s = dict(seg)
        s["start"] = round(start + offset_s, 3)
        s["end"] = round(float(seg.get("end") or start) + offset_s, 3)
        toks = []
        for tok in seg.get("words") or []:
            if not isinstance(tok, dict):
                continue
            t = dict(tok)
            if isinstance(t.get("t_dtw"), (int, float)) and t["t_dtw"] >= 0:
                t["t_dtw"] = t["t_dtw"] + int(round(offset_s * 100))
            for name in ("start", "end"):
                if isinstance(t.get(name), (int, float)):
                    t[name] = round(t[name] + offset_s, 3)
            toks.append(t)
        if "words" in seg:
            s["words"] = toks
        out.append(s)
    return out


def merge_verbose_json(parts: list[tuple[float, float | None, dict[str, Any]]], duration: float,
                       language: str = "") -> dict[str, Any]:
    """One ``verbose_json`` of the whole file from ``(offset s, length s, answer)`` per piece
    sent; the language is the first piece's that said one (or *language*)."""
    segments: list[dict[str, Any]] = []
    first: dict[str, Any] = {}
    for offset, length, vj in parts:
        segments.extend(shift_verbose_json(vj, offset, length))
        if not first and (vj.get("language_probabilities") or vj.get("language")) and vj.get("segments"):
            first = vj
    out: dict[str, Any] = {"duration": round(float(duration or 0.0), 3), "segments": segments,
                           "text": "".join(str(s.get("text") or "") for s in segments)}
    if first.get("language_probabilities"):
        out["language_probabilities"] = first["language_probabilities"]
    if first.get("language") or language:
        out["language"] = first.get("language") or language
    return out


# -- Word ends ----------------------------------------------------------------------------

def refine_words(words: list[dict[str, Any]], db: list[float], hop_s: float = HOP_S,
                 quiet: float | None = None) -> list[dict[str, Any]]:
    """*words* (file time, ends already capped by :func:`transcripts.word_cap`) with the
    words that lie in silence dropped (nothing within 0.2 s of them is louder than the pause
    level), and each end moved back to the first pause of :data:`_WORD_PAUSE_S` after the
    word started."""
    if not db:
        return [dict(w) for w in words]
    if quiet is None:
        quiet = quiet_level(db)
    pause = max(1, int(round(_WORD_PAUSE_S / hop_s)))
    out = []
    for w in words:
        start, end = float(w["start"]), float(w["end"])
        a, b = _frames(db, start - 0.2, end + 0.2, hop_s)
        if a >= b or max(db[a:b]) < quiet:
            continue
        last = min(len(db), int(math.ceil(end / hop_s)))
        # From the word's first loud frame on (a DTW start can be a little early).
        onset = next((i for i in range(max(0, int(math.floor(start / hop_s))), last) if db[i] >= quiet), last)
        f = max(onset, int(math.floor((start + _WORD_MIN_S) / hop_s)))
        quiet_run = 0
        new_end = end
        for i in range(max(0, f), last):
            quiet_run = quiet_run + 1 if db[i] < quiet else 0
            if quiet_run >= pause:
                new_end = (i - pause + 1) * hop_s + _WORD_TAIL_S
                break
        new_end = min(end, max(start + _WORD_MIN_S, new_end))
        out.append(dict(w, end=round(new_end, 3)))
    return out
