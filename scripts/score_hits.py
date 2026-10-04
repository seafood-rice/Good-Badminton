"""Score hit detection against a LABELS.md (outputs/axelsen-hit-labels format).

Usage:
  python scripts/score_hits.py --labels outputs/axelsen-hit-labels/LABELS.md \
      --runs-dir outputs/axelsen-hit-labels/runs --suffix learned [--tol 0.2] \
      [--baseline] [--json result.json]

Clip N's run lives at <runs-dir>/rally<N>_<suffix>/ and must contain hits.json
(and hit_signals.npz for --baseline, which scores shuttle-path turns instead).
Clip spans come from the "### rallyN.mp4 (START s - END s)" headings; a clip's
time t maps to source time START + t. Labels inside "Unusable spans" are
excluded, as are detections there.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke.hit_metrics import summarize  # noqa: E402

DONE_BAR = {"recall": 0.60, "precision": 0.70, "hitter": 0.85}
_HEADING = re.compile(r"^###\s+rally(\d+)\.mp4\s+\(([\d.]+)\s*s\s*[–-]\s*([\d.]+)\s*s\)")
_SIDE = {"lower": "near", "upper": "far"}


def parse_labels(text):
    spans, hits, unusable = {}, {}, []
    section = None
    for line in text.splitlines():
        m = _HEADING.match(line.strip())
        if m:
            section = int(m.group(1))
            spans[section] = (float(m.group(2)), float(m.group(3)))
            hits[section] = []
            continue
        if line.startswith("## "):
            section = "unusable" if line.lower().startswith("## unusable") else None
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or not re.match(r"^\d", cells[0]):
            continue
        if section == "unusable":
            if len(cells) > 1 and re.match(r"^\d", cells[1]):
                unusable.append((float(cells[0]), float(cells[1])))
        elif isinstance(section, int):
            hits[section].append({"t": float(cells[0]),
                                  "player": (cells[1] if len(cells) > 1 else "").lower() or None,
                                  "stroke": (cells[2] if len(cells) > 2 else "").lower() or None})
    return spans, hits, unusable


def run_hits(run_dir, span_start):
    with open(os.path.join(run_dir, "hits.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    fps = float(payload["fps"])
    return [{"t": span_start + (h["time_sec"] if h.get("time_sec") is not None
                                else (h["frame"] - 1) / fps),
             "side": _SIDE.get(h.get("hitter"))} for h in payload["hits"]]


def baseline_hits(run_dir, span_start):
    from badminton_analysis.stroke.hit_signals import load_signals
    from badminton_analysis.stroke.shuttle_turns import shuttle_turn_events

    sig = load_signals(os.path.join(run_dir, "hit_signals.npz"))
    return [{"t": span_start + i / sig.fps, "side": None}
            for i in shuttle_turn_events(sig.shuttle, sig.fps)]


def score(labels_text, runs_dir, suffix, tol=0.2, baseline=False):
    spans, hits, unusable = parse_labels(labels_text)

    def usable(t):
        return not any(a <= t <= b for a, b in unusable)

    truth, det, per_clip = [], [], {}
    for n, (start, end) in sorted(spans.items()):
        clip_truth = [h for h in hits.get(n, []) if usable(h["t"])]
        if not clip_truth:
            continue
        run_dir = os.path.join(runs_dir, f"rally{n}_{suffix}")
        found = baseline_hits(run_dir, start) if baseline else run_hits(run_dir, start)
        clip_det = [d for d in found if start <= d["t"] <= end and usable(d["t"])]
        per_clip[n] = summarize([h["t"] for h in clip_truth], [d["t"] for d in clip_det], tol,
                                [h["player"] for h in clip_truth], [d["side"] for d in clip_det])
        truth += clip_truth
        det += clip_det
    summary = summarize([h["t"] for h in truth], [d["t"] for d in det], tol,
                        [h["player"] for h in truth], [d["side"] for d in det])
    return {"suffix": suffix, "baseline": baseline, "tol": tol, "summary": summary,
            "per_clip": per_clip}


def meets_bar(summary):
    hitter = summary.get("hitter_correct")
    return (summary["recall"] >= DONE_BAR["recall"] and summary["precision"] >= DONE_BAR["precision"]
            and hitter is not None and hitter >= DONE_BAR["hitter"])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--labels", required=True)
    p.add_argument("--runs-dir", required=True)
    p.add_argument("--suffix", required=True)
    p.add_argument("--tol", type=float, default=0.2)
    p.add_argument("--baseline", action="store_true")
    p.add_argument("--json")
    a = p.parse_args(argv)
    with open(a.labels, encoding="utf-8") as fh:
        result = score(fh.read(), a.runs_dir, a.suffix, a.tol, a.baseline)
    s = result["summary"]
    hitter = "n/a" if s["hitter_correct"] is None else f"{s['hitter_correct']:.2f}"
    print(f"{a.suffix}{' (baseline)' if a.baseline else ''}: truth {s['truth']} detected "
          f"{s['detected']} matched {s['matched']} recall {s['recall']:.2f} precision "
          f"{s['precision']:.2f} hitter {hitter} -> done bar {'MET' if meets_bar(s) else 'not met'}")
    for n, c in result["per_clip"].items():
        print(f"  clip {n}: truth {c['truth']} det {c['detected']} matched {c['matched']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
