"""Learned hit detection: features -> per-step scores -> peaks -> native-frame hits."""
import numpy as np
import torch

from .hit_features import TARGET_HZ, compute_features

MIN_SHUTTLE_COVERAGE = 0.5


def shuttle_coverage(sig, in_play_frames):
    """Fraction of in-play frames (1-based numbers) with a shuttle position."""
    lo, hi = sig.first_frame, sig.first_frame + sig.n_frames
    idx = np.asarray([int(f) - lo for f in in_play_frames if lo <= int(f) < hi], dtype=np.int64)
    if idx.size == 0:
        return 0.0
    return float(np.isfinite(sig.shuttle[idx]).all(axis=1).mean())


def pick_peaks(scores, threshold, min_spacing):
    """Local maxima >= threshold, strongest first, at least min_spacing steps apart."""
    s = np.asarray(scores, dtype=np.float64)
    if s.size == 0:
        return []
    left = np.r_[-np.inf, s[:-1]]
    right = np.r_[s[1:], -np.inf]
    cand = np.where((s >= threshold) & (s >= left) & (s > right))[0]
    kept = []
    for i in cand[np.argsort(-s[cand], kind="stable")]:
        if all(abs(int(i) - j) >= min_spacing for j in kept):
            kept.append(int(i))
    return sorted(kept)


def predict_scores(model, feats, chunk=2048, context=64):
    """Sigmoid hit and far-player probabilities per step, inferred in overlapping chunks."""
    k = len(feats)
    hit = np.zeros(k, dtype=np.float64)
    far = np.zeros(k, dtype=np.float64)
    device = next(model.parameters()).device
    model.eval()
    with torch.no_grad():
        for start in range(0, k, chunk):
            stop = min(k, start + chunk)
            lo, hi = max(0, start - context), min(k, stop + context)
            x = torch.from_numpy(np.ascontiguousarray(feats[lo:hi])).float().unsqueeze(0).to(device)
            h, f = model(x)
            a, b = start - lo, start - lo + (stop - start)
            hit[start:stop] = torch.sigmoid(h)[0, a:b].cpu().numpy()
            far[start:stop] = torch.sigmoid(f)[0, a:b].cpu().numpy()
    return hit, far


def detect_hits(sig, model, meta):
    feats, idx = compute_features(sig)
    # Context covers the model's receptive field: a chunk edge must not change a score.
    context = 2 * (2 ** int(meta.get("levels", 4)) - 1) + 4
    hit, far = predict_scores(model, feats, context=context)
    spacing = max(1, int(round(float(meta["min_spacing_sec"]) * TARGET_HZ)))
    return [{"frame": sig.frame_number(idx[k]),
             "hitter": "upper" if far[k] >= 0.5 else "lower",
             "confidence": round(float(hit[k]), 4)}
            for k in pick_peaks(hit, float(meta["threshold"]), spacing)]
