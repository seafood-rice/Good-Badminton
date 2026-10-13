"""Small temporal CNN that scores every 30 Hz step for "a hit happens here".

Dilated residual blocks (kernel 3, dilations 1..2**(levels-1)) see about +-1 s
at levels=4. Two heads: hit score and P(far player hit). Inputs are normalised
inside the model (feat_mean / feat_std buffers), so the weights file is
self-contained. Saved as a state dict plus a JSON sidecar; loaded with
torch.load(weights_only=True).
"""
import hashlib
import json
import os

import torch
from torch import nn

from .hit_features import FEATURE_DIM, FEATURE_SCHEMA, TARGET_HZ

MODEL_SCHEMA = 1
_REQUIRED_META = ("schema", "feature_schema", "feature_dim", "target_hz", "hidden", "levels",
                  "threshold", "min_spacing_sec")


class HitModelError(Exception):
    """The hit-model weights or sidecar are missing or incompatible."""


class _Block(nn.Module):
    def __init__(self, ch, dilation, dropout):
        super().__init__()
        self.conv1 = nn.Conv1d(ch, ch, 3, padding=dilation, dilation=dilation)
        self.conv2 = nn.Conv1d(ch, ch, 3, padding=dilation, dilation=dilation)
        self.norm1 = nn.BatchNorm1d(ch)
        self.norm2 = nn.BatchNorm1d(ch)
        self.drop = nn.Dropout(dropout)

    def forward(self, x):
        y = self.drop(torch.relu(self.norm1(self.conv1(x))))
        y = self.norm2(self.conv2(y))
        return torch.relu(x + y)


class HitTCN(nn.Module):
    def __init__(self, in_ch, hidden=64, levels=4, dropout=0.1):
        super().__init__()
        self.register_buffer("feat_mean", torch.zeros(in_ch))
        self.register_buffer("feat_std", torch.ones(in_ch))
        self.inp = nn.Conv1d(in_ch, hidden, 1)
        self.blocks = nn.Sequential(*[_Block(hidden, 2 ** i, dropout) for i in range(levels)])
        self.head = nn.Conv1d(hidden, 2, 1)

    def forward(self, x):
        x = (x - self.feat_mean) / self.feat_std.clamp_min(1e-6)
        h = self.blocks(torch.relu(self.inp(x.transpose(1, 2))))
        out = self.head(h)
        return out[:, 0], out[:, 1]


def meta_path(weights_path):
    return os.path.splitext(str(weights_path))[0] + ".json"


def save_hit_model(model, meta, weights_path):
    torch.save(model.state_dict(), str(weights_path))
    with open(meta_path(weights_path), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


def load_hit_model(weights_path, device="cpu"):
    sidecar = meta_path(weights_path)
    if not os.path.isfile(sidecar):
        raise HitModelError(f"hit-model sidecar not found: {sidecar}")
    with open(sidecar, encoding="utf-8") as fh:
        meta = json.load(fh)
    missing = [k for k in _REQUIRED_META if k not in meta]
    if missing:
        raise HitModelError(f"hit-model sidecar lacks {missing}")
    if (meta["schema"] != MODEL_SCHEMA or meta["feature_schema"] != FEATURE_SCHEMA
            or meta["feature_dim"] != FEATURE_DIM or float(meta["target_hz"]) != TARGET_HZ):
        raise HitModelError(
            f"incompatible hit model (schema {meta['schema']}, features {meta['feature_schema']}/"
            f"{meta['feature_dim']} at {meta['target_hz']} Hz)")
    model = HitTCN(meta["feature_dim"], int(meta["hidden"]), int(meta["levels"]), dropout=0.0)
    state = torch.load(str(weights_path), map_location=device, weights_only=True)
    model.load_state_dict(state)
    return model.to(device).eval(), meta


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
