"""Masking and per-band budgeting for classical shuttle candidate extraction.

The costing spike used a single global cap of 60 candidates/frame, which bound on
100% of frames and dropped 533,718 candidates -- so its recall figures were shaped
by an arbitrary truncation, and a dim far-court shuttle could be crowded out by
bright near-court clutter. These tests pin the three fixes (court mask, structural
flicker mask, per-depth-band budgets) plus gain correction for exposure drift.
"""
import numpy as np
import pytest

from badminton_analysis.shuttle_track.classical import (N_BANDS, build_masks,
                                                        frame_candidates)

H, W = 300, 400
QUAD = [(120.0, 120.0), (280.0, 120.0), (380.0, 260.0), (20.0, 260.0)]


def _blank(v=40):
    return np.full((H, W), v, np.uint8)


def _frames(n=16, v=40):
    return [_blank(v) for _ in range(n)]


def _put(img, cx, cy, r=3, v=230):
    ys, xs = np.ogrid[:img.shape[0], :img.shape[1]]
    img[(xs - cx) ** 2 + (ys - cy) ** 2 <= r * r] = v


def test_court_mask_covers_the_quad_and_its_airspace():
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    assert masks["court"][200, 200] > 0          # inside the quad
    assert masks["court"][80, 200] > 0           # airspace above it
    assert masks["court"][200, 5] == 0           # outside, left of the quad


def test_structural_flicker_is_masked_out():
    # A pixel that changes in most frames is structure, not a passing shuttle.
    frames = _frames(20)
    for i, f in enumerate(frames):
        if i % 2 == 0:
            _put(f, 200, 200, r=4)
    masks = build_masks(frames, QUAD, band_top_px=60, activity_frac=0.35)
    assert masks["static"][200, 200] == 0


def test_a_moving_blob_is_found_and_a_masked_one_is_not():
    frames = _frames(16)
    masks = build_masks(frames, QUAD, band_top_px=60)
    g = _blank()
    _put(g, 200, 200, r=3)
    found, _dropped = frame_candidates(g, masks)
    assert len(found) == 1
    assert found[0].x == pytest.approx(200, abs=2)
    outside = _blank()
    _put(outside, 5, 200, r=3)
    assert frame_candidates(outside, masks)[0] == []


def test_dim_blob_below_brightness_floor_is_rejected():
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    g = _blank()
    _put(g, 200, 200, r=3, v=90)      # above diff threshold, below min_bright
    assert frame_candidates(g, masks, min_bright=110.0)[0] == []


def test_budget_is_per_band_so_a_far_blob_survives_near_clutter():
    """A single far-court blob must not be crowded out by many near-court blobs."""
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    g = _blank()
    _put(g, 200, 130, r=3, v=255)                      # one far (small y) blob
    for i in range(20):                                # crowd the near band
        _put(g, 40 + i * 16, 250, r=3, v=255)
    found, dropped = frame_candidates(g, masks, band_budget=4)
    assert any(c.y < 160 for c in found), "far-band candidate was crowded out"
    assert dropped > 0


def test_dropped_count_is_zero_when_no_band_is_over_budget():
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    g = _blank()
    _put(g, 200, 200, r=3)
    assert frame_candidates(g, masks, band_budget=12)[1] == 0


def test_bands_partition_the_masked_rows_without_overlap():
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    bands = masks["bands"]
    assert len(bands) == N_BANDS
    for (_lo1, hi1), (lo2, _hi2) in zip(bands, bands[1:]):
        assert hi1 == lo2


def test_build_masks_rejects_a_bad_quad():
    with pytest.raises(ValueError):
        build_masks(_frames(), QUAD[:2], band_top_px=60)


def test_build_masks_rejects_empty_frames():
    with pytest.raises(ValueError):
        build_masks([], QUAD, band_top_px=60)


# The drift tests need a brighter baseline than the others: the gain ratio must land
# inside GAIN_LIMITS (0.5, 2.0) while the uncorrected difference still clears
# diff_thresh=35. Background 100 drifting to 160 gives ratio 0.625 (inside) and an
# uncorrected difference of 60 (over threshold). A 40 -> 130 drift would be ratio
# 0.31, which the clamp rejects by design, so correction would not engage at all.
DRIFT_BG, DRIFT_LEVEL = 100, 160


def test_global_brightness_drift_does_not_flood_candidates():
    """Auto-exposure drift must not make every pixel a candidate."""
    masks = build_masks(_frames(v=DRIFT_BG), QUAD, band_top_px=60)
    drifted = np.full((H, W), DRIFT_LEVEL, np.uint8)      # no moving object at all
    found, _dropped = frame_candidates(drifted, masks, gain_correct=True)
    assert found == []
    # Uncorrected, the same frame differs everywhere -- the defect being fixed.
    diff_map = np.abs(drifted.astype(int) - masks["median"].astype(int))
    assert diff_map.max() > 35


def test_gain_correction_still_finds_a_real_blob_under_drift():
    masks = build_masks(_frames(v=DRIFT_BG), QUAD, band_top_px=60)
    g = np.full((H, W), 120, np.uint8)            # mildly drifted background
    _put(g, 200, 200, r=3, v=255)                 # plus a genuine bright mover
    found, _dropped = frame_candidates(g, masks, gain_correct=True)
    assert len(found) == 1
    assert found[0].x == pytest.approx(200, abs=2)


def test_oversized_and_elongated_blobs_are_rejected():
    """People are far larger than a shuttle; edges are far thinner."""
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    big = _blank()
    big[150:260, 100:300] = 230                   # a person-sized region
    assert frame_candidates(big, masks)[0] == []
    thin = _blank()
    thin[200:203, 100:300] = 230                  # a long thin streak
    assert frame_candidates(thin, masks)[0] == []


def test_default_budget_does_not_bind_at_realistic_load():
    """Section 0.23's acceptance criterion: the cap must stop binding.

    Real load measured on the DJI footage peaks at ~66 candidates/frame in the
    busiest band. The default budget must sit clear of that, so it acts as a runaway
    guard rather than an arbitrary truncation shaping recall.
    """
    from badminton_analysis.shuttle_track.classical import DEFAULT_BAND_BUDGET

    assert DEFAULT_BAND_BUDGET >= 80, "budget must clear the measured ~66/frame peak"

    masks = build_masks(_frames(), QUAD, band_top_px=60)
    g = _blank()
    for i in range(70):                       # 70 blobs crowded into one band
        _put(g, 25 + (i % 35) * 10, 245 + (i // 35) * 8, r=2, v=255)
    _found, dropped = frame_candidates(g, masks)
    assert dropped == 0, f"default budget still truncated {dropped} candidates"


def test_budget_still_bounds_a_pathological_frame():
    """The guard must remain a guard -- an absurd frame is still capped."""
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    g = _blank()
    _found, dropped = frame_candidates(g, masks, band_budget=2)
    # With an explicit tiny budget the mechanism is provably still active; the
    # default merely sits above realistic load.
    g2 = _blank()
    for i in range(12):
        _put(g2, 60 + i * 24, 200, r=3, v=255)
    _f2, d2 = frame_candidates(g2, masks, band_budget=2)
    assert d2 > 0


def test_candidates_carry_area_and_brightness_for_the_tracker():
    """score_chain needs both fields; a Candidate without them is useless."""
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    g = _blank()
    _put(g, 200, 200, r=4, v=250)
    found, _dropped = frame_candidates(g, masks)
    assert len(found) == 1
    c = found[0]
    assert c.area >= 15
    assert c.bright == pytest.approx(250, abs=6)
