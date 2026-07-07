from badminton_analysis.posture.rep_segmenter import RepWindow, segment_reps


def _still_track(n, wrist=(100, 100)):
    return [{"frame": i, "wrist": wrist, "shuttle": None} for i in range(n)]


def _add_swing(track, center, jump=120):
    # A single large wrist displacement at `center` creates a clear speed spike.
    track[center] = {"frame": center, "wrist": (100 + jump, 100),
                     "shuttle": track[center].get("shuttle")}
    return track


def _add_teleport(track, at, jump=500):
    """A positional jump that PERSISTS (does not revert next frame): simulates
    a person-identity flip or a hard cut, not a real swing. Produces exactly
    one large raw delta (entering `at`); everything after stays quiet at the
    new position, same as everything before was quiet at the old one.
    """
    for j in range(at, len(track)):
        track[j] = {"frame": track[j]["frame"], "wrist": (100 + jump, 100),
                   "shuttle": track[j].get("shuttle")}
    return track


def _add_swing_ramp(track, start, xs):
    """Move the wrist through consecutive absolute x positions (y fixed at
    100) starting at frame `start` - a multi-frame speed profile (accelerate
    then decelerate) instead of a single jump-and-revert, so deltas vary
    (e.g. 20/20/80/20/20/80) like a real stroke rather than one flat spike.
    """
    for offset, x in enumerate(xs):
        idx = start + offset
        track[idx] = {"frame": track[idx]["frame"], "wrist": (x, 100),
                     "shuttle": track[idx].get("shuttle")}
    return track


def test_repwindow_to_dict():
    rw = RepWindow(rep_id=1, peak_frame=30, window_start=10, window_end=45, prominence=0.9)
    d = rw.to_dict()
    assert d["rep_id"] == 1 and d["peak_frame"] == 30
    assert d["window_start"] == 10 and d["window_end"] == 45


def test_two_swings_far_apart_give_two_reps():
    track = _still_track(120)
    _add_swing(track, 30)
    _add_swing(track, 90)
    reps = segment_reps(track, fps=30)
    assert len(reps) == 2
    peaks = sorted(r.peak_frame for r in reps)
    assert abs(peaks[0] - 30) <= 2 and abs(peaks[1] - 90) <= 2
    assert reps[0].rep_id == 1 and reps[1].rep_id == 2


def test_still_track_gives_no_reps():
    assert segment_reps(_still_track(120), fps=30) == []


def test_close_swings_collapse_to_one():
    track = _still_track(120)
    _add_swing(track, 50, jump=120)
    _add_swing(track, 55, jump=200)  # within 0.8s (24 frames) -> keep the bigger (55)
    reps = segment_reps(track, fps=30)
    assert len(reps) == 1
    assert abs(reps[0].peak_frame - 55) <= 2


def test_window_clamps_at_zero():
    track = _still_track(60)
    _add_swing(track, 5)
    reps = segment_reps(track, fps=30, pre=20, post=15)
    assert reps[0].window_start == 0


def test_shuttle_refines_peak_frame():
    track = _still_track(120)
    _add_swing(track, 40, jump=120)
    # Put the shuttle nearest the wrist at frame 42 (within the window) -> snap there.
    for i in range(120):
        track[i]["shuttle"] = (5000, 5000)
    track[42]["shuttle"] = track[42]["wrist"]
    reps = segment_reps(track, fps=30)
    assert len(reps) == 1
    assert reps[0].peak_frame == 42


def test_empty_and_all_none_wrist():
    assert segment_reps([], fps=30) == []
    none_track = [{"frame": i, "wrist": None, "shuttle": None} for i in range(50)]
    assert segment_reps(none_track, fps=30) == []


# ── Teleport guard: a person-flip / hard-cut jump is a track break, not a rep ──

def test_teleport_delta_does_not_spawn_a_phantom_rep():
    """B1 (red before the fix): a genuine swing (deltas ~40px) plus a single
    500px teleport (person-flip / hard cut) in an otherwise-quiet stretch
    must yield only the genuine rep.

    Before the guard: the teleport's raw 500px delta survives smoothing and
    clears the candidate floor just like the genuine swing does -> a second,
    phantom rep at the teleport frame. After the guard: the implausible
    delta (500 > max(TELEPORT_MIN_PX, TELEPORT_MEDIAN_MULT * median~40) =
    400) is zeroed before smoothing, so no candidate ever appears there.
    """
    track = _still_track(150)
    _add_swing(track, 30, jump=40)
    _add_teleport(track, 90, jump=500)

    reps = segment_reps(track, fps=30)

    peak_frames = [r.peak_frame for r in reps]
    assert all(abs(f - 90) > 5 for f in peak_frames), peak_frames
    assert len(reps) == 1
    assert abs(reps[0].peak_frame - 30) <= 2


def test_teleport_guard_does_not_clip_genuine_fast_swings():
    """B2: legitimate swing deltas (up to 80px, median ~20 -> guard threshold
    = max(TELEPORT_MIN_PX, TELEPORT_MEDIAN_MULT * 20) = 200) must all stay
    well under the teleport threshold, so the guard is a no-op here and rep
    detection is unaffected (same rep count as without the guard).
    """
    track = _still_track(150)
    _add_swing_ramp(track, 30, [120, 140, 220, 200, 180, 100])
    _add_swing_ramp(track, 90, [120, 140, 220, 200, 180, 100])

    reps = segment_reps(track, fps=30)

    assert len(reps) == 2
    peaks = sorted(r.peak_frame for r in reps)
    assert 30 <= peaks[0] <= 35
    assert 90 <= peaks[1] <= 95
