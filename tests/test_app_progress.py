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
