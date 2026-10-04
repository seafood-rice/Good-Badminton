import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import shuttleset_labels as sl  # noqa: E402

CORNERS = [[407.6, 308.6], [867.0, 307.4], [973.0, 669.4], [307.2, 671.2]]
SET_CSV = (
    "rally,ball_round,time,frame_num,roundscore_A,roundscore_B,player,server,type,aroundhead,"
    "backhand,hit_height,hit_area,hit_x,hit_y,landing_height,landing_area,landing_x,landing_y,"
    "lose_reason,win_reason,getpoint_player,flaw,player_location_area,player_location_x,"
    "player_location_y,opponent_location_area,opponent_location_x,opponent_location_y,db\n"
    "1,1.0,00:06:28,11657.0,0,1,B,1,發短球,,1.0,2.0,,,,2.0,7,597.0,390.0,,,,,8,650.0,532.0,8,561.0,341.0,0\n"
    "1,2.0,00:06:29,11678.0,0,1,A,2,挑球,,,2.0,7,597.0,390.0,1.0,9,800.0,633.0,,,,,8,591.0,373.0,8,626.0,532.0,0\n"
    "2,1.0,00:06:58,12557.0,0,1,B,1,未知球種,,,,,,,,,,,,,,,,,,,,,0\n"
)


def test_all_18_types_map_and_unknown_is_none():
    # The README lists 18 types; the CSVs add 過度切球 (alternate spelling of 過渡切球)
    # so the mapping has 19 total.
    readme_types = {
        "發短球", "發長球", "長球", "挑球", "防守回挑", "殺球", "點扣",
        "切球", "過渡切球", "放小球", "擋小球", "勾球", "撲球",
        "平球", "小平球", "後場抽平球", "推球", "防守回抽"
    }
    assert len(readme_types) == 18
    assert readme_types == set(sl.TYPE_TO_STROKE7.keys()) - {"過度切球"}
    assert len(sl.TYPE_TO_STROKE7) == 19
    assert set(sl.TYPE_TO_STROKE7.values()) == set(sl.STROKE7)
    assert sl.stroke7_of("挑球") == "lift" and sl.stroke7_of("長球") == "clear"
    assert sl.stroke7_of("推球") == "drive" and sl.stroke7_of("撲球") == "net"
    assert sl.stroke7_of("過度切球") == "drop"
    assert sl.stroke7_of("未知球種") is None and sl.stroke7_of("") is None


def test_load_corners_reorders_to_far_left_far_right_near_right_near_left(tmp_path):
    csv = tmp_path / "homography.csv"
    csv.write_text("id,video,homography_matrix,upleft_x,upright_x,downleft_x,downright_x,"
                   "upleft_y,upright_y,downleft_y,downright_y\n"
                   '1,M,"[[1,0,0],[0,1,0],[0,0,1]]",407.6,867.0,307.2,973.0,308.6,307.4,671.2,669.4\n',
                   encoding="utf-8")
    assert sl.load_corners(str(csv), "M") == CORNERS
    assert sl.scale_corners(CORNERS, (1920, 1080))[0] == [407.6 * 1.5, 308.6 * 1.5]


def test_hitter_side_from_location_against_net_row():
    assert sl.hitter_side(532.0, CORNERS) == "lower"
    assert sl.hitter_side(373.0, CORNERS) == "upper"
    assert sl.hitter_side(float("nan"), CORNERS) is None


def test_parse_match_hits_and_rally_spans(tmp_path):
    d = tmp_path / "M"
    d.mkdir()
    (d / "set1.csv").write_text(SET_CSV, encoding="utf-8")
    hits = sl.parse_match_hits(str(d), CORNERS)
    assert [h["frame30"] for h in hits] == [11657, 11678, 12557]
    assert hits[0]["stroke7"] == "serve" and hits[0]["side"] == "lower"
    assert hits[1]["stroke7"] == "lift" and hits[1]["side"] == "upper"
    assert hits[2]["stroke7"] is None and hits[2]["side"] is None
    spans = sl.rally_spans(hits)
    assert [(s["set"], s["rally"]) for s in spans] == [(1, 1), (1, 2)]
    assert abs(spans[0]["start"] - (11657 / 30.0 - 1.0)) < 1e-9
    assert abs(spans[0]["end"] - (11678 / 30.0 + 1.5)) < 1e-9


def test_best_offset_recovers_a_shift_and_rejects_noise():
    rng = np.random.default_rng(0)
    labels = np.sort(rng.uniform(0, 300, 80))
    events = np.sort(np.concatenate([labels + 2.0 + rng.normal(0, 0.02, 80),
                                     rng.uniform(0, 300, 30)]))
    r = sl.best_offset(labels.tolist(), events.tolist())
    assert abs(r["offset"] - 2.0) < 0.05 and r["accepted"] is True
    noise = sl.best_offset(labels.tolist(), np.sort(rng.uniform(0, 300, 110)).tolist())
    assert noise["accepted"] is False
    assert sl.best_offset([], [1.0])["accepted"] is False


def test_best_offset_is_the_plateau_centre_for_noise_free_labels():
    labels = np.sort(np.random.default_rng(1).uniform(0, 300, 60))   # aperiodic, or shifted copies tie
    for shift in (2.0, -3.0):
        r = sl.best_offset(labels.tolist(), (labels + shift).tolist(), tol_sec=0.1)
        assert abs(r["offset"] - shift) <= 1 / 60, (shift, r)
        assert r["accepted"] is True


def test_fps_ok_accepts_30_and_rejects_slower_videos():
    assert sl.fps_ok(29.97) and sl.fps_ok(30) and sl.fps_ok(60)
    assert not sl.fps_ok(25) and not sl.fps_ok(0) and not sl.fps_ok(None)


def test_alignment_script_rejects_a_25_fps_video_without_tracking(tmp_path, monkeypatch, capsys):
    import json

    import check_shuttleset_alignment as chk

    class Cap:
        def get(self, prop):
            return {chk.cv2.CAP_PROP_FPS: 25.0, chk.cv2.CAP_PROP_FRAME_WIDTH: 1280.0,
                    chk.cv2.CAP_PROP_FRAME_HEIGHT: 720.0}[prop]

        def release(self):
            pass

    monkeypatch.setattr(chk.cv2, "VideoCapture", lambda p: Cap())
    monkeypatch.setattr(chk.tn, "load_tracknet", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tracked")))
    assert chk.main(["--match", "m1", "--data", str(tmp_path)]) == 3
    written = json.loads((tmp_path / "alignment" / "m1.json").read_text())
    assert written["accepted"] is False and written["fps"] == 25.0 and written["width"] == 1280
    assert written["reason"] == "fps below 30 (resampling would repeat frames)"
    assert "REJECTED" in capsys.readouterr().out
