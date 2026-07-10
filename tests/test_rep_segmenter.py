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


# ── Wrist-apex contact fallback and its effect on flat-y, no-shuttle fixtures ──
# segment_reps now falls back to the wrist apex (min image-y) inside the
# window when no shuttle anchors the contact (see the `else` branch after the
# shuttle-refinement loop). Every fixture below that has no shuttle keeps the
# wrist at a *constant* y (only x moves, to isolate speed-based detection from
# elevation), so every in-window frame ties for "highest" and the tie-break
# (first frame scanned wins) resolves to window_start. This is expected,
# documented behavior of the fallback's tie-break on these flat-y fixtures,
# not a segmentation regression: the swing is still found at the same raw
# peak_frame the old assertions checked, its window is just now re-centered
# on that peak_frame's window_start instead of the peak_frame itself.
# Below, "before"/"after" always refers to peak_frame pre- vs. post-fallback;
# window_start/pre are always the unchanged defaults (pre=20, so before=X ->
# after=X-20, clamped at 0). See test_apex_fallback_prefers_wrist_apex_* below
# for a fixture that actually varies wrist y and exercises the fallback for
# its intended purpose.


def test_repwindow_to_dict():
    rw = RepWindow(rep_id=1, peak_frame=30, window_start=10, window_end=45, prominence=0.9)
    d = rw.to_dict()
    assert d["rep_id"] == 1 and d["peak_frame"] == 30
    assert d["window_start"] == 10 and d["window_end"] == 45


def test_two_swings_far_apart_give_two_reps():
    # Before the apex fallback: peaks landed at the swing frames themselves
    # (30, 90). After: no shuttle + flat wrist-y in this fixture ties the
    # apex search, snapping each peak to its window_start (peak-20): 10, 70.
    track = _still_track(120)
    _add_swing(track, 30)
    _add_swing(track, 90)
    reps = segment_reps(track, fps=30)
    assert len(reps) == 2
    peaks = sorted(r.peak_frame for r in reps)
    assert abs(peaks[0] - 10) <= 2 and abs(peaks[1] - 70) <= 2
    assert reps[0].rep_id == 1 and reps[1].rep_id == 2


def test_still_track_gives_no_reps():
    assert segment_reps(_still_track(120), fps=30) == []


def test_close_swings_collapse_to_one():
    # Before the apex fallback: peak_frame == 55 (the bigger hump). After: no
    # shuttle + flat wrist-y ties the apex search over the window, snapping
    # to window_start (55-20=35).
    track = _still_track(120)
    _add_swing(track, 50, jump=120)
    _add_swing(track, 55, jump=200)  # within 0.8s (24 frames) -> keep the bigger (55)
    reps = segment_reps(track, fps=30)
    assert len(reps) == 1
    assert abs(reps[0].peak_frame - 35) <= 2


# ── Intra-swing double-peak merge: min_gap_sec=1.5 (was 0.8) ────────────────
# One real overhead clear produces two wrist-speed humps (forward swing +
# follow-through/recovery). On IMG_1270 every such pair sat 24-26 frames
# (0.80-0.87s) apart - right on the old 0.8s/24-frame boundary, so both
# survived as separate reps. The new 1.5s/45-frame gap merges them.

def test_intra_swing_double_peak_merges_at_default_gap():
    """Red before the fix (old default min_gap_sec=0.8 -> 24-frame gap): the
    forward-swing hump (frame 40) and its follow-through hump (frame 65, 25
    frames later) are far enough apart to both survive as separate reps, on
    top of a genuinely separate swing at frame 220 (155 frames later) ->
    3 reps. Green after the fix (default min_gap_sec=1.5 -> 45-frame gap):
    the 25-frame-apart pair is now within the gap and merges into the
    higher-prominence hump (frame 65); the far-away swing stays separate ->
    2 reps.

    peak_frame note (apex fallback): before the fallback, the merged/separate
    peaks landed at 65 and 220. After: no shuttle + flat wrist-y in this
    fixture ties the apex search, snapping each to its window_start (65-20=45,
    220-20=200).
    """
    track = _still_track(300)
    _add_swing(track, 40, jump=120)   # forward-swing hump
    _add_swing(track, 65, jump=200)   # follow-through hump, 25 frames later (bigger)
    _add_swing(track, 220, jump=150)  # a genuinely separate swing, 155 frames later

    reps_old = segment_reps(track, fps=30, min_gap_sec=0.8)
    assert len(reps_old) == 3  # documents the pre-fix over-count (red)

    reps_new = segment_reps(track, fps=30)  # new default: min_gap_sec=1.5
    assert len(reps_new) == 2
    peaks = sorted(r.peak_frame for r in reps_new)
    assert abs(peaks[0] - 45) <= 2    # the merged pair keeps its bigger hump (window_start of 65)
    assert abs(peaks[1] - 200) <= 2   # window_start of 220


def test_humps_well_over_1_5s_apart_stay_separate_reps():
    """Guard against over-merging: two humps ~1.87s (56 frames) apart -
    comfortably past the new 1.5s/45-frame gap - are genuinely separate
    swings and must still yield 2 reps.

    peak_frame note (apex fallback): before the fallback, peaks landed at 30
    and 86. After: no shuttle + flat wrist-y ties the apex search, snapping
    each to its window_start (30-20=10, 86-20=66).
    """
    track = _still_track(150)
    _add_swing(track, 30, jump=120)
    _add_swing(track, 86, jump=200)
    reps = segment_reps(track, fps=30)
    assert len(reps) == 2
    peaks = sorted(r.peak_frame for r in reps)
    assert abs(peaks[0] - 10) <= 2 and abs(peaks[1] - 66) <= 2


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

    peak_frame note (apex fallback): before the fallback, the genuine rep's
    peak_frame == 30. After: no shuttle + flat wrist-y ties the apex search,
    snapping to window_start (30-20=10).
    """
    track = _still_track(150)
    _add_swing(track, 30, jump=40)
    _add_teleport(track, 90, jump=500)

    reps = segment_reps(track, fps=30)

    peak_frames = [r.peak_frame for r in reps]
    assert all(abs(f - 90) > 5 for f in peak_frames), peak_frames
    assert len(reps) == 1
    assert abs(reps[0].peak_frame - 10) <= 2


def test_teleport_guard_does_not_clip_genuine_fast_swings():
    """B2: legitimate swing deltas (up to 80px, median ~20 -> guard threshold
    = max(TELEPORT_MIN_PX, TELEPORT_MEDIAN_MULT * 20) = 200) must all stay
    well under the teleport threshold, so the guard is a no-op here and rep
    detection is unaffected (same rep count as without the guard).

    peak_frame note (apex fallback): before the fallback, peaks landed in
    [30, 35] and [90, 95] (raw speed peak within the ramp). After: no
    shuttle + flat wrist-y ties the apex search, snapping each to its
    window_start - the same ranges shifted down by pre=20 frames.
    """
    track = _still_track(150)
    _add_swing_ramp(track, 30, [120, 140, 220, 200, 180, 100])
    _add_swing_ramp(track, 90, [120, 140, 220, 200, 180, 100])

    reps = segment_reps(track, fps=30)

    assert len(reps) == 2
    peaks = sorted(r.peak_frame for r in reps)
    assert 10 <= peaks[0] <= 15
    assert 70 <= peaks[1] <= 75


# ── Wrist-apex contact fallback: no shuttle -> snap to the wrist apex ───────
# For an overhead stroke, the fastest wrist motion is often the
# follow-through whip, not the contact itself. When no shuttle anchors the
# contact, segment_reps now falls back to the in-window wrist apex (min
# image-y = highest point), which sits at or near contact for overhead
# strokes.

def test_apex_fallback_prefers_wrist_apex_over_speed_peak_when_no_shuttle():
    """Red before the else-branch: with no shuttle, peak_frame stays on the
    raw speed peak (the big-x-displacement follow-through at frame 65).
    Green after: with no shuttle, the wrist apex (frame 50, the contact,
    where the wrist is raised highest / min image-y) wins instead.
    """
    track = _still_track(120)
    # Contact (frame 50): wrist raised well above its resting height (y=20 vs
    # the y=100 baseline everywhere else) - no shuttle here.
    track[50] = {"frame": 50, "wrist": (100, 20), "shuttle": None}
    # Follow-through (frame 65): the bigger x-displacement -> the raw
    # wrist-speed peak, but at the resting wrist height (y=100), not the apex.
    _add_swing(track, 65, jump=200)

    reps = segment_reps(track, fps=30)

    assert len(reps) == 1
    assert reps[0].peak_frame == 50  # apex wins, not the frame-65 speed peak


def test_shuttle_still_wins_over_apex_fallback_when_present():
    """Guard: the SAME contact/follow-through setup as the test above, but
    WITH a shuttle right at the wrist at frame 65 -> shuttle refinement must
    still decide peak_frame (65), proving the apex fallback never overrides
    an available shuttle anchor.
    """
    track = _still_track(120)
    for i in range(120):
        track[i]["shuttle"] = (5000, 5000)  # far from the wrist everywhere by default
    track[50] = {"frame": 50, "wrist": (100, 20), "shuttle": (5000, 5000)}  # contact apex, no nearby shuttle
    _add_swing(track, 65, jump=200)  # follow-through: the raw speed peak
    track[65]["shuttle"] = track[65]["wrist"]  # shuttle right at the wrist -> shuttle refinement wins

    reps = segment_reps(track, fps=30)

    assert len(reps) == 1
    assert reps[0].peak_frame == 65  # shuttle anchor wins, apex fallback does not apply
