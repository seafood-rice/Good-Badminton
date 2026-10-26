"""Synthetic run directories for the rally-outcome tests."""
import json

import cv2
import numpy as np

FPS = 60.0
CORNERS = [[1000, 500], [2000, 500], [2600, 2000], [400, 2000]]


def court_to_image(x, y):
    src = np.array([[0, 0], [6.1, 0], [6.1, 13.4], [0, 13.4]], dtype=np.float32)
    h = cv2.getPerspectiveTransform(src, np.array(CORNERS, dtype=np.float32))
    p = cv2.perspectiveTransform(np.array([[[x, y]]], dtype=np.float32), h)[0, 0]
    return [float(p[0]), float(p[1])]


def write(out_dir, name, doc):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(json.dumps(doc), encoding="utf-8")


def make_run(out_dir, segments, hits=None, strokes=None, landings=None, match_format="singles",
             corners=True):
    """``segments`` = [(id, start_sec, end_sec)]; ``hits`` = [(time_sec, hitter)];
    ``strokes`` = [(time_sec, hitter, stroke)]; ``landings`` = [(start_sec, court_x, court_y)]
    (the shuttle rests there for 0.5 s)."""
    meta = {"match_format": match_format,
            "video": {"fps": FPS, "height": 2160, "width": 3840}}
    if corners:
        meta["court"] = {"corners": CORNERS}
    write(out_dir, "metadata.json", meta)
    write(out_dir, "rally_segments.json", {
        "fps": FPS, "detection": {"signal": "swing", "degraded": False},
        "rallies": [{"id": i, "start_sec": s, "end_sec": e, "start_frame": int(s * FPS),
                     "end_frame": int(e * FPS), "degraded": False} for i, s, e in segments]})
    if hits is not None:
        write(out_dir, "hits.json", {"source": "learned", "fps": FPS, "hits": [
            {"frame": int(t * FPS), "time_sec": t, "hitter": who, "confidence": 0.9} for t, who in hits]})
    if strokes is not None:
        write(out_dir, "strokes.json", _strokes_doc(strokes))
    if landings is not None:
        traj = {}
        for start, x, y in landings:
            p = court_to_image(x, y)
            for f in range(int(start * FPS), int((start + 0.5) * FPS)):
                traj[str(f)] = p
        write(out_dir, "shuttle_trajectory.json", {"key": "x", "trajectory": traj})
    return out_dir


def _strokes_doc(strokes):
    from badminton_analysis.stroke import stroke_classifier
    doc = {"strokes": [{"frame": int(t * FPS), "hitter": who, "stroke": st,
                        "group": None if st == "unknown" else st, "confidence": 0.9,
                        "hit_confidence": 0.9} for t, who, st in strokes]}
    doc["schema"] = stroke_classifier.STROKES_SCHEMA
    return doc
