# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for reframe_vertical: Make Vertical (9:16) in the Sequencer (Video 3, #333).

Calls the add-on's ``coworker_api.reframe_vertical`` (the same code as the
Sequencer's Strip > Make Vertical), so the result is the editable Reframe
modifier a user gets, and answers with what was done.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    strips: Any
    mode: str
    in_copy: bool
    resolution: str
    reset: bool


class Result(NamedTuple):
    status: str
    message: str
    scene: str | None = None
    source_scene: str | None = None
    copied: bool = False
    mode: str | None = None
    resolution: Any = None
    strips_changed: Any = None
    strips_kept: Any = None


# @include_begin: _coworker_api_shared.py
# @include_end


_MODES = ("CROP", "BLUR_FILL")


def main(params: Params) -> Any:
    mode = str(params.mode or "CROP").upper().replace(" ", "_")
    if mode in ("CENTRE_CROP", "CENTER_CROP", "CENTRE", "CENTER"):
        mode = "CROP"
    if mode == "BLUR":
        mode = "BLUR_FILL"
    if mode not in _MODES:
        return Result(status="error", message="Unknown mode '{:s}'. Use CROP (centre crop) or BLUR_FILL.".format(
            str(params.mode)))
    strips = params.strips
    if isinstance(strips, str):
        strips = [strips] if strips else []
    strips = [str(s) for s in (strips or [])]
    try:
        done = _coworker_api().reframe_vertical(strips, mode, bool(params.in_copy),
                                                str(params.resolution or "1080x1920"), bool(params.reset))
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return Result(status="error", message=str(ex), mode=mode)
    changed = list(done["added"]) + list(done["reset"])
    kept = list(done["kept"])
    where = ("a vertical copy '{:s}' (the Sequencer shows it now; '{:s}' is unchanged)".format(
        done["scene"], done["source_scene"]) if done["copied"] else "'{:s}'".format(done["scene"]))
    msg = "Made {:s} {:d}x{:d} with {:s}: {:d} strip(s) reframed{:s}. Each strip's framing is its 'Reframe' " \
          "modifier (Offset X / Y, Zoom, Blur Fill), editable in the Sequencer sidebar's Modifiers tab.".format(
              where, done["resolution"][0], done["resolution"][1],
              "Centre Crop" if mode == "CROP" else "Blur Fill", len(changed),
              ", {:d} kept their framing".format(len(kept)) if kept else "")
    return Result(status="ok", message=msg, scene=done["scene"], source_scene=done["source_scene"],
                  copied=bool(done["copied"]), mode=mode, resolution=list(done["resolution"]),
                  strips_changed=changed, strips_kept=kept)
