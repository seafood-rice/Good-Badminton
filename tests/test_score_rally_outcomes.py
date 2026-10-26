"""Accuracy script for the automatic rally-outcome labeller (scripts/score_rally_outcomes.py)."""
import importlib.util
from pathlib import Path

from badminton_analysis.outcomes import labels as lab
from tests.outcome_fixtures import make_run

_spec = importlib.util.spec_from_file_location(
    "score_rally_outcomes", Path(__file__).resolve().parents[1] / "scripts" / "score_rally_outcomes.py")
score_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score_mod)


def test_precision_and_coverage(tmp_path):
    # Rally 1: auto claims lower (landing in the upper half + lower serves next); rally 2: abstains.
    out = make_run(tmp_path / "vid", [(1, 10.0, 15.0), (2, 20.0, 25.0)],
                   hits=[(12.0, "upper"), (14.5, "lower"), (20.2, "lower"), (22.0, "upper")],
                   landings=[(15.0, 3.0, 3.0)])
    labels_dir = tmp_path / "labels"
    lab.save(lab.labels_path(labels_dir, "vid"), {"schema": 1, "revision": 1, "labels": lab.validate_labels([
        {"start_sec": 10, "end_sec": 15, "verdict": "lower"},
        {"start_sec": 20, "end_sec": 25, "verdict": "upper"},
        {"start_sec": 30, "end_sec": 31, "verdict": "unsure"},
    ])})
    result = score_mod.score([out], labels_dir)
    assert result["owner_points"] == 2 and result["auto_claims"] == 1 and result["correct"] == 1
    assert result["precision"] == 1.0 and result["coverage"] == 0.5
    assert result["per_evidence"]["landing"] == {"named": 1, "correct": 1, "accuracy": 1.0}
    assert result["per_evidence"]["last_hitter"]["named"] == 2
    assert result["per_video"][0]["stem"] == "vid"


def test_no_labels_gives_null_ratios(tmp_path):
    out = make_run(tmp_path / "vid", [(1, 1.0, 5.0)])
    result = score_mod.score([out], tmp_path / "labels")
    assert result["owner_points"] == 0 and result["precision"] is None and result["coverage"] is None
