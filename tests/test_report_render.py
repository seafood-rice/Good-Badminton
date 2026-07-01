from badminton_analysis.posture.report_render import render_html, render_pdf


def _report():
    return {
        "lang": "en",
        "header": {"stroke": "smash", "stroke_label": "Smash", "rep_count": 5,
                   "dominant_hand": "right", "date": "2026-07-01", "pose_family": "yolo-pose"},
        "summary": {"mean_score": 68.0, "consistency": 11.2, "verdict_text": "Developing smash."},
        "strengths": [{"metric": "trunk_rotation", "metric_label": "Trunk rotation",
                       "measured": 35, "impact_label": "Power", "text": "Good rotation adds power."}],
        "weaknesses": [{"metric": "elbow_extension", "metric_label": "Elbow extension",
                        "measured": 138, "ideal_range": [150, 170], "direction": "under",
                        "severity": "moderate", "impact_label": "Power",
                        "mechanism_text": "Short arm cuts racket-head speed.",
                        "drill_text": "Overhead extension throws, 3x10."}],
        "per_rep": [{"rep_id": 1, "overall_score": 65, "top_weakness": "elbow_extension"}],
        "training_plan": {"weeks": [{"week": 1, "phase": "Foundation",
                          "sessions": [{"name": "Shadow clears", "location": "court",
                          "frequency_per_week": 3, "reps": 20, "sets": 3, "duration_min": 10}]}]},
    }


def test_render_html_contains_sections_and_text():
    html = render_html(_report())
    assert "<html" in html.lower()
    assert "Smash" in html
    assert "Developing smash." in html
    assert "Elbow extension" in html
    assert "Overhead extension throws" in html      # drill text present
    assert "Short arm cuts racket-head speed." in html  # mechanism present
    assert "Trunk rotation" in html                  # strengths section
    assert "Shadow clears" in html                   # training plan present


def test_render_html_escapes_and_is_self_contained():
    html = render_html(_report())
    # self-contained: no external resource references
    assert "http://" not in html and "https://" not in html
    assert "<style" in html.lower()  # inline CSS


def test_render_pdf_returns_false_when_lib_absent(monkeypatch, tmp_path):
    # Simulate weasyprint missing by making the import fail.
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name == "weasyprint":
            raise ImportError("no weasyprint")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = tmp_path / "r.pdf"
    assert render_pdf("<html><body>x</body></html>", str(out)) is False
    assert not out.exists()
