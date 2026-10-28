"""Point-end segmenter: run inputs, gap features, labels, training, segments, F1."""
import json

import pytest

from badminton_analysis.outcomes import run_inputs, segment_features as sf, segmenter as sg

FPS = 60.0
QUAD = [[1000, 500], [2800, 500], [3300, 1900], [500, 1900]]


def inputs(hits, positions=None, ok=None, swing=None, shuttle=(), end_sec=100.0):
    return run_inputs.Inputs(
        fps=FPS, hits=[(t, int(t * FPS), side, None) for t, side in hits],
        positions=positions or {"upper": {}, "lower": {}}, swing=swing or [],
        shuttle_frames=set(shuttle), positions_ok=ok or {"upper": False, "lower": False},
        quad=QUAD, end_sec=end_sec)


def labels(*items):
    return {"schema": 1, "revision": 1, "labels": [
        {"start_sec": s, "end_sec": e, "verdict": v, "outcome": None, "added": a}
        for s, e, v, a in items]}


def test_gap_features_basic():
    inp = inputs([(10.0, "lower"), (11.0, "upper"), (14.0, "upper")],
                 swing=[(10.5, 2.0), (10.8, 6.0), (12.0, 1.0)],
                 shuttle=range(600, 660))
    g = sf.gaps(inp)
    assert len(g) == 2 and set(g[0].features) == set(sf.FEATURES)
    assert g[0].features["gap_sec"] == pytest.approx(1.0)
    assert g[0].features["same_side"] == 0.0 and g[1].features["same_side"] == 1.0
    assert g[0].features["swing_max"] == 6.0 and g[0].features["swing_mean"] == pytest.approx(4.0)
    assert g[0].features["shuttle_share"] > 0.9 and g[1].features["shuttle_share"] == 0.0


def test_walked_distance_zero_when_positions_not_ok():
    pos = {"upper": {}, "lower": {f: (3.0 + (f - 600) * 0.01, 10.0) for f in range(600, 700)}}
    inp = inputs([(10.0, "lower"), (11.6, "lower")], positions=pos)
    assert sf.gaps(inp)[0].features["walk_lower"] == 0.0
    inp = inputs([(10.0, "lower"), (11.6, "lower")], positions=pos, ok={"upper": False, "lower": True})
    assert sf.gaps(inp)[0].features["walk_lower"] > 0.5


def test_service_court_flag_uses_the_hitters_own_half():
    pos = {"upper": {}, "lower": {int(12 * FPS): (1.5, 9.0)}}
    ok = {"upper": False, "lower": True}
    f = sf.gaps(inputs([(10.0, "upper"), (12.0, "lower")], positions=pos, ok=ok))[0].features
    assert (f["svc_left"], f["svc_right"]) == (1.0, 0.0)
    pos = {"upper": {int(12 * FPS): (4.5, 3.0)}, "lower": {}}
    f = sf.gaps(inputs([(10.0, "lower"), (12.0, "upper")], positions=pos, ok={"upper": True, "lower": False}))[0].features
    assert (f["svc_left"], f["svc_right"]) == (0.0, 1.0)
    # at the net (inside the front service line) or outside the side lines: not a service court
    pos = {"upper": {}, "lower": {int(12 * FPS): (1.5, 7.5)}}
    f = sf.gaps(inputs([(10.0, "upper"), (12.0, "lower")], positions=pos, ok=ok))[0].features
    assert (f["svc_left"], f["svc_right"]) == (0.0, 0.0)
    pos = {"upper": {}, "lower": {int(12 * FPS): (0.2, 9.0)}}
    f = sf.gaps(inputs([(10.0, "upper"), (12.0, "lower")], positions=pos, ok=ok))[0].features
    assert (f["svc_left"], f["svc_right"]) == (0.0, 0.0)


def test_no_learned_hits_no_gaps():
    assert sf.gaps(inputs([])) == []
    assert sf.gaps(inputs([(5.0, "lower")])) == []
    assert sg.segments(inputs([]), {"features": list(sf.FEATURES), "mean": [0] * 10, "scale": [1] * 10,
                                    "coef": [0] * 10, "intercept": 0, "threshold": 0.5}) == []


def test_labels_from_segments_added_points_and_not_point():
    hits = [(10.0, "lower"), (11.0, "upper"), (12.0, "lower"),      # rally, ends ~12.5
            (30.0, "lower"), (31.0, "upper"), (40.0, "lower"),      # not_point 30-32, then a long gap
            (60.0, "lower"), (61.0, "upper")]                       # added point at 65 (no hits there)
    g = sf.gaps(inputs(hits))
    doc = labels((9.0, 12.5, "lower", False), (29.0, 32.0, "not_point", False),
                 (None, 100.0, "upper", True))
    got = sf.labels_for(g, doc, [])
    # gaps: 10-11, 11-12, 12-30, 30-31, 31-40, 40-60, 60-61
    # one positive per point end: the end 12.5 lies in gap 12-30; the added end at 100 fits no gap
    assert got == [0, 0, 1, 0, 0, 0, 0]
    doc = labels((None, 61.5, "upper", True))   # outside every gap: nearest midpoint within 1 s
    assert sf.labels_for(g, doc, []) == [0, 0, 0, 0, 0, 0, 1]
    # two ends in one gap still make one positive; two gaps never share an end
    doc = labels((9.0, 12.2, "lower", False), (None, 12.4, "upper", True))
    assert sum(1 for v in sf.labels_for(g, doc, []) if v == 1) == 1
    doc = labels((9.0, 11.0, "lower", False))   # on the boundary of two gaps: exactly one
    assert sum(1 for v in sf.labels_for(g, doc, []) if v == 1) == 1


def test_labels_unsure_and_missing_document():
    g = sf.gaps(inputs([(10.0, "lower"), (11.0, "upper"), (30.0, "lower")]))
    assert sf.labels_for(g, None, []) == [None, None]
    assert sf.labels_for(g, {"schema": 1, "revision": 0, "labels": []}, []) == [None, None]
    assert sf.labels_for(g, labels((8.0, 9.0, "unsure", False)), []) == [None, 0]


def synthetic_examples():
    ex = []
    for i in range(60):
        long = i % 3 == 0
        ex.append(({"gap_sec": 6.0 + (i % 5) if long else 0.6 + (i % 4) * 0.2,
                    "log_gap_sec": 2.0 if long else -0.3, "swing_mean": 0.1 if long else 3.0,
                    "swing_max": 0.5 if long else 8.0, "walk_upper": 0.0, "walk_lower": 0.0,
                    "svc_left": 1.0 if long else 0.0, "svc_right": 0.0, "same_side": 1.0,
                    "shuttle_share": 0.2 if long else 0.9}, 1 if long else 0))
    return ex


def test_training_separates_an_easy_set_and_is_deterministic():
    ex = synthetic_examples()
    m = sg.train(ex)
    assert m["schema"] == 1 and m["threshold"] == 0.5 and m["features"] == list(sf.FEATURES)
    json.dumps(m)
    assert len(m["coef"]) == len(m["mean"]) == len(m["scale"]) == len(sf.FEATURES) and m["version"]
    gaps = [sf.Gap(0, 1, f) for f, _ in ex]
    p = sg.predict(m, gaps)
    assert all((pi >= 0.5) == bool(y) for pi, (_, y) in zip(p, ex))
    m2 = sg.train(ex)
    assert m2["coef"] == m["coef"] and m2["intercept"] == m["intercept"]
    with pytest.raises(ValueError):
        sg.train([(f, 0) for f, _ in ex])


def easy_model():
    """Point end when the gap is longer than ~3 s (a hand-set model on gap length only)."""
    return {"features": ["gap_sec"], "mean": [0.0], "scale": [1.0], "coef": [3.0], "intercept": -9.0,
            "threshold": 0.5}


def long_gap_inputs(hits, **kw):
    # swing activity high near every hit so lone hits are kept; features mimic the training set
    swing = [(t / 10.0, 8.0) for t in range(0, 1000)]
    return inputs(hits, swing=swing, shuttle=kw.pop("shuttle", ()), **kw)


def test_segments_pads_clip_and_split():
    model = easy_model()
    # rally 1: hits 10, 10.8, 11.6; 8 s dead gap; rally 2: 19.6, 20.4; end at video edge
    inp = long_gap_inputs([(10.0, "lower"), (10.8, "upper"), (11.6, "lower"),
                           (19.6, "upper"), (20.4, "lower")], end_sec=20.9,
                          shuttle=range(int(10 * FPS), int(11.6 * FPS)))
    segs = sg.segments(inp, model)
    assert [s["id"] for s in segs] == [1, 2]
    assert segs[0]["start_sec"] == pytest.approx(8.0) and segs[0]["end_sec"] == pytest.approx(12.6)
    assert segs[1]["start_sec"] == pytest.approx(17.6) and segs[1]["end_sec"] == pytest.approx(20.9)  # clipped
    assert all(s["degraded"] is False and 0 <= s["p_point"] <= 1 for s in segs)
    assert segs[0]["start_frame"] == round(8.0 * FPS) and segs[0]["end_frame"] == round(12.6 * FPS)
    # start clipped to the video start
    inp = long_gap_inputs([(1.0, "lower"), (1.8, "upper")])
    assert sg.segments(inp, model)[0]["start_sec"] == 0.0


def test_overlapping_pads_are_split_between_rallies():
    model = {"features": ["gap_sec"], "mean": [0.0], "scale": [1.0], "coef": [5.0], "intercept": -10.0,
             "threshold": 0.5}
    inp = long_gap_inputs([(10.0, "lower"), (10.5, "upper"), (12.5, "lower"), (13.0, "upper")])
    segs = sg.segments(inp, model)   # gap 10.5->12.5 is 2 s: point end; pads would overlap
    assert len(segs) == 2 and segs[0]["end_sec"] == pytest.approx(segs[1]["start_sec"])
    assert 10.5 < segs[0]["end_sec"] < 12.5


def test_single_hit_without_swing_is_dropped():
    model = easy_model()
    hits = [(10.0, "lower"), (10.8, "upper"), (11.6, "lower"), (30.0, "upper"), (50.0, "lower"), (50.8, "upper")]
    swing = [(t / 10.0, 8.0) for t in range(0, 600)]   # no swing activity after 60 s... and near 30 s there is some
    swing = [(t, a) for t, a in swing if not 29.0 < t < 31.0]
    inp = inputs(hits, swing=swing)
    starts = [round(s["start_sec"]) for s in sg.segments(inp, model)]
    assert 28 not in starts and 8 in starts
    # no swing signal at all: lone hits go
    assert all(s["end_sec"] - s["start_sec"] > 3 for s in sg.segments(inputs(hits), model))


def test_load_model_and_junk(tmp_path):
    m = sg.train(synthetic_examples())
    path = tmp_path / "m.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    assert sg.load(path) == m
    assert sg.load(tmp_path / "missing.json") is None
    path.write_text("not json", encoding="utf-8")
    assert sg.load(path) is None
    path.write_text(json.dumps({**m, "schema": 2}), encoding="utf-8")
    assert sg.load(path) is None
    path.write_text(json.dumps({**m, "coef": [1.0]}), encoding="utf-8")
    assert sg.load(path) is None


def test_point_end_f1_is_one_to_one():
    r = sg.point_end_f1([10.0, 10.5, 30.0], [10.2, 50.0])
    assert (r["tp"], r["fp"], r["fn"]) == (1, 2, 1)
    assert r["precision"] == pytest.approx(1 / 3) and r["recall"] == pytest.approx(0.5)
    assert r["f1"] == pytest.approx(0.4)
    assert sg.point_end_f1([], [1.0])["f1"] == 0.0
    assert sg.point_end_f1([5.0], [6.4])["tp"] == 1 and sg.point_end_f1([5.0], [6.6])["tp"] == 0


def write_run(d, hits_source="learned", hits=True, junk=False):
    d.mkdir(parents=True, exist_ok=True)
    if junk:
        (d / "metadata.json").write_text("{", encoding="utf-8")
        return d
    (d / "metadata.json").write_text(json.dumps(
        {"video": {"fps": FPS}, "court": {"corners": QUAD}}), encoding="utf-8")
    lines = []
    for f in range(1, 601):
        wob = (f % 7) * 3.0
        lines.append(json.dumps({"frame": f, "time_sec": f / FPS, "players": {
            "upper": {"court": [3.0, 3.0], "hands": {"left": None, "right": [2000 + wob, 800]}},
            "lower": {"court": [3.0, 10.0], "hands": {"left": [1500, 1500 + wob], "right": None}}},
            "shuttlecock": {"image": [1.0, 2.0] if f % 2 else None}}))
    lines.insert(3, "garbage line")
    (d / "detections.jsonl").write_text("\n".join(lines), encoding="utf-8")
    if hits:
        (d / "hits.json").write_text(json.dumps({"source": hits_source, "hits": [
            {"frame": 120, "time_sec": 2.0, "hitter": "lower"},
            {"frame": 180, "time_sec": 3.0, "hitter": "upper", "hitter_id": "p2"},
            {"frame": 240, "time_sec": 4.0, "hitter": "bogus"}]}), encoding="utf-8")
    return d


def test_run_inputs_load(tmp_path):
    inp = run_inputs.load(write_run(tmp_path / "run"))
    assert inp.fps == FPS
    assert [(h[2], h[3]) for h in inp.hits] == [("lower", None), ("upper", "p2")]
    assert 120 in inp.positions["lower"] and len(inp.swing) == 600
    assert inp.shuttle_frames and inp.quad is not None and set(inp.positions_ok) == {"upper", "lower"}
    assert inp.end_sec == pytest.approx(600 / FPS)
    assert len(sf.gaps(inp)) == 1


def test_run_inputs_junk_and_missing_hits(tmp_path):
    assert run_inputs.load(tmp_path / "nothing") is None
    assert run_inputs.load(write_run(tmp_path / "junk", junk=True)) is None
    # no hits.json, or rule-based hits: loads, but there are no gaps (fallback to swing)
    for name, kw in (("nohits", {"hits": False}), ("rule", {"hits_source": "rule"})):
        inp = run_inputs.load(write_run(tmp_path / name, **kw))
        assert inp is not None and inp.hits == [] and sf.gaps(inp) == []


def test_swing_features():
    inp = inputs([(10.0, "lower"), (11.0, "upper"), (14.0, "upper"), (30.0, "lower")])
    inp = inp._replace(swing_segments=((9.0, 10.5), (20.0, 25.0)))
    f = [g.features for g in sf.gaps(inp)]
    assert f[0]["swing_end_in_gap"] == 1.0 and f[0]["swing_end_dist"] == 0.0 and f[0]["swing_outside"] == 0.0
    assert f[1]["swing_end_in_gap"] == 0.0 and f[1]["swing_end_dist"] == pytest.approx(0.5)
    assert f[1]["swing_outside"] == 1.0 and f[2]["swing_outside"] == 0.0   # 14-30 spans 20-25
    assert f[2]["swing_end_in_gap"] == 1.0
    f = [g.features for g in sf.gaps(inputs([(10.0, "lower"), (11.0, "upper")]))]
    assert (f[0]["swing_end_dist"], f[0]["swing_outside"], f[0]["swing_end_in_gap"]) == (10.0, 1.0, 0.0)
    far = inp._replace(swing_segments=((100.0, 120.0),))
    assert sf.gaps(far)[0].features["swing_end_dist"] == 10.0   # capped


def test_nms_keeps_the_local_maximum_only():
    g = [sf.Gap(t, t + 0.5, {}) for t in (10.0, 11.0, 12.0, 20.0, 21.0)]
    p = [0.6, 0.9, 0.7, 0.8, 0.8]
    assert sg.point_ends(g, p, 0.5) == [1, 3]   # 0.6 and 0.7 lose to 0.9; the tie goes to the earlier
    assert sg.point_ends(g, p, 0.85) == [1]
    assert sg.point_ends(g, p, 0.95) == []


def test_segments_use_nms_and_tuned_threshold_is_stored():
    model = {"features": ["gap_sec"], "mean": [0.0], "scale": [1.0], "coef": [3.0], "intercept": -3.0,
             "threshold": 0.5}
    # two long gaps 2 s apart: only the longer one ends a rally
    hits = [(10.0, "lower"), (10.8, "upper"), (13.0, "lower"), (15.5, "upper"), (16.3, "lower")]
    segs = sg.segments(long_gap_inputs(hits), model)
    assert len(segs) == 2 and segs[0]["end_sec"] == pytest.approx(13.0 + 2.5 / 3)   # split in the 2.5 s gap
    truth = [13.8, 17.3]
    tuned = sg.tune_threshold(model, [(long_gap_inputs(hits), truth)])
    assert tuned["threshold"] in sg.TUNE_GRID and tuned["train_f1"] == 1.0 and tuned["coef"] == model["coef"]
    # a threshold above every p scores 0, so a truth the model cannot hit still returns a model
    assert sg.tune_threshold(model, [(long_gap_inputs(hits), [50.0])])["train_f1"] == 0.0
    json.dumps(tuned)


# --- swing-anchored veto mode -----------------------------------------------------------

def veto_inputs(swing_segments, hits=(), end_sec=200.0):
    swing = [(t / 10.0, 5.0) for t in range(0, int(end_sec * 10))]
    return inputs(list(hits), swing=swing, shuttle=range(0, 3000), end_sec=end_sec)._replace(
        swing_segments=tuple(swing_segments))


def test_segment_rows_features():
    hits = [(10.0, "lower"), (11.0, "upper"), (12.5, "lower"), (13.0, "lower")]
    inp = veto_inputs([(8.0, 15.0), (30.0, 40.0)], hits)
    a, b = sf.segment_rows(inp)
    assert set(a.features) == set(sf.VETO_FEATURES)
    assert a.features["seg_dur"] == pytest.approx(7.0) and a.features["hits_n"] == 4.0
    assert a.features["alt_share"] == pytest.approx(2 / 3) and a.features["max_hit_gap"] == pytest.approx(1.5)
    assert a.features["gap_prev"] == 30.0 and a.features["gap_next"] == pytest.approx(15.0)
    assert b.features["hits_n"] == 0.0 and b.features["gap_prev"] == pytest.approx(15.0)
    assert a.features["swing_mean"] == pytest.approx(5.0) and a.features["shuttle_share"] > 0.9
    assert sf.segment_rows(veto_inputs([])) == []


def test_veto_labels():
    rows = sf.segment_rows(veto_inputs([(8.0, 15.0), (30.0, 40.0), (50.0, 60.0), (70.0, 80.0)]))
    doc = labels((7.0, 14.8, "lower", False), (30.0, 40.0, "not_point", False),
                 (50.0, 59.5, "unsure", False))
    assert sf.veto_labels_for(rows, doc) == [1, 0, None, None]   # unsure and unlabelled give None
    assert sf.veto_labels_for(rows, None) == [None] * 4
    assert sf.veto_labels_for(rows, {"schema": 1, "revision": 0, "labels": []}) == [None] * 4


def veto_model(threshold=0.5):
    # keeps segments with at least 2 hits
    return {"mode": "swing_veto", "features": ["hits_n"], "mean": [0.0], "scale": [1.0], "coef": [4.0],
            "intercept": -6.0, "threshold": threshold}


def test_segments_dispatch_on_mode():
    hits = [(10.0, "lower"), (11.0, "upper"), (12.0, "lower"), (31.0, "lower")]
    inp = veto_inputs([(8.0, 14.0), (30.0, 33.0)], hits)
    segs = sg.segments(inp, veto_model())
    assert len(segs) == 1 and segs[0]["id"] == 1 and segs[0]["degraded"] is False
    assert (segs[0]["start_sec"], segs[0]["end_sec"]) == (8.0, 14.0)
    assert segs[0]["start_frame"] == round(8.0 * FPS) and segs[0]["end_frame"] == round(14.0 * FPS)
    assert segs[0]["p_point"] > 0.5
    assert [s["id"] for s in sg.segments(inp, veto_model(threshold=0.0))] == [1, 2]
    gap_model = dict(easy_model(), mode="gap")
    assert sg.segments(inp, gap_model) == sg.segments(inp, easy_model())   # a missing mode is gap
    assert sg.segments(veto_inputs([]), veto_model()) == []   # no swing segments: nothing to keep


def test_train_stores_mode_and_load_defaults_it(tmp_path):
    m = sg.train(synthetic_examples())
    assert m["mode"] == "gap"
    v = sg.train([({n: float(i % 2) for n in sf.VETO_FEATURES}, i % 2) for i in range(10)], mode="swing_veto")
    assert v["mode"] == "swing_veto" and v["features"] == list(sf.VETO_FEATURES)
    with pytest.raises(ValueError):
        sg.train(synthetic_examples(), mode="nope")
    path = tmp_path / "m.json"
    old = dict(m)
    del old["mode"]
    path.write_text(json.dumps(old), encoding="utf-8")
    assert sg.load(path)["mode"] == "gap"
    path.write_text(json.dumps({**m, "mode": "bogus"}), encoding="utf-8")
    assert sg.load(path) is None
    path.write_text(json.dumps(v), encoding="utf-8")
    assert sg.load(path)["mode"] == "swing_veto"


def veto_match():
    """Swing segments at 10, 30, 50, 70 s: the 2 with several hits are points, the 2 with one hit are not."""
    spans = [(8.0, 14.0), (28.0, 34.0), (48.0, 54.0), (68.0, 74.0)]
    hits = []
    for k, (a, _b) in enumerate(spans):
        n = 4 if k % 2 == 0 else 1
        hits += [(a + 2 + 1.0 * j, "lower" if j % 2 else "upper") for j in range(n)]
    inp = veto_inputs(spans, hits, end_sec=80.0)
    doc = labels((8.0, 14.0, "lower", False), (28.0, 34.0, "not_point", False),
                 (48.0, 54.0, "upper", False), (68.0, 74.0, "not_point", False))
    return inp, doc, [14.0, 54.0]


def test_fit_picks_the_mode_with_the_higher_training_f1():
    inp, doc, ends = veto_match()
    model = sg.fit([(inp, doc, ends), (inp, doc, ends)])
    assert model["mode"] in sg.MODES and set(model["mode_scores"]) <= set(sg.MODES)
    assert model["train_f1"] == max(model["mode_scores"].values())
    assert model["mode"] == "swing_veto" and model["train_f1"] == 1.0   # vetoing the one-hit segments is exact
    assert [(s["start_sec"], s["end_sec"]) for s in sg.segments(inp, model)] == [(8.0, 14.0), (48.0, 54.0)]
    json.dumps(model)
    # labels with no usable class: no model
    with pytest.raises(ValueError):
        sg.fit([(inp, {"schema": 1, "revision": 0, "labels": []}, [])])
