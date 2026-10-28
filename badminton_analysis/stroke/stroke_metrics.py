"""Stroke-classification metrics and the owner's done bar (spec 2026-10-22 section 6.2).

Accuracy is over confident predictions at the 7-group level; recall per group counts an
"unknown" prediction as a miss, so abstaining cannot raise it; the unknown rate is over all
matched hits. 12-type accuracy and the confusion matrix are informational.
"""
from collections import defaultdict

import numpy as np

from .stroke_types import GROUPS, STROKE_TYPES, UNKNOWN, group_of

BAR = {"accuracy": 0.85, "recall": 0.75, "unknown": 0.15}
MIN_GROUP_HITS = 20


def score(pairs):
    pairs = list(pairs)
    confident = [(t, p) for t, p in pairs if p != UNKNOWN]
    recall = {}
    for g in GROUPS:
        rows = [(t, p) for t, p in pairs if group_of(t) == g]
        correct = sum(1 for t, p in rows if group_of(p) == g)
        recall[g] = {"n": len(rows), "correct": correct,
                     "recall": round(correct / len(rows), 4) if rows else None}
    confusion = defaultdict(lambda: defaultdict(int))
    for t, p in pairs:
        if p != t:
            confusion[t][p] += 1
    return {
        "matched": len(pairs),
        "confident": len(confident),
        "unknown_rate": round((len(pairs) - len(confident)) / len(pairs), 4) if pairs else None,
        "accuracy": (round(sum(group_of(t) == group_of(p) for t, p in confident) / len(confident), 4)
                     if confident else None),
        "type_accuracy": (round(sum(t == p for t, p in confident) / len(confident), 4)
                          if confident else None),
        "recall": recall,
        "confusion": {t: dict(v) for t, v in confusion.items()},
    }


def check_bar_codes(m):
    """``(met, [code])``: why the owner's bar is not met, as language-neutral codes with numbers."""
    codes = []
    if m["accuracy"] is None or m["accuracy"] < BAR["accuracy"]:
        codes.append({"code": "accuracy", "value": m["accuracy"], "bar": BAR["accuracy"]})
    if m["unknown_rate"] is None or m["unknown_rate"] > BAR["unknown"]:
        codes.append({"code": "unknown", "value": m["unknown_rate"], "bar": BAR["unknown"]})
    for g, r in m["recall"].items():
        if r["n"] < MIN_GROUP_HITS:
            codes.append({"code": "group_few", "group": g, "need": MIN_GROUP_HITS, "have": r["n"]})
        elif r["recall"] < BAR["recall"]:
            codes.append({"code": "group_recall", "group": g, "value": r["recall"], "bar": BAR["recall"]})
    return not codes, codes


def describe_bar_code(c):
    """English text of one ``check_bar_codes`` code (CLI output and logs)."""
    k = c.get("code")
    if k == "accuracy":
        return f"accuracy {c['value']} < {c['bar']}"
    if k == "unknown":
        return f"unknown rate {c['value']} > {c['bar']}"
    if k == "group_few":
        return f"{c['group']}: fewer than {c['need']} test hits ({c['have']})"
    if k == "group_recall":
        return f"{c['group']}: recall {c['value']} < {c['bar']}"
    if k == "no_model":
        return "no model"
    if k == "unreadable":
        return "model unreadable" + (f": {c['detail']}" if c.get("detail") else "")
    return str(k)


def check_bar(m):
    met, codes = check_bar_codes(m)
    return met, [describe_bar_code(c) for c in codes]


def predict_labels(probs, threshold):
    probs = np.asarray(probs)
    if len(probs) == 0:
        return []
    best = probs.argmax(axis=1)
    return [STROKE_TYPES[b] if probs[k, b] >= threshold else UNKNOWN for k, b in enumerate(best)]


def choose_threshold(probs, truth_types, max_unknown=BAR["unknown"]):
    """Threshold maximising the worst group's recall, then accuracy, with unknown <= cap."""
    best_key, best_thr = None, 0.0
    for thr in np.round(np.arange(0.0, 0.96, 0.01), 2):
        m = score(zip(truth_types, predict_labels(probs, thr)))
        if m["unknown_rate"] is None or m["unknown_rate"] > max_unknown:
            continue
        recalls = [r["recall"] for r in m["recall"].values() if r["recall"] is not None]
        key = (min(recalls) if recalls else 0.0, m["accuracy"] or 0.0, -thr)
        if best_key is None or key > best_key:
            best_key, best_thr = key, float(thr)
    return best_thr
