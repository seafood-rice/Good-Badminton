from badminton_analysis.analysis.reference_ranges import REFERENCE_RANGES
from badminton_analysis.analysis.reference_ranges_3d import REFERENCE_RANGES_3D


def test_same_strokes_and_metrics_as_2d():
    assert set(REFERENCE_RANGES_3D) == set(REFERENCE_RANGES)
    for stroke in REFERENCE_RANGES:
        assert set(REFERENCE_RANGES_3D[stroke]) == set(REFERENCE_RANGES[stroke])


def test_wrist_and_weight_transfer_reuse_2d_values():
    for stroke in REFERENCE_RANGES:
        for metric in ("wrist_flexion", "weight_transfer"):
            assert REFERENCE_RANGES_3D[stroke][metric] == REFERENCE_RANGES[stroke][metric]


def test_capable_metric_ranges_are_valid_and_weights_preserved():
    for stroke in REFERENCE_RANGES:
        for metric in ("elbow_extension", "knee_flexion", "trunk_rotation", "hip_shoulder_separation"):
            spec = REFERENCE_RANGES_3D[stroke][metric]
            assert spec["min"] <= spec["ideal"] <= spec["max"]
            assert spec["weight"] == REFERENCE_RANGES[stroke][metric]["weight"]
