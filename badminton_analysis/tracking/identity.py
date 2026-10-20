"""Constrained global assignment of tracklets to four players (spec 6.4).

Pure. Two stages, each a 2-colouring under hard constraints, with appearance
deciding only what the constraints leave open:

1. Teams. Same side + overlapping in time => same team (partners share a side);
   opposite sides + overlapping => different teams.
2. Partners. Same team + overlapping => different partners.

Constraint-forced placements get confidence 1.0; appearance-decided ones get
the margin between the two choices.

The team rule only holds while the rally is in play. Between rallies players
walk around and cross the net at different times (end changes), so one player of
each team can briefly stand on the same side and would be wrongly read as
partners. ``constraint_frames`` (a set of frame numbers) restricts the
coexistence used by the stage-1 team rule to those frames; a tracklet with no
frame inside it gets no team constraint and is placed by appearance. ``None``
applies the team rule to all frames.

"Present at the same frame => different people" holds at every frame, so the
same-side triple check, the stage-2 partner cannot-links and the final check
that no identity holds two tracklets at once all use each tracklet's FULL frame
set, as do coverage and labelling.

Coexisting pairs are computed once (interval sweep plus an exact set test for
interval-overlapping pairs); dropped tracklets are removed from those
structures rather than rebuilt, so long matches (thousands of tracklets) stay
fast.
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


def _sig(members):
    samples = [s for m in members for s in m.get("appearance") or []]
    return ap.median_signature(samples)


def _memo(fn):
    cache = {}

    def wrapped(key):
        if key not in cache:
            cache[key] = fn(key)
        return cache[key]
    return wrapped


def _coexisting_pairs(tl):
    """Sorted pairs (a, b), a < b, of tracklets sharing at least one frame (full sets).

    Interval sweep over (first, last) frame; the exact set test runs only for
    pairs whose intervals overlap.
    """
    pairs, active = [], []
    for n in sorted(tl, key=lambda k: (tl[k]["_lo"], k)):
        lo, fs = tl[n]["_lo"], tl[n]["_fs"]
        active = [(hi, m) for hi, m in active if hi >= lo]
        for _, m in active:
            if not fs.isdisjoint(tl[m]["_fs"]):
                pairs.append((min(n, m), max(n, m)))
        active.append((tl[n]["_hi"], n))
    pairs.sort()
    return pairs


class _Graph:
    """Live tracklets plus their coexistence pairs; drops remove nodes in place."""

    def __init__(self, tl, cf, dropped):
        self.tl = tl
        self.dropped = dropped
        self.full = _coexisting_pairs(tl)
        self.team_pairs = (self.full if cf is None else
                           [(a, b) for a, b in self.full
                            if not tl[a]["_cs"].isdisjoint(tl[b]["_cs"])])
        self.nbr = defaultdict(set)
        for a, b in self.full:
            self.nbr[a].add(b)
            self.nbr[b].add(a)

    def alive(self, pairs):
        return [(a, b) for a, b in pairs if a in self.tl and b in self.tl]

    def drop(self, n):
        self.dropped.append(n)
        del self.tl[n]
        for m in self.nbr.pop(n, ()):
            self.nbr[m].discard(n)

    def drop_same_side_triples(self):
        """Drop the shortest of every three same-side tracklets sharing a frame.

        Three people on one side at once is impossible in doubles, so one of them
        (the shortest, i.e. the cheapest to lose) is a bystander or a duplicate.
        Triples are visited in id order; removing a node never creates a triple,
        so one pass (re-checking the current node after each drop) finds them all.
        """
        tl = self.tl
        for a in sorted(tl):
            while a in tl:
                found = self._first_triple(a)
                if found is None:
                    break
                self.drop(min(found, key=lambda k: len(tl[k]["frames"])))

    def _first_triple(self, a):
        tl = self.tl
        peers = sorted(b for b in self.nbr[a] if b > a and tl[b]["side"] == tl[a]["side"])
        for x, b in enumerate(peers):
            ab = tl[a]["_fs"] & tl[b]["_fs"]
            for c in peers[x + 1:]:
                if c in self.nbr[b] and not ab.isdisjoint(tl[c]["_fs"]):
                    return a, b, c
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


def _weighted_median(values, weights):
    """Median of values, each counted with its weight; equal weights give np.median."""
    order = sorted(zip(values, weights))
    total = float(sum(w for _, w in order))
    cum = 0.0
    for i, (v, w) in enumerate(order):
        cum += w
        if 2 * cum > total:
            return float(v)
        if 2 * cum == total and i + 1 < len(order):
            return (float(v) + float(order[i + 1][0])) / 2
    return float(order[-1][0])


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
            tl[t["id"]] = dict(t, _fs=fs, _cs=fs if cf is None else fs & cf,
                               _lo=min(fs), _hi=max(fs))
    if not tl:
        result["reason"] = "no tracklets"
        return result
    g = _Graph(tl, cf, result["dropped"])
    no_left = "no tracklets after resolving conflicts"

    # --- Stage 1: teams ---------------------------------------------------
    # Three simultaneous same-side people is impossible at any frame.
    g.drop_same_side_triples()
    if not tl:
        result["reason"] = no_left
        return result
    while True:
        ids = sorted(tl)
        uf = _UF(ids)
        same, opposite = [], []
        for a, b in g.alive(g.team_pairs):
            (same if tl[a]["side"] == tl[b]["side"] else opposite).append((a, b))
        for a, b in same:
            uf.union(a, b)
        comps = defaultdict(list)
        for i in ids:
            comps[uf.find(i)].append(i)
        # Opposite sides at once inside one same-team component is impossible:
        # take the last such pair of the first (lowest-id) offending component.
        bad = None
        inner = [(a, b) for a, b in opposite if uf.find(a) == uf.find(b)]
        if inner:
            first = min(comps[uf.find(a)][0] for a, _ in inner)
            bad = max(p for p in inner if comps[uf.find(p[0])][0] == first)
        if bad is None:
            cannot = set()
            for a, b in opposite:
                ca, cb = uf.find(a), uf.find(b)
                if ca != cb:
                    cannot.add((min(ca, cb), max(ca, cb)))
            groups, conflict = _two_colour(sorted(comps), cannot)
            if conflict is not None:
                bad = conflict
        if bad is None:
            break
        a, b = bad
        g.drop(min({a, b}, key=lambda k: len(tl[k]["frames"])))
        if not tl:
            result["reason"] = no_left
            return result

    comp_members = {c: comps[c] for c in comps}
    weight = lambda c: sum(len(tl[m]["frames"]) for m in comp_members[c])
    sig_of_comp = _memo(lambda c: _sig([tl[m] for m in comp_members[c]]))
    comp_conf = {}
    team_of_comp = _orient(groups, weight, sig_of_comp, comp_conf)
    team = {m: team_of_comp[c] for c, ms in comp_members.items() for m in ms}
    conf = {m: comp_conf[c] for c, ms in comp_members.items() for m in ms}
    forced_comps = set(sorted(groups, key=lambda g: -sum(weight(n) for n in g))[0])
    # Weighted by frames, so a few short orphan components cannot outvote long ones.
    unforced = [(comp_conf[c], weight(c)) for c in comp_members if c not in forced_comps]

    # --- Stage 2: partners within each team ------------------------------
    # Coexisting at ANY frame (full frame sets) => different partners.
    partner = {}
    sig_of_node = _memo(lambda n: _sig([tl[n]]))
    for k in (0, 1):
        members = sorted(m for m in tl if team[m] == k)
        if not members:
            continue
        while True:
            edges = [(a, b) for a, b in g.alive(g.full) if team[a] == k and team[b] == k]
            pgroups, conflict = _two_colour(members, edges)
            if conflict is None:
                break
            drop = min(conflict, key=lambda n: len(tl[n]["frames"]))
            members.remove(drop)
            g.drop(drop)
        pconf = {}
        oriented = _orient(pgroups, lambda n: len(tl[n]["frames"]), sig_of_node, pconf)
        for n, c in oriented.items():
            partner[n] = c
            conf[n] = min(conf.get(n, 1.0), pconf.get(n, 1.0))

    # Safety net: one identity can never hold two tracklets at the same frame.
    for a, b in g.full:
        if a in tl and b in tl and (team[a], partner[a]) == (team[b], partner[b]):
            g.drop(min((a, b), key=lambda n: len(tl[n]["frames"])))
    if not tl:
        result["reason"] = no_left
        return result

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
    elif unforced and _weighted_median(*zip(*unforced)) < team_conf_min:
        result["status"] = "degraded_teams"
        result["reason"] = "teams cannot be told apart by appearance"
        result["teams"] = {p: None for p in teams}
    else:
        result["status"] = "ok"
    return result
