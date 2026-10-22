"""Per-hit input windows for the stroke classifier (spec 2026-10-22 section 4.1).

A window is PRE_STEPS before to POST_STEPS after the hit at TARGET_HZ, cut from the hit
detector's features. Player blocks are reordered hitter-first, so one model serves near
and far hits; a final channel flags a far-player (upper) hitter. Steps outside the signal
are zero (every feature's "missing" value).
"""
import numpy as np

from .hit_features import FEATURE_DIM, PLAYER_DIM, SHUTTLE_DIM, compute_features

PRE_STEPS = 15
POST_STEPS = 30
WINDOW_STEPS = PRE_STEPS + 1 + POST_STEPS
INPUT_DIM = FEATURE_DIM + 1
MIN_POST_SHUTTLE = 0.5
POSE_RADIUS = 2
_VISIBLE = SHUTTLE_DIM - 1                   # shuttle "visible" flag column
_P0 = slice(SHUTTLE_DIM, SHUTTLE_DIM + PLAYER_DIM)
_P1 = slice(SHUTTLE_DIM + PLAYER_DIM, FEATURE_DIM)
_HITTER_VALID = SHUTTLE_DIM + PLAYER_DIM - 1  # block 0's pose-valid flag, after reordering


def nearest_step(idx, native):
    """Index of the TARGET_HZ step whose native frame index is nearest ``native``."""
    idx = np.asarray(idx)
    pos = int(np.clip(np.searchsorted(idx, native), 0, len(idx) - 1))
    if pos > 0 and abs(int(idx[pos - 1]) - native) <= abs(int(idx[pos]) - native):
        pos -= 1
    return pos


def window(feats, step, hitter):
    out = np.zeros((WINDOW_STEPS, INPUT_DIM), np.float32)
    lo, hi = step - PRE_STEPS, step + POST_STEPS + 1
    src_lo, src_hi = max(0, lo), min(len(feats), hi)
    if src_lo < src_hi:
        out[src_lo - lo:src_hi - lo, :FEATURE_DIM] = feats[src_lo:src_hi]
    if hitter == "upper":
        p0 = out[:, _P0].copy()
        out[:, _P0] = out[:, _P1]
        out[:, _P1] = p0
        out[:, -1] = 1.0
    return out


def has_signal(win):
    post = win[PRE_STEPS + 1:, _VISIBLE]
    if post.mean() < MIN_POST_SHUTTLE:
        return False
    near = win[PRE_STEPS - POSE_RADIUS:PRE_STEPS + POSE_RADIUS + 1, _HITTER_VALID]
    return bool(near.max() > 0)


def extract(sig, hits, idx=None):
    hits = list(hits)
    X = np.zeros((len(hits), WINDOW_STEPS, INPUT_DIM), np.float32)
    ok = np.zeros(len(hits), bool)
    if not hits:
        return X, ok
    feats, idx = compute_features(sig, idx)
    if len(idx) == 0:
        return X, ok
    for k, h in enumerate(hits):
        step = nearest_step(idx, int(h["frame"]) - sig.first_frame)
        X[k] = window(feats, step, h.get("hitter"))
        ok[k] = has_signal(X[k])
    return X, ok
