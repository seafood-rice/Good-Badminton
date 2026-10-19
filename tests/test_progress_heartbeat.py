import json
import os
import badminton_analysis.system as sysmod


def _sys(tmp_path):
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s._total_frames = 200
    return s


def test_write_progress_emits_json(tmp_path):
    s = _sys(tmp_path)
    s._write_progress("analyzing", current_frame=50)
    d = json.load(open(os.path.join(str(tmp_path), "progress.json"), encoding="utf-8"))
    assert d["stage"] == "analyzing"
    assert d["current_frame"] == 50
    assert d["total_frames"] == 200
    assert d["pct"] == 25  # 50/200
    assert isinstance(d["updated"], (int, float))


def test_write_progress_never_raises_on_bad_dir():
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = "/nonexistent\\zzz\\path"
    s._total_frames = 0
    s._write_progress("analyzing", current_frame=1)  # must not raise


def test_write_progress_stage_only_pins_pct(tmp_path):
    s = _sys(tmp_path)
    s._write_progress("encoding")
    d = json.load(open(os.path.join(str(tmp_path), "progress.json"), encoding="utf-8"))
    assert d["stage"] == "encoding"
    assert d["pct"] == 99


def _read(tmp_path):
    return json.load(open(os.path.join(str(tmp_path), "progress.json"), encoding="utf-8"))


def test_write_progress_substage(tmp_path):
    s = _sys(tmp_path)
    s._write_progress("visualizing", current_frame=40, substage="doubles_video")
    d = _read(tmp_path)
    assert d["stage"] == "visualizing" and d["substage"] == "doubles_video"
    assert d["current_frame"] == 40 and d["pct"] == 97
    s._write_progress("visualizing")
    assert "substage" not in _read(tmp_path)        # singles payload unchanged


def test_heartbeat_passes_substage_through():
    calls = []
    with sysmod._ProgressHeartbeat(lambda st, **kw: calls.append((st, kw)), "visualizing",
                                   interval=60, substage="doubles_video") as beat:
        beat.update(7)
    assert calls[0] == ("visualizing", {"current_frame": 0, "substage": "doubles_video"})
    assert calls[-1] == ("visualizing", {"current_frame": 7, "substage": "doubles_video"})


def test_heartbeat_without_substage_keeps_old_call():
    calls = []
    with sysmod._ProgressHeartbeat(lambda st, **kw: calls.append(kw), "tracking_shuttle",
                                   interval=60):
        pass
    assert all(kw == {"current_frame": 0} for kw in calls)
