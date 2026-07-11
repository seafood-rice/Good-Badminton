"""Smoke test for the vendored BST_CG_AP stroke-type model wrapper.

Gated on the real ShuttleSet-25-merged checkpoint (`weights/bst-shuttleset.pt`)
existing on disk, since it is a git-ignored, separately-fetched binary (see
`third_party/bst/CONTRACT.md`). Skips cleanly (rather than failing) when the
weights are absent so this stays hermetic in CI / on a fresh checkout.
"""

import os

import numpy as np
import pytest

from badminton_analysis.stroke_recog import bst_model

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS_PATH = os.path.join(PROJECT_ROOT, "weights", "bst-shuttleset.pt")


def test_predict_shape():
    if not os.path.exists(WEIGHTS_PATH):
        pytest.skip(f"weights not found at {WEIGHTS_PATH}; see third_party/bst/CONTRACT.md")

    pose = np.zeros((bst_model.SEQ_LEN, bst_model.N_PEOPLE, bst_model.POSE_IN_DIM), dtype=np.float32)
    shuttle = np.zeros((bst_model.SEQ_LEN, 2), dtype=np.float32)
    positions = np.zeros((bst_model.SEQ_LEN, bst_model.N_PEOPLE, 2), dtype=np.float32)

    model = bst_model.load_bst(WEIGHTS_PATH)
    logits = bst_model.predict(model, pose, shuttle, positions)

    assert isinstance(logits, np.ndarray)
    assert logits.shape == (bst_model.N_CLASSES,)
    assert np.all(np.isfinite(logits))
