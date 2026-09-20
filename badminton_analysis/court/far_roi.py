"""The far half of the court, as an image rectangle.

On wide multi-court framing the far player is tiny: measured on the 0007 clip
(3840x2160), the opponent's visible keypoint span is 100-155 px. Ultralytics
letterboxes a 3840-wide frame to its 640 default, shrinking that to 17-26 px,
and yolo11n-pose finds nothing -- the far player was absent from all 36,732
recorded frames of that run.

Cropping to the far half first and running pose on the crop restores the
resolution where it matters. Measured over 8 frames spanning 4 rallies:

    full frame, imgsz=640  (as shipped)     14.8 ms   upper found in 0/8 frames
    full frame, imgsz=2560                  39.5 ms   upper found in 5/8 frames
    far ROI,    imgsz=1280                  19.3 ms   upper found in 8/8 frames

The crop wins on both axes because it spends its pixels only where the small
target is. Taking the x-extent over ALL four corners instead of the far half's
own rows makes the crop as wide as the whole frame and throws that away.
"""

DEFAULT_NET_COURT_Y = 6.7
"""Net line in court metres (half of the 13.4 m court length)."""

HEADROOM_FACTOR = 2.5
"""Rows above the far baseline to include, as a multiple of the baseline-to-net
image depth. The crop is keyed on where feet can be, but it must contain whole
bodies; at this framing the far half is ~150 rows deep while a standing player
is ~250 rows tall, so the headroom is necessarily larger than the depth."""

SIDE_PAD_PX = 80
"""Horizontal slack, so a player lunging just outside the sideline is kept."""


def _edges_at(quad, row):
    """Left and right court edge x at an image row, interpolating the sides."""
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = (
        (float(p[0]), float(p[1])) for p in quad)
    if bly == tly or bry == try_:
        return None
    fl = (bly - row) / (bly - tly)
    fr = (bry - row) / (bry - try_)
    return blx + fl * (tlx - blx), brx + fr * (trx - brx)


def net_image_row(quad, court_mapper, net_court_y=DEFAULT_NET_COURT_Y):
    """Image row where the net crosses the court's centre line, or None.

    Found by scanning rather than inverting the homography, because the quad's
    own corners are the only reliable anchors and a scan cannot produce a row
    outside them.
    """
    if court_mapper is None or quad is None or len(quad) != 4:
        return None
    top = min(float(quad[0][1]), float(quad[1][1]))
    bottom = max(float(quad[2][1]), float(quad[3][1]))
    cx = (float(quad[0][0]) + float(quad[1][0])) / 2.0
    for row in range(int(top), int(bottom) + 1):
        court = court_mapper.image_to_court((cx, row))
        if court is None or len(court) < 2:
            continue
        if float(court[1]) >= net_court_y:
            return row
    return None


def far_court_roi(quad, frame_shape, court_mapper,
                  net_court_y=DEFAULT_NET_COURT_Y,
                  headroom_factor=HEADROOM_FACTOR, pad=SIDE_PAD_PX):
    """Crop rectangle (x0, y0, x1, y1) covering the far half, or None.

    Returns None whenever the geometry is unusable -- no quad, no mapper, a
    degenerate scan -- so the caller simply skips the second pass rather than
    cropping somewhere arbitrary.
    """
    if quad is None or len(quad) != 4 or not frame_shape:
        return None
    height, width = int(frame_shape[0]), int(frame_shape[1])

    far_row = min(float(quad[0][1]), float(quad[1][1]))
    net_row = net_image_row(quad, court_mapper, net_court_y)
    if net_row is None or net_row <= far_row:
        return None

    edges_far = _edges_at(quad, far_row)
    edges_net = _edges_at(quad, net_row)
    if edges_far is None or edges_net is None:
        return None

    headroom = (net_row - far_row) * float(headroom_factor)
    x0 = max(0, int(min(edges_far[0], edges_net[0]) - pad))
    y0 = max(0, int(far_row - headroom))
    x1 = min(width, int(max(edges_far[1], edges_net[1]) + pad))
    y1 = min(height, int(net_row) + 60)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return (x0, y0, x1, y1)
