# tests/test_analysis_quality.py
import numpy as np
import badminton_analysis.system as sysmod


def test_court_view_cadence_holds_state_between_checks(monkeypatch):
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    calls = {"n": 0}
    def fake_check(frame, tmpl, threshold=0.75):
        calls["n"] += 1
        return True
    s.is_court_view = fake_check
    s._court_view_cached = None
    s._court_view_last_frame = -10
    # First call computes; next (interval-1) calls reuse the cache.
    for f in range(1, sysmod.COURT_VIEW_CHECK_INTERVAL + 1):
        s._court_view_for_frame(np.zeros((4, 4), dtype=np.uint8), None, f)
    assert calls["n"] == 1  # only the first frame in the interval hit the model


def test_accurate_is_default_and_analyzes_every_frame():
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.analysis_quality = "accurate"
    assert s._analyze_this_frame(1) and s._analyze_this_frame(2) and s._analyze_this_frame(3)


def test_fast_mode_strides_heavy_analysis():
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.analysis_quality = "fast"
    hits = [f for f in range(1, 3 * sysmod.FAST_FRAME_STRIDE + 1) if s._analyze_this_frame(f)]
    # Only every FAST_FRAME_STRIDE-th frame is analyzed in Fast mode.
    assert hits == list(range(sysmod.FAST_FRAME_STRIDE, 3 * sysmod.FAST_FRAME_STRIDE + 1, sysmod.FAST_FRAME_STRIDE))
