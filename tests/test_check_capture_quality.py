"""Capture-quality gate: geometry derivation, quad parsing, and threshold provenance."""
import importlib.util
import pathlib

import numpy as np
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_capture_quality",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_capture_quality.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)

# The real DJI quad, whose footage is the known-BAD reference (design doc 0.24).
DJI_QUAD = [(1724, 1122), (2372, 1135), (3827, 2095), (145, 2005)]


def test_dji_geometry_reproduces_the_measured_span():
    geo = mod.measure_geometry(DJI_QUAD)
    assert geo["px_per_m_far"] == pytest.approx(106.3, abs=0.5)
    assert geo["px_per_m_near"] == pytest.approx(603.8, abs=0.5)
    assert geo["span"] == pytest.approx(5.68, abs=0.05)


def test_dji_fails_on_span_but_not_on_resolution():
    """The known-bad clip must fail SOMETHING, and it matters which.

    Its minimum scale is 106.3 px/m, which CLEARS the 92 px/m floor -- so raw
    resolution is adequate and the failure is perspective span (5.68x against a 2.0x
    limit) plus clutter. That matches section 0.21, where going finer made detection
    worse and refuted resolution as the cause. A capture fix therefore needs a
    different camera POSITION, not a better sensor.
    """
    geo = mod.measure_geometry(DJI_QUAD)
    assert geo["span"] > mod.MAX_SPAN, "span must fail"
    assert geo["min_px_per_m"] >= mod.MIN_PX_PER_M, (
        "resolution is adequate; if this starts failing the spec has drifted")


def test_implied_standoff_matches_the_hand_derivation():
    # (L + d0)/d0 = span  =>  d0 = L/(span-1) = 13.4/4.68 = 2.86 m
    geo = mod.measure_geometry(DJI_QUAD)
    assert geo["implied_standoff_m"] == pytest.approx(2.86, abs=0.05)


def test_min_scale_threshold_is_derived_not_arbitrary():
    """92 px/m comes from a 3 px detector floor at 2x tiling on a 6.5 cm shuttle."""
    assert mod.MIN_PX_PER_M == pytest.approx(
        mod.DETECTOR_FLOOR_PX * mod.PRACTICAL_TILE_SCALE / mod.SHUTTLE_M)
    assert mod.MIN_PX_PER_M == pytest.approx(92.3, abs=0.5)


def test_a_side_on_quad_passes_the_geometry_checks():
    """Side-on framing is the derived fix; the checker must agree it works."""
    # court length (13.4 m) across the frame width at 4K, viewed near-perpendicular:
    # a shallow quad whose two baselines differ little in scale.
    s_far, s_near = 250.0, 320.0
    y_far, y_near = 400.0, 1900.0
    half_far = s_far * mod.COURT_WIDTH_M / 2
    half_near = s_near * mod.COURT_WIDTH_M / 2
    quad = [(1920 - half_far, y_far), (1920 + half_far, y_far),
            (1920 + half_near, y_near), (1920 - half_near, y_near)]
    geo = mod.measure_geometry(quad)
    assert geo["span"] <= mod.MAX_SPAN
    assert geo["min_px_per_m"] >= mod.MIN_PX_PER_M


def test_parse_quad_from_inline_points():
    quad = mod.parse_quad("10,20 30,20 40,50 5,50")
    assert quad == [(10.0, 20.0), (30.0, 20.0), (40.0, 50.0), (5.0, 50.0)]


def test_parse_quad_from_annotations_file(tmp_path):
    p = tmp_path / "court_annotations.txt"
    p.write_text("corners=[[1724, 1122], [2372, 1135], [3827, 2095], [145, 2005]]\n"
                 "roi_corners=[(0, 0), (3839, 2159)]\nmid_height=1589\n",
                 encoding="utf-8")
    assert mod.parse_quad(str(p)) == [(1724.0, 1122.0), (2372.0, 1135.0),
                                      (3827.0, 2095.0), (145.0, 2005.0)]


def test_parse_quad_rejects_wrong_point_count():
    with pytest.raises(ValueError):
        mod.parse_quad("10,20 30,20")


def test_parse_quad_rejects_file_without_corners(tmp_path):
    p = tmp_path / "bad.txt"
    p.write_text("mid_height=1589\n", encoding="utf-8")
    with pytest.raises(ValueError):
        mod.parse_quad(str(p))


def test_competing_movers_threshold_is_labelled_unvalidated():
    """A target with no measured success case behind it must say so in its docstring."""
    doc = mod.__dict__["__annotations__"] if False else None
    src = pathlib.Path(_SPEC.origin).read_text(encoding="utf-8")
    idx = src.index("MAX_CANDIDATES_PER_FRAME = ")
    tail = src[idx:idx + 700]
    assert "NOT VALIDATED" in tail
    assert "necessary, not sufficient" in tail


def test_measure_clutter_rejects_an_unreadable_video(tmp_path):
    with pytest.raises(ValueError):
        mod.measure_clutter(str(tmp_path / "missing.mp4"), DJI_QUAD, 0.0, 60, 4)
