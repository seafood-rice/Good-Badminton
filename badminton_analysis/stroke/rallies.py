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
    TL, TR, BR, BL; a degenerate quad or a non-positive fps raises ValueError.
    """
    if not fps or float(fps) <= 0:
        raise ValueError("fps must be positive")
    scale = PerspectiveScale.from_quad(quad)
    far_y = (float(quad[0][1]) + float(quad[1][1])) / 2.0
    near_y = (float(quad[3][1]) + float(quad[2][1])) / 2.0
    return (SPRINT_CAP_MPS * scale.px_per_m(far_y) / float(fps),
            SPRINT_CAP_MPS * scale.px_per_m(near_y) / float(fps))


def side_speed(points, cap_px):
    """Per-frame displacement of one side's point series, in px/frame.

    Zero where either endpoint is missing (a gap is not a jump), and zero where
    the delta exceeds ``cap_px`` -- that is a track break, not movement.
    """
    out = [0.0] * len(points)
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        if a is None or b is None:
            continue
        v = math.hypot(b[0] - a[0], b[1] - a[1])
        if v <= cap_px:
            out[i] = v
    return out


def swing_activity(track, fps, caps=None):
    """``(times, activity)``: smoothed racket-arm activity per recorded frame.

    ``track`` is the per-frame ``_rally_track``: records with ``frame``,
    ``wrist_lower`` and ``wrist_upper``. Per side, wrist speed with teleports
    zeroed; ``max`` across sides; then a ``round(SMOOTH_SEC * fps)``-sample
    trailing mean. This is the variant that scored F1 0.644 against human
    labels in §0.16, and it is arithmetically identical to
    ``scripts/score_rally_labels.py::load_activity`` (the combining rule was
    under-specified in §0.8 and reproducing it loosely moved coverage by 20
    points, §0.15 Defect 1).

    ``caps`` is ``(far_cap_px, near_cap_px)`` -- see ``baseline_caps``. The far
    cap bounds the UPPER side and the near cap the LOWER side, because each
    player sits at a different perspective scale. None falls back to
    ``TELEPORT_MIN_PX`` for both, which is ~4.7x too loose for the far player.
    Times are ``frame / fps`` seconds.
    """
    far_cap, near_cap = caps if caps is not None else (TELEPORT_MIN_PX, TELEPORT_MIN_PX)
    lower = [rec.get("wrist_lower") for rec in track]
    upper = [rec.get("wrist_upper") for rec in track]
    act = [max(a, b) for a, b in zip(side_speed(lower, near_cap),
                                     side_speed(upper, far_cap))]
    fps = float(fps)
    times = [rec["frame"] / fps for rec in track]
    return times, smooth(act, max(1, int(round(SMOOTH_SEC * fps))))
