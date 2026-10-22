import subprocess
import sys

import pytest

from badminton_analysis.stroke import stroke_labels as sl

VIDEOS = [{"name": "Dji 2026 0007 D", "split": "test", "fps": 60.0, "clip": "Dji 2026 0007 D.mp4",
           "hits": [{"frame": 61, "time_sec": 1.0, "hitter": "lower"},
                    {"frame": 121, "time_sec": 2.0, "hitter": "upper"}]},
          {"name": "B", "split": "train", "fps": 30.0, "clip": "B.mp4", "hits": []}]


def test_round_trip_prefilled_rows():
    sheet = sl.parse_sheet(sl.write_sheet(VIDEOS))
    a = sheet["Dji 2026 0007 D"]
    assert a["split"] == "test" and a["fps"] == pytest.approx(60.0)
    assert [(r["t"], r["player"], r["frame"], r["status"]) for r in a["rows"]] == [
        (1.0, "near", 61, "blank"), (2.0, "far", 121, "blank")]
    assert sheet["B"]["rows"] == []


def _fill(text, values):
    lines, k = text.splitlines(), 0
    for i, line in enumerate(lines):
        if line.startswith("| ") and line.split("|")[1].strip().replace(".", "").isdigit():
            cells = line.split("|")
            cells[3] = f" {values[k]} "
            lines[i] = "|".join(cells)
            k += 1
    return "\n".join(lines)


def test_parse_statuses_and_case_tolerance():
    text = _fill(sl.write_sheet(VIDEOS[:1]), [" Smash ", "?"])
    text += "\n| 3.5 | far | NET_SHOT |  | added by owner |\n| 4.0 | near | not-a-hit |  |  |\n"
    rows = sl.parse_sheet(text)["Dji 2026 0007 D"]["rows"]
    assert [(r["stroke"], r["status"]) for r in rows] == [
        ("smash", "labelled"), (None, "unsure"), ("net_shot", "labelled"), (None, "not_a_hit")]
    hits = sl.labelled_hits(sl.parse_sheet(text)["Dji 2026 0007 D"])
    assert hits[1] == {"t": 3.5, "frame": 211, "hitter": "upper", "stroke": "net_shot"}


def test_parse_rejects_unknown_stroke_with_row():
    text = _fill(sl.write_sheet(VIDEOS[:1]), ["nethit", ""])
    with pytest.raises(ValueError, match=r"nethit.*Dji 2026 0007 D"):
        sl.parse_sheet(text)


def test_parse_rejects_bad_split_and_player():
    bad = sl.write_sheet(VIDEOS[:1]).replace("(split: test)", "(split: holdout)")
    with pytest.raises(ValueError, match="split"):
        sl.parse_sheet(bad)
    bad = sl.write_sheet(VIDEOS[:1]).replace("| near |", "| middle |", 1)
    with pytest.raises(ValueError, match="middle"):
        sl.parse_sheet(bad)


def test_progress_counts_groups_per_split():
    text = _fill(sl.write_sheet(VIDEOS[:1]), ["push", "long_serve"])
    prog = sl.progress(sl.parse_sheet(text))
    assert prog["test"]["drive"] == 1 and prog["test"]["serve"] == 1
    assert prog["train"] == {g: 0 for g in sl.GROUPS}


def test_refuses_to_overwrite(tmp_path):
    out = tmp_path / "sheet"
    out.mkdir()
    (out / "LABELS.md").write_text("owner work", encoding="utf-8")
    r = subprocess.run([sys.executable, "scripts/make_stroke_label_sheet.py",
                        "--results-dir", str(tmp_path), "--out", str(out), "--no-clips", "X"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "--force" in (r.stderr + r.stdout)
    assert (out / "LABELS.md").read_text(encoding="utf-8") == "owner work"


def test_cli_writes_sheet_from_results(tmp_path):
    import json
    res = tmp_path / "res" / "V1"
    res.mkdir(parents=True)
    (res / "hits.json").write_text(json.dumps({"fps": 30.0, "hits": [
        {"frame": 31, "time_sec": 1.0, "hitter": "lower"}]}), encoding="utf-8")
    out = tmp_path / "sheet"
    r = subprocess.run([sys.executable, "scripts/make_stroke_label_sheet.py",
                        "--results-dir", str(tmp_path / "res"), "--out", str(out),
                        "--no-clips", "--test", "V1", "V1"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    sheet = sl.parse_sheet((out / "LABELS.md").read_text(encoding="utf-8"))
    assert sheet["V1"]["split"] == "test" and sheet["V1"]["rows"][0]["frame"] == 31


def test_parse_rejects_malformed_time_cell():
    text = sl.write_sheet(VIDEOS[:1]) + "| 4.2s | near | smash |  |  |\n"
    with pytest.raises(ValueError, match=r"Dji 2026 0007 D.*4\.2s"):
        sl.parse_sheet(text)


def test_corrected_time_overrides_stale_frame():
    text = _fill(sl.write_sheet(VIDEOS[:1]), ["smash", "smash"])
    text = text.replace("| 1.0000 |", "| 2.0000 |", 1)      # owner corrects row 1 to 2.0 s
    hits = sl.labelled_hits(sl.parse_sheet(text)["Dji 2026 0007 D"])
    assert [h["frame"] for h in hits] == [121, 121]
    agree = {"fps": 60.0, "rows": [{"t": 2.0, "player": "far", "stroke": "smash",
                                    "frame": 122, "status": "labelled"}]}
    assert sl.labelled_hits(agree)[0]["frame"] == 122


def test_indented_rows_parse():
    text = ("### A (split: test)\nclip: `A.mp4` · fps 30.0000\n\n"
            "  | 1.0000 | near | smash | 31 |  |\n")
    rows = sl.parse_sheet(text)["A"]["rows"]
    assert [(r["t"], r["stroke"], r["frame"]) for r in rows] == [(1.0, "smash", 31)]


def test_parse_rejects_repeated_video_heading():
    text = sl.write_sheet(VIDEOS[:1])
    twice = text + text.split("\n", 1)[1].replace("split: test", "split: train")
    with pytest.raises(ValueError, match="repeated"):
        sl.parse_sheet(twice)


def test_force_backs_up_existing_sheet(tmp_path):
    import json
    res = tmp_path / "res" / "V1"
    res.mkdir(parents=True)
    (res / "hits.json").write_text(json.dumps({"fps": 30.0, "hits": []}), encoding="utf-8")
    out = tmp_path / "sheet"
    out.mkdir()
    (out / "LABELS.md").write_text("owner work 1", encoding="utf-8")
    cmd = [sys.executable, "scripts/make_stroke_label_sheet.py", "--results-dir", str(tmp_path / "res"),
           "--out", str(out), "--no-clips", "--force", "V1"]
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    assert (out / "LABELS.md.bak").read_text(encoding="utf-8") == "owner work 1"
    (out / "LABELS.md").write_text("owner work 2", encoding="utf-8")
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    assert (out / "LABELS.md.bak").read_text(encoding="utf-8") == "owner work 1"
    assert (out / "LABELS.md.bak.1").read_text(encoding="utf-8") == "owner work 2"
    assert "### V1 (split: train)" in (out / "LABELS.md").read_text(encoding="utf-8")
