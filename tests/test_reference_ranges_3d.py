from badminton_analysis.analysis.joint_angles import METRICS_3D_CAPABLE
from badminton_analysis.analysis.reference_ranges import REFERENCE_RANGES
from badminton_analysis.analysis.reference_ranges_3d import REFERENCE_RANGES_3D, _3D_OVERRIDES


def test_same_strokes_and_metrics_as_2d():
    assert set(REFERENCE_RANGES_3D) == set(REFERENCE_RANGES)
    for stroke in REFERENCE_RANGES:
        assert set(REFERENCE_RANGES_3D[stroke]) == set(REFERENCE_RANGES[stroke])


def test_non_capable_metrics_reuse_2d_values():
    # trunk_rotation is NOT 3D-capable: the 2D metric measures the shoulder
    # line's tilt from the image horizontal, so it must reuse the 2D range table
    # verbatim just like wrist_flexion and weight_transfer.
    for stroke in REFERENCE_RANGES:
        for metric in ("wrist_flexion", "weight_transfer", "trunk_rotation"):
            assert REFERENCE_RANGES_3D[stroke][metric] == REFERENCE_RANGES[stroke][metric]


def test_only_capable_metrics_are_overridden():
    assert set(_3D_OVERRIDES) == set(REFERENCE_RANGES)
    for stroke in REFERENCE_RANGES:
        assert set(_3D_OVERRIDES[stroke]) == set(METRICS_3D_CAPABLE)


def test_capable_metric_ranges_are_valid_and_weights_preserved():
    for stroke in REFERENCE_RANGES:
        for metric in METRICS_3D_CAPABLE:
            spec = REFERENCE_RANGES_3D[stroke][metric]
            assert spec["min"] <= spec["ideal"] <= spec["max"]
            assert spec["weight"] == REFERENCE_RANGES[stroke][metric]["weight"]
