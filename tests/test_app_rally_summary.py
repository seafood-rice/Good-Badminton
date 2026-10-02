"""Task 10: the one summary of rally_segments.json every surface shares.

A swing-derived segmentation inflates rally COUNT by +36% and total rally time
by +41% (design spec §0.16/§0.17) and a degraded one has only coarse windows,
so neither may be reported as a rally count (§12a-C).
"""
import json

import pytest

import app as webapp


def _write(save_dir, payload):
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / "rally_segments.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def _payload(n, **detection):
    body = {"fps": 30.0,
            "rallies": [{"id": i + 1, "start_frame": 0, "end_frame": 1} for i in range(n)]}
    if detection:
        body["detection"] = detection
    return body


@pytest.mark.parametrize("detection, expected", [
    ({"signal": "shuttle", "degraded": False},
     {"count": 3, "suppressed": False, "label": "rallies", "signal": "shuttle", "degraded": False}),
    ({"signal": "courtview", "degraded": False},
     {"count": 3, "suppressed": False, "label": "rallies", "signal": "courtview", "degraded": False}),
    ({"signal": "swing", "degraded": False},
     {"count": 3, "suppressed": True, "label": "segments", "signal": "swing", "degraded": False}),
    ({"signal": "none", "degraded": True},
     {"count": 3, "suppressed": True, "label": "windows", "signal": "none", "degraded": True}),
    ({"signal": "swing", "degraded": True},
     {"count": 3, "suppressed": True, "label": "windows", "signal": "swing", "degraded": True}),
    ({"signal": "none", "degraded": False},
     {"count": 3, "suppressed": True, "label": "windows", "signal": "none", "degraded": False}),
])
def test_summary_per_signal(tmp_path, detection, expected):
    _write(tmp_path, _payload(3, **detection))
    assert webapp._rally_summary(tmp_path) == expected


def test_summary_error_signal_is_unknown_not_suppressed(tmp_path):
    _write(tmp_path, {"fps": 30.0, "rallies": [],
                      "detection": {"signal": "error", "degraded": False, "error": True,
                                    "reason": "ValueError"}})
    assert webapp._rally_summary(tmp_path) == {
        "count": 0, "suppressed": False, "label": "rallies", "signal": None, "degraded": False}


def test_summary_old_file_without_detection_counts_like_today(tmp_path):
    _write(tmp_path, _payload(4))
    assert webapp._rally_summary(tmp_path) == {
        "count": 4, "suppressed": False, "label": "rallies", "signal": None, "degraded": False}


def test_summary_missing_file(tmp_path):
    assert webapp._rally_summary(tmp_path) == {
        "count": 0, "suppressed": False, "label": "rallies", "signal": None, "degraded": False}


@pytest.mark.parametrize("raw", ["{not json", "[]", "null", '{"rallies": "x"}'])
def test_summary_unreadable_or_malformed_file_is_unknown(tmp_path, raw):
    _write(tmp_path, raw)
    assert webapp._rally_summary(tmp_path) == {
        "count": 0, "suppressed": False, "label": "rallies", "signal": None, "degraded": False}


# ---- /api/stats library totals ---------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path / "outputs")
    (tmp_path / "outputs").mkdir()
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), videos, tmp_path / "outputs"


def test_stats_total_sums_only_non_suppressed_runs(client):
    c, videos, outputs = client
    runs = {
        "reliable": _payload(5, signal="shuttle", degraded=False),
        "courtview": _payload(2, signal="courtview", degraded=False),
        "swing": _payload(7, signal="swing", degraded=False),
        "coarse": _payload(11, signal="none", degraded=True),
        "old": _payload(1),
    }
    for stem, payload in runs.items():
        (videos / f"{stem}.mp4").write_bytes(b"\x00")
        _write(outputs / stem, payload)
    (videos / "nofile.mp4").write_bytes(b"\x00")

    assert c.get("/api/stats").get_json()["rallies"] == 5 + 2 + 1


def _stat_client_with(client, runs, extra_videos=()):
    c, videos, outputs = client
    for stem, payload in runs.items():
        (videos / f"{stem}.mp4").write_bytes(b"\x00")
        if payload is not None:
            _write(outputs / stem, payload)
    for stem in extra_videos:
        (videos / f"{stem}.mp4").write_bytes(b"\x00")
    return c.get("/api/stats").get_json()


def test_stats_swing_only_library_is_unknown_not_zero(client):
    s = _stat_client_with(client, {"a": _payload(7, signal="swing", degraded=False),
                                   "b": _payload(3, signal="swing", degraded=False)})
    assert s["rallies"] is None                      # the tile shows a dash, not "0"
    assert s["rallies_suppressed_runs"] == 2


def test_stats_all_degraded_library_is_unknown_not_zero(client):
    s = _stat_client_with(client, {"a": _payload(4, signal="none", degraded=True)},
                          extra_videos=["not_analysed"])
    assert s["rallies"] is None and s["rallies_suppressed_runs"] == 1


def test_stats_mixed_library_sums_only_the_reliable_runs(client):
    s = _stat_client_with(client, {"a": _payload(5, signal="shuttle", degraded=False),
                                   "b": _payload(7, signal="swing", degraded=False)})
    assert s["rallies"] == 5 and s["rallies_suppressed_runs"] == 1


def test_stats_reliable_run_with_zero_rallies_is_a_real_zero(client):
    s = _stat_client_with(client, {"a": _payload(0, signal="shuttle", degraded=False)})
    assert s["rallies"] == 0 and "rallies_suppressed_runs" not in s


def test_stats_empty_library_is_unchanged(client):
    s = _stat_client_with(client, {})
    assert s["rallies"] == 0 and "rallies_suppressed_runs" not in s
    assert s["videos"] == 0


def test_stats_library_with_no_rally_files_is_unchanged(client):
    s = _stat_client_with(client, {"a": None, "b": None})
    assert s["rallies"] == 0 and "rallies_suppressed_runs" not in s
