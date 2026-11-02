"""scripts/score_landing.py on a synthetic ShuttleSet layout."""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import score_landing as sc  # noqa: E402

MATCH = "Fake_Match"
# ShuttleSet labels are at 1280 x 720; the npz frames at 1920 x 1080 (x 1.5). Court = metres x 50 + 100.
BASE = [[100, 100], [405, 100], [405, 770], [100, 770]]


def _write(tmp_path, landing_y=275.0):
    data = tmp_path / "data"
    labels = data / "shuttleset" / "labels"
    (labels / MATCH).mkdir(parents=True)
    with open(labels / "homography.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "video", "homography_matrix", "upleft_x", "upright_x", "downleft_x", "downright_x",
                    "upleft_y", "upright_y", "downleft_y", "downright_y"])
        w.writerow([1, MATCH, "[]", 100, 405, 100, 405, 100, 100, 770, 770])
    rows = [  # shot 1 (lower) lands at the upper receiver's contact: court (3.0, 3.5) -> label (250, 275)
        {"rally": 1, "ball_round": 1, "frame_num": 30, "landing_x": 250, "landing_y": landing_y},
        {"rally": 1, "ball_round": 2, "frame_num": 60, "landing_x": "", "landing_y": ""},
    ]
    with open(labels / MATCH / "set1.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    n = 90
    shuttle = np.full((n, 2), np.nan, np.float32)
    kp = np.full((n, 2, 17, 2), np.nan, np.float32)
    # upper receiver's ankles at court (3.0, 2.9) at frame 60 (index 59): image (100 + 150, 100 + 145) x 1.5
    kp[59, 1, 15] = kp[59, 1, 16] = [(100 + 3.0 * 50) * 1.5, (100 + 2.9 * 50) * 1.5]
    shuttle[59] = [(100 + 3.0 * 50) * 1.5, 50.0]
    hd = data / "hit_dataset" / MATCH
    hd.mkdir(parents=True)
    np.savez(hd / "s1_r1.npz", fps=30.0, first_frame=1, shuttle=shuttle, keypoints=kp,
             court_corners=np.asarray(BASE, np.float32) * 1.5, image_size=np.asarray([1920, 1080]),
             match=MATCH, set=1, rally=1, kind="rally", hit_frames=np.asarray([30, 60]),
             hitter=np.asarray([0, 1]))
    return data


def test_scores_feet_and_shipped_against_the_label(tmp_path):
    data = _write(tmp_path)
    by_match = sc.collect(str(data))
    assert list(by_match) == [MATCH] and len(by_match[MATCH]) == 1     # the last shot is terminal
    side, truth, est = by_match[MATCH][0]
    assert side == "upper" and truth == pytest.approx((3.0, 3.5))
    assert est["feet"] == pytest.approx((3.0, 2.9), abs=1e-4)
    assert est["shipped"] == pytest.approx((3.0, 3.5), abs=0.01)     # 2.9 + REACH_M 0.6
    doc = sc.report(by_match)
    assert doc["overall"]["shipped"]["median_m"] == pytest.approx(0.0, abs=0.01)
    assert doc["overall"]["feet"]["median_m"] == pytest.approx(0.6, abs=0.01)
    assert doc["overall"]["shipped"]["depth_agree"] == 1.0
    assert MATCH in sc.markdown(doc)


def test_main_writes_reports(tmp_path):
    data = _write(tmp_path)
    out = tmp_path / "rep"
    assert sc.main(["--data", str(data), "--out", str(out)]) == 0
    doc = json.loads((out / "landing_report.json").read_text(encoding="utf-8"))
    assert doc["overall"]["shipped"]["n"] == 1
    assert (out / "landing_report.md").read_text(encoding="utf-8").startswith("# Landing")


def test_rally_with_a_different_hit_count_is_skipped(tmp_path):
    data = _write(tmp_path)
    with open(data / "shuttleset" / "labels" / MATCH / "set1.csv", "a", newline="", encoding="utf-8") as fh:
        fh.write("1,3,90,,\n")
    assert sc.collect(str(data)) == {}
    assert sc.main(["--data", str(data), "--out", str(tmp_path / "r")]) == 1
