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


def test_render_identity_video_stale_output(tmp_path):
    out = tmp_path / "detect_src_players.mp4"
    out.write_bytes(b"junk from an earlier run")
    assert dr.render_identity_video(str(tmp_path / "none.mp4"), str(out),
                                    TRACKS, PAYLOAD["players"]) is False
    assert not out.exists()


def test_render_identity_video_uses_replace_when_copy_fails(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    _tiny_video(src)
    out = tmp_path / "detect_src_players.mp4"
    monkeypatch.setattr("badminton_analysis.media.video_audio.process_video_without_audio",
                        lambda **kwargs: False)
    assert dr.render_identity_video(str(src), str(out), TRACKS, PAYLOAD["players"]) is True
    cap = cv2.VideoCapture(str(out))
    ok, _ = cap.read()
    cap.release()
    assert ok
    assert not (tmp_path / ("temp_" + out.name)).exists()


def test_render_identity_video_failure_leaves_no_temp(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    _tiny_video(src)
    out = tmp_path / "detect_src_players.mp4"

    class _BrokenWriter:
        def __init__(self, path):
            with open(path, "wb") as fh:
                fh.write(b"partial")

        def write(self, frame):
            raise RuntimeError("disk full")

        def release(self):
            pass

    monkeypatch.setattr("badminton_analysis.media.video_audio.setup_video_writer",
                        lambda frame_width, frame_height, fps, temp_output_path: _BrokenWriter(temp_output_path))
    assert dr.render_identity_video(str(src), str(out), TRACKS, PAYLOAD["players"]) is False
    assert not out.exists()
    assert not (tmp_path / ("temp_" + out.name)).exists()


def test_write_heatmaps_dollar_in_name(tmp_path):
    payload = {"players": [{"id": "P1", "team": 1, "name": "$a^$b_"}],
               "teams": [{"id": 1, "players": ["P1"]}, {"id": 2, "players": ["P3"]}]}
    written = dr.write_heatmaps(str(tmp_path), payload, TRACKS)
    assert set(written) == {"heatmap_P1.png", "heatmap_P3.png", "heatmap_team1.png", "heatmap_team2.png"}


def _failing_savefig(monkeypatch, fail_on):
    """Make the fail_on-th Figure.savefig call raise; earlier calls save normally."""
    from matplotlib.figure import Figure

    real, calls = Figure.savefig, {"n": 0}

    def savefig(self, path, *a, **k):
        calls["n"] += 1
        if calls["n"] == fail_on:
            with open(path, "wb") as fh:
                fh.write(b"partial")
            raise OSError("disk full")
        return real(self, path, *a, **k)

    monkeypatch.setattr(Figure, "savefig", savefig)


def test_heatmap_figure_closed_when_save_fails(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt

    _failing_savefig(monkeypatch, 1)
    before = set(plt.get_fignums())
    try:
        dr._heatmap_png(str(tmp_path / "h.png"), [(1.0, 2.0)], "P1")
    except OSError:
        pass
    assert set(plt.get_fignums()) == before


def test_write_heatmaps_abort_leaves_no_partial_pngs(tmp_path, monkeypatch):
    _failing_savefig(monkeypatch, 3)
    assert dr.write_heatmaps(str(tmp_path), PAYLOAD, TRACKS) == []
    assert list(tmp_path.glob("heatmap_*.png")) == []


def test_label_sprite_renders_cjk_with_simhei():
    assert dr.FONT_PATH is not None and dr.FONT_PATH.endswith("simhei.ttf")
    cjk, _ = dr._label_sprite("张三", (255, 255, 255), 24)
    qq, _ = dr._label_sprite("??", (255, 255, 255), 24)
    assert cjk.shape != qq.shape or not np.array_equal(cjk, qq)
    assert cjk.max() > 0.5                          # glyphs, not empty boxes


def test_label_sprite_without_font_falls_back(monkeypatch):
    monkeypatch.setattr(dr, "FONT_PATH", None)
    dr._label_sprite.cache_clear()
    try:
        assert dr._label_sprite("张三", (255, 255, 255), 24) is None
    finally:
        dr._label_sprite.cache_clear()


def _label_region(tmp_path, name):
    tmp_path.mkdir()
    src = tmp_path / "src.mp4"
    _tiny_video(src, n=2)
    out = tmp_path / "detect_src_players.mp4"
    tracks = {"P1": {1: {"court": (1.5, 10.0), "image": (20, 70)}}}
    assert dr.render_identity_video(str(src), str(out), tracks,
                                    [{"id": "P1", "team": 1, "name": name}]) is True
    cap = cv2.VideoCapture(str(out))
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame[30:70, 26:110].astype(int)


def test_identity_video_draws_cjk_label(tmp_path, monkeypatch):
    cjk = _label_region(tmp_path / "pil", "张三")
    monkeypatch.setattr(dr, "FONT_PATH", None)        # the old cv2.putText path: "??????"
    dr._label_sprite.cache_clear()
    try:
        fallback = _label_region(tmp_path / "cv2", "张三")
    finally:
        dr._label_sprite.cache_clear()
    assert cjk.max() > 100                          # label drawn up-right of the marker
    assert np.abs(cjk - fallback).mean() > 5        # real glyphs, not question marks


def test_render_identity_video_reports_progress(tmp_path):
    src = tmp_path / "src.mp4"
    _tiny_video(src, n=6)
    done = []
    assert dr.render_identity_video(str(src), str(tmp_path / "o_players.mp4"), TRACKS,
                                    PAYLOAD["players"], progress=done.append) is True
    assert done == [1, 2, 3, 4, 5, 6]
