"""Which signal anchors a rep's contact frame when no shuttle is detected.

The apex fallback picks the HIGHEST wrist point, which is right for an overhead
stroke and wrong for an underhand serve -- rep_segmenter's own comment says so.
"""
from badminton_analysis.posture.rep_segmenter import segment_reps


def _track_with_late_apex(n=120):
    """One speed spike at frame 40; the wrist's highest point is later, at 55.

    The apex must be reached GRADUALLY. A single jump to the high point would create
    its own speed spike larger than the intended one, and segment_reps would then
    find two reps rather than one. The rise is 15 px/frame, comfortably under the
    peak floor: the 120 px jump at frame 40 smooths to ~80, and PEAK_FLOOR_FRAC of
    0.25 puts the floor at ~20.

    The spike must stay under _wrist_speed's teleport floor, which is
    max(TELEPORT_MIN_PX, 10 * median(positive speeds)). The 15 px/frame ramp makes
    that median 15 and the floor 150, so a 160 px spike would be discarded as a
    track break and the speed peak would vanish. 120 px survives.

    Frame 40 is the speed peak; frame 55 is the wrist apex (min image y = 35). Both
    lie inside the rep window (peak 40, pre 20, post 15 at 30 fps -> frames 20..55).
    """
    track = []
    for i in range(n):
        if 45 <= i <= 55:
            y = 200.0 - 15.0 * (i - 44)      # gradual rise to y=35 at frame 55
        elif 56 <= i <= 66:
            y = 35.0 + 15.0 * (i - 55)       # gradual fall back
        else:
            y = 200.0
        x = 220.0 if i == 40 else 100.0      # 120px spike -> the speed peak
        track.append({"frame": i, "wrist": (x, y), "shuttle": None})
    return track


def test_apex_fallback_moves_contact_to_the_wrist_apex():
    reps = segment_reps(_track_with_late_apex(), fps=30, positional_fallback="apex")
    assert len(reps) == 1
    assert reps[0].peak_frame == 55
    assert reps[0].contact_anchor == "apex"


def test_no_fallback_keeps_the_speed_peak():
    reps = segment_reps(_track_with_late_apex(), fps=30, positional_fallback=None)
    assert len(reps) == 1
    assert reps[0].peak_frame == 40
    assert reps[0].contact_anchor == "speed_peak"


def test_apex_is_the_default_so_existing_callers_are_unaffected():
    reps = segment_reps(_track_with_late_apex(), fps=30)
    assert reps[0].peak_frame == 55
    assert reps[0].contact_anchor == "apex"


def test_shuttle_anchor_wins_over_both_and_is_recorded():
    track = _track_with_late_apex()
    # Place the shuttle exactly on the wrist so its distance is 0 and it wins
    # regardless of the wrist's y at that frame.
    track[47]["shuttle"] = track[47]["wrist"]
    reps = segment_reps(track, fps=30, positional_fallback="apex")
    assert reps[0].peak_frame == 47
    assert reps[0].contact_anchor == "shuttle"


def test_contact_anchor_survives_to_dict():
    reps = segment_reps(_track_with_late_apex(), fps=30, positional_fallback=None)
    assert reps[0].to_dict()["contact_anchor"] == "speed_peak"
