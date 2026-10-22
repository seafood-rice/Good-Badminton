# tests/test_score_strokes.py
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import score_strokes as ss  # noqa: E402

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402


def test_match_prefers_same_hitter_and_reports_missed():
    labelled = [{"t": 1.0, "frame": 31, "hitter": "lower", "stroke": "smash"},
                {"t": 5.0, "frame": 151, "hitter": "upper", "stroke": "lift"}]
    predicted = [{"frame": 32, "hitter": "upper", "stroke": "clear"},
                 {"frame": 33, "hitter": "lower", "stroke": "smash"}]
    pairs, missed = ss.match(labelled, predicted, fps=30.0)
    assert pairs == [("smash", "smash")] and missed == 1


def _result(tmp_path, name, strokes, schema=2):
    d = tmp_path / "res" / name
    d.mkdir(parents=True)
    (d / "strokes.json").write_text(json.dumps({"schema": schema, "strokes": strokes}), encoding="utf-8")
    (d / "hits.json").write_text(json.dumps({"fps": 30.0, "hits": []}), encoding="utf-8")


def _sheet(tmp_path, stroke="smash"):
    text = sl.write_sheet([{"name": "V", "split": "test", "fps": 30.0, "clip": "V.mp4",
                            "hits": [{"frame": 31, "time_sec": 1.0, "hitter": "lower"}]}])
    text = text.replace("|  | 31 |", f"| {stroke} | 31 |")
    p = tmp_path / "LABELS.md"
    p.write_text(text, encoding="utf-8")
    return p


def _run(tmp_path, *extra):
    return subprocess.run([sys.executable, "scripts/score_strokes.py", "--labels", str(_sheet(tmp_path)),
                           "--results-dir", str(tmp_path / "res"), *extra],
                          capture_output=True, text=True, cwd=ROOT)


def test_cli_fails_bar_on_tiny_set(tmp_path):
    _result(tmp_path, "V", [{"frame": 31, "hitter": "lower", "stroke": "smash"}])
    out = tmp_path / "m.json"
    r = _run(tmp_path, "--json", str(out))
    assert r.returncode == 1 and "FAIL" in r.stdout and "fewer than 20" in r.stdout
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["metrics"]["accuracy"] == 1.0 and m["passed"] is False


def test_cli_rejects_stale_strokes(tmp_path):
    _result(tmp_path, "V", [{"frame": 31, "hitter": "lower", "stroke": "net"}], schema=None)
    r = _run(tmp_path)
    assert r.returncode == 2 and "re-label" in (r.stdout + r.stderr)
