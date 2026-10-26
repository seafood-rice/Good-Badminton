"""Owner rally label store (badminton_analysis/outcomes/labels.py)."""
import json

from badminton_analysis.outcomes import labels


def _seg(i, start, end):
    return {"id": i, "start_sec": start, "end_sec": end}


def _lab(end, verdict="upper", start=None, outcome=None, added=False):
    return {"start_sec": start, "end_sec": end, "verdict": verdict, "outcome": outcome, "added": added}


def test_validate_accepts_and_normalises():
    out = labels.validate_labels([
        {"end_sec": 30, "start_sec": 20, "verdict": "lower", "outcome": "winner"},
        {"end_sec": 40.5, "verdict": "not_point", "outcome": "winner"},
        {"end_sec": 50, "start_sec": 45, "verdict": "upper", "added": True},
    ])
    assert out == [
        _lab(30.0, "lower", start=20.0, outcome="winner"),
        _lab(40.5, "not_point"),
        _lab(50.0, "upper", added=True),
    ]


def test_validate_rejects_bad_input():
    assert labels.validate_labels("x") is None
    assert labels.validate_labels([{"end_sec": 1, "verdict": "both"}]) is None
    assert labels.validate_labels([{"end_sec": float("nan"), "verdict": "upper"}]) is None
    assert labels.validate_labels([{"end_sec": -1, "verdict": "upper"}]) is None
    assert labels.validate_labels([{"end_sec": True, "verdict": "upper"}]) is None
    assert labels.validate_labels([{"end_sec": 5, "start_sec": 9, "verdict": "upper"}]) is None
    assert labels.validate_labels([{"end_sec": 5, "verdict": "upper", "outcome": "ace"}]) is None
    assert labels.validate_labels([{"end_sec": 5, "verdict": "upper", "added": "yes"}]) is None
    assert labels.validate_labels([{"end_sec": 1, "verdict": "upper"}] * (labels.MAX_LABELS + 1)) is None


def test_load_absent_is_empty(tmp_path):
    assert labels.load(tmp_path / "none.json") == labels.empty()


def test_load_corrupt_is_none(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{not json", encoding="utf-8")
    assert labels.load(p) is None
    p.write_text(json.dumps({"schema": 2, "revision": 0, "labels": []}), encoding="utf-8")
    assert labels.load(p) is None
    p.write_text(json.dumps({"schema": 1, "revision": "1", "labels": []}), encoding="utf-8")
    assert labels.load(p) is None
    p.write_text(json.dumps({"schema": 1, "revision": 1, "labels": [{"verdict": "upper"}]}), encoding="utf-8")
    assert labels.load(p) is None


def test_save_round_trip_leaves_no_tmp(tmp_path):
    p = labels.labels_path(tmp_path / "rally_labels", "vid")
    doc = {"schema": 1, "revision": 3, "labels": [_lab(12.0, "lower", start=5.0)]}
    labels.save(p, doc)
    assert labels.load(p) == doc
    assert [f.name for f in p.parent.iterdir()] == ["vid.json"]


def test_attach_exact():
    segs = [_seg(1, 10, 20), _seg(2, 25, 35)]
    by_seg, added = labels.attach([_lab(20, "upper", start=10), _lab(35, "not_point", start=25)], segs)
    assert by_seg[1]["verdict"] == "upper" and by_seg[2]["verdict"] == "not_point"
    assert added == []


def test_attach_after_resegmentation():
    # Made on a 20-30 s segment; the new analysis split it into 18-25 and 26-31.
    by_seg, added = labels.attach([_lab(30, "lower", start=20)], [_seg(7, 18, 25), _seg(8, 26, 31)])
    assert list(by_seg) == [8] and added == []


def test_unattached_label_becomes_added():
    lab = _lab(60, "lower", start=55)
    by_seg, added = labels.attach([lab, _lab(80, "not_point", start=75)], [_seg(1, 10, 20)])
    assert by_seg == {} and added == [lab]


def test_added_points_always_added():
    lab = _lab(15, "upper", added=True)
    by_seg, added = labels.attach([lab], [_seg(1, 10, 20)])
    assert by_seg == {} and added == [lab]


def test_later_label_wins_on_one_segment():
    first, second = _lab(19, "upper", start=10), _lab(20, "lower", start=10)
    by_seg, added = labels.attach([first, second], [_seg(1, 10, 20)])
    assert by_seg[1] is second and added == [first]
    by_seg, added = labels.attach([_lab(19, "unsure", start=10), second], [_seg(1, 10, 20)])
    assert by_seg[1] is second and added == []
