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
import csv
import glob
import json
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
if os.path.join(ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_hit_dataset as bhd  # noqa: E402
import shuttleset_labels as ssl  # noqa: E402

from badminton_analysis.stroke import owner_labels as ol  # noqa: E402
from badminton_analysis.stroke import stroke_classifier as sc  # noqa: E402
from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402
from badminton_analysis.stroke import stroke_model as smod  # noqa: E402
from badminton_analysis.stroke.hit_augment import augment, jittered_index  # noqa: E402
from badminton_analysis.stroke.hit_signals import RawSignals, load_signals  # noqa: E402
from badminton_analysis.stroke.stroke_metrics import (  # noqa: E402
    choose_threshold, predict_labels, score)
from badminton_analysis.stroke.stroke_types import ATTRIBUTES, STROKE_TYPES, type_of_raw  # noqa: E402
from badminton_analysis.stroke.stroke_windows import INPUT_DIM, extract  # noqa: E402

Rally = namedtuple("Rally", "sig hits match")
_SIDES = {0: "lower", 1: "upper"}
MIN_ATTR_LABELS = 200      # an attribute head is trained only with this many known training labels


def _recomputed_attrs(z, match, labels_root, cache):
    """The attribute arrays for an npz built before they existed, or None when not provable.

    Recomputes this rally's labels exactly as build_hit_dataset did and accepts them only
    if hit_frames and stroke_raw equal the npz's (a join, never a fuzzy match).
    """
    if not labels_root or "set" not in z.files or "rally" not in z.files:
        return None
    if "kind" in z.files and str(z["kind"]) != "rally":
        return None
    if match not in cache:
        cache[match] = None
        align_path = os.path.join(labels_root, "alignment", f"{match}.json")
        label_dir = os.path.join(labels_root, "labels", match)
        if os.path.isfile(align_path) and os.path.isdir(label_dir):
            try:      # a broken label source costs this match its attribute heads, never the training run
                with open(align_path, encoding="utf-8") as fh:
                    offset = float(json.load(fh)["offset"])
                corners = ssl.load_corners(os.path.join(labels_root, "labels", "homography.csv"), match)
                spans = ssl.rally_spans(ssl.parse_match_hits(label_dir, corners))
                cache[match] = (offset, {(sp["set"], sp["rally"]): sp for sp in spans})
            except (KeyError, IndexError, TypeError, ValueError, OSError, csv.Error) as exc:
                print(f"attribute labels unusable for {match}: {type(exc).__name__}: {exc}")
    if cache[match] is None:
        return None
    offset, spans = cache[match]
    span = spans.get((int(z["set"]), int(z["rally"])))
    if span is None:
        return None
    clip_start, _ = bhd.clip_window(span["start"], span["end"], offset)
    enc = bhd.encode_hits(span["hits"], offset, clip_start, float(z["fps"]), int(z["shuttle"].shape[0]))
    if (not np.array_equal(enc["hit_frames"], z["hit_frames"])
            or not np.array_equal(enc["stroke_raw"], z["stroke_raw"])):
        return None
    return {k: enc[k] for k in ATTRIBUTES}


def load_shuttleset(dataset_dir, labels_root=None):
    rallies, joined, unjoined = [], 0, 0
    cache = {}
    for path in sorted(glob.glob(os.path.join(dataset_dir, "*", "*.npz"))):
        if path.endswith(".part.npz"):
            continue
        with np.load(path) as z:
            if "stroke_raw" not in z.files:
                continue
            sig = RawSignals(float(z["fps"]), int(z["first_frame"]), z["shuttle"].astype(np.float32),
                             z["keypoints"].astype(np.float32), z["court_corners"].astype(np.float32),
                             tuple(int(v) for v in z["image_size"]))
            if all(k in z.files for k in ATTRIBUTES):
                arrays = {k: z[k] for k in ATTRIBUTES}
            else:
                arrays = _recomputed_attrs(z, str(z["match"]), labels_root, cache)
            if "kind" not in z.files or str(z["kind"]) == "rally":      # gap files carry no labels to join
                if arrays is None:
                    unjoined += 1
                else:
                    joined += 1
            hits = []
            for i, (f, who, raw) in enumerate(zip(z["hit_frames"], z["hitter"], z["stroke_raw"])):
                stroke = type_of_raw(str(raw))
                if stroke is None or int(who) not in _SIDES:
                    continue
                hit = {"frame": int(f), "hitter": _SIDES[int(who)], "stroke": stroke}
                if arrays is not None:
                    hit["attrs"] = {k: int(arrays[k][i]) for k in ATTRIBUTES if int(arrays[k][i]) >= 0}
                hits.append(hit)
            if hits:
                rallies.append(Rally(sig, hits, str(z["match"])))
    if unjoined:
        print(f"attributes not joined for {unjoined} of {joined + unjoined} rally file(s) in {dataset_dir}")
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
    out = []
    for s in applied["strokes"]:
        if s.get("source") != "owner":
            continue
        hit = {"frame": int(s["frame"]), "hitter": s["hitter"], "stroke": s["stroke"]}
        if isinstance(s.get("owner_backhand"), bool):
            hit["attrs"] = {"backhand": int(s["owner_backhand"])}
        out.append(hit)
    return out


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
    A = np.array([[h.get("attrs", {}).get(k, -1) for k in ATTRIBUTES] for h in rally.hits],
                 np.int64).reshape(len(rally.hits), len(ATTRIBUTES))     # -1 = unknown
    return X[ok], y[ok], A[ok]


def _stack(rallies, rng=None):
    parts = [_windows(r, rng) for r in rallies]
    X = np.concatenate([p[0] for p in parts]) if parts else np.zeros((0, 1, INPUT_DIM), np.float32)
    y = np.concatenate([p[1] for p in parts]) if parts else np.zeros(0, np.int64)
    A = np.concatenate([p[2] for p in parts]) if parts else np.zeros((0, len(ATTRIBUTES)), np.int64)
    return X, y, A


def _trained_attributes(A):
    """Attributes with enough known labels and both classes present, in ATTRIBUTES order."""
    out = []
    for i, name in enumerate(ATTRIBUTES):
        pos, neg = int((A[:, i] == 1).sum()), int((A[:, i] == 0).sum())
        if pos + neg >= MIN_ATTR_LABELS and pos > 0 and neg > 0:
            out.append(name)
    return out


def _masked_bce(logits, targets, pos_weight):
    """Mean BCE over the known (target >= 0) entries of each column, then over columns."""
    known = (targets >= 0).float()
    raw = F.binary_cross_entropy_with_logits(logits, targets.clamp_min(0).float(),
                                             pos_weight=pos_weight, reduction="none") * known
    return (raw.sum(0) / known.sum(0).clamp_min(1.0)).mean()


def _attr_metrics(model, names, X, A, min_conf):
    probs = smod.predict_attrs(model, X) if names else None
    out = {}
    for j, name in enumerate(names):
        col = A[:, ATTRIBUTES.index(name)]
        known = col >= 0
        p, truth = probs[known, j], col[known]
        sure = np.maximum(p, 1.0 - p) >= min_conf
        correct = int((sure & ((p >= 0.5).astype(np.int64) == truth)).sum())
        n = int(known.sum())
        out[name] = {"n": n, "accuracy": round(correct / n, 4) if n else None,
                     "abstained": int((~sure).sum())}
    return out


def _evaluate(model, X, y, threshold):
    probs = smod.predict_probs(model, X)
    truth = [STROKE_TYPES[i] for i in y]
    return probs, truth, score(zip(truth, predict_labels(probs, threshold)))


def train(train_rallies, val_rallies, epochs=60, hidden=64, levels=3, batch=64, lr=1e-3,
          seed=0, device=None, log=print):
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    clean_X, clean_y, clean_A = _stack(train_rallies)
    val_X, val_y, val_A = _stack(val_rallies)
    names = _trained_attributes(clean_A)
    cols = [ATTRIBUTES.index(n) for n in names]
    pos_weight = None
    if names:
        pos = (clean_A[:, cols] == 1).sum(0).astype(np.float64)
        neg = (clean_A[:, cols] == 0).sum(0).astype(np.float64)
        pos_weight = torch.tensor(np.clip(neg / np.maximum(pos, 1.0), 1.0, 10.0),
                                  dtype=torch.float32, device=device)
    model = smod.StrokeNet(INPUT_DIM, hidden=hidden, levels=levels, n_attrs=len(names)).to(device)
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
        X, y, A = _stack(train_rallies, rng)         # fresh augmentation every epoch
        order = rng.permutation(len(X))
        losses = []
        for s in range(0, len(order), batch):
            b = order[s:s + batch]
            logits, attr_logits = model.forward_all(torch.from_numpy(X[b]).to(device))
            loss = F.cross_entropy(logits, torch.from_numpy(y[b]).to(device), weight=weight)
            if names:
                loss = loss + _masked_bce(attr_logits, torch.from_numpy(A[b][:, cols]).to(device),
                                          pos_weight)
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
    val_metrics["attributes"] = _attr_metrics(model, names, val_X, val_A, smod.ATTR_MIN_CONFIDENCE)
    meta = smod.base_meta(hidden, levels, threshold, attributes=names)
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
    p.add_argument("--shuttleset-data", default=os.path.join(ROOT, "data", "shuttleset"),
                   help="ShuttleSet labels/alignment, to attach attributes to older dataset files")
    p.add_argument("--results-dir", default=os.path.join(ROOT, "outputs"))
    p.add_argument("--calib-video", action="append", default=[])
    p.add_argument("--epochs", type=int, default=60)
    p.add_argument("--device")
    p.add_argument("--out", default=os.path.join(ROOT, "weights", "stroke-classifier.pt"))
    a = p.parse_args(argv)
    if a.epochs < 1:
        p.error("--epochs must be at least 1")
    shuttle = [r for d in a.dataset for r in load_shuttleset(d, a.shuttleset_data)]
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
