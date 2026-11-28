# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Edit by Transcript in Blender (Tier 4, Video 7, #498): the transcript of a clip as text in the
Text Editor; editing the text edits the video.

* **Edit Transcript** (Sequencer: the Coworker Video panel and the Strip menu): the active sound
  or movie strip's transcript (``transcripts.py``) becomes a text data-block, one line per
  sentence with a time mark, shown in a Text Editor (the Sequencer's area is split when the
  screen has none).  The text carries :data:`transcript_edit.PROP` (the media file, its
  transcript, the scene, the last applied tokens, the retexts); every strip of that file
  carries :data:`transcript_edit.STRIP_PROP` (the text's name), so the link survives saving.
* **Apply** (Text Editor header and its Coworker panel): the core (``transcript_edit``) turns
  the change into cuts, retexts and moves.  Cuts go through Remove Silences' split / ripple
  code (``wand.sequencer.apply_cuts``, #539: picture and sound together, every track in step,
  captions trimmed, locked strips refusing before anything changes, strip removals with the
  prefetch stopped, #536); moves through ``wand.sequencer.move_frames``; a retext changes the
  captions over those words only (Auto Captions' captions, #376, still as Coworker made them).
  One undo step.  Text with no audio behind it is flagged on its line and changes nothing; a cut
  of more than half the clip asks first (Apply's dialog, the tool's ``confirm_large_cut``).
* **Both ways**: a click on a line moves the playhead to it (*Follow*), Jump to Playhead
  moves the cursor to the line playing, the line playing is highlighted, and a cut made in the
  Sequencer shows in the text (it is rebuilt from the timeline when it has no edits pending).
* **Live** (opt-in): applies by itself after a pause in typing (longer for whole lines), never
  while a render runs or a clip plays, never a cut whose words are on the clipboard (Ctrl+X: the
  paste comes next), never a cut of more than half the clip.
* **Wands**: Remove Filler Words (lists what it found first), Remove Silences, Make Captions.
* **Tools**: :func:`tool_get_transcript_text` and :func:`tool_apply_transcript_edit`.
"""

from __future__ import annotations

__all__ = (
    "apply_text",
    "open_transcript",
    "register",
    "tool_apply_transcript_edit",
    "tool_get_transcript_text",
    "unregister",
)

import bisect
import hashlib
import json
import os
import time
from typing import Any

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, IntProperty, StringProperty  # pylint: disable=import-error
from bpy.types import Operator, Panel  # pylint: disable=import-error

from . import transcript_edit as te
from .shared import icon as safe_icon
from .wand import scene as _seq_scene
from .wand.spec import agent_line

CATEGORY = "Coworker"
WHAT = "Edit by Transcript"
LIVE_PAUSE_S = 1.5          # Live applies this long after the last key
LIVE_LINE_PAUSE_S = 4.0     # ... a cut of whole lines this long: a line cut to be pasted elsewhere gets its paste first
HOLD_RETRY_S = 0.5          # a held back Live edit looks again this often
CHECK_PAUSE_S = 0.4         # the panel's preview of the edit, this long after the last key
_TICK_S = 0.25
NOT_TRANSCRIBED = ("“{:s}” hasn't been transcribed yet: run Auto Captions on it (it transcribes first) or ask "
                   "Coworker to transcribe it, then Edit Transcript again")

# Per text (by name): what the timer last saw and the preview of the pending edit.
_SEEN: dict[str, dict[str, Any]] = {}
_WORDS: dict[tuple, list] = {}
_DRAW: list[Any] = []


# -- the text's state ----------------------------------------------------------------------------

def _hash(body: str) -> str:
    return hashlib.sha1(str(body or "").encode("utf-8")).hexdigest()[:16]


def state_of(text: Any) -> dict[str, Any] | None:
    """What the text is a view of (:data:`transcript_edit.PROP`), or None for any other text."""
    if text is None or not hasattr(text, "get"):
        return None
    raw = text.get(te.PROP)
    if raw is None:
        return None
    try:
        data = raw.to_dict()
    except AttributeError:
        data = dict(raw) if isinstance(raw, dict) else None
    return data if data and data.get("source") else None


def _j(state: dict, key: str, default: Any) -> Any:
    try:
        value = json.loads(state.get(key) or "")
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def pending(text: Any, state: dict | None = None) -> bool:
    """The text has edits that aren't applied yet."""
    state = state or state_of(text)
    return bool(state) and _hash(text.as_string()) != state.get("applied", "")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


def strip_path(strip: Any) -> str:
    if strip.type == 'MOVIE':
        path, lib = strip.filepath, None
    elif strip.type == 'SOUND' and strip.sound is not None:
        path, lib = strip.sound.filepath, strip.sound.library
    else:
        return ""
    if not path:
        return ""
    return os.path.normpath(bpy.path.abspath(path, library=lib))


def _fps(scene: Any) -> float:
    return scene.render.fps / (scene.render.fps_base or 1.0)


def source_strips(scene: Any, source: str) -> list:
    """The top-level strips playing *source* (normalised path): its sound strips, else its movies."""
    ed = scene.sequence_editor
    if ed is None:
        return []
    top = list(ed.strips)
    sounds = [s for s in top if s.type == 'SOUND' and _norm(strip_path(s)) == source]
    if sounds:
        return sounds
    return [s for s in top if s.type == 'MOVIE' and _norm(strip_path(s)) == source]


def _piece(strip: Any) -> dict[str, Any]:
    return {"name": strip.name, "left": int(strip.left_handle), "right": int(strip.right_handle),
            "content_start": float(strip.content_start), "channel": int(strip.channel),
            "offset": float(getattr(strip, "sound_offset", 0.0) or 0.0) if strip.type == 'SOUND' else 0.0}


def _signature(pieces: list[dict]) -> str:
    rows = sorted((p["name"], p["left"], p["right"], round(p["content_start"], 3), round(p["offset"], 4))
                  for p in pieces)
    return _hash(json.dumps(rows))


# -- the transcript -------------------------------------------------------------------------------

def _fallback_dir() -> str:
    try:
        from . import whisper_manager
        return str(whisper_manager.data_dir())
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def find_transcript(media: str) -> str:
    """The cached transcript file of the media file *media* ("" when it was never transcribed)."""
    from . import transcripts
    try:
        key, _size = transcripts.media_key(media)
    except OSError:
        return ""
    blend = bpy.data.filepath or ""
    fallback = _fallback_dir()
    found = transcripts.find_cached(key, None, None, blend, fallback)
    if found is None and blend:
        found = transcripts.find_cached(key, None, None, "", fallback)
    return str(found) if found else ""


def load_words(path: str) -> list:
    """The word list of the transcript file *path*, as it is (word indices are its own)."""
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    words = _WORDS.get(key)
    if words is None:
        with open(path, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
        raw = doc.get("words") if isinstance(doc, dict) else doc
        words = [w if isinstance(w, dict) else {} for w in (raw or [])]
        if len(_WORDS) > 8:
            _WORDS.clear()
        _WORDS[key] = words
    return words


# -- the view now ----------------------------------------------------------------------------------

class _Problem(Exception):
    pass


def view_now(state: dict) -> dict[str, Any]:
    """The source's words on the timeline now and their text: ``{"scene", "words", "pieces",
    "occ", "view", "fps"}``; raises :class:`_Problem` with a sentence for the artist."""
    scene = bpy.data.scenes.get(state.get("scene", ""))
    if scene is None:
        raise _Problem("The scene “{:s}” of this transcript is gone".format(state.get("scene", "")))
    ed = scene.sequence_editor
    if ed is None:
        raise _Problem("The scene “{:s}” has no strips".format(scene.name))
    if len(ed.meta_stack):
        raise _Problem("Leave the meta strip first (Tab): {:s} works on the top level".format(WHAT))
    path = state.get("transcript", "")
    if not path or not os.path.isfile(path):
        path = find_transcript(state.get("path", ""))
        if not path:
            raise _Problem("The transcript of “{:s}” isn't there any more".format(
                os.path.basename(state.get("path", ""))))
        state["transcript"] = path
    try:
        words = load_words(path)
    except (OSError, ValueError) as ex:
        raise _Problem("Couldn't read the transcript: {!s}".format(ex)) from None
    strips = source_strips(scene, state["source"])
    if not strips:
        raise _Problem("No strip of “{:s}” is on the timeline of “{:s}”".format(
            os.path.basename(state.get("path", "")), scene.name))
    pieces = [_piece(s) for s in strips]
    fps = _fps(scene)
    occ = te.timeline_words(words, pieces, fps, scene.frame_start)
    seen: set[int] = set()
    for o in occ:
        if o["i"] in seen:
            raise _Problem("Part of “{:s}” is on the timeline twice: {:s} needs each part of the clip once".format(
                os.path.basename(state.get("path", "")), WHAT))
        seen.add(o["i"])
    view = te.render(occ, words, _j(state, "retext", {}), fps)
    return {"scene": scene, "words": words, "pieces": pieces, "strips": strips, "occ": occ, "view": view,
            "fps": fps}


def _set_body(text: Any, body: str) -> None:
    """Replace the text, keeping the cursor on the same line where it can."""
    line = int(getattr(text, "current_line_index", 0) or 0)
    text.from_string(body)
    count = max(1, body.count("\n"))
    try:
        text.cursor_set(min(line, count - 1))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass


def _state_from_view(state: dict, got: dict, flags: list | None = None, carried: dict | None = None) -> None:
    """Put the view *got* into *state* (a dict: nothing in Blender is written).  *carried*
    (:func:`transcript_edit.carry_base`): the text stays as the artist typed it, so the last applied lines
    are the artist's own lines, with the marks that are in the text."""
    view = got["view"]
    state["tokens"] = json.dumps(view["tokens"])
    state["order"] = json.dumps(view["order"])
    if carried:
        frames = {o["i"]: o["frame"] for o in got["occ"]}
        rows, lines = [], []
        for (mark, first, count), n in zip(carried["lines"], carried["text_lines"]):
            rows.append([mark, first, count])
            at = [frames[int(w)] for w, _t in view["tokens"][first:first + count] if int(w) in frames]
            if n is not None and at:
                lines.append([n, min(at)])
        state["lines"] = json.dumps(lines)
        state["base_lines"] = json.dumps(rows)
    else:
        state["lines"] = json.dumps([[ln["line"], ln["frame"]] for ln in view["lines"]])
        state["base_lines"] = json.dumps([[ln["mark"], ln["first"], ln["count"]] for ln in view["lines"]])
    state["frames"] = json.dumps({str(o["i"]): o["frame"] for o in got["occ"]})
    state["sig"] = _signature(got["pieces"])
    state["fps"] = float(got["fps"])
    state["flags"] = json.dumps(flags or [])


class _Virtual:
    """A transcript text that is no data-block: what a dry run reads, so it creates and writes nothing."""

    def __init__(self, name: str, state: dict, body: str) -> None:
        self.name, self._state, self._body = name, state, body

    def get(self, key: str, default: Any = None) -> Any:
        return self._state if key == te.PROP else default

    def as_string(self) -> str:
        return self._body


def _store(text: Any, state: dict, got: dict, write: bool, flags: list | None = None,
           carried: dict | None = None) -> None:
    """The view *got* as the last applied state; with *write* the text becomes it."""
    view = got["view"]
    _state_from_view(state, got, flags, carried)
    if write:
        _set_body(text, view["text"])
    state["applied"] = _hash(text.as_string())
    text[te.PROP] = state
    names = {s.name for s in got["strips"]}
    for s in got["strips"]:
        s[te.STRIP_PROP] = text.name
    if names:
        state["strip"] = sorted(got["strips"], key=lambda s: s.left_handle)[0].name
        text[te.PROP] = state
    _SEEN.pop(text.name, None)


# -- opening ---------------------------------------------------------------------------------------

def text_for(scene: Any, source: str) -> Any:
    """The transcript text of *source* in *scene*, or None."""
    for text in bpy.data.texts:
        st = state_of(text)
        if st and st.get("source") == source and st.get("scene") == scene.name:
            return text
    return None


def open_transcript(scene: Any, strip: Any, transcript_path: str = "") -> tuple[Any, str]:
    """The transcript text of *strip* (made, or brought up to date with the timeline when it has
    no edits pending); ``(text, note)``.  Raises :class:`_Problem`."""
    media = strip_path(strip)
    if not media:
        raise _Problem("“{:s}” isn't a sound or movie strip with a file".format(strip.name))
    path = bpy.path.abspath(transcript_path) if transcript_path else find_transcript(media)
    if not path or not os.path.isfile(path):
        raise _Problem(NOT_TRANSCRIBED.format(strip.name))
    source = _norm(media)
    text = text_for(scene, source)
    note = ""
    if text is None:
        text = bpy.data.texts.new("{:s} transcript".format(os.path.splitext(os.path.basename(media))[0]))
        state: dict[str, Any] = {"schema": 1, "source": source, "path": media, "scene": scene.name,
                                 "strip": strip.name, "retext": "{}", "live": False, "follow": True}
    else:
        state = state_of(text)
        if pending(text, state):
            note = "It has edits not applied yet: they are kept (Apply them, or Refresh from Timeline)"
            return text, note
    state["transcript"] = os.path.normpath(path)
    got = view_now(state)
    _store(text, state, got, write=True)
    if not got["view"]["tokens"]:
        note = "No words of the transcript are inside what the strips show"
    return text, note


def show_text(context: Any, text: Any) -> Any:
    """Show *text* in a Text Editor of the screen (the Sequencer's area is split when there is none)."""
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type == 'TEXT_EDITOR':
            area.spaces.active.text = text
            return area
    area = context.area if getattr(context.area, "type", "") == 'SEQUENCE_EDITOR' else None
    if area is None:
        seqs = [a for a in screen.areas if a.type == 'SEQUENCE_EDITOR']
        area = max(seqs, key=lambda a: a.width * a.height) if seqs else None
    if area is None:
        return None
    before = set(a.as_pointer() for a in screen.areas)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    try:
        with context.temp_override(area=area, region=region):
            bpy.ops.screen.area_split(direction='VERTICAL', factor=0.62)
    except RuntimeError:
        return None
    new = next((a for a in screen.areas if a.as_pointer() not in before), None)
    if new is None:
        return None
    new.type = 'TEXT_EDITOR'
    space = new.spaces.active
    space.text = text
    for name, value in (("show_line_numbers", True), ("show_word_wrap", True), ("show_syntax_highlight", False)):
        try:
            setattr(space, name, value)
        except (AttributeError, TypeError):
            pass
    return new


# -- applying --------------------------------------------------------------------------------------

def _seq() -> Any:
    from .wand import sequencer
    return sequencer


def _caption_key(source: str) -> str:
    return "media:" + source            # captions_ops.source_key of the media file (#376)


def _update_captions(scene: Any, state: dict, words: list, occ: list, retext: dict, names: set[str],
                     spans: list[tuple[int, int]]) -> tuple[int, int]:
    """The captions of the source over the changed frames show the words under them (#376); a
    caption changed by hand is left and counted.  ``(updated, kept)``."""
    try:
        from . import captions_ops
    except Exception:  # pylint: disable=broad-exception-caught
        return 0, 0
    seq = _seq()
    tag_name = getattr(captions_ops.cap, "TAG", "bfacw_caption")
    key = _caption_key(state["source"])
    updated = kept = 0
    # Names first: setting a Word-by-word caption's text removes and adds its word strips, and a
    # strip taken before that is dead after it (#593).  Each strip is looked up again, by name,
    # right before it is used, and skipped when it is gone.
    candidates = []
    for strip in list(scene.sequence_editor.strips):
        if strip.type != 'TEXT':
            continue
        tag = captions_ops.tag_of(strip) or {}
        if tag.get("role") != "line" or tag.get("source") != key:
            continue
        left, right = int(strip.left_handle), int(strip.right_handle)
        if strip.name not in names and not any(a < right and left < b for a, b in spans):
            continue
        candidates.append(strip.name)
    for name in candidates:
        strip = scene.sequence_editor.strips.get(name)
        if strip is None:
            continue
        left, right = int(strip.left_handle), int(strip.right_handle)
        want = te.caption_text(occ, words, retext, left, right)
        if not want or " ".join(want.split()).casefold() == " ".join(strip.text.split()).casefold():
            continue
        if not seq._unedited(strip):  # pylint: disable=protected-access
            kept += 1
            continue
        captions_ops.set_caption_text(scene, strip, want)
        strip = scene.sequence_editor.strips.get(name)      # its word strips were made again: look it up again
        if strip is not None and strip.get(tag_name) is not None:
            seq._resign(strip)  # pylint: disable=protected-access
        updated += 1
    return updated, kept


def _moves(scene: Any, state: dict, words: list, target: list[int], fps: float, progress: dict) -> dict[str, Any]:
    """Reorder the clips so the words play in the *target* order, one block at a time."""
    seq = _seq()
    spans: list[tuple[int, int]] = []
    pieces_named: dict[str, list[str]] = {}
    count = 0
    for _step in range(200):
        pieces = [_piece(s) for s in source_strips(scene, state["source"])]
        occ = te.timeline_words(words, pieces, fps, scene.frame_start)
        current = [o["i"] for o in occ]
        on = set(current)
        blocks = te.plan_moves(current, [w for w in target if w in on])
        if not blocks:
            return {"status": "ok", "moves": count, "spans": spans, "new_names": pieces_named}
        block, after = blocks[0]
        where = {o["i"]: o["piece"] for o in occ}
        sub = [block[0]]
        for w in block[1:]:
            if where[w] != where[sub[0]]:
                break
            sub.append(w)
        rng = te.move_ranges(occ, sub, after, words, pieces, fps)
        if rng is None:
            return {"status": "error", "message": "Couldn't place a moved block on the timeline", "moves": count,
                    "spans": spans, "changed": count > 0, "new_names": pieces_named}
        out = seq.move_frames(scene, *rng, what=WHAT)
        if out.get("changed"):
            progress["changed"] = True
        if out["status"] != "ok":
            out.update(moves=count, spans=spans, changed=count > 0 or out.get("changed", False),
                       new_names=pieces_named)
            return out
        progress["changed"] = True
        count += 1
        pieces_named.update(out.get("new_names") or {})
        spans.append((out["to"], out["to"] + out["frames"]))
        spans.append((min(rng), max(rng)))
    return {"status": "error", "message": "The moves didn't settle", "moves": count, "spans": spans,
            "changed": True, "new_names": pieces_named}


def _refit_captions(scene: Any, new_names: dict) -> None:
    """After a move: the captions split on the way get Remove Silences' refit (their animation
    fits the new length), when the add-on has it (#542)."""
    try:
        from . import captions_ops
    except Exception:  # pylint: disable=broad-exception-caught
        return
    refit = getattr(captions_ops, "refit_captions", None)
    if refit is None:
        return
    strips = scene.sequence_editor.strips_all
    names = []
    for pieces in (new_names or {}).values():
        for name in pieces:
            strip = strips.get(name)
            if strip is not None and strip.type == 'TEXT' and name not in names:
                names.append(name)
    if names:
        try:
            refit(scene, names)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] {:s}: caption refit: {!s}".format(WHAT, ex))


def apply_text(text: Any, body: str | None = None, keep_text: bool = False, dry_run: bool = False,
               padding: float = te.PADDING_S, confirm: bool = False) -> dict[str, Any]:
    """Apply the transcript text *text* (or *body* as its new text) to the timeline: cuts,
    moves and caption fixes; no undo step of its own.  *keep_text*: the artist's text stays as
    it is (Live, while typing).  *dry_run*: only say what would happen (nothing is written).
    *confirm*: an edit that would cut more than half of the clip (``needs_confirm``) goes ahead;
    without it the answer is ``status`` "confirm" and nothing changes.

    An exception inside leaves what it had changed as it is, and says so: ``changed`` is true and
    the caller's undo step (the operator's, the tool's) takes it all back with one Ctrl+Z; before
    any change it leaves the timeline as it was."""
    progress = {"changed": False}
    try:
        return _apply_text(text, body, keep_text, dry_run, padding, confirm, progress)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        name = getattr(text, "name", "")
        if progress["changed"]:
            return {"status": "error", "changed": True, "text": name, "message":
                    "{:s} stopped part-way ({!s}): it is one undo step, press Ctrl+Z to get the edit back".format(
                        WHAT, ex)}
        return {"status": "error", "changed": False, "text": name,
                "message": "{:s} couldn't apply ({!s}): nothing was changed".format(WHAT, ex)}


def _plan(state: dict, body: str, padding: float = te.PADDING_S) -> dict[str, Any]:
    """What *body* (as the text now) asks of the timeline: the core's edit, the cuts as frames
    and the numbers the artist sees (words, seconds, share of the clip).  Reads the timeline;
    changes nothing.  Raises :class:`_Problem`."""
    got = view_now(state)
    words, occ, fps = got["words"], got["occ"], got["fps"]
    tokens = _j(state, "tokens", [])
    order = _j(state, "order", [])
    if not tokens and not order:
        tokens, order = got["view"]["tokens"], got["view"]["order"]
        base_lines = [[ln["mark"], ln["first"], ln["count"]] for ln in got["view"]["lines"]]
    else:
        base_lines = _j(state, "base_lines", [])
    timeline_s = sum(max(0, p["right"] - p["left"]) for p in got["pieces"]) / fps
    edit = te.compute_edit(tokens, order, body, words, _j(state, "retext", {}), base_lines=base_lines,
                           timeline_s=timeline_s)
    on = {o["i"] for o in occ}
    cut = [w for w in edit["cut"] if w in on]
    gone = [w for w in edit["order"] if w not in on]
    frames = te.cut_ranges(occ, cut, words, got["pieces"], fps, padding) if cut else []
    seconds = sum(b - a for a, b in frames) / fps
    share = max(len(cut) / len(on) if on else 0.0, seconds / timeline_s if timeline_s > 0 else 0.0)
    needs_confirm = bool(cut) and (share > te.LARGE_CUT or bool(edit["needs_confirm"]))
    summary = {"cut_words": len(cut), "retexted": len(edit["retexted"]), "moved": bool(edit["moved"]),
               "flags": [dict(f, line=f["line"] + 1) for f in edit["flags"]], "ops": edit["ops"],
               "seconds": round(seconds, 2), "percent": int(round(100 * share)), "needs_confirm": needs_confirm,
               "lines_cut": edit["lines_cut"]}
    if gone:
        summary["not_on_timeline"] = len(gone)
    return {"got": got, "edit": edit, "cut": cut, "frames": frames, "seconds": seconds, "summary": summary,
            "needs_confirm": needs_confirm}


def _apply_text(text: Any, body: str | None, keep_text: bool, dry_run: bool, padding: float, confirm: bool,
                progress: dict) -> dict[str, Any]:
    state = state_of(text)
    if state is None:
        return {"status": "error", "message": "“{:s}” isn't a transcript text".format(getattr(text, "name", ""))}
    if body is None:
        body = text.as_string()
    try:
        plan = _plan(state, body, padding)
    except _Problem as ex:
        return {"status": "error", "message": str(ex)}
    got, edit, cut, frames, seconds = plan["got"], plan["edit"], plan["cut"], plan["frames"], plan["seconds"]
    words, occ, scene, fps = got["words"], got["occ"], got["scene"], got["fps"]
    summary, needs_confirm, flags = plan["summary"], plan["needs_confirm"], edit["flags"]
    if dry_run or not (cut or edit["retexted"] or edit["moved"]):
        status = "ok" if dry_run else "nothing"
        message = _message(summary, None, None, 0, 0, dry_run=dry_run)
        return dict(summary, status=status, message=message, text=text.name)
    if needs_confirm and not confirm:
        return dict(summary, status="confirm", text=text.name, message=(
            "This cuts {:.1f} s ({:d}% of the clip, {:s}): confirm it (Apply asks; the tool needs "
            "confirm_large_cut) or edit less".format(seconds, summary["percent"], _plural(len(cut), "word"))))
    region = te.move_span(occ, edit["order"], words, got["pieces"], fps) if edit["moved"] else None
    if region is not None:                              # everything the moves touch is checked before the cuts
        blocked = _seq().blocked_in(scene, *region)
        if blocked:
            listed = "; ".join("“{:s}” {:s}".format(n, r) for n, r in blocked[:4])
            return dict(summary, status="refused", refused=[{"strip": n, "reason": r} for n, r in blocked],
                        message="Nothing was changed, so the edit stays in sync: {:s}".format(listed), text=text.name)
    seq = _seq()
    cut_out = None
    names: set[str] = set()
    spans: list[tuple[int, int]] = []
    if cut:
        cut_out = seq.apply_cuts(scene, frames, True, True, WHAT)
        if cut_out["status"] != "ok":
            if cut_out.get("changed"):
                progress["changed"] = True
            return dict(summary, **cut_out, text=text.name)
        progress["changed"] = True
        names.update(cut_out.get("captions_trimmed") or ())
        cuts_index = _cuts_index(cut_out.get("cut_ranges") or ())
        for a, _b in cut_out.get("cut_ranges") or ():
            landed = int(cuts_index.map(int(a)))            # where the cut closed up
            spans.append((landed - 1, landed + 1))
    move_out = None
    if edit["moved"]:
        move_out = _moves(scene, state, words, edit["order"], fps, progress)
        spans.extend(move_out.get("spans") or ())
        if move_out["status"] != "ok":
            changed = bool(cut_out) or move_out.get("changed")
            return dict(summary, status="error", changed=changed, text=text.name,
                        message=move_out.get("message", "") + (": press Ctrl+Z to get the edit back" if changed else ""))
        if move_out.get("moves"):
            _refit_captions(scene, move_out.get("new_names") or {})
    state["retext"] = json.dumps(edit["retext"])
    try:
        after = view_now(state)
    except _Problem as ex:
        return dict(summary, status="error", changed=True, message=str(ex), text=text.name)
    frames_of = {o["i"]: o["frame"] for o in after["occ"]}
    for w in edit["retexted"]:
        if w in frames_of:
            spans.append((frames_of[w], frames_of[w] + 1))
    updated, kept = _update_captions(scene, state, words, after["occ"], edit["retext"], names, spans)
    write = not keep_text and not flags
    # the text stays as typed (Live; flagged lines): the base is then the artist's own lines, so the marks in the
    # text and in the base agree and flagged lines stay pending against the words they were pending against
    carried = None if write else te.carry_base(edit, after["view"])
    _store(text, state, after, write=write, flags=summary["flags"], carried=carried)
    _record(state, edit, after)
    message = _message(summary, cut_out, move_out, updated, kept)
    out = dict(summary, status="ok", message=message, text=text.name, captions_updated=updated,
               captions_kept_edited=kept)
    if cut_out:
        out.update(seconds_removed=cut_out["seconds_removed"], cut_ranges=cut_out["cut_ranges"],
                   new_names=cut_out["new_names"], end_frame=cut_out["end_frame"])
    if move_out:
        out["moves"] = move_out.get("moves", 0)
    return out


def _cuts_index(ranges: Any) -> Any:
    from .wand import cut_plan
    return cut_plan.Cuts([tuple(r) for r in ranges])


def _record(state: dict, edit: dict, got: dict) -> None:
    """The edit list next to the transcript (never the transcript itself)."""
    path = state.get("transcript", "")
    if not path:
        return
    target = te.edits_path(path)
    try:
        rec = te.edit_record(te.read_record(target), {"path": state.get("path", ""), "transcript":
                             os.path.basename(path)}, edit, got["view"]["order"])
        te.write_record(target, rec)
    except OSError as ex:
        print("[Coworker] {:s}: couldn't write {:s}: {!s}".format(WHAT, str(target), ex))


def _plural(n: int, one: str, many: str = "") -> str:
    return "{:d} {:s}".format(n, one if n == 1 else (many or one + "s"))


def _message(summary: dict, cut_out: dict | None, move_out: dict | None, updated: int, kept: int,
             dry_run: bool = False) -> str:
    parts = []
    if summary["cut_words"]:
        secs = cut_out["seconds_removed"] if cut_out else summary.get("seconds")
        parts.append("cut {:s}{:s}".format(_plural(summary["cut_words"], "word"),
                                           " ({:.1f} s)".format(secs) if secs is not None else ""))
    if summary["moved"]:
        n = (move_out or {}).get("moves")
        parts.append("moved {:s}".format(_plural(n, "block")) if n else "moved words")
    if summary["retexted"]:
        parts.append("{:s} changed".format(_plural(summary["retexted"], "word")))
    if updated:
        parts.append("{:s} updated".format(_plural(updated, "caption")))
    if kept:
        parts.append("{:s} changed by hand left as {:s}".format(_plural(kept, "caption"),
                                                               "it is" if kept == 1 else "they are"))
    flags = summary["flags"]
    flag_text = ""
    if flags:
        flag_text = "{:s} flagged (line {:s}): {:s}".format(
            _plural(len(flags), "line"), ", ".join(str(f["line"]) for f in flags[:5]), flags[0]["message"])
    if not parts:
        return flag_text or "Nothing to apply: the text matches the timeline"
    head = ("Would " if dry_run else "") + "; ".join(parts)
    head = head[0].upper() + head[1:]
    if dry_run and summary.get("needs_confirm"):
        head += ", {:d}% of the clip: applying asks first".format(summary.get("percent", 0))
    return head + (". " + flag_text if flag_text else "")


def refresh_text(text: Any) -> dict[str, Any]:
    """Rebuild the text from the timeline (edits not applied are dropped)."""
    state = state_of(text)
    if state is None:
        return {"status": "error", "message": "Not a transcript text"}
    try:
        got = view_now(state)
    except _Problem as ex:
        return {"status": "error", "message": str(ex)}
    _store(text, state, got, write=True)
    return {"status": "ok", "message": "The text shows the timeline now ({:s})".format(
        _plural(len(got["view"]["lines"]), "line"))}


# -- lines and the playhead -------------------------------------------------------------------------

def _cached_hash(text: Any) -> str:
    """The hash of the text as the timer last saw it (read once here when it hasn't): drawing the
    panel or the overlay never reads the whole text again."""
    seen = _SEEN.setdefault(text.name, {})
    h = seen.get("view_hash")
    if h is None:
        h = seen["view_hash"] = _hash(text.as_string())
    return h


def _is_pending(text: Any, state: dict | None = None) -> bool:
    state = state or state_of(text)
    return bool(state) and _cached_hash(text) != state.get("applied", "")


def line_frames(text: Any, state: dict | None = None) -> dict[int, int]:
    """``{0-based line: frame}`` of the text as it is now (cached per text and content: the lines
    sorted for the playhead too, ``_SEEN[name]["sorted"]``)."""
    state = state or state_of(text)
    if not state:
        return {}
    seen = _SEEN.setdefault(text.name, {})
    key = (_cached_hash(text), state.get("applied"), state.get("sig"))
    if seen.get("lines_for") == key:
        return seen["line_frames"]
    body = text.as_string()
    if key[0] == state.get("applied"):
        frames = {int(a): int(b) for a, b in _j(state, "lines", [])}
    else:
        frames = te.line_frames(_j(state, "tokens", []), body, _j(state, "frames", {}),
                                _j(state, "base_lines", []))
    scene = bpy.data.scenes.get(state.get("scene", ""))
    if scene is not None:
        fps = _fps(scene)
        for n, line in enumerate(body.splitlines()):
            if n not in frames:
                t = te.parse_mark(line)
                if t is not None and line[te.MARK_RE.match(line).end():].strip():
                    frames[n] = int(round(scene.frame_start + t * fps))
    ends = [int(f) for f in _j(state, "frames", {}).values()]
    seen["lines_for"] = key
    seen["line_frames"] = frames
    seen["sorted"] = sorted((f, n) for n, f in frames.items())
    seen["sorted_frames"] = [f for f, _n in seen["sorted"]]
    seen["last_frame"] = max(ends) if ends else None
    return frames


def playing_line(text: Any, state: dict | None = None) -> int | None:
    state = state or state_of(text)
    scene = bpy.data.scenes.get(state.get("scene", "")) if state else None
    if scene is None:
        return None
    if not line_frames(text, state):
        return None
    seen = _SEEN[text.name]
    current = scene.frame_current
    k = bisect.bisect_right(seen["sorted_frames"], current) - 1
    if k < 0:
        return None
    end = seen.get("last_frame")
    if end is not None and current > end + 3 * _fps(scene):
        return None
    return seen["sorted"][k][1]


def seek_line(text: Any, line: int) -> bool:
    """Move the playhead to the start of *line*."""
    state = state_of(text)
    scene = bpy.data.scenes.get(state.get("scene", "")) if state else None
    if scene is None:
        return False
    frame = line_frames(text, state).get(int(line))
    if frame is None:
        return False
    scene.frame_current = int(frame)
    return True


# -- operators -------------------------------------------------------------------------------------

def _context_text(context: Any) -> Any:
    space = getattr(context, "space_data", None)
    text = getattr(space, "text", None) if getattr(space, "type", "") == 'TEXT_EDITOR' else None
    if text is None:
        text = getattr(context, "edit_text", None)
    return text if state_of(text) else None


def _named_text(name: str, context: Any) -> Any:
    if name:
        text = bpy.data.texts.get(name)
        return text if state_of(text) else None
    return _context_text(context)


def _source_strip(context: Any) -> Any:
    scene = _seq_scene.sequencer_scene(context)
    ed = getattr(scene, "sequence_editor", None)
    if ed is None:
        return None
    active = ed.active_strip
    if active is not None and active.type in {'SOUND', 'MOVIE'}:
        return active
    chosen = [s for s in ed.strips if s.select and s.type in {'SOUND', 'MOVIE'}]
    return chosen[0] if chosen else None


class BFACW_OT_open_transcript(Operator):  # type: ignore[misc]
    """Edit the video by its words: the transcript of the active sound or movie strip opens as text in the Text Editor"""

    bl_idname = "bfacw.open_transcript"
    bl_label = "Edit Transcript"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER', 'UNDO'}

    transcript_path: StringProperty(name="Transcript File", subtype='FILE_PATH', options={'SKIP_SAVE'},  # type: ignore[valid-type]
                                    description="A transcript JSON file (bfa_coworker.transcript/1) to use instead of "
                                                "the clip's own")
    strip: StringProperty(name="Strip", options={'SKIP_SAVE'},  # type: ignore[valid-type]
                          description="The strip (empty: the active one)")

    @classmethod
    def poll(cls, context):
        if not _seq_scene.poll(cls, context):
            return False
        if _source_strip(context) is None:
            cls.poll_message_set("Select a sound or movie strip that has been transcribed")
            return False
        return True

    def execute(self, context):
        scene = _seq_scene.sequencer_scene(context) or context.scene
        strip = scene.sequence_editor.strips.get(self.strip) if self.strip else _source_strip(context)
        if strip is None:
            self.report({'WARNING'}, "No strip named “{:s}”".format(self.strip))
            return {'CANCELLED'}
        try:
            text, note = open_transcript(scene, strip, self.transcript_path)
        except _Problem as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        shown = show_text(context, text)
        lines = len(_j(state_of(text) or {}, "lines", []))
        msg = "“{:s}”: {:s}. Edit the text, then Apply (Text Editor header)".format(text.name, _plural(lines, "line"))
        if shown is None and getattr(context, "screen", None) is not None:
            msg += "; open it in a Text Editor"
        self.report({'INFO'}, msg + (". " + note if note else ""))
        return {'FINISHED'}


class _TextOp:
    text_name: StringProperty(name="Text", options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if _context_text(context) is None:
            cls.poll_message_set("Show a transcript text (Sequencer > Strip > Edit Transcript)")
            return False
        return True

    def text(self, context):
        return _named_text(self.text_name, context)


class BFACW_OT_transcript_apply(_TextOp, Operator):  # type: ignore[misc]
    """Edit the video as the text says: deleted words are cut (the gap closed), moved lines move their clips, changed words change the captions only"""

    bl_idname = "bfacw.transcript_apply"
    bl_label = "Apply"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER', 'UNDO'}

    confirmed: BoolProperty(name="Confirmed", default=False, options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]

    def invoke(self, context, event):
        text = self.text(context)
        if text is not None and not self.confirmed:
            out = apply_text(text, dry_run=True)
            if out.get("needs_confirm"):
                self.confirmed = True
                return context.window_manager.invoke_confirm(
                    self, event, title="Cut most of the clip?",
                    message="This cuts {:.1f} s ({:d}% of the clip, {:s}). One Ctrl+Z brings it back. Go on?".format(
                        out.get("seconds", 0.0), out.get("percent", 0), _plural(out.get("cut_words", 0), "word")),
                    confirm_text="Cut")
        return self.execute(context)

    def execute(self, context):
        text = self.text(context)
        if text is None:
            return {'CANCELLED'}
        out = apply_text(text, confirm=bool(self.confirmed))
        self.confirmed = False
        status = out["status"]
        for flag in out.get("flags") or ():
            self.report({'WARNING'}, "Line {:d}: “{:s}”: {:s}".format(flag["line"], flag["text"][:40], flag["message"]))
        if status == "ok":
            self.report({'INFO'}, out["message"])
            _redraw_text_editors()
            return {'FINISHED'}
        self.report({'INFO'} if status == "nothing" else {'ERROR'}, out["message"])
        return {'FINISHED'} if out.get("changed") else {'CANCELLED'}


class BFACW_OT_transcript_refresh(_TextOp, Operator):  # type: ignore[misc]
    """Rebuild the text from the timeline as it is now (cuts made in the Sequencer show); edits not applied are dropped"""

    bl_idname = "bfacw.transcript_refresh"
    bl_label = "Refresh from Timeline"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, _event):
        text = self.text(context)
        if text is not None and pending(text):
            return context.window_manager.invoke_confirm(self, _event, title=self.bl_label,
                                                         message="Drop the edits that aren't applied yet?")
        return self.execute(context)

    def execute(self, context):
        text = self.text(context)
        if text is None:
            return {'CANCELLED'}
        out = refresh_text(text)
        self.report({'INFO'} if out["status"] == "ok" else {'WARNING'}, out["message"])
        return {'FINISHED'} if out["status"] == "ok" else {'CANCELLED'}


def _visible_line_top(space: Any, line: int) -> None:
    try:
        rows = max(4, int(space.visible_lines))
    except (AttributeError, TypeError):
        rows = 20
    if not space.top <= line < space.top + rows - 1:
        space.top = max(0, line - rows // 3)


class BFACW_OT_transcript_jump_to_playhead(_TextOp, Operator):  # type: ignore[misc]
    """Put the text cursor on the line playing at the playhead"""

    bl_idname = "bfacw.transcript_jump_to_playhead"
    bl_label = "Jump to Playhead"
    bl_description = agent_line(__doc__)

    def execute(self, context):
        text = self.text(context)
        line = playing_line(text) if text is not None else None
        if line is None:
            self.report({'INFO'}, "No line plays at the playhead")
            return {'CANCELLED'}
        try:
            text.cursor_set(line)
        except (AttributeError, TypeError, RuntimeError):
            text.current_line_index = line
        _SEEN.setdefault(text.name, {})["cursor"] = line          # not a click: the playhead stays
        space = getattr(context, "space_data", None)
        if getattr(space, "type", "") == 'TEXT_EDITOR':
            _visible_line_top(space, line)
        return {'FINISHED'}


def _sequencer_area(context: Any) -> tuple[Any, Any, Any]:
    """``(window, area, region)`` of the biggest Sequencer of the window (``(None, None, None)`` when there is none)."""
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) or getattr(context, "screen", None)
    areas = [a for a in getattr(screen, "areas", ()) if a.type == 'SEQUENCE_EDITOR']
    if not areas:
        return window, None, None
    area = max(areas, key=lambda a: a.width * a.height)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    return window, area, region


def _scene_problem(context: Any, scene: Any) -> str:
    """"" when the Sequencer shows *scene*; else what to tell the artist (nothing may change then)."""
    shown = _seq_scene.sequencer_scene(context)
    if shown is not None and getattr(shown, "name", None) != getattr(scene, "name", None):
        return "the Sequencer shows another scene: switch it to {:s}".format(scene.name)
    return ""


def _apply_retext(scene: Any, state: dict) -> None:
    """The words changed in the text go into the captions of this transcript's clip (holds no strip)."""
    retext = _j(state, "retext", {})
    if not retext:
        return
    try:
        got = view_now(state)
        spans = [(o["frame"], o["frame"] + 1) for o in got["occ"] if str(o["i"]) in retext]
        _update_captions(scene, state, got["words"], got["occ"], retext, set(), spans)
    except _Problem:
        pass


_ASKED: dict[str, str] = {}      # scene name -> transcript path: Make Captions opened the dialog and waits for OK


def captions_made(scene: Any, transcript_path: str) -> None:
    """Auto Captions' dialog (opened by Make Captions) made its captions: the transcript's changed words follow (#595)."""
    name = getattr(scene, "name", "")
    asked = _ASKED.pop(name, None)
    if asked is None or asked != (transcript_path or ""):
        return
    for text in bpy.data.texts:
        state = state_of(text)
        if state and state.get("scene") == name and state.get("transcript", "") == asked:
            _apply_retext(scene, state)
            return


def _toggle_playback(context: Any) -> bool:
    """Start (or stop) playback with the Sequencer as the area the operator sees: the Text Editor
    stays the active area (#595).  False when it couldn't."""
    window, area, region = _sequencer_area(context)
    try:
        if area is not None and region is not None:
            with context.temp_override(window=window, area=area, region=region):
                bpy.ops.screen.animation_play()
        else:
            bpy.ops.screen.animation_play()
    except RuntimeError:
        return False
    return True


class BFACW_OT_transcript_seek(_TextOp, Operator):  # type: ignore[misc]
    """Start or stop playback: plays from the start of the text cursor's line, and a second press stops it"""

    bl_idname = "bfacw.transcript_seek"
    bl_label = "Play from Line"
    bl_description = agent_line(__doc__)

    line: IntProperty(name="Line", default=-1, options={'SKIP_SAVE'})  # type: ignore[valid-type]
    play: BoolProperty(name="Play", default=True, options={'SKIP_SAVE'},  # type: ignore[valid-type]
                       description="Start playback from the line (off: only move the playhead)")

    def execute(self, context):
        text = self.text(context)
        if text is None:
            return {'CANCELLED'}
        playing = bool(getattr(getattr(context, "screen", None), "is_animation_playing", False))
        if self.play and playing:                       # a second press stops
            _toggle_playback(context)
            return {'FINISHED'}
        state = state_of(text)
        scene = bpy.data.scenes.get(state.get("scene", "")) if state else None
        if self.play and scene is not None:
            problem = _scene_problem(context, scene)
            if not problem and _sequencer_area(context)[1] is None and                     getattr(getattr(context, "window", None), "scene", scene) is not scene:
                problem = "this window plays another scene: switch it to {:s}".format(scene.name)
            if problem:
                self.report({'WARNING'}, problem[0].upper() + problem[1:])
                return {'CANCELLED'}
        line = self.line if self.line >= 0 else int(text.current_line_index)
        if self.line >= 0:
            try:
                text.cursor_set(line)
            except (AttributeError, TypeError, RuntimeError):
                pass
        if not seek_line(text, line):
            self.report({'INFO'}, "Line {:d} has no audio behind it".format(line + 1))
            return {'CANCELLED'}
        if self.play and not playing and not _toggle_playback(context):
            self.report({'INFO'}, "The playhead moved, but playback couldn't start here")
        return {'FINISHED'}


class BFACW_OT_transcript_toggle(_TextOp, Operator):  # type: ignore[misc]
    """Turn a transcript option on or off"""

    bl_idname = "bfacw.transcript_toggle"
    bl_label = "Transcript Option"
    bl_description = agent_line(__doc__)

    option: StringProperty(options={'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, props):
        return agent_line({"live": "Live: apply the text by itself a moment after you stop typing (each apply is one undo step)",
                           "follow": "Follow: a click on a line moves the playhead there, and the text scrolls along "
                                     "with playback"}.get(props.option, cls.__doc__))

    def execute(self, context):
        text = self.text(context)
        if text is None or self.option not in ("live", "follow"):
            return {'CANCELLED'}
        state = state_of(text)
        state[self.option] = not bool(state.get(self.option, self.option == "follow"))
        text[te.PROP] = state
        _SEEN.pop(text.name, None)
        _redraw_text_editors()
        return {'FINISHED'}


def _pending_edit(text: Any, state: dict) -> dict | None:
    """The edit the text holds now (cached until it changes: ``_SEEN[name]["edit"]``, its numbers in
    ``["preview"]``), or None when it can't be read.  Reads the whole text: the timer calls it, the
    panel only looks at what is cached."""
    seen = _SEEN.setdefault(text.name, {})
    body = text.as_string()
    h = _hash(body)
    seen["view_hash"] = h
    if seen.get("edit_for") == (h, state.get("applied")):
        return seen.get("edit")
    try:
        plan = _plan(dict(state), body)
        edit, preview = plan["edit"], plan["summary"]
    except (OSError, ValueError, _Problem):
        edit, preview = None, None
    seen["edit_for"] = (h, state.get("applied"))
    seen["edit"] = edit
    seen["preview"] = preview
    return edit


def _cached_edit(text: Any, state: dict) -> tuple[dict | None, dict | None]:
    """``(edit, preview)`` as the timer last made them for the text as it is: no work, for drawing."""
    seen = _SEEN.get(text.name) or {}
    if seen.get("edit_for") == (_cached_hash(text), state.get("applied")):
        return seen.get("edit"), seen.get("preview")
    return None, None


_FILLERS: dict[str, Any] = {"found": [], "text": ""}


class BFACW_OT_transcript_remove_fillers(_TextOp, Operator):  # type: ignore[misc]
    """Cut the filler words ("um", "uh"...) the transcript has, after a list of what it found; one undo step"""

    bl_idname = "bfacw.transcript_remove_fillers"
    bl_label = "Remove Filler Words"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER', 'UNDO'}

    phrases: BoolProperty(name="Also “you know”, “I mean”, “like”...", default=False,  # type: ignore[valid-type]
                          description="Also cut these phrases (they are real words too: check the list)")

    def _find(self, context) -> list:
        text = self.text(context)
        state = state_of(text)
        try:
            got = view_now(state)
        except _Problem as ex:
            self.report({'WARNING'}, str(ex))
            return []
        found = te.find_fillers(got["occ"], got["words"], _j(state, "retext", {}), self.phrases)
        lines = got["view"]["lines"]
        for f in found:
            f["line"] = te.line_of_word(lines, f["words"][0])
        _FILLERS.update(found=found, text=text.name)
        return found

    PENDING = "Apply or undo your text edits first: the filler words are listed from the timeline, and this would apply them too"

    def invoke(self, context, _event):
        text = self.text(context)
        if text is None:
            return {'CANCELLED'}
        if pending(text):
            self.report({'ERROR'}, self.PENDING)
            return {'CANCELLED'}
        found = self._find(context)
        return context.window_manager.invoke_props_dialog(self, width=420, title=self.bl_label,
                                                          confirm_text=self.bl_label if found else "Close")

    def check(self, context):
        self._find(context)
        return True

    def draw(self, context):
        layout = self.layout
        found = _FILLERS["found"]
        layout.prop(self, "phrases")
        box = layout.box()
        if not found:
            col = box.column(align=True)
            col.label(text="No filler words found in the transcript.", icon=safe_icon('INFO'))
            col.label(text="Speech-to-text usually leaves out “um” and “uh”, so the")
            col.label(text="transcript may have none even when they are spoken:")
            col.label(text="Remove Silences catches the pauses they leave.")
            return
        box.label(text="{:s} will be cut:".format(_plural(len(found), "filler word")), icon=safe_icon('X'))
        col = box.column(align=True)
        for f in found[:12]:
            line = f.get("line")
            col.label(text="{:s}  “{:s}”{:s}".format(te.format_mark(f.get("t", 0.0)), f["text"],
                                                     "  (line {:d})".format(line + 1) if line is not None else ""))
        if len(found) > 12:
            col.label(text="... and {:d} more".format(len(found) - 12))

    def execute(self, context):
        text = self.text(context)
        if text is None:
            return {'CANCELLED'}
        if pending(text):
            self.report({'ERROR'}, self.PENDING)
            return {'CANCELLED'}
        found = self._find(context)
        if not found:
            self.report({'INFO'}, "No filler words found: speech-to-text usually leaves out “um” and “uh”")
            return {'CANCELLED'}
        state = state_of(text)
        doomed = [w for f in found for w in f["words"]]
        body = te.remove_tokens(text.as_string(), _j(state, "tokens", []), doomed)
        try:
            plan = _plan(state, body)
        except _Problem as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        extra = te.only_listed(plan["edit"], doomed, {o["i"] for o in plan["got"]["occ"]})
        if extra:                                       # it asks for no confirmation: it may only cut what it listed
            self.report({'ERROR'}, "Remove Filler Words would also {:s}: nothing was changed (edit the text, or "
                        "cut them by hand)".format(extra))
            return {'CANCELLED'}
        out = apply_text(text, body, confirm=True)
        if out["status"] == "ok":
            self.report({'INFO'}, "Removed {:s}: {:s}".format(_plural(len(found), "filler word"), out["message"]))
            return {'FINISHED'}
        self.report({'WARNING'} if out["status"] in ("refused", "nothing") else {'ERROR'}, out["message"])
        return {'FINISHED'} if out.get("changed") else {'CANCELLED'}


class BFACW_OT_transcript_remove_silences(_TextOp, Operator):  # type: ignore[misc]
    """Remove Silences on this transcript's clip (the quiet parts out of the whole edit), then refresh the text"""

    bl_idname = "bfacw.transcript_remove_silences"
    bl_label = "Remove Silences"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        text = self.text(context)
        if text is not None and pending(text):
            return context.window_manager.invoke_confirm(
                self, event, title=self.bl_label,
                message="The text has edits not applied yet: they are dropped. Go on?")
        return self.execute(context)

    def execute(self, context):
        text = self.text(context)
        state = state_of(text) if text is not None else None
        scene = bpy.data.scenes.get(state.get("scene", "")) if state else None
        if scene is None:
            return {'CANCELLED'}
        seq = _seq()
        settings = tuple(seq.SPEC.param(n).default for n in ("threshold_db", "min_silence", "padding", "min_keep"))
        names = [s.name for s in source_strips(scene, state["source"])]
        out = seq.remove_silences(scene, settings, names, True, True, context=context)
        if out["status"] != "ok":
            self.report({'INFO'} if out["status"] == "nothing" else {'WARNING'}, out["message"])
            return {'FINISHED'} if out.get("changed") else {'CANCELLED'}
        refresh_text(text)
        self.report({'INFO'}, out["message"])
        return {'FINISHED'}


class BFACW_OT_transcript_make_captions(_TextOp, Operator):  # type: ignore[misc]
    """Auto Captions for this transcript's clip, with its changed words"""

    bl_idname = "bfacw.transcript_make_captions"
    bl_label = "Make Captions"
    bl_description = agent_line(__doc__, also="Needs Speech set up once (Preferences) to transcribe.")
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, _event):
        """The dialog of Strip > Auto Captions (style, animation, position), for this transcript's clip (#595)."""
        text = self.text(context)
        state = state_of(text) if text is not None else None
        scene = bpy.data.scenes.get(state.get("scene", "")) if state else None
        if scene is None or scene.sequence_editor is None:
            return {'CANCELLED'}
        problem = _scene_problem(context, scene)
        if problem:
            self.report({'WARNING'}, problem[0].upper() + problem[1:])
            return {'CANCELLED'}
        window, area, region = _sequencer_area(context)
        if area is None:
            self.report({'WARNING'}, "Open a Sequencer in this window to make captions")
            return {'CANCELLED'}
        sources = {s.name for s in source_strips(scene, state["source"])}
        for strip in scene.sequence_editor.strips:
            strip.select = strip.name in sources
        _ASKED[scene.name] = state.get("transcript", "")
        try:
            with context.temp_override(window=window, area=area, region=region):
                bpy.ops.bfacw.auto_captions('INVOKE_DEFAULT', transcript_path=state.get("transcript", ""))
        except RuntimeError as ex:
            _ASKED.pop(scene.name, None)
            self.report({'WARNING'}, str(ex).strip().splitlines()[-1] if str(ex).strip() else "Auto Captions can't run here")
            return {'CANCELLED'}
        return {'FINISHED'}

    def execute(self, context):
        text = self.text(context)
        state = state_of(text) if text is not None else None
        scene = bpy.data.scenes.get(state.get("scene", "")) if state else None
        if scene is None:
            return {'CANCELLED'}
        try:
            from . import captions_ops
            out = captions_ops.add_captions(scene, source_strips(scene, state["source"]),
                                            transcript_path=state.get("transcript", ""))
        except Exception as ex:  # pylint: disable=broad-exception-caught
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        _apply_retext(scene, state)
        self.report({'INFO'}, "{:s} made".format(_plural(int(out.get("lines_created", 0)), "caption")))
        return {'FINISHED'}


# -- the Text Editor: panel, header, overlay ---------------------------------------------------------

def _draw_flags(layout: Any, text: Any, flags: list) -> None:
    box = layout.box()
    col = box.column(align=True)
    col.label(text="{:s} flagged: nothing cut there".format(_plural(len(flags), "line")), icon=safe_icon('ERROR'))
    for f in flags[:8]:
        row = col.row(align=True)
        op = row.operator(BFACW_OT_transcript_seek.bl_idname, text="Line {:d}".format(f["line"] + 1),
                          icon=safe_icon('TRIA_RIGHT'), emboss=False)
        op.line = f["line"]
        op.play = False                     # a flagged line: the playhead goes there, nothing plays
        op.text_name = text.name
        row.label(text="“{:s}”".format(f["text"][:28] + ("..." if len(f["text"]) > 28 else "")))
    if flags:
        col.label(text={"no_audio": "No audio behind it",
                        "ambiguous": "The same words more than once: which one?"}.get(
                            flags[0]["reason"], "It no longer matches what is said"))


class BFACW_PT_transcript(Panel):  # type: ignore[misc]
    """Edit by Transcript in the Text Editor's sidebar (only for transcript texts)"""

    bl_label = WHAT
    bl_idname = "BFACW_PT_transcript"
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return _context_text(context) is not None

    def draw(self, context):
        layout = self.layout
        text = _context_text(context)
        state = state_of(text)
        col = layout.column(align=True)
        col.label(text=os.path.basename(state.get("path", "")), icon=safe_icon('FILE_MOVIE'))
        col.label(text="Scene: {:s}".format(state.get("scene", "")), icon=safe_icon('SEQUENCE'))
        is_pending = _is_pending(text, state)
        edit, preview = _cached_edit(text, state) if is_pending else (None, None)       # cached: no work per redraw
        box = layout.box()
        if not is_pending:
            box.label(text="The text matches the timeline", icon=safe_icon('CHECKMARK'))
        elif edit is None or preview is None:
            box.label(text="Checking your edit...", icon=safe_icon('TIME'))
        elif edit["changed"]:
            bits = []
            if preview["cut_words"]:
                bits.append("{:d} cut ({:.1f} s)".format(preview["cut_words"], preview["seconds"]))
            if preview["retexted"]:
                bits.append("{:d} changed".format(preview["retexted"]))
            if preview["moved"]:
                bits.append("moved")
            box.label(text="To apply: " + ", ".join(bits), icon=safe_icon('MODIFIER'))
            if preview["needs_confirm"]:
                box.label(text="{:d}% of the clip: Apply asks first".format(preview["percent"]), icon=safe_icon('ERROR'))
        else:
            box.label(text="Nothing to apply", icon=safe_icon('INFO'))
        note = (_SEEN.get(text.name) or {}).get("live_note") if is_pending else None
        if note:
            box.label(text=note, icon=safe_icon('TIME'))
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(BFACW_OT_transcript_apply.bl_idname, icon=safe_icon('CHECKMARK'))
        row = layout.row(align=True)
        row.operator(BFACW_OT_transcript_toggle.bl_idname, text="Live", icon=safe_icon('REC'),
                     depress=bool(state.get("live"))).option = "live"
        row.operator(BFACW_OT_transcript_toggle.bl_idname, text="Follow", icon=safe_icon('LINKED'),
                     depress=bool(state.get("follow", True))).option = "follow"
        col = layout.column(align=True)
        col.operator(BFACW_OT_transcript_jump_to_playhead.bl_idname, icon=safe_icon('PLAY'))
        col.operator(BFACW_OT_transcript_seek.bl_idname, icon=safe_icon('TIME'))
        col.operator(BFACW_OT_transcript_refresh.bl_idname, icon=safe_icon('FILE_REFRESH'))
        flags = edit.get("flags") if edit is not None else ([] if is_pending else [
            dict(f, line=f["line"] - 1) for f in _j(state, "flags", [])])
        if flags:
            _draw_flags(layout, text, flags)
        layout.separator()
        _wands_header(layout)
        col = layout.column(align=True)
        # what makes something first, what cuts words or sound away last (wand rules, #610)
        _wand(col, BFACW_OT_transcript_make_captions.bl_idname, "Make Captions", "captions", "FONT_DATA")
        _wand(col, BFACW_OT_transcript_remove_silences.bl_idname, "Remove Silences...", "remove_silences", "SPEAKER")
        _wand(col, BFACW_OT_transcript_remove_fillers.bl_idname, "Remove Filler Words...", "remove_silences",
              "X")


def _wands_header(layout: Any) -> None:
    """The section header of the wands: the wand icon, else a Blender one (a header has an icon, #610)."""
    value = 0
    try:
        from .wand import icons
        value = icons.icon_id("wand")
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    if value:
        layout.label(text="Wands", icon_value=value)
    else:
        layout.label(text="Wands", icon=safe_icon('SHADERFX'))


def _wand(layout: Any, idname: str, text: str, icon: str, fallback: str) -> None:
    try:
        from .wand import icons
        icons.operator(layout, idname, name=icon, fallback=fallback, text=text)
    except Exception:  # pylint: disable=broad-exception-caught
        layout.operator(idname, text=text, icon=safe_icon(fallback))


def _header_menus() -> Any:
    """Where the header buttons go: right after the Text Editor's menus (View ... Templates), so
    they show even when the header is too narrow for all of it; the header itself as a fallback."""
    return getattr(bpy.types, "TEXT_MT_editor_menus", None) or getattr(bpy.types, "TEXT_HT_header", None)


def _draw_header(self: Any, context: Any) -> None:
    text = _context_text(context)
    if text is None:
        return
    state = state_of(text)
    layout = self.layout
    layout.separator()
    row = layout.row(align=True)
    try:
        row.emboss = 'NORMAL'               # buttons, not menu titles, among the header's menus
    except (AttributeError, TypeError):
        pass
    row.operator(BFACW_OT_transcript_apply.bl_idname, text="Apply", icon=safe_icon('CHECKMARK'))
    row.operator(BFACW_OT_transcript_make_captions.bl_idname, text="", icon=safe_icon('FONT_DATA'))   # #595
    row.operator(BFACW_OT_transcript_toggle.bl_idname, text="Live", icon=safe_icon('REC'),
                 depress=bool(state.get("live"))).option = "live"
    row.operator(BFACW_OT_transcript_jump_to_playhead.bl_idname, text="", icon=safe_icon('PLAY'))


_FLAG_RGBA = (0.95, 0.35, 0.25, 0.85)
_PLAY_RGBA = (0.35, 0.6, 1.0, 0.18)


def _draw_overlay() -> None:
    """The line playing (a band) and the flagged lines (a red mark at the left and a note)."""
    try:
        context = bpy.context
        space = context.space_data
        text = getattr(space, "text", None)
        state = state_of(text)
        if not state:
            return
        region = context.region
        flagged: dict[int, str] = {}
        if _is_pending(text, state):
            edit = _SEEN.get(text.name, {}).get("edit")
            for f in (edit or {}).get("flags") or ():
                flagged.setdefault(f["line"], f["reason"])
        else:
            for f in _j(state, "flags", []):
                flagged.setdefault(int(f["line"]) - 1, f["reason"])
        playing = playing_line(text, state)
        if playing is None and not flagged:
            return
        import gpu  # pylint: disable=import-error
        from gpu_extras.batch import batch_for_shader  # pylint: disable=import-error
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        height = _line_height(space)

        def band(line: int, x0: float, x1: float, rgba: tuple) -> float | None:
            x, y = space.region_location_from_cursor(line, 0)
            if y < -height or y > region.height + height:
                return None
            top = y + height * 0.8
            bottom = top - height
            verts = ((x0, bottom), (x1, bottom), (x1, top), (x0, top))
            batch = batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=((0, 1, 2), (0, 2, 3)))
            shader.uniform_float("color", rgba)
            batch.draw(shader)
            return bottom

        if playing is not None:
            band(playing, 0, region.width, _PLAY_RGBA)
        if flagged:
            import blf  # pylint: disable=import-error
            for line, reason in flagged.items():
                bottom = band(line, 0, 4 * context.preferences.system.ui_scale, _FLAG_RGBA)
                if bottom is None:
                    continue
                note = {"no_audio": "no audio", "ambiguous": "which one?"}.get(reason, "doesn't match")
                blf.size(0, 11 * context.preferences.system.ui_scale)
                w, _h = blf.dimensions(0, note)
                blf.color(0, *_FLAG_RGBA)
                blf.position(0, region.width - w - 24, bottom + height * 0.25, 0)
                blf.draw(0, note)
        gpu.state.blend_set('NONE')
    except Exception:  # pylint: disable=broad-exception-caught
        pass                                # never break the editor's drawing


def _line_height(space: Any) -> float:
    try:
        a = space.region_location_from_cursor(0, 0)[1]
        b = space.region_location_from_cursor(1, 0)[1]
        if a - b > 4:
            return float(a - b)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    return float(space.font_size) * bpy.context.preferences.system.ui_scale * 1.3


def _redraw_text_editors() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        for area in window.screen.areas:
            if area.type == 'TEXT_EDITOR':
                area.tag_redraw()


# -- the timer: Live, Follow, the timeline changed ---------------------------------------------------

def _transcript_areas() -> list[tuple[Any, Any, Any]]:
    out = []
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        screen = window.screen
        for area in screen.areas:
            if area.type != 'TEXT_EDITOR':
                continue
            text = area.spaces.active.text
            if text is not None and state_of(text):
                out.append((window, area, text))
    return out


def _push_undo(window: Any, name: str) -> None:
    try:
        with bpy.context.temp_override(window=window):
            bpy.ops.ed.undo_push(message=name)
    except (RuntimeError, TypeError, AttributeError):
        pass


def _tokens_in(small: list[str], big: list[str]) -> bool:
    n = len(small)
    return n > 0 and any(big[k:k + n] == small for k in range(len(big) - n + 1))


def _live_hold(window: Any, edit: dict | None) -> str:
    """Why Live must wait now ("" when it can go): a render is running (or a clip is playing), or
    the words the text lost are on the clipboard (Ctrl+X: the paste comes next, a line cut to be
    moved must not be cut before it is pasted)."""
    try:
        if bpy.app.is_job_running("RENDER"):
            return "Live waits: a render is running"
    except (AttributeError, TypeError, ValueError):
        pass
    if getattr(getattr(window, "screen", None), "is_animation_playing", False):
        return "Live waits: the timeline is playing"
    if not edit or not edit.get("cut"):
        return ""
    try:
        clip = str(bpy.context.window_manager.clipboard or "")
    except (AttributeError, RuntimeError):
        clip = ""
    if not clip.strip():
        return ""
    on_clip: list[str] = []
    for line in clip.splitlines():
        on_clip.extend(te.norm(t) for t in te.MARK_RE.sub("", line, count=1).split())
    for op in edit.get("ops") or ():
        if op.get("op") != "cut":
            continue
        cut_tokens = [te.norm(t) for t in str(op.get("text", "")).split()]
        # the cut is (or is part of) what is on the clipboard, or the clipboard is part of the cut: two lines cut
        # side by side, one of them Ctrl+X'd to be pasted elsewhere, are one cut of both
        if _tokens_in(cut_tokens, on_clip) or _tokens_in(on_clip, cut_tokens):
            return "Live waits: the text you cut is on the clipboard (paste it, or press Apply)"
    return ""


def _tick() -> float:
    try:
        return _tick_inner()
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] {:s} timer: {!s}".format(WHAT, ex))
        return 1.0


def _tick_inner() -> float:
    areas = _transcript_areas()
    if not areas:
        return 1.0
    now = time.monotonic()
    done: set[str] = set()
    for window, area, text in areas:
        if text.name in done:
            continue
        done.add(text.name)
        state = state_of(text)
        seen = _SEEN.setdefault(text.name, {})
        body_hash = _hash(text.as_string())
        seen["view_hash"] = body_hash                   # what the panel and the overlay read, so they never hash the text
        line = int(text.current_line_index)
        if seen.get("hash") != body_hash:
            seen.update(hash=body_hash, changed_at=now, cursor=line, live_note=None)
            continue
        changed_at = seen.get("changed_at", 0.0)
        is_pending = body_hash != state.get("applied")
        # the panel's preview, once the typing stops; then one redraw, so the flagged lines are marked
        if is_pending and now - changed_at >= CHECK_PAUSE_S and seen.get("drawn_for") != body_hash:
            _pending_edit(text, state)
            seen["drawn_for"] = body_hash
            area.tag_redraw()
        # Live: apply after a pause (longer for whole lines), never mid-render, never a cut the clipboard still holds
        if state.get("live") and is_pending and seen.get("live_tried") != body_hash:
            edit = _pending_edit(text, state)
            wait = LIVE_LINE_PAUSE_S if edit and edit.get("lines_cut") else LIVE_PAUSE_S
            if now - changed_at >= wait:
                held = _live_hold(window, edit)
                if held:
                    if seen.get("live_note") != held:
                        seen["live_note"] = held
                        area.tag_redraw()
                    continue
                seen["live_tried"] = body_hash
                if edit and edit["changed"]:
                    with bpy.context.temp_override(window=window, area=area):
                        out = apply_text(text, keep_text=True)
                    if out["status"] == "ok" or out.get("changed"):
                        _push_undo(window, WHAT)
                    if out["status"] == "confirm":
                        seen["live_note"] = "Live won't cut {:d}% of the clip: press Apply".format(out.get("percent", 0))
                    else:
                        seen["live_note"] = None
                    print("[Coworker] {:s} (Live): {:s}".format(WHAT, out.get("message", "")))
                    seen = _SEEN.setdefault(text.name, {})
                    seen.update(hash=_hash(text.as_string()), cursor=int(text.current_line_index))
                    area.tag_redraw()
            continue
        # Follow: a click on another line (the text unchanged) moves the playhead
        if line != seen.get("cursor"):
            seen["cursor"] = line
            playing = getattr(window.screen, "is_animation_playing", False)
            no_selection = int(getattr(text, "select_end_line_index", line)) == line
            if state.get("follow", True) and not playing and no_selection:
                seek_line(text, line)
            continue
        # The timeline changed (a cut in the Sequencer, an undo): the text follows when nothing is pending
        if not is_pending and now - seen.get("sig_checked", 0.0) > 0.5:
            seen["sig_checked"] = now
            scene = bpy.data.scenes.get(state.get("scene", ""))
            if scene is not None and scene.sequence_editor is not None:
                sig = _signature([_piece(s) for s in source_strips(scene, state["source"])])
                if sig != state.get("sig"):
                    out = refresh_text(text)
                    if out["status"] == "ok":
                        _SEEN.setdefault(text.name, {}).update(hash=_hash(text.as_string()),
                                                               cursor=int(text.current_line_index))
                        area.tag_redraw()
    return _TICK_S


@bpy.app.handlers.persistent
def _on_frame(scene: Any, _depsgraph: Any = None) -> None:
    """Playback: the playing line's band moves, and with Follow the text scrolls along."""
    try:
        for window, area, text in _transcript_areas():
            state = state_of(text)
            if state.get("scene") != scene.name:
                continue
            area.tag_redraw()
            if state.get("follow", True) and getattr(window.screen, "is_animation_playing", False):
                line = playing_line(text, state)
                if line is not None:
                    _visible_line_top(area.spaces.active, line)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


@bpy.app.handlers.persistent
def _on_load(*_args: Any) -> None:
    _SEEN.clear()
    _WORDS.clear()


# -- the agent tools ---------------------------------------------------------------------------------

def _tool_scene() -> Any:
    return _seq()._tool_scene()  # pylint: disable=protected-access


def _tool_strip(scene: Any, name: str | None) -> tuple[Any, str]:
    ed = scene.sequence_editor
    if ed is None:
        raise _Problem("The Sequencer's scene “{:s}” has no strips".format(scene.name))
    if name:
        strip = ed.strips_all.get(name)
        if strip is None:
            raise _Problem("No strip named “{:s}”".format(name))
        if strip.type not in {'SOUND', 'MOVIE'}:
            raise _Problem("“{:s}” isn't a sound or movie strip".format(name))
        return strip, ""
    active = ed.active_strip
    if active is not None and active.type in {'SOUND', 'MOVIE'}:
        return active, ""
    found, seen = [], set()
    for s in ed.strips:
        if s.type in {'SOUND', 'MOVIE'}:
            path = _norm(strip_path(s))
            if path and path not in seen:
                seen.add(path)
                if find_transcript(strip_path(s)):
                    found.append(s)
    if len(found) == 1:
        return found[0], ""
    if not found:
        raise _Problem("No clip in “{:s}” has a transcript yet: transcribe one first (transcribe_strip)".format(
            scene.name))
    raise _Problem("Several clips have transcripts ({:s}): name the strip".format(
        ", ".join("“{:s}”".format(s.name) for s in found[:5])))


def tool_get_transcript_text(strip: str | None = None) -> dict[str, Any]:
    """``get_transcript_text`` (MCP): the transcript of a clip as it plays on the timeline now,
    one line per sentence, with line numbers and times (the same lines ``cut_lines`` of
    :func:`tool_apply_transcript_edit` counts) and a ``version`` (a hash of the text: ``cut_lines``
    must come with it, so a cut never lands on lines that moved).  Changes nothing."""
    scene = _tool_scene()
    try:
        s, _note = _tool_strip(scene, strip)
        media = strip_path(s)
        source = _norm(media)
        text = text_for(scene, source)
        if text is not None:
            state = dict(state_of(text))
        else:
            path = find_transcript(media)
            if not path:
                raise _Problem(NOT_TRANSCRIBED.format(s.name))
            state = {"source": source, "path": media, "scene": scene.name, "transcript": path, "retext": "{}"}
        got = view_now(state)
        body = got["view"]["text"]
        fps, start = got["fps"], scene.frame_start
        rows = body.splitlines()
        lines = [{"line": ln["line"] + 1, "frame": ln["frame"],
                  "time": te.format_mark((ln["frame"] - start) / fps).strip("[]"),
                  "text": te.MARK_RE.sub("", rows[ln["line"]], count=1).strip()}
                 for ln in got["view"]["lines"]]
        name = text.name if text is not None else ""
        is_pending = pending(text) if text is not None else False
    except _Problem as ex:
        return {"status": "error", "message": str(ex)}
    return {"status": "ok", "strip": s.name, "scene": scene.name, "media": os.path.basename(media),
            "text_block": name, "pending_edits": is_pending, "lines": lines, "text": body, "version": _hash(body),
            "message": "{:s} of “{:s}”; edit it with apply_transcript_edit".format(_plural(len(lines), "line"),
                                                                                 os.path.basename(media))}


def _plain_line(row: str) -> str:
    """A line of the text as the tool compares it: no time mark, one space between words."""
    return " ".join(te.MARK_RE.sub("", row, count=1).split())


def _stale(body: str, block_name: str, why: str) -> dict[str, Any]:
    return {"status": "refused", "message": "{:s}: read it again (the current text is in text_now) and send the "
            "edit for that".format(why), "text_now": body, "version": _hash(body), "text_block": block_name}


def tool_apply_transcript_edit(strip: str | None = None, text: str | None = None, cut_lines: Any = None,
                               dry_run: bool = False, version: str | None = None, lines: Any = None,
                               confirm_large_cut: bool = False) -> dict[str, Any]:
    """``apply_transcript_edit`` (MCP): edit the clip by editing its transcript text, one undo step.

    ``cut_lines`` (or ``lines``) cut whole lines and must come with what the caller saw: the
    ``version`` ``get_transcript_text`` returned, or ``lines`` = ``[{"line", "text"}]`` for the
    lines to cut; if the text isn't the one they were read from the answer is ``refused`` with the
    current text.  ``cut_lines`` with ``text`` is refused.  A dry run creates nothing and writes
    nothing.  An edit that cuts more than half the clip needs ``confirm_large_cut``."""
    if (cut_lines or lines) and text is not None:
        return {"status": "error", "message": "Give either the edited text (text) or the lines to cut (cut_lines / "
                "lines), not both: nothing was changed"}
    scene = _tool_scene()
    try:
        s, _note = _tool_strip(scene, strip)
        media = strip_path(s)
        block = text_for(scene, _norm(media))
        if block is not None and pending(block):
            raise _Problem("The transcript text “{:s}” has edits that aren't applied yet: Apply or Refresh it in "
                           "the Text Editor first".format(block.name))
        # the text as the timeline shows it now, in memory only: everything is checked on it first, so a call
        # that is refused creates, refreshes and drops nothing (the artist's text block stays as it is)
        if block is not None:
            state = dict(state_of(block))
        else:
            path = find_transcript(media)
            if not path:
                raise _Problem(NOT_TRANSCRIBED.format(s.name))
            state = {"source": _norm(media), "path": media, "scene": scene.name, "transcript": path,
                     "retext": "{}"}
        got = view_now(state)
        _state_from_view(state, got)
        target: Any = _Virtual(block.name if block is not None else "", state, got["view"]["text"])
    except _Problem as ex:
        return {"status": "error", "message": str(ex)}
    body = target.as_string()
    now_version = _hash(body)
    if version and version != now_version:
        return _stale(body, target.name, "The transcript changed since you read it")
    if cut_lines or lines:
        wanted: set[int] = set()
        shown: dict[int, str] = {}
        try:
            if isinstance(cut_lines, (int, str)):
                cut_lines = [cut_lines]
            wanted.update(int(n) for n in (cut_lines or ()))
            for item in lines or ():
                wanted.add(int(item["line"]))
                shown[int(item["line"])] = str(item.get("text", ""))
        except (TypeError, ValueError, KeyError, AttributeError):
            return {"status": "error", "message": "cut_lines are line numbers (1 = the first line); lines are "
                    "[{\"line\": 3, \"text\": \"...\"}]"}
        rows = body.splitlines()
        bad = sorted(n for n in wanted if not 1 <= n <= len(rows))
        if bad:
            return {"status": "error", "message": "No line {:s}: the text has {:d} lines".format(
                ", ".join(str(n) for n in bad), len(rows))}
        if not version:
            unchecked = sorted(n for n in wanted if n not in shown)
            if unchecked:
                return {"status": "error", "message": "Give the version get_transcript_text returned, or lines with "
                        "the text of line {:s} as you read it: cutting by number alone could cut the wrong "
                        "lines. Nothing was changed".format(", ".join(str(n) for n in unchecked)),
                        "version": now_version}
        for n, said in shown.items():
            if " ".join(said.split()).casefold() != _plain_line(rows[n - 1]).casefold():
                return _stale(body, target.name, "Line {:d} isn't what you read".format(n))
        body = "\n".join(r for n, r in enumerate(rows, 1) if n not in wanted) + "\n"
    elif text is not None:
        body = str(text)
    else:
        return {"status": "error", "message": "Give the edited text (text) or the lines to cut (cut_lines with the "
                "version of get_transcript_text, or lines)"}
    snapshot = None
    if not dry_run:
        try:
            plan = _plan(state, body)                   # what it would do, before anything is touched
        except _Problem as ex:
            return {"status": "error", "message": str(ex)}
        if plan["needs_confirm"] and not confirm_large_cut and (plan["cut"] or plan["edit"]["cut"]):
            out = dict(plan["summary"], status="refused", needs_confirm=True, text=target.name)
            out["message"] = ("This cuts {:.1f} s ({:d}% of the clip): confirm it with confirm_large_cut or edit "
                              "less (nothing was changed)").format(plan["seconds"], plan["summary"]["percent"])
            out.update(strip=s.name, scene=scene.name, text_block=target.name)
            return out
        if block is not None:
            snapshot = (block, block.as_string(), dict(state_of(block)))
        try:
            target, _note = open_transcript(scene, s)
        except _Problem as ex:
            return {"status": "error", "message": str(ex)}
    out = apply_text(target, body, keep_text=False, dry_run=bool(dry_run), confirm=bool(confirm_large_cut))
    if not dry_run and out["status"] in ("refused", "confirm", "error") and not out.get("changed"):
        _undo_open(scene, s, target, snapshot)           # nothing was applied: the text block is as it was
        out.update(note="Nothing was changed")
    if out["status"] == "confirm":
        out.update(status="refused", needs_confirm=True)
        out["message"] += " (nothing was changed)"
    if not dry_run and (out["status"] == "ok" or out.get("changed")):
        _seq()._push_undo(WHAT)  # pylint: disable=protected-access
        if out.get("flags"):
            refresh_text(target)
            out["note"] = "The flagged text wasn't applied; the transcript text shows the timeline now"
    if not dry_run and out["status"] in ("ok", "nothing"):
        out["text_now"] = target.as_string()
        out["version"] = _hash(out["text_now"])
    out.update(strip=s.name, scene=scene.name, text_block=target.name)
    return out


def _undo_open(scene: Any, strip: Any, target: Any, snapshot: tuple | None) -> None:
    """A tool call that applied nothing leaves the transcript text as it was before the call: the text block it
    made is removed, the one it refreshed gets its text and link back."""
    try:
        if snapshot is None:
            for st in source_strips(scene, state_of(target)["source"]) if state_of(target) else ():
                try:
                    del st[te.STRIP_PROP]
                except (KeyError, TypeError, AttributeError):
                    pass
            _SEEN.pop(target.name, None)
            bpy.data.texts.remove(target)
        else:
            block, body, state = snapshot
            block.from_string(body)
            block[te.PROP] = state
            _SEEN.pop(block.name, None)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] {:s}: couldn't put the text back: {!s}".format(WHAT, ex))


# -- placement -------------------------------------------------------------------------------------

_CLASSES = (BFACW_OT_open_transcript, BFACW_OT_transcript_apply, BFACW_OT_transcript_refresh,
            BFACW_OT_transcript_jump_to_playhead, BFACW_OT_transcript_seek, BFACW_OT_transcript_toggle,
            BFACW_OT_transcript_remove_fillers, BFACW_OT_transcript_remove_silences,
            BFACW_OT_transcript_make_captions, BFACW_PT_transcript)
_REGISTERED: list[type] = []


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
        _REGISTERED.append(cls)
    header = _header_menus()
    if header is not None:
        header.append(_draw_header)
    space = getattr(bpy.types, "SpaceTextEditor", None)
    if space is not None and not _DRAW:
        _DRAW.append(space.draw_handler_add(_draw_overlay, (), 'WINDOW', 'POST_PIXEL'))
    if _on_frame not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=1.0, persistent=True)


def unregister() -> None:
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for handlers, fn in ((bpy.app.handlers.frame_change_post, _on_frame), (bpy.app.handlers.load_post, _on_load)):
        while fn in handlers:
            handlers.remove(fn)
    space = getattr(bpy.types, "SpaceTextEditor", None)
    while _DRAW:
        handle = _DRAW.pop()
        try:
            space.draw_handler_remove(handle, 'WINDOW')
        except (ValueError, RuntimeError, AttributeError):
            pass
    header = _header_menus()
    if header is not None:
        try:
            header.remove(_draw_header)
        except (ValueError, RuntimeError):
            pass
    for cls in reversed(_REGISTERED):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _REGISTERED.clear()
    _SEEN.clear()
    _WORDS.clear()
