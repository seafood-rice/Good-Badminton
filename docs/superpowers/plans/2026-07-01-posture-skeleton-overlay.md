# Posture Skeleton Overlay + Pose-Family Selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw the human skeleton + keypoints on the Posture Drill output video, and make the pose family (Ultralytics YOLO Pose / RTMPose / RTMO) selectable via CLI flag and Web UI dropdown.

**Architecture:** Extract the existing COCO-17 skeleton drawer from `PlayerPoseVisualizer` into a shared `visualization/skeleton.py`, have both match and posture modes use it, and add pose-family selection to `PostureAnalysisSystem` mirroring how the match system already chooses a processor. Purely additive; the match pipeline keeps identical behavior.

**Tech Stack:** Python 3.12 (venv), NumPy, OpenCV, Ultralytics YOLO pose + rtmlib (RTMPose/RTMO), Flask, pytest.

## Global Constraints

- Python 3.8+ compatible syntax (no `match`, no `X | Y` annotation unions). NumPy `>=1.21.6,<2.0`.
- Use the venv interpreter EXACTLY: `.venv/Scripts/python.exe` (Windows; NOT bare `python`).
- The existing match pipeline (`badminton_analysis/system.py`, `main.py`) must remain behavior-unchanged. Match mode's skeleton output must look identical after the refactor.
- Pose family values EXACTLY: `yolo-pose` (default), `rtmpose`, `rtmo`. Pose mode values: `lightweight`, `balanced` (default), `performance`.
- Full test suite currently passes at 91 tests; it must stay green throughout.
- Both `YOLOPoseProcessor(model_path=...)` and `RTMPoseProcessor(mode=..., pose_family=...)` already expose `process_frame(frame) -> (keypoints, scores)` where keypoints is an `(N,17,2)` ndarray or None and scores is `(N,17)` or None.

## Reused/verified signatures (do not redefine)
- COCO-17 skeleton connections currently in `badminton_analysis/visualization/player_pose.py` `PlayerPoseVisualizer.skeleton_connections`: `[(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)]`.
- `PlayerPoseVisualizer._draw_skeleton_on_frame(self, frame, keypoints, offset_x, offset_y)` — currently draws bones (color `(255,191,0)`, thickness 2) and keypoint dots (color `(255,128,0)`, radius 3), skipping points with `x<=1 or y<=1`.
- `joint_angles.is_valid(kp, idx, conf=None, conf_thresh=0.3)` (Plan-1 module).
- `PostureAnalysisSystem.__init__(self, video_path, stroke_type, dominant_hand="right", output_dir=None, ball_model_path=None, show_display=False, show_overlay=True, pose_model="weights/yolo11n-pose.pt")` — current signature to extend.
- `RTMPoseProcessor(mode='balanced', backend='onnxruntime', device='auto', pose_family='rtmpose')` — existing, `.process_frame(frame)`.

---

## File Structure

**New files:**
- `badminton_analysis/visualization/skeleton.py` — `SKELETON_CONNECTIONS`, `draw_skeleton`
- `tests/test_skeleton.py`

**Modified files:**
- `badminton_analysis/visualization/player_pose.py` — `_draw_skeleton_on_frame` delegates to shared drawer
- `badminton_analysis/posture/system.py` — pose-family selection; draw skeleton per frame
- `main_posture.py` — `--pose-family`, `--pose-mode`, `--yolo-pose-model` flags
- `app.py` — pass `pose_family` from the posture analyze route to `main_posture.py`
- `web_ui.html` — pose-family dropdown in the posture panel

---

### Task 1: Shared skeleton drawer

**Files:**
- Create: `badminton_analysis/visualization/skeleton.py`
- Test: `tests/test_skeleton.py`

**Interfaces:**
- Produces:
  - `SKELETON_CONNECTIONS` — the 12 COCO-17 bone tuples.
  - `draw_skeleton(frame, keypoints, conf=None, conf_thresh=0.3, line_color=(255,191,0), point_color=(255,128,0), line_thickness=2, point_radius=3) -> frame`. `keypoints` is a single-person `(17,2)` array-like. Draws bones for connections whose both endpoints are valid, and a dot per valid keypoint. "Valid" = not `(x<=1 and y<=1)` AND (if `conf` given) `conf[idx] >= conf_thresh`. Mutates and returns the same frame. No-op-safe when `keypoints` is None/empty.

- [ ] **Step 1: Write the failing tests**

`tests/test_skeleton.py`:
```python
import numpy as np
from badminton_analysis.visualization.skeleton import SKELETON_CONNECTIONS, draw_skeleton


def _full_person():
    # 17 valid keypoints spread across a 400x400 frame region.
    kp = np.zeros((17, 2))
    for i in range(17):
        kp[i] = (50 + i * 5, 60 + i * 5)
    return kp


def test_connections_are_coco_pairs():
    assert (5, 6) in SKELETON_CONNECTIONS
    assert len(SKELETON_CONNECTIONS) == 12
    for a, b in SKELETON_CONNECTIONS:
        assert 0 <= a < 17 and 0 <= b < 17


def test_draw_skeleton_mutates_and_returns_same_frame():
    frame = np.zeros((400, 400, 3), dtype=np.uint8)
    out = draw_skeleton(frame, _full_person())
    assert out is frame
    assert int(frame.sum()) > 0  # something drawn


def test_draw_skeleton_none_and_empty_safe():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert draw_skeleton(frame, None) is frame
    assert draw_skeleton(frame, np.zeros((0, 2))) is frame
    assert int(frame.sum()) == 0  # nothing drawn


def test_draw_skeleton_skips_missing_keypoints():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    kp = np.ones((17, 2))  # all (1,1) -> all "missing" (x<=1 and y<=1)
    draw_skeleton(frame, kp)
    assert int(frame.sum()) == 0  # nothing drawn for missing points


def test_draw_skeleton_respects_confidence():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    kp = _full_person()
    conf = np.ones(17) * 0.9
    conf[:] = 0.0  # all below threshold
    draw_skeleton(frame, kp, conf=conf, conf_thresh=0.3)
    assert int(frame.sum()) == 0  # all filtered out by low confidence
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skeleton.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/visualization/skeleton.py`:
```python
"""Shared COCO-17 skeleton + keypoint drawer, used by match and posture modes."""
import cv2
import numpy as np

# COCO-17 bone connections (shoulders, arms, torso, hips, legs).
SKELETON_CONNECTIONS = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]


def _valid(kp, idx, conf, conf_thresh):
    x, y = float(kp[idx][0]), float(kp[idx][1])
    if x <= 1 and y <= 1:
        return False
    if conf is not None and float(conf[idx]) < conf_thresh:
        return False
    return True


def draw_skeleton(frame, keypoints, conf=None, conf_thresh=0.3,
                  line_color=(255, 191, 0), point_color=(255, 128, 0),
                  line_thickness=2, point_radius=3):
    """Draw one person's COCO-17 skeleton + keypoints on frame (in place)."""
    if keypoints is None:
        return frame
    kp = np.asarray(keypoints, dtype=float)
    if kp.ndim != 2 or kp.shape[0] < 17 or kp.shape[1] < 2:
        return frame

    for a, b in SKELETON_CONNECTIONS:
        if _valid(kp, a, conf, conf_thresh) and _valid(kp, b, conf, conf_thresh):
            pt1 = (int(kp[a][0]), int(kp[a][1]))
            pt2 = (int(kp[b][0]), int(kp[b][1]))
            cv2.line(frame, pt1, pt2, line_color, line_thickness, cv2.LINE_AA)

    for i in range(kp.shape[0]):
        if _valid(kp, i, conf, conf_thresh):
            cv2.circle(frame, (int(kp[i][0]), int(kp[i][1])), point_radius,
                       point_color, -1, cv2.LINE_AA)

    return frame
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skeleton.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Run full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q` (expect 91 + 5 = 96 passed).
```bash
git add badminton_analysis/visualization/skeleton.py tests/test_skeleton.py
git commit -m "feat: shared COCO-17 skeleton drawer"
```

---

### Task 2: Match visualizer delegates to the shared drawer

**Files:**
- Modify: `badminton_analysis/visualization/player_pose.py`
- Test: `tests/test_player_pose_skeleton.py`

**Interfaces:**
- Consumes: `draw_skeleton`, `SKELETON_CONNECTIONS` (Task 1).
- Produces: `PlayerPoseVisualizer._draw_skeleton_on_frame(self, frame, keypoints, offset_x, offset_y)` — same signature and visual output as before, but implemented by shifting each person's keypoints by `(offset_x, offset_y)` and calling `draw_skeleton`. Keeps `self.skeleton_connections` referencing the shared list (so any existing reads still work).

- [ ] **Step 1: Write the failing test**

`tests/test_player_pose_skeleton.py`:
```python
import numpy as np
from badminton_analysis.visualization.player_pose import PlayerPoseVisualizer
from badminton_analysis.visualization.skeleton import SKELETON_CONNECTIONS


class _FakePose:
    inference_name = "Fake"
    def process_frame(self, frame):
        return None, None


def test_uses_shared_connections():
    v = PlayerPoseVisualizer(rtmpose_processor=_FakePose())
    assert list(v.skeleton_connections) == list(SKELETON_CONNECTIONS)


def test_draw_skeleton_on_frame_applies_offset_and_draws():
    v = PlayerPoseVisualizer(rtmpose_processor=_FakePose())
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    person = np.zeros((17, 2))
    for i in range(17):
        person[i] = (10 + i * 3, 12 + i * 3)  # ROI-local coords
    # single person as (1,17,2)
    v._draw_skeleton_on_frame(frame, person[None, ...], offset_x=40, offset_y=50)
    assert int(frame.sum()) > 0  # drew something after offset shift
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_player_pose_skeleton.py -v`
Expected: FAIL — `test_uses_shared_connections` fails because `skeleton_connections` is still a local literal (not the shared list object), or the import path assertion differs.

Note: if `test_uses_shared_connections` already passes because the values are equal, that is acceptable — the binding requirement below still applies. The key new test is `test_draw_skeleton_on_frame_applies_offset_and_draws`, which passes both before and after; its purpose is a regression guard for the refactor.

- [ ] **Step 3: Refactor to delegate**

In `badminton_analysis/visualization/player_pose.py`:

At the top, add the import (with the other imports):
```python
from .skeleton import SKELETON_CONNECTIONS, draw_skeleton
```

In `__init__`, replace the literal `self.skeleton_connections = [ ... ]` block with:
```python
        self.skeleton_connections = SKELETON_CONNECTIONS
```

Replace the body of `_draw_skeleton_on_frame` with a delegation that preserves the offset behavior:
```python
    def _draw_skeleton_on_frame(self, frame, keypoints, offset_x, offset_y):
        for person in self._normalize_people(keypoints):
            person_arr = np.asarray(person, dtype=float)
            if person_arr.ndim != 2 or person_arr.shape[1] < 2:
                continue
            shifted = person_arr.copy()
            # shift only present points (leave the <=1 "missing" sentinel untouched)
            present = ~((shifted[:, 0] <= 1) & (shifted[:, 1] <= 1))
            shifted[present, 0] += offset_x
            shifted[present, 1] += offset_y
            draw_skeleton(frame, shifted)
```
(This preserves the prior colors/thickness/radius, which are the `draw_skeleton` defaults, and the same "skip missing" behavior.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_player_pose_skeleton.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q` (expect 96 + 2 = 98 passed; no regressions).
```bash
git add badminton_analysis/visualization/player_pose.py tests/test_player_pose_skeleton.py
git commit -m "refactor: match visualizer uses shared skeleton drawer"
```

---

### Task 3: Pose-family selection + skeleton overlay in PostureAnalysisSystem

**Files:**
- Modify: `badminton_analysis/posture/system.py`
- Test: `tests/test_posture_pose_family.py`

**Interfaces:**
- Consumes: `draw_skeleton` (Task 1); existing `YOLOPoseProcessor`, `RTMPoseProcessor`.
- Produces: `PostureAnalysisSystem.__init__(..., pose_family="yolo-pose", pose_mode="balanced", yolo_pose_model="weights/yolo11n-pose.pt")` (added params, all defaulted so existing callers keep working). A new method `_build_pose_processor()` returns the right processor for the family. `_capture_frame` draws the skeleton before the angle overlay. `metadata.json` gains `"pose_family"`.

- [ ] **Step 1: Write the failing test**

`tests/test_posture_pose_family.py`:
```python
from badminton_analysis.posture.system import PostureAnalysisSystem


def _sys(tmp_path, **kw):
    # Construct without a real video: point at a dummy path but only test __init__ + _build_pose_processor,
    # which do not open the video. Use a path that exists to pass the __init__ guard.
    import os
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"x")  # existence only; not opened by these methods
    return PostureAnalysisSystem(str(vid), stroke_type="high_clear",
                                 output_dir=str(tmp_path / "out"), **kw)


def test_defaults_to_yolo_family(tmp_path):
    s = _sys(tmp_path)
    assert s.pose_family == "yolo-pose"


def test_pose_family_stored(tmp_path):
    s = _sys(tmp_path, pose_family="rtmpose", pose_mode="performance")
    assert s.pose_family == "rtmpose"
    assert s.pose_mode == "performance"


def test_build_pose_processor_yolo_uses_injected_builder(tmp_path, monkeypatch):
    s = _sys(tmp_path, pose_family="yolo-pose")
    made = {}
    import badminton_analysis.detection.yolo_pose as yp
    class _FakeYolo:
        def __init__(self, model_path="x", **kw): made["yolo"] = model_path
        def process_frame(self, f): return None, None
    monkeypatch.setattr(yp, "YOLOPoseProcessor", _FakeYolo)
    proc = s._build_pose_processor()
    assert isinstance(proc, _FakeYolo)
    assert "yolo" in made


def test_build_pose_processor_rtm_uses_injected_builder(tmp_path, monkeypatch):
    s = _sys(tmp_path, pose_family="rtmo", pose_mode="balanced")
    made = {}
    import badminton_analysis.detection.rtmpose as rp
    class _FakeRtm:
        def __init__(self, mode="balanced", pose_family="rtmpose", **kw):
            made["mode"] = mode; made["family"] = pose_family
        def process_frame(self, f): return None, None
    monkeypatch.setattr(rp, "RTMPoseProcessor", _FakeRtm)
    proc = s._build_pose_processor()
    assert isinstance(proc, _FakeRtm)
    assert made["family"] == "rtmo" and made["mode"] == "balanced"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posture_pose_family.py -v`
Expected: FAIL — `pose_family` attribute / `_build_pose_processor` don't exist yet.

- [ ] **Step 3: Implement**

In `badminton_analysis/posture/system.py`, extend `PostureAnalysisSystem.__init__` signature and store the new params. Change the signature line:
```python
    def __init__(self, video_path, stroke_type, dominant_hand="right",
                 output_dir=None, ball_model_path=None, show_display=False,
                 show_overlay=True, pose_model="weights/yolo11n-pose.pt",
                 pose_family="yolo-pose", pose_mode="balanced",
                 yolo_pose_model="weights/yolo11n-pose.pt"):
```
After the existing attribute assignments (after `self.pose_model = pose_model`), add:
```python
        self.pose_family = pose_family
        self.pose_mode = pose_mode
        self.yolo_pose_model = yolo_pose_model
```

Add this method to the class (near `process_video`):
```python
    def _build_pose_processor(self):
        if self.pose_family == "yolo-pose":
            from ..detection.yolo_pose import YOLOPoseProcessor
            return YOLOPoseProcessor(model_path=self.yolo_pose_model)
        from ..detection.rtmpose import RTMPoseProcessor
        return RTMPoseProcessor(mode=self.pose_mode, pose_family=self.pose_family)
```

In `process_video`, replace the line `pose = YOLOPoseProcessor(model_path=self.pose_model)` (and remove the now-unused top-of-method `from ..detection.yolo_pose import YOLOPoseProcessor` import) with:
```python
        pose = self._build_pose_processor()
```

Add the skeleton import at the top of `process_video`'s import block:
```python
        from ..visualization.skeleton import draw_skeleton
```
Pass it into `_capture_frame` — change the call site:
```python
            self._capture_frame(frame, frame_count, pose, ball_model, dom_wrist,
                                ja, draw_technique_overlay, draw_skeleton)
```

Update `_capture_frame`'s signature to accept it and draw the skeleton before the angle overlay. Change the signature:
```python
    def _capture_frame(self, frame, frame_count, pose, ball_model, dom_wrist,
                       ja, draw_technique_overlay, draw_skeleton):
```
Inside the `if keypoints is not None and len(keypoints) > 0:` block, immediately after `kp = keypoints[best_i].astype(float)` and `conf_row = ...` are set (and BEFORE the `if self.show_overlay:` angle block), add:
```python
            draw_skeleton(frame, kp, conf=conf_row)
```

In `process_video`, add `"pose_family": self.pose_family` to the `metadata.json` dict (in the top-level metadata object alongside `"mode"`, `"stroke_type"`, `"dominant_hand"`).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posture_pose_family.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q` (expect 98 + 4 = 102 passed; the existing `test_posture_runner.py` still passes since it doesn't touch `_capture_frame`).
```bash
git add badminton_analysis/posture/system.py tests/test_posture_pose_family.py
git commit -m "feat: selectable pose family and skeleton overlay in posture mode"
```

---

### Task 4: CLI flags for pose family

**Files:**
- Modify: `main_posture.py`

**Interfaces:**
- Consumes: `PostureAnalysisSystem` (Task 3).
- Produces: `main_posture.py` accepts `--pose-family {yolo-pose,rtmpose,rtmo}` (default yolo-pose), `--pose-mode {lightweight,balanced,performance}` (default balanced), `--yolo-pose-model` (default weights/yolo11n-pose.pt), passing them into the constructor.

- [ ] **Step 1: Add the flags**

In `main_posture.py`, add after the `--pose-model` argument:
```python
    parser.add_argument("--pose-family", default="yolo-pose",
                        choices=["yolo-pose", "rtmpose", "rtmo"],
                        help="Pose model family for skeleton detection")
    parser.add_argument("--pose-mode", default="balanced",
                        choices=["lightweight", "balanced", "performance"],
                        help="RTMPose/RTMO model tier (ignored for yolo-pose)")
    parser.add_argument("--yolo-pose-model", default="weights/yolo11n-pose.pt",
                        help="YOLO pose model path (used when pose-family=yolo-pose)")
```
In the `PostureAnalysisSystem(...)` construction call, add:
```python
        pose_family=args.pose_family,
        pose_mode=args.pose_mode,
        yolo_pose_model=args.yolo_pose_model,
```

- [ ] **Step 2: Verify --help**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe main_posture.py --help`
Expected: exit 0; help lists `--pose-family` (with the 3 choices), `--pose-mode`, `--yolo-pose-model`.

- [ ] **Step 3: Full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q` (expect 102 passed; unchanged — CLI has no tests).
```bash
git add main_posture.py
git commit -m "feat: pose-family CLI flags for posture mode"
```

---

### Task 5: Web UI pose-family dropdown + analyze wiring

**Files:**
- Modify: `app.py`
- Modify: `web_ui.html`
- Test: served-HTML assertion + full suite (front-end manual)

**Interfaces:**
- Consumes: the posture analyze route `POST /api/posture/analyze` (existing) and `main_posture.py` (Task 4).
- Produces: the posture analyze route accepts an optional `pose_family` in the JSON body (default `yolo-pose`, validated against the 3 values) and passes `--pose-family` to `main_posture.py`; the posture panel has a pose-family `<select>` whose value is sent on analyze.

- [ ] **Step 1: Extend the analyze route in app.py**

Read `app.py`'s `api_posture_analyze`. After reading `dominant = data.get('dominant_hand', 'right')`, add:
```python
    pose_family = data.get('pose_family', 'yolo-pose')
    if pose_family not in ('yolo-pose', 'rtmpose', 'rtmo'):
        return jsonify({'error': 'invalid pose_family'}), 400
```
In the `cmd = [...]` list that launches `main_posture.py`, add the flag (after the `--dominant-hand` pair):
```python
        '--pose-family', pose_family,
```

- [ ] **Step 2: Add a test for the pose_family validation**

Add to `tests/test_app_posture_routes.py` a test (match the file's existing `client` fixture):
```python
def test_posture_analyze_rejects_bad_pose_family(client, tmp_path):
    # video must exist for the 404 guard to pass; create it under VIDEOS
    import app as webapp
    webapp.VIDEOS.mkdir(parents=True, exist_ok=True)
    (webapp.VIDEOS / "clip.mp4").write_bytes(b"x")
    r = client.post("/api/posture/analyze",
                    json={"video": "clip.mp4", "stroke_type": "high_clear",
                          "pose_family": "bogus"})
    assert r.status_code == 400
```
Note: if the existing tests monkeypatch `VIDEOS`, mirror that; read the file first. If `VIDEOS` isn't monkeypatched, this writes into the real `videos/` dir — instead prefer monkeypatching `webapp.VIDEOS` to `tmp_path` in this test to avoid touching the real tree. Use whichever matches the file's existing convention.

- [ ] **Step 3: Run the route test**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_posture_routes.py -v`
Expected: PASS (existing + 1 new).

- [ ] **Step 4: Add the dropdown to web_ui.html**

In the posture panel (near the stroke-type / dominant-hand selects), add:
```html
    <label style="margin-left:12px">姿态模型 Pose:
      <select id="posture-pose-family">
        <option value="yolo-pose">YOLO Pose (快)</option>
        <option value="rtmpose">RTMPose (准)</option>
        <option value="rtmo">RTMO</option>
      </select>
    </label>
```
In the `postureAnalyze()` JS, include the value in the POST body — change the body to add:
```javascript
      pose_family: document.getElementById('posture-pose-family').value,
```

- [ ] **Step 5: Verify served HTML + full suite**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -c "from app import app; c=app.test_client(); r=c.get('/'); assert r.status_code==200 and b'posture-pose-family' in r.data; print('pose-family dropdown served')"`
Run: `.venv/Scripts/python.exe -m pytest -q` (expect 103 passed).
Expected: both succeed.

- [ ] **Step 6: Commit**

```bash
git add app.py web_ui.html tests/test_app_posture_routes.py
git commit -m "feat: pose-family dropdown wired through posture analyze"
```

---

## Self-Review

**1. Spec coverage (skeleton + pose-family portion of the spec):**
- Shared skeleton drawer extracted → Task 1. ✅
- Match mode uses shared drawer, behavior unchanged → Task 2. ✅
- Skeleton always drawn on posture output video → Task 3 (`draw_skeleton` in `_capture_frame`, before angle overlay). ✅
- Pose family selectable (yolo-pose/rtmpose/rtmo) → Task 3 (`_build_pose_processor`), Task 4 (CLI), Task 5 (UI). ✅
- `metadata.json` records pose_family → Task 3. ✅
- Match pipeline behavior unchanged → Tasks 1-2 keep colors/thickness/skip-missing identical; `system.py`/`main.py` untouched. ✅

**2. Placeholder scan:** No TBD/TODO; complete code in each code step. Task 5 Step 2 gives an explicit test with a documented convention-match caveat (read the file), not a vague placeholder.

**3. Type consistency:**
- `draw_skeleton(frame, keypoints, conf=None, ...)` signature identical across Tasks 1, 2, 3. ✅
- `SKELETON_CONNECTIONS` name consistent (Task 1 defines, Task 2 imports). ✅
- `PostureAnalysisSystem.__init__` new params (`pose_family`, `pose_mode`, `yolo_pose_model`) consistent between Task 3 (definition), Task 4 (CLI passes them), Task 5 (UI sends pose_family). ✅
- `_build_pose_processor()` defined Task 3, used in `process_video` Task 3. ✅
- `_capture_frame` gains a `draw_skeleton` param — call site updated in the same task. ✅

No issues requiring rework.
