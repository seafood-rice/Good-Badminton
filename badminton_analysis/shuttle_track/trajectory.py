"""On-disk cache for the dense shuttle trajectory produced by the TrackNetV3
pre-pass. Keyed by video path + mtime + inference params so a stale or
differently-configured run misses and recomputes.
"""
import hashlib
import json
import os


def cache_key(video_path, params):
    try:
        mtime = os.path.getmtime(video_path)
    except OSError:
        mtime = 0.0
    payload = json.dumps(
        {"video": os.path.abspath(str(video_path)), "mtime": mtime,
         "params": {k: params[k] for k in sorted(params)}},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_cache(cache_path, key, trajectory):
    serial = {str(f): (list(pt) if pt is not None else None)
              for f, pt in trajectory.items()}
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump({"key": key, "trajectory": serial}, fh)


def load_cache(cache_path, key):
    if not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if data.get("key") != key:
        return None
    out = {}
    for f, pt in data.get("trajectory", {}).items():
        out[int(f)] = (float(pt[0]), float(pt[1])) if pt is not None else None
    return out
