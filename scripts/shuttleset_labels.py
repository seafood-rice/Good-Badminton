"""ShuttleSet label parsing for the hit-detector dataset (pure; no video, no models).

Labels come from wywyWang/CoachAI-Projects (MIT), ShuttleSet/set/. frame_num is
at 30 fps in the annotators' copy of each match; pixel coordinates are at
1280x720. Corners are returned in this project's CourtMapper order:
far-left, far-right, near-right, near-left.
"""
import ast
import bisect
import csv
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke.hit_features import CourtGeometry  # noqa: E402

BASE_SIZE = (1280, 720)
LABEL_FPS = 30.0
STROKE7 = ("clear", "serve", "drop", "smash", "net", "lift", "drive")
TYPE_TO_STROKE7 = {
    "發短球": "serve", "發長球": "serve",
    "長球": "clear",
    "挑球": "lift", "防守回挑": "lift",
    "殺球": "smash", "點扣": "smash",
    "切球": "drop", "過渡切球": "drop",
    "放小球": "net", "擋小球": "net", "勾球": "net", "撲球": "net",
    "平球": "drive", "小平球": "drive", "後場抽平球": "drive", "推球": "drive", "防守回抽": "drive",
}


def stroke7_of(raw):
    return TYPE_TO_STROKE7.get((raw or "").strip())


def load_match_table(match_csv):
    with open(match_csv, encoding="utf-8") as fh:
        return {row["video"]: row for row in csv.DictReader(fh)}


def load_corners(homography_csv, video):
    with open(homography_csv, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["video"] == video:
                p = {k: float(row[k]) for k in row if k.endswith("_x") or k.endswith("_y")}
                return [[p["upleft_x"], p["upleft_y"]], [p["upright_x"], p["upright_y"]],
                        [p["downright_x"], p["downright_y"]], [p["downleft_x"], p["downleft_y"]]]
    raise KeyError(f"no homography row for {video}")


def scale_corners(corners, size):
    sx, sy = size[0] / BASE_SIZE[0], size[1] / BASE_SIZE[1]
    return [[x * sx, y * sy] for x, y in corners]


def hitter_side(loc_y, corners):
    try:
        y = float(loc_y)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(y):
        return None
    return "upper" if y < CourtGeometry(corners).net_mid[1] else "lower"


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def parse_match_hits(match_dir, corners):
    hits = []
    for set_no in (1, 2, 3):
        path = os.path.join(match_dir, f"set{set_no}.csv")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                frame = _f(row.get("frame_num"))
                if not math.isfinite(frame):
                    continue
                raw = (row.get("type") or "").strip()
                hits.append({"set": set_no, "rally": int(_f(row["rally"])),
                             "ball_round": int(_f(row["ball_round"])),
                             "frame30": int(round(frame)), "t": frame / LABEL_FPS,
                             "player": row.get("player"), "type_raw": raw,
                             "stroke7": stroke7_of(raw),
                             "side": hitter_side(_f(row.get("player_location_y")), corners)})
    return sorted(hits, key=lambda h: h["frame30"])


def rally_spans(hits, pre_sec=1.0, post_sec=1.5):
    groups = {}
    for h in hits:
        groups.setdefault((h["set"], h["rally"]), []).append(h)
    spans = []
    for (set_no, rally), hs in groups.items():
        hs = sorted(hs, key=lambda h: h["frame30"])
        spans.append({"set": set_no, "rally": rally, "start": hs[0]["t"] - pre_sec,
                      "end": hs[-1]["t"] + post_sec, "hits": hs})
    return sorted(spans, key=lambda s: s["start"])


def best_offset(label_times, event_times, search_sec=10.0, step_sec=1 / 30, tol_sec=0.1,
                min_frac=0.6, peak_ratio=1.5, exclusion_sec=0.5):
    """Video-time offset o maximising labels t with an event within tol of t + o."""
    labels = sorted(label_times)
    events = sorted(event_times)
    if not labels or not events:
        return {"offset": 0.0, "matched": 0, "runner_up": 0, "matched_frac": 0.0, "accepted": False}
    n_steps = int(round(search_sec / step_sec))
    offsets = [k * step_sec for k in range(-n_steps, n_steps + 1)]
    counts = []
    for o in offsets:
        c = 0
        for t in labels:
            lo = bisect.bisect_left(events, t + o - tol_sec)
            c += lo < len(events) and events[lo] <= t + o + tol_sec
        counts.append(c)
    best_i = max(range(len(offsets)), key=lambda i: counts[i])
    best = offsets[best_i]
    runner = max([c for o, c in zip(offsets, counts) if abs(o - best) > exclusion_sec] or [0])
    frac = counts[best_i] / len(labels)
    accepted = counts[best_i] >= peak_ratio * runner and frac >= min_frac
    return {"offset": round(best, 4), "matched": counts[best_i], "runner_up": runner,
            "matched_frac": round(frac, 4), "accepted": bool(accepted)}


def parse_matrix(text):
    """homography_matrix column -> nested list (kept for completeness; unused by the builder)."""
    return ast.literal_eval(text)
