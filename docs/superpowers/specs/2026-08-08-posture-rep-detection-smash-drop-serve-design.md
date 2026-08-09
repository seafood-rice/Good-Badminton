# Posture rep detection for smash, drop shot and serve — design

**Status:** approved 2026-08-08. Supersedes nothing; extends the posture drill mode added in
the posture-skeleton-overlay and coach-report work.

## 0. Premise check (read first — "unsupported" was the wrong diagnosis)

The request was to "add support" for smash, drop shot and serve. Inspection shows all three are
already *selectable* end to end:

- `badminton_analysis/analysis/reference_ranges.py` — `REFERENCE_RANGES` has genuinely
  differentiated specs for all four strokes (smash demands more elbow extension and trunk
  rotation than drop shot; serve the least). `knee_flexion` is deliberately identical across all
  four.
- `badminton_analysis/posture/coach_kb.py` — `STROKES = ("high_clear", "smash", "drop_shot",
  "serve")`.
- `main_posture.py:10` — `choices=["high_clear", "smash", "drop_shot", "serve"]`, and the Kestrel
  wizard offers the same list.

What they lack differs per stroke, and only one of the three gaps is addressed here:

| stroke | rep gate | contact anchor | stroke-specific coach lines | AI quality score |
|---|---|---|---|---|
| `high_clear` | gated | apex fallback valid | 3/12 | yes |
| `smash` | **none** | apex valid (overhead) | 9/12 | no |
| `drop_shot` | **none** | apex valid (overhead) | **1/12** | no |
| `serve` | **none** | **apex is wrong** | 3/12 | no |

Coach-content counts are stroke-specific entries out of 12 (6 metrics x under/over); the
remainder resolve to the `'*'` wildcard, which is present for all 12 pairs. So a drop-shot report
today is almost entirely stroke-agnostic advice.

**This spec addresses only the rep-detection column.** Everything a score or a coaching line says
is downstream of counting the right reps at the right contact frame, so that layer comes first.
Content depth and range calibration are deferred (§5).

## 1. Goal

Rep detection in posture drill mode is correct for `smash`, `drop_shot` and `serve`: overhead
strokes are gated against over-counting, and a serve's contact frame is never anchored by a
heuristic known to be wrong for it.

## 2. Done means

1. `smash` and `drop_shot` runs are subject to the overhead-swing gate, so a non-stroke action
   cannot be counted as a rep.
2. A `serve` run is **not** overhead-gated (which would filter every rep) and is instead gated
   against reps that are clearly overhead strokes.
3. A `serve` rep with no detected shuttle keeps the wrist-speed peak as its contact frame rather
   than moving to the wrist apex.
4. The shuttle used for contact refinement is the detection **nearest the wrist**, not whichever
   box the detector happened to return first.
5. Every rep records which signal anchored its contact frame, so a clip with poor shuttle
   detection is visible in the output rather than silent.
6. The three existing `high_clear` clips produce **identical** rep counts to today
   (`highclear1` 16, `IMG_1270` 5, `IMG_9691` 7). No behaviour change for `high_clear`.

## 3. Design

### 3.1 Extend the overhead gate to smash and drop shot

`badminton_analysis/posture/system.py:59` currently reads:

```python
OVERHEAD_GATED_STROKES = ("high_clear",)  # only these stroke types are gated
```

becomes `("high_clear", "smash", "drop_shot")`.

The gate itself needs no change. `apex_overhead_elevation` (`system.py:83`) is pure geometry —
the maximum over the apex window of `(shoulder_y - wrist_y) / torso` on the dominant side, where
`torso = |shoulder_y - hip_y|`. Nothing in it is specific to a clear. A smash and a drop shot are
both overhead strokes that lift the dominant wrist above the dominant shoulder at the apex, which
is exactly what the measurement tests.

### 3.2 Serve: invert the same threshold rather than invent a new one

A serve is underhand, so its apex elevation is low. Adding `serve` to the gated set would filter
**every** rep. Serve instead gets a ceiling: a rep whose apex elevation reaches
`OVERHEAD_MIN_ELEVATION` is an overhead stroke and does not belong in a serve drill.

This deliberately reuses the single calibrated boundary in both directions:

```python
UNDERHAND_GATED_STROKES = ("serve",)
# No new constant: OVERHEAD_MIN_ELEVATION is the floor for overhead strokes and the
# ceiling for underhand ones. On IMG_1270 real overhead clears score 0.48-0.70 and
# soft/non-overhead actions ~0.26, so 0.35 separates "overhead" from "not overhead"
# regardless of which side of it a given stroke type is supposed to fall on.
```

**The `None` convention is preserved in both directions.** `apex_overhead_elevation` returns
`None` when no frame in the window yields a valid shoulder/wrist/hip measurement, and the caller
treats that as "cannot judge -> keep the rep". The ceiling gate must do the same: `None` keeps the
rep. Only a *measured* elevation at or above the threshold filters a serve rep.

Filtered serve reps are counted **separately** (§3.6), not folded into
`filtered_non_overhead` — a serve rep is filtered *because* it was overhead, so reusing that
field would make its name mean the opposite of what happened.

### 3.3 Shuttle selection: nearest to the wrist, not first returned

`badminton_analysis/posture/system.py:602-606`:

```python
res = ball_model(frame, conf=0.18, verbose=False)[0]
boxes = getattr(res, "boxes", None)
if boxes is not None and boxes.xywh.shape[0] > 0:
    b = boxes.xywh.detach().cpu().numpy()[0]      # <- index 0, highest-confidence
    shuttle = (float(b[0]), float(b[1]))
```

Take the box whose centre is nearest the dominant wrist when a wrist is available, else keep the
current index-0 behaviour. `track["wrist"]` is the *dominant* wrist (`kp[dom_wrist]`), and it is
assigned earlier in the same frame iteration than this block, so the wrist is already known here —
no reordering is needed. This is the third instance of the same defect shape in this codebase
— `predict_location` keeps only the max-area contour and `detect_racket_head` had the same
single-candidate problem — so it is fixed the same way: consider all candidates, choose by a
stated criterion.

Note on the old behaviour: ultralytics returns NMS output ordered by DESCENDING CONFIDENCE, so
index 0 was the *highest-confidence* detection, not an arbitrary one. Proximity-based picking is
still the right fix — the true shuttle is not always the most confident detection when several are
present — but it trades away that confidence signal: it has no confidence tie-break and no
maximum-distance guard, so a low-confidence false positive near the wrist could outrank a
high-confidence true shuttle detected further away. To be revisited when the shuttle path is
enabled (§3.7) and real footage exists to check how often this occurs.

Rationale: the shuttle is the *primary* contact anchor (`rep_segmenter.py:153-167` snaps the
contact frame to the in-window frame where shuttle and wrist are closest). Feeding it an arbitrary
detection when several are present can move the contact frame off the stroke. The effect is
largest exactly where the shuttle sits near the wrist, which is every contact frame.

This change is stroke-agnostic and improves all four stroke types.

### 3.4 Make the positional fallback stroke-appropriate

`rep_segmenter.py:168-186` refines the contact frame to the **wrist apex** (minimum image `y`)
when no shuttle anchors the window. Its own comment states the limitation:

> Overhead-oriented heuristic: for an underhand serve the apex is not the contact.

`segment_reps` gains a keyword:

```python
def segment_reps(track, fps, pre=20, post=15, smooth=3, min_speed_px=5.0,
                 max_reps=50, positional_fallback="apex"):
```

- `positional_fallback="apex"` — current behaviour, used for `high_clear`, `smash`, `drop_shot`.
- `positional_fallback=None` — no positional refinement; the rep keeps the wrist-speed peak the
  candidate scan already produced. Used for `serve`.

The caller — `PostureRunner.run` (`badminton_analysis/posture/system.py:211`) — chooses the value
from `stroke_type`. **`rep_segmenter` gains no
knowledge of stroke types** — it stays a signal-processing module whose behaviour is set by its
arguments, which keeps it independently testable.

Why the speed peak rather than a nadir or a max-reach heuristic: the apex refinement exists only to
stop an overhead rep centring on its faster follow-through. For a serve there is no evidence that
any positional extremum is the contact — the wrist is low throughout, so a nadir may well sit in
the backswing — and no serve footage exists to test one. The speed peak is not known-wrong and
invents nothing.

### 3.5 Record which signal anchored each contact

`RepWindow` gains `contact_anchor`, one of:

| value | meaning |
|---|---|
| `"shuttle"` | snapped to the frame of closest shuttle-wrist approach |
| `"apex"` | no shuttle in window; moved to the wrist apex |
| `"speed_peak"` | no shuttle in window and no positional refinement requested |

Carried into the per-rep report and `drill_reps.jsonl` alongside the existing `contact_frame` and
`overhead_elevation`.

This exists because §3.4 makes the serve path *depend* on shuttle detection quality: with a
shuttle the contact is well anchored, without one it falls back to a coarser signal. Without this
field that difference is invisible, and a clip where the shuttle was rarely detected would look
identical to one where it was always detected.

### 3.6 Gate reporting: a second counter, and UI copy that is currently wrong

`gate_info` gains `filtered_overhead` beside the existing `filtered_non_overhead`:

| key | meaning |
|---|---|
| `filtered_non_overhead` | overhead-gated stroke, rep dropped for being *below* the floor |
| `filtered_overhead` | underhand-gated stroke (serve), rep dropped for being *above* the ceiling |

Both flow into `metadata.json` as today (`build_drill_summary`/`drill_summary.json` carries only
`rep_count` and score aggregates, not the gate counters).

**The existing UI copy becomes incorrect and must change.** `static/kestrel.js:740-742` renders a
hardcoded string:

> `'N non-overhead swing(s) excluded (only full overhead clears counted)'`
> `'已排除 N 个非头顶挥拍（仅统计高远球头顶动作）'`

Two problems once this ships: "only full overhead clears counted" is untrue when the gated stroke
is a smash or a drop shot, and for a serve the message is backwards — those reps were excluded for
*being* overhead. The copy must become stroke-aware, rendering the floor message from
`filtered_non_overhead` and a distinct ceiling message from `filtered_overhead`, in both
languages.

**Six existing tests assert the exact `gate_info` dict** (`test_posture_runner.py` lines 60, 114,
217, 246, 262, 306 and `test_posture_runner_3d.py:191`). Adding a key breaks all of them. That is
expected and they are to be updated deliberately — this is called out so an implementer does not
mistake it for a regression, and does not "fix" it by dropping the new counter.

### 3.7 Known limitation: the shuttle path is inert as shipped

No posture entry point passes a ball model: `PostureAnalysisSystem` defaults
`ball_model_path` to `None`, `main_posture.py`'s `--ball-model` defaults to `None`, and
`app.py`'s posture endpoint never sets one (only match mode does). As a result `pick_shuttle`
never runs in production, the `"shuttle"` contact anchor never occurs, and `contact_anchor` is
currently a per-stroke constant (`"apex"` for the overhead-gated strokes, `"speed_peak"` for
`serve`) rather than the provenance signal §3.5 describes — every rep in a given run gets the
same anchor value regardless of shuttle detection quality. §3.5's stated rationale ("a clip where
the shuttle was rarely detected would look identical to one where it was always detected")
therefore describes a distinction that cannot arise yet, because the shuttle is never detected at
all in posture mode today. Enabling the ball model in posture mode (wiring `--ball-model` through
`app.py`'s posture endpoint, analogous to `--racket-model`/`--quality-model`/`--lift-model`) is
the prerequisite for `contact_anchor` to carry real information. The real-footage regression (§4,
test 9) must be re-run after that change, since shuttle anchoring can move a contact frame and the
existing `high_clear` counts were captured with the shuttle path inert.

## 4. Testing

The gate and the segmenter are pure functions over keypoints and tracks, so the behavioural work
is unit-testable with synthetic data — no video decode.

**Unit**
1. Ceiling gate filters a synthetic rep with elevation 0.6 when `stroke_type="serve"`.
2. Ceiling gate keeps a synthetic rep with elevation 0.2 when `stroke_type="serve"`.
3. Ceiling gate keeps a rep whose elevation is `None` (cannot judge -> keep).
4. Overhead gate now filters a low-elevation rep for `smash` and for `drop_shot`.
5. `positional_fallback=None` leaves the contact on the speed peak; `"apex"` moves it to the
   apex, on the same synthetic track.
6. Shuttle selection picks the nearer of two detections to the wrist, and still works with one.
7. `contact_anchor` is `"shuttle"`, `"apex"` and `"speed_peak"` respectively in those three
   situations.
8. `gate_info` reports the serve ceiling in `filtered_overhead`, leaving
   `filtered_non_overhead` at zero — and the floor case does the reverse. This is the test that
   would fail if someone folded the two counters together (§3.6).

**Regression on real footage**

9. Re-run the three existing `high_clear` clips and assert rep counts are unchanged
   (`highclear1` 16, `IMG_1270` 5, `IMG_9691` 7). This is the guard on §2.6: `high_clear` is the
   only stroke with frame-verified ground truth, and none of these changes should touch it. Note
   that §3.3 *can* legitimately move a contact frame when several shuttle detections are present,
   so the assertion is on rep **counts**; a changed contact frame is investigated, not assumed
   wrong.

## 5. Explicitly deferred (named, not rejected)

- **Coaching content depth.** `drop_shot` has 1 of 12 stroke-specific entries, `high_clear` and
  `serve` 3. Authoring the rest in `en`/`zh-Hans`/`zh-Hant` is content work with no algorithmic
  risk, and belongs after rep counts are trustworthy.
- **Reference-range calibration.** `reference_ranges.py`'s own docstring calls the values
  "indicative first-release starting points … expected to be tuned against literature/labeled
  clips."
- **AI quality scorer for non-clear strokes.** `system.py:506` restricts it to `high_clear`
  because only `quality-high_clear.pt` exists. Training more models is its own project.
- **Front-court net shots (forehand and backhand).** Deferred pending drill footage. Decisions
  already taken and recorded here so they need not be re-litigated: the user selects the drill
  type (two stroke types, `net_shot_forehand` and `net_shot_backhand`, rather than a per-rep
  classifier); the work is validated against a purpose-shot clip; the target is full parity minus
  the AI scorer. Note that a net shot is contacted low and in front, so it needs neither the
  overhead floor nor the serve ceiling but a front-court test of its own, and the apex fallback is
  wrong for it too.

## 6. Recorded as unvalidated

Both new gate applications rest on a threshold calibrated only on `high_clear` footage
(`IMG_1270`). Stated plainly so it is not mistaken for a measured result:

- **`drop_shot`'s 0.35 floor.** A drop shot is a *soft* overhead stroke. Its apex elevation should
  clear 0.35, but a gentle one could land near the boundary and be filtered. No drop-shot footage
  exists to check.
- **`serve`'s 0.35 ceiling.** Reuses the same boundary from the other side and is untested on
  serve footage.

The mitigation is visibility, not confidence: `filtered_non_overhead` is already surfaced in
`metadata.json` and the web UI, and printed per run, so over-filtering shows up in the output
immediately rather than silently losing reps. Both numbers should be re-checked against a
drop-shot clip and a serve clip when those exist, and this section updated with the measurement.

## 7. Out of scope

- Match-mode stroke recognition (BST). This spec is posture drill mode only.
- The `is_court_view` and fps-normalisation gaps recorded in the sub-project B design. Unrelated
  to posture mode.
