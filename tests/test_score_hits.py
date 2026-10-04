import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_hits import meets_bar, parse_labels, score  # noqa: E402

LABELS = """# sheet
### rally1.mp4 (280.5 s – 301 s)

| hit_sec | player | stroke | notes |
|---|---|---|---|
| 281.00 | near | serve | |
| 282.00 | far | lift | |
| 283.00 | near |  | |

### rally2.mp4 (527.5 s – 546.5 s)

| hit_sec | player | stroke | notes |
|---|---|---|---|
|  |  |  |  |

## Unusable spans

| from_sec | to_sec | why |
|---|---|---|
| 282.9 | 283.2 | blocked |
"""


def test_parse_labels_spans_hits_and_unusable():
    spans, hits, unusable = parse_labels(LABELS)
    assert spans == {1: (280.5, 301.0), 2: (527.5, 546.5)}
    assert [h["t"] for h in hits[1]] == [281.0, 282.0, 283.0]
    assert hits[1][1]["player"] == "far" and hits[1][1]["stroke"] == "lift"
    assert hits[2] == []
    assert unusable == [(282.9, 283.2)]


def _run(tmp_path, suffix, hits, fps=60.0):
    d = tmp_path / f"rally1_{suffix}"
    d.mkdir()
    (d / "hits.json").write_text(json.dumps({
        "source": "learned", "fps": fps,
        "hits": [{"frame": f, "time_sec": (f - 1) / fps, "hitter": h} for f, h in hits]}))


def test_score_excludes_unusable_and_checks_hitter(tmp_path):
    # 281.0 -> clip frame 31 at 60 fps (0.5 s); 282.0 -> frame 91; 283.0 is unusable
    _run(tmp_path, "learned", [(31, "lower"), (91, "lower"), (151, "lower")])
    result = score(LABELS, str(tmp_path), "learned", tol=0.2)
    s = result["summary"]
    assert s["truth"] == 2 and s["matched"] == 2 and s["detected"] == 2
    assert s["hitter_correct"] == 0.5
    assert meets_bar(s) is False


def test_meets_bar():
    assert meets_bar({"recall": 0.6, "precision": 0.7, "hitter_correct": 0.85})
    assert not meets_bar({"recall": 0.59, "precision": 0.9, "hitter_correct": 0.9})
    assert not meets_bar({"recall": 0.9, "precision": 0.9, "hitter_correct": None})
