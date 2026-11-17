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
* **The edit** (:func:`compute_edit`): the last applied text against the text as it is now,
  line by line.  Nothing is cut by guessing: a cut comes only from text the user took out,
  mapped with certainty to the words it came from.  Every line carries its own time mark; a line
  with the mark of one line of the last text IS that line, wherever it stands (this tells the
  second of three identical takes from the first, and makes a moved line certain); a line
  without a usable mark matches a line only by the same words (the only one that fits) or by
  nearly the same.  Inside a pair of lines: words deleted are *cut*; changed words (a typo,
  punctuation) are *retext*: their timing stays and only the captions change; n words written as
  m words are one retext of the block, the words stay; words added are flagged and the line is
  left as it was.  A line that moved is a *move*.  Everything uncertain (typed text, a rewritten
  line, a repeated word or line the text can't tell apart) is *flagged* on its line and changes
  nothing; the words it might be the rest of stay too.  A line nothing stands for is cut whole.
  The time marks are all different, so each line can be told.  A mark has exactly the shape the
  renderer writes (``[0:05.2]``); anything else in brackets is a word.  Text the timeline has no
  words for (flagged lines, words added to a line, a changed word that is also a word of a line
  the text has no mark for) makes every line that has one of its words stay, and the end of a
  line that could be the end of the line below it (a selection deleted from the middle of one line
  to the middle of the next) is flagged, so a line is never cut while some of its words are still
  in the text.  Words that overlap in time are never cut into a kept word (flag ``overlap``).
* **After an Apply that leaves the text as typed** (Live, or flags): :func:`carry_base` makes the
  artist's own lines, with the marks in the text, the base of the next diff.
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
    "CLOSE_LINE",
    "HISTORY_LIMIT",
    "LARGE_CUT",
    "MARK_RE",
    "PADDING_S",
    "PAUSE_LIMIT_S",
    "PROP",
    "STRIP_PROP",
    "Edit",
    "caption_text",
    "carry_base",
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
    "move_span",
    "norm",
    "only_listed",
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
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Sequence

EDIT_SCHEMA = "bfa_coworker.transcript_edit/1"
PROP = "bfacw_transcript"            # on the Text data-block: what it is a view of (see transcript_edit_ops)
STRIP_PROP = "bfacw_transcript_text"  # on a strip: the name of its transcript text

PADDING_S = 0.12        # pause kept next to the words around a cut, so no word is clipped
PAUSE_LIMIT_S = 0.6     # a cut takes the pause after its words only this far: a longer pause stays
HISTORY_LIMIT = 50      # edits kept in <transcript>.edits.json
LARGE_CUT = 0.5         # an Apply that cuts more than this share of the words or the time asks first
LINE_PAUSE_S = 0.8      # a pause this long starts a new line
PARAGRAPH_S = 2.0       # ... and this long a new paragraph (a blank line)
MAX_LINE_WORDS = 28     # a line longer than this breaks after a comma, or at 1.5 times this
SIMILAR = 0.6           # how alike a changed word must be to count as the same word (a typo)
BLOCK_SIMILAR = 0.5     # how alike (letters only) a block of n words written as m words must stay ("it is" -> "it's") ...
BLOCK_PREFIX = 2        # ... and how many letters it must start with the same ("going to" -> "gonna"); one word for one word
                        # needs SIMILAR; a block that is not clearly the same words is flagged, not retexted
CLOSE_LINE = 0.85       # how alike a line without a time mark must be to a line of the text (its words)

# A time mark at the start of a line has exactly the shape the renderer writes: "[0:05.2]",
# "[1:02:03.4]" (minutes:seconds.fraction).  Anything else in brackets ("[2.5]", "[1]", "[laughs]")
# is a word of the text.  A mark the artist changed or broke is a word too: the line is flagged
# (nothing is cut by it), never read as a mark.
MARK_RE = re.compile(r"^\s*\[(?:\d+:)?\d+:\d\d\.\d+\]\s?")
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
    t = unicodedata.normalize("NFC", str(token or "").strip())
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

def format_mark(seconds: float, digits: int = 1) -> str:
    """``[0:05.2]``, ``[12:30.0]``, ``[1:02:03.4]``; *digits* after the point (more when two lines
    would start at the same tenth: every line's mark is its own)."""
    digits = max(1, int(digits))
    scale = 10 ** digits
    units = int(round(max(0.0, float(seconds)) * scale))
    whole, frac = divmod(units, scale)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "[{:d}:{:02d}:{:02d}.{:0{w}d}]".format(hours, minutes, secs, frac, w=digits)
    return "[{:d}:{:02d}.{:0{w}d}]".format(minutes, secs, frac, w=digits)


def _unique_mark(seconds: float, used: set[str]) -> str:
    """The mark of a line: the time to the tenth, with more digits when another line has it."""
    for digits in (1, 2, 3):
        mark = format_mark(seconds, digits)
        if mark not in used:
            return mark
    stem, n = format_mark(seconds, 3)[:-1], 1
    while stem + str(n) + "]" in used:
        n += 1
    return stem + str(n) + "]"


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
    [{"line": 0-based line in the text, "frame", "t", "words": [indices], "mark": the line's
    time mark, "first": its first token, "count": how many tokens}]}``.  The marks are all different,
    so each tells its line (:func:`compute_edit` finds the lines by them).  A word with an empty
    retext (joined to the word before it) has no token but is in ``order``."""
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
    used: set[str] = set()

    def flush(blank: bool) -> None:
        nonlocal cur, cur_words, cur_info
        if cur_info is not None and cur:
            cur_info["line"] = len(out_lines)
            cur_info["words"] = cur_words
            cur_info["count"] = len(cur)
            mark = _unique_mark(cur_info["t"], used)
            used.add(mark)
            cur_info["mark"] = _mark_key(mark)
            lines.append(cur_info)
            out_lines.append(mark + " " + " ".join(cur))
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
            cur_info = {"frame": int(occ["frame"]), "t": float(occ.get("t", 0.0)), "first": len(tokens)}
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
    for n, line in enumerate(_split_lines(text)):
        body = MARK_RE.sub("", line, count=1)
        for tok in body.split():
            out.append((n, tok))
    return out


# -- the edit: last applied text -> text now ----------------------------------------------------
#
# The rule: nothing is cut by guessing.  A cut comes only from text the user took out, mapped
# with certainty to the words it came from; everything uncertain is flagged on its line and
# changes nothing.  The mapping goes line by line:
#
#   A. a line whose time mark is one base line's mark (and occurs once in the text) IS that line,
#      wherever it stands: this tells take 2 from take 1 and makes a moved line certain;
#   B. a line without a usable mark is that base line only when its words are the same (one
#      candidate, or one whose place between the neighbouring known lines is the only fitting
#      one), or nearly the same (one candidate, ratio >= CLOSE_LINE); else it is flagged;
#   C. inside a pair of lines only that line's words are compared: words taken out are cut,
#      words changed are retext (timing kept), a block of n words written as m words is one
#      retext of the block (the words stay), words added are flagged and the line is left as it
#      was;
#   D. a base line nothing in the text stands for, and no word of text that stands for no line (flagged
#      lines, words added) is also a word of it, is cut.
#
# A mark and the words can disagree (a mark typed onto another line, that other line deleted): when the
# words of a line with one line's mark are exactly the words of a base line the text has no mark for,
# which line it is can't be told: flagged "ambiguous", both base lines stay.

class Edit(dict):
    """What changed (a dict, JSON-ready).

    ``order``: the word indices in their new order (kept words; joined words after theirs);
    ``cut``: word indices cut; ``retext``: the full new ``{index: text}`` (relative to the
    transcript); ``retexted``: the indices whose text changed in this edit; ``moved``: whether
    the order of the kept words changed; ``flags``: ``[{"line", "text", "reason", "message"}]``
    (0-based lines of the new text); ``ops``: the edit list (cut / retext / move; a cut has
    ``seconds``, the media time it takes with its padding); ``seconds``: the total of the cuts;
    ``lines_cut``: how many whole lines of the last text go (Live waits longer before those);
    ``fraction``: the share of the words (and of the time) cut, the larger of the two;
    ``needs_confirm``: more than half goes (:data:`LARGE_CUT`): ask before applying;
    ``changed``: anything to apply; ``carry``: the lines of the text as typed, in the order of
    ``order``: ``[{"mark", "words", "line"}]`` (:func:`carry_base`)."""

    @property
    def flags(self) -> list[dict]:
        return self["flags"]


def _ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _one_letter(a: str, b: str) -> bool:
    """Two words of the same length (four letters or more) that differ in one letter: a typo, however short."""
    return len(a) == len(b) >= 4 and sum(1 for x, y in zip(a, b) if x != y) == 1


def _similar(a: str, b: str) -> bool:
    """*a* and *b* are one word with a typo."""
    return _ratio(a, b) >= SIMILAR or _one_letter(a, b)


def _list_ratio(a: Sequence[str], b: Sequence[str]) -> float:
    """How alike two token lists are (0..1); cheap when they share too little."""
    if a == b:
        return 1.0
    sa, sb = set(a), set(b)
    if not sa or not sb or len(sa & sb) < 0.6 * max(len(sa), len(sb)):
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


_DP_LIMIT = 2500        # token pairs compared at most when two changed stretches of a line are aligned


def _opcodes(a: Sequence[str], b: Sequence[str]) -> list[tuple[str, int, int, int, int]]:
    """Like ``SequenceMatcher.get_opcodes`` for two token lists of one line, but where several
    words are the same the alignment prefers the one that pairs the changed word with its own
    (a typo beside a repeated word is a typo, not an insert and a delete): the common start and end
    are kept, and inside the rest equal tokens count most, alike tokens (:data:`SIMILAR`) next."""
    n, m = len(a), len(b)
    p = 0
    while p < n and p < m and a[p] == b[p]:
        p += 1
    q = 0
    while q < n - p and q < m - p and a[n - 1 - q] == b[m - 1 - q]:
        q += 1
    ops: list[tuple[str, int, int, int, int]] = []
    if p:
        ops.append(("equal", 0, p, 0, p))
    x, y = a[p:n - q], b[p:m - q]
    if x or y:
        if not x:
            ops.append(("insert", p, p, p, p + len(y)))
        elif not y:
            ops.append(("delete", p, p + len(x), p, p))
        elif len(x) * len(y) > _DP_LIMIT:
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes():
                ops.append((tag, p + i1, p + i2, p + j1, p + j2))
        else:
            ops.extend(_dp_ops(x, y, p))
    if q:
        ops.append(("equal", n - q, n, m - q, m))
    return ops


def _dp_ops(x: Sequence[str], y: Sequence[str], offset: int) -> list[tuple[str, int, int, int, int]]:
    nx, ny = len(x), len(y)
    weight: dict[tuple[int, int], float] = {}
    for i in range(nx):
        for j in range(ny):
            if x[i] == y[j]:
                weight[(i, j)] = 3.0
            else:
                r = _ratio(x[i], y[j])
                if r >= SIMILAR or _one_letter(x[i], y[j]):
                    weight[(i, j)] = 1.0 + r * 1e-3
    best = [[0.0] * (ny + 1) for _ in range(nx + 1)]
    for i in range(nx - 1, -1, -1):
        for j in range(ny - 1, -1, -1):
            val = max(best[i + 1][j], best[i][j + 1])
            w = weight.get((i, j))
            if w is not None:
                val = max(val, best[i + 1][j + 1] + w)
            best[i][j] = val
    ops: list[tuple[str, int, int, int, int]] = []
    i = j = 0
    mark_i = mark_j = 0                                 # where the stretch not yet written starts

    def flush(i_end: int, j_end: int) -> None:
        if i_end > mark_i or j_end > mark_j:
            tag = "insert" if i_end == mark_i else "delete" if j_end == mark_j else "replace"
            ops.append((tag, offset + mark_i, offset + i_end, offset + mark_j, offset + j_end))

    while i < nx and j < ny:
        w = weight.get((i, j))
        if w is not None and abs(best[i][j] - (best[i + 1][j + 1] + w)) < 1e-9:
            if x[i] == y[j]:
                flush(i, j)
                if ops and ops[-1][0] == "equal" and ops[-1][2] == offset + i and ops[-1][4] == offset + j:
                    last = ops.pop()
                    ops.append(("equal", last[1], offset + i + 1, last[3], offset + j + 1))
                else:
                    ops.append(("equal", offset + i, offset + i + 1, offset + j, offset + j + 1))
                mark_i, mark_j = i + 1, j + 1
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    flush(nx, ny)
    return ops


def _alike(a: Sequence[str], b: Sequence[str]) -> bool:
    """The two lines share at least half of their words: one could be what is left of the other."""
    sa, sb = set(a), set(b)
    return bool(sa and sb) and len(sa & sb) >= 0.5 * max(len(sa), len(sb))


def _same_block(old: Sequence[str], new: Sequence[str]) -> bool:
    """Several words written as other words (or one) that are clearly the same words: "it is very" -> "it's"."""
    a, b = "".join(old), "".join(new)
    k = 0
    while k < min(len(a), len(b)) and a[k] == b[k]:
        k += 1
    return k >= min(BLOCK_PREFIX, len(a), len(b)) and _ratio(a, b) >= BLOCK_SIMILAR


def _shares(norms: Iterable[str], pool: set[str]) -> bool:
    """One of the words *norms* is a word of *pool* (or a typo of one): text that holds it may be where it went."""
    for t in norms:
        if t in pool or any(_similar(t, u) for u in pool):
            return True
    return False


def _matches(a: Sequence[str], b: Sequence[str]) -> int:
    """The number of tokens of *a* that stand for a token of *b*: the same, or alike (a typo)."""
    if not a or not b:
        return 0
    total = 0
    for tag, i1, i2, j1, j2 in _opcodes(a, b):
        if tag == "equal":
            total += i2 - i1
        elif tag == "replace" and i2 - i1 == j2 - j1 and all(_similar(a[i1 + k], b[j1 + k])
                                                              for k in range(i2 - i1)):
            total += i2 - i1
    return total


def _runs(indices: Iterable[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for k in sorted(set(indices)):
        if out and k == out[-1][-1] + 1:
            out[-1].append(k)
        else:
            out.append([k])
    return out


def _shiftable(a: Sequence[str], i1: int, i2: int) -> bool:
    """Taking out ``a[i1:i2]`` leaves the same text as taking out the same number of words one
    place earlier or later: the text can't say which words were meant."""
    return (i1 > 0 and a[i1 - 1] == a[i2 - 1]) or (i2 < len(a) and a[i1] == a[i2])


def _deleted_certain(a: Sequence[str], b: Sequence[str], gone: set[int]) -> bool:
    """The words ``a[i]`` for *gone* are the ones taken out of *a* to get *b*, and no other
    choice of words gives the same text: no best way of lining the two up keeps any of them."""
    n, m = len(a), len(b)
    if n * m > 4 * _DP_LIMIT:                           # a very long line: only a neighbour that repeats a word is checked
        return not any(_shiftable(a, r[0], r[-1] + 1) for r in _runs(gone))
    f = [[0] * (m + 1) for _ in range(n + 1)]
    g = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            f[i][j] = f[i - 1][j - 1] + 1 if a[i - 1] == b[j - 1] else max(f[i - 1][j], f[i][j - 1])
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            g[i][j] = g[i + 1][j + 1] + 1 if a[i] == b[j] else max(g[i + 1][j], g[i][j + 1])
    best = f[n][m]
    if n - len(gone) != best:
        return False
    return not any(a[i] == b[j] and f[i][j] + 1 + g[i + 1][j + 1] == best for i in gone for j in range(m))


_DELETE_LIMIT = 40_000    # token pairs at most for the exact check of words taken out of a line


def _delete_only(a: Sequence[str], ar: Sequence[str], al: Sequence[int], b: Sequence[str],
                 br: Sequence[str]) -> list[tuple[int, int]] | None:
    """Which words of a line (or the few lines of a group) were taken out to get *b*, when the text
    left is *a* (norms; *ar* the tokens as written, *al* the base line of each) minus some words and
    nothing else: the kept words as pairs ``(index in a, index in b)``, or None when the text can't
    say.  Where several words are the same, the written form tells ("Okay, okay" -> "okay": the
    second one stays), and a run of the very same token in one line ("I I think" -> "I think") is
    one choice whichever it is: the earliest of the run goes.  Any other way of taking out the
    same text (a repeated phrase, the same word in two places) is no certainty: None."""
    n, m = len(a), len(b)
    big = 1000
    neg = -10 ** 9
    f = [[neg] * (m + 1) for _ in range(n + 1)]
    g = [[neg] * (m + 1) for _ in range(n + 1)]
    f[0][0] = 0
    for i in range(n):
        for j in range(min(i, m) + 1):
            cur = f[i][j]
            if cur < 0 and not (i == 0 and j == 0):
                continue
            if f[i + 1][j] < cur:
                f[i + 1][j] = cur
            if j < m and a[i] == b[j]:
                v = cur + big + (1 if ar[i] == br[j] else 0)
                if f[i + 1][j + 1] < v:
                    f[i + 1][j + 1] = v
    g[n][m] = 0
    for i in range(n - 1, -1, -1):
        for j in range(m + 1):
            best = g[i + 1][j]
            if j < m and a[i] == b[j] and g[i + 1][j + 1] >= 0:
                best = max(best, g[i + 1][j + 1] + big + (1 if ar[i] == br[j] else 0))
            g[i][j] = best if best >= 0 else neg
    total = f[n][m]
    if total < 0 or g[0][0] != total:
        return None
    run_of = [0] * n                                    # the run of the very same token (as written) in one line
    for i in range(1, n):
        run_of[i] = run_of[i - 1] if (ar[i] == ar[i - 1] and al[i] == al[i - 1]) else run_of[i - 1] + 1
    base = n + 2
    reach: dict[tuple[int, int], set[int]] = {(0, 0): {0}}
    for i in range(n):
        for j in range(min(i, m) + 1):
            sigs = reach.get((i, j))
            if not sigs:
                continue
            if len(sigs) > 1:
                return None
            sig = next(iter(sigs))
            if f[i][j] + g[i][j] != total:
                continue
            if g[i + 1][j] >= 0 and f[i][j] + g[i + 1][j] == total:             # a[i] taken out
                reach.setdefault((i + 1, j), set()).add(sig + base ** run_of[i])
            if j < m and a[i] == b[j]:
                w = big + (1 if ar[i] == br[j] else 0)
                if g[i + 1][j + 1] >= 0 and f[i][j] + w + g[i + 1][j + 1] == total:     # a[i] stands for b[j]
                    reach.setdefault((i + 1, j + 1), set()).add(sig)
    if len(reach.get((n, m), ())) != 1:
        return None
    # one walk through the best lining-up, then the kept words of each run moved to the end of it
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < n:
        if j < m and a[i] == b[j] and g[i + 1][j + 1] >= 0 \
                and f[i][j] + big + (1 if ar[i] == br[j] else 0) + g[i + 1][j + 1] == total:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        else:
            i += 1
    out: list[tuple[int, int]] = []
    k = 0
    while k < len(pairs):
        r = run_of[pairs[k][0]]
        e = k
        while e + 1 < len(pairs) and run_of[pairs[e + 1][0]] == r:
            e += 1
        end = max(x for x in range(n) if run_of[x] == r)         # the last word of the run
        count = e - k + 1
        for q in range(count):
            out.append((end - count + 1 + q, pairs[k + q][1]))
        k = e + 1
    return out


def _pair_opcodes(pairs: Sequence[tuple[int, int]], n: int, m: int) -> list[tuple[str, int, int, int, int]]:
    """``get_opcodes``-style rows for a lining-up that takes words out only."""
    ops: list[tuple[str, int, int, int, int]] = []
    i = j = 0
    for pi, pj in pairs:
        if pi > i:
            ops.append(("delete", i, pi, j, j))
        if ops and ops[-1][0] == "equal" and ops[-1][2] == pi and ops[-1][4] == pj:
            last = ops.pop()
            ops.append(("equal", last[1], pi + 1, last[3], pj + 1))
        else:
            ops.append(("equal", pi, pi + 1, pj, pj + 1))
        i, j = pi + 1, pj + 1
    if i < n:
        ops.append(("delete", i, n, j, j))
    return ops


def _split_lines(text: str) -> list[str]:
    """The lines of a text as the Text Editor counts them (CRLF is one break)."""
    out = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if out and out[-1] == "":
        out.pop()
    return out


def _mark_key(prefix: str) -> str:
    """A time mark as it is compared: ``[0:05.2]`` without spaces."""
    return "".join(str(prefix).split()).replace(",", ".")


class _Base:
    """A line of the last applied text: its mark, its tokens ``(word, token)`` and its words."""

    __slots__ = ("idx", "mark", "tokens", "norms", "words")

    def __init__(self, idx: int, mark: str | None, tokens: list[tuple[int, str]]) -> None:
        self.idx, self.mark, self.tokens = idx, mark, tokens
        self.norms = [norm(t) for _w, t in tokens]
        self.words: list[int] = []


class _New:
    """A line of the text now: its mark (None when it has none), its tokens and their norms."""

    __slots__ = ("n", "key", "toks", "norms")

    def __init__(self, n: int, key: str | None, toks: list[str]) -> None:
        self.n, self.key, self.toks = n, key, toks
        self.norms = [norm(t) for t in toks]


def _read_lines(text: str) -> list[_New]:
    out: list[_New] = []
    for n, raw in enumerate(_split_lines(text)):
        m = MARK_RE.match(raw)
        key = None
        body = raw
        if m:
            key = _mark_key(m.group(0))
            body = raw[m.end():]
        toks = body.split()
        if not toks and key is None:
            continue                                    # a blank line
        out.append(_New(n, key, toks))
    return out


def _sentence_rows(base: Sequence[tuple[int, str]]) -> list[tuple[str | None, int, int]]:
    rows, first = [], 0
    for k, (_w, tok) in enumerate(base):
        if _sentence_end(tok) or k == len(base) - 1:
            rows.append((None, first, k + 1 - first))
            first = k + 1
    return rows


def _base_lines(base: Sequence[tuple[int, str]], order: Sequence[int], spec: Any) -> list[_Base]:
    """The lines of the last applied text.  *spec*: ``[{"mark", "first", "count"}]`` or
    ``[[mark, first, count]]`` (:func:`render`'s ``lines``, or what the Text keeps); without it
    (or when it doesn't fit the tokens) the tokens are cut into sentences with no marks."""
    rows: list[tuple[str | None, int, int]] = []
    if spec:
        try:
            for r in spec:
                if isinstance(r, dict):
                    mark, first, count = r.get("mark"), r.get("first"), r.get("count")
                else:
                    mark, first, count = r[0], r[1], r[2]
                rows.append((_mark_key(mark) if mark else None, int(first), int(count)))
        except (TypeError, ValueError, IndexError, KeyError):
            rows = []
        pos = 0
        for _mark, first, count in rows:
            if first != pos or count <= 0:
                rows = []
                break
            pos += count
        if rows and pos != len(base):
            rows = []
    if not rows:
        rows = _sentence_rows(base)
    lines = [_Base(j, mark, [base[k] for k in range(first, first + count)]) for j, (mark, first, count) in
             enumerate(rows)]
    where = {w: k for k, w in enumerate(order)}
    starts = []
    for b in lines:
        starts.append(where.get(b.tokens[0][0]) if b.tokens else None)
    for j, b in enumerate(lines):
        lo = 0 if j == 0 else starts[j]
        hi = len(order)
        for k in range(j + 1, len(lines)):
            if starts[k] is not None:
                hi = starts[k]
                break
        if lo is not None and hi >= lo:
            b.words = [int(w) for w in order[lo:hi]]
        else:
            b.words = []
        for w, _t in b.tokens:                          # words with tokens are always the line's own
            if w not in b.words:
                b.words.append(w)
    return lines


def _match_lines(bl: list[_Base], lines: list[_New]) -> tuple[list[dict], dict[int, list[int]]]:
    """Which new line stands for which base line: ``(groups, ambiguous)``.  A group is ``{"new":
    [line indices], "base": [base indices]}`` (one pair, or a line split in two / two lines joined
    in one); *ambiguous* maps a new line to the base lines it might be (and is neither)."""
    nb = len(bl)
    pair: dict[int, int] = {}                           # new line -> base line
    used: set[int] = set()

    # A. the time mark names the line
    by_mark: dict[str, _Base] = {}
    dup: set[str] = set()
    for b in bl:
        if b.mark:
            if b.mark in by_mark:
                dup.add(b.mark)
            by_mark[b.mark] = b
    counts: dict[str, int] = {}
    for nl in lines:
        if nl.key:
            counts[nl.key] = counts.get(nl.key, 0) + 1
    for li, nl in enumerate(lines):
        if nl.key and counts[nl.key] == 1 and nl.key in by_mark and nl.key not in dup:
            pair[li] = by_mark[nl.key].idx
            used.add(by_mark[nl.key].idx)

    # a mark and the words can disagree: a line that carries one line's mark but has exactly the words of
    # another line the text has no mark for (a mark typed onto another line, a line deleted): which one?
    ambiguous: dict[int, list[int]] = {}
    free_norms: dict[tuple, list[int]] = {}
    for b in bl:
        if b.idx not in used:
            free_norms.setdefault(tuple(b.norms), []).append(b.idx)
    for li in sorted(pair):
        nl, b = lines[li], bl[pair[li]]
        if nl.toks and nl.norms != b.norms and tuple(nl.norms) in free_norms:
            ambiguous[li] = [b.idx] + free_norms[tuple(nl.norms)]
    for li in ambiguous:
        used.discard(pair.pop(li))

    # B. the same words
    todo = [li for li, nl in enumerate(lines) if li not in pair and li not in ambiguous and nl.toks]
    by_norm: dict[tuple, list[int]] = {}
    for b in bl:
        if b.idx not in used:
            by_norm.setdefault(tuple(b.norms), []).append(b.idx)

    def bounds(li: int) -> tuple[int, int]:
        p, n = -1, nb
        for k in range(li - 1, -1, -1):
            if k in pair:
                p = pair[k]
                break
        for k in range(li + 1, len(lines)):
            if k in pair:
                n = pair[k]
                break
        return p, n

    def could_continue(li: int) -> bool:
        """The line may be the rest of the line above it (it was split with Enter), not a line of its own."""
        k = li - 1
        if k not in pair:
            return False
        a = bl[pair[k]].norms
        above = lines[k].norms
        gain = _matches(a, above + lines[li].norms) - _matches(a, above)
        return gain >= 1 and gain >= 0.5 * len(lines[li].norms)

    groups: dict[tuple, tuple[list[int], list[int]]] = {}
    while True:
        groups = {}
        for li in todo:
            if li in pair or li in ambiguous:
                continue
            t = tuple(lines[li].norms)
            cands = [c for c in by_norm.get(t, ()) if c not in used]
            if cands:
                groups.setdefault(t, ([], cands))[0].append(li)
        moved = False
        for _t, (us, cs) in groups.items():
            if len(us) == 1 and len(cs) == 1 and us[0] not in ambiguous:
                if could_continue(us[0]):
                    ambiguous[us[0]] = list(cs)
                    continue
                pair[us[0]] = cs[0]
                used.add(cs[0])
                moved = True
        if moved:
            continue
        for _t, (us, cs) in groups.items():
            fits = {}
            for li in us:
                p, n = bounds(li)
                fits[li] = [c for c in cs if p < c < n]
            claim: dict[int, list[int]] = {}
            for li, fit in fits.items():
                for c in fit:
                    claim.setdefault(c, []).append(li)
            for li, fit in fits.items():
                if len(fit) == 1 and len(claim[fit[0]]) == 1 and fit[0] not in used and li not in ambiguous:
                    if could_continue(li):
                        ambiguous[li] = list(cs)
                        continue
                    pair[li] = fit[0]
                    used.add(fit[0])
                    moved = True
        if not moved:
            break
    for _t, (us, cs) in groups.items():
        for li in us:
            ambiguous[li] = [c for c in cs if c not in used]

    # B'. nearly the same words (one line each way)
    rest = [li for li in todo if li not in pair and li not in ambiguous]
    free = [b for b in bl if b.idx not in used]
    if rest and free and len(rest) * len(free) <= 250_000:
        cand: dict[int, list[int]] = {}
        for li in rest:
            nl = lines[li]
            for b in free:
                if _list_ratio(b.norms, nl.norms) >= CLOSE_LINE:
                    cand.setdefault(li, []).append(b.idx)
        claimants: dict[int, list[int]] = {}
        for li, cs in cand.items():
            for c in cs:
                claimants.setdefault(c, []).append(li)
        for li, cs in cand.items():
            if len(cs) == 1 and len(claimants[cs[0]]) == 1 and not could_continue(li):
                pair[li] = cs[0]
                used.add(cs[0])
            else:
                ambiguous[li] = list(cs)

    # a line split in two (Enter) or two lines joined into one (Delete): the words that carry on
    taken_new = set(pair) | set(ambiguous)
    taken_base = set(used)
    out: list[dict] = []
    free_twins: dict[tuple, list[int]] = {}
    for b in bl:
        if b.idx not in used:
            free_twins.setdefault(tuple(b.norms), []).append(b.idx)
    for li in sorted(pair):
        g = {"new": [li], "base": [pair[li]]}
        for _step in range(8):
            nxt_new = g["new"][-1] + 1
            add_new = nxt_new if nxt_new < len(lines) and nxt_new not in taken_new and lines[nxt_new].toks else None
            nxt_base = g["base"][-1] + 1
            add_base = nxt_base if nxt_base < nb and nxt_base not in taken_base else None
            if add_base is not None and any(c != add_base and c not in taken_base and bl[c].norms == bl[add_base].norms
                                            for c in free_twins.get(tuple(bl[add_base].norms), ())):
                add_base = None                         # another line has the same words: which one was joined?
            if add_new is None and add_base is None:
                break
            a_norms = [x for bi in g["base"] for x in bl[bi].norms]
            b_norms = [x for lj in g["new"] for x in lines[lj].norms]
            have = _matches(a_norms, b_norms)
            choice = None
            for use_new, use_base in ((True, False), (False, True), (True, True)):
                if (use_new and add_new is None) or (use_base and add_base is None):
                    continue
                more_a = bl[add_base].norms if use_base else []
                more_b = lines[add_new].norms if use_new else []
                gain = _matches(a_norms + more_a, b_norms + more_b) - have
                if gain >= 1 and gain >= 0.5 * max(len(more_a), len(more_b)):
                    choice = (use_new, use_base)
                    break
            if choice is None:
                break
            if choice[0]:
                g["new"].append(add_new)
                taken_new.add(add_new)
            if choice[1]:
                g["base"].append(add_base)
                taken_base.add(add_base)
        out.append(g)
    return out, ambiguous


def _align_group(bts: Sequence[tuple[int, str, str, int]], nts: Sequence[tuple[int, int, str, str]]) -> dict[str, Any]:
    """One line (or a split / joined few) against its base: *bts* ``(word, token, norm, base line)``, *nts*
    ``(line, position in the line, token, norm)``.  ``emit``: ``[(base token, [new tokens])]`` in
    the new order; ``flags``: ``[(line, position, token, reason)]`` (then nothing here changes);
    ``pairs``: ``[(new token, base token)]`` for the tokens that stand for one."""
    a = [x[2] for x in bts]
    b = [x[3] for x in nts]
    emit: list[tuple[int, list[str]]] = []
    flags: list[tuple[int, int, str, str]] = []
    pairs: list[tuple[int, int]] = []
    deletes: list[tuple[int, int, int]] = []
    ops = _opcodes(a, b)
    certain = None
    if nts and all(op[0] in ("equal", "delete") for op in ops) and any(op[0] == "delete" for op in ops) \
            and len(a) * len(b) <= _DELETE_LIMIT:
        # words taken out and nothing else: where the written form or a run of the same token tells, no flag
        kept = _delete_only(a, [x[1] for x in bts], [x[3] for x in bts], b, [x[2] for x in nts])
        if kept is not None:
            ops = _pair_opcodes(kept, len(a), len(b))
            certain = True
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                emit.append((i1 + k, [nts[j1 + k][2]]))
                pairs.append((j1 + k, i1 + k))
        elif tag == "delete":
            deletes.append((i1, i2, j1))
        elif tag == "insert":
            for j in range(j1, j2):
                flags.append((nts[j][0], nts[j][1], nts[j][2], "no_audio"))
        else:
            old, new = a[i1:i2], b[j1:j2]
            if len(old) == len(new) and all(_similar(x, y) for x, y in zip(old, new)):
                for k in range(len(old)):
                    emit.append((i1 + k, [nts[j1 + k][2]]))
                    pairs.append((j1 + k, i1 + k))
            elif (len(old) > 1 or len(new) > 1) and _same_block(old, new):
                emit.append((i1, [nts[j][2] for j in range(j1, j2)]))
                pairs.append((j1, i1))
                for i in range(i1 + 1, i2):
                    emit.append((i, []))
            else:
                for j in range(j1, j2):
                    flags.append((nts[j][0], nts[j][1], nts[j][2], "no_match"))
    gone = {i for i1, i2, _j in deletes for i in range(i1, i2)}
    if nts and gone and not certain and not _deleted_certain(a, b, gone):
        n, k, tok, _nm = nts[min(deletes[0][2], len(nts) - 1)]
        flags.append((n, k, tok, "ambiguous"))
    own = {j: i for j, i in pairs}                      # which base token each new token stands for (a block: its first)
    for tag, i1, i2, j1, j2 in ops:
        if tag == "replace" and j1 in own:
            for j in range(j1 + 1, j2):
                own.setdefault(j, i1)
    return {"emit": emit, "flags": flags, "pairs": pairs, "deletes": deletes, "own": own}


def _group_tokens(bl: list[_Base], lines: list[_New], g: dict) -> tuple[list, list]:
    bts = [(w, t, bl[bi].norms[k], bi) for bi in g["base"] for k, (w, t) in enumerate(bl[bi].tokens)]
    nts = [(lines[li].n, k, lines[li].toks[k], lines[li].norms[k]) for li in g["new"]
           for k in range(len(lines[li].toks))]
    return bts, nts


def _cut_extent(words: Sequence[Any], prev: int | None, first: int, last: int, nxt: int | None,
                padding: float = PADDING_S, limit: float = PAUSE_LIMIT_S,
                before_end: float | None = None) -> tuple[float, float]:
    """Media seconds ``(start, end)`` a cut of the words *first* .. *last* takes: the words with
    *padding* before the first (at most half the pause there) and, after the last, the pause up
    to the next kept word less *padding* but never more than *limit* seconds.  *prev* / *nxt*: the
    words kept around the cut (None at a strip's edge: then the cut ends at the word plus the
    padding, not at the edge); *before_end*: the latest end of any word kept before the cut.  The
    cut never reaches into a kept word: it starts after the kept words before it end and stops where
    the next kept word starts, so words that overlap in time are not clipped (``end <= start``, or a
    start after the first word's start: the cut words lie inside a kept word's time, nothing of them
    can be cut)."""
    ws, we = _start(words[first]), _end(words[last])
    if prev is None:
        s = ws - padding
    else:
        pe = _end(words[prev])
        if before_end is not None:
            pe = max(pe, before_end)
        s = max(pe, ws - min(padding, max(0.0, ws - pe) / 2.0))
    if nxt is None:
        e = we + padding
    else:
        ns = _start(words[nxt])
        e = min(max(we, ns - min(padding, max(0.0, ns - we) / 2.0)), ns)
    return s, min(e, we + limit)


def compute_edit(base_tokens: Sequence[Sequence[Any]], base_order: Sequence[int], text: str,
                 words: Sequence[Any], retext: dict | None = None, base_lines: Any = None,
                 timeline_s: float | None = None) -> Edit:
    """The edit from the last applied text to *text*.

    *base_tokens*: ``[[word index, token]]`` of the last applied text (:func:`render`);
    *base_order*: its word order (with the joined words); *retext*: its retexts; *base_lines*:
    its lines with their time marks (``render(...)["lines"]``: the marks tell the lines apart);
    *timeline_s*: the seconds the source plays on the timeline (the guard against a cut of
    more than half; estimated from the words when not given).

    Words whose time lies inside a kept word's time can't be cut apart from it (a cut never reaches into a
    kept word): their line is flagged ``overlap`` and nothing of it changes."""
    blocked: frozenset[int] = frozenset()
    edit = None
    for _round in range(4):
        edit, dead = _compute_edit(base_tokens, base_order, text, words, retext, base_lines, timeline_s, blocked)
        if not dead or dead <= blocked:
            break
        blocked = blocked | dead
    return edit


def _compute_edit(base_tokens: Sequence[Sequence[Any]], base_order: Sequence[int], text: str,
                  words: Sequence[Any], retext: dict | None, base_lines: Any, timeline_s: float | None,
                  blocked: frozenset[int]) -> tuple[Edit, frozenset[int]]:
    """:func:`compute_edit` once; *blocked*: base lines that stay as they are, flagged ``overlap``; the second
    answer: the base lines whose cut would be empty (:func:`_cut_extent`)."""
    base = [(int(i), str(t)) for i, t in base_tokens]
    order0 = [int(w) for w in base_order]
    bl = _base_lines(base, order0, base_lines)
    lines = _read_lines(text)
    groups, ambiguous = _match_lines(bl, lines)
    nb = len(bl)

    flag_items: list[tuple[int, int, str, str]] = []
    in_group: set[int] = set()
    span_of: dict[int, tuple[int, int]] = {}
    stay: set[int] = set()
    results = []
    for g in groups:
        bts, nts = _group_tokens(bl, lines, g)
        res = _align_group(bts, nts)
        in_group.update(g["base"])
        for li in g["new"]:
            span_of[li] = (min(g["base"]), max(g["base"]))
        results.append((g, bts, nts, res))

    # new lines nothing stands for: flagged; the base lines they might be the rest of stay
    prev_b, next_b = [-1] * len(lines), [nb] * len(lines)
    run = -1
    for li in range(len(lines)):
        prev_b[li] = run
        if li in span_of:
            run = span_of[li][1]
    run = nb
    for li in range(len(lines) - 1, -1, -1):
        next_b[li] = run
        if li in span_of:
            run = span_of[li][0]
    free = [b for b in range(nb) if b not in in_group]
    free_set = set(free)
    for g, _bts, nts, res in results:
        # a selection from the middle of one line to the middle of a later one, deleted, leaves the first part of
        # the line and the end of a line below it: the end of this line may be that other line's end, not its own
        if res["flags"] or not res["deletes"] or not nts:
            continue
        kept = [x[3] for x in nts]
        k = max(g["base"]) + 1
        while k in free_set:
            tail = bl[k].norms
            if any(kept[-s:] == tail[-s:] for s in range(1, min(len(kept), len(tail)) + 1)):
                n, p, tok, _nm = nts[-1]
                res["flags"].append((n, p, tok, "ambiguous"))
                break
            k += 1
    free_words = {t for b in free for t in bl[b].norms}
    for _g, bts, nts, res in results:
        # a word changed into another that a line the text has no mark for holds: a typo, or that line's word?
        if res["flags"]:
            continue
        for j, i in sorted(res["own"].items()):
            if nts[j][3] != bts[i][2] and nts[j][3] in free_words:
                res["flags"].append((nts[j][0], nts[j][1], nts[j][2], "no_match"))
                break
    # a new line nothing stands for right behind a line whose tail was deleted (or right before one whose beginning was):
    # the Enter may have split the line, and the words cut off are then the new line's, so nothing of that line is cut
    orphan = [li for li, nl in enumerate(lines) if li not in span_of and nl.toks]
    if orphan:
        by_edge: dict[tuple[str, int], tuple] = {}
        for g, bts, nts, res in results:
            if res["flags"] or not nts or not g["new"]:
                continue
            if any(i2 == len(bts) for _i1, i2, _j1 in res["deletes"]):
                by_edge[("tail", g["new"][-1])] = (res, nts[-1])
            if any(i1 == 0 for i1, _i2, _j1 in res["deletes"]):
                by_edge[("head", g["new"][0])] = (res, nts[0])
        for li in orphan:
            for edge, at in (("tail", li - 1), ("head", li + 1)):
                hit = by_edge.get((edge, at))
                if hit and not hit[0]["flags"]:
                    n, k, tok, _nm = hit[1]
                    hit[0]["flags"].append((n, k, tok, "no_match"))
    protect: set[int] = set()
    pool: set[str] = set()                            # text that is not on the timeline: words may have been moved there
    for li, nl in enumerate(lines):
        if li in span_of or not nl.toks:
            continue
        pool.update(nl.norms)
        if li in ambiguous:
            reason = "ambiguous"
            protect.update(ambiguous[li])
        else:
            lo, hi = sorted((prev_b[li], next_b[li]))
            near = set(free[bisect.bisect_right(free, lo):bisect.bisect_left(free, hi)])
            if len(lines) * len(free) <= 250_000:
                near.update(b for b in free if _alike(bl[b].norms, nl.norms))
            else:
                near.update(free)
            reason = "no_match" if near else "no_audio"
            protect.update(near)
        for k, tok in enumerate(nl.toks):
            flag_items.append((nl.n, k, tok, reason))
    for _g, _bts, nts, res in results:
        if res["flags"]:                                # a line the text can't place: its words may be moved ones
            pool.update(x[3] for x in nts)
    # every word the text holds that no line of the timeline certainly stands for: a base line that has it stays
    # (computed last, after every flag above), and so does a deletion of it elsewhere
    if pool:
        protect.update(b for b in free if _shares(bl[b].norms, pool))
        for g, bts, nts, res in results:                # words taken out of a line may have been moved into text nobody said
            if res["flags"]:
                continue
            for i1, i2, j1 in res["deletes"]:
                if _shares([bts[i][2] for i in range(i1, i2)], pool):
                    if nts:
                        n, k, tok, _nm = nts[min(j1, len(nts) - 1)]
                    else:
                        n, k, tok = lines[g["new"][0]].n, 0, bts[i1][1]
                    res["flags"].append((n, k, tok, "no_match"))
                    break
    for bi in sorted(blocked):                          # words inside a kept word's time: nothing of their line is cut
        if bi in in_group:
            for g, bts, nts, res in results:
                if bi in g["base"] and not res["flags"]:
                    n, k, tok = (nts[0][0], 0, nts[0][2]) if nts else (lines[g["new"][0]].n, 0, bts[0][1])
                    res["flags"].append((n, k, tok, "overlap"))
        else:
            protect.add(bi)
            anchor = next((li for li in range(len(lines)) if li in span_of and span_of[li][0] > bi), None)
            if anchor is None:
                anchor = max((li for li in span_of), default=None)
            n = lines[anchor].n if anchor is not None else 0
            flag_items.append((n, 0, lines[anchor].toks[0] if anchor is not None and lines[anchor].toks else "",
                               "overlap"))
    items: list[dict[str, Any]] = []
    where: dict[int, dict] = {}
    for g, bts, _nts, res in results:
        if res["flags"]:
            flag_items.extend(res["flags"])
            stay.update(g["base"])
            continue
        item = {"bases": list(g["base"]), "words": [(bts[i][0], toks) for i, toks in res["emit"]], "carry": []}
        off = 0
        for li in g["new"]:                             # the lines as typed, each with the words its tokens stand for
            seen_words: list[int] = []
            for j in range(off, off + len(lines[li].toks)):
                i = res["own"].get(j)
                if i is not None and bts[i][0] not in seen_words:
                    seen_words.append(bts[i][0])
            off += len(lines[li].toks)
            if seen_words:
                item["carry"].append({"mark": lines[li].key, "words": seen_words, "line": lines[li].n})
        items.append(item)
        for bi in g["base"]:
            where[bi] = item
    protect = {b for b in protect if b not in in_group}
    stay.update(protect)
    typed_line = {bi: lines[g["new"][0]].n for g, _bts, _nts, _res in results for bi in g["base"] if g["new"]}
    for bi in sorted(stay):
        words_of = list(dict.fromkeys(w for w, _t in bl[bi].tokens))
        item = {"bases": [bi], "words": [(w, [t]) for w, t in bl[bi].tokens],
                "carry": [{"mark": bl[bi].mark, "words": words_of, "line": typed_line.get(bi)}]}
        q = bi - 1
        while q >= 0 and q not in where:
            q -= 1
        items.insert(items.index(where[q]) + 1 if q >= 0 else 0, item)
        where[bi] = item

    # the new order
    tokens_of: dict[int, list[str]] = {}
    new_order: list[int] = []
    seen: set[int] = set()
    for item in items:
        for w, toks in item["words"]:
            if w not in seen:
                seen.add(w)
                new_order.append(w)
            tokens_of.setdefault(w, []).extend(toks)
    # Words without tokens in the last text (joined to the word before them) follow that word.
    with_tokens = {w for w, _t in base}
    final: list[int] = []
    pending_owner: dict[int, list[int]] = {}
    owner = None
    for w in order0:
        if w in with_tokens:
            owner = w
        elif owner is not None:
            pending_owner.setdefault(owner, []).append(w)
    for w in new_order:
        final.append(w)
        for extra in pending_owner.get(w, ()):
            if extra not in seen:
                seen.add(extra)
                final.append(extra)
                tokens_of.setdefault(extra, [])
    seen_final = set(final)
    cut = [w for w in order0 if w not in seen_final]
    lines_cut = sum(1 for b in bl if b.words and not any(w in seen_final for w in b.words))

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
    kept_base = [w for w in order0 if w in seen_final]
    moved = kept_base != final

    flags = _flags(flag_items)
    ops, seconds, dead = _ops(order0, cut, final, retexted, words, retext, new_retext, moved)
    dead_lines = frozenset(b.idx for b in bl if any(w in dead for w in b.words)) if dead else frozenset()
    total_s = float(timeline_s) if timeline_s else _timeline_seconds(words, order0)
    frac_words = len(cut) / len(order0) if order0 else 0.0
    frac_secs = min(1.0, seconds / total_s) if total_s > 0 else 0.0
    carry = [c for item in items for c in item["carry"]]
    edit = Edit(order=final, cut=cut, retext=new_retext, retexted=retexted, moved=moved, flags=flags, ops=ops,
                carry=carry,
                changed=bool(cut or retexted or moved), seconds=round(seconds, 2), lines_cut=lines_cut, fraction=round(
                    max(frac_words, frac_secs), 3),
                needs_confirm=bool(cut) and (frac_words > LARGE_CUT or frac_secs > LARGE_CUT))
    return edit, dead_lines


def only_listed(edit: dict, wanted: Iterable[int], on: Iterable[int]) -> str:
    """"" when *edit* does exactly one thing, cut the words *wanted*; else a sentence for the artist saying what
    else it would do.  *on*: the words on the timeline (a word that is not there is nothing to cut).  Remove
    Filler Words checks its plan with this before it goes ahead (it asks for no further confirmation)."""
    on = set(on)
    wanted = set(wanted) & on
    cut = set(edit["cut"]) & on
    problems = []
    if cut - wanted:
        problems.append("cut {:d} other word{:s}".format(len(cut - wanted), "" if len(cut - wanted) == 1 else "s"))
    if wanted - cut and not edit["flags"]:
        problems.append("leave {:d} of the filler words in".format(len(wanted - cut)))
    if edit["retexted"]:
        problems.append("change {:d} word{:s}".format(len(edit["retexted"]), "" if len(edit["retexted"]) == 1 else "s"))
    if edit["moved"]:
        problems.append("move words")
    if edit["flags"]:
        problems.append("flag {:d} line{:s}".format(len(edit["flags"]), "" if len(edit["flags"]) == 1 else "s"))
    return "; ".join(problems)


def carry_base(edit: dict, view: dict) -> dict | None:
    """The last-applied state for an Apply that leaves the artist's text as typed (Live, or an Apply
    with flags): the view of the timeline *view* (``render``'s answer), but its lines are the artist's
    own lines, with the time marks they carry in the text, each holding the words its text stood for
    (a line the edit left flagged stays the base line it was pending against, mark and all).  The next
    :func:`compute_edit` then sees marks in the text and in the base that agree.  ``{"tokens",
    "order", "lines": [[mark, first, count]], "text_lines": [line of the text or None]}``; None when the
    lines don't fit the view's tokens (the caller then takes the view's own lines)."""
    carry = edit.get("carry")
    if not carry:
        return None
    owner: dict[int, int] = {}
    for k, c in enumerate(carry):
        for w in c["words"]:
            if w in owner:
                return None
            owner[w] = k
    rows: list[list[int]] = []
    done: set[int] = set()
    for first, (w, _t) in enumerate(view["tokens"]):
        k = owner.get(int(w))
        if k is None:
            return None
        if rows and rows[-1][0] == k:
            rows[-1][2] += 1
            continue
        if k in done:
            return None
        done.add(k)
        rows.append([k, first, 1])
    return {"tokens": view["tokens"], "order": view["order"],
            "lines": [[carry[k]["mark"], first, count] for k, first, count in rows],
            "text_lines": [carry[k]["line"] for k, _first, _count in rows]}


def _timeline_seconds(words: Sequence[Any], order: Sequence[int]) -> float:
    """About how long the words in *order* play (the pauses between them capped at two seconds)."""
    total, prev = 0.0, None
    for w in order:
        if not 0 <= w < len(words):
            continue
        if prev is not None:
            total += min(2.0, max(0.0, _start(words[w]) - _start(words[prev])))
        prev = w
    if prev is not None:
        total += max(0.0, _end(words[prev]) - _start(words[prev]))
    return total


_REASONS = {
    "no_audio": "no audio behind this text: nothing is added to the video",
    "no_match": "this no longer matches what is said: nothing here is cut or changed",
    "ambiguous": "can't tell which of the same words or lines you meant: nothing here is cut or changed",
    "overlap": "these words overlap the words next to them in time, so they can't be cut apart: nothing here is cut or changed",
}


def _flags(items: Sequence[tuple[int, int, str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line, pos, tok, reason in sorted(set(items)):
        if out and out[-1]["line"] == line and out[-1]["reason"] == reason and out[-1]["_last"] == pos - 1:
            out[-1]["text"] += " " + tok
            out[-1]["_last"] = pos
            continue
        out.append({"line": line, "text": tok, "reason": reason, "message": _REASONS[reason], "_last": pos})
    for f in out:
        del f["_last"]
    return out


def _ops(base_order, cut, final, retexted, words, old_retext, new_retext, moved)         -> tuple[list[dict[str, Any]], float, list[int]]:
    ops: list[dict[str, Any]] = []
    seconds = 0.0
    dead: list[int] = []                                # words of a cut that has no frames (inside a kept word's time)
    pos = {w: k for k, w in enumerate(base_order)}
    cut_set = set(cut)
    latest: list[float | None] = []                     # latest end of the kept words before each place
    top: float | None = None
    # after an applied move the timeline is out of source order: a word that starts earlier in the source than the one
    # before it on the timeline begins another strip (another stretch of the source), so the checks start again there
    brk = [False] * len(base_order)
    for k in range(1, len(base_order)):
        a, b = base_order[k - 1], base_order[k]
        brk[k] = 0 <= a < len(words) and 0 <= b < len(words) and _start(words[b]) < _start(words[a])
    for k, w in enumerate(base_order):
        if brk[k]:
            top = None
        latest.append(top)
        if w not in cut_set and 0 <= w < len(words):
            top = _end(words[w]) if top is None else max(top, _end(words[w]))
    runs: list[list[int]] = []
    for run in _runs(pos[w] for w in cut if w in pos):
        for k in run:
            if runs and runs[-1][-1] == k - 1 and not brk[k]:
                runs[-1].append(k)
            else:
                runs.append([k])
    for run in runs:
        ws = [base_order[k] for k in run]
        prev = base_order[run[0] - 1] if run[0] > 0 and not brk[run[0]] else None
        nxt = base_order[run[-1] + 1] if run[-1] + 1 < len(base_order) and not brk[run[-1] + 1] else None
        secs = 0.0
        if all(0 <= w < len(words) for w in ws) and (prev is None or 0 <= prev < len(words)) \
                and (nxt is None or 0 <= nxt < len(words)):
            s, e = _cut_extent(words, prev, ws[0], ws[-1], nxt, before_end=latest[run[0]])
            secs = max(0.0, e - s)
            if e <= s or s > _start(words[ws[0]]):     # no frames, or the first word starts before the cut does
                dead.extend(ws)
        seconds += secs
        ops.append({"op": "cut", "words": [ws[0], ws[-1]], "count": len(ws), "seconds": round(secs, 2),
                    "text": " ".join(t for t in (display(w, words, old_retext) for w in ws) if t)})
    for w in retexted:
        ops.append({"op": "retext", "word": w, "from": display(w, words, old_retext),
                    "to": display(w, words, new_retext)})
    if moved:
        kept = [w for w in base_order if w in set(final)]
        for block, after in plan_moves(kept, final):
            ops.append({"op": "move", "words": [block[0], block[-1]], "count": len(block), "after": after})
    return ops, seconds, dead


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


def cut_ranges(occurrences: Sequence[dict], cut: Iterable[int], words: Sequence[Any], pieces: Iterable[dict],
               fps: float, padding: float = PADDING_S, pause_limit: float = PAUSE_LIMIT_S) -> list[tuple[int, int]]:
    """Timeline frame ranges ``(first, end)`` (end exclusive) that remove the *cut* words.

    In each strip, a run of cut words goes with the pause around it: *padding* (or half the pause,
    when shorter) before the first word and, after the last, the pause up to the next word kept
    less *padding* but never more than *pause_limit* seconds, so a long silence after a sentence
    stays.  A run at the start or the end of a strip is padded the same way (the cut doesn't run
    to the strip's edge).  Merged and sorted."""
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
        top: float | None = None                        # the latest end of the kept words before the cut
        while k < len(occs):
            if occs[k]["i"] not in cut:
                end = _end(words[occs[k]["i"]])
                top = end if top is None else max(top, end)
                k += 1
                continue
            first = k
            while k + 1 < len(occs) and occs[k + 1]["i"] in cut:
                k += 1
            prev = occs[first - 1]["i"] if first > 0 else None
            nxt = occs[k + 1]["i"] if k + 1 < len(occs) else None
            s, e = _cut_extent(words, prev, occs[first]["i"], occs[k]["i"], nxt, padding, pause_limit, top)
            f0 = int(round(cs + (s - off) * fps))
            f1 = int(round(cs + (e - off) * fps))
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

def move_span(occurrences: Sequence[dict], target: Sequence[int], words: Sequence[Any], pieces: Iterable[dict],
              fps: float) -> tuple[int, int] | None:
    """The timeline frames ``(first, end)`` a reordering to *target* may split and move: from the
    middle of the pause before the first word out of place to the middle of the pause after the
    last (the stretches :func:`move_ranges` cuts at), a little wider.  None when nothing moves.
    Asked before anything changes, so a locked strip in the way refuses the whole Apply."""
    fps = float(fps) or 24.0
    info = {p["name"]: p for p in pieces}
    occs = sorted(occurrences, key=lambda o: o["frame"])
    current = [o["i"] for o in occs]
    on = set(current)
    want = [w for w in target if w in on]
    kept = [w for w in current if w in set(want)]
    differs = [k for k, w in enumerate(kept) if want[k] != w]
    if not differs:
        return None
    where = {o["i"]: o for o in occs}
    lo = hi = None
    for k in (differs[0], differs[-1]):
        for w in (kept[k], want[k]):
            o = where[w]
            p = info.get(o["piece"])
            if p is None:
                continue
            mine = [x for x in occs if x["piece"] == o["piece"]]
            n = next(n for n, x in enumerate(mine) if x["i"] == w)
            a, b = _boundary(words, mine, n, p, fps), _boundary(words, mine, n + 1, p, fps)
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    if lo is None or hi is None:
        return None
    return int(lo) - 2, int(hi) + 2


def line_words(base_tokens: Sequence[Sequence[Any]], text: str, base_lines: Any = None,
               base_order: Sequence[int] | None = None) -> dict[int, list[int]]:
    """``{0-based line: [word indices]}`` of *text* as it is now (edited or not): each line's
    tokens matched to the last applied text's line they stand for (:func:`compute_edit`'s rules).
    A line of new text, or one that can't be told, has no words."""
    base = [(int(i), str(t)) for i, t in base_tokens]
    order = list(base_order) if base_order is not None else [w for w, _t in base]
    bl = _base_lines(base, [int(w) for w in order], base_lines)
    lines = _read_lines(text)
    groups, _ambiguous = _match_lines(bl, lines)
    out: dict[int, list[int]] = {}
    for g in groups:
        bts, nts = _group_tokens(bl, lines, g)
        for j, i in _align_group(bts, nts)["pairs"]:
            ws = out.setdefault(nts[j][0], [])
            if bts[i][0] not in ws:
                ws.append(bts[i][0])
    return out


def line_frames(base_tokens: Sequence[Sequence[Any]], text: str, frames: dict, base_lines: Any = None) -> dict[int, int]:
    """``{0-based line: timeline frame of its first word}`` for *text* as it is now; *frames*:
    ``{word index (int or str): frame}``.  A line whose words aren't known uses its time mark
    (not here: the caller has the scene's frame rate)."""
    out = {}
    for line, ws in line_words(base_tokens, text, base_lines).items():
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
    rec["history"] = history[-HISTORY_LIMIT:]
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
