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
"""

from __future__ import annotations

__all__ = (
    "answer_question",
    "cancel_question",
    "cancel_questions",
    "current_question",
    "post_question",
    "question_state",
    "scene_digest",
    "update_plan",
)

import hashlib
import itertools
import threading
from typing import Any

_lock = threading.Lock()
_ids = itertools.count(1)
# id -> {"question", "choices", "allow_text", "answer" (None = open), "source"}
_questions: dict[str, dict[str, Any]] = {}
_MAX_CHOICES = 6
# Auto mode (#177): while set, a question is answered with this at once.
auto_answer = [""]


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
        result = session_memory.store.goal.apply_update(args if isinstance(args, dict) else {})
    _redraw()
    return result


# -- Questions ----------------------------------------------------------------------
def post_question(question: str, choices: list[str] | None = None, allow_text: bool = True,
                  source: str = "") -> str:
    choices = [str(c).strip() for c in (choices or []) if str(c).strip()][:_MAX_CHOICES]
    qid = "q{:d}".format(next(_ids))
    with _lock:
        _questions[qid] = {"question": str(question).strip() or "?", "choices": choices,
                           "allow_text": bool(allow_text or not choices),
                           "answer": auto_answer[0] or None, "source": source}
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


def current_question() -> tuple[str, dict[str, Any]] | None:
    """The oldest open question ``(id, question)`` for the chat to show, or None."""
    with _lock:
        for qid, q in _questions.items():
            if q["answer"] is None:
                return qid, dict(q)
    return None


def answer_question(answer: str, qid: str = "") -> bool:
    """Answer *qid* (or the oldest open one); False when there's none."""
    with _lock:
        open_ids = [i for i, q in _questions.items() if q["answer"] is None]
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


def cancel_questions() -> None:
    """Stop: every open question is answered "cancelled"."""
    with _lock:
        for q in _questions.values():
            if q["answer"] is None:
                q["answer"] = False
    _redraw()


# -- Turn checkpoints and Revert (Engine 6b) ------------------------------------------
# Each turn gets an undo step before and after it, so the agent's whole turn is one
# step of Edit > Undo History, and Revert (the chat's button) undoes it in one go --
# but only while you haven't changed the scene since (the depsgraph counter).
_turn = {"before": False, "after": False, "edits": 0}
_edits = [0]


def _count_edit(*_args: Any) -> None:
    _edits[0] += 1


def _undo(push_message: str = "") -> bool:
    """``ed.undo_push`` (with a message) or ``ed.undo``, in a window's context."""
    import bpy  # pylint: disable=import-error
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm.windows else None)
    if window is None:
        return False
    try:
        with bpy.context.temp_override(window=window, screen=window.screen):
            if push_message:
                bpy.ops.ed.undo_push(message=push_message)
            else:
                bpy.ops.ed.undo()
        return True
    except (RuntimeError, AttributeError):
        return False


def turn_started(label: str) -> None:
    """Main thread, before the turn's first change."""
    if not _turn["before"]:
        _turn.update(before=_undo("Before " + label), after=False)


def turn_finished(label: str) -> None:
    """Main thread, once the turn is over."""
    if _turn["before"] and not _turn["after"]:
        done = _undo(label)
        _turn.update(before=False, after=done, edits=_edits[0])


def can_revert() -> bool:
    return bool(_turn["after"]) and _edits[0] == _turn["edits"]


def revert_turn() -> str:
    """Undo the last turn; returns "" or why it can't."""
    if not _turn["after"]:
        return "There's no finished turn to revert."
    if _edits[0] != _turn["edits"]:
        return "The scene changed after the turn, so use Edit > Undo History to pick the step."
    if not _undo():
        return "Blender couldn't undo here."
    _turn["after"] = False
    _redraw()
    return ""


def register() -> None:
    import bpy  # pylint: disable=import-error
    if _count_edit not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_count_edit)


def unregister() -> None:
    import bpy  # pylint: disable=import-error
    if _count_edit in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_count_edit)


# -- Scene digest -------------------------------------------------------------------
_MAX_OBJECTS = 40
_snapshots: dict[str, dict[str, tuple]] = {}      # hash -> {object name: fingerprint}
_MAX_SNAPSHOTS = 8


def _fingerprint(ob: Any) -> tuple:
    r = lambda v: tuple(round(float(x), 3) for x in v)  # noqa: E731
    data = getattr(ob, "data", None)
    mats = tuple(s.material.name if s.material else "" for s in getattr(ob, "material_slots", ()))
    return (ob.type, r(ob.location), r(ob.rotation_euler), r(ob.scale),
            ob.parent.name if ob.parent else "", data.name if data else "",
            tuple(m.type for m in ob.modifiers), mats, bool(ob.hide_get()) if hasattr(ob, "hide_get") else False,
            len(getattr(data, "vertices", ())) if data is not None else 0)


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
    if len(_snapshots) >= _MAX_SNAPSHOTS and digest not in _snapshots:
        _snapshots.pop(next(iter(_snapshots)))
    _snapshots[digest] = prints
    if since and since == digest:
        return dict(head, unchanged=True)
    head["selected"] = [ob.name for ob in context.selected_objects][:10]
    counts: dict[str, int] = {}
    for ob in objects:
        counts[ob.type] = counts.get(ob.type, 0) + 1
    head["counts"] = counts
    before = _snapshots.get(since) if since else None
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
