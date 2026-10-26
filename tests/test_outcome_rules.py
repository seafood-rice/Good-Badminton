"""Rule cascade (badminton_analysis/outcomes/rules.py), one case per spec table row."""
import pytest

from badminton_analysis.outcomes.rules import decide


def _land(winner, ending):
    return {"winner": winner, "ending": ending, "point": [0, 0], "reason": "x"}


@pytest.mark.parametrize("landing,e1,e3,winner,conf,rule", [
    (None, "upper", "upper", None, None, "no_landing"),
    (_land(None, None), "upper", "upper", None, None, "no_landing"),
    (_land("upper", "out"), "upper", None, None, None, "out_hitter_conflict"),
    (_land("upper", "out"), "upper", "upper", None, None, "out_hitter_conflict"),
    (_land("upper", "in"), "lower", "lower", None, None, "server_conflict"),
    (_land("upper", "in"), "lower", "upper", "upper", 0.8, "landing_and_server"),
    (_land("upper", None), None, "upper", "upper", 0.8, "landing_and_server"),
    (_land("upper", "out"), "lower", None, "upper", 0.6, "out_and_hitter"),
    (_land("upper", "in"), "lower", None, None, None, "single_signal"),
    (_land("upper", "net"), "upper", None, None, None, "single_signal"),
    (_land("lower", "out"), None, None, None, None, "single_signal"),
])
def test_rule_table(landing, e1, e3, winner, conf, rule):
    assert decide(landing, e1, e3) == {"winner_side": winner, "confidence": conf, "rule": rule}
