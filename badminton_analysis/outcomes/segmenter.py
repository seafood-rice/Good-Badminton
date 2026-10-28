"""The learned point-end segmenter (spec 5.2): logistic regression over hit gaps.

numpy only and deterministic (full-batch gradient descent from zero weights). The
model is a JSON-serialisable dict; ``segments`` turns predicted point ends into
rally segments in the ``rally_segments.json`` row shape.
"""
import datetime
import json
import math
from pathlib import Path

import numpy as np

from . import segment_features as sf

SCHEMA = 1
SERVE_PAD_SEC = 2.0
END_PAD_SEC = 1.0
SWING_FRAC = 0.25          # a lone hit needs swing activity above this share of the run's p99
ITERATIONS = 3000
LEARNING_RATE = 0.2
L2 = 0.01


def _matrix(features, rows):
    return np.array([[float(r.get(name, 0.0)) for name in features] for r in rows], dtype=float)


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -40.0, 40.0)))


def train(examples, features=None):
    """The model dict from ``[(features_dict, label 0|1)]``; ValueError without both classes."""
    features = list(features or sf.FEATURES)
    rows = [(f, int(y)) for f, y in examples if y in (0, 1)]
    y = np.array([y for _, y in rows], dtype=float)
    if len(rows) == 0 or y.min() == y.max():
        raise ValueError("training needs examples of both classes")
    x = _matrix(features, [f for f, _ in rows])
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-9] = 1.0
    z = (x - mean) / scale
    pos, neg = y.sum(), len(y) - y.sum()
    w_row = np.where(y == 1, len(y) / (2.0 * pos), len(y) / (2.0 * neg))
    coef, intercept = np.zeros(z.shape[1]), 0.0
    for _ in range(ITERATIONS):
        err = (_sigmoid(z @ coef + intercept) - y) * w_row
        coef -= LEARNING_RATE * (z.T @ err / len(y) + L2 * coef)
        intercept -= LEARNING_RATE * float(err.mean())
    return {"schema": SCHEMA, "features": features,
            "mean": [float(v) for v in mean], "scale": [float(v) for v in scale],
            "coef": [float(v) for v in coef], "intercept": float(intercept),
            "threshold": 0.5,
            "version": datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")}


def predict(model, gap_list):
    """The point-end probability of each gap."""
    if not gap_list:
        return []
    x = _matrix(model["features"], [g.features for g in gap_list])
    z = (x - np.array(model["mean"])) / np.array(model["scale"])
    return [float(p) for p in _sigmoid(z @ np.array(model["coef"]) + model["intercept"])]


def load(path):
    """The model dict, or None when missing, unreadable or of another schema."""
    try:
        with open(Path(path), encoding="utf-8") as f:
            model = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None
    if not isinstance(model, dict) or model.get("schema") != SCHEMA:
        return None
    names = model.get("features")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        return None
    for key in ("mean", "scale", "coef"):
        v = model.get(key)
        if not (isinstance(v, list) and len(v) == len(names)
                and all(isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a) for a in v)):
            return None
    if any(a == 0 for a in model["scale"]):
        return None
    for key in ("intercept", "threshold"):
        v = model.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
    return model


def _lone_hit_swings(inputs, t):
    """True when the swing signal shows a swing within 0.5 s of ``t``."""
    if not inputs.swing:
        return False
    times = np.array([a for a, _ in inputs.swing])
    act = np.array([b for _, b in inputs.swing])
    cut = SWING_FRAC * float(np.percentile(act, 99))
    lo, hi = np.searchsorted(times, [t - 0.5, t + 0.5], side="left")
    return bool(cut > 0 and hi > lo and act[lo:hi].max() > cut)


def segments(inputs, model):
    """Rally segments from the predicted point ends; [] without hits.

    ``[{"id", "start_frame", "end_frame", "start_sec", "end_sec", "degraded": False,
    "p_point"}]``. A run of hits between point ends becomes ``first hit - 2 s`` ..
    ``last hit + 1 s``, clipped to the video; where two pads meet, the gap between the
    rallies is split 1:2 (end pad : serve pad). A run of one hit without swing activity
    is dropped.
    """
    hits = list(inputs.hits or [])
    if not hits:
        return []
    probs = predict(model, sf.gaps(inputs))
    cut = float(model.get("threshold", 0.5))
    runs, first = [], 0
    for i in range(len(hits)):
        last = i == len(hits) - 1
        if last or probs[i] >= cut:
            before = probs[first - 1] if first > 0 else None
            after = probs[i] if not last else None
            near = [p for p in (before, after) if p is not None]
            runs.append((first, i, sum(near) / len(near) if near else 1.0))
            first = i + 1
    kept = []
    for a, b, p in runs:
        if a == b and not _lone_hit_swings(inputs, hits[a][0]):
            continue
        kept.append([hits[a][0] - SERVE_PAD_SEC, hits[b][0] + END_PAD_SEC, hits[a][0], hits[b][0], p])
    limit = inputs.end_sec
    for k, row in enumerate(kept):
        if k > 0 and kept[k - 1][1] > row[0]:
            prev_last, first_hit = kept[k - 1][3], row[2]
            boundary = prev_last + (first_hit - prev_last) * END_PAD_SEC / (SERVE_PAD_SEC + END_PAD_SEC)
            kept[k - 1][1] = max(prev_last, min(kept[k - 1][1], boundary))
            row[0] = min(first_hit, max(row[0], boundary))
        row[0] = max(0.0, row[0])
        if limit is not None:
            row[1] = min(row[1], max(limit, row[3]))
    fps = float(inputs.fps)
    return [{"id": n, "start_frame": int(round(r[0] * fps)), "end_frame": int(round(r[1] * fps)),
             "start_sec": float(r[0]), "end_sec": float(r[1]), "degraded": False, "p_point": float(r[4])}
            for n, r in enumerate(kept, 1)]


def point_end_f1(pred_ends, true_ends, tol=1.5):
    """Precision / recall / F1 with one-to-one matching of ends within ``tol`` seconds."""
    pred, true = sorted(pred_ends), sorted(true_ends)
    pairs = sorted((abs(p - t), i, j) for i, p in enumerate(pred) for j, t in enumerate(true)
                   if abs(p - t) <= tol)
    used_p, used_t, tp = set(), set(), 0
    for _d, i, j in pairs:
        if i in used_p or j in used_t:
            continue
        used_p.add(i)
        used_t.add(j)
        tp += 1
    fp, fn = len(pred) - tp, len(true) - tp
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(true) if true else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}
