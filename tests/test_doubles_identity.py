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


def test_partners_coexisting_between_rallies_get_distinct_ids():
    # Same-kit partners (told apart only by height) are tracked together in frames
    # 120-180, outside every rally. Present at the same frame means different people
    # at every frame, so they must not both become the same player.
    t = [tl(0, "lower", 0, 100, sig(1, 1.30)), tl(1, "lower", 0, 100, sig(1, 1.50)),
         tl(2, "upper", 0, 100, sig(10)), tl(3, "upper", 0, 100, sig(11)),
         tl(4, "lower", 120, 180, sig(1, 1.31)), tl(5, "lower", 120, 180, sig(1, 1.31))]
    r = assign_identities(t, 180, constraint_frames=set(range(0, 100)))
    assert r["assignment"][4] != r["assignment"][5]


def test_no_identity_holds_two_tracklets_at_once():
    t, _ = _walk_around_scenario()
    for cf in (None, set(range(0, 50)) | set(range(60, 111))):
        r = assign_identities(t, 111, constraint_frames=cf)
        by_id = {}
        for tid, pid in r["assignment"].items():
            by_id.setdefault(pid, []).append(set(next(x for x in t if x["id"] == tid)["frames"]))
        for sets in by_id.values():
            for i, a in enumerate(sets):
                for b in sets[i + 1:]:
                    assert not (a & b)


def _scale_scenario(n_frag=600, seed=0):
    """4 players x n_frag staggered fragments, end changes every 100, phantom triples."""
    rng = np.random.default_rng(seed)
    period, length = 62, 60
    colours = {"A1": (1, 1.30), "A2": (2, 1.50), "B1": (10, 1.40), "B2": (11, 1.60)}
    offsets = {"A1": 0, "A2": 15, "B1": 30, "B2": 45}
    tracklets, truth, phantoms = [], {}, set()
    tid = 0
    for k in range(n_frag):
        half = k // 100
        base = k * period + half * 60           # a 60-frame walk-around gap per end change
        for p, (col, h) in colours.items():
            team_a_lower = half % 2 == 0
            side = "lower" if (p[0] == "A") == team_a_lower else "upper"
            start = base + offsets[p]
            s = sig(col, h + float(rng.normal(0, 0.01)))
            tracklets.append(tl(tid, side, start, start + length, s))
            truth[tid] = p
            tid += 1
        if k % 20 == 5:                          # a bystander standing on the lower side
            start = base + 20
            tracklets.append(tl(tid, "lower", start, start + 20, sig(30, 1.7)))
            phantoms.add(tid)
            tid += 1
    total = n_frag * period + (n_frag // 100) * 60 + 110
    return tracklets, truth, phantoms, total


def test_identity_scales():
    import time

    t, truth, phantoms, total = _scale_scenario()
    t0 = time.perf_counter()
    r = assign_identities(t, total)
    elapsed = time.perf_counter() - t0
    assert elapsed < 20.0, elapsed
    assert r["status"] == "ok", r["reason"]
    assert phantoms <= set(r["dropped"])
    ids_of = {}
    for tid, p in truth.items():
        ids_of.setdefault(p, set()).add(r["assignment"].get(tid))
    assert all(len(v) == 1 for v in ids_of.values()), ids_of
    assert len({next(iter(v)) for v in ids_of.values()}) == 4


def test_tracklet_wholly_outside_constraint_frames_is_placed_by_appearance():
    t = [tl(0, "lower", 0, 50, sig(1)), tl(1, "lower", 0, 50, sig(2)),
         tl(2, "upper", 0, 50, sig(10)), tl(3, "upper", 0, 50, sig(11)),
         tl(4, "lower", 50, 60, sig(2))]               # entirely in the walk-around
    r = assign_identities(t, 60, constraint_frames=set(range(0, 50)))
    assert r["assignment"][4] == r["assignment"][1]
    assert 0.0 < r["confidence"][4] <= 1.0
