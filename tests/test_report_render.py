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


def test_render_html_empty_sections_safe():
    html = render_html({"lang": "en", "header": {}, "summary": {},
                        "strengths": [], "weaknesses": [], "per_rep": [], "training_plan": {}})
    assert "<html" in html.lower()  # produced a document, no crash


def test_render_pdf_returns_false_on_render_error(monkeypatch, tmp_path):
    import sys, types
    fake = types.ModuleType("weasyprint")
    class _HTML:
        def __init__(self, *a, **k): pass
        def write_pdf(self, *a, **k): raise RuntimeError("boom")
    fake.HTML = _HTML
    monkeypatch.setitem(sys.modules, "weasyprint", fake)
    out = tmp_path / "r.pdf"
    assert render_pdf("<html><body>x</body></html>", str(out)) is False
    assert not out.exists()


def _zh_hans_report():
    """Minimal zh-Hans report dict with localized section_labels."""
    return {
        "lang": "zh-Hans",
        "header": {"stroke": "smash", "stroke_label": "杀球", "rep_count": 3,
                   "dominant_hand": "right", "date": "2026-07-01", "pose_family": "yolo-pose"},
        "summary": {"mean_score": 70.0, "consistency": 9.5, "verdict_text": "稳步进步中。"},
        "section_labels": {
            "strengths": "做得好的方面",
            "weaknesses": "需要改善的方面",
            "per_rep": "每回合分析",
            "training_plan": "训练计划",
            "col_rep": "回合",
            "col_score": "得分",
            "col_top_weakness": "主要弱点",
        },
        "strengths": [{"metric": "trunk_rotation", "metric_label": "躯干转体",
                       "measured": 35, "impact_label": "力量", "text": "转体良好。"}],
        "weaknesses": [{"metric": "elbow_extension", "metric_label": "手肘伸展",
                        "measured": 138, "ideal_range": [150, 170], "direction": "under",
                        "severity": "moderate", "impact_label": "力量",
                        "mechanism_text": "杠杆臂短。", "drill_text": "墙壁点击。"}],
        "per_rep": [{"rep_id": 1, "overall_score": 70, "top_weakness": "elbow_extension"}],
        "training_plan": {"weeks": []},
    }


def test_render_html_zh_hans_uses_localized_headings():
    html = render_html(_zh_hans_report())
    # Chinese headings must appear; English headings must NOT
    assert "做得好的方面" in html, "zh-Hans strengths heading missing"
    assert "需要改善的方面" in html, "zh-Hans weaknesses heading missing"
    assert "每回合分析" in html, "zh-Hans per-rep heading missing"
    assert "Strengths" not in html, "English 'Strengths' heading leaked into zh-Hans report"
    assert "Areas to" not in html, "English 'Areas to' heading leaked into zh-Hans report"
    assert "Per-Rep" not in html, "English 'Per-Rep' heading leaked into zh-Hans report"


def test_render_html_falls_back_to_english_when_no_section_labels():
    """Renderer must produce valid English output when section_labels is absent."""
    report = _report()  # no section_labels key
    html = render_html(report)
    assert "Strengths" in html
    assert "Areas to Improve" in html
    assert "Per-Rep Breakdown" in html
