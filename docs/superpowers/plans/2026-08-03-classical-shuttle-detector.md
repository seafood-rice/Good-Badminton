# Classical Shuttle Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a static-camera classical shuttle detector that supplies a dense shuttle
trajectory on footage where TrackNetV3 fails, and measure it against the 11 labelled rallies to
reach the §0.23 decision gate.

**Architecture:** A new `classical` producer emits exactly the same contract as
`shuttle_track.tracknet.track_video` — `{frame_index: (x, y) | None}` — so it drops into the
existing `_run_shuttle_pretrack` seam behind a footage-class router (static camera → classical,
moving camera → TrackNetV3; they are complementary, not competing, per §0.23). Detection is
median-background subtraction with a court mask, a structural-flicker mask and per-depth-band
candidate budgets; selection is a single-target tracker using the §0.22 area∝speed signature,
which replaces the spike's greedy chain selection that fragmented one shuttle into ~38 pieces.

**Tech Stack:** Python 3, OpenCV (`opencv-python==4.10.0.84`), NumPy (`>=1.21.6,<2.0`),
SciPy (`>=1.7.0`, already declared — `scipy.spatial.cKDTree`), pytest.

## Global Constraints

- `main` is the protected integration branch. Never commit, merge, rebase, push or force-update
  `main`. Work on `claude/match-stroke-recognition-b`.
- Never commit model weights or datasets. `weights/`, `*.pt` and `outputs/` are gitignored.
- Never stage the pre-existing dirty paths: the `app.py` CourtMapper/net-line hunk, the deleted
  `assets/*` files, and `BirdEye Prototype.html`. They are intentionally uncommitted.
- Run tests with the project venv and UTF-8 forced:
  `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest <target> -q -p no:cacheprovider`
- Prefer targeted test runs. The `tests/test_ai_handoff.py` continuity suite is known to be flaky
  in long batch runs (~21 failures) while passing individually; it is unrelated to app code.
- The web API must never pass a client-supplied path into `torch.load(..., weights_only=False)`.
- **Backward compatibility is mandatory:** when the classical path is inactive, the existing yolo
  and TrackNetV3 behaviour must be byte-identical to today. New parameters are opt-in with
  defaults that preserve current behaviour.
- Court quad convention (from `badminton_analysis/court/mapper.py`): 4 corners ordered
  top-left, top-right, bottom-right, bottom-left. Index 0/1 are the far baseline, 3/2 the near.
- Assume the quad traces the **outer (doubles) court boundary**, width **6.10 m**. This is an
  assumption, declared as a named constant so it can be corrected in one place.

---

### Task 1: Perspective scale from the court quad

The contact gate in `stroke/events.py` uses a fixed `contact_px = 80.0`, which is 13 cm at the
near baseline (603.8 px/m) but 75 cm at the far baseline (106.3 px/m) — a defect recorded in
§0.15/§0.20 and one that must be fixed regardless of which detector wins. For a ground plane
under perspective, lateral scale is **linear in image y**: `px_per_m(y) = a * (y - y0)`. Two
anchors from the quad determine `a` and `y0`.

**Files:**
- Create: `badminton_analysis/court/scale.py`
- Test: `tests/test_court_scale.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `PerspectiveScale` with classmethod `from_quad(quad, court_width_m=COURT_WIDTH_M)`
  and method `px_per_m(y: float) -> float`; attributes `a: float`, `y0: float`; module constant
  `COURT_WIDTH_M = 6.10`. Tasks 2 and 6 depend on these exact names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_court_scale.py
import math

import pytest

from badminton_analysis.court.scale import COURT_WIDTH_M, PerspectiveScale

# A quad constructed to reproduce the two scales measured on the DJI footage
# (design doc sections 0.13/0.23): 106.3 px/m at the far baseline y=1122 and
# 603.8 px/m at the near baseline y=2095, with a 6.10 m outer court width.
FAR_Y, NEAR_Y = 1122.0, 2095.0
FAR_PX_PER_M, NEAR_PX_PER_M = 106.3, 603.8
_CX = 1920.0
_FAR_HALF = FAR_PX_PER_M * COURT_WIDTH_M / 2.0     # 324.215
_NEAR_HALF = NEAR_PX_PER_M * COURT_WIDTH_M / 2.0   # 1841.59
QUAD = [
    (_CX - _FAR_HALF, FAR_Y),    # top-left  (far baseline)
    (_CX + _FAR_HALF, FAR_Y),    # top-right (far baseline)
    (_CX + _NEAR_HALF, NEAR_Y),  # bottom-right (near baseline)
    (_CX - _NEAR_HALF, NEAR_Y),  # bottom-left  (near baseline)
]


def test_reproduces_both_measured_anchors():
    s = PerspectiveScale.from_quad(QUAD)
    assert s.px_per_m(FAR_Y) == pytest.approx(FAR_PX_PER_M, abs=0.1)
    assert s.px_per_m(NEAR_Y) == pytest.approx(NEAR_PX_PER_M, abs=0.1)


def test_horizon_and_slope_match_hand_derivation():
    # y0 = (s_far*y_near - s_near*y_far) / (s_far - s_near)
    #    = (222698.5 - 677463.6) / (-497.5) = 914.101
    # a  = s_near / (y_near - y0) = 603.8 / 1180.899 = 0.511306
    s = PerspectiveScale.from_quad(QUAD)
    assert s.y0 == pytest.approx(914.101, abs=0.01)
    assert s.a == pytest.approx(0.511306, abs=0.00005)


def test_scale_is_linear_between_anchors():
    s = PerspectiveScale.from_quad(QUAD)
    mid_y = (FAR_Y + NEAR_Y) / 2.0
    assert s.px_per_m(mid_y) == pytest.approx(355.05, abs=0.1)


def test_scale_grows_monotonically_toward_the_camera():
    s = PerspectiveScale.from_quad(QUAD)
    vals = [s.px_per_m(y) for y in range(1100, 2100, 100)]
    assert all(b > a for a, b in zip(vals, vals[1:]))


def test_degenerate_quad_is_rejected():
    flat = [(0.0, 500.0), (100.0, 500.0), (100.0, 500.0), (0.0, 500.0)]
    with pytest.raises(ValueError):
        PerspectiveScale.from_quad(flat)


def test_requires_exactly_four_corners():
    with pytest.raises(ValueError):
        PerspectiveScale.from_quad(QUAD[:3])


def test_px_per_m_above_horizon_is_clamped_positive():
    s = PerspectiveScale.from_quad(QUAD)
    # y at or above the horizon has no physical scale; must not return <= 0
    assert s.px_per_m(s.y0) > 0.0
    assert s.px_per_m(s.y0 - 500.0) > 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_court_scale.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'badminton_analysis.court.scale'`

- [ ] **Step 3: Write minimal implementation**

```python
# badminton_analysis/court/scale.py
"""Perspective scale (pixels per metre) as a function of image row.

For a planar court viewed by a pinhole camera, lateral scale is linear in image
y: ``px_per_m(y) = a * (y - y0)``, where ``y0`` is the horizon row. Two anchors
-- the far and near baselines of the court quad, whose real-world separation is
the court width -- determine ``a`` and ``y0``.

This replaces absolute-pixel constants, which are wrong across a perspective
gradient: on the DJI footage the scale runs from 106.3 px/m at the far baseline
to 603.8 px/m at the near one, a 5.7x span.
"""
import math

COURT_WIDTH_M = 6.10        # outer (doubles) sideline separation
_MIN_SCALE = 1e-3           # px/m floor, so callers never divide by zero


class PerspectiveScale:
    """Linear-in-y pixels-per-metre model for one calibrated court view."""

    def __init__(self, a, y0):
        self.a = float(a)
        self.y0 = float(y0)

    @classmethod
    def from_quad(cls, quad, court_width_m=COURT_WIDTH_M):
        """Build from a 4-corner court quad ordered TL, TR, BR, BL.

        Corners 0-1 span the far baseline, corners 3-2 the near baseline.
        """
        if quad is None or len(quad) != 4:
            raise ValueError("court quad must have exactly 4 corners (TL, TR, BR, BL)")
        if court_width_m <= 0:
            raise ValueError("court_width_m must be positive")
        (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = (
            (float(p[0]), float(p[1])) for p in quad)

        far_px = math.hypot(trx - tlx, try_ - tly)
        near_px = math.hypot(brx - blx, bry - bly)
        s_far = far_px / court_width_m
        s_near = near_px / court_width_m
        y_far = (tly + try_) / 2.0
        y_near = (bly + bry) / 2.0

        if s_far <= 0 or s_near <= 0:
            raise ValueError("degenerate quad: a baseline has zero length")
        if abs(s_near - s_far) < 1e-9 or abs(y_near - y_far) < 1e-9:
            raise ValueError("degenerate quad: baselines have no depth separation")

        y0 = (s_far * y_near - s_near * y_far) / (s_far - s_near)
        a = s_near / (y_near - y0)
        if a <= 0:
            raise ValueError("degenerate quad: non-physical scale gradient")
        return cls(a, y0)

    def px_per_m(self, y):
        """Pixels per metre at image row ``y``, floored to stay positive."""
        return max(self.a * (float(y) - self.y0), _MIN_SCALE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_court_scale.py -q -p no:cacheprovider`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/court/scale.py tests/test_court_scale.py
git commit -m "feat(court): perspective scale (px/m) derived from the court quad"
```

---

### Task 2: Perspective-correct contact gate

Make `detect_contacts` / `detect_contacts_multi` accept a metre-based radius scaled by depth.
The existing fixed-pixel behaviour stays the default so every current caller and test is
unaffected.

**Files:**
- Modify: `badminton_analysis/stroke/events.py:45-72` (both detector signatures and the
  distance comparison)
- Test: `tests/test_stroke_events_perspective.py`

**Interfaces:**
- Consumes: `badminton_analysis.court.scale.PerspectiveScale` (Task 1) — only its `px_per_m(y)`
  method is used, so any object exposing `px_per_m` works.
- Produces: `detect_contacts(track, contact_px=80.0, contact_m=None, scale=None, lookahead=3,
  dir_change_deg=45.0, window_pre=20, window_post=15, min_gap=15)` and the same three new
  keyword arguments on `detect_contacts_multi`. Task 6 passes `contact_m=CONTACT_M, scale=...`.
  Module constant `CONTACT_M = 0.30`.

`CONTACT_M = 0.30` basis: at the mid-court scale of 355 px/m this is 107 px, comparable to
today's fixed 80 px, while scaling correctly to 32 px at the far baseline and 181 px at the near
one — instead of 80 px meaning 0.75 m far and 0.13 m near.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stroke_events_perspective.py
"""The contact radius must scale with depth, not be a fixed pixel count."""
import pytest

from badminton_analysis.stroke.events import (CONTACT_M, detect_contacts,
                                              detect_contacts_multi)


class FakeScale:
    """Minimal px_per_m provider: 100 px/m far (y=1000), 600 px/m near (y=2000)."""

    def px_per_m(self, y):
        return 100.0 + (float(y) - 1000.0) * 0.5


def _track(racket, shuttles, side="lower"):
    """Build a track whose shuttle turns sharply at index 2 (a contact)."""
    out = []
    for i, sh in enumerate(shuttles):
        row = {"frame": i, "shuttle": sh, "racket_upper": None, "racket_lower": None}
        row["racket_" + side] = racket
        out.append(row)
    return out


# Shuttle approaches, then reverses direction at index 2 -> a direction change
SHUTTLES = [(1000.0, 1000.0), (1010.0, 1000.0), (1020.0, 1000.0),
            (1010.0, 1000.0), (1000.0, 1000.0), (990.0, 1000.0)]


def test_far_contact_rejected_by_fixed_px_but_accepted_when_scaled():
    # Racket 60 px from the shuttle at a far-court row (y=1000, 100 px/m) is 0.60 m
    # away -- farther than CONTACT_M, so it must NOT fire when scaled...
    far_racket = (1080.0, 1000.0)
    scaled = detect_contacts(_track(far_racket, SHUTTLES),
                             contact_m=CONTACT_M, scale=FakeScale())
    assert scaled == []
    # ...yet the legacy fixed 80 px gate accepts it, which is the defect.
    legacy = detect_contacts(_track(far_racket, SHUTTLES), contact_px=80.0)
    assert len(legacy) == 1


def test_near_contact_accepted_when_scaled_but_missed_by_fixed_px():
    # At a near row (y=2000, 600 px/m) a genuine 0.15 m contact is 90 px, which the
    # fixed 80 px gate wrongly rejects.
    near = [(x, 2000.0) for x, _ in SHUTTLES]
    near_racket = (1020.0 + 90.0, 2000.0)
    scaled = detect_contacts(_track(near_racket, near),
                             contact_m=CONTACT_M, scale=FakeScale())
    assert len(scaled) == 1
    legacy = detect_contacts(_track(near_racket, near), contact_px=80.0)
    assert legacy == []


def test_default_behaviour_is_unchanged_when_scale_is_absent():
    track = _track((1020.0, 1000.0), SHUTTLES)
    assert detect_contacts(track) == detect_contacts(track, contact_px=80.0)


def test_multi_accepts_the_same_scaled_arguments():
    near = [(x, 2000.0) for x, _ in SHUTTLES]
    track = _track((1110.0, 2000.0), near, side="lower")
    got = detect_contacts_multi(track, contact_m=CONTACT_M, scale=FakeScale())
    assert len(got) == 1
    assert got[0]["hitter"] == "lower"


def test_contact_m_without_scale_is_rejected():
    with pytest.raises(ValueError):
        detect_contacts(_track((1020.0, 1000.0), SHUTTLES), contact_m=0.3)


def test_scale_uses_the_shuttle_row_not_a_constant():
    # Same pixel separation, two depths: far must reject, near must accept.
    sep = 80.0
    far = _track((1020.0 + sep, 1000.0), SHUTTLES)
    near_sh = [(x, 2000.0) for x, _ in SHUTTLES]
    near = _track((1020.0 + sep, 2000.0), near_sh)
    assert detect_contacts(far, contact_m=CONTACT_M, scale=FakeScale()) == []
    assert len(detect_contacts(near, contact_m=CONTACT_M, scale=FakeScale())) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_stroke_events_perspective.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'CONTACT_M'`

- [ ] **Step 3: Write minimal implementation**

Add the module constant near the top of `badminton_analysis/stroke/events.py`:

```python
CONTACT_M = 0.30
"""Contact radius in metres.

A fixed pixel radius is wrong across a perspective gradient: 80 px is 0.13 m at
the near baseline (603.8 px/m) but 0.75 m at the far one (106.3 px/m). At the
mid-court scale of 355 px/m, 0.30 m is 107 px -- comparable to the legacy 80 px
-- while scaling to 32 px far and 181 px near.
"""
```

Add a shared radius helper next to `_dist`:

```python
def _contact_radius(shuttle, contact_px, contact_m, scale):
    """Pixel radius allowed at this shuttle's depth.

    Returns ``contact_px`` unchanged unless a metre radius and a ``px_per_m``
    provider are both supplied, so existing callers are bit-for-bit unaffected.
    """
    if contact_m is None:
        return contact_px
    return contact_m * scale.px_per_m(shuttle[1])
```

In **both** `detect_contacts` and `detect_contacts_multi`, add the three keyword arguments
(`contact_m=None, scale=None`) after `contact_px`, validate them once at the top of each:

```python
    if contact_m is not None and scale is None:
        raise ValueError("contact_m requires a scale providing px_per_m(y)")
```

and replace each fixed comparison. In `detect_contacts` the line currently reading
`if _dist(racket, shuttle) >= contact_px:` becomes:

```python
        if _dist(racket, shuttle) >= _contact_radius(shuttle, contact_px, contact_m, scale):
            continue
```

Apply the identical substitution to the corresponding comparison inside
`detect_contacts_multi`'s per-side loop, so both sides use the shuttle-row radius.

- [ ] **Step 4: Run test to verify it passes, and that nothing regressed**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_stroke_events_perspective.py tests/test_stroke_events.py tests/test_bst_integration.py -q -p no:cacheprovider`
Expected: PASS — the 6 new tests plus every pre-existing stroke/BST test unchanged.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/events.py tests/test_stroke_events_perspective.py
git commit -m "feat(stroke): optional perspective-correct contact radius"
```

---

### Task 3: Single-target shuttle tracker

The costing spike selected chains greedily and non-overlapping, which fragmented a single
shuttle into ~38 pieces per rally (§0.23). Replace that with a one-shuttle prior: seed on the
best-scoring chain, then extend only with chains that are temporally and spatially continuous
with the accepted track. Scoring uses the §0.22 signature — a real shuttle's blob **area tracks
its speed**, because apparent size is motion-blur length; clutter has no such relation.

This task is pure geometry over point lists, so it is tested with synthetic sequences plus the
one hand-verified real trajectory. No video decoding.

**Files:**
- Create: `badminton_analysis/shuttle_track/shuttle_tracker.py`
- Test: `tests/test_shuttle_tracker.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `Candidate = namedtuple("Candidate", "x y area bright")`
  - `build_chains(candidates, min_speed=8.0, max_speed=250.0, accel_frac=0.25, accel_px=4.0,
    min_len=4) -> list[list[tuple[int, Candidate]]]`
  - `score_chain(chain) -> float`
  - `select_track(chains, n_frames, max_gap=6, predict_tol_px=40.0) -> dict[int, tuple[float, float]]`
  - Task 4 calls `build_chains` then `select_track`.

`candidates` is a list of length `n_frames`; element *i* is the list of `Candidate` in frame *i*.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shuttle_tracker.py
"""Tracker behaviour: ballistic chaining, the area~speed score, and one-shuttle selection."""
import pytest

from badminton_analysis.shuttle_track.shuttle_tracker import (Candidate, build_chains,
                                                              score_chain, select_track)

# The 30-frame trajectory hand-verified in design-doc section 0.22 (t=68.485..68.969s).
# Area tracks speed here (speed ratio 13.7, area ratio 13.9) -- the signature the score keys on.
VERIFIED = [
    (2493, 758, 1053), (2397, 774, 881), (2322, 790, 714), (2261, 804, 585),
    (2211, 815, 500), (2168, 825, 413), (2133, 833, 365), (2102, 842, 319),
    (2076, 850, 293), (2053, 856, 245), (2032, 864, 180), (2013, 872, 233),
    (1997, 878, 218), (1981, 885, 202), (1968, 892, 203), (1956, 899, 186),
    (1944, 905, 182), (1933, 912, 169), (1924, 919, 166), (1914, 925, 151),
    (1906, 932, 150), (1898, 939, 147), (1891, 946, 137), (1884, 953, 138),
    (1877, 960, 135), (1871, 967, 138), (1865, 974, 131), (1861, 982, 114),
    (1855, 988, 90), (1850, 995, 76),
]


def _verified_frames(n_frames=40, offset=5):
    frames = [[] for _ in range(n_frames)]
    for i, (x, y, a) in enumerate(VERIFIED):
        frames[offset + i].append(Candidate(float(x), float(y), a, 200.0))
    return frames


def test_recovers_the_verified_trajectory_end_to_end():
    frames = _verified_frames()
    chains = build_chains(frames)
    track = select_track(chains, len(frames))
    for i, (x, y, _a) in enumerate(VERIFIED):
        got = track.get(5 + i)
        assert got is not None, f"frame {5 + i} missing"
        assert abs(got[0] - x) <= 1.0 and abs(got[1] - y) <= 1.0


def test_stationary_points_never_chain():
    # The spike's original bug: min_speed was not enforced when extending, so a
    # static blob chained across the whole window.
    frames = [[Candidate(500.0, 500.0, 100, 200.0)] for _ in range(30)]
    assert build_chains(frames) == []


def test_area_uncorrelated_with_speed_scores_below_a_shuttle():
    n = 12
    shuttle, clutter = [[] for _ in range(n)], [[] for _ in range(n)]
    x, v = 100.0, 60.0
    for i in range(n):
        shuttle[i].append(Candidate(x, 500.0, int(round(v * 10)), 200.0))
        clutter[i].append(Candidate(x, 500.0, 300, 200.0))   # constant area
        x += v
        v *= 0.88
    s_chain = build_chains(shuttle)
    c_chain = build_chains(clutter)
    assert s_chain and c_chain
    assert score_chain(max(s_chain, key=len)) > score_chain(max(c_chain, key=len))


def test_one_shuttle_prior_rejects_a_simultaneous_second_track():
    """Two disjoint ballistic tracks: only the better-scoring one survives."""
    n = 20
    frames = [[] for _ in range(n)]
    x1, v1 = 100.0, 50.0
    for i in range(n):                      # area tracks speed -> shuttle-like
        frames[i].append(Candidate(x1, 400.0, int(round(v1 * 10)), 210.0))
        x1 += v1
        v1 *= 0.9
    for i in range(n):                      # constant area -> clutter-like
        frames[i].append(Candidate(3000.0 - 30.0 * i, 1500.0, 250, 210.0))
    track = select_track(build_chains(frames), n)
    assert track
    assert all(pt[1] == pytest.approx(400.0, abs=1.0) for pt in track.values())


def test_gap_tolerance_bridges_a_short_occlusion():
    n = 24
    frames = [[] for _ in range(n)]
    x, v = 100.0, 60.0
    for i in range(n):
        if 10 <= i <= 12:                   # occluded for 3 frames
            x += v
            v *= 0.9
            continue
        frames[i].append(Candidate(x, 500.0, int(round(v * 10)), 200.0))
        x += v
        v *= 0.9
    track = select_track(build_chains(frames), n)
    covered = sorted(track)
    assert covered[0] < 10 and covered[-1] > 12
    assert len(track) >= 16


def test_empty_and_short_inputs_are_safe():
    assert build_chains([]) == []
    assert build_chains([[], []]) == []
    assert select_track([], 5) == {}


def test_selected_frames_are_unique_and_in_range():
    frames = _verified_frames()
    track = select_track(build_chains(frames), len(frames))
    assert all(0 <= f < len(frames) for f in track)
    assert len(track) == len(set(track))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_shuttle_tracker.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'badminton_analysis.shuttle_track.shuttle_tracker'`

- [ ] **Step 3: Write minimal implementation**

```python
# badminton_analysis/shuttle_track/shuttle_tracker.py
"""Single-target shuttle tracking over per-frame candidate blobs.

Two ideas carry this module.

1. Ballistic chaining. A shuttle's next position is close to the constant-velocity
   prediction, so chains are grown by predicting ``2*last - prev`` and accepting
   only near neighbours. ``min_speed`` is enforced on EVERY step, not just the
   seeding triple -- omitting that let a stationary noise blob chain across an
   entire window in the costing spike.

2. The area~speed signature. A real shuttle's apparent size is motion-blur
   length, so blob area tracks instantaneous speed almost proportionally
   (measured 13.7 vs 13.9 on the verified trajectory). Clutter -- a limb, a
   flickering edge -- has no such relation, which makes the correlation a cheap
   and discriminating score.

Selection applies a one-shuttle prior: seed on the best chain, then extend only
with chains continuous in time and space with what is already accepted. The
alternative (greedy non-overlapping selection over all chains) fragmented one
shuttle into roughly 38 pieces per rally.
"""
import math
from collections import namedtuple

import numpy as np
from scipy.spatial import cKDTree

Candidate = namedtuple("Candidate", "x y area bright")


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _trees(candidates):
    out = []
    for frame in candidates:
        if frame:
            out.append(cKDTree(np.array([[c.x, c.y] for c in frame], dtype=float)))
        else:
            out.append(None)
    return out


def build_chains(candidates, min_speed=8.0, max_speed=250.0, accel_frac=0.25,
                 accel_px=4.0, min_len=4):
    """Grow ballistic chains of ``(frame_index, Candidate)`` pairs.

    ``max_speed`` must stay well below the frame width: a radius wider than the
    search band makes every candidate a neighbour of every other and the search
    degenerates to O(n^2) per frame.
    """
    n = len(candidates)
    if n < 3:
        return []
    trees = _trees(candidates)
    chains = []
    for f in range(n - 2):
        if not (trees[f] and trees[f + 1] and trees[f + 2]):
            continue
        for c1 in candidates[f]:
            for i2 in trees[f + 1].query_ball_point((c1.x, c1.y), max_speed):
                c2 = candidates[f + 1][i2]
                s1 = _dist(c1, c2)
                if s1 < min_speed:
                    continue
                pred = (2 * c2.x - c1.x, 2 * c2.y - c1.y)
                for i3 in trees[f + 2].query_ball_point(pred, accel_frac * s1 + accel_px):
                    c3 = candidates[f + 2][i3]
                    if not (min_speed <= _dist(c2, c3) <= max_speed):
                        continue
                    chain = [(f, c1), (f + 1, c2), (f + 2, c3)]
                    prev, last, k = c2, c3, f + 3
                    while k < n and trees[k]:
                        speed = _dist(prev, last)
                        pr = (2 * last.x - prev.x, 2 * last.y - prev.y)
                        idx = trees[k].query_ball_point(pr, accel_frac * speed + accel_px)
                        nxt = [candidates[k][j] for j in idx
                               if min_speed <= _dist(last, candidates[k][j]) <= max_speed]
                        if not nxt:
                            break
                        pick = min(nxt, key=lambda c: _dist(pr, c))
                        chain.append((k, pick))
                        prev, last = last, pick
                        k += 1
                    if len(chain) >= min_len:
                        chains.append(chain)
                    break
    return chains


def score_chain(chain):
    """Higher is more shuttle-like. Zero when the area~speed relation is absent."""
    pts = [c for _f, c in chain]
    if len(pts) < 4:
        return 0.0
    speeds = [_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    areas = [float(p.area) for p in pts[:-1]]
    if np.std(speeds) < 1e-6 or np.std(areas) < 1e-6:
        return 0.0
    r = float(np.corrcoef(speeds, areas)[0, 1])
    if not math.isfinite(r) or r <= 0.0:
        return 0.0
    bright = float(np.mean([p.bright for p in pts])) / 255.0
    return (r ** 2) * len(chain) * (0.5 + bright)


def select_track(chains, n_frames, max_gap=6, predict_tol_px=40.0):
    """One-shuttle selection: best chain as seed, then continuous extensions."""
    scored = sorted(((score_chain(c), c) for c in chains), key=lambda z: -z[0])
    scored = [(s, c) for s, c in scored if s > 0.0]
    if not scored:
        return {}

    track = {}
    for f, cand in scored[0][1]:
        track[f] = (cand.x, cand.y)

    for _s, chain in scored[1:]:
        frames = [f for f, _c in chain]
        if any(f in track for f in frames):
            continue
        known = sorted(track)
        before = [f for f in known if f < frames[0]]
        after = [f for f in known if f > frames[-1]]
        ok = False
        if before and frames[0] - before[-1] <= max_gap:
            ok = _dist(track[before[-1]], chain[0][1]) <= predict_tol_px * (
                frames[0] - before[-1])
        if not ok and after and after[0] - frames[-1] <= max_gap:
            ok = _dist(track[after[0]], chain[-1][1]) <= predict_tol_px * (
                after[0] - frames[-1])
        if not ok:
            continue
        for f, cand in chain:
            if 0 <= f < n_frames:
                track[f] = (cand.x, cand.y)
    return track
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_shuttle_tracker.py -q -p no:cacheprovider`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/shuttle_track/shuttle_tracker.py tests/test_shuttle_tracker.py
git commit -m "feat(shuttle): single-target tracker with area~speed chain scoring"
```

---

### Task 4: Candidate extraction with court, structural and per-band masks

This is the precision work from §0.23 item 1. In the costing spike the global
`MAX_CAND_PER_FRAME = 60` cap **bound on 100 % of frames** (533,718 candidates dropped), so
recall figures were shaped by an arbitrary truncation and a dim far-court shuttle could be
crowded out by bright near-court clutter. Three fixes: mask to the court and its airspace, mask
out pixels that flicker structurally (adjacent courts, spectators, banner edges), and budget
candidates **per depth band** rather than globally.

**Files:**
- Create: `badminton_analysis/shuttle_track/classical.py`
- Test: `tests/test_classical_candidates.py`

**Interfaces:**
- Consumes: `Candidate` from Task 3.
- Produces:
  - `build_masks(frames, quad, band_top_px=620, activity_frac=0.35) -> dict` with keys
    `"court"` (uint8 HxW), `"static"` (uint8 HxW), `"median"` (uint8 HxW), `"bands"`
    (list of `(y_lo, y_hi)`).
  - `frame_candidates(gray, masks, diff_thresh=35, area_lo=15, area_hi=1500,
    aspect_max=2.5, min_bright=110.0, band_budget=12, gain_correct=True)
    -> tuple[list[Candidate], int]` returning the candidates and the number dropped by
    budgeting.
  - Module constants `N_BANDS = 6`, `DEFAULT_BAND_BUDGET = 12`, `GAIN_LIMITS = (0.5, 2.0)`.
  - Task 5 calls both.

Illumination is handled here too (the fourth part of §0.23 item 1). A camera's auto-exposure
drifts over a multi-minute clip, and once the frame's overall level departs from the background
model's, *every* pixel exceeds the difference threshold and the frame floods with candidates. So
each frame is gain-corrected to the background's mean level before differencing, with the ratio
clamped to `GAIN_LIMITS` so a frame dominated by one large bright object cannot distort it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classical_candidates.py
"""Masking and per-band budgeting for classical shuttle candidate extraction."""
import numpy as np
import pytest

from badminton_analysis.shuttle_track.classical import (N_BANDS, build_masks,
                                                        frame_candidates)

H, W = 300, 400
QUAD = [(120.0, 120.0), (280.0, 120.0), (380.0, 260.0), (20.0, 260.0)]


def _blank(v=40):
    return np.full((H, W), v, np.uint8)


def _frames(n=16, v=40):
    return [_blank(v) for _ in range(n)]


def _put(img, cx, cy, r=3, v=230):
    ys, xs = np.ogrid[:img.shape[0], :img.shape[1]]
    img[(xs - cx) ** 2 + (ys - cy) ** 2 <= r * r] = v


def test_court_mask_covers_the_quad_and_its_airspace():
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    assert masks["court"][200, 200] > 0          # inside the quad
    assert masks["court"][80, 200] > 0           # airspace above it
    assert masks["court"][200, 5] == 0           # outside, left of the quad


def test_structural_flicker_is_masked_out():
    # A pixel that changes in most frames is structure, not a shuttle.
    frames = _frames(20)
    for i, f in enumerate(frames):
        if i % 2 == 0:
            _put(f, 200, 200, r=4)
    masks = build_masks(frames, QUAD, band_top_px=60, activity_frac=0.35)
    assert masks["static"][200, 200] == 0


def test_a_moving_blob_is_found_and_a_masked_one_is_not():
    frames = _frames(16)
    masks = build_masks(frames, QUAD, band_top_px=60)
    g = _blank()
    _put(g, 200, 200, r=3)
    found, _dropped = frame_candidates(g, masks)
    assert len(found) == 1
    assert found[0].x == pytest.approx(200, abs=2)
    outside = _blank()
    _put(outside, 5, 200, r=3)
    assert frame_candidates(outside, masks)[0] == []


def test_dim_blob_below_brightness_floor_is_rejected():
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    g = _blank()
    _put(g, 200, 200, r=3, v=90)      # above diff threshold, below min_bright
    assert frame_candidates(g, masks, min_bright=110.0)[0] == []


def test_budget_is_per_band_so_a_far_blob_survives_near_clutter():
    """A single far-court blob must not be crowded out by many near-court blobs."""
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    g = _blank()
    _put(g, 200, 130, r=3, v=255)                      # one far (small y) blob
    for i in range(20):                                # crowd the near band
        _put(g, 40 + i * 16, 250, r=3, v=255)
    found, dropped = frame_candidates(g, masks, band_budget=4)
    assert any(c.y < 160 for c in found), "far-band candidate was crowded out"
    assert dropped > 0


def test_dropped_count_is_zero_when_no_band_is_over_budget():
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    g = _blank()
    _put(g, 200, 200, r=3)
    assert frame_candidates(g, masks, band_budget=12)[1] == 0


def test_bands_partition_the_masked_rows_without_overlap():
    masks = build_masks(_frames(), QUAD, band_top_px=60)
    bands = masks["bands"]
    assert len(bands) == N_BANDS
    for (_lo1, hi1), (lo2, _hi2) in zip(bands, bands[1:]):
        assert hi1 == lo2


def test_build_masks_rejects_a_bad_quad():
    with pytest.raises(ValueError):
        build_masks(_frames(), QUAD[:2], band_top_px=60)


# The drift tests need a brighter baseline than the other tests: the gain ratio must
# land inside GAIN_LIMITS (0.5, 2.0) while the uncorrected difference still clears
# diff_thresh=35. Background 100 drifting to 160 gives ratio 0.625 (inside) and an
# uncorrected difference of 60 (over threshold). A 40 -> 130 drift would be ratio
# 0.31, which the clamp rejects by design, so correction would not engage at all.
DRIFT_BG, DRIFT_LEVEL = 100, 160


def test_global_brightness_drift_does_not_flood_candidates():
    """Auto-exposure drift must not make every pixel a candidate."""
    masks = build_masks(_frames(v=DRIFT_BG), QUAD, band_top_px=60)
    drifted = np.full((H, W), DRIFT_LEVEL, np.uint8)      # no moving object at all
    found, _dropped = frame_candidates(drifted, masks, gain_correct=True)
    assert found == []
    # Uncorrected, the same frame differs everywhere -- the defect being fixed.
    diff_map = np.abs(drifted.astype(int) - masks["median"].astype(int))
    assert diff_map.max() > 35


def test_gain_correction_still_finds_a_real_blob_under_drift():
    masks = build_masks(_frames(v=DRIFT_BG), QUAD, band_top_px=60)
    g = np.full((H, W), 120, np.uint8)            # mildly drifted background
    _put(g, 200, 200, r=3, v=255)                 # plus a genuine bright mover
    found, _dropped = frame_candidates(g, masks, gain_correct=True)
    assert len(found) == 1
    assert found[0].x == pytest.approx(200, abs=2)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_classical_candidates.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'badminton_analysis.shuttle_track.classical'`

- [ ] **Step 3: Write minimal implementation**

```python
# badminton_analysis/shuttle_track/classical.py
"""Classical static-camera shuttle detection.

Emits the same contract as ``tracknet.track_video`` -- ``{frame: (x, y) | None}``
-- so it is a drop-in alternative producer. It requires a static camera because
it models the background with a per-pixel median, and therefore complements
TrackNetV3 (which serves moving-camera broadcast footage) rather than replacing
it.

Three masks keep the candidate count honest. In the costing spike a single global
cap of 60 candidates/frame bound on every frame, dropping 533,718 candidates, so
recall was shaped by arbitrary truncation and a dim far-court shuttle could lose
to bright near-court clutter:

* ``court``  -- the court quad plus the airspace above it, since the shuttle
  spends most of its flight above the floor.
* ``static`` -- pixels that differ from the median in more than ``activity_frac``
  of sampled frames are structural flicker (adjacent courts, spectators, banner
  edges), not a passing shuttle.
* per-band budgets -- candidates are capped within each depth band, so the far
  court gets its own allowance instead of competing with the near court.
"""
import cv2
import numpy as np

from .shuttle_tracker import Candidate

N_BANDS = 6
DEFAULT_BAND_BUDGET = 12
GAIN_LIMITS = (0.5, 2.0)


def build_masks(frames, quad, band_top_px=620, activity_frac=0.35):
    """Background median plus court/structural masks and depth bands.

    ``frames`` are grayscale arrays sampled across the clip. ``band_top_px`` is
    how far above the quad's highest row to admit as shuttle airspace.
    """
    if quad is None or len(quad) != 4:
        raise ValueError("court quad must have exactly 4 corners (TL, TR, BR, BL)")
    if not frames:
        raise ValueError("need at least one frame to build a background model")

    h, w = frames[0].shape[:2]
    stack = np.stack(frames)
    median = np.median(stack, axis=0).astype(np.uint8)

    pts = np.array([[int(round(x)), int(round(y))] for x, y in quad], np.int32)
    court = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(court, pts, 255)
    # extend upward into the airspace, keeping the quad's horizontal extent
    top = max(0, int(min(p[1] for p in pts)) - int(band_top_px))
    x_lo = max(0, int(min(p[0] for p in pts)))
    x_hi = min(w, int(max(p[0] for p in pts)) + 1)
    court[top:int(min(p[1] for p in pts)) + 1, x_lo:x_hi] = 255

    activity = np.zeros((h, w), np.float32)
    for g in frames:
        activity += (cv2.absdiff(g, median) > 35).astype(np.float32)
    activity /= float(len(frames))
    static = np.where(activity <= activity_frac, 255, 0).astype(np.uint8)

    rows = np.nonzero(court.any(axis=1))[0]
    y_lo, y_hi = (int(rows[0]), int(rows[-1]) + 1) if rows.size else (0, h)
    edges = np.linspace(y_lo, y_hi, N_BANDS + 1).round().astype(int)
    bands = [(int(edges[i]), int(edges[i + 1])) for i in range(N_BANDS)]

    return {"court": court, "static": static, "median": median, "bands": bands}


def _gain_correct(gray, median):
    """Rescale ``gray`` to the background's mean level.

    Auto-exposure drift shifts the whole frame, and once the overall level departs
    from the background model every pixel exceeds the difference threshold and the
    frame floods with candidates. The ratio is clamped so a frame dominated by one
    large bright object cannot distort the correction.
    """
    cur = float(gray.mean())
    if cur <= 1e-6:
        return gray
    ratio = float(median.mean()) / cur
    lo, hi = GAIN_LIMITS
    if not (lo < ratio < hi):
        return gray
    return np.clip(gray.astype(np.float32) * ratio, 0, 255).astype(np.uint8)


def frame_candidates(gray, masks, diff_thresh=35, area_lo=15, area_hi=1500,
                     aspect_max=2.5, min_bright=110.0,
                     band_budget=DEFAULT_BAND_BUDGET, gain_correct=True):
    """Compact bright movers in this frame, budgeted per depth band.

    Returns ``(candidates, n_dropped)``. A non-zero ``n_dropped`` means a band
    hit its budget, which must be reported rather than silently truncating.
    """
    if gain_correct:
        gray = _gain_correct(gray, masks["median"])
    allow = cv2.bitwise_and(masks["court"], masks["static"])
    diff = cv2.absdiff(gray, masks["median"])
    diff = cv2.bitwise_and(diff, diff, mask=allow)
    _, bw = cv2.threshold(diff, diff_thresh, 255, cv2.THRESH_BINARY)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(bw, connectivity=8)

    per_band = {i: [] for i in range(len(masks["bands"]))}
    for k in range(1, n):
        area = int(stats[k, cv2.CC_STAT_AREA])
        if not (area_lo <= area <= area_hi):
            continue
        bw_, bh_ = int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT])
        if max(bw_, bh_) > aspect_max * max(min(bw_, bh_), 1):
            continue
        x0, y0 = int(stats[k, cv2.CC_STAT_LEFT]), int(stats[k, cv2.CC_STAT_TOP])
        bright = float(gray[y0:y0 + bh_, x0:x0 + bw_].max())
        if bright < min_bright:
            continue
        cx, cy = float(cent[k][0]), float(cent[k][1])
        for bi, (lo, hi) in enumerate(masks["bands"]):
            if lo <= cy < hi:
                per_band[bi].append(Candidate(cx, cy, area, bright))
                break

    out, dropped = [], 0
    for bi, items in per_band.items():
        if len(items) > band_budget:
            items.sort(key=lambda c: -c.bright)
            dropped += len(items) - band_budget
            items = items[:band_budget]
        out.extend(items)
    return out, dropped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_classical_candidates.py -q -p no:cacheprovider`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/shuttle_track/classical.py tests/test_classical_candidates.py
git commit -m "feat(shuttle): court, structural, per-band and gain-corrected candidates"
```

---

### Task 5: `track_video` and the static-camera test

Join Tasks 3 and 4 into a producer matching `tracknet.track_video`'s contract, and add the
predicate the router needs. Decode is 64 % of per-frame cost and the costing spike paid it twice
(median pass, then detect pass), so this implementation makes **one** pass, seeding the
background from an initial sample and reusing it.

**Files:**
- Modify: `badminton_analysis/shuttle_track/classical.py` (append)
- Test: `tests/test_classical_track_video.py`

**Interfaces:**
- Consumes: `build_masks`, `frame_candidates` (Task 4); `build_chains`, `select_track` (Task 3).
- Produces:
  - `is_static_camera(video_path, samples=12, max_shift_px=1.0) -> bool`
  - `track_video(video_path, quad, max_frames=None, warmup=48, refresh_every=600,
    reservoir=48, progress=None) -> tuple[dict[int, tuple | None], dict]` — trajectory keyed by
    0-based frame index (so `system.py` applies the same `+1` alignment it already applies to
    TrackNet), plus a stats dict with keys `"frames"`, `"candidates"`, `"dropped"`, `"covered"`,
    `"refreshes"`.
- Task 6 calls both.

`refresh_every` exists because a median built once from the opening frames decays: over a
multi-minute clip the court gains bags, benches shift and players linger, and any such change
becomes a permanent false mover. The reservoir keeps recent sampled frames so the background is
periodically rebuilt — still within a single decode pass, which matters because decode is 64 % of
per-frame cost.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classical_track_video.py
"""End-to-end classical tracking over a synthesised static-camera clip."""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from badminton_analysis.shuttle_track.classical import is_static_camera, track_video

W, H, N = 960, 540, 90
QUAD = [(280.0, 240.0), (680.0, 240.0), (900.0, 500.0), (60.0, 500.0)]

# Shuttle parameters chosen so every drawn frame stays above the tracker's
# min_speed of 8 px/frame: v starts at 30 and decays 0.97 over 40 frames, ending
# at 30*0.97**39 = 9.3. Total travel is ~700 px, which fits inside W=960.
# Radius is floored at 3 so the blob area stays above area_lo=15, and otherwise
# tracks speed, reproducing the area~speed signature the score keys on.
SHUTTLE_FIRST, SHUTTLE_LAST = 10, 50


def _write(path, shift_per_frame=0.0, with_shuttle=True, bg=60):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (W, H))
    x, y, v = 880.0, 150.0, -30.0
    for i in range(N):
        frame = np.full((H, W, 3), bg, np.uint8)
        # A GRID, not a single horizontal line: phase correlation needs texture in
        # both axes, and a lone horizontal line gives no horizontal signal, so a
        # panning camera would be misreported as static.
        for gx in range(0, W, 80):
            cv2.line(frame, (gx, 0), (gx, H), (110, 110, 110), 1)
        for gy in range(0, H, 80):
            cv2.line(frame, (0, gy), (W, gy), (110, 110, 110), 1)
        if shift_per_frame:
            m = np.float32([[1, 0, shift_per_frame * i], [0, 1, 0]])
            frame = cv2.warpAffine(frame, m, (W, H))
        if with_shuttle and SHUTTLE_FIRST <= i < SHUTTLE_LAST:
            r = max(3, int(round(abs(v) / 4.0)))
            cv2.circle(frame, (int(x), int(y)), r, (245, 245, 245), -1)
            x += v
            y += 2.0
            v *= 0.97
        vw.write(frame)
    vw.release()


def test_recovers_a_synthetic_shuttle(tmp_path):
    p = tmp_path / "static.mp4"
    _write(p)
    traj, stats = track_video(str(p), QUAD, warmup=SHUTTLE_FIRST)
    assert stats["frames"] > 0
    hits = [f for f, pt in traj.items() if pt is not None]
    assert len(hits) >= 20, f"only {len(hits)} frames recovered of 40 drawn"
    assert all(0 <= traj[f][0] <= W for f in hits)


def test_contract_matches_tracknet_track_video(tmp_path):
    p = tmp_path / "static.mp4"
    _write(p)
    traj, _stats = track_video(str(p), QUAD, warmup=SHUTTLE_FIRST)
    assert isinstance(traj, dict)
    assert all(isinstance(f, int) for f in traj)
    for pt in traj.values():
        assert pt is None or (isinstance(pt, tuple) and len(pt) == 2)
    assert min(traj) == 0, "frames must be 0-based, as tracknet.track_video is"


def test_blank_clip_yields_no_track(tmp_path):
    p = tmp_path / "blank.mp4"
    _write(p, with_shuttle=False)
    _traj, stats = track_video(str(p), QUAD, warmup=SHUTTLE_FIRST)
    # Allowed to be non-zero only marginally: codec noise can survive the masks,
    # but it must never assemble into a scoring ballistic track.
    assert stats["covered"] <= 2, f"blank clip produced {stats['covered']} points"


def test_max_frames_is_respected(tmp_path):
    p = tmp_path / "static.mp4"
    _write(p)
    _traj, stats = track_video(str(p), QUAD, warmup=SHUTTLE_FIRST, max_frames=30)
    assert stats["frames"] <= 30


def test_background_is_refreshed_periodically(tmp_path, monkeypatch):
    """A background built once decays; the reservoir must rebuild it."""
    import badminton_analysis.shuttle_track.classical as clmod

    calls = []
    real = clmod.build_masks

    def counting(frames, quad, **kw):
        calls.append(len(frames))
        return real(frames, quad, **kw)

    monkeypatch.setattr(clmod, "build_masks", counting)
    p = tmp_path / "static.mp4"
    _write(p)
    _traj, stats = clmod.track_video(str(p), QUAD, warmup=SHUTTLE_FIRST,
                                    refresh_every=30, reservoir=8)
    assert len(calls) >= 3, f"expected periodic rebuilds, saw {len(calls)}"
    assert stats["refreshes"] == len(calls) - 1


def test_static_camera_detected(tmp_path):
    p = tmp_path / "static.mp4"
    _write(p)
    assert is_static_camera(str(p)) is True


def test_panning_camera_detected(tmp_path):
    p = tmp_path / "moving.mp4"
    _write(p, shift_per_frame=3.0)
    assert is_static_camera(str(p)) is False


def test_missing_file_is_reported_not_crashed(tmp_path):
    with pytest.raises(ValueError):
        track_video(str(tmp_path / "nope.mp4"), QUAD)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_classical_track_video.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'is_static_camera'`

- [ ] **Step 3: Write minimal implementation**

Append to `badminton_analysis/shuttle_track/classical.py`:

```python
def is_static_camera(video_path, samples=12, max_shift_px=1.0):
    """True when the camera does not move, which this detector requires.

    Uses phase correlation between spaced frame pairs. Median shift is used so a
    single mis-registered pair (a flash, a cut) does not flip the verdict.
    """
    cap = cv2.VideoCapture(str(video_path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total < 4:
            return True
        step = max(1, total // (samples + 1))
        shifts, prev = [], None
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(i * step, total - 1))
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (320, 180))
            cur = small.astype(np.float32)
            if prev is not None:
                (dx, dy), _resp = cv2.phaseCorrelate(prev, cur)
                shifts.append((dx * dx + dy * dy) ** 0.5)
            prev = cur
        if not shifts:
            return True
        return bool(float(np.median(shifts)) < max_shift_px)
    finally:
        cap.release()


def track_video(video_path, quad, max_frames=None, warmup=48, refresh_every=600,
                reservoir=48, progress=None):
    """Classical dense shuttle trajectory for a static-camera clip.

    Returns ``(trajectory, stats)`` where ``trajectory`` is keyed by 0-based
    frame index and matches ``tracknet.track_video``'s value contract.

    Decoding is 64% of per-frame cost, so this makes a SINGLE pass: the first
    ``warmup`` frames seed the background model and are then scored from the
    buffer. A background built once decays -- over a multi-minute clip the court
    gains bags, benches shift and players linger, and each such change becomes a
    permanent false mover -- so a reservoir of recent frames rebuilds it every
    ``refresh_every`` frames, still without decoding anything twice.
    """
    from collections import deque

    from .shuttle_tracker import build_chains, select_track

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ValueError(f"cannot open video: {video_path}")
    try:
        warm, recent = [], deque(maxlen=max(4, int(reservoir)))
        masks, refreshes = None, 0
        per_frame, dropped_total, cand_total = [], 0, 0
        idx = 0
        sample_step = max(1, warmup // max(4, int(reservoir)) or 1)

        while True:
            if max_frames is not None and idx >= max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            if masks is None:
                warm.append(gray)
                if len(warm) >= warmup:
                    masks = build_masks(warm, quad)
                    recent.extend(warm[::sample_step])
                    for g in warm:
                        cands, drop = frame_candidates(g, masks)
                        per_frame.append(cands)
                        cand_total += len(cands)
                        dropped_total += drop
                    warm = []
            else:
                if idx % 8 == 0:
                    recent.append(gray)
                if refresh_every and idx % refresh_every == 0 and len(recent) >= 4:
                    masks = build_masks(list(recent), quad)
                    refreshes += 1
                cands, drop = frame_candidates(gray, masks)
                per_frame.append(cands)
                cand_total += len(cands)
                dropped_total += drop

            idx += 1
            if progress is not None and idx % 200 == 0:
                progress(idx)

        if masks is None:                      # clip shorter than warmup
            if not warm:
                return {}, {"frames": 0, "candidates": 0, "dropped": 0,
                            "covered": 0, "refreshes": 0}
            masks = build_masks(warm, quad)
            for g in warm:
                cands, drop = frame_candidates(g, masks)
                per_frame.append(cands)
                cand_total += len(cands)
                dropped_total += drop

        n = len(per_frame)
        track = select_track(build_chains(per_frame), n)
        traj = {f: track.get(f) for f in range(n)}
        stats = {"frames": n, "candidates": cand_total, "dropped": dropped_total,
                 "covered": sum(1 for v in traj.values() if v is not None),
                 "refreshes": refreshes}
        return traj, stats
    finally:
        cap.release()
```

Note on the refresh test: `build_masks` is called once after warmup and then once per refresh, so
`stats["refreshes"] == n_calls - 1`. The implementation must call the **module-level**
`build_masks` (not a local alias captured at import time) so the test's `monkeypatch.setattr` is
observed.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_classical_track_video.py -q -p no:cacheprovider`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/shuttle_track/classical.py tests/test_classical_track_video.py
git commit -m "feat(shuttle): classical track_video single-pass plus static-camera test"
```

---

### Task 6: Router wiring in `system.py`

`_run_shuttle_pretrack` currently only ever tries TrackNetV3. Route by footage class: static
camera → classical (which needs no weights and no `SHUTTLE_PRETRACK_MAX_FRAMES` budget, being
~25× cheaper), moving camera → TrackNetV3 exactly as today. Pass the perspective scale into
stroke recognition so Task 2's gate is actually used.

**Files:**
- Modify: `badminton_analysis/system.py:749-792` (`_run_shuttle_pretrack`)
- Modify: `badminton_analysis/system.py:171` (add `self._shuttle_scale = None`)
- Modify: `badminton_analysis/stroke_recog/hits.py:20` (`hit_events` gains the gate keywords)
- Modify: `badminton_analysis/stroke_recog/recognizer.py:51,70` (`label_rally` threads them)
- Test: `tests/test_classical_router.py`

**Interfaces:**
- Consumes: `is_static_camera`, `track_video` (Task 5); `PerspectiveScale` (Task 1);
  `CONTACT_M` (Task 2).
- Produces: `self._shuttle_source` gains the value `"classical"`; `self._shuttle_scale` holds a
  `PerspectiveScale` or `None`; `hit_events(track, contact_m=None, scale=None)`;
  `label_rally(self, track, frame_lookup, court_corners, video_wh, contact_m=None, scale=None)`.

The contact detector is **not** called from `system.py`. The real chain is
`system._run_stroke_recognition` → `StrokeRecognizer.label_rally` → `hits.hit_events` →
`stroke.events.detect_contacts_multi`, so the scale threads through those two intermediate
functions as optional keywords.

**Deliberate scoping decision:** the perspective gate defaults to **off** at every level, so
`hit_events(track)` and `label_rally(...)` behave exactly as today and every existing BST test
passes untouched. Only the classical route turns it on. Making it the default for all footage is
a genuine improvement but a *behaviour change* for the yolo and TrackNetV3 paths that would
require re-checking the BST integration fixtures — that belongs in its own reviewed change, not
smuggled in here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classical_router.py
"""The shuttle pre-pass must route by footage class and stay non-fatal."""
import types

import pytest

from badminton_analysis import system as sysmod


class _Sys:
    """Minimal stand-in exposing only what _run_shuttle_pretrack touches."""

    def __init__(self, **kw):
        self.video_path = kw.get("video_path", "video.mp4")
        self.tracknet_weights = kw.get("tracknet_weights", "")
        self.inpaintnet_weights = ""
        self.analyze_technique = True
        self.save_dir = kw.get("save_dir", ".")
        self.court_roi_corners = [(0, 0), (10, 10)]
        self.court_corners = [(120.0, 120.0), (280.0, 120.0), (380.0, 260.0), (20.0, 260.0)]
        self._shuttle_trajectory = None
        self._shuttle_source = "yolo"
        self._shuttle_scale = None

    _run_shuttle_pretrack = sysmod.BadmintonAnalysisSystem._run_shuttle_pretrack


def test_static_camera_uses_the_classical_detector(monkeypatch):
    calls = {}
    fake = types.SimpleNamespace(
        is_static_camera=lambda p, **k: calls.setdefault("static", True) or True,
        track_video=lambda p, quad, **k: ({0: (5.0, 6.0), 1: None},
                                          {"frames": 2, "candidates": 3,
                                           "dropped": 0, "covered": 1}),
    )
    monkeypatch.setitem(__import__("sys").modules,
                        "badminton_analysis.shuttle_track.classical", fake)
    s = _Sys()
    s._run_shuttle_pretrack()
    assert s._shuttle_source == "classical"
    # aligned to the match loop's 1-based frame_count, as the tracknet path is
    assert s._shuttle_trajectory[1] == (5.0, 6.0)
    assert s._shuttle_trajectory[2] is None


def test_moving_camera_falls_through_to_tracknet(monkeypatch):
    fake = types.SimpleNamespace(is_static_camera=lambda p, **k: False,
                                 track_video=lambda *a, **k: pytest.fail("must not run"))
    monkeypatch.setitem(__import__("sys").modules,
                        "badminton_analysis.shuttle_track.classical", fake)
    s = _Sys(tracknet_weights="")     # no weights -> tracknet path is a no-op
    s._run_shuttle_pretrack()
    assert s._shuttle_source == "yolo"
    assert s._shuttle_trajectory is None


def test_classical_failure_is_never_fatal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("decode exploded")

    fake = types.SimpleNamespace(is_static_camera=lambda p, **k: True, track_video=boom)
    monkeypatch.setitem(__import__("sys").modules,
                        "badminton_analysis.shuttle_track.classical", fake)
    s = _Sys()
    s._run_shuttle_pretrack()          # must not raise
    assert s._shuttle_source == "yolo"


def test_perspective_scale_is_built_from_the_court_quad(monkeypatch):
    fake = types.SimpleNamespace(
        is_static_camera=lambda p, **k: True,
        track_video=lambda p, quad, **k: ({0: (1.0, 2.0)},
                                          {"frames": 1, "candidates": 1,
                                           "dropped": 0, "covered": 1}))
    monkeypatch.setitem(__import__("sys").modules,
                        "badminton_analysis.shuttle_track.classical", fake)
    s = _Sys()
    s._run_shuttle_pretrack()
    assert s._shuttle_scale is not None
    assert s._shuttle_scale.px_per_m(260.0) > s._shuttle_scale.px_per_m(120.0)


def test_no_court_quad_skips_classical_without_error(monkeypatch):
    fake = types.SimpleNamespace(is_static_camera=lambda p, **k: True,
                                 track_video=lambda *a, **k: pytest.fail("must not run"))
    monkeypatch.setitem(__import__("sys").modules,
                        "badminton_analysis.shuttle_track.classical", fake)
    s = _Sys()
    s.court_corners = None
    s._run_shuttle_pretrack()
    assert s._shuttle_source == "yolo"


# --- the gate keywords must thread through the real call chain ---

def test_hit_events_defaults_are_unchanged():
    from badminton_analysis.stroke_recog.hits import hit_events

    track = [{"frame": i, "shuttle": None,
              "racket_upper": None, "racket_lower": None} for i in range(3)]
    assert hit_events(track) == []


def test_hit_events_forwards_the_gate_keywords(monkeypatch):
    from badminton_analysis.stroke_recog import hits

    seen = {}

    def spy(track, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(hits, "detect_contacts_multi", spy)
    scale = object()
    hits.hit_events([], contact_m=0.3, scale=scale)
    assert seen["contact_m"] == 0.3
    assert seen["scale"] is scale


def test_label_rally_forwards_the_gate_keywords(monkeypatch):
    from badminton_analysis.stroke_recog import recognizer as rec

    seen = {}

    def spy(track, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(rec, "hit_events", spy)
    r = rec.StrokeRecognizer.__new__(rec.StrokeRecognizer)
    scale = object()
    rec.StrokeRecognizer.label_rally(r, [], {}, None, (1920, 1080),
                                     contact_m=0.3, scale=scale)
    assert seen["contact_m"] == 0.3
    assert seen["scale"] is scale
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_classical_router.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: '_Sys' object has no attribute '_shuttle_scale'` is not set by
the current implementation, and `_shuttle_source` never becomes `"classical"`.

- [ ] **Step 3: Write minimal implementation**

In `__init__` (beside `self._shuttle_source = "yolo"` at system.py:171) add:

```python
        self._shuttle_scale = None
```

Insert this block at the **start** of `_run_shuttle_pretrack`, before the existing
`if not (self.tracknet_weights and self.analyze_technique): return` guard, so the classical
route is tried first and TrackNetV3 remains the untouched fallback:

```python
        # Footage-class router. The classical detector models the background with a
        # per-pixel median, so it requires a static camera -- but it needs no
        # weights and is ~25x cheaper than TrackNetV3, which cannot detect the
        # shuttle at all on multi-court hall footage. Broadcast (moving camera)
        # still goes to TrackNetV3. Never fatal.
        if self.analyze_technique and self.court_corners and len(self.court_corners) == 4:
            try:
                from .court.scale import PerspectiveScale
                from .shuttle_track import classical as clmod

                if clmod.is_static_camera(self.video_path):
                    traj0, stats = clmod.track_video(self.video_path, self.court_corners)
                    if stats.get("covered"):
                        self._shuttle_trajectory = {f + 1: pt for f, pt in traj0.items()}
                        self._shuttle_source = "classical"
                        self._shuttle_scale = PerspectiveScale.from_quad(self.court_corners)
                        print(f"Dense shuttle tracking: {stats['covered']}/"
                              f"{stats['frames']} frames via classical detector "
                              f"({stats['candidates']} candidates, "
                              f"{stats['dropped']} dropped by band budget)")
                        return
                    print("Classical shuttle detector found nothing; trying TrackNetV3.")
            except Exception as e:  # never fatal
                print(f"Classical shuttle detector unavailable ({e}); "
                      f"trying TrackNetV3.")
                self._shuttle_trajectory = None
                self._shuttle_source = "yolo"
```

Next, thread the gate keywords through the two intermediate functions. In
`badminton_analysis/stroke_recog/hits.py`, change the signature and the single call:

```python
def hit_events(track, contact_m=None, scale=None):
    """Derive ``[{"frame": int, "hitter": "lower"|"upper"}, ...]`` from a
    both-player contact track (see module docstring), sorted by frame
    ascending (sorted explicitly rather than relying on
    ``detect_contacts_multi`` / the input track already being in frame
    order).

    ``contact_m``/``scale`` opt into the perspective-correct contact radius. Both
    default to ``None``, which leaves the fixed-pixel behaviour untouched.
    """
    contacts = detect_contacts_multi(track, contact_m=contact_m, scale=scale)
    events = [{"frame": c["contact_frame"], "hitter": c["hitter"]} for c in contacts]
    return sorted(events, key=lambda e: e["frame"])
```

In `badminton_analysis/stroke_recog/recognizer.py`, add the same two keywords to `label_rally`
and forward them at the `hit_events` call (line 70):

```python
    def label_rally(self, track, frame_lookup, court_corners, video_wh,
                    contact_m=None, scale=None):
```

```python
        for hit in hit_events(track, contact_m=contact_m, scale=scale):
```

Finally, in `_run_stroke_recognition`, pass them at the `label_rally` call site, which leaves
behaviour identical whenever `_shuttle_scale` is `None`:

```python
            from .stroke.events import CONTACT_M
            gate_kw = ({"contact_m": CONTACT_M, "scale": self._shuttle_scale}
                       if self._shuttle_scale is not None else {})
```

and expand `**gate_kw` into the existing `recognizer.label_rally(...)` call.

- [ ] **Step 4: Run test to verify it passes, and that the existing wiring still holds**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_classical_router.py tests/test_tracknet_wiring.py tests/test_tracknet_integration.py tests/test_bst_integration.py tests/test_bst_hits.py tests/test_bst_recognizer.py -q -p no:cacheprovider`
Expected: PASS — 8 new tests plus every existing TrackNet, BST, hits and recognizer test
unchanged. If any pre-existing test fails, the gate has been switched on by default somewhere;
find it and restore the `None` default rather than editing the fixture's expectations.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/system.py badminton_analysis/stroke_recog/hits.py \
        badminton_analysis/stroke_recog/recognizer.py tests/test_classical_router.py
git commit -m "feat(system): route static-camera footage to the classical shuttle detector"
```

---

### Task 7: Evaluation harness and the decision-gate measurement

§0.23 left three figures untrustworthy: coverage was inflated by construction, the candidate cap
bound on every frame, and precision rested on one trajectory that shared preprocessing with the
detector. This task builds the harness that produces defensible numbers and takes the
measurement the owner's decision depends on.

**Files:**
- Create: `scripts/eval_shuttle_track.py`
- Create: `outputs/b11-shuttle-eval/SHUTTLE_LABELS.md` (generated; `outputs/` is gitignored)
- Test: `tests/test_eval_shuttle_track.py`

**Interfaces:**
- Consumes: `track_video` (Task 5), `PerspectiveScale` (Task 1), `CONTACT_M` (Task 2).
- Produces: `parse_shuttle_labels(path) -> list[dict]`, `score_positions(traj, labels, tol_px,
  fps) -> dict`, `make_label_sheet(rallies, every_n, out_path, fps) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_shuttle_track.py
"""Scoring and label parsing for the shuttle-position evaluation harness."""
import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "eval_shuttle_track",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "eval_shuttle_track.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)

SHEET = """# labels

| time_sec | x | y | notes |
|---|---|---|---|
| 68.485 | 2493 | 758 |  |
| 68.501 | 2397 | 774 | clear |
| 68.900 |  |  | not visible |
"""


def test_parses_only_rows_with_coordinates(tmp_path):
    p = tmp_path / "s.md"
    p.write_text(SHEET, encoding="utf-8")
    rows = mod.parse_shuttle_labels(p)
    assert len(rows) == 2
    assert rows[0] == {"time_sec": 68.485, "x": 2493.0, "y": 758.0}


def test_scores_hits_within_tolerance():
    labels = [{"time_sec": 1.0, "x": 100.0, "y": 100.0},
              {"time_sec": 2.0, "x": 200.0, "y": 200.0}]
    traj = {60: (105.0, 100.0), 120: (400.0, 400.0)}
    r = mod.score_positions(traj, labels, tol_px=25.0, fps=60.0)
    assert r["labelled"] == 2
    assert r["recalled"] == 1
    assert r["recall"] == pytest.approx(0.5)


def test_missing_frame_counts_as_a_miss_not_an_error():
    labels = [{"time_sec": 5.0, "x": 10.0, "y": 10.0}]
    r = mod.score_positions({}, labels, tol_px=25.0, fps=60.0)
    assert r["recalled"] == 0
    assert r["recall"] == 0.0


def test_false_positive_rate_counts_unlabelled_detections():
    labels = [{"time_sec": 1.0, "x": 100.0, "y": 100.0}]
    traj = {60: (100.0, 100.0), 61: (900.0, 900.0), 62: (950.0, 950.0)}
    r = mod.score_positions(traj, labels, tol_px=25.0, fps=60.0)
    assert r["detections"] == 3
    assert r["unmatched_detections"] == 2


def test_label_sheet_lists_one_row_per_sampled_frame(tmp_path):
    out = tmp_path / "sheet.md"
    n = mod.make_label_sheet([(66.0, 67.0)], every_n=10, out_path=out, fps=60.0)
    text = out.read_text(encoding="utf-8")
    assert n == 6
    assert text.count("| 66.") >= 5
    assert "x" in text and "y" in text


def test_empty_labels_are_reported_not_divided_by_zero():
    r = mod.score_positions({0: (1.0, 1.0)}, [], tol_px=25.0, fps=60.0)
    assert r["labelled"] == 0
    assert r["recall"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_eval_shuttle_track.py -q -p no:cacheprovider`
Expected: FAIL with `FileNotFoundError` for `scripts/eval_shuttle_track.py`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/eval_shuttle_track.py
"""Evaluate the classical shuttle detector against hand-labelled shuttle positions.

Section 0.23 recorded three reasons its figures could not be trusted: coverage was
inflated by construction (min_len made every point sit inside a >=3 run), the
candidate cap bound on 100% of frames, and precision rested on a single
trajectory that shared preprocessing with the detector. This harness fixes the
third by scoring against independently clicked positions, and reports recall,
unmatched detections and the band-budget drop rate side by side so none of them
can be quoted without the others.

Usage:
  python scripts/eval_shuttle_track.py --make-sheet     # emit a labelling sheet
  python scripts/eval_shuttle_track.py --score          # score after labelling
"""
import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LABEL_DIR = os.path.join(ROOT, "outputs", "b11-shuttle-eval")
SHEET_PATH = os.path.join(LABEL_DIR, "SHUTTLE_LABELS.md")


def parse_shuttle_labels(path):
    """Read a labelling sheet, keeping only rows that carry both coordinates."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s.startswith("|") or set(s) <= set("|- "):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not cells or cells[0].lower().startswith("time"):
                continue
            try:
                t = float(cells[0])
                x, y = float(cells[1]), float(cells[2])
            except (ValueError, IndexError):
                continue
            rows.append({"time_sec": t, "x": x, "y": y})
    return rows


def score_positions(traj, labels, tol_px, fps):
    """Recall against clicked positions, plus unmatched-detection count."""
    matched = 0
    matched_frames = set()
    for lab in labels:
        f = int(round(lab["time_sec"] * fps))
        pt = traj.get(f)
        if pt is None:
            continue
        if math.hypot(pt[0] - lab["x"], pt[1] - lab["y"]) <= tol_px:
            matched += 1
            matched_frames.add(f)
    detections = sum(1 for v in traj.values() if v is not None)
    return {"labelled": len(labels), "recalled": matched,
            "recall": (matched / len(labels)) if labels else None,
            "detections": detections,
            "unmatched_detections": detections - len(matched_frames)}


def make_label_sheet(rallies, every_n, out_path, fps):
    """Write a sheet with one row per sampled frame. Returns the row count."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    lines = [
        "# Shuttle-position ground truth",
        "",
        "For each row, scrub to `time_sec` and click the shuttle. Enter its pixel",
        "coordinates in the ORIGINAL video resolution. If the shuttle is not visible,",
        "leave x and y blank and note why -- a blank row is excluded from scoring",
        "rather than counted as a miss.",
        "",
        "| time_sec | x | y | notes |",
        "|---|---|---|---|",
    ]
    n = 0
    for (t0, t1) in rallies:
        f0, f1 = int(round(t0 * fps)), int(round(t1 * fps))
        for f in range(f0, f1, every_n):
            lines.append(f"| {f / fps:.3f} |  |  |  |")
            n += 1
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return n


def _rallies_from(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s.startswith("|") or set(s) <= set("|- "):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not cells or cells[0].lower().startswith("start"):
                continue
            try:
                out.append((float(cells[0]), float(cells[1])))
            except (ValueError, IndexError):
                continue
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=os.path.join(
        ROOT, "videos", "Dji 20260718111111 0010 D.mp4"))
    ap.add_argument("--rally-labels", default=os.path.join(
        ROOT, "outputs", "b11-labelling", "LABELS.md"))
    ap.add_argument("--make-sheet", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--every-n", type=int, default=10)
    ap.add_argument("--tol-px", type=float, default=25.0)
    ap.add_argument("--fps", type=float, default=59.94)
    args = ap.parse_args(argv)

    rallies = _rallies_from(args.rally_labels)
    if args.make_sheet:
        n = make_label_sheet(rallies, args.every_n, SHEET_PATH, args.fps)
        print(f"wrote {SHEET_PATH} with {n} rows to label "
              f"(every {args.every_n}th frame across {len(rallies)} rallies)")
        return 0

    if args.score:
        from badminton_analysis.shuttle_track.classical import track_video
        labels = parse_shuttle_labels(SHEET_PATH)
        # Court quad must match the run that produced detections.jsonl.
        quad_path = os.path.join(LABEL_DIR, "court_quad.json")
        with open(quad_path, encoding="utf-8") as fh:
            quad = [tuple(p) for p in json.load(fh)]
        traj, stats = track_video(args.video, quad)
        res = score_positions(traj, labels, args.tol_px, args.fps)
        print(json.dumps({"scoring": res, "detector": stats}, indent=1))
        drop_rate = stats["dropped"] / max(stats["candidates"] + stats["dropped"], 1)
        print(f"band-budget drop rate: {100 * drop_rate:.1f}% "
              f"(section 0.23 required this to stop binding)")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_eval_shuttle_track.py -q -p no:cacheprovider`
Expected: PASS (6 passed)

- [ ] **Step 5: Generate the labelling sheet and hand it to the owner**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B scripts/eval_shuttle_track.py --make-sheet`
Expected: a sheet at `outputs/b11-shuttle-eval/SHUTTLE_LABELS.md` with roughly 470 rows
(every 10th frame across 4,735 rally frames). **Stop here and ask the owner to label it** —
scoring cannot run without independent ground truth, and inventing labels would reproduce
exactly the circularity §0.23 warned about. Also write the court quad used by the recorded run
to `outputs/b11-shuttle-eval/court_quad.json`, since scoring must use the same geometry.

- [ ] **Step 6: Run the full suite, then commit**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ai_handoff.py`
Expected: PASS. (`test_ai_handoff.py` is excluded because it is known flaky in long batch runs
and unrelated to this work; run it separately if the continuity layer was touched.)

```bash
git add scripts/eval_shuttle_track.py tests/test_eval_shuttle_track.py
git commit -m "feat(eval): shuttle-position evaluation harness and labelling sheet"
```

- [ ] **Step 7: Record the decision-gate measurement**

Once labels exist, run `--score` and append a section to
`docs/superpowers/specs/2026-07-30-rally-play-detection-b11-design.md` reporting: recall against
the clicked positions, unmatched-detection count, band-budget drop rate, contacts fired per
rally, and how many of the 11 rallies yield at least two contacts. **The gate is: enough
contacts to identify each rally's first and last contact** — at the time of costing, 1 of 11
rallies produced none. State plainly whether the gate is met; if it is not, the §0.23 fallback
to option A applies with the tracker, gating, router and integration from this plan already
built.

---

## Notes for the implementer

- **Do not tune thresholds against the evaluation set until it is labelled.** Every constant in
  Task 4 (`diff_thresh`, `area_lo/hi`, `min_bright`, `band_budget`) came from a costing spike
  whose recall was distorted by a binding cap. Fit them once, on labelled data, and record what
  changed.
- **`max_speed` has a performance cliff.** Task 3's `build_chains` uses it as a KD-tree query
  radius. If it approaches the frame width, every candidate becomes a neighbour of every other
  and the search degenerates to O(n²) per frame — this made an earlier spike fail to finish two
  rallies in nine minutes.
- **The area∝speed score is the load-bearing idea.** If precision disappoints, examine that
  correlation before adding new heuristics; it is the one signal measured to separate a real
  shuttle (13.7 vs 13.9) from clutter.
- **Contact counts derived from `detections.jsonl` use wrist midpoints as a racket stand-in**
  (§0.20). Any contact figure from a harness rather than the production path is indicative, not
  exact — say so when reporting it.
