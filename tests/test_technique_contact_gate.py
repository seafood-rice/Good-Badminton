"""The technique path's contact radius must follow perspective.

A radius in absolute pixels is wrong across a perspective gradient. On the
project's fixed-camera footage the scale runs 122 px/m at the far baseline to
625 px/m at the near one, so the inherited 80 px means 0.13 m near and 0.65 m
far. The metre-based gate has existed since the B11 work but the technique
runner was still calling detect_contacts with the fixed pixel default.
"""
import pytest

from badminton_analysis.system import TechniqueAnalysisRunner

QUAD = [(1536, 1229), (2287, 1254), (3823, 2103), (9, 2031)]


def test_a_court_quad_produces_a_scale():
    runner = TechniqueAnalysisRunner(analyzer=None, court_corners=QUAD)
    assert runner.scale is not None
    # Far baseline is coarse, near baseline fine -- the whole point of the gate.
    assert runner.scale.px_per_m(1240) < runner.scale.px_per_m(2067)


def test_no_court_quad_keeps_the_fixed_pixel_behaviour():
    assert TechniqueAnalysisRunner(analyzer=None).scale is None
    assert TechniqueAnalysisRunner(analyzer=None, court_corners=None).scale is None


def test_a_degenerate_quad_falls_back_rather_than_raising():
    """A bad annotation must not take the whole technique pass down."""
    flat = [(0, 100), (100, 100), (100, 100), (0, 100)]
    assert TechniqueAnalysisRunner(analyzer=None, court_corners=flat).scale is None


def test_the_scale_reaches_detect_contacts(monkeypatch):
    import badminton_analysis.stroke.events as events

    seen = {}
    real = events.detect_contacts

    def spy(track, **kwargs):
        seen.update(kwargs)
        return real(track, **kwargs)

    monkeypatch.setattr(events, "detect_contacts", spy)
    runner = TechniqueAnalysisRunner(analyzer=None, court_corners=QUAD)
    runner.run([], lambda idx: None)

    assert seen.get("scale") is not None
    assert seen.get("contact_m") == pytest.approx(events.CONTACT_M)


def test_without_a_quad_no_metre_radius_is_passed(monkeypatch):
    import badminton_analysis.stroke.events as events

    seen = {}
    real = events.detect_contacts

    def spy(track, **kwargs):
        seen.update(kwargs)
        return real(track, **kwargs)

    monkeypatch.setattr(events, "detect_contacts", spy)
    TechniqueAnalysisRunner(analyzer=None).run([], lambda idx: None)

    assert seen.get("contact_m") is None
    assert seen.get("scale") is None
