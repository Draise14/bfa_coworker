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
the result.  Make Short is H5 (#615): the button is here, and says so until that module exists.
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
from bpy.props import EnumProperty, IntProperty, StringProperty  # pylint: disable=import-error
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
NO_SHORT = "Make Short is built in Highlights H5 (#615) and is not in this build yet."
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
    got = scene_reader_ops.read(scene)
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

_JOB: dict[str, Any] = {"running": False, "progress": "", "stop": None, "thread": None, "scene": "", "box": None,
                        "message": "", "level": "INFO"}


def _finder() -> Any:
    try:
        return importlib.import_module(__package__ + ".highlights")
    except ImportError:
        return None


def _engine() -> tuple[str, str]:
    """The line the dialog shows about where the transcript text goes: ``(text, kind)``."""
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        mode = str(prefs.operating_mode)
    except (KeyError, AttributeError):
        mode = ""
    host = ""
    if mode == "REMOTE_API":
        try:
            from urllib.parse import urlparse
            from . import llm_manager
            host = urlparse(str(llm_manager.get_config().remote_api_url)).netloc
        except Exception:  # pylint: disable=broad-exception-caught
            host = ""
    return hp.engine_line(mode, host)


def _job_tick() -> float | None:
    """On the main thread: show the progress; when the worker is over, keep its picks on the scene."""
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
        _JOB.update(message=box["error"], level="ERROR")
    else:
        out = box["result"]
        sentences = box["sentences"]
        store = load(scene)
        found = hp.from_found(out["picks"], sentences)
        store["picks"] = found
        store["found"] = {"edit": edit_signature(scene), "seconds": int(box["seconds"]), "kind": str(box["kind"]),
                          "count": int(box["count"])}
        if store["markers_on"]:
            sync_markers(scene, store)
        save(scene, store)
        _wand._push_undo("Find Highlights")      # pylint: disable=protected-access
        notes = " ".join(out["report"].get("messages") or [])
        _JOB.update(message=(hp.summary(store, _wand._fps(scene), scene.frame_start).splitlines()[0] + " " + notes).strip(),   # pylint: disable=protected-access
                    level="INFO" if found else "WARNING", summary=hp.summary(store, _wand._fps(scene), scene.frame_start))   # pylint: disable=protected-access
    _redraw()
    return None


def _work(finder: Any, box: dict[str, Any], asker: Any, opts: dict[str, Any]) -> None:
    try:
        box["result"] = finder.find_highlights(
            box["sentences"], opts["fps"], asker, kind=opts["kind"], max_seconds=opts["seconds"],
            count=opts["count"], context=opts["context"], should_stop=_JOB["stop"].is_set,
            progress=lambda text: _JOB.update(progress=str(text)))
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
        box: dict[str, Any] = {"sentences": got["sentences"], "seconds": self.seconds, "kind": self.kind,
                               "count": self.count}
        opts = {"fps": _wand._fps(scene), "kind": self.kind, "seconds": float(self.seconds),      # pylint: disable=protected-access
                "count": int(self.count), "context": scene_reader_ops.context_tokens()}
        thread = threading.Thread(target=_work, args=(finder, box, asker, opts), name="bfacw-highlights", daemon=True)
        _JOB.update(running=True, progress="Reading the transcript...", stop=threading.Event(), thread=thread,
                    scene=scene.name, box=box, message="", level="INFO", summary="")
        thread.start()
        bpy.app.timers.register(_job_tick, first_interval=TICK)
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
        if _JOB["stop"] is not None:
            _JOB["stop"].set()
            _JOB["progress"] = "Stopping..."
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
    win, playing = _window_playing()
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


def _window_playing() -> tuple[Any, bool]:
    wm = bpy.context.window_manager
    wins = list(wm.windows) if wm else []
    return (wins[0] if wins else None), any(w.screen.is_animation_playing for w in wins)


def _preview_tick() -> float | None:
    """Stops the playback at the end of the pick (it plays once) and puts the preview range back."""
    scene = bpy.data.scenes.get(_PREVIEW["scene"])
    if _PREVIEW["orig"] is None or scene is None:
        return None
    _win, playing = _window_playing()
    last = _PREVIEW.get("seen", 0)
    _PREVIEW["seen"] = scene.frame_current
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
        handlers = getattr(bpy.app.handlers, "frame_change_post", None)
        if handlers is not None and _on_frame not in handlers:
            handlers.append(_on_frame)
        win, playing = _window_playing()
        if win is not None and playing:
            with bpy.context.temp_override(window=win, screen=win.screen):
                bpy.ops.screen.animation_cancel(restore_frame=False)
        if win is not None:
            with bpy.context.temp_override(window=win, screen=win.screen):
                bpy.ops.screen.animation_play()
        if not bpy.app.timers.is_registered(_preview_tick):
            bpy.app.timers.register(_preview_tick, first_interval=TICK)
        self.report({'INFO'}, "Playing '{:s}' ({:.0f} s)".format(found["title"], found["seconds"]))
        return {'FINISHED'}


class BFACW_OT_highlights_make_short(_PickOperator):
    """Build this pick as its own vertical scene"""
    bl_idname = "bfacw.highlights_make_short"
    bl_label = "Make Short"
    bl_description = agent_line("Build this pick as its own scene, already cut, vertical and captioned. "
                                "The long edit is never changed.", also="Needs a transcript of the edit.")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Any) -> set[str]:
        scene, found = self._placed(context)
        if found is None:
            return {'CANCELLED'}
        try:
            short = importlib.import_module(__package__ + ".highlight_short")
        except ImportError:
            self.report({'WARNING'}, NO_SHORT)
            return {'CANCELLED'}
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
        _wrapped(layout, _JOB["message"], width, "ERROR" if _JOB["level"] != "INFO" else "CHECKMARK")
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


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
        _DONE.append(cls)


def unregister() -> None:
    for fn in (_job_tick, _preview_tick):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    if _JOB["stop"] is not None:
        _JOB["stop"].set()
    _restore_preview()
    for cls in reversed(_DONE):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _DONE.clear()
