"""Which partner hit: within-side attribution for doubles (spec 6.5).

The side comes from the existing hit list (learned model or contact rule).
This module only chooses between the (at most two) identities on that side:
by shuttle proximity when a shuttle track exists, otherwise by swing evidence
(peak wrist speed, wrist above shoulder). Unvalidated without labels; the
confidence and evidence fields let consumers say so.
"""
import math

HITTER_MIN_CONF = 0.6
SWING_WINDOW_SEC = 0.3
NEAR_FRAMES = 2
SPEED_NORM_MPS = 10.0
DEFAULT_PX_PER_M = 100.0


def _valid(kp, i):
    return kp is not None and len(kp) > i and kp[i][0] > 1 and kp[i][1] > 1


def _obs_near(track, frame, k=NEAR_FRAMES):
    for d in range(k + 1):
        for f in (frame - d, frame + d):
            if f in track:
                return track[f]
    return None


def _ppm(scale, y):
    if scale is None:
        return DEFAULT_PX_PER_M
    try:
        v = float(scale.px_per_m(y))
        return v if v > 0 else DEFAULT_PX_PER_M
    except Exception:
        return DEFAULT_PX_PER_M


def _shuttle_score(obs, pt, scale):
    kp = obs.get("keypoints")
    dists = [math.hypot(kp[i][0] - pt[0], kp[i][1] - pt[1]) for i in (9, 10) if _valid(kp, i)]
    if not dists:
        return None
    return -min(dists) / _ppm(scale, obs["image"][1])


def _swing_score(track, frame, fps, scale):
    w = max(1, int(round(SWING_WINDOW_SEC * fps)))
    frames = [f for f in range(frame - w, frame + w + 1) if f in track]
    peak, raised = 0.0, False
    for f in frames:
        kp = track[f].get("keypoints")
        for i, sh in ((9, 5), (10, 6)):
            if _valid(kp, i) and _valid(kp, sh) and kp[i][1] < kp[sh][1]:
                raised = True              # image y grows downwards: above the shoulder
    for a, b in zip(frames, frames[1:]):
        ka, kb = track[a].get("keypoints"), track[b].get("keypoints")
        ppm = _ppm(scale, track[b]["image"][1])
        for i in (9, 10):
            if _valid(ka, i) and _valid(kb, i):
                px = math.hypot(kb[i][0] - ka[i][0], kb[i][1] - ka[i][1]) / (b - a)
                peak = max(peak, px * fps / ppm)
    return min(peak, 2 * SPEED_NORM_MPS) / SPEED_NORM_MPS + (0.5 if raised else 0.0)


def _confidence(s1, s2):
    return float(math.tanh(abs(s1 - s2) / 2.0))


def attribute_hitters(hits, tracks, fps, *, teams=None, shuttle=None, scale=None):
    teams = teams or {}
    out = []
    for h in hits or []:
        h = dict(h)
        frame, side = int(h["frame"]), h.get("hitter")
        cands = []
        if side is not None:
            for pid, track in (tracks or {}).items():
                obs = _obs_near(track, frame)
                if obs is not None and obs.get("side") == side:
                    cands.append((pid, obs, track))
        pid, conf, evidence = None, None, "none"
        if len(cands) == 1:
            pid, conf, evidence = cands[0][0], 0.5, "single_candidate"
        elif len(cands) >= 2:
            pt = None
            if shuttle:
                for d in range(NEAR_FRAMES + 1):
                    pt = shuttle.get(frame - d) or shuttle.get(frame + d)
                    if pt is not None:
                        break
            scores = None
            if pt is not None:
                ss = [_shuttle_score(o, pt, scale) for _, o, _ in cands]
                if all(s is not None for s in ss):
                    scores, evidence = ss, "shuttle"
            if scores is None:
                scores = [_swing_score(t, frame, fps, scale) for _, _, t in cands]
                evidence = "swing"
            ranked = sorted(range(len(cands)), key=lambda i: scores[i], reverse=True)
            best = ranked[0]
            conf = round(_confidence(scores[best], scores[ranked[1]]), 4)
            pid = cands[best][0] if conf >= HITTER_MIN_CONF else None
        h["hitter_id"] = pid
        h["hitter_team"] = teams.get(pid) if pid else None
        h["hitter_confidence"] = conf
        h["hitter_evidence"] = evidence
        out.append(h)
    return out
