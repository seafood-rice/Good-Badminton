"""Frame-to-frame association of N people into single-side tracklets (spec 6.2).

Pure: no I/O, no cv2. A constant-velocity Kalman filter per track predicts
court position; the Hungarian algorithm matches predictions to detections.
Tracks are ended early whenever identity could be confused (occlusion while
two people are close, a long miss, a net crossing): a short tracklet is cheap,
a swapped one poisons identity assignment downstream.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

NET_Y = 6.7
GATE_GROWTH_CAP = 2.0      # the gate widens with sqrt(missed strides), at most 2x
_BIG = 1e6                 # cost of a gated-out (forbidden) track/detection pair


@dataclass
class Detection:
    court: tuple
    image: tuple
    keypoints: object = None
    appearance: object = None


def _side(y, net_y=NET_Y):
    return "upper" if float(y) < net_y else "lower"


class _Kalman:
    """2-D constant-velocity Kalman filter, time in frames."""

    def __init__(self, xy, q=0.02, r=0.05):
        self.x = np.array([xy[0], xy[1], 0.0, 0.0], dtype=float)
        self.P = np.diag([r, r, 1.0, 1.0])
        self.q, self.r = q, r

    def predict(self, dt):
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        Q = self.q * np.diag([dt ** 3 / 3, dt ** 3 / 3, dt, dt])
        return F @ self.x, F @ self.P @ F.T + Q

    def update(self, xy, dt):
        x, P = self.predict(dt)
        H = np.zeros((2, 4))
        H[0, 0] = H[1, 1] = 1.0
        S = H @ P @ H.T + self.r * np.eye(2)
        K = P @ H.T @ np.linalg.inv(S)
        self.x = x + K @ (np.asarray(xy, float) - H @ x)
        self.P = (np.eye(4) - K @ H) @ P


@dataclass
class _Track:
    id: int
    side: str
    kf: _Kalman
    last_frame: int
    frames: list = field(default_factory=list)
    court: list = field(default_factory=list)
    image: list = field(default_factory=list)
    keypoints: list = field(default_factory=list)
    appearance: list = field(default_factory=list)
    cross_run: int = 0

    def add(self, frame, d):
        self.frames.append(int(frame))
        self.court.append([float(d.court[0]), float(d.court[1])])
        self.image.append([float(d.image[0]), float(d.image[1])])
        kp = d.keypoints
        self.keypoints.append(None if kp is None else np.asarray(kp, float).tolist())
        if d.appearance is not None:
            self.appearance.append(d.appearance)
        self.last_frame = int(frame)

    def predicted(self, frame):
        x, _ = self.kf.predict(max(1, frame - self.last_frame))
        return x[:2]

    def to_dict(self):
        ys = [c[1] for c in self.court]
        return {"id": self.id, "side": _side(np.median(ys)) if ys else self.side,
                "frames": self.frames, "court": self.court, "image": self.image,
                "keypoints": self.keypoints, "appearance": self.appearance}


class MultiTracker:
    def __init__(self, fps, *, gate_m=1.0, max_miss_sec=0.5, cross_m=0.6,
                 net_y=NET_Y, cross_frames=3):
        self.fps = float(fps) if fps and fps > 0 else 30.0
        self.gate_m = gate_m
        self.max_miss = max(1, int(round(max_miss_sec * self.fps)))
        self.cross_m = cross_m
        self.net_y = net_y
        self.cross_frames = cross_frames
        self._active = []
        self._done = []
        self._next_id = 0
        self._stride = None
        self._last_step = None

    def _new(self, frame, d):
        t = _Track(self._next_id, _side(d.court[1], self.net_y), _Kalman(d.court), frame)
        self._next_id += 1
        t.add(frame, d)
        self._active.append(t)
        return t

    def _close(self, t):
        if t in self._active:
            self._active.remove(t)
            self._done.append(t)

    def _miss_limit(self):
        # A regular analysis stride (fast mode) is not a miss.
        return self.max_miss + (self._stride or 1)

    def step(self, frame, detections):
        frame = int(frame)
        if self._last_step is not None:
            gap = frame - self._last_step
            if gap > 0 and (self._stride is None or gap < self._stride):
                self._stride = gap
        self._last_step = frame
        dets = [d for d in detections if d is not None and d.court is not None]

        for t in list(self._active):
            if frame - t.last_frame > self._miss_limit():
                self._close(t)

        tracks = list(self._active)
        matches, unmatched_t, unmatched_d = [], set(range(len(tracks))), set(range(len(dets)))
        if tracks and dets:
            preds = [t.predicted(frame) for t in tracks]
            cost = np.array([[np.hypot(p[0] - d.court[0], p[1] - d.court[1]) for d in dets]
                             for p in preds])
            # Gate BEFORE the assignment: gating after it lets a stale track (wide
            # gate) win a fresh neighbour's detection when that is the global optimum.
            for r, t in enumerate(tracks):
                gap = max(1.0, (frame - t.last_frame) / (self._stride or 1))
                gate = self.gate_m * min(np.sqrt(gap), GATE_GROWTH_CAP)
                cost[r, cost[r] > gate] = _BIG
            rows, cols = linear_sum_assignment(cost)
            for r, c in zip(rows, cols):
                if cost[r, c] < _BIG:
                    matches.append((r, c))
                    unmatched_t.discard(r)
                    unmatched_d.discard(c)

        # Ambiguity: two tracks predicted within cross_m where either is unmatched.
        cut = set()
        if len(tracks) > 1:
            preds = [t.predicted(frame) for t in tracks]
            for i in range(len(tracks)):
                for j in range(i + 1, len(tracks)):
                    if (np.hypot(*(preds[i] - preds[j])) < self.cross_m
                            and (i in unmatched_t or j in unmatched_t)):
                        cut.update((i, j))
        # Proximity: two tracks whose matched detections are within cross_m
        # this frame, even though both are detected. Partners that touch and
        # recoil are indistinguishable by position alone, so end both.
        for a in range(len(matches)):
            for b in range(a + 1, len(matches)):
                ca, cb = dets[matches[a][1]].court, dets[matches[b][1]].court
                if np.hypot(ca[0] - cb[0], ca[1] - cb[1]) < self.cross_m:
                    cut.update((matches[a][0], matches[b][0]))
        # Re-emergence: a detection that would start a new track lies within
        # cross_m of a detection matched this frame (a person splitting out of
        # a merged blob). Which person the old track follows is arbitrary, so
        # end it and start fresh tracks for both detections.
        if unmatched_d:
            for r, c in matches:
                if r in cut:
                    continue
                if any(np.hypot(dets[c].court[0] - dets[u].court[0],
                                dets[c].court[1] - dets[u].court[1]) < self.cross_m
                       for u in unmatched_d if u != c):
                    cut.add(r)
        for r, c in matches:
            if r in cut:
                unmatched_d.add(c)
        for i in cut:
            self._close(tracks[i])

        for r, c in matches:
            if r in cut:
                continue
            t, d = tracks[r], dets[c]
            dt = max(1, frame - t.last_frame)
            t.kf.update(d.court, dt)
            t.add(frame, d)
            if _side(d.court[1], self.net_y) != t.side:
                t.cross_run += 1
                if t.cross_run >= self.cross_frames:
                    self._split(t)
            else:
                t.cross_run = 0

        for c in sorted(unmatched_d):
            self._new(frame, dets[c])

    def _split(self, t):
        """Move the last cross_frames observations onto a new track on the other side."""
        n = self.cross_frames
        tail = {k: getattr(t, k)[-n:] for k in ("frames", "court", "image", "keypoints")}
        for k in ("frames", "court", "image", "keypoints"):
            setattr(t, k, getattr(t, k)[:-n])
        t.last_frame = t.frames[-1] if t.frames else t.last_frame
        self._close(t)
        new = _Track(self._next_id, _side(tail["court"][-1][1], self.net_y),
                     _Kalman(tail["court"][0]), tail["frames"][0])
        self._next_id += 1
        for k in ("frames", "court", "image", "keypoints"):
            setattr(new, k, tail[k])
        new.last_frame = tail["frames"][-1]
        for xy in tail["court"][1:]:
            new.kf.update(xy, 1)
        self._active.append(new)

    def finish(self):
        for t in list(self._active):
            self._close(t)
        return [t.to_dict() for t in sorted(self._done, key=lambda t: t.id) if t.frames]


def filter_tracklets(tracklets, fps, *, min_sec=1.0, margin_m=0.3, court=(6.1, 13.4)):
    """Drop tracklets shorter than min_sec or whose median position is off court."""
    fps = float(fps) if fps and fps > 0 else 30.0
    kept = []
    for t in tracklets:
        if not t["frames"] or (t["frames"][-1] - t["frames"][0] + 1) / fps < min_sec:
            continue
        xs = [c[0] for c in t["court"]]
        ys = [c[1] for c in t["court"]]
        mx, my = float(np.median(xs)), float(np.median(ys))
        if not (-margin_m <= mx <= court[0] + margin_m and -margin_m <= my <= court[1] + margin_m):
            continue
        kept.append(t)
    return kept
