import json
import os
from badminton_analysis.posture.system import PostureAnalysisSystem


def _summary():
    return {"stroke_type": "smash", "rep_count": 1, "mean_score": 65.0,
            "best_rep": {"rep_id": 1, "score": 65}, "worst_rep": {"rep_id": 1, "score": 65},
            "consistency": 0.0, "per_metric_avg": {"elbow_extension": 55.0},
            "recurring_weaknesses": [{"metric": "elbow_extension", "count": 1}],
            "strengths": []}


def _reports():
    return [{"rep_id": 1, "stroke_type": "smash", "overall_score": 65,
             "per_metric": {"elbow_extension": {"measured": 138, "score": 55,
                            "ideal_range": [150, 170], "direction": "under", "severity": "moderate"}},
             "weaknesses": [{"metric": "elbow_extension", "measured": 138, "ideal_range": [150, 170],
                             "direction": "under", "severity": "moderate", "description": "x"}],
             "strengths": []}]


def test_write_reports_produces_three_languages(tmp_path):
    vid = tmp_path / "clip.mp4"; vid.write_bytes(b"x")
    sys = PostureAnalysisSystem(str(vid), stroke_type="smash", output_dir=str(tmp_path / "out"))
    paths = sys._write_reports(_reports(), _summary(), date="2026-07-01")
    for lang in ("en", "zh-Hant", "zh-Hans"):
        j = os.path.join(sys.save_dir, "coach_report_" + lang + ".json")
        h = os.path.join(sys.save_dir, "coach_report_" + lang + ".html")
        assert os.path.exists(j), j
        assert os.path.exists(h), h
        data = json.load(open(j, encoding="utf-8"))
        assert data["lang"] == lang
        assert data["header"]["date"] == "2026-07-01"
    # returned mapping covers all three langs
    assert set(paths.keys()) == {"en", "zh-Hant", "zh-Hans"}


def test_write_reports_feature_space_all_2d(tmp_path):
    vid = tmp_path / "clip.mp4"; vid.write_bytes(b"x")
    sys = PostureAnalysisSystem(str(vid), stroke_type="smash", output_dir=str(tmp_path / "out"))
    reports = _reports()  # no feature_space key -> defaults to "2d" per-rep
    sys._write_reports(reports, _summary(), date="2026-07-01")
    data = json.load(open(os.path.join(sys.save_dir, "coach_report_en.json"), encoding="utf-8"))
    assert data["header"]["feature_space"] == "2d"


def test_write_reports_feature_space_all_3d(tmp_path):
    vid = tmp_path / "clip.mp4"; vid.write_bytes(b"x")
    sys = PostureAnalysisSystem(str(vid), stroke_type="smash", output_dir=str(tmp_path / "out"))
    reports = _reports()
    reports[0]["feature_space"] = "3d"
    sys._write_reports(reports, _summary(), date="2026-07-01")
    data = json.load(open(os.path.join(sys.save_dir, "coach_report_en.json"), encoding="utf-8"))
    assert data["header"]["feature_space"] == "3d"


def test_write_reports_feature_space_mixed_when_some_reps_lifted(tmp_path):
    vid = tmp_path / "clip.mp4"; vid.write_bytes(b"x")
    sys = PostureAnalysisSystem(str(vid), stroke_type="smash", output_dir=str(tmp_path / "out"))
    reports = _reports() + _reports()
    reports[0]["rep_id"] = 1
    reports[0]["feature_space"] = "3d"
    reports[1]["rep_id"] = 2
    reports[1]["feature_space"] = "2d"
    sys._write_reports(reports, _summary(), date="2026-07-01")
    data = json.load(open(os.path.join(sys.save_dir, "coach_report_en.json"), encoding="utf-8"))
    assert data["header"]["feature_space"] == "mixed"


def test_write_reps_3d_sidecar_writes_when_lifted(tmp_path):
    vid = tmp_path / "clip.mp4"; vid.write_bytes(b"x")
    sys = PostureAnalysisSystem(str(vid), stroke_type="smash", output_dir=str(tmp_path / "out"))
    os.makedirs(sys.save_dir, exist_ok=True)
    sys.lift_model_path = "weights/motionbert.pt"
    gate_info = {"reps_3d": [{"rep_id": 1, "frames": [1, 2, 3],
                              "keypoints_3d": __import__("numpy").zeros((3, 17, 3))}]}
    sys._write_reps_3d_sidecar(gate_info)
    path = os.path.join(sys.save_dir, "drill_reps_3d.npz")
    assert os.path.exists(path)


def test_write_reps_3d_sidecar_removes_stale_file_when_no_lifts(tmp_path):
    """A prior run in this save_dir left a 3D sidecar (weights were available
    then); this run produces no lifts (weights removed, or every rep failed
    to lift) -- the stale file must not survive, since its rep IDs/frames no
    longer correspond to this run's reports."""
    vid = tmp_path / "clip.mp4"; vid.write_bytes(b"x")
    sys = PostureAnalysisSystem(str(vid), stroke_type="smash", output_dir=str(tmp_path / "out"))
    os.makedirs(sys.save_dir, exist_ok=True)
    stale_path = os.path.join(sys.save_dir, "drill_reps_3d.npz")
    with open(stale_path, "wb") as f:
        f.write(b"stale-sidecar-from-a-prior-3d-run")
    assert os.path.exists(stale_path)

    sys._write_reps_3d_sidecar({"reps_3d": []})

    assert not os.path.exists(stale_path)


def test_write_reps_3d_sidecar_no_op_when_nothing_to_clean_up(tmp_path):
    """No 3D lifts and no pre-existing sidecar -> nothing written, no error."""
    vid = tmp_path / "clip.mp4"; vid.write_bytes(b"x")
    sys = PostureAnalysisSystem(str(vid), stroke_type="smash", output_dir=str(tmp_path / "out"))
    os.makedirs(sys.save_dir, exist_ok=True)
    sys._write_reps_3d_sidecar({"reps_3d": []})
    assert not os.path.exists(os.path.join(sys.save_dir, "drill_reps_3d.npz"))
