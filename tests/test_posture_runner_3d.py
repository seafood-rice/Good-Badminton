import numpy as np
from badminton_analysis.posture.system import PostureRunner
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer


class _FakeLifter:
    available = True

    def lift(self, window_frames, image_size):
        frames = [int(f["frame"]) for f in window_frames if f.get("keypoints") is not None]
        if not frames:
            return None
        kp3d = np.zeros((len(frames), 17, 3), dtype=float)
        # straight R arm (elbow ~180) so 3D angles are well-defined
        kp3d[:, 14], kp3d[:, 15], kp3d[:, 16] = (0, 0, 0), (1, 0, 0), (2, 0, 0)
        kp3d[:, 1], kp3d[:, 2], kp3d[:, 3] = (0, 1, 0), (0, 0, 0), (1, 0, 0)
        kp3d[:, 11], kp3d[:, 14] = (-1, 0, 0), (1, 0, 0)
        kp3d[:, 4], kp3d[:, 1] = (-1, 0, 0), (1, 0, 0)
        return kp3d, frames


def _track_and_frames():
    # A single clear peak in the wrist-y trajectory so segment_reps yields one rep.
    fps = 30.0
    track = []
    frames = {}
    for i in range(60):
        wy = 200.0 - (80.0 if i == 30 else 0.0) - max(0.0, 40.0 - abs(i - 30) * 4)
        wrist = (100.0, wy)
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
        kp = np.full((17, 2), 50.0, dtype=float)
        kp[9] = kp[10] = wrist            # wrists
        kp[11], kp[12] = (95.0, 200.0), (105.0, 200.0)
        frames[i] = {"frame": i, "keypoints": kp, "conf": None,
                     "racket_head": None, "centroid": (100.0, 200.0),
                     "racket_head_detected": False}
    return track, frames, fps


def test_runner_attaches_3d_and_counts():
    track, frames, fps = _track_and_frames()
    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="smash", dominant="right",
                           pose_lifter=_FakeLifter(), image_size=(200, 400))
    reports, reps, gate = runner.run(track, frames.get, fps)
    assert len(reports) >= 1
    assert reports[0]["feature_space"] == "3d"
    assert gate["scored_3d"] >= 1
    assert len(gate["reps_3d"]) >= 1


def test_runner_without_lifter_is_2d():
    track, frames, fps = _track_and_frames()
    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="smash", dominant="right")
    reports, reps, gate = runner.run(track, frames.get, fps)
    assert reports[0]["feature_space"] == "2d"
    assert gate["scored_3d"] == 0
    assert gate["reps_3d"] == []
