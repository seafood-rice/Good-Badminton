"""Build ``outputs/<stem>/rally_outcomes.json`` (spec 5.2).

Merges the owner's labels with the automatic labeller and adds the
reconciliation with the recorded result. Everything is read back from the run
directory, so the build works the same at the end of an analysis, after a label
save and from the CLI::

    python -m badminton_analysis.outcomes.build <out_dir> [--labels-dir D] [--athletes F]

Nothing here raises for bad input; ``build`` prints and returns None when it
cannot write.
"""
import argparse
import datetime
import json
from pathlib import Path

from ..insights.loaders import load_run, number
from ..insights.rally import summarise_rally_payload
from ..library import athletes as libathletes
from . import evidence as ev
from . import labels as lab
from .reconcile import match_parts, reconcile
from .rules import decide

SCHEMA = 1
OUTCOMES_NAME = "rally_outcomes.json"
ADDED_WINDOW_SEC = 10.0
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LABELS_DIR = PROJECT_ROOT / "data" / "rally_labels"
DEFAULT_ATHLETES = PROJECT_ROOT / "data" / "athletes.json"


def _segments(run):
    """``[{"id", "start_sec", "end_sec", "start_frame", "end_frame"}]`` in time order."""
    payload = run.rallies_payload
    if payload is None or summarise_rally_payload(payload)["failed"]:
        return []
    fps = run.fps
    out = []
    for r in payload.get("rallies") or []:
        if not isinstance(r, dict) or type(r.get("id")) is not int:
            continue
        start, end = number(r.get("start_sec")), number(r.get("end_sec"))
        sf, ef = number(r.get("start_frame")), number(r.get("end_frame"))
        if (start is None or end is None) and fps and sf is not None and ef is not None:
            start, end = sf / fps, ef / fps
        if start is None or end is None or end < start:
            continue
        out.append({"id": r["id"], "start_sec": start, "end_sec": end})
    return sorted(out, key=lambda s: s["start_sec"])


def _learned_hits(run):
    meta = run.hits_meta if isinstance(run.hits_meta, dict) else {}
    return run.hits or [] if meta.get("source") == "learned" else []


def _trajectory(out_dir):
    """``{frame: [x, y]}`` from ``shuttle_trajectory.json``, or {} when absent / unusable."""
    path = Path(out_dir) / "shuttle_trajectory.json"
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return {}
    traj = doc.get("trajectory") if isinstance(doc, dict) else None
    if not isinstance(traj, dict):
        return {}
    out = {}
    for key, point in traj.items():
        try:
            out[int(key)] = point
        except (TypeError, ValueError):
            continue
    return out


class _Landing:
    """E2 for one run: the homography, frame height, static cells and trajectory."""

    def __init__(self, run, out_dir):
        meta = run.metadata if isinstance(run.metadata, dict) else {}
        court = meta.get("court") if isinstance(meta.get("court"), dict) else {}
        video = meta.get("video") if isinstance(meta.get("video"), dict) else {}
        self.h = ev.court_homography(court.get("corners")) if court.get("corners") else None
        self.frame_h = number(video.get("height"))
        self.fps = run.fps
        detection = (run.rallies_payload or {}).get("detection")
        cells = ((detection or {}).get("suppressed_static_shuttle") or {}).get("cells") \
            if isinstance(detection, dict) else None
        self.static = []
        for cell in cells if isinstance(cells, list) else []:
            point = cell.get("point") if isinstance(cell, dict) else None
            if isinstance(point, list) and len(point) == 2 and all(number(v) is not None for v in point):
                self.static.append((float(point[0]), float(point[1])))
        usable = self.h is not None and self.frame_h and self.fps
        self.trajectory = _trajectory(out_dir) if usable else {}

    def verdict(self, seg, last_hit, hitter):
        if not self.trajectory:
            return None
        first = int(last_hit[1]) + 1 if last_hit else int(seg["start_sec"] * self.fps)
        last = int((seg["end_sec"] + ev.LANDING_AFTER_END_SEC) * self.fps)
        points = ev.clean_track(self.trajectory, range(first, last + 1), self.frame_h, self.static)
        image_xy = ev.find_landing(points, self.fps, self.frame_h)
        court_xy = ev.to_court(self.h, image_xy) if image_xy else None
        return ev.landing_verdict(court_xy, hitter) if court_xy else None


def _auto(segments, hits, landing):
    """``{segment_id: {"decision", "ending", "evidence"}}`` for every segment."""
    out = {}
    for i, seg in enumerate(segments):
        inside = ev.rally_hits(hits, seg["start_sec"], seg["end_sec"])
        last_hit = inside[-1] if inside else None
        hitter = last_hit[2] if last_hit else None
        land = landing.verdict(seg, last_hit, hitter)
        server = None
        if i + 1 < len(segments) and last_hit:
            nxt = segments[i + 1]
            server = ev.next_server(hits, last_hit[0], nxt["start_sec"], nxt["end_sec"])
        out[seg["id"]] = {"decision": decide(land, hitter, server),
                          "ending": (land or {}).get("ending"),
                          "evidence": {"landing": land, "last_hitter": hitter, "next_server": server}}
    return out


def _ending_stroke(run, start_sec, end_sec):
    """The last stroke in the window, when it has a type; None for doubles or unknown."""
    if run.match_format == "doubles" or not run.strokes or not run.fps:
        return None
    inside = []
    for s in run.strokes:
        frame = number(s.get("frame"))
        if frame is not None and start_sec - ev.RALLY_PAD_SEC <= frame / run.fps <= end_sec + ev.RALLY_PAD_SEC:
            inside.append(s)
    if not inside:
        return None
    last = max(inside, key=lambda s: s["frame"])
    if last.get("stroke") in (None, "unknown"):
        return None
    return {"type": last.get("stroke"), "group": last.get("group"), "hitter": last.get("hitter"),
            "frame": int(last["frame"]), "confidence": number(last.get("confidence"))}


def _entry(run, start, end, segment_id, label, auto):
    entry = {"id": 0, "segment_id": segment_id, "start_sec": round(start, 2), "end_sec": round(end, 2),
             "winner_side": None, "outcome": None, "ending": None,
             "ending_stroke": _ending_stroke(run, start, end),
             "confidence": None, "source": None, "reviewed": label is not None, "evidence": None}
    if auto is not None:
        entry["ending"] = auto["ending"]
    if label is not None and label["verdict"] in lab.SIDES:
        entry.update(winner_side=label["verdict"], outcome=label["outcome"], confidence=1.0, source="owner")
    elif auto is not None and auto["decision"]["winner_side"] is not None:
        entry.update(winner_side=auto["decision"]["winner_side"],
                     confidence=auto["decision"]["confidence"], source="auto",
                     evidence=dict(auto["evidence"], rule=auto["decision"]["rule"]))
    return entry


def _summary(entries, segments, rejected, added):
    labelled = [e for e in entries if e["winner_side"]]
    return {
        "entries": len(entries), "labelled": len(labelled),
        "by_source": {s: sum(1 for e in labelled if e["source"] == s) for s in ("owner", "auto")},
        "coverage": round(len(labelled) / len(entries), 4) if entries else None,
        "points_won": {s: sum(1 for e in labelled if e["winner_side"] == s) for s in lab.SIDES},
        "segments": len(segments), "segments_rejected": rejected, "added": added,
        "confidence_calibrated": False,
    }


def _read_doc(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == SCHEMA else None


def build_document(out_dir, labels_doc, athletes_data, other_parts=None):
    """The ``rally_outcomes.json`` document for ``out_dir``.

    ``other_parts`` = ``{stem: doc|None}`` for the other parts of the match; read
    from the sibling output directories when None.
    """
    out_dir = Path(out_dir)
    run = load_run(out_dir)
    notes = []
    if labels_doc is None:
        notes.append("rally labels unreadable; ignored")
        labels_doc = lab.empty()
    segments = _segments(run)
    auto = _auto(segments, _learned_hits(run), _Landing(run, out_dir))
    by_segment, added = lab.attach(labels_doc["labels"], segments)

    rows = []   # (start, end, segment_id, label, auto)
    rejected = 0
    for seg in segments:
        label = by_segment.get(seg["id"])
        if label is not None and label["verdict"] == "not_point":
            rejected += 1
            continue
        rows.append([seg["start_sec"], seg["end_sec"], seg["id"], label, auto[seg["id"]]])
    for label in added:
        rows.append([None, label["end_sec"], None, label, None])
    rows.sort(key=lambda r: r[1])
    entries, previous_end = [], None
    for start, end, seg_id, label, auto_row in rows:
        if start is None:
            start = previous_end if previous_end is not None and previous_end < end \
                else max(0.0, end - ADDED_WINDOW_SEC)
        entries.append(_entry(run, start, end, seg_id, label, auto_row))
        previous_end = end
    for i, entry in enumerate(entries, 1):
        entry["id"] = i

    doc = {"schema": SCHEMA, "stem": run.stem, "match_format": run.match_format,
           "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rallies": entries,
           "summary": _summary(entries, segments, rejected, sum(1 for e in entries if e["segment_id"] is None)),
           "notes": notes + list(run.notes)}
    parts = match_parts(athletes_data, run.stem)
    docs = {run.stem: doc}
    for part, _swapped in parts:
        if part == run.stem:
            continue
        if other_parts is not None and part in other_parts:
            docs[part] = other_parts[part]
        else:
            docs[part] = _read_doc(out_dir.parent / part / OUTCOMES_NAME)
    doc["summary"]["reconciliation"] = reconcile(athletes_data, run.stem, docs)
    return doc


def build(out_dir, labels_dir=DEFAULT_LABELS_DIR, athletes_path=DEFAULT_ATHLETES):
    """Build and write ``out_dir/rally_outcomes.json``; the document, or None on failure."""
    out_dir = Path(out_dir)
    try:
        if not out_dir.is_dir():
            return None
        labels_doc = lab.load(lab.labels_path(labels_dir, out_dir.name))
        doc = build_document(out_dir, labels_doc, libathletes.load(athletes_path))
        lab.write_json_atomic(out_dir / OUTCOMES_NAME, doc)
        return doc
    except Exception as exc:   # never fatal: the analysis / label save already succeeded
        print(f"rally_outcomes.json not written for {out_dir.name}: {exc!r}")
        return None


def rebuild_match(outputs_dir, stem, labels_dir=DEFAULT_LABELS_DIR, athletes_path=DEFAULT_ATHLETES):
    """Rebuild ``stem`` and then every other part of its match; the stems written."""
    outputs_dir = Path(outputs_dir)
    data = libathletes.load(athletes_path)
    stems = [stem] + [p for p, _ in match_parts(data, stem) if p != stem]
    docs = {}
    for s in stems:
        out_dir = outputs_dir / s
        if not out_dir.is_dir():
            continue
        try:
            docs[s] = build_document(out_dir, lab.load(lab.labels_path(labels_dir, s)), data)
        except Exception as exc:   # never fatal, as in build()
            print(f"rally_outcomes.json not built for {s}: {exc!r}")
    # Every part's reconciliation sees every part's fresh document.
    others = {s: _read_doc(outputs_dir / s / OUTCOMES_NAME) for s in stems if s not in docs}
    written = []
    for s, doc in docs.items():
        doc["summary"]["reconciliation"] = reconcile(data, s, {**others, **docs})
        try:
            lab.write_json_atomic(outputs_dir / s / OUTCOMES_NAME, doc)
            written.append(s)
        except OSError as exc:
            print(f"rally_outcomes.json not written for {s}: {exc!r}")
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build rally_outcomes.json for a run directory.")
    parser.add_argument("out_dir")
    parser.add_argument("--labels-dir", default=str(DEFAULT_LABELS_DIR))
    parser.add_argument("--athletes", default=str(DEFAULT_ATHLETES))
    args = parser.parse_args(argv)
    doc = build(args.out_dir, args.labels_dir, args.athletes)
    if doc is None:
        return 1
    s = doc["summary"]
    print(f"{doc['stem']}: {s['entries']} entries, {s['labelled']} labelled "
          f"(owner {s['by_source']['owner']}, auto {s['by_source']['auto']}), "
          f"reconciliation {s['reconciliation']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
