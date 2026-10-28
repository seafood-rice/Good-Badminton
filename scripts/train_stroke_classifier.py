"""Train the stroke classifier (spec 2026-10-22 section 4.1).

Usage:
  python scripts/train_stroke_classifier.py --dataset data/hit_dataset \
      --val-match Pusarla_V._Sindhu_Pornpawee_Chochuwong_HSBC_BWF_WORLD_TOUR_FINALS_2020_QuarterFinals \
      [--dji-labels outputs/dji-stroke-labels/LABELS.md --results-dir outputs [--calib-video NAME]]       [--stroke-labels data/stroke_labels] \
      [--epochs 60] [--device cuda] [--out weights/stroke-classifier.pt]

Training rallies come from the hit dataset (ShuttleSet stroke_raw mapped to the 12 types)
plus the DJI sheet's train split and the owner's stroke labels (train-split files of
--stroke-labels, match names ``owner:<stem>``). The DJI and owner test splits are never loaded. The unknown
threshold is tuned on the validation match plus any --calib-video DJI videos, which are
held out of training.
"""
import argparse
import glob
import os
import sys
import time
from collections import Counter, namedtuple

import numpy as np
import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke import owner_labels as ol  # noqa: E402
from badminton_analysis.stroke import stroke_classifier as sc  # noqa: E402
from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402
from badminton_analysis.stroke import stroke_model as smod  # noqa: E402
from badminton_analysis.stroke.hit_augment import augment, jittered_index  # noqa: E402
from badminton_analysis.stroke.hit_signals import RawSignals, load_signals  # noqa: E402
from badminton_analysis.stroke.stroke_metrics import (  # noqa: E402
    choose_threshold, predict_labels, score)
from badminton_analysis.stroke.stroke_types import STROKE_TYPES, type_of_raw  # noqa: E402
from badminton_analysis.stroke.stroke_windows import INPUT_DIM, extract  # noqa: E402

Rally = namedtuple("Rally", "sig hits match")
_SIDES = {0: "lower", 1: "upper"}


def load_shuttleset(dataset_dir):
    rallies = []
    for path in sorted(glob.glob(os.path.join(dataset_dir, "*", "*.npz"))):
        if path.endswith(".part.npz"):
            continue
        with np.load(path) as z:
            if "stroke_raw" not in z.files:
                continue
            sig = RawSignals(float(z["fps"]), int(z["first_frame"]), z["shuttle"].astype(np.float32),
                             z["keypoints"].astype(np.float32), z["court_corners"].astype(np.float32),
                             tuple(int(v) for v in z["image_size"]))
            hits = []
            for f, who, raw in zip(z["hit_frames"], z["hitter"], z["stroke_raw"]):
                stroke = type_of_raw(str(raw))
                if stroke is None or int(who) not in _SIDES:
                    continue
                hits.append({"frame": int(f), "hitter": _SIDES[int(who)], "stroke": stroke})
            if hits:
                rallies.append(Rally(sig, hits, str(z["match"])))
    return rallies


def load_dji(sheet_path, results_dir, split="train", only=None):
    if split != "train":
        raise ValueError("the DJI test split is scored by score_strokes.py, never trained on")
    with open(sheet_path, encoding="utf-8") as fh:
        sheet = sl.parse_sheet(fh.read())
    rallies = []
    for name, entry in sheet.items():
        if entry["split"] != "train" or (only is not None and name not in only):
            continue
        labelled = list(sl.labelled_hits(entry))
        hits = [{"frame": h["frame"], "hitter": h["hitter"], "stroke": h["stroke"]}
                for h in labelled if h["hitter"] and h["frame"]]
        if len(labelled) > len(hits):
            print(f"{name}: skipped {len(labelled) - len(hits)} labelled row(s) without player or frame")
        if hits:
            sig = load_signals(os.path.join(results_dir, name, "hit_signals.npz"))
            rallies.append(Rally(sig, hits, f"dji:{name}"))
    return rallies


def _owner_hits(result_dir, doc):
    """The owner's trainable hits (label + added) for one result, as ``apply`` sees them."""
    fps = ol._fps(result_dir)
    if fps is None:
        return []
    strokes_doc = ol._json(os.path.join(result_dir, "strokes.json"))
    if not isinstance(strokes_doc, dict) or not isinstance(strokes_doc.get("strokes"), list):
        hits_doc = ol._json(os.path.join(result_dir, "hits.json"))
        raw = hits_doc.get("hits") if isinstance(hits_doc, dict) else None
        raw = [h for h in raw or [] if isinstance(h, dict) and isinstance(h.get("frame"), int)
               and h.get("hitter")]
        strokes_doc = {"schema": sc.STROKES_SCHEMA, "strokes": sc.unknown_labels(raw)}
    applied = ol.apply(strokes_doc, doc, fps)
    return [{"frame": int(s["frame"]), "hitter": s["hitter"], "stroke": s["stroke"]}
            for s in applied["strokes"] if s.get("source") == "owner"]


def load_owner(labels_dir, results_dir):
    """Rallies from the owner's train-split stroke labels (the test split is never read)."""
    rallies, outside = [], 0
    if not os.path.isdir(labels_dir):
        return rallies
    for name in sorted(os.listdir(labels_dir)):
        if not name.endswith(".json"):
            continue
        stem = name[:-5]
        doc = ol.load(os.path.join(labels_dir, name))
        if doc is None or doc["split"] != "train":
            continue
        result_dir = os.path.join(results_dir, stem)
        signals = os.path.join(result_dir, "hit_signals.npz")
        if not os.path.isfile(signals):
            continue
        hits = _owner_hits(result_dir, doc)
        if not hits:
            continue
        sig = load_signals(signals)
        inside = [h for h in hits if sig.first_frame <= h["frame"] < sig.first_frame + sig.n_frames]
        outside += len(hits) - len(inside)
        rallies.append(Rally(sig, hits, f"owner:{stem}"))
    if outside:
        print(f"owner labels: {outside} hit(s) outside the saved signals; skipped")
    return rallies


HIT_OFFSET_STEPS = 2   # augmented training: each hit moves by up to this many 30 Hz steps


def _windows(rally, rng=None):
    sig, idx, hits = rally.sig, None, rally.hits
    if rng is not None:
        sig = augment(sig, rng)
        idx = jittered_index(sig.n_frames, sig.fps, rng)
        # the hit detector's frame is not exact: train on hits off by up to +-2 steps
        offsets = rng.integers(-HIT_OFFSET_STEPS, HIT_OFFSET_STEPS + 1, len(hits))
        hits = [dict(h, frame=int(h["frame"]) + int(round(o * sig.fps / 30.0)))
                for h, o in zip(hits, offsets)]
    X, ok = extract(sig, hits, idx)
    y = np.array([STROKE_TYPES.index(h["stroke"]) for h in rally.hits], np.int64)
    return X[ok], y[ok]


def _stack(rallies, rng=None):
    parts = [_windows(r, rng) for r in rallies]
    X = np.concatenate([p[0] for p in parts]) if parts else np.zeros((0, 1, INPUT_DIM), np.float32)
    y = np.concatenate([p[1] for p in parts]) if parts else np.zeros(0, np.int64)
    return X, y


def _evaluate(model, X, y, threshold):
    probs = smod.predict_probs(model, X)
    truth = [STROKE_TYPES[i] for i in y]
    return probs, truth, score(zip(truth, predict_labels(probs, threshold)))


def train(train_rallies, val_rallies, epochs=60, hidden=64, levels=3, batch=64, lr=1e-3,
          seed=0, device=None, log=print):
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    clean_X, clean_y = _stack(train_rallies)
    val_X, val_y = _stack(val_rallies)
    model = smod.StrokeNet(INPUT_DIM, hidden=hidden, levels=levels).to(device)
    flat = clean_X.reshape(-1, INPUT_DIM)
    model.feat_mean.copy_(torch.from_numpy(flat.mean(0)))
    model.feat_std.copy_(torch.from_numpy(flat.std(0) + 1e-3))
    counts = np.bincount(clean_y, minlength=len(STROKE_TYPES)).astype(np.float64)
    weight = torch.tensor(1.0 / np.sqrt(np.maximum(counts, 1.0)), dtype=torch.float32, device=device)
    weight = weight / weight.mean()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, epochs))
    best, best_state = None, None
    for epoch in range(1, epochs + 1):
        model.train()
        X, y = _stack(train_rallies, rng)            # fresh augmentation every epoch
        order = rng.permutation(len(X))
        losses = []
        for s in range(0, len(order), batch):
            b = order[s:s + batch]
            logits = model(torch.from_numpy(X[b]).to(device))
            loss = F.cross_entropy(logits, torch.from_numpy(y[b]).to(device), weight=weight)
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss))
        sched.step()
        model.eval()
        _, _, m = _evaluate(model, val_X, val_y, 0.0)
        recalls = [r["recall"] for r in m["recall"].values() if r["recall"] is not None]
        key = (min(recalls) if recalls else 0.0, m["accuracy"] or 0.0)
        log(f"epoch {epoch}: loss {np.mean(losses) if losses else float('nan'):.4f} "
            f"val group acc {m['accuracy']} worst recall {key[0]:.3f}")
        if best is None or key > best:
            best, best_state = key, {k: v.detach().clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.eval()
    probs, truth, _ = _evaluate(model, val_X, val_y, 0.0)
    threshold = choose_threshold(probs, truth) if len(truth) else 0.5
    _, _, val_metrics = _evaluate(model, val_X, val_y, threshold)
    meta = smod.base_meta(hidden, levels, threshold)
    meta.update({
        "train_matches": sorted({r.match for r in train_rallies}),
        "val_matches": sorted({r.match for r in val_rallies}),
        "train_counts": {STROKE_TYPES[i]: int(c) for i, c in enumerate(counts)},
        "val_metrics": val_metrics, "epochs": epochs, "seed": seed,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    return model.cpu(), meta


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dataset", action="append", default=[])
    p.add_argument("--val-match")
    p.add_argument("--dji-labels")
    p.add_argument("--stroke-labels")
    p.add_argument("--results-dir", default=os.path.join(ROOT, "outputs"))
    p.add_argument("--calib-video", action="append", default=[])
    p.add_argument("--epochs", type=int, default=60)
    p.add_argument("--device")
    p.add_argument("--out", default=os.path.join(ROOT, "weights", "stroke-classifier.pt"))
    a = p.parse_args(argv)
    if a.epochs < 1:
        p.error("--epochs must be at least 1")
    shuttle = [r for d in a.dataset for r in load_shuttleset(d)]
    dji = load_dji(a.dji_labels, a.results_dir) if a.dji_labels else []
    owner = load_owner(a.stroke_labels, a.results_dir) if a.stroke_labels else []
    calib = set()
    for v in a.calib_video:
        stem = v[len("owner:"):] if v.startswith("owner:") else v
        names = {f"dji:{stem}", f"owner:{stem}"}
        found = names & {r.match for r in dji + owner}
        if not found:
            print(f"calibration video {v} not found in the train split; ignored")
        calib |= found
    held = [r for r in dji + owner]
    train_r = [r for r in shuttle if r.match != a.val_match] + [r for r in held if r.match not in calib]
    val_r = [r for r in shuttle if r.match == a.val_match] + [r for r in held if r.match in calib]
    if not train_r or not val_r:
        raise SystemExit(f"need training and validation data (val match {a.val_match!r}, "
                         f"calibration videos {a.calib_video!r}; {len(train_r)} training and "
                         f"{len(val_r)} validation rallies found)")
    print(f"{sum(len(r.hits) for r in train_r)} training hits, {sum(len(r.hits) for r in val_r)} validation hits")
    model, meta = train(train_r, val_r, epochs=a.epochs, device=a.device)
    smod.save_stroke_model(model, meta, a.out)
    print(f"saved {a.out}: threshold {meta['unknown_threshold']}, val {meta['val_metrics']['accuracy']} "
          f"group accuracy, unknown {meta['val_metrics']['unknown_rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
