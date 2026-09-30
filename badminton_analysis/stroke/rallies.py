"""Rally segmentation over an already-recorded per-frame analysis track.

Runs AFTER the frame loop, not inside it. Nothing in the loop consumes rally
segments live -- they are written post-loop, and everything that needs them
(per-rally BST, rally_id, coverage metadata) happens later still -- so
segmenting offline costs nothing, may use non-causal windows, and can be
re-run without re-processing the video.

Measured limits, which callers must not overstate (design spec §0.16, scored
against human labels on the owner's own footage): the swing signal finds 10 of
11 rallies but bounds them poorly -- frame-level F1 0.644, a +1.90 s median
start lag, rally count inflated +36%, total rally time +41%. It is fit for
deciding which spans deserve expensive downstream work. It is NOT fit for
defining authoritative rally windows, and a consumer needing the serve inside
its window must pad the start by at least ~2 s.
"""
import math

from ..court.scale import PerspectiveScale

GAP_SEC = 1.0
MIN_LEN_SEC = 2.0
SWING_FRAC = 0.25
SMOOTH_SEC = 0.5

# As-shipped values, deliberately NOT the fitted ones. An 84-point grid over
# (swing_frac, gap_sec, min_len_sec) buys +3.5 F1 points, fitted on 11 rallies
# from a single video -- a real overfitting risk -- and its min_len_sec of 3.0
# would discard genuinely short rallies on other footage (§0.16).


def smooth(values, window):
    """Trailing mean over ``window`` samples; window<=1 is a no-op copy.

    ``window`` is a count of SAMPLES, not seconds: callers convert with
    ``round(SMOOTH_SEC * fps)``.
    """
    w = max(1, int(window))
    if w == 1:
        return [float(v) for v in values]
    out, run = [], 0.0
    for i, v in enumerate(values):
        run += float(v)
        if i >= w:
            run -= float(values[i - w])
        out.append(run / min(i + 1, w))
    return out


def _p99_5(values):
    srt = sorted(values)
    if not srt:
        return 0.0
    return srt[min(len(srt) - 1, int(0.995 * len(srt)))]


def segments_from_activity(times, activity, *, frac=SWING_FRAC,
                           gap_sec=GAP_SEC, min_len_sec=MIN_LEN_SEC):
    """Active spans of ``activity``, as (start_sec, end_sec) pairs.

    The threshold is self-relative -- ``frac`` of this video's own p99.5 --
    the same idiom as rep_segmenter's PEAK_FLOOR_FRAC, so it transfers across
    footage whose absolute pixel speeds differ by a perspective factor.
    """
    if len(times) != len(activity):
        raise ValueError("times and activity must be the same length")
    n = len(activity)
    if n == 0:
        return []

    # A non-finite sample carries no evidence: it is inactive, and it is kept
    # out of the percentile so one inf cannot lift the threshold above every
    # real sample (and a NaN cannot make the sort order undefined).
    finite = [math.isfinite(float(v)) for v in activity]
    thr = float(frac) * _p99_5([float(v) for v, ok in zip(activity, finite) if ok])
    if thr <= 0.0:
        return []
    active = [ok and float(v) >= thr for v, ok in zip(activity, finite)]

    runs, i = [], 0
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1] and (times[j + 1] - times[j]) <= gap_sec:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1

    merged = []
    for run in runs:
        if merged and (times[run[0]] - times[merged[-1][1]]) <= gap_sec:
            merged[-1][1] = run[1]
        else:
            merged.append(run)

    return [(times[a], times[b]) for a, b in merged
            if (times[b] - times[a]) >= min_len_sec]


# --- The swing signal (§0.16) --------------------------------------------------

TELEPORT_MIN_PX = 100.0     # rep_segmenter's absolute fallback, used only without caps
SPRINT_CAP_MPS = 12.0
"""Generous human sprint cap. Above this a frame-to-frame delta is the tracker
jumping between people, not motion -- measured at 5.6% of far-player samples on
the multi-court footage (§0.15)."""


def _valid_point(point):
    """``(x, y)`` floats if ``point`` is two finite numbers, else None."""
    if point is None:
        return None
    try:
        if len(point) != 2:
            return None
        x, y = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return (x, y)


def wrists_from_hands(left, right):
    """Mean of whichever of the two hand points are present, or None.

    This is the combining rule §0.16's F1 0.644 was measured with (the script
    that produced it averaged whichever hands ``detections.jsonl`` carried).
    Changing it silently invalidates that measurement, so every consumer --
    the live recorder and the scoring script -- goes through here.
    """
    pts = [p for p in (_valid_point(left), _valid_point(right)) if p is not None]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def baseline_caps(quad, fps):
    """Per-frame teleport caps ``(far_px, near_px)`` from the court quad's own scale.

    ``SPRINT_CAP_MPS`` at the px/m of the FAR and NEAR baseline rows, divided by
    fps. On DJI 0010 (106.3 / 603.8 px/m at 59.94 fps) this gives the 21.3 /
    120.9 px the §0.16 measurement used (§0.15 Defect 2). ``quad`` is
    TL, TR, BR, BL; a degenerate or non-finite quad, or a non-finite or
    non-positive fps, raises ValueError (a NaN cap would silently zero the
    whole signal).
    """
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        raise ValueError("fps must be a finite positive number") from None
    if not (math.isfinite(fps) and fps > 0):
        raise ValueError("fps must be a finite positive number")
    try:
        coords = [float(v) for p in quad for v in p]
    except (TypeError, ValueError):
        raise ValueError("court quad must be four (x, y) points") from None
    if not all(math.isfinite(v) for v in coords):
        raise ValueError("court quad has a non-finite coordinate")
    scale = PerspectiveScale.from_quad(quad)
    far_y = (float(quad[0][1]) + float(quad[1][1])) / 2.0
    near_y = (float(quad[3][1]) + float(quad[2][1])) / 2.0
    return (SPRINT_CAP_MPS * scale.px_per_m(far_y) / fps,
            SPRINT_CAP_MPS * scale.px_per_m(near_y) / fps)


def track_stride(track):
    """The track's regular frame stride: the median gap between consecutive
    ``frame`` values, as an integer >= 1 (1 for fewer than two records).

    Fast mode records only every ``FAST_FRAME_STRIDE``-th court frame, so
    consecutive records are that many frames apart. The median ignores the
    irregular gaps (non-court stretches) that any track has.
    """
    deltas = sorted(b["frame"] - a["frame"] for a, b in zip(track, track[1:]))
    if not deltas:
        return 1
    mid = len(deltas) // 2
    median = deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2.0
    return max(1, int(round(median)))


def side_speed(points, cap_px, stride=1):
    """Per-frame displacement of one side's point series, in px/frame.

    Each consecutive-record displacement is divided by ``stride`` (the frames
    between regular records) before it is compared with ``cap_px`` and used as
    speed. Zero where either endpoint is missing (a gap is not a jump), and
    zero where the per-frame delta exceeds ``cap_px`` -- that is a track
    break, not movement. At ``stride`` 1 this is the script's arithmetic.
    """
    out = [0.0] * len(points)
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        if a is None or b is None:
            continue
        v = math.hypot(b[0] - a[0], b[1] - a[1])
        if stride != 1:
            v /= stride
        if v <= cap_px:
            out[i] = v
    return out


def swing_activity(track, fps, caps=None):
    """``(times, activity)``: smoothed racket-arm activity per recorded frame.

    ``track`` is the per-frame ``_rally_track``: records with ``frame``,
    ``wrist_lower`` and ``wrist_upper``. Per side, wrist speed with teleports
    zeroed; ``max`` across sides; then a trailing mean over
    ``round(SMOOTH_SEC * fps / stride)`` samples. This is the variant that
    scored F1 0.644 against human labels in §0.16, and at stride 1 it is
    arithmetically identical to
    ``scripts/score_rally_labels.py::load_activity`` (the combining rule was
    under-specified in §0.8 and reproducing it loosely moved coverage by 20
    points, §0.15 Defect 1).

    ``stride`` is the track's regular frame gap (``track_stride``): a strided
    (fast-mode) track has its displacements scaled to per-frame speed and its
    window sized in seconds, not samples. Irregular gaps larger than the
    stride are NOT normalised per pair -- records are treated as adjacent, as
    the script does.

    ``caps`` is ``(far_cap_px, near_cap_px)`` -- see ``baseline_caps``. The far
    cap bounds the UPPER side and the near cap the LOWER side, because each
    player sits at a different perspective scale. None falls back to
    ``TELEPORT_MIN_PX`` for both, which is ~4.7x too loose for the far player.
    Times are ``frame / fps`` seconds.
    """
    far_cap, near_cap = caps if caps is not None else (TELEPORT_MIN_PX, TELEPORT_MIN_PX)
    stride = track_stride(track)
    lower = [rec.get("wrist_lower") for rec in track]
    upper = [rec.get("wrist_upper") for rec in track]
    act = [max(a, b) for a, b in zip(side_speed(lower, near_cap, stride),
                                     side_speed(upper, far_cap, stride))]
    fps = float(fps)
    times = [rec["frame"] / fps for rec in track]
    return times, smooth(act, max(1, int(round(SMOOTH_SEC * fps / stride))))


# --- The shuttle signal, artifact suppression, and signal selection (§0.7, §5 C2) ---

SHUTTLE_DENSITY_MIN = 0.50
STATIC_FRAC_MAX = 0.25
STATIC_SPREAD_PX = 25.0
STATIC_GRID_PX = 50.0
STATIC_DROP_RADIUS_PX = 1.5 * STATIC_SPREAD_PX
"""A static cluster is judged on the 90th percentile of its spread, so up to
10% of its points may lie beyond STATIC_SPREAD_PX of its median. Points are
removed out to this larger radius, so that tail goes with the fixture."""

COURT_VOLUME_RAISE_FRAC = 0.9
"""How far above the far baseline the play volume extends, as a fraction of the
court's own image depth (spec §0.7: 0.9 x court depth).

Not optional: a clear or a lob spends most of its flight ABOVE the far
baseline's image row, so gating to the flat court polygon would discard exactly
the shots that matter (1 of 4,194 detections survive the flat polygon on DJI
0010; 17 survive the raised volume). This gate exists to reject the scoreboard,
the next court over, and the ceiling lights, not to be tight.
"""


def _median(values):
    srt = sorted(values)
    mid = len(srt) // 2
    return srt[mid] if len(srt) % 2 else (srt[mid - 1] + srt[mid]) / 2.0


def _p90(values):
    srt = sorted(values)
    return srt[min(len(srt) - 1, int(math.ceil(0.9 * len(srt))) - 1)]


def suppress_static(track):
    """Drop shuttle detections that are a fixture rather than a shuttle.

    A cluster holding more than STATIC_FRAC_MAX of all detections whose robust
    spread is under STATIC_SPREAD_PX is a light fitting, a line marking, or a
    pole-mounted decoy -- not a shuttle, which never sits inside a 25 px disc
    for minutes. On the owner's footage this is 86% of the ball model's output
    (§0.7), so without this the density gate would be fooled into choosing a
    dead signal.

    Points are bucketed into STATIC_GRID_PX cells and each cell is MERGED with
    its 8 neighbours before testing, because the real fixture is a jittered
    ~25 px cluster that straddles cell edges. The spread is the 90th-percentile
    distance of the merged points to their median point, so a few stray points
    do not defeat the test the way a max-min spread would. Cells are examined
    fullest first and a suppressed cluster's points leave the pool, so one
    fixture is reported once. Points within STATIC_DROP_RADIUS_PX of the
    cluster's median are removed.

    Returns ``(cleaned_track, summary)`` and never mutates the input.
    ``summary`` is JSON-friendly, for provenance as ``suppressed_static_shuttle``:
    ``{"count": points removed, "cells": [{"point": [x, y], "count": n}, ...]}``
    with the median point of each suppressed cluster rounded to 1 decimal.
    """
    points = {}
    for i, rec in enumerate(track):
        p = _valid_point(rec.get("shuttle"))
        if p is not None:
            points[i] = p
    total = len(points)

    cells = {}
    for i, (x, y) in points.items():
        key = (int(x // STATIC_GRID_PX), int(y // STATIC_GRID_PX))
        cells.setdefault(key, []).append(i)

    drop, summary_cells = set(), []
    for key in sorted(cells, key=lambda k: -len(cells[k])):
        # merged 3x3 block of the cells not yet suppressed
        block = [i for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 for i in cells.get((key[0] + dx, key[1] + dy), ())
                 if i not in drop]
        if len(block) <= STATIC_FRAC_MAX * total:
            continue
        mx = _median([points[i][0] for i in block])
        my = _median([points[i][1] for i in block])
        dist = {i: math.hypot(points[i][0] - mx, points[i][1] - my) for i in block}
        if _p90(list(dist.values())) >= STATIC_SPREAD_PX:
            continue
        removed = [i for i in block if dist[i] <= STATIC_DROP_RADIUS_PX]
        drop.update(removed)
        summary_cells.append({"point": [round(mx, 1), round(my, 1)],
                              "count": len(removed)})

    cleaned = []
    for i, rec in enumerate(track):
        out = dict(rec)
        if i in drop:
            out["shuttle"] = None
        cleaned.append(out)
    return cleaned, {"count": len(drop), "cells": summary_cells}


def _point_in_polygon(x, y, poly):
    """Ray-casting point-in-polygon. Vertices in order, closed implicitly."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1)
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


def gate_to_court_volume(track, quad, raise_frac=COURT_VOLUME_RAISE_FRAC):
    """Drop shuttle detections outside the court's play volume.

    ``quad`` is the annotated 4-corner court in TL, TR, BR, BL order -- the
    ordering badminton_analysis.court.mapper already uses. The far edge is
    raised by ``raise_frac`` of the quad's image depth so airborne shuttles
    count.

    This is what stops a dead detector from looking alive: on the owner's
    multi-court footage it cuts 4,194 detections to 17, which is the *correct*
    answer -- that footage's shuttle detection does not work -- and is exactly
    why the density check must run after this gate rather than on raw output.
    A malformed shuttle point carries no evidence and is dropped too.

    ``quad=None`` gates nothing, so a run without court corners degrades to
    "no gating" instead of silently discarding every detection.

    Returns (gated_track, dropped_count) and never mutates the input.
    """
    if not quad or len(quad) != 4:
        return [dict(rec) for rec in track], 0

    pts = [(float(p[0]), float(p[1])) for p in quad]
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = pts
    top_y = min(tly, try_)
    bottom_y = max(bly, bry)
    lift = max(0.0, (bottom_y - top_y) * float(raise_frac))
    volume = [(tlx, tly - lift), (trx, try_ - lift), (brx, bry), (blx, bly)]

    gated, dropped = [], 0
    for rec in track:
        out = dict(rec)
        raw = rec.get("shuttle")
        if raw is not None:
            p = _valid_point(raw)
            if p is None or not _point_in_polygon(p[0], p[1], volume):
                out["shuttle"] = None
                dropped += 1
        gated.append(out)
    return gated, dropped


def shuttle_density(track):
    """Fraction of frames with a usable shuttle. Call AFTER suppress_static."""
    if not track:
        return 0.0
    return (sum(1 for rec in track if _valid_point(rec.get("shuttle")) is not None)
            / float(len(track)))


def _has_swing_input(track):
    return any(rec.get("wrist_lower") is not None or rec.get("wrist_upper") is not None
               for rec in track)


def choose_signal(track, *, signal="auto"):
    """Pick the segmentation signal and say why. ``track`` must be suppressed already.

    Selection is gated on density rather than trusting whatever the detector
    handed over, because on the owner's footage the shuttle signal is
    measurably dead (§0.7) and using it anyway would produce confident
    nonsense. Returns ``(name, reason)`` with ``name`` in
    ``{"shuttle", "swing", "none"}``; a non-"auto" ``signal`` is honoured as
    pinned.
    """
    if signal != "auto":
        return signal, f"signal pinned to {signal!r} by the caller"

    density = shuttle_density(track)
    if density >= SHUTTLE_DENSITY_MIN:
        return "shuttle", (f"shuttle density {density:.3f} at or above "
                           f"{SHUTTLE_DENSITY_MIN:.2f} after artifact suppression")
    if _has_swing_input(track):
        return "swing", (f"shuttle density {density:.3f} below "
                         f"{SHUTTLE_DENSITY_MIN:.2f} after artifact suppression")
    return "none", (f"shuttle density {density:.3f} below "
                    f"{SHUTTLE_DENSITY_MIN:.2f} and no wrist track to fall back on")
