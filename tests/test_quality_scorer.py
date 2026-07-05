import numpy as np

from badminton_analysis.posture.writer import build_drill_summary
from badminton_analysis.quality.scorer import QualityScorer, to_display_score


class _FakeModel:
    def __init__(self, value):
        self.value = value

    def __call__(self, x):
        import torch
        return torch.tensor([[self.value]], dtype=torch.float32)


def _frames(n=30):
    kp = np.zeros((17, 2), dtype=float)
    kp[5] = (90.0, 40.0)
    kp[6] = (110.0, 40.0)
    kp[11] = (92.0, 100.0)
    kp[12] = (108.0, 100.0)
    return [{"keypoints": kp} for _ in range(n)]


def test_display_mapping():
    assert to_display_score(1.0) == 0.0
    assert to_display_score(7.0) == 100.0
    assert to_display_score(4.0) == 50.0
    assert to_display_score(9.0) == 100.0   # clamped
    assert to_display_score(0.0) == 0.0     # clamped


def test_score_with_fake_model():
    s = QualityScorer(model_path=None, model=_FakeModel(4.0))
    assert s.available
    assert s.score(_frames()) == 50.0


def test_score_none_on_unnormalizable_window():
    s = QualityScorer(model_path=None, model=_FakeModel(4.0))
    assert s.score([{"keypoints": None}] * 30) is None


def test_score_none_on_model_failure():
    class _Boom:
        def __call__(self, x):
            raise RuntimeError("bad weights")
    s = QualityScorer(model_path=None, model=_Boom())
    assert s.score(_frames()) is None


def test_unavailable_without_model_or_path():
    s = QualityScorer(model_path=None)
    assert not s.available
    assert s.score(_frames()) is None


def test_missing_weights_file_unavailable(tmp_path):
    s = QualityScorer(model_path=str(tmp_path / "nope.pt"))
    assert not s.available


def test_summary_mean_ai_score():
    reports = [
        {"rep_id": 1, "stroke_type": "high_clear", "overall_score": 50,
         "per_metric": {}, "weaknesses": [], "strengths": [], "ai_score": 40.0},
        {"rep_id": 2, "stroke_type": "high_clear", "overall_score": 60,
         "per_metric": {}, "weaknesses": [], "strengths": [], "ai_score": 60.0},
    ]
    summary = build_drill_summary(reports, "high_clear")
    assert summary["mean_ai_score"] == 50.0


def test_summary_omits_mean_ai_score_when_absent():
    reports = [{"rep_id": 1, "stroke_type": "high_clear", "overall_score": 50,
                "per_metric": {}, "weaknesses": [], "strengths": []}]
    summary = build_drill_summary(reports, "high_clear")
    assert "mean_ai_score" not in summary
