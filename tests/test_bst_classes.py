"""Tests for the 25->6 coarse stroke-class mapping and confidence gate.

See third_party/bst/CONTRACT.md (CLASS_NAMES) and
badminton_analysis/stroke_recog/classes.py for the mapping this exercises.
"""

import numpy as np

from badminton_analysis.stroke_recog.classes import COARSE, FINE_TO_COARSE, CLASS_NAMES, to_coarse

UNKNOWN = "未知球種"

# stroke -> expected coarse bucket, per the controller-resolved mapping.
_STROKE_TO_COARSE = {
    "放小球": "net",
    "擋小球": "net",
    "殺球": "smash",
    "挑球": "clear",
    "長球": "clear",
    "平球": "drive",
    "切球": "drop",
    "推球": "drive",
    "撲球": "net",
    "勾球": "net",
    "發短球": "serve",
    "發長球": "serve",
}


def test_every_fine_class_maps_to_one_coarse():
    assert set(FINE_TO_COARSE) == set(CLASS_NAMES)
    assert all(v in COARSE or v == "uncertain" for v in FINE_TO_COARSE.values())


def test_unknown_class_maps_to_uncertain():
    assert FINE_TO_COARSE[UNKNOWN] == "uncertain"


def test_bucket_membership_counts():
    counts = {label: 0 for label in COARSE}
    for name in CLASS_NAMES:
        if name == UNKNOWN:
            continue
        stroke = name.split("_", 1)[1]
        expected = _STROKE_TO_COARSE[stroke]
        assert FINE_TO_COARSE[name] == expected
        counts[expected] += 1

    assert counts["net"] == 8
    assert counts["smash"] == 2
    assert counts["clear"] == 4
    assert counts["drive"] == 4
    assert counts["drop"] == 2
    assert counts["serve"] == 4


def test_to_coarse_sums_bucket_probs_and_argmaxes():
    # two fine classes in the 'smash' bucket dominate -> 'smash'
    logits = np.full(len(CLASS_NAMES), -10.0)
    smash_idx = [i for i, c in enumerate(CLASS_NAMES) if FINE_TO_COARSE[c] == "smash"]
    for i in smash_idx:
        logits[i] = 5.0
    label, conf = to_coarse(logits, min_conf=0.5)
    assert label == "smash"
    assert conf > 0.5


def test_low_confidence_is_uncertain():
    logits = np.zeros(len(CLASS_NAMES))  # uniform -> max bucket prob well under a high gate
    label, conf = to_coarse(logits, min_conf=0.99)
    assert label == "uncertain"


def test_unknown_class_dominant_is_uncertain_regardless_of_conf():
    logits = np.full(len(CLASS_NAMES), -10.0)
    logits[CLASS_NAMES.index(UNKNOWN)] = 10.0
    label, conf = to_coarse(logits, min_conf=0.0)
    assert label == "uncertain"
