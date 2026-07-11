"""Tests for rally-level stroke-labeling orchestration.

See badminton_analysis/stroke_recog/recognizer.py, which ties together
hits.hit_events, inputs.build_inputs, bst_model.load_bst/predict, and
classes.to_coarse (Tasks 1-4). Hermetic: no real weights/torch -- bst_model's
load_bst/predict are monkeypatched where recognizer.py looks them up
(`recognizer.bst_model.load_bst` / `.predict`), and the contact track /
frame_lookup fixtures mirror tests/test_bst_hits.py and
tests/test_bst_inputs.py so hit_events and build_inputs run for real.
"""

import numpy as np

from badminton_analysis.stroke_recog import recognizer
from badminton_analysis.stroke_recog.classes import CLASS_NAMES, FINE_TO_COARSE

VIDEO_WH = (1280, 720)
COURT_CORNERS = [(100, 100), (1180, 100), (1180, 620), (100, 620)]


def _shuttle_pos(f, first, second):
    """Piecewise-linear shuttle path with a direction-change ("bounce") at
    each contact frame (mirrors tests/test_bst_hits.py)."""
    if f <= first:
        return (200 - f * 2, 200 - f * 2)
    p1 = 200 - first * 2
    if f <= second:
        d = f - first
        return (p1 + d * 2, p1 + d * 2)
    p2 = p1 + (second - first) * 2
    d2 = f - second
    return (p2 - d2 * 2, p2 - d2 * 2)


def _track_with_two_contacts(first=30, second=90, n=120):
    track = []
    for f in range(n):
        shuttle = _shuttle_pos(f, first, second)
        racket = shuttle if f in (first, second) else (5000, 5000)
        track.append({"frame": f, "racket_head": racket, "shuttle": shuttle})
    return track


def _posed_record(player_side="lower"):
    """A frame_lookup record with valid (sentinel-free) keypoints, per the
    _record() helper in tests/test_bst_inputs.py."""
    kp = np.array([[600 + 2 * j, 300 + 3 * j] for j in range(17)], dtype=float)
    return {
        "keypoints": kp,
        "centroid": (float(kp[11][0] + kp[12][0]) / 2.0, float(kp[11][1] + kp[12][1]) / 2.0),
        "player_side": player_side,
        "shuttle": (640.0, 360.0),
    }


def _fully_posed_window(contact_frame, side, into):
    """Fill every frame of the SEQ_LEN window around contact_frame with a
    posed record, well above build_inputs's _MIN_POSED_FRAMES floor."""
    from badminton_analysis.stroke_recog.inputs import SEQ_LEN

    half = SEQ_LEN // 2
    for f in range(contact_frame - half, contact_frame - half + SEQ_LEN):
        into[f] = _posed_record(side)


def _smash_dominant_logits():
    logits = np.full(len(CLASS_NAMES), -10.0)
    for i, name in enumerate(CLASS_NAMES):
        if FINE_TO_COARSE[name] == "smash":
            logits[i] = 5.0
    return logits


def test_label_rally_two_hits_smash_dominant_logits(monkeypatch):
    track = _track_with_two_contacts(30, 90)
    records = {}
    _fully_posed_window(30, "lower", records)
    _fully_posed_window(90, "upper", records)

    monkeypatch.setattr(recognizer.bst_model, "load_bst", lambda path: object())
    monkeypatch.setattr(
        recognizer.bst_model, "predict",
        lambda model, pose, shuttle, positions: _smash_dominant_logits(),
    )

    r = recognizer.StrokeRecognizer("dummy-weights.pt")
    result = r.label_rally(track, records.get, COURT_CORNERS, VIDEO_WH)

    assert len(result) == 2
    assert [e["frame"] for e in result] == sorted(e["frame"] for e in result)
    assert result[0]["frame"] == 30
    assert result[0]["hitter"] == "lower"
    assert result[1]["frame"] == 90
    assert result[1]["hitter"] == "upper"
    for e in result:
        assert e["stroke"] == "smash"
        assert e["uncertain"] is False
        assert e["confidence"] > recognizer.MIN_STROKE_CONF


def test_label_rally_incomplete_window_is_uncertain(monkeypatch):
    track = _track_with_two_contacts(30, 90)
    records = {}
    _fully_posed_window(30, "lower", records)
    # No records at all near frame 90 -> build_inputs returns None there.

    monkeypatch.setattr(recognizer.bst_model, "load_bst", lambda path: object())
    monkeypatch.setattr(
        recognizer.bst_model, "predict",
        lambda model, pose, shuttle, positions: _smash_dominant_logits(),
    )

    r = recognizer.StrokeRecognizer("dummy-weights.pt")
    result = r.label_rally(track, records.get, COURT_CORNERS, VIDEO_WH)

    assert len(result) == 2
    first, second = result
    assert first["frame"] == 30
    assert first["stroke"] == "smash"
    assert first["uncertain"] is False

    assert second["frame"] == 90
    # frame_lookup(90) is None (no record near that hit) -> hit_events falls
    # back to "unknown" for the hitter, same as build_inputs falling back to
    # "uncertain" for the stroke.
    assert second["hitter"] == "unknown"
    assert second["stroke"] == "uncertain"
    assert second["uncertain"] is True
    assert second["confidence"] == 0.0


def test_label_rally_returns_empty_when_load_bst_raises(monkeypatch):
    def _raise(path):
        raise RuntimeError("checkpoint corrupt")

    monkeypatch.setattr(recognizer.bst_model, "load_bst", _raise)

    track = _track_with_two_contacts(30, 90)
    r = recognizer.StrokeRecognizer("dummy-weights.pt")

    result = r.label_rally(track, lambda f: None, COURT_CORNERS, VIDEO_WH)

    assert result == []


def test_label_rally_returns_empty_without_weights_path(monkeypatch):
    calls = []
    monkeypatch.setattr(
        recognizer.bst_model, "load_bst",
        lambda path: calls.append(path) or object(),
    )

    for weights_path in (None, ""):
        r = recognizer.StrokeRecognizer(weights_path)
        result = r.label_rally([], lambda f: None, COURT_CORNERS, VIDEO_WH)
        assert result == []

    assert calls == []
