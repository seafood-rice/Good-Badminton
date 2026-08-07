"""Per-rep 3D pose feature sidecar (drill_reps_3d.npz) for the future AQA scorer.

Ragged per-rep sequences are stored as separate arrays keyed by rep id; metadata
travels as a JSON string under a reserved key.
"""
import json

import numpy as np

_META_KEY = "__meta__"


def write_reps_3d(path, reps_3d, meta):
    arrays = {_META_KEY: np.array(json.dumps(meta))}
    for r in reps_3d:
        rid = int(r["rep_id"])
        arrays["rep%d_frames" % rid] = np.asarray(r["frames"], dtype=np.int64)
        arrays["rep%d_kp3d" % rid] = np.asarray(r["keypoints_3d"], dtype=np.float32)
    np.savez(path, **arrays)


def read_reps_3d(path):
    data = np.load(path, allow_pickle=True)
    meta = json.loads(str(data[_META_KEY]))
    by_id = {}
    for key in data.files:
        if key == _META_KEY:
            continue
        rid_str, kind = key[3:].split("_", 1)  # strip "rep" prefix
        rid = int(rid_str)
        by_id.setdefault(rid, {"rep_id": rid})
        if kind == "frames":
            by_id[rid]["frames"] = [int(v) for v in data[key]]
        else:
            by_id[rid]["keypoints_3d"] = np.asarray(data[key], dtype=float)
    return [by_id[k] for k in sorted(by_id)], meta
