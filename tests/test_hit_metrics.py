import numpy as np

from badminton_analysis.stroke.hit_metrics import match_events, summarize
from badminton_analysis.stroke.shuttle_turns import shuttle_turn_events
from tests.hit_fixtures import make_signals


def test_match_is_one_to_one_and_closest_first():
    pairs = match_events([1.0, 1.1], [1.05], tol=0.2)
    assert pairs == [(0, 0)] or pairs == [(1, 0)]
    assert len(pairs) == 1
    assert match_events([1.0], [1.5], tol=0.2) == []


def test_summarize_rates_and_hitter_accuracy():
    s = summarize([1.0, 2.0, 3.0, 4.0], [1.05, 2.1, 5.0], tol=0.2,
                  truth_sides=["near", "far", "near", "far"], det_sides=["near", "near", "far"])
    assert s["matched"] == 2 and s["truth"] == 4 and s["detected"] == 3
    assert s["recall"] == 0.5 and abs(s["precision"] - 2 / 3) < 1e-9
    assert s["hitter_compared"] == 2 and s["hitter_correct"] == 0.5


def test_summarize_empty_inputs_are_safe():
    s = summarize([], [], tol=0.2)
    assert s["recall"] == 0.0 and s["precision"] == 0.0 and s["hitter_correct"] is None


def test_shuttle_turns_find_triangle_wave_corners():
    sig = make_signals(n=150, fps=30.0)
    events = shuttle_turn_events(sig.shuttle, 30.0)
    assert any(abs(e - 30) <= 2 for e in events) and any(abs(e - 90) <= 2 for e in events)


def test_shuttle_turns_skip_missing_points():
    shuttle = np.full((50, 2), np.nan)
    assert shuttle_turn_events(shuttle, 30.0) == []
