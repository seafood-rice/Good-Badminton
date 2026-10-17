import json

import numpy as np

from badminton_analysis.court.mapper import CourtMapper
from badminton_analysis.tracking import doubles as dbl

# Image quad mapping 1 px = 1 cm: TL (0,0) .. BR (610,1340).
QUAD = [[0, 0], [610, 0], [610, 1340], [0, 1340]]
FPS = 30


def kp_for(foot_x, foot_y):
    kp = np.zeros((17, 2))
    kp[5], kp[6] = (foot_x - 20, foot_y - 130), (foot_x + 20, foot_y - 130)
    kp[11], kp[12] = (foot_x - 15, foot_y - 70), (foot_x + 15, foot_y - 70)
    kp[13], kp[14] = (foot_x - 15, foot_y - 35), (foot_x + 15, foot_y - 35)
    kp[15], kp[16] = (foot_x - 10, foot_y - 10), (foot_x + 10, foot_y - 10)
    kp[9], kp[10] = (foot_x - 30, foot_y - 90), (foot_x + 30, foot_y - 90)
    return kp


POS = {"a": (150, 1000), "b": (450, 1100), "c": (150, 300), "d": (450, 200)}


def feed(collector, frames, people=("a", "b", "c", "d"), extra=()):
    img = np.zeros((1400, 700, 3), np.uint8)
    img[:, :350] = (0, 0, 200)
    img[:, 350:] = (200, 0, 0)
    for f in frames:
        cents, kps = [], []
        for p in people:
            x, y = POS[p]
            cents.append((x, y))
            kps.append(kp_for(x, y))
        for (x, y) in extra:
            cents.append((x, y))
            kps.append(kp_for(x, y))
        collector.observe(f, img, cents, kps)


def make():
    return dbl.DoublesCollector(CourtMapper(QUAD), QUAD, FPS)


def test_finalize_writes_players_and_tracklets(tmp_path):
    c = make()
    feed(c, range(1, 91))
    payload = c.finalize(str(tmp_path), analysed_frames=90)
    assert (tmp_path / "tracklets.jsonl").exists()
    saved = json.loads((tmp_path / "players.json").read_text(encoding="utf-8"))
    assert saved["match_format"] == "doubles" and saved["validated"] is False
    assert saved["status"] == payload["status"]
    assert len(saved["players"]) == 4
    assert {p["id"] for p in saved["players"]} == {"P1", "P2", "P3", "P4"}
    assert saved["stroke_labels_withheld"].startswith("doubles")


def test_tracklets_jsonl_keypoints_rounded_and_tracks_share_arrays(tmp_path):
    c = make()
    img = np.zeros((1400, 700, 3), np.uint8)
    for f in range(1, 61):
        cents = [POS[p] for p in "abcd"]
        kps = [kp_for(x + 0.123456, y + 0.987654) for x, y in cents]
        c.observe(f, img, cents, kps)
    c.finalize(str(tmp_path), analysed_frames=60)
    row = json.loads((tmp_path / "tracklets.jsonl").read_text(encoding="utf-8").splitlines()[0])
    values = [v for kp in row["keypoints"] if kp is not None for pt in kp for v in pt]
    assert values and all(round(v, 1) == v for v in values)
    assert any(v != int(v) for v in values)               # rounded, not truncated to ints
    # identity_tracks references the tracker's float32 arrays (no per-frame copies).
    t = c.tracklets[0]
    pid = c.assignment[t["id"]]
    obs = c.identity_tracks[pid][t["frames"][0]]
    assert obs["keypoints"] is t["keypoints"][0]
    assert obs["keypoints"].dtype == np.float32


def test_observe_dedupes_near_duplicate_detections(tmp_path):
    c = make()
    # A duplicate of "c" 20 cm away (far player seen by both passes).
    feed(c, range(1, 91), extra=[(POS["c"][0] + 20, POS["c"][1])])
    c.finalize(str(tmp_path), analysed_frames=90)
    lines = (tmp_path / "tracklets.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4


def test_finalize_with_no_observations(tmp_path):
    payload = make().finalize(str(tmp_path), analysed_frames=0)
    assert payload["status"] == "degraded"
    assert payload["players"] == []
    assert (tmp_path / "players.json").exists()


def test_finalize_without_hits_json(tmp_path):
    c = make()
    feed(c, range(1, 91))
    payload = c.finalize(str(tmp_path), analysed_frames=90)
    assert payload["stats"]["hits_total"] == 0
    assert not (tmp_path / "hits.json").exists()


def test_finalize_augments_hits_json(tmp_path):
    c = make()
    feed(c, range(1, 91))
    (tmp_path / "hits.json").write_text(json.dumps(
        {"source": "contact_rule", "hits": [{"frame": 45, "hitter": "lower", "confidence": None}]}),
        encoding="utf-8")
    c.finalize(str(tmp_path), analysed_frames=90)
    hits = json.loads((tmp_path / "hits.json").read_text(encoding="utf-8"))
    assert hits["hitter_attribution"] == {"validated": False}
    h = hits["hits"][0]
    assert h["hitter"] == "lower"
    for key in ("hitter_id", "hitter_team", "hitter_confidence", "hitter_evidence"):
        assert key in h


def test_names_are_copied(tmp_path):
    (tmp_path / "player_names.json").write_text(json.dumps({"P1": "Alice"}), encoding="utf-8")
    c = make()
    feed(c, range(1, 91))
    payload = c.finalize(str(tmp_path), analysed_frames=90)
    names = {p["id"]: p["name"] for p in payload["players"]}
    assert names["P1"] == "Alice" and names["P2"] is None


def test_observe_never_raises():
    c = make()
    c.observe(1, None, [(1, 2)], [None])
    c.observe(2, np.zeros((10, 10, 3), np.uint8), [("x", None)], [object()])


def test_keypoints_from_pose_data_shifts_offsets():
    kp = np.zeros((1, 17, 2))
    kp[0, 5] = (10, 10)
    out = dbl.keypoints_from_pose_data({"keypoints": kp, "offset_x": 100, "offset_y": 50}, 1)
    assert tuple(out[0][5]) == (110, 60)
    assert tuple(out[0][0]) == (0, 0)                     # missing stays missing
    assert dbl.keypoints_from_pose_data(None, 2) == [None, None]
    assert dbl.keypoints_from_pose_data({"keypoints": kp, "offset_x": 0, "offset_y": 0}, 3) == [None, None, None]


def test_remove_doubles_outputs(tmp_path):
    for name in ("players.json", "tracklets.jsonl", "heatmap_P1.png", "heatmap_team1.png",
                 "detect_v_players.mp4", "keep.json"):
        (tmp_path / name).write_text("x")
    dbl.remove_doubles_outputs(str(tmp_path))
    assert [p.name for p in tmp_path.iterdir()] == ["keep.json"]


def test_finalize_passes_constraint_frames(tmp_path, monkeypatch):
    seen = {}
    real = dbl.assign_identities

    def wrapper(*args, **kwargs):
        seen["constraint_frames"] = kwargs.get("constraint_frames", "missing")
        return real(*args, **kwargs)

    monkeypatch.setattr("badminton_analysis.tracking.doubles.assign_identities", wrapper)
    c = make()
    feed(c, range(1, 91))
    frames = {10, 20, 30}
    c.finalize(str(tmp_path), analysed_frames=90, constraint_frames=frames)
    assert seen["constraint_frames"] == frames
