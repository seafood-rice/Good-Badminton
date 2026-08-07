"""End-to-end classical tracking over a synthesised static-camera clip.

Also pins the two performance decisions Task 5 rests on, both of which came from
profiling rather than intuition: gain correction via an 8-bit LUT (the float path
cost 31 ms/frame on a 4K frame, a third of the total) and processing restricted to
the court bounding box.
"""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from badminton_analysis.shuttle_track.classical import (is_static_camera,  # noqa: E402
                                                       track_video)

W, H, N = 960, 540, 90
QUAD = [(280.0, 240.0), (680.0, 240.0), (900.0, 500.0), (60.0, 500.0)]

# Shuttle parameters chosen so every drawn frame stays above the tracker's min_speed
# of 8 px/frame: v starts at 30 and decays 0.97 over 40 frames, ending at
# 30*0.97**39 = 9.3. Total travel is ~704 px, which fits inside W=960 starting at
# x=880. Radius is floored at 3 so blob area stays above area_lo=15, and otherwise
# tracks speed, reproducing the area~speed signature the tracker's score keys on.
SHUTTLE_FIRST, SHUTTLE_LAST = 10, 50


def _texture(seed=5):
    """Non-periodic two-axis texture for the background.

    A periodic grid is pathological for phase correlation: equally-spaced lines give
    several equal correlation peaks, which produced spurious 160 px shifts (half the
    downscaled width) and inverted the static-vs-panning verdict. Random blobs give
    an unambiguous peak, like real scene texture.
    """
    rng = np.random.RandomState(seed)
    tex = np.full((H, W, 3), 60, np.uint8)
    for _ in range(400):
        cx, cy = int(rng.randint(0, W)), int(rng.randint(0, H))
        r = int(rng.randint(3, 12))
        val = int(rng.randint(90, 200))
        cv2.circle(tex, (cx, cy), r, (val, val, val), -1)
    return tex


_BG_TEXTURE = _texture()


def _write(path, shift_per_frame=0.0, with_shuttle=True, bg=60):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (W, H))
    x, y, v = 880.0, 150.0, -30.0
    for i in range(N):
        frame = _BG_TEXTURE.copy() if bg == 60 else np.full((H, W, 3), bg, np.uint8)
        if shift_per_frame:
            m = np.float32([[1, 0, shift_per_frame * i], [0, 1, 0]])
            frame = cv2.warpAffine(frame, m, (W, H))
        if with_shuttle and SHUTTLE_FIRST <= i < SHUTTLE_LAST:
            r = max(3, int(round(abs(v) / 4.0)))
            cv2.circle(frame, (int(x), int(y)), r, (245, 245, 245), -1)
            x += v
            y += 2.0
            v *= 0.97
        vw.write(frame)
    vw.release()


def test_recovers_a_synthetic_shuttle(tmp_path):
    p = tmp_path / "static.mp4"
    _write(p)
    traj, stats = track_video(str(p), QUAD, warmup=SHUTTLE_FIRST)
    assert stats["frames"] > 0
    hits = [f for f, pt in traj.items() if pt is not None]
    assert len(hits) >= 20, f"only {len(hits)} frames recovered of 40 drawn"
    assert all(0 <= traj[f][0] <= W for f in hits)


def test_contract_matches_tracknet_track_video(tmp_path):
    p = tmp_path / "static.mp4"
    _write(p)
    traj, _stats = track_video(str(p), QUAD, warmup=SHUTTLE_FIRST)
    assert isinstance(traj, dict)
    assert all(isinstance(f, int) for f in traj)
    for pt in traj.values():
        assert pt is None or (isinstance(pt, tuple) and len(pt) == 2)
    assert min(traj) == 0, "frames must be 0-based, as tracknet.track_video is"


def test_blank_clip_yields_no_track(tmp_path):
    p = tmp_path / "blank.mp4"
    _write(p, with_shuttle=False)
    _traj, stats = track_video(str(p), QUAD, warmup=SHUTTLE_FIRST)
    # Allowed to be non-zero only marginally: codec noise can survive the masks, but
    # it must never assemble into a scoring ballistic track.
    assert stats["covered"] <= 2, f"blank clip produced {stats['covered']} points"


def test_max_frames_is_respected(tmp_path):
    p = tmp_path / "static.mp4"
    _write(p)
    _traj, stats = track_video(str(p), QUAD, warmup=SHUTTLE_FIRST, max_frames=30)
    assert stats["frames"] <= 30


def test_background_is_refreshed_periodically(tmp_path, monkeypatch):
    """A background built once decays; the reservoir must rebuild it."""
    import badminton_analysis.shuttle_track.classical as clmod

    calls = []
    real = clmod.build_masks

    def counting(frames, quad, **kw):
        calls.append(len(frames))
        return real(frames, quad, **kw)

    monkeypatch.setattr(clmod, "build_masks", counting)
    p = tmp_path / "static.mp4"
    _write(p)
    _traj, stats = clmod.track_video(str(p), QUAD, warmup=SHUTTLE_FIRST,
                                     refresh_every=30, reservoir=8)
    assert len(calls) >= 3, f"expected periodic rebuilds, saw {len(calls)}"
    assert stats["refreshes"] == len(calls) - 1


def test_static_camera_detected(tmp_path):
    p = tmp_path / "static.mp4"
    _write(p)
    assert is_static_camera(str(p)) is True


def test_panning_camera_detected(tmp_path):
    # 6 px/frame, comfortably above the 3.0 px/frame threshold. The threshold is 3.0
    # because this project's broadcast clip measures 1.37 px/frame of fixed-camera
    # jitter, so a tighter bound made that clip's routing knife-edge.
    p = tmp_path / "moving.mp4"
    _write(p, shift_per_frame=6.0)
    assert is_static_camera(str(p)) is False


def test_jitter_below_the_threshold_is_still_static(tmp_path):
    """Sub-threshold jitter must not route a fixed camera away from this detector."""
    p = tmp_path / "jitter.mp4"
    _write(p, shift_per_frame=0.4)
    assert is_static_camera(str(p)) is True


def test_missing_file_is_reported_not_crashed(tmp_path):
    with pytest.raises(ValueError):
        track_video(str(tmp_path / "nope.mp4"), QUAD)


# --- performance decisions, pinned so a future "optimisation" cannot silently undo them ---

def test_masks_precompute_allow_and_roi():
    """Per-frame work must not recompute constants or scan outside the court."""
    from badminton_analysis.shuttle_track.classical import build_masks

    frames = [np.full((H, W), 60, np.uint8) for _ in range(8)]
    masks = build_masks(frames, QUAD, band_top_px=60)
    assert "allow" in masks and "roi" in masks
    x0, y0, x1, y1 = masks["roi"]
    assert 0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H
    # the ROI must be a strict subset when the quad does not span the frame
    assert (x1 - x0) * (y1 - y0) < W * H


def test_gain_correction_matches_the_float_reference_exactly():
    """The LUT path replaced a float pass costing 31 ms/frame; results must match."""
    from badminton_analysis.shuttle_track.classical import _gain_correct

    rng = np.random.RandomState(3)
    gray = rng.randint(0, 256, size=(64, 64)).astype(np.uint8)
    median = np.full((64, 64), int(gray.mean() * 0.8), np.uint8)
    got = _gain_correct(gray, median)
    ratio = float(median.mean()) / float(gray.mean())
    want = np.clip(gray.astype(np.float32) * ratio, 0, 255).astype(np.uint8)
    assert np.array_equal(got, want)


def test_windowing_recovers_multiple_separate_flights():
    """The one-shuttle prior must not collapse a match into a single trajectory.

    select_track seeds on one chain and extends only with abutting ones. Run over a
    whole video that yields one flight plus neighbours, but a rally is a SEQUENCE of
    flights separated by contacts that break ballistic continuity. Windowing gives
    each flight its own selection.
    """
    from badminton_analysis.shuttle_track.classical import _track_windowed
    from badminton_analysis.shuttle_track.shuttle_tracker import (Candidate,
                                                                 build_chains,
                                                                 select_track)

    n = 900
    frames = [[] for _ in range(n)]
    # three well-separated flights, each ballistic with area tracking speed
    for f0, x0, direction in ((50, 200.0, 1.0), (400, 1800.0, -1.0), (750, 400.0, 1.0)):
        x, v = x0, 40.0 * direction
        for k in range(40):
            frames[f0 + k].append(
                Candidate(x, 500.0 + 2.0 * k, int(round(abs(v) * 10)), 200.0))
            x += v
            v *= 0.96

    single = select_track(build_chains(frames), n)
    windowed = _track_windowed(frames, build_chains, select_track)

    def covered(track, f0):
        return sum(1 for k in range(40) if (f0 + k) in track)

    single_hits = [covered(single, f0) for f0 in (50, 400, 750)]
    win_hits = [covered(windowed, f0) for f0 in (50, 400, 750)]
    assert sum(1 for h in win_hits if h >= 30) == 3, (
        f"windowed recovered {win_hits}, expected all three flights")
    assert sum(1 for h in single_hits if h >= 30) < 3, (
        f"whole-video selection unexpectedly found all three: {single_hits}")


def test_windowing_keeps_chain_search_bounded():
    """Cost must be linear in video length, not explosive."""
    import time

    from badminton_analysis.shuttle_track.classical import _track_windowed
    from badminton_analysis.shuttle_track.shuttle_tracker import (Candidate,
                                                                 build_chains,
                                                                 select_track)

    rng = np.random.RandomState(4)

    def clutter(n_frames, per=40):
        out = [[] for _ in range(n_frames)]
        for i in range(n_frames):
            for _ in range(per):
                out[i].append(Candidate(float(rng.uniform(0, 1200)),
                                        float(rng.uniform(0, 800)),
                                        int(rng.randint(15, 900)),
                                        float(rng.uniform(110, 255))))
        return out

    small = clutter(300)
    big = clutter(1200)
    t = time.time()
    _track_windowed(small, build_chains, select_track)
    t_small = time.time() - t
    t = time.time()
    _track_windowed(big, build_chains, select_track)
    t_big = time.time() - t
    # 4x the frames must cost far less than 4x-squared; allow generous slack for noise
    assert t_big < max(0.25, t_small * 12), (
        f"windowed cost scaled badly: {t_small:.2f}s -> {t_big:.2f}s")


def test_gain_correction_is_skipped_when_levels_already_match():
    from badminton_analysis.shuttle_track.classical import _gain_correct

    gray = np.full((32, 32), 100, np.uint8)
    out = _gain_correct(gray, np.full((32, 32), 100, np.uint8))
    assert np.array_equal(out, gray)
