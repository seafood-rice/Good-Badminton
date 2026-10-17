import cv2
import numpy as np

from badminton_analysis.visualization import doubles_render as dr

TRACKS = {
    "P1": {f: {"court": (1.5, 10.0), "image": (40, 60)} for f in range(1, 11)},
    "P3": {f: {"court": (4.5, 3.0), "image": (120, 20)} for f in range(1, 11)},
}
PAYLOAD = {"players": [{"id": "P1", "team": 1, "name": "Alice"}, {"id": "P3", "team": 2, "name": None}],
           "teams": [{"id": 1, "players": ["P1"]}, {"id": 2, "players": ["P3"]}]}


def test_write_heatmaps(tmp_path):
    written = dr.write_heatmaps(str(tmp_path), PAYLOAD, TRACKS)
    assert set(written) == {"heatmap_P1.png", "heatmap_P3.png", "heatmap_team1.png", "heatmap_team2.png"}
    for name in written:
        assert cv2.imread(str(tmp_path / name)) is not None


def test_write_heatmaps_never_raises(tmp_path):
    assert dr.write_heatmaps(str(tmp_path / "nope" / "\0bad"), PAYLOAD, TRACKS) == []


def _tiny_video(path, n=10):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 90))
    for _ in range(n):
        w.write(np.zeros((90, 160, 3), np.uint8))
    w.release()


def test_render_identity_video(tmp_path):
    src = tmp_path / "src.mp4"
    _tiny_video(src)
    out = tmp_path / "detect_src_players.mp4"
    assert dr.render_identity_video(str(src), str(out), TRACKS, PAYLOAD["players"]) is True
    cap = cv2.VideoCapture(str(out))
    ok, frame = cap.read()
    cap.release()
    assert ok and frame[60, 40].sum() > 0          # P1's marker drawn at its foot point


def test_render_identity_video_missing_source(tmp_path):
    assert dr.render_identity_video(str(tmp_path / "none.mp4"), str(tmp_path / "o.mp4"),
                                    TRACKS, PAYLOAD["players"]) is False
