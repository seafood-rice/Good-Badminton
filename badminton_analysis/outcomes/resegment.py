"""Re-segment existing results after the learned segmenter was kept, replaced or rolled back (spec 7.5).

  python -m badminton_analysis.outcomes.resegment --weights-dir weights --labels-dir data/rally_labels \
      --stroke-labels-dir data/stroke_labels --athletes data/athletes.json outputs/<stem> [outputs/<stem> ...]

Per result directory: rebuild the run inputs, write ``rally_segments.json`` (learned segments when
``weights/rally-segmenter.json`` loads, else the swing path recomputed offline from
``detections.jsonl``, or nothing when the file already says swing), point every hit's ``rally_id`` at the
new segments, and mark pre-cut rally clips stale (``clips/.stale``: re-cut on request). Then, for every
result touched: rebuild ``rally_outcomes.json`` for the whole match (owner rally labels are keyed by time,
so each re-attaches to the segment now holding its end time) and re-apply the owner's stroke labels.
Files are written atomically; a result that fails keeps its old files and the others still run.

The offline swing recompute uses ``detections.jsonl`` (every analysed frame) with the default
``signal="auto"`` and the run's court corners, so for a run that took the swing path in analysis it gives
the same segments; the pipeline's in-loop track holds the gate-passed frames only.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from ..data.writer import clean_value
from ..stroke import owner_labels, relabel
from ..stroke import rallies as rallies_mod
from . import build as outcomes_build
from . import run_inputs
from . import segmenter
from .labels import write_json_atomic

SEGMENTER_FILE = "rally-segmenter.json"
DECODER_FILE = "rally-decoder.json"
STALE_MARK = ".stale"


class _Skip(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None


def _restore(path, data):
    """Put ``data`` (bytes) back at ``path`` atomically."""
    fd, tmp = tempfile.mkstemp(dir=str(Path(path).parent), prefix=".restore-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _row(r):
    return {"id": r["id"], "start_frame": int(r["start_frame"]), "end_frame": int(r["end_frame"]),
            "start_sec": r["start_sec"], "end_sec": r["end_sec"], "degraded": bool(r.get("degraded"))}


def _learned(inputs, model, old_detection):
    segs = segmenter.segments(inputs, model)
    if not segs:
        return None
    version = model.get("version")
    detection = dict(old_detection)
    detection.update({
        "signal": "learned", "attempted_signal": "learned", "degraded": False,
        "reason": f"learned segmenter {version}",
        "model": {"version": version, "mode": model.get("mode", "gap"), "f1": model.get("train_f1")},
        "stroke_labels_withheld": False})
    return [dict(_row(s), p_point=s["p_point"]) for s in segs], detection


def _swing(out_dir, old_detection):
    loaded = run_inputs.load_track(out_dir)
    if loaded is None:
        raise _Skip("no_run")
    track, fps, quad = loaded
    segs, prov = rallies_mod.segment_rallies(track, fps, signal="auto", quad=quad)
    detection = dict(prov)
    detection["court_view"] = old_detection.get("court_view")
    detection["stroke_labels_withheld"] = bool(detection.get("degraded"))
    return [_row(s) for s in segs], detection


def _same(old_rallies, new_rallies):
    key = lambda rows: [(r.get("id"), r.get("start_frame"), r.get("end_frame")) for r in rows]   # noqa: E731
    return key(old_rallies) == key(new_rallies)


def _mark_stale(out_dir):
    clips = Path(out_dir) / "clips"
    if clips.is_dir() and any(p.is_file() and p.name != STALE_MARK for p in clips.iterdir()):
        (clips / STALE_MARK).write_text("", encoding="utf-8")


def _one(out_dir, model):
    """``'touched'`` or ``'unchanged'``; raises ``_Skip`` (reason code) or another error."""
    out_dir = Path(out_dir)
    if not (out_dir / "metadata.json").is_file() or not (out_dir / "detections.jsonl").is_file():
        raise _Skip("no_run")
    hits_path, seg_path = out_dir / "hits.json", out_dir / "rally_segments.json"
    hits_doc = _read(hits_path)
    if not (isinstance(hits_doc, dict) and hits_doc.get("source") == "learned"
            and isinstance(hits_doc.get("hits"), list)):
        raise _Skip("no_learned_hits")
    old = _read(seg_path)
    if not isinstance(old, dict) or not isinstance(old.get("rallies"), list):
        raise _Skip("no_segments")
    old_detection = old.get("detection") if isinstance(old.get("detection"), dict) else {}
    result = None
    if model is not None:
        inputs = run_inputs.load(out_dir)
        if inputs is not None and inputs.hits:
            result = _learned(inputs, model, old_detection)
    if result is None:
        if old_detection.get("signal") != "learned":
            return "unchanged"           # already the swing path: nothing to put back
        result = _swing(out_dir, old_detection)
    rallies, detection = result
    if old_detection.get("signal") == detection.get("signal") and _same(old["rallies"], rallies) \
            and old_detection.get("model") == detection.get("model"):
        return "unchanged"
    old_bytes = seg_path.read_bytes()
    write_json_atomic(seg_path, clean_value({"fps": old.get("fps"), "rallies": rallies, "detection": detection}))
    try:
        def rally_id(frame):
            for r in rallies:
                if int(r["start_frame"]) <= frame <= int(r["end_frame"]):
                    return r["id"]
            return None

        for h in hits_doc["hits"]:
            h["rally_id"] = rally_id(int(h["frame"]))
        write_json_atomic(hits_path, hits_doc)
    except BaseException:
        _restore(seg_path, old_bytes)
        raise
    _mark_stale(out_dir)
    return "touched"


def _error(exc):
    return f"{type(exc).__name__}: {exc}"[:200]


def run(results_dirs, weights_dir, labels_dir, stroke_labels_dir, athletes_path):
    """Re-segment every result dir; ``{"touched", "unchanged", "skipped": [{stem, reason}], "failed":
    [{stem, error}]}`` (stems). Outcomes are rebuilt and stroke labels re-applied for the touched ones."""
    weights_dir = Path(weights_dir)
    model = segmenter.load(weights_dir / SEGMENTER_FILE)
    summary = {"touched": [], "unchanged": [], "skipped": [], "failed": []}
    for out_dir in (Path(d) for d in results_dirs):
        stem = out_dir.name
        try:
            summary[_one(out_dir, model)].append(stem)
        except _Skip as skip:
            summary["skipped"].append({"stem": stem, "reason": skip.reason})
        except Exception as exc:
            summary["failed"].append({"stem": stem, "error": _error(exc)})
    for stem in list(summary["touched"]):
        out_dir = next(Path(d) for d in results_dirs if Path(d).name == stem)
        try:
            outcomes_build.rebuild_match(out_dir.parent, stem, labels_dir, athletes_path,
                                         decoder_path=weights_dir / DECODER_FILE)
        except Exception as exc:
            summary["failed"].append({"stem": stem, "error": "outcomes: " + _error(exc)})
        try:
            if owner_labels.apply_to_result(out_dir, stroke_labels_dir, stem)["applied"]:
                relabel.rescore_reports(out_dir)
        except Exception as exc:
            summary["failed"].append({"stem": stem, "error": "stroke labels: " + _error(exc)})
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--weights-dir", required=True)
    p.add_argument("--labels-dir", required=True)
    p.add_argument("--stroke-labels-dir", required=True)
    p.add_argument("--athletes", required=True)
    p.add_argument("results_dirs", nargs="*")
    a = p.parse_args(argv)
    summary = run(a.results_dirs, a.weights_dir, a.labels_dir, a.stroke_labels_dir, a.athletes)
    for s in summary["skipped"]:
        print(f"skipped {s['stem']}: {s['reason']}")
    for f in summary["failed"]:
        print(f"failed {f['stem']}: {f['error']}")
    print(json.dumps(summary))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
