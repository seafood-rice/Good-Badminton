import numpy as np

from badminton_analysis.stroke import stroke_windows as sw
from badminton_analysis.stroke.hit_features import FEATURE_DIM, PLAYER_DIM, SHUTTLE_DIM
from badminton_analysis.stroke.hit_signals import RawSignals

CORNERS = np.array([[400, 200], [880, 200], [1180, 700], [100, 700]], np.float32)


def _sig(n=300, fps=30.0, shuttle=True, pose=True):
    sh = np.full((n, 2), np.nan, np.float32)
    if shuttle:
        sh[:, 0] = np.linspace(300, 900, n)
        sh[:, 1] = 400 + 100 * np.sin(np.linspace(0, 6, n))
    kp = np.full((n, 2, 17, 2), np.nan, np.float32)
    if pose:
        base = np.stack(np.meshgrid(np.arange(17) * 3.0, [0.0]), -1)[0]
        kp[:, 0] = base + [640, 600]
        kp[:, 1] = base + [640, 250]
        kp[:, :, 5:7, 1] -= 60      # shoulders above hips -> valid torso
    return RawSignals(fps, 1, sh, kp, CORNERS, (1280, 720))


def test_window_shape_and_flag():
    X, ok = sw.extract(_sig(), [{"frame": 150, "hitter": "upper"}, {"frame": 150, "hitter": "lower"}])
    assert X.shape == (2, sw.WINDOW_STEPS, sw.INPUT_DIM) and X.dtype == np.float32
    assert X[0, :, -1].min() == 1.0 and X[1, :, -1].max() == 0.0
    assert ok.tolist() == [True, True]


def test_hitter_first_swaps_player_blocks():
    X, _ = sw.extract(_sig(), [{"frame": 150, "hitter": "upper"}, {"frame": 150, "hitter": "lower"}])
    a, b = SHUTTLE_DIM, SHUTTLE_DIM + PLAYER_DIM
    np.testing.assert_array_equal(X[0, :, a:b], X[1, :, b:FEATURE_DIM])
    np.testing.assert_array_equal(X[0, :, :SHUTTLE_DIM], X[1, :, :SHUTTLE_DIM])


def test_window_pads_at_edges():
    X, ok = sw.extract(_sig(n=40), [{"frame": 1, "hitter": "lower"}, {"frame": 40, "hitter": "lower"}])
    assert X.shape[0] == 2
    assert np.all(X[0, :sw.PRE_STEPS, :FEATURE_DIM] == 0)       # before the video starts
    assert not ok[1]                                             # no post-hit shuttle steps


def test_no_signal_without_shuttle_or_pose():
    _, ok = sw.extract(_sig(shuttle=False), [{"frame": 150, "hitter": "lower"}])
    assert not ok[0]
    _, ok = sw.extract(_sig(pose=False), [{"frame": 150, "hitter": "lower"}])
    assert not ok[0]


def test_resamples_60fps_to_30hz():
    X, ok = sw.extract(_sig(n=600, fps=60.0), [{"frame": 300, "hitter": "lower"}])
    assert X.shape == (1, sw.WINDOW_STEPS, sw.INPUT_DIM) and ok[0]


def test_empty_hits():
    X, ok = sw.extract(_sig(), [])
    assert X.shape == (0, sw.WINDOW_STEPS, sw.INPUT_DIM) and ok.shape == (0,)
