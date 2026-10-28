"""Owner stroke labels (badminton_analysis/stroke/owner_labels.py)."""
import json

from badminton_analysis.stroke import owner_labels as ol
from badminton_analysis.stroke import stroke_classifier as sc
from badminton_analysis.stroke.stroke_types import group_of


def payload(*rows):
    strokes = [{"frame": f, "hitter": h, "stroke": s, "group": group_of(s), "confidence": 0.6}
               for f, h, s in rows]
    return sc.build_payload(strokes, {"version": "t"}, "model", "tracknet")


def lab(t, hitter="lower", stroke="smash", kind="label"):
    return {"time_sec": t, "hitter": hitter, "stroke": stroke, "kind": kind}


def doc_of(*labels, revision=1, split="train"):
    return {"schema": 1, "revision": revision, "split": split, "labels": list(labels)}


def test_round_trip_and_absent(tmp_path):
    path = ol.labels_path(tmp_path / "d", "vid")
    assert ol.load(path) == ol.empty()
    doc = doc_of(lab(1.0), lab(2.0, kind="not_hit", stroke=None), split="test")
    ol.save(path, doc)
    assert ol.load(path) == doc


def test_invalid_items_rejected():
    assert ol.validate_labels([lab(1.0, kind="bogus")]) is None
    assert ol.validate_labels([lab(1.0, stroke="wallop")]) is None
    assert ol.validate_labels([lab(1.0, stroke=None)]) is None
    assert ol.validate_labels([lab(-1.0)]) is None
    assert ol.validate_labels([lab(True)]) is None
    assert ol.validate_labels([lab(1.0, hitter="")]) is None
    assert ol.validate_labels([lab(1.0, hitter="x" * 33)]) is None
    assert ol.validate_labels("nope") is None
    assert ol.validate_labels([lab(1.0)] * (ol.MAX_LABELS + 1)) is None
    assert ol.validate_labels([lab(1.0)] * ol.MAX_LABELS) is not None


def test_not_hit_and_unsure_force_stroke_none():
    out = ol.validate_labels([lab(1.0, kind="not_hit", stroke="smash"),
                              lab(2.0, kind="unsure", stroke="clear")])
    assert [x["stroke"] for x in out] == [None, None]


def test_load_rejects_bad_files(tmp_path):
    path = tmp_path / "v.json"
    path.write_text("{not json", encoding="utf-8")
    assert ol.load(path) is None
    path.write_text(json.dumps({"schema": 2, "revision": 0, "labels": []}), encoding="utf-8")
    assert ol.load(path) is None


def test_attach_nearest_within_tolerance():
    strokes = payload((60, "lower", "clear"), (120, "upper", "lift"))["strokes"]
    by, orphaned, added = ol.attach([lab(1.1), lab(2.5)], strokes, 60.0)
    assert list(by) == [0] and orphaned == [lab(2.5)] and added == []


def test_attach_two_hits_near_one_label_takes_nearest_only():
    strokes = payload((60, "lower", "clear"), (66, "lower", "drop"))["strokes"]   # 1.0 s, 1.1 s
    by, orphaned, _ = ol.attach([lab(1.08)], strokes, 60.0)
    assert list(by) == [1] and orphaned == []


def test_attach_two_labels_one_hit_later_wins():
    strokes = payload((60, "lower", "clear"))["strokes"]
    first, second = lab(1.0, stroke="smash"), lab(1.05, stroke="drop")
    by, orphaned, _ = ol.attach([first, second], strokes, 60.0)
    assert by == {0: second} and orphaned == [first]


def test_added_never_attaches():
    strokes = payload((60, "lower", "clear"))["strokes"]
    by, orphaned, added = ol.attach([lab(1.0, kind="added")], strokes, 60.0)
    assert by == {} and orphaned == [] and len(added) == 1


def test_apply_sets_source_and_recounts():
    p = payload((60, "lower", "clear"), (120, "upper", "lift"), (180, "lower", "drop"),
                (240, "upper", "drop"))
    doc = doc_of(lab(1.0, hitter="upper", stroke="smash"),
                 lab(3.0, kind="not_hit", stroke=None),
                 lab(4.0, kind="unsure", stroke=None))
    out = ol.apply(p, doc, 60.0)
    assert [s["frame"] for s in out["strokes"]] == [60, 120, 240]
    first = out["strokes"][0]
    assert (first["stroke"], first["group"], first["hitter"], first["source"]) == \
        ("smash", "smash", "upper", "owner")
    assert (first["model_stroke"], first["model_group"], first["model_hitter"]) == \
        ("clear", "clear", "lower")
    assert out["strokes"][2]["owner_unsure"] is True and out["strokes"][2]["stroke"] == "drop"
    assert [s["frame"] for s in out["removed"]] == [180]
    assert out["distribution"] == {"smash": 1, "lift": 1, "drop": 1}
    assert out["groups"] == {"smash": 1, "lift": 1, "drop": 1}
    assert out["owner_labels"] == {"labelled": 1, "added": 0, "removed": 1, "unsure": 1,
                                   "orphaned": 0, "revision": 1}
    assert p["strokes"][0]["stroke"] == "clear"      # the input is untouched


def test_apply_is_idempotent():
    p = payload((60, "lower", "clear"), (120, "upper", "lift"))
    doc = {"schema": 1, "revision": 3, "split": "train",
           "labels": [{"time_sec": 1.0, "hitter": "lower", "stroke": "smash", "kind": "label"},
                      {"time_sec": 3.0, "hitter": "upper", "stroke": "net_shot", "kind": "added"}]}
    once = ol.apply(p, doc, 60.0)
    assert ol.apply(once, doc, 60.0) == once
    assert ol.model_view(once) == p
    assert [s["source"] for s in once["strokes"]] == ["owner", "model", "owner"]
    assert once["strokes"][0]["model_stroke"] == "clear"


def test_model_view_restores_removed():
    p = payload((60, "lower", "clear"), (180, "lower", "drop"))
    once = ol.apply(p, doc_of(lab(3.0, kind="not_hit", stroke=None)), 60.0)
    assert len(once["strokes"]) == 1
    assert ol.model_view(once) == p


def _result(tmp_path, with_strokes=True, fps=60.0):
    result = tmp_path / "out" / "vid"
    result.mkdir(parents=True)
    (result / "hits.json").write_text(json.dumps({"fps": fps, "hits": []}), encoding="utf-8")
    if with_strokes:
        (result / "strokes.json").write_text(
            json.dumps(payload((60, "lower", "clear"))), encoding="utf-8")
    return result


def test_apply_to_result_writes_and_reverts(tmp_path):
    result, labels = _result(tmp_path), tmp_path / "labels"
    ol.save(ol.labels_path(labels, "vid"), doc_of(lab(1.0)))
    assert ol.apply_to_result(result, labels, "vid") == {"applied": True, "reason": None}
    got = json.loads((result / "strokes.json").read_text(encoding="utf-8"))
    assert got["strokes"][0]["stroke"] == "smash"
    ol.labels_path(labels, "vid").unlink()           # labels deleted -> overlay reverted
    assert ol.apply_to_result(result, labels, "vid")["applied"] is True
    got = json.loads((result / "strokes.json").read_text(encoding="utf-8"))
    assert got["strokes"][0]["stroke"] == "clear" and "owner_labels" not in got


def test_apply_to_result_without_labels_leaves_file(tmp_path):
    result = _result(tmp_path)
    before = (result / "strokes.json").read_text(encoding="utf-8")
    assert ol.apply_to_result(result, tmp_path / "labels", "vid") == \
        {"applied": False, "reason": "no_labels"}
    assert (result / "strokes.json").read_text(encoding="utf-8") == before


def test_apply_to_result_no_strokes_creates_nothing(tmp_path):
    result = _result(tmp_path, with_strokes=False)
    assert ol.apply_to_result(result, tmp_path / "labels", "vid") == \
        {"applied": False, "reason": "no_strokes"}
    assert not (result / "strokes.json").exists()


def test_apply_to_result_fps_fallback_and_missing(tmp_path):
    result, labels = _result(tmp_path), tmp_path / "labels"
    ol.save(ol.labels_path(labels, "vid"), doc_of(lab(1.0)))
    (result / "hits.json").unlink()
    assert ol.apply_to_result(result, labels, "vid")["reason"] == "no_fps"
    (result / "rally_segments.json").write_text(json.dumps({"fps": 60}), encoding="utf-8")
    assert ol.apply_to_result(result, labels, "vid")["applied"] is True


def test_apply_to_result_unreadable_label_file(tmp_path):
    result, labels = _result(tmp_path), tmp_path / "labels"
    labels.mkdir()
    ol.labels_path(labels, "vid").write_text("{broken", encoding="utf-8")
    assert ol.apply_to_result(result, labels, "vid") == {"applied": False, "reason": "unreadable"}


SHEET = """### vid (split: test)
clip: `x.mp4` - fps 60.0000

| hit_sec | player | stroke | frame | notes |
|---|---|---|---|---|
| 1.0000 | near | smash | 61 |  |
| 2.0000 | far | ? | 121 |  |
| 3.0000 | far | not-a-hit | 181 |  |
| 4.0000 | near |  | 241 |  |
| 5.0000 | far | lift |  |  |

### other (split: train)
clip: `y.mp4` - fps 60.0000

| hit_sec | player | stroke | frame | notes |
|---|---|---|---|---|
| 1.0000 | near | clear | 61 |  |
"""


def test_import_sheet_once_and_skips_existing(tmp_path):
    sheet = tmp_path / "LABELS.md"
    sheet.write_text(SHEET, encoding="utf-8")
    results, labels = tmp_path / "out", tmp_path / "labels"
    (results / "vid").mkdir(parents=True)
    (results / "other").mkdir(parents=True)
    ol.save(ol.labels_path(labels, "other"), doc_of(lab(9.0)))
    assert ol.import_sheet(sheet, labels, results) == ["vid"]
    doc = ol.load(ol.labels_path(labels, "vid"))
    assert doc["split"] == "test"
    assert [(x["kind"], x["hitter"], x["stroke"]) for x in doc["labels"]] == [
        ("label", "lower", "smash"), ("unsure", "upper", None), ("not_hit", "upper", None),
        ("added", "upper", "lift")]
    assert ol.import_sheet(sheet, labels, results) == []
    assert ol.load(ol.labels_path(labels, "other"))["labels"] == [lab(9.0)]
