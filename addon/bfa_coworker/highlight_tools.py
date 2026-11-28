# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Highlights H6 (#616): the chat tools, on every engine.

* ``suggest_highlights(kind, max_seconds, count)`` starts the search (the panel's Find, the same worker and the same
  progress line) as a **background job** and answers at once with a ``job_id``; the agent waits for it with
  ``get_job_status`` (:mod:`long_jobs`, #309).  A run takes minutes, which a tool call must not block.
* ``read_edit_sentences`` (H1, ``scene_reader_ops``) and ``propose_highlights(picks)`` are the same pipeline in two
  steps, for an agent that reads the sentences itself (the External Agent path, where the add-on cannot ask a model
  directly).  The proposals go through the checks of the search (:mod:`highlight_agent`) and land in the same picks
  list on the scene.
* ``make_short(pick, steps)`` builds a pick as its own scene (H5, :mod:`highlight_short`).  It does not switch the
  artist's window to the new scene, so an agent can build several shorts from the same long edit.
* :func:`hand_to_agent`: with an External Agent or Harness the panel's button cannot search by itself.  It hands the
  job to the agent as a prepared message that uses these tools (an External Harness has no chat in Blender: the message
  goes to the clipboard).

All of these run on the main thread (the bridge).  The picks list, its checks and Make Short are the panel's own code.
"""

from __future__ import annotations

__all__ = (
    "hand_to_agent",
    "tool_make_short",
    "tool_propose_highlights",
    "tool_suggest_highlights",
)

from typing import Any

import bpy  # pylint: disable=import-error

from . import highlight_agent as ha
from . import highlight_picks as hp
from . import highlight_picks_ops as ops
from . import scene_reader_ops
from . import scene_reader as sr
from .wand import sequencer as _wand

JOB_KIND = "HIGHLIGHTS"
JOB_LABEL = "Find highlights"
SECONDS = (15.0, 60.0)
MAX_COUNT = 10


def _scene() -> Any:
    return scene_reader_ops._ops()._tool_scene()      # pylint: disable=protected-access


def _running_message() -> str:
    return ("A highlights search is running (get_job_status shows it). Wait for it to end, or ask the user to press "
            "Stop in the Highlights panel.") if ops._JOB["running"] else ""     # pylint: disable=protected-access


def _error(message: str, **more: Any) -> dict[str, Any]:
    return dict({"status": "error", "message": message}, **more)


def _store_rows(scene: Any, store: dict[str, Any]) -> list[dict[str, Any]]:
    return ha.rows(store, _wand._fps(scene), scene.frame_start)      # pylint: disable=protected-access


# -- suggest_highlights: a background job -----------------------------------------------------------

class _Search:
    """The job's checker (:mod:`long_jobs`): ``None`` while the search runs, then its result.  The search itself is the
    panel's (``highlight_picks_ops``); this only watches it."""

    job_kind = JOB_KIND
    job_label = JOB_LABEL

    def __init__(self, job_id: int, scene_name: str) -> None:
        self.job_id = job_id
        self.scene_name = scene_name

    def job_progress(self) -> tuple[float | None, str, str]:
        text = str(ops._JOB["progress"] or "")      # pylint: disable=protected-access
        return None, text, text[:40]

    def __call__(self) -> dict[str, Any] | None:
        job = ops._JOB      # pylint: disable=protected-access
        if job["id"] != self.job_id:
            return {"status": "cancelled", "message": "The search was dropped (another file was loaded)."}
        if job["running"]:
            thread = job["thread"]
            if thread is not None and not thread.is_alive():
                ops._job_tick()      # pylint: disable=protected-access  # the worker is over and nobody took its answer
            if job["running"]:
                return None
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is None:
            return _error("The scene “{:s}” is gone.".format(self.scene_name))
        store = ops.load(scene)
        report = job.get("report") or {}
        out: dict[str, Any] = {
            "status": "error" if job["level"] == "error" else "ok",
            "message": job["message"] or "The search ended.", "picks": _store_rows(scene, store), "scene": scene.name,
            "report": {k: report[k] for k in ("sentences", "windows", "windows_failed", "requests", "retries", "stopped",
                                              "kind", "seconds_taken", "messages") if k in report}}
        if out["picks"]:
            out["next"] = ("Say which pick suits the user and call make_short(pick) with its id, or give them the list. "
                           "Nothing is built until make_short.")
        return out


def tool_suggest_highlights(kind: str = "auto", max_seconds: float = 60.0, count: int = 5) -> dict[str, Any]:
    """``suggest_highlights`` (MCP): start the search for shorts in the Sequencer's edit, answer with a ``job_id``."""
    kind = str(kind or "auto").strip().lower()
    if kind not in ("auto", "podcast", "tutorial", "talk"):
        return _error("kind is auto, podcast, tutorial or talk, not '{:s}'.".format(kind[:30]))
    try:
        seconds = max(SECONDS[0], min(SECONDS[1], float(max_seconds)))
        count = max(1, min(MAX_COUNT, int(count)))
    except (TypeError, ValueError):
        return _error("max_seconds and count are numbers (up to 60 seconds, up to {:d} picks).".format(MAX_COUNT))
    scene = _scene()
    from . import long_jobs
    try:
        sentences = ops.start_search(scene, seconds, kind, count, source="agent")
    except ops.SearchError as ex:
        if ex.kind == "external":
            return _error(
                "{:s} Read the speech yourself instead: read_edit_sentences (every page), choose the pieces, then "
                "propose_highlights with your picks.".format(str(ex)), mode="read_and_propose")
        return _error(str(ex), **({"job_running": True} if ex.kind == "busy" else {}))
    job_id = long_jobs.start(JOB_KIND, JOB_LABEL, _Search(int(ops._JOB["id"]), scene.name))      # pylint: disable=protected-access
    ops._redraw()      # pylint: disable=protected-access
    text, _where = ops._engine()      # pylint: disable=protected-access
    return {"status": "ok", "job_id": job_id, "scene": scene.name, "sentences": sentences,
            "message": "Searching {:d} sentences for up to {:d} shorts of at most {:.0f} s. This takes minutes: call "
                       "get_job_status(job_id, wait_seconds=240) until it is done; its result lists the picks.".format(
                           sentences, count, seconds),
            "privacy": text}


# -- propose_highlights: the agent's own picks --------------------------------------------------------

def tool_propose_highlights(picks: Any = None, kind: str = "auto", max_seconds: float = 60.0, version: str = "",
                            replace: bool = False) -> dict[str, Any]:
    """``propose_highlights`` (MCP): picks the agent chose from ``read_edit_sentences`` go through the search's
    checks into the scene's picks list."""
    busy = _running_message()
    if busy:
        return _error(busy)
    kind = str(kind or "auto").strip().lower()
    if kind not in ("auto", "podcast", "tutorial", "talk"):
        return _error("kind is auto, podcast, tutorial or talk, not '{:s}'.".format(kind[:30]))
    try:
        max_s = max(SECONDS[0], min(SECONDS[1], float(max_seconds)))
    except (TypeError, ValueError):
        return _error("max_seconds is a number of seconds (15 to 60).")
    if not picks:
        return _error("`picks` is a list of {first, last, title, hook, reason, scores}: first and last are sentence "
                      "numbers from read_edit_sentences.")
    scene = _scene()
    ed = scene.sequence_editor
    if ed is None:
        return _error("The Sequencer's scene “{:s}” has no strips".format(scene.name))
    if len(ed.meta_stack):
        return _error("Leave the meta strip first (Tab): the edit is read from its top level")
    try:
        from . import highlights
        chapter = highlights.load_chapter()
        got = scene_reader_ops.read(scene)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return _error("Couldn't read the edit: {!s}".format(ex))
    sentences = got["sentences"]
    now = sr.version(sentences)
    if not sentences:
        return _error("No speech to pick from: " + " ".join(got["report"]["messages"]))
    if version and str(version) != now:
        return _error("The edit changed since you read it (version {:s}, now {:s}): the sentence numbers may point "
                      "elsewhere. Read it again with read_edit_sentences and send the picks again.".format(
                          str(version)[:20], now), version=now, refused=True)
    store = ops.load(scene)
    fps = _wand._fps(scene)      # pylint: disable=protected-access
    if not replace:
        ops.check(scene)
        store = ops.load(scene)
    keep = [] if replace else list(store["picks"])      # changed and gone picks stay: adding never shrinks the list
    made, refused = ha.check_proposals(picks, sentences, fps, chapter, kind=kind, max_seconds=max_s,
                                       taken=[(p["start"], p["end"]) for p in keep if p["state"] == hp.State.OK])
    stored = hp.from_found(made, sentences)
    room = max(0, hp.MAX_PICKS - len(keep))
    if len(stored) > room:
        for p in made[room:]:
            refused.append({"index": -1, "first": p["first"], "last": p["last"], "why": "list_full",
                            "message": "the list holds at most {:d} picks".format(hp.MAX_PICKS)})
        stored = stored[:room]
    if not stored:
        return {"status": "refused", "message": "None of the {:d} picks passed the checks, so the list is unchanged.".format(
            len(picks) if isinstance(picks, (list, tuple)) else 0), "refused": refused, "version": now}
    store["picks"] = keep + stored
    store["found"] = {"edit": ops.edit_signature(scene), "seconds": int(max_s), "kind": kind,
                      "count": len(store["picks"])}
    if store["markers_on"]:
        ops.sync_markers(scene, store)
    ops.save(scene, store)
    _wand._push_undo("Propose Highlights")      # pylint: disable=protected-access
    ops._redraw()      # pylint: disable=protected-access
    return {"status": "ok", "added": len(stored), "refused": refused, "version": now, "picks": _store_rows(scene, store),
            "message": "{:d} pick{:s} added to the list{:s}.".format(
                len(stored), "" if len(stored) == 1 else "s", ", {:d} refused (see refused)".format(len(refused)) if refused else ""),
            "next": "make_short(pick) builds one as its own scene; nothing is built until then."}


# -- make_short ------------------------------------------------------------------------------------

def tool_make_short(pick: Any = "", steps: Any = None) -> dict[str, Any]:
    """``make_short`` (MCP): build the pick as its own scene (the Make Short button's code); one undo step."""
    busy = _running_message()
    if busy:
        return _error(busy)
    wanted, why = ha.parse_steps(steps)
    if wanted is None:
        return _error(why)
    scene = _scene()
    if scene.sequence_editor is None:
        return _error("The Sequencer's scene “{:s}” has no strips".format(scene.name))
    counts, why = ops.check(scene)
    if counts is None:
        return _error(why)
    store = ops.load(scene)
    found, why = ha.resolve_pick(store["picks"], pick)
    if found is None:
        return _error(why)
    if found["state"] != hp.State.OK:
        return {"status": "refused", "message": "'{:s}': {:s}. The edit was cut after this pick was found; find the "
                "picks again (suggest_highlights) or choose another.".format(found["title"], hp.state_text(found["state"]).lower()),
                "picks": _store_rows(scene, store)}
    import importlib
    short = importlib.import_module(__package__ + ".highlight_short")
    try:
        got = short.build(scene, found, wanted, None)       # no window: the artist's view stays on the long edit
    except short.ShortError as ex:
        return _error(str(ex))
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return _error("Make Short stopped: {!s}".format(ex))
    _wand._push_undo("Make Short")      # pylint: disable=protected-access
    ops._redraw()      # pylint: disable=protected-access
    new = got["scene"]
    fps = _wand._fps(new)      # pylint: disable=protected-access
    return {"status": "ok", "scene": new.name, "pick": found["id"], "title": found["title"],
            "frame_start": int(got["first"]), "frame_end": int(got["last"]),
            "seconds": round((got["last"] - got["first"] + 1) / fps, 1), "steps": got["lines"],
            "message": "Built “{:s}” from the pick '{:s}'. The long edit is unchanged; the user switches to the "
                       "scene to see it and renders it themselves.".format(new.name, found["title"])}


# -- the panel's button with an External Agent or Harness -------------------------------------------------

def hand_to_agent(context: Any, seconds: float, kind: str, count: int) -> tuple[str, str]:
    """``(report level, text)``: the External Agent gets the search as a prepared message (the chat starts it); an
    External Harness has no chat in Blender, so the message goes to the clipboard."""
    text = ha.prepared_message(kind, seconds, count)
    try:
        mode = str(context.preferences.addons[__package__].preferences.operating_mode)
    except (KeyError, AttributeError):
        mode = ""
    try:
        if mode == "EXTERNAL_AGENT":
            from . import helper_chats
            from . import ui_chat
            sent = helper_chats.submit(ui_chat._ui_thread(context), text)      # pylint: disable=protected-access
            return "INFO", ("Handed to the agent: it reads the speech and proposes the picks; they appear here."
                            if sent == "started" else "The agent is busy: the search waits in its queue.")
        context.window_manager.clipboard = text
        return "INFO", "Copied a message for your agent: paste it into your agent's chat. It uses read_edit_sentences and propose_highlights."
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return "ERROR", "Couldn't hand the search to the agent: {!s}".format(ex)
