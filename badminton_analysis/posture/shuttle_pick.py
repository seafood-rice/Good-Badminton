"""Choose which shuttle detection to use as a frame's contact anchor.

The shuttle is the PRIMARY contact anchor: rep_segmenter snaps a rep's contact frame
to the in-window frame where shuttle and wrist are closest. Previously the code took
index 0 unconditionally -- ultralytics returns NMS output ordered by DESCENDING
CONFIDENCE, so that was the highest-confidence detection, not an arbitrary one. But
highest-confidence is not the same as correct: when several shuttle-like detections
are present in a frame, the true shuttle is not always the most confident one, and the
error is largest exactly where the shuttle sits near the wrist -- which is every
contact frame. Picking by proximity to the wrist instead targets that directly.

Trade-off: proximity-based picking has no confidence tie-break and no maximum-distance
guard. A low-confidence false positive sitting near the wrist can therefore outrank a
high-confidence true shuttle detected further away. Revisit when the shuttle path is
enabled (see below) and real footage is available to check how often this occurs.

This function is only reached when a ball model is supplied to PostureAnalysisSystem.
As shipped, no posture entry point does that: PostureAnalysisSystem defaults
ball_model_path to None, main_posture.py's --ball-model defaults to None, and app.py's
posture endpoint never passes one (only match mode does). So this code path is inert
today -- contact_anchor is always "apex" or "speed_peak", never "shuttle" -- and exists
for when the shuttle path is enabled for posture mode.
"""
import math


def pick_shuttle(boxes_xywh, wrist):
    """Return the (x, y) centre of the detection nearest ``wrist``, or None.

    ``boxes_xywh`` is an (N, 4) array-like of x, y, w, h with x/y as the box centre
    (ultralytics' xywh convention). When ``wrist`` is None there is nothing to
    measure against, so index 0 (the highest-confidence detection) is kept -- the
    previous behaviour.
    """
    if boxes_xywh is None or len(boxes_xywh) == 0:
        return None
    if wrist is None:
        return (float(boxes_xywh[0][0]), float(boxes_xywh[0][1]))
    best, best_d = None, None
    for b in boxes_xywh:
        x, y = float(b[0]), float(b[1])
        d = math.hypot(x - float(wrist[0]), y - float(wrist[1]))
        if best_d is None or d < best_d:
            best, best_d = (x, y), d
    return best
