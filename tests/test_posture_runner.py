import numpy as np
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.posture.system import PostureRunner


def _smash_kp():
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, 122)
    kp[ja.L_SHOULDER] = (60, 90)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def _track_with_two_swings(n=160):
    track = []
    for i in range(n):
        wrist = (100, 100)
        if i == 40 or i == 110:
            wrist = (260, 100)  # big displacement -> speed spike
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
    return track


def test_runner_one_report_per_rep_with_rep_id():
    kp = _smash_kp()
    racket = ja.infer_racket_head(kp, dominant="right")

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": racket, "centroid": (100 + (idx % 5), 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="high_clear", dominant="right")
    reports, reps = runner.run(_track_with_two_swings(), frame_lookup, fps=30)
    assert len(reps) == 2
    assert len(reports) == 2
    assert reports[0]["rep_id"] == 1 and reports[1]["rep_id"] == 2
    assert reports[0]["stroke_type"] == "high_clear"
    assert reports[0]["player_side"] == "single"
    assert "overall_score" in reports[0]


def test_runner_empty_track_no_reports():
    runner = PostureRunner(BiomechanicalAnalyzer(), stroke_type="smash")
    reports, reps = runner.run([], lambda i: None, fps=30)
    assert reports == [] and reps == []
