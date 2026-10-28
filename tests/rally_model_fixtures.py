"""Synthetic labelled matches for the rally-model tests (measurement, retrain, re-segmenting).

A match is a list of rallies of three hits one second apart, ten seconds between rallies. The
winner hits last, so the last-hitter signal is right every time; the owner's point end sits just
after the last hit. Labels live in ``labels_dir/<stem>.json``, runs in ``results_dir/<stem>/``.
"""
import json
from pathlib import Path

from tests.outcome_fixtures import FPS, make_run

STEP = 1.0
PAUSE = 10.0


def winners_for(n, seed):
    return ["upper" if (i * 7 + seed) % 3 else "lower" for i in range(n)]


def rally_times(n, offset=5.0):
    """``[(first_hit, last_hit)]`` for ``n`` rallies."""
    out, t = [], offset
    for _ in range(n):
        out.append((t, t + 2 * STEP))
        t += 2 * STEP + PAUSE
    return out


def hits_for(times, winners):
    """Three alternating hits per rally, the winner's side first and last."""
    hits = []
    for (first, _last), w in zip(times, winners):
        loser = "lower" if w == "upper" else "upper"
        hits += [(first, w), (first + STEP, loser), (first + 2 * STEP, w)]
    return hits


def write_detections(out_dir, seconds):
    lines = [json.dumps({"frame": f, "players": {}}) for f in range(1, int(seconds * FPS) + 1)]
    (Path(out_dir) / "detections.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def label_rows(times, winners):
    return [{"start_sec": first - 2.0, "end_sec": last + 0.6, "verdict": w, "outcome": None, "added": False}
            for (first, last), w in zip(times, winners)]


def write_labels(labels_dir, stem, times, winners, extra=()):
    labels_dir = Path(labels_dir)
    labels_dir.mkdir(parents=True, exist_ok=True)
    rows = label_rows(times, winners) + list(extra)
    (labels_dir / f"{stem}.json").write_text(json.dumps(
        {"schema": 1, "revision": 1, "labels": rows}), encoding="utf-8")


def make_match(results_dir, labels_dir, stem, n, seed=0, offset=5.0, source="learned", labelled=True):
    """Write one synthetic labelled match; returns ``(times, winners)``."""
    times = rally_times(n, offset)
    winners = winners_for(n, seed)
    out = Path(results_dir) / stem
    segs = [(i + 1, first - 2.0, last + 1.0) for i, (first, last) in enumerate(times)]
    make_run(out, segs, hits=hits_for(times, winners))
    if source != "learned":
        doc = json.loads((out / "hits.json").read_text())
        doc["source"] = source
        (out / "hits.json").write_text(json.dumps(doc), encoding="utf-8")
    write_detections(out, times[-1][1] + 6.0)
    if labelled:
        write_labels(labels_dir, stem, times, winners)
    return times, winners
