"""Train the learned hit detector (spec 7).

Usage:
  python scripts/train_hit_detector.py --dataset data/hit_dataset \
      --val-match Kento_MOMOTA_Viktor_AXELSEN_Malaysia_Masters_2020_Finals \
      --out weights/hit-detector.pt [--epochs 40] [--init weights/hit-detector.pt] [--device cuda]

Every data/hit_dataset/<match>/*.npz is one labelled rally (scripts/build_hit_dataset.py).
The validation match is held out entirely; the decision threshold and peak spacing are
chosen on it. Writes the state dict plus a JSON sidecar (hit_model.save_hit_model).
"""
import argparse
import glob
import os
import sys
import time
from collections import namedtuple

import numpy as np
import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke import hit_model as hm  # noqa: E402
from badminton_analysis.stroke.hit_augment import augment, jittered_index  # noqa: E402
from badminton_analysis.stroke.hit_detector import predict_scores  # noqa: E402
from badminton_analysis.stroke.hit_features import (  # noqa: E402
    FEATURE_DIM, FEATURE_SCHEMA, TARGET_HZ, compute_features)
from badminton_analysis.stroke.hit_signals import RawSignals  # noqa: E402
from badminton_analysis.stroke.hit_training import (  # noqa: E402
    choose_peak_config, label_steps, make_targets, random_window)

Sample = namedtuple("Sample", "sig hit_frames hitter match")
THRESHOLDS = [round(x, 2) for x in np.arange(0.2, 0.85, 0.05)]
SPACINGS_SEC = [0.2, 0.27, 0.33, 0.4]


def load_dataset(dataset_dir):
    samples = []
    for path in sorted(glob.glob(os.path.join(dataset_dir, "*", "*.npz"))):
        if path.endswith(".part.npz"):      # a hard kill during the dataset build can leave one
            continue
        with np.load(path) as z:
            sig = RawSignals(float(z["fps"]), int(z["first_frame"]), z["shuttle"].astype(np.float32),
                             z["keypoints"].astype(np.float32), z["court_corners"].astype(np.float32),
                             tuple(int(v) for v in z["image_size"]))
            samples.append(Sample(sig, z["hit_frames"].astype(np.int64), z["hitter"].astype(np.int64),
                                  str(z["match"])))
    return samples


def _labels_for(sample, sig, idx):
    """Hit steps and their hitter codes, dropping hits outside the resampled span."""
    native = sample.hit_frames - sig.first_frame
    inside = ((native >= idx[0]) & (native <= idx[-1])) if len(idx) else np.zeros(len(native), bool)
    return label_steps(sample.hit_frames, sig.first_frame, idx), sample.hitter[inside]


def _example(sample, rng, augmented):
    sig = augment(sample.sig, rng) if augmented else sample.sig
    idx = jittered_index(sig.n_frames, sig.fps, rng) if augmented else None
    feats, idx = compute_features(sig, idx)
    steps, hitters = _labels_for(sample, sig, idx)
    hit_t, far_t, mask = make_targets(len(idx), steps, hitters)
    return feats, hit_t, far_t, mask


def _batch(examples, window, rng):
    xs, hs, fs, ms = [], [], [], []
    for feats, hit_t, far_t, mask in examples:
        s, e = random_window(len(feats), window, rng)
        pad = window - (e - s)
        xs.append(np.pad(feats[s:e], ((0, pad), (0, 0))))
        hs.append(np.pad(hit_t[s:e], (0, pad)))
        fs.append(np.pad(far_t[s:e], (0, pad)))
        ms.append(np.pad(mask[s:e], (0, pad)))
    return [torch.from_numpy(np.stack(a)) for a in (xs, hs, fs, ms)]


def _evaluate(model, samples):
    rows = []
    for smp in samples:
        feats, idx = compute_features(smp.sig)
        hit, far = predict_scores(model, feats)
        steps, hitters = _labels_for(smp, smp.sig, idx)
        rows.append((hit, far, steps, hitters))
    return choose_peak_config(rows, THRESHOLDS, SPACINGS_SEC)


def train(train_samples, val_samples, epochs=40, hidden=64, levels=4, window=256, batch=32,
          lr=1e-3, seed=0, device=None, log=print, init=None):
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    if init:
        model, init_meta = hm.load_hit_model(init, device=device)
        hidden, levels = int(init_meta["hidden"]), int(init_meta["levels"])
        model.train()
    else:
        model = hm.HitTCN(FEATURE_DIM, hidden=hidden, levels=levels).to(device)
        clean = np.concatenate([compute_features(s.sig)[0] for s in train_samples])
        model.feat_mean.copy_(torch.from_numpy(clean.mean(0)))
        model.feat_std.copy_(torch.from_numpy(clean.std(0) + 1e-3))
    pos = sum(len(s.hit_frames) for s in train_samples)
    steps = sum(len(compute_features(s.sig)[1]) for s in train_samples)
    pos_weight = torch.tensor(max(1.0, (steps - pos) / max(pos, 1) / 4.0), device=device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, epochs))
    best, best_state = None, None
    for epoch in range(1, epochs + 1):
        model.train()
        order = rng.permutation(len(train_samples))
        losses = []
        for start in range(0, len(order), batch):
            exs = [_example(train_samples[i], rng, augmented=True) for i in order[start:start + batch]]
            x, ht, ft, m = (t.to(device) for t in _batch(exs, window, rng))
            hit_logit, far_logit = model(x.float())
            loss = F.binary_cross_entropy_with_logits(hit_logit, ht.float(), pos_weight=pos_weight)
            if m.any():
                loss = loss + F.binary_cross_entropy_with_logits(far_logit[m], ft[m].float())
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss))
        sched.step()
        if epoch % 5 == 0 or epoch == epochs:
            model.eval()
            metrics = _evaluate(model, val_samples)
            log(f"epoch {epoch}: loss {np.mean(losses):.4f} val recall {metrics['recall']:.3f} "
                f"precision {metrics['precision']:.3f} hitter {metrics['hitter_correct']}")
            if best is None or metrics["f1"] > best["f1"]:
                best = metrics
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.eval()
    meta = {"schema": hm.MODEL_SCHEMA, "feature_schema": FEATURE_SCHEMA, "feature_dim": FEATURE_DIM,
            "target_hz": TARGET_HZ, "hidden": hidden, "levels": levels,
            "threshold": best["threshold"], "min_spacing_sec": best["min_spacing_sec"],
            "train_matches": sorted({s.match for s in train_samples}),
            "val_matches": sorted({s.match for s in val_samples}),
            "val_metrics": {k: best[k] for k in ("truth", "detected", "matched", "recall",
                                                  "precision", "f1", "hitter_correct")},
            "epochs": epochs, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return model, meta


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dataset", required=True, action="append")
    p.add_argument("--val-match", required=True)
    p.add_argument("--out", default=os.path.join(ROOT, "weights", "hit-detector.pt"))
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--init")
    p.add_argument("--device")
    a = p.parse_args(argv)
    samples = [s for d in a.dataset for s in load_dataset(d)]
    train_s = [s for s in samples if s.match != a.val_match]
    val_s = [s for s in samples if s.match == a.val_match]
    if not train_s or not val_s:
        raise SystemExit(f"need training and validation rallies (val match {a.val_match!r})")
    print(f"{len(train_s)} training rallies, {len(val_s)} validation rallies")
    model, meta = train(train_s, val_s, epochs=a.epochs, device=a.device, init=a.init)
    hm.save_hit_model(model.cpu(), meta, a.out)
    print(f"saved {a.out} (+ {hm.meta_path(a.out)}): val {meta['val_metrics']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
