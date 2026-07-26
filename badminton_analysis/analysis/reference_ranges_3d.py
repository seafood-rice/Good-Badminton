"""3D anatomical reference ranges per stroke (degrees).

Indicative first-release starting points for MotionBERT-lifted H36M-17 angles,
expected to be tuned against biomechanics literature and labeled clips (same
posture as reference_ranges.py).

The lifted pose is MotionBERT's perspective-projected 2.5D image-space output,
so these angles are *less* view-dependent than the 2D image-plane ones but are
not fully view-invariant; see badminton_analysis/detection/pose_lift.py's module
docstring for the confirmed contract and docs/motionbert-weights.md for the
validation checklist before reading any absolute 3D score quantitatively.

wrist_flexion, weight_transfer and trunk_rotation stay in the 2D image-plane
space and reuse the 2D table verbatim; only the three metrics in
joint_angles.METRICS_3D_CAPABLE are overridden. Per-metric weights are preserved
so each stroke's weights still sum to the same total as the 2D table.
"""
from .reference_ranges import REFERENCE_RANGES

# 3D min/max/ideal for the three 3D-capable metrics; weights are copied from the
# 2D table by _merge below, so they are intentionally omitted here.
#
# trunk_rotation is intentionally absent: it is not 3D-capable (the 2D metric is
# the shoulder line's tilt from the image horizontal, a different quantity from
# anything 3D offers under that name, and the 3D candidate duplicated
# hip_shoulder_separation). It therefore falls through to the 2D range table
# verbatim, exactly like wrist_flexion and weight_transfer. See
# joint_angles.METRICS_3D_CAPABLE.
_3D_OVERRIDES = {
    "high_clear": {
        "elbow_extension":         {"min": 150, "max": 175, "ideal": 165},
        "knee_flexion":            {"min": 145, "max": 175, "ideal": 162},
        "hip_shoulder_separation": {"min": 20,  "max": 50,  "ideal": 35},
    },
    "smash": {
        "elbow_extension":         {"min": 155, "max": 178, "ideal": 170},
        "knee_flexion":            {"min": 140, "max": 172, "ideal": 158},
        "hip_shoulder_separation": {"min": 25,  "max": 55,  "ideal": 40},
    },
    "drop_shot": {
        "elbow_extension":         {"min": 135, "max": 165, "ideal": 150},
        "knee_flexion":            {"min": 148, "max": 176, "ideal": 163},
        "hip_shoulder_separation": {"min": 15,  "max": 45,  "ideal": 28},
    },
    "serve": {
        "elbow_extension":         {"min": 140, "max": 168, "ideal": 155},
        "knee_flexion":            {"min": 150, "max": 178, "ideal": 166},
        "hip_shoulder_separation": {"min": 10,  "max": 35,  "ideal": 20},
    },
}


def _merge(base_stroke, override_stroke):
    merged = {name: dict(spec) for name, spec in base_stroke.items()}
    for name, ov in override_stroke.items():
        spec = dict(merged[name])           # keep 2D weight
        spec["min"] = ov["min"]
        spec["max"] = ov["max"]
        spec["ideal"] = ov["ideal"]
        merged[name] = spec
    return merged


REFERENCE_RANGES_3D = {
    stroke: _merge(REFERENCE_RANGES[stroke], _3D_OVERRIDES[stroke])
    for stroke in REFERENCE_RANGES
}
