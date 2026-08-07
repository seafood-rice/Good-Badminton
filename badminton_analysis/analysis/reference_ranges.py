"""Sport-science reference ranges per stroke. Values are indicative first-release
starting points (image-plane 2D angles, degrees; weight_transfer is a ratio of
shoulder widths) and are expected to be tuned against literature/labeled clips."""

REFERENCE_RANGES = {
    "high_clear": {
        "elbow_extension":         {"min": 140, "max": 160, "ideal": 150, "weight": 0.25},
        "trunk_rotation":          {"min": 20,  "max": 40,  "ideal": 30,  "weight": 0.20},
        "wrist_flexion":           {"min": 80,  "max": 100, "ideal": 90,  "weight": 0.20},
        # Interior hip-knee-ankle angle (not a "flexion amount"); ~156 deg is a
        # moderate athletic bend at contact, falling off toward a locked-straight
        # leg (>172) or a deep bend (<140). First-pass estimate calibrated to
        # observed rep contacts (IMG_1270 medians 157-166, IMG_9691 medians
        # 154-174), tunable with labeled data (see this file's module docstring).
        "knee_flexion":            {"min": 140, "max": 172, "ideal": 156, "weight": 0.15},
        "hip_shoulder_separation": {"min": 15,  "max": 35,  "ideal": 25,  "weight": 0.10},
        "weight_transfer":         {"min": 0.10, "max": 1.50, "ideal": 0.35, "weight": 0.10},
    },
    "smash": {
        "elbow_extension":         {"min": 150, "max": 170, "ideal": 160, "weight": 0.25},
        "trunk_rotation":          {"min": 30,  "max": 50,  "ideal": 40,  "weight": 0.20},
        "wrist_flexion":           {"min": 100, "max": 120, "ideal": 110, "weight": 0.20},
        # See high_clear's knee_flexion comment: interior hip-knee-ankle angle,
        # same corrected range applied to all four stroke types.
        "knee_flexion":            {"min": 140, "max": 172, "ideal": 156, "weight": 0.15},
        "hip_shoulder_separation": {"min": 20,  "max": 45,  "ideal": 32,  "weight": 0.10},
        "weight_transfer":         {"min": 0.20, "max": 1.80, "ideal": 0.50, "weight": 0.10},
    },
    "drop_shot": {
        "elbow_extension":         {"min": 120, "max": 140, "ideal": 130, "weight": 0.25},
        "trunk_rotation":          {"min": 15,  "max": 30,  "ideal": 22,  "weight": 0.20},
        "wrist_flexion":           {"min": 90,  "max": 110, "ideal": 100, "weight": 0.20},
        # See high_clear's knee_flexion comment: interior hip-knee-ankle angle,
        # same corrected range applied to all four stroke types.
        "knee_flexion":            {"min": 140, "max": 172, "ideal": 156, "weight": 0.15},
        "hip_shoulder_separation": {"min": 10,  "max": 30,  "ideal": 20,  "weight": 0.10},
        "weight_transfer":         {"min": 0.05, "max": 1.00, "ideal": 0.25, "weight": 0.10},
    },
    "serve": {
        "elbow_extension":         {"min": 130, "max": 150, "ideal": 140, "weight": 0.25},
        "trunk_rotation":          {"min": 10,  "max": 25,  "ideal": 17,  "weight": 0.20},
        "wrist_flexion":           {"min": 70,  "max": 90,  "ideal": 80,  "weight": 0.20},
        # See high_clear's knee_flexion comment: interior hip-knee-ankle angle,
        # same corrected range applied to all four stroke types.
        "knee_flexion":            {"min": 140, "max": 172, "ideal": 156, "weight": 0.15},
        "hip_shoulder_separation": {"min": 5,   "max": 20,  "ideal": 12,  "weight": 0.10},
        "weight_transfer":         {"min": 0.02, "max": 0.80, "ideal": 0.15, "weight": 0.10},
    },
}
