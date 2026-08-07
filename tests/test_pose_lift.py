# tests/test_pose_lift.py
import numpy as np
from badminton_analysis.detection.pose_lift import PoseLifter, POSE_LIFT_MIN_POSED


def _posed_frame(n, hip_y=200.0):
    kp = np.full((17, 2), 2.0, dtype=float)  # all "detected" (x,y > 1)
    kp[11] = (95.0, hip_y)   # L hip valid
    kp[12] = (105.0, hip_y)  # R hip valid
    kp[5] = (90.0, 100.0)    # L shoulder
    kp[6] = (110.0, 100.0)   # R shoulder
    return {"frame": n, "keypoints": kp}


class _StubModel:
    """Echoes a deterministic 3D output: appends a z=0 plane to the 2D input."""
    def __call__(self, arr):
        arr = np.asarray(arr, dtype=np.float32)  # (1, M, 17, 2)
        z = np.zeros(arr.shape[:-1] + (1,), dtype=np.float32)
        return np.concatenate([arr, z], axis=-1)  # (1, M, 17, 3)


def test_available_flag_without_model_or_weights():
    assert PoseLifter().available is False
    assert PoseLifter(model=_StubModel()).available is True


def test_lift_returns_none_when_too_few_posed_frames():
    lifter = PoseLifter(model=_StubModel())
    frames = [_posed_frame(i) for i in range(POSE_LIFT_MIN_POSED - 1)]
    frames.append({"frame": 999, "keypoints": None})  # unposed, dropped
    assert lifter.lift(frames, image_size=(200, 400)) is None


def test_lift_shapes_and_frame_alignment():
    lifter = PoseLifter(model=_StubModel())
    src = [_posed_frame(i) for i in range(POSE_LIFT_MIN_POSED)]
    src.insert(3, {"frame": 500, "keypoints": None})  # dropped, not in output
    result = lifter.lift(src, image_size=(200, 400))
    assert result is not None
    kp3d, out_frames = result
    assert kp3d.shape == (POSE_LIFT_MIN_POSED, 17, 3)
    assert out_frames == [f["frame"] for f in src if f["keypoints"] is not None]
    assert kp3d.shape[0] == len(out_frames)
