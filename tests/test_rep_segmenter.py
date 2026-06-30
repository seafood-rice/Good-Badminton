from badminton_analysis.posture.rep_segmenter import RepWindow, segment_reps


def _still_track(n, wrist=(100, 100)):
    return [{"frame": i, "wrist": wrist, "shuttle": None} for i in range(n)]


def _add_swing(track, center, jump=120):
    # A single large wrist displacement at `center` creates a clear speed spike.
    track[center] = {"frame": center, "wrist": (100 + jump, 100),
                     "shuttle": track[center].get("shuttle")}
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
