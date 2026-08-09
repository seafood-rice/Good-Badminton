# Posture Rep Detection for Smash, Drop Shot and Serve — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make posture-mode rep detection correct for `smash`, `drop_shot` and `serve` — gate the overhead strokes against over-counting, and stop anchoring a serve's contact frame with a heuristic its own code comment calls wrong for serves.

**Architecture:** Five small changes along one existing path (`track → segment_reps → gate → biomechanics → report`). The gate's geometry is already stroke-agnostic, so smash and drop_shot join the gated set unchanged; serve gets the *same* calibrated threshold applied as a ceiling instead of a floor. `rep_segmenter` stays a pure signal-processing module — the stroke-dependent choice is passed in as an argument by `PostureRunner`, never inferred inside the segmenter.

**Tech Stack:** Python 3, NumPy (`>=1.21.6,<2.0`), OpenCV (`opencv-python==4.10.0.84`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-08-posture-rep-detection-smash-drop-serve-design.md`

## Global Constraints

- `main` is the protected integration branch. Never commit, merge, rebase or push to it. Work on `claude/posture-rep-detection`.
- Run tests with the project venv and UTF-8 forced:
  `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest <target> -q -p no:cacheprovider`
- Prefer targeted test runs. `tests/test_ai_handoff.py` is known flaky in long batch runs and is unrelated to this work; exclude it from full-suite runs with `--ignore=tests/test_ai_handoff.py`.
- Never stage the pre-existing untracked `BirdEye Prototype.html`.
- **No new tuning constants.** The spec's §3.2 decision is that serve reuses `OVERHEAD_MIN_ELEVATION = 0.35` as a ceiling. Do not introduce a second threshold.
- **`None` elevation means "cannot judge → keep the rep"**, in both gate directions. This is the existing convention in `apex_overhead_elevation`; preserve it exactly.
- **`high_clear` behaviour must not change.** It is the only stroke with frame-verified ground truth (`highclear1` 16 reps, `IMG_1270` 5, `IMG_9691` 7).
- Both new gate applications rest on a threshold calibrated only on `high_clear` footage. Where you write a comment about them, say they are unvalidated rather than implying they are measured (spec §6).

---

### Task 1: Gate smash and drop shot

The gate at `system.py:235` is applied only when `self.stroke_type in OVERHEAD_GATED_STROKES`, and that tuple currently holds `high_clear` alone. `apex_overhead_elevation` (`system.py:83`) measures the maximum over the apex window of `(shoulder_y - wrist_y) / torso` on the dominant side — pure geometry, with nothing clear-specific in it. A smash and a drop shot are overhead strokes that lift the dominant wrist above the dominant shoulder, which is exactly what it tests.

**Files:**
- Modify: `badminton_analysis/posture/system.py:59`
- Test: `tests/test_posture_gate_strokes.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `OVERHEAD_GATED_STROKES == ("high_clear", "smash", "drop_shot")`. Tasks 2 and 5 import it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_posture_gate_strokes.py
"""Which stroke types the overhead-swing gate applies to.

The gate exists because rep over-counting was a real bug. It was applied only to
high_clear, leaving smash and drop_shot -- equally overhead strokes with the same
failure mode -- entirely unprotected.
"""
import numpy as np
import pytest
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.posture.system import (
    OVERHEAD_GATED_STROKES, PostureRunner,
)


def _kp(wrist_y):
    """Dominant-side keypoints with a settable wrist height.

    torso = |shoulder_y - hip_y| = |100 - 250| = 150, so elevation is
    (100 - wrist_y) / 150: wrist_y=40 -> 0.40 (overhead), wrist_y=120 -> -0.13 (low).
    """
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, wrist_y)
    kp[ja.L_SHOULDER] = (60, 90)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def _track(n=160):
    track = []
    for i in range(n):
        wrist = (100, 100)
        if i == 40 or i == 110:
            wrist = (260, 100)  # displacement spike -> rep candidate
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
    return track


def _run(stroke_type, wrist_y):
    kp = _kp(wrist_y)
    racket = ja.infer_racket_head(kp, dominant="right")

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": racket, "centroid": (100 + (idx % 5), 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type=stroke_type, dominant="right")
    return runner.run(_track(), frame_lookup, fps=30)


def test_smash_and_drop_shot_are_gated():
    assert OVERHEAD_GATED_STROKES == ("high_clear", "smash", "drop_shot")


@pytest.mark.parametrize("stroke_type", ["smash", "drop_shot"])
def test_low_swings_are_filtered_for_overhead_strokes(stroke_type):
    _reports, _reps, gate = _run(stroke_type, wrist_y=120)
    assert gate["gated"] is True
    assert gate["counted"] == 0
    assert gate["filtered_non_overhead"] == 2


@pytest.mark.parametrize("stroke_type", ["smash", "drop_shot", "high_clear"])
def test_genuine_overhead_swings_survive(stroke_type):
    _reports, _reps, gate = _run(stroke_type, wrist_y=40)
    assert gate["counted"] == 2
    assert gate["filtered_non_overhead"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_gate_strokes.py -q -p no:cacheprovider`
Expected: FAIL — `test_smash_and_drop_shot_are_gated` asserts the tuple equals three entries but it is `("high_clear",)`, and the two `smash`/`drop_shot` filtering cases report `gated=False, counted=2`.

- [ ] **Step 3: Write minimal implementation**

In `badminton_analysis/posture/system.py:59`, replace:

```python
OVERHEAD_GATED_STROKES = ("high_clear",)  # only these stroke types are gated
```

with:

```python
# Overhead strokes subject to the elevation FLOOR above. The gate measurement
# (apex_overhead_elevation) is pure geometry with nothing clear-specific in it, so
# it transfers to any stroke contacted above the shoulder. NOTE the 0.35 threshold
# was calibrated on high_clear footage (IMG_1270) only: a soft drop shot could sit
# near the boundary and be filtered. Unvalidated for drop_shot until drop-shot
# footage exists; over-filtering shows up in filtered_non_overhead rather than
# silently losing reps.
OVERHEAD_GATED_STROKES = ("high_clear", "smash", "drop_shot")
```

- [ ] **Step 4: Run test to verify it passes, and that high_clear is unchanged**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_gate_strokes.py tests/test_posture_runner.py tests/test_posture_runner_3d.py -q -p no:cacheprovider`
Expected: PASS. The pre-existing `test_posture_runner.py` assertions on `gate_info` must still pass untouched — they all use `high_clear` or an ungated stroke, and neither changes here.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/posture/system.py tests/test_posture_gate_strokes.py
git commit -m "feat(posture): gate smash and drop shot with the overhead-swing gate"
```

---

### Task 2: Serve ceiling gate

A serve is underhand, so its apex elevation is low. Adding `serve` to `OVERHEAD_GATED_STROKES` would filter **every** rep. It needs the opposite test: a rep whose apex elevation *reaches* the threshold is an overhead stroke and does not belong in a serve drill.

Per spec §3.2 this reuses `OVERHEAD_MIN_ELEVATION` from the other side rather than introducing a second constant, and per §3.6 it counts into a **separate** `filtered_overhead` key — folding it into `filtered_non_overhead` would make that name mean the opposite of what happened.

**Files:**
- Modify: `badminton_analysis/posture/system.py:59` (add `UNDERHAND_GATED_STROKES`), `:216-217`, `:235-237`, `:276`
- Modify: `tests/test_posture_runner.py` (6 `gate_info` assertions gain the new key)
- Modify: `tests/test_posture_runner_3d.py:191`
- Test: `tests/test_posture_gate_strokes.py` (extend)

**Interfaces:**
- Consumes: `OVERHEAD_GATED_STROKES` (Task 1), `OVERHEAD_MIN_ELEVATION`, `apex_overhead_elevation`.
- Produces: `UNDERHAND_GATED_STROKES == ("serve",)`; `gate_info` gains key `"filtered_overhead"` (int). Task 5 renders both counters.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_posture_gate_strokes.py`:

```python
from badminton_analysis.posture.system import UNDERHAND_GATED_STROKES


def test_serve_is_underhand_gated_not_overhead_gated():
    assert UNDERHAND_GATED_STROKES == ("serve",)
    assert "serve" not in OVERHEAD_GATED_STROKES


def test_serve_keeps_low_swings():
    """The whole point: overhead-gating a serve would filter every rep."""
    _reports, _reps, gate = _run("serve", wrist_y=120)
    assert gate["gated"] is True
    assert gate["counted"] == 2
    assert gate["filtered_overhead"] == 0
    assert gate["filtered_non_overhead"] == 0


def test_serve_filters_overhead_swings():
    _reports, _reps, gate = _run("serve", wrist_y=40)
    assert gate["counted"] == 0
    assert gate["filtered_overhead"] == 2
    assert gate["filtered_non_overhead"] == 0, (
        "a serve rep dropped for BEING overhead must not be counted as non-overhead")


def test_unjudgeable_elevation_keeps_the_rep_in_both_directions():
    """None elevation means 'cannot judge -> keep', the existing convention."""
    kp = np.zeros((17, 2))  # all-sentinel: no valid shoulder/wrist/hip
    racket = None

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": racket, "centroid": (100, 300)}

    for stroke_type in ("serve", "smash"):
        runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                               stroke_type=stroke_type, dominant="right")
        _reports, _reps, gate = runner.run(_track(), frame_lookup, fps=30)
        assert gate["filtered_overhead"] == 0
        assert gate["filtered_non_overhead"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_gate_strokes.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'UNDERHAND_GATED_STROKES'`.

- [ ] **Step 3: Write minimal implementation**

Add beneath `OVERHEAD_GATED_STROKES` in `badminton_analysis/posture/system.py`:

```python
# Underhand strokes subject to the same threshold as a CEILING: a rep that reaches
# overhead elevation is an overhead stroke, so it does not belong in this drill.
# Deliberately reuses OVERHEAD_MIN_ELEVATION rather than introducing a second
# constant -- 0.35 separates "overhead" from "not overhead" regardless of which side
# of it a given stroke is supposed to fall on. Unvalidated on serve footage.
UNDERHAND_GATED_STROKES = ("serve",)
```

In `PostureRunner.run`, replace the two lines at `:216-217`:

```python
        gated = self.stroke_type in OVERHEAD_GATED_STROKES
        filtered_non_overhead = 0
```

with:

```python
        overhead_gated = self.stroke_type in OVERHEAD_GATED_STROKES
        underhand_gated = self.stroke_type in UNDERHAND_GATED_STROKES
        gated = overhead_gated or underhand_gated
        filtered_non_overhead = 0
        filtered_overhead = 0
```

Replace the gate application at `:235-237`:

```python
            if gated and elev is not None and elev < OVERHEAD_MIN_ELEVATION:
                filtered_non_overhead += 1
                continue
```

with:

```python
            # `elev is None` means the window yielded no valid shoulder/wrist/hip
            # measurement: cannot judge -> keep the rep, in BOTH directions.
            if elev is not None:
                if overhead_gated and elev < OVERHEAD_MIN_ELEVATION:
                    filtered_non_overhead += 1
                    continue
                if underhand_gated and elev >= OVERHEAD_MIN_ELEVATION:
                    filtered_overhead += 1
                    continue
```

Add the new key to `gate_info` at `:276`:

```python
        gate_info = {"counted": len(reports), "filtered_non_overhead": filtered_non_overhead,
                     "filtered_overhead": filtered_overhead,
                     "gated": gated, "scored_3d": scored_3d, "reps_3d": reps_3d}
```

- [ ] **Step 4: Update the six pre-existing exact-dict assertions**

These assert `gate_info ==` a literal dict and will fail on the new key. This is expected — update them by adding `"filtered_overhead": 0` to each, and do **not** "fix" them by removing the new counter.

Sites: `tests/test_posture_runner.py` lines 60, 114, 217, 246, 262, 306; `tests/test_posture_runner_3d.py` line 191 (which asserts a single key and needs no change — verify).

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_gate_strokes.py tests/test_posture_runner.py tests/test_posture_runner_3d.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/posture/system.py tests/test_posture_gate_strokes.py \
        tests/test_posture_runner.py tests/test_posture_runner_3d.py
git commit -m "feat(posture): serve ceiling gate reusing the overhead threshold"
```

---

### Task 3: Shuttle selection nearest the wrist

`posture/system.py:602-606` takes `boxes.xywh[0]` — whichever detection the model returned first. The shuttle is the **primary** contact anchor (`rep_segmenter.py:153-167` snaps the contact frame to the closest shuttle-wrist approach), so feeding it an arbitrary detection can move the contact frame off the stroke. This is the third instance of the same defect shape in this codebase, after `predict_location` keeping only the max-area contour and `detect_racket_head`.

`track["wrist"]` is the *dominant* wrist (`kp[dom_wrist]`) and is assigned earlier in the same frame iteration, so it is already available at this point — no reordering needed.

**Files:**
- Create: `badminton_analysis/posture/shuttle_pick.py`
- Modify: `badminton_analysis/posture/system.py:602-606`
- Test: `tests/test_posture_shuttle_pick.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `pick_shuttle(boxes_xywh, wrist) -> tuple[float, float] | None`, where `boxes_xywh` is an `(N, 4)` array-like of `x, y, w, h` centres and `wrist` is `(x, y)` or `None`. Task 4 does not use it; Task 5 does not use it.

Extracted to its own module rather than inlined because it is pure, needs no video, and is the only part of this task that is unit-testable — matching how `rep_segmenter` and `joint_angles` are already separated from the frame loop.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_posture_shuttle_pick.py
"""Choosing WHICH shuttle detection to use as the contact anchor.

The frame loop previously took boxes.xywh[0] -- whichever box the detector returned
first. The shuttle is the primary contact anchor, so an arbitrary pick can move a
rep's contact frame off the stroke.
"""
import numpy as np
import pytest
from badminton_analysis.posture.shuttle_pick import pick_shuttle


def test_picks_the_box_nearest_the_wrist():
    boxes = np.array([[500.0, 500.0, 10.0, 10.0],
                      [110.0, 105.0, 10.0, 10.0],
                      [300.0, 300.0, 10.0, 10.0]])
    assert pick_shuttle(boxes, wrist=(100.0, 100.0)) == (110.0, 105.0)


def test_single_box_is_returned_regardless_of_distance():
    boxes = np.array([[900.0, 900.0, 8.0, 8.0]])
    assert pick_shuttle(boxes, wrist=(0.0, 0.0)) == (900.0, 900.0)


def test_no_wrist_falls_back_to_the_first_box():
    """Preserves the previous behaviour when there is nothing to measure against."""
    boxes = np.array([[10.0, 20.0, 5.0, 5.0], [30.0, 40.0, 5.0, 5.0]])
    assert pick_shuttle(boxes, wrist=None) == (10.0, 20.0)


def test_empty_input_returns_none():
    assert pick_shuttle(np.zeros((0, 4)), wrist=(1.0, 2.0)) is None
    assert pick_shuttle(None, wrist=(1.0, 2.0)) is None


def test_returns_plain_floats_not_numpy_scalars():
    """The track is serialised to JSON downstream."""
    boxes = np.array([[110.0, 105.0, 10.0, 10.0]])
    x, y = pick_shuttle(boxes, wrist=(100.0, 100.0))
    assert type(x) is float and type(y) is float
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_shuttle_pick.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'badminton_analysis.posture.shuttle_pick'`.

- [ ] **Step 3: Write minimal implementation**

```python
# badminton_analysis/posture/shuttle_pick.py
"""Choose which shuttle detection to use as a frame's contact anchor.

The shuttle is the PRIMARY contact anchor: rep_segmenter snaps a rep's contact frame
to the in-window frame where shuttle and wrist are closest. Taking whichever box the
detector happened to return first can therefore move a rep's contact off the stroke,
and the error is largest exactly where the shuttle sits near the wrist -- which is
every contact frame.
"""
import math


def pick_shuttle(boxes_xywh, wrist):
    """Return the (x, y) centre of the detection nearest ``wrist``, or None.

    ``boxes_xywh`` is an (N, 4) array-like of x, y, w, h with x/y as the box centre
    (ultralytics' xywh convention). When ``wrist`` is None there is nothing to
    measure against, so the first box is kept -- the previous behaviour.
    """
    if boxes_xywh is None or len(boxes_xywh) == 0:
        return None
    if wrist is None:
        return (float(boxes_xywh[0][0]), float(boxes_xywh[0][1]))
    best, best_d = None, None
    for b in boxes_xywh:
        x, y = float(b[0]), float(b[1])
        d = math.hypot(x - float(wrist[0]), y - float(wrist[1]))
        if best_d is None or d < best_d:
            best, best_d = (x, y), d
    return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_shuttle_pick.py -q -p no:cacheprovider`
Expected: PASS (5 passed).

- [ ] **Step 5: Wire it into the frame loop**

Add to the imports at the top of `badminton_analysis/posture/system.py`:

```python
from .shuttle_pick import pick_shuttle
```

Replace `badminton_analysis/posture/system.py:602-606`:

```python
                res = ball_model(frame, conf=0.18, verbose=False)[0]
                boxes = getattr(res, "boxes", None)
                if boxes is not None and boxes.xywh.shape[0] > 0:
                    b = boxes.xywh.detach().cpu().numpy()[0]
                    shuttle = (float(b[0]), float(b[1]))
```

with:

```python
                res = ball_model(frame, conf=0.18, verbose=False)[0]
                boxes = getattr(res, "boxes", None)
                if boxes is not None and boxes.xywh.shape[0] > 0:
                    # Nearest the dominant wrist, not whichever box came back first:
                    # this detection is the primary contact anchor downstream.
                    shuttle = pick_shuttle(boxes.xywh.detach().cpu().numpy(), wrist)
```

- [ ] **Step 6: Run the posture suite to verify nothing regressed**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_shuttle_pick.py tests/test_posture_runner.py tests/test_posture_runner_3d.py tests/test_posture_racket.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add badminton_analysis/posture/shuttle_pick.py badminton_analysis/posture/system.py \
        tests/test_posture_shuttle_pick.py
git commit -m "fix(posture): anchor on the shuttle nearest the wrist, not the first box"
```

---

### Task 4: Stroke-appropriate positional fallback

`rep_segmenter.py:168-186` refines a rep's contact frame to the **wrist apex** (minimum image `y`) when no shuttle anchors the window. Its own comment states the limitation: *"Overhead-oriented heuristic: for an underhand serve the apex is not the contact."*

`segment_reps` gains a `positional_fallback` argument so the caller decides. The segmenter gains **no** knowledge of stroke types — it stays a signal-processing module whose behaviour is set by its arguments.

Spec §3.4 rejects a nadir or max-forward-reach heuristic: for a serve the wrist is low throughout, so a nadir may sit in the backswing, and no serve footage exists to test one. The wrist-speed peak is not known-wrong and invents nothing.

**Files:**
- Modify: `badminton_analysis/posture/rep_segmenter.py:91-92` (signature), `:168-186` (fallback)
- Modify: `badminton_analysis/posture/system.py:211` (caller)
- Test: `tests/test_rep_segmenter_fallback.py`

**Interfaces:**
- Consumes: `UNDERHAND_GATED_STROKES` (Task 2) — used by the **caller** in `system.py` to pick the fallback. `rep_segmenter` itself imports nothing new and holds no stroke vocabulary.
- Produces: `segment_reps(track, fps, pre=20, post=15, smooth=3, min_speed_px=5.0, max_reps=50, positional_fallback="apex")`, and `RepWindow.contact_anchor: str` defaulting to `"speed_peak"`. Task 5 reads that field.

**Depends on Task 2** for `UNDERHAND_GATED_STROKES`; do not start it before Task 2 is committed.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rep_segmenter_fallback.py
"""Which signal anchors a rep's contact frame when no shuttle is detected.

The apex fallback picks the HIGHEST wrist point, which is right for an overhead
stroke and wrong for an underhand serve -- rep_segmenter's own comment says so.
"""
from badminton_analysis.posture.rep_segmenter import segment_reps


def _track_with_late_apex(n=120):
    """One speed spike at frame 40; the wrist's highest point is later, at 55.

    The apex must be reached GRADUALLY. A single jump to the high point would create
    its own speed spike larger than the intended one, and segment_reps would then
    find two reps rather than one. The rise is 15 px/frame, comfortably under the
    peak floor: the 160 px jump at frame 40 smooths to ~107, and PEAK_FLOOR_FRAC of
    0.25 puts the floor at ~27.

    Frame 40 is the speed peak; frame 55 is the wrist apex (min image y = 35). Both
    lie inside the rep window (peak 40, pre 20, post 15 at 30 fps -> frames 20..55).
    """
    track = []
    for i in range(n):
        if 45 <= i <= 55:
            y = 200.0 - 15.0 * (i - 44)      # gradual rise to y=35 at frame 55
        elif 56 <= i <= 66:
            y = 35.0 + 15.0 * (i - 55)       # gradual fall back
        else:
            y = 200.0
        x = 260.0 if i == 40 else 100.0      # displacement spike -> the speed peak
        track.append({"frame": i, "wrist": (x, y), "shuttle": None})
    return track


def test_apex_fallback_moves_contact_to_the_wrist_apex():
    reps = segment_reps(_track_with_late_apex(), fps=30, positional_fallback="apex")
    assert len(reps) == 1
    assert reps[0].peak_frame == 55
    assert reps[0].contact_anchor == "apex"


def test_no_fallback_keeps_the_speed_peak():
    reps = segment_reps(_track_with_late_apex(), fps=30, positional_fallback=None)
    assert len(reps) == 1
    assert reps[0].peak_frame == 40
    assert reps[0].contact_anchor == "speed_peak"


def test_apex_is_the_default_so_existing_callers_are_unaffected():
    reps = segment_reps(_track_with_late_apex(), fps=30)
    assert reps[0].peak_frame == 55
    assert reps[0].contact_anchor == "apex"


def test_shuttle_anchor_wins_over_both_and_is_recorded():
    track = _track_with_late_apex()
    # Place the shuttle exactly on the wrist so its distance is 0 and it wins
    # regardless of the wrist's y at that frame.
    track[47]["shuttle"] = track[47]["wrist"]
    reps = segment_reps(track, fps=30, positional_fallback="apex")
    assert reps[0].peak_frame == 47
    assert reps[0].contact_anchor == "shuttle"


def test_contact_anchor_survives_to_dict():
    reps = segment_reps(_track_with_late_apex(), fps=30, positional_fallback=None)
    assert reps[0].to_dict()["contact_anchor"] == "speed_peak"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rep_segmenter_fallback.py -q -p no:cacheprovider`
Expected: FAIL with `TypeError: segment_reps() got an unexpected keyword argument 'positional_fallback'`.

- [ ] **Step 3: Write minimal implementation**

In `badminton_analysis/posture/rep_segmenter.py`, add the field to the dataclass:

```python
@dataclass
class RepWindow:
    rep_id: int
    peak_frame: int
    window_start: int
    window_end: int
    prominence: float
    contact_anchor: str = "speed_peak"

    def to_dict(self):
        return asdict(self)
```

Change the signature at `:91-92`:

```python
def segment_reps(track, fps, pre=20, post=15,
                 smooth=3, min_speed_px=5.0, max_reps=50,
                 positional_fallback="apex"):
    """Segment a drill clip into stroke reps from wrist swing motion.

    ``positional_fallback`` selects what anchors a rep's contact frame when no
    shuttle is found in its window:

    * ``"apex"``  -- the wrist's highest point. Correct for overhead strokes, where
      it sits at or near contact and avoids centring the rep on the faster
      follow-through or backswing speed peak.
    * ``None``    -- no positional refinement; keep the wrist-speed peak. Used for
      underhand strokes, where the wrist is low throughout and no positional
      extremum is known to be the contact.

    The caller chooses; this module holds no stroke-type knowledge.
    """
```

Replace the refinement block at `:153-186` so it records which branch ran:

```python
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
            anchor = "shuttle"
        elif positional_fallback == "apex":
            apex_pos, apex_y = None, None
            for p in range(len(track)):
                f = track[p]["frame"]
                if f < window_start or f > window_end:
                    continue
                wr = track[p].get("wrist")
                if wr is not None and (apex_y is None or wr[1] < apex_y):
                    apex_y = wr[1]
                    apex_pos = p
            if apex_pos is not None:
                peak_frame = track[apex_pos]["frame"]
                anchor = "apex"
            else:
                anchor = "speed_peak"
        else:
            anchor = "speed_peak"
```

and pass it through when constructing the `RepWindow`:

```python
        reps.append(RepWindow(
            rep_id=rep_id,
            peak_frame=peak_frame,
            window_start=max(0, peak_frame - pre_f),
            window_end=peak_frame + post_f,
            prominence=round(float(arr[idx]) / max_speed, 3),
            contact_anchor=anchor,
        ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rep_segmenter_fallback.py tests/test_rep_segmenter.py -q -p no:cacheprovider`
Expected: PASS. `tests/test_rep_segmenter.py` must pass untouched — `"apex"` is the default, so existing callers are unaffected.

- [ ] **Step 5: Wire the caller to choose by stroke type**

Replace `badminton_analysis/posture/system.py:211`:

```python
        reps = segment_reps(track, fps, pre=self.window_pre, post=self.window_post)
```

with:

```python
        # An underhand stroke's contact is not at the wrist apex (rep_segmenter's
        # apex fallback is an overhead heuristic), so serve keeps the speed peak.
        fallback = None if self.stroke_type in UNDERHAND_GATED_STROKES else "apex"
        reps = segment_reps(track, fps, pre=self.window_pre, post=self.window_post,
                            positional_fallback=fallback)
```

- [ ] **Step 6: Run the posture suite**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rep_segmenter_fallback.py tests/test_rep_segmenter.py tests/test_posture_runner.py tests/test_posture_gate_strokes.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add badminton_analysis/posture/rep_segmenter.py badminton_analysis/posture/system.py \
        tests/test_rep_segmenter_fallback.py
git commit -m "feat(posture): stroke-appropriate contact fallback, recorded per rep"
```

---

### Task 5: Surface anchor provenance and fix the gate copy

Two reporting changes that make the previous four honest to a user.

`contact_anchor` reaches `drill_reps.jsonl` so a clip with poor shuttle detection is visible — Task 4 makes the serve path *depend* on shuttle quality, and without this that difference is invisible.

And `static/kestrel.js:740-742` hardcodes copy that is now wrong: *"only full overhead clears counted"* is untrue once smash and drop_shot are gated, and for a serve the message is **backwards** — those reps were excluded for *being* overhead.

**Files:**
- Modify: `badminton_analysis/posture/system.py` (report field)
- Modify: `static/kestrel.js:740-742`
- Test: `tests/test_posture_gate_strokes.py` (extend), `tests/test_posture_report_fields.py` (create)

**Interfaces:**
- Consumes: `RepWindow.contact_anchor` (Task 4); `gate_info["filtered_overhead"]` (Task 2).
- Produces: per-rep report key `"contact_anchor"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_posture_report_fields.py
"""Anchor provenance must reach the per-rep report.

Task 4 made the serve path depend on shuttle detection quality: with a shuttle the
contact is well anchored, without one it falls back to a coarser signal. Without
this field a clip where the shuttle was rarely detected looks identical to one where
it was always detected.
"""
import numpy as np
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.posture.system import PostureRunner


def _kp(wrist_y):
    """torso = |100 - 250| = 150, so elevation is (100 - wrist_y) / 150.

    wrist_y=40 -> 0.40, an overhead swing. wrist_y=120 -> -0.13, a low one. A serve
    fixture MUST use the low wrist: at 0.40 the Task 2 ceiling gate filters every rep
    and there would be no report to inspect.
    """
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, wrist_y)
    kp[ja.L_SHOULDER] = (60, 90)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def _run(stroke_type, with_shuttle):
    # Overhead strokes need a raised wrist to survive the floor gate; a serve needs a
    # low one to survive the ceiling gate.
    kp = _kp(120 if stroke_type == "serve" else 40)
    racket = ja.infer_racket_head(kp, dominant="right")
    track = []
    for i in range(160):
        wrist = (260.0, 100.0) if i in (40, 110) else (100.0, 100.0)
        shuttle = (100.0, 100.0) if (with_shuttle and i in (40, 110)) else None
        track.append({"frame": i, "wrist": wrist, "shuttle": shuttle})

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": racket, "centroid": (100 + (idx % 5), 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type=stroke_type, dominant="right")
    return runner.run(track, frame_lookup, fps=30)


def test_report_records_shuttle_anchor():
    reports, _reps, _gate = _run("smash", with_shuttle=True)
    assert reports[0]["contact_anchor"] == "shuttle"


def test_report_records_apex_anchor_for_overhead_without_shuttle():
    reports, _reps, _gate = _run("smash", with_shuttle=False)
    assert reports[0]["contact_anchor"] == "apex"


def test_report_records_speed_peak_for_serve_without_shuttle():
    """Serve gets no positional refinement, so the speed peak stands."""
    reports, _reps, _gate = _run("serve", with_shuttle=False)
    assert reports
    assert reports[0]["contact_anchor"] == "speed_peak"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_report_fields.py -q -p no:cacheprovider`
Expected: FAIL with `KeyError: 'contact_anchor'`.

- [ ] **Step 3: Write minimal implementation**

In `PostureRunner.run`, beside the existing `report["overhead_elevation"] = ...` line:

```python
            report["contact_anchor"] = rep.contact_anchor
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_report_fields.py -q -p no:cacheprovider`
Expected: PASS (3 passed).

- [ ] **Step 5: Fix the gate copy in the web UI**

`static/kestrel.js:740-742` currently reads:

```javascript
    if (reps && reps.gated && reps.filtered_non_overhead > 0) {
      lines.push(zh ? ('已排除 ' + reps.filtered_non_overhead + ' 个非头顶挥拍（仅统计高远球头顶动作）')
        : (reps.filtered_non_overhead + ' non-overhead swing(s) excluded (only full overhead clears counted)'));
    }
```

Replace with copy that is true for every gated stroke and states the serve case in the right direction:

```javascript
    if (reps && reps.gated && reps.filtered_non_overhead > 0) {
      // Floor case: an overhead drill (high clear / smash / drop shot) excluded a
      // swing that never got above the shoulder. The old copy said "only full
      // overhead clears counted", which stopped being true once smash and drop
      // shot were gated too.
      lines.push(zh ? ('已排除 ' + reps.filtered_non_overhead + ' 个非头顶挥拍（本项仅统计头顶动作）')
        : (reps.filtered_non_overhead + ' non-overhead swing(s) excluded (this drill counts overhead swings only)'));
    }
    if (reps && reps.gated && reps.filtered_overhead > 0) {
      // Ceiling case: a serve drill excluded a swing for BEING overhead -- the
      // opposite of the message above.
      lines.push(zh ? ('已排除 ' + reps.filtered_overhead + ' 个头顶挥拍（本项仅统计低手动作）')
        : (reps.filtered_overhead + ' overhead swing(s) excluded (this drill counts underarm swings only)'));
    }
```

- [ ] **Step 6: Run the full suite**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ai_handoff.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add badminton_analysis/posture/system.py static/kestrel.js \
        tests/test_posture_report_fields.py
git commit -m "feat(posture): record contact anchor per rep; correct gate copy"
```

---

### Task 6: Regression check on real footage

The only guard on Global Constraint "`high_clear` behaviour must not change". `high_clear` is the sole stroke with frame-verified ground truth, and none of Tasks 1–5 should touch it: it was already gated, already uses the apex fallback, and Task 3's shuttle change only matters when several detections exist in a frame.

**Files:**
- Create: `scripts/check_posture_regression.py`
- Test: none (this is a manual verification script; its output is the evidence)

**Interfaces:**
- Consumes: the shipped `PostureAnalysisSystem` pipeline.
- Produces: a pass/fail report against the recorded rep counts.

- [ ] **Step 1: Write the script**

```python
# scripts/check_posture_regression.py
"""Verify high_clear rep counts are unchanged after the rep-detection changes.

high_clear is the only stroke with frame-verified ground truth, so it is the guard
on "no behaviour change for high_clear". Counts come from the drill_summary.json of
runs made before this work.

Usage:
  python scripts/check_posture_regression.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED = {"highclear1": 16, "IMG_1270": 5, "IMG_9691": 7}


def main():
    failures = []
    for name, want in sorted(EXPECTED.items()):
        path = os.path.join(ROOT, "outputs", name, "posture", "drill_summary.json")
        if not os.path.exists(path):
            print(f"  {name:<14} SKIP (no run at {path})")
            continue
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh).get("rep_count")
        ok = got == want
        print(f"  {name:<14} expected {want:>3}  got {str(got):>4}  "
              f"{'OK' if ok else 'CHANGED'}")
        if not ok:
            failures.append((name, want, got))
    if failures:
        print("\nhigh_clear rep counts CHANGED. Investigate before shipping -- none of")
        print("Tasks 1-5 should alter high_clear. Task 3 can legitimately move a")
        print("CONTACT FRAME when several shuttle detections share a frame, but it")
        print("must not change the rep COUNT.")
        return 1
    print("\nhigh_clear rep counts unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Re-run the three high_clear clips**

Run each through the posture pipeline so `drill_summary.json` is regenerated with the new code:

```bash
.venv\Scripts\python.exe main_posture.py --video-path "videos/highclear1.mp4" --stroke-type high_clear --display false
```

```bash
.venv\Scripts\python.exe main_posture.py --video-path "videos/IMG_1270.mov" --stroke-type high_clear --display false
```

```bash
.venv\Scripts\python.exe main_posture.py --video-path "videos/IMG_9691.MOV" --stroke-type high_clear --display false
```

- [ ] **Step 3: Run the regression check**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B scripts/check_posture_regression.py`
Expected: `high_clear rep counts unchanged.` and exit 0, with 16 / 5 / 7.

If a count changed, **stop and investigate** — do not adjust `EXPECTED` to match. The counts are frame-verified ground truth, not a snapshot.

- [ ] **Step 4: Commit**

```bash
git add scripts/check_posture_regression.py
git commit -m "test(posture): high_clear rep-count regression check"
```

---

## Notes for the implementer

- **Do not add a second elevation threshold.** The design deliberately reuses `OVERHEAD_MIN_ELEVATION` as both floor and ceiling so that no unmeasured constant enters the code. If drop shots turn out to be over-filtered, that is a finding to record against real footage, not a number to invent.
- **Do not let `rep_segmenter` learn about stroke types.** It takes `positional_fallback` as an argument precisely so it stays a signal-processing module that is testable without any stroke vocabulary.
- **The six exact-dict `gate_info` assertions break by design in Task 2.** Update them; do not remove the new counter to make them pass.
- **`filtered_non_overhead` and `filtered_overhead` mean opposite things.** A serve rep counted in the former would be a bug — Task 2's test asserts against exactly that.
- **Both new gate applications are unvalidated** (spec §6). Comments should say so. The mitigation is that filtered counts are visible per run, not that the thresholds are known correct.
