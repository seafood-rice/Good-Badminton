import json
import pytest
import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path / "outputs")
    (tmp_path / "outputs").mkdir()
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), videos, tmp_path / "outputs"


def test_videos_reports_status_and_metadata(client, monkeypatch):
    c, videos, outputs = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    # No duration decode in tests: stub the helper.
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: 12.5)
    out = outputs / "clip"
    out.mkdir(parents=True)
    (out / "detections.jsonl").write_text("{}\n", encoding="utf-8")

    data = c.get("/api/videos").get_json()
    row = next(r for r in data if r["name"] == "clip")
    assert row["has_match"] is True
    assert row["has_posture"] is False
    assert row["status"] == "analyzed"
    assert row["duration_sec"] == 12.5
    assert "date" in row and len(row["date"]) == 10  # YYYY-MM-DD


def test_videos_status_new_and_court_set(client, monkeypatch):
    c, videos, outputs = client
    (videos / "a.mp4").write_bytes(b"\x00")
    (videos / "b.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    (outputs / "b").mkdir(parents=True)
    (outputs / "b" / "court_annotations.txt").write_text("x", encoding="utf-8")

    rows = {r["name"]: r for r in c.get("/api/videos").get_json()}
    assert rows["a"]["status"] == "new"
    assert rows["b"]["status"] == "court_set"


def test_ensure_thumbnail_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path / "outputs")

    # Stub the frame grab so no real video decode is needed.
    def fake_write(video_path, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\xff\xd8\xff")  # jpeg magic
        return True
    monkeypatch.setattr(webapp, "_write_first_frame", fake_write)

    out = webapp._ensure_thumbnail(tmp_path / "clip.mp4", "clip")
    assert out is not None and out.exists()


def test_videos_backfills_thumbnail(client, monkeypatch):
    c, videos, outputs = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    monkeypatch.setattr(webapp, "_write_first_frame",
                        lambda vp, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                          dest.write_bytes(b"\xff\xd8\xff"), True)[-1])
    row = next(r for r in c.get("/api/videos").get_json() if r["name"] == "clip")
    assert row["thumb"] == "/api/output/clip/thumb.jpg"


def test_thumb_url_is_percent_encoded_for_names_with_spaces(client, monkeypatch):
    """A URL with raw spaces is malformed and breaks the CSS url() that renders it.

    kestrel.js builds `background-image:url(<thumb>)` unquoted. An unquoted CSS
    url() cannot contain spaces, so the whole declaration is dropped by the parser
    and no thumbnail appears. Verified in a browser:

        url(/api/output/Dji 2026 0010 D/thumb.jpg)   -> DROPPED, no image
        url(/api/output/Dji%202026%200010%20D/...)   -> parses OK

    This surfaced as "H.265 DJI videos have no thumbnail", but the codec is
    incidental: those files were merely the only ones with spaces in their names.
    Thumbnail generation works fine for 10-bit HEVC.
    """
    c, videos, outputs = client
    (videos / "Dji 20260718 0010 D.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    monkeypatch.setattr(webapp, "_write_first_frame",
                        lambda vp, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                          dest.write_bytes(b"\xff\xd8\xff"), True)[-1])
    row = next(r for r in c.get("/api/videos").get_json()
               if r["name"] == "Dji 20260718 0010 D")
    assert " " not in row["thumb"], f"raw space in thumb URL: {row['thumb']!r}"
    assert row["thumb"] == "/api/output/Dji%2020260718%200010%20D/thumb.jpg"


def test_encoded_thumb_url_still_serves_the_file(client, monkeypatch):
    """Encoding must not break retrieval -- Flask decodes %20 back to a space."""
    c, videos, outputs = client
    (videos / "Dji 20260718 0010 D.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    monkeypatch.setattr(webapp, "_write_first_frame",
                        lambda vp, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                          dest.write_bytes(b"\xff\xd8\xff"), True)[-1])
    row = next(r for r in c.get("/api/videos").get_json()
               if r["name"] == "Dji 20260718 0010 D")
    resp = c.get(row["thumb"])
    assert resp.status_code == 200
    assert resp.data.startswith(b"\xff\xd8\xff")


def test_plain_names_keep_an_unescaped_thumb_url(client, monkeypatch):
    """Encoding must not churn URLs for the names that already worked."""
    c, videos, outputs = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    monkeypatch.setattr(webapp, "_write_first_frame",
                        lambda vp, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                          dest.write_bytes(b"\xff\xd8\xff"), True)[-1])
    row = next(r for r in c.get("/api/videos").get_json() if r["name"] == "clip")
    assert row["thumb"] == "/api/output/clip/thumb.jpg"


def test_stats_aggregates_outputs(client, monkeypatch):
    c, videos, outputs = client
    (videos / "m.mp4").write_bytes(b"\x00")
    (videos / "n.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    monkeypatch.setattr(webapp, "_write_first_frame", lambda vp, d: False)

    m = outputs / "m"
    m.mkdir(parents=True)
    (m / "detections.jsonl").write_text("{}\n", encoding="utf-8")
    (m / "rally_segments.json").write_text(
        json.dumps({"rallies": [{}, {}, {}]}), encoding="utf-8")
    (m / "technique_summary.json").write_text(
        json.dumps({"by_type": {"smash": {"avg_score": 70.0},
                                "clear": {"avg_score": 50.0}}}), encoding="utf-8")

    s = c.get("/api/stats").get_json()
    assert s["videos"] == 2
    assert s["analyzed"] == 1
    assert s["rallies"] == 3
    assert s["avg_technique_score"] == 60.0


def test_stats_empty(client, monkeypatch):
    c, videos, outputs = client
    s = c.get("/api/stats").get_json()
    assert s == {"videos": 0, "analyzed": 0, "rallies": 0, "avg_technique_score": None}


def test_models_false_when_weights_absent(client, tmp_path, monkeypatch):
    c, videos, outputs = client
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    assert c.get("/api/models").get_json() == {"racket": False, "quality": False, "bst": False,
                                                "tracknet": False, "lift": False}


def test_models_true_when_weights_installed(client, tmp_path, monkeypatch):
    c, videos, outputs = client
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    weights = tmp_path / "weights"
    weights.mkdir()
    (weights / "yolo11n-racket.pt").write_bytes(b"n")
    (weights / "quality-high_clear.pt").write_bytes(b"q")
    (weights / "bst-shuttleset.pt").write_bytes(b"b")
    (weights / "motionbert.pt").write_bytes(b"m")
    assert c.get("/api/models").get_json() == {"racket": True, "quality": True, "bst": True,
                                                "tracknet": False, "lift": True}
