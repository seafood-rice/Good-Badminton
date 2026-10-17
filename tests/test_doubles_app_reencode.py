"""The doubles identity video must get the same browser-compat transcode.

The renderer writes mp4v, which no browser decodes; the main video is
re-encoded after a run, and detect_<stem>_players.mp4 must be too, without a
failure there ever changing the job's main outcome.
"""
import inspect
import os

import app as app_module


def _record(monkeypatch, result=None):
    calls = []

    def fake(raw, h264, ffmpeg_bin, duration):
        calls.append((raw, h264, ffmpeg_bin, duration))
        return result

    monkeypatch.setattr(app_module, "_reencode_to_h264", fake)
    return calls


def test_players_video_is_reencoded_when_present(tmp_path, monkeypatch):
    (tmp_path / "detect_clip_players.mp4").write_bytes(b"mp4v")
    calls = _record(monkeypatch)

    warning = app_module._reencode_players_video(str(tmp_path), "clip", "ffmpeg", 12.5)

    assert warning is None
    assert calls == [(
        os.path.join(str(tmp_path), "detect_clip_players.mp4"),
        os.path.join(str(tmp_path), "detect_clip_players_h264.mp4"),
        "ffmpeg", 12.5)]


def test_players_video_absent_means_no_reencode(tmp_path, monkeypatch):
    (tmp_path / "detect_clip.mp4").write_bytes(b"main only (singles run)")
    calls = _record(monkeypatch)

    assert app_module._reencode_players_video(str(tmp_path), "clip", "ffmpeg", 12.5) is None
    assert calls == []


def test_players_video_failure_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    (tmp_path / "detect_clip_players.mp4").write_bytes(b"mp4v")
    _record(monkeypatch, result="ffmpeg failed: boom")

    warning = app_module._reencode_players_video(str(tmp_path), "clip", "ffmpeg", 12.5)

    assert warning == "ffmpeg failed: boom"
    assert "boom" in capsys.readouterr().out


def test_players_video_exception_is_never_fatal(tmp_path, monkeypatch):
    (tmp_path / "detect_clip_players.mp4").write_bytes(b"mp4v")

    def explode(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(app_module, "_reencode_to_h264", explode)

    warning = app_module._reencode_players_video(str(tmp_path), "clip", "ffmpeg", 12.5)
    assert warning is not None and "unexpected" in warning


def test_job_monitor_calls_players_reencode_after_main_video():
    src = inspect.getsource(app_module)
    main_call = src.index("video_warning = _reencode_to_h264(")
    players_call = src.index("_reencode_players_video(sd, vs,")
    assert players_call > main_call
    # Its result must not feed the job's main status/message.
    assert "video_warning = _reencode_players_video" not in src
