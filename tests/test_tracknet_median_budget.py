"""The TrackNet median image must not decode 1800 full-resolution 4K frames (about 45 GB)."""
from badminton_analysis.shuttle_track import tracknet as tn


def test_4k_samples_few_frames_and_sd_keeps_the_default():
    n4k = tn.median_sample_count(3840, 2160)
    assert tn.MEDIAN_MIN_FRAMES <= n4k <= 100
    assert n4k * 3840 * 2160 * 3 <= tn.MEDIAN_BUDGET_BYTES * 1.4
    assert tn.median_sample_count(640, 360) == tn.MEDIAN_MAX_FRAMES
    assert tn.median_sample_count(0, 0) == tn.MEDIAN_MAX_FRAMES
    assert tn.median_sample_count(100000, 100000) == tn.MEDIAN_MIN_FRAMES


def test_track_video_passes_the_budgeted_sample_count(monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"Frame": [0], "X": [1.0], "Y": [2.0], "Visibility": [1]}

    monkeypatch.setattr(tn, "_run_prediction", fake)
    monkeypatch.setattr(tn, "_frame_size", lambda p: (3840, 2160))
    tn.track_video("x.mp4", {"tracknet_file": "t"})
    assert seen["max_sample_num"] == tn.median_sample_count(3840, 2160) < 200


def test_unreadable_size_keeps_the_vendor_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(tn, "_run_prediction", lambda **kw: seen.update(kw) or {"Frame": [], "X": [], "Y": [], "Visibility": []})
    monkeypatch.setattr(tn, "_frame_size", lambda p: (_ for _ in ()).throw(RuntimeError("no cv2")))
    tn.track_video("x.mp4", {"tracknet_file": "t"})
    assert "max_sample_num" not in seen
