# tests/test_tracknet_wrapper.py
import badminton_analysis.shuttle_track.tracknet as tn


def test_track_video_converts_pred_dict_and_gates_roi(monkeypatch):
    fake = {"Frame": [0, 1, 2, 3],
            "X": [100, 0, 900, 150],
            "Y": [200, 0, 200, 210],
            "Visibility": [1, 0, 1, 1]}
    monkeypatch.setattr(tn, "_run_prediction", lambda **kw: fake)

    models = {"tracknet_file": "x.pt", "inpaintnet_file": None, "device": "cpu"}
    # ROI rect keeps x in [0,800], y in [0,600]; frame 2 (x=900) is off-court.
    traj = tn.track_video("video.mp4", models, court_roi=[(0, 0), (800, 600)])

    assert traj[0] == (100.0, 200.0)
    assert traj[1] is None          # Visibility 0
    assert traj[2] is None          # gated out by ROI
    assert traj[3] == (150.0, 210.0)


def test_track_video_without_roi_keeps_all_visible(monkeypatch):
    fake = {"Frame": [0, 1], "X": [900, 10], "Y": [900, 10], "Visibility": [1, 1]}
    monkeypatch.setattr(tn, "_run_prediction", lambda **kw: fake)
    traj = tn.track_video("v.mp4", {"tracknet_file": "x.pt", "inpaintnet_file": None, "device": "cpu"})
    assert traj[0] == (900.0, 900.0) and traj[1] == (10.0, 10.0)


def test_load_tracknet_missing_file_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        tn.load_tracknet(str(tmp_path / "nope.pt"))
