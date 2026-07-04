import app as webapp


def _tree(tmp_path, monkeypatch, stem="vid1"):
    videos = tmp_path / "videos"
    outputs = tmp_path / "outputs"
    templates = tmp_path / "templates"
    for d in (videos, outputs, templates):
        d.mkdir()
    (videos / (stem + ".mp4")).write_bytes(b"v" * 10)
    out = outputs / stem
    out.mkdir()
    (out / "detections.jsonl").write_text("{}", encoding="utf-8")
    (out / "rally_segments.json").write_text("{}", encoding="utf-8")
    (out / "thumb.jpg").write_bytes(b"t")
    clips = out / "clips"
    clips.mkdir()
    (clips / "highlight_1.mp4").write_bytes(b"c" * 20)
    posture = out / "posture"
    posture.mkdir()
    (posture / "drill_summary.json").write_text("{}", encoding="utf-8")
    (posture / "coach_report_en.json").write_text("{}", encoding="utf-8")
    (posture / "coach_report_zh-Hans.html").write_text("x", encoding="utf-8")
    rep = posture / "rep_clips"
    rep.mkdir()
    (rep / "rep_1.mp4").write_bytes(b"r" * 30)
    (templates / ("_auto_" + stem + ".png")).write_bytes(b"p")
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "TEMPLATES", templates)
    return videos, outputs, templates


def test_safe_stem_accepts_existing(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    assert webapp._safe_stem("vid1") == "vid1"


def test_safe_stem_accepts_video_without_outputs(tmp_path, monkeypatch):
    videos, outputs, _ = _tree(tmp_path, monkeypatch)
    (videos / "fresh.mp4").write_bytes(b"x")
    assert webapp._safe_stem("fresh") == "fresh"


def test_safe_stem_rejects_malformed(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    for bad in ("", "..", "../vid1", "a/b", "a\\b", " vid1", "vid1 ", ".", "C:", "c:", None):
        assert webapp._safe_stem(bad) is None, bad


def test_safe_stem_rejects_unknown(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    assert webapp._safe_stem("ghost") is None


def test_delete_groups_match_keeps_posture_and_thumb(tmp_path, monkeypatch):
    _, outputs, _ = _tree(tmp_path, monkeypatch)
    groups = dict(webapp._delete_groups("vid1", "match"))
    names = {p.name for p in groups["match"]}
    assert "posture" not in names and "thumb.jpg" not in names
    assert {"detections.jsonl", "rally_segments.json", "clips"} <= names


def test_delete_groups_clips_and_reports(tmp_path, monkeypatch):
    _, outputs, _ = _tree(tmp_path, monkeypatch)
    clips = dict(webapp._delete_groups("vid1", "clips"))
    assert [p.name for p in clips["clips_rally"]] == ["clips"]
    assert [p.name for p in clips["clips_rep"]] == ["rep_clips"]
    reports = dict(webapp._delete_groups("vid1", "reports"))
    assert sorted(p.name for p in reports["reports"]) == [
        "coach_report_en.json", "coach_report_zh-Hans.html"]


def test_delete_groups_all_disjoint_and_complete(tmp_path, monkeypatch):
    videos, outputs, templates = _tree(tmp_path, monkeypatch)
    groups = dict(webapp._delete_groups("vid1", "all"))
    all_paths = [p for paths in groups.values() for p in paths]
    assert len(all_paths) == len(set(all_paths))
    covered = set()
    for p in all_paths:
        if p.is_dir():
            covered |= {f for f in p.rglob("*") if f.is_file()}
        else:
            covered.add(p)
    expected = {f for f in (outputs / "vid1").rglob("*") if f.is_file()}
    expected.add(videos / "vid1.mp4")
    expected.add(templates / "_auto_vid1.png")
    assert covered == expected


def test_delete_groups_all_excludes_sibling_stem_files(tmp_path, monkeypatch):
    videos, outputs, templates = _tree(tmp_path, monkeypatch)
    (videos / "vid10.mp4").write_bytes(b"x")
    (templates / "_auto_vid10.png").write_bytes(b"p")
    groups = dict(webapp._delete_groups("vid1", "all"))
    assert templates / "_auto_vid10.png" not in groups["source"]
    assert videos / "vid10.mp4" not in groups["source"]


def test_paths_stats_counts(tmp_path, monkeypatch):
    _, outputs, _ = _tree(tmp_path, monkeypatch)
    files, size_mb = webapp._paths_stats([outputs / "vid1" / "clips"])
    assert files == 1
    assert size_mb == 0.0


def test_running_job_for(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    monkeypatch.setattr(webapp, "jobs", {})
    assert webapp._running_job_for("vid1") is None
    monkeypatch.setitem(webapp.jobs, "vid1", {"status": "running", "video_name": "vid1.mp4"})
    assert webapp._running_job_for("vid1") == "vid1"
    webapp.jobs["vid1"]["status"] = "completed"
    assert webapp._running_job_for("vid1") is None
    monkeypatch.setitem(webapp.jobs, "posture_vid1", {"status": "running", "video_name": "vid1"})
    assert webapp._running_job_for("vid1") == "posture_vid1"
