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


def _add_valley_ramp(track, start, deltas):
    """Move the wrist through a sequence of signed per-frame DELTAS (px)
    starting at frame `start` - accelerate/decelerate/reaccelerate like a
    real stroke's forward-swing-into-follow-through, rather than a single
    jump-and-revert. Holds the resulting position for the rest of the track
    afterward so the ramp's own tail can't create a spurious revert-delta
    (which would otherwise trip the unrelated teleport guard).
    """
    x = track[start - 1]["wrist"][0]
    for offset, d in enumerate(deltas):
        x += d
        idx = start + offset
        track[idx] = {"frame": track[idx]["frame"], "wrist": (x, 100),
                     "shuttle": track[idx].get("shuttle")}
    for idx in range(start + len(deltas), len(track)):
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


# ── Valley-based merge: VALLEY_RATIO=0.15, MIN_SEP_SEC=0.3 ─────────────────
# A fixed time gap can't tell a swing's own follow-through (peaks close in
# time, wrist never rests between them) apart from two genuinely separate
# strokes fed back-to-back at a similar cadence (peaks also close in time,
# but the wrist DOES reset between them). segment_reps now merges two
# time-adjacent speed peaks based on the min smoothed speed BETWEEN them
# (the valley), relative to the smaller of the two peaks:
#   - valley <= 15% of the smaller peak -> a reset -> genuinely new stroke.
#   - valley > 15% of the smaller peak -> never rested -> same stroke, merge.
# Peaks less than MIN_SEP_SEC=0.3s (9 frames @ 30fps) apart always merge,
# regardless of valley depth (noise floor).

def test_min_sep_floor_merges_regardless_of_valley():
    """Two peaks 5 frames (0.167s) apart - under the MIN_SEP_SEC=0.3s/9-frame
    floor - always merge to one stroke, even though the valley between them
    is a genuine 0 (measured: valley[50:56]=0.0 vs 0.15*min(80,133.33)=12.0,
    i.e. by valley depth ALONE this pair looks like two separate resets). The
    noise floor overrides the valley test at this range: real inter-frame
    jitter this close together is never two strokes.

    peak_frame note (apex fallback): before the fallback, peak_frame == 55
    (the bigger hump). After: no shuttle + flat wrist-y ties the apex search,
    snapping to window_start (55-20=35).
    """
    track = _still_track(120)
    _add_swing(track, 50, jump=120)
    _add_swing(track, 55, jump=200)  # 5 frames later, < MIN_SEP_SEC floor -> always merge
    reps = segment_reps(track, fps=30)
    assert len(reps) == 1
    assert abs(reps[0].peak_frame - 35) <= 2


def test_deep_valley_between_peaks_gives_two_reps():
    """The IMG_9691 fast-cadence case: two genuinely separate fed strokes
    ~0.7s apart, each an isolated hump with several fully-at-rest frames
    between them (the wrist RESETS). Measured: valley[30:52]=0.0 vs
    0.15*min(peak1,peak2)=12.0 - the valley is far below the 15% cutoff, and
    the 21-frame gap is well past the MIN_SEP_SEC=9-frame floor, so the
    valley test alone applies and correctly keeps them separate.

    Red before this fix (old fixed 1.5s/45-frame gap): 21 frames < 45 ->
    ALWAYS merges regardless of valley -> 1 rep. This is the bug the brief
    root-causes: a fixed gap cannot tell this apart from an intra-swing
    follow-through at a similar spacing.
    Green after this fix (valley-based): the deep reset is detected -> 2 reps.

    peak_frame note (apex fallback): no shuttle + flat wrist-y ties the apex
    search, snapping each peak to its window_start (30-20=10, 51-20=31).
    """
    track = _still_track(120)
    _add_swing(track, 30, jump=120)
    _add_swing(track, 51, jump=120)  # 21 frames (~0.7s) later, wrist fully rests between
    reps = segment_reps(track, fps=30)
    assert len(reps) == 2
    peaks = sorted(r.peak_frame for r in reps)
    assert abs(peaks[0] - 10) <= 2
    assert abs(peaks[1] - 31) <= 2


def test_shallow_valley_between_peaks_merges_to_one_rep():
    """The IMG_1270 follow-through case: ONE continuous swing that produces
    two wrist-speed humps (forward swing then follow-through) ~0.9s apart,
    but the wrist never rests between them - it only slows to a shallow
    trough before re-accelerating. Measured: valley[41:69]=30.3 vs
    0.15*min(90,105)=13.5 - the valley sits well above the 15% cutoff (a
    "never rested" profile, not a reset), so the two peaks merge into the
    higher-speed one as this stroke's representative.

    Red/green vs. the old fixed 1.5s-gap code: this fixture's 27-frame
    (0.9s) gap is already under the old 45-frame gap, so the OLD code also
    produces 1 rep here - already correct, by gap-size coincidence rather
    than by understanding the motion. This test is the genuine regression
    guard for the fix: it proves the new valley criterion doesn't
    accidentally start OVER-splitting a real intra-swing follow-through that
    the old code correctly merged.

    peak_frame note (apex fallback): no shuttle + flat wrist-y ties the apex
    search; the kept candidate (the bigger, later hump) snaps to its
    window_start (68-20=48).
    """
    track = _still_track(140)
    # Forward swing ramps up to 100px/frame, decelerates to a 30px/frame
    # trough (never near-zero), then re-accelerates into the follow-through
    # peak at 110px/frame - one continuous motion, no rest.
    deltas = [100, 90, 80, 70, 60, 50, 45, 40, 37, 35, 33, 32, 31, 30, 30,
              31, 32, 33, 35, 37, 40, 45, 50, 60, 70, 80, 90, 100, 105, 110]
    _add_valley_ramp(track, 40, deltas)
    reps = segment_reps(track, fps=30)
    assert len(reps) == 1
    assert abs(reps[0].peak_frame - 48) <= 2


def test_well_separated_strokes_stay_separate_reps():
    """Guard against over-merging: two humps ~1.87s (56 frames) apart, with a
    full rest between them, are genuinely separate swings (deep valley, and
    comfortably past the MIN_SEP_SEC floor) and must still yield 2 reps.

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
#
# Both fixtures below place the follow-through only 8 frames (0.267s) after
# the contact apex - under the MIN_SEP_SEC=0.3s/9-frame floor - so the two
# humps always merge into one stroke regardless of valley depth, letting the
# refinement step (apex fallback / shuttle) decide which frame represents
# it. This is a deliberate, minimal fixture adjustment for the valley-based
# merge (previously the two humps were 15 frames apart and relied on the old
# fixed 1.5s/45-frame gap to merge; that fixed gap is gone, and 15 frames
# with a fully-at-rest gap between them is a genuine valley reset under the
# new criterion). The apex-vs-shuttle selection logic under test is
# unchanged either way.

def test_apex_fallback_prefers_wrist_apex_over_speed_peak_when_no_shuttle():
    """Red before the else-branch: with no shuttle, peak_frame stays on the
    raw speed peak (the big-x-displacement follow-through at frame 58).
    Green after: with no shuttle, the wrist apex (frame 50, the contact,
    where the wrist is raised highest / min image-y) wins instead.
    """
    track = _still_track(120)
    # Contact (frame 50): wrist raised well above its resting height (y=20 vs
    # the y=100 baseline everywhere else) - no shuttle here.
    track[50] = {"frame": 50, "wrist": (100, 20), "shuttle": None}
    # Follow-through (frame 58): the bigger x-displacement -> the raw
    # wrist-speed peak, but at the resting wrist height (y=100), not the apex.
    _add_swing(track, 58, jump=200)

    reps = segment_reps(track, fps=30)

    assert len(reps) == 1
    assert reps[0].peak_frame == 50  # apex wins, not the frame-58 speed peak


def test_shuttle_still_wins_over_apex_fallback_when_present():
    """Guard: the SAME contact/follow-through setup as the test above, but
    WITH a shuttle right at the wrist at frame 58 -> shuttle refinement must
    still decide peak_frame (58), proving the apex fallback never overrides
    an available shuttle anchor.
    """
    track = _still_track(120)
    for i in range(120):
        track[i]["shuttle"] = (5000, 5000)  # far from the wrist everywhere by default
    track[50] = {"frame": 50, "wrist": (100, 20), "shuttle": (5000, 5000)}  # contact apex, no nearby shuttle
    _add_swing(track, 58, jump=200)  # follow-through: the raw speed peak
    track[58]["shuttle"] = track[58]["wrist"]  # shuttle right at the wrist -> shuttle refinement wins

    reps = segment_reps(track, fps=30)

    assert len(reps) == 1
    assert reps[0].peak_frame == 58  # shuttle anchor wins, apex fallback does not apply
