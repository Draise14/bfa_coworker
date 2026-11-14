# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for one Video Sequencer strip in detail (Tools 1, #304).
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    strip_name: str
    scene_name: str = ""


class Result(NamedTuple):
    status: str
    scene: str
    strip: dict[str, Any] | None
    message: str


# @include_begin: _template_vse_shared.py
from blmcp.tools._template_vse_shared import (  # noqa: E402  (the real helpers, inlined in Blender)
    _vse_find_strip, _vse_get, _vse_not_found_message, _vse_resolve_scene, _vse_row, _vse_rows, _vse_top_strips,
)
# @include_end


def _plain(value: Any) -> Any:
    """A property value as JSON: numbers, strings, lists of numbers; data-blocks by name."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return round(value, 4)
    name = getattr(value, "name", None)
    if isinstance(name, str) and hasattr(value, "bl_rna"):
        return name
    try:
        return [round(float(v), 4) for v in value]
    except (TypeError, ValueError):
        return str(value)


def _props(owner: Any, names: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in names:
        if owner is not None and hasattr(owner, name):
            try:
                out[name] = _plain(getattr(owner, name))
            except (AttributeError, RuntimeError):
                pass
    return out


def _source(strip: Any) -> dict[str, Any]:
    """Where the strip's picture or sound comes from."""
    import os
    kind = str(strip.type)
    out: dict[str, Any] = {}
    if kind == "MOVIE":
        out["filepath"] = getattr(strip, "filepath", "")
        out.update(_props(strip, ("fps", "stream_index")))
    elif kind == "IMAGE":
        elements = list(getattr(strip, "elements", []) or [])
        directory = getattr(strip, "directory", "")
        if elements:
            out["filepath"] = os.path.join(directory, elements[0].filename)
        out["directory"] = directory
        out["images"] = len(elements)
    elif kind == "SOUND":
        sound = getattr(strip, "sound", None)
        out["filepath"] = getattr(sound, "filepath", "") if sound else ""
        out["sound"] = sound.name if sound else ""
    elif kind == "SCENE":
        out.update(_props(strip, ("scene", "scene_camera", "scene_input")))
    elif kind == "MOVIECLIP":
        out.update(_props(strip, ("clip",)))
    elif kind == "MASK":
        out.update(_props(strip, ("mask",)))
    return out


_TEXT_PROPS = ("text", "font", "font_size", "color", "location", "alignment_x", "anchor_x", "anchor_y",
               "wrap_width", "use_bold", "use_italic", "use_shadow", "use_outline", "use_box", "box_color")


def _detail(strip: Any) -> dict[str, Any]:
    kind = str(strip.type)
    info = _vse_row(strip)
    info["content"] = {
        "content_start": _plain(_vse_get(strip, "content_start")),
        "content_duration": _plain(_vse_get(strip, "content_duration")),
        "start_offset": _plain(_vse_get(strip, "start_offset")),
        "end_offset": _plain(_vse_get(strip, "end_offset")),
    }
    # Opacity and blend mode.
    info.update(_props(strip, ("blend_type", "blend_alpha", "color_tag", "color_multiply", "color_saturation",
                               "use_flip_x", "use_flip_y", "use_reverse_frames", "strobe")))
    if "blend_alpha" in info:
        info["opacity"] = info.pop("blend_alpha")
    transform = getattr(strip, "transform", None)
    if transform is not None:
        info["transform"] = _props(transform, ("offset_x", "offset_y", "scale_x", "scale_y", "rotation",
                                               "origin", "filter"))
    crop = getattr(strip, "crop", None)
    if crop is not None:
        info["crop"] = _props(crop, ("min_x", "max_x", "min_y", "max_y"))
    source = _source(strip)
    if source:
        info["source"] = source
    if kind == "TEXT":
        info["text"] = _props(strip, _TEXT_PROPS)
    elif kind == "COLOR":
        info.update(_props(strip, ("color",)))
    if kind in ("SOUND", "SCENE", "MOVIE"):
        sound = _props(strip, ("volume", "pan", "sound_offset", "pitch_correction"))
        if sound:
            info["sound"] = sound
    # Speed: retiming keys (5.x) and the Speed effect.
    keys = getattr(strip, "retiming_keys", None)
    speed: dict[str, Any] = {}
    if keys is not None:
        try:
            speed["retiming_keys"] = len(keys)
        except TypeError:
            pass
    speed.update(_props(strip, ("speed_control", "speed_factor", "speed_frame_number", "speed_length")))
    if speed:
        info["speed"] = speed
    # Effect inputs.
    inputs = [getattr(strip, k, None) for k in ("input_1", "input_2")]
    if any(inputs):
        info["inputs"] = [s.name for s in inputs if s is not None]
    modifiers = []
    for mod in getattr(strip, "modifiers", []) or []:
        entry: dict[str, Any] = {"name": mod.name, "type": str(mod.type)}
        entry.update(_props(mod, ("mute", "enable")))
        balance = getattr(mod, "color_balance", None)
        if balance is not None:
            entry["color_balance"] = _props(balance, ("correction_method", "lift", "gamma", "gain",
                                                      "slope", "offset", "power"))
        modifiers.append(entry)
    info["modifiers"] = modifiers
    if getattr(strip, "use_proxy", False):
        proxy = getattr(strip, "proxy", None)
        info["proxy"] = _props(proxy, ("build_25", "build_50", "build_75", "build_100", "quality",
                                       "use_overwrite", "directory"))
    elif hasattr(strip, "use_proxy"):
        info["proxy"] = None
    if kind == "META":
        children = _vse_top_strips(strip)
        info["meta_strips"] = [c.name for c in children][:50]
        info["meta_strip_count"] = len(children)
    return info


def main(params: Params) -> Result:
    scene, error = _vse_resolve_scene(params.scene_name)
    if scene is None:
        return Result(status="error", scene="", strip=None, message=error)
    name = (params.strip_name or "").strip()
    if not name:
        return Result(status="error", scene=scene.name, strip=None, message=(
            "Give strip_name (get_vse_strips_summary lists the strips)."))
    strip = _vse_find_strip(scene, name)
    if strip is None:
        return Result(status="error", scene=scene.name, strip=None,
                      message=_vse_not_found_message(name, _vse_rows(scene), scene.name))
    ed = scene.sequence_editor
    detail = _detail(strip)
    detail["active"] = bool(ed.active_strip is not None and ed.active_strip.name == strip.name)
    return Result(status="ok", scene=scene.name, strip=detail, message="Strip '{:s}' ({:s}) on channel {:d}.".format(
        strip.name, str(strip.type), int(strip.channel)))
