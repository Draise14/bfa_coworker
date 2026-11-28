# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Make Chapters (#542), the Blender side: the **Make Chapters...** wand (the Sequencer's Video panel and Strip
menu, the Transcript panel), its dialog, the search on a worker thread, and what it writes: timeline markers
(``CH1: Title``) and a text data-block with the list in the ``0:00 Title`` form video sites accept.  The checks and
the texts are ``chapters.py`` (bpy-free).

It is a wand (``_misc/wand_panel_rules.md``): the options are in its dialog, in boxes with a header each; one
report line says what it did; the one that removes what it made (Remove Chapters) is last in the dialog.

The edit's speech is read on the main thread with the scene reader (#611), the model is asked on a worker thread
(``ask_json``, #612), and a small modal operator on the main thread (``bfacw.make_chapters_watch``: Esc stops it,
the status bar shows its progress) takes the answer and writes the markers and the text in one undo step, then
gives the one report line.  Reopening the dialog while a search runs shows its progress and a Stop button.  An
External Agent or Harness runs its own model: the wand says so and to switch to Local or Remote (like Find
Highlights).
"""

from __future__ import annotations

__all__ = (
    "BFACW_OT_chapters_remove",
    "BFACW_OT_make_chapters",
    "BFACW_OT_make_chapters_stop",
    "BFACW_OT_make_chapters_watch",
    "apply",
    "register",
    "remove_ours",
    "unregister",
)

import threading
from typing import Any

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, FloatProperty, IntProperty  # pylint: disable=import-error
from bpy.types import Operator  # pylint: disable=import-error

from . import chapters as ch
from . import scene_reader_ops
from .shared import icon as safe_icon
from .wand import scene as _seq
from .wand import sequencer as _wand
from .wand.spec import agent_line

TICK = 0.25
TEXT_NAME = "Chapters"
TEXT_TAG = "bfacw_chapters_text"      # custom property on the text data-block Make Chapters made


# -- what it writes ---------------------------------------------------------------------------------

def load(scene: Any) -> dict[str, Any]:
    return ch.loads(scene.get(ch.PROP, ""))


def save(scene: Any, store: dict[str, Any]) -> None:
    scene[ch.PROP] = ch.dumps(store)


def _text_for(store: dict[str, Any]) -> Any:
    """The text data-block this scene's chapters go to: the one Make Chapters made before (still ours), else a new one."""
    texts = bpy.data.texts
    old = texts.get(store.get("text") or "")
    if old is not None and old.get(TEXT_TAG):
        return old
    text = texts.new(TEXT_NAME)
    text[TEXT_TAG] = True
    return text


def apply(scene: Any, chapters: list[dict[str, Any]], fps: float) -> dict[str, Any]:
    """Write *chapters* on *scene*: its earlier chapter markers (the ones this scene's store made) go, the new
    ones are made, the list goes to the text data-block.  Returns ``{"text", "lines", "markers"}``."""
    store = load(scene)
    markers = scene.timeline_markers
    remove, add, names = ch.marker_plan(store, [(m.name, m.frame) for m in markers], chapters)
    gone = {n for n, _f in remove}
    for m in [m for m in markers if m.name in gone]:
        markers.remove(m)
    for name, frame in add:
        markers.new(name, frame=frame)
    body = ch.youtube_text(chapters, scene.frame_start, fps)
    text = _text_for(store)
    text.clear()
    text.write(body + "\n")
    store.update(chapters=[dict(c) for c in chapters], markers=names, text=text.name)
    save(scene, store)
    return {"text": text.name, "lines": body, "markers": len(add)}


def remove_ours(scene: Any) -> int:
    """Take the chapter markers this scene's store made off it (the artist's markers stay); the text stays."""
    store = load(scene)
    names = set(store["markers"])
    gone = [m for m in scene.timeline_markers if m.name in names]
    for m in gone:
        scene.timeline_markers.remove(m)
    store.update(chapters=[], markers=[])
    save(scene, store)
    return len(gone)


def _report_text(chapters: list[dict[str, Any]], info: dict[str, Any], fps: float, frame_start: int, copied: bool) -> str:
    last = ch.format_time((chapters[-1]["frame"] - frame_start) / fps) if chapters else "0:00"
    return "Made {:d} chapter{:s} (the last at {:s}): {:d} markers on the timeline and the list in the text “{:s}”{:s}".format(
        len(chapters), "" if len(chapters) == 1 else "s", last, info["markers"], info["text"],
        ", copied to the clipboard" if copied else "")


# -- the search (a worker thread, a timer on the main thread) ----------------------------------------

def _idle_job() -> dict[str, Any]:
    return {"running": False, "progress": "", "stop": None, "thread": None, "scene": "", "box": None,
            "message": "", "level": "ok", "id": 0}


_JOB: dict[str, Any] = _idle_job()


def _reset_job() -> None:
    """Forget a search for good (a file was loaded, or the add-on is going): its worker is told to stop and its
    answer is dropped; the next job gets a new id, so the old worker can't touch it."""
    if _JOB["stop"] is not None:
        _JOB["stop"].set()
    new = _idle_job()
    new["id"] = int(_JOB["id"]) + 1
    _JOB.clear()
    _JOB.update(new)


def _status(text: str | None) -> None:
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        try:
            win.workspace.status_text_set(text)
        except (AttributeError, RuntimeError):
            pass


def _redraw() -> None:
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        for area in win.screen.areas:
            area.tag_redraw()


def finish() -> tuple[str, str]:
    """On the main thread, when the worker is over: write what it found (or say why not).  ``(level, line)``:
    the level is ``INFO``, ``WARNING`` or ``ERROR`` for the report."""
    box = _JOB["box"] or {}
    stopped = _JOB["stop"] is not None and _JOB["stop"].is_set()
    _JOB.update(running=False, thread=None, progress="")
    _status(None)
    scene = bpy.data.scenes.get(_JOB["scene"])
    if scene is None:
        return "WARNING", "The scene is gone: nothing was written"
    fps = _wand._fps(scene)       # pylint: disable=protected-access
    if stopped:
        _JOB.update(message="Stopped: nothing was written", level="warn")
    elif box.get("error"):
        _JOB.update(message=box["error"], level="error")
    elif "result" not in box:
        _JOB.update(message="The search ended without an answer. Try Make Chapters again.", level="error")
    else:
        out = box["result"]
        notes = " ".join(out["report"].get("messages") or [])
        if out["chapters"]:
            info = apply(scene, out["chapters"], fps)
            _wand._push_undo("Make Chapters")      # pylint: disable=protected-access
            copied = False
            try:
                bpy.context.window_manager.clipboard = info["lines"]
                copied = True
            except (AttributeError, RuntimeError):
                pass
            line = _report_text(out["chapters"], info, fps, scene.frame_start, copied)
            _JOB.update(message=(line + ". " + notes).strip() if notes else line, level="ok")
        else:
            _JOB.update(message=notes or "No chapters were made.", level="warn")
    _redraw()
    return {"ok": "INFO", "warn": "WARNING"}.get(_JOB["level"], "ERROR"), _JOB["message"]


def _job_tick() -> float | None:
    """The fallback when no modal operator could start (no window): a timer takes the answer."""
    if not _JOB["running"]:
        return None
    thread = _JOB["thread"]
    if thread is not None and thread.is_alive():
        _status("Make Chapters: " + (_JOB["progress"] or "working..."))
        return TICK
    finish()
    return None


def _work(box: dict[str, Any], asker: Any, opts: dict[str, Any], stop: Any, job_id: int) -> None:
    def progress(text: Any) -> None:
        if _JOB["id"] == job_id:           # a worker from a job that was reset must not write into the next one
            _JOB["progress"] = str(text)
    try:
        box["result"] = ch.find_chapters(
            box["sentences"], opts["fps"], opts["frame_start"], opts["end_frame"], asker, count=opts["count"],
            min_seconds=opts["min_seconds"], context=opts["context"], should_stop=stop.is_set, progress=progress)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        box["error"] = str(ex) or type(ex).__name__


def start(scene: Any, count: int, min_seconds: float, asker: Any = None) -> str:
    """Read the edit and start the search; ``""`` when it started, else why it did not (for the artist)."""
    if _JOB["running"]:
        return "Chapters are already being made: reopen Make Chapters to stop them"
    ed = scene.sequence_editor
    if len(ed.meta_stack):
        return "Leave the meta strip first (Tab): the edit is read from its top level"
    try:
        got = scene_reader_ops.read(scene, scene_reader_ops.context_tokens())
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return "Couldn't read the edit: {!s}".format(ex)
    if not got["sentences"]:
        return ("No speech to make chapters from. Make a transcript first (Edit Transcript or Auto Captions). "
                + " ".join(got["report"]["messages"])).strip()
    from . import ask_json
    if asker is None:
        try:
            asker = ask_json.resolve_asker("Make Chapters")
        except ask_json.Unavailable as ex:
            return str(ex)
    box: dict[str, Any] = {"sentences": got["sentences"]}
    opts = {"fps": _wand._fps(scene), "frame_start": int(scene.frame_start),       # pylint: disable=protected-access
            "end_frame": int(scene.frame_end) + 1, "count": int(count), "min_seconds": float(min_seconds),
            "context": scene_reader_ops.context_tokens()}
    stop = threading.Event()
    job_id = int(_JOB["id"]) + 1
    thread = threading.Thread(target=_work, args=(box, asker, opts, stop, job_id), name="bfacw-chapters", daemon=True)
    _JOB.update(running=True, progress="Reading the transcript...", stop=stop, thread=thread, id=job_id,
                scene=scene.name, box=box, message="", level="ok")
    thread.start()
    return ""


# -- the operators ----------------------------------------------------------------------------------

def _wrapped(layout: Any, text: str, width: int) -> None:
    from .highlight_picks_ops import _wrapped as wrapped
    wrapped(layout, text, width)


def _engine() -> tuple[str, str]:
    from .highlight_picks_ops import _engine as engine
    text, kind = engine()
    return text.replace("Find Highlights", "Make Chapters").replace("The search needs", "Making chapters needs"), kind


def _draw_dialog(layout: Any, op: Any) -> None:
    """The dialog: options in boxes with a header each, then where the transcript text goes, then what removes."""
    box = layout.box()
    box.label(text="Count: about how many chapters", icon=safe_icon("SORTSIZE"))
    box.prop(op, "count_auto")
    row = box.row()
    row.active = not op.count_auto
    row.prop(op, "count")
    box = layout.box()
    box.label(text="Shortest: no chapter is shorter than this", icon=safe_icon("TIME"))
    box.prop(op, "min_seconds")
    text, kind = _engine()
    box = layout.box()
    box.label(text="Privacy: who reads the transcript", icon=safe_icon("LOCKED" if kind == "local" else "WORLD"))
    _wrapped(box, text, 56)
    if load(_seq.sequencer_scene(bpy.context) or bpy.context.scene)["markers"]:
        layout.separator()
        layout.operator("bfacw.chapters_remove", icon=safe_icon("X"))


def _draw_progress(layout: Any) -> None:
    box = layout.box()
    box.label(text=_JOB["progress"] or "Working...", icon=safe_icon("SORTTIME"))
    box.operator("bfacw.make_chapters_stop", icon=safe_icon("CANCEL"))


class BFACW_OT_make_chapters(Operator):
    """Make a chapter list from the edit's transcript: timeline markers and a text with a "0:00 Title" line for each chapter, for the description of the video"""
    bl_idname = "bfacw.make_chapters"
    bl_label = "Make Chapters"
    bl_description = agent_line(
        "Make a chapter list from the edit's transcript: timeline markers (CH1, CH2 ...) and a text with a "
        "\"0:00 Title\" line for each chapter, for the description of the video. Chapters made before are replaced; "
        "your own markers stay.",
        needs="the titles, which need a model (Local or Remote)", also="Needs a transcript of the edit.")
    bl_options = {'REGISTER'}          # the markers and the text are written, in one undo step, when the search ends

    count_auto: BoolProperty(  # type: ignore[valid-type]
        name="Auto", default=True, description="As many chapters as the topics need, usually one every few minutes")
    count: IntProperty(  # type: ignore[valid-type]
        name="Chapters", default=6, min=3, max=30, description="About how many chapters (the model decides the exact number)")
    min_seconds: FloatProperty(  # type: ignore[valid-type]
        name="Minimum Length", default=ch.MIN_SECONDS, min=5.0, max=300.0, unit='TIME_ABSOLUTE',
        description="No chapter is shorter than this (video sites ask for at least 10 s)")

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _seq.poll(cls, context)

    def invoke(self, context: Any, _event: Any) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=380, confirm_text="Make Chapters")

    def draw(self, _context: Any) -> None:
        if _JOB["running"]:
            _draw_progress(self.layout)
        else:
            _draw_dialog(self.layout, self)

    def execute(self, context: Any) -> set[str]:
        scene = _seq.sequencer_scene(context)
        why = start(scene, 0 if self.count_auto else self.count, self.min_seconds)
        if why:
            self.report({'ERROR'} if "already" not in why else {'WARNING'}, why)
            return {'CANCELLED'}
        try:
            bpy.ops.bfacw.make_chapters_watch('INVOKE_DEFAULT')
        except RuntimeError:            # no window to watch from: a timer takes the answer instead
            if not bpy.app.timers.is_registered(_job_tick):
                bpy.app.timers.register(_job_tick, first_interval=TICK, persistent=True)
        self.report({'INFO'}, "Making chapters (Esc stops). A message tells when they are on the timeline.")
        return {'FINISHED'}


class BFACW_OT_make_chapters_watch(Operator):
    """Wait for the chapters being made and write them when they are done (Esc stops)"""
    bl_idname = "bfacw.make_chapters_watch"
    bl_label = "Make Chapters"
    bl_description = agent_line(__doc__)
    bl_options = {'INTERNAL'}

    _timer = None

    def invoke(self, context: Any, _event: Any) -> set[str]:
        if not _JOB["running"]:
            return {'CANCELLED'}
        wm = context.window_manager
        self._timer = wm.event_timer_add(TICK, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context: Any) -> None:
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def modal(self, context: Any, event: Any) -> set[str]:
        if not _JOB["running"]:
            self._end(context)
            return {'CANCELLED'}
        if event.type == 'ESC' and _JOB["stop"] is not None:
            _JOB["stop"].set()
            _JOB["progress"] = "Stopping after the current request..."
            return {'RUNNING_MODAL'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        thread = _JOB["thread"]
        if thread is not None and thread.is_alive():
            _status("Make Chapters: {:s} (Esc stops)".format(_JOB["progress"] or "working..."))
            return {'PASS_THROUGH'}
        self._end(context)
        level, line = finish()
        self.report({level}, line)
        return {'FINISHED'}

    def cancel(self, context: Any) -> None:
        self._end(context)


class BFACW_OT_make_chapters_stop(Operator):
    """Stop making chapters; nothing is written"""
    bl_idname = "bfacw.make_chapters_stop"
    bl_label = "Stop"
    bl_description = agent_line("Stop the chapters that are being made. Nothing is written.")
    bl_options = {'REGISTER'}

    def execute(self, _context: Any) -> set[str]:
        if _JOB["running"] and _JOB["stop"] is not None:
            _JOB["stop"].set()
            _JOB["progress"] = "Stopping after the current request..."
        return {'FINISHED'}


class BFACW_OT_chapters_remove(Operator):
    """Remove the chapter markers Make Chapters made (your own markers stay)"""
    bl_idname = "bfacw.chapters_remove"
    bl_label = "Remove Chapters"
    bl_description = agent_line(__doc__)
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _seq.poll(cls, context)

    def execute(self, context: Any) -> set[str]:
        scene = _seq.sequencer_scene(context)
        gone = remove_ours(scene)
        self.report({'INFO'}, "Removed {:d} chapter marker{:s}; the text stays".format(gone, "" if gone == 1 else "s"))
        return {'FINISHED'} if gone else {'CANCELLED'}


# -- registration -----------------------------------------------------------------------------------

_CLASSES = (BFACW_OT_make_chapters, BFACW_OT_make_chapters_watch, BFACW_OT_make_chapters_stop,
            BFACW_OT_chapters_remove)
_DONE: list[type] = []


def _persistent(fn: Any) -> Any:
    return getattr(bpy.app.handlers, "persistent", lambda f: f)(fn)


@_persistent
def _on_load(*_args: Any) -> None:
    """Before another file replaces this one: forget a running search, so nothing of this file is written into the
    next one that happens to have the same scene name."""
    _reset_job()


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
        _DONE.append(cls)
    handlers = getattr(bpy.app.handlers, "load_pre", None)
    if handlers is not None and _on_load not in handlers:
        handlers.append(_on_load)


def unregister() -> None:
    if bpy.app.timers.is_registered(_job_tick):
        bpy.app.timers.unregister(_job_tick)
    handlers = getattr(bpy.app.handlers, "load_pre", None)
    while handlers is not None and _on_load in handlers:
        handlers.remove(_on_load)
    _reset_job()
    for cls in reversed(_DONE):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _DONE.clear()
