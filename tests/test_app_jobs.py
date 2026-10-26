"""GET /api/jobs (badminton_analysis/web/insights_routes.py)."""
import json
import os
import re
import time

import pytest

import app as webapp

MINUTE = 60.0
BILINGUAL = re.compile(r"^[^/]*[一-鿿][^/]* / [A-Za-z].*$")


@pytest.fixture
def env(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "ATHLETES_PATH", tmp_path / "data" / "athletes.json")
    monkeypatch.setattr(webapp, "jobs", {})
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), tmp_path, outputs


def _progress(outputs, stem, *, updated=None, stage="analyzing", pct=40, mtime=None):
    d = outputs / stem
    d.mkdir(parents=True, exist_ok=True)
    doc = {"stage": stage, "pct": pct}
    if updated is not None:
        doc["updated"] = updated
    p = d / "progress.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _ids(res):
    return [j["id"] for j in res.get_json()]


def test_no_jobs_is_an_empty_list(env):
    c, *_ = env
    res = c.get("/api/jobs")
    assert res.status_code == 200
    assert res.get_json() == []


def test_in_memory_job_serialises_without_the_proc_handle(env):
    c, tmp_path, outputs = env
    log = tmp_path / "secret" / "analyze.log"
    webapp.jobs["clip"] = {
        "status": "running", "progress": 42, "message": "分析中... 10/100 帧",
        "video_name": "clip.mp4", "save_dir": str(outputs / "clip"),
        "stage": "analyzing", "updated": time.time(), "proc": object(),
        "log_path": str(log)}
    res = c.get("/api/jobs")
    assert res.status_code == 200
    (job,) = res.get_json()
    assert set(job) == {"id", "video", "kind", "status", "stage", "progress",
                        "message", "updated"}
    assert job["id"] == "clip" and job["video"] == "clip"
    assert job["kind"] == "match" and job["status"] == "running"
    assert job["progress"] == 42 and job["stage"] == "analyzing"
    text = res.get_data(as_text=True)
    assert "proc" not in text and "secret" not in text and str(tmp_path) not in text


def test_error_message_is_cut_to_its_first_line(env):
    c, tmp_path, _ = env
    webapp.jobs["clip"] = {
        "status": "error", "progress": 5, "video_name": "clip.mp4", "stage": "error",
        "message": f"分析失败 (exit=1)\nTraceback in {tmp_path}/x.py"}
    (job,) = c.get("/api/jobs").get_json()
    assert job["message"] == "分析失败 (exit=1)"


def test_posture_job_kind(env):
    c, *_ = env
    webapp.jobs["posture_clip"] = {"status": "running", "progress": 3,
                                   "video_name": "clip", "message": ""}
    webapp.jobs["other"] = {"status": "running", "progress": 3, "video_name": "other",
                            "stage": "posture_lifting", "message": ""}
    webapp.jobs["plain"] = {"status": "completed", "progress": 100, "video_name": "plain",
                            "stage": "done", "message": ""}
    kinds = {j["id"]: j["kind"] for j in c.get("/api/jobs").get_json()}
    assert kinds == {"posture_clip": "posture", "other": "posture", "plain": "match"}


def test_strategy_job_kind_and_no_detached_twin(env):
    c, _, outputs = env
    _progress(outputs, "clip", updated=time.time())
    webapp.jobs["strategy_clip"] = {"status": "running", "progress": 0, "video_name": "clip",
                                    "stage": "strategy", "message": ""}
    rows = c.get("/api/jobs").get_json()
    assert [(j["id"], j["kind"]) for j in rows] == [("strategy_clip", "strategy")]


def test_disk_run_updated_now_is_listed_as_detached(env):
    c, _, outputs = env
    _progress(outputs, "orphan", updated=time.time(), stage="analyzing", pct=55)
    (job,) = c.get("/api/jobs").get_json()
    assert job["id"] == "orphan" and job["video"] == "orphan"
    assert job["status"] == "detached" and job["kind"] == "match"
    assert job["progress"] == 55 and job["stage"] == "analyzing"


def test_disk_run_updated_eleven_minutes_ago_is_not_listed(env):
    c, _, outputs = env
    _progress(outputs, "stale", updated=time.time() - 11 * MINUTE)
    assert c.get("/api/jobs").get_json() == []


def test_disk_run_updated_nine_minutes_ago_is_listed(env):
    c, _, outputs = env
    _progress(outputs, "recent", updated=time.time() - 9 * MINUTE)
    assert _ids(c.get("/api/jobs")) == ["recent"]


def test_disk_run_without_updated_field_falls_back_to_mtime(env):
    c, _, outputs = env
    _progress(outputs, "fresh_file", mtime=time.time() - MINUTE)
    _progress(outputs, "old_file", mtime=time.time() - 11 * MINUTE)
    assert _ids(c.get("/api/jobs")) == ["fresh_file"]


def test_own_updated_field_wins_over_mtime(env):
    c, _, outputs = env
    _progress(outputs, "lying_mtime", updated=time.time() - 30 * MINUTE,
              mtime=time.time())
    assert c.get("/api/jobs").get_json() == []


def test_corrupt_progress_file_uses_mtime_and_does_not_fail(env):
    c, _, outputs = env
    d = outputs / "garbled"
    d.mkdir()
    (d / "progress.json").write_text("{nope", encoding="utf-8")
    res = c.get("/api/jobs")
    assert res.status_code == 200
    assert _ids(res) == ["garbled"]


def test_disk_run_already_in_the_job_dict_is_not_duplicated(env):
    c, _, outputs = env
    _progress(outputs, "clip", updated=time.time())
    _progress(outputs, "other", updated=time.time())
    webapp.jobs["clip"] = {"status": "running", "progress": 1, "video_name": "clip.mp4"}
    webapp.jobs["posture_other"] = {"status": "running", "progress": 1,
                                    "video_name": "other"}
    res = c.get("/api/jobs")
    assert sorted(_ids(res)) == ["clip", "posture_other"]
    assert all(j["status"] != "detached" for j in res.get_json())


def test_newest_first(env):
    c, _, outputs = env
    now = time.time()
    webapp.jobs["mid"] = {"status": "completed", "progress": 100, "video_name": "mid",
                          "updated": now - 120}
    webapp.jobs["old"] = {"status": "completed", "progress": 100, "video_name": "old",
                          "updated": now - 300}
    _progress(outputs, "newest", updated=now - 5)
    assert _ids(c.get("/api/jobs")) == ["newest", "mid", "old"]


def test_jobs_without_timestamps_keep_insertion_order_newest_first(env):
    c, *_ = env
    for name in ("first", "second", "third"):
        webapp.jobs[name] = {"status": "completed", "progress": 100, "video_name": name}
    assert _ids(c.get("/api/jobs")) == ["third", "second", "first"]


def test_progress_is_clamped_to_an_int_percentage(env):
    c, *_ = env
    webapp.jobs["a"] = {"status": "running", "progress": 250.7, "video_name": "a"}
    webapp.jobs["b"] = {"status": "running", "progress": "junk", "video_name": "b"}
    got = {j["id"]: j["progress"] for j in c.get("/api/jobs").get_json()}
    assert got == {"a": 100, "b": 0}


def test_updated_iso_string_is_understood(env):
    c, *_ = env
    webapp.jobs["x"] = {"status": "completed", "progress": 100, "video_name": "x",
                        "updated": "2026-01-01T00:00:00Z"}
    (job,) = c.get("/api/jobs").get_json()
    assert re.fullmatch(r"\d+(\.\d+)?", str(job["updated"]))


# ------------------------------------------------- hostile progress.json / messages

@pytest.mark.parametrize("raw_pct", ["Infinity", "-Infinity", "NaN", '"x"',
                                     '{"a": {"b": 1}}', "[1, 2]", "1e999", "null"])
def test_hostile_pct_does_not_fail_the_listing(env, raw_pct):
    c, _, outputs = env
    d = outputs / "hostile"
    d.mkdir()
    (d / "progress.json").write_text(
        '{"stage": "analyzing", "pct": %s, "updated": %r}' % (raw_pct, time.time()),
        encoding="utf-8")
    _progress(outputs, "healthy", updated=time.time() - 5, pct=70)
    webapp.jobs["live"] = {"status": "running", "progress": 1, "video_name": "live"}
    res = c.get("/api/jobs")
    assert res.status_code == 200
    got = {j["id"]: j["progress"] for j in res.get_json()}
    assert got["healthy"] == 70 and got["live"] == 1
    assert got["hostile"] in (0, 100)


def test_hostile_stage_and_updated_in_progress_file(env):
    c, _, outputs = env
    d = outputs / "weird"
    d.mkdir()
    (d / "progress.json").write_text(
        json.dumps({"stage": {"x": 1}, "pct": 5, "updated": time.time()}), encoding="utf-8")
    d2 = outputs / "junk_updated"
    d2.mkdir()
    (d2 / "progress.json").write_text(
        json.dumps({"stage": "analyzing", "pct": 5, "updated": {"x": 1}}), encoding="utf-8")
    res = c.get("/api/jobs")
    assert res.status_code == 200
    assert sorted(_ids(res)) == ["junk_updated", "weird"]


def test_infinite_in_memory_progress_does_not_fail_the_listing(env):
    c, *_ = env
    webapp.jobs["inf"] = {"status": "running", "progress": float("inf"), "video_name": "inf"}
    webapp.jobs["ok"] = {"status": "running", "progress": 3, "video_name": "ok"}
    res = c.get("/api/jobs")
    assert res.status_code == 200
    assert {j["id"]: j["progress"] for j in res.get_json()} == {"inf": 0, "ok": 3}


def test_one_failing_row_is_skipped_not_fatal(env, monkeypatch):
    c, *_ = env
    webapp.jobs["good"] = {"status": "running", "progress": 3, "video_name": "good"}
    webapp.jobs["bad"] = {"status": "running", "progress": 3, "video_name": "bad"}
    real = webapp.insights_routes._safe_message

    def picky(text, status):
        if text == "boom":
            raise RuntimeError("row exploded")
        return real(text, status)

    monkeypatch.setattr(webapp.insights_routes, "_safe_message", picky)
    webapp.jobs["bad"]["message"] = "boom"
    res = c.get("/api/jobs")
    assert res.status_code == 200
    assert _ids(res) == ["good"]


def test_error_message_keeps_only_the_exit_prefix(env):
    c, *_ = env
    webapp.jobs["e1"] = {"status": "error", "video_name": "e1",
                         "message": r"分析失败 (exit=3) at C:\Users\me\x.py"}
    webapp.jobs["e2"] = {"status": "error", "video_name": "e2",
                         "message": "Traceback /home/me/x.py"}
    got = {j["id"]: j["message"] for j in c.get("/api/jobs").get_json()}
    assert got["e1"] == "分析失败 (exit=3)"
    assert BILINGUAL.match(got["e2"]) and "/home" not in got["e2"]


def test_path_tokens_are_scrubbed_from_a_first_line(env):
    c, *_ = env
    webapp.jobs["p"] = {"status": "running", "video_name": "p",
                        "message": r"reading C:\Users\me\clip.mp4 and /home/me/clip.mp4 now"}
    (job,) = c.get("/api/jobs").get_json()
    assert "Users" not in job["message"] and "/home" not in job["message"]
    assert "<path>" in job["message"] and job["message"].endswith("now")


def test_detached_run_dated_in_the_future_is_not_listed(env):
    c, _, outputs = env
    _progress(outputs, "far_future", updated=time.time() + 365 * 24 * 3600 * 900)
    _progress(outputs, "skewed", updated=time.time() + 3600)
    _progress(outputs, "slight_skew", updated=time.time() + 10)
    assert _ids(c.get("/api/jobs")) == ["slight_skew"]


def test_future_mtime_without_updated_field_is_not_listed(env):
    c, _, outputs = env
    _progress(outputs, "future_file", mtime=time.time() + 3600)
    assert c.get("/api/jobs").get_json() == []
