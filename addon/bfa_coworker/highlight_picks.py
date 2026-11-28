# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Highlights H4 (#614): the picks list, the part that needs no Blender.

* **One store**: the picks of an edit live on the scene as one custom property holding JSON
  (:func:`dumps`, :func:`loads`).  Per pick: the media file's key and the first and last word position
  in its transcript (the identity: a cut renumbers every later sentence, the words do not move), the
  frames it had when found, a signature of the sentences at that moment, title, hook, reason, scores
  and state.  :func:`loads` takes any text and gives back only what is a well-formed pick.
* **Placing again** (:func:`place_all`): after the long edit was cut, each pick is looked up by its
  words in the sentences as they play now.  Still whole and in order: new frames (state ``ok``).
  Partly cut away, or in the wrong order: ``changed`` (check it).  Gone: ``gone``.
* **Markers**: the names of the timeline markers of a pick (:func:`marker_names`) and the plan that makes
  the scene's markers match the picks (:func:`marker_plan`).  Only markers this store made are ever removed.
* **Preview range**: :func:`preview_range` gives the frames Preview plays (the pick, never outside the edit).

The model never sees any of this: H3 (``highlights.find_highlights``) hands over picks as sentence ranges,
:func:`from_found` turns them into stored picks.  Importing this module needs no ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "MARKERS_PREFIX",
    "MAX_PICKS",
    "PROP",
    "STATES",
    "State",
    "clock",
    "dumps",
    "empty",
    "engine_line",
    "from_found",
    "loads",
    "marker_names",
    "marker_plan",
    "place",
    "place_all",
    "preview_range",
    "state_text",
    "summary",
)

import json
import re
import secrets
from typing import Any, Sequence

from . import scene_reader as sr

PROP = "bfacw_highlights"          # the scene's custom property
VERSION = 1
MAX_PICKS = 30
TITLE_CHARS = 60
HOOK_CHARS = 110
REASON_CHARS = 220
MAX_STORE_CHARS = 60000            # a custom property that holds more than this is dropped, not parsed
MARKERS_PREFIX = "HL"
STATES = ("ok", "changed", "gone")
SCORE_KEYS = 12


class State:
    OK = "ok"
    CHANGED = "changed"
    GONE = "gone"


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _int(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _word(value: Any) -> dict[str, Any] | None:
    """``{"media", "i"}`` or ``None``."""
    if not isinstance(value, dict):
        return None
    media, i = value.get("media"), _int(value.get("i"))
    if not isinstance(media, str) or not media or i is None or i < 0:
        return None
    return {"media": media, "i": i}


def _scores(value: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(value, dict):
        for k, v in list(value.items())[:SCORE_KEYS]:
            if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k[:24]] = round(float(v), 2)
    return out


def _pick(raw: Any) -> dict[str, Any] | None:
    """The well-formed stored pick made of *raw*, or ``None``."""
    if not isinstance(raw, dict):
        return None
    first, last = _word(raw.get("first_word")), _word(raw.get("last_word"))
    start, end = _int(raw.get("start")), _int(raw.get("end"))
    if first is None or last is None or start is None or end is None or end <= start:
        return None
    state = raw.get("state") if raw.get("state") in STATES else State.OK
    total = raw.get("total")
    return {
        "id": _text(raw.get("id"), 16) or secrets.token_hex(3),
        "first_word": first, "last_word": last, "start": start, "end": end,
        "seconds": round(float(raw["seconds"]), 2) if isinstance(raw.get("seconds"), (int, float))
        and not isinstance(raw.get("seconds"), bool) else 0.0,
        "sig": _text(raw.get("sig"), 40),
        "title": _text(raw.get("title"), TITLE_CHARS) or _text(raw.get("label"), TITLE_CHARS) or "Untitled",
        "hook": _text(raw.get("hook"), HOOK_CHARS), "reason": _text(raw.get("reason"), REASON_CHARS),
        "scores": _scores(raw.get("scores")),
        "total": round(float(total), 1) if isinstance(total, (int, float)) and not isinstance(total, bool) else 0.0,
        "kind": _text(raw.get("kind"), 16), "state": state,
    }


def empty() -> dict[str, Any]:
    """A store with no picks: ``picks``, the names of the markers made for them, the markers switch,
    and what the last search was (``found``)."""
    return {"v": VERSION, "picks": [], "markers": [], "markers_on": False, "found": {}}


def loads(text: Any) -> dict[str, Any]:
    """The store in *text* (what ``dumps`` wrote).  Anything else, an old version or damage gives an
    empty store; a pick that is not well-formed is left out."""
    out = empty()
    if not isinstance(text, str) or not text or len(text) > MAX_STORE_CHARS:
        return out
    try:
        data = json.loads(text)
    except ValueError:
        return out
    if not isinstance(data, dict) or data.get("v") != VERSION:
        return out
    seen: set[str] = set()
    for raw in data.get("picks") or []:
        pick = _pick(raw)
        if pick is None or pick["id"] in seen:
            continue
        seen.add(pick["id"])
        out["picks"].append(pick)
        if len(out["picks"]) >= MAX_PICKS:
            break
    names = data.get("markers")
    out["markers"] = [n[:80] for n in names if isinstance(n, str)][:2 * MAX_PICKS] if isinstance(names, list) else []
    out["markers_on"] = bool(data.get("markers_on")) is True
    found = data.get("found")
    if isinstance(found, dict):
        out["found"] = {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, (str, int, float, bool))}
    return out


def dumps(store: dict[str, Any]) -> str:
    """The text to keep on the scene."""
    body = dict(store, v=VERSION, picks=[p for p in (_pick(x) for x in store.get("picks", ())) if p][:MAX_PICKS])
    return json.dumps(body, separators=(",", ":"), ensure_ascii=True)


def from_found(found: Sequence[dict], sentences: Sequence[dict]) -> list[dict[str, Any]]:
    """Stored picks for the picks ``highlights.find_highlights`` returned (sentence ranges): the words
    are the identity, the frames and the signature of *sentences* are what they had when found."""
    sig = sr.version(sentences)
    by_n = {s["n"]: s for s in sentences}
    out: list[dict[str, Any]] = []
    for p in found:
        a, b = by_n.get(p.get("first")), by_n.get(p.get("last"))
        if a is None or b is None:
            continue
        pick = _pick(dict(p, first_word=p.get("first_word") or a["first"], last_word=p.get("last_word") or b["last"],
                          start=a["start"], end=b["end"], sig=sig, state=State.OK, id=""))
        if pick is not None:
            out.append(pick)
    return out


# -- placing again after a cut -----------------------------------------------------------------------

def _covers(s: dict, word: dict) -> bool:
    """The sentence *s* holds the transcript word *word* (one voice: its first and last word are in one file)."""
    f, l = s["first"], s["last"]
    return f["media"] == word["media"] == l["media"] and f["i"] <= word["i"] <= l["i"]


def _whole(chunk: Sequence[dict]) -> bool:
    """No words missing between the sentences of *chunk*: inside one (a sentence that holds fewer words
    than its span) or between two neighbours of the same file."""
    expect: dict[str, int] = {}
    for s in chunk:
        f, l = s["first"], s["last"]
        if f["media"] == l["media"]:
            if s["words"] < l["i"] - f["i"] + 1:
                return False
            nxt = expect.get(f["media"])
            if nxt is not None and f["i"] != nxt:
                return False
            expect[f["media"]] = l["i"] + 1
        else:
            expect.pop(f["media"], None)
            expect.pop(l["media"], None)
    return True


def place(pick: dict[str, Any], sentences: Sequence[dict]) -> tuple[str, int, int]:
    """``(state, start, end)`` of *pick* in *sentences* as they play now.  The frames are the new ones
    when both ends were found, else the old ones."""
    starts = [k for k, s in enumerate(sentences) if _covers(s, pick["first_word"])]
    ends = [k for k, s in enumerate(sentences) if _covers(s, pick["last_word"])]
    if not starts and not ends:
        return State.GONE, pick["start"], pick["end"]
    pairs = [(a, b) for a in starts for b in ends if a <= b]
    if not pairs:
        return State.CHANGED, pick["start"], pick["end"]
    old_len = pick["end"] - pick["start"]
    a, b = min(pairs, key=lambda ab: (abs(sentences[ab[0]]["start"] - pick["start"])
                                      + abs((sentences[ab[1]]["end"] - sentences[ab[0]]["start"]) - old_len), ab))
    start, end = int(sentences[a]["start"]), int(sentences[b]["end"])
    if end <= start:
        return State.CHANGED, pick["start"], pick["end"]
    return (State.OK if _whole(sentences[a:b + 1]) else State.CHANGED), start, end


def place_all(store: dict[str, Any], sentences: Sequence[dict], fps: float = 24.0) -> dict[str, int]:
    """Place every pick of *store* again in *sentences* (changes the store's picks), and say how many
    ended in each state plus how many moved: ``{"ok", "changed", "gone", "moved"}``.  When the sentences'
    signature is the one a pick was found with, nothing about it can have moved."""
    sig = sr.version(sentences)
    counts = {"ok": 0, "changed": 0, "gone": 0, "moved": 0}
    for p in store["picks"]:
        if p["sig"] == sig and p["state"] == State.OK:
            counts["ok"] += 1
            continue
        state, start, end = place(p, sentences)
        if state != State.GONE and (start, end) != (p["start"], p["end"]):
            counts["moved"] += 1
        if state == State.OK:
            p["sig"] = sig
            p["seconds"] = round((end - start) / (fps if fps > 0 else 24.0), 2)
        p["state"], p["start"], p["end"] = state, start, end
        counts[state] += 1
    return counts


def preview_range(pick: dict[str, Any], frame_start: int, frame_end: int) -> tuple[int, int]:
    """The frames Preview plays: the pick, kept inside the scene's range."""
    start = max(int(frame_start), int(pick["start"]))
    end = min(int(frame_end), int(pick["end"]) - 1)
    return start, max(start, end)


# -- markers -----------------------------------------------------------------------------------------

def marker_names(n: int, pick: dict[str, Any]) -> tuple[str, str]:
    """The names of a pick's marker pair: ``HL1: title`` and ``HL1: end``."""
    return ("{:s}{:d}: {:s}".format(MARKERS_PREFIX, n, pick["title"][:40]),
            "{:s}{:d}: end".format(MARKERS_PREFIX, n))


def marker_plan(store: dict[str, Any], existing: Sequence[tuple[str, int]], want: bool
                ) -> tuple[list[tuple[str, int]], list[str], list[tuple[str, int]]]:
    """What to do so the scene's markers match the store: ``(remove, names, add)``.  *existing* is the
    scene's markers as ``(name, frame)``.  *remove* are the ones the store made earlier (by name and,
    when the switch is off, all of them); *names* is the store's new list; *add* the markers to create
    (empty when *want* is false).  A marker the artist made is never touched, even with the same name
    as a pick's, unless the store recorded that name."""
    mine = set(store.get("markers") or [])
    remove = [(n, f) for n, f in existing if n in mine]
    add: list[tuple[str, int]] = []
    names: list[str] = []
    if want:
        for k, p in enumerate((p for p in store["picks"] if p["state"] != State.GONE), 1):
            a, b = marker_names(k, p)
            add.extend(((a, int(p["start"])), (b, int(p["end"]))))
            names.extend((a, b))
    return remove, names, add


# -- words for the panel and the chat ------------------------------------------------------------------

def clock(frames: float, fps: float) -> str:
    """``0:42`` or ``1:02:03``."""
    s = int(max(0, frames) / (fps if fps > 0 else 24.0))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return "{:d}:{:02d}:{:02d}".format(h, m, sec) if h else "{:d}:{:02d}".format(m, sec)


def state_text(state: str) -> str:
    return {"ok": "", "changed": "Changed, check it", "gone": "No longer in the edit"}.get(state, "")


def engine_line(kind: str, host: str = "") -> tuple[str, str]:
    """``(text, kind)``: what the dialog says about where the transcript text goes.  *kind* is what
    ``ask_json.engine_kind`` decided (``local``, ``remote`` or ``external``), the same decision the search acts on."""
    if kind == "remote":
        return ("The transcript text is sent to {:s}.".format(host or "the Remote API"), "remote")
    if kind == "external":
        return ("Your agent reads the transcript, so the text goes to its own service. The search is handed to it as a "
                "message (External Harness: copied to the clipboard).", "external")
    return ("Nothing leaves this computer: the Local model reads the transcript.", "local")


def summary(store: dict[str, Any], fps: float, frame_start: int = 1) -> str:
    """The short text the chat shows after a search: the picks, one line each, pointing to the panel."""
    picks = store["picks"]
    if not picks:
        return "No highlights found."
    lines = ["{:d} highlight{:s} found. They are in the Sequencer's Coworker tab, Highlights panel:".format(
        len(picks), "" if len(picks) == 1 else "s")]
    for k, p in enumerate(picks, 1):
        lines.append("{:d}. {:s} ({:s}, {:.0f} s)".format(k, p["title"], clock(p["start"] - frame_start, fps), p["seconds"]))
    return "\n".join(lines)
