"""Leave-one-match-out measurement of the rally models, keep rules and card status (spec 7).

For every labelled match M the models are trained on the other labelled matches and scored
on M with M's labels hidden (the decoder decodes M with no clamps). A match recorded as
several videos (parts) is one fold: all its parts are held out together and no owner label
of any part reaches the decoding. The claim threshold is nested: each fold claims at the
threshold chosen on the other folds' held-out probabilities. ``evaluate`` pools the
held-out scores; ``decide`` applies the keep rules to the pooled numbers; ``status`` feeds the
Rally model card. ``promote`` / ``reject`` / ``rollback`` swap the live files through
``models/store.py`` with the rally spec. ``weights_dir`` is always a parameter. This module
takes no lock: the caller serialises retrain, promote and rollback.
"""
import json
from pathlib import Path

from ..insights.loaders import load_run
from ..library import athletes as libathletes
from ..models import store
from . import build as ob
from . import decoder as dec
from . import labels as lab
from . import run_inputs, scoring
from . import segmenter as sg
from .reconcile import match_parts

SEGMENTER_FILE = "rally-segmenter.json"
DECODER_FILE = "rally-decoder.json"
HISTORY_FILE = "rally-model-history.json"
RALLY = store.ModelSpec("rally", (SEGMENTER_FILE, DECODER_FILE), HISTORY_FILE, "rally",
                        rollback_to_absent=True)

MIN_MATCHES = 2             # labelled matches with MIN_POINTS point labels each
MIN_POINTS = 20
MIN_NEW_POINTS = 20
TARGET_PRECISION = 0.90     # the claim threshold is chosen for this held-out precision
KEEP_PRECISION = 0.85       # the decoder's pooled held-out precision must reach this
MIN_CLAIMS = 3              # a threshold backed by fewer claims is not trusted


def _json(path):
    return store.read_json(path)


# ---------------------------------------------------------------- which matches count

def point_ends(doc):
    """The owner's point-end times (a label naming a winner), sorted."""
    return sorted(l["end_sec"] for l in doc["labels"] if l["verdict"] in lab.SIDES)


def _has_learned_hits(out_dir):
    doc = _json(Path(out_dir) / "hits.json")
    return (isinstance(doc, dict) and doc.get("source") == "learned"
            and isinstance(doc.get("hits"), list) and len(doc["hits"]) >= 2)


def scan(labels_dir, results_dir):
    """``(usable, skipped)``: ``usable`` = ``[(stem, label_doc, n_points)]`` (a point label and learned
    hits), ``skipped`` = ``[{"stem", "reason"}]`` (``labels_unreadable``, ``no_point_labels``,
    ``no_run``, ``no_learned_hits``). Both sorted by stem."""
    usable, skipped = [], []
    d = Path(labels_dir)
    for path in sorted(d.glob("*.json")) if d.is_dir() else []:
        stem = path.stem
        doc = lab.load(path)
        out_dir = Path(results_dir) / stem
        if doc is None:
            reason = "labels_unreadable"
        elif not point_ends(doc):
            reason = "no_point_labels"
        elif not (out_dir / "metadata.json").is_file() or not (out_dir / "detections.jsonl").is_file():
            reason = "no_run"
        elif not _has_learned_hits(out_dir):
            reason = "no_learned_hits"
        else:
            usable.append((stem, doc, len(point_ends(doc))))
            continue
        skipped.append({"stem": stem, "reason": reason})
    return usable, skipped


def labelled_matches(labels_dir, results_dir):
    """The stems the models can learn from or be measured on."""
    return [stem for stem, _doc, _n in scan(labels_dir, results_dir)[0]]


# ---------------------------------------------------------------- one match, loaded once

class _Match:
    def __init__(self, stem, doc, results_dir):
        self.stem, self.doc = stem, doc
        self.out_dir = Path(results_dir) / stem
        self.run = load_run(self.out_dir)
        self.inputs = run_inputs.load(self.out_dir)
        if self.inputs is None:
            raise ValueError("unreadable run")
        self.ends = point_ends(doc)
        self.landing = ob._Landing(self.run, self.out_dir)
        self.hits = ob._learned_hits(self.run)
        self.live = ob._segments(self.run)
        self._auto = {}

    def auto(self, segments):
        """The automatic labeller's view of ``segments``; cached by the segments themselves (each
        fold's segmenter cuts the video differently)."""
        key = tuple((s["id"], s["start_sec"], s["end_sec"]) for s in segments)
        if key not in self._auto:
            self._auto[key] = ob._auto(segments, self.hits, self.landing)
        return self._auto[key]


def _fmt(athletes, stem):
    parts = match_parts(athletes, stem) or [(stem, False)]
    return scoring.resolve(libathletes.resolve_record(athletes, parts[0][0]))


def _group(athletes, stem):
    """The fold a video belongs to: its match's primary stem (itself when unlinked)."""
    parts = match_parts(athletes, stem)
    return parts[0][0] if parts else stem


def _match_stems(athletes, stem):
    return {s for s, _swapped in match_parts(athletes, stem)} | {stem}


def _sequence(match, segments):
    """``[(Obs | None, verdict)]`` of the owner-labelled steps, for ``dec.fit``."""
    items = ob._decoder_items(match.run, segments, match.auto(segments), match.doc, match.inputs)
    return [(None if sid is None else obs, clamp) for _end, sid, obs, clamp in items if clamp is not None]


def _truth(match, segments):
    """``{segment_id: winner}`` of the owner's point labels that attach to ``segments``."""
    by_segment, _added = lab.attach(match.doc["labels"], segments)
    return {sid: l["verdict"] for sid, l in by_segment.items() if l["verdict"] in lab.SIDES}


# ---------------------------------------------------------------- threshold and keep rules

def _top(p):
    return max(dec.SIDES, key=lambda s: p[s])


def choose_threshold(heldout, target=TARGET_PRECISION):
    """The lowest claim threshold whose held-out precision reaches ``target``, else None.

    ``heldout`` is ``[(p, truth)]`` with ``p`` = ``{"upper", "lower", ...}`` and ``truth`` the
    owner's winner. A claim is the more probable side; a threshold needs ``MIN_CLAIMS`` claims."""
    rows = [(p[_top(p)], _top(p) == truth) for p, truth in heldout]
    for th in sorted({c for c, _ in rows}):
        claimed = [ok for c, ok in rows if c >= th]
        if len(claimed) >= MIN_CLAIMS and sum(claimed) / len(claimed) >= target:
            return float(th)
    return None


def fold_thresholds(pools):
    """``{fold: threshold}``: each fold's claim threshold, chosen on the OTHER folds' held-out
    probabilities only (None when they cannot reach the target: that fold then makes no claim).

    ``pools`` = ``{fold: [(p, truth)]}``."""
    return {g: choose_threshold([row for h, rows in pools.items() if h != g for row in rows])
            for g in pools}


def _f1(tp, fp, fn):
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def decide(totals):
    """``{"segmenter": (keep, reason), "decoder": (keep, reason)}``; a reason is a code dict."""
    seg, swing = totals.get("seg_f1"), totals.get("swing_f1")
    if seg is None or not totals.get("segmenter_folds"):
        segmenter = (False, {"code": "no_training_data"})
    elif swing is not None and seg < swing:
        segmenter = (False, {"code": "lower_f1", "candidate": round(seg, 4), "swing": round(swing, 4)})
    else:
        segmenter = (True, {"code": "f1_ok", "candidate": round(seg, 4),
                            "swing": round(swing, 4) if swing is not None else None})
    claimed, correct = totals.get("dec_claimed") or 0, totals.get("dec_correct") or 0
    precision, rules = totals.get("dec_precision"), totals.get("rules_correct") or 0
    if not totals.get("points"):
        decoder = (False, {"code": "no_training_data"})
    elif totals.get("threshold") is None:
        decoder = (False, {"code": "precision_unreachable", "target": TARGET_PRECISION})
    elif not claimed or precision is None:
        decoder = (False, {"code": "no_claims"})
    elif precision < KEEP_PRECISION:
        decoder = (False, {"code": "low_precision", "value": round(precision, 4), "bar": KEEP_PRECISION})
    elif correct < rules:
        decoder = (False, {"code": "fewer_correct", "correct": correct, "rules": rules})
    else:
        decoder = (True, {"code": "decoder_ok", "precision": round(precision, 4),
                          "correct": correct, "rules": rules})
    return {"segmenter": segmenter, "decoder": decoder}


def describe(code):
    """English text of a reason / missing code (CLI output and job messages)."""
    if not isinstance(code, dict):
        return str(code)
    k = code.get("code")
    if k == "decoder_ok":
        return f"precision {code.get('precision')}, {code.get('correct')} correct claims >= rules {code.get('rules')}"
    if k == "f1_ok":
        return f"point-end F1 {code.get('candidate')} >= swing {code.get('swing')}"
    if k == "lower_f1":
        return f"point-end F1 {code.get('candidate')} < swing {code.get('swing')}"
    if k == "no_training_data":
        return "no labelled match to train and measure on"
    if k == "precision_unreachable":
        return f"no claim threshold reaches {code.get('target')} held-out precision"
    if k == "no_claims":
        return "the decoder made no claim on the held-out matches"
    if k == "low_precision":
        return f"held-out precision {code.get('value')} < {code.get('bar')}"
    if k == "fewer_correct":
        return f"{code.get('correct')} correct claims < the rules' {code.get('rules')}"
    if k == "not_enough_matches":
        return "fewer than two labelled matches with learned hits"
    if k == "retrain_failed":
        return f"retrain failed: {code.get('detail') or 'unknown error'}"
    if k == "rolled_back":
        return "rolled back"
    if k == "matches":
        return "label points on another match"
    if k == "new_points":
        return f"{max(0, code.get('need', 0) - code.get('have', 0))} more point labels (need {code.get('need')} new)"
    return str(code)


# ---------------------------------------------------------------- the measurement

def _segment_fold(match, seg_model):
    """Point-end scores of the learned segments and of the swing ones on ``match``."""
    learned = sg.point_end_f1([s["end_sec"] for s in sg.segments(match.inputs, seg_model)], match.ends)
    swing = sg.point_end_f1([e for _s, e in (match.inputs.swing_segments or ())], match.ends)
    return learned, swing


def _pool(rows):
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    return _f1(tp, fp, fn)


def evaluate(labels_dir, results_dir, athletes_path, candidate=None):
    """Leave-one-match-out scores of the learned models.

    ``candidate`` None: for each labelled match, train on the others and score on it (its
    labels stay out of training and decoding). ``candidate`` ``{"segmenter": model|None,
    "decoder": model|None}``: score these fixed models on every labelled match, training nothing.

    Returns ``{"fixed", "per_match": {stem: {...}}, "skipped": [...], "totals": {seg_f1,
    swing_f1, segmenter_folds, points, threshold, dec_claimed, dec_correct, dec_precision,
    rules_claimed, rules_correct, rules_precision}}``. The decoder is scored on the segments the
    segmenter would produce when it wins its own keep rule, else on the live segments, with the
    rule cascade scored on the same ones. The claim threshold is chosen once from the held-out
    probabilities pooled over every fold.
    """
    athletes = libathletes.load(athletes_path)
    usable, skipped = scan(labels_dir, results_dir)
    matches = []
    for stem, doc, _n in usable:
        try:
            matches.append(_Match(stem, doc, results_dir))
        except Exception:
            skipped.append({"stem": stem, "reason": "run_unreadable"})
    fixed = candidate is not None
    per_match = {m.stem: {"points": len(m.ends), "segmenter": None, "swing": None, "decoder": None,
                          "rules": None} for m in matches}

    groups = {m.stem: _group(athletes, m.stem) for m in matches}
    order = list(dict.fromkeys(groups.values()))

    # the segmenter, one model per fold (a fold is a whole match, however many videos)
    seg_models = {}
    for g in order:
        if fixed:
            seg_models[g] = candidate.get("segmenter")
            continue
        try:
            seg_models[g] = sg.fit([(o.inputs, o.doc, o.ends) for o in matches if groups[o.stem] != g])
        except ValueError:
            seg_models[g] = None
    seg_rows, swing_rows = [], []
    for m in matches:
        if seg_models[groups[m.stem]] is None:
            continue
        learned, swing = _segment_fold(m, seg_models[groups[m.stem]])
        per_match[m.stem]["segmenter"], per_match[m.stem]["swing"] = learned, swing
        seg_rows.append(learned)
        swing_rows.append(swing)
    totals = {"seg_f1": _pool(seg_rows) if seg_rows else None,
              "swing_f1": _pool(swing_rows) if swing_rows else None,
              "segmenter_folds": len(seg_rows), "points": sum(len(m.ends) for m in matches)}
    use_learned = decide(totals)["segmenter"][0]

    def segments_of(match, model):
        if use_learned and model is not None:
            return sg.segments(match.inputs, model)
        return match.live

    # the decoder: per fold held-out probabilities, then thresholds
    rows = []       # per match: (match, model, truth, probs, auto, fold)
    pools = {}      # fold -> [(p, truth)]
    for g in order:
        members = [m for m in matches if groups[m.stem] == g]
        if fixed:
            model = candidate.get("decoder")
        else:
            train = [o for o in matches if groups[o.stem] != g]
            seqs = [(_sequence(o, segments_of(o, seg_models[g])), _fmt(athletes, o.stem)) for o in train]
            seqs = [(q, f) for q, f in seqs if q]
            model = dec.fit([q for q, _f in seqs], fmts=[f for _q, f in seqs]) if train else None
        if model is None:
            continue
        for m in members:
            segments = segments_of(m, seg_models[g])
            auto = m.auto(segments)
            probs, _open = ob._decode_match(m.out_dir, m.run, segments, auto, lab.empty(), athletes,
                                            model, labels_dir, inputs=m.inputs,
                                            unclamped=_match_stems(athletes, m.stem))
            truth = _truth(m, segments)
            rows.append((m, model, truth, probs, auto, g))
            pools.setdefault(g, []).extend((probs[sid], side) for sid, side in truth.items() if sid in probs)
    if fixed:
        thresholds = [r[1].get("claim_threshold") for r in rows if r[1].get("claim_threshold") is not None]
        threshold = thresholds[0] if thresholds else None
        fold_th = {g: threshold if threshold is not None else dec.DEFAULT_CLAIM_THRESHOLD for g in pools}
    else:
        # shipped: chosen on every fold's held-out probabilities; measured: each fold at the
        # threshold the other folds would have given it, so the bar is not met by construction
        threshold = choose_threshold([row for rs in pools.values() for row in rs])
        fold_th = fold_thresholds(pools)
    d_claimed = d_correct = r_claimed = r_correct = 0
    for m, _model, truth, probs, auto, g in rows:
        dc = dk = rc = rk = 0
        for sid, side in truth.items():
            p = probs.get(sid)
            claim = dec.claims({"p": [p]}, fold_th[g])[0] if p is not None and fold_th.get(g) is not None else None
            if claim is not None:
                dc += 1
                dk += claim == side
            rule = auto[sid]["decision"]["winner_side"]
            if rule is not None:
                rc += 1
                rk += rule == side
        per_match[m.stem]["decoder"] = {"claimed": dc, "correct": dk,
                                        "precision": dk / dc if dc else None}
        per_match[m.stem]["rules"] = {"claimed": rc, "correct": rk, "precision": rk / rc if rc else None}
        d_claimed, d_correct, r_claimed, r_correct = d_claimed + dc, d_correct + dk, r_claimed + rc, r_correct + rk
    totals.update({
        "threshold": threshold, "dec_claimed": d_claimed, "dec_correct": d_correct,
        "dec_precision": d_correct / d_claimed if d_claimed else None,
        "rules_claimed": r_claimed, "rules_correct": r_correct,
        "rules_precision": r_correct / r_claimed if r_claimed else None})
    return {"fixed": fixed, "per_match": per_match, "skipped": skipped, "totals": totals}


# ---------------------------------------------------------------- live models, history, swaps

def live_views(weights_dir):
    """``{"segmenter": view | None, "decoder": view | None}`` of the live model files.

    A view is ``{"version", "heldout", ...}`` read from the file itself (a retrain stores its
    held-out numbers in the model it writes, so a roll back shows the right ones)."""
    weights_dir = Path(weights_dir)
    seg = sg.load(weights_dir / SEGMENTER_FILE)
    dm = dec.load(weights_dir / DECODER_FILE)
    return {
        "segmenter": None if seg is None else {
            "version": seg.get("version"), "mode": seg.get("mode"), "train_f1": seg.get("train_f1"),
            "heldout": seg.get("heldout")},
        "decoder": None if dm is None else {
            "version": dm.get("version"), "claim_threshold": dm.get("claim_threshold"),
            "heldout": dm.get("heldout")},
    }


def history(weights_dir):
    return store.history(RALLY, weights_dir)


def can_rollback(weights_dir):
    return store.can_rollback(RALLY, weights_dir)


def recover(weights_dir):
    return store.recover(RALLY, weights_dir)


def promote(weights_dir, candidate_files, record):
    """Make the given candidate files live (``{file name: path}``); the files they replace go to history."""
    return store.promote(RALLY, weights_dir, candidate_files, record,
                         lambda entry: {"live_after": live_views(weights_dir)})


def reject(weights_dir, candidate_files, record):
    """Delete candidates that lost and record the numbers."""
    return store.reject(RALLY, weights_dir, candidate_files, record,
                        lambda entry: {"live_after": live_views(weights_dir)})


def rollback(weights_dir, points=None):
    """Swap in the newest replaced model files that no rollback retired; False when there is none."""
    weights_dir = Path(weights_dir)

    def live_after(newest):
        views = live_views(weights_dir)
        seg, dm = sg.load(newest / SEGMENTER_FILE), dec.load(newest / DECODER_FILE)
        if seg is not None:
            views["segmenter"] = {"version": seg.get("version"), "mode": seg.get("mode"),
                                  "train_f1": seg.get("train_f1"), "heldout": seg.get("heldout")}
        if dm is not None:
            views["decoder"] = {"version": dm.get("version"), "claim_threshold": dm.get("claim_threshold"),
                                "heldout": dm.get("heldout")}
        # a file the first promote installed over nothing rolls back to no model at all
        for name in store._absent_names(newest):
            views[{SEGMENTER_FILE: "segmenter", DECODER_FILE: "decoder"}[name]] = None
        return views

    return store.rollback(RALLY, weights_dir, live_after=live_after,
                          points=int(points) if points is not None else None)


def status(labels_dir, results_dir, weights_dir, athletes_path=None):
    """The Rally model card's data: live models, label counts, what is missing, history.

    ``counts.matches`` counts labelled matches the way ``evaluate`` folds them (the parts of one
    match are one match; a match qualifies with ``MIN_POINTS`` point labels summed over its
    parts); ``counts.videos`` counts the labelled videos. ``athletes_path`` defaults to the
    project's ``data/athletes.json``."""
    recover(weights_dir)
    usable, skipped = scan(labels_dir, results_dir)
    athletes = libathletes.load(athletes_path if athletes_path is not None else ob.DEFAULT_ATHLETES)
    points = sum(n for _s, _d, n in usable)
    per_match = {}
    for stem, _d, n in usable:
        g = _group(athletes, stem)
        per_match[g] = per_match.get(g, 0) + n
    qualifying = sum(1 for n in per_match.values() if n >= MIN_POINTS)
    hist = history(weights_dir)
    seen = [e["points"] for e in hist if type(e.get("points")) is int and not e.get("rollback")]
    new_points = max(0, points - (seen[-1] if seen else 0))
    missing = []
    if qualifying < MIN_MATCHES:
        missing.append({"code": "matches", "need": MIN_MATCHES, "have": qualifying})
    if new_points < MIN_NEW_POINTS:
        missing.append({"code": "new_points", "need": MIN_NEW_POINTS, "have": new_points})
    return {"live": live_views(weights_dir),
            "counts": {"matches": qualifying, "videos": len(usable), "points": points,
                       "new_points": new_points},
            "can_retrain": not missing, "missing": missing, "skipped": skipped,
            "history": hist, "can_rollback": can_rollback(weights_dir)}


# ---------------------------------------------------------------- the final candidates

def _stamp():
    import time
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def fit_candidates(labels_dir, results_dir, athletes_path, totals, keep):
    """The models to promote, trained on every labelled match: ``{"segmenter": model|None,
    "decoder": model|None}`` (None where ``keep`` says not to keep, or training failed).

    ``totals`` are the pooled held-out numbers of ``evaluate``; they are stored in each model's
    ``heldout`` block, and the decoder takes the claim threshold chosen on them. The decoder
    is trained on the learned segments when the segmenter is kept too, else on the live ones.
    """
    athletes = libathletes.load(athletes_path)
    matches = []
    for stem, doc, _n in scan(labels_dir, results_dir)[0]:
        try:
            matches.append(_Match(stem, doc, results_dir))
        except Exception:
            continue
    out = {"segmenter": None, "decoder": None}
    if not matches:
        return out
    n_points = sum(len(m.ends) for m in matches)
    seg = None
    if keep.get("segmenter"):
        try:
            seg = sg.fit([(m.inputs, m.doc, m.ends) for m in matches])
            seg["heldout"] = {"f1": totals.get("seg_f1"), "swing_f1": totals.get("swing_f1"),
                              "matches": len(matches), "points": n_points}
            out["segmenter"] = seg
        except ValueError:
            seg = None
    if keep.get("decoder"):
        seqs = []
        for m in matches:
            segments = sg.segments(m.inputs, seg) if seg is not None else m.live
            seqs.append((_sequence(m, segments), _fmt(athletes, m.stem)))
        seqs = [(q, f) for q, f in seqs if q]
        model = dec.fit([q for q, _f in seqs], fmts=[f for _q, f in seqs])
        threshold = totals.get("threshold")
        model.update({"version": _stamp(),
                      "claim_threshold": threshold if threshold is not None else dec.DEFAULT_CLAIM_THRESHOLD,
                      "heldout": {"precision": totals.get("dec_precision"), "correct": totals.get("dec_correct"),
                                  "claimed": totals.get("dec_claimed"), "rules_correct": totals.get("rules_correct"),
                                  "rules_precision": totals.get("rules_precision"), "threshold": threshold,
                                  "matches": len(matches), "points": n_points}})
        out["decoder"] = model
    return out
