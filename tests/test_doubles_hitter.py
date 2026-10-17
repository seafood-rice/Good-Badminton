# tests/test_doubles_hitter.py
import numpy as np

from badminton_analysis.stroke.hitter import HITTER_MIN_CONF, attribute_hitters

FPS = 30


def kp_at(x, y, wrist_dy=40.0, wrist_dx=30.0):
    kp = np.zeros((17, 2))
    kp[5], kp[6] = (x - 20, y - 120), (x + 20, y - 120)
    kp[9] = (x - wrist_dx, y - 120 + wrist_dy)
    kp[10] = (x + wrist_dx, y - 120 + wrist_dy)
    kp[15], kp[16] = (x - 10, y), (x + 10, y)
    return kp.tolist()


def still(x, y, frames, side="lower"):
    return {f: {"court": (3, 10), "image": (x, y), "keypoints": kp_at(x, y), "side": side}
            for f in frames}


def swinging(x, y, frames, hit_frame, side="lower"):
    out = {}
    for f in frames:
        d = abs(f - hit_frame)
        dy = 40.0 if d > 2 else -60.0 + 50.0 * d       # wrist whips above the shoulder
        out[f] = {"court": (3, 10), "image": (x, y), "keypoints": kp_at(x, y, wrist_dy=dy),
                  "side": side}
    return out


FR = range(0, 60)
TEAMS = {"P1": 1, "P2": 1, "P3": 2, "P4": 2}


def test_swing_evidence_picks_the_swinging_partner():
    tracks = {"P1": swinging(200, 400, FR, 30), "P2": still(450, 420, FR)}
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS, teams=TEAMS)
    assert h["hitter_id"] == "P1" and h["hitter_team"] == 1
    assert h["hitter_evidence"] == "swing"
    assert h["hitter_confidence"] >= HITTER_MIN_CONF
    assert h["hitter"] == "lower"                       # side kept


def test_shuttle_evidence_picks_the_nearest_wrist():
    tracks = {"P1": still(200, 400, FR), "P2": still(450, 420, FR)}
    shuttle = {f: (452.0 + 30, 420.0 - 80) for f in FR}  # at P2's right wrist
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS,
                             teams=TEAMS, shuttle=shuttle)
    assert h["hitter_id"] == "P2" and h["hitter_evidence"] == "shuttle"


def test_single_candidate_named_with_half_confidence():
    tracks = {"P1": still(200, 400, FR), "P3": still(300, 100, FR, side="upper")}
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS, teams=TEAMS)
    assert h["hitter_id"] == "P1"
    assert h["hitter_confidence"] == 0.5 and h["hitter_evidence"] == "single_candidate"


def test_no_candidate_on_side():
    tracks = {"P3": still(300, 100, FR, side="upper")}
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS, teams=TEAMS)
    assert h["hitter_id"] is None and h["hitter_evidence"] == "none"
    assert h["hitter_confidence"] is None


def test_low_confidence_withholds_the_id_but_keeps_the_side():
    tracks = {"P1": still(200, 400, FR), "P2": still(450, 420, FR)}   # nobody swings
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS, teams=TEAMS)
    assert h["hitter_id"] is None and h["hitter"] == "lower"
    assert h["hitter_confidence"] < HITTER_MIN_CONF


def test_input_hits_are_not_mutated():
    hits = [{"frame": 30, "hitter": "lower"}]
    attribute_hitters(hits, {}, FPS)
    assert hits == [{"frame": 30, "hitter": "lower"}]


def test_three_candidates_best_swing_wins():
    # The swinging partner is last in dict order: a truncation to the first two
    # candidates would drop it and wrongly report no clear hitter.
    tracks = {"P2": still(450, 420, FR), "P3": still(300, 300, FR),
              "P1": swinging(200, 400, FR, 30)}
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS, teams=TEAMS)
    assert h["hitter_id"] == "P1" and h["hitter_team"] == 1
    assert h["hitter_evidence"] == "swing"
    assert h["hitter_confidence"] >= HITTER_MIN_CONF


def test_hit_without_side_is_not_attributed():
    # Observations with side None must not match a hit that has no side.
    tracks = {"P1": still(200, 400, FR, side=None)}
    for hit in ({"frame": 30, "hitter": None}, {"frame": 30}):
        (h,) = attribute_hitters([hit], tracks, FPS, teams=TEAMS)
        assert h["hitter_id"] is None and h["hitter_team"] is None
        assert h["hitter_confidence"] is None and h["hitter_evidence"] == "none"
