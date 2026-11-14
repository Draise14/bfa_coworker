# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Coworker's own tools, for every engine (Tier 4, task "Engine 6").

The MCP server's ``update_plan``, ``ask_user`` and ``get_scene_digest`` tools
run small tool-code in Blender (over the bridge, on the main thread) that
calls the functions here.  So an external agent keeps Goal & Plan current,
asks you a question with buttons in the chat, and reads a short scene summary
that says "unchanged" when nothing changed, just like the local loop.

Questions: :func:`post_question` opens one and returns its id; the chat shows
the oldest open question as a card (choices as buttons; with *allow_text*,
what you type next answers it); the tool polls :func:`question_state` until it
is answered.  Stop cancels every open question.

Threads 1 (#357): the bridge says which thread a call comes from
(:func:`for_thread`); its plan goes to that thread's Goal & Plan, its questions
carry the thread (the chat can show and Stop can cancel one thread's), and the
digest keeps each thread's own earlier snapshots.

Long jobs (Tools 6, #309): a background render or bake registers a checker with
:func:`start_job` and answers with its id; ``get_job_status`` reads
:func:`job_status`.  The render and bake handlers registered here feed the
job registry (:mod:`long_jobs`) progress and cancels.
"""

from __future__ import annotations

__all__ = (
    "answer_question",
    "for_thread",
    "attached_image",
    "cancel_question",
    "cancel_questions",
    "current_question",
    "job_status",
    "jobs_tick",
    "post_question",
    "question_state",
    "scene_digest",
    "start_job",
    "update_plan",
)

import contextlib
import hashlib
import itertools
import threading
from typing import Any, Iterator

_lock = threading.Lock()
_ids = itertools.count(1)
# id -> {"question", "choices", "allow_text", "answer" (None = open), "source"}
_questions: dict[str, dict[str, Any]] = {}
_MAX_CHOICES = 6
# Auto mode (#177): while set, a question is answered with this at once.
auto_answer = [""]
auto_answers: dict[str, str] = {}     # Threads 2b (#417): per thread tag, while its Auto mode runs
# The thread the bridge runs a call for (#357; "" = the default thread).  The bridge
# runs one call at a time on the main thread, so one slot is enough.
_thread = [""]


@contextlib.contextmanager
def for_thread(thread: str) -> Iterator[None]:
    """The bridge runs a call for *thread* (its tag) inside this block."""
    previous, _thread[0] = _thread[0], str(thread or "")
    try:
        yield
    finally:
        _thread[0] = previous


def _store() -> Any:
    """The calling thread's session store (its Goal & Plan)."""
    from . import session_memory
    if _thread[0]:
        try:
            from . import agent_controller
            thread = agent_controller.THREADS.get(_thread[0])
            if thread is not None:
                return agent_controller._memory(thread).store
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    return session_memory.store


def _redraw() -> None:
    try:
        from . import chat_editor, ui_chat
        chat_editor._tag_chat_regions()
        ui_chat._tag_chat_regions_redraw()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


# -- Goal & Plan --------------------------------------------------------------------
def update_plan(args: dict[str, Any]) -> str:
    """Set or tick the plan the Goal & Plan panel shows (the same as the local loop's)."""
    from . import session_memory
    with session_memory.store_lock:
        result = _store().goal.apply_update(args if isinstance(args, dict) else {})
    _redraw()
    return result


# -- Questions ----------------------------------------------------------------------
def post_question(question: str, choices: list[str] | None = None, allow_text: bool = True,
                  source: str = "", thread: str | None = None) -> str:
    choices = [str(c).strip() for c in (choices or []) if str(c).strip()][:_MAX_CHOICES]
    qid = "q{:d}".format(next(_ids))
    with _lock:
        _questions[qid] = {"question": str(question).strip() or "?", "choices": choices,
                           "allow_text": bool(allow_text or not choices),
                           "answer": auto_answer[0] or auto_answers.get(
                               _thread[0] if thread is None else thread) or None, "source": source,
                           "thread": _thread[0] if thread is None else thread}
    _redraw()
    return qid


def question_state(qid: str) -> dict[str, Any]:
    """``{"answered": bool, "answer": str, "cancelled": bool}``; a gone id reads as cancelled."""
    with _lock:
        q = _questions.get(qid)
        if q is None:
            return {"answered": False, "answer": "", "cancelled": True}
        if q["answer"] is None:
            return {"answered": False, "answer": "", "cancelled": False}
        _questions.pop(qid, None)        # read once: done with it
        return {"answered": q["answer"] is not False, "answer": q["answer"] or "",
                "cancelled": q["answer"] is False}


def _mine(q: dict[str, Any], thread: str | None) -> bool:
    return thread is None or q.get("thread", "") == thread


def current_question(thread: str | None = None) -> tuple[str, dict[str, Any]] | None:
    """The oldest open question ``(id, question)`` (of *thread*) for the chat to show, or None."""
    with _lock:
        for qid, q in _questions.items():
            if q["answer"] is None and _mine(q, thread):
                return qid, dict(q)
    return None


def answer_question(answer: str, qid: str = "", thread: str | None = None) -> bool:
    """Answer *qid* (or the oldest open one, of *thread*); False when there's none."""
    with _lock:
        open_ids = [i for i, q in _questions.items() if q["answer"] is None and _mine(q, thread)]
        target = qid if qid in open_ids else (open_ids[0] if open_ids and not qid else "")
        if not target:
            return False
        _questions[target]["answer"] = str(answer)
    _redraw()
    return True


def cancel_question(qid: str) -> None:
    """Withdraw one question (it waited too long)."""
    with _lock:
        _questions.pop(qid, None)
    _redraw()


def cancel_questions(thread: str | None = None) -> None:
    """Stop: every open question (of *thread*) is answered "cancelled"."""
    with _lock:
        for q in _questions.values():
            if q["answer"] is None and _mine(q, thread):
                q["answer"] = False
    _redraw()


# -- Turn checkpoints and Revert (Engine 6b) ------------------------------------------
# Each turn gets an undo step before and after it, so the agent's whole turn is one
# step of Edit > Undo History, and Revert (the chat's button) undoes it in one go --
# but only while you haven't changed the scene since (the depsgraph counter).
# Threads 3 (#359): one checkpoint per thread (by its tag; "" = the default thread's,
# ``_turn``), since chats' turns run side by side.
_turn = {"before": False, "after": False, "edits": 0, "name": "", "n": 0, "mark": 0}
_turns: dict[str, dict[str, Any]] = {"": _turn}
_names = itertools.count(1)       # "Before ... (n)": unique across threads
_edits = [0]


def _turn_of(thread: str = "") -> dict[str, Any]:
    return _turns.setdefault(str(thread or ""), {"before": False, "after": False, "edits": 0, "name": "",
                                                 "n": 0, "mark": 0})


def _any_turn_open() -> bool:
    return any(t["before"] and not t["after"] for t in _turns.values())


def _count_edit(*_args: Any) -> None:
    # While a chat's turn is open the scene changes are its own (#359): another chat's
    # Revert must not read them as yours; that chat's undo steps show up in the journal.
    if not _any_turn_open():
        _edits[0] += 1


def _undo(push_message: str = "", to_item: int = -1) -> bool:
    """``ed.undo_push`` (with a message), ``ed.undo_history`` (to step *to_item*) or
    ``ed.undo``, in a window's context."""
    import bpy  # pylint: disable=import-error
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm.windows else None)
    if window is None:
        return False
    try:
        with bpy.context.temp_override(window=window):
            if push_message:
                bpy.ops.ed.undo_push(message=push_message)
            elif to_item >= 0:
                bpy.ops.ed.undo_history(item=to_item)
            else:
                bpy.ops.ed.undo()
        return True
    except (RuntimeError, AttributeError):
        return False


def turn_started(label: str, thread: str = "") -> None:
    """Main thread, before the turn's first change (*thread*: whose turn, its tag)."""
    turn = _turn_of(thread)
    if not turn["before"]:
        # A name of its own, so Revert finds this step again (#242).
        turn["n"] = next(_names)
        name = "Before {:s} ({:d})".format(label, turn["n"])
        turn.update(before=_undo(name), after=False, name=name)


def turn_finished(label: str, thread: str = "") -> None:
    """Main thread, once the turn is over."""
    turn = _turn_of(thread)
    if turn["before"] and not turn["after"]:
        done = _undo(label)
        turn.update(before=False, after=done, edits=_edits[0])


def can_revert(thread: str = "") -> bool:
    turn = _turn_of(thread)
    return bool(turn["after"]) and _edits[0] == turn["edits"]


def revert_turn(thread: str = "") -> str:
    """Undo the last turn of *thread*; returns "" or why it can't."""
    _turn = _turn_of(thread)
    if not _turn["after"]:
        return "There's no finished turn to revert."
    if _edits[0] != _turn["edits"]:
        return "The scene changed after the turn, so use Edit > Undo History to pick the step."
    # The tools' own operators push undo steps of their own during the turn, so one
    # undo only took back the last of them (#242): go back to the turn's "Before" step.
    try:
        import bpy  # pylint: disable=import-error
        stack = getattr(bpy.context.window_manager, "undo_stack", None)
    except (ImportError, AttributeError):
        stack = None
    if stack is not None:
        names = [step.name for step in stack.steps]
        if _turn["name"] not in names:
            return ("This turn made more steps than the undo history keeps (Preferences > System > "
                    "Undo Steps), so use Edit > Undo History.")
        if not _undo(to_item=len(names) - 1 - names[::-1].index(_turn["name"])):
            return "Blender couldn't undo here."
    elif not _undo():
        return "Blender couldn't undo here."
    _turn["after"] = False
    _redraw()
    return ""


# -- Long jobs (Tools 6, #309) --------------------------------------------------------
def start_job(kind: str, label: str, check_fn: Any, on_finish: Any = None) -> str:
    """A background job the agent checks with get_job_status; returns its id."""
    from . import long_jobs
    job_id = long_jobs.start(kind, label, check_fn, on_finish)
    _redraw()
    return job_id


def job_status(job_id: str) -> dict[str, Any]:
    from . import long_jobs
    return long_jobs.status(job_id)


_jobs_drawn = [0.0]


def jobs_tick() -> bool:
    """The bridge's timer: check the jobs, and redraw the chat (its job row) when one
    ends or about once a second while one runs.  True when a job ended."""
    import time
    from . import activity, long_jobs
    ended = long_jobs.poll()
    if activity.following():
        # #423: a call that started a job shows how the job ended (also one a file load ended).
        activity.follow_jobs(long_jobs.status)
    now = time.monotonic()
    if ended or (long_jobs.has_running() and now - _jobs_drawn[0] >= 1.0):
        _jobs_drawn[0] = now
        _redraw()
    return ended


def _on_render_stats(*args: Any) -> None:
    # Called from the render thread with the engine's status line ("Sample 12/128").
    from . import long_jobs
    if args and isinstance(args[0], str):
        long_jobs.note_progress("RENDER", args[0])


def _on_render_cancel(*_args: Any) -> None:
    from . import long_jobs
    long_jobs.cancelled("RENDER")


def _on_bake_cancel(*_args: Any) -> None:
    from . import long_jobs
    long_jobs.cancelled("OBJECT_BAKE")


def _on_load_pre(*_args: Any) -> None:
    # A file load stops Blender's render / bake jobs; end ours without restoring
    # settings onto the old file's data.
    from . import long_jobs
    long_jobs.file_loading()


def _handlers() -> list[tuple[Any, Any]]:
    import bpy  # pylint: disable=import-error
    h = bpy.app.handlers
    return [(h.depsgraph_update_post, _count_edit), (h.render_stats, _on_render_stats),
            (h.render_cancel, _on_render_cancel), (h.object_bake_cancel, _on_bake_cancel),
            (h.load_pre, _on_load_pre)]


def register() -> None:
    import bpy  # pylint: disable=import-error
    for handlers, fn in _handlers():
        if fn is not _count_edit:
            bpy.app.handlers.persistent(fn)     # jobs outlive a file load
        if fn not in handlers:
            handlers.append(fn)


def unregister() -> None:
    from . import long_jobs
    for handlers, fn in _handlers():
        if fn in handlers:
            handlers.remove(fn)
    long_jobs.clear()


# -- Scene digest -------------------------------------------------------------------
_MAX_OBJECTS = 40
# thread tag -> {hash -> {object name: fingerprint}}: each thread keeps its own last
# snapshots, so another thread's reads never push its "since" baseline out (#357).
_snapshots: dict[str, dict[str, dict[str, tuple]]] = {}
_MAX_SNAPSHOTS = 8


def _fingerprint(ob: Any) -> tuple:
    r = lambda v: tuple(round(float(x), 3) for x in v)  # noqa: E731
    data = getattr(ob, "data", None)
    mats = tuple(s.material.name if s.material else "" for s in getattr(ob, "material_slots", ()))
    return (ob.type, r(ob.location), r(ob.rotation_euler), r(ob.scale),
            ob.parent.name if ob.parent else "", data.name if data else "",
            tuple(m.type for m in ob.modifiers), mats, bool(ob.hide_get()) if hasattr(ob, "hide_get") else False,
            len(getattr(data, "vertices", ())) if data is not None else 0)


def attached_image() -> dict[str, Any]:
    """The image waiting in the chat's attach socket, for an external client (#199).

    In External Harness the chat runs in another app: the user drops or picks an
    image in Coworker and asks the client to look at it.  Main thread only.
    """
    import bpy  # pylint: disable=import-error
    from . import chat_attachments
    props = getattr(bpy.context.window_manager, "bfacw_chat_props", None)
    img = getattr(props, "chat_image", None)
    if img is None:
        return {"status": "none", "message": "No image is attached in Coworker. Ask the user to drop one "
                                             "onto the Coworker chat, or use Attach Render / Attach Screen."}
    uri = chat_attachments.image_to_data_uri(img)
    if not uri:
        return {"status": "error", "message": "The attached image could not be read."}
    head, data = uri.split(",", 1)
    return {"status": "ok", "name": chat_attachments.attachment_name(img),
            "mime": head[5:].split(";", 1)[0], "image_base64": data}


def scene_digest(since: str = "") -> dict[str, Any]:
    """A short summary of the scene with a hash; with *since* = an earlier hash,
    ``unchanged`` or just what changed."""
    import bpy  # pylint: disable=import-error
    context = bpy.context
    scene = context.scene
    objects = list(scene.objects)
    prints = {ob.name: _fingerprint(ob) for ob in objects}
    digest = hashlib.sha1(repr(sorted(prints.items())).encode("utf-8")).hexdigest()[:12]
    head: dict[str, Any] = {"hash": digest, "scene": scene.name, "frame": scene.frame_current,
                            "mode": context.mode,
                            "active": context.view_layer.objects.active.name
                            if context.view_layer.objects.active else None}
    snapshots = _snapshots.setdefault(_thread[0], {})
    if len(snapshots) >= _MAX_SNAPSHOTS and digest not in snapshots:
        snapshots.pop(next(iter(snapshots)))
    snapshots[digest] = prints
    if since and since == digest:
        return dict(head, unchanged=True)
    head["selected"] = [ob.name for ob in context.selected_objects][:10]
    counts: dict[str, int] = {}
    for ob in objects:
        counts[ob.type] = counts.get(ob.type, 0) + 1
    head["counts"] = counts
    before = snapshots.get(since) if since else None
    if before is not None:
        added = sorted(set(prints) - set(before))
        removed = sorted(set(before) - set(prints))
        changed = sorted(n for n in set(prints) & set(before) if prints[n] != before[n])
        head["changes"] = {"added": added[:30], "removed": removed[:30], "changed": changed[:30]}
        return head
    head["objects"] = [{"name": ob.name, "type": ob.type,
                        "location": [round(v, 2) for v in ob.location],
                        **({"parent": ob.parent.name} if ob.parent else {})}
                       for ob in objects[:_MAX_OBJECTS]]
    if len(objects) > _MAX_OBJECTS:
        head["more_objects"] = len(objects) - _MAX_OBJECTS
    if since:
        head["note"] = "The earlier hash is too old to compare; here is the whole summary."
    return head
