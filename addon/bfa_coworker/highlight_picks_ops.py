# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Highlights H4 (#614), the Blender side: the **Highlights** panel in the Sequencer's Coworker tab, the picks on
the scene, Find, Preview, Check, Dismiss and the markers switch.  The store, the placing after a cut and the
marker plan are ``highlight_picks.py`` (bpy-free).

The panel is a wand panel (``_misc/wand_panel_rules.md``): the main action (Find Highlights...) first and large;
its options in its dialog; the results in a box; one status line.  It is in the sidebar, which Blender and
Bforartists both have, and does not need the chat.

Find reads the edit's sentences on the main thread, then asks the engine on a worker thread
(``highlights.find_highlights``, H3 #613) with a progress line and a working Stop; the main thread's timer takes
the result.  Make Short is H5 (#615), ``highlight_short.py``: the button asks which polish steps to run, then builds the scene.
"""

from __future__ import annotations

__all__ = (
    "BFACW_OT_highlights_check",
    "BFACW_OT_highlights_dismiss",
    "BFACW_OT_highlights_find",
    "BFACW_OT_highlights_make_short",
    "BFACW_OT_highlights_markers",
    "BFACW_OT_highlights_preview",
    "BFACW_OT_highlights_stop",
    "BFACW_PT_highlights",
    "register",
    "unregister",
)

import hashlib
import importlib
import textwrap
import threading
from typing import Any

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty  # pylint: disable=import-error
from bpy.types import Operator, Panel  # pylint: disable=import-error

from . import highlight_picks as hp
from . import scene_reader_ops
from .shared import icon as safe_icon
from .wand import scene as _seq
from .wand import sequencer as _wand
from .wand.spec import agent_line

CATEGORY = "Coworker"
PANEL_LABEL = "Highlights"
VIEWS = frozenset({"SEQUENCER", "SEQUENCER_PREVIEW"})
SECONDS = (("15", "15 s", "Short pieces: one quick idea", "TIME", 0),
           ("30", "30 s", "A piece with a setup and a payoff", "TIME", 1),
           ("60", "60 s", "The longest a short may be", "TIME", 2))
KINDS = (("auto", "Auto", "Decide from the first minutes of the video", "AUTO", 0),
         ("podcast", "Podcast or Interview", "An exchange between speakers, a story", "OUTLINER_OB_GROUP_INSTANCE", 1),
         ("tutorial", "Tutorial", "One tip with a result", "TOOL_SETTINGS", 2),
         ("talk", "Talk or Lecture", "A claim and its proof", "SPEAKER", 3))
TICK = 0.25
STILL_TICKS = 12          # 3 s without the frame moving while something plays: Preview stops watching
NO_FINDER = "The search (Highlights H3, #613) is not in this build yet."


# -- the store on the scene --------------------------------------------------------------------------

def load(scene: Any) -> dict[str, Any]:
    return hp.loads(scene.get(hp.PROP, ""))


def save(scene: Any, store: dict[str, Any]) -> None:
    scene[hp.PROP] = hp.dumps(store)


def edit_signature(scene: Any) -> str:
    """A hash of the strips' places: it changes when the edit is cut, moved or muted (cheap: no transcripts)."""
    ed = getattr(scene, "sequence_editor", None)
    if ed is None:
        return ""
    rows = []
    for k, s in enumerate(ed.strips_all):
        if k >= 5000:
            break
        rows.append((s.name, s.type, int(s.channel), int(s.left_handle), int(s.right_handle),
                     round(float(getattr(s, "content_start", 0.0)), 3), bool(s.mute)))
    return hashlib.sha1(repr(sorted(rows)).encode("utf-8")).hexdigest()[:12]


def _find(store: dict[str, Any], pick_id: str) -> dict[str, Any] | None:
    return next((p for p in store["picks"] if p["id"] == pick_id), None)


def sync_markers(scene: Any, store: dict[str, Any]) -> None:
    """Make the scene's timeline markers match the store's switch (only markers the store made are touched)."""
    markers = scene.timeline_markers
    remove, names, add = hp.marker_plan(store, [(m.name, m.frame) for m in markers], bool(store["markers_on"]))
    gone = {n for n, _f in remove}
    for m in [m for m in markers if m.name in gone]:
        markers.remove(m)
    for name, frame in add:
        markers.new(name, frame=frame)
    store["markers"] = names


def check(scene: Any) -> tuple[dict[str, int] | None, str]:
    """Place every pick again in the edit as it plays now and keep the result on the scene.  ``(counts, "")``,
    or ``(None, why)`` when the edit's speech can't be read (the picks stay as they were)."""
    store = load(scene)
    try:
        got = scene_reader_ops.read(scene)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return None, "Couldn't read the edit: {!s}".format(ex)
    if not got["sentences"]:
        return None, "No speech to place the picks in: " + " ".join(got["report"]["messages"])
    counts = hp.place_all(store, got["sentences"], _wand._fps(scene))      # pylint: disable=protected-access
    store["found"] = dict(store["found"], edit=edit_signature(scene))
    if store["markers_on"]:
        sync_markers(scene, store)
    save(scene, store)
    return counts, ""


def _counts_text(counts: dict[str, int]) -> str:
    parts = ["{:d} placed again".format(counts["moved"])] if counts["moved"] else []
    if counts["changed"]:
        parts.append("{:d} changed, check {:s}".format(counts["changed"], "it" if counts["changed"] == 1 else "them"))
    if counts["gone"]:
        parts.append("{:d} no longer in the edit".format(counts["gone"]))
    return ", ".join(parts) or "All picks are where they were"


def _redraw() -> None:
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        for area in win.screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                area.tag_redraw()


def _poll(cls: Any, context: Any) -> bool:
    return _seq.poll(cls, context)


# -- Find (a worker thread, a timer on the main thread) -------------------------------------------------

def _idle_job() -> dict[str, Any]:
    return {"running": False, "progress": "", "stop": None, "thread": None, "scene": "", "box": None,
            "message": "", "level": "ok", "id": 0}


_JOB: dict[str, Any] = _idle_job()


def _reset_job() -> None:
    """Forget a search for good (a file was loaded, or the add-on is going): its worker is told to stop, its
    answer is dropped, and Find works again.  The next job gets a new id, so the old worker can't touch it."""
    if _JOB["stop"] is not None:
        _JOB["stop"].set()
    new = _idle_job()
    new["id"] = int(_JOB["id"]) + 1
    _JOB.clear()
    _JOB.update(new)


def _finder() -> Any:
    try:
        return importlib.import_module(__package__ + ".highlights")
    except ImportError:
        return None


def _engine() -> tuple[str, str]:
    """The line the dialog shows about where the transcript text goes: ``(text, kind)``.  The kind is the one
    ``ask_json.engine_kind`` decides, the decision the search itself acts on."""
    from . import ask_json, llm_manager
    try:
        mode = str(bpy.context.preferences.addons[__package__].preferences.operating_mode)
    except (KeyError, AttributeError):
        mode = ""
    try:
        cfg = llm_manager.get_config()
    except Exception:  # pylint: disable=broad-exception-caught
        cfg = None
    kind = ask_json.engine_kind(mode, cfg)
    host = ""
    if kind == "remote":
        from urllib.parse import urlparse
        host = urlparse(str(getattr(cfg, "remote_api_url", "") or "")).netloc
    return hp.engine_line(kind, host)


def _job_tick() -> float | None:
    """On the main thread: show the progress; when the worker is over, keep its picks on the scene."""
    if not _JOB["running"]:
        return None
    thread = _JOB["thread"]
    _redraw()
    if thread is not None and thread.is_alive():
        return TICK
    box = _JOB["box"] or {}
    _JOB.update(running=False, thread=None, progress="")
    scene = bpy.data.scenes.get(_JOB["scene"])
    if scene is None:
        return None
    if box.get("error"):
        _JOB.update(message=box["error"], level="error")
    elif "result" not in box:
        _JOB.update(message="The search ended without an answer. Try Find Highlights again.", level="error")
    else:
        out = box["result"]
        sentences = box["sentences"]
        store = load(scene)
        found = hp.from_found(out["picks"], sentences)
        store["picks"] = found
        store["found"] = {"edit": box["edit"], "seconds": int(box["seconds"]), "kind": str(box["kind"]),
                          "count": int(box["count"])}
        if store["markers_on"]:
            sync_markers(scene, store)
        save(scene, store)
        _wand._push_undo("Find Highlights")      # pylint: disable=protected-access
        notes = " ".join(out["report"].get("messages") or [])
        _JOB.update(message=(hp.summary(store, _wand._fps(scene), scene.frame_start).splitlines()[0] + " " + notes).strip(),   # pylint: disable=protected-access
                    level="ok" if found else "warn", summary=hp.summary(store, _wand._fps(scene), scene.frame_start))   # pylint: disable=protected-access
    _redraw()
    return None


def _work(finder: Any, box: dict[str, Any], asker: Any, opts: dict[str, Any], stop: Any, job_id: int) -> None:
    def progress(text: Any) -> None:
        if _JOB["id"] == job_id:           # a worker from a job that was reset must not write into the next one
            _JOB["progress"] = str(text)
    try:
        box["result"] = finder.find_highlights(
            box["sentences"], opts["fps"], asker, kind=opts["kind"], max_seconds=opts["seconds"],
            count=opts["count"], context=opts["context"], should_stop=stop.is_set,
            progress=progress)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        box["error"] = str(ex) or type(ex).__name__


class BFACW_OT_highlights_find(Operator):
    """Find the pieces of this edit that would stand alone as a short"""
    bl_idname = "bfacw.highlights_find"
    bl_label = "Find Highlights..."
    bl_description = agent_line(
        "Find the pieces of this edit that would stand alone as a short, each with a title and the reason it was "
        "picked. The edit is never changed; Preview plays a pick, Make Short builds it.",
        needs="the search, which needs a model (Local or Remote)", also="Needs a transcript of the edit.")
    bl_options = {'REGISTER'}

    seconds: EnumProperty(name="Longest", items=SECONDS, default="60")                      # type: ignore[valid-type]
    count: IntProperty(name="How many", min=3, max=10, default=5)                           # type: ignore[valid-type]
    kind: EnumProperty(name="Kind of video", items=KINDS, default="auto")                   # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _poll(cls, context)

    def invoke(self, context: Any, _event: Any) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=380, confirm_text="Find Highlights")

    def draw(self, _context: Any) -> None:
        _draw_dialog(self.layout, self)

    def execute(self, context: Any) -> set[str]:
        if _JOB["running"]:
            self.report({'WARNING'}, "A search is already running: Stop it first")
            return {'CANCELLED'}
        finder = _finder()
        if finder is None:
            self.report({'ERROR'}, NO_FINDER)
            return {'CANCELLED'}
        scene = _seq.sequencer_scene(context)
        ed = scene.sequence_editor
        if len(ed.meta_stack):
            self.report({'ERROR'}, "Leave the meta strip first (Tab): the edit is read from its top level")
            return {'CANCELLED'}
        try:
            got = scene_reader_ops.read(scene, scene_reader_ops.context_tokens())
        except Exception as ex:  # pylint: disable=broad-exception-caught
            self.report({'ERROR'}, "Couldn't read the edit: {!s}".format(ex))
            return {'CANCELLED'}
        if not got["sentences"]:
            self.report({'ERROR'}, "No speech to pick from. Make a transcript first (Edit Transcript or Auto "
                                   "Captions). " + " ".join(got["report"]["messages"]))
            return {'CANCELLED'}
        from . import ask_json
        try:
            asker = ask_json.resolve_asker("Find Highlights")
        except ask_json.Unavailable as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        # the edit's signature belongs to the sentences read now, not to the edit as it is when the search ends
        box: dict[str, Any] = {"sentences": got["sentences"], "seconds": self.seconds, "kind": self.kind,
                               "count": self.count, "edit": edit_signature(scene)}
        opts = {"fps": _wand._fps(scene), "kind": self.kind, "seconds": float(self.seconds),      # pylint: disable=protected-access
                "count": int(self.count), "context": scene_reader_ops.context_tokens()}
        stop = threading.Event()
        job_id = int(_JOB["id"]) + 1
        thread = threading.Thread(target=_work, args=(finder, box, asker, opts, stop, job_id), name="bfacw-highlights",
                                  daemon=True)
        _JOB.update(running=True, progress="Reading the transcript...", stop=stop, thread=thread, id=job_id,
                    scene=scene.name, box=box, message="", level="ok", summary="")
        thread.start()
        if not bpy.app.timers.is_registered(_job_tick):
            # persistent: a file load must not drop the timer and leave the panel stuck on "Searching"
            bpy.app.timers.register(_job_tick, first_interval=TICK, persistent=True)
        self.report({'INFO'}, "Searching {:d} sentences. The Highlights panel shows the progress.".format(
            len(got["sentences"])))
        return {'FINISHED'}


class BFACW_OT_highlights_stop(Operator):
    """Stop the search; the picks found so far are kept"""
    bl_idname = "bfacw.highlights_stop"
    bl_label = "Stop"
    bl_description = agent_line("Stop the search that is running. The picks found so far are kept.")
    bl_options = {'REGISTER'}

    def execute(self, _context: Any) -> set[str]:
        if not _JOB["running"]:
            return {'FINISHED'}
        if _JOB["stop"] is not None:
            _JOB["stop"].set()
        thread = _JOB["thread"]
        if thread is None or not thread.is_alive():
            _job_tick()            # the worker is over but nobody took its answer (the timer was lost): take it now
        else:
            # H3 looks at the stop between requests: one already sent to the engine is waited for
            _JOB["progress"] = "Stopping after the current request..."
        return {'FINISHED'}


# -- Check, Preview, Make Short, Dismiss, markers --------------------------------------------------------

class _PickOperator(Operator):
    pick: StringProperty(name="Pick", options={'HIDDEN', 'SKIP_SAVE'})        # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _poll(cls, context)

    def _placed(self, context: Any) -> tuple[Any, dict[str, Any] | None]:
        """The scene and the pick, placed again first (a stale pick is refused with the reason)."""
        scene = _seq.sequencer_scene(context)
        counts, why = check(scene)
        if counts is None:
            self.report({'ERROR'}, why)
            return scene, None
        found = _find(load(scene), self.pick)
        if found is None:
            self.report({'ERROR'}, "That pick is no longer in the list")
            return scene, None
        if found["state"] != hp.State.OK:
            self.report({'WARNING'}, "'{:s}': {:s}. The edit was cut after this pick was found; use Find Highlights "
                                     "again or Dismiss it.".format(found["title"], hp.state_text(found["state"]).lower()))
            _redraw()
            return scene, None
        return scene, found


class BFACW_OT_highlights_check(Operator):
    """Place the picks again after the edit was cut"""
    bl_idname = "bfacw.highlights_check"
    bl_label = "Check Picks"
    bl_description = agent_line("Place the picks again from their words after the edit was cut: new frames, "
                                "'changed, check it', or 'no longer in the edit'. Preview and Make Short do this first.",
                                also="Needs a transcript of the edit.")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _poll(cls, context)

    def execute(self, context: Any) -> set[str]:
        counts, why = check(_seq.sequencer_scene(context))
        if counts is None:
            self.report({'ERROR'}, why)
            return {'CANCELLED'}
        self.report({'WARNING'} if counts["changed"] or counts["gone"] else {'INFO'}, _counts_text(counts))
        return {'FINISHED'}


_PREVIEW: dict[str, Any] = {"scene": "", "orig": None, "end": 0}


def _stop_playback() -> None:
    win, playing = _window_playing(bpy.data.scenes.get(_PREVIEW["scene"]))
    if playing and win is not None:
        try:
            with bpy.context.temp_override(window=win, screen=win.screen):
                bpy.ops.screen.animation_cancel(restore_frame=False)
        except RuntimeError:
            pass


def _on_frame(scene: Any, *_args: Any) -> None:
    """Stops the playback on the pick's last frame (the preview range would loop it)."""
    if _PREVIEW["orig"] is not None and scene.name == _PREVIEW["scene"] and scene.frame_current >= _PREVIEW["end"]:
        _stop_playback()


def _restore_preview() -> None:
    handlers = getattr(bpy.app.handlers, "frame_change_post", None)
    while handlers is not None and _on_frame in handlers:
        handlers.remove(_on_frame)
    orig = _PREVIEW["orig"]
    scene = bpy.data.scenes.get(_PREVIEW["scene"])
    _PREVIEW["orig"] = None
    if orig is None or scene is None:
        return
    scene.use_preview_range, scene.frame_preview_start, scene.frame_preview_end = orig
    _redraw()


def _window_playing(scene: Any = None) -> tuple[Any, bool]:
    """``(the window to play in, is anything playing)``: the window that shows *scene* when there is one (the
    Sequencer's scene can differ from the first window's), else the first window."""
    wm = bpy.context.window_manager
    wins = list(wm.windows) if wm else []
    mine = [w for w in wins if scene is not None and getattr(w, "scene", None) is scene]
    return (mine or wins or [None])[0], any(w.screen.is_animation_playing for w in wins)


def _preview_tick() -> float | None:
    """Stops the playback at the end of the pick (it plays once) and puts the preview range back."""
    scene = bpy.data.scenes.get(_PREVIEW["scene"])
    if _PREVIEW["orig"] is None or scene is None:
        return None
    _win, playing = _window_playing(scene)
    last = _PREVIEW.get("seen", 0)
    _PREVIEW["seen"] = scene.frame_current
    # playback that never moves this scene's frame (another scene is playing): give up after a while
    _PREVIEW["still"] = _PREVIEW.get("still", 0) + 1 if playing and scene.frame_current == last else 0
    if _PREVIEW["still"] >= STILL_TICKS:
        _stop_playback()
        playing = False
    if playing and (scene.frame_current >= _PREVIEW["end"] or scene.frame_current < last):    # reached the end, or looped
        _stop_playback()
        playing = False
    if playing:
        return TICK
    _restore_preview()
    return None


class BFACW_OT_highlights_preview(_PickOperator):
    """Play this pick inside the long edit"""
    bl_idname = "bfacw.highlights_preview"
    bl_label = "Preview"
    bl_description = agent_line("Play this pick inside the long edit: the preview range is set to the pick, it plays "
                                "once, and the range is put back. Nothing is changed.", also="Needs a transcript of the edit.")
    bl_options = {'REGISTER'}

    def execute(self, context: Any) -> set[str]:
        scene, found = self._placed(context)
        if found is None:
            return {'CANCELLED'}
        if _PREVIEW["orig"] is not None:
            _restore_preview()
        first, last = hp.preview_range(found, scene.frame_start, scene.frame_end)
        _PREVIEW.update(scene=scene.name, end=last,
                        orig=(bool(scene.use_preview_range), int(scene.frame_preview_start), int(scene.frame_preview_end)))
        scene.use_preview_range = True
        scene.frame_preview_start, scene.frame_preview_end = first, last
        scene.frame_current = first
        _PREVIEW["seen"] = first
        _PREVIEW["still"] = 0
        handlers = getattr(bpy.app.handlers, "frame_change_post", None)
        if handlers is not None and _on_frame not in handlers:
            handlers.append(_on_frame)
        win, playing = _window_playing(scene)
        if win is not None and playing:
            with bpy.context.temp_override(window=win, screen=win.screen):
                bpy.ops.screen.animation_cancel(restore_frame=False)
        if win is not None:
            with bpy.context.temp_override(window=win, screen=win.screen):
                bpy.ops.screen.animation_play()
        if not bpy.app.timers.is_registered(_preview_tick):
            bpy.app.timers.register(_preview_tick, first_interval=TICK, persistent=True)
        self.report({'INFO'}, "Playing '{:s}' ({:.0f} s)".format(found["title"], found["seconds"]))
        return {'FINISHED'}


class BFACW_OT_highlights_make_short(_PickOperator):
    """Build this pick as its own vertical scene"""
    bl_idname = "bfacw.highlights_make_short"
    bl_label = "Make Short"
    bl_description = agent_line("Build this pick as its own scene, already cut, vertical and captioned. "
                                "The long edit is never changed.", also="Needs a transcript of the edit.")
    bl_options = {'REGISTER', 'UNDO'}

    silences: BoolProperty(name="Remove Silences", default=True, description=(      # type: ignore[valid-type]
        "Cut the pauses inside the short (tighter than for a long edit); the rest of the edit is not touched"))
    vertical: BoolProperty(name="Make Vertical", default=True, description=(        # type: ignore[valid-type]
        "Make the short 9:16, inside the short (not a second copy)"))
    captions: BoolProperty(name="Auto Captions", default=True, description=(        # type: ignore[valid-type]
        "Caption the speech of the short in the style Auto Captions was last used with"))
    hook: BoolProperty(name="Hook Title", default=True, description=(               # type: ignore[valid-type]
        "Put the pick's hook line over the first three seconds"))

    def invoke(self, context: Any, _event: Any) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context: Any) -> None:
        box = self.layout.box()
        box.label(text="Polish the short: each step is optional", icon=safe_icon("SEQUENCE"))
        for prop, wand_icon, fallback in (("silences", "remove_silences", "SPEAKER"), ("vertical", "vertical", "SEQ_PREVIEW"),
                                          ("captions", "captions", "FONT_DATA"), ("hook", "", "TEXT")):
            value = 0
            if wand_icon:
                try:
                    from .wand import icons as _icons      # the wand panels' own full-colour icons
                    value = _icons.icon_id(wand_icon)
                except Exception:  # pylint: disable=broad-exception-caught
                    value = 0
            if value:
                box.prop(self, prop, icon_value=value)
            else:
                box.prop(self, prop, icon=safe_icon(fallback))
        _wrapped(box, "The short is a copy of the scene with its range set to the pick. The long edit is not changed; "
                      "one undo removes the short. Rendering is your click.", 56, "INFO")

    def execute(self, context: Any) -> set[str]:
        scene, found = self._placed(context)
        if found is None:
            return {'CANCELLED'}
        short = importlib.import_module(__package__ + ".highlight_short")
        return short.make_short(self, scene, found)


class BFACW_OT_highlights_dismiss(_PickOperator):
    """Remove this pick from the list"""
    bl_idname = "bfacw.highlights_dismiss"
    bl_label = "Dismiss"
    bl_description = agent_line("Remove this pick from the list (and its markers). The edit is not changed.")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Any) -> set[str]:
        scene = _seq.sequencer_scene(context)
        store = load(scene)
        found = _find(store, self.pick)
        if found is None:
            self.report({'WARNING'}, "That pick is no longer in the list")
            return {'CANCELLED'}
        store["picks"] = [p for p in store["picks"] if p["id"] != self.pick]
        if store["markers_on"]:
            sync_markers(scene, store)
        save(scene, store)
        self.report({'INFO'}, "Dismissed '{:s}'".format(found["title"]))
        _redraw()
        return {'FINISHED'}


class BFACW_OT_highlights_markers(Operator):
    """Mark the picks on the timeline, or take the markers away again"""
    bl_idname = "bfacw.highlights_markers"
    bl_label = "Markers"
    bl_description = agent_line("Switch on: one pair of timeline markers per pick (start and end). Switch off: "
                                "the markers Highlights made are removed, yours are never touched. Off by default.")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return _poll(cls, context)

    def execute(self, context: Any) -> set[str]:
        scene = _seq.sequencer_scene(context)
        store = load(scene)
        store["markers_on"] = not store["markers_on"]
        sync_markers(scene, store)
        save(scene, store)
        self.report({'INFO'}, "Markers on: {:d} pairs".format(len(store["markers"]) // 2) if store["markers_on"]
                    else "Markers removed")
        _redraw()
        return {'FINISHED'}


# -- the panel ----------------------------------------------------------------------------------------

def _draw_dialog(layout: Any, op: Any) -> None:
    """The Find dialog: options in boxes with a header each, then where the transcript text goes."""
    box = layout.box()
    box.label(text="Length: the longest a short may be", icon=safe_icon("TIME"))
    box.prop(op, "seconds", expand=True)
    box = layout.box()
    box.label(text="Count: how many to look for", icon=safe_icon("SORTSIZE"))
    box.prop(op, "count", text="")
    box = layout.box()
    box.label(text="Kind of video: what makes a good piece", icon=safe_icon("SEQUENCE"))
    box.prop(op, "kind", text="")
    text, kind = _engine()
    box = layout.box()
    box.label(text="Privacy: who reads the transcript", icon=safe_icon("LOCKED" if kind == "local" else "WORLD"))
    _wrapped(box, text, 56)


def _wrapped(layout: Any, text: str, width: int, icon: str = "NONE") -> None:
    """*text* in lines of at most *width* characters; the first has *icon*, the next ones continue it."""
    col = layout.column(align=True)
    col.scale_y = 0.85
    for k, line in enumerate(textwrap.wrap(text, max(16, width)) or [""]):
        if k == 0 and icon != "NONE":
            col.label(text=line, icon=safe_icon(icon))
        else:
            col.label(text=line)


def _draw_pick(layout: Any, pick: dict[str, Any], fps: float, frame_start: int, width: int) -> None:
    """One pick: title, where and how long, why, then its buttons."""
    box = layout.box()
    icon = {"ok": "SOLO_ON", "changed": "ERROR", "gone": "CANCEL"}[pick["state"]]
    box.label(text=pick["title"], icon=safe_icon(icon))
    note = hp.state_text(pick["state"])
    box.label(text="{:s} at {:s}{:s}".format("{:.0f} s".format(pick["seconds"]),
                                           hp.clock(pick["start"] - frame_start, fps), "  -  " + note if note else ""),
              icon=safe_icon("TIME"))
    if pick["reason"]:
        _wrapped(box, pick["reason"], width, "INFO")
    row = box.row(align=True)
    ok = pick["state"] == hp.State.OK
    sub = row.row(align=True)
    sub.enabled = ok
    for idname, label, icon_name in (("bfacw.highlights_preview", "Preview", "PLAY"),
                                     ("bfacw.highlights_make_short", "Make Short", "SCENE_DATA")):
        sub.operator(idname, text=label, icon=safe_icon(icon_name)).pick = pick["id"]
    row.operator("bfacw.highlights_dismiss", text="Dismiss", icon=safe_icon("X")).pick = pick["id"]


def _draw_panel(layout: Any, context: Any) -> None:
    scene = _seq.sequencer_scene(context)
    store = load(scene)
    fps = _wand._fps(scene)       # pylint: disable=protected-access
    width = int((context.region.width - 64) / 7) if context.region else 40
    main = layout.row()
    main.scale_y = 1.4
    main.operator("bfacw.highlights_find", icon=safe_icon("VIEWZOOM"))
    if _JOB["running"]:
        row = layout.row(align=True)
        row.label(text=_JOB["progress"] or "Searching...", icon=safe_icon("SORTTIME"))
        row.operator("bfacw.highlights_stop", text="Stop", icon=safe_icon("CANCEL"))
    elif _JOB["message"]:
        _wrapped(layout, _JOB["message"], width, "ERROR" if _JOB["level"] != "ok" else "CHECKMARK")
    picks = store["picks"]
    if picks:
        stale = store["found"].get("edit", "") != edit_signature(scene)
        for pick in picks:
            _draw_pick(layout, pick, fps, scene.frame_start, width)
        if stale:
            _wrapped(layout, "The edit changed since these were found.", width, "ERROR")
        row = layout.row(align=True)
        row.operator("bfacw.highlights_markers", text="Markers", icon=safe_icon("MARKER_HLT"),
                     depress=bool(store["markers_on"]))
        row.operator("bfacw.highlights_check", text="Check Picks", icon=safe_icon("FILE_REFRESH"), depress=False)
    elif not _JOB["running"] and not _JOB["message"]:
        _wrapped(layout, "No highlights yet. Find Highlights looks through the transcript for pieces that stand "
                         "alone.", width, "INFO")


class BFACW_PT_highlights(Panel):
    """The Highlights panel in the Sequencer's Coworker tab (Blender and Bforartists)"""
    bl_label = PANEL_LABEL
    bl_idname = "BFACW_PT_highlights"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 1          # right under Reframe

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (getattr(context.space_data, "view_type", "") in VIEWS
                and _seq.sequencer_scene(context) is not None)

    def draw_header(self, _context: Any) -> None:
        self.layout.label(icon=safe_icon("SOLO_ON"))

    def draw(self, context: Any) -> None:
        scene = _seq.sequencer_scene(context)
        if getattr(scene, "sequence_editor", None) is None:
            self.layout.label(text="No strips in this scene yet", icon=safe_icon("INFO"))
            return
        _draw_panel(self.layout, context)


# -- registration -----------------------------------------------------------------------------------

_CLASSES = (BFACW_OT_highlights_find, BFACW_OT_highlights_stop, BFACW_OT_highlights_check,
            BFACW_OT_highlights_preview, BFACW_OT_highlights_make_short, BFACW_OT_highlights_dismiss,
            BFACW_OT_highlights_markers, BFACW_PT_highlights)
_DONE: list[type] = []


def _persistent(fn: Any) -> Any:
    return getattr(bpy.app.handlers, "persistent", lambda f: f)(fn)


@_persistent
def _on_load(*_args: Any) -> None:
    """Before another file replaces this one: put the preview range back on the scene that is still here, and forget
    a running search, so nothing of this file is applied to the next one that happens to have the same scene name."""
    try:
        _restore_preview()
    except Exception:  # pylint: disable=broad-exception-caught
        _PREVIEW.update(orig=None)
    if bpy.app.timers.is_registered(_preview_tick):
        bpy.app.timers.unregister(_preview_tick)
    _reset_job()


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
        _DONE.append(cls)
    handlers = getattr(bpy.app.handlers, "load_pre", None)
    if handlers is not None and _on_load not in handlers:
        handlers.append(_on_load)


def unregister() -> None:
    for fn in (_job_tick, _preview_tick):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    handlers = getattr(bpy.app.handlers, "load_pre", None)
    while handlers is not None and _on_load in handlers:
        handlers.remove(_on_load)
    _reset_job()
    _restore_preview()
    for cls in reversed(_DONE):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _DONE.clear()
