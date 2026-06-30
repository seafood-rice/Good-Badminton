# Posture Drill Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a court-free "Posture Drill" mode — single-player side-view clips of a chosen stroke are segmented into reps by swing motion, each rep scored by the existing biomechanical analyzer, producing per-rep reports, a drill aggregate, an annotated video, and a training plan — surfaced under its own Web UI mode switch.

**Architecture:** Approach A — a new `badminton_analysis/posture/` package with its own `PostureAnalysisSystem` that runs the EXISTING pose detector full-frame (no court gate/ROI/rally), a new swing-motion `segment_reps`, and reuses the already-tested `BiomechanicalAnalyzer` (Plan 1), `generate_plan` (Plan 2), `draw_technique_overlay`, and `infer_racket_head`. The working court-match pipeline is left untouched.

**Tech Stack:** Python 3.12 (venv), NumPy, OpenCV, Ultralytics YOLO pose, Flask, pytest.

## Global Constraints

- Python 3.8+ compatible syntax (no `match`, no `X | Y` annotation unions). NumPy `>=1.21.6,<2.0`. Flask `>=2.0,<4.0`.
- Use the venv interpreter EXACTLY: `.venv/Scripts/python.exe` (Windows; NOT bare `python`).
- The existing court-match pipeline (`badminton_analysis/system.py`, `main.py`, existing `app.py` routes, existing `web_ui.html` flow) MUST remain behavior-unchanged. Posture is purely additive.
- Stroke vocabulary EXACTLY: `high_clear`, `smash`, `drop_shot`, `serve`. Posture player side is the literal string `"single"`. Dominant hand default `"right"`, choices `right`/`left`.
- Posture outputs go in the subdir `outputs/<video>/posture/` (never collide with match outputs).
- Reuse, do NOT reimplement: `BiomechanicalAnalyzer` (`badminton_analysis/analysis/biomechanics.py`), `StrokeEvent` (`badminton_analysis/stroke/events.py`), `generate_plan` (`badminton_analysis/training/plan_generator.py`), `write_json`/`clean_value` (`badminton_analysis/data/writer.py`), `infer_racket_head` + COCO indices (`badminton_analysis/analysis/joint_angles.py`), `draw_technique_overlay` (`badminton_analysis/visualization/technique_overlay.py`), `YOLOPoseProcessor` (`badminton_analysis/detection/yolo_pose.py`), `setup_video_writer`/`process_video_without_audio` (`badminton_analysis/media/video_audio.py`).
- New Flask routes must use `try/except → jsonify({'error': str(e)}), 500` and structured 404 on missing files (the convention already in `app.py`).

## Reused interface signatures (verified — do not redefine these)
- `StrokeEvent(stroke_type:str, contact_frame:int, window_start:int, window_end:int, player_side:str, confidence:float)` with `.to_dict()`.
- `BiomechanicalAnalyzer(dominant="right").analyze(stroke_event, window_frames) -> report dict` with keys `stroke_type, contact_frame, player_side, confidence, overall_score, per_metric, weaknesses (list of {metric,measured,ideal_range,direction,severity,description}), strengths (list of metric-name strings)`. `window_frames` = list of `{"frame":int, "keypoints":(17,2)|None, "conf":(17,)|None, "racket_head":(x,y)|None, "centroid":(x,y)|None}`.
- `infer_racket_head(keypoints, dominant="right", extend=0.6) -> (x,y)|None`.
- `draw_technique_overlay(frame, angles, label=None, score=None, origin=(20,20)) -> frame`.
- `generate_plan(match_summary, library=None, weeks=4) -> dict` — consumes `match_summary["recurring_weaknesses"] = [{"metric","count"}]`.
- `write_json(path, payload)`, `clean_value(value)`.
- `YOLOPoseProcessor(model_path="yolo11n-pose.pt").process_frame(frame) -> (keypoints, scores)` where keypoints is `(N,17,2)` ndarray or None.
- COCO indices in `joint_angles`: `R_WRIST, L_WRIST, R_SHOULDER, L_SHOULDER, R_HIP, L_HIP`, plus `is_valid(kp, idx, conf=None, conf_thresh=0.3)`.

---

## File Structure

**New files:**
- `badminton_analysis/posture/__init__.py` — package marker
- `badminton_analysis/posture/rep_segmenter.py` — `RepWindow`, `segment_reps`
- `badminton_analysis/posture/writer.py` — `write_rep_reports`, `build_drill_summary`
- `badminton_analysis/posture/system.py` — `PostureRunner` (testable post-loop) + `PostureAnalysisSystem` (video-loop orchestrator)
- `main_posture.py` — CLI entry
- `tests/test_rep_segmenter.py`, `tests/test_drill_writer.py`, `tests/test_posture_runner.py`, `tests/test_app_posture_routes.py`

**Modified files:**
- `app.py` — add posture routes (additive)
- `web_ui.html` — add mode switch + posture panel (additive)
- `README.md` / `README_EN.md` — document posture mode

---

### Task 1: Rep segmenter

**Files:**
- Create: `badminton_analysis/posture/__init__.py`
- Create: `badminton_analysis/posture/rep_segmenter.py`
- Test: `tests/test_rep_segmenter.py`

**Interfaces:**
- Produces:
  - `RepWindow` dataclass: `rep_id:int, peak_frame:int, window_start:int, window_end:int, prominence:float`; `.to_dict()`.
  - `segment_reps(track, fps, min_gap_sec=0.8, pre=20, post=15, k=1.0, smooth=3, min_speed_px=5.0, max_reps=50) -> list[RepWindow]`. `track` = list of `{"frame":int, "wrist":(x,y)|None, "shuttle":(x,y)|None}` ordered by frame. Returns reps ordered by peak_frame, `rep_id` starting at 1.

- [ ] **Step 1: Write the failing tests**

`tests/test_rep_segmenter.py`:
```python
from badminton_analysis.posture.rep_segmenter import RepWindow, segment_reps


def _still_track(n, wrist=(100, 100)):
    return [{"frame": i, "wrist": wrist, "shuttle": None} for i in range(n)]


def _add_swing(track, center, jump=120):
    # A single large wrist displacement at `center` creates a clear speed spike.
    track[center] = {"frame": center, "wrist": (100 + jump, 100),
                     "shuttle": track[center].get("shuttle")}
    return track


def test_repwindow_to_dict():
    rw = RepWindow(rep_id=1, peak_frame=30, window_start=10, window_end=45, prominence=0.9)
    d = rw.to_dict()
    assert d["rep_id"] == 1 and d["peak_frame"] == 30
    assert d["window_start"] == 10 and d["window_end"] == 45


def test_two_swings_far_apart_give_two_reps():
    track = _still_track(120)
    _add_swing(track, 30)
    _add_swing(track, 90)
    reps = segment_reps(track, fps=30)
    assert len(reps) == 2
    peaks = sorted(r.peak_frame for r in reps)
    assert abs(peaks[0] - 30) <= 2 and abs(peaks[1] - 90) <= 2
    assert reps[0].rep_id == 1 and reps[1].rep_id == 2


def test_still_track_gives_no_reps():
    assert segment_reps(_still_track(120), fps=30) == []


def test_close_swings_collapse_to_one():
    track = _still_track(120)
    _add_swing(track, 50, jump=120)
    _add_swing(track, 55, jump=200)  # within 0.8s (24 frames) -> keep the bigger (55)
    reps = segment_reps(track, fps=30)
    assert len(reps) == 1
    assert abs(reps[0].peak_frame - 55) <= 2


def test_window_clamps_at_zero():
    track = _still_track(60)
    _add_swing(track, 5)
    reps = segment_reps(track, fps=30, pre=20, post=15)
    assert reps[0].window_start == 0


def test_shuttle_refines_peak_frame():
    track = _still_track(120)
    _add_swing(track, 40, jump=120)
    # Put the shuttle nearest the wrist at frame 42 (within the window) -> snap there.
    for i in range(120):
        track[i]["shuttle"] = (5000, 5000)
    track[42]["shuttle"] = track[42]["wrist"]
    reps = segment_reps(track, fps=30)
    assert len(reps) == 1
    assert reps[0].peak_frame == 42


def test_empty_and_all_none_wrist():
    assert segment_reps([], fps=30) == []
    none_track = [{"frame": i, "wrist": None, "shuttle": None} for i in range(50)]
    assert segment_reps(none_track, fps=30) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rep_segmenter.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/posture/__init__.py`: (empty file)

`badminton_analysis/posture/rep_segmenter.py`:
```python
"""Segment a single-player drill clip into individual stroke reps from wrist swing motion."""
from dataclasses import dataclass, asdict

import numpy as np


@dataclass
class RepWindow:
    rep_id: int
    peak_frame: int
    window_start: int
    window_end: int
    prominence: float

    def to_dict(self):
        return asdict(self)


def _dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _wrist_speed(track):
    """Per-position wrist speed (px/frame); 0 where either endpoint is missing."""
    speed = [0.0] * len(track)
    for i in range(1, len(track)):
        a = track[i - 1].get("wrist")
        b = track[i].get("wrist")
        if a is not None and b is not None:
            speed[i] = _dist(a, b)
    return speed


def _smooth(values, window):
    if window <= 1 or len(values) == 0:
        return list(values)
    out = []
    half = window // 2
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def segment_reps(track, fps, min_gap_sec=0.8, pre=20, post=15, k=1.0,
                 smooth=3, min_speed_px=5.0, max_reps=50):
    if not track:
        return []

    speed = _smooth(_wrist_speed(track), smooth)
    arr = np.asarray(speed, dtype=float)
    if arr.size == 0 or float(np.max(arr)) <= 0.0:
        return []

    threshold = float(np.mean(arr) + k * np.std(arr))
    floor = max(threshold, min_speed_px)

    # Candidate local maxima above the floor.
    candidates = []
    for i in range(len(arr)):
        v = arr[i]
        if v < floor:
            continue
        left_ok = i == 0 or arr[i - 1] <= v
        right_ok = i == len(arr) - 1 or arr[i + 1] <= v
        if left_ok and right_ok:
            candidates.append(i)

    if not candidates:
        return []

    # Enforce a minimum gap: keep the higher-speed peak within each gap window.
    min_gap = max(1, int(min_gap_sec * fps))
    candidates.sort(key=lambda idx: arr[idx], reverse=True)
    kept = []
    for idx in candidates:
        frame_idx = track[idx]["frame"]
        if all(abs(frame_idx - track[j]["frame"]) >= min_gap for j in kept):
            kept.append(idx)
    kept.sort(key=lambda idx: track[idx]["frame"])

    scale = fps / 30.0
    pre_f = int(round(pre * scale))
    post_f = int(round(post * scale))
    max_speed = float(np.max(arr)) or 1.0

    reps = []
    for rep_id, idx in enumerate(kept[:max_reps], start=1):
        peak_frame = track[idx]["frame"]
        window_start = max(0, peak_frame - pre_f)
        window_end = peak_frame + post_f

        # Shuttle refinement: snap to the in-window frame where shuttle is closest to wrist.
        best_pos, best_d = None, None
        for p in range(len(track)):
            f = track[p]["frame"]
            if f < window_start or f > window_end:
                continue
            sh = track[p].get("shuttle")
            wr = track[p].get("wrist")
            if sh is not None and wr is not None:
                d = _dist(sh, wr)
                if best_d is None or d < best_d:
                    best_d = d
                    best_pos = p
        if best_pos is not None:
            peak_frame = track[best_pos]["frame"]

        reps.append(RepWindow(
            rep_id=rep_id,
            peak_frame=peak_frame,
            window_start=max(0, peak_frame - pre_f),
            window_end=peak_frame + post_f,
            prominence=round(float(arr[idx]) / max_speed, 3),
        ))

    return reps
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rep_segmenter.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Run full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q` (expected: all prior tests still pass + 7 new).
```bash
git add badminton_analysis/posture/__init__.py badminton_analysis/posture/rep_segmenter.py tests/test_rep_segmenter.py
git commit -m "feat: swing-motion rep segmenter for posture drills"
```

---

### Task 2: Drill report writer + summary

**Files:**
- Create: `badminton_analysis/posture/writer.py`
- Test: `tests/test_drill_writer.py`

**Interfaces:**
- Consumes: `clean_value` from `badminton_analysis/data/writer.py`.
- Produces:
  - `write_rep_reports(path, reports) -> None` — one JSON object per line (JSONL); creates parent dirs.
  - `build_drill_summary(reports, stroke_type) -> dict` with keys: `stroke_type, rep_count, mean_score (float|None), best_rep ({rep_id,score}|None), worst_rep ({rep_id,score}|None), consistency (float|None, stddev of overall scores), per_metric_avg ({metric: avg|None}), recurring_weaknesses ([{metric,count}] desc), strengths ([{metric,count}] desc)`. Each report is expected to carry `rep_id`, `overall_score`, `per_metric` ({metric:{score}}), `weaknesses` (list of {metric}), `strengths` (list of metric strings).

- [ ] **Step 1: Write the failing tests**

`tests/test_drill_writer.py`:
```python
import json
from badminton_analysis.posture.writer import write_rep_reports, build_drill_summary


def _rep(rep_id, score, weak=None, strong=None, per_metric=None):
    return {
        "rep_id": rep_id, "stroke_type": "high_clear", "player_side": "single",
        "overall_score": score,
        "per_metric": per_metric or {},
        "weaknesses": [{"metric": m} for m in (weak or [])],
        "strengths": strong or [],
    }


def test_write_rep_reports_one_per_line(tmp_path):
    out = tmp_path / "posture" / "drill_reps.jsonl"
    write_rep_reports(str(out), [_rep(1, 80), _rep(2, 70)])
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["rep_id"] == 1


def test_summary_aggregates_scores_and_consistency():
    reports = [_rep(1, 80, weak=["elbow_extension"]),
               _rep(2, 60, weak=["elbow_extension", "knee_flexion"]),
               _rep(3, 70, weak=[])]
    s = build_drill_summary(reports, "high_clear")
    assert s["stroke_type"] == "high_clear"
    assert s["rep_count"] == 3
    assert s["mean_score"] == 70.0
    assert s["best_rep"] == {"rep_id": 1, "score": 80}
    assert s["worst_rep"] == {"rep_id": 2, "score": 60}
    assert s["consistency"] is not None and s["consistency"] > 0
    assert s["recurring_weaknesses"][0] == {"metric": "elbow_extension", "count": 2}


def test_summary_skips_none_scores_and_counts_strengths():
    reports = [_rep(1, None, strong=["wrist_flexion"]),
               _rep(2, 90, strong=["wrist_flexion"])]
    s = build_drill_summary(reports, "smash")
    assert s["mean_score"] == 90.0          # None skipped
    assert s["strengths"][0] == {"metric": "wrist_flexion", "count": 2}


def test_summary_empty():
    s = build_drill_summary([], "serve")
    assert s["rep_count"] == 0
    assert s["mean_score"] is None
    assert s["best_rep"] is None and s["worst_rep"] is None
    assert s["consistency"] is None
    assert s["recurring_weaknesses"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_drill_writer.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/posture/writer.py`:
```python
"""Write per-rep drill reports (JSONL) and build the drill-level summary."""
import json
import os
from collections import Counter, defaultdict

from ..data.writer import clean_value


def write_rep_reports(path, reports):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for report in reports:
            f.write(json.dumps(clean_value(report), ensure_ascii=False, separators=(",", ":")))
            f.write("\n")


def _stddev(values):
    n = len(values)
    if n < 2:
        return 0.0 if n == 1 else None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return var ** 0.5


def build_drill_summary(reports, stroke_type):
    scored = [r for r in reports if r.get("overall_score") is not None]
    scores = [r["overall_score"] for r in scored]

    mean_score = round(sum(scores) / len(scores), 1) if scores else None
    consistency = round(_stddev(scores), 2) if scores else None

    best_rep = worst_rep = None
    if scored:
        best = max(scored, key=lambda r: r["overall_score"])
        worst = min(scored, key=lambda r: r["overall_score"])
        best_rep = {"rep_id": best["rep_id"], "score": best["overall_score"]}
        worst_rep = {"rep_id": worst["rep_id"], "score": worst["overall_score"]}

    metric_scores = defaultdict(list)
    weakness_counter = Counter()
    strength_counter = Counter()
    for r in reports:
        for metric, m in (r.get("per_metric") or {}).items():
            if m.get("score") is not None:
                metric_scores[metric].append(m["score"])
        for w in r.get("weaknesses", []):
            weakness_counter[w["metric"]] += 1
        for s in r.get("strengths", []):
            strength_counter[s] += 1

    per_metric_avg = {m: round(sum(v) / len(v), 1) if v else None
                      for m, v in metric_scores.items()}

    def _ranked(counter):
        return [{"metric": m, "count": c} for m, c in counter.most_common()]

    return {
        "stroke_type": stroke_type,
        "rep_count": len(reports),
        "mean_score": mean_score,
        "best_rep": best_rep,
        "worst_rep": worst_rep,
        "consistency": consistency,
        "per_metric_avg": per_metric_avg,
        "recurring_weaknesses": _ranked(weakness_counter),
        "strengths": _ranked(strength_counter),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_drill_writer.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add badminton_analysis/posture/writer.py tests/test_drill_writer.py
git commit -m "feat: drill report writer and aggregate summary"
```

---

### Task 3: PostureRunner + PostureAnalysisSystem

**Files:**
- Create: `badminton_analysis/posture/system.py`
- Test: `tests/test_posture_runner.py`

**Interfaces:**
- Consumes: `segment_reps`/`RepWindow` (Task 1); `write_rep_reports`/`build_drill_summary` (Task 2); `StrokeEvent` + `BiomechanicalAnalyzer` + `infer_racket_head` + `draw_technique_overlay` + `YOLOPoseProcessor` + `setup_video_writer`/`process_video_without_audio` + `write_json` (existing).
- Produces:
  - `PostureRunner(analyzer, stroke_type, dominant="right", window_pre=20, window_post=15)` with `run(track, frame_lookup, fps) -> (reports, reps)`. `track` = rep-segmenter track shape; `frame_lookup(frame_index) -> dict|None` returns `{frame, keypoints, conf, racket_head, centroid}`. Each report is the analyzer report dict PLUS `rep_id`.
  - `PostureAnalysisSystem(video_path, stroke_type, dominant_hand="right", output_dir=None, ball_model_path=None, show_display=False, show_overlay=True)` with `process_video()`. Writes `outputs/<video>/posture/` (or `<output_dir>`): `drill_reps.jsonl`, `drill_summary.json`, `metadata.json`, and `detect_<video>.mp4`.

This task has two deliverables sharing one test cycle: the fully unit-tested `PostureRunner` (fakes, no video) and the `PostureAnalysisSystem` video-loop wiring (verified by the CLI smoke in Task 4).

- [ ] **Step 1: Write the failing test for the runner**

`tests/test_posture_runner.py`:
```python
import numpy as np
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.posture.system import PostureRunner


def _smash_kp():
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, 122)
    kp[ja.L_SHOULDER] = (60, 90)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def _track_with_two_swings(n=160):
    track = []
    for i in range(n):
        wrist = (100, 100)
        if i == 40 or i == 110:
            wrist = (260, 100)  # big displacement -> speed spike
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
    return track


def test_runner_one_report_per_rep_with_rep_id():
    kp = _smash_kp()
    racket = ja.infer_racket_head(kp, dominant="right")

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": racket, "centroid": (100 + (idx % 5), 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="high_clear", dominant="right")
    reports, reps = runner.run(_track_with_two_swings(), frame_lookup, fps=30)
    assert len(reps) == 2
    assert len(reports) == 2
    assert reports[0]["rep_id"] == 1 and reports[1]["rep_id"] == 2
    assert reports[0]["stroke_type"] == "high_clear"
    assert reports[0]["player_side"] == "single"
    assert "overall_score" in reports[0]


def test_runner_empty_track_no_reports():
    runner = PostureRunner(BiomechanicalAnalyzer(), stroke_type="smash")
    reports, reps = runner.run([], lambda i: None, fps=30)
    assert reports == [] and reps == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posture_runner.py -v`
Expected: FAIL with `ImportError`/`ModuleNotFoundError`.

- [ ] **Step 3: Implement system.py (runner first, then the system)**

`badminton_analysis/posture/system.py`:
```python
"""Court-free single-player posture/technique drill analysis."""
import os
import time

from .rep_segmenter import segment_reps
from .writer import write_rep_reports, build_drill_summary


class PostureRunner:
    """Post-loop orchestration: reps -> per-rep StrokeEvent -> biomechanical reports."""

    def __init__(self, analyzer, stroke_type, dominant="right",
                 window_pre=20, window_post=15):
        self.analyzer = analyzer
        self.stroke_type = stroke_type
        self.dominant = dominant
        self.window_pre = window_pre
        self.window_post = window_post

    def _window_frames(self, window_start, window_end, frame_lookup):
        frames = []
        for idx in range(window_start, window_end + 1):
            rec = frame_lookup(idx)
            if rec is None:
                continue
            frames.append({
                "frame": idx,
                "keypoints": rec.get("keypoints"),
                "conf": rec.get("conf"),
                "racket_head": rec.get("racket_head"),
                "centroid": rec.get("centroid"),
            })
        return frames

    def run(self, track, frame_lookup, fps):
        from ..stroke.events import StrokeEvent
        reps = segment_reps(track, fps, pre=self.window_pre, post=self.window_post)
        reports = []
        for rep in reps:
            event = StrokeEvent(
                stroke_type=self.stroke_type,
                contact_frame=rep.peak_frame,
                window_start=rep.window_start,
                window_end=rep.window_end,
                player_side="single",
                confidence=rep.prominence,
            )
            window_frames = self._window_frames(rep.window_start, rep.window_end, frame_lookup)
            report = self.analyzer.analyze(event, window_frames)
            report["rep_id"] = rep.rep_id
            reports.append(report)
        return reports, reps


class PostureAnalysisSystem:
    """Run the full court-free pipeline over a video file."""

    def __init__(self, video_path, stroke_type, dominant_hand="right",
                 output_dir=None, ball_model_path=None, show_display=False,
                 show_overlay=True, pose_model="weights/yolo11n-pose.pt"):
        if not os.path.exists(video_path):
            raise FileNotFoundError("Input video not found: " + video_path)
        self.video_path = video_path
        self.stroke_type = stroke_type
        self.dominant_hand = dominant_hand
        self.show_display = show_display
        self.show_overlay = show_overlay
        self.ball_model_path = ball_model_path
        self.pose_model = pose_model

        self.video_name = os.path.basename(video_path).rsplit(".", 1)[0]
        self.save_dir = output_dir or os.path.join("outputs", self.video_name, "posture")
        os.makedirs(self.save_dir, exist_ok=True)
        self.output_video_path = os.path.join(self.save_dir, "detect_" + self.video_name + ".mp4")

        self._track = []
        self._frames = {}

    def process_video(self):
        import cv2
        from ..detection.yolo_pose import YOLOPoseProcessor
        from ..analysis import joint_angles as ja
        from ..analysis.biomechanics import BiomechanicalAnalyzer
        from ..visualization.technique_overlay import draw_technique_overlay
        from ..media import video_audio as vap
        from ..data.writer import write_json

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError("Unable to open video: " + self.video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        pose = YOLOPoseProcessor(model_path=self.pose_model)
        ball_model = None
        if self.ball_model_path and os.path.exists(self.ball_model_path):
            from ultralytics import YOLO
            ball_model = YOLO(self.ball_model_path)

        temp_path = os.path.join(self.save_dir, "temp_detect_" + self.video_name + ".mp4")
        writer = vap.setup_video_writer(width, height, fps, temp_path)

        dom_wrist = ja.R_WRIST if self.dominant_hand == "right" else ja.L_WRIST
        frame_count = 0
        start = time.time()
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frame_count += 1
            self._capture_frame(frame, frame_count, pose, ball_model, dom_wrist,
                                ja, draw_technique_overlay)
            writer.write(frame)
            if self.show_display:
                cv2.imshow("posture", frame)
                cv2.waitKey(1)

        writer.release()
        time.sleep(0.5)
        cap.release()
        if self.show_display:
            cv2.destroyAllWindows()
        vap.process_video_without_audio(temp_path, self.output_video_path)

        runner = PostureRunner(BiomechanicalAnalyzer(dominant=self.dominant_hand),
                               stroke_type=self.stroke_type, dominant=self.dominant_hand)
        reports, reps = runner.run(self._track, self._frames.get, fps)

        write_rep_reports(os.path.join(self.save_dir, "drill_reps.jsonl"), reports)
        write_json(os.path.join(self.save_dir, "drill_summary.json"),
                   build_drill_summary(reports, self.stroke_type))
        write_json(os.path.join(self.save_dir, "metadata.json"), {
            "video": {"path": self.video_path, "name": self.video_name,
                      "fps": float(fps), "width": width, "height": height},
            "mode": "posture", "stroke_type": self.stroke_type,
            "dominant_hand": self.dominant_hand,
        })
        print("Posture analysis: " + str(len(reports)) + " reps -> " + self.save_dir)
        print("Elapsed: " + str(round(time.time() - start, 1)) + "s")
        return reports

    def _capture_frame(self, frame, frame_count, pose, ball_model, dom_wrist,
                       ja, draw_technique_overlay):
        keypoints, scores = pose.process_frame(frame)
        kp = None
        wrist = None
        racket_head = None
        centroid = None
        conf_row = None
        if keypoints is not None and len(keypoints) > 0:
            # Single-player drill: take the largest-bbox person (max keypoint spread).
            def _spread(person):
                xs = person[:, 0]
                ys = person[:, 1]
                return float((xs.max() - xs.min()) + (ys.max() - ys.min()))
            best_i = max(range(len(keypoints)), key=lambda i: _spread(keypoints[i]))
            kp = keypoints[best_i].astype(float)
            conf_row = scores[best_i] if scores is not None else None
            if ja.is_valid(kp, dom_wrist, conf_row):
                wrist = (float(kp[dom_wrist][0]), float(kp[dom_wrist][1]))
            racket_head = ja.infer_racket_head(kp, dominant=self.dominant_hand)
            # centroid = hip midpoint when available, else mean of valid points
            if ja.is_valid(kp, ja.L_HIP, conf_row) and ja.is_valid(kp, ja.R_HIP, conf_row):
                centroid = (float((kp[ja.L_HIP][0] + kp[ja.R_HIP][0]) / 2),
                            float((kp[ja.L_HIP][1] + kp[ja.R_HIP][1]) / 2))
            if self.show_overlay:
                angles = ja.compute_joint_angles(kp, racket_head=racket_head,
                                                 dominant=self.dominant_hand, conf=conf_row)
                draw_technique_overlay(frame, angles)

        shuttle = None
        if ball_model is not None:
            try:
                res = ball_model(frame, conf=0.18, verbose=False)[0]
                boxes = getattr(res, "boxes", None)
                if boxes is not None and boxes.xywh.shape[0] > 0:
                    b = boxes.xywh.detach().cpu().numpy()[0]
                    shuttle = (float(b[0]), float(b[1]))
            except Exception:
                shuttle = None

        self._track.append({"frame": frame_count, "wrist": wrist, "shuttle": shuttle})
        self._frames[frame_count] = {
            "frame": frame_count, "keypoints": kp, "conf": conf_row,
            "racket_head": racket_head, "centroid": centroid,
        }
```

- [ ] **Step 4: Run the runner test to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posture_runner.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add badminton_analysis/posture/system.py tests/test_posture_runner.py
git commit -m "feat: posture analysis system and testable rep runner"
```

---

### Task 4: CLI entry (main_posture.py)

**Files:**
- Create: `main_posture.py`

**Interfaces:**
- Consumes: `PostureAnalysisSystem` (Task 3).
- Produces: a CLI: `python main_posture.py --video-path X --stroke-type high_clear [--dominant-hand right] [--output-dir D] [--ball-model W] [--display false]`.

This is a thin wiring task; its acceptance is a working `--help` and an import-clean entry. (Real-video execution is a manual smoke, not part of CI.)

- [ ] **Step 1: Implement the CLI**

`main_posture.py`:
```python
#!/usr/bin/env python3
"""Posture Drill analysis CLI (court-free, single player)."""
import argparse


def main():
    parser = argparse.ArgumentParser(description="Badminton posture/technique drill analysis")
    parser.add_argument("--video-path", required=True, help="Input drill video path")
    parser.add_argument("--stroke-type", required=True,
                        choices=["high_clear", "smash", "drop_shot", "serve"],
                        help="Stroke being practiced")
    parser.add_argument("--dominant-hand", default="right", choices=["right", "left"])
    parser.add_argument("--output-dir", default=None,
                        help="Output dir (default outputs/<video>/posture)")
    parser.add_argument("--ball-model", default=None,
                        help="Optional YOLO shuttlecock model for rep refinement")
    parser.add_argument("--pose-model", default="weights/yolo11n-pose.pt")
    parser.add_argument("--display", choices=["true", "false"], default="false")
    args = parser.parse_args()

    from badminton_analysis.posture.system import PostureAnalysisSystem
    system = PostureAnalysisSystem(
        video_path=args.video_path,
        stroke_type=args.stroke_type,
        dominant_hand=args.dominant_hand,
        output_dir=args.output_dir,
        ball_model_path=args.ball_model,
        show_display=args.display == "true",
        pose_model=args.pose_model,
    )
    system.process_video()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify --help works (argparse runs before heavy imports)**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe main_posture.py --help`
Expected: exit 0; help lists `--video-path`, `--stroke-type` (with the 4 choices), `--dominant-hand`.

- [ ] **Step 3: Verify it errors cleanly without required args**

Run: `.venv/Scripts/python.exe main_posture.py 2>&1 | head -3`
Expected: argparse error mentioning the required `--video-path`/`--stroke-type` (exit code 2).

- [ ] **Step 4: Commit**

```bash
git add main_posture.py
git commit -m "feat: main_posture CLI entry"
```

---

### Task 5: Flask posture routes

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_posture_routes.py`

**Interfaces:**
- Consumes: `generate_plan` and `write_json` (already imported in app.py from Plan 2); reads `outputs/<video>/posture/drill_summary.json` + `drill_reps.jsonl`.
- Produces (new routes):
  - `GET /api/posture/<video_name>` → `{"summary": <drill_summary.json>, "reps": [<rep report>...]}`; 404 if no summary; 500 on malformed JSON.
  - `GET /api/posture-plan/<video_name>` → existing `posture/training_plan.json` if present, else generate from `drill_summary.json` via `generate_plan`, write it, return it; 404 if no summary; 500 on error.
  - `POST /api/posture-plan/<video_name>` with optional `{"weeks": int}` → regenerate (force), overwrite, return; 400 on non-int weeks.
  (The analyze-subprocess route + UI wiring is Task 6; these read/serve routes are testable now with the test client.)

- [ ] **Step 1: Write the failing tests**

`tests/test_app_posture_routes.py`:
```python
import json
import pytest

import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def _write_drill(tmp_path, video):
    out = tmp_path / video / "posture"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "stroke_type": "high_clear", "rep_count": 2, "mean_score": 70.0,
        "best_rep": {"rep_id": 1, "score": 80}, "worst_rep": {"rep_id": 2, "score": 60},
        "consistency": 10.0, "per_metric_avg": {},
        "recurring_weaknesses": [{"metric": "elbow_extension", "count": 2}], "strengths": [],
    }
    (out / "drill_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with open(out / "drill_reps.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"rep_id": 1, "stroke_type": "high_clear",
                            "overall_score": 80, "weaknesses": [], "strengths": []}) + "\n")
    return out


def test_posture_404_when_missing(client):
    assert client.get("/api/posture/nope").status_code == 404


def test_posture_returns_summary_and_reps(client, tmp_path):
    _write_drill(tmp_path, "drill1")
    r = client.get("/api/posture/drill1")
    assert r.status_code == 200
    data = r.get_json()
    assert data["summary"]["rep_count"] == 2
    assert len(data["reps"]) == 1


def test_posture_500_on_malformed_summary(client, tmp_path):
    out = tmp_path / "drillbad" / "posture"
    out.mkdir(parents=True, exist_ok=True)
    (out / "drill_summary.json").write_text("{ not json", encoding="utf-8")
    r = client.get("/api/posture/drillbad")
    assert r.status_code == 500
    assert "error" in r.get_json()


def test_posture_plan_generates_and_persists(client, tmp_path):
    out = _write_drill(tmp_path, "drill1")
    r = client.get("/api/posture-plan/drill1")
    assert r.status_code == 200
    assert "weeks" in r.get_json()
    assert (out / "training_plan.json").exists()


def test_posture_plan_404_without_summary(client):
    assert client.get("/api/posture-plan/missing").status_code == 404


def test_posture_plan_post_rejects_bad_weeks(client, tmp_path):
    _write_drill(tmp_path, "drill1")
    r = client.post("/api/posture-plan/drill1", json={"weeks": "nope"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_posture_routes.py -v`
Expected: FAIL (routes 404/HTML; assertions fail).

- [ ] **Step 3: Implement the routes in app.py**

Read `app.py` and add these routes immediately before the `# Static page` section (next to the existing `/api/technique` and `/api/training-plan` routes; `generate_plan`, `write_json`, `request`, `jsonify`, `json` are already imported there from Plan 2):
```python
@app.route('/api/posture/<video_name>')
def api_posture(video_name):
    """Return drill summary + per-rep reports for a posture-drill video."""
    out_dir = OUTPUTS / video_name / 'posture'
    summary_path = out_dir / 'drill_summary.json'
    if not summary_path.exists():
        return jsonify({'error': '还没有姿态训练分析数据，请先运行姿态分析'}), 404
    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        reps = []
        reps_path = out_dir / 'drill_reps.jsonl'
        if reps_path.exists():
            with open(reps_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        reps.append(json.loads(line))
        return jsonify({'summary': summary, 'reps': reps})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _load_or_make_posture_plan(video_name, weeks=4, force=False):
    out_dir = OUTPUTS / video_name / 'posture'
    plan_path = out_dir / 'training_plan.json'
    summary_path = out_dir / 'drill_summary.json'
    if plan_path.exists() and not force:
        try:
            with open(plan_path, encoding='utf-8') as f:
                return json.load(f), 200
        except Exception as e:
            return {'error': str(e)}, 500
    if not summary_path.exists():
        return {'error': '没有姿态分析数据，无法生成训练计划'}, 404
    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        plan = generate_plan(summary, weeks=weeks)
        write_json(str(plan_path), plan)
        return plan, 200
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/api/posture-plan/<video_name>', methods=['GET'])
def api_posture_plan(video_name):
    plan, status = _load_or_make_posture_plan(video_name)
    return jsonify(plan), status


@app.route('/api/posture-plan/<video_name>', methods=['POST'])
def api_posture_plan_regenerate(video_name):
    data = request.json or {}
    try:
        weeks = int(data.get('weeks', 4))
    except (TypeError, ValueError):
        return jsonify({'error': 'weeks 必须是整数'}), 400
    plan, status = _load_or_make_posture_plan(video_name, weeks=weeks, force=True)
    return jsonify(plan), status
```

Note: `generate_plan` consumes `summary["recurring_weaknesses"] = [{"metric","count"}]`, which `build_drill_summary` (Task 2) produces — so the drill summary feeds the plan generator unchanged.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_posture_routes.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Run full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add app.py tests/test_app_posture_routes.py
git commit -m "feat: posture drill API routes (read + training plan)"
```

---

### Task 6: Web UI mode switch + posture panel + analyze route

**Files:**
- Modify: `app.py` (add the analyze-subprocess route + status)
- Modify: `web_ui.html` (mode switch + posture panel + JS)
- Test: manual (front-end) + a served-HTML assertion via the test client

**Interfaces:**
- Consumes: `/api/posture/<video>`, `/api/posture-plan/<video>` (Task 5), and the new analyze/status routes below; `main_posture.py` (Task 4).
- Produces: a top-level mode switch (🏟 Match / 🧍 Posture Drill) and a posture panel; new routes `POST /api/posture/analyze`, `GET /api/posture-analyze-status/<job_id>`.

This is one task: the analyze route and the UI that drives it share a manual review.

- [ ] **Step 1: Add the analyze + status routes to app.py**

Read how the existing `/api/analyze` + `track_progress` + `/api/status` work (the job dict, subprocess.Popen, the H.264 re-encode block), then add a posture analogue before the `# Static page` section. It launches `main_posture.py` (NOT `main.py`), tracks progress by counting frames is not available, so track by process liveness + a simple running/!done state:
```python
@app.route('/api/posture/analyze', methods=['POST'])
def api_posture_analyze():
    data = request.json or {}
    video_name = data.get('video')
    stroke_type = data.get('stroke_type')
    dominant = data.get('dominant_hand', 'right')
    if not video_name or stroke_type not in ('high_clear', 'smash', 'drop_shot', 'serve'):
        return jsonify({'error': '需要 video 和有效的 stroke_type'}), 400

    video_path = VIDEOS / video_name
    if not video_path.exists():
        return jsonify({'error': '视频不存在'}), 404

    save_dir = OUTPUTS / video_path.stem / 'posture'
    save_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        _venv_python, str(PROJECT_ROOT / 'main_posture.py'),
        '--video-path', str(video_path),
        '--stroke-type', stroke_type,
        '--dominant-hand', dominant,
        '--output-dir', str(save_dir),
        '--display', 'false',
    ]
    job_id = 'posture_' + video_path.stem
    jobs[job_id] = {'status': 'running', 'progress': 0, 'message': '姿态分析中...',
                    'video_name': video_path.stem, 'save_dir': str(save_dir)}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=str(PROJECT_ROOT), env=os.environ.copy())
    jobs[job_id]['proc'] = proc

    import threading

    def track():
        job = jobs[job_id]
        proc.wait()
        if proc.returncode == 0:
            vs = video_path.stem
            sd = str(save_dir)
            raw = os.path.join(sd, 'detect_' + vs + '.mp4')
            h264 = os.path.join(sd, 'detect_' + vs + '_h264.mp4')
            ffmpeg_bin = shutil.which('ffmpeg') or 'ffmpeg'
            try:
                subprocess.run([ffmpeg_bin, '-y', '-i', raw, '-c:v', 'libx264',
                                '-preset', 'fast', '-crf', '23', '-movflags', '+faststart', h264],
                               check=True, capture_output=True, timeout=600)
                if os.path.exists(h264) and os.path.getsize(h264) > 0:
                    os.replace(h264, raw)
            except Exception as e:
                print('posture ffmpeg re-encode failed: ' + str(e))
            job['status'] = 'completed'
            job['progress'] = 100
            job['message'] = '完成!'
            job['result'] = {'video_name': vs,
                             'output_video': '/api/output/' + vs + '/posture/detect_' + vs + '.mp4'}
        else:
            job['status'] = 'error'
            job['message'] = '姿态分析失败 (exit=' + str(proc.returncode) + ')'

    threading.Thread(target=track, daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/posture-analyze-status/<job_id>')
def api_posture_analyze_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'status': 'not_found'}), 404
    return jsonify({'status': job.get('status'), 'progress': job.get('progress', 0),
                    'message': job.get('message', ''), 'result': job.get('result')})
```
(Confirm `os`, `shutil`, `subprocess`, `VIDEOS`, `OUTPUTS`, `PROJECT_ROOT`, `_venv_python`, `jobs` are already module-level in app.py — they are, used by the existing analyze flow.)

- [ ] **Step 2: Add the mode switch + posture panel markup to web_ui.html**

Read `web_ui.html` to find the top of the body / main container and the `panel-results` conventions. Add a mode switch near the top (above the existing match panels) and a posture panel that starts hidden:
```html
<div id="mode-switch" style="margin:12px 0;display:flex;gap:8px">
  <button id="mode-match" onclick="setMode('match')">🏟 比赛分析 Match</button>
  <button id="mode-posture" class="btn2" onclick="setMode('posture')">🧍 姿态训练 Posture</button>
</div>

<div id="panel-posture" class="hidden" style="margin-top:16px">
  <div class="ph">🧍 姿态训练 Posture Drill</div>
  <div style="margin:8px 0">
    <label>击球类型 Stroke:
      <select id="posture-stroke">
        <option value="high_clear">高远球 High Clear</option>
        <option value="smash">杀球 Smash</option>
        <option value="drop_shot">吊球 Drop Shot</option>
        <option value="serve">发球 Serve</option>
      </select>
    </label>
    <label style="margin-left:12px">持拍手 Hand:
      <select id="posture-hand"><option value="right">右 Right</option><option value="left">左 Left</option></select>
    </label>
    <button id="btn-posture-analyze" onclick="postureAnalyze()" disabled>▶ 分析 Analyze</button>
  </div>
  <div id="posture-progress-wrap" class="hidden">
    <div style="background:#222;border-radius:6px;overflow:hidden;height:14px">
      <div id="posture-pfill" style="height:100%;width:0;background:var(--ac)"></div>
    </div>
    <div id="posture-pmsg" style="font-size:.8rem;color:var(--mu);margin-top:4px"></div>
  </div>
  <div id="posture-results" class="hidden" style="margin-top:12px">
    <video id="posture-video" controls style="width:100%;max-height:480px;background:#000"></video>
    <div id="posture-summary" style="margin-top:10px"></div>
    <div id="posture-rep-list"></div>
    <div id="posture-rep-detail" class="hidden" style="margin:10px 0;background:#0f172a;border-radius:8px;padding:12px"></div>
    <div id="posture-plan" style="margin-top:14px">
      <div class="ph">🏋 训练计划 Training Plan</div>
      <button onclick="posturePlanLoc('court')">🏟 球场 On-Court</button>
      <button class="btn2" onclick="posturePlanLoc('home')">🏠 居家 At-Home</button>
      <button onclick="postureRegen()">🔄 重新生成 Regenerate</button>
      <div id="posture-plan-weeks"></div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add the posture JS to web_ui.html**

Add inside the existing `<script>` block (before `</script>`). Reuse the same `scoreColor`/`METRIC_LABEL` helpers added in Plan 2 (they already exist in this file). `selVideo` is the existing selected-video variable.
```javascript
var _postureData = null, _posturePlan = null;

function setMode(mode) {
  var match = mode !== 'posture';
  // Toggle the match panels (court/results) vs the posture panel.
  ['panel-court','panel-results'].forEach(function(id){
    var el = document.getElementById(id); if (el) el.classList.toggle('hidden', !match);
  });
  document.getElementById('panel-posture').classList.toggle('hidden', match);
  document.getElementById('mode-match').className = match ? '' : 'btn2';
  document.getElementById('mode-posture').className = match ? 'btn2' : '';
  var pb = document.getElementById('btn-posture-analyze');
  if (pb) pb.disabled = !selVideo;
}

function postureAnalyze() {
  if (!selVideo) return;
  var btn = document.getElementById('btn-posture-analyze');
  btn.disabled = true; btn.textContent = '分析中...';
  document.getElementById('posture-progress-wrap').classList.remove('hidden');
  fetch('/api/posture/analyze', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({video: selVideo,
      stroke_type: document.getElementById('posture-stroke').value,
      dominant_hand: document.getElementById('posture-hand').value})})
  .then(function(r){return r.json()}).then(function(d){
    if (!d.ok) { alert('启动失败: '+(d.error||'未知')); btn.disabled=false; btn.textContent='▶ 分析 Analyze'; return; }
    posturePoll(d.job_id);
  });
}

function posturePoll(jid) {
  var iv = setInterval(function(){
    fetch('/api/posture-analyze-status/'+jid).then(function(r){return r.json()}).then(function(d){
      document.getElementById('posture-pfill').style.width = (d.progress||0)+'%';
      document.getElementById('posture-pmsg').textContent = d.message||'';
      if (d.status === 'completed') {
        clearInterval(iv);
        var btn = document.getElementById('btn-posture-analyze');
        btn.disabled = false; btn.textContent = '▶ 重新分析';
        if (d.result && d.result.output_video) {
          var v = document.getElementById('posture-video');
          v.src = d.result.output_video+'?'+Date.now(); v.load();
        }
        postureLoad(selVideo.replace(/\.[^.]+$/,''));
      } else if (d.status === 'error') {
        clearInterval(iv);
        var b = document.getElementById('btn-posture-analyze');
        b.disabled = false; b.textContent = '▶ 重试';
        document.getElementById('posture-pmsg').textContent = d.message||'失败';
      }
    });
  }, 2000);
}

function postureLoad(stem) {
  fetch('/api/posture/'+stem).then(function(r){ if(!r.ok) return null; return r.json(); })
  .then(function(d){
    if (!d) return;
    _postureData = d;
    document.getElementById('posture-results').classList.remove('hidden');
    var s = d.summary;
    var html = '<p>共 <b>'+s.rep_count+'</b> 次重复 / '+s.rep_count+' reps. 平均分 '
      + (s.mean_score!=null?s.mean_score:'N/A') + '，一致性(越低越稳) ' + (s.consistency!=null?s.consistency:'N/A') + '</p>';
    if (s.best_rep) html += '<p>最佳 Rep '+s.best_rep.rep_id+' ('+s.best_rep.score+') · 最弱 Rep '+(s.worst_rep?s.worst_rep.rep_id+' ('+s.worst_rep.score+')':'N/A')+'</p>';
    if ((s.recurring_weaknesses||[]).length) {
      html += '<p>主要弱点 Top weaknesses:</p><ul style="margin:4px 0 0 16px">';
      s.recurring_weaknesses.slice(0,3).forEach(function(w){ html += '<li>'+(METRIC_LABEL[w.metric]||w.metric)+' ('+w.count+')</li>'; });
      html += '</ul>';
    }
    document.getElementById('posture-summary').innerHTML = html;
    var rl = '<div style="font-weight:600;margin:8px 0">重复列表 Reps — 点击查看</div>';
    (d.reps||[]).forEach(function(rep, i){
      var c = scoreColor(rep.overall_score);
      rl += '<div onclick="postureRepDetail('+i+')" style="cursor:pointer;padding:7px 10px;border-left:5px solid '+c+';margin:4px 0;background:#0f172a;border-radius:0 6px 6px 0;font-size:.83rem">Rep '+rep.rep_id+' — <b style="color:'+c+'">'+(rep.overall_score!=null?rep.overall_score:'N/A')+'</b></div>';
    });
    document.getElementById('posture-rep-list').innerHTML = rl;
    var dt = document.getElementById('posture-rep-detail'); dt.innerHTML=''; dt.classList.add('hidden');
    postureLoadPlan(stem);
  });
}

function postureRepDetail(i) {
  var rep = _postureData.reps[i];
  var html = '<div style="font-weight:600;margin-bottom:10px">Rep '+rep.rep_id+': '+rep.stroke_type+'</div>';
  var pm = rep.per_metric || {};
  for (var metric in pm) {
    if (!Object.prototype.hasOwnProperty.call(pm, metric)) continue;
    var m = pm[metric]; var sc = m.score; var w = sc==null?0:Math.max(0,Math.min(100,sc));
    html += '<div style="margin:5px 0;display:flex;align-items:center;gap:8px">'
      + '<span style="display:inline-block;width:170px;flex-shrink:0">'+(METRIC_LABEL[metric]||metric)+'</span>'
      + '<span style="display:inline-block;width:200px;background:#333;border-radius:3px;flex-shrink:0"><span style="display:inline-block;height:12px;width:'+(w*2)+'px;background:'+scoreColor(sc)+';border-radius:3px;"></span></span>'
      + '<span style="color:var(--mu)">'+(m.measured!=null?m.measured:'N/A')+(m.ideal_range?' (ideal '+m.ideal_range[0]+'–'+m.ideal_range[1]+')':'')+'</span></div>';
  }
  if ((rep.weaknesses||[]).length) {
    html += '<div style="margin-top:10px;font-weight:600">改进建议 Suggestions</div><ul style="margin:6px 0 0 16px">';
    rep.weaknesses.forEach(function(wk){ html += '<li style="margin:3px 0">'+wk.description+'</li>'; });
    html += '</ul>';
  }
  var dt = document.getElementById('posture-rep-detail'); dt.innerHTML = html; dt.classList.remove('hidden');
}

function postureLoadPlan(stem) {
  fetch('/api/posture-plan/'+stem).then(function(r){ if(!r.ok) return null; return r.json(); })
  .then(function(d){ if(!d) return; _posturePlan = d; posturePlanLoc('court'); });
}
function postureRegen() {
  var stem = selVideo.replace(/\.[^.]+$/,'');
  fetch('/api/posture-plan/'+stem, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({weeks:4})})
  .then(function(r){return r.json()}).then(function(d){ _posturePlan = d; posturePlanLoc('court'); });
}
function posturePlanLoc(loc) {
  if (!_posturePlan) return;
  var html = '';
  (_posturePlan.weeks||[]).forEach(function(wk){
    var rows = (wk.sessions||[]).filter(function(s){ return s.location === loc; });
    if (!rows.length) return;
    html += '<h4>第 '+wk.week+' 周 ('+wk.phase+')</h4><ul>';
    rows.forEach(function(s){ html += '<li>'+s.name+' — '+s.sets+'×'+s.reps+', '+s.frequency_per_week+'×/周, '+s.duration_min+'min</li>'; });
    html += '</ul>';
  });
  document.getElementById('posture-plan-weeks').innerHTML = html || '<p>本视图暂无项目</p>';
}
```
Also: in the EXISTING video-select handler (where `selVideo` is set and the match analyze button is enabled), add `var pb=document.getElementById('btn-posture-analyze'); if(pb) pb.disabled=false;` so the posture analyze button enables on selection too. And call `setMode('match')` once at init (next to the existing `loadVideos();`).

- [ ] **Step 4: Verify routes + served HTML**

Run: `.venv/Scripts/python.exe -m pytest -q` (full suite — confirms no Python regressions).
Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -c "import re; s=open('web_ui.html',encoding='utf-8').read(); assert s.count('<script>')==s.count('</script>'); assert 'setMode' in s and 'postureAnalyze' in s and 'panel-posture' in s; print('posture UI present, script tags balanced')"`
Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -c "from app import app; c=app.test_client(); r=c.get('/'); assert r.status_code==200 and b'panel-posture' in r.data and b'setMode' in r.data; print('index serves posture UI: 200')"`
Expected: all three succeed.

- [ ] **Step 5: Manual smoke (front-end has no automated harness)**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe app.py --port 5056` and open http://127.0.0.1:5056 . Confirm: the mode switch shows; clicking 🧍 Posture hides the court/results panels and shows the posture panel; selecting a video enables ▶ Analyze; (optionally) running analyze on a short side-view clip populates the rep list, detail, summary, and training plan. Stop the server when done.

- [ ] **Step 6: Commit**

```bash
git add app.py web_ui.html
git commit -m "feat: posture drill mode switch, panel, and analyze route in web UI"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`, `README_EN.md`

**Interfaces:** none (docs only). One review gate.

- [ ] **Step 1: Document posture mode**

Read the existing README technique section (added in the earlier docs task) and add a "Posture Drill / 姿态训练" subsection covering: what it is (court-free, single player, side view, you pick the stroke), the CLI (`python main_posture.py --video-path X --stroke-type high_clear --dominant-hand right`), the output dir `outputs/<video>/posture/` with `drill_reps.jsonl` / `drill_summary.json` / `training_plan.json` / annotated video, and the Web UI mode switch (🏟 Match / 🧍 Posture). Note side-view is the tuned/supported view for the first release (other views future work) and that reps are detected from swing motion (shuttle optional). Match `README.md` tone (Chinese) and add the parallel English section to `README_EN.md`.

- [ ] **Step 2: Verify docs-only + commit**

Run: `git status --short` (expect only README.md / README_EN.md).
```bash
git add README.md README_EN.md
git commit -m "docs: document posture drill mode"
```

---

## Self-Review

**1. Spec coverage:**
- Court-free pipeline, full-frame pose, no court/ROI/rally → Task 3 (`PostureAnalysisSystem._capture_frame`, no `is_court`). ✅
- User-chosen stroke type, no classifier → Task 3 `PostureRunner` builds `StrokeEvent` with the given `stroke_type`; Task 4 CLI `--stroke-type`; Task 6 dropdown. ✅
- Swing-motion rep segmentation, shuttle refinement when present → Task 1. ✅
- Side-view focus / reuse existing reference ranges → uses existing `BiomechanicalAnalyzer`/ranges (spec non-goal: side-specific tuning deferred). ✅
- Reuse Plan 1 analyzer + Plan 2 training plan → Task 3 (analyzer), Task 5 (`generate_plan`). ✅
- Per-rep reports + aggregate (consistency/best/worst) + annotated video + training plan, no court maps → Tasks 2, 3, 5, 6. ✅
- Output in `outputs/<video>/posture/` → Tasks 3, 5. ✅
- Top-level UI mode switch, match flow untouched → Task 6 (`setMode`, additive panels/routes). ✅
- Error handling: 0 reps valid empty result (Task 1/2/3); routes JSON 500 + 404 + 400 (Task 5/6). ✅
- Match pipeline untouched → no edits to `system.py`/`main.py`; `app.py`/`web_ui.html` additive only. ✅
- Tests: rep segmenter, drill writer, posture runner (fakes), Flask routes; UI manual + served-HTML assertion → Tasks 1,2,3,5,6. ✅

**2. Placeholder scan:** No TBD/TODO; every code step has complete code. `--help`/served-HTML/manual steps have exact commands.

**3. Type consistency:**
- `StrokeEvent(stroke_type, contact_frame, window_start, window_end, player_side, confidence)` used identically in Task 3 and matches the existing dataclass. ✅
- `RepWindow` fields (`rep_id, peak_frame, window_start, window_end, prominence`) defined Task 1, consumed Task 3. ✅
- Report dict gains `rep_id` in Task 3; `build_drill_summary` (Task 2) reads `rep_id`, `overall_score`, `per_metric{score}`, `weaknesses[].metric`, `strengths[str]` — consistent with the analyzer's report shape and Task 3's addition. ✅
- `build_drill_summary` output `recurring_weaknesses=[{metric,count}]` matches `generate_plan`'s expected input (Task 5). ✅
- `frame_lookup` record (`frame, keypoints, conf, racket_head, centroid`) produced by Task 3 `_capture_frame` / fakes, consumed by `PostureRunner._window_frames` and the analyzer. ✅
- Route paths (`/api/posture/<v>`, `/api/posture-plan/<v>`, `/api/posture/analyze`, `/api/posture-analyze-status/<id>`) consistent between Task 5, Task 6, and the UI JS. ✅

No issues requiring rework.
