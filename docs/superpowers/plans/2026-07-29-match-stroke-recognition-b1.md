# Implementation Plan: B1 — Both-Player Capture + Hitter Selection

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Scope:** the first implementation step of Sub-project B (full-match stroke recognition),
per `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md` §3 item B1 and
§7's recommended sequencing. B1 only: both-player capture, contact detection by either
player, and hitter-by-proximity attribution so BST's person-0/person-1 slots are filled
correctly. It does NOT include segment-scoped dense tracking (B2-B4), the background-job
redesign (B10), rally/play-detection fixes (B11), or fps/resolution normalization (B7) —
those are separate, not-yet-planned pieces of Sub-project B.

---

## Key finding that changes the Validation section (read before the tasks)

The 750-frame Axelsen validation clip referenced by both design docs (`rally_seg.mp4`, the one that produced "8 contacts, `{net:4, clear:3, uncertain:1}`") **no longer exists on disk, and its extraction parameters were never recorded anywhere in the repo.**

Verified directly:
- `videos/` contains only the full 64,085-frame / 60fps Axelsen match (`YTDown.com_..._001_1080p.mp4`, 723MB) — no short segment.
- `outputs/Ytdown.Com Youtube Media  001 1080P/` (note: a *different*, no-metadata output directory from the three known ones in the design doc) contains **only** `auto_court_preview.png` — no video, no `metadata.json`, no `strokes.json`. This is almost certainly the leftover output directory from the original T9/TrackNetV3 run, with everything except the court-annotation artifact since cleaned up.
- `grep` across `.superpowers/sdd/progress.md` and both design specs found the narrative ("25 s segment @30 fps", "750-frame Axelsen segment") but **no `ffmpeg`/extraction command, no start timestamp**.
- All required weights (`bst-shuttleset.pt`, `tracknet.pt`, `inpaintnet.pt`, `yolo11n-racket.pt`, `yolo11s-ball.pt`) are present, so re-running validation is otherwise feasible.

This is a genuine decision point, not something to paper over — see **Decision Point C** and the **Validation** section below, which proposes reconstructing a comparable (not identical) segment and says so honestly in the recorded outcome.

---

## Architecture

B1 adds an **additive, opt-in-by-construction "both players" data path** alongside the existing single-tracked-player path, rather than modifying the existing path in place. This is the only way to satisfy the non-regression constraint (Open Question F: `TechniqueAnalysisRunner` keeps consuming exactly what it consumes today) while giving contacts/BST what they need.

Concretely:
- `PlayerTracker.players` already tracks **both** `"lower"` and `"upper"` centroids per frame (confirmed reading `tracking/player.py` post-fe31415 — this was never single-player; only the half-classification was buggy, and that's already fixed). B1 does not touch `player.py`.
- `_capture_analysis_frame` keeps its existing single-player block **completely untouched** (byte-for-byte), and appends a new block that additionally captures both sides' pose/centroid/racket point into a new `_analysis_frames[frame]["players"] = {"lower": {...}, "upper": {...}}` sub-record, and a new parallel `_analysis_track_both` list (mirroring `_analysis_track`'s shape/cadence exactly, but carrying `racket_lower`/`racket_upper` instead of one `racket_head`).
- A new `stroke.events.detect_contacts_multi` (new function, `detect_contacts` untouched) finds contacts using **either** player's racket point and attributes `hitter` by shuttle proximity **inside contact detection itself** — not via a `player_side` lookup, which was the actual bug (that field is ~always `"lower"`).
- `stroke_recog.hits.hit_events` is rewritten to consume `detect_contacts_multi`'s already-correct `hitter`, dropping its former (buggy) dependency on `frame_lookup`'s `player_side`.
- `stroke_recog.inputs.build_inputs` gains an **optional** `hitter` parameter: when given, person-0 = hitter's pose/position (read from `rec["players"][hitter]`), person-1 = opponent's (`rec["players"][opponent]`); when omitted (default), behavior is **byte-identical to today** (person-0 = the record's existing top-level `keypoints`/`centroid`, person-1 = zero-filled) — every existing test in `test_bst_inputs.py` keeps passing unmodified.
- `stroke_recog.recognizer.label_rally` threads the per-hit `hitter` through to `build_inputs`.
- `system.py._run_stroke_recognition` switches its one call from `self._analysis_track` to `self._analysis_track_both`. `_run_technique_analysis`/`TechniqueAnalysisRunner` keep using `self._analysis_track` (untouched) and `detect_contacts` (untouched) — this is the load-bearing non-regression seam and gets its own explicit test.
- Racket detection for two simultaneous players needs a new `RacketDetector.detect_racket_heads` (all in-ROI boxes, not just the best one) so `system.py` can nearest-match a detection to each side's own centroid independently, falling back to the existing kinematic `infer_racket_head` per side when no detection is close enough.

---

## Decision points (flagging explicitly, not silently picking)

**A — Racket-to-player assignment heuristic.** `RacketDetector.detect_racket_head` returns only the single highest-confidence box in the ROI; there is no existing multi-object-per-player racket assignment anywhere in this codebase. B1 adds `detect_racket_heads` (all in-ROI boxes) and, in `system.py`, nearest-matches each detection to each side's own tracked centroid independently, gated by a new constant `RACKET_TO_PLAYER_MAX_PX = 300.0` (chosen the same way `contact_px=80.0` was — a deliberately dumb, untuned v1 constant; project 2 measures accuracy, per the BST/TrackNetV3 spec's own convention). Two players simultaneously very close to the net (rare) could both nearest-match the same box; this is accepted as a v1 heuristic risk, not fixed here. **Alternative considered and rejected for v1:** always use kinematic inference (`infer_racket_head` from each side's own elbow/wrist) instead of real detections for the second player — rejected because it throws away real racket-model signal that's already available and cheap to nearest-match.

**B — Hitter tie-break.** If both players' racket points are simultaneously within `contact_px` of the shuttle at the same frame (adjacent-at-net scenario), `detect_contacts_multi` breaks the tie toward `"lower"` (arbitrary, documented in the docstring). Not expected to matter often; flagged rather than hidden.

**C — The lost validation clip (see above).** B1's validation step cannot literally re-run the recorded baseline. Proposal: reconstruct a comparable ~25s/750-frame segment from the full match via `ffmpeg`, anchored on the earliest rally in the existing (buggy but still informative) `rally_segments.json` (rally 1: frames 9518–9689, ≈158.6s–161.5s), re-encoded to 30fps to match the documented original methodology. **This is explicitly not the same clip** — the validation task records contact count and hitter distribution as a fresh, honestly-labeled result, not a claim of reproducing `{net:4, clear:3, uncertain:1}` exactly. If the owner still has the original clip or remembers its timestamp, that should be used instead — flagging this for a go/no-go before the validation task runs.

**D — Scope boundary on fps.** Both real match videos are 60fps; B7 (fps/resolution normalization) is explicitly out of scope for B1 per the brief. The reconstructed validation clip is re-encoded to 30fps specifically so this plan's `detect_contacts`/`detect_contacts_multi`/BST-window constants (tuned implicitly at 30fps) stay valid for validation purposes. A 60fps full-match run is **not** what B1's done-means claims to handle — that's B2-B4/B7/B10/B11 territory.

---

## Global Constraints

- Python; match existing code style (module docstrings, minimal type annotations, ASCII-only source).
- Non-regression is load-bearing: `TechniqueAnalysisRunner`'s inputs (`self._analysis_track`, `detect_contacts`, the top-level single-player fields in `_analysis_frames`) must not change in any line of logic, only in what's additively appended around them.
- Graceful degradation / never-fatal: every new capture path degrades to `None`/empty exactly like the existing single-player path does (missing racket detector, missing pose, missing centroid → `None`, never an exception that reaches `process_video`).
- No new pip dependencies.
- Tests run on Windows via the project venv: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest <path> -q -p no:cacheprovider`.
- Commit after each task with a `Co-Authored-By:` trailer reflecting the implementer's own actual identity (not hardcoded to a specific model name here).
- B1 only: no segment-scoped dense tracking (B2-B4), no background job (B10), no rally/play-detection fixes (B11), no fps/resolution normalization (B7). If implementation reveals B1 cannot be done cleanly without one of these, that must be raised as a plan risk, not silently absorbed.

---

### Task 1: `RacketDetector.detect_racket_heads` — all in-ROI boxes, not just the best one

**Files:**
- Modify: `badminton_analysis/detection/racket.py`
- Test: `tests/test_racket.py` (add; existing tests untouched)

**Interfaces:**
- Produces: `RacketDetector.detect_racket_heads(frame, roi_corners=None) -> list[(x, y)]` — all in-ROI box centers, confidence-descending; `[]` when the model is absent or nothing is in the ROI.
- `detect_racket_head` (existing, single-best) is refactored to share the new `_boxes_in_roi` helper but keeps its exact existing behavior/signature.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_racket.py

def test_detect_racket_heads_returns_all_boxes_confidence_desc():
    boxes = _FakeBoxes(xywh=[[50, 60, 10, 10], [20, 20, 10, 10]], conf=[0.4, 0.9])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    heads = det.detect_racket_heads(frame)
    assert heads == [(20, 20), (50, 60)]  # confidence-descending: 0.9 then 0.4


def test_detect_racket_heads_filters_outside_roi():
    boxes = _FakeBoxes(xywh=[[50, 60, 10, 10], [500, 500, 10, 10]], conf=[0.9, 0.4])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((600, 600, 3), dtype=np.uint8)
    heads = det.detect_racket_heads(frame, roi_corners=[(0, 0), (100, 100)])
    assert heads == [(50, 60)]


def test_detect_racket_heads_empty_without_model():
    det = RacketDetector(model=None)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert det.detect_racket_heads(frame) == []


def test_detect_racket_head_unchanged_after_refactor():
    """Non-regression: detect_racket_head's own behavior is untouched by the
    _boxes_in_roi refactor -- same fixture as test_picks_highest_confidence_center."""
    boxes = _FakeBoxes(xywh=[[50, 60, 10, 10], [20, 20, 10, 10]], conf=[0.9, 0.4])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    assert det.detect_racket_head(frame) == (50, 60)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_racket.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: 'RacketDetector' object has no attribute 'detect_racket_heads'`.

- [ ] **Step 3: Write minimal implementation**

Replace `detect_racket_head` in `badminton_analysis/detection/racket.py` with:

```python
    def _boxes_in_roi(self, frame, roi_corners=None):
        """All in-ROI (point, confidence) box centers this frame, unsorted, []
        when the model is absent or nothing was detected. Shared by
        detect_racket_head (single best) and detect_racket_heads (all of
        them, for assigning different detections to different players)."""
        if self.model is None:
            return []
        try:
            result = self.model(frame, conf=self.conf, device=self.device, verbose=False)[0]
        except TypeError:
            result = self.model(frame, conf=self.conf, verbose=False)[0]

        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xywh.shape[0] < 1:
            return []

        xywh = boxes.xywh.detach().cpu().numpy()
        conf = boxes.conf.detach().cpu().numpy() if boxes.conf is not None else np.ones(len(xywh))

        out = []
        for box, c in zip(xywh, conf):
            cx, cy = int(box[0]), int(box[1])
            if not self._point_in_roi((cx, cy), roi_corners):
                continue
            out.append(((cx, cy), float(c)))
        return out

    def detect_racket_head(self, frame, roi_corners=None):
        boxes = self._boxes_in_roi(frame, roi_corners)
        if not boxes:
            return None
        return max(boxes, key=lambda b: b[1])[0]

    def detect_racket_heads(self, frame, roi_corners=None):
        """All in-ROI racket-box centers this frame, confidence-descending.

        Unlike detect_racket_head (single highest-confidence box), this lets a
        caller nearest-match different detections to different tracked
        players (badminton_analysis.system._capture_analysis_frame's
        both-player capture). Returns [] when the model is absent or no boxes
        fall in the ROI.
        """
        boxes = self._boxes_in_roi(frame, roi_corners)
        boxes.sort(key=lambda b: b[1], reverse=True)
        return [pt for pt, _c in boxes]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_racket.py -q -p no:cacheprovider`
Expected: PASS (all existing + new tests).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/detection/racket.py tests/test_racket.py
git commit -m "feat(racket): add detect_racket_heads for all in-ROI boxes"
```

---

### Task 2: `stroke.events.detect_contacts_multi` — both-player contact detection + hitter-by-proximity

**Files:**
- Modify: `badminton_analysis/stroke/events.py` (add function; `detect_contacts`/`StrokeEvent` untouched)
- Test: `tests/test_stroke_events.py` (add)

**Interfaces:**
- Produces: `detect_contacts_multi(track, contact_px=80.0, lookahead=3, dir_change_deg=45.0, window_pre=20, window_post=15, min_gap=15) -> list[{"contact_frame", "window_start", "window_end", "hitter"}]`. `track` records are `{"frame", "racket_lower", "racket_upper", "shuttle"}`. `hitter` is `"lower"` or `"upper"` — whichever racket point is nearer the shuttle at the contact frame (ties toward `"lower"`, Decision Point B).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_stroke_events.py
from badminton_analysis.stroke.events import detect_contacts_multi


def _shuttle_pos_multi(f, first, second):
    if f <= first:
        return (200 - f * 2, 200 - f * 2)
    p1 = 200 - first * 2
    if f <= second:
        d = f - first
        return (p1 + d * 2, p1 + d * 2)
    p2 = p1 + (second - first) * 2
    d2 = f - second
    return (p2 - d2 * 2, p2 - d2 * 2)


def _track_both(first=30, second=90, n=120, first_side="lower", second_side="upper"):
    """Two contacts, one per side, far enough apart that min_gap doesn't merge them."""
    track = []
    for f in range(n):
        shuttle = _shuttle_pos_multi(f, first, second)
        racket_lower = shuttle if (f == first and first_side == "lower") or (f == second and second_side == "lower") else (5000, 5000)
        racket_upper = shuttle if (f == first and first_side == "upper") or (f == second and second_side == "upper") else (5000, 5000)
        track.append({"frame": f, "racket_lower": racket_lower, "racket_upper": racket_upper, "shuttle": shuttle})
    return track


def test_detect_contacts_multi_attributes_hitter_per_side():
    track = _track_both(30, 90, first_side="lower", second_side="upper")
    contacts = detect_contacts_multi(track)
    assert len(contacts) == 2
    assert contacts[0]["contact_frame"] == 30
    assert contacts[0]["hitter"] == "lower"
    assert contacts[1]["contact_frame"] == 90
    assert contacts[1]["hitter"] == "upper"


def test_detect_contacts_multi_upper_only_still_fires():
    """Regression target: the pre-B1 bug meant a hit by the far/upper player
    could never register at all (racket_head belonged to the tracked/lower
    player). This must now fire."""
    track = _track_both(30, 90, first_side="upper", second_side="upper")
    contacts = detect_contacts_multi(track)
    assert len(contacts) == 2
    assert all(c["hitter"] == "upper" for c in contacts)


def test_detect_contacts_multi_ignores_far_rackets_both_sides():
    track = _track_both(30, 90)
    for fr in track:
        fr["racket_lower"] = (5000, 5000)
        fr["racket_upper"] = (5000, 5000)
    assert detect_contacts_multi(track) == []


def test_detect_contacts_multi_tie_breaks_toward_lower():
    track = _track_both(30, 90, first_side="lower", second_side="upper")
    # Make both rackets coincide with the shuttle at frame 30 (a tie).
    track[30]["racket_upper"] = track[30]["shuttle"]
    contacts = detect_contacts_multi(track)
    assert contacts[0]["contact_frame"] == 30
    assert contacts[0]["hitter"] == "lower"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_stroke_events.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'detect_contacts_multi'`.

- [ ] **Step 3: Write minimal implementation**

Append to `badminton_analysis/stroke/events.py`:

```python
def detect_contacts_multi(track, contact_px=80.0, lookahead=3, dir_change_deg=45.0,
                          window_pre=20, window_post=15, min_gap=15):
    """Find racket-shuttle impacts by EITHER player.

    Generalizes detect_contacts (which stays single-racket and is still the
    sole detector wired to TechniqueAnalysisRunner's existing, unchanged
    path) to a per-frame record carrying BOTH players' racket points -- see
    badminton_analysis.system.BadmintonAnalysisSystem._analysis_track_both:
    {"frame", "racket_lower", "racket_upper", "shuttle"}.

    A contact fires when EITHER player's racket point is within contact_px of
    the shuttle AND the shuttle changes direction by >= dir_change_deg soon
    after (the same physical test as detect_contacts, evaluated against two
    racket points instead of one). "hitter" is the side whose racket point is
    nearer the shuttle at the contact frame (this is the fix for the known
    hitter/opponent bug: attribution comes from proximity here, never from a
    frame's cached "currently tracked" player_side). Ties are broken toward
    "lower" (arbitrary; noted, not tuned).

    Returns list of {contact_frame, window_start, window_end, hitter}.
    """
    contacts = []
    last_contact_frame = None
    for i, rec in enumerate(track):
        shuttle = rec.get("shuttle")
        if shuttle is None:
            continue
        best_side, best_dist = None, None
        for side in ("lower", "upper"):
            racket = rec.get("racket_" + side)
            if racket is None:
                continue
            d = _dist(racket, shuttle)
            if d < contact_px and (best_dist is None or d < best_dist):
                best_dist, best_side = d, side
        if best_side is None:
            continue
        change = _shuttle_dir_change(track, i, lookahead)
        if change is None or change < dir_change_deg:
            continue
        frame = rec["frame"]
        if last_contact_frame is not None and (frame - last_contact_frame) < min_gap:
            continue
        contacts.append({
            "contact_frame": frame,
            "window_start": max(0, frame - window_pre),
            "window_end": frame + window_post,
            "hitter": best_side,
        })
        last_contact_frame = frame
    return contacts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_stroke_events.py -q -p no:cacheprovider`
Expected: PASS (all existing `detect_contacts` tests + new `detect_contacts_multi` tests).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/events.py tests/test_stroke_events.py
git commit -m "feat(stroke): add detect_contacts_multi for both-player contact attribution"
```

---

### Task 3: `stroke_recog.hits.hit_events` — attribute hitter from contact detection, not `player_side`

**Files:**
- Modify: `badminton_analysis/stroke_recog/hits.py`
- Test: `tests/test_bst_hits.py` (full rewrite — the old tests exercised the pre-fix contract this task removes; see rationale below)

**Interfaces:**
- Produces: `hit_events(track) -> [{"frame": int, "hitter": "lower"|"upper"}, ...]`, sorted by frame. `track` is the both-player track (`{"frame", "racket_lower", "racket_upper", "shuttle"}`). The `frame_lookup` parameter is **removed** — `hitter` no longer needs a pose-record lookup at all, since `detect_contacts_multi` already knows which racket triggered the contact.

**Why the old tests must go, not just be extended:** `tests/test_bst_hits.py` explicitly asserted `hitter` came from `frame_lookup(frame)["player_side"]` — that field is ~always `"lower"` in a real match (the documented bug this task exists to fix). Keeping those assertions would mean keeping the bug.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bst_hits.py (full replacement)
"""Tests for hit/hitter extraction from the both-player contact track.

See badminton_analysis/stroke_recog/hits.py and
badminton_analysis/stroke/events.py::detect_contacts_multi. Pre-B1, hitter
came from a frame_lookup's cached "player_side" (~always "lower" in a real
match -- the known bug). Post-B1, hitter comes directly from
detect_contacts_multi's own shuttle-proximity attribution, so it no longer
depends on frame_lookup / pose data being available at all.
"""

from badminton_analysis.stroke_recog import hits


def _shuttle_pos(f, first, second):
    if f <= first:
        return (200 - f * 2, 200 - f * 2)
    p1 = 200 - first * 2
    if f <= second:
        d = f - first
        return (p1 + d * 2, p1 + d * 2)
    p2 = p1 + (second - first) * 2
    d2 = f - second
    return (p2 - d2 * 2, p2 - d2 * 2)


def _track_with_two_contacts(first=30, second=90, n=120, first_side="lower", second_side="upper"):
    track = []
    for f in range(n):
        shuttle = _shuttle_pos(f, first, second)
        racket_lower = shuttle if (f == first and first_side == "lower") or (f == second and second_side == "lower") else (5000, 5000)
        racket_upper = shuttle if (f == first and first_side == "upper") or (f == second and second_side == "upper") else (5000, 5000)
        track.append({"frame": f, "racket_lower": racket_lower, "racket_upper": racket_upper, "shuttle": shuttle})
    return track


def test_hit_events_attributes_hitter_from_contact_track_not_lookup():
    track = _track_with_two_contacts(30, 90, first_side="lower", second_side="upper")
    events = hits.hit_events(track)
    assert len(events) == 2
    assert [e["frame"] for e in events] == sorted(e["frame"] for e in events)
    assert events[0]["frame"] == 30 and events[0]["hitter"] == "lower"
    assert events[1]["frame"] == 90 and events[1]["hitter"] == "upper"


def test_hit_events_upper_only_hits_now_register():
    """The pre-B1 regression target: an upper-court-only rally must yield
    hitter == "upper", not be silently dropped or misattributed to "lower"."""
    track = _track_with_two_contacts(30, 90, first_side="upper", second_side="upper")
    events = hits.hit_events(track)
    assert len(events) == 2
    assert all(e["hitter"] == "upper" for e in events)


def test_hit_events_empty_track_returns_empty_list():
    assert hits.hit_events([]) == []


def test_hit_events_sorted_by_frame_even_if_contacts_unordered(monkeypatch):
    monkeypatch.setattr(
        hits, "detect_contacts_multi",
        lambda track: [
            {"contact_frame": 90, "hitter": "upper"},
            {"contact_frame": 30, "hitter": "lower"},
            {"contact_frame": 60, "hitter": "lower"},
        ],
    )
    events = hits.hit_events([])
    assert [e["frame"] for e in events] == [30, 60, 90]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_hits.py -q -p no:cacheprovider`
Expected: FAIL (`ImportError`/`AttributeError` — `hits.detect_contacts_multi` doesn't exist yet in `hits.py`'s namespace, and `hit_events`'s current signature/behavior differs).

- [ ] **Step 3: Write minimal implementation**

Replace `badminton_analysis/stroke_recog/hits.py` in full:

```python
"""Hit/hitter extraction from the match pipeline's both-player contact track.

Pure function: turns badminton_analysis.system.BadmintonAnalysisSystem's
``_analysis_track_both`` (list of ``{frame, racket_lower, racket_upper,
shuttle}`` records) into the ordered list of hit events the recognizer
iterates over. Both the hit *frames* and the *hitter* attribution come
straight from ``stroke.events.detect_contacts_multi`` -- this module only
sorts and reshapes its output.

Pre-B1, hitter was read from a frame_lookup's cached "player_side", which is
~always "lower" in a real two-player match (the documented hitter/opponent
bug). Post-B1, detect_contacts_multi already knows which racket triggered
each contact, so hitter is correct by construction and no longer depends on
frame_lookup / pose availability at all.
"""

from ..stroke.events import detect_contacts_multi


def hit_events(track):
    """Derive ``[{"frame": int, "hitter": "lower"|"upper"}, ...]`` from a
    both-player contact track (see module docstring), sorted by frame
    ascending (sorted explicitly rather than relying on
    ``detect_contacts_multi`` / the input track already being in frame
    order).
    """
    contacts = detect_contacts_multi(track)
    events = [{"frame": c["contact_frame"], "hitter": c["hitter"]} for c in contacts]
    return sorted(events, key=lambda e: e["frame"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_hits.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke_recog/hits.py tests/test_bst_hits.py
git commit -m "fix(stroke-recog): attribute hitter from contact detection, not cached player_side"
```

---

### Task 4: `stroke_recog.inputs.build_inputs` — hitter-aware both-player tensors (additive, backward-compatible)

**Files:**
- Modify: `badminton_analysis/stroke_recog/inputs.py`
- Test: `tests/test_bst_inputs.py` (add; all existing tests must keep passing unmodified)

**Interfaces:**
- Produces: `build_inputs(contact_frame, frame_lookup, court_corners, video_wh, hitter=None)`. When `hitter` is `"lower"`/`"upper"`: person-0 = `frame_lookup(f)["players"][hitter]`, person-1 = `frame_lookup(f)["players"][opponent]` (opponent = the other side). When `hitter` is `None`/omitted (default): **exactly today's behavior** — person-0 = the record's top-level `keypoints`/`centroid`, person-1 zero-filled.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_bst_inputs.py

def _side_dict(offset=0.0):
    kp = np.array([[600 + 2 * j + offset, 300 + 3 * j + offset] for j in range(17)], dtype=float)
    return {
        "keypoints": kp,
        "centroid": (float(kp[11][0] + kp[12][0]) / 2.0, float(kp[11][1] + kp[12][1]) / 2.0),
    }


def _both_players_record(frame, shuttle=(640.0, 360.0)):
    return {
        "shuttle": shuttle,
        "players": {"lower": _side_dict(offset=0.0), "upper": _side_dict(offset=100.0)},
    }


def test_hitter_lower_fills_person0_from_lower_person1_from_upper():
    records = {f: _both_players_record(f) for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    result = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH, hitter="lower")
    assert result is not None
    contact_index = bi.SEQ_LEN // 2
    # person-0 (hitter=lower) and person-1 (opponent=upper) both carry real,
    # DIFFERENT data -- opponent is no longer permanently zero-filled.
    assert np.any(result["pose"][contact_index, 0] != 0.0)
    assert np.any(result["pose"][contact_index, 1] != 0.0)
    assert not np.allclose(result["pose"][contact_index, 0], result["pose"][contact_index, 1])
    assert np.any(result["positions"][contact_index, 0] != result["positions"][contact_index, 1])


def test_hitter_upper_swaps_person0_and_person1():
    records = {f: _both_players_record(f) for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    result_lower = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH, hitter="lower")
    result_upper = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH, hitter="upper")
    contact_index = bi.SEQ_LEN // 2
    # Swapping hitter swaps which side lands in person-0 vs person-1.
    np.testing.assert_allclose(result_lower["pose"][contact_index, 0], result_upper["pose"][contact_index, 1])
    np.testing.assert_allclose(result_lower["pose"][contact_index, 1], result_upper["pose"][contact_index, 0])


def test_hitter_none_default_is_byte_identical_to_pre_b1_behavior():
    """No hitter given -> exactly today's contract: person-0 from the
    record's top-level keypoints/centroid, person-1 zero-filled."""
    records = {f: _record(f) for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    result = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH)
    assert result is not None
    assert np.all(result["pose"][:, 1] == 0.0)
    assert np.all(result["positions"][:, 1] == 0.0)


def test_hitter_given_but_players_key_missing_degrades_to_zero_fill():
    """Never-fatal: a hitter is requested but some/all frames in the window
    lack the "players" sub-dict (e.g. that frame wasn't densely captured) ->
    those frames zero-fill rather than raising."""
    records = {f: {"shuttle": (640.0, 360.0)} for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    result = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH, hitter="lower")
    # Too few posed frames (none have "players") -> gate returns None, same
    # never-fatal contract as test_all_none_keypoints_returns_none.
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_inputs.py -q -p no:cacheprovider`
Expected: FAIL with `TypeError: build_inputs() got an unexpected keyword argument 'hitter'`.

- [ ] **Step 3: Write minimal implementation**

In `badminton_analysis/stroke_recog/inputs.py`, add a helper and modify `build_inputs`:

```python
def _select_hitter_and_opponent(rec, hitter):
    """(hitter_rec, opponent_rec) sub-dicts to read pose/position from.

    hitter is None/"unknown" -> (rec, None): today's pre-B1 contract
    (person-0 from the record's own top-level keys, person-1 always zero).
    hitter is "lower"/"upper" -> reads rec["players"][hitter] /
    rec["players"][opponent]; missing/absent -> None (zero-fills that slot,
    never raises -- same never-fatal convention as the rest of this module).
    """
    if hitter not in ("lower", "upper"):
        return rec, None
    players = (rec or {}).get("players") or {}
    opponent = "upper" if hitter == "lower" else "lower"
    return players.get(hitter), players.get(opponent)


def build_inputs(contact_frame, frame_lookup, court_corners, video_wh, hitter=None):
    """Assemble one hit's (pose, shuttle, positions) BST input arrays.

    ``hitter`` (new, optional): "lower" or "upper" selects which side's data
    fills person-0 (hitter) vs person-1 (opponent), read from each window
    frame's ``rec["players"][side]`` sub-dict (badminton_analysis.system's
    both-player capture). Omitted/None keeps the original v1 contract:
    person-0 from the record's own top-level "keypoints"/"centroid", person-1
    always zero-filled. See module docstring for the full parameter list;
    only ``hitter`` is new here.
    """
    half = SEQ_LEN // 2
    start = contact_frame - half
    records = [frame_lookup(idx) for idx in range(start, start + SEQ_LEN)]
    sides = [_select_hitter_and_opponent(rec, hitter) for rec in records]

    if sum(1 for hitter_rec, _opp in sides if _is_posed(hitter_rec)) < _MIN_POSED_FRAMES:
        return None

    mapper = CourtMapper(court_corners)

    pose = np.zeros(POSE_SHAPE, dtype=np.float32)
    shuttle = np.zeros(SHUTTLE_SHAPE, dtype=np.float32)
    positions = np.zeros(POS_SHAPE, dtype=np.float32)

    for t, (rec, (hitter_rec, opponent_rec)) in enumerate(zip(records, sides)):
        if hitter_rec is not None and hitter_rec.get("keypoints") is not None:
            pose[t, 0] = _normalize_pose(hitter_rec["keypoints"])
        if opponent_rec is not None and opponent_rec.get("keypoints") is not None:
            pose[t, 1] = _normalize_pose(opponent_rec["keypoints"])

        shuttle[t] = _shuttle_xy(rec, video_wh)

        foot = _foot_point(hitter_rec)
        if foot is not None:
            court_xy = mapper.image_to_court(foot)
            if len(court_xy):
                positions[t, 0, 0] = float(court_xy[0]) / _COURT_W
                positions[t, 0, 1] = float(court_xy[1]) / _COURT_H

        foot_opp = _foot_point(opponent_rec)
        if foot_opp is not None:
            court_xy_opp = mapper.image_to_court(foot_opp)
            if len(court_xy_opp):
                positions[t, 1, 0] = float(court_xy_opp[0]) / _COURT_W
                positions[t, 1, 1] = float(court_xy_opp[1]) / _COURT_H

    return {"pose": pose, "shuttle": shuttle, "positions": positions}
```

(`_is_posed`, `_foot_point`, `_shuttle_xy`, `_normalize_pose` are unchanged — they already operate on a single side-shaped dict with `"keypoints"`/`"centroid"` keys, which both the top-level record and a `players[side]` sub-dict satisfy.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_inputs.py -q -p no:cacheprovider`
Expected: PASS (all existing tests unmodified + new hitter-aware tests).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke_recog/inputs.py tests/test_bst_inputs.py
git commit -m "feat(stroke-recog): fill BST person-0/person-1 from the actual hitter/opponent"
```

---

### Task 5: `stroke_recog.recognizer.label_rally` — wire hitter through to `build_inputs`

**Files:**
- Modify: `badminton_analysis/stroke_recog/recognizer.py`
- Test: `tests/test_bst_recognizer.py` (rewrite fixtures — old ones used the single-racket track shape, incompatible with `hit_events`'s new contract)

**Interfaces:**
- `label_rally(track, frame_lookup, court_corners, video_wh)` — same signature; `track` now expected in the both-player shape (`{"frame", "racket_lower", "racket_upper", "shuttle"}`); internally calls `hit_events(track)` (no `frame_lookup` arg) then `build_inputs(frame, frame_lookup, court_corners, video_wh, hitter=hit["hitter"])`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bst_recognizer.py (full replacement)
"""Tests for rally-level stroke-labeling orchestration.

See badminton_analysis/stroke_recog/recognizer.py. Hermetic: bst_model's
load_bst/predict are monkeypatched; hit_events and build_inputs run for
real against both-player track/frame fixtures (mirrors
tests/test_bst_hits.py and tests/test_bst_inputs.py's post-B1 shapes).
"""

import numpy as np

from badminton_analysis.stroke_recog import recognizer
from badminton_analysis.stroke_recog.classes import CLASS_NAMES, FINE_TO_COARSE
from badminton_analysis.stroke_recog.inputs import SEQ_LEN

VIDEO_WH = (1280, 720)
COURT_CORNERS = [(100, 100), (1180, 100), (1180, 620), (100, 620)]


def _shuttle_pos(f, first, second):
    if f <= first:
        return (200 - f * 2, 200 - f * 2)
    p1 = 200 - first * 2
    if f <= second:
        d = f - first
        return (p1 + d * 2, p1 + d * 2)
    p2 = p1 + (second - first) * 2
    d2 = f - second
    return (p2 - d2 * 2, p2 - d2 * 2)


def _track_with_two_contacts(first=30, second=90, n=120, first_side="lower", second_side="upper"):
    track = []
    for f in range(n):
        shuttle = _shuttle_pos(f, first, second)
        racket_lower = shuttle if (f == first and first_side == "lower") or (f == second and second_side == "lower") else (5000, 5000)
        racket_upper = shuttle if (f == first and first_side == "upper") or (f == second and second_side == "upper") else (5000, 5000)
        track.append({"frame": f, "racket_lower": racket_lower, "racket_upper": racket_upper, "shuttle": shuttle})
    return track


def _side_pose_dict():
    kp = np.array([[600 + 2 * j, 300 + 3 * j] for j in range(17)], dtype=float)
    return {"keypoints": kp, "centroid": (float(kp[11][0] + kp[12][0]) / 2.0, float(kp[11][1] + kp[12][1]) / 2.0)}


def _fully_posed_window(contact_frame, into, shuttle_at=(640.0, 360.0)):
    half = SEQ_LEN // 2
    for f in range(contact_frame - half, contact_frame - half + SEQ_LEN):
        into[f] = {"shuttle": shuttle_at, "players": {"lower": _side_pose_dict(), "upper": _side_pose_dict()}}


def _smash_dominant_logits():
    logits = np.full(len(CLASS_NAMES), -10.0)
    for i, name in enumerate(CLASS_NAMES):
        if FINE_TO_COARSE[name] == "smash":
            logits[i] = 5.0
    return logits


def test_label_rally_two_hits_from_both_sides_smash_dominant_logits(monkeypatch):
    track = _track_with_two_contacts(30, 90, first_side="lower", second_side="upper")
    records = {}
    _fully_posed_window(30, records)
    _fully_posed_window(90, records)

    monkeypatch.setattr(recognizer.bst_model, "load_bst", lambda path: object())
    monkeypatch.setattr(recognizer.bst_model, "predict",
                        lambda model, pose, shuttle, positions: _smash_dominant_logits())

    r = recognizer.StrokeRecognizer("dummy-weights.pt")
    result = r.label_rally(track, records.get, COURT_CORNERS, VIDEO_WH)

    assert len(result) == 2
    assert [e["frame"] for e in result] == sorted(e["frame"] for e in result)
    assert result[0]["frame"] == 30 and result[0]["hitter"] == "lower"
    assert result[1]["frame"] == 90 and result[1]["hitter"] == "upper"
    for e in result:
        assert e["stroke"] == "smash"
        assert e["uncertain"] is False
        assert e["confidence"] > recognizer.MIN_STROKE_CONF


def test_label_rally_incomplete_window_is_uncertain_but_hitter_still_known(monkeypatch):
    """hitter now comes from the contact track itself, not frame_lookup -- so
    it stays correct even when there's no pose data for BST at all."""
    track = _track_with_two_contacts(30, 90, first_side="lower", second_side="upper")
    records = {}
    _fully_posed_window(30, records)
    # No records near frame 90 at all -> build_inputs returns None there.

    monkeypatch.setattr(recognizer.bst_model, "load_bst", lambda path: object())
    monkeypatch.setattr(recognizer.bst_model, "predict",
                        lambda model, pose, shuttle, positions: _smash_dominant_logits())

    r = recognizer.StrokeRecognizer("dummy-weights.pt")
    result = r.label_rally(track, records.get, COURT_CORNERS, VIDEO_WH)

    assert len(result) == 2
    assert result[0]["frame"] == 30 and result[0]["stroke"] == "smash" and result[0]["uncertain"] is False
    assert result[1]["frame"] == 90
    assert result[1]["hitter"] == "upper"          # known despite no pose data
    assert result[1]["stroke"] == "uncertain"
    assert result[1]["uncertain"] is True
    assert result[1]["confidence"] == 0.0


def test_label_rally_returns_empty_when_load_bst_raises(monkeypatch):
    def _raise(path):
        raise RuntimeError("checkpoint corrupt")

    monkeypatch.setattr(recognizer.bst_model, "load_bst", _raise)
    track = _track_with_two_contacts(30, 90)
    r = recognizer.StrokeRecognizer("dummy-weights.pt")
    assert r.label_rally(track, lambda f: None, COURT_CORNERS, VIDEO_WH) == []


def test_label_rally_returns_empty_without_weights_path(monkeypatch):
    calls = []
    monkeypatch.setattr(recognizer.bst_model, "load_bst", lambda path: calls.append(path) or object())
    for weights_path in (None, ""):
        r = recognizer.StrokeRecognizer(weights_path)
        result = r.label_rally([], lambda f: None, COURT_CORNERS, VIDEO_WH)
        assert result == []
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_recognizer.py -q -p no:cacheprovider`
Expected: FAIL — `label_rally` still calls `hit_events(track, frame_lookup)` (now a `TypeError`, since Task 3 removed that parameter) and doesn't pass `hitter` to `build_inputs`, so hitter attribution won't work.

- [ ] **Step 3: Write minimal implementation**

In `badminton_analysis/stroke_recog/recognizer.py`, update `label_rally`:

```python
    def label_rally(self, track, frame_lookup, court_corners, video_wh):
        """Label every hit in ``track`` with a coarse stroke, frame-sorted.

        ``track`` is the both-player contact track (see hits.hit_events /
        stroke.events.detect_contacts_multi). ``hitter`` for each hit comes
        from contact detection itself (shuttle-proximity, correct for both
        players) and is threaded into build_inputs so BST's person-0 slot is
        the actual hitter and person-1 the actual opponent -- no longer
        permanently zero-filled.

        Returns ``[]`` when the model is unavailable. Hits whose window has
        too little pose signal are labeled ``"uncertain"`` with confidence
        0.0 rather than skipped.
        """
        model = self._get_model()
        if model is None:
            return []

        results = []
        for hit in hit_events(track):
            frame = hit["frame"]
            hitter = hit["hitter"]
            built = build_inputs(frame, frame_lookup, court_corners, video_wh, hitter=hitter)
            if built is None:
                results.append({
                    "frame": frame,
                    "hitter": hitter,
                    "stroke": "uncertain",
                    "confidence": 0.0,
                    "uncertain": True,
                })
                continue

            logits = bst_model.predict(model, built["pose"], built["shuttle"], built["positions"])
            label, conf = to_coarse(logits, MIN_STROKE_CONF)
            results.append({
                "frame": frame,
                "hitter": hitter,
                "stroke": label,
                "confidence": float(conf),
                "uncertain": label == "uncertain",
            })

        results.sort(key=lambda r: r["frame"])
        return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_recognizer.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke_recog/recognizer.py tests/test_bst_recognizer.py
git commit -m "fix(stroke-recog): thread the real hitter/opponent through label_rally"
```

---

### Task 6: `system.py` — capture both players additively (`_capture_analysis_frame`, `_analysis_track_both`)

**Files:**
- Modify: `badminton_analysis/system.py` (`__init__`, module constants, new `_capture_side_pose` method, `_capture_analysis_frame`)
- Test: `tests/test_bst_integration.py` (add)

**Interfaces:**
- New constant `RACKET_TO_PLAYER_MAX_PX = 300.0` (Decision Point A).
- New `BadmintonAnalysisSystem._capture_side_pose(side, people, ox, oy) -> (keypoints_or_None, centroid_or_None)`.
- `_analysis_frames[frame]["players"] = {"lower": {"keypoints", "centroid", "racket_head"}, "upper": {...}}` (new, additive key).
- `self._analysis_track_both` (new list, same append cadence as `self._analysis_track`): `{"frame", "racket_lower", "racket_upper", "shuttle"}`.
- **Unchanged, verified by test:** every existing top-level key in `_analysis_frames[frame]` and the exact shape of `_analysis_track` entries.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_bst_integration.py

def _person_with_feet_at(x, y):
    kp = np.full((17, 2), 50.0, dtype=float)
    kp[15] = (x, y)  # L_ANKLE
    kp[16] = (x, y)  # R_ANKLE
    return kp


class _FakePoseVisualizerTwoPeople:
    def __init__(self, people, offset_x=0, offset_y=0):
        self._people = people
        self._offset_x = offset_x
        self._offset_y = offset_y

    def get_current_pose_data(self):
        return {"keypoints": self._people, "offset_x": self._offset_x, "offset_y": self._offset_y}


class _FakePlayerTrackerBoth:
    def __init__(self, players):
        self.players = players


class _FakeRacketDetectorBoth:
    def __init__(self, heads):
        self._heads = list(heads)

    def detect_racket_heads(self, frame, roi_corners=None):
        return list(self._heads)

    def detect_racket_head(self, frame, roi_corners=None):
        return self._heads[0] if self._heads else None


def test_capture_analysis_frame_captures_both_players_additively():
    sys_ = object.__new__(BadmintonAnalysisSystem)
    sys_._shuttle_trajectory = None
    sys_._shuttle_source = "yolo"
    sys_._analysis_track = []
    sys_._analysis_track_both = []
    sys_._analysis_frames = {}
    sys_.dominant_hand = "right"
    sys_._racket_detector = _FakeRacketDetectorBoth([(105.0, 105.0), (105.0, 505.0)])

    lower_person = _person_with_feet_at(100.0, 100.0)
    upper_person = _person_with_feet_at(100.0, 500.0)
    sys_.player_pose_visualizer = _FakePoseVisualizerTwoPeople(np.array([lower_person, upper_person]))
    sys_.player_tracker = _FakePlayerTrackerBoth({"lower": (100.0, 100.0), "upper": (100.0, 500.0)})

    sys_._capture_analysis_frame(7, None, [(0, 0), (10, 10)], [42.0, 24.0])

    rec = sys_._analysis_frames[7]
    assert set(rec["players"]) == {"lower", "upper"}
    assert rec["players"]["lower"]["centroid"] == (100.0, 100.0)
    assert rec["players"]["upper"]["centroid"] == (100.0, 500.0)
    np.testing.assert_allclose(rec["players"]["lower"]["keypoints"][15], (100.0, 100.0))
    np.testing.assert_allclose(rec["players"]["upper"]["keypoints"][15], (100.0, 500.0))
    assert rec["players"]["lower"]["racket_head"] == (105.0, 105.0)
    assert rec["players"]["upper"]["racket_head"] == (105.0, 505.0)

    # Non-regression: pre-existing single-player fields unchanged in meaning
    # ("prefer lower, else upper" tracked player).
    assert rec["player_side"] == "lower"
    assert rec["centroid"] == (100.0, 100.0)
    np.testing.assert_allclose(rec["keypoints"][15], (100.0, 100.0))
    assert rec["racket_head"] == (105.0, 105.0)

    both = sys_._analysis_track_both[-1]
    assert both == {"frame": 7, "racket_lower": (105.0, 105.0), "racket_upper": (105.0, 505.0), "shuttle": (42.0, 24.0)}

    # _analysis_track (TechniqueAnalysisRunner's input) keeps its exact
    # pre-B1 shape -- no new keys leak in.
    assert set(sys_._analysis_track[-1]) == {"frame", "racket_head", "shuttle"}


def test_capture_analysis_frame_racket_assignment_falls_back_to_kinematic_inference():
    """No racket detector -> each side's racket_head falls back to
    infer_racket_head from that side's OWN pose, same fallback the
    single-player path already had, now applied per side."""
    sys_ = object.__new__(BadmintonAnalysisSystem)
    sys_._shuttle_trajectory = None
    sys_._shuttle_source = "yolo"
    sys_._analysis_track = []
    sys_._analysis_track_both = []
    sys_._analysis_frames = {}
    sys_.dominant_hand = "right"
    sys_._racket_detector = None

    from badminton_analysis.analysis import joint_angles as ja

    def _person_with_arm(foot_x, foot_y):
        kp = _person_with_feet_at(foot_x, foot_y)
        kp[ja.R_ELBOW] = (foot_x, foot_y - 100)
        kp[ja.R_WRIST] = (foot_x + 40, foot_y - 100)
        return kp

    lower_person = _person_with_arm(100.0, 100.0)
    upper_person = _person_with_arm(100.0, 500.0)
    sys_.player_pose_visualizer = _FakePoseVisualizerTwoPeople(np.array([lower_person, upper_person]))
    sys_.player_tracker = _FakePlayerTrackerBoth({"lower": (100.0, 100.0), "upper": (100.0, 500.0)})

    sys_._capture_analysis_frame(9, None, [(0, 0), (10, 10)], None)

    rec = sys_._analysis_frames[9]
    assert rec["players"]["lower"]["racket_head"] is not None
    assert rec["players"]["upper"]["racket_head"] is not None
    assert rec["players"]["lower"]["racket_head"] != rec["players"]["upper"]["racket_head"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_integration.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: 'BadmintonAnalysisSystem' object has no attribute '_analysis_track_both'` (not yet initialized/populated) and `rec["players"]` missing (`KeyError`).

- [ ] **Step 3: Write minimal implementation**

In `badminton_analysis/system.py`:

1. Add a constant near `SHUTTLE_PRETRACK_MAX_FRAMES`:

```python
# Nearest-in-ROI-racket-detection-to-player-centroid gate for the
# both-player capture (Task 6). Deliberately dumb/untuned v1 constant, same
# convention as contact_px in stroke/events.py -- project 2 measures
# accuracy against ground truth, not this plan.
RACKET_TO_PLAYER_MAX_PX = 300.0
```

2. Add a small module-level helper (near the top, after imports/constants):

```python
def _sq_dist(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
```

3. In `BadmintonAnalysisSystem.__init__`, next to the existing `self._analysis_track = []` line, add:

```python
        self._analysis_track_both = []  # both-player contact track (contacts/BST only)
```

4. Add a new method (place it right before `_capture_analysis_frame`):

```python
    def _capture_side_pose(self, side, people, ox, oy):
        """This side's own pose, matched independently from all people
        detected in the ROI this frame, by nearest foot-midpoint to the
        side's own tracked centroid (badminton_analysis.tracking.player.
        PlayerTracker.players[side]). Returns (keypoints, centroid), or
        (None, None) when this side has no tracked player this frame, or
        (None, centroid) when it does but no pose person matched.
        """
        from .analysis import joint_angles as ja

        centroid_pt = self.player_tracker.players.get(side)
        if centroid_pt is None:
            return None, None
        centroid = (float(centroid_pt[0]), float(centroid_pt[1]))
        if not people:
            return None, centroid

        def _foot_midpoint(kp_arr):
            pts = []
            for idx in (ja.L_ANKLE, ja.R_ANKLE):
                if ja.is_valid(kp_arr, idx):
                    pts.append((float(kp_arr[idx][0]) + ox, float(kp_arr[idx][1]) + oy))
            if pts:
                return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            return None

        def _dist_to_centroid(pers):
            fm = _foot_midpoint(pers)
            if fm is None:
                return float("inf")
            return _sq_dist(fm, centroid)

        person = min(people, key=_dist_to_centroid)
        kp = person.astype(float).copy()
        mask = ~((kp[:, 0] <= 1) & (kp[:, 1] <= 1))
        kp[mask, 0] += ox
        kp[mask, 1] += oy
        return kp, centroid
```

5. In `_capture_analysis_frame`, **do not change any existing line**. Insert a new block right before the final `self._analysis_track.append(...)` / `self._analysis_frames[frame_count] = {...}` statements:

```python
        # --- B1: additive both-player capture (contacts/BST only; the
        # single-player fields above are unchanged and keep driving
        # TechniqueAnalysisRunner exactly as before) ---
        from .analysis.joint_angles import infer_racket_head as _infer_racket_head_both

        people_list = []
        p_ox = p_oy = 0
        if pose is not None and pose.get("keypoints") is not None and len(pose["keypoints"]) > 0:
            people_list = list(pose["keypoints"])
            p_ox, p_oy = pose.get("offset_x", 0), pose.get("offset_y", 0)

        racket_candidates = []
        if self._racket_detector is not None:
            racket_candidates = self._racket_detector.detect_racket_heads(frame, roi_corners=roi_corners)

        players_data = {}
        for region in ("lower", "upper"):
            side_kp, side_centroid = self._capture_side_pose(region, people_list, p_ox, p_oy)
            side_racket = None
            if side_centroid is not None and racket_candidates:
                nearest = min(racket_candidates, key=lambda p: _sq_dist(p, side_centroid))
                if _sq_dist(nearest, side_centroid) <= RACKET_TO_PLAYER_MAX_PX ** 2:
                    side_racket = (float(nearest[0]), float(nearest[1]))
            if side_racket is None and side_kp is not None:
                side_racket = _infer_racket_head_both(side_kp, dominant=self.dominant_hand)
            players_data[region] = {
                "keypoints": side_kp, "centroid": side_centroid, "racket_head": side_racket,
            }

        self._analysis_track_both.append({
            "frame": frame_count,
            "racket_lower": players_data["lower"]["racket_head"],
            "racket_upper": players_data["upper"]["racket_head"],
            "shuttle": shuttle,
        })

```

6. In the existing `self._analysis_frames[frame_count] = {...}` dict literal, add one new key (only line touched in that dict):

```python
            "player_side": side, "shuttle": shuttle, "players": players_data,
```

(replacing the existing `"player_side": side, "shuttle": shuttle,` line — purely additive, every existing key stays.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_integration.py -q -p no:cacheprovider`
Expected: PASS (new tests + all existing `test_bst_integration.py` tests still passing unmodified — confirms non-regression of the pre-existing single-player fields).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/system.py tests/test_bst_integration.py
git commit -m "feat(system): additively capture both players' pose/racket per frame"
```

---

### Task 7: Wire `_run_stroke_recognition` to the both-player track + update existing integration fixtures + full suite

**Files:**
- Modify: `badminton_analysis/system.py` (`_run_stroke_recognition`)
- Modify: `tests/test_bst_integration.py` (`_bare_system`, `_build_synthetic_track_and_frames`, and the two tests that build a real end-to-end track)

**Interfaces:**
- `_run_stroke_recognition` calls `StrokeRecognizer(self.bst_weights).label_rally(self._analysis_track_both, self._analysis_frames.get, self.court_corners, (self.frame_width, self.frame_height))` (was `self._analysis_track`).

- [ ] **Step 1: Update the shared test fixtures (failing first)**

In `tests/test_bst_integration.py`:

1. Add one line to `_bare_system` (every test using it needs the attribute, even ones that stub `label_rally` entirely, since `_run_stroke_recognition` reads it regardless):

```python
    sys_._analysis_track = []
    sys_._analysis_track_both = []   # NEW
    sys_._analysis_frames = {}
```

2. Replace `_build_synthetic_track_and_frames` in full:

```python
def _build_synthetic_track_and_frames(contact_frame=30, total_frames=60):
    """Both-player synthetic track + frames: exactly one detected hit (by
    "lower") via the real detect_contacts_multi -> hit_events chain, with
    >=10 posed frames in that hit's build_inputs window, so the real
    (unstubbed) recognition path runs end to end.
    """
    step = 10.0
    track = []
    frames = {}
    for f in range(1, total_frames + 1):
        if f <= contact_frame:
            x, y = step * f, step * f
        else:
            offset = f - contact_frame
            x = step * contact_frame + step * offset
            y = step * contact_frame - step * offset
        shuttle = (x, y)
        racket_head = shuttle if f == contact_frame else None
        track.append({
            "frame": f, "racket_lower": racket_head, "racket_upper": None, "shuttle": shuttle,
        })

        keypoints = np.full((17, 2), 50.0, dtype=float)
        frames[f] = {
            "frame": f, "keypoints": keypoints, "conf": None,
            "racket_head": racket_head, "centroid": (50.0, 50.0),
            "nose": (50.0, 50.0), "shoulder": (50.0, 50.0), "hip": (50.0, 50.0),
            "elbow_angle": 170.0, "player_side": "lower", "shuttle": shuttle,
            "players": {
                "lower": {"keypoints": keypoints, "centroid": (50.0, 50.0), "racket_head": racket_head},
                "upper": {"keypoints": None, "centroid": None, "racket_head": None},
            },
        }
    return track, frames
```

3. In `test_run_stroke_recognition_real_pipeline_uses_four_point_court_corners` and `test_run_stroke_recognition_swallows_recognition_exceptions`, change:

```python
    sys_._analysis_track = track
    sys_._analysis_frames = frames
```

to:

```python
    sys_._analysis_track_both = track
    sys_._analysis_frames = frames
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_integration.py -q -p no:cacheprovider`
Expected: FAIL — `_run_stroke_recognition` still reads `self._analysis_track` (empty `[]` from `_bare_system`, since the test now populates `_analysis_track_both` instead), so `test_run_stroke_recognition_real_pipeline_uses_four_point_court_corners` gets 0 hits instead of 1.

- [ ] **Step 3: Write minimal implementation**

In `badminton_analysis/system.py`, modify `_run_stroke_recognition`:

```python
    def _run_stroke_recognition(self):
        """Post-loop BST coarse stroke labeling -- entirely optional.

        No-ops (writes nothing) unless ``self.bst_weights`` was passed to the
        constructor. Uses ``self._analysis_track_both`` (both players' racket
        points per frame, Task 6) so hits by EITHER player are detected and
        correctly attributed, and ``self.court_corners`` -- the 4-point court
        quad -- rather than ``self.court_roi_corners`` (a 2-point pose ROI):
        ``build_inputs`` -> ``CourtMapper`` requires exactly 4 corners.

        Never fatal: any exception is caught here and only turns stroke
        recognition off for this run. Writes nothing when there are no hits,
        so ``strokes.json``'s presence stays a meaningful signal.

        BST's person-0 ("hitter") and person-1 ("opponent") slots are both
        filled from the actual hitter/opponent, chosen by shuttle proximity
        at the contact frame -- no longer a zero-filled opponent (fixed by
        B1; previously the tracked/near player's pose leaked into person-0
        even for far-player hits).
        """
        if not self.bst_weights:
            return

        try:
            from collections import Counter
            from .stroke_recog.recognizer import StrokeRecognizer

            labels = StrokeRecognizer(self.bst_weights).label_rally(
                self._analysis_track_both, self._analysis_frames.get,
                self.court_corners, (self.frame_width, self.frame_height),
            )
            if not labels:
                return
            strokes_path = os.path.join(self.save_dir, "strokes.json")
            distribution = dict(Counter(label["stroke"] for label in labels))
            payload = {"strokes": labels, "distribution": distribution}
            if self._shuttle_source == "tracknet":
                payload["shuttle_source"] = "tracknet"
            write_json(strokes_path, payload)
            print(f"Stroke recognition: {len(labels)} strokes -> {strokes_path}")
        except Exception as e:
            print(f"Stroke recognition skipped: {e}")
            return
```

(Only the `self._analysis_track` → `self._analysis_track_both` argument and the docstring changed; everything else is identical.)

- [ ] **Step 4: Run the affected tests, then the full committed suite**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_bst_integration.py -q -p no:cacheprovider`
Expected: PASS.

Then run the full suite to confirm zero regressions across the whole repo (per the Global Constraint that the committed suite stays green):

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider`
Expected: PASS (the pre-existing flaky `tests/test_ai_handoff.py` under long full-suite runs is a known, unrelated continuity-suite issue per the design doc's R9 — re-run it in isolation if it's the only failure, don't chase it as a B1 regression).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/system.py tests/test_bst_integration.py
git commit -m "fix(system): run stroke recognition over the both-player contact track"
```

---

## Validation (controller-run, real footage — NOT part of the committed suite)

This is the real done-check for done-means items 1-4, and must be run by a human/controller after all 7 tasks are merged, not as a pytest step.

**Step 0 — Confirm or reconstruct the validation clip (Decision Point C).** Before running anything, check whether the owner still has the original 750-frame `rally_seg.mp4` (or knows its source timestamp within the full match) — if so, use it and skip the reconstruction below entirely, since it lets B1 be compared directly against the recorded `{net:4, clear:3, uncertain:1}` baseline. If not, reconstruct a comparable segment:

```bash
PYTHONUTF8=1 ffmpeg -y -ss 150 -t 25 \
  -i "videos/YTDown.com_YouTube_Nice-Angle-4K60FPS-Viktor-Axelsen-vs-Kod_Media_HhrFqbM-x4Q_001_1080p.mp4" \
  -r 30 -an \
  "<scratchpad>/b1_validation_clip.mp4"
```

(`-ss 150 -t 25` anchors on rally 1 from the existing, if under-recalled, `outputs/YTDown.../rally_segments.json` — frames 9518-9689 ≈ 158.6s-161.5s at 60fps — with 8.6s of lead-in so the clip isn't cut mid-rally; `-r 30` re-encodes to 30fps per the documented original methodology.) **Record explicitly in the outcome that this is a reconstructed, not identical, clip.**

**Step 1 — Run the real pipeline, headless:**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe main.py \
  --video-path "<scratchpad>/b1_validation_clip.mp4" \
  --template-path "templates/_auto_YTDown.com_YouTube_Nice-Angle-4K60FPS-Viktor-Axelsen-vs-Kod_Media_HhrFqbM-x4Q_001_1080p.png" \
  --output-dir "outputs/b1-validation" \
  --display false \
  --analyze-technique \
  --racket-model weights/yolo11n-racket.pt \
  --tracknet-model weights/tracknet.pt \
  --inpaintnet-model weights/inpaintnet.pt \
  --bst-model weights/bst-shuttleset.pt
```

(`--racket-model` must point at the file that actually exists — `weights/yolo11n-racket.pt`, not main.py's stale default `weights/yolo11s-racket.pt`.)

**Step 2 — Inspect `outputs/b1-validation/strokes.json`:**
- Contact count: compare to the recorded baseline (8 default-threshold contacts) — similar or better, not badly regressed. A large drop would indicate the racket-to-player nearest-matching (Decision Point A) is misassigning or over-gating.
- `hitter` values: **both `"lower"` and `"upper"` must appear** if the clip has hits from both sides (this is the literal fix — pre-B1, `hitter` was ~always `"lower"`).
- Distribution: non-empty, and not degenerate (e.g., not 100% `uncertain`).

**Step 3 — Qualitative alternation check (item 4, not unit-testable):** Open `outputs/b1-validation/detect_b1_validation_clip.mp4` (or the raw clip) at each `strokes.json` contact frame's timestamp and eyeball whether the hitter sequence alternates in a physically plausible way (a rally shouldn't show 6 consecutive "lower" hits with no reply). Long same-side runs are a defect signal per the done-means, not something to explain away.

**Step 4 — Record the outcome.** Append a short, honest outcome note (mirroring the BST/TrackNetV3 spec convention) stating: which clip was actually used (reconstructed vs. original), contact count, hitter distribution, whether both sides appeared, and the qualitative alternation verdict. If transfer/attribution is poor, say so plainly rather than presenting it as fixed — consistent with the "ships experimental if needed" convention already established for BST.

---

## Self-Review

**Spec coverage (done-means items 1-4, the only items in B1's scope):**
- Item 1 (both-player capture, single-player fields unchanged) → Task 6 (capture) + Task 6's explicit non-regression assertions (`_analysis_track`'s exact key set, top-level `_analysis_frames` fields).
- Item 2 (contacts by either player) → Task 2 (`detect_contacts_multi`).
- Item 3 (hitter by shuttle proximity; BST person-0/person-1 correctly filled) → Task 2 (attribution) + Task 3 (`hit_events` consumes it) + Task 4 (`build_inputs` hitter-aware) + Task 5 (wiring).
- Item 4 (plausible alternation on a real rally) → explicitly **not** unit-tested per the brief's own framing; covered by the Validation section's Step 3.
- Items 5-14 (budgeted dense tracking, progress stage, per-rally invocation, coverage metadata, fps normalization, full committed suite for the *whole* B feature) are **out of scope for B1** and not claimed as done here — B1 only claims 1-4.

**Placeholder scan:** every task's implementation code is complete, runnable code, not a stub. The one place a design decision was genuinely underdetermined by existing code/docs (exact racket-to-player assignment heuristic, Decision Point A) is resolved with a concrete, documented default (`RACKET_TO_PLAYER_MAX_PX = 300.0`, nearest-match) rather than left vague, matching the MotionBERT plan's convention for its own Task 10 Step 1. The lost validation clip (Decision Point C) is handled the same way — a concrete reconstruction command is given, explicitly labeled as not-identical rather than silently substituted.

**Type/interface consistency:**
- `hitter` is consistently the string `"lower"`/`"upper"` from `detect_contacts_multi` onward (Tasks 2-5) — the old `"unknown"` sentinel from the pre-B1 `hits.py` is gone from that path entirely (a contact is only ever recorded when a concrete side triggered it).
- `build_inputs`'s `hitter=None` default (Task 4) preserves the *only* place `"unknown"`/no-hitter semantics still matter — callers other than `label_rally` (none currently exist, but the parameter is genuinely optional, not just present-for-recognizer) get the pre-B1 contract untouched.
- The `players` sub-dict shape (`{"keypoints", "centroid", "racket_head"}`) is identical whether produced by `system.py` (Task 6) or a test fixture (Tasks 4-7), and is read the same way by `inputs.py`'s `_is_posed`/`_foot_point` (which already operated on any dict with those two keys, so no new helper duplication was needed).
- `_analysis_track_both` entries (`{"frame", "racket_lower", "racket_upper", "shuttle"}`) have the same shape everywhere they're produced (Task 6) or consumed (Tasks 2, 3, 7).

---

### Critical Files for Implementation

- badminton_analysis/system.py
- badminton_analysis/stroke/events.py
- badminton_analysis/stroke_recog/hits.py
- badminton_analysis/stroke_recog/inputs.py
- badminton_analysis/stroke_recog/recognizer.py

---

## Validation outcome (2026-07-29) — BLOCKED UPSTREAM (rally/court-view detection), same pattern as the original BST T9 result

Ran the real match pipeline (`main.py`, all real weights: racket, TrackNetV3, BST) against a
**reconstructed** clip — the original 750-frame validation clip no longer exists on disk and
its extraction timestamp was never recorded (Decision Point C), so a comparable ~25s/752-frame
segment was cut from the full Axelsen match at `-ss 150 -t 25`, anchored on rally 1's recorded
frame range, re-encoded to 30fps. This is **not** the original clip; no comparison against the
recorded `{net:4, clear:3, uncertain:1}` baseline is possible.

**Result: 0 BST-recognized strokes** (`strokes.json` was never written; `_run_stroke_recognition`
returns silently when `hit_events` finds no contacts).

**Root cause, traced through the actual run's artifacts, is upstream of B1, not a B1 defect:**
- Dense shuttle tracking worked well: TrackNetV3 covered 668/752 frames (88.8%).
- But `_capture_analysis_frame` — the only producer of `_analysis_track_both`, which B1's whole
  chain (`detect_contacts_multi` → `hit_events` → `build_inputs` → `label_rally`) depends on —
  is gated behind `is_court_view` (`system.py:406-413`: a non-court frame returns before
  `_capture_analysis_frame` is ever called). On this clip, `rally_segments.json` shows exactly
  **one** rally window, frames 263-350 (87 frames, ~2.9s) — the pipeline judged only ~11.6% of
  the 752-frame clip as "court view," so real analysis-track data exists for only that narrow
  window. Within 87 frames, no hit satisfied the untuned default contact thresholds
  (`contact_px=80.0`, `dir_change_deg`, `min_gap=15`).
- This is exactly the failure mode the completion-bar design doc's §0 already documented and
  named as its dominant blocker (0.66% court-view coverage on the full match) and exactly the
  pattern the original BST design spec's own T9 validation hit ("BLOCKED UPSTREAM (shuttle/contact
  detection)"). It is explicitly **B11's** scope (rally/play detection on both footage types),
  not B1's — B1 never claimed to fix rally/court-view detection.
- **B1's own correctness claims (done-means items 1-3) are not weakened by this result** — they
  were independently proven by the final whole-branch review's real, non-synthetic-shortcut
  end-to-end test (`tests/test_bst_integration.py`, added in the final-review fix pass), which
  drives the actual `_capture_analysis_frame` → `_run_stroke_recognition` chain over a synthetic
  90-frame rally with contacts from both sides and asserts both `"lower"` and `"upper"` appear
  as hitters, the racket model is called once per frame, and an unmatched side degrades honestly
  rather than cross-assigning. That test is real code-path coverage; this validation attempt's
  null result is a real-footage rally-detection gap, not evidence against it.
- Item 4 (plausible alternation on a real rally) is **unvalidated** — there were no contacts to
  eyeball.

**A critical, previously-unmeasured data point this run surfaced:** total processing time for
this 25-second clip was **17,443 seconds (≈4.85 hours)** on this machine, which runs CPU-only
PyTorch (`torch==2.5.1+cpu`, per `requirements.txt` — no CUDA GPU). The completion-bar doc's R1
throughput risk was an estimate from a 4090 GPU; on CPU-only hardware the cost is far higher
still. This is a direct, load-bearing data point for the owner's Q1 answer (deep analysis as a
background job with UI-tracked status) — a background job is not a nice-to-have here, it is the
only way any of this is usable on hardware like this one. It also means iterating on validation
clips (trying a different segment to find one with better rally-detection coverage) costs
multiple hours per attempt on this hardware, not minutes.

**Recommendation:** do not spend further multi-hour validation attempts chasing a better clip
segment until B11 (rally/court-view detection) is at least partially addressed — validating B1
end-to-end on real footage is now understood to be coupled to B11's fix, not independent of it,
confirming the completion-bar doc's dependency ordering rather than contradicting it. B1 ships
with strong synthetic/unit/end-to-end evidence and an honestly-reported real-footage attempt
that hit a known, out-of-scope blocker — consistent with how BST and TrackNetV3 both shipped
"experimental" pending exactly this same class of fix.