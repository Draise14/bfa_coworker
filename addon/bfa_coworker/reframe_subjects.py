# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Follow Subject on the real Sequencer (#592): the Blender side of :mod:`subject_find`.

``start`` renders a few small pictures per shot (and the same moment a fraction of a second later, so
what moves can be seen), turns them into small grey grids, and on a worker thread asks the vision model
for faces and people (when asked for and connected) and runs ``subject_find.analyse``.  When the
worker is done a timer applies the result on the main thread:

* one subject (or ``layout="single"``): the pan of the strip is keyed on its centre (the same keys as
  the earlier Follow Subject, ``reframe.apply_follow``);
* two subjects apart (or ``layout="split"``): the strip becomes a *Track All* split (``reframe_layouts``:
  two ordinary strips of the same footage, top and bottom) and each part is keyed to its own subject:
  scale, offset and crop, so each part shows its subject at its usual size;
* nothing found: the shot keeps its centre crop and the message says so.

Nothing leaves the machine except, on a Remote engine, the small sampled pictures sent for face
finding; ``Follow = screen`` sends nothing.  Frames are never downloaded or saved outside the
temporary folder.
"""

from __future__ import annotations

__all__ = (
    "FOLLOWS",
    "LAYOUTS",
    "apply_split",
    "cancel",
    "clear_strip_keys",
    "last_result",
    "progress",
    "start",
    "take_ready",
)

import tempfile
import threading
from typing import Any, Iterable

import bpy  # pylint: disable=import-error

from . import reframe, reframe_follow
from . import reframe_math as rm
from . import subject_find as sf
from .wand import reframe_apply as ra
from .wand import reframe_layout as rl

FOLLOWS = ("auto", "face", "screen")
LAYOUTS = ("auto", "single", "split")
GRID_W = 96
MAX_RUN_SAMPLES = 120        # the cap for a whole run: each sample is two renders on the main thread, and one model request
_KINDS = {"face": ("face", "person"), "screen": ("pip", "screen", "pointer"), "auto": None}
_RUN: dict[str, Any] = {}
# Analyses ready for a strip whose Track All split is being built (the build asks for tracking once more).
_READY: dict[str, Any] = {}
_KEYED_PROPS = (("transform", "scale_x"), ("transform", "scale_y"), ("transform", "offset_x"),
                ("transform", "offset_y"), ("crop", "min_x"), ("crop", "max_x"), ("crop", "min_y"), ("crop", "max_y"))


class _Job:
    """One shot to analyse."""

    def __init__(self, name: str, fps: float) -> None:
        self.name = name
        self.fps = fps
        self.moments: list[tuple[int, Any, Any, str]] = []   # (frame, grid, later grid, png of the frame)


def last_result() -> dict[str, Any]:
    """What the last run said (``message``, ``layouts``): for the tool's answer and the tests."""
    return dict(_RUN.get("last", {}))


def take_ready(name: str) -> Any:
    """The analysis waiting for strip *name*'s split (taken once), else ``None``."""
    return _READY.pop(name, None)


# -- pictures -------------------------------------------------------------------------------------

def _grid(path: str, width: int = GRID_W) -> list[list[float]]:
    """A small grey grid (top row first) of the picture at *path*."""
    import numpy as np  # shipped with Blender
    img = bpy.data.images.load(path, check_existing=False)
    try:
        img.colorspace_settings.name = "Non-Color"            # the picture's own values, not linearised
        w, h = int(img.size[0]), int(img.size[1])
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
    finally:
        bpy.data.images.remove(img)
    gray = px.reshape(h, w, 4)[:, :, :3].mean(axis=2)[::-1]
    k = max(1, w // width)
    gh, gw = h // k, w // k
    small = gray[:gh * k, :gw * k].reshape(gh, k, gw, k).mean(axis=(1, 3))
    return small.tolist()


def _plan(strip: Any, scene: Any) -> int:
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    return sf.sample_plan(int(strip.right_handle) - int(strip.left_handle), fps)


def _budget(plans: list[int], cap: int = MAX_RUN_SAMPLES) -> list[int]:
    """How many samples each shot gets so the whole run stays within *cap*: a shot gets its plan, or an even share of
    what is left when that is less (never below the minimum); ``0`` for the shots that no longer fit."""
    out: list[int] = []
    left = cap
    for i, want in enumerate(plans):
        shots = len(plans) - i
        n = min(want, max(sf.MIN_SAMPLES, left // shots))
        if n > left:
            n = 0
        out.append(n)
        left -= n
    return out


def _moments(strip: Any, scene: Any, outdir: str, count: int) -> _Job:
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    left, right = int(strip.left_handle), int(strip.right_handle)
    frames = rm.sample_frames(left, right, count)
    gap = max(1, int(round(fps / 8.0)))
    later = [f + gap if f + gap < right else max(left, f - gap) for f in frames]
    wanted = sorted(set(frames) | set(later))
    rendered = dict(reframe.render_samples(strip, wanted, outdir))
    job = _Job(strip.name, fps)
    for f, g in zip(frames, later):
        if f in rendered and g in rendered:
            job.moments.append((f, _grid(rendered[f]), _grid(rendered[g]), rendered[f]))
    return job


# -- the worker ---------------------------------------------------------------------------------------

def _work(jobs: list[_Job], follow: str, layout: str, ask: Any, state: dict[str, Any]) -> None:
    try:
        out: dict[str, Any] = {}
        state["total"] = sum(len(j.moments) for j in jobs)
        state["done"] = 0
        for job in jobs:
            samples = []
            for frame, grid, later, png in job.moments:
                if state.get("cancel"):
                    state["cancelled"] = True
                    return
                faces: list = []
                people: list = []
                if ask is not None:
                    try:
                        got_faces, got_people = ask(png, job.name, frame)
                        faces, people = [sf.Box(*b) for b in got_faces], [sf.Box(*b) for b in got_people]
                    except Exception as ex:  # pylint: disable=broad-exception-caught  # Unavailable: no answer
                        # the model is gone for this run: go on with what moves, which needs no model
                        ask = None
                        state["faces_lost"] = str(ex) or ex.__class__.__name__
                samples.append(sf.Sample(frame, grid, later, tuple(faces), tuple(people)))
                state["done"] += 1
            out[job.name] = sf.analyse(samples, job.fps, layout, _KINDS.get(follow))
        state["results"] = out
    except Exception as ex:  # pylint: disable=broad-exception-caught
        state["error"] = str(ex)


def cancel() -> bool:
    """Stop the running Follow Subject after the sample it is on; ``False`` when none runs."""
    state = _RUN.get("state")
    if state is None or _RUN.get("thread") is None or not _RUN["thread"].is_alive():
        return False
    state["cancel"] = True
    return True


def progress() -> str:
    """``"3/40"`` while a run is going, else ``""``."""
    state = _RUN.get("state")
    thread = _RUN.get("thread")
    if state is None or thread is None or not thread.is_alive():
        return ""
    return "{:d}/{:d}".format(state.get("done", 0), state.get("total", 0))


def start(scene: Any, strips: Iterable[Any], follow: str = "auto", layout: str = "auto",
          on_done: Any = None) -> str:
    """Find the subject(s) of *strips* and key the vertical framing to them; the status line, or
    ``ReframeError`` / ``reframe_follow.Unavailable`` with the reason when it can't start.

    *follow*: ``auto`` (faces when a vision model is connected, and what moves), ``face`` (faces and
    people only: needs the model) or ``screen`` (what moves: no model, nothing sent).  *layout*:
    ``auto`` (two crops when two subjects are apart), ``single`` or ``split``."""
    follow, layout = str(follow or "auto").lower(), str(layout or "auto").lower()
    if follow not in FOLLOWS:
        raise reframe.ReframeError("follow must be one of {:s}, not {!r}.".format(", ".join(FOLLOWS), follow))
    if layout not in LAYOUTS:
        raise reframe.ReframeError("layout must be one of {:s}, not {!r}.".format(", ".join(LAYOUTS), layout))
    if _RUN.get("thread") is not None and _RUN["thread"].is_alive():
        raise reframe.ReframeError("Follow subject is already running.")
    targets, skipped = _targets(scene, strips)
    if not targets:
        raise reframe.ReframeError("Nothing to follow: " + ("; ".join(skipped) or "select strips that Make Vertical has framed"))
    ask = None
    note = ""
    if follow != "screen":
        try:
            ask = reframe_follow.resolve_box_asker()
        except reframe_follow.Unavailable as ex:
            if follow == "face":
                raise
            note = " No faces: {!s}".format(ex).rstrip(".") + ". Looking at what moves only."
    outdir = tempfile.mkdtemp(prefix="bfacw_subjects_", dir=bpy.app.tempdir or None)
    counts = _budget([_plan(s, scene) for s in targets])
    over = [s.name for s, n in zip(targets, counts) if n == 0]
    if over:
        skipped.append("{:d} shot(s) over the cap of {:d} samples per run, select fewer: {:s}".format(
            len(over), MAX_RUN_SAMPLES, ", ".join(over[:5]) + (", ..." if len(over) > 5 else "")))
    jobs = [_moments(s, scene, outdir, n) for s, n in zip(targets, counts) if n]
    state: dict[str, Any] = {"results": None, "error": "", "scene": scene.name, "skipped": skipped,
                             "on_done": on_done, "layout": layout, "note": note, "faces": ask is not None}
    thread = threading.Thread(target=_work, args=(jobs, follow, layout, ask, state), name="bfacw-subjects",
                              daemon=True)
    _RUN.clear()
    _RUN.update(thread=thread, state=state)
    thread.start()
    bpy.app.timers.register(_poll, first_interval=0.5)
    where = " " + reframe._engine_notice(follow) if ask is not None else ""   # pylint: disable=protected-access
    return "Looking for the subject in {:d} shot(s) ({:d} sampled moments){:s}...{:s}".format(
        len(jobs), sum(len(j.moments) for j in jobs), note, where)


def _targets(scene: Any, strips: Iterable[Any]) -> tuple[list[Any], list[str]]:
    """Strips Follow Subject can work on: a Make Vertical centre crop, or a part of a Track All split (the top one).
    The other splits (half, top or bottom bias) keep their fixed crops and are skipped with the reason."""
    ok, skipped = [], []
    level = list(scene.sequence_editor.strips_all) if scene.sequence_editor else []
    for s in strips:
        tag = ra.tag_of(s)
        if tag is not None and tag["layout"] != "track_all" and rl.layout(tag["layout"]).parts[1:]:
            skipped.append("{:s} (a {:s} split has fixed crops: use Track All)".format(s.name, rl.layout(tag["layout"]).short))
            continue
        if tag is not None and tag["layout"] == "track_all":
            base = s if tag["part"] == 0 else ra.find_partner(level, s)
            if base is not None and base.name not in {o.name for o in ok}:
                ok.append(base)
            elif base is None:
                skipped.append("{:s} (its other part is missing)".format(s.name))
            continue
        more, why = reframe._follow_targets(scene, [s])          # pylint: disable=protected-access
        ok.extend(more)
        skipped.extend(why)
    return ok, skipped


# -- applying ----------------------------------------------------------------------------------------------

def clear_strip_keys(scene: Any, strip: Any) -> int:
    """Remove the keys on *strip*'s transform and crop (a Track All part's); how many curves went."""
    prefixes = tuple(getattr(strip, group).path_from_id() + "." for group in ("transform", "crop"))
    gone = 0
    for bag in reframe._channelbags(scene):                       # pylint: disable=protected-access
        for fc in list(bag.fcurves):
            if fc.data_path.startswith(prefixes):
                bag.fcurves.remove(fc)
                gone += 1
    return gone


def _key_part(scene: Any, strip: Any, index: int, tracks: list, fps: float, sw: int, sh: int,
              fw: int, fh: int) -> int:
    rect = rl.part_rects("track_all", fw, fh)[index]
    keys, zoom = sf.part_framing(tracks, rect[2] - rect[0], rect[3] - rect[1], sw, sh, fps)
    clear_strip_keys(scene, strip)
    if not keys:
        return 0
    for frame, u, v in keys:
        part = rl.part_for(index, rect, sw, sh, fw, fh, rl.Framing(u, v, zoom))
        ra.set_part(strip, part)
        for group, prop in _KEYED_PROPS:
            getattr(strip, group).keyframe_insert(prop, frame=frame)
    for bag in reframe._channelbags(scene):                       # pylint: disable=protected-access
        for fc in bag.fcurves:
            if fc.data_path.startswith((strip.transform.path_from_id() + ".", strip.crop.path_from_id() + ".")):
                for kp in fc.keyframe_points:
                    kp.interpolation = 'LINEAR'
    return len(keys)


def apply_split(scene: Any, base_name: str, analysis: Any) -> str:
    """Key the two parts of the Track All split of *base_name*: the top follows the main subject, the bottom
    the second one; without a second one the bottom keeps its fixed crop.  The status text."""
    from . import reframe_layouts
    level = reframe_layouts._level_strips(scene)                  # pylint: disable=protected-access
    base = {s.name: s for s in level}.get(base_name)
    other = ra.find_partner(level, base) if base is not None else None
    if base is None or other is None:
        return "{:s}: the split's two parts were not found".format(base_name)
    backend = reframe_layouts.BlenderBackend(scene)
    sw, sh = backend.source_size(base)
    fw, fh = backend.canvas_size(scene)
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    top_keys = _key_part(scene, base, 0, list(analysis.primary), fps, sw, sh, fw, fh)
    other = {s.name: s for s in reframe_layouts._level_strips(scene)}[other.name]   # pylint: disable=protected-access
    second = [t for t in analysis.secondary if t is not None]
    bottom_keys = _key_part(scene, other, 1, second, fps, sw, sh, fw, fh) if second else 0
    if not second:
        return "{:s}: one subject found; the top part follows it, the bottom keeps its crop".format(base_name)
    return "{:s}: top follows the {:s}, bottom the {:s} ({:d} + {:d} keys)".format(
        base_name, analysis.primary[0].kind, second[0].kind, top_keys, bottom_keys)


def _single(scene: Any, results: dict[str, Any]) -> dict[str, int]:
    """Key the pan of the shots that stay one crop (already smoothed per shot, never across a cut)."""
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    samples = {name: sf.smooth_segments(list(a.primary), "cx", fps) for name, a in results.items()}
    return reframe.apply_follow(scene, samples, smooth=False)


def apply_results(scene: Any, results: dict[str, Any], layout: str) -> list[str]:
    """Apply each shot's analysis; the lines to tell the artist."""
    from . import reframe_layouts
    lines: list[str] = []
    singles: dict[str, Any] = {}
    for name, analysis in results.items():
        strip = scene.sequence_editor.strips_all.get(name) if scene.sequence_editor else None
        if strip is None:
            continue
        tagged = ra.tag_of(strip)
        if tagged is not None and tagged["layout"] != "track_all" and rl.layout(tagged["layout"]).parts[1:]:
            lines.append("{:s}: a {:s} split has fixed crops, left alone (use Track All)".format(
                name, rl.layout(tagged["layout"]).short))
            continue
        in_split = tagged is not None and tagged["layout"] == "track_all"
        if not analysis.primary:
            lines.append("{:s}: no subject found, it keeps the centre crop".format(name))
            continue
        want_split = in_split or analysis.layout == "split"
        if want_split and not in_split:
            _READY[name] = analysis
            out = reframe_layouts.build(scene, "track_all", [name])
            _READY.pop(name, None)
            if out.get("status") != "ok":
                lines.append("{:s}: could not build the split ({:s})".format(name, out.get("message", "")))
                continue
            lines.append(out.get("tracking") or "{:s}: split".format(name))
        elif want_split:
            lines.append(apply_split(scene, name, analysis))
        else:
            singles[name] = analysis
            if layout == "split" and not any(analysis.secondary):
                lines.append("{:s}: only one subject found, so it stays one crop".format(name))
    if singles:
        keyed = _single(scene, singles)
        for name, n in keyed.items():
            kind = singles[name].primary[0].kind
            lines.append("{:s}: one crop follows the {:s} ({:d} keys)".format(name, kind, n))
    return lines


def consume_ready(scene: Any, names: list[str]) -> str:
    """The Track All build asks for tracking: if the analysis is ready, apply it now (else ``""``)."""
    done = []
    for name in names:
        analysis = take_ready(name)
        if analysis is not None:
            done.append(apply_split(scene, name, analysis))
    return "; ".join(done)


# -- the timer ------------------------------------------------------------------------------------------------

def _poll() -> float | None:
    thread, state = _RUN.get("thread"), _RUN.get("state")
    if thread is None or state is None:
        return None
    if thread.is_alive():
        return 0.5
    _RUN.clear()
    scene = bpy.data.scenes.get(state["scene"])
    msg = state["error"]
    lines: list[str] = []
    if state.get("cancelled"):
        msg = "Follow subject: stopped, nothing keyed."
    elif not msg and scene is None:
        msg = "Follow subject: the scene {!r} was not found (removed or undone), nothing keyed.".format(state["scene"])
    elif not msg and state["results"] is not None:
        try:
            lines = apply_results(scene, state["results"], state["layout"])
            msg = "Follow subject: " + ("; ".join(lines) or "nothing to key") + "."
        except Exception as ex:  # pylint: disable=broad-exception-caught
            msg = "Follow subject failed part-way, what was keyed stays (Ctrl+Z takes it back): {!s}".format(ex)
        try:
            bpy.ops.ed.undo_push(message="Follow Subject")      # also after a failure: one step to take back
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    elif msg:
        msg = "Follow subject failed: " + msg
    if state.get("faces_lost"):
        msg += " The model gave no answer ({:s}): faces were dropped from that point, only what moves was used.".format(
            state["faces_lost"])
    if state["note"]:
        msg += state["note"]
    if state["skipped"]:
        msg += " Skipped: " + "; ".join(state["skipped"]) + "."
    print("[Coworker] " + msg)
    _RUN["last"] = {"message": msg, "lines": lines}
    if state.get("on_done"):
        try:
            state["on_done"](msg, lines)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    reframe._tell(msg)                                           # pylint: disable=protected-access
    return None


def unregister() -> None:
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
