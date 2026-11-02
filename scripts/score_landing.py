# scripts/score_landing.py
"""Score the shipped landing estimate against ShuttleSet landing_x/y (landing spec 2026-11-02 section 7).

Usage:
  python scripts/score_landing.py [--data data] [--out <dir>]

Reads every data/hit_dataset/<match>/*.npz rally (our pipeline's own shuttle track and keypoints, the
ShuttleSet court corners) whose hit count matches its label CSV rally. For each non-terminal shot it
estimates the landing from the next hit (receiver's ankle midpoint + the shuttle's image column) and
compares it, in court metres, with landing_x/y projected through the ShuttleSet corners. Reports, per
video and overall, the error and the depth-band / side-third agreement of the receiver's feet (the
baseline, today's ``sent_to``) and of the shipped estimate, plus a leave-one-match-out check of
REACH_M. Writes landing_report.json and landing_report.md to --out.
"""
import argparse
import csv
import glob
import json
import math
import os
import statistics
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import shuttleset_labels as sl  # noqa: E402
from badminton_analysis.insights import landing as land  # noqa: E402
from badminton_analysis.outcomes import evidence as ev  # noqa: E402
from badminton_analysis.strategy.shots import zone_of  # noqa: E402

FOOT_WINDOW = 8       # frames, as the shot table's position lookup
SHUTTLE_WINDOW = 3    # frames, as the shot table's shuttle lookup
ANKLES = (15, 16)     # COCO keypoints
METHODS = ("feet", "shipped")


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def label_rallies(labels_dir):
    """``{(set, rally): [row, ...]}`` in frame order from a match's set CSVs."""
    out = {}
    for set_no in (1, 2, 3):
        path = os.path.join(labels_dir, f"set{set_no}.csv")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if not math.isfinite(_f(row.get("frame_num"))) or not math.isfinite(_f(row.get("rally"))):
                    continue
                out.setdefault((set_no, int(_f(row["rally"]))), []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: _f(r["frame_num"]))
    return out


def _near(arr_at, index, n, window):
    for d in range(window + 1):
        for j in ((index,) if d == 0 else (index - d, index + d)):
            if 0 <= j < n:
                p = arr_at(j)
                if p is not None:
                    return p
    return None


def rally_samples(z, rows, h_truth):
    """``[(side, truth_xy, {method: xy})]`` for the non-terminal shots of one rally."""
    hit_frames, hitter = z["hit_frames"], z["hitter"]
    if len(rows) != len(hit_frames):
        return []
    h = ev.court_homography(z["court_corners"].tolist())
    if h is None:
        return []
    h_inv = land.inverse(h)
    shuttle, kp = z["shuttle"], z["keypoints"]
    n = len(shuttle)

    def foot_img(j, p):
        a = kp[j, p, list(ANKLES)].mean(axis=0)
        return a if np.all(np.isfinite(a)) else None

    def shuttle_img(j):
        return shuttle[j] if np.all(np.isfinite(shuttle[j])) else None

    out = []
    for k in range(len(hit_frames) - 1):
        truth = ev.to_court(h_truth, (_f(rows[k]["landing_x"]), _f(rows[k]["landing_y"])))
        receiver = int(hitter[k + 1])        # 0 lower, 1 upper: also the keypoint index
        if truth is None or receiver not in (0, 1):
            continue
        side = ("lower", "upper")[receiver]
        index = int(hit_frames[k + 1]) - int(z["first_frame"])
        f_img = _near(lambda j: foot_img(j, receiver), index, n, FOOT_WINDOW)
        foot = ev.to_court(h, f_img) if f_img is not None else None
        if foot is None:
            continue
        s_img = _near(shuttle_img, index, n, SHUTTLE_WINDOW)
        est = land.estimate(side, foot, tuple(map(float, s_img)) if s_img is not None else None, h_inv)
        out.append((side, truth, {"feet": foot, "shipped": (est["x"], est["y"])}))
    return out


def collect(data_dir):
    """``{match: [(side, truth, {method: xy})]}`` over every usable rally."""
    labels_root = os.path.join(data_dir, "shuttleset", "labels")
    out = {}
    for match_dir in sorted(glob.glob(os.path.join(data_dir, "hit_dataset", "*"))):
        match = os.path.basename(match_dir)
        if match.startswith("_") or not os.path.isdir(match_dir):
            continue
        try:
            h_truth = ev.court_homography(sl.load_corners(os.path.join(labels_root, "homography.csv"), match))
            rallies = label_rallies(os.path.join(labels_root, match))
        except (KeyError, OSError):
            continue
        samples = []
        for path in sorted(glob.glob(os.path.join(match_dir, "*.npz"))):
            with np.load(path) as z:
                if "kind" in z.files and str(z["kind"]) != "rally":
                    continue
                rows = rallies.get((int(z["set"]), int(z["rally"])))
                if rows:
                    samples.extend(rally_samples(z, rows, h_truth))
        if samples:
            out[match] = samples
    return out


def score(samples, method):
    """``{n, median_m, p80_m, depth_agree, third_agree}`` of one method over samples."""
    if not samples:
        return {"n": 0, "median_m": None, "p80_m": None, "depth_agree": None, "third_agree": None}
    errs, depth, third = [], 0, 0
    for side, truth, est in samples:
        xy = est[method]
        errs.append(math.dist(xy, truth))
        zt, ze = zone_of(side, *truth), zone_of(side, *xy)
        depth += zt["depth"] == ze["depth"]
        third += zt["side"] == ze["side"]
    errs.sort()
    n = len(errs)
    return {"n": n, "median_m": round(statistics.median(errs), 3), "p80_m": round(errs[int(0.8 * (n - 1))], 3),
            "depth_agree": round(depth / n, 3), "third_agree": round(third / n, 3)}


def reach_lomo(by_match):
    """Net-ward feet-to-landing gap per held-out match: the offset the other matches would fit."""
    out = {}
    for held in by_match:
        gaps = [(t[1] - e["feet"][1]) * (1 if s == "upper" else -1)
                for m, rows in by_match.items() if m != held for s, t, e in rows]
        out[held] = round(statistics.median(gaps), 3) if gaps else None
    return out


def report(by_match):
    per_video = {m: {meth: score(rows, meth) for meth in METHODS} for m, rows in by_match.items()}
    every = [r for rows in by_match.values() for r in rows]
    return {"per_video": per_video, "overall": {meth: score(every, meth) for meth in METHODS},
            "reach_m": land.REACH_M, "reach_lomo": reach_lomo(by_match)}


def markdown(doc):
    lines = ["# Landing estimate vs ShuttleSet landing_x/y", "",
             f"Shipped estimate: depth from the receiver's feet moved {doc['reach_m']} m towards the net, "
             "width from the shuttle's image column. Baseline: the receiver's feet (today's `sent_to`).", "",
             "| Video | n | Feet median m | Feet depth agree | Shipped median m | Shipped depth agree | "
             "Shipped third agree |", "|---|---|---|---|---|---|---|"]
    rows = list(doc["per_video"].items()) + [("**overall**", doc["overall"])]
    for name, r in rows:
        f, s = r["feet"], r["shipped"]
        lines.append(f"| {name} | {s['n']} | {f['median_m']} | {f['depth_agree']} | {s['median_m']} | "
                     f"{s['depth_agree']} | {s['third_agree']} |")
    lines += ["", "Leave-one-match-out reach offset (fit on the other matches): "
              + ", ".join(f"{m[:30]} {v}" for m, v in doc["reach_lomo"].items()), ""]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data", default=os.path.join(ROOT, "data"))
    p.add_argument("--out", default=os.path.join(ROOT, "outputs", "landing-validation"))
    a = p.parse_args(argv)
    by_match = collect(a.data)
    if not by_match:
        print("no usable ShuttleSet rallies found")
        return 1
    doc = report(by_match)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "landing_report.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    text = markdown(doc)
    with open(os.path.join(a.out, "landing_report.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
