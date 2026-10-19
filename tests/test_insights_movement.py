"""Task 4: movement grids, recovery to base and pull zones."""
import time

import pytest

from badminton_analysis.insights.loaders import Run, load_run
from badminton_analysis.insights.movement import (
    BASE, GRID_COLS, GRID_ROWS, PULL_GAP_S, RECOVERY_RADIUS_M, RECOVERY_WINDOW_S, cell,
    movement_insight, pull_insight, pull_stats, recovery_insight, recovery_stats)
from badminton_analysis.insights.rally import rally_insight
from tests.insights_fixtures import make_run


def _run(tmp_path, stem="clip", **kw):
    return load_run(make_run(tmp_path, stem, **kw))


def _movement(run):
    return movement_insight(run, rally_insight(run))


def _mass(grid):
    return [(r, c, v) for r, row in enumerate(grid) for c, v in enumerate(row) if v]


# ---- constants and cell -----------------------------------------------------

def test_constants():
    assert (GRID_ROWS, GRID_COLS) == (11, 10)
    assert BASE == {"upper": (3.05, 3.35), "lower": (3.05, 10.05)}
    assert (RECOVERY_RADIUS_M, RECOVERY_WINDOW_S, PULL_GAP_S) == (1.0, 4.0, 3.0)


def test_cell_row_zero_is_at_the_net_for_both_halves():
    # spec 5.3: rows 0 (net) .. 10 (baseline); column 0 at x = 0
    assert cell("lower", 3.05, 6.7) == (0, 5)
    assert cell("lower", 0.0, 6.7) == (0, 0)
    assert cell("lower", 6.1, 13.4) == (10, 9)
    assert cell("upper", 6.09, 6.69) == (0, 9)
    assert cell("upper", 0.0, 0.0) == (10, 0)
    assert cell("upper", 3.05, 6.7) == (0, 5)


def test_cell_bins_follow_the_cell_size():
    assert cell("lower", 0.6, 6.7 + 0.6) == (0, 0)      # 0.609 m rows, 0.61 m columns
    assert cell("lower", 0.62, 6.7 + 0.62) == (1, 1)
    assert cell("upper", 1.0, 6.0) == (1, 1)


def test_cell_clamps_up_to_1_5_m_outside_and_drops_beyond():
    assert cell("upper", 1.0, 1.0 - 1.0) == (10, 1)
    assert cell("upper", -1.0, 3.0) == cell("upper", 0.0, 3.0)
    assert cell("lower", 3.05, 6.7 - 1.0) == (0, 5)      # 1.0 m onto the other half: clamped
    assert cell("lower", 3.05, 13.4 + 1.0) == (10, 5)
    assert cell("upper", 6.1 + 1.0, 3.0) == (6, 9)
    assert cell("upper", 3.05, -1.6) is None
    assert cell("upper", -1.6, 3.0) is None
    assert cell("lower", 3.05, 6.7 - 1.6) is None
    assert cell("lower", 3.05, 13.4 + 1.6) is None
    assert cell("upper", 3.05, 6.7 + 1.5) == (0, 5)      # exactly 1.5 m is kept


# ---- movement ---------------------------------------------------------------

def test_movement_players_at_base_land_in_one_cell(tmp_path):
    ins = _movement(_run(tmp_path, frames=100, rallies=[(0.0, 10.0)]))
    assert ins["status"] == "ok" and ins["reason"] is None
    for side in ("upper", "lower"):
        g = ins["value"][side]
        assert g["samples"] == 100 and g["distance_m"] == 0
        assert g["mean_speed_mps"] == 0
        assert len(g["grid"]) == GRID_ROWS and all(len(r) == GRID_COLS for r in g["grid"])
        assert sum(map(sum, g["grid"])) == pytest.approx(1.0, abs=1e-3)
        assert _mass(g["grid"]) == [(5, 5, 1.0)]
        assert g["base"] == {"x": BASE[side][0], "y": BASE[side][1]}


def test_movement_distance_and_speed(tmp_path):
    def walk(frame, side):
        return (0.5 + 0.1 * frame, 2.0) if side == "upper" else (3.0, 10.0)
    run = _run(tmp_path, frames=30, positions=walk, rallies=[(0.0, 5.0)])
    up = _movement(run)["value"]["upper"]
    assert up["samples"] == 30
    assert up["distance_m"] == pytest.approx(2.9, abs=0.01)
    assert up["mean_speed_mps"] == pytest.approx(3.0, abs=0.05)
    assert _movement(run)["value"]["lower"]["distance_m"] == 0


def test_movement_jump_is_excluded_from_distance(tmp_path):
    def pos(frame, side):
        if side == "lower":
            return (3.0, 10.0)
        return (0.5, 2.0) if frame < 20 else (0.5, 8.0)     # 6 m in one frame (to the lower half)
    run = _run(tmp_path, frames=40, positions=pos, rallies=[(0.0, 5.0)])
    assert _movement(run)["value"]["upper"]["distance_m"] == 0


def test_movement_gap_wider_than_three_frames_is_not_a_step(tmp_path):
    def pos(frame, side):
        if side == "lower":
            return (3.0, 10.0)
        return None if 10 <= frame < 14 else (0.5 + 0.1 * (frame % 2), 2.0)
    run = _run(tmp_path, frames=60, positions=pos, rallies=[(0.0, 5.0)])
    up = _movement(run)["value"]["upper"]
    assert up["samples"] == 56
    # 55 successive pairs minus the 5-frame hop (frame 9 -> 14) = 54 steps of 0.1 m
    assert up["distance_m"] == pytest.approx(5.4, abs=0.01)


def test_movement_under_thirty_samples_is_unavailable(tmp_path):
    ins = _movement(_run(tmp_path, frames=29, rallies=[(0.0, 5.0)]))
    assert ins["status"] == "unavailable" and ins["value"] is None
    assert ins["reason"]["code"] == "no_positions"
    assert _movement(_run(tmp_path, "b", frames=30, rallies=[(0.0, 5.0)]))["status"] == "ok"


def test_movement_no_detections_is_unavailable(tmp_path):
    ins = _movement(_run(tmp_path))
    assert ins["status"] == "unavailable" and ins["reason"]["code"] == "no_positions"


def test_movement_missing_side_is_null(tmp_path):
    run = _run(tmp_path, frames=60, positions={"upper": (1.0, 1.0)}, rallies=[(0.0, 5.0)])
    ins = _movement(run)
    assert ins["status"] == "ok"
    assert ins["value"]["lower"] is None and ins["value"]["upper"]["samples"] == 60


def test_movement_only_counts_frames_inside_rally_windows(tmp_path):
    def pos(frame, side):
        inside = 60 <= frame <= 120                              # t in [2, 4] at 30 fps
        y = 5.0 if inside else 1.0
        return (1.0, y) if side == "upper" else (1.0, y + 6.7)
    run = _run(tmp_path, frames=300, positions=pos, rallies=[(2.0, 4.0)])
    ins = _movement(run)
    assert ins["status"] == "ok"
    up = ins["value"]["upper"]
    assert up["samples"] == 61
    assert [(r, c) for r, c, _ in _mass(up["grid"])] == [cell("upper", 1.0, 5.0)]
    assert ins["coverage"] == {"n": 122, "of": 122}


def test_movement_swing_windows_still_filter(tmp_path):
    run = _run(tmp_path, frames=300, rallies=[(2.0, 4.0)], signal="swing")
    ins = _movement(run)
    assert ins["status"] == "ok" and ins["value"]["upper"]["samples"] == 61


def test_movement_without_rallies_uses_all_frames_and_is_partial(tmp_path):
    for kw in ({}, {"rallies": []}, {"rallies": [(2.0, 4.0)], "signal": "degraded"},
               {"rallies": [(2.0, 4.0)], "signal": "error"}):
        ins = _movement(_run(tmp_path, frames=300, **kw))
        assert ins["status"] == "partial", kw
        assert ins["reason"]["code"] == "no_rallies", kw
        assert ins["value"]["upper"]["samples"] == 300, kw


def test_movement_drops_points_far_outside_and_clamps_near_ones(tmp_path):
    def pos(frame, side):
        if side == "lower":
            return (3.0, 10.0)
        return (-1.0, 3.0) if frame < 40 else (3.05, -2.0)       # clamped, then dropped
    ins = _movement(_run(tmp_path, frames=80, positions=pos))
    up = ins["value"]["upper"]
    assert up["samples"] == 40
    assert _mass(up["grid"]) == [(6, 0, 1.0)]


def test_movement_grid_shares_are_rounded_to_four_places(tmp_path):
    def pos(frame, side):
        return (1.0, 5.0) if frame % 3 else (5.0, 1.0)
    grid = _movement(_run(tmp_path, frames=100, positions=pos))["value"]["upper"]["grid"]
    assert all(v == round(v, 4) for row in grid for v in row)
    assert sum(map(sum, grid)) == pytest.approx(1.0, abs=1e-3)


# ---- recovery ---------------------------------------------------------------

def _away_until(side, until_frame, offset=3.0, only="lower"):
    """The ``only`` side stands ``offset`` m past its base until ``until_frame``."""
    def pos(frame, s):
        bx, by = BASE[s]
        if s == only and frame < until_frame:
            return (bx, by + offset)
        return (bx, by)
    return pos


def test_recovery_time_is_the_first_frame_back_inside_one_metre(tmp_path):
    run = _run(tmp_path, frames=300, hits=[(1.0, "lower")], positions=_away_until("lower", 54))
    stats, found, total = recovery_stats(run)
    assert (found, total) == (1, 1)
    assert stats["lower"] == {"median_sec": 0.8, "found": 1, "hits": 1}
    assert stats["upper"] == {"median_sec": None, "found": 0, "hits": 0}


def test_recovery_already_inside_is_zero(tmp_path):
    run = _run(tmp_path, frames=300, hits=[(1.0, "upper")])
    stats, found, _ = recovery_stats(run)
    assert found == 1 and stats["upper"]["median_sec"] == 0.0


def test_recovery_radius_is_inclusive_at_one_metre(tmp_path):
    inside = _run(tmp_path, "a", frames=300, hits=[(1.0, "lower")],
                  positions=_away_until("lower", 10 ** 6, offset=1.0))
    outside = _run(tmp_path, "b", frames=300, hits=[(1.0, "lower")],
                   positions=_away_until("lower", 10 ** 6, offset=1.01))
    assert recovery_stats(inside)[1] == 1 and recovery_stats(outside)[1] == 0


def test_recovery_never_returning_within_four_seconds_is_not_found(tmp_path):
    # back at base at t = 5.2 s (frame 156): 4.2 s after the hit
    run = _run(tmp_path, frames=300, hits=[(1.0, "lower"), (1.0, "upper")],
               positions=_away_until("lower", 156))
    stats, found, total = recovery_stats(run)
    assert (found, total) == (1, 2)
    assert stats["lower"] == {"median_sec": None, "found": 0, "hits": 1}
    assert stats["upper"]["found"] == 1


def test_recovery_four_seconds_exactly_is_found(tmp_path):
    run = _run(tmp_path, frames=300, hits=[(1.0, "lower")], positions=_away_until("lower", 150))
    stats, found, _ = recovery_stats(run)
    assert found == 1 and stats["lower"]["median_sec"] == 4.0


def test_recovery_median_ignores_unfound_hits(tmp_path):
    def pos(frame, s):
        bx, by = BASE[s]
        away = (30 <= frame < 33) or (90 <= frame < 99) or (150 <= frame < 400)
        return (bx, by + 3.0) if s == "lower" and away else (bx, by)
    run = _run(tmp_path, frames=300, hits=[(1.0, "lower"), (3.0, "lower"), (5.0, "lower")],
               positions=pos)
    stats, found, total = recovery_stats(run)
    assert (found, total) == (2, 3)
    assert stats["lower"]["median_sec"] == pytest.approx(0.2, abs=0.001)   # median(0.1, 0.3)
    assert stats["lower"]["hits"] == 3


def _hits_run(tmp_path, n, stem="clip"):
    hits = [(1.0 + i, "lower" if i % 2 else "upper") for i in range(n)]
    return _run(tmp_path, stem, frames=30 * (n + 6), hits=hits)


def test_recovery_status_by_found_count(tmp_path):
    ok = recovery_insight(_hits_run(tmp_path, 12, "a"))
    assert ok["status"] == "ok" and ok["reason"] is None
    assert ok["coverage"] == {"n": 12, "of": 12}
    assert ok["value"]["upper"]["found"] == 6 and ok["value"]["lower"]["median_sec"] == 0.0
    assert recovery_insight(_hits_run(tmp_path, 10, "e"))["status"] == "ok"
    few = recovery_insight(_hits_run(tmp_path, 5, "b"))
    assert few["status"] == "partial" and few["reason"]["code"] == "few_hits"
    assert few["coverage"] == {"n": 5, "of": 5} and few["value"] is not None
    assert recovery_insight(_hits_run(tmp_path, 3, "f"))["status"] == "partial"
    low = recovery_insight(_hits_run(tmp_path, 2, "c"))
    assert low["status"] == "unavailable" and low["reason"]["code"] == "no_hits"
    assert low["value"] is None


def test_recovery_with_no_hits_is_unavailable(tmp_path):
    for kw in ({}, {"hits": []}):
        ins = recovery_insight(_run(tmp_path, frames=100, **kw))
        assert ins["status"] == "unavailable" and ins["reason"]["code"] == "no_hits"


def test_recovery_without_positions_is_unavailable(tmp_path):
    ins = recovery_insight(_run(tmp_path, hits=[(1.0, "lower")]))
    assert ins["status"] == "unavailable"


def test_recovery_unfound_hits_count_towards_the_total(tmp_path):
    run = _run(tmp_path, frames=300, hits=[(1.0, "lower")] * 12,
               positions=_away_until("lower", 10 ** 6))
    ins = recovery_insight(run)
    assert ins["status"] == "unavailable" and ins["reason"]["code"] == "no_hits"
    assert recovery_stats(run)[2] == 12


def test_recovery_falls_back_to_strokes_frames(tmp_path):
    run = _run(tmp_path, frames=300, strokes=[("lower", "net", 30)],
               positions=_away_until("lower", 54))
    assert run.hits is None
    stats, found, total = recovery_stats(run)
    assert (found, total) == (1, 1) and stats["lower"]["median_sec"] == 0.8


def test_recovery_hits_json_wins_over_strokes(tmp_path):
    run = _run(tmp_path, frames=300, hits=[(2.0, "upper")],
               strokes=[("lower", "net", 30), ("lower", "net", 60)])
    assert recovery_stats(run)[2] == 1


def test_recovery_strokes_need_fps_and_a_known_hitter(tmp_path):
    out = make_run(tmp_path, frames=100, strokes=[{"frame": 30, "hitter": "nobody"},
                                                  {"hitter": "lower"}, ("lower", "net", 30)])
    run = load_run(out)
    assert recovery_stats(run)[2] == 1
    run.metadata = None
    assert recovery_stats(run)[2] == 0


# ---- pull -------------------------------------------------------------------

def test_pull_belongs_to_the_player_who_hit_first(tmp_path):
    run = _run(tmp_path, frames=300, hits=[(1.0, "lower"), (2.0, "upper")],
               positions={"upper": (1.0, 6.0), "lower": (3.0, 10.0)})
    pull, used, eligible = pull_stats(run)
    assert (used, eligible) == (1, 1)
    low = pull["lower"]
    assert low["n"] == 1
    assert _mass(low["grid"]) == [(1, 1, 1.0)]
    assert (low["front"], low["mid"], low["back"]) == (1.0, 0.0, 0.0)
    assert (low["left"], low["right"]) == (1.0, 0.0)
    up = pull["upper"]
    assert up["n"] == 0 and up["front"] == 0.0 and _mass(up["grid"]) == []


def test_pull_zones_split_rows_and_columns(tmp_path):
    spots = [(1.0, 6.0), (5.0, 3.0), (3.0, 0.2), (5.9, 0.2)]     # front-left, mid-right, back x2
    hits = []
    for i, _ in enumerate(spots):
        hits += [(1.0 + 2 * i, "lower"), (2.0 + 2 * i, "upper")]

    def pos(frame, side):
        if side == "lower":
            return (3.0, 10.0)
        return spots[min(max(frame - 45, 0) // 60, 3)]
    run = _run(tmp_path, frames=400, hits=hits, positions=pos)
    low = pull_stats(run)[0]["lower"]
    assert low["n"] == 4
    assert (low["front"], low["mid"], low["back"]) == (0.25, 0.25, 0.5)
    assert (low["left"], low["right"]) == (0.5, 0.5)
    assert sum(map(sum, low["grid"])) == pytest.approx(1.0, abs=1e-3)


def test_pull_ignores_same_hitter_and_wide_gaps(tmp_path):
    run = _run(tmp_path, frames=600, hits=[(1.0, "lower"), (2.0, "lower"),    # same hitter
                                           (5.5, "upper"),                      # gap 3.5 s
                                           (9.0, "lower")])                     # gap 3.5 s
    assert pull_stats(run)[1:] == (0, 0)


def test_pull_gap_of_exactly_three_seconds_counts(tmp_path):
    run = _run(tmp_path, frames=300, hits=[(1.0, "lower"), (4.0, "upper")])
    assert pull_stats(run)[1:] == (1, 1)


def test_pull_orders_hits_by_frame(tmp_path):
    run = _run(tmp_path, frames=300, hits=[(2.0, "upper"), (1.0, "lower")])
    assert pull_stats(run)[0]["lower"]["n"] == 1


def test_pull_b_position_must_be_within_five_frames(tmp_path):
    def gap(lo, hi):
        return lambda frame, side: None if side == "upper" and lo <= frame <= hi else \
            BASE[side]
    hits = [(1.0, "lower"), (2.0, "upper")]                       # B's frame is 60
    skipped = _run(tmp_path, "a", frames=300, hits=hits, positions=gap(50, 70))
    assert pull_stats(skipped)[1:] == (0, 1)
    near = _run(tmp_path, "b", frames=300, hits=hits, positions=gap(56, 70))   # frame 55 is 5 away
    assert pull_stats(near)[1:] == (1, 1)
    too_far = _run(tmp_path, "c", frames=300, hits=hits, positions=gap(55, 70))
    assert pull_stats(too_far)[1:] == (0, 1)


def test_pull_skips_b_far_outside_the_court(tmp_path):
    run = _run(tmp_path, frames=300, hits=[(1.0, "lower"), (2.0, "upper")],
               positions={"upper": (3.0, -3.0), "lower": (3.0, 10.0)})
    assert pull_stats(run)[1:] == (0, 1)


def test_pull_status_by_pair_count(tmp_path):
    ok = pull_insight(_hits_run(tmp_path, 11, "a"))
    assert ok["status"] == "ok" and ok["coverage"] == {"n": 10, "of": 10}
    assert ok["value"]["lower"]["n"] == 5 and ok["value"]["upper"]["n"] == 5
    assert ok["value"]["lower"]["mid"] == 1.0
    few = pull_insight(_hits_run(tmp_path, 5, "b"))
    assert few["status"] == "partial" and few["reason"]["code"] == "few_hits"
    assert few["coverage"] == {"n": 4, "of": 4}
    low = pull_insight(_hits_run(tmp_path, 3, "c"))
    assert low["status"] == "unavailable" and low["reason"]["code"] == "no_hits"
    assert pull_insight(_run(tmp_path, "d", frames=50))["reason"]["code"] == "no_hits"
    assert pull_insight(_run(tmp_path, "e", hits=[(1.0, "lower")]))["status"] == "unavailable"


# ---- performance ------------------------------------------------------------

def test_fifty_thousand_frames_complete_quickly():
    fps = 60.0
    detections = []
    for i in range(50000):
        s = (i % 120) / 120.0
        detections.append((i, i / fps, {
            "upper": (1.0 + 4.0 * s, 1.0 + 3.0 * s) if i % 7 else None,
            "lower": (5.0 - 4.0 * s, 9.0 + 3.0 * s)}))
    hits = [(1.0 + 1.5 * k, round((1.0 + 1.5 * k) * fps), "lower" if k % 2 else "upper")
            for k in range(500)]
    run = Run(stem="big", out_dir=None, metadata={"video": {"fps": fps}},
              detections=detections, hits=hits,
              rallies_payload={"fps": fps, "detection": {"signal": "shuttle", "degraded": False},
                               "rallies": [{"start_sec": 10.0 * k, "end_sec": 10.0 * k + 6.0}
                                           for k in range(80)]})
    t0 = time.perf_counter()
    mv = movement_insight(run, rally_insight(run))
    rec = recovery_insight(run)
    pull = pull_insight(run)
    assert time.perf_counter() - t0 < 3.0
    assert mv["status"] == "ok" and mv["value"]["upper"]["samples"] > 1000
    assert rec["status"] in ("ok", "partial", "unavailable")
    assert pull["status"] in ("ok", "partial", "unavailable")
