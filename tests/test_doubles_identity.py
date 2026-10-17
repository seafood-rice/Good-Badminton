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


def _walk_around_scenario():
    # Frames 50-59 are the end-change walk-around: A1 is still on the lower side while B1
    # has already crossed to it. Their lower-side tracklets share frames 52-59, which the
    # plain same-side-means-same-team rule would read as "partners".
    t = [tl(0, "lower", 0, 60, sig(1)), tl(1, "lower", 0, 50, sig(2)),        # A1, A2
         tl(2, "upper", 0, 50, sig(10)), tl(3, "upper", 0, 50, sig(11)),      # B1, B2
         tl(4, "upper", 60, 111, sig(1)), tl(5, "upper", 60, 111, sig(2)),    # A1, A2
         tl(6, "lower", 52, 111, sig(10)), tl(7, "lower", 60, 111, sig(11))]  # B1, B2
    person = {0: 1, 1: 2, 2: 10, 3: 11, 4: 1, 5: 2, 6: 10, 7: 11}
    return t, person


def _matches_truth(r, person):
    by_colour = {}
    for tid, colour in person.items():
        by_colour.setdefault(colour, set()).add(r["assignment"].get(tid))
    ids = [next(iter(v)) for v in by_colour.values() if len(v) == 1]
    return len(ids) == 4 and len(set(ids)) == 4


def test_walk_around_outside_constraint_frames_does_not_merge_teams():
    t, person = _walk_around_scenario()
    play = set(range(0, 50)) | set(range(60, 111))
    r = assign_identities(t, 111, constraint_frames=play)
    assert _matches_truth(r, person)
    team_of = lambda colour: r["teams"][r["assignment"][next(k for k, c in person.items() if c == colour)]]
    assert team_of(1) == team_of(2) == 1
    assert team_of(10) == team_of(11) == 2
    # Without the in-play restriction the walk-around overlap merges A1 with B1/B2.
    assert not _matches_truth(assign_identities(t, 111), person)


def test_tracklet_wholly_outside_constraint_frames_is_placed_by_appearance():
    t = [tl(0, "lower", 0, 50, sig(1)), tl(1, "lower", 0, 50, sig(2)),
         tl(2, "upper", 0, 50, sig(10)), tl(3, "upper", 0, 50, sig(11)),
         tl(4, "lower", 50, 60, sig(2))]               # entirely in the walk-around
    r = assign_identities(t, 60, constraint_frames=set(range(0, 50)))
    assert r["assignment"][4] == r["assignment"][1]
    assert 0.0 < r["confidence"][4] <= 1.0
