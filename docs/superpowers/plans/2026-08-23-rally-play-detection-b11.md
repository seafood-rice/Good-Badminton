# B11 — Rally / Play Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the court-view gate that admits 0.66% of a match with a self-calibrating one,
and replace the in-loop rally state machine that calls 98% of a clip one rally with a post-loop
segmenter that declares which signal it used and how much to trust it.

**Architecture:** Two mechanisms at two points in the pipeline. **C0/C1** — a strided pre-scan
derives a per-video NCC cut (`median − k·MAD`), and the per-frame gate scores a 480px-wide
downscale against a once-downscaled template. **C2** — a new pure module,
`badminton_analysis/stroke/rallies.py`, segments rallies *after* the frame loop from the
already-recorded per-frame track, choosing between a shuttle signal, a swing signal, and a
degraded coarse-window fallback, and recording which it chose and why. Nothing in the frame
loop needs rallies live, so segmenting offline costs nothing and can use non-causal windows.

**Tech Stack:** Python 3.11 + OpenCV (`cv2.matchTemplate`, `TM_CCOEFF_NORMED`), NumPy, pytest.
No new dependencies. No learned model is added.

---

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the
design spec's §13 and §12a.

- **Never-fatal.** Neither the gate's calibration nor the segmenter may crash the pipeline;
  every failure path leaves the match video and all other outputs intact.
- **Zero regression when pinned.** With `--court-view-threshold 0.75` and the signal forced,
  behaviour matches today's.
- **Honest reporting.** Coverage, chosen signal, calibration, and suppressed artifacts are
  recorded in `metadata.json` / `rally_segments.json`, not inferred by the reader.
- **Cost-aware, not CPU-only.** This machine has an RTX 4090 and a CUDA torch install. Every
  per-frame cost claim in this plan is measured, never estimated.
- **Never commit** model weights, datasets, or the pre-existing local dirt. `outputs/`,
  `weights/`, `*.pt` are gitignored. The untracked `BirdEye Prototype.html` at the repo root
  and the deleted `assets/*.gif|png` paths are pre-existing local state: **never** stage them.
- **Windows env:** run everything as
  `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider --basetemp <unique>`.
  Kill background processes by PID.
- **No writes to `main`.** Work proceeds on `claude/b11-rally-detection` (already created from
  `origin/main`, upstream deliberately unset so no push can target `main`).
- **`tests/test_ai_handoff.py` is known-flaky under long full-suite runs** and unrelated to app
  code. Do not mistake it for a B11 regression; re-run it alone to confirm.
- **Decision §12a-C (owner, 2026-08-23):** when no rally signal qualifies, emit **uniform coarse
  windows marked degraded and withhold stroke recognition on them**. Never zero rallies, never
  one whole-video rally, and never a UI claim of "N rallies" for a degraded segmentation.
- **Decision §12a-A (owner, 2026-08-23):** true multi-camera TV broadcast stays in scope. Its
  shot-boundary component is built and hermetically tested here, and its real-footage
  validation is recorded as **not run, blocked on a genuine broadcast sample** — no on-disk
  footage has a cut in it.
- **Measured limits that must not be overstated (§0.16):** the swing signal is a *good rally
  detector and a poor boundary estimator* — F1 0.644, 10/11 rallies found, **+1.90 s median
  start lag**, rally count inflated +36%, total rally time +41%. It may be described as
  measured; it must never be described as accurate boundaries.

---

## File Structure

| Path | Responsibility |
|---|---|
| `badminton_analysis/stroke/rallies.py` | **New.** Pure segmentation: activity signals, gap-close/min-length runs, static-artifact suppression, signal selection, degraded fallback, provenance. No cv2, no file I/O, no pipeline state. |
| `badminton_analysis/stroke/shot_boundary.py` | **New.** Hard-cut detection for the true-broadcast class (decision A). Separate file because it is the one component with no real-footage validation path — keeping it isolated keeps that caveat legible. |
| `badminton_analysis/system.py` | Modified. C0 calibration, C1 gate rewrite, template pre-downscale, post-loop `segment_rallies` call, `rally_segments.json` provenance, degraded-run stroke-recognition skip. |
| `badminton_analysis/court/mapper.py` | Modified. `annotate_court` gains a headless mode so unattended runs cannot hang. |
| `app.py` | Modified. Stop reporting a bare `rally_count` for a segmentation that measured +36% count inflation. |
| `scripts/check_rally_gate.py` | **New.** Controller-run harness: gate pass-fractions on both videos, and `segment_rallies` replayed over recorded `detections.jsonl`. Not part of the committed suite. |
| `tests/test_rally_segmentation.py` | **New.** C2's hermetic tests. |
| `tests/test_court_view_gate.py` | **New.** C0/C1's hermetic tests. |
| `tests/test_shot_boundary.py` | **New.** Shot-boundary hermetic tests. |
| `tests/test_court_annotation_headless.py` | **New.** The headless-annotation regression test. |

**Not touched:** `badminton_analysis/visualization/player_positions.py:330-351` has its own
independent frame-gap rally detector used only for position visualisations. Unifying it is out
of scope (spec §10), and it will disagree with `rally_segments.json` — that is pre-existing and
accepted.

---

## A pre-flight correction the implementer must know

The spec's §5 describes C2's swing activity as *"0.5s-smoothed `max` over both players'
**racket-point** displacement"*, consuming `_analysis_track_both`. **The validated
implementation does not use racket points.** `scripts/score_rally_labels.py:66-108` — the code
that produced §0.16's F1 0.644 against human labels — uses, per side, the **mean of whichever
of the left/right wrists are present**, and takes `max(lower, upper)` of their speeds.

`_analysis_track_both` (`system.py:735-740`) carries only `racket_lower`, `racket_upper`,
`shuttle` — **no wrists**. So implementing §5 literally would ship a variant that inherits none
of §0.16's measurement. This plan therefore:

1. adds `wrist_lower` / `wrist_upper` to `_analysis_track_both` (Task 5), and
2. gates the swing signal on **reproducing §0.16's numbers** against the human labels still on
   disk at `outputs/b11-labelling/LABELS.md` (Task 6).

This also resolves §0.15's Defect 1 ("the recipe as written is under-specified — §0.8 does not
say how left/right wrists are combined"): the combining rule is pinned here as *mean of present
wrists per side, then `max` across sides*.

---

### Task 1: Headless-safe court annotation

The 4h43m hang that consumed the B1 validation run. `annotate_court` takes **no** display
parameter, opens a `cv2` window, and spins in `while True: cv2.waitKey(1)` with no timeout, so
`--display false` structurally cannot suppress it. Every later task's validation needs
unattended runs, so this goes first.

**Files:**
- Modify: `badminton_analysis/court/mapper.py:116` (`annotate_court` signature and the
  auto-accept loop at `:136-140`)
- Modify: `badminton_analysis/system.py:924` (the single call site)
- Test: `tests/test_court_annotation_headless.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `annotate_court(image, auto_preview_path=None, interactive=True)`. When
  `interactive=False` and auto-detection succeeded, it returns the auto corners immediately
  without creating a window or waiting for a key. Return type is unchanged:
  `(original_corners, original_roi_corners, mid_height_int)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_court_annotation_headless.py
"""annotate_court must never block on a keypress when non-interactive.

Regression test for the defect that silently consumed 4h43m of the B1
validation run: the auto-detect branch opened a cv2 window and spun in
`while True: cv2.waitKey(1)` with no timeout and no way for a caller to
opt out.
"""
import numpy as np
import pytest

from badminton_analysis.court import mapper


def test_non_interactive_accepts_auto_detection_without_gui(monkeypatch):
    corners = [(10, 20), (90, 20), (95, 70), (5, 70)]
    monkeypatch.setattr(mapper, "auto_detect_court_corners",
                        lambda img: (corners, None, {}))
    monkeypatch.setattr(mapper, "compute_expanded_roi",
                        lambda c, shape: [(0, 0), (99, 99)])
    monkeypatch.setattr(mapper, "render_auto_court_preview",
                        lambda *a, **k: np.zeros((720, 1080, 3), np.uint8))

    def _explode(*a, **k):
        raise AssertionError("non-interactive annotate_court touched the GUI")

    for name in ("namedWindow", "imshow", "waitKey", "destroyWindow"):
        monkeypatch.setattr(mapper.cv2, name, _explode)

    image = np.zeros((1080, 1920, 3), np.uint8)
    got_corners, got_roi, mid = mapper.annotate_court(image, interactive=False)

    assert len(got_corners) == 4
    assert len(got_roi) == 2
    assert isinstance(mid, int)


def test_non_interactive_raises_when_auto_detection_fails(monkeypatch):
    """No corners and no human to ask: fail loudly rather than hang or guess."""
    monkeypatch.setattr(mapper, "auto_detect_court_corners",
                        lambda img: (None, None, {}))
    image = np.zeros((1080, 1920, 3), np.uint8)
    with pytest.raises(RuntimeError, match="auto court detection failed"):
        mapper.annotate_court(image, interactive=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_court_annotation_headless.py -v -p no:cacheprovider
```
Expected: FAIL — `annotate_court() got an unexpected keyword argument 'interactive'`.

- [ ] **Step 3: Add the headless branch**

In `badminton_analysis/court/mapper.py`, change the signature and insert the non-interactive
branch *before* any `cv2` window call:

```python
def annotate_court(image, auto_preview_path=None, interactive=True):
    """Resolve the court quad, by auto-detection and optional human review.

    ``interactive=False`` is for unattended runs (background jobs, the
    validation harness, CI): auto-detection is accepted without opening a
    window or waiting for a keypress, and a detection failure raises instead
    of falling through to manual annotation. The interactive path is
    unchanged.
    """
```

Then, immediately after `auto_preview` is written and before `cv2.namedWindow`:

```python
        if not interactive:
            court_mapper = CourtMapper(auto_corners)
            _, auto_mid_height = court_mapper.draw_court_overlay(base_image)
            scale_x = original_width / fixed_size[0]
            scale_y = original_height / fixed_size[1]
            original_corners = [(int(x * scale_x), int(y * scale_y)) for x, y in auto_corners]
            original_roi_corners = [(int(x * scale_x), int(y * scale_y))
                                    for x, y in auto_roi_corners]
            return original_corners, original_roi_corners, int(auto_mid_height * scale_y)
```

And in the `elif auto_preview_path:` / no-corners branch, before manual annotation begins:

```python
        if not interactive:
            raise RuntimeError(
                "auto court detection failed and annotate_court is non-interactive; "
                "supply --court-annotations or run once interactively to annotate")
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_court_annotation_headless.py -v -p no:cacheprovider
```
Expected: PASS (2 passed).

- [ ] **Step 5: Thread the flag through the one call site**

`badminton_analysis/system.py:924` currently reads:

```python
        corners, roi_corners, mid_height = annotate_court(template_color, auto_preview_path=auto_preview_path)
```

Change to pass the system's existing display setting:

```python
        corners, roi_corners, mid_height = annotate_court(
            template_color, auto_preview_path=auto_preview_path,
            interactive=bool(self.show_display))
```

- [ ] **Step 6: Verify no existing test regressed**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ai_handoff.py
```
Expected: PASS, same count as before this task plus 2.

- [ ] **Step 7: Commit**

```bash
git add badminton_analysis/court/mapper.py badminton_analysis/system.py tests/test_court_annotation_headless.py
git commit -m "fix(court): never block on a keypress in an unattended run"
```

---

### Task 2: C0 — self-calibrating court-view cut

**Files:**
- Modify: `badminton_analysis/system.py` (new constants near `COURT_VIEW_CHECK_INTERVAL` at
  `:18`; new `_calibrate_court_view`; `_write_metadata` at `:366-397`)
- Test: `tests/test_court_view_gate.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `system.COURT_VIEW_MAD_K = 4.0`, `system.COURT_VIEW_MIN_MEDIAN = 0.30`,
    `system.COURT_VIEW_SCORE_WIDTH = 480`, `system.COURT_VIEW_FALLBACK_CUT = 0.75`
  - `courtview_cut_from_scores(scores, k=COURT_VIEW_MAD_K) -> dict` — a **module-level pure
    function** with keys `{"cut", "median", "mad", "samples", "method", "k"}`. Pure so it can be
    tested without a video; `_calibrate_court_view` is the thin I/O wrapper around it.
  - `downscale_for_gate(gray, width=COURT_VIEW_SCORE_WIDTH) -> ndarray` and
    `self._court_view_score(gray_frame, template_small) -> float` — the shared scoring
    primitive. **Defined here, not in Task 3**, because this task's `_calibrate_court_view`
    already calls it; splitting them would leave Task 2 broken at runtime until Task 3 landed.
  - `self.court_view_cut` (float) and `self.court_view_calibration` (dict), consumed by Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_court_view_gate.py
"""C0/C1: the court-view gate calibrates itself per video.

Today's gate uses one global 0.75 cutoff on a similarity score whose
absolute scale is video-dependent, which admitted 0.66% of frames on the
Axelsen match (design spec §0.3). These tests pin the replacement.
"""
import numpy as np

from badminton_analysis import system


def test_cut_is_median_minus_k_robust_sd():
    # 100 court frames near 0.80 plus 5 clear outliers near 0.10.
    scores = [0.80] * 100 + [0.10] * 5
    out = system.courtview_cut_from_scores(scores, k=4.0)
    # MAD of this set is 0.0 -> the robust SD collapses, so the cut must fall
    # back to the median rather than emitting median - 0 and admitting nothing
    # below it.
    assert out["median"] == 0.80
    assert out["cut"] <= 0.80
    assert out["samples"] == 105
    assert out["method"] == "median-4.0mad"
    assert out["k"] == 4.0


def test_cut_separates_play_from_non_play():
    rng = np.random.default_rng(11)
    play = list(0.80 + 0.02 * rng.standard_normal(400))
    non_play = list(0.30 + 0.02 * rng.standard_normal(40))
    out = system.courtview_cut_from_scores(play + non_play, k=4.0)
    assert min(play) > out["cut"] > max(non_play)


def test_template_mismatch_passes_everything():
    """A template that does not match this video must lose the gate, not the run."""
    out = system.courtview_cut_from_scores([0.05, 0.07, 0.06, 0.04], k=4.0)
    assert out["cut"] == float("-inf")
    assert out["method"] == "template_mismatch_pass_all"


def test_no_samples_falls_back_to_the_shipped_constant():
    out = system.courtview_cut_from_scores([], k=4.0)
    assert out["cut"] == system.COURT_VIEW_FALLBACK_CUT
    assert out["method"] == "fallback_constant"
    assert out["samples"] == 0


def test_cut_is_clamped_to_a_usable_range():
    out = system.courtview_cut_from_scores([0.99] * 50 + [0.98] * 50, k=4.0)
    assert 0.0 <= out["cut"] <= 0.95


def _fake_system():
    """A bare instance with only the attributes the gate touches."""
    cls = system.BadmintonAnalysisSystem
    s = cls.__new__(cls)
    s.court_view_cut = 0.5
    s._court_view_cached = None
    s._court_view_last_frame = -10
    return s


def test_downscaled_scoring_separates_a_match_from_a_mismatch():
    rng = np.random.default_rng(3)
    template = rng.integers(0, 255, (720, 1280), dtype=np.uint8)
    mismatch = rng.integers(0, 255, (720, 1280), dtype=np.uint8)

    s = _fake_system()
    small = system.downscale_for_gate(template)
    assert s._court_view_score(template.copy(), small) > 0.9
    assert s._court_view_score(mismatch, small) < 0.5


def test_downscale_preserves_aspect_ratio_and_never_upscales():
    tall = np.zeros((2160, 3840), np.uint8)
    out = system.downscale_for_gate(tall, width=480)
    assert out.shape == (270, 480)
    small = np.zeros((90, 160), np.uint8)
    assert system.downscale_for_gate(small, width=480).shape == (90, 160)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_court_view_gate.py -v -p no:cacheprovider
```
Expected: FAIL — `module 'badminton_analysis.system' has no attribute 'courtview_cut_from_scores'`.

- [ ] **Step 3: Add the constants, the scoring primitive, and the pure cut function**

Next to `COURT_VIEW_CHECK_INTERVAL = 3` at `badminton_analysis/system.py:18`:

```python
COURT_VIEW_SCORE_WIDTH = 480      # downscale width for the gate's NCC (§0.9 signal D)
COURT_VIEW_MAD_K = 4.0            # cut = median - k * 1.4826 * MAD (§0.6)
COURT_VIEW_MIN_MEDIAN = 0.30      # below this the template is not of this video
COURT_VIEW_FALLBACK_CUT = 0.75    # the historically shipped constant
COURT_VIEW_CALIBRATION_STRIDE_SEC = 0.5   # ~2 samples/second
COURT_VIEW_CALIBRATION_MAX_SAMPLES = 1500
```

Then, at module level, the scoring primitive both C0 and C1 share:

```python
def downscale_for_gate(gray, width=COURT_VIEW_SCORE_WIDTH):
    """Downscale a grayscale image to ``width``, preserving aspect ratio.

    The gate compares whole-frame framing, not fine court lines, so 480px
    wide is ample and ~10x cheaper than full resolution (§0.9 signal D).
    Never upscales -- an already-small frame is returned untouched.
    """
    h, w = gray.shape[:2]
    if w <= width:
        return gray
    scale = width / float(w)
    return cv2.resize(gray, (width, max(1, int(round(h * scale)))),
                      interpolation=cv2.INTER_AREA)
```

and, as a method on `BadmintonAnalysisSystem`:

```python
    def _court_view_score(self, gray_frame, template_small):
        """Whole-frame NCC at COURT_VIEW_SCORE_WIDTH. Shared by C0 and C1."""
        small = downscale_for_gate(gray_frame)
        if small.shape != template_small.shape:
            template_small = cv2.resize(template_small,
                                        (small.shape[1], small.shape[0]),
                                        interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(small, template_small, cv2.TM_CCOEFF_NORMED)
        return float(np.max(result))
```

and the pure cut function:

```python
def courtview_cut_from_scores(scores, k=COURT_VIEW_MAD_K):
    """Per-video court-view cut from a sample of whole-frame NCC scores.

    Returns the audit record written to metadata.json's ``court_view`` key.
    Pure and video-free so the branch logic is testable without decoding.

    Three non-normal outcomes, each named rather than silent:
      * no samples          -> the shipped 0.75 constant
      * median below floor  -> template is not of this video; pass everything
      * MAD collapses to 0  -> use the median itself, not median - 0
    """
    vals = sorted(float(s) for s in scores)
    n = len(vals)
    if n == 0:
        return {"cut": COURT_VIEW_FALLBACK_CUT, "median": None, "mad": None,
                "samples": 0, "method": "fallback_constant", "k": float(k)}

    median = vals[n // 2] if n % 2 else 0.5 * (vals[n // 2 - 1] + vals[n // 2])
    if median < COURT_VIEW_MIN_MEDIAN:
        return {"cut": float("-inf"), "median": median, "mad": None,
                "samples": n, "method": "template_mismatch_pass_all", "k": float(k)}

    devs = sorted(abs(v - median) for v in vals)
    mad = devs[n // 2] if n % 2 else 0.5 * (devs[n // 2 - 1] + devs[n // 2])
    cut = median - float(k) * 1.4826 * mad if mad > 0 else median
    cut = max(0.0, min(0.95, cut))
    return {"cut": cut, "median": median, "mad": mad, "samples": n,
            "method": f"median-{float(k)}mad", "k": float(k)}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_court_view_gate.py -v -p no:cacheprovider
```
Expected: PASS (7 passed).

- [ ] **Step 5: Add the pre-scan wrapper**

Add to `BadmintonAnalysisSystem`, called once after `_load_template` and before the frame loop:

```python
    def _calibrate_court_view(self, video_path, template_small, fps):
        """Strided pre-scan deriving this video's court-view cut.

        Cost: ~1,500 samples x ~1.5 ms of NCC plus their decode, order 10-20 s
        once per run. Never fatal -- any failure falls back to the shipped
        constant and says so.
        """
        if self.court_view_threshold_override is not None:
            self.court_view_cut = float(self.court_view_threshold_override)
            self.court_view_calibration = {
                "cut": self.court_view_cut, "median": None, "mad": None,
                "samples": 0, "method": "manual", "k": None}
            return

        scores = []
        cap = None
        try:
            cap = cv2.VideoCapture(video_path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            stride = max(1, int(round(COURT_VIEW_CALIBRATION_STRIDE_SEC * (fps or 30))))
            if total > 0:
                stride = max(stride, total // COURT_VIEW_CALIBRATION_MAX_SAMPLES or 1)
            idx = 0
            while len(scores) < COURT_VIEW_CALIBRATION_MAX_SAMPLES:
                ok, frame = cap.read()
                if not ok:
                    break
                if idx % stride == 0:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    scores.append(self._court_view_score(gray, template_small))
                idx += 1
        except Exception as exc:
            print(f"Court-view calibration failed, using {COURT_VIEW_FALLBACK_CUT}: {exc}")
            scores = []
        finally:
            if cap is not None:
                cap.release()

        self.court_view_calibration = courtview_cut_from_scores(scores)
        self.court_view_cut = self.court_view_calibration["cut"]
        print(f"Court-view gate: cut={self.court_view_cut} "
              f"({self.court_view_calibration['method']}, "
              f"{self.court_view_calibration['samples']} samples)")
```

In `__init__`, initialise `self.court_view_threshold_override = court_view_threshold`
(new constructor keyword, default `None`), `self.court_view_cut = COURT_VIEW_FALLBACK_CUT`,
and `self.court_view_calibration = None`. Add `--court-view-threshold` to the CLI, forwarded
to that keyword.

- [ ] **Step 6: Record it in metadata.json**

In `_write_metadata` (`system.py:366-397`), add a sibling key to `"court"`:

```python
            "court_view": self.court_view_calibration,
```

- [ ] **Step 7: Run the suite**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ai_handoff.py
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add badminton_analysis/system.py tests/test_court_view_gate.py
git commit -m "feat(court-view): derive the gate's cut per video instead of a global 0.75"
```

---

### Task 3: C1 — downscaled gate, evaluated every frame

**Files:**
- Modify: `badminton_analysis/system.py:1001-1016` (`is_court_view`, `_court_view_for_frame`),
  `:884-897` (`_load_template`), `:18` (`COURT_VIEW_CHECK_INTERVAL`), `:416` (the call site)
- Test: `tests/test_court_view_gate.py` (extend)

> **Line numbers are as of commit `397844c`.** `is_court_view` is at **1001**, not the
> `952-967` the design spec quotes — the spec's numbers are stale. Re-grep before editing;
> do not trust either number blindly.

**Interfaces:**
- Consumes: `self.court_view_cut`, `downscale_for_gate`, `self._court_view_score` (all Task 2).
- Produces:
  - `_load_template(...) -> (template_gray, template_color, template_small)` — **a three-value
    return; every call site must be updated.**
  - `is_court_view(frame, template_gray, threshold=None)` — `threshold=None` means "use the
    calibrated cut". The keyword survives for the existing tests and the CLI override.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_court_view_gate.py`:

```python
def test_explicit_threshold_still_overrides_the_calibrated_cut():
    rng = np.random.default_rng(4)
    template = (rng.integers(0, 255, (720, 1280), dtype=np.uint8))
    s = _fake_system()
    small = system.downscale_for_gate(template)
    # A perfect match scores ~1.0, so a 0.99 bar passes and a 1.01 bar cannot.
    assert s.is_court_view(template.copy(), small, threshold=0.99) is True
    assert s.is_court_view(template.copy(), small, threshold=1.01) is False


def test_gate_is_evaluated_every_frame():
    assert system.COURT_VIEW_CHECK_INTERVAL == 1


def test_pass_all_cut_admits_everything():
    """A template that is not of this video loses the gate, not the run."""
    rng = np.random.default_rng(5)
    s = _fake_system()
    s.court_view_cut = float("-inf")
    template = rng.integers(0, 255, (720, 1280), dtype=np.uint8)
    noise = rng.integers(0, 255, (720, 1280), dtype=np.uint8)
    assert s.is_court_view(noise, system.downscale_for_gate(template)) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_court_view_gate.py -v -p no:cacheprovider
```
Expected: FAIL — `is_court_view()` still requires a positional threshold default of `0.75` and
ignores `court_view_cut`; `COURT_VIEW_CHECK_INTERVAL` is still 3.

- [ ] **Step 3: Rewrite the gate**

Replace `is_court_view` / `_court_view_for_frame` (`:1001-1016`). `_court_view_score` and
`downscale_for_gate` already exist from Task 2 — **do not redefine them**:

```python
    def is_court_view(self, frame, template_gray, threshold=None):
        """Whether the court is in view, by whole-frame NCC at 480px wide.

        ``threshold=None`` uses this video's calibrated cut from
        _calibrate_court_view(); an explicit value overrides it (the CLI
        override and the existing hermetic tests both rely on this).
        """
        cut = self.court_view_cut if threshold is None else float(threshold)
        if cut == float("-inf"):
            return True
        return self._court_view_score(frame, template_gray) >= cut

    def _court_view_for_frame(self, gray_frame, template_gray, frame_count):
        """Evaluate the gate, honouring COURT_VIEW_CHECK_INTERVAL.

        The interval is 1 (every frame) because the downscaled gate costs
        1.4-1.7 ms -- cheaper than the old full-resolution gate even without
        striding -- so the 2-frame staleness is no longer worth paying for.
        The constant stays so a slower future footage class can raise it.
        """
        if COURT_VIEW_CHECK_INTERVAL <= 1:
            return self.is_court_view(gray_frame, template_gray)
        cached = getattr(self, "_court_view_cached", None)
        last_frame = getattr(self, "_court_view_last_frame", -10)
        if cached is None or frame_count - last_frame >= COURT_VIEW_CHECK_INTERVAL:
            self._court_view_cached = self.is_court_view(gray_frame, template_gray)
            self._court_view_last_frame = frame_count
        return self._court_view_cached
```

Set `COURT_VIEW_CHECK_INTERVAL = 1` at `:18`.

- [ ] **Step 4: Downscale the template once at load**

In `_load_template` (`:884-897`), before the return:

```python
        template_small = downscale_for_gate(template_gray)
        return template_gray, template_color, template_small
```

Update the call site at `:293` to unpack three values and pass `template_small` (not
`template_gray`) into the frame loop's gate calls, and into `_calibrate_court_view`.

> **Watch for this:** `template_gray` is also passed to `_process_frame` and onward for
> non-gate uses. Grep every `template_gray` reference before changing the parameter that
> reaches `_court_view_for_frame` — pass `template_small` to the gate and leave other
> consumers on the full-resolution template.

- [ ] **Step 5: Run tests**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ai_handoff.py
```
Expected: PASS.

- [ ] **Step 6: Measure the per-frame cost, do not assume it**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "import cv2,numpy as np,time; from badminton_analysis.system import downscale_for_gate; g=np.random.randint(0,255,(2160,3840),dtype=np.uint8); t=downscale_for_gate(g); s=downscale_for_gate(g); t0=time.perf_counter(); [cv2.matchTemplate(downscale_for_gate(g),t,cv2.TM_CCOEFF_NORMED) for _ in range(50)]; print('ms/frame', (time.perf_counter()-t0)/50*1000)"
```
Expected: order 1-3 ms/frame at 4K. **If it exceeds today's amortised cost** (the old gate at
`COURT_VIEW_CHECK_INTERVAL = 3`), done-means 4 fails — record the number and raise the interval
rather than shipping a regression.

- [ ] **Step 7: Commit**

```bash
git add badminton_analysis/system.py tests/test_court_view_gate.py
git commit -m "feat(court-view): score the gate on a 480px downscale, every frame"
```

---

### Task 4: C2 — the segmentation core

Pure, signal-agnostic run detection: threshold an activity series, merge runs across short
gaps, drop short runs. Everything is in **seconds**, resolved to frames by the caller, so
done-means 8 (fps invariance) holds from the start rather than being retrofitted.

**Files:**
- Create: `badminton_analysis/stroke/rallies.py`
- Test: `tests/test_rally_segmentation.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `GAP_SEC = 1.0`, `MIN_LEN_SEC = 2.0`, `SWING_FRAC = 0.25`, `SMOOTH_SEC = 0.5`
  - `smooth(values, window) -> list[float]` — trailing mean over `window` samples
  - `segments_from_activity(times, activity, *, frac=SWING_FRAC, gap_sec=GAP_SEC,
    min_len_sec=MIN_LEN_SEC) -> list[tuple[float, float]]` — `(start_sec, end_sec)` pairs
  - Later tasks call both.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rally_segmentation.py
"""C2: rally segmentation from a recorded per-frame track.

Boundaries are defined in SECONDS throughout, so the same temporal pattern
at 30 and 60 fps must produce the same answer (design spec done-means 8).
"""
import pytest

from badminton_analysis.stroke import rallies


def _burst_series(fps, pattern):
    """(times, activity) from a list of (seconds, level) spans."""
    times, act, t = [], [], 0.0
    for dur, level in pattern:
        for _ in range(int(round(dur * fps))):
            times.append(t)
            act.append(level)
            t += 1.0 / fps
    return times, act


def test_two_bursts_separated_by_a_long_gap_are_two_segments():
    times, act = _burst_series(60, [(3.0, 1.0), (3.0, 0.0), (3.0, 1.0)])
    out = rallies.segments_from_activity(times, act)
    assert len(out) == 2


def test_a_gap_shorter_than_gap_sec_is_closed():
    times, act = _burst_series(60, [(3.0, 1.0), (0.5, 0.0), (3.0, 1.0)])
    out = rallies.segments_from_activity(times, act, gap_sec=1.0)
    assert len(out) == 1


def test_a_burst_shorter_than_min_len_sec_is_dropped():
    times, act = _burst_series(60, [(1.0, 1.0), (5.0, 0.0)])
    out = rallies.segments_from_activity(times, act, min_len_sec=2.0)
    assert out == []


@pytest.mark.parametrize("fps", [30, 60])
def test_boundaries_are_fps_invariant_in_seconds(fps):
    times, act = _burst_series(fps, [(1.0, 0.0), (4.0, 1.0), (3.0, 0.0), (4.0, 1.0)])
    out = rallies.segments_from_activity(times, act)
    assert len(out) == 2
    assert out[0][0] == pytest.approx(1.0, abs=1.5 / fps)
    assert out[0][1] == pytest.approx(5.0, abs=1.5 / fps)


def test_flat_activity_yields_no_segments():
    times, act = _burst_series(60, [(10.0, 0.0)])
    assert rallies.segments_from_activity(times, act) == []


def test_smooth_is_a_trailing_mean():
    assert rallies.smooth([0.0, 0.0, 3.0, 3.0], 2) == pytest.approx([0.0, 0.0, 1.5, 3.0])
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -v -p no:cacheprovider
```
Expected: FAIL — `No module named 'badminton_analysis.stroke.rallies'`.

- [ ] **Step 3: Implement the core**

```python
# badminton_analysis/stroke/rallies.py
"""Rally segmentation over an already-recorded per-frame analysis track.

Runs AFTER the frame loop, not inside it. Nothing in the loop consumes rally
segments live -- they are written post-loop, and everything that needs them
(per-rally BST, rally_id, coverage metadata) happens later still -- so
segmenting offline costs nothing, may use non-causal windows, and can be
re-run without re-processing the video.

Measured limits, which callers must not overstate (design spec §0.16, scored
against human labels on the owner's own footage): the swing signal finds 10 of
11 rallies but bounds them poorly -- frame-level F1 0.644, a +1.90 s median
start lag, rally count inflated +36%, total rally time +41%. It is fit for
deciding which spans deserve expensive downstream work. It is NOT fit for
defining authoritative rally windows, and a consumer needing the serve inside
its window must pad the start by at least ~2 s.
"""

GAP_SEC = 1.0
MIN_LEN_SEC = 2.0
SWING_FRAC = 0.25
SMOOTH_SEC = 0.5

# As-shipped values, deliberately NOT the fitted ones. An 84-point grid over
# (swing_frac, gap_sec, min_len_sec) buys +3.5 F1 points, fitted on 11 rallies
# from a single video -- a real overfitting risk -- and its min_len_sec of 3.0
# would discard genuinely short rallies on other footage (§0.16).


def smooth(values, window):
    """Trailing mean over ``window`` samples; window<=1 is a no-op copy."""
    w = max(1, int(window))
    if w == 1:
        return [float(v) for v in values]
    out, run = [], 0.0
    for i, v in enumerate(values):
        run += float(v)
        if i >= w:
            run -= float(values[i - w])
        out.append(run / min(i + 1, w))
    return out


def _p99_5(values):
    srt = sorted(values)
    if not srt:
        return 0.0
    return srt[min(len(srt) - 1, int(0.995 * len(srt)))]


def segments_from_activity(times, activity, *, frac=SWING_FRAC,
                           gap_sec=GAP_SEC, min_len_sec=MIN_LEN_SEC):
    """Active spans of ``activity``, as (start_sec, end_sec) pairs.

    The threshold is self-relative -- ``frac`` of this video's own p99.5 --
    the same idiom as rep_segmenter's PEAK_FLOOR_FRAC, so it transfers across
    footage whose absolute pixel speeds differ by a perspective factor.
    """
    if len(times) != len(activity):
        raise ValueError("times and activity must be the same length")
    n = len(activity)
    if n == 0:
        return []

    thr = float(frac) * _p99_5(activity)
    if thr <= 0.0:
        return []
    active = [float(v) >= thr for v in activity]

    runs, i = [], 0
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1]:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1

    merged = []
    for run in runs:
        if merged and (times[run[0]] - times[merged[-1][1]]) <= gap_sec:
            merged[-1][1] = run[1]
        else:
            merged.append(run)

    return [(times[a], times[b]) for a, b in merged
            if (times[b] - times[a]) >= min_len_sec]
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -v -p no:cacheprovider
```
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/rallies.py tests/test_rally_segmentation.py
git commit -m "feat(rallies): fps-invariant activity segmentation core"
```

---

### Task 5: The swing signal, with perspective-correct teleport caps

§0.15's Defect 2: the teleport cap is in absolute pixels applied across a 5.7× perspective
gradient — 106.3 px/m at the far baseline vs 603.8 px/m at the near one — so a fixed
`max(100 px, …)` is about right for the near player and ~4.7× too loose for the far one. Derive
each side's cap from that side's own px/m instead. `badminton_analysis/court/scale.py` already
provides exactly this.

**Files:**
- Modify: `badminton_analysis/stroke/rallies.py`
- Modify: `badminton_analysis/system.py:700-740` (`_capture_analysis_frame`: record wrists)
- Test: `tests/test_rally_segmentation.py` (extend)

**Interfaces:**
- Consumes: `smooth`, `segments_from_activity` (Task 4);
  `badminton_analysis.court.scale.PerspectiveScale` (`from_quad(quad)`, `px_per_m(y)`).
- Produces:
  - `SPRINT_CAP_MPS = 12.0`
  - `side_speed(points, cap_px) -> list[float]`
  - `swing_activity(track, fps, scale=None) -> list[float]` — activity aligned to `track`
  - `_analysis_track_both` records gain `wrist_lower` / `wrist_upper` (mean of whichever of
    COCO keypoints 9/10 are valid for that side, or `None`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rally_segmentation.py`:

```python
class _Scale:
    """px_per_m increasing with image row, like a real court view."""
    def px_per_m(self, y):
        return 100.0 + y


def test_far_player_cap_is_tighter_than_near_player_cap():
    """§0.15 Defect 2: one absolute-pixel cap is ~4.7x too loose for the far player."""
    scale = _Scale()
    far = rallies.teleport_cap_px(scale, y=10.0, fps=60.0)
    near = rallies.teleport_cap_px(scale, y=500.0, fps=60.0)
    assert far < near
    # 12 m/s at 110 px/m and 60 fps -> 22 px/frame.
    assert far == pytest.approx(12.0 * 110.0 / 60.0, rel=1e-6)


def test_speed_rejects_deltas_above_the_cap():
    pts = [(0.0, 0.0), (5.0, 0.0), (500.0, 0.0), (505.0, 0.0)]
    out = rallies.side_speed(pts, cap_px=50.0)
    assert out[0] == 0.0            # no predecessor
    assert out[1] == pytest.approx(5.0)
    assert out[2] == 0.0            # 495 px jump: an identity switch, not motion
    assert out[3] == pytest.approx(5.0)


def test_speed_treats_missing_points_as_zero_not_as_a_jump():
    pts = [(0.0, 0.0), None, (100.0, 0.0)]
    assert rallies.side_speed(pts, cap_px=1000.0) == [0.0, 0.0, 0.0]


def test_swing_activity_takes_the_max_across_sides():
    """The pinned combining rule (§0.15 Defect 1): max of the two sides' speeds."""
    track = [
        {"frame": 0, "wrist_lower": (0.0, 500.0), "wrist_upper": (0.0, 10.0)},
        {"frame": 1, "wrist_lower": (0.0, 500.0), "wrist_upper": (3.0, 10.0)},
        {"frame": 2, "wrist_lower": (9.0, 500.0), "wrist_upper": (3.0, 10.0)},
    ]
    act = rallies.swing_activity(track, fps=60.0, scale=_Scale())
    assert len(act) == 3
    # Frame 2's lower-side 9px move must dominate the upper side's 0px move.
    assert act[2] > act[1] > 0.0


def test_swing_activity_without_a_scale_falls_back_to_the_absolute_cap():
    track = [{"frame": i, "wrist_lower": (float(i), 0.0), "wrist_upper": None}
             for i in range(5)]
    assert len(rallies.swing_activity(track, fps=60.0, scale=None)) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -v -p no:cacheprovider
```
Expected: FAIL — no `teleport_cap_px`, `side_speed`, `swing_activity`.

- [ ] **Step 3: Implement the swing signal**

Append to `badminton_analysis/stroke/rallies.py`:

```python
import math

TELEPORT_MIN_PX = 100.0     # rep_segmenter's absolute fallback, used only without a scale
SPRINT_CAP_MPS = 12.0
"""Generous human sprint cap. Above this a frame-to-frame delta is the tracker
jumping between people, not motion -- measured at 5.6% of far-player samples on
the multi-court footage (§0.15)."""


def teleport_cap_px(scale, y, fps):
    """Per-frame displacement cap in pixels, at image row ``y``.

    A cap in absolute pixels is wrong across a perspective gradient: on the
    DJI footage the scale runs 106.3 px/m (far) to 603.8 px/m (near), so one
    constant is ~4.7x too loose for the far player (§0.15 Defect 2).
    """
    if scale is None or not fps:
        return TELEPORT_MIN_PX
    return SPRINT_CAP_MPS * float(scale.px_per_m(y)) / float(fps)


def side_speed(points, cap_px):
    """Per-frame displacement of one side's point series, in px/frame.

    Zero where either endpoint is missing, and zero where the delta exceeds
    ``cap_px`` -- that is a track break, not movement.
    """
    out = [0.0] * len(points)
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        if a is None or b is None:
            continue
        v = math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
        if v <= cap_px:
            out[i] = v
    return out


def _side_points(track, key):
    return [rec.get(key) for rec in track]


def swing_activity(track, fps, scale=None):
    """Smoothed racket-arm activity per frame: max over both sides' wrist speed.

    The combining rule is pinned here because §0.8's written recipe was
    under-specified and reproducing it loosely moved the coverage figure by 20
    points (§0.15 Defect 1). This is the variant that scored F1 0.644 against
    human labels in §0.16: per side, the mean of whichever wrists are present
    (done upstream, in _capture_analysis_frame), then ``max`` across sides,
    then a 0.5 s trailing mean.

    Each side's teleport cap comes from that side's own image row, so the far
    player is not held to the near player's 5.7x looser bound.
    """
    lower = _side_points(track, "wrist_lower")
    upper = _side_points(track, "wrist_upper")

    def _median_y(points):
        ys = sorted(float(p[1]) for p in points if p is not None)
        return ys[len(ys) // 2] if ys else 0.0

    cap_lo = teleport_cap_px(scale, _median_y(lower), fps)
    cap_up = teleport_cap_px(scale, _median_y(upper), fps)

    act = [max(a, b) for a, b in zip(side_speed(lower, cap_lo),
                                     side_speed(upper, cap_up))]
    return smooth(act, max(1, int(round(SMOOTH_SEC * float(fps or 1)))))
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -v -p no:cacheprovider
```
Expected: PASS (12 passed).

- [ ] **Step 5: Record the wrists the signal needs**

`_analysis_track_both` (`system.py:735-740`) carries racket heads, not wrists — the swing
signal's validated input is missing. In `_capture_analysis_frame`, where `players_data[region]`
is built from `side_kp` (around `:731`), add a per-side wrist:

```python
            players_data[region] = {
                "keypoints": side_kp, "centroid": side_centroid, "racket_head": side_racket,
                "wrist": _mean_wrist(side_kp),
            }
```

and add the module-level helper (COCO-17: `L_WRIST, R_WRIST = 9, 10`, matching
`badminton_analysis/analysis/joint_angles.py:10`):

```python
def _mean_wrist(keypoints):
    """Mean of whichever wrists are valid, or None.

    The combining rule the swing signal's §0.16 measurement was made with;
    changing it silently invalidates that measurement.
    """
    if keypoints is None:
        return None
    from .analysis.joint_angles import L_WRIST, R_WRIST, is_valid
    kp = np.asarray(keypoints, dtype=float)
    pts = [(float(kp[i][0]), float(kp[i][1]))
           for i in (L_WRIST, R_WRIST) if is_valid(kp, i)]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
```

Then extend the `_analysis_track_both` append (`:735-740`):

```python
        self._analysis_track_both.append({
            "frame": frame_count,
            "racket_lower": players_data["lower"]["racket_head"],
            "racket_upper": players_data["upper"]["racket_head"],
            "wrist_lower": players_data["lower"]["wrist"],
            "wrist_upper": players_data["upper"]["wrist"],
            "shuttle": shuttle,
        })
```

This is purely additive: `detect_contacts_multi` and `StrokeRecognizer.label_rally` read
`racket_*` and `shuttle` by key and are unaffected.

- [ ] **Step 6: Run the suite**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ai_handoff.py
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add badminton_analysis/stroke/rallies.py badminton_analysis/system.py tests/test_rally_segmentation.py
git commit -m "feat(rallies): swing signal with per-side perspective teleport caps"
```

---

### Task 6: Fidelity gate — reproduce §0.16's measured numbers

The only task that produces no new behaviour. Its deliverable is **evidence** that Task 5's
implementation is the same signal that was scored against human labels — otherwise B11 would
ship an unmeasured variant while quoting F1 0.644 for it.

**Files:**
- Modify: `scripts/score_rally_labels.py` (call `rallies.swing_activity` instead of its own
  private copy)
- No production code changes. No new test.

**Interfaces:**
- Consumes: `rallies.swing_activity`, `rallies.segments_from_activity` (Tasks 4-5).
- Produces: a recorded verification result, pasted into the workstream file.

**Fixtures (verified present at commit `397844c`):**
`outputs/b11-labelling/LABELS.md` and `outputs/Dji 20260718111111 0010 D/detections.jsonl`.
Both are git-ignored per-footage data — **never commit them.**

- [ ] **Step 1: Record the baseline before touching anything**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/score_rally_labels.py
```
Expected: the §0.16 figures — F1 ≈ 0.644, 10/11 rallies found, start error ≈ +1.90 s. Save the
output. **If the baseline does not reproduce, stop and report** — the fixture or the script has
drifted and there is nothing to compare against.

- [ ] **Step 2: Point the script at the shared implementation**

In `scripts/score_rally_labels.py`, replace `load_activity`'s private speed/smooth/cap code
(`:90-108`) and `segment` (`:111-131`) with calls into the module, keeping its own
`detections.jsonl` parsing:

```python
from badminton_analysis.stroke import rallies

def load_activity():
    """Per-frame (time, smoothed swing activity), via the production module.

    Parses detections.jsonl into the same record shape _analysis_track_both
    uses, so this scores the shipped code path rather than a private copy.
    """
    times, track = [], []
    with open(DET, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            pl = d.get("players") or {}

            def wrist(side):
                hands = (pl.get(side) or {}).get("hands") or {}
                pts = [p for p in (hands.get("left"), hands.get("right")) if p]
                if not pts:
                    return None
                return (sum(p[0] for p in pts) / len(pts),
                        sum(p[1] for p in pts) / len(pts))

            times.append(d["time_sec"])
            track.append({"frame": d.get("frame"),
                          "wrist_lower": wrist("lower"),
                          "wrist_upper": wrist("upper")})
    return times, rallies.swing_activity(track, fps=FPS, scale=_quad_scale())
```

Add `_quad_scale()` building a `PerspectiveScale` from that run's `metadata.json` court
corners, so the caps are the derived ones rather than the script's hardcoded `21.3 / 120.9`.

- [ ] **Step 3: Re-score and compare**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/score_rally_labels.py
```
Expected: **F1 within ±0.02 of the Step 1 baseline, and rallies-found unchanged at 10/11.**

- [ ] **Step 4: If it does not match, diagnose in this order**

The likely causes, most probable first:

1. **Wrist source.** The script reads `players[side].hands` (written by
   `player.py:168-171` from the pose visualiser's hand points, keyed by centroid y); Task 5
   derives the wrist from `side_kp` keypoints 9/10. If these differ, they are different points
   — record which one §0.16's number belongs to and make the production path use *that*.
2. **Cap derivation.** The script's hardcoded `21.3 / 120.9` came from the far/near baselines;
   `swing_activity` uses each side's **median observed row**, which is not the baseline row.
   A materially different cap changes the rejection count.
3. **Smoothing alignment.** The script's window is `round(0.5 * 59.94)`; confirm
   `swing_activity` computes the same integer.

**Do not "fix" the comparison by loosening the tolerance.** Record whichever variant carries
the measurement and use it.

- [ ] **Step 5: Record the result**

Append a Verification row to `.ai/workstreams/match-stroke-recognition-b.md` with the command,
both F1 values, the rallies-found figure, and the tested commit.

- [ ] **Step 6: Commit**

```bash
git add scripts/score_rally_labels.py .ai/workstreams/match-stroke-recognition-b.md
git commit -m "test(rallies): score the shipped swing signal against the human labels"
```

---

### Task 7: The shuttle signal, artifact suppression, and signal selection

The shuttle signal is the *good* one — 88.8% density on the broadcast clip — and it is
**unusable** on the owner's fixed-camera footage, where 86% of `yolo11s-ball`'s output is a
single static fixture and court-volume gating cuts 4,194 detections to 17. The density gate is
what keeps that broken signal out of segmentation.

**Files:**
- Modify: `badminton_analysis/stroke/rallies.py`
- Test: `tests/test_rally_segmentation.py` (extend)

**Interfaces:**
- Consumes: `smooth`, `segments_from_activity`, `swing_activity` (Tasks 4-5).
- Produces:
  - `SHUTTLE_DENSITY_MIN = 0.50`, `STATIC_FRAC_MAX = 0.25`, `STATIC_SPREAD_PX = 25.0`,
    `STATIC_GRID_PX = 50.0`, `COURT_VOLUME_RAISE_FRAC = 1.0`
  - `suppress_static(track) -> (cleaned_track, list[dict])` — each dict is
    `{"point": [x, y], "count": n}`
  - `gate_to_court_volume(track, quad, raise_frac=COURT_VOLUME_RAISE_FRAC) -> (track, int)`
  - `shuttle_density(track) -> float`
  - `choose_signal(track, *, signal="auto") -> (name, reason)` with `name` in
    `{"shuttle", "swing", "none"}`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rally_segmentation.py`:

```python
def _track(shuttles):
    return [{"frame": i, "shuttle": s, "wrist_lower": None, "wrist_upper": None}
            for i, s in enumerate(shuttles)]


def test_static_fixture_is_suppressed_and_reported():
    """86% of the ball model's output on the owner's footage is one fixture (§0.7)."""
    fixture = [(473.0, 846.0)] * 90
    real = [(100.0 + 8 * i, 200.0 + 5 * i) for i in range(10)]
    cleaned, suppressed = rallies.suppress_static(_track(fixture + real))
    assert len(suppressed) == 1
    assert suppressed[0]["count"] == 90
    assert suppressed[0]["point"] == pytest.approx([473.0, 846.0])
    assert sum(1 for r in cleaned if r["shuttle"] is not None) == 10


def test_a_moving_shuttle_is_never_suppressed():
    moving = [(10.0 * i, 5.0 * i) for i in range(100)]
    cleaned, suppressed = rallies.suppress_static(_track(moving))
    assert suppressed == []
    assert sum(1 for r in cleaned if r["shuttle"] is not None) == 100


def test_dense_track_selects_the_shuttle_signal():
    dense = [(10.0 * i % 900, 7.0 * i % 700) for i in range(100)]
    name, reason = rallies.choose_signal(_track(dense))
    assert name == "shuttle"
    assert "density" in reason


def test_sparse_track_falls_back_to_swing():
    sparse = [None] * 95 + [(10.0 * i, 7.0 * i) for i in range(5)]
    track = _track(sparse)
    for i, rec in enumerate(track):          # give the swing signal something to see
        rec["wrist_lower"] = (float(i), 500.0)
    name, reason = rallies.choose_signal(track)
    assert name == "swing"
    assert "0.50" in reason


def test_neither_signal_available_selects_none():
    name, reason = rallies.choose_signal(_track([None] * 100))
    assert name == "none"
    assert reason


def test_density_is_computed_after_suppression():
    """A track that is 90% one fixture is sparse, not dense."""
    fixture = [(473.0, 846.0)] * 90
    real = [(100.0 + 8 * i, 200.0 + 5 * i) for i in range(10)]
    cleaned, _ = rallies.suppress_static(_track(fixture + real))
    assert rallies.shuttle_density(cleaned) == pytest.approx(0.10)


# --- court-volume gating (design spec §5: "gate the shuttle before segmentation") ---

_QUAD = [(100.0, 200.0), (900.0, 200.0), (1000.0, 800.0), (0.0, 800.0)]  # TL, TR, BR, BL


def test_points_outside_the_court_are_dropped():
    inside = (500.0, 500.0)
    outside = (500.0, 1500.0)          # well below the near baseline
    gated, dropped = rallies.gate_to_court_volume(
        _track([inside, outside, inside]), _QUAD)
    assert dropped == 1
    assert [r["shuttle"] for r in gated] == [inside, None, inside]


def test_airborne_shuttles_above_the_far_baseline_are_kept():
    """The top edge is raised, or every clear and lob would be gated away."""
    airborne = (500.0, 40.0)           # above the quad's far baseline (y=200)
    gated, dropped = rallies.gate_to_court_volume(_track([airborne]), _QUAD)
    assert dropped == 0
    assert gated[0]["shuttle"] == airborne


def test_no_quad_means_no_gating():
    """A run without court corners must not silently discard every detection."""
    pts = [(9999.0, 9999.0)] * 5
    gated, dropped = rallies.gate_to_court_volume(_track(pts), None)
    assert dropped == 0
    assert all(r["shuttle"] is not None for r in gated)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -v -p no:cacheprovider
```
Expected: FAIL — no `suppress_static`, `gate_to_court_volume`, `shuttle_density`,
`choose_signal`.

- [ ] **Step 3: Implement suppression, court-volume gating, density, and selection**

Append to `badminton_analysis/stroke/rallies.py`:

```python
SHUTTLE_DENSITY_MIN = 0.50
STATIC_FRAC_MAX = 0.25
STATIC_SPREAD_PX = 25.0
STATIC_GRID_PX = 50.0


def suppress_static(track):
    """Drop shuttle detections that are a fixture rather than a shuttle.

    A bucket holding more than STATIC_FRAC_MAX of all detections whose points
    span less than STATIC_SPREAD_PX is a light fitting, a line marking, or a
    bag -- not a shuttle, which never occupies a 25 px box for minutes. On the
    owner's footage this is 86% of the ball model's output (§0.7), so without
    this the density gate would be fooled into choosing a dead signal.

    Returns (cleaned_track, suppressed) and never mutates the input.
    """
    points = [(i, rec.get("shuttle")) for i, rec in enumerate(track)]
    present = [(i, p) for i, p in points if p is not None]
    total = len(present)
    if total == 0:
        return [dict(rec) for rec in track], []

    buckets = {}
    for i, p in present:
        key = (int(float(p[0]) // STATIC_GRID_PX), int(float(p[1]) // STATIC_GRID_PX))
        buckets.setdefault(key, []).append((i, p))

    drop, suppressed = set(), []
    for members in buckets.values():
        if len(members) <= STATIC_FRAC_MAX * total:
            continue
        xs = [float(p[0]) for _, p in members]
        ys = [float(p[1]) for _, p in members]
        if (max(xs) - min(xs)) >= STATIC_SPREAD_PX or (max(ys) - min(ys)) >= STATIC_SPREAD_PX:
            continue
        drop.update(i for i, _ in members)
        suppressed.append({"point": [sum(xs) / len(xs), sum(ys) / len(ys)],
                           "count": len(members)})

    cleaned = []
    for i, rec in enumerate(track):
        out = dict(rec)
        if i in drop:
            out["shuttle"] = None
        cleaned.append(out)
    return cleaned, suppressed


COURT_VOLUME_RAISE_FRAC = 1.0
"""How far above the far baseline the play volume extends, as a fraction of the
quad's own image height.

Not optional and not conservative: a clear or a lob spends most of its flight
ABOVE the far baseline's image row, so gating to the flat court polygon would
discard exactly the shots that matter. 1.0 (one court-height of headroom) is a
deliberately generous default -- this gate exists to reject the scoreboard, the
next court over, and the ceiling lights, not to be tight.
"""


def _point_in_polygon(x, y, poly):
    """Ray-casting point-in-polygon. Vertices in order, closed implicitly."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1)
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


def gate_to_court_volume(track, quad, raise_frac=COURT_VOLUME_RAISE_FRAC):
    """Drop shuttle detections outside the court's play volume.

    ``quad`` is the annotated 4-corner court in TL, TR, BR, BL order -- the
    ordering badminton_analysis.court.mapper already uses. The far edge is
    raised by ``raise_frac`` of the quad's image height so airborne shuttles
    count.

    This is what stops a dead detector from looking alive: on the owner's
    multi-court footage it cuts 4,194 detections to 17, which is the *correct*
    answer -- that footage's shuttle detection does not work -- and is exactly
    why the density check must run after this gate rather than on raw output.

    ``quad=None`` gates nothing, so a run without court corners degrades to
    "no gating" instead of silently discarding every detection.

    Returns (gated_track, dropped_count) and never mutates the input.
    """
    if not quad or len(quad) != 4:
        return [dict(rec) for rec in track], 0

    pts = [(float(p[0]), float(p[1])) for p in quad]
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = pts
    top_y = min(tly, try_)
    bottom_y = max(bly, bry)
    lift = max(0.0, (bottom_y - top_y) * float(raise_frac))
    volume = [(tlx, tly - lift), (trx, try_ - lift), (brx, bry), (blx, bly)]

    gated, dropped = [], 0
    for rec in track:
        out = dict(rec)
        p = rec.get("shuttle")
        if p is not None and not _point_in_polygon(float(p[0]), float(p[1]), volume):
            out["shuttle"] = None
            dropped += 1
        gated.append(out)
    return gated, dropped


def shuttle_density(track):
    """Fraction of frames with a shuttle. Call AFTER suppress_static."""
    if not track:
        return 0.0
    return sum(1 for rec in track if rec.get("shuttle") is not None) / float(len(track))


def _has_swing_input(track):
    return any(rec.get("wrist_lower") is not None or rec.get("wrist_upper") is not None
               for rec in track)


def choose_signal(track, *, signal="auto"):
    """Pick the segmentation signal and say why. ``track`` must be suppressed already.

    Selection is gated on density rather than trusting whatever the detector
    handed over, because on the owner's footage the shuttle signal is
    measurably dead (§0.11, §0.14, §0.21-0.24) and using it anyway would
    produce confident nonsense.
    """
    if signal != "auto":
        return signal, f"signal pinned to {signal!r} by the caller"

    density = shuttle_density(track)
    if density >= SHUTTLE_DENSITY_MIN:
        return "shuttle", (f"shuttle density {density:.3f} at or above "
                           f"{SHUTTLE_DENSITY_MIN:.2f} after artifact suppression")
    if _has_swing_input(track):
        return "swing", (f"shuttle density {density:.3f} below "
                         f"{SHUTTLE_DENSITY_MIN:.2f} after artifact suppression")
    return "none", (f"shuttle density {density:.3f} below "
                    f"{SHUTTLE_DENSITY_MIN:.2f} and no wrist track to fall back on")
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -v -p no:cacheprovider
```
Expected: PASS (21 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/rallies.py tests/test_rally_segmentation.py
git commit -m "feat(rallies): court-volume gate, artifact suppression, density-gated signal choice"
```

---

### Task 8: `segment_rallies` — the public API, provenance, and the degraded path

Owner decision §12a-C: when no signal qualifies, emit **uniform coarse windows marked
degraded**, not zero rallies and not one whole-video rally. Withholding stroke labels on them
(Task 10) is what makes this honest rather than a fabrication.

**Files:**
- Modify: `badminton_analysis/stroke/rallies.py`
- Test: `tests/test_rally_segmentation.py` (extend)

**Interfaces:**
- Consumes: everything from Tasks 4, 5, 7.
- Produces:
  - `DEGRADED_WINDOW_SEC = 10.0`
  - `RallySegment` — a `dict` with keys `id`, `start_frame`, `end_frame`, `start_sec`,
    `end_sec`, `degraded` (bool). A plain dict, not a class, so it serialises straight into
    `rally_segments.json` and keeps the existing array's schema.
  - `segment_rallies(track, fps, *, signal="auto", gap_sec=GAP_SEC, min_len_sec=MIN_LEN_SEC,
    swing_frac=SWING_FRAC, scale=None, quad=None) -> (list[RallySegment], dict)` — the `dict`
    is the `detection` provenance block.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rally_segmentation.py`:

```python
def test_no_signal_emits_degraded_coarse_windows_not_zero_rallies():
    """Owner decision §12a-C: never an empty result, never one whole-video rally."""
    track = _track([None] * 1800)          # 30 s at 60 fps, nothing to see
    segments, prov = rallies.segment_rallies(track, fps=60.0)
    assert prov["signal"] == "none"
    assert prov["degraded"] is True
    assert len(segments) == 3              # 30 s / DEGRADED_WINDOW_SEC
    assert all(s["degraded"] for s in segments)
    assert segments[0]["start_frame"] == 0
    assert segments[-1]["end_frame"] == 1799
    # The one thing this must never collapse into:
    assert not (len(segments) == 1 and segments[0]["end_frame"] == 1799)


def test_a_real_signal_produces_non_degraded_segments():
    track = []
    for i in range(1800):
        active = 300 <= i < 600 or 900 <= i < 1200
        track.append({"frame": i, "shuttle": None,
                      "wrist_lower": (float(i % 40) * (3.0 if active else 0.0), 500.0),
                      "wrist_upper": None})
    segments, prov = rallies.segment_rallies(track, fps=60.0)
    assert prov["signal"] == "swing"
    assert prov["degraded"] is False
    assert len(segments) == 2
    assert all(not s["degraded"] for s in segments)
    assert [s["id"] for s in segments] == [1, 2]


def test_provenance_states_signal_reason_and_params():
    segments, prov = rallies.segment_rallies(_track([None] * 600), fps=60.0)
    assert set(prov) >= {"signal", "reason", "degraded", "params",
                         "suppressed_static", "gated_outside_court"}
    assert prov["params"]["gap_sec"] == 1.0
    assert prov["params"]["min_len_sec"] == 2.0
    assert prov["params"]["swing_frac"] == 0.25
    assert prov["reason"]


def test_out_of_court_detections_are_reported_not_silently_dropped():
    """A dead detector must look dead in the provenance, not just in the count."""
    # Moving, so suppress_static does not eat them first -- the point of this
    # test is the court gate, not the fixture filter.
    below_court = [(50.0, 1500.0 + 2.0 * i) for i in range(200)]
    track = _track(below_court)
    _segments, prov = rallies.segment_rallies(track, fps=60.0, quad=_QUAD)
    assert prov["gated_outside_court"] > 0
    assert prov["signal"] == "none"


def test_suppressed_fixture_appears_in_provenance():
    fixture = [(473.0, 846.0)] * 900
    _segments, prov = rallies.segment_rallies(_track(fixture + [None] * 100), fps=60.0)
    assert prov["suppressed_static"]
    assert prov["suppressed_static"][0]["count"] == 900


def test_empty_track_is_not_an_error():
    segments, prov = rallies.segment_rallies([], fps=60.0)
    assert segments == []
    assert prov["signal"] == "none"


def test_pinned_signal_reproduces_todays_behaviour_for_non_regression():
    track = [{"frame": i, "shuttle": None,
              "wrist_lower": (float(i % 30) * 3.0, 500.0), "wrist_upper": None}
             for i in range(1200)]
    _segments, prov = rallies.segment_rallies(track, fps=60.0, signal="swing")
    assert prov["signal"] == "swing"
    assert "pinned" in prov["reason"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -v -p no:cacheprovider
```
Expected: FAIL — no `segment_rallies`.

- [ ] **Step 3: Implement the public API**

Append to `badminton_analysis/stroke/rallies.py`:

```python
DEGRADED_WINDOW_SEC = 10.0
"""Window length for the degraded fallback (owner decision §12a-C).

Deliberately dumb and not load-bearing: nothing downstream labels a degraded
segment, so this only controls how the unreliable span is chopped up for
display.
"""


def _shuttle_activity(track):
    """1.0 where a shuttle is present, else 0.0 -- presence *is* the activity."""
    return [1.0 if rec.get("shuttle") is not None else 0.0 for rec in track]


def _coarse_windows(track, fps):
    """Uniform DEGRADED_WINDOW_SEC windows spanning the whole track."""
    if not track:
        return []
    step = max(1, int(round(DEGRADED_WINDOW_SEC * float(fps or 1))))
    out = []
    for start in range(0, len(track), step):
        end = min(start + step, len(track)) - 1
        if end <= start:
            if out:                       # fold a stub tail into its predecessor
                out[-1][1] = end
                continue
        out.append([start, end])
    return out


def segment_rallies(track, fps, *, signal="auto", gap_sec=GAP_SEC,
                    min_len_sec=MIN_LEN_SEC, swing_frac=SWING_FRAC,
                    scale=None, quad=None):
    """Segment a recorded per-frame analysis track into rallies.

    ``track`` is ``_analysis_track_both``: per-frame dicts of
    {frame, racket_lower, racket_upper, wrist_lower, wrist_upper, shuttle}.

    Returns (segments, provenance). ``provenance["degraded"]`` is the flag that
    matters downstream: a degraded segmentation is displayable but must NOT be
    labelled by stroke recognition and must never be reported as a rally count
    (owner decision §12a-C; §0.17 measured count inflated +36% and total rally
    time +41% on the swing signal, which is why volume figures are withheld).
    """
    cleaned, suppressed = suppress_static(track)
    cleaned, gated_out = gate_to_court_volume(cleaned, quad)
    name, reason = choose_signal(cleaned, signal=signal)
    params = {"gap_sec": gap_sec, "min_len_sec": min_len_sec,
              "swing_frac": swing_frac, "smooth_sec": SMOOTH_SEC,
              "degraded_window_sec": DEGRADED_WINDOW_SEC,
              "court_volume_raise_frac": COURT_VOLUME_RAISE_FRAC}

    times = [i / float(fps or 1) for i in range(len(cleaned))]
    degraded = False

    if name == "shuttle":
        activity = _shuttle_activity(cleaned)
        spans = segments_from_activity(times, activity, frac=swing_frac,
                                       gap_sec=gap_sec, min_len_sec=min_len_sec)
    elif name == "swing":
        activity = swing_activity(cleaned, fps, scale=scale)
        spans = segments_from_activity(times, activity, frac=swing_frac,
                                       gap_sec=gap_sec, min_len_sec=min_len_sec)
    else:
        spans = None

    if spans is None or not spans:
        degraded = True
        if spans is not None and name != "none":
            reason = f"{reason}; {name} signal produced no segments"
        index_spans = _coarse_windows(cleaned, fps)
    else:
        index_spans = [[int(round(a * float(fps or 1))), int(round(b * float(fps or 1)))]
                       for a, b in spans]

    segments = []
    for n, (i0, i1) in enumerate(index_spans, start=1):
        i0 = max(0, min(i0, len(cleaned) - 1))
        i1 = max(i0, min(i1, len(cleaned) - 1))
        segments.append({
            "id": n,
            "start_frame": int(cleaned[i0].get("frame", i0)),
            "end_frame": int(cleaned[i1].get("frame", i1)),
            "start_sec": i0 / float(fps or 1),
            "end_sec": i1 / float(fps or 1),
            "degraded": degraded,
        })

    provenance = {"signal": name, "reason": reason, "degraded": degraded,
                  "params": params, "suppressed_static": suppressed,
                  "gated_outside_court": gated_out}
    return segments, provenance
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -v -p no:cacheprovider
```
Expected: PASS (28 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/rallies.py tests/test_rally_segmentation.py
git commit -m "feat(rallies): segment_rallies with provenance and the degraded window path"
```

---

### Task 9: Wire the segmenter into the pipeline, post-loop

**Files:**
- Modify: `badminton_analysis/system.py:337-348` (the `rally_segments.json` write),
  `:426-439` (the in-loop rally state machine)
- Test: `tests/test_rally_segmentation.py` (extend with a wiring test)

**Interfaces:**
- Consumes: `rallies.segment_rallies` (Task 8); `PerspectiveScale` from
  `badminton_analysis.court.scale`.
- Produces: `rally_segments.json` with a `detection` block alongside the **unchanged**
  `rallies` array; `self.rally_detection` (the provenance dict) for Task 10.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rally_segmentation.py`:

```python
def test_pipeline_writes_provenance_without_breaking_the_rallies_schema(tmp_path):
    """player_positions.py and the UI read `rallies`; it must keep its exact shape."""
    from badminton_analysis import system as sysmod

    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s.court_corners = None
    s._analysis_track_both = [
        {"frame": i, "shuttle": None,
         "wrist_lower": (float(i % 40) * 3.0, 500.0), "wrist_upper": None}
        for i in range(1200)
    ]
    s._write_rally_segments(fps=60.0)

    import json
    with open(tmp_path / "rally_segments.json", encoding="utf-8") as fh:
        payload = json.load(fh)

    assert payload["fps"] == 60.0
    assert payload["rallies"]
    first = payload["rallies"][0]
    assert set(first) >= {"id", "start_frame", "end_frame", "start_sec", "end_sec"}
    assert payload["detection"]["signal"] in {"shuttle", "swing", "none"}
    assert "reason" in payload["detection"]


def test_segmenter_failure_is_never_fatal(tmp_path, monkeypatch):
    from badminton_analysis import system as sysmod
    from badminton_analysis.stroke import rallies as rallies_mod

    def _boom(*a, **k):
        raise RuntimeError("synthetic segmenter failure")

    monkeypatch.setattr(rallies_mod, "segment_rallies", _boom)

    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s.court_corners = None
    s._analysis_track_both = []
    s._write_rally_segments(fps=60.0)      # must not raise

    import json
    with open(tmp_path / "rally_segments.json", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["rallies"] == []
    assert payload["detection"]["signal"] == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -k pipeline -v -p no:cacheprovider
```
Expected: FAIL — no `_write_rally_segments`.

- [ ] **Step 3: Replace the write with a post-loop call**

Replace `system.py:337-348` with:

```python
        self._write_rally_segments(fps)
```

and add the method:

```python
    def _write_rally_segments(self, fps):
        """Segment rallies from the recorded track and write rally_segments.json.

        Post-loop by design: nothing in the frame loop consumes rally segments
        live, so segmenting offline costs nothing and can use non-causal
        windows (a rally's start is best judged knowing what follows).

        Never fatal: a segmenter failure writes an empty rallies array with
        detection.signal == "error" and leaves every other output intact.
        """
        from .stroke import rallies as rallies_mod

        segments, detection = [], {"signal": "error", "reason": "", "degraded": True,
                                   "params": {}, "suppressed_static": [],
                                   "gated_outside_court": 0}
        try:
            scale = None
            if getattr(self, "court_corners", None):
                try:
                    from .court.scale import PerspectiveScale
                    scale = PerspectiveScale.from_quad(self.court_corners)
                except Exception as exc:
                    print(f"Rally segmentation: no perspective scale ({exc}); "
                          "falling back to the absolute teleport cap")
            segments, detection = rallies_mod.segment_rallies(
                self._analysis_track_both, fps, scale=scale,
                quad=getattr(self, "court_corners", None))
            print(f"Rally segmentation: {len(segments)} segments, "
                  f"signal={detection['signal']}, degraded={detection['degraded']} "
                  f"({detection['reason']})")
        except Exception as exc:
            print(f"Rally segmentation failed, writing an empty result: {exc}")
            detection["reason"] = str(exc)

        self.rally_detection = detection
        detection_block = dict(detection)
        detection_block["court_view"] = getattr(self, "court_view_calibration", None)
        write_json(os.path.join(self.save_dir, "rally_segments.json"), {
            "fps": fps,
            "rallies": [{"id": s["id"], "start_frame": s["start_frame"],
                         "end_frame": s["end_frame"], "start_sec": s["start_sec"],
                         "end_sec": s["end_sec"], "degraded": s["degraded"]}
                        for s in segments],
            "detection": detection_block,
        })
```

- [ ] **Step 4: Strip rally bookkeeping from the in-loop state machine**

At `system.py:426-439`, keep the tracker hygiene and drop the segment recording — those
transitions are about view breaks, not rallies:

```python
        if self.is_court_view_count >= self.court_view_frames_threshold and not self.rally_active:
            self.rally_active = True
            # Kept for the on-video overlay only: this counts court-view
            # transitions, NOT rallies. rally_segments.json is authoritative
            # and is produced post-loop by _write_rally_segments().
            self.rally_count += 1
            self.player_tracker.start_new_rally()

        if self.consecutive_non_court_frames >= self.non_court_frames_threshold and self.rally_active:
            self.rally_active = False
            self.shuttlecock_tracker.clear_trajectory()
```

Also delete the end-of-video `if self.rally_active:` append that used to sit at `:338-339`, and
remove `self._current_rally_start` if nothing else reads it (grep first).

> **Known, accepted discrepancy:** the burned-in overlay's "Rally: N"
> (`visualization/stats.py:167` via `player_pose.py:142`) now counts court-view transitions
> while `rally_segments.json` counts segments. They will disagree. Do not "fix" this by feeding
> the overlay the post-loop count — it does not exist yet when the frame is drawn.

- [ ] **Step 5: Run the suite**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ai_handoff.py
```
Expected: PASS. `tests/test_full_duration_video.py` sets `s.rally_count = 0` — confirm it still
passes, and if it asserted on `rally_segments`, update it to the post-loop path.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/system.py tests/test_rally_segmentation.py
git commit -m "feat(rallies): segment post-loop; the in-loop machine keeps only tracker hygiene"
```

---

### Task 10: Withhold labels and volume figures on a degraded segmentation

The half of decision §12a-C that makes coarse windows honest rather than a fabrication, plus
§0.17's suppression of the two measurements that inflate.

**Scope note, stated because it is easy to over-read:** per-rally BST invocation is **B6, not
B11**. `_run_stroke_recognition` (`system.py:810-852`) calls `label_rally` **once** over the
whole track. So B11's implementable form of "BST is not invoked on a degraded segment" is: when
the segmentation is degraded, stroke recognition **does not run at all**, and says so. When B6
adds per-segment invocation, the same `degraded` flag governs per-segment skipping.

**Files:**
- Modify: `badminton_analysis/system.py:810-852` (`_run_stroke_recognition`)
- Modify: `app.py:646-663` (the `rally_count` the API reports)
- Test: `tests/test_rally_segmentation.py` (extend)

**Interfaces:**
- Consumes: `self.rally_detection` (Task 9).
- Produces: no new API. `app.py`'s job result gains `rally_count_suppressed` (bool) and
  `rally_signal` (str) alongside a `rally_count` that is `None` when suppressed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rally_segmentation.py`:

```python
def test_degraded_segmentation_skips_stroke_recognition(tmp_path, capsys):
    """Labels over windows that are not rallies would be noise presented as strokes."""
    from badminton_analysis import system as sysmod

    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s.bst_weights = "weights/bst.pt"        # pretend weights exist
    s.rally_detection = {"signal": "none", "degraded": True, "reason": "no signal"}

    s._run_stroke_recognition()

    assert not (tmp_path / "strokes.json").exists()
    assert "withheld" in capsys.readouterr().out.lower()


def test_non_degraded_segmentation_does_not_block_recognition(tmp_path, monkeypatch):
    from badminton_analysis import system as sysmod

    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s.bst_weights = None                    # no weights -> the usual silent no-op
    s.rally_detection = {"signal": "shuttle", "degraded": False, "reason": "dense"}
    s._run_stroke_recognition()             # must not raise, must not print "withheld"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_rally_segmentation.py -k stroke_recognition -v -p no:cacheprovider
```
Expected: FAIL — recognition does not consult `rally_detection`.

- [ ] **Step 3: Gate recognition on the degraded flag**

At the top of `_run_stroke_recognition` (`system.py:830`), before `if not self.bst_weights:`:

```python
        detection = getattr(self, "rally_detection", None) or {}
        if detection.get("degraded"):
            print("Stroke recognition withheld: rally segmentation is degraded "
                  f"({detection.get('reason', 'no reason recorded')}). Labels over "
                  "windows that are not rallies would be noise, so none are produced.")
            return
```

Extend the docstring to say why, so the next reader does not "restore" it:

```
        Withheld entirely when rally segmentation is degraded (owner decision
        §12a-C): BST labels computed over coarse windows that do not
        correspond to rallies are close to noise, and shipping them behind a
        badge is weaker than the impression that strokes were detected. When
        B6 adds per-rally invocation, the same `degraded` flag skips
        individual segments instead of the whole run.
```

- [ ] **Step 4: Stop reporting a count that measured +36% inflation**

Replace `app.py:646-663`'s `rally_count` block:

```python
            rally_file = os.path.join(sd, 'rally_segments.json')
            rally_count = None
            rally_signal = None
            rally_suppressed = False
            if os.path.exists(rally_file):
                try:
                    with open(rally_file, encoding='utf-8') as rf:
                        payload = json.load(rf)
                    detection = payload.get('detection') or {}
                    rally_signal = detection.get('signal')
                    # The swing signal inflates rally COUNT by +36% and total
                    # rally time by +41% while typical DURATION stays within
                    # 3% (design spec §0.17). Duration is shippable; volume is
                    # not, and a degraded segmentation has no rally count at
                    # all -- only coarse windows.
                    rally_suppressed = bool(detection.get('degraded')) or rally_signal == 'swing'
                    if not rally_suppressed:
                        rally_count = len(payload.get('rallies', []))
                except Exception:
                    pass
```

and in the result dict replace `'rally_count': rally_count,` with:

```python
                'rally_count': rally_count,
                'rally_count_suppressed': rally_suppressed,
                'rally_signal': rally_signal,
```

> The UI change that consumes these is intentionally **not** in this task: `static/kestrel.js`
> must render "segmentation unreliable, labels withheld" rather than a count, and that copy
> belongs with B6's results-screen work. Leaving `rally_count: null` makes a stale UI show
> nothing rather than a wrong number — the safe failure direction.

- [ ] **Step 5: Run the suite**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ai_handoff.py
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/system.py app.py tests/test_rally_segmentation.py
git commit -m "feat(rallies): withhold stroke labels and volume figures when degraded"
```

---

### Task 11: Shot-boundary detection for the true-broadcast class

Owner decision §12a-A kept real TV broadcast in scope. **No footage on disk can validate this**
— the Axelsen file is a single-angle continuous recording with no cuts (§0.5). So this task
builds and hermetically tests the component, and records its real-footage validation as *not
run*. Do not claim otherwise in any commit message or status line.

**Files:**
- Create: `badminton_analysis/stroke/shot_boundary.py`
- Test: `tests/test_shot_boundary.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `HIST_BINS = 32`, `CUT_CORRELATION_MAX = 0.60`
  - `frame_signature(gray, bins=HIST_BINS) -> list[float]` — normalised histogram
  - `is_cut(sig_a, sig_b, max_corr=CUT_CORRELATION_MAX) -> bool`
  - `find_cuts(signatures, max_corr=CUT_CORRELATION_MAX) -> list[int]` — indices where a cut
    lands *on* that frame

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shot_boundary.py
"""Hard-cut detection for the true-broadcast footage class.

NOT VALIDATED ON REAL FOOTAGE. Owner decision B11 §12a-A kept true
multi-camera broadcast in scope, but the only "broadcast" file on disk is a
single-angle continuous recording with no cuts in it (design spec §0.5), so
there is nothing to validate against. These tests pin the mechanism, not its
accuracy on real television.
"""
import numpy as np

from badminton_analysis.stroke import shot_boundary


def _flat(value, shape=(180, 320)):
    return np.full(shape, value, dtype=np.uint8)


def test_identical_frames_are_not_a_cut():
    sig = shot_boundary.frame_signature(_flat(120))
    assert shot_boundary.is_cut(sig, sig) is False


def test_a_wholesale_change_is_a_cut():
    a = shot_boundary.frame_signature(_flat(20))
    b = shot_boundary.frame_signature(_flat(230))
    assert shot_boundary.is_cut(a, b) is True


def test_gradual_drift_is_not_a_cut():
    """A pan or a light change must not read as a camera switch."""
    rng = np.random.default_rng(7)
    base = rng.integers(60, 90, (180, 320), dtype=np.uint8)
    a = shot_boundary.frame_signature(base)
    b = shot_boundary.frame_signature(np.clip(base.astype(int) + 3, 0, 255).astype(np.uint8))
    assert shot_boundary.is_cut(a, b) is False


def test_find_cuts_reports_each_boundary_once():
    sigs = ([shot_boundary.frame_signature(_flat(30))] * 10
            + [shot_boundary.frame_signature(_flat(220))] * 10
            + [shot_boundary.frame_signature(_flat(30))] * 10)
    assert shot_boundary.find_cuts(sigs) == [10, 20]


def test_no_cuts_in_continuous_footage():
    sigs = [shot_boundary.frame_signature(_flat(100))] * 30
    assert shot_boundary.find_cuts(sigs) == []


def test_signature_is_normalised():
    sig = shot_boundary.frame_signature(_flat(77))
    assert len(sig) == shot_boundary.HIST_BINS
    assert abs(sum(sig) - 1.0) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_shot_boundary.py -v -p no:cacheprovider
```
Expected: FAIL — `No module named 'badminton_analysis.stroke.shot_boundary'`.

- [ ] **Step 3: Implement it**

```python
# badminton_analysis/stroke/shot_boundary.py
"""Hard-cut detection for true multi-camera broadcast footage.

STATUS: mechanism implemented, NOT validated on real footage. Owner decision
B11 §12a-A kept true TV broadcast in scope, but the only "broadcast" file this
project has is a single-angle continuous recording with no cuts, no replays,
and no score bug (design spec §0.5). There is therefore nothing on disk to
measure this against, and a genuine broadcast sample is the cheapest thing
that would change that.

Method: correlate coarse intensity histograms of consecutive frames. A hard
cut replaces the whole image at once, so the histogram decorrelates in a
single step, while pans, zooms, and lighting changes drift smoothly. This
deliberately does NOT attempt replay detection (logo wipes, slow motion),
which is a different and harder problem.
"""
import math

HIST_BINS = 32
CUT_CORRELATION_MAX = 0.60
"""Below this Pearson correlation between consecutive histograms, call it a cut.

Unvalidated on real broadcast footage -- see the module docstring. Named and
overridable so it can be calibrated the moment a real sample exists.
"""


def frame_signature(gray, bins=HIST_BINS):
    """Normalised coarse intensity histogram of a grayscale frame."""
    import numpy as np

    arr = np.asarray(gray)
    hist, _ = np.histogram(arr, bins=bins, range=(0, 256))
    total = float(hist.sum())
    if total <= 0:
        return [0.0] * bins
    return [float(v) / total for v in hist]


def _correlation(a, b):
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da <= 0 or db <= 0:
        return 1.0 if da == db else 0.0
    return num / (da * db)


def is_cut(sig_a, sig_b, max_corr=CUT_CORRELATION_MAX):
    """Whether these consecutive signatures straddle a hard cut."""
    return _correlation(sig_a, sig_b) < max_corr


def find_cuts(signatures, max_corr=CUT_CORRELATION_MAX):
    """Indices of frames on which a hard cut lands."""
    return [i for i in range(1, len(signatures))
            if is_cut(signatures[i - 1], signatures[i], max_corr)]
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_shot_boundary.py -v -p no:cacheprovider
```
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/shot_boundary.py tests/test_shot_boundary.py
git commit -m "feat(broadcast): hard-cut detection, hermetic tests only (no sample to validate on)"
```

---

### Task 12: Controller-run validation harness

Staged cheapest-first, because a full run costs hours. **Steps 1-3 are runnable now; step 4 is
the owner's to run.**

**Files:**
- Create: `scripts/check_rally_gate.py`
- Modify: `.ai/workstreams/match-stroke-recognition-b.md` (record every outcome, including
  failures)

**Interfaces:**
- Consumes: `courtview_cut_from_scores`, `downscale_for_gate` (Tasks 2-3);
  `rallies.segment_rallies` (Task 8).
- Produces: a CLI with `--gate <video> --template <png>` and
  `--replay <outputs/<run>/detections.jsonl>`.

- [ ] **Step 1: Write the harness**

```python
# scripts/check_rally_gate.py
"""Controller-run B11 checks. Not part of the committed suite.

Staged cheapest-first, because a full pipeline run costs hours:

  --gate    re-measure the court-view pass fraction on a video (minutes, no weights)
  --replay  run segment_rallies over an ALREADY RECORDED detections.jsonl (seconds)

Usage:
    PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/check_rally_gate.py \
        --gate "path/to/video.mp4" --template "path/to/template.png"
    PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/check_rally_gate.py \
        --replay "outputs/Dji 20260718111111 0010 D/detections.jsonl" --fps 59.94
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def gate(video, template_path, stride_sec=0.5):
    import cv2
    from badminton_analysis.system import courtview_cut_from_scores, downscale_for_gate

    tmpl = cv2.imread(str(template_path), 0)
    if tmpl is None:
        raise SystemExit(f"unreadable template: {template_path}")
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tmpl_small = downscale_for_gate(cv2.resize(tmpl, (frame_w, frame_h)))

    stride = max(1, int(round(stride_sec * fps)))
    scores, idx = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride == 0:
            small = downscale_for_gate(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            scores.append(float(cv2.matchTemplate(
                small, tmpl_small, cv2.TM_CCOEFF_NORMED).max()))
        idx += 1
    cap.release()

    calib = courtview_cut_from_scores(scores)
    passed = sum(1 for s in scores if s >= calib["cut"])
    print(json.dumps({"video": str(video), "samples": len(scores),
                      "calibration": calib,
                      "pass_frac": passed / len(scores) if scores else 0.0}, indent=2))


def replay(detections, fps):
    from badminton_analysis.stroke import rallies

    track = []
    with open(detections, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            pl = d.get("players") or {}

            def wrist(side):
                hands = (pl.get(side) or {}).get("hands") or {}
                pts = [p for p in (hands.get("left"), hands.get("right")) if p]
                if not pts:
                    return None
                return (sum(p[0] for p in pts) / len(pts),
                        sum(p[1] for p in pts) / len(pts))

            track.append({"frame": d.get("frame"),
                          "wrist_lower": wrist("lower"),
                          "wrist_upper": wrist("upper"),
                          "shuttle": (d.get("ball") or {}).get("image")})

    segments, prov = rallies.segment_rallies(track, fps)
    durations = sorted(s["end_sec"] - s["start_sec"] for s in segments)
    print(json.dumps({
        "frames": len(track), "segments": len(segments),
        "median_duration_sec": durations[len(durations) // 2] if durations else None,
        "longest_sec": durations[-1] if durations else None,
        "detection": prov,
    }, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate")
    ap.add_argument("--template")
    ap.add_argument("--replay")
    ap.add_argument("--fps", type=float, default=59.94)
    args = ap.parse_args()
    if args.gate:
        gate(args.gate, args.template)
    if args.replay:
        replay(args.replay, args.fps)
```

> **Check `detections.jsonl`'s shuttle key before trusting the replay.** This harness guesses
> `d["ball"]["image"]`. Grep one line of a real file and correct it — a wrong key silently
> reads as "no shuttle", which would make every replay look degraded and look like a finding.

- [ ] **Step 2: Run the gate check on both footage classes**

First locate the two source videos and their templates — do not guess the paths. Each completed
run records both in its own `metadata.json`:

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "import json,glob; [print(json.load(open(p,encoding='utf-8'))['video']['path'], '|', json.load(open(p,encoding='utf-8'))['court']['template_path']) for p in glob.glob('outputs/*/metadata.json')]"
```

Then, for the broadcast (Axelsen) run and the fixed-camera (DJI) run in turn:

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/check_rally_gate.py --gate "<video path>" --template "<template path>"
```

Expected, against the measured ground truth in §0.3/§0.6: on the broadcast video `pass_frac`
rises from **0.0066** to a large majority; on the DJI clip **~0.98 is preserved, not
regressed**. Both numbers matter — a change that fixes one and breaks the other fails
done-means 1.

- [ ] **Step 3: Replay the segmenter on both recorded runs**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/check_rally_gate.py --replay "outputs/Dji 20260718111111 0010 D/detections.jsonl" --fps 59.94
```
Expected on the DJI clip: `signal == "swing"` (its shuttle track is dead), segment durations in
**seconds not minutes**, and a count in the same order as a human's — around 15 predicted for
11 real, per §0.17. **A single multi-thousand-frame segment covering ~98% of the video means
done-means 5 has failed.**

> This harness passes no `quad`, so its shuttle density is measured **without** court-volume
> gating and will read *higher* than production. That is fine for the segment-shape check, but
> do not quote its density figure as the production one — extend the harness to read
> `court.corners` from that run's `metadata.json` if a density number is what you need.

- [ ] **Step 4: Record every outcome, including the disappointing ones**

Add a Verification row per check to `.ai/workstreams/match-stroke-recognition-b.md` with the
command, the result, and the tested commit. Where a check was not run, write **`not run`** and
the reason — specifically, the true-broadcast shot-boundary validation from Task 11, which has
no sample.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_rally_gate.py .ai/workstreams/match-stroke-recognition-b.md
git commit -m "test(b11): staged gate and segmenter-replay validation harness"
```

---

## Final verification

- [ ] **Full suite, including the flaky continuity file**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp "$TEMP/b11-final"
```
Expected: all pass. If `tests/test_ai_handoff.py` fails, re-run it alone to confirm it is the
known batch flake and record that — do not report it as a B11 regression, and do not assume it
either.

- [ ] **Non-regression with the gate pinned** (global constraint, done-means 10)

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_court_view_gate.py tests/test_rally_segmentation.py -q -p no:cacheprovider
```

- [ ] **Nothing forbidden got staged**

```bash
git status --short
```
Expected: clean apart from the pre-existing untracked `BirdEye Prototype.html`. No `outputs/`,
no `weights/`, no `*.pt`, no `LABELS.md`.

---

## What this plan does NOT deliver

Stated so no status line overclaims:

1. **Stroke recognition does not start working end-to-end.** B11 removes the *detection*
   blocker. Contacts and BST labels additionally need B2-B7 and the unmeasured domain-shift
   risk (R7). On the owner's own footage `detect_contacts_multi` still begins each frame with
   `if shuttle is None: continue`, and there is still no shuttle (§0.17).
2. **Accurate rally boundaries.** The swing signal is a good detector and a poor boundary
   estimator: +1.90 s start lag, count +36%, total time +41% (§0.16, §0.17). Task 10 suppresses
   the two figures that inflate; it does not make them right.
3. **Validated broadcast support.** Task 11's shot-boundary component has hermetic tests and no
   real-footage evidence, because no on-disk file contains a cut (§0.5). Replay and slow-motion
   detection are not attempted at all.
4. **Per-rally BST invocation, `rally_id`, or the results-screen copy.** Those are B6. Task 10
   withholds labels for a whole degraded run because per-segment invocation does not exist yet.
5. **A working shuttle detector.** Closed as a code problem (§0.21-0.24); the remaining path is
   capture-time (§0.25 option D) and is the owner's to run.
