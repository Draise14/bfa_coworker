# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
A trace of the GPU chat's last draws, to pin down a rare glitch (#387).

The chat sometimes draws one wrong frame when you switch sidebar tabs or
while Coworker works, and it has not been caught yet.  With the trace on
(Status & Diagnostics > Chat Draw Trace) every draw of a chat -- each
sidebar's compact chat and each Coworker editor -- adds one :class:`Frame`
to a ring buffer of the last :data:`SIZE` draws: where it drew (region,
size, the active sidebar tab, how the panel's room was found and the
rectangle used), what it drew (scroll, turns, whether a turn ran, the
live text lengths before and after), how long the marker scan, layout and
GPU draw took, and any error.  :meth:`DrawTrace.text` gives it as plain
text for the issue ("Copy Chat Draw Trace").

Frames that look wrong get flags (:data:`FLAGS`), so the line to look at
stands out.  The trace never raises: a fault in it is dropped, never the
chat's frame.  Off, the host doesn't call it.  Free of ``bpy``.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

__all__ = (
    "FLAGS",
    "Frame",
    "DrawTrace",
    "TRACE",
)

SIZE = 300            # draws kept
SLOW_MS = 50.0        # a draw slower than this is flagged
MARKER_SOURCES = ("both", "top", "bottom")    # the room was found from a marker seen this frame

# What a flag means (the copied text lists the ones that occur).
FLAGS: dict[str, str] = {
    "outside": "the chat's rectangle reaches outside its region",
    "other_tab": "drawn while the sidebar's active tab is not Coworker's (fine only if the panel is pinned)",
    "stale": "drawn from the room remembered in view space: no marker on screen this frame",
    "resized": "the region's size changed since this region's previous draw",
    "width_jump": "the rectangle's width or left edge changed while the region's size did not",
    "blip": "not drawn, though the previous draw of this region (same tab, same size) was",
    "live_changed": "a worker changed the running turn's text or history during this draw (drawn from one snapshot)",
    "slow": "scan + layout + draw took longer than {:.0f} ms".format(SLOW_MS),
    "error": "the draw raised (the chat is missing from this frame)",
}


@dataclass
class Frame:
    """One draw of one chat."""

    where: str = "sidebar"               # "sidebar" or "editor"
    area: int = 0                        # area / region pointers (ids)
    region: int = 0
    region_w: int = 0
    region_h: int = 0
    tab: str = ""                        # the sidebar's active tab ("" in the editor)
    # How the room was found: both / top / bottom (markers seen), view (#198: remembered),
    # none (not found: not drawn), short (too little on screen: not drawn), editor.
    source: str = ""
    rect: tuple[float, float, float, float] | None = None    # drawn (x, y, w, h); None = not drawn
    scroll: float = 0.0
    content_h: float = 0.0
    turns: int = 0
    running: bool = False
    # (len(streaming_text), len(reasoning_text), len(conversation_history)) before / after the draw.
    live_before: tuple[int, int, int] = (0, 0, 0)
    live_after: tuple[int, int, int] = (0, 0, 0)
    find_ms: float = 0.0
    layout_ms: float = 0.0
    draw_ms: float = 0.0
    error: str = ""
    t: float = 0.0                       # the trace's clock when it was added
    marker_age: int = 0                  # draws of this region since a marker was seen (-1: never)
    flags: tuple[str, ...] = ()


@dataclass
class DrawTrace:
    """The ring buffer: :meth:`add` a frame per draw, :meth:`text` to read it."""

    size: int = SIZE
    clock: Any = time.monotonic
    frames: deque = field(default_factory=deque)
    dropped: int = 0                     # frames that fell off the front
    _last: dict[int, Frame] = field(default_factory=dict)    # region -> its previous frame
    _seen: dict[int, int] = field(default_factory=dict)      # region -> draws since a marker
    _count: int = 0

    def add(self, frame: Frame) -> Frame | None:
        """Stamp, flag and keep *frame*; never raises (returns None if it couldn't)."""
        try:
            frame.t = self.clock()
            if frame.source in MARKER_SOURCES:
                self._seen[frame.region] = 0
            elif frame.region in self._seen:
                self._seen[frame.region] += 1
            frame.marker_age = self._seen.get(frame.region, -1)
            frame.flags = flags_for(frame, self._last.get(frame.region))
            self._last[frame.region] = frame
            if len(self._last) > 64:     # regions come and go (editors closed)
                self._last = {frame.region: frame}
                self._seen = {frame.region: self._seen.get(frame.region, -1)}
            self.frames.append(frame)
            self._count += 1
            while len(self.frames) > max(1, self.size):
                self.frames.popleft()
                self.dropped += 1
            return frame
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def clear(self) -> None:
        self.frames.clear()
        self._last.clear()
        self._seen.clear()
        self.dropped = self._count = 0

    def text(self, header: str = "") -> str:
        """The trace as plain text, oldest first: a summary, the flags seen, one line per draw."""
        try:
            return _text(self, header)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            return "Chat draw trace: could not be written ({:s})".format(_short(ex))


TRACE = DrawTrace()


def _inside(rect: tuple[float, float, float, float], w: int, h: int, slack: float = 1.0) -> bool:
    x, y, rw, rh = rect
    return x >= -slack and y >= -slack and x + rw <= w + slack and y + rh <= h + slack


def flags_for(frame: Frame, prev: Frame | None) -> tuple[str, ...]:
    """The anomaly flags of *frame*, given the same region's previous frame."""
    out = []
    drawn = frame.rect is not None
    if drawn and not _inside(frame.rect, frame.region_w, frame.region_h):
        out.append("outside")
    if drawn and frame.where == "sidebar" and frame.tab and frame.tab != "Coworker":
        out.append("other_tab")
    if drawn and frame.source == "view":
        out.append("stale")
    if prev is not None:
        same_size = (prev.region_w, prev.region_h) == (frame.region_w, frame.region_h)
        if not same_size:
            out.append("resized")
        if drawn and prev.rect is not None and same_size and (
                abs(prev.rect[2] - frame.rect[2]) > 0.5 or abs(prev.rect[0] - frame.rect[0]) > 0.5):
            out.append("width_jump")
        if not drawn and prev.rect is not None and same_size and prev.tab == frame.tab \
                and not frame.error:
            out.append("blip")
    if frame.live_before != frame.live_after:
        out.append("live_changed")
    if frame.find_ms + frame.layout_ms + frame.draw_ms > SLOW_MS:
        out.append("slow")
    if frame.error:
        out.append("error")
    return tuple(out)


def _short(ex: BaseException, limit: int = 120) -> str:
    text = "{:s}: {:s}".format(type(ex).__name__, str(ex)).replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def short_error(ex: BaseException) -> str:
    """An exception on one short line, for :attr:`Frame.error`."""
    try:
        return _short(ex)
    except Exception:  # pylint: disable=broad-exception-caught
        return "error"


def _id(pointer: int) -> str:
    return "{:04x}".format(pointer & 0xFFFF) if pointer else "-"


def _rect(rect: tuple[float, float, float, float] | None) -> str:
    if rect is None:
        return "-"
    return "{:.0f},{:.0f} {:.0f}x{:.0f}".format(*rect)


def frame_line(frame: Frame, t0: float) -> str:
    """One frame on one line."""
    live = "{:d}/{:d}/{:d}".format(*frame.live_before)
    if frame.live_after != frame.live_before:
        live += "->{:d}/{:d}/{:d}".format(*frame.live_after)
    parts = [
        "{:8.3f}".format(frame.t - t0),
        frame.where[:4],
        "a{:s} r{:s}".format(_id(frame.area), _id(frame.region)),
        "{:d}x{:d}".format(frame.region_w, frame.region_h),
        "tab={:s}".format(frame.tab or "-"),
        frame.source or "-",
        "age={:d}".format(frame.marker_age),
        "rect={:s}".format(_rect(frame.rect)),
        "scroll={:.0f}/{:.0f}".format(frame.scroll, frame.content_h),
        "turns={:d}{:s}".format(frame.turns, " run" if frame.running else ""),
        "live={:s}".format(live),
        "ms={:.1f}+{:.1f}+{:.1f}".format(frame.find_ms, frame.layout_ms, frame.draw_ms),
    ]
    if frame.flags:
        parts.append("[{:s}]".format(" ".join(frame.flags)))
    if frame.error:
        parts.append("error: " + frame.error)
    return " ".join(parts)


def _text(trace: DrawTrace, header: str) -> str:
    frames = list(trace.frames)
    lines = ["Coworker chat draw trace (#387)"]
    if header:
        lines.append(header)
    if not frames:
        lines.append("No draws recorded: turn Chat Draw Trace on, use the chat until it glitches, then copy.")
        return "\n".join(lines) + "\n"
    span = frames[-1].t - frames[0].t
    lines.append("{:d} draws over {:.1f} s ({:d} older ones dropped)".format(len(frames), span, trace.dropped))
    counts: dict[str, int] = {}
    for frame in frames:
        for flag in frame.flags:
            counts[flag] = counts.get(flag, 0) + 1
    if counts:
        lines.append("Flags:")
        lines += ["  {:s} x{:d}: {:s}".format(name, counts[name], FLAGS.get(name, ""))
                  for name in FLAGS if name in counts]
    else:
        lines.append("Flags: none")
    lines.append("Columns: seconds, sidebar/editor, area and region id, region size, active tab, how the room "
                 "was found, draws since a marker, drawn rect (x,y wxh), scroll/content height, turns, "
                 "live text/thinking/history lengths (before->after), ms scan+layout+draw, flags")
    t0 = frames[0].t
    lines += [frame_line(frame, t0) for frame in frames]
    return "\n".join(lines) + "\n"
