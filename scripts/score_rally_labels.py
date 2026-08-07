"""Score the wrist/swing rally segmentation against human-labelled rally boundaries,
and fit its constants to this footage.

Usage (from the repo root):
    PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/score_rally_labels.py

Reads the filled-in table in outputs/b11-labelling/LABELS.md (git-ignored, since it is
per-footage data). Reports, restricted to the labelled
window and excluding any spans the labeller marked unusable:

  * frame-level precision / recall / F1 for "is a rally in play"
  * per-rally detection rate and boundary error (start/end, in seconds)
  * a small grid search over swing_frac / gap_sec / min_len_sec, so the constants can be
    FITTED to this footage rather than inherited from rep_segmenter's drill context

Nothing here is committed as production behaviour -- it is the measurement that decides
whether the swing path can ship validated or must stay experimental (B11 design doc §0.15).
"""
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL_DIR = ROOT / "outputs/b11-labelling"
DET = ROOT / "outputs/Dji 20260718111111 0010 D/detections.jsonl"
LABELS_MD = LABEL_DIR / "LABELS.md"

FPS = 59.94
SMOOTH_SEC = 0.5
# Perspective-correct teleport caps (B11 §0.15 Defect 2): derived from the court quad's own
# scale -- 106.3 px/m at the far baseline vs 603.8 px/m at the near one -- at a generous
# 12 m/s human sprint cap, instead of one absolute-pixel constant for both players.
FAR_CAP_PX = 21.3
NEAR_CAP_PX = 120.9


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


def load_activity():
    """Per-frame (time, smoothed swing activity) with perspective-correct teleport caps."""
    t, up, lo = [], [], []
    with open(DET, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            pl = d.get("players") or {}

            def wrist(side):
                rec = pl.get(side) or {}
                hands = rec.get("hands") or {}
                pts = [p for p in (hands.get("left"), hands.get("right")) if p]
                if not pts:
                    return None
                return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

            t.append(d["time_sec"])
            up.append(wrist("upper"))
            lo.append(wrist("lower"))
    n = len(t)

    def speed(seq, cap):
        out = [0.0] * n
        for i in range(1, n):
            a, b = seq[i - 1], seq[i]
            if a and b:
                v = math.hypot(b[0] - a[0], b[1] - a[1])
                if v <= cap:
                    out[i] = v
        return out

    act = [max(a, b) for a, b in zip(speed(lo, NEAR_CAP_PX), speed(up, FAR_CAP_PX))]
    w = max(1, int(round(SMOOTH_SEC * FPS)))
    sm, run = [], 0.0
    for i, v in enumerate(act):
        run += v
        if i >= w:
            run -= act[i - w]
        sm.append(run / min(i + 1, w))
    return t, sm


def segment(t, sm, swing_frac, gap_sec, min_len_sec):
    srt = sorted(sm)
    thr = swing_frac * srt[min(len(srt) - 1, int(0.995 * len(srt)))]
    active = [v >= thr for v in sm]
    runs, i, n = [], 0, len(sm)
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1]:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1
    merged = []
    for r in runs:
        if merged and (t[r[0]] - t[merged[-1][1]]) <= gap_sec:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [(t[a], t[b]) for a, b in merged if (t[b] - t[a]) >= min_len_sec]


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
    truth, excluded = parse_labels()
    window = (min(a for a, _ in truth) - 5.0, max(b for _, b in truth) + 5.0)
    print(f"labelled rallies: {len(truth)}   window {window[0]:.1f}-{window[1]:.1f}s"
          f"   excluded spans: {len(excluded)}")

    t, sm = load_activity()
    base = dict(swing_frac=0.25, gap_sec=1.0, min_len_sec=2.0)
    pred = segment(t, sm, **base)
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
                rr = score(segment(t, sm, sf, gap, mn), truth, window, excluded)
                results.append((rr["f1"], sf, gap, mn, rr))
    results.sort(reverse=True, key=lambda x: x[0])
    print(" F1     swing_frac gap  min_len   prec   rec   rallies")
    for f1, sf, gap, mn, rr in results[:8]:
        print(f" {f1:.3f}   {sf:>6.2f}  {gap:>4.1f}  {mn:>5.1f}   "
              f"{rr['precision']:.3f} {rr['recall']:.3f}  {rr['rallies_detected']}/{rr['rallies_total']}")
    best = results[0]
    print(f"\nbest: swing_frac={best[1]} gap_sec={best[2]} min_len_sec={best[3]} -> F1 {best[0]:.3f}")
    print("(as-shipped F1 was %.3f)" % r["f1"])

    json.dump({"as_shipped": {k: v for k, v in r.items() if k not in ("start_err", "end_err")},
               "best": {"swing_frac": best[1], "gap_sec": best[2], "min_len_sec": best[3],
                        "f1": best[0]}},
              open(LABEL_DIR / "score_result.json", "w"), indent=1)
    print("\nwrote outputs/b11-labelling/score_result.json")


if __name__ == "__main__":
    main()
