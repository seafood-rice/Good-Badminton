# tests/test_posture_progress.py
from badminton_analysis.posture.system import format_progress, analyzing_pct


def test_format_progress_shape():
    assert format_progress(42, "analyzing") == "PROGRESS 42 analyzing"
    assert format_progress(5.0, "loading") == "PROGRESS 5 loading"


def test_analyzing_pct_bands():
    assert analyzing_pct(0, 100) == 5        # start of analyzing band
    assert analyzing_pct(100, 100) == 85     # end of analyzing band
    assert analyzing_pct(50, 100) == 45      # midpoint
    assert analyzing_pct(10, 0) == 85        # guard: total 0 -> clamped to full band
