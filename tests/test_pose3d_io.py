import numpy as np
from badminton_analysis.analysis.pose3d_io import write_reps_3d, read_reps_3d


def test_round_trip(tmp_path):
    reps = [
        {"rep_id": 1, "frames": [10, 11, 12], "keypoints_3d": np.arange(3 * 17 * 3).reshape(3, 17, 3).astype(float)},
        {"rep_id": 2, "frames": [20, 21], "keypoints_3d": np.ones((2, 17, 3), dtype=float)},
    ]
    meta = {"model": "motionbert", "joint_format": "h36m-17", "normalization": "screen"}
    path = tmp_path / "drill_reps_3d.npz"
    write_reps_3d(str(path), reps, meta)

    out_reps, out_meta = read_reps_3d(str(path))
    assert out_meta == meta
    assert [r["rep_id"] for r in out_reps] == [1, 2]
    assert out_reps[0]["frames"] == [10, 11, 12]
    np.testing.assert_allclose(out_reps[0]["keypoints_3d"], reps[0]["keypoints_3d"])
    assert out_reps[1]["keypoints_3d"].shape == (2, 17, 3)
