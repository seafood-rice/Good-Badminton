import numpy as np
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.system import TechniqueAnalysisRunner


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


def _build_track(contact_frame, n=70):
    track = []
    for f in range(n):
        if f <= contact_frame:
            shuttle = (300 - f, 60 + f)        # descending toward player
        else:
            d = f - contact_frame
            shuttle = (300 - contact_frame + d, 60 + contact_frame - d * 3)  # rebounds up/away
        racket = shuttle if f == contact_frame else (2000, 2000)
        track.append({"frame": f, "racket_head": racket, "shuttle": shuttle})
    return track


def test_runner_produces_one_report_per_contact():
    contact_frame = 30
    track = _build_track(contact_frame)
    kp = _smash_kp()

    def frame_lookup(idx):
        return {
            "frame": idx,
            "keypoints": kp if idx == contact_frame else None,
            "conf": None,
            "racket_head": (220, 122) if idx == contact_frame else None,
            "centroid": (100 + idx, 300),
            "nose": (100, 80),
            "shoulder": (100, 120),
            "hip": (100, 250),
            "elbow_angle": 160.0,
        }

    runner = TechniqueAnalysisRunner(BiomechanicalAnalyzer(dominant="right"))
    reports, events = runner.run(track, frame_lookup)
    assert len(reports) == 1
    assert len(events) == 1
    assert reports[0]["stroke_type"] in {"high_clear", "smash", "drop_shot", "serve"}
    assert "overall_score" in reports[0]
