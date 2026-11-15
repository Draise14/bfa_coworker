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
#   * compositing, the Sequencer pass, motion blur and persistent data off;
#   * written as a PNG image (a movie output format makes a still render fail).
# Adaptive sampling, denoising and the device stay as the user set them.
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

# The thumbnail: the same profile, smaller.
_THUMB_MAX_EDGE = 320
_THUMB_MAX_SAMPLES = 16
_THUMB_SIMPLIFY_SUBDIV = 1

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
) -> tuple[list[tuple[Any, str, Any]], dict[str, Any]]:
    """Put the preview profile on *scene*: ``(saved, caps)``.

    *saved* is the undo list for :func:`_preview_profile_restore`; *caps* says what the render
    runs with (for the tool's answer).  Best-effort: a setting the build or engine lacks is
    skipped.
    """
    saved: list[tuple[Any, str, Any]] = []
    caps: dict[str, Any] = {}
    rd = getattr(scene, "render", None)
    if rd is None:
        return saved, caps

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
    eevee = getattr(scene, "eevee", None)
    if eevee is not None:
        taa = getattr(eevee, "taa_render_samples", None)
        if isinstance(taa, int) and taa > max_samples:
            _preview_set(saved, eevee, "taa_render_samples", max_samples)
    caps["samples_max"] = max_samples
    caps["cycles_time_limit_s"] = float(time_limit)

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
