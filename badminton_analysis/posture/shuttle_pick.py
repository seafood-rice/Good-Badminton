"""Choose which shuttle detection to use as a frame's contact anchor.

The shuttle is the PRIMARY contact anchor: rep_segmenter snaps a rep's contact frame
to the in-window frame where shuttle and wrist are closest. Taking whichever box the
detector happened to return first can therefore move a rep's contact off the stroke,
and the error is largest exactly where the shuttle sits near the wrist -- which is
every contact frame.
"""
import math


def pick_shuttle(boxes_xywh, wrist):
    """Return the (x, y) centre of the detection nearest ``wrist``, or None.

    ``boxes_xywh`` is an (N, 4) array-like of x, y, w, h with x/y as the box centre
    (ultralytics' xywh convention). When ``wrist`` is None there is nothing to
    measure against, so the first box is kept -- the previous behaviour.
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
