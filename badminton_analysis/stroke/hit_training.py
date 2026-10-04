"""Pure training helpers for the hit detector (no torch): targets, windows, peak config."""
import numpy as np

from .hit_detector import pick_peaks
from .hit_features import TARGET_HZ
from .hit_metrics import summarize


def label_steps(hit_frames, first_frame, idx):
    idx = np.asarray(idx)
    if idx.size == 0:
        return np.zeros(0, dtype=np.int64)
    native = np.asarray(hit_frames, dtype=np.int64) - int(first_frame)
    keep = (native >= idx[0]) & (native <= idx[-1])
    pos = np.searchsorted(idx, native[keep])
    pos = np.clip(pos, 0, idx.size - 1)
    prev = np.clip(pos - 1, 0, idx.size - 1)
    nearer_prev = np.abs(idx[prev] - native[keep]) <= np.abs(idx[pos] - native[keep])
    return np.where(nearer_prev, prev, pos).astype(np.int64)


def make_targets(n_steps, hit_steps, hitters, sigma=1.0, hitter_radius=2):
    t = np.arange(n_steps)
    hit_t = np.zeros(n_steps, np.float32)
    far_t = np.zeros(n_steps, np.float32)
    mask = np.zeros(n_steps, bool)
    for s, who in zip(np.asarray(hit_steps), np.asarray(hitters)):
        hit_t = np.maximum(hit_t, np.exp(-0.5 * ((t - s) / sigma) ** 2).astype(np.float32))
        if who in (0, 1):
            lo, hi = max(0, s - hitter_radius), min(n_steps, s + hitter_radius + 1)
            mask[lo:hi] = True
            far_t[lo:hi] = float(who)
    return hit_t, far_t, mask


def random_window(n_steps, length, rng):
    if n_steps <= length:
        return 0, n_steps
    start = int(rng.integers(0, n_steps - length + 1))
    return start, start + length


def choose_peak_config(samples, thresholds, spacings_sec, tol_sec=0.2):
    best = None
    for th in thresholds:
        for sp in spacings_sec:
            spacing = max(1, int(round(sp * TARGET_HZ)))
            truth_t, det_t, truth_side, det_side = [], [], [], []
            offset = 0.0
            for hit, far, steps, far_truth in samples:
                peaks = pick_peaks(hit, th, spacing)
                det_t += [offset + p / TARGET_HZ for p in peaks]
                det_side += ["far" if far[p] >= 0.5 else "near" for p in peaks]
                truth_t += [offset + s / TARGET_HZ for s in steps]
                truth_side += [{0: "near", 1: "far"}.get(int(w)) for w in far_truth]
                offset += len(hit) / TARGET_HZ + 10.0       # keep samples apart in time
            s = summarize(truth_t, det_t, tol_sec, truth_side, det_side)
            s.update({"threshold": float(th), "min_spacing_sec": float(sp)})
            if best is None or s["f1"] > best["f1"]:
                best = s
    return best
