"""Score-sequence winner decoder (rally-winner-learning spec 6).

A hidden-state model over the match's score: one step per rally segment, each step
either gives a point to the upper or the lower side, or is "not a point". The state
is ``(game, a, b, server, flipped, games_a)``: ``a`` / ``b`` are the points of the two
players (``a`` is the primary record's upper player in game 1), ``server`` is the
player who served (0 / 1, 2 = unknown) and ``flipped`` says whether the players
have changed ends an odd number of times; ``games_a`` is the games ``a`` has won (the
games ``b`` has won follow from ``game``), so the match ends when ``scoring.match_over``.
With ``win_by`` 2 and no cap the deuce scores are folded to their equivalent classes (tied or
one point up, with the parity of the score kept) so the state space is finite. Game ends, end changes and the match end
follow the scoring format (``scoring.py``).

Each signal (landing winner, last hitter, next server) has a table of
P(observation | true winner) counted from labelled rallies with add-one smoothing;
the service court is checked against the court implied by the server's own score
(even: right). Forward-backward gives P(winner) per step. The numbers are scaled
per step rather than kept as logs (the same result); log-likelihoods are summed.

Pure functions; nothing here reads or writes the run directory except ``load``.
"""
import json
import math
from typing import NamedTuple, Optional

import numpy as np

from . import evidence as ev
from . import scoring

SCHEMA = 1
KIND = "rally-decoder"
SIDES = ("upper", "lower")
NOT_POINT = "not_point"
SIGNALS = ("landing", "last_hitter", "next_server")
DEFAULT_P_POINT = 0.9
DEFAULT_CLAIM_THRESHOLD = 0.9
P_EPS = 1e-4
CENTRE_X = 3.05
CENTRE_BAND = 0.15       # a server this close to the centre line has no readable court
FRAME_SLACK = 15         # nearest position within this many frames of the first hit
END = (-1, 0, 0, 2, 0, 0)   # the match is over
START_ZERO = (1, 0, 0, 2, 0, 0)
END_MASS_MIN = 1e-6


class Obs(NamedTuple):
    landing: Optional[str] = None       # E2 verdict winner side
    last_hitter: Optional[str] = None   # E1
    next_server: Optional[str] = None   # E3
    serve_court: Optional[str] = None   # 'left' / 'right' from the server's own view
    p_point: float = DEFAULT_P_POINT


def _side(value):
    return value if value in SIDES else None


def _p_point(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return DEFAULT_P_POINT
    return min(1.0, max(0.0, float(value)))


def _position(inputs, side, frame):
    pos = (inputs.positions or {}).get(side) or {}
    if frame in pos:
        return pos[frame]
    for d in range(1, FRAME_SLACK + 1):
        for f in (frame - d, frame + d):
            if f in pos:
                return pos[f]
    return None


def _serve_court(first_hit, inputs):
    """'left' / 'right' from the server's own view at the first hit; None when unreadable."""
    if first_hit is None or inputs is None:
        return None
    frame, side = first_hit[1], first_hit[2]
    if side not in SIDES or not (inputs.positions_ok or {}).get(side):
        return None
    xy = _position(inputs, side, frame)
    if xy is None:
        return None
    dx = xy[0] - CENTRE_X
    if abs(dx) < CENTRE_BAND:
        return None
    image_right = dx > 0
    left = (not image_right) if side == "lower" else image_right   # mirrored for upper
    return "left" if left else "right"


def observations(segments, inputs, auto_evidence):
    """``[Obs]``, one per segment.

    ``segments`` = ``[{"id", "start_sec", "end_sec", optional "p_point"}]``;
    ``inputs`` = ``run_inputs.Inputs`` (or None); ``auto_evidence`` =
    ``{segment_id: {"evidence": {"landing", "last_hitter", "next_server"}}}``.
    """
    hits = list(inputs.hits) if inputs is not None else []
    out = []
    for seg in segments:
        e = ((auto_evidence or {}).get(seg["id"]) or {}).get("evidence") or {}
        land = e.get("landing")
        inside = ev.rally_hits(hits, seg["start_sec"], seg["end_sec"])
        out.append(Obs(landing=_side(land.get("winner")) if isinstance(land, dict) else None,
                       last_hitter=_side(e.get("last_hitter")),
                       next_server=_side(e.get("next_server")),
                       serve_court=_serve_court(inside[0] if inside else None, inputs),
                       p_point=_p_point(seg.get("p_point"))))
    return out


# ---------------------------------------------------------------- score rules

def _fold(fmt, a, b):
    """Equivalent class of a deuce score of an uncapped win-by-2 game (parity of the score kept)."""
    low = fmt["points_to_win"] - 1
    if fmt["cap"] is not None or fmt["win_by"] != 2 or min(a, b) < low:
        return a, b
    base = low if (min(a, b) - low) % 2 == 0 else low + 1
    return base + max(a - b, 0), base + max(b - a, 0)


def _advance(fmt, final, g, a, b, fl, player, ga=None):
    """The state after ``player`` wins a point, ``END``, or None when ``final`` forbids it.

    ``ga`` = games won by ``a`` so far; None (unknown) ends the match after game ``best_of``."""
    pa, pb = a, b
    if player == 0:
        a += 1
    else:
        b += 1
    over = scoring.game_over(fmt, a, b)
    if final is not None:
        if g > len(final):
            return None
        sa, sb = final[g - 1]
        if a > sa or b > sb or (over and (a, b) != (sa, sb)):
            return None
    change = int(bool(scoring.ends_change(fmt, g, a, b, pa, pb)))
    if over:
        if final is not None:
            last = g >= len(final)
        elif ga is None:
            last = g >= fmt["best_of"]
        else:
            last = scoring.match_over(fmt, ga + (player == 0), (g - 1 - ga) + (player == 1))
        gw = (ga or 0) + (player == 0)
        return END if last else (g + 1, 0, 0, player, fl ^ change, gw)
    if final is None:
        a, b = _fold(fmt, a, b)
    return (g, a, b, player, fl ^ change, ga or 0)


def _free_states(fmt, final):
    top = fmt["cap"] if fmt["cap"] is not None else fmt["points_to_win"] + 2   # folded deuce states reach (p+1, p)
    games = len(final) if final is not None else fmt["best_of"]
    need = fmt["best_of"] // 2 + 1
    out = []
    for g in range(1, games + 1):
        if final is not None:
            gas = [sum(1 for x, y in final[:g - 1] if x > y)]
        else:
            gas = [x for x in range(g) if x < need and g - 1 - x < need]
        for ga in gas:
            for a in range(top):
                for b in range(top):
                    if scoring.game_over(fmt, a, b):
                        continue
                    if final is not None and (a > final[g - 1][0] or b > final[g - 1][1]):
                        continue
                    if final is None and _fold(fmt, a, b) != (a, b):
                        continue
                    for fl in (0, 1):
                        out.append((g, a, b, 2, fl, ga))
    return out


class _Space:
    """The reachable states from a start set, with successor arrays."""

    def __init__(self, fmt, final, starts):
        self.index, self.states = {}, []
        for s in starts:
            self._add(s)
        self.su, self.sl = [], []
        i = 0
        while i < len(self.states):
            s = self.states[i]
            i += 1
            succ = [-1, -1]
            if s != END:
                g, a, b, _sv, fl, ga = s
                for player in (0, 1):
                    nxt = _advance(fmt, final, g, a, b, fl, player, ga)
                    if nxt is not None:
                        succ[player] = self._add(nxt)
            self.su.append(succ[0])
            self.sl.append(succ[1])
        self.n = len(self.states)
        self.su, self.sl = np.array(self.su, dtype=np.int64), np.array(self.sl, dtype=np.int64)
        self.fl = np.array([s[4] for s in self.states], dtype=np.int64)
        self.is_end = np.array([s == END for s in self.states], dtype=bool)
        implied = []
        for s in self.states:
            sv = s[3]
            implied.append(2 if s == END or sv not in (0, 1) else (s[1] if sv == 0 else s[2]) % 2)
        self.implied = np.array(implied, dtype=np.int64)   # 0 right, 1 left, 2 unknown

    def _add(self, s):
        i = self.index.get(s)
        if i is None:
            i = self.index[s] = len(self.states)
            self.states.append(s)
        return i

    def side_successors(self, swapped):
        """Successor index per state when the upper / lower side wins a point."""
        pu = self.fl ^ int(bool(swapped))     # the player on the upper side
        up = np.where(pu == 0, self.su, self.sl)
        lo = np.where(pu == 0, self.sl, self.su)
        return up, lo


# ------------------------------------------------------------- model and fit

def _norm(counts):
    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()}


def _parity_counts(fmt, seq):
    """``(agree, n)`` of observed service courts against the score implied from 0-0."""
    g, a, b, fl, server, ga = 1, 0, 0, 0, None, 0
    agree = n = 0
    for obs, verdict in seq:
        if verdict not in SIDES:
            continue
        if obs is not None and obs.serve_court in ("left", "right") and server is not None:
            n += 1
            implied = "right" if (a if server == 0 else b) % 2 == 0 else "left"
            agree += implied == obs.serve_court
        nxt = _advance(fmt, None, g, a, b, fl, SIDES.index(verdict) ^ fl, ga)
        if nxt == END:
            break
        g, a, b, server, fl, ga = nxt
    return agree, n


def fit(labelled, fmt=None, fmts=None):
    """The decoder model from labelled rallies.

    ``labelled`` is ``[(Obs | None, verdict)]`` in time order for one video (verdict
    ``upper`` / ``lower`` / ``not_point``; an ``Obs`` of None is a point with no
    segment: it moves the score but gives no signal counts), or a list of such lists.
    Parity is counted from 0-0 under the video's own format, per video with the better
    of the two mirror readings: ``fmts`` is one format per video (parallel to the lists),
    else ``fmt`` (default preset) for all.
    """
    fmt = fmt or scoring.resolve(None)
    seqs = labelled if labelled and isinstance(labelled[0], list) else [list(labelled or [])]
    point = {s: {"agree": 1, "disagree": 1, "absent": 1} for s in SIGNALS}
    none = {s: {"present": 1, "absent": 1} for s in SIGNALS}
    points = not_points = agree_total = n_total = 0
    for k, seq in enumerate(seqs):
        seq_fmt = fmts[k] if fmts is not None and k < len(fmts) and fmts[k] else fmt
        for obs, verdict in seq:
            if obs is None:
                points += verdict in SIDES
                continue
            if verdict in SIDES:
                points += 1
                for name in SIGNALS:
                    v = getattr(obs, name)
                    point[name]["absent" if v is None else "agree" if v == verdict else "disagree"] += 1
            elif verdict == NOT_POINT:
                not_points += 1
                for name in SIGNALS:
                    none[name]["absent" if getattr(obs, name) is None else "present"] += 1
        agree, n = _parity_counts(seq_fmt, seq)
        agree_total += max(agree, n - agree)
        n_total += n
    return {"schema": SCHEMA, "kind": KIND,
            "signals": {s: {"point": _norm(point[s]), "none": _norm(none[s])} for s in SIGNALS},
            "parity": {"reliability": (agree_total + 1) / (n_total + 2) if n_total else 0.5, "n": n_total},
            "claim_threshold": DEFAULT_CLAIM_THRESHOLD,
            "counts": {"points": points, "not_points": not_points}}


def _valid_model(doc):
    try:
        if doc.get("schema") != SCHEMA or doc.get("kind") != KIND:
            return False
        for name in SIGNALS:
            t = doc["signals"][name]
            if not all(0 < float(t["point"][k]) <= 1 for k in ("agree", "disagree", "absent")):
                return False
            if not all(0 < float(t["none"][k]) <= 1 for k in ("present", "absent")):
                return False
        return 0 < float(doc["parity"]["reliability"]) < 1
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def load(path):
    """The decoder model at ``path``, or None when absent, unreadable or invalid."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None
    return doc if _valid_model(doc) else None


# -------------------------------------------------------------------- decode

def _emit_point(model, o, side):
    f = 1.0
    for name in SIGNALS:
        v, t = getattr(o, name), model["signals"][name]["point"]
        f *= t["absent"] if v is None else t["agree"] if v == side else t["disagree"]
    return f


def _emit_none(model, o):
    f = 1.0
    for name in SIGNALS:
        t = model["signals"][name]["none"]
        f *= t["absent"] if getattr(o, name) is None else t["present"] / 2
    return f


class _Run:
    def __init__(self, model, fmt, obs, clamps, start, final, swapped):
        self.model, self.obs, self.clamps = model, obs, clamps
        self.n = len(obs)
        self.final = final
        starts = {"zero": [START_ZERO], "free": _free_states(fmt, final)}.get(start) \
            if isinstance(start, str) else [s for s in start if s == END or _legal(fmt, final, s)]
        if not starts:
            raise _Infeasible
        self.space = _Space(fmt, final, starts)
        sp = self.space
        self.up, self.lo = sp.side_successors(swapped)
        self.mu, self.ml = self.up >= 0, self.lo >= 0
        alpha0 = np.zeros(sp.n)
        if isinstance(start, str):
            for s in starts:
                alpha0[sp.index[s]] = 1.0
        else:
            for s in starts:
                alpha0[sp.index[s]] = float(start[s])
        if alpha0.sum() <= 0:
            raise _Infeasible
        self.alpha0 = alpha0 / alpha0.sum()
        self.endmask = sp.is_end.astype(float) if final is not None else np.ones(sp.n)
        r = float(model["parity"]["reliability"])
        self.r = min(1 - P_EPS, max(P_EPS, r))
        self.emit = []
        for i, o in enumerate(obs):
            pp = min(1 - P_EPS, max(P_EPS, o.p_point))
            self.emit.append((pp, _emit_point(model, o, "upper"), _emit_point(model, o, "lower"),
                              _emit_none(model, o)))

    def parity(self, o, mirror):
        sp = self.space
        if o.serve_court not in ("left", "right"):
            return np.ones(sp.n)
        code = 0 if o.serve_court == "right" else 1
        known = sp.implied < 2
        match = (sp.implied ^ int(mirror)) == code
        return np.where(known, np.where(match, self.r, 1 - self.r), 1.0)

    def weights(self, i, mirror):
        S = self.space.n
        c = self.clamps.get(i)
        par = self.parity(self.obs[i], mirror)
        zero = np.zeros(S)
        if c in SIDES:
            return zero, par if c == "upper" else zero, par if c == "lower" else zero
        if c == NOT_POINT:
            return np.ones(S), zero, zero
        pp, eu, el, en = self.emit[i]
        return np.full(S, (1 - pp) * en), pp * eu * par, pp * el * par

    def forward(self, mirror):
        """``(loglik, [alpha_0 .. alpha_n])`` normalised per step, or None when infeasible."""
        S = self.space.n
        alpha, ll, alphas = self.alpha0, 0.0, [self.alpha0]
        for i in range(self.n):
            wn, wu, wl = self.weights(i, mirror)
            act = np.flatnonzero(alpha)    # zero-mass states are skipped
            new = np.zeros(S)
            new[act] = alpha[act] * wn[act]
            for w, succ, ok in ((wu, self.up, self.mu), (wl, self.lo, self.ml)):
                sel = act[ok[act]]
                new += np.bincount(succ[sel], weights=alpha[sel] * w[sel], minlength=S)
            z = new.sum()
            if z <= 0:
                return None
            ll += math.log(z)
            alpha = new / z
            alphas.append(alpha)
        z = float((alpha * self.endmask).sum())
        if z <= 0:
            return None
        return ll + math.log(z), alphas

    def backward(self, mirror, alphas):
        S = self.space.n
        beta = self.endmask.copy()
        post, p = beta, [None] * self.n
        for i in range(self.n - 1, -1, -1):
            wn, wu, wl = self.weights(i, mirror)
            alpha = alphas[i]
            bu = np.where(self.mu, beta[np.where(self.mu, self.up, 0)], 0.0)
            bl = np.where(self.ml, beta[np.where(self.ml, self.lo, 0)], 0.0)
            mass = (float((alpha * wu * bu).sum()), float((alpha * wl * bl).sum()),
                    float((alpha * wn * beta).sum()))
            total = sum(mass)
            p[i] = ({"upper": mass[0] / total, "lower": mass[1] / total, "none": mass[2] / total}
                    if total > 0 else {"upper": 0.0, "lower": 0.0, "none": 1.0})
            beta = wn * beta + wu * bu + wl * bl
            m = beta.max()
            if m > 0:
                beta = beta / m
        end = alphas[-1] * self.endmask
        end = end / end.sum()
        ends = {self.space.states[j]: float(end[j]) for j in np.flatnonzero(end > END_MASS_MIN)}
        return p, ends


class _Infeasible(Exception):
    pass


def _legal(fmt, final, s):
    g, a, b, _sv, _fl, _ga = s
    if scoring.game_over(fmt, a, b):
        return False
    if final is not None and (g > len(final) or a > final[g - 1][0] or b > final[g - 1][1]):
        return False
    return True


def _decode_once(model, fmt, obs, clamps, start, final, swapped):
    try:
        run = _Run(model, fmt, obs, clamps, start, final, swapped)
    except _Infeasible:
        return None
    fits = [(m, run.forward(m)) for m in (False, True)]
    fits = [(m, f) for m, f in fits if f is not None]
    if not fits:
        return None
    mirror, (ll, alphas) = fits[0]
    if len(fits) == 2 and fits[1][1][0] > ll + 1e-9:
        mirror, (ll, alphas) = fits[1]
    p, ends = run.backward(mirror, alphas)
    return {"p": p, "mirror": mirror, "end": ends, "loglik": ll}


def _clamp_only(obs, clamps):
    p = []
    for i in range(len(obs)):
        c = clamps.get(i)
        p.append({"upper": float(c == "upper"), "lower": float(c == "lower"), "none": float(c not in SIDES)})
    return p


def decode(model, fmt, obs, clamps=None, start="zero", final=None, swapped=False):
    """Per-step winner probabilities.

    ``start``: ``'zero'`` (0-0 in game 1, server unknown), ``'free'`` (any reachable
    state) or ``{state: probability}`` (a previous part's ``end``). ``final``: the
    recorded sets ``[(a, b)]`` (``a`` = the record's upper player) the match must
    end on, or None. ``swapped``: the video's upper side is the primary's lower one.
    ``clamps``: ``{index: 'upper' | 'lower' | 'not_point'}`` owner labels (hard).

    Returns ``{"p": [{"upper", "lower", "none"}], "mirror": bool, "final_open": bool,
    "end": {state: probability}, "loglik", "start"}``. When the final constraint
    cannot be met the end is left open (``final_open``); when even the start cannot
    be met the start is freed; when nothing fits, only the clamps are reported.
    """
    clamps = {int(k): v for k, v in (clamps or {}).items() if v in SIDES + (NOT_POINT,)}
    final = [tuple(s) for s in final] if final else None
    tries = [(start, final)]
    if final is not None:
        tries.append((start, None))
    if start != "free":
        tries.append(("free", None))
    for st, fin in tries:
        res = _decode_once(model, fmt, obs, clamps, st, fin, swapped)
        if res is not None:
            res["final_open"] = fin is None
            res["start"] = st if isinstance(st, str) else "carried"
            return res
    return {"p": _clamp_only(obs, clamps), "mirror": False, "final_open": True, "end": {},
            "loglik": float("-inf"), "start": "none"}


def claims(result, threshold):
    """``[winner | None]``: a side only when its probability reaches ``threshold``."""
    out = []
    for p in result["p"]:
        side = max(SIDES, key=lambda s: p[s])
        out.append(side if p[side] >= threshold else None)
    return out
