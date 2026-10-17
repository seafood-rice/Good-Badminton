# tests/test_doubles_identity.py
import numpy as np

from badminton_analysis.tracking.identity import assign_identities


def hist(i):
    h = np.full(64, 0.001)
    h[i] = 1.0
    return h / h.sum()


def sig(color, height=1.3):
    return {"torso": hist(color), "legs": hist(color + 20), "height": height, "embed": None}


def tl(tid, side, start, end, s):
    y = 10.0 if side == "lower" else 3.0
    frames = list(range(start, end))
    return {"id": tid, "side": side, "frames": frames,
            "court": [[3.0, y]] * len(frames), "image": [[300.0, y * 50]] * len(frames),
            "keypoints": [None] * len(frames), "appearance": [s] * 3}


def person_of(result, tid):
    return result["assignment"][tid]


def test_four_continuous_players_ok():
    t = [tl(0, "lower", 0, 100, sig(1)), tl(1, "lower", 0, 100, sig(2)),
         tl(2, "upper", 0, 100, sig(10)), tl(3, "upper", 0, 100, sig(11))]
    r = assign_identities(t, 100)
    assert r["status"] == "ok"
    assert len(set(r["assignment"].values())) == 4
    assert r["teams"][person_of(r, 0)] == r["teams"][person_of(r, 1)] == 1
    assert r["teams"][person_of(r, 2)] == r["teams"][person_of(r, 3)] == 2
    assert all(c == 1.0 for c in r["confidence"].values())


def test_fragmented_tracklets_rejoin_by_appearance():
    # Each player's track breaks at frame 50; second halves must rejoin the right person.
    t = [tl(0, "lower", 0, 50, sig(1)), tl(1, "lower", 0, 50, sig(2)),
         tl(2, "upper", 0, 50, sig(10)), tl(3, "upper", 0, 50, sig(11)),
         tl(4, "lower", 50, 100, sig(2)), tl(5, "lower", 50, 100, sig(1)),
         tl(6, "upper", 50, 100, sig(11)), tl(7, "upper", 50, 100, sig(10))]
    r = assign_identities(t, 100)
    assert r["status"] == "ok"
    assert person_of(r, 4) == person_of(r, 1) and person_of(r, 5) == person_of(r, 0)
    assert person_of(r, 6) == person_of(r, 3) and person_of(r, 7) == person_of(r, 2)


def test_end_change_keeps_identity_across_sides():
    # Team A (colours 1,2) lower then upper; team B (10,11) upper then lower.
    t = [tl(0, "lower", 0, 50, sig(1)), tl(1, "lower", 0, 50, sig(2)),
         tl(2, "upper", 0, 50, sig(10)), tl(3, "upper", 0, 50, sig(11)),
         tl(4, "upper", 60, 110, sig(1)), tl(5, "upper", 60, 110, sig(2)),
         tl(6, "lower", 60, 110, sig(10)), tl(7, "lower", 60, 110, sig(11))]
    r = assign_identities(t, 110)
    assert person_of(r, 4) == person_of(r, 0) and person_of(r, 5) == person_of(r, 1)
    assert person_of(r, 6) == person_of(r, 2) and person_of(r, 7) == person_of(r, 3)
    assert r["teams"][person_of(r, 0)] == 1      # team first seen on the lower side


def test_matching_kits_separated_by_height():
    t = [tl(0, "lower", 0, 50, sig(1, 1.20)), tl(1, "lower", 0, 50, sig(1, 1.50)),
         tl(2, "upper", 0, 100, sig(10)), tl(3, "upper", 0, 100, sig(11)),
         tl(4, "lower", 50, 100, sig(1, 1.49)), tl(5, "lower", 50, 100, sig(1, 1.21))]
    r = assign_identities(t, 100)
    assert person_of(r, 4) == person_of(r, 1) and person_of(r, 5) == person_of(r, 0)


def test_three_people_on_one_side_drops_the_shortest():
    t = [tl(0, "lower", 0, 100, sig(1)), tl(1, "lower", 0, 100, sig(2)),
         tl(9, "lower", 10, 30, sig(5)),
         tl(2, "upper", 0, 100, sig(10)), tl(3, "upper", 0, 100, sig(11))]
    r = assign_identities(t, 100)
    assert 9 in r["dropped"] and 9 not in r["assignment"]


def test_only_near_side_visible_is_degraded():
    t = [tl(0, "lower", 0, 100, sig(1)), tl(1, "lower", 0, 100, sig(2))]
    r = assign_identities(t, 100)
    assert r["status"] == "degraded" and "identities" in r["reason"]


def test_no_tracklets_is_degraded():
    r = assign_identities([], 100)
    assert r["status"] == "degraded" and r["assignment"] == {}


def test_indistinguishable_teams_degraded_teams():
    # Second half has every tracklet in identical kit/height: team placement is a coin flip.
    same = sig(1, 1.3)
    t = [tl(0, "lower", 0, 50, same), tl(1, "lower", 0, 50, same),
         tl(2, "upper", 0, 50, same), tl(3, "upper", 0, 50, same),
         tl(4, "lower", 60, 110, same), tl(5, "lower", 60, 110, same),
         tl(6, "upper", 60, 110, same), tl(7, "upper", 60, 110, same)]
    r = assign_identities(t, 110)
    assert r["status"] == "degraded_teams"
    assert all(v is None for v in r["teams"].values())
