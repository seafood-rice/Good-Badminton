import numpy as np
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.stroke.events import StrokeEvent


def _good_smash_keypoints():
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, 122)   # near-straight arm -> ~160-ish elbow
    kp[ja.L_SHOULDER] = (60, 90)  # shoulder line tilted -> trunk_rotation > 0
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def _window_frames(contact_frame, kp, racket_head, n_pre=20, n_post=15):
    frames = []
    start = contact_frame - n_pre
    for f in range(start, contact_frame + n_post + 1):
        frames.append({
            "frame": f,
            "keypoints": kp if f == contact_frame else None,
            "conf": None,
            "racket_head": racket_head if f == contact_frame else None,
            "centroid": (100 + (f - start), 300),
        })
    return frames


def test_analyze_produces_report_with_scores():
    analyzer = BiomechanicalAnalyzer(dominant="right")
    ev = StrokeEvent("smash", 30, 10, 45, "upper", 0.8)
    frames = _window_frames(30, _good_smash_keypoints(), racket_head=(220, 122))
    report = analyzer.analyze(ev, frames)
    assert report["stroke_type"] == "smash"
    assert report["player_side"] == "upper"
    assert report["overall_score"] is not None
    assert "elbow_extension" in report["per_metric"]


def test_weaknesses_carry_descriptions():
    analyzer = BiomechanicalAnalyzer(dominant="right")
    ev = StrokeEvent("smash", 30, 10, 45, "upper", 0.8)
    # bent arm -> low elbow extension -> weakness with a description string
    kp = _good_smash_keypoints()
    kp[ja.R_WRIST] = (120, 170)  # sharply bent elbow
    frames = _window_frames(30, kp, racket_head=(150, 200))
    report = analyzer.analyze(ev, frames)
    weak_metrics = [w["metric"] for w in report["weaknesses"]]
    assert "elbow_extension" in weak_metrics
    for w in report["weaknesses"]:
        assert isinstance(w["description"], str) and w["description"]


def test_missing_contact_keypoints_gives_none_scores():
    analyzer = BiomechanicalAnalyzer(dominant="right")
    ev = StrokeEvent("high_clear", 30, 10, 45, "lower", 0.5)
    frames = _window_frames(30, np.ones((17, 2)), racket_head=None)  # all missing
    report = analyzer.analyze(ev, frames)
    assert report["overall_score"] is None
