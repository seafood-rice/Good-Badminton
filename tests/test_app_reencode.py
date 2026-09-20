"""The browser-compat transcode must not fail silently.

Regression tests for the defect that shipped an unplayable video: OpenCV could
not initialise H.264 (openh264 DLL version mismatch) and silently fell back to
MPEG-4 Part 2, which no browser decodes; the rescue ffmpeg transcode was then
killed by a fixed 300 s timeout on a 679 s 4K video that needs ~425 s, leaving a
truncated file with no moov atom while the job still reported success.
"""
import os

import pytest

import app as app_module


class _FakeCompleted:
    returncode = 0


def test_timeout_scales_with_duration_not_a_fixed_300s():
    # A 679 s video measured at ~0.63x realtime to encode; 300 s could never
    # finish it. The budget must grow with the video.
    assert app_module._reencode_timeout_sec(679.0) > 425.0
    assert app_module._reencode_timeout_sec(3600.0) > app_module._reencode_timeout_sec(679.0)


def test_short_videos_still_get_a_usable_floor():
    """A 5 s clip must not get a 30 s budget on a cold/slow machine."""
    assert app_module._reencode_timeout_sec(5.0) >= 900.0


def test_missing_duration_falls_back_to_the_floor():
    assert app_module._reencode_timeout_sec(None) >= 900.0


def test_successful_transcode_replaces_the_raw_video(tmp_path, monkeypatch):
    raw = tmp_path / "detect_x.mp4"
    h264 = tmp_path / "detect_x_h264.mp4"
    raw.write_bytes(b"mpeg4-original")

    def fake_run(cmd, **kwargs):
        h264.write_bytes(b"h264-transcoded-and-complete")
        return _FakeCompleted()

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    err = app_module._reencode_to_h264(str(raw), str(h264), "ffmpeg", 60.0)

    assert err is None
    assert raw.read_bytes() == b"h264-transcoded-and-complete"
    assert not h264.exists(), "the intermediate must not be left behind"


def test_failed_transcode_deletes_the_partial_file(tmp_path, monkeypatch):
    """The truncated 848 MB file left by the timeout is what made a broken run
    look like a successful one. A failed transcode must leave nothing behind."""
    raw = tmp_path / "detect_x.mp4"
    h264 = tmp_path / "detect_x_h264.mp4"
    raw.write_bytes(b"mpeg4-original")

    def fake_run(cmd, **kwargs):
        h264.write_bytes(b"truncated-no-moov-atom")
        raise app_module.subprocess.TimeoutExpired(cmd, 300)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    err = app_module._reencode_to_h264(str(raw), str(h264), "ffmpeg", 679.0)

    assert err is not None
    assert "timed out" in err.lower() or "timeout" in err.lower()
    assert not h264.exists(), "a partial transcode must be deleted, not left on disk"
    assert raw.read_bytes() == b"mpeg4-original", "the original must survive"


def test_missing_ffmpeg_is_reported_not_swallowed(tmp_path, monkeypatch):
    raw = tmp_path / "detect_x.mp4"
    h264 = tmp_path / "detect_x_h264.mp4"
    raw.write_bytes(b"mpeg4-original")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    err = app_module._reencode_to_h264(str(raw), str(h264), None, 60.0)
    assert err is not None


def test_empty_output_is_not_treated_as_success(tmp_path, monkeypatch):
    raw = tmp_path / "detect_x.mp4"
    h264 = tmp_path / "detect_x_h264.mp4"
    raw.write_bytes(b"mpeg4-original")

    def fake_run(cmd, **kwargs):
        h264.write_bytes(b"")
        return _FakeCompleted()

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    err = app_module._reencode_to_h264(str(raw), str(h264), "ffmpeg", 60.0)
    assert err is not None
    assert raw.read_bytes() == b"mpeg4-original"


def test_duration_is_read_from_run_metadata(tmp_path):
    """Named _run_duration_sec, not _video_duration_sec: the latter already
    exists and reads a VIDEO PATH through cv2, which is a different thing."""
    (tmp_path / "metadata.json").write_text(
        '{"video": {"duration_sec": 678.96}}', encoding="utf-8")
    assert app_module._run_duration_sec(str(tmp_path)) == pytest.approx(678.96)


def test_duration_missing_metadata_is_none(tmp_path):
    assert app_module._run_duration_sec(str(tmp_path)) is None


def test_duration_unreadable_metadata_is_none(tmp_path):
    (tmp_path / "metadata.json").write_text("{not json", encoding="utf-8")
    assert app_module._run_duration_sec(str(tmp_path)) is None
