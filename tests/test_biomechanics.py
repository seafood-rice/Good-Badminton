import numpy as np
import pytest
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


def _window_frames(contact_frame, kp, racket_head, n_pre=20, n_post=15, detected=True):
    frames = []
    start = contact_frame - n_pre
    for f in range(start, contact_frame + n_post + 1):
        frames.append({
            "frame": f,
            "keypoints": kp if f == contact_frame else None,
            "conf": None,
            "racket_head": racket_head if f == contact_frame else None,
            "centroid": (100 + (f - start), 300),
            "racket_head_detected": detected if f == contact_frame else False,
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
    # racket_head_detected=True (the _window_frames default) -> wrist_flexion
    # is measured and contributes to the weighted average.
    assert report["per_metric"]["wrist_flexion"]["measured"] is not None


def test_wrist_flexion_none_when_racket_head_inferred_not_detected():
    """When the racket head came from the kinematic fallback (not a real
    detection), racket_head_detected is False and wrist_flexion must be
    None (unmeasurable) - not scored as 0 for the ~180 deg collinear angle
    the fallback produces. Compares the renormalized overall_score against
    the old score-it-as-0 behavior for an otherwise-identical rep.
    """
    analyzer = BiomechanicalAnalyzer(dominant="right")
    ev = StrokeEvent("high_clear", 30, 10, 45, "single", 0.8)
    kp = _good_smash_keypoints()
    inferred_head = ja.infer_racket_head(kp, dominant="right")  # collinear, ~180 deg

    frames = _window_frames(30, kp, racket_head=inferred_head, detected=False)
    report = analyzer.analyze(ev, frames)

    assert report["per_metric"]["wrist_flexion"]["measured"] is None
    assert report["per_metric"]["wrist_flexion"]["score"] is None

    # Old (pre-fix) behavior: the inferred head was fed to scoring
    # unconditionally, so wrist_flexion measured the ~180 deg collinear
    # angle and was scored as if detected (near 0, far outside 80-100).
    from badminton_analysis.analysis.scoring import score_stroke
    old_metrics = {name: entry["measured"] for name, entry in report["per_metric"].items()}
    old_metrics["wrist_flexion"] = ja.angle_at(kp[ja.R_ELBOW], kp[ja.R_WRIST], inferred_head)
    old_scored = score_stroke(old_metrics, "high_clear")

    assert old_scored["per_metric"]["wrist_flexion"]["score"] == pytest.approx(0.0, abs=1e-6)
    assert report["overall_score"] > old_scored["overall"]


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
