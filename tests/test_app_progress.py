# tests/test_app_progress.py
import json
import os
import app as webapp


def test_read_progress_file(tmp_path):
    p = tmp_path / "progress.json"
    p.write_text(json.dumps({"stage": "analyzing", "pct": 42, "updated": 123.0}))
    d = webapp._read_progress_file(str(tmp_path))
    assert d["stage"] == "analyzing" and d["pct"] == 42


def test_read_progress_file_missing(tmp_path):
    assert webapp._read_progress_file(str(tmp_path)) is None


def test_log_tail(tmp_path):
    p = tmp_path / "analyze.log"
    p.write_text("\n".join(f"line{i}" for i in range(100)))
    tail = webapp._log_tail(str(p), n=5)
    assert "line99" in tail and "line0" not in tail


def test_reset_progress_file_removes_stale(tmp_path):
    p = tmp_path / "progress.json"
    p.write_text('{"stage":"done","pct":100}')
    webapp._reset_progress_file(str(tmp_path))
    assert not p.exists()


def test_reset_progress_file_missing_is_noop(tmp_path):
    webapp._reset_progress_file(str(tmp_path))  # must not raise


def test_progress_message_frames():
    msg = webapp._progress_message({"stage": "analyzing", "current_frame": 3, "total_frames": 9})
    assert msg == "分析中... 3/9 帧"


def test_progress_message_doubles_substages():
    msg = webapp._progress_message({"stage": "visualizing", "substage": "doubles_video",
                                    "current_frame": 120, "total_frames": 600})
    assert "players video" in msg and "120/600" in msg
    assert "Identifying players" in webapp._progress_message(
        {"stage": "visualizing", "substage": "doubles_identity"})
    # Unknown substages fall back to the plain frame message.
    assert webapp._progress_message({"substage": "x", "current_frame": 1,
                                     "total_frames": 2}) == "分析中... 1/2 帧"


def test_players_video_reencode_announces_itself():
    import inspect
    src = inspect.getsource(webapp)
    i = src.index("_reencode_players_video(sd, vs")
    assert "Encoding the players video" in src[src.rindex("job['message']", 0, i):i]
