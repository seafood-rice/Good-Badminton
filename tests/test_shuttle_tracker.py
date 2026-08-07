"""Tracker behaviour: ballistic chaining, the area~speed score, and one-shuttle selection.

Pure geometry over point lists -- no video decoding -- so the real 30-frame trajectory
hand-verified on the DJI footage can serve as a fixture alongside synthetic cases.
"""
import pytest

from badminton_analysis.shuttle_track.shuttle_tracker import (Candidate, build_chains,
                                                              score_chain, select_track)

# The 30-frame trajectory hand-verified in design-doc section 0.22 (t=68.485..68.969s).
# Area tracks speed here (speed ratio 13.7, area ratio 13.9) -- the signature the score
# keys on, because a small fast object's apparent size is its motion-blur length.
VERIFIED = [
    (2493, 758, 1053), (2397, 774, 881), (2322, 790, 714), (2261, 804, 585),
    (2211, 815, 500), (2168, 825, 413), (2133, 833, 365), (2102, 842, 319),
    (2076, 850, 293), (2053, 856, 245), (2032, 864, 180), (2013, 872, 233),
    (1997, 878, 218), (1981, 885, 202), (1968, 892, 203), (1956, 899, 186),
    (1944, 905, 182), (1933, 912, 169), (1924, 919, 166), (1914, 925, 151),
    (1906, 932, 150), (1898, 939, 147), (1891, 946, 137), (1884, 953, 138),
    (1877, 960, 135), (1871, 967, 138), (1865, 974, 131), (1861, 982, 114),
    (1855, 988, 90), (1850, 995, 76),
]
OFFSET = 5


def _verified_frames(n_frames=40, offset=OFFSET):
    frames = [[] for _ in range(n_frames)]
    for i, (x, y, a) in enumerate(VERIFIED):
        frames[offset + i].append(Candidate(float(x), float(y), a, 200.0))
    return frames


def test_recovers_the_verified_trajectory_end_to_end():
    frames = _verified_frames()
    track = select_track(build_chains(frames), len(frames))
    for i, (x, y, _a) in enumerate(VERIFIED):
        got = track.get(OFFSET + i)
        assert got is not None, f"frame {OFFSET + i} missing"
        assert abs(got[0] - x) <= 1.0 and abs(got[1] - y) <= 1.0


def test_stationary_points_never_chain():
    # The costing spike's original bug: min_speed was not enforced when EXTENDING a
    # chain, so a static blob chained across the whole window ("longest 180, speed 0").
    frames = [[Candidate(500.0, 500.0, 100, 200.0)] for _ in range(30)]
    assert build_chains(frames) == []


def test_area_uncorrelated_with_speed_scores_below_a_shuttle():
    n = 12
    shuttle, clutter = [[] for _ in range(n)], [[] for _ in range(n)]
    x, v = 100.0, 60.0
    for i in range(n):
        shuttle[i].append(Candidate(x, 500.0, int(round(v * 10)), 200.0))
        clutter[i].append(Candidate(x, 500.0, 300, 200.0))   # constant area
        x += v
        v *= 0.88
    s_chains, c_chains = build_chains(shuttle), build_chains(clutter)
    assert s_chains and c_chains
    assert score_chain(max(s_chains, key=len)) > score_chain(max(c_chains, key=len))


def test_constant_area_chain_scores_zero():
    """No area~speed relation means no shuttle evidence at all."""
    n = 12
    frames = [[] for _ in range(n)]
    x, v = 100.0, 60.0
    for i in range(n):
        frames[i].append(Candidate(x, 500.0, 300, 200.0))
        x += v
        v *= 0.88
    assert all(score_chain(c) == 0.0 for c in build_chains(frames))


def test_one_shuttle_prior_rejects_a_simultaneous_second_track():
    n = 20
    frames = [[] for _ in range(n)]
    x1, v1 = 100.0, 50.0
    for i in range(n):                      # area tracks speed -> shuttle-like
        frames[i].append(Candidate(x1, 400.0, int(round(v1 * 10)), 210.0))
        x1 += v1
        v1 *= 0.9
    for i in range(n):                      # constant area -> clutter-like
        frames[i].append(Candidate(3000.0 - 30.0 * i, 1500.0, 250, 210.0))
    track = select_track(build_chains(frames), n)
    assert track
    assert all(pt[1] == pytest.approx(400.0, abs=1.0) for pt in track.values())


def test_gap_tolerance_bridges_a_short_occlusion():
    n = 24
    frames = [[] for _ in range(n)]
    x, v = 100.0, 60.0
    for i in range(n):
        if 10 <= i <= 12:                   # occluded for 3 frames
            x += v
            v *= 0.9
            continue
        frames[i].append(Candidate(x, 500.0, int(round(v * 10)), 200.0))
        x += v
        v *= 0.9
    track = select_track(build_chains(frames), n)
    covered = sorted(track)
    assert covered[0] < 10 and covered[-1] > 12
    assert len(track) >= 16


def test_empty_and_short_inputs_are_safe():
    assert build_chains([]) == []
    assert build_chains([[], []]) == []
    assert select_track([], 5) == {}


def test_selected_frames_are_unique_and_in_range():
    frames = _verified_frames()
    track = select_track(build_chains(frames), len(frames))
    assert all(0 <= f < len(frames) for f in track)
    assert len(track) == len(set(track))


def test_max_speed_bounds_the_chain():
    """A jump beyond max_speed must not be chained."""
    frames = [[Candidate(0.0, 0.0, 100, 200.0)],
              [Candidate(400.0, 0.0, 100, 200.0)],
              [Candidate(800.0, 0.0, 100, 200.0)],
              [Candidate(1200.0, 0.0, 100, 200.0)]]
    assert build_chains(frames, max_speed=250.0) == []


def _clutter_frames(n_frames, per_frame, seed=7):
    """Realistic clutter density: the costing spike measured ~60 candidates/frame.

    Clutter is placed across the play region with area drawn independently of any
    motion, so it cannot satisfy the area~speed signature -- which is what the
    score is supposed to exploit. Seeded for determinism.
    """
    import numpy as _np

    rng = _np.random.RandomState(seed)
    frames = [[] for _ in range(n_frames)]
    for i in range(n_frames):
        for _ in range(per_frame):
            frames[i].append(Candidate(float(rng.uniform(200, 3700)),
                                       float(rng.uniform(500, 2100)),
                                       int(rng.randint(15, 1500)),
                                       float(rng.uniform(110, 255))))
    return frames


@pytest.mark.parametrize("per_frame", [20, 60, 120, 200])
def test_recovers_the_verified_arc_buried_in_clutter(per_frame):
    """The case that predicts production behaviour, not the single-candidate one.

    Measured: all 30 arc frames are recovered at every density up to 200
    candidates/frame, even though the chain count grows from 28 to ~1479 -- the
    area~speed score keeps selecting the real trajectory.

    CAVEAT: this clutter is uniform random, so it rarely forms a ballistic chain.
    Real clutter is structured -- a swinging limb moves smoothly and its motion blur
    varies with speed -- and is therefore harder than this. Real precision is
    measured in Task 7 against hand-clicked positions, not here.
    """
    n = 40
    frames = _clutter_frames(n, per_frame)
    for i, (x, y, a) in enumerate(VERIFIED):
        frames[OFFSET + i].append(Candidate(float(x), float(y), a, 200.0))
    track = select_track(build_chains(frames), n)
    hits = 0
    for i, (x, y, _a) in enumerate(VERIFIED):
        got = track.get(OFFSET + i)
        if got is not None and abs(got[0] - x) <= 2.0 and abs(got[1] - y) <= 2.0:
            hits += 1
    assert hits >= 28, f"recovered only {hits}/30 arc frames at {per_frame} clutter/frame"


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_pure_clutter_does_not_fabricate_a_shuttle(seed):
    """Clutter alone yields a 4-frame track -- the minimum min_len permits."""
    n = 40
    track = select_track(build_chains(_clutter_frames(n, 60, seed=seed)), n)
    assert len(track) <= 6, f"clutter alone yielded a {len(track)}-frame track"


def test_min_len_is_respected():
    n = 6
    frames = [[] for _ in range(n)]
    x, v = 100.0, 40.0
    for i in range(3):                      # only 3 points available
        frames[i].append(Candidate(x, 500.0, int(round(v * 10)), 200.0))
        x += v
        v *= 0.9
    assert build_chains(frames, min_len=4) == []
    assert build_chains(frames, min_len=3) != []
