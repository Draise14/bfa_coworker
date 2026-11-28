# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Subject finding for Make Vertical's Follow Subject (#592).  Free of ``bpy`` and of any model.

What a viewer wants to see in a 16:9 shot is found in two ways, cheapest first:

* **Screen activity and the pointer** need no model.  Two small grey pictures a moment apart are
  subtracted; the cells that changed are joined into regions (:func:`find_activity`).  A big region is
  *screen* (what changes on a screen recording, or a facecam box, which moves while the rest is
  still), a small compact one is the *pointer*.
* **Faces and people** come from the vision model Follow Subject already asks (``reframe_follow``):
  boxes, in the same coordinates as here.  Where a face sits inside a changing region (a facecam), the
  face replaces the region, so the crop is on the face and not on the whole camera box.

The finds of every sampled frame are joined into **tracks** (who is where, and when), a cut starts new
ones, a lone missing sample is bridged, positions are smoothed (``reframe_math.smooth_track``).  Then
the **layout** is chosen: one crop that follows the subject, or two crops, top and bottom, when two
subjects are apart (:func:`choose_layout`); a layout has to hold for a minimum time before it counts.

Coordinates: fractions of the picture, origin at the top-left, y down (the way an image is read);
a framing's ``v`` is from the bottom, so ``v = 1 - cy``.
"""

from __future__ import annotations

__all__ = (
    "APART_DIST",
    "Analysis",
    "Box",
    "Sample",
    "Subject",
    "Track",
    "analyse",
    "assign_slots",
    "choose_layout",
    "find_activity",
    "hold_runs",
    "is_cut",
    "merge_faces",
    "part_framing",
    "smooth_segments",
    "sample_plan",
    "track_samples",
)

import statistics
from typing import NamedTuple, Sequence

from . import reframe_math as rm

Grid = Sequence[Sequence[float]]            # rows of grey values 0..1 (top row first)

# --- numbers (measured on generated clips, see tests/test_subject_find_592.py) ---
CHANGE = 0.08                # a cell changed when its grey differs by more than this
MIN_ACTIVE = 0.003           # a region is real above this share of the cells (noise below)
MIN_CELLS = 3                # and at least this many cells (a pointer is only a few)
MERGE_GAP = 0.08             # regions closer than this (fraction of the picture) are one
POINTER_MAX = 0.12           # a pointer's box is at most this wide and tall
CUT_CELLS = 0.6              # this share of cells changed between two samples: a cut
PIP_AREA = 0.2               # a corner box up to this share of the picture is a camera overlay, not the screen
PIP_CORNER = 0.3             # ... when its centre is this close to the picture's sides, both ways
APART_DIST = 0.25            # two subjects are apart when their centres are this far (fraction of the picture)
MIN_HOLD_S = 2.0             # a layout must hold this long (seconds) before it counts
MIN_COVER = 0.25             # a track is kept when it was found in this share of its segment's samples
MAX_JUMP = 0.3               # a subject moved more than this between two samples is another one
PER_MINUTE = 20              # samples per minute of a shot
MAX_SAMPLES = 40             # the cap for a long shot
MIN_SAMPLES = 4
ZOOM_MAX = 3.0
MARGIN = {"face": 2.6, "person": 1.15, "pip": 1.1, "screen": 1.1, "pointer": 1.0}
MIN_SIZE = {"face": 0.0, "person": 0.0, "pip": 0.0, "screen": 0.0, "pointer": 0.3}
RANK = {"face": 0, "person": 1, "pip": 2, "screen": 3, "pointer": 4}


class Box(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def grown(self, factor: float) -> "Box":
        dx, dy = self.w * (factor - 1.0) / 2.0, self.h * (factor - 1.0) / 2.0
        return Box(self.x0 - dx, self.y0 - dy, self.x1 + dx, self.y1 + dy)


class Subject(NamedTuple):
    kind: str                # face / person / pip / screen / pointer
    box: Box
    weight: float = 0.0      # the changed cells (activity), for ordering


class Sample(NamedTuple):
    """One sampled moment: the picture, the same a moment later, and the faces / people a model found."""
    frame: int
    grid: Grid
    later: Grid | None = None
    faces: tuple[Box, ...] = ()
    people: tuple[Box, ...] = ()


class Track(NamedTuple):
    kind: str
    points: tuple[tuple[int, Box | None], ...]      # (frame, box or None where it was not found)
    coverage: float


class Analysis(NamedTuple):
    frames: tuple[int, ...]
    cuts: tuple[int, ...]                   # index i: a cut between sample i-1 and i
    primary: tuple[Track, ...]              # per segment: the main subject's track
    secondary: tuple[Track | None, ...]     # per segment: a second subject apart from it, or None
    runs: tuple[tuple[str, int, int], ...]  # (layout, first sample, last sample) after the minimum hold
    layout: str                             # single / split / centre (nothing found)


# -- sampling ------------------------------------------------------------------------------

def sample_plan(length_frames: int, fps: float, per_minute: int = PER_MINUTE, cap: int = MAX_SAMPLES) -> int:
    """How many frames to sample in a shot of *length_frames*: *per_minute*, at least a few, at most *cap*."""
    seconds = max(0.0, length_frames / (fps if fps > 0 else 24.0))
    return max(MIN_SAMPLES, min(cap, int(round(seconds * per_minute / 60.0))))


# -- activity and the pointer ----------------------------------------------------------------

def _mask(a: Grid, b: Grid) -> list[list[bool]]:
    return [[abs(x - y) > CHANGE for x, y in zip(ra, rb)] for ra, rb in zip(a, b)]


def _components(mask: list[list[bool]]) -> list[dict]:
    """8-connected regions of changed cells: ``{"n", "x0", "y0", "x1", "y1"}`` in cells (x1, y1 inclusive)."""
    h = len(mask)
    w = len(mask[0]) if h else 0
    seen = [[False] * w for _ in range(h)]
    out = []
    for y in range(h):
        for x in range(w):
            if not mask[y][x] or seen[y][x]:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            comp = {"n": 0, "x0": x, "y0": y, "x1": x, "y1": y}
            while stack:
                cx, cy = stack.pop()
                comp["n"] += 1
                comp["x0"], comp["x1"] = min(comp["x0"], cx), max(comp["x1"], cx)
                comp["y0"], comp["y1"] = min(comp["y0"], cy), max(comp["y1"], cy)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and mask[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
            out.append(comp)
    return out


def _gap(a: Box, b: Box) -> float:
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return max(dx, dy)


def find_activity(a: Grid, b: Grid, limit: int = 3) -> list[Subject]:
    """The regions that changed between two grey grids: up to *limit*, the most changed first.

    A region that is small and compact is the *pointer*, the others *screen* (what changes on a screen,
    or a facecam box)."""
    if not a or not b:
        return []
    h, w = len(a), len(a[0])
    comps = _components(_mask(a, b))
    clusters: list[dict] = []
    for c in sorted(comps, key=lambda c: -c["n"]):
        box = Box(c["x0"] / w, c["y0"] / h, (c["x1"] + 1) / w, (c["y1"] + 1) / h)
        for cl in clusters:
            if _gap(cl["box"], box) <= MERGE_GAP:
                old = cl["box"]
                cl["box"] = Box(min(old.x0, box.x0), min(old.y0, box.y0), max(old.x1, box.x1), max(old.y1, box.y1))
                cl["n"] += c["n"]
                break
        else:
            clusters.append({"box": box, "n": c["n"]})
    # Merging can bring two clusters together: join those too.
    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if _gap(clusters[i]["box"], clusters[j]["box"]) <= MERGE_GAP:
                    p, q = clusters[i]["box"], clusters[j]["box"]
                    clusters[i] = {"box": Box(min(p.x0, q.x0), min(p.y0, q.y0), max(p.x1, q.x1), max(p.y1, q.y1)),
                                   "n": clusters[i]["n"] + clusters[j]["n"]}
                    del clusters[j]
                    merged = True
                    break
            if merged:
                break
    cells = float(w * h)
    found = []
    for cl in sorted(clusters, key=lambda c: -c["n"]):
        box = cl["box"]
        small = box.w <= POINTER_MAX and box.h <= POINTER_MAX
        if cl["n"] < MIN_CELLS or (cl["n"] / cells < MIN_ACTIVE and not small):
            continue
        kind = "pointer" if small else "screen"
        found.append(Subject(kind, box, cl["n"] / cells))
    return found[:limit]


def is_cut(a: Grid, b: Grid) -> bool:
    """True when most of the picture changed between two samples (a cut, or a whole new screen)."""
    if not a or not b:
        return False
    cells = changed = 0
    for ra, rb in zip(a, b):
        for x, y in zip(ra, rb):
            cells += 1
            changed += abs(x - y) > 2 * CHANGE
    return cells > 0 and changed / cells >= CUT_CELLS


def merge_faces(activity: list[Subject], faces: Sequence[Box], people: Sequence[Box] = ()) -> list[Subject]:
    """Faces and people found by a model, plus the activity regions that are not just the same thing: a
    region around a face (a facecam box) gives way to the face; a pointer inside a person stays."""
    out = [Subject("face", f, 1.0) for f in faces] + [Subject("person", p, 0.9) for p in people]
    for s in activity:
        around = [f for f in faces if s.kind == "screen" and s.box.contains(f.cx, f.cy) and s.box.w * s.box.h <= 0.35]
        inside = [f for f in faces if s.kind == "screen" and f.grown(2.5).contains(s.box.cx, s.box.cy)
                  and s.box.w * s.box.h <= f.w * f.h * 6.0]
        if around or inside:
            continue
        out.append(s)
    return out


# -- tracks -----------------------------------------------------------------------------------

def _centre_dist(a: Box, b: Box) -> float:
    return ((a.cx - b.cx) ** 2 + (a.cy - b.cy) ** 2) ** 0.5


def _segments(count: int, cuts: Sequence[int]) -> list[tuple[int, int]]:
    edges = [0] + sorted(c for c in cuts if 0 < c < count) + [count]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1) if edges[i + 1] > edges[i]]


def _tracks(frames: Sequence[int], per_sample: Sequence[Sequence[Subject]], i0: int, i1: int) -> list[Track]:
    """Join the subjects of samples ``i0 .. i1-1`` (one segment) into tracks of one kind each, greedily by distance."""
    live: list[dict] = []
    for i in range(i0, i1):
        used: set[int] = set()
        for subject in sorted(per_sample[i], key=lambda s: (RANK.get(s.kind, 9), -s.weight)):
            best, best_d = None, MAX_JUMP
            for idx, tr in enumerate(live):
                if idx in used or tr["kind"] != subject.kind:
                    continue
                last = tr["last"]
                d = _centre_dist(last, subject.box)
                if d <= best_d:
                    best, best_d = idx, d
            if best is None:
                live.append({"kind": subject.kind, "last": subject.box, "pts": {}})
                best = len(live) - 1
            used.add(best)
            live[best]["last"] = subject.box
            live[best]["pts"][i] = subject.box
    out = []
    n = i1 - i0
    for tr in live:
        cover = len(tr["pts"]) / float(n) if n else 0.0
        if cover < MIN_COVER:
            continue
        out.append(Track(tr["kind"], tuple((frames[i], tr["pts"].get(i)) for i in range(i0, i1)), cover))
    out.sort(key=lambda t: (RANK.get(t.kind, 9), -t.coverage))
    return out


def _median_box(track: Track) -> Box | None:
    boxes = [b for _f, b in track.points if b is not None]
    if not boxes:
        return None
    return Box(*(statistics.median(getattr(b, f) for b in boxes) for f in ("x0", "y0", "x1", "y1")))


def _apart(a: Track, b: Track) -> bool:
    ma, mb = _median_box(a), _median_box(b)
    if ma is None or mb is None:
        return False
    if ma.contains(mb.cx, mb.cy) or mb.contains(ma.cx, ma.cy):
        return False
    return _centre_dist(ma, mb) >= APART_DIST


def _as_pip(track: Track) -> Track:
    """A *screen* track that is small and sits in a corner (a facecam box, a camera overlay) is a *pip*: it goes
    on top before the rest of the screen."""
    if track.kind != "screen":
        return track
    box = _median_box(track)
    if box is None or box.w * box.h > PIP_AREA:
        return track
    if min(box.cx, 1.0 - box.cx) <= PIP_CORNER and min(box.cy, 1.0 - box.cy) <= PIP_CORNER:
        return track._replace(kind="pip")
    return track


def assign_slots(tracks: Sequence[Track]) -> tuple[Track | None, Track | None]:
    """The main subject (best kind, then most often found) and, when there is one, a second subject apart
    from it (the next in that order that is far enough away and not inside it)."""
    if not tracks:
        return None, None
    first = tracks[0]
    for other in tracks[1:]:
        if _apart(first, other):
            return first, other
    return first, None


# -- layout --------------------------------------------------------------------------------------

def hold_runs(labels: Sequence[str], frames: Sequence[int], fps: float,
              min_hold: float = MIN_HOLD_S) -> list[tuple[str, int, int]]:
    """Runs ``(label, first sample, last sample)`` of *labels* with every run shorter than *min_hold*
    seconds given to its longer neighbour, so the layout does not flicker."""
    if not labels:
        return []
    fps = fps if fps > 0 else 24.0
    runs: list[list] = []
    for i, lab in enumerate(labels):
        if runs and runs[-1][0] == lab:
            runs[-1][2] = i
        else:
            runs.append([lab, i, i])

    def seconds(run: list) -> float:
        # A run lasts from its first sample to the start of the next (the last one: one sample gap).
        end = frames[run[2] + 1] if run[2] + 1 < len(frames) else frames[run[2]] + (
            (frames[run[2]] - frames[run[1]]) / max(1, run[2] - run[1]) if run[2] > run[1] else fps)
        return (end - frames[run[1]]) / fps

    changed = True
    while changed and len(runs) > 1:
        changed = False
        short = [k for k, r in enumerate(runs) if seconds(r) < min_hold]
        if not short:
            break
        k = min(short, key=lambda j: seconds(runs[j]))
        left = runs[k - 1] if k > 0 else None
        right = runs[k + 1] if k + 1 < len(runs) else None
        target = left if right is None or (left is not None and seconds(left) >= seconds(right)) else right
        assert target is not None
        target[1], target[2] = min(target[1], runs[k][1]), max(target[2], runs[k][2])
        del runs[k]
        changed = True
        merged: list[list] = []
        for r in runs:
            if merged and merged[-1][0] == r[0]:
                merged[-1][2] = r[2]
            else:
                merged.append(r)
        runs = merged
    return [(r[0], r[1], r[2]) for r in runs]


def choose_layout(requested: str, runs: Sequence[tuple[str, int, int]], have_secondary: bool) -> str:
    """``single`` or ``split`` for the strip: *requested* ``auto`` takes the one that holds most samples
    (after the minimum hold); ``single`` / ``split`` is taken as asked (a split needs two subjects)."""
    if requested == "single":
        return "single"
    if requested == "split":
        return "split" if have_secondary else "single"
    score = {"single": 0, "split": 0}
    for lab, a, b in runs:
        if lab in score:
            score[lab] += b - a + 1
    return "split" if have_secondary and score["split"] > score["single"] else "single"


def analyse(samples: Sequence[Sample], fps: float, requested: str = "auto",
            kinds: Sequence[str] | None = None) -> Analysis:
    """Everything above on the sampled moments of one shot.  *kinds*: keep only subjects of these kinds
    (``face`` / ``person`` for "faces only", ``pip`` / ``screen`` / ``pointer`` for "screen only")."""
    frames = tuple(s.frame for s in samples)
    cuts = tuple(i for i in range(1, len(samples)) if is_cut(samples[i - 1].grid, samples[i].grid))
    per_sample = []
    for s in samples:
        activity = find_activity(s.grid, s.later) if s.later is not None else []
        found = merge_faces(activity, s.faces, s.people)
        per_sample.append([x for x in found if kinds is None or x.kind in kinds])
    primary: list[Track] = []
    secondary: list[Track | None] = []
    labels = [""] * len(samples)
    for i0, i1 in _segments(len(samples), cuts):
        found = sorted((_as_pip(t) for t in _tracks(frames, per_sample, i0, i1)),
                       key=lambda t: (RANK.get(t.kind, 9), -t.coverage))
        first, second = assign_slots(found)
        if first is None:
            for i in range(i0, i1):
                labels[i] = "centre"
            continue
        primary.append(first)
        secondary.append(second)
        for i in range(i0, i1):
            here = second is not None and second.points[i - i0][1] is not None and first.points[i - i0][1] is not None
            labels[i] = "split" if here else "single"
    runs = tuple(hold_runs(labels, frames, fps))
    have_second = any(s is not None for s in secondary)
    if not primary:
        layout = "centre"
    else:
        layout = choose_layout(requested, runs, have_second)
    return Analysis(frames, cuts, tuple(primary), tuple(secondary), runs, layout)


# -- from tracks to numbers ----------------------------------------------------------------------

def track_samples(tracks: Sequence[Track | None], attr: str = "cx") -> list[tuple[int, float | None]]:
    """``(frame, x)`` samples for ``reframe_math.smooth_track`` of a slot's tracks across the segments
    (``attr`` ``cx`` for the horizontal centre, ``cy`` for the vertical one, an unfound moment ``None``)."""
    out: list[tuple[int, float | None]] = []
    for tr in tracks:
        if tr is None:
            continue
        out.extend((f, None if b is None else float(getattr(b, attr))) for f, b in tr.points)
    return out


def part_framing(tracks: Sequence[Track | None], rect_w: float, rect_h: float, src_w: float, src_h: float,
                 fps: float) -> tuple[list[tuple[int, float, float]], float]:
    """Keys ``(frame, u, v)`` and the zoom of one part that follows a slot: *u* is the picture's horizontal
    fraction from the left, *v* the vertical one from the bottom, both at the middle of the part; the zoom
    (over the cover scale of the part's rectangle) is fixed per part: it shows the subject's usual size with
    the kind's margin, between 1 and :data:`ZOOM_MAX`."""
    live = [t for t in tracks if t is not None]
    if not live:
        return [], 1.0
    xs = smooth_segments(live, "cx", fps)
    ys = smooth_segments(live, "cy", fps)
    if not xs or not ys:
        return [], 1.0
    frames = sorted({f for f, _v in xs} | {f for f, _v in ys})
    xs_f = _interp(xs, frames)
    ys_f = _interp(ys, frames)
    boxes = [(t.kind, b) for t in live for _f, b in t.points if b is not None]
    kind = live[0].kind
    w = max(statistics.median(max(b.w, MIN_SIZE.get(k, 0.0)) for k, b in boxes), 1e-3)
    h = max(statistics.median(max(b.h, MIN_SIZE.get(k, 0.0)) for k, b in boxes), 1e-3)
    margin = MARGIN.get(kind, 1.1)
    need = min(rect_w / (margin * w * src_w), rect_h / (margin * h * src_h))
    cover = max(rect_w / src_w, rect_h / src_h)
    zoom = max(1.0, min(ZOOM_MAX, need / cover))
    return [(f, x, 1.0 - y) for f, x, y in zip(frames, xs_f, ys_f)], round(zoom, 4)


def smooth_segments(tracks: Sequence[Track], attr: str, fps: float) -> list[tuple[int, float]]:
    """Smoothed keys of each segment's track, one after the other.  Smoothing never runs across a cut: the
    value jumps there (held until the frame before the middle of the gap between the two samples, then the
    new one), so the crop does not slide between two different shots."""
    keys: list[tuple[int, float]] = []
    for tr in tracks:
        mine = rm.smooth_track(track_samples([tr], attr), fps)
        if not mine:
            continue
        if keys:
            prev_f, prev_v = keys[-1]
            mid = (prev_f + mine[0][0]) // 2
            if mid - 1 > prev_f:
                keys.append((mid - 1, prev_v))
            if keys[-1][0] < mid < mine[0][0]:
                keys.append((mid, mine[0][1]))
        keys.extend(mine)
    return keys


def _interp(keys: Sequence[tuple[int, float]], frames: Sequence[int]) -> list[float]:
    """Linear value of the keys at each of *frames* (held before the first and after the last)."""
    out = []
    for f in frames:
        if f <= keys[0][0]:
            out.append(keys[0][1])
        elif f >= keys[-1][0]:
            out.append(keys[-1][1])
        else:
            for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
                if f0 <= f <= f1:
                    t = 0.0 if f1 == f0 else (f - f0) / float(f1 - f0)
                    out.append(v0 + (v1 - v0) * t)
                    break
    return out
