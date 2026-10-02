"""Score the wrist/swing rally segmentation against human-labelled rally boundaries,
and fit its constants to this footage.

Usage (from the repo root):
    PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/score_rally_labels.py
    Optional: --detections <detections.jsonl> (default: the DJI 0010 fixture),
              --out <score_result.json> (default: outputs/b11-labelling/score_result.json)

The swing signal and the segmentation are the production ones
(badminton_analysis.stroke.rallies), so this scores the shipped code path rather
than a private copy. The teleport caps come from the court corners and fps in the
metadata.json next to the detections file.

Reads the filled-in table in outputs/b11-labelling/LABELS.md (git-ignored, since it is
per-footage data). Reports, restricted to the labelled
window and excluding any spans the labeller marked unusable:

  * frame-level precision / recall / F1 for "is a rally in play"
  * per-rally detection rate and boundary error (start/end, in seconds)
  * a small grid search over swing_frac / gap_sec / min_len_sec, so the constants can be
    FITTED to this footage rather than inherited from rep_segmenter's drill context

This scores the PRODUCTION segmenter (``badminton_analysis.stroke.rallies``) against the
human labels; it is the measurement that decided whether the swing path could ship
validated or had to stay experimental (B11 design doc §0.15-§0.16). The grid search is
diagnostic only -- the as-shipped constants are deliberately not the fitted ones.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from badminton_analysis.stroke import rallies  # noqa: E402

LABEL_DIR = ROOT / "outputs/b11-labelling"
DET = ROOT / "outputs/Dji 20260718111111 0010 D/detections.jsonl"
DEFAULT_OUT = LABEL_DIR / "score_result.json"
LABELS_MD = LABEL_DIR / "LABELS.md"

# Frame-level scoring step. The teleport caps and the smoothing window come from
# rallies.baseline_caps / rallies.swing_activity, using the run's own fps. (Historically
# this script hardcoded far/near caps of 21.3 / 120.9 px for DJI 0010; baseline_caps
# derives those same values from the court quad.)
FPS = 59.94


def parse_labels():
    """Pull the two markdown tables out of LABELS.md. Ignores example/blank rows."""
    if not LABELS_MD.exists():
        sys.exit(f"missing {LABELS_MD}")
    rows, excl, seen_second = [], [], False
    for line in LABELS_MD.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("| from_sec"):
            seen_second = True
            continue
        if not s.startswith("|") or set(s) <= set("|- "):
            continue
        if "EXAMPLE" in s.upper() or s.startswith("| start_sec"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        nums = []
        for c in cells[:2]:
            m = re.match(r"^-?\d+(?:\.\d+)?$", c)
            nums.append(float(c) if m else None)
        if nums[0] is None or nums[1] is None:
            continue
        (excl if seen_second else rows).append((nums[0], nums[1]))
    if not rows:
        sys.exit("no rally rows found in LABELS.md -- fill in the table first")
    return sorted(rows), sorted(excl)


def load_activity(det_path):
    """Per-frame (time, smoothed swing activity), via the production module.

    Parses detections.jsonl into the record shape the analysis track uses and
    hands it to rallies.swing_activity, with caps from the run's metadata.json.
    """
    det_path = Path(det_path)
    meta = json.loads((det_path.parent / "metadata.json").read_text(encoding="utf-8"))
    fps = float(meta["video"]["fps"])
    caps = rallies.baseline_caps(meta["court"]["corners"], fps)

    track = []
    with open(det_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            pl = d.get("players") or {}

            def wrist(side):
                hands = (pl.get(side) or {}).get("hands") or {}
                return rallies.wrists_from_hands(hands.get("left"), hands.get("right"))

            track.append({"frame": d["frame"],
                          "wrist_lower": wrist("lower"),
                          "wrist_upper": wrist("upper")})
    print(f"detections {det_path}  fps {fps:.3f}  caps far/near "
          f"{caps[0]:.1f}/{caps[1]:.1f} px")
    return rallies.swing_activity(track, fps, caps)


def score(pred, truth, window, excluded):
    lo_w, hi_w = window

    def usable(x):
        return lo_w <= x <= hi_w and not any(a <= x <= b for a, b in excluded)

    step = 1.0 / FPS
    tp = fp = fn = tn = 0
    x = lo_w
    while x <= hi_w:
        if usable(x):
            p = any(a <= x <= b for a, b in pred)
            g = any(a <= x <= b for a, b in truth)
            tp += p and g
            fp += p and not g
            fn += g and not p
            tn += not p and not g
        x += step
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0

    matched, dstart, dend = 0, [], []
    for a, b in truth:
        best = None
        for pa, pb in pred:
            ov = min(b, pb) - max(a, pa)
            if ov > 0 and (best is None or ov > best[0]):
                best = (ov, pa, pb)
        if best:
            matched += 1
            dstart.append(best[1] - a)
            dend.append(best[2] - b)
    return {"precision": prec, "recall": rec, "f1": f1,
            "rallies_detected": matched, "rallies_total": len(truth),
            "start_err": dstart, "end_err": dend}


def med(v):
    return sorted(v)[len(v) // 2] if v else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--detections", type=Path, default=DET,
                    help="detections.jsonl to score (metadata.json is read beside it)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="where to write the JSON result")
    args = ap.parse_args()

    truth, excluded = parse_labels()
    window = (min(a for a, _ in truth) - 5.0, max(b for _, b in truth) + 5.0)
    print(f"labelled rallies: {len(truth)}   window {window[0]:.1f}-{window[1]:.1f}s"
          f"   excluded spans: {len(excluded)}")

    t, sm = load_activity(args.detections)
    base = dict(swing_frac=0.25, gap_sec=1.0, min_len_sec=2.0)
    pred = rallies.segments_from_activity(t, sm, frac=base["swing_frac"],
                                          gap_sec=base["gap_sec"],
                                          min_len_sec=base["min_len_sec"])
    r = score(pred, truth, window, excluded)
    print(f"\n==== as-shipped constants {base} ====")
    print(f"segments predicted in window: {sum(1 for a,b in pred if b>=window[0] and a<=window[1])}")
    print(f"frame-level  precision {r['precision']:.3f}  recall {r['recall']:.3f}  F1 {r['f1']:.3f}")
    print(f"rallies detected: {r['rallies_detected']}/{r['rallies_total']}")
    print(f"boundary error (s): start median {med(r['start_err']):+.2f}   end median {med(r['end_err']):+.2f}")

    print("\n==== fitted to this footage (grid search, ranked by F1) ====")
    results = []
    for sf in (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50):
        for gap in (0.5, 1.0, 1.5, 2.0):
            for mn in (1.0, 2.0, 3.0):
                segs = rallies.segments_from_activity(t, sm, frac=sf, gap_sec=gap,
                                                      min_len_sec=mn)
                rr = score(segs, truth, window, excluded)
                results.append((rr["f1"], sf, gap, mn, rr))
    results.sort(reverse=True, key=lambda x: x[0])
    print(" F1     swing_frac gap  min_len   prec   rec   rallies")
    for f1, sf, gap, mn, rr in results[:8]:
        print(f" {f1:.3f}   {sf:>6.2f}  {gap:>4.1f}  {mn:>5.1f}   "
              f"{rr['precision']:.3f} {rr['recall']:.3f}  {rr['rallies_detected']}/{rr['rallies_total']}")
    best = results[0]
    print(f"\nbest: swing_frac={best[1]} gap_sec={best[2]} min_len_sec={best[3]} -> F1 {best[0]:.3f}")
    print("(as-shipped F1 was %.3f)" % r["f1"])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = {"as_shipped": {k: v for k, v in r.items() if k not in ("start_err", "end_err")},
              "best": {"swing_frac": best[1], "gap_sec": best[2], "min_len_sec": best[3],
                       "f1": best[0]}}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
