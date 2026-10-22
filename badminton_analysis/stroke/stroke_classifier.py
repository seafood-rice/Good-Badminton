# badminton_analysis/stroke/stroke_classifier.py
"""The app's one stroke-identification mechanism (spec 2026-10-22 section 4.2).

classify_hits labels every hit of the shared hit list with a stroke type or "unknown";
build_payload makes the schema-2 strokes.json every consumer reads. There is no fallback
classifier: without a usable model every hit is "unknown" and classifier is null.
"""
from collections import Counter

from .stroke_model import StrokeModelError, file_sha256, load_stroke_model, predict_probs  # noqa: F401
from .stroke_types import STROKE_TYPES, UNKNOWN, group_of
from .stroke_windows import extract

STROKES_SCHEMA = 2


def load_classifier(path):
    model, meta = load_stroke_model(path)
    info = {"version": meta.get("created_utc") or "unversioned",
            "weights_sha256": file_sha256(path),
            "validated_on_owner_footage": bool(meta.get("validated_on_owner_footage")),
            "unknown_threshold": float(meta["unknown_threshold"])}
    return model, meta, info


def _label(hit, stroke, confidence):
    out = {"frame": int(hit["frame"]), "hitter": hit["hitter"], "stroke": stroke,
           "group": group_of(stroke), "confidence": confidence}
    if hit.get("confidence") is not None:
        out["hit_confidence"] = float(hit["confidence"])
    return out


def unknown_labels(hits):
    return sorted((_label(h, UNKNOWN, None) for h in hits), key=lambda l: l["frame"])


def classify_hits(sig, hits, model, meta):
    hits = list(hits)
    X, ok = extract(sig, hits)
    probs = predict_probs(model, X[ok]) if ok.any() else None
    thr = float(meta["unknown_threshold"])
    labels, j = [], 0
    for k, h in enumerate(hits):
        if not ok[k]:
            labels.append(_label(h, UNKNOWN, 0.0))
            continue
        p = probs[j]
        j += 1
        best = int(p.argmax())
        conf = round(float(p[best]), 4)
        labels.append(_label(h, STROKE_TYPES[best] if conf >= thr else UNKNOWN, conf))
    return sorted(labels, key=lambda l: l["frame"])


def build_payload(labels, info, hit_source, shuttle_source):
    return {"schema": STROKES_SCHEMA, "classifier": info, "strokes": labels,
            "distribution": dict(Counter(l["stroke"] for l in labels)),
            "groups": dict(Counter(l["group"] for l in labels if l["group"])),
            "hit_source": hit_source, "shuttle_source": shuttle_source}


def is_current(payload):
    return isinstance(payload, dict) and payload.get("schema") == STROKES_SCHEMA
