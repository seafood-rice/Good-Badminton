import badminton_analysis.system as sysmod
from badminton_analysis.stroke.events import detect_contacts


def _make_system(tmp_path, **kw):
    """Construct without running __init__'s heavy model loads."""
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.analyze_technique = True
    s.tracknet_weights = kw.get("tracknet_weights")
    s.inpaintnet_weights = kw.get("inpaintnet_weights")
    s._shuttle_trajectory = None
    s._shuttle_source = "yolo"
    s._analysis_track = []
    s._analysis_frames = {}
    s.save_dir = str(tmp_path)
    s.video_path = str(tmp_path / "v.mp4")
    s.court_roi_corners = [(0, 0), (1000, 1000)]
    s.frame_width, s.frame_height = 1280, 720
    return s


def test_capture_prefers_trajectory_with_plus_one_alignment(tmp_path):
    s = _make_system(tmp_path)
    # TrackNet 0-based frame 0 -> match loop frame_count 1.
    s._shuttle_trajectory = {1: (500.0, 300.0)}
    s._shuttle_source = "tracknet"
    # Minimal _capture_analysis_frame path: no pose, yolo ball says elsewhere.
    s.player_tracker = type("PT", (), {"players": {}})()
    s.player_pose_visualizer = type("PV", (), {"get_current_pose_data": lambda self=None: None})()
    s._racket_detector = None
    s.dominant_hand = "right"

    s._capture_analysis_frame(1, frame=None, roi_corners=[(0, 0), (1000, 1000)], ball_position=[0, 0])
    rec = s._analysis_track[-1]
    assert rec["shuttle"] == (500.0, 300.0)  # from trajectory, not yolo (which was [0,0])


def test_capture_falls_back_to_yolo_when_no_trajectory(tmp_path):
    s = _make_system(tmp_path)
    s._shuttle_trajectory = None
    s.player_tracker = type("PT", (), {"players": {}})()
    s.player_pose_visualizer = type("PV", (), {"get_current_pose_data": lambda self=None: None})()
    s._racket_detector = None
    s.dominant_hand = "right"
    s._capture_analysis_frame(1, frame=None, roi_corners=[(0, 0), (1000, 1000)], ball_position=[123, 456])
    assert s._analysis_track[-1]["shuttle"] == (123.0, 456.0)


def test_pretrack_is_never_fatal(tmp_path, monkeypatch):
    s = _make_system(tmp_path, tracknet_weights="tn.pt")
    import badminton_analysis.shuttle_track.tracknet as tnmod
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    s._run_shuttle_pretrack()  # must not raise
    assert s._shuttle_trajectory is None
    assert s._shuttle_source == "yolo"


def test_dense_trajectory_yields_contacts(tmp_path):
    # A dense synthetic trajectory + racket near a direction-change frame -> >=1 contact,
    # where the same track with 65%-missing shuttle would yield 0.
    track = []
    for f in range(1, 41):
        x = 500 + (f * 5 if f <= 20 else (40 - f) * 5)   # rises then falls: a peak ~ f=20
        track.append({"frame": f, "racket_head": (x, 300) if f == 20 else (0, 0),
                      "shuttle": (x, 300)})
    contacts = detect_contacts(track, contact_px=80.0, dir_change_deg=30.0, min_gap=5)
    assert len(contacts) >= 1


def test_pretrack_applies_plus_one_offset(tmp_path, monkeypatch):
    s = _make_system(tmp_path, tracknet_weights="tn.pt")
    import badminton_analysis.shuttle_track.tracknet as tnmod
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: {"tracknet_file": "tn.pt", "inpaintnet_file": None, "device": None})
    monkeypatch.setattr(tnmod, "track_video", lambda *a, **k: {0: (500.0, 300.0), 5: None})
    s._run_shuttle_pretrack()
    assert s._shuttle_trajectory == {1: (500.0, 300.0), 6: None}
    assert s._shuttle_source == "tracknet"


def test_pretrack_uses_warm_cache_without_reinference(tmp_path, monkeypatch):
    import os
    import badminton_analysis.shuttle_track.tracknet as tnmod
    import badminton_analysis.shuttle_track.trajectory as tjmod
    s = _make_system(tmp_path, tracknet_weights="tn.pt")
    params = {"eval_mode": "weight", "inpaint": bool(s.inpaintnet_weights),
              "roi": s.court_roi_corners}
    key = tjmod.cache_key(s.video_path, params)
    tjmod.save_cache(os.path.join(s.save_dir, "shuttle_trajectory.json"), key,
                     {0: (10.0, 20.0), 3: None})

    def _boom(*a, **k):
        raise AssertionError("track_video must not run on a cache hit")
    monkeypatch.setattr(tnmod, "track_video", _boom)
    monkeypatch.setattr(tnmod, "load_tracknet",
                        lambda *a, **k: {"tracknet_file": "tn.pt", "inpaintnet_file": None, "device": None})
    s._run_shuttle_pretrack()
    assert s._shuttle_trajectory == {1: (10.0, 20.0), 4: None}
    assert s._shuttle_source == "tracknet"
