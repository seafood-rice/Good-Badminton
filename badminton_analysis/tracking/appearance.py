"""Appearance signatures for doubles identity (spec 6.3).

A signature is what makes the same person recognisable across tracklets:
HSV histograms of the torso and the legs (shirt and shorts), standing height in
metres, and an optional embedding slot (None unless local re-ID weights exist;
nothing is downloaded). Distances are in [0, 1].
"""
import cv2
import numpy as np

H_BINS, S_BINS = 16, 4
MIN_REGION_PX = 50
W_TORSO, W_LEGS, W_HEIGHT, W_EMBED = 0.6, 0.25, 0.15, 0.5
HEIGHT_SCALE_M = 0.3
_EPS = 1e-9


def _valid(kp, i):
    return kp is not None and len(kp) > i and float(kp[i][0]) > 1 and float(kp[i][1]) > 1


def _polygon(kp, idx):
    if not all(_valid(kp, i) for i in idx):
        return None
    return np.array([[float(kp[i][0]), float(kp[i][1])] for i in idx], dtype=np.float32)


def _region_hist(frame, poly):
    """Normalised H x S histogram of the pixels inside poly's convex hull, or None.

    Works on the hull's bounding box (clipped to the frame) only: the same
    pixels and histogram as a whole-frame mask, without converting a 4K frame
    to HSV per region.
    """
    if poly is None:
        return None
    hull = cv2.convexHull(poly.astype(np.int32))
    x, y, w, h = cv2.boundingRect(hull)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    crop = frame[y0:y1, x0:x1]
    mask = np.zeros(crop.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, hull - np.array([x0, y0], np.int32), 255)
    if int(np.count_nonzero(mask)) < MIN_REGION_PX:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, [H_BINS, S_BINS], [0, 180, 0, 256]).flatten()
    total = float(hist.sum())
    if total <= 0:
        return None
    return (hist / total).astype(np.float64)


def standing_height_m(kp, scale):
    """Ankle-midpoint to shoulder-midpoint distance, in metres at the ankle row."""
    if scale is None or not all(_valid(kp, i) for i in (5, 6, 15, 16)):
        return None
    sh = (np.asarray(kp[5], float) + np.asarray(kp[6], float)) / 2.0
    an = (np.asarray(kp[15], float) + np.asarray(kp[16], float)) / 2.0
    ppm = float(scale.px_per_m(an[1]))
    if ppm <= 0:
        return None
    return float(np.linalg.norm(an - sh) / ppm)


def signature(frame_bgr, keypoints, scale):
    """Signature of one person in one frame, or None when nothing is measurable."""
    if frame_bgr is None or keypoints is None:
        return None
    kp = np.asarray(keypoints, dtype=float)
    torso = _region_hist(frame_bgr, _polygon(kp, (5, 6, 12, 11)))
    legs = _region_hist(frame_bgr, _polygon(kp, (11, 12, 14, 13)))
    height = standing_height_m(kp, scale)
    if torso is None and legs is None and height is None:
        return None
    return {"torso": torso, "legs": legs, "height": height, "embed": None}


def _median_hist(hists):
    hists = [h for h in hists if h is not None]
    if not hists:
        return None
    m = np.median(np.stack(hists), axis=0)
    s = float(m.sum())
    return m / s if s > 0 else None


def median_signature(samples):
    samples = [s for s in samples or [] if s is not None]
    if not samples:
        return None
    heights = [s["height"] for s in samples if s.get("height") is not None]
    embeds = [s["embed"] for s in samples if s.get("embed") is not None]
    return {
        "torso": _median_hist([s.get("torso") for s in samples]),
        "legs": _median_hist([s.get("legs") for s in samples]),
        "height": float(np.median(heights)) if heights else None,
        "embed": np.median(np.stack(embeds), axis=0) if embeds else None,
    }


def chi2(a, b):
    """Symmetric chi-square distance of two L1-normalised histograms, in [0, 1]."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(0.5 * np.sum((a - b) ** 2 / (a + b + _EPS)))


def distance(a, b):
    """Weighted distance in [0, 1] over the terms both signatures have; 1.0 if none."""
    if a is None or b is None:
        return 1.0
    terms = []
    if a.get("torso") is not None and b.get("torso") is not None:
        terms.append((W_TORSO, chi2(a["torso"], b["torso"])))
    if a.get("legs") is not None and b.get("legs") is not None:
        terms.append((W_LEGS, chi2(a["legs"], b["legs"])))
    if a.get("height") is not None and b.get("height") is not None:
        terms.append((W_HEIGHT, min(1.0, abs(a["height"] - b["height"]) / HEIGHT_SCALE_M)))
    if a.get("embed") is not None and b.get("embed") is not None:
        ea, eb = np.asarray(a["embed"], float), np.asarray(b["embed"], float)
        cos = float(ea @ eb / (np.linalg.norm(ea) * np.linalg.norm(eb) + _EPS))
        terms.append((W_EMBED, min(1.0, max(0.0, (1.0 - cos) / 2.0))))
    if not terms:
        return 1.0
    wsum = sum(w for w, _ in terms)
    return float(sum(w * d for w, d in terms) / wsum)


def to_json(sig):
    if sig is None:
        return None
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in sig.items()}


def from_json(d):
    if d is None:
        return None
    return {k: (np.asarray(v, float) if isinstance(v, list) else v) for k, v in d.items()}
