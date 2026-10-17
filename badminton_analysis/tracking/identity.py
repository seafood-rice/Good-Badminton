"""Constrained global assignment of tracklets to four players (spec 6.4).

Pure. Two stages, each a 2-colouring under hard constraints, with appearance
deciding only what the constraints leave open:

1. Teams. Same side + overlapping in time => same team (partners share a side);
   opposite sides + overlapping => different teams.
2. Partners. Same team + overlapping => different partners.

Constraint-forced placements get confidence 1.0; appearance-decided ones get
the margin between the two choices.

Constraints only hold while the rally is in play. Between rallies players walk
around and cross the net at different times (end changes), so one player of each
team can briefly stand on the same side and would be wrongly read as partners.
``constraint_frames`` (a set of frame numbers) restricts the coexistence used by
every constraint to those frames; coverage and labelling still use each
tracklet's full frame set. A tracklet with no frame inside it has no constraints
and is placed purely by appearance. ``None`` applies constraints to all frames.
"""
from collections import defaultdict

import numpy as np

from . import appearance as ap


class _UF:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _coexist(a, b):
    return bool(a["_cs"] & b["_cs"])


def _sig(members):
    samples = [s for m in members for s in m.get("appearance") or []]
    return ap.median_signature(samples)


def _same_side_triple(tl, ids):
    """Shortest of the first three same-side tracklets that share a frame, or None.

    Three people on one side at once is impossible in doubles, so one of them
    (the shortest, i.e. the cheapest to lose) is a bystander or a duplicate.
    """
    for i, a in enumerate(ids):
        peers = [b for b in ids[i + 1:]
                 if tl[b]["side"] == tl[a]["side"] and _coexist(tl[a], tl[b])]
        for x, b in enumerate(peers):
            for c in peers[x + 1:]:
                if tl[a]["_cs"] & tl[b]["_cs"] & tl[c]["_cs"]:
                    return min((a, b, c), key=lambda k: len(tl[k]["frames"]))
    return None


def _margin(d_own, d_other):
    tot = d_own + d_other
    if tot <= 0:
        return 0.0
    return float(np.clip(2.0 * d_other / tot - 1.0, 0.0, 1.0))


def _two_colour(nodes, edges):
    """BFS 2-colouring of each connected group. Returns (groups, conflict_edge).

    groups: list of {node: 0|1}. conflict_edge is the first odd-cycle edge or None.
    """
    adj = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    colour, groups = {}, []
    for n in nodes:
        if n in colour:
            continue
        colour[n] = 0
        group, queue = {n: 0}, [n]
        while queue:
            u = queue.pop()
            for v in adj[u]:
                if v not in colour:
                    colour[v] = 1 - colour[u]
                    group[v] = colour[v]
                    queue.append(v)
                elif colour[v] == colour[u]:
                    return None, (u, v)
        groups.append(group)
    return groups, None


def _orient(groups, weight, sig_of, conf, base_conf=1.0):
    """Fix the heaviest group's orientation; orient the others by appearance.

    Returns {node: 0|1}; writes per-node confidence into ``conf``.
    """
    groups = sorted(groups, key=lambda g: -sum(weight(n) for n in g))
    out = {}
    for n, c in groups[0].items():
        out[n] = c
        conf[n] = min(conf.get(n, 1.0), base_conf)
    for g in groups[1:]:
        ref = [_sig_nodes([n for n in out if out[n] == k], sig_of) for k in (0, 1)]
        mine = [_sig_nodes([n for n in g if g[n] == k], sig_of) for k in (0, 1)]
        straight = sum(ap.distance(mine[k], ref[k]) for k in (0, 1) if mine[k] is not None)
        swapped = sum(ap.distance(mine[k], ref[1 - k]) for k in (0, 1) if mine[k] is not None)
        flip = swapped < straight
        m = _margin(min(straight, swapped), max(straight, swapped))
        for n, c in g.items():
            out[n] = (1 - c) if flip else c
            conf[n] = min(conf.get(n, 1.0), m)
    return out


def _sig_nodes(nodes, sig_of):
    samples = []
    for n in nodes:
        s = sig_of(n)
        if s is not None:
            samples.append(s)
    return ap.median_signature(samples)


def assign_identities(tracklets, analysed_frames, *, min_coverage=0.2, team_conf_min=0.3,
                    constraint_frames=None):
    result = {"status": "degraded", "reason": None, "assignment": {}, "teams": {},
              "confidence": {}, "coverage": {}, "dropped": []}
    cf = None if constraint_frames is None else set(constraint_frames)
    tl = {}
    for t in tracklets:
        if t.get("frames"):
            fs = set(t["frames"])
            tl[t["id"]] = dict(t, _fs=fs, _cs=fs if cf is None else fs & cf)
    if not tl:
        result["reason"] = "no tracklets"
        return result

    # --- Stage 1: teams ---------------------------------------------------
    while True:
        ids = sorted(tl)
        uf = _UF(ids)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if tl[a]["side"] == tl[b]["side"] and _coexist(tl[a], tl[b]):
                    uf.union(a, b)
        comps = defaultdict(list)
        for i in ids:
            comps[uf.find(i)].append(i)
        # A component with 3+ simultaneous same-side members is impossible.
        bad = None
        for members in comps.values():
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if tl[a]["side"] != tl[b]["side"] and _coexist(tl[a], tl[b]):
                        bad = (a, b)
            if bad:
                break
        if bad is None:
            shortest = _same_side_triple(tl, ids)
            if shortest is not None:
                bad = (shortest, shortest)
        cannot = set()
        if bad is None:
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    if tl[a]["side"] != tl[b]["side"] and _coexist(tl[a], tl[b]):
                        ca, cb = uf.find(a), uf.find(b)
                        if ca != cb:
                            cannot.add((min(ca, cb), max(ca, cb)))
            groups, conflict = _two_colour(sorted(comps), cannot)
            if conflict is not None:
                bad = conflict
        if bad is None:
            break
        a, b = bad
        drop = min({a, b}, key=lambda k: len(tl[k]["frames"]))
        result["dropped"].append(drop)
        del tl[drop]
        if not tl:
            result["reason"] = "no tracklets after resolving conflicts"
            return result

    comp_members = {c: comps[c] for c in comps}
    weight = lambda c: sum(len(tl[m]["frames"]) for m in comp_members[c])
    sig_of_comp = lambda c: _sig([tl[m] for m in comp_members[c]])
    comp_conf = {}
    team_of_comp = _orient(groups, weight, sig_of_comp, comp_conf)
    team = {m: team_of_comp[c] for c, ms in comp_members.items() for m in ms}
    conf = {m: comp_conf[c] for c, ms in comp_members.items() for m in ms}
    forced_comps = set(sorted(groups, key=lambda g: -sum(weight(n) for n in g))[0])
    unforced_conf = [comp_conf[c] for c in comp_members if c not in forced_comps]

    # --- Stage 2: partners within each team ------------------------------
    partner = {}
    for k in (0, 1):
        members = sorted(m for m in tl if team[m] == k)
        if not members:
            continue
        while True:
            edges = [(a, b) for i, a in enumerate(members) for b in members[i + 1:]
                     if _coexist(tl[a], tl[b])]
            pgroups, conflict = _two_colour(members, edges)
            if conflict is None:
                break
            drop = min(conflict, key=lambda n: len(tl[n]["frames"]))
            result["dropped"].append(drop)
            members.remove(drop)
            del tl[drop]
        pconf = {}
        oriented = _orient(pgroups, lambda n: len(tl[n]["frames"]),
                           lambda n: _sig([tl[n]]), pconf)
        for n, c in oriented.items():
            partner[n] = c
            conf[n] = min(conf.get(n, 1.0), pconf.get(n, 1.0))

    # --- Labels -------------------------------------------------------------
    lower_first = min((t for t in tl.values() if t["side"] == "lower"),
                      key=lambda t: t["frames"][0], default=None)
    team1 = team[lower_first["id"]] if lower_first else 0
    label_team = {team1: 1, 1 - team1: 2}
    frames_of = defaultdict(set)
    for n, t in tl.items():
        frames_of[(team[n], partner[n])].update(t["_fs"])
    names = {}
    for k in (0, 1):
        order = sorted((0, 1), key=lambda p: -len(frames_of[(k, p)]))
        base = 1 if label_team[k] == 1 else 3
        for rank, p in enumerate(order):
            names[(k, p)] = f"P{base + rank}"

    assignment = {n: names[(team[n], partner[n])] for n in tl}
    present = sorted(set(assignment.values()))
    coverage = {pid: round(len(frames_of[key]) / max(1, analysed_frames), 4)
                for key, pid in names.items() if pid in present}
    teams = {pid: label_team[key[0]] for key, pid in names.items() if pid in present}

    result.update(assignment=assignment, confidence={n: round(float(conf.get(n, 1.0)), 4) for n in tl},
                  coverage=coverage, teams=teams)
    good = [p for p, c in coverage.items() if c >= min_coverage]
    if len(good) < 4:
        result["status"] = "degraded"
        result["reason"] = (f"{len(good)} of 4 identities cover at least "
                            f"{int(min_coverage * 100)}% of analysed frames")
    elif unforced_conf and float(np.median(unforced_conf)) < team_conf_min:
        result["status"] = "degraded_teams"
        result["reason"] = "teams cannot be told apart by appearance"
        result["teams"] = {p: None for p in teams}
    else:
        result["status"] = "ok"
    return result
