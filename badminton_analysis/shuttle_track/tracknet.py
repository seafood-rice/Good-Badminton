"""Inference wrapper around the vendored TrackNetV3 (third_party/tracknet).

Produces a dense per-frame shuttle trajectory from a video. Torch is imported
lazily (inside _run_prediction) so weightless / --help paths stay torch-free.
Frame indices returned here are 0-based (TrackNet's own convention); the
+1 alignment to the match loop's 1-based frame_count is applied by the
pipeline integration (see system.py::_run_shuttle_pretrack), not here.

third_party/tracknet exposes a bare top-level module named ``model``, as does
the still-vendored third_party/bst (nothing imports it since the stroke
classifier replaced BST) and any other bare ``model`` on sys.path. Whichever
is imported first would win the ``sys.modules['model']`` cache slot, so
all vendored-tracknet imports must go through ``vendored_imports()`` below,
which evicts any colliding cached modules for the duration of the import and
restores the previous state on exit.
"""
import contextlib
import os
import sys
from pathlib import Path

_VENDOR_DIR = Path(__file__).resolve().parents[2] / "third_party" / "tracknet"
_VENDOR_NAMES = ("infer", "model", "dataset", "utils")


def _is_vendor_module(name):
    return name in _VENDOR_NAMES or any(name.startswith(n + ".") for n in _VENDOR_NAMES)


@contextlib.contextmanager
def vendored_imports():
    """Import vendored TrackNetV3 modules in isolation so they never collide
    with another bare 'model' package (e.g. third_party/bst's) across one
    process's sys.modules cache. Evicts cached colliding modules, puts the tracknet vendor dir first
    on sys.path, and on exit removes whatever it imported + restores originals.
    """
    saved = {n: m for n, m in list(sys.modules.items()) if _is_vendor_module(n)}
    for n in saved:
        del sys.modules[n]
    vendor = str(_VENDOR_DIR)
    added = vendor not in sys.path
    if added:
        sys.path.insert(0, vendor)
    try:
        yield
    finally:
        for n in [n for n in sys.modules if _is_vendor_module(n)]:
            del sys.modules[n]
        sys.modules.update(saved)
        if added:
            try:
                sys.path.remove(vendor)
            except ValueError:
                pass


def _run_prediction(**kwargs):
    """Indirection point so tests can stub the neural inference."""
    with vendored_imports():
        from infer import run_prediction
        return run_prediction(**kwargs)


def load_tracknet(tracknet_path, inpaintnet_path=None, device=None):
    if not os.path.isfile(str(tracknet_path)):
        raise FileNotFoundError(f"TrackNet weights not found: {tracknet_path}")
    if inpaintnet_path is not None and not os.path.isfile(str(inpaintnet_path)):
        # InpaintNet is optional; treat a bad path as "no rectification".
        inpaintnet_path = None
    return {"tracknet_file": str(tracknet_path),
            "inpaintnet_file": str(inpaintnet_path) if inpaintnet_path else None,
            "device": device}


def _in_roi(x, y, court_roi):
    if court_roi is None:
        return True
    (x1, y1), (x2, y2) = court_roi
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    lo_y, hi_y = min(y1, y2), max(y1, y2)
    return lo_x <= x <= hi_x and lo_y <= y <= hi_y


# The vendored background (median) image decodes up to ``max_sample_num`` frames at the video's
# full resolution before taking their median. At the default 1800 frames a 4K video needs about
# 45 GB and the process is killed by the OOM killer (seen on a 21k-frame 3840x2160 recording).
MEDIAN_BUDGET_BYTES = 1_500_000_000
MEDIAN_MIN_FRAMES = 50
MEDIAN_MAX_FRAMES = 1800


def median_sample_count(width, height, budget=MEDIAN_BUDGET_BYTES):
    """Frames to sample for the median image so the decoded stack stays within ``budget`` bytes."""
    frame_bytes = max(1, int(width) * int(height) * 3)
    return max(MEDIAN_MIN_FRAMES, min(MEDIAN_MAX_FRAMES, int(budget // frame_bytes)))


def _frame_size(video_path):
    import cv2
    cap = cv2.VideoCapture(str(video_path))
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()


def track_video(video_path, models, court_roi=None, eval_mode="weight", progress=None):
    try:
        w, h = _frame_size(video_path)
    except Exception:
        w = h = 0
    extra = {"max_sample_num": median_sample_count(w, h)} if w > 0 and h > 0 else {}
    pred = _run_prediction(
        video_file=str(video_path),
        tracknet_file=models["tracknet_file"],
        inpaintnet_file=models.get("inpaintnet_file") or "",
        device=models.get("device"),
        eval_mode=eval_mode,
        progress=progress,
        **extra,
    )
    traj = {}
    for f, x, y, vis in zip(pred["Frame"], pred["X"], pred["Y"], pred["Visibility"]):
        if not vis:
            traj[int(f)] = None
            continue
        fx, fy = float(x), float(y)
        traj[int(f)] = (fx, fy) if _in_roi(fx, fy, court_roi) else None
    return traj
