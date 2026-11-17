# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Inline the preview profile (#524): the one set of caps for every Coworker render that is only
# for looking -- render_viewport_to_path (quality "preview" and "viewport"), render_thumbnail_to_path
# and the chat's Capture Render (``chat_attachments.py`` loads this same file).
#
# What a preview does to the scene while it renders:
#   * the longest edge at most _PREVIEW_MAX_EDGE px (resolution % folded in, never upscaled);
#   * Cycles and EEVEE samples at most _PREVIEW_MAX_SAMPLES, a Cycles time limit;
#   * Simplify on, subdivision at most _PREVIEW_SIMPLIFY_SUBDIV;
#   * Cycles loads image textures at most _PREVIEW_TEXTURE_LIMIT px (``texture_limit_render``,
#     #527: less memory for big textures; a 1024 px image doesn't show the difference);
#   * compositing, the Sequencer pass, motion blur and persistent data off;
#   * written as a PNG image (a movie output format makes a still render fail).
# Adaptive sampling, denoising and the device stay as the user set them.
#
# #527: a Sequencer render (render_vse_preview, Reframe's Follow subject samples) renders each
# scene a scene strip pulls in with that scene's own settings: the same caps go on those scenes
# (``_preview_profile_apply_strip_scenes``), except their size (the Sequencer places the strip's
# picture by its scene's resolution: a smaller scene shows smaller in the frame, probed on
# 5.2.4), their own Sequencer pass and their output format.
#
# Every change is recorded as ``(owner, attr, previous)`` in the order it was made.  Restoring
# walks that list in the same order (an output's media type before its file format, the format
# before its colour settings) and empties it, so a second restore -- the render job's end and its
# checker both call it -- changes nothing.  A render started with INVOKE_DEFAULT reads the
# settings on its own thread after the operator returned: restore only when the job is over.
#
# bpy-free: it only reads and sets attributes, so it is tested with stand-ins.

__all__ = ()

from typing import Any

_PREVIEW_MAX_EDGE = 1024          # px; a vision model gets no more out of a bigger image
_PREVIEW_MAX_SAMPLES = 32         # Cycles samples, EEVEE render samples
_PREVIEW_TIME_LIMIT_S = 30.0      # Cycles stops sampling after this (EEVEE has no time limit)
_PREVIEW_SIMPLIFY_SUBDIV = 2      # Simplify > Max Subdivision (render)
_PREVIEW_TEXTURE_LIMIT = 2048     # Cycles Simplify > Texture Limit (render), px (#527)

# The thumbnail: the same profile, smaller.
_THUMB_MAX_EDGE = 320
_THUMB_MAX_SAMPLES = 16
_THUMB_SIMPLIFY_SUBDIV = 1
_THUMB_TEXTURE_LIMIT = 1024

# Passes a look at the scene does without: (render attribute, its name in the answer).
_PREVIEW_OFF = (
    ("use_compositing", "compositing"),
    ("use_sequencer", "Sequencer"),
    ("use_motion_blur", "motion blur"),
    ("use_persistent_data", "persistent data"),
)

# Jobs a new render must not start next to.
_PREVIEW_BUSY_JOBS = (
    ("RENDER", "A render is already running"),
    ("OBJECT_BAKE", "A bake is running"),
)

_PREVIEW_SET_ERRORS = (AttributeError, ReferenceError, TypeError, ValueError, RuntimeError)


def _preview_set(saved: list[tuple[Any, str, Any]], owner: Any, attr: str, value: Any) -> bool:
    """Set ``owner.attr`` to *value*, recording the old value in *saved*; ``False`` when unchanged."""
    try:
        previous = getattr(owner, attr)
    except _PREVIEW_SET_ERRORS:
        return False
    if previous == value:
        return False
    try:
        setattr(owner, attr, value)
    except _PREVIEW_SET_ERRORS:
        return False
    saved.append((owner, attr, previous))
    return True


def _preview_output_size(rd: Any) -> tuple[int, int]:
    """The pixel size *rd* renders at (resolution x / y times the percentage)."""
    try:
        pct = float(getattr(rd, "resolution_percentage", 100) or 100)
        return (max(1, int(int(getattr(rd, "resolution_x", 0) or 0) * pct / 100.0)),
                max(1, int(int(getattr(rd, "resolution_y", 0) or 0) * pct / 100.0)))
    except (TypeError, ValueError):
        return (0, 0)


def _preview_profile_apply(
        scene: Any,
        max_edge: int = _PREVIEW_MAX_EDGE,
        max_samples: int = _PREVIEW_MAX_SAMPLES,
        simplify_subdiv: int = _PREVIEW_SIMPLIFY_SUBDIV,
        time_limit: float = _PREVIEW_TIME_LIMIT_S,
        strip_scene: bool = False,
        texture_limit: int = _PREVIEW_TEXTURE_LIMIT,
) -> tuple[list[tuple[Any, str, Any]], dict[str, Any]]:
    """Put the preview profile on *scene*: ``(saved, caps)``.

    *saved* is the undo list for :func:`_preview_profile_restore`; *caps* says what the render
    runs with (for the tool's answer).  Best-effort: a setting the build or engine lacks is
    skipped.  *strip_scene*: *scene* is rendered by a Sequencer scene strip (#527): its size,
    its own Sequencer pass and its output format stay.
    """
    saved: list[tuple[Any, str, Any]] = []
    caps: dict[str, Any] = {}
    rd = getattr(scene, "render", None)
    if rd is None:
        return saved, caps

    if not strip_scene:
        width, height = _preview_output_size(rd)
        longest = max(width, height)
        if longest > max_edge:
            scale = max_edge / float(longest)
            width, height = max(1, round(width * scale)), max(1, round(height * scale))
            _preview_set(saved, rd, "resolution_x", width)
            _preview_set(saved, rd, "resolution_y", height)
            _preview_set(saved, rd, "resolution_percentage", 100)
        caps["resolution"] = [width, height]

    _preview_set(saved, rd, "use_simplify", True)
    subdiv = getattr(rd, "simplify_subdivision_render", None)
    if isinstance(subdiv, int):
        if subdiv > simplify_subdiv:
            _preview_set(saved, rd, "simplify_subdivision_render", simplify_subdiv)
        caps["simplify_subdivision_max"] = min(subdiv, simplify_subdiv)

    off = []
    for attr, name in _PREVIEW_OFF:
        if strip_scene and attr == "use_sequencer":
            continue
        if hasattr(rd, attr):
            _preview_set(saved, rd, attr, False)
            off.append(name)
    caps["off"] = off

    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        samples = getattr(cycles, "samples", None)
        if isinstance(samples, int) and samples > max_samples:
            _preview_set(saved, cycles, "samples", max_samples)
        limit = getattr(cycles, "time_limit", None)
        if isinstance(limit, (int, float)) and (not limit or limit > time_limit):
            _preview_set(saved, cycles, "time_limit", float(time_limit))
        # #527: Cycles loads image textures at most this big (an enum: "OFF", "128" ... "8192").
        tex = getattr(cycles, "texture_limit_render", None)
        if isinstance(tex, str) and texture_limit:
            if not tex.isdigit() or int(tex) > texture_limit:
                _preview_set(saved, cycles, "texture_limit_render", str(int(texture_limit)))
            caps["texture_limit_px"] = min(int(tex), texture_limit) if tex.isdigit() else int(texture_limit)
    eevee = getattr(scene, "eevee", None)
    if eevee is not None:
        taa = getattr(eevee, "taa_render_samples", None)
        if isinstance(taa, int) and taa > max_samples:
            _preview_set(saved, eevee, "taa_render_samples", max_samples)
    caps["samples_max"] = max_samples
    caps["cycles_time_limit_s"] = float(time_limit)
    if strip_scene:
        return saved, caps

    ims = getattr(rd, "image_settings", None)
    if ims is not None and (getattr(ims, "file_format", "PNG") != "PNG"
                            or getattr(ims, "media_type", "IMAGE") != "IMAGE"):
        # A format change also adjusts the colour mode and depth on its own: keep all four,
        # in the order they go back (media type, format, then the colour settings).
        keep = [(ims, attr, getattr(ims, attr)) for attr in ("media_type", "file_format", "color_mode", "color_depth")
                if hasattr(ims, attr)]
        try:
            if hasattr(ims, "media_type"):
                ims.media_type = "IMAGE"
            ims.file_format = "PNG"
        except _PREVIEW_SET_ERRORS:
            pass
        saved.extend(keep)
    caps["format"] = "PNG"
    return saved, caps


def _preview_strip_scenes(scene: Any) -> list[Any]:
    """Every scene a scene strip in *scene*'s Sequencer pulls in (inside metas too, and the
    scenes their own scene strips pull in), each once; never *scene* itself."""
    found: list[Any] = []
    todo = [scene]
    while todo:
        ed = getattr(todo.pop(0), "sequence_editor", None)
        try:
            strips = list(getattr(ed, "strips_all", None) or ()) if ed is not None else []
        except (TypeError, ReferenceError, RuntimeError):
            strips = []
        for strip in strips:
            if getattr(strip, "type", "") != "SCENE":
                continue
            other = getattr(strip, "scene", None)
            if other is None or other is scene or any(other is s for s in found):
                continue
            found.append(other)
            todo.append(other)
    return found


def _preview_profile_apply_strip_scenes(
        scene: Any,
        max_samples: int = _PREVIEW_MAX_SAMPLES,
        simplify_subdiv: int = _PREVIEW_SIMPLIFY_SUBDIV,
        time_limit: float = _PREVIEW_TIME_LIMIT_S,
) -> tuple[list[tuple[Any, str, Any]], list[str]]:
    """The preview profile on every scene a scene strip of *scene* pulls in (#527):
    ``(saved, scene names)``.

    A Sequencer render renders those scenes with their own settings (a 4096-sample Cycles
    shot inside a quick look).  Their picture goes into the Sequencer, not into a file, so
    their output format, their own Sequencer pass and their size stay (the Sequencer sizes
    the strip); samples, the time limit, Simplify and the passes are capped as for any
    preview.  One undo list for :func:`_preview_profile_restore`.
    """
    saved: list[tuple[Any, str, Any]] = []
    names = []
    for other in _preview_strip_scenes(scene):
        part, _caps = _preview_profile_apply(other, max_samples=max_samples, simplify_subdiv=simplify_subdiv,
                                             time_limit=time_limit, strip_scene=True)
        saved.extend(part)
        names.append(str(getattr(other, "name", "")))
    return saved, names


def _preview_profile_restore(saved: list[tuple[Any, str, Any]]) -> None:
    """Put back what :func:`_preview_profile_apply` (or anything recorded in *saved*) changed.

    In recorded order, once: *saved* is emptied.  A value that can't be put back (the data is
    gone) doesn't keep the others from going back.
    """
    while saved:
        owner, attr, previous = saved.pop(0)
        try:
            setattr(owner, attr, previous)
        except _PREVIEW_SET_ERRORS:
            pass


def _preview_caps_text(caps: dict[str, Any]) -> str:
    """*caps* in a sentence, for the tool's answer."""
    if not caps:
        return ""
    parts = []
    if caps.get("resolution"):
        parts.append("{:d}x{:d} px".format(*caps["resolution"]))
    if caps.get("samples_max"):
        parts.append("at most {:d} samples (Cycles stops after {:.0f} s)".format(
            caps["samples_max"], caps.get("cycles_time_limit_s", 0.0)))
    if "simplify_subdivision_max" in caps:
        parts.append("Simplify on (subdivision at most {:d})".format(caps["simplify_subdivision_max"]))
    if caps.get("texture_limit_px"):
        parts.append("Cycles textures at most {:d} px".format(caps["texture_limit_px"]))
    if caps.get("off"):
        parts.append(", ".join(caps["off"]) + " off")
    if caps.get("format"):
        parts.append("saved as a {:s}".format(caps["format"]))
    return "; ".join(parts)


def _preview_busy_message(is_job_running: Any) -> str:
    """Why a render can't start now ("" when it can): a render or bake job is running."""
    for kind, text in _PREVIEW_BUSY_JOBS:
        try:
            running = bool(is_job_running(kind))
        except (TypeError, ValueError, RuntimeError):
            running = False
        if running:
            return ("{:s}. Wait for it (get_job_status, or the job bar at the bottom), then render "
                    "again.".format(text))
    return ""


def _preview_no_camera(scene: Any) -> bool:
    """True when *scene* has nothing to render from: no active camera and no camera object
    (Blender's own render takes the first camera it finds when none is active)."""
    if getattr(scene, "camera", None) is not None:
        return False
    try:
        return not any(getattr(ob, "type", "") == "CAMERA" for ob in getattr(scene, "objects", ()))
    except (TypeError, ReferenceError):
        return True


_PREVIEW_NO_CAMERA_MESSAGE = (
    "The scene has no camera, so there is nothing to render from. Add a camera (and make it the "
    "scene's camera), or look at the 3D Viewport with get_screenshot_of_area_as_image instead.")
