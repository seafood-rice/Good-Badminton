# tests/test_tracknet_infer.py
import numpy as np
import torch

from badminton_analysis.shuttle_track.tracknet import vendored_imports


def test_predict_scales_heatmap_center_to_original_pixels():
    with vendored_imports():
        from infer import predict

        # One sample, seq_len=2. Frame 0 heatmap has a hot 4x4 block centered at
        # model-space (100, 50); frame 1 is empty -> invisible.
        H, W = 288, 512
        y_pred = torch.zeros((1, 2, H, W), dtype=torch.float32)
        y_pred[0, 0, 48:52, 98:102] = 1.0
        indices = torch.tensor([[[0, 0], [0, 1]]], dtype=torch.float32)  # (N, L, 2), col 1 = frame id

        out = predict(indices, y_pred=y_pred, img_scaler=(2.0, 2.0))  # 2x upscale to original

        assert out["Frame"] == [0, 1]
        # Frame 0: center ~ (100, 50) in model space -> ~ (200, 100) original.
        assert abs(out["X"][0] - 200) <= 4 and abs(out["Y"][0] - 100) <= 4
        assert out["Visibility"][0] == 1
        # Frame 1: no response -> (0, 0), invisible.
        assert out["X"][1] == 0 and out["Y"][1] == 0 and out["Visibility"][1] == 0


def test_get_ensemble_weight_is_symmetric_and_normalized():
    with vendored_imports():
        from infer import get_ensemble_weight

        w = get_ensemble_weight(8, "weight")
        assert abs(float(w.sum()) - 1.0) < 1e-6
        assert torch.allclose(w, w.flip(0))


def test_generate_inpaint_mask_marks_interior_gap():
    with vendored_imports():
        from infer import generate_inpaint_mask

        # visible, gap, visible -> the gap (indices 2..3) gets masked (y above th_h).
        pred = {"Frame": list(range(6)),
                "X": [10, 10, 0, 0, 10, 10],
                "Y": [100, 100, 0, 0, 100, 100],
                "Visibility": [1, 1, 0, 0, 1, 1]}
        mask = generate_inpaint_mask(pred, th_h=30)
        assert mask[2] == 1 and mask[3] == 1
        assert mask[0] == 0 and mask[5] == 0
