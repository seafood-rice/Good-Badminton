"""The learned point-end segmenter in the pipeline, and every consumer of its output.

With no usable model (or no learned hits, or any failure) the swing path's files stay byte-identical;
with one, ``rally_segments.json`` says ``signal: learned`` and the consumers treat it as a validated,
non-degraded signal.
"""
import json
import os

import pytest

import app as webapp
from badminton_analysis import system as sysmod
from badminton_analysis.insights.loaders import load_run
from badminton_analysis.insights.rally import rally_insight, summarise_rally_payload
from badminton_analysis.outcomes import build as outcomes_build
from badminton_analysis.outcomes import segment_features as sf
from badminton_analysis.outcomes import segmenter
from badminton_analysis.stroke import owner_labels, relabel
from badminton_analysis.strategy.facts import build_facts
from badminton_analysis.strategy.shots import build_shots
from tests.insights_fixtures import make_run

FPS = 60.0
QUAD = [[1000, 500], [2800, 500], [3300, 1900], [500, 1900]]
HITS = [(5.0, "lower"), (6.0, "upper"), (7.0, "lower"), (8.0, "upper"),
        (20.0, "lower"), (21.0, "upper"), (22.0, "lower")]
SWING = {"signal": "swing", "attempted_signal": "auto", "degraded": False, "reason": "swing",
         "court_view": {"cut": 0.8}, "stroke_labels_withheld": False,
         "suppressed_static_shuttle": 2}
SWING_RALLIES = [{"id": 1, "start_frame": 240, "end_frame": 600, "start_sec": 4.0, "end_sec": 10.0,
                  "degraded": False}]


def model(**kw):
    """A point end when the gap between hits is longer than ~3 s."""
    base = {"schema": 1, "mode": "gap", "features": ["gap_sec"], "mean": [0.0], "scale": [1.0],
            "coef": [3.0], "intercept": -9.0, "threshold": 0.5, "version": "v-test", "train_f1": 0.83}
    base.update(kw)
    return base


def write_run(d, hits=HITS, source="learned"):
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(
        {"video": {"fps": FPS}, "court": {"corners": QUAD}}), encoding="utf-8")
    lines = []
    for f in range(1, int(30 * FPS) + 1):
        wob = (f % 7) * 3.0
        lines.append(json.dumps({"frame": f, "time_sec": f / FPS, "players": {
            "upper": {"court": [3.0, 3.0], "hands": {"left": None, "right": [2000 + wob, 800]}},
            "lower": {"court": [3.0, 10.0], "hands": {"left": [1500, 1500 + wob], "right": None}}},
            "shuttlecock": {"image": [1.0, 2.0] if f % 2 else None}}))
    (d / "detections.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "hits.json").write_text(json.dumps({"source": source, "model": {"sha256": "x"}, "hits": [
        {"frame": int(t * FPS), "time_sec": t, "hitter": s, "confidence": 0.9, "rally_id": 1}
        for t, s in hits]}), encoding="utf-8")
    (d / "rally_segments.json").write_text(json.dumps(
        {"fps": FPS, "rallies": SWING_RALLIES, "detection": SWING}), encoding="utf-8")
    return d


def system(d, tmp_path, model_doc="default", hit_source="learned"):
    s = object.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(d)
    path = tmp_path / "rally-segmenter.json"
    if model_doc == "default":
        model_doc = model()
    if model_doc is not None:
        path.write_text(model_doc if isinstance(model_doc, str) else json.dumps(model_doc),
                        encoding="utf-8")
    s.rally_segmenter_path = str(path)
    s.rally_detection = dict(SWING)
    s.rally_list = list(SWING_RALLIES)
    s._hit_source = hit_source
    s._hit_list = [{"frame": int(t * FPS), "hitter": h, "confidence": 0.9} for t, h in HITS]
    s.detection_writer = None
    return s


def snapshot(d):
    return {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}


# ---- system.py ---------------------------------------------------------------------------------

def test_learned_segments_replace_the_swing_ones(tmp_path):
    d = write_run(tmp_path / "run")
    s = system(d, tmp_path)
    s._apply_learned_segments(FPS)
    doc = json.loads((d / "rally_segments.json").read_text())
    det = doc["detection"]
    assert det["signal"] == "learned" and det["attempted_signal"] == "learned"
    assert det["degraded"] is False and det["stroke_labels_withheld"] is False
    assert det["reason"] == "learned segmenter v-test"
    assert det["model"] == {"version": "v-test", "mode": "gap", "train_f1": 0.83}   # no held-out block stored
    assert det["court_view"] == {"cut": 0.8} and det["suppressed_static_shuttle"] == 2
    assert [r["id"] for r in doc["rallies"]] == [1, 2]
    assert doc["rallies"][0]["start_sec"] == pytest.approx(3.0)      # first hit - 2 s
    assert doc["rallies"][1]["end_sec"] == pytest.approx(23.0)       # last hit + 1 s
    assert all(r["degraded"] is False for r in doc["rallies"])
    assert s.rally_detection == det and [r["id"] for r in s.rally_list] == [1, 2]
    hits = json.loads((d / "hits.json").read_text())
    assert [h["rally_id"] for h in hits["hits"]] == [1, 1, 1, 1, 2, 2, 2]
    assert hits["model"] == {"sha256": "x"} and hits["source"] == "learned"


def test_detection_model_f1_is_the_heldout_score(tmp_path):
    """Review 15: the stored held-out F1, not the training F1, is what the file reports as ``f1``."""
    d = write_run(tmp_path / "run")
    s = system(d, tmp_path, model_doc=model(heldout={"f1": 0.61, "swing_f1": 0.5}))
    s._apply_learned_segments(FPS)
    det = json.loads((d / "rally_segments.json").read_text())["detection"]
    assert det["model"] == {"version": "v-test", "mode": "gap", "f1": 0.61}


def test_the_learned_post_step_writes_segments_and_hits_atomically(tmp_path, monkeypatch):
    """Review 14: both files go through the atomic writer (temp file + replace), never a plain open('w')."""
    from badminton_analysis.outcomes import labels as lab
    d = write_run(tmp_path / "run")
    s = system(d, tmp_path)
    real, written = lab.write_json_atomic, []
    monkeypatch.setattr(lab, "write_json_atomic", lambda path, doc: written.append(os.path.basename(str(path)))
                        or real(path, doc))
    from badminton_analysis.data import writer
    monkeypatch.setattr(writer, "write_json", lambda *a, **k: pytest.fail("non-atomic writer used"))
    s._apply_learned_segments(FPS)
    assert written == ["rally_segments.json", "hits.json"]
    assert json.loads((d / "rally_segments.json").read_text())["detection"]["signal"] == "learned"


@pytest.mark.parametrize("case", ["no_file", "corrupt", "wrong_schema", "contact_rule", "no_hits"])
def test_fallback_leaves_every_file_byte_identical(tmp_path, case):
    d = write_run(tmp_path / "run")
    kw = {}
    if case == "no_file":
        kw["model_doc"] = None
    elif case == "corrupt":
        kw["model_doc"] = "{not json"
    elif case == "wrong_schema":
        kw["model_doc"] = model(schema=99)
    elif case == "contact_rule":
        kw["hit_source"] = "contact_rule"
    s = system(d, tmp_path, **kw)
    if case == "no_hits":
        s._hit_list = []
    before = snapshot(d)
    detection, rallies = dict(s.rally_detection), list(s.rally_list)
    s._apply_learned_segments(FPS)
    assert snapshot(d) == before
    assert s.rally_detection == detection and s.rally_list == rallies


def test_exception_keeps_the_swing_output(tmp_path, monkeypatch):
    d = write_run(tmp_path / "run")
    s = system(d, tmp_path)
    monkeypatch.setattr(segmenter, "segments", lambda *a, **k: 1 / 0)
    before = snapshot(d)
    s._apply_learned_segments(FPS)
    assert snapshot(d) == before and s.rally_detection["signal"] == "swing"


def test_a_failed_second_write_rolls_the_first_back(tmp_path, monkeypatch):
    d = write_run(tmp_path / "run")
    s = system(d, tmp_path)
    real = sysmod.BadmintonAnalysisSystem._rewrite_hit_rally_ids

    def boom(self, rallies):
        raise OSError("disk full")
    monkeypatch.setattr(sysmod.BadmintonAnalysisSystem, "_rewrite_hit_rally_ids", boom)
    before = snapshot(d)
    s._apply_learned_segments(FPS)
    monkeypatch.setattr(sysmod.BadmintonAnalysisSystem, "_rewrite_hit_rally_ids", real)
    assert json.loads((d / "rally_segments.json").read_text()) == json.loads(before["rally_segments.json"])
    assert s.rally_detection["signal"] == "swing"


def test_the_stroke_stage_is_not_withheld_for_learned(tmp_path):
    d = write_run(tmp_path / "run")
    s = system(d, tmp_path)
    s._apply_learned_segments(FPS)
    assert not s.rally_detection.get("stroke_labels_withheld", bool(s.rally_detection.get("degraded")))
    assert s._doubles_constraint_frames()   # identity constraint accepts it too


def test_default_model_path_is_under_weights():
    s = object.__new__(sysmod.BadmintonAnalysisSystem)
    assert s._rally_segmenter_path().replace("\\", "/").endswith("weights/rally-segmenter.json")


# ---- consumers on a learned-segment fixture ------------------------------------------------------

def learned_run(tmp_path, **kw):
    kw.setdefault("rallies", [(3.0, 10.0), (18.0, 23.0)])
    kw.setdefault("hits", [(t, h) for t, h in HITS])
    kw.setdefault("strokes", [("lower", "smash", 150), ("upper", "clear", 360)])
    kw.setdefault("frames", 900)
    return make_run(tmp_path, signal="learned", **kw)


def test_summary_counts_learned_as_rallies(tmp_path):
    out = learned_run(tmp_path)
    payload = json.loads((out / "rally_segments.json").read_text())
    expected = {"count": 2, "suppressed": False, "label": "rallies", "signal": "learned",
                "degraded": False, "failed": False}
    assert summarise_rally_payload(payload) == expected
    assert webapp._rally_summary(out) == expected


def test_insights_rally_is_ok_not_partial(tmp_path):
    env = rally_insight(load_run(learned_run(tmp_path)))
    assert env["status"] == "ok"
    assert env["value"]["signal"] == "learned" and env["value"]["label"] == "rallies"
    assert env["value"]["suppressed"] is False and env["value"]["count"] == 2
    assert env["value"]["shots_per_rally"] == [4, 3]


def test_strategy_facts_have_no_signal_caveat(tmp_path):
    pack = build_facts(learned_run(tmp_path), {"upper": {"name": "S"}, "lower": {"name": "V"}},
                       {"athletes": {}, "matches": {}})
    codes = [c["code"] for c in pack["caveats"]]
    assert "rally_signal" not in codes and "degraded_rallies" not in codes
    assert {f["id"]: f for f in pack["facts"]}["match.rallies"]["value"] == 2


def test_strategy_shots_assign_hits_to_learned_rallies(tmp_path):
    table = build_shots(learned_run(tmp_path))
    assert table is not None
    rows = table["shots"] if isinstance(table, dict) and "shots" in table else table
    assert {r.get("rally") for r in rows if isinstance(r, dict)} - {None} <= {1, 2}


def test_outcomes_build_accepts_learned_segments(tmp_path):
    out = learned_run(tmp_path)
    doc = outcomes_build.build(out, labels_dir=tmp_path / "labels", athletes_path=tmp_path / "a.json")
    assert doc is not None
    assert len(doc["rallies"]) == 2


def test_owner_labels_apply_on_a_learned_result(tmp_path):
    out = learned_run(tmp_path)
    result = owner_labels.apply_to_result(out, tmp_path / "labels", "clip")
    assert result["reason"] in ("no_strokes", "no_labels", "unreadable") or result["applied"]


def test_relabel_is_not_withheld_for_learned(tmp_path):
    out = learned_run(tmp_path)
    got = relabel.relabel(str(out), None, allow_unknown=False, labels_dir=None)
    assert got.get("skipped") != "labels withheld"


def test_segments_from_run_dir_match_in_memory_gap_count(tmp_path):
    from badminton_analysis.outcomes import run_inputs
    d = write_run(tmp_path / "run")
    inp = run_inputs.load(d)
    assert len(sf.gaps(inp)) == len(HITS) - 1
    segs = segmenter.segments(inp, model())
    assert [s["id"] for s in segs] == [1, 2] and all(s["degraded"] is False for s in segs)


# ---- more consumers: player positions, insights routes, strategy routes -------------------------

def test_player_positions_do_not_depend_on_the_segment_signal(tmp_path):
    """The position maps cut their own rallies from detections.jsonl; a learned file changes nothing."""
    from badminton_analysis.visualization.player_positions import PlayerPositionVisualizer
    out = learned_run(tmp_path, frames=900)

    def load():
        viz = PlayerPositionVisualizer(str(out / "detections.jsonl"), str(tmp_path / "viz"), fps=30)
        return viz._load_data()

    with_learned = load()
    (out / "rally_segments.json").unlink()
    without = load()
    assert len(with_learned) == len(without) and with_learned.equals(without)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    (tmp_path / "videos").mkdir()
    monkeypatch.setattr(webapp, "VIDEOS", tmp_path / "videos")
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(webapp, "ATHLETES_PATH", tmp_path / "data" / "athletes.json")
    monkeypatch.setattr(webapp, "jobs", {})
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), tmp_path


def test_insights_routes_serve_a_learned_run(app_env):
    client, tmp_path = app_env
    learned_run(tmp_path)
    (tmp_path / "videos" / "clip.mp4").write_bytes(b"x")
    video = client.get("/api/insights/video/clip")
    assert video.status_code == 200
    rally = video.get_json()["rally"]
    assert rally["status"] == "ok" and rally["value"]["signal"] == "learned"
    assert rally["value"]["count"] == 2 and rally["value"]["suppressed"] is False
    overview = client.get("/api/insights/overview?range=all")
    assert overview.status_code == 200
    assert {m["stem"] for m in overview.get_json()["matches"]} == {"clip"}      # a learned run is a library run


def test_strategy_routes_serve_a_learned_run(app_env):
    client, tmp_path = app_env
    learned_run(tmp_path)
    got = client.get("/api/strategy/clip")
    assert got.status_code == 200 and got.get_json()["status"] == "none"
    # the learned file counts as an analysis: regenerate is not refused as "no match analysis yet"
    assert client.post("/api/strategy/clip/regenerate", json={}).status_code != 404
