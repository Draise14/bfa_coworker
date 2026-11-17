# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Edit by Transcript (Tier 4, Video 7, #498): the parts that need no Blender.

The text in the Text Editor is a *view* of a transcript's word list (``transcripts.py``,
``bfa_coworker.transcript/1``): the words a source (one media file) plays on the timeline, in
timeline order, one line per sentence (or after a pause), each line with a small time mark.
Editing that text edits the video.

* **The view** (:func:`timeline_words`, :func:`render`): which words each strip of the source
  shows (a word belongs to the strip whose visible frames hold its start, as the captions do),
  in timeline order; rendered to lines ``[0:05.2] Hello there, this is a sentence.``.  Every
  token of the text knows its word (by its index in the transcript), so the text can be
  rebuilt from the timeline at any time.
* **The edit** (:func:`compute_edit`): the last applied text (its tokens) against the text as
  it is now.  Deleted words are *cut*; changed words (a typo, punctuation, "alot" -> "a lot")
  are *retext*: their timing stays and only the captions change; a block of words that moved
  is a *move*.  Text with no audio behind it (typed in, or a line rewritten so it no longer
  matches what is said) is *flagged* on its line and never cut or placed by guessing: the
  words it replaced stay as they are.  The time marks may be edited or deleted: the parser
  ignores them.
* **The timeline** (:func:`cut_ranges`, :func:`plan_moves`, :func:`move_ranges`): the cut
  words become timeline frame ranges, padded and placed in the pause between words, so the
  Sequencer side (``transcript_edit_ops``) can cut them with Remove Silences' split / ripple
  code (``wand/sequencer.py``, ``wand/cut_plan.py``); moves become blocks of frames.
* **The record** (:func:`edit_record`, :func:`edits_path`): the transcript file is never
  changed; the edit list goes into a file next to it (``<transcript>.edits.json``).
* **Filler words** (:func:`find_fillers`): "um", "uh"... as whisper wrote them.

Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "EDIT_SCHEMA",
    "FILLERS",
    "FILLER_PHRASES",
    "MARK_RE",
    "PADDING_S",
    "PROP",
    "STRIP_PROP",
    "Edit",
    "caption_text",
    "compute_edit",
    "cut_ranges",
    "display",
    "edit_record",
    "edits_path",
    "find_fillers",
    "format_mark",
    "line_at_frame",
    "line_frames",
    "line_of_word",
    "line_words",
    "move_ranges",
    "norm",
    "parse",
    "parse_mark",
    "plan_moves",
    "read_record",
    "remove_tokens",
    "render",
    "timeline_words",
    "write_record",
)

import bisect
import datetime
import difflib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

EDIT_SCHEMA = "bfa_coworker.transcript_edit/1"
PROP = "bfacw_transcript"            # on the Text data-block: what it is a view of (see transcript_edit_ops)
STRIP_PROP = "bfacw_transcript_text"  # on a strip: the name of its transcript text

PADDING_S = 0.12        # pause kept next to the words around a cut, so no word is clipped
LINE_PAUSE_S = 0.8      # a pause this long starts a new line
PARAGRAPH_S = 2.0       # ... and this long a new paragraph (a blank line)
MAX_LINE_WORDS = 28     # a line longer than this breaks after a comma, or at 1.5 times this
SIMILAR = 0.6           # how alike a changed word must be to count as the same word (a typo)
MERGED = 0.8            # how alike (letters only) words joined or split must stay ("went up" -> "wentup")
MOVE_MIN_TOKENS = 2     # a moved block has at least this many words (a lone "the" is no move)
_DP_LIMIT = 160_000     # word pairs compared at most inside one changed block

# A time mark at the start of a line: "[0:05.2]", "[1:02:03.4]".  Edited or deleted marks are
# fine: anything in brackets made of digits, colons, dots, commas and spaces at the start of a
# line is not text.
MARK_RE = re.compile(r"^\s*\[[\d\s:.,;'\-]*\]\s?")
_EDGE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)

_SENTENCE_END = (".", "!", "?", "…", "。", "！", "？")
_CLAUSE_END = (",", ";", ":", "—", "–", "，", "、")
_CLOSERS = "\"')]}”’»"
_ABBREVIATIONS = frozenset({"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "mt.", "e.g.", "i.e.", "etc.", "vs.",
                            "approx.", "no.", "sr.", "jr.", "sra."})

# Filler words as whisper writes them (it leaves most of them out: what is there is all there is).
FILLERS = frozenset({"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "ahh", "hmm", "hm", "mm", "mhm", "eh"})
FILLER_PHRASES = (("you", "know"), ("i", "mean"), ("kind", "of"), ("sort", "of"), ("like",))


# -- words and tokens ------------------------------------------------------------------------

def norm(token: str) -> str:
    """A token as it is compared: lower case, without the punctuation around it ("Hello," ->
    "hello"); a token of punctuation only stays as it is."""
    t = str(token or "").strip()
    core = _EDGE.sub("", t).casefold()
    return core or t


def _text(word: Any) -> str:
    if isinstance(word, dict):
        return " ".join(str(word.get("w", word.get("word", ""))).split())
    return " ".join(str(word or "").split())


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default       # NaN


def _start(word: Any) -> float:
    return _num(word.get("start")) if isinstance(word, dict) else 0.0


def _end(word: Any) -> float:
    if not isinstance(word, dict):
        return 0.0
    return max(_start(word), _num(word.get("end"), _start(word)))


def _sentence_end(token: str) -> bool:
    core = token.rstrip(_CLOSERS)
    if not core.endswith(_SENTENCE_END):
        return False
    low = core.lower()
    if low in _ABBREVIATIONS:
        return False
    return not (re.fullmatch(r"(?:[A-Za-z]\.){1,}", core) and len(core) <= 6)


def _clause_end(token: str) -> bool:
    return token.rstrip(_CLOSERS).endswith(_CLAUSE_END)


def display(i: int, words: Sequence[Any], retext: dict | None) -> str:
    """The text of word *i* as the view shows it: its retext when it has one ("" for a word
    joined to the word before it), else the transcript's."""
    if retext:
        for key in (i, str(i)):
            if key in retext:
                return " ".join(str(retext[key]).split())
    return _text(words[i]) if 0 <= i < len(words) else ""


# -- the timeline: which words each strip of the source shows --------------------------------

def timeline_words(words: Sequence[Any], pieces: Iterable[dict], fps: float,
                   frame_start: int = 1) -> list[dict[str, Any]]:
    """The words of one source on the timeline, in timeline order.

    *pieces*: the source's strips (``{"name", "left", "right", "content_start", "offset"}``:
    frames, *offset* the sound offset in seconds).  A word is in the piece whose visible frames
    (``left <= frame < right``) hold its start, as captions count it.  Each occurrence:
    ``{"i", "piece", "frame", "end_frame", "t"}`` (*t*: seconds from the scene start)."""
    fps = float(fps) or 24.0
    starts, index = [], []
    for i, w in enumerate(words):
        if _text(w):
            starts.append(_start(w))
            index.append(i)
    order = sorted(range(len(starts)), key=lambda k: starts[k])
    starts = [starts[k] for k in order]
    index = [index[k] for k in order]
    out: list[dict[str, Any]] = []
    for piece in sorted(pieces, key=lambda p: (p["left"], p.get("channel", 0), p["name"])):
        cs, off = float(piece["content_start"]), float(piece.get("offset") or 0.0)
        left, right = int(piece["left"]), int(piece["right"])
        t0 = (left - 0.5 - cs) / fps + off              # media seconds that can round onto the piece
        k = bisect.bisect_left(starts, t0)
        while k < len(starts):
            frame = int(round(cs + (starts[k] - off) * fps))
            if frame >= right:
                break
            if frame >= left:
                i = index[k]
                end_frame = cs + (_end(words[i]) - off) * fps
                out.append({"i": i, "piece": piece["name"], "frame": frame,
                            "end_frame": round(end_frame, 3), "t": (frame - frame_start) / fps})
            k += 1
    return out


# -- the view: words -> lines of text ----------------------------------------------------------

def format_mark(seconds: float) -> str:
    """``[0:05.2]``, ``[12:30.0]``, ``[1:02:03.4]``."""
    tenths = int(round(max(0.0, float(seconds)) * 10))
    whole, frac = divmod(tenths, 10)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "[{:d}:{:02d}:{:02d}.{:d}]".format(hours, minutes, secs, frac)
    return "[{:d}:{:02d}.{:d}]".format(minutes, secs, frac)


def parse_mark(line: str) -> float | None:
    """The seconds of a line's time mark, or None when it has none (or it can't be read)."""
    m = MARK_RE.match(line or "")
    if not m:
        return None
    body = m.group(0).strip().strip("[]").replace(",", ".").strip()
    try:
        parts = [float(p) for p in body.split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3:
        return None
    total = 0.0
    for p in parts:
        total = total * 60.0 + p
    return total


def render(occurrences: Sequence[dict], words: Sequence[Any], retext: dict | None = None,
           fps: float = 24.0) -> dict[str, Any]:
    """The text of a view: one line per sentence (or after a pause of :data:`LINE_PAUSE_S`), a
    blank line after a long pause, each line starting with its time mark.

    Returns ``{"text", "tokens": [[word index, token]], "order": [word indices], "lines":
    [{"line": 0-based line in the text, "frame", "t", "words": [indices]}]}``.  A word with an
    empty retext (joined to the word before it) has no token but is in ``order``."""
    fps = float(fps) or 24.0
    tokens: list[list[Any]] = []
    order: list[int] = []
    lines: list[dict[str, Any]] = []
    out_lines: list[str] = []
    cur: list[str] = []
    cur_words: list[int] = []
    cur_info: dict[str, Any] | None = None
    prev: dict | None = None
    prev_token = ""

    def flush(blank: bool) -> None:
        nonlocal cur, cur_words, cur_info
        if cur_info is not None and cur:
            cur_info["line"] = len(out_lines)
            cur_info["words"] = cur_words
            lines.append(cur_info)
            out_lines.append(format_mark(cur_info["t"]) + " " + " ".join(cur))
            if blank:
                out_lines.append("")
        cur, cur_words, cur_info = [], [], None

    for occ in occurrences:
        i = int(occ["i"])
        order.append(i)
        shown = display(i, words, retext)
        if not shown:                       # joined to the word before it
            if cur_info is not None:
                cur_words.append(i)
            continue
        if prev is not None and cur:
            gap = (occ["frame"] - float(prev.get("end_frame", prev["frame"]))) / fps
            n = len(cur)
            if gap >= PARAGRAPH_S:
                flush(True)
            elif gap >= LINE_PAUSE_S or _sentence_end(prev_token) or n >= MAX_LINE_WORDS * 1.5 \
                    or (n >= MAX_LINE_WORDS and _clause_end(prev_token)):
                flush(False)
        if cur_info is None:
            cur_info = {"frame": int(occ["frame"]), "t": float(occ.get("t", 0.0))}
        for tok in shown.split():
            tokens.append([i, tok])
            cur.append(tok)
        cur_words.append(i)
        prev, prev_token = occ, shown.split()[-1]
    flush(False)
    while out_lines and not out_lines[-1]:
        out_lines.pop()
    return {"text": "\n".join(out_lines) + ("\n" if out_lines else ""), "tokens": tokens, "order": order,
            "lines": lines}


def parse(text: str) -> list[tuple[int, str]]:
    """The tokens of a text: ``(0-based line, token)``, without the time marks."""
    out = []
    for n, line in enumerate(str(text or "").splitlines()):
        body = MARK_RE.sub("", line, count=1)
        for tok in body.split():
            out.append((n, tok))
    return out


# -- the edit: last applied text -> text now ----------------------------------------------------

class Edit(dict):
    """What changed (a dict, JSON-ready).

    ``order``: the word indices in their new order (kept words; joined words after theirs);
    ``cut``: word indices cut; ``retext``: the full new ``{index: text}`` (relative to the
    transcript); ``retexted``: the indices whose text changed in this edit; ``moved``: whether
    the order of the kept words changed; ``flags``: ``[{"line", "text", "reason", "message"}]``
    (0-based lines of the new text); ``ops``: the edit list (cut / retext / move);
    ``changed``: anything to apply."""

    @property
    def flags(self) -> list[dict]:
        return self["flags"]


def _ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _align(a: Sequence[str], b: Sequence[str]) -> list[tuple[int, int]] | None:
    """Pairs ``(i, j)`` in order matching the most tokens of *a* to tokens of *b* that are alike
    (:data:`SIMILAR`); None when the block is too big to compare."""
    n, m = len(a), len(b)
    if n * m > _DP_LIMIT:
        return None
    sim = [[_ratio(a[i], b[j]) for j in range(m)] for i in range(n)]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            val = max(best[i + 1][j], best[i][j + 1])
            if sim[i][j] >= SIMILAR:
                val = max(val, best[i + 1][j + 1] + 1.0 + sim[i][j] * 1e-3)
            best[i][j] = val
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if sim[i][j] >= SIMILAR and abs(best[i][j] - (best[i + 1][j + 1] + 1.0 + sim[i][j] * 1e-3)) < 1e-9:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _runs(indices: Iterable[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for k in sorted(set(indices)):
        if out and k == out[-1][-1] + 1:
            out[-1].append(k)
        else:
            out.append([k])
    return out


def compute_edit(base_tokens: Sequence[Sequence[Any]], base_order: Sequence[int], text: str,
                 words: Sequence[Any], retext: dict | None = None) -> Edit:
    """The edit from the last applied text to *text*.

    *base_tokens*: ``[[word index, token]]`` of the last applied text (:func:`render`);
    *base_order*: its word order (with the joined words); *retext*: its retexts."""
    base = [(int(i), str(t)) for i, t in base_tokens]
    cur = parse(text)
    a = [norm(t) for _i, t in base]
    b = [norm(t) for _n, t in cur]
    assign: list[int | None] = [None] * len(cur)     # current token -> base token
    deleted: list[int] = []                          # base tokens gone
    inserted: list[int] = []                         # current tokens with nothing behind them
    flagged: dict[int, str] = {}                     # current token -> reason
    joined_into: dict[int, int] = {}                 # base token -> base token it is joined to now
    kept_as_is: dict[int, list[int]] = {}            # first current token of a flagged block -> base tokens
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                assign[j1 + k] = i1 + k
        elif tag == "delete":
            deleted.extend(range(i1, i2))
        elif tag == "insert":
            inserted.extend(range(j1, j2))
        else:
            _replace(a, b, i1, i2, j1, j2, assign, deleted, inserted, flagged, joined_into, kept_as_is)
    _moves(a, b, deleted, inserted, assign)
    for j in inserted:
        flagged.setdefault(j, "no_audio")

    # The new order: the current tokens' words, in text order.
    word_of = [i for i, _t in base]
    tokens_of: dict[int, list[str]] = {}
    new_order: list[int] = []
    seen: set[int] = set()

    def emit(w: int) -> None:
        if w not in seen:
            seen.add(w)
            new_order.append(w)

    for j, (_line, tok) in enumerate(cur):
        if j in kept_as_is:
            for bt in kept_as_is[j]:
                emit(word_of[bt])
                tokens_of.setdefault(word_of[bt], []).append(base[bt][1])
        bt = assign[j]
        if bt is None:
            continue
        w = word_of[bt]
        emit(w)
        tokens_of.setdefault(w, []).append(tok)
        for other, owner in sorted(joined_into.items()):
            if owner == bt:
                emit(word_of[other])
                tokens_of.setdefault(word_of[other], [])
    # Words without tokens in the last text (joined to the word before them) follow that word.
    with_tokens = set(word_of)
    final: list[int] = []
    pending_owner: dict[int, list[int]] = {}
    owner = None
    for w in base_order:
        if w in with_tokens:
            owner = w
        elif owner is not None:
            pending_owner.setdefault(owner, []).append(w)
    for w in new_order:
        final.append(w)
        for extra in pending_owner.get(w, ()):
            if extra not in seen:
                final.append(extra)
                tokens_of.setdefault(extra, [])
    seen_final = set(final)
    cut = [w for w in base_order if w not in seen_final]

    new_retext: dict[str, str] = {}
    for key, value in (retext or {}).items():
        try:
            k = int(key)
        except (TypeError, ValueError):
            continue
        if k in seen_final:
            new_retext[str(k)] = str(value)
    retexted = []
    for w in final:
        toks = tokens_of.get(w)
        if toks is None:
            continue
        shown = " ".join(toks)
        old = display(w, words, retext)
        if shown == old:
            continue
        retexted.append(w)
        if shown == _text(words[w]) if 0 <= w < len(words) else False:
            new_retext.pop(str(w), None)
        else:
            new_retext[str(w)] = shown
    kept_base = [w for w in base_order if w in seen_final]
    moved = kept_base != final

    flags = _flags(cur, flagged)
    ops = _ops(base_order, cut, final, retexted, words, retext, new_retext, moved)
    return Edit(order=final, cut=cut, retext=new_retext, retexted=retexted, moved=moved, flags=flags, ops=ops,
                changed=bool(cut or retexted or moved))


def _replace(a, b, i1, i2, j1, j2, assign, deleted, inserted, flagged, joined_into, kept_as_is) -> None:
    """A changed block: a typo or a reworded word keeps its time (retext); words taken out go;
    text that no longer matches is flagged and the words it replaced stay."""
    old, new = a[i1:i2], b[j1:j2]
    if len(old) == len(new) and all(_ratio(x, y) >= SIMILAR for x, y in zip(old, new)):
        for k in range(len(old)):
            assign[j1 + k] = i1 + k
        return
    whole = _ratio("".join(old), "".join(new))
    if whole >= MERGED and abs(len(old) - len(new)) <= 2 and len(old) <= 6:
        # "a lot" -> "alot", "today" -> "to day": one retext over the block, timing kept.
        for j in range(j1, j2):
            assign[j] = i1
        for i in range(i1 + 1, i2):
            joined_into[i] = i1
        return
    pairs = _align(old, new)
    if pairs is None:
        _flag_block(i1, i2, j1, j2, flagged, kept_as_is, "no_match")
        return
    left_old = [i1 + i for i in range(len(old)) if i not in {p for p, _q in pairs}]
    left_new = [j1 + j for j in range(len(new)) if j not in {q for _p, q in pairs}]
    if left_old and left_new:
        _flag_block(i1, i2, j1, j2, flagged, kept_as_is, "no_match")
        return
    for p, q in pairs:
        assign[j1 + q] = i1 + p
    deleted.extend(left_old)
    inserted.extend(left_new)


def _flag_block(i1, i2, j1, j2, flagged, kept_as_is, reason) -> None:
    for j in range(j1, j2):
        flagged[j] = reason
    if j2 > j1:
        kept_as_is[j1] = list(range(i1, i2))
    else:                                   # cannot happen for a replace; kept for safety
        kept_as_is.setdefault(j1, []).extend(range(i1, i2))


def _moves(a, b, deleted: list[int], inserted: list[int], assign: list) -> None:
    """A block of words deleted in one place and the same words inserted in another: a move."""
    d_runs = _runs(deleted)
    i_runs = _runs(inserted)
    if not d_runs or not i_runs:
        return
    used_d: set[int] = set()
    matched_i: set[int] = set()
    for ir in sorted(i_runs, key=len, reverse=True):
        if len(ir) < MOVE_MIN_TOKENS:
            continue
        ins = [b[j] for j in ir]
        best = None
        for n, dr in enumerate(d_runs):
            if n in used_d:
                continue
            # the inserted words inside a deleted run (a line moved out of a longer deletion), or equal
            dl = [a[i] for i in dr]
            for off in range(0, len(dl) - len(ins) + 1):
                window = dl[off:off + len(ins)]
                if window == ins or (len(ins) >= 3 and _ratio(" ".join(window), " ".join(ins)) >= 0.85):
                    score = (window == ins, -abs(len(dl) - len(ins)))
                    if best is None or score > best[0]:
                        best = (score, n, off)
                    break
        if best is None:
            continue
        _score, n, off = best
        dr = d_runs[n]
        taken = dr[off:off + len(ir)]
        for i, j in zip(taken, ir):
            assign[j] = i
        matched_i.update(ir)
        rest = [i for i in dr if i not in taken]
        d_runs[n] = []
        used_d.add(n)
        for piece in _runs(rest):
            d_runs.append(piece)
        for i in taken:
            deleted.remove(i)
    inserted[:] = [j for j in inserted if j not in matched_i]


_REASONS = {
    "no_audio": "no audio behind this text: nothing is added to the video",
    "no_match": "this no longer matches what is said: nothing here is cut or changed",
}


def _flags(cur: Sequence[tuple[int, str]], flagged: dict[int, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for j in sorted(flagged):
        line, tok = cur[j]
        reason = flagged[j]
        if out and out[-1]["line"] == line and out[-1]["reason"] == reason and out[-1]["_last"] == j - 1:
            out[-1]["text"] += " " + tok
            out[-1]["_last"] = j
            continue
        out.append({"line": line, "text": tok, "reason": reason, "message": _REASONS[reason], "_last": j})
    for f in out:
        del f["_last"]
    return out


def _ops(base_order, cut, final, retexted, words, old_retext, new_retext, moved) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    pos = {w: k for k, w in enumerate(base_order)}
    for run in _runs(pos[w] for w in cut if w in pos):
        ws = [base_order[k] for k in run]
        ops.append({"op": "cut", "words": [ws[0], ws[-1]], "count": len(ws),
                    "text": " ".join(t for t in (display(w, words, old_retext) for w in ws) if t)})
    for w in retexted:
        ops.append({"op": "retext", "word": w, "from": display(w, words, old_retext),
                    "to": display(w, words, new_retext)})
    if moved:
        kept = [w for w in base_order if w in set(final)]
        for block, after in plan_moves(kept, final):
            ops.append({"op": "move", "words": [block[0], block[-1]], "count": len(block), "after": after})
    return ops


# -- moves ---------------------------------------------------------------------------------------

def _lis(seq: Sequence[int]) -> set[int]:
    """Positions of one longest increasing subsequence of *seq*."""
    tails: list[int] = []
    tails_at: list[int] = []
    back = [-1] * len(seq)
    for k, v in enumerate(seq):
        n = bisect.bisect_left(tails, v)
        if n == len(tails):
            tails.append(v)
            tails_at.append(k)
        else:
            tails[n] = v
            tails_at[n] = k
        back[k] = tails_at[n - 1] if n else -1
    out: set[int] = set()
    k = tails_at[-1] if tails_at else -1
    while k >= 0:
        out.add(k)
        k = back[k]
    return out


def plan_moves(current: Sequence[int], target: Sequence[int]) -> list[tuple[list[int], int | None]]:
    """The blocks to move to turn the word order *current* into *target* (the same words):
    ``[(block of words in target order, the word it goes after, or None for the start)]``.

    The words that keep their relative order (a longest increasing run) stay; the others move,
    as few blocks as runs allow; applied one after another, in this order."""
    pos = {w: k for k, w in enumerate(current)}
    seq = [pos[w] for w in target if w in pos]
    tw = [w for w in target if w in pos]
    stay = _lis(seq)
    out: list[tuple[list[int], int | None]] = []
    k = 0
    while k < len(tw):
        if k in stay:
            k += 1
            continue
        block = [tw[k]]
        while k + 1 < len(tw) and k + 1 not in stay and seq[k + 1] == seq[k] + 1:
            k += 1
            block.append(tw[k])
        start = k - len(block) + 1
        out.append((block, tw[start - 1] if start > 0 else None))
        k += 1
    return out


# -- the timeline: cuts and moves as frames ------------------------------------------------------

def _by_piece(occurrences: Sequence[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for occ in occurrences:
        out.setdefault(occ["piece"], []).append(occ)
    for occs in out.values():
        occs.sort(key=lambda o: o["frame"])
    return out


def _gap_point(words, prev: int | None, nxt: int | None, padding: float, side: str) -> float | None:
    """Media seconds where a cut starts (*side* "start": *nxt* is the first word cut, *prev* the
    word kept before it) or ends ("end": *nxt* is the next word kept): *padding* (at most half
    the pause) before the word after the cut point.  So a cut takes the words and the pause
    after them, and the pause before them stays as it was: the rhythm of the speech is kept."""
    if side == "start":
        if prev is None:
            return None
        pe, ws = _end(words[prev]), _start(words[nxt])
        gap = max(0.0, ws - pe)
        return max(pe, ws - min(padding, gap / 2.0))
    if nxt is None:
        return None
    ns = _start(words[nxt])
    we = _end(words[prev]) if prev is not None else None
    gap = max(0.0, (ns - we) if we is not None else padding * 2)
    point = ns - min(padding, gap / 2.0)
    return max(point, we) if we is not None else point


def cut_ranges(occurrences: Sequence[dict], cut: Iterable[int], words: Sequence[Any], pieces: Iterable[dict],
               fps: float, padding: float = PADDING_S) -> list[tuple[int, int]]:
    """Timeline frame ranges ``(first, end)`` (end exclusive) that remove the *cut* words.

    In each strip, a run of cut words goes with the pause around it, up to *padding* (or half
    the pause, when shorter) next to the words kept; a run at the start or the end of a strip
    goes to its edge.  Merged and sorted."""
    cut = set(cut)
    fps = float(fps) or 24.0
    info = {p["name"]: p for p in pieces}
    out: list[tuple[int, int]] = []
    for name, occs in _by_piece(occurrences).items():
        p = info.get(name)
        if p is None:
            continue
        cs, off = float(p["content_start"]), float(p.get("offset") or 0.0)
        left, right = int(p["left"]), int(p["right"])
        k = 0
        while k < len(occs):
            if occs[k]["i"] not in cut:
                k += 1
                continue
            first = k
            while k + 1 < len(occs) and occs[k + 1]["i"] in cut:
                k += 1
            prev = occs[first - 1]["i"] if first > 0 else None
            nxt = occs[k + 1]["i"] if k + 1 < len(occs) else None
            s = _gap_point(words, prev, occs[first]["i"], padding, "start")
            e = _gap_point(words, occs[k]["i"], nxt, padding, "end")
            f0 = left if s is None else int(round(cs + (s - off) * fps))
            f1 = right if e is None else int(round(cs + (e - off) * fps))
            f0, f1 = max(f0, left), min(f1, right)
            if f1 > f0:
                out.append((f0, f1))
            k += 1
    return _merge(out)


def _merge(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(r for r in ranges if r[1] > r[0]):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _boundary(words, occs: Sequence[dict], k: int, p: dict, fps: float) -> int:
    """The timeline frame between occurrence *k*-1 and *k* of one strip: the middle of the pause
    (the strip's edge before the first word or after the last)."""
    cs, off = float(p["content_start"]), float(p.get("offset") or 0.0)
    if k <= 0:
        return int(p["left"])
    if k >= len(occs):
        return int(p["right"])
    pe, ns = _end(words[occs[k - 1]["i"]]), _start(words[occs[k]["i"]])
    mid = (min(pe, ns) + ns) / 2.0
    return max(int(p["left"]), min(int(p["right"]), int(round(cs + (mid - off) * fps))))


def move_ranges(occurrences: Sequence[dict], block: Sequence[int], after: int | None, words: Sequence[Any],
                pieces: Iterable[dict], fps: float) -> tuple[int, int, int] | None:
    """A move as frames: ``(first, end, to)``: the frames ``first..end`` (the *block*'s words,
    cut in the middle of the pauses around them) go to frame *to* (the pause after the word
    *after*, or the start of the source's first strip).  None when the block isn't one
    stretch of one strip (move it in smaller steps)."""
    fps = float(fps) or 24.0
    info = {p["name"]: p for p in pieces}
    occs = sorted(occurrences, key=lambda o: o["frame"])
    where = {o["i"]: n for n, o in enumerate(occs)}
    if not block or any(w not in where for w in block):
        return None
    n0, n1 = where[block[0]], where[block[-1]]
    if n1 - n0 + 1 != len(block) or len({occs[n]["piece"] for n in range(n0, n1 + 1)}) != 1:
        return None
    piece = occs[n0]["piece"]
    mine = [o for o in occs if o["piece"] == piece]
    k0 = next(k for k, o in enumerate(mine) if o["i"] == block[0])
    k1 = k0 + len(block)
    p = info[piece]
    first = _boundary(words, mine, k0, p, fps)
    end = _boundary(words, mine, k1, p, fps)
    if after is None:
        to = min(int(info[o["piece"]]["left"]) for o in occs if o["piece"] in info)
    else:
        if after not in where:
            return None
        o = occs[where[after]]
        theirs = [x for x in occs if x["piece"] == o["piece"]]
        k = next(k for k, x in enumerate(theirs) if x["i"] == after) + 1
        to = _boundary(words, theirs, k, info[o["piece"]], fps)
    if end <= first:
        return None
    return first, end, to


# -- lines, captions and filler words -----------------------------------------------------------

def line_words(base_tokens: Sequence[Sequence[Any]], text: str) -> dict[int, list[int]]:
    """``{0-based line: [word indices]}`` of *text* as it is now (edited or not): its tokens
    matched to the last applied text's.  A line of new text has no words."""
    base = [(int(i), str(t)) for i, t in base_tokens]
    cur = parse(text)
    sm = difflib.SequenceMatcher(None, [norm(t) for _i, t in base], [norm(t) for _n, t in cur], autojunk=False)
    out: dict[int, list[int]] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(j2 - j1):
                line = cur[j1 + k][0]
                w = base[i1 + k][0]
                ws = out.setdefault(line, [])
                if w not in ws:
                    ws.append(w)
    return out


def line_frames(base_tokens: Sequence[Sequence[Any]], text: str, frames: dict) -> dict[int, int]:
    """``{0-based line: timeline frame of its first word}`` for *text* as it is now; *frames*:
    ``{word index (int or str): frame}``.  A line whose words aren't known uses its time mark
    (not here: the caller has the scene's frame rate)."""
    out = {}
    for line, ws in line_words(base_tokens, text).items():
        got = [frames.get(w, frames.get(str(w))) for w in ws]
        got = [int(f) for f in got if f is not None]
        if got:
            out[line] = min(got)
    return out


def line_of_word(lines: Sequence[dict], word: int) -> int | None:
    for info in lines:
        if word in info.get("words", ()):
            return int(info["line"])
    return None


def line_at_frame(lines: Sequence[dict], frame: float) -> int | None:
    """The text line playing at *frame* (the last one starting at or before it)."""
    best = None
    for info in lines:
        if info["frame"] <= frame and (best is None or info["frame"] >= best["frame"]):
            best = info
    return int(best["line"]) if best is not None else None


def caption_text(occurrences: Sequence[dict], words: Sequence[Any], retext: dict | None,
                 left: int, right: int) -> str:
    """The words a caption over frames ``left..right`` shows (their retexts), in order."""
    out = []
    for occ in sorted(occurrences, key=lambda o: o["frame"]):
        if left <= occ["frame"] < right:
            shown = display(occ["i"], words, retext)
            if shown:
                out.append(shown)
    return " ".join(out)


def find_fillers(occurrences: Sequence[dict], words: Sequence[Any], retext: dict | None = None,
                 phrases: bool = False) -> list[dict[str, Any]]:
    """The filler words in the view: ``[{"words": [indices], "text", "frame", "t"}]``.  Single
    fillers ("um", "uh"...) always; with *phrases* also "you know", "I mean", "kind of", "sort of"
    and "like" (real words too: only on request)."""
    occs = list(occurrences)
    found: list[dict[str, Any]] = []
    k = 0
    while k < len(occs):
        here = norm(display(occs[k]["i"], words, retext))
        hit = 0
        if here in FILLERS:
            hit = 1
        elif phrases:
            for ph in FILLER_PHRASES:
                if k + len(ph) <= len(occs) and all(
                        norm(display(occs[k + n]["i"], words, retext)) == ph[n] for n in range(len(ph))):
                    hit = len(ph)
                    break
        if hit:
            ws = [occs[k + n]["i"] for n in range(hit)]
            found.append({"words": ws, "text": " ".join(display(w, words, retext) for w in ws),
                          "frame": occs[k]["frame"], "t": occs[k].get("t", 0.0)})
            k += hit
        else:
            k += 1
    return found


def remove_tokens(text: str, base_tokens: Sequence[Sequence[Any]], doomed: Iterable[int]) -> str:
    """*text* with the tokens of the words *doomed* taken out (the text the artist sees, with
    their own edits kept): the tokens are found by aligning the text with the last applied one."""
    doomed = set(doomed)
    base = [(int(i), str(t)) for i, t in base_tokens]
    lines = str(text or "").splitlines()
    cur = []
    for n, line in enumerate(lines):
        m = MARK_RE.match(line)
        pos = m.end() if m else 0
        for mt in re.finditer(r"\S+", line[pos:]):
            cur.append((n, pos + mt.start(), pos + mt.end(), mt.group(0)))
    sm = difflib.SequenceMatcher(None, [norm(t) for _i, t in base], [norm(c[3]) for c in cur], autojunk=False)
    drop: dict[int, list[tuple[int, int]]] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            continue
        for k in range(i2 - i1):
            if base[i1 + k][0] in doomed:
                n, s, e, _t = cur[j1 + k]
                drop.setdefault(n, []).append((s, e))
    for n, spans in drop.items():
        line = lines[n]
        for s, e in sorted(spans, reverse=True):
            line = line[:s].rstrip(" ") + (" " if line[:s].strip() and line[e:].lstrip(" ") else "") + \
                line[e:].lstrip(" ")
        lines[n] = line
    out = "\n".join(lines)
    return out + ("\n" if str(text or "").endswith("\n") else "")


# -- the record next to the transcript -----------------------------------------------------------

def edits_path(transcript_path: str | os.PathLike) -> Path:
    """``<transcript>.edits.json`` next to the transcript (never the transcript itself)."""
    p = Path(transcript_path)
    stem = p.name[:-len(".json")] if p.name.endswith(".json") else p.name
    return p.with_name(stem + ".edits.json")


def edit_record(previous: dict | None, media: dict, edit: dict, order: Sequence[int],
                when: str | None = None) -> dict[str, Any]:
    """The edit list file: the media it belongs to, the state now (the word order on the
    timeline and the retexts) and every applied edit, oldest first."""
    rec = dict(previous) if isinstance(previous, dict) and previous.get("schema") == EDIT_SCHEMA else {}
    rec["schema"] = EDIT_SCHEMA
    rec["media"] = dict(media)
    history = list(rec.get("history") or [])
    if edit.get("ops"):
        history.append({"time": when or datetime.datetime.now(datetime.timezone.utc).replace(
            microsecond=0).isoformat(), "ops": list(edit["ops"])})
    rec["history"] = history
    rec["state"] = {"order": [int(w) for w in order], "retext": dict(edit.get("retext") or {})}
    return rec


def write_record(path: str | os.PathLike, record: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def read_record(path: str | os.PathLike) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == EDIT_SCHEMA else None
