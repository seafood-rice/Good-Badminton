import json
import sys
from pathlib import Path

import pytest

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


def test_per_clip_carries_source_and_reason(tmp_path):
    _run(tmp_path, "learned", [(31, "lower")])
    clip = score(LABELS, str(tmp_path), "learned")["per_clip"][1]
    assert clip["source"] == "learned" and clip["reason"] is None


def test_expect_source_reports_mismatching_clips(tmp_path):
    d = tmp_path / "rally1_learned"
    d.mkdir()
    (d / "hits.json").write_text(json.dumps({
        "source": "contact_rule", "reason": "no dense shuttle track", "fps": 60.0,
        "hits": [{"frame": 31, "time_sec": 0.5, "hitter": "lower"}]}))
    ok = score(LABELS, str(tmp_path), "learned", expect_source="contact_rule")
    assert ok["source_mismatch"] == []
    bad = score(LABELS, str(tmp_path), "learned", expect_source="learned")
    assert bad["source_mismatch"] == [1]
    assert bad["per_clip"][1]["reason"] == "no dense shuttle track"
    assert "source_mismatch" not in score(LABELS, str(tmp_path), "learned")


def test_main_flags_source_mismatch_and_hitter_counts(tmp_path, capsys):
    import score_hits

    _run(tmp_path, "learned", [(31, "lower"), (91, "lower")])
    labels = tmp_path / "LABELS.md"
    labels.write_text(LABELS, encoding="utf-8")
    score_hits.main(["--labels", str(labels), "--runs-dir", str(tmp_path), "--suffix", "learned",
                     "--expect-source", "contact_rule"])
    out = capsys.readouterr().out
    assert "SOURCE MISMATCH clips 1" in out and "done bar not met" in out
    assert "hitter 0.50 (1/2)" in out and "source learned" in out


def test_main_reports_empty_labels(tmp_path, capsys):
    import score_hits

    labels = tmp_path / "LABELS.md"
    labels.write_text("# sheet\n### rally1.mp4 (1 s - 9 s)\n\n| hit_sec | player |\n|---|---|\n|  |  |\n",
                      encoding="utf-8")
    score_hits.main(["--labels", str(labels), "--runs-dir", str(tmp_path), "--suffix", "learned"])
    assert "no labelled hits found" in capsys.readouterr().out


def _labels_with_row(row):
    return f"### rally1.mp4 (280.5 s – 301 s)\n\n| hit_sec | player |\n|---|---|\n{row}\n"


def test_parse_labels_rejects_a_non_numeric_time():
    with pytest.raises(ValueError, match=r"rally1.*281,0"):
        parse_labels(_labels_with_row("| 281,0 | near |"))


def test_parse_labels_rejects_an_unknown_player():
    with pytest.raises(ValueError, match=r"rally1.*player 'nera'"):
        parse_labels(_labels_with_row("| 281.0 | nera |"))


def test_parse_labels_rejects_a_time_outside_the_clip_span():
    with pytest.raises(ValueError, match=r"rally1.*outside"):
        parse_labels(_labels_with_row("| 12.0 | near |"))


def test_parse_labels_unmatched_heading_ends_the_clip_section():
    text = _labels_with_row("| 281.0 | near |") + "\n### notes\n\n| a | b |\n|---|---|\n| 99.0 | oops |\n"
    _, hits, _ = parse_labels(text)
    assert [h["t"] for h in hits[1]] == [281.0]
