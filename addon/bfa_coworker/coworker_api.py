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

Make Vertical (Video 3, #333): ``reframe_vertical`` runs :func:`reframe_vertical`
on the Sequencer's scene.
"""

from __future__ import annotations

__all__ = (
    "answer_question",
    "for_thread",
    "attached_image",
    "cancel_question",
    "cancel_questions",
    "current_question",
    "delegate_task",
    "job_status",
    "jobs_tick",
    "post_question",
    "question_state",
    "reframe_strips",
    "reframe_vertical",
    "scene_digest",
    "start_job",
    "transcribe_strip",
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


# -- Helper chats (#479) ------------------------------------------------------------
def delegate_task(args: dict[str, Any]) -> dict[str, Any]:
    """Open a helper chat for the calling thread and queue *args*' task there; answers at
    once (``status``, ``message``, ``helper_id``, ``title``).  Main thread (the bridge)."""
    try:
        from . import helper_chats
    except ImportError as ex:
        return {"status": "error", "message": "Helper chats aren't available here ({:s}).".format(str(ex))}
    try:
        return helper_chats.delegate(_thread[0], args if isinstance(args, dict) else {})
    except Exception as ex:  # pylint: disable=broad-exception-caught
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": "Coworker couldn't open a helper chat: {:s}".format(str(ex))}


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
# A turn that changed the scene is one step of Edit > Undo History, pushed when it ends
# (#501: one Ctrl+Z per turn; a turn that changed nothing pushes none), and Revert (the
# chat's button) undoes it in one go -- but only while you haven't changed the scene
# since (the depsgraph counter).  Nothing pushes in between: a tool called from a turn
# leaves its own step out (turn_owns_undo), the loop's save points are snapshots only.
# Threads 3 (#359): one checkpoint per thread (by its tag; "" = the default thread's,
# ``_turn``), since chats' turns run side by side; then each finished write call gets
# a step of its own (note_call), and the turn's end pushes only what no step holds yet.
# "before": the turn is open; "writes": its changes no step holds yet; "steps": the
# undo steps it pushed; "pushes": Coworker's push count when it began (Revert both).
_turn = {"before": False, "after": False, "edits": 0, "name": "", "n": 0, "mark": 0,
         "writes": 0, "steps": 0, "pushes": 0}
_turns: dict[str, dict[str, Any]] = {"": _turn}
_names = itertools.count(1)       # a number per turn, unique across threads
_edits = [0]
_pushes = [0]                     # Coworker's own undo pushes so far (#501)
_last_push = [""]                 # ... and the name of the newest one
_ended: set[str] = set()          # chats whose loop turn ended (worker thread, #501)


def _turn_of(thread: str = "") -> dict[str, Any]:
    return _turns.setdefault(str(thread or ""), {"before": False, "after": False, "edits": 0, "name": "",
                                                 "n": 0, "mark": 0, "writes": 0, "steps": 0, "pushes": 0})


def turn_open(thread: str = "") -> bool:
    """*thread*'s turn has started and not ended yet."""
    turn = _turns.get(str(thread or ""))
    return bool(turn and turn["before"])


def any_turn_open() -> bool:
    """Some chat's turn has started and not ended yet (#594: captions are not touched meanwhile)."""
    return any(t["before"] for t in list(_turns.values()))


def turn_owns_undo() -> bool:
    """The bridge runs this call for a chat whose turn is open (#501): the turn's own undo
    step covers what the call changes, so a tool pushes no step of its own.  Outside a
    turn (a plain MCP client, a wand, an operator) False: the tool keeps its step."""
    return turn_open(_thread[0])


def loop_turn_ended(thread: str = "") -> None:
    """The loop's turn of *thread* ended (its worker thread): the chat timer closes it at
    its next tick, also a turn shorter than one tick, which it never saw run (#501)."""
    with _lock:
        _ended.add(str(thread or ""))


def take_ended(thread: str = "") -> bool:
    """Main thread: did *thread*'s loop turn end since the last ask?"""
    with _lock:
        if str(thread or "") in _ended:
            _ended.discard(str(thread or ""))
            return True
    return False


def _any_turn_open() -> bool:
    return any(t["before"] and not t["after"] for t in _turns.values())


_OWN_WINDOW = 1.0                 # seconds a bridge call's depsgraph updates are Coworker's own
_own_until = [0.0]


def own_change() -> None:
    """The bridge ran a call (a chat's tool or Coworker's own bookkeeping, as the scene
    lock's release when the last chat ends): the depsgraph updates it causes are no
    edit of yours (#359)."""
    import time
    _own_until[0] = time.monotonic() + _OWN_WINDOW
    from . import ui_activity
    ui_activity.own(_OWN_WINDOW)          # #576: no drag either


def _count_edit(*_args: Any) -> None:
    # While a chat's turn is open the scene changes are its own (#359): another chat's
    # Revert must not read them as yours; that chat's undo steps show up in the journal.
    import time
    if not _any_turn_open() and time.monotonic() > _own_until[0]:
        _edits[0] += 1


def _undo(push_message: str = "", to_item: int = -1) -> bool:
    """``ed.undo_push`` (with a message), ``ed.undo_history`` (to step *to_item*) or
    ``ed.undo``, in a window's context.  Coworker's own step: no edit of yours (#359)."""
    import bpy  # pylint: disable=import-error
    own_change()
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm.windows else None)
    if window is None:
        return False
    try:
        with bpy.context.temp_override(window=window):
            if push_message:
                from . import co_work_guard
                with co_work_guard.lifted(bpy.data):    # #548: the step never holds the co-work lock
                    bpy.ops.ed.undo_push(message=push_message)
            elif to_item >= 0:
                bpy.ops.ed.undo_history(item=to_item)
            else:
                bpy.ops.ed.undo()
        return True
    except (RuntimeError, AttributeError):
        return False


_deferred_pushes: list[str] = []


def _push_when_idle() -> float | None:
    """Timer (main thread): the undo steps held back by a running render / bake (#591), pushed
    one by one once no job runs."""
    from . import long_jobs
    if long_jobs.busy_kind():
        return 1.0
    while _deferred_pushes:
        _push(_deferred_pushes.pop(0))
    return None


def _push(name: str) -> bool:
    """One undo step of Coworker's own, counted (#501: Revert goes back by the count).

    #591: not while a render or bake runs (its thread reads the scene, an undo push stores it):
    the step is pushed once the job is over, and this answers False now (the turn then offers no
    Revert for it)."""
    from . import long_jobs
    if long_jobs.busy_kind():
        _deferred_pushes.append(name)
        try:
            import bpy  # pylint: disable=import-error
            if not bpy.app.timers.is_registered(_push_when_idle):
                bpy.app.timers.register(_push_when_idle, first_interval=0.25, persistent=True)
        except (ImportError, AttributeError, ValueError):
            pass
        return False
    if not _undo(name):
        return False
    _pushes[0] += 1
    _last_push[0] = name
    return True


def _journal() -> Any:
    from . import side_by_side
    return side_by_side.JOURNAL


def note_call(thread: str, tool: str, code: str = "", step: str = "") -> None:
    """Main thread (the bridge): chat *thread*'s call *tool* ran, one that isn't only a
    look (#501).  With *step* it gets that undo step of its own now (chats side by side,
    #359: it holds every change so far); else what it may have changed waits for its
    turn's step."""
    from . import turn_undo
    turn = _turns.get(str(thread or ""))
    is_open = bool(turn and turn["before"])
    if step:
        if _push(step) and is_open:
            turn.update(steps=turn["steps"] + 1, writes=0, name=step)
    elif is_open and turn_undo.call_changes(tool, code):
        turn["writes"] += 1


def turn_started(label: str, thread: str = "") -> None:
    """Main thread, before the turn's first change (*thread*: whose turn, its tag).  No
    undo step here (#501): the turn's step comes at its end, if it changed something."""
    turn = _turn_of(thread)
    if not turn["before"]:
        turn["n"] = next(_names)
        # #359: this turn sees the other chats' steps from here on.
        turn.update(before=True, after=False, name="", writes=0, steps=0, pushes=_pushes[0],
                    mark=_journal().mark())


def turn_finished(label: str, thread: str = "") -> None:
    """Main thread, once the turn is over: its one undo step, *label*, when it changed
    something no step holds yet (#501); a turn that changed nothing pushes none."""
    turn = _turn_of(thread)
    if turn["before"]:
        if turn["writes"] and _push(label):
            turn.update(steps=turn["steps"] + 1, name=label)
            _journal().note(thread)
        turn.update(before=False, after=turn["steps"] > 0, edits=_edits[0], writes=0)


def can_revert(thread: str = "") -> bool:
    """*thread*'s last turn has a Revert to offer (directly, or with a choice, #359)."""
    turn = _turn_of(thread)
    return bool(turn["after"]) and _edits[0] == turn["edits"]


def revert_choice(thread: str = "", title: Any = None) -> Any:
    """What Revert may offer for *thread*'s last turn (#359): directly only when no
    other chat stepped on the undo stack since that turn began; else *Revert both* (not
    while that chat still works) or *Cancel*.  *title* names a chat by its tag."""
    from . import side_by_side
    turn = _turn_of(thread)
    others = side_by_side.JOURNAL.others_since(thread, int(turn.get("mark", 0))) if turn["after"] else []
    return side_by_side.revert_choice(bool(turn["after"]), _edits[0] != turn["edits"], others,
                                      side_by_side.working_chats(), has_snapshot=False,
                                      title=title or (lambda tag: tag or "All editors"))


# The chats whose steps the last "Revert both" undid as well (the chat tells them, #359).
reverted_with: list[str] = []


def revert_turn(thread: str = "", both: bool = False, title: Any = None) -> str:
    """Undo the last turn of *thread*; returns "" or why it can't.  When another chat
    stepped since the turn began, only with *both* (its steps since go too, #359)."""
    _turn = _turn_of(thread)
    choice = revert_choice(thread, title)
    if choice.kind in ("none", "edited", "busy"):
        return choice.message
    if choice.kind == "choose" and not both:
        return choice.message + " Choose Revert Both Turns to do that."
    # #501: the turn's steps are its end step (side by side, a step per write call), so go
    # back by them; with *both* by every Coworker step since the turn began (#359).
    from . import turn_undo
    count = _turn["steps"] if choice.kind == "direct" else _pushes[0] - int(_turn.get("pushes", 0))
    try:
        import bpy  # pylint: disable=import-error
        stack = getattr(bpy.context.window_manager, "undo_stack", None)
    except (ImportError, AttributeError):
        stack = None
    if stack is not None:
        names = [step.name for step in stack.steps]
        active = next((getattr(stack, a) for a in ("active_index", "active_step_index") if hasattr(stack, a)), None)
        target, why = turn_undo.revert_target(count, names, active if isinstance(active, int) else None,
                                              _last_push[0])
        if why:
            return why
        if not _undo(to_item=target):
            return "Blender couldn't undo here."
    elif choice.kind == "choose":
        # Plain undos would take back the newest steps, another chat's: never by position.
        return "This Blender can't find the turn's first step, so use Edit > Undo History."
    else:
        for _step in range(count):
            if not _undo():
                return "Blender couldn't undo here."
    _turn["after"] = False
    reverted_with[:] = list(choice.others) if choice.kind == "choose" else []
    for other in reverted_with:     # their last turns' steps are gone from the stack now
        _turn_of(other)["after"] = False
    _journal().note(thread)
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


# -- Speech (Video 1, #375) -----------------------------------------------------------
def transcribe_strip(strip: str, language: str = "", limit: int = 150, offset: int = 0,
                     prompt: str = "", model: str = "") -> Any:
    """The words of a sound / movie strip with times (cached per media file): a result
    dict, or a checker while whisper runs (a long one becomes a job, see speech.py).
    The calling chat owns it: Stop in that chat ends it (#522)."""
    from . import speech
    return speech.transcribe_strip(strip, language=language, limit=limit, offset=offset,
                                   prompt=prompt, model=model, owner=speech.chat_owner(_thread[0]))


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


# What the co-work lock lifted for a save (#548): the file keeps the objects' own
# hide_select, never the lock of a turn that is running.
_save_lifted: list[Any] = [None]


def _on_save_pre(*_args: Any) -> None:
    import bpy  # pylint: disable=import-error
    from . import co_work_guard, ui_activity
    ui_activity.own(1.0)
    _save_lifted[0] = co_work_guard.lift(bpy.data)


def _on_save_post(*_args: Any) -> None:
    import bpy  # pylint: disable=import-error
    from . import co_work_guard
    held, _save_lifted[0] = _save_lifted[0], None
    co_work_guard.relock(bpy.data, held)
    from . import ui_activity
    ui_activity.own(1.0)


def _handlers() -> list[tuple[Any, Any]]:
    import bpy  # pylint: disable=import-error
    h = bpy.app.handlers
    from . import ui_activity
    return [(h.depsgraph_update_post, _count_edit), (h.depsgraph_update_post, ui_activity.note_update),
            (h.render_stats, _on_render_stats),
            (h.render_cancel, _on_render_cancel), (h.object_bake_cancel, _on_bake_cancel),
            (h.load_pre, _on_load_pre), (h.save_pre, _on_save_pre), (h.save_post, _on_save_post),
            (h.save_post_fail, _on_save_post)]


def _sample_modals() -> float:
    """Timer (#576): note when each modal operator was first seen, so a long-lived one is no drag."""
    import bpy  # pylint: disable=import-error
    from . import ui_activity
    ui_activity.sample(bpy.context.window_manager)
    return 0.25


def register() -> None:
    import bpy  # pylint: disable=import-error
    if not bpy.app.timers.is_registered(_sample_modals):
        bpy.app.timers.register(_sample_modals, first_interval=0.25, persistent=True)
    for handlers, fn in _handlers():
        if fn is not _count_edit:
            bpy.app.handlers.persistent(fn)     # jobs outlive a file load
        if fn not in handlers:
            handlers.append(fn)


def unregister() -> None:
    import bpy  # pylint: disable=import-error
    if bpy.app.timers.is_registered(_sample_modals):
        bpy.app.timers.unregister(_sample_modals)
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


def reframe_strips(layout: str = "", strips: list[str] | None = None, swap: bool = False) -> dict[str, Any]:
    """Reframe's layouts for *strips* (the selected ones when empty) in the Sequencer's scene (#606), one
    undo step (``reframe_layouts.tool_reframe_strips``).  Main thread only."""
    from . import reframe_layouts
    return reframe_layouts.tool_reframe_strips(layout, strips, swap)


def reframe_vertical(strips: list[str] | None = None, mode: str = "CROP", in_copy: bool = True,
                     resolution: str = "1080x1920", reset: bool = False, follow: str = "",
                     layout: str = "auto") -> dict[str, Any]:
    """Make the Sequencer's edit 9:16: Centre Crop with each strip's own crop and transform, Blur Fill with a Reframe modifier (#333, #632), one undo step.

    Returns the made strips; with ``follow`` also ``following`` (the status of the subject search, whose keys come
    later as a second undo step) or ``following_error``.
    Raises ``reframe.ReframeError`` with a clear reason when it can't.  Main thread only.
    """
    import bpy  # pylint: disable=import-error
    from . import reframe
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm and wm.windows else None)
    scene = getattr(getattr(window, "workspace", None), "sequencer_scene", None) if window else None
    if scene is None:
        scene = window.scene if window is not None else bpy.context.scene
    done = reframe.make_vertical(scene, mode, in_copy, resolution, reset, names=strips or None, window=window)
    if not turn_owns_undo():        # from a chat turn its own step covers it (#501)
        _undo(push_message="Make Vertical")
    follow = str(follow or "").strip().lower()
    if not follow and str(layout or "auto").strip().lower() in ("single", "split"):
        follow = "auto"             # a layout asked for means: find the subject
    if follow and mode == "CROP":
        # Looks at a few frames per shot, then keys the crop (and builds the split) on a timer: not part of this undo step.
        try:
            done["following"] = reframe.follow_made(done, follow, str(layout or "auto"))
        except Exception as ex:  # pylint: disable=broad-exception-caught  # ReframeError, Unavailable
            done["following_error"] = str(ex)
    elif follow:
        done["following_error"] = "Follow Subject needs mode CROP (Blur Fill shows the whole shot)."
    return done
