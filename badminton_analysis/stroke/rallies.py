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

GAP_SEC = 1.0
MIN_LEN_SEC = 2.0
SWING_FRAC = 0.25
SMOOTH_SEC = 0.5

# As-shipped values, deliberately NOT the fitted ones. An 84-point grid over
# (swing_frac, gap_sec, min_len_sec) buys +3.5 F1 points, fitted on 11 rallies
# from a single video -- a real overfitting risk -- and its min_len_sec of 3.0
# would discard genuinely short rallies on other footage (§0.16).


def smooth(values, window):
    """Trailing mean over ``window`` samples; window<=1 is a no-op copy."""
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

    thr = float(frac) * _p99_5(activity)
    if thr <= 0.0:
        return []
    active = [float(v) >= thr for v in activity]

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
