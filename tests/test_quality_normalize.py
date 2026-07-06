import numpy as np

from badminton_analysis.quality.normalize import TARGET_FRAMES, normalize_window, posed_frames


def _kp(x_off=0.0):
    kp = np.zeros((17, 2), dtype=float)
    kp[5] = (90.0 + x_off, 40.0)    # L shoulder
    kp[6] = (110.0 + x_off, 40.0)   # R shoulder
    kp[11] = (92.0 + x_off, 100.0)  # L hip
    kp[12] = (108.0 + x_off, 100.0) # R hip
    kp[10] = (130.0 + x_off, 20.0)  # R wrist
    return kp


def _frames(n, x_off=0.0):
    return [{"keypoints": _kp(x_off)} for _ in range(n)]


def test_output_shape_and_dtype():
    out = normalize_window(_frames(30))
    assert out.shape == (TARGET_FRAMES, 34)
    assert out.dtype == np.float32


def test_hip_centering_and_scale():
    out = normalize_window(_frames(30))
    seq = out.reshape(TARGET_FRAMES, 17, 2)
    hips = (seq[:, 11] + seq[:, 12]) / 2
    assert np.allclose(hips, 0.0, atol=1e-5)          # hip midpoint at origin
    torso = np.linalg.norm(seq[0, 5] / 2 + seq[0, 6] / 2 - hips[0])
    assert 0.9 < torso < 1.1                          # torso length ~1


def test_translation_invariance():
    a = normalize_window(_frames(30, x_off=0.0))
    b = normalize_window(_frames(30, x_off=500.0))
    assert np.allclose(a, b, atol=1e-5)


def test_mirroring_flips_x_and_swaps_sides():
    plain = normalize_window(_frames(30)).reshape(TARGET_FRAMES, 17, 2)
    mirrored = normalize_window(_frames(30), mirror=True).reshape(TARGET_FRAMES, 17, 2)
    # valid R wrist (idx 10) lands in the L-wrist slot (idx 9) with negated x
    assert np.allclose(mirrored[:, 9, 0], -plain[:, 10, 0], atol=1e-5)
    assert np.allclose(mirrored[:, 9, 1], plain[:, 10, 1], atol=1e-5)
    # sentinel joints stay masked at zero in both
    assert np.allclose(mirrored[:, 0], 0.0, atol=1e-5)


def test_time_resampling_short_and_long():
    assert normalize_window(_frames(10)).shape == (TARGET_FRAMES, 34)
    assert normalize_window(_frames(200)).shape == (TARGET_FRAMES, 34)


def test_none_when_too_few_posed_frames():
    frames = [{"keypoints": None}] * 30 + _frames(5)
    assert normalize_window(frames) is None


def test_hip_invalid_frames_are_dropped_from_count():
    bad = _kp()
    bad[11] = (0.0, 0.0)
    bad[12] = (0.0, 0.0)
    frames = [{"keypoints": bad} for _ in range(10)] + _frames(5)
    assert normalize_window(frames) is None  # only 5 usable < 8


def test_shoulder_invalid_frames_excluded_from_scale():
    clean = _frames(8)
    noshoulder = []
    for _ in range(8):
        kp = _kp()
        kp[5] = (0.0, 0.0)
        kp[6] = (0.0, 0.0)
        noshoulder.append({"keypoints": kp})
    mixed = normalize_window(clean + noshoulder).reshape(TARGET_FRAMES, 17, 2)
    baseline = normalize_window(clean).reshape(TARGET_FRAMES, 17, 2)
    # valid-frame torso scale must match the clean baseline (sentinel shoulders
    # must not skew the median); compare the wrist trajectory magnitude
    assert abs(np.linalg.norm(mixed[0, 10]) - np.linalg.norm(baseline[0, 10])) < 1e-5


def test_posed_frames_keeps_only_valid_hip_frames():
    valid = _kp()
    hip_invalid = _kp()
    hip_invalid[11] = (0.0, 0.0)
    hip_invalid[12] = (0.0, 0.0)
    frames = (
        [{"keypoints": None}] * 2
        + [{"keypoints": hip_invalid}] * 3
        + [{"keypoints": valid}] * 4
    )
    result = posed_frames(frames)
    assert len(result) == 4
    for kp in result:
        assert isinstance(kp, np.ndarray)
        assert np.array_equal(kp, valid)
