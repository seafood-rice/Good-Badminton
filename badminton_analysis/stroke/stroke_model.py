"""Stroke classifier network: a small dilated temporal CNN over a per-hit window.

Same building blocks and file conventions as hit_model.py: input normalisation lives in
buffers, weights are a state dict plus a JSON sidecar, loaded with weights_only=True.
Output: logits over stroke_types.STROKE_TYPES (12). "unknown" is not a class; it is
decided by the sidecar's unknown_threshold on the top softmax probability.
"""
import json
import os

import numpy as np
import torch
from torch import nn

from .hit_features import FEATURE_SCHEMA, TARGET_HZ
from .hit_model import _Block, file_sha256, meta_path  # noqa: F401  (file_sha256 re-exported)
from .stroke_types import ATTRIBUTES, STROKE_TYPES
from .stroke_windows import INPUT_DIM, POST_STEPS, PRE_STEPS

MODEL_SCHEMA = 2
LOADABLE_SCHEMAS = (1, 2)       # schema 1 = no attribute heads
ATTR_MIN_CONFIDENCE = 0.6
_REQUIRED = ("schema", "feature_schema", "input_dim", "target_hz", "window_pre_steps",
             "window_post_steps", "stroke_types", "hidden", "levels", "unknown_threshold")


class StrokeModelError(Exception):
    """The stroke-model weights or sidecar are missing or incompatible."""


class StrokeNet(nn.Module):
    def __init__(self, in_ch, n_classes=len(STROKE_TYPES), hidden=64, levels=3, dropout=0.1,
                 n_attrs=0):
        super().__init__()
        self.register_buffer("feat_mean", torch.zeros(in_ch))
        self.register_buffer("feat_std", torch.ones(in_ch))
        self.inp = nn.Conv1d(in_ch, hidden, 1)
        self.blocks = nn.Sequential(*[_Block(hidden, 2 ** i, dropout) for i in range(levels)])
        self.drop = nn.Dropout(dropout)
        self.head = nn.Linear(2 * hidden, n_classes)
        self.n_attrs = int(n_attrs)
        if self.n_attrs > 0:
            self.attr_head = nn.Linear(2 * hidden, self.n_attrs)

    def forward_all(self, x):
        """(type logits, attribute logits or None when the model has no attribute heads)."""
        x = (x - self.feat_mean) / self.feat_std.clamp_min(1e-6)
        h = self.blocks(torch.relu(self.inp(x.transpose(1, 2))))
        pooled = self.drop(torch.cat([h.mean(dim=2), h.amax(dim=2)], dim=1))
        return self.head(pooled), (self.attr_head(pooled) if self.n_attrs > 0 else None)

    def forward(self, x):
        return self.forward_all(x)[0]


def base_meta(hidden, levels, unknown_threshold, attributes=()):
    return {"schema": MODEL_SCHEMA, "feature_schema": FEATURE_SCHEMA, "input_dim": INPUT_DIM,
            "target_hz": TARGET_HZ, "window_pre_steps": PRE_STEPS,
            "window_post_steps": POST_STEPS, "stroke_types": list(STROKE_TYPES),
            "hidden": hidden, "levels": levels, "unknown_threshold": float(unknown_threshold),
            "validated_on_owner_footage": False, "attributes": list(attributes),
            "attribute_min_confidence": ATTR_MIN_CONFIDENCE}


def save_stroke_model(model, meta, path):
    torch.save(model.state_dict(), str(path))
    with open(meta_path(path), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)


def load_stroke_model(path, device="cpu"):
    sidecar = meta_path(path)
    if not os.path.isfile(sidecar):
        raise StrokeModelError(f"stroke-model sidecar not found: {sidecar}")
    with open(sidecar, encoding="utf-8") as fh:
        meta = json.load(fh)
    missing = [k for k in _REQUIRED if k not in meta]
    if missing:
        raise StrokeModelError(f"stroke-model sidecar lacks {missing}")
    expected = base_meta(meta["hidden"], meta["levels"], meta["unknown_threshold"])
    if meta["schema"] not in LOADABLE_SCHEMAS:
        raise StrokeModelError(f"incompatible stroke model: schema is {meta['schema']!r}, "
                               f"this code needs one of {LOADABLE_SCHEMAS!r}")
    attributes = []
    if meta["schema"] >= 2:
        attributes = meta.get("attributes", [])
        if (not isinstance(attributes, list) or len(set(map(str, attributes))) != len(attributes)
                or not all(isinstance(a, str) and a in ATTRIBUTES for a in attributes)):
            raise StrokeModelError(f"stroke-model sidecar attributes invalid: {attributes!r}")
        floor = meta.get("attribute_min_confidence", ATTR_MIN_CONFIDENCE)
        if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0.5 <= floor <= 1:
            raise StrokeModelError(f"stroke-model sidecar attribute_min_confidence invalid: {floor!r}")
    meta["attributes"] = attributes
    meta.setdefault("attribute_min_confidence", ATTR_MIN_CONFIDENCE)
    for key in ("feature_schema", "input_dim", "target_hz", "window_pre_steps",
                "window_post_steps", "stroke_types"):
        if meta[key] != expected[key]:
            raise StrokeModelError(f"incompatible stroke model: {key} is {meta[key]!r}, "
                                   f"this code needs {expected[key]!r}")
    model = StrokeNet(INPUT_DIM, hidden=int(meta["hidden"]), levels=int(meta["levels"]), dropout=0.0,
                      n_attrs=len(attributes))
    try:
        model.load_state_dict(torch.load(str(path), map_location=device, weights_only=True))
    except Exception as exc:
        raise StrokeModelError(f"stroke-model weights unreadable: {exc}") from exc
    return model.to(device).eval(), meta


def predict_probs(model, X, batch=256):
    X = np.asarray(X, np.float32)
    if len(X) == 0:
        return np.zeros((0, len(STROKE_TYPES)), np.float64)
    device = next(model.parameters()).device
    model.eval()
    out = []
    with torch.no_grad():
        for s in range(0, len(X), batch):
            logits = model(torch.from_numpy(X[s:s + batch]).to(device))
            out.append(torch.softmax(logits, dim=1).cpu().numpy())
    return np.concatenate(out).astype(np.float64)


def predict_attrs(model, X, batch=256):
    """Sigmoid probabilities (n, n_attrs) in the model's head order, or None without heads."""
    n_attrs = getattr(model, "n_attrs", 0)
    if n_attrs <= 0:
        return None
    X = np.asarray(X, np.float32)
    if len(X) == 0:
        return np.zeros((0, n_attrs), np.float64)
    device = next(model.parameters()).device
    model.eval()
    out = []
    with torch.no_grad():
        for s in range(0, len(X), batch):
            _, attr = model.forward_all(torch.from_numpy(X[s:s + batch]).to(device))
            out.append(torch.sigmoid(attr).cpu().numpy())
    return np.concatenate(out).astype(np.float64)
